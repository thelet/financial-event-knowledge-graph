"""Unit tests for identity, naming, and classification.

Cases use real Opendoor filenames and TYPE values observed on EDGAR.
"""

from __future__ import annotations

import pytest

from acquisition import identity


# -- CIK ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (1801169, "0001801169"),
        ("1801169", "0001801169"),
        ("0001801169", "0001801169"),
        ("CIK0001801169", "0001801169"),
        (320193, "0000320193"),
    ],
)
def test_cik10_pads_and_normalizes(value, expected):
    assert identity.cik10(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "12345678901234"])
def test_cik10_rejects_invalid(value):
    with pytest.raises(ValueError):
        identity.cik10(value)


# -- Accession ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["0001801169-26-000009", "000180116926000009"],
)
def test_normalize_accession_accepts_both_forms(value):
    assert identity.normalize_accession(value) == "0001801169-26-000009"


def test_accession_nodash():
    assert identity.accession_nodash("0001801169-26-000009") == "000180116926000009"


@pytest.mark.parametrize("value", ["", "not-an-accession", "0001801169-26-00009"])
def test_normalize_accession_rejects_invalid(value):
    with pytest.raises(ValueError):
        identity.normalize_accession(value)


def test_accession_prefix_is_not_the_cik():
    """Opendoor's 2020 filings were agent-filed; the prefix is the agent's CIK.

    Guards v0 plan section 13.5: the issuer CIK must come from configuration.
    """
    accession = "0001104659-20-132667"
    assert identity.normalize_accession(accession).startswith("0001104659")
    assert identity.filing_id(1801169, accession) == (
        "sec:0001801169:0001104659-20-132667"
    )


# -- Identities --------------------------------------------------------------------------


def test_filing_id_is_source_cik_accession():
    assert identity.filing_id(1801169, "000180116926000009") == (
        "sec:0001801169:0001801169-26-000009"
    )


def test_artifact_id_keys_on_filename():
    got = identity.artifact_id(1801169, "0001801169-26-000009", "q4.htm")
    assert got == "sec:0001801169:0001801169-26-000009:q4.htm"


def test_artifact_id_is_deterministic():
    args = (1801169, "0001801169-26-000009", "open-20260219.htm")
    assert identity.artifact_id(*args) == identity.artifact_id(*args)


def test_artifact_id_rejects_empty_filename():
    with pytest.raises(ValueError):
        identity.artifact_id(1801169, "0001801169-26-000009", "  ")


# -- Form sanitization -------------------------------------------------------------------


@pytest.mark.parametrize(
    "form,expected",
    [
        ("10-K", "10-K"),
        ("8-K", "8-K"),
        ("DEF 14A", "DEF-14A"),
        ("8-K/A", "8-K-A"),
        ("10-K/A", "10-K-A"),
        ("SC 13G/A", "SC-13G-A"),
        ("  10-q  ", "10-Q"),
        ("S-1/A", "S-1-A"),
    ],
)
def test_sanitize_form(form, expected):
    assert identity.sanitize_form(form) == expected


def test_sanitize_form_never_yields_a_path_separator():
    for form in ("8-K/A", "SC 13G/A", "N-1A/A", "10-K\\X"):
        assert "/" not in identity.sanitize_form(form)
        assert "\\" not in identity.sanitize_form(form)


@pytest.mark.parametrize("form", ["", "   ", "///"])
def test_sanitize_form_rejects_empty(form):
    with pytest.raises(ValueError):
        identity.sanitize_form(form)


@pytest.mark.parametrize(
    "form,expected", [("8-K/A", True), ("10-K/A", True), ("8-K", False), ("DEF 14A", False)]
)
def test_is_amendment(form, expected):
    assert identity.is_amendment(form) is expected


# -- Paths -------------------------------------------------------------------------------


def test_filing_dir_name_is_date_first():
    assert identity.filing_dir_name("2026-02-19", "0001801169-26-000009") == (
        "2026-02-19_0001801169-26-000009"
    )


def test_filing_dir_names_sort_chronologically():
    names = [
        identity.filing_dir_name(date, acc)
        for date, acc in [
            ("2026-02-19", "0001801169-26-000009"),
            ("2020-12-07", "0001104659-20-132667"),
            ("2025-11-06", "0001801169-25-000090"),
        ]
    ]
    assert sorted(names) == [
        "2020-12-07_0001104659-20-132667",
        "2025-11-06_0001801169-25-000090",
        "2026-02-19_0001801169-26-000009",
    ]


def test_filing_relpath():
    got = identity.filing_relpath(1801169, "DEF 14A", "2026-04-30", "0001801169-26-000020")
    assert str(got) == "sec/0001801169/DEF-14A/2026-04-30_0001801169-26-000020"


@pytest.mark.parametrize("date", ["2026/02/19", "20260219", "", "feb 19"])
def test_filing_dir_name_rejects_bad_dates(date):
    with pytest.raises(ValueError):
        identity.filing_dir_name(date, "0001801169-26-000009")


# -- Exhibit roles -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sgml_type,expected",
    [
        ("EX-99.1", "ex99-01"),
        ("EX-99.2", "ex99-02"),
        ("EX-99.3", "ex99-03"),
        ("EX-10.38", "ex10-38"),
        ("EX-4.7", "ex4-07"),
        ("EX-21.1", "ex21-01"),
        ("EX-31.1", "ex31-01"),
        ("EX-101.SCH", "ex101-sch"),
        ("EX-101.PRE", "ex101-pre"),
        ("EX-99", "ex99"),
        ("ex-99.1", "ex99-01"),
        ("EX-10.11", "ex10-11"),
    ],
)
def test_normalize_exhibit_role(sgml_type, expected):
    assert identity.normalize_exhibit_role(sgml_type) == expected


@pytest.mark.parametrize("sgml_type", ["8-K", "10-K", "GRAPHIC", "XML", "", "DEF 14A"])
def test_normalize_exhibit_role_returns_none_for_non_exhibits(sgml_type):
    assert identity.normalize_exhibit_role(sgml_type) is None


def test_exhibit_role_zero_padding_makes_lexical_order_numeric():
    roles = [identity.normalize_exhibit_role(f"EX-99.{n}") for n in (1, 2, 10, 11)]
    assert roles == sorted(roles)


# -- Classification ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sgml_type,filename,sequence,form,expected_kind,expected_role",
    [
        ("8-K", "open-20260219.htm", 1, "8-K", identity.KIND_PRIMARY, "primary"),
        ("10-K", "open-20251231.htm", 1, "10-K", identity.KIND_PRIMARY, "primary"),
        ("EX-99.1", "q4.htm", 2, "8-K", identity.KIND_EXHIBIT, "ex99-01"),
        ("EX-21.1", "subs.htm", 5, "10-K", identity.KIND_EXHIBIT, "ex21-01"),
        ("GRAPHIC", "chart001.jpg", 9, "8-K", identity.KIND_ASSET, None),
        ("EX-101.SCH", "open.xsd", 5, "8-K", identity.KIND_XBRL, "ex101-sch"),
        ("EX-101.LAB", "open_lab.xml", 7, "8-K", identity.KIND_XBRL, "ex101-lab"),
        ("EX-101.CAL", "open_cal.xml", 8, "8-K", identity.KIND_XBRL, "ex101-cal"),
    ],
)
def test_classify_artifact(
    sgml_type, filename, sequence, form, expected_kind, expected_role
):
    kind, role, _ = identity.classify_artifact(sgml_type, filename, sequence, form)
    assert (kind, role) == (expected_kind, expected_role)


@pytest.mark.parametrize(
    "sgml_type,filename,description",
    [
        ("XML", "R1.htm", "IDEA: XBRL DOCUMENT"),
        ("XML", "Show.js", "IDEA: XBRL DOCUMENT"),
        ("XML", "report.css", "IDEA: XBRL DOCUMENT"),
        ("XML", "FilingSummary.xml", "IDEA: XBRL DOCUMENT"),
        ("JSON", "MetaLinks.json", "IDEA: XBRL DOCUMENT"),
        ("ZIP", "0001801169-26-000009-xbrl.zip", "IDEA: XBRL DOCUMENT"),
        ("XML", "open-20260219_htm.xml", "IDEA: XBRL DOCUMENT"),
    ],
)
def test_idea_generated_files_are_render_artifacts(sgml_type, filename, description):
    """SEC rendering output is derived from the filing, not part of it."""
    kind, role, low = identity.classify_artifact(
        sgml_type, filename, 30, "8-K", description
    )
    assert kind == identity.KIND_RENDER_ARTIFACT
    assert role is None
    assert low is True


def test_render_artifacts_detected_by_filename_without_description():
    """Fallback for filings that omit DESCRIPTION."""
    for filename in ("R7.htm", "Show.js", "MetaLinks.json", "x-xbrl.zip", "a_htm.xml"):
        assert identity.is_render_artifact(filename, None) is True


def test_filer_submitted_xbrl_is_not_a_render_artifact():
    """EX-101.* linkbases are filed by the issuer and must be kept."""
    for sgml_type, filename in [
        ("EX-101.SCH", "open-20260219.xsd"),
        ("EX-101.LAB", "open-20260219_lab.xml"),
        ("EX-101.PRE", "open-20260219_pre.xml"),
        ("EX-101.DEF", "open-20260219_def.xml"),
    ]:
        kind, _, _ = identity.classify_artifact(
            sgml_type, filename, 6, "8-K", "XBRL TAXONOMY EXTENSION DOCUMENT"
        )
        assert kind == identity.KIND_XBRL


def test_classify_uses_type_not_description():
    """DESCRIPTION differs across years for the same exhibit (v0 plan 13.4)."""
    modern = identity.classify_artifact("EX-99.1", "a.htm", 2, "8-K")
    legacy = identity.classify_artifact("EX-99.1", "tm_ex99-1.htm", 2, "8-K")
    assert modern == legacy


def test_certifications_are_low_priority():
    for sgml_type in ("EX-31.1", "EX-31.2", "EX-32.1"):
        _, _, low = identity.classify_artifact(sgml_type, "cert.htm", 3, "10-K")
        assert low is True


def test_substantive_exhibits_are_not_low_priority():
    for sgml_type in ("EX-99.1", "EX-21.1", "EX-10.38"):
        _, _, low = identity.classify_artifact(sgml_type, "x.htm", 3, "10-K")
        assert low is False


def test_unknown_type_is_other_not_silently_dropped():
    kind, role, _ = identity.classify_artifact("COVER", "cover.htm", 4, "10-K")
    assert kind == identity.KIND_OTHER
    assert role is None


def test_primary_falls_back_to_sequence_one():
    kind, _, _ = identity.classify_artifact("FORM 8-K", "x.htm", 1, "8-K")
    assert kind == identity.KIND_PRIMARY


# -- Media types -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("a.htm", "text/html"),
        ("a.HTM", "text/html"),
        ("a.jpg", "image/jpeg"),
        ("a.xml", "application/xml"),
        ("a.xsd", "application/xml"),
        ("a.txt", "text/plain"),
        ("a.zip", "application/zip"),
        ("a.unknown", "application/octet-stream"),
    ],
)
def test_media_type_for(filename, expected):
    assert identity.media_type_for(filename) == expected


def test_file_extension_is_lowercased():
    assert identity.file_extension("EXHIBIT992-Q4.HTM") == ".htm"


# -- URLs --------------------------------------------------------------------------------


def test_urls_use_unpadded_cik_in_archive_path():
    url = identity.artifact_url(1801169, "0001801169-26-000009", "open-20260219.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1801169/000180116926000009/"
        "open-20260219.htm"
    )


def test_submissions_url_uses_padded_cik():
    assert identity.submissions_url(1801169) == (
        "https://data.sec.gov/submissions/CIK0001801169.json"
    )


def test_derived_filenames():
    assert identity.index_headers_filename("0001801169-26-000009") == (
        "0001801169-26-000009-index-headers.html"
    )
    assert identity.full_submission_filename("0001801169-26-000009") == (
        "0001801169-26-000009.txt"
    )
