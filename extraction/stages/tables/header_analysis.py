"""Which original column carries which period.

The hard shape, from `q42020form8-kxexhibit991.htm#p27`:

    |  |  | Three Months Ended December 31, |  | Year Ended December 31, |  |
    |  |  | 2020 |  | 2019 |  | 2020 |  | 2019 |

Two column groups of different length share one row of bare years, and the end dates agree.
The duration wording sits in the row above and spans several columns. Reading the year row
alone yields four annual observations, two of which are wrong by a factor of four in
duration — right value, right metric, wrong period.

Group assignment is by ordinal division, not column position — see `_assign_groups`. The
`colspan` that put "Three Months Ended" above the first two years does not survive the
Markdown rendering, so the phrases sit at columns 2 and 4 while the years they govern sit at
2, 4, 6 and 8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...core import periods
from ...core.models import PeriodRef

_MONTH_ALT = "|".join(periods.MONTHS)
from .table_grid import TableGrid

_DURATION_PHRASE = re.compile(
    r"\b(three|six|nine|twelve)\s+months\s+ended\b|\b(year|fiscal\s+year)\s+ended\b", re.I)
_FISCAL_QUARTER = re.compile(r"\b([1-4])Q(\d{2})\b")
_FISCAL_YEAR = re.compile(r"\bFY\s?(\d{2}|\d{4})\b", re.I)
_BARE_YEAR = re.compile(r"^\s*(19|20)\d{2}\s*$")
_CHANGE_COLUMN = re.compile(r"\b(change|variance|increase|decrease|%\s*change)\b", re.I)


@dataclass(frozen=True)
class PeriodColumn:
    """One data column and the period it carries."""

    column_index: int          # original column index — what evidence points at
    collapsed_index: int
    period: PeriodRef
    header_row_index: int
    group_header: str
    column_label: str
    is_change_column: bool = False


@dataclass
class HeaderAnalysis:
    period_columns: tuple[PeriodColumn, ...] = ()
    header_row_indices: tuple[int, ...] = ()
    first_data_row: int = 0
    unresolved_columns: tuple[tuple[int, str], ...] = ()
    # True when the duration groups cannot be assigned to columns uniquely. The caller emits
    # AMBIGUOUS_COLUMN_ALIGNMENT and no claim rather than falling back to position.
    group_assignment_ambiguous: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.period_columns)


def analyse(grid: TableGrid) -> HeaderAnalysis:
    """Find the header rows and map data columns to periods."""
    date_row = _date_row(grid)
    if date_row is None:
        return HeaderAnalysis(unresolved_columns=(( -1, "no row carries a date or period label"),))

    group_spans = _group_spans(grid, date_row)
    # Two different subsets, deliberately. `header_cells` is what becomes a column, and
    # includes change columns, which are period-shaped in position but not in label.
    # `date_cells` is what decides how the duration groups divide, and must be period-shaped
    # only: the row carrying the dates usually also carries the scale declaration in its
    # first cell — `| (in thousands, except percentages) |  | March 31, 2021 | …` — and
    # counting that inflates the division the assignment depends on.
    header_cells = [c for c in grid.rows[date_row] if c.text.strip()
                    and (_looks_like_a_period(c.text.strip())
                         or _CHANGE_COLUMN.search(c.text.strip()))]
    date_cells = [c for c in header_cells if not _CHANGE_COLUMN.search(c.text.strip())]
    group_of, group_assignment_ambiguous = _assign_groups(group_spans, date_cells)
    if group_assignment_ambiguous:
        return HeaderAnalysis(
            group_assignment_ambiguous=True,
            unresolved_columns=tuple(
                (c.column_index, c.text.strip()) for c in date_cells),
        )
    columns: list[PeriodColumn] = []
    unresolved: list[tuple[int, str]] = []

    for cell in header_cells:
        label = cell.text.strip()
        group = group_of.get(cell.column_index, "")
        if _CHANGE_COLUMN.search(label):
            # A period-over-period change is not a measurement of a period. Recorded so the
            # column is accounted for, never turned into an observation.
            columns.append(PeriodColumn(
                column_index=cell.column_index, collapsed_index=cell.collapsed_index,
                period=PeriodRef(label_raw=label), header_row_index=date_row,
                group_header=group, column_label=label, is_change_column=True))
            continue

        period = _resolve(label, group)
        if period is None:
            if _looks_like_a_period(label):
                unresolved.append((cell.column_index, label))
            continue
        columns.append(PeriodColumn(
            column_index=cell.column_index, collapsed_index=cell.collapsed_index,
            period=period, header_row_index=date_row, group_header=group,
            column_label=label))

    header_rows = tuple(sorted({date_row, *(row for row, _, _ in group_spans)}))
    first_data = max(header_rows) + 1 if header_rows else 0
    while first_data in grid.separator_rows:
        first_data += 1

    return HeaderAnalysis(
        period_columns=tuple(columns),
        header_row_indices=header_rows,
        first_data_row=first_data,
        unresolved_columns=tuple(unresolved),
    )


def resolve_for_row(column: PeriodColumn, row_label: str) -> PeriodRef | None:
    """Re-resolve a column's period against a row label.

    A row saying "(at period end)" is an instant even in a column group headed
    "Three Months Ended" — `Homes in inventory` and the 120-day percentage both sit in
    tables whose other rows are flows. The column decides *when*; the row decides *what
    kind*.
    """
    if not periods.is_instant_label(row_label):
        return column.period
    end = column.period.instant_date or column.period.period_end
    if not end:
        return None
    return PeriodRef(instant_date=end, label_raw=column.period.label_raw)


def _date_row(grid: TableGrid) -> int | None:
    """The header row carrying the per-column period labels.

    The *last* header row that holds two or more period-ish cells: in a two-row header the
    upper row carries the duration phrase and the lower carries the dates, and it is the
    lower one that addresses columns.
    """
    best: int | None = None
    for row_index in grid.data_rows():
        labels = [c.text.strip() for c in grid.rows[row_index] if c.text.strip()]
        if len(labels) < 2:
            continue
        scored = sum(1 for label in labels if _looks_like_a_period(label))
        if scored >= 2:
            best = row_index
            if scored >= 3:
                break
    return best


def _looks_like_a_period(label: str) -> bool:
    return bool(
        periods.parse_date(label)
        or _BARE_YEAR.match(label)
        or _FISCAL_QUARTER.search(label)
        or _FISCAL_YEAR.search(label)
        or _DURATION_PHRASE.search(label)
    )


#: A group heading that names an instant rather than a duration: a month and day with no year,
#: because the year sits in the row beneath. `September 30,` and `March 31,` are these.
_INSTANT_HEADING = re.compile(rf"^({_MONTH_ALT})\s+\d{{1,2}},?$", re.I)


def _group_spans(grid: TableGrid, date_row: int) -> list[tuple[int, int, str]]:
    """(row, start column, phrase) for every group heading above the date row.

    **Instant headings are collected as well as duration phrases** *(corrected 2026-08-02)*.
    Until then only `_DURATION_PHRASE` was matched, so a header reading

        |  |  | September 30, |  | June 30, |  | March 31, |  | Year Ended December 31, |
        | (in whole numbers) |  | 2022 |  | 2022 |  | 2022 |  | 2021 |  | 2020 |  | 2019 |

    presented **one** phrase to `_assign_groups` where the layout has four. Six date columns
    divided evenly by one group handed every column to `Year Ended December 31,`, and three
    quarter-end instants were reported as full years. The step 13 corpus run found it: 21
    passages, 138 observations, and 8 pairs where two rows then disagreed about the value of
    one (metric, subject, period).

    A month-day with no year is a group heading for exactly the reason a duration phrase is —
    it governs the columns beneath it and supplies what they do not carry.
    """
    spans: list[tuple[int, int, str]] = []
    for row_index in range(date_row):
        if row_index in grid.separator_rows:
            continue
        for cell in grid.rows[row_index]:
            text = cell.text.strip()
            if text and (_DURATION_PHRASE.search(text) or _INSTANT_HEADING.match(text)):
                spans.append((row_index, cell.column_index, text))
    return spans


def _assign_groups(spans, date_cells) -> tuple[dict[int, str], bool]:
    """Map each date column to its duration group. Returns (assignment, ambiguous).

    Position alone does not work, and the Q4 2020 reconciliation shows why. Its header reads

        |  |  | Three Months Ended December 31, |  | Year Ended December 31, |  |
        |  |  | 2020 |  | 2019 |  | 2020 |  | 2019 |

    The phrases land at columns 2 and 4 while the years they govern sit at 2, 4, 6 and 8 —
    the `colspan` that put "Three Months Ended" above the first *two* years is gone by the
    time the table is Markdown, so "nearest phrase at or left of this column" hands 2019 to
    the annual group and reports a quarterly figure as a full year.

    Three rules, in order of how defensible they are.

    **Date shape decides when the shapes disagree.** A column label is either a full date
    (`March 31, 2023`) or a bare year (`2023`), and a duration phrase either supplies a
    month and day (`Year Ended December 31,`) or does not (`Three Months Ended`). The two
    fit together exactly one way: a full-date column already carries its own month and day,
    so it belongs to the phrase that supplies none; a bare year is unusable without one, so
    it belongs to the phrase that supplies it. This is a reading of the layout, not a guess.

    It is what the Q4 2023 earnings table needs — five full dates under `Three Months Ended`
    beside two bare years under `Year Ended December 31,`, seven columns over two groups.
    Even division cannot split 7 by 2, and the positional fallback read `December 31, 2022`
    as FY2022 and `March 31, 2023` and `June 30, 2023` as twelve-month durations: 21
    observations with the wrong period, none of them gold, so every score stayed at 1.000.

    **Even division otherwise.** When every column has the same shape the discriminator says
    nothing, and N/G consecutive columns per group in document order is what these layouts
    mean — the Q4 2020 reconciliation's four bare years under two date-supplying phrases.

    **Otherwise, refuse.** An uneven split that the shapes cannot resolve is not assigned at
    all. The caller raises `AMBIGUOUS_COLUMN_ALIGNMENT` and emits nothing, because the
    period-type check catches an instant read as a duration and *not* a duration read as the
    wrong duration — the failure that hid here for exactly that reason.
    """
    if not spans:
        return {}, False
    phrases = [text for _, _, text in spans]

    duration_only = [p for p in phrases if not _supplies_month_day(p)]
    date_supplying = [p for p in phrases if _supplies_month_day(p)]
    full_date = [c for c in date_cells if periods.parse_date(c.text.strip())]
    bare_year = [c for c in date_cells if _BARE_YEAR.match(c.text.strip())]

    # Unique only when each shape has exactly one home and both shapes are present.
    if (len(duration_only) == 1 and len(date_supplying) == 1
            and full_date and bare_year
            and len(full_date) + len(bare_year) == len(date_cells)):
        assignment = {c.column_index: duration_only[0] for c in full_date}
        assignment.update({c.column_index: date_supplying[0] for c in bare_year})
        return assignment, False

    if len(date_cells) and len(phrases) and len(date_cells) % len(phrases) == 0:
        per_group = len(date_cells) // len(phrases)
        return {
            cell.column_index: phrases[index // per_group]
            for index, cell in enumerate(date_cells)
        }, False

    positional = _positional_spans(spans, date_cells)
    if positional is not None:
        return positional, False

    return {}, True


def _positional_spans(spans, date_cells) -> dict[int, str] | None:
    """Bind each column to the heading that sits over it, when the layout says so uniquely.

    Deliberately the **last** rule, after even division, and the ordering is the whole of its
    safety. The Q4 2020 reconciliation would be bound wrongly by position — its two phrases
    sit at columns 2 and 4 above years at 2, 4, 6 and 8 because the `colspan` is gone, so
    "nearest heading at or left" gives `Three Months Ended` one column instead of two. Even
    division answers that layout first and correctly, and this rule never sees it.

    What reaches here is a header even division cannot split: several partial-span headings
    over a column count that is not a multiple of them. It applies only when **every heading
    sits exactly on a date column**, which is what makes the binding a reading of the rendered
    structure rather than a guess about a lost `colspan`. When a heading floats between
    columns, or before the first of them, nothing here is supported and the caller refuses
    with `AMBIGUOUS_COLUMN_ALIGNMENT`.
    """
    if len(spans) < 2:
        return None
    date_indices = [cell.column_index for cell in date_cells]
    starts = [start for _, start, _ in spans]
    if len(set(starts)) != len(starts):
        return None
    if any(start not in date_indices for start in starts):
        return None
    ordered = sorted(spans, key=lambda span: span[1])
    assignment: dict[int, str] = {}
    for cell in date_cells:
        governing = [text for _, start, text in ordered if start <= cell.column_index]
        if not governing:
            return None
        assignment[cell.column_index] = governing[-1]
    # Every heading must actually govern something, or the reading is not the one the layout
    # shows and a heading has been silently absorbed by its neighbour.
    if len(set(assignment.values())) != len(ordered):
        return None
    # And the binding must not contradict the shapes. A column that already carries its own
    # month and day cannot sit under a heading that supplies a different one — the position
    # says it does, the shapes say it cannot, and two readings disagreeing is the definition
    # of not uniquely supported. Caught by `test_an_unresolvable_uneven_grouping_abstains`,
    # which this rule made pass claims until the check was added.
    for cell in date_cells:
        if periods.parse_date(cell.text.strip()) and _supplies_month_day(
                assignment[cell.column_index]):
            return None
    return assignment


def _supplies_month_day(phrase: str) -> bool:
    """Whether a duration phrase carries its own month and day.

    `Year Ended December 31,` does and `Three Months Ended` does not. Matched without a year,
    because the year is what the column beneath supplies.
    """
    return bool(re.search(rf"\b({_MONTH_ALT})\s+\d{{1,2}}\b", phrase or "", re.I))


def _nearest(spans: list[tuple[int, int, str]], column_index: int) -> str:
    candidates = [(start, text) for _, start, text in spans if start <= column_index]
    if not candidates:
        return spans[0][2] if spans else ""
    return max(candidates, key=lambda pair: pair[0])[1]


def _resolve(label: str, group_header: str) -> PeriodRef | None:
    fiscal_year = _FISCAL_YEAR.search(label)
    if fiscal_year and not periods.parse_date(label):
        digits = fiscal_year.group(1)
        year = int(digits) if len(digits) == 4 else 2000 + int(digits)
        return PeriodRef(period_start=f"{year}-01-01", period_end=f"{year}-12-31",
                         label_raw=label)
    return periods.resolve(label, group_header=group_header)
