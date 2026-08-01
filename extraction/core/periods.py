"""Period phrases to resolved periods.

Every pattern here comes from a header actually present in the corpus. The hard case, and
the reason this module exists rather than a regex at the call site, is a single header row
carrying two column groups of different length:

    |  |  | Three Months Ended December 31, |  | Year Ended December 31, |  |
    |  |  | 2020 |  | 2019 |  | 2020 |  | 2019 |

Four columns, two durations, identical end dates, and the year row alone cannot tell them
apart. A reader that takes the years and stops emits four annual observations, two of which
are wrong by a factor of four — right value, right metric, wrong period.

Instants are not durations. A row labelled "(at period end)" is a stock measured on a date;
`housing_inventory_homes` and `pct_homes_on_market_gt_120_days` are both instants and both
sit in tables whose other rows are flows.
"""

from __future__ import annotations

import re

from .models import PeriodRef

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_ALT = "|".join(MONTHS)

# "December 31, 2020" / "March 31, 2021"
_FULL_DATE = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
# "December 31," with the year supplied by a row beneath
_DATE_NO_YEAR = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}}),\s*$", re.I)
_BARE_YEAR = re.compile(r"^\s*(\d{4})\s*$")
# "4Q21", "1Q23" - the shareholder letters' shorthand
_FISCAL_SHORT = re.compile(r"\b([1-4])Q(\d{2})\b")

_THREE_MONTHS = re.compile(r"\bthree\s+months\s+ended\b", re.I)
_SIX_MONTHS = re.compile(r"\bsix\s+months\s+ended\b", re.I)
_NINE_MONTHS = re.compile(r"\bnine\s+months\s+ended\b", re.I)
_YEAR = re.compile(r"\b(year|twelve\s+months|fiscal\s+year)\s+ended\b", re.I)
_AT_PERIOD_END = re.compile(r"\bat\s+period\s+end\b|\bas\s+of\b", re.I)

_LAST_DAY = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
             7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _iso(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _month_start(year: int, month: int) -> str:
    return _iso(year, month, 1)


def _shift_back(year: int, month: int, months: int) -> tuple[int, int]:
    """The first month of a window ending in (year, month) and spanning `months` months."""
    index = (year * 12 + (month - 1)) - (months - 1)
    return index // 12, index % 12 + 1


def parse_date(text: str) -> tuple[int, int, int] | None:
    match = _FULL_DATE.search(text or "")
    if not match:
        return None
    return int(match.group(3)), MONTHS[match.group(1).lower()], int(match.group(2))


def duration_months(header: str) -> int | None:
    """How long the column group is, from its wording. None when the wording says nothing."""
    text = header or ""
    if _THREE_MONTHS.search(text):
        return 3
    if _SIX_MONTHS.search(text):
        return 6
    if _NINE_MONTHS.search(text):
        return 9
    if _YEAR.search(text):
        return 12
    return None


def is_instant_label(label: str) -> bool:
    """A row label, not a column header: "(at period end)" makes the row a stock."""
    return bool(_AT_PERIOD_END.search(label or ""))


def resolve(
    column_label: str,
    *,
    group_header: str = "",
    row_label: str = "",
    fallback_year: int | None = None,
) -> PeriodRef | None:
    """Resolve one column into a period.

    `group_header` carries the duration wording when the column itself is a bare year — the
    two-group case this module exists for. `row_label` decides instant versus duration,
    because a "(at period end)" row is a stock no matter what its column group says.
    """
    label = (column_label or "").strip()
    raw = label or (group_header or "").strip()

    date = parse_date(label) or parse_date(group_header)
    if date is None:
        year_match = _BARE_YEAR.match(label)
        if year_match:
            # A bare year under a "... Ended <Month> <day>," group takes the group's date.
            month_day = _DATE_NO_YEAR.search((group_header or "").strip())
            year = int(year_match.group(1))
            if month_day:
                month, day = MONTHS[month_day.group(1).lower()], int(month_day.group(2))
            else:
                month, day = 12, 31
            date = (year, month, day)
        else:
            fiscal = _FISCAL_SHORT.search(label) or _FISCAL_SHORT.search(group_header or "")
            if fiscal:
                quarter, short_year = int(fiscal.group(1)), int(fiscal.group(2))
                year = 2000 + short_year
                end_month = quarter * 3
                date = (year, end_month, _LAST_DAY[end_month])
                if not is_instant_label(row_label):
                    start_year, start_month = _shift_back(year, end_month, 3)
                    return PeriodRef(
                        period_start=_month_start(start_year, start_month),
                        period_end=_iso(*date),
                        label_raw=raw,
                    )
                return PeriodRef(instant_date=_iso(*date), label_raw=raw)
            if fallback_year is None:
                return None
            date = (fallback_year, 12, 31)

    year, month, day = date
    end = _iso(year, month, day)

    if is_instant_label(row_label):
        return PeriodRef(instant_date=end, label_raw=raw)

    months = duration_months(group_header) or duration_months(label)
    if months is None:
        # No stated length. Refusing is correct: guessing "quarter" here is how a full-year
        # figure becomes a Q4 figure, and both are plausible in the same table.
        return None

    start_year, start_month = _shift_back(year, month, months)
    return PeriodRef(period_start=_month_start(start_year, start_month),
                     period_end=end, label_raw=raw)


def resolve_instant(text: str) -> PeriodRef | None:
    """For prose: "As of March 31, 2023, 59% of our homes ..." """
    date = parse_date(text)
    if date is None:
        return None
    return PeriodRef(instant_date=_iso(*date), label_raw=text.strip()[:120] or None)
