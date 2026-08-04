"""§13.4's closed grammar: the period surfaces a draft may write, and nothing else.

Responsibility: `"the third quarter of 2022"` → `(duration, 2022-07-01, 2022-09-30)`, and
`"the quarter"` → unresolvable. Pure functions of one string; the endpoints are then handed to
`story.core.periods`, which owns the key and the shape, so this module never mints a period
key itself.

**Why a grammar and not a date parser.** §13.4: *"Resolve `period_surface` through a closed
grammar, never a free date parser."* A general parser answers every string, including the ones
that should be refused — `dateutil` reads `"the quarter"` as today's date and `"2022"` as the
1st of January. The failure it would cause is the one §13.4 calls catastrophic: 188
`(metric, period_end)` pairs in the package carry more than one `period_key`, and
`adjusted_ebitda` ending `2022-09-30` is `+$183M` for the nine-month YTD and `−$211M` for the
quarter. A surface that resolves to an endpoint but not to a *shape* is exactly how those two
become one.

So the vocabulary here is small and total: quarters, halves, nine- and six-month
year-to-dates, fiscal years, and four instant forms. Anything else resolves to nothing and
§13.4 refuses it. A bare `"2022"` is deliberately **not** in the grammar — it is the shape
§13.7.1 measures as ambiguous on 61.3% of table observations, and licensing it as a sentence
surface would re-open at the sentence level the hole the citation rule closes at the column
level.

Two-digit years (`2Q22`, `1H23`, `9M23`) are read as 20xx. Every document in the corpus is a
2019–2025 filing *(the run's 185 documents; verified via the graph manifest)*, and a
century-window rule that could not be exercised would be a guess wearing a comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from story.core.periods import PeriodShape, classify_shape, period_key_of

#: Which quarter an ordinal word names. Written out rather than derived from an index, because
#: the failure mode of an index is silent (`"forth"` → 4) and the failure mode of a missing
#: key is a refusal.
_ORDINAL_QUARTER = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

#: The last day of each month, for the year-end and month-end instant forms. February is 28
#: here and `_month_end` corrects it; a leap-year table would be a second authority.
_MONTH_LENGTH = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


@dataclass(frozen=True, slots=True)
class PeriodSurface:
    """What a period surface resolved to, with the key and the shape derived from it.

    `resolved` is a field rather than `key is None` because §13.4 has two distinct refusals —
    *"this surface names no period"* and *"this surface names a different period"* — and a
    caller that could not tell them apart would report the wrong remedy.
    """

    surface: str
    resolved: bool
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    key: str = ""
    shape: PeriodShape = PeriodShape.OTHER

    @property
    def is_instant(self) -> bool:
        return self.instant_date is not None


def _duration(surface: str, start: date, end: date) -> PeriodSurface:
    """A resolved window, with `story.core.periods` deriving the key and the shape.

    Derived and not passed in: `PeriodRef.key` is the graph's own rule and the only reading
    that can be compared against a stored `period_key` (§6.1's P7 correction), and a second
    copy of it here would be a second authority rather than a check.
    """
    start_iso, end_iso = start.isoformat(), end.isoformat()
    return PeriodSurface(
        surface=surface,
        resolved=True,
        period_start=start_iso,
        period_end=end_iso,
        key=period_key_of(start_iso, end_iso, None),
        shape=classify_shape(start_iso, end_iso, None),
    )


def _instant(surface: str, moment: date) -> PeriodSurface:
    iso = moment.isoformat()
    return PeriodSurface(
        surface=surface,
        resolved=True,
        instant_date=iso,
        key=period_key_of(None, None, iso),
        shape=classify_shape(None, None, iso),
    )


def _unresolvable(surface: str) -> PeriodSurface:
    return PeriodSurface(surface=surface, resolved=False)


def _year(raw: str) -> int:
    value = int(raw)
    return value if value >= 1000 else 2000 + value


def _month_end(year: int, month: int) -> date:
    if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        return date(year, 2, 29)
    return date(year, month, _MONTH_LENGTH[month])


def _quarter(surface: str, quarter: int, year: int) -> PeriodSurface:
    start_month = 3 * (quarter - 1) + 1
    return _duration(surface, date(year, start_month, 1), _month_end(year, start_month + 2))


# Each rule is `(pattern, builder)`. Ordered longest-and-most-specific first for the same
# reason §13.5's alias index is: `"fiscal 2022"` contains `"2022"`, and `"nine months ended
# September 30, 2022"` contains `"September 30, 2022"`, so a scanner that tried the short form
# first would resolve a nine-month window to an instant.
_RULES: tuple[tuple[re.Pattern[str], Callable[[str, re.Match[str]], PeriodSurface]], ...] = (
    # -- year-to-date, worded and coded -----------------------------------------------------
    (re.compile(r"^(?:the\s+)?nine\s+months\s+ended\s+september\s+30,?\s+(\d{4})$"),
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 9, 30))),
    (re.compile(r"^9m\s*(\d{2}|\d{4})$"),
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 9, 30))),
    (re.compile(r"^(?:the\s+)?six\s+months\s+ended\s+june\s+30,?\s+(\d{4})$"),
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 6, 30))),
    (re.compile(r"^(?:1h|6m)\s*(\d{2}|\d{4})$"),
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 6, 30))),
    (re.compile(r"^(?:the\s+)?first\s+half\s+of\s+(\d{4})$"),
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 6, 30))),

    # -- fiscal years -----------------------------------------------------------------------
    (re.compile(r"^(?:fiscal(?:\s+year)?|full\s+year|fy)\s*(\d{4})$"),
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 12, 31))),

    # -- quarters ---------------------------------------------------------------------------
    (re.compile(r"^(?:the\s+)?(first|second|third|fourth)\s+quarter\s+(?:of\s+|ended\s+)?"
                r"(?:fiscal\s+)?(\d{4})$"),
     lambda s, m: _quarter(s, _ORDINAL_QUARTER[m.group(1)], _year(m.group(2)))),
    (re.compile(r"^q\s*([1-4])\s*(?:fy)?\s*(\d{2}|\d{4})$"),
     lambda s, m: _quarter(s, int(m.group(1)), _year(m.group(2)))),
    (re.compile(r"^([1-4])q\s*(?:fy)?\s*(\d{2}|\d{4})$"),
     lambda s, m: _quarter(s, int(m.group(1)), _year(m.group(2)))),

    # -- instants ---------------------------------------------------------------------------
    (re.compile(r"^(?:as\s+of\s+|at\s+)?year[-\s]end\s+(\d{4})$"),
     lambda s, m: _instant(s, date(_year(m.group(1)), 12, 31))),
    (re.compile(r"^(?:as\s+of\s+|at\s+)?(january|february|march|april|may|june|july|august"
                r"|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})$"),
     lambda s, m: _instant(s, date(_year(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))))),
    (re.compile(r"^(?:as\s+of\s+|at\s+)?(?:the\s+)?end\s+of\s+(january|february|march|april"
                r"|may|june|july|august|september|october|november|december)\s+(\d{4})$"),
     lambda s, m: _instant(s, _month_end(_year(m.group(2)), _MONTHS[m.group(1)]))),
    (re.compile(r"^(?:as\s+of\s+)?(\d{4})-(\d{2})-(\d{2})$"),
     lambda s, m: _instant(s, date(int(m.group(1)), int(m.group(2)), int(m.group(3))))),
)


def normalise(surface: str) -> str:
    """Lowercase, whitespace collapsed, leading article and trailing punctuation removed.

    `"in the third quarter of 2022."` and `"The Third Quarter of 2022"` are one surface. The
    leading `in`/`during`/`for` is stripped because a writer's preposition is not part of the
    period, and nothing in the grammar distinguishes two surfaces by it.
    """
    text = re.sub(r"\s+", " ", surface.strip().lower()).strip(" .,;:")
    return re.sub(r"^(?:in|during|for|as\s+at)\s+", "", text)


def resolve(surface: str) -> PeriodSurface:
    """§13.4's grammar, applied to one surface. Unresolvable is an answer, not an exception."""
    text = normalise(surface)
    if not text:
        return _unresolvable(surface)
    for pattern, build in _RULES:
        match = pattern.match(text)
        if match is None:
            continue
        try:
            return build(surface, match)
        except ValueError:
            # A syntactically well-formed surface naming a date that does not exist
            # (`"February 30, 2022"`). Refused rather than clamped: §13.4 requires exact
            # endpoint equality, and a clamped endpoint would compare equal to a real one.
            return _unresolvable(surface)
    return _unresolvable(surface)


__all__ = [
    "PeriodSurface",
    "normalise",
    "resolve",
]
