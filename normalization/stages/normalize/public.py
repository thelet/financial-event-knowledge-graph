"""Public contract for the NORMALIZE stage.

Turns selected artifacts into authoritative normalized documents and passages. Owns the
per-artifact loop, because three collaborators (parser, passage strategy, store) each
contribute to one document and only the finished document is meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from ...core.models import NormalizationIssue, NormalizedDocument, ParserComparison, SelectedArtifact


@dataclass(frozen=True)
class NormalizeRequest:
    run_id: str
    artifacts: list[SelectedArtifact] = field(default_factory=list)
    mode: str = "normal"
    progress: Callable[[int, int, str], None] | None = None


@dataclass
class NormalizeResult:
    documents: list[NormalizedDocument] = field(default_factory=list)
    issues: list[NormalizationIssue] = field(default_factory=list)
    comparisons: list[ParserComparison] = field(default_factory=list)
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    attempted: int = 0

    @property
    def failed(self) -> list[NormalizationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return bool(self.documents) and not self.failed


@runtime_checkable
class NormalizeStage(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, request: NormalizeRequest) -> NormalizeResult: ...
