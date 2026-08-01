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


# -- the periods a passage of prose states ---------------------------------------------------
#
# A table's periods come from its header row. A passage's come from its own sentences, and the
# two readers must be one reader or the enum a model chooses from and the resolution applied to
# its choice will drift apart. These lived in the narrative lane's rejection boundary until
# review moved them here *(2026-08-02)*: they are the schema's period vocabulary, built before
# a prompt exists and consumed by the lane, so a lane reaching into the rejection boundary for
# them was an inversion.

DURATION = "duration"
INSTANT = "instant"

_DURATION_PHRASE = re.compile(
    rf"\b(?:(?:three|six|nine|twelve)\s+months|year|fiscal\s+year)\s+ended\s+"
    rf"(?:{_MONTH_ALT})\s+\d{{1,2}},?\s+\d{{4}}\b", re.I)
_FISCAL_SHORTHAND = re.compile(r"\b[1-4]Q\d{2}\b")
_FULL_DATE_IN_TEXT = re.compile(rf"\b(?:{_MONTH_ALT})\s+\d{{1,2}},?\s+\d{{4}}\b", re.I)


def resolve_period_phrase(phrase: str, *, period_type: str) -> PeriodRef | None:
    """One printed phrase to a `PeriodRef` of the type a metric declares, or None.

    An instant falls back to a duration's end date because a passage naming "3Q23" does name
    2023-09-30, and every stock metric in this corpus is reported at a period end. The reverse
    fallback does not exist: a bare date says nothing about how long a flow ran, and inventing
    a length is how a full-year figure becomes a Q4 figure.
    """
    if period_type == INSTANT:
        instant = resolve_instant(phrase)
        if instant is not None:
            return instant
        duration = resolve(phrase, group_header=phrase)
        if duration is not None and not duration.is_instant and duration.period_end:
            return PeriodRef(instant_date=duration.period_end, label_raw=phrase)
        return None
    duration = resolve(phrase, group_header=phrase)
    return duration if (duration is not None and not duration.is_instant) else None


def period_phrases(text: str) -> tuple[str, ...]:
    """Every phrase in a passage that this module can resolve, in the order printed.

    Derived by *asking* `resolve_period_phrase` rather than by trusting the patterns above: the
    patterns propose, and a phrase only enters the vocabulary if it actually resolves.

    Ordered by first occurrence rather than sorted, because the vocabulary is read by a human
    reviewing a prompt and document order is how the passage reads.
    """
    found: dict[str, int] = {}
    for pattern in (_DURATION_PHRASE, _FISCAL_SHORTHAND, _FULL_DATE_IN_TEXT):
        for match in pattern.finditer(text or ""):
            phrase = " ".join(match.group(0).split())
            if phrase in found:
                continue
            if any(resolve_period_phrase(phrase, period_type=kind) is not None
                   for kind in (DURATION, INSTANT)):
                found[phrase] = match.start()
    return tuple(sorted(found, key=lambda phrase: (found[phrase], phrase)))


def reporting_period_keys(text: str) -> frozenset[str]:
    """The period keys a passage's *own* reporting period resolves to, per period type.

    The latest end date of each type, which is what a filing's own reporting period is: an
    MD&A paragraph prints "three months ended September 30, 2021" beside the comparative
    "three months ended September 30, 2020", and only the first is the period the paragraph is
    about. Used to measure period attribution, never to repair it — a figure whose evidence
    sentence prints its own period is judged against that sentence, not against this.
    """
    latest: dict[str, tuple[str, str]] = {}
    for phrase in period_phrases(text):
        for kind in (DURATION, INSTANT):
            resolved = resolve_period_phrase(phrase, period_type=kind)
            if resolved is None:
                continue
            end = resolved.instant_date or resolved.period_end or ""
            if kind not in latest or end > latest[kind][0]:
                latest[kind] = (end, resolved.key)
    return frozenset(key for _, key in latest.values())
