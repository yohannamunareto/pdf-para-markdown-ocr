"""Gera os PDFs de teste em data/. Todo o conteúdo é FICTÍCIO.

- digitado.pdf: 3 páginas com texto digital
- escaneado.pdf: 1 página só imagem (certidão fictícia)
- girado.pdf: a mesma página escaneada, de lado
- misto.pdf: digitado + escaneado
- acentos.pdf: texto digital com Í, Á, ª e ² (testa a codificação no Windows)

Uso: python tests/gerar_pdfs_teste.py
"""

import os

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
FONTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"   # ajuste no Windows: C:/Windows/Fonts/arial.ttf


def caminho(nome):
    return os.path.join(DATA, nome)


def digitado():
    c = canvas.Canvas(caminho("digitado.pdf"), pagesize=A4)
    for p in range(1, 4):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(2 * cm, 27 * cm, f"EXECUCAO DE TITULO EXTRAJUDICIAL - PARTE {p}")
        c.setFont("Helvetica", 11)
        y = 25.5 * cm
        for i in range(20):
            c.drawString(2 * cm, y, f"Linha {i + 1} da pagina {p}: valor de R$ 12.345,67 "
                                    f"com vencimento em 10/05/2027.")
            y -= 0.6 * cm
        c.setFont("Helvetica", 8)
        c.drawCentredString(A4[0] / 2, 1 * cm, f"Pagina {p}")
        c.showPage()
    c.save()


def imagem_certidao():
    im = Image.new("L", (1654, 2339), 255)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONTE, 36)
    linhas = ["CERTIDAO DE MATRICULA", "Imovel registrado sob o numero 98.765",
              "Proprietario: Fulano de Tal Ficticio", "Valor da avaliacao: R$ 450.000,00",
              "Data: 15/02/2029"]
    for i, l in enumerate(linhas):
        d.text((150, 150 + 60 * i), l, fill=0, font=f)
    return im


def escaneado_e_girado():
    im = imagem_certidao()
    im.save(caminho("escaneado.pdf"), "PDF", resolution=200)
    im.rotate(90, expand=True).save(caminho("girado.pdf"), "PDF", resolution=200)


def misto():
    w = PdfWriter()
    for f in ("digitado.pdf", "escaneado.pdf"):
        w.append(caminho(f))
    w.write(caminho("misto.pdf"))


def acentos():
    pdfmetrics.registerFont(TTFont("DV", FONTE))
    c = canvas.Canvas(caminho("acentos.pdf"))
    c.setFont("DV", 12)
    y = 780
    for _ in range(12):
        for l in ("EXMO. SR. DR. JUÍZO DA 3ª VARA CÍVEL DA COMARCA DE EXEMPLO",
                  "ÁREA total do imóvel: 1.200 m², avaliação em execução."):
            c.drawString(40, y, l)
            y -= 22
    c.save()


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    digitado()
    escaneado_e_girado()
    misto()
    acentos()
    print("PDFs fictícios gerados em", os.path.abspath(DATA))
