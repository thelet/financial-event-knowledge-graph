"""Public contract for the ontology-guided narrative lane.

Four things live here and nothing else: the issue vocabulary, the response schema the model is
constrained to, the declared-ambiguous wordings offered alongside it, and the structured result
a passage produces. All four are contract rather than implementation — `prompt.py` renders the
schema, `response_mapping.py` decides the codes, and `narrative_lane.py` orchestrates, and none
of them may invent a fifth vocabulary between them.

**The ambiguous wordings are here and not in `prompt.py` because two modules read them.** The
prompt tells the model to abstain on them and `response_mapping._ambiguity_unsettled` enforces
that it did; a rule stated in one place and checked from another is how the two drift, which is
exactly the drift review found in 2026-08-02 when the instruction had no check at all.

**The vocabulary is the table lane's wherever the meaning is identical.** `AMBIGUOUS_ALIAS`,
`UNRESOLVED_METRIC`, `MISSING_PERIOD`, `PERIOD_TYPE_MISMATCH`, `MISSING_UNIT`,
`INVALID_NUMBER`, `DEFERRED_REQUIRED_SOURCE_LANE` and `MISSING_REQUIRED_CONTEXT` are
imported from `stages/tables/public.py`, not re-spelled. Step 11 classifies both lanes'
silences in one table, and two spellings of one abstention would make that classification
meaningless (STAGE_10 §4).

What prose adds is genuinely new. A table row cannot be a period-over-period *sentence*, a
definition of the metric it names, a statement about a third party, or a quotation that is
not in the passage — and each of those is a refusal this lane has to be able to state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.assembly import MISSING_POPULATION_DEFINITION
from ...core.models import (
    GUIDANCE_NOT_REPORTED,
    NOT_THE_SUBJECT_COMPANY,
    LaneAbstention,
    LaneClaim,
    LaneResult,
)
from ..tables.public import (
    AMBIGUOUS_ALIAS,
    DEFERRED_REQUIRED_SOURCE_LANE,
    INVALID_NUMBER,
    MISSING_PERIOD,
    MISSING_REQUIRED_CONTEXT,
    MISSING_UNIT,
    PERIOD_TYPE_MISMATCH,
    UNRESOLVED_METRIC,
)

LANE_NAME = "normalized_narrative"
LANE_VERSION = "1.0.0"

# -- issue codes prose needs and a table cannot produce ---------------------------------------

# The model named a concept the injected scope did not offer. Distinct from
# `UNRESOLVED_METRIC`, which means a surface in the text matched nothing: this one means the
# answer named something that was never on the menu, and remapping it to the nearest concept
# in scope is §7's forbidden move.
METRIC_OUT_OF_SCOPE = "METRIC_OUT_OF_SCOPE"

# The answer's unit or its magnitude scale contradicts what the ontology declares. The
# ontology declares it; the model does not get a vote.
UNIT_CONTRADICTS_ONTOLOGY = "UNIT_CONTRADICTS_ONTOLOGY"
SCALE_NOT_APPLICABLE = "SCALE_NOT_APPLICABLE"
SCALE_NOT_DECLARED = "SCALE_NOT_DECLARED"

# The three evidence refusals. Fabricated evidence is the one failure this pipeline exists to
# refuse, so it is not one code but three: the quotation is not in the passage at all, the
# number is not in the quotation it was supposedly read from, or the two disagree.
QUOTED_SPAN_NOT_IN_PASSAGE = "QUOTED_SPAN_NOT_IN_PASSAGE"
VALUE_NOT_IN_QUOTED_SPAN = "VALUE_NOT_IN_QUOTED_SPAN"
VALUE_CONTRADICTS_QUOTED_TEXT = "VALUE_CONTRADICTS_QUOTED_TEXT"

# A period-over-period change stated in a sentence. The table lane's `DERIVED_CHANGE_COLUMN`
# is the same finding about a column; prose states it as "increased by $149.7 million to
# $169.7 million", where only the second figure is an observation.
DERIVED_COMPARISON = "DERIVED_COMPARISON"

# The passage explains what a metric means rather than reporting a value for it. A lane that
# mines a number out of a definition paragraph is attaching a value to a definition.
DEFINITIONAL_NOT_OBSERVATIONAL = "DEFINITIONAL_NOT_OBSERVATIONAL"

# §7.1: a metric whose ontology entry declares a `population` must carry the filed denominator
# wording verbatim, or two observations that are not one series will look like one. Imported
# rather than re-spelled: `core.assembly` enforces the same policy at the other end, and this
# lane refusing under a differently spelled code would split one finding into two.

# The provider answered and the answer could not be used — unparseable, non-conformant, or
# truncated. A model result rather than a fault, so it is recorded as a finding rather than
# raised: a transport failure propagates, an unusable answer is data about the model.
MODEL_ANSWER_UNUSABLE = "MODEL_ANSWER_UNUSABLE"

# The prompt plus a usable output budget does not fit the model's context slot, so no request
# was issued at all. A lane decision taken before a provider is reached, and therefore not a
# model result: recorded so a passage that produced nothing says which of the two it was
# *(added 2026-08-02 after review found the lane issuing a request that structurally could not
# fit — see `narrative_lane.output_budget`)*.
PROMPT_EXCEEDS_CONTEXT = "PROMPT_EXCEEDS_CONTEXT"

# What the *model* may choose. Deliberately narrower than `ISSUE_CODES`: every other code is
# a decision `response_mapping.py` makes about an answer, and offering the model a reason it
# cannot possibly assess invites it to pick one instead of answering.
MODEL_ABSTENTION_REASONS: tuple[str, ...] = (
    AMBIGUOUS_ALIAS,
    UNRESOLVED_METRIC,
    MISSING_PERIOD,
    MISSING_UNIT,
    MISSING_REQUIRED_CONTEXT,
    DEFERRED_REQUIRED_SOURCE_LANE,
    NOT_THE_SUBJECT_COMPANY,
    DERIVED_COMPARISON,
    DEFINITIONAL_NOT_OBSERVATIONAL,
    GUIDANCE_NOT_REPORTED,
)

# Everything this lane can put on a non-claim, whoever decided it.
ISSUE_CODES = frozenset(MODEL_ABSTENTION_REASONS) | frozenset({
    PERIOD_TYPE_MISMATCH, INVALID_NUMBER, METRIC_OUT_OF_SCOPE, UNIT_CONTRADICTS_ONTOLOGY,
    SCALE_NOT_APPLICABLE, SCALE_NOT_DECLARED, QUOTED_SPAN_NOT_IN_PASSAGE,
    VALUE_NOT_IN_QUOTED_SPAN, VALUE_CONTRADICTS_QUOTED_TEXT,
    MISSING_POPULATION_DEFINITION, MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT,
})

# -- the answer's own vocabulary ---------------------------------------------------------------

# What kind of statement a figure is. Asked rather than inferred: "Contribution Profit
# increased by $149.7 million to $169.7 million" contains one observation and one change, and
# a schema with no way to say which produces two observations for one measured period.
REPORTED_LEVEL = "reported_level"
PERIOD_OVER_PERIOD_CHANGE = "period_over_period_change"
DEFINITION_ONLY = "definition_only"
FORWARD_GUIDANCE = "forward_guidance"
STATEMENT_TYPES: tuple[str, ...] = (
    REPORTED_LEVEL, PERIOD_OVER_PERIOD_CHANGE, DEFINITION_ONLY, FORWARD_GUIDANCE)

# Whose figure it is. A single-registrant corpus still quotes market statistics about
# third-party inventory in the same sentence as its own, and emitting one of those with
# `subject_entity_id: opendoor` is a fabricated measurement.
FILING_COMPANY = "the_filing_company"
ANOTHER_PARTY = "another_party"
SUBJECTS: tuple[str, ...] = (FILING_COMPANY, ANOTHER_PARTY)

DURATION = "duration"
INSTANT = "instant"
PERIOD_KINDS: tuple[str, ...] = (DURATION, INSTANT)

SCALES: tuple[str, ...] = ("units", "thousands", "millions", "billions")

# The one `period_label` answer that is not a phrase the passage prints.
#
# **Added 2026-08-02 because the enum could not express a true state.** Constraining
# `period_label` to the printed phrases removed every period the passage does not name, and
# thereby removed the model's only way to say "none of these is this figure's period". Measured
# on `q42021formxex992sharehol.htm#p10`: `period_phrases()` yields exactly
# `['December 31, 2021', '4Q21', '4Q20']`, the letter reports a full year as well as a quarter,
# and the model labelled "For the year, we delivered Contribution Profit of $525 million" as
# `4Q21` — producing two values for one deterministic observation id and four
# `DUPLICATE_OBSERVATION_CONFLICT` errors. Rule 6 already told the model to abstain with
# `MISSING_PERIOD` in that case, but a grammar that only offers wrong answers gets one.
#
# This is a correctness fix and not benchmark tuning: the schema could not express a state the
# corpus actually has. `response_mapping._resolve_period` maps it to `MISSING_PERIOD`, so
# choosing it refuses the claim rather than dating it.
PERIOD_NOT_PRINTED = "(none of the phrases above states this figure's period)"


@dataclass(frozen=True)
class AmbiguousSurface:
    """A surface form `aliases.yaml` declares ambiguous, and the note saying why.

    In the prompt because the ontology's ambiguity notes are the second half of what STAGE_10
    §3 asks the candidate list to carry, and the half that changes answers. Measured
    2026-08-01: with the `distinct_from` siblings alone, the 9B model read "Delivered gross
    profit of $96 million" as `adjusted_gross_profit`. The note that separates them — *"Only
    the qualifier 'Adjusted' separates them, and it is sometimes only in the row label"* — is
    declared on the alias, not on either concept, so a per-concept rendering cannot reach it.

    `concept_ids` is already narrowed to the passage's candidate scope by
    `ambiguous_surfaces_for`, so the wordings warned about and the wordings enforced are the
    same list.
    """

    surface: str
    concept_ids: tuple[str, ...]
    note: str


def ambiguous_surfaces_for(ontology, metric_ids: frozenset[str]) -> tuple[AmbiguousSurface, ...]:
    """The declared-ambiguous wordings that could denote something in a passage's scope.

    Read off `aliases.yaml` through the loaded definitions rather than re-derived, so the note
    the model is shown is the note the vocabulary actually records. Filtered to the candidate
    set because a warning about a wording no candidate here could carry is noise that pushes
    the passage further down an 8,192-token window — and, since `response_mapping` enforces
    exactly this list, because a wording whose rivals are all out of scope was never a choice
    the model could get wrong.
    """
    surfaces = []
    for entry in getattr(getattr(ontology, "definitions", None), "aliases", ()) or ():
        if not getattr(entry, "ambiguous", False):
            continue
        shared = tuple(sorted(set(entry.concept_ids) & set(metric_ids)))
        if len(shared) < 2:
            continue
        surfaces.append(AmbiguousSurface(
            surface=str(entry.alias),
            concept_ids=shared,
            note=str(getattr(entry, "note", "") or "")))
    return tuple(sorted(surfaces, key=lambda surface: surface.surface))


@dataclass(frozen=True)
class NarrativeIssue:
    """A structured non-claim, carrying enough to reproduce the decision.

    `raw_finding` is the model's own object, untouched. STAGE_10 §5 requires it: step 11
    classifies these and step 13 writes them to `rejected_claims.jsonl`, and a rejection
    without the thing that was rejected cannot be reviewed.
    """

    code: str
    detail: str
    passage_id: str
    metric_ids: tuple[str, ...] = ()
    quoted_span: str | None = None
    raw_finding: dict[str, Any] | None = None
    # True when the model proposed a claim and this lane refused it; False when the model
    # itself declined. Both are recorded, and step 11 scores them differently.
    rejected_claim: bool = False


@dataclass
class NarrativeExtraction:
    """Everything one passage produced, claims and explained silences alike.

    The same shape `TableExtraction` has, for the same reason: the lane's own result is
    richer than `LaneResult` and the richer one is what a report needs, while `LaneResult` is
    what the protocol promises.
    """

    passage_id: str
    claims: list[LaneClaim] = field(default_factory=list)
    issues: list[NarrativeIssue] = field(default_factory=list)
    # The digest of the request that produced this, or None where no request was issued.
    #
    # Deterministic — it is `answer_store.request_identity` over (prompt, schema, model,
    # sampling parameters) — so it is a property of the run and not a measurement of it, and it
    # may enter a byte-identical artifact. Recorded because step 11 compares two scopes and had
    # been inferring "the two runs are the same run" from the metric concepts alone, which is a
    # weaker statement than the digest makes *(added 2026-08-02, review)*.
    request_sha256: str | None = None

    @property
    def rejected_claims(self) -> tuple[NarrativeIssue, ...]:
        return tuple(issue for issue in self.issues if issue.rejected_claim)

    def counts_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return dict(sorted(counts.items()))

    def as_lane_result(self) -> LaneResult:
        """The protocol's view, with the one distinction §5 scores kept intact.

        `LaneAbstention.rejected_claim` carries it. Flattening both kinds into one abstention
        made a `QUOTED_SPAN_NOT_IN_PASSAGE` — the model proposed a claim and this lane refused
        it as fabricated evidence — indistinguishable through `ClaimLane` from the model
        declining to answer *(corrected after review 2026-08-02)*. Step 11 scores those
        differently: the first is a lane catching a model, the second is a model behaving.
        """
        return LaneResult(
            passage_id=self.passage_id,
            lane=LANE_NAME,
            claims=tuple(self.claims),
            abstentions=tuple(
                LaneAbstention(
                    reason=issue.code,
                    detail=issue.detail,
                    passage_id=issue.passage_id,
                    candidate_metric_ids=issue.metric_ids,
                    row_label=None,
                    rejected_claim=issue.rejected_claim,
                )
                for issue in self.issues
            ),
        )


def response_schema(
    metric_ids: tuple[str, ...], units: tuple[str, ...], period_phrases: tuple[str, ...] = ()
) -> dict:
    """The JSON Schema the provider constrains generation with.

    Two arrays rather than one array of tagged unions. A single `findings` array would need
    every field of both shapes present on every element, so the model would have to fill a
    period into an abstention and a reason into a claim — and a grammar that forces a field
    to exist is a grammar that gets it invented.

    Every property is required inside its own object. llama.cpp builds a GBNF grammar from
    this, and an optional property is one the model silently omits on the hard cases; an empty
    string is a statement that a field does not apply, which this lane can check, while a
    missing key is a hole it would have to guess about.

    `metric_ids` is the injected scope's answer, verbatim and including concepts this lane
    will refuse — a metric deferred to an XBRL lane stays on the menu so that naming it is a
    recorded rejection rather than an impossibility. Constraining the enum to only what may be
    emitted would hide the model's mistakes rather than measure them.

    **`period_phrases` is the enum that removed a whole class of wrong answer.** It holds the
    phrases in this passage that `core.periods` can resolve without a model, so the answer
    states no dates at all — it points at a printed phrase, and
    `core.periods.resolve_period_phrase` decides what that phrase means. Asking for ISO dates
    instead produced six claims dated a year early on a passage naming "3Q23" three times
    *(measured 2026-08-01)*, and no schema that lets a model type a year can prevent that.
    Three fields left the object as a side effect, which is roughly a fifth of the output
    budget on a passage reporting ten figures.

    **The class removed is "a period the passage does not print", and that is not every wrong
    period** *(corrected after review 2026-08-02, where this said "unrepresentable")*. A
    comparative paragraph prints its prior-period phrase, so the enum offers it and choosing it
    passes every check; `response_mapping._resolve_period` states the residual and
    V1_CLAIM_EXTRACTION §8a.12 makes it a dimension step 11 scores.

    **The enum always carries `PERIOD_NOT_PRINTED` as its last member** *(added 2026-08-02)*.
    Narrowing the enum to printed phrases left the model no way to say that a printed figure's
    period is not among them, which is a state the corpus has: a shareholder letter reporting
    both 4Q21 and the full year prints no phrase resolving to FY2021. Step 11 measured the
    consequence — the model attached `4Q21` to the full-year figures and two values collided
    under one observation id. The member is mapped to `MISSING_PERIOD`, so choosing it refuses
    the claim rather than dating it.

    **A passage printing no resolvable phrase degenerates this enum to `[PERIOD_NOT_PRINTED]`**,
    so every claim on it is refused with `MISSING_PERIOD` by construction. That is the intended
    behaviour — a figure whose period cannot be read is not extractable — but it means an empty
    claim list on such a passage is a structural result and not, on its own, evidence about the
    model.
    """
    claim_item = {
        "type": "object",
        # `evidence_sentence` is first, and that is a decision about generation order rather
        # than about readability. llama.cpp emits properties in schema order, so putting the
        # quotation first makes the model copy the filing's own sentence before it names a
        # metric or a number — quote, then answer. Generated last, beside `value_text`, it
        # collapsed into a copy of the figure: "to 5,988", then bare "5,988" *(measured
        # 2026-08-01, six spans on `open-20210930.htm#p142`, across two prompt revisions that
        # asked for whole sentences in words and did not get them)*.
        "properties": {
            "evidence_sentence": {"type": "string"},
            "metric_id": {"type": "string", "enum": list(metric_ids)},
            "statement_type": {"type": "string", "enum": list(STATEMENT_TYPES)},
            "subject": {"type": "string", "enum": list(SUBJECTS)},
            "value_text": {"type": "string"},
            "value": {"type": "number"},
            "scale": {"type": "string", "enum": list(SCALES)},
            "unit": {"type": "string", "enum": list(units)},
            "period_kind": {"type": "string", "enum": list(PERIOD_KINDS)},
            "period_label": {"type": "string",
                             "enum": [*period_phrases, PERIOD_NOT_PRINTED]},
            "population_text": {"type": "string"},
        },
        "required": [
            "evidence_sentence", "metric_id", "statement_type", "subject", "value_text",
            "value", "scale", "unit", "period_kind", "period_label", "population_text",
        ],
        "additionalProperties": False,
    }
    abstention_item = {
        "type": "object",
        "properties": {
            "evidence_sentence": {"type": "string"},
            "reason": {"type": "string", "enum": list(MODEL_ABSTENTION_REASONS)},
            "metric_ids": {"type": "array", "items": {
                "type": "string", "enum": list(metric_ids)}},
            "detail": {"type": "string"},
        },
        "required": ["evidence_sentence", "reason", "metric_ids", "detail"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "claims": {"type": "array", "items": claim_item},
            "abstentions": {"type": "array", "items": abstention_item},
        },
        "required": ["claims", "abstentions"],
        "additionalProperties": False,
    }
