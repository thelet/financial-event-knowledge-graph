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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

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

#: §13.10 condition 5. A negation inside the same clause, before the marker, inverts it. 37
#: passages in the corpus carry one — *"not as a result of any general solicitation"* — and each
#: satisfies conditions 1–4 while the filing asserts the opposite.
NEGATION_TOKENS: tuple[str, ...] = ("not", "no", "never", "rather than", "other than")

#: §13.14's first class, verbatim.
SUPERLATIVE_TERMS: tuple[str, ...] = (
    "only", "sole", "first", "last", "never", "always", "unprecedented", "worst", "best",
    "largest", "smallest", "record", "peak", "trough",
)

#: §13.14's second class. **The plan's eleven terms are the first eleven; the rest were
#: measured onto the list by R8.** Mutating the demo's own accepted `compare_levels` sentence
#: to *"The GAAP Gross Margin **exceeded** the Adjusted Gross Margin by 15.9 percentage
#: points"* — leaving the declaration `left < right` untouched — passed with **zero findings**,
#: and so did `surpassed`, `topped`, `beat` and `outperformed`. Every one of those sentences is
#: false and inverted. Ordinary investor English walks around a closed word list, which is why
#: R8 also stopped treating a lexicon hit as the *trigger* for the comparison check: a
#: `compare_levels` sentence with no recognised comparative is now refused rather than passed
#: (`claims._comparison_text_findings`). The widening is the smaller half of that fix.
COMPARATIVE_TERMS: tuple[str, ...] = (
    "more", "less", "better", "worse", "faster", "slower", "higher", "lower", "outpaced",
    "held up", "lagged",
    # R8, measured: each of these passed as an inverted comparison before it was listed.
    "exceeded", "exceeds", "exceed", "surpassed", "surpasses", "surpass",
    "topped", "tops", "beat", "beats", "outperformed", "outperforms", "outperform",
    "outpaces", "outpace", "trailed", "trails", "trail", "lags", "lag",
    "underperformed", "underperforms", "underperform", "lead", "leads", "led",
    "above", "below", "sat above", "sat below", "sits above", "sits below",
    "came in above", "came in below", "stronger than", "weaker than",
    "ahead of", "behind", "greater than", "smaller than", "larger than",
)

#: Which way each comparative points: `True` when *"A <term> than B"* asserts A above B.
#:
#: **Total over `COMPARATIVE_TERMS`, and it is what makes a declared comparison checkable
#: against its own sentence.** A `Calculation` names its two sides positionally and the prose
#: names them in words; without this map the verifier would recompute `left < right` over a
#: declaration and never ask whether the sentence said `lower` or `higher` — which is the word
#: a reader actually reads, and the one an input list swapped by accident would contradict.
#: `"held up"` points up: §13.14's own attack sentence is *"contribution profit held up better
#: than adjusted gross profit"*, where both of its comparatives point the same way.
#:
#: A term in `COMPARATIVE_TERMS` and absent here resolves to `None`, which a caller must refuse
#: on — `comparative_direction`'s docstring says so and `claims.py` obeys it. Totality is
#: asserted by a test rather than trusted, because a widened lexicon with an unstated polarity
#: would turn a refusal into a pass at exactly the place this map exists to guard.
COMPARATIVE_DIRECTION: Mapping[str, bool] = {
    "more": True, "less": False, "better": True, "worse": False, "faster": True,
    "slower": False, "higher": True, "lower": False, "outpaced": True, "held up": True,
    "lagged": False,
    "exceeded": True, "exceeds": True, "exceed": True,
    "surpassed": True, "surpasses": True, "surpass": True,
    "topped": True, "tops": True, "beat": True, "beats": True,
    "outperformed": True, "outperforms": True, "outperform": True,
    "outpaces": True, "outpace": True,
    "trailed": False, "trails": False, "trail": False, "lags": False, "lag": False,
    "underperformed": False, "underperforms": False, "underperform": False,
    "lead": True, "leads": True, "led": True,
    "above": True, "below": False, "sat above": True, "sat below": False,
    "sits above": True, "sits below": False,
    "came in above": True, "came in below": False,
    "stronger than": True, "weaker than": False,
    "ahead of": True, "behind": False,
    "greater than": True, "smaller than": False, "larger than": True,
}

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


@dataclass(frozen=True, slots=True)
class LexicalMatch:
    """One located substring, with the span a finding points at.

    The stage's single span type. A lexicon hit, a numeral-covering `fact_binding` span and an
    occurrence of a declared period surface are all *"these characters, that text"*, and three
    dataclasses saying so would have to be converted into each other at every boundary.
    """

    term: str
    start: int
    end: int

    def contains(self, start: int, end: int) -> bool:
        return self.start <= start and end <= self.end


def _alternation(terms: Sequence[str]) -> str:
    """Longest alternative first, whitespace tolerant — the rule `numerals._alternation` uses.

    Longest first matters here for the same reason it does there: `"because"` is inside
    `"because of"` and `"not"` is inside `"not as a result of"`, and a shortest-match scanner
    reports a span that highlights half the construction.
    """
    return "|".join(
        re.escape(term).replace(r"\ ", r"\s+")
        for term in sorted(terms, key=len, reverse=True)
    )


def _compile(terms: Sequence[str]) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])(?:{_alternation(terms)})(?![\w-])", re.IGNORECASE)


_CAUSAL = _compile(CAUSAL_MARKERS)
_FRAME = _compile(ATTRIBUTION_FRAMES)
_NEGATION = _compile(NEGATION_TOKENS)
_SUPERLATIVE = _compile(SUPERLATIVE_TERMS)
_COMPARATIVE = _compile(COMPARATIVE_TERMS)
_ABSENCE = _compile(ABSENCE_TERMS)
_TEMPORAL = _compile(TEMPORAL_TERMS)
_STATE = _compile(STATE_TERMS)
_FORWARD = _compile(FORWARD_LOOKING_TERMS)
_FOREIGN = _compile(FOREIGN_SUBJECTS)

#: Clause boundaries for §13.10 condition 5's *"within the same clause"*. A comma **is** a
#: boundary here and is not one in `numerals._clauses`, and the difference is deliberate:
#: negation scope is clausal — *"revenue fell, not as a result of pricing"* — while a change
#: verb and its numeral routinely sit either side of a comma.
#:
#: **The `.` may not sit between two digits**, which `numerals._CLAUSE_BOUNDARY` already knew
#: and this pattern did not. Measured by R8: *"The GAAP Gross Margin was **not** 15.9
#: percentage points lower than the Adjusted Gross Margin."* — the `.` inside `15.9` opened a
#: new clause between `not` and `lower`, so `negated()` reported `False` and the inversion
#: passed. The same hole was live for §13.10 wherever a cited span carried a decimal between a
#: negation and its causal marker.
_CLAUSE = re.compile(r"[,;:()—–]|\.(?!\d)|(?<!\d)\.|\band\b|\bbut\b", re.IGNORECASE)

_WORD = re.compile(r"[a-z0-9$%.,-]+")


def _scan(pattern: re.Pattern[str], text: str) -> tuple[LexicalMatch, ...]:
    return tuple(
        LexicalMatch(term=match.group(0), start=match.start(), end=match.end())
        for match in pattern.finditer(text)
    )


def occurrences(text: str, needle: str) -> tuple[LexicalMatch, ...]:
    """Every occurrence of `needle` in `text`, case-insensitively.

    Case-insensitive because a period surface is declared as the writer rendered it mid-sentence
    and the same surface opens a sentence capitalised; a case-sensitive scan would leave the
    numeral inside *"Third quarter of 2022"* uncovered and refuse a correct draft.
    """
    if not needle:
        return ()
    found: list[LexicalMatch] = []
    haystack, target = text.lower(), needle.lower()
    start = haystack.find(target)
    while start != -1:
        found.append(LexicalMatch(term=text[start:start + len(needle)],
                                  start=start, end=start + len(needle)))
        start = haystack.find(target, start + 1)
    return tuple(found)


def causal_markers(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_CAUSAL, text)


def attribution_frames(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_FRAME, text)


def superlatives(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_SUPERLATIVE, text)


def comparatives(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_COMPARATIVE, text)


def comparative_direction(term: str) -> bool | None:
    """`True` for a term that puts its subject above its object, `False` below, `None` unknown.

    `None` is a real answer and a caller must refuse on it rather than assume a direction: a
    comparative whose polarity the lexicon does not state is a comparison nothing can check.
    """
    return COMPARATIVE_DIRECTION.get(re.sub(r"\s+", " ", term.strip().lower()))


def absence_claims(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_ABSENCE, text)


def temporal_orderings(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_TEMPORAL, text)


def state_terms(text: str) -> tuple[LexicalMatch, ...]:
    """Every direction, level or financial-state word in `text` (§13.14's connective rule)."""
    return _scan(_STATE, text)


def forward_looking(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_FORWARD, text)


def foreign_subjects(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_FOREIGN, text)


def frame_document_type(frame: str) -> str:
    """The `document_type` a frame commits to, or `""` when it names no document."""
    return FRAME_DOCUMENT_TYPES.get(re.sub(r"\s+", " ", frame.strip().lower()), "")


def negated(text: str, marker: LexicalMatch) -> bool:
    """§13.10 condition 5: a negation token precedes the marker within the same clause.

    Scoped to the clause the marker sits in rather than to the whole span, because a passage
    runs to thousands of characters and a `"not"` four sentences earlier negates nothing.
    """
    start = 0
    for boundary in _CLAUSE.finditer(text, 0, marker.start):
        start = boundary.end()
    return _NEGATION.search(text, start, marker.start) is not None


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
    "COMPARATIVE_DIRECTION",
    "COMPARATIVE_TERMS",
    "CONNECTIVE_LEXICON",
    "FOREIGN_SUBJECTS",
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
    "comparative_direction",
    "comparatives",
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
