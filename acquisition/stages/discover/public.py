"""Public contract for the DISCOVER stage.

Types and the stage interface only. No SEC, HTTP, filesystem, manifest, or formatting
logic lives here -- a caller can read this file to understand and replace the stage
without meeting any provider detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ...core.models import FilingRecord


@dataclass(frozen=True)
class DiscoverRequest:
    cik: str | None = None
    include_amendments: bool = True

@dataclass(frozen=True)
class DiscoverResult:
    run_id: str
    manifest_path: Path
    filings: list[FilingRecord]
    requests_made: int
    company_name: str
    company_cik10: str
    date_from: str
    date_to: str
    config_hash: str
    manifest_hash: str

    @property
    def filing_count(self) -> int:
        return len(self.filings)

    @property
    def ok(self) -> bool:
        return bool(self.filings)


@runtime_checkable
class DiscoverStage(Protocol):
    """Any object that can discover filings and record a filing manifest."""

    @property
    def name(self) -> str: ...

    def run(self, request: DiscoverRequest) -> DiscoverResult: ...
