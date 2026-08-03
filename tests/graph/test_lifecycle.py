"""Replacement policy, the wipe, and the one thing that says a load finished (§6.2, §6.5, §10).

Three kinds of test, split by what they need:

    unmarked   the policy, the statement builders and the unresolved-target guard — all pure,
               or driven with a driver that raises if it is touched at all
    @neo4j     the live 5.26.28 Community server: idempotent reload, the refusal, the wipe, and
               the two claims about `IN TRANSACTIONS` that only the server can settle

`pytest -m "not live and not neo4j"` is green with no container running.

**The live tests reuse `test_loader.py`'s synthetic export**, re-stamped with two lifecycle run
ids, rather than the 28,836-node real one: 13 nodes and 14 edges are enough to tell an
idempotent reload from a wipe, and the marked tests here are the only ones in the suite that
delete rows they did not write. Two guards keep that honest — the destructive tests skip if the
database holds a run they did not create, and every test deletes both lifecycle run ids
afterwards.

## The execution gap, stated rather than left to be inferred from a skip count

*(added 2026-08-03 after review, which measured it: **22 of the suite's 37 `neo4j` tests skip**
whenever the real export is loaded — 9 here and 13 in `test_graph_verification.py` — so
`15 passed` reads as a full run and is not one.)*

`require_disposable_database` refuses to wipe a graph this file did not load. That is correct
and stays. Its consequence is that **the two claims this file exists to make — a same-run reload
changes nothing, and `--replace` round-trips — are asserted only by tests that cannot run in the
machine's normal state**, which is the state where a real run is loaded. A green suite in that
state has not exercised them.

There is one way to close it, and it is deliberately not the default:

    FKG_GRAPH_TESTS_MAY_WIPE=1 pytest tests/graph -m neo4j

sets `MAY_WIPE` below, and the guard then wipes and reloads freely. **Every graph in the
database is destroyed by that run**, so it is an operator's decision, taken by typing it, and
the reload afterwards is theirs too:

    python -m graph load data/graph_runs/<graph_run_id> --replace
    python -m graph verify data/graph_runs/<graph_run_id>

The opt-in exists so the gap is closable on demand rather than permanently invisible; the
default is the refusing one for the same reason `load.replace_policy` is (§6.2). V1_GRAPH_
PROTOTYPE §6.5 carries the same note, so a reader of the plan is not left to discover it here.
"""

from __future__ import annotations

import dataclasses
import os

import pytest
from neo4j.exceptions import Neo4jError

from graph.core.models import GraphEdge, GraphNode
from graph.stages.load.connection import (
    GraphSettings,
    MissingGraphCredentialError,
    UnresolvedGraphTargetError,
    driver_for,
    load_settings,
)
from graph.stages.load.lifecycle import (
    LOAD_MARKER_LABEL,
    NODE_INVENTORY_STATEMENT,
    REMOVABLE_MARKER_PROPERTIES,
    RUN_ID_PROPERTY,
    STATUS_COMPLETE,
    STATUS_FAILED,
    DatabaseState,
    ExportRunIdentityError,
    GraphLifecycleError,
    GraphRunConflictError,
    GraphTarget,
    LoadMarker,
    check_export_run_identity,
    completed_run_id,
    decide_replacement,
    graph_node_count,
    inspect_database,
    load_graph_run,
    read_markers,
    resolve_target,
    wipe,
    wipe_batch_report_statement,
    wipe_statement,
    write_marker_statement,
)
from graph.stages.load.loader import ShortWriteError
from graph.stages.load.schema import CONSTRAINT_NAMES, apply_schema

# `tests/graph` is on `sys.path` while this package's tests are collected (pytest's default
# prepend import mode), so the loader's synthetic export is importable rather than duplicated.
from test_loader import SYNTHETIC_EDGES, SYNTHETIC_NODES

#: The operator opt-in that closes this file's execution gap — see the module docstring. Read
#: once at import so a test cannot enable it for itself: the only way to set it is from outside.
MAY_WIPE = os.environ.get("FKG_GRAPH_TESTS_MAY_WIPE", "") == "1"

#: Two runs, because half of §6.2's policy is about telling them apart.
RUN_A = "graph-test-lifecycle-a"
RUN_B = "graph-test-lifecycle-b"
LIFECYCLE_RUNS = (RUN_A, RUN_B)


def restamp(run_id: str) -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    """The loader's synthetic export, carrying `run_id` on every row.

    Re-stamped rather than re-invented: §5.5 puts `graph_run_id` on every node and edge, and
    the export a lifecycle test loads has to be one the loader would accept unchanged.
    """
    nodes = tuple(
        node.model_copy(update={"properties": {**node.properties, RUN_ID_PROPERTY: run_id}})
        for node in SYNTHETIC_NODES
    )
    edges = tuple(
        edge.model_copy(update={"properties": {**edge.properties, RUN_ID_PROPERTY: run_id}})
        for edge in SYNTHETIC_EDGES
    )
    return nodes, edges


NODES_A, EDGES_A = restamp(RUN_A)
NODES_B, EDGES_B = restamp(RUN_B)


def state(
    *,
    nodes: int = 0,
    relationships: int = 0,
    node_run_ids: tuple[str, ...] = (),
    relationship_run_ids: tuple[str, ...] = (),
    unidentified_nodes: int = 0,
    unidentified_relationships: int = 0,
    markers: tuple[LoadMarker, ...] = (),
) -> DatabaseState:
    """A read state, hand-built. The policy is pure, so its tests need no server."""
    return DatabaseState(
        target=GraphTarget(uri="bolt://localhost:7687", database="neo4j"),
        node_count=nodes,
        relationship_count=relationships,
        unidentified_nodes=unidentified_nodes,
        unidentified_relationships=unidentified_relationships,
        node_run_ids=node_run_ids,
        relationship_run_ids=relationship_run_ids,
        markers=markers,
    )


class RefusingDriver:
    """A driver that fails the test if anything at all is sent through it."""

    def session(self, **_):  # pragma: no cover - reaching this is the failure
        raise AssertionError("a statement was sent before the target was resolved")

    def execute_query(self, *_, **__):  # pragma: no cover - same
        raise AssertionError("a statement was sent before the target was resolved")


# -- the wipe statement, as text --------------------------------------------------------------


def test_the_match_stays_outside_the_subquery():
    """§6.2's first suspicion, as a shape assertion; the live test measures the consequence."""
    statement = wipe_statement()
    assert statement.startswith("MATCH (n) CALL (n) {")
    assert "IN TRANSACTIONS OF $batch_rows ROWS" in statement
    # The plan's own literal form, which 5.26.28 accepts but deprecates in favour of the scoped
    # one. Recorded here so a silent revert to it fails rather than merely warns.
    assert "CALL { WITH n" not in statement


def test_the_batch_size_travels_as_a_parameter_and_never_as_text():
    assert "$batch_rows" in wipe_statement()
    assert "10000" not in wipe_statement()


def test_the_wipe_is_not_wrapped_in_an_error_mode_that_would_swallow_a_failure():
    """`REPORT STATUS` forces `ON ERROR CONTINUE|BREAK`, under which a half-wipe returns rows
    instead of raising. The reporting form is a diagnostic and must stay out of `wipe`."""
    assert "ON ERROR" not in wipe_statement()
    assert "REPORT STATUS" not in wipe_statement()
    assert "ON ERROR BREAK REPORT STATUS" in wipe_batch_report_statement()


def test_every_count_excludes_the_loader_s_own_marker():
    assert f"NOT n:{LOAD_MARKER_LABEL}" in NODE_INVENTORY_STATEMENT


def test_a_marker_property_name_this_module_does_not_own_never_reaches_cypher():
    assert "REMOVE m.error" in write_marker_statement(("error",))
    assert "REMOVE" not in write_marker_statement(())
    with pytest.raises(GraphLifecycleError):
        write_marker_statement(("status",))
    with pytest.raises(GraphLifecycleError):
        write_marker_statement(("error, m.status //",))
    assert set(REMOVABLE_MARKER_PROPERTIES) == {
        "completed_at", "node_count", "edge_count", "error"
    }


# -- the export's claim about its own run id ----------------------------------------------------


def test_an_export_whose_rows_carry_the_run_id_is_accepted():
    assert check_export_run_identity(RUN_A, NODES_A, EDGES_A) is None


def test_a_row_carrying_no_run_id_is_refused_by_key():
    stripped = NODES_A[0].model_copy(
        update={"properties": {k: v for k, v in NODES_A[0].properties.items()
                               if k != RUN_ID_PROPERTY}}
    )
    with pytest.raises(ExportRunIdentityError) as refused:
        check_export_run_identity(RUN_A, (stripped,), ())
    assert stripped.key in str(refused.value)


def test_a_row_from_another_run_is_refused_naming_both_ids():
    with pytest.raises(ExportRunIdentityError) as refused:
        check_export_run_identity(RUN_A, NODES_B, EDGES_B)
    assert RUN_A in str(refused.value) and RUN_B in str(refused.value)


def test_an_edge_from_another_run_is_refused_too():
    with pytest.raises(ExportRunIdentityError) as refused:
        check_export_run_identity(RUN_A, NODES_A, EDGES_B)
    assert EDGES_B[0].edge_key in str(refused.value)


# -- §6.2's policy, decided with no server ------------------------------------------------------


def test_an_empty_database_is_loaded():
    decision = decide_replacement(state(), RUN_A, replace=False)
    assert decision.action == "load" and not decision.wipes


def test_the_same_run_reloads_without_replace():
    decision = decide_replacement(
        state(nodes=13, relationships=14, node_run_ids=(RUN_A,), relationship_run_ids=(RUN_A,)),
        RUN_A,
        replace=False,
    )
    assert decision.action == "reload" and not decision.wipes
    assert "idempotent" in decision.reason


def test_a_different_run_without_replace_is_refused_naming_both_ids():
    with pytest.raises(GraphRunConflictError) as refused:
        decide_replacement(
            state(nodes=13, node_run_ids=(RUN_B,), relationship_run_ids=(RUN_B,)),
            RUN_A,
            replace=False,
        )
    assert refused.value.requested == RUN_A
    assert refused.value.present == (RUN_B,)
    assert RUN_A in str(refused.value) and RUN_B in str(refused.value)


def test_a_run_named_only_by_an_unfinished_marker_still_refuses_a_different_run():
    """An interrupted load wrote a marker and no rows. The database is not empty in the sense
    that matters: something was aimed at it and did not finish."""
    with pytest.raises(GraphRunConflictError) as refused:
        decide_replacement(
            state(markers=(LoadMarker(graph_run_id=RUN_B, status="loading"),)),
            RUN_A,
            replace=False,
        )
    assert refused.value.present == (RUN_B,)


def test_rows_carrying_no_run_id_at_all_are_refused_rather_than_merged_into():
    with pytest.raises(GraphRunConflictError) as refused:
        decide_replacement(state(nodes=3, unidentified_nodes=3), RUN_A, replace=False)
    assert "carrying no graph_run_id" in str(refused.value)


def test_replace_is_the_only_thing_that_authorises_a_wipe():
    decision = decide_replacement(
        state(nodes=13, node_run_ids=(RUN_B,)), RUN_A, replace=True
    )
    assert decision.action == "replace" and decision.wipes
    assert decision.replace_requested_by == "flag"


def test_the_config_policy_cannot_authorise_a_wipe_and_says_so_in_the_refusal():
    """R3's first half. `load.replace_policy: replace` used to be the *first* branch of the
    policy, so one word in a tracked file made every load destructive — including a same-run
    reload MERGE would have made a no-op. It is now advisory: read, named, and ignored."""
    with pytest.raises(GraphRunConflictError) as refused:
        decide_replacement(
            state(nodes=13, node_run_ids=(RUN_B,)), RUN_A, replace=False, replace_policy="replace"
        )
    assert "not a wipe authority" in str(refused.value) and "--replace" in str(refused.value)

    # And a same-run reload stays a reload rather than becoming a wipe-and-rebuild.
    reload_decision = decide_replacement(
        state(nodes=13, node_run_ids=(RUN_A,)), RUN_A, replace=False, replace_policy="replace"
    )
    assert reload_decision.action == "reload" and not reload_decision.wipes

    with pytest.raises(GraphRunConflictError):
        decide_replacement(
            state(nodes=13, node_run_ids=(RUN_B,)), RUN_A, replace=False, replace_policy="refuse"
        )
    assert load_settings().replace_policy == "refuse"


def test_the_unidentified_rows_guard_is_not_bypassable_from_a_config_file():
    """R3's second half, and the sharper one: rows carrying no `graph_run_id` are the state the
    guard exists for, and the config branch used to sit *ahead* of it — so a YAML edit turned
    the one thing that must never be silently deleted into a wipe."""
    with pytest.raises(GraphRunConflictError) as refused:
        decide_replacement(
            state(nodes=3, unidentified_nodes=3), RUN_A, replace=False, replace_policy="replace"
        )
    assert "carrying no graph_run_id" in str(refused.value)
    assert "not a wipe authority" in str(refused.value)

    # The flag still gets through, because an operator typed it. That is the whole distinction.
    assert decide_replacement(
        state(nodes=3, unidentified_nodes=3), RUN_A, replace=True, replace_policy="refuse"
    ).wipes


def test_a_marker_is_only_complete_when_it_says_so():
    assert LoadMarker(graph_run_id=RUN_A, status=STATUS_COMPLETE).is_complete
    assert not LoadMarker(graph_run_id=RUN_A, status="loading").is_complete
    assert not LoadMarker(graph_run_id=RUN_A, status=STATUS_FAILED).is_complete
    mid_load = LoadMarker.from_properties({RUN_ID_PROPERTY: RUN_A, "status": "loading"})
    assert mid_load.node_count is None and mid_load.completed_at is None


# -- the unresolved target, checked before anything can be destroyed ---------------------------


def blank(field: str) -> GraphSettings:
    return dataclasses.replace(load_settings(), **{field: ""})


@pytest.mark.parametrize("field", ["configured_uri", "configured_database"])
def test_resolving_the_target_raises_rather_than_defaulting(field):
    with pytest.raises(UnresolvedGraphTargetError):
        resolve_target(blank(field))


@pytest.mark.parametrize("field", ["configured_uri", "configured_database"])
def test_the_wipe_refuses_an_unnamed_target_before_sending_anything(field):
    """The guard §6.2 depends on, driven with a driver that raises if it is used at all."""
    with pytest.raises(UnresolvedGraphTargetError):
        wipe(RefusingDriver(), blank(field))


@pytest.mark.parametrize("field", ["configured_uri", "configured_database"])
def test_a_load_refuses_an_unnamed_target_before_sending_anything(field):
    with pytest.raises(UnresolvedGraphTargetError):
        load_graph_run(RefusingDriver(), blank(field), RUN_A, NODES_A, EDGES_A, replace=True)


# -- the two verbs, parsed offline (§6.1) -------------------------------------------------------


def test_load_and_verify_are_real_verbs_with_the_flag_the_refusal_names():
    """`GraphRunConflictError` tells the operator to "re-run with --replace", and until this was
    written that flag existed nowhere in the repository. Parsing only — no driver is opened."""
    from graph.cli import build_parser, cmd_load, cmd_verify

    parser = build_parser()

    loaded = parser.parse_args(["load", "data/graph_runs/some-run"])
    assert loaded.handler is cmd_load
    assert loaded.export == "data/graph_runs/some-run"
    assert loaded.replace is False, "absence is never destructive (§6.2)"

    assert parser.parse_args(["load", "some-run", "--replace"]).replace is True

    verified = parser.parse_args(["verify", "some-run"])
    assert verified.handler is cmd_verify and verified.export == "some-run"

    # `--replace` belongs to `load` alone: a verify that could wipe would be a contradiction.
    with pytest.raises(SystemExit):
        parser.parse_args(["verify", "some-run", "--replace"])
    # …and the refusal the flag answers names it, so the two cannot drift apart.
    assert "--replace" in str(GraphRunConflictError(requested=RUN_A, present=(RUN_B,)))


def test_an_export_directory_with_no_manifest_is_refused_rather_than_loaded(tmp_path):
    """§6.5: a projection with no `manifest.json` did not finish. `load` must not read the run id
    off the directory name and put half a projection in the database."""
    from graph.pipeline import export_run_id, resolve_export

    unfinished = tmp_path / "graph-v1-unfinished"
    unfinished.mkdir()
    with pytest.raises(FileNotFoundError, match="did not finish"):
        export_run_id(unfinished)

    (unfinished / "manifest.json").write_text('{"graph_run_id": "graph-v1-abc"}', encoding="utf-8")
    assert export_run_id(unfinished) == "graph-v1-abc"
    # Both spellings resolve: a path a shell tab-completed, and a bare id under graph_runs_root.
    assert resolve_export(unfinished) == unfinished
    assert resolve_export("graph-v1-abc") == load_settings().graph_runs_root / "graph-v1-abc"


# -- a wipe that destroys a run must leave a trace of having done so (R5) ----------------------


class RecordingDriver:
    """Enough of a driver for `load_graph_run` to reach the wipe, and no further.

    Offline rather than marked: the claim is "a failure *between* the wipe and the load leaves a
    marker", and reproducing it on the live server would mean wiping a real graph to inject a
    failure into it. Every statement is recorded, so what the test asserts is what was sent.
    """

    def __init__(self, *, nodes: int = 13, run_ids: tuple[str, ...] = (RUN_B,)) -> None:
        self.nodes = nodes
        self.run_ids = run_ids
        self.markers: list[dict] = []
        self.statements: list[str] = []
        self.wiped = False

    # `settings.query()` wraps text in a `neo4j.Query`; both spellings arrive here.
    @staticmethod
    def _text(query) -> str:
        return getattr(query, "text", query)

    def execute_query(self, query, parameters=None, **_):
        text = self._text(query)
        self.statements.append(text)
        parameters = parameters or {}
        if "MERGE (m:GraphLoad" in text:
            self.markers = [
                m for m in self.markers if m["graph_run_id"] != parameters["run_id"]
            ]
            self.markers.append({"graph_run_id": parameters["run_id"], **parameters["properties"]})
            return ([{"p": self.markers[-1]}], None, None)
        if text.startswith("MATCH (m:GraphLoad)"):
            return ([{"p": marker} for marker in self.markers], None, None)
        if "db.awaitIndexes" in text:
            return ([], None, None)
        if "MATCH ()-[r]->()" in text:
            return ([{"total": 0, "identified": 0, "run_ids": []}], None, None)
        if "count(n)" in text:
            return (
                [{"total": self.nodes, "identified": self.nodes, "run_ids": list(self.run_ids)}],
                None,
                None,
            )
        raise AssertionError(f"unexpected statement: {text}")  # pragma: no cover

    def session(self, **_):
        driver = self

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def run(self, query, parameters=None, **__):
                driver.statements.append(driver._text(query))
                driver.wiped = True
                driver.nodes = 0
                driver.run_ids = ()
                driver.markers = []  # `MATCH (n)` takes the marker with everything else

                class Summary:
                    counters = type("C", (), {"nodes_deleted": 13, "relationships_deleted": 14})()

                return type("Result", (), {"consume": lambda _self: Summary()})()

        return Session()


def test_a_failure_between_the_wipe_and_the_load_leaves_a_failed_marker(monkeypatch):
    """R5. The wipe deletes the old marker first, so the three steps after it — schema, index
    wait, `begin_load` — used to run *outside* the `try` and could not reach `fail_load`.

    A failure there left an empty database with no marker at all, which `decide_replacement`
    reads as "the database holds no run": indistinguishable from a fresh install, with the run
    it destroyed leaving no trace anywhere.
    """
    from graph.stages.load import lifecycle

    def boom(*_, **__):
        raise RuntimeError("index population died")

    monkeypatch.setattr(lifecycle, "apply_schema", boom)
    driver = RecordingDriver()

    with pytest.raises(RuntimeError, match="index population died"):
        load_graph_run(driver, load_settings(), RUN_A, NODES_A, EDGES_A, replace=True)

    assert driver.wiped, "the wipe ran, so a previous run was destroyed"
    assert [m["status"] for m in driver.markers] == [STATUS_FAILED]
    assert driver.markers[0]["graph_run_id"] == RUN_A
    assert "index population died" in driver.markers[0]["error"]
    # The trace is what makes the state readable: an empty database *with* a marker is a load
    # that failed, not an installation nobody has used.
    marker = LoadMarker.from_properties(driver.markers[0])
    assert not marker.is_complete
    with pytest.raises(GraphRunConflictError):
        decide_replacement(state(markers=(marker,)), RUN_B, replace=False)


@pytest.mark.parametrize("failing_step", ["await_indexes", "begin_load"])
def test_every_step_after_the_wipe_is_inside_the_try(monkeypatch, failing_step):
    """The other two steps that used to sit above the `try`, each in turn."""
    from graph.stages.load import lifecycle

    def boom(*_, **__):
        raise RuntimeError(f"{failing_step} died")

    monkeypatch.setattr(lifecycle, failing_step, boom)
    monkeypatch.setattr(lifecycle, "apply_schema", lambda *_, **__: None)
    driver = RecordingDriver()

    with pytest.raises(RuntimeError):
        load_graph_run(driver, load_settings(), RUN_A, NODES_A, EDGES_A, replace=True)

    assert [m["status"] for m in driver.markers] == [STATUS_FAILED]


# -- against the live server --------------------------------------------------------------------


def live_settings() -> GraphSettings:
    settings = load_settings()
    try:
        settings.resolved_uri, settings.resolved_database, settings.auth
    except (UnresolvedGraphTargetError, MissingGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    return settings


def delete_lifecycle_rows(driver, settings: GraphSettings) -> None:
    """Delete exactly what this file creates — data rows and markers alike, both carry the id."""
    driver.execute_query(
        f"MATCH (n) WHERE n.{RUN_ID_PROPERTY} IN $runs DETACH DELETE n",
        {"runs": list(LIFECYCLE_RUNS)},
        database_=settings.resolved_database,
    )


@pytest.fixture()
def live_graph():
    settings = live_settings()
    try:
        driver = driver_for(settings)
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip, not a failure
        pytest.skip(
            f"local Neo4j is unreachable ({type(exc).__name__}); start it with "
            "`docker compose up -d`"
        )
    with driver:
        apply_schema(driver, settings)
        delete_lifecycle_rows(driver, settings)
        try:
            yield driver, settings
        finally:
            delete_lifecycle_rows(driver, settings)


def require_disposable_database(driver, settings) -> None:
    """Skip rather than wipe a graph this file did not load, unless an operator opted in.

    The one rule the destructive tests must not break: `MATCH (n) DETACH DELETE n` does not know
    whose rows it is deleting, so the test has to. The skip is the *correct* behaviour and the
    reason 18 of 31 marked tests do not run beside a real graph — see the module docstring on
    `FKG_GRAPH_TESTS_MAY_WIPE`, which is how that gap is closed on purpose.
    """
    if MAY_WIPE:
        return
    current = inspect_database(driver, settings)
    foreign = [run for run in current.run_ids if run not in LIFECYCLE_RUNS]
    if foreign or current.unidentified_rows:
        pytest.skip(
            f"the database holds {foreign or current.unidentified_rows} that this test did not "
            "load; refusing to wipe it. Set FKG_GRAPH_TESTS_MAY_WIPE=1 to run these anyway — it "
            "destroys every graph in the database and you reload it yourself afterwards"
        )


def seed(driver, settings, run_id, nodes, edges, *, replace=False):
    return load_graph_run(
        driver, settings, run_id, nodes, edges, replace=replace, wipe_batch_rows=5
    )


@pytest.mark.neo4j
def test_a_load_into_an_empty_database_completes_and_says_which_run_it_holds(live_graph):
    driver, settings = live_graph
    require_disposable_database(driver, settings)

    outcome = seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)

    assert outcome.decision.action == "replace"  # `replace=True` was asked for
    assert outcome.complete and outcome.marker.status == STATUS_COMPLETE
    assert outcome.verification.node_count == len(NODES_A)
    assert outcome.verification.relationship_count == len(EDGES_A)
    assert outcome.marker.node_count == len(NODES_A)
    assert completed_run_id(driver, settings) == RUN_A
    assert inspect_database(driver, settings).run_ids == (RUN_A,)
    # The marker is the only loader-owned addition, and it is not a projected node.
    assert graph_node_count(driver, settings) == len(NODES_A)
    assert len(read_markers(driver, settings)) == 1


@pytest.mark.neo4j
def test_the_same_run_reloaded_without_replace_changes_nothing(live_graph):
    """§6.5's idempotence check, through the lifecycle rather than through the loader."""
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)
    before = inspect_database(driver, settings)

    again = seed(driver, settings, RUN_A, NODES_A, EDGES_A)

    assert again.decision.action == "reload" and again.wipe is None
    assert again.complete
    after = inspect_database(driver, settings)
    assert after.node_count == before.node_count == len(NODES_A)
    assert after.relationship_count == before.relationship_count == len(EDGES_A)
    assert after.run_ids == (RUN_A,)
    assert len(read_markers(driver, settings)) == 1, "a reload must not mint a second marker"


@pytest.mark.neo4j
def test_a_different_run_without_replace_is_refused_and_writes_nothing(live_graph):
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)

    with pytest.raises(GraphRunConflictError) as refused:
        seed(driver, settings, RUN_B, NODES_B, EDGES_B)

    assert RUN_A in str(refused.value) and RUN_B in str(refused.value)
    assert refused.value.requested == RUN_B and refused.value.present == (RUN_A,)
    after = inspect_database(driver, settings)
    assert after.run_ids == (RUN_A,), "the refused run must not have landed"
    assert after.node_count == len(NODES_A)
    assert completed_run_id(driver, settings) == RUN_A


@pytest.mark.neo4j
def test_replace_wipes_the_previous_run_and_reloads_to_the_same_counts(live_graph):
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    first = seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)

    second = seed(driver, settings, RUN_B, NODES_B, EDGES_B, replace=True)

    assert second.wipe is not None
    assert second.wipe.nodes_deleted == len(NODES_A) + 1, "the previous run and its marker"
    assert second.wipe.relationships_deleted == len(EDGES_A)
    assert second.wipe.target.database == settings.resolved_database
    after = inspect_database(driver, settings)
    assert after.run_ids == (RUN_B,)
    assert after.node_count == first.verification.node_count == len(NODES_B)
    assert after.relationship_count == first.verification.relationship_count == len(EDGES_B)
    assert completed_run_id(driver, settings) == RUN_B
    # Schema is data-independent: a wipe deletes rows, never §5.2's nineteen constraints.
    assert {row[0] for row in second.schema.managed_constraints()} == set(CONSTRAINT_NAMES)


@pytest.mark.neo4j
@pytest.mark.parametrize("field", ["configured_uri", "configured_database"])
def test_an_unresolved_target_raises_before_a_live_wipe_deletes_anything(live_graph, field):
    """The same guard as the offline test, measured: the rows are still there afterwards."""
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)
    before = inspect_database(driver, settings)

    with pytest.raises(UnresolvedGraphTargetError):
        wipe(driver, dataclasses.replace(settings, **{field: ""}), batch_rows=5)
    with pytest.raises(UnresolvedGraphTargetError):
        load_graph_run(
            driver,
            dataclasses.replace(settings, **{field: ""}),
            RUN_B,
            NODES_B,
            EDGES_B,
            replace=True,
        )

    after = inspect_database(driver, settings)
    assert after.node_count == before.node_count == len(NODES_A)
    assert after.relationship_count == before.relationship_count == len(EDGES_A)
    assert completed_run_id(driver, settings) == RUN_A


@pytest.mark.neo4j
def test_a_failed_load_never_reads_as_complete(live_graph):
    """A dangling edge aborts the load after the nodes have landed. The rows are there; the
    completion metadata is not, which is the only difference that may be trusted."""
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    dangling = GraphEdge(
        edge_key="test:lifecycle:edge:dangling",
        type="EVIDENCED_BY",
        source_key=NODES_A[6].key,
        source_base_label="Observation",
        target_key="test:lifecycle:passage:never-loaded",
        target_base_label="Passage",
        properties={RUN_ID_PROPERTY: RUN_A},
    )

    with pytest.raises(ShortWriteError):
        seed(driver, settings, RUN_A, NODES_A, EDGES_A + (dangling,), replace=True)

    assert completed_run_id(driver, settings) is None
    markers = read_markers(driver, settings)
    assert [marker.status for marker in markers] == [STATUS_FAILED]
    assert not markers[0].is_complete and markers[0].completed_at is None
    assert markers[0].node_count is None, "a load that failed counted nothing"
    assert "ShortWriteError" in (markers[0].error or "")
    # Batched loading is not one transaction: the nodes are committed and some edges are too.
    # That is exactly why the marker, not the row count, is what "complete" means.
    assert graph_node_count(driver, settings) == len(NODES_A)


@pytest.mark.neo4j
def test_the_wipe_batches_and_the_plan_s_inner_match_form_does_not(live_graph):
    """§6.2's first suspicion, measured on this server rather than argued.

    `REPORT STATUS` yields one row per deleted node carrying the transaction that deleted it, so
    `count(DISTINCT status.transactionId)` is the number of transactions the wipe actually used.
    """
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)
    rows_in_database = graph_node_count(driver, settings) + 1  # + the marker

    with driver.session(database=settings.resolved_database) as session:
        record = session.run(
            settings.query(wipe_batch_report_statement()), {"batch_rows": 5}
        ).single()
    assert record["rows"] == rows_in_database
    assert record["transactions"] == -(-rows_in_database // 5) > 1
    assert graph_node_count(driver, settings) == 0

    # The plan's alternative, with the `MATCH` *inside* the subquery. Written out here rather
    # than exported, because a statement that deletes an entire database in one transaction is
    # not something this package should offer: with no driving row the subquery gets a single
    # input row and runs once.
    seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)
    with driver.session(database=settings.resolved_database) as session:
        record = session.run(
            "CALL () { MATCH (n) DETACH DELETE n } IN TRANSACTIONS OF 5 ROWS "
            "ON ERROR BREAK REPORT STATUS AS status "
            "RETURN count(DISTINCT status.transactionId) AS transactions, count(status) AS rows"
        ).single()
    assert record["transactions"] == 1 and record["rows"] == 1
    assert graph_node_count(driver, settings) == 0


@pytest.mark.neo4j
def test_in_transactions_is_refused_in_a_managed_transaction(live_graph):
    """§6.2's second suspicion, with the server's own words.

    Both managed paths fail identically — `driver.execute_query` runs a managed transaction too
    — which is why `wipe` uses `session.run`. Asserted rather than commented because the day
    someone "tidies" the wipe into `execute_query` it must fail here, not in production.

    **The rows are loaded first, and that is not incidental.** The first draft of this test ran
    against an empty database and failed with `DID NOT RAISE`: with no rows, `MATCH (n)` drives
    nothing, the subquery never runs, and no inner transaction is ever started, so the refusal
    never arrives. The restriction is enforced at the first batch, not at planning time.
    """
    driver, settings = live_graph
    require_disposable_database(driver, settings)
    seed(driver, settings, RUN_A, NODES_A, EDGES_A, replace=True)
    before = graph_node_count(driver, settings)

    with pytest.raises(Neo4jError) as refused:
        driver.execute_query(
            wipe_statement(), {"batch_rows": 5}, database_=settings.resolved_database
        )
    assert refused.value.code == "Neo.DatabaseError.Transaction.TransactionStartFailed"
    assert "can only be executed in an implicit transaction" in str(refused.value)

    with driver.session(database=settings.resolved_database) as session:
        with pytest.raises(Neo4jError) as refused:
            session.execute_write(
                lambda tx: tx.run(wipe_statement(), {"batch_rows": 5}).consume()
            )
    assert refused.value.code == "Neo.DatabaseError.Transaction.TransactionStartFailed"
    assert graph_node_count(driver, settings) == before, "a refused wipe deletes nothing"
