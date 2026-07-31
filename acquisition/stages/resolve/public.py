"""Public contract for the RESOLVE stage.

Types, anomaly kinds, and the stage interface. The anomaly constants are here rather than
with the implementation because they name what a caller finds inside a result and may
legitimately match on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from ...core.models import ArtifactRecord, FilingRecord, ResolutionAnomaly

ANOMALY_UNRECOGNIZED_TYPE = "unrecognized_type"
ANOMALY_NO_PRIMARY = "no_primary_document"
ANOMALY_MULTIPLE_PRIMARY = "multiple_primary_documents"
ANOMALY_HEADER_NOT_IN_INDEX = "header_document_missing_from_index"
ANOMALY_INDEX_NOT_IN_HEADER = "index_file_not_in_header"
ANOMALY_CASE_COLLISION = "case_insensitive_filename_collision"
ANOMALY_PARSE_FAILED = "header_parse_failed"


@dataclass(frozen=True)
class ResolveRequest:
    cik: str | None = None
    filings_run_id: str | None = None
    limit: int | None = None
    progress: Callable[[int, int, FilingRecord], None] | None = None

@dataclass(frozen=True)
class ResolveResult:
    run_id: str
    filings_run_id: str
    manifest_path: Path
    artifacts: list[ArtifactRecord]
    anomalies: list[ResolutionAnomaly]
    requests_made: int
    filing_count: int

    @property
    def artifact_count(self) -> int:
        return len(self.artifacts)

    @property
    def ok(self) -> bool:
        return bool(self.artifacts)


@runtime_checkable
class ResolveStage(Protocol):
    """Any object that can expand filings into their artifact set."""

    @property
    def name(self) -> str: ...

    def run(self, request: ResolveRequest) -> ResolveResult: ...
