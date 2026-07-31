"""SGML header parsing, against saved real EDGAR headers."""

from __future__ import annotations

import pytest

from acquisition.stages.resolve.sgml import SgmlParseError, parse_index_headers


@pytest.fixture
def header_8k_2026(fixtures_dir):
    return (fixtures_dir / "headers_8k_2026.html").read_text(encoding="utf-8")


@pytest.fixture
def header_8k_2020(fixtures_dir):
    return (fixtures_dir / "headers_8k_2020.html").read_text(encoding="utf-8")


@pytest.fixture
def header_10k_2026(fixtures_dir):
    return (fixtures_dir / "headers_10k_2026.html").read_text(encoding="utf-8")


def test_parses_modern_earnings_8k(header_8k_2026):
    parsed = parse_index_headers(header_8k_2026)
    by_type = {d.type: d for d in parsed.documents}

    assert by_type["8-K"].filename == "open-20260219.htm"
    assert by_type["8-K"].sequence == 1
    assert by_type["EX-99.1"].filename == "q42025formxex991earningsre.htm"
    assert by_type["EX-99.1"].sequence == 2
    assert by_type["EX-99.2"].filename == "exhibit992-q42025form8xk.htm"
    assert by_type["EX-99.3"].filename == "exhibit993-4q25opendoors.htm"


def test_earnings_8k_carries_the_expected_shape(header_8k_2026):
    """Verified structure: primary + 3 EX-99.x + XBRL + 18 GRAPHIC."""
    parsed = parse_index_headers(header_8k_2026)
    graphics = [d for d in parsed.documents if d.type == "GRAPHIC"]
    exhibits = [d for d in parsed.documents if d.type.startswith("EX-99.")]

    assert len(exhibits) == 3
    assert len(graphics) == 18
    assert all(d.filename.lower().endswith(".jpg") for d in graphics)


def test_acceptance_datetime_is_recovered(header_8k_2026):
    parsed = parse_index_headers(header_8k_2026)
    assert parsed.acceptance_datetime == "20260219162253"


def test_item_information_is_recovered(header_8k_2026):
    parsed = parse_index_headers(header_8k_2026)
    assert "Results of Operations and Financial Condition" in parsed.item_descriptions
    assert len(parsed.item_descriptions) == 3


def test_parses_2020_agent_filed_header(header_8k_2020):
    """Different filing agent, older filename and DESCRIPTION conventions."""
    parsed = parse_index_headers(header_8k_2020)
    by_type = {d.type: d for d in parsed.documents}

    assert by_type["8-K"].filename == "tm2037821d1_8k.htm"
    assert by_type["EX-99.1"].filename == "tm2037821d1_ex99-1.htm"


def test_description_style_differs_between_eras(header_8k_2026, header_8k_2020):
    """The reason classification must key on TYPE, not DESCRIPTION (v0 plan 13.4)."""
    modern = {d.type: d.description for d in parse_index_headers(header_8k_2026).documents}
    legacy = {d.type: d.description for d in parse_index_headers(header_8k_2020).documents}

    assert modern["EX-99.1"] == "EX-99.1"
    assert legacy["EX-99.1"] == "EXHIBIT 99.1"
    assert modern["8-K"] != legacy["8-K"]


def test_parses_10k_with_many_exhibit_types(header_10k_2026):
    parsed = parse_index_headers(header_10k_2026)
    types = {d.type for d in parsed.documents}

    assert "10-K" in types
    assert "EX-21.1" in types  # subsidiaries
    assert "EX-23.1" in types  # auditor consent
    assert {"EX-31.1", "EX-31.2", "EX-32.1"} <= types  # certifications
    assert any(t.startswith("EX-101.") for t in types)  # XBRL


def test_every_document_has_a_filename(header_10k_2026):
    parsed = parse_index_headers(header_10k_2026)
    assert all(d.filename.strip() for d in parsed.documents)


def test_filenames_are_unique_within_a_filing(header_10k_2026, header_8k_2026):
    for raw in (header_10k_2026, header_8k_2026):
        names = [d.filename for d in parse_index_headers(raw).documents]
        assert len(names) == len(set(names))


def test_sequences_are_unique_and_start_at_one(header_8k_2026):
    parsed = parse_index_headers(header_8k_2026)
    sequences = [d.sequence for d in parsed.documents if d.sequence is not None]
    assert min(sequences) == 1
    assert len(sequences) == len(set(sequences))


def test_render_artifacts_are_present_and_marked(header_8k_2026):
    """SEC's IDEA system appends rendering output to the DOCUMENT list.

    Corrects the original v0 plan 13.7 assumption that the header contained only
    filer-submitted documents. They are identifiable by their DESCRIPTION.
    """
    documents = parse_index_headers(header_8k_2026).documents
    names = {d.filename for d in documents}
    assert "FilingSummary.xml" in names
    assert "MetaLinks.json" in names
    assert "R1.htm" in names

    generated = [d for d in documents if (d.description or "").startswith("IDEA:")]
    assert {d.filename for d in generated} == {
        "R1.htm",
        "Show.js",
        "report.css",
        "FilingSummary.xml",
        "MetaLinks.json",
        "0001801169-26-000009-xbrl.zip",
        "open-20260219_htm.xml",
    }


def test_filer_submitted_documents_are_separable_from_generated(header_8k_2026):
    documents = parse_index_headers(header_8k_2026).documents
    filer = [d for d in documents if not (d.description or "").startswith("IDEA:")]
    assert len(documents) == 33
    assert len(filer) == 26  # primary + 3 EX-99.x + 4 EX-101.x + 18 GRAPHIC


def test_sequence_numbers_may_have_gaps(header_8k_2026):
    """EDGAR does not guarantee contiguous SEQUENCE values; do not assume a range."""
    sequences = sorted(
        d.sequence for d in parse_index_headers(header_8k_2026).documents if d.sequence
    )
    assert sequences[-1] > len(sequences)


def test_rejects_input_without_document_blocks():
    with pytest.raises(SgmlParseError):
        parse_index_headers("<html><body><pre>no documents here</pre></body></html>")


def test_rejects_empty_input():
    with pytest.raises(SgmlParseError):
        parse_index_headers("")


def test_handles_missing_description():
    raw = (
        "<html><body><PRE>&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n"
        "&lt;SEQUENCE&gt;2\n&lt;FILENAME&gt;x.htm\n&lt;TEXT&gt;\n</PRE></body></html>"
    )
    parsed = parse_index_headers(raw)
    assert parsed.documents[0].description is None
    assert parsed.documents[0].filename == "x.htm"


def test_handles_non_numeric_sequence():
    raw = (
        "<html><PRE>&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n"
        "&lt;SEQUENCE&gt;none\n&lt;FILENAME&gt;x.htm\n&lt;TEXT&gt;\n</PRE></html>"
    )
    assert parse_index_headers(raw).documents[0].sequence is None


def test_document_body_after_text_is_not_parsed_as_fields():
    """Tag-like lines inside the document body must not leak into field parsing."""
    raw = (
        "<html><PRE>&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n"
        "&lt;FILENAME&gt;real.htm\n&lt;TEXT&gt;\n&lt;FILENAME&gt;decoy.htm\n</PRE></html>"
    )
    assert parse_index_headers(raw).documents[0].filename == "real.htm"
