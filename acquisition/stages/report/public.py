"""Public contract for the REPORT stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ReportRequest:
    artifacts_run_id: str | None = None

@dataclass(frozen=True)
class ReportResult:
    path: Path
    text: str

    @property
    def ok(self) -> bool:
        return True


@runtime_checkable
class ReportStage(Protocol):
    """Any object that can render the corpus report."""

    @property
    def name(self) -> str: ...

    def run(self, request: ReportRequest) -> ReportResult: ...
