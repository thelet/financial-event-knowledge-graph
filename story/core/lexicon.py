"""The closed direction lexicons, and the scan that locates one of their terms in a string.

Responsibility: which way a word points — up, down, above, below, across zero — and where in a
string it was found. Nothing here decides anything. A match is not a refusal until a caller has
asked whether the word agrees with a direction code computed, which is `story/stages/`' question
in both of the stages that ask it.

**Why these lists sit in `core/` while the rest of the verification vocabulary does not.** All
of them lived in `story/stages/verification/language.py` until a plan check needed to compare a
planner's stated direction against the spine's. `story/stages/generation/` may not import
`story/stages/verification/` (`test_no_stage_imports_another_stage`), and the two other ways out
were rejected in 02-PLANNER-STABILIZATION §5: threading a hundred-entry mapping through the
composition root is plumbing rather than a boundary, and a checked copy inside the generation
stage is a hundred words in two places. A closed word list carrying application meaning is what
`core/` is for, and `story/core/numerals.py` already holds `CHANGE_VERBS` — the list
`CHANGE_DIRECTION` is asserted total over (`test_story_verification_derived.py`). The vocabulary
was already split across two layers; moving this half gives it one home and one re-export.

`COMPARATIVE_TERMS`, `NEGATION_TOKENS`, `negated` and the clause pattern came with it because
`comparatives` and `negated` cannot be separated from the lists they scan without leaving a
`core/` module importing a stage — the import the move exists to delete. Everything the
verification stage alone reads stayed in `language.py`, which re-exports every name here so no
call site changed.

**§16's read-only scan reads every non-docstring string constant in `story/` case-folded against
the Cypher keyword list, and it walks `story/core/` exactly as it walks `story/stages/`.** The
two deliberate absences recorded on `CHANGE_DIRECTION` below therefore survive the move
unchanged: `"drop"`, `"drops"` and `"dropping"` are missing while `"dropped"` is present, and
`"contract"` and `"contracts"` are missing for a second, ontological reason. Neither is a gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


# -- the lexicons ----------------------------------------------------------------------------

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
#: Which way each change verb points, for DETERMINISTIC_FACT_TOOLS §6's orientation rule.
#: `True` when the word says the quantity went up, `False` down, **`None` when the word states
#: no direction of the stored number at all**.
#:
#: Total over `story.core.numerals.CHANGE_VERBS` — asserted by a test, exactly as
#: `COMPARATIVE_DIRECTION`'s totality over `COMPARATIVE_TERMS` is — because a widened change-verb
#: lexicon with an unstated polarity would turn a refusal into a pass at the one place this map
#: guards: a sentence that says *"fell"* over a derived fact whose `display_semantics` is
#: *"increased by"*.
#:
#: **H1 widened this from thirteen words, and the widening is only half the repair.** The
#: adversarial review drove ordinary investor English past the thirteen and every one was
#: **accepted** over a fact whose `display_semantics` is *"decreased by"* and whose `result` is
#: `-446000000.0`: `grew`, `climbed`, `surged`, `gained`, `jumped`, `expanded`. The rule read
#: this map to decide whether it ran at all, so an unlisted synonym made it abstain silently —
#: exactly the arrangement `COMPARATIVE_TERMS` records R8 finding for comparatives, in the same
#: place, one release later. Widening alone would only move the boundary, so
#: `derived_facts._direction_findings` no longer lets the vocabulary decide whether it runs: a
#: directional derivation whose sentence states **no** word this map gives the computed
#: direction to is `derived_direction_not_stated_in_text`. The list below is what keeps that
#: from refusing true sentences; it is not what makes the check sound.
#:
#: **`None` is desirability or magnitude, never a hedge, and the boundary is a measurement.**
#: `improved` is a statement about the quantity's desirability and not about its sign —
#: `direct_selling_costs` is stored negative on 46 of 46 canonical values, so a cost that
#: improves is a *rise* in the stored number — and `worsened`, `deteriorated`, `strengthened`,
#: `weakened` and `recovered` say the same thing about the same convention. `widened` and
#: `narrowed` describe a magnitude: a widening loss is a fall. `turned`, `swung`, `flipped` and
#: `reversed` name a sign change without saying which way, which is `CROSSING_TERMS`' question
#: and not this one. Under the H1 rule a `None` word no longer means the check abstains — it
#: means that word does not *state* the direction, so a sentence carrying only `None` words is
#: refused for saying nothing rather than accepted for saying nothing.
#:
#: **`drop`, `drops` and `dropping` are deliberately absent while `dropped` is present**, for
#: `STATE_TERMS`' reason: §16's read-only scan reads every string constant in `story/`
#: case-folded against the Cypher keyword list, and a three-letter `"drop"` is `DROP`. `fell`,
#: `declined`, `slipped` and `dropped` cover the same claim. `contract` and `contracts` are
#: absent for a second reason — `"contracts"` is a `DECLARED_AMBIGUOUS` **metric** surface in
#: this ontology — while `contracted` and `contraction` are unambiguous and are listed.
CHANGE_DIRECTION: Mapping[str, bool | None] = {
    # -- the quantity went up ----------------------------------------------------------------
    "rose": True, "rise": True, "rises": True, "risen": True, "rising": True,
    "increased": True, "increase": True, "increases": True, "increasing": True,
    "up": True, "higher": True,
    "grew": True, "grow": True, "grows": True, "growing": True, "grown": True,
    "growth": True,
    "climbed": True, "climb": True, "climbs": True, "climbing": True,
    "surged": True, "surge": True, "surges": True, "surging": True,
    "jumped": True, "jump": True, "jumps": True, "jumping": True,
    "gained": True, "gain": True, "gains": True, "gaining": True,
    "expanded": True, "expand": True, "expands": True, "expanding": True,
    "expansion": True,
    "rebounded": True, "rebound": True, "rebounds": True,
    "doubled": True, "tripled": True,
    # -- the quantity went down --------------------------------------------------------------
    "fell": False, "fall": False, "falls": False, "fallen": False, "falling": False,
    "declined": False, "decline": False, "declines": False, "declining": False,
    "decreased": False, "decrease": False, "decreases": False, "decreasing": False,
    "dropped": False, "down": False, "lower": False,
    "slipped": False, "slip": False, "slips": False, "slipping": False,
    "slid": False, "slide": False, "slides": False, "sliding": False,
    "shrank": False, "shrunk": False, "shrink": False, "shrinks": False, "shrinking": False,
    "contracted": False, "contracting": False, "contraction": False,
    "plunged": False, "plunge": False, "plunges": False, "plunging": False,
    "tumbled": False, "tumble": False, "tumbles": False, "tumbling": False,
    "sank": False, "sunk": False, "sinking": False,
    "reduced": False, "reduce": False, "reduces": False, "reducing": False,
    "reduction": False,
    "halved": False, "retreated": False, "retreat": False, "retreats": False,
    "eased": False, "softened": False,
    # -- desirability, magnitude or an unstated sign change: no direction of the number -------
    "improved": None, "improve": None, "improves": None, "improving": None,
    "improvement": None,
    "worsened": None, "worsen": None, "worsens": None, "worsening": None,
    "deteriorated": None, "deteriorate": None, "deteriorates": None, "deteriorating": None,
    "strengthened": None, "strengthen": None, "strengthens": None, "strengthening": None,
    "weakened": None, "weaken": None, "weakens": None, "weakening": None,
    "recovered": None, "recover": None, "recovers": None, "recovering": None,
    "recovery": None,
    "widened": None, "widen": None, "widens": None, "widening": None,
    "narrowed": None, "narrow": None, "narrows": None, "narrowing": None,
    "changed": None, "change": None, "changes": None, "changing": None,
    "moved": None, "move": None, "moves": None, "moving": None,
    "swung": None, "swing": None, "swings": None,
    "turned": None, "turn": None, "turns": None,
    "flipped": None, "flip": None, "flips": None,
    "reversed": None, "reverse": None, "reverses": None,
}
#: Which way a **sign-crossing** phrase points: `True` when it asserts the quantity crossed
#: zero, `False` when it asserts it did not (§6, over a `crossed_zero` derivation).
#:
#: **This lexicon exists because a word-valued derived fact had no prose check at all.** H1's
#: third finding, reproduced: `crossed_zero` over `$556M -> $110M` computes `did_not_cross`,
#: `SEMANTIC_DIRECTION[CROSSED_ZERO]` is `None` so the change-verb rule abstained, the fact
#: carries no numeral so §13.1 had nothing to compare, and `ungrounded_words` reached only an
#: `EvidenceScopeFact`. *"Adjusted gross profit **turned negative** between the second quarter
#: of 2022 and the third quarter of 2022."* was **accepted with zero findings** over a fact
#: saying it did not cross.
#:
#: **Total by construction — every member states a polarity — and the rule that reads it fails
#: closed.** `derived_facts._crossing_findings` refuses a sentence stating a phrase that
#: disagrees *and* a sentence stating none at all, so an unlisted paraphrase costs a true
#: sentence rather than admitting a false one. That is the direction §13.14 says the failure
#: should point, and it is the opposite of the arrangement this lexicon replaces.
CROSSING_TERMS: Mapping[str, bool] = {
    # -- asserts a crossing ------------------------------------------------------------------
    "crossed zero": True, "crosses zero": True, "cross zero": True, "crossing zero": True,
    "crossed into negative territory": True, "crossed into positive territory": True,
    "into negative territory": True, "into positive territory": True,
    "turned negative": True, "turned positive": True,
    "turns negative": True, "turns positive": True,
    "went negative": True, "went positive": True,
    "swung to a loss": True, "swung to a profit": True,
    "swung from a profit to a loss": True, "swung from profit to loss": True,
    "swung from a loss to a profit": True, "swung from loss to profit": True,
    "from profit to loss": True, "from loss to profit": True,
    "flipped negative": True, "flipped positive": True,
    "flipped to a loss": True, "flipped to a profit": True,
    "fell below zero": True, "rose above zero": True,
    "reversed sign": True, "changed sign": True, "sign reversal": True,
    # -- asserts no crossing -----------------------------------------------------------------
    "did not cross zero": False, "did not cross": False, "does not cross zero": False,
    "without crossing zero": False, "on the same side of zero": False,
    "stayed positive": False, "stayed negative": False,
    "remained positive": False, "remained negative": False,
    "did not turn negative": False, "did not turn positive": False,
    "held its sign": False, "kept its sign": False,
}
#: §13.10 condition 5. A negation inside the same clause, before the marker, inverts it. 37
#: passages in the corpus carry one — *"not as a result of any general solicitation"* — and each
#: satisfies conditions 1–4 while the filing asserts the opposite.
NEGATION_TOKENS: tuple[str, ...] = ("not", "no", "never", "rather than", "other than")


# -- locating one of them --------------------------------------------------------------------


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


_COMPARATIVE = _compile(COMPARATIVE_TERMS)
_NEGATION = _compile(NEGATION_TOKENS)
_CHANGE = _compile(tuple(CHANGE_DIRECTION))
_CROSSING = _compile(tuple(CROSSING_TERMS))

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


# -- which way a term points -----------------------------------------------------------------



def comparatives(text: str) -> tuple[LexicalMatch, ...]:
    return _scan(_COMPARATIVE, text)


def comparative_direction(term: str) -> bool | None:
    """`True` for a term that puts its subject above its object, `False` below, `None` unknown.

    `None` is a real answer and a caller must refuse on it rather than assume a direction: a
    comparative whose polarity the lexicon does not state is a comparison nothing can check.
    """
    return COMPARATIVE_DIRECTION.get(re.sub(r"\s+", " ", term.strip().lower()))


def change_verbs(text: str) -> tuple[LexicalMatch, ...]:
    """Every change verb in `text` (§6's orientation rule over a derived fact)."""
    return _scan(_CHANGE, text)


def change_direction(term: str) -> bool | None:
    """`True` for a word saying the quantity rose, `False` fell, `None` for one saying neither.

    `None` is *"this word states no direction"*, and after H1 a caller may not treat it as
    *"nothing to check"*: `derived_facts._direction_findings` requires a word whose answer is
    the computed direction, so a `None` word neither contradicts the fact nor satisfies it. A
    term this map does not carry at all answers `None` too, and the same rule catches it — which
    is the whole of H1's second finding, where six unlisted synonyms each made the check abstain.
    """
    return CHANGE_DIRECTION.get(re.sub(r"\s+", " ", term.strip().lower()))


def crossing_claims(text: str) -> tuple[LexicalMatch, ...]:
    """Every sign-crossing phrase in `text` (§6's rule over a `crossed_zero` derivation)."""
    return _scan(_CROSSING, text)


def crossing_direction(term: str) -> bool | None:
    """`True` for a phrase asserting the quantity crossed zero, `False` for one asserting it did
    not, `None` for a phrase this lexicon does not carry.

    `None` is `comparative_direction`'s contract and not `change_direction`'s: every member of
    `CROSSING_TERMS` states a polarity, so `None` here can only mean the caller passed a phrase
    the scan did not produce.
    """
    return CROSSING_TERMS.get(re.sub(r"\s+", " ", term.strip().lower()))


def negated(text: str, marker: LexicalMatch) -> bool:
    """§13.10 condition 5: a negation token precedes the marker within the same clause.

    Scoped to the clause the marker sits in rather than to the whole span, because a passage
    runs to thousands of characters and a `"not"` four sentences earlier negates nothing.
    """
    start = 0
    for boundary in _CLAUSE.finditer(text, 0, marker.start):
        start = boundary.end()
    return _NEGATION.search(text, start, marker.start) is not None


__all__ = [
    "CHANGE_DIRECTION",
    "COMPARATIVE_DIRECTION",
    "COMPARATIVE_TERMS",
    "CROSSING_TERMS",
    "NEGATION_TOKENS",
    "LexicalMatch",
    "change_direction",
    "change_verbs",
    "comparative_direction",
    "comparatives",
    "crossing_claims",
    "crossing_direction",
    "negated",
    "occurrences",
]
