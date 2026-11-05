#!/usr/bin/env python3
"""
pdf2md — conversor de PDF para Markdown 100% determinístico (sem IA / sem LLM).

Toda a estrutura (títulos, listas, tabelas, código, colunas, cabeçalhos e
rodapés) é inferida por heurísticas geométricas e tipográficas extraídas do
próprio PDF: tamanho de fonte, nome da fonte, coordenadas x/y e espaçamento.

Uso básico:
    python3 pdf2md.py arquivo.pdf -o saida.md

Ver todas as opções:
    python3 pdf2md.py --help
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    sys.exit("Erro: pdfplumber não instalado.  Rode: pip install pdfplumber")


__version__ = "1.0.0"


# --------------------------------------------------------------------------- #
# Constantes de heurística (todas ajustáveis por CLI quando fizer sentido)
# --------------------------------------------------------------------------- #

BULLET_CHARS = "•◦▪▫‣⁃∙·●○■□–—*-"
CID_RE = re.compile(r"\(cid:\d+\)")
BULLET_RE = re.compile(rf"^([{re.escape(BULLET_CHARS)}]|\(cid:\d+\))\s+(?=\S)")
ORDERED_RE = re.compile(r"^((?:\d{1,3}|[ivxlcdm]{1,7}|[a-z])[.)])\s+(?=\S)")
PAGENUM_RE = re.compile(
    r"^\s*(?:p[áa]g(?:ina)?\.?\s*)?[-—–\[(]?\s*(\d{1,4}|[ivxlcdm]{1,7})"
    r"\s*(?:/\s*\d{1,4})?\s*[-—–\])]?\s*$",
    re.IGNORECASE,
)
# cabeçalhos e rodapés são curtos; o limite evita apagar texto de corpo
MAX_RUNNING_HEAD_CHARS = 120

MONO_RE = re.compile(r"mono|courier|consol|menlo|inconsolata|source ?code", re.I)
BOLD_RE = re.compile(r"bold|black|heavy|semibold|demibold|-bd|extrab", re.I)
ITALIC_RE = re.compile(r"italic|oblique|-it\b", re.I)

LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st", "\u00a0": " ",
    "\u2010": "-", "\u2011": "-", "\ufeff": "",
}

MD_ESCAPE_RE = re.compile(r"([\\`*\[\]])")


# --------------------------------------------------------------------------- #
# Estruturas de dados
# --------------------------------------------------------------------------- #

@dataclass
class Line:
    """Uma linha visual reconstruída a partir das palavras do PDF."""
    words: List[Dict[str, Any]]
    page: int

    text: str = ""
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    mono: bool = False

    def __post_init__(self) -> None:
        self.words = sorted(self.words, key=lambda w: w["x0"])
        self.text = clean_text(" ".join(w["text"] for w in self.words))
        self.x0 = min(w["x0"] for w in self.words)
        self.x1 = max(w["x1"] for w in self.words)
        self.top = min(w["top"] for w in self.words)
        self.bottom = max(w["bottom"] for w in self.words)

        # tamanho dominante = mediana ponderada pelo nº de caracteres
        sizes: List[float] = []
        for w in self.words:
            sizes.extend([round(float(w.get("size", 0.0)), 1)] * max(len(w["text"]), 1))
        self.size = statistics.median(sizes) if sizes else 0.0

        total = sum(len(w["text"]) for w in self.words) or 1
        self.bold = _font_ratio(self.words, BOLD_RE) / total >= 0.6
        self.italic = _font_ratio(self.words, ITALIC_RE) / total >= 0.6
        self.mono = _font_ratio(self.words, MONO_RE) / total >= 0.8

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)


def _font_ratio(words: Sequence[Dict[str, Any]], regex: re.Pattern) -> int:
    return sum(len(w["text"]) for w in words if regex.search(str(w.get("fontname", ""))))


@dataclass
class Block:
    """Um bloco lógico: parágrafo, título, item de lista ou trecho de código."""
    lines: List[Line]
    kind: str = "paragraph"          # paragraph | heading | list | code
    level: int = 0                   # nível do título ou indentação da lista
    marker: str = ""                 # marcador original da lista
    ordered: bool = False

    @property
    def top(self) -> float:
        return self.lines[0].top

    @property
    def page(self) -> int:
        return self.lines[0].page


@dataclass
class Table:
    bbox: Tuple[float, float, float, float]
    rows: List[List[Optional[str]]]
    page: int

    @property
    def top(self) -> float:
        return self.bbox[1]


@dataclass
class PageData:
    number: int
    width: float
    height: float
    columns: List[List[Line]] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    ocr_text: str = ""


# --------------------------------------------------------------------------- #
# Limpeza de texto
# --------------------------------------------------------------------------- #

def clean_text(text: str) -> str:
    for src, dst in LIGATURES.items():
        text = text.replace(src, dst)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def escape_md(text: str) -> str:
    """
    Escapa só o que o Markdown realmente interpretaria como formatação.
    Escapar tudo deixaria o resultado ilegível ("C\\+\\+", "nome\\_campo").
    """
    text = MD_ESCAPE_RE.sub(r"\\\1", text)
    # sublinhado só é ênfase fora do meio de uma palavra
    text = re.sub(r"(?<![\w])_|_(?![\w])", r"\\_", text)
    return text


def escape_line_start(text: str) -> str:
    """Impede que um parágrafo comece parecendo título, citação ou lista."""
    text = re.sub(r"^(\s*)([#>+])", r"\1\\\2", text)
    text = re.sub(r"^(\s*)([-*])(\s)", r"\1\\\2\3", text)
    return re.sub(r"^(\s*)(\d{1,3})([.)])(\s)", r"\1\2\\\3\4", text)


# --------------------------------------------------------------------------- #
# Etapa 1 — extração de palavras, linhas e colunas
# --------------------------------------------------------------------------- #

def cluster_lines(words: Iterable[Dict[str, Any]], page_no: int) -> List[Line]:
    """Agrupa palavras em linhas visuais pela coordenada vertical."""
    items = sorted(words, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"]))
    lines: List[Line] = []
    buf: List[Dict[str, Any]] = []
    ref: Optional[float] = None

    for w in items:
        mid = (w["top"] + w["bottom"]) / 2
        tol = max((w["bottom"] - w["top"]) * 0.5, 1.5)
        if ref is None or abs(mid - ref) <= tol:
            buf.append(w)
            ref = mid if ref is None else ref
        else:
            lines.append(Line(buf, page_no))
            buf, ref = [w], mid

    if buf:
        lines.append(Line(buf, page_no))
    return [ln for ln in lines if ln.text]


def detect_columns(
    words: Sequence[Dict[str, Any]], width: float, max_columns: int, min_gap: float = 18.0
) -> List[List[Dict[str, Any]]]:
    """
    Detecta colunas procurando faixas verticais totalmente vazias ("calhas").
    Recebe palavras (não linhas), pois linhas de colunas distintas compartilham
    a mesma coordenada vertical e seriam fundidas se agrupadas antes.
    """
    if max_columns <= 1 or len(words) < 40:
        return [list(words)]

    bin_w = 2.0
    nbins = max(int(width / bin_w) + 1, 1)
    counts = [0] * nbins

    # elementos de margem (cabeçalho/rodapé centralizado) e faixas de largura
    # total não podem "tampar" a calha, por isso contamos ocorrências em vez
    # de usar um booleano e toleramos um resíduo pequeno.
    body = [w for w in words if not w.get("_margin")]
    for w in body:
        for b in range(int(w["x0"] / bin_w), min(int(w["x1"] / bin_w) + 1, nbins)):
            counts[b] += 1

    tolerance = max(1, int(len(body) * 0.02))
    occupied = [c > tolerance for c in counts]

    # margens: ignora vazio nas bordas
    try:
        first = occupied.index(True)
        last = len(occupied) - 1 - occupied[::-1].index(True)
    except ValueError:
        return [list(words)]

    gaps: List[Tuple[float, float]] = []
    run_start: Optional[int] = None
    for b in range(first, last + 1):
        if not occupied[b]:
            run_start = b if run_start is None else run_start
        else:
            if run_start is not None and (b - run_start) * bin_w >= min_gap:
                gaps.append((run_start * bin_w, b * bin_w))
            run_start = None

    if not gaps:
        return [list(words)]

    # mantém as maiores calhas, limitado a max_columns-1
    gaps.sort(key=lambda g: g[1] - g[0], reverse=True)
    cuts = sorted((g[0] + g[1]) / 2 for g in gaps[: max_columns - 1])

    columns: List[List[Dict[str, Any]]] = [[] for _ in range(len(cuts) + 1)]
    for w in words:
        center = (w["x0"] + w["x1"]) / 2
        idx = sum(1 for c in cuts if center > c)
        columns[idx].append(w)

    columns = [c for c in columns if c]
    # coluna quase vazia provavelmente é falso positivo (ex.: nota de margem)
    if len(columns) < 2 or min(len(c) for c in columns) < len(words) * 0.15:
        return [list(words)]
    return columns


def word_in_bbox(w: Dict[str, Any], bbox: Tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    cx = (w["x0"] + w["x1"]) / 2
    cy = (w["top"] + w["bottom"]) / 2
    return x0 <= cx <= x1 and top <= cy <= bottom


def extract_pages(pdf: "pdfplumber.PDF", args: argparse.Namespace) -> List[PageData]:
    pages: List[PageData] = []
    selected = parse_page_range(args.pages, len(pdf.pages))

    for idx in selected:
        page = pdf.pages[idx]
        pd = PageData(number=idx + 1, width=page.width, height=page.height)

        tables: List[Table] = []
        if not args.no_tables:
            for tb in page.find_tables():
                try:
                    rows = tb.extract()
                except Exception:
                    continue
                if rows and len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                    tables.append(Table(bbox=tuple(tb.bbox), rows=rows, page=pd.number))
        pd.tables = tables

        words = page.extract_words(
            extra_attrs=["size", "fontname"],
            keep_blank_chars=False,
            use_text_flow=False,
        )
        if tables:
            words = [
                w for w in words
                if not any(word_in_bbox(w, t.bbox) for t in tables)
            ]

        if not words and args.ocr:
            pd.ocr_text = ocr_page(args.input, pd.number, args.ocr_dpi, args.ocr_lang)

        for w in words:
            mid = (w["top"] + w["bottom"]) / 2
            w["_margin"] = (
                mid <= page.height * args.header_zone
                or mid >= page.height * (1 - args.header_zone)
            )

        # colunas primeiro, linhas depois: linhas de colunas vizinhas
        # compartilham a mesma altura e seriam fundidas na ordem inversa
        pd.columns = [
            cluster_lines(group, pd.number)
            for group in detect_columns(words, page.width, args.columns)
        ] if words else []
        pd.columns = [c for c in pd.columns if c]
        pages.append(pd)

    return pages


def parse_page_range(spec: Optional[str], total: int) -> List[int]:
    """Converte '1-5,8' em índices 0-based."""
    if not spec:
        return list(range(total))
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else total
        else:
            start = end = int(part)
        out.extend(range(max(start, 1) - 1, min(end, total)))
    return sorted(set(out))


# --------------------------------------------------------------------------- #
# Etapa 2 — estatísticas globais e remoção de cabeçalho/rodapé
# --------------------------------------------------------------------------- #

def body_font_size(pages: Sequence[PageData]) -> float:
    counter: Counter = Counter()
    for pd in pages:
        for col in pd.columns:
            for ln in col:
                counter[round(ln.size, 1)] += len(ln.text)
    if not counter:
        return 10.0
    return counter.most_common(1)[0][0]


def _normalize_repeat(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def strip_running_heads(pages: List[PageData], zone: float, min_ratio: float) -> int:
    """Remove cabeçalhos, rodapés e números de página repetidos."""
    if len(pages) < 2:
        # ainda assim remove números de página isolados
        min_ratio = 1.1

    def candidates(pd: PageData) -> set:
        """Linhas elegíveis: curtas, na zona de margem e nos extremos da página."""
        alllines = sorted((ln for col in pd.columns for ln in col), key=lambda l: l.top)
        extremes = alllines[:2] + alllines[-2:]
        out = set()
        for ln in extremes:
            in_zone = ln.top <= pd.height * zone or ln.bottom >= pd.height * (1 - zone)
            if in_zone and len(ln.text) <= MAX_RUNNING_HEAD_CHARS:
                out.add(id(ln))
        return out

    page_candidates = {pd.number: candidates(pd) for pd in pages}

    counter: Counter = Counter()
    for pd in pages:
        seen = set()
        for col in pd.columns:
            for ln in col:
                if id(ln) in page_candidates[pd.number]:
                    key = _normalize_repeat(ln.text)
                    if key and key not in seen:
                        seen.add(key)
                        counter[key] += 1

    threshold = max(2, int(len(pages) * min_ratio))
    repeated = {k for k, v in counter.items() if v >= threshold}

    removed = 0
    for pd in pages:
        for i, col in enumerate(pd.columns):
            kept = []
            for ln in col:
                if id(ln) in page_candidates[pd.number] and (
                    _normalize_repeat(ln.text) in repeated or PAGENUM_RE.match(ln.text)
                ):
                    removed += 1
                    continue
                kept.append(ln)
            pd.columns[i] = kept
    return removed


# --------------------------------------------------------------------------- #
# Etapa 3 — agrupamento em blocos e classificação
# --------------------------------------------------------------------------- #

def split_blocks(lines: Sequence[Line], body_size: float) -> List[Block]:
    """Quebra a sequência de linhas em blocos lógicos."""
    blocks: List[Block] = []
    buf: List[Line] = []

    gaps = [
        lines[i + 1].top - lines[i].bottom
        for i in range(len(lines) - 1)
        if 0 <= lines[i + 1].top - lines[i].bottom < body_size * 3
    ]
    # o espaçamento "normal" dentro do parágrafo é estimado pelo percentil 25,
    # o que evita que títulos e saltos entre blocos inflem a referência
    normal_gap = sorted(gaps)[len(gaps) // 4] if gaps else body_size * 0.3
    gap_limit = normal_gap + max(body_size * 0.45, 2.0)
    right_edge = max(l.x1 for l in lines)

    for i, ln in enumerate(lines):
        if not buf:
            buf.append(ln)
            continue

        prev = buf[-1]
        gap = ln.top - prev.bottom
        both_mono = ln.mono and prev.mono

        starts_list = bool(BULLET_RE.match(ln.text) or ORDERED_RE.match(ln.text))
        size_changed = abs(ln.size - prev.size) > max(body_size * 0.08, 0.6)
        style_changed = ln.mono != prev.mono or (
            ln.bold != prev.bold and (ln.size > body_size * 1.05 or prev.size > body_size * 1.05)
        )
        indent_jump = (ln.x0 - prev.x0) > body_size * 1.4 and gap > normal_gap * 0.5

        # linha anterior terminou a frase antes da margem direita -> novo parágrafo
        short_end = (
            prev.x1 < right_edge - body_size * 2
            and prev.text.rstrip().endswith((".", "!", "?", ":", ";", '"'))
            and bool(re.match(r"^[A-ZÀ-Ý0-9(\[]", ln.text))
            and gap >= normal_gap * 0.9
        )

        if both_mono:
            # código: só separa em intervalos claramente grandes
            if gap > gap_limit * 2.5:
                blocks.append(Block(buf))
                buf = [ln]
            else:
                buf.append(ln)
            continue

        if gap > gap_limit or starts_list or size_changed or style_changed or indent_jump or short_end:
            blocks.append(Block(buf))
            buf = [ln]
        else:
            buf.append(ln)

    if buf:
        blocks.append(Block(buf))
    return blocks


def classify_blocks(
    blocks: List[Block], body_size: float, heading_sizes: Dict[float, int], args
) -> None:
    for blk in blocks:
        first = blk.lines[0]
        text = first.text

        size_key = round(first.size, 1)
        if size_key in heading_sizes and len(blk.lines) <= 3 and len(text) <= 250:
            blk.kind = "heading"
            blk.level = heading_sizes[size_key]
            continue

        if all(l.mono for l in blk.lines) and not args.no_code:
            blk.kind = "code"
            continue

        m_b = BULLET_RE.match(text)
        m_o = ORDERED_RE.match(text)
        if m_b or m_o:
            blk.kind = "list"
            blk.ordered = bool(m_o)
            blk.marker = (m_b or m_o).group(1)
            continue

        if (
            not args.no_bold_headings
            and first.bold
            and len(blk.lines) == 1
            and 2 <= len(text) <= 90
            and not text.rstrip().endswith((".", ",", ";", ":"))
            and first.size >= body_size * 0.95
        ):
            blk.kind = "heading"
            blk.level = max(heading_sizes.values(), default=1) + 1
            continue

        blk.kind = "paragraph"


def build_heading_map(
    pages: Sequence[PageData], body_size: float, max_levels: int = 6
) -> Dict[float, int]:
    """Mapeia tamanhos de fonte maiores que o corpo em níveis H1..H6."""
    counter: Counter = Counter()
    for pd in pages:
        for col in pd.columns:
            for ln in col:
                size = round(ln.size, 1)
                if size > body_size * 1.10 and len(ln.text) <= 250:
                    counter[size] += 1

    # descarta tamanhos raríssimos (ruído de fórmulas / notas)
    sizes = [s for s, c in counter.items() if c >= 1]
    sizes.sort(reverse=True)
    return {s: min(i + 1, max_levels) for i, s in enumerate(sizes[:max_levels])}


# --------------------------------------------------------------------------- #
# Etapa 4 — renderização Markdown
# --------------------------------------------------------------------------- #

def join_lines(lines: Sequence[Line], escape: bool, style: bool = True) -> str:
    """Junta linhas de um parágrafo desfazendo hifenização de quebra."""
    parts: List[str] = []
    for i, ln in enumerate(lines):
        txt = inline_format(ln, escape, style)
        if i and parts:
            prev = parts[-1]
            if re.search(r"[a-zà-ÿ]-$", prev) and re.match(r"^[a-zà-ÿ]", txt):
                parts[-1] = prev[:-1] + txt
                continue
            parts[-1] = prev + " " + txt
        else:
            parts.append(txt)
    return "".join(parts).strip()


def inline_format(line: Line, escape: bool, style: bool = True) -> str:
    """Aplica **negrito** e *itálico* por trechos contíguos da linha."""
    if not style:
        text = " ".join(w["text"] for w in line.words)
        return clean_text(escape_md(text) if escape else text)

    out: List[str] = []
    run: List[str] = []
    run_style: Optional[Tuple[bool, bool]] = None

    def flush() -> None:
        if not run:
            return
        text = " ".join(run)
        bold, italic = run_style or (False, False)
        if escape:
            text = escape_md(text)
        if bold and italic:
            text = f"***{text}***"
        elif bold:
            text = f"**{text}**"
        elif italic:
            text = f"*{text}*"
        out.append(text)
        run.clear()

    for w in line.words:
        font = str(w.get("fontname", ""))
        style = (bool(BOLD_RE.search(font)), bool(ITALIC_RE.search(font)))
        if run_style is None or style == run_style:
            run_style = style
            run.append(w["text"])
        else:
            flush()
            run_style = style
            run.append(w["text"])
    flush()
    return clean_text(" ".join(out))


def render_table(table: Table) -> str:
    rows = [[(c or "").strip().replace("\n", " ") for c in row] for row in table.rows]
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    rows = [[c.replace("|", "\\|") for c in r] for r in rows]

    header, *body = rows
    if not any(header):
        header = [f"Coluna {i+1}" for i in range(ncols)]
        body = rows[1:] if len(rows) > 1 else []

    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(["---"] * ncols) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def render_block(blk: Block, args, list_base: Dict[int, float]) -> str:
    escape = not args.no_escape

    if blk.kind == "heading":
        level = min(max(blk.level, 1), 6)
        text = join_lines(blk.lines, escape=False, style=False)
        text = re.sub(r"\s+", " ", text).strip().lstrip("#").strip()
        return f"{'#' * level} {text}" if text else ""

    if blk.kind == "code":
        base_x = min(l.x0 for l in blk.lines)
        char_w = max(blk.lines[0].size * 0.6, 1.0)
        rows = []
        for ln in blk.lines:
            pad = " " * max(int(round((ln.x0 - base_x) / char_w)), 0)
            rows.append(pad + CID_RE.sub("", ln.text))
        return "```\n" + "\n".join(rows) + "\n```"

    if blk.kind == "list":
        first = blk.lines[0]
        base = list_base.setdefault(blk.page, first.x0)
        indent = min(max(int((first.x0 - base) / 18), 0), 5)

        head = strip_marker(first, blk.marker)
        text = join_lines([head] + list(blk.lines[1:]), escape=escape) if head else \
            join_lines(list(blk.lines[1:]), escape=escape)

        if blk.ordered and re.fullmatch(r"\d{1,3}[.)]", blk.marker):
            bullet = blk.marker.replace(")", ".")
        elif blk.ordered:
            bullet = "-"
            text = f"{blk.marker} {text}"
        else:
            bullet = "-"
        return "  " * indent + f"{bullet} {text.strip()}"

    text = join_lines(blk.lines, escape=escape)
    return escape_line_start(text) if escape else text


def strip_marker(line: Line, marker: str) -> Optional[Line]:
    """Devolve a linha sem o marcador de lista inicial."""
    words = list(line.words)
    if words and words[0]["text"].strip() in (marker, marker.rstrip(".)")):
        words = words[1:]
    elif words and words[0]["text"].startswith(marker):
        w = dict(words[0])
        w["text"] = w["text"][len(marker):].lstrip()
        words = ([w] if w["text"] else []) + words[1:]
    return Line(words, line.page) if words else None


def render_page(pd: PageData, body_size: float, heading_map, args) -> str:
    chunks: List[str] = []
    list_base: Dict[int, float] = {}

    if pd.ocr_text:
        chunks.append(pd.ocr_text.strip())

    for col in pd.columns:
        blocks = split_blocks(col, body_size)
        classify_blocks(blocks, body_size, heading_map, args)

        elements: List[Tuple[float, str]] = []
        for blk in blocks:
            md = render_block(blk, args, list_base)
            if md.strip():
                elements.append((blk.top, md))

        # tabelas entram na posição vertical correta dentro da coluna
        col_x = (
            (min(l.x0 for l in col), max(l.x1 for l in col)) if col else (0, pd.width)
        )
        for t in pd.tables:
            cx = (t.bbox[0] + t.bbox[2]) / 2
            if len(pd.columns) == 1 or col_x[0] - 20 <= cx <= col_x[1] + 20:
                elements.append((t.top, render_table(t)))

        elements.sort(key=lambda e: e[0])
        chunks.extend(md for _, md in elements)

    # tabelas de páginas sem texto algum
    if not pd.columns:
        for t in pd.tables:
            chunks.append(render_table(t))

    for img in pd.images:
        chunks.append(f"![imagem da página {pd.number}]({img})")

    return merge_chunks(chunks)


def merge_chunks(chunks: Sequence[str]) -> str:
    """Junta blocos com espaçamento correto, agrupando itens de lista."""
    out: List[str] = []
    for chunk in chunks:
        is_list = bool(re.match(r"^\s*(?:[-*+]|\d{1,3}[.)])\s", chunk))
        prev_list = bool(out) and bool(
            re.match(r"^\s*(?:[-*+]|\d{1,3}[.)])\s", out[-1].splitlines()[-1])
        )
        if out and is_list and prev_list:
            out[-1] = out[-1] + "\n" + chunk
        else:
            out.append(chunk)
    return "\n\n".join(out)


# --------------------------------------------------------------------------- #
# Recursos opcionais: OCR e extração de imagens
# --------------------------------------------------------------------------- #

def ocr_page(pdf_path: str, page_no: int, dpi: int, lang: str) -> str:
    """Fallback para páginas escaneadas. Requer poppler-utils + tesseract."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "pg")
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page_no),
                 "-l", str(page_no), pdf_path, prefix],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
        texts = []
        for f in sorted(glob.glob(prefix + "*.png")):
            try:
                texts.append(pytesseract.image_to_string(Image.open(f), lang=lang))
            except Exception:
                pass
        return "\n".join(t.strip() for t in texts if t.strip())


def extract_images(pdf_path: str, page_no: int, outdir: str, min_bytes: int) -> List[str]:
    """Extrai imagens rasterizadas da página com pdfimages (poppler-utils)."""
    os.makedirs(outdir, exist_ok=True)
    prefix = os.path.join(outdir, f"pagina-{page_no:03d}")
    try:
        subprocess.run(
            ["pdfimages", "-png", "-f", str(page_no), "-l", str(page_no), pdf_path, prefix],
            check=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    found = []
    for f in sorted(glob.glob(prefix + "*.png")):
        if os.path.getsize(f) >= min_bytes:
            found.append(f)
        else:
            os.remove(f)
    return found


# --------------------------------------------------------------------------- #
# Montagem final do documento
# --------------------------------------------------------------------------- #

def yaml_front_matter(meta: Dict[str, Any], source: str, npages: int) -> str:
    def esc(v: Any) -> str:
        return '"' + str(v).replace('"', "'").replace("\n", " ").strip() + '"'

    fields = [("source", esc(os.path.basename(source))), ("pages", npages)]
    for key, label in (("Title", "title"), ("Author", "author"),
                       ("Subject", "subject"), ("CreationDate", "created")):
        val = str(meta.get(key) or "").strip()
        if val and val.lower() not in ("(anonymous)", "(unspecified)", "untitled", "none"):
            fields.append((label, esc(val)))
    body = "\n".join(f"{k}: {v}" for k, v in fields)
    return f"---\n{body}\n---\n"


def convert(path: str, args: argparse.Namespace) -> Tuple[str, Dict[str, Any]]:
    with pdfplumber.open(path, password=args.password or "") as pdf:
        pages = extract_pages(pdf, args)
        meta = dict(pdf.metadata or {})
        total = len(pdf.pages)

    if args.images_dir:
        for pd in pages:
            rel_dir = args.images_dir
            imgs = extract_images(path, pd.number, rel_dir, args.min_image_bytes)
            pd.images = [os.path.relpath(i, os.path.dirname(args.output) or ".")
                         for i in imgs]

    removed = 0
    if not args.keep_headers:
        removed = strip_running_heads(pages, args.header_zone, args.header_ratio)

    body_size = body_font_size(pages)
    heading_map = build_heading_map(pages, body_size, args.max_heading_levels)

    parts: List[str] = []
    for pd in pages:
        md = render_page(pd, body_size, heading_map, args)
        if args.page_markers:
            parts.append(f"<!-- página {pd.number} -->")
        if md.strip():
            parts.append(md)

    doc = "\n\n".join(p for p in parts if p.strip())
    doc = re.sub(r"\n{3,}", "\n\n", doc).strip() + "\n"

    if args.front_matter:
        doc = yaml_front_matter(meta, path, total) + "\n" + doc

    stats = {
        "paginas_processadas": len(pages),
        "paginas_totais": total,
        "tabelas": sum(len(p.tables) for p in pages),
        "imagens": sum(len(p.images) for p in pages),
        "linhas_removidas_cabecalho_rodape": removed,
        "tamanho_fonte_corpo": body_size,
        "niveis_titulo": len(heading_map),
    }
    return doc, stats


def convert_split(path: str, args: argparse.Namespace) -> Dict[str, Any]:
    """Escreve um arquivo .md por página dentro do diretório de saída."""
    outdir = args.output
    os.makedirs(outdir, exist_ok=True)
    with pdfplumber.open(path, password=args.password or "") as pdf:
        pages = extract_pages(pdf, args)

    if not args.keep_headers:
        strip_running_heads(pages, args.header_zone, args.header_ratio)
    body_size = body_font_size(pages)
    heading_map = build_heading_map(pages, body_size, args.max_heading_levels)

    written = []
    base = os.path.splitext(os.path.basename(path))[0]
    for pd in pages:
        md = render_page(pd, body_size, heading_map, args).strip()
        fname = os.path.join(outdir, f"{base}-{pd.number:04d}.md")
        with open(fname, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
        written.append(fname)
    return {"arquivos": written, "paginas": len(pages)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf2md",
        description="Converte PDF em Markdown por heurísticas determinísticas (sem IA).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  pdf2md.py livro.pdf -o livro.md\n"
            "  pdf2md.py artigo.pdf -o artigo.md --columns 2 --front-matter\n"
            "  pdf2md.py relatorio.pdf -o relatorio.md --images-dir img --page-markers\n"
            "  pdf2md.py escaneado.pdf -o out.md --ocr --ocr-lang por\n"
            "  pdf2md.py apostila.pdf -o paginas/ --split-pages --pages 10-40\n"
        ),
    )
    p.add_argument("input", help="arquivo PDF de entrada")
    p.add_argument("-o", "--output", help="arquivo .md de saída (ou diretório com --split-pages)")
    p.add_argument("--pages", help="intervalo de páginas, ex.: 1-10,15,20-")
    p.add_argument("--password", help="senha do PDF, se protegido")

    g = p.add_argument_group("estrutura")
    g.add_argument("--columns", type=int, default=1, metavar="N",
                   help="número máximo de colunas a detectar (padrão: 1)")
    g.add_argument("--max-heading-levels", type=int, default=6,
                   help="quantos tamanhos de fonte viram títulos H1..HN (padrão: 6)")
    g.add_argument("--no-bold-headings", action="store_true",
                   help="não tratar linhas curtas em negrito como títulos")
    g.add_argument("--no-tables", action="store_true", help="ignorar tabelas")
    g.add_argument("--no-code", action="store_true",
                   help="não converter fonte monoespaçada em bloco de código")
    g.add_argument("--no-escape", action="store_true",
                   help="não escapar caracteres especiais do Markdown")

    h = p.add_argument_group("cabeçalho e rodapé")
    h.add_argument("--keep-headers", action="store_true",
                   help="preservar cabeçalhos, rodapés e números de página")
    h.add_argument("--header-zone", type=float, default=0.06,
                   help="fração da altura tratada como zona de margem (padrão: 0.06)")
    h.add_argument("--header-ratio", type=float, default=0.5,
                   help="fração de páginas em que a linha deve repetir (padrão: 0.5)")

    e = p.add_argument_group("extras")
    e.add_argument("--page-markers", action="store_true",
                   help="inserir comentário HTML marcando cada página")
    e.add_argument("--front-matter", action="store_true",
                   help="incluir front matter YAML com os metadados do PDF")
    e.add_argument("--split-pages", action="store_true",
                   help="gerar um arquivo .md por página")
    e.add_argument("--images-dir", metavar="DIR",
                   help="extrair imagens para DIR e referenciá-las no Markdown")
    e.add_argument("--min-image-bytes", type=int, default=4096,
                   help="descarta imagens menores que N bytes (padrão: 4096)")
    e.add_argument("--ocr", action="store_true",
                   help="usar OCR (tesseract) em páginas sem camada de texto")
    e.add_argument("--ocr-lang", default="por", help="idioma do OCR (padrão: por)")
    e.add_argument("--ocr-dpi", type=int, default=300, help="DPI do OCR (padrão: 300)")
    e.add_argument("-q", "--quiet", action="store_true", help="não exibir estatísticas")
    e.add_argument("--version", action="version", version=f"pdf2md {__version__}")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"Erro: arquivo não encontrado: {args.input}", file=sys.stderr)
        return 1

    if not args.output:
        args.output = os.path.splitext(args.input)[0] + (
            "" if args.split_pages else ".md"
        )

    try:
        if args.split_pages:
            stats = convert_split(args.input, args)
            if not args.quiet:
                print(f"OK: {stats['paginas']} arquivo(s) em {args.output}", file=sys.stderr)
            return 0

        doc, stats = convert(args.input, args)
    except Exception as exc:  # erro de PDF corrompido, senha etc.
        print(f"Erro ao converter: {exc}", file=sys.stderr)
        return 2

    if args.output == "-":
        sys.stdout.write(doc)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(doc)

    if not args.quiet:
        print(f"OK -> {args.output}", file=sys.stderr)
        for k, v in stats.items():
            print(f"  {k}: {v}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
