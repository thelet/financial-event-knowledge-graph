"""The constraints and indexes a loaded graph stands on (V1_GRAPH_PROTOTYPE §5.2, §5.3).

Applied before any row is written, because §6.3's `MERGE` without a backing constraint is a
scan and, at scale, a duplicate-node generator. Every statement carries `IF NOT EXISTS`, so
applying the schema to a database that already has it is a no-op rather than an error — which
is what lets `graph load` be re-run without a wipe.

**What Community Edition actually allows** *(verified 2026-08-03 against the live
`fkg-neo4j`, Neo4j Kernel 5.26.28 community, driver 6.2.0 — this had been carried as
"(unverified)" since G0)*:

    REQUIRE n.x IS UNIQUE     accepted; creates a UNIQUENESS constraint plus a backing RANGE
                              index that takes the constraint's own name
    REQUIRE r.x IS UNIQUE     **accepted** on a relationship pattern too, `FOR ()-[r:T]-()`;
    (relationship)            `SHOW CONSTRAINTS` reports type `RELATIONSHIP_UNIQUENESS`
                              *(probed 2026-08-03 on `:PLACEHOLDER_FOR` and dropped again)*
    REQUIRE n.x IS NODE KEY   refused, Neo.DatabaseError.Schema.ConstraintCreationFailed —
                              "Node Key constraint requires Neo4j Enterprise Edition"
    REQUIRE n.x IS NOT NULL   refused, Neo.DatabaseError.Schema.ConstraintCreationFailed —
                              "Property existence constraint requires Neo4j Enterprise Edition"
    CREATE DATABASE           refused, Neo.ClientError.Statement.UnsupportedAdministrationCommand
    SHOW DATABASES            `neo4j` (standard) and `system` — one user database, as designed for

Two details worth carrying: `IF NOT EXISTS` does **not** soften the two Enterprise refusals —
they raise even for a constraint that does not exist — and the refusal arrives as a
`DatabaseError`, not a `ClientError`, so "unsupported feature" and "server broke" share a
class here. Hence the module offers uniqueness only; NOT NULL stays enforced by the projection
models and re-checked by a post-load assertion, exactly as §5.1 planned for.

**The relationship row of that table was added on 2026-08-03 after review, and it changed the
design.** §5.1 had probed four node-side capabilities and generalised from them that Community
offers node uniqueness; nobody had asked the server about a relationship. It answers yes. Until
then `edge_key` was protected by `MERGE` semantics alone, which are scoped to one
`(source, target)` pair — so two relationships of one type could carry the same `edge_key`
between different pairs and the database had no opinion. §5.2's constraint set is therefore 19
objects, not 7: seven node keys and twelve `edge_key` constraints, one per §3.2 relationship
type.

No APOC (§6.3). Nothing below is a procedure call except `db.awaitIndexes`, which ships with
the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from neo4j import Driver, Query

from graph.core.models import BASE_LABELS

from .connection import GraphSettings


def _key_property(label: str) -> str:
    """`EvidenceSource` -> `evidence_source_id`. Snake-case, not merely lower-case.

    The original rule was `label.lower() + "_id"`, which is correct for every single-word base
    label and silently wrong for the first multi-word one: F0's `:EvidenceSource` carries
    `evidence_source_id`, and `evidencesource_id` would have put the uniqueness constraint and
    the loader's `MERGE` on a property no node has — every source node merging onto one null
    key. Caught by `test_every_base_label_key_property_exists_on_its_nodes`, which is the
    reason that test compares against the projection rather than restating this rule.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", label).lower() + "_id"


#: The key property of each base label. Derived rather than listed because §4.1 makes node keys
#: the extraction ids and every one of them is the snake-cased base label plus `_id`; writing
#: the pairs out by hand would let the set drift from `BASE_LABELS`, which is the closed set
#: §3.1 declares and the set §5.2 says constraints exist for — no more, no fewer.
KEY_PROPERTIES: dict[str, str] = {label: _key_property(label) for label in BASE_LABELS}

#: (constraint name, label). The names are §5.2's.
CONSTRAINTS: tuple[tuple[str, str], ...] = tuple(
    (f"{label.lower()}_key", label) for label in sorted(BASE_LABELS)
)

#: The property every projected relationship is identified by, set by `loader.edge_statement`'s
#: `MERGE (s)-[r:T {edge_key: row.edge_key}]->(t)`. Named here because the constraints below are
#: on it, and the loader reads the same constant back for its allowlist.
EDGE_KEY_PROPERTY = "edge_key"

#: §3.2's closed set of twelve relationship types, which is also the set the loader will submit
#: (`loader.ALLOWED_RELATIONSHIP_TYPES` is this tuple). One relationship uniqueness constraint
#: is created per type: Neo4j scopes a relationship constraint to a single type, so there is no
#: "any relationship" form to write, and a type absent from this list would load with its
#: `edge_key` unprotected.
CONSTRAINED_RELATIONSHIP_TYPES: tuple[str, ...] = (
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

#: (constraint name, relationship type). Lower-cased type plus `_edge_key`, so the names sort
#: beside §5.2's node constraints and say what they protect.
RELATIONSHIP_CONSTRAINTS: tuple[tuple[str, str], ...] = tuple(
    (f"{relationship_type.lower()}_edge_key", relationship_type)
    for relationship_type in CONSTRAINED_RELATIONSHIP_TYPES
)

#: (index name, label, property). §5.3's list, **with every property name checked against the
#: real export** `data/graph_runs/graph-v1-886059d862ce/nodes.jsonl` (28,836 nodes) on
#: 2026-08-03 rather than taken from the plan: all eight exist, with occupancy
#: `Observation.metric_id` 2,707/2,707, `period_key` 2,707/2,707, `subject_entity_id`
#: 2,707/2,707, `source_lane` 2,707/2,707, `Event.event_type_id` 6/6, `Event.occurred_on` 3/6,
#: `Passage.document_id` 8,776/8,776, `Issue.code` 17,127/17,127. `period_key` was the one the
#: brief singled out for confirmation — G1 dropped nulls and removed the three date twins, so
#: it could have gone the way `period_end_date` did (§5.5); it did not, and it is on every row.
#: `occurred_on`'s three nulls are the §5.5 fact that an announcement is not an occurrence, not
#: a gap in the property, and a range index simply does not index the absent rows.
INDEXES: tuple[tuple[str, str, str], ...] = (
    ("obs_metric", "Observation", "metric_id"),
    ("obs_period", "Observation", "period_key"),
    ("obs_subject", "Observation", "subject_entity_id"),
    ("obs_lane", "Observation", "source_lane"),
    ("evt_type", "Event", "event_type_id"),
    ("evt_occurred", "Event", "occurred_on"),
    ("psg_document", "Passage", "document_id"),
    ("iss_code", "Issue", "code"),
)

#: (index name, label, property). Full text, which is what makes Browser search useful without
#: Bloom (§5.3). `Passage.text` is present on all 8,776 passages in the export.
FULLTEXT_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("passage_text", "Passage", "text"),
)

NODE_CONSTRAINT_NAMES = tuple(name for name, _ in CONSTRAINTS)
RELATIONSHIP_CONSTRAINT_NAMES = tuple(name for name, _ in RELATIONSHIP_CONSTRAINTS)
CONSTRAINT_NAMES = NODE_CONSTRAINT_NAMES + RELATIONSHIP_CONSTRAINT_NAMES
INDEX_NAMES = tuple(name for name, _, _ in INDEXES + FULLTEXT_INDEXES)


def constraint_statements() -> tuple[str, ...]:
    """The seven node-key constraints, then the twelve relationship `edge_key` ones.

    The relationship form is written **undirected** (`FOR ()-[r:T]-()`), which is the only form
    the parser accepts — a directed pattern is a syntax error — and it is also the reading that
    is wanted: `edge_key` identifies the relationship, not the direction it happens to point.
    """
    nodes = tuple(
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.{KEY_PROPERTIES[label]} IS UNIQUE"
        for name, label in CONSTRAINTS
    )
    relationships = tuple(
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR ()-[r:{relationship_type}]-() REQUIRE r.{EDGE_KEY_PROPERTY} IS UNIQUE"
        for name, relationship_type in RELATIONSHIP_CONSTRAINTS
    )
    return nodes + relationships


def index_statements() -> tuple[str, ...]:
    range_indexes = tuple(
        f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        for name, label, prop in INDEXES
    )
    fulltext = tuple(
        f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) ON EACH [n.{prop}]"
        for name, label, prop in FULLTEXT_INDEXES
    )
    return range_indexes + fulltext


def schema_statements() -> tuple[str, ...]:
    """Constraints first: a uniqueness constraint brings its own backing index with it, and an
    index created first under a different name would be redundant storage nobody asked for."""
    return constraint_statements() + index_statements()


@dataclass(frozen=True)
class SchemaState:
    """What the server says it holds, in a form two runs can be compared by equality.

    Everything is included, the two default token-lookup indexes among it, because a schema
    check that filtered to what it expected could not notice something unexpected. Callers that
    want only this module's objects filter on `CONSTRAINT_NAMES` / `INDEX_NAMES`.
    """

    #: (name, entity type, label or relationship type, property), sorted. `entityType` comes
    #: from the server rather than from this module's naming convention, so "is this constraint
    #: on a node or on a relationship" is read back rather than assumed.
    constraints: tuple[tuple[str, str, str, str], ...]
    #: (name, index type, label or '', property or ''), sorted.
    indexes: tuple[tuple[str, str, str, str], ...]

    def managed_constraints(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(row for row in self.constraints if row[0] in CONSTRAINT_NAMES)

    def managed_node_constraints(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(row for row in self.managed_constraints() if row[1] == "NODE")

    def managed_relationship_constraints(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(row for row in self.managed_constraints() if row[1] == "RELATIONSHIP")

    def managed_indexes(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(row for row in self.indexes if row[0] in INDEX_NAMES)


def _one(values: list[str] | None) -> str:
    """`labelsOrTypes` and `properties` are lists; every object here is single-valued."""
    return values[0] if values else ""


def read_schema(driver: Driver, settings: GraphSettings) -> SchemaState:
    database = settings.resolved_database
    constraints, _, _ = driver.execute_query(
        settings.query(
            "SHOW CONSTRAINTS YIELD name, entityType, labelsOrTypes, properties "
            "RETURN name, entityType, labelsOrTypes, properties"
        ),
        database_=database,
    )
    indexes, _, _ = driver.execute_query(
        settings.query(
            "SHOW INDEXES YIELD name, type, labelsOrTypes, properties "
            "RETURN name, type, labelsOrTypes, properties"
        ),
        database_=database,
    )
    return SchemaState(
        constraints=tuple(sorted(
            (row["name"], row["entityType"], _one(row["labelsOrTypes"]), _one(row["properties"]))
            for row in constraints
        )),
        indexes=tuple(sorted(
            (row["name"], row["type"], _one(row["labelsOrTypes"]), _one(row["properties"]))
            for row in indexes
        )),
    )


def apply_schema(driver: Driver, settings: GraphSettings) -> SchemaState:
    """Create every constraint and index, then report what the server holds afterwards.

    Idempotent by `IF NOT EXISTS` on every statement, and returning the read-back state rather
    than `None` is what lets a caller — or a test — assert that twice is the same as once
    without knowing which statements were no-ops.
    """
    database = settings.resolved_database
    for statement in schema_statements():
        driver.execute_query(settings.query(statement), database_=database)
    return read_schema(driver, settings)


def await_indexes(driver: Driver, settings: GraphSettings, timeout_seconds: int = 300) -> None:
    """Block until every index is online.

    Index population is asynchronous: `CREATE INDEX` returns before the index exists in a
    usable state, so a load that started immediately would run its `MERGE`s against a
    half-built index and a verification query could read a stale one. `db.awaitIndexes` is a
    server built-in, not APOC.

    Deliberately not wrapped in `settings.query`: this statement is *supposed* to block for up
    to `timeout_seconds`, and the configured 120s statement timeout would cut a legitimate wait
    short and report it as a failure. The client-side ceiling is set from the same number.
    """
    driver.execute_query(
        Query("CALL db.awaitIndexes($seconds)", timeout=timeout_seconds + 30),
        {"seconds": timeout_seconds},
        database_=settings.resolved_database,
    )


__all__ = [
    "CONSTRAINED_RELATIONSHIP_TYPES",
    "CONSTRAINTS",
    "CONSTRAINT_NAMES",
    "EDGE_KEY_PROPERTY",
    "FULLTEXT_INDEXES",
    "INDEXES",
    "INDEX_NAMES",
    "KEY_PROPERTIES",
    "NODE_CONSTRAINT_NAMES",
    "RELATIONSHIP_CONSTRAINTS",
    "RELATIONSHIP_CONSTRAINT_NAMES",
    "SchemaState",
    "apply_schema",
    "await_indexes",
    "constraint_statements",
    "index_statements",
    "read_schema",
    "schema_statements",
]
