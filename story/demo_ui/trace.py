"""The Process trace event, and the only code allowed to author one.

Responsibility: the shape of everything the trace panel and `trace_events.jsonl` can ever
carry, plus the emitter that appends an event to a run. Boundaries: no HTTP, no registry, no
database, no prompt and no model. It imports `story.core.models` for the frozen-model base and
nothing else first-party, so a detector or a packaging step can be instrumented without
dragging the server in.

**Why there is no free-text field, and how that is enforced rather than promised** (§3).

The panel is called *Process trace* and not *reasoning*, because a model's private
chain-of-thought is never shown here — it is never even routed here. The enforcement is
structural, in three parts:

1. `message` is a `computed_field`, not a field. There is no constructor argument to put prose
   in; the string is composed by `_compose_message` below from the stage label, the status, the
   counts and the *lengths* of the id lists. `TraceEvent(message="…")` raises, because
   `StoryModel` forbids extras, and `test_demo_ui_trace.py` asserts it.
2. Every other field is an id, an integer, or a member of a closed set. `phase` and `status`
   are `Literal`s; `stage` is validated against `STAGES_BY_PHASE`, which pins the *pairing* as
   well as the membership — `("discovery", "drafting")` is refused, not just an invented stage.
3. The same events are what `write_trace_events` serialises, so the artifact and the stream
   cannot diverge into two versions of what happened.

A summary derived from a schema-constrained model field (`len(draft.sentences)`, a finding's
`code`) reaches the panel as a *count* or an *id*, which is what §3's second clause allows.

**No clock in the digest path.** `timestamp` is display metadata and is deliberately the one
field that moves between two otherwise identical runs, so `without_timestamps` exists and every
equality assertion goes through it. Nothing here hashes anything, and nothing here reads a
clock except `TraceEmitter`, which takes its clock as an argument.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import Field, computed_field, model_validator

from story.core.models import StoryModel

#: The two halves of the demo, which are also the two SSE endpoints (§5) and the two kinds of
#: run the registry mints. A phase is not a decoration on a stage: it decides which stream an
#: event belongs to, so it is validated against the stage rather than beside it.
Phase = Literal["discovery", "generation"]

#: What a stage is doing. `passed`, `warning` and `failed` are outcomes of a check; `complete`
#: is the terminal state of the run as a whole and is emitted once, by the emitter's owner.
Status = Literal["queued", "running", "passed", "warning", "failed", "skipped", "complete"]

#: The closed stage set, keyed by the phase it belongs to. **This mapping is the enum** — a
#: `Literal` listing twenty-one names would state membership and lose the pairing, and the
#: pairing is the half that catches a generation stage emitted onto the discovery stream.
#:
#: The names are the pipeline's own steps and not a narration of them: `comparability` is
#: §6.2's rule, `cross_metric_comparison` is D4, and the five `checking_*` stages are §13's
#: deterministic checks in the order the verifier runs them. A stage exists here because code
#: instruments it; there is no stage for "thinking".
STAGES_BY_PHASE: Mapping[str, tuple[str, ...]] = {
    "discovery": (
        "loading_graph_snapshot",
        "canonical_series",
        "comparability",
        "metric_changes",
        "sign_reversals",
        "acceleration",
        "cross_metric_comparison",
        "grouping",
        "ranking",
        "preparing_suggestions",
    ),
    "generation": (
        "building_evidence_package",
        "resolving_primary_sources",
        "freshness",
        "planning",
        "binding_facts_and_citations",
        "drafting",
        "checking_numbers_and_units",
        "checking_metrics_and_periods",
        "checking_citation_support",
        "checking_causal_language",
        "rendering",
    ),
}

STAGES: tuple[str, ...] = tuple(
    stage for stages in STAGES_BY_PHASE.values() for stage in stages)

#: What a stage is called in the interface. Held here rather than in the browser because the
#: same string goes into `trace_events.jsonl`, and a label that existed only in JavaScript
#: would make the artifact and the panel two different accounts of the run.
STAGE_LABELS: Mapping[str, str] = {
    "loading_graph_snapshot": "loading graph snapshot",
    "canonical_series": "canonical series",
    "comparability": "comparability",
    "metric_changes": "metric changes",
    "sign_reversals": "sign reversals",
    "acceleration": "acceleration",
    "cross_metric_comparison": "cross-metric comparison",
    "grouping": "grouping",
    "ranking": "ranking",
    "preparing_suggestions": "preparing suggestions",
    "building_evidence_package": "building evidence package",
    "resolving_primary_sources": "resolving primary sources",
    "freshness": "freshness",
    "planning": "planning",
    "binding_facts_and_citations": "binding facts and citations",
    "drafting": "drafting",
    "checking_numbers_and_units": "checking numbers and units",
    "checking_metrics_and_periods": "checking metrics and periods",
    "checking_citation_support": "checking citation support",
    "checking_causal_language": "checking causal language",
    "rendering": "rendering",
}

#: The filename §2 names as the one new artifact this workstream adds.
TRACE_EVENTS_FILENAME = "trace_events.jsonl"

_SEPARATOR = " · "


class TraceProgress(StoryModel):
    """How far through a bounded piece of work a stage is.

    `total` is required, because a progress bar with no denominator is an animation. A stage
    that cannot say how much work it has does not emit progress at all.
    """

    completed: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _completed_within_total(self) -> "TraceProgress":
        if self.completed > self.total:
            raise ValueError(
                f"progress claims {self.completed} of {self.total} done, which is more work "
                f"than the stage said it had")
        return self


class TraceCounts(StoryModel):
    """What a stage measured. Every field optional and every field an integer.

    Optional because the four are not all meaningful in one place — a detector processes and
    accepts, a verifier refuses and warns — and a zero is a measurement while an absence is
    not. `_compose_message` renders only the fields that were set, so the panel never shows a
    fabricated `refused 0` for a stage that refuses nothing.
    """

    processed: int | None = Field(default=None, ge=0)
    accepted: int | None = Field(default=None, ge=0)
    refused: int | None = Field(default=None, ge=0)
    warnings: int | None = Field(default=None, ge=0)


class GraphHighlights(StoryModel):
    """Ids the view should light up. **Never invented** (§3).

    This type cannot check that an id exists — it has no graph. What it does is make the
    highlight a list of ids rather than a description of a region, so the discovery stage's
    own test can resolve every entry against the projection, the package or the candidate.
    """

    node_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()


class TraceEvent(StoryModel):
    """One instrumented moment in a run, frozen, with no room in it for prose.

    `sequence` is not in §3's list and is here on purpose: it is the SSE `id:` field, it is
    what `Run.since` resumes from, and it gives the tests a total order that does not depend on
    the clock. It is assigned by the emitter, monotonically per run.
    """

    run_id: str = Field(min_length=1, max_length=64)
    phase: Phase
    stage: str
    status: Status
    sequence: int = Field(ge=0)
    #: ISO-8601 UTC, display metadata only. Excluded from `without_timestamps`, which is what
    #: every equality assertion uses; see the module docstring.
    timestamp: str = Field(min_length=1, max_length=40)
    progress: TraceProgress | None = None
    counts: TraceCounts | None = None
    graph_highlights: GraphHighlights = GraphHighlights()
    related_fact_ids: tuple[str, ...] = ()
    related_sentence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _stage_belongs_to_the_phase(self) -> "TraceEvent":
        stages = STAGES_BY_PHASE.get(self.phase, ())
        if self.stage not in stages:
            raise ValueError(
                f"{self.stage} is not a stage of the {self.phase} phase; the closed set is "
                f"{', '.join(stages)}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def message(self) -> str:
        """The panel's line, composed here and nowhere else. Read the module docstring."""
        return _compose_message(self)

    @property
    def label(self) -> str:
        return STAGE_LABELS[self.stage]


def _compose_message(event: TraceEvent) -> str:
    """Stage label, status, and whatever the event actually measured.

    Lengths rather than the ids themselves: a message naming forty fact ids is unreadable, and
    the ids are already on the event for the view to resolve. The parts are joined in a fixed
    order so two runs that measured the same thing produce the same string.
    """
    parts = [event.label, event.status]
    if event.progress is not None:
        parts.append(f"{event.progress.completed}/{event.progress.total}")
    counts = event.counts
    if counts is not None:
        for name in ("processed", "accepted", "refused", "warnings"):
            value = getattr(counts, name)
            if value is not None:
                parts.append(f"{name} {value}")
    highlights = event.graph_highlights
    if highlights.node_ids:
        parts.append(f"{len(highlights.node_ids)} nodes")
    if highlights.edge_ids:
        parts.append(f"{len(highlights.edge_ids)} edges")
    if event.related_fact_ids:
        parts.append(f"{len(event.related_fact_ids)} facts")
    if event.related_sentence_ids:
        parts.append(f"{len(event.related_sentence_ids)} sentences")
    return _SEPARATOR.join(parts)


class TraceSink(Protocol):
    """Where an emitted event goes. `story.demo_ui.runs.Run` satisfies it.

    A protocol so a detector can be instrumented and driven in a test with a list-backed sink
    and no server, which is the same argument `story/contracts.py` makes for every stage.
    """

    def append(self, event: TraceEvent) -> None: ...


def utc_now() -> str:
    """The default clock, in one place so a test can pass a different one."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


class TraceEmitter:
    """Mints `TraceEvent`s for one run and appends them to one sink.

    Holds the sequence counter, the run id and the phase, so an instrumented stage passes only
    what it measured. The clock is an argument because the emitter is the single place in this
    package that reads one, and a test that wants a fixed timestamp should not have to patch a
    module.

    Not itself locked: one worker thread emits for one run, and the sink is what is shared
    with the streaming threads. `Run.append` is where the lock is.
    """

    def __init__(
        self,
        *,
        run_id: str,
        phase: Phase,
        sink: TraceSink,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._run_id = run_id
        self._phase = phase
        self._sink = sink
        self._clock = clock
        self._sequence = 0

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def run_id(self) -> str:
        return self._run_id

    def emit(
        self,
        stage: str,
        status: Status,
        *,
        progress: TraceProgress | None = None,
        counts: TraceCounts | None = None,
        node_ids: Sequence[str] = (),
        edge_ids: Sequence[str] = (),
        fact_ids: Sequence[str] = (),
        sentence_ids: Sequence[str] = (),
    ) -> TraceEvent:
        event = TraceEvent(
            run_id=self._run_id,
            phase=self._phase,
            stage=stage,
            status=status,
            sequence=self._sequence,
            timestamp=self._clock(),
            progress=progress,
            counts=counts,
            graph_highlights=GraphHighlights(
                node_ids=tuple(node_ids), edge_ids=tuple(edge_ids)),
            related_fact_ids=tuple(fact_ids),
            related_sentence_ids=tuple(sentence_ids),
        )
        self._sequence += 1
        self._sink.append(event)
        return event


def event_payload(event: TraceEvent) -> dict[str, Any]:
    """The JSON form, with `message` included because it is a computed field."""
    return event.model_dump(mode="json")


def without_timestamps(events: Iterable[TraceEvent]) -> tuple[dict[str, Any], ...]:
    """Every event as JSON with `timestamp` removed — the comparison determinism uses.

    The demo manifest already excludes `created_at` from its determinism comparison for the
    same reason. Two runs over one graph must produce the same trace; they cannot produce it at
    the same instant, and asserting on the raw payloads would make a passing test a statement
    about how fast the machine is.
    """
    stripped = []
    for event in events:
        payload = event_payload(event)
        payload.pop("timestamp", None)
        stripped.append(payload)
    return tuple(stripped)


def write_trace_events(path: Path, events: Iterable[TraceEvent]) -> Path:
    """Write `trace_events.jsonl`, staged and renamed into place.

    Atomic finalisation for the same reason every other artifact in this repository has it: a
    half-written trace that looked complete would be a run reporting fewer stages than it ran.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".partial")
    with staged.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event_payload(event), sort_keys=True,
                                    ensure_ascii=False) + "\n")
    os.replace(staged, path)
    return path


__all__ = [
    "STAGES",
    "STAGES_BY_PHASE",
    "STAGE_LABELS",
    "TRACE_EVENTS_FILENAME",
    "GraphHighlights",
    "Phase",
    "Status",
    "TraceCounts",
    "TraceEmitter",
    "TraceEvent",
    "TraceProgress",
    "TraceSink",
    "event_payload",
    "utc_now",
    "without_timestamps",
    "write_trace_events",
]
