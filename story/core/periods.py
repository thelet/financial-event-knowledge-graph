"""Period identity and R3's shape classifier.

Responsibility: turning the three date fields a graph observation carries — `period_start`,
`period_end`, `instant_date` — into a period key and one of six shapes. Nothing here reads a
graph, an ontology or a clock; every function is a pure function of three optional strings.

**The key is `PeriodRef.key`, imported and not reimplemented.** `extraction/core/models.py`
owns the rule that turns two dates into `2023Q4`, `FY2023`, `2023-12-31` or
`{start}_{end}`, and `graph/core/derivation.py:67-79` already imports it for exactly this
reason and says so: *"a second copy of a period rule is how an id scheme drifts from the ids
already on disk."* The graph's own `Observation.period_key` was minted through that call, so
deriving it here a second way would be a second authority, not a check. `period_key_of` is a
one-line wrapper that exists so no story module constructs a `PeriodRef` itself.

*Recorded because a reader of §6.1 will trip on it*: the plan's §6.1 defines the slot key as
*"`instant_date` or `f"{period_start}_{period_end}"`"*, which is **not** what the graph
stores. `PeriodRef.key` renders `2022-04-01..2022-06-30` as `2022Q2` and
`2021-01-01..2021-12-31` as `FY2021`, falling back to `{start}_{end}` only for a window it
cannot name. The two schemes are in bijection over this corpus — 74 distinct periods either
way — so the 537-slot census is unaffected, but the strings differ, and so would every id
built from them. This module follows the graph.

**Shape is derived structurally, from the fields present** (correction C2). Neo4j stores no
null, so `SET n += {k: null}` removes the key and an absent property is indistinguishable from
an explicit null; `period_start`/`period_end` are present on 2,304 of 2,704 observations
*(verified live 2026-08-03)*. There is no `shape` property anywhere in the graph and this
module is where its absence is answered.

Two decisions inside `classify_shape`, both stated in §6.9 R3 and both load-bearing:

* **`2020-01-01..2020-03-31` is both a quarter and a YTD-3M, and is classified `quarter`.**
  The quarter test therefore runs *before* the year-to-date tests, and `ytd_3m` is not a member
  of the vocabulary at all — a Q1 column and a "three months ended" column are the same window
  in this corpus and a classifier that split them would refuse every 2020Q1 comparison.
* **The quarter test requires `end.day` to be the last day of its month.** Without it
  `2022-04-01..2022-06-15` classifies as a quarter — a 76-day window compared against 91-day
  ones. Zero rows in the current run have that shape (the only non-canonical duration windows
  are the three cross-year ones below), so this is a latent gap closed rather than a live bug;
  it is tested synthetically because live data cannot exercise it.

The three cross-year windows `2022-04-01..2022-12-31`, `2022-07-01..2023-03-31` and
`2022-10-01..2023-06-30` — 6 observations *(verified live 2026-08-03)* — classify as `other`,
which R3 refuses, so they are excluded from every comparison.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from enum import Enum

from extraction.core.models import PeriodRef

#: The months a quarter may start in. Opendoor's fiscal year is the calendar year *(every one
#: of the 74 distinct periods in the run starts on the 1st of January, April, July or October,
#: or is an instant)*, so a fiscal-quarter offset would be an unverified generalisation.
_QUARTER_START_MONTHS = frozenset({1, 4, 7, 10})

class PeriodShape(str, Enum):
    """R3's closed vocabulary. `other` is a member, not a failure mode.

    A shape that could not be named would have to be represented by `None`, and `None` reads as
    "not computed" everywhere else in this package. `OTHER` says the window was examined and is
    not one of the five comparable shapes — which is the finding R3 refuses on.
    """

    INSTANT = "instant"
    QUARTER = "quarter"
    FISCAL_YEAR = "fiscal_year"
    YTD_6M = "ytd_6m"
    YTD_9M = "ytd_9m"
    OTHER = "other"


#: How far apart two adjacent points of a shape are, in months, when nothing is missing between
#: them. R10 reads this: a `MOVEMENT` claim across a wider gap is a claim about a series hole.
#:
#: `INSTANT` is 3 because every instant in this run but one is a quarter- or year-end balance
#: sheet date *(measured: 24 of the 25 distinct instants; the exception is `2022-10-19`, the
#: credit-facility date on the single `borrowing_capacity` observation)*. `YTD_6M` and `YTD_9M`
#: are 12 because a year-to-date window recurs annually, not quarterly — 2023's first half is
#: the successor of 2022's first half, and the quarter between them is a different shape.
#:
#: `OTHER` is absent deliberately: R3 has already refused by the time R10 could ask, and a step
#: for a window nobody can name would be a number invented to fill a table.
SHAPE_STEP_MONTHS: dict[PeriodShape, int] = {
    PeriodShape.INSTANT: 3,
    PeriodShape.QUARTER: 3,
    PeriodShape.FISCAL_YEAR: 12,
    PeriodShape.YTD_6M: 12,
    PeriodShape.YTD_9M: 12,
}


@dataclass(frozen=True, slots=True)
class StoryPeriod:
    """One period, as this package handles it: three optional dates, a key and a shape.

    Frozen and hashable so it can key a slot dictionary directly. The three raw fields are kept
    beside the derived ones because a refusal has to be able to name what it looked at — R3
    saying *"`2022-07-01_2023-03-31` is `other`"* is only actionable if the window is visible.
    """

    period_start: str | None
    period_end: str | None
    instant_date: str | None
    key: str
    shape: PeriodShape

    @property
    def is_instant(self) -> bool:
        return self.shape is PeriodShape.INSTANT

    @property
    def anchor_date(self) -> str | None:
        """The date a period is ordered and windowed by: its end, or the instant itself.

        `coalesce(period_end, instant_date)` is what `cypher.METRIC_HISTORY` orders by and what
        its `$since`/`$until` window compares against, so a series ordered any other way would
        disagree with the rows it was built from.
        """
        return self.period_end or self.instant_date


def period_key_of(
    period_start: str | None, period_end: str | None, instant_date: str | None
) -> str:
    """`PeriodRef.key`, reached through the model that owns it."""
    return PeriodRef(
        period_start=period_start, period_end=period_end, instant_date=instant_date
    ).key


def classify_shape(
    period_start: str | None, period_end: str | None, instant_date: str | None
) -> PeriodShape:
    """R3's classifier, over the fields present rather than over a stored property (C2).

    `instant_date` wins outright when it is present. That mirrors `PeriodRef.key`, which reads
    it first, and `PeriodRef`'s own docstring — *"a duration or an instant, never both"* — so
    an observation carrying all three fields is already malformed upstream and this module
    resolves it the same way the id scheme does rather than inventing a second answer.

    A date that does not parse yields `OTHER` rather than raising: F13 measured that this graph
    stores every date as a `String`, so a malformed one is a data state a detector must be able
    to skip, not an exception that ends a run.
    """
    if instant_date is not None:
        return PeriodShape.INSTANT if _parse(instant_date) is not None else PeriodShape.OTHER
    start, end = _parse(period_start), _parse(period_end)
    if start is None or end is None:
        return PeriodShape.OTHER
    if _is_quarter(start, end):
        return PeriodShape.QUARTER
    if start == date(start.year, 1, 1) and end == date(start.year, 12, 31):
        return PeriodShape.FISCAL_YEAR
    if start == date(start.year, 1, 1) and end == date(start.year, 6, 30):
        return PeriodShape.YTD_6M
    if start == date(start.year, 1, 1) and end == date(start.year, 9, 30):
        return PeriodShape.YTD_9M
    return PeriodShape.OTHER


def story_period(
    period_start: str | None = None,
    period_end: str | None = None,
    instant_date: str | None = None,
) -> StoryPeriod:
    """The one constructor. Key and shape are derived here so they cannot disagree."""
    return StoryPeriod(
        period_start=period_start,
        period_end=period_end,
        instant_date=instant_date,
        key=period_key_of(period_start, period_end, instant_date),
        shape=classify_shape(period_start, period_end, instant_date),
    )


def months_between(earlier: str, later: str) -> int | None:
    """Whole months from one anchor date to another, or `None` if either does not parse.

    Month arithmetic rather than day counting, because the anchors it compares are month-ends:
    `2021-12-31 → 2022-03-31` is 90 days and `2022-06-30 → 2022-09-30` is 92, and a day-based
    step would have to carry a tolerance that a month difference does not need.
    """
    first, second = _parse(earlier), _parse(later)
    if first is None or second is None:
        return None
    return (second.year - first.year) * 12 + (second.month - first.month)


def _is_quarter(start: date, end: date) -> bool:
    """Jan/Apr/Jul/Oct 1st to the last day of the month two months later, same year.

    The `end.day` check is the latent gap §6.9 records: without it `2022-04-01..2022-06-15` is
    a quarter. `calendar.monthrange` rather than a table, so February and leap years need no
    special case.
    """
    return (
        start.day == 1
        and start.month in _QUARTER_START_MONTHS
        and end.year == start.year
        and end.month == start.month + 2
        and end.day == calendar.monthrange(end.year, end.month)[1]
    )


def _parse(value: str | None) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "SHAPE_STEP_MONTHS",
    "PeriodShape",
    "StoryPeriod",
    "classify_shape",
    "months_between",
    "period_key_of",
    "story_period",
]
