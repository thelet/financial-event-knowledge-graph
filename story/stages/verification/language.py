"""The closed lexicons §13.6, §13.10, §13.14 and §13.15 refuse on, and the scans over them.

Responsibility: finding a construction in a string and saying exactly where. No decision is
made here — a match is not a refusal until a caller has asked whether the machinery §13.14
requires is present, or whether §13.10's six conditions hold. Keeping the two apart is what
lets `deterministic.py` read as the gate rather than as a regex file.

**Every lexicon is a module constant with the plan's own words in it.** §13.10's causal list,
§13.14's four construction classes and §13.15's forward-looking list are quoted verbatim from
the plan rather than paraphrased, because the whole claim of a closed lexicon is that a reader
can compare it against the section it implements in one pass. Where a member is added it is
marked and justified; where the plan's member could not be implemented it is recorded here
rather than dropped silently.

**What §13.10 asks for and the draft contract cannot supply.** Conditions 3 and 6 require the
binding to name *the cause term*, *the effect term* and *which marker occurrence* the sentence
relies on. `DraftSentence`, `FactBinding` and `Calculation` (S0, `story/core/models.py`) carry
no field for any of the three, so linkage cannot be tested as §13.10 states it. The
conservative closure is here instead: a cited span carrying **more than one** causal marker is
refused outright, because the sentence cannot say which one it means. That refuses some true
sentences — 367 passages carry two or more markers — and it never accepts a false one, which
is the direction §13.14 says the failure should point.

**Five of these lexicons and six of these functions now live in `story/core/lexicon.py`**, and
are re-exported below so every call site here reads unchanged. The move is
02-PLANNER-STABILIZATION §5's: a planner-side check of a stated direction against the spine's
needs `CHANGE_DIRECTION`, and `story/stages/generation/` may not import this stage. What went is
the vocabulary that answers *"which way does this word point"* — `COMPARATIVE_TERMS`,
`COMPARATIVE_DIRECTION`, `CHANGE_DIRECTION`, `CROSSING_TERMS`, `NEGATION_TOKENS`, their three
lookups, their three scanners, and the `LexicalMatch`/`_compile`/`_scan` machinery all of them
share. What stayed is the vocabulary only a *sentence-level* rule reads: §13.10's causal
markers and frames, §13.14's superlative, absence, temporal and state classes, §13.15's
forward-looking list, §13.6's foreign subjects and §13.7's connective lexicon.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

# Re-exported unchanged so this stage's call sites — and every test that names them through this
# module — keep resolving after the move to `core/`. Imported by name rather than with a star so
# `__all__` below stays the readable statement of what this module offers.
from story.core.lexicon import (
    CHANGE_DIRECTION,
    COMPARATIVE_DIRECTION,
    COMPARATIVE_TERMS,
    CROSSING_TERMS,
    NEGATION_TOKENS,
    LexicalMatch,
    _compile,
    _scan,
    change_direction,
    change_verbs,
    comparative_direction,
    comparatives,
    crossing_claims,
    crossing_direction,
    negated,
    occurrences,
)

#: §13.10 A, verbatim. Banned unconditionally in a `calculated` or `connective` sentence and in
#: any sentence with no citation.
CAUSAL_MARKERS: tuple[str, ...] = (
    "because of", "because", "caused by", "caused", "due to", "as a result of",
    "attributable to", "attributed to", "drove", "led to", "resulted in", "stemmed from",
    "contributed to", "owing to", "was impacted by", "is why", "explains", "reflects",
    "reflecting", "thanks to", "the driver of", "on the back of",
)

#: §13.10 B condition 1. The frame must name the source **in the sentence itself**; a frame in
#: the passage is the filing's voice, not the post's.
ATTRIBUTION_FRAMES: tuple[str, ...] = (
    "the company said", "the company stated", "the company disclosed",
    "management attributed", "management said", "management stated",
    "the filing states", "the filing stated", "according to the 10-k",
    "according to the 10-q", "according to the shareholder letter",
    "according to the earnings release",
)

#: Which `document_type` a frame's noun commits the sentence to (§13.10 B condition 4). A frame
#: naming no document — `"the company said"` — commits to none and the condition abstains; a
#: frame naming one and citing another is a refusal.
FRAME_DOCUMENT_TYPES: Mapping[str, str] = {
    "according to the 10-k": "10-K",
    "according to the 10-q": "10-Q",
    "according to the shareholder letter": "shareholder_letter",
    "according to the earnings release": "earnings_release",
    "the filing states": "",
    "the filing stated": "",
}

#: §13.14's first class, verbatim.
SUPERLATIVE_TERMS: tuple[str, ...] = (
    "only", "sole", "first", "last", "never", "always", "unprecedented", "worst", "best",
    "largest", "smallest", "record", "peak", "trough",
)

#: What a sentence says *about* a metric: a direction, a level, or a financial state. Read only
#: inside a `connective` sentence, where §13.14 permits no claim at all.
#:
#: **R8's defect E, measured**: replacing the demo's connective sentence with *"Opendoor's gross
#: margin turned positive during the period."* passed with zero findings. `_connective_findings`
#: looked at bindings, calculations and numerals and never at the prose, so a claim carrying no
#: number and no declaration walked straight through. A connective naming a package metric
#: beside one of these is refused.
#:
#: Deliberately includes the copulas. A `connective` sentence is *"transition, structure and
#: reference only"*; naming a metric and saying it **was** anything is a claim, and this is the
#: one place §13.14 says to prefer a false positive — a refused connective costs a sentence and
#: an accepted false one costs the claim.
STATE_TERMS: tuple[str, ...] = (
    "is", "are", "was", "were", "remains", "remained", "stays", "stayed", "stands", "stood",
    "positive", "negative", "profitable", "unprofitable", "loss", "losses", "profit",
    "turned", "swung", "flipped", "reversed", "crossed", "recovered",
    "rose", "rise", "risen", "fell", "fall", "fallen", "declined", "decline", "increased",
    "increase", "decreased", "decrease", "dropped", "improved", "improve",
    "deteriorated", "worsened", "widened", "narrowed", "expanded", "contracted",
    "grew", "grown", "shrank", "shrunk", "climbed", "slipped", "surged", "plunged",
    "reached", "hit", "came in", "held", "up", "down",
)
# The bare noun `"drop"` is deliberately absent while `"dropped"` is present: §16's read-only
# scan reads every string constant in `story/` case-folded against the Cypher keyword list, and
# a three-letter `"drop"` is `DROP`. Keeping the package-wide invariant literal is worth more
# than one inflection that `"fell"`, `"declined"` and `"slipped"` already cover.

#: §13.14's third class, verbatim.
ABSENCE_TERMS: tuple[str, ...] = (
    "has not", "have not", "did not", "no longer", "has yet to", "remains the only",
)

#: §13.14's fourth class, verbatim. Permitted when the token sits inside a period surface the
#: binding declared — *"the third quarter of 2022"* is naming a period, not ordering two package
#: items, and §13.14 says so in the rule's own qualifier.
TEMPORAL_TERMS: tuple[str, ...] = ("before", "after", "until", "since", "by the time")

#: §13.15, verbatim. Unconditional today: all 2,704 observations are `assertion_type: reported`
#: and no lane emits `guidance_issuance`, so nothing in the package could support or contradict
#: a forward-looking construction.
FORWARD_LOOKING_TERMS: tuple[str, ...] = (
    "expects", "expect", "guidance", "outlook", "forecasts", "forecast", "targets",
    "anticipates", "anticipate", "projects", "will be", "on track to", "guided to",
    "plans to reach", "full-year target",
)

#: The modal auxiliaries `_FORWARD_AUXILIARY` reads a future tense off, and **the reason there is
#: a pattern here at all is a measured hole** *(plan §9, 2026-08-26)*: `"it will recover next
#: year"` earned **zero** findings. The list above holds the literal `"will be"`, so
#: *"it will be higher next year"* was refused and the sentence beside it, making the same claim
#: with a different verb, was not. A literal list of auxiliary-plus-verb pairs has a next hole
#: after every hole closed, and §13.15 is unconditional — every future-tense construction in this
#: corpus is unsupported, whatever verb follows the modal.
#:
#: **`should` is not here and the plan's draft of this list had it** *(recorded rather than
#: dropped silently)*. The plan §9 wrote `will`/`would`/`should`; the implementation instruction
#: for this step wrote `will`/`would`/`shall`, and this follows the instruction. So
#: *"the margin should recover"* is still uncovered by the pattern — `should` is also the
#: deontic modal of ordinary prose (*"a connective sentence should carry no claim"*), which is
#: why it is the one of the four that would need its own false-positive measurement first.
FORWARD_LOOKING_AUXILIARIES: tuple[str, ...] = ("will", "would", "shall")

#: Words that may follow a modal without a verb following it — the whole of the false-positive
#: defence, and deliberately a closed list of function words rather than a guess at English.
#:
#: `will` is a noun as well as an auxiliary, and the noun is always followed by a preposition or
#: a determiner: *"the will of the shareholders"*, *"at will, in either direction"*. A modal is
#: followed by a verb, including the auxiliaries `be`, `have` and `been` — which is why those are
#: **absent** here: `"will have recovered"` must match, and a stop list holding `have` would let
#: it through. Nothing in this list is a verb in any inflection.
FORWARD_LOOKING_NON_VERBS: tuple[str, ...] = (
    "the", "a", "an", "of", "and", "or", "but", "that", "this", "these", "those",
    "its", "their", "our", "your", "his", "her", "it", "they", "we",
    "in", "on", "at", "for", "from", "with", "by", "as", "than", "to",
)

#: §13.6, from the plan's own sentence: *"No Zillow, no Offerpad, no Redfin, no index, no 'the
#: market'."* Every observation in the run carries `subject_entity_id: "opendoor"`, so any of
#: these named as a subject is an automatic refusal and a comparative post is not draftable.
#: A closed list and not a proper-noun heuristic: capitalisation would refuse *"Adjusted
#: EBITDA"* and a named-entity model would put a model inside a deterministic check.
FOREIGN_SUBJECTS: tuple[str, ...] = (
    "zillow", "offerpad", "redfin", "compass", "anywhere real estate", "realogy",
    "rocket companies", "the s&p 500", "the s&p", "the nasdaq", "the dow", "the index",
    "the market", "the industry", "peers", "competitors",
)

#: Words that carry no claim and therefore need no grounding in a cited span (§13.7 Rule B's
#: *"or be in the connective lexicon"*). Short on purpose: every word not here has to be found
#: in the span or resolve through an alias, and a long stop list is how paraphrase distance
#: stops measuring anything.
CONNECTIVE_LEXICON: frozenset[str] = frozenset({
    "a", "an", "and", "as", "at", "be", "been", "but", "by", "for", "from", "had", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "or", "over", "that", "the", "their",
    "then", "there", "this", "to", "was", "were", "which", "while", "with", "were",
})


_CAUSAL = _compile(CAUSAL_MARKERS)
_FRAME = _compile(ATTRIBUTION_FRAMES)
_SUPERLATIVE = _compile(SUPERLATIVE_TERMS)
_ABSENCE = _compile(ABSENCE_TERMS)
_TEMPORAL = _compile(TEMPORAL_TERMS)
_STATE = _compile(STATE_TERMS)
_FORWARD = _compile(FORWARD_LOOKING_TERMS)

#: §13.15's future tense as a construction rather than as a vocabulary: a modal auxiliary, an
#: optional negator, and a following word that is not one of the function words a *noun* `will`
#: takes. `_compile`'s `(?<![\w-])…(?![\w-])` convention is preserved by hand because the terms
#: here are a pattern rather than literals — `_compile` escapes what it is given, which is
#: exactly right for a lexicon and cannot express *"followed by a verb"*.
#:
#: The trailing `(?:-[a-z]+)*` is the boundary convention doing something real: `(?![\w-])`
#: refuses to end a match against a hyphen, so a hyphenated verb — *"will re-enter the market"* —
#: would match nothing at all without it, in either direction of backtracking.
#:
#: The span reported is the modal and the word after it, not the whole predicate. §13.15's remedy
#: is `DROP_SENTENCE`, so the span is a pointer into the sentence rather than a boundary anything
#: is cut on.
_FORWARD_AUXILIARY = re.compile(
    r"(?<![\w-])(?:" + "|".join(FORWARD_LOOKING_AUXILIARIES) + r")"
    r"(?:\s+not|\s+never)?"
    r"\s+(?!(?:" + "|".join(FORWARD_LOOKING_NON_VERBS) + r")(?![\w-]))"
    r"[a-z]+(?:-[a-z]+)*(?![\w-])",
    re.IGNORECASE,
)
_FOREIGN = _compile(FOREIGN_SUBJECTS)

_WORD = re.compile(r"[a-z0-9$%.,-]+")


def causal_markers(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_CAUSAL, text)


def attribution_frames(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_FRAME, text)


def superlatives(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_SUPERLATIVE, text)


def absence_claims(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_ABSENCE, text)


def temporal_orderings(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_TEMPORAL, text)


def state_terms(text: str) -> tuple[LexicalMatch, ...]:
    """Every direction, level or financial-state word in `text` (§13.14's connective rule)."""
    return _scan(_STATE, text)


def forward_looking(text: str) -> tuple[LexicalMatch, ...]:
    """§13.15's literals **and** the auxiliary construction, in one left-to-right sequence.

    Two scanners because the two questions are different in kind — `FORWARD_LOOKING_TERMS` is a
    vocabulary (`"guidance"`, `"outlook"`) and `_FORWARD_AUXILIARY` is a grammar — and one
    result because every caller treats a match as one finding at one span
    (`claims._forward_looking_findings`, and the title scan beside it).

    A match contained inside another is dropped, and it is reachable rather than theoretical:
    *"it will be higher next year"* is `"will be"` to the literal scan and `"will be"` to the
    pattern, and a sentence has not become twice as forward-looking for having been caught
    twice. The longer span wins, so the reported term is the whole construction.
    """
    ordered = sorted(_scan(_FORWARD, text) + _scan(_FORWARD_AUXILIARY, text),
                     key=lambda match: (match.start, -match.end))
    kept: list[LexicalMatch] = []
    for match in ordered:
        if any(match.start >= held.start and match.end <= held.end for held in kept):
            continue
        kept.append(match)
    return tuple(kept)


def foreign_subjects(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_FOREIGN, text)


def frame_document_type(frame: str) -> str:
    """The `document_type` a frame commits to, or `""` when it names no document."""
    return FRAME_DOCUMENT_TYPES.get(re.sub(r"\s+", " ", frame.strip().lower()), "")


def ungrounded_words(
    assertion: str,
    span: str,
    *,
    licensed: Iterable[str] = (),
) -> tuple[str, ...]:
    """§13.7 Rule B's lexical grounding: the assertion's content words absent from the span.

    A word passes when it occurs in the span, is in the connective lexicon, or is one the
    caller licensed — the metric aliases, the subject's surfaces and the resolved period
    surfaces, which are the three §13.7 names. Numerals are excluded because §13.1 has already
    compared them against the fact and a digit string is not a content word.

    Returns the words rather than a score: §13.7 raises a WARN on paraphrase distance and
    escalates to §13.16, and a model check that is handed *"0.34"* instead of *"the words
    `deteriorating` and `sharply` are not in the passage"* has been given a number in place of
    a pointer.
    """
    haystack = span.lower()
    allowed = {word.lower() for word in licensed}
    missing: list[str] = []
    for raw in _WORD.findall(assertion.lower()):
        word = raw.strip(".,-")
        if not word or word in CONNECTIVE_LEXICON or word in allowed:
            continue
        if any(character.isdigit() for character in word):
            continue
        if word in haystack:
            continue
        missing.append(word)
    # Order-preserving deduplication: a word repeated in the sentence is one missing word.
    seen: list[str] = []
    for word in missing:
        if word not in seen:
            seen.append(word)
    return tuple(seen)


__all__ = [
    "ABSENCE_TERMS",
    "ATTRIBUTION_FRAMES",
    "CAUSAL_MARKERS",
    "CHANGE_DIRECTION",
    "COMPARATIVE_DIRECTION",
    "COMPARATIVE_TERMS",
    "CONNECTIVE_LEXICON",
    "CROSSING_TERMS",
    "FOREIGN_SUBJECTS",
    "FORWARD_LOOKING_AUXILIARIES",
    "FORWARD_LOOKING_NON_VERBS",
    "FORWARD_LOOKING_TERMS",
    "FRAME_DOCUMENT_TYPES",
    "NEGATION_TOKENS",
    "STATE_TERMS",
    "SUPERLATIVE_TERMS",
    "TEMPORAL_TERMS",
    "LexicalMatch",
    "absence_claims",
    "attribution_frames",
    "causal_markers",
    "change_direction",
    "change_verbs",
    "comparative_direction",
    "comparatives",
    "crossing_claims",
    "crossing_direction",
    "foreign_subjects",
    "forward_looking",
    "frame_document_type",
    "negated",
    "occurrences",
    "state_terms",
    "superlatives",
    "temporal_orderings",
    "ungrounded_words",
]
