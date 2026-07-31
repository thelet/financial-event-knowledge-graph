"""Public contract for the SELECT stage.

Types and the stage interface only. No catalog reading, filesystem access, or policy
evaluation here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ...core.models import NormalizationIssue, SelectedArtifact

# Reason codes. Every artifact receives exactly one.
PRIMARY_NARRATIVE = "PRIMARY_NARRATIVE"
EARNINGS_MATERIAL = "EARNINGS_MATERIAL"
MATERIAL_AGREEMENT = "MATERIAL_AGREEMENT"
GOVERNANCE_DOCUMENT = "GOVERNANCE_DOCUMENT"
STRUCTURED_EXHIBIT = "STRUCTURED_EXHIBIT"
BOILERPLATE_CERTIFICATION = "BOILERPLATE_CERTIFICATION"
NON_NARRATIVE_MEDIA = "NON_NARRATIVE_MEDIA"
XBRL_LANE = "XBRL_LANE"
ARCHIVAL_ONLY = "ARCHIVAL_ONLY"
BELOW_TEXT_FLOOR = "BELOW_TEXT_FLOOR"
NEEDS_REVIEW = "NEEDS_REVIEW"

REASON_CODES = frozenset(
    {
        PRIMARY_NARRATIVE, EARNINGS_MATERIAL, MATERIAL_AGREEMENT, GOVERNANCE_DOCUMENT,
        STRUCTURED_EXHIBIT, BOILERPLATE_CERTIFICATION, NON_NARRATIVE_MEDIA, XBRL_LANE,
        ARCHIVAL_ONLY, BELOW_TEXT_FLOOR, NEEDS_REVIEW,
    }
)


@dataclass(frozen=True)
class SelectRequest:
    run_id: str
    artifact_ids: list[str] | None = None   # None means the whole acquisition catalog
    write_manifest: bool = True


@dataclass
class SelectResult:
    artifacts: list[SelectedArtifact] = field(default_factory=list)
    manifest_path: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)
    # Selection is an issue producer in its own right: an artifact no rule classified is a
    # finding regardless of whether it later normalizes.
    issues: list[NormalizationIssue] = field(default_factory=list)

    @property
    def included(self) -> list[SelectedArtifact]:
        """Artifacts a rule positively included."""
        return [a for a in self.artifacts if a.decision == "include"]

    @property
    def needs_review(self) -> list[SelectedArtifact]:
        return [a for a in self.artifacts if a.decision == "needs_review"]

    @property
    def for_processing(self) -> list[SelectedArtifact]:
        """Everything that enters normalization: include plus needs_review.

        `needs_review` means "a human should look at this", not "throw it away". Measured
        against the real corpus, SEC exhibit descriptions carry no information at all --
        every one is just "EXHIBIT 10.12" or "ex-10.1" -- and item codes classify only 24
        of 64 EX-10 artifacts. Excluding unclassified agreements from processing would
        silently drop 40 material contracts from the corpus, which is a worse outcome than
        normalizing them with a flag. They are normalized, marked `needs_review` on the
        document, and listed first in the corpus report.
        """
        return [a for a in self.artifacts if a.decision in ("include", "needs_review")]

    @property
    def ok(self) -> bool:
        return bool(self.for_processing)


@runtime_checkable
class SelectStage(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, request: SelectRequest) -> SelectResult: ...
