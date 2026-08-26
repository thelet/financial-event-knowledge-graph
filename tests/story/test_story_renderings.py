"""`story/core/renderings.py` — the one emitter, and the two recognisers it must agree with.

S1 of `docs/2026-08-23-deterministic-draft-compiler/02-IMPLEMENTATION-PLAN.md`. The module under
test writes; `story/stages/verification/` reads; neither imports the other. Every claim this
file makes is therefore an **agreement** between two independent implementations rather than a
property of one — which is the whole reason the move was worth making: before it, the prompt
printer held one private copy of the rendering rules and the derivation stage held a second copy
of the period rule that disagreed with the first.

Two tests carry the load:

* `test_every_rendering_the_core_emitter_produces_is_one_the_verifier_admits` drives every
  string `legal_renderings` returns through `numerals.tokenize_numerals` and the two surface
  maps §13.2 judges against — `SURFACE_UNITS`/`CHANGE_SURFACES` for an observation,
  `derived_facts.DERIVED_SURFACES` for a derived row — and through `compare_token_to_fact`,
  which is §13.1's own tolerance and sign test. A rendering that failed either would be a string
  code told a writer to write and then refused it for writing.
* `test_every_period_surface_the_emitter_can_produce_resolves_back_to_its_own_endpoints` drives
  every form `period_surface` has through `period_grammar.resolve` and requires the endpoints
  back. A surface the grammar cannot parse would look like a period surface and refuse as an
  unresolvable one, which is worse than none at all.

The fixtures are the corpus's own values — `adjusted_gross_margin` 3.3 and `gaap_gross_margin`
-12.6 in 2022Q3, `adjusted_gross_profit` 556 → 110 in millions — for the reason
`test_story_writer.py` gives: a rendering test against invented magnitudes would not exercise
the scale arithmetic that made the change worth making.
"""

from __future__ import annotations

from typing import Any

import pytest

from story.core.models import (
    DERIVED_UNITS,
    DerivationOperation,
    DerivedFact,
    DisplaySemantics,
    EvidenceRole,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    StoryEvidencePackage,
)
from story.core.numerals import (
    SurfaceUnit,
    compare_token_to_fact,
    tokenize_numerals,
)
from story.core.periods import PeriodShape, story_period
from story.core.renderings import (
    DERIVED_PRESENTATION_DECIMALS,
    NON_NUMERIC_DERIVED_UNITS,
    derived_figure,
    direction_phrase,
    legal_renderings,
    metric_surfaces,
    observed_figure,
    period_surface,
    period_surface_of_fact,
    period_surface_of_period,
    scaled_money,
)
# The verifier is a different stage and `story/core/` may not import it — a test may, and must:
# the claim under test is that the two ends agree without sharing an implementation.
from story.stages.verification import derived_facts as derived_rules
from story.stages.verification.deterministic import CHANGE_SURFACES, SURFACE_UNITS
from story.stages.verification.metric_surfaces import MetricAliasIndex
from story.stages.verification.period_grammar import resolve as resolve_period

from conftest import make_package

PASSAGE_ID = "doc:opendoor-8k-2022q3#p133"
DOCUMENT_ID = "doc:opendoor-8k-2022q3"


def make_fact(**overrides: Any) -> PackagedFact:
    """One observation, defaulted to the demo's `adjusted_gross_margin` reading."""
    fields: dict[str, Any] = dict(
        observation_id="obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:aaaaaaaaaaaa",
        metric_id="adjusted_gross_margin",
        metric_label="Adjusted Gross Margin",
        period_key="2022Q3",
        period_start="2022-07-01",
        period_end="2022-09-30",
        shape="duration",
        value=3.3,
        unit="percent",
        scale="units",
        source_lane="normalized_table",
        validation_state="ok",
        passage_id=PASSAGE_ID,
        document_id=DOCUMENT_ID,
        quoted_text="3.3",
    )
    fields.update(overrides)
    return PackagedFact(**fields)


#: One reading per unit the corpus holds, at both scales that matter: `units`, where no money
#: form exists, and `millions`, where one does. Signed and unsigned, because the sign rule is
#: separate from the tolerance rule at §13.1 and a rendering has to survive both.
OBSERVATIONS: tuple[tuple[str, PackagedFact], ...] = (
    ("percent", make_fact(value=3.3, unit="percent", scale="units")),
    ("negative percent", make_fact(value=-12.6, unit="percent", scale="units")),
    ("money in millions", make_fact(value=556_000_000.0, unit="USD", currency="USD",
                                    scale="millions", metric_id="adjusted_gross_profit",
                                    metric_label="Adjusted Gross Profit")),
    ("money at a loss", make_fact(value=-110_000_000.0, unit="USD", currency="USD",
                                  scale="millions", metric_id="adjusted_gross_profit",
                                  metric_label="Adjusted Gross Profit")),
    ("money at an unscaled reading", make_fact(value=556_000_000.5, unit="USD", currency="USD",
                                               scale="units",
                                               metric_id="adjusted_gross_profit",
                                               metric_label="Adjusted Gross Profit")),
    ("homes", make_fact(value=8380.0, unit="homes", scale="units",
                        metric_id="homes_purchased", metric_label="Homes Purchased")),
    ("markets", make_fact(value=39.0, unit="markets", scale="units",
                          metric_id="markets_served", metric_label="Markets Served")),
)


def money_package() -> StoryEvidencePackage:
    """A package whose two facts are the derivation's inputs, both filed in millions.

    `_derived_money` reads the inputs' scale off the package to decide whether the scaled form
    is available at all, so a derived-figure test against a package that does not hold them
    would be testing the fallback and calling it the answer.
    """
    facts = tuple(
        make_fact(observation_id=f"obs:adjusted-gross-profit:opendoor:{key}:table:{digest}",
                  metric_id="adjusted_gross_profit", metric_label="Adjusted Gross Profit",
                  period_key=key, period_start=start, period_end=end, value=value,
                  unit="USD", currency="USD", scale="millions", quoted_text=printed,
                  printed_form=printed)
        for key, start, end, value, printed, digest in (
            ("2022Q2", "2022-04-01", "2022-06-30", 556_000_000.0, "556", "0c4364ebbc44"),
            ("2022Q3", "2022-07-01", "2022-09-30", 110_000_000.0, "110", "4d66ef7200e9"))
    )
    return make_package(
        facts=facts,
        metrics=(PackagedMetric(metric_id="adjusted_gross_profit",
                                label="Adjusted Gross Profit", unit="USD",
                                period_type="duration"),),
        primary_passages=(PackagedPassage(passage_id="psg:1", document_id="doc:1",
                                          text="Adjusted Gross Profit 556 110",
                                          char_count=29,
                                          role=EvidenceRole.PRIMARY_SUPPORT),))


def make_derived(**overrides: Any) -> DerivedFact:
    """The `absolute_change` §2 measured, hand-built here because the *rendering* is the subject.

    `test_story_writer.py` builds its derived fact by running the derivation stage, and that is
    the right choice there — the claim is that the writer binds what the pipeline computes. Here
    the claim is about a string per `(unit, display_semantics)` pair, and several of the pairs
    below are ones no operation in this corpus produces yet. The stage cannot mint those, and a
    table asserted only over the rows the demo happens to reach is the table a new unit gets
    added to without anyone noticing.
    """
    fields: dict[str, Any] = dict(
        fact_id="fact:derived:absolute-change:opendoor:adjusted-gross-profit:2022Q3:aaaaaaaaaaaa",
        operation=DerivationOperation.ABSOLUTE_CHANGE,
        package_id=money_package().package_id,
        from_fact_id=money_package().facts[0].observation_id,
        to_fact_id=money_package().facts[1].observation_id,
        from_period="2022Q2", to_period="2022Q3",
        from_value=556_000_000.0, to_value=110_000_000.0,
        result=-446_000_000.0, unit="USD", currency="USD",
        display_semantics=DisplaySemantics.DECREASED_BY,
        metric_id="adjusted_gross_profit", from_metric_id="adjusted_gross_profit",
        period_surface_hint="the third quarter of 2022",
        tool_version="1.0.0",
    )
    fields.update(overrides)
    return DerivedFact(**fields)


# -- the agreement with §13.1 and §13.2 -------------------------------------------------------


def assert_one_admissible_numeral(rendering: str, value: float) -> SurfaceUnit:
    """Every rendering is one numeral, reading back the value it was rendered from.

    `binding_rendering_is_not_one_numeral` is what a span holding none or two earns, and
    `number_outside_tolerance` is what a span whose digits do not denote the fact earns. Both
    are asserted through the verifier's own functions rather than by comparing strings.
    """
    tokens = tokenize_numerals(rendering)
    assert len(tokens) == 1, f"{rendering!r} holds {len(tokens)} numerals, not one"
    verdict = compare_token_to_fact(tokens[0], value)
    assert verdict.within_window, f"{rendering!r} does not denote {value!r}"
    assert not (verdict.sign_explicit and not verdict.sign_agrees), (
        f"{rendering!r} carries a sign that argues with {value!r}")
    return tokens[0].unit


@pytest.mark.parametrize("label,fact", OBSERVATIONS, ids=[label for label, _ in OBSERVATIONS])
def test_every_rendering_the_core_emitter_produces_is_one_the_verifier_admits(label, fact):
    """R1's precondition: a compiler may insert only strings §13 already admits.

    The four things §13.1/§13.2 ask of a numeral bound to an **observation** — one numeral, in
    tolerance, sign agreeing, and a surface that either makes no unit claim or makes this
    observation's — asked here of everything the emitter is willing to write. `CHANGE_SURFACES`
    is the fifth: `percentage_points`, `basis_points` and `multiple` are quantities no
    observation in this corpus is, so an emitter that produced one would be rendering a derived
    quantity onto a reported row.
    """
    renderings = legal_renderings(fact, money_package())
    assert renderings, f"{label}: an observation with a finite value always has a rendering"

    for rendering in renderings:
        unit = assert_one_admissible_numeral(rendering, fact.value)
        assert unit not in CHANGE_SURFACES, f"{rendering!r} renders a change on a level"
        claimed = SURFACE_UNITS.get(unit)
        assert claimed in (None, fact.unit), (
            f"{rendering!r} claims {claimed} on a {fact.unit} observation — unit_mismatch")
        if unit is SurfaceUnit.USD:
            assert fact.unit == "USD", f"{rendering!r} puts a currency symbol on a non-money row"


@pytest.mark.parametrize("unit", DERIVED_UNITS)
def test_every_derived_rendering_carries_a_unit_surface_13_2_reads(unit):
    """The same agreement on the strict side of the map, over **every** derived unit.

    `DERIVED_SURFACES` admits one surface family per unit — `USD` only with a currency symbol,
    `percentage_points` only in the two-word spelling — where `SURFACE_UNITS` tolerates a bare
    numeral. `"446000000.0 USD"` tokenises as `none` and is `derived_unit_mismatch`; that string
    is what a live 9B model wrote when the prompt printed `{result} {unit}`, which is why the
    emitter renders the unit's *surface* and this test is over the table rather than over the
    two units the demo produces.
    """
    result = -446_000_000.0
    if unit in NON_NUMERIC_DERIVED_UNITS:
        # A word-valued row, built the way the model validator requires: a result *or* a word.
        wordy = make_derived(unit=unit, result=None, result_word=derived_rules.CROSSED,
                             currency=None,
                             display_semantics=DisplaySemantics.CROSSED_ZERO)
        assert legal_renderings(wordy, money_package()) == ()
        # …and the same row carrying a scalar, which the validator permits and §13.2 refuses.
        assert legal_renderings(make_derived(unit=unit, result=result, currency=None),
                                money_package()) == ()
        return

    derived = make_derived(unit=unit, result=result,
                           currency="USD" if unit == "USD" else None)
    renderings = legal_renderings(derived, money_package())
    assert renderings == (derived_figure(derived, money_package()),), (
        "the first rendering must be the string the prompt already prints, and a derived row "
        "offers exactly one")
    surface = assert_one_admissible_numeral(renderings[0], abs(result))
    assert surface in derived_rules.DERIVED_SURFACES[unit], (
        f"{renderings[0]!r} reads as {surface.value}, which §13.2 refuses for a {unit} result")


#: §13.1's window against the exact `{{D2}}` value the plan §8 measured, reproduced here rather
#: than quoted: `d` is the **draft's** significant figures, so a shortened figure widens its own
#: window and stays inside it. The last row is the one that must fail — a rounding that flipped
#: the sign would be a different claim, and §13.1 refuses it on a separate test from the window.
D2_RESULT = 80.215827338
D2_TOLERANCE: tuple[tuple[str, int, float, bool], ...] = (
    ("80.215827338%", 11, 5e-10, True),
    ("80.22%", 4, 0.005, True),
    ("80.2%", 3, 0.05, True),
    ("80%", 1, 5.0, True),
    ("-80.2%", 3, 0.05, False),
)


@pytest.mark.parametrize("written,figures,window,accepted", D2_TOLERANCE,
                         ids=[row[0] for row in D2_TOLERANCE])
def test_rounding_a_derived_percentage_stays_inside_13_1s_window(written, figures, window,
                                                                 accepted):
    """The measurement `DERIVED_PRESENTATION_DECIMALS` rests on, run rather than cited.

    Plan §8's table, against §13.1's own function. It is the load-bearing fact of the change:
    the tolerance window is computed from what the **draft** printed, so writing `80.2%` for
    `80.215827338` widens the window from `5e-10` to `0.05` and the figure lands inside it. A
    presentation rule that had to be checked against a *fixed* tolerance could not be adopted at
    all.
    """
    tokens = tokenize_numerals(written)
    assert len(tokens) == 1
    verdict = compare_token_to_fact(tokens[0], D2_RESULT)
    assert verdict.draft_significant_figures == figures
    assert verdict.window == pytest.approx(window)
    assert verdict.accepted is accepted
    if not accepted:
        assert verdict.within_window and not verdict.sign_agrees, (
            "the last row must fail on the sign and not on the window")


def test_a_derived_percentage_is_written_at_the_precision_the_corpus_prints_margins_at():
    """`{{D2}}` printed `80.215827338%`, and nine of those decimals were a comparison constant.

    `derivation/operations.round_delta` rounds to `series.DELTA_PRECISION = 9` so that
    `13.2 − 3.3` does not publish `9.899999999999999`; the renderer then printed the float. This
    is the fix asserted end to end — the string, and the fact that `result` did not move.
    """
    package = money_package()
    fact = make_derived(unit="percent", currency=None, result=D2_RESULT,
                        operation=DerivationOperation.PERCENTAGE_CHANGE,
                        display_semantics=DisplaySemantics.DECREASED_BY)
    assert derived_figure(fact, package) == "80.2%"
    assert fact.result == D2_RESULT, "the row keeps every digit the derivation tool computed"
    assert_one_admissible_numeral("80.2%", D2_RESULT)


@pytest.mark.parametrize("unit,result,expected", (
    ("percent", 80.215827338, "80.2%"),
    ("percent", -12.6, "12.6%"),          # directional semantics; the word carries the sign
    ("percentage_points", 15.899999999999999, "15.9 percentage points"),
    ("multiple", 1.3512396694214877, "1.35x"),
    ("multiple", 2.5, "2.5x"),            # a maximum, so a trailing zero is not written
    ("homes", 4953.0, "4953 homes"),
    ("markets", 39.0, "39 markets"),
))
def test_every_unit_with_a_presentation_precision_is_written_to_it(unit, result, expected):
    """The table, row by row, on values the corpus holds or the demo produced.

    `4953.0 homes` is one of the three stored renderings this change alters — a count of houses
    with a decimal point on it — and `2.5x` is the reason the table is a **maximum**: quantising
    to two decimals and then normalising, exactly as `scaled_money` finishes, writes `2.5x`
    rather than `2.50x`.
    """
    fact = make_derived(unit=unit, currency=None, result=result,
                        display_semantics=(DisplaySemantics.TIMES if unit == "multiple"
                                           else DisplaySemantics.DECREASED_BY))
    assert derived_figure(fact, money_package()) == expected
    assert_one_admissible_numeral(expected, abs(result))


def test_a_unit_with_no_presentation_precision_is_printed_exactly_as_before():
    """`USD` is absent from the table on purpose and money is unchanged by this policy.

    `scaled_money` already owns it with the stronger guarantee — the quotient is kept only where
    it multiplies back exactly — and a second rounding rule over the same unit would be the two
    copies of a rendering rule this module exists to prevent.
    """
    assert "USD" not in DERIVED_PRESENTATION_DECIMALS
    package = money_package()
    assert derived_figure(make_derived(), package) == "$446 million"
    # …and the plain form, where the inputs' scale gives `scaled_money` nothing to work with.
    unscaled = make_derived(result=-446_000_000.5)
    assert derived_figure(unscaled, package) == "$446000000.5"


def test_a_value_too_small_for_its_precision_is_written_in_full_rather_than_rounded_to_zero():
    """The guard, on the case that makes it necessary rather than on a hypothetical.

    §13.1's window is `0.5 × 10^(e − d + 1)` with `e` taken from the **fact**, so rounding to a
    fixed number of *decimals* only coincides with the window while the value is at least 1. A
    `percent` result of `0.04` written to one decimal is `0.0`: a difference of `0.04` against a
    window of `0.005`, which is `number_outside_tolerance` — a blocking refusal manufactured by
    a presentation rule. The renderer checks its own output and writes the full float instead.
    """
    package = money_package()
    tiny = make_derived(unit="percent", currency=None, result=0.04,
                        display_semantics=DisplaySemantics.DECREASED_BY)
    assert derived_figure(tiny, package) == "0.04%"
    assert_one_admissible_numeral("0.04%", 0.04)

    rounded = tokenize_numerals("0.0%")
    assert not compare_token_to_fact(rounded[0], 0.04).within_window, (
        "the string the guard refused to write must be the one §13.1 refuses")


def test_the_word_valued_units_are_the_two_the_verifier_refuses_a_numeral_on():
    """A checked copy, not a hoped-over one — `core/` may import neither stage that holds it.

    `derivation/operations.py` spells the pair as `UNIT_BOOLEAN`/`UNIT_DIRECTION` and
    `verification/derived_facts.py` as `NON_NUMERIC_UNITS`; this is the third site, and the
    reason it is worth having is that the emitter has to answer *"is there a numeral for this
    row"* before either stage is reachable.
    """
    assert NON_NUMERIC_DERIVED_UNITS == derived_rules.NON_NUMERIC_UNITS


def test_a_directional_row_renders_the_magnitude_and_a_non_directional_one_the_sign():
    """§13.1's `sign_disagreement`, from the writing end.

    *"decreased by -446000000.0"* is a double negative in prose and a written minus arguing with
    `result`'s sign is a refusal, so the four `DIRECTIONAL_SEMANTICS` render the magnitude —
    their word already carries the direction. `times`, `equal to` and `unchanged` are not in that
    set on purpose: a `ratio` over a negative reading is genuinely negative and its word says
    nothing about that.
    """
    package = money_package()
    fell = make_derived(display_semantics=DisplaySemantics.DECREASED_BY)
    ratio = make_derived(unit="multiple", currency=None, result=-2.5,
                         operation=DerivationOperation.RATIO,
                         display_semantics=DisplaySemantics.TIMES)

    assert legal_renderings(fell, package) == ("$446 million",)
    assert legal_renderings(ratio, package) == ("-2.5x",)


def test_an_observation_offers_the_money_form_first_and_never_the_printed_form():
    """Order is a claim: the compiler takes the first element, so the first must be the best.

    `"$556 million"` over `"556000000.0 USD"` is H3's measured repair — the FACTS section and the
    DERIVED FACTS section print one money value in one shape, so a model has no scaling left to
    do by hand. And `printed_form` is **not** offered at all, though it is the string the filing
    carries: it is printed at the filing's scale (`"556"` for `556000000.0`), so a sentence
    carrying it would be refused as `number_outside_tolerance` against the fact's own value.
    """
    fact = money_package().facts[0]
    assert fact.printed_form == "556"
    assert legal_renderings(fact, money_package()) == (
        "$556 million", "556000000.0 USD", "556000000.0")


def test_a_value_no_single_numeral_can_spell_is_offered_no_plain_form():
    """`repr(1e+21)` is `'1e+21'`, which tokenises as **two** numerals — `1` and `21`.

    No corpus value is within eleven orders of magnitude of that, so this is a latent gap closed
    rather than a live bug; it is here because the emitter's contract is that everything it
    returns is admissible, and a contract with an unstated exception is not one.
    """
    huge = make_fact(value=1e21, unit="USD", currency="USD", scale="units")
    assert legal_renderings(huge, money_package()) == ()

    scaled = make_fact(value=1e21, unit="USD", currency="USD", scale="millions")
    # The scaled form spells the same value with one digit run, so it is offered and it holds.
    assert legal_renderings(scaled, money_package()) == ("$1000000000000000 million",)
    assert_one_admissible_numeral("$1000000000000000 million", 1e21)


# -- the period grammar, round-tripped ---------------------------------------------------------


#: Every window §13.4's closed grammar has a form for, and the one form the emitter writes.
#: The instant and the fiscal year are the two the derivation stage used to spell differently.
PERIODS: tuple[tuple[str | None, str | None, str | None, str], ...] = (
    ("2022-01-01", "2022-03-31", None, "the first quarter of 2022"),
    ("2022-04-01", "2022-06-30", None, "the second quarter of 2022"),
    ("2022-07-01", "2022-09-30", None, "the third quarter of 2022"),
    ("2022-10-01", "2022-12-31", None, "the fourth quarter of 2022"),
    ("2022-01-01", "2022-06-30", None, "the first half of 2022"),
    ("2022-01-01", "2022-09-30", None, "the nine months ended September 30, 2022"),
    ("2022-01-01", "2022-12-31", None, "fiscal 2022"),
    (None, None, "2022-09-30", "September 30, 2022"),
    (None, None, "2021-12-31", "December 31, 2021"),
)


@pytest.mark.parametrize("start,end,instant,expected", PERIODS)
def test_every_period_surface_the_emitter_can_produce_resolves_back_to_its_own_endpoints(
        start, end, instant, expected):
    """The grammar is §13.4's and this is the writing direction of it, so the pair is
    round-tripped rather than assumed to agree. A surface that resolved to different endpoints
    would be `period_mismatch` on a draft code itself wrote."""
    surface = period_surface(period_start=start, period_end=end, instant_date=instant)
    assert surface == expected

    resolved = resolve_period(surface)
    assert resolved.resolved, f"{surface!r} does not resolve at all"
    assert (resolved.period_start, resolved.period_end, resolved.instant_date) == (
        start, end, instant)


@pytest.mark.parametrize("start,end,instant,expected", PERIODS)
def test_the_three_period_entry_points_are_one_emitter_with_three_doors(
        start, end, instant, expected):
    """A fact, a `StoryPeriod` and three loose dates are the same question asked three ways.

    This is the collapse S1 exists for. `derivation/operations.period_surface_hint` used to
    answer it a second time and disagreed for three shapes; it is now this function under
    another name, so the assertion that they agree is an identity rather than a coincidence.
    """
    fact = make_fact(period_start=start, period_end=end, instant_date=instant,
                     period_key="x", shape="instant" if instant else "duration")
    period = story_period(start, end, instant)

    assert period_surface_of_fact(fact) == expected
    assert period_surface_of_period(period) == expected


def test_the_worded_forms_won_and_three_derived_hints_changed_with_them():
    """The behaviour change S1 chose, recorded as an assertion rather than left in a docstring.

    `period_surface_hint` wrote `"2022-09-30"`, `"fiscal year 2022"` and `"the six months ended
    June 30, 2022"` for these three shapes; it now writes the worded forms, because the string
    goes into a `FactBinding.period_surface` that a *sentence* has to agree with. Quarters and
    nine-month windows are unchanged, which is every shape any recorded run has produced — no
    committed artifact moves.
    """
    from story.stages.derivation.operations import period_surface_hint

    assert period_surface_hint(story_period(None, None, "2022-09-30")) == "September 30, 2022"
    assert period_surface_hint(story_period("2022-01-01", "2022-12-31")) == "fiscal 2022"
    assert period_surface_hint(story_period("2022-01-01", "2022-06-30")) == (
        "the first half of 2022")

    unchanged = story_period("2022-07-01", "2022-09-30")
    assert period_surface_hint(unchanged) == "the third quarter of 2022"
    assert period_surface_hint(story_period("2022-01-01", "2022-09-30")) == (
        "the nine months ended September 30, 2022")


def test_a_window_outside_the_closed_grammar_is_offered_no_surface_at_all():
    """§13.4 refuses a surface it cannot resolve, so the honest answer is that there is none.

    `""` from `period_surface_of_period` and `None` from the other two doors, and the difference
    is the caller's: `DerivedFact.period_surface_hint` is a `str` field whose empty value already
    means *"no surface names this window"*, and `period_surface_hint`'s contract is kept.
    """
    odd = ("2022-02-01", "2022-11-30", None)
    assert period_surface(period_start=odd[0], period_end=odd[1]) is None
    assert period_surface_of_fact(
        make_fact(period_start=odd[0], period_end=odd[1], period_key="odd")) is None
    assert story_period(*odd).shape is PeriodShape.OTHER
    assert period_surface_of_period(story_period(*odd)) == ""


def test_a_date_that_does_not_parse_is_no_surface_rather_than_an_exception():
    """F13's finding, at the writing end: this graph stores every date as a `String`."""
    assert period_surface(instant_date="not-a-date") is None
    assert period_surface(period_start="2022-13-01", period_end="2022-03-31") is None


# -- metric surfaces ----------------------------------------------------------------------------


def two_metric_package() -> StoryEvidencePackage:
    """The demo's own pair, which is the pair §13.5's sub-phrase rule exists for."""
    return make_package(metrics=(
        PackagedMetric(metric_id="gaap_gross_margin", label="Gross Margin", unit="percent",
                       period_type="duration"),
        PackagedMetric(metric_id="adjusted_gross_margin", label="Adjusted Gross Margin",
                       unit="percent", period_type="duration")))


def test_a_surface_inside_another_metrics_surface_is_not_offered():
    """§13.5 resolves by longest match, so `"Gross Margin"` names two metrics and neither.

    The id survives the filter and is what the accepted demo draft binds: `MetricAliasIndex`
    indexes `(metric_id, label, *aliases)` and normalises `_` to a space, so `"gaap gross
    margin"` resolves through the id entry.
    """
    package = two_metric_package()
    assert metric_surfaces(package, "gaap_gross_margin") == ("gaap gross margin",)
    assert metric_surfaces(package, "adjusted_gross_margin") == ("Adjusted Gross Margin",)


def test_a_metric_no_surface_names_uniquely_offers_nothing_rather_than_a_guess():
    """An empty tuple is a real answer, and the caller decides what to say about it."""
    package = make_package(metrics=(
        PackagedMetric(metric_id="gross_margin", label="Margin", unit="percent",
                       period_type="duration"),
        PackagedMetric(metric_id="adjusted_gross_margin", label="Adjusted Margin",
                       unit="percent", period_type="duration")))
    assert metric_surfaces(package, "gross_margin") == ()
    assert metric_surfaces(package, "no_such_metric") == ()


def test_two_metrics_sharing_a_surface_exactly_are_still_offered_it():
    """**A gap in the local approximation, recorded rather than closed here** *(2026-08-23)*.

    The filter drops a surface that is a *strict* sub-phrase of another metric's — `" gross
    margin " in " adjusted gross margin "` — and the `other != phrase` guard means an
    **exactly equal** surface survives it. Two metrics whose label and alias coincide are
    therefore both offered that string, and §13.5's index resolves it to two metric ids, which
    is `metric_surface_ambiguous`.

    Left alone in S1 because S1 is a move: changing the comparison would change what some
    package's prompt prints, and the stage's own rule is that the verifier remains the authority
    on §13.5. It is named here because the draft compiler's R3 — *"a row with no unique metric
    surface offers no slot"* — cannot be read off this function alone, and a later stage that
    assumes it can would offer a slot the verifier refuses.
    """
    package = make_package(metrics=(
        PackagedMetric(metric_id="homes_purchased", label="Homes Acquired", unit="homes",
                       period_type="duration"),
        PackagedMetric(metric_id="homes_sold", label="Homes Resold", unit="homes",
                       period_type="duration", aliases=("Homes Acquired",))))

    assert metric_surfaces(package, "homes_purchased") == ("Homes Acquired", "homes purchased")
    resolution = MetricAliasIndex.from_package(package).resolve("Homes Acquired")
    assert resolution.metric_ids == ("homes_purchased", "homes_sold")


# -- the direction word --------------------------------------------------------------------------


def test_the_direction_phrase_is_the_closed_vocabulary_and_not_a_translation_of_it():
    """`DisplaySemantics`' values are already the English words, because code chose them."""
    package = money_package()
    assert direction_phrase(make_derived()) == "decreased by"
    assert direction_phrase(
        make_derived(display_semantics=DisplaySemantics.HIGHER_THAN)) == "higher than"
    assert direction_phrase(
        make_derived(display_semantics=DisplaySemantics.UNCHANGED)) == "unchanged"
    assert legal_renderings(make_derived(), package)  # the row still carries its numeral


def test_an_unverifiable_direction_offers_no_phrase():
    """*"…was direction unverifiable by $446 million"* is not a sentence.

    The member is the honest reading of an unmeasured sign convention — `direct_selling_costs` is
    stored negative on 46 of 46 canonical values — and what it says is *"no direction was
    established"*. A row that offers no phrase is one a template may not ask for a direction
    word from, which is R3 applied to this slot.
    """
    unverifiable = make_derived(display_semantics=DisplaySemantics.DIRECTION_UNVERIFIABLE)
    assert direction_phrase(unverifiable) == ""
    # …and the figure is still writable: the move is real and citable, only the word is not.
    assert legal_renderings(unverifiable, money_package()) == ("-$446 million",)


# -- the two money helpers, kept honest -----------------------------------------------------------


def test_the_scaled_form_is_reserved_for_an_exact_division_and_not_for_a_round_number():
    """`scaled_money` divides in `Decimal` and keeps the quotient only when it multiplies back.

    An emitter that guessed here would be choosing the digits §13.1's tolerance window is
    computed from, so the fallback is the long form and never a rounding.
    """
    assert scaled_money(-446_000_000.0, "millions") == "-$446 million"
    assert scaled_money(446_500_000.0, "millions") == "$446.5 million"
    assert scaled_money(446_000_000.5, "millions") is None    # not exact
    assert scaled_money(446_050_000.0, "millions") is None    # exact, but two decimals
    assert scaled_money(446_000_000.0, "units") is None       # no scale word
    assert scaled_money(446_000_000.0, "") is None

    assert observed_figure(make_fact(value=3.3, unit="percent")) is None
