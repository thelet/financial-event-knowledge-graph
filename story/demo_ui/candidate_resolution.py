"""One candidate, re-derived from whichever detector minted it, then packaged. §5's other half.

Responsibility: turn a `candidate_id` a reader clicked into the four values `story.pipeline`'s
`run_demo` consumes — the graph identity, the freshness report, the `StoryCandidate` and the
`StoryEvidencePackage` — by **re-deriving** the candidate from the graph rather than trusting
anything the browser sent. It holds no threshold, no detector rule, no packaging bound and no
score; every value below comes out of a stage this module called.

**Why it exists, measured rather than argued.** `story/pipeline.py:resolve_demo_inputs` re-derives
with §6.6's D4 alone — a deliberate §8b narrowing, because that path's claim is about *one
manually selected* cross-metric divergence. The demo UI runs all four detectors and offers what
they found. Against the running server on 2026-08-05, `POST /demo/story-suggestions` returned
twenty suggestions — eleven `metric_move`, six `trend_reversal`, two `acceleration`, one
`cross_metric_divergence` — and `POST /demo/evidence-package` answered
`404 candidate_not_reproducible` for nineteen of them, with the message *"the detectors did not
reproduce that candidate from this graph run."*

**That message was false**, and that is why this module exists rather than a wider
`resolve_demo_inputs`. The process had reproduced the candidate seconds earlier and was still
serving it at position 1; what had not run was D1. A refusal that names the wrong cause sends a
reader to look for a graph problem that is not there, and a false explanation is worse than a
refusal. Everything needed to package the other three families was already public —
`BoundedEvidencePackageBuilder` takes any `StoryCandidate` plus its own `evidence_request` — so
the fix is composition here, not a change to an accepted stage.

**What is kept from `resolve_demo_inputs`, exactly.**

* The freshness gate is **first and blocking**. §17.8's failure is a published post holding
  numbers from a run the graph no longer contains, and every check is cheaper than the load that
  follows. `StaleGraphRefused` is `story/demo_ui/discovery.py`'s, reused rather than re-minted,
  so one refusal type covers both doors into this package.
* Observations are loaded **whole and paged to completeness** by `load_observations`, and the
  builder is handed the records rather than being allowed to fetch its own: §9's
  `get_metric_history` is bounded at 200 rows, five metrics exceed it, and only canonical series
  construction proves the page walk finished.
* The builder is constructed with the same arguments `resolve_demo_inputs` gives it, so a D4
  candidate resolved through this module produces the same package, the same
  `package_content_digest` and — because `detector_versions` carries only the minting detector —
  the same `story_run_id` as `python -m story demo`. That equality is deliberate: the recorded
  answer store and the determinism proof are both keyed off it.

**Nothing here writes anything**, to Neo4j, to a catalog or to disk. It reads, it re-derives, and
it returns.

**Boundaries.** No HTTP, no registry, no run state, no model provider, and no `story.context`:
that module constructs the Bolt adapter and
`tests/story/test_story_package_structure.py::test_no_driver_is_reachable_from_the_contract_the_
core_or_any_stage` walks a guarded import exactly as it walks a plain one, so naming it here —
even under `TYPE_CHECKING` — would put the driver in this package's closure. `discovery.GraphSource`
is the structural type a `StoryContext` already satisfies. `story.pipeline` is likewise not
imported: `DemoInputs` is constructed through a factory the composition root hands in, which is
`as_demo_inputs` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from story.core.graph_identity import GraphIdentity, read_graph_identity
from story.core.models import StoryCandidate, StoryEvidencePackage
from story.core.series import build_series, default_authority
from story.stages.detection import (
    POLICY_VERSION,
    ObservationLoad,
    canonicalize,
    detect_acceleration_from_load,
    detect_cross_metric_divergence,
    detect_metric_moves_from_load,
    detect_trend_reversals_from_load,
    load_observations,
)
from story.stages.detection import (
    acceleration,
    cross_metric_divergence,
    metric_move,
    trend_reversal,
)
from story.stages.freshness import FreshnessReport
from story.stages.packaging import BoundedEvidencePackageBuilder, PackagingError
from story.stages.retrieval.graph_tools import BoundedGraphRetriever

from .discovery import GraphSource, StaleGraphRefused

#: Every detector this module will re-derive through, in the order it tries them. The order is
#: the UI's own listing order and carries no precedence: a `candidate_id` is unique across
#: detectors (§6.11 digests `detector_id` into it), so at most one of these can produce a match.
DETECTOR_MODULES = (metric_move, trend_reversal, acceleration, cross_metric_divergence)


# -- refusals, each naming a cause that is true --------------------------------------------------


class ResolutionError(RuntimeError):
    """Base of every refusal this module raises. Each names what it refused and why."""


class CandidateNotReproduced(ResolutionError):
    """No detector in this graph run produced that id — and all four were asked.

    The distinction from the old behaviour is the whole point: this is raised only after every
    detector has run against a fresh load, so the sentence *"the detectors did not reproduce that
    candidate"* is a measurement. `produced` is how many candidates the four did mint, which is
    what tells a reader whether the run was empty or the id was simply not in it.
    """

    def __init__(self, candidate_id: str, *, detector_ids: Sequence[str], produced: int) -> None:
        super().__init__(
            f"{candidate_id} was not produced by any of {len(detector_ids)} detectors over this "
            f"graph run, which minted {produced} candidates")
        self.candidate_id = candidate_id
        self.detector_ids = tuple(detector_ids)
        self.produced = produced

    def context(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id,
                "detectors_run": list(self.detector_ids),
                "candidates_reproduced": self.produced}


#: Why a re-derived candidate could still not be packaged, and the sentence for each. A closed
#: table so the reason a reader sees is one this repository wrote — the same rule
#: `story/demo_ui/api.py:ERRORS` follows, one level down. There is no entry for "the detectors did
#: not reproduce it": that is `CandidateNotReproduced`'s, and conflating the two is the bug this
#: module was written to fix.
REASON_NO_LOADED_OBSERVATION = "evidence_request_names_no_loaded_observation"
REASON_NO_CITATION_CHAIN = "no_fact_resolved_a_citation_chain"
REASON_BUILDER_REFUSED = "the_bounded_builder_refused_the_request"

PACKAGING_REASONS: Mapping[str, str] = {
    REASON_NO_LOADED_OBSERVATION: (
        "the candidate was re-derived from this graph run, but the observations its evidence "
        "request names are not in the load — so there is no fact to package. The detector and "
        "the graph disagree about what exists; the candidate is real and its evidence is not."),
    REASON_NO_CITATION_CHAIN: (
        "the candidate was re-derived and its observations were found, but none of them "
        "resolved a passage and a document (§13.7). A package of uncitable facts is not a "
        "package, so the bounded builder refused it rather than shipping facts nothing "
        "supports."),
    REASON_BUILDER_REFUSED: (
        "the candidate was re-derived from this graph run, and the bounded evidence builder "
        "refused to package it. The refusal is the builder's own and is recorded in the server "
        "log; it is not a claim that the detectors failed to reproduce the candidate."),
}


class CandidateNotPackageable(ResolutionError):
    """The candidate *was* re-derived, and the bounded builder still could not package it.

    Separate from `CandidateNotReproduced` because they are different facts about different
    things, and reporting one as the other is what this module exists to stop.
    """

    def __init__(
        self,
        candidate_id: str,
        *,
        detector_id: str,
        reason: str,
        metric_ids: Sequence[str] = (),
        period_keys: Sequence[str] = (),
    ) -> None:
        super().__init__(f"{candidate_id}: {reason}")
        self.candidate_id = candidate_id
        self.detector_id = detector_id
        self.reason = reason if reason in PACKAGING_REASONS else REASON_BUILDER_REFUSED
        self.metric_ids = tuple(metric_ids)
        self.period_keys = tuple(period_keys)

    @property
    def explanation(self) -> str:
        return PACKAGING_REASONS[self.reason]

    def context(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id,
                "detector_id": self.detector_id,
                "reason": self.reason,
                "metric_ids": list(self.metric_ids),
                "period_keys": list(self.period_keys)}


# -- what a resolution produced ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedCandidate:
    """`pipeline.DemoInputs`, plus the two facts about *how* it was resolved.

    Deterministic in full: two resolutions over one graph produce the same candidate id, the same
    `package_content_digest` and the same version strings, with no clock anywhere — the property
    `DemoInputs` states and this type must not weaken.

    `detector_id` and `detectors_run` are not on `DemoInputs` and are not smuggled onto it. They
    are what the endpoint reports beside the package (*"re-derived by detector:metric_move; four
    detectors ran"*), and they are the reason a refusal from here can name a cause that is true.
    """

    identity: GraphIdentity
    freshness: FreshnessReport
    candidate: StoryCandidate
    package: StoryEvidencePackage
    detector_versions: Mapping[str, str]
    policy_version: str
    detector_id: str
    detectors_run: tuple[str, ...]
    candidates_reproduced: int

    def as_demo_inputs(self, factory: Callable[..., Any]) -> Any:
        """Build `story.pipeline.DemoInputs` through the factory the composition root supplies.

        A factory rather than an import, for the reason the module docstring gives: naming
        `story.pipeline` here would pull `neo4j` into this package's import closure and fail
        `test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage`. `api.py` already
        holds the module — it reaches it through `services["story_pipeline"]` — so the one place
        that may name it is the one place that passes it in.
        """
        return factory(
            identity=self.identity,
            freshness=self.freshness,
            candidate=self.candidate,
            package=self.package,
            detector_versions=dict(self.detector_versions),
            policy_version=self.policy_version,
        )

    def as_dict(self) -> dict[str, Any]:
        """What the endpoint reports about the resolution itself. No package, no candidate."""
        return {
            "detector_id": self.detector_id,
            "detector_version": dict(self.detector_versions).get(self.detector_id, ""),
            "detectors_run": list(self.detectors_run),
            "candidates_reproduced": self.candidates_reproduced,
            "policy_version": self.policy_version,
            "rederived": True,
            "note": ("the candidate was re-derived from this graph run by the detector that "
                     "minted it; nothing the client sent was trusted beyond the id."),
        }


# -- the resolution --------------------------------------------------------------------------


def resolve_candidate(
    context: GraphSource,
    *,
    candidate_id: str,
    graph_run_id: str,
) -> ResolvedCandidate:
    """Freshness gate, then all four detectors, then the bounded package. Refuses before it reads.

    The one graph load is shared by the four detectors and the builder, which is what keeps the
    cost of running four where `resolve_demo_inputs` ran one: `load_observations` is the round
    trip, and `*_from_load` is arithmetic over rows already in memory.

    Every refusal below names a cause that is true. `StaleGraphRefused` when the gate refused,
    `CandidateNotReproduced` only after all four detectors have run and none produced the id, and
    `CandidateNotPackageable` when the candidate is real and its evidence is not.
    """
    identity = read_graph_identity(context.graph_runs_root, graph_run_id)
    # Imported inside the function for `discovery.discover_from_context`'s reason: `check_freshness`
    # is the only thing in this package's reach that wants a filesystem root and `graph.core`'s
    # manifest reader, and the offline tests must not need either on `sys.path`.
    from story.stages.freshness import check_freshness

    report = check_freshness(
        executor=context.executor, graph_run_id=graph_run_id,
        graph_runs_root=context.graph_runs_root, root=context.root)
    if not report.passed:
        raise StaleGraphRefused(report)

    retriever = BoundedGraphRetriever(context.executor)
    load = load_observations(retriever)
    points = canonicalize(load.records)
    minted = rederive_candidates(load, points, graph_run_id=graph_run_id)

    found = minted.get(candidate_id)
    if found is None:
        raise CandidateNotReproduced(
            candidate_id,
            detector_ids=[module.DETECTOR_ID for module in DETECTOR_MODULES],
            produced=len(minted))
    detector_id, candidate = found

    builder = BoundedEvidencePackageBuilder(
        retriever, identity=identity, records=load.records, points=points,
        unreadable=load.unreadable)
    request = candidate.evidence_request
    if not _request_is_loadable(load, candidate):
        # Checked here rather than inferred from the builder's message: this module can see the
        # load and the request, so it can say *which* inputs are missing instead of restating a
        # stage's prose. The builder would refuse next in any case.
        raise CandidateNotPackageable(
            candidate_id, detector_id=detector_id, reason=REASON_NO_LOADED_OBSERVATION,
            metric_ids=request.metric_ids or candidate.metric_ids,
            period_keys=request.period_keys or candidate.anchor_period_keys)
    try:
        package = builder.build(candidate, request)
    except PackagingError as exc:
        raise CandidateNotPackageable(
            candidate_id, detector_id=detector_id, reason=_packaging_reason(exc),
            metric_ids=request.metric_ids or candidate.metric_ids,
            period_keys=request.period_keys or candidate.anchor_period_keys) from None

    version_of = {module.DETECTOR_ID: module.DETECTOR_VERSION for module in DETECTOR_MODULES}
    return ResolvedCandidate(
        identity=identity,
        freshness=report,
        candidate=candidate,
        package=package,
        # **The minting detector alone**, exactly as `resolve_demo_inputs` records D4 alone.
        # `detector_versions` reaches `story_run_id` and §14's `selection.detector_ids`, so
        # listing all four here would mean a D4 candidate resolved through the UI landed in a
        # different run directory from the same candidate resolved through `python -m story demo`
        # — two ids for one run, which is the failure §14's correction exists to prevent.
        detector_versions={detector_id: version_of[detector_id]},
        policy_version=POLICY_VERSION,
        detector_id=detector_id,
        detectors_run=tuple(module.DETECTOR_ID for module in DETECTOR_MODULES),
        candidates_reproduced=len(minted),
    )


def rederive_candidates(
    load: ObservationLoad,
    points: Sequence[Any],
    *,
    graph_run_id: str,
) -> dict[str, tuple[str, StoryCandidate]]:
    """Every candidate all four detectors produce from this load, keyed by id.

    **Re-derived and never copied.** Nothing a client sent reaches a detector: the id is used as
    a dictionary key against what the detectors just minted, and a candidate that does not match
    one is not resolved. That is the same guarantee `pipeline.select_candidate` gives for D4, held
    for all four families.

    Each detector is called through its own public entry point with the shared authority, and
    each re-canonicalises the same load — `*_from_load` does §6.1 again per detector. That
    repetition is the stages' own contract and is not optimised around here, for
    `discovery._detect`'s reason: hand-feeding points into a detector's inner function would be
    reimplementing the entry point this module is supposed to be composing.
    """
    authority = default_authority()
    series_by_metric = build_series(points)
    minted: dict[str, tuple[str, StoryCandidate]] = {}
    runs = (
        (metric_move, lambda: detect_metric_moves_from_load(
            load, graph_run_id=graph_run_id, authority=authority)),
        (trend_reversal, lambda: detect_trend_reversals_from_load(
            load, graph_run_id=graph_run_id, authority=authority)),
        (acceleration, lambda: detect_acceleration_from_load(
            load, graph_run_id=graph_run_id, authority=authority)),
        (cross_metric_divergence, lambda: detect_cross_metric_divergence(
            series_by_metric, graph_run_id=graph_run_id, authority=authority)),
    )
    for module, run in runs:
        for candidate in run().candidates:
            minted[candidate.candidate_id] = (module.DETECTOR_ID, candidate)
    return minted


def _request_is_loadable(load: ObservationLoad, candidate: StoryCandidate) -> bool:
    """Whether anything the candidate's evidence request names is in the load this run made.

    The same question `BoundedEvidencePackageBuilder._select_facts` asks first, asked here so the
    refusal can name the missing inputs rather than quote a stage. A request that names a slot or
    an observation the load holds is loadable; the builder decides everything after that.
    """
    request = candidate.evidence_request
    if not request.metric_ids and not request.observation_ids:
        # A detector that stated no ids at all leaves the builder nothing to refuse *for*, so the
        # question is not answerable here and the builder's own refusal is the honest source.
        return True
    observations = {record.observation_id for record in load.records}
    if set(request.observation_ids) & observations:
        return True
    if set(candidate.anchor_observation_ids) & observations:
        return True
    slots = {(record.metric_id, record.period.key) for record in load.records}
    wanted_periods = set(request.period_keys) or set(candidate.anchor_period_keys)
    return any((metric_id, period_key) in slots
               for metric_id in request.metric_ids
               for period_key in wanted_periods)


def _packaging_reason(exc: PackagingError) -> str:
    """Classify the builder's own refusal onto this module's closed reason set.

    A substring match on the stage's message, and it is a *fallback* rather than the primary
    classification — `_request_is_loadable` above answers the common case from the data. It is
    written this way because `PackagingError` carries no code; giving it one belongs in
    `story/stages/packaging/`, which this workstream does not own, and is named here as a gap
    rather than worked around by putting the stage's prose into a response body.
    """
    if "citation chain" in str(exc):
        return REASON_NO_CITATION_CHAIN
    if "names no observation" in str(exc):
        return REASON_NO_LOADED_OBSERVATION
    return REASON_BUILDER_REFUSED


__all__ = [
    "DETECTOR_MODULES",
    "PACKAGING_REASONS",
    "REASON_BUILDER_REFUSED",
    "REASON_NO_CITATION_CHAIN",
    "REASON_NO_LOADED_OBSERVATION",
    "CandidateNotPackageable",
    "CandidateNotReproduced",
    "ResolutionError",
    "ResolvedCandidate",
    "rederive_candidates",
    "resolve_candidate",
]
