#!/usr/bin/env python3
"""
app_exe.py — ponto de entrada do "Leitor de Documentos.exe".

Quando empacotado pelo PyInstaller, o Tesseract, o Poppler e o pacote de
português viajam dentro do próprio .exe. Este arquivo aponta o programa para
eles antes de qualquer coisa, de modo que o computador do colega não precisa
ter nada instalado.

Uso: dois cliques no .exe e escolher o PDF, ou arrastar o PDF para cima dele.
O resultado é salvo ao lado do PDF, com o nome terminando em " - OCR.md".
"""

import json
import os
import sys
import time


def pasta_base() -> str:
    """Dentro do .exe, o PyInstaller descompacta tudo numa pasta temporária."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


BASE = pasta_base()
TESS = os.path.join(BASE, "tesseract")
POPP = os.path.join(BASE, "poppler")

# os componentes embutidos vêm antes de qualquer coisa instalada na máquina
os.environ["PATH"] = TESS + os.pathsep + POPP + os.pathsep + os.environ.get("PATH", "")
# o pacote de português embutido fica em "tessdata"; o do Tesseract é a reserva
for pasta in (os.path.join(BASE, "tessdata"), os.path.join(TESS, "tessdata")):
    if os.path.exists(os.path.join(pasta, "por.traineddata")):
        os.environ["TESSDATA_PREFIX"] = pasta
        break
else:
    os.environ["TESSDATA_PREFIX"] = os.path.join(TESS, "tessdata")

import ocr_lote  # noqa: E402  (precisa vir depois do PATH)


LARGURA = 68


def titulo(texto):
    print()
    print("=" * LARGURA)
    print("  " + texto)
    print("=" * LARGURA)
    print()


def pausar():
    print()
    try:
        input("  Pressione Enter para fechar.")
    except EOFError:
        pass


def escolher_pdf():
    """Janela do Windows para escolher o arquivo. Devolve None se cancelar."""
    try:
        import tkinter
        from tkinter import filedialog
        raiz = tkinter.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        caminho = filedialog.askopenfilename(
            title="Escolha o PDF para ler",
            filetypes=[("PDF", "*.pdf"), ("Todos os arquivos", "*.*")])
        raiz.destroy()
        return caminho or None
    except Exception:
        return None


def perguntar_paginas():
    print("  O documento é grande. Ler inteiro ou só um trecho?")
    print()
    print("    1  Documento inteiro")
    print("    2  Escolher as páginas")
    print()
    try:
        escolha = input("  Digite 1 ou 2 e tecle Enter: ").strip()
    except EOFError:
        return None
    if escolha == "2":
        print()
        print("  Exemplos: 1-20   ou   261-279,320-343   ou   862")
        try:
            return input("  Páginas: ").strip() or None
        except EOFError:
            return None
    return None


def tempo_legivel(segundos: float) -> str:
    m = int(segundos // 60)
    return "menos de 1 min" if m < 1 else f"cerca de {m} min"


def main() -> int:
    titulo("LEITOR DE DOCUMENTOS (PDF para Markdown)")

    pdf = sys.argv[1] if len(sys.argv) >= 2 else None
    if not pdf:
        print("  Escolha o PDF na janela que vai abrir...")
        pdf = escolher_pdf()
    if not pdf:
        print()
        print("  Nenhum PDF foi escolhido.")
        print("  Abra o programa de novo e escolha um arquivo, ou arraste o PDF")
        print("  para cima do ícone do programa.")
        pausar()
        return 1

    if not os.path.isfile(pdf) or not pdf.lower().endswith(".pdf"):
        print(f"  Isso não parece um arquivo PDF: {os.path.basename(pdf)}")
        pausar()
        return 1

    saida = os.path.splitext(pdf)[0] + " - OCR.md"
    progresso = saida + ".progresso.jsonl"
    print(f"  Documento: {os.path.basename(pdf)}")

    try:
        total = ocr_lote.total_paginas(pdf)
        print(f"  Páginas: {total}")
        print()
    except Exception as e:
        print(f"  Não consegui abrir esse PDF: {e}")
        pausar()
        return 1

    # o que já foi lido numa execução anterior não é lido de novo
    resultados = {}
    if os.path.exists(progresso):
        with open(progresso, encoding="utf-8") as fh:
            for linha in fh:
                try:
                    r = json.loads(linha)
                    resultados[r["pagina"]] = r
                except Exception:
                    pass

    faixa = perguntar_paginas() if total > 50 and not resultados else None
    alvo = ocr_lote.parse_paginas(faixa, total)
    pendentes = [n for n in alvo if n not in resultados]

    idioma = "por" if "por" in ocr_lote.idiomas_instalados() else "eng"
    if idioma != "por":
        print("  AVISO: o pacote de português não foi encontrado dentro do")
        print("  programa. A leitura vai sair com acentos trocados.")
        print()

    nucleos = max((os.cpu_count() or 2) - 1, 1)
    titulo("LENDO O DOCUMENTO")
    if resultados:
        print(f"  Continuando de onde parou: {len(alvo) - len(pendentes)} página(s) já lidas.")
    print(f"  Faltam ler: {len(pendentes)} página(s). Usando {nucleos} núcleo(s).")
    print("  Pode usar o computador enquanto isso. Não feche esta janela.")
    print("  Se fechar sem querer, abra de novo: o que já foi lido fica guardado.")
    print()

    feitas = [0]
    inicio = time.time()

    with open(progresso, "a", encoding="utf-8") as reg:
        def ao_concluir(r):
            resultados[r["pagina"]] = r
            reg.write(json.dumps(r, ensure_ascii=False) + "\n")
            reg.flush()
            feitas[0] += 1
            n, tot = feitas[0], len(pendentes)
            pct = int(n * 100 / tot) if tot else 100
            barra = "#" * (pct // 4) + "." * (25 - pct // 4)
            decorrido = time.time() - inicio
            resta = decorrido / n * (tot - n) if n else 0
            print(f"\r  [{barra}] {pct}%  lidas {n} de {tot}  |  falta {tempo_legivel(resta)}   ",
                  end="", flush=True)

        try:
            if pendentes:
                ocr_lote.executar(pdf, pendentes, 200, idioma, nucleos, False, ao_concluir)
        except KeyboardInterrupt:
            print("\n\n  Interrompido. Vou salvar o que já foi lido.")

    print()
    resumo = ocr_lote.escrever_markdown(saida, pdf, alvo, resultados)

    titulo("PRONTO")
    print(f"  Arquivo salvo em: {saida}")
    print()
    print(f"  Páginas lidas por reconhecimento de imagem: {resumo['reconhecidas']}")
    print(f"  Confiança média: {resumo['confianca']}%")
    print(f"  Páginas giradas corrigidas: {resumo['giradas']}")
    print(f"  Páginas a conferir no original: {resumo['revisar']}")
    print(f"  Páginas de pouco texto legível (carimbo, rubrica): {resumo['pouco_texto']}")
    print(f"  Linhas de leitura fraca, marcadas no texto: {resumo['duvidosas']}")
    print()
    print("  A lista do que conferir está no topo do arquivo.")
    print("  Texto lido de imagem pode conter erro: confira valores, datas e CPF")
    print("  no PDF original antes de usar em peça.")

    try:
        os.startfile(saida)          # abre no programa padrão do Windows
    except Exception:
        pass

    pausar()
    return 0


if __name__ == "__main__":
    # necessário para o paralelismo funcionar dentro de um .exe
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main())
