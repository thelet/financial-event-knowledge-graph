"""Bounded, deterministic graph projections for the demo UI, as nodes and edges.

Responsibility: five Cypher constants and the pure mapping from their rows to a
`{nodes: [...], edges: [...]}` payload the canvas renderer can draw without a second thought.
It owns no HTTP, no server state, no trace emission and **no driver** — every read goes through
an injected `story.contracts.ReadQueryExecutor`, so the whole module is exercised offline
against `tests/story/conftest.py`'s `RecordedReadExecutor`.

**Why the queries live here and not behind `BoundedGraphRetriever`.** §9's nine retrieval tools
return flat rows of named scalars and nothing else: `test_no_cypher_statement_returns_a_whole_node`
forbids a whole node and `plain_value` refuses a `neo4j.graph.Entity` outright, so no §9 tool can
return an edge. A graph view cannot be assembled through that surface. The queries are therefore
this module's own — and they are deliberately placed **inside `story/`**, where
`tests/story/test_story_retrieval_cypher.py` walks `story/**/*.py` and applies every §16 rule to
them: fixed at import time, explicit `LIMIT`, named return fields, no variable-length path, no
APOC, allowlisted labels and relationship types, no write clause. Putting them outside `story/`
would have removed the demo's Cypher from §16's primary control by choice of directory, which is
the quiet erosion the scan exists to prevent.

**Two consequences of staying inside the scan, and both are visible in the payload.**

* **`:Entity` is not an allowlisted label and `OBSERVATION_OF_SUBJECT` is banned outright**, so
  the company node is **not read from the graph**. It is synthesised in Python from
  `Observation.subject_entity_id`, which all 2,704 observations carry *(verified live
  2026-08-04)*. Its `id` is nonetheless a real graph node id — `opendoor` is the `entity_id` of
  an `(:Entity:PublicCompany:Company)` node *(verified live 2026-08-04, out of band)* — but its
  display text is the id, because reading the node's own `entity_text` is exactly what the scan
  forbids. The node carries `synthesised: true` and `read_from_graph: false`, and the UI is
  required to label it as derived. This is the same honesty the package's existing
  `subject_identity_not_read_from_graph` warning already states on every run.
* **Every other node and edge here is traversed for real.** `HAS_OBSERVATION`, `EVIDENCED_BY`
  and `PART_OF` are all allowlisted, so the projections walk them rather than re-deriving them
  from `Observation.metric_id`/`passage_id`/`document_id`. Those foreign-key properties are
  carried as *fields* on the observation node, so the client can cross-link between views
  without a second read, and they are marked `derived_from` where they appear.

**What is synthesised, and it is marked in the payload rather than explained here only.**

    company node          from `Observation.subject_entity_id`         synthesised: true
    about_subject edge    from `Observation.subject_entity_id`         synthesised: true
    period node           from `Observation.period_key`; no `:Period`  synthesised: true
                          label exists in this graph
    covers_period edge    an aggregate the coverage query computed     synthesised: true
    cites_passage edge    metric to passage across an observation the  synthesised: true
                          backbone query aggregated away

A synthesised edge is this module's inference from a property, not the graph's own assertion.
The property joins are faithful — they reproduce the relationship counts — but a reader of the
payload must be able to tell the two apart without reading this file, which is what the flag is
for.

**Measured live against `graph-v1-0483dc6b4b10`, 2026-08-04**, three runs each, md5 of the row
list identical across runs for all five statements:

    projection        rows   nodes   edges   db hits   wall (warm)
    overview           308     403     976    27,933      ~18 ms
    coverage           546     101     554    21,778      ~19 ms
    evidence backbone  691     215     841    90,035      ~22 ms
    subgraph story      11      14      22       470      ~1.2 ms
    subgraph evidence   11      25      40       864      ~1.1 ms

`evidence backbone` is the one that grows with the corpus: it expands all 2,704 observations
before aggregating them away, which is where its 90,035 db accesses come from. The other two
overview-scale statements are bounded by the metric count and by the sample size.

**Determinism technique, used by all five statements.** Every `ORDER BY` ends in a key unique
within its result — an `observation_id`, a `passage_id`, a `first_observation_id`. Where a
representative is chosen, the `WITH … ORDER BY` runs *before* the aggregation, because
`collect()` consumes rows in receipt order and that is the only thing that makes `[..$n]` and
`collect(…)[0]` reproducible. `content_digest` on every payload is the check: build twice,
compare.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from story.contracts import ReadQueryExecutor
from story.stages.freshness.loaded_graph import LOAD_MARKERS, LoadMarkerRow

# ---------------------------------------------------------------------------------------
# Bounds. Constructor-owned, never caller-owned, for the reason `graph_tools.py` states: a
# caller that can choose its own bound has no bound.
# ---------------------------------------------------------------------------------------

#: Until `config/story.yaml` exists (S11). The slowest statement here is 22 ms warm, so this is
#: a ceiling rather than a budget. It may never be zero: `Neo4jReadExecutor` refuses a
#: non-positive value because the server reads `0` as *no timeout* (finding F10).
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Row bounds per projection, each about four times the measured row count so the bound is a
#: ceiling that has never bitten rather than a cap that silently shapes the picture. Every
#: statement is asked for `row_limit + 1` and reports `truncated` when the extra row arrives —
#: `len(rows) == row_limit` cannot tell a full page from a bound that bit, and the extra row can.
OVERVIEW_ROW_LIMIT = 1200
COVERAGE_ROW_LIMIT = 1500
BACKBONE_ROW_LIMIT = 1500
SUBGRAPH_ROW_LIMIT = 400

#: §4's "a deterministic sample of observations" per metric. Bound rather than literal, so the
#: number the UI reports and the number the database applies are the same object.
OBSERVATIONS_PER_METRIC = 20

MAX_ROW_LIMIT = 5000
MAX_OBSERVATIONS_PER_METRIC = 50
MAX_SUBGRAPH_METRIC_IDS = 25
MAX_SUBGRAPH_PERIOD_KEYS = 25

#: The whole graph, for the disclosure. Read from the `:GraphLoad` marker rather than pinned,
#: so a reloaded database moves the number instead of making this file wrong. The marker counts
#: every node but itself, so the database holds one more than it reports.
LOAD_MARKER_IS_ITSELF_UNCOUNTED = 1

# ---------------------------------------------------------------------------------------
# The vocabulary the frontend renders. Lower-case and distinct from the graph's own label and
# relationship-type spellings on purpose: a payload type is this module's rendering category,
# and `graph_label` / `graph_relationship_type` on the node and the edge say what it came from.
# ---------------------------------------------------------------------------------------

NODE_COMPANY = "company"
NODE_METRIC = "metric"
NODE_OBSERVATION = "observation"
NODE_PASSAGE = "passage"
NODE_DOCUMENT = "document"
NODE_PERIOD = "period"

EDGE_HAS_OBSERVATION = "has_observation"
EDGE_EVIDENCED_BY = "evidenced_by"
EDGE_PART_OF = "part_of"
EDGE_ABOUT_SUBJECT = "about_subject"
EDGE_COVERS_PERIOD = "covers_period"
EDGE_CITES_PASSAGE = "cites_passage"

PROJECTION_OVERVIEW = "overview"
PROJECTION_COVERAGE = "coverage"
PROJECTION_EVIDENCE_BACKBONE = "evidence_backbone"
PROJECTION_NAMES = (PROJECTION_OVERVIEW, PROJECTION_COVERAGE, PROJECTION_EVIDENCE_BACKBONE)

VIEW_STORY = "story"
VIEW_EVIDENCE = "evidence"
SUBGRAPH_VIEWS = (VIEW_STORY, VIEW_EVIDENCE)

#: §7's honest label, rendered verbatim in the header. Deliberately says what is *not* shown.
DISCLOSURE = (
    "A bounded visual projection of the loaded graph, not the whole graph. Nodes and edges "
    "here are chosen by one deterministic query under an explicit row bound and an explicit "
    "timeout; the snapshot counts report what the whole database holds."
)

#: Why the company node is a different kind of thing from every other node in the payload.
COMPANY_DISCLOSURE = (
    "The company node is derived, not read. Its identity comes from the subject_entity_id "
    "property every observation carries; the entity node itself is outside the label "
    "allowlist the read-only scan enforces, so its own text was never fetched."
)

#: Which property a synthesised element was inferred from. Free text nowhere: these three
#: strings are what the payload carries, so a client can group by them.
DERIVED_FROM_SUBJECT = "observation.subject_entity_id"
DERIVED_FROM_PERIOD = "observation.period_key"
DERIVED_FROM_AGGREGATE = "aggregate over HAS_OBSERVATION"
DERIVED_FROM_EVIDENCE_AGGREGATE = "aggregate over HAS_OBSERVATION and EVIDENCED_BY"
DERIVED_FROM_OBSERVATION_KEYS = "observation.passage_id, observation.document_id"

# ---------------------------------------------------------------------------------------
# The five statements. Plain constants, no interpolation, one `ast.Constant` each — the same
# shape `story/stages/retrieval/cypher.py` keeps, for the same reason: this is the file a
# reviewer reads to check what the demo asks the database.
# ---------------------------------------------------------------------------------------

#: Projection C, the demo default. Deterministic top-`$observations_per_metric` observations per
#: metric with their evidence spine: metric, observation, passage, document.
#:
#: **The `WITH … ORDER BY` before the `collect` is the whole determinism argument.** `collect()`
#: consumes rows in receipt order, so `[..$observations_per_metric]` is reproducible only
#: because the rows arrive sorted by `coalesce(period_end, instant_date) DESC` and then by
#: `observation_id`, which is unique. Reverse those two clauses and the same query returns a
#: different twenty every plan change.
#:
#: **The second `ORDER BY passage.passage_id` before the second aggregation is there for a case
#: that does not currently arise and would be silent if it did.** The graph holds 2,710
#: `EVIDENCED_BY` edges over 2,704 observations, so an observation *can* carry two; none of the
#: 308 sampled here does *(verified live 2026-08-04)*. `collect(...)[0]` on the ordered stream
#: picks the same passage and the document *of that same row* every time; a pair of independent
#: `min()` calls could pick a passage from one edge and a document from another, which is the
#: kind of wrong that never fails a test.
#:
#: A metric with no observations does not appear: the `MATCH` is not optional, and 17 of the 26
#: metrics survive it. `coverage` below is where the other nine are visible, which is why the
#: two projections both exist.
OVERVIEW_STATEMENT = """
MATCH (metric:Metric)-[:HAS_OBSERVATION]->(observation:Observation)
WITH metric, observation
ORDER BY coalesce(observation.period_end, observation.instant_date) DESC,
         observation.observation_id
WITH metric, collect(observation)[..$observations_per_metric] AS recent
UNWIND recent AS observation
OPTIONAL MATCH (observation)-[:EVIDENCED_BY]->(passage:Passage)-[:PART_OF]->(document:Document)
WITH metric, observation, passage, document
ORDER BY passage.passage_id
WITH metric, observation,
     collect(passage.passage_id)[0] AS passage_id,
     collect(passage.passage_kind)[0] AS passage_kind,
     collect(passage.char_count)[0] AS passage_char_count,
     collect(document.document_id)[0] AS document_id,
     collect(document.form)[0] AS document_form,
     collect(document.filing_date)[0] AS document_filing_date,
     count(passage) AS evidence_edge_count
RETURN metric.metric_id AS metric_id,
       metric.label AS metric_label,
       metric.metric_category AS metric_category,
       metric.unit AS metric_unit,
       metric.gaap_status AS metric_gaap_status,
       observation.observation_id AS observation_id,
       observation.period_key AS period_key,
       observation.value AS value,
       observation.unit AS observation_unit,
       observation.scale AS scale,
       observation.period_start AS period_start,
       observation.period_end AS period_end,
       observation.instant_date AS instant_date,
       observation.subject_entity_id AS subject_entity_id,
       observation.validation_state AS validation_state,
       observation.source_lane AS source_lane,
       passage_id,
       passage_kind,
       passage_char_count,
       document_id,
       document_form,
       document_filing_date,
       evidence_edge_count
ORDER BY metric.metric_id, observation.observation_id
LIMIT $row_limit
"""

#: Projection A, the coverage lattice. One row per metric and period, observations aggregated
#: away, and **the nine metrics with no observation at all kept as rows with a null period** —
#: `revenue` is one of them, which is a real and interesting fact about this corpus rather than
#: a gap to hide. That is what the `OPTIONAL MATCH` buys and it is the only reason it is here.
#:
#: `min(coalesce(period_end, instant_date))` gives each period a sortable anchor without a
#: second statement; `min(observation_id)` is the unique last sort key.
COVERAGE_STATEMENT = """
MATCH (metric:Metric)
OPTIONAL MATCH (metric)-[:HAS_OBSERVATION]->(observation:Observation)
WITH metric, observation.period_key AS period_key, observation
ORDER BY observation.observation_id
WITH metric, period_key,
     count(observation) AS observation_count,
     min(observation.observation_id) AS first_observation_id,
     min(coalesce(observation.period_end, observation.instant_date)) AS period_anchor,
     collect(observation.subject_entity_id)[0] AS subject_entity_id
RETURN metric.metric_id AS metric_id,
       metric.label AS metric_label,
       metric.metric_category AS metric_category,
       metric.unit AS metric_unit,
       metric.gaap_status AS metric_gaap_status,
       period_key,
       observation_count,
       first_observation_id,
       period_anchor,
       subject_entity_id
ORDER BY metric.metric_id, period_key, first_observation_id
LIMIT $row_limit
"""

#: Projection B, the evidence backbone: which metric is cited by which passage, and which
#: document that passage belongs to.
#:
#: **This is the expensive one and the only one that grows with the corpus.** It expands all
#: 2,704 observations and both of their hops before the aggregation discards them — 90,035 db
#: accesses for 691 rows *(profiled live 2026-08-04)*, against 27,933 for the overview. Adding a
#: second filer multiplies it. It stays because "which documents actually support this metric"
#: is not answerable any other way, and the row bound holds the result size flat even when the
#: work does not.
EVIDENCE_BACKBONE_STATEMENT = """
MATCH (metric:Metric)-[:HAS_OBSERVATION]->(observation:Observation)
MATCH (observation)-[:EVIDENCED_BY]->(passage:Passage)-[:PART_OF]->(document:Document)
WITH metric, passage, document,
     count(observation) AS cited_observation_count,
     min(observation.observation_id) AS first_observation_id
RETURN metric.metric_id AS metric_id,
       metric.label AS metric_label,
       metric.metric_category AS metric_category,
       passage.passage_id AS passage_id,
       passage.passage_kind AS passage_kind,
       passage.char_count AS passage_char_count,
       document.document_id AS document_id,
       document.form AS document_form,
       document.filing_date AS document_filing_date,
       document.title AS document_title,
       cited_observation_count,
       first_observation_id
ORDER BY metric.metric_id, passage.passage_id, first_observation_id
LIMIT $row_limit
"""

#: The focused neighbourhood, `view=story`: the candidate's metrics, their observations for the
#: candidate's periods, and the derived company. `$metric_ids` and `$period_keys` are bound
#: lists — the client names a projection and passes parameters, never Cypher (§4).
#:
#: `observation.passage_id` and `observation.document_id` are returned as *fields*, not walked.
#: They are how the UI offers "show me the evidence for this" without a read, and the evidence
#: view below is what answers it from the relationships themselves.
SUBGRAPH_STORY_STATEMENT = """
MATCH (metric:Metric)-[:HAS_OBSERVATION]->(observation:Observation)
WHERE metric.metric_id IN $metric_ids
  AND observation.period_key IN $period_keys
RETURN metric.metric_id AS metric_id,
       metric.label AS metric_label,
       metric.metric_category AS metric_category,
       metric.unit AS metric_unit,
       metric.gaap_status AS metric_gaap_status,
       observation.observation_id AS observation_id,
       observation.period_key AS period_key,
       observation.value AS value,
       observation.unit AS observation_unit,
       observation.scale AS scale,
       observation.period_start AS period_start,
       observation.period_end AS period_end,
       observation.instant_date AS instant_date,
       observation.subject_entity_id AS subject_entity_id,
       observation.validation_state AS validation_state,
       observation.source_lane AS source_lane,
       observation.passage_id AS observation_passage_id,
       observation.document_id AS observation_document_id
ORDER BY metric.metric_id, observation.observation_id
LIMIT $row_limit
"""

#: The focused neighbourhood, `view=evidence`: the same facts, plus the passages that evidence
#: them and the documents those passages are part of.
#:
#: **`quoted_text` is a property of the `EVIDENCED_BY` edge, not of the passage** (correction C3
#: in `cypher.py`, verified on 2,710 of 2,710 edges). The relationship is bound as `evidence` and
#: the quote is returned from it; a query that read only node fields would lose every citation
#: quote and still look complete. `passage.passage_kind` is returned beside it so the two are
#: not confused.
#:
#: A non-optional second `MATCH`: an observation with no evidence has nothing to show in an
#: evidence view, and the story view above is where it is still visible.
SUBGRAPH_EVIDENCE_STATEMENT = """
MATCH (metric:Metric)-[:HAS_OBSERVATION]->(observation:Observation)
WHERE metric.metric_id IN $metric_ids
  AND observation.period_key IN $period_keys
MATCH (observation)-[evidence:EVIDENCED_BY]->(passage:Passage)-[:PART_OF]->(document:Document)
RETURN metric.metric_id AS metric_id,
       metric.label AS metric_label,
       metric.metric_category AS metric_category,
       metric.unit AS metric_unit,
       metric.gaap_status AS metric_gaap_status,
       observation.observation_id AS observation_id,
       observation.period_key AS period_key,
       observation.value AS value,
       observation.unit AS observation_unit,
       observation.subject_entity_id AS subject_entity_id,
       observation.validation_state AS validation_state,
       evidence.quoted_text AS quoted_text,
       evidence.table_id AS evidence_table_id,
       passage.passage_id AS passage_id,
       passage.passage_kind AS passage_kind,
       passage.char_count AS passage_char_count,
       document.document_id AS document_id,
       document.form AS document_form,
       document.filing_date AS document_filing_date,
       document.title AS document_title
ORDER BY metric.metric_id, observation.observation_id, passage.passage_id
LIMIT $row_limit
"""

#: Every statement this module owns, in one place, so a test can assert the read set rather than
#: read the source of five functions. `LOAD_MARKERS` is imported from the freshness stage and
#: not restated: the snapshot identity has one owner already.
PROJECTION_STATEMENTS = (
    OVERVIEW_STATEMENT,
    COVERAGE_STATEMENT,
    EVIDENCE_BACKBONE_STATEMENT,
    SUBGRAPH_STORY_STATEMENT,
    SUBGRAPH_EVIDENCE_STATEMENT,
)


class ProjectionError(RuntimeError):
    """A projection could not be asked for: a bad bound, a bad view, or an empty selection.

    Raised where the request is *built*, never as a result of one. A database that is down is
    the executor's error to raise and the server's to render; this type means the demo asked for
    something that is not a projection.
    """


# ---------------------------------------------------------------------------------------
# The accumulator. Dedupes by id, preserves first-seen order, and is the reason two builds of
# the same projection produce byte-identical payloads.
# ---------------------------------------------------------------------------------------


@dataclass
class _Accumulator:
    """Nodes and edges keyed by id, in insertion order.

    **First write wins, and that is a determinism decision rather than a shortcut.** A metric
    appears on twenty rows of the overview carrying identical properties; merging them would be
    the same work with an ordering-dependent result the day two rows disagree. Insertion order
    is a function of the statement's `ORDER BY`, which is total, so the node list is too.
    """

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def node(
        self,
        node_id: Any,
        node_type: str,
        label: Any,
        *,
        graph_label: str | None = None,
        synthesised: bool = False,
        derived_from: str | None = None,
        **fields: Any,
    ) -> str | None:
        """Add a node and return its id, or `None` when the row carried no id.

        A missing id is an ordinary state, not a fault: the coverage statement returns a null
        `period_key` for the nine metrics with no observations, and an observation with no
        evidence returns a null `passage_id`. Returning `None` lets the caller skip the edge
        without a branch on the row.
        """
        if node_id is None or node_id == "":
            return None
        key = str(node_id)
        if key not in self.nodes:
            payload: dict[str, Any] = {
                "id": key,
                "type": node_type,
                "label": str(label) if label is not None else key,
                "synthesised": synthesised,
                "read_from_graph": not synthesised,
            }
            if graph_label is not None:
                payload["graph_label"] = graph_label
            if derived_from is not None:
                payload["derived_from"] = derived_from
            payload.update({name: value for name, value in fields.items() if value is not None})
            self.nodes[key] = payload
        return key

    def edge(
        self,
        source: str | None,
        target: str | None,
        edge_type: str,
        *,
        synthesised: bool,
        graph_relationship_type: str | None = None,
        derived_from: str | None = None,
        **fields: Any,
    ) -> None:
        """Add an edge unless either endpoint is absent. Ids are readable, not random.

        `has_observation:<metric>->|<observation>` is legible in a browser inspector and stable
        across runs, which is the same argument the pipeline makes for every other id it mints.
        The separator is two characters no id in this graph contains.
        """
        if source is None or target is None:
            return
        key = f"{edge_type}:{source}->|{target}"
        if key in self.edges:
            return
        payload: dict[str, Any] = {
            "id": key,
            "source": source,
            "target": target,
            "type": edge_type,
            "synthesised": synthesised,
            "read_from_graph": not synthesised,
        }
        if graph_relationship_type is not None:
            payload["graph_relationship_type"] = graph_relationship_type
        if derived_from is not None:
            payload["derived_from"] = derived_from
        payload.update({name: value for name, value in fields.items() if value is not None})
        self.edges[key] = payload

    def node_list(self) -> list[dict[str, Any]]:
        return list(self.nodes.values())

    def edge_list(self) -> list[dict[str, Any]]:
        return list(self.edges.values())


@dataclass(frozen=True)
class _Read:
    """One statement's rows, its bound, and whether the bound bit."""

    rows: tuple[dict[str, Any], ...]
    row_limit: int
    truncated: bool
    elapsed_ms: float
    timeout_seconds: float


def _read(
    executor: ReadQueryExecutor,
    statement: str,
    parameters: Mapping[str, Any],
    *,
    row_limit: int,
    timeout_seconds: float,
) -> _Read:
    """One bounded read, asked for `row_limit + 1` rows so truncation is observed, not guessed.

    §16's timeout is explicit here and has no default reachable from a caller that forgot one:
    every public builder takes it keyword-only and passes it down. A non-positive value is
    refused before the driver sees it, because `Neo4jReadExecutor` would refuse it anyway and a
    refusal from this layer names the projection.
    """
    if not isinstance(row_limit, int) or isinstance(row_limit, bool):
        raise ProjectionError(f"row_limit must be an integer, got {row_limit!r}")
    if not 1 <= row_limit <= MAX_ROW_LIMIT:
        raise ProjectionError(
            f"row_limit must be between 1 and {MAX_ROW_LIMIT}, got {row_limit}")
    if timeout_seconds <= 0:
        raise ProjectionError(
            f"timeout_seconds must be positive, got {timeout_seconds!r}; the server reads zero "
            "as no timeout at all")
    bound = dict(parameters)
    bound["row_limit"] = row_limit + 1
    started = time.perf_counter()
    rows = executor.read(statement, bound, timeout_seconds=timeout_seconds)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return _Read(
        rows=tuple(rows[:row_limit]),
        row_limit=row_limit,
        truncated=len(rows) > row_limit,
        elapsed_ms=elapsed_ms,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------------------
# The snapshot identity, from the load marker the freshness gate already reads.
# ---------------------------------------------------------------------------------------


def read_snapshot(
    executor: ReadQueryExecutor,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Which graph the picture is of, and how much of it the picture is not showing.

    Reuses `story.stages.freshness.loaded_graph.LOAD_MARKERS` rather than restating it: the
    `:GraphLoad` marker has one owner and a second copy of its query would be a second answer to
    "which run is loaded". Its `node_count` excludes the marker itself, so `total_node_count`
    adds it back — 28,836 recorded, 28,837 present *(verified live 2026-08-04)*.

    An absent or duplicated marker is reported, not raised on. §7's gate is what refuses a run
    over it; a header that could not draw because the marker was missing would be the demo
    hiding the state the gate exists to show.
    """
    if timeout_seconds <= 0:
        raise ProjectionError(f"timeout_seconds must be positive, got {timeout_seconds!r}")
    rows = executor.read(LOAD_MARKERS, {}, timeout_seconds=timeout_seconds)
    markers = [LoadMarkerRow.from_row(row) for row in rows]
    first = markers[0] if markers else LoadMarkerRow()
    node_count = first.node_count
    return {
        "graph_run_id": first.graph_run_id,
        "status": first.status,
        "completed_at": first.completed_at,
        "marker_node_count": node_count,
        "total_node_count": (
            None if node_count is None else node_count + LOAD_MARKER_IS_ITSELF_UNCOUNTED),
        "edge_count": first.edge_count,
        "marker_count": len(markers),
    }


# ---------------------------------------------------------------------------------------
# Row -> node/edge mapping, one function per projection.
# ---------------------------------------------------------------------------------------


def _company(accumulator: _Accumulator, entity_id: Any) -> str | None:
    """The synthesised company node. See the module docstring for why it is not read."""
    return accumulator.node(
        entity_id,
        NODE_COMPANY,
        entity_id,
        synthesised=True,
        derived_from=DERIVED_FROM_SUBJECT,
        entity_id=entity_id,
        note=COMPANY_DISCLOSURE,
    )


def _metric(accumulator: _Accumulator, row: Mapping[str, Any]) -> str | None:
    return accumulator.node(
        row.get("metric_id"),
        NODE_METRIC,
        row.get("metric_label") or row.get("metric_id"),
        graph_label="Metric",
        metric_id=row.get("metric_id"),
        metric_category=row.get("metric_category"),
        unit=row.get("metric_unit"),
        gaap_status=row.get("metric_gaap_status"),
    )


def _observation(accumulator: _Accumulator, row: Mapping[str, Any]) -> str | None:
    """An observation node, labelled by its period because that is what fits at graph scale."""
    return accumulator.node(
        row.get("observation_id"),
        NODE_OBSERVATION,
        row.get("period_key") or row.get("observation_id"),
        graph_label="Observation",
        observation_id=row.get("observation_id"),
        metric_id=row.get("metric_id"),
        period_key=row.get("period_key"),
        value=row.get("value"),
        unit=row.get("observation_unit"),
        scale=row.get("scale"),
        period_start=row.get("period_start"),
        period_end=row.get("period_end"),
        instant_date=row.get("instant_date"),
        validation_state=row.get("validation_state"),
        source_lane=row.get("source_lane"),
        subject_entity_id=row.get("subject_entity_id"),
        evidence_edge_count=row.get("evidence_edge_count"),
        passage_id=row.get("observation_passage_id"),
        document_id=row.get("observation_document_id"),
        foreign_keys_derived_from=(
            DERIVED_FROM_OBSERVATION_KEYS if row.get("observation_passage_id") else None),
    )


def _passage(accumulator: _Accumulator, row: Mapping[str, Any]) -> str | None:
    """Labelled by the ordinal half of its id: `…#p139` renders as `p139` and nothing wider."""
    passage_id = row.get("passage_id")
    short = str(passage_id).rpartition("#")[2] if passage_id else None
    return accumulator.node(
        passage_id,
        NODE_PASSAGE,
        short or passage_id,
        graph_label="Passage",
        passage_id=passage_id,
        passage_kind=row.get("passage_kind"),
        char_count=row.get("passage_char_count"),
        document_id=row.get("document_id"),
    )


def _document(accumulator: _Accumulator, row: Mapping[str, Any]) -> str | None:
    document_id = row.get("document_id")
    form = row.get("document_form")
    filing_date = row.get("document_filing_date")
    label = f"{form} {filing_date}".strip() if form or filing_date else document_id
    return accumulator.node(
        document_id,
        NODE_DOCUMENT,
        label,
        graph_label="Document",
        document_id=document_id,
        form=form,
        filing_date=filing_date,
        title=row.get("document_title"),
    )


def _overview_graph(rows: Sequence[Mapping[str, Any]]) -> _Accumulator:
    """metric to observation to passage to document, plus the derived company hub.

    The company edge lands on the *observation* rather than on the metric, because
    `subject_entity_id` is an observation property and an edge to the metric would be a claim
    the property does not make. It makes a 308-spoke hub, which is an accurate picture of a
    single-filer corpus.
    """
    accumulator = _Accumulator()
    for row in rows:
        metric_id = _metric(accumulator, row)
        observation_id = _observation(accumulator, row)
        passage_id = _passage(accumulator, row)
        document_id = _document(accumulator, row)
        company_id = _company(accumulator, row.get("subject_entity_id"))
        accumulator.edge(metric_id, observation_id, EDGE_HAS_OBSERVATION,
                         synthesised=False, graph_relationship_type="HAS_OBSERVATION")
        accumulator.edge(observation_id, passage_id, EDGE_EVIDENCED_BY,
                         synthesised=False, graph_relationship_type="EVIDENCED_BY")
        accumulator.edge(passage_id, document_id, EDGE_PART_OF,
                         synthesised=False, graph_relationship_type="PART_OF")
        accumulator.edge(observation_id, company_id, EDGE_ABOUT_SUBJECT,
                         synthesised=True, derived_from=DERIVED_FROM_SUBJECT)
    return accumulator


def _coverage_graph(rows: Sequence[Mapping[str, Any]]) -> _Accumulator:
    """The metric-by-period lattice: metric nodes, period nodes, and a weighted edge per cell.

    Both the period node and the edge are synthesised. There is no `:Period` label in this graph
    and no relationship between a metric and a period — the edge is the `count()` the statement
    computed, carried as a property so the renderer can weight it.

    A metric with no observations arrives with a null `period_key` and stays as an **isolated
    node**. That is the point of this view: nine of the 26 metrics are in that state, `revenue`
    among them, and a lattice that dropped them would answer "what does this graph cover?" by
    showing only what it covers.
    """
    accumulator = _Accumulator()
    for row in rows:
        metric_id = _metric(accumulator, row)
        company_id = _company(accumulator, row.get("subject_entity_id"))
        period_key = row.get("period_key")
        period_id = accumulator.node(
            None if period_key is None else f"period:{period_key}",
            NODE_PERIOD,
            period_key,
            synthesised=True,
            derived_from=DERIVED_FROM_PERIOD,
            period_key=period_key,
            period_anchor=row.get("period_anchor"),
        )
        accumulator.edge(metric_id, period_id, EDGE_COVERS_PERIOD,
                         synthesised=True, derived_from=DERIVED_FROM_AGGREGATE,
                         observation_count=row.get("observation_count"),
                         first_observation_id=row.get("first_observation_id"))
        accumulator.edge(metric_id, company_id, EDGE_ABOUT_SUBJECT,
                         synthesised=True, derived_from=DERIVED_FROM_SUBJECT)
    return accumulator


def _evidence_backbone_graph(rows: Sequence[Mapping[str, Any]]) -> _Accumulator:
    """metric to passage to document, over cited evidence only.

    **No company node here, and its absence is deliberate.** The statement aggregates the
    observations away, so no row carries `subject_entity_id` and there is nothing to synthesise
    a subject from. Inventing one would be the module asserting something this query did not
    read.
    """
    accumulator = _Accumulator()
    for row in rows:
        metric_id = _metric(accumulator, row)
        passage_id = _passage(accumulator, row)
        document_id = _document(accumulator, row)
        accumulator.edge(metric_id, passage_id, EDGE_CITES_PASSAGE,
                         synthesised=True, derived_from=DERIVED_FROM_EVIDENCE_AGGREGATE,
                         cited_observation_count=row.get("cited_observation_count"),
                         first_observation_id=row.get("first_observation_id"))
        accumulator.edge(passage_id, document_id, EDGE_PART_OF,
                         synthesised=False, graph_relationship_type="PART_OF")
    return accumulator


def _subgraph_story_graph(rows: Sequence[Mapping[str, Any]]) -> _Accumulator:
    accumulator = _Accumulator()
    for row in rows:
        metric_id = _metric(accumulator, row)
        observation_id = _observation(accumulator, row)
        company_id = _company(accumulator, row.get("subject_entity_id"))
        accumulator.edge(metric_id, observation_id, EDGE_HAS_OBSERVATION,
                         synthesised=False, graph_relationship_type="HAS_OBSERVATION")
        accumulator.edge(observation_id, company_id, EDGE_ABOUT_SUBJECT,
                         synthesised=True, derived_from=DERIVED_FROM_SUBJECT)
    return accumulator


def _subgraph_evidence_graph(rows: Sequence[Mapping[str, Any]]) -> _Accumulator:
    """The same facts with their sources, and `quoted_text` riding on the evidence edge (§4)."""
    accumulator = _Accumulator()
    for row in rows:
        metric_id = _metric(accumulator, row)
        observation_id = _observation(accumulator, row)
        passage_id = _passage(accumulator, row)
        document_id = _document(accumulator, row)
        company_id = _company(accumulator, row.get("subject_entity_id"))
        accumulator.edge(metric_id, observation_id, EDGE_HAS_OBSERVATION,
                         synthesised=False, graph_relationship_type="HAS_OBSERVATION")
        accumulator.edge(observation_id, passage_id, EDGE_EVIDENCED_BY,
                         synthesised=False, graph_relationship_type="EVIDENCED_BY",
                         quoted_text=row.get("quoted_text"),
                         table_id=row.get("evidence_table_id"))
        accumulator.edge(passage_id, document_id, EDGE_PART_OF,
                         synthesised=False, graph_relationship_type="PART_OF")
        accumulator.edge(observation_id, company_id, EDGE_ABOUT_SUBJECT,
                         synthesised=True, derived_from=DERIVED_FROM_SUBJECT)
    return accumulator


# ---------------------------------------------------------------------------------------
# The legend, the counts and the digest.
# ---------------------------------------------------------------------------------------

#: What each rendered type means, in the words the header shows. Keyed by payload type so the
#: legend below can be built from the nodes actually present rather than from a fixed list —
#: a legend naming a type the picture does not contain is a legend that lies about the picture.
NODE_TYPE_DESCRIPTIONS: Mapping[str, str] = {
    NODE_COMPANY: "The subject of every observation, derived from a property and never read.",
    NODE_METRIC: "One metric node in the loaded graph.",
    NODE_OBSERVATION: "One observed value for one metric in one period.",
    NODE_PASSAGE: "The passage an observation was evidenced by.",
    NODE_DOCUMENT: "The filing a passage belongs to.",
    NODE_PERIOD: "A reporting period, derived from period_key; no such node exists in the graph.",
}

EDGE_TYPE_DESCRIPTIONS: Mapping[str, str] = {
    EDGE_HAS_OBSERVATION: "Traversed HAS_OBSERVATION.",
    EDGE_EVIDENCED_BY: "Traversed EVIDENCED_BY; the quote rides on this edge.",
    EDGE_PART_OF: "Traversed PART_OF.",
    EDGE_ABOUT_SUBJECT: "Derived from subject_entity_id; the graph edge for it is never walked.",
    EDGE_COVERS_PERIOD: "An observation count, aggregated per metric and period.",
    EDGE_CITES_PASSAGE: "A metric and a passage joined through observations already aggregated.",
}


def _legend(elements: Sequence[Mapping[str, Any]],
            descriptions: Mapping[str, str]) -> list[dict[str, Any]]:
    """One entry per type present, with its real count and whether it was read or derived."""
    order: list[str] = []
    counts: dict[str, int] = {}
    derived: dict[str, bool] = {}
    for element in elements:
        kind = str(element["type"])
        if kind not in counts:
            order.append(kind)
            counts[kind] = 0
            derived[kind] = bool(element.get("synthesised"))
        counts[kind] += 1
        derived[kind] = derived[kind] or bool(element.get("synthesised"))
    return [
        {
            "type": kind,
            "count": counts[kind],
            "source": "derived" if derived[kind] else "graph",
            "description": descriptions.get(kind, ""),
        }
        for kind in order
    ]


def _counts(nodes: Sequence[Mapping[str, Any]],
            edges: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_node_type: dict[str, int] = {}
    by_edge_type: dict[str, int] = {}
    for node in nodes:
        by_node_type[str(node["type"])] = by_node_type.get(str(node["type"]), 0) + 1
    for edge in edges:
        by_edge_type[str(edge["type"])] = by_edge_type.get(str(edge["type"]), 0) + 1
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "nodes_by_type": by_node_type,
        "edges_by_type": by_edge_type,
        "synthesised_nodes": sum(1 for node in nodes if node.get("synthesised")),
        "synthesised_edges": sum(1 for edge in edges if edge.get("synthesised")),
    }


def content_digest(nodes: Sequence[Mapping[str, Any]],
                   edges: Sequence[Mapping[str, Any]]) -> str:
    """sha256 over the nodes and edges as they will be serialised, order included.

    The determinism claim in one function: build a projection twice and compare this. Order is
    *not* normalised away — a payload whose node list reordered between runs would draw
    differently under the layout's seed, so an ordering change has to fail this.
    """
    canonical = json.dumps({"nodes": list(nodes), "edges": list(edges)},
                           sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload(
    name: str,
    read: _Read,
    accumulator: _Accumulator,
    *,
    snapshot: Mapping[str, Any] | None,
    view: str | None = None,
    bounds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = accumulator.node_list()
    edges = accumulator.edge_list()
    resolved_bounds: dict[str, Any] = {
        "row_limit": read.row_limit,
        "rows_returned": len(read.rows),
        "truncated": read.truncated,
        "timeout_seconds": read.timeout_seconds,
    }
    resolved_bounds.update(bounds or {})
    return {
        "projection": name,
        "view": view,
        "disclosure": DISCLOSURE,
        "company_disclosure": COMPANY_DISCLOSURE,
        "snapshot": dict(snapshot) if snapshot is not None else None,
        "bounds": resolved_bounds,
        "counts": _counts(nodes, edges),
        "legend": {
            "nodes": _legend(nodes, NODE_TYPE_DESCRIPTIONS),
            "edges": _legend(edges, EDGE_TYPE_DESCRIPTIONS),
        },
        "content_digest": content_digest(nodes, edges),
        "timing": {"query_ms": round(read.elapsed_ms, 3)},
        "nodes": nodes,
        "edges": edges,
    }


# ---------------------------------------------------------------------------------------
# The public builders.
# ---------------------------------------------------------------------------------------


def build_overview(
    executor: ReadQueryExecutor,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    row_limit: int = OVERVIEW_ROW_LIMIT,
    observations_per_metric: int = OBSERVATIONS_PER_METRIC,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Projection C, the demo default. Two reads: the load marker, then the overview.

    `snapshot` is an override so a caller holding a freshness read already does not pay for a
    second marker lookup; passing nothing does the read here.
    """
    if not 1 <= observations_per_metric <= MAX_OBSERVATIONS_PER_METRIC:
        raise ProjectionError(
            f"observations_per_metric must be between 1 and {MAX_OBSERVATIONS_PER_METRIC}, got "
            f"{observations_per_metric}")
    resolved = (snapshot if snapshot is not None
                else read_snapshot(executor, timeout_seconds=timeout_seconds))
    read = _read(executor, OVERVIEW_STATEMENT,
                 {"observations_per_metric": observations_per_metric},
                 row_limit=row_limit, timeout_seconds=timeout_seconds)
    return _payload(PROJECTION_OVERVIEW, read, _overview_graph(read.rows), snapshot=resolved,
                    bounds={"observations_per_metric": observations_per_metric})


def build_coverage(
    executor: ReadQueryExecutor,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    row_limit: int = COVERAGE_ROW_LIMIT,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Projection A, the coverage lattice, including the metrics with no observations at all."""
    resolved = (snapshot if snapshot is not None
                else read_snapshot(executor, timeout_seconds=timeout_seconds))
    read = _read(executor, COVERAGE_STATEMENT, {},
                 row_limit=row_limit, timeout_seconds=timeout_seconds)
    return _payload(PROJECTION_COVERAGE, read, _coverage_graph(read.rows), snapshot=resolved)


def build_evidence_backbone(
    executor: ReadQueryExecutor,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    row_limit: int = BACKBONE_ROW_LIMIT,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Projection B, the evidence backbone. The most expensive of the three — see the statement."""
    resolved = (snapshot if snapshot is not None
                else read_snapshot(executor, timeout_seconds=timeout_seconds))
    read = _read(executor, EVIDENCE_BACKBONE_STATEMENT, {},
                 row_limit=row_limit, timeout_seconds=timeout_seconds)
    return _payload(PROJECTION_EVIDENCE_BACKBONE, read, _evidence_backbone_graph(read.rows),
                    snapshot=resolved)


#: Name to builder. Not a mapping of name to *statement*: a dict literal holding Cypher would be
#: a statement whose text is not one `ast.Constant`, and the package-wide scan is right to
#: refuse it. Names of functions are not Cypher.
PROJECTION_BUILDERS = {
    PROJECTION_OVERVIEW: build_overview,
    PROJECTION_COVERAGE: build_coverage,
    PROJECTION_EVIDENCE_BACKBONE: build_evidence_backbone,
}


def build_projection(
    executor: ReadQueryExecutor,
    name: str = PROJECTION_OVERVIEW,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The one entry point a request handler needs. An unknown name is refused, never defaulted.

    A default would make a typo in a query string return the overview and look like it worked,
    which is the same failure mode `StoryProviderConfig.kind` refuses for the same reason.
    """
    if name not in PROJECTION_BUILDERS:
        raise ProjectionError(
            f"unknown projection {name!r}; the demo offers {list(PROJECTION_NAMES)}")
    return PROJECTION_BUILDERS[name](
        executor, timeout_seconds=timeout_seconds, snapshot=snapshot)


def _validated_ids(values: Sequence[str] | None, *, name: str, maximum: int) -> list[str]:
    """A bound, de-duplicated, sorted list of identifiers, or a refusal.

    Sorted because the parameter is part of what makes the result reproducible: two clients
    naming the same two metrics in different orders must get the same picture and the same
    `content_digest`. De-duplicated because `IN` would otherwise do the work twice.
    """
    if not values:
        raise ProjectionError(f"{name} must name at least one value")
    if isinstance(values, str):
        raise ProjectionError(f"{name} must be a sequence of strings, not one string")
    cleaned = sorted({str(value).strip() for value in values if str(value).strip()})
    if not cleaned:
        raise ProjectionError(f"{name} must name at least one non-blank value")
    if len(cleaned) > maximum:
        raise ProjectionError(f"{name} may name at most {maximum} values, got {len(cleaned)}")
    return cleaned


def build_subgraph(
    executor: ReadQueryExecutor,
    *,
    metric_ids: Sequence[str],
    period_keys: Sequence[str],
    view: str = VIEW_STORY,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    row_limit: int = SUBGRAPH_ROW_LIMIT,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The focused neighbourhood for one candidate, in either of §4's two views.

    The client names the view and passes bound parameters; there is no path from a request to
    the statement text, which is what "no Cypher from the browser, ever" means in practice.
    """
    if view not in SUBGRAPH_VIEWS:
        raise ProjectionError(f"unknown view {view!r}; the demo offers {list(SUBGRAPH_VIEWS)}")
    parameters = {
        "metric_ids": _validated_ids(metric_ids, name="metric_ids",
                                     maximum=MAX_SUBGRAPH_METRIC_IDS),
        "period_keys": _validated_ids(period_keys, name="period_keys",
                                      maximum=MAX_SUBGRAPH_PERIOD_KEYS),
    }
    statement = (SUBGRAPH_STORY_STATEMENT if view == VIEW_STORY
                 else SUBGRAPH_EVIDENCE_STATEMENT)
    to_graph = _subgraph_story_graph if view == VIEW_STORY else _subgraph_evidence_graph
    read = _read(executor, statement, parameters,
                 row_limit=row_limit, timeout_seconds=timeout_seconds)
    return _payload("subgraph", read, to_graph(read.rows), snapshot=snapshot, view=view,
                    bounds={"metric_ids": parameters["metric_ids"],
                            "period_keys": parameters["period_keys"]})


def build_candidate_subgraph(
    executor: ReadQueryExecutor,
    candidate: Any,
    *,
    view: str = VIEW_STORY,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    row_limit: int = SUBGRAPH_ROW_LIMIT,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`build_subgraph` for a `StoryCandidate`, reading its metrics and its anchor periods.

    `candidate` is typed loosely on purpose: `story.core.models.StoryCandidate` is what a caller
    passes, and naming it here would make a module that draws pictures import the detector's
    result type to read two tuples off it.
    """
    metric_ids = getattr(candidate, "metric_ids", None)
    period_keys = getattr(candidate, "anchor_period_keys", None)
    if metric_ids is None or period_keys is None:
        raise ProjectionError(
            "candidate must carry metric_ids and anchor_period_keys; got "
            f"{type(candidate).__name__}")
    return build_subgraph(executor, metric_ids=metric_ids, period_keys=period_keys, view=view,
                          timeout_seconds=timeout_seconds, row_limit=row_limit,
                          snapshot=snapshot)


__all__ = [
    "BACKBONE_ROW_LIMIT",
    "COMPANY_DISCLOSURE",
    "COVERAGE_ROW_LIMIT",
    "COVERAGE_STATEMENT",
    "DEFAULT_TIMEOUT_SECONDS",
    "DISCLOSURE",
    "EDGE_ABOUT_SUBJECT",
    "EDGE_CITES_PASSAGE",
    "EDGE_COVERS_PERIOD",
    "EDGE_EVIDENCED_BY",
    "EDGE_HAS_OBSERVATION",
    "EDGE_PART_OF",
    "EVIDENCE_BACKBONE_STATEMENT",
    "MAX_OBSERVATIONS_PER_METRIC",
    "MAX_ROW_LIMIT",
    "MAX_SUBGRAPH_METRIC_IDS",
    "MAX_SUBGRAPH_PERIOD_KEYS",
    "NODE_COMPANY",
    "NODE_DOCUMENT",
    "NODE_METRIC",
    "NODE_OBSERVATION",
    "NODE_PASSAGE",
    "NODE_PERIOD",
    "OBSERVATIONS_PER_METRIC",
    "OVERVIEW_ROW_LIMIT",
    "OVERVIEW_STATEMENT",
    "PROJECTION_BUILDERS",
    "PROJECTION_COVERAGE",
    "PROJECTION_EVIDENCE_BACKBONE",
    "PROJECTION_NAMES",
    "PROJECTION_OVERVIEW",
    "PROJECTION_STATEMENTS",
    "ProjectionError",
    "SUBGRAPH_EVIDENCE_STATEMENT",
    "SUBGRAPH_ROW_LIMIT",
    "SUBGRAPH_STORY_STATEMENT",
    "SUBGRAPH_VIEWS",
    "VIEW_EVIDENCE",
    "VIEW_STORY",
    "build_candidate_subgraph",
    "build_coverage",
    "build_evidence_backbone",
    "build_overview",
    "build_projection",
    "build_subgraph",
    "content_digest",
    "read_snapshot",
]
