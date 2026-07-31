"""Live integration against the real SEC API, using one representative filing.

Marked `live` so the rest of the suite never needs network:

    pytest -m "not live"     # offline
    pytest -m live           # this file only

Uses the Q4 2025 earnings 8-K, which exercises the whole shape in one filing:
primary + EX-99.1/2/3 + XBRL + 18 GRAPHIC assets, plus the full submission and index
header.
"""

from __future__ import annotations

import pytest

from acquisition import identity
from acquisition.catalog import CatalogBuilder
from acquisition.discovery import FilingDiscoverer
from acquisition.download import ArtifactDownloader
from acquisition.resolution import ArtifactResolver
from acquisition.sec_client import SecClient
from acquisition.storage import LocalRawArtifactStore

pytestmark = pytest.mark.live

ACCESSION = "0001801169-26-000009"
CIK = 1801169


@pytest.fixture(scope="module")
def live_client(repo_config):
    with SecClient(repo_config.fetch.http) as client:
        yield client


def test_discovery_against_the_real_api(live_client, repo_config):
    company = repo_config.company()
    filings = FilingDiscoverer(live_client).discover(
        company,
        forms=repo_config.fetch.forms,
        date_from=repo_config.fetch.date_from,
        date_to=repo_config.fetch.effective_date_to(),
    )

    assert len(filings) > 100
    forms = {f.form for f in filings}
    assert {"10-K", "10-Q", "8-K", "DEF 14A"} <= forms

    target = next(f for f in filings if f.accession == ACCESSION)
    assert target.form == "8-K"
    assert target.filing_date == "2026-02-19"
    assert "2.02" in target.items


def test_resolution_against_the_real_api(live_client, repo_config):
    company = repo_config.company()
    filings = FilingDiscoverer(live_client).discover(
        company,
        forms=["8-K"],
        date_from="2026-02-19",
        date_to="2026-02-19",
    )
    target = next(f for f in filings if f.accession == ACCESSION)

    resolver = ArtifactResolver(live_client, repo_config.fetch.include)
    artifacts, anomalies = resolver.resolve_filing(target, company.cik)

    kinds = [a.artifact_kind for a in artifacts]
    assert kinds.count("primary") == 1
    assert kinds.count("exhibit") == 3
    assert kinds.count("asset") == 18
    assert kinds.count("full_submission") == 1
    assert kinds.count("index_header") == 1
    assert not anomalies

    earnings = next(a for a in artifacts if a.role == "ex99-01")
    assert earnings.original_filename == "q42025formxex991earningsre.htm"
    assert earnings.expected_size > 400_000


def test_download_one_filing_end_to_end(live_client, repo_config, tmp_path):
    company = repo_config.company()
    store = LocalRawArtifactStore(tmp_path / "raw", tmp_path / "tmp")

    filings = FilingDiscoverer(live_client).discover(
        company, forms=["8-K"], date_from="2026-02-19", date_to="2026-02-19"
    )
    target = next(f for f in filings if f.accession == ACCESSION)
    artifacts, _ = ArtifactResolver(live_client, repo_config.fetch.include).resolve_filing(
        target, company.cik
    )

    downloader = ArtifactDownloader(live_client, store, company, run_id="live-test")
    outcome = downloader.download_filing(target, artifacts)
    assert outcome.status == "downloaded", outcome.reason
    assert outcome.artifact_count == len(artifacts)

    metadata = store.inspect_finalized_filing(target.filing_dir)
    assert metadata is not None
    assert metadata.items == ["2.02", "7.01", "9.01"]
    assert not store.verify_filing_contents(target.filing_dir, metadata)

    # Bytes are exactly what EDGAR served, under the original filename.
    root = store.filing_dir(target.filing_dir)
    earnings = root / "source" / "q42025formxex991earningsre.htm"
    assert earnings.is_file()
    assert earnings.stat().st_size > 400_000
    assert b"<" in earnings.read_bytes()[:2048]

    # Rerun is a no-op.
    assert downloader.download_filing(target, artifacts).status == "skipped"

    # And the catalog builds from it.
    result = CatalogBuilder(store, tmp_path / "catalog").build()
    assert result.ok
    assert result.filing_count == 1
    assert result.artifact_count == len(artifacts)


def test_index_json_type_field_is_still_an_icon(live_client):
    """Regression guard: if SEC ever fixes this, we can simplify resolution."""
    import json

    payload = json.loads(
        live_client.get_bytes(identity.index_json_url(CIK, ACCESSION)).content
    )
    types = {item["type"] for item in payload["directory"]["item"]}
    assert not any(t.startswith("EX-") for t in types)


def test_hdr_sgml_still_lacks_document_blocks(live_client):
    """Regression guard for v0 plan 13.3."""
    url = (
        f"{identity.filing_base_url(CIK, ACCESSION)}/"
        f"{identity.normalize_accession(ACCESSION)}.hdr.sgml"
    )
    body = live_client.get_bytes(url).content.decode("utf-8", errors="replace")
    assert "<DOCUMENT>" not in body
