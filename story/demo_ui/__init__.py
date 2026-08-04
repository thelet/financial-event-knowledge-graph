"""The interactive demo interface over the accepted story-agent pipeline.

Responsibility: showing a real run happening — the graph it reads, the candidates the detectors
found, the evidence package a candidate produced, the prompts, the draft, and the deterministic
verifier's decision. Boundaries, and they run one way only: **this package imports
`story.pipeline`, `story.stages.*`, `story.core.*` and `story.contracts`, and none of them may
import it.** `tests/story/test_demo_ui_server.py` asserts that direction rather than describing
it, so the interface can never become load-bearing for the pipeline.

This module re-exports the skeleton: the trace contract, the run registry and the server. §5's
endpoints live in `api.py` and register themselves with `server.ROUTER`; nothing here imports
them, because a public surface that imported every handler would make the import graph decide
which endpoints exist.

**What is a mockup here: nothing.** Every number, highlight and trace event resolves from a
real artifact or a real read-only query, and the one thing the panel will never show is a
model's private reasoning — see `trace.py` for how that is a property of the types rather than
a promise in a docstring.
"""

from __future__ import annotations

from .runs import (
    CANDIDATE_ID_PATTERN,
    RUN_ID_PATTERN,
    InvalidIdentifier,
    Run,
    RunRegistry,
    UnknownRun,
    execute,
    start_background,
    validate_candidate_id,
    validate_run_id,
)
from .server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_BODY_BYTES,
    ROUTER,
    DemoUiApp,
    DemoUiError,
    EventStream,
    JsonResponse,
    Request,
    Router,
    SseMessage,
    build_app,
    build_server,
    register,
    resolve_static,
    serve,
    trace_stream,
)
from .trace import (
    STAGE_LABELS,
    STAGES,
    STAGES_BY_PHASE,
    TRACE_EVENTS_FILENAME,
    GraphHighlights,
    TraceCounts,
    TraceEmitter,
    TraceEvent,
    TraceProgress,
    TraceSink,
    event_payload,
    without_timestamps,
    write_trace_events,
)

__all__ = [
    "CANDIDATE_ID_PATTERN",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_BODY_BYTES",
    "ROUTER",
    "RUN_ID_PATTERN",
    "STAGES",
    "STAGES_BY_PHASE",
    "STAGE_LABELS",
    "TRACE_EVENTS_FILENAME",
    "DemoUiApp",
    "DemoUiError",
    "EventStream",
    "GraphHighlights",
    "InvalidIdentifier",
    "JsonResponse",
    "Request",
    "Router",
    "Run",
    "RunRegistry",
    "SseMessage",
    "TraceCounts",
    "TraceEmitter",
    "TraceEvent",
    "TraceProgress",
    "TraceSink",
    "UnknownRun",
    "build_app",
    "build_server",
    "event_payload",
    "execute",
    "register",
    "resolve_static",
    "serve",
    "start_background",
    "trace_stream",
    "validate_candidate_id",
    "validate_run_id",
    "without_timestamps",
    "write_trace_events",
]
