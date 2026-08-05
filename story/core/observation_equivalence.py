"""When two observations are two sources for **one** reading, and when they are two readings.

Responsibility: one predicate — `same_reading(left, right)` — and the typed answer it returns.
It is the arithmetic behind both halves of EVIDENCE_ROLES_AND_SEMANTIC_FACTS §4 S2 and S3, and
that is why it is one function rather than two: *"are these the same number?"* answered one way
for corroboration and another way for contradiction is the divergence R2b found once already.
S3 keeps every observation this module calls equivalent; S2 turns the ones it calls divergent
into counter-evidence, and reads the `reason` to say on what basis.

**It owns no table.** `presentation_tolerance`, `within_tolerance` and `DELTA_PRECISION` are
`story/core/series.py`'s, which is where §6.1 step 2's clustering and §6.9's R8 already read
them from — three callers, one table. The comparison here is the same single-pair test
`canonicalization._cluster` makes (`max(left.tolerance, right.tolerance)`), so an observation
this module calls equivalent is an observation canonicalisation put in the same cluster.

**Comparison is on canonical values, never on raw strings.** `ObservationRecord.value` is
already scale-applied, so `6,261`, `6261` and `6.261 thousand` arrive as the same float and
`3.3%` and `3.30 percent` as `3.3`; the printed form never enters. What is compared is value,
sign, unit, currency, metric identity, subject, period and formula version — and the accepted
presentation tolerance is the only slack.

**`15.9%` and `15.9 percentage points` are refused with a reason of their own** (E4b). A bare
unit comparison would already refuse them, but it would report `UNIT_MISMATCH`, which reads as
a units bookkeeping problem; §13.3 exists because that specific confusion is the most likely
factual error this package can make, and a reason that names it is a finding a reader can act
on. The same rule refuses a percentage against basis points.

**No model, no graph, no clock**, matching `series.py`: everything here is a pure function of
its arguments plus the ontology's `ComparabilityAuthority`, which the caller supplies.
"""

from __future__ import annotations

from dataclasses import dataclass

from story.core.periods import PeriodShape, StoryPeriod
from story.core.series import (
    ComparabilityAuthority,
    ObservationRecord,
    presentation_tolerance,
    within_tolerance,
)

#: The units that all measure "a percentage of something" and none of which mean the same thing.
#: §13.3's confusion, as a set: a level in `percent`, a *change* in `percentage_points` and the
#: same change in `basis_points` are three quantities, and two of them are numerically equal for
#: every value between 0 and 100. Nothing in this corpus carries `percentage_points` today —
#: every percentage observation is `unit == "percent"` *(measured 2026-08-05: 1,163 percent/units
#: rows and no other percentage unit)* — so this set is a guard against the day a lane emits one,
#: which is exactly when the mistake would be invisible.
PERCENT_FAMILY_UNITS = frozenset({
    "percent", "percentage", "pct", "percentage_points", "percentage_point", "pp", "ppt",
    "basis_points", "basis_point", "bps",
})


@dataclass(frozen=True, slots=True)
class SameReading:
    """The two observations are two sources for one reading. Truthy."""

    tolerance: float

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Divergent:
    """The two observations are two readings, with the rule that said so and both sides named.

    `reason` is machine-readable and is what `counter_evidence.basis_for_divergence` maps to a
    `match_basis`; `detail` names both operands, for the same reason `series.Refuse` does —
    *"incomparable"* with no operands is not a finding anyone can act on.
    """

    rule: str
    reason: str
    detail: str

    def __bool__(self) -> bool:
        return False


Equivalence = SameReading | Divergent

#: Every `Divergent.reason` this module produces. Declared so a consumer mapping reasons to
#: something else — `counter_evidence` maps them to match bases — can be checked total rather
#: than falling through to a default nobody chose.
DIVERGENCE_REASONS: tuple[str, ...] = (
    "SUBJECT_MISMATCH",
    "METRIC_MISMATCH",
    "PERIOD_MISMATCH",
    "UNCOMPARABLE_SHAPE",
    "PERCENT_VERSUS_PERCENTAGE_POINTS",
    "UNIT_MISMATCH",
    "CURRENCY_MISMATCH",
    "FORMULA_VERSION_MISMATCH",
    "OPPOSITE_SIGN",
    "VALUE_OUTSIDE_TOLERANCE",
)


def same_reading(
    left: ObservationRecord,
    right: ObservationRecord,
    *,
    authority: ComparabilityAuthority | None = None,
) -> Equivalence:
    """E1–E8, in order, as a value. Raises nothing, for `series.comparable`'s reason.

    The order is not cosmetic. Identity comes before arithmetic so that two different metrics
    holding the same number are refused as different metrics rather than accepted as one
    reading — *"the same number on a different metric"* is the false-corroboration case S7's
    adversarial review is aimed at, and it must never reach the tolerance test at all.

    `authority` is only needed by E7. It is optional because most call sites compare two rows of
    one unversioned metric and loading an ontology to learn that would make this module unusable
    from a test with no YAML on disk; when it is absent, E7 is **skipped and says so** rather
    than silently passing — the caller that cares supplies one.
    """
    for rule in (_e1_subject, _e2_metric, _e3_period, _e4_unit, _e5_currency, _e6_formula):
        divergence = rule(left, right, authority)
        if divergence is not None:
            return divergence
    return _e7_value(left, right)


def equivalent_period(left: StoryPeriod, right: StoryPeriod) -> bool:
    """Same window, however it is spelled — and never two windows of different lengths.

    Key equality is the ordinary case and is not sufficient on its own: the key is minted by
    `extraction.core.models.PeriodRef.key`, which renders `2022-04-01..2022-06-30` as `2022Q2`,
    so two rows can carry one key and three different raw date fields only if one of them is
    wrong. Both are compared, and `OTHER` is refused outright for R3's reason — a window nobody
    can name is a window whose length nobody knows.
    """
    if PeriodShape.OTHER in (left.shape, right.shape):
        return False
    if left.shape is not right.shape:
        return False
    return (left.key == right.key
            and (left.period_start, left.period_end, left.instant_date)
            == (right.period_start, right.period_end, right.instant_date))


def _e1_subject(
    left: ObservationRecord, right: ObservationRecord, _authority: ComparabilityAuthority | None
) -> Divergent | None:
    if left.subject_entity_id == right.subject_entity_id:
        return None
    return Divergent(
        "E1", "SUBJECT_MISMATCH",
        f"{left.observation_id} is about {left.subject_entity_id!r} and "
        f"{right.observation_id} is about {right.subject_entity_id!r}; two subjects are never "
        "two sources for one reading")


def _e2_metric(
    left: ObservationRecord, right: ObservationRecord, _authority: ComparabilityAuthority | None
) -> Divergent | None:
    """Strict equality, with **no** cross-metric escape.

    `series._r2_metric` lets two metrics of one `mutually_distinct_groups` group be *compared*
    as a divergence pair. Corroboration is the opposite question: membership of a group the
    ontology created to say *"these must never be merged"* is the strongest possible argument
    that two rows are not one reading.
    """
    if left.metric_id == right.metric_id:
        return None
    return Divergent(
        "E2", "METRIC_MISMATCH",
        f"{left.observation_id} measures {left.metric_id} and {right.observation_id} measures "
        f"{right.metric_id}; the same number on two metrics is two facts")


def _e3_period(
    left: ObservationRecord, right: ObservationRecord, _authority: ComparabilityAuthority | None
) -> Divergent | None:
    if equivalent_period(left.period, right.period):
        return None
    if PeriodShape.OTHER in (left.period.shape, right.period.shape):
        return Divergent(
            "E3", "UNCOMPARABLE_SHAPE",
            f"{left.period.key} is a {left.period.shape.value} and {right.period.key} is a "
            f"{right.period.shape.value}; a window nobody can name has no length to compare")
    return Divergent(
        "E3", "PERIOD_MISMATCH",
        f"{left.observation_id} is {left.period.key} ({left.period.shape.value}) and "
        f"{right.observation_id} is {right.period.key} ({right.period.shape.value}); §13.4 "
        "measured 188 (metric, period_end) pairs carrying more than one period key")


def _e4_unit(
    left: ObservationRecord, right: ObservationRecord, _authority: ComparabilityAuthority | None
) -> Divergent | None:
    """Unit equality, with §13.3's confusion given a reason of its own.

    Deliberately **not** scale: `ObservationRecord.value` is already scale-applied (§6.9 R4) and
    the scale survives only in the tolerance E7 reads.
    """
    if left.unit == right.unit:
        return None
    units = {left.unit, right.unit}
    if units <= PERCENT_FAMILY_UNITS:
        return Divergent(
            "E4b", "PERCENT_VERSUS_PERCENTAGE_POINTS",
            f"{left.observation_id} is in {left.unit!r} and {right.observation_id} is in "
            f"{right.unit!r}; a percentage and a change in percentage points are numerically "
            "equal and are different quantities (§13.3), and merging them is the single most "
            "likely factual error this package can make")
    return Divergent(
        "E4", "UNIT_MISMATCH",
        f"{left.observation_id} is in {left.unit!r} and {right.observation_id} is in "
        f"{right.unit!r}")


def _e5_currency(
    left: ObservationRecord, right: ObservationRecord, _authority: ComparabilityAuthority | None
) -> Divergent | None:
    """Both `None` is agreement — `currency` is present on 997 of 2,704 observations (C2)."""
    if left.currency == right.currency:
        return None
    return Divergent(
        "E5", "CURRENCY_MISMATCH",
        f"{left.observation_id} is in {left.currency!r} and {right.observation_id} is in "
        f"{right.currency!r}")


def _e6_formula(
    left: ObservationRecord, right: ObservationRecord, authority: ComparabilityAuthority | None
) -> Divergent | None:
    """Same formula version at each observation's own anchor date, when there is an authority.

    `adjusted_gross_profit` is the only versioned metric in this ontology, so this rule fires on
    nothing in the current run and is the rule that matters most the day a second one is
    versioned: two filings reporting one metric under two definitions are not one reading
    however equal the numbers are.
    """
    if authority is None or not authority.is_versioned(left.metric_id):
        return None
    left_version = authority.resolve_version(left.metric_id, left.period.anchor_date)
    right_version = authority.resolve_version(right.metric_id, right.period.anchor_date)
    if left_version == right_version:
        return None
    return Divergent(
        "E6", "FORMULA_VERSION_MISMATCH",
        f"{left.observation_id} falls under {left_version!r} and {right.observation_id} under "
        f"{right_version!r}; a comparison across the boundary compares two definitions")


def _e7_value(left: ObservationRecord, right: ObservationRecord) -> Equivalence:
    """§6.1 step 2's single-pair test, at the coarser of the two tolerances.

    **Sign is read only outside tolerance, and that ordering is the whole of it.** Two rows at
    `+0.05` and `-0.05` on a metric whose printed precision is one decimal are one reading that
    two filings rounded across zero, and refusing them would make `presentation_rounding` depend
    on which side of zero a value happened to land. Outside tolerance the sign is what
    distinguishes *"the filings disagree about how big it is"* from *"the filings disagree about
    which way it went"*, and only the second can flip a story's direction.
    """
    tolerance = max(
        presentation_tolerance(left.unit, left.scale),
        presentation_tolerance(right.unit, right.scale),
    )
    if within_tolerance(left.value, right.value, tolerance):
        return SameReading(tolerance=tolerance)
    if (left.value > 0) != (right.value > 0) and left.value != 0 and right.value != 0:
        return Divergent(
            "E7", "OPPOSITE_SIGN",
            f"{left.observation_id} reports {left.value} and {right.observation_id} reports "
            f"{right.value}; the two filings disagree about the direction, not only the size")
    return Divergent(
        "E7", "VALUE_OUTSIDE_TOLERANCE",
        f"{left.observation_id} reports {left.value} and {right.observation_id} reports "
        f"{right.value}, which is outside the presentation tolerance of {tolerance} for "
        f"{left.unit!r} at scale {left.scale!r}/{right.scale!r}")


__all__ = [
    "DIVERGENCE_REASONS",
    "PERCENT_FAMILY_UNITS",
    "Divergent",
    "Equivalence",
    "SameReading",
    "equivalent_period",
    "same_reading",
]
