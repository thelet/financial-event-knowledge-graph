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


# -- reading a figure out of prose ---------------------------------------------------------
#
# These read a *sentence*, not a cell, and they carry no ontology and no model answer: given a
# string they say what number it prints, what scale word it prints, and whether it names
# anything at all. They lived inside the narrative lane's rejection boundary until review moved
# them here *(2026-08-02)* — a module reviewed as a text reader is a module whose off-by-one
# defects are visible, which is the same argument that moved span location to `text_spans`.

# One printed number, wherever it sits in a phrase. The grouped form is first because
# alternation is ordered and `\d+` would otherwise read "17,164" as 17 followed by 164.
_MAGNITUDE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")

# Accounting parentheses, meaning the parentheses that wrap **the number itself** — optionally
# with its currency sigil or percent sign inside them. Written as a pattern rather than as
# "the phrase contains a '(' and a ')'" because the loose test read every incidental
# parenthetical as a sign: `'$43 million (Contribution Profit)'` came back as −43 and
# `'9.8% gross margin (GAAP)'` as −9.8 *(defect found by review 2026-08-02)*. A prose figure
# carries its label far more often than a cell does, which is why the cell reader never had to
# make this distinction.
_ACCOUNTING_NEGATIVE = re.compile(
    r"\(\s*[$€£]?\s*(?P<value>-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*%?\s*\)")

_WORD_SCALE_NAMES = {"thousand": "thousands", "million": "millions", "billion": "billions"}


def printed_magnitude(text: str) -> tuple[float, Scale | None] | None:
    """A prose figure as printed: `(magnitude, scale word)`, or None.

    Deliberately *not* `parse_money_phrase`, which applies the scale word and returns a scaled
    value. What is needed by a lane checking a model is the number as the sentence prints it,
    so the answer's stated value can be checked against it before anything is multiplied — a
    check that compares two already-scaled numbers cannot tell a scale error from a
    transcription error.

    **Exactly one number, and everything else ignored.** The rule is a count, not a pattern,
    which is what makes it both permissive and safe. "17,164 homes" and "44 markets" carry the
    noun with the figure and are read *(the first version anchored a regex to the whole string
    and refused both, 2026-08-01)*; "$149.7 million to $169.7 million" carries two figures and
    is refused, because there is no defensible way to choose between them and choosing wrongly
    is a silently wrong claim.

    Accounting parentheses are honoured as `parse_magnitude` honours them — `$(49) million` is
    negative forty-nine million — but **only when the parentheses wrap the number**. See
    `_ACCOUNTING_NEGATIVE`.
    """
    cleaned = clean_cell(text)
    if not cleaned:
        return None

    tokens = _MAGNITUDE.findall(cleaned)
    if len(tokens) != 1:
        return None

    match = _WORD_SCALE.search(cleaned)
    word = _WORD_SCALE_NAMES[match.group(1).lower()] if match else None

    value = float(tokens[0].replace(",", ""))
    negative = _ACCOUNTING_NEGATIVE.search(cleaned)
    if negative is not None and negative.group("value") == tokens[0] and value > 0:
        value = -value
    return value, word


def identifies_its_subject(span: str) -> bool:
    """Whether a quoted span says anything about what its figure measures.

    "to 5,988" is a true quotation of a passage and supports nothing: it locates a number and
    names neither the metric nor the period *(measured 2026-08-01 — six of one passage's spans
    came back in that shape after the prompt was rewritten to separate levels from changes)*.
    Two words of three letters or more, once the numbers are removed, is the smallest rule that
    separates it from "Sold 2,687 homes" and "or 9.8% gross margin".

    The numbers are stripped rather than the whole figure, because a quotation sometimes puts
    the noun in with the figure — "17,164 homes" — and removing the whole of that would delete
    the very word that identifies the subject.
    """
    return len(re.findall(r"[A-Za-z]{3,}", _MAGNITUDE.sub(" ", span or ""))) >= 2


def scale_word(scale: Scale | str) -> str:
    """The singular word a scale is printed as. Raises on anything that is not a scale."""
    return {"thousands": "thousand", "millions": "million", "billions": "billion"}[str(scale)]


def declares_scale_word(text: str, word: str) -> bool:
    """Whether `text` prints the scale word, singular or plural."""
    return bool(re.search(rf"\b{re.escape(word)}s?\b", text or "", re.I))


def scale_declaration_phrase(text: str, word: str) -> str | None:
    """The scale word with enough of its surroundings to be read back by a human.

    Recorded on the claim rather than the bare word: "in millions, except percentages" and
    "returned to positive Contribution Profit of $43 million" declare the same scale with very
    different strength, and a report that kept only "million" could not tell them apart.
    """
    match = re.search(rf".{{0,40}}\b{re.escape(word)}s?\b.{{0,20}}", text or "", re.I)
    return " ".join(match.group(0).split()) if match else None
