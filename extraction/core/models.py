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
# absence from extraction is as auditable as its presence — the discipline
# `selection.jsonl` already applies to artifacts one layer up.
#
# These live here rather than in the select stage because `CandidatePassage.selected` has to
# know which codes mean "in", and a core model must not import a stage.
EXACT_ALIAS = "exact_alias"
AMBIGUOUS_ALIAS_SIGNAL = "ambiguous_alias"
TABLE_LABEL = "table_label"
DOCUMENT_TYPE_PRIOR = "document_type_prior"
HEADING_PRIOR = "heading_prior"
STABLE_CORE = "stable_core"
ADJACENT_SCALE_CONTEXT = "adjacent_scale_context"

INCLUSION_REASONS = frozenset({
    EXACT_ALIAS, AMBIGUOUS_ALIAS_SIGNAL, TABLE_LABEL, DOCUMENT_TYPE_PRIOR,
    HEADING_PRIOR, STABLE_CORE, ADJACENT_SCALE_CONTEXT,
})

UNSUPPORTED_DOCUMENT_TYPE = "unsupported_document_type"
UNSUPPORTED_PASSAGE_KIND = "unsupported_passage_kind"
NO_CANDIDATE_SIGNAL = "no_candidate_signal"
DEFERRED_REQUIRED_SOURCE_LANE = "deferred_required_source_lane"
CONTRACT_BOILERPLATE = "contract_boilerplate"
GOVERNANCE_BOILERPLATE = "governance_boilerplate"

EXCLUSION_REASONS = frozenset({
    UNSUPPORTED_DOCUMENT_TYPE, UNSUPPORTED_PASSAGE_KIND, NO_CANDIDATE_SIGNAL,
    DEFERRED_REQUIRED_SOURCE_LANE, CONTRACT_BOILERPLATE, GOVERNANCE_BOILERPLATE,
})

SELECTION_REASONS = INCLUSION_REASONS | EXCLUSION_REASONS

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
    # Where the declaration was found. `metric_default` means no source declared one and the
    # metric's own unit governed — recorded distinctly so "nobody said" is never mistaken for
    # "the table said units".
    #
    # `inline_prose` is the narrative lane's location and the one a table cannot produce: a
    # letter writes "$279 million" and the scale travels with that number rather than being
    # declared once for a block of figures. Recorded as its own location because "the word was
    # printed beside the figure" and "a header two rows up said so" are different strengths of
    # evidence, and a report that collapsed them could not say which claims rest on which.
    location: Literal["table_header", "preceding_context", "metric_default", "inline_prose"]
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
        return self.reason in INCLUSION_REASONS


class LaneAbstention(BaseModel):
    """A claim a lane deliberately did not make, and why.

    First-class rather than a log line. A large minority of the benchmark's cases expect an
    abstention rather than a claim, so a lane that cannot express one cannot be scored. The
    count is deliberately not written here: it was `24` until a 2026-08-02 gold correction
    made it 23, and a number in a docstring is a number nothing regenerates.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str
    detail: str
    passage_id: str
    candidate_metric_ids: tuple[str, ...] = ()
    row_label: str | None = None
    # True when the lane refused something a reader proposed; False when the reader itself
    # declined. Both are silences and they are not the same silence: a lane refusing a
    # fabricated quotation is the lane working, and a model declining to answer is the model
    # working, and §5 says step 11 scores them apart. Added at stage 10, where a narrative
    # rejection was otherwise indistinguishable from an abstention through this protocol.
    # Defaulted so the deterministic table lane, whose issues are all its own decisions, is
    # unchanged.
    rejected_claim: bool = False


class LaneClaim(BaseModel):
    """What a lane read, before it becomes an ontology claim.

    `value` is already scaled — the lane owns scale resolution because only the lane can see
    the declaration. `raw_text` keeps the cell or phrase as printed so a scale or sign error
    is recoverable after the fact instead of being a bare wrong number.

    **The two grid fields are optional because only a grid has a position.** They state where
    in a table the reading sits, and they are the discriminator `observation_id` digests; a
    lane reading prose leaves both unset and its ids are unchanged by their existence. They
    are explicit fields rather than `extractor_metadata` keys precisely because an id is
    derived from them — a free-form metadata dict is where a key gets renamed without anything
    noticing, and renaming one of these silently changes every table observation id.
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
    # Where in a grid this reading sits: the data row carrying the metric label, and the header
    # column carrying the period (`PeriodColumn.column_index`) — the *original* column index,
    # which is what evidence points at, and not the value cell's, which shifts row by row with
    # layout spacing (see `_value_cells`).
    #
    # **No `table_id` here, and the omission is deliberate.** The grid's own identity is
    # already in the digest as `passage_id`, which is what a table passage is; a `table_id`
    # beside it would add nothing and would make the id depend on the entry point, because a
    # run calls `extract` (which supplies none) while the benchmark calls `extract_table` with
    # the catalog's real block id — two callers, one cell, two ids.
    row_index: int | None = None
    column_index: int | None = None
    population_definition_raw: str | None = None
    ambiguity_codes: tuple[str, ...] = ()
    confidence: float | None = None
    extractor_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def structural_position(self) -> tuple[str, ...]:
        """The claim's grid coordinates, as ordered parts for a digest. Empty when it has none.

        Empty is the honest answer for prose and it is also the additive one: `observation_id`
        digests `()` to exactly the id it minted before this field existed, so adding grid
        coordinates changed table ids and left every narrative id alone.

        Self-describing parts (`row=12`) rather than bare numbers, because these surface in a
        debugging session as the reason two ids differ, and `12` on its own does not say
        whether it is a row or a column.
        """
        if self.row_index is None and self.column_index is None:
            return ()
        return (
            f"row={'' if self.row_index is None else self.row_index}",
            f"column={'' if self.column_index is None else self.column_index}",
        )


class LaneEventParticipant(BaseModel):
    """One entity taking part in an event, in a role the event type declares.

    `entity_text` is the passage's own words for the participant, kept for the same reason
    `LaneClaim.raw_text` is: an id derived from a printed name is auditable only beside the
    name it was derived from.

    `named` records whether the filing actually named the entity. "a subsidiary of the
    Company" is a real participant that the filing never names, and an unresolved placeholder
    and a resolved id must not look alike — the first is a flag for entity resolution and the
    second is an answer.
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    entity_id: str
    entity_type: str
    entity_text: str
    named: bool = True


class LaneEvent(BaseModel):
    """What a lane read about one event, before it becomes an ontology claim.

    **Both dates are optional here and neither is ever derived from the other.** `occurred_on`
    is populated only from evidence saying the event happened or took effect;`announced_on`
    only from a dateline or an explicit statement that it was announced on that day. A
    document's `filing_date` or `report_date` populates neither. Which of them an event type
    requires is the event type's declaration to make (`required_temporal_fields`,
    `required_temporal_any_of`) and this model deliberately encodes no answer to it — the
    lane asks the definition (V1_CLAIM_EXTRACTION §4.0b).
    """

    model_config = ConfigDict(extra="forbid")

    event_type_id: str
    occurred_on: str | None = None
    announced_on: str | None = None
    participants: tuple[LaneEventParticipant, ...] = ()
    properties: dict[str, str] = Field(default_factory=dict)
    passage_id: str
    document_id: str
    raw_text: str
    source_lane: str
    assertion_type: str = "reported"
    extractor_metadata: dict[str, Any] = Field(default_factory=dict)


class LaneRelationship(BaseModel):
    """A typed edge a lane read out of one passage.

    Endpoints carry both an id and a type because the ontology validates only the second:
    `check_relationship_instance` reads `source_type` and `target_type` against the
    predicate's declarations and never looks at an id. The ids are carried, and are
    deterministic, because the graph is built from them.

    `relationship_id` is the **uppercase** predicate the registry is keyed by
    (`HOLDS_POSITION_AT`), not the concept id (`holds_position_at`). The two indexes exist and
    only one of them is the one `validate_relationship` asks.
    """

    model_config = ConfigDict(extra="forbid")

    relationship_id: str
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    valid_from: str | None = None
    valid_to: str | None = None
    passage_id: str
    document_id: str
    raw_text: str
    source_lane: str
    assertion_type: str = "reported"
    extractor_metadata: dict[str, Any] = Field(default_factory=dict)


class LaneResult(BaseModel):
    """Everything one lane produced for one candidate passage."""

    model_config = ConfigDict(extra="forbid")

    passage_id: str
    lane: str
    claims: tuple[LaneClaim, ...] = ()
    abstentions: tuple[LaneAbstention, ...] = ()
