"""The in-memory run registry, and the patterns every client-supplied id is checked against.

Responsibility: what a run is while it is happening — its status, the trace events it has
emitted so far, the result it finished with — and safe lookup of one by id. Boundaries: no
HTTP, no filesystem, no database, no model. `server.py` imports this; this imports only
`trace.py`.

**In memory and nowhere else, on purpose.** §10 puts multi-user state out of scope and §14
forbids generated prose outside the demo run directory. A run here lives for as long as the
process does; the artifacts a run *produces* are written by the pipeline to
`data/story_demo/<story_run_id>/` exactly as the `demo` verb writes them, and this registry
holds the id of that directory rather than a second copy of its contents.

**Ids from the client are validated, never interpolated.** Two patterns, both anchored and both
bounded in length, live here because this module owns the run id and is the only place the two
rules can be read together. A run id that does not match is refused before it is used as a
dictionary key, and a candidate id that does not match is refused before it reaches a query
parameter. Neither is ever formatted into a string that goes anywhere but an error code.

**Thread safety.** One worker thread appends to a run while one or more SSE threads read it, so
every run carries a `threading.Condition`: appends notify, readers wait with a timeout and wake
on the next event or on the run finishing. The registry's own map has a separate lock, because
creating a run must not block a stream that is draining one.

**A failed run is failed.** `execute` is the only intended way to drive a run to a terminal
state: it marks the run running, calls the work, and on *any* exception marks it failed with an
error code. There is no path through it that leaves a run that raised looking complete, which
is the property the server's error handling depends on.
"""

from __future__ import annotations

import re
import threading
import traceback
from typing import Any, Callable, Iterator, Literal, Mapping

from .trace import Phase, TraceEmitter, TraceEvent

#: What a run is, from the outside. `queued` before a worker picks it up, `running` while one
#: holds it, and two terminal states. Derived from nothing — the registry sets it — but a run
#: cannot leave a terminal state, which `_set_status` enforces.
RunStatus = Literal["queued", "running", "complete", "failed"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"complete", "failed"})

#: `run-<phase>-<counter>`. Minted here, so the pattern and the minting cannot drift apart;
#: `test_demo_ui_server.py` mints one and matches it against this.
RUN_ID_PATTERN = re.compile(r"^run-(discovery|generation)-[0-9]{4,8}$")

#: `cand:<detector>:<scope>:<subject>:<anchor>:<digest12>` — the shape `story.core.keys`
#: actually mints, checked against the committed fixture rather than assumed. The anchor
#: segment allows upper case and `_` because it is `"_".join(sorted(period_keys))` and a period
#: key reads `2022Q3`; the digest segment is twelve lower-case hex characters.
#:
#: This is not the authority on whether a candidate exists — the detectors are. It is the
#: check that stops a string from the browser being carried any further than this function.
CANDIDATE_ID_PATTERN = re.compile(
    r"^cand:[a-z0-9-]{1,64}:[a-z0-9-]{1,120}:[a-z0-9-]{1,64}:[A-Za-z0-9_-]{1,64}:[0-9a-f]{12}$")

MAX_ID_LENGTH = 320


class InvalidIdentifier(ValueError):
    """A client-supplied id that did not match its pattern. Carries the *kind*, not the value.

    The value is deliberately not in the message: it came from outside and it ends up in a log
    line and, one careless format string later, in a response body.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(f"the supplied {kind} is not a well-formed identifier")
        self.kind = kind


class UnknownRun(LookupError):
    def __init__(self) -> None:
        super().__init__("no run with that id is registered in this process")


def validate_run_id(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_ID_LENGTH \
            or not RUN_ID_PATTERN.match(value):
        raise InvalidIdentifier("run id")
    return value


def validate_candidate_id(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_ID_LENGTH \
            or not CANDIDATE_ID_PATTERN.match(value):
        raise InvalidIdentifier("candidate id")
    return value


class Run:
    """One discovery or generation run, and the events it has emitted so far.

    Satisfies `trace.TraceSink` through `append`, which is how an instrumented stage reaches a
    run without importing the registry.
    """

    def __init__(self, run_id: str, phase: Phase) -> None:
        self.run_id = run_id
        self.phase = phase
        self._condition = threading.Condition()
        self._events: list[TraceEvent] = []
        self._status: str = "queued"
        self._result: Mapping[str, Any] | None = None
        self._error_code: str | None = None

    # -- the sink side, written by the worker -------------------------------------------

    def append(self, event: TraceEvent) -> None:
        if event.run_id != self.run_id:
            raise ValueError("a trace event was appended to a run it does not belong to")
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()

    def emitter(self, *, clock: Callable[[], str] | None = None) -> TraceEmitter:
        if clock is None:
            return TraceEmitter(run_id=self.run_id, phase=self.phase, sink=self)
        return TraceEmitter(run_id=self.run_id, phase=self.phase, sink=self, clock=clock)

    # -- status ---------------------------------------------------------------------------

    @property
    def status(self) -> str:
        with self._condition:
            return self._status

    @property
    def finished(self) -> bool:
        with self._condition:
            return self._status in TERMINAL_STATUSES

    def start(self) -> None:
        self._set_status("running")

    def complete(self, result: Mapping[str, Any]) -> None:
        with self._condition:
            self._require_not_terminal("complete")
            self._result = dict(result)
            self._status = "complete"
            self._condition.notify_all()

    def fail(self, error_code: str) -> None:
        """Terminal, and it takes a *code* rather than a message.

        The code is one of this package's own short tokens. An exception's own text can carry a
        path, a URL or a credential fragment, and a failed run's status is read by the browser.
        """
        with self._condition:
            self._require_not_terminal("fail")
            self._error_code = error_code
            self._status = "failed"
            self._condition.notify_all()

    def _set_status(self, status: str) -> None:
        with self._condition:
            self._require_not_terminal(status)
            self._status = status
            self._condition.notify_all()

    def _require_not_terminal(self, attempted: str) -> None:
        if self._status in TERMINAL_STATUSES:
            raise RuntimeError(
                f"the run is already {self._status} and cannot be moved to {attempted}")

    # -- the reader side, read by the streams and the endpoints ---------------------------

    def events(self) -> tuple[TraceEvent, ...]:
        with self._condition:
            return tuple(self._events)

    def since(self, sequence: int) -> tuple[TraceEvent, ...]:
        """Every event whose sequence is at or after `sequence`."""
        with self._condition:
            return tuple(e for e in self._events if e.sequence >= sequence)

    def result(self) -> Mapping[str, Any] | None:
        with self._condition:
            return self._result

    def error_code(self) -> str | None:
        with self._condition:
            return self._error_code

    def summary(self) -> dict[str, Any]:
        """What `GET /demo/runs/{run_id}` reports about the run itself.

        The result is not merged in here: an endpoint decides how much of it to return, and a
        summary that quietly carried the whole outcome would make every status poll a full
        response.
        """
        with self._condition:
            return {
                "run_id": self.run_id,
                "phase": self.phase,
                "status": self._status,
                "event_count": len(self._events),
                "error_code": self._error_code,
            }

    def wait(self, *, after: int, timeout: float) -> tuple[TraceEvent, ...]:
        """Block until an event at or after `after` exists, or until `timeout` elapses.

        Returns what it found, which may be empty — an empty return is how the streaming loop
        learns it should send a keep-alive and check whether the client is still there.
        """
        with self._condition:
            pending = [e for e in self._events if e.sequence >= after]
            if not pending and self._status not in TERMINAL_STATUSES:
                self._condition.wait(timeout)
                pending = [e for e in self._events if e.sequence >= after]
            return tuple(pending)

    def stream(self, *, poll_seconds: float = 1.0) -> Iterator[TraceEvent]:
        """Every event, in order, until the run is terminal and drained.

        A generator rather than a callback so the SSE handler owns the socket writes and this
        module owns nothing but the ordering. It yields nothing while waiting, so the caller's
        loop is what decides how often to send a keep-alive.
        """
        next_sequence = 0
        while True:
            pending = self.wait(after=next_sequence, timeout=poll_seconds)
            for event in pending:
                next_sequence = event.sequence + 1
                yield event
            if not pending and self.finished:
                return


class RunRegistry:
    """Run id to run, for this process.

    The counter is per phase, so the two streams' ids read as what they are and a discovery run
    can never be fetched through a generation route by accident.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._counters: dict[str, int] = {"discovery": 0, "generation": 0}

    def create(self, phase: Phase) -> Run:
        with self._lock:
            if phase not in self._counters:
                raise ValueError(f"a run cannot be created for the unknown phase {phase}")
            self._counters[phase] += 1
            run_id = f"run-{phase}-{self._counters[phase]:04d}"
            run = Run(run_id, phase)
            self._runs[run_id] = run
            return run

    def get(self, run_id: str) -> Run:
        """Validated first, then looked up. Never `self._runs[unchecked]`."""
        validated = validate_run_id(run_id)
        with self._lock:
            run = self._runs.get(validated)
        if run is None:
            raise UnknownRun()
        return run

    def ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._runs))

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)


def execute(
    run: Run,
    work: Callable[[TraceEmitter], Mapping[str, Any]],
    *,
    error_code: str = "run_failed",
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Drive a run to a terminal state, and to a *truthful* one.

    `work` receives the run's emitter and returns the result payload. Anything it raises marks
    the run failed and is swallowed here rather than propagated, because the caller is a worker
    thread whose exception nobody would see. The traceback goes to `on_error` — the server
    passes its own logger — and never to the run, so the browser learns the run failed and does
    not learn what the machine's filesystem looks like.
    """
    run.start()
    try:
        result = work(run.emitter())
    except BaseException:  # noqa: BLE001 - a worker thread's last chance to record anything
        if on_error is not None:
            on_error(traceback.format_exc())
        run.fail(error_code)
        return
    run.complete(result)


def start_background(
    run: Run,
    work: Callable[[TraceEmitter], Mapping[str, Any]],
    *,
    error_code: str = "run_failed",
    on_error: Callable[[str], None] | None = None,
) -> threading.Thread:
    """`execute` on a daemon thread, so the endpoint that started the run can return its id."""
    thread = threading.Thread(
        target=execute, args=(run, work),
        kwargs={"error_code": error_code, "on_error": on_error},
        name=f"demo-ui-{run.run_id}", daemon=True)
    thread.start()
    return thread


__all__ = [
    "CANDIDATE_ID_PATTERN",
    "RUN_ID_PATTERN",
    "TERMINAL_STATUSES",
    "InvalidIdentifier",
    "Run",
    "RunRegistry",
    "RunStatus",
    "UnknownRun",
    "execute",
    "start_background",
    "validate_candidate_id",
    "validate_run_id",
]
