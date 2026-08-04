"""What a canonical series is, and when two of its points may be compared (§6.1, §6.9).

Responsibility: the vocabulary every detector stands on — one observation as this package
handles it, one canonical point, one series, and `comparable(a, b)`, which answers R1–R10 with
a value and never with an exception. It owns no policy: §6.1's clustering, majority and
representative rules are `story/stages/detection/canonicalization.py`'s, and this module holds
only the types that policy produces and the tolerance table the policy and R8 share.

**No model, no graph, no clock.** Everything here is a pure function of its arguments plus the
ontology, which is loaded lazily and cached. `core/` may not import a stage, so nothing in this
file can reach the retriever; a `CanonicalPoint` arrives already built.

**Refusals are values.** `comparable` returns `Ok` or `Refuse` and raises nothing. §11's
planner and §6's detectors both have to branch on "these two numbers may not be subtracted",
and an exception makes an ordinary editorial state indistinguishable from a broken run. Every
`Refuse` names the rule, a machine-readable reason and a detail that names **both sides** —
`§13` requires a writer to be told why, and *"incomparable"* with no operands is not a finding
anyone can act on.

**The ontology is authoritative for every compatibility decision** (correction C4). R2's
groups, R6's formula windows and R9's cohort basis are all read from the loaded
`OntologyDefinitions` and its registry, never from a `:Metric` node — which carries no
`distinct_from`, no `reconciles_to` and no percentage bound to read *(WORKSTREAM_BOUNDARY
§4.2 point 3)*. R9's cohort set in particular is **derived**, not listed: a metric is a cohort
measure when one of its formula's adjustment components says so, which is how
`contribution_profit` and `contribution_profit_after_interest` arrive here and how a third
would arrive without a code change. `formulas.yaml` is the source of the sentence R9 exists
for: *"a Contribution Profit value is NOT a slice of any single period's expenses."*
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Mapping, Sequence

from ontology import load_ontology
from ontology.contracts import ConceptRegistry, OntologyDefinitions
from story.core.periods import (
    SHAPE_STEP_MONTHS,
    PeriodShape,
    StoryPeriod,
    months_between,
    story_period,
)

#: `canon-policy` §6.1 step 2, as a table rather than as a branch inside the clusterer, because
#: R8 reads the same numbers: *"for a MOVEMENT claim, `|A − B|` must exceed `max(tol(A),
#: tol(B))`, else the two filings just rounded differently."* Two consumers, one table.
#:
#: A dollar figure printed in millions carries ±$1M of rounding — `adjusted_ebitda 2021Q2` is
#: filed as both `25,579,000` (a thousands table) and `25,000,000` (a millions one), and those
#: are one reading *(measured: 10 of the run's 36 multi-valued slots differ by more than 1% and
#: **all ten** are exactly this)*. A percentage printed to one decimal carries ±0.1. A count is
#: a count, so its tolerance is one home, one market, one contract.
PERCENT_TOLERANCE = 0.1
COUNT_TOLERANCE = 1.0
SCALE_TOLERANCE: Mapping[str, float] = {"millions": 1e6, "thousands": 1e3}

#: Decimal places a difference is rounded to before it is compared with a tolerance, a threshold
#: or another difference. `7.4 − 7.3` is `0.10000000000000053` in IEEE 754 and `gaap_gross_margin`
#: holds exactly those two values in adjacent quarters, so R8's `|A − B| <= tol` was deciding one
#: printed unit on a residue at the seventeenth digit — D9, and the one case in this corpus where
#: this module's code and its own docstring disagreed. Nine places is far below every tolerance in
#: the table above (0.1 for a percentage, $1 for a count) and far above the residue, so rounding
#: here cannot change a decision the data supports.
#:
#: **It lives in `core/` and not in `story/stages/detection/detector_config.py`**, which is where
#: every detector reads it from, because `core/` may not import a stage
#: (`test_story_package_structure.py::test_core_never_imports_a_stage_a_provider_or_the_cli`) and
#: because R8 and the detectors must round to the same number of places or they can disagree
#: about what a movement is. `detector_config` binds this name rather than restating the literal.
DELTA_PRECISION = 9

#: Least precise last. §6.1 step 5 orders the representative by `(scale_precision, filing_date,
#: observation_id)` with *"`thousands|units < millions < None`"*: a figure filed in a thousands
#: table states more digits than the same figure filed in a millions table, and a figure with no
#: declared scale states the least of all because nothing says what its digits mean.
SCALE_PRECISION: Mapping[str | None, int] = {"units": 0, "thousands": 0, "millions": 1}
UNKNOWN_SCALE_PRECISION = 2

#: The lanes §6.1's quarantine rule applies to. The plan writes `lane == "narrative"`; the value
#: the graph actually stores is `ontology.core.values.SourceLane.NORMALIZED_NARRATIVE`, i.e.
#: `"normalized_narrative"`, on 14 of 2,704 observations *(verified live 2026-08-03)*. Recorded
#: rather than silently fixed: a guard written against the plan's spelling would match nothing
#: and would look like a rule that simply never fires.
NARRATIVE_LANES = frozenset({"normalized_narrative"})

#: The warning R9 attaches rather than refusing. §10.1 carries it onto the candidate.
COHORT_VS_PERIOD_BASIS = "cohort_vs_period_basis"

#: The word `formulas.yaml` uses for the accounting fact R9 exists to protect. Matched against
#: an adjustment component's `note`, so the cohort set is the ontology's statement about itself
#: rather than a list in this file that the ontology could silently outgrow.
_COHORT_MARKER = "cohort"


class CanonicalStatus(str, Enum):
    """§6.1 steps 3–4. A `CONFLICT` slot exists and holds no value."""

    OK = "ok"
    RESOLVED_BY_MAJORITY = "resolved_by_majority"
    CONFLICT = "conflict"


class ClaimKind(str, Enum):
    """What a comparison is *for*, because R8, R9 and R10 apply to different claims.

    A `LEVEL` comparison — *"inventory was 16,873 homes then and 3,558 homes later"* — needs
    R1–R7 and nothing else: it asserts two readings, not a step between them. `MOVEMENT` and
    `ACCELERATION` assert a step, so they pick up R8's tolerance floor and R10's adjacency.
    `DIVERGENCE` is the only kind that may name two different metrics (R2), and it never
    asserts a step, so R10 does not apply to it.

    This is why §6.3's F4 — *"16,873 homes → 3,558, −78.9% in three quarters"* — is a `LEVEL`
    claim with a stated span and not a `MOVEMENT`: R10 would refuse it as a step claim, and it
    is not one.
    """

    LEVEL = "level"
    MOVEMENT = "movement"
    ACCELERATION = "acceleration"
    DIVERGENCE = "divergence"


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """One graph observation, flattened into what §6.1 and §6.9 need from it.

    A story-owned type rather than a retrieval row, for two reasons. Retrieval returns
    `Mapping[str, Any]` and a policy written against raw dicts cannot be type-checked or built
    by hand in a test; and the fields come from **two** tools — `get_metric_history` carries the
    period and the value, while `quoted_text` and `filing_date` live behind
    `get_fact_evidence`, because C3 puts the quote on the `EVIDENCED_BY` edge and the filing
    date on the `:Document`. Merging them is `canonicalization.py`'s job; holding the merged
    shape is this one's.

    `quoted_text` and `filing_date` are optional and their absence is *reported*, never assumed
    benign: a record with no quote cannot be put through the quarantine test, and a record with
    no filing date cannot claim to be the earliest.
    """

    observation_id: str
    metric_id: str
    subject_entity_id: str
    period: StoryPeriod
    value: float
    unit: str
    scale: str | None = None
    currency: str | None = None
    source_lane: str = ""
    validation_state: str = ""
    document_id: str | None = None
    passage_id: str | None = None
    filing_date: str | None = None
    quoted_text: str | None = None
    warning_codes: tuple[str, ...] = ()
    ambiguity_codes: tuple[str, ...] = ()

    @property
    def slot_key(self) -> tuple[str, str]:
        """§6.1's fact-slot key: `(metric_id, period_key)`."""
        return (self.metric_id, self.period.key)

    @property
    def tolerance(self) -> float:
        return presentation_tolerance(self.unit, self.scale)

    @property
    def scale_precision(self) -> int:
        return SCALE_PRECISION.get(self.scale, UNKNOWN_SCALE_PRECISION)

    @property
    def is_narrative(self) -> bool:
        return self.source_lane in NARRATIVE_LANES


@dataclass(frozen=True, slots=True)
class ValueCluster:
    """One reading of a slot: the observations that agree within presentation tolerance.

    `document_ids` is the field §6.1 step 4 counts. It is distinct documents and not distinct
    observations deliberately — six rows of one filing are one source, and a majority rule that
    counted rows would let a single well-formatted table outvote the rest of the corpus.
    """

    value: float
    observation_ids: tuple[str, ...]
    document_ids: tuple[str, ...]

    @property
    def n_docs(self) -> int:
        return len(self.document_ids)


@dataclass(frozen=True, slots=True)
class CanonicalPoint:
    """One fact-slot, canonicalised. `value` is `None` exactly when the status is `CONFLICT`.

    §6.1 step 6's six provenance fields are not decoration: `supporting_observation_ids` is what
    a candidate digests (§6.11), `minority_observation_ids` is what §10.1 has to disclose,
    `quarantined_observation_ids` is what raises a `lane_defect` warning, and
    `n_docs`/`first_filed`/`last_filed` are ranking inputs (§6.10's corroboration term) that
    must not be recomputed from a package by anything downstream.
    """

    metric_id: str
    period: StoryPeriod
    subject_entity_id: str
    status: CanonicalStatus
    value: float | None
    unit: str
    scale: str | None
    currency: str | None
    representative_observation_id: str | None
    supporting_observation_ids: tuple[str, ...] = ()
    minority_observation_ids: tuple[str, ...] = ()
    quarantined_observation_ids: tuple[str, ...] = ()
    n_docs: int = 0
    first_filed: str | None = None
    last_filed: str | None = None
    warnings: tuple[str, ...] = ()
    clusters: tuple[ValueCluster, ...] = ()
    #: Distinct **raw** values among the observations that survived quarantine, before
    #: tolerance clustering. §6.1's headline census counts slots by this and not by cluster
    #: count — *"537 slots exist over 2,704 observations; 36 hold more than one distinct
    #: value"* — and the whole claim of step 2 is that those 36 collapse to `len(clusters) == 1`.
    #: Keeping both numbers on the point is what makes that claim checkable after the fact.
    distinct_values: int = 0

    @property
    def slot_key(self) -> tuple[str, str]:
        return (self.metric_id, self.period.key)

    @property
    def period_key(self) -> str:
        return self.period.key

    @property
    def has_value(self) -> bool:
        """R7's test, as a property. A `CONFLICT` slot emits no value (§6.1 step 4)."""
        return self.status is not CanonicalStatus.CONFLICT and self.value is not None

    @property
    def tolerance(self) -> float:
        return presentation_tolerance(self.unit, self.scale)


@dataclass(frozen=True, slots=True)
class CanonicalSeries:
    """One metric's canonical points, ordered, with the adjacency R10 asks about.

    Ordered by `(anchor_date, period_key)` — the same order `cypher.METRIC_HISTORY` returns
    rows in, so a series and the rows it was built from cannot disagree about what comes first.

    **Every slot is a member, including the conflicted ones.** A series that silently dropped a
    `CONFLICT` slot would make two points look consecutive across a hole the run knows about,
    which is the mistake R10 exists to catch; `valued_points` is where the drop happens, and it
    happens visibly.
    """

    metric_id: str
    points: tuple[CanonicalPoint, ...] = ()

    def point(self, period_key: str) -> CanonicalPoint | None:
        for candidate in self.points:
            if candidate.period.key == period_key:
                return candidate
        return None

    def valued_points(self, shape: PeriodShape | None = None) -> tuple[CanonicalPoint, ...]:
        """The points a claim may be built on: canonical, and optionally of one shape."""
        return tuple(
            point
            for point in self.points
            if point.has_value and (shape is None or point.period.shape is shape)
        )

    def points_between(
        self, left: CanonicalPoint, right: CanonicalPoint
    ) -> tuple[CanonicalPoint, ...]:
        """Same-shape canonical points strictly between two points, in series order.

        Same-shape because a quarterly series and the fiscal year that contains it are not
        neighbours: `2022Q2 → FY2022 → 2022Q3` would make every quarter-to-quarter step
        non-adjacent, and R10 would refuse the whole plan.
        """
        anchors = sorted(
            filter(None, (left.period.anchor_date, right.period.anchor_date))
        )
        if len(anchors) != 2:
            return ()
        low, high = anchors
        return tuple(
            point
            for point in self.valued_points(left.period.shape)
            if point.period.anchor_date is not None
            and low < point.period.anchor_date < high
            and point.period.key not in {left.period.key, right.period.key}
        )


@dataclass(frozen=True, slots=True)
class ComparabilityWarning:
    """A comparison that is permitted and must be disclosed (§10.1). R9's channel."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class Ok:
    """The comparison is permitted, with any warnings it must carry."""

    warnings: tuple[ComparabilityWarning, ...] = ()

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Refuse:
    """The comparison is not permitted, with the rule that said so and both operands named."""

    rule: str
    reason: str
    detail: str

    def __bool__(self) -> bool:
        return False


Comparability = Ok | Refuse


@dataclass(frozen=True, slots=True)
class ComparabilityAuthority:
    """Everything R2, R6 and R9 need from the ontology, resolved once and cached.

    Built by `default_authority()` from `load_ontology()`, or by `authority_from` in a test that
    wants a hand-built ontology. Injected rather than reached for, so `comparable` has no import
    of a composition root and no file access of its own.
    """

    groups: Mapping[str, frozenset[str]] = field(default_factory=dict)
    co_components: Mapping[str, frozenset[str]] = field(default_factory=dict)
    cohort_metrics: frozenset[str] = frozenset()
    versioned_metrics: frozenset[str] = frozenset()
    registry: ConceptRegistry | None = None

    def resolve_version(self, metric_id: str, as_of: str | None) -> str | None:
        """The formula version in force for a metric on a date, or `None`.

        **§6.9 records R6 as undefined for instants** — `resolve_version(metric, period_end)`
        has no answer when `period_end` is null — and asks for a decision. **The decision: an
        instant resolves on its own `instant_date`.** R6's own sentence is *"resolution is BY
        OBSERVATION DATE"*, and an instant is nothing but an observation date; the null is an
        artefact of `period_end` being the field the rule happened to name. `comparable` passes
        `StoryPeriod.anchor_date`, which is `period_end` for a duration and `instant_date` for
        an instant, so the two cases are one call.

        Harmless today either way — `adjusted_gross_profit` is the only versioned metric and it
        is a duration metric — and it needs to be settled before `inventory_balance` or
        `borrowing_capacity` is versioned, which is why it is settled here rather than left.
        """
        if self.registry is None or metric_id not in self.versioned_metrics:
            return None
        formula = self.registry.formula_for(metric_id, as_of)
        return None if formula is None else formula.concept_id

    def is_versioned(self, metric_id: str) -> bool:
        return metric_id in self.versioned_metrics


def presentation_tolerance(unit: str | None, scale: str | None) -> float:
    """§6.1 step 2's `tol`, from the unit and scale an observation actually carries.

    Keyed on both because a USD figure's rounding is a property of how it was *printed*, not of
    what it measures: the same `adjusted_ebitda` quarter is exact to the dollar in a thousands
    table and to the million in a millions one. Counts and percentages have no scale that
    varies in this corpus — every `homes`, `markets` and `percent` row is `units` or carries no
    scale at all *(measured: 1,163 percent/units, 358 homes/units, 92 markets/units, and 94 rows
    with no scale, all of them counts or percentages)* — so their tolerance is a constant.
    """
    if unit == "percent":
        return PERCENT_TOLERANCE
    if unit is not None and unit.startswith("USD"):
        return SCALE_TOLERANCE.get(scale or "", COUNT_TOLERANCE)
    return COUNT_TOLERANCE


def within_tolerance(left: float, right: float, tolerance: float) -> bool:
    """One reading, or two. `<=` and not `<`: a difference of exactly one printed unit is the
    rounding, not a movement.

    **The difference is rounded to `DELTA_PRECISION` before the comparison, and that is D9's
    whole fix.** On raw subtraction the function did not honour the sentence above: `7.4 − 7.3`
    is `0.10000000000000053`, a hair over `PERCENT_TOLERANCE`, so a one-printed-unit step read as
    a movement. Blast radius measured across every canonical series in the run: **exactly one
    case**, `gaap_gross_margin 2020Q1 → 2020Q2`, whose survival was the eighth of §6.6 D3's eight
    quarterly accelerations. The tolerances themselves are unchanged and `<=` is unchanged; the
    residue is the only thing removed.
    """
    return round(abs(left - right), DELTA_PRECISION) <= tolerance


@lru_cache(maxsize=1)
def default_authority() -> ComparabilityAuthority:
    """The shipped ontology's answer to R2, R6 and R9.

    Cached because `load_ontology()` parses and validates YAML; lazy because `core/` must stay
    importable with no file on disk. Restated rather than shared with
    `story/stages/retrieval/metric_metadata.py`'s `default_registry()` for a structural reason,
    not a stylistic one: `tests/story/test_story_package_structure.py::
    test_no_stage_imports_another_stage` forbids `story.stages.detection` importing
    `story.stages.retrieval`, and putting the loader in `core/` — where both stages may reach
    it — is the version of "share it" that the layout permits.
    """
    ontology = load_ontology()
    return authority_from(ontology.definitions, ontology.registry)


def authority_from(
    definitions: OntologyDefinitions, registry: ConceptRegistry
) -> ComparabilityAuthority:
    """Build the authority from definitions and an index over them.

    Two arguments rather than one `Ontology`, because `definitions` is not on the `Ontology`
    protocol — it lives on the concrete `LoadedOntology` — and a test that hand-builds an
    `OntologyDefinitions` with two metrics and one formula version should not have to construct
    a whole ontology to exercise R2, R6 and R9.
    """
    groups: dict[str, set[str]] = {}
    for group in definitions.constraints.mutually_distinct_groups:
        for concept_id in group.concept_ids:
            groups.setdefault(concept_id, set()).add(group.group_id)

    co_components: dict[str, set[str]] = {}
    cohort: set[str] = set()
    versioned: set[str] = set()
    for formula in definitions.formula_versions:
        versioned.add(formula.metric_id)
        members = set(formula.component_metrics)
        for member in members:
            co_components.setdefault(member, set()).update(members - {member})
        if any(_COHORT_MARKER in (part.note or "").lower()
               for part in formula.adjustment_components):
            cohort.add(formula.metric_id)

    return ComparabilityAuthority(
        groups={key: frozenset(value) for key, value in groups.items()},
        co_components={key: frozenset(value) for key, value in co_components.items()},
        cohort_metrics=frozenset(cohort),
        versioned_metrics=frozenset(versioned),
        registry=registry,
    )


def comparable(
    left: CanonicalPoint,
    right: CanonicalPoint,
    *,
    claim: ClaimKind = ClaimKind.LEVEL,
    authority: ComparabilityAuthority | None = None,
    series: CanonicalSeries | None = None,
) -> Comparability:
    """§6.9's R1–R10, in order, as a value.

    `series` is required for a `MOVEMENT` or `ACCELERATION` claim and is otherwise unused:
    R10's question — *are these two points consecutive?* — cannot be answered from the points
    alone, and a signature that made it optional for a step claim would let a detector skip the
    rule by forgetting an argument. It is refused rather than skipped.
    """
    resolved = default_authority() if authority is None else authority
    for rule in (
        _r1_subject,
        _r2_metric,
        _r3_shape,
        _r4_unit,
        _r5_currency,
        _r6_formula,
        _r7_canonical,
        _r8_tolerance,
        _r10_adjacency,
    ):
        refusal = rule(left, right, claim, resolved, series)
        if refusal is not None:
            return refusal
    return Ok(warnings=_r9_basis(left, right, claim, resolved))


# -- the ten rules ------------------------------------------------------------------------
#
# One function each, same signature, applied in order by `comparable`. A dispatch table rather
# than nine nested `if`s because the rules are numbered in the plan and a reader checking R6
# should find one function called `_r6_formula`, not a branch in the middle of a long body.


def _r1_subject(
    left: CanonicalPoint,
    right: CanonicalPoint,
    _claim: ClaimKind,
    _authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    if left.subject_entity_id == right.subject_entity_id:
        return None
    return Refuse(
        "R1",
        "SUBJECT_MISMATCH",
        f"{left.metric_id} {left.period.key} is about {left.subject_entity_id!r} and "
        f"{right.metric_id} {right.period.key} is about {right.subject_entity_id!r}; §13.6 "
        "makes any subject other than the one under discussion an automatic refusal",
    )


def _r2_metric(
    left: CanonicalPoint,
    right: CanonicalPoint,
    claim: ClaimKind,
    authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    """Same metric, or a declared cross-metric intent between two related ones.

    **Note the direction**, because it reads backwards at first: shared membership of a
    `mutually_distinct_groups` group makes two metrics comparable *as a divergence pair*
    precisely because the ontology says they must never be merged. `adjusted_gross_margin`
    against `gaap_gross_margin` is §6.3's F3 and both are in `margin_measures`; a home count
    against a margin shares no group and is `UNRELATED_METRICS`.
    """
    if left.metric_id == right.metric_id:
        return None
    if claim is not ClaimKind.DIVERGENCE:
        return Refuse(
            "R2",
            "METRIC_MISMATCH",
            f"{left.metric_id} and {right.metric_id} are different metrics and a "
            f"{claim.value} claim compares one metric with itself; a cross-metric comparison "
            "must declare ClaimKind.DIVERGENCE",
        )
    shared_groups = authority.groups.get(left.metric_id, frozenset()) & authority.groups.get(
        right.metric_id, frozenset()
    )
    if shared_groups or right.metric_id in authority.co_components.get(
        left.metric_id, frozenset()
    ):
        return None
    return Refuse(
        "R2",
        "UNRELATED_METRICS",
        f"{left.metric_id} and {right.metric_id} appear together in no formulas.yaml "
        f"component_metrics list and share no mutually_distinct_groups group "
        f"({sorted(authority.groups.get(left.metric_id, ())) or 'no group'} vs "
        f"{sorted(authority.groups.get(right.metric_id, ())) or 'no group'})",
    )


def _r3_shape(
    left: CanonicalPoint,
    right: CanonicalPoint,
    _claim: ClaimKind,
    _authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    """Same shape, and neither of them `other`.

    §13.4 calls matching `period_end` alone catastrophic and measures why: 188
    `(metric, period_end)` pairs carry more than one period key, and `adjusted_ebitda` ending
    `2022-09-30` is `+$183M` for the nine-month year-to-date and `−$211M` for Q3.
    """
    if PeriodShape.OTHER in (left.period.shape, right.period.shape):
        unnameable = [
            point.period.key
            for point in (left, right)
            if point.period.shape is PeriodShape.OTHER
        ]
        return Refuse(
            "R3",
            "UNCOMPARABLE_SHAPE",
            f"{', '.join(unnameable)} is not a quarter, a fiscal year, a year-to-date window "
            f"or an instant; comparing {left.period.key} with {right.period.key} would compare "
            "windows of different lengths",
        )
    if left.period.shape is right.period.shape:
        return None
    return Refuse(
        "R3",
        "UNCOMPARABLE_SHAPE",
        f"{left.period.key} is a {left.period.shape.value} and {right.period.key} is a "
        f"{right.period.shape.value}; §13.4 refuses quarter-versus-year-to-date conflation "
        "with its own code because that is the finding a writer can act on",
    )


def _r4_unit(
    left: CanonicalPoint,
    right: CanonicalPoint,
    _claim: ClaimKind,
    _authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    """Unit, and deliberately **not** scale: the value is already scale-applied (§6.9 R4)."""
    if left.unit == right.unit:
        return None
    return Refuse(
        "R4",
        "UNIT_MISMATCH",
        f"{left.metric_id} {left.period.key} is in {left.unit!r} and {right.metric_id} "
        f"{right.period.key} is in {right.unit!r}",
    )


def _r5_currency(
    left: CanonicalPoint,
    right: CanonicalPoint,
    _claim: ClaimKind,
    _authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    """Both `None` is agreement — `currency` is present on 997 of 2,704 observations (C2), and
    a rule that treated absence as disagreement would refuse every percentage and every count.
    """
    if left.currency == right.currency:
        return None
    return Refuse(
        "R5",
        "CURRENCY_MISMATCH",
        f"{left.metric_id} {left.period.key} is denominated in {left.currency!r} and "
        f"{right.metric_id} {right.period.key} in {right.currency!r}",
    )


def _r6_formula(
    left: CanonicalPoint,
    right: CanonicalPoint,
    _claim: ClaimKind,
    authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    """No metric involved may have been redefined between the two dates, and no duration may
    straddle a redefinition.

    **Correction to §6.9 as written, and it matters.** R6 reads *"`resolve_version(metric,
    period_end)` must agree"*, which, taken literally on a cross-metric pair, compares
    `adjusted_gross_margin`'s version id with `gaap_gross_margin`'s. Those are `…_v1` on both
    sides but different *concepts* — every metric's formula versions carry their own ids — so
    the literal rule can never be satisfied by two different metrics, and it refuses §6.3's F3,
    the plan's own recommended spike, on a technicality. **R6 is therefore evaluated per metric
    across both dates**: for each metric named by the comparison, the formula in force at one
    period must be the formula in force at the other. On a same-metric comparison — which is
    every pair §6.9 quantifies — this is identical to the literal reading, so the R6 census is
    unaffected by the correction.

    Quantified in §6.9 against the only metric this bites on: `adjusted_gross_profit`, v1
    `2020-01-01..2021-12-31` and v2 `2022-01-01→`. It refuses the one adjacent quarter step
    (2021Q4→2022Q1) and the year-over-year steps that cross the boundary — exactly the
    comparisons the 2022 crisis story wants — and the writer is told why, quoting the
    restructuring adjustment that v2 drops.

    **`FORMULA_VERSION_UNDECLARED` is this module's own addition**, for a period that precedes
    every declared window. `adjusted_gross_profit` has 3 such slots and 5 such observations
    (`FY2018`, `FY2019`, `2019Q4`) — the metric's formulas begin at `2020-01-01` and its
    pre-2020 values are filed comparatives. C4 forbids a silent fallback to metric metadata that
    is not there, and clamping to the earliest window would assert that v1's restructuring
    adjustment applied in 2018, which the ontology does not say. §6.9's own census counts those
    three slots as v1 (*"v1 … 17 slots, 47 observations"*); this module counts them as
    undeclared, and the report records both readings.
    """
    for point in (left, right):
        straddle = _straddles(point, authority)
        if straddle is not None:
            return straddle
    dates = (left.period.anchor_date, right.period.anchor_date)
    for metric_id in dict.fromkeys((left.metric_id, right.metric_id)):
        if not authority.is_versioned(metric_id):
            continue
        versions = [authority.resolve_version(metric_id, when) for when in dates]
        if None in versions:
            undeclared = [
                point.period.key
                for point, version in zip((left, right), versions)
                if version is None
            ]
            return Refuse(
                "R6",
                "FORMULA_VERSION_UNDECLARED",
                f"{metric_id} declares dated formula versions and none is in force for "
                f"{', '.join(undeclared)}; that period precedes or follows every declared "
                "window, so there is no definition for the other side to agree with",
            )
        if versions[0] != versions[1]:
            return Refuse(
                "R6",
                "FORMULA_VERSION_MISMATCH",
                f"{metric_id} is defined by {versions[0]} at {left.period.key} and by "
                f"{versions[1]} at {right.period.key}; the two are different definitions and "
                "their difference is not a movement",
            )
    return None


def _straddles(point: CanonicalPoint, authority: ComparabilityAuthority) -> Refuse | None:
    """A duration whose start and end fall in different formula windows (§6.9 R6).

    An instant cannot straddle — it is one date — which is the other half of the instant
    decision recorded on `ComparabilityAuthority.resolve_version`.
    """
    if point.period.period_start is None or point.period.period_end is None:
        return None
    if not authority.is_versioned(point.metric_id):
        return None
    at_start = authority.resolve_version(point.metric_id, point.period.period_start)
    at_end = authority.resolve_version(point.metric_id, point.period.period_end)
    if at_start == at_end:
        return None
    return Refuse(
        "R6",
        "FORMULA_VERSION_STRADDLES",
        f"{point.metric_id} {point.period.key} runs from {point.period.period_start} "
        f"({at_start}) to {point.period.period_end} ({at_end}); one window covered by two "
        "definitions has no single formula to resolve to",
    )


def _r7_canonical(
    left: CanonicalPoint,
    right: CanonicalPoint,
    _claim: ClaimKind,
    _authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    unusable = [point for point in (left, right) if not point.has_value]
    if not unusable:
        return None
    return Refuse(
        "R7",
        "NOT_CANONICAL",
        "; ".join(
            f"{point.metric_id} {point.period.key} is {point.status.value} and emits no value"
            for point in unusable
        ),
    )


def _r8_tolerance(
    left: CanonicalPoint,
    right: CanonicalPoint,
    claim: ClaimKind,
    _authority: ComparabilityAuthority,
    _series: CanonicalSeries | None,
) -> Refuse | None:
    """A movement smaller than the coarser side's printed unit is rounding, not news.

    The difference printed in the refusal is the **rounded** one `within_tolerance` decided on.
    On the raw subtraction the sentence contradicted the verdict it was explaining:
    `gaap_gross_margin 2020Q1 → 2020Q2` refused with *"a difference of 0.10000000000000053
    against a presentation tolerance of 0.1"*, which reads as a movement that cleared the bar.
    """
    if claim not in (ClaimKind.MOVEMENT, ClaimKind.ACCELERATION):
        return None
    if left.value is None or right.value is None:
        return None
    tolerance = max(left.tolerance, right.tolerance)
    if not within_tolerance(left.value, right.value, tolerance):
        return None
    difference = round(abs(left.value - right.value), DELTA_PRECISION)
    return Refuse(
        "R8",
        "WITHIN_PRESENTATION_TOLERANCE",
        f"{left.metric_id} moved from {right.value} ({right.period.key}) to {left.value} "
        f"({left.period.key}), a difference of {difference} against a "
        f"presentation tolerance of {tolerance}; the two filings rounded differently",
    )


def _r9_basis(
    left: CanonicalPoint,
    right: CanonicalPoint,
    claim: ClaimKind,
    authority: ComparabilityAuthority,
) -> tuple[ComparabilityWarning, ...]:
    """A cohort measure differenced against a period measure, disclosed rather than refused.

    R1–R8 as first drafted permitted the `contribution_profit` ↔ `adjusted_gross_profit` pair
    silently: both are USD, both are quarters, both are in `profit_measures`, so a divergence
    detector would have subtracted them and called the residual an operating result. It is not
    one — `formulas.yaml` states that *"a Contribution Profit value is NOT a slice of any single
    period's expenses"*, because prior-period holding costs attach to the period of sale.

    A warning and not a refusal, matching the plan's wording: the pair **is** the story in
    §6.6's D3, and the disclosure is what stops a writer describing a cohort residual as a
    quarter's spending. §10.1 carries the code onto the candidate.
    """
    if claim is not ClaimKind.DIVERGENCE or left.metric_id == right.metric_id:
        return ()
    cohort = [p.metric_id for p in (left, right) if p.metric_id in authority.cohort_metrics]
    period = [
        p.metric_id for p in (left, right) if p.metric_id not in authority.cohort_metrics
    ]
    if not cohort or not period:
        return ()
    return (
        ComparabilityWarning(
            COHORT_VS_PERIOD_BASIS,
            f"{', '.join(cohort)} is a cohort measure — costs from earlier periods attach to "
            f"the period of sale — and {', '.join(period)} is a period measure; their "
            "difference is not a slice of any single period's expenses (formulas.yaml)",
        ),
    )


def _r10_adjacency(
    left: CanonicalPoint,
    right: CanonicalPoint,
    claim: ClaimKind,
    _authority: ComparabilityAuthority,
    series: CanonicalSeries | None,
) -> Refuse | None:
    """Consecutive in the canonical series, not merely adjacent in a sparse one.

    Two questions, and both have to be answered yes, because either alone lets a wrong claim
    through. *Nothing between them* alone is what `pct_homes_on_market_gt_120_days` satisfies
    between `2021-12-31` (8%) and `2022-12-31` (55%) — adjacent rows, a four-quarter hole, and
    a **+47 pp** "quarterly jump" that never happened. *One step apart on the calendar* alone
    would let a claim skip a quarter the run does hold, if a detector handed in the wrong pair.

    The step comes from `SHAPE_STEP_MONTHS`, and the refusal reports the gap it measured, so
    the answer to "why was this refused" is a number rather than a rule name.
    """
    if claim not in (ClaimKind.MOVEMENT, ClaimKind.ACCELERATION):
        return None
    if series is None:
        return Refuse(
            "R10",
            "SERIES_UNAVAILABLE",
            f"a {claim.value} claim on {left.metric_id} between {right.period.key} and "
            f"{left.period.key} needs the canonical series to check consecutiveness, and none "
            "was supplied; refused rather than skipped, because a rule that can be dropped by "
            "omitting an argument is not a rule",
        )
    if series.metric_id != left.metric_id:
        return Refuse(
            "R10",
            "SERIES_UNAVAILABLE",
            f"the series supplied is {series.metric_id!r} and the claim is about "
            f"{left.metric_id!r}",
        )
    between = series.points_between(left, right)
    if between:
        return Refuse(
            "R10",
            "SERIES_NOT_ADJACENT",
            f"{', '.join(point.period.key for point in between)} lies between "
            f"{right.period.key} and {left.period.key} in {left.metric_id}'s canonical series, "
            f"so they are not consecutive {left.period.shape.value} points",
        )
    expected = SHAPE_STEP_MONTHS.get(left.period.shape)
    earlier, later = sorted(
        (left, right), key=lambda point: point.period.anchor_date or ""
    )
    gap = months_between(earlier.period.anchor_date or "", later.period.anchor_date or "")
    if expected is None or gap is None or gap != expected:
        return Refuse(
            "R10",
            "SERIES_GAP",
            f"{earlier.period.key} and {later.period.key} are {gap} months apart in "
            f"{left.metric_id}'s canonical series, not the {expected} a "
            f"{left.period.shape.value} step is; nothing lies between them because the run "
            "holds nothing there, and a step claim across a hole is a claim about the hole",
        )
    return None


def observation_period(row: Mapping[str, object]) -> StoryPeriod:
    """`story_period` over the three date fields of a retrieval row, as strings or `None`.

    Here rather than in the stage because both the canonicaliser and a test that hand-builds a
    row need the same coercion, and `None`-versus-missing is C2's whole point: `row.get` on an
    absent key and `row[key]` returning `None` must produce the same period.
    """
    def text(key: str) -> str | None:
        value = row.get(key)
        return value if isinstance(value, str) and value else None

    return story_period(text("period_start"), text("period_end"), text("instant_date"))


def build_series(points: Sequence[CanonicalPoint]) -> dict[str, CanonicalSeries]:
    """One `CanonicalSeries` per metric, each ordered by `(anchor_date, period_key)`."""
    by_metric: dict[str, list[CanonicalPoint]] = {}
    for point in points:
        by_metric.setdefault(point.metric_id, []).append(point)
    return {
        metric_id: CanonicalSeries(
            metric_id=metric_id,
            points=tuple(
                sorted(group, key=lambda p: (p.period.anchor_date or "", p.period.key))
            ),
        )
        for metric_id, group in sorted(by_metric.items())
    }


__all__ = [
    "COHORT_VS_PERIOD_BASIS",
    "COUNT_TOLERANCE",
    "DELTA_PRECISION",
    "NARRATIVE_LANES",
    "PERCENT_TOLERANCE",
    "SCALE_PRECISION",
    "SCALE_TOLERANCE",
    "UNKNOWN_SCALE_PRECISION",
    "CanonicalPoint",
    "CanonicalSeries",
    "CanonicalStatus",
    "ClaimKind",
    "Comparability",
    "ComparabilityAuthority",
    "ComparabilityWarning",
    "ObservationRecord",
    "Ok",
    "Refuse",
    "ValueCluster",
    "authority_from",
    "build_series",
    "comparable",
    "default_authority",
    "observation_period",
    "presentation_tolerance",
    "within_tolerance",
]
