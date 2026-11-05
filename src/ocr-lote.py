#!/usr/bin/env python3
"""
ocr-lote v2 — reconhecimento em massa de autos digitalizados.

Pensado para processos de milhares de páginas em que só parte está em imagem.
O que ele faz, por página:

  1. tenta o texto nativo primeiro, e nem aciona o OCR se já houver texto;
  2. lê a página e, se a leitura sair fraca, checa se ela está girada;
  3. mede a confiança do reconhecimento;
  4. com --caprichado, relê em resolução maior as páginas fracas (mais lento);
  5. marca como "revisar" o que continuou ruim, em vez de entregar como certo;
  6. guarda o progresso, então interromper e retomar não refaz nada.

Instalação:
    Linux:    sudo apt install poppler-utils tesseract-ocr tesseract-ocr-por
    macOS:    brew install poppler tesseract tesseract-lang
    Windows:  Tesseract pelo instalador da UB Mannheim, marcando Portuguese
              nos idiomas adicionais, e Poppler pelo repositório oficial.
              Depois, os dois no PATH.
    Sem permissão de administrador: baixe por.traineddata do repositório
    tessdata do Tesseract e aponte TESSDATA_PREFIX para a pasta onde salvou.

Uso:
    python3 ocr-lote.py autos.pdf -o autos.md
    python3 ocr-lote.py autos.pdf -o trecho.md --paginas 261-279,320-343
    python3 ocr-lote.py autos.pdf -o autos.md --nucleos 8 --dpi 200
    python3 ocr-lote.py autos.pdf -o autos.md --retomar
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

# Cada processo já ocupa um núcleo. Sem este limite, o Tesseract tenta usar
# todos os núcleos em cada página, os processos disputam a máquina e o lote
# inteiro fica várias vezes mais lento (acontece muito no Windows).
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

MIN_CARACTERES = 180      # texto nativo acima disso dispensa o OCR
CONF_MINIMA = 72          # abaixo disso a página é refeita em resolução maior
CONF_REVISAR = 65         # abaixo disso a página sai marcada para conferência
CONF_ROTACAO = 60        # abaixo disso vale checar se a página está de lado
MIN_TEXTO_OCR = 40        # menos que isso é página sem texto legível


# --------------------------------------------------------------------------- #
# Ambiente
# --------------------------------------------------------------------------- #

def idiomas_instalados() -> set:
    try:
        r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        saida = (r.stdout or "") + (r.stderr or "")
        return {l.strip() for l in saida.splitlines()
                if l.strip() and not l.lower().startswith("list of")}
    except FileNotFoundError:
        return set()


def checar_ambiente(idioma: str, permitir_outro: bool) -> str:
    faltando = [p for p in ("pdftoppm", "pdftotext", "tesseract") if not shutil.which(p)]
    if faltando:
        sys.exit("Faltam programas no PATH: " + ", ".join(faltando) +
                 "\nVeja as instruções de instalação no cabeçalho deste arquivo.")

    langs = idiomas_instalados()
    if idioma in langs:
        return idioma

    aviso = (f"O pacote de idioma '{idioma}' não está instalado. "
             f"Disponíveis: {', '.join(sorted(langs)) or 'nenhum'}.")
    if not permitir_outro:
        sys.exit(aviso + "\n\nEm português o ganho é grande: acentos, cedilha e "
                 "vocabulário jurídico passam a ser reconhecidos, e números erram menos.\n"
                 "Instale conforme o cabeçalho deste arquivo, ou rode com "
                 "--aceitar-idioma-errado para seguir assim mesmo.")
    restantes = sorted(langs - {"osd"})
    alternativa = "eng" if "eng" in langs else (restantes[0] if restantes else "eng")
    print(aviso + f" Seguindo com '{alternativa}': espere acentos trocados.",
          file=sys.stderr)
    return alternativa


# --------------------------------------------------------------------------- #
# Leitura de uma página
# --------------------------------------------------------------------------- #

# Alguns PDFs de tribunal trazem uma camada de texto invisível, com milhares
# de códigos de assinatura digital repetidos. Sem limpar isso, uma página
# digitalizada parece cheia de texto e o reconhecimento nunca é acionado.
TOKEN_TECNICO = re.compile(r"^[0-9A-F]{1,12}$")


def limpar_camada_tecnica(texto: str) -> tuple:
    """O sinal é a repetição: a camada técnica repete poucos códigos milhares
    de vezes. Uma planilha de débito tem valores quase todos distintos."""
    tokens = texto.split()
    if len(tokens) < 100:
        return texto, 0

    contagem = {}
    for t in tokens:
        if TOKEN_TECNICO.match(t):
            contagem[t] = contagem.get(t, 0) + 1

    repetidos = {t for t, n in contagem.items() if n >= 20}
    total = sum(contagem[t] for t in repetidos)
    if total < 100 or total <= len(tokens) * 0.5:
        return texto, 0

    linhas = []
    for linha in texto.splitlines():
        restante = " ".join(t for t in linha.split() if t not in repetidos)
        if restante.strip():
            linhas.append(restante)
    return "\n".join(linhas), total


def texto_nativo(caminho: str, pagina: int) -> str:
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-f", str(pagina), "-l", str(pagina),
                        "-layout", caminho, "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    bruto = (r.stdout or "").strip()
    limpo, _ = limpar_camada_tecnica(bruto)
    return limpo.strip()


def letras(texto: str) -> int:
    """Conta só caracteres visíveis. O pdftotext -layout enche a página de
    espaços para manter o alinhamento, e uma folha escaneada com o carimbo
    do eproc parecia ter texto suficiente só por causa deles."""
    return len("".join(texto.split()))


# Ideogramas e símbolos que não existem em autos em português. Quando aparecem
# no texto digital, a fonte do PDF não tem a tabela que liga cada desenho à
# sua letra ("AU丁OR" no lugar de "AUTOR"). A tela mostra certo, a extração não.
RE_FONTE_DEFEITUOSA = re.compile(r"[\u2E80-\u2FDF\u3000-\u30FF\u3400-\u9FFF\uFF00-\uFFEF]")


def fonte_defeituosa(texto: str) -> bool:
    return len(RE_FONTE_DEFEITUOSA.findall(texto)) >= 2


def texto_utilizavel(texto: str) -> bool:
    """Texto digital que dispensa o OCR: tem letras suficientes e não está
    embaralhado por fonte defeituosa."""
    return letras(texto) >= MIN_CARACTERES and not fonte_defeituosa(texto)


def faixas_continuas(paginas: list, maximo: int = 0) -> list:
    """[3,4,5,9,10] vira [(3,5),(9,10)]. Com maximo, corta faixas longas."""
    faixas = []
    for n in sorted(paginas):
        if faixas and n == faixas[-1][1] + 1 and (not maximo or n - faixas[-1][0] < maximo):
            faixas[-1][1] = n
        else:
            faixas.append([n, n])
    return [tuple(f) for f in faixas]


def textos_nativos(caminho: str, paginas: list) -> dict:
    """Texto de todas as páginas pedidas com UMA chamada por faixa contínua.

    Chamar o pdftotext página a página obriga o programa a abrir e analisar o
    PDF inteiro a cada vez. Num processo de 60 MB no Windows isso custa
    segundos por página; numa chamada só, custa segundos no total.
    """
    out = {}
    for a, b in faixas_continuas(paginas):
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-f", str(a), "-l", str(b),
                            "-layout", caminho, "-"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        partes = (r.stdout or "").split("\f")
        for i, n in enumerate(range(a, b + 1)):
            bruto = partes[i].strip() if i < len(partes) else ""
            out[n] = limpar_camada_tecnica(bruto)[0].strip()
    return out


def corrigir_rotacao(img: str) -> int:
    """Lê a orientação pelo OSD do Tesseract e gira a imagem quando preciso."""
    try:
        r = subprocess.run(["tesseract", img, "-", "--psm", "0"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        saida = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"Rotate:\s*(\d+)", saida)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", saida)
        graus = int(m.group(1)) if m else 0
        seguranca = float(conf.group(1)) if conf else 0.0
        if graus in (90, 180, 270) and seguranca >= 2.0:
            from PIL import Image
            Image.open(img).rotate(-graus, expand=True).save(img)
            return graus
    except Exception:
        pass
    return 0


def ler_imagem_linhas(img: str, idioma: str, psm: str) -> tuple:
    """Devolve (linhas, confiança média, nº de palavras) a partir do TSV do
    Tesseract. Cada linha é [texto, confiança média das palavras da linha]."""
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "s")
        # "-c tessedit_create_tsv=1" em vez do atalho "tsv": o atalho depende de um
        # arquivo de configuração que nem toda pasta tessdata tem
        subprocess.run(["tesseract", img, base, "-l", idioma, "--psm", psm,
                        "-c", "tessedit_create_tsv=1"],
                       capture_output=True)
        caminho = base + ".tsv"
        if not os.path.exists(caminho):
            return [], 0.0, 0

        linhas, confs, atual = [], [], None
        palavras, confs_linha = [], []

        def fecha():
            if palavras:
                media_l = sum(confs_linha) / len(confs_linha) if confs_linha else 0.0
                linhas.append([" ".join(palavras), round(media_l, 1)])

        with open(caminho, encoding="utf-8", errors="replace") as fh:
            next(fh, None)
            for registro in fh:
                c = registro.rstrip("\n").split("\t")
                if len(c) < 12:
                    continue
                try:
                    conf = float(c[10])
                except ValueError:
                    continue
                palavra = c[11].strip()
                chave = tuple(c[2:5])          # bloco, parágrafo, linha
                if chave != atual:
                    fecha()
                    atual, palavras, confs_linha = chave, [], []
                if palavra:
                    palavras.append(palavra)
                    if conf >= 0:
                        confs.append(conf)
                        confs_linha.append(conf)
        fecha()

    linhas = [l for l in linhas if l[0].strip()]
    media = sum(confs) / len(confs) if confs else 0.0
    return linhas, media, len(confs)


def ler_imagem(img: str, idioma: str, psm: str) -> tuple:
    """Devolve (texto, confiança média). Mantida por compatibilidade."""
    linhas, media, _ = ler_imagem_linhas(img, idioma, psm)
    return "\n".join(l[0] for l in linhas), media


# --------------------------------------------------------------------------- #
# Conferência: linhas duvidosas, números validados e páginas de pouco texto
# --------------------------------------------------------------------------- #

LINHA_DUVIDOSA = 50       # linha com confiança média abaixo disso ganha ⚠
POUCO_TEXTO_PALAVRAS = 120  # página com menos palavras que isso...
POUCO_TEXTO_CONF = 55       # ...e confiança abaixo disso é carimbo, termo ou rubrica
COPIA_RUIM_CONF = 45        # datilografada abaixo disso: nenhum número é confiável

RE_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
RE_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
RE_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
RE_DATA = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
# número que importa numa due diligence: valor em dinheiro, data ou número longo
# (conta, matrícula, documento). Dígito solto ("Fls. 2", "19") não pesa: o OCR
# ruim produz esses fragmentos o tempo todo.
RE_NUMERO_RELEVANTE = re.compile(
    r"(?:R\$|Cz\$|NCz\$|Cr\$)\s*\S"                 # moeda, mesmo com dígito mal lido
    r"|\b\d{1,3}(?:\.\d{3})+,\d{2}\b"                # 1.234,56
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}\b"   # datas
    r"|(?:\d[\d.\-/]*){5,}")                         # 5+ dígitos (matrícula, registro, conta)



def cpf_valido(t: str) -> bool:
    d = [int(x) for x in re.sub(r"\D", "", t)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        soma = sum(d[i] * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != d[n]:
            return False
    return True


def cnpj_valido(t: str) -> bool:
    d = [int(x) for x in re.sub(r"\D", "", t)]
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for n, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                     (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(d[i] * pesos[i] for i in range(n)) % 11
        if (0 if r < 2 else 11 - r) != d[n]:
            return False
    return True


def cnj_valido(t: str) -> bool:
    """Número único do processo (Resolução CNJ 65/2008): dígito módulo 97."""
    m = re.fullmatch(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", t)
    if not m:
        return False
    n, dd, ano, j, tr, orig = m.groups()
    return 98 - int(n + ano + j + tr + orig + "00") % 97 == int(dd)


def data_valida(t: str) -> bool:
    import datetime
    try:
        d, m, a = (int(x) for x in t.split("/"))
        datetime.date(a, m, d)
        return 1900 <= a <= 2035
    except ValueError:
        return False


VALIDADORES = (("CPF", RE_CPF, cpf_valido), ("CNPJ", RE_CNPJ, cnpj_valido),
               ("nº do processo", RE_CNJ, cnj_valido), ("data", RE_DATA, data_valida))


def conferir_linhas(linhas: list) -> dict:
    """Valida os números de cada linha e decide o que precisa de conferência.

    Um número com dígito verificador certo dificilmente foi lido errado pelo
    OCR; um com dígito errado, sim. Uma linha duvidosa só pesa na conferência
    se tiver algum número que não foi validado: é ali que mora o risco."""
    validos, invalidos, duvidosas, numeros_duvidosos = [], [], 0, 0
    for texto, conf in linhas:
        resto = texto
        for nome, padrao, ok in VALIDADORES:
            for m in padrao.findall(texto):
                (validos if ok(m) else invalidos).append([nome, m])
                if ok(m):
                    resto = resto.replace(m, " ")
        if conf < LINHA_DUVIDOSA:
            duvidosas += 1
            if RE_NUMERO_RELEVANTE.search(resto):
                numeros_duvidosos += 1
    return {"validos": validos, "invalidos": invalidos,
            "duvidosas": duvidosas, "numeros_duvidosos": numeros_duvidosos}


def classificar(linhas: list, conf: float, palavras: int) -> dict:
    """Junta tudo numa decisão por página."""
    c = conferir_linhas(linhas)
    pouco_texto = palavras < POUCO_TEXTO_PALAVRAS and conf < POUCO_TEXTO_CONF
    revisar = (not pouco_texto) and (bool(c["invalidos"]) or c["numeros_duvidosos"] > 0
                                     or conf < COPIA_RUIM_CONF
                                     or palavras < MIN_TEXTO_OCR // 5)
    return {"tipo": "pouco_texto" if pouco_texto else "texto", "revisar": revisar,
            "validos": c["validos"], "invalidos": c["invalidos"], "duvidosas": c["duvidosas"]}


def ocr_pagina(img: str, caminho: str, pagina: int, dpi: int, idioma: str,
               caprichado: bool, td: str) -> dict:
    """Lê uma página já transformada em imagem.

    Ordem pensada para velocidade: lê primeiro e só gasta tempo extra
    (checar rotação, reler em alta resolução) quando a leitura sai fraca.
    """
    linhas, conf, palavras = ler_imagem_linhas(img, idioma, "6")
    texto_de = lambda ls: "\n".join(l[0] for l in ls)
    fraca = lambda ls, c: c < CONF_MINIMA or len(texto_de(ls)) < MIN_TEXTO_OCR

    # página de lado sai com leitura muito ruim; manuscrito e cópia velha saem
    # só medianos e não precisam da checagem, que custa quase uma leitura inteira
    girou = 0
    if conf < CONF_ROTACAO or len(texto_de(linhas)) < MIN_TEXTO_OCR:
        girou = corrigir_rotacao(img)
        if girou:
            l2, c2, p2 = ler_imagem_linhas(img, idioma, "6")
            if c2 > conf:
                linhas, conf, palavras = l2, c2, p2

    # segunda tentativa em alta resolução: dobra o tempo e quase não
    # melhora em manuscrito, por isso só com --caprichado
    if caprichado and fraca(linhas, conf):
        prefixo2 = os.path.join(td, f"alta{pagina}")
        subprocess.run(["pdftoppm", "-png", "-r", str(min(dpi * 2, 400)),
                        "-f", str(pagina), "-l", str(pagina), caminho, prefixo2],
                       capture_output=True)
        altas = sorted(f for f in os.listdir(td) if f.startswith(f"alta{pagina}"))
        if altas:
            img2 = os.path.join(td, altas[0])
            if girou:
                try:
                    from PIL import Image
                    Image.open(img2).rotate(-girou, expand=True).save(img2)
                except Exception:
                    pass
            l2, c2, p2 = ler_imagem_linhas(img2, idioma, "1")
            if c2 > conf or len(texto_de(l2)) > len(texto_de(linhas)) * 1.3:
                linhas, conf, palavras = l2, c2, p2

    r = {"pagina": pagina, "texto": texto_de(linhas), "ocr": True,
         "conf": round(conf, 1), "girou": girou, "linhas": linhas, "palavras": palavras}
    r.update(classificar(linhas, conf, palavras))
    return r


def processar_bloco(args) -> list:
    """Roda num processo por núcleo. Recebe páginas seguidas, gera todas as
    imagens com UMA chamada ao pdftoppm e lê uma a uma. Nunca deixa exceção
    escapar: página com problema volta marcada para conferir."""
    caminho, paginas, dpi, idioma, caprichado = args
    a, b = min(paginas), max(paginas)
    saida = []
    try:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(a), "-l", str(b),
                            caminho, os.path.join(td, "p")], capture_output=True)
            imagens = {}
            for f in os.listdir(td):
                m = re.match(r"p-0*(\d+)\.png$", f)
                if m:
                    imagens[int(m.group(1))] = os.path.join(td, f)
            for n in paginas:
                try:
                    if n not in imagens:
                        saida.append({"pagina": n, "texto": "", "ocr": True, "conf": 0.0,
                                      "revisar": True, "girou": 0,
                                      "erro": "não foi possível gerar a imagem da página"})
                        continue
                    saida.append(ocr_pagina(imagens[n], caminho, n, dpi, idioma, caprichado, td))
                except Exception as e:
                    saida.append({"pagina": n, "texto": "", "ocr": True, "conf": 0.0,
                                  "revisar": True, "girou": 0, "erro": str(e)})
                finally:
                    if n in imagens and os.path.exists(imagens[n]):
                        os.remove(imagens[n])   # libera disco à medida que lê
    except Exception as e:
        feitas = {r["pagina"] for r in saida}
        saida += [{"pagina": n, "texto": "", "ocr": True, "conf": 0.0, "revisar": True,
                   "girou": 0, "erro": str(e)} for n in paginas if n not in feitas]
    return saida


def processar(args) -> dict:
    """Uma página só (compatibilidade). Para lotes, use executar()."""
    caminho, pagina, dpi, idioma, *resto = args
    caprichado = bool(resto and resto[0])
    try:
        nativo = texto_nativo(caminho, pagina)
        if texto_utilizavel(nativo):
            return {"pagina": pagina, "texto": nativo, "ocr": False,
                    "conf": 100.0, "revisar": False, "girou": 0}
    except Exception:
        pass
    return processar_bloco((caminho, [pagina], dpi, idioma, caprichado))[0]


TAMANHO_BLOCO = 8   # páginas seguidas por chamada ao pdftoppm


def executar(pdf: str, pendentes: list, dpi: int, idioma: str, nucleos: int,
             caprichado: bool = False, ao_concluir=None) -> dict:
    """Lê as páginas pendentes do jeito mais rápido:
    1) texto digital de todas numa chamada só;
    2) só as páginas sem texto vão para o OCR, em blocos de páginas seguidas,
       distribuídos entre os núcleos.
    ao_concluir(resultado) é chamado a cada página pronta."""
    resultados = {}
    nativos = textos_nativos(pdf, pendentes)
    precisam = []
    for n in pendentes:
        t = nativos.get(n, "")
        if texto_utilizavel(t):
            r = {"pagina": n, "texto": t, "ocr": False, "conf": 100.0,
                 "revisar": False, "girou": 0}
            resultados[n] = r
            if ao_concluir:
                ao_concluir(r)
        else:
            precisam.append(n)

    if precisam:
        blocos = [list(range(a, b + 1)) for a, b in faixas_continuas(precisam, TAMANHO_BLOCO)]
        tarefas = [(pdf, bl, dpi, idioma, caprichado) for bl in blocos]
        with ProcessPoolExecutor(max_workers=max(1, min(nucleos, len(tarefas)))) as pool:
            futuros = [pool.submit(processar_bloco, t) for t in tarefas]
            for fut in as_completed(futuros):
                for r in fut.result():
                    resultados[r["pagina"]] = r
                    if ao_concluir:
                        ao_concluir(r)
    return resultados


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #

def total_paginas(caminho: str) -> int:
    if shutil.which("pdfinfo"):
        saida = subprocess.run(["pdfinfo", caminho], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        m = re.search(r"Pages:\s+(\d+)", saida or "")
        if m:
            return int(m.group(1))
    saida = subprocess.run(["pdftotext", "-enc", "UTF-8", caminho, "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return max((saida or "").count("\f"), 1)


def parse_paginas(spec: str, total: int) -> list:
    if not spec:
        return list(range(1, total + 1))
    out = set()
    for parte in spec.split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte:
            a, _, b = parte.partition("-")
            out.update(range(max(int(a), 1), min(int(b), total) + 1))
        else:
            out.add(min(max(int(parte), 1), total))
    return sorted(out)


def escrever_markdown(caminho_md: str, pdf: str, alvo: list, resultados: dict) -> dict:
    ocr = {p: r for p, r in resultados.items() if r.get("ocr") and r.get("texto")}
    reconhecidas = len(ocr)
    confs = [r["conf"] for r in ocr.values()]
    media = round(sum(confs) / len(confs), 1) if confs else 0.0
    giradas = sorted(p for p, r in resultados.items() if r.get("girou"))
    pouco = sorted(p for p, r in ocr.items() if r.get("tipo") == "pouco_texto")
    revisar = sorted(p for p, r in resultados.items() if r.get("revisar"))
    duvidosas = sum(r.get("duvidosas", 0) for r in ocr.values())
    validos = [v for r in ocr.values() for v in r.get("validos", [])]
    invalidos = [(p, v) for p, r in ocr.items() for v in r.get("invalidos", [])]

    def amostra(lista):
        return ", ".join(str(x) for x in lista[:40]) + (" e outras" if len(lista) > 40 else "")

    with open(caminho_md, "w", encoding="utf-8") as fh:
        fh.write(f"# {os.path.basename(pdf)}\n\n")
        fh.write(f"- Páginas processadas: {len(alvo)}\n")
        fh.write(f"- Lidas por OCR: {reconhecidas} (confiança média {media}%)\n")
        fh.write(f"- Páginas giradas corrigidas: {len(giradas)}\n")
        fh.write(f"- **Páginas a conferir no original: {len(revisar)}** "
                 "(número inválido ou número em linha de leitura fraca)\n")
        if revisar:
            fh.write(f"  - {amostra(revisar)}\n")
        fh.write(f"- Páginas de pouco texto legível (carimbo, termo manuscrito, rubrica): "
                 f"{len(pouco)}. Ver no original se o conteúdo importar.\n")
        if pouco:
            fh.write(f"  - {amostra(pouco)}\n")
        fh.write(f"- Linhas de leitura fraca, marcadas com ⚠ no texto: {duvidosas}\n")
        fh.write(f"- Números conferidos pelo dígito verificador ou formato (CPF, CNPJ, "
                 f"nº do processo, data): {len(validos)} válidos, {len(invalidos)} inválidos\n")
        if invalidos:
            fh.write("  - Inválidos: " + "; ".join(f"{v[0]} {v[1]} (pág. {p})"
                                                   for p, v in invalidos[:30]) +
                     (" e outros" if len(invalidos) > 30 else "") + "\n")
        fh.write("\nTrechos marcados como OCR não são texto original do documento. "
                 "Linhas com ⚠ tiveram leitura fraca. Número inválido quase sempre é "
                 "erro de leitura: confira no PDF antes de usar em peça.\n")

        for n in alvo:
            r = resultados.get(n)
            fh.write(f"\n## Página {n}\n\n")
            if not r or not r.get("texto"):
                fh.write("*(sem texto reconhecível nesta página)*\n")
                continue
            if not r["ocr"]:
                fh.write(r["texto"] + "\n")
                continue
            marca = "OCR" + (f", confiança {r['conf']}%" if r["conf"] else "")
            if r.get("tipo") == "pouco_texto":
                marca += ", POUCO TEXTO LEGÍVEL (carimbo, termo manuscrito ou rubrica): ver original"
            elif r.get("revisar"):
                marca += ", CONFERIR NO ORIGINAL"
            if r.get("girou"):
                marca += f", página girada {r['girou']} graus"
            fh.write(f"> **[{marca}]**\n>\n")
            inval = {v[1] for v in r.get("invalidos", [])}
            if "linhas" in r:
                for texto, conf in r["linhas"]:
                    linha = texto
                    for x in inval:
                        linha = linha.replace(x, f"{x} ⚠[inválido]")
                    prefixo = "⚠ " if conf < LINHA_DUVIDOSA else ""
                    fh.write(f"> {prefixo}{linha}\n")
            else:   # registro antigo, sem confiança por linha
                fh.write("\n".join("> " + l for l in r["texto"].splitlines()) + "\n")

    return {"reconhecidas": reconhecidas, "revisar": len(revisar), "pouco_texto": len(pouco),
            "giradas": len(giradas), "confianca": media, "duvidosas": duvidosas,
            "invalidos": len(invalidos)}


def main() -> int:
    p = argparse.ArgumentParser(description="OCR em massa de autos digitalizados.")
    p.add_argument("pdf")
    p.add_argument("-o", "--saida", required=True)
    p.add_argument("--paginas", help="ex.: 261-279,320-343")
    p.add_argument("--dpi", type=int, default=200, help="150 rápido, 300 preciso")
    p.add_argument("--idioma", default="por")
    p.add_argument("--aceitar-idioma-errado", action="store_true",
                   help="segue mesmo sem o pacote de português")
    p.add_argument("--nucleos", type=int, default=max(cpu_count() - 1, 1))
    p.add_argument("--retomar", action="store_true",
                   help="aproveita o progresso de uma execução interrompida")
    p.add_argument("--caprichado", action="store_true",
                   help="relê em alta resolução as páginas fracas (bem mais lento)")
    a = p.parse_args()

    idioma = checar_ambiente(a.idioma, a.aceitar_idioma_errado)
    total = total_paginas(a.pdf)
    alvo = parse_paginas(a.paginas, total)

    progresso = a.saida + ".progresso.jsonl"
    resultados = {}
    if a.retomar and os.path.exists(progresso):
        with open(progresso, encoding="utf-8") as fh:
            for linha in fh:
                try:
                    r = json.loads(linha)
                    resultados[r["pagina"]] = r
                except Exception:
                    pass
        print(f"retomando: {len(resultados)} página(s) já prontas", file=sys.stderr)

    pendentes = [n for n in alvo if n not in resultados]
    print(f"{len(pendentes)} de {len(alvo)} página(s) a processar, "
          f"{a.nucleos} núcleo(s), idioma {idioma}", file=sys.stderr)

    if pendentes:
        feitas = [0]
        with open(progresso, "a", encoding="utf-8") as reg:
            def ao_concluir(r):
                resultados[r["pagina"]] = r
                reg.write(json.dumps(r, ensure_ascii=False) + "\n")
                reg.flush()
                feitas[0] += 1
                if feitas[0] % 10 == 0 or feitas[0] == len(pendentes):
                    print(f"  {feitas[0]}/{len(pendentes)}", end="\r", file=sys.stderr)
            try:
                executar(a.pdf, pendentes, a.dpi, idioma, a.nucleos, a.caprichado, ao_concluir)
            except KeyboardInterrupt:
                print("\ninterrompido; rode de novo com --retomar", file=sys.stderr)

    resumo = escrever_markdown(a.saida, a.pdf, alvo, resultados)
    print(f"\nOK -> {a.saida}", file=sys.stderr)
    print(f"  reconhecidas por OCR: {resumo['reconhecidas']} "
          f"(confiança média {resumo['confianca']}%)", file=sys.stderr)
    print(f"  giradas corrigidas:   {resumo['giradas']}", file=sys.stderr)
    print(f"  a conferir:           {resumo['revisar']}", file=sys.stderr)
    print(f"  pouco texto legível:  {resumo['pouco_texto']}", file=sys.stderr)
    print(f"  linhas fracas marcadas: {resumo['duvidosas']}", file=sys.stderr)
    print(f"  progresso salvo em:   {progresso}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
