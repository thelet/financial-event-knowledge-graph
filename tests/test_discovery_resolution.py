"""DISCOVER and RESOLVE, driven by saved SEC fixtures. No network."""

from __future__ import annotations

import json

import pytest

from acquisition.core import identity
from acquisition.core.config import IncludeConfig
from acquisition.discovery import FilingDiscoverer, summarize
from acquisition.resolution import (
    ANOMALY_CASE_COLLISION,
    ANOMALY_UNRECOGNIZED_TYPE,
    ArtifactResolver,
)
from acquisition.sgml import parse_index_headers

from conftest import FakeSecClient, make_filing_record

CIK = 1801169


# -- DISCOVER ----------------------------------------------------------------------------


@pytest.fixture
def discovery_client(fixtures_dir):
    payload = (fixtures_dir / "submissions_opendoor_slice.json").read_bytes()
    return FakeSecClient({identity.submissions_url(CIK): payload})


def _discover(client, company, **kwargs):
    options = {
        "forms": ["10-K", "10-Q", "8-K", "DEF 14A"],
        "date_from": "2020-01-01",
        "date_to": "2026-12-31",
    }
    options.update(kwargs)
    return FilingDiscoverer(client).discover(company, **options)


def test_discovery_filters_to_configured_forms(discovery_client, company):
    records = _discover(discovery_client, company)
    forms = {r.form for r in records}
    assert "4" not in forms  # ownership forms are out of scope
    assert "SC 13G" not in forms
    assert forms <= {"10-K", "10-Q", "8-K", "DEF 14A", "8-K/A"}


def test_discovery_includes_amendments_by_default(discovery_client, company):
    records = _discover(discovery_client, company)
    assert any(r.is_amendment for r in records)
    assert any(r.form == "8-K/A" for r in records)


def test_discovery_can_exclude_amendments(discovery_client, company):
    records = _discover(discovery_client, company, include_amendments=False)
    assert not any(r.is_amendment for r in records)


def test_discovery_respects_date_bounds(discovery_client, company):
    records = _discover(discovery_client, company, date_from="2025-01-01")
    assert records
    assert all(r.filing_date >= "2025-01-01" for r in records)


def test_discovery_is_chronologically_ordered(discovery_client, company):
    records = _discover(discovery_client, company)
    dates = [(r.filing_date, r.accession) for r in records]
    assert dates == sorted(dates)


def test_discovery_preserves_item_codes(discovery_client, company):
    records = _discover(discovery_client, company)
    with_items = [r for r in records if r.items]
    assert with_items
    assert all(isinstance(r.items, list) for r in with_items)
    assert all("," not in item for r in with_items for item in r.items)


def test_discovery_populates_identity_and_paths(discovery_client, company):
    record = _discover(discovery_client, company)[0]
    assert record.filing_id == identity.filing_id(CIK, record.accession)
    assert record.cik10 == "0001801169"
    assert record.filing_dir.startswith("sec/0001801169/")
    assert record.form_sanitized in record.filing_dir
    assert "/" not in record.form_sanitized


def test_discovery_amends_accession_is_null(discovery_client, company):
    """Not reliably determinable from the submissions API (v0 plan section 11)."""
    records = _discover(discovery_client, company)
    assert all(r.amends_accession is None for r in records)


def test_discovery_captures_xbrl_flags_and_urls(discovery_client, company):
    record = _discover(discovery_client, company)[0]
    assert record.source_index_url.endswith("/index.json")
    assert record.source_index_headers_url.endswith("-index-headers.html")
    assert isinstance(record.is_xbrl, bool)


def test_discovery_makes_one_request(discovery_client, company):
    _discover(discovery_client, company)
    assert discovery_client.request_count == 1


def test_discovery_follows_continuation_files(fixtures_dir, company):
    """Issuers with >1000 filings split older ones into continuation files."""
    base = json.loads((fixtures_dir / "submissions_opendoor_slice.json").read_text())
    extra_rows = {
        "form": ["10-K"],
        "accessionNumber": ["0001801169-19-000001"],
        "filingDate": ["2019-03-01"],
        "reportDate": ["2018-12-31"],
        "items": [""],
        "primaryDocument": ["old.htm"],
        "size": [1234],
        "isXBRL": [1],
        "isInlineXBRL": [0],
        "acceptanceDateTime": ["2019-03-01T16:00:00.000Z"],
        "act": ["34"],
        "fileNumber": ["001-39253"],
        "filmNumber": ["19000001"],
        "primaryDocDescription": ["10-K"],
        "core_type": ["10-K"],
    }
    base["filings"]["files"] = [{"name": "CIK0001801169-submissions-001.json"}]
    client = FakeSecClient(
        {
            identity.submissions_url(CIK): json.dumps(base).encode(),
            "https://data.sec.gov/submissions/CIK0001801169-submissions-001.json": json.dumps(
                {"form": extra_rows["form"], **extra_rows}
            ).encode(),
        }
    )
    records = _discover(client, company, date_from="2019-01-01")
    assert any(r.accession == "0001801169-19-000001" for r in records)
    assert client.request_count == 2


def test_summarize_renders_counts(discovery_client, company):
    text = summarize(_discover(discovery_client, company))
    assert "TOTAL" in text
    assert "Amendments:" in text


def test_summarize_handles_empty():
    assert "No filings" in summarize([])


# -- RESOLVE -----------------------------------------------------------------------------


@pytest.fixture
def resolve_client(fixtures_dir):
    accession = "0001801169-26-000009"
    return FakeSecClient(
        {
            identity.artifact_url(
                CIK, accession, identity.index_headers_filename(accession)
            ): (fixtures_dir / "headers_8k_2026.html").read_bytes(),
            identity.index_json_url(CIK, accession): (
                fixtures_dir / "index_8k_2026.json"
            ).read_bytes(),
        }
    )


@pytest.fixture
def resolver(resolve_client):
    return ArtifactResolver(resolve_client, IncludeConfig())


def test_resolve_produces_the_expected_artifact_set(resolver):
    artifacts, anomalies = resolver.resolve_filing(make_filing_record(), CIK)
    kinds = {}
    for artifact in artifacts:
        kinds.setdefault(artifact.artifact_kind, []).append(artifact)

    assert len(kinds["primary"]) == 1
    assert len(kinds["exhibit"]) == 3  # EX-99.1, 99.2, 99.3
    assert len(kinds["xbrl"]) == 4  # EX-101.SCH/DEF/LAB/PRE
    assert len(kinds["asset"]) == 18  # GRAPHIC
    assert len(kinds["full_submission"]) == 1
    assert len(kinds["index_header"]) == 1
    assert len(artifacts) == 28
    assert not anomalies


def test_resolve_excludes_sec_render_artifacts(resolver):
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    names = {a.original_filename for a in artifacts}
    assert "R1.htm" not in names
    assert "FilingSummary.xml" not in names
    assert "MetaLinks.json" not in names
    assert "Show.js" not in names
    assert not any(n.endswith("-xbrl.zip") for n in names)
    assert not any(n.endswith("_htm.xml") for n in names)


def test_resolve_can_include_render_artifacts(resolve_client):
    resolver = ArtifactResolver(
        resolve_client, IncludeConfig(render_artifacts=True)
    )
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    kinds = [a.artifact_kind for a in artifacts]
    assert kinds.count("render_artifact") == 7
    assert len(artifacts) == 35


def test_resolve_can_exclude_assets(resolve_client):
    resolver = ArtifactResolver(resolve_client, IncludeConfig(assets=False))
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    assert not any(a.artifact_kind == "asset" for a in artifacts)


def test_resolve_can_exclude_full_submission(resolve_client):
    resolver = ArtifactResolver(resolve_client, IncludeConfig(full_submission=False))
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    assert not any(a.artifact_kind == "full_submission" for a in artifacts)


def test_resolve_assigns_exhibit_roles(resolver):
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    roles = {a.role for a in artifacts if a.artifact_kind == "exhibit"}
    assert roles == {"ex99-01", "ex99-02", "ex99-03"}


def test_resolve_preserves_original_filenames_and_urls(resolver):
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    earnings = next(a for a in artifacts if a.role == "ex99-01")
    assert earnings.original_filename == "q42025formxex991earningsre.htm"
    assert earnings.stored_path == "source/q42025formxex991earningsre.htm"
    assert earnings.source_url.endswith("/q42025formxex991earningsre.htm")
    assert "000180116926000009" in earnings.source_url


def test_resolve_records_expected_sizes_from_index_json(resolver):
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    earnings = next(a for a in artifacts if a.role == "ex99-01")
    assert earnings.expected_size and earnings.expected_size > 100_000


def test_resolve_artifact_ids_are_unique(resolver):
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    ids = [a.artifact_id for a in artifacts]
    assert len(ids) == len(set(ids))


def test_resolve_flags_low_priority_correctly(resolver):
    artifacts, _ = resolver.resolve_filing(make_filing_record(), CIK)
    earnings = next(a for a in artifacts if a.role == "ex99-01")
    full = next(a for a in artifacts if a.artifact_kind == "full_submission")
    assert earnings.low_processing_priority is False
    assert full.low_processing_priority is True


def test_resolve_makes_two_requests_per_filing(resolve_client, resolver):
    resolver.resolve_filing(make_filing_record(), CIK)
    assert resolve_client.request_count == 2


def test_resolve_handles_2020_agent_filed_header(fixtures_dir):
    accession = "0001104659-20-132667"
    client = FakeSecClient(
        {
            identity.artifact_url(
                CIK, accession, identity.index_headers_filename(accession)
            ): (fixtures_dir / "headers_8k_2020.html").read_bytes(),
            identity.index_json_url(CIK, accession): b'{"directory":{"item":[]}}',
        }
    )
    filing = make_filing_record(accession=accession, filing_date="2020-12-07")
    artifacts, anomalies = ArtifactResolver(client, IncludeConfig()).resolve_filing(
        filing, CIK
    )
    kinds = [a.artifact_kind for a in artifacts]
    assert kinds.count("primary") == 1
    assert kinds.count("exhibit") == 1
    assert not [a for a in anomalies if a.kind == ANOMALY_UNRECOGNIZED_TYPE]


def test_resolve_10k_has_many_exhibits(fixtures_dir):
    accession = "0001801169-26-000010"
    client = FakeSecClient(
        {
            identity.artifact_url(
                CIK, accession, identity.index_headers_filename(accession)
            ): (fixtures_dir / "headers_10k_2026.html").read_bytes(),
            identity.index_json_url(CIK, accession): b'{"directory":{"item":[]}}',
        }
    )
    filing = make_filing_record(
        accession=accession, form="10-K", filing_date="2026-02-19", items=[]
    )
    artifacts, _ = ArtifactResolver(client, IncludeConfig()).resolve_filing(filing, CIK)
    roles = {a.role for a in artifacts if a.artifact_kind == "exhibit"}
    assert "ex21-01" in roles  # subsidiaries
    assert "ex23-01" in roles  # auditor consent
    assert {"ex31-01", "ex31-02", "ex32-01"} <= roles


def test_resolve_marks_certifications_low_priority(fixtures_dir):
    accession = "0001801169-26-000010"
    client = FakeSecClient(
        {
            identity.artifact_url(
                CIK, accession, identity.index_headers_filename(accession)
            ): (fixtures_dir / "headers_10k_2026.html").read_bytes(),
            identity.index_json_url(CIK, accession): b'{"directory":{"item":[]}}',
        }
    )
    filing = make_filing_record(accession=accession, form="10-K", items=[])
    artifacts, _ = ArtifactResolver(client, IncludeConfig()).resolve_filing(filing, CIK)
    certs = [a for a in artifacts if a.role in {"ex31-01", "ex31-02", "ex32-01"}]
    assert certs and all(a.low_processing_priority for a in certs)


def test_resolve_reports_unrecognized_types():
    raw = (
        "<html><PRE>&lt;DOCUMENT&gt;\n&lt;TYPE&gt;8-K\n&lt;SEQUENCE&gt;1\n"
        "&lt;FILENAME&gt;main.htm\n&lt;TEXT&gt;\n"
        "&lt;DOCUMENT&gt;\n&lt;TYPE&gt;COVER\n&lt;SEQUENCE&gt;2\n"
        "&lt;FILENAME&gt;cover.htm\n&lt;TEXT&gt;\n</PRE></html>"
    ).encode()
    accession = "0001801169-26-000009"
    client = FakeSecClient(
        {
            identity.artifact_url(
                CIK, accession, identity.index_headers_filename(accession)
            ): raw,
            identity.index_json_url(CIK, accession): b'{"directory":{"item":[]}}',
        }
    )
    artifacts, anomalies = ArtifactResolver(client, IncludeConfig()).resolve_filing(
        make_filing_record(), CIK
    )
    unrecognized = [a for a in anomalies if a.kind == ANOMALY_UNRECOGNIZED_TYPE]
    assert len(unrecognized) == 1
    # Reported, but still acquired -- never silently dropped.
    assert any(a.original_filename == "cover.htm" for a in artifacts)


def test_resolve_detects_case_insensitive_collisions():
    raw = (
        "<html><PRE>&lt;DOCUMENT&gt;\n&lt;TYPE&gt;8-K\n&lt;SEQUENCE&gt;1\n"
        "&lt;FILENAME&gt;Main.htm\n&lt;TEXT&gt;\n"
        "&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n"
        "&lt;FILENAME&gt;MAIN.HTM\n&lt;TEXT&gt;\n</PRE></html>"
    ).encode()
    accession = "0001801169-26-000009"
    client = FakeSecClient(
        {
            identity.artifact_url(
                CIK, accession, identity.index_headers_filename(accession)
            ): raw,
            identity.index_json_url(CIK, accession): b'{"directory":{"item":[]}}',
        }
    )
    _, anomalies = ArtifactResolver(client, IncludeConfig()).resolve_filing(
        make_filing_record(), CIK
    )
    assert any(a.kind == ANOMALY_CASE_COLLISION for a in anomalies)


def test_resolve_all_covers_every_filing(resolve_client, fixtures_dir):
    """resolve_all must not lose or duplicate filings under concurrency."""
    filing = make_filing_record()
    resolver = ArtifactResolver(resolve_client, IncludeConfig())
    artifacts, _ = resolver.resolve_all([filing, filing, filing], CIK, max_workers=3)
    assert len(artifacts) == 28 * 3


def test_index_json_type_field_is_an_icon_not_an_exhibit_type(fixtures_dir):
    """Guards v0 plan section 13.1 -- the reason we parse the SGML header at all."""
    payload = json.loads((fixtures_dir / "index_8k_2026.json").read_text())
    types = {item["type"] for item in payload["directory"]["item"]}
    # Every value is an icon filename; none is an exhibit type.
    assert all(t == "" or t.endswith(".gif") for t in types)
    assert not any(t.startswith("EX-") for t in types)
    assert "text.gif" in types


def test_header_and_index_agree_on_filer_documents(fixtures_dir):
    header = parse_index_headers((fixtures_dir / "headers_8k_2026.html").read_text())
    index = json.loads((fixtures_dir / "index_8k_2026.json").read_text())
    index_names = {item["name"] for item in index["directory"]["item"]}
    for document in header.documents:
        assert document.filename in index_names
