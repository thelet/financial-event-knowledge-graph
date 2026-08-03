"""The story-owned read adapter: what crosses the boundary, and what cannot (S0c, D1).

Three kinds of test, split by what they need:

    unmarked   the protocol, the `.env` layering, the conversion, the refusals, the structural
               scan, and the server-side timeout — driven either with `RecordedReadExecutor` or
               with a driver stub that records what it was handed and connects to nothing
    @neo4j     the live 5.26.28 Community server: connectivity, one real parameterised read and
               the plain-types claim against real rows

`pytest -m "not live and not neo4j"` is green with no container running, and the marked tests
skip in two stages the way `tests/graph/test_loader.py` does: unconfigured is a skip, then
unreachable is a skip, and neither is a failure.

**Nothing here writes to the database, so there is no cleanup step.** That is not an omission
and not an oversight: the adapter has no method that could write, the three live tests issue
`MATCH`/`RETURN` only, and no fixture creates a node, an index or a constraint. A teardown
here would have nothing to delete. `tests/graph/test_loader.py` needs `delete_test_rows`
because it loads an export; this file's whole point is that it never could.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any, Mapping

import pytest
from conftest import RecordedReadExecutor
from neo4j import Query, RoutingControl
from neo4j.exceptions import ServiceUnavailable
from neo4j.graph import Graph, Node
from neo4j.spatial import CartesianPoint
from neo4j.time import Date, DateTime, Duration
from pydantic import SecretStr

from story.context import GRAPH_RUNS_DIRNAME, STORY_RUNS_DIRNAME, build_story_context
from story.contracts import ReadQueryExecutor
from story.providers.neo4j_connection import (
    CONNECTIVITY_PROBE,
    ENV_DATABASE,
    ENV_PASSWORD,
    ENV_URI,
    ENV_USER,
    Neo4jReadExecutor,
    StoryGraphCredentialError,
    StoryGraphResultError,
    StoryGraphUnconfiguredError,
    StoryGraphUnreachableError,
    StoryNeo4jSettings,
    load_neo4j_settings,
    open_read_executor,
    plain_row,
    plain_value,
    read_env_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STORY_PACKAGE = REPO_ROOT / "story"

#: A metric and quarter that exist in the loaded run (IMPLEMENTATION_STEPS §0). The
#: observation *id* is never written down — §6.3's cluster is real, its digests are not
#: something to hard-code — so the live tests look one up and then read it back.
LIVE_METRIC = "adjusted_ebitda"
LIVE_PERIOD = "2022Q3"

OBSERVATION_ID_FOR_METRIC_PERIOD = (
    "MATCH (o:Observation {metric_id: $metric, period_key: $period}) "
    "RETURN o.observation_id AS observation_id "
    "ORDER BY o.observation_id LIMIT 1"
)
OBSERVATION_BY_ID = (
    "MATCH (o:Observation {observation_id: $observation_id}) "
    "RETURN o.observation_id AS observation_id, o.metric_id AS metric_id, "
    "o.period_key AS period_key, o.value AS value, o.unit AS unit, "
    "o.warning_codes AS warning_codes, o.ambiguity_codes AS ambiguity_codes"
)
PASSAGE_WITH_A_LIST_PROPERTY = (
    "MATCH (p:Passage) WHERE p.heading_path IS NOT NULL "
    "RETURN p.passage_id AS passage_id, p.heading_path AS heading_path "
    "ORDER BY p.passage_id LIMIT 1"
)
EVIDENCE_EDGE_WITH_A_LIST_PROPERTY = (
    "MATCH ()-[e:EVIDENCED_BY]->() WHERE e.block_ids IS NOT NULL "
    "RETURN e.quoted_text AS quoted_text, e.block_ids AS block_ids LIMIT 1"
)


def settings(**overrides: Any) -> StoryNeo4jSettings:
    """A fully configured settings object, aimed at a port nothing listens on by default."""
    fields: dict[str, Any] = dict(
        configured_uri="bolt://127.0.0.1:7687",
        configured_database="neo4j",
        user="neo4j",
        password=SecretStr("not-a-real-password"),
        connect_timeout_seconds=1.0,
        query_timeout_seconds=5.0,
    )
    fields.update(overrides)
    return StoryNeo4jSettings(**fields)


class CapturingDriver:
    """A driver that records what it was asked and connects to nothing.

    Enough of `neo4j.Driver` for `Neo4jReadExecutor` and no more: anything else the adapter
    started calling would fail here as an `AttributeError`, which is the point — this stub is
    also an assertion about how small the driver surface the adapter uses is.
    """

    def __init__(self, records: tuple[Mapping[str, Any], ...] = ()) -> None:
        self.records = records
        self.calls: list[dict[str, Any]] = []
        self.closed = 0

    def execute_query(self, query_, parameters_=None, *, routing_=None, database_=None, **kw):
        self.calls.append({"query": query_, "parameters": parameters_,
                           "routing": routing_, "database": database_, "kwargs": kw})
        return (self.records, None, None)

    def verify_connectivity(self) -> None:
        return None

    def close(self) -> None:
        self.closed += 1


# -- the protocol, satisfied with and without a driver ----------------------------------------


def test_a_recorded_executor_satisfies_the_read_query_executor_protocol_with_no_server():
    """Driven through the protocol, not checked with `isinstance` against it.

    A `runtime_checkable` protocol only proves some attributes exist; what `story/contracts.py`
    claims is that a stage can be written against `ReadQueryExecutor` and exercised with
    `neo4j` uninstalled, and the way to show that is to call every method.
    """
    executor: ReadQueryExecutor = RecordedReadExecutor(
        rows_by_statement={CONNECTIVITY_PROBE: ({"ok": 1},)})

    rows = executor.read(CONNECTIVITY_PROBE, {}, timeout_seconds=5.0)
    health = executor.verify_connectivity()
    executor.close()

    assert rows == ({"ok": 1},)
    assert health.ok is True
    assert executor.calls[0].timeout_seconds == 5.0  # type: ignore[attr-defined]
    assert executor.closed is True  # type: ignore[attr-defined]


def test_the_bolt_adapter_satisfies_the_same_protocol_without_touching_a_server():
    """`Neo4jReadExecutor` conforms structurally — no base class, no registration."""
    driver = CapturingDriver(records=({"observation_id": "obs:x"},))
    executor: ReadQueryExecutor = Neo4jReadExecutor(settings(), driver)  # type: ignore[arg-type]

    rows = executor.read(OBSERVATION_BY_ID, {"observation_id": "obs:x"}, timeout_seconds=2.5)
    executor.close()

    assert rows == ({"observation_id": "obs:x"},)
    assert driver.closed == 1


@pytest.mark.parametrize("build", [
    lambda: RecordedReadExecutor(),
    lambda: Neo4jReadExecutor(settings(), CapturingDriver()),  # type: ignore[arg-type]
], ids=["recorded", "bolt"])
def test_read_refuses_to_be_called_without_a_timeout(build):
    """`timeout_seconds` is keyword-only with no default, so omitting it cannot compile away.

    §16 requires a timeout on every statement. A default would be how one call site ends up
    without one and nobody notices until a runaway read holds a connection open.
    """
    executor = build()
    with pytest.raises(TypeError):
        executor.read(CONNECTIVITY_PROBE, {})  # type: ignore[call-arg]


# -- what actually reaches the driver ---------------------------------------------------------


def test_the_statement_reaches_the_driver_character_for_character_with_values_only_bound():
    """No interpolation anywhere on the path, and the value never appears in the text."""
    driver = CapturingDriver()
    executor = Neo4jReadExecutor(settings(), driver)  # type: ignore[arg-type]

    executor.read(OBSERVATION_BY_ID, {"observation_id": "obs:adjusted-ebitda:x"},
                  timeout_seconds=3.0)

    call = driver.calls[0]
    assert call["query"].text == OBSERVATION_BY_ID
    assert call["parameters"] == {"observation_id": "obs:adjusted-ebitda:x"}
    assert "obs:adjusted-ebitda:x" not in call["query"].text
    assert "$observation_id" in call["query"].text


def test_the_recorded_executor_receives_the_statement_character_for_character_too():
    """The fake every later step will use records the text, not a normalised form of it."""
    executor = RecordedReadExecutor()
    executor.read(OBSERVATION_BY_ID, {"observation_id": "obs:x"}, timeout_seconds=1.5)

    assert executor.calls[0].statement == OBSERVATION_BY_ID
    assert executor.calls[0].parameters == {"observation_id": "obs:x"}
    assert executor.calls[0].timeout_seconds == 1.5


def test_every_statement_is_wrapped_so_the_timeout_is_applied_by_the_server():
    """The ceiling leaves this process as transaction metadata, and this process enforces none.

    Two halves, because either alone is weak. `neo4j.Query(text, timeout=…)` is the only
    spelling the driver turns into the `BEGIN` message's transaction metadata — the same text
    passed as a plain `str` is accepted and the ceiling silently dropped — so the first
    assertion is that what reaches `execute_query` is a `Query` carrying exactly the value the
    caller asked for, per call and not once at construction.

    The second is what makes *server-side* a claim rather than a label: a driver that takes
    fifty times the ceiling to answer still returns its rows. If `read` held a stopwatch of its
    own, that call could not come back — the adapter would be enforcing the ceiling itself
    against a server that had never been told about it. It is deterministic in the only
    direction that matters: a client-side deadline of 1 ms cannot survive a 50 ms call.

    **This replaces a live test that raced a 1 ms ceiling against a 1.4-second statement.**
    *(Measured 2026-08-03 against 5.26.28, by running the old statement in a loop:
    `db.transaction.monitor.check.interval` is `2s`, so the server only notices an expired
    transaction every two seconds, and a statement that finishes inside one window is never
    terminated. It completed 3 times in 10 with nothing else running.)* A test that has to be
    re-run teaches a reader to re-run rather than to read.
    """
    driver = CapturingDriver()
    executor = Neo4jReadExecutor(settings(), driver)  # type: ignore[arg-type]

    executor.read(CONNECTIVITY_PROBE, {}, timeout_seconds=7.5)

    query = driver.calls[0]["query"]
    assert isinstance(query, Query)
    assert query.timeout == 7.5

    class UnhurriedDriver(CapturingDriver):
        """Answers, eventually. Nothing else about it differs."""

        def execute_query(self, *args: Any, **kwargs: Any):
            time.sleep(0.05)
            return super().execute_query(*args, **kwargs)

    unhurried = UnhurriedDriver(records=({"ok": 1},))
    rows = Neo4jReadExecutor(settings(), unhurried).read(  # type: ignore[arg-type]
        CONNECTIVITY_PROBE, {}, timeout_seconds=0.001)

    assert rows == ({"ok": 1},)
    assert unhurried.calls[0]["query"].timeout == 0.001


def test_reads_are_issued_with_read_routing_because_the_server_refuses_a_write_under_it():
    """The second of story's two write controls, pinned so it cannot be quietly dropped.

    F9, measured twice against this 5.26.28 Community instance: a write clause under
    `routing_=READ` is refused by the *server* with `Neo.ClientError.Statement.AccessMode`, and
    the identical statement is accepted under `routing_=WRITE`. §16 originally called routing
    "intent, not a control" and this test's name said so too; both were wrong, and a maintainer
    who believed them would delete the argument this test asserts.

    It is still not access *control* — nothing stops this module from being changed to WRITE —
    so the structural scan proving no story statement holds a write clause remains the first
    control. The live half of the claim is deliberately not asserted anywhere in this suite:
    proving it means issuing a write clause against the loaded graph, which this workstream may
    not do. It rests on F9's two measurements, recorded in
    `plans/llm-agent/IMPLEMENTATION_STEPS.md`.
    """
    driver = CapturingDriver()
    Neo4jReadExecutor(settings(), driver).read(  # type: ignore[arg-type]
        CONNECTIVITY_PROBE, {}, timeout_seconds=1.0)

    assert driver.calls[0]["routing"] is RoutingControl.READ
    assert driver.calls[0]["database"] == "neo4j"


def test_a_zero_timeout_is_refused_because_the_server_reads_it_as_no_timeout():
    """Measured 2026-08-03 against 5.26.28, and the reason this guard exists at all.

    A 1.5-second statement completed under `timeout=0` and under `timeout=None` alike, and was
    terminated under `timeout=0.001`. So zero is not the tightest possible ceiling; it is the
    absence of one, and accepting it would defeat §16 while looking like compliance.
    """
    executor = Neo4jReadExecutor(settings(), CapturingDriver())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="no timeout"):
        executor.read(CONNECTIVITY_PROBE, {}, timeout_seconds=0)


# -- configuration: the same layering the loader uses, restated ---------------------------------


def write_env(path: Path, **values: str) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def test_the_environment_beats_dot_env_which_beats_the_committed_config(tmp_path):
    """`config/graph.yaml`, then `.env`, then the process environment — later wins."""
    config = tmp_path / "graph.yaml"
    config.write_text("neo4j:\n  uri: bolt://from-yaml:7687\n  database: neo4j\n",
                      encoding="utf-8")
    env = write_env(tmp_path / ".env", NEO4J_URI="bolt://from-dotenv:7687",
                    NEO4J_USER="dotenv-user", NEO4J_PASSWORD="dotenv-password")

    from_yaml = load_neo4j_settings(config, tmp_path / "absent", environ={})
    from_dotenv = load_neo4j_settings(config, env, environ={})
    from_environ = load_neo4j_settings(config, env, environ={ENV_URI: "bolt://from-env:7687"})

    assert from_yaml.resolved_uri == "bolt://from-yaml:7687"
    assert from_dotenv.resolved_uri == "bolt://from-dotenv:7687"
    assert from_environ.resolved_uri == "bolt://from-env:7687"
    assert from_environ.user == "dotenv-user"


def test_an_empty_environment_variable_is_an_override_to_empty_not_an_absence(tmp_path):
    """A typo in `.env` must not silently restore the committed default.

    Restoring it would turn one blank line into a story run that read a different database
    than the one the operator meant and cited it as this graph's.
    """
    config = tmp_path / "graph.yaml"
    config.write_text("neo4j:\n  uri: bolt://from-yaml:7687\n  database: neo4j\n",
                      encoding="utf-8")

    resolved = load_neo4j_settings(config, tmp_path / "absent", environ={ENV_URI: ""})

    assert resolved.configured_uri == ""
    with pytest.raises(StoryGraphUnconfiguredError):
        resolved.resolved_uri


def test_the_dot_env_parser_strips_export_quotes_and_whitespace(tmp_path):
    """The three-line subtlety the graph layer records: Compose strips these and `split` does not."""
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n"
        "\n"
        "export NEO4J_USER = neo4j \n"
        "NEO4J_PASSWORD='quoted-password'\n"
        'NEO4J_URI="bolt://localhost:7687"\n'
        "not-a-pair\n",
        encoding="utf-8",
    )

    assert read_env_file(path) == {
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "quoted-password",
        "NEO4J_URI": "bolt://localhost:7687",
    }
    assert read_env_file(tmp_path / "absent") == {}


def test_the_real_config_names_a_target_and_carries_no_credential():
    """The committed `config/graph.yaml`, read the way this adapter reads it."""
    resolved = load_neo4j_settings(env_path=REPO_ROOT / "absent-on-purpose", environ={})

    assert resolved.resolved_uri.startswith("bolt://")
    assert resolved.resolved_database
    assert resolved.user is None and resolved.password is None
    with pytest.raises(StoryGraphCredentialError):
        resolved.auth


def test_the_settings_carry_no_load_setting():
    """`StoryNeo4jSettings` is not `GraphSettings` and must not grow into it.

    D1's "must not implement" list is a set of *absences*, and an absence is only checkable by
    naming it. A batch size or a replace policy on a read-only adapter's settings would be one
    attribute access away from something that is not read-only.
    """
    forbidden = {"node_batch_size", "edge_batch_size", "replace_policy", "schema_version",
                 "graph_runs_root"}

    assert forbidden.isdisjoint(vars(settings()))
    assert type(settings()).__name__ != "GraphSettings"


# -- refusals: unnamed target, missing credential, unreachable server ---------------------------


def test_an_unnamed_target_is_a_typed_refusal_rather_than_a_connection_to_a_default():
    """No URI resolved means no driver, and the failure names which variable to set."""
    with pytest.raises(StoryGraphUnconfiguredError, match=ENV_URI):
        open_read_executor(settings(configured_uri=None))
    with pytest.raises(StoryGraphUnconfiguredError, match=ENV_DATABASE):
        open_read_executor(settings(configured_database="   "))


@pytest.mark.parametrize("missing, variable", [
    ({"user": None}, ENV_USER),
    ({"user": "  "}, ENV_USER),
    ({"password": None}, ENV_PASSWORD),
    ({"password": SecretStr("")}, ENV_PASSWORD),
], ids=["no-user", "blank-user", "no-password", "blank-password"])
def test_a_missing_credential_is_a_typed_refusal_naming_the_variable(missing, variable):
    with pytest.raises(StoryGraphCredentialError, match=variable):
        open_read_executor(settings(**missing))


def test_an_unreachable_server_is_reported_as_a_health_status_and_never_raised():
    """A database that is not running is an ordinary state for the freshness gate to branch on.

    Port 1 is refused immediately, so this runs offline and fast — which is itself the
    measured reason `verify_connectivity` calls the driver's own check before the probe:
    against a refused port `Driver.verify_connectivity()` fails in 0.0s while `execute_query`
    retries a managed transaction for 35 seconds (measured 2026-08-03).
    """
    executor = open_read_executor(settings(configured_uri="bolt://127.0.0.1:1"))
    with executor:
        health = executor.verify_connectivity()

    assert health.ok is False
    assert health.status == "unavailable"
    assert "127.0.0.1:1" in (health.detail or "")


def test_a_transport_fault_on_a_read_is_a_story_error_not_a_leaked_driver_exception():
    """Reaching the server is this module's problem; the statement is the caller's.

    A `ServiceUnavailable` escaping into a retrieval tool would make every tool import the
    driver's exception module to handle it, which is the coupling the adapter exists to stop.
    A statement fault — a `Neo4jError` carrying the server's own code, a transaction timeout
    among them — is deliberately *not* wrapped, because that code is what the tool has to act
    on.

    Driven with a stub that raises rather than against a dead port: `execute_query` retries a
    managed transaction for 35 seconds before giving up (measured 2026-08-03), and a
    thirty-five-second offline test is one somebody eventually deletes.
    """
    class RefusingDriver(CapturingDriver):
        def execute_query(self, *args, **kwargs):
            raise ServiceUnavailable("Couldn't connect to 127.0.0.1:1")

    executor = Neo4jReadExecutor(settings(), RefusingDriver())  # type: ignore[arg-type]
    with pytest.raises(StoryGraphUnreachableError, match="bolt://127.0.0.1:7687"):
        executor.read(CONNECTIVITY_PROBE, {}, timeout_seconds=1.0)


def test_the_password_appears_in_no_repr_and_in_no_health_detail():
    """`SecretStr` masks it, and the executor keeps the settings object out of its `repr`."""
    secret = "correct-horse-battery-staple"
    configured = settings(password=SecretStr(secret))
    executor = Neo4jReadExecutor(configured, CapturingDriver())  # type: ignore[arg-type]

    rendered = f"{configured!r} {executor!r} {configured.password!r}"

    assert secret not in rendered
    assert "**********" in repr(configured.password)
    assert "bolt://127.0.0.1:7687" in repr(executor)


# -- rows cross as plain Python values ----------------------------------------------------------


def assert_plain(value: Any, where: str = "row") -> None:
    """Recursive: no driver type at any depth, not merely at the top of the row."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert type(key) is str, f"{where}: key {key!r} is {type(key).__name__}"
            assert_plain(item, f"{where}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_plain(item, f"{where}[{index}]")
        return
    assert type(value) in (str, bool, int, float, bytes, bytearray, type(None)), (
        f"{where} is {type(value).__module__}.{type(value).__name__}")


def test_temporal_and_spatial_values_are_converted_rather_than_passed_through():
    """None of these is in the graph today — the branch exists so that day is not a leak.

    *(Verified 2026-08-03: `db.schema.nodeTypeProperties` and `relTypeProperties` over the
    loaded run report five property types — Boolean, Double, Long, String, StringArray — and
    nothing temporal or spatial.)* A projection version that started writing a `date` would
    otherwise put a `neo4j.time.Date` inside an evidence package, and nothing would fail until
    something tried to serialise it.

    **This test found a defect in the first conversion.** `Duration` and `Point` are `tuple`
    subclasses, so a generic sequence branch placed ahead of them turned `Duration(days=3)`
    into `[0, 3, 0, 0]` — plain Python, and wrong. The specific branches now come first.
    """
    converted = plain_row({
        "when": DateTime(2022, 9, 30, 12, 0, 0),
        "day": Date(2022, 9, 30),
        "span": Duration(days=3),
        "where": CartesianPoint((1.0, 2.0)),
        "nested": [Date(2022, 9, 30), {"inner": Date(2022, 10, 1)}],
    })

    assert converted["when"] == "2022-09-30T12:00:00.000000000"
    assert converted["day"] == "2022-09-30"
    assert converted["span"] == "P3D"
    assert converted["where"] == {"srid": 7203, "coordinates": [1.0, 2.0]}
    assert converted["nested"] == ["2022-09-30", {"inner": "2022-10-01"}]
    assert_plain(converted)


def test_a_whole_node_is_refused_rather_than_flattened_into_an_unnamed_blob():
    """`RETURN o` is a query-writing mistake, and silently flattening it would hide one.

    Flattening a `Node` to its properties satisfies "no driver type escaped" while dropping
    its labels and its identity, and §9 requires named return fields precisely so a tool's
    result shape is something code decided rather than something the graph happened to have.

    **The second defect this file found.** `neo4j.graph.Entity` *is* a `Mapping` over its
    properties, so the first conversion's mapping branch flattened a node into
    `{"observation_id": "obs:x"}` and returned it, labels gone, without raising anything.
    """
    node = Node(Graph(), "4:x:1", 1, ["Observation"], {"observation_id": "obs:x"})

    with pytest.raises(StoryGraphResultError, match="Node"):
        plain_row({"o": node})
    with pytest.raises(StoryGraphResultError, match="named properties"):
        plain_value(node, field="o")


def test_a_value_with_no_plain_form_is_refused_by_name():
    """The fallback is a refusal, not a `str()` — an invented rendering is an invented fact."""
    with pytest.raises(StoryGraphResultError, match="object"):
        plain_value(object(), field="mystery")


def test_plain_scalars_and_lists_cross_untouched():
    row = plain_row({"id": "obs:x", "value": -211000000.0, "ok": True, "n": 3,
                     "codes": [], "path": ["8-K", "Item 2.02"], "absent": None})

    assert row == {"id": "obs:x", "value": -211000000.0, "ok": True, "n": 3,
                   "codes": [], "path": ["8-K", "Item 2.02"], "absent": None}
    assert_plain(row)


# -- the composition root ------------------------------------------------------------------------


def test_building_a_context_around_a_supplied_executor_opens_no_connection():
    """The injection D1 requires: a stage-level test never needs a server, or an environment."""
    recorded = RecordedReadExecutor()

    with build_story_context(executor=recorded) as context:
        assert context.executor is recorded
        assert context.graph_runs_root == context.root / GRAPH_RUNS_DIRNAME
        assert context.story_runs_root == context.root / STORY_RUNS_DIRNAME

    assert recorded.closed is True


def test_context_paths_can_be_redirected_and_an_absolute_override_wins(tmp_path):
    context = build_story_context(
        tmp_path, executor=RecordedReadExecutor(),
        graph_runs_root="elsewhere/graph_runs", story_runs_root=tmp_path / "out")

    assert context.graph_runs_root == tmp_path / "elsewhere/graph_runs"
    assert context.story_runs_root == tmp_path / "out"


def test_the_context_names_the_same_graph_runs_root_the_committed_config_does():
    """Two files state `data/graph_runs`; this is what keeps them from drifting apart.

    `config/graph.yaml`'s `paths.graph_runs_root` is the graph layer's spelling and
    `story/context.py`'s constant is this package's. Reading the other layer's `paths:` block
    at runtime would make the story context depend on a section §4.1 does not grant it, so the
    duplication is deliberate — and checked here rather than trusted.
    """
    import yaml

    document = yaml.safe_load((REPO_ROOT / "config" / "graph.yaml").read_text(encoding="utf-8"))

    assert document["paths"]["graph_runs_root"] == GRAPH_RUNS_DIRNAME


# -- structural: no write clause in any Cypher this package holds ----------------------------------

#: Every clause that could change the database, plus the bulk-import form. Matched
#: case-insensitively inside a Cypher literal, because a lowercase `merge` is still a write.
WRITE_CLAUSE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV)\b", re.IGNORECASE)

#: What makes a string literal Cypher rather than prose. Upper case only: this is the
#: repository's house spelling for every keyword in every statement, and matching prose would
#: make "the value is not set" a violation.
CYPHER_LITERAL = re.compile(r"\b(MATCH|RETURN|UNWIND|CALL|WITH|WHERE|YIELD)\b")


def executable_source(path: Path) -> ast.Module:
    """The module with its docstrings removed, re-parsed.

    The technique `tests/ontology/test_package_structure.py:30-46` uses, for the same reason:
    a structural rule is about what the code *does*. This file's own docstrings name `CREATE`
    and `MERGE` in order to say they are forbidden, and a rule that could not tell that from a
    statement would forbid explaining itself.
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
    return ast.parse(ast.unparse(tree))


def cypher_literals() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in sorted(STORY_PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(executable_source(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and CYPHER_LITERAL.search(node.value):
                found.append((path, node.value))
    return found


def test_the_cypher_scan_finds_something_to_scan():
    """A rule over an empty set passes without examining anything.

    At S0c there is exactly one statement in the package — the connectivity probe — and the
    guard is what stops this file reading as green after a refactor moved every literal out of
    the scan's reach.
    """
    literals = cypher_literals()
    assert literals, "no Cypher literal found in story/; the rule below proves nothing"
    assert any(text == CONNECTIVITY_PROBE for _, text in literals)


def test_no_cypher_literal_anywhere_in_the_story_package_contains_a_write_clause():
    """Every Cypher constant in `story/`, not just the retrieval stage's (§16).

    The plan's first draft scoped this to `stages/retrieval/` while §7's freshness gate also
    issues Cypher, so the primary read-only guarantee did not cover the stage that ships
    first. Scanning the whole package is the correction, and it is written now — with one
    statement to walk — so it cannot be forgotten when there are fifteen.
    """
    offences = [
        f"{path.relative_to(REPO_ROOT)}: {text!r}"
        for path, text in cypher_literals()
        if WRITE_CLAUSE.search(text)
    ]
    assert offences == []


# -- against the live server -----------------------------------------------------------------


def live_settings() -> StoryNeo4jSettings:
    resolved = load_neo4j_settings()
    try:
        resolved.resolved_uri, resolved.resolved_database, resolved.auth
    except (StoryGraphUnconfiguredError, StoryGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    return resolved


@pytest.fixture()
def live_executor():
    """An executor against the running instance, or a skip. **No teardown of data.**

    The only thing closed is the driver. Nothing in this file writes, so there is nothing to
    delete — see the module docstring.
    """
    executor = open_read_executor(live_settings())
    health = executor.verify_connectivity()
    if not health.ok:
        executor.close()
        pytest.skip(f"local Neo4j is unreachable ({health.status}: {health.detail}); "
                    "start it with `docker compose up -d`")
    with executor:
        yield executor


@pytest.mark.neo4j
def test_connectivity_verification_against_the_running_instance_reports_ok(live_executor):
    """Reachable, authenticated, and answering a read under these credentials."""
    health = live_executor.verify_connectivity()

    assert health.ok is True
    assert health.status == "ok"
    assert live_executor.database in (health.detail or "")


@pytest.mark.neo4j
def test_one_real_parameterised_read_returns_the_observation_it_was_asked_for(live_executor):
    """A real id, looked up rather than invented, then read back by that id.

    Hard-coding the digest would make this test a claim about a snapshot; §6.3's 2022Q3
    adjusted-EBITDA cluster is the durable fact, and the id is derived from it.
    """
    found = live_executor.read(
        OBSERVATION_ID_FOR_METRIC_PERIOD,
        {"metric": LIVE_METRIC, "period": LIVE_PERIOD},
        timeout_seconds=10.0,
    )
    assert found, f"the loaded run holds no {LIVE_METRIC} observation for {LIVE_PERIOD}"
    observation_id = found[0]["observation_id"]

    rows = live_executor.read(
        OBSERVATION_BY_ID, {"observation_id": observation_id}, timeout_seconds=10.0)

    assert len(rows) == 1
    assert rows[0]["observation_id"] == observation_id
    assert rows[0]["metric_id"] == LIVE_METRIC
    assert rows[0]["period_key"] == LIVE_PERIOD
    assert isinstance(rows[0]["value"], float)


@pytest.mark.neo4j
@pytest.mark.parametrize("statement, parameters, expect_list_at", [
    (PASSAGE_WITH_A_LIST_PROPERTY, {}, "heading_path"),
    (EVIDENCE_EDGE_WITH_A_LIST_PROPERTY, {}, "block_ids"),
], ids=["passage-heading-path", "evidenced-by-block-ids"])
def test_a_returned_row_holds_only_plain_python_types_all_the_way_down(
        live_executor, statement, parameters, expect_list_at):
    """The test that matters most: what crosses is `dict`, `str`, `float`, `bool`, `list`.

    Both rows carry a real list property — `heading_path` on `:Passage` and `block_ids` on the
    `EVIDENCED_BY` edge — so the recursive check has something nested to walk rather than a
    flat row that would pass by accident.
    """
    rows = live_executor.read(statement, parameters, timeout_seconds=10.0)

    assert rows, "the loaded run holds no row with that list property"
    assert type(rows) is tuple
    for row in rows:
        assert type(row) is dict
        assert_plain(row)
    assert type(rows[0][expect_list_at]) is list
    assert rows[0][expect_list_at]
    assert all(type(item) is str for item in rows[0][expect_list_at])


# There is deliberately no live test that the server *fires* the timeout. Firing it requires a
# statement that outlives `db.transaction.monitor.check.interval` (`2s` on this instance,
# measured 2026-08-03), and the one that shipped here did not: a 1.4-second statement under a
# 1 ms ceiling completed 3 times in 10. What the ceiling is for — that it leaves this process as
# transaction metadata and is enforced by nobody here — is settled offline and deterministically
# by `test_every_statement_is_wrapped_so_the_timeout_is_applied_by_the_server`.
