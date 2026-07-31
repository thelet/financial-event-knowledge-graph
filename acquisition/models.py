"""Pydantic models for manifests, per-filing metadata, and catalog rows.

Schemas are specified in the v0 plan section 8. Field names are SEC-shaped on purpose:
acquisition owns SEC concepts and stores them faithfully. Conversion to canonical
document models belongs to the parsing phase, not here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

VerificationStatus = Literal["verified", "unverified", "mismatch"]


class RunMetadata(BaseModel):
    """Provenance recorded on every manifest and run record (v0 plan section 10)."""

    run_id: str
    stage: str
    created_at: str
    config_hash: str
    code_commit: str | None = None
    python_version: str
    platform: str
    fetcher_version: str
    dependency_versions: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------------------
# DISCOVER
# --------------------------------------------------------------------------------------


class FilingRecord(BaseModel):
    """One expected filing. Produced by DISCOVER."""

    filing_id: str
    source: str = "sec"
    cik10: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)

    accession: str
    accession_nodash: str
    form: str
    form_sanitized: str
    is_amendment: bool = False
    amends_accession: str | None = None

    filing_date: str
    report_date: str | None = None
    acceptance_datetime: str | None = None

    items: list[str] = Field(default_factory=list)
    file_number: str | None = None
    act: str | None = None
    film_number: str | None = None

    primary_document: str | None = None
    primary_doc_description: str | None = None
    is_xbrl: bool = False
    is_inline_xbrl: bool = False
    size_reported: int | None = None

    filing_dir: str
    source_index_url: str
    source_index_headers_url: str


class FilingManifest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: RunMetadata
    manifest_hash: str | None = None
    company_cik10: str
    forms: list[str]
    date_from: str
    date_to: str
    filings: list[FilingRecord]


# --------------------------------------------------------------------------------------
# RESOLVE
# --------------------------------------------------------------------------------------


class ArtifactRecord(BaseModel):
    """One expected artifact. Produced by RESOLVE."""

    artifact_id: str
    filing_id: str
    cik10: str
    accession: str

    artifact_kind: str
    role: str | None = None
    exhibit_type: str | None = None
    description: str | None = None
    sequence: int | None = None

    original_filename: str
    stored_path: str
    source_url: str

    media_type: str
    file_extension: str
    expected_size: int | None = None
    low_processing_priority: bool = False


class ResolutionAnomaly(BaseModel):
    """Something RESOLVE could not classify cleanly. Reported, never silently dropped."""

    filing_id: str
    accession: str
    kind: str
    detail: str


class ArtifactManifest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: RunMetadata
    manifest_hash: str | None = None
    filings_run_id: str
    company_cik10: str
    artifacts: list[ArtifactRecord]
    anomalies: list[ResolutionAnomaly] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# DOWNLOAD
# --------------------------------------------------------------------------------------


class StoredArtifact(BaseModel):
    """One downloaded file, as recorded in _filing.json."""

    artifact_id: str
    artifact_kind: str
    role: str | None = None
    exhibit_type: str | None = None
    description: str | None = None
    sequence: int | None = None

    original_filename: str
    stored_path: str
    media_type: str
    file_extension: str

    size_bytes: int
    sha256: str

    source_url: str
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    download_attempts: int = 1
    downloaded_at: str
    verification_status: VerificationStatus = "verified"
    low_processing_priority: bool = False


class FilingMetadata(BaseModel):
    """Authoritative per-filing record. Written last, inside the staging directory.

    Global catalogs are derived from these files; these files are never derived from the
    catalogs (v0 plan section 9).
    """

    schema_version: int = SCHEMA_VERSION
    filing_id: str
    source: str = "sec"
    cik: int
    cik10: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)

    accession: str
    accession_nodash: str
    form: str
    form_sanitized: str
    is_amendment: bool = False
    amends_accession: str | None = None

    filing_date: str
    report_date: str | None = None
    acceptance_datetime: str | None = None

    items: list[str] = Field(default_factory=list)
    file_number: str | None = None
    act: str | None = None
    film_number: str | None = None

    is_xbrl: bool = False
    is_inline_xbrl: bool = False
    size_reported: int | None = None

    source_index_url: str
    source_index_headers_url: str

    run_id: str
    fetched_at: str
    fetcher_version: str
    tool_versions: dict[str, str] = Field(default_factory=dict)

    artifacts: list[StoredArtifact] = Field(default_factory=list)


class RunRecord(BaseModel):
    run: RunMetadata
    started_at: str
    finished_at: str | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    bytes_downloaded: int = 0
    requests_made: int = 0
    errors: list[str] = Field(default_factory=list)
