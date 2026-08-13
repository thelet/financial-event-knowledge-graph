"""Every Cypher statement the story agent will ever run, as plain string constants.

Responsibility: the statements, and nothing else. No executor, no result type, no ontology, no
parameter validation — this module holds eleven strings and a docstring per string saying what
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

**`:Issue`, `FOUND_IN` and `CONCERNS_METRIC` appear in exactly one tool's two statements** —
`COUNTER_EVIDENCE` and the `COUNTER_EVIDENCE_SCOPE` that D5 added to it — and both exclude
`:NotAttempted`. 10,852 of 17,130 issues record a question that was never asked; surfacing them
would report the run's own bounds as a finding about Opendoor.

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

**The table-cell indices are returned because a label pair cannot name a cell**
(TABLE_CELL_CITATIONS §1.3). `row_label` and `column_label` were the only structural fields
carried, and they do not locate anything: `quoted_text` is a bare cell value that occurs more
than once in its own passage for **523 of 2,704** observations, worst case **32** times, and
exactly once for the other 2,181 *(re-measured live 2026-08-13 by splitting `Passage.text` on
the quote; zero quotes are absent from their passage)*. `:Observation`
already carries `row_index`, `value_column_index`, `period_header_row_index`,
`period_header_column_index` and `metric_label_row_index` on **2,690 of 2,690** table-backed
rows and on **none** of the 14 narrative ones *(verified live 2026-08-13)*, so the two statements
that read an observation's cell — `METRIC_HISTORY` and `FACT_EVIDENCE_FOR_OBSERVATION` — return
all five. They are node properties added to an existing `RETURN` list: no new pattern, no new
hop, no new relationship type.

`value_column_index` and `period_header_column_index` are deliberately **both** returned rather
than one inferred from the other. `$` signs and blank spacer cells push a period header out of
the column its value sits in, and the two indices differ on **2,125 of the 2,690** table-backed
rows *(verified live 2026-08-13)* — so a consumer that read the header at `value_column_index`
would name the wrong column four times in five.
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
#:
#: **The five cell indices are read here and not only from the evidence statement**, because
#: they are `:Observation` properties and this is the tool that loads observations whole
#: (`canonicalization.load_observations` pages every one of the 2,704). `get_fact_evidence` is
#: called per observation and is optional on that path, so a series loaded without evidence
#: would otherwise carry no cell identity at all.
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
       observation.row_index AS row_index,
       observation.value_column_index AS value_column_index,
       observation.period_header_row_index AS period_header_row_index,
       observation.period_header_column_index AS period_header_column_index,
       observation.metric_label_row_index AS metric_label_row_index,
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
#:
#: **`passage.passage_kind` is what tells a table row from a narrative one**, and it was already
#: returned here before the cell indices were: `'table'` on all 2,690 table-backed evidence rows
#: and `'narrative'` on all 14 others *(verified live 2026-08-13)*. It agrees with the indices
#: exactly, which is why nothing downstream has to infer one from the other.
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
       observation.row_index AS row_index,
       observation.value_column_index AS value_column_index,
       observation.period_header_row_index AS period_header_row_index,
       observation.period_header_column_index AS period_header_column_index,
       observation.metric_label_row_index AS metric_label_row_index,
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

#: `get_passage_context`, bound 7 (`before` ≤ 3 + anchor + `after` ≤ 3). Two index seeks and
#: nothing else: `passage_key` for the anchor, and `passage_key` again for the ≤ 7 neighbour ids.
#:
#: **Ordinality is parsed out of the id because no property carries it.** A `:Passage` has 17
#: properties and none is a sequence number *(checked live)*; `passage_id` is
#: `<document_id>#p<n>` on all 8,776 nodes, and `document_id + '#p' + split(id,'#p')[1] == id`
#: holds for every one of them — measured, because an id scheme that is nearly regular is worse
#: than one that is not. Lexicographic ordering would put `#p100` between `#p1` and `#p11`, so
#: the integer is what the window is computed over.
#:
#: **D8: the neighbour ids are computed, not searched for.** The first version sought every
#: passage of the anchor's document on `psg_document` and then applied a non-indexable
#: `toInteger(split(...))` filter to each, which is linear in document size — 968 db hits for 7
#: rows on the 453-passage `open-20201231.htm` *(PROFILE, 2026-08-03)*. The grammar above is
#: total, and re-measured to be so: 8,776/8,776 passages split into exactly two parts,
#: reconstruct exactly, and round-trip through `toInteger` without changing. So `graph_tools`
#: builds `<document_id>#p<n>` for each `n` in the window and this statement looks them up on
#: the `passage_key` uniqueness constraint: **113 db hits for the same 7 rows**, and the count
#: no longer depends on how long the document is. `neighbour.document_id = anchor.document_id`
#: stays as a belt-and-braces guard — the constructed ids cannot name another document, and a
#: statement that relied on that silently would be one refactor away from crossing one.
#:
#: **Neighbouring here means neighbouring among the *cited* passages.** The graph holds 8,776
#: of the corpus's 12,442 passages, so the passage before `#p120` may be `#p118`. Rather than
#: imply contiguity, every row carries its own `passage_index` and its
#: `offset_from_anchor`, and a gap between them is a gap a reader can see. That is the fourth
#: shape fact, made legible instead of assumed away. A constructed id for a passage the graph
#: does not hold simply matches nothing, which is the same clamp the range filter gave.
PASSAGE_CONTEXT = """
MATCH (anchor:Passage)-[:PART_OF]->(document:Document)
WHERE anchor.passage_id = $passage_id
WITH anchor, document, toInteger(split(anchor.passage_id, '#p')[1]) AS anchor_index
MATCH (neighbour:Passage)
WHERE neighbour.passage_id IN $neighbour_passage_ids
  AND neighbour.document_id = anchor.document_id
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
#: **D7: there is an inner `limit`, and the reason the first version left it off was measured
#: to be the smaller problem.** Without it the statement is unbounded in the corpus: `["the"]`
#: matches 6,873 of 8,776 passages, the `PART_OF` expand runs on every one of them *before* the
#: filter, and `ORDER BY score DESC` forces a `Top` that drains the whole stream — **45,972 db
#: hits for 26 rows**, and 38,787 for a `document_types` filter that matched nothing *(PROFILE,
#: 2026-08-03)*. With `{limit: $inner_limit}` at 500 the same call costs **3,430**, the empty
#: filter costs 2,618, and the 26 returned passages are byte-identical for `the`, `margin`,
#: `Adjusted EBITDA` and the earnings-release-plus-window filter the live suite runs. What the
#: bound buys is not the 13× — it is that the cost stops being linear in corpus size.
#:
#: **What the inner limit costs, measured rather than argued.** It is applied before the
#: document filter, so a filter whose matches all rank below the pool is starved: `["the"]`
#: restricted to `shareholder_letter` (582 of 8,776 passages) returns 26 rows unbounded and
#: **9** at 500. That is real, and it is why `candidate_pool_size` is a returned field —
#: `candidate_pool_size == $inner_limit` says the ranking pool was capped and fewer rows may be
#: a consequence of the cap rather than of the corpus. Raising the limit does not remove the
#: effect (the pool would have to reach ~1,445 for that one query), so it is reported instead
#: of hidden. §11's silent-omission channel is the orchestrator's to rule on; this statement's
#: job is to make the omission visible and the scan finite.
#:
#: The `collect`/`UNWIND` round trip exists only to count the pool, and it is free — **3,430 db
#: hits either way** for `["the"]`, measured against the same statement with
#: `WITH node AS passage, score` in its place. *(An earlier draft of this comment claimed it was
#: cheaper, on a measurement taken with a shortened `RETURN` list; the saving was the missing
#: fields, not the `collect`.)* It is bounded by `$inner_limit` by construction.
#:
#: **The window is on `filing_date`, not `report_date`.** `filing_date` is on all 185
#: documents and `report_date` on 184 (C2), so filtering on `report_date` would drop one
#: document from every dated search without saying so. Both are returned.
#:
#: `left(passage.text, $excerpt_chars)` bounds the text at §10.2.1's excerpt radius while
#: `char_count` reports the real length, so a truncated excerpt is visibly truncated. Full text
#: is `get_passage_context`'s job.
SEARCH_PASSAGES = """
CALL db.index.fulltext.queryNodes('passage_text', $lucene_query, {limit: $inner_limit})
     YIELD node, score
WITH collect({passage_node: node, passage_score: score}) AS candidates
WITH candidates, size(candidates) AS candidate_pool_size
UNWIND candidates AS candidate
WITH candidate.passage_node AS passage, candidate.passage_score AS score, candidate_pool_size
MATCH (passage:Passage)-[:PART_OF]->(document:Document)
WHERE ($document_types IS NULL OR document.document_type IN $document_types)
  AND ($since IS NULL OR document.filing_date >= $since)
  AND ($until IS NULL OR document.filing_date <= $until)
RETURN passage.passage_id AS passage_id,
       score AS score,
       candidate_pool_size AS candidate_pool_size,
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
#: **D5: zero rows is an `Ok` only when the scope exists.** The first `MATCH` is what makes
#: `document_ids` — so a metric with no observation of this period has `document_ids = []` and
#: *every* recorded refusal is excluded by construction. Nine of the 26 metrics have no
#: observation at all, and six of those are the ones the refusals are about: `revenue` has 170
#: attempted issues and 0 observations *(verified live 2026-08-03)*, which is to say it has no
#: number **because** of them. `Ok([])` there reads as "this filing refused nothing", which is
#: the most misleading answer this tool could give. `COUNTER_EVIDENCE_SCOPE` below tells the two
#: apart and `graph_tools` runs it only when this one returns nothing.
#:
#: **D6: `ORDER BY issue.severity` sorted the strongest evidence to the bottom.** The vocabulary
#: is `rejection` (49), `refusal` (6,228) and `diagnostic` (1) *(counted live, excluding
#: `:NotAttempted`)*, and alphabetically `rejection` sorts last — so `housing_inventory_homes`
#: 2023-03-31, which has 34 refusals and 2 rejections in scope, returned 25 refusals and
#: dropped both rejections. The rank below is explicit and most-severe-first, which also makes
#: truncation safe to describe: a dropped issue can never outrank a returned one, so
#: `graph_tools` can say what the bound cost without a second query. `severity_rank` is returned
#: beside `severity` so the caller can see the order it was given rather than infer it.
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
       CASE issue.severity
           WHEN 'rejection' THEN 0
           WHEN 'refusal' THEN 1
           WHEN 'diagnostic' THEN 2
           ELSE 3
       END AS severity_rank,
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
ORDER BY severity_rank, issue.code, issue.issue_id
LIMIT $row_limit
"""

#: The scope `find_counter_evidence` needs to read its own empty result (D5), and the **second**
#: statement that names `:Issue` — both belong to the one tool §9 allows near the label, and
#: both exclude `:NotAttempted`.
#:
#: Run only when `COUNTER_EVIDENCE` returns nothing, so the ordinary path is still one query. It
#: answers the question `Ok([])` cannot: was the document scope empty? `period_observation_count`
#: is the scope itself; `metric_observation_count` separates "this metric is unmeasured in the
#: run" from "this period of it is"; `recorded_issue_count` is the number the caller most needs
#: in the first case — `revenue`'s 170.
#:
#: Every hop is a property lookup on `obs_metric`/`obs_period` or the `metric_key` index except
#: the last, which is the same `CONCERNS_METRIC` traversal the statement above makes: 611 db
#: hits for `revenue` *(PROFILE, 2026-08-03)*. Three `OPTIONAL MATCH`es rather than three
#: queries because each aggregates to one row before the next begins, so the result is exactly
#: one row whatever the graph holds — no `LIMIT` could have promised that.
COUNTER_EVIDENCE_SCOPE = """
MATCH (metric:Metric)
WHERE metric.metric_id = $metric_id
OPTIONAL MATCH (observation:Observation)
WHERE observation.metric_id = $metric_id AND observation.period_key = $period_key
WITH metric, count(observation) AS period_observation_count
OPTIONAL MATCH (any_observation:Observation)
WHERE any_observation.metric_id = $metric_id
WITH metric, period_observation_count, count(any_observation) AS metric_observation_count
OPTIONAL MATCH (metric)<-[:CONCERNS_METRIC]-(issue:Issue)
WHERE NOT issue:NotAttempted
RETURN metric.metric_id AS metric_id,
       period_observation_count AS period_observation_count,
       metric_observation_count AS metric_observation_count,
       count(issue) AS recorded_issue_count
ORDER BY metric_id
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
    "COUNTER_EVIDENCE_SCOPE": COUNTER_EVIDENCE_SCOPE,
}

__all__ = [
    "COMPARE_METRIC_PERIODS",
    "COUNTER_EVIDENCE",
    "COUNTER_EVIDENCE_SCOPE",
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
