"""Regression cover for the latin-1 fallback that corrupted every non-ASCII character.

The original implementation re-encoded an already-decoded `str` with
`source.encode("utf-8")` and handed the bytes to `parse_html_bytes` without saying what
encoding they were in. SEC fragments declare no charset, so lxml fell back to latin-1 (the
HTML default) and read UTF-8 `\\xc2\\xa0` back as `Â` + NBSP. A later whitespace pass folded
the NBSP into a space, leaving `Â` stranded in 7,920 of 14,203 passages.

These tests assert the *absence of corruption*, not the presence of a cleanup step. A test
that stripped `Â` from output would pass against the broken parser too, and would also
delete the character from filings that legitimately contain it.
"""

from __future__ import annotations

import pytest

from normalization.stages.parse import SecHtmlDocumentParser
from normalization.stages.parse.html_text import element_text, parse_html_bytes

from factories import make_artifact

# The two forms an NBSP arrives in, and the punctuation that shares the failure mode.
NBSP_ENTITY = "&#160;"
NBSP_CHAR = " "
PUNCTUATION = "em—dash en–dash “curly” ‘single’ café ±5% 25°"


def parser_text(body: str, *, charset_declared: bool = False) -> str:
    """Drive the *real* parser over a document body and return its block text.

    Deliberately end-to-end. A helper-level test that passes `encoding="utf-8"` itself
    cannot fail when a call site forgets to — reverting the fix and re-running proved that:
    only the tests routed through `SecHtmlDocumentParser` went red.
    """
    head = '<head><meta charset="utf-8"></head>' if charset_declared else ""
    raw = f"<html>{head}<body>{body}</body></html>".encode("utf-8")
    parsed = SecHtmlDocumentParser().parse(make_artifact(), raw)
    return "\n".join(block.text for block in parsed.blocks)


# -- the two NBSP forms ------------------------------------------------------------------


def test_numeric_entity_nbsp_does_not_become_mojibake():
    text = parser_text(f"<p>97,132{NBSP_ENTITY}</p>")
    assert "Â" not in text
    assert "97,132" in text


def test_already_decoded_nbsp_does_not_become_mojibake():
    """The actual defect path: sec-parser resolves the entity before we re-encode."""
    text = parser_text(f"<p>97,132{NBSP_CHAR}</p>")
    assert "Â" not in text
    assert "97,132" in text


def test_both_nbsp_forms_produce_the_same_text():
    assert parser_text(f"<p>13.0{NBSP_ENTITY}</p>") == parser_text(f"<p>13.0{NBSP_CHAR}</p>")


# -- the wider blast radius --------------------------------------------------------------


def test_visible_utf8_punctuation_survives_the_round_trip():
    """NBSP was the visible symptom; every non-ASCII character was corrupted."""
    text = parser_text(f"<p>{PUNCTUATION}</p>")
    for char in ("—", "–", "“", "”", "‘", "’", "é", "±", "°"):
        assert char in text, f"{char!r} did not survive"
    assert "Â" not in text and "â" not in text and "Ã" not in text


@pytest.mark.parametrize("char", ["—", "“", "é", " ", "±", "°", "€", "™"])
def test_no_multibyte_character_produces_a_latin1_artifact(char):
    text = parser_text(f"<p>x{char}y</p>")
    assert "Â" not in text and "Ã" not in text
    assert "â\x80" not in text


# -- charset declaration present or absent ------------------------------------------------


@pytest.mark.parametrize("charset_declared", [True, False])
def test_text_is_identical_whether_or_not_the_document_declares_a_charset(charset_declared):
    """The defect only bit charset-less documents, which is most of the corpus. Both forms
    must now agree, so a filing agent's <meta> tag stops changing the extracted text."""
    text = parser_text(f"<p>café — 97,132{NBSP_ENTITY}</p>", charset_declared=charset_declared)
    assert "café" in text and "—" in text and "97,132" in text
    assert "Â" not in text and "Ã" not in text


# -- the encoding argument itself --------------------------------------------------------


def test_charsetless_bytes_without_the_encoding_argument_still_fall_back():
    """Pins *why* the argument is needed. If lxml ever changes this default, this fails
    and the fix can be simplified rather than silently kept for no reason."""
    raw = "<p>97,132 </p>".encode("utf-8")
    assert "Â" in element_text(parse_html_bytes(raw))
    assert "Â" not in element_text(parse_html_bytes(raw, encoding="utf-8"))


def test_xml_declared_bytes_parse_with_and_without_the_encoding_argument():
    """91 of 377 corpus artifacts open with an XML declaration. Neither call form may
    regress them, and neither may be handed a `str`."""
    raw = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<html><body><p>caf\xc3\xa9 \xe2\x80\x94 dash</p></body></html>"
    )
    for kwargs in ({}, {"encoding": "utf-8"}):
        text = element_text(parse_html_bytes(raw, **kwargs))
        assert "café" in text and "—" in text
        assert "Â" not in text and "Ã" not in text


def test_str_input_is_still_rejected():
    """The bytes-only rule exists for XML-declared documents and must survive the fix."""
    with pytest.raises(TypeError):
        parse_html_bytes("<p>x</p>", encoding="utf-8")


# -- end to end through the real parser --------------------------------------------------


def test_parser_emits_no_mojibake_for_a_charsetless_reconciliation_table():
    """The corpus shape from V1_CLAIM_EXTRACTION §1.3: no charset declaration, NBSP-padded
    numeric cells, an em dash in a label. Driven through the real parser, not a helper."""
    raw = (
        "<html><body>"
        "<p>(in thousands, except&#160;percentages)</p>"
        "<table>"
        "<tr><td>Gross profit (GAAP)</td><td>$</td><td>97,132&#160;</td></tr>"
        "<tr><td>Gross Margin</td><td>13.0&#160;</td><td>%</td></tr>"
        "<tr><td>Inventory impairment &#8212; Current Period</td><td>20&#160;</td></tr>"
        "</table></body></html>"
    ).encode("utf-8")

    parsed = SecHtmlDocumentParser().parse(make_artifact(), raw)
    text = "\n".join(block.text for block in parsed.blocks)

    assert "Â" not in text and "â" not in text
    assert "97,132" in text and "13.0" in text
    assert "—" in text, "the em dash must survive as itself"
    assert "except percentages" in text or "except percentages" in text


def test_parser_preserves_a_source_native_latin1_looking_character():
    """`Â` is only a defect when normalization invents it. A filing that genuinely contains
    the character must keep it — the fix must not become a blanket strip."""
    raw = (
        "<html><body><p>Ârea Metropolitana and Ângelo Gonçalves</p></body></html>"
    ).encode("utf-8")

    parsed = SecHtmlDocumentParser().parse(make_artifact(), raw)
    text = "\n".join(block.text for block in parsed.blocks)

    assert "Ârea" in text
    assert "Ângelo Gonçalves" in text
