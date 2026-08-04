"""S4 — §6.10's ranking and deduplication, offline against built candidates and live over 262.

The offline half drives every rule with hand-built candidates and a hand-built `MetricHistory`,
because most of §6.10's rules are about *cases the live run does not contain*: an internal
candidate (D15–D17 are not implemented, so all 262 live candidates are `external`), a
single-source slot, a repetition history (V1 has published nothing), and a genuine score tie.

The `neo4j`-marked half is the other claim — that the rules hold over the real candidate set, that
the dispersion this stage computes is the one the detectors fired against, and that §6.3's three
approved spikes rank where a reader would expect. Measured live against `graph-v1-0483dc6b4b10` on
2026-08-04:

    262 candidates ranked, 112 clusters, 43 of them collapsing more than one candidate
    the 2022Q3 cluster holds 13 candidates over 7 correlated metrics
    F1 adjusted_ebitda 2022Q2→2022Q3    position  16 / 262
    F2 gaap_gross_margin 2022Q2→2022Q3  position   5 / 262
    F3 AGM ↔ GGM divergence 2022Q3      position   6 / 262
    119 candidates carry no `magnitude_z` at all — §6.10's 8-point floor, honoured

**No exact rank is asserted for a spike.** The plan's weights are *"a stated starting point, not a
derivation"*; pinning a spike to position 16 would make every future weight change a test failure
about a number nobody derived. The top decile and a decomposable breakdown are the properties that
matter, and they are what is asserted.
"""

from __future__ import annotations

import ast
import pathlib
import random

import pytest
from pydantic import ValidationError

from story.core.keys import candidate_id
from story.core.models import Audience, CandidateScore, EvidenceRequest, StoryCandidate
from story.core.periods import PeriodShape, story_period
from story.core.series import CanonicalPoint, CanonicalStatus, build_series
from story.stages.ranking import candidate_ranking, deduplication, metric_history, scoring
from story.stages.ranking.candidate_ranking import (
    AUDIENCE_ORDER,
    RankingResult,
    rank_candidates,
)
from story.stages.ranking.deduplication import LineageRelation, deduplicate, lineage_between
from story.stages.ranking.metric_history import (
    MIN_DELTA_POPULATION,
    DeltaDistribution,
    MetricHistory,
    SlotFacts,
    build_metric_history,
)
from story.stages.ranking.scoring import (
    SCORE_PRECISION,
    WEIGHTS,
    AcceptedPost,
    ScoreBreakdown,
    score_candidate,
)

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
POLICY_VERSION = "canon-policy:1.0.0"
PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "story"
RANKING = PACKAGE / "stages" / "ranking"

#: §6.3's three approved spikes, by the ids S3 minted for them (IMPLEMENTATION_STEPS, S3 census).
F1_ADJUSTED_EBITDA = "cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:1503b5b21731"
F2_GAAP_GROSS_MARGIN = "cand:metric-move:gaap-gross-margin:opendoor:2022Q2_2022Q3:15b62d34dbaa"
F3_DIVERGENCE = (
    "cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
    "opendoor:2022Q3:9682f1c1c85a"
)


# ---------------------------------------------------------------------------------------
# Builders — a canonical series and a candidate over it, with no database and no detector
# ---------------------------------------------------------------------------------------


def quarter(key: str):  # type: ignore[no-untyped-def]
    """`2022Q3` -> the `StoryPeriod` for 2022-07-01..2022-09-30, keyed and shaped by core."""
    year, index = int(key[:4]), int(key[-1])
    start_month = 1 + 3 * (index - 1)
    end_month, end_day = start_month + 2, (31, 30, 30, 31)[index - 1]
    return story_period(
        period_start=f"{year}-{start_month:02d}-01", period_end=f"{year}-{end_month:02d}-{end_day}"
    )


def point(  # type: ignore[no-untyped-def]
    metric_id: str,
    period_key: str,
    value: float,
    *,
    n_docs: int = 3,
    unit: str = "homes",
    scale: str | None = "units",
    warnings: tuple[str, ...] = (),
):
    period = quarter(period_key)
    return CanonicalPoint(
        metric_id=metric_id,
        period=period,
        subject_entity_id="opendoor",
        status=CanonicalStatus.OK,
        value=value,
        unit=unit,
        scale=scale,
        currency="USD" if unit.startswith("USD") else None,
        representative_observation_id=f"obs:{metric_id}:{period_key}",
        supporting_observation_ids=(f"obs:{metric_id}:{period_key}",),
        n_docs=n_docs,
        first_filed="2023-01-01",
        last_filed="2023-01-01",
        warnings=warnings,
    )


def series_points(metric_id: str, values: dict[str, float], **kwargs):  # type: ignore[no-untyped-def]
    return [point(metric_id, key, value, **kwargs) for key, value in values.items()]


def move_candidate(  # type: ignore[no-untyped-def]
    metric_id: str,
    earlier: str,
    later: str,
    *,
    delta: float,
    delta_pct: float | None = None,
    story_type: str = "metric_move",
    detector_id: str = "detector:metric_move",
    audience: Audience = Audience.EXTERNAL,
    warnings: tuple[str, ...] = (),
    extra_signals: dict[str, object] | None = None,
    metric_ids: tuple[str, ...] | None = None,
):
    """A `StoryCandidate` shaped exactly as `metric_move.candidate_from_move` builds one."""
    ids = metric_ids or (metric_id,)
    keys = (earlier, later)
    observations = tuple(sorted(f"obs:{name}:{key}" for name in ids for key in keys))
    signals: dict[str, object] = {"delta": delta, "period_shape": "quarter"}
    if delta_pct is not None:
        signals["delta_pct"] = delta_pct
    signals.update(extra_signals or {})
    return StoryCandidate(
        candidate_id=candidate_id(
            detector_id=detector_id,
            detector_version="1.0.0",
            policy_version=POLICY_VERSION,
            scope="-".join(ids),
            subject_entity_id="opendoor",
            anchor_period_keys=keys,
            metric_ids=ids,
            anchor_input_ids=observations,
        ),
        detector_id=detector_id,
        detector_version="1.0.0",
        policy_version=POLICY_VERSION,
        graph_run_id=GRAPH_RUN_ID,
        subject_entity_id="opendoor",
        story_type=story_type,
        metric_ids=tuple(sorted(ids)),
        anchor_period_keys=keys,
        anchor_observation_ids=observations,
        signals=signals,  # type: ignore[arg-type]
        warnings=warnings,
        evidence_request=EvidenceRequest(metric_ids=ids, period_keys=keys),
        audience=audience,
    )


def history_of(*point_groups) -> MetricHistory:  # type: ignore[no-untyped-def]
    """A `MetricHistory` over hand-built points, through the real builder."""
    return build_metric_history([item for group in point_groups for item in group])


QUARTER_KEYS = [f"{year}Q{index}" for year in (2023, 2024, 2025) for index in (1, 2, 3, 4)]

#: A series that actually moves, because a linear one has σ = 0 and suppresses the magnitude term
#: for a reason that has nothing to do with §6.10's population floor.
UNEVEN_COUNTS = [
    1_000.0, 1_280.0, 1_190.0, 1_640.0, 1_500.0,
    1_930.0, 1_760.0, 2_310.0, 2_040.0, 2_620.0, 2_380.0, 3_050.0,
]


def steady_history(metric_id: str = "homes_sold", n_docs: int = 3) -> MetricHistory:
    """Twelve quarters of a metric that appears in no formula — 11 deltas, over §6.10's floor."""
    return history_of(
        series_points(metric_id, dict(zip(QUARTER_KEYS, UNEVEN_COUNTS)), n_docs=n_docs)
    )


def only(result: RankingResult, candidate: StoryCandidate):  # type: ignore[no-untyped-def]
    row = result.row(candidate.candidate_id)
    assert row is not None, candidate.candidate_id
    return row


# ---------------------------------------------------------------------------------------
# The candidate is never given a score — §6.4
# ---------------------------------------------------------------------------------------


def test_a_story_candidate_refuses_to_hold_a_score_field_at_all():
    """§6.4's rule, driven rather than described: the model forbids the extra field.

    `extra="forbid"` is what makes *"a candidate that carried its own score would let a detector
    rank itself"* a structural fact instead of a convention.
    """
    fields = move_candidate("homes_sold", "2024Q1", "2024Q2", delta=900.0).model_dump()
    assert StoryCandidate(**fields)  # the field set without an addition is valid
    for field_name in ("total", "score", "rank", "materiality", "novelty", "repetition_penalty"):
        with pytest.raises(ValidationError):
            StoryCandidate(**{**fields, field_name: 1.0})


def test_the_ranking_leaves_the_candidate_object_byte_identical():
    """The row *references* the candidate; nothing in this stage writes to it."""
    candidate = move_candidate("homes_sold", "2024Q1", "2024Q2", delta=900.0)
    before = candidate.model_dump_json()
    result = rank_candidates([candidate], steady_history())
    assert only(result, candidate).candidate is candidate
    assert candidate.model_dump_json() == before


def test_the_score_row_is_the_core_candidate_score_and_not_a_second_one_declared_here():
    """§6.10's output type already exists in `story/core/models.py`. Redeclaring it would give
    the package two answers to *"what is a score"*."""
    candidate = move_candidate("homes_sold", "2024Q1", "2024Q2", delta=900.0)
    row = only(rank_candidates([candidate], steady_history()), candidate)
    assert isinstance(row.score, CandidateScore)
    assert CandidateScore.__module__ == "story.core.models"
    declared = {
        node.name
        for path in RANKING.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
    }
    assert "CandidateScore" not in declared


# ---------------------------------------------------------------------------------------
# The score decomposes — §6.10
# ---------------------------------------------------------------------------------------


def test_every_present_component_multiplied_by_its_weight_sums_to_the_total():
    """The whole reason `components` exists: a total nobody can decompose is a number nobody can
    argue with, and §6.10's weights are meant to be argued with."""
    history = steady_history()
    for candidate in (
        move_candidate("homes_sold", "2024Q1", "2024Q2", delta=900.0, delta_pct=45.0),
        move_candidate("homes_sold", "2025Q3", "2025Q4", delta=-1_400.0, delta_pct=-30.0),
    ):
        breakdown = score_candidate(candidate, history)
        rebuilt = round(
            sum(
                round(value * WEIGHTS[name], SCORE_PRECISION)
                for name, value in breakdown.components.items()
            ),
            SCORE_PRECISION,
        )
        assert rebuilt == breakdown.total
        assert sum(breakdown.contributions().values()) == pytest.approx(breakdown.total)


def test_a_component_absent_from_the_breakdown_names_the_reason_it_could_not_be_computed():
    """`0.0` and *"not computable"* are different findings, and §6.10 requires the second to be
    representable — `magnitude_z` is *"null, not computed"*."""
    short = history_of(series_points("homes_sold", {"2025Q1": 1_000.0, "2025Q2": 1_900.0}))
    breakdown = score_candidate(
        move_candidate("homes_sold", "2025Q1", "2025Q2", delta=900.0, delta_pct=90.0), short
    )
    assert scoring.COMPONENT_MAGNITUDE_Z not in breakdown.components
    assert "below the 8-point floor" in breakdown.suppressed[scoring.COMPONENT_MAGNITUDE_Z]
    assert set(breakdown.suppressed) & set(breakdown.components) == set()


def test_ambiguity_count_is_never_a_component_because_both_codes_are_metric_constant():
    """§6.10 measured it: `homes_sold_recognition_point` is on all 124 `homes_sold` rows and
    `pct_120_days_denominator` on all 88 `pct_>120d` rows. A term over a metric-constant flag
    ranks the metric, not the story. It is a mandatory caveat (§10.1), not a discriminator."""
    candidate = move_candidate(
        "homes_sold",
        "2024Q1",
        "2024Q2",
        delta=900.0,
        extra_signals={"ambiguity_count": 3, "ambiguity_codes": "homes_sold_recognition_point"},
    )
    breakdown = score_candidate(candidate, steady_history())
    assert not [name for name in breakdown.components if "ambigu" in name]
    assert not [name for name in WEIGHTS if "ambigu" in name]
    assert not [
        node.value
        for path in RANKING.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in ("ambiguity_count", "ambiguity_codes")
    ]


def test_magnitude_z_is_suppressed_below_the_eight_point_floor_and_computed_above_it():
    """§6.10's own boundary, driven from both sides over one metric.

    Seven deltas produce no z at all; eight produce one. The number is not this module's to
    choose — `holding_costs` (4 deltas), `homes_under_contract` (4) and `borrowing_capacity` (0)
    are the metrics the plan names, and the floor is what keeps them out of the magnitude term.
    """
    for population, expect_z in ((MIN_DELTA_POPULATION - 1, False), (MIN_DELTA_POPULATION, True)):
        window = population + 1
        history = history_of(
            series_points(
                "homes_sold", dict(zip(QUARTER_KEYS[:window], UNEVEN_COUNTS[:window]))
            )
        )
        distribution = history.distribution("homes_sold", PeriodShape.QUARTER)
        assert distribution is not None and distribution.population == population
        assert distribution.sigma > 0.0, "a flat series would suppress z for the wrong reason"
        breakdown = score_candidate(
            move_candidate(
                "homes_sold",
                QUARTER_KEYS[population - 1],
                QUARTER_KEYS[population],
                delta=UNEVEN_COUNTS[population] - UNEVEN_COUNTS[population - 1],
            ),
            history,
        )
        assert (scoring.COMPONENT_MAGNITUDE_Z in breakdown.components) is expect_z


MARGIN_VALUES = [12.0, 12.5, 11.0, 12.0, 12.4, 11.5, 12.2, 12.6, 11.8, 12.3, 12.9, 16.9]


def test_the_units_term_divides_by_the_metrics_own_p90_and_never_by_a_base():
    """Defect P10, which cost a factor of twelve at S3, cannot be reintroduced by this term.

    A margin stepping 4.0 pp against the p90 of its own |Δ| is one number whatever `delta_pct`
    says, because `delta_pct` is not consulted: a relative reading of the same step — 4.0 pp on a
    12.9 % base is a 31 % move — is exactly the conflation P10 records.
    """
    history = history_of(
        series_points(
            "gaap_gross_margin", dict(zip(QUARTER_KEYS, MARGIN_VALUES)),
            unit="percent", scale=None,
        )
    )
    distribution = history.distribution("gaap_gross_margin", PeriodShape.QUARTER)
    assert distribution is not None and distribution.unit == "percent"
    with_misleading_pct = move_candidate(
        "gaap_gross_margin", "2025Q3", "2025Q4", delta=4.0, delta_pct=31.007751938
    )
    without = move_candidate("gaap_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    assert (
        score_candidate(with_misleading_pct, history).components[scoring.COMPONENT_MAGNITUDE_UNITS]
        == score_candidate(without, history).components[scoring.COMPONENT_MAGNITUDE_UNITS]
    )
    assert distribution.own_units_ratio(4.0) == pytest.approx(
        4.0 / distribution.p90_abs_delta  # type: ignore[operator]
    )


def test_a_step_across_zero_still_carries_a_units_magnitude_though_it_has_no_delta_pct():
    """The case that decided the term's shape, and the reason it is `|Δ|/p90(|Δ|)`.

    S3's reviewer suppressed `delta_pct` wherever a step crosses zero, because `5.2% → −6.3%` is
    a `−221%` that means nothing. §6.3's F1 is such a step. Measured 2026-08-04, a units term
    reading `delta_pct` would drop the most newsworthy candidate in the corpus from 16th to 37th
    of 262 — out of the top decile — *for having crossed zero*.
    """
    values = {"2024Q1": 400.0, "2024Q2": 380.0, "2024Q3": 410.0, "2024Q4": 370.0,
              "2025Q1": 420.0, "2025Q2": 390.0, "2025Q3": 300.0, "2025Q4": -900.0}
    history = history_of(series_points("adjusted_ebitda", values, unit="USD", scale="units"))
    crossing = move_candidate(
        "adjusted_ebitda", "2025Q3", "2025Q4", delta=-1_200.0,
        extra_signals={"crosses_zero": True},
    )
    assert "delta_pct" not in crossing.signals
    breakdown = score_candidate(crossing, history)
    assert breakdown.components[scoring.COMPONENT_MAGNITUDE_UNITS] == 1.0
    assert scoring.COMPONENT_MAGNITUDE_UNITS not in breakdown.suppressed


def test_the_weights_are_the_eight_section_6_10_states():
    """The numbers, as the plan writes them. A weight change must be a change to this line."""
    assert WEIGHTS == {
        "magnitude_z": 0.40,
        "magnitude_units": 0.20,
        "corroboration": 0.15,
        "novelty": 0.10,
        "recency": 0.10,
        "warning_count": -0.15,
        "single_source": -0.20,
        "repetition": -0.25,
    }


def test_no_weight_is_written_as_a_literal_anywhere_but_its_own_declaration():
    """A weight repeated at a call site is a weight that can change in one place only.

    Walks every ranking module for a float constant equal to one of the eight weights and
    refuses any that is not the right-hand side of a module-level assignment.
    """
    magnitudes = {abs(weight) for weight in WEIGHTS.values()}
    offences: list[str] = []
    for path in sorted(RANKING.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Every constant reachable from a module-level assignment: `WEIGHT_WARNINGS = -0.15` is a
        # unary minus over a constant, not a constant, and the negative weights are all spelled
        # that way.
        declared = {
            id(child)
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            for child in ast.walk(node.value)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, float)
                and abs(node.value) in magnitudes
                and id(node) not in declared
            ):
                offences.append(f"{path.name}:{node.lineno} {node.value}")
    assert offences == [], offences


def test_the_corroboration_term_reads_the_weakest_anchor_slot_and_not_the_strongest():
    """A step is only as filed as its least-filed end. Summing would let a restated quarter
    carry a single-source one past the `single_source` penalty that exists to catch it."""
    history = history_of(
        [
            point("homes_sold", "2025Q1", 1_000.0, n_docs=5),
            point("homes_sold", "2025Q2", 1_900.0, n_docs=1),
        ]
    )
    breakdown = score_candidate(
        move_candidate("homes_sold", "2025Q1", "2025Q2", delta=900.0), history
    )
    assert breakdown.components[scoring.COMPONENT_CORROBORATION] == pytest.approx(0.2)
    assert breakdown.components[scoring.COMPONENT_SINGLE_SOURCE] == 1.0


def test_a_well_corroborated_candidate_outscores_the_same_move_filed_once():
    """The two `n_docs` terms pull the same way: `+0.15·clip(n/5)` and `−0.20` at `n == 1`."""
    move = ("homes_sold", "2025Q1", "2025Q2")
    single = history_of(
        [point("homes_sold", "2025Q1", 1_000.0, n_docs=1),
         point("homes_sold", "2025Q2", 1_900.0, n_docs=1)]
    )
    many = history_of(
        [point("homes_sold", "2025Q1", 1_000.0, n_docs=5),
         point("homes_sold", "2025Q2", 1_900.0, n_docs=5)]
    )
    candidate = move_candidate(*move, delta=900.0)
    assert score_candidate(candidate, many).total > score_candidate(candidate, single).total
    assert score_candidate(candidate, many).components[scoring.COMPONENT_SINGLE_SOURCE] == 0.0


def test_novelty_falls_as_one_metric_and_detector_fire_again_in_later_quarters():
    """§6.10's `1/(1+prior)`: the first time a metric moves is news, the twelfth is a pattern.

    *Prior* is measured in the series, not in the input list — the test shuffles the candidates
    and expects the earliest quarter to keep the highest novelty.
    """
    history = steady_history()
    keys = [f"{year}Q{index}" for year in (2023, 2024, 2025) for index in (1, 2, 3, 4)]
    candidates = [
        move_candidate("homes_sold", earlier, later, delta=250.0)
        for earlier, later in zip(keys, keys[1:])
    ]
    random.Random(11).shuffle(candidates)
    result = rank_candidates(candidates, history)
    novelties = [
        only(result, candidate).score.components[scoring.COMPONENT_NOVELTY]
        for candidate in sorted(candidates, key=lambda item: item.anchor_period_keys)
    ]
    assert novelties == sorted(novelties, reverse=True)
    assert novelties[0] == 1.0
    assert novelties[-1] == pytest.approx(1.0 / len(novelties))


def test_the_first_ever_negative_value_of_a_metric_lifts_its_novelty():
    """§6.10's `is_first_occurrence`, with the plan's own example — *"the first negative value
    ever"*. The bonus applies to the quarter that first went negative and to no later one."""
    values = {"2024Q1": 400.0, "2024Q2": 300.0, "2024Q3": -900.0, "2024Q4": -1_500.0}
    history = history_of(series_points("adjusted_ebitda", values, unit="USD", scale="units"))
    assert history.first_negative_period["adjusted_ebitda"] == "2024Q3"
    # Driven at a high prior count, where the `1/(1+prior)` base is small: at prior 0 both terms
    # clip to 1.0 and the bonus would be invisible, which is the shape a vacuous test would have.
    crossed = score_candidate(
        move_candidate("adjusted_ebitda", "2024Q2", "2024Q3", delta=-1_200.0),
        history,
        prior_count=9,
    )
    after = score_candidate(
        move_candidate("adjusted_ebitda", "2024Q3", "2024Q4", delta=-600.0),
        history,
        prior_count=9,
    )
    assert after.components[scoring.COMPONENT_NOVELTY] == pytest.approx(0.1)
    assert crossed.components[scoring.COMPONENT_NOVELTY] == pytest.approx(
        0.1 + scoring.NOVELTY_FIRST_OCCURRENCE_BONUS
    )
    assert scoring.NOVELTY_FIRST_OCCURRENCE_BONUS == 0.5


def test_recency_falls_with_distance_from_the_2026q1_anchor_and_never_leaves_zero_to_one():
    """§6.10's *"quarters from 2026Q1, normalised"*, over the horizon the corpus sets."""
    keys = [f"{year}Q{index}" for year in (2023, 2024, 2025) for index in (1, 2, 3, 4)]
    history = steady_history()
    recencies = [
        score_candidate(
            move_candidate("homes_sold", earlier, later, delta=250.0), history
        ).components[scoring.COMPONENT_RECENCY]
        for earlier, later in zip(keys, keys[1:])
    ]
    assert recencies == sorted(recencies)
    assert all(0.0 <= value <= 1.0 for value in recencies)
    assert scoring.RECENCY_ANCHOR_PERIOD_KEY == "2026Q1"


def test_repetition_is_zero_on_every_candidate_because_v1_has_accepted_no_post():
    """The term is a function of an explicitly passed history that defaults to empty. There is no
    store of accepted posts in this repository and none is invented here."""
    candidate = move_candidate("homes_sold", "2025Q1", "2025Q2", delta=900.0)
    breakdown = score_candidate(candidate, steady_history())
    assert breakdown.components[scoring.COMPONENT_REPETITION] == 0.0
    assert scoring.repetition(candidate, ()) == 0.0


def test_repetition_rises_with_overlap_against_an_explicitly_passed_accepted_post():
    """§6.10's set overlap over `(metric_ids, period_keys, story_type)` — the cosine of two
    binary membership vectors, computed as sets. No vector space, no embedding."""
    candidate = move_candidate("homes_sold", "2025Q1", "2025Q2", delta=900.0)
    identical = AcceptedPost(
        metric_ids=("homes_sold",), period_keys=("2025Q1", "2025Q2"), story_type="metric_move"
    )
    unrelated = AcceptedPost(
        metric_ids=("market_count",), period_keys=("2019Q1",), story_type="acceleration"
    )
    assert scoring.repetition(candidate, [identical]) == pytest.approx(1.0)
    assert scoring.repetition(candidate, [unrelated]) == 0.0
    partial = scoring.repetition(
        candidate,
        [AcceptedPost(metric_ids=("homes_sold",), period_keys=("2025Q1",),
                      story_type="metric_move")],
    )
    assert 0.0 < partial < 1.0
    penalised = score_candidate(candidate, steady_history(), accepted_history=[identical])
    assert penalised.total < score_candidate(candidate, steady_history()).total


def test_the_warning_penalty_is_capped_so_disclosures_alone_cannot_decide_a_rank():
    """`−0.15·warning_count` is unbounded as §6.10 writes it. Live the maximum is two warnings on
    two candidates, so the cap binds on nothing today and exists for the candidate that would."""
    history = steady_history()
    many = move_candidate(
        "homes_sold", "2025Q1", "2025Q2", delta=900.0,
        warnings=tuple(f"warning_{index}" for index in range(9)),
    )
    breakdown = score_candidate(many, history)
    assert breakdown.components[scoring.COMPONENT_WARNINGS] == scoring.WARNING_COUNT_CAP
    two = move_candidate(
        "homes_sold", "2025Q1", "2025Q2", delta=900.0, warnings=("a_warning", "b_warning")
    )
    assert score_candidate(two, history).components[scoring.COMPONENT_WARNINGS] == 2.0
    assert score_candidate(two, history).total < score_candidate(
        move_candidate("homes_sold", "2025Q1", "2025Q2", delta=900.0), history
    ).total


# ---------------------------------------------------------------------------------------
# Ordering — the declared tie-break, and the audience partition
# ---------------------------------------------------------------------------------------


def test_two_candidates_with_identical_totals_are_ordered_by_candidate_id():
    """The declared key ends in `candidate_id`, which §6.11 makes unique, so the order is total."""
    history = history_of(
        series_points("homes_sold", {"2025Q1": 1_000.0, "2025Q2": 1_900.0}),
        series_points("market_count", {"2025Q1": 1_000.0, "2025Q2": 1_900.0}),
    )
    left = move_candidate("homes_sold", "2025Q1", "2025Q2", delta=900.0)
    right = move_candidate("market_count", "2025Q1", "2025Q2", delta=900.0)
    result = rank_candidates([right, left], history)
    assert only(result, left).score.total == only(result, right).score.total
    assert [row.candidate.candidate_id for row in result.ranked] == sorted(
        [left.candidate_id, right.candidate_id]
    )


def test_shuffling_the_input_leaves_the_order_every_rank_and_every_group_unchanged():
    """Requirement 3 and requirement 7 in one drive: the ranking is a function of the set."""
    history = steady_history()
    keys = [f"{year}Q{index}" for year in (2023, 2024, 2025) for index in (1, 2, 3, 4)]
    candidates = [
        move_candidate("homes_sold", earlier, later, delta=250.0)
        for earlier, later in zip(keys, keys[1:])
    ]
    expected = [
        (row.candidate.candidate_id, row.score.total, row.score.rank, row.score.dedup_group)
        for row in rank_candidates(candidates, history).ranked
    ]
    for seed in (1, 2, 3):
        shuffled = list(candidates)
        random.Random(seed).shuffle(shuffled)
        assert [
            (row.candidate.candidate_id, row.score.total, row.score.rank, row.score.dedup_group)
            for row in rank_candidates(shuffled, history).ranked
        ] == expected


def test_an_internal_candidate_never_outranks_an_external_one_even_scoring_higher():
    """§6.10's guarantee, built before the detectors that need it.

    D15 `fact_conflict`, D16 `coverage_gap` and D17 `formula_closure_break` are not implemented,
    so **every one of the 262 live candidates is `external`** and this rule can only be driven
    synthetically today. That is the reason to drive it: the partition has to exist before the
    first internal candidate arrives, not after.
    """
    history = history_of(
        series_points("homes_sold", dict(zip(QUARTER_KEYS, UNEVEN_COUNTS))),
        # Single-source and three years old: an external candidate at the bottom of the run.
        series_points("market_count", {"2023Q1": 40.0, "2023Q2": 52.0}, n_docs=1),
    )
    external = move_candidate("market_count", "2023Q1", "2023Q2", delta=12.0)
    internal = move_candidate(
        "homes_sold", "2025Q3", "2025Q4", delta=670.0,
        detector_id="detector:fact_conflict", story_type="fact_conflict",
        audience=Audience.INTERNAL,
    )
    result = rank_candidates([internal, external], history)
    assert only(result, internal).score.total > only(result, external).score.total
    assert [row.candidate.candidate_id for row in result.ranked] == [
        external.candidate_id, internal.candidate_id
    ]
    assert only(result, external).score.rank < only(result, internal).score.rank
    assert [row.candidate.candidate_id for row in result.for_post_selection] == [
        external.candidate_id
    ]
    assert AUDIENCE_ORDER == {Audience.EXTERNAL: 0, Audience.INTERNAL: 1}


def test_an_internal_finding_is_never_collapsed_into_an_external_story():
    """A data-quality report and a post about the same quarter are not one story. Collapsing
    across the boundary would let a post suppress the report saying its numbers are in doubt."""
    history = steady_history()
    external = move_candidate("homes_sold", "2025Q1", "2025Q2", delta=250.0)
    internal = move_candidate(
        "homes_sold", "2025Q1", "2025Q2", delta=250.0,
        detector_id="detector:coverage_gap", story_type="coverage_gap",
        audience=Audience.INTERNAL,
    )
    result = rank_candidates([external, internal], history)
    assert only(result, internal).score.dedup_group == internal.candidate_id
    assert only(result, external).score.dedup_group == external.candidate_id
    assert all(len(cluster.member_ids) == 1 for cluster in result.clusters)


def test_the_ranking_is_a_pure_function_of_its_arguments_across_repeated_calls():
    """Requirement 7 and requirement 8: two runs, one answer, and no module state moved."""
    history = steady_history()
    candidates = [
        move_candidate("homes_sold", earlier, later, delta=250.0)
        for earlier, later in zip(
            [f"2024Q{index}" for index in (1, 2, 3)], [f"2024Q{index}" for index in (2, 3, 4)]
        )
    ]
    modules = (metric_history, scoring, deduplication, candidate_ranking)
    before = {
        f"{module.__name__}.{name}": repr(getattr(module, name))
        for module in modules
        for name in dir(module)
        if not name.startswith("__")
    }
    first = rank_candidates(candidates, history)
    second = rank_candidates(candidates, history)
    after = {
        f"{module.__name__}.{name}": repr(getattr(module, name))
        for module in modules
        for name in dir(module)
        if not name.startswith("__")
    }
    assert before == after
    assert [(row.candidate.candidate_id, row.score.total, row.score.rank) for row in first.ranked]\
        == [(row.candidate.candidate_id, row.score.total, row.score.rank) for row in second.ranked]


def test_no_ranking_module_caches_a_result_or_accumulates_into_a_global():
    """Requirement 8, structurally: no `lru_cache`, no `cache`, no module-level accumulator.

    `story.core.series.default_authority` is cached and that is `core/`'s decision about parsing
    the ontology once; a cache *in this stage* would be one keyed on a run.
    """
    for path in sorted(RANKING.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        decorators = {
            ast.unparse(decorator)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
        }
        assert not {name for name in decorators if "cache" in name}, path.name
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            # `__all__` is the one module-level list, and it is a declaration rather than a
            # container anything writes to.
            names = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
            if any(isinstance(name, ast.Name) and name.id == "__all__" for name in names):
                continue
            assert not isinstance(node.value, (ast.List, ast.Set)), f"{path.name}:{node.lineno}"


def test_nothing_in_the_ranking_stage_reaches_a_model_a_provider_or_another_stage():
    """§6.10's *"no LLM is the ranker in V1, and no LLM is a tie-breaker either"*, as imports."""
    for path in sorted(RANKING.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )
            for name in names:
                assert not name.startswith("story.providers"), f"{path.name}: {name}"
                assert not name.startswith("story.stages.detection"), f"{path.name}: {name}"
                assert not name.startswith("story.stages.retrieval"), f"{path.name}: {name}"
                assert name.split(".")[0] not in ("openai", "httpx", "neo4j"), f"{path.name}"


# ---------------------------------------------------------------------------------------
# Deduplication — §6.6 D3, and the sentence that argues for a group
# ---------------------------------------------------------------------------------------


def margin_history() -> MetricHistory:
    """Two margins and their profit component, twelve quarters each — the live lineage shape."""
    keys = [f"{year}Q{index}" for year in (2023, 2024, 2025) for index in (1, 2, 3, 4)]
    margins = [12.0, 12.5, 11.0, 12.0, 12.4, 11.5, 12.2, 12.6, 11.8, 12.3, 12.9, 16.9]
    return history_of(
        series_points("adjusted_gross_margin", dict(zip(keys, margins)),
                      unit="percent", scale=None),
        series_points("gaap_gross_margin", dict(zip(keys, margins)), unit="percent", scale=None),
        series_points("adjusted_gross_profit",
                      {key: 1_000.0 + 300.0 * step for step, key in enumerate(keys)},
                      unit="USD", scale="units"),
        series_points("homes_sold", {key: 1_000.0 + 250.0 * step
                                     for step, key in enumerate(keys)}),
    )


def test_two_candidates_sharing_an_anchor_period_and_a_component_lineage_become_one_group():
    """§6.6 D3's rule. The two margins share the `revenue` denominator `formulas.yaml` declares,
    and a quarter in which both moved is one story."""
    history = margin_history()
    left = move_candidate("adjusted_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    right = move_candidate("gaap_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    clusters = deduplicate([left, right], history)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.anchor_period_key == "2025Q4"
    assert cluster.member_ids == tuple(sorted([left.candidate_id, right.candidate_id]))
    assert cluster.correlated_metric_ids == ("adjusted_gross_margin", "gaap_gross_margin")


def test_a_grouping_explains_itself_by_naming_the_period_and_the_shared_component():
    """Requirement 5: a reader must be able to see **why** two candidates were grouped."""
    history = margin_history()
    left = move_candidate("adjusted_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    right = move_candidate("gaap_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    cluster = deduplicate([left, right], history)[0]
    sentence = cluster.explain()[0]
    assert "2025Q4" in sentence
    assert "adjusted_gross_margin" in sentence and "gaap_gross_margin" in sentence
    assert "revenue" in sentence and "formulas.yaml" in sentence
    assert cluster.reasons[0].relation is LineageRelation.SHARED_COMPONENTS
    assert cluster.reasons[0].via_metric_ids == ("revenue",)


def test_a_margin_and_the_profit_in_its_own_formula_are_grouped_as_a_component_relation():
    """The closest relation is the one reported: `adjusted_gross_profit` is literally a member of
    `adjusted_gross_margin`'s `component_metrics`, which is a stronger statement than sharing one."""
    history = margin_history()
    margin = move_candidate("adjusted_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    profit = move_candidate("adjusted_gross_profit", "2025Q3", "2025Q4", delta=900.0,
                            delta_pct=30.0)
    cluster = deduplicate([margin, profit], history)[0]
    assert cluster.reasons[0].relation is LineageRelation.COMPONENT
    assert cluster.reasons[0].via_metric_ids == ("adjusted_gross_profit",)
    assert "is a component of the other's formula" in cluster.explain()[0]


def test_a_metric_that_appears_in_no_formula_is_never_collapsed_with_a_margin():
    """`homes_sold` is in no `component_metrics` list anywhere in `formulas.yaml`. Collapsing it
    into a margin story would assert an economic relationship the ontology does not declare."""
    history = margin_history()
    margin = move_candidate("adjusted_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    homes = move_candidate("homes_sold", "2025Q3", "2025Q4", delta=900.0, delta_pct=25.0)
    clusters = deduplicate([margin, homes], history)
    assert sorted(len(cluster.member_ids) for cluster in clusters) == [1, 1]
    assert lineage_between("homes_sold", "adjusted_gross_margin", {}) is None


def test_candidates_about_adjacent_quarters_do_not_chain_into_one_group():
    """The rule joins on the **primary** anchor — the period the claim is about — because joining
    on any shared period chains `2022Q2→2022Q3` to `2022Q3→2022Q4` to the next, and eight years of
    adjacent quarters become one story."""
    history = margin_history()
    earlier = move_candidate("adjusted_gross_margin", "2025Q2", "2025Q3", delta=0.5)
    later = move_candidate("adjusted_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    clusters = deduplicate([earlier, later], history)
    assert sorted(cluster.anchor_period_key for cluster in clusters) == ["2025Q3", "2025Q4"]
    assert all(len(cluster.member_ids) == 1 for cluster in clusters)


def test_a_suppressed_member_keeps_its_whole_score_and_carries_its_representatives_rank():
    """Deduplication hides nothing: `CandidateScore.dedup_group` exists *"so a reader can see what
    a rank suppressed"*, so the suppressed row is scored in full and points at its group."""
    history = margin_history()
    strong = move_candidate("adjusted_gross_margin", "2025Q3", "2025Q4", delta=4.0)
    weak = move_candidate("gaap_gross_margin", "2025Q3", "2025Q4", delta=0.6)
    result = rank_candidates([strong, weak], history)
    top, suppressed = only(result, strong), only(result, weak)
    assert top.is_representative and not suppressed.is_representative
    assert suppressed.score.dedup_group == strong.candidate_id
    assert suppressed.score.rank == top.score.rank == 1
    assert suppressed.score.components and suppressed.score.total < top.score.total
    assert len(result.representatives) == 1


def test_the_lineage_relations_are_the_ontologys_and_agree_with_the_divergence_detectors():
    """Restated, not imported — a stage may not import a sibling stage — so the two are pinned.

    `distinct_group` is deliberately absent here: `mutually_distinct_groups` says the ontology
    forbids confusing two metrics, which is close to the opposite of *"these are one story"*.
    """
    from story.stages.detection import cross_metric_divergence

    shared = {relation.name for relation in LineageRelation} & {
        relation.name for relation in cross_metric_divergence.RelationKind
    }
    assert shared == {"COMPONENT", "SHARED_COMPONENTS", "CO_COMPONENT"}
    for name in shared:
        assert (
            LineageRelation[name].value == cross_metric_divergence.RelationKind[name].value
        )
    assert "DISTINCT_GROUP" not in {relation.name for relation in LineageRelation}


def test_the_lineage_relation_reported_is_the_closest_one_that_holds():
    """Identity beats component beats shared component beats co-component, so the sentence a
    group renders names the strongest relationship rather than the first one found."""
    components = {
        "contribution_margin": ("contribution_profit", "revenue"),
        "adjusted_gross_margin": ("adjusted_gross_profit", "revenue"),
        "contribution_profit": ("adjusted_gross_profit", "direct_selling_costs", "holding_costs"),
    }
    assert lineage_between("homes_sold", "homes_sold", {}) == (LineageRelation.SAME_METRIC, ())
    assert lineage_between("contribution_margin", "contribution_profit", components) == (
        LineageRelation.COMPONENT, ("contribution_profit",)
    )
    assert lineage_between("contribution_margin", "adjusted_gross_margin", components) == (
        LineageRelation.SHARED_COMPONENTS, ("revenue",)
    )
    assert lineage_between("direct_selling_costs", "holding_costs", components) == (
        LineageRelation.CO_COMPONENT, ("contribution_profit",)
    )


def test_the_population_floor_this_stage_applies_is_the_number_the_detectors_share():
    """One value, three modules, and no way for them to drift apart unnoticed."""
    from story.stages.detection import cross_metric_divergence, detector_config

    assert (
        MIN_DELTA_POPULATION
        == detector_config.MIN_DELTA_POPULATION
        == cross_metric_divergence.MIN_POPULATION
        == 8
    )


# ---------------------------------------------------------------------------------------
# Live — the real 262, against the graph
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_ranking():  # type: ignore[no-untyped-def]
    """The four detectors' 262 candidates, ranked, from one read of the graph.

    `verify_connectivity` first, like every other live fixture in this suite: the handshake fails
    in 0.0 s against a refused port while `execute_query` retries for 35 seconds.
    """
    from story.context import build_story_context
    from story.stages.detection import (
        canonicalize,
        detect_acceleration,
        detect_cross_metric_divergence,
        detect_metric_moves,
        detect_trend_reversals,
        load_observations,
    )
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
    reversals = detect_trend_reversals(
        points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable
    )
    candidates = [
        candidate
        for result in (
            detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable),
            reversals,
            detect_acceleration(points, graph_run_id=GRAPH_RUN_ID, unreadable=load.unreadable),
            detect_cross_metric_divergence(build_series(points), graph_run_id=GRAPH_RUN_ID),
        )
        for candidate in result.candidates
    ]
    history = build_metric_history(points)
    yield {
        "points": points,
        "candidates": candidates,
        "history": history,
        "reversals": reversals,
        "result": rank_candidates(candidates, history),
    }


@pytest.mark.neo4j
def test_live_every_one_of_the_262_candidates_is_ranked_exactly_once(live_ranking):  # type: ignore[no-untyped-def]
    """The census S3 accepted, carried into S4 without loss or duplication."""
    result = live_ranking["result"]
    assert len(live_ranking["candidates"]) == 262
    assert len(result.ranked) == 262
    assert len({row.candidate.candidate_id for row in result.ranked}) == 262
    assert {row.score.candidate_id for row in result.ranked} == {
        candidate.candidate_id for candidate in live_ranking["candidates"]
    }
    assert [row.score.rank for row in result.representatives] == list(
        range(1, len(result.representatives) + 1)
    )


@pytest.mark.neo4j
def test_live_two_runs_over_one_graph_produce_an_identical_ranking(live_ranking):  # type: ignore[no-untyped-def]
    """Requirement 7, over the real set: same order, same totals, same ranks, same groups.

    The history is rebuilt from the same points as well, so a second run reproduces the
    *distributions* too and not only the ordering over cached numbers.
    """
    def fingerprint(result):  # type: ignore[no-untyped-def]
        return [
            (row.candidate.candidate_id, row.score.total, row.score.rank, row.score.dedup_group,
             tuple(sorted(row.score.components.items())))
            for row in result.ranked
        ]

    again = rank_candidates(
        live_ranking["candidates"], build_metric_history(live_ranking["points"])
    )
    assert fingerprint(again) == fingerprint(live_ranking["result"])


@pytest.mark.neo4j
def test_live_shuffling_the_262_candidates_leaves_the_order_identical(live_ranking):  # type: ignore[no-untyped-def]
    """Requirement 3 over a set that contains a real tie — two candidates share a total of
    0.360535714 on this run — so the `candidate_id` tail of the key is actually exercised."""
    expected = [row.candidate.candidate_id for row in live_ranking["result"].ranked]
    totals = [row.score.total for row in live_ranking["result"].ranked]
    assert len(set(totals)) < len(totals), "no tie in the live set; the tail is untested"
    for seed in (3, 17, 99):
        shuffled = list(live_ranking["candidates"])
        random.Random(seed).shuffle(shuffled)
        again = rank_candidates(shuffled, live_ranking["history"])
        assert [row.candidate.candidate_id for row in again.ranked] == expected


@pytest.mark.neo4j
def test_live_the_2022q3_cluster_collapses_thirteen_candidates_over_seven_metrics(live_ranking):  # type: ignore[no-untyped-def]
    """§6.10's own example, measured: *"the 2022Q3 cluster is five metrics at once in
    `trend_reversal` and the same event appears as `metric_move` + `trend_reversal` +
    `cross_metric_divergence`"*.

    Thirteen candidates: seven `metric_move`s, the five reversals ending 2022Q3, and the
    `adjusted_gross_margin ↔ gaap_gross_margin` divergence. Every one of the seven metrics is
    joined to another through `formulas.yaml` — the four margins by their `revenue` denominator,
    the profits by being components.
    """
    result = live_ranking["result"]
    cluster = next(item for item in result.clusters if item.anchor_period_key == "2022Q3"
                   and len(item.member_ids) > 1)
    assert len(cluster.member_ids) == 13
    assert cluster.correlated_metric_ids == (
        "adjusted_ebitda",
        "adjusted_ebitda_margin",
        "adjusted_gross_margin",
        "adjusted_gross_profit",
        "contribution_margin",
        "contribution_profit",
        "gaap_gross_margin",
    )
    by_type: dict[str, int] = {}
    for member_id in cluster.member_ids:
        row = result.row(member_id)
        assert row is not None
        by_type[row.candidate.story_type] = by_type.get(row.candidate.story_type, 0) + 1
    assert by_type == {"metric_move": 7, "trend_reversal": 5, "cross_metric_divergence": 1}
    assert len({result.row(member).score.dedup_group for member in cluster.member_ids}) == 1  # type: ignore[union-attr]


@pytest.mark.neo4j
def test_live_the_2022q3_grouping_explains_every_join_it_made(live_ranking):  # type: ignore[no-untyped-def]
    """Twelve sentences for thirteen members — one per join, not one per related pair — and every
    one names the period, both metrics and the `formulas.yaml` fact behind it."""
    cluster = next(
        item
        for item in live_ranking["result"].clusters
        if item.anchor_period_key == "2022Q3" and len(item.member_ids) > 1
    )
    sentences = cluster.explain()
    assert len(sentences) == len(cluster.member_ids) - 1
    assert all("2022Q3" in sentence and "formulas.yaml" in sentence for sentence in sentences)
    assert any("share the component revenue" in sentence for sentence in sentences)
    assert any("is a component of the other's formula" in sentence for sentence in sentences)
    relations = {reason.relation for reason in cluster.reasons}
    assert LineageRelation.SHARED_COMPONENTS in relations
    assert LineageRelation.COMPONENT in relations


@pytest.mark.neo4j
def test_live_homes_sold_anchors_2022q3_and_stays_out_of_the_margin_cluster(live_ranking):  # type: ignore[no-untyped-def]
    """The negative half of the dedup rule, on the real run: `homes_sold` moved in the same
    quarter and appears in no formula, so nothing in the ontology relates it to a margin."""
    result = live_ranking["result"]
    homes = [
        row
        for row in result.ranked
        if row.candidate.metric_ids == ("homes_sold",)
        and row.candidate.anchor_period_keys == ("2022Q2", "2022Q3")
    ]
    assert len(homes) == 1
    assert homes[0].score.dedup_group == homes[0].candidate.candidate_id
    assert homes[0].cluster.member_ids == (homes[0].candidate.candidate_id,)


@pytest.mark.neo4j
def test_live_the_three_approved_spike_candidates_all_land_in_the_top_decile(live_ranking):  # type: ignore[no-untyped-def]
    """§6.3's F1, F2 and F3 — the findings the whole package exists to reach.

    The top decile of 262 is 26 rows, and the assertion is membership, not a rank: §6.10's weights
    are *"a stated starting point, not a derivation"*, and a test pinning F1 to position 16 would
    turn every future weight change into a failure about a number nobody derived. Measured
    2026-08-04: F1 at 16, F2 at 5, F3 at 6.
    """
    result = live_ranking["result"]
    decile = max(1, len(result.ranked) // 10)
    positions = {
        row.candidate.candidate_id: index + 1 for index, row in enumerate(result.ranked)
    }
    for spike in (F1_ADJUSTED_EBITDA, F2_GAAP_GROSS_MARGIN, F3_DIVERGENCE):
        assert spike in positions, spike
        assert positions[spike] <= decile, (spike, positions[spike], decile)


@pytest.mark.neo4j
def test_live_every_spike_candidates_score_decomposes_into_its_named_components(live_ranking):  # type: ignore[no-untyped-def]
    """Requirement 2 where it matters most: a reader can take the three headline candidates apart.

    F3's `magnitude_units` is **absent and explained** — `cross_metric_divergence` publishes a z, a
    mean and a σ for its gap distribution but no percentile — which is exactly the case the
    `suppressed` mapping exists for.
    """
    result = live_ranking["result"]
    for spike in (F1_ADJUSTED_EBITDA, F2_GAAP_GROSS_MARGIN, F3_DIVERGENCE):
        row = result.row(spike)
        assert row is not None
        rebuilt = round(
            sum(
                round(value * WEIGHTS[name], SCORE_PRECISION)
                for name, value in row.score.components.items()
            ),
            SCORE_PRECISION,
        )
        assert rebuilt == row.score.total
        assert set(row.score.components) <= set(WEIGHTS)
        assert scoring.COMPONENT_MAGNITUDE_Z in row.score.components
    divergence = result.row(F3_DIVERGENCE)
    assert divergence is not None
    assert scoring.COMPONENT_MAGNITUDE_UNITS not in divergence.score.components
    assert "no p90" in divergence.suppressed[scoring.COMPONENT_MAGNITUDE_UNITS]


@pytest.mark.neo4j
def test_live_the_dispersion_this_stage_computes_is_the_one_trend_reversal_fired_against(live_ranking):  # type: ignore[no-untyped-def]
    """The pin on the one rule this stage restates rather than imports.

    `metric_history.runs_of` is `trend_reversal._runs` written a second time, because a stage may
    not import a sibling stage. If the two ever disagreed, a candidate would have fired against
    one spread and been scored against another — so all eleven live reversals are checked, σ and
    population, against the `sigma` and `delta_population` signals the detector published.
    """
    history = live_ranking["history"]
    reversals = live_ranking["reversals"].candidates
    assert len(reversals) == 11
    for candidate in reversals:
        distribution = history.distribution(
            candidate.metric_ids[0], PeriodShape(candidate.signals["period_shape"])
        )
        assert distribution is not None, candidate.candidate_id
        assert round(distribution.sigma, SCORE_PRECISION) == round(
            float(candidate.signals["sigma"]), SCORE_PRECISION
        )
        assert distribution.population == candidate.signals["delta_population"]


@pytest.mark.neo4j
def test_live_no_candidates_units_magnitude_depends_on_a_suppressed_delta_pct(live_ranking):  # type: ignore[no-untyped-def]
    """The units term is defined for every candidate whose metric has a p90, `delta_pct` or not.

    S3's reviewer suppressed `delta_pct` on 45 zero-crossing and 70 percent-metric candidates; a
    term reading it would have gone dark on 115 of 262 — including §6.3's F1 — for reasons that
    have nothing to do with magnitude.
    """
    without_pct = [
        row
        for row in live_ranking["result"].ranked
        if "delta_pct" not in row.candidate.signals
        and row.candidate.story_type != scoring.DIVERGENCE_STORY_TYPE
    ]
    assert len(without_pct) >= 100
    assert [
        row.candidate.candidate_id
        for row in without_pct
        if scoring.COMPONENT_MAGNITUDE_UNITS in row.suppressed
        and "no p90" not in row.suppressed[scoring.COMPONENT_MAGNITUDE_UNITS]
    ] == []


@pytest.mark.neo4j
def test_live_no_candidate_publishes_a_signal_that_would_let_a_detector_rank_itself(live_ranking):  # type: ignore[no-untyped-def]
    """§6.4 from the ranker's side: nothing in `signals` is a score this stage could have read
    instead of computing. The divergence `z` is a measurement against a published population, not
    a ranking — it carries no weight and no comparison to another candidate."""
    forbidden = set(WEIGHTS) | {"score", "total", "rank", "materiality", "priority"}
    for candidate in live_ranking["candidates"]:
        assert not (set(candidate.signals) & forbidden), candidate.candidate_id


@pytest.mark.neo4j
def test_live_ambiguity_appears_in_none_of_the_262_component_breakdowns(live_ranking):  # type: ignore[no-untyped-def]
    """§6.10's explicit instruction, over the whole run rather than one built candidate."""
    for row in live_ranking["result"].ranked:
        assert set(row.score.components) <= set(WEIGHTS)
        assert not [name for name in row.score.components if "ambigu" in name]


@pytest.mark.neo4j
def test_live_the_eight_point_floor_leaves_119_candidates_with_no_magnitude_term(live_ranking):  # type: ignore[no-untyped-def]
    """§6.10's floor, counted. Nearly half the run is scored with no z at all — mostly the
    fiscal-year and year-to-date shapes, which have five to seven steps each — and every one of
    them says so rather than carrying a synthesised number."""
    rows = live_ranking["result"].ranked
    without = [row for row in rows if scoring.COMPONENT_MAGNITUDE_Z not in row.score.components]
    assert len(without) == 119
    assert all(scoring.COMPONENT_MAGNITUDE_Z in row.suppressed for row in without)
    assert all(
        "below the 8-point floor" in row.suppressed[scoring.COMPONENT_MAGNITUDE_Z]
        for row in without
    )


@pytest.mark.neo4j
def test_live_the_twelve_month_steps_s3_flagged_do_not_dominate_the_top_decile(live_ranking):  # type: ignore[no-untyped-def]
    """The item S3 carried forward, discharged and measured.

    S3 recorded that *"112 of `metric_move`'s 227 candidates are twelve-month steps judged against
    quarter-over-quarter percentiles"* and that *"§6.10 ranking is where an annual restatement
    should lose"*. It loses because every population here is **shape-matched**: a fiscal-year step
    is scored against fiscal-year steps, of which no metric in this run has eight. Measured
    2026-08-04: 112 twelve-month-step candidates exist and **not one** is in the top 26.
    """
    result = live_ranking["result"]
    history = live_ranking["history"]
    annual = {PeriodShape.FISCAL_YEAR, PeriodShape.YTD_6M, PeriodShape.YTD_9M}

    def shape(row):  # type: ignore[no-untyped-def]
        return scoring.anchor_shape(row.candidate, history)

    assert len([row for row in result.ranked if shape(row) in annual]) == 112
    top = result.ranked[: max(1, len(result.ranked) // 10)]
    assert [row.candidate.candidate_id for row in top if shape(row) in annual] == []


@pytest.mark.neo4j
def test_live_the_ranking_holds_112_clusters_of_which_43_collapse_more_than_one_candidate(live_ranking):  # type: ignore[no-untyped-def]
    """The census this step adds to the run's numbers, so a rule change has to move a figure."""
    result = live_ranking["result"]
    assert len(result.clusters) == 112
    assert len(result.representatives) == 112
    assert sum(1 for cluster in result.clusters if cluster.is_collapsed) == 43
    assert sum(len(cluster.member_ids) for cluster in result.clusters) == 262
    # Every live candidate is `external` — D15, D16 and D17 are not implemented — so post
    # selection offers the whole deduplicated set today. The partition is driven synthetically.
    assert len(result.for_post_selection) == 112
    assert {row.audience for row in result.ranked} == {Audience.EXTERNAL}
