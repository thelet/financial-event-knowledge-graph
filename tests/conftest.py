"""Shared test fixtures and doubles.

No test outside tests/test_live.py touches the network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from acquisition.core import identity
from acquisition.core.config import AppConfig, CompanyConfig, FetchConfig, HttpConfig, load_config
from acquisition.core.models import (
    ArtifactRecord,
    FilingMetadata,
    FilingRecord,
    StoredArtifact,
)
from acquisition.core.sec_client import DownloadOutcome, FetchResult

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def repo_config() -> AppConfig:
    """The real repository configuration. Treat as read-only; deep-copy before mutating."""
    return load_config(REPO_ROOT)


class FakeClock:
    """Deterministic, instant clock. Records what would have been slept."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


class FakeSecClient:
    """Serves canned bytes by URL. Substitutes for SecClient in stage tests."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.requested: list[str] = []
        self.request_count = 0

    def get_bytes(self, url: str) -> FetchResult:
        self.requested.append(url)
        self.request_count += 1
        if url not in self.responses:
            raise AssertionError(f"FakeSecClient has no canned response for {url}")
        return FetchResult(url=url, status=200, content=self.responses[url])

    def download_to(self, url: str, destination: Path) -> DownloadOutcome:
        self.requested.append(url)
        self.request_count += 1
        payload = self.responses.get(url, b"canned-" + url.rsplit("/", 1)[-1].encode())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return DownloadOutcome(
            url=url,
            status=200,
            path=destination,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            etag='"fake-etag"',
            last_modified="Thu, 19 Feb 2026 16:22:53 GMT",
        )


@pytest.fixture
def company() -> CompanyConfig:
    return CompanyConfig(
        cik=1801169,
        name="Opendoor Technologies Inc.",
        tickers=["OPEN", "OPENL"],
        wave="W0",
    )


@pytest.fixture
def http_config() -> HttpConfig:
    return HttpConfig(
        user_agent="test-agent test@example.com",
        requests_per_second=1000.0,
        max_concurrency=2,
        max_retries=3,
        backoff_base_seconds=1.0,
        backoff_max_seconds=8.0,
    )


@pytest.fixture
def temp_config(tmp_path: Path, http_config: HttpConfig, company: CompanyConfig) -> AppConfig:
    """An AppConfig rooted in a temporary directory."""
    raw_fetch = {
        "source": "sec",
        "forms": ["10-K", "8-K"],
        "date_from": "2020-01-01",
        "date_to": "2026-12-31",
        "http": http_config.model_dump(),
    }
    raw_companies = {"companies": [company.model_dump()]}
    return AppConfig(
        companies=[company],
        fetch=FetchConfig(**raw_fetch),
        root=tmp_path,
        raw_companies=raw_companies,
        raw_fetch=raw_fetch,
    )


def make_filing_record(
    *,
    accession: str = "0001801169-26-000009",
    form: str = "8-K",
    filing_date: str = "2026-02-19",
    items: list[str] | None = None,
    cik: int = 1801169,
) -> FilingRecord:
    return FilingRecord(
        filing_id=identity.filing_id(cik, accession),
        cik10=identity.cik10(cik),
        company_name="Opendoor Technologies Inc.",
        tickers=["OPEN"],
        accession=identity.normalize_accession(accession),
        accession_nodash=identity.accession_nodash(accession),
        form=form,
        form_sanitized=identity.sanitize_form(form),
        is_amendment=identity.is_amendment(form),
        filing_date=filing_date,
        report_date=filing_date,
        items=items if items is not None else ["2.02", "7.01", "9.01"],
        filing_dir=str(identity.filing_relpath(cik, form, filing_date, accession)),
        source_index_url=identity.index_json_url(cik, accession),
        source_index_headers_url=identity.artifact_url(
            cik, accession, identity.index_headers_filename(accession)
        ),
    )


def make_artifact_record(
    filing: FilingRecord,
    filename: str,
    *,
    kind: str = identity.KIND_EXHIBIT,
    role: str | None = "ex99-01",
    exhibit_type: str | None = "EX-99.1",
    sequence: int | None = 2,
    cik: int = 1801169,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=identity.artifact_id(cik, filing.accession, filename),
        filing_id=filing.filing_id,
        cik10=filing.cik10,
        accession=filing.accession,
        artifact_kind=kind,
        role=role,
        exhibit_type=exhibit_type,
        sequence=sequence,
        original_filename=filename,
        stored_path=f"source/{filename}",
        source_url=identity.artifact_url(cik, filing.accession, filename),
        media_type=identity.media_type_for(filename),
        file_extension=identity.file_extension(filename),
        expected_size=None,
    )


def write_finalized_filing(
    raw_root: Path,
    filing: FilingRecord,
    files: dict[str, bytes],
    *,
    cik: int = 1801169,
) -> Path:
    """Create a complete, valid finalized filing directory directly on disk."""
    root = raw_root / filing.filing_dir
    (root / "source").mkdir(parents=True, exist_ok=True)

    artifacts: list[StoredArtifact] = []
    for name, payload in files.items():
        path = root / "source" / name
        path.write_bytes(payload)
        artifacts.append(
            StoredArtifact(
                artifact_id=identity.artifact_id(cik, filing.accession, name),
                artifact_kind=identity.KIND_EXHIBIT,
                role="ex99-01",
                exhibit_type="EX-99.1",
                sequence=2,
                original_filename=name,
                stored_path=f"source/{name}",
                media_type=identity.media_type_for(name),
                file_extension=identity.file_extension(name),
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                source_url=identity.artifact_url(cik, filing.accession, name),
                http_status=200,
                downloaded_at="2026-07-31T00:00:00+00:00",
            )
        )

    metadata = FilingMetadata(
        filing_id=filing.filing_id,
        cik=cik,
        cik10=filing.cik10,
        company_name=filing.company_name,
        tickers=filing.tickers,
        accession=filing.accession,
        accession_nodash=filing.accession_nodash,
        form=filing.form,
        form_sanitized=filing.form_sanitized,
        is_amendment=filing.is_amendment,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        items=filing.items,
        source_index_url=filing.source_index_url,
        source_index_headers_url=filing.source_index_headers_url,
        run_id="test-run",
        fetched_at="2026-07-31T00:00:00+00:00",
        fetcher_version="0.1.0",
        artifacts=artifacts,
    )
    (root / "_filing.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    return root
