"""The demo server skeleton: routing, SSE, static files, and the security set §9 names.

Offline, no database, no model. Every HTTP test drives a real `ThreadingHTTPServer` bound to an
ephemeral loopback port — not a hand-rolled fake handler — because the properties under test
are properties of the transport: that a 404 is JSON rather than the base class's HTML page,
that an over-large body is refused before it is read, that a stream terminates.

**The secret test is the one worth reading first.** `test_no_response_carries_an_environment
_marker` puts a unique token into `os.environ` and drives every route in this file, including
the error path and a handler that raises with the token in its own message, then asserts the
token appears in nothing the client received. `story/demo_ui/` reads no environment today, so
the test passes trivially — which is the point of writing it now rather than after the first
endpoint quotes `str(exc)` into a response.
"""

from __future__ import annotations

import ast
import http.client
import json
import os
import re
import threading
from pathlib import Path

import pytest

from story.demo_ui.runs import (
    RUN_ID_PATTERN,
    InvalidIdentifier,
    RunRegistry,
    UnknownRun,
    execute,
    start_background,
    validate_candidate_id,
    validate_run_id,
)
from story.demo_ui.server import (
    ROUTER,
    DemoUiError,
    JsonResponse,
    Router,
    build_app,
    build_server,
    resolve_static,
    trace_stream,
)
from story.demo_ui.trace import TraceCounts

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "story"

ENV_MARKER_NAME = "STORY_DEMO_UI_TEST_MARKER"
ENV_MARKER_VALUE = "s3cr3t-neo4j-password-b7f2"


# ---------------------------------------------------------------------------------------
# A real server, on a real socket.
# ---------------------------------------------------------------------------------------


class Client:
    """Enough of an HTTP client to assert on status, headers and body."""

    def __init__(self, port: int) -> None:
        self.port = port

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None,
                declared_length: int | None = None) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest(method, path, skip_host=headers is not None
                                  and "Host" in headers)
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            if declared_length is not None:
                # Declared but never sent: the server must refuse on the header alone.
                connection.putheader("Content-Length", str(declared_length))
                connection.endheaders()
            elif body is not None:
                connection.putheader("Content-Length", str(len(body)))
                connection.endheaders()
                connection.send(body)
            else:
                connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json(self, method: str, path: str, **kwargs) -> tuple[int, dict]:
        status, _, body = self.request(method, path, **kwargs)
        return status, json.loads(body.decode("utf-8"))


@pytest.fixture
def serve_app():
    """Start a server per test on port 0 and shut it down afterwards."""
    started = []

    def start(*, router=None, registry=None, static_root=None, services=None,
              root: Path | None = None):
        app = build_app(
            root=root if root is not None else REPO_ROOT,
            registry=registry if registry is not None else RunRegistry(),
            router=router if router is not None else Router(),
            static_root=static_root,
            services=services,
        )
        server = build_server(app, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return app, Client(server.server_address[1])

    yield start

    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def ok(request) -> JsonResponse:
    return JsonResponse({"seen": dict(request.params), "query": dict(request.query),
                         "body": dict(request.body)})


# ---------------------------------------------------------------------------------------
# Routing.
# ---------------------------------------------------------------------------------------


def test_a_registered_handler_answers_and_receives_its_path_parameters(serve_app):
    router = Router()
    router.register("GET", "/demo/candidates/{candidate_id}", ok)
    _, client = serve_app(router=router)

    status, payload = client.json("GET", "/demo/candidates/abc?view=story")
    assert status == 200
    assert payload["seen"] == {"candidate_id": "abc"}
    assert payload["query"] == {"view": "story"}


def test_a_path_parameter_never_spans_a_separator(serve_app):
    """A percent-encoded `/` stays inside one segment instead of inventing a route.

    Decoding the whole path before splitting is the classic way this goes wrong, and it is how
    `/demo/runs/{run_id}/events` would be reachable as `/demo/runs/x%2Fevents`.
    """
    router = Router()
    router.register("GET", "/demo/runs/{run_id}", ok)
    _, client = serve_app(router=router)

    status, payload = client.json("GET", "/demo/runs/a%2Fb")
    assert status == 200
    assert payload["seen"] == {"run_id": "a/b"}


def test_an_unknown_path_and_a_wrong_method_are_told_apart(serve_app):
    router = Router()
    router.register("POST", "/demo/generate", ok)
    _, client = serve_app(router=router)

    assert client.json("GET", "/demo/generate")[1]["error"]["code"] == "method_not_allowed"
    assert client.json("GET", "/demo/generate")[0] == 405
    assert client.json("GET", "/demo/nothing-here")[1]["error"]["code"] == "not_found"


def test_two_handlers_cannot_claim_one_route():
    """Import order would otherwise decide which endpoint answers."""
    router = Router()
    router.register("GET", "/demo/prompt-presets", ok)
    with pytest.raises(ValueError):
        router.register("GET", "/demo/prompt-presets", ok)
    router.register("POST", "/demo/prompt-presets", ok)
    assert router.routes() == (("GET", "/demo/prompt-presets"),
                               ("POST", "/demo/prompt-presets"))


def test_the_process_wide_router_carries_the_liveness_route_and_nothing_of_section_five():
    """`api.py` registers §5's endpoints; this module must not have claimed any of them."""
    templates = {template for _, template in ROUTER.routes()}
    assert "/demo-ui/health" in templates
    assert not any(template.startswith("/demo/") for template in templates)


def test_the_liveness_route_reports_the_registered_routes(serve_app):
    _, client = serve_app(router=ROUTER)
    status, payload = client.json("GET", "/demo-ui/health")
    assert status == 200
    assert payload["status"] == "ok"
    assert "GET /demo-ui/health" in payload["routes"]


# ---------------------------------------------------------------------------------------
# Request bodies.
# ---------------------------------------------------------------------------------------


def test_a_json_body_reaches_the_handler(serve_app):
    router = Router()
    router.register("POST", "/demo/evidence-package", ok)
    _, client = serve_app(router=router)

    status, payload = client.json(
        "POST", "/demo/evidence-package", body=json.dumps({"candidate_id": "x"}).encode(),
        headers={"Content-Type": "application/json"})
    assert status == 200
    assert payload["body"] == {"candidate_id": "x"}


def test_an_over_large_body_is_refused_on_its_declared_length_alone(serve_app):
    router = Router()
    router.register("POST", "/demo/generate", ok)
    _, client = serve_app(router=router)

    status, payload = client.json(
        "POST", "/demo/generate", declared_length=512 * 1024,
        headers={"Content-Type": "application/json"})
    assert status == 413
    assert payload["error"]["code"] == "payload_too_large"


def test_a_body_that_is_not_json_is_refused_by_content_type_and_by_parse(serve_app):
    router = Router()
    router.register("POST", "/demo/generate", ok)
    _, client = serve_app(router=router)

    status, payload = client.json("POST", "/demo/generate", body=b"planner_instructions=1",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert (status, payload["error"]["code"]) == (415, "unsupported_media_type")

    status, payload = client.json("POST", "/demo/generate", body=b"{not json",
                                  headers={"Content-Type": "application/json"})
    assert (status, payload["error"]["code"]) == (400, "malformed_json")

    status, payload = client.json("POST", "/demo/generate", body=b"[1, 2]",
                                  headers={"Content-Type": "application/json"})
    assert (status, payload["error"]["code"]) == (400, "malformed_json")


# ---------------------------------------------------------------------------------------
# Errors: JSON, always, and never the exception's own words.
# ---------------------------------------------------------------------------------------


def test_a_failing_handler_returns_structured_json_and_no_traceback(serve_app):
    def explodes(request):
        raise RuntimeError(
            f"connecting to bolt://neo4j:{ENV_MARKER_VALUE}@127.0.0.1:7687 failed")

    router = Router()
    router.register("GET", "/demo/graph/overview", explodes)
    _, client = serve_app(router=router)

    status, headers, body = client.request("GET", "/demo/graph/overview")
    text = body.decode("utf-8")
    assert status == 500
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(text)["error"]["code"] == "internal_error"
    assert "Traceback" not in text
    assert "RuntimeError" not in text
    assert "bolt" not in text
    assert ENV_MARKER_VALUE not in text


def test_an_unsupported_method_produces_json_rather_than_the_base_class_html(serve_app):
    """`BaseHTTPRequestHandler.send_error` renders an HTML page by default."""
    router = Router()
    router.register("GET", "/demo/prompt-presets", ok)
    _, client = serve_app(router=router)

    status, headers, body = client.request("DELETE", "/demo/prompt-presets")
    assert status in (405, 501)
    assert headers["Content-Type"].startswith("application/json")
    assert b"<html" not in body.lower()
    assert json.loads(body.decode("utf-8"))["error"]["code"] == "method_not_allowed"


def test_an_error_detail_that_is_not_a_short_token_is_dropped():
    """The only free string that can accompany an error code is a bounded, matched one."""
    assert DemoUiError("not_found", detail="candidate_id").payload()["error"][
        "detail"] == "candidate_id"
    leaked = DemoUiError("not_found", detail="password=hunter2 at /home/thele/.env line 3")
    assert "detail" not in leaked.payload()["error"]


def test_an_unknown_error_code_becomes_an_internal_error_rather_than_a_new_one():
    assert DemoUiError("something_new").code == "internal_error"
    assert DemoUiError("something_new").status == 500


# ---------------------------------------------------------------------------------------
# Loopback only.
# ---------------------------------------------------------------------------------------


def test_the_server_refuses_to_bind_anything_but_loopback():
    app = build_app(root=REPO_ROOT, router=Router())
    with pytest.raises(ValueError):
        build_server(app, host="0.0.0.0", port=0)
    with pytest.raises(ValueError):
        build_server(app, host="", port=0)


def test_a_rebound_host_header_is_refused(serve_app):
    """A page on the internet can point a DNS name at 127.0.0.1; the `Host` header is the tell."""
    router = Router()
    router.register("GET", "/demo/prompt-presets", ok)
    _, client = serve_app(router=router)

    status, payload = client.json("GET", "/demo/prompt-presets",
                                  headers={"Host": "demo.example.com"})
    assert (status, payload["error"]["code"]) == (403, "forbidden_host")

    assert client.json("GET", "/demo/prompt-presets",
                       headers={"Host": f"127.0.0.1:{client.port}"})[0] == 200


# ---------------------------------------------------------------------------------------
# Static files.
# ---------------------------------------------------------------------------------------


def test_static_serving_is_confined_to_the_static_root(tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
    (tmp_path / "secrets.env").write_text(f"{ENV_MARKER_NAME}={ENV_MARKER_VALUE}\n",
                                          encoding="utf-8")

    assert resolve_static(root, "/").name == "index.html"
    assert resolve_static(root, "/index.html").name == "index.html"
    for attack in ("/../secrets.env", "/../../etc/passwd", "//../secrets.env",
                   "/a/../../secrets.env", "/./../secrets.env"):
        with pytest.raises(DemoUiError):
            resolve_static(root, attack)


def test_a_symlink_out_of_the_static_root_is_refused(tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    outside = tmp_path / "secrets.env"
    outside.write_text(f"{ENV_MARKER_NAME}={ENV_MARKER_VALUE}\n", encoding="utf-8")
    try:
        (root / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem does not allow the test to create a symlink")

    with pytest.raises(DemoUiError):
        resolve_static(root, "/escape.txt")


def test_an_encoded_traversal_is_decoded_before_it_is_judged(serve_app, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
    (tmp_path / "secrets.env").write_text(f"{ENV_MARKER_NAME}={ENV_MARKER_VALUE}\n",
                                          encoding="utf-8")
    _, client = serve_app(static_root=root)

    status, _, body = client.request("GET", "/%2e%2e/secrets.env")
    assert status == 404
    assert ENV_MARKER_VALUE not in body.decode("utf-8")


def test_a_served_file_carries_a_content_type_from_the_explicit_table(serve_app, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
    (root / "app.js").write_text("export const ready = true;\n", encoding="utf-8")
    (root / "notes.rst").write_text("unknown to the table", encoding="utf-8")
    _, client = serve_app(static_root=root)

    status, headers, body = client.request("GET", "/")
    assert (status, headers["Content-Type"]) == (200, "text/html; charset=utf-8")
    assert b"<h1>demo</h1>" in body

    status, headers, _ = client.request("GET", "/app.js")
    assert (status, headers["Content-Type"]) == (200, "text/javascript; charset=utf-8")

    assert client.request("GET", "/notes.rst")[0] == 404


def test_a_missing_static_directory_serves_a_placeholder_rather_than_crashing(
        serve_app, tmp_path):
    """The frontend arrives in a later wave; until then the server still starts and answers."""
    _, client = serve_app(static_root=tmp_path / "not-built-yet")

    status, headers, body = client.request("GET", "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"has not been built" in body

    status, payload = client.json("GET", "/app.js")
    assert (status, payload["error"]["code"]) == (503, "static_unavailable")


def test_an_unrouted_api_path_is_a_missing_route_not_a_missing_file(serve_app, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    _, client = serve_app(static_root=root)
    assert client.json("GET", "/demo/graph/overview")[1]["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------------------
# Server-sent events.
# ---------------------------------------------------------------------------------------


def read_sse(client: Client, path: str) -> list[tuple[str, dict]]:
    """Read a whole stream to EOF and parse it into `(event, data)` pairs."""
    status, headers, body = client.request("GET", path)
    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    assert headers["Cache-Control"] == "no-cache"

    frames = []
    for frame in body.decode("utf-8").split("\n\n"):
        if not frame.strip():
            continue
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        frames.append((fields["event"], json.loads(fields["data"])))
    return frames


def test_a_stream_carries_every_event_in_order_and_ends_once(serve_app):
    registry = RunRegistry()
    run = registry.create("discovery")

    def work(emitter):
        emitter.emit("loading_graph_snapshot", "passed")
        emitter.emit("canonical_series", "passed", counts=TraceCounts(processed=41))
        emitter.emit("preparing_suggestions", "complete", counts=TraceCounts(accepted=3))
        return {"candidates": ["cand:one"]}

    router = Router()
    router.register("GET", "/demo/story-suggestions/{run_id}/events",
                    lambda request: trace_stream(
                        request.app.registry.get(request.params["run_id"]), poll_seconds=0.05))
    _, client = serve_app(router=router, registry=registry)

    start_background(run, work)
    frames = read_sse(client, f"/demo/story-suggestions/{run.run_id}/events")

    assert [name for name, _ in frames] == ["trace", "trace", "trace", "end"]
    assert [data["stage"] for _, data in frames[:3]] == [
        "loading_graph_snapshot", "canonical_series", "preparing_suggestions"]
    assert [data["sequence"] for _, data in frames[:3]] == [0, 1, 2]
    assert frames[-1][1]["status"] == "complete"
    assert frames[-1][1]["event_count"] == 3
    assert run.result() == {"candidates": ["cand:one"]}


def test_a_stream_opened_after_the_run_finished_replays_it_and_terminates(serve_app):
    registry = RunRegistry()
    run = registry.create("generation")
    execute(run, lambda emitter: (emitter.emit("planning", "passed"), {"ok": True})[1])

    router = Router()
    router.register("GET", "/demo/runs/{run_id}/events",
                    lambda request: trace_stream(
                        request.app.registry.get(request.params["run_id"]), poll_seconds=0.05))
    _, client = serve_app(router=router, registry=registry)

    frames = read_sse(client, f"/demo/runs/{run.run_id}/events")
    assert [name for name, _ in frames] == ["trace", "end"]
    assert frames[-1][1]["status"] == "complete"


def test_a_failed_run_streams_its_failure_rather_than_a_false_completion(serve_app):
    registry = RunRegistry()
    run = registry.create("generation")

    def work(emitter):
        emitter.emit("planning", "running")
        raise RuntimeError(f"the model server at {ENV_MARKER_VALUE} refused the request")

    logged: list[str] = []
    router = Router()
    router.register("GET", "/demo/runs/{run_id}/events",
                    lambda request: trace_stream(
                        request.app.registry.get(request.params["run_id"]), poll_seconds=0.05))
    _, client = serve_app(router=router, registry=registry)

    thread = start_background(run, work, error_code="generation_failed", on_error=logged.append)
    thread.join(timeout=5)

    frames = read_sse(client, f"/demo/runs/{run.run_id}/events")
    assert frames[-1][0] == "end"
    assert frames[-1][1]["status"] == "failed"
    assert frames[-1][1]["error_code"] == "generation_failed"
    assert run.result() is None
    # The traceback exists, on the server side, and it is the only place the text survives.
    assert logged and ENV_MARKER_VALUE in logged[0]
    assert ENV_MARKER_VALUE not in json.dumps(frames)


def test_an_unknown_or_malformed_run_id_never_reaches_the_registry_map(serve_app):
    registry = RunRegistry()
    router = Router()
    router.register("GET", "/demo/runs/{run_id}/events",
                    lambda request: trace_stream(
                        request.app.registry.get(request.params["run_id"])))
    _, client = serve_app(router=router, registry=registry)

    status, payload = client.json("GET", "/demo/runs/run-discovery-0009/events")
    assert (status, payload["error"]["code"]) == (404, "unknown_run")

    status, payload = client.json("GET", "/demo/runs/..%2F..%2Fetc/events")
    assert (status, payload["error"]["code"]) == (400, "invalid_identifier")


# ---------------------------------------------------------------------------------------
# The run registry.
# ---------------------------------------------------------------------------------------


def test_a_minted_run_id_matches_the_pattern_the_client_is_checked_against():
    registry = RunRegistry()
    for phase in ("discovery", "generation", "discovery"):
        assert RUN_ID_PATTERN.match(registry.create(phase).run_id)
    assert registry.ids() == ("run-discovery-0001", "run-discovery-0002",
                              "run-generation-0001")


def test_a_run_that_raised_is_failed_and_holds_no_result():
    run = RunRegistry().create("discovery")

    def work(emitter):
        emitter.emit("ranking", "running")
        raise ZeroDivisionError("a detector divided by a zero denominator")

    execute(run, work)
    assert run.status == "failed"
    assert run.error_code() == "run_failed"
    assert run.result() is None
    assert len(run.events()) == 1


def test_a_terminal_run_cannot_be_moved_again():
    run = RunRegistry().create("discovery")
    execute(run, lambda emitter: {"ok": True})
    assert run.status == "complete"
    with pytest.raises(RuntimeError):
        run.fail("run_failed")


def test_an_event_cannot_be_appended_to_a_run_it_does_not_belong_to():
    registry = RunRegistry()
    first, second = registry.create("discovery"), registry.create("discovery")
    event = first.emitter().emit("ranking", "running")
    with pytest.raises(ValueError):
        second.append(event)


def test_client_supplied_ids_are_validated_before_they_are_used():
    assert validate_run_id("run-generation-0007") == "run-generation-0007"
    for bad in ("run-generation-7", "run-other-0001", "../run-discovery-0001",
                "run-discovery-0001; DETACH", "", 7, None):
        with pytest.raises(InvalidIdentifier):
            validate_run_id(bad)

    real = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
            "opendoor:2022Q3:9682f1c1c85a")
    assert validate_candidate_id(real) == real
    for bad in ("cand:x", real + "'", "cand:a:b:c:d:NOTHEX000000", "* MATCH", real.upper()):
        with pytest.raises(InvalidIdentifier):
            validate_candidate_id(bad)


def test_the_candidate_pattern_matches_what_the_key_module_actually_mints():
    """Checked against the committed fixture rather than against the docstring's example."""
    candidate = json.loads(
        (REPO_ROOT / "tests" / "story" / "fixtures" / "story_demo" / "candidate.json")
        .read_text(encoding="utf-8"))
    assert validate_candidate_id(candidate["candidate_id"]) == candidate["candidate_id"]


def test_an_unregistered_run_raises_rather_than_returning_none():
    with pytest.raises(UnknownRun):
        RunRegistry().get("run-discovery-0001")


# ---------------------------------------------------------------------------------------
# No secret reaches the client.
# ---------------------------------------------------------------------------------------


def test_no_response_carries_an_environment_marker(serve_app, tmp_path, monkeypatch):
    """Every route in this file, driven with a marker in the environment and in a `.env`.

    A drift guard rather than a discovery: `story/demo_ui/` imports no `os.environ` read today.
    The day an endpoint renders a provider URL or a config value into a response, this fails.
    """
    monkeypatch.setenv(ENV_MARKER_NAME, ENV_MARKER_VALUE)
    (tmp_path / ".env").write_text(f"{ENV_MARKER_NAME}={ENV_MARKER_VALUE}\n", encoding="utf-8")

    registry = RunRegistry()
    run = registry.create("discovery")

    def explodes(request):
        raise RuntimeError(f"failed with {os.environ[ENV_MARKER_NAME]}")

    router = Router()
    router.register("GET", "/demo/graph/overview", explodes)
    router.register("GET", "/demo/runs/{run_id}/events",
                    lambda request: trace_stream(
                        request.app.registry.get(request.params["run_id"]), poll_seconds=0.05))
    router.register("POST", "/demo/generate", ok)
    _, client = serve_app(router=router, registry=registry, root=tmp_path,
                          static_root=tmp_path / "static")

    execute(run, lambda emitter: (emitter.emit("ranking", "complete"), {"ok": True})[1])

    bodies = [
        client.request("GET", "/demo/graph/overview")[2],
        client.request("GET", "/demo/nothing")[2],
        client.request("GET", "/")[2],
        client.request("GET", "/../.env")[2],
        client.request("GET", f"/demo/runs/{run.run_id}/events")[2],
        client.request("POST", "/demo/generate", body=b"{}",
                       headers={"Content-Type": "application/json"})[2],
        client.request("POST", "/demo/generate", declared_length=512 * 1024,
                       headers={"Content-Type": "application/json"})[2],
        client.request("DELETE", "/demo/generate")[2],
    ]
    for body in bodies:
        text = body.decode("utf-8", errors="replace")
        assert ENV_MARKER_VALUE not in text
        assert ENV_MARKER_NAME not in text


def executable_source(path: Path) -> str:
    """The module with its docstrings removed — `tests/ontology/test_package_structure.py:30`.

    The scan below is about what the code *does*. Both modules explain in prose why they never
    read the environment, and a scan that could not tell an explanation from a call would be a
    scan whose only fix is to delete the explanation.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_interface_modules_read_no_environment_at_all():
    """The structural half of the test above: no `os.environ`, no `getenv`, no `dotenv`."""
    for path in sorted((PACKAGE / "demo_ui").rglob("*.py")):
        source = executable_source(path)
        for reach in ("os.environ", "getenv", "dotenv", "load_env"):
            assert reach not in source, f"{path.name} names {reach}"


# ---------------------------------------------------------------------------------------
# The dependency runs one way.
# ---------------------------------------------------------------------------------------


def imports_of(path: Path) -> set[str]:
    """Every module a file imports, relative imports resolved — the pattern
    `tests/story/test_story_package_structure.py:100` uses, restated for the one rule below."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    relative = path.resolve().relative_to(REPO_ROOT)
    parts = list(relative.parts)
    parts[-1] = parts[-1][: -len(".py")]
    package_parts = parts if path.name == "__init__.py" else parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    found.add(node.module)
                continue
            base = list(package_parts)
            for _ in range(node.level - 1):
                if base:
                    base.pop()
            if node.module:
                base.extend(node.module.split("."))
            found.add(".".join(base))
    return found


def accepted_modules() -> list[Path]:
    return sorted(
        [p for p in (PACKAGE / "stages").rglob("*.py") if "__pycache__" not in p.parts]
        + [p for p in (PACKAGE / "core").rglob("*.py") if "__pycache__" not in p.parts]
        + [PACKAGE / "pipeline.py", PACKAGE / "contracts.py"])


def test_no_accepted_stage_imports_the_demo_interface():
    """§1: `demo_ui` imports the pipeline; the pipeline must never import `demo_ui`.

    Written in this file rather than added to `test_story_package_structure.py` so the rule
    belongs to the workstream that could break it. It is the guard that keeps the interface from
    becoming load-bearing: the story agent must run, and its suite must pass, with
    `story/demo_ui/` deleted.
    """
    walked = accepted_modules()
    assert len(walked) > 20, "the walked set is empty or shrank unexpectedly"
    offences = [
        f"{path.relative_to(REPO_ROOT)} imports {imported}"
        for path in walked
        for imported in sorted(imports_of(path))
        if imported == "story.demo_ui" or imported.startswith("story.demo_ui.")
    ]
    assert offences == []


def test_the_demo_interface_never_reaches_the_driver_or_the_composition_root():
    """Why `cli.py` passes factories in `services` instead of `demo_ui` building a context.

    `tests/story/test_story_package_structure.py::test_no_driver_is_reachable_from_the_contract
    _the_core_or_any_stage` exempts five named entry modules and walks the closure of the rest;
    a `demo_ui` module importing `story.context` would put `neo4j` in that closure. The rule is
    restated here as a direct check so a failure names the file that broke it.
    """
    forbidden = {"neo4j", "story.context", "story.providers.neo4j_connection"}
    offences = [
        f"{path.name} imports {imported}"
        for path in sorted((PACKAGE / "demo_ui").rglob("*.py"))
        for imported in sorted(imports_of(path))
        if imported in forbidden or imported.split(".")[0] == "neo4j"
    ]
    assert offences == []


def test_the_ui_verb_exists_and_leaves_the_demo_verb_alone():
    from story import cli

    parser = cli.build_parser()
    parsed = parser.parse_args(["ui"])
    assert parsed.handler is cli.cmd_ui
    assert (parsed.host, parsed.port) == ("127.0.0.1", 8765)

    parsed = parser.parse_args(["ui", "--host", "localhost", "--port", "0"])
    assert (parsed.host, parsed.port) == ("localhost", 0)

    demo = parser.parse_args(["demo", "--candidate-id", "cand:x"])
    assert demo.handler is cli.cmd_demo
    assert (demo.candidate_id, demo.out, demo.live) == ("cand:x", None, False)


def test_the_printed_url_is_a_loopback_one():
    """The one string a reader copies out of the terminal, checked rather than assumed."""
    source = (PACKAGE / "demo_ui" / "server.py").read_text(encoding="utf-8")
    printed = re.findall(r'print\(f?"([^"]*http[^"]*)"', source)
    assert printed
    for line in printed:
        assert "{host}" in line and "{bound}" in line
