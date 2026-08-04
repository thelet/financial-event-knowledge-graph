"""One candidate, re-derived from whichever detector minted it. The repair of a false refusal.

**What this file is about, measured before it was written.** On 2026-08-05, against the running
server and `graph-v1-0483dc6b4b10`:

    POST /demo/story-suggestions {"max_suggestions": 20}   -> 20 suggestions
      by detector: metric_move 11, trend_reversal 6, acceleration 2, cross_metric_divergence 1
    POST /demo/evidence-package {"candidate_id": "<row 1, a metric_move>"}
      -> 404 candidate_not_reproducible
         "the detectors did not reproduce that candidate from this graph run"

The message was false. The process had reproduced that candidate seconds earlier and was still
serving it at position 1; what had not run was D1. `story/pipeline.py:resolve_demo_inputs`
re-derives with §6.6's D4 alone — §8b's deliberate narrowing — so nineteen of twenty rows were
dead ends, each with an untrue explanation.

The offline tests below pin the properties that make the new refusal honest: it is raised only
after all four detectors have run, it is distinct from *"re-derived but not packageable"*, and
nothing a caller supplies reaches a detector. The `neo4j`-marked tests at the bottom are the
measurement — all four families packaged against the real graph, and the D4 package still
byte-identical to the one `resolve_demo_inputs` produces, which is what proves the composition
changed no evidence.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from story.demo_ui import candidate_resolution
from story.demo_ui.candidate_resolution import (
    PACKAGING_REASONS,
    REASON_BUILDER_REFUSED,
    REASON_NO_CITATION_CHAIN,
    REASON_NO_LOADED_OBSERVATION,
    CandidateNotPackageable,
    CandidateNotReproduced,
    _packaging_reason,
    _request_is_loadable,
)
from story.core.models import EvidenceRequest
from story.stages.detection import ObservationLoad
from story.stages.packaging import PackagingError

from conftest import make_candidate  # type: ignore[import-not-found]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "story_demo"

#: §8b's candidate — a cross-metric divergence, and the only family the old path could package.
D4_CANDIDATE_ID = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
                   "opendoor:2022Q3:9682f1c1c85a")


# -- the refusals say what is true ---------------------------------------------------------


def test_the_not_reproduced_refusal_carries_what_actually_ran():
    """The distinction from the old refusal is the whole point: it names its own evidence."""
    error = CandidateNotReproduced(
        "cand:metric-move:x:opendoor:2022Q3:aaaaaaaaaaaa",
        detector_ids=[module.DETECTOR_ID
                      for module in candidate_resolution.DETECTOR_MODULES],
        produced=262)
    context = error.context()
    assert len(context["detectors_run"]) == 4
    assert set(context["detectors_run"]) == {
        "detector:metric_move", "detector:trend_reversal",
        "detector:acceleration", "detector:cross_metric_divergence"}
    assert context["candidates_reproduced"] == 262
    assert "262 candidates" in str(error)


def test_all_four_detectors_are_re_derived_and_not_only_the_demo_paths_one():
    """The property the false refusal came from. Four modules, four distinct detector ids."""
    ids = [module.DETECTOR_ID for module in candidate_resolution.DETECTOR_MODULES]
    assert len(ids) == len(set(ids)) == 4
    assert "detector:metric_move" in ids, (
        "D1 is the detector whose absence produced the false refusal; a list without it would "
        "reintroduce the bug this module was written to fix")


def test_a_candidate_that_cannot_be_packaged_is_a_different_refusal_from_one_not_reproduced():
    """Two facts about two different things, and reporting one as the other was the defect."""
    error = CandidateNotPackageable(
        D4_CANDIDATE_ID, detector_id="detector:metric_move",
        reason=REASON_NO_LOADED_OBSERVATION,
        metric_ids=("adjusted_gross_margin",), period_keys=("2022Q3",))
    assert not isinstance(error, CandidateNotReproduced)
    assert error.explanation == PACKAGING_REASONS[REASON_NO_LOADED_OBSERVATION]
    assert "re-derived" in error.explanation
    assert "did not reproduce" not in error.explanation
    assert error.context()["detector_id"] == "detector:metric_move"


def test_an_unknown_reason_falls_back_to_a_sentence_that_exists():
    """A reason with no row must not produce a `KeyError` inside an error handler."""
    error = CandidateNotPackageable(
        D4_CANDIDATE_ID, detector_id="detector:acceleration", reason="something_invented")
    assert error.reason == REASON_BUILDER_REFUSED
    assert error.explanation == PACKAGING_REASONS[REASON_BUILDER_REFUSED]


@pytest.mark.parametrize("reason", sorted(PACKAGING_REASONS))
def test_no_packaging_reason_blames_the_detectors_for_a_candidate_they_produced(reason):
    """Each sentence is about packaging. None of them may restate the refusal it replaced."""
    sentence = PACKAGING_REASONS[reason]
    assert "re-derived" in sentence
    assert "did not reproduce" not in sentence


def test_the_builders_own_refusal_is_classified_onto_the_closed_set():
    """A fallback, and it is written as one — see `_packaging_reason`'s docstring."""
    assert _packaging_reason(PackagingError(
        "cand:x: every selected observation failed to resolve a citation chain (§13.7)"
    )) == REASON_NO_CITATION_CHAIN
    assert _packaging_reason(PackagingError(
        "cand:x: the evidence request names no observation this builder holds"
    )) == REASON_NO_LOADED_OBSERVATION
    assert _packaging_reason(PackagingError("something else entirely")) == REASON_BUILDER_REFUSED


# -- the loadability pre-check ---------------------------------------------------------------


class _Record:
    """The three fields `_request_is_loadable` reads off an `ObservationRecord`."""

    def __init__(self, observation_id: str, metric_id: str, period_key: str) -> None:
        self.observation_id = observation_id
        self.metric_id = metric_id
        self.period = type("P", (), {"key": period_key})()


def _load(*records: _Record) -> ObservationLoad:
    return ObservationLoad(records=tuple(records))


def _asking_for(metric_id: str, period_key: str, *, observation_ids=()) -> object:
    """A candidate whose evidence request names exactly one slot. See `conftest.make_candidate`."""
    return make_candidate(
        metric_ids=(metric_id,),
        anchor_period_keys=(period_key,),
        anchor_observation_ids=tuple(observation_ids),
        evidence_request=EvidenceRequest(metric_ids=(metric_id,), period_keys=(period_key,),
                                         observation_ids=tuple(observation_ids)))


def test_a_request_whose_slot_is_in_the_load_is_loadable():
    load = _load(_Record("obs:1", "adjusted_gross_margin", "2022Q3"))
    assert _request_is_loadable(load, _asking_for("adjusted_gross_margin", "2022Q3")) is True


def test_a_request_naming_a_metric_the_load_does_not_hold_is_not_loadable():
    """The case that produces a true, specific refusal instead of a vague one."""
    load = _load(_Record("obs:1", "adjusted_gross_margin", "2022Q3"))
    candidate = _asking_for("contribution_profit_after_interest", "2022Q3")
    assert _request_is_loadable(load, candidate) is False


def test_an_observation_id_the_load_holds_is_enough_on_its_own():
    load = _load(_Record("obs:1", "adjusted_gross_margin", "2022Q3"))
    candidate = _asking_for("adjusted_gross_margin", "2019Q1", observation_ids=("obs:1",))
    assert _request_is_loadable(load, candidate) is True


# -- live ------------------------------------------------------------------------------------


@pytest.mark.neo4j
def test_every_detector_family_resolves_and_packages_against_the_real_graph():
    """**The measurement this whole module exists for**, against `graph-v1-0483dc6b4b10`.

    One candidate per family, each taken from what discovery actually ranks, each re-derived
    and packaged through `resolve_candidate`. Before this module, three of these four answered
    `candidate_not_reproducible` with a sentence that was not true of any of them.

    The resolution reports the detector that *minted* the candidate, and `detector_versions`
    carries that detector alone — which is what keeps a D4 candidate's `story_run_id` equal to
    the one `python -m story demo` mints for it.
    """
    from story.context import build_story_context
    from story.demo_ui.discovery import DiscoveryFilters, discover_from_context

    context = build_story_context(REPO_ROOT)
    try:
        found = discover_from_context(
            context, graph_run_id=GRAPH_RUN_ID,
            filters=DiscoveryFilters(max_suggestions=200))
        first_of = {}
        for suggestion in found.suggestions:
            first_of.setdefault(suggestion.candidate.detector_id,
                                suggestion.candidate.candidate_id)
        assert len(first_of) == 4, f"discovery offered only {sorted(first_of)}"

        packaged = {}
        for detector_id, candidate_id in sorted(first_of.items()):
            resolved = candidate_resolution.resolve_candidate(
                context, candidate_id=candidate_id, graph_run_id=GRAPH_RUN_ID)
            packaged[detector_id] = resolved
            assert resolved.detector_id == detector_id
            assert list(resolved.detector_versions) == [detector_id], (
                "detector_versions must name the minting detector alone; listing all four "
                "would move story_run_id away from the CLI's for the same candidate")
            assert resolved.package.facts, "a package with no fact cites nothing"
            assert resolved.freshness.passed
            assert len(resolved.detectors_run) == 4
    finally:
        context.close()

    assert set(packaged) == {"detector:metric_move", "detector:trend_reversal",
                             "detector:acceleration", "detector:cross_metric_divergence"}


@pytest.mark.neo4j
def test_the_d4_package_is_byte_identical_to_the_one_the_accepted_path_builds():
    """The composition changed no evidence, and this is how that is shown rather than said.

    `resolve_demo_inputs` built the committed `evidence_package.json` for this candidate on
    2026-08-04. `resolve_candidate` hands the builder the same identity, the same records, the
    same points and the same `unreadable` list, so the digest must not move — and if it did,
    the demo's evidence would be coming from somewhere the accepted pipeline does not.
    """
    from story.context import build_story_context

    committed = json.loads((FIXTURES / "evidence_package.json").read_text(encoding="utf-8"))
    context = build_story_context(REPO_ROOT)
    try:
        resolved = candidate_resolution.resolve_candidate(
            context, candidate_id=D4_CANDIDATE_ID, graph_run_id=GRAPH_RUN_ID)
    finally:
        context.close()

    assert resolved.package.package_content_digest == committed["package_content_digest"]
    assert resolved.package.package_id == committed["package_id"]


@pytest.mark.neo4j
def test_an_id_no_detector_produces_is_refused_only_after_all_four_have_run():
    """The refusal is now a measurement, and it says how much was measured."""
    from story.context import build_story_context

    context = build_story_context(REPO_ROOT)
    try:
        with pytest.raises(CandidateNotReproduced) as raised:
            candidate_resolution.resolve_candidate(
                context,
                candidate_id="cand:metric-move:nothing:opendoor:2022Q3:ffffffffffff",
                graph_run_id=GRAPH_RUN_ID)
    finally:
        context.close()

    context_block = raised.value.context()
    assert len(context_block["detectors_run"]) == 4
    assert context_block["candidates_reproduced"] > 200, (
        "the run did produce candidates; the refusal must say so, because 'none reproduced' "
        "and 'this one did not' are different findings")
