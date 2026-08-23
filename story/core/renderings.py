"""The strings a sentence may legally carry about a trusted row — one emitter, in `core/`.

Responsibility: given a row code is willing to write about — a `PackagedFact` or a
`DerivedFact` — say how its value, its metric and its period may be spelled in prose. Nothing
here refuses, renders a prompt, or reads a draft: it answers *"what may be written"* and the
two callers decide what to do with the answer. The prompt printer offers the strings to a
model; the draft compiler inserts them itself.

**Why this is one module and not two private copies** *(§4.5 of
`docs/2026-08-23-deterministic-draft-compiler/01-TARGET-ARCHITECTURE.md`)*. Until this module
existed, the same three questions were answered inside `story/stages/generation/prompts.py` by
private functions, and the period one was answered a *second* time by
`story/stages/derivation/operations.period_surface_hint` — with two different answers for the
same window (`"fiscal year 2022"` against `"fiscal 2022"`, `"2022-09-30"` against
`"September 30, 2022"`). Both round-tripped through the verifier's grammar, so nothing was
broken; but a compiler that picked one of them would have been a third answer. They are
collapsed here, on the **worded** forms, because a sentence carries a worded date.

**What this module is not.** It is not a recogniser. `story/stages/verification/` keeps its own
independent readers — `period_grammar.resolve`, `MetricAliasIndex`, `numerals.tokenize_numerals`
— and the agreement between what is written here and what is admitted there is asserted by
`tests/story/test_story_renderings.py` rather than obtained by sharing an implementation. A
writer that imported the verifier would make the two ends one end.

**Imports.** `story.core.models`, `story.core.periods`, `story.core.numerals` and the standard
library. The third is one constant — `SCALE_MULTIPLIERS`, which `scaled_money` divides by — and
it is imported rather than restated because the alternative is a second copy of the scale table
inside the module whose whole purpose is to stop there being two copies of a rendering rule.
Nothing under `story/stages/` or `story/providers/` may be imported here, and
`tests/story/test_story_package_structure.py` enforces it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Mapping

from story.core.models import (
    DerivedFact,
    DisplaySemantics,
    PackagedFact,
    StoryEvidencePackage,
)
from story.core.numerals import SCALE_MULTIPLIERS
from story.core.periods import StoryPeriod

# ---------------------------------------------------------------------------------------
# Metric surfaces — §13.5's question, asked in the writing direction
# ---------------------------------------------------------------------------------------


def metric_surfaces(package: StoryEvidencePackage, metric_id: str) -> tuple[str, ...]:
    """Surfaces that name this metric and no other metric in this package (§13.5).

    A surface is dropped when it is a sub-phrase of some *other* package metric's surface —
    `"gross margin"` inside `"adjusted gross margin"` — because §13.5 resolves by longest match
    and the shorter one is exactly the ambiguity the section is about. Order is the metric's own
    (`label`, then `aliases`, then the id), deduplicated on the normalised form, so a rendering
    is stable across two builds of one package.

    **The metric id is a surface, and leaving it out was a defect measured on the demo's own
    package** *(2026-08-19)*. `MetricAliasIndex.from_package` indexes `(metric_id, label,
    *aliases)` and `normalise` maps `_` to a space, so `"GAAP Gross Margin"` resolves through the
    `gaap_gross_margin` **id** entry and §13.5 accepts it — which is exactly what the accepted
    draft in `tests/story/fixtures/story_demo` binds. This function offered only `label` and
    `aliases`, and `gaap_gross_margin`'s label is `"Gross Margin"`, dropped as a sub-phrase of
    `"Adjusted Gross Margin"`. So both the FACTS row and the DERIVED FACTS row for a metric the
    *plan* required the writer to state printed *"do not write about this fact"* while
    `WRITER_SYSTEM` rule 4 told it to write `"GAAP gross margin"` — a contradiction with no legal
    way out. The id is rendered with `_` as a space because that is the form a sentence can
    carry; the verifier normalises both to one string.

    The id goes **last** so a human label still wins where one is unambiguous, and it is filtered
    by the same sub-phrase rule as everything else — an id that names two metrics inside this
    package is no more writable than a label that does.

    An empty result is still a real answer: the row then offers no metric surface at all, which
    is the honest outcome for a package whose two metrics share every surface, id included. The
    prompt turns that into *"do not write about this fact"*; the compiler turns it into a slot
    the template may not name.

    The filter is a **conservative local approximation** of §13.5's alias index — it drops any
    surface that is a sub-phrase of another package metric's surface — and the verifier remains
    the authority. It cannot *add* a surface the index would refuse for a reason the package does
    not carry, which is why the approximation is safe in the direction that matters.
    """
    own = next((metric for metric in package.metrics if metric.metric_id == metric_id), None)
    if own is None:
        return ()
    others = {
        _normalised_phrase(surface)
        for metric in package.metrics if metric.metric_id != metric_id
        for surface in (metric.metric_id, metric.label, *metric.aliases)
    }
    kept: list[str] = []
    seen: set[str] = set()
    for surface in (own.label, *own.aliases, own.metric_id.replace("_", " ")):
        phrase = _normalised_phrase(surface)
        if not phrase or phrase in seen:
            continue
        if any(f" {phrase} " in f" {other} " for other in others if other != phrase):
            continue
        seen.add(phrase)
        kept.append(surface)
    return tuple(kept)


def _normalised_phrase(surface: str) -> str:
    return " ".join(surface.replace("_", " ").lower().split())


# ---------------------------------------------------------------------------------------
# Period surfaces — §13.4's closed grammar, written rather than read
# ---------------------------------------------------------------------------------------

#: The ordinal a quarter is written with, and the day its last month ends on. A table and not
#: arithmetic over month numbers, for `period_grammar`'s own reason: the failure mode of an index
#: is silent, and the first draft of this table had September ending on the 31st.
_QUARTERS: Mapping[int, tuple[str, int, int]] = {
    1: ("first", 3, 31), 4: ("second", 6, 30), 7: ("third", 9, 30), 10: ("fourth", 12, 31)}

_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")


def period_surface(
    *,
    period_start: str | None = None,
    period_end: str | None = None,
    instant_date: str | None = None,
) -> str | None:
    """The one surface §13.4's grammar accepts for these endpoints, or `None`.

    Derived from the three date fields and never from a `period_key` or a `PeriodShape`: a key
    is a label and the grammar resolves to *endpoints*, so a surface built from the key would be
    an assertion that the two agree. `None` for a window the closed grammar has no form for —
    the row then offers no period surface, which is §13.4's refusal reached before the sentence
    is written rather than after.

    **Every form here is a worded one, and that is the collapse this module exists for.** The
    derivation stage used to mint `"2022-09-30"` for an instant and `"fiscal year 2022"` for a
    year; both resolve, and neither is what a sentence carries. Choosing the worded forms costs
    a month-name table that `period_grammar._MONTHS` already has a copy of — a duplication the
    old `period_surface_hint` docstring refused for exactly that reason — and buys one emitter
    where there were two. The duplication is checked:
    `test_story_renderings.py` round-trips every form below through the grammar.
    """
    if instant_date:
        moment = _as_date(instant_date)
        if moment is None:
            return None
        return f"{_MONTH_NAMES[moment.month - 1]} {moment.day}, {moment.year}"
    start, end = _as_date(period_start), _as_date(period_end)
    if start is None or end is None or start.year != end.year:
        return None
    if start.day == 1 and start.month in _QUARTERS:
        ordinal, last_month, last_day = _QUARTERS[start.month]
        if (end.month, end.day) == (last_month, last_day):
            return f"the {ordinal} quarter of {start.year}"
    if (start.month, start.day) == (1, 1):
        if (end.month, end.day) == (12, 31):
            return f"fiscal {start.year}"
        if (end.month, end.day) == (9, 30):
            return f"the nine months ended September 30, {start.year}"
        if (end.month, end.day) == (6, 30):
            return f"the first half of {start.year}"
    return None


def period_surface_of_fact(fact: PackagedFact) -> str | None:
    """`period_surface` over an observation's own endpoints."""
    return period_surface(period_start=fact.period_start, period_end=fact.period_end,
                          instant_date=fact.instant_date)


def period_surface_of_period(period: StoryPeriod) -> str:
    """`period_surface` over a `StoryPeriod`, with `""` where there is none.

    **This is the repair §2 measured, not a convenience.** The demo's refusal was never the
    arithmetic: `$446 million` recomputed cleanly and `unbound_numeral` fired on the literal
    `2022`, because the model left `Calculation.period_surface` empty while its own text read
    *"in the third quarter of 2022"*. A derived fact binds through an ordinary `FactBinding`,
    whose `period_surface` is per binding, and `DerivedFact.period_surface_hint` — filled from
    this call — is where code fills it.

    The empty string rather than `None` because that field is a `str` whose `""` already means
    *"no surface names this window"*. `PeriodShape.OTHER` is refused by R3 before a derived fact
    can exist, so `""` is a state this returns for a period that cannot arrive rather than an
    invented phrase for a window nobody can name.

    Answered off the three date fields and not off `period.shape`, which is the one behavioural
    difference from the emitter this replaced. The two agree on every shape the classifier can
    produce — `classify_shape` derives the shape from the same endpoints — and a quarter's
    ordinal now comes from its *start* month rather than its end, which is the same number for
    any window `PeriodShape.QUARTER` admits.
    """
    return period_surface(period_start=period.period_start, period_end=period.period_end,
                          instant_date=period.instant_date) or ""


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------
# Figures — §13.1's numeral and §13.2's unit surface, in the writing direction
# ---------------------------------------------------------------------------------------

#: The four `DisplaySemantics` whose word already states which way the comparison or the change
#: runs. For these a derived figure renders the **magnitude**: *"decreased by -446000000.0"* and
#: *"-15.9 percentage points lower than"* are double negatives in prose, and a written minus that
#: argues with `result`'s sign is `sign_disagreement` at §13.1. `times`, `equal to` and
#: `unchanged` are not here, and that is the point of listing rather than testing for a sign: a
#: `ratio` over a negative reading is genuinely negative and its word says nothing about that.
DIRECTIONAL_SEMANTICS: frozenset[DisplaySemantics] = frozenset({
    DisplaySemantics.INCREASED_BY,
    DisplaySemantics.DECREASED_BY,
    DisplaySemantics.HIGHER_THAN,
    DisplaySemantics.LOWER_THAN,
})

#: The two derived units whose value is a **word** and not a quantity: `crossed_zero` answers a
#: boolean and `trend_direction` a direction. No numeral may render one — a boolean written as
#: `1.0` is a number §13.1 would then compare against the prose — so `legal_renderings` offers
#: nothing at all for a row carrying one.
#:
#: Restated rather than imported, like everything else that crosses this boundary: the two
#: spellings already exist in `derivation/operations.py` (`UNIT_BOOLEAN`, `UNIT_DIRECTION`) and
#: in `verification/derived_facts.py` (`NON_NUMERIC_UNITS`), and `core/` may import neither.
#: `test_story_renderings.py` asserts this copy equals the verifier's.
#:
#: **Keyed on the unit and not only on `result is None`, and the difference is reachable.**
#: `DerivedFact`'s validator pairs `result` with `result_word`, but nothing in it stops a
#: `boolean` row carrying a scalar — and §13.2 refuses a numeral on that row under
#: `derived_unit_mismatch` whichever field it came from. The emitter answers the same way the
#: verifier asks.
NON_NUMERIC_DERIVED_UNITS: frozenset[str] = frozenset({"boolean", "direction"})

#: How each derived unit is **written**, as against how it is spelled in `DERIVED_UNITS`.
#:
#: **Every entry produces a surface `tokenize_numerals` reads the unit off, and that is the whole
#: requirement rather than a preference** *(measured 2026-08-19)*. §13.2 judges a derived
#: numeral against `verification.derived_facts.DERIVED_SURFACES`, which admits `USD` only for a
#: currency-symbol surface and `percentage_points` only for the two-word one — a `SurfaceUnit` of
#: `none` is `derived_unit_mismatch` there, unlike on an observation where it is tolerated. The
#: first wording printed `{result} {unit}`, so the live run copied `"446000000.0 USD"` into its
#: draft and earned exactly that finding, and the next one copied `"15.9 percentage_points"`
#: into `rendered` while writing *"15.9 percentage points"* in its text.
#:
#: A checked agreement and not an import: `story/core/` may not import a stage, and
#: `test_story_renderings.py` drives every rendering this module emits through
#: `tokenize_numerals` and asserts the surface lands in the set that module requires.
DERIVED_FIGURE_FORMATS: Mapping[str, str] = {
    "USD": "${value}",
    "percent": "{value}%",
    "percentage_points": "{value} percentage points",
    "multiple": "{value}x",
    "homes": "{value} homes",
    "markets": "{value} markets",
}

#: The scale words a filing writes, for the two `SCALE_MULTIPLIERS` entries that are not 1.
#: Singular, because that is how a sentence carries one: *"$446 million"*, never *"millions"*.
#: `numerals.MAGNITUDE_MULTIPLIERS` reads both spellings back, so the choice is a rendering one.
SCALE_WORDS: Mapping[str, str] = {"thousands": "thousand", "millions": "million"}

#: How many fractional digits the scaled form may carry before the plain one is offered instead.
#: A judgment and not a measurement: `$446.0000005 million` and `$446000000.5` state the same
#: value, the scaled form exists only so a person can read the figure at a glance, and it stops
#: earning its place the moment it is longer than what it replaced. One digit keeps `$446.5
#: million` and refuses everything below it.
SCALED_MONEY_DECIMALS = 1


def observed_figure(fact: PackagedFact) -> str | None:
    """A money reading spelled the way a sentence carries it, or `None` for every other row.

    **H3, and the whole of it.** The FACTS row prints the value as the package stores it —
    `556000000.0 USD` — and the DERIVED FACTS row beside it prints `$446 million`, because
    `derived_figure` renders a derived money figure at the scale its inputs were filed at.
    Those are two shapes for one kind of thing, and a 9B model reconciled them the wrong way:
    it scaled `556000000.0` itself to write *"Adjusted Gross Profit of 556 million USD"*, and
    then copied **that** spelling onto the derived row, which had told it in so many words to
    write `$446 million`. `446 million USD` carries no currency surface, so §13.2 refused it
    (`derived_unit_mismatch`) — legal against an observation and illegal against a derivation —
    and the run went from accepted to rejected on that one span *(measured live against
    Qwen3.5-9B-Q4_K_M, 2026-08-19; see plan §14.7 for the control run)*.

    So the scaling the model was doing by hand is done here, once, by the function the derived
    row already used. The two sections print one money value in one shape.

    **`None` for anything that is not money at a filed scale, and the restraint is the point.**
    A `percent` reading is already writable as it stands — the committed accepted draft writes
    *"-12.6 percent"* — and `DERIVED_FIGURE_FORMATS` would spell it `-12.6%`, which is a
    narrowing of legal prose bought for nothing. Only the shape that forced the model to compute
    is replaced; every other row renders exactly what it rendered before, which is why the demo
    candidate's writer prompt is byte-identical across the change that introduced this.
    """
    if fact.unit != "USD":
        return None
    return scaled_money(fact.value, fact.scale or "")


def derived_figure(fact: DerivedFact, package: StoryEvidencePackage) -> str:
    """The figure as a sentence must carry it: a number in a surface §13.2 can read.

    **A monetary figure is offered at the scale its inputs were filed at**, so a `$446,000,000`
    delta between two rows printed in millions is offered as `"$446 million"`. That is not
    tidying and it does not choose a precision: `scaled_money` divides in `Decimal` and keeps
    the result only when it multiplies back **exactly**, so the number written denotes the same
    value to the last digit and §13.1's window is computed from what the draft prints, as it
    always is. The reason it is worth doing is measured: `"$446000000.0"` is a legal surface no
    9B model would write, and the one it wrote instead — `"446000000.0 USD"`, the shape of the
    FACTS rows above it — carries no unit surface at all and is `derived_unit_mismatch`.

    Everything else is printed as Python renders the float. The fallback for a unit with no row
    in `DERIVED_FIGURE_FORMATS` is the machine spelling with its underscores opened up —
    writable, and refused by §13.2 rather than silently wrong.

    Callers with a word-valued row must not reach here: `crossed_zero` and `trend_direction`
    carry `result=None`, and `legal_renderings` returns `()` for them rather than a numeral
    §13.1 would then compare against the prose.
    """
    magnitude = _derived_magnitude(fact)
    if fact.unit == "USD":
        scaled = _derived_money(magnitude, fact, package)
        if scaled is not None:
            return scaled
    template = DERIVED_FIGURE_FORMATS.get(
        fact.unit, "{value} " + fact.unit.replace("_", " "))
    return template.format(value=magnitude)


def _derived_magnitude(fact: DerivedFact) -> float:
    """The number a derived figure renders: the magnitude where the word carries the direction.

    Split out because `legal_renderings` has to ask the same question to know whether the value
    is spellable as one numeral at all, and two copies of *"which rows drop the sign"* is exactly
    the duplication this module exists to end.
    """
    return abs(fact.result) if fact.display_semantics in DIRECTIONAL_SEMANTICS else fact.result


def _derived_money(magnitude: float, fact: DerivedFact,
                   package: StoryEvidencePackage) -> str | None:
    """`scaled_money` at the scale **both** of a derivation's inputs were filed at.

    `None` — meaning *"write the plain form"* — when the inputs are not both in this package or
    disagree about scale. A derived figure has no scale of its own; it inherits one only where
    the two readings agree, and where they do not the long form is what gets written.
    """
    inputs = [row for row in package.facts
              if row.observation_id in (fact.from_fact_id, fact.to_fact_id)]
    scales = {row.scale for row in inputs}
    if len(inputs) != 2 or len(scales) != 1:
        return None
    return scaled_money(magnitude, next(iter(scales)) or "")


def scaled_money(magnitude: float, scale: str) -> str | None:
    """`"$446 million"` when the reading was filed in millions and the division is exact.

    `None` — meaning *"write the plain form"* — whenever anything is uncertain: the scale is
    `units` or unknown, or the quotient does not multiply back to the same value. An emitter
    that guessed here would be choosing the digits §13.1's tolerance window is computed from.

    **One function for a FACTS row and a DERIVED FACTS row, which is H3's whole repair.** The
    two sections printed a money value in two shapes — an observation as `556000000.0 USD` and a
    derivation as `$446 million` — so a writer that wanted a sentence had to scale the first one
    itself, and the spelling it invented doing that (`"556 million USD"`) is the spelling it then
    copied onto the derived row, where §13.2 refuses it. See `observed_figure`.
    """
    word = SCALE_WORDS.get(scale)
    if word is None:
        return None
    # The sign goes **outside** the symbol. Both `-$110 million` and `$-110 million` tokenise to
    # the same value and unit, and the first is the one a filing writes; a reading that is
    # negative in the corpus — a loss — has to be spellable, so this is not a hypothetical.
    sign = "-" if magnitude < 0 else ""
    exact = Decimal(str(abs(magnitude)))
    try:
        quotient = exact / SCALE_MULTIPLIERS[scale]
        if quotient * SCALE_MULTIPLIERS[scale] != exact:
            return None
        # `quantize` raises rather than silently rounding when the result would not fit the
        # context, which is the behaviour wanted: a figure that cannot be shortened safely is
        # written long.
        if quotient != quotient.quantize(Decimal(1).scaleb(-SCALED_MONEY_DECIMALS)):
            return None
    except InvalidOperation:
        return None
    return f"{sign}${quotient.normalize():f} {word}"


# ---------------------------------------------------------------------------------------
# What a sentence may carry for a row
# ---------------------------------------------------------------------------------------


def legal_renderings(
    row: PackagedFact | DerivedFact, package: StoryEvidencePackage
) -> tuple[str, ...]:
    """Every string this emitter is willing to write for `row`'s value, most-preferred first.

    `()` where there is none, and the two cases that produce it are different in kind:

    * a `boolean` or `direction` derived row **carries no numeral at all** — `crossed_zero`
      answers a word and `trend_direction` a direction — and §13.2 refuses one written for it
      under `derived_unit_mismatch`. The caller decides what to do about a row that can be
      talked about but not counted; this function will not invent a number for it.
    * a value Python renders in exponential notation (`1e+21`) tokenises as *two* numerals and
      is `binding_rendering_is_not_one_numeral`. No corpus value is near that, so this is a
      latent gap closed rather than a live bug — but the emitter's contract is that everything
      it returns is admissible, and a guard is cheaper than an exception to the contract.

    **An observation is offered more than one form and a derived fact exactly one**, and the
    asymmetry is §13.2's rather than a preference. `SURFACE_UNITS` is tolerant on an observation
    — a bare numeral makes no unit claim and the binding supplies the unit — while
    `DERIVED_SURFACES` admits one surface family per derived unit, and `derived_figure` already
    picks the writable member of it. Offering `"$446000000.0"` beside `"$446 million"` would be
    offering the exact spelling §2 measured a model failing on.

    **The printed form is deliberately not offered.** `PackagedFact.printed_form` and
    `quoted_text` are what the *filing* printed, at the filing's scale — `"556"` for
    `556000000.0` — so writing one would be a numeral §13.1 compares against `value` and refuses
    as `number_outside_tolerance`. The scaled money form is the safe version of the same idea:
    it is checked to multiply back exactly.
    """
    if isinstance(row, DerivedFact):
        if row.result is None or row.unit in NON_NUMERIC_DERIVED_UNITS:
            return ()  # a word-valued row; §13.2 refuses a numeral written for one
        magnitude = _derived_magnitude(row)
        if not _is_writable(magnitude) and _derived_money(magnitude, row, package) is None:
            return ()  # the exponent guard, and the scaled form is the one thing that rescues it
        return (derived_figure(row, package),)
    return tuple(form for form in (observed_figure(row), _plain_figure(row), _bare_figure(row))
                 if form is not None)


def _plain_figure(fact: PackagedFact) -> str | None:
    """`"-12.6 percent"` — the value and its unit, which is how the FACTS section prints it.

    Legal against §13.2 for every unit the corpus holds: `percent` and `USD` spelled as words
    carry no surface the tokeniser reads (`SurfaceUnit.NONE` makes no unit claim), and `homes`
    and `markets` are read off the noun and equal the observation's own unit. `None` where the
    unit is empty, in which case this form and `_bare_figure` are the same string.
    """
    if not fact.unit or not _is_writable(fact.value):
        return None
    return f"{fact.value} {fact.unit}"


def _bare_figure(fact: PackagedFact) -> str | None:
    """The numeral alone, for a sentence that carries the unit in its own words."""
    return f"{fact.value}" if _is_writable(fact.value) else None


def _is_writable(value: float) -> bool:
    """One numeral, or nothing.

    `repr` is what the two forms above interpolate, so `repr` is what decides: `1e+21` holds two
    digit runs and tokenises as two numerals, and `nan`/`inf` hold none.
    """
    return isfinite(value) and "e" not in repr(value)


def direction_phrase(derived: DerivedFact) -> str:
    """The words this derivation's `display_semantics` already is, or `""`.

    `DisplaySemantics`' twelve values are English phrases *because* code chose them — that is
    §4.4's whole reason for the field — so there is nothing to translate here and translating
    would be a second vocabulary. `DIRECTION_UNVERIFIABLE` is the one member that is a statement
    about the *measurement* rather than a phrase a sentence can carry (`"…was direction
    unverifiable by…"` is not English), and it returns `""` for the same reason
    `period_surface_of_period` does: the row offers no such string, and the caller decides.

    This states no direction the verifier would not: `derived_facts.SEMANTIC_DIRECTION` maps
    `unchanged`, `equal to`, `times` and the two crossing members to `None`, meaning *"this
    derivation states no direction"*, and §13.14 refuses a directional word written over one of
    them. The phrases themselves are still the sentence's own words for what the row is.
    """
    if derived.display_semantics is DisplaySemantics.DIRECTION_UNVERIFIABLE:
        return ""
    return derived.display_semantics.value


__all__ = [
    "DERIVED_FIGURE_FORMATS",
    "NON_NUMERIC_DERIVED_UNITS",
    "DIRECTIONAL_SEMANTICS",
    "SCALED_MONEY_DECIMALS",
    "SCALE_WORDS",
    "derived_figure",
    "direction_phrase",
    "legal_renderings",
    "metric_surfaces",
    "observed_figure",
    "period_surface",
    "period_surface_of_fact",
    "period_surface_of_period",
    "scaled_money",
]
