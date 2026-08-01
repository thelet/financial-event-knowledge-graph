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

    @property
    def ok(self) -> bool:
        return bool(self.period_columns)


def analyse(grid: TableGrid) -> HeaderAnalysis:
    """Find the header rows and map data columns to periods."""
    date_row = _date_row(grid)
    if date_row is None:
        return HeaderAnalysis(unresolved_columns=(( -1, "no row carries a date or period label"),))

    group_spans = _duration_spans(grid, date_row)
    date_cells = [c for c in grid.rows[date_row] if c.text.strip()]
    group_of = _assign_groups(group_spans, date_cells)
    columns: list[PeriodColumn] = []
    unresolved: list[tuple[int, str]] = []

    for cell in date_cells:
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


def _duration_spans(grid: TableGrid, date_row: int) -> list[tuple[int, int, str]]:
    """(row, start column, phrase) for every duration header above the date row."""
    spans: list[tuple[int, int, str]] = []
    for row_index in range(date_row):
        if row_index in grid.separator_rows:
            continue
        for cell in grid.rows[row_index]:
            text = cell.text.strip()
            if text and _DURATION_PHRASE.search(text):
                spans.append((row_index, cell.column_index, text))
    return spans


def _assign_groups(spans, date_cells) -> dict[int, str]:
    """Map each date column to its duration group.

    Position alone does not work, and the Q4 2020 reconciliation shows why. Its header reads

        |  |  | Three Months Ended December 31, |  | Year Ended December 31, |  |
        |  |  | 2020 |  | 2019 |  | 2020 |  | 2019 |

    The phrases land at columns 2 and 4 while the years they govern sit at 2, 4, 6 and 8 —
    the `colspan` that put "Three Months Ended" above the first *two* years is gone by the
    time the table is Markdown, so "nearest phrase at or left of this column" hands 2019 to
    the annual group and reports a quarterly figure as a full year.

    When the date columns divide evenly among the duration groups, they are assigned in
    document order, N/G consecutive columns each. That is what these layouts mean: every
    group carries the same comparative years in the same order. An uneven split is not
    guessed at — it falls back to position and any resulting period is still checked
    against the metric's declared period type.
    """
    if not spans:
        return {}
    phrases = [text for _, _, text in spans]
    if len(date_cells) and len(phrases) and len(date_cells) % len(phrases) == 0:
        per_group = len(date_cells) // len(phrases)
        return {
            cell.column_index: phrases[index // per_group]
            for index, cell in enumerate(date_cells)
        }
    return {
        cell.column_index: _nearest(spans, cell.column_index) for cell in date_cells
    }


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
