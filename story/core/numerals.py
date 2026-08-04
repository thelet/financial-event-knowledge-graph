"""Printed form ⇄ canonical value (§13.1, §13.2, §13.3, §13.7.1).

Responsibility: turn a numeral as a human wrote it into the number the graph holds, and back
the other way for a table quote. Nothing here decides what to do about a mismatch — §13.17's
gate owns REFUSE and WARN, and this module owns arithmetic, surfaces and spans. Pure
functions, no I/O, standard library only, so the verifier can be tested with no database and
the live reconstruction can be asserted from a test rather than trusted from a docstring.

**The rule this module exists to keep: never multiply by the fact's `scale`.** Corpus values
are already canonical. `adjusted_ebitda 2020Q4 = -27075000.0` was printed `"(27,075)"` in a
thousands-scaled table, and `scale` records *how it was printed*, not a multiplier to apply to
the stored value. Two directions follow from that and they are not symmetric:

* `tokenize_numerals` reads prose. It reconstructs from sign, separators and the *magnitude
  word the writer typed* only. A narrative observation carries `scale: millions` next to the
  quote *"Adjusted EBITDA was $218 million in 2Q22"* — the surface already says "million", and
  applying `scale` as well yields 2.18e14 *(verified 2026-08-04 against the loaded graph:
  4 narrative USD observations carry `scale: millions` and a full-sentence quote)*.
* `reconstruct_table_quote` reads a table cell. Here the magnitude is *not* in the surface —
  the quote is a bare numeral of median 4 characters — so `scale` is the only place the
  magnitude lives, and §13.7.1's rule applies it. Verified on **all 2,690 table observations**
  by `tests/story/test_story_numerals.py`.

Three things measured here that §13.1–§13.3 state differently, recorded rather than smoothed
over *(all verified 2026-08-04 against `bolt://localhost:7687`, 2,704 observations)*:

1. **§13.1's tolerance formula and §13.1's own worked case disagree about sign.** The text
   says `|V_draft − V_fact| ≤ 0.5 × 10^(e−d+1)`; the worked case says `"$27.1 million"`
   against `−27,075,000` gives `|Δ| = 25,000`, which is `||V_draft| − |V_fact||`. Signed
   subtraction gives 54,175,000 and the case fails. The worked case is right about what a
   verifier must do — *"Adjusted EBITDA was a loss of $27.1 million"* carries its sign in the
   words, not in the numeral — so `compare_to_fact` tests magnitudes and reports sign
   agreement as a **separate** signal, which the caller can require when the numeral itself
   was signed. See `ToleranceVerdict`.
2. **The "significant figures in the fact's printed form" that over-precision compares
   against is the `EVIDENCED_BY.quoted_text` edge property, not `printed_form`.** No
   `:Observation` in this corpus carries a `printed_form` property at all (C2: Neo4j stores no
   nulls, and `PackagedFact.printed_form` is populated from nothing today). Worse, a fact can
   have *two* printed forms: `adjusted_ebitda 2020Q4` is quoted both `"(27,075)"` in a
   thousands table and `"(27)"` in a millions one, so `"$27.1 million"` is over-precise
   against the second printing and not against the first. `compare_to_fact` therefore takes
   the printed form as an argument and never guesses which one applies.
3. **§13.2's surface map lists five units; the corpus has four.** `contracts` is not one —
   `homes_under_contract` and `acquisition_contracts` carry `unit: "homes"`. And two of the
   four (`homes`, `markets`) are never a numeral *suffix*; they are the noun after it. So
   `SurfaceUnit` covers the noun as well as the suffix, and a bare numeral is `NONE` rather
   than being guessed at.

`percentage_points`, `basis_points` and `multiple` are surfaces a draft can write and no
observation can hold: §13.3's second gate says no observation in the package is a change
(the extraction refused 174 `DERIVED_CHANGE_COLUMN` + 12 `DERIVED_COMPARISON`). They exist
here so a change sentence can *declare* which quantity it means, which is the whole of §13.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Mapping

# ---------------------------------------------------------------------------------------
# Closed vocabularies. Every one is a module constant rather than a literal inside a regex,
# because §13.3's lexicons are the part of this module a later measurement is most likely to
# amend, and an amendment should be a diff to a named list with a reason beside it.
# ---------------------------------------------------------------------------------------

#: What a magnitude word multiplies by, as `Decimal` so `6.6 billion` is 6600000000 exactly
#: rather than the 6600000000.000001 a float product would give.
MAGNITUDE_MULTIPLIERS: Mapping[str, Decimal] = {
    "thousand": Decimal(1000), "thousands": Decimal(1000), "k": Decimal(1000),
    "million": Decimal(1000000), "millions": Decimal(1000000), "m": Decimal(1000000),
    "billion": Decimal(1000000000), "billions": Decimal(1000000000),
    "bn": Decimal(1000000000),
}

#: §13.7.1's table rule. A table observation whose `scale` property is *absent* is read as
#: `units` — measured, that is 84 `market_count` rows and 3 narrative `homes` rows, and C2
#: says absent is the only reading Neo4j can produce.
SCALE_MULTIPLIERS: Mapping[str, Decimal] = {
    "units": Decimal(1), "thousands": Decimal(1000), "millions": Decimal(1000000),
}

#: §13.1's hedge guard. These relax nothing; they are recorded so §13.16 can ask whether the
#: hedge is doing work the number cannot support.
HEDGES: tuple[str, ...] = (
    "approximately", "about", "roughly", "around", "nearly", "over", "more than", "under",
    "~",
)

#: §13.3's surface gate. A percent numeral beside one of these is a change claim, and a change
#: claim that does not say *which* change is unresolvable rather than imprecise.
CHANGE_VERBS: tuple[str, ...] = (
    "rose", "fell", "declined", "increased", "decreased", "dropped", "improved", "widened",
    "narrowed", "up", "down", "higher", "lower",
)

PERCENTAGE_POINT_MARKERS: tuple[str, ...] = ("percentage point", "percentage points", "pp")

BASIS_POINT_MARKERS: tuple[str, ...] = ("basis point", "basis points", "bps")

#: What makes a `%` change sentence explicitly *relative* rather than ambiguous. Deliberately
#: short and deliberately not inferred: the natural surface for a relative change ("rose 136%")
#: is exactly the ambiguous one §13.3 refuses, so this list holds only phrases that state the
#: reading outright. Extend it with a measurement, not with an intuition.
RELATIVE_MARKERS: tuple[str, ...] = (
    "relative to", "relative", "in relative terms", "on a relative basis",
    "in percentage terms", "percentage change", "percent change", "proportionally",
    "proportionately",
)


class SurfaceUnit(str, Enum):
    """The unit a numeral's own surface implies (§13.2).

    `USD`, `PERCENT`, `HOMES` and `MARKETS` are the corpus's four `Observation.unit` values
    spelled identically, so §13.2's comparison is a string equality and not a translation
    table. The rest are surfaces a draft can write that no observation holds.
    """

    USD = "USD"
    PERCENT = "percent"
    HOMES = "homes"
    MARKETS = "markets"
    PERCENTAGE_POINTS = "percentage_points"
    BASIS_POINTS = "basis_points"
    MULTIPLE = "multiple"
    #: A bare numeral. §13.2's map is closed and total over *surfaces*, not over numerals:
    #: `"(27,075)"` implies no unit at all and the binding has to supply one.
    NONE = "none"


class PercentOperation(str, Enum):
    """The three readings of a change in a percent metric (§13.3).

    The values are the strings a draft puts in `Calculation.operation`; §13.3 requires the
    draft to *declare* the reading and the verifier to recompute it, never to infer it.
    """

    DELTA_PP = "delta_pp"
    DELTA_BPS = "delta_bps"
    DELTA_RELATIVE = "delta_relative"


class UnreconstructableQuote(ValueError):
    """A printed form this module refuses to guess at, named rather than coerced."""


class RelativeChangeAcrossZero(ValueError):
    """§13.3's third gate. `(−6.3 − 5.2)/|5.2|` is defined and rhetorically meaningless."""


# ---------------------------------------------------------------------------------------
# Direction 1 — a numeral out of prose
# ---------------------------------------------------------------------------------------

#: §13.1's tokeniser. Ordered longest-alternative-first throughout, for the same reason
#: §13.5's alias index is: `m` inside `million` and `pp` inside `percentage points` both make
#: a shortest-match scanner silently right about the digits and wrong about the size.
#:
#: The opening parenthesis is only read as a sign when it opens *immediately* on the numeral
#: (`$` and whitespace may intervene, words may not), and the conditional group at the end
#: makes it balance — otherwise every parenthetical aside in the draft would negate the first
#: number inside it.
#:
#: The sign and the currency symbol share one `prefix` group rather than being two ordered
#: ones, because both orders are written — `-$27.1 million` and `$-27.1 million` — and an
#: ordered pair would have to pick one. The group cannot begin with a space, so a numeral with
#: no `$` and no parenthesis starts its span at the first digit: §13.1's span is what a
#: finding highlights, and a leading space in it highlights the word before the number.
_NUMERAL = re.compile(
    r"""
    (?:(?P<open>\()[ ]*)?
    (?P<prefix>(?:[-−$][ ]*)+)?
    (?P<integer>\d{1,3}(?:,\d{3})+|\d+)
    (?:\.(?P<fraction>\d+))?
    (?:[ ]*(?P<magnitude>thousands?|millions?|billions?|bn|k|m)\b)?
    (?:
          [ ]*(?P<points>percentage[ ]+points?|pp)\b
        | [ ]*(?P<bps>basis[ ]+points?|bps)\b
        | (?P<percent>%)
        | (?P<multiple>x)(?![\w])
    )?
    (?(open)[ ]*\))
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: The noun that carries the unit when the suffix does not (§13.2). Applied only to a numeral
#: that took no suffix, so `"5.2% of homes"` stays `PERCENT`.
_UNIT_NOUN = re.compile(r"[ ]+(?P<noun>homes?|markets?)\b", re.IGNORECASE)

#: A hedge counts only when it runs straight into the numeral — `\s*$` against the text before
#: the match. `"over"` and `"under"` are common words here (`"under contract"`), and a hedge
#: found three clauses away would be a hedge attached to a different claim.
_HEDGE = re.compile(
    r"(?:\b(?:approximately|about|roughly|around|nearly|over|more\s+than|under)\b|~)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumeralToken:
    """One numeral as written, and the value it reconstructs to.

    `value` applies the magnitude word and nothing else — see the module docstring. `text`,
    `start` and `end` are the exact span §13.1 requires so a finding can point at the
    characters it refuses.
    """

    text: str
    start: int
    end: int
    value: float
    unit: SurfaceUnit
    #: `d` in §13.1's tolerance formula, counted as written: leading zeros never count,
    #: trailing zeros after a decimal point always do.
    significant_figures: int
    #: Trailing zeros of an integer written with no decimal point, which convention says are
    #: not significant. `"1,200"` is `significant_figures=2, trailing_zeros_excluded=2`; a
    #: caller that wants the tight reading adds them. Recorded rather than decided because the
    #: two readings give windows a factor of 100 apart and only the writer knows which is
    #: meant.
    trailing_zeros_excluded: int
    #: Digits after the decimal point, as written. The only safe basis for comparing a
    #: recomputed float to a printed one — `adjusted_gross_margin` 7.3 → 9.9 subtracts to
    #: `2.6000000000000005`.
    decimals_written: int
    parenthesised: bool
    #: True when the *numeral itself* carried a sign — a parenthesis or a minus. A draft that
    #: writes "a loss of $27.1 million" carries the sign in words this module does not read,
    #: which is why `compare_to_fact` treats an unsigned numeral as a claim about magnitude.
    negative: bool
    magnitude: str | None
    currency_symbol: bool
    hedge: str | None


def tokenize_numerals(text: str) -> tuple[NumeralToken, ...]:
    """Every numeral in `text`, left to right, with exact spans (§13.1).

    Years, ordinals and threshold values tokenise like anything else. §13.1 requires *every*
    numeral to be covered by a binding, a calculation, a period surface or the `literal_ok`
    allowlist, so a tokeniser that quietly skipped `"2022"` would be deciding a question the
    gate exists to ask.
    """
    return tuple(_token(match, text) for match in _NUMERAL.finditer(text))


def _token(match: re.Match[str], text: str) -> NumeralToken:
    integer = match.group("integer").replace(",", "")
    fraction = match.group("fraction") or ""
    magnitude = (match.group("magnitude") or "").lower() or None
    mantissa = Decimal(f"{integer}.{fraction}") if fraction else Decimal(integer)
    if magnitude:
        mantissa *= MAGNITUDE_MULTIPLIERS[magnitude]
    prefix = match.group("prefix") or ""
    negative = bool(match.group("open")) or "-" in prefix or "−" in prefix
    figures, dropped = _figures(integer, fraction)
    return NumeralToken(
        text=match.group(0),
        start=match.start(),
        end=match.end(),
        value=float(-mantissa if negative else mantissa),
        unit=_surface_unit(match, text),
        significant_figures=figures,
        trailing_zeros_excluded=dropped,
        decimals_written=len(fraction),
        parenthesised=bool(match.group("open")),
        negative=negative,
        magnitude=magnitude,
        currency_symbol="$" in prefix,
        hedge=_hedge(text, match.start()),
    )


def _surface_unit(match: re.Match[str], text: str) -> SurfaceUnit:
    if match.group("percent"):
        return SurfaceUnit.PERCENT
    if match.group("points"):
        return SurfaceUnit.PERCENTAGE_POINTS
    if match.group("bps"):
        return SurfaceUnit.BASIS_POINTS
    if match.group("multiple"):
        return SurfaceUnit.MULTIPLE
    if "$" in (match.group("prefix") or ""):
        return SurfaceUnit.USD
    noun = _UNIT_NOUN.match(text, match.end())
    if noun:
        return SurfaceUnit.HOMES if noun.group("noun").lower().startswith("home") \
            else SurfaceUnit.MARKETS
    return SurfaceUnit.NONE


def _hedge(text: str, start: int) -> str | None:
    found = _HEDGE.search(text, 0, start)
    if found is None:
        return None
    return re.sub(r"\s+", " ", found.group(0).strip()).lower()


def _figures(integer: str, fraction: str) -> tuple[int, int]:
    """Significant figures as written, and the trailing integer zeros convention excludes.

    `"27.0750"` is 6 and 0; `"0.052"` is 2 and 0; `"1200"` is 2 and 2; `"0"` is 1 and 0.
    """
    digits = (integer + fraction).lstrip("0")
    if not digits:
        return 1, 0
    if fraction:
        return len(digits), 0
    trimmed = digits.rstrip("0")
    return len(trimmed), len(digits) - len(trimmed)


def significant_figures(printed: str) -> int:
    """`d` for a printed numeral, read off its first numeral (§13.1).

    Raises rather than returning a default: over-precision compares two counts, and a silent
    zero would make every comparison pass.
    """
    match = _NUMERAL.search(printed)
    if match is None:
        raise UnreconstructableQuote(f"no numeral in {printed!r}")
    return _figures(match.group("integer").replace(",", ""), match.group("fraction") or "")[0]


# ---------------------------------------------------------------------------------------
# Direction 2 — a table quote back to its canonical value (§13.7.1 Rule A step 2)
# ---------------------------------------------------------------------------------------

#: What a table quote is allowed to be. Measured over all 2,690 table observations
#: (2026-08-04): the only characters that occur are digits, `.`, `,`, `(` and `)` — no `$`,
#: no `%`, no sign. The leading `-` is admitted anyway because it costs nothing and an XBRL
#: lane would bring one; nothing else is, because guessing at an unmeasured shape is how a
#: reconstruction rule stops being a proof.
_TABLE_QUOTE = re.compile(
    r"^(?P<open>\()?(?P<minus>-)?(?P<integer>\d{1,3}(?:,\d{3})*|\d+)"
    r"(?:\.(?P<fraction>\d+))?(?(open)\))$"
)


def reconstruct_table_quote(quoted_text: str, *, scale: str | None, unit: str) -> float:
    """§13.7.1's rule: strip `()` → negate, strip `,`, multiply by the scale unless percent.

    `quoted_text` comes off the **`EVIDENCED_BY` edge** (C3), not off the observation. `scale`
    absent means `units` (C2). This holds on all 2,690 table observations and is asserted
    against the live graph rather than claimed here.

    **Table facts only.** The 14 narrative observations quote whole sentences and carry the
    magnitude in the sentence — `reconstruct_table_quote` refuses them, which is correct:
    applying `scale: millions` to *"$218 million"* would give 2.18e14.

    The percent exception is real and unexercised: every one of the 1,163 percent table rows
    carries `scale: "units"`, so `× 1` and "skip the multiply" agree on every row in the
    corpus. It is written the way §13.7.1 states it so that a percent row arriving with a
    thousands scale is handled by the rule rather than by an accident.
    """
    match = _TABLE_QUOTE.match(quoted_text.strip())
    if match is None:
        raise UnreconstructableQuote(f"not a bare table numeral: {quoted_text!r}")
    fraction = match.group("fraction") or ""
    integer = match.group("integer").replace(",", "")
    value = Decimal(f"{integer}.{fraction}") if fraction else Decimal(integer)
    if unit != SurfaceUnit.PERCENT.value:
        key = scale or "units"
        if key not in SCALE_MULTIPLIERS:
            raise UnreconstructableQuote(f"no multiplier declared for scale {scale!r}")
        value *= SCALE_MULTIPLIERS[key]
    if match.group("open") or match.group("minus"):
        value = -value
    return float(value)


# ---------------------------------------------------------------------------------------
# Direction 3 — printed-precision half-ulp (§13.1)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToleranceVerdict:
    """What §13.1's tolerance test decided, with every input it decided from.

    `within_window` is the half-ulp test on **magnitudes**; `sign_agrees` is separate. See the
    module docstring: §13.1's prose says signed subtraction and §13.1's worked case computes
    an unsigned difference, and the worked case is the one a verifier can act on.
    """

    draft_value: float
    fact_value: float
    #: `||V_draft| − |V_fact||`.
    difference: float
    #: `0.5 × 10^(e − d + 1)`.
    window: float
    #: `e = floor(log10|V_fact|)`.
    exponent: int
    draft_significant_figures: int
    within_window: bool
    #: The draft numeral carried its own sign (a parenthesis or a minus).
    sign_explicit: bool
    sign_agrees: bool
    #: `d` exceeds the significant figures in the printed form it was compared against.
    over_precise: bool
    fact_significant_figures: int | None

    @property
    def accepted(self) -> bool:
        """Derived, never stored — a verdict cannot claim acceptance beside a sign conflict.

        An unsigned draft numeral is a claim about magnitude only; "a loss of $27.1 million"
        puts the sign in words §13.1's tokeniser does not read.
        """
        return self.within_window and (self.sign_agrees or not self.sign_explicit)


def tolerance_window(fact_value: float, draft_significant_figures: int) -> float:
    """`0.5 × 10^(e − d + 1)` — the fact rounds to the draft's numeral at the draft's precision.

    `e` is taken from the decimal representation rather than `math.log10`, which is exact for
    every value in the corpus and needs no reasoning about whether `log10(1000)` came back
    just under 3.
    """
    if draft_significant_figures < 1:
        raise ValueError(f"significant figures must be positive, got {draft_significant_figures}")
    exponent = _exponent(fact_value)
    return float(Decimal("0.5") * Decimal(10) ** (exponent - draft_significant_figures + 1))


def compare_to_fact(
    draft_value: float,
    fact_value: float,
    *,
    draft_significant_figures: int,
    draft_sign_explicit: bool = True,
    fact_printed_form: str | None = None,
) -> ToleranceVerdict:
    """Run §13.1's tolerance test and the over-precision signal beside it.

    `fact_printed_form` is the fact's `EVIDENCED_BY.quoted_text`, and it is an argument rather
    than something derived here because a fact can have more than one printing:
    `adjusted_ebitda 2020Q4` is quoted `"(27,075)"` in one table and `"(27)"` in another, and
    `"$27.1 million"` is over-precise against exactly one of them. Omitted, over-precision is
    not evaluated — reported as `over_precise=False, fact_significant_figures=None`, never as
    a pass.
    """
    window = tolerance_window(fact_value, draft_significant_figures)
    difference = abs(abs(draft_value) - abs(fact_value))
    fact_figures = significant_figures(fact_printed_form) if fact_printed_form else None
    return ToleranceVerdict(
        draft_value=draft_value,
        fact_value=fact_value,
        difference=difference,
        window=window,
        exponent=_exponent(fact_value),
        draft_significant_figures=draft_significant_figures,
        within_window=difference <= window,
        sign_explicit=draft_sign_explicit,
        sign_agrees=(draft_value < 0) == (fact_value < 0),
        over_precise=fact_figures is not None and draft_significant_figures > fact_figures,
        fact_significant_figures=fact_figures,
    )


def compare_token_to_fact(
    token: NumeralToken,
    fact_value: float,
    *,
    fact_printed_form: str | None = None,
) -> ToleranceVerdict:
    """`compare_to_fact` with `d` and sign explicitness taken from the numeral as written."""
    return compare_to_fact(
        token.value,
        fact_value,
        draft_significant_figures=token.significant_figures,
        draft_sign_explicit=token.negative,
        fact_printed_form=fact_printed_form,
    )


def matches_at_printed_precision(computed: float, printed: float, *, decimals: int) -> bool:
    """Compare a recomputed quantity to a printed one at the precision it was printed to.

    Never `==`. Measured on real quarters: `adjusted_gross_margin` 7.3 → 9.9 subtracts to
    `2.6000000000000005` and 9.9 → 13.2 to `3.299999999999999`, so equality refuses two of the
    metric's own consecutive moves.

    The instance §13.1 gives for this hazard is `5.2 - 2.2`, which is **exactly** `3.0` in
    IEEE-754 and would have demonstrated nothing. The hazard is real; the example is not.
    """
    return round(computed, decimals) == round(printed, decimals)


def _exponent(value: float) -> int:
    """`floor(log10|value|)`, exactly, via the decimal representation.

    Zero has no exponent. No observation in the corpus is zero *(verified 2026-08-04: 0 of
    2,704)*, so this is a contract for a value that cannot arrive rather than a behaviour to
    rely on: `e = 0` makes the window `0.5 × 10^(1−d)`, which accepts `"0"` against `0.0` and
    nothing wider than a one-figure draft would allow.
    """
    if value == 0:
        return 0
    return Decimal(repr(abs(value))).adjusted()


# ---------------------------------------------------------------------------------------
# Direction 4 — percentage semantics (§13.3)
# ---------------------------------------------------------------------------------------


def delta_pp(earlier: float, later: float) -> float:
    """`v2 − v1`. Defined everywhere, including across zero."""
    return later - earlier


def delta_bps(earlier: float, later: float) -> float:
    """`(v2 − v1) × 100`. Defined everywhere, including across zero."""
    return (later - earlier) * 100


def relative_change_across_zero(earlier: float, later: float) -> bool:
    """§13.3's third gate: `v1 == 0`, or the two values sit on opposite sides of zero.

    `adjusted_ebitda_margin` crosses zero six times across its 26 quarters, so this is the
    common case for the metric a percent story is most likely to be about, not an edge one.
    """
    if earlier == 0:
        return True
    return (earlier < 0) != (later < 0)


def delta_relative(earlier: float, later: float) -> float:
    """`(v2 − v1)/|v1| × 100`, refused across zero rather than returned meaninglessly."""
    if relative_change_across_zero(earlier, later):
        raise RelativeChangeAcrossZero(
            f"relative change is not a claim anyone can read: {earlier} -> {later}")
    return (later - earlier) / abs(earlier) * 100


def percent_delta(operation: PercentOperation, earlier: float, later: float) -> float:
    """The declared reading, recomputed. §13.3: the draft declares, the verifier recomputes."""
    if operation is PercentOperation.DELTA_PP:
        return delta_pp(earlier, later)
    if operation is PercentOperation.DELTA_BPS:
        return delta_bps(earlier, later)
    return delta_relative(earlier, later)


def surface_carries_percentage_points(rendered: str) -> bool:
    return _POINTS_SURFACE.search(rendered) is not None


def surface_carries_basis_points(rendered: str) -> bool:
    return _BPS_SURFACE.search(rendered) is not None


def surface_carries_relative_marker(rendered: str) -> bool:
    return _RELATIVE_SURFACE.search(rendered) is not None


#: The arithmetic operations whose result is a quantity in the unit its inputs carry. `ratio`
#: is not one of them — it divides the unit out — and the three §13.14 operations
#: (`extremum`, `absence`, `temporal_order`) have no scalar result at all.
UNIT_PRESERVING_OPERATIONS: tuple[str, ...] = (
    "compare_levels", "compare_deltas", "difference", "sum",
)


def operation_result_surfaces(operation: str, input_unit: str) -> frozenset[SurfaceUnit]:
    """What a calculation's `result_rendered` may be written in, given its inputs' unit.

    **R8's defect D, measured.** The demo's own `compare_levels` sentence renders a gap between
    two percent levels. Before this table nothing checked that rendering: `"15.9 basis points"`
    passed (the gap is 1,590 bps — false by 100×), `"15.9x"` passed (the ratio is −0.26), and
    `"15.9 percent"` passed, which is the exact percentage-point-versus-percent confusion
    §13.3 exists to stop. `surface_supports_operation` only ever governed `delta_pp`,
    `delta_bps` and `delta_relative`, so every other operation rendered whatever it liked.

    A gap between two percent *levels* is in percentage points and in nothing else here.
    `basis points` is the same quantity at a hundred times the number, so admitting it would
    require scaling the recomputation by the surface; refusing it costs a true sentence that
    can be rewritten in points, which is the direction §13.14 says the failure should point.
    An empty set means *this module states no requirement* — the caller must not read it as
    "anything goes" for an operation it thought was governed.
    """
    if operation == "ratio":
        # Dimensionless: `2.5x`, or a bare numeral. A unit on a ratio is a unit that survived
        # a division that should have cancelled it.
        return frozenset({SurfaceUnit.MULTIPLE, SurfaceUnit.NONE})
    if operation not in UNIT_PRESERVING_OPERATIONS:
        return frozenset()
    if input_unit == SurfaceUnit.PERCENT.value:
        return frozenset({SurfaceUnit.PERCENTAGE_POINTS})
    if input_unit == SurfaceUnit.USD.value:
        return frozenset({SurfaceUnit.USD})
    if input_unit == SurfaceUnit.HOMES.value:
        return frozenset({SurfaceUnit.HOMES, SurfaceUnit.NONE})
    if input_unit == SurfaceUnit.MARKETS.value:
        return frozenset({SurfaceUnit.MARKETS, SurfaceUnit.NONE})
    return frozenset()


def surface_supports_operation(
    operation: PercentOperation | str,
    rendered: str,
    *,
    input_unit: str | None = None,
) -> bool:
    """§13.3's rendering requirement for each of the three percent readings, and §13.2's for
    the five arithmetic operations that were never given one.

    `delta_relative` needs both halves — a `%`-suffixed numeral *and* an explicit relative
    marker — because the `%` alone is exactly what makes the sentence ambiguous.

    For an operation outside `PercentOperation` the requirement is the inputs' own unit, via
    `operation_result_surfaces`, and it needs `input_unit` to state one: called without it —
    or for an operation this module governs no rendering of — the answer is `True`, because
    *"no requirement stated"* must not be reported as *"requirement failed"*.
    """
    if isinstance(operation, PercentOperation) or operation in {op.value
                                                                for op in PercentOperation}:
        percent_operation = PercentOperation(operation)
        if percent_operation is PercentOperation.DELTA_PP:
            return surface_carries_percentage_points(rendered)
        if percent_operation is PercentOperation.DELTA_BPS:
            return surface_carries_basis_points(rendered)
        return (surface_carries_relative_marker(rendered)
                and any(token.unit is SurfaceUnit.PERCENT
                        for token in tokenize_numerals(rendered)))
    if input_unit is None:
        return True
    allowed = operation_result_surfaces(str(operation), input_unit)
    if not allowed:
        return True
    tokens = tokenize_numerals(rendered)
    if len(tokens) != 1:
        # Nothing to judge a surface on. `_recompute_findings` already refuses this as
        # `calculation_does_not_recompute`, and two codes for one fault help nobody.
        return True
    return tokens[0].unit in allowed


@dataclass(frozen=True)
class AmbiguousPercentChange:
    """A `%` numeral sharing a clause with a change verb, and no token saying which reading.

    Carries the clause and both spans so a finding can quote the sentence back rather than
    naming a rule. §13.3: this is unresolvable, not imprecise.

    **§13.3's "factor of nineteen" is measured from the wrong end.** The ratio between the two
    readings is exactly `100/|v1|`, and `v1` is the *earlier* value because that is what
    `delta_relative` divides by. On §13.3's own worked pair — `adjusted_ebitda_margin` 2Q21
    `2.2` → 2Q22 `5.2` — the readings are `3.0 pp` and `136.4%`, a factor of **45.5**;
    nineteen is `100/5.2`, computed from the later value. Right order of magnitude, wrong
    base, and the correct number is larger, so the plan understates the gap it is refusing.
    """

    clause: str
    clause_start: int
    clause_end: int
    verb: str
    verb_start: int
    verb_end: int
    numeral: NumeralToken


def find_ambiguous_percent_changes(text: str) -> tuple[AmbiguousPercentChange, ...]:
    """Every unresolvable percent-change reading in `text` (§13.3's surface gate).

    Clauses are split on `;`, `:`, dashes and sentence-ending stops only — never on a comma,
    and never on the `.` inside `5.2`. The splitter is deliberately coarse: it is feeding a
    REFUSE, and under-splitting joins a verb to a numeral that a finer reading would have
    separated, which errs towards refusing. *"Adjusted EBITDA margin improved, reaching 5.2%"*
    stays one clause for that reason.
    """
    found: list[AmbiguousPercentChange] = []
    for clause, offset in _clauses(text):
        if _POINTS_SURFACE.search(clause) or _BPS_SURFACE.search(clause):
            continue
        if _RELATIVE_SURFACE.search(clause):
            continue
        verb = _CHANGE_VERB.search(clause)
        if verb is None:
            continue
        for token in tokenize_numerals(clause):
            if token.unit is not SurfaceUnit.PERCENT:
                continue
            found.append(AmbiguousPercentChange(
                clause=clause,
                clause_start=offset,
                clause_end=offset + len(clause),
                verb=verb.group(0),
                verb_start=offset + verb.start(),
                verb_end=offset + verb.end(),
                numeral=_shift(token, offset),
            ))
    return tuple(found)


def _shift(token: NumeralToken, offset: int) -> NumeralToken:
    """Re-anchor a clause-relative span onto the text the clause was cut from."""
    if offset == 0:
        return token
    return replace(token, start=token.start + offset, end=token.end + offset)


def _clauses(text: str) -> tuple[tuple[str, int], ...]:
    pieces: list[tuple[str, int]] = []
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(text):
        pieces.append((text[start:boundary.start()], start))
        start = boundary.end()
    pieces.append((text[start:], start))
    return tuple(piece for piece in pieces if piece[0].strip())


def _alternation(markers: tuple[str, ...]) -> str:
    return "|".join(re.escape(marker).replace(r"\ ", r"\s+")
                    for marker in sorted(markers, key=len, reverse=True))


_POINTS_SURFACE = re.compile(rf"\b(?:{_alternation(PERCENTAGE_POINT_MARKERS)})\b", re.IGNORECASE)
_BPS_SURFACE = re.compile(rf"\b(?:{_alternation(BASIS_POINT_MARKERS)})\b", re.IGNORECASE)
_RELATIVE_SURFACE = re.compile(rf"\b(?:{_alternation(RELATIVE_MARKERS)})\b", re.IGNORECASE)
_CHANGE_VERB = re.compile(rf"\b(?:{_alternation(CHANGE_VERBS)})\b", re.IGNORECASE)

#: A `.` between two digits is a decimal point, not a stop — splitting `"5.2%"` in half would
#: turn the number this module exists to read into two numbers.
_CLAUSE_BOUNDARY = re.compile(r"\.(?!\d)|(?<!\d)\.|[;:—–]")


__all__ = [
    "BASIS_POINT_MARKERS",
    "CHANGE_VERBS",
    "HEDGES",
    "MAGNITUDE_MULTIPLIERS",
    "PERCENTAGE_POINT_MARKERS",
    "RELATIVE_MARKERS",
    "SCALE_MULTIPLIERS",
    "UNIT_PRESERVING_OPERATIONS",
    "AmbiguousPercentChange",
    "NumeralToken",
    "PercentOperation",
    "RelativeChangeAcrossZero",
    "SurfaceUnit",
    "ToleranceVerdict",
    "UnreconstructableQuote",
    "compare_to_fact",
    "compare_token_to_fact",
    "delta_bps",
    "delta_pp",
    "delta_relative",
    "find_ambiguous_percent_changes",
    "matches_at_printed_precision",
    "operation_result_surfaces",
    "percent_delta",
    "reconstruct_table_quote",
    "relative_change_across_zero",
    "significant_figures",
    "surface_carries_basis_points",
    "surface_carries_percentage_points",
    "surface_carries_relative_marker",
    "surface_supports_operation",
    "tokenize_numerals",
    "tolerance_window",
]
