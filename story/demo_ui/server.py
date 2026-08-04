"""The local HTTP server: routing, JSON, SSE and static files, on the standard library.

Responsibility: the transport, and the boundary the browser talks to. Boundaries: it knows
about `runs.py` and `trace.py` and about nothing else in `story/`. **It builds no database
connection and imports no composition root** — `story/cli.py:cmd_ui` is where a driver would be
constructed, and what it hands over is a callable in `DemoUiApp.services`. That is not
fastidiousness: `tests/story/test_story_package_structure.py::
test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage` walks the import closure of
every module except five named composition and entry files, and a `demo_ui` module that
imported `story.context` would put `neo4j` inside that closure and fail it.

**Why `http.server` and not FastAPI** (§1): `fastapi`, `uvicorn` and `starlette` are installed
in the conda environment and are **not** declared in `pyproject.toml`, which this workstream may
not edit. An undeclared dependency is worse than an honest stdlib one. `ThreadingHTTPServer`
serves JSON, SSE and static files for a single local user without ceremony.

**Handlers are registered, not imported.** This module owns the routing table and the plumbing;
`api.py` owns §5's endpoints and calls `register(...)` at import time. Nothing here imports
`api`, so the two can be built independently and the server has no opinion about what a
`/demo/...` path means.

**The security properties this file is responsible for**, each with a test in
`tests/story/test_demo_ui_server.py`:

* Loopback only. `serve` refuses any host that is not a loopback name, and the `Host` header is
  checked on every request — a browser pointed at a rebound DNS name is refused, which is the
  one remote-access route a bound-to-127.0.0.1 server still has.
* Path traversal. A static path is resolved and confirmed to be inside the static root, so
  `..`, an absolute path, a percent-encoded separator and a symlink out are all refused.
* Bounded bodies. `MAX_BODY_BYTES` and a required `application/json` content type.
* No HTML error page and no exception text. Every error is a JSON object with a code from a
  closed table; the traceback goes to the server's own log. `BaseHTTPRequestHandler.send_error`
  is overridden for the same reason — its default is an HTML page carrying the message it was
  given.
* No secret in any response. Nothing here reads `os.environ`, and the error path never echoes
  the text of the exception it caught.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

from .runs import InvalidIdentifier, Run, RunRegistry, UnknownRun
from .trace import event_payload

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: The only hosts this server will bind to or answer for. §1 and the brief: never `0.0.0.0`.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: 256 KiB. The largest thing the client legitimately sends is an edited prompt (§6), which is
#: bounded again by the prompt endpoint's own limits; this is the bound before parsing.
MAX_BODY_BYTES = 256 * 1024

MAX_QUERY_BYTES = 2048
MAX_PATH_BYTES = 1024

_LOOPBACK_NAMES: frozenset[str] = frozenset(h.strip("[]") for h in LOOPBACK_HOSTS)

#: How long a stream waits for the next event before sending a keep-alive comment. Short enough
#: that a closed browser tab is noticed promptly, long enough that an idle run is not a busy
#: loop.
SSE_POLL_SECONDS = 1.0

#: Served by extension, from an explicit table rather than `mimetypes`, whose answers come from
#: the operating system's mime database and differ between machines. A demo whose JavaScript is
#: served as `text/plain` on one box and not another is not a demo.
CONTENT_TYPES: Mapping[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/vnd.microsoft.icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

#: Every error this server can return, with the status it returns and the sentence the client
#: sees. **The table is the whole vocabulary**: an error is rendered from a code, never from an
#: exception's `str()`, because an exception raised deep in a stage carries paths, URLs and
#: occasionally a credential fragment, and a demo that shows the operator's environment to the
#: browser has leaked it to anything else running in that browser.
_ERRORS: Mapping[str, tuple[int, str]] = {
    "not_found": (404, "no route matches that path"),
    "method_not_allowed": (405, "that path does not accept this method"),
    "invalid_identifier": (400, "an identifier in the request is not well formed"),
    "unknown_run": (404, "no run with that id is registered in this process"),
    "unsupported_media_type": (415, "a JSON request body is required"),
    "payload_too_large": (413, "the request body is larger than this server accepts"),
    "malformed_json": (400, "the request body is not valid JSON"),
    "length_required": (411, "the request body must declare its length"),
    "forbidden_host": (403, "this server answers only to a loopback host"),
    "bad_request": (400, "the request could not be understood"),
    "internal_error": (500, "the request failed inside the server; the server log has why"),
    "static_not_found": (404, "no such file in the demo's static directory"),
    "static_unavailable": (503, "the demo's static files have not been built in this tree"),
}

#: What may accompany an error code — a short token naming *which* parameter or route was at
#: fault. Anything else is dropped rather than truncated, because a partially sanitised string
#: is the shape a leak takes.
_DETAIL_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")

_PARAMETER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")

#: First path segments that belong to the JSON API. A request under one of these is never
#: answered from the static tree, so a mistyped endpoint reports a missing route rather than a
#: missing file.
API_PREFIXES: frozenset[str] = frozenset({"demo", "demo-ui"})

#: What `send_error` renders when the base class reports a protocol-level failure it detected
#: before any handler ran — a malformed request line, an unsupported method, an over-long
#: header. Explicit rather than derived from `_ERRORS`, because several codes share a status
#: and "whichever the dict comprehension saw last" is not a decision.
_STATUS_CODES: Mapping[int, str] = {
    400: "bad_request", 403: "forbidden_host", 404: "not_found", 405: "method_not_allowed",
    411: "length_required", 413: "payload_too_large", 414: "bad_request",
    415: "unsupported_media_type", 431: "bad_request", 501: "method_not_allowed",
    505: "bad_request",
}


class DemoUiError(Exception):
    """An error with a code from `_ERRORS`. The status and the sentence come from the table."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code if code in _ERRORS else "internal_error"
        self.status = _ERRORS[self.code][0]
        self.detail = detail if detail and _DETAIL_PATTERN.match(detail) else None

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": {"code": self.code, "message": _ERRORS[self.code][1],
                      "status": self.status}}
        if self.detail is not None:
            body["error"]["detail"] = self.detail
        return body


# ---------------------------------------------------------------------------------------
# What a handler receives and what it may return.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoUiApp:
    """Everything a handler needs that is not the request.

    `services` is the plug for anything that would otherwise have to be constructed here: the
    composition root puts zero-argument factories in it — see the module docstring for why the
    factories rather than the objects. A handler reads what it needs and refuses cleanly when
    the key is absent, which is what makes the server startable with no database running.
    """

    root: Path
    registry: RunRegistry
    router: "Router"
    static_root: Path
    services: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    params: Mapping[str, str]
    query: Mapping[str, str]
    body: Mapping[str, Any]
    app: DemoUiApp


@dataclass(frozen=True)
class JsonResponse:
    payload: Mapping[str, Any]
    status: int = 200


@dataclass(frozen=True)
class SseMessage:
    """One `event:`/`data:` pair. `identifier` becomes the SSE `id:` field when present."""

    event: str
    data: Mapping[str, Any]
    identifier: str | None = None


@dataclass(frozen=True)
class EventStream:
    """A handler's answer when the response is a stream rather than a document."""

    messages: Iterable[SseMessage]


Handler = Callable[[Request], Any]


# ---------------------------------------------------------------------------------------
# Routing.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    method: str
    template: str
    handler: Handler
    segments: tuple[str, ...]


class Router:
    """(method, path template) to handler, matched a segment at a time.

    Path parameters are extracted by comparing decoded segments against a template, never by
    `format`, `eval` or a regex assembled from client input. A `{name}` segment matches exactly
    one segment and the value never contains a separator, because the separator is what the
    split already consumed.
    """

    def __init__(self) -> None:
        self._routes: list[Route] = []

    def register(self, method: str, template: str, handler: Handler) -> None:
        method = method.upper()
        if not template.startswith("/"):
            raise ValueError(f"a route template must be absolute; {template} is not")
        for existing in self._routes:
            if existing.method == method and existing.template == template:
                raise ValueError(
                    f"{method} {template} is already registered; two handlers for one route "
                    f"would make which one answers an accident of import order")
        self._routes.append(
            Route(method, template, handler, tuple(_segments(template))))

    def routes(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((route.method, route.template) for route in self._routes))

    def resolve(self, method: str, segments: tuple[str, ...]) -> tuple[Route, dict[str, str]]:
        """The route and its parameters, or `DemoUiError` distinguishing 404 from 405."""
        path_matched = False
        for route in self._routes:
            params = _match(route.segments, segments)
            if params is None:
                continue
            path_matched = True
            if route.method == method.upper():
                return route, params
        raise DemoUiError("method_not_allowed" if path_matched else "not_found")


def host_name(header: str) -> str:
    """The host out of a `Host` header, with the port and any IPv6 brackets removed."""
    if header.startswith("["):
        return header[1:].split("]", 1)[0]
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def _printable(value: str, *, limit: int = 200) -> str:
    """Printable ASCII only, bounded. For the server's own log lines and nothing else."""
    return "".join(c if " " <= c <= "~" else "?" for c in str(value))[:limit]


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _match(template: tuple[str, ...], actual: tuple[str, ...]) -> dict[str, str] | None:
    if len(template) != len(actual):
        return None
    params: dict[str, str] = {}
    for expected, given in zip(template, actual):
        parameter = _PARAMETER.match(expected)
        if parameter is None:
            if expected != given:
                return None
            continue
        if not given:
            return None
        params[parameter.group(1)] = given
    return params


#: The process-wide table. `api.py` calls `register(...)` at import time; this module registers
#: only the liveness route below, so nothing here collides with §5's endpoints.
ROUTER = Router()


def register(method: str, template: str, handler: Handler) -> None:
    """Add a handler to the process-wide router. The registration API other modules use."""
    ROUTER.register(method, template, handler)


def _server_health(request: Request) -> JsonResponse:
    """Liveness, and the only route this module owns.

    Deliberately outside §5's `/demo/...` table so that registering it cannot collide with the
    endpoint module. It reports the number of runs and the registered routes — no version, no
    path, no environment.
    """
    return JsonResponse({
        "status": "ok",
        "runs": len(request.app.registry),
        "routes": [f"{method} {template}" for method, template in request.app.router.routes()],
    })


register("GET", "/demo-ui/health", _server_health)


# ---------------------------------------------------------------------------------------
# Server-sent events.
# ---------------------------------------------------------------------------------------


def trace_stream(run: Run, *, poll_seconds: float = SSE_POLL_SECONDS) -> EventStream:
    """The SSE body for a run's trace, terminated by exactly one `end` message.

    **How termination works, because both event endpoints depend on it.** `Run.stream` yields
    every event in sequence order and returns when the run has reached a terminal status *and*
    every event has been drained — the drain is what stops a run that completes in the same
    breath as its last event from losing it. This generator then sends one final message with
    `event: end` carrying the run's summary, and returns; the handler writes it, flushes, and
    closes the connection. The browser's `EventSource` would otherwise reconnect, so the client
    is expected to call `close()` on `end` and the server does not rely on it doing so: the
    stream is finished either way, and a reconnect gets a fresh replay from sequence 0.
    """

    def messages() -> Iterator[SseMessage]:
        for event in run.stream(poll_seconds=poll_seconds):
            yield SseMessage("trace", event_payload(event), identifier=str(event.sequence))
        yield SseMessage("end", run.summary())

    return EventStream(messages())


def _sse_bytes(message: SseMessage) -> bytes:
    """One SSE frame. `json.dumps` cannot emit a newline inside a string, so `data:` is one
    line by construction and the frame cannot be split by a value."""
    lines = []
    if message.identifier is not None:
        lines.append(f"id: {message.identifier}")
    lines.append(f"event: {message.event}")
    lines.append("data: " + json.dumps(message.data, sort_keys=True, ensure_ascii=False))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


# ---------------------------------------------------------------------------------------
# Static files.
# ---------------------------------------------------------------------------------------

#: Served when `static/` is absent, which it is until the frontend workstream creates it. A
#: clear page rather than a traceback: the server skeleton is testable and runnable before the
#: interface exists, and "the files are not here" is a fact worth stating plainly.
_PLACEHOLDER = (
    "<!doctype html><meta charset=\"utf-8\"><title>Story agent demo</title>"
    "<style>body{font:16px/1.5 system-ui;margin:3rem auto;max-width:40rem}"
    "code{background:#eee;padding:.1rem .3rem}</style>"
    "<h1>Story agent demo</h1>"
    "<p>The server is running. The single-page interface has not been built into "
    "<code>story/demo_ui/static/</code> in this tree yet, so there is nothing to show here.</p>"
    "<p>The JSON API is live; <code>/demo-ui/health</code> lists the registered routes.</p>"
).encode("utf-8")


def resolve_static(static_root: Path, path: str) -> Path:
    """The file a raw request path names. Splits, then defers to `resolve_static_segments`."""
    return resolve_static_segments(static_root, tuple(_segments(path)))


def resolve_static_segments(static_root: Path, segments: Sequence[str]) -> Path:
    """The file a request names, or `DemoUiError`. Traversal-safe by resolution, not by regex.

    Three refusals, and the third is the one a `..` check alone misses: a rejected `..`
    segment, a rejected absolute or drive-qualified path, and — after `resolve()` follows every
    symlink — a rejected result that does not sit inside the resolved static root. The last
    covers a symlink placed inside `static/` pointing at a dotfile, which no amount of string
    inspection would catch.

    Takes **decoded** segments, because the decision has to be made about what the path means
    rather than about how it was spelled: `%2e%2e` is `..`, and a check that ran before
    decoding would see neither.
    """
    segments = [segment for segment in segments if segment]
    if not segments:
        segments = ["index.html"]
    for segment in segments:
        if segment in {".", ".."} or "\\" in segment or "\x00" in segment:
            raise DemoUiError("static_not_found")
    root = static_root.resolve()
    candidate = (root / "/".join(segments)).resolve()
    if not candidate.is_relative_to(root):
        raise DemoUiError("static_not_found")
    if not candidate.is_file():
        raise DemoUiError("static_not_found")
    return candidate


# ---------------------------------------------------------------------------------------
# The request handler.
# ---------------------------------------------------------------------------------------


class DemoUiRequestHandler(BaseHTTPRequestHandler):
    """One request, dispatched to a registered handler or to the static tree.

    `app` is set on the subclass built by `build_handler_class`, because
    `BaseHTTPRequestHandler` is instantiated per request by the server and there is nowhere
    else to put state that outlives one.
    """

    app: DemoUiApp
    protocol_version = "HTTP/1.1"
    server_version = "story-demo-ui"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - the base class names the method
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            raw_path, segments, query = self._split_target()
            try:
                route, params = self.app.router.resolve(method, segments)
            except DemoUiError as unrouted:
                # An unrouted GET outside the API namespaces is a static file request; an
                # unrouted GET *inside* one is a 404 about a route, and answering it with "no
                # such file" would send a reader looking in the wrong place.
                if (method == "GET" and unrouted.code == "not_found"
                        and (not segments or segments[0] not in API_PREFIXES)):
                    self._serve_static(segments)
                    return
                raise
            request = Request(
                method=method, path=raw_path, params=params, query=query,
                body=self._read_body(method), app=self.app)
            self._respond(route.handler(request))
        except DemoUiError as exc:
            self._send_json(exc.status, exc.payload())
        except (InvalidIdentifier, UnknownRun) as exc:
            translated = DemoUiError(
                "invalid_identifier" if isinstance(exc, InvalidIdentifier) else "unknown_run")
            self._send_json(translated.status, translated.payload())
        except (BrokenPipeError, ConnectionResetError):
            # The browser closed a stream or navigated away. Not an error, and there is no
            # socket left to report one on.
            self.close_connection = True
        except Exception:  # noqa: BLE001 - the boundary; nothing above it would report this
            self._log_traceback()
            failure = DemoUiError("internal_error")
            self._send_json(failure.status, failure.payload())

    # -- request parsing -------------------------------------------------------------------

    def _check_host(self) -> None:
        """Refuse a `Host` this server does not answer for.

        A server bound to 127.0.0.1 is still reachable from a page the user visits, through a
        DNS name that resolves to 127.0.0.1 — the rebinding attack. Checking the header closes
        it, and costs a local user nothing because the URL the CLI prints is a loopback one.
        """
        host = (self.headers.get("Host") or "").strip()
        if host and host_name(host) not in _LOOPBACK_NAMES:
            raise DemoUiError("forbidden_host")

    def _split_target(self) -> tuple[str, tuple[str, ...], dict[str, str]]:
        """The raw path, the decoded segments, and the query.

        The segments are decoded **and kept apart**, never rejoined into a string: `%2f` must
        not be able to invent a separator, and the only way to guarantee that is for the split
        to happen before the decoding and for nothing downstream to split again. The raw path
        goes on the request for a handler that wants to report what was asked for.
        """
        if len(self.path) > MAX_PATH_BYTES:
            raise DemoUiError("bad_request")
        split = urlsplit(self.path)
        if len(split.query) > MAX_QUERY_BYTES:
            raise DemoUiError("bad_request")
        decoded = tuple(unquote(segment) for segment in split.path.split("/") if segment)
        if any("\x00" in segment for segment in decoded):
            raise DemoUiError("bad_request")
        query = {key: value for key, value in parse_qsl(split.query, keep_blank_values=True)}
        return split.path, decoded, query

    def _read_body(self, method: str) -> Mapping[str, Any]:
        if method != "POST":
            return {}
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if (self.headers.get("Transfer-Encoding") or "").strip():
                raise DemoUiError("length_required")
            return {}
        try:
            length = int(raw_length)
        except ValueError:
            raise DemoUiError("bad_request") from None
        if length < 0:
            raise DemoUiError("bad_request")
        if length > MAX_BODY_BYTES:
            raise DemoUiError("payload_too_large")
        if length == 0:
            return {}
        media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise DemoUiError("unsupported_media_type")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DemoUiError("malformed_json") from None
        if not isinstance(parsed, dict):
            raise DemoUiError("malformed_json")
        return parsed

    # -- responses --------------------------------------------------------------------------

    def _respond(self, result: Any) -> None:
        if isinstance(result, EventStream):
            self._send_stream(result)
        elif isinstance(result, JsonResponse):
            self._send_json(result.status, result.payload)
        elif isinstance(result, Mapping):
            self._send_json(200, result)
        else:
            raise TypeError(
                f"a handler returned {type(result).__name__}; a handler returns a Mapping, a "
                f"JsonResponse or an EventStream")

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        if status >= 400:
            # A rejected request may have an unread body still in the socket — an over-large
            # `Content-Length` is refused without reading it — and reusing the connection would
            # parse those bytes as the next request line.
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_stream(self, stream: EventStream) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # No length is knowable, so the connection is the frame boundary and must close.
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for message in stream.messages:
                self.wfile.write(_sse_bytes(message))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_static(self, segments: tuple[str, ...]) -> None:
        root = self.app.static_root
        if not root.is_dir():
            if segments in ((), ("index.html",)):
                self._send_bytes(200, "text/html; charset=utf-8", _PLACEHOLDER)
                return
            raise DemoUiError("static_unavailable")
        resolved = resolve_static_segments(root, segments)
        content_type = CONTENT_TYPES.get(resolved.suffix.lower())
        if content_type is None:
            raise DemoUiError("static_not_found")
        self._send_bytes(200, content_type, resolved.read_bytes())

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):  # type: ignore[override]
        """JSON, never the base class's HTML page.

        The default renders `message` and `explain` into an HTML document. Both can carry the
        text of whatever went wrong — including a malformed request line echoed back — so the
        page is replaced rather than restyled. `message` and `explain` are dropped.
        """
        del message, explain
        status = int(code)
        error = DemoUiError(
            _STATUS_CODES.get(status, "bad_request" if status < 500 else "internal_error"))
        try:
            self._send_json(error.status, error.payload())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # -- logging ---------------------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base class name
        """One line to stderr, with the client's own strings sanitised before they reach it.

        The base class's format arguments include the raw request line, which is attacker-chosen
        text going to a terminal; `_printable` is what keeps an escape sequence out of the
        operator's scrollback.
        """
        del format
        detail = " ".join(str(argument) for argument in args[1:]) if len(args) > 1 else ""
        sys.stderr.write(
            f"[demo-ui] {_printable(getattr(self, 'command', '?'))} "
            f"{_printable(getattr(self, 'path', '?'))} {_printable(detail)}\n")

    def _log_traceback(self) -> None:
        """Server-side only. This is the *only* place the exception's text is written, and it
        goes to the operator's terminal rather than to the response body."""
        sys.stderr.write("[demo-ui] handler failed\n")
        sys.stderr.write(traceback.format_exc())


def build_handler_class(app: DemoUiApp) -> type[DemoUiRequestHandler]:
    return type("BoundDemoUiRequestHandler", (DemoUiRequestHandler,), {"app": app})


def build_app(
    *,
    root: Path,
    registry: RunRegistry | None = None,
    router: Router | None = None,
    services: Mapping[str, Any] | None = None,
    static_root: Path | None = None,
) -> DemoUiApp:
    return DemoUiApp(
        root=root,
        registry=registry if registry is not None else RunRegistry(),
        router=router if router is not None else ROUTER,
        static_root=static_root if static_root is not None
        else Path(__file__).resolve().parent / "static",
        services=dict(services or {}),
    )


def build_server(app: DemoUiApp, *, host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """A threaded server on a loopback address, or `ValueError`.

    One thread per connection is what makes SSE workable here: a stream holds its thread for as
    long as the run lasts, and the rest of the interface keeps answering. `daemon_threads` so a
    Ctrl-C is not held open by a stream nobody is reading.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"the demo UI binds to a loopback address only; {host} is not one of "
            f"{', '.join(sorted(LOOPBACK_HOSTS))}")
    server = ThreadingHTTPServer((host, port), build_handler_class(app))
    server.daemon_threads = True
    return server


def serve(
    *,
    root: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    services: Mapping[str, Any] | None = None,
) -> int:
    """Build, announce and run until interrupted. Returns the port actually bound."""
    app = build_app(root=root, services=services)
    server = build_server(app, host=host, port=port)
    bound = server.server_address[1]
    print(f"story demo UI on http://{host}:{bound}/")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        server.shutdown()
        server.server_close()
    return int(bound)


__all__ = [
    "CONTENT_TYPES",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LOOPBACK_HOSTS",
    "MAX_BODY_BYTES",
    "ROUTER",
    "DemoUiApp",
    "DemoUiError",
    "EventStream",
    "Handler",
    "JsonResponse",
    "Request",
    "Route",
    "Router",
    "SseMessage",
    "build_app",
    "build_handler_class",
    "build_server",
    "register",
    "resolve_static",
    "resolve_static_segments",
    "serve",
    "trace_stream",
]
