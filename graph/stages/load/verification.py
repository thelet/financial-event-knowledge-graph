"""Reconciling the loaded database against the export it was loaded from (§10, §5.5, §6.5).

`loader.py` proves that every row it *submitted* was written. That is a claim about a
conversation with the server, not about the graph: it cannot see a node another run left
behind, a label that arrived twice, a property nobody exported, or an `:Observation` whose
evidence edge is gone. This module asks the other question — **is what is in the database what
the export says should be there** — and answers it as twenty-seven named checks, not as one
boolean.

## Which of §10's twelve criteria this file answers, and which it does not

Stated as a table because the previous version of this docstring said "§10's twelve criteria"
and left a reader to assume twelve-for-twelve *(corrected 2026-08-03 after review: five had no
check at all)*.

    §10.1  rebuild succeeds, non-zero counts     `total_node_count`, `total_relationship_count`,
                                                 `node_counts_by_base_label`
    §10.2  no dangling refs, everything cited    `no_dangling_relationships`,
                                                 `observations_and_events_are_evidenced`
    §10.3  stable across two rebuilds            **not here.** Byte-identical `nodes.jsonl` /
                                                 `edges.jsonl` is a file comparison and belongs
                                                 to `tests/graph/test_export_determinism.py`;
                                                 the load half ("identical counts after two
                                                 loads") needs two loads, which is a lifecycle
                                                 test (`test_lifecycle.py`), not a read of one
                                                 database. Nothing here can see a second load.
    §10.4  claim and evidence provenance         `cited_passages_present`,
                                                 `cited_documents_present`, the four provenance
                                                 checks; the claim/vocabulary split itself is
                                                 asserted on the export by `test_edges.py`
    §10.5  nothing unsupported, nothing lost     `issues_present`,
                                                 `relationship_counts_by_type`
    §10.6  announcement stays distinct           `event_dates_preserved_independently`
    §10.7  unresolved entities are not guessed   `unresolved_entities_are_scoped_per_event`,
                                                 `unresolved_entities_assert_no_identity`
    §10.8  merge hazards are reported            **not here.** The criterion is that G5's report
                                                 publishes four counts; it is explicitly "these
                                                 are findings, not failures", so a *verifier*
                                                 that failed a load over them would be wrong,
                                                 and one that passed silently would be noise.
    §10.9  the query pack runs                   **not here.** G4 owns the pack; there is no
                                                 pack to run yet, and a check over a file that
                                                 does not exist would be a stub.
    §10.10 no refused reading is resurrected     `refused_readings_are_not_resurrected`
    §10.11 warned nodes match the run's count    `warned_nodes_match_export`
    §10.12 no custom frontend                    not executable here and not executable anywhere:
                                                 it is a statement about what the repository
                                                 does not contain.

Value-level parity — `node_property_values_match_export` and its relationship twin — belongs to
no single criterion and is the check §10 never asked for *(added 2026-08-03 after review)*.
Every other check reads counts, keys or property **names**, so `SET n += row.properties` writing
`value: 999` where the export said `1234.5` passed all twenty-two of the checks that
existed before it.

## Where this sits relative to `lifecycle.verify_load`

`lifecycle.load_graph_run` calls `verify_load` between the last batch and `complete_load`, and
`verify_load` is deliberately narrow: rows carrying this run's id equal rows submitted, and
nothing foreign is present. It says so in its own docstring, and it is not §10.

This module is the §10 sweep, and it **reuses rather than restates** what lifecycle already
owns:

    `LOAD_MARKER_LABEL`      the `NOT n:GraphLoad` exclusion, imported, not re-spelled
    `RUN_ID_PROPERTY`        the provenance key identity is read from
    `LoadVerificationError`  what `raise_for_failures` raises, so a caller that wires this in
                             before `complete_load` fails the load with the type lifecycle
                             already fails it with

A caller that wants the full sweep as the completion gate runs `verify_graph(...)` and then
`raise_for_failures(...)` in place of — or after — `verify_load`. Nothing in lifecycle changes,
and lifecycle's two claims are covered here anyway: criterion 1 is the count comparison and
criterion 10 is "exactly one `graph_run_id`", which is `verify_load`'s foreign-row test stated
positively.

## The `:GraphLoad` exclusion, and why relationships need no clause

Every node count, key read, property-key scan and provenance scan below is qualified with
`NOT n:{LOAD_MARKER_LABEL}` — the same predicate `lifecycle._DATA_NODES` uses, built from the
same imported constant so the two cannot drift. The marker is the one node no projection
emits, so counting it would fail criterion 1 by one and criterion 12 by its whole property set
(`status`, `started_at`, `wiped`, `schema_version`, …).

The relationship queries carry **no** such clause, because lifecycle attaches nothing to the
marker — it is written by a `MERGE` on the label and the run id alone. That is an assumption
about another module, so it is not assumed: `marker_relationships` counts relationships
incident to a `:GraphLoad` node and criterion 4 fails if there is one. The exclusion is
verified rather than inherited.

## Expectations come from the export, never from a constant

`expected_graph(nodes, edges)` derives every number this module compares against from the rows
themselves. No check below compares against a literal: 28,836 and 35,603 appear in this file
only inside a comment recording a measurement, never in a comparison. The export is the
authority (§6.1: two artifacts, one direction), and a verifier carrying its own copy of the
answer would pass a load of a *different* export.

The split is deliberate and is what makes the checks testable: `read_database` does all the
I/O, `build_report` is a pure function of (expectation, facts), and `verify_graph` is the two
composed. Every failure mode below can therefore be produced with no server by handing
`build_report` a doctored `DatabaseFacts` — and five of them are also produced against the
real server, because a check that has only ever failed against a stub proves the arithmetic
and not the Cypher.

The split survives the two checks that need per-row data (§10.6's event dates and §10.10's
refused readings) because `read_database` returns those rows *as facts* — six event date pairs,
2,707 observation identities — and `build_report` does the matching. The alternative, passing
the expectation into the reader so the Cypher could filter on it, would have moved a comparison
into the I/O half where no test can reach it without a server.

## Cypher notes

No APOC (§6.3). Labels and relationship types are written into statements only from
`graph.core.models.BASE_LABELS` and this module's own constants — never from the export —
and every value travels as a parameter. Property *names* travel as parameters too where they
vary: `n[$property]` dynamic access is accepted by 5.26.28 *(verified 2026-08-03 against the
live server: `MATCH (n) … RETURN collect(DISTINCT n[$p])` executes)*, which keeps the four
provenance scans one statement instead of four hand-written ones.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from neo4j import Driver

from graph.core.models import BASE_LABELS, GraphEdge, GraphNode
from graph.core.verification_report import Check, VerificationReport

from .connection import GraphSettings
from .lifecycle import LOAD_MARKER_LABEL, RUN_ID_PROPERTY, LoadVerificationError, resolve_target
from .schema import KEY_PROPERTIES

# -- the vocabulary the checks are stated in ----------------------------------------------------

EXTRACTION_RUN_ID_PROPERTY = "extraction_run_id"
ONTOLOGY_HASH_PROPERTY = "ontology_definition_hash"
PROJECTION_VERSION_PROPERTY = "graph_projection_version"

#: §5.5 puts all four on every node and every edge *(verified 2026-08-03 over
#: `data/graph_runs/graph-v1-886059d862ce/`: 28,836/28,836 nodes and 35,603/35,603 edges carry
#: each, with exactly one distinct value each)*. Criteria 10 and 11 are the same scan asked
#: twice: identity must be single-valued, provenance must equal the export's.
PROVENANCE_PROPERTIES = (
    RUN_ID_PROPERTY,
    EXTRACTION_RUN_ID_PROPERTY,
    ONTOLOGY_HASH_PROPERTY,
    PROJECTION_VERSION_PROPERTY,
)

EVIDENCE_TYPE = "EVIDENCED_BY"
PLACEHOLDER_TYPE = "PLACEHOLDER_FOR"
PARTICIPATION_TYPE = "PARTICIPATES_IN"

WARNED_LABEL = "Warned"
UNRESOLVED_LABEL = "Unresolved"

#: §4.2's per-event key: an unresolved participant's id is its extraction id with the event it
#: was named in appended. The separator is the whole guarantee — two filings that both say "a
#: subsidiary of the Company" produce two nodes, not one merged entity.
EVENT_SCOPE_SEPARATOR = "#evt:"

#: How a relationship-claim edge names itself. The four edges of `relationships.jsonl`
#: (`HOLDS_POSITION_AT` ×3, `BORROWS_UNDER` ×1) carry a `claim_id` under this prefix; no other
#: edge in the export does *(verified 2026-08-03: 4 matches, 0 elsewhere)*. Matched on the
#: claim id rather than on the type list, because the type list is the ontology's and will grow
#: — the prefix is the extraction layer's own statement about which lane produced the row.
RELATIONSHIP_CLAIM_ID_PREFIX = "claim:relationship:"

#: Predicates that say *who an entity is*. §10.7: an unresolved participant is never guessed
#: at, so none of these may ever touch one — not even carrying a claim id, because a filing
#: that says "a subsidiary of the Company" has named a role, not a company, and an edge
#: asserting the ownership would be the merge §4.2 exists to prevent. The corporate-structure
#: family of `ontology/versions/real_estate_marketplace_v1/definitions/relationships.yaml`.
IDENTITY_ASSERTING_TYPES = ("ACQUIRED", "HAS_SUBSIDIARY", "SUBSIDIARY_OF")

#: What an `:Unresolved` node may be attached to at all. `PLACEHOLDER_FOR` is the projection's
#: own "stands in for" edge, `PARTICIPATES_IN` is the role the passage actually stated, and
#: `EVIDENCED_BY` cites — none of the three asserts an identity. Anything else must be a
#: relationship claim (above) and must not be identity-asserting; an allowlist rather than a
#: denylist, because a predicate added to the ontology tomorrow should fail this check rather
#: than pass it by omission.
UNRESOLVED_ALLOWED_TYPES = (PLACEHOLDER_TYPE, PARTICIPATION_TYPE, EVIDENCE_TYPE)

#: The loader's own property, set by `loader.edge_statement`'s
#: `MERGE (s)-[r:T {edge_key: row.edge_key}]->(t)`. It is a *top-level field* of an edge row
#: and never a member of its `properties` map, so criterion 12 would otherwise report it as a
#: property the database invented. This is the relationship-side twin of the `:GraphLoad`
#: exclusion: documented loader-owned metadata, named here rather than silently tolerated.
LOADER_OWNED_RELATIONSHIP_PROPERTIES = ("edge_key",)

#: How many offenders a diagnostic query collects. The report names examples, not populations.
COLLECTED_EXAMPLES = 5

#: §10.6's two dates, which the whole criterion is about keeping apart. Neo4j stores no null, so
#: an event with no `occurred_on` carries no such property at all — 3 of the run's 6 events, and
#: `n.occurred_on` returns null for them, which is exactly the absence the criterion calls for.
EVENT_DATE_PROPERTIES = ("announced_on", "occurred_on")

#: §10.10's issue code, and the five fields the plan names as the refused reading's identity.
CONFLICT_GUARD_CODE = "AMBIGUOUS_COLUMN_ALIGNMENT"

#: The three of those five fields an `:Issue` row actually carries *(verified 2026-08-03 over
#: `data/graph_runs/graph-v1-886059d862ce/`: the 5 `AMBIGUOUS_COLUMN_ALIGNMENT` issues carry
#: `passage_id`, an empty `concept_ids`, a `row_label` and a `detail`, and **no**
#: `subject_entity_id` or `source_lane` at all)*. Matching on the three is not a weakening: the
#: run has exactly one `subject_entity_id` (§7.4) and the refused readings are all
#: `normalized_table`, so a resurrected observation would have to differ in metric, period or
#: passage to escape, and then it would not be the refused reading.
CONFLICT_GUARD_IDENTITY_FIELDS = ("metric_id", "period_key", "passage_id")

#: How a conflict-guard issue names the reading it refused. The metric id and the period key
#: live only inside `detail`, in the extractor's own sentence — *"2 cells of one row resolve to
#: adjusted_ebitda at 2022-01-01_2022-09-30 with different values"*. Parsed rather than read off
#: a property because the projection has no property to read: `concept_ids` is `[]` on all five.
#: The 5th issue's `detail` is a different sentence ("duration groups cannot be assigned to
#: columns uniquely"), names no identity, and correctly yields none — which is why the run has
#: **four** refused identities from five issues, exactly as §10.10 says.
CONFLICT_GUARD_IDENTITY_PATTERN = re.compile(r"resolve to (?P<metric_id>\S+) at (?P<period_key>\S+) ")


# -- statements ---------------------------------------------------------------------------------

#: The same predicate `lifecycle` counts with, from the same constant. See the module docstring.
DATA_NODES = f"MATCH (n) WHERE NOT n:{LOAD_MARKER_LABEL}"

TOTAL_NODES_STATEMENT = f"{DATA_NODES} RETURN count(n) AS total"

TOTAL_RELATIONSHIPS_STATEMENT = "MATCH ()-[r]->() RETURN count(r) AS total"

#: One row per label per node, so base and secondary labels are counted by one scan. A node
#: carrying two base labels is visible here as two rows and is caught by criterion 4's
#: endpoint-shape query as well.
LABEL_COUNTS_STATEMENT = (
    f"{DATA_NODES} UNWIND labels(n) AS label RETURN label AS label, count(*) AS count"
)

TYPE_COUNTS_STATEMENT = "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count"

#: Relationships whose endpoint is not exactly one base-labelled data node. Three failures in
#: one query: an endpoint that is the `:GraphLoad` marker (zero base labels), an endpoint some
#: other process created without a base label, and a node that acquired a second base label.
ENDPOINT_SHAPE_STATEMENT = (
    "MATCH (s)-[r]->(t) "
    "WHERE size([l IN labels(s) WHERE l IN $base_labels]) <> 1 "
    "   OR size([l IN labels(t) WHERE l IN $base_labels]) <> 1 "
    f"RETURN count(r) AS total, collect(r.edge_key)[..{COLLECTED_EXAMPLES}] AS examples"
)

#: Verifying the assumption the relationship queries rest on: lifecycle's marker is isolated.
MARKER_RELATIONSHIPS_STATEMENT = (
    f"MATCH (m:{LOAD_MARKER_LABEL})-[r]-() "
    f"RETURN count(r) AS total, collect(type(r))[..{COLLECTED_EXAMPLES}] AS examples"
)

#: §10.2's second half, verbatim in shape: a fact with no citation.
UNEVIDENCED_STATEMENT = (
    f"MATCH (x) WHERE NOT x:{LOAD_MARKER_LABEL} AND (x:Observation OR x:Event) "
    f"AND NOT (x)-[:{EVIDENCE_TYPE}]->() "
    "RETURN count(x) AS total, "
    f"collect(coalesce(x.observation_id, x.event_id))[..{COLLECTED_EXAMPLES}] AS examples"
)

RELATIONSHIP_CLAIM_EDGES_STATEMENT = (
    "MATCH ()-[r]->() WHERE r.claim_id STARTS WITH $prefix RETURN r.edge_key AS edge_key"
)

UNRESOLVED_ENTITIES_STATEMENT = (
    f"MATCH (u:{UNRESOLVED_LABEL}) WHERE NOT u:{LOAD_MARKER_LABEL} "
    f"RETURN u.{KEY_PROPERTIES['Entity']} AS key, u.resolved AS resolved"
)

#: Undirected on purpose: `(:Company)-[:HAS_SUBSIDIARY]->(:Unresolved)` asserts the placeholder's
#: identity exactly as firmly as the outgoing form §10.7's draft query looked for.
UNRESOLVED_EDGES_STATEMENT = (
    f"MATCH (u:{UNRESOLVED_LABEL})-[r]-() "
    "RETURN type(r) AS type, r.edge_key AS edge_key, r.claim_id AS claim_id"
)

PROVENANCE_NODES_STATEMENT = (
    f"{DATA_NODES} RETURN collect(DISTINCT n[$property]) AS values, "
    "count(n) - count(n[$property]) AS missing"
)

PROVENANCE_RELATIONSHIPS_STATEMENT = (
    "MATCH ()-[r]->() RETURN collect(DISTINCT r[$property]) AS values, "
    "count(r) - count(r[$property]) AS missing"
)

NODE_PROPERTY_KEYS_STATEMENT = f"{DATA_NODES} UNWIND keys(n) AS key RETURN DISTINCT key"

#: One row per relationship, carrying everything it holds. The property *names* are derived from
#: this rather than from a second `UNWIND keys(r)` scan, because the values have to be read
#: anyway for the content digest and 35,603 rows is one scan too many to do twice.
RELATIONSHIP_ROWS_STATEMENT = (
    "MATCH ()-[r]->() RETURN type(r) AS type, r.edge_key AS edge_key, "
    "properties(r) AS properties"
)

#: §10.6. Six rows on the real run, and a null on either side is the fact being checked.
EVENT_DATES_STATEMENT = (
    f"MATCH (e:Event) WHERE NOT e:{LOAD_MARKER_LABEL} "
    f"RETURN e.{KEY_PROPERTIES['Event']} AS key, "
    + ", ".join(f"e.{name} AS {name}" for name in EVENT_DATE_PROPERTIES)
)

#: §10.10. Every observation's refusable identity, read whole rather than filtered by the four
#: refused ones — the comparison belongs to `build_report`, and a query that already knew what
#: it was looking for could not be tested without a server. 2,707 rows on the real run.
OBSERVATION_IDENTITIES_STATEMENT = (
    f"MATCH (o:Observation) WHERE NOT o:{LOAD_MARKER_LABEL} "
    f"RETURN o.{KEY_PROPERTIES['Observation']} AS key, "
    + ", ".join(f"o.{name} AS {name}" for name in CONFLICT_GUARD_IDENTITY_FIELDS)
)


def node_keys_statement(base_label: str) -> str:
    """Every key of one base label, with everything that node holds.

    Built from `BASE_LABELS`, never from the export. It returns `properties` as well as `key`
    because criterion 6 needs the key multiset and value parity needs the content, and one pass
    over 28,836 nodes serves both — the alternative was a second full scan per label.
    """
    if base_label not in BASE_LABELS:
        raise ValueError(f"{base_label!r} is not one of {BASE_LABELS}")
    return (
        f"MATCH (n:{base_label}) WHERE NOT n:{LOAD_MARKER_LABEL} "
        f"RETURN n.{KEY_PROPERTIES[base_label]} AS key, properties(n) AS properties"
    )


def citation_statements(base_label: str) -> tuple[str, str]:
    """(nodes, relationships) that cite a `{passage,document}_id` no such node holds.

    The citing property and the cited node's key property are the same name — §4.1 makes node
    keys the extraction ids, so a `passage_id` on an `:Issue` is literally the `:Passage`'s key
    — which is why one constant serves both sides of the `EXISTS`.
    """
    if base_label not in ("Passage", "Document"):
        raise ValueError(f"{base_label!r} is not a citable node type")
    key = KEY_PROPERTIES[base_label]

    def resolves(citing: str) -> str:
        return f"EXISTS {{ MATCH (t:{base_label}) WHERE t.{key} = {citing}.{key} }}"

    nodes = (
        f"{DATA_NODES} AND NOT n:{base_label} AND n.{key} IS NOT NULL "
        f"AND NOT {resolves('n')} "
        f"RETURN count(n) AS total, collect(n.{key})[..{COLLECTED_EXAMPLES}] AS examples"
    )
    relationships = (
        f"MATCH ()-[r]->() WHERE r.{key} IS NOT NULL AND NOT {resolves('r')} "
        f"RETURN count(r) AS total, collect(r.{key})[..{COLLECTED_EXAMPLES}] AS examples"
    )
    return nodes, relationships


# -- content digests: the one comparison that reads a property's *value* -----------------------

#: How much of the sha256 a content digest keeps. 16 hex characters is 64 bits; over 64,439 rows
#: a collision is around 1 in 10^13, and the digest is read by a human in a failure line.
DIGEST_LENGTH = 16


def content_digest(properties: Mapping[str, Any]) -> str:
    """One property map, reduced to a string that differs whenever any value differs.

    `sort_keys` is what makes it order-independent — the export writes properties in one order
    and `properties(n)` returns them in another — and `separators` removes the whitespace that
    would otherwise be the only thing two runs of `json.dumps` could disagree on.

    **No `default=` fallback**, deliberately: every value in the export is a JSON primitive or a
    homogeneous list of them (`reader.property_fault` refuses anything else before a row is
    loaded) and the driver returns those as the same Python types. A value that is not
    serialisable is therefore a fact about the database worth a `TypeError` rather than a
    `str()` that would quietly make two different values digest the same.
    """
    return hashlib.sha256(
        json.dumps(dict(properties), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:DIGEST_LENGTH]


def node_content(base_label: str, key: str, properties: Mapping[str, Any]) -> str:
    """`Label|key|digest`. The key is in the string so a failing set difference names the row."""
    return f"{base_label}|{key}|{content_digest(properties)}"


def edge_content(relationship_type: str, edge_key: str, properties: Mapping[str, Any]) -> str:
    return f"{relationship_type}|{edge_key}|{content_digest(properties)}"


@dataclass(frozen=True)
class EventDates:
    """One `:Event`'s two dates. `None` is a value here, not a missing measurement (§10.6)."""

    key: str
    announced_on: str | None
    occurred_on: str | None

    def describe(self) -> str:
        return f"{self.key} (announced_on={self.announced_on!r}, occurred_on={self.occurred_on!r})"


@dataclass(frozen=True)
class ObservationIdentity:
    """The fields §10.10 refuses a resurrected reading by. `key` is not part of the identity —
    a projection bug would mint a *new* observation id for the same reading."""

    key: str
    fields: tuple[tuple[str, Any], ...]

    @property
    def identity(self) -> tuple[Any, ...]:
        return tuple(value for _, value in self.fields)

    def describe(self) -> str:
        named = ", ".join(f"{name}={value!r}" for name, value in self.fields)
        return f"{self.key} ({named})"


def refused_identities(nodes: Iterable[GraphNode]) -> tuple[tuple[Any, ...], ...]:
    """§10.10's refused readings, read off the export's own `:Issue` rows.

    Derived rather than declared, like every other expectation in this file: a verifier carrying
    four hard-coded tuples would pass a load of a different run. Four identities on the real run,
    from five conflict-guard issues — see `CONFLICT_GUARD_IDENTITY_PATTERN` on the fifth.
    """
    found: set[tuple[Any, ...]] = set()
    for node in nodes:
        if node.base_label != "Issue" or node.properties.get("code") != CONFLICT_GUARD_CODE:
            continue
        match = CONFLICT_GUARD_IDENTITY_PATTERN.search(str(node.properties.get("detail", "")))
        passage_id = node.properties.get("passage_id")
        if match is None or not passage_id:
            continue
        found.add((match["metric_id"], match["period_key"], str(passage_id)))
    return tuple(sorted(found))


# -- what the export says the database must hold ------------------------------------------------


@dataclass(frozen=True)
class Offenders:
    """A count and a few examples of it. Fewer examples than `count` is normal and intended."""

    count: int = 0
    examples: tuple[str, ...] = ()

    def merged(self, other: "Offenders") -> "Offenders":
        return Offenders(
            count=self.count + other.count,
            examples=(self.examples + other.examples)[:COLLECTED_EXAMPLES],
        )


@dataclass(frozen=True)
class ProvenanceFact:
    """One provenance property, as the database holds it."""

    values: frozenset[str] = frozenset()
    missing_nodes: int = 0
    missing_relationships: int = 0

    @property
    def missing(self) -> int:
        return self.missing_nodes + self.missing_relationships


@dataclass(frozen=True)
class UnresolvedEdge:
    """One relationship incident to an `:Unresolved` node, and what authorised it."""

    type: str
    edge_key: str | None
    claim_id: str | None

    @property
    def is_relationship_claim(self) -> bool:
        return bool(self.claim_id) and self.claim_id.startswith(RELATIONSHIP_CLAIM_ID_PREFIX)

    def refusal(self) -> str | None:
        """Why this edge may not exist, or `None` if it may."""
        if self.type in IDENTITY_ASSERTING_TYPES:
            return f"{self.type} asserts an identity §4.2 refuses to guess"
        if self.type in UNRESOLVED_ALLOWED_TYPES:
            return None
        if self.is_relationship_claim:
            return None
        allowed = "/".join(UNRESOLVED_ALLOWED_TYPES)
        return f"{self.type} is neither {allowed} nor a relationship claim"

    def describe(self) -> str:
        return f"{self.type} ({self.edge_key or '<no edge_key>'})"


@dataclass(frozen=True)
class ExpectedGraph:
    """The export, reduced to everything a check compares against. Derived, never declared."""

    node_count: int
    edge_count: int
    node_counts_by_base_label: Mapping[str, int]
    secondary_label_counts: Mapping[str, int]
    edge_counts_by_type: Mapping[str, int]
    node_keys: Mapping[str, frozenset[str]]
    relationship_claim_edge_keys: frozenset[str]
    endpoint_references: tuple[tuple[str, str], ...]
    provenance: Mapping[str, frozenset[str]]
    node_property_keys: frozenset[str]
    relationship_property_keys: frozenset[str]
    #: `Label|key|digest` per node and `TYPE|edge_key|digest` per relationship — every value the
    #: export carries, reduced to something comparable as a set.
    node_content: frozenset[str]
    relationship_content: frozenset[str]
    #: §10.6, keyed so a missing event is one difference rather than two.
    event_dates: Mapping[str, EventDates]
    #: §10.10's refused `(metric_id, period_key, passage_id)` triples, and the observation keys
    #: the export *does* place on the passages those triples name — the second half matters
    #: because "no observation on that passage at all" would satisfy the first half.
    refused_reading_identities: tuple[tuple[Any, ...], ...]
    observations_on_refused_passages: frozenset[str]

    @property
    def graph_run_id(self) -> str:
        values = sorted(self.provenance.get(RUN_ID_PROPERTY, frozenset()))
        return values[0] if len(values) == 1 else ""

    def keys_of(self, base_label: str) -> frozenset[str]:
        return self.node_keys.get(base_label, frozenset())


def expected_graph(
    nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]
) -> ExpectedGraph:
    """Read the export's own claims about itself. No number in this function is a literal."""
    node_counts: Counter[str] = Counter()
    secondary: Counter[str] = Counter()
    keys: dict[str, set[str]] = {}
    node_properties: set[str] = set()
    provenance: dict[str, set[str]] = {name: set() for name in PROVENANCE_PROPERTIES}
    node_content: set[str] = set()
    event_dates: dict[str, EventDates] = {}

    refused = refused_identities(nodes)
    refused_passages = {identity[-1] for identity in refused}
    on_refused_passages: set[str] = set()

    for node in nodes:
        node_counts[node.base_label] += 1
        for label in node.labels[1:]:
            secondary[label] += 1
        keys.setdefault(node.base_label, set()).add(node.key)
        node_properties.update(node.properties)
        node_content.add(node_content_of(node))
        if node.base_label == "Event":
            event_dates[node.key] = EventDates(
                key=node.key,
                announced_on=node.properties.get("announced_on"),
                occurred_on=node.properties.get("occurred_on"),
            )
        if (
            node.base_label == "Observation"
            and node.properties.get("passage_id") in refused_passages
        ):
            on_refused_passages.add(node.key)
        for name in PROVENANCE_PROPERTIES:
            if name in node.properties:
                provenance[name].add(str(node.properties[name]))

    edge_counts: Counter[str] = Counter()
    relationship_properties: set[str] = set()
    claim_edges: set[str] = set()
    references: set[tuple[str, str]] = set()
    relationship_content: set[str] = set()
    for edge in edges:
        edge_counts[edge.type] += 1
        relationship_properties.update(edge.properties)
        references.add((edge.source_base_label, edge.source_key))
        references.add((edge.target_base_label, edge.target_key))
        relationship_content.add(edge_content_of(edge))
        claim_id = edge.properties.get("claim_id")
        if isinstance(claim_id, str) and claim_id.startswith(RELATIONSHIP_CLAIM_ID_PREFIX):
            claim_edges.add(edge.edge_key)
        for name in PROVENANCE_PROPERTIES:
            if name in edge.properties:
                provenance[name].add(str(edge.properties[name]))

    return ExpectedGraph(
        node_count=len(nodes),
        edge_count=len(edges),
        node_counts_by_base_label=dict(node_counts),
        secondary_label_counts=dict(secondary),
        edge_counts_by_type=dict(edge_counts),
        node_keys={label: frozenset(values) for label, values in keys.items()},
        relationship_claim_edge_keys=frozenset(claim_edges),
        endpoint_references=tuple(sorted(references)),
        provenance={name: frozenset(values) for name, values in provenance.items()},
        node_property_keys=frozenset(node_properties),
        relationship_property_keys=frozenset(relationship_properties),
        node_content=frozenset(node_content),
        relationship_content=frozenset(relationship_content),
        event_dates=event_dates,
        refused_reading_identities=refused,
        observations_on_refused_passages=frozenset(on_refused_passages),
    )


def node_content_of(node: GraphNode) -> str:
    return node_content(node.base_label, node.key, node.properties)


def edge_content_of(edge: GraphEdge) -> str:
    """The export's edge, with the property the *loader* adds.

    `edge_key` is a top-level field of an export row and never a member of its `properties` map,
    but `loader.edge_statement`'s `MERGE` writes it as a property — so the database's digest
    covers it and the expectation has to as well, or every relationship would differ.
    """
    return edge_content(
        edge.type, edge.edge_key, {**dict(edge.properties), "edge_key": edge.edge_key}
    )


# -- what the database holds ---------------------------------------------------------------------


@dataclass(frozen=True)
class DatabaseFacts:
    """One read of the loaded graph. Every field is a measurement, none is a judgement.

    Separated from `build_report` so the twenty-seven checks are a pure function of two values —
    which is what lets every failure mode be produced without a server.
    """

    node_count: int = 0
    relationship_count: int = 0
    label_counts: Mapping[str, int] = field(default_factory=dict)
    type_counts: Mapping[str, int] = field(default_factory=dict)
    #: Per base label, with repeats preserved: a duplicate key is a fact about the database,
    #: and collapsing it into a set here would hide criterion 6's whole subject.
    node_keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    relationship_claim_edge_keys: tuple[str, ...] = ()
    endpoint_shape: Offenders = Offenders()
    marker_relationships: Offenders = Offenders()
    unevidenced: Offenders = Offenders()
    unresolved_entities: tuple[tuple[str, Any], ...] = ()
    unresolved_edges: tuple[UnresolvedEdge, ...] = ()
    unresolved_passage_citations: Offenders = Offenders()
    unresolved_document_citations: Offenders = Offenders()
    provenance: Mapping[str, ProvenanceFact] = field(default_factory=dict)
    node_property_keys: frozenset[str] = frozenset()
    relationship_property_keys: frozenset[str] = frozenset()
    #: `Label|key|digest` / `TYPE|edge_key|digest`, computed by `content_digest` from exactly
    #: what the server returned. A set rather than a mapping: a duplicated key is criterion 6's
    #: subject and would show here as one entry either way.
    node_content: frozenset[str] = frozenset()
    relationship_content: frozenset[str] = frozenset()
    event_dates: tuple[EventDates, ...] = ()
    observation_identities: tuple[ObservationIdentity, ...] = ()


def _rows(
    driver: Driver, settings: GraphSettings, statement: str, parameters: dict | None = None
) -> list:
    target = resolve_target(settings)
    rows, _, _ = driver.execute_query(
        settings.query(statement), parameters or {}, database_=target.database
    )
    return rows


def _offenders(
    driver: Driver, settings: GraphSettings, statement: str, parameters: dict | None = None
) -> Offenders:
    row = _rows(driver, settings, statement, parameters)[0]
    return Offenders(
        count=int(row["total"]),
        examples=tuple(str(value) for value in row["examples"] if value is not None),
    )


def read_database(driver: Driver, settings: GraphSettings) -> DatabaseFacts:
    """Every read the checks need, in one place. Full scans, deliberately.

    Narrowing these to the labels the export declares would make the verification blind to
    exactly the thing it exists to catch — a node class nobody expected. The same argument
    `lifecycle.inspect_database` makes for its two scans.
    """
    label_counts = {
        row["label"]: int(row["count"])
        for row in _rows(driver, settings, LABEL_COUNTS_STATEMENT)
    }
    type_counts = {
        row["type"]: int(row["count"]) for row in _rows(driver, settings, TYPE_COUNTS_STATEMENT)
    }
    node_keys: dict[str, tuple[str, ...]] = {}
    node_digests: set[str] = set()
    for label in BASE_LABELS:
        rows = _rows(driver, settings, node_keys_statement(label))
        node_keys[label] = tuple(str(row["key"]) for row in rows)
        node_digests.update(
            node_content(label, str(row["key"]), row["properties"]) for row in rows
        )

    relationship_property_names: set[str] = set()
    relationship_digests: set[str] = set()
    for row in _rows(driver, settings, RELATIONSHIP_ROWS_STATEMENT):
        properties = dict(row["properties"])
        relationship_property_names.update(properties)
        relationship_digests.add(
            edge_content(str(row["type"]), str(row["edge_key"]), properties)
        )

    provenance: dict[str, ProvenanceFact] = {}
    for name in PROVENANCE_PROPERTIES:
        node_row = _rows(driver, settings, PROVENANCE_NODES_STATEMENT, {"property": name})[0]
        edge_row = _rows(
            driver, settings, PROVENANCE_RELATIONSHIPS_STATEMENT, {"property": name}
        )[0]
        provenance[name] = ProvenanceFact(
            values=frozenset(
                str(value) for value in list(node_row["values"]) + list(edge_row["values"])
            ),
            missing_nodes=int(node_row["missing"]),
            missing_relationships=int(edge_row["missing"]),
        )

    passage_nodes, passage_edges = citation_statements("Passage")
    document_nodes, document_edges = citation_statements("Document")

    return DatabaseFacts(
        node_count=int(_rows(driver, settings, TOTAL_NODES_STATEMENT)[0]["total"]),
        relationship_count=int(
            _rows(driver, settings, TOTAL_RELATIONSHIPS_STATEMENT)[0]["total"]
        ),
        label_counts=label_counts,
        type_counts=type_counts,
        node_keys=node_keys,
        relationship_claim_edge_keys=tuple(
            str(row["edge_key"])
            for row in _rows(
                driver,
                settings,
                RELATIONSHIP_CLAIM_EDGES_STATEMENT,
                {"prefix": RELATIONSHIP_CLAIM_ID_PREFIX},
            )
        ),
        endpoint_shape=_offenders(
            driver, settings, ENDPOINT_SHAPE_STATEMENT, {"base_labels": list(BASE_LABELS)}
        ),
        marker_relationships=_offenders(driver, settings, MARKER_RELATIONSHIPS_STATEMENT),
        unevidenced=_offenders(driver, settings, UNEVIDENCED_STATEMENT),
        unresolved_entities=tuple(
            (str(row["key"]), row["resolved"])
            for row in _rows(driver, settings, UNRESOLVED_ENTITIES_STATEMENT)
        ),
        unresolved_edges=tuple(
            UnresolvedEdge(
                type=str(row["type"]),
                edge_key=row["edge_key"],
                claim_id=row["claim_id"],
            )
            for row in _rows(driver, settings, UNRESOLVED_EDGES_STATEMENT)
        ),
        unresolved_passage_citations=_offenders(driver, settings, passage_nodes).merged(
            _offenders(driver, settings, passage_edges)
        ),
        unresolved_document_citations=_offenders(driver, settings, document_nodes).merged(
            _offenders(driver, settings, document_edges)
        ),
        provenance=provenance,
        node_property_keys=frozenset(
            str(row["key"]) for row in _rows(driver, settings, NODE_PROPERTY_KEYS_STATEMENT)
        ),
        relationship_property_keys=frozenset(relationship_property_names),
        node_content=frozenset(node_digests),
        relationship_content=frozenset(relationship_digests),
        event_dates=tuple(
            EventDates(
                key=str(row["key"]),
                announced_on=row["announced_on"],
                occurred_on=row["occurred_on"],
            )
            for row in _rows(driver, settings, EVENT_DATES_STATEMENT)
        ),
        observation_identities=tuple(
            ObservationIdentity(
                key=str(row["key"]),
                fields=tuple((name, row[name]) for name in CONFLICT_GUARD_IDENTITY_FIELDS),
            )
            for row in _rows(driver, settings, OBSERVATION_IDENTITIES_STATEMENT)
        ),
    )


# -- the checks, as a pure function ---------------------------------------------------------------


def _secondary_label_counts(facts: DatabaseFacts) -> dict[str, int]:
    return {
        label: count for label, count in facts.label_counts.items() if label not in BASE_LABELS
    }


def _duplicates(keys: Iterable[str]) -> tuple[str, ...]:
    counted = Counter(keys)
    return tuple(sorted(key for key, count in counted.items() if count > 1))


def _keys_present_exactly_once(
    name: str, *, expected: frozenset[str], actual: Sequence[str], subject: str
) -> Check:
    """Set equality **and** multiplicity. §5.2's uniqueness constraint should make the second
    impossible; checking it anyway is how a missing constraint becomes visible as a fact rather
    than as a silently overwritten node."""
    repeated = _duplicates(actual)
    check = Check.comparing_sets(
        name, expected=expected, actual=actual, subject=subject
    )
    if not repeated:
        return check
    detail = f"{len(repeated)} key(s) present more than once: {', '.join(repeated[:5])}"
    return check.model_copy(
        update={
            "passed": False,
            "actual": f"{len(actual)} {subject} ({len(set(actual))} distinct)",
            "detail": "; ".join(part for part in (check.detail, detail) if part),
        }
    )


def _provenance_check(
    name: str,
    *,
    property_name: str,
    expected: frozenset[str],
    fact: ProvenanceFact,
    single_valued: bool,
) -> Check:
    check = Check.comparing(
        name,
        expected=sorted(expected),
        actual=sorted(fact.values),
        detail="",
    )
    problems = []
    if not check.passed:
        problems.append(
            f"the database holds {len(fact.values)} distinct {property_name} value(s), "
            f"the export holds {len(expected)}"
        )
    if single_valued and len(fact.values) != 1:
        problems.append(f"{property_name} must have exactly one value across every row")
    if fact.missing:
        problems.append(
            f"{fact.missing_nodes} node(s) and {fact.missing_relationships} relationship(s) "
            f"carry no {property_name}"
        )
    if not problems:
        return check
    return check.model_copy(update={"passed": False, "detail": "; ".join(problems)})


def build_report(expected: ExpectedGraph, facts: DatabaseFacts) -> VerificationReport:
    """The twenty-seven checks, in the order of the docstring's criteria table.

    Pure: no driver, no I/O, no clock."""
    checks: list[Check] = []

    # 1 — totals.
    checks.append(
        Check.comparing(
            "total_node_count", expected=expected.node_count, actual=facts.node_count
        )
    )
    checks.append(
        Check.comparing(
            "total_relationship_count",
            expected=expected.edge_count,
            actual=facts.relationship_count,
        )
    )

    # 2 — counts by label. Base labels are what §5.2's constraints attach to; the secondary
    # ones carry §5.5's validation state and §4.2's placeholder marking, and a load that
    # dropped or doubled one would pass every count check that looked only at base labels.
    checks.append(
        Check.comparing_counts(
            "node_counts_by_base_label",
            expected=dict(expected.node_counts_by_base_label),
            actual={label: facts.label_counts.get(label, 0) for label in BASE_LABELS},
            subject="labels",
        )
    )
    checks.append(
        Check.comparing_counts(
            "node_counts_by_secondary_label",
            expected=dict(expected.secondary_label_counts),
            actual=_secondary_label_counts(facts),
            subject="labels",
        )
    )

    # 3 — counts by relationship type.
    checks.append(
        Check.comparing_counts(
            "relationship_counts_by_type",
            expected=dict(expected.edge_counts_by_type),
            actual=dict(facts.type_counts),
            subject="types",
        )
    )

    # 4 — endpoints. Three ways an edge can have gone wrong, in one check: an endpoint that is
    # not a base-labelled data node, an endpoint the export named that the database does not
    # hold, and a relationship attached to the loader's own marker.
    present = {
        (label, key) for label, keys in facts.node_keys.items() for key in keys
    }
    unresolved_endpoints = tuple(
        f"({label}) {key}"
        for label, key in expected.endpoint_references
        if (label, key) not in present
    )
    dangling = (
        facts.endpoint_shape.count
        + facts.marker_relationships.count
        + len(unresolved_endpoints)
    )
    checks.append(
        Check.forbidding(
            "no_dangling_relationships",
            subject="relationships without two loaded endpoints",
            found=dangling,
            examples=(
                facts.endpoint_shape.examples
                + facts.marker_relationships.examples
                + unresolved_endpoints[:COLLECTED_EXAMPLES]
            ),
        )
    )

    # 5 — §10.2's second half.
    checks.append(
        Check.forbidding(
            "observations_and_events_are_evidenced",
            subject=f"facts with no {EVIDENCE_TYPE}",
            found=facts.unevidenced.count,
            examples=facts.unevidenced.examples,
        )
    )

    # 6 — every key once, for the three families whose duplication would be a silent merge.
    checks.append(
        _keys_present_exactly_once(
            "observation_keys_present_exactly_once",
            expected=expected.keys_of("Observation"),
            actual=facts.node_keys.get("Observation", ()),
            subject="observation keys",
        )
    )
    checks.append(
        _keys_present_exactly_once(
            "event_keys_present_exactly_once",
            expected=expected.keys_of("Event"),
            actual=facts.node_keys.get("Event", ()),
            subject="event keys",
        )
    )
    checks.append(
        _keys_present_exactly_once(
            "relationship_claim_edges_present_exactly_once",
            expected=expected.relationship_claim_edge_keys,
            actual=facts.relationship_claim_edge_keys,
            subject="relationship-claim edges",
        )
    )

    # 7 — everything an issue, an observation or an edge points at is in the graph.
    checks.append(
        Check.comparing_sets(
            "issues_present",
            expected=expected.keys_of("Issue"),
            actual=facts.node_keys.get("Issue", ()),
            subject="issue keys",
        )
    )
    checks.append(
        Check.forbidding(
            "cited_passages_present",
            subject="citations of a passage the graph does not hold",
            found=facts.unresolved_passage_citations.count,
            examples=facts.unresolved_passage_citations.examples,
        )
    )
    checks.append(
        Check.forbidding(
            "cited_documents_present",
            subject="citations of a document the graph does not hold",
            found=facts.unresolved_document_citations.count,
            examples=facts.unresolved_document_citations.examples,
        )
    )

    # 8 — §10.11. Covered arithmetically by `node_counts_by_secondary_label`, and named anyway,
    # because the plan asks for this number by itself and a reader looking for it should not
    # have to read a mapping to find out whether it held.
    checks.append(
        Check.comparing(
            "warned_nodes_match_export",
            expected=expected.secondary_label_counts.get(WARNED_LABEL, 0),
            actual=facts.label_counts.get(WARNED_LABEL, 0),
            detail=f"§5.5's re-derived warning set, as :{WARNED_LABEL}",
        )
    )

    # 9 — §4.2 and §10.7.
    unscoped = tuple(
        key for key, _ in facts.unresolved_entities if EVENT_SCOPE_SEPARATOR not in key
    )
    claimed_resolved = tuple(
        key for key, resolved in facts.unresolved_entities if resolved is not False
    )
    scope_examples = unscoped + tuple(f"{key} (resolved is not false)" for key in claimed_resolved)
    checks.append(
        Check.forbidding(
            "unresolved_entities_are_scoped_per_event",
            subject=(
                f"unresolved entities without a {EVENT_SCOPE_SEPARATOR!r} scope or "
                "without resolved: false"
            ),
            found=len(unscoped) + len(claimed_resolved),
            examples=scope_examples[:COLLECTED_EXAMPLES],
            detail=(
                f"{len(facts.unresolved_entities)} unresolved entit(ies), each keyed per event"
            ),
        )
    )
    refused = tuple(
        f"{edge.describe()}: {refusal}"
        for edge in facts.unresolved_edges
        if (refusal := edge.refusal()) is not None
    )
    checks.append(
        Check.forbidding(
            "unresolved_entities_assert_no_identity",
            subject="identity-asserting edges on an unresolved entity",
            found=len(refused),
            examples=refused[:COLLECTED_EXAMPLES],
            detail=(
                f"allowed: {', '.join(UNRESOLVED_ALLOWED_TYPES)} and a relationship claim; "
                f"never: {', '.join(IDENTITY_ASSERTING_TYPES)}"
            ),
        )
    )

    # 10 and 11 — the four provenance properties, two of them required to be single-valued.
    for name, property_name, single in (
        ("graph_run_id_is_consistent", RUN_ID_PROPERTY, True),
        ("extraction_run_id_is_consistent", EXTRACTION_RUN_ID_PROPERTY, True),
        ("ontology_definition_hash_matches_export", ONTOLOGY_HASH_PROPERTY, False),
        ("graph_projection_version_matches_export", PROJECTION_VERSION_PROPERTY, False),
    ):
        checks.append(
            _provenance_check(
                name,
                property_name=property_name,
                expected=expected.provenance.get(property_name, frozenset()),
                fact=facts.provenance.get(property_name, ProvenanceFact()),
                single_valued=single,
            )
        )

    # 12 — property parity, both directions. The `:GraphLoad` node is excluded by every node
    # statement above; `edge_key` is the loader's own and is added to the expectation rather
    # than filtered out of the measurement, so it is named in the report either way.
    checks.append(
        Check.comparing_sets(
            "node_property_keys_match_export",
            expected=expected.node_property_keys,
            actual=facts.node_property_keys,
            subject="node property keys",
            detail=f"the :{LOAD_MARKER_LABEL} node is excluded from the measurement",
        )
    )
    checks.append(
        Check.comparing_sets(
            "relationship_property_keys_match_export",
            expected=expected.relationship_property_keys
            | frozenset(LOADER_OWNED_RELATIONSHIP_PROPERTIES),
            actual=facts.relationship_property_keys,
            subject="relationship property keys",
            detail=(
                f"loader-owned: {', '.join(LOADER_OWNED_RELATIONSHIP_PROPERTIES)} "
                "(loader.edge_statement's MERGE key)"
            ),
        )
    )

    # 12 (continued) — property *values*, which no check above reads. `SET n += row.properties`
    # writing the wrong number, or dropping one property from one row, is invisible to a union
    # of key names: `node_property_keys_match_export` passes as long as *some* row still carries
    # the key. The digest is per row, so the row that differs is named.
    checks.append(
        Check.comparing_sets(
            "node_property_values_match_export",
            expected=expected.node_content,
            actual=facts.node_content,
            subject="node content digests",
            detail=f"Label|key|sha256[:{DIGEST_LENGTH}] of every property the row carries",
        )
    )
    checks.append(
        Check.comparing_sets(
            "relationship_property_values_match_export",
            expected=expected.relationship_content,
            actual=facts.relationship_content,
            subject="relationship content digests",
            detail=(
                f"TYPE|edge_key|sha256[:{DIGEST_LENGTH}], with the loader's own "
                f"{', '.join(LOADER_OWNED_RELATIONSHIP_PROPERTIES)} included on both sides"
            ),
        )
    )

    # §10.6 — announcement is not occurrence. Covered arithmetically by the digest above and
    # named anyway, because the criterion asks for this pair by itself and a date collapsed into
    # its twin should read as "the two dates were merged", not as "a node's content differs".
    measured_dates = {dates.key: dates for dates in facts.event_dates}
    date_faults = tuple(
        f"{expected_dates.describe()} -> found "
        f"{measured_dates[key].describe() if key in measured_dates else '<no such event>'}"
        for key, expected_dates in sorted(expected.event_dates.items())
        if measured_dates.get(key) != expected_dates
    )
    checks.append(
        Check.forbidding(
            "event_dates_preserved_independently",
            subject="events whose announced_on/occurred_on pair differs from the export",
            found=len(date_faults),
            examples=date_faults[:COLLECTED_EXAMPLES],
            detail=(
                f"{len(expected.event_dates)} event(s); a null on either side is part of the "
                "comparison, because §5.5's whole point is that an announcement is not an "
                "occurrence"
            ),
        )
    )

    # §10.10 — no refused reading is resurrected, and nothing legitimate was suppressed to
    # achieve it. Both halves, because a projection that emitted no observation at all on the
    # refused passages would satisfy the first half perfectly.
    refused = set(expected.refused_reading_identities)
    resurrected = tuple(
        identity.describe()
        for identity in facts.observation_identities
        if identity.identity in refused
    )
    checks.append(
        Check.forbidding(
            "refused_readings_are_not_resurrected",
            subject=f"observations carrying a {CONFLICT_GUARD_CODE} identity the extractor refused",
            found=len(resurrected),
            examples=resurrected[:COLLECTED_EXAMPLES],
            detail=(
                f"{len(refused)} refused "
                f"({', '.join(CONFLICT_GUARD_IDENTITY_FIELDS)}) identit(ies) read off the "
                f"export's own :Issue rows"
            ),
        )
    )
    checks.append(
        Check.comparing_sets(
            "observations_on_refused_passages_are_present",
            expected=expected.observations_on_refused_passages,
            actual={
                identity.key
                for identity in facts.observation_identities
                if identity.fields[-1][1] in {triple[-1] for triple in refused}
            },
            subject="observations on a passage a conflict guard fired on",
            detail=(
                "§10.10's converse: a refusal is per reading, so the readings the extractor did "
                "accept on that passage must all be there"
            ),
        )
    )

    return VerificationReport(
        graph_run_id=expected.graph_run_id,
        node_count=facts.node_count,
        relationship_count=facts.relationship_count,
        expected_node_count=expected.node_count,
        expected_relationship_count=expected.edge_count,
        checks=tuple(checks),
    )


# -- the two composed -----------------------------------------------------------------------------


def verify_graph(
    driver: Driver,
    settings: GraphSettings,
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
) -> VerificationReport:
    """Read the database and reconcile it against the export it claims to hold.

    Returns a report rather than raising: a verification that stopped at the first failure
    would report one of §10's criteria and hide the other eleven, and the interesting case —
    a load that went wrong in two ways — is exactly the one a first-failure exception loses.
    `raise_for_failures` is the caller's choice, taken after reading.
    """
    return build_report(expected_graph(nodes, edges), read_database(driver, settings))


def raise_for_failures(report: VerificationReport) -> VerificationReport:
    """`LoadVerificationError` if anything failed — lifecycle's own type, so a caller wiring
    this in before `complete_load` fails a load the way lifecycle already fails one."""
    if report.passed:
        return report
    named = "; ".join(
        f"{check.name}: expected {check.expected}, found {check.actual}"
        f"{' (' + check.detail + ')' if check.detail else ''}"
        for check in report.failures
    )
    raise LoadVerificationError(
        f"post-load verification failed for {report.graph_run_id!r}: "
        f"{len(report.failures)} of {len(report.checks)} checks — {named}"
    )


__all__ = [
    "COLLECTED_EXAMPLES",
    "CONFLICT_GUARD_CODE",
    "CONFLICT_GUARD_IDENTITY_FIELDS",
    "CONFLICT_GUARD_IDENTITY_PATTERN",
    "DATA_NODES",
    "DIGEST_LENGTH",
    "DatabaseFacts",
    "EVENT_DATES_STATEMENT",
    "EVENT_DATE_PROPERTIES",
    "EventDates",
    "ENDPOINT_SHAPE_STATEMENT",
    "EVENT_SCOPE_SEPARATOR",
    "EVIDENCE_TYPE",
    "EXTRACTION_RUN_ID_PROPERTY",
    "ExpectedGraph",
    "IDENTITY_ASSERTING_TYPES",
    "LABEL_COUNTS_STATEMENT",
    "LOADER_OWNED_RELATIONSHIP_PROPERTIES",
    "MARKER_RELATIONSHIPS_STATEMENT",
    "NODE_PROPERTY_KEYS_STATEMENT",
    "ONTOLOGY_HASH_PROPERTY",
    "OBSERVATION_IDENTITIES_STATEMENT",
    "ObservationIdentity",
    "Offenders",
    "PARTICIPATION_TYPE",
    "PLACEHOLDER_TYPE",
    "PROJECTION_VERSION_PROPERTY",
    "PROVENANCE_PROPERTIES",
    "ProvenanceFact",
    "RELATIONSHIP_CLAIM_ID_PREFIX",
    "RELATIONSHIP_ROWS_STATEMENT",
    "TOTAL_NODES_STATEMENT",
    "TOTAL_RELATIONSHIPS_STATEMENT",
    "TYPE_COUNTS_STATEMENT",
    "UNEVIDENCED_STATEMENT",
    "UNRESOLVED_ALLOWED_TYPES",
    "UNRESOLVED_EDGES_STATEMENT",
    "UNRESOLVED_ENTITIES_STATEMENT",
    "UNRESOLVED_LABEL",
    "UnresolvedEdge",
    "WARNED_LABEL",
    "build_report",
    "content_digest",
    "citation_statements",
    "edge_content",
    "expected_graph",
    "node_content",
    "node_keys_statement",
    "raise_for_failures",
    "read_database",
    "refused_identities",
    "verify_graph",
]
