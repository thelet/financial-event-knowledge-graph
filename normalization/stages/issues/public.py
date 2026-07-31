"""Public contract for the WRITE_ISSUES stage.

Owns the authoritative per-run issue file. It exists as its own stage, rather than as a
side effect of the CLI, because the derived issue catalog is built from that file: if the
write happens after the catalog stage, a single pipeline run leaves the catalog empty.

Authoritative:  normalization_runs/<run_id>-issues.jsonl
Derived:        normalization_catalog/issues.jsonl
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ...core.models import NormalizationIssue


@dataclass(frozen=True)
class IssueLogRequest:
    run_id: str
    issues: list[NormalizationIssue] = field(default_factory=list)


@dataclass
class IssueLogResult:
    path: Path | None = None
    issue_count: int = 0
    duplicate_ids: list[str] = field(default_factory=list)
    written: bool = False

    @property
    def ok(self) -> bool:
        # A run with no issues is a success, and still writes an empty authoritative file.
        return self.written


@runtime_checkable
class IssueLogStage(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, request: IssueLogRequest) -> IssueLogResult: ...
