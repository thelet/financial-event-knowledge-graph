"""Table extraction, rendering, and classification.

Structured rows are authoritative. Markdown and plain text are pure deterministic
functions of the rows, so verification can recompute and compare them — three
representations that cannot drift apart.

Classification never removes a table and never changes how it is stored. EX-21.1 holds 511
characters of text and 68 table rows, so any rule that discards a class of table empties
the document.
"""

from __future__ import annotations

import re

from lxml.html import HtmlElement

from ...core.models import ParsedTable
from ...utils.text import normalize_whitespace
from .html_text import element_text

MAX_RENDERED_CELL = 200


def extract_table(
    element: HtmlElement,
    *,
    wide_table_columns: int = 12,
    layout_max_rows: int = 1,
    layout_max_cols: int = 1,
) -> ParsedTable:
    """Build a ParsedTable from a <table> element.

    Only the table's *own* rows are taken. A nested table is left to be handled as its own
    block, so an outer layout table wrapping a data table does not swallow it.
    """
    rows: list[list[str]] = []
    has_merged = False

    for tr in _own_rows(element):
        cells: list[str] = []
        for cell in tr.xpath("./td|./th"):
            if cell.get("colspan") or cell.get("rowspan"):
                has_merged = True
            cells.append(normalize_whitespace(_cell_text(cell)))
        if cells:
            rows.append(cells)

    rows = _drop_empty_edges(rows)
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (n_cols - len(r)) for r in rows]

    header_rows = _count_header_rows(element, rows)
    kind, confidence = classify_table(rows, header_rows, layout_max_rows, layout_max_cols)

    flags: list[str] = []
    if n_cols > wide_table_columns:
        flags.append("wide_table")

    return ParsedTable(
        rows=rows,
        header_rows=header_rows,
        n_rows=n_rows,
        n_cols=n_cols,
        has_merged_cells=has_merged,
        markdown=render_markdown(rows, header_rows),
        plain_text=render_plain_text(rows),
        table_kind=kind,
        kind_confidence=confidence,
        caption=_caption(element),
        flags=flags,
    )


def _own_rows(element: HtmlElement) -> list[HtmlElement]:
    """Rows belonging to this table, not to a nested one."""
    return [
        tr
        for tr in element.iter("tr")
        if _nearest_table(tr) is element
    ]


def _nearest_table(node: HtmlElement) -> HtmlElement | None:
    parent = node.getparent()
    while parent is not None:
        if parent.tag == "table":
            return parent
        parent = parent.getparent()
    return None


def _cell_text(cell: HtmlElement) -> str:
    """Cell text with block separators, so adjacent divs do not run together."""
    return element_text(cell)


def _drop_empty_edges(rows: list[list[str]]) -> list[list[str]]:
    """Remove wholly empty leading/trailing rows; SEC tables are padded with them."""
    while rows and not any(c.strip() for c in rows[0]):
        rows.pop(0)
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    return rows


def _count_header_rows(element: HtmlElement, rows: list[list[str]]) -> int:
    if element.xpath("./thead//tr") or element.xpath(".//th"):
        explicit = len([tr for tr in element.xpath("./thead//tr")])
        if explicit:
            return min(explicit, len(rows))
        return 1 if rows else 0
    return 0


def _caption(element: HtmlElement) -> str | None:
    caption = element.find("caption")
    if caption is not None:
        text = normalize_whitespace(caption.text_content())
        if text:
            return text
    return None


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


def classify_table(
    rows: list[list[str]],
    header_rows: int,
    layout_max_rows: int = 1,
    layout_max_cols: int = 1,
) -> tuple[str, float]:
    """Conservative classification. `unknown` whenever the signal is weak.

    A legal or narrative table is not scaffolding merely because it holds no digits, so
    the absence of numbers is never on its own enough to call something layout.
    """
    if not rows:
        return "layout", 0.9

    n_rows = len(rows)
    n_cols = max(len(r) for r in rows)
    non_empty = [c for row in rows for c in row if c.strip()]
    if not non_empty:
        return "layout", 0.95

    # Structural layout: a single cell, or a single row/column used for positioning.
    if n_rows <= layout_max_rows and n_cols <= layout_max_cols:
        return "layout", 0.9
    if n_rows == 1 or n_cols == 1:
        return "layout", 0.6

    numeric_cells = sum(1 for c in non_empty if _looks_numeric(c))
    numeric_ratio = numeric_cells / len(non_empty)
    mean_len = sum(len(c) for c in non_empty) / len(non_empty)

    # Strong data signal: a grid with numbers, or an explicit header row over a grid.
    if numeric_ratio >= 0.30 and n_rows >= 2 and n_cols >= 2:
        return "data", min(0.95, 0.5 + numeric_ratio)
    if header_rows > 0 and n_rows >= 2 and n_cols >= 2:
        return "data", 0.7

    # Repeating short-cell grid with no numbers: a list rendered as a table, e.g. the
    # EX-21.1 subsidiary list. Data, not scaffolding.
    if n_rows >= 3 and n_cols >= 2 and mean_len <= 60:
        return "data", 0.6

    # Long prose in a multi-cell grid: genuinely ambiguous.
    if mean_len > 200:
        return "mixed", 0.4

    return "unknown", 0.3


_NUMERIC = re.compile(r"^[\s$€£(]*-?[\d,.]+\s*[%)]*\s*$")


def _looks_numeric(cell: str) -> bool:
    text = cell.strip()
    if not text or text in {"$", "%", "—", "-", "–"}:
        return False
    return bool(_NUMERIC.match(text))


# --------------------------------------------------------------------------------------
# Rendering — pure functions of `rows`
# --------------------------------------------------------------------------------------


def render_markdown(rows: list[list[str]], header_rows: int = 0) -> str:
    """Deterministic Markdown. Verification recomputes this and compares."""
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    padded = [r + [""] * (n_cols - len(r)) for r in rows]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(_escape_cell(c) for c in cells) + " |"

    if header_rows > 0:
        head, body = padded[:header_rows], padded[header_rows:]
        out = [line(r) for r in head]
        out.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
        out.extend(line(r) for r in body)
    else:
        out = ["| " + " | ".join("" for _ in range(n_cols)) + " |",
               "| " + " | ".join("---" for _ in range(n_cols)) + " |"]
        out.extend(line(r) for r in padded)
    return "\n".join(out)


def render_plain_text(rows: list[list[str]]) -> str:
    """Deterministic row-joined text. Verification recomputes this and compares."""
    return "\n".join(" | ".join(c for c in row) for row in rows)


def _escape_cell(cell: str) -> str:
    text = normalize_whitespace(cell).replace("|", "\\|")
    if len(text) > MAX_RENDERED_CELL:
        text = text[: MAX_RENDERED_CELL - 1] + "…"
    return text
