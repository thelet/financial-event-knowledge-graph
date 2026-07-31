"""Public contract for the BUILD_CATALOG stage.

`documents.jsonl` and `passages.jsonl` are deliberately absent -- those names are reserved
for the parsing and normalization phases. A filing, a downloaded file, and a normalized
document are three different things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CatalogRequest:
    """No inputs: the catalog is always rebuilt from whatever is finalized on disk."""

@dataclass
class CatalogResult:
    filings_path: Path
    artifacts_path: Path
    filing_count: int
    artifact_count: int
    duplicate_filing_ids: list[str] = field(default_factory=list)
    duplicate_artifact_ids: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.duplicate_filing_ids or self.duplicate_artifact_ids or self.unreadable
        )

    def render(self) -> str:
        lines = [
            f"Filings   : {self.filing_count:>6}  -> {self.filings_path}",
            f"Artifacts : {self.artifact_count:>6}  -> {self.artifacts_path}",
        ]
        if self.duplicate_filing_ids:
            lines.append(f"DUPLICATE filing ids: {len(self.duplicate_filing_ids)}")
            lines.extend(f"  {i}" for i in self.duplicate_filing_ids[:10])
        if self.duplicate_artifact_ids:
            lines.append(f"DUPLICATE artifact ids: {len(self.duplicate_artifact_ids)}")
            lines.extend(f"  {i}" for i in self.duplicate_artifact_ids[:10])
        if self.unreadable:
            lines.append(f"UNREADABLE filing metadata: {len(self.unreadable)}")
            lines.extend(f"  {p}" for p in self.unreadable[:10])
        return "\n".join(lines)


@runtime_checkable
class CatalogStage(Protocol):
    """Any object that can rebuild the derived catalogs."""

    @property
    def name(self) -> str: ...

    def run(self, request: CatalogRequest) -> CatalogResult: ...
