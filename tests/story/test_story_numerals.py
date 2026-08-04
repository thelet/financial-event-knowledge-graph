"""§13.1–§13.3 and §13.7.1 against the corpus they were written about.

**No invented number appears below.** Every fixture is a value, a quote or a sentence read out
of the loaded graph on 2026-08-04 (`bolt://localhost:7687`, 2,704 observations, 2,690 of them
from tables), and the two `neo4j`-marked tests re-read the graph so the offline fixtures
cannot drift away from it silently. The reason is §13.1's own failure mode: a tolerance test
and a reconstruction rule both look right against numbers chosen to make them look right, and
the only thing that distinguishes a correct rule from a plausible one here is a corpus where
`scale` means "how it was printed" rather than "multiply by this".

The live reconstruction is the load-bearing one — §13.7.1 claims Rule A step 2 holds
2,690/2,690, and this file is where that claim is executed rather than quoted.
"""

from __future__ import annotations

import pytest

from story.core.numerals import (
    PercentOperation,
    RelativeChangeAcrossZero,
    SurfaceUnit,
    UnreconstructableQuote,
    compare_to_fact,
    compare_token_to_fact,
    delta_bps,
    delta_pp,
    delta_relative,
    find_ambiguous_percent_changes,
    matches_at_printed_precision,
    percent_delta,
    reconstruct_table_quote,
    significant_figures,
    surface_supports_operation,
    tokenize_numerals,
    tolerance_window,
)
from story.providers.neo4j_connection import (
    StoryGraphCredentialError,
    StoryGraphUnconfiguredError,
    load_neo4j_settings,
    open_read_executor,
)

# -- what the graph holds, read on 2026-08-04 ------------------------------------------------

#: §13.1's own example. The same fact is printed twice in the corpus, at two precisions and
#: two scales, and both reconstruct to a value 1,000x apart in *printed* terms and identical
#: in canonical ones only because `scale` is applied to the quote and never to `value`.
ADJUSTED_EBITDA_2020Q4 = -27075000.0
ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE = "(27,075)"
#: A second printing of a *different* value for the same metric-period: `(27)` in a millions
#: table is `-27,000,000`, not a rounding of `-27,075,000`. §13.12's conflict, and the reason
#: over-precision has to be told which printing it is judging.
ADJUSTED_EBITDA_2020Q4_MILLIONS_VALUE = -27000000.0
ADJUSTED_EBITDA_2020Q4_MILLIONS_QUOTE = "(27)"

#: §6.3's spike, and the row the S0c adapter first read off the `EVIDENCED_BY` edge.
ADJUSTED_EBITDA_2022Q3 = -211000000.0
ADJUSTED_EBITDA_2022Q3_QUOTE = "(211)"

#: §13.1's third worked case. `(341)` is `adjusted_ebitda` 2023Q1 — and also the nine months
#: ending 2023-03-31, which is §13.4's conflation in one quote.
ADJUSTED_EBITDA_2023Q1 = -341000000.0
ADJUSTED_EBITDA_2023Q1_QUOTE = "(341)"

#: `adjusted_ebitda_margin`, the metric §13.3's worked example is about.
MARGIN_2021Q2 = 2.2
MARGIN_2022Q2 = 5.2
MARGIN_2022Q3 = -6.3

#: `adjusted_gross_margin` quarters, kept because subtracting them is *not* exact in binary.
GROSS_MARGIN_2021Q4 = 7.3
GROSS_MARGIN_2022Q1 = 9.9
GROSS_MARGIN_2022Q2 = 13.2
GROSS_MARGIN_2022Q3 = 3.3
GROSS_MARGIN_2022Q4 = -3.2

#: Verbatim `EVIDENCED_BY.quoted_text` on narrative observations — full sentences, unlike the
#: median-4-character table quotes.
LEVELS_PASSAGE = ("As a percentage of revenue, Adjusted EBITDA was 5.2% in 2Q22 versus 2.2% "
                  "in 2Q21.")
CONTRACTS_PASSAGE = ("Additionally, as of the end of the year, we were in contract to "
                     "purchase 2,114 homes, up 27% versus 3Q23 and up 109% versus 4Q22.")
FACILITY_PASSAGE = ("On October 19, 2022, a subsidiary of the Company entered into a new "
                    "asset-backed senior revolving credit facility with $525 million in "
                    "borrowing capacity and a final maturity date of October 31, 2023.")
INVENTORY_PASSAGE = ("We ended 2Q with 17,013 homes in inventory on our balance sheet, "
                     "representing $6.6 billion in value.")

#: The measurement §13.7.1 asserts and this file executes.
TABLE_OBSERVATION_COUNT = 2690
NARRATIVE_OBSERVATION_COUNT = 14

TABLE_FACTS = (
    "MATCH (o:Observation)-[e:EVIDENCED_BY]->() "
    "WHERE o.source_lane = 'normalized_table' "
    "RETURN o.observation_id AS observation_id, o.value AS value, o.unit AS unit, "
    "o.scale AS scale, e.quoted_text AS quoted_text"
)
NARRATIVE_FACTS = (
    "MATCH (o:Observation)-[e:EVIDENCED_BY]->() "
    "WHERE o.source_lane = 'normalized_narrative' "
    "RETURN o.observation_id AS observation_id, o.value AS value, o.unit AS unit, "
    "o.scale AS scale, e.quoted_text AS quoted_text"
)


# -- direction 1: a numeral out of prose (§13.1) ----------------------------------------------


@pytest.mark.parametrize("surface, span, value, unit, figures", [
    ("$27.1 million", "$27.1 million", 27100000.0, SurfaceUnit.USD, 3),
    ("$27 million", "$27 million", 27000000.0, SurfaceUnit.USD, 2),
    ("-$27.1 million", "-$27.1 million", -27100000.0, SurfaceUnit.USD, 3),
    ("$27.0750 million", "$27.0750 million", 27075000.0, SurfaceUnit.USD, 6),
    ("5.2%", "5.2%", 5.2, SurfaceUnit.PERCENT, 2),
    ("$525 million", "$525 million", 525000000.0, SurfaceUnit.USD, 3),
    ("$6.6 billion", "$6.6 billion", 6600000000.0, SurfaceUnit.USD, 2),
    # The unit noun is not part of the numeral. It supplies the unit and stays out of the span,
    # because the span is what a §13.1 finding highlights.
    ("2,114 homes", "2,114", 2114.0, SurfaceUnit.HOMES, 4),
    ("17,013 homes", "17,013", 17013.0, SurfaceUnit.HOMES, 5),
    ("3.0 percentage points", "3.0 percentage points", 3.0, SurfaceUnit.PERCENTAGE_POINTS, 2),
    ("300 bps", "300 bps", 300.0, SurfaceUnit.BASIS_POINTS, 1),
])
def test_a_numeral_reconstructs_from_its_own_surface_and_carries_the_unit_that_surface_implies(
        surface, span, value, unit, figures):
    """Sign, separators and the magnitude word the writer typed. Nothing else."""
    token, = tokenize_numerals(surface)

    assert token.value == value
    assert token.unit is unit
    assert token.significant_figures == figures
    assert token.text == span


def test_the_scale_is_never_applied_to_a_numeral_the_writer_spelled_out():
    """The 1,000x error §13.1 exists to prevent, shown from both ends of the same fact.

    `adjusted_ebitda 2020Q4` is `-27,075,000` and was printed `"(27,075)"` in a thousands
    table. Read as prose the quote is minus twenty-seven thousand; read as a table cell under
    its scale it is the canonical value. Two different functions, and the wrong one applied to
    a narrative surface — `"$218 million"` alongside `scale: millions` — gives 2.18e14.
    """
    prose, = tokenize_numerals(ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE)
    assert prose.value == -27075.0

    assert reconstruct_table_quote(
        ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE, scale="thousands", unit="USD"
    ) == ADJUSTED_EBITDA_2020Q4

    narrative, = tokenize_numerals("$218 million")
    assert narrative.value == 218000000.0


@pytest.mark.parametrize("text", [LEVELS_PASSAGE, CONTRACTS_PASSAGE, FACILITY_PASSAGE,
                                  INVENTORY_PASSAGE])
def test_every_span_slices_back_to_exactly_the_characters_it_was_read_from(text):
    """§13.1's spans are what a finding points at, so an off-by-one is a mis-highlighted post."""
    tokens = tokenize_numerals(text)

    assert tokens
    for token in tokens:
        assert text[token.start:token.end] == token.text
        assert not token.text.startswith(" ")


@pytest.mark.parametrize("text, hedge", [
    ("approximately $27 million", "approximately"),
    ("about $27 million", "about"),
    ("roughly 5.2%", "roughly"),
    ("around 2,114 homes", "around"),
    ("nearly $6.6 billion", "nearly"),
    ("over 17,013 homes", "over"),
    ("more than 5.2%", "more than"),
    ("under 5.2%", "under"),
    ("~5.2%", "~"),
    ("we were in contract to purchase 2,114 homes", None),
    ("over the quarter, revenue of $525 million", None),
])
def test_a_hedge_is_recorded_only_where_it_runs_straight_into_the_numeral(text, hedge):
    """§13.1's hedge guard relaxes nothing; it records. `over` and `under` are ordinary words
    in this corpus (`under contract`), so proximity is the whole of the rule."""
    token = tokenize_numerals(text)[0]

    assert token.hedge == hedge


def test_a_parenthesis_negates_only_when_it_opens_on_the_numeral_itself():
    """Otherwise every parenthetical aside would flip the sign of the first number inside it."""
    negated, = tokenize_numerals("(27,075)")
    aside = tokenize_numerals("Adjusted EBITDA (a loss) was 27,075")[0]
    unbalanced = tokenize_numerals("(as of 2022")[0]

    assert negated.value == -27075.0 and negated.parenthesised is True
    assert aside.value == 27075.0 and aside.negative is False
    assert unbalanced.value == 2022.0 and unbalanced.negative is False


@pytest.mark.parametrize("surface, figures, dropped", [
    ("$27 million", 2, 0),
    ("$27.1 million", 3, 0),
    ("$27.0750 million", 6, 0),
    ("(27,075)", 5, 0),
    ("5.2%", 2, 0),
    ("0.4%", 1, 0),
    ("13.0%", 3, 0),
    ("1,200", 2, 2),
])
def test_significant_figures_are_counted_as_the_draft_wrote_them(surface, figures, dropped):
    """Leading zeros never count, trailing zeros after a decimal point always do, and trailing
    zeros of a bare integer are reported separately because only the writer knows whether
    `1,200` was meant to two figures or four."""
    token, = tokenize_numerals(surface)

    assert (token.significant_figures, token.trailing_zeros_excluded) == (figures, dropped)


def test_the_surface_supplies_a_unit_for_all_four_the_corpus_holds_and_none_for_a_bare_numeral():
    """§13.2's map is closed over surfaces. Two of the four units are the noun *after* the
    numeral rather than a suffix, and a bare numeral implies no unit at all."""
    units = [tokenize_numerals(surface)[0].unit
             for surface in ("$525 million", "5.2%", "2,114 homes", "51 markets", "(211)")]

    assert units == [SurfaceUnit.USD, SurfaceUnit.PERCENT, SurfaceUnit.HOMES,
                     SurfaceUnit.MARKETS, SurfaceUnit.NONE]
    # §13.2 refuses a currency symbol on a non-monetary unit, so the symbol is recorded in its
    # own right and not folded into the unit it usually implies.
    assert tokenize_numerals("$525 million")[0].currency_symbol is True
    assert tokenize_numerals("5.2%")[0].currency_symbol is False


# -- direction 2: a table quote back to its value (§13.7.1 Rule A step 2) ---------------------


@pytest.mark.parametrize("quote, scale, unit, value", [
    (ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE, "thousands", "USD", ADJUSTED_EBITDA_2020Q4),
    (ADJUSTED_EBITDA_2020Q4_MILLIONS_QUOTE, "millions", "USD",
     ADJUSTED_EBITDA_2020Q4_MILLIONS_VALUE),
    (ADJUSTED_EBITDA_2022Q3_QUOTE, "millions", "USD", ADJUSTED_EBITDA_2022Q3),
    (ADJUSTED_EBITDA_2023Q1_QUOTE, "millions", "USD", ADJUSTED_EBITDA_2023Q1),
    ("5.2", "units", "percent", MARGIN_2022Q2),
    ("(6.3)", "units", "percent", MARGIN_2022Q3),
    ("17,013", "units", "homes", 17013.0),
])
def test_the_table_quote_rule_rebuilds_the_corpus_value(quote, scale, unit, value):
    assert reconstruct_table_quote(quote, scale=scale, unit=unit) == value


def test_an_absent_scale_is_read_as_units_because_neo4j_holds_no_null():
    """C2: 84 `market_count` rows carry no `scale` property at all, and absent is the only
    reading the database can produce."""
    assert reconstruct_table_quote("51", scale=None, unit="markets") == 51.0


def test_the_percent_exception_survives_a_scale_that_would_otherwise_multiply():
    """Unexercised by the corpus and written anyway.

    All 1,163 percent table rows carry `scale: "units"`, so on every row in the package
    "multiply by 1" and "skip the multiply" agree. §13.7.1 states the rule with the exception,
    and the day a percent row arrives scaled, the rule should handle it rather than an
    accident of the multiplier being 1.
    """
    assert reconstruct_table_quote("5.2", scale="millions", unit="percent") == 5.2


def test_a_narrative_sentence_is_refused_rather_than_reconstructed():
    """Rule A is for table facts. The 14 narrative quotes are whole sentences carrying their
    own magnitude word, and applying `scale: millions` to one would be the 1e6 error."""
    with pytest.raises(UnreconstructableQuote):
        reconstruct_table_quote(FACILITY_PASSAGE, scale="millions", unit="USD")


def test_an_undeclared_scale_is_refused_rather_than_defaulted():
    with pytest.raises(UnreconstructableQuote):
        reconstruct_table_quote("(211)", scale="billions", unit="USD")


# -- direction 3: printed-precision half-ulp (§13.1) ------------------------------------------


@pytest.mark.parametrize("surface, fact, printed, accepted, over_precise, window, difference", [
    ("$27.1 million", ADJUSTED_EBITDA_2020Q4, ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE,
     True, False, 50000.0, 25000.0),
    ("$27 million", ADJUSTED_EBITDA_2020Q4, ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE,
     True, False, 500000.0, 75000.0),
    ("$27.0750 million", ADJUSTED_EBITDA_2020Q4, ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE,
     True, True, 50.0, 0.0),
    ("5.2%", MARGIN_2022Q2, "5.2", True, False, 0.05, 0.0),
    ("$28 million", ADJUSTED_EBITDA_2020Q4, ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE,
     False, False, 500000.0, 925000.0),
])
def test_the_plans_worked_tolerance_cases_land_where_it_says_they_do(
        surface, fact, printed, accepted, over_precise, window, difference):
    """Every row but the last is quoted from §13.1; the last is the nearest numeral that must
    fail, so the window is shown to have an edge rather than merely a value."""
    token, = tokenize_numerals(surface)

    verdict = compare_token_to_fact(token, fact, fact_printed_form=printed)

    assert verdict.window == window
    assert verdict.difference == difference
    assert verdict.within_window is accepted
    assert verdict.accepted is accepted
    assert verdict.over_precise is over_precise


def test_the_millions_table_quote_reconstructed_under_its_scale_matches_the_fact_exactly():
    """§13.1's `(341)` case: the magnitude is in the scale, not the surface, so the tolerance
    test is fed by `reconstruct_table_quote` and the window never has to absorb 1e6."""
    value = reconstruct_table_quote(ADJUSTED_EBITDA_2023Q1_QUOTE, scale="millions", unit="USD")

    verdict = compare_to_fact(
        value, ADJUSTED_EBITDA_2023Q1,
        draft_significant_figures=significant_figures(ADJUSTED_EBITDA_2023Q1_QUOTE),
        fact_printed_form=ADJUSTED_EBITDA_2023Q1_QUOTE,
    )

    assert verdict.difference == 0.0
    assert verdict.window == 500000.0
    assert verdict.accepted is True
    assert verdict.over_precise is False


def test_the_window_is_the_half_ulp_of_the_drafts_own_precision():
    """`0.5 x 10^(e-d+1)` walked over d, against the fact §13.1 walks it over."""
    windows = [tolerance_window(ADJUSTED_EBITDA_2020Q4, d) for d in (1, 2, 3, 6)]

    assert windows == [5000000.0, 500000.0, 50000.0, 50.0]


def test_over_precision_depends_on_which_printing_of_the_fact_it_is_judged_against():
    """The same fact is printed `(27,075)` and `(27)` in different tables, so `$27.1 million`
    is over-precise against one and not the other. A verifier that derived the printed form
    itself would be picking a printing and calling it the fact's."""
    token, = tokenize_numerals("$27.1 million")

    against_thousands = compare_token_to_fact(
        token, ADJUSTED_EBITDA_2020Q4,
        fact_printed_form=ADJUSTED_EBITDA_2020Q4_THOUSANDS_QUOTE)
    against_millions = compare_token_to_fact(
        token, ADJUSTED_EBITDA_2020Q4_MILLIONS_VALUE,
        fact_printed_form=ADJUSTED_EBITDA_2020Q4_MILLIONS_QUOTE)
    unstated = compare_token_to_fact(token, ADJUSTED_EBITDA_2020Q4)

    assert (against_thousands.fact_significant_figures, against_thousands.over_precise) \
        == (5, False)
    assert (against_millions.fact_significant_figures, against_millions.over_precise) == (2, True)
    assert (unstated.fact_significant_figures, unstated.over_precise) == (None, False)


def test_an_unsigned_numeral_is_a_claim_about_magnitude_and_a_signed_one_is_not():
    """§13.1's formula says `|V_draft - V_fact|` and §13.1's worked case computes
    `||V_draft| - |V_fact||` — 25,000 rather than 54,175,000. The worked case is the one a
    verifier can act on, because "a loss of $27.1 million" carries its sign in the words. So
    the sign the *numeral* carries is checked separately, and only when it is there."""
    unsigned, = tokenize_numerals("$27.1 million")
    signed, = tokenize_numerals("-$27.1 million")

    positive_draft = compare_token_to_fact(unsigned, ADJUSTED_EBITDA_2020Q4)
    negative_draft = compare_token_to_fact(signed, -ADJUSTED_EBITDA_2020Q4)

    assert positive_draft.sign_explicit is False
    assert positive_draft.sign_agrees is False
    assert positive_draft.accepted is True

    assert negative_draft.sign_explicit is True
    assert negative_draft.within_window is True
    assert negative_draft.accepted is False


# -- direction 4: percentage semantics (§13.3) ------------------------------------------------


def test_the_three_readings_of_the_same_margin_move_are_all_correct_and_all_different():
    """§13.3's worked quarter: `adjusted_ebitda_margin` 2.2 in 2Q21, 5.2 in 2Q22. Both
    "improved 3.0 percentage points" and "improved 136%" are true, and they are not the same
    claim."""
    assert matches_at_printed_precision(
        percent_delta(PercentOperation.DELTA_PP, MARGIN_2021Q2, MARGIN_2022Q2), 3.0, decimals=1)
    assert matches_at_printed_precision(
        percent_delta(PercentOperation.DELTA_BPS, MARGIN_2021Q2, MARGIN_2022Q2),
        300.0, decimals=0)
    assert matches_at_printed_precision(
        percent_delta(PercentOperation.DELTA_RELATIVE, MARGIN_2021Q2, MARGIN_2022Q2),
        136.4, decimals=1)
    assert delta_pp(MARGIN_2021Q2, MARGIN_2022Q2) \
        == percent_delta(PercentOperation.DELTA_PP, MARGIN_2021Q2, MARGIN_2022Q2)
    assert delta_bps(MARGIN_2021Q2, MARGIN_2022Q2) \
        == percent_delta(PercentOperation.DELTA_BPS, MARGIN_2021Q2, MARGIN_2022Q2)
    assert delta_relative(MARGIN_2021Q2, MARGIN_2022Q2) \
        == percent_delta(PercentOperation.DELTA_RELATIVE, MARGIN_2021Q2, MARGIN_2022Q2)


def test_the_two_readings_differ_by_one_hundred_over_the_earlier_value_not_by_nineteen():
    """§13.3 says the readings "differ by a factor of nineteen". The factor is exactly
    `100/|v1|`, and `v1` is the earlier value because that is what `delta_relative` divides
    by: on §13.3's own pair that is `100/2.2 = 45.5`, not `100/5.2 = 19.2`. The plan measured
    from the later value and so understates the gap it refuses."""
    relative = delta_relative(MARGIN_2021Q2, MARGIN_2022Q2)
    points = delta_pp(MARGIN_2021Q2, MARGIN_2022Q2)

    assert round(relative / points, 1) == 45.5
    assert round(100 / abs(MARGIN_2021Q2), 1) == 45.5
    assert round(100 / abs(MARGIN_2022Q2), 1) == 19.2


def test_a_recomputed_delta_is_compared_at_the_printed_precision_because_binary_is_not_exact():
    """Real corpus quarters, not a constructed example: `adjusted_gross_margin` 7.3 -> 9.9 is
    `2.6000000000000005` and 9.9 -> 13.2 is `3.299999999999999`. Equality would refuse both.

    (§13.1's stated example, `5.2 - 2.2`, is *exactly* 3.0 in IEEE-754 and would have proved
    nothing — the hazard is real, the instance quoted for it is not.)"""
    narrow = delta_pp(GROSS_MARGIN_2021Q4, GROSS_MARGIN_2022Q1)
    wide = delta_pp(GROSS_MARGIN_2022Q1, GROSS_MARGIN_2022Q2)
    rendered, = tokenize_numerals("2.6 percentage points")

    assert narrow != 2.6
    assert wide != 3.3
    # The precision to compare at comes off the draft's own numeral, not off a constant.
    assert rendered.decimals_written == 1
    assert matches_at_printed_precision(narrow, rendered.value, decimals=rendered.decimals_written)
    assert matches_at_printed_precision(wide, 3.3, decimals=1)


@pytest.mark.parametrize("earlier, later", [
    (MARGIN_2022Q2, MARGIN_2022Q3),
    (GROSS_MARGIN_2022Q3, GROSS_MARGIN_2022Q4),
    (0.0, MARGIN_2022Q2),
])
def test_relative_change_is_refused_across_zero_rather_than_returned_meaninglessly(
        earlier, later):
    """§13.3's third gate on real quarters: `adjusted_ebitda_margin` 5.2 -> -6.3 and
    `adjusted_gross_margin` 3.3 -> -3.2 both cross. `(-6.3 - 5.2)/|5.2| = -221%` is defined
    and says nothing anyone can read."""
    assert delta_pp(earlier, later) == pytest.approx(later - earlier)

    with pytest.raises(RelativeChangeAcrossZero):
        delta_relative(earlier, later)


@pytest.mark.parametrize("rendered, operation, supported", [
    ("improved 3.0 percentage points", PercentOperation.DELTA_PP, True),
    ("improved 3.0 pp", PercentOperation.DELTA_PP, True),
    ("improved 136%", PercentOperation.DELTA_PP, False),
    ("widened 300 bps", PercentOperation.DELTA_BPS, True),
    ("widened 300 basis points", PercentOperation.DELTA_BPS, True),
    ("widened 3.0 percentage points", PercentOperation.DELTA_BPS, False),
    ("improved 136% relative to 2Q21", PercentOperation.DELTA_RELATIVE, True),
    ("improved 136%", PercentOperation.DELTA_RELATIVE, False),
    ("improved relative to 2Q21", PercentOperation.DELTA_RELATIVE, False),
])
def test_the_rendering_has_to_carry_the_token_for_the_reading_the_draft_declared(
        rendered, operation, supported):
    """§13.3: the draft declares the operation, the verifier recomputes it, and nobody infers
    it. `delta_relative` needs both halves — a `%` numeral *and* a relative marker — because
    the `%` on its own is exactly what makes the sentence unresolvable."""
    assert surface_supports_operation(operation, rendered) is supported


def test_a_bare_percent_change_sentence_is_flagged_unresolvable():
    """*"Margin fell 3%"* — §13.3 names this one specifically. The finding carries the clause,
    the verb and the numeral span so the refusal can quote itself back."""
    finding, = find_ambiguous_percent_changes("Adjusted EBITDA margin fell 3% in 2Q22.")

    assert finding.verb == "fell"
    assert finding.numeral.text == "3%"
    assert finding.clause.startswith("Adjusted EBITDA margin fell 3%")
    assert finding.clause[finding.numeral.start:finding.numeral.end] == "3%"


def test_naming_the_reading_resolves_the_sentence_the_bare_percent_left_open():
    """The three ways out, all from §13.3's worked quarter."""
    assert find_ambiguous_percent_changes(
        "Adjusted EBITDA margin improved 3.0 percentage points.") == ()
    assert find_ambiguous_percent_changes("Adjusted EBITDA margin widened 300 bps.") == ()
    assert find_ambiguous_percent_changes(
        "Adjusted EBITDA margin improved 136% relative to 2Q21.") == ()


def test_a_sentence_stating_two_levels_is_not_a_change_sentence():
    """The real passage F3's quarter is built on. It states levels, carries no change verb,
    and must not be flagged — §13.3's gate is about change claims, and over-firing here would
    refuse the one sentence the filing actually made."""
    assert find_ambiguous_percent_changes(LEVELS_PASSAGE) == ()


def test_the_real_homes_under_contract_sentence_is_flagged_at_both_of_its_percentages():
    """Verbatim from `homes_under_contract`'s own evidence. Both `27%` and `109%` sit beside
    `up` with nothing saying which reading, and the metric's unit is `homes` — so neither
    number is a level of anything the sentence names."""
    findings = find_ambiguous_percent_changes(CONTRACTS_PASSAGE)

    assert [finding.numeral.text for finding in findings] == ["27%", "109%"]
    assert {finding.verb for finding in findings} == {"up"}
    for finding in findings:
        assert CONTRACTS_PASSAGE[finding.numeral.start:finding.numeral.end] \
            == finding.numeral.text


def test_a_decimal_point_never_splits_a_clause():
    """`5.2%` split on its own `.` would make the gate read two numbers where there is one."""
    finding, = find_ambiguous_percent_changes("The margin fell 5.2% this quarter.")

    assert finding.numeral.text == "5.2%"


# -- against the live graph -------------------------------------------------------------------


def live_settings():
    resolved = load_neo4j_settings()
    try:
        resolved.resolved_uri, resolved.resolved_database, resolved.auth
    except (StoryGraphUnconfiguredError, StoryGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    return resolved


@pytest.fixture()
def live_executor():
    """Read-only, and nothing is created or deleted. Skips in two stages, as
    `tests/story/test_story_neo4j_adapter.py` does: unconfigured, then unreachable."""
    executor = open_read_executor(live_settings())
    health = executor.verify_connectivity()
    if not health.ok:
        executor.close()
        pytest.skip(f"local Neo4j is unreachable ({health.status}: {health.detail}); "
                    "start it with `docker compose up -d`")
    with executor:
        yield executor


@pytest.mark.neo4j
def test_every_table_observation_in_the_graph_reconstructs_from_its_own_quote(live_executor):
    """§13.7.1 Rule A step 2, executed rather than quoted: 2,690/2,690.

    The quote comes off the **`EVIDENCED_BY` edge** (C3) — it is not a property of the
    observation, and a version of this test that read `o.quoted_text` would pass on zero rows
    while claiming to check every one. Failures are named individually rather than counted,
    because §13.7.1's number is only evidence if the exceptions have nowhere to hide.
    """
    rows = live_executor.read(TABLE_FACTS, {}, timeout_seconds=60.0)

    failures = []
    for row in rows:
        try:
            rebuilt = reconstruct_table_quote(
                row["quoted_text"], scale=row.get("scale"), unit=row["unit"])
        except UnreconstructableQuote as exc:
            failures.append(f"{row['observation_id']}: {exc}")
            continue
        if rebuilt != row["value"]:
            failures.append(
                f"{row['observation_id']}: {row['quoted_text']!r} scale={row.get('scale')!r} "
                f"unit={row['unit']!r} rebuilt {rebuilt} but the graph holds {row['value']}")

    assert failures == []
    assert len(rows) == TABLE_OBSERVATION_COUNT


@pytest.mark.neo4j
def test_every_narrative_observations_value_is_in_its_own_sentence_with_no_scale_applied(
        live_executor):
    """The other half of the scale rule, and the one that would break loudest if `scale` were
    treated as a multiplier.

    Four of the 14 narrative observations carry `scale: millions` beside a sentence that
    already says "million". Tokenising the sentence must yield the stored value; multiplying
    by the scale as well would yield 1e6 times it.
    """
    rows = live_executor.read(NARRATIVE_FACTS, {}, timeout_seconds=60.0)

    missing = [
        f"{row['observation_id']}: {row['value']} not among "
        f"{[token.value for token in tokenize_numerals(row['quoted_text'])]}"
        for row in rows
        if row["value"] not in {token.value for token in tokenize_numerals(row["quoted_text"])}
    ]

    assert missing == []
    assert len(rows) == NARRATIVE_OBSERVATION_COUNT
