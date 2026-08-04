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
PACKAGE_VERSION = "1.0.0"

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


class Severity(str, Enum):
    """§13.17's gate. `REFUSE` rejects the whole draft; `WARN` must be acknowledged in the
    accepted artifact; `ANNOTATE` must be rendered beside the claim; `ADVISORY` is a model
    finding that did not meet the bar to block."""

    REFUSE = "REFUSE"
    WARN = "WARN"
    ANNOTATE = "ANNOTATE"
    ADVISORY = "ADVISORY"


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
# Evidence package rows — §10
# ---------------------------------------------------------------------------------------


class PackagedSubject(StoryModel):
    """§10's `subject`. `resolved` is false for an entity the corpus names and never keys —
    §13.11 refuses a draft that renders one as a name."""

    entity_id: str
    entity_text: str
    resolved: bool
    labels: tuple[str, ...] = ()


class PackagedFact(StoryModel):
    """One observation as the model may see it (§10 `facts[]`).

    The evidence chain is optional on purpose. §13.7.2: an `:EvidenceSource` is a leaf with no
    `PART_OF` edge, so a fact evidenced by one has no `passage_id`, and `:MarketData` and
    `:Calculated` carry no `document_id` at all *"because nobody filed it"*. Zero such rows
    exist today; the shape exists so the XBRL lane cannot arrive by widening this type.
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
    """

    metric_id: str
    label: str
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
    """

    code: str
    severity: Severity
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

    Sixteen bounded sections. Nothing a model produced may enter one, and nothing outside one
    may reach a prompt: that is the whole of §2's line — the model chooses words, code chooses
    facts.

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
    metrics: tuple[PackagedMetric, ...] = ()
    formula_windows: tuple[PackagedFormulaWindow, ...] = ()
    events: tuple[PackagedEvent, ...] = ()
    relationships: tuple[PackagedRelationship, ...] = ()
    evidence_sources: tuple[PackagedEvidenceSource, ...] = ()
    primary_passages: tuple[PackagedPassage, ...] = ()
    context_passages: tuple[PackagedPassage, ...] = ()
    explanatory_passages: tuple[PackagedPassage, ...] = ()
    counter_evidence: tuple[PackagedPassage, ...] = ()
    warnings: tuple[PackagedWarning, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    compatibility: tuple[CompatibilityDecision, ...] = ()
    documents: tuple[PackagedDocument, ...] = ()
    retrieval_trace: tuple[RetrievalTraceEntry, ...] = ()
    budget: PackageBudget

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
    """

    operation: str
    input_observation_ids: tuple[str, ...] = ()
    expression: str
    result_rendered: str
    formula_version_id: str | None = None


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
    "CompatibilityDecision",
    "Conflict",
    "ConflictCluster",
    "Counterpoint",
    "Draft",
    "DraftSentence",
    "EditorialPlan",
    "EventParticipant",
    "EvidenceRequest",
    "EvidenceSourceCitation",
    "FactBinding",
    "FactLedgerEntry",
    "GenerationResult",
    "HealthStatus",
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
    "Remedy",
    "RetrievalOutcome",
    "RetrievalResult",
    "RetrievalTraceEntry",
    "RejectedDraft",
    "RunSelection",
    "SentenceKind",
    "Severity",
    "StatementClass",
    "StoryCandidate",
    "StoryEvidencePackage",
    "StoryModel",
    "UnusableEvidence",
    "UnusableReason",
    "VerificationFinding",
    "VerifiedDraft",
    "canonical_json",
]
