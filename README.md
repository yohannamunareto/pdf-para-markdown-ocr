# Conversor de PDF para Markdown com OCR

App de arrastar e soltar que deixa autos gigantes prontos para a IA ler, sem instalar nada.

> **Confidencialidade:** esta ferramenta foi construída para uso interno de um escritório de advocacia. O código e os dados não são públicos. Este repositório documenta o problema, a solução e os resultados, sem nomes de clientes, partes ou processos.

## Problema

PDFs de tribunal são enormes, muitas vezes digitalizados, e carregam uma camada técnica invisível de assinatura digital que vira lixo na extração de texto. Colegas sem perfil técnico precisavam converter esses arquivos sem usar terminal.

## Como funciona

- Arquivo HTML único: a pessoa arrasta o PDF e recebe o Markdown com a estrutura detectada por heurísticas determinísticas.
- OCR automático nas páginas digitalizadas e filtro da camada técnica de assinatura.
- Script em Python para OCR em lote, com processamento paralelo, correção de rotação, marcação de páginas com baixa confiança e retomada após interrupção.
- Guia de instalação e atalhos de um clique para colegas não técnicos.

## Ferramentas

HTML e JavaScript (pdf.js), OCR, Python.

## Resultados

- Processa documentos com mais de 1.500 páginas.
- Aprovado em 55 de 55 critérios de auditoria, que conferiram valores, datas e números extraídos contra os originais.
- Usado também pela chefia do escritório.
