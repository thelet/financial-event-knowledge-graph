"""Every value the story agent passes between its stages, frozen at S0.

Responsibility: the vocabulary. What a detector emits, what the packager assembles, what the
planner and the writer are allowed to say, and what the verifier decides about it. Nothing
here retrieves, generates, verifies or persists — those are stages, and this module must stay
constructible with no database, no model server and no filesystem, so that a package or a
verification report can be rebuilt from a stored artifact and asserted against.

**Why almost everything is a frozen pydantic model rather than a frozen dataclass.** Every
type below is written to `data/story_runs/<id>/` as JSON and read back (§14) — candidates,
packages, plans, drafts, verifications. Pydantic declares both directions once;
`@dataclass(frozen=True)` would need a hand-written `from_dict` per type, and there are
thirty of them. Several also carry an invariant that must hold at construction and not merely
in a docstring — `metric_ids` sorted (§6.4), a citation being *either* a passage or an
evidence source (§13.7.2), a counterpoint being grounded in something (§11), a rejection
carrying a blocking finding (§13.17). That is exactly the trade `graph/core/models.py` and
`graph/core/verification_report.py` already make, and this module follows them.

`StoryRunManifest` is the one exception and lives in `manifest.py` as a frozen dataclass,
mirroring `GraphRunManifest`: it is written once, never parsed back by this code, and its
`render()` is the artifact contract.

**`passed` is never stored.** `VerifiedDraft.passed` and `CheckResult.outcome` are derived
from the findings they hold, so a report cannot claim success while carrying a refusal — the
discipline `graph/core/verification_report.py:217` states for the graph layer.

**What `None` means on a row read from the graph: absent, and only absent.** Neo4j holds no
null property — `SET n += {k: null}` *removes* the key, which is why the projection stopped
writing 53,951 null-valued properties at version 1.1.0 (`graph/core/models.py:22-27`). So on
`PackagedFact`, `PackagedEvent`, `PackagedDocument` and the rest, `None` is not the ambiguous
"present but null"; it is "the node carried no such property", and no other reading is
producible. Recorded here rather than modelled with a sentinel, because a sentinel for a state
the database cannot hold would be a distinction no code could ever populate. The one place the
difference would matter — an absent `occurred_on` on an event — is already a §13.8 refusal
(`date_not_in_package`) rather than a value to interpret.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal, Mapping, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

#: The evidence package's own version (§10). Bumped when a section is added, removed or
#: re-shaped; a digest input to `package_id`, so two shapes can never share an id.
#:
#: **1.1.0 at S1 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS.** Three sections were added
#: (`semantic_facts`, `identity_facts`, `comparability_facts`) and four row types re-shaped
#: (`PackagedPassage` gained a role, a match basis and a quality pair; `PackagedFact` a kind and
#: three corroboration lists; `PackagedMetric` a description). That is exactly the event this
#: constant exists for, and the consequence the plan's §3 predicted follows from it: `package_id`
#: moves, `package_id` is rendered into the planner and writer prompts, so the recorded
#: generation store misses and the committed replay demo raises `MissingGenerationError` until
#: S7 re-records it once. Not bumping it would have kept the demo green by letting two package
#: shapes share one id, which is the single thing this field forbids.
#:
#: **1.2.0 at S2/S3.** One more section — `diagnostic_passages`, where a row that
#: `find_counter_evidence` produced but that does not qualify as counter-evidence now lives —
#: and one more field on `PackagedPassage`, `diagnostic_codes`. The same event, the same
#: consequence: `package_id` moves again, and S7's single re-record covers both bumps because it
#: happens after every schema change has landed.
#:
#: **1.3.0 at S3 of TABLE_CELL_CITATIONS.** `PackagedFact` gained `cell` — the grid coordinates
#: the story layer had been discarding — and `evidence_handle`, the deterministic name a citation
#: now binds to instead of retyping a four-character quote. Both are inside
#: `package_content_digest`, and the handle is the *model's* vocabulary, so a change to its
#: format must re-key the package exactly as a new section does; that is the whole reason the
#: handle is a stored field rather than a property derived on read.
PACKAGE_VERSION = "1.3.0"

#: The canonicalisation policy every candidate and every canonical series is computed under
#: (§6.1). A digest input to `candidate_id`, so a policy change mints new candidates rather
#: than silently mutating existing ones.
POLICY_VERSION = "canon-policy:1.0.0"


class StoryModel(BaseModel):
    """Frozen, and no field that was not declared.

    `extra="forbid"` is load-bearing rather than tidy: §6.4 says a candidate carries no prose,
    and the way to make that true is for `StoryCandidate(thesis_hypothesis=...)` to raise.
    Declared once here instead of thirty times, which is the only difference from
    `graph/core/models.py`'s per-class `model_config`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------------------
# Closed vocabularies. Every one of these is an enum rather than a string because §15.3's
# portable schema subset can express `enum` and cannot express `pattern` — a constraint the
# model runtime cannot enforce is not a constraint.
# ---------------------------------------------------------------------------------------


class Audience(str, Enum):
    """Who a candidate is for (§6.4). Internal candidates are data-quality findings —
    `fact_conflict`, `coverage_gap`, `formula_closure_break` — and §6.10 forbids them from
    outranking an external candidate for post generation."""

    EXTERNAL = "external"
    INTERNAL = "internal"


class StatementClass(str, Enum):
    """What a key point claims (§11), which decides which §13 rule judges it."""

    REPORTED = "reported"
    CALCULATED = "calculated"
    EXPLANATORY = "explanatory"


class SentenceKind(str, Enum):
    """What a draft sentence is (§12). `connective` carries no fact and may carry no numeral."""

    REPORTED = "reported"
    CALCULATED = "calculated"
    EXPLANATORY = "explanatory"
    CONNECTIVE = "connective"


class CausalLanguage(str, Enum):
    """§11. `reported_only` is set by code before the planner is called, never chosen by the
    model, and only when a cited span carries a causal marker (§13.10)."""

    FORBIDDEN = "forbidden"
    REPORTED_ONLY = "reported_only"


class UnusableReason(str, Enum):
    """Why the plan declined a package item (§11 point 2).

    An enum and not a free string: in a regime that cannot enforce `pattern`, a free-text
    reason is a box to be filled rather than a decision to be made.
    """

    SUPERSEDED_BY_LATER_FILING = "superseded_by_later_filing"
    DIFFERENT_PERIOD_SHAPE = "different_period_shape"
    DIFFERENT_POPULATION = "different_population"
    IMMATERIAL_AT_STATED_PRECISION = "immaterial_at_stated_precision"
    OUTSIDE_THESIS_SCOPE = "outside_thesis_scope"


class PassageUnusableReason(str, Enum):
    """Why a passage's own *content* cannot carry evidence, decided by code at packaging time.

    **Separate from `UnusableReason`, and the separation is the point.** `UnusableReason`'s five
    members — superseded, different period shape, different population, immaterial, outside
    thesis scope — are editorial dispositions the *planner model* declares about an item it
    chose not to use (§11 point 2), and they reach the model as an enum in §15.3's portable
    schema. The four below are determinations *code* makes about a passage before any model
    sees it: a pipe-only table row, a passage with no proposition about the candidate, an
    extraction that came back corrupted, a fragment too short to state anything. Folding them
    into one vocabulary would do two wrong things at once — it would let the planner claim
    `corrupted_extraction` as an editorial judgement it has no way to establish, and it would
    widen the plan schema the model is constrained by, which is a change to what the model may
    assert rather than to what the packager may record.

    Populated at S2 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS. Nothing sets it today, and
    `PackagedPassage.quality_status` says so rather than defaulting to a clean bill of health.
    """

    EMPTY_OR_STRUCTURAL_ONLY = "empty_or_structural_only"
    NO_RELEVANT_PROPOSITION = "no_relevant_proposition"
    CORRUPTED_EXTRACTION = "corrupted_extraction"
    INSUFFICIENT_CONTENT = "insufficient_content"


class PassageQuality(str, Enum):
    """Whether a passage's content has been assessed, and what the assessment found.

    Three members and not two, because *"nobody has looked"* and *"looked and it is fine"* are
    different facts and only one of them licenses using the passage as evidence. `UNASSESSED` is
    the default for exactly that reason: S1 adds the field, S2 computes it, and until S2 lands
    every passage in every package honestly reports that no quality assessment ran.
    """

    UNASSESSED = "unassessed"
    USABLE = "usable"
    UNUSABLE = "unusable"


class EvidenceRole(str, Enum):
    """What a packaged passage *is to this story* — EVIDENCE_ROLES_AND_SEMANTIC_FACTS §4 S1.

    The defect this vocabulary exists to make expressible: today a passage's role is its §10
    section, and `counter_evidence[]` is joined at document grain, so the best-corroborated fact
    in the corpus arrives labelled as contradicted by a diagnostic about a different concept in
    the same filing (§1). A section is a place; a role is a claim about the evidence, and only
    the second can be wrong in a way a check can catch.

    * `PRIMARY_SUPPORT` — the passage a used fact was read from.
    * `CORROBORATING_SUPPORT` — a second, concordant source for a fact already carried. S3.
    * `CONTEXT` — surrounding or explanatory disclosure that supports nothing by itself.
    * `COUNTER_EVIDENCE` — deterministic evidence that challenges *this* story. S2 states the
      qualifying bases; a shared document and a shared keyword are not among them.
    * `WARNING_ONLY` — an extraction or data-quality diagnostic. It qualifies a fact and is not
      a counterpoint, which is the distinction §1.1 measured being lost.
    * `UNUSABLE` — the content cannot carry evidence at all; `PassageUnusableReason` says why.
    """

    PRIMARY_SUPPORT = "primary_support"
    CORROBORATING_SUPPORT = "corroborating_support"
    CONTEXT = "context"
    COUNTER_EVIDENCE = "counter_evidence"
    WARNING_ONLY = "warning_only"
    UNUSABLE = "unusable"


class FactKind(str, Enum):
    """What kind of thing a packaged fact is — EVIDENCE_ROLES_AND_SEMANTIC_FACTS §4 S1.

    `OBSERVED` and `DERIVED` are quantities: one read from a filing, one computed from readings.
    The other three are the plan's §4 S4 correction — the ontology *defines* every metric the
    post names, and carrying that definition as metadata beside the facts rather than as a fact
    is why a post can state a number whose declared meaning never reached the model. They are
    facts, they are authoritative, and they are not editable.
    """

    OBSERVED = "observed"
    DERIVED = "derived"
    SEMANTIC = "semantic"
    IDENTITY = "identity"
    COMPARABILITY = "comparability"


class Severity(str, Enum):
    """§13.17's gate. `REFUSE` rejects the whole draft; `WARN` must be acknowledged in the
    accepted artifact; `ANNOTATE` must be rendered beside the claim; `ADVISORY` is a model
    finding that did not meet the bar to block."""

    REFUSE = "REFUSE"
    WARN = "WARN"
    ANNOTATE = "ANNOTATE"
    ADVISORY = "ADVISORY"


class WarningKind(str, Enum):
    """Which audience a package warning is for (§10.1), and therefore what may demand it.

    §10.1's `warnings[]` serves two audiences, and the two were conflated until the demo ran.

    * `CLAIM_QUALIFYING` — a property of the *evidence* that changes what a sentence resting on
      it means: a minority reading, a population wording, a declared metric ambiguity, a single
      corroborating document. A post that states the claim and drops the qualifier has said
      something the evidence does not support, so §13's disclosure check refuses it.
    * `BUILD_PROVENANCE` — a property of *how the package was assembled*: a section the token
      budget trimmed, an `:EvidenceSource` lane that does not exist yet. It qualifies no claim,
      and an investor post that said *"this package's token budget was trimmed"* would be
      reporting on its own plumbing. It travels on the package to the evidence panel and the
      manifest, and it may not become a `required_warning`.

    The default is `CLAIM_QUALIFYING` wherever this is not stated, and the direction is
    deliberate: provenance misfiled as a qualifier refuses loudly, while a qualifier misfiled as
    provenance would go unsaid in silence. The codes' own kinds are declared beside their
    severities in `story/stages/packaging/warning_codes.py`.
    """

    CLAIM_QUALIFYING = "claim_qualifying"
    BUILD_PROVENANCE = "build_provenance"


class WarningCategory(str, Enum):
    """What *kind of thing* a package warning is about — EVIDENCE_ROLES_AND_SEMANTIC_FACTS §4 S5.

    **A refinement of `WarningKind`, not a third axis beside it.** Each category belongs to
    exactly one kind (`warning_codes.KIND_OF_CATEGORY`), so `KIND_OF` is *derived* from the
    category table rather than declared a second time. Two independent tables over one code set
    can disagree; one table that induces the other cannot. The five names are §4 S5's own, and
    the split they add is the one the evidence panel needs: `WarningKind` answers *"must the
    post say this?"* and stops there, so a capability the retrieval layer does not have yet, a
    read that came back short and a diagnostic the extractor recorded all render as one
    undifferentiated *"warning"*.

    * `SUBSTANTIVE_COUNTER_EVIDENCE` — the package carries something that disputes this story,
      and the code says on what basis it was matched. §1's defect is a row in this family that
      should never have been in it; §4 S2 is what keeps it out, and the category is what lets a
      reader see the difference from the four below.
    * `FACT_QUALITY_WARNING` — the fact is intact and its evidential standing is qualified: one
      corroborating document, an unpreferred lane, a declared ambiguity, a population wording.
    * `EXTRACTION_ISSUE` — the pipeline that produced the row hit a defect: an unresolved
      entity, an undated event, a canonicalisation warning, a slot that holds no value.
    * `RETRIEVAL_WARNING` — what reached the model is smaller than what the corpus holds,
      whether the bound was a query limit, a capped search pool, a section cap or a token
      budget. It qualifies no claim and §13.14 already refuses outright the absence and
      uniqueness claims a partial read would make false.
    * `CAPABILITY_LIMITATION` — V1 cannot do this at all: no §9 tool reads `:Entity`, no tool
      returns a relationship, no `:EvidenceSource` node exists. **Not a fact about the subject
      and not a defect in the evidence**, so it must not read as one and must not reduce a
      post's verification status. It is still carried, still ordered by severity and still
      rendered — relabelled, never hidden.
    * `PACKAGE_COMPOSITION` — how the builder assembled *this* package out of the rows the graph
      gave it: which concordant readings it folded into one fact, which reading it kept as the
      representative. **Sixth, added at S6, and it exists because the five above had no honest
      home for `concordant_readings_collapsed`** — see `warning_codes.CATEGORY_OF` for the
      measurement that forced it. Nothing came out smaller than the corpus, so it is not a
      `RETRIEVAL_WARNING`; V1 can do this and did, so it is not a `CAPABILITY_LIMITATION`; and
      it qualifies no claim, because *"six filings agreed"* is the reason to trust a number
      rather than a caveat about reading it.
    """

    SUBSTANTIVE_COUNTER_EVIDENCE = "substantive_counter_evidence"
    FACT_QUALITY_WARNING = "fact_quality_warning"
    EXTRACTION_ISSUE = "extraction_issue"
    RETRIEVAL_WARNING = "retrieval_warning"
    CAPABILITY_LIMITATION = "capability_limitation"
    PACKAGE_COMPOSITION = "package_composition"


class Remedy(str, Enum):
    """What would fix a finding (§13.17), so a rejection is dispatchable rather than a search.

    `REBIND_TO_DISTINGUISHING_COLUMN` is **not** in §13.17's list; §13.7.1 adds it in the same
    plan and the two sections disagree. Both are carried here — the narrower remedy is the
    only actionable answer to `column_label_ambiguous_in_passage`, which §13.7.1 measures as
    firing on 61.3% of table observations.
    """

    REBIND_TO_FACT = "REBIND_TO_FACT"
    REBIND_TO_DISTINGUISHING_COLUMN = "REBIND_TO_DISTINGUISHING_COLUMN"
    RESTATE_AS_CALCULATION = "RESTATE_AS_CALCULATION"
    ADD_PERCENTAGE_POINT_QUALIFIER = "ADD_PERCENTAGE_POINT_QUALIFIER"
    NARROW_METRIC_SURFACE = "NARROW_METRIC_SURFACE"
    ADD_PERIOD_QUALIFIER = "ADD_PERIOD_QUALIFIER"
    ADD_CONFLICT_DISCLOSURE = "ADD_CONFLICT_DISCLOSURE"
    ADD_ATTRIBUTION_FRAME = "ADD_ATTRIBUTION_FRAME"
    REMOVE_CAUSAL_CONSTRUCTION = "REMOVE_CAUSAL_CONSTRUCTION"
    DROP_SENTENCE = "DROP_SENTENCE"
    REBUILD_PACKAGE = "REBUILD_PACKAGE"


class CheckOutcome(str, Enum):
    """A check's answer, derived from what it examined and what it found. `NOT_APPLICABLE` is
    why §13.17 requires `examined` as a denominator: a draft with no numeric sentences must
    not be able to report `numbers: PASS`."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RetrievalOutcome(str, Enum):
    """The five answers §9's tools may give. `UNAVAILABLE` is the honest answer for evidence
    kinds no lane emits yet (§13.7.2) — an unimplemented rule that silently passes is worse
    than one that refuses."""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    REFUSED = "REFUSED"


# ---------------------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------------------


def _require_sorted_unique(values: Sequence[str], field: str) -> None:
    """§6.4 declares these tuples sorted, and this is what makes the declaration true.

    Refused rather than silently sorted. `candidate_id` sorts its own digest inputs, so a
    reordered field cannot change an id — but a candidate whose stored `metric_ids` differ in
    order from another candidate's is two byte sequences in `candidates.jsonl` describing one
    thing, and the JSONL is compared byte for byte.
    """
    if list(values) != sorted(values):
        raise ValueError(f"{field} must be sorted; got {list(values)}")
    if len(set(values)) != len(values):
        raise ValueError(f"{field} contains a repeat: {list(values)}")


def _require_span(start: int, end: int, field: str) -> None:
    if start < 0:
        raise ValueError(f"{field}: char_start {start} is negative")
    if end <= start:
        raise ValueError(f"{field}: char_end {end} does not follow char_start {start}")


def canonical_json(payload: Any) -> str:
    """The repository's compact encoding, restated for the two artifacts that are hashed.

    `sort_keys=True, ensure_ascii=False, separators=(",", ":")` — identical to
    `graph/stages/projection/export.py:194-205`, which is what §10.3 names. Two packages
    built from one graph run must produce one string, so key order and whitespace are part of
    the contract rather than a formatting preference.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------------------
# Discovery — §6
# ---------------------------------------------------------------------------------------


class EvidenceRequest(StoryModel):
    """What §10 must fetch for one candidate — the detector's whole influence over the package.

    A detector states which ids it anchored on and whether the two *searched* sections are
    wanted; it cannot state how many rows, which passages, or in what order. That is §10.2's
    business, and keeping it there is what stops a detector widening its own evidence.
    """

    metric_ids: tuple[str, ...] = ()
    period_keys: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    #: §10's `counter_evidence[]`. Defaults on: a candidate that suppressed its own
    #: counter-evidence would be a detector deciding what the writer is allowed to weigh.
    want_counter_evidence: bool = True
    #: §10's `explanatory_passages[]` — fulltext hits. Off by default because a detector with
    #: no explanatory question to ask should not pay for a search whose rows enter the budget.
    want_explanatory_search: bool = False

    @model_validator(mode="after")
    def _ids_are_sorted(self) -> "EvidenceRequest":
        for name in ("metric_ids", "period_keys", "observation_ids", "event_ids"):
            _require_sorted_unique(getattr(self, name), name)
        return self


class StoryCandidate(StoryModel):
    """One thing a detector noticed, stated structurally. §6.4's field list exactly.

    **Carries no prose, deliberately.** There is no `thesis_hypothesis`: that field is the
    seam through which a detector's guess becomes a post's claim. The detector asserts what
    moved, by how much, and which observations back it; the thesis is §11's output and is
    derived from the *package*. `extra="forbid"` is what enforces the absence.

    **Carries no score, deliberately.** `materiality`, `novelty`, `evidence_quality` and the
    two penalties are `CandidateScore`'s, computed over the whole candidate set (§6.10). A
    candidate holding its own score would let a detector rank itself.
    """

    candidate_id: str
    detector_id: str
    detector_version: str
    policy_version: str
    graph_run_id: str
    subject_entity_id: str
    story_type: str
    metric_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    anchor_period_keys: tuple[str, ...] = ()
    #: The digest inputs (§6.11), sorted so input ordering cannot change the candidate's id.
    anchor_observation_ids: tuple[str, ...] = ()
    #: Numbers and flags a detector computed. Free-keyed because each detector's signal set is
    #: its own; never free *text*, and never read by the writer.
    signals: Mapping[str, bool | int | float | str] = {}
    warnings: tuple[str, ...] = ()
    evidence_request: EvidenceRequest = EvidenceRequest()
    audience: Audience = Audience.EXTERNAL

    @model_validator(mode="after")
    def _ids_are_sorted(self) -> "StoryCandidate":
        for name in ("metric_ids", "event_ids", "anchor_observation_ids"):
            _require_sorted_unique(getattr(self, name), name)
        return self


class CandidateScore(StoryModel):
    """§6.10's ranking output, kept apart from the candidate it scores.

    `components` is the whole score broken out by term, because §6.10's weights are a stated
    starting point rather than a derivation: a total nobody can decompose is a number nobody
    can argue with, and the weights are meant to be argued with.
    """

    candidate_id: str
    total: float
    components: Mapping[str, float]
    #: 1-based, over the deduplicated set. Stable under §6.10's tie-break.
    rank: int
    #: The id of the group this candidate was collapsed into, or its own id when it survived
    #: deduplication alone. Recorded so a reader can see what a rank suppressed.
    dedup_group: str


# ---------------------------------------------------------------------------------------
# Citation handles — §13.7
#
# **Moved above the package rows at S1 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS, and only moved.**
# They lived beside `DraftSentence` while a draft sentence was the only thing that cited; the
# ontology facts §4 S4 adds cite too — *"scope rules where source-backed, citation handles"* —
# so the handle is now shared vocabulary and has to be defined before its first use. Nothing
# about either type changed.
# ---------------------------------------------------------------------------------------


class PassageCitation(StoryModel):
    """A citation into filed text: Rules A and B (§13.7) apply and the span must exist."""

    kind: Literal["passage"] = "passage"
    passage_id: str
    document_id: str
    char_start: int
    char_end: int

    @model_validator(mode="after")
    def _span_is_usable(self) -> "PassageCitation":
        _require_span(self.char_start, self.char_end, f"citation {self.passage_id}")
        return self


class EvidenceSourceCitation(StoryModel):
    """A citation to evidence that names no filed passage — Rule C (§13.7.2).

    The chain ends at the `:EvidenceSource`; there is no span to contain, so support is
    coordinate reconstruction or input recursion. **Refused in V1** with
    `evidence_kind_not_supported_in_v1` until a lane emits one, which is deliberate: an
    unimplemented rule that silently passes is worse than one that refuses.
    """

    kind: Literal["evidence_source"] = "evidence_source"
    evidence_source_id: str
    evidence_kind: str
    source_url: str | None = None


#: A citation is *either* a passage citation or an evidence-source citation. Modelled as a
#: discriminated union rather than one type with two optional halves, so "neither" is not a
#: value that can be constructed and then have to be caught downstream. `CITATION_ADAPTER` is
#: how a caller validates a raw mapping into one.
CitationHandle = Annotated[
    Union[PassageCitation, EvidenceSourceCitation], Field(discriminator="kind")]
CITATION_ADAPTER: TypeAdapter[Any] = TypeAdapter(CitationHandle)


# ---------------------------------------------------------------------------------------
# Evidence package rows — §10
# ---------------------------------------------------------------------------------------


class PackagedSubject(StoryModel):
    """§10's `subject`. `resolved` is false for an entity the corpus names and never keys —
    §13.11 refuses a draft that renders one as a name."""

    entity_id: str
    entity_text: str
    resolved: bool
    labels: tuple[str, ...] = ()


class TableCellRef(StoryModel):
    """Where a table-backed fact's value sits in its passage's flattened grid, and where the
    period header standing over it sits (TABLE_CELL_CITATIONS §1.3).

    **Nested rather than four loose `int | None` fields on `PackagedFact`, because the
    coordinates are all-or-nothing and a partial one is the dangerous state.** Verified live
    2026-08-13: 2,690 of 2,690 table-backed observations carry every index, the 14 narrative
    ones carry none, and **0 carry some** — `tests/story/test_story_retrieval.py`'s census
    holds that over the whole graph. Four optional ints would make fifteen partial combinations
    constructible, and a fact holding a `row_index` with no `value_column_index` looks citable
    and resolves to the wrong cell. Here there are two states: a cell, or no cell.

    **`period_header_column_index` is carried and is not `value_column_index`.** `$` signs and
    blank spacer columns push a period header out of the column its value sits in, and the two
    differ on **2,125 of the 2,690** table-backed rows *(verified live 2026-08-13)* — so a
    verifier reading the header at the value's own column would name the wrong period four
    times in five, and would get a well-formed `$` or empty string back rather than an error.
    `story.core.table_cells.resolve_header` takes the header column as an argument for that
    reason, and this is where the argument comes from.

    **`metric_label_row_index` is deliberately not carried.** It equals `row_index` on all
    2,690 rows, and `resolve_cell` already returns the row label from column 0 of the value's
    own row. A fifth field holding a copy of the first is a second place for one truth to drift.
    """

    row_index: int
    value_column_index: int
    period_header_row_index: int
    period_header_column_index: int

    @model_validator(mode="after")
    def _indices_are_positions(self) -> "TableCellRef":
        """No negative index, for `table_cells.resolve_cell`'s reason.

        Python would resolve `-1` by wrapping to the end of the table and returning a plausible
        wrong cell in silence; the resolver refuses it with `CellOutOfBounds` and this refuses
        it a step earlier, at the point a package is built.
        """
        for name in ("row_index", "value_column_index", "period_header_row_index",
                     "period_header_column_index"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(
                    f"{name}={value} is not a grid position; a negative index resolves by "
                    "wrapping to the end of the table, which is a wrong cell and not an error")
        return self


#: Every evidence handle opens with this. A citation carries a fact id and a handle, and the
#: prefix is what lets a reader — and a rejection message — tell one from the other at a glance
#: without parsing either.
EVIDENCE_HANDLE_PREFIX = "ev"


def _evidence_handle(
    *, passage_id: Any, metric_id: Any, period_key: Any, cell: Any
) -> str | None:
    """The handle for a fact with this passage, this slot and this cell (§3.1).

        ev:<passage_id>:r<row_index>c<value_column_index>       table-backed
        ev:<passage_id>:span:<metric_id>:<period_key>           narrative
        None                                                    no filed passage (§13.7.2)

    **The plan's narrative form, `ev:<passage_id>:span`, does not work, and the correction is
    measured rather than defensive.** §3.1 wrote the span handle as passage-only on the strength
    of §1.4 — which measured uniqueness for *table cells* and never for spans. Live 2026-08-13,
    the 14 narrative observations sit in **6** distinct passages: one carries 6 of them, one 3,
    one 2. So that form names two to six facts at once, and it does so in a package that exists
    today: `cand:cross-metric-divergence:adjusted-gross-profit-contribution-profit:opendoor:
    2021Q4:727148801299` carries `adjusted_gross_profit` and `contribution_profit` for 2021Q4,
    both read out of `…q42021formxex992sharehol.htm#p10`, quoting two different sentences at
    character 996 and character 1,359.

    Adding the *span offsets* instead would not have fixed it: `adjusted_gross_profit` and
    `adjusted_gross_margin` 2021Q4 quote the **same 66-character sentence at the same offset**
    in that passage. One sentence really does evidence two facts, so no structural coordinate
    can separate them and the discriminator has to come from the fact.

    **What it names is the fact's slot, `(metric_id, period_key)`, and both halves earn their
    place.** The metric separates the sentence-sharing pair above. The period separates two
    readings of one metric out of one passage — which the corpus does not hold in prose today,
    but which `tests/story/test_story_deterministic_verifier.py` builds as a matter of course
    for table facts, and §6.1 guarantees exactly one canonical fact per slot, so the slot is the
    finest grain that is guaranteed unique rather than merely observed to be.

    The table form takes neither, and the asymmetry is a measurement and not an oversight: §1.4
    grouped every table-backed observation by `(passage_id, row_index, value_column_index)` and
    found 2,690 distinct cells, **0** mapping to two periods and **0** to two metrics. A cell
    identifies its fact; a passage does not.
    """
    if not isinstance(passage_id, str) or not passage_id:
        return None
    indices = _cell_indices(cell)
    if indices is not None:
        return f"{EVIDENCE_HANDLE_PREFIX}:{passage_id}:r{indices[0]}c{indices[1]}"
    if not (isinstance(metric_id, str) and metric_id
            and isinstance(period_key, str) and period_key):
        return None
    return f"{EVIDENCE_HANDLE_PREFIX}:{passage_id}:span:{metric_id}:{period_key}"


def _cell_indices(cell: Any) -> tuple[int, int] | None:
    """`(row_index, value_column_index)` off a `TableCellRef` or the mapping one is built from.

    Both shapes because the handle is minted *before* field validation — that is the only hook
    that can fill a field pydantic would otherwise leave to the caller — so `cell` is whatever
    the caller passed: a model on a direct construction, a `dict` on the `model_validate` that
    reads a package back from `data/story_runs/` (§14).

    `None` only when there is no cell at all. A `cell` that is present but carries no usable
    pair is **refused**, not read as absent: treating it as absent would mint the narrative
    `…:span:…` form for a table row and present a grid reading as prose evidence.

    `bool` is rejected for the reason `canonicalization._integer` states: it subclasses `int`,
    so a stray `True` would mint `r1` — a handle that resolves to a real cell and is wrong.
    """
    if cell is None:
        return None
    read = cell.get if isinstance(cell, Mapping) else (lambda key: getattr(cell, key, None))
    row, column = read("row_index"), read("value_column_index")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (row, column)):
        raise ValueError(
            f"cell={cell!r} carries no usable (row_index, value_column_index); a handle minted "
            "from a coordinate this row does not actually hold would name a cell nobody chose")
    return row, column


class PackagedFact(StoryModel):
    """One observation as the model may see it (§10 `facts[]`).

    The evidence chain is optional on purpose. §13.7.2: an `:EvidenceSource` is a leaf with no
    `PART_OF` edge, so a fact evidenced by one has no `passage_id`, and `:MarketData` and
    `:Calculated` carry no `document_id` at all *"because nobody filed it"*. Zero such rows
    exist today; the shape exists so the XBRL lane cannot arrive by widening this type.

    **The three `corroborating_*` lists are what S3 filled, and they are three id lists rather
    than five more `facts[]` rows on purpose.** EVIDENCE_ROLES_AND_SEMANTIC_FACTS §1.1 counted
    `housing_inventory_homes` 2023-03-31 at **6 observations, all 6261.0 homes, 6 different
    documents**; canonicalisation collapses the slot to one fact and §10 carried one passage, so
    five concordant sources were discarded and the best-corroborated fact in the candidate was
    presented as thinly sourced. S3 records them here — one canonical fact, one `primary_support`
    source chosen by §6.1 step 5's own ordering, the rest by id — and **mints no duplicate
    fact**: a second `facts[]` row stating the same number would be a second fact, not a second
    source, and §13 would then have two things to verify where the corpus has one.

    Membership is decided by `story.core.observation_equivalence.same_reading`, on canonical
    values and never on printed strings.

    **`evidence_handle` is derived, never stated, and that is not a retreat from
    `PackagedPassage.role`.** `role` is required with no default because *nothing else on that
    row answers it*: only the caller knows what a passage is to the story, and every default was
    a claim about evidence, so seventeen call sites had to say. The handle is the opposite case.
    It is a pure function of `passage_id`, `cell` and the fact's slot — four fields of this row —
    so a caller who stated it would be adding no information and could only disagree, and
    "the handle this package minted for `F`" (§3.4 check 7) would then have two authorities. It
    is therefore minted at construction, and a stated handle that differs from the coordinates
    is **refused** rather than believed. Every existing call site keeps working, and none of
    them was ever in a position to know the answer better than the row.

    **Stored, not a `@property`, for two reasons.** §14 writes the package to
    `data/story_runs/<id>/evidence_package.json` and the evidence panel reads it there, so the
    token a rejection names has to be greppable in the artifact. And it is inside
    `package_content_digest`: the handle is the *model's* citation vocabulary, so changing its
    format must re-key the package the way a new section does, and a handle computed on read
    would let that vocabulary change while the digest stood still. A pydantic `computed_field`
    would give the first and not the second, and would additionally break the round trip — under
    `extra="forbid"` a computed field is rejected as input, so `model_validate(model_dump())`
    raises *(checked against pydantic 2.13.4)*, and §14 reads every package back.

    **`None` means this fact names no filed passage** — the §13.7.2 `evidence_source_id` row, of
    which zero exist today. That fact has no passage handle and is given none: minting
    `ev:<evidence_source_id>:…` would hand the writer a citable-looking token for the one path
    V1 refuses outright (`evidence_kind_not_supported_in_v1`), which is worse than a fact the
    writer cannot cite at all.

    **The handle names this fact's own cell and no corroborating one.** The `corroborating_*`
    ids stay ids: a concordant source that §6.1 collapsed is a second *source*, not a second
    piece of evidence for this sentence, and a handle minted for one could be cited as though
    the package had verified it. `cell` and `evidence_handle` describe `passage_id`, which is
    the reading §6.1 step 5 chose as the representative.
    """

    observation_id: str
    metric_id: str
    metric_label: str
    period_key: str
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    shape: str
    value: float
    unit: str
    currency: str | None = None
    scale: str | None = None
    scale_location: str | None = None
    printed_form: str | None = None
    row_label: str | None = None
    column_label: str | None = None
    source_lane: str
    validation_state: str
    warning_codes: tuple[str, ...] = ()
    ambiguity_codes: tuple[str, ...] = ()
    passage_id: str | None = None
    document_id: str | None = None
    source_url: str | None = None
    #: A property of the `EVIDENCED_BY` **relationship**, not of `:Observation`
    #: (WORKSTREAM_BOUNDARY §4.2 point 2) — so are `table_id` and `block_ids`. Retrieval Cypher
    #: has to return the edge property explicitly; a query returning only node fields loses
    #: every citation quote silently, and §13.7's Rule A is entirely about this string.
    quoted_text: str | None = None
    #: Set instead of `passage_id` when the fact's evidence names no filed passage (§13.7.2).
    evidence_source_id: str | None = None
    #: `OBSERVED` by default because every row this package has ever carried is one reading of
    #: one filed cell. A derived quantity is the writer's `Calculation` today and is not a
    #: packaged fact; the member exists so it cannot arrive by widening `OBSERVED`.
    fact_kind: FactKind = FactKind.OBSERVED
    #: The concordant sources §6.1 collapsed into this one canonical fact. Sorted and unique for
    #: `_require_sorted_unique`'s reason: these are digest inputs by way of the package digest,
    #: and two orderings of one set would be two byte sequences describing one thing.
    corroborating_observation_ids: tuple[str, ...] = ()
    corroborating_passage_ids: tuple[str, ...] = ()
    corroborating_document_ids: tuple[str, ...] = ()
    #: Where in `passage_id`'s flattened grid this fact's value was read, and where its period
    #: header sits. `None` for the 14 narrative observations, which were read out of prose and
    #: have no grid — the same `None` `ObservationRecord`'s five indices carry, kept honest
    #: rather than filled.
    cell: TableCellRef | None = None
    #: The name a citation binds to instead of retyping `quoted_text` (§3.1). Derived — see the
    #: class docstring for why it is not a required field and why it is stored rather than
    #: computed on read.
    evidence_handle: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _mint_evidence_handle(cls, data: Any) -> Any:
        """Fill `evidence_handle` from the coordinates, and refuse one that disagrees.

        `mode="before"` because it is the only hook that can *fill* a field: an after-validator
        cannot assign to a frozen model, and returning a copy from one is silently ignored on
        direct construction — pydantic 2.13.4 warns *"returning anything other than `self` from
        a top level model validator isn't supported when validating via `__init__`"*, so
        `PackagedFact(...)` would have kept the `None` while `model_validate(...)` filled it.
        Two constructions of one row disagreeing about its handle is the single thing this
        field may not do.
        """
        if not isinstance(data, Mapping):
            return data
        minted = _evidence_handle(passage_id=data.get("passage_id"),
                                  metric_id=data.get("metric_id"),
                                  period_key=data.get("period_key"), cell=data.get("cell"))
        stated = data.get("evidence_handle")
        if stated is not None and stated != minted:
            raise ValueError(
                f"{data.get('observation_id')}: evidence_handle={stated!r} was stated, but this "
                f"row's own passage, slot and cell mint {minted!r}"
                + (" — None because the row names no filed passage" if minted is None else "")
                + ". A handle is derived from coordinates the row already carries; a stated one "
                "that differs is a second authority for one fact, and naming another cell is "
                "the mis-citation §13.7 check 7 refuses")
        return {**data, "evidence_handle": minted}

    @model_validator(mode="after")
    def _corroboration_is_sorted(self) -> "PackagedFact":
        for name in ("corroborating_observation_ids", "corroborating_passage_ids",
                     "corroborating_document_ids"):
            _require_sorted_unique(getattr(self, name), name)
        return self

    @model_validator(mode="after")
    def _has_some_evidence(self) -> "PackagedFact":
        if not (self.passage_id or self.evidence_source_id):
            raise ValueError(
                f"{self.observation_id}: a packaged fact must name either the passage it was "
                "read from or the :EvidenceSource it was tagged in; a fact with neither has "
                "no citation chain and §13.7 has nothing to check")
        return self


class PackagedPassage(StoryModel):
    """§10's four passage sections, one shape.

    `char_start`/`char_end` are into the **full** `:Passage.text`. Explanatory and
    counter-evidence passages are excerpted to a ±400-character window (§10.2.1 point 2) and
    carry `excerpted: true`, so a citation still resolves to the byte and an evidence panel
    can fetch the rest. A passage a fact is bound to is never excerpted — Rule A needs the
    whole table.

    **`role` has no default, and the absence is the design.** Every value it could default to is
    a claim about evidence: `PRIMARY_SUPPORT` would let an unmigrated construction assert that
    an arbitrary passage backs a fact, and `UNUSABLE` would assert that it backs nothing. The
    honest third option is that a caller who has not said cannot build one, so the field is
    required and pydantic refuses the construction by name. Seventeen call sites had to state a
    role when this landed; each of them knew the answer.

    **`match_basis` is a field here rather than a warning read back out of the package.**
    `counter_evidence.py` recorded the workaround it was forced into — *"§10's
    `counter_evidence[]` is typed `tuple[PackagedPassage, ...]` … which S5 does not own and
    which has no field for a match basis, so the basis travels as one of two `ANNOTATE` warnings
    and `match_basis_of` reads it back"*. S1 owns this module, so the basis is on the row. The
    two disclosure warnings stay: they are what an evidence panel renders beside the claim, and
    both they and this field are written from one value at one call site. Empty string means
    *"this passage was not associated with a fact on any basis"*, which is true of every passage
    outside `counter_evidence[]`; the named bases live in
    `story/stages/packaging/counter_evidence.py` beside the code that decides them, and S2 adds
    the rest of them there rather than as an enum here.
    """

    passage_id: str
    document_id: str
    text: str
    char_count: int
    heading_path: tuple[str, ...] = ()
    section_id: str | None = None
    passage_kind: str | None = None
    source_url: str | None = None
    char_start: int = 0
    char_end: int | None = None
    excerpted: bool = False
    #: Set on `explanatory_passages[]` only — the terms that matched and the fulltext score,
    #: recorded so the reason a passage is in the package is inspectable.
    query_terms: tuple[str, ...] = ()
    score: float | None = None
    #: What this passage is to the story. Required — see the class docstring.
    role: EvidenceRole
    #: Why this passage was associated with the candidate's facts, when it was. Empty otherwise.
    match_basis: str = ""
    #: What `passage_quality.assess` found in this passage's own text. `UNASSESSED` is still the
    #: default and still means *"nobody looked"*: S2 assesses every passage the packager builds,
    #: and a row constructed by hand elsewhere has honestly not been assessed.
    quality_status: PassageQuality = PassageQuality.UNASSESSED
    unusable_reason: PassageUnusableReason | None = None
    #: The `:Issue` codes that caused this passage to be collected at all — set on
    #: `counter_evidence[]` and on `diagnostic_passages[]`, empty everywhere else.
    #:
    #: **A field rather than a warning, and the reason is §13.** The two `counter_evidence_*`
    #: warning codes are `CLAIM_QUALIFYING`, so a post that carries one must write a sentence
    #: about it; demanding *"as reported elsewhere in the filing"* about an `AMBIGUOUS_ALIAS`
    #: refusal is the §1 defect one layer down. A demoted row therefore emits no warning, and
    #: this is where the diagnostic it was demoted from survives — visible to a reviewer, silent
    #: to §13's disclosure rule. Sorted and unique: it reaches `package_content_digest`.
    diagnostic_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _diagnostic_codes_are_sorted(self) -> "PackagedPassage":
        _require_sorted_unique(self.diagnostic_codes, "diagnostic_codes")
        return self

    @model_validator(mode="after")
    def _a_reason_needs_a_finding(self) -> "PackagedPassage":
        if self.unusable_reason is not None and self.quality_status is not PassageQuality.UNUSABLE:
            raise ValueError(
                f"{self.passage_id}: unusable_reason={self.unusable_reason.value} on a passage "
                f"whose quality_status is {self.quality_status.value}. A reason without the "
                "finding it explains is a row a consumer can read either way")
        return self


class EventParticipant(StoryModel):
    """Who took part in an event and as what. A role can repeat within one event — one 8-K
    reports an incoming chief executive and a returning chairman — so this is a sequence and
    not a mapping."""

    entity_id: str
    role: str
    entity_text: str | None = None
    resolved: bool = True


class PackagedEvent(StoryModel):
    """§10's `events[]`.

    `properties` values are **verbatim strings** and stay that way: `charge_amount:
    "approximately $15 million"` is not a typed value and §13.8 refuses any `fact_binding`
    pointing at one. The only permitted use is quoting the string with the event's evidence
    passage cited.
    """

    event_id: str
    event_type_id: str
    occurred_on: str | None = None
    announced_on: str | None = None
    date_basis: str | None = None
    review_flag: str | None = None
    properties: Mapping[str, str] = {}
    participants: tuple[EventParticipant, ...] = ()
    passage_id: str | None = None
    quoted_text: str | None = None


class PackagedRelationship(StoryModel):
    """§10's `relationships[]`."""

    relationship_instance_id: str
    predicate: str
    source_entity_id: str
    source_entity_type: str
    target_entity_id: str
    target_entity_type: str
    valid_from: str | None = None
    valid_to: str | None = None
    passage_id: str | None = None
    quoted_text: str | None = None


class PackagedDocument(StoryModel):
    """§10's `documents[]` — built only from **cited passages**.

    An `:EvidenceSource` carrying a `document_id` does not imply this row exists (§13.7.2), so
    a consumer must treat a missing document as ordinary rather than as a packaging defect.
    """

    document_id: str
    form: str | None = None
    filing_date: str | None = None
    report_date: str | None = None
    accession: str | None = None
    source_url: str | None = None
    document_type: str | None = None
    title: str | None = None
    content_sha256: str | None = None


class MetricAmbiguity(StoryModel):
    """One declared ambiguity on a metric, with the ontology's own words.

    `description` and `impact` are carried verbatim rather than summarised: §10.1 requires the
    caveat to reach the post, and a paraphrase of a caveat is a new claim.
    """

    code: str
    description: str
    impact: str


class PackagedMetric(StoryModel):
    """§10's `metrics[]` — the full definition row for every metric the package references.

    **Populated from the ontology, never from the `:Metric` node** (WORKSTREAM_BOUNDARY §4.2
    point 3, verified 2026-08-03): the node carries no `percentage_min`, `percentage_max`,
    `distinct_from` or `reconciles_to`, and the 36 `DISTINCT_FROM` edges may support graph
    inspection but must never be the source of a comparability ruling. A packager that read
    these off the graph would silently fill them with nothing.

    **`description` is the plain-language definition, and it comes from the ontology.** The
    `:Metric` node carries 22 properties including one called `description`, and it is **not**
    the source of truth (recon correction C4, restated by
    EVIDENCE_ROLES_AND_SEMANTIC_FACTS §2): the same argument that keeps `percentage_min` and
    `distinct_from` off the graph keeps this off it. `None` today — S1 adds the field and S4
    populates it — and `None` here means the field has not been populated yet rather than that
    the ontology declares no definition.
    """

    metric_id: str
    label: str
    #: The metric in words, as the ontology states it. `None` until S4; never read off `:Metric`.
    description: str | None = None
    unit: str
    allowed_units: tuple[str, ...] = ()
    period_type: str | None = None
    aliases: tuple[str, ...] = ()
    distinct_from: tuple[str, ...] = ()
    mutually_distinct_groups: tuple[str, ...] = ()
    ambiguities: tuple[MetricAmbiguity, ...] = ()
    population: str | None = None
    percentage_min: float | None = None
    percentage_max: float | None = None


class PackagedFormulaWindow(StoryModel):
    """§10's `formula_windows[]`.

    `adjustment_components` keeps its `note` text: the notes are the only place the corpus
    says what an adjustment *is*, and §6.3's F8 shows an expression whose declared four-term
    form and the corpus's three-term identity disagree. A window without its notes would let a
    verifier check the wrong identity and pass.
    """

    metric_id: str
    version_id: str
    valid_from: str | None = None
    valid_to: str | None = None
    expression: str
    component_metrics: tuple[str, ...] = ()
    adjustment_components: tuple[Mapping[str, str], ...] = ()
    basis: str | None = None


# ---------------------------------------------------------------------------------------
# Semantic, identity and comparability facts — EVIDENCE_ROLES_AND_SEMANTIC_FACTS §4 S4
#
# **Why these are facts and not more metadata.** §10 already carries `metrics[]`,
# `formula_windows[]` and `compatibility[]`, and the planner already receives seven metric
# fields through `prompts._metric_lines`. What it does not receive is the *definition* — what
# the number means in words — and a post whose numbers have no declared meaning is the failure
# S5 turns into a package refusal. The plan's answer is not another metadata block: it is that
# the ontology's declarations enter the package on the same footing as the observations, with
# an authority, an editability and a citation handle, so §11's planner and S6's evidence panel
# read them as facts rather than as configuration.
#
# **Authority is stated per row, not assumed.** The ontology is authoritative for metric
# semantics (C4) and is not editable from the prompt UI (S6). Subject identity is a different
# case and must not be flattered into the same one: today `subject_identity_not_read_from_graph`
# records that `entity_text` and `labels` are the *caller's* and `resolved` is inferred, so an
# identity fact built from them is not authoritative. Both fields are required on all three
# types for that reason — a default would be this module answering a question the populating
# stage is the only one that can.
#
# Every section is empty on every package built at S1. S4 fills them.
# ---------------------------------------------------------------------------------------


class SemanticFact(StoryModel):
    """One thing the ontology declares about a metric, stated as a fact (§4 S4).

    `attribute` names which of the plan's per-metric declarations this row carries — display
    name, aliases, plain-language definition, unit, scale, instant-vs-duration semantics,
    formula version and effective window, a source-backed scope rule. A free string and not an
    enum, matching `PackagedPassage.match_basis`: the names belong beside the code that emits
    them, and S4 is the stage that decides what the list is. `statement` is the ontology's own
    words rather than a paraphrase, for `MetricAmbiguity`'s reason — a paraphrase of a
    definition is a new definition.
    """

    fact_id: str
    fact_kind: Literal[FactKind.SEMANTIC] = FactKind.SEMANTIC
    metric_id: str
    attribute: str
    #: The declaration in plain language, as the model will read it.
    statement: str
    #: The structured value the statement renders, where there is one — `"percent"`, `"instant"`,
    #: a version id. Empty when the declaration is prose and has no other form.
    value: str = ""
    #: True for the ontology (C4). Stated, never defaulted — see the section comment.
    authoritative: bool
    #: Whether a human may change it from the prompt UI. False for the ontology (S6).
    editable: bool
    #: What declared it — `ontology:<id>@<semantic_version>` for a row read from the ontology.
    source: str = ""
    #: Filed text backing the declaration, where the corpus has any. Empty is the ordinary case:
    #: a definition the ontology asserts is not a sentence in a 10-Q, and inventing a span for it
    #: would be the fabrication §13.7 exists to refuse.
    citations: tuple[CitationHandle, ...] = ()
    warning_codes: tuple[str, ...] = ()


class IdentityFact(StoryModel):
    """One structured thing known about the story's subject (§4 S4).

    **`available` is the field that keeps a missing description missing.** §4 S4: *"No company
    description may be invented from model knowledge. If no source-backed description exists,
    carry structured identity only and mark the description unavailable."* A package that simply
    omitted the row would leave the model free to supply one from its weights and leave the
    evidence panel unable to say the corpus was asked; a row with `available: false` says both.
    """

    fact_id: str
    fact_kind: Literal[FactKind.IDENTITY] = FactKind.IDENTITY
    entity_id: str
    #: `legal_name`, `ticker`, `entity_type`, `description` — S4 owns the list.
    attribute: str
    statement: str
    value: str = ""
    #: False when the corpus carries no source-backed answer. `statement` then says so, and
    #: `value` is empty.
    available: bool = True
    authoritative: bool
    editable: bool
    source: str = ""
    citations: tuple[CitationHandle, ...] = ()
    warning_codes: tuple[str, ...] = ()


class ComparabilityFact(StoryModel):
    """One comparison rule the ontology declares, stated as a fact (§4 S4).

    **Not a second `CompatibilityDecision`, and the boundary is worth stating once.**
    `CompatibilityDecision` is §6.9's *answer about one pair of slots* — `left_id`, `right_id`,
    comparable or not, and the rule that decided it. This is the *rule itself*, ranging over the
    metrics it constrains and readable without a pair: that `adjusted_gross_margin` and
    `gaap_gross_margin` are mutually distinct, that a percentage and a percentage-point change
    are not one quantity (§13.3), that a formula version boundary separates two definitions of
    one name. The planner needs the rule to avoid writing the sentence; the decision only tells
    it whether one comparison it already made was allowed.
    """

    fact_id: str
    fact_kind: Literal[FactKind.COMPARABILITY] = FactKind.COMPARABILITY
    #: The ontology's own rule identifier, so a reader can find what this is a rendering of.
    rule_id: str
    #: The metrics the rule ranges over, sorted and unique.
    metric_ids: tuple[str, ...] = ()
    statement: str
    authoritative: bool
    editable: bool
    source: str = ""
    citations: tuple[CitationHandle, ...] = ()
    warning_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _metric_ids_are_sorted(self) -> "ComparabilityFact":
        _require_sorted_unique(self.metric_ids, "metric_ids")
        return self


class PackagedEvidenceSource(StoryModel):
    """§10's `evidence_sources[]`. **Empty on every run today** — all 2,714 evidence rows are
    `normalized_passage` or `normalized_table` (§13.7.2, verified 2026-08-03).

    `fields` holds the kind's own columns rather than fifteen optional attributes, because the
    five kinds share almost nothing: `xbrl_fact` has `accession`/`xbrl_concept`, `market_data`
    has `provider`/`instrument_id`/`session_date`, `calculated` has `input_observation_ids`.
    """

    evidence_source_id: str
    evidence_kind: str
    labels: tuple[str, ...] = ()
    fields: Mapping[str, Any] = {}


class PackagedWarning(StoryModel):
    """§10's `warnings[]` — the union of §10.1.

    **Named `PackagedWarning`, not `Warning`.** A module-level class called `Warning` shadows
    the builtin exception for every consumer that does `from story.core.models import *` or
    reads the module namespace, and the shadow is silent. The `Packaged*` prefix is what the
    other fifteen row types already use.

    **`kind` is a field and `WarningCategory` is not, and the difference is measured.** `kind`
    has to travel on the row because the *planner* reads it and a stage may not import another
    stage. The finer category has no such consumer: the only readers that need it — the code
    catalogue and the evidence panel — are free to import `warning_codes.CATEGORY_OF`, which is
    where the decision lives. Carrying it anyway costs **170 prompt tokens on a full twenty-row
    `warnings[]`** *(measured 2026-08-05)*, and `warnings[]` is inside the slice the model reads,
    where §10.2's budget is tight enough that the three spike packages ship two to four passages.
    A label the UI can look up is not worth a third of a passage.
    """

    code: str
    severity: Severity
    #: Whether this warning qualifies a claim or records how the package was built. Defaulted
    #: to `CLAIM_QUALIFYING` so a warning constructed without stating a kind demands disclosure
    #: rather than escaping it — see `WarningKind` for why the default points that way.
    kind: WarningKind = WarningKind.CLAIM_QUALIFYING
    subject_ids: tuple[str, ...] = ()
    detail: str = ""


class ConflictCluster(StoryModel):
    """One reading of a fact-slot: the value, and which filings support it (§6.1 step 4)."""

    value: float
    document_ids: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()


class Conflict(StoryModel):
    """§10's `conflicts[]` — a fact-slot that held more than one reading.

    `slot` is `(metric_id, period_key)` rendered, matching §6.1's keying. Measured on the
    current run: 537 slots, 36 multi-valued, all collapsing to one cluster under presentation
    tolerance, so `classification` is a shape this fires on today only via §6.1 step 2.
    """

    slot: str
    clusters: tuple[ConflictCluster, ...]
    classification: str
    resolution_rule: str


class CompatibilityDecision(StoryModel):
    """One §6.9 comparability answer and the rule that gave it.

    Bounded at 12 (§10.2) and restricted to **the decisions the candidate's own comparisons
    made** — "every comparability decision" is O(n²) over a 26-quarter series and was how an
    unbounded section got into a package with a token budget (§0c item 4).
    """

    left_id: str
    right_id: str
    rule_id: str
    comparable: bool
    reason: str = ""


class RetrievalTraceEntry(StoryModel):
    """One §9 tool call, recorded so the package's provenance is inspectable without a
    database. `truncated` is separate from `row_count` because a bound that bit is the fact a
    reader needs, and a count alone never says whether it did."""

    tool: str
    parameters: Mapping[str, Any] = {}
    row_count: int
    truncated: bool
    elapsed_ms: float
    outcome: RetrievalOutcome = RetrievalOutcome.OK


class BudgetParameters(StoryModel):
    """§10.2's caps, as the packager was configured — a digest input, not a measurement.

    Defaults are §10.2's *defaults*, not its ceilings. `config/story.yaml` (S11) owns the
    values; they are digested into `package_id` and `story_run_id` so two runs at different
    caps can never share a directory. That collision is exactly the defect §14 records against
    its own first draft: `--limit 3` and `--limit 20` minted one id, and atomic finalisation
    would then have replaced one with the other.
    """

    max_facts: int = 12
    max_events: int = 5
    max_relationships: int = 4
    max_primary_passages: int = 4
    max_context_neighbours: int = 1
    max_explanatory_passages: int = 3
    max_counter_evidence: int = 3
    max_documents: int = 20
    max_metrics: int = 8
    max_formula_windows: int = 8
    max_warnings: int = 20
    max_conflicts: int = 8
    max_compatibility: int = 12
    max_retrieval_trace: int = 40
    max_total_tokens: int = 5000
    #: §10.2.1 point 2 — the excerpt window around a matched span.
    excerpt_radius_chars: int = 400
    max_graph_hops: int = 2

    def digest_parts(self) -> tuple[str, ...]:
        """Labelled `name=value` parts, sorted, for `package_id` and `story_run_id`.

        Labelled rather than positional so a cap added later cannot shift the meaning of every
        part after it — the discipline `extraction/core/identifiers.py` states for absent
        optional components.
        """
        payload = self.model_dump(mode="json")
        return tuple(f"{name}={payload[name]}" for name in sorted(payload))


class SectionLedgerEntry(StoryModel):
    """What one §10 section had, what it shipped, and why the difference exists (§4 S5).

    §4 S5: *"Every section records available / carried / dropped / reason."* `section_counts`
    answers only the middle one, and a count of what shipped cannot distinguish *"the corpus
    held four"* from *"the corpus held nine and the budget took five"* — which is exactly what
    §4 S6 requires the panel to render for `token_budget_trimmed` and `section_truncated`
    (*"4 of 9 primary passages sent to the model"*).

    `reasons` is plural and holds **warning codes**, not sentences: a section can be bound twice
    — once by its §10.2 cap and again by the token budget — and a code is resolvable through the
    same catalogue the panel already renders warnings from, while a sentence written here would
    be a second copy of one written there.

    `required_dropped` is the field the plan's *"and that no required fact was dropped"* claim
    is read from. It is false on every package the assembler can produce, because a required
    fact is untrimmable and a package that cannot fit one refuses (`required_fact_does_not_fit`)
    rather than shipping without it. It exists so the panel states that as a measured fact
    rather than as a property nobody checked.

    **`available` is `None` when nobody counted it, and never a flattering guess.** It is exact
    for every section the assembler bound itself and for every section a stage reported through
    `package_assembly.note_available`. Where an earlier §10.2 cap dropped rows and reported no
    count it is `None`: *"4 of 4 available"* about a section that had nine is a false statement,
    and the `section_truncated` code in `reasons` points at the warning whose detail carries the
    real numbers.
    """

    section: str
    available: int | None
    carried: int
    dropped: int
    reasons: tuple[str, ...] = ()
    #: Whether the trimmer was forbidden to touch this section at all (§4 S5's protected set).
    protected: bool = False
    required_dropped: bool = False


class PackageBudget(StoryModel):
    """§10's `budget` — what the package actually cost and which caps bound it.

    Distinct from `BudgetParameters`: that is the configuration, this is the measurement.
    `caps_hit` names the sections that were truncated, so "the model did not see it" is a
    statement in the artifact rather than an inference from two counts.

    **Two estimates, because the artifact and the prompt are not the same document.**
    `artifact_token_estimate` sizes the whole package as written to disk;
    `prompt_token_estimate` sizes only the slice that can reach a model — everything except
    `retrieval_trace` and this block. **§10.2's ≤5,000 total and its 6,000 ceiling bind the
    prompt estimate**, and the trim targets it. Measured on the F1 spike before the split: the
    trace cost 1,084 tokens and this block 190, so a fifth of §10.2's budget was spent on
    provenance no model slice contains, and all three spikes shipped two primary passages and
    zero context as a result. §10.2.1 point 3 already says *"the planner and the writer see
    different slices of one package"*; the trace is in neither.
    """

    artifact_token_estimate: int
    prompt_token_estimate: int
    section_counts: Mapping[str, int] = {}
    #: §4 S5's available / carried / dropped / reason, one row per section, sorted by name.
    #: Empty only on a `PackageBudget` built by hand; the assembler always fills it.
    section_ledger: tuple[SectionLedgerEntry, ...] = ()
    parameters: BudgetParameters = BudgetParameters()
    caps_hit: tuple[str, ...] = ()


class PackageIdentity(StoryModel):
    """The identity block, as a value a draft and a verification can carry whole.

    §13.17 requires `VerifiedDraft` to hold it, and copying fourteen fields onto three types
    is how they drift. `StoryEvidencePackage.identity` derives one from its own flat fields
    rather than storing a second copy.
    """

    package_id: str
    package_version: str
    candidate_id: str
    detector_id: str
    detector_version: str
    policy_version: str
    graph_run_id: str
    graph_projection_version: str
    extraction_run_id: str
    run_complete_sha256: str
    ontology_id: str
    ontology_definition_hash: str
    ontology_semantic_version: str
    package_content_digest: str


class StoryEvidencePackage(StoryModel):
    """The model's entire universe (§10), serializable, hashed and reproducible.

    Twenty bounded sections — sixteen at S0, plus `semantic_facts`, `identity_facts` and
    `comparability_facts` at S1 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS, plus
    `diagnostic_passages` at S2. Nothing a model produced
    may enter one, and nothing outside one may reach a prompt: that is the whole of §2's line —
    the model chooses words, code chooses facts.

    `package_content_digest` is the sha256 of this package's own canonical JSON **with that
    field excluded**, which is the only way §10.3's "digest over the whole package" can be a
    field of the package it digests. `story.core.keys.package_content_digest` computes it and
    `with_content_digest` stamps it.
    """

    package_id: str
    package_version: str = PACKAGE_VERSION
    candidate_id: str
    detector_id: str
    detector_version: str
    policy_version: str
    graph_run_id: str
    graph_projection_version: str
    extraction_run_id: str
    run_complete_sha256: str
    ontology_id: str
    ontology_definition_hash: str
    ontology_semantic_version: str
    package_content_digest: str = ""

    subject: PackagedSubject
    facts: tuple[PackagedFact, ...] = ()
    #: The ontology's declarations, as facts (§4 S4). Empty until S4 populates them; they are
    #: inside `prompt_slice` from the day they are added, because a section the token budget
    #: does not cover is a section two runs can differ on silently.
    semantic_facts: tuple[SemanticFact, ...] = ()
    identity_facts: tuple[IdentityFact, ...] = ()
    comparability_facts: tuple[ComparabilityFact, ...] = ()
    metrics: tuple[PackagedMetric, ...] = ()
    formula_windows: tuple[PackagedFormulaWindow, ...] = ()
    events: tuple[PackagedEvent, ...] = ()
    relationships: tuple[PackagedRelationship, ...] = ()
    evidence_sources: tuple[PackagedEvidenceSource, ...] = ()
    primary_passages: tuple[PackagedPassage, ...] = ()
    context_passages: tuple[PackagedPassage, ...] = ()
    explanatory_passages: tuple[PackagedPassage, ...] = ()
    counter_evidence: tuple[PackagedPassage, ...] = ()
    #: Every passage `find_counter_evidence` pointed at that **did not qualify** as
    #: counter-evidence (§4 S2), carrying the role it actually plays — `warning_only` for an
    #: extraction or data-quality diagnostic, `unusable` for content that can carry no evidence
    #: at all.
    #:
    #: **A section is a place; a role is a claim.** This one is named for the reason the rows
    #: were collected, and each row's `role` says what it turned out to be — which is the same
    #: split `EvidenceRole` exists for, applied to the section list itself. Putting these back in
    #: `counter_evidence[]` would keep §11's planner obliged to write a counterpoint about an
    #: `AMBIGUOUS_ALIAS` refusal; dropping them would make the reclassification unreviewable.
    diagnostic_passages: tuple[PackagedPassage, ...] = ()
    warnings: tuple[PackagedWarning, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    compatibility: tuple[CompatibilityDecision, ...] = ()
    documents: tuple[PackagedDocument, ...] = ()
    retrieval_trace: tuple[RetrievalTraceEntry, ...] = ()
    budget: PackageBudget

    @model_validator(mode="after")
    def _evidence_handles_are_unique(self) -> "StoryEvidencePackage":
        """No two facts in one package answer to one handle.

        This is what §3.4's check 7 rests on. A citation carries a fact id and a handle, and the
        check is *"the handle this package minted for `F`"* — which is a well-formed question
        only while handle → fact is a function. Two facts sharing one would let a sentence bound
        to the wrong fact pass the check that exists to catch exactly that.

        For a table cell a collision is a **defect and not a naming clash**: §1.4 grouped every
        table-backed observation by `(passage_id, row_index, value_column_index)` and measured
        2,690 distinct cells, none mapping to two periods or two metrics *(verified live
        2026-08-13)*. One cell is one fact, so two facts claiming one cell means one of them was
        read out of a cell it does not occupy, and a package that shipped both would be citing a
        number the grid does not hold at that position.

        For a narrative span it is a shape the corpus is one row away from producing — 14
        observations in 6 passages, and `_evidence_handle` explains what the slot in the handle
        is doing. Refused rather than deduplicated or warned about, because the failure
        it prevents is a *citation* that verifies against the wrong fact, and a package that
        cannot name its evidence distinctly cannot support the contract §12 and §13 are about.
        """
        minted: dict[str, str] = {}
        for fact in self.facts:
            if fact.evidence_handle is None:
                continue
            first = minted.setdefault(fact.evidence_handle, fact.observation_id)
            if first != fact.observation_id:
                raise ValueError(
                    f"evidence_handle {fact.evidence_handle!r} is minted for two facts, "
                    f"{first} and {fact.observation_id}. A handle names one piece of evidence "
                    "and §13.7 check 7 asks which fact a package minted it for; two answers "
                    "make a citation to the wrong fact unrefusable")
        return self

    def facts_by_evidence_handle(self) -> Mapping[str, PackagedFact]:
        """Handle → fact, for every fact that has one.

        Here rather than in each consumer because §12's writer gate and §13.7's citation rules
        both resolve handles and would otherwise build one index each from one package — the
        arrangement `PackageIndex` already refuses for the column-ambiguity map. A method and
        not a property, so the O(facts) build is visible at the call site and a caller holds the
        result instead of rebuilding it per citation.

        Facts with no handle — §13.7.2's `evidence_source_id` rows — are absent rather than
        keyed under `None`: a citation that resolves to nothing is `unresolvable_evidence_handle`
        (§3.4 check 1), and that is the answer they should produce.
        """
        return {fact.evidence_handle: fact
                for fact in self.facts if fact.evidence_handle is not None}

    @property
    def identity(self) -> PackageIdentity:
        payload = self.model_dump(mode="json")
        return PackageIdentity(
            **{name: payload[name] for name in PackageIdentity.model_fields})

    def digestible_payload(self) -> dict[str, Any]:
        """This package minus the field that holds its own digest."""
        payload = self.model_dump(mode="json")
        payload.pop("package_content_digest")
        return payload

    def with_content_digest(self, value: str) -> "StoryEvidencePackage":
        return self.model_copy(update={"package_content_digest": value})


# ---------------------------------------------------------------------------------------
# Planning — §11
# ---------------------------------------------------------------------------------------


class KeyPoint(StoryModel):
    """§11. Every id must resolve in the package; a plan naming anything else is rejected
    before the writer runs."""

    claim: str
    required_fact_ids: tuple[str, ...] = ()
    required_citation_passage_ids: tuple[str, ...] = ()
    statement_class: StatementClass


class Counterpoint(StoryModel):
    """§11, with the rule that stopped it being satisfiable vacuously.

    A counterpoint must carry at least one id drawn from the package's `counter_evidence`:
    `{claim: "Margins vary.", required_fact_ids: []}` satisfied the first draft's
    non-emptiness rule literally, and a counterpoint grounded in nothing is not a
    counterpoint. §15.3 forbids `minItems` in the schema, so the constraint cannot be
    expressed to the model at all — which is why it is enforced here, at construction, and
    not hoped for.
    """

    claim: str
    required_fact_ids: tuple[str, ...] = ()
    required_citation_passage_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _is_grounded(self) -> "Counterpoint":
        if not (self.required_fact_ids or self.required_citation_passage_ids):
            raise ValueError(
                f"counterpoint {self.claim!r} names no fact and no passage; §11 requires at "
                "least one id drawn from the package's counter_evidence")
        return self


class UnusableEvidence(StoryModel):
    """A package item the plan deliberately did not use, and why (§11). Every
    counter-evidence item the plan skipped must appear here."""

    id: str
    reason: UnusableReason


class EditorialPlan(StoryModel):
    """§11's output — the first model call, temperature 0.0, no tools, package-only input."""

    candidate_id: str
    package_id: str
    thesis: str
    why_it_matters: str
    key_points: tuple[KeyPoint, ...] = ()
    counterpoints: tuple[Counterpoint, ...] = ()
    #: Warning codes from the package that MUST appear in the post. §13 refuses a draft that
    #: drops one.
    required_warnings: tuple[str, ...] = ()
    #: Computed by code from the package before the call, never chosen by the model.
    causal_language: CausalLanguage = CausalLanguage.FORBIDDEN
    uncertainty: str = ""
    structure: tuple[str, ...] = ()
    prohibited_claims: tuple[str, ...] = ()
    unusable_evidence: tuple[UnusableEvidence, ...] = ()
    prompt_version: str = ""
    model_id: str = ""


# ---------------------------------------------------------------------------------------
# Drafting — §12
# ---------------------------------------------------------------------------------------


class FactBinding(StoryModel):
    """The writer's declaration of which fact a span of its own text states (§12).

    The verifier **checks the binding it was handed and never guesses one**. Measured over the
    corpus, a bare numeral is ambiguous 98.9% of the time, number + unit 97.9%, number + unit
    + period 97.2%; a verifier that reverse-engineers the intended fact is wrong far more
    often than not.
    """

    fact_id: str
    rendered: str
    char_start: int
    char_end: int
    metric_surface: str
    period_surface: str

    @model_validator(mode="after")
    def _span_is_usable(self) -> "FactBinding":
        _require_span(self.char_start, self.char_end, f"fact_binding {self.fact_id}")
        return self


class Calculation(StoryModel):
    """A quantity the draft computed rather than read (§12, §13.9).

    `formula_version_id` is null for an arithmetic derivation the ontology declares no formula
    for — a quarter-over-quarter delta — and set when the expression is the ontology's own, so
    §13.9 can recompute against the declared window rather than against the draft's arithmetic.

    **`period_surface` is here because §13.9 and §13.1 could not both be satisfied without it.**
    §13.9 gives a `calculated` sentence no `fact_bindings` — it cites nothing and carries this
    instead — and `FactBinding.period_surface` was the only place a draft could declare which
    words of its own text name a period. So *"the third quarter of 2022"* in a derived sentence
    was an `unbound_numeral` by construction, and §13.1's own text says a period surface is not
    a fact. Declared by the writer rather than inferred by the verifier from the inputs, for
    §12's reason: the verifier checks the declaration it was handed and never guesses one. It is
    checked exactly as a binding's is — resolved through §13.4's closed grammar and required to
    agree with **every** input observation's window — so a wrong period refuses.
    """

    operation: str
    input_observation_ids: tuple[str, ...] = ()
    expression: str
    result_rendered: str
    formula_version_id: str | None = None
    period_surface: str = ""


class DraftSentence(StoryModel):
    """One sentence, with the bindings that make it checkable (§12).

    Any numeral in `text` not covered by a binding, a calculation result or a period surface
    is `unbound_numeral` and refuses the draft (§13.1).
    """

    index: int
    text: str
    kind: SentenceKind
    fact_bindings: tuple[FactBinding, ...] = ()
    calculation: Calculation | None = None
    citations: tuple[CitationHandle, ...] = ()


class Draft(StoryModel):
    """A structured post, not prose (§12).

    `style_profile_id` is recorded because §12 requires two drafts of one package under two
    style profiles to have identical binding fact ids — a style change must not be able to
    change a number, and the profile has to be in the artifact for that test to mean anything.
    """

    candidate_id: str
    package_id: str
    title: str = ""
    sentences: tuple[DraftSentence, ...] = ()
    style_profile_id: str = ""
    prompt_version: str = ""
    model_id: str = ""

    @model_validator(mode="after")
    def _sentences_are_indexed_in_order(self) -> "Draft":
        expected = list(range(len(self.sentences)))
        actual = [sentence.index for sentence in self.sentences]
        if actual != expected:
            raise ValueError(
                f"sentence indexes {actual} are not 0..{len(self.sentences) - 1} in order; "
                "§13's findings address a sentence by index and a gap makes them unaddressable")
        return self

    def digestible_payload(self) -> dict[str, Any]:
        """What `draft_content_sha256` covers: the draft as written."""
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------------------
# Verification — §13
# ---------------------------------------------------------------------------------------


class VerificationFinding(StoryModel):
    """One thing a check decided, actionable enough to fix without re-deriving it (§13.17).

    `blocking` is stored rather than derived from `severity` alone because §13.17 makes the
    two separately meaningful — a model finding is `ADVISORY` unless it is one of the three
    that may REFUSE — but the two must agree, which the validator enforces: `REFUSE` is always
    blocking and `WARN`/`ANNOTATE` never are. "A REFUSE that did not block" is not a state
    this type can hold.

    `suggested_fact_ids` is §13.17's *"up to five package facts that would satisfy the
    sentence"*, so a rebind is a choice rather than a search.
    """

    code: str
    severity: Severity
    sentence_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    fact_ids: tuple[str, ...] = ()
    citation_ids: tuple[str, ...] = ()
    expected: str = ""
    observed: str = ""
    explanation: str = ""
    remedy: Remedy
    blocking: bool
    suggested_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _blocking_agrees_with_severity(self) -> "VerificationFinding":
        if self.severity is Severity.REFUSE and not self.blocking:
            raise ValueError(f"{self.code}: a REFUSE always blocks (§13.17)")
        if self.severity in (Severity.WARN, Severity.ANNOTATE) and self.blocking:
            raise ValueError(
                f"{self.code}: {self.severity.value} never blocks — it must be acknowledged in "
                "the accepted artifact, not used to reject the draft (§13.17)")
        if len(self.suggested_fact_ids) > 5:
            raise ValueError(
                f"{self.code}: {len(self.suggested_fact_ids)} suggestions; §13.17 caps them at "
                "five so a rejection reads as a choice rather than as a search")
        return self


class CheckResult(StoryModel):
    """One §13 check over one draft, with a denominator.

    `outcome` is derived from `examined` and `findings`, which is what §13.17 means by *"a
    draft with no numeric sentences must not report `numbers: PASS`"*: nothing examined is
    `NOT_APPLICABLE`, and the difference between "checked and clean" and "never ran" survives
    into the artifact.
    """

    name: str
    examined: int
    findings: tuple[VerificationFinding, ...] = ()

    @property
    def outcome(self) -> CheckOutcome:
        if self.examined == 0:
            return CheckOutcome.NOT_APPLICABLE
        return CheckOutcome.FAIL if self.findings else CheckOutcome.PASS

    def describe(self) -> str:
        return f"{self.outcome.value}  {self.name}  ({len(self.findings)}/{self.examined})"


class FactLedgerEntry(StoryModel):
    """One row of the evidence panel §13.17 requires on an accepted post.

    This is also what `story recheck` (§7) re-resolves against the current graph: an accepted
    post carries values and is never revisited, so without a ledger a rebuilt graph leaves a
    published file holding numbers from a run the freshness gate now rejects.
    """

    fact_id: str
    metric_id: str
    period_key: str
    value: float
    unit: str
    rendered: str
    sentence_index: int
    passage_id: str | None = None
    document_id: str | None = None
    source_url: str | None = None
    evidence_source_id: str | None = None


class CalculationLedgerEntry(StoryModel):
    """One derivation the draft made, recomputed and recorded (§13.9, §13.17)."""

    sentence_index: int
    operation: str
    input_observation_ids: tuple[str, ...] = ()
    expression: str
    recomputed_value: float
    rendered: str
    formula_version_id: str | None = None


class VerifiedDraft(StoryModel):
    """A draft that has been through the gate, whether or not it survived it (§13.17).

    `passed` is **derived, not stored** — the discipline
    `graph/core/verification_report.py:217` states. A report that could hold `passed: true`
    beside a blocking finding is a report that will eventually be written that way.

    Deliberately no validator forbidding blocking findings: a verification of a bad draft is
    still a verification, and refusing to construct one would leave the failing case with
    nowhere to be recorded. `RejectedDraft` is what a caller builds *from* a failed one.
    """

    candidate_id: str
    package_identity: PackageIdentity
    draft_content_sha256: str
    checks: tuple[CheckResult, ...] = ()
    #: Findings that did not attach to a check — §13.16's model adjudication, and anything
    #: raised about the draft as a whole.
    findings: tuple[VerificationFinding, ...] = ()
    fact_ledger: tuple[FactLedgerEntry, ...] = ()
    calculation_ledger: tuple[CalculationLedgerEntry, ...] = ()

    @property
    def all_findings(self) -> tuple[VerificationFinding, ...]:
        found = [finding for check in self.checks for finding in check.findings]
        found.extend(self.findings)
        return tuple(found)

    @property
    def passed(self) -> bool:
        return not any(finding.blocking for finding in self.all_findings)

    @property
    def acknowledged_warnings(self) -> tuple[VerificationFinding, ...]:
        return tuple(f for f in self.all_findings if f.severity is Severity.WARN)

    @property
    def annotations(self) -> tuple[VerificationFinding, ...]:
        return tuple(f for f in self.all_findings if f.severity is Severity.ANNOTATE)

    def check(self, name: str) -> CheckResult:
        """The named check, or `KeyError`. A test asserting on a check this verification never
        ran must fail loudly rather than silently assert nothing —
        `VerificationReport.check` makes the same argument."""
        for result in self.checks:
            if result.name == name:
                return result
        raise KeyError(f"no check named {name!r}; ran {[c.name for c in self.checks]}")


class RejectedDraft(StoryModel):
    """A draft the gate refused, with what to do about it (§13.17).

    Must carry at least one blocking finding: a rejection with nothing blocking is a draft
    that was accepted and filed in the wrong place, and §14's atomic finalisation would then
    write it into `<id>.rejected/` where nothing looks for it.

    `passed` is derived here too, and the validator is what makes it always `False` — asserted
    by construction rather than by a stored flag.
    """

    candidate_id: str
    package_identity: PackageIdentity
    draft_content_sha256: str
    checks: tuple[CheckResult, ...] = ()
    findings: tuple[VerificationFinding, ...] = ()

    @model_validator(mode="after")
    def _something_blocked(self) -> "RejectedDraft":
        if not any(f.blocking for f in self.all_findings):
            raise ValueError(
                f"{self.candidate_id}: a rejection with no blocking finding is an acceptance "
                "written into the wrong directory (§13.17, §14)")
        return self

    @property
    def all_findings(self) -> tuple[VerificationFinding, ...]:
        found = [finding for check in self.checks for finding in check.findings]
        found.extend(self.findings)
        return tuple(found)

    @property
    def passed(self) -> bool:
        return not any(finding.blocking for finding in self.all_findings)

    @property
    def remedies(self) -> tuple[Remedy, ...]:
        """The distinct verbs this rejection asks for, in first-seen order — §13.17's point:
        a human should be able to read five rejections as five verbs."""
        seen: list[Remedy] = []
        for finding in self.all_findings:
            if finding.blocking and finding.remedy not in seen:
                seen.append(finding.remedy)
        return tuple(seen)


# ---------------------------------------------------------------------------------------
# Run selection and provider results
# ---------------------------------------------------------------------------------------


class RunSelection(StoryModel):
    """Every CLI argument that changes *what is in a run* (§14), normalised.

    In the digest because the first draft's `story_run_id` covered neither these nor the
    budget parameters, so `story run --limit 3` and `story run --limit 20` minted the same id
    — and §1.6's atomic finalisation would then have replaced one run's directory with the
    other's, silently. That is the `extraction_run_id` defect this plan discovered, reproduced
    in its own design.
    """

    limit: int | None = None
    candidate_ids: tuple[str, ...] = ()
    detector_ids: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None

    def digest_parts(self) -> tuple[str, ...]:
        """Labelled, with the two id lists sorted — asking for two detectors in the other
        order is one selection, not two."""
        return (
            f"limit={'' if self.limit is None else self.limit}",
            "candidates=" + ",".join(sorted(self.candidate_ids)),
            "detectors=" + ",".join(sorted(self.detector_ids)),
            f"since={self.since or ''}",
            f"until={self.until or ''}",
        )


class GenerationResult(StoryModel):
    """One model answer, restated here rather than imported (§15.2).

    `extraction.contracts.GenerationResult` is the same shape, and `extraction.contracts` is
    not a shared upstream surface — `story/` may read `extraction.core.` and nothing else of
    it. `extraction/core/config.py:34-43` already records the repository's answer to exactly
    this trade: re-state the fields rather than reach across a boundary, and let a test assert
    the two field sets match.

    Two hashes for two questions: `raw_sha256` covers the whole envelope and is *not* stable
    across identical requests, because llama.cpp sends a fresh `id`, `created` and `timings`;
    `content_sha256` covers the answer alone and is the one determinism is checked against.
    """

    content: Mapping[str, Any]
    raw_content: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    raw_sha256: str
    content_sha256: str
    finish_reason: str
    attempts: int
    metadata: Mapping[str, Any] = {}


class HealthStatus(StoryModel):
    """Structured, never an exception (§15.1). A local server that is not running is an
    ordinary state for a caller to branch on."""

    ok: bool
    status: str
    detail: str | None = None


class RetrievalResult(StoryModel):
    """What one §9 tool returned. Bounded rows, and the reason when there are none.

    `truncated` is a field and not an inference: a caller that has to compare `len(rows)`
    against a cap it does not own cannot tell a full page from a bound that bit.
    """

    outcome: RetrievalOutcome
    rows: tuple[Mapping[str, Any], ...] = ()
    truncated: bool = False
    reason: str = ""


__all__ = [
    "CITATION_ADAPTER",
    "EVIDENCE_HANDLE_PREFIX",
    "PACKAGE_VERSION",
    "POLICY_VERSION",
    "Audience",
    "BudgetParameters",
    "Calculation",
    "CalculationLedgerEntry",
    "CandidateScore",
    "CausalLanguage",
    "CheckOutcome",
    "CheckResult",
    "CitationHandle",
    "ComparabilityFact",
    "CompatibilityDecision",
    "Conflict",
    "ConflictCluster",
    "Counterpoint",
    "Draft",
    "DraftSentence",
    "EditorialPlan",
    "EventParticipant",
    "EvidenceRole",
    "EvidenceRequest",
    "EvidenceSourceCitation",
    "FactBinding",
    "FactKind",
    "FactLedgerEntry",
    "GenerationResult",
    "HealthStatus",
    "IdentityFact",
    "KeyPoint",
    "MetricAmbiguity",
    "PackageBudget",
    "PackageIdentity",
    "PackagedDocument",
    "PackagedEvent",
    "PackagedEvidenceSource",
    "PackagedFact",
    "PackagedFormulaWindow",
    "PackagedMetric",
    "PackagedPassage",
    "PackagedRelationship",
    "PackagedSubject",
    "PackagedWarning",
    "PassageCitation",
    "PassageQuality",
    "PassageUnusableReason",
    "Remedy",
    "RetrievalOutcome",
    "RetrievalResult",
    "RetrievalTraceEntry",
    "RejectedDraft",
    "RunSelection",
    "SectionLedgerEntry",
    "SemanticFact",
    "SentenceKind",
    "Severity",
    "StatementClass",
    "StoryCandidate",
    "StoryEvidencePackage",
    "StoryModel",
    "TableCellRef",
    "UnusableEvidence",
    "UnusableReason",
    "VerificationFinding",
    "VerifiedDraft",
    "WarningCategory",
    "WarningKind",
    "canonical_json",
]
