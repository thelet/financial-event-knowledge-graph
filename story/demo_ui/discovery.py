"""Discovery for the demo UI: all four detectors, real deduplication, real ranking, streamed.

Responsibility: **compose** the accepted stages into one staged, observable operation and report
what each step actually did. It holds no threshold, no comparability rule, no score, no weight
and no dedup rule of its own; every number below comes out of a stage this module called. It
reaches no model — discovery involves none — and it writes nothing anywhere.

**Why it exists.** `story/pipeline.py:resolve_demo_inputs` runs §6.6's D4 alone and never calls
§6.10's ranking at all, because §8b's claim is about *one manually selected* candidate. The UI
claims something wider — *"here is what the graph has to say, in priority order"* — and that
needs the other three detectors, the deduplication and the ranking, plus a way to watch it
happen. `pipeline.py` is not widened to do it: §8b's `selection_mode: manual_demo_candidate` is
an honest label that a discovery-capable `resolve_demo_inputs` would quietly falsify.

**The stages are instrumented, not narrated.** Every event below is emitted at a point where
this module has just done, or is about to do, the work the event names. There is no stage for a
step that does not exist, and no count that is not read off a returned value:

    freshness               §7's gate, only when a caller supplied a report    (optional)
    loading_graph_snapshot  `load_observations` — every observation, paged whole
    canonical_series        `canonicalize` + `build_series` + `census`
    comparability           §6.9's R1–R10 over every adjacent same-shape pair
    metric_changes          D1 — one step is large for this metric
    sign_reversals          D2 — a run of steps turned, by more than σ
    acceleration            D3 — three same-sign steps of growing magnitude
    cross_metric_comparison D4 — two definitions of one quantity stopped agreeing
    metric_history          the delta distributions and slot facts §6.10 scores against
    grouping                §6.6 D3's clusters
    ranking                 §6.10, over every candidate
    preparing_suggestions   the filter, applied to the ranked list

The names are `story/demo_ui/trace.py`'s and not this module's inventions — see `TRACE_STAGES`,
which also records the two that do not map cleanly onto that module's closed set.

**`suppressed` is the point of the return type.** `CandidateScore` — the model §6.10 persists —
carries `components` and no reason a component is missing, so a UI rendering it cannot tell *"no
magnitude term: fewer than eight prior deltas"* from *"a magnitude of zero"*. `RankedCandidate`
carries the reasons, and this module returns `RankedCandidate` and rebuilds the stage's own
`ScoreBreakdown` from it rather than flattening either into a persisted score.

**Filters are applied after ranking and never before it.** §6.10's novelty term counts *prior
candidates for this metric and detector* over the run's candidate set, and §6.6 D3's clusters
are over the same set: filtering first would make a candidate's rank and its score depend on
what the reader happened to ask for. Every row keeps the rank it has in the whole run, and the
result reports how many rows the filter removed.

**Determinism.** Two runs over one graph produce one order, one set of totals and one set of
ids: no clock enters a score, an id or an ordering, ranking is applied through the stage's own
`ordering_key`, and `max_suggestions` truncates an order rather than choosing one. The only
clock in the module is `elapsed_ms` on an event, which is reported and never read.

**Boundaries.** This module names no database driver, no HTTP client, no model provider and no
generation stage — a test asserts the import set. It also deliberately does not import
`story.context`: `story/context.py` constructs the Bolt adapter, and
`tests/story/test_story_package_structure.py::
test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage` walks guarded imports
exactly as it walks plain ones, so naming that module here — even under `TYPE_CHECKING` — would
put the driver in this module's closure. `GraphSource` below is the structural type a
`StoryContext` already satisfies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from story.contracts import GraphRetriever, ReadQueryExecutor
from story.core.models import Audience, StoryCandidate
from story.core.series import (
    CanonicalPoint,
    CanonicalSeries,
    CanonicalStatus,
    ClaimKind,
    ComparabilityAuthority,
    Ok,
    build_series,
    comparable,
    default_authority,
)
from story.stages.detection import (
    COMPARABLE_SHAPES,
    POLICY_VERSION,
    ObservationLoad,
    SlotCensus,
    canonicalize,
    census,
    detect_acceleration_from_load,
    detect_cross_metric_divergence,
    detect_metric_moves_from_load,
    detect_trend_reversals_from_load,
    load_observations,
)
# The three names per detector §6.11 digests into every `candidate_id` are deliberately not
# re-exported by the detection package — a flat alias would mint a second package-level name for
# them — so they are reached through the modules that declare them, exactly as `pipeline.py`
# reaches `cross_metric_divergence.DETECTOR_ID`.
from story.stages.detection import (
    acceleration,
    cross_metric_divergence,
    metric_move,
    trend_reversal,
)
from story.stages.freshness import FreshnessReport
from story.stages.ranking import (
    AUDIENCE_ORDER,
    RANKING_POLICY_VERSION,
    CandidateCluster,
    MetricHistory,
    RankedCandidate,
    RankingResult,
    ScoreBreakdown,
    build_metric_history,
    deduplicate,
    ordering_key,
    rank_candidates,
)
from story.stages.retrieval.graph_tools import BoundedGraphRetriever

# -- the closed stage set ------------------------------------------------------------------------
#
# A tuple and not a free string at each call site: §3 of the plan requires the emitted stage to be
# a member of a closed set, and a test asserts every event's stage is one of these and that they
# arrive in this order.

STAGE_FRESHNESS = "freshness"
STAGE_GRAPH_SNAPSHOT = "loading_graph_snapshot"
STAGE_CANONICAL_SERIES = "canonical_series"
STAGE_COMPARABILITY = "comparability"
STAGE_METRIC_MOVE = "metric_changes"
STAGE_TREND_REVERSAL = "sign_reversals"
STAGE_ACCELERATION = "acceleration"
STAGE_DIVERGENCE = "cross_metric_comparison"
STAGE_METRIC_HISTORY = "metric_history"
STAGE_GROUPING = "grouping"
STAGE_RANKING = "ranking"
STAGE_SUGGESTIONS = "preparing_suggestions"

STAGES: tuple[str, ...] = (
    STAGE_FRESHNESS,
    STAGE_GRAPH_SNAPSHOT,
    STAGE_CANONICAL_SERIES,
    STAGE_COMPARABILITY,
    STAGE_METRIC_MOVE,
    STAGE_TREND_REVERSAL,
    STAGE_ACCELERATION,
    STAGE_DIVERGENCE,
    STAGE_METRIC_HISTORY,
    STAGE_GROUPING,
    STAGE_RANKING,
    STAGE_SUGGESTIONS,
)

#: Two events per stage: one when the work starts, one when it finishes with what it found. The
#: opening event exists because the graph read is the long pole — a panel that showed nothing for
#: its duration would look stalled — and it carries zeros rather than a guess.
#:
#: The three values are `story/demo_ui/trace.py:Status`'s, so the adapter below is an identity on
#: status. See `TRACE_STAGES` for why this module borrows that vocabulary rather than minting one.
STATUS_RUNNING = "running"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUSES: tuple[str, ...] = (STATUS_RUNNING, STATUS_PASSED, STATUS_FAILED)

#: This module's stage, as `story/demo_ui/trace.py` names it. **Ten of the twelve are identical**
#: — the stage constants above adopt that module's `STAGES_BY_PHASE["discovery"]` vocabulary
#: outright, because `TraceEvent` validates `stage` against it and a second set of names would
#: guarantee a validation error at the wiring rather than a translation.
#:
#: Two do not map cleanly and are recorded rather than smoothed over:
#:
#: * `metric_history` is a real step — §6.10 scores against distributions this module builds
#:   before grouping — and the discovery phase's closed set has no name for it. Reported as a
#:   second `ranking` event, which is what it is the input to; nothing is dropped.
#: * `freshness` is in `trace.py`'s **generation** phase, not its discovery one. Discovery gates
#:   first (§7), so an adapter emitting it onto the discovery stream would be refused by
#:   `TraceEvent`'s phase validator. Either the report is rendered outside the trace or that
#:   stage moves; it is not this module's call, and the mapping states the collision instead of
#:   hiding it.
TRACE_STAGES: Mapping[str, str] = {
    STAGE_FRESHNESS: STAGE_FRESHNESS,
    STAGE_GRAPH_SNAPSHOT: STAGE_GRAPH_SNAPSHOT,
    STAGE_CANONICAL_SERIES: STAGE_CANONICAL_SERIES,
    STAGE_COMPARABILITY: STAGE_COMPARABILITY,
    STAGE_METRIC_MOVE: STAGE_METRIC_MOVE,
    STAGE_TREND_REVERSAL: STAGE_TREND_REVERSAL,
    STAGE_ACCELERATION: STAGE_ACCELERATION,
    STAGE_DIVERGENCE: STAGE_DIVERGENCE,
    STAGE_METRIC_HISTORY: STAGE_RANKING,
    STAGE_GROUPING: STAGE_GROUPING,
    STAGE_RANKING: STAGE_RANKING,
    STAGE_SUGGESTIONS: STAGE_SUGGESTIONS,
}

#: How many ids one event may highlight. The graph load touches 2,704 observations and an event
#: carrying all of them is a payload, not a highlight. Sorted and then truncated, so the choice is
#: deterministic and the event says when it truncated.
HIGHLIGHT_LIMIT = 24

#: Where each detector's identity lives, in the order the UI lists them. Read from the modules so
#: a version bump cannot fail to reach the response.
DETECTOR_MODULES = (metric_move, trend_reversal, acceleration, cross_metric_divergence)

STORY_TYPES: Mapping[str, str] = {module.STORY_TYPE: module.DETECTOR_ID
                                  for module in DETECTOR_MODULES}
DETECTOR_VERSIONS: Mapping[str, str] = {module.DETECTOR_ID: module.DETECTOR_VERSION
                                        for module in DETECTOR_MODULES}


class DiscoveryError(RuntimeError):
    """Base of every refusal this module raises. Each names what it refused and why."""


class StaleGraphRefused(DiscoveryError):
    """§7's gate refused. Carries the report so the UI can print every check, not the first."""

    def __init__(self, report: FreshnessReport) -> None:
        super().__init__(
            f"the freshness gate refused {report.graph_run_id}: "
            + ", ".join(check.code.value for check in report.refusals))
        self.report = report


class UnknownStoryType(DiscoveryError):
    """A filter named a story type or detector id no detector in this run produces.

    Raised rather than returning an empty list, because the two are indistinguishable to a
    reader and only one of them is a mistake worth fixing.
    """


# -- the event -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveryEvent:
    """One operational step, with what it examined and what it produced.

    Every field is composed by this module from values a stage returned. **No model authors one,
    and none can**: there is no free-text field a caller may set, `message` is built from counts
    and ids here, and discovery reaches no model at all.

    `processed`, `accepted`, `refused` and `warnings` mean something slightly different per
    stage and each stage's emitter says which in `message`; the invariant is that all four are
    read off a returned value and none is estimated.
    """

    sequence: int
    stage: str
    status: str
    message: str
    processed: int = 0
    accepted: int = 0
    refused: int = 0
    warnings: int = 0
    highlight_node_ids: tuple[str, ...] = ()
    highlights_truncated: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "counts": {
                "processed": self.processed,
                "accepted": self.accepted,
                "refused": self.refused,
                "warnings": self.warnings,
            },
            "highlight_node_ids": list(self.highlight_node_ids),
            "highlights_truncated": self.highlights_truncated,
            "detail": dict(self.detail),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


#: What a caller hands in to watch the run. A plain callable rather than a type from
#: `story/demo_ui/trace.py`: that module is another session's, and a discovery service that could
#: not run without it would make the two impossible to land independently. The adapter is one
#: function — see `run_discovery`'s docstring.
EventSink = Callable[[DiscoveryEvent], None]


class _Emitter:
    """Sequence numbers, timing, and the highlight cap. The only mutable thing in the module."""

    def __init__(self, sink: EventSink | None) -> None:
        self._sink = sink
        self._events: list[DiscoveryEvent] = []
        self._started = time.perf_counter()
        self._universe: frozenset[str] | None = None

    @property
    def events(self) -> tuple[DiscoveryEvent, ...]:
        return tuple(self._events)

    def bind(self, universe: frozenset[str]) -> None:
        """Fix the set a highlight may name, once the graph read has said what exists.

        **This is where §3's *"highlights are never faked"* is enforced rather than intended.**
        The case that forces it is real: §6.6 D4 declares five metric pairs and refuses one with
        `SERIES_ABSENT` because `contribution_profit_after_interest` has no series in this run.
        The refusal is a genuine finding and it names a genuine metric — and there is no node to
        highlight, so the name travels on `DetectorRefusal.metric_ids` and on the event's
        `detail`, and never on `highlight_node_ids`.
        """
        self._universe = universe

    def emit(
        self,
        stage: str,
        status: str,
        message: str,
        *,
        processed: int = 0,
        accepted: int = 0,
        refused: int = 0,
        warnings: int = 0,
        highlights: Sequence[str] = (),
        detail: Mapping[str, Any] | None = None,
    ) -> DiscoveryEvent:
        named = {identifier for identifier in highlights if identifier}
        if self._universe is not None:
            named &= self._universe
        ordered = sorted(named)
        event = DiscoveryEvent(
            sequence=len(self._events),
            stage=stage,
            status=status,
            message=message,
            processed=processed,
            accepted=accepted,
            refused=refused,
            warnings=warnings,
            highlight_node_ids=tuple(ordered[:HIGHLIGHT_LIMIT]),
            highlights_truncated=len(ordered) > HIGHLIGHT_LIMIT,
            detail=dict(detail or {}),
            elapsed_ms=(time.perf_counter() - self._started) * 1000.0,
        )
        self._events.append(event)
        if self._sink is not None:
            self._sink(event)
        return event


# -- filters -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveryFilters:
    """What the reader asked for. Every field is answerable from data this run actually holds.

    **`period_from`/`period_to` are anchor dates (`YYYY-MM-DD`), not period keys**, and that is a
    correction rather than a preference: a candidate anchors period *keys* of mixed shape, and
    `"2022FY" <= "2022Q3"` is a string comparison that means nothing. Every canonical slot carries
    an `anchor_date`, so a date range is the one bound that orders a quarter, a fiscal year and an
    instant against each other. `DiscoveryResult.periods` maps every key in the run to its anchor
    date, so a UI can offer keys and send dates.

    **Two filters a reader might expect and this type does not offer**, because the data does not
    support them and a control that silently did nothing would be worse than its absence:

    * *free-text search over the story* — a candidate carries no prose at all (§6.4 keeps
      editorial text off it), so there is nothing to search but ids;
    * *filing or document filters* — a `StoryCandidate` names observations, not documents, and
      the join to a document runs through the evidence package, which discovery does not build.
    """

    subject_entity_id: str = ""
    metric_ids: tuple[str, ...] = ()
    #: Either a story type (`metric_move`) or a detector id (`detector:metric_move`); both name
    #: the same detector and a UI should not have to know which spelling this layer wants.
    story_types: tuple[str, ...] = ()
    period_from: str = ""
    period_to: str = ""
    external_only: bool = False
    min_score: float | None = None
    max_suggestions: int = 20

    def detector_ids(self) -> frozenset[str]:
        """The detector ids this filter selects, or every one when it names none."""
        if not self.story_types:
            return frozenset(DETECTOR_VERSIONS)
        selected: set[str] = set()
        for name in self.story_types:
            if name in STORY_TYPES:
                selected.add(STORY_TYPES[name])
            elif name in DETECTOR_VERSIONS:
                selected.add(name)
            else:
                raise UnknownStoryType(
                    f"{name!r} is neither a story type ({', '.join(sorted(STORY_TYPES))}) nor a "
                    f"detector id ({', '.join(sorted(DETECTOR_VERSIONS))})")
        return frozenset(selected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_entity_id": self.subject_entity_id,
            "metric_ids": list(self.metric_ids),
            "story_types": list(self.story_types),
            "period_from": self.period_from,
            "period_to": self.period_to,
            "external_only": self.external_only,
            "min_score": self.min_score,
            "max_suggestions": self.max_suggestions,
        }


# -- what each half of the run produced ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DetectorRefusal:
    """One detector refusal, in the one shape the UI can render.

    The four detectors declare four refusal types — `MetricMoveRefusal` and its two siblings
    carry `(metric_id, reason, detail)`, `DivergenceRefusal` carries a metric *pair*, a shape, a
    period and the rule that declined it. Normalised here rather than rendered four ways,
    because *"what was examined and declined"* is one column in the panel; nothing is dropped —
    `rule` and `period_key` are empty for the three that do not have them.
    """

    detector_id: str
    reason: str
    detail: str
    metric_ids: tuple[str, ...] = ()
    period_key: str = ""
    period_shape: str = ""
    rule: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector_id": self.detector_id,
            "reason": self.reason,
            "detail": self.detail,
            "metric_ids": list(self.metric_ids),
            "period_key": self.period_key,
            "period_shape": self.period_shape,
            "rule": self.rule,
        }


@dataclass(frozen=True, slots=True)
class DetectorOutcome:
    """One detector's two halves, plus what it had in front of it.

    `examined` means the number of metric series scanned for the three series detectors and the
    number of declared metric pairs for D4, which is what each of them iterates. The two are not
    the same denominator and are not presented as one — `examined_unit` says which.
    """

    detector_id: str
    detector_version: str
    story_type: str
    examined: int
    examined_unit: str
    candidates: tuple[StoryCandidate, ...] = ()
    refusals: tuple[DetectorRefusal, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "story_type": self.story_type,
            "examined": self.examined,
            "examined_unit": self.examined_unit,
            "candidates": len(self.candidates),
            "refusals": [refusal.as_dict() for refusal in self.refusals],
        }


@dataclass(frozen=True, slots=True)
class ComparabilitySweep:
    """§6.9's rules over every adjacent same-shape pair in the run, counted.

    The same pairs the detectors judge, judged once here so the UI can show the comparability
    layer as a step rather than as an invisible precondition. It is a measurement and not a
    second authority: the same `comparable()` and the same `ComparabilityAuthority` object the
    detectors are handed below, so a pair refused here is refused there.
    """

    pairs: int = 0
    permitted: int = 0
    refused: int = 0
    warned: int = 0
    refusals_by_rule: Mapping[str, int] = field(default_factory=dict)
    warnings_by_code: Mapping[str, int] = field(default_factory=dict)
    refused_metric_ids: tuple[str, ...] = ()
    refused_period_keys: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "pairs": self.pairs,
            "permitted": self.permitted,
            "refused": self.refused,
            "warned": self.warned,
            "refusals_by_rule": dict(self.refusals_by_rule),
            "warnings_by_code": dict(self.warnings_by_code),
            "refused_metric_ids": list(self.refused_metric_ids),
            "refused_period_keys": list(self.refused_period_keys),
        }


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One ranked candidate, with the whole of its score including what could not be computed.

    Wraps `RankedCandidate` rather than replacing it: the row is the ranking stage's own value
    and the UI is entitled to reach past this type for anything not rendered here. `breakdown`
    rebuilds §6.10's `ScoreBreakdown` from the row so `contributions()` — the numbers that
    literally sum to the total — comes from the scoring module's arithmetic and is not restated.
    """

    row: RankedCandidate
    #: Where this candidate sits in the run's full ordering, 1-based and over **every**
    #: candidate rather than over the filtered list. Distinct from `rank` on purpose: §6.10
    #: gives every member of a cluster its *representative's* rank, so six different candidates
    #: can all be `rank 1`, and a UI listing them needs a number that increases down the page.
    position: int = 0
    cluster_reasons: tuple[str, ...] = ()

    @property
    def candidate(self) -> StoryCandidate:
        return self.row.candidate

    @property
    def rank(self) -> int:
        return self.row.score.rank

    @property
    def breakdown(self) -> ScoreBreakdown:
        return ScoreBreakdown(
            candidate_id=self.row.candidate.candidate_id,
            total=self.row.score.total,
            components=dict(self.row.score.components),
            suppressed=dict(self.row.suppressed),
        )

    def as_dict(self) -> dict[str, Any]:
        candidate = self.row.candidate
        breakdown = self.breakdown
        return {
            "candidate_id": candidate.candidate_id,
            "rank": self.row.score.rank,
            "position": self.position,
            "detector_id": candidate.detector_id,
            "detector_version": candidate.detector_version,
            "story_type": candidate.story_type,
            "policy_version": candidate.policy_version,
            "graph_run_id": candidate.graph_run_id,
            "subject_entity_id": candidate.subject_entity_id,
            "audience": candidate.audience.value,
            "metric_ids": list(candidate.metric_ids),
            "anchor_period_keys": list(candidate.anchor_period_keys),
            "anchor_observation_ids": list(candidate.anchor_observation_ids),
            # Detector-computed numbers (`z`, `delta`, `sigma`, …). Every one is a measurement
            # this pipeline made; §6.4 keeps prose off a candidate, so there is nothing here a
            # model could have written.
            "signals": dict(candidate.signals),
            "warnings": list(candidate.warnings),
            "score": {
                "total": breakdown.total,
                "components": dict(breakdown.components),
                "contributions": dict(breakdown.contributions()),
                # The reason each absent term is absent. The field `CandidateScore` has no room
                # for, and the reason this type exists.
                "suppressed": dict(breakdown.suppressed),
                "policy_version": RANKING_POLICY_VERSION,
                "is_probability": False,
            },
            "dedup_group": self.row.score.dedup_group,
            "is_representative": self.row.is_representative,
            "cluster_member_ids": list(self.row.cluster.member_ids),
            "cluster_reasons": list(self.cluster_reasons),
            "evidence_request": candidate.evidence_request.model_dump(mode="json"),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Everything one discovery run decided, and everything it needs to be checked.

    Deterministic in full: no field carries a clock, and `events` is the only place a duration
    appears. Two runs over one graph with one set of filters produce equal `suggestions`, equal
    `ranked`, and an equal sequence of `(stage, status, counts, highlights)`.
    """

    graph_run_id: str
    filters: DiscoveryFilters
    policy_version: str
    ranking_policy_version: str
    detector_versions: Mapping[str, str]
    load: ObservationLoad
    census: SlotCensus
    comparability: ComparabilitySweep
    detectors: tuple[DetectorOutcome, ...] = ()
    clusters: tuple[CandidateCluster, ...] = ()
    ranking: RankingResult = field(default_factory=RankingResult)
    suggestions: tuple[Suggestion, ...] = ()
    matched: int = 0
    events: tuple[DiscoveryEvent, ...] = ()
    freshness: FreshnessReport | None = None
    periods: Mapping[str, str] = field(default_factory=dict)

    @property
    def candidates(self) -> tuple[StoryCandidate, ...]:
        return tuple(row.candidate for row in self.ranking.ranked)

    def suggestion(self, candidate_id: str) -> Suggestion | None:
        return next((s for s in self.suggestions
                     if s.candidate.candidate_id == candidate_id), None)

    def resolvable_ids(self) -> frozenset[str]:
        """Every id this run produced — the set a highlight must resolve in.

        Four namespaces and no fifth: metric ids, period keys, observation ids and candidate
        ids. Held in memory and deliberately **not** in `as_dict()`: it runs to thousands of
        observation ids, and a response carrying them would be a payload rather than an answer.
        `tests/story/test_demo_ui_discovery.py` is what uses it.
        """
        found: set[str] = set()
        for record in self.load.records:
            found.add(record.observation_id)
            found.add(record.metric_id)
            found.add(record.period.key)
        for metric_id, _reason in self.load.unreadable:
            found.add(metric_id)
        found.update(self.periods)
        for row in self.ranking.ranked:
            found.add(row.candidate.candidate_id)
            found.update(row.candidate.metric_ids)
            found.update(row.candidate.anchor_period_keys)
            found.update(row.candidate.anchor_observation_ids)
        return frozenset(found)

    def as_dict(self) -> dict[str, Any]:
        """The HTTP response body. Plain JSON, and nothing a model wrote — there was no model."""
        return {
            "graph_run_id": self.graph_run_id,
            "filters": self.filters.as_dict(),
            "policy_version": self.policy_version,
            "ranking_policy_version": self.ranking_policy_version,
            "detector_versions": dict(self.detector_versions),
            "selection_mode": "deterministic_ranking",
            "score_disclaimer": (
                "Ranking scores are prioritisation heuristics, not probabilities."
            ),
            "counts": {
                "observations": len(self.load.records),
                "metrics": len(self.load.metric_ids),
                "unreadable_metrics": len(self.load.unreadable),
                "retrieval_calls": self.load.calls,
                "slots": self.census.slots,
                "candidates": len(self.ranking.ranked),
                "clusters": len(self.clusters),
                "representatives": len(self.ranking.representatives),
                "matched": self.matched,
                "returned": len(self.suggestions),
            },
            "comparability": self.comparability.as_dict(),
            "detectors": [outcome.as_dict() for outcome in self.detectors],
            "suggestions": [suggestion.as_dict() for suggestion in self.suggestions],
            "events": [event.as_dict() for event in self.events],
            "freshness": (None if self.freshness is None
                          else self.freshness.model_dump(mode="json")),
            "periods": dict(self.periods),
        }


# -- the composition root's other half -----------------------------------------------------------


class GraphSource(Protocol):
    """What discovery needs from a `StoryContext`, as a structural type.

    Structural and not an import (see the module docstring): naming `story.context` here would
    pull the Bolt adapter into this module's import closure and break the package's own
    driver-free rule. A `StoryContext` satisfies this without knowing the protocol exists, which
    is the entire argument `story/contracts.py` makes for every other boundary in the package.
    """

    @property
    def executor(self) -> ReadQueryExecutor: ...

    @property
    def root(self) -> Path: ...

    @property
    def graph_runs_root(self) -> Path: ...


def discover_from_context(
    context: GraphSource,
    *,
    graph_run_id: str,
    filters: DiscoveryFilters | None = None,
    on_event: EventSink | None = None,
    gate: bool = True,
) -> DiscoveryResult:
    """`StoryContext` in, ranked suggestions out. The call an HTTP handler should make.

    The freshness gate runs **first** and is not advisory, for §7's reason and
    `resolve_demo_inputs`'s: §17.8's failure is a post holding numbers from a run the graph no
    longer contains, and every check is cheaper than the load that follows. A refusal raises
    `StaleGraphRefused` after the event describing it has been emitted, so a UI streaming the run
    shows *why* it stopped rather than only that it did.

    `gate=False` exists for one caller — a UI that has already gated this graph run in the same
    session and is re-running discovery under new filters. It is not a way to run against a stale
    graph: the report it skips is the one the response would have carried, and `freshness` is
    `None` in the result rather than absent-but-implied-fine.
    """
    report: FreshnessReport | None = None
    if gate:
        # Imported inside the function: `check_freshness` is the only thing in this module's
        # reach that wants a filesystem root, and the offline path — `run_discovery` against a
        # fake retriever — must not need `graph/core`'s manifest reader on `sys.path` at all.
        from story.stages.freshness import check_freshness

        report = check_freshness(
            executor=context.executor, graph_run_id=graph_run_id,
            graph_runs_root=context.graph_runs_root, root=context.root)
    return run_discovery(
        BoundedGraphRetriever(context.executor),
        graph_run_id=graph_run_id,
        filters=filters,
        on_event=on_event,
        freshness=report,
    )


def run_discovery(
    retriever: GraphRetriever,
    *,
    graph_run_id: str,
    filters: DiscoveryFilters | None = None,
    on_event: EventSink | None = None,
    freshness: FreshnessReport | None = None,
) -> DiscoveryResult:
    """Four detectors, one deduplication, one ranking, one filter — instrumented throughout.

    `retriever` is anything satisfying `story.contracts.GraphRetriever`, which is what makes the
    whole of discovery drivable offline from a recorded set of rows.

    `on_event` is called synchronously as each stage opens and closes, on the calling thread. A
    `story/demo_ui/trace.py` adapter is one function — `lambda event: emit(TraceEvent(...))` over
    `DiscoveryEvent.as_dict()`, whose `stage`, `status`, `message`, counts and
    `highlight_node_ids` are the fields §3 of the plan requires and which no model can reach.

    `freshness` is a report a caller already computed; it is *reported*, never recomputed here,
    and a report that did not pass raises rather than proceeding.
    """
    resolved = filters if filters is not None else DiscoveryFilters()
    # Raised before any work, so a mistyped story type costs a round trip and not a graph read.
    selected_detectors = resolved.detector_ids()
    emitter = _Emitter(on_event)

    if freshness is not None:
        _emit_freshness(emitter, freshness)

    load = _load(emitter, retriever)
    # Bound as soon as the read has said what exists, and before any stage that could name
    # something that does not — see `_Emitter.bind`.
    emitter.bind(frozenset(
        [record.observation_id for record in load.records]
        + [record.metric_id for record in load.records]
        + [record.period.key for record in load.records]
        + [metric_id for metric_id, _reason in load.unreadable]))
    points, slot_census = _canonicalize(emitter, load)
    series_by_metric = build_series(points)
    authority = default_authority()
    sweep = _sweep_comparability(emitter, series_by_metric, authority)

    outcomes = _detect(emitter, load, series_by_metric, graph_run_id=graph_run_id,
                       authority=authority)
    candidates = tuple(
        candidate for outcome in outcomes for candidate in outcome.candidates)

    history = _history(emitter, points, authority)
    clusters = _group(emitter, candidates, history, authority)
    ranking = _rank(emitter, candidates, history, authority)

    periods = {
        period_key: slot.anchor_date or ""
        for (_metric_id, period_key), slot in sorted(history.slots.items())
        if slot.anchor_date
    }
    suggestions, matched = _suggest(
        emitter, ranking, resolved, selected_detectors, history, periods)

    return DiscoveryResult(
        graph_run_id=graph_run_id,
        filters=resolved,
        policy_version=POLICY_VERSION,
        ranking_policy_version=RANKING_POLICY_VERSION,
        detector_versions=dict(DETECTOR_VERSIONS),
        load=load,
        census=slot_census,
        comparability=sweep,
        detectors=outcomes,
        clusters=clusters,
        ranking=ranking,
        suggestions=suggestions,
        matched=matched,
        events=emitter.events,
        freshness=freshness,
        periods=periods,
    )


# -- the stages, one function each ---------------------------------------------------------------


def _emit_freshness(emitter: _Emitter, report: FreshnessReport) -> None:
    """§7's verdict, as the run's first event. Refuses loudly and after the event, not before."""
    refusals = tuple(check.code.value for check in report.refusals)
    emitter.emit(
        STAGE_FRESHNESS, STATUS_RUNNING,
        f"checking the loaded graph against {report.graph_run_id}")
    emitter.emit(
        STAGE_FRESHNESS, STATUS_PASSED if report.passed else STATUS_FAILED,
        (f"{len(report.checks) - len(report.refusals)} of {len(report.checks)} freshness checks "
         f"passed") if report.passed else
        f"the freshness gate refused {report.graph_run_id}: {', '.join(refusals)}",
        processed=len(report.checks),
        accepted=len(report.checks) - len(report.refusals),
        refused=len(report.refusals),
        detail={"codes": list(refusals)},
    )
    if not report.passed:
        raise StaleGraphRefused(report)


def _load(emitter: _Emitter, retriever: GraphRetriever) -> ObservationLoad:
    """Every observation of every projected metric, through §9's bounded tools and nothing else.

    Paged to completeness by `load_observations`, which is why the count below is a fact rather
    than a page: five metrics exceed `get_metric_history`'s 200-row bound, and a short read would
    make every delta over them a delta across an unknown hole.
    """
    emitter.emit(STAGE_GRAPH_SNAPSHOT, STATUS_RUNNING,
                 "reading every observation of every projected metric")
    load = load_observations(retriever)
    unreadable = tuple(metric_id for metric_id, _reason in load.unreadable)
    emitter.emit(
        STAGE_GRAPH_SNAPSHOT, STATUS_PASSED,
        f"{len(load.records)} observations across {len(load.metric_ids)} metrics in "
        f"{load.calls} bounded reads"
        + (f"; {len(unreadable)} metric(s) unreadable" if unreadable else ""),
        processed=load.calls,
        accepted=len(load.records),
        refused=len(load.unreadable),
        highlights=(*load.metric_ids, *unreadable),
        detail={"metrics": len(load.metric_ids), "unreadable": list(unreadable)},
    )
    return load


def _canonicalize(emitter: _Emitter,
                  load: ObservationLoad) -> tuple[tuple[CanonicalPoint, ...], SlotCensus]:
    """§6.1: observations to one canonical value per `(metric, period)` slot."""
    emitter.emit(STAGE_CANONICAL_SERIES, STATUS_RUNNING,
                 f"canonicalising {len(load.records)} observations into fact slots")
    points = canonicalize(load.records)
    slot_census = census(points)
    conflicted = tuple(point for point in points if point.status is CanonicalStatus.CONFLICT)
    warned = tuple(point for point in points if point.warnings)
    emitter.emit(
        STAGE_CANONICAL_SERIES, STATUS_PASSED,
        f"{slot_census.slots} slots over {slot_census.metrics} metrics: {slot_census.ok} clean, "
        f"{slot_census.resolved_by_majority} resolved by majority, {slot_census.conflict} "
        f"unresolved, {slot_census.quarantined} observations quarantined",
        processed=slot_census.observations,
        accepted=slot_census.slots - slot_census.conflict,
        refused=slot_census.conflict,
        warnings=len(warned),
        highlights=[point.metric_id for point in conflicted]
                   + [point.period_key for point in conflicted],
        detail={
            "multi_valued": slot_census.multi_valued,
            "multi_cluster": slot_census.multi_cluster,
            "resolved_by_majority": slot_census.resolved_by_majority,
            "quarantined": slot_census.quarantined,
        },
    )
    return points, slot_census


def _sweep_comparability(
    emitter: _Emitter,
    series_by_metric: Mapping[str, CanonicalSeries],
    authority: ComparabilityAuthority,
) -> ComparabilitySweep:
    """§6.9's R1–R10 over every adjacent same-shape pair the detectors are about to judge.

    Under `ClaimKind.MOVEMENT`, which is the claim all three series detectors make and therefore
    the standard whose refusals explain their candidate counts. D4 asks a different question of
    different pairs — two metrics at one period — and its refusals arrive on its own outcome
    rather than being folded in here, because they are not the same measurement.
    """
    emitter.emit(STAGE_COMPARABILITY, STATUS_RUNNING,
                 f"checking §6.9's rules over {len(series_by_metric)} metric series")
    pairs = permitted = refused = warned = 0
    by_rule: dict[str, int] = {}
    by_code: dict[str, int] = {}
    refused_metrics: set[str] = set()
    refused_periods: set[str] = set()

    for metric_id, series in sorted(series_by_metric.items()):
        for shape in COMPARABLE_SHAPES:
            points = series.valued_points(shape)
            for earlier, later in zip(points, points[1:]):
                pairs += 1
                verdict = comparable(later, earlier, claim=ClaimKind.MOVEMENT,
                                     authority=authority, series=series)
                if isinstance(verdict, Ok):
                    permitted += 1
                    if verdict.warnings:
                        warned += 1
                        for warning in verdict.warnings:
                            by_code[warning.code] = by_code.get(warning.code, 0) + 1
                    continue
                refused += 1
                by_rule[verdict.rule] = by_rule.get(verdict.rule, 0) + 1
                refused_metrics.add(metric_id)
                refused_periods.update((earlier.period_key, later.period_key))

    sweep = ComparabilitySweep(
        pairs=pairs, permitted=permitted, refused=refused, warned=warned,
        refusals_by_rule=dict(sorted(by_rule.items())),
        warnings_by_code=dict(sorted(by_code.items())),
        refused_metric_ids=tuple(sorted(refused_metrics)),
        refused_period_keys=tuple(sorted(refused_periods)),
    )
    emitter.emit(
        STAGE_COMPARABILITY, STATUS_PASSED,
        f"{permitted} of {pairs} adjacent same-shape pairs may be compared; {refused} refused, "
        f"{warned} permitted with a disclosure",
        processed=pairs, accepted=permitted, refused=refused, warnings=warned,
        highlights=(*sweep.refused_metric_ids, *sweep.refused_period_keys),
        detail={"refusals_by_rule": dict(sweep.refusals_by_rule),
                "warnings_by_code": dict(sweep.warnings_by_code)},
    )
    return sweep


def _detect(
    emitter: _Emitter,
    load: ObservationLoad,
    series_by_metric: Mapping[str, CanonicalSeries],
    *,
    graph_run_id: str,
    authority: ComparabilityAuthority,
) -> tuple[DetectorOutcome, ...]:
    """All four detectors of §6.6, each with its own event.

    Every detector is called through its own public entry point with the same `authority` the
    sweep above used, and each is re-canonicalising the same load — `*_from_load` does §6.1
    again per detector. That repetition is the stages' own contract and is not optimised around
    here: a discovery service that hand-fed points into a detector's inner function would be
    reimplementing the entry point it is supposed to be composing.
    """
    scanned = len(series_by_metric) - len(load.unreadable)
    outcomes: list[DetectorOutcome] = []

    for module, run in (
        (metric_move, lambda: detect_metric_moves_from_load(
            load, graph_run_id=graph_run_id, authority=authority)),
        (trend_reversal, lambda: detect_trend_reversals_from_load(
            load, graph_run_id=graph_run_id, authority=authority)),
        (acceleration, lambda: detect_acceleration_from_load(
            load, graph_run_id=graph_run_id, authority=authority)),
    ):
        outcomes.append(_run_detector(
            emitter, module, run, examined=scanned, examined_unit="metric series"))

    outcomes.append(_run_detector(
        emitter, cross_metric_divergence,
        lambda: detect_cross_metric_divergence(
            series_by_metric, graph_run_id=graph_run_id, authority=authority),
        examined=len(cross_metric_divergence.DIVERGENCE_PAIRS),
        examined_unit="declared metric pairs"))
    return tuple(outcomes)


#: Which stage each detector's events carry. Keyed by detector id so a renamed module cannot
#: silently re-point a stage.
_DETECTOR_STAGES: Mapping[str, str] = {
    metric_move.DETECTOR_ID: STAGE_METRIC_MOVE,
    trend_reversal.DETECTOR_ID: STAGE_TREND_REVERSAL,
    acceleration.DETECTOR_ID: STAGE_ACCELERATION,
    cross_metric_divergence.DETECTOR_ID: STAGE_DIVERGENCE,
}


def _run_detector(
    emitter: _Emitter,
    module: Any,
    run: Callable[[], Any],
    *,
    examined: int,
    examined_unit: str,
) -> DetectorOutcome:
    """One detector, with its two halves reported and neither of them dropped."""
    stage = _DETECTOR_STAGES[module.DETECTOR_ID]
    emitter.emit(stage, STATUS_RUNNING,
                 f"running {module.STORY_TYPE} over {examined} {examined_unit}")
    result = run()
    refusals = tuple(_refusal_view(module.DETECTOR_ID, refusal)
                     for refusal in result.refusals)
    warned = sum(1 for candidate in result.candidates if candidate.warnings)
    outcome = DetectorOutcome(
        detector_id=module.DETECTOR_ID,
        detector_version=module.DETECTOR_VERSION,
        story_type=module.STORY_TYPE,
        examined=examined,
        examined_unit=examined_unit,
        candidates=tuple(result.candidates),
        refusals=refusals,
    )
    emitter.emit(
        stage, STATUS_PASSED,
        f"{len(result.candidates)} {module.STORY_TYPE} candidate(s) from {examined} "
        f"{examined_unit}; {len(refusals)} declined",
        processed=examined,
        accepted=len(result.candidates),
        refused=len(refusals),
        warnings=warned,
        highlights=[value
                    for candidate in result.candidates
                    for value in (*candidate.metric_ids, *candidate.anchor_period_keys,
                                  *candidate.anchor_observation_ids)]
                   + [metric_id for refusal in refusals for metric_id in refusal.metric_ids],
        detail={"reasons": _counted(refusal.reason for refusal in refusals)},
    )
    return outcome


def _refusal_view(detector_id: str, refusal: Any) -> DetectorRefusal:
    """One detector refusal, normalised (see `DetectorRefusal`).

    Read by attribute rather than branched on by class, because the three series detectors'
    refusal types are three distinct frozen dataclasses with one shape, and a `match` over them
    would need editing to *record* a fifth detector rather than only to understand it.
    """
    metric_ids = getattr(refusal, "metric_ids", None)
    if metric_ids is None:
        metric_ids = (getattr(refusal, "metric_id", ""),)
    return DetectorRefusal(
        detector_id=detector_id,
        reason=str(getattr(refusal, "reason", "")),
        detail=str(getattr(refusal, "detail", "")),
        metric_ids=tuple(str(value) for value in metric_ids if value),
        period_key=str(getattr(refusal, "period_key", "") or ""),
        period_shape=str(getattr(refusal, "period_shape", "") or ""),
        rule=str(getattr(refusal, "rule", "") or ""),
    )


def _history(emitter: _Emitter, points: Sequence[CanonicalPoint],
             authority: ComparabilityAuthority) -> MetricHistory:
    """What §6.10 scores *against*: each metric's own delta distribution and its slot facts."""
    emitter.emit(STAGE_METRIC_HISTORY, STATUS_RUNNING,
                 f"measuring each metric's own delta history over {len(points)} slots")
    history = build_metric_history(points, authority=authority)
    spreads = tuple(d for d in history.distributions.values() if d.characterises_a_spread)
    emitter.emit(
        STAGE_METRIC_HISTORY, STATUS_PASSED,
        f"{len(spreads)} of {len(history.distributions)} metric-and-shape distributions "
        f"characterise a spread; the rest are below §6.10's floor and yield no magnitude term",
        processed=len(points),
        accepted=len(spreads),
        refused=len(history.distributions) - len(spreads),
        highlights=sorted({metric_id for metric_id, _shape in history.distributions}),
        detail={"distributions": len(history.distributions), "slots": len(history.slots)},
    )
    return history


def _group(
    emitter: _Emitter,
    candidates: Sequence[StoryCandidate],
    history: MetricHistory,
    authority: ComparabilityAuthority,
) -> tuple[CandidateCluster, ...]:
    """§6.6 D3's clusters, computed here so the grouping is a step a reader can watch.

    Clustered per audience and in `AUDIENCE_ORDER`, which is exactly what `rank_candidates` does
    internally — an internal data-quality finding and an external story about one metric and one
    quarter are not one story. `rank_candidates` recomputes this; the duplication is deliberate
    and cheap (union-find over the run's candidates), and
    `tests/story/test_demo_ui_discovery.py` asserts the two agree, so the grouping the UI
    displays is provably the grouping the ranker used.
    """
    emitter.emit(STAGE_GROUPING, STATUS_RUNNING,
                 f"grouping {len(candidates)} candidates into stories")
    clusters = tuple(
        cluster
        for audience in sorted(AUDIENCE_ORDER, key=lambda item: AUDIENCE_ORDER[item])
        for cluster in deduplicate(
            [item for item in candidates if item.audience is audience],
            history, authority=authority)
    )
    collapsed = tuple(cluster for cluster in clusters if cluster.is_collapsed)
    suppressed = sum(len(cluster.member_ids) - 1 for cluster in collapsed)
    emitter.emit(
        STAGE_GROUPING, STATUS_PASSED,
        f"{len(clusters)} distinct stories from {len(candidates)} candidates; "
        f"{len(collapsed)} group(s) collapse {suppressed} candidate(s) into a representative",
        processed=len(candidates),
        accepted=len(clusters),
        refused=suppressed,
        highlights=[metric_id for cluster in collapsed
                    for metric_id in cluster.correlated_metric_ids]
                   + [cluster.anchor_period_key for cluster in collapsed],
        detail={"collapsed_groups": len(collapsed)},
    )
    return clusters


def _rank(
    emitter: _Emitter,
    candidates: Sequence[StoryCandidate],
    history: MetricHistory,
    authority: ComparabilityAuthority,
) -> RankingResult:
    """§6.10, over every candidate the four detectors produced.

    `accepted_history` is left empty because V1 has published nothing: there is no store of
    accepted posts in this repository, so §6.10's repetition term is exactly zero on every
    candidate today. Passing a fabricated store would be a made-up input to a real score.
    """
    emitter.emit(STAGE_RANKING, STATUS_RUNNING,
                 f"scoring {len(candidates)} candidates on §6.10's eight terms")
    ranking = rank_candidates(candidates, history, authority=authority)
    with_suppressed = sum(1 for row in ranking.ranked if row.suppressed)
    emitter.emit(
        STAGE_RANKING, STATUS_PASSED,
        f"{len(ranking.ranked)} candidates ranked over {len(ranking.representatives)} stories; "
        f"{with_suppressed} carry at least one score term that could not be computed",
        processed=len(candidates),
        accepted=len(ranking.ranked),
        refused=0,
        warnings=with_suppressed,
        highlights=[value for row in ranking.ranked[:HIGHLIGHT_LIMIT]
                    for value in (*row.candidate.metric_ids,
                                  *row.candidate.anchor_period_keys)],
        detail={"policy_version": RANKING_POLICY_VERSION,
                "suppressed_terms": _counted(
                    name for row in ranking.ranked for name in row.suppressed)},
    )
    return ranking


def _suggest(
    emitter: _Emitter,
    ranking: RankingResult,
    filters: DiscoveryFilters,
    detector_ids: frozenset[str],
    history: MetricHistory,
    periods: Mapping[str, str],
) -> tuple[tuple[Suggestion, ...], int]:
    """The filter, applied to the ranked list, with the reason each exclusion happened counted.

    Ordered by the ranking stage's own `ordering_key` rather than by anything invented here —
    `rank_candidates` already returns that order, and re-sorting through the same function is
    what makes *"the UI cannot reorder the ranking"* a property instead of a comment.
    """
    emitter.emit(STAGE_SUGGESTIONS, STATUS_RUNNING,
                 f"applying filters to {len(ranking.ranked)} ranked candidates")
    reasons: dict[str, int] = {}
    kept: list[tuple[int, RankedCandidate]] = []
    for position, row in enumerate(sorted(ranking.ranked, key=ordering_key), start=1):
        reason = _excluded_because(row, filters, detector_ids, history)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        kept.append((position, row))

    limit = max(0, filters.max_suggestions)
    suggestions = tuple(
        Suggestion(row=row, position=position, cluster_reasons=row.cluster.explain())
        for position, row in kept[:limit])
    warned = sum(1 for suggestion in suggestions if suggestion.candidate.warnings)
    emitter.emit(
        STAGE_SUGGESTIONS, STATUS_PASSED,
        f"{len(kept)} of {len(ranking.ranked)} candidates match; returning the top "
        f"{len(suggestions)}",
        processed=len(ranking.ranked),
        accepted=len(suggestions),
        refused=len(ranking.ranked) - len(kept),
        warnings=warned,
        highlights=[value for suggestion in suggestions
                    for value in (*suggestion.candidate.metric_ids,
                                  *suggestion.candidate.anchor_period_keys)],
        detail={"excluded_by": dict(sorted(reasons.items())),
                "periods_known": len(periods)},
    )
    return suggestions, len(kept)


def _excluded_because(
    row: RankedCandidate,
    filters: DiscoveryFilters,
    detector_ids: frozenset[str],
    history: MetricHistory,
) -> str:
    """The first filter this row fails, or `""`. Named so the UI can say *why* a list is short."""
    candidate = row.candidate
    if candidate.detector_id not in detector_ids:
        return "story_type"
    if filters.subject_entity_id and candidate.subject_entity_id != filters.subject_entity_id:
        return "subject"
    if filters.metric_ids and not set(candidate.metric_ids) & set(filters.metric_ids):
        return "metric"
    if filters.external_only and candidate.audience is not Audience.EXTERNAL:
        return "audience"
    if filters.min_score is not None and row.score.total < filters.min_score:
        return "min_score"
    if filters.period_from or filters.period_to:
        dates = _anchor_dates(candidate, history)
        if not dates:
            # Excluded rather than kept: a period filter is a claim about *when*, and a
            # candidate whose anchors resolve to no date cannot answer it either way.
            return "period_unresolved"
        if filters.period_from and max(dates) < filters.period_from:
            return "period"
        if filters.period_to and min(dates) > filters.period_to:
            return "period"
    return ""


def _anchor_dates(candidate: StoryCandidate, history: MetricHistory) -> tuple[str, ...]:
    """Every anchor date this candidate resolves to, through the run's own canonical slots."""
    return tuple(sorted({
        slot.anchor_date
        for metric_id in candidate.metric_ids
        for period_key in candidate.anchor_period_keys
        if (slot := history.slot(metric_id, period_key)) is not None and slot.anchor_date
    }))


def _counted(values: Any) -> dict[str, int]:
    """A tally, ordered by key so two runs produce one dict."""
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "DETECTOR_VERSIONS",
    "HIGHLIGHT_LIMIT",
    "STAGES",
    "TRACE_STAGES",
    "STAGE_ACCELERATION",
    "STAGE_CANONICAL_SERIES",
    "STAGE_COMPARABILITY",
    "STAGE_DIVERGENCE",
    "STAGE_FRESHNESS",
    "STAGE_GRAPH_SNAPSHOT",
    "STAGE_GROUPING",
    "STAGE_METRIC_HISTORY",
    "STAGE_METRIC_MOVE",
    "STAGE_RANKING",
    "STAGE_SUGGESTIONS",
    "STAGE_TREND_REVERSAL",
    "STATUSES",
    "STATUS_FAILED",
    "STATUS_PASSED",
    "STATUS_RUNNING",
    "STORY_TYPES",
    "ComparabilitySweep",
    "DetectorOutcome",
    "DetectorRefusal",
    "DiscoveryError",
    "DiscoveryEvent",
    "DiscoveryFilters",
    "DiscoveryResult",
    "EventSink",
    "GraphSource",
    "StaleGraphRefused",
    "Suggestion",
    "UnknownStoryType",
    "discover_from_context",
    "run_discovery",
]
