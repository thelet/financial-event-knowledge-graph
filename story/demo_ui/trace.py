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
#:
#: **`metric_history` was added 2026-08-05, after an adversarial review measured the cost of its
#: absence.** `story/demo_ui/discovery.py` builds each metric's own delta distribution before it
#: groups or ranks anything, and with no name for that step it mapped the events onto `ranking`
#: — so the live stream emitted `ranking` twice, before and after `grouping`, with different
#: numbers, and a viewer saw ranking run twice. A stage exists here because code instruments it;
#: this one does, and borrowing another stage's name to report it was the dishonest option.
#:
#: **Three derivation stages were added 2026-08-19 (DETERMINISTIC_FACT_TOOLS §8), and the plan
#: asked for four.** `offering_derivations` is `offers(package, candidate)`, which runs before
#: the planner and decides what it may ask for; `executing_derivations` is the plan's requests
#: validated and computed; `derived_facts_added` is what entered the writer's trusted context,
#: §7's evidence-scope facts included.
#:
#: §8's fourth name, `validating_derivations`, is **deliberately not here**. `execute.py`
#: validates and computes in one call per request — `execute()` runs §4.2's clauses and returns
#: either a `DerivedFact` or a `DerivationRefusal`, so every refusal *is* a validation outcome
#: and every granted fact passed every clause. A `validating_derivations` row would therefore
#: carry the same three numbers as `executing_derivations` for every run that can exist, and
#: this table's own rule is that a stage exists because code instruments it. Two rows reporting
#: one measurement is the `ranking`-twice defect with the names swapped. What validation did is
#: reported where it happened: `executing_derivations · failed · refused N` is a refusal at
#: §4.2, and the refusal codes are in `derived_facts.json` and on the rejection payload.
STAGES_BY_PHASE: Mapping[str, tuple[str, ...]] = {
    "discovery": (
        "loading_graph_snapshot",
        "canonical_series",
        "comparability",
        "metric_changes",
        "sign_reversals",
        "acceleration",
        "cross_metric_comparison",
        "metric_history",
        "grouping",
        "ranking",
        "preparing_suggestions",
    ),
    "generation": (
        "building_evidence_package",
        "resolving_primary_sources",
        "freshness",
        "planning",
        "offering_derivations",
        "executing_derivations",
        "derived_facts_added",
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
    "metric_history": "metric history",
    "grouping": "grouping",
    "ranking": "ranking",
    "preparing_suggestions": "preparing suggestions",
    "building_evidence_package": "building evidence package",
    "resolving_primary_sources": "resolving primary sources",
    "freshness": "freshness",
    "planning": "planning",
    "offering_derivations": "offering derivations",
    "executing_derivations": "executing derivations",
    "derived_facts_added": "derived facts added",
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


#: What a count may be counting. **A closed set, and that is the whole point of the field.**
#:
#: Added 2026-08-05 because three discovery rows rendered two populations as one number —
#: `cross-metric comparison · processed 5 · refused 85` reads as a refusal rate of 1700%, when
#: the 5 are *declared metric pairs* and the 85 are *declines*. A count with no unit is not a
#: measurement a reader can check, and the fix is the unit rather than a reworked denominator:
#: `examined` and `refused` genuinely count different things at every detector, and forcing them
#: onto one population would lose the smaller number rather than explain it.
#:
#: Closed, not free text, for §3's reason: a `str` field on a trace model is exactly the seam
#: prose gets in through. Every member below is written here, by us, and a value outside the set
#: is a validation error rather than a rendered sentence.
#: **The four derivation units are four populations and not four names for one** (§8). An offer
#: is a triple code would grant if asked; a request is one the plan actually made; a derived fact
#: is one that came back; an evidence-scope fact was never requested at all, because absence is
#: not a calculation. `derivations_offered 12 · derivations_requested 1` is the sentence the
#: manifest's own `_counts` docstring argues for — *"a run that recorded only 'one derived fact'
#: could not say whether the planner chose one of twelve or one of one"* — and it is unreadable
#: unless each number says which population it counted.
COUNT_UNITS: frozenset[str] = frozenset({
    "offered derivations",
    "requested derivations",
    "derived facts",
    "evidence-scope facts",
    "freshness checks",
    "bounded reads",
    "observations",
    "metrics",
    "fact slots",
    "adjacent same-shape pairs",
    "metric series",
    "declared metric pairs",
    "candidates",
    "declines",
    "canonical slots",
    "metric-and-shape distributions",
    "stories",
})


class TraceCounts(StoryModel):
    """What a stage measured. Every count optional, every count an integer, each with its unit.

    Optional because the four are not all meaningful in one place — a detector processes and
    accepts, a verifier refuses and warns — and a zero is a measurement while an absence is
    not. `_compose_message` renders only the fields that were set, so the panel never shows a
    fabricated `refused 0` for a stage that refuses nothing.

    The four `*_unit` fields name the population each number counted, drawn from `COUNT_UNITS`
    and from nowhere else. An empty unit renders as it always did — a bare `processed 41` — so a
    stage whose four counts share one obvious population need not say so four times.
    """

    processed: int | None = Field(default=None, ge=0)
    accepted: int | None = Field(default=None, ge=0)
    refused: int | None = Field(default=None, ge=0)
    warnings: int | None = Field(default=None, ge=0)
    processed_unit: str = ""
    accepted_unit: str = ""
    refused_unit: str = ""
    warnings_unit: str = ""

    @model_validator(mode="after")
    def _units_are_from_the_closed_set(self) -> "TraceCounts":
        for name in ("processed_unit", "accepted_unit", "refused_unit", "warnings_unit"):
            unit = getattr(self, name)
            if unit and unit not in COUNT_UNITS:
                raise ValueError(
                    f"{unit!r} is not one of the units this trace declares; the closed set is "
                    f"{', '.join(sorted(COUNT_UNITS))}")
        return self


class GraphHighlights(StoryModel):
    """Ids the view should light up. **Never invented** (§3), and separated by what they are.

    This type cannot check that an id exists — it has no graph. What it does is make the
    highlight a list of ids rather than a description of a region, so the discovery stage's
    own test can resolve every entry against the projection, the package or the candidate.

    **`period_keys` was split out of `node_ids` on 2026-08-05, measured rather than argued.**
    Eight of discovery's twelve stages highlight period keys (`2019Q4`,
    `2020-01-01_2020-06-30`) alongside metric and observation ids, and the composed message said
    `24 nodes` for a list in which — measured against the overview payload the client draws —
    comparability resolved 0 of 24 and grouping 0 of 24. No id was invented, so §3's rule held;
    the *count* was still a claim about nodes that most of the list did not support.
    A period key is a real entity, but of the **coverage** projection, where it is a node whose
    id is `period:<key>` — so it travels in its own field, is counted as its own thing, and the
    view maps it to that projection's id form rather than looking for it among the metrics.
    """

    node_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    #: Canonical period keys, **unprefixed**. The coverage projection renders each as
    #: `period:<key>`; the raw key is what the pipeline's own values are, so that is what is
    #: carried and the prefixing belongs to whoever draws the projection.
    period_keys: tuple[str, ...] = ()


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
                unit = getattr(counts, f"{name}_unit")
                parts.append(f"{name} {value} {unit}" if unit else f"{name} {value}")
    highlights = event.graph_highlights
    if highlights.node_ids:
        parts.append(f"{len(highlights.node_ids)} nodes")
    if highlights.period_keys:
        # Not "nodes". See `GraphHighlights` — these resolve in the coverage projection, under
        # a different id form, and counting them as nodes was the claim that had to go.
        parts.append(f"{len(highlights.period_keys)} period keys")
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
        period_keys: Sequence[str] = (),
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
                node_ids=tuple(node_ids), edge_ids=tuple(edge_ids),
                period_keys=tuple(period_keys)),
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
    "COUNT_UNITS",
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
