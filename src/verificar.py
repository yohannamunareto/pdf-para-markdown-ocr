#!/usr/bin/env python3
"""
verificar.py — conferência da instalação, em linguagem de gente.

Roda quando a pessoa dá dois cliques em "conferir.bat".
Não exige nada além do Python padrão.
"""

import shutil
import subprocess
import sys


VERDE = "  [ OK ]  "
VERMELHO = "  [FALTA] "


def rodar(comando) -> str:
    try:
        r = subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def linha(simbolo, texto):
    print(simbolo + texto)


def main() -> int:
    print()
    print("=" * 66)
    print("  CONFERINDO SE O COMPUTADOR ESTA PRONTO PARA LER DOCUMENTOS")
    print("=" * 66)
    print()

    problemas = []

    # 1. Python
    linha(VERDE, f"Python instalado (versão {sys.version.split()[0]})")

    # 2. Tesseract
    if shutil.which("tesseract"):
        versao = rodar(["tesseract", "--version"]).splitlines()
        v = versao[0] if versao else "versão desconhecida"
        linha(VERDE, f"Leitor de texto instalado ({v.strip()})")

        # 3. Português
        saida = rodar(["tesseract", "--list-langs"])
        idiomas = [l.strip() for l in saida.splitlines()
                   if l.strip() and not l.lower().startswith("list of")]
        if "por" in idiomas:
            linha(VERDE, "Pacote de português instalado")
        else:
            linha(VERMELHO, "O pacote de PORTUGUÊS não está instalado")
            print(f"           Idiomas encontrados: {', '.join(idiomas) or 'nenhum'}")
            problemas.append(
                "O leitor está instalado, mas só entende outro idioma.\n"
                "    Refaça o PASSO 2 do guia. Ao rodar o instalador do leitor,\n"
                "    na tela 'Choose Components', abra 'Additional language data'\n"
                "    e marque 'Portuguese'.")
    else:
        linha(VERMELHO, "O leitor de texto (Tesseract) não foi encontrado")
        problemas.append(
            "O Windows não achou o leitor de texto.\n"
            "    Pode ser que ele não tenha sido instalado (PASSO 2 do guia),\n"
            "    ou que o endereço dele não tenha sido informado (PASSO 5).")

    # 4. Poppler
    if shutil.which("pdftoppm"):
        linha(VERDE, "Separador de páginas de PDF instalado")
    else:
        linha(VERMELHO, "O separador de páginas de PDF (Poppler) não foi encontrado")
        problemas.append(
            "O Windows não achou o separador de páginas.\n"
            "    Confira se a pasta foi extraída (PASSO 3) e se o endereço\n"
            "    dela foi informado corretamente (PASSO 5). O endereço precisa\n"
            "    terminar em Library\\bin")

    # 5. Pillow
    try:
        import PIL  # noqa: F401
        linha(VERDE, "Complemento de imagem instalado")
    except ImportError:
        linha(VERMELHO, "Complemento de imagem (Pillow) não instalado")
        print()
        print("           Esse complemento é pequeno e endireita páginas")
        print("           digitalizadas de lado. Posso instalar agora?")
        resposta = input("           Digite S para sim, ou tecle Enter para pular: ")
        if resposta.strip().lower().startswith("s"):
            print("           Instalando, aguarde...")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "pillow"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                import PIL  # noqa: F401
                linha(VERDE, "Complemento de imagem instalado agora")
            except ImportError:
                problemas.append(
                    "A instalação automática do complemento não funcionou.\n"
                    "    Não impede o uso: só as páginas de lado ficam ilegíveis.")
        else:
            problemas.append(
                "Complemento de imagem pulado.\n"
                "    Não impede o uso: só as páginas de lado ficam ilegíveis.")

    print()
    print("-" * 66)
    if not problemas:
        print("  TUDO CERTO. O computador está pronto.")
        print()
        print("  Para usar: arraste um arquivo PDF para cima do atalho")
        print("  'reconhecer.bat'. O resultado aparece numa janela sozinho.")
    else:
        print(f"  FALTAM {len(problemas)} ITEM(NS). Veja o que fazer:")
        print()
        for i, p in enumerate(problemas, 1):
            print(f"  {i}. {p}")
            print()
        print("  Depois de corrigir, FECHE esta janela, abra de novo e confira.")
    print("-" * 66)
    print()
    return 0 if not problemas else 1


if __name__ == "__main__":
    raise SystemExit(main())
