"""The trace contract — §3's "no hidden chain-of-thought" as a set of executable properties.

Offline, no server, no database. Everything here drives `TraceEvent` and `TraceEmitter` through
the surface an instrumented stage uses, rather than asserting their shape.

**The load-bearing test in this file is `test_no_field_accepts_free_text`.** It is not a style
check: it enumerates the model's declared fields and fails the day someone adds a `detail: str`
or a `note: str` — which is exactly how a panel that promised to show only instrumented
operations would start showing a model's prose. The allowlist below is short and every entry
has a reason written beside it.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from story.demo_ui.trace import (
    STAGE_LABELS,
    STAGES,
    STAGES_BY_PHASE,
    GraphHighlights,
    TraceCounts,
    TraceEmitter,
    TraceEvent,
    TraceProgress,
    event_payload,
    without_timestamps,
    write_trace_events,
)


class ListSink:
    """A `TraceSink` that is a list. The protocol is structural; nothing subclasses anything."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def append(self, event: TraceEvent) -> None:
        self.events.append(event)


def emitter(phase: str = "discovery", *, sink: ListSink | None = None,
            clock=lambda: "2026-08-04T00:00:00.000Z") -> tuple[TraceEmitter, ListSink]:
    target = sink if sink is not None else ListSink()
    return TraceEmitter(run_id="run-discovery-0001", phase=phase, sink=target,
                        clock=clock), target


# -- the closed vocabularies -------------------------------------------------------------


def test_every_stage_carries_a_label_and_no_stage_is_declared_twice():
    assert len(STAGES) == len(set(STAGES))
    assert set(STAGES) == set(STAGE_LABELS)
    assert set(STAGES_BY_PHASE) == {"discovery", "generation"}


def test_the_declared_stages_are_the_ones_the_brief_names():
    """Pinned, so a stage cannot be added or renamed without the change being seen.

    The count is the assertion that matters — ten discovery stages and eleven generation ones —
    because a scan that discovered its own corpus could pass by discovering nothing.
    """
    assert len(STAGES_BY_PHASE["discovery"]) == 10
    assert len(STAGES_BY_PHASE["generation"]) == 11
    assert STAGES_BY_PHASE["discovery"][0] == "loading_graph_snapshot"
    assert STAGES_BY_PHASE["generation"][-1] == "rendering"


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_is_emittable_on_exactly_one_phase(stage):
    phases = [phase for phase, stages in STAGES_BY_PHASE.items() if stage in stages]
    assert len(phases) == 1
    emit, sink = emitter(phases[0])
    emit.emit(stage, "running")
    assert sink.events[-1].stage == stage


def test_a_stage_from_the_other_phase_is_refused():
    emit, _ = emitter("discovery")
    with pytest.raises(ValidationError):
        emit.emit("drafting", "running")


def test_an_invented_stage_a_phase_or_a_status_is_refused():
    emit, _ = emitter()
    with pytest.raises(ValidationError):
        emit.emit("reasoning", "running")
    with pytest.raises(ValidationError):
        TraceEvent(run_id="r", phase="thinking", stage="ranking", status="running",
                   sequence=0, timestamp="t")
    with pytest.raises(ValidationError):
        TraceEvent(run_id="r", phase="discovery", stage="ranking", status="pondering",
                   sequence=0, timestamp="t")


# -- §3: there is nowhere to put prose ----------------------------------------------------

#: Every field whose value is or contains a plain string, and why it cannot be free text.
#: `stage` is validated against the closed set; `run_id` is minted by the registry and matched
#: against `RUN_ID_PATTERN`; `timestamp` comes from the emitter's clock; the three id tuples
#: hold ids that §3's resolution test checks against the run's own output.
_STRING_FIELDS = {
    "run_id", "phase", "stage", "status", "timestamp",
    "related_fact_ids", "related_sentence_ids",
}


def test_no_field_accepts_free_text():
    declared = set(TraceEvent.model_fields)
    assert declared == _STRING_FIELDS | {
        "sequence", "progress", "counts", "graph_highlights"}


def test_message_is_computed_and_cannot_be_supplied():
    """The enforcement, not the promise: there is no `message` argument to pass."""
    assert "message" not in TraceEvent.model_fields
    with pytest.raises(ValidationError):
        TraceEvent(run_id="r", phase="discovery", stage="ranking", status="running",
                   sequence=0, timestamp="t",
                   message="I first considered the margin, then decided")


def test_the_message_is_composed_from_counts_progress_and_id_counts():
    emit, sink = emitter()
    emit.emit("cross_metric_comparison", "passed",
              progress=TraceProgress(completed=262, total=262),
              counts=TraceCounts(processed=262, accepted=3, refused=1),
              node_ids=("n1", "n2"), edge_ids=("e1",), fact_ids=("f1", "f2", "f3"))
    assert sink.events[0].message == (
        "cross-metric comparison · passed · 262/262 · processed 262 · accepted 3 · "
        "refused 1 · 2 nodes · 1 edges · 3 facts")


def test_an_absent_count_is_not_rendered_as_a_zero():
    """A stage that refuses nothing must not appear to have refused nothing on purpose."""
    emit, sink = emitter()
    emit.emit("ranking", "passed", counts=TraceCounts(accepted=3))
    assert "refused" not in sink.events[0].message
    assert sink.events[0].counts.refused is None


def test_the_computed_message_reaches_the_serialised_payload():
    emit, sink = emitter()
    emit.emit("ranking", "complete")
    assert event_payload(sink.events[0])["message"] == "ranking · complete"


# -- the numbers are numbers ---------------------------------------------------------------


def test_progress_cannot_claim_more_work_done_than_it_had():
    with pytest.raises(ValidationError):
        TraceProgress(completed=9, total=8)


def test_counts_and_progress_refuse_negatives():
    with pytest.raises(ValidationError):
        TraceCounts(processed=-1)
    with pytest.raises(ValidationError):
        TraceProgress(completed=-1, total=0)


def test_an_event_is_frozen():
    emit, sink = emitter()
    event = emit.emit("ranking", "running")
    with pytest.raises(ValidationError):
        event.status = "complete"


# -- the emitter ---------------------------------------------------------------------------


def test_sequences_are_monotonic_from_zero_and_the_sink_sees_every_event():
    emit, sink = emitter()
    for stage in STAGES_BY_PHASE["discovery"][:4]:
        emit.emit(stage, "passed")
    assert [event.sequence for event in sink.events] == [0, 1, 2, 3]
    assert [event.run_id for event in sink.events] == ["run-discovery-0001"] * 4


def test_highlights_default_to_empty_rather_than_to_something_plausible():
    emit, sink = emitter()
    emit.emit("grouping", "running")
    assert sink.events[0].graph_highlights == GraphHighlights()
    assert sink.events[0].related_fact_ids == ()


# -- determinism, with the clock left out --------------------------------------------------


def test_without_timestamps_is_what_two_runs_can_be_compared_on():
    """The same instrumented sequence twice, on two different clocks."""
    first, first_sink = emitter(clock=lambda: "2026-08-04T00:00:00.000Z")
    second, second_sink = emitter(clock=lambda: "2027-01-01T12:34:56.789Z")
    for emit in (first, second):
        emit.emit("canonical_series", "passed", counts=TraceCounts(processed=41))
        emit.emit("ranking", "complete")

    assert [e.timestamp for e in first_sink.events] != [e.timestamp for e in second_sink.events]
    assert without_timestamps(first_sink.events) == without_timestamps(second_sink.events)
    assert all("timestamp" not in payload for payload in without_timestamps(first_sink.events))
    assert all("message" in payload for payload in without_timestamps(first_sink.events))


# -- the artifact --------------------------------------------------------------------------


def test_trace_events_are_written_as_one_json_object_per_line(tmp_path):
    emit, sink = emitter()
    emit.emit("loading_graph_snapshot", "passed")
    emit.emit("ranking", "complete", counts=TraceCounts(accepted=3))
    path = write_trace_events(tmp_path / "trace_events.jsonl", sink.events)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert [row["stage"] for row in rows] == ["loading_graph_snapshot", "ranking"]
    assert rows[1]["message"] == "ranking · complete · accepted 3"
    # Staged and renamed: nothing partial is left behind for a reader to mistake for the file.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["trace_events.jsonl"]


def test_a_rewritten_trace_replaces_rather_than_appends(tmp_path):
    emit, sink = emitter()
    emit.emit("ranking", "complete")
    path = tmp_path / "trace_events.jsonl"
    write_trace_events(path, sink.events)
    write_trace_events(path, sink.events)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
