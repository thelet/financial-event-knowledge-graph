"""§5's endpoints: plain dicts over the accepted pipeline, and the two runs that stream.

Responsibility: turn one HTTP request into one call against something that already exists —
a projection, a discovery run, the §8b demo pipeline — and turn what it returned into a
JSON-serialisable dict. It holds no threshold, no score, no verification rule and no prompt
text of its own; every number below is read off a value a stage returned.

Boundaries: it imports `server.py` for the request and response types, `runs.py` for the run
registry and the two id patterns, `trace.py` for the event contract, and the four sibling
modules that do the work (`projection`, `discovery`, `code_catalogue`, `prompt_presets`). It
opens no socket, it builds no driver, and it reads nothing from the process environment.

**§6 belongs to `prompt_presets` entirely and is not restated here.** Which keys a request may
carry, the per-field and total bounds, the composition of the fixed sections with the editable
ones, `requires_live`, the diffs, the advisory scan and the caveats are all that module's, and
this one supplies exactly one value it cannot know — `DemoConfig.length_target`, the baseline
`requires_live` is compared against. The only prompt logic owned here is `ObservedProvider`,
and its docstring says what each of its three jobs is for.

**Two corrections to the plan, both forced by a committed test rather than by preference.**

1. **These endpoints are registered by an explicit call, not as an import side effect.**
   INTERACTIVE_DEMO_UI §1 and `server.py`'s docstring both say `api.py` calls `register(...)`
   at import time. It cannot: `tests/story/test_demo_ui_server.py::
   test_the_process_wide_router_carries_the_liveness_route_and_nothing_of_section_five`
   asserts the process-wide `ROUTER` holds no `/demo/` template, and pytest imports every test
   module before it runs any of them — so an import-time registration would make that test
   pass or fail on collection order. `register_endpoints()` is the seam instead, it is
   idempotent, and it defaults to the process-wide router so the composition root's call is
   one line. Import order deciding which endpoints exist is exactly what `Router.register`'s
   duplicate check already refuses; this keeps that property.

2. **`story.pipeline` arrives through `services`, like the context and the config.** Measured
   2026-08-04: `story/pipeline.py` names `story.context` under `TYPE_CHECKING`, and
   `tests/story/test_story_package_structure.py::
   test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage` walks guarded imports
   exactly as it walks plain ones. An `import story.pipeline` in this file therefore puts
   `story.providers.neo4j_connection imports neo4j` into the closure of a module set that must
   stay driver-free, and the suite fails. Verified by probing that test's own `closure()`
   against this package with and without the import. The plan's argument for passing
   `story_context` as a factory is the same argument, one hop further out, so this module asks
   for a third factory under `story_pipeline` and refuses cleanly when it is absent. The
   composition root — `story/cli.py:cmd_ui` — is the only place that may name the module.

**What is never in a response, and each is a test.** `GenerationResult.raw_content`, which is
the model's unstructured output and the one thing §3's no-chain-of-thought rule is about; the
text of any exception, because a provider failure carries a URL and a filesystem failure
carries a path; anything read from the process environment, which this module never reads;
and any absolute path — a run directory is reported relative to the repository root.

**No Cypher from the browser.** The client names a projection or a view and passes bound
parameters. Every statement is a module-level constant in `projection.py`, scanned by
`tests/story/test_story_retrieval_cypher.py` under every §16 rule, and nothing client-supplied
reaches one.

**Zeroes on a replay are reported as "not measured", never as zero.** `_token_totals` sums
`GenerationResult` fields that a replayed row deliberately does not carry, and
`RetrievalTraceEntry.elapsed_ms` is pinned to `TRACE_ELAPSED_MS_NOT_CARRIED` so a wall-clock
reading cannot break `package_content_digest`. Rendering either as `0` would put a cost panel
in front of an investor claiming a run was free. `_cost_panel` returns `null` with the reason.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from story.stages.generation import PLANNER_SCHEMA_NAME, WRITER_SCHEMA_NAME
from story.stages.generation.prompts import PLAIN_INVESTOR_STYLE, writer_system

from . import code_catalogue, discovery, projection, prompt_presets
from .runs import InvalidIdentifier, Run, UnknownRun, validate_candidate_id
from .server import (
    ROUTER,
    EventStream,
    JsonResponse,
    Request,
    Router,
    trace_stream,
)
from .trace import (
    STAGES_BY_PHASE,
    TRACE_EVENTS_FILENAME,
    TraceCounts,
    TraceEmitter,
    write_trace_events,
)

# ---------------------------------------------------------------------------------------
# The error vocabulary. A closed table, exactly as `server.py` keeps one, and for the same
# reason: an error is rendered from a code and never from an exception's `str()`.
# ---------------------------------------------------------------------------------------

#: code -> (status, the sentence the client sees). Nothing outside this table can be returned,
#: so no message can carry a path, a URL or a credential fragment that a stage raised with.
ERRORS: Mapping[str, tuple[int, str]] = {
    "bad_request": (400, "the request could not be understood"),
    "invalid_identifier": (400, "an identifier in the request is not well formed"),
    "missing_field": (400, "a required field is absent from the request body"),
    "unknown_projection": (400, "that projection is not one this demo offers"),
    "unknown_view": (400, "that subgraph view is not one this demo offers"),
    "unknown_story_type": (400, "that story type is not one any detector in this run produces"),
    "invalid_prompt_request": (400, "a prompt field in the request body was refused; the "
                                    "presets endpoint states every bound"),
    "wrong_phase": (400, "that run belongs to the other half of the demo"),
    "field_too_long": (413, "a text field in the request is longer than this server accepts"),
    "unknown_candidate": (404, "no candidate with that id is held by this process; run "
                               "discovery first"),
    "unknown_run": (404, "no run with that id is registered in this process"),
    "candidate_not_reproducible": (404, "the detectors did not reproduce that candidate from "
                                        "this graph run"),
    "stale_graph": (409, "the freshness gate refused the loaded graph"),
    "edited_prompt_requires_live": (409, "an edited prompt is a different request and is not "
                                         "in the recorded store; ask for a live run"),
    "generation_not_recorded": (409, "the recorded store holds no answer for this request; ask "
                                     "for a live run"),
    "graph_unavailable": (503, "the graph is not reachable from this process"),
    "pipeline_unavailable": (503, "the demo pipeline was not supplied to this server"),
    "prompt_presets_unavailable": (503, "the prompt presets module is not present in this "
                                        "tree"),
    "generation_store_empty": (503, "the recorded answer store holds no generation; ask for a "
                                   "live run to call the model server instead"),
    "provider_unavailable": (503, "the model provider could not be constructed"),
    "fixed_section_missing": (500, "the fixed factual-safety rules were not present in the "
                                   "message a stage handed the provider; nothing was sent"),
    "internal_error": (500, "the request failed inside the server; the server log has why"),
}

#: What may travel beside a code — a short token naming which parameter or stage was at fault.
#: Anything else is dropped rather than truncated, because a partially sanitised string is the
#: shape a leak takes. The same rule and the same character class `server.py` applies.
_DETAIL_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")


class ApiError(Exception):
    """An error with a code from `ERRORS`. The status and the sentence come from the table."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code if code in ERRORS else "internal_error"
        self.status = ERRORS[self.code][0]
        self.detail = detail if detail and _DETAIL_PATTERN.match(detail) else None

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code, "message": ERRORS[self.code][1], "status": self.status}
        if self.detail is not None:
            body["detail"] = self.detail
        return body

    def response(self) -> JsonResponse:
        return JsonResponse({"error": self.payload()}, status=self.status)


# ---------------------------------------------------------------------------------------
# Bounds and vocabulary.
# ---------------------------------------------------------------------------------------

#: §7's labels, rendered verbatim in the interface. Held here rather than in the browser for
#: the reason `trace.py` holds `STAGE_LABELS`: a claim that existed only in JavaScript would be
#: a claim the artifacts do not make.
HONEST_LABELS: tuple[str, ...] = (
    "Story selection can be manual for the demo (selection_mode: manual_demo_candidate).",
    "Ranking scores are prioritisation heuristics, not probabilities.",
    "Process traces show pipeline activity, not private model chain-of-thought.",
    "Deterministic verification remains authoritative.",
    "The graph view is a bounded visual projection, not the whole graph.",
)

#: The two keys `POST /demo/generate` consumes itself. `prompt_presets.PromptRequest`
#: projects an allowlist of five out of the same body and reports every other key as ignored,
#: so these two would arrive back at the client as *"you sent something that did nothing"* —
#: which would be false. They are removed from that list here and nowhere else, so a genuinely
#: stray key is still reported.
GENERATE_OWN_FIELDS: frozenset[str] = frozenset({"candidate_id", "live"})

#: §13's twelve checks, grouped onto the four `checking_*` stages `trace.py` declares. A table
#: rather than a prefix rule, because `identity_and_freshness` and `disclosures` are named for
#: what they check and not for which panel they belong in. A check name absent from here is a
#: check the trace would silently drop, so `test_demo_ui_api.py` asserts the table covers every
#: name the verifier actually returns.
CHECK_STAGES: Mapping[str, str] = {
    "numbers": "checking_numbers_and_units",
    "units": "checking_numbers_and_units",
    "percentages": "checking_numbers_and_units",
    "identity_and_freshness": "checking_metrics_and_periods",
    "periods": "checking_metrics_and_periods",
    "metric_identity": "checking_metrics_and_periods",
    "subject_identity": "checking_metrics_and_periods",
    "citations": "checking_citation_support",
    "disclosures": "checking_citation_support",
    "language_safety": "checking_causal_language",
    "reported_vs_calculated": "checking_causal_language",
    "title": "checking_causal_language",
}

#: The order the four verification stages are emitted in — `trace.py`'s own order, so the panel
#: reads as the verifier ran rather than as a dict happened to iterate.
VERIFICATION_STAGES: tuple[str, ...] = (
    "checking_numbers_and_units", "checking_metrics_and_periods",
    "checking_citation_support", "checking_causal_language")

#: Why a replayed run reports no cost. Quoted from `generation_store.py`'s own reasoning, which
#: is where the decision lives: the fields were never stored, because a record holding them
#: could not be byte-identical.
REPLAY_COST_REASON = (
    "not measured on replay. The recorded store deliberately holds no token count, latency or "
    "attempt count: those change on every identical request, and a record carrying them could "
    "never be byte-identical. A zero here would be a fabricated measurement.")

#: The same distinction for the package's retrieval trace.
RETRIEVAL_TIMING_REASON = (
    "not measured. `retrieval_trace[].elapsed_ms` is pinned to zero inside a package because "
    "§10.3 requires a rebuild to reproduce `package_content_digest`, and a wall-clock reading "
    "cannot. The timing stays on the retriever, which the run manifest may carry.")

#: `trace_events.jsonl` is the one file written **after** the manifest, and a reader has to be
#: told, because §14 makes the manifest the completion marker. It records the run including the
#: manifest write, so it cannot precede it; its absence is not evidence of an incomplete run.
TRACE_ARTIFACT_NOTE = (
    "written after demo_manifest.json, because it records the run including the manifest "
    "write. The manifest remains the completion marker; a directory holding one is complete "
    "whether or not this file arrived.")

SSE_POLL_SECONDS = 0.5


# ---------------------------------------------------------------------------------------
# Process state. One local user, one process (§10 puts multi-user state out of scope).
# ---------------------------------------------------------------------------------------


@dataclass
class _GenerationRecord:
    """What one generation run produced, in the two shapes its two endpoints return."""

    outcome: dict[str, Any]
    sources: dict[str, Any]


class DemoState:
    """What the endpoints remember between requests, and nothing the pipeline owns.

    `runs.RunRegistry` owns a run's *lifecycle* — status, events, terminal state. This owns the
    *payloads*, which are large, are only fetched on demand, and would make every status poll a
    full response if the registry carried them.

    A module-level singleton rather than per-app state, for the reason §10 gives: one local
    user, one process, and no authentication. `reset()` exists so a test starts from nothing.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._discovery: dict[str, Any] = {}
        self._candidates: dict[str, tuple[str, Any]] = {}
        self._packages: dict[str, dict[str, Any]] = {}
        self._generations: dict[str, _GenerationRecord] = {}
        self._failures: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        with self._lock:
            self._discovery.clear()
            self._candidates.clear()
            self._packages.clear()
            self._generations.clear()
            self._failures.clear()

    # -- discovery -------------------------------------------------------------------------

    def put_discovery(self, run_id: str, result: Any) -> None:
        """Index every ranked candidate, not only the ones the filter let through.

        A reader can select a row the filter hid — the list is filtered, the graph view is not
        — and a detail endpoint that only knew the filtered set would 404 on a candidate the
        run genuinely produced. `Suggestion` is constructed here for the hidden rows so the two
        cases render through one type; `position` is the row's place in the full ordering,
        which is what `Suggestion.position` means.
        """
        with self._lock:
            self._discovery[run_id] = result
            for suggestion in result.suggestions:
                self._candidates[suggestion.candidate.candidate_id] = (run_id, suggestion)
            for position, row in enumerate(result.ranking.ranked, start=1):
                candidate_id = row.candidate.candidate_id
                if candidate_id in self._candidates:
                    continue
                self._candidates[candidate_id] = (
                    run_id, discovery.Suggestion(row=row, position=position))

    def discovery_result(self, run_id: str) -> Any | None:
        with self._lock:
            return self._discovery.get(run_id)

    def candidate(self, candidate_id: str) -> tuple[str, Any] | None:
        with self._lock:
            return self._candidates.get(candidate_id)

    def suggested_ids(self, run_id: str) -> frozenset[str]:
        result = self.discovery_result(run_id)
        if result is None:
            return frozenset()
        return frozenset(s.candidate.candidate_id for s in result.suggestions)

    # -- packages --------------------------------------------------------------------------

    def put_package(self, candidate_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._packages[candidate_id] = payload

    def package(self, candidate_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._packages.get(candidate_id)

    # -- generation ------------------------------------------------------------------------

    def put_generation(self, run_id: str, record: _GenerationRecord) -> None:
        with self._lock:
            self._generations[run_id] = record

    def generation(self, run_id: str) -> _GenerationRecord | None:
        with self._lock:
            return self._generations.get(run_id)

    # -- failures --------------------------------------------------------------------------

    def put_failure(self, run_id: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._failures[run_id] = dict(payload)

    def failure(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._failures.get(run_id)


#: The process's state. Reset between tests through `STATE.reset()`.
STATE = DemoState()


# ---------------------------------------------------------------------------------------
# Services, and the refusal when one is absent.
# ---------------------------------------------------------------------------------------


def _service(request: Request, key: str, *, error: str) -> Any:
    """One zero-argument factory out of `app.services`, called, or a typed refusal.

    The factories are the composition root's (`story/cli.py:cmd_ui`), memoised there. A factory
    that raises is a service that could not be built — an unreachable database is the ordinary
    case — and it becomes a 503 with a code rather than a traceback.
    """
    factory = request.app.services.get(key)
    if factory is None:
        raise ApiError(error, detail=key)
    try:
        return factory()
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - the boundary; the text never leaves this frame
        _log(f"the {key} service could not be built", exc)
        raise ApiError(error, detail=key) from None


def _context(request: Request) -> Any:
    return _service(request, "story_context", error="graph_unavailable")


def _config(request: Request) -> Any:
    return _service(request, "demo_config", error="pipeline_unavailable")


def _pipeline(request: Request) -> Any:
    """The `story.pipeline` module, from the composition root. See the module docstring.

    Read as a module rather than as two functions, so every constant the endpoints render —
    the four dispositions, the artifact filenames, the refusal classes — comes from the module
    that declares them instead of being restated here and drifting.
    """
    return _service(request, "story_pipeline", error="pipeline_unavailable")


def _log(what: str, exc: BaseException | None = None) -> None:
    """The server's own terminal, and the only place an exception's text is written."""
    sys.stderr.write(f"[demo-ui] {what}\n")
    if exc is not None:
        sys.stderr.write("".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)))


# ---------------------------------------------------------------------------------------
# Request reading. Every client value is bounded and typed before it is used.
# ---------------------------------------------------------------------------------------


def _text(body: Mapping[str, Any], key: str, *, maximum: int = 320) -> str:
    value = body.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError("bad_request", detail=key)
    if len(value) > maximum:
        raise ApiError("field_too_long", detail=key)
    return value


def _flag(body: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ApiError("bad_request", detail=key)
    return value


def _string_list(body: Mapping[str, Any], key: str, *, maximum: int = 50) -> tuple[str, ...]:
    value = body.get(key) or ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ApiError("bad_request", detail=key)
    if len(value) > maximum:
        raise ApiError("bad_request", detail=key)
    for entry in value:
        if not isinstance(entry, str) or len(entry) > 320:
            raise ApiError("bad_request", detail=key)
    return tuple(str(entry) for entry in value)


def _optional_number(body: Mapping[str, Any], key: str) -> float | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError("bad_request", detail=key)
    return float(value)


def _bounded_int(body: Mapping[str, Any], key: str, *, default: int, low: int,
                 high: int) -> int:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError("bad_request", detail=key)
    if not low <= value <= high:
        raise ApiError("bad_request", detail=key)
    return value


def _candidate_id(source: Mapping[str, Any], key: str = "candidate_id") -> str:
    value = source.get(key)
    if value is None or value == "":
        raise ApiError("missing_field", detail=key)
    return validate_candidate_id(value)


def _run(request: Request, phase: str) -> Run:
    """The run a path parameter names, validated, looked up, and checked for its phase.

    The phase check is not decoration: `run-discovery-0001` and `run-generation-0001` are
    different runs, and a discovery run fetched through a generation route would return a
    payload the caller cannot read. `runs.RunRegistry` mints per-phase counters precisely so
    this check is possible.
    """
    run = request.app.registry.get(request.params["run_id"])
    if run.phase != phase:
        raise ApiError("wrong_phase", detail=phase)
    return run


def _relative(path: Path, root: Path) -> str:
    """A run directory as the repository sees it. Never an absolute path in a response.

    An absolute path names the operator's home directory and their username, which is exactly
    the kind of environment detail a demo in a browser must not hand out.
    """
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def _display_number(value: float) -> str:
    """§7's float residue, formatted for display while the exact value stays beside it.

    `15.899999999999999` renders as `15.9`; the untouched float is returned in the same object
    under its own key, because the *rendered* string is what §13 checks and the exact value is
    what a reviewer recomputes.
    """
    return f"{value:.10g}"


# ---------------------------------------------------------------------------------------
# Prompts. Composed by `prompt_presets`, delivered through the seam it built for it.
# ---------------------------------------------------------------------------------------


def _prompt_request(body: Mapping[str, Any]) -> Any:
    """`prompt_presets.PromptRequest` out of a generate body, or a typed refusal.

    Every §6 decision — which five keys are readable, the per-field and total character bounds,
    the line bound, the control-character refusal, the length-target range — belongs to that
    module and is not restated here. This function does two things it owns: it turns
    `PromptRequestInvalid` into a code with a field name, and it removes this endpoint's own two
    keys from the ignored list, because reporting `candidate_id` as ignored would be false.
    """
    try:
        request = prompt_presets.PromptRequest.from_payload(dict(body))
    except prompt_presets.PromptRequestInvalid as exc:
        raise ApiError("invalid_prompt_request", detail=exc.field) from None
    return dataclasses.replace(request, ignored_fields=tuple(
        name for name in request.ignored_fields if name not in GENERATE_OWN_FIELDS))


class ObservedProvider:
    """A `StoryGenerationProvider` that reports its calls, forwards `store`, and delivers style.

    Three jobs, and each is a seam the objects it sits between do not have:

    * **Reporting.** `run_demo` is one blocking call, so without a hook the whole generation
      half would reach the trace panel at once. The schema name it is handed says which call
      site is opening, which is an operational fact observed here rather than a narration.
    * **`store`.** `pipeline._write_run` reads `provider.store` to write `generations.jsonl`
      (§14's replay artifact). `prompt_presets.EditedSystemProvider` is a frozen dataclass with
      two fields and no such attribute, so a chain that ended there would silently drop the one
      file that makes a live run reproducible. The store is read off the innermost provider.
    * **Style delivery, and it is the one thing here that is not free.** §6 lists
      `write_story_style_argument` as the mechanism carrying a custom `StyleProfile` — but
      `run_demo` calls `write_story` without a `style` argument, so through this path the
      argument is not reachable. The composed writer system text is `writer_system(style)`, so
      handing that same text to the wrapper reproduces byte-for-byte what the panel shows, and
      `EditedSystemProvider` still verifies `WRITER_SYSTEM` is inside it before it goes out.
      What is *not* reproduced is `draft.style_profile_id`: `write_story` records the profile it
      was given, which is the stage's default, so a custom-style run has a system message from
      one profile and a recorded id from another. That is reported in the outcome under
      `style_delivery` rather than smoothed over.
    """

    def __init__(self, inner: Any, *, composed: Any, store: Any = None,
                 on_call: Callable[[str], None] | None = None) -> None:
        self._inner = inner
        self._composed = composed
        self._store = store
        self._on_call = on_call
        self.calls: list[str] = []

    @property
    def model_id(self) -> str:
        return str(getattr(self._inner, "model_id", "") or "")

    @property
    def store(self) -> Any:
        return self._store

    def health(self) -> Any:
        return self._inner.health()

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float,
    ) -> Any:
        self.calls.append(schema_name)
        if self._on_call is not None:
            self._on_call(schema_name)
        if schema_name == WRITER_SCHEMA_NAME:
            system = writer_system(self._composed.style)
        return self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)


def _style_delivery(composed: Any) -> dict[str, Any]:
    """What carried the style, and what the artifact will say instead. See `ObservedProvider`."""
    default = composed.style.profile_id == PLAIN_INVESTOR_STYLE.profile_id
    return {
        "profile_id": composed.style.profile_id,
        "is_recorded_default": default,
        "mechanism": "stage_default" if default else "system_message_substitution",
        "note": "" if default else (
            "run_demo calls write_story without a style argument, so the system message "
            "carries this profile while draft.style_profile_id records the stage's default. "
            "The text that reached the model is the one the prompt panel shows."),
    }


# ---------------------------------------------------------------------------------------
# Running work in the background, with a truthful terminal state.
# ---------------------------------------------------------------------------------------


def _start_run(run: Run, work: Callable[[TraceEmitter], Mapping[str, Any]]) -> threading.Thread:
    """`runs.execute` with one difference: the failure *code* comes from the failure.

    `runs.execute` takes one fixed `error_code`, which is right for a caller with one way to
    fail. A generation run has several and they are different answers — a stale graph, a
    candidate the detectors no longer produce, a request the recorded store does not hold — and
    the SSE `end` frame is where the browser learns which. Everything else about `execute`'s
    contract is kept and is what the tests assert: the run is marked running, *every* exit path
    reaches a terminal state, an exception is swallowed here rather than lost on a worker
    thread, and nothing that raised is ever left looking complete.
    """

    def drive() -> None:
        run.start()
        try:
            result = work(run.emitter())
        except ApiError as exc:
            _log(f"run {run.run_id} failed: {exc.code}")
            run.fail(exc.code)
            return
        except BaseException as exc:  # noqa: BLE001 - the worker thread's last chance
            _log(f"run {run.run_id} raised", exc)
            run.fail("internal_error")
            return
        run.complete(result)

    thread = threading.Thread(target=drive, name=f"demo-ui-{run.run_id}", daemon=True)
    thread.start()
    return thread


def _run_envelope(run: Run) -> dict[str, Any]:
    """What every run-scoped endpoint says about the run itself, before its payload."""
    summary = run.summary()
    error = summary.get("error_code")
    return {
        "run": summary,
        "ready": summary["status"] == "complete",
        "error": (None if not error else
                  {"code": error, "message": ERRORS.get(str(error), ERRORS["internal_error"])[1],
                   "status": ERRORS.get(str(error), ERRORS["internal_error"])[0]}),
    }


# ---------------------------------------------------------------------------------------
# Endpoint: the graph.
# ---------------------------------------------------------------------------------------


def graph_overview(request: Request) -> JsonResponse:
    """`GET /demo/graph/overview?projection=overview|coverage|evidence_backbone`.

    The client names a projection. It does not send a query, it cannot send a bound, and the
    three names map to three module-level statements in `projection.py` — the *"no Cypher from
    the browser, ever"* rule as a signature.
    """
    name = request.query.get("projection", projection.PROJECTION_OVERVIEW)
    if name not in projection.PROJECTION_NAMES:
        raise ApiError("unknown_projection", detail="projection")
    context = _context(request)
    try:
        payload = dict(projection.build_projection(context.executor, name))
    except projection.ProjectionError:
        raise ApiError("unknown_projection", detail="projection") from None
    except Exception as exc:  # noqa: BLE001 - a database that is not up is an ordinary state
        _log("the graph projection could not be read", exc)
        raise ApiError("graph_unavailable", detail=name) from None
    payload["available_projections"] = list(projection.PROJECTION_NAMES)
    payload["available_views"] = list(projection.SUBGRAPH_VIEWS)
    payload["honest_labels"] = list(HONEST_LABELS)
    return JsonResponse(payload)


def graph_subgraph(request: Request) -> JsonResponse:
    """`GET /demo/graph/subgraph?candidate_id=…&view=story|evidence`.

    The candidate is resolved out of a discovery run this process performed, so the metric ids
    and period keys reaching the statement are values the detectors produced — never strings
    from the browser. The browser's own `candidate_id` is checked against `CANDIDATE_ID_PATTERN`
    before it is used as a dictionary key.
    """
    candidate_id = _candidate_id(request.query)
    view = request.query.get("view", projection.VIEW_STORY)
    if view not in projection.SUBGRAPH_VIEWS:
        raise ApiError("unknown_view", detail="view")
    found = STATE.candidate(candidate_id)
    if found is None:
        raise ApiError("unknown_candidate")
    _run_id, suggestion = found
    context = _context(request)
    try:
        payload = dict(projection.build_candidate_subgraph(
            context.executor, suggestion.candidate, view=view))
    except projection.ProjectionError:
        raise ApiError("unknown_view", detail="view") from None
    except Exception as exc:  # noqa: BLE001
        _log("the candidate subgraph could not be read", exc)
        raise ApiError("graph_unavailable", detail=view) from None
    payload["candidate_id"] = candidate_id
    payload["metric_ids"] = list(suggestion.candidate.metric_ids)
    payload["anchor_period_keys"] = list(suggestion.candidate.anchor_period_keys)
    payload["honest_labels"] = list(HONEST_LABELS)
    return JsonResponse(payload)


# ---------------------------------------------------------------------------------------
# Endpoint: discovery.
# ---------------------------------------------------------------------------------------


def _filters_from(body: Mapping[str, Any]) -> Any:
    filters = discovery.DiscoveryFilters(
        subject_entity_id=_text(body, "subject_entity_id", maximum=320),
        metric_ids=_string_list(body, "metric_ids"),
        story_types=_string_list(body, "story_types", maximum=8),
        period_from=_text(body, "period_from", maximum=32),
        period_to=_text(body, "period_to", maximum=32),
        external_only=_flag(body, "external_only"),
        min_score=_optional_number(body, "min_score"),
        max_suggestions=_bounded_int(body, "max_suggestions", default=20, low=1, high=200),
    )
    try:
        # Raised here rather than on the worker thread, so a mistyped story type is a 400 with
        # a code instead of a run that starts and immediately fails.
        filters.detector_ids()
    except discovery.UnknownStoryType:
        raise ApiError("unknown_story_type", detail="story_types") from None
    return filters


def _discovery_trace(emitter: TraceEmitter) -> Callable[[Any], None]:
    """Adapt a `DiscoveryEvent` onto the trace stream, and drop the one stage that cannot map.

    `discovery.TRACE_STAGES` records the collision honestly: `freshness` is a stage of
    `trace.py`'s **generation** phase, and `TraceEvent` validates the pairing, so emitting it
    onto a discovery stream would raise. The gate's verdict is not lost — it travels in the
    result payload under `freshness` — and inventing a discovery stage for it here would be
    this layer minting a name the closed set does not have.
    """
    discovery_stages = frozenset(STAGES_BY_PHASE["discovery"])

    def on_event(event: Any) -> None:
        stage = discovery.TRACE_STAGES.get(event.stage)
        if stage is None or stage not in discovery_stages:
            return
        emitter.emit(
            stage, event.status,
            counts=TraceCounts(processed=event.processed, accepted=event.accepted,
                               refused=event.refused, warnings=event.warnings),
            node_ids=event.highlight_node_ids)

    return on_event


def start_discovery(request: Request) -> JsonResponse:
    """`POST /demo/story-suggestions` — start a run and hand back its id.

    Suggestions are not computed on page load (§5). This is the button, and what it starts is
    all four detectors, the deduplication and §6.10's ranking — the wider claim `pipeline.py`
    deliberately does not make.
    """
    filters = _filters_from(request.body)
    gate = _flag(request.body, "gate", default=True)
    context = _context(request)
    config = _config(request)
    graph_run_id = str(config.graph_run_id)
    run = request.app.registry.create("discovery")

    def work(emitter: TraceEmitter) -> Mapping[str, Any]:
        try:
            result = discovery.discover_from_context(
                context, graph_run_id=graph_run_id, filters=filters,
                on_event=_discovery_trace(emitter), gate=gate)
        except discovery.StaleGraphRefused as exc:
            STATE.put_failure(run.run_id, {
                "code": "stale_graph",
                "freshness": _scrub_paths(exc.report.model_dump(mode="json")),
                "refusal_codes": [check.code.value for check in exc.report.refusals],
            })
            raise ApiError("stale_graph") from None
        except discovery.UnknownStoryType:
            raise ApiError("unknown_story_type", detail="story_types") from None
        except Exception as exc:  # noqa: BLE001
            _log("discovery failed", exc)
            raise ApiError("graph_unavailable", detail="discovery") from None
        STATE.put_discovery(run.run_id, result)
        emitter.emit(
            "preparing_suggestions", "complete",
            counts=TraceCounts(processed=len(result.ranking.ranked),
                               accepted=len(result.suggestions)))
        return {"candidates": len(result.ranking.ranked),
                "suggestions": len(result.suggestions)}

    _start_run(run, work)
    return JsonResponse({
        "run_id": run.run_id,
        "phase": "discovery",
        "status": run.status,
        "events_url": f"/demo/story-suggestions/{run.run_id}/events",
        "result_url": f"/demo/story-suggestions/{run.run_id}",
        "filters": filters.as_dict(),
        "honest_labels": list(HONEST_LABELS),
    }, status=202)


def discovery_events(request: Request) -> EventStream:
    """`GET /demo/story-suggestions/{run_id}/events` — the SSE stream of `TraceEvent`."""
    return trace_stream(_run(request, "discovery"), poll_seconds=SSE_POLL_SECONDS)


def discovery_result(request: Request) -> JsonResponse:
    """`GET /demo/story-suggestions/{run_id}` — the ranked candidates, once complete."""
    run = _run(request, "discovery")
    payload = _run_envelope(run)
    result = STATE.discovery_result(run.run_id)
    payload["result"] = None if result is None else result.as_dict()
    failure = STATE.failure(run.run_id)
    if failure is not None:
        payload["failure"] = failure
    payload["honest_labels"] = list(HONEST_LABELS)
    return JsonResponse(payload)


# ---------------------------------------------------------------------------------------
# Endpoint: one candidate.
# ---------------------------------------------------------------------------------------


def _explanations(codes: Sequence[str], family: str | None = None) -> list[dict[str, Any]]:
    """Every code with the catalogue's sentence, and the bare code when it has none.

    `code_catalogue.explain` returns `None` rather than a synthesised row, and that is passed
    through: a UI meeting a code nobody documented must show the code and say so, not print a
    sentence nobody wrote.
    """
    rows: list[dict[str, Any]] = []
    for code in codes:
        found = code_catalogue.explain(code, family)
        rows.append(found.as_dict() if found is not None
                    else {"code": code, "family": family or "", "description": "",
                          "severity": "", "remedy": "", "section": "", "warning_kind": "",
                          "blocking": False})
    return rows


def candidate_detail(request: Request) -> JsonResponse:
    """`GET /demo/candidates/{candidate_id}` — the row, its whole score, and its facts.

    `suppressed` is the field this endpoint exists for. `CandidateScore` carries components and
    no reason a component is missing, so a panel rendering it cannot tell *"no magnitude term:
    fewer than eight prior deltas"* from *"a magnitude of zero"*. `Suggestion.breakdown` carries
    the reasons and they are returned whole.
    """
    candidate_id = validate_candidate_id(request.params["candidate_id"])
    found = STATE.candidate(candidate_id)
    if found is None:
        raise ApiError("unknown_candidate")
    run_id, suggestion = found
    package = STATE.package(candidate_id)
    return JsonResponse({
        "candidate_id": candidate_id,
        "discovery_run_id": run_id,
        "in_suggestions": candidate_id in STATE.suggested_ids(run_id),
        "suggestion": suggestion.as_dict(),
        "candidate": suggestion.candidate.model_dump(mode="json"),
        "warnings": _explanations(suggestion.candidate.warnings),
        "score_disclaimer": "Ranking scores are prioritisation heuristics, not probabilities.",
        "package_built": package is not None,
        "package": None if package is None else package["package"],
        "facts": [] if package is None else package["facts"],
        "honest_labels": list(HONEST_LABELS),
    })


#: An absolute filesystem path, POSIX or Windows-drive, of at least two segments.
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:)?(?:/[^\s/\"',;)]+){2,}")

#: How many trailing segments of a scrubbed path survive. Two keeps the run directory and
#: the file — `graph_runs/graph-v1-0483dc6b4b10` — which is what makes a freshness detail
#: readable, while the operator's home directory is what makes it a leak.
_PATH_TAIL_SEGMENTS = 2


def _scrub_paths(value: Any) -> Any:
    """Reduce every absolute path in a payload to its last two segments.

    **Found by the frontend agent and confirmed against the running server (2026-08-05): four
    of the fourteen freshness checks carried the operator's home directory** —
    `read from /mnt/c/Users/thele/Projects/FKG-story-agent-impl/data/graph_runs/…` — into the
    bodies of `POST /demo/evidence-package` and `GET /demo/story-suggestions/{run_id}`. That
    contradicts this module's own rule that no response names a filesystem location, and it is
    the same class of leak as `provider_model_id`, which was already reduced to a filename.

    A `FreshnessCheck`'s `detail`/`expected`/`observed` are free-form prose composed by the
    gate, so there is no field to omit and no structured place to intercept: the path is inside
    a sentence. Scrubbing the rendered payload is therefore the whole fix rather than a
    cosmetic one — and it takes no repository root, which matters because neither dump site has
    one to hand and plumbing it through two call paths to reach a regex would be the worse
    trade.

    The run directory survives because it is identity a reader needs (`graph-v1-0483dc6b4b10`
    is the snapshot the whole demo is pinned to); the machine it sits on does not.
    """
    if isinstance(value, str):
        return _ABSOLUTE_PATH.sub(
            lambda match: ".../" + "/".join(match.group(0).split("/")[-_PATH_TAIL_SEGMENTS:]),
            value)
    if isinstance(value, Mapping):
        return {key: _scrub_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_paths(item) for item in value]
    return value


# ---------------------------------------------------------------------------------------
# Endpoint: the evidence package.
# ---------------------------------------------------------------------------------------


def _package_payload(inputs: Any, previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """One built package, with the digest comparison that makes a rebuild worth doing.

    A rebuild whose digest moved is the finding; a rebuild whose digest held is §10.3's claim
    executed rather than asserted. `digest_stable` is `None` on a first build, because there is
    nothing to compare against and reporting `true` would be an answer to a question nobody
    asked.
    """
    package = inputs.package
    digest = package.package_content_digest
    prior = None if previous is None else previous["package"]["package_content_digest"]
    facts = [fact.model_dump(mode="json") for fact in package.facts]
    return {
        "candidate_id": package.candidate_id,
        "graph_run_id": package.graph_run_id,
        "rebuilt": previous is not None,
        "previous_digest": prior,
        "digest_stable": None if prior is None else prior == digest,
        "package": {
            "package_id": package.package_id,
            "package_version": package.package_version,
            "package_content_digest": digest,
            "identity": package.identity.model_dump(mode="json"),
            "budget": package.budget.model_dump(mode="json"),
            "counts": {
                "facts": len(package.facts),
                "metrics": len(package.metrics),
                "formula_windows": len(package.formula_windows),
                "events": len(package.events),
                "relationships": len(package.relationships),
                "primary_passages": len(package.primary_passages),
                "context_passages": len(package.context_passages),
                "explanatory_passages": len(package.explanatory_passages),
                "counter_evidence": len(package.counter_evidence),
                "documents": len(package.documents),
                "warnings": len(package.warnings),
                "conflicts": len(package.conflicts),
                "compatibility": len(package.compatibility),
                "retrieval_trace": len(package.retrieval_trace),
            },
            "subject": package.subject.model_dump(mode="json"),
            "warnings": [
                {**warning.model_dump(mode="json"),
                 "explanation": _explanations(
                     [warning.code], code_catalogue.FAMILY_PACKAGE_WARNING)[0]}
                for warning in package.warnings
            ],
            "retrieval_trace": [entry.model_dump(mode="json")
                                for entry in package.retrieval_trace],
            "retrieval_timing": {"measured": False, "reason": RETRIEVAL_TIMING_REASON},
        },
        "facts": facts,
        "primary_passages": [p.model_dump(mode="json") for p in package.primary_passages],
        "context_passages": [p.model_dump(mode="json") for p in package.context_passages],
        "counter_evidence": [p.model_dump(mode="json") for p in package.counter_evidence],
        "documents": [d.model_dump(mode="json") for d in package.documents],
        "freshness": _scrub_paths(inputs.freshness.model_dump(mode="json")),
        "honest_labels": list(HONEST_LABELS),
    }


def _resolve_inputs(request: Request, candidate_id: str) -> Any:
    """`resolve_demo_inputs`, with every refusal it can raise mapped onto a code.

    **What this endpoint can package, stated rather than discovered by the reader.**
    `resolve_demo_inputs` re-derives the candidate through §6.6's D4 alone — §8b's path runs
    one detector — so a candidate found by D1, D2 or D3 in a discovery run reaches
    `candidate_not_reproducible` here. That is the pipeline's own boundary and this layer does
    not widen it: composing a second packaging path would make the demo's evidence come from
    somewhere the accepted pipeline does not.
    """
    pipeline = _pipeline(request)
    context = _context(request)
    config = _config(request)
    try:
        return pipeline.resolve_demo_inputs(
            context, candidate_id=candidate_id, graph_run_id=str(config.graph_run_id))
    except pipeline.FreshnessRefused:
        raise ApiError("stale_graph") from None
    except pipeline.CandidateNotFound:
        raise ApiError("candidate_not_reproducible") from None
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        _log("the evidence package could not be built", exc)
        raise ApiError("graph_unavailable", detail="packaging") from None


def build_package(request: Request) -> JsonResponse:
    """`POST /demo/evidence-package` — build or rebuild one package and hash it."""
    candidate_id = _candidate_id(request.body)
    previous = STATE.package(candidate_id)
    payload = _package_payload(_resolve_inputs(request, candidate_id), previous)
    STATE.put_package(candidate_id, payload)
    return JsonResponse(payload)


# ---------------------------------------------------------------------------------------
# Endpoint: prompts.
# ---------------------------------------------------------------------------------------


def prompt_preset_catalogue(request: Request) -> JsonResponse:
    """`GET /demo/prompt-presets` — the presets, the editable/fixed split, and the bounds.

    Rendered entirely by `prompt_presets.presets_payload`. `baseline_length_target` is the
    run's own configured value rather than that module's default, because it is what
    `requires_live` is compared against: passing the wrong one errs towards `True`, which costs
    a live call rather than producing a silent `MissingGenerationError`.
    """
    config = _config(request)
    return JsonResponse(prompt_presets.presets_payload(
        baseline_length_target=int(config.length_target)))


# ---------------------------------------------------------------------------------------
# Endpoint: generation. The substantive one.
# ---------------------------------------------------------------------------------------


def _provider_for(request: Request, config: Any, *, live: bool) -> Any:
    """`story/cli.py:_provider`, mirrored — the same object on both paths.

    Replay is a store with no inner provider, so a miss raises rather than quietly reaching for
    a GPU. Live is the same replaying decorator around the HTTP adapter, so a live run captures
    what it generated and can be replayed afterwards. `httpx` enters `sys.modules` only on the
    live path, exactly as the CLI arranges it.
    """
    from story.providers.generation_store import (
        GenerationStore,
        ReplayingStoryGenerationProvider,
    )
    from story.providers.public import load_provider_config

    try:
        provider_config = load_provider_config(config.raw)
    except Exception as exc:  # noqa: BLE001 - a configuration failure names a path
        _log("the provider configuration could not be read", exc)
        raise ApiError("provider_unavailable", detail="config") from None
    if live:
        try:
            from story.providers.openai_compatible import StoryOpenAICompatibleProvider
        except ImportError:
            raise ApiError("provider_unavailable", detail="http_adapter") from None
        try:
            inner = StoryOpenAICompatibleProvider(provider_config)
        except Exception as exc:  # noqa: BLE001 - the message carries the server's URL
            _log("the live provider could not be constructed", exc)
            raise ApiError("provider_unavailable", detail="live") from None
        return ReplayingStoryGenerationProvider(
            GenerationStore(), inner, model_id=provider_config.model)
    store = GenerationStore(config.resolved_path(config.generation_store))
    if not len(store):
        raise ApiError("generation_store_empty")
    return ReplayingStoryGenerationProvider(store, model_id=provider_config.model)


def _generation_trace(emitter: TraceEmitter) -> Callable[[str], None]:
    """One event as each model call opens, from inside the provider decorator.

    `run_demo` is one blocking call, so without this the whole generation half would arrive at
    the trace panel at once. The decorator is already in the path for §6's prompt composition,
    and the schema name it is handed says which of the two call sites is opening — which is an
    operational fact this code observes, not a narration of one.
    """

    def on_call(schema_name: str) -> None:
        if schema_name == PLANNER_SCHEMA_NAME:
            emitter.emit("planning", "running")
        elif schema_name == WRITER_SCHEMA_NAME:
            emitter.emit("drafting", "running")

    return on_call


def _emit_inputs(emitter: TraceEmitter, inputs: Any) -> None:
    """Three events for the three things `resolve_demo_inputs` did, each counted off its result.

    They are emitted after one call rather than around three, because the graph half is one
    function and this layer will not claim to have watched steps inside it. Every count is read
    off the returned value; none is estimated.
    """
    package = inputs.package
    emitter.emit(
        "freshness", "passed",
        counts=TraceCounts(processed=len(inputs.freshness.checks),
                           refused=len(inputs.freshness.refusals)))
    emitter.emit(
        "building_evidence_package", "passed",
        counts=TraceCounts(processed=len(package.facts),
                           accepted=len(package.metrics),
                           warnings=len(package.warnings)),
        fact_ids=[fact.observation_id for fact in package.facts])
    emitter.emit(
        "resolving_primary_sources", "passed",
        counts=TraceCounts(processed=len(package.primary_passages),
                           accepted=len(package.documents),
                           warnings=len(package.counter_evidence)))


def _emit_outcome(emitter: TraceEmitter, outcome: Any, accepted: str) -> None:
    """The generation half's events, every count read off `DemoOutcome`."""
    plan, draft, verified = outcome.plan, outcome.draft, outcome.verified
    if plan is None:
        emitter.emit("planning", "failed",
                     counts=TraceCounts(refused=len(outcome.refusal_codes)))
    else:
        emitter.emit("planning", "passed",
                     counts=TraceCounts(processed=len(plan.key_points),
                                        accepted=len(plan.counterpoints),
                                        warnings=len(plan.required_warnings)))
    if draft is None:
        emitter.emit("binding_facts_and_citations", "skipped")
        emitter.emit("drafting", "failed",
                     counts=TraceCounts(refused=len(outcome.refusal_codes)))
    else:
        bindings = [binding.fact_id for sentence in draft.sentences
                    for binding in sentence.fact_bindings]
        citations = sum(len(sentence.citations) for sentence in draft.sentences)
        emitter.emit("binding_facts_and_citations", "passed",
                     counts=TraceCounts(processed=len(bindings), accepted=citations),
                     fact_ids=bindings,
                     sentence_ids=[str(sentence.index) for sentence in draft.sentences])
        emitter.emit("drafting", "passed",
                     counts=TraceCounts(processed=len(draft.sentences)))

    for stage in VERIFICATION_STAGES:
        if verified is None:
            emitter.emit(stage, "skipped")
            continue
        checks = [check for check in verified.checks
                  if CHECK_STAGES.get(check.name) == stage]
        findings = [finding for check in checks for finding in check.findings]
        blocking = [finding for finding in findings if finding.blocking]
        status = "failed" if blocking else ("warning" if findings else "passed")
        emitter.emit(
            stage, status,
            counts=TraceCounts(processed=sum(check.examined for check in checks),
                               accepted=len(checks), refused=len(blocking),
                               warnings=len(findings) - len(blocking)),
            sentence_ids=[str(finding.sentence_index) for finding in findings
                          if finding.sentence_index is not None])

    emitter.emit(
        "rendering", "passed" if outcome.disposition == accepted else "warning",
        counts=TraceCounts(processed=len(outcome.artifacts)))
    emitter.emit("rendering", "complete")


def _manifest_payload(manifest: Any) -> dict[str, Any]:
    """§14's manifest, with the one field that is a filesystem path reduced to its basename.

    **Found by a test rather than predicted.** `provider_model_id` is what the *server* called
    itself, and the local runtime answers with an absolute `.gguf` path — which names the
    operator's home directory and their username. The distinction the manifest draws between
    the configured `model_id` and the reported one is worth keeping, and the part of it a
    browser needs is the filename; the run directory's own `demo_manifest.json` keeps the whole
    value, because that is the artifact a reviewer reads and it is not served to anyone.
    """
    payload = manifest.as_dict()
    reported = str(payload.get("provider_model_id") or "")
    if reported:
        payload["provider_model_id"] = PurePosixPath(reported.replace("\\", "/")).name
        payload["provider_model_id_note"] = (
            "the filename only. The server reports an absolute path to the model file, which "
            "names the operator's filesystem; the run's own manifest on disk keeps it whole.")
    return payload


def _cost_panel(outcome: Any, *, live: bool) -> dict[str, Any]:
    """What the run spent, or an explicit statement that nothing measured it.

    **The zeroes are not a measurement and are not rendered as one.** A replayed row carries no
    token count and no latency by design, so `_token_totals` sums zeros; a panel showing `0`
    would tell an investor the run was free. `measured` is derived from the totals rather than
    from the `live` flag, because the replaying provider answers from the store first — a run
    asked for live can still be a replay, and the flag alone would mislabel it.

    The two package estimates are real: `BudgetParameters` computes them before any call, and
    they are reported as estimates whatever the mode.
    """
    totals = dict(getattr(outcome.manifest, "token_totals", {}) or {})
    total = int(totals.get("total_tokens", 0) or 0)
    measured = total > 0
    panel: dict[str, Any] = {
        "measured": measured,
        "mode": "live" if live else "replay",
        "generation_calls": int(totals.get("generation_calls", 0) or 0),
        "package_prompt_token_estimate": totals.get("package_prompt_token_estimate"),
        "package_artifact_token_estimate": totals.get("package_artifact_token_estimate"),
        "estimates_are_computed": True,
    }
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        panel[name] = int(totals.get(name, 0) or 0) if measured else None
    panel["latency_ms"] = None
    if not measured:
        panel["reason"] = REPLAY_COST_REASON
    else:
        panel["reason"] = ("latency is not recorded by this path; the token counts are the "
                           "server's own, summed across the run's generation calls")
    return panel


def _verification_payload(verified: Any) -> dict[str, Any]:
    """§13's decision, whole, plus the display form of the one number that has residue.

    The exact float stays: `recomputed_value` is what a reviewer recomputes, and `15.9` is what
    a reader sees. §7 asks for both, and the *rendered* string is what §13 checked in the first
    place.
    """
    payload = verified.model_dump(mode="json")
    payload["passed"] = verified.passed
    payload["blocking_findings"] = [
        {**finding.model_dump(mode="json"),
         "explanation_row": _explanations(
             [finding.code], code_catalogue.FAMILY_VERIFICATION)[0]}
        for finding in verified.all_findings if finding.blocking
    ]
    payload["warnings"] = [
        finding.model_dump(mode="json") for finding in verified.all_findings
        if not finding.blocking
    ]
    payload["coverage"] = {
        "checks": len(verified.checks),
        "examined": sum(check.examined for check in verified.checks),
        "findings": len(verified.all_findings),
        "blocking": sum(1 for f in verified.all_findings if f.blocking),
        "note": ("these are counts of what the deterministic checks examined and refused. They "
                 "are not precision, recall or a confidence."),
    }
    payload["calculation_ledger_display"] = [
        {"sentence_index": entry.sentence_index,
         "expression": entry.expression,
         "recomputed_value": entry.recomputed_value,
         "recomputed_display": _display_number(entry.recomputed_value),
         "rendered": entry.rendered}
        for entry in verified.calculation_ledger
    ]
    return payload


def _outcome_payload(outcome: Any, *, pipeline: Any, root: Path, live: bool,
                     composed: Any, trace_digest: str, trace_count: int) -> dict[str, Any]:
    """`DemoOutcome` as the run endpoint returns it.

    **A refused or rejected run renders as rejected, and there is one branch that decides it.**
    `post` is populated only on `ACCEPTED`, which is the same condition `pipeline._write_run`
    writes `post.md` under; every other disposition carries `rejection` instead. The two can
    never both be present, here or on disk.

    `outcome.refusal` is deliberately **not** in this payload. It is `str(exc)` from whatever
    refused, and a `StoryProviderError` raised by the HTTP adapter carries the server's URL.
    The structured `refusal_codes` say the same thing without carrying an environment detail,
    and `code_catalogue` turns each into a sentence this repository wrote.
    """
    accepted = outcome.disposition == pipeline.ACCEPTED
    directory = Path(outcome.directory)
    post = None
    if accepted:
        post_path = directory / pipeline.POST_FILENAME
        if post_path.is_file():
            post = {"filename": pipeline.POST_FILENAME,
                    "markdown": post_path.read_text(encoding="utf-8"),
                    "sentences": len(outcome.draft.sentences) if outcome.draft else 0}
    rejection = None
    if not accepted:
        family = (code_catalogue.FAMILY_PLANNER
                  if outcome.disposition == pipeline.PLAN_REFUSED
                  else code_catalogue.FAMILY_WRITER
                  if outcome.disposition == pipeline.DRAFT_REFUSED
                  else code_catalogue.FAMILY_VERIFICATION)
        rejection = {
            "filename": pipeline.REJECTED_FILENAME,
            "stage": {pipeline.PLAN_REFUSED: "editorial_planner",
                      pipeline.DRAFT_REFUSED: "post_writer",
                      pipeline.REJECTED: "deterministic_verifier"}.get(
                          outcome.disposition, outcome.disposition),
            "codes": _explanations(outcome.refusal_codes, family),
            "verifier_ran": outcome.verified is not None,
        }
    return {
        "story_run_id": outcome.story_run_id,
        "directory": _relative(directory, root),
        "disposition": outcome.disposition,
        "accepted": accepted,
        "rendered_as": "post" if accepted else "rejection",
        "dispositions": [pipeline.ACCEPTED, pipeline.REJECTED, pipeline.PLAN_REFUSED,
                         pipeline.DRAFT_REFUSED],
        "selection_mode": pipeline.SELECTION_MODE,
        "generation_mode": "live" if live else "replay",
        "artifacts": dict(outcome.artifacts),
        "trace_events": {"filename": TRACE_EVENTS_FILENAME, "sha256": trace_digest,
                         "events": trace_count, "note": TRACE_ARTIFACT_NOTE},
        "manifest": _manifest_payload(outcome.manifest),
        "plan": None if outcome.plan is None else outcome.plan.model_dump(mode="json"),
        "draft": None if outcome.draft is None else outcome.draft.model_dump(mode="json"),
        "verification": (None if outcome.verified is None
                         else _verification_payload(outcome.verified)),
        "post": post,
        "rejection": rejection,
        "cost": _cost_panel(outcome, live=live),
        # A summary rather than the whole composition: `POST /demo/generate` already returned
        # `composed.as_dict()` with both system texts and both diffs, and repeating ten kilobytes
        # of prompt on every status poll would make the outcome a prompt panel.
        "prompt": {
            "preset_id": composed.request.preset_id,
            "planner_edited": composed.planner.edited,
            "writer_edited": composed.writer.edited,
            "length_target": composed.length_target,
            "baseline_length_target": composed.baseline_length_target,
            "requires_live": composed.requires_live,
            "requires_live_reason": composed.requires_live_reason,
            "effective_prompt_sha256": composed.effective_prompt_sha256,
            "planner_system_sha256": composed.planner.system_sha256,
            "writer_system_sha256": composed.writer.system_sha256,
            "advisories": [advisory.as_dict() for advisory in composed.advisories],
            "advisory_disclaimer": prompt_presets.ADVISORY_DISCLAIMER,
            "ignored_request_fields": list(composed.request.ignored_fields),
            "caveats": [dict(caveat) for caveat in prompt_presets.CAVEATS],
        },
        "style_delivery": _style_delivery(composed),
        "honest_labels": list(HONEST_LABELS),
    }


def _sources_payload(inputs: Any, outcome: Any) -> dict[str, Any]:
    """Sentence to fact to citation to passage to document, grouped by document.

    The link the demo claims, resolved in one direction and reported where it breaks. A
    citation whose passage is not in the package is listed under `unresolved` rather than
    dropped: §13.7 refuses that draft, so an empty `unresolved` is a property of an accepted
    run and not something this function should be able to arrange by hiding a row.

    The quote is taken from the passage text by the citation's own span, which is what a
    citation *is* in §12 — the model declares a substring and `writer.draft_from` locates it.
    `quote_resolved` says whether the span landed inside the text it names.
    """
    package = inputs.package
    passages = {p.passage_id: p for p in (
        package.primary_passages + package.context_passages
        + package.explanatory_passages + package.counter_evidence)}
    documents = {d.document_id: d for d in package.documents}
    facts_by_passage: dict[str, list[dict[str, Any]]] = {}
    citations_by_passage: dict[str, list[dict[str, Any]]] = {}
    unresolved: list[dict[str, Any]] = []

    for fact in package.facts:
        if fact.passage_id is None:
            continue
        facts_by_passage.setdefault(fact.passage_id, []).append({
            "fact_id": fact.observation_id,
            "metric_id": fact.metric_id,
            "metric_label": fact.metric_label,
            "period_key": fact.period_key,
            "value": fact.value,
            "value_display": _display_number(fact.value),
            "unit": fact.unit,
            "printed_form": fact.printed_form,
            "quoted_text": fact.quoted_text,
            "source_lane": fact.source_lane,
            "document_id": fact.document_id,
            "sentence_indexes": [],
        })

    draft = outcome.draft
    sentence_count = 0
    citation_count = 0
    if draft is not None:
        sentence_count = len(draft.sentences)
        bound: dict[str, list[int]] = {}
        for sentence in draft.sentences:
            for binding in sentence.fact_bindings:
                bound.setdefault(binding.fact_id, []).append(sentence.index)
            for citation in sentence.citations:
                citation_count += 1
                passage_id = getattr(citation, "passage_id", None)
                if passage_id is None:
                    unresolved.append({"sentence_index": sentence.index,
                                       "kind": getattr(citation, "kind", "evidence_source"),
                                       "evidence_source_id": getattr(
                                           citation, "evidence_source_id", "")})
                    continue
                passage = passages.get(passage_id)
                start = getattr(citation, "char_start", 0)
                end = getattr(citation, "char_end", 0)
                quote = "" if passage is None else passage.text[start:end]
                if passage is None:
                    unresolved.append({"sentence_index": sentence.index,
                                       "kind": "passage", "passage_id": passage_id})
                    continue
                citations_by_passage.setdefault(passage_id, []).append({
                    "sentence_index": sentence.index,
                    "sentence_text": sentence.text,
                    "char_start": start,
                    "char_end": end,
                    "quoted_text": quote,
                    "quote_resolved": bool(quote),
                })
        for rows in facts_by_passage.values():
            for row in rows:
                row["sentence_indexes"] = sorted(bound.get(row["fact_id"], []))

    grouped: list[dict[str, Any]] = []
    for document_id in sorted(
            {p.document_id for p in passages.values()} | set(documents)):
        document = documents.get(document_id)
        rows = [
            {
                "passage_id": passage.passage_id,
                "text": passage.text,
                "char_count": passage.char_count,
                "heading_path": list(passage.heading_path),
                "excerpted": passage.excerpted,
                "source_url": passage.source_url,
                "citations": citations_by_passage.get(passage.passage_id, []),
                "facts": facts_by_passage.get(passage.passage_id, []),
            }
            for passage in sorted(passages.values(), key=lambda p: p.passage_id)
            if passage.document_id == document_id
        ]
        grouped.append({
            "document_id": document_id,
            "form": None if document is None else document.form,
            "filing_date": None if document is None else document.filing_date,
            "report_date": None if document is None else document.report_date,
            "accession": None if document is None else document.accession,
            "source_url": None if document is None else document.source_url,
            "title": None if document is None else document.title,
            "in_package": document is not None,
            "passages": rows,
        })
    return {
        "candidate_id": package.candidate_id,
        "package_id": package.package_id,
        "documents": grouped,
        "unresolved": unresolved,
        "counts": {
            "documents": len(grouped),
            "passages": len(passages),
            "sentences": sentence_count,
            "citations": citation_count,
            "facts": len(package.facts),
            "unresolved": len(unresolved),
        },
        "honest_labels": list(HONEST_LABELS),
    }


def start_generation(request: Request) -> JsonResponse:
    """`POST /demo/generate` — the real pipeline, instrumented, in the background.

    `resolve_demo_inputs` then `run_demo`, and nothing here reimplements a stage: the plan, the
    draft, the deterministic verification and every artifact are `run_demo`'s. §6's request is
    composed by `prompt_presets` and delivered through its `EditedSystemProvider`; the only
    seams this function adds are the trace hook, the `store` forwarding and `length_target` —
    the one configured value a request may move, through the argument `write_story` already
    takes.

    **The replay consequence is enforced before the graph is read.** An edited prompt moves the
    system message or the length target, both of which are `request_identity` inputs, and the
    recorded store is keyed by that digest — so the request cannot be in it. Refusing here costs
    a round trip; letting the run start would cost a full graph read and a packaging pass before
    reaching the same answer through `MissingGenerationError`.
    """
    candidate_id = _candidate_id(request.body)
    live = _flag(request.body, "live")
    pipeline = _pipeline(request)
    # Asked for here and not only on the worker thread: a database that is not up should be a
    # 503 on the request that started the run, rather than a run that starts and dies.
    _context(request)
    config = _config(request)
    baseline = int(config.length_target)
    composed = prompt_presets.compose(_prompt_request(request.body),
                                      baseline_length_target=baseline)
    if composed.requires_live and not live:
        # The reason travels beside the error rather than inside its message: `requires_live_
        # reason` names which of the three inputs moved and what the remedy is, and the error
        # table stays a closed set of sentences this repository wrote.
        return JsonResponse({
            "error": ApiError("edited_prompt_requires_live").payload(),
            "prompt": composed.as_dict(),
        }, status=ERRORS["edited_prompt_requires_live"][0])

    root = Path(config.root)
    # The one configured value a request may move, and it moves through the argument
    # `write_story` already takes. `raw` is untouched, so `config_hash` — and therefore
    # `story_run_id` — is unchanged, which is `prompt_presets`' own correction: an edited prompt
    # moves `request_identity` and not the run id.
    run_config = (config if composed.length_target == baseline
                  else dataclasses.replace(config, length_target=composed.length_target))
    provider_base = _provider_for(request, config, live=live)
    run = request.app.registry.create("generation")

    def work(emitter: TraceEmitter) -> Mapping[str, Any]:
        from story.providers.generation_store import MissingGenerationError

        emitter.emit("freshness", "running")
        inputs = _resolve_inputs(request, candidate_id)
        _emit_inputs(emitter, inputs)
        provider = ObservedProvider(
            prompt_presets.EditedSystemProvider(inner=provider_base, composed=composed),
            composed=composed,
            store=getattr(provider_base, "store", None),
            on_call=_generation_trace(emitter))
        try:
            outcome = pipeline.run_demo(inputs, provider=provider, config=run_config, live=live)
        except MissingGenerationError:
            # §6's stated consequence, reached rather than predicted: this is what an edited
            # prompt does when it is allowed through, and it is reported as an actionable code
            # rather than as the traceback of a LookupError.
            raise ApiError("generation_not_recorded") from None
        except prompt_presets.FixedSectionMissing:
            # The wire check. A system message that reached this point without its fixed rules
            # is refused before it is sent, and it is refused here as its own code because it
            # would otherwise be indistinguishable from an ordinary run failure.
            raise ApiError("fixed_section_missing") from None
        except pipeline.FreshnessRefused:
            raise ApiError("stale_graph") from None
        except Exception as exc:  # noqa: BLE001
            _log("the generation run failed", exc)
            raise ApiError("internal_error", detail="run_demo") from None

        _emit_outcome(emitter, outcome, pipeline.ACCEPTED)
        events = run.events()
        path = write_trace_events(Path(outcome.directory) / TRACE_EVENTS_FILENAME, events)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        STATE.put_generation(run.run_id, _GenerationRecord(
            outcome=_outcome_payload(
                outcome, pipeline=pipeline, root=root, live=live, composed=composed,
                trace_digest=digest, trace_count=len(events)),
            sources=_sources_payload(inputs, outcome)))
        return {"story_run_id": outcome.story_run_id, "disposition": outcome.disposition,
                "accepted": outcome.disposition == pipeline.ACCEPTED}

    _start_run(run, work)
    return JsonResponse({
        "run_id": run.run_id,
        "phase": "generation",
        "status": run.status,
        "candidate_id": candidate_id,
        "live": live,
        "events_url": f"/demo/runs/{run.run_id}/events",
        "result_url": f"/demo/runs/{run.run_id}",
        "sources_url": f"/demo/runs/{run.run_id}/sources",
        "prompt": composed.as_dict(),
        "style_delivery": _style_delivery(composed),
        "honest_labels": list(HONEST_LABELS),
    }, status=202)


def generation_events(request: Request) -> EventStream:
    """`GET /demo/runs/{run_id}/events` — planner, writer and verifier, as they happen."""
    return trace_stream(_run(request, "generation"), poll_seconds=SSE_POLL_SECONDS)


def generation_result(request: Request) -> JsonResponse:
    """`GET /demo/runs/{run_id}` — plan, draft, verification, and the post or the rejection."""
    run = _run(request, "generation")
    payload = _run_envelope(run)
    record = STATE.generation(run.run_id)
    payload["outcome"] = None if record is None else record.outcome
    payload["honest_labels"] = list(HONEST_LABELS)
    return JsonResponse(payload)


def generation_sources(request: Request) -> JsonResponse:
    """`GET /demo/runs/{run_id}/sources` — the documents behind the run, with quoted text."""
    run = _run(request, "generation")
    payload = _run_envelope(run)
    record = STATE.generation(run.run_id)
    payload["sources"] = None if record is None else record.sources
    return JsonResponse(payload)


# ---------------------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------------------

#: §5's table, in the plan's order. A tuple rather than twelve `register(...)` calls, so the
#: set of endpoints is a value a test can read and compare against the plan.
ENDPOINTS: tuple[tuple[str, str, Callable[[Request], Any]], ...] = (
    ("GET", "/demo/graph/overview", graph_overview),
    ("GET", "/demo/graph/subgraph", graph_subgraph),
    ("POST", "/demo/story-suggestions", start_discovery),
    ("GET", "/demo/story-suggestions/{run_id}/events", discovery_events),
    ("GET", "/demo/story-suggestions/{run_id}", discovery_result),
    ("GET", "/demo/candidates/{candidate_id}", candidate_detail),
    ("POST", "/demo/evidence-package", build_package),
    ("GET", "/demo/prompt-presets", prompt_preset_catalogue),
    ("POST", "/demo/generate", start_generation),
    ("GET", "/demo/runs/{run_id}/events", generation_events),
    ("GET", "/demo/runs/{run_id}", generation_result),
    ("GET", "/demo/runs/{run_id}/sources", generation_sources),
)


def _guarded(handler: Callable[[Request], Any]) -> Callable[[Request], Any]:
    """Turn this module's typed refusals into a JSON body with a code, and nothing else.

    `server.py` already translates `InvalidIdentifier` and `UnknownRun` and already refuses to
    render an exception's text. This adds the api-level vocabulary on top, so a handler can
    raise a code and a client never sees a stage's message — which is the property the secret
    test asserts by putting a marker into a raised exception and looking for it in the body.
    """

    def guarded(request: Request) -> Any:
        try:
            return handler(request)
        except ApiError as exc:
            return exc.response()
        except InvalidIdentifier:
            return ApiError("invalid_identifier").response()
        except UnknownRun:
            return ApiError("unknown_run").response()

    guarded.__name__ = handler.__name__
    guarded.__doc__ = handler.__doc__
    return guarded


def register_endpoints(router: Router | None = None) -> Router:
    """Add §5's twelve routes to `router`, or to the process-wide one. Idempotent.

    Called by the composition root — `story/cli.py:cmd_ui` — rather than at import time. See
    the module docstring for the committed test that decides it, and for the third `services`
    key `POST /demo/generate` needs.
    """
    target = router if router is not None else ROUTER
    existing = set(target.routes())
    for method, template, handler in ENDPOINTS:
        if (method, template) in existing:
            continue
        target.register(method, template, _guarded(handler))
    return target


__all__ = [
    "CHECK_STAGES",
    "GENERATE_OWN_FIELDS",
    "ENDPOINTS",
    "ERRORS",
    "HONEST_LABELS",
    "STATE",
    "VERIFICATION_STAGES",
    "ApiError",
    "DemoState",
    "ObservedProvider",
    "build_package",
    "candidate_detail",
    "discovery_events",
    "discovery_result",
    "generation_events",
    "generation_result",
    "generation_sources",
    "graph_overview",
    "graph_subgraph",
    "prompt_preset_catalogue",
    "register_endpoints",
    "start_discovery",
    "start_generation",
]
