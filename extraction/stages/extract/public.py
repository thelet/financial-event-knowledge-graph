"""Public contract for the integrated extraction pass.

One request, one result, and the durable row a lane pass produces. This stage runs the three
lanes over the selected candidates and writes what they produced; it assembles nothing,
validates nothing and renders nothing, because each of those is a stage that has to be
runnable on its own against a directory this one already wrote.

**The issue vocabulary is the three lanes' union plus two codes only a run can state.** A code
is imported wherever a lane already spells it — STAGE_10 §4's rule, applied one level up — so
the coverage table classifies every silence in one vocabulary rather than three.

`NO_STORED_ANSWER` and `SILENT_NO_FINDING` are the two additions, and both exist because a
passage that produced nothing must say which nothing it was. A candidate whose request has no
recorded answer was never asked; a candidate whose lane answered and recorded neither a claim
nor a reason is a bare silence, which `ClaimLane`'s own contract already says is a different
answer from an abstention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.assembly import MISSING_POPULATION_DEFINITION
from ...core.models import (
    LaneClaim,
    LaneEvent,
    LaneRelationship,
)
from ..narrative.events_public import (
    EVENT_LANE_NAME,
    ISSUE_CODES as EVENT_ISSUE_CODES,
)
from ..narrative.public import (
    LANE_NAME as NARRATIVE_LANE_NAME,
    ISSUE_CODES as NARRATIVE_ISSUE_CODES,
)
from ..tables.deterministic_lane import LANE_NAME as TABLE_LANE_NAME
from ..tables.public import ISSUE_CODES as TABLE_ISSUE_CODES

# The lane names the run routes to. `events` is a lane of this stage's routing rather than of
# the selector's original two: the selector's narrative policy requires *metric* alias
# evidence, and an event lane that offers the declared event category whole has no metric to
# require. See `config/extraction.yaml` `selection.lanes.events`.
TABLE_LANE = "tables"
NARRATIVE_LANE = "narrative"
EVENT_LANE = "events"
LANES: tuple[str, ...] = (TABLE_LANE, NARRATIVE_LANE, EVENT_LANE)

# A lane's own name is not the routing key, and both belong in a durable row. `source_lane` on
# an observation is `normalized_table` — the vocabulary's name for where the value came from,
# which the ontology reads in `forbidden_source_lanes` — while the routing key is `tables`, the
# name the selector and the coverage table use. Written down once, here, because a report that
# counted claims under one spelling and candidates under the other would produce two half-empty
# rows per lane and call it a table.
ROUTING_LANE_OF: dict[str, str] = {
    TABLE_LANE_NAME: TABLE_LANE,
    NARRATIVE_LANE_NAME: NARRATIVE_LANE,
    EVENT_LANE_NAME: EVENT_LANE,
}

# -- the two codes only a run can state -------------------------------------------------------

# The request this candidate would have issued has no recorded answer, so no request was made.
# **Never a silence and never a refusal**: it is a statement about this run's bounds, and
# STAGE_13 §1.1 makes recording it the condition on which a bounded run may be trusted.
NO_STORED_ANSWER = "NO_STORED_ANSWER"

# The lane returned neither a claim nor a reason. Recorded rather than dropped: step 12 found
# one on the real corpus and scored it `silence_broken`, and a coverage table that omitted it
# would show a passage as having been read and say nothing about what reading it produced.
SILENT_NO_FINDING = "SILENT_NO_FINDING"

# -- the §4.1 diagnostic ------------------------------------------------------------------------

# An event whose `occurred_on` equals its `announced_on`. **A review flag, not an error.** A
# change can be announced the day it takes effect, so equal dates are not proof of anything;
# they are the shape the step 12 residual takes, and STAGE_13 §4.1 asks for them to be recorded
# rather than classified. The general announcement-versus-occurrence verifier is follow-up.
ANNOUNCEMENT_EQUALS_OCCURRENCE = "ANNOUNCEMENT_EQUALS_OCCURRENCE"

RUN_ISSUE_CODES = (
    TABLE_ISSUE_CODES
    | NARRATIVE_ISSUE_CODES
    | EVENT_ISSUE_CODES
    | frozenset({
        NO_STORED_ANSWER, SILENT_NO_FINDING, ANNOUNCEMENT_EQUALS_OCCURRENCE,
        MISSING_POPULATION_DEFINITION,
    })
)

# What an issue row is *for*, which is not the same question as what its code is.
REFUSAL = "refusal"          # a lane declined to make a claim it could have made
REJECTION = "rejection"      # a payload was proposed and this layer refused it
DIAGNOSTIC = "diagnostic"    # a flag for review; nothing was refused
NOT_ATTEMPTED = "not_attempted"  # no request was issued, so there is nothing to judge
SEVERITIES: tuple[str, ...] = (REFUSAL, REJECTION, DIAGNOSTIC, NOT_ATTEMPTED)


@dataclass(frozen=True)
class RunIssue:
    """One recorded non-claim, from any lane, with enough to reproduce the decision.

    Flat and durable on purpose. The three lanes' own issue types carry different extra fields
    — a table row index, a metric list, an event type list — and a durable row that inherited
    all three would be mostly empty in every direction. `detail` and `raw_finding` carry what
    the lane knew; the columns here are the ones every consumer joins on.
    """

    code: str
    severity: str
    lane: str
    passage_id: str
    document_id: str
    document_type: str
    detail: str
    concept_ids: tuple[str, ...] = ()
    row_label: str | None = None
    quoted_span: str | None = None
    request_sha256: str | None = None
    rejected_claim: bool = False
    raw_finding: dict[str, Any] | None = None

    def as_row(self) -> dict[str, Any]:
        """The durable row. Key order is fixed here and nowhere else."""
        return {
            "code": self.code,
            "severity": self.severity,
            "lane": self.lane,
            "passage_id": self.passage_id,
            "document_id": self.document_id,
            "document_type": self.document_type,
            "concept_ids": list(self.concept_ids),
            "row_label": self.row_label,
            "quoted_span": self.quoted_span,
            "request_sha256": self.request_sha256,
            "rejected_claim": self.rejected_claim,
            "detail": self.detail,
            "raw_finding": self.raw_finding,
        }


@dataclass(frozen=True)
class PassageOutcome:
    """What one (passage, lane) pair produced. The unit the coverage table counts.

    Recorded for every *selected* candidate, whether it produced a claim or not, because
    STAGE_13 §4 asks for every candidate that produced no claim and its reason — which is only
    answerable if the ones that produced nothing are rows rather than absences.
    """

    passage_id: str
    document_id: str
    document_type: str
    lane: str
    selection_reason: str
    claims: int
    events: int
    relationships: int
    issue_codes: tuple[str, ...]
    request_sha256: str | None = None
    request_issued: bool = False
    # How many concepts the candidate scope offered this passage, where a scope was consulted.
    # None on the table lane, which takes no scope, and on the event lane, which takes none by
    # design (STAGE_12 §4). It is the corpus-cost half of the step 13 scoping evidence.
    scope_size: int | None = None

    @property
    def produced_nothing(self) -> bool:
        return not (self.claims or self.events or self.relationships)

    def as_row(self) -> dict[str, Any]:
        return {
            "passage_id": self.passage_id,
            "document_id": self.document_id,
            "document_type": self.document_type,
            "lane": self.lane,
            "selection_reason": self.selection_reason,
            "claims": self.claims,
            "events": self.events,
            "relationships": self.relationships,
            "issue_codes": list(self.issue_codes),
            "request_issued": self.request_issued,
            "request_sha256": self.request_sha256,
            "scope_size": self.scope_size,
        }


@dataclass
class ExtractRequest:
    """What to extract. `document_ids` of None means the whole corpus."""

    lanes: tuple[str, ...] = LANES
    document_ids: tuple[str, ...] | None = None
    # A ceiling on candidates per lane, for a smoke run. None means no ceiling. Applied after
    # sorting by passage id, so a bounded run is a deterministic prefix rather than a sample.
    limit: int | None = None


@dataclass
class ExtractResult:
    """Everything the lanes produced, in the order the run visited passages.

    Persisted verbatim by `write_lane_outputs`; the catalogs are then built from the file, not
    from this object. That is the split STAGE_13 §7 asks for and the reason the byte-identity
    claim is checkable at all — two builds read the same bytes rather than sharing memory.
    """

    claims: list[LaneClaim] = field(default_factory=list)
    events: list[LaneEvent] = field(default_factory=list)
    relationships: list[LaneRelationship] = field(default_factory=list)
    issues: list[RunIssue] = field(default_factory=list)
    outcomes: list[PassageOutcome] = field(default_factory=list)
    # Every (passage, lane) the selector considered and did not select, with its reason. Not
    # a candidate and therefore not in `outcomes`; counted separately so "why is this passage
    # absent" is answerable for the whole corpus and not only for what was selected.
    unselected: dict[str, int] = field(default_factory=dict)
    ok: bool = True
