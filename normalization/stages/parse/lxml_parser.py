"""Direct lxml implementation of `DocumentParser`.

Serves two purposes: it is the fallback when the default parser hits a hard failure, and
it is a genuinely independent second implementation, which is the only way to know the
parser protocol is a real seam rather than a sec-parser-shaped hole.

Walks the DOM emitting one block per block-level element that owns text, using
`element_text` so block boundaries get separators and inline runs do not.
"""

from __future__ import annotations

import time

from lxml.html import HtmlElement

from ...core.identity import sha256_text
from ...core.models import ParsedBlock, ParsedDocument, SelectedArtifact, SourceStyle
from ...utils.text import normalize_whitespace
from .html_text import (
    count_images,
    element_text,
    font_size_of,
    font_weight_of,
    inside_table,
    parse_html_bytes,
    strip_non_content,
    text_align_of,
)
from .public import ParserError, TRIGGER_NO_BLOCKS
from .tables import extract_table

PARSER_NAME = "lxml"
PARSER_VERSION = "1.0.0"

# Elements that can own a block of text. `table` is handled separately.
_TEXT_BLOCKS = frozenset({"p", "div", "li", "td", "th", "dd", "dt", "blockquote", "center",
                          "h1", "h2", "h3", "h4", "h5", "h6", "caption", "pre"})
_LIST_TAGS = frozenset({"li", "dd", "dt"})


class LxmlDocumentParser:
    """Structure-preserving HTML parser built directly on lxml."""

    name = PARSER_NAME
    version = PARSER_VERSION

    def __init__(
        self,
        *,
        min_font_weight: int = 600,
        max_heading_chars: int = 200,
        wide_table_columns: int = 12,
        layout_max_rows: int = 1,
        layout_max_cols: int = 1,
    ) -> None:
        self._min_font_weight = min_font_weight
        self._max_heading_chars = max_heading_chars
        self._wide = wide_table_columns
        self._layout_max_rows = layout_max_rows
        self._layout_max_cols = layout_max_cols

    def supports(self, artifact: SelectedArtifact) -> bool:
        return artifact.media_type == "text/html"

    def parse(self, artifact: SelectedArtifact, raw: bytes) -> ParsedDocument:
        started = time.time()
        root = parse_html_bytes(raw)
        strip_non_content(root)

        blocks: list[ParsedBlock] = []
        warnings: list[str] = []
        self._emit(root, blocks)

        blocks = [b for b in blocks if b.text or b.block_type == "table"]
        for index, block in enumerate(blocks):
            block.block_sequence = index

        if not blocks:
            raise ParserError(TRIGGER_NO_BLOCKS, f"lxml produced no blocks for {artifact.artifact_id}")

        return ParsedDocument(
            artifact_id=artifact.artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            blocks=blocks,
            title=_first_heading(blocks),
            warnings=warnings,
            parse_duration_ms=int((time.time() - started) * 1000),
        )

    # -- traversal ---------------------------------------------------------------------

    def _emit(self, element: HtmlElement, blocks: list[ParsedBlock]) -> None:
        """Depth-first, emitting the outermost block element that owns text.

        Emitting the outermost owner avoids the over-fragmentation a naive leaf walk
        produces on SEC markup, where a paragraph is often wrapped in several divs.

        No visited-set is kept. An earlier version tracked emitted elements by `id()`,
        which is not safe with lxml: element proxies are created on demand and destroyed,
        and CPython reuses `id()` values, so a table could be skipped because its address
        matched a long-dead proxy. That made output non-deterministic across runs. The set
        was also unnecessary -- both emitting branches `continue` without descending, so
        nothing inside an emitted element is ever visited twice.
        """
        for child in element:
            if not isinstance(child.tag, str):
                continue
            tag = child.tag.lower()

            if tag == "table":
                blocks.append(self._table_block(child, len(blocks)))
                continue

            if tag in _TEXT_BLOCKS and self._owns_text(child):
                blocks.append(self._text_block(child, len(blocks)))
                continue

            self._emit(child, blocks)

    def _owns_text(self, element: HtmlElement) -> bool:
        """True when this element holds text and no descendant block element does."""
        if not normalize_whitespace(element_text(element)):
            return False
        for descendant in element.iter():
            if descendant is element:
                continue
            if not isinstance(descendant.tag, str):
                continue
            if descendant.tag.lower() in _TEXT_BLOCKS or descendant.tag.lower() == "table":
                if normalize_whitespace(element_text(descendant)):
                    return False
        return True

    # -- block construction ------------------------------------------------------------

    def _text_block(self, element: HtmlElement, sequence: int) -> ParsedBlock:
        text = element_text(element)
        tag = element.tag.lower()
        weight = self._inherited_weight(element)
        size = font_size_of(element)
        style = SourceStyle(
            font_weight=weight,
            font_size=size,
            text_align=text_align_of(element),
            display=None,
            source_element_id=element.get("id"),
        )

        block_type = "paragraph"
        level: int | None = None
        if tag in _LIST_TAGS:
            block_type = "list_item"
        elif tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            block_type, level = "heading", int(tag[1])
        elif self._looks_like_heading(text, weight, element):
            block_type, level = "heading", 2

        return ParsedBlock(
            block_sequence=sequence,
            block_type=block_type,
            text=text,
            level=level,
            tag_name=tag,
            fragment_sha256=_fragment_hash(element),
            image_count=count_images(element),
            source_style=style,
        )

    def _table_block(self, element: HtmlElement, sequence: int) -> ParsedBlock:
        table = extract_table(
            element,
            wide_table_columns=self._wide,
            layout_max_rows=self._layout_max_rows,
            layout_max_cols=self._layout_max_cols,
        )
        return ParsedBlock(
            block_sequence=sequence,
            block_type="table",
            text=table.plain_text,
            tag_name="table",
            fragment_sha256=_fragment_hash(element),
            table=table,
            image_count=count_images(element),
            source_style=SourceStyle(source_element_id=element.get("id")),
        )

    def _inherited_weight(self, element: HtmlElement) -> int | None:
        """Weight from the element or a single styled descendant wrapping all its text."""
        own = font_weight_of(element)
        if own is not None:
            return own
        children = [c for c in element if isinstance(c.tag, str)]
        if len(children) == 1:
            return self._inherited_weight(children[0])
        return None

    def _looks_like_heading(self, text: str, weight: int | None, element: HtmlElement) -> bool:
        """Style-based heading candidate.

        Candidates inside tables are rejected here: a styled cell in a financial table is
        not a section heading. The normalizer applies the filing-label rules on top.
        """
        if not text or len(text) > self._max_heading_chars:
            return False
        if weight is None or weight < self._min_font_weight:
            return False
        if inside_table(element):
            return False
        return not text.endswith(".")


def _fragment_hash(element: HtmlElement) -> str:
    from lxml import etree

    try:
        raw = etree.tostring(element, encoding="unicode", method="html")
    except Exception:  # noqa: BLE001 - hashing is best-effort provenance
        raw = element.tag if isinstance(element.tag, str) else "?"
    return sha256_text(raw)


def _first_heading(blocks: list[ParsedBlock]) -> str | None:
    for block in blocks:
        if block.block_type == "heading" and block.text:
            return block.text
    for block in blocks:
        if block.text:
            return block.text[:200]
    return None
