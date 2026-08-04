"""§13.4's closed grammar: the period surfaces a draft may write, and nothing else.

Responsibility: `"the third quarter of 2022"` → `(duration, 2022-07-01, 2022-09-30)`, and
`"the quarter"` → unresolvable; plus `scan`, which answers the same question about a whole
sentence rather than about one declared surface. Pure functions of one string; the endpoints
are then handed to `story.core.periods`, which owns the key and the shape, so this module never
mints a period key itself.

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

**R9 added `scan`, and the reason is a defect this module's own strength concealed.** The
grammar was only ever asked about the string a *binding declared*; nobody asked it what the
*sentence* said. R8 recorded — and R9 measured false — the claim that a period surface was
already protected because it contains numerals and §13.1's coverage rule forces it into the
text. That holds only when the period phrase carries a numeral. Measured against `863edf6` on
the demo's own accepted draft, with the binding still declaring the true `"the third quarter of
2022"` and the `rendered` span honestly re-anchored, *"…for **the fourth quarter**."*, *"…for
**the full year**."* and *"…for **the most recent quarter**."* all **passed with zero
findings**. `"the fourth quarter of 2022"` was refused, but only as `unbound_numeral` on the
year. So the protection was an accident of numeral coverage, and it stopped exactly where the
numeral did.

`scan` therefore reads a sentence two ways. **Tier one** is this grammar itself, applied
unanchored: the phrases §13.4 can resolve. **Tier two** is `_DEICTIC_PERIOD`, the closed list
of period-shaped phrases the grammar deliberately refuses — §13.4's own `"the quarter"`, and
the family it belongs to. A phrase in tier two is not resolved and never given a period; it is
reported so a caller can tell *"this sentence pins no period"* from *"this sentence pins a
period the grammar cannot read"*, which are different remedies. A sentence carrying neither is
a sentence making no period claim, and `scan` returns nothing for it.
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


# Each rule is `(source, builder)`, and the source carries **no anchors**: `resolve` compiles it
# anchored and `scan` compiles it bounded, so the two readings of one vocabulary cannot drift
# apart. Ordered longest-and-most-specific first for the same reason §13.5's alias index is:
# `"fiscal 2022"` contains `"2022"`, and `"nine months ended September 30, 2022"` contains
# `"September 30, 2022"`, so a scanner that tried the short form first would resolve a
# nine-month window to an instant.
_MONTH_NAMES = ("january|february|march|april|may|june|july|august"
                "|september|october|november|december")

_RULE_SOURCES: tuple[tuple[str, Callable[[str, re.Match[str]], PeriodSurface]], ...] = (
    # -- year-to-date, worded and coded -----------------------------------------------------
    (r"(?:the\s+)?nine\s+months\s+ended\s+september\s+30,?\s+(\d{4})",
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 9, 30))),
    (r"9m\s*(\d{2}|\d{4})",
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 9, 30))),
    (r"(?:the\s+)?six\s+months\s+ended\s+june\s+30,?\s+(\d{4})",
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 6, 30))),
    (r"(?:1h|6m)\s*(\d{2}|\d{4})",
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 6, 30))),
    (r"(?:the\s+)?first\s+half\s+of\s+(\d{4})",
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 6, 30))),

    # -- fiscal years -----------------------------------------------------------------------
    (r"(?:fiscal(?:\s+year)?|full\s+year|fy)\s*(\d{4})",
     lambda s, m: _duration(s, date(_year(m.group(1)), 1, 1), date(_year(m.group(1)), 12, 31))),

    # -- quarters ---------------------------------------------------------------------------
    (r"(?:the\s+)?(first|second|third|fourth)\s+quarter\s+(?:of\s+|ended\s+)?"
     r"(?:fiscal\s+)?(\d{4})",
     lambda s, m: _quarter(s, _ORDINAL_QUARTER[m.group(1)], _year(m.group(2)))),
    (r"q\s*([1-4])\s*(?:fy)?\s*(\d{2}|\d{4})",
     lambda s, m: _quarter(s, int(m.group(1)), _year(m.group(2)))),
    (r"([1-4])q\s*(?:fy)?\s*(\d{2}|\d{4})",
     lambda s, m: _quarter(s, int(m.group(1)), _year(m.group(2)))),

    # -- instants ---------------------------------------------------------------------------
    (r"(?:as\s+of\s+|at\s+)?year[-\s]end\s+(\d{4})",
     lambda s, m: _instant(s, date(_year(m.group(1)), 12, 31))),
    (rf"(?:as\s+of\s+|at\s+)?({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+(\d{{4}})",
     lambda s, m: _instant(s, date(_year(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))))),
    (rf"(?:as\s+of\s+|at\s+)?(?:the\s+)?end\s+of\s+({_MONTH_NAMES})\s+(\d{{4}})",
     lambda s, m: _instant(s, _month_end(_year(m.group(2)), _MONTHS[m.group(1)]))),
    (r"(?:as\s+of\s+)?(\d{4})-(\d{2})-(\d{2})",
     lambda s, m: _instant(s, date(int(m.group(1)), int(m.group(2)), int(m.group(3))))),
)

_RULES: tuple[tuple[re.Pattern[str], Callable[[str, re.Match[str]], PeriodSurface]], ...] = tuple(
    (re.compile(rf"\A(?:{source})\Z"), build) for source, build in _RULE_SOURCES)

# Bounded rather than `\b`-delimited: several sources begin with an optional `(?:the\s+)?`, and
# a `\b` in front of an optional group asserts about whichever alternative the engine tried
# first. A lookaround for "no word character and no hyphen either side" says the thing meant —
# `"3q22"` inside `"13q225"` is not a quarter, and `"2022"` inside `"2022-09-30"` is not a year.
_SCAN_RULES: tuple[tuple[re.Pattern[str], Callable[[str, re.Match[str]], PeriodSurface]], ...] = (
    tuple((re.compile(rf"(?<![\w-])(?:{source})(?![\w-])"), build)
          for source, build in _RULE_SOURCES))

#: §13.4 names `"the quarter"` UNRESOLVABLE. This is that phrase's **family**: a period noun the
#: grammar knows, under a qualifier that points at a period without pinning one. It is a closed
#: list and deliberately not a parser — nothing here ever produces a date, and its only job is
#: to let a caller distinguish *"the sentence pins no period"* from *"the sentence pins one the
#: grammar refuses"*. The three measured attacks — `"the fourth quarter"`, `"the full year"`,
#: `"the most recent quarter"` — are each one qualifier plus one noun, and so is every deictic
#: form a writer reaches for when the period is meant to be inferred from context.
_QUALIFIER = (r"the|this|that|its|our|a|an|last|latest|next|prior|previous|current"
              r"|most\s+recent|recent|earlier|later|same|full|fiscal|opening|closing"
              r"|first|second|third|fourth|final|nine|six|three|twelve")
_PERIOD_NOUN = r"quarters?|halves|half|years?|periods?|months?"
_DEICTIC_PERIOD = re.compile(
    rf"(?<![\w-])(?:(?:{_QUALIFIER})\s+)+(?:{_PERIOD_NOUN})(?![\w-])"
    # The coded forms stripped of the year the grammar requires. `"in Q3"` names a quarter and
    # names no year, so it is period-shaped and unresolvable exactly as `"the quarter"` is.
    rf"|(?<![\w-])(?:q[1-4]|[1-4]q|1h|2h|9m|6m)(?![\w-])"
    rf"|(?<![\w-])year[-\s]?(?:to[-\s]?date|end)(?![\w-])")

_WHITESPACE = re.compile(r"\s+")


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


@dataclass(frozen=True, slots=True)
class PeriodPhrase:
    """One period-naming phrase found *in prose*, and what §13.4's grammar makes of it.

    Distinct from `PeriodSurface` because the question is different: a surface answers *"what
    does this declared string mean"*, and a phrase answers *"which periods does this sentence
    name"* — of which there may be several, and some of which the grammar refuses.

    **No character span**, on the same reasoning `MetricSurfaceOccurrence` gives: the scan runs
    over a lowercased, whitespace-collapsed copy, so an offset into it does not point at the
    sentence the reader sees, and a span that lies about where it points is worse than none.
    `text` is the matched phrase, which is what a finding should quote.
    """

    text: str
    period: PeriodSurface

    @property
    def resolved(self) -> bool:
        return self.period.resolved


def scan(text: str) -> tuple[PeriodPhrase, ...]:
    """Every period phrase `text` names, left to right, longest and most specific winning.

    Two tiers, in this order and never the other: the grammar's own rules first, and
    `_DEICTIC_PERIOD` only over what they left, so `"the third quarter of 2022"` is never also
    reported as the bare `"the third quarter"`. Overlap is what consumes, not equality — the
    short match inside a long one starts at a different offset.

    **Within tier one the longest match wins, and `_RULE_SOURCES`' own order does not.** That
    order is `resolve`'s, where the only ambiguity is over a whole string; scanning has a second
    kind, because a rule's phrase can sit *inside* another rule's. `"fiscal 2022"` is inside
    `"the third quarter of fiscal 2022"`, and rule order puts the fiscal-year rule first — so
    scanning in rule order read a true Q3 sentence as naming FY2022 and contradicted its own
    binding. Longest-match-wins is §13.5's rule for the alias index and it is this one's too.
    """
    lowered = _WHITESPACE.sub(" ", text.lower())
    candidates: list[tuple[int, int, int, PeriodSurface]] = []
    for order, (pattern, _build) in enumerate(_SCAN_RULES):
        for match in pattern.finditer(lowered):
            period = resolve(match.group(0))
            # `resolve` is the single authority, so a phrase the scan shapes but the grammar
            # rejects — `"February 30, 2022"` — is not silently promoted to tier two either.
            if period.resolved:
                candidates.append((match.start(), match.end(), order, period))

    taken: list[tuple[int, int]] = []
    found: list[tuple[int, PeriodPhrase]] = []

    def claim(start: int, end: int) -> bool:
        if any(taken_start < end and start < taken_end for taken_start, taken_end in taken):
            return False
        taken.append((start, end))
        return True

    for start, end, _order, period in sorted(
            candidates, key=lambda item: (item[0] - item[1], item[0], item[2])):
        if claim(start, end):
            found.append((start, PeriodPhrase(text=lowered[start:end], period=period)))
    for match in _DEICTIC_PERIOD.finditer(lowered):
        if claim(*match.span()):
            found.append((match.start(),
                          PeriodPhrase(text=match.group(0),
                                       period=_unresolvable(match.group(0)))))
    return tuple(phrase for _start, phrase in sorted(found, key=lambda pair: pair[0]))


__all__ = [
    "PeriodPhrase",
    "PeriodSurface",
    "normalise",
    "resolve",
    "scan",
]
