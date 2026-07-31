"""sec-parser implementation of `DocumentParser`.

Used for block segmentation and heading *classification* only. Section hierarchy is derived
by the normalizer instead, which sidesteps sec-parser's biggest limitation — it ships only
`Edgar10QParser`, with no 10-K parser, so its own top-section detection is 10-Q-shaped and
found just 7 top sections in a 10-K.

Its genuine strength is style-based title detection, which nothing else in the Python
ecosystem does for SEC HTML, and which this corpus requires because it contains zero
`<h1>`-`<h4>` elements.

Every sec-parser type dies inside this module.
"""

from __future__ import annotations

import time
import warnings

from ...core.identity import sha256_text
from ...core.models import ParsedBlock, ParsedDocument, SelectedArtifact, SourceStyle
from ...utils.text import normalize_whitespace
from .html_text import (
    count_images,
    element_text,
    font_size_of,
    font_weight_of,
    parse_html_bytes,
    text_align_of,
)
from .public import TRIGGER_EXCEPTION, TRIGGER_NO_BLOCKS, ParserError
from .tables import extract_table

PARSER_NAME = "sec_html"
PARSER_VERSION = "0.58.1"

# sec-parser element class name -> canonical block type. Unmapped names become "other" and
# are counted, so a library upgrade that renames a class is visible rather than silent.
_ELEMENT_MAP = {
    "TitleElement": "heading",
    "TopSectionTitle": "heading",
    "TextElement": "paragraph",
    "SupplementaryText": "paragraph",
    "TableElement": "table",
    "ImageElement": "image",
    "PageHeaderElement": "page_header",
    "PageNumberElement": "page_number",
    "EmptyElement": "other",
    "IrrelevantElement": "other",
    "TableOfContentsElement": "other",
}


class SecHtmlDocumentParser:
    """SEC HTML parser backed by the pinned `sec-parser` release."""

    name = PARSER_NAME
    version = PARSER_VERSION

    def __init__(
        self,
        *,
        max_heading_chars: int = 200,
        wide_table_columns: int = 12,
        layout_max_rows: int = 1,
        layout_max_cols: int = 1,
    ) -> None:
        self._max_heading_chars = max_heading_chars
        self._wide = wide_table_columns
        self._layout_max_rows = layout_max_rows
        self._layout_max_cols = layout_max_cols

    def supports(self, artifact: SelectedArtifact) -> bool:
        return artifact.media_type == "text/html"

    def parse(self, artifact: SelectedArtifact, raw: bytes) -> ParsedDocument:
        started = time.time()
        elements = self._parse_elements(artifact, raw)

        blocks: list[ParsedBlock] = []
        warn: list[str] = []
        unmapped: set[str] = set()

        for element in elements:
            class_name = type(element).__name__
            block_type = _ELEMENT_MAP.get(class_name)
            if block_type is None:
                unmapped.add(class_name)
                block_type = "other"

            text = normalize_whitespace(getattr(element, "text", "") or "")
            if block_type == "table":
                block = self._table_block(element, len(blocks))
            else:
                if not text:
                    continue
                block = self._text_block(element, block_type, text, len(blocks))
            blocks.append(block)

        for index, block in enumerate(blocks):
            block.block_sequence = index

        if not blocks:
            raise ParserError(
                TRIGGER_NO_BLOCKS, f"sec-parser produced no usable blocks for {artifact.artifact_id}"
            )
        if unmapped:
            warn.append(f"unmapped sec-parser element types: {sorted(unmapped)}")

        return ParsedDocument(
            artifact_id=artifact.artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            blocks=blocks,
            title=_first_heading(blocks),
            warnings=warn,
            parse_duration_ms=int((time.time() - started) * 1000),
        )

    # -- sec-parser boundary -----------------------------------------------------------

    def _parse_elements(self, artifact: SelectedArtifact, raw: bytes) -> list:
        """The only place sec-parser is invoked. Its exceptions become ParserError."""
        import sec_parser as sp

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # sec-parser wants str; the bytes/str rule applies to lxml, and decoding
                # here is safe because we control the error handler.
                return sp.Edgar10QParser().parse(raw.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - converted to the canonical failure type
            raise ParserError(TRIGGER_EXCEPTION, f"{type(exc).__name__}: {exc}") from exc

    def _text_block(self, element, block_type: str, text: str, sequence: int) -> ParsedBlock:
        level = getattr(element, "level", None)
        if block_type == "heading":
            level = int(level) + 1 if isinstance(level, int) else 2
            # A "heading" longer than a heading has any business being is prose that
            # happened to be styled boldly.
            if len(text) > self._max_heading_chars:
                block_type, level = "paragraph", None
        else:
            level = None

        html_tag = getattr(element, "html_tag", None)
        source = _source_code(html_tag)
        # Re-extract from the source fragment rather than trusting the library's own text.
        # sec-parser concatenates adjacent elements without separators, which produces
        # run-together tokens such as `INFORMATIONItem` and `BENSONDirector`. Using our own
        # extractor applies the block-boundary rule uniformly across both parsers.
        node = _node_of(source)
        if node is not None:
            extracted = element_text(node)
            if extracted:
                text = extracted
        style = _style_from_node(node)
        return ParsedBlock(
            block_sequence=sequence,
            block_type=block_type,
            text=text,
            level=level,
            tag_name=_tag_name(html_tag),
            fragment_sha256=sha256_text(source or text),
            image_count=count_images(node) if node is not None else 0,
            source_style=style,
        )

    def _table_block(self, element, sequence: int) -> ParsedBlock:
        html_tag = getattr(element, "html_tag", None)
        source = _source_code(html_tag)
        table = None
        if source:
            try:
                root = parse_html_bytes(source.encode("utf-8"))
                found = root.find(".//table")
                node = root if root.tag == "table" else (found if found is not None else root)
                table = extract_table(
                    node,
                    wide_table_columns=self._wide,
                    layout_max_rows=self._layout_max_rows,
                    layout_max_cols=self._layout_max_cols,
                )
            except Exception:  # noqa: BLE001 - fall back to the element's own text
                table = None
        text = table.plain_text if table else normalize_whitespace(
            getattr(element, "text", "") or ""
        )
        return ParsedBlock(
            block_sequence=sequence,
            block_type="table",
            text=text,
            tag_name="table",
            fragment_sha256=sha256_text(source or text),
            table=table,
            source_style=_style_of(html_tag),
        )


# --------------------------------------------------------------------------------------
# sec-parser attribute access, isolated so a library change breaks one place
# --------------------------------------------------------------------------------------


def _source_code(html_tag) -> str | None:
    if html_tag is None:
        return None
    try:
        return html_tag.get_source_code()
    except Exception:  # noqa: BLE001
        return None


def _tag_name(html_tag) -> str | None:
    if html_tag is None:
        return None
    try:
        name = html_tag.name
        return str(name) if name else None
    except Exception:  # noqa: BLE001
        return None


def _node_of(source: str | None):
    """Parse a source fragment once; both text and style are derived from it."""
    if not source:
        return None
    try:
        return parse_html_bytes(source.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _style_from_node(node) -> SourceStyle:
    """The canonical style subset, never a passthrough of parser metrics."""
    if node is None:
        return SourceStyle()
    return SourceStyle(
        font_weight=font_weight_of(node),
        font_size=font_size_of(node),
        text_align=text_align_of(node),
        display=None,
        source_element_id=node.get("id"),
    )


def _style_of(html_tag) -> SourceStyle:
    return _style_from_node(_node_of(_source_code(html_tag)))


def _first_heading(blocks: list[ParsedBlock]) -> str | None:
    for block in blocks:
        if block.block_type == "heading" and block.text:
            return block.text
    for block in blocks:
        if block.text:
            return block.text[:200]
    return None
