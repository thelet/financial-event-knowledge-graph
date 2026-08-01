"""Public contract for typed candidate selection.

Reason codes are the point of this module. A passage's absence from extraction must be as
auditable as its presence — the same discipline `selection.jsonl` already applies to
artifacts one layer up — so every passage receives exactly one code per lane, whether it was
selected or not.

No benchmark import belongs here, and an executable test enforces it. Gold annotations are
for scoring a selection policy after the fact; a policy that consulted them would be
measuring itself against its own answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ...core.models import CandidatePassage

# Reason codes live in `core.models` because `CandidatePassage.selected` must know which
# ones mean "in", and a core model may not import a stage. Re-exported here so callers see
# them on the stage's public contract where they belong.
from ...core.models import (  # noqa: E402
    ADJACENT_SCALE_CONTEXT,
    AMBIGUOUS_ALIAS_SIGNAL as AMBIGUOUS_ALIAS,
    CONTRACT_BOILERPLATE,
    DEFERRED_REQUIRED_SOURCE_LANE,
    DOCUMENT_TYPE_PRIOR,
    EXACT_ALIAS,
    EXCLUSION_REASONS,
    GOVERNANCE_BOILERPLATE,
    HEADING_PRIOR,
    INCLUSION_REASONS,
    NO_CANDIDATE_SIGNAL,
    STABLE_CORE,
    TABLE_LABEL,
    UNSUPPORTED_DOCUMENT_TYPE,
    UNSUPPORTED_PASSAGE_KIND,
)

REASON_CODES = INCLUSION_REASONS | EXCLUSION_REASONS


@dataclass(frozen=True)
class SelectionRequest:
    run_id: str
    lanes: tuple[str, ...] = ("tables", "narrative")
    # None means the whole normalized corpus.
    document_ids: tuple[str, ...] | None = None


@dataclass
class SelectionResult:
    candidates: list[CandidatePassage] = field(default_factory=list)
    ok: bool = True

    @property
    def selected(self) -> list[CandidatePassage]:
        return [c for c in self.candidates if c.selected]

    def by_lane(self, lane: str) -> list[CandidatePassage]:
        return [c for c in self.candidates if c.lane == lane and c.selected]


@runtime_checkable
class CandidateSelector(Protocol):
    """Decides which passages each lane sees, and records why for every passage."""

    @property
    def name(self) -> str: ...

    def run(self, request: SelectionRequest) -> SelectionResult: ...
