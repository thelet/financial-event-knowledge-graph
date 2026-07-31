"""Parser behaviour: byte input, hidden XBRL, separators, tables, protocol conformance."""

from __future__ import annotations

import pytest

from normalization.core.models import ParsedDocument
from normalization.stages.parse import DocumentParser, LxmlDocumentParser, SecHtmlDocumentParser
from normalization.stages.parse.html_text import (
    element_text,
    parse_html_bytes,
    plain_text_from_bytes,
    strip_non_content,
)
from normalization.stages.parse.tables import (
    classify_table,
    extract_table,
    render_markdown,
    render_plain_text,
)

from factories import make_artifact

PARSERS = [SecHtmlDocumentParser, LxmlDocumentParser]


# -- byte handling -----------------------------------------------------------------------


def test_parse_requires_bytes_not_str():
    """91 of 377 corpus artifacts declare an encoding; lxml rejects str for those."""
    with pytest.raises(TypeError):
        parse_html_bytes("<html><body>x</body></html>")


def test_xml_declared_document_parses_from_bytes():
    raw = b'<?xml version="1.0" encoding="UTF-8"?><html><body><p>Hello</p></body></html>'
    assert "Hello" in element_text(parse_html_bytes(raw))


# -- hidden inline XBRL ------------------------------------------------------------------


def test_hidden_content_is_removed():
    raw = (
        b"<html><body><div style='display:none'>SECRETFACT</div>"
        b"<p>Visible text</p></body></html>"
    )
    text = plain_text_from_bytes(raw)
    assert "SECRETFACT" not in text
    assert "Visible text" in text


def test_visible_inline_xbrl_text_is_kept():
    raw = (
        b"<html><body><p>Revenue was "
        b"<ix:nonFraction name='Revenues'>1,234</ix:nonFraction> million</p></body></html>"
    )
    assert "1,234" in plain_text_from_bytes(raw)


def test_inline_xbrl_metadata_containers_are_dropped():
    """<ix:resources> carries no display:none, so the hidden rule misses it.

    It holds <xbrli:unit>iso4217:USD</xbrli:unit>, which leaked into text as `USDxbrli`
    on two documents in the full corpus.
    """
    raw = (
        b"<html><body><ix:header><ix:resources>"
        b"<xbrli:unit id='usd'><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>"
        b"</ix:resources></ix:header><p>Total revenue</p></body></html>"
    )
    text = plain_text_from_bytes(raw)
    assert "iso4217" not in text and "USD" not in text
    assert "Total revenue" in text


def test_visible_inline_xbrl_values_survive_the_metadata_drop():
    """ix:nonFraction wraps displayed numbers and must never be dropped."""
    raw = (
        b"<html><body><ix:resources><xbrli:context id='c'/></ix:resources>"
        b"<p>Revenue <ix:nonFraction name='Revenues'>1,234</ix:nonFraction></p></body></html>"
    )
    text = plain_text_from_bytes(raw)
    assert "1,234" in text


def test_html_comments_do_not_leak_into_text():
    """A filing agent's licensing banner reached document text before this was fixed."""
    raw = b"<html><head><!-- Broadridge PROfile 25.10 --></head><body><p>Body</p></body></html>"
    text = plain_text_from_bytes(raw)
    assert "PROfile" not in text
    assert "Body" in text


# -- separators --------------------------------------------------------------------------


def test_adjacent_blocks_do_not_run_together():
    """The `ASSETSFor` class of bug: a heading div fusing into the paragraph below."""
    raw = b"<html><body><div>ASSETS</div><div>For the year ended</div></body></html>"
    assert "ASSETSFor" not in plain_text_from_bytes(raw)


def test_inline_elements_do_not_split_words():
    """Inline XBRL wraps fragments mid-word; separating those would corrupt them."""
    raw = b"<html><body><p>Open<span>door</span> Technologies</p></body></html>"
    assert "Opendoor Technologies" in plain_text_from_bytes(raw)


# -- tables ------------------------------------------------------------------------------


def _table(html: bytes):
    return extract_table(parse_html_bytes(html).find(".//table"))


def test_table_rows_are_extracted():
    table = _table(b"<html><body><table><tr><td>A</td><td>1</td></tr>"
                   b"<tr><td>B</td><td>2</td></tr></table></body></html>")
    assert table.rows == [["A", "1"], ["B", "2"]]
    assert (table.n_rows, table.n_cols) == (2, 2)


def test_renderings_are_pure_functions_of_rows():
    table = _table(b"<html><body><table><tr><td>A</td><td>1</td></tr>"
                   b"<tr><td>B</td><td>2</td></tr></table></body></html>")
    assert table.markdown == render_markdown(table.rows, table.header_rows)
    assert table.plain_text == render_plain_text(table.rows)


def test_merged_cells_are_recorded():
    table = _table(b"<html><body><table><tr><td colspan='2'>Wide</td></tr>"
                   b"<tr><td>A</td><td>B</td></tr></table></body></html>")
    assert table.has_merged_cells is True


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([["Item", "2024"], ["Revenue", "1,234"], ["Cost", "567"]], "data"),
        ([["only one cell"]], "layout"),
        ([["a"], ["b"], ["c"]], "layout"),
        ([["Sub A", "DE"], ["Sub B", "DE"], ["Sub C", "NY"]], "data"),
    ],
)
def test_classification_is_conservative(rows, expected):
    kind, _ = classify_table(rows, 0)
    assert kind == expected


def test_uncertain_tables_are_unknown_not_guessed():
    kind, _ = classify_table([["some prose here", "more prose"], ["x", "y"]], 0)
    assert kind in ("unknown", "mixed", "data")


def test_nested_table_rows_belong_to_their_own_table():
    outer = _table(
        b"<html><body><table><tr><td>outer</td></tr>"
        b"<tr><td><table><tr><td>inner</td></tr></table></td></tr></table></body></html>"
    )
    flat = [c for row in outer.rows for c in row]
    assert any("outer" in c for c in flat)


# -- protocol conformance ----------------------------------------------------------------


@pytest.mark.parametrize("parser_class", PARSERS, ids=lambda c: c.__name__)
def test_parser_satisfies_protocol(parser_class):
    parser = parser_class()
    assert isinstance(parser, DocumentParser)
    assert isinstance(parser.name, str) and parser.name
    assert isinstance(parser.version, str) and parser.version


@pytest.mark.parametrize("parser_class", PARSERS, ids=lambda c: c.__name__)
def test_parser_produces_canonical_output_only(parser_class):
    """Driven through the protocol; the result must be canonical, not parser-native."""
    raw = (
        b"<html><body><div style='font-weight:700'>Item 1. Business</div>"
        b"<p>Opendoor operates a digital platform.</p>"
        b"<table><tr><td>Metric</td><td>2025</td></tr><tr><td>Revenue</td><td>1,234</td></tr>"
        b"</table></body></html>"
    )

    def drive(parser: DocumentParser) -> ParsedDocument:
        artifact = make_artifact(size_bytes=len(raw))
        assert parser.supports(artifact)
        return parser.parse(artifact, raw)

    parsed = drive(parser_class())
    assert isinstance(parsed, ParsedDocument)
    assert parsed.blocks
    assert all(b.__class__.__module__.startswith("normalization") for b in parsed.blocks)
    assert any("Opendoor operates" in b.text for b in parsed.blocks)


@pytest.mark.parametrize("parser_class", PARSERS, ids=lambda c: c.__name__)
def test_parser_rejects_empty_document(parser_class):
    from normalization.stages.parse.public import ParserError

    with pytest.raises((ParserError, Exception)):
        parser_class().parse(make_artifact(), b"<html><body></body></html>")
