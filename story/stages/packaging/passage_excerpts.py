"""§10.2.1's ±400-character window, and the rule about which passages may never have one.

Responsibility: turning a full `:Passage.text` and a span worth centring on into an excerpt with
honest `char_start`/`char_end` offsets into that full text. It decides nothing about which
passages are in the package — that is `evidence_package.py` — and it reads nothing.

**The measurement this exists for.** §10.2.1's first draft sized passages from a median of 698
characters, which is the median over all 8,776 graph passages. **The median passage that backs
an observation is 2,144.5 characters** (n = 150, mean 2,070, max 4,300) — a table passage *is* a
markdown table and tables are long. At that size eight primaries with context is ~12,900 tokens,
which exceeds the local runtime's entire 8,192 context before the system prompt. So explanatory
and counter-evidence passages ship as a window and not whole.

**And the rule that limits it.** *"Excerpting is not applied to a passage a fact is bound to —
§13.7's Rule A needs the whole table."* Rule A's step 4 resolves `column_label` to a period
**within this passage**, and §13.7.1 measured that 179 of 485 `(passage_id, column_label)` pairs
are ambiguous. That check cannot run on a fragment: an excerpt that carried one column would let
an ambiguous pair look unique, turning a REFUSE into a pass. `refuse_excerpting_bound_passage`
is that rule with a raise behind it rather than a comment.

**Offsets are into the full text, always.** A citation must still resolve to the byte and an
evidence panel must be able to fetch the rest, so `char_start`/`char_end` are absolute and
`text[char_start:char_end]` is exactly what the package carries. A window that renumbered from
zero would make every excerpted citation unresolvable while looking correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

#: §10.2.1 point 2. Also `BudgetParameters.excerpt_radius_chars`, which is what a caller passes;
#: this is the value that field defaults to and the number the plan states.
EXCERPT_RADIUS_CHARS = 400


@dataclass(frozen=True, slots=True)
class Excerpt:
    """A window into a passage, with the offsets that make it resolvable.

    `matched_on` records *why* the window sits where it does — the surface that was found, or
    the empty string when nothing was and the window is the head of the passage. Without it a
    reader cannot tell an excerpt centred on the evidence from one that gave up and took the
    first 800 characters, and those are different qualities of evidence.
    """

    text: str
    char_start: int
    char_end: int
    excerpted: bool
    matched_on: str = ""

    @property
    def char_count(self) -> int:
        """The **full** passage's length is the caller's; this is the window's own."""
        return self.char_end - self.char_start


class BoundPassageExcerpted(ValueError):
    """An attempt to excerpt a passage a fact is bound to. §13.7's Rule A forbids it."""


def whole(text: str) -> Excerpt:
    """The passage unchanged, with the offsets that say so.

    Used for `primary_passages[]` and `context_passages[]`. `excerpted=False` and
    `char_start == 0` are not the same statement — a caller can and does check both — so both
    are set explicitly rather than left to a default.
    """
    return Excerpt(text=text, char_start=0, char_end=len(text), excerpted=False)


def window(
    text: str,
    *,
    needles: Sequence[str] = (),
    radius: int = EXCERPT_RADIUS_CHARS,
) -> Excerpt:
    """A ±`radius` window around the first needle found, or the head of the passage.

    Needles are tried **in the order given, and the earliest match of the first one that occurs
    wins** — not the earliest match of any of them. The order is the caller's statement of what
    the excerpt is evidence *of*: for a counter-evidence row the issue's `row_label` before its
    `rejected_claim`, for an explanatory hit the metric alias before the period surface. A
    "closest match wins" rule would let a period surface that happens to appear in a footnote
    move the window off the row the refusal is about.

    Matching is case-insensitive because a filed row label is `Gross Margin` in one table and
    `Gross margin` in the next — both real, in the same 2022Q3 filings *(measured)* — and a
    case-sensitive search would silently fall back to the head for one of them.

    A passage already shorter than the window is returned whole and `excerpted` is `False`: a
    "window" that contains the entire passage is not an excerpt, and reporting it as one would
    make an evidence panel offer to fetch a rest that does not exist.
    """
    if radius <= 0:
        raise ValueError(f"a non-positive radius is not a window: {radius}")
    if len(text) <= 2 * radius:
        return whole(text)

    folded = text.casefold()
    position, matched = -1, ""
    for needle in needles:
        candidate = needle.strip().casefold()
        if not candidate:
            continue
        found = folded.find(candidate)
        if found >= 0:
            position, matched = found + len(candidate) // 2, needle.strip()
            break

    if position < 0:
        # Nothing to centre on. The head is the honest fallback: it is where a table's caption
        # and its first labelled rows are, and it is the same slice `cypher.SEARCH_PASSAGES`
        # returns as `text_excerpt`, so an unmatched excerpt and the search row agree.
        return Excerpt(text=text[: 2 * radius], char_start=0, char_end=2 * radius, excerpted=True)

    start = max(position - radius, 0)
    end = min(start + 2 * radius, len(text))
    # Re-anchor when the window ran off the end, so an excerpt near the tail is still the full
    # width rather than a half window that looks like a short passage.
    start = max(end - 2 * radius, 0)
    return Excerpt(text=text[start:end], char_start=start, char_end=end,
                   excerpted=True, matched_on=matched)


def refuse_excerpting_bound_passage(passage_id: str, bound_passage_ids: Sequence[str]) -> None:
    """§13.7's Rule A, as a raise. Called before any excerpt of a passage that might be bound.

    A raise and not a warning: an excerpted table that a fact cites is a package that passes
    §13.7 step 4 by hiding the columns that would have refused it, and a package cannot disclose
    its way out of that.
    """
    if passage_id in set(bound_passage_ids):
        raise BoundPassageExcerpted(
            f"{passage_id} is bound to a fact in this package and may not be excerpted: "
            "§13.7's Rule A resolves `column_label` to a period *within this passage*, and "
            "§13.7.1 measured 179 of 485 (passage_id, column_label) pairs ambiguous — a "
            "fragment carrying one column would let an ambiguous pair look unique")


__all__ = [
    "EXCERPT_RADIUS_CHARS",
    "BoundPassageExcerpted",
    "Excerpt",
    "refuse_excerpting_bound_passage",
    "whole",
    "window",
]
