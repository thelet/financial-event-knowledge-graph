"""S13 — what the derivation stage computes, and everything it refuses to compute.

Offline. No database, no model server, no fixture on disk: every package here is built from
values read out of `data/story_demo/story-v1-2405d9b03c5e/` and typed in, so the suite runs from
a clean checkout and the numbers are still the run's own.

**The case at the centre of this file is the one §2 measured.**
`cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d` moves
`adjusted_gross_profit` from `556,000,000.0` in 2022Q2 to `110,000,000.0` in 2022Q3, and its
candidate publishes `delta = -446000000.0`, `delta_pct = -80.215827338`, `direction = "decrease"`
and `crosses_zero = false`. The brief said `$446M` was *"rejected as `unbound_numeral`"*; §2
measured that it was not — the arithmetic recomputed cleanly and the refusal fired on the
literal `2022`, because the model left `Calculation.period_surface` empty. So the tests below
assert **both** halves: that the number is right, and that the period surface the writer could
forget is minted by code.

**Every test drives real objects.** `detector_config.quantity_direction` is passed through the
`DirectionOracle` seam unchanged rather than replaced by a stub — the seam exists because a
stage may not import a sibling stage, and a test that stubbed it would leave the one thing the
seam is for untested. The offer-set test executes *every* offer rather than sampling, because
§4.3's promise is about the whole list.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from story.core.keys import derived_fact_id, package_id, story_run_id
from story.core.models import (
    BudgetParameters,
    CausalLanguage,
    DerivationOperation,
    DerivationRequest,
    DerivedFact,
    DerivedFactKind,
    DisplaySemantics,
    EvidenceRole,
    PackageBudget,
    PackagedFact,
    PackagedPassage,
    RunSelection,
    TableCellRef,
)
# Names imported from the submodules directly, never `import story.stages.derivation.execute as
# …`: the package re-exports a *function* called `execute` and one called `offers`, and
# `import a.b as c` binds the package attribute, so that spelling would silently alias the
# function. `story/stages/derivation/__init__.py` records the same hazard for any other caller.
from story.stages.derivation import public
from story.stages.derivation.execute import (
    evidence_scope_facts,
    execute,
    execute_all,
)
from story.stages.derivation.offers import offers, validate
from story.stages.derivation.operations import CROSSED, period_surface_hint, round_delta
from story.stages.derivation.public import (
    DerivationRefusal,
    DerivationRefusalCode,
    TOOL_VERSION,
)
from story.stages.detection import detector_config
from story.stages.verification import period_grammar

from conftest import make_candidate, make_package

DIRECTION = detector_config.quantity_direction

#: The two 2022 quarters and their dates, as the graph stores them.
QUARTERS = {
    "2022Q1": ("2022-01-01", "2022-03-31"),
    "2022Q2": ("2022-04-01", "2022-06-30"),
    "2022Q3": ("2022-07-01", "2022-09-30"),
}

AGP_Q2 = "obs:adjusted-gross-profit:opendoor:2022Q2:normalized-table:0c4364ebbc44"
AGP_Q3 = "obs:adjusted-gross-profit:opendoor:2022Q3:normalized-table:4d66ef7200e9"


def fact(
    observation_id: str,
    metric_id: str,
    label: str,
    period_key: str,
    value: float,
    *,
    unit: str = "USD",
    currency: str | None = "USD",
    scale: str | None = "millions",
    row: int = 3,
) -> PackagedFact:
    """One canonical table reading, with the grid coordinates a real package carries.

    `row` differs per fact because `StoryEvidencePackage` refuses two facts that mint one
    evidence handle, and a handle is `ev:<passage>:r<row>c<column>`. Two facts sharing a cell is
    a defect and not a naming clash, so the fixture gives each its own row rather than dropping
    the coordinates.
    """
    start, end = QUARTERS[period_key]
    return PackagedFact(
        observation_id=observation_id,
        metric_id=metric_id,
        metric_label=label,
        period_key=period_key,
        period_start=start,
        period_end=end,
        shape="quarter",
        value=value,
        unit=unit,
        currency=currency,
        scale=scale,
        source_lane="normalized_table",
        validation_state="ok",
        passage_id="psg:1",
        document_id="doc:1",
        quoted_text=str(value),
        cell=TableCellRef(row_index=row, value_column_index=2,
                          period_header_row_index=0, period_header_column_index=2),
    )


def agp_facts() -> tuple[PackagedFact, PackagedFact]:
    return (
        fact(AGP_Q2, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q2",
             556_000_000.0, row=3),
        fact(AGP_Q3, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q3",
             110_000_000.0, row=4),
    )


def agp_package(**overrides):
    return make_package(facts=agp_facts(), **overrides)


def agp_candidate(**signal_overrides):
    """§2's candidate, with the four signals `candidate.json` actually records."""
    signals = {
        "crosses_zero": False,
        "delta": -446_000_000.0,
        "delta_pct": -80.215827338,
        "direction": "decrease",
        "period_shape": "quarter",
        "polarity": "revenue",
    }
    signals.update(signal_overrides)
    return make_candidate(
        candidate_id="cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d",
        metric_ids=("adjusted_gross_profit",),
        anchor_period_keys=("2022Q2", "2022Q3"),
        anchor_observation_ids=(AGP_Q2, AGP_Q3),
        signals=signals,
    )


def one(package, candidate, operation, from_id, to_id, **kwargs):
    """Execute a single request against the package's own offer set."""
    request = DerivationRequest(operation=operation, from_fact_id=from_id, to_fact_id=to_id)
    return execute(
        request, package, candidate,
        direction=DIRECTION,
        offered=offers(package, candidate),
        **kwargs)


# ---------------------------------------------------------------------------------------
# The case §2 measured
# ---------------------------------------------------------------------------------------


def test_the_446_million_the_demo_could_not_bind_is_computed_with_its_sign_and_its_word():
    """556M → 110M is exactly −446,000,000.0, and code — not the model — says "decreased"."""
    package, candidate = agp_package(), agp_candidate()

    derived = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)

    assert isinstance(derived, DerivedFact)
    assert derived.result == -446_000_000.0
    assert derived.display_semantics is DisplaySemantics.DECREASED_BY
    assert derived.unit == "USD"
    assert derived.currency == "USD"
    assert derived.from_value == 556_000_000.0
    assert derived.to_value == 110_000_000.0
    assert derived.from_period == "2022Q2"
    assert derived.to_period == "2022Q3"
    assert derived.fact_kind is DerivedFactKind.DERIVED
    assert derived.tool_version == TOOL_VERSION


def test_the_period_surface_the_writer_forgot_is_minted_by_code_and_names_the_right_quarter():
    """§2's actual failure: `Calculation.period_surface` was empty while the prose said 2022Q3.

    Asserted through the verifier's own grammar rather than by string comparison — a hint the
    verifier cannot parse would look like a period surface and refuse as an unresolvable one,
    which is a worse failure than no hint at all.
    """
    derived = one(agp_package(), agp_candidate(),
                  DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)

    assert derived.period_surface_hint == "the third quarter of 2022"
    resolved = period_grammar.resolve(derived.period_surface_hint)
    assert resolved.resolved
    assert resolved.key == derived.to_period == "2022Q3"


@pytest.mark.parametrize("period_key", sorted(QUARTERS))
def test_every_quarter_hint_this_stage_mints_resolves_back_through_the_grammar(period_key):
    from story.core.periods import story_period

    start, end = QUARTERS[period_key]
    hint = period_surface_hint(story_period(start, end, None))

    assert period_grammar.resolve(hint).key == period_key


def test_reversing_the_two_periods_flips_the_sign_the_word_and_the_id():
    """A reversed request is a different derivation, not the same one read backwards.

    The id has to say so, which is why the digest labels its two operands `from=` and `to=`
    rather than sorting them — see `keys.derived_fact_id`, which records that §4.4's own
    "sorted input fact ids" mints one digest for both orientations.
    """
    package, candidate = agp_package(), agp_candidate()

    forward = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)
    backward = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE, AGP_Q3, AGP_Q2)

    assert forward.result == -446_000_000.0
    assert backward.result == 446_000_000.0
    assert forward.display_semantics is DisplaySemantics.DECREASED_BY
    assert backward.display_semantics is DisplaySemantics.INCREASED_BY
    assert forward.fact_id != backward.fact_id
    assert forward.fact_id.split(":")[-1] != backward.fact_id.split(":")[-1]


def test_the_relative_change_is_the_detectors_own_number_to_the_ninth_decimal():
    derived = one(agp_package(), agp_candidate(),
                  DerivationOperation.PERCENTAGE_CHANGE, AGP_Q2, AGP_Q3)

    assert derived.result == -80.215827338
    assert derived.unit == "percent"
    assert derived.currency is None, "a relative change is dimensionless; USD must not survive"
    assert derived.reused_detector_signal == "delta_pct"


# ---------------------------------------------------------------------------------------
# §4.2 — every refusal, and none of them falling through to a value
# ---------------------------------------------------------------------------------------


def test_percentage_change_refuses_a_step_across_zero_and_names_the_gate():
    """§13.3's third gate. `adjusted_ebitda` really does cross zero between these two quarters."""
    facts = (
        fact("obs:adjusted-ebitda:2022Q2", "adjusted_ebitda", "Adjusted EBITDA", "2022Q2",
             218_000_000.0, row=5),
        fact("obs:adjusted-ebitda:2022Q3", "adjusted_ebitda", "Adjusted EBITDA", "2022Q3",
             -211_000_000.0, row=6),
    )
    package = make_package(facts=facts)
    candidate = make_candidate(metric_ids=("adjusted_ebitda",),
                               anchor_period_keys=("2022Q2", "2022Q3"), signals={})

    refusal = one(package, candidate, DerivationOperation.PERCENTAGE_CHANGE,
                  facts[0].observation_id, facts[1].observation_id)

    assert isinstance(refusal, DerivationRefusal)
    assert refusal.code is DerivationRefusalCode.RELATIVE_CHANGE_ACROSS_ZERO
    assert not refusal
    assert "opposite sides" in refusal.detail

    crossed = one(package, candidate, DerivationOperation.CROSSED_ZERO,
                  facts[0].observation_id, facts[1].observation_id)
    assert crossed.result_word == CROSSED
    assert crossed.display_semantics is DisplaySemantics.CROSSED_ZERO


def test_percent_and_percentage_point_are_two_operations_each_refusing_the_others_unit():
    """§4.1's separation, made unrepresentable rather than checked afterwards.

    A USD input refuses `percentage_point_change`; a percent input refuses `percentage_change`
    **and** `absolute_change` — the second is this implementation's correction to §4.1's
    "input unit" column, recorded in `offers.ADMITTED_UNITS`.
    """
    usd_package, usd_candidate = agp_package(), agp_candidate()
    on_dollars = one(usd_package, usd_candidate,
                     DerivationOperation.PERCENTAGE_POINT_CHANGE, AGP_Q2, AGP_Q3)

    assert on_dollars.code is DerivationRefusalCode.UNIT_NOT_ADMITTED
    assert "'USD'" in on_dollars.detail

    margins = (
        fact("obs:agm:2022Q2", "adjusted_gross_margin", "Adjusted Gross Margin", "2022Q2",
             13.2, unit="percent", currency=None, scale="units", row=7),
        fact("obs:agm:2022Q3", "adjusted_gross_margin", "Adjusted Gross Margin", "2022Q3",
             3.3, unit="percent", currency=None, scale="units", row=8),
    )
    percent_package = make_package(facts=margins)
    percent_candidate = make_candidate(metric_ids=("adjusted_gross_margin",),
                                       anchor_period_keys=("2022Q2", "2022Q3"), signals={})

    for refused in (DerivationOperation.PERCENTAGE_CHANGE, DerivationOperation.ABSOLUTE_CHANGE):
        answer = one(percent_package, percent_candidate, refused,
                     margins[0].observation_id, margins[1].observation_id)
        assert answer.code is DerivationRefusalCode.UNIT_NOT_ADMITTED, refused

    points = one(percent_package, percent_candidate,
                 DerivationOperation.PERCENTAGE_POINT_CHANGE,
                 margins[0].observation_id, margins[1].observation_id)
    assert points.result == -9.9, "13.2 - 3.3 is 9.899999999999999 raw; DELTA_PRECISION rounds it"
    assert points.unit == "percentage_points"
    assert points.currency is None


def test_incomparable_inputs_are_refused_with_the_rule_that_refused_them():
    """R2 on two metrics, R10 on a step across a hole. Both name the rule and both operands."""
    package = make_package(facts=(
        fact(AGP_Q3, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q3",
             110_000_000.0, row=3),
        fact("obs:revenue:2022Q3", "revenue", "Revenue", "2022Q3", 3_400_000_000.0, row=4),
    ))
    candidate = make_candidate(metric_ids=("adjusted_gross_profit", "revenue"),
                               anchor_period_keys=("2022Q3",), signals={})

    cross_metric = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE,
                       AGP_Q3, "obs:revenue:2022Q3")
    assert cross_metric.code is DerivationRefusalCode.PERIOD_ALIGNMENT, (
        "a two-period operation over one period is refused on shape before R2 is asked")

    gapped = make_package(facts=(
        fact("obs:agp:2022Q1", "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q1",
             800_000_000.0, row=3),
        fact(AGP_Q3, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q3",
             110_000_000.0, row=4),
    ))
    refusal = one(gapped, agp_candidate(), DerivationOperation.ABSOLUTE_CHANGE,
                  "obs:agp:2022Q1", AGP_Q3)

    assert refusal.code is DerivationRefusalCode.INPUTS_INCOMPARABLE
    assert refusal.rule_id == "R10"
    assert "6 months apart" in refusal.detail


def test_two_different_metrics_are_refused_by_r2_when_the_claim_is_a_movement():
    """The cross-metric refusal itself, on a pair whose periods do line up for a step claim."""
    package = make_package(facts=(
        fact(AGP_Q2, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q2",
             556_000_000.0, row=3),
        fact("obs:revenue:2022Q3", "revenue", "Revenue", "2022Q3", 3_400_000_000.0, row=4),
    ))
    candidate = make_candidate(metric_ids=("adjusted_gross_profit", "revenue"),
                               anchor_period_keys=("2022Q2", "2022Q3"), signals={})

    refusal = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE,
                  AGP_Q2, "obs:revenue:2022Q3")

    assert refusal.code is DerivationRefusalCode.INPUTS_INCOMPARABLE
    assert refusal.rule_id == "R2"
    assert "adjusted_gross_profit" in refusal.detail and "revenue" in refusal.detail


def test_a_move_inside_the_presentation_tolerance_is_refused_as_rounding_rather_than_news():
    """R8, reached through this stage. A USD figure filed in millions carries ±$1M."""
    facts = (
        fact("obs:hc:2022Q2", "holding_costs", "Holding Costs", "2022Q2", -5_000_000.0, row=3),
        fact("obs:hc:2022Q3", "holding_costs", "Holding Costs", "2022Q3", -5_400_000.0, row=4),
    )
    package = make_package(facts=facts)
    candidate = make_candidate(metric_ids=("holding_costs",),
                               anchor_period_keys=("2022Q2", "2022Q3"), signals={})

    refusal = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE,
                  "obs:hc:2022Q2", "obs:hc:2022Q3")

    assert refusal.code is DerivationRefusalCode.INPUTS_INCOMPARABLE
    assert refusal.rule_id == "R8"


def test_an_operation_outside_the_seven_is_refused_rather_than_attempted():
    """Unreachable through the enum, reachable through a replayed artifact (§14).

    `model_construct` is the shape a stored plan arrives in when an operation was retired
    between two runs, and it is the only way to reach this clause — which is exactly why the
    clause is here and not left to the enum.
    """
    package, candidate = agp_package(), agp_candidate()
    request = DerivationRequest.model_construct(
        operation="median", from_fact_id=AGP_Q2, to_fact_id=AGP_Q3)

    refusal = validate(request, package, candidate)

    assert refusal.code is DerivationRefusalCode.OPERATION_NOT_SUPPORTED
    assert refusal.operation == "median"


def test_a_fact_id_that_resolves_nowhere_in_the_package_is_refused():
    package, candidate = agp_package(), agp_candidate()
    request = DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                                from_fact_id=AGP_Q2, to_fact_id="obs:invented:2023Q1")

    refusal = validate(request, package, candidate)

    assert refusal.code is DerivationRefusalCode.INPUT_NOT_IN_PACKAGE
    assert "to_fact_id" in refusal.detail and "from_fact_id" not in refusal.detail


def test_one_fact_given_as_both_operands_is_refused():
    package, candidate = agp_package(), agp_candidate()
    request = DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                                from_fact_id=AGP_Q2, to_fact_id=AGP_Q2)

    assert validate(request, package, candidate).code is (
        DerivationRefusalCode.INPUTS_IDENTICAL)


def test_the_same_period_operations_and_the_two_period_operations_refuse_each_others_shape():
    """§4.1's period constraints, both directions."""
    package, candidate = agp_package(), agp_candidate()

    across_periods = one(package, candidate, DerivationOperation.RATIO, AGP_Q2, AGP_Q3)
    assert across_periods.code is DerivationRefusalCode.PERIOD_ALIGNMENT
    assert "one period" in across_periods.detail

    one_period_facts = (
        fact("obs:revenue:2022Q3", "revenue", "Revenue", "2022Q3", 3_400_000_000.0, row=3),
        fact(AGP_Q3, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q3",
             110_000_000.0, row=4),
    )
    one_period = make_package(facts=one_period_facts)
    divergence = make_candidate(metric_ids=("adjusted_gross_profit", "revenue"),
                               anchor_period_keys=("2022Q3",), signals={})

    a_step = one(one_period, divergence, DerivationOperation.ABSOLUTE_CHANGE,
                 "obs:revenue:2022Q3", AGP_Q3)
    assert a_step.code is DerivationRefusalCode.PERIOD_ALIGNMENT
    assert "step between two periods" in a_step.detail


def test_a_ratio_divides_the_unit_out_and_keeps_the_pair_the_ontology_relates():
    """`adjusted_gross_profit / revenue` — co-components under `adjusted_gross_margin`."""
    facts = (
        fact("obs:revenue:2022Q3", "revenue", "Revenue", "2022Q3", 3_400_000_000.0, row=3),
        fact(AGP_Q3, "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q3",
             110_000_000.0, row=4),
    )
    package = make_package(facts=facts)
    candidate = make_candidate(metric_ids=("adjusted_gross_profit", "revenue"),
                               anchor_period_keys=("2022Q3",), signals={})

    derived = one(package, candidate, DerivationOperation.RATIO,
                  "obs:revenue:2022Q3", AGP_Q3)

    assert isinstance(derived, DerivedFact)
    assert derived.result == round(110_000_000.0 / 3_400_000_000.0, 9)
    assert derived.unit == "multiple"
    assert derived.currency is None
    assert derived.display_semantics is DisplaySemantics.TIMES


# ---------------------------------------------------------------------------------------
# §4.4 — the id
# ---------------------------------------------------------------------------------------


def test_the_id_is_stable_across_two_constructions_of_one_derivation():
    first = one(agp_package(), agp_candidate(),
                DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)
    second = one(agp_package(), agp_candidate(),
                 DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)

    assert first.fact_id == second.fact_id
    assert first == second, "two runs over one package must produce one row, byte for byte"


def test_the_id_carries_readable_segments_and_a_digest_that_moves_with_every_input():
    """§4.4's shape, and the rule `keys.py` opens with: segments read, the digest separates."""
    base = dict(
        operation="absolute_change",
        subject_entity_id="opendoor",
        metric_ids=("adjusted_gross_profit",),
        period_keys=("2022Q2", "2022Q3"),
        from_fact_id=AGP_Q2,
        to_fact_id=AGP_Q3,
        package_id="pkg:metric-move-adjusted-gross-profit:0123456789ab",
        tool_version=TOOL_VERSION,
    )
    minted = derived_fact_id(**base)
    head, digest = minted.rsplit(":", 1)

    assert head == ("fact:derived:absolute-change:opendoor:adjusted-gross-profit:2022Q2-2022Q3")
    assert len(digest) == 12

    for field, replacement in (
        ("operation", "percentage_change"),
        ("from_fact_id", AGP_Q3),
        ("to_fact_id", AGP_Q2),
        ("package_id", "pkg:metric-move-adjusted-gross-profit:ffffffffffff"),
        ("tool_version", "2.0.0"),
    ):
        moved = derived_fact_id(**{**base, field: replacement})
        assert moved.rsplit(":", 1)[1] != digest, f"{field} is a digest input and did not move it"

    # A segment-only change must *not* be silently absorbed either — it changes the whole id.
    assert derived_fact_id(**{**base, "subject_entity_id": "zillow"}) != minted


def test_an_id_may_not_be_minted_from_a_blank_part():
    from extraction.core.identifiers import EmptyIdentityError

    base = dict(
        operation="absolute_change", subject_entity_id="opendoor",
        metric_ids=("adjusted_gross_profit",), period_keys=("2022Q3",),
        from_fact_id=AGP_Q2, to_fact_id=AGP_Q3,
        package_id="pkg:x:0123456789ab", tool_version=TOOL_VERSION)

    for blanked in ("operation", "subject_entity_id", "package_id", "tool_version",
                    "from_fact_id", "to_fact_id"):
        with pytest.raises(EmptyIdentityError):
            derived_fact_id(**{**base, blanked: "  "})
    with pytest.raises(EmptyIdentityError):
        derived_fact_id(**{**base, "metric_ids": ()})
    with pytest.raises(EmptyIdentityError):
        derived_fact_id(**{**base, "period_keys": ()})


# ---------------------------------------------------------------------------------------
# §7 of the brief — the detector's own answer, asserted against
# ---------------------------------------------------------------------------------------


def test_the_detector_signal_is_asserted_against_and_the_fact_records_which_one():
    package, candidate = agp_package(), agp_candidate()

    reused = {
        operation: one(package, candidate, operation, AGP_Q2, AGP_Q3).reused_detector_signal
        for operation in (DerivationOperation.ABSOLUTE_CHANGE,
                          DerivationOperation.PERCENTAGE_CHANGE,
                          DerivationOperation.CROSSED_ZERO,
                          DerivationOperation.TREND_DIRECTION)
    }

    assert reused == {
        DerivationOperation.ABSOLUTE_CHANGE: "delta",
        DerivationOperation.PERCENTAGE_CHANGE: "delta_pct",
        DerivationOperation.CROSSED_ZERO: "crosses_zero",
        DerivationOperation.TREND_DIRECTION: "direction",
    }


@pytest.mark.parametrize(
    ("signal", "planted", "operation"),
    [
        ("delta", -445_000_000.0, DerivationOperation.ABSOLUTE_CHANGE),
        ("delta_pct", -80.0, DerivationOperation.PERCENTAGE_CHANGE),
        ("crosses_zero", True, DerivationOperation.CROSSED_ZERO),
        ("direction", "increase", DerivationOperation.TREND_DIRECTION),
    ],
)
def test_a_planted_disagreement_with_the_detector_is_a_refusal_and_not_a_preference(
    signal, planted, operation
):
    """Two code paths computing one number and differing is a defect in one of them."""
    package = agp_package()
    candidate = agp_candidate(**{signal: planted})

    refusal = one(package, candidate, operation, AGP_Q2, AGP_Q3)

    assert isinstance(refusal, DerivationRefusal), f"{signal} disagreement was not refused"
    assert refusal.code is DerivationRefusalCode.DETECTOR_SIGNAL_DISAGREES
    assert refusal.code.value == "derived_result_mismatch", (
        "the stage and §6's GATE table must spell this one fault one way")
    assert repr(planted) in refusal.detail or str(planted) in refusal.detail


def test_the_reversed_orientation_records_no_signal_rather_than_negating_the_detectors():
    """`delta` is `later − earlier`; `delta_pct` does not negate symmetrically at all."""
    backward = one(agp_package(), agp_candidate(),
                   DerivationOperation.PERCENTAGE_CHANGE, AGP_Q3, AGP_Q2)

    assert backward.result == round((556_000_000.0 - 110_000_000.0) / 110_000_000.0 * 100, 9)
    assert backward.reused_detector_signal == ""


def test_a_signal_for_another_pair_of_quarters_is_not_asserted_against_this_one():
    """A package can carry more facts of a metric than the candidate anchored on."""
    package = make_package(facts=(
        fact("obs:agp:2022Q1", "adjusted_gross_profit", "Adjusted Gross Profit", "2022Q1",
             800_000_000.0, row=2),
        *agp_facts(),
    ))

    derived = one(package, agp_candidate(), DerivationOperation.ABSOLUTE_CHANGE,
                  "obs:agp:2022Q1", AGP_Q2)

    assert isinstance(derived, DerivedFact)
    assert derived.result == -244_000_000.0
    assert derived.reused_detector_signal == "", (
        "the candidate's delta is 2022Q2->2022Q3 and says nothing about 2022Q1->2022Q2")


def test_the_divergence_gap_is_the_candidates_own_signal_and_its_endpoints_are_checked():
    """`cross_metric_divergence` publishes `left_value`, `right_value` and `gap` — all three."""
    left = fact("obs:agm:2022Q3", "adjusted_gross_margin", "Adjusted Gross Margin", "2022Q3",
                3.3, unit="percent", currency=None, scale="units", row=3)
    right = fact("obs:ggm:2022Q3", "gaap_gross_margin", "Gross Margin", "2022Q3",
                 -12.6, unit="percent", currency=None, scale="units", row=4)
    package = make_package(facts=(left, right))
    signals = {
        "gap": 15.899999999999999,
        "left_metric_id": "adjusted_gross_margin",
        "right_metric_id": "gaap_gross_margin",
        "left_value": 3.3,
        "right_value": -12.6,
    }
    candidate = make_candidate(
        story_type="cross_metric_divergence",
        metric_ids=("adjusted_gross_margin", "gaap_gross_margin"),
        anchor_period_keys=("2022Q3",), signals=signals)

    derived = one(package, candidate, DerivationOperation.COMPARE_LEVELS,
                  right.observation_id, left.observation_id)

    assert isinstance(derived, DerivedFact)
    assert derived.result == 15.9, "the detector publishes the raw 15.899999999999999"
    assert derived.unit == "percentage_points", (
        "R8's defect D: a gap between two percent levels is in points and in nothing else")
    assert derived.reused_detector_signal == "gap"
    assert derived.display_semantics is DisplaySemantics.HIGHER_THAN
    assert derived.metric_id == "adjusted_gross_margin"
    assert derived.from_metric_id == "gaap_gross_margin"
    assert derived.metric_surfaces == ("Gross Margin", "Adjusted Gross Margin")

    moved = make_candidate(
        story_type="cross_metric_divergence",
        metric_ids=("adjusted_gross_margin", "gaap_gross_margin"),
        anchor_period_keys=("2022Q3",), signals={**signals, "left_value": 4.4})
    refusal = one(package, moved, DerivationOperation.COMPARE_LEVELS,
                  right.observation_id, left.observation_id)
    assert refusal.code is DerivationRefusalCode.DETECTOR_SIGNAL_DISAGREES
    assert "different canonical readings" in refusal.detail


# ---------------------------------------------------------------------------------------
# Sign convention — the thing a negative delta does not tell you
# ---------------------------------------------------------------------------------------


def test_a_cost_stored_negative_reads_as_a_rise_and_not_as_a_fall():
    """`holding_costs` is stored negative on 15 of 15 canonical values.

    The delta is `−3,000,000`, and the underlying cost went **up**. Anything that read the word
    off the sign of the number would print the opposite of what happened.
    """
    facts = (
        fact("obs:hc:2022Q2", "holding_costs", "Holding Costs", "2022Q2", -5_000_000.0, row=3),
        fact("obs:hc:2022Q3", "holding_costs", "Holding Costs", "2022Q3", -8_000_000.0, row=4),
    )
    package = make_package(facts=facts)
    candidate = make_candidate(metric_ids=("holding_costs",),
                               anchor_period_keys=("2022Q2", "2022Q3"), signals={})

    derived = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE,
                  "obs:hc:2022Q2", "obs:hc:2022Q3")

    assert derived.result == -3_000_000.0
    assert derived.display_semantics is DisplaySemantics.INCREASED_BY
    assert derived.warning_codes == ()


def test_an_unmeasured_sign_convention_produces_no_direction_word_and_says_so():
    """`cost_of_revenue` has zero observations in this run, so its convention is unverified.

    `detector_config.value_sign_of` returns `UNVERIFIED` and `quantity_direction` returns `None`
    rather than defaulting into the common case; this is what that `None` becomes downstream.
    """
    assert detector_config.value_sign_of("cost_of_revenue") is detector_config.ValueSign.UNVERIFIED

    facts = (
        fact("obs:cor:2022Q2", "cost_of_revenue", "Cost of Revenue", "2022Q2",
             3_100_000_000.0, row=3),
        fact("obs:cor:2022Q3", "cost_of_revenue", "Cost of Revenue", "2022Q3",
             3_400_000_000.0, row=4),
    )
    package = make_package(facts=facts)
    candidate = make_candidate(metric_ids=("cost_of_revenue",),
                               anchor_period_keys=("2022Q2", "2022Q3"), signals={})

    derived = one(package, candidate, DerivationOperation.ABSOLUTE_CHANGE,
                  "obs:cor:2022Q2", "obs:cor:2022Q3")

    assert derived.result == 300_000_000.0
    assert derived.display_semantics is DisplaySemantics.DIRECTION_UNVERIFIABLE
    assert derived.warning_codes == (detector_config.SIGN_CONVENTION_UNVERIFIED,)


def test_the_constants_restated_from_the_detection_stage_have_not_drifted():
    """A stage may not import a sibling stage; what is restated is pinned instead.

    The precedent is `story/stages/ranking/metric_history.py`, which restates
    `MIN_DELTA_POPULATION` and asserts it against the detector's own. `VALUE_SIGN`'s 26 rows are
    deliberately *not* restated — `public.DirectionOracle` records why the oracle is injected
    instead — so what is pinned here is the small vocabulary the oracle answers in.
    """
    assert public.DIRECTION_INCREASE == detector_config.DIRECTION_INCREASE
    assert public.DIRECTION_DECREASE == detector_config.DIRECTION_DECREASE
    assert public.DIRECTION_UNCHANGED == detector_config.DIRECTION_UNCHANGED
    assert public.SIGN_CONVENTION_UNVERIFIED == detector_config.SIGN_CONVENTION_UNVERIFIED

    for value in (9.899999999999999, -2.999999999999999, 0.10000000000000053, 446_000_000.0):
        assert round_delta(value) == detector_config.round_delta(value)


def test_the_real_quantity_direction_satisfies_the_oracle_seam():
    """Driven through the protocol, not asserted against it with `isinstance`."""
    oracle: object = DIRECTION
    assert oracle(  # type: ignore[operator]
        "holding_costs", -3_000_000.0) == detector_config.DIRECTION_INCREASE
    assert oracle("adjusted_gross_profit", -446_000_000.0) == detector_config.DIRECTION_DECREASE
    assert oracle("cost_of_revenue", 1.0) is None


# ---------------------------------------------------------------------------------------
# §4.3 — the offer set
# ---------------------------------------------------------------------------------------


def test_the_offer_set_is_deterministic_and_holds_no_triple_validation_would_refuse():
    """Asserted by *executing* every offer, which is the only way to prove the two agree."""
    package, candidate = agp_package(), agp_candidate()

    offered = offers(package, candidate)
    again = offers(agp_package(), agp_candidate())

    assert offered == again
    assert offered == tuple(sorted(
        offered, key=lambda r: (list(DerivationOperation).index(r.operation),
                                r.from_fact_id, r.to_fact_id)))
    assert offered

    for request in offered:
        answer = execute(request, package, candidate,
                                   direction=DIRECTION, offered=offered)
        assert isinstance(answer, DerivedFact), (
            f"{request.operation.value} was offered and then refused: "
            f"{getattr(answer, 'code', None)} {getattr(answer, 'detail', '')}")


def test_the_offer_set_is_exactly_the_operations_this_package_can_support():
    """Four of the seven, and each absence is a rule rather than an oversight."""
    offered = offers(agp_package(), agp_candidate())

    assert {request.operation for request in offered} == {
        DerivationOperation.ABSOLUTE_CHANGE,
        DerivationOperation.PERCENTAGE_CHANGE,
        DerivationOperation.CROSSED_ZERO,
        DerivationOperation.TREND_DIRECTION,
    }
    # Both orientations of the one pair, for each of the four.
    assert len(offered) == 8


def test_a_valid_triple_that_was_not_offered_is_refused_rather_than_executed():
    package, candidate = agp_package(), agp_candidate()
    request = DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                                from_fact_id=AGP_Q2, to_fact_id=AGP_Q3)

    refusal = execute(request, package, candidate, direction=DIRECTION, offered=())

    assert refusal.code is DerivationRefusalCode.NOT_OFFERED
    assert refusal.code.value == "derivation_not_offered"


def test_the_offer_set_is_capped_by_the_budget_parameter_and_the_cap_is_the_packages_own():
    package = agp_package(budget=PackageBudget(
        artifact_token_estimate=536, prompt_token_estimate=402,
        section_counts={"facts": 2}, parameters=BudgetParameters(max_derivations=3)))

    offered = offers(package, agp_candidate())

    assert len(offered) == 3
    assert offered == offers(agp_package(), agp_candidate())[:3]


def test_max_derivations_reaches_both_digests():
    """§4.3 puts the cap on `BudgetParameters` for exactly this, and it moves two ids.

    Recorded plainly because it is a cost, not a free win: every `package_id` and every
    `story_run_id` minted before this field existed is a different string now. That is correct —
    a package a planner may request derivations against is not the package that came before it —
    and it is the same event `PACKAGE_VERSION`'s own comment records for S1 and S3.
    """
    package_arguments = dict(
        candidate_id="cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d",
        package_version="1.3.0",
        graph_run_id="graph-v1-0483dc6b4b10",
        run_complete_sha256="1cc8f7b01c040531" + "0" * 48,
        ontology_definition_hash="bb94f522ba122470" + "0" * 48,
        fact_ids=(AGP_Q2, AGP_Q3),
    )
    assert (package_id(**package_arguments, budget=BudgetParameters())
            != package_id(**package_arguments, budget=BudgetParameters(max_derivations=4)))

    run_arguments = dict(
        graph_run_id="graph-v1-0483dc6b4b10",
        run_complete_sha256="1cc8f7b01c040531" + "0" * 48,
        ontology_definition_hash="bb94f522ba122470" + "0" * 48,
        config_hash="c" * 64, prompt_version="1.0.0", provider_id="llamacpp",
        model_id="qwen", provider_model_id="/models/qwen.gguf",
        temperature=0.0, max_tokens=1024,
        schema_digests={"plan": "a" * 12}, detector_versions={"metric_move": "1.0.0"},
        policy_version="canon-policy:1.0.0", ranking_policy_version="1.0.0",
        selection=RunSelection(),
    )
    assert (story_run_id(**run_arguments, budget=BudgetParameters())
            != story_run_id(**run_arguments, budget=BudgetParameters(max_derivations=4)))

    assert "max_derivations=12" in BudgetParameters().digest_parts()


# ---------------------------------------------------------------------------------------
# §3 and §6 — what a derived fact structurally cannot be
# ---------------------------------------------------------------------------------------


def test_a_derived_fact_can_never_carry_a_citation_chain():
    """§3: a derived fact has no passage and no evidence source; §6: no handle is minted for one.

    `extra="forbid"` is what makes this a construction error rather than a convention.
    """
    assert not set(DerivedFact.model_fields) & {
        "passage_id", "evidence_source_id", "evidence_handle", "quoted_text", "cell",
        "citations", "document_id", "source_url"}

    derived = one(agp_package(), agp_candidate(),
                  DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)
    for field in ("passage_id", "evidence_source_id", "evidence_handle"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            DerivedFact(**{**derived.model_dump(), field: "psg:1"})


def test_a_derived_fact_carries_a_scalar_or_a_word_and_never_both_or_neither():
    derived = one(agp_package(), agp_candidate(),
                  DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)
    payload = derived.model_dump()

    with pytest.raises(ValidationError, match="exactly one"):
        DerivedFact(**{**payload, "result_word": "decrease"})
    with pytest.raises(ValidationError, match="exactly one"):
        DerivedFact(**{**payload, "result": None})
    with pytest.raises(ValidationError, match="surface map is closed"):
        DerivedFact(**{**payload, "unit": "contracts"})


def test_the_rules_that_were_evaluated_are_the_ones_that_actually_ran():
    """`series.rules_evaluated`, not a list restated beside it."""
    derived = one(agp_package(), agp_candidate(),
                  DerivationOperation.ABSOLUTE_CHANGE, AGP_Q2, AGP_Q3)

    assert derived.comparability_rule_ids == (
        "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R10", "R9")


# ---------------------------------------------------------------------------------------
# §7 — the evidence-scope fact
# ---------------------------------------------------------------------------------------


def test_the_evidence_scope_fact_is_minted_only_when_the_package_establishes_the_absence():
    package = agp_package()

    minted = evidence_scope_facts(
        package, causal_language=CausalLanguage.FORBIDDEN, causal_marker_fact_ids=())

    assert len(minted) == 1
    scope = minted[0]
    assert scope.fact_kind is DerivedFactKind.EVIDENCE_SCOPE
    assert scope.claim == "no_supported_causal_explanation_in_package"
    assert scope.fact_id.startswith("fact:evidence-scope:no-supported-causal-explanation")
    assert scope.examined_fact_ids == (AGP_Q2, AGP_Q3)
    assert scope.tool_version == TOOL_VERSION


@pytest.mark.parametrize(
    ("description", "kwargs", "package_kwargs"),
    [
        ("an explanatory passage exists", {}, {"explanatory_passages": (
            PackagedPassage(passage_id="psg:9", document_id="doc:1",
                            text="Margins fell because of write-downs.", char_count=36,
                            role=EvidenceRole.CONTEXT, excerpted=True),)}),
        ("the package permits reported causal language",
         {"causal_language": CausalLanguage.REPORTED_ONLY}, {}),
        ("a fact quote carries a causal marker, negated or not",
         {"causal_marker_fact_ids": (AGP_Q3,)}, {}),
    ],
)
def test_each_of_the_three_conditions_alone_withholds_the_evidence_scope_fact(
    description, kwargs, package_kwargs
):
    arguments = {"causal_language": CausalLanguage.FORBIDDEN,
                 "causal_marker_fact_ids": (), **kwargs}

    assert evidence_scope_facts(
        agp_package(**package_kwargs), **arguments) == (), description


def test_the_evidence_scope_statement_is_about_the_evidence_and_never_about_the_world():
    """§7's line: *"the evidence in this package supplies no explanation"*, never *"no cause"*."""
    scope = evidence_scope_facts(
        agp_package(), causal_language=CausalLanguage.FORBIDDEN,
        causal_marker_fact_ids=())[0]

    assert scope.statement.startswith("The evidence in this package supplies no explanation")
    for forbidden in ("there was no cause", "nothing caused", "no reason exists"):
        assert forbidden not in scope.statement.lower()
    assert "citations" not in type(scope).model_fields, (
        "§7: it carries no citation and mints no evidence handle; an empty tuple would be a "
        "place to put one")


# ---------------------------------------------------------------------------------------
# The whole stage, once
# ---------------------------------------------------------------------------------------


def test_execute_all_keeps_the_planners_order_and_separates_facts_from_refusals():
    package, candidate = agp_package(), agp_candidate()
    requests = (
        DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                          from_fact_id=AGP_Q2, to_fact_id=AGP_Q3),
        DerivationRequest(operation=DerivationOperation.PERCENTAGE_POINT_CHANGE,
                          from_fact_id=AGP_Q2, to_fact_id=AGP_Q3),
        DerivationRequest(operation=DerivationOperation.PERCENTAGE_CHANGE,
                          from_fact_id=AGP_Q2, to_fact_id=AGP_Q3),
    )

    result = execute_all(
        requests, package, candidate,
        direction=DIRECTION,
        causal_language=CausalLanguage.FORBIDDEN,
        causal_marker_fact_ids=())

    assert [f.operation for f in result.facts] == [
        DerivationOperation.ABSOLUTE_CHANGE, DerivationOperation.PERCENTAGE_CHANGE]
    assert [r.code for r in result.refusals] == [DerivationRefusalCode.UNIT_NOT_ADMITTED]
    assert len(result.evidence_scope_facts) == 1
