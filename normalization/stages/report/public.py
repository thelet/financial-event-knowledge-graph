"""Public contract for the REPORT stage.

A separate stage rather than a module inside verify: verification answers "is the corpus
broken", reporting answers "what does it look like and what needs human judgement". Those
are different questions with different audiences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ...core.models import NormalizationIssue, NormalizedDocument, ParserComparison


@dataclass(frozen=True)
class ReportRequest:
    run_id: str
    documents: list[NormalizedDocument] = field(default_factory=list)
    issues: list[NormalizationIssue] = field(default_factory=list)
    comparisons: list[ParserComparison] = field(default_factory=list)
    review: bool = True


@dataclass
class ReportResult:
    corpus_path: Path | None = None
    review_path: Path | None = None
    comparison_path: Path | None = None

    @property
    def ok(self) -> bool:
        return True


@runtime_checkable
class ReportStage(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, request: ReportRequest) -> ReportResult: ...
