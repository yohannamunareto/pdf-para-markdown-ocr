"""Regras de conferência: validação de números e classificação de páginas.
Todos os números usados aqui são fictícios ou exemplos públicos de formato."""


def test_cpf(ocr_lote):
    assert ocr_lote.cpf_valido("111.444.777-35")
    assert not ocr_lote.cpf_valido("111.444.777-36")      # dígito errado
    assert not ocr_lote.cpf_valido("111.111.111-11")      # repetido


def test_cnpj(ocr_lote):
    assert ocr_lote.cnpj_valido("11.222.333/0001-81")
    assert not ocr_lote.cnpj_valido("11.222.333/0001-82")


def test_numero_processo_cnj(ocr_lote):
    # dígito verificador módulo 97 (Resolução CNJ 65/2008), número fictício
    n, ano, j, tr, orig = "0000001", "2024", "8", "16", "0001"
    dd = 98 - int(n + ano + j + tr + orig + "00") % 97
    certo = f"{n}-{dd:02d}.{ano}.{j}.{tr}.{orig}"
    errado = f"{n}-{(dd + 1) % 100:02d}.{ano}.{j}.{tr}.{orig}"
    assert ocr_lote.cnj_valido(certo)
    assert not ocr_lote.cnj_valido(errado)


def test_datas(ocr_lote):
    assert ocr_lote.data_valida("29/02/2024")
    assert not ocr_lote.data_valida("31/04/2020")
    assert not ocr_lote.data_valida("11/42/2008")         # mês impossível, típico de OCR
    assert not ocr_lote.data_valida("10/05/2815")


def test_numero_relevante(ocr_lote):
    r = ocr_lote.RE_NUMERO_RELEVANTE
    for t in ("R$ 450.000,00", "R$ 2.5OO,OO", "15/02/2029", "nº 98.765", "conta 320.452-9"):
        assert r.search(t), t
    for t in ("Fls. 2", "19", "1988"):
        assert not r.search(t), t


def test_faixas(ocr_lote):
    assert ocr_lote.faixas_continuas([3, 4, 5, 9, 10]) == [(3, 5), (9, 10)]
    assert ocr_lote.faixas_continuas(list(range(1, 20)), 8) == [(1, 8), (9, 16), (17, 19)]
    assert ocr_lote.parse_paginas("2-4,7", 10) == [2, 3, 4, 7]


def test_fonte_defeituosa(ocr_lote):
    assert ocr_lote.fonte_defeituosa("AU丁OR. BANCO 巨X")
    assert not ocr_lote.fonte_defeituosa("AUTOR: EMPRESA FICTÍCIA LTDA")


def test_classificar(ocr_lote):
    boas = [["Valor da causa: R$ 10.000,00", 90.0], ["Data: 10/05/2027", 88.0]] * 30
    assert ocr_lote.classificar(boas, 89.0, 300)["revisar"] is False

    numero_em_linha_fraca = boas + [["Valor: R$ 1O.OOO,OO", 30.0]]
    assert ocr_lote.classificar(numero_em_linha_fraca, 85.0, 300)["revisar"] is True

    cnpj_invalido = boas + [["CNPJ 11.222.333/0001-82", 90.0]]
    c = ocr_lote.classificar(cnpj_invalido, 89.0, 300)
    assert c["revisar"] is True and c["invalidos"]

    carimbo = [["JUNTADA", 40.0], ["aos ... dias", 35.0]]
    assert ocr_lote.classificar(carimbo, 38.0, 20)["tipo"] == "pouco_texto"
