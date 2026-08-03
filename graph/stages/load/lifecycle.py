"""Which run the database holds, whether the load that put it there finished, and what a
replacement is allowed to destroy (V1_GRAPH_PROTOTYPE §6.2, §6.5, §10).

`loader.py` writes rows. This module decides whether it may, wipes when it may not merge, and
is the only place that says a load is *complete*.

## Run identity: the rows, not a badge

**The graph's identity is read from the data itself.** Every node and every edge already
carries `graph_run_id` in its §5.5 provenance block *(verified 2026-08-03 over
`data/graph_runs/graph-v1-886059d862ce/`: 28,836 nodes and 35,603 edges, 0 missing, exactly one
distinct value)*, so `inspect_database` answers "which run is in here" by reading the rows
rather than by trusting a marker. That ordering matters: a badge can survive a wipe it did not
observe, or be absent from a graph some other path loaded — `tests/graph/test_loader.py` loads
rows through `loader.load_export` with no lifecycle involved at all — and a policy that
believed a badge over the rows would then merge two runs into one graph and call it clean.

## The one loader-owned addition, and why there has to be one

Identity is not completion. "Which run" and "did the load finish" are different questions, and
the rows cannot answer the second: a load that died after 20,000 of 28,836 nodes leaves rows
that look exactly like the rows of a load that finished. So this module adds **one** node —
`(:GraphLoad {graph_run_id, status, …})`, §10's "explicitly documented loader-owned completion
metadata", and the only thing in the database that no projection emitted. It is:

    written after the wipe, never before   the wipe is `MATCH (n)`; a marker written first is
                                           deleted by the very statement it was recording
    `status: "loading"` before any row     so an interrupted load reads as mid-load, not as
                                           absent-and-therefore-maybe-fine
    `status: "failed"` if anything after   the wipe deletes the previous marker, so every step
    the decision raises                    from the wipe onwards runs inside `load_graph_run`'s
                                           `try`. A run destroyed by a load that then fell over
                                           leaves a `failed` marker rather than an empty
                                           database that reads as a fresh install
    `status: "complete"` only after nodes, edges **and** `verify_load` have all succeeded
    excluded from every count this module takes (`NOT n:GraphLoad`), so it can never be
                                           mistaken for a projected node

Callers that count the graph must exclude it too; `graph_node_count` is here for that.

## The wipe statement, verified rather than assumed

§6.2 proposed `MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS` and
flagged two suspicions. Both are **confirmed** against the live server *(Neo4j 5.26.28
Community, driver 6.2.0, 2026-08-03; probe: 25 `:Probe` nodes, `IN TRANSACTIONS OF 5 ROWS`,
`REPORT STATUS` counting `DISTINCT status.transactionId`)*:

    form                                              transactions used over 25 nodes
    `MATCH (n) CALL (n) { DETACH DELETE n } IN …`      5   — batched, one per 5 rows
    `CALL () { MATCH (n) DETACH DELETE n } IN …`       1   — the whole database in one
                                                          transaction, which is the heap
                                                          exhaustion the clause exists to avoid

So the `MATCH` must stay outside, as §6.2 said. And the transaction-mode restriction is real:
both managed-transaction paths fail, verbatim, with

    Neo.DatabaseError.Transaction.TransactionStartFailed: A query with
    'CALL { ... } IN TRANSACTIONS' can only be executed in an implicit transaction, but tried
    to execute in an explicit transaction.

— raised identically by `session.execute_write(lambda tx: tx.run(...))` and by
`driver.execute_query(...)`, because `execute_query` is a managed transaction too. `session.run`
(auto-commit) is therefore the only path, and it is what `wipe` uses.

**That refusal is a runtime error, not a planning one** *(measured 2026-08-03, and it cost this
module a test that passed for the wrong reason)*: against an **empty** database the same
`driver.execute_query(wipe_statement())` **succeeds**, because `MATCH (n)` yields no rows, the
subquery never runs and no inner transaction is ever started. So a managed-transaction wipe
cannot be ruled out by trying it once on a clean server — the failure only appears the first
time there is something to delete, which is the worst moment to discover it.

**Two corrections to the plan's text, from the same probe.** (1) The plan's literal
`CALL { WITH n … }` still runs on 5.26.28 but is deprecated — the server returns
`01N00 … CALL subquery without a variable scope clause is deprecated. Use CALL (n) { ... }` —
so the scoped form the plan mentions as merely "also accepted" is the one used here. (2) The
batch size travels as a **parameter**: `IN TRANSACTIONS OF $batch_rows ROWS` is accepted, which
keeps the last number that could have been formatted into this statement out of its text.

`ON ERROR FAIL` — the default — is left alone. `REPORT STATUS` is only legal with `ON ERROR
CONTINUE` or `ON ERROR BREAK` (`Neo.ClientError.Statement.SyntaxError: REPORT STATUS can only
be used when specifying ON ERROR CONTINUE or ON ERROR BREAK`), and under `BREAK` a wipe that
failed halfway returns rows instead of raising. A silent half-wipe is worse than a loud one, so
the reporting form exists here as a diagnostic only and `wipe` does not use it.

## Transaction boundaries, stated because they are weaker than they look

    schema        one implicit transaction per `CREATE CONSTRAINT`/`CREATE INDEX`; DDL cannot
                  be rolled back and is not undone by a later failure
    wipe          N transactions of `batch_rows`. **Not atomic.** An interrupted wipe leaves a
                  partly deleted graph and no marker, which reads as "holds the old run,
                  unfinished" — refused without `--replace`, re-wiped with it
    nodes/edges   one transaction per batch (`loader._session_batches`). **Not atomic.** A
                  failing batch rolls itself back; earlier batches stay committed
    completion    one transaction, one statement, written last

Nothing here makes a load atomic, and nothing pretends to. The only guarantee is the ordering:
`status: "complete"` is written after everything else returned success, so *complete implies
finished*, while the converse — data present, marker missing or `loading` — is exactly the
"unambiguously incomplete" state the repository's atomic-finalization rule asks for.

Concurrency is out of scope and unguarded: `:GraphLoad` carries no uniqueness constraint,
because `schema.py` owns §5.2's nineteen constraints and adding a twentieth from here would put
a schema object outside the module that declares them. One operator, one load at a time (§5.1's
single Community database).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import Any, Iterable, Mapping, Sequence

from neo4j import Driver

from graph.core.manifest import utc_now_iso
from graph.core.models import GraphEdge, GraphNode

from .connection import GraphSettings
from .loader import LoadResult, load_export
from .schema import SchemaState, apply_schema, await_indexes

#: The provenance key §5.5 puts on every node and every edge, and therefore the property this
#: module reads identity from. Not a constant this module invents — it is the projection's.
RUN_ID_PROPERTY = "graph_run_id"

#: The one label no projection emits. Every count this module takes excludes it explicitly.
LOAD_MARKER_LABEL = "GraphLoad"

STATUS_LOADING = "loading"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

#: §6.2's number. Here rather than in `config/graph.yaml` because the file's `load.*` sizes are
#: about *writing* — an edge batch carries two endpoint `MATCH`es, a delete batch carries
#: neither — and inventing a third configured size before a real wipe has been timed would be
#: guessing. Overridable per call, which is what the batching test uses.
WIPE_BATCH_ROWS = 10_000


class GraphLifecycleError(RuntimeError):
    """Anything that stops a load from being started, finished, or trusted."""


class GraphRunConflictError(GraphLifecycleError):
    """The database holds a different run, and no replacement was asked for (§6.2).

    Carries both sides as fields, not only in the message: a caller printing a refusal should
    not have to parse the sentence to say which id it found.
    """

    def __init__(self, *, requested: str, present: Sequence[str], detail: str = "") -> None:
        self.requested = requested
        self.present = tuple(present)
        named = ", ".join(repr(run_id) for run_id in self.present) or "<none>"
        super().__init__(
            f"the database already holds {named} and the requested run is {requested!r}"
            f"{'; ' + detail if detail else ''}. Refusing to merge two runs into one graph "
            "(§6.2). Re-run with --replace to wipe and load, or point NEO4J_DATABASE at "
            "another database."
        )


class ExportRunIdentityError(GraphLifecycleError):
    """A row does not carry the `graph_run_id` the load claims to be loading.

    Checked before anything is written, because the whole replacement policy is read off this
    property: rows that disagree with the run id would load into a graph whose identity is a
    different question depending on which node you ask.
    """


class LoadVerificationError(GraphLifecycleError):
    """What the database holds after the load is not what the export submitted.

    Raised before the completion marker is written, which is what keeps a failed load from ever
    reading as complete.
    """


# -- the target, whose *resolution* is the guard on everything destructive ----------------------


@dataclass(frozen=True)
class GraphTarget:
    """The two names a destructive statement needs, both positively resolved.

    Constructing one is the guard, not a check performed beside it: `resolve_target` reads
    `resolved_uri` and `resolved_database`, each of which raises `UnresolvedGraphTargetError`
    on a missing or blank value (`connection.py`'s module docstring says why). Everything below
    that deletes or writes takes a `GraphTarget` first, so "we never wiped an unnamed database"
    is a property of the call graph rather than of a comment.
    """

    uri: str
    database: str


def resolve_target(settings: GraphSettings) -> GraphTarget:
    return GraphTarget(uri=settings.resolved_uri, database=settings.resolved_database)


# -- statements ---------------------------------------------------------------------------------


def wipe_statement() -> str:
    """§6.2's wipe, in the form that actually batches on 5.26.28 — see the module docstring."""
    return "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF $batch_rows ROWS"


def wipe_batch_report_statement() -> str:
    """The same wipe, reporting one row per deleted node with the transaction that deleted it.

    **Diagnostic only.** `REPORT STATUS` forces `ON ERROR CONTINUE|BREAK`, under which a wipe
    that failed part-way returns rows rather than raising — which is why `wipe` runs the plain
    form and this one exists to prove, on a live server, that the batching claim is true.
    """
    return (
        f"{wipe_statement()}\n"
        "ON ERROR BREAK REPORT STATUS AS status\n"
        "RETURN count(DISTINCT status.transactionId) AS transactions, count(status) AS rows"
    )


#: Every count this module takes, in one place, all of them excluding the loader's own marker.
_DATA_NODES = f"MATCH (n) WHERE NOT n:{LOAD_MARKER_LABEL}"

NODE_INVENTORY_STATEMENT = (
    f"{_DATA_NODES} RETURN count(n) AS total, count(n.{RUN_ID_PROPERTY}) AS identified, "
    f"collect(DISTINCT n.{RUN_ID_PROPERTY}) AS run_ids"
)

#: No `NOT`-clause on the relationship side, and it is not an oversight: the marker is an
#: isolated node — nothing in this module ever attaches an edge to it — so every relationship in
#: the database came from a projection.
EDGE_INVENTORY_STATEMENT = (
    f"MATCH ()-[r]->() RETURN count(r) AS total, count(r.{RUN_ID_PROPERTY}) AS identified, "
    f"collect(DISTINCT r.{RUN_ID_PROPERTY}) AS run_ids"
)

MARKER_STATEMENT = f"MATCH (m:{LOAD_MARKER_LABEL}) RETURN properties(m) AS p"

#: The marker's optional fields. A `REMOVE` list rather than a null or a `-1`: Neo4j stores no
#: null, and a `node_count: 0` on a load that has not counted anything yet is a measurement
#: nobody took. Fixed and allowlisted because a property name cannot be a Cypher parameter.
REMOVABLE_MARKER_PROPERTIES = ("completed_at", "node_count", "edge_count", "error")


def write_marker_statement(remove: Sequence[str] = ()) -> str:
    for name in remove:
        if name not in REMOVABLE_MARKER_PROPERTIES:
            raise GraphLifecycleError(
                f"{name!r} is not a removable marker property {REMOVABLE_MARKER_PROPERTIES!r}; "
                "a property name is written into Cypher literally"
            )
    clause = f"REMOVE {', '.join(f'm.{name}' for name in remove)} " if remove else ""
    return (
        f"MERGE (m:{LOAD_MARKER_LABEL} {{{RUN_ID_PROPERTY}: $run_id}}) "
        f"SET m += $properties {clause}"
        "RETURN properties(m) AS p"
    )


# -- what the database currently holds ------------------------------------------------------------


@dataclass(frozen=True)
class LoadMarker:
    """One `(:GraphLoad)` node, read back.

    `completed_at`, `node_count`, `edge_count` and `error` are `None` until the load that would
    set them has happened — absent rather than zero, because Neo4j stores no null property and
    a `0` would be a measurement nobody took.
    """

    graph_run_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    schema_version: str | None = None
    wiped: bool | None = None
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == STATUS_COMPLETE

    @classmethod
    def from_properties(cls, properties: Mapping[str, Any]) -> "LoadMarker":
        return cls(
            graph_run_id=str(properties.get(RUN_ID_PROPERTY, "")),
            status=str(properties.get("status", "")),
            started_at=properties.get("started_at"),
            completed_at=properties.get("completed_at"),
            node_count=properties.get("node_count"),
            edge_count=properties.get("edge_count"),
            schema_version=properties.get("schema_version"),
            wiped=properties.get("wiped"),
            error=properties.get("error"),
        )


@dataclass(frozen=True)
class DatabaseState:
    """Everything the replacement decision is made from, read in three statements.

    `run_ids` is the union over nodes and relationships, so a graph whose edges name a run its
    nodes do not is visible as the two-run conflict it is.
    """

    target: GraphTarget
    node_count: int
    relationship_count: int
    unidentified_nodes: int
    unidentified_relationships: int
    node_run_ids: tuple[str, ...]
    relationship_run_ids: tuple[str, ...]
    markers: tuple[LoadMarker, ...]

    @property
    def run_ids(self) -> tuple[str, ...]:
        """Every run id present, from the rows *and* from any marker, sorted.

        A marker for a run with no rows is a load that was interrupted before it wrote one;
        including it here is what makes that state refuse a *different* run rather than look
        like an empty database.
        """
        found = set(self.node_run_ids) | set(self.relationship_run_ids)
        found.update(marker.graph_run_id for marker in self.markers if marker.graph_run_id)
        return tuple(sorted(found))

    @property
    def unidentified_rows(self) -> int:
        return self.unidentified_nodes + self.unidentified_relationships

    def marker_for(self, graph_run_id: str) -> LoadMarker | None:
        for marker in self.markers:
            if marker.graph_run_id == graph_run_id:
                return marker
        return None


def _single(driver: Driver, settings: GraphSettings, target: GraphTarget, statement: str):
    rows, _, _ = driver.execute_query(settings.query(statement), database_=target.database)
    return rows[0]


def read_markers(driver: Driver, settings: GraphSettings) -> tuple[LoadMarker, ...]:
    target = resolve_target(settings)
    rows, _, _ = driver.execute_query(
        settings.query(MARKER_STATEMENT), database_=target.database
    )
    markers = [LoadMarker.from_properties(row["p"]) for row in rows]
    return tuple(sorted(markers, key=lambda marker: marker.graph_run_id))


def inspect_database(driver: Driver, settings: GraphSettings) -> DatabaseState:
    """What is in the database right now — the read §6.2 requires *before* a load decides.

    Two full scans. Deliberately not indexed and deliberately not narrowed: `graph_run_id`
    carries no index (§5.3's list is the plan's, and this module may not add to it), the scans
    cost ~28,836 + 35,603 rows once per load, and a query that read only the labels it expected
    could not notice a row from a run nobody remembers loading.
    """
    target = resolve_target(settings)
    nodes = _single(driver, settings, target, NODE_INVENTORY_STATEMENT)
    edges = _single(driver, settings, target, EDGE_INVENTORY_STATEMENT)
    return DatabaseState(
        target=target,
        node_count=int(nodes["total"]),
        relationship_count=int(edges["total"]),
        unidentified_nodes=int(nodes["total"]) - int(nodes["identified"]),
        unidentified_relationships=int(edges["total"]) - int(edges["identified"]),
        node_run_ids=tuple(sorted(str(value) for value in nodes["run_ids"])),
        relationship_run_ids=tuple(sorted(str(value) for value in edges["run_ids"])),
        markers=read_markers(driver, settings),
    )


def completed_run_id(driver: Driver, settings: GraphSettings) -> str | None:
    """The run the database holds a *finished* load of, or `None`.

    `None` covers four different situations on purpose — nothing loaded, a load in flight, a
    load that failed, and *two* completed markers — because none of them may be read as "the
    graph is ready". The last cannot arise through this module (a replacement wipes the marker
    it replaces) and would mean two runs share the database, which is the one thing §6.2 exists
    to prevent; naming either of them would be picking a winner.
    """
    completed = [marker for marker in read_markers(driver, settings) if marker.is_complete]
    return completed[0].graph_run_id if len(completed) == 1 else None


def graph_node_count(driver: Driver, settings: GraphSettings) -> int:
    """Projected nodes, excluding the loader's marker. What §10's counts should be taken with."""
    target = resolve_target(settings)
    statement = f"{_DATA_NODES} RETURN count(n) AS total"
    return int(_single(driver, settings, target, statement)["total"])


# -- the export's own claim about which run it is -------------------------------------------------


def check_export_run_identity(
    graph_run_id: str, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]
) -> None:
    """Every row carries `graph_run_id`, and it is the one being loaded.

    Cheap, pure, and run before the first statement: the replacement policy, the post-load
    verification and every future "which run said this" query all read this property, so a load
    whose rows disagree with its own run id would poison all three at once.
    """
    rows = chain(
        (("node", node.key, node.properties) for node in nodes),
        (("edge", edge.edge_key, edge.properties) for edge in edges),
    )
    for kind, key, properties in rows:
        found = properties.get(RUN_ID_PROPERTY)
        if found is None:
            raise ExportRunIdentityError(
                f"{kind} {key!r} carries no {RUN_ID_PROPERTY}; §5.5 puts it on every row and "
                "the graph's identity is read from it"
            )
        if found != graph_run_id:
            raise ExportRunIdentityError(
                f"{kind} {key!r} carries {RUN_ID_PROPERTY}={found!r}, but this load is "
                f"{graph_run_id!r}"
            )


# -- the replacement decision (pure) ------------------------------------------------------------


@dataclass(frozen=True)
class ReplacementDecision:
    """What a load is about to do, and on whose authority — a value, so it can be tested and
    printed without a database."""

    graph_run_id: str
    #: `load` (nothing there), `reload` (same run, MERGE makes it a no-op), `replace` (wipe first).
    action: str
    present_run_ids: tuple[str, ...]
    #: `flag` when the action is `replace`; empty otherwise. There is no second value: a config
    #: file cannot authorise a wipe (see `decide_replacement`).
    replace_requested_by: str
    reason: str

    @property
    def wipes(self) -> bool:
        return self.action == "replace"


def decide_replacement(
    state: DatabaseState, graph_run_id: str, *, replace: bool, replace_policy: str = "refuse"
) -> ReplacementDecision:
    """§6.2's policy, exactly, and nowhere else in this module.

    Pure: it takes a read state and returns a decision, so the four branches are testable with
    no server.

    **A tracked file is not a wipe authority** *(corrected 2026-08-03 after review)*. This
    function used to read `if replace or replace_policy == "replace":` as its first branch, which
    made a one-word edit to `config/graph.yaml` outrank every guard below it: every load became
    destructive, including a same-run reload that `MERGE` would have made a no-op, and including
    a database holding rows with no `graph_run_id` — which is the exact state the guard three
    lines down exists to refuse. `config/graph.yaml`'s own comment said the opposite
    ("`--replace` on the command line is the only way to wipe"), and the safe reading is the one
    that now holds:

        `replace=True`                   the only thing that wipes. An operator typed it.
        `load.replace_policy: replace`   **never** wipes. It is read, reported and named in the
                                         refusal so the operator can see it was ignored, and it
                                         cannot reach past the unidentified-rows guard.

    `replace_requested_by` is therefore only ever `"flag"`.
    """
    present = state.run_ids
    if replace:
        return ReplacementDecision(
            graph_run_id=graph_run_id,
            action="replace",
            present_run_ids=present,
            replace_requested_by="flag",
            reason="replacement was requested explicitly",
        )
    # Below this line nothing can wipe, so the guards run in the order that refuses the most.
    policy_note = (
        "; config/graph.yaml sets load.replace_policy: replace, which is not a wipe "
        "authority — pass --replace"
        if replace_policy == "replace"
        else ""
    )
    if state.unidentified_rows:
        raise GraphRunConflictError(
            requested=graph_run_id,
            present=present,
            detail=(
                f"and {state.unidentified_rows} row(s) carrying no {RUN_ID_PROPERTY} at all, "
                f"which no projection emits{policy_note}"
            ),
        )
    if not present:
        return ReplacementDecision(
            graph_run_id=graph_run_id,
            action="load",
            present_run_ids=(),
            replace_requested_by="",
            reason="the database holds no run",
        )
    if present == (graph_run_id,):
        marker = state.marker_for(graph_run_id)
        return ReplacementDecision(
            graph_run_id=graph_run_id,
            action="reload",
            present_run_ids=present,
            replace_requested_by="",
            reason=(
                "the database already holds this run; MERGE makes the load idempotent"
                if marker is None or marker.is_complete
                else f"the database holds this run with an unfinished load ({marker.status})"
            ),
        )
    raise GraphRunConflictError(
        requested=graph_run_id, present=present, detail=policy_note.lstrip("; ")
    )


# -- the destructive path -------------------------------------------------------------------------


@dataclass(frozen=True)
class WipeResult:
    """What a wipe destroyed, and where. Named fields so a log line cannot lie about the target."""

    target: GraphTarget
    nodes_deleted: int
    relationships_deleted: int
    batch_rows: int


def wipe(
    driver: Driver, settings: GraphSettings, *, batch_rows: int = WIPE_BATCH_ROWS
) -> WipeResult:
    """Delete every node and relationship, in batched auto-commit transactions.

    `resolve_target` is the first statement of the function and reads both names, so a blank
    `NEO4J_URI` or `NEO4J_DATABASE` raises `UnresolvedGraphTargetError` here with nothing sent
    to any server. That ordering is the whole point of the function's shape, and
    `tests/graph/test_lifecycle.py` asserts it by counting nodes afterwards.

    Auto-commit (`session.run`) rather than a managed transaction, because `IN TRANSACTIONS` is
    refused in an explicit one — the verbatim error is in the module docstring. Schema survives:
    a wipe deletes data, and §5.2's 19 constraints and the 30 indexes are still
    there afterwards *(verified 2026-08-03; 30 = §5.3's 9, one backing index per constraint, and
    the 2 default token-lookup indexes)*. **Not atomic** — see the module docstring's boundary
    table.
    """
    target = resolve_target(settings)
    if not isinstance(batch_rows, int) or isinstance(batch_rows, bool) or batch_rows < 1:
        raise GraphLifecycleError(f"wipe batch_rows is {batch_rows!r}, expected a positive integer")
    with driver.session(database=target.database) as session:
        summary = session.run(
            settings.query(wipe_statement()), {"batch_rows": batch_rows}
        ).consume()
    counters = summary.counters
    return WipeResult(
        target=target,
        nodes_deleted=counters.nodes_deleted,
        relationships_deleted=counters.relationships_deleted,
        batch_rows=batch_rows,
    )


# -- completion metadata --------------------------------------------------------------------------


def _write_marker(
    driver: Driver,
    settings: GraphSettings,
    graph_run_id: str,
    properties: dict[str, Any],
    *,
    remove: Sequence[str] = (),
) -> LoadMarker:
    target = resolve_target(settings)
    rows, _, _ = driver.execute_query(
        settings.query(write_marker_statement(remove)),
        {"run_id": graph_run_id, "properties": properties},
        database_=target.database,
    )
    return LoadMarker.from_properties(rows[0]["p"])


def begin_load(
    driver: Driver, settings: GraphSettings, graph_run_id: str, *, wiped: bool
) -> LoadMarker:
    """Mark the database mid-load. Called after any wipe — `MATCH (n)` would delete this node.

    Every field a completion carries is removed here rather than left standing, so a re-load of
    a run that previously finished cannot show yesterday's `completed_at` and yesterday's counts
    while today's rows are still arriving.
    """
    return _write_marker(
        driver,
        settings,
        graph_run_id,
        {
            "status": STATUS_LOADING,
            "started_at": utc_now_iso(),
            "schema_version": settings.schema_version,
            "wiped": wiped,
        },
        remove=REMOVABLE_MARKER_PROPERTIES,
    )


def complete_load(
    driver: Driver, settings: GraphSettings, graph_run_id: str, *, nodes: int, edges: int
) -> LoadMarker:
    """The only place `status: "complete"` is ever written. Call last, after verification."""
    return _write_marker(
        driver,
        settings,
        graph_run_id,
        {
            "status": STATUS_COMPLETE,
            "completed_at": utc_now_iso(),
            "node_count": nodes,
            "edge_count": edges,
        },
        remove=("error",),
    )


def fail_load(
    driver: Driver, settings: GraphSettings, graph_run_id: str, *, error: str
) -> LoadMarker:
    """Record why a load stopped. Best-effort: the caller re-raises the original failure.

    Never a substitute for the absence of `complete` — it is only there so the next operator
    reads a reason instead of inferring one from a `loading` marker with an old timestamp.
    """
    return _write_marker(
        driver,
        settings,
        graph_run_id,
        {"status": STATUS_FAILED, "error": error[:2000]},
        remove=("completed_at", "node_count", "edge_count"),
    )


# -- post-load verification -----------------------------------------------------------------------


@dataclass(frozen=True)
class LoadVerification:
    """What the database held when the load claimed to be done."""

    graph_run_id: str
    node_count: int
    relationship_count: int
    foreign_nodes: int
    foreign_relationships: int


def verify_load(
    driver: Driver,
    settings: GraphSettings,
    graph_run_id: str,
    *,
    expected_nodes: int,
    expected_edges: int,
) -> LoadVerification:
    """The database holds this run's rows, all of them, and nothing else's.

    Narrow on purpose. This is not §10 — no dangling-endpoint sweep, no provenance audit, no
    query pack; those belong to the verify stage, which reads a *finished* graph. The question
    here is the only one completion metadata can honestly answer: did what was submitted arrive,
    and is the graph one run rather than two.
    """
    state = inspect_database(driver, settings)
    node_count = _count_for_run(driver, settings, graph_run_id, nodes=True)
    relationship_count = _count_for_run(driver, settings, graph_run_id, nodes=False)
    # Anything in the database that is not this run's and not the loader's own marker. Counted
    # by subtraction from the totals rather than by a second predicate, so a row carrying no
    # `graph_run_id` at all — which no projection emits — is included rather than filtered out.
    foreign_nodes = state.node_count - node_count
    foreign_relationships = state.relationship_count - relationship_count

    problems: list[str] = []
    if node_count != expected_nodes:
        problems.append(f"{node_count} nodes carry {graph_run_id!r}, expected {expected_nodes}")
    if relationship_count != expected_edges:
        problems.append(
            f"{relationship_count} relationships carry {graph_run_id!r}, expected {expected_edges}"
        )
    if foreign_nodes or foreign_relationships:
        problems.append(
            f"{foreign_nodes} node(s) and {foreign_relationships} relationship(s) belong to "
            f"another run or to none: {state.run_ids!r}"
        )
    if problems:
        raise LoadVerificationError(
            f"post-load verification failed for {graph_run_id!r}: " + "; ".join(problems)
        )
    return LoadVerification(
        graph_run_id=graph_run_id,
        node_count=node_count,
        relationship_count=relationship_count,
        foreign_nodes=foreign_nodes,
        foreign_relationships=foreign_relationships,
    )


def _count_for_run(
    driver: Driver, settings: GraphSettings, graph_run_id: str, *, nodes: bool
) -> int:
    target = resolve_target(settings)
    statement = (
        f"{_DATA_NODES} AND n.{RUN_ID_PROPERTY} = $run_id RETURN count(n) AS total"
        if nodes
        else f"MATCH ()-[r]->() WHERE r.{RUN_ID_PROPERTY} = $run_id RETURN count(r) AS total"
    )
    rows, _, _ = driver.execute_query(
        settings.query(statement), {"run_id": graph_run_id}, database_=target.database
    )
    return int(rows[0]["total"])


# -- the whole lifecycle, in the order §6.2 and §6.3 require ------------------------------------


@dataclass(frozen=True)
class LoadOutcome:
    """One `graph load`, described by what it decided, destroyed, wrote and checked."""

    graph_run_id: str
    target: GraphTarget
    decision: ReplacementDecision
    wipe: WipeResult | None
    schema: SchemaState
    result: LoadResult
    verification: LoadVerification
    marker: LoadMarker

    @property
    def complete(self) -> bool:
        return self.marker.is_complete


def load_graph_run(
    driver: Driver,
    settings: GraphSettings,
    graph_run_id: str,
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    *,
    replace: bool = False,
    wipe_batch_rows: int = WIPE_BATCH_ROWS,
    await_indexes_seconds: int = 300,
) -> LoadOutcome:
    """Decide, wipe if asked, apply the schema, load, verify, and only then say complete.

    The order is the contract:

        1. resolve the target      — both names, before anything else can touch a database
        2. check the export        — every row carries the run id this load claims
        3. inspect and decide      — §6.2's policy; a different run without `replace` raises here
        4. wipe                    — only on the `replace` branch, batched, auto-commit
        5. schema                  — always, `IF NOT EXISTS`; after a wipe because the wipe
                                     removes rows, not constraints, and re-applying is a no-op
                                     that keeps the two paths identical
        6. `begin_load`            — after the wipe, or it would delete its own marker
        7. nodes, then edges       — `loader.load_export`, one transaction per batch
        8. verify                  — counts in the database against counts submitted
        9. `complete_load`         — the single write that makes this run readable as finished

    Any failure from step 4 on leaves the marker at `loading` or `failed` and the graph
    unfinished. Nothing here is rolled back for you; see the module docstring's boundary table.

    **Steps 4 to 6 are inside the `try`, and that is the whole point of where it starts**
    *(corrected 2026-08-03 after review)*. They used to sit above it, which made `fail_load`
    unreachable for exactly the three steps that run *after* the wipe has already deleted the
    old marker. A failure in `apply_schema`, `await_indexes` or `begin_load` therefore left an
    empty database with no marker at all — which `decide_replacement` reads as "the database
    holds no run", indistinguishable from a fresh install, and the destroyed run left no trace.
    The module docstring claims data-present-without-`complete` is the unambiguous incomplete
    state; a wipe that erased the evidence of itself was the one hole in that claim.
    """
    target = resolve_target(settings)
    check_export_run_identity(graph_run_id, nodes, edges)

    state = inspect_database(driver, settings)
    decision = decide_replacement(
        state, graph_run_id, replace=replace, replace_policy=settings.replace_policy
    )

    wiped: WipeResult | None = None
    try:
        if decision.wipes:
            wiped = wipe(driver, settings, batch_rows=wipe_batch_rows)

        schema = apply_schema(driver, settings)
        await_indexes(driver, settings, timeout_seconds=await_indexes_seconds)

        marker = begin_load(driver, settings, graph_run_id, wiped=wiped is not None)
        result = load_export(driver, settings, nodes, edges)
        verification = verify_load(
            driver,
            settings,
            graph_run_id,
            expected_nodes=len(nodes),
            expected_edges=len(edges),
        )
    except Exception as failure:  # noqa: BLE001 - re-raised below; the marker must not lie
        try:
            fail_load(driver, settings, graph_run_id, error=f"{type(failure).__name__}: {failure}")
        except Exception:  # pragma: no cover - a dead connection must not mask the real failure
            pass
        raise

    marker = complete_load(
        driver, settings, graph_run_id, nodes=len(nodes), edges=len(edges)
    )
    return LoadOutcome(
        graph_run_id=graph_run_id,
        target=target,
        decision=decision,
        wipe=wiped,
        schema=schema,
        result=result,
        verification=verification,
        marker=marker,
    )


__all__ = [
    "DatabaseState",
    "EDGE_INVENTORY_STATEMENT",
    "ExportRunIdentityError",
    "GraphLifecycleError",
    "GraphRunConflictError",
    "GraphTarget",
    "LOAD_MARKER_LABEL",
    "LoadMarker",
    "LoadOutcome",
    "LoadVerification",
    "LoadVerificationError",
    "MARKER_STATEMENT",
    "NODE_INVENTORY_STATEMENT",
    "REMOVABLE_MARKER_PROPERTIES",
    "RUN_ID_PROPERTY",
    "ReplacementDecision",
    "STATUS_COMPLETE",
    "STATUS_FAILED",
    "STATUS_LOADING",
    "WIPE_BATCH_ROWS",
    "WipeResult",
    "begin_load",
    "check_export_run_identity",
    "complete_load",
    "completed_run_id",
    "decide_replacement",
    "fail_load",
    "graph_node_count",
    "inspect_database",
    "load_graph_run",
    "read_markers",
    "resolve_target",
    "verify_load",
    "wipe",
    "wipe_batch_report_statement",
    "wipe_statement",
    "write_marker_statement",
]
