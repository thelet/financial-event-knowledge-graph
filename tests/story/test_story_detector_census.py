"""R3 — the four detectors of §6.6 run together, over one load, against the live graph.

Each detector already has a file that pins its own census. This one exists for the thing none of
them can see: the **package-level** total, and the three §6.3 spike candidates that the whole
pipeline is being built to surface. A later change that quietly moves a census — a threshold
edited, a comparability rule loosened, a shape added — fails here loudly and by number, rather
than showing up as a post that says something new.

Measured live against `graph-v1-0483dc6b4b10` on 2026-08-04, from 2,704 observations and 537
canonical slots:

    metric_move                227 candidates across 16 metrics, 0 refusals
    trend_reversal              11 candidates across  9 metrics
    acceleration                 7 quarters-only, 9 over every comparable shape
    cross_metric_divergence     15 candidates, all of them quarters
                               ---
                               262 candidates, every id distinct

**The acceleration figures are 7 and 9, where §6.6 D3 states 8.** The plan's eighth firing was
`gaap_gross_margin 2020Q1 → 2020Q4`, whose first step is `7.3 → 7.4` — one printed unit, which
R8 is written to refuse and did not, because raw subtraction makes the difference
`0.10000000000000053`. D9 made R8 round before comparing; the step is refused and the window is
gone. No threshold moved. `tests/story/test_story_detector_acceleration.py` holds that step
directly.

The offline tests at the top are the other half of R3: `detector_config.py` was written after two
of the four detectors, and a constant that must agree across them has to be single-sourced rather
than copied.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from story.core.periods import PeriodShape
from story.core.series import build_series
from story.stages.detection import (
    canonicalize,
    detect_acceleration,
    detect_cross_metric_divergence,
    detect_metric_moves,
    detect_trend_reversals,
    load_observations,
)
from story.stages.detection import acceleration, cross_metric_divergence, detector_config
from story.stages.detection import metric_move, trend_reversal

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: The four modules `detector_config.py` exists to keep in agreement.
DETECTORS = (metric_move, trend_reversal, acceleration, cross_metric_divergence)

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "story"


# ---------------------------------------------------------------------------------------
# Configuration — one source per shared constant, offline
# ---------------------------------------------------------------------------------------


def test_no_detector_shadows_a_detector_config_name_with_a_different_value():
    """S3-B and S3-C finished before `detector_config.py` existed and declared their own
    constants; R3 moved the shared ones. This is what stops the next one drifting back.

    Equality and not identity: a detector re-exporting `COMPARABLE_SHAPES` is exactly what is
    wanted, and a detector that redeclared it with `PeriodShape.OTHER` added would be a rule
    change wearing a config file's name. Two modules holding the same name at two values is the
    failure — one detector scanning shapes another refuses, or rounding to a precision R8 does
    not.
    """
    shared = {name: getattr(detector_config, name) for name in detector_config.__all__}

    disagreements = [
        f"{module.__name__.rsplit('.', 1)[-1]}.{name} = {getattr(module, name)!r}, "
        f"detector_config.{name} = {value!r}"
        for module in DETECTORS
        for name, value in shared.items()
        if hasattr(module, name) and getattr(module, name) != value
    ]

    assert disagreements == [], disagreements


def test_the_rounding_precision_every_detector_and_r8_share_is_written_down_once():
    """`DELTA_PRECISION` is the one constant a second copy could make actively wrong.

    Since D9 it is what R8 rounds a difference to before testing a presentation tolerance, *and*
    what every detector rounds a delta to before testing a threshold or a sign. A run where the
    two disagreed would refuse a step as rounding in one place and measure it as a movement in
    another. The literal therefore lives in `story/core/series.py` — `core/` may not import a
    stage, so it cannot live in `detector_config.py` — and everything else binds that name.

    Asserted against the source rather than against the values, because two modules that happen
    to say `9` today would pass a value check and still be two places to edit.
    """
    assigning = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "DELTA_PRECISION"
            for target in node.targets
        )
    )

    assert assigning == ["core/series.py"], assigning
    assert detector_config.DELTA_PRECISION == 9
    assert acceleration.DELTA_PRECISION == detector_config.DELTA_PRECISION


def test_the_population_floor_the_two_dispersion_detectors_share_is_one_number():
    """§6.10's *"fewer than ~8 points"* floor, applied by `trend_reversal` to a delta
    distribution and by `cross_metric_divergence` to a gap distribution. Two names, deliberately
    — the things counted are different — and one value, necessarily."""
    assert cross_metric_divergence.MIN_POPULATION == detector_config.MIN_DELTA_POPULATION == 8


def test_each_detector_still_owns_the_constants_only_it_can_use():
    """The other half of the judgment: `detector_config` is not a bag for every constant.

    A list of metric pairs, a run length and a magnitude ratio are one detector's rule. Moving
    them into the shared module would put D3's and D4's rules in a file the other two read, and
    would invite a future detector to fire on a threshold that was never measured for it.
    """
    for name in ("RUN_LENGTH", "MAGNITUDE_RATIO", "WINDOW_POINTS"):
        assert hasattr(acceleration, name)
        assert not hasattr(detector_config, name)
    for name in ("DIVERGENCE_PAIRS", "DIVERGENCE_SHAPES", "Z_MIN", "VARIANCE_FLOOR_MULTIPLE"):
        assert hasattr(cross_metric_divergence, name)
        assert not hasattr(detector_config, name)
    for name in ("MIN_RUN", "SIGMA_MULTIPLE"):
        assert hasattr(trend_reversal, name)
        assert not hasattr(detector_config, name)


# ---------------------------------------------------------------------------------------
# Live — all four detectors, one load, one census
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_census():  # type: ignore[no-untyped-def]
    """Every candidate the four detectors produce from one read of the graph.

    `verify_connectivity` first, matching every other live fixture in this suite: the handshake
    fails in 0.0 s against a refused port while `execute_query` retries for 35 seconds. One load
    for all four detectors, because a census taken over four different reads would not be a
    census of one run.
    """
    from story.context import build_story_context
    from story.stages.retrieval.graph_tools import BoundedGraphRetriever

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        load = load_observations(BoundedGraphRetriever(context.executor))
    finally:
        context.close()

    points = canonicalize(load.records)
    yield {
        "load": load,
        "points": points,
        "metric_move": detect_metric_moves(
            points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable),
        "trend_reversal": detect_trend_reversals(
            points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable),
        "acceleration": detect_acceleration(
            points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable),
        "acceleration_quarters": detect_acceleration(
            points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable,
            shapes=(PeriodShape.QUARTER,)),
        "cross_metric_divergence": detect_cross_metric_divergence(
            build_series(points), graph_run_id=GRAPH_RUN_ID),
    }


def all_candidates(census):  # type: ignore[no-untyped-def]
    """Every candidate of the run, once. `acceleration_quarters` is a subset of `acceleration`
    and is counted through the wider scan, not twice."""
    return [
        candidate
        for key in ("metric_move", "trend_reversal", "acceleration", "cross_metric_divergence")
        for candidate in census[key].candidates
    ]


@pytest.mark.neo4j
def test_live_the_four_detector_census_is_two_hundred_and_sixty_two_candidates(live_census):  # type: ignore[no-untyped-def]
    """The whole of §6.6 in one assertion per detector, so a change that moves a census has to
    move a number in this file.

    The metric counts are asserted beside the candidate counts because the two move
    independently: a threshold change usually moves the candidate count alone, while a
    comparability change can silently drop a whole metric — which is exactly what D9 did to
    `gaap_gross_margin` in `acceleration`.
    """
    census = live_census
    assert census["load"].unreadable == ()
    assert len(census["load"].records) == 2704

    moves = census["metric_move"]
    assert (len(moves.candidates), len({c.metric_ids[0] for c in moves.candidates})) == (227, 16)
    assert moves.refusals == ()

    reversals = census["trend_reversal"]
    assert (len(reversals.candidates),
            len({c.metric_ids[0] for c in reversals.candidates})) == (11, 9)

    quarters = census["acceleration_quarters"]
    assert (len(quarters.candidates),
            len({c.metric_ids[0] for c in quarters.candidates})) == (7, 5)
    shapes = census["acceleration"]
    assert (len(shapes.candidates),
            len({c.metric_ids[0] for c in shapes.candidates})) == (9, 6)

    divergences = census["cross_metric_divergence"]
    assert len(divergences.candidates) == 15
    assert {c.signals["period_shape"] for c in divergences.candidates} == {"quarter"}

    assert len(all_candidates(census)) == 262


@pytest.mark.neo4j
def test_live_the_three_approved_2022q3_spike_candidates_are_emitted_with_their_numbers(live_census):  # type: ignore[no-untyped-def]
    """§6.3's F1, F2 and F3 — the three findings the whole package exists to reach.

    Pinned by `candidate_id` as well as by value: §6.11 derives the id from the detector, its
    version, the policy version, the subject, the anchors and the observation ids, so an id that
    changed while the numbers held would mean the evidence underneath moved.
    """
    by_id = {candidate.candidate_id: candidate for candidate in all_candidates(live_census)}

    ebitda = by_id["cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:1503b5b21731"]
    assert ebitda.metric_ids == ("adjusted_ebitda",)
    assert ebitda.anchor_period_keys == ("2022Q2", "2022Q3")
    assert ebitda.signals["delta"] == -429_000_000.0
    assert ebitda.signals["crosses_zero"] is True

    margin = by_id["cand:metric-move:gaap-gross-margin:opendoor:2022Q2_2022Q3:15b62d34dbaa"]
    assert margin.metric_ids == ("gaap_gross_margin",)
    assert margin.anchor_period_keys == ("2022Q2", "2022Q3")
    assert margin.signals["delta_pp"] == -24.2
    assert margin.signals["crosses_zero"] is True

    wedge = by_id[
        "cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
        "opendoor:2022Q3:9682f1c1c85a"
    ]
    assert wedge.metric_ids == ("adjusted_gross_margin", "gaap_gross_margin")
    assert wedge.anchor_period_keys == ("2022Q3",)
    # `15.899999999999999` on the stored floats; the gap is +15.9 pp to the precision the two
    # margins are printed at.
    assert round(float(wedge.signals["gap"]), 4) == 15.9


@pytest.mark.neo4j
def test_live_not_one_candidate_from_any_detector_carries_prose_a_headline_or_a_score(live_census):  # type: ignore[no-untyped-def]
    """§6.4's non-negotiable, over the whole run rather than over one detector's fixture.

    Asserted as the field set and as the signal keys, not as `hasattr` checks: `extra="forbid"`
    already stops a field arriving by accident, and this stops one arriving on purpose — in a
    field *or* smuggled in under a signal key, which `extra="forbid"` cannot see because
    `signals` is free-keyed by design.
    """
    fields = {
        "candidate_id", "detector_id", "detector_version", "policy_version", "graph_run_id",
        "subject_entity_id", "story_type", "metric_ids", "event_ids", "anchor_period_keys",
        "anchor_observation_ids", "signals", "warnings", "evidence_request", "audience",
    }
    forbidden = (
        "thesis", "headline", "title", "summary", "narrative", "prose", "text", "sentence",
        "score", "materiality", "novelty", "rank", "why",
    )

    candidates = all_candidates(live_census)
    assert len(candidates) == 262

    for candidate in candidates:
        assert set(candidate.model_dump()) == fields, candidate.candidate_id
        offending = [
            key for key in candidate.signals if any(word in key.lower() for word in forbidden)
        ]
        assert offending == [], f"{candidate.candidate_id}: {offending}"


@pytest.mark.neo4j
def test_live_every_candidate_id_in_the_run_is_unique_across_all_four_detectors(live_census):  # type: ignore[no-untyped-def]
    """§6.11 across detectors, which no single detector's test can check.

    The digest covers the detector id, so two detectors firing on the same window — and they do:
    `adjusted_ebitda 2022Q2 → 2022Q3` is a `metric_move` and a `trend_reversal` — must still mint
    two ids. A collision would let §6.10 rank one candidate and silently drop the other.
    """
    ids = [candidate.candidate_id for candidate in all_candidates(live_census)]

    assert len(set(ids)) == len(ids) == 262
    assert {candidate.graph_run_id for candidate in all_candidates(live_census)} == {GRAPH_RUN_ID}
