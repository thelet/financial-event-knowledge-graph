"""Every Cypher statement the story agent will ever run, as plain string constants.

Responsibility: the statements, and nothing else. No executor, no result type, no ontology, no
parameter validation — this module holds twelve strings and a docstring per string saying what
bounds it. It is its own file because it is the file a reviewer reads: §16 makes code-owned
parameterised Cypher *the primary control*, and a control that is scattered across a package
cannot be read in one sitting.

**Nothing here is built.** No f-string, no `%`, no `+`, no `.format()`, no `join`. Every
caller value arrives as a bound `$parameter`, including every `LIMIT`, so there is no
expression anywhere in this package that could put a caller's characters into a query.
`tests/story/test_story_retrieval_cypher.py` walks the AST of every module under `story/` and
refuses anything but an `ast.Constant` here — a rule about the shape of the code, not about
anyone's discipline.

**What the statements are collectively bounded by** (§9, §16):

    hard LIMIT            every statement that reads a node ends in `LIMIT $row_limit`
    named return fields   no `RETURN n`, no `n {.*}` — a whole node blows §10.2's budget and
                          escapes as a driver type (S0c's `StoryGraphResultError`)
    two hops              the longest pattern below is three nodes; nothing is variable-length
    no dynamic labels     every label and relationship type is a literal in this file
    no APOC               `db.index.fulltext.queryNodes` ships with the server
    no write clause       there is no `CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE` or `DROP`
    allowlisted labels    `Metric, Observation, Event, Passage, Document, Issue` — the two
                          base labels not named here, `Entity` and `EvidenceSource`, are
                          absent for the reasons below

**`OBSERVATION_OF_SUBJECT` appears nowhere in this file, and neither does `:Entity`.**
`opendoor` carries 2,704 of them *(verified live 2026-08-03)*, so one hop outward from the
entity is the whole graph. Subject identity is read from `Observation.subject_entity_id`,
which every observation carries — a property lookup instead of a traversal, and the same
answer.

**`:Issue`, `FOUND_IN` and `CONCERNS_METRIC` appear in exactly one statement**, the
counter-evidence one, and it excludes `:NotAttempted`. 10,852 of 17,130 issues record a
question that was never asked; surfacing them would report the run's own bounds as a finding
about Opendoor.

**`:EvidenceSource` is not queried because zero exist** — all 2,710 `EVIDENCED_BY` edges land
on a `:Passage` *(verified live)*. §13.7.2's Rule C is the answer when a lane emits one, and
until then `get_fact_evidence` returning nothing for such a binding is the honest state; a
statement written against a label with no nodes would be untested Cypher pretending to be
coverage.

**Two shape corrections are visible in the text below and are the reason to read it.**

* **C3.** `quoted_text`, `table_id` and `block_ids` are properties of the `EVIDENCED_BY`
  *edge*. The evidence statements bind the relationship (`evidence`) and return
  `evidence.quoted_text`; a query that returned only node fields would silently lose every
  citation quote and still look complete. *(Verified live: `quoted_text` on 2,710/2,710 edges,
  `table_id` on 2,690, `block_ids` on 2,710 — and `Passage.table_id` is a **different**
  property on 503 of 8,776 passages, returned separately as `passage_table_id` so the two
  cannot be confused.)*
* **C2.** Neo4j stores no null, so `IS NOT NULL` here reads as *"the node carries this
  property"* and nothing else. Period shape is therefore derived from the fields actually
  present — `period_start`+`period_end` on 2,304 of 2,704 observations, `instant_date` on the
  other 400 — never from a `shape` property, which does not exist. Where an aggregate could
  hide the difference, a `…_present_count` is returned beside it, because `collect()` drops
  absent values and a caller cannot tell an empty list from a list of nothings.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------------------
# Metrics — §9's two zero-hop tools
# ---------------------------------------------------------------------------------------

#: `list_metrics`, bound 26 (`MAX_ROWS`). Zero hops: a bare label scan of the 26 `:Metric`
#: nodes, backed by the `metric_key` uniqueness constraint's range index only for the ordering
#: key. Deliberately no `HAS_OBSERVATION` hop for a coverage count — §9 declares this tool at
#: zero hops, and a per-metric count over 2,704 observations is `get_metric_history`'s job.
LIST_METRICS = """
MATCH (metric:Metric)
RETURN metric.metric_id AS metric_id,
       metric.label AS label,
       metric.metric_category AS metric_category,
       metric.unit AS unit,
       metric.value_type AS value_type,
       metric.period_type AS period_type,
       metric.gaap_status AS gaap_status
ORDER BY metric.metric_id
LIMIT $row_limit
"""

#: `get_metric_definition`'s graph half, bound 1, zero hops, on the `metric_key` index.
#:
#: **C4 lives in what this statement does not return.** `:Metric` carries no `percentage_min`,
#: `percentage_max`, `distinct_from` or `reconciles_to` — 22 properties, checked live — so the
#: definition comes from the ontology and this statement contributes only *presence* and the
#: ontology stamp the graph was projected under. `metric_metadata.py` refuses to fall back to
#: a node property, and there is no node property here to fall back to. The 36 `DISTINCT_FROM`
#: edges are deliberately not traversed: they may support inspection, never a comparability
#: ruling, and reading them here would put a second answer beside the authoritative one.
METRIC_NODE = """
MATCH (metric:Metric)
WHERE metric.metric_id = $metric_id
RETURN metric.metric_id AS metric_id,
       metric.label AS graph_label,
       metric.ontology_id AS graph_ontology_id,
       metric.ontology_version AS graph_ontology_version,
       metric.ontology_definition_hash AS graph_ontology_definition_hash,
       metric.graph_run_id AS graph_run_id,
       metric.graph_projection_version AS graph_projection_version
ORDER BY metric.metric_id
LIMIT $row_limit
"""

# ---------------------------------------------------------------------------------------
# Observations — the fact surface
# ---------------------------------------------------------------------------------------

#: `get_metric_history`, bound 200, one hop's worth of index work and no traversal at all:
#: `obs_metric` on `Observation.metric_id` selects, `obs_period` supports the ordering.
#:
#: **C2 twice over.** `period_shape` is computed from the fields present, because there is no
#: `shape` property and an absent `period_start` is indistinguishable from a null one. The
#: optional window then compares against `coalesce(period_end, instant_date)`, which every one
#: of the 2,704 observations carries one of — an observation with neither could not be placed
#: in a window at all, and excluding it silently is exactly what the returned `period_shape`
#: makes visible.
#:
#: `$shape`, `$since` and `$until` are optional by the `IS NULL OR` idiom rather than by
#: building a `WHERE` clause: one statement, one plan, and no branch that could assemble text.
METRIC_HISTORY = """
MATCH (observation:Observation)
WHERE observation.metric_id = $metric_id
  AND ($shape IS NULL
       OR ($shape = 'duration'
           AND observation.period_start IS NOT NULL AND observation.period_end IS NOT NULL)
       OR ($shape = 'instant' AND observation.instant_date IS NOT NULL))
  AND ($since IS NULL
       OR coalesce(observation.period_end, observation.instant_date) >= $since)
  AND ($until IS NULL
       OR coalesce(observation.period_end, observation.instant_date) <= $until)
RETURN observation.observation_id AS observation_id,
       observation.metric_id AS metric_id,
       observation.period_key AS period_key,
       observation.value AS value,
       observation.unit AS unit,
       observation.scale AS scale,
       observation.currency AS currency,
       observation.period_start AS period_start,
       observation.period_end AS period_end,
       observation.instant_date AS instant_date,
       CASE
           WHEN observation.period_start IS NOT NULL AND observation.period_end IS NOT NULL
               THEN 'duration'
           WHEN observation.instant_date IS NOT NULL THEN 'instant'
           ELSE 'unknown'
       END AS period_shape,
       observation.row_label AS row_label,
       observation.column_label AS column_label,
       observation.source_lane AS source_lane,
       observation.assertion_type AS assertion_type,
       observation.validation_state AS validation_state,
       observation.warning_codes AS warning_codes,
       observation.ambiguity_codes AS ambiguity_codes,
       observation.subject_entity_id AS subject_entity_id,
       observation.passage_id AS passage_id,
       observation.document_id AS document_id
ORDER BY coalesce(observation.period_end, observation.instant_date),
         observation.period_key,
         observation.observation_id
LIMIT $row_limit
"""

#: `compare_metric_periods`, bound 2 — one row per period, never one row per observation.
#: `adjusted_ebitda` has seven observations in 2022Q2 and six in 2022Q3 *(verified live)*, so a
#: row-per-observation shape could not fit the plan's cap of 2 without dropping evidence.
#:
#: **It aggregates and refuses to choose.** §6.1's canonical-value policy is S2's, and a
#: retrieval tool that returned "the" value would have made that ruling silently. Every field
#: is a `DISTINCT` collection: one element means the period agrees with itself, more than one
#: is the disagreement S2 has to resolve, and the caller can see which it got.
#:
#: **C2 is why the `_present_count` fields exist.** `collect()` drops absent values, so
#: `distinct_currencies = []` and `distinct_currencies = ['USD']` are the only two shapes a
#: collection can take and neither says how many observations carried the property — `currency`
#: is on 997 of 2,704. The counts beside them are the missing-versus-valued distinction C2
#: requires, in the one place an aggregate would otherwise erase it.
#:
#: `WITH observation ORDER BY observation.observation_id` before the aggregation is what makes
#: every collected list deterministic: aggregation consumes rows in the order it receives them,
#: so without this the lists would be in scan order and two identical calls could differ.
COMPARE_METRIC_PERIODS = """
MATCH (observation:Observation)
WHERE observation.metric_id = $metric_id
  AND observation.period_key IN [$period_key_a, $period_key_b]
WITH observation ORDER BY observation.observation_id
WITH observation.period_key AS period_key,
     count(observation) AS observation_count,
     collect(DISTINCT observation.value) AS distinct_values,
     collect(DISTINCT observation.unit) AS distinct_units,
     collect(DISTINCT observation.scale) AS distinct_scales,
     collect(DISTINCT observation.currency) AS distinct_currencies,
     collect(DISTINCT observation.source_lane) AS distinct_source_lanes,
     collect(DISTINCT observation.validation_state) AS distinct_validation_states,
     collect(DISTINCT observation.period_start) AS distinct_period_starts,
     collect(DISTINCT observation.period_end) AS distinct_period_ends,
     collect(DISTINCT observation.instant_date) AS distinct_instant_dates,
     collect(DISTINCT
         CASE
             WHEN observation.period_start IS NOT NULL AND observation.period_end IS NOT NULL
                 THEN 'duration'
             WHEN observation.instant_date IS NOT NULL THEN 'instant'
             ELSE 'unknown'
         END) AS distinct_period_shapes,
     sum(CASE WHEN observation.currency IS NOT NULL THEN 1 ELSE 0 END)
         AS currency_present_count,
     sum(CASE WHEN observation.scale IS NOT NULL THEN 1 ELSE 0 END) AS scale_present_count,
     sum(CASE WHEN observation.period_start IS NOT NULL THEN 1 ELSE 0 END)
         AS period_start_present_count,
     sum(CASE WHEN observation.period_end IS NOT NULL THEN 1 ELSE 0 END)
         AS period_end_present_count,
     collect(DISTINCT observation.observation_id) AS collected_observation_ids,
     collect(DISTINCT observation.passage_id) AS collected_passage_ids,
     collect(DISTINCT observation.document_id) AS collected_document_ids
RETURN period_key,
       observation_count,
       distinct_values,
       distinct_units,
       distinct_scales,
       distinct_currencies,
       distinct_source_lanes,
       distinct_validation_states,
       distinct_period_starts,
       distinct_period_ends,
       distinct_instant_dates,
       distinct_period_shapes,
       currency_present_count,
       scale_present_count,
       period_start_present_count,
       period_end_present_count,
       collected_observation_ids[..$handle_limit] AS observation_ids,
       collected_passage_ids[..$handle_limit] AS passage_ids,
       collected_document_ids[..$handle_limit] AS document_ids
ORDER BY period_key
LIMIT $row_limit
"""

# ---------------------------------------------------------------------------------------
# Evidence — §13.7's citation chain, and the one place C3 decides whether it works
# ---------------------------------------------------------------------------------------

#: `get_fact_evidence` for an observation, bound 10, two hops
#: (`Observation -> Passage -> Document`), entered on the `observation_key` uniqueness index.
#:
#: **C3 is the whole point of the `evidence` binding.** `quoted_text`, `table_id` and
#: `block_ids` hang off the `EVIDENCED_BY` relationship, not off the observation, and this is
#: the statement that would silently return uncitable facts if it bound only the two nodes.
#: `passage.table_id` is returned as `passage_table_id` because it is a *different* property
#: with different occupancy (503 passages against 2,690 edges) and merging the two names would
#: manufacture agreement between them.
#:
#: `document.report_date` is on 184 of 185 documents (C2); it is returned as-is and the one
#: absence surfaces as `None`, which S0c's contract defines as *absent* and nothing else.
FACT_EVIDENCE_FOR_OBSERVATION = """
MATCH (observation:Observation)-[evidence:EVIDENCED_BY]->(passage:Passage)-[:PART_OF]->(document:Document)
WHERE observation.observation_id = $observation_id
RETURN observation.observation_id AS observation_id,
       observation.metric_id AS metric_id,
       observation.period_key AS period_key,
       observation.value AS value,
       observation.unit AS unit,
       observation.scale AS scale,
       observation.currency AS currency,
       observation.row_label AS row_label,
       observation.column_label AS column_label,
       observation.source_lane AS source_lane,
       observation.validation_state AS validation_state,
       evidence.quoted_text AS quoted_text,
       evidence.table_id AS table_id,
       evidence.block_ids AS block_ids,
       evidence.evidence_kind AS evidence_kind,
       evidence.ontology_declared AS ontology_declared,
       evidence.source_url AS source_url,
       passage.passage_id AS passage_id,
       passage.passage_kind AS passage_kind,
       passage.char_count AS passage_char_count,
       passage.table_id AS passage_table_id,
       passage.section_id AS section_id,
       document.document_id AS document_id,
       document.form AS form,
       document.document_type AS document_type,
       document.accession AS accession,
       document.filing_date AS filing_date,
       document.report_date AS report_date
ORDER BY passage.passage_id, evidence.edge_key
LIMIT $row_limit
"""

#: `get_fact_evidence` for an event, bound 10, two hops, on the `event_key` index.
#:
#: **§13.8 decides what is absent here.** An event's own properties are `dict[str, str]` of
#: free text, projected as `prop_*` keys — returning them would require either a dynamic
#: property name or a hand-listed set of six, and either way it hands a model strings that look
#: like facts and are not. `property_names` is returned instead: the caller learns *that* the
#: event records a headcount without being handed a numeral it could bind.
#:
#: `occurred_on` is on 3 of 6 events and `announced_on` on 4 (C2), so both are returned and
#: neither is coalesced away — §5.5's point that an announcement is not an occurrence is only
#: legible if the two arrive separately.
FACT_EVIDENCE_FOR_EVENT = """
MATCH (event:Event)-[evidence:EVIDENCED_BY]->(passage:Passage)-[:PART_OF]->(document:Document)
WHERE event.event_id = $event_id
RETURN event.event_id AS event_id,
       event.event_type_id AS event_type_id,
       event.occurred_on AS occurred_on,
       event.announced_on AS announced_on,
       event.occurrence_date_text AS occurrence_date_text,
       event.announcement_date_text AS announcement_date_text,
       event.dates_equal AS dates_equal,
       event.property_names AS property_names,
       event.validation_state AS validation_state,
       evidence.quoted_text AS quoted_text,
       evidence.evidence_kind AS evidence_kind,
       evidence.source_url AS source_url,
       passage.passage_id AS passage_id,
       passage.passage_kind AS passage_kind,
       passage.char_count AS passage_char_count,
       document.document_id AS document_id,
       document.form AS form,
       document.document_type AS document_type,
       document.filing_date AS filing_date,
       document.report_date AS report_date
ORDER BY passage.passage_id, evidence.edge_key
LIMIT $row_limit
"""

# ---------------------------------------------------------------------------------------
# Passages — the corpus surface, and the one place the corpus is not what it looks like
# ---------------------------------------------------------------------------------------

#: `get_passage_context`, bound 7 (`before` ≤ 3 + anchor + `after` ≤ 3), one hop for the
#: anchor's document and a `psg_document` index lookup for the neighbours.
#:
#: **Ordinality is parsed out of the id because no property carries it.** A `:Passage` has 17
#: properties and none is a sequence number *(checked live)*; `passage_id` is
#: `<document_id>#p<n>` on all 8,776 nodes, and `document_id + '#p' + split(id,'#p')[1] == id`
#: holds for every one of them — measured, because an id scheme that is nearly regular is worse
#: than one that is not. Lexicographic ordering would put `#p100` between `#p1` and `#p11`, so
#: the integer is what the window is computed over.
#:
#: **Neighbouring here means neighbouring among the *cited* passages.** The graph holds 8,776
#: of the corpus's 12,442 passages, so the passage before `#p120` may be `#p118`. Rather than
#: imply contiguity, every row carries its own `passage_index` and its
#: `offset_from_anchor`, and a gap between them is a gap a reader can see. That is the fourth
#: shape fact, made legible instead of assumed away.
PASSAGE_CONTEXT = """
MATCH (anchor:Passage)-[:PART_OF]->(document:Document)
WHERE anchor.passage_id = $passage_id
WITH anchor, document, toInteger(split(anchor.passage_id, '#p')[1]) AS anchor_index
MATCH (neighbour:Passage)
WHERE neighbour.document_id = anchor.document_id
  AND toInteger(split(neighbour.passage_id, '#p')[1]) >= anchor_index - $before
  AND toInteger(split(neighbour.passage_id, '#p')[1]) <= anchor_index + $after
RETURN neighbour.passage_id AS passage_id,
       toInteger(split(neighbour.passage_id, '#p')[1]) AS passage_index,
       toInteger(split(neighbour.passage_id, '#p')[1]) - anchor_index AS offset_from_anchor,
       neighbour.passage_id = anchor.passage_id AS is_anchor,
       neighbour.text AS text,
       neighbour.char_count AS char_count,
       neighbour.passage_kind AS passage_kind,
       neighbour.heading_path AS heading_path,
       neighbour.section_id AS section_id,
       neighbour.table_id AS table_id,
       neighbour.source_url AS source_url,
       document.document_id AS document_id,
       document.form AS form,
       document.document_type AS document_type,
       document.filing_date AS filing_date,
       document.report_date AS report_date
ORDER BY passage_index
LIMIT $row_limit
"""

#: `search_passages`, bound 25, one hop (`Passage -> Document`) after the `passage_text`
#: fulltext index. `db.index.fulltext.queryNodes` is a server built-in, not APOC (§16).
#:
#: **The index name is a literal and the query is a parameter.** That split is the tool's
#: safety: `lucene_escaping.build_query` produces `$lucene_query`, so a term list cannot become
#: syntax, and nothing anywhere can choose which index is read.
#:
#: **No inner `limit` on the procedure call, deliberately.** `queryNodes` accepts one, and it
#: would be applied *before* the document filter — so asking for 8-K passages would silently
#: return nothing whenever the top matches were 10-Qs. The trailing `LIMIT` bounds the result;
#: the timeout bounds the scan.
#:
#: **The window is on `filing_date`, not `report_date`.** `filing_date` is on all 185
#: documents and `report_date` on 184 (C2), so filtering on `report_date` would drop one
#: document from every dated search without saying so. Both are returned.
#:
#: `left(passage.text, $excerpt_chars)` bounds the text at §10.2.1's excerpt radius while
#: `char_count` reports the real length, so a truncated excerpt is visibly truncated. Full text
#: is `get_passage_context`'s job.
SEARCH_PASSAGES = """
CALL db.index.fulltext.queryNodes('passage_text', $lucene_query) YIELD node, score
WITH node AS passage, score
MATCH (passage:Passage)-[:PART_OF]->(document:Document)
WHERE ($document_types IS NULL OR document.document_type IN $document_types)
  AND ($since IS NULL OR document.filing_date >= $since)
  AND ($until IS NULL OR document.filing_date <= $until)
RETURN passage.passage_id AS passage_id,
       score AS score,
       left(passage.text, $excerpt_chars) AS text_excerpt,
       passage.char_count AS char_count,
       passage.passage_kind AS passage_kind,
       passage.heading_path AS heading_path,
       passage.section_id AS section_id,
       passage.table_id AS table_id,
       passage.source_url AS source_url,
       document.document_id AS document_id,
       document.form AS form,
       document.document_type AS document_type,
       document.filing_date AS filing_date,
       document.report_date AS report_date
ORDER BY score DESC, passage.passage_id
LIMIT $row_limit
"""

# ---------------------------------------------------------------------------------------
# Events and counter-evidence
# ---------------------------------------------------------------------------------------

#: `get_events_in_window`, bound 50, one hop to the passage that evidences the event. The
#: `evt_occurred` index covers the `occurred_on` half of the window.
#:
#: **The window is an explicit disjunction, not a `coalesce`.** Only 3 of 6 events carry
#: `occurred_on` and 4 carry `announced_on`; coalescing would place an undated event on its
#: announcement date and report it as an occurrence, which §5.5 and §13.8 both refuse.
#: `window_matched_on` says which field put the row in the window, so the caller never has to
#: guess. `IS NOT NULL` is a presence test here, not a null test (C2).
EVENTS_IN_WINDOW = """
MATCH (event:Event)-[evidence:EVIDENCED_BY]->(passage:Passage)
WHERE (event.occurred_on IS NOT NULL
       AND event.occurred_on >= $since AND event.occurred_on <= $until)
   OR (event.announced_on IS NOT NULL
       AND event.announced_on >= $since AND event.announced_on <= $until)
RETURN event.event_id AS event_id,
       event.event_type_id AS event_type_id,
       event.occurred_on AS occurred_on,
       event.announced_on AS announced_on,
       event.occurrence_date_text AS occurrence_date_text,
       event.announcement_date_text AS announcement_date_text,
       event.dates_equal AS dates_equal,
       event.property_names AS property_names,
       event.validation_state AS validation_state,
       CASE
           WHEN event.occurred_on IS NOT NULL
                AND event.occurred_on >= $since AND event.occurred_on <= $until
               THEN 'occurred_on'
           ELSE 'announced_on'
       END AS window_matched_on,
       evidence.quoted_text AS quoted_text,
       evidence.source_url AS source_url,
       passage.passage_id AS passage_id,
       event.document_id AS document_id
ORDER BY coalesce(event.occurred_on, event.announced_on), event.event_id
LIMIT $row_limit
"""

#: `find_counter_evidence`, bound 25. **The only statement in this package that names
#: `:Issue`, `FOUND_IN` or `CONCERNS_METRIC`** (§9), and it excludes `:NotAttempted`.
#:
#: Two hops at the widest: `Metric <- Issue -> Passage`. The first `MATCH` is a property
#: lookup on the `obs_metric` and `obs_period` indexes and traverses nothing — the observations
#: carry `document_id`, so the documents that report this fact are read rather than walked.
#:
#: **The join is at document grain, and that was a measurement, not a preference.** Joining on
#: the *passages* that evidence the fact returns zero rows for `adjusted_ebitda` 2022Q3 and for
#: `gaap_gross_margin` 2022Q3 — the refusals live in neighbouring tables of the same filing,
#: not in the cell the number came from. At document grain the same call returns 9 rows for
#: `contribution_margin` 2022Q3 and 3 for `homes_sold` 2022Q3 *(verified live 2026-08-03)*.
#: Counter-evidence is "what this filing would not let the run say about this metric", and the
#: filing is the scope in which that is true.
#:
#: Zero rows is an `Ok`, not a `NotFound`: `adjusted_ebitda` 2022Q3 genuinely has no refused
#: claim in its documents, and that is a finding.
COUNTER_EVIDENCE = """
MATCH (observation:Observation)
WHERE observation.metric_id = $metric_id AND observation.period_key = $period_key
WITH collect(DISTINCT observation.document_id) AS document_ids
MATCH (metric:Metric)<-[:CONCERNS_METRIC]-(issue:Issue)-[:FOUND_IN]->(passage:Passage)
WHERE metric.metric_id = $metric_id
  AND NOT issue:NotAttempted
  AND issue.document_id IN document_ids
RETURN issue.issue_id AS issue_id,
       issue.code AS code,
       issue.severity AS severity,
       issue.detail AS detail,
       issue.rejected_claim AS rejected_claim,
       issue.row_label AS row_label,
       issue.concept_ids AS concept_ids,
       issue.lane AS lane,
       metric.metric_id AS metric_id,
       passage.passage_id AS passage_id,
       passage.passage_kind AS passage_kind,
       passage.source_url AS source_url,
       issue.document_id AS document_id,
       issue.document_type AS document_type
ORDER BY issue.severity, issue.code, issue.issue_id
LIMIT $row_limit
"""

#: Every statement above, by the name a trace entry and a test refer to it by. Exported so the
#: structural scan can assert it walked a non-empty, *known* set rather than whatever it
#: happened to find — a scan that discovers its own corpus can pass by discovering nothing.
STATEMENTS: dict[str, str] = {
    "LIST_METRICS": LIST_METRICS,
    "METRIC_NODE": METRIC_NODE,
    "METRIC_HISTORY": METRIC_HISTORY,
    "COMPARE_METRIC_PERIODS": COMPARE_METRIC_PERIODS,
    "FACT_EVIDENCE_FOR_OBSERVATION": FACT_EVIDENCE_FOR_OBSERVATION,
    "FACT_EVIDENCE_FOR_EVENT": FACT_EVIDENCE_FOR_EVENT,
    "PASSAGE_CONTEXT": PASSAGE_CONTEXT,
    "SEARCH_PASSAGES": SEARCH_PASSAGES,
    "EVENTS_IN_WINDOW": EVENTS_IN_WINDOW,
    "COUNTER_EVIDENCE": COUNTER_EVIDENCE,
}

__all__ = [
    "COMPARE_METRIC_PERIODS",
    "COUNTER_EVIDENCE",
    "EVENTS_IN_WINDOW",
    "FACT_EVIDENCE_FOR_EVENT",
    "FACT_EVIDENCE_FOR_OBSERVATION",
    "LIST_METRICS",
    "METRIC_HISTORY",
    "METRIC_NODE",
    "PASSAGE_CONTEXT",
    "SEARCH_PASSAGES",
    "STATEMENTS",
]
