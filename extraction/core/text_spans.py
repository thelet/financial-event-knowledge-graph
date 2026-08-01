"""Finding a retyped span in the text it was copied from, and reporting the text's own bytes.

Responsibility: given an original string and a candidate quotation of it, return the slice of
the *original* that the candidate quotes, or nothing. Boundaries: it knows about whitespace
and about a character-wise typographic fold, and about nothing else — no ontology, no model
answer, no passage identifier, no claim. It is `core/` material by that test alone, and it
lives here rather than inside a lane because it is the layer at which a shifted slice is a
visible defect rather than a plausible-looking string.

**The index map is the whole design, and getting it wrong is silent.** A model retypes a
sentence; the corpus stores it. What is recorded must be the corpus's own characters, so the
search happens on a normalised copy and the answer is an offset *into the original*. The first
implementation normalised with `transform(text)` and then sliced `text` with offsets taken over
the transformed string *(defect found by review 2026-08-02)*. `concept_resolution.fold` deletes
quote characters, so every deleted character before a match shifted the recorded slice left by
one — and because the shifted slice is still a contiguous run of the passage,
`validate_quoted_text` was satisfied by it. On this corpus 5,967 of 12,442 passages contain at
least one fold-deleted character, so the defect was reachable from just under half of them.

The fix is that the transform is applied **one character at a time** and each produced
character remembers the index of the original character it came from. That makes the map
correct by construction rather than by the transform happening to preserve length, and it is
why `transform` is declared below as a character-wise function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

EXACT = "exact"
FOLDED = "folded"


@dataclass(frozen=True)
class LocatedSpan:
    """A span found in the original, reported in the original's own characters."""

    text: str
    start: int
    end: int
    # `exact` when whitespace folding alone found it, `folded` when the typographic fold was
    # needed. Recorded because the second says the quoting party retyped the filing's quotes,
    # which is a fact about the quotation worth keeping and not a reason to refuse it.
    basis: str


def locate(
    text: str, candidate: str, *, fold: Callable[[str], str]
) -> LocatedSpan | None:
    """Find a retyped span in the original and return the original's characters.

    Whitespace is folded on both sides because the corpus stores Markdown, where a sentence
    may be broken across lines with column padding, and no model reproduces that. The
    typographic fold is a second attempt rather than the first so that the weaker match is
    recorded as such.

    `fold` is injected rather than imported so this module keeps no opinion about which
    characters are typography. It **must be character-wise** — `f(a + b) == f(a) + f(b)` — or
    the index map below is not a map; `concept_resolution.fold` is a `str.translate` and
    satisfies that, and a test asserts it rather than trusting it.
    """
    if not candidate or not candidate.strip() or not text:
        return None
    found = _find(text, candidate, transform=lambda character: character, basis=EXACT)
    if found is not None:
        return found
    return _find(text, candidate, transform=fold, basis=FOLDED)


def _find(text, candidate, *, transform, basis) -> LocatedSpan | None:
    normalized, offsets = normalize(text, transform)
    needle, _ = normalize(candidate, transform)
    if not needle:
        return None
    position = normalized.find(needle)
    if position < 0:
        return None
    start = offsets[position]
    end = offsets[position + len(needle) - 1] + 1
    return LocatedSpan(text=text[start:end], start=start, end=end, basis=basis)


def normalize(
    text: str, transform: Callable[[str], str] = lambda character: character
) -> tuple[str, list[int]]:
    """Transformed, whitespace-folded text beside the *original* index of each kept character.

    One pass rather than two because the composition is where the defect was: transforming
    first and mapping afterwards produces offsets into a string nobody keeps. Applying
    `transform` per character means a character it deletes simply contributes nothing to the
    map, and a character it expands contributes several entries all naming the same origin.
    """
    out: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, character in enumerate(text or ""):
        for produced in transform(character):
            if produced.isspace():
                if in_space or not out:
                    continue
                out.append(" ")
                offsets.append(index)
                in_space = True
                continue
            out.append(produced)
            offsets.append(index)
            in_space = False
    while out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets
