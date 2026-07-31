"""Public contract for the BUILD_CATALOG stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CatalogRequest:
    """No inputs: catalogs are always rebuilt from whatever is finalized on disk."""

    run_id: str | None = None


@dataclass
class CatalogResult:
    documents_path: Path | None = None
    passages_path: Path | None = None
    selection_path: Path | None = None
    issues_path: Path | None = None
    document_count: int = 0
    passage_count: int = 0
    selection_count: int = 0
    issue_count: int = 0
    duplicate_document_ids: list[str] = field(default_factory=list)
    duplicate_passage_ids: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.duplicate_document_ids or self.duplicate_passage_ids or self.unreadable)

    def render(self) -> str:
        lines = [
            f"Documents : {self.document_count:>7}  -> {self.documents_path}",
            f"Passages  : {self.passage_count:>7}  -> {self.passages_path}",
            f"Selection : {self.selection_count:>7}  -> {self.selection_path}",
            f"Issues    : {self.issue_count:>7}  -> {self.issues_path}",
        ]
        for label, items in (
            ("DUPLICATE document ids", self.duplicate_document_ids),
            ("DUPLICATE passage ids", self.duplicate_passage_ids),
            ("UNREADABLE", self.unreadable),
        ):
            if items:
                lines.append(f"{label}: {len(items)}")
                lines.extend(f"  {i}" for i in items[:8])
        return "\n".join(lines)


@runtime_checkable
class CatalogStage(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, request: CatalogRequest) -> CatalogResult: ...
