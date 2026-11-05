"""Roda o ocr-lote.py nos PDFs fictícios de data/. Precisa de Tesseract
(com português) e Poppler instalados; sem eles, os testes são pulados."""

import os
import shutil
import subprocess
import sys

import pytest

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(RAIZ, "src", "ocr-lote.py")

pytestmark = pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("pdftoppm")),
                                reason="Tesseract ou Poppler não instalados")


def converter(pdf, tmp_path):
    saida = tmp_path / (os.path.splitext(os.path.basename(pdf))[0] + ".md")
    r = subprocess.run([sys.executable, SCRIPT, pdf, "-o", str(saida), "--nucleos", "2"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return saida.read_text(encoding="utf-8")


def test_misto(data_dir, tmp_path):
    md = converter(os.path.join(data_dir, "misto.pdf"), tmp_path)
    assert "Páginas processadas: 4" in md
    assert "Lidas por OCR: 1" in md
    assert "CERTIDAO DE MATRICULA" in md and "450.000,00" in md
    assert md.count("valor de R$ 12.345,67") == 60          # texto digital intacto


def test_pagina_girada(data_dir, tmp_path):
    md = converter(os.path.join(data_dir, "girado.pdf"), tmp_path)
    assert "giradas corrigidas: 1" in md
    assert "CERTIDAO DE MATRICULA" in md


def test_acentos(data_dir, tmp_path):
    md = converter(os.path.join(data_dir, "acentos.pdf"), tmp_path)
    assert md.count("JUÍZO DA 3ª VARA CÍVEL") == 12
    assert "ÁREA total do imóvel: 1.200 m²" in md
