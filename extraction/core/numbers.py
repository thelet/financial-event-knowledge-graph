"""Cell and phrase text to numbers, with scale, sign and currency.

Three corpus facts drive this module:

1. **A value is spread across cells.** `| $ | 97,132 |` and `| 13.0 | % |` — the sigil, the
   magnitude and the percent sign are separate table columns, because HTML layout decided
   the split, not meaning.

2. **Negatives are parenthesised**, accounting-style: `(30)`, `(0.3)`, `(17,340)`. Dropping
   the parentheses turns a loss into a gain, and `(0.3)` — the smallest magnitude in the
   Q1 2025 table — is where that is least visible.

3. **An em dash is nil, not a number.** `| — |` in a reconciliation means the adjustment did
   not apply. It is also the character the pre-fix encoding defect corrupted, so a reader
   that treats a bare dash as extractable behaves differently on a corpus rebuilt before
   that correction.

Mojibake tolerance is deliberate and narrow. The corpus is clean now, but a `Â` stranded
inside a numeric cell is exactly what a pre-fix rebuild would produce, and a number reader
that silently returns None for `97,132Â` would look like a metric-matching failure rather
than a corpus problem.
"""

from __future__ import annotations

import re

from .models import SCALE_FACTOR, Scale

# Dashes filings use for nil, plus the ASCII hyphen.
NIL_DASHES = frozenset({"—", "–", "-", "‒", "―"})

# Latin-1 misreads of UTF-8 lead bytes. Stripped from numeric cells only.
_MOJIBAKE = str.maketrans("", "", "ÂâÃ ")

_SCALE_PATTERNS: tuple[tuple[re.Pattern[str], Scale], ...] = (
    (re.compile(r"\bin\s+billions\b", re.I), "billions"),
    (re.compile(r"\bin\s+millions\b", re.I), "millions"),
    (re.compile(r"\bin\s+thousands\b", re.I), "thousands"),
)
# "(In millions, except percentages, homes sold, ...)" — the exception list names filing
# labels, so it is kept as text rather than resolved to metric ids here.
_EXCEPTIONS = re.compile(r"\bexcept\s+(?P<items>[^)]+)", re.I)

_NUMBER = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")
_WORD_SCALE = re.compile(r"\b(thousand|million|billion)s?\b", re.I)
_WORD_FACTOR = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
# "$279 million", "$6.6 billion", "$1.3 billion"
_MONEY_PHRASE = re.compile(
    r"\$\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>thousand|million|billion)?s?\b",
    re.I,
)
_PERCENT_PHRASE = re.compile(r"(?P<value>-?\d+(?:\.\d+)?)\s*%")


def clean_cell(text: str) -> str:
    """Strip layout noise and encoding artifacts, keeping sign and decimal information."""
    return (text or "").translate(_MOJIBAKE).strip()


def is_nil(text: str) -> bool:
    """A dash cell. Nil, which is not the same as a reported zero and not a missing value."""
    cleaned = clean_cell(text)
    return cleaned in NIL_DASHES


def parse_magnitude(text: str) -> float | None:
    """One numeric cell to a number, honouring accounting parentheses.

    Returns None for anything that is not purely a number — a cell holding `$`, `%`, a row
    label or a dash is not a magnitude, and coercing it would invent data.
    """
    cleaned = clean_cell(text)
    if not cleaned or is_nil(cleaned):
        return None

    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative, cleaned = True, cleaned[1:-1].strip()

    cleaned = cleaned.replace("$", "").replace("%", "").strip()
    if not _NUMBER.match(cleaned):
        return None

    value = float(cleaned.replace(",", ""))
    return -value if negative else value


def apply_scale(value: float, scale: Scale | str) -> float:
    return value * SCALE_FACTOR[str(scale)]


def parse_scale_declaration(text: str) -> tuple[Scale, str | None] | None:
    """Find "(in thousands, except percentages)" and return the scale and its exceptions.

    Works on a table's header cell and on a standalone narrative passage alike, because the
    corpus uses both: 65 of 74 KPI-bearing tables declare it inline, 9 in the preceding
    passage *(verified 2026-08-01)*.
    """
    haystack = clean_cell(text)
    for pattern, scale in _SCALE_PATTERNS:
        if pattern.search(haystack):
            match = _EXCEPTIONS.search(haystack)
            exceptions = match.group("items").strip() if match else None
            return scale, exceptions
    return None


def scale_excludes(exceptions_text: str | None, row_label: str) -> bool:
    """Whether a row is exempt from the declared scale.

    "except percentages, homes sold, number of markets, homes purchased, and homes in
    inventory" excludes five of the eleven rows in the Q1 2025 table. Matching is on the
    filing's own words, both directions, because the exception list says "homes sold" while
    the row says "Homes sold" and elsewhere "Homes sold in period".
    """
    if not exceptions_text:
        return False
    label = clean_cell(row_label).lower().strip()
    if not label:
        return False
    for item in re.split(r",|\band\b", exceptions_text.lower()):
        item = item.strip().strip(".")
        if len(item) < 4:
            continue
        if item in label or label in item:
            return True
    # A percent row is excluded by the near-universal "except percentages" without the
    # label having to name itself.
    return "percentage" in exceptions_text.lower() and "%" in row_label


def parse_money_phrase(text: str) -> tuple[float, str] | None:
    """Prose money: "$279 million" -> (279000000.0, "$279 million").

    The letters state magnitudes as words, so the scale travels with the number rather than
    being declared once for a table.
    """
    match = _MONEY_PHRASE.search(clean_cell(text))
    if not match:
        return None
    value = float(match.group("value").replace(",", ""))
    word = (match.group("scale") or "").lower()
    if word:
        value *= _WORD_FACTOR[word]
    return value, match.group(0).strip()


def parse_percent_phrase(text: str) -> tuple[float, str] | None:
    match = _PERCENT_PHRASE.search(clean_cell(text))
    if not match:
        return None
    return float(match.group("value")), match.group(0).strip()


def word_scale(text: str) -> Scale | None:
    match = _WORD_SCALE.search(text or "")
    if not match:
        return None
    return {"thousand": "thousands", "million": "millions",
            "billion": "billions"}[match.group(1).lower()]
