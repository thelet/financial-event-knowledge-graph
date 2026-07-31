"""Public contract for the DOWNLOAD stage.

`FilingOutcome` and `DownloadSummary` are here because they are the result payload a caller
inspects. `DownloadSummary.render` stays with them: a twelve-line method that displays the
type it belongs to is not a separate concern, and splitting it would divide a class from its
own presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

STATUS_DOWNLOADED = "downloaded"

STATUS_SKIPPED = "skipped"

STATUS_REPAIRED = "repaired"

STATUS_FAILED = "failed"

@dataclass
class FilingOutcome:
    filing_id: str
    accession: str
    status: str
    artifact_count: int = 0
    bytes_downloaded: int = 0
    reason: str | None = None

@dataclass
class DownloadSummary:
    outcomes: list[FilingOutcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def bytes_downloaded(self) -> int:
        return sum(o.bytes_downloaded for o in self.outcomes)

    @property
    def failures(self) -> list[FilingOutcome]:
        return [o for o in self.outcomes if o.status == STATUS_FAILED]

    def render(self) -> str:
        lines = [
            f"Downloaded : {self.count(STATUS_DOWNLOADED)}",
            f"Repaired   : {self.count(STATUS_REPAIRED)}",
            f"Skipped    : {self.count(STATUS_SKIPPED)}  (already valid and complete)",
            f"Failed     : {self.count(STATUS_FAILED)}",
            f"Bytes      : {self.bytes_downloaded / 1_048_576:.1f} MiB",
        ]
        for failure in self.failures:
            lines.append(f"  FAILED {failure.accession}: {failure.reason}")
        return "\n".join(lines)

@dataclass(frozen=True)
class DownloadRequest:
    cik: str | None = None
    artifacts_run_id: str | None = None
    limit: int | None = None
    force: bool = False
    progress: Callable[[int, int, FilingOutcome], None] | None = None

@dataclass(frozen=True)
class DownloadResult:
    run_id: str
    artifacts_run_id: str
    summary: DownloadSummary
    requests_made: int
    filing_count: int
    artifact_count: int

    @property
    def bytes_downloaded(self) -> int:
        return self.summary.bytes_downloaded

    @property
    def ok(self) -> bool:
        return not self.summary.failures


@runtime_checkable
class DownloadStage(Protocol):
    """Any object that can fetch artifacts and finalize filings."""

    @property
    def name(self) -> str: ...

    def run(self, request: DownloadRequest) -> DownloadResult: ...
