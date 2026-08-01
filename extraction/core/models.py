"""The objects that move between extraction stages.

Deliberately provider-independent. A `LaneClaim` carries what a lane read and where it read
it, never a model response, a prompt, or a vendor type — `extractor_metadata` is a free dict
that nothing downstream interprets, matching `OntologyClaim`'s own rule.

The split between `LaneClaim` and the ontology's `MetricObservation` is the point of this
module. A lane reports what it saw in a passage; `assemble` turns that into an observation
with a period, a scale and an identity. Keeping them apart is what lets the table lane be
tested without an ontology and the assembler be tested without a corpus.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Why a passage was or was not offered to a lane. Recorded either way, so a passage's
# absence from extraction is as auditable as its presence.
SELECTED = "SELECTED"
DOCUMENT_TYPE_EXCLUDED = "DOCUMENT_TYPE_EXCLUDED"
NO_ALIAS_MATCH = "NO_ALIAS_MATCH"
WRONG_PASSAGE_KIND = "WRONG_PASSAGE_KIND"
SELECTION_REASONS = frozenset({SELECTED, DOCUMENT_TYPE_EXCLUDED, NO_ALIAS_MATCH, WRONG_PASSAGE_KIND})

# Why a lane declined to emit a claim it could plausibly have emitted. These are findings,
# not failures: the benchmark scores a lane on producing them.
AMBIGUOUS_ALIAS = "AMBIGUOUS_ALIAS"
UNAVAILABLE_REQUIRED_SOURCE_LANE = "UNAVAILABLE_REQUIRED_SOURCE_LANE"
NO_MATCHING_METRIC = "NO_MATCHING_METRIC"
METRIC_SPLIT_ACROSS_ROWS = "METRIC_SPLIT_ACROSS_ROWS"
UNRESOLVED_PERIOD = "UNRESOLVED_PERIOD"
UNRESOLVED_SCALE = "UNRESOLVED_SCALE"
DERIVED_CHANGE_COLUMN = "DERIVED_CHANGE_COLUMN"
NOT_THE_SUBJECT_COMPANY = "NOT_THE_SUBJECT_COMPANY"
GUIDANCE_NOT_REPORTED = "GUIDANCE_NOT_REPORTED"
ABSTENTION_REASONS = frozenset({
    AMBIGUOUS_ALIAS, UNAVAILABLE_REQUIRED_SOURCE_LANE, NO_MATCHING_METRIC,
    METRIC_SPLIT_ACROSS_ROWS, UNRESOLVED_PERIOD, UNRESOLVED_SCALE,
    DERIVED_CHANGE_COLUMN, NOT_THE_SUBJECT_COMPANY, GUIDANCE_NOT_REPORTED,
})

Scale = Literal["units", "thousands", "millions", "billions"]

SCALE_FACTOR: dict[str, int] = {
    "units": 1,
    "thousands": 1_000,
    "millions": 1_000_000,
    "billions": 1_000_000_000,
}


class ScaleDeclaration(BaseModel):
    """Where a table's magnitude scale was found, and what it said.

    Carried explicitly because the corpus puts it in two places: 65 of 74 KPI-bearing table
    passages declare it inside the table, and 9 declare it in the immediately preceding
    narrative passage *(verified 2026-08-01)*. A lane that only reads the table is wrong by
    six orders of magnitude on every dollar figure in those 9 and right on every count,
      which is why `scale` is scored as its own benchmark dimension.
    """

    model_config = ConfigDict(extra="forbid")

    scale: Scale
    location: Literal["in_table", "preceding_passage", "assumed_units"]
    source_passage_id: str | None = None
    declaration_text: str | None = None
    # Row labels the declaration excludes, e.g. "except percentages, homes sold". Kept as
    # written rather than parsed into metric ids: the exclusion list names filing labels,
    # and mapping those to concepts is the resolver's job, not the reader's.
    exceptions_text: str | None = None

    @property
    def factor(self) -> int:
        return SCALE_FACTOR[self.scale]


class PeriodRef(BaseModel):
    """A resolved period: a duration or an instant, never both.

    `label_raw` is the column or phrase it came from, kept because period resolution is the
    dimension most likely to fail silently and the raw label is what makes a wrong answer
    diagnosable.
    """

    model_config = ConfigDict(extra="forbid")

    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    label_raw: str | None = None

    @property
    def is_instant(self) -> bool:
        return self.instant_date is not None

    @property
    def key(self) -> str:
        """Readable period component of a deterministic id: 2023Q4, FY2023, 2023-12-31."""
        if self.instant_date:
            return self.instant_date
        if not (self.period_start and self.period_end):
            return "unknown"
        start, end = self.period_start, self.period_end
        start_year, end_year = start[:4], end[:4]
        if start == f"{start_year}-01-01" and end == f"{end_year}-12-31" and start_year == end_year:
            return f"FY{end_year}"
        quarters = {("01-01", "03-31"): 1, ("04-01", "06-30"): 2,
                    ("07-01", "09-30"): 3, ("10-01", "12-31"): 4}
        quarter = quarters.get((start[5:], end[5:]))
        if quarter and start_year == end_year:
            return f"{end_year}Q{quarter}"
        return f"{start}_{end}"


class CandidatePassage(BaseModel):
    """One passage offered to one lane, with the reason it was or was not offered."""

    model_config = ConfigDict(extra="forbid")

    passage_id: str
    document_id: str
    document_type: str
    passage_kind: str
    lane: str
    reason: str
    matched_metric_ids: tuple[str, ...] = ()
    # The preceding passage under the same heading, when there is one. The table lane needs
    # it for the scale declaration; nothing else may read it.
    preceding_passage_id: str | None = None

    @property
    def selected(self) -> bool:
        return self.reason == SELECTED


class LaneAbstention(BaseModel):
    """A claim a lane deliberately did not make, and why.

    First-class rather than a log line. The benchmark's expected behaviour for 24 of its
    cases is an abstention, so a lane that cannot express one cannot be scored.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str
    detail: str
    passage_id: str
    candidate_metric_ids: tuple[str, ...] = ()
    row_label: str | None = None


class LaneClaim(BaseModel):
    """What a lane read, before it becomes an ontology claim.

    `value` is already scaled — the lane owns scale resolution because only the lane can see
    the declaration. `raw_text` keeps the cell or phrase as printed so a scale or sign error
    is recoverable after the fact instead of being a bare wrong number.
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    value: float | int
    unit: str
    currency: str | None = None
    period: PeriodRef
    subject_entity_id: str = "opendoor"
    subject_type: str = "public_company"
    source_lane: str
    assertion_type: str = "reported"
    passage_id: str
    document_id: str
    raw_text: str
    scale: ScaleDeclaration | None = None
    row_label: str | None = None
    column_label: str | None = None
    population_definition_raw: str | None = None
    ambiguity_codes: tuple[str, ...] = ()
    confidence: float | None = None
    extractor_metadata: dict[str, Any] = Field(default_factory=dict)


class LaneResult(BaseModel):
    """Everything one lane produced for one candidate passage."""

    model_config = ConfigDict(extra="forbid")

    passage_id: str
    lane: str
    claims: tuple[LaneClaim, ...] = ()
    abstentions: tuple[LaneAbstention, ...] = ()
