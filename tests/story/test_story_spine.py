"""S1 and S2 of 01-TARGET-ARCHITECTURE §5, driven from the committed run rather than a fixture.

The spine's whole claim is that it restates what a real detector, a real package and a real
derivation call already agreed on. A hand-built candidate could satisfy every assertion here
while the pipeline produced something else, so the two candidates under test are read off disk:

    data/story_demo/story-v1-1daff167348f   metric_move, adjusted_gross_profit, 2022Q2 -> 2022Q3
    data/story_demo/story-v1-d0d1be4a4f0e   cross_metric_divergence, one period, two metrics

**This file imports two stages, and that is the point.** `spine_for` may not reach
`detector_config.quantity_direction` or `derived_facts.SEMANTIC_DIRECTION` — `core/` imports no
stage — so the only place the spine's word can be checked against the function that produced it
is a test, where the import direction rule does not apply.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.models import (
    CausalLanguage,
    DerivedFact,
    DisplaySemantics,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.core.spine import (
    DIRECTION_UNSTATED,
    DIRECTION_WORDS,
    METRIC_MOVE,
    StorySpine,
    spine_for,
)
from story.stages.detection.detector_config import (
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    DIRECTION_UNCHANGED,
    quantity_direction,
)
from story.stages.verification.derived_facts import SEMANTIC_DIRECTION

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS = REPO_ROOT / "data" / "story_demo"
METRIC_MOVE_RUN = RUNS / "story-v1-1daff167348f"
DIVERGENCE_RUN = RUNS / "story-v1-d0d1be4a4f0e"

#: The two `DisplaySemantics` members that state a direction of the stored number. The other ten
#: are `None` in `SEMANTIC_DIRECTION` — a crossing, a ratio and an unverifiable convention are
#: not rises or falls — and S2 says nothing about them.
DIRECTIONAL_SEMANTICS = (DisplaySemantics.INCREASED_BY, DisplaySemantics.DECREASED_BY,
                         DisplaySemantics.INCREASED, DisplaySemantics.DECREASED)


def _read(run: Path, name: str):
    return json.loads((run / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def candidate() -> StoryCandidate:
    return StoryCandidate.model_validate(_read(METRIC_MOVE_RUN, "candidate.json"))


@pytest.fixture(scope="module")
def package() -> StoryEvidencePackage:
    return StoryEvidencePackage.model_validate(_read(METRIC_MOVE_RUN, "evidence_package.json"))


@pytest.fixture(scope="module")
def derived() -> tuple[DerivedFact, ...]:
    return tuple(DerivedFact.model_validate(row)
                 for row in _read(METRIC_MOVE_RUN, "derived_facts.json")["facts"])


@pytest.fixture(scope="module")
def spine(candidate, package, derived) -> StorySpine:
    built = spine_for(candidate, package, derived)
    assert built is not None, "the committed metric_move run must have a spine"
    return built


# -- rule 0: the artifacts this file rests on are the ones it claims --------------------------


def test_the_committed_run_is_the_metric_move_this_file_describes(candidate, derived):
    """An edited or replaced run would make every assertion below true of something else."""
    assert candidate.story_type == METRIC_MOVE
    assert candidate.metric_ids == ("adjusted_gross_profit",)
    assert candidate.anchor_period_keys == ("2022Q2", "2022Q3")
    assert len(candidate.anchor_observation_ids) == 8, "the supporting set, 4 per period"
    assert len(derived) == 2


# -- S1: the direction is the detector's word, never the sign of a delta ----------------------


def test_s1_the_spines_direction_is_the_word_quantity_direction_computed(spine):
    """Invariant S1, against the function that produced it and over the spine's own values.

    `to_value - from_value` is recomputed here rather than read from `signals["delta"]` so the
    assertion is about the two numbers a reader of the post sees, not about a number the
    detector carried forward.
    """
    assert spine.direction == DIRECTION_DECREASE
    assert spine.direction == quantity_direction(spine.metric_id,
                                                 spine.to_value - spine.from_value)
    assert spine.direction_verified is True


def test_s1_holds_for_a_metric_whose_stored_values_are_negative_by_convention():
    """The reason S1 is a rule and not a formality, stated over the metric it was written for.

    `direct_selling_costs` is stored negative on 46 of 46 canonical values, so a **rise** in
    costs is a fall in the stored number. Any spine that derived `direction` from
    `sign(delta)` would say `"increase"` for the row below and print *"costs rose"* over a
    quarter in which they fell. Driven through `quantity_direction` because that is the
    authority `spine_for` reads at one remove.
    """
    assert quantity_direction("direct_selling_costs", -10_000_000.0) == DIRECTION_INCREASE
    assert quantity_direction("adjusted_gross_profit", -10_000_000.0) == DIRECTION_DECREASE


def test_the_direction_vocabulary_here_is_the_detectors_own():
    """`core/` may not import `detector_config`, so the copy is checked instead of trusted.

    The repository's precedent for a necessary copy is a *checked* one — `slot_table`'s
    operation tuple against the derivation stage's. Three words with this test is a different
    proposition from a hundred-word lexicon with one, which is why `story/core/lexicon.py`
    exists and this constant did not follow it there.
    """
    assert DIRECTION_WORDS == {DIRECTION_INCREASE, DIRECTION_DECREASE, DIRECTION_UNCHANGED}
    assert DIRECTION_UNSTATED not in DIRECTION_WORDS


# -- S2: every derived fact in the spine agrees with the spine's direction --------------------


def test_s2_every_directional_derived_fact_agrees_with_the_spines_polarity(spine, derived):
    """Invariant S2, over the run's real `display_semantics`.

    The bridge is what makes this one comparison instead of a translation: the planner's word,
    the verifier's `SEMANTIC_DIRECTION` and `lexicon.CHANGE_DIRECTION` all answer `bool | None`,
    and the spine carries its direction in that vocabulary so a check never has to convert.
    """
    directional = [f for f in derived if f.display_semantics in DIRECTIONAL_SEMANTICS]
    assert directional, "the run's two derivations both state a direction; S2 needs one"
    for fact in directional:
        assert SEMANTIC_DIRECTION[fact.display_semantics] is spine.direction_polarity


def test_the_polarity_is_the_change_lexicons_own_answer_for_the_detectors_word():
    """`increase -> True`, `decrease -> False`, `unchanged -> None`, unstated -> `None`.

    Pinned because `spine_for` looks the polarity up in `lexicon.CHANGE_DIRECTION` rather than
    holding a four-row table beside it. That is the right call — the detector's word *is* a
    change verb and a second table would be a second authority — and it is only right while the
    lexicon answers these four the way this test says.
    """
    from story.core import lexicon

    assert lexicon.change_direction(DIRECTION_INCREASE) is True
    assert lexicon.change_direction(DIRECTION_DECREASE) is False
    assert lexicon.change_direction(DIRECTION_UNCHANGED) is None
    assert lexicon.change_direction(DIRECTION_UNSTATED) is None


# -- the pair, the values and the periods -----------------------------------------------------


def test_the_spine_binds_the_run_the_pipeline_actually_produced(spine):
    assert spine.metric_id == "adjusted_gross_profit"
    assert spine.from_period == "2022Q2"
    assert spine.to_period == "2022Q3"
    assert spine.from_value == 556_000_000.0
    assert spine.to_value == 110_000_000.0
    assert spine.unit == "USD"
    assert spine.subject_entity_id == "opendoor"
    assert spine.story_type == METRIC_MOVE


def test_the_pair_is_the_two_facts_the_package_carries_not_two_of_the_eight_anchors(
        spine, candidate, package):
    """`anchor_observation_ids` holds 8 supporting ids; `facts` holds the 2 canonical readings.

    A spine that picked from the anchors would have four candidates per period and no rule for
    choosing, so the resolution goes through the package — which is where §6.1 step 5 already
    recorded its choice.
    """
    assert len(package.facts) == 2
    assert {spine.from_fact_id, spine.to_fact_id} == {f.observation_id for f in package.facts}
    assert {spine.from_fact_id, spine.to_fact_id} <= set(candidate.anchor_observation_ids)
    assert spine.from_fact_id != spine.to_fact_id


def test_the_derived_fact_ids_are_the_ones_passed_in_in_the_order_given(spine, derived):
    assert spine.derived_fact_ids == tuple(fact.fact_id for fact in derived)


def test_the_spine_contradicts_nothing_in_the_package_it_was_built_from(spine, package):
    """The round trip: every value on the spine is re-findable on the fact it names.

    Written over the package rather than over the spine's own fields, because the failure this
    guards is a spine that copied a number correctly from the wrong row.
    """
    facts = {fact.observation_id: fact for fact in package.facts}
    from_fact, to_fact = facts[spine.from_fact_id], facts[spine.to_fact_id]
    for fact, period, value in ((from_fact, spine.from_period, spine.from_value),
                                (to_fact, spine.to_period, spine.to_value)):
        assert fact.metric_id == spine.metric_id
        assert fact.period_key == period
        assert fact.value == value
        assert fact.unit == spine.unit
    assert from_fact.period_key < to_fact.period_key, "from precedes to"
    assert spine.package_id == package.package_id
    assert spine.candidate_id == package.candidate_id


def test_the_caller_supplies_the_causal_language_because_core_may_not_compute_it(
        candidate, package, derived):
    """`planner.causal_language_for` is in the generation stage; `core/` may not import it."""
    assert spine_for(candidate, package, derived).causal_language is None
    passed = spine_for(candidate, package, derived,
                       causal_language=CausalLanguage.FORBIDDEN)
    assert passed.causal_language is CausalLanguage.FORBIDDEN


# -- what has no spine shape ------------------------------------------------------------------


def test_no_spine_is_forced_onto_the_divergence_candidate():
    """`cross_metric_divergence` compares two metrics inside one period and has no from/to pair.

    Read from the run rather than asserted about the type name: the candidate really does carry
    one anchor period and two metric ids, which is the shape that has nowhere to put a `from`.
    """
    candidate = StoryCandidate.model_validate(_read(DIVERGENCE_RUN, "candidate.json"))
    package = StoryEvidencePackage.model_validate(_read(DIVERGENCE_RUN, "evidence_package.json"))
    assert candidate.story_type == "cross_metric_divergence"
    assert candidate.anchor_period_keys == ("2022Q3",)
    assert len(candidate.metric_ids) == 2
    assert spine_for(candidate, package, ()) is None


def test_a_candidate_that_published_no_direction_says_so_rather_than_guessing(
        candidate, package, derived):
    """`quantity_direction` returning `None` leaves no `direction` key on the candidate at all.

    `metric_move.candidate_from_move` writes the key only `if direction is not None`, and
    attaches `SIGN_CONVENTION_UNVERIFIED` instead — so the absence, and not a sentinel value, is
    what an `UNVERIFIED` sign convention looks like downstream. The spine is still produced,
    because the move is real and citable and only the sentence describing it is unavailable.
    """
    without = candidate.model_copy(update={
        "signals": {k: v for k, v in candidate.signals.items() if k != "direction"}})
    assert "direction" not in without.signals

    spine = spine_for(without, package, derived)
    assert spine is not None
    assert spine.direction_verified is False
    assert spine.direction_polarity is None
    assert spine.direction == DIRECTION_UNSTATED
    assert spine.from_value == 556_000_000.0, "the facts are unaffected by the missing word"


def test_a_direction_word_the_spine_cannot_map_is_treated_as_no_word(
        candidate, package, derived):
    """Fail closed on a vocabulary this module does not carry.

    Carrying an unrecognised word through would set `direction_verified` over a direction whose
    polarity is `None`, which is a spine asserting it knows something it cannot state.
    """
    odd = candidate.model_copy(update={"signals": {**candidate.signals, "direction": "sideways"}})
    spine = spine_for(odd, package, derived)
    assert spine.direction == DIRECTION_UNSTATED
    assert spine.direction_verified is False


# -- the type cannot be assembled into a claim its producers did not make ----------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"direction": DIRECTION_DECREASE, "direction_verified": False},
        {"direction": DIRECTION_UNSTATED, "direction_verified": True},
        {"direction": DIRECTION_DECREASE, "direction_polarity": True},
        {"direction": DIRECTION_UNCHANGED, "direction_polarity": False},
    ],
    ids=["verified-denies-a-word", "verified-over-no-word", "polarity-inverted",
         "polarity-over-unchanged"],
)
def test_the_three_direction_fields_cannot_be_constructed_in_disagreement(spine, overrides):
    """One datum in three spellings, so two of them are checkable against the first."""
    consistent = {DIRECTION_INCREASE: True, DIRECTION_DECREASE: False}
    fields = {f: getattr(spine, f) for f in StorySpine.__dataclass_fields__}
    fields["direction_verified"] = overrides["direction"] in DIRECTION_WORDS
    fields["direction_polarity"] = consistent.get(overrides["direction"])
    fields.update(overrides)
    with pytest.raises(ValueError):
        StorySpine(**fields)
