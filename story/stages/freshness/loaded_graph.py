"""What the database actually holds, in four read statements and one value.

Responsibility: the only place in this stage that names Cypher, and the only place that calls
the executor. Everything downstream of `read_loaded_graph` compares two plain values, which is
what lets §7's comparison logic be exercised with no server running and no fake — a hand-built
`LoadedGraph` is enough.

**Read-only, and structurally so.** Every statement below is a `MATCH`/`RETURN` constant with
no interpolation; values would arrive as bound parameters and today there are none. §16 and
WORKSTREAM_BOUNDARY §3 forbid `CREATE`, `MERGE`, `SET`, `DELETE` and every index or constraint
statement, and `tests/story/test_story_freshness.py` scans this module's own source for those
keywords rather than trusting the sentence.

**A timeout on every statement** (§16), and a positive one: F10 measured that Bolt reads a
transaction timeout of `0` as *no timeout* rather than as *expire immediately*, so
`Neo4jReadExecutor.read` refuses a non-positive value outright. `FRESHNESS_TIMEOUT_SECONDS` is
the ceiling, not the expectation — see its note for what the four statements actually cost.

**Two counts, and the `:GraphLoad` marker is why.** The loader writes one
`(:GraphLoad {graph_run_id, status, node_count, edge_count})` node of its own, excluded from
every count it takes, so its `node_count` is 28,836 against a database holding 28,837 nodes
(measured 2026-08-03). A live count that did not exclude the marker would refuse the current,
correct run with `count_mismatch` every single time. `DATA_NODE_INVENTORY` therefore carries
`WHERE NOT n:GraphLoad`, exactly as `graph/stages/load/lifecycle.py:_DATA_NODES` does. The
relationship statement carries no such clause and that is not an oversight — the marker is an
isolated node, so every relationship in the database came from a projection.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from story.contracts import ReadQueryExecutor
from story.core.models import StoryModel

#: The one label no projection emits. **Re-stated from `graph/stages/load/lifecycle.py:127`,
#: not imported**: `graph.stages` is not a surface this package may reach
#: (WORKSTREAM_BOUNDARY §4), and the same trade is recorded in
#: `story/providers/neo4j_connection.py`'s docstring for the `.env` layering. A test asserts
#: the four inventory statements return what the live marker holds, so a rename upstream
#: surfaces as a failing `neo4j`-marked test rather than as a gate that quietly counts wrong.
LOAD_MARKER_LABEL = "GraphLoad"

#: §5.5's provenance key, on every node and every edge the projection writes. Also the property
#: the marker carries its own identity under.
RUN_ID_PROPERTY = "graph_run_id"

#: Generous by two orders of magnitude, deliberately. Measured 2026-08-03 against the loaded
#: run on the local instance: markers 10 ms, observations 11 ms, edges 25 ms, the full data-node
#: scan 31 ms. The ceiling is not a performance target — it is §16's guarantee that a statement
#: this stage issues cannot hold a connection open indefinitely, and a value close to the
#: measurement would turn an unrelated slow moment into a refusal that reads like staleness.
#: S11's `config/story.yaml` owns per-tool budgets; this constant moves there when it exists.
FRESHNESS_TIMEOUT_SECONDS = 30.0

#: Every `:GraphLoad` node, with the four properties §7 compares. Named fields rather than
#: `properties(m)`: §9's rule, and `plain_value` refuses a whole node anyway. `ORDER BY` so a
#: database that somehow holds two markers reports them the same way twice.
LOAD_MARKERS = (
    f"MATCH (m:{LOAD_MARKER_LABEL}) "
    f"RETURN m.{RUN_ID_PROPERTY} AS graph_run_id, m.status AS status, "
    "m.node_count AS node_count, m.edge_count AS edge_count, "
    "m.completed_at AS completed_at "
    "ORDER BY graph_run_id, status"
)

#: One pass over the loaded nodes answering three of §7's questions at once: how many there are,
#: which run they name, and which ontology they were projected under.
#: `collect(DISTINCT …)` skips nulls, so a node carrying no `graph_run_id` at all would not
#: appear in the id list — `node_count` is what catches that, and the load stage's own
#: verification refuses an unidentified node before the marker is ever written.
DATA_NODE_INVENTORY = (
    f"MATCH (n) WHERE NOT n:{LOAD_MARKER_LABEL} "
    "RETURN count(n) AS node_count, "
    f"collect(DISTINCT n.{RUN_ID_PROPERTY}) AS graph_run_ids, "
    "collect(DISTINCT n.ontology_definition_hash) AS ontology_definition_hashes"
)

EDGE_INVENTORY = (
    "MATCH ()-[r]->() "
    f"RETURN count(r) AS edge_count, collect(DISTINCT r.{RUN_ID_PROPERTY}) AS graph_run_ids"
)

#: §7 names `:Observation` specifically, so it is asked specifically rather than inferred from
#: the all-node scan above. The redundancy is 11 ms and it keeps the code answering the
#: question the plan asks: an `:Observation` from a superseded run is the fact that ends up
#: quoted in a published post (§17.8).
OBSERVATION_INVENTORY = (
    "MATCH (o:Observation) "
    f"RETURN count(o) AS observation_count, collect(DISTINCT o.{RUN_ID_PROPERTY}) AS graph_run_ids"
)

#: Every statement this stage owns, in the order it issues them. Exported so a test can assert
#: that the gate issued these and nothing else — "zero graph writes" is checkable that way and
#: is not checkable by reading the source of a function.
FRESHNESS_STATEMENTS = (
    LOAD_MARKERS,
    DATA_NODE_INVENTORY,
    EDGE_INVENTORY,
    OBSERVATION_INVENTORY,
)


class LoadMarkerRow(StoryModel):
    """One `(:GraphLoad)` node as the gate sees it.

    Every field is optional because Neo4j stores no null: `SET m += {node_count: null}` removes
    the key, and the loader deliberately `REMOVE`s `node_count`, `edge_count` and `completed_at`
    on a load that has not finished (`graph/stages/load/lifecycle.py`'s
    `REMOVABLE_MARKER_PROPERTIES`). So `None` here means *absent*, which for a marker means
    "the load that would have set this has not happened" — and absent is exactly the state §7's
    `load_incomplete` refusal is about.
    """

    graph_run_id: str | None = None
    status: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "LoadMarkerRow":
        return cls(
            graph_run_id=row.get("graph_run_id"),
            status=row.get("status"),
            node_count=row.get("node_count"),
            edge_count=row.get("edge_count"),
            completed_at=row.get("completed_at"),
        )


class LoadedGraph(StoryModel):
    """Everything §7's second and third checks are decided from, read in four statements.

    A value, not a session: once this exists the comparison is pure, so every refusal below can
    be reproduced in a test by writing a different number here. That is the whole reason the
    read and the judgment are two modules.

    The id and hash tuples are **sorted** on the way in. `collect(DISTINCT …)` makes no ordering
    promise, and a check that compared two tuples would otherwise be able to fail over an
    ordering the database chose.
    """

    markers: tuple[LoadMarkerRow, ...] = ()
    node_count: int = 0
    edge_count: int = 0
    observation_count: int = 0
    node_graph_run_ids: tuple[str, ...] = ()
    edge_graph_run_ids: tuple[str, ...] = ()
    observation_graph_run_ids: tuple[str, ...] = ()
    ontology_definition_hashes: tuple[str, ...] = ()

    @property
    def marker_graph_run_ids(self) -> tuple[str, ...]:
        return tuple(sorted(m.graph_run_id for m in self.markers if m.graph_run_id))

    @property
    def marker_statuses(self) -> tuple[str, ...]:
        return tuple(sorted(m.status or "<absent>" for m in self.markers))


def _sorted_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(sorted(str(item) for item in value))


def _first(rows: tuple[dict[str, Any], ...]) -> Mapping[str, Any]:
    """An aggregate statement returns exactly one row; an empty result is an empty row.

    `count(…)` over an empty graph still returns `0`, so no row at all means the statement was
    answered by a fake that had nothing scripted — treated as zeros rather than an `IndexError`,
    because a gate that crashed on an empty database would refuse to report that it is empty.
    """
    return rows[0] if rows else {}


def read_loaded_graph(
    executor: ReadQueryExecutor,
    *,
    timeout_seconds: float = FRESHNESS_TIMEOUT_SECONDS,
) -> LoadedGraph:
    """Four reads, one value. The executor is injected and never constructed here (D1).

    The caller checks connectivity first — F12 measured `verify_connectivity()` failing in 0.0 s
    against a refused port while `execute_query` retries a managed transaction for 35 s, so a
    gate that started here would take half a minute to say "the database is down".
    """
    markers = executor.read(LOAD_MARKERS, {}, timeout_seconds=timeout_seconds)
    nodes = _first(executor.read(DATA_NODE_INVENTORY, {}, timeout_seconds=timeout_seconds))
    edges = _first(executor.read(EDGE_INVENTORY, {}, timeout_seconds=timeout_seconds))
    observations = _first(
        executor.read(OBSERVATION_INVENTORY, {}, timeout_seconds=timeout_seconds))
    return LoadedGraph(
        markers=tuple(LoadMarkerRow.from_row(row) for row in markers),
        node_count=int(nodes.get("node_count") or 0),
        edge_count=int(edges.get("edge_count") or 0),
        observation_count=int(observations.get("observation_count") or 0),
        node_graph_run_ids=_sorted_strings(nodes.get("graph_run_ids")),
        edge_graph_run_ids=_sorted_strings(edges.get("graph_run_ids")),
        observation_graph_run_ids=_sorted_strings(observations.get("graph_run_ids")),
        ontology_definition_hashes=_sorted_strings(nodes.get("ontology_definition_hashes")),
    )


__all__ = [
    "DATA_NODE_INVENTORY",
    "EDGE_INVENTORY",
    "FRESHNESS_STATEMENTS",
    "FRESHNESS_TIMEOUT_SECONDS",
    "LOAD_MARKERS",
    "LOAD_MARKER_LABEL",
    "LoadMarkerRow",
    "LoadedGraph",
    "OBSERVATION_INVENTORY",
    "RUN_ID_PROPERTY",
    "read_loaded_graph",
]
