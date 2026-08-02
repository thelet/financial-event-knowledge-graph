"""Writing a read export into Neo4j, in batches, without ever losing a row quietly.

The stage's whole claim is in one sentence: **every batch compares the rows it submitted
against the rows the server says it wrote, and a shortfall aborts the load naming the keys**
(V1_GRAPH_PROTOTYPE §6.3, acceptance criterion §10.2). `MATCH` filters, it does not raise — a
relationship row whose endpoint is absent produces zero rows and no error — so the guarantee
this module offers is the comparison, not the Cypher. The `MATCH` is only what makes the
shortfall visible.

Three shapes of refusal, in the order they can happen:

    read        `reader.py` rejects a map-valued property, a null, a heterogeneous list or a
                duplicate key **before this module is called at all**
    build       a label or relationship type outside the measured allowlist never reaches a
                statement, so nothing from the export is ever concatenated into Cypher
    write       a batch whose `written` count is short rolls its own transaction back and
                raises `ShortWriteError` with the offending keys

**No APOC** (§6.3). Dynamic labels are the only thing it would buy, and the export needs none:
the nodes fall into exactly **15 label sets** and the edges into exactly **13 (type, source
base label, target base label)** groups *(measured 2026-08-03 over
`data/graph_runs/graph-v1-886059d862ce/`)*, so one statement per group is 28 statements with
every label written literally into the Cypher and every value passed as a parameter.

**Why explicit transactions rather than `driver.execute_query`.** The shortfall must be
detected *inside* the transaction that wrote the batch, both so the diagnostic query can see
the batch's own uncommitted writes and so raising rolls that batch back rather than leaving a
partial group behind. `session.begin_transaction` carries the timeout as a number, because
driver 6.2 refuses a `Query` object there — `TypeError: Query object is only supported for
session.run` *(verified in `neo4j._sync.work.transaction`, 2026-08-03)*. The configured
ceiling still has exactly one reader: it is taken off the `Query` that
`GraphSettings.query()` builds.

Earlier batches stay committed when a later one fails. That is deliberate and is not a
half-written graph pretending to be whole: the completion marker belongs to the lifecycle
stage, which never writes one for a load that raised, so a partial load is unambiguously
incomplete in exactly the way the repository's atomic-finalization rule requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Protocol, Sequence

from neo4j import Driver

from graph.core.models import BASE_LABELS, GraphEdge, GraphNode

from .connection import GraphSettings
from .schema import KEY_PROPERTIES

#: A label may only ever be an identifier. This is the injection barrier, not the allowlist:
#: it is what makes a backtick, a brace or a closing paren unable to reach a statement even if
#: the allowlist below were widened carelessly.
LABEL_PATTERN = re.compile(r"\A[A-Za-z][A-Za-z0-9_]*\Z")
TYPE_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")

#: The 14 concrete labels that occur in the real export, beside the 7 base labels *(measured
#: 2026-08-03 over 28,836 nodes)*. Entity chains come from the ontology's `is_a` graph and the
#: four marker labels from the projection: `NotAttempted` 10,852, `Warned` 186, `Rejected` 46,
#: `Unresolved` 1. Listed rather than derived from the rows being loaded, because an allowlist
#: computed from its own input allows everything; a new ontology type must be added here (or
#: passed as `allowed_labels`) and the failure is loud.
CONCRETE_LABELS: tuple[str, ...] = (
    "Agreement",
    "AssetBackedDebtFacility",
    "Company",
    "CreditAgreement",
    "CreditFacility",
    "DisclosureChannel",
    "NotAttempted",
    "Person",
    "PublicCompany",
    "Rejected",
    "StockExchange",
    "Subsidiary",
    "Unresolved",
    "Warned",
)

ALLOWED_LABELS: tuple[str, ...] = tuple(sorted(set(BASE_LABELS) | set(CONCRETE_LABELS)))

#: The 12 relationship types in the real export (§3.2), with their measured row counts:
#: FOUND_IN 17,127 · PART_OF 8,776 · EVIDENCED_BY 2,713 · HAS_OBSERVATION 2,707 ·
#: OBSERVATION_OF_SUBJECT 2,707 · CONCERNS_METRIC 1,520 · DISTINCT_FROM 36 · PARTICIPATES_IN 10
#: · HOLDS_POSITION_AT 3 · RECONCILES_TO 2 · BORROWS_UNDER 1 · PLACEHOLDER_FOR 1.
ALLOWED_RELATIONSHIP_TYPES: tuple[str, ...] = (
    "BORROWS_UNDER",
    "CONCERNS_METRIC",
    "DISTINCT_FROM",
    "EVIDENCED_BY",
    "FOUND_IN",
    "HAS_OBSERVATION",
    "HOLDS_POSITION_AT",
    "OBSERVATION_OF_SUBJECT",
    "PARTICIPATES_IN",
    "PART_OF",
    "PLACEHOLDER_FOR",
    "RECONCILES_TO",
)

#: How many offending keys a `ShortWriteError` message names before it summarises. The full set
#: is on the exception; the message is read by a human.
NAMED_IN_MESSAGE = 10


class GraphLoadError(RuntimeError):
    """Anything that stops this module from writing what it was given."""


class DisallowedLabelError(GraphLoadError):
    """A label that is not an identifier, or not on the allowlist. Never reaches Cypher."""


class DisallowedRelationshipTypeError(GraphLoadError):
    """A relationship type that is not an identifier, or not on the allowlist."""


@dataclass(frozen=True)
class MissingEndpoint:
    """One relationship row the database could not attach, and which end was absent."""

    edge_key: str
    source_key: str
    target_key: str
    source_missing: bool
    target_missing: bool

    def describe(self) -> str:
        ends = []
        if self.source_missing:
            ends.append(f"source {self.source_key!r}")
        if self.target_missing:
            ends.append(f"target {self.target_key!r}")
        return f"{self.edge_key} (missing {' and '.join(ends) or 'nothing — see counts'})"


class ShortWriteError(GraphLoadError):
    """A batch wrote fewer rows than it submitted.

    The load-bearing error of this module. `submitted`/`written` are the raw counts and `keys`
    names the rows that did not land, so the failure is actionable without re-running.
    """

    def __init__(
        self,
        *,
        group: str,
        submitted: int,
        written: int,
        keys: tuple[str, ...],
        missing_endpoints: tuple[MissingEndpoint, ...] = (),
        cause: str,
    ) -> None:
        self.group = group
        self.submitted = submitted
        self.written = written
        self.keys = keys
        self.missing_endpoints = missing_endpoints
        named = ", ".join(keys[:NAMED_IN_MESSAGE])
        if len(keys) > NAMED_IN_MESSAGE:
            named += f", and {len(keys) - NAMED_IN_MESSAGE} more"
        super().__init__(
            f"{group}: submitted {submitted} rows, wrote {written} — {cause}. "
            f"Offending keys: {named or '<none identified>'}"
        )


@dataclass(frozen=True)
class NodeGroupLoad:
    """What one exact label set cost and produced."""

    labels: tuple[str, ...]
    submitted: int
    written: int
    batches: int

    @property
    def base_label(self) -> str:
        return self.labels[0]


@dataclass(frozen=True)
class EdgeGroupLoad:
    """What one (type, source base label, target base label) group cost and produced."""

    type: str
    source_base_label: str
    target_base_label: str
    submitted: int
    written: int
    batches: int


@dataclass(frozen=True)
class LoadResult:
    """Per-group and total counts, for the lifecycle and verification stages to consume.

    No completion metadata and no manifest: writing one is the lifecycle stage's job, and a
    result object that also finalized a run would let a caller mark a load complete by
    accident.
    """

    node_groups: tuple[NodeGroupLoad, ...]
    edge_groups: tuple[EdgeGroupLoad, ...]

    @property
    def nodes_submitted(self) -> int:
        return sum(group.submitted for group in self.node_groups)

    @property
    def nodes_written(self) -> int:
        return sum(group.written for group in self.node_groups)

    @property
    def edges_submitted(self) -> int:
        return sum(group.submitted for group in self.edge_groups)

    @property
    def edges_written(self) -> int:
        return sum(group.written for group in self.edge_groups)

    @property
    def label_set_count(self) -> int:
        return len(self.node_groups)

    @property
    def edge_group_count(self) -> int:
        return len(self.edge_groups)


class CypherRunner(Protocol):
    """The one method this module needs from a transaction.

    Structural, so `write_node_batch` and `write_edge_batch` — where the short-write comparison
    lives — are drivable with no server at all. The claim under test is "a short count raises",
    and that claim should not require a database to check.
    """

    def run(self, query: str, parameters: dict[str, Any] | None = None, **kwargs: Any) -> Any: ...


# -- statement construction -------------------------------------------------------------------


def validated_labels(
    labels: Sequence[str], allowed: Sequence[str] = ALLOWED_LABELS
) -> tuple[str, ...]:
    """`labels`, or a refusal. Nothing else in this module writes a label into a statement."""
    if not labels:
        raise DisallowedLabelError("a node group has no labels")
    checked: list[str] = []
    for label in labels:
        if not isinstance(label, str) or not LABEL_PATTERN.match(label):
            raise DisallowedLabelError(
                f"label {label!r} is not an identifier; labels are written into Cypher "
                "literally and only an identifier may be"
            )
        if label not in allowed:
            raise DisallowedLabelError(
                f"label {label!r} is not on the allowlist {tuple(allowed)!r}; a label the "
                "loader has never measured is a projection change, not a load-time decision"
            )
        checked.append(label)
    if checked[0] not in KEY_PROPERTIES:
        raise DisallowedLabelError(
            f"labels[0] is {checked[0]!r}, which is not one of the base labels {BASE_LABELS!r} "
            "— the MERGE key property is derived from it"
        )
    return tuple(checked)


def validated_type(
    relationship_type: str, allowed: Sequence[str] = ALLOWED_RELATIONSHIP_TYPES
) -> str:
    if not isinstance(relationship_type, str) or not TYPE_PATTERN.match(relationship_type):
        raise DisallowedRelationshipTypeError(
            f"relationship type {relationship_type!r} is not an upper-case identifier"
        )
    if relationship_type not in allowed:
        raise DisallowedRelationshipTypeError(
            f"relationship type {relationship_type!r} is not on the allowlist "
            f"{tuple(allowed)!r}; §3.2 declares a closed set of edge types"
        )
    return relationship_type


def node_statement(
    labels: Sequence[str], *, allowed_labels: Sequence[str] = ALLOWED_LABELS
) -> str:
    """One `UNWIND` statement for one exact label set.

    `MERGE` is on the base label and its key property alone — that is where §5.2's uniqueness
    constraint sits, and a `MERGE` on the full label set would not use it. The concrete labels
    are then `SET` statically, which is the whole reason the rows are grouped by label set and
    the reason no APOC procedure is needed to add them.

    `count(DISTINCT n)` is returned beside `count(n)` because they differ exactly when two rows
    in one batch carry the same key: the second silently overwrites the first, which is a lost
    row that `count(n)` alone would report as a success.
    """
    checked = validated_labels(labels, allowed_labels)
    base, extra = checked[0], checked[1:]
    lines = [
        "UNWIND $rows AS row",
        f"MERGE (n:{base} {{{KEY_PROPERTIES[base]}: row.key}})",
    ]
    if extra:
        lines.append("SET n" + "".join(f":{label}" for label in extra))
    lines.append("SET n += row.properties")
    lines.append("RETURN count(n) AS written, count(DISTINCT n) AS distinct_written")
    return "\n".join(lines)


def edge_statement(
    relationship_type: str,
    source_base_label: str,
    target_base_label: str,
    *,
    allowed_types: Sequence[str] = ALLOWED_RELATIONSHIP_TYPES,
    allowed_labels: Sequence[str] = ALLOWED_LABELS,
) -> str:
    """One `UNWIND` statement for one (type, source base label, target base label) group.

    `MATCH` on both endpoints, never `MERGE`: an edge whose endpoint is missing must not
    conjure an empty node into existence (§6.3). The endpoint labels are base labels so the
    match uses §5.2's constraint index rather than scanning.
    """
    relationship = validated_type(relationship_type, allowed_types)
    source = validated_labels((source_base_label,), allowed_labels)[0]
    target = validated_labels((target_base_label,), allowed_labels)[0]
    return "\n".join(
        [
            "UNWIND $rows AS row",
            f"MATCH (s:{source} {{{KEY_PROPERTIES[source]}: row.source_key}})",
            f"MATCH (t:{target} {{{KEY_PROPERTIES[target]}: row.target_key}})",
            f"MERGE (s)-[r:{relationship} {{edge_key: row.edge_key}}]->(t)",
            "SET r += row.properties",
            "RETURN count(r) AS written, count(DISTINCT r) AS distinct_written",
        ]
    )


def endpoint_diagnostic_statement(
    source_base_label: str,
    target_base_label: str,
    *,
    allowed_labels: Sequence[str] = ALLOWED_LABELS,
) -> str:
    """Which rows of a short batch had no endpoint to attach to.

    Run inside the failing batch's own transaction, so it sees that batch's uncommitted writes
    and cannot blame a row the batch itself had already handled.
    """
    source = validated_labels((source_base_label,), allowed_labels)[0]
    target = validated_labels((target_base_label,), allowed_labels)[0]
    return "\n".join(
        [
            "UNWIND $rows AS row",
            f"OPTIONAL MATCH (s:{source} {{{KEY_PROPERTIES[source]}: row.source_key}})",
            f"OPTIONAL MATCH (t:{target} {{{KEY_PROPERTIES[target]}: row.target_key}})",
            "WITH row, s, t WHERE s IS NULL OR t IS NULL",
            "RETURN row.edge_key AS edge_key, row.source_key AS source_key,",
            "       row.target_key AS target_key,",
            "       s IS NULL AS source_missing, t IS NULL AS target_missing",
            "ORDER BY edge_key",
        ]
    )


# -- grouping and batching --------------------------------------------------------------------


def group_nodes(nodes: Iterable[GraphNode]) -> dict[tuple[str, ...], list[GraphNode]]:
    """Rows by their **exact** label set, in a deterministic order.

    Exact, not by base label: `(:Issue)` and `(:Issue:Rejected)` are different statements, and
    a group keyed on the base label would need a runtime label list — which is the APOC
    dependency §6.3 refuses.
    """
    grouped: dict[tuple[str, ...], list[GraphNode]] = {}
    for node in nodes:
        grouped.setdefault(tuple(node.labels), []).append(node)
    return {labels: grouped[labels] for labels in sorted(grouped)}


def group_edges(edges: Iterable[GraphEdge]) -> dict[tuple[str, str, str], list[GraphEdge]]:
    grouped: dict[tuple[str, str, str], list[GraphEdge]] = {}
    for edge in edges:
        key = (edge.type, edge.source_base_label, edge.target_base_label)
        grouped.setdefault(key, []).append(edge)
    return {key: grouped[key] for key in sorted(grouped)}


def node_parameters(node: GraphNode) -> dict[str, Any]:
    """Everything the node statement reads, as parameters. No value is ever in the statement."""
    return {"key": node.key, "properties": dict(node.properties)}


def edge_parameters(edge: GraphEdge) -> dict[str, Any]:
    return {
        "edge_key": edge.edge_key,
        "source_key": edge.source_key,
        "target_key": edge.target_key,
        "properties": dict(edge.properties),
    }


def batches(rows: Sequence[Any], size: int) -> Iterator[list[Any]]:
    if size < 1:
        raise GraphLoadError(f"batch size {size!r} is not a positive integer")
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


# -- the write, and the comparison that makes it trustworthy ------------------------------------


def _counts(runner: CypherRunner, statement: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    record = runner.run(statement, {"rows": rows}).single()
    if record is None:
        # An aggregating RETURN always yields exactly one row; no record means the server did
        # not run the statement we think it ran, which is a shortfall of the worst kind.
        return 0, 0
    return int(record["written"]), int(record["distinct_written"])


def write_node_batch(
    runner: CypherRunner, statement: str, rows: list[dict[str, Any]], *, group: str
) -> int:
    """One batch of nodes, or `ShortWriteError`, which leaves the caller's transaction to roll back.

    Two ways to be short, both fatal: fewer rows processed than submitted, or fewer *distinct*
    nodes than rows — the second is a duplicate key inside the batch, where two rows merged
    onto one node and one of them ceased to exist.
    """
    written, distinct = _counts(runner, statement, rows)
    if written == len(rows) and distinct == len(rows):
        return written
    keys = [row["key"] for row in rows]
    duplicates = tuple(sorted({key for key in keys if keys.count(key) > 1}))
    cause = (
        "two or more rows share a key and merged onto one node"
        if duplicates
        # No duplicate explains it, so the batch's own keys are all that can honestly be
        # named — the offender is among them, and saying which would be a guess.
        else "the server processed fewer rows than were submitted; the batch's first keys are"
        " listed and the offender is one of them"
    )
    raise ShortWriteError(
        group=group,
        submitted=len(rows),
        written=min(written, distinct),
        keys=duplicates or tuple(keys[:NAMED_IN_MESSAGE]),
        cause=cause,
    )


def write_edge_batch(
    runner: CypherRunner,
    statement: str,
    diagnostic: str,
    rows: list[dict[str, Any]],
    *,
    group: str,
) -> int:
    """One batch of relationships, or `ShortWriteError` naming every `edge_key` that did not land.

    This is the case §6.3 exists for. `MATCH` filters silently, so the count comparison here is
    the only thing standing between a missing endpoint and a graph that looks complete.
    """
    written, distinct = _counts(runner, statement, rows)
    if written == len(rows) and distinct == len(rows):
        return written
    missing = tuple(
        MissingEndpoint(
            edge_key=record["edge_key"],
            source_key=record["source_key"],
            target_key=record["target_key"],
            source_missing=bool(record["source_missing"]),
            target_missing=bool(record["target_missing"]),
        )
        for record in runner.run(diagnostic, {"rows": rows})
    )
    if missing:
        cause = "endpoints were not found, and MATCH filters rather than raising"
        keys = tuple(endpoint.edge_key for endpoint in missing)
    else:
        cause = (
            "two or more rows share an edge_key and merged onto one relationship"
            if distinct < written
            else "the server processed fewer rows than were submitted"
        )
        edge_keys = [row["edge_key"] for row in rows]
        keys = tuple(sorted({key for key in edge_keys if edge_keys.count(key) > 1}))
    raise ShortWriteError(
        group=group,
        submitted=len(rows),
        written=min(written, distinct),
        keys=keys,
        missing_endpoints=missing,
        cause=cause,
    )


def _session_batches(
    driver: Driver,
    settings: GraphSettings,
    statement: str,
    rows: list[dict[str, Any]],
    batch_size: int,
    write: Any,
) -> tuple[int, int]:
    """Run `write` over `rows` in batches, one explicit transaction each.

    The timeout is taken off `settings.query(...)` rather than read from the settings directly,
    so `query_timeout_seconds` keeps the single reader `connection.py` gives it, and unwrapped
    because driver 6.2 refuses a `Query` object inside an explicit transaction.
    """
    query = settings.query(statement)
    written_total = 0
    batch_count = 0
    with driver.session(database=settings.resolved_database) as session:
        for batch in batches(rows, batch_size):
            with session.begin_transaction(timeout=query.timeout) as transaction:
                written_total += write(transaction, query.text, batch)
                transaction.commit()
            batch_count += 1
    return written_total, batch_count


def load_nodes(
    driver: Driver,
    settings: GraphSettings,
    nodes: Sequence[GraphNode],
    *,
    allowed_labels: Sequence[str] = ALLOWED_LABELS,
) -> tuple[NodeGroupLoad, ...]:
    """Every node, grouped by exact label set. Statements are built before anything is sent."""
    grouped = group_nodes(nodes)
    statements = {
        labels: node_statement(labels, allowed_labels=allowed_labels) for labels in grouped
    }
    results: list[NodeGroupLoad] = []
    for labels, group_nodes_list in grouped.items():
        rows = [node_parameters(node) for node in group_nodes_list]
        group = ":" + ":".join(labels)
        written, batch_count = _session_batches(
            driver,
            settings,
            statements[labels],
            rows,
            settings.node_batch_size,
            lambda tx, text, batch, group=group: write_node_batch(tx, text, batch, group=group),
        )
        results.append(
            NodeGroupLoad(
                labels=tuple(labels),
                submitted=len(rows),
                written=written,
                batches=batch_count,
            )
        )
    return tuple(results)


def load_edges(
    driver: Driver,
    settings: GraphSettings,
    edges: Sequence[GraphEdge],
    *,
    allowed_types: Sequence[str] = ALLOWED_RELATIONSHIP_TYPES,
    allowed_labels: Sequence[str] = ALLOWED_LABELS,
) -> tuple[EdgeGroupLoad, ...]:
    """Every relationship, grouped by type and endpoint labels. Call only after `load_nodes`."""
    grouped = group_edges(edges)
    statements = {
        key: (
            edge_statement(
                key[0], key[1], key[2], allowed_types=allowed_types, allowed_labels=allowed_labels
            ),
            endpoint_diagnostic_statement(key[1], key[2], allowed_labels=allowed_labels),
        )
        for key in grouped
    }
    results: list[EdgeGroupLoad] = []
    for key, group_edges_list in grouped.items():
        relationship_type, source_label, target_label = key
        statement, diagnostic = statements[key]
        rows = [edge_parameters(edge) for edge in group_edges_list]
        group = f"[:{relationship_type}] ({source_label})->({target_label})"
        written, batch_count = _session_batches(
            driver,
            settings,
            statement,
            rows,
            settings.edge_batch_size,
            lambda tx, text, batch, diagnostic=diagnostic, group=group: write_edge_batch(
                tx, text, diagnostic, batch, group=group
            ),
        )
        results.append(
            EdgeGroupLoad(
                type=relationship_type,
                source_base_label=source_label,
                target_base_label=target_label,
                submitted=len(rows),
                written=written,
                batches=batch_count,
            )
        )
    return tuple(results)


def load_export(
    driver: Driver,
    settings: GraphSettings,
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    *,
    allowed_labels: Sequence[str] = ALLOWED_LABELS,
    allowed_types: Sequence[str] = ALLOWED_RELATIONSHIP_TYPES,
) -> LoadResult:
    """Nodes first, then edges — the order is the contract, not a convenience.

    Every edge statement `MATCH`es its endpoints, so an edge loaded before its nodes would find
    nothing and abort the load. Loading all nodes first is what makes a shortfall mean "the
    export references a node it does not contain" rather than "the edges arrived early".
    """
    # Build every edge statement first, and throw the text away. Constructing one is what
    # validates its type and endpoint labels, and a relationship type off the allowlist is a
    # fact about the export that should not be discovered after 28,836 nodes have landed.
    for relationship_type, source_label, target_label in group_edges(edges):
        edge_statement(
            relationship_type,
            source_label,
            target_label,
            allowed_types=allowed_types,
            allowed_labels=allowed_labels,
        )
    node_groups = load_nodes(driver, settings, nodes, allowed_labels=allowed_labels)
    edge_groups = load_edges(
        driver, settings, edges, allowed_types=allowed_types, allowed_labels=allowed_labels
    )
    return LoadResult(node_groups=node_groups, edge_groups=edge_groups)


__all__ = [
    "ALLOWED_LABELS",
    "ALLOWED_RELATIONSHIP_TYPES",
    "CONCRETE_LABELS",
    "CypherRunner",
    "DisallowedLabelError",
    "DisallowedRelationshipTypeError",
    "EdgeGroupLoad",
    "GraphLoadError",
    "LoadResult",
    "MissingEndpoint",
    "NodeGroupLoad",
    "ShortWriteError",
    "batches",
    "edge_parameters",
    "edge_statement",
    "endpoint_diagnostic_statement",
    "group_edges",
    "group_nodes",
    "load_edges",
    "load_export",
    "load_nodes",
    "node_parameters",
    "node_statement",
    "write_edge_batch",
    "write_node_batch",
]
