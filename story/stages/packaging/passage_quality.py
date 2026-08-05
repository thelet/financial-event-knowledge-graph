"""Whether a passage's *content* can carry evidence at all — S2's filter, before any role.

Responsibility: one function, `assess(text)`, and the four findings
`PassageUnusableReason` names. It selects nothing, reads nothing and knows nothing about the
candidate: a passage that says nothing says nothing whichever story is being written, and a
relevance rule dressed up as a quality rule is how a filter starts deleting inconvenient
evidence.

**Why it runs before role assignment.** EVIDENCE_ROLES_AND_SEMANTIC_FACTS §4 S2: *"Near-empty
passages never become counter-evidence."* A pipe-only markdown table promoted to
`counter_evidence[]` costs §10.2's budget, obliges §11's planner to write a counterpoint about
it, and gives the writer nothing to write — the exact shape of the vacuous counterpoint §11's
correction exists to stop. So the assessment is made first and the role is chosen from it.

**Unusable is not discarded.** The row stays in the package with
`quality_status = unusable` and a typed reason, because *"the run found nothing here"* and
*"the run never looked"* are different facts and only one of them can be reviewed.

---

**Every threshold below is a measurement over `graph-v1-0483dc6b4b10`, taken 2026-08-05.**

| measurement | value |
| --- | --- |
| `:Passage` nodes | 8,776 |
| passages whose text is structural characters only | 39 |
| passages carrying the Unicode replacement character | 0 |
| passages with < 20 content characters **or** < 3 word tokens | 377 (4.3%) |
| **passages an `:Observation` was actually read from** | **150** |
| the smallest of those 150 | **82 content characters, 12 word tokens** |
| how this module scores all 8,776 | 8,359 usable, 300 boilerplate, 78 short, 39 structural |
| how it scores the 150 that evidence a fact | **150 usable, none refused** |

The last two rows are what set the bar. The floor sits at roughly a quarter of the smallest
passage this corpus has ever evidenced a fact from — low enough that nothing the extraction
lanes have ever cited comes near it, high enough to refuse anything that cannot be a sentence.
A bar placed at the observed floor itself would have refused 1,325 passages, 15% of the corpus,
on the strength of one filing's formatting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from story.core.models import PassageQuality, PassageUnusableReason

#: Characters a markdown table, a rule line or a separator is made of. Removed before anything
#: is counted, so `| --- | --- |` and `____________________` measure zero rather than twenty.
_STRUCTURAL = re.compile(r"[|\-\+:_=~*#\s –—…\.]")

#: A word token: two or more letters. Digits deliberately do not count — `| 1 | 2 | 3 |` is a
#: table fragment with no proposition in it, and a rule that accepted digits would admit every
#: stray numeric row as evidence.
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

#: Characters that mean the extraction came back broken rather than short.
_REPLACEMENT_CHAR = "�"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: See the module docstring for the measurement each of these is placed against.
MIN_CONTENT_CHARS = 20
MIN_WORD_TOKENS = 3

#: The fraction of a passage that may be control characters before it is called corrupt. One
#: stray byte in a long table is a formatting artefact; a tenth of the text is a decode failure.
MAX_CONTROL_FRACTION = 0.1

#: Filing furniture that is grammatical, is over the length floor, and states nothing. Compared
#: against the text with every non-alphanumeric character removed and the result lower-cased, so
#: `"(in millions)\n\n(unaudited)"` and `"(In millions) (Unaudited)"` are one entry.
#:
#: **Closed, short and measured** — every member was counted in the corpus rather than imagined,
#: and the two that earn the set its place are `inmillionsunaudited` (31 passages) and
#: `nmnotmeaningful` (35): both clear `MIN_WORD_TOKENS` and neither states anything. A wider
#: vocabulary would start deciding that filed sentences are boilerplate, which is a judgement
#: this module has no evidence for.
BOILERPLATE: frozenset[str] = frozenset({
    "none",
    "notapplicable",
    "nmnotmeaningful",
    "unaudited",
    "inmillions",
    "inmillionsunaudited",
    "inthousands",
    "inthousandsunaudited",
    "markone",
    "dexhibits",
    "exhibits",
    "anchor",
    "tableofcontents",
})

#: The page-boundary marker the normaliser leaves behind, e.g. `"End page 4\n\nAnchor"`. A
#: pattern rather than thirteen more members of the set above, because the number varies.
_PAGE_MARKER = re.compile(r"^endpage\d+anchor$")

_ALPHANUMERIC = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True, slots=True)
class Assessment:
    """What was found, and the reason when the finding is `unusable`.

    A pair rather than two returns, because `PackagedPassage` refuses a reason without the
    finding it explains (`_a_reason_needs_a_finding`) and handing the two back separately would
    let a call site set one and forget the other.
    """

    quality: PassageQuality
    reason: PassageUnusableReason | None = None

    @property
    def usable(self) -> bool:
        return self.quality is PassageQuality.USABLE


def assess(text: str) -> Assessment:
    """One passage's content, in the order the findings are most specific.

    The order matters and is stated rather than incidental: `"(in millions) (unaudited)"` is
    boilerplate *and* short, and reporting it as `insufficient_content` would say the extractor
    got a fragment when what it got is a table caption. Empty and structural come first because
    they are the cheapest and most certain; corruption next, because a corrupt passage may be
    any length; boilerplate before length for the reason above.
    """
    if not text or not text.strip():
        return Assessment(PassageQuality.UNUSABLE,
                          PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY)
    if _REPLACEMENT_CHAR in text or _control_fraction(text) > MAX_CONTROL_FRACTION:
        return Assessment(PassageQuality.UNUSABLE, PassageUnusableReason.CORRUPTED_EXTRACTION)

    content = _STRUCTURAL.sub("", text)
    if not content:
        return Assessment(PassageQuality.UNUSABLE,
                          PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY)

    if _is_boilerplate(text):
        return Assessment(PassageQuality.UNUSABLE,
                          PassageUnusableReason.NO_RELEVANT_PROPOSITION)

    if len(content) < MIN_CONTENT_CHARS or len(_WORD.findall(text)) < MIN_WORD_TOKENS:
        return Assessment(PassageQuality.UNUSABLE, PassageUnusableReason.INSUFFICIENT_CONTENT)

    return Assessment(PassageQuality.USABLE)


def describe(assessment: Assessment, passage_id: str) -> str:
    """The sentence a disclosure warning carries. One wording, so two call sites cannot differ."""
    if assessment.reason is None:
        return f"{passage_id}: content assessed and usable"
    return (f"{passage_id}: {assessment.reason.value} — the passage carries no proposition this "
            "package could present as evidence, so it is carried as a diagnostic rather than as "
            "counter-evidence")


def _control_fraction(text: str) -> float:
    return len(_CONTROL.findall(text)) / len(text)


def _is_boilerplate(text: str) -> bool:
    normalized = _ALPHANUMERIC.sub("", text.lower())
    return normalized in BOILERPLATE or bool(_PAGE_MARKER.match(normalized))


__all__ = [
    "BOILERPLATE",
    "MAX_CONTROL_FRACTION",
    "MIN_CONTENT_CHARS",
    "MIN_WORD_TOKENS",
    "Assessment",
    "assess",
    "describe",
]
