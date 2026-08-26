# 05 — Research Agent feasibility

*What already exists that could become a tool for a tool-using research agent, read off the
repository rather than invented. Nothing built. Verified 2026-08-26.*

---

## 1. Answer first

**A typed tool layer already exists, is already read-only, is already bounded, and is already
traced.** `story/contracts.py:72` declares a `GraphRetriever` protocol whose entire surface is:

```python
def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult: ...
def trace(self) -> Sequence[RetrievalTraceEntry]: ...
```

`BoundedGraphRetriever` implements it over **nine** named tools with declared parameter sets and
per-tool row caps, plus **three** tools that are declared and answer `Unavailable` with a reason.
Its docstring states the property a research agent needs: *"No caller passes Cypher: `tool` names
one of §9's code-owned statements and `parameters` are bound, never interpolated, so a model
cannot widen a query by choosing a string."*

**A Research Agent would not need raw Neo4j access, and could not be given it without deleting
an enforced architectural rule.**

---

## 2. The nine tools that exist today

Read from `graph_tools.MAX_ROWS` and `TOOL_PARAMETERS` (verified):

| Tool | Required params | Optional params | Row cap |
| --- | --- | --- | --- |
| `list_metrics` | — | — | 26 |
| `get_metric_definition` | `metric_id` | — | 1 |
| `get_metric_history` | `metric_id` | `shape`, `since`, `until` | 200 |
| `compare_metric_periods` | `metric_id`, `period_key_a`, `period_key_b` | — | 2 |
| `get_fact_evidence` | — | `observation_id`, `event_id` | 10 |
| `get_passage_context` | `passage_id` | `before`, `after` | 7 |
| `search_passages` | `terms` | `document_types`, `since`, `until` | 25 |
| `get_events_in_window` | `since`, `until` | — | 50 |
| `find_counter_evidence` | `metric_id`, `period_key` | — | 25 |

Declared-and-unavailable, each with a named reason (`UNAVAILABLE_TOOLS`):
`get_guidance_history` → `no_guidance_lane`, `compare_guidance_to_actual` → `no_guidance_lane`,
`get_price_reaction` → `no_market_data_lane`.

An unknown tool name returns `refused("unknown_tool", …)` naming the nine. Unknown or missing
parameters return `refused("unknown_parameter" | "missing_parameter", …)`. The result vocabulary
is closed: `ok`, `not_found`, `ambiguous`, `unavailable`, `refused` (`retrieval/results.py`).

**Every call is traced** — tool, parameters, row count, truncation flag, elapsed ms, outcome —
including refusals, *"because 'the model asked for guidance three times and was told no' is
exactly the sort of thing §10's provenance is for"*.

---

## 3. Existing capability → tool mapping

The brief's candidate tool list, against what the repository actually has.

| Brief's tool | Existing function / module | Current consumer | Tool-shaped already? |
| --- | --- | --- | --- |
| `get_fact` | `BoundedGraphRetriever._get_fact_evidence` (`get_fact_evidence`) | `detection/canonicalization.py:305`, `packaging/evidence_package.py:1516` | **Yes** |
| `get_metric_history` | `BoundedGraphRetriever._get_metric_history` | `canonicalization.py:383` | **Yes** |
| `get_source_passages` | `_get_passage_context`, `_search_passages` | packaging | **Yes** — two tools, `passage_window_ids` for windows |
| `get_related_events` | `_get_events_in_window` | packaging | **Yes** |
| `get_related_metrics` | `_list_metrics`, `_get_metric_definition` | `canonicalization.py:286`, packaging | **Yes** |
| `get_graph_neighbours` | **does not exist** | — | **No** — and deliberately: `test_no_cypher_statement_contains_a_variable_length_path` forbids it |
| `compute_absolute_change` | `derivation/operations.py:124` `absolute_change` | `execute_all` | **Almost** — takes a `ValidatedDerivation`, not two ids |
| `compute_percent_change` | `operations.py:153` `percentage_change` | `execute_all` | Almost |
| `compute_percentage_point_change` | `operations.py:180` `percentage_point_change` | `execute_all` | Almost |
| `check_period_order` | `core/periods.py:180` `months_between(earlier, later)` | `series`, detectors | **Almost** — a plain function, not on a tool surface |
| *(not in the brief, but exists)* | `compare_metric_periods` | packaging | **Yes** |
| *(not in the brief)* | `find_counter_evidence` | packaging | **Yes** |
| *(not in the brief)* | `offers(package, candidate)` | `run_demo` | **Yes in spirit** — "what may legally be computed here" |
| *(not in the brief)* | `detector_config.quantity_direction(metric_id, delta)` | detectors, `execute_all` | **Yes** — a pure function returning a closed word |
| *(not in the brief)* | `series.comparable(a, b, authority)` | derivation, detection | **Yes** — R1–R10 with rule ids |
| *(not in the brief)* | `renderings.metric_surfaces`, `period_surface*`, `legal_renderings` | slot table, prompts | **Yes** — the display layer |

**The gap between "exists" and "tool-shaped" is narrow and specific.** The seven derivation
operations all take a `ValidatedDerivation` — a type minted by `offers.validate()` after R1–R10
comparability has been checked. A tool-facing wrapper would take two `obs:` ids and an operation
name and return either an `OperationOutcome` or one of the 11 `DerivationRefusalCode`s. That is
exactly what `execute()` already does (`execute.py:92`); it just is not exposed under a `call`
interface.

---

## 4. Graph access boundary

### Would a Research Agent need raw Neo4j / Cypher?

**No.** Four independent reasons, each enforced by a passing test.

1. **All 11 statements are module-level string constants.**
   `test_every_cypher_statement_in_the_package_is_fixed_at_import_time` and
   `test_every_retrieval_statement_is_one_string_constant_with_no_interpolation_at_all`.
2. **No write clause can exist anywhere in the package.**
   `test_no_cypher_statement_contains_a_write_clause` and
   `test_no_string_constant_anywhere_in_the_package_could_be_a_write_statement`, scanning for
   `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH`, `REMOVE`, `DROP`, `LOAD CSV` — plus a
   meta-test (`test_the_write_clause_scan_catches_both_spellings_d4_found_it_missing`) that the
   scan itself is not vacuous.
3. **Every statement is bounded, ordered and parameterised.**
   `test_every_retrieval_statement_is_bounded`, `…orders_by_a_unique_key_before_it_limits`,
   `…binds_its_limit_to_a_parameter`. Labels and relationship types are on allowlists. No APOC.
   No variable-length paths. No whole-node returns.
4. **The driver is reachable from exactly one module.**
   `test_the_driver_is_named_only_in_the_story_owned_connection_module`,
   `test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage`.

Handing a model Cypher would defeat all four at once. The alternative — **typed deterministic
tools** — is not a proposal here; it is the design already in the repository.

### The layers, named

| Layer | Module | What it owns |
| --- | --- | --- |
| Driver / connection | `story/providers/neo4j_connection.py` | the only module naming the driver |
| Query layer | `story/stages/retrieval/cypher.py` | 11 frozen statements |
| Tool layer | `story/stages/retrieval/graph_tools.py` | 9 tools + 3 declared-unavailable, caps, trace |
| Result vocabulary | `story/stages/retrieval/results.py` | `ok` / `not_found` / `ambiguous` / `unavailable` / `refused` |
| Metric resolution | `story/stages/retrieval/metric_metadata.py` | surface → `metric_id` against the ontology registry |
| Evidence retrieval | `story/stages/packaging/evidence_package.py` | assembles a package under §10.2 budgets |
| Passage retrieval | `packaging/passage_excerpts.py`, `passage_quality.py`, `section_bounds.py` | excerpting, quality, bounds |
| Metric history | `story/stages/ranking/metric_history.py`, `story/core/series.py` | canonical series, R1–R10 |
| Event retrieval | `get_events_in_window` | events in a date window |
| Relationship retrieval | `PackagedRelationship` on the package | **capped at 4**; no traversal tool exists |
| Deterministic derivation | `story/stages/derivation/` | offers, validation, 7 operations, 11 refusals |
| Rendering | `story/core/renderings.py` | figures, metric surfaces, period surfaces, direction phrases |

---

## 5. What a Research Agent would actually have to decide

Read from what the downstream stages **require** rather than from what would be nice.

`plan_violations` + `execute_all` + `slot_table` + `writer_prompt` + `DeterministicVerifier`
between them need exactly this from a semantic authority:

| Field | Needed because | Could code supply it instead? |
| --- | --- | --- |
| `thesis` (prose) | it is the post's claim; `THESIS_EMPTY` requires it; the writer prompt prints it | **No** |
| `fact_ids_to_use` | `_thesis_violations` compares the draft against it; the writer's slot rows come from the package but the *plan* bounds what must appear | **No** — bounded by the package, chosen editorially |
| `angle` / `why_it_matters` | printed to the writer; nothing validates it | **No** |
| `counterpoint_fact_ids` | `COUNTERPOINT_UNGROUNDED` requires grounding in `counter_evidence` | **No** — the set is bounded, the choice is not |
| `uncertainty` | printed to the writer | **No** |
| `causal_claim_policy` | **already code's** — `causal_language_for(package)`, pinned to a one-member enum | **Already** |
| which derivations to request | `execute_all` needs the requests; `offers()` bounds them | **Partly** — as an index into the offer list, not a triple |
| `required_warnings` | §13 refuses a post that drops one | **Yes** — `claim_qualifying_warnings(package)` already computes the admissible set |

**So the minimum semantic output is roughly five prose/selection fields**: thesis, why it
matters, the fact ids to build on, the counterpoint's grounding, and the uncertainty. Everything
else the current `EditorialPlan` carries — `causal_language`, `required_warnings`,
`required_citation_passage_ids`, `statement_class`, the derivation triples' spelling — is
already or could be code's.

That is **five fields versus the nineteen the planner schema asks for today** — the same kind of
reduction the writer schema went through on 2026-08-23, from 8 leaves to 4 (and from 15 at its
1.1.0 peak).

> **Not locked.** This is an inventory of what downstream code demands, not a schema proposal.
> Two open questions it does not answer: whether a research agent needs to *carry* the
> retrieval trace into the brief (the package already carries one), and whether `structure[]`
> and `prohibited_claims[]` — both currently unvalidated and observably unreliable — should
> exist at all.

---

## 6. What a Research Agent would reuse unchanged

| Component | Reusable? | Note |
| --- | --- | --- |
| The graph, projection and freshness gate | **Yes, unchanged** | nothing about them is planner-shaped |
| The nine retrieval tools | **Yes, unchanged** | already a `call(tool, params)` surface with a trace |
| `StoryEvidencePackage` | **Yes** — but its role changes | today it is *"the model's entire universe"*, pre-computed under a 5,000-token budget. An agent that retrieves for itself makes the package a *result* rather than an *input* |
| `offers()` / the derivation stage | **Yes, unchanged** | it is already the "what may I legally compute" tool |
| `slot_table` + `compile_draft` | **Yes, unchanged** | they consume a package, derived facts and passages — not a plan's provenance |
| `DeterministicVerifier` | **Yes, unchanged** | it verifies a `Draft` against a package; it does not care who planned it |
| `GenerationStore` / `request_identity` | **Yes** — with a caveat | a multi-turn tool loop issues N calls per run; `request_identity` keys each one independently, which works, but `_token_totals` and `_call_site_provenance` in `pipeline.py` assume **two** results |
| `EditorialPlan` as a type | **Partly** | six of its nineteen fields are things code should own |
| The demo UI's trace timeline | **Partly** | `STAGES_BY_PHASE` has 14 fixed generation stages; a variable-length tool loop does not fit that shape |

**The honest summary: the retrieval and verification halves of this system are already built for
an agent. The planner-shaped middle is the part that would change.**

---

## 7. The one structural mismatch worth naming now

`story/pipeline.py` records provenance for **exactly two model calls**: `planner_provider_model`
and `writer_provider_model` in the manifest, `_token_totals(results, package)` over a list, and
`_call_site_provenance(provider, result, …)` per call site. `DISPOSITIONS` names stages, not
turns. `REFUSING_STAGE` maps a disposition to one stage name.

None of that is hard to widen, and none of it is a reason not to build an agent. It is named
here because it is the part of the current code that assumes the *shape* of the current
architecture rather than its *contents* — and it is the same code a repair loop would also have
to widen.
