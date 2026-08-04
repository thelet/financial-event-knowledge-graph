# Implementation steps — story agent V1

The single ledger for the V1 vertical slice. One row per step, updated after every accepted
step. Authoritative plan: [V1_STORY_AGENT.md](V1_STORY_AGENT.md). Boundary:
[WORKSTREAM_BOUNDARY.md](WORKSTREAM_BOUNDARY.md).

**Worktree** `C:\Users\thele\Projects\FKG-story-agent-impl` · **branch** `impl/story-agent-v1` ·
**base** `13b1f23` (code byte-identical to `main` `edc2d5a`, plus the two plan documents).

**Nothing is pushed. Nothing is merged. No upstream file is edited.**

---

## 0. State this implementation is built on *(verified 2026-08-03, before any code)*

| | |
| --- | --- |
| graph run | `graph-v1-0483dc6b4b10`, projection `1.2.0` |
| extraction run | `extract-v1-lexical-833f7bcfbce9` |
| freshness | recorded `1cc8f7b01c040531…` == actual `1cc8f7b01c040531…` — **fresh** |
| ontology | `real_estate_marketplace_v1` `2.0.0` / `bb94f522ba122470…` |
| observations / claims / events / relationships | 2,704 / 2,714 / 6 / 4 |
| export nodes / edges | 28,836 / 35,600 |
| Neo4j nodes / edges | 28,837 (incl. `:GraphLoad`) / 35,600 |
| `graph verify` | **28 PASS / 0 FAIL** |
| extraction self-verification | 5/5 pass |
| existing `story/` code | none — greenfield |

---

## 1. Scope

**In:** freshness gate → bounded read-only retrieval → deterministic detection → deterministic
ranking → bounded evidence package → schema-constrained planning → constrained drafting →
deterministic verification → advisory verification → accepted/rejected artifacts, driven by a
CLI, proven end-to-end on the 2022Q3 cluster with recorded model responses.

**Out, and not to be started:** LightRAG, `neo4j-graphrag`, LlamaIndex, Graphiti, Microsoft
GraphRAG, embeddings, vector indexes, generated Cypher, unrestricted traversal, autonomous
agents, chat UI, automatic publishing, model-selected evidence, guidance detectors,
guidance-vs-actual, stock reaction, transcript ingestion, peer/market divergence,
entity-network stories, factual-spine ingestion, and any mutable LLM output inside the
authoritative graph.

**No new dependency.** `neo4j`, `httpx`, `pydantic`, `PyYAML`, `pytest` only. A dependency
proposal is a founder gate and stops implementation.

---

## 2. Ownership

| Owned (writable) | Read-only |
| --- | --- |
| `story/` · `config/story.yaml` · `tests/story/` · `plans/llm-agent/` | `acquisition/` `normalization/` `extraction/` `ontology/` `graph/` `benchmarks/` `docs/` `config/*.yaml` (except `story.yaml`) `tests/` (except `tests/story/`) `pyproject.toml` `compose.yaml` `.env` `data/` |

`story/` may import only: `ontology`, `ontology.core.*`, `ontology.contracts`,
`extraction.core.*`, `graph.core.*`, `graph.contracts`. **Never** `extraction.stages.*`,
`extraction.providers.*`, `extraction.contracts`, `normalization.*`, `acquisition.*`,
`graph.stages.*`. Upstream must never import `story`.

Forbidden module names: `service.py` `implementation.py` `utils.py` `helpers.py` `common.py`
`misc.py` `base.py`.

---

## 3. Dependency graph

```text
S0 contracts ── S0c neo4j adapter ──┬── S0b freshness ──┐
      │              (providers/)   │                    │
      │                             └── S1 retrieval ────┼── S2 series ── S3 detectors ──┐
      │                                                  │                                │
      └── S6 model providers ──────────────────────────  ┘        S4 ranking ── S5 packaging ─┐
                                                                                              │
                            S7 planner ── S8 writer ── S9 det. verifier ── S10 advisory ──────┤
                                                                                              │
                                                                     S11 pipeline+CLI ── S12 spike
```

S0c exists because D1 put the driver in `story/providers/`, injected through a protocol. Both
S0b and S1 consume that protocol, so neither may construct a driver and both wait on S0c.

**Parallel waves actually used** are recorded per step in §4 and summarised in §6.

---

## 4. Steps

Legend — status: `PLANNED` · `RUNNING` · `REVIEW` · `ACCEPTED` · `BLOCKED`.

### S0 — Package skeleton and frozen contracts

| | |
| --- | --- |
| **Goal** | Create `story/`, freeze every V1 contract type, and enforce the structural rules by test. |
| **Depends on** | — |
| **Inputs** | Plan §5, §6.4, §6.11, §10, §11, §12, §13, §14. Upstream: `extraction.core.identifiers.digest`, `extraction.core.models.PeriodRef`, `ontology` loader, `graph.core.manifest`. |
| **Outputs** | `StoryCandidate`, `CandidateScore`, `EvidenceRequest`, `StoryEvidencePackage`, `FactBinding`, `CitationHandle`, `EditorialPlan`, `DraftSentence`/`Draft`, `VerificationFinding`, `VerifiedDraft`, `RejectedDraft`, `StoryRunManifest`; protocols `StoryDetector`, `GraphRetriever`, `StoryGenerationProvider`, `DraftVerifier`; `candidate_id`/`package_id`/`story_run_id`. |
| **Owns** | `story/__init__.py` `story/contracts.py` `story/core/{__init__,models,keys,manifest}.py` `tests/story/{__init__.py,conftest.py,test_story_package_structure.py,test_story_contracts.py,test_story_keys.py}` |
| **Read-only** | everything else |
| **Parallel** | no — everything depends on it |
| **Tests** | structure guards (forbidden imports, catch-all names, import direction, `graph/` never imports `story`); id determinism incl. input-reordering; serialization round-trip; hash stability |
| **Acceptance** | ids stable and order-independent; `detector_version`/`policy_version` bump mints a new id; no free-text thesis on `StoryCandidate`; score is a separate type; package carries graph run id + input digest + version; all structure tests pass |
| **Rollback** | delete `story/` and `tests/story/` |
| **Commit** | one |
| **Risks** | contract churn later; mitigated by freezing names here and treating a change as a versioned break |
| **Status** | **ACCEPTED** 2026-08-03 |
| **Commit hash** | `9603cb9` |
| **Delivered** | 10 files, 3,653 lines, 40 contract types; `ReadQueryExecutor` folded in mid-flight per D1 |
| **Orchestrator validation** | ownership: 0 files outside `story/`+`tests/story/` · `167 passed` · full offline `2890 passed` = 2,723 baseline + 167, **0 new failures** · **5 independent mutation checks**: `neo4j` in `core/`→3 failures, extra import in `contracts.py`→1, `numpy` anywhere→2, `graph/` importing `story`→1, `extraction.stages` in `story/`→2 · id order-independence and version-bump behaviour re-derived from the code, not the report |

### S0c — Neo4j read adapter *(added by D1)*

| | |
| --- | --- |
| **Goal** | The one story module that imports `neo4j`. Read-only by construction. |
| **Depends on** | S0 (the `ReadQueryExecutor` protocol) |
| **Inputs** | Boundary §4.1. Behaviour and `.env` conventions of `graph/stages/load/connection.py` — **reused, never imported**. |
| **Outputs** | `story/providers/neo4j_connection.py` satisfying the protocol; constructed in `story/context.py`. |
| **Owns** | `story/providers/{__init__,neo4j_connection}.py` `story/context.py` `tests/story/test_story_neo4j_adapter.py` |
| **Implements** | URI/user/password/database config · repository-consistent `.env` layering · driver creation · connectivity verification · lifecycle and close · parameterised read statements with an explicit timeout |
| **Must not** | create schema or indexes · wipe or replace · load · issue any write · carry load-batch settings · migrate |
| **Parallel** | no — S0b and S1 both wait on it |
| **Tests** | fake executor for normal tests; one `neo4j`-marked live test for connectivity and a real parameterised read; structural test that `neo4j` is imported nowhere else in `story/`; no write keyword in any story Cypher |
| **Acceptance** | rows cross the boundary as plain dicts — no `neo4j.Record`/`Node` reaches a stage; timeout on every statement; no retrieval tool constructs a driver |
| **Status** | **ACCEPTED** 2026-08-03 |
| **Commit hash** | `9e798b3` |
| **Delivered** | 1,458 lines; `Neo4jReadExecutor`, `StoryNeo4jSettings`, `StoryContext`, 5 typed errors, `plain_row`/`plain_value` |
| **Orchestrator validation** | `222 passed` story · `5 passed, 0 skipped` neo4j-marked · offline `2940 passed` (2,890 before) · **4 mutation checks on the widened exemption**: `core/` importing the adapter → 3 failures, a *stage* importing it → 3, all six `neo4j` imports removed → 7 · **drove the real adapter against the live graph myself**: evidence chain resolves, `quote='(211)'` read off the `EVIDENCED_BY` edge (C3), every returned value is a `builtins` type, `heading_path` arrives as a plain `list`, non-positive timeout raises `ValueError`, and a `CREATE` is refused by the server |
| **Scope deviation** | edited `tests/story/test_story_package_structure.py`, outside its declared `Owns`. **Flagged by the agent, reviewed and accepted** — see below |

### S0b — Freshness gate

| | |
| --- | --- |
| **Goal** | Refuse to proceed when the graph does not match its inputs. Ships before any retrieval. |
| **Depends on** | S0, S0c |
| **Inputs** | Plan §7, §13.13. Graph manifest `inputs.run_complete_sha256`, `:GraphLoad` marker, node/edge counts. |
| **Outputs** | `FreshnessReport(checks, passed)`, `GraphIdentity`, typed refusal codes `package_input_digest_mismatch`, `graph_run_id_mismatch`, `load_incomplete`, `count_mismatch`, `ontology_hash_mismatch`. |
| **Owns** | `story/stages/freshness/` `story/core/graph_identity.py` `tests/story/test_story_freshness.py` |
| **Note** | consumes the injected executor; **constructs no driver** (D1) |
| **Parallel** | no (S0 first); after S0 it may run beside S1/S6 |
| **Tests** | current run passes; synthetic stale fixtures refuse with the right code; incomplete load refuses; count mismatch refuses; **no write statement anywhere** |
| **Acceptance** | digest-based, not id-based; structured refusal not exception; zero graph writes; marked `neo4j` where it touches the DB, with an offline fixture path |
| **Rollback** | delete the stage |
| **Commit** | one |
| **Status** | **ACCEPTED** 2026-08-03 |
| **Commit hash** | `54a4287` |
| **Delivered** | 1,966 lines; 13 checks, 8 refusal codes, 46 tests |
| **Orchestrator validation** | scope: all 8 files inside the workstream tree · `46 passed` · **read the digest-vs-id test in full** — it stages a run regenerated in place, asserts an explicit `id_only_gate()` returns `[]` *first*, then asserts the real gate refuses with both digests named. That is the strongest form of that test · marker count verified live: marker `node_count`=28,836, live total 28,837, live excluding `:GraphLoad` = 28,836, so the exclusion is correct and a naive total would false-positive |

### S1 — Safe retrieval layer

| | |
| --- | --- |
| **Goal** | Code-owned, bounded, read-only, parameterised Cypher tools. Owns every Cypher string; **imports no driver** — the executor is injected (D1). |
| **Depends on** | S0, S0c |
| **Inputs** | Plan §9, §16. Live label/property inventory from recon. |
| **Outputs** | `list_metrics`, `get_metric_definition`, `get_metric_history`, `compare_metric_periods`, `get_fact_evidence`, `get_passage_context`, `search_passages`, `get_events_in_window`, `find_counter_evidence`; result types `Ok`/`NotFound`/`Ambiguous`/`Unavailable`/`Refused`. |
| **Owns** | `story/stages/retrieval/` `tests/story/test_story_retrieval*.py` |
| **Parallel** | **yes** — wave A with S6 |
| **Shape facts** | C2 sparse properties · C3 `quoted_text` on the `EVIDENCED_BY` edge · C4 ontology authoritative for metric metadata |
| **Tests** | exact rows; row bounds + `truncated`; deterministic ordering; Lucene escaping incl. reserved words; structural scan proving no write keyword and no f-string in any Cypher constant; missing observation/passage; ambiguous period; timeout applied to every statement |
| **Acceptance** | no interpolation, no dynamic labels, no variable-length path, no APOC, hard `LIMIT`, timeout on every statement, named return fields, `OBSERVATION_OF_SUBJECT` never traversed outward, `:NotAttempted` excluded |
| **Rollback** | delete the stage |
| **Commit** | one |
| **Review** | adversarial retrieval reviewer before acceptance |
| **Status** | **ACCEPTED (pending R1 + adversarial review)** 2026-08-03 |
| **Commit hash** | `99ed12a` |
| **Delivered** | 3,056 lines across 8 files; 9 tools + 3 `Unavailable` stubs; 65 offline + 24 neo4j + 239 structural-scan cases |
| **Orchestrator validation** | scope: 8 files, all its own · import cycle **resolved** (structure suite 168 passed) · found and correctly refused to paper over a defect in accepted work (see R1) |
| **Notable** | `OBSERVATION_OF_SUBJECT` appears in **no statement at all** — stronger than the rule I specified; subject identity read from `Observation.subject_entity_id` · truncation is arithmetic, not trust: each query asks `max_rows + 1` and the extra row's arrival is the only evidence of cutting · `find_counter_evidence` joins at **document** grain, measured — passage grain returns 0 rows for the 2022Q3 metrics because the refusals sit in neighbouring tables of the same filing · Lucene reserved *words* quoted case-insensitively, wider than Lucene's own rule, because silent evidence suppression is the costlier mistake |

### R1 — Repair: unbounded read, flaky test *(fresh agent, not a step)*

| | |
| --- | --- |
| **Trigger** | S1 found `story/stages/freshness/loaded_graph.py::LOAD_MARKERS` (S0b, accepted at `54a4287`) has **no `LIMIT`** and no bounding aggregate, violating §16's own rule in the stage that runs before every command. It left the test **failing rather than exempting** a sibling's code — the correct call. Orchestrator confirmed: `graph/stages/load/lifecycle.py:101` states *"Concurrency is out of scope and unguarded: `:GraphLoad` carries no uniqueness constraint"*, and `schema.py` has none. Markers accumulate one per load; exactly one exists today, which is why S0b's own tests passed |
| **Also** | `test_the_timeout_is_applied_by_the_server_and_not_by_the_client` is **flaky** — orchestrator measured 5/5 pass in isolation, fails under full-suite load. It races a 0.001 s server timeout against a possibly-warm plan |
| **Founder constraint** | **Smallest correct fix, no scope expansion.** `LIMIT 2` only if the code already needs to detect more than one marker, else `LIMIT 1`. **No lifecycle redesign, no new marker concepts, no new refusal code.** Current freshness behaviour preserved. **One** focused regression test. For the flake: **no wall-clock race** — a fake/recording driver asserting the configured timeout reaches the query execution call; any live timeout test stays optional and `neo4j`-marked |
| **Owns** | `story/stages/freshness/*` `tests/story/test_story_freshness.py` `tests/story/test_story_neo4j_adapter.py` |
| **Acceptance** | both failures green, nothing else broken, full `tests/story/` green **three consecutive times** |
| **Status** | **ACCEPTED** 2026-08-03 |
| **Commit hash** | `ff95671` |
| **Delivered** | 3 files, +108/−40. `LIMIT 2`, one regression test, wall-clock race deleted |
| **Orchestrator validation** | scope: 3 files · **three consecutive runs `781 passed`** (7.71s/7.27s/7.50s) · `32 passed, 0 skipped` neo4j-marked · verified `LIMIT 2` is the correct minimal bound because `gate.py:272` already branches on `len(loaded.markers) != 1` — `LIMIT 1` would have bounded the read while hiding the exact state the gate refuses on · read the replacement test in full |
| **Re-diagnosis** | **The flake was not load-dependent.** `SHOW SETTINGS` reports `db.transaction.monitor.check.interval = 2s` (verified by me), so the server notices an expired transaction only every two seconds and a 1.4 s statement can finish inside one window untouched. Measured 3 completions in 10 standalone. Plan warmth and load were both red herrings |
| **What the replacement proves** | (a) what reaches `execute_query` is a `neo4j.Query` carrying the caller's exact ceiling — a bare `str` is accepted and the ceiling silently dropped, so this is what makes it transaction metadata at all; (b) a driver taking 50 ms under a 1 ms ceiling still returns rows, so `read` holds no client-side stopwatch. Server-side proven negatively, deterministically |
| **What is lost** | No test that the server *fires* the timeout. A deterministic live version exists (at `upper=200_000_000` the statement runs 6.0 s = three monitor intervals; measured **15/15 terminations, slowest kill 1.47 s**) but it still uses a 1 ms ceiling, which the founder's correction rules out. **Not built. Available on request** |

**Orchestrator note, recorded against myself.** My R1 packet invited the scope creep the founder then had to rule out — it asked the agent to decide whether a multi-marker graph "is itself a fact worth refusing on", which opens a new refusal code and new marker semantics for a defect whose fix is one token. The founder's constraint is narrower and correct. Corrected mid-flight.

### S2 — Canonical series and comparability

| | |
| --- | --- |
| **Goal** | Deterministic fact-slot canonicalisation and the R1–R10 comparability rules. |
| **Depends on** | S0, S1 |
| **Inputs** | Plan §6.1, §6.9. `PeriodRef.key`, ontology `mutually_distinct_groups`, `distinct_from`, formula windows. |
| **Outputs** | `CanonicalPoint`, `CanonicalSeries`, `comparable(A,B) -> Ok|Refuse(reason)`, shape classifier. |
| **Owns** | `story/core/series.py` `story/core/periods.py` `story/stages/detection/canonicalization.py` `tests/story/test_story_series.py` `tests/story/test_story_comparability.py` |
| **Parallel** | no |
| **Tests** | 537 slots / 36 multi-valued / 0 conflict against the current run; quarantine rule; R1–R10 each with a positive and negative case; adjacency rule; formula-straddle refusal |
| **Acceptance** | series matches the plan's §6.2 table recomputed from live data, not copied; typed refusals; no model involvement |
| **Status** | **ACCEPTED** 2026-08-04 |
| **Commit hash** | `b8bbe93` |
| **Delivered** | 3,321 lines, 6 files, 105 tests (84 offline + 21 `neo4j`) |
| **Orchestrator validation** | scope: 6 files, all its own · story `1019 passed` · neo4j `58 passed` (37 + its 21, no sibling test disturbed) · offline `3682 passed` · **verified both structural findings myself against the ontology and the live graph** |
| **Census** | `slots=537 multi_valued=36 multi_cluster=0 ok=537 resolved_by_majority=0 conflict=0 quarantined=0` — **matches the expected figures exactly** |
| **§6.2 drift** | **zero.** Every cell of all four series reproduces, asserted against a transcription of the plan rather than against itself |

### S3 — Deterministic detectors

| | |
| --- | --- |
| **Goal** | `metric_move`, `trend_reversal`, `acceleration`, `cross_metric_divergence` first; then `inventory_risk`, `leadership_change`, `fact_conflict`, `coverage_gap`, `formula_closure_break`. |
| **Depends on** | S2 |
| **Inputs** | Plan §6.5, §6.6, §6.7. |
| **Outputs** | `StoryCandidate` instances with signals, warnings, `EvidenceRequest`. |
| **Owns** | `story/stages/detection/` `tests/story/test_story_detectors*.py` |
| **Parallel** | **yes** — wave B, one agent for move/reversal/acceleration, one for divergence, one for internal guards |
| **Tests** | real fixtures from the current run; the 2022Q3 cluster appears; small-base guard; adjacency; polarity; dedup of correlated lineage |
| **Acceptance** | no free-text thesis; no LLM; no self-ranking; blocked detectors skipped with a reason; ids stable |
| **Review** | adversarial detector reviewer |
| **Status** | **ACCEPTED** 2026-08-04 |
| **Commits** | S3-A `b478864` · S3-B `961d51b` · S3-C `b2303a6` · R3 integration `b910e29` |
| **Delivered** | 4 detectors, ~7,500 lines across 12 files, 130 detector tests |
| **Live census (orchestrator-verified independently)** | `metric_move` **227** · `trend_reversal` **11** · `acceleration` **9** (all shapes) / 7 (quarters) · `cross_metric_divergence` **15** · **total 262 candidates, 262 distinct ids, 0 prose signals** |
| **Spike candidates emitted** | `cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:1503b5b21731` δ=`-429000000.0` crosses_zero · `cand:metric-move:gaap-gross-margin:...:15b62d34dbaa` δpp=`-24.2` crosses_zero · `cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:opendoor:2022Q3:9682f1c1c85a` gap=`15.9` z=`3.945` n=`25` |
| **Suites** | story `1193 passed` ×3 · neo4j `91 passed` · offline `3823 passed` |

### S4 — Ranking and deduplication

| | |
| --- | --- |
| **Goal** | Deterministic scoring, stable ordering, dedup. No model. |
| **Depends on** | S3 |
| **Owns** | `story/stages/ranking/` `tests/story/test_story_ranking.py` |
| **Acceptance** | every component inspectable; `CandidateScore` separate from the candidate; stable tie-break; internal candidates never outrank external for post generation; reproducible |
| **Status** | PLANNED |

### S5 — Bounded evidence-package builder

| | |
| --- | --- |
| **Goal** | Build the model's entire universe, deterministically and within every bound. |
| **Depends on** | S1, S2, S4 |
| **Inputs** | Plan §10, §10.1, §10.2, §10.2.1, §10.3, §13.7.2. |
| **Owns** | `story/stages/packaging/` `tests/story/test_story_evidence_package*.py` |
| **Tests** | every section bounded; token estimate ≤ 6,000; deterministic truncation; package hash stable across rebuilds; citation chain resolves for every fact |
| **Acceptance** | model cannot influence selection; counter-evidence never silently dropped; warnings preserved; serializable and reproducible |
| **Review** | adversarial package reviewer |
| **Status** | PLANNED |

### S6 — Story-owned provider boundary

| | |
| --- | --- |
| **Goal** | OpenAI-compatible provider with strict structured output, distinct system/user messages, recorded-response replay. |
| **Depends on** | S0 |
| **Inputs** | Plan §15.1–§15.3. Restated from `extraction/providers/`, **not imported**. |
| **Owns** | `story/providers/` `tests/story/test_story_provider*.py` |
| **Parallel** | **yes** — wave A with S1 |
| **Acceptance** | no vendor SDK; no hard-coded secret; schema violation never retried; timeout never retried; replay store keyed by request identity; live tests file-wide `live`-marked |
| **Status** | **ACCEPTED** 2026-08-03 |
| **Commit hash** | `1a5d716` |
| **Delivered** | 2,229 lines across 7 files; `StoryOpenAICompatibleProvider`, `GenerationStore`, `portable_schema` |
| **Orchestrator validation** | scope: 0 files outside its 7 paths · `87 passed, 2 skipped` · `pyproject.toml` untouched · **reproduced the digest-collision finding myself** — the naive `\x1f` join gives one digest for `('planner\x1f','write')` and `('planner','\x1fwrite')`; story's does not, and personas separate |
| **Live model server** | **VERIFIED 2026-08-03** after the founder started it. S6's two live tests now **pass** rather than skip: a real schema-constrained generation with `schema_violations(...) == []`, `finish_reason == "stop"`, `attempts == 1`. The orchestrator then ran an independent probe through the provider with real graph values — `+218,000,000` (2022Q2) → `−211,000,000` (2022Q3) — and got `{"direction":"decrease","crossed_zero":true,"magnitude_usd_millions":429}` in 0.92 s, 132 tokens, off `Qwen3.5-9B-Q4_K_M.gguf`. **The wire contract is now proven against a real llama.cpp**, not only `httpx.MockTransport`: the nested `response_format` is honoured and the two-turn `system`/`prompt` split is accepted. *(The `429` is a wire test, not a licence — §13.9 requires a published magnitude to come from a deterministic `Calculation` over two bound observation ids, never from the model.)* |

### S7 — Editorial planner · S8 — Constrained writer

| | |
| --- | --- |
| **Depends on** | S5, S6 (S8 also on S7) |
| **Owns** | `story/stages/generation/planner.py`, `writer.py`, prompts; matching tests |
| **Acceptance** | planner sees only the package and has no tools; writer's passage set derived from fact bindings by code, never from the plan's citation ids (§10.2.1 point 3); structured draft with per-sentence bindings, never raw Markdown alone |
| **Status** | PLANNED |

### S9 — Deterministic verifier · S10 — Advisory verifier

| | |
| --- | --- |
| **Depends on** | S5, S8 (S10 also on S6) |
| **Owns** | `story/core/numerals.py` `story/stages/verification/` and tests |
| **Acceptance** | deterministic layer is final authority; blocking finding rejects the draft; model can only tighten; advisory findings discarded when spans do not occur verbatim |
| **Review** | adversarial verifier reviewer with malicious drafts |
| **Status** | PLANNED |

### S11 — Pipeline, artifacts, CLI · S12 — End-to-end spike

| | |
| --- | --- |
| **Depends on** | all above |
| **Owns** | `story/pipeline.py` `story/context.py` `story/cli.py` `story/__main__.py` `config/story.yaml` and tests |
| **Artifacts** | `data/story_runs/<story_run_id>/` per plan §14 — gitignored, atomic `.partial` → `os.replace`, manifest written last |
| **Acceptance** | reproducible ids; rejected never overwrites accepted; exit codes match house convention; spike runs on the 2022Q3 cluster with recorded responses |
| **Status** | PLANNED |

### S13 — Interactive research mode

Deferred. Only after S12 is green and only if it stays inside V1 scope. **Not part of the
approved slice.**

---

## 5. Validation performed at every accepted step

`git show --stat` · full diff read · only owned files changed · step tests · package-structure
tests · all `tests/story/` · `pytest -m "not live and not neo4j"` at checkpoints · ledger
updated · narrow commit · clean worktree.

---

## 6. Subagents and parallelism — actual record

Filled in as steps complete.

| Wave | Steps | Agents | Overlap check |
| --- | --- | --- | --- |
| **C** | **S3-A** move+reversal ∥ **S3-B** acceleration ∥ **S3-C** divergence | 3 concurrent | one detector module + one test file each, inside a shared package. **`story/stages/detection/__init__.py` is barred to all three** — the orchestrator wires re-exports at integration, because three agents editing one `__init__` is the classic concurrent-edit collision. `detector_config.py` is created by S3-A and read-only to the others |
| **B** | **S2** canonical series ∥ **S9a** numerals | 2 concurrent | disjoint files; S9a is a pure library depending only on S0 contracts, so it is pulled forward off S9's critical path. Neither may edit `story/core/__init__.py` |
| AR1 | adversarial review → **R2a** ∥ **R2b** | 1 reviewer, then 2 concurrent repairers | reviewer read-only; repairers split freshness+providers against retrieval, no shared file |
| recon | upstream contract survey | 1, read-only | owns nothing |
| — | **S0** contracts | 1 | sole writer; everything depends on it |
| — | **S0c** neo4j adapter | 1 | sole writer; S0b and S1 both wait on it |
| **A** | **S0b** freshness ∥ **S1** retrieval ∥ **S6** model provider | 3 concurrent | see below |

**Wave A overlap analysis, done before launch.**

| | S0b freshness | S1 retrieval | S6 model provider |
| --- | --- | --- | --- |
| writes | `story/stages/freshness/` `story/core/graph_identity.py` `tests/story/test_story_freshness.py` | `story/stages/retrieval/` `tests/story/test_story_retrieval*.py` | `story/providers/openai_compatible.py` `story/providers/answer_store.py` `story/providers/public.py` `tests/story/test_story_provider*.py` |
| reads | contracts, adapter, context | contracts, conftest fake | contracts, `core/models` |
| runtime touched | Neo4j (read-only) | Neo4j (read-only) | llama.cpp, or skips |
| depends on the others' output | no | no | no |

No file is written by two agents. The one shared risk is `story/stages/__init__.py`, which S0b
and S1 both need: both were told to create it only if absent and otherwise leave it alone.
Each commits with **explicit paths**, never `git add -A`, so a concurrent working tree cannot
be swept into someone else's commit. Their tests do not interfere: S0b and S1 read Neo4j and
write nothing, S6 either reaches llama.cpp or skips.

Not parallelised, deliberately: S2 canonicalisation and S3 detectors (S3 consumes S2's series);
S5 packaging before the package schema is accepted; S8 writer and S9 verifier before the draft
bindings are frozen; and any two agents editing one module.

---

## 7. Decisions and corrections taken during implementation

### D1 — How `story/` reaches Neo4j *(decided 2026-08-03, founder)*

`load_settings`/`read_env_file`/`driver_for` live in `graph/stages/load/connection.py`, which
§4 of the boundary doc forbids. Four options were put; **option 1 was chosen with an
architectural correction**: the adapter does **not** go in `story/core/`.

- `story/providers/neo4j_connection.py` owns it. `core/` stays independent of Neo4j and of
  environment concerns; `providers/` is already where an external system is reached.
- `story/context.py` constructs it. No retrieval tool constructs a driver.
- `story/contracts.py` declares a story-owned **query-executor protocol**; retrieval tools
  depend on that protocol and receive it by injection.
- Implements only: URI/user/password/database config, repository-consistent `.env` loading,
  driver creation, connectivity verification, lifecycle/close, and parameterised read
  statements with an explicit timeout.
- Must not implement: schema/index creation, wipe or replace, loading, writes, load settings,
  migration.
- The adapter is the **only** story module importing `neo4j`, enforced structurally.
- Normal tests use a fake/recorded executor; live Neo4j tests are optional and marked.

Rejected: importing `graph.stages.load.connection` (transitively drags `neo4j` into everything
reachable from retrieval, and depends on a file another session owns); asking that session to
promote it to `graph/core/` (coordination event, breaks their
`test_the_driver_is_named_only_inside_the_load_stage`); JSONL-only retrieval (loses the
fulltext index and every traversal the approved slice needs).

### C1–C4 — Recon corrections applied before downstream packets were issued

| # | Correction | Where it lands |
| --- | --- | --- |
| C1 | `normalize_alias` is in **`ontology.core.identifiers`**, not `extraction.core.identifiers`. The boundary doc stated it wrongly and a packet written from it would not have compiled | boundary §4 |
| C2 | **Neo4j stores no nulls.** Never assume an optional property exists; distinguish missing from valued; derive period shape from the fields present. `period_start/end` on 2,304/2,704, `currency` 997, `row_label` 2,690, `table_id` 503/8,776, `report_date` 184/185 | boundary §4.2, packets S1/S5 |
| C3 | **`quoted_text` is on the `EVIDENCED_BY` edge**, not on `:Observation` — as are `table_id` and `block_ids`. Retrieval Cypher must return the edge property explicitly; citation and verification tests must exercise this shape | boundary §4.2, packets S1/S5/S9 |
| C4 | **Ontology is authoritative for metric metadata.** `:Metric` carries no `percentage_min/max`, `distinct_from` or `reconciles_to`. `DISTINCT_FROM` edges may support inspection but never a comparability ruling; no silent fallback to a missing node property | boundary §4.2, packets S2/S3/S9 |

Also recorded: the graph holds **8,776** cited passages against a corpus of **12,442** — a
consumer treating them as the same is wrong by 3,666.

### Environment note — `data/` in this worktree

`data/` is gitignored and existed only in the main checkout, so the worktree's baseline was
`6 failed, 2141 passed, 576 skipped`. `extraction_runs`, `graph_runs`, `normalization_catalog`
and `catalog` were **copied** (not symlinked — a symlink made `Path.resolve()` escape the
worktree and one manifest test recorded an absolute path); the rest are symlinks and
`data/story_runs/` is a real local directory, so **no story artifact is ever written into the
shared tree**. Baseline is now **2,723 passed, 0 skipped, 97 deselected**, matching F0.

### S0 findings accepted into the record

| # | Finding | Disposition |
| --- | --- | --- |
| F1 | **`tests/story/__init__.py` cannot exist.** `pythonpath` puts `tests/` on `sys.path`, so with that file present `import story` resolves to `tests/story` and `story.core` disappears. I re-verified by probe: `ModuleNotFoundError: No module named 'story.core'` | **No later step may create it.** No other `tests/` subdirectory has one |
| F2 | My packet described `graph/core/models.py` as frozen dataclasses. It is frozen **pydantic** with validators; `graph/core/manifest.py` is the dataclass | Packet was wrong, agent followed the code. Contracts are pydantic except `StoryRunManifest` |
| F3 | `Warning` shadows the builtin | Renamed `PackagedWarning`, matching the other `Packaged*` rows |
| F4 | §13.17 and §13.7.1 disagree — `REBIND_TO_DISTINGUISHING_COLUMN` is in one remedy list and not the other | Carried; it is the only actionable answer to the refusal §13.7.1 measures at 61.3% of table observations |
| F5 | **§15.2 is self-contradictory**: it places `GenerationResult` in `story/contracts.py`, which the same plan's import rule forbids because it needs pydantic | Lives in `core/models.py`; a test compares its field set against `extraction.contracts.GenerationResult` so the two cannot drift |
| F6 | **§10.3's `package_content_digest` has no fixed point** — a digest over a structure that contains it | Computed over the package minus that field: `digestible_payload()` / `with_content_digest()` |
| F7 | §6.11's published digests are not reproducible — computed against the 2026-08-02 snapshot, and the plan never records the `anchor_input_ids` behind them | Expected; the plan already says "recompute at L3". Deferred to S3 |
| F8 | Two concurrent `pytest` runs collide on extraction report artifacts and produce five spurious failures | **Run the suite serially.** Not a defect |

F5 and F6 are real defects in the approved plan, found by building against it.

### S0c findings and the one scope deviation

**The scope deviation, reviewed.** S0c edited a structural test it did not own, and said so
rather than burying it. The rule S0 wrote —
`test_no_driver_is_reachable_from_anything_but_the_connection_module` — walked *everything
except the adapter* and asserted the closure reached no driver. **D1 makes that unsatisfiable**:
`story/context.py` is the composition root and must construct the executor, so it reaches
`neo4j` in one hop by design. Keeping the rule as written would have required a package with no
composition root, or hiding the import behind `importlib` and defeating the import-graph check
it belongs to.

The correction is not a weakening, and I mutation-tested it rather than accepting the argument:

| Change | Direction |
| --- | --- |
| `escaped == set()` → `importers == {adapter}` | **stronger** — deleting the adapter now fails |
| exemption `{adapter}` → `{adapter, context.py}` | wider by one file, architecturally required |
| new `test_the_composition_root_is_the_only_module_outside_providers_that_reaches_the_adapter` | **fences it** — `core/` importing the adapter fails 3 rules, a *stage* importing it fails 3 |
| new `assert any(p.is_relative_to(PACKAGE / "core"))` | **stronger** — the walked set cannot silently lose `core/` |

This is exactly how `tests/graph/test_graph_package_structure.py` scopes the same rule, to
`DATABASE_FREE` rather than to "everything but the loader".

| # | Finding | Disposition |
| --- | --- | --- |
| F9 | **`RoutingControl.READ` is a real server-side write barrier**, not mere routing intent. A `CREATE` under READ is refused `Neo.ClientError.Statement.AccessMode`; the identical statement succeeds under WRITE | **Corrects plan §16**, which was too pessimistic. Story now has two independent controls, not one |
| F10 | **`timeout=0` means *no timeout*, not "expire immediately"** — a 1.5s statement completed under `0` and `None`, and was terminated under `0.001` | `read()` refuses a non-positive timeout; accepting one would remove §16's ceiling while looking like the tightest possible |
| F11 | **`Duration` and `Point` are `tuple` subclasses; `neo4j.graph.Entity` is a `Mapping`** | A generic sequence branch turned `Duration(days=3)` into `[0,3,0,0]`; a generic mapping branch flattened a whole `:Node` and dropped its labels. Specific branches now precede general ones; a node is refused, not flattened |
| F12 | `Driver.verify_connectivity()` fails in 0.0s against a refused port, but `execute_query` retries a managed transaction for **35 seconds** | Health check does the handshake before the probe. **S0b must know this** — a `read()` against a dead server pays 35s |
| F13 | The graph stores only five property types: Boolean, Double, Long, String, StringArray. No temporal or spatial value exists in it today | The `Date`/`Time`/`Point` conversion branches are unreachable now and exist so a future `date` property cannot leak |
| F14 | A fifth error type `StoryGraphResultError` beyond the boundary doc's three | Accepted — the conversion needs somewhere to refuse a returned `Node`, and flattening it satisfies "no driver type escaped" while dropping labels, which is worse |

### S0b and S6 findings

| # | Finding | Disposition |
| --- | --- | --- |
| F15 | **§7 names five refusal codes; the gate needs eight.** `graph_manifest_unreadable`, `extraction_run_directory_missing` and `graph_unreachable` have no code in the plan — without them a stopped container or a deleted manifest escapes as a traceback from the gate every command runs first | Accepted; plan §7 under-specified |
| F16 | The `:GraphLoad` marker's own `node_count` is **28,836** — the loader excludes itself from what it records. The trap is the *live* count (28,837) | Handled with `WHERE NOT n:GraphLoad`, and a test that counting the marker as data would refuse the correct run. Verified live by me |
| F17 | `graph/core/verification_report.Check` is `extra="forbid"` with no refusal code, and `VerificationReport` requires four count fields a filesystem check has no honest value for | Types modelled on it, not borrowed; only the shared rendering rule imported |
| F18 | The graph manifest reader is **allow-subset**, not `extra="forbid"` | Justified: `manifest.json` is the other workstream's artifact with several consumers, and refusing a *fresh* graph because the projection added a diagnostic block would raise where §7 promises a structured refusal |
| F19 | **Extraction's `\x1f` digest join is ambiguous once a second free-text field exists.** Reproduced: `('planner\x1f','write')` and `('planner','\x1fwrite')` share a digest | Story digests each text field before joining. **Extraction is not currently vulnerable** — only `prompt` is genuinely free-text there — but story with `system` *and* `prompt` would have inherited it |
| F20 | **§15.3's six keywords are not sufficient alone.** An object without `additionalProperties:false` admits any key; an array without `items` admits any element | `validate_portable_schema` enforces those beyond the keyword list and refuses `description`/`title`. **Constrains what S7/S8 may write** — flagged for those packets |
| F21 | **The local model server is down.** Nothing on `:8080` or `:8081` | S12 will run on recorded responses. Reported honestly rather than worked around; see §8 |

### R1 findings — two that outlive the repair

| # | Finding | Why it matters |
| --- | --- | --- |
| F22 | **`db.transaction.monitor.check.interval = 2s`** and `db.transaction.timeout = 0s` (both verified live). The server has no default statement ceiling, and it notices an expired transaction only every two seconds | **This is a real ceiling on §16's whole timeout story, not just on one test.** `FRESHNESS_TIMEOUT_SECONDS = 30.0` is far above it so the gate is unaffected — but **any per-tool budget S11 sets in `config/story.yaml` below ~2 s will be advisory rather than enforced on this instance.** Must be written down before the budgets are chosen |
| F23 | **The structural bound rule has a hole the aggregate branch opens.** A statement returning `count(n) AS c, collect(DISTINCT n.x) AS xs` passes as "one row by construction" — correct on row count, and an unbounded *payload*. `DATA_NODE_INVENTORY` does exactly this over 28,836 nodes and is legitimately fine today, but the rule as written would equally pass a statement collecting 28,836 ids into one row | The row bound is enforced; the **token** bound §10.2 actually cares about is not. Referred to the adversarial review |

### Adversarial review AR1 — freshness + retrieval *(independent reviewer, 2026-08-03)*

Attacked 17 lines of enquiry against the live graph. **Found one defect that invalidated the
layer's central guarantee**, plus four evidence-quality defects, three unbounded reads and three
structural holes. Everything below was re-verified by the orchestrator before repair.

#### The dangerous one — and the fixture that hid it

**A1: the freshness gate hashed `run.complete` and nothing it lists.** `run.complete` *is* a
digest manifest of the other ten files — `extraction/core/run_directory.py:7-10` says its whole
purpose is that *"'this run finished' and 'this run still holds what it finished with' are the
same question"*. The gate never verified the digests inside it.

Orchestrator's own reproduction, before the fix:

```
baseline, untouched copy                    passed=True   codes=[]
run.complete byte-identical after tamper:   True
claims.jsonl replaced with 8 bytes          passed=True   codes=[]
```

Every other control in the layer is downstream of §7 being true. The gate caught a *regenerated*
run because the marker moved; it did not catch an *edited* one — which is the §17.8 failure it
exists to make impossible.

**Why it was invisible:** the offline fixtures staged a marker naming `claims.jsonl` and
`observations.jsonl` that the fixture builder never wrote, with a comment asserting *"its content
is irrelevant to every test here"*. **Twenty tests exercised the gate against a run directory
that had no contents to check.** A fixture that cannot express the failure is why the check was
never missed — the most transferable lesson of this review.

#### Confirmed and repaired at R2a (`b101382`)

| # | Defect | Fix | Orchestrator verification |
| --- | --- | --- | --- |
| A1 | gate hashes only the marker | new `extraction_run_contents` check re-hashes all ten listed files; **reused** `package_input_digest_mismatch` rather than minting a code, because "the marker moved" and "a file it names moved" are one operator action | reproduced tamper → now `passed=False`, naming file and both digests; deleted listed file → refuses; **138–171 ms** on `/mnt/c`, 14 checks (was 13) |
| C3 | gate *raised* on two paths — `UnicodeDecodeError` (a `ValueError`, not `OSError`) and `PermissionError` outside any handler | both refuse structurally now | verified in the report |
| C2 | shipped docstring said `RoutingControl.READ` is "intent, not enforcement" — contradicting **F9**, measured twice. A maintainer would have deleted a load-bearing control | corrected in three places incl. a test *name* that restated the false claim | grepped: the only surviving phrase is inside the correction quoting what it fixes |

Sub-decisions worth keeping: a **listed-but-absent** file refuses; a **present-but-unlisted** file
does **not** (it cannot change a recorded digest, and refusing would stop every command over a
stray `.DS_Store`) but is named in the check detail.

#### Referred to R2b (retrieval) — see that row

D4 write-scan evadable two ways · D5 `find_counter_evidence` returns `Ok([])` for zero-observation
metrics · D6 truncation drops `rejection` rows first · D7 `search_passages` 2,306× amplification ·
D8 `get_passage_context` scans a whole document.

#### Quantified, not theorised — `PROFILE` at each tool's declared bound

| tool | rows | db hits | ratio |
| --- | ---: | ---: | ---: |
| `search_passages` | 25 | **57,661** | **2,306×** |
| `compare_metric_periods` | 2 | 480 | 240× |
| `get_passage_context` | 3 | 940 | 134× |
| everything else | — | — | ≤71× |

**F23 is narrower than feared.** The aggregate loophole fires only in the freshness gate
(192k db hits across four statements) where the payload *is* bounded — one distinct value per
collected list. In `story/stages/retrieval/` no aggregate runs over an unbounded match. A work
risk, not the token risk F23 predicted.

#### Design finding the orchestrator owns — not sent to a repair agent

**A4: `search_passages` is model-steerable at top-k.** Escaping closes the *operator* channel
(proved: escaped `margin "NOT" gross` → 3,318 hits, unescaped → 372), but terms are model-supplied
and results are `ORDER BY score DESC LIMIT 25`, so **adding three innocuous terms evicted 18 of
25 previously-returned passages**. That is §11's silent-omission channel controlled by the thing
being verified. Not a retrieval bug — a fact about the design that **§11 and S7 must account for**.

#### Survived — what tells us the review was real

Cypher injection on every parameter (metric surfaces resolve through the ontology *before* a
query is built, so a hostile `metric_id` never reaches the database). Lucene operator injection,
disproved with numbers. `Node`/`Path`/`Date`/`Point`/`Duration`/`set`/`Decimal` all refused by
`plain_value`, driven against real driver objects. The `LIMIT 2` decision — no scenario flips a
refusal into a pass. 12 of 13 gate checks, across 18 failure scenarios, all typed refusals.
Citation resolvability **100% occupied** on all nine tools. Driver confinement caught all five
mutations. Zero observations produce an `unknown` period shape.

### R2b accepted (`070457a`) — and the orchestrator's ruling on the channel it opened

| Defect | Fix | Orchestrator verification |
| --- | --- | --- |
| **D4** scan evadable two ways | `_is_cypher` now means "first word is a clause keyword" (not "contains RETURN"); keywords upper-cased; `DETACH` added; a new rule scans **every** non-docstring string in `story/` | **Mutation-tested by me**: no-`RETURN` `DETACH DELETE` → **3 failures**; lowercase `set … return` → **2**. Restored clean, `213 passed` |
| **D5** `Ok([])` where refusals explain the absence | `Unavailable("no_document_scope")`, reason naming the count. Scope query runs **only** when the first returns zero rows | `revenue`/2022Q3 → `Unavailable`, reason contains `170` |
| **D6** truncation drops `rejection` first | explicit `severity_rank` CASE; **nothing dropped can outrank anything returned**, so `truncated_below_severity` names the floor with no second query | rank vocabulary confirmed live: refusal 6,228 · rejection 49 · diagnostic 1 |
| **D7** 2,306× amplification | `{limit: 500}` inside `queryNodes` | `["the"]` **45,972 → 3,430**; top-26 byte-identical on every measured query |
| **D8** whole-document scan | neighbour ids computed in Python, looked up on the `passage_key` constraint | 453-passage doc: **968 → 113**; rows byte-identical; cost no longer a function of document length |

**A correction R2b made against itself**, worth keeping: a draft comment claimed the
`collect`/`UNWIND` was cheaper than streaming. Measured, it is **exactly equal at 3,430** — the
saving came from a shortened `RETURN` list. Corrected in the code and the commit message.

#### Ruling — D7's inner limit opens a filter-starvation channel, and it stays disclosed rather than refused

R2b flagged honestly that `{limit: 500}` applies **before** the document filter, so a filtered
query can be starved: `["the"]` + `shareholder_letter` returns **9 rows instead of 26**. It
offered a one-line refusal and asked for a ruling. Measured by the orchestrator before deciding:

| query | pool | rows | starved? |
| --- | ---: | ---: | --- |
| `["the"]` | **500 (capped)** | 25 | — |
| `["the"]` + `shareholder_letter` | **500 (capped)** | **9** | **yes** |
| `["Adjusted EBITDA"]` | 432 | 25 | no |
| `["Adjusted EBITDA"]` + `shareholder_letter` | 432 | 25 | no |
| `["Adjusted EBITDA"]` + `earnings_release` | 432 | 25 | no |

**Starvation requires the pool to cap, and the pool only caps on a degenerate term set.**
`"the"` matches 6,873 of 8,776 passages. A realistic query — one metric alias — has a pool of
432, below the limit, and filters are exact.

**Ruling: keep the disclosure, do not refuse in retrieval.** Two reasons. First, §11's
correction already requires search terms to be **derived by code from the candidate's metric
aliases and period surfaces**, never authored by a model — a stopword cannot reach this tool on
the pipeline's path. Second, refusing in retrieval would put an evidence decision in the wrong
layer; the plan puts those in packaging.

**Therefore S5 owns this, and its packet must carry it:** a `search_passages` result whose
`candidate_pool_size == inner_limit` **and** which was filtered must not contribute explanatory
passages to a package — it must raise a package warning instead. Retrieval discloses; packaging
decides. Recorded here so S5 cannot inherit it as an unstated assumption.

### S9a accepted (`7c8a13b`) — the numerals library, and four plan defects it exposed

`story/core/numerals.py`, 697 lines, stdlib only. First execution of §13.1 and §13.3 against
real data.

**Orchestrator validation:** scope clean (2 files) · **I re-ran the reconstruction myself:
2,690/2,690 table quotes reconstruct exactly** off the `EVIDENCED_BY` edge (C3), with absent
`scale` read as `units` (C2 — 84 `market_count` rows carry no `scale` property) · 79 tests
(77 offline + 2 `neo4j`) · story `914 passed`, offline `3596 passed`.

| # | Plan defect | Verified by me | Corrected in |
| --- | --- | --- | --- |
| **P1** | **§13.1's tolerance formula contradicts its own worked case.** Prose says `\|V_draft − V_fact\|`; the case computes magnitudes | **Confirmed and serious.** `"$27.1 million loss"` is an *unsigned* numeral — *loss* carries the sign in prose the tokeniser never sees. Signed: `54,175,000`, blowing the ±50,000 window. Magnitudes: `25,000`, which is what the case asserts. **Left as written it fails every one of the 825 negative-valued observations** | plan §13.1 — magnitudes compared, sign agreement a separate check |
| **P2** | Over-precision has no single "fact's printed form" | Confirmed: no `:Observation` carries `printed_form`, and `adjusted_ebitda` 2020Q4 is quoted **both** `(27,075)` (thousands) and `(27)` (millions) | the printed form is an argument; the library never picks a printing |
| **P3** | §13.3's "factor of nineteen" presented as general | **Partly.** It is `100/\|v1\|` and base-dependent: `5.2→2.2` gives **19.2×**, `2.2→5.2` gives **45.5×**. The plan's number was right for the falling case it described and wrong as a constant | plan §13.3 — stated as `100/\|v1\|` with both measured |
| **P4** | §13.9's float-residue example does not float | Confirmed: `5.2 − 2.2` is **exactly 3.0**. Real residues do exist — `9.9 − 7.3 = 2.6000000000000005`, and the spike value `3.3 − 13.2 = −9.899999999999999` | plan §13.9 — real residues substituted |

Two smaller ones recorded in the module: §13.2 lists five unit surfaces where the corpus has
four (`contracts` is not a unit — both contract metrics are `homes`), and §13.7.1's percent
exception is unexercised because all 1,163 percent table rows carry `scale: units`. Implemented
the plan's way regardless.

**A discipline point against myself:** my §13.1 correction first claimed "1,046 of 2,704
observations are negative". Measured, it is **825**. Corrected before commit — an unverified
number in a correction is the same defect the correction exists to fix.

### S2 accepted (`b8bbe93`) — and it found a rule that refuses the plan's own spike

**It did not tune to match my numbers.** Told the expected R6 figures were ground truth, it
reproduced `383` exactly, measured `198` where the plan says `185`, **explained the difference
precisely, verified that the plan's reading reproduces 185/48.3%/17/47 exactly**, and then chose
the other option on principle. That is the behaviour the packet asked for and the opposite of
fitting the policy to the data.

| # | Finding | Verified by me | Corrected in |
| --- | --- | --- | --- |
| **P5** | **R6 as written refuses every cross-metric pair — including F3, this plan's own recommended spike.** *"resolve_version(metric, period_end) for each side must agree"* compares two different metrics' version ids | **Confirmed against the ontology**: at 2022Q3, `adjusted_gross_margin → adjusted_gross_margin_v1` and `gaap_gross_margin → gaap_gross_margin_v1`; equality is structurally impossible. It would also kill the shipping CP↔AGP divergence pair | plan §6.9 — R6 evaluated **per metric across both dates**; identical on every same-metric pair, so no census moves |
| **P6** | **§6.9's v1 census is internally inconsistent.** "v1 `2020-01-01..2021-12-31` (17 slots, 47 observations)" — a slot ending in 2019 cannot lie in a window starting 2020 | **Confirmed**: `FY2018`, `FY2019`, `2019Q4` (5 observations) end before `valid_from`. The ontology declares only two windows, so they lie in **none**. Counting them as v1 reproduces my numbers exactly — by asserting v1's restructuring adjustment applied in 2018 | plan §6.9 — **198 forbidden (51.7%)**: 160 mismatch + 38 `FORMULA_VERSION_UNDECLARED`, refused rather than clamped per C4. The "1 adjacent-quarter + 4 YoY" figure survives unchanged |
| **P7** | §6.1's slot key is not the graph's — the graph stores `PeriodRef.key` (`2022Q2`, `FY2021`), not `{start}_{end}` | Bijective over this corpus (74 either way, census unaffected), but every id built from it differs | plan §6.1 |
| **P8** | **§6.1's quarantine lane name matches nothing.** Plan says `lane == "narrative"`; the stored value is `normalized_narrative` | A guard written to the plan's spelling **would read as a rule that never fires** — the same shape of invisibility as AR1's fixture that could not express its own failure | plan §6.1 |

**Operational finding, not in the plan: `get_metric_history` is bounded at 200 and five metrics
exceed it** — `adjusted_ebitda`, `adjusted_ebitda_margin`, `contribution_margin`,
`contribution_profit`, `gaap_gross_margin`. A single call returns 2,238 observations with
`truncated=True` and no other signal. S2 keyset-pages on the tool's own `$since` window and
**refuses if one anchor date ever fills a page** rather than returning short. Largest anchor
date today is 20 rows.

**Design judgments worth keeping.** R9 derives its cohort set from the ontology
(`adjustment_components` whose `note` contains `cohort`) rather than hard-coding two names —
yielding exactly `{contribution_profit, contribution_profit_after_interest}`, with a third
arriving without a code change; it **warns, never refuses**. R10 needs *two* conditions:
"nothing between them in the series" alone is satisfied by `pct_>120d` `2021-12-31 →
2022-12-31`, since those are neighbouring **rows** — the +47 pp jump is caught only by the
calendar-step check. And `SERIES_UNAVAILABLE` **refuses** rather than skips: a rule droppable by
omitting an argument is not a rule.

**For S3:** `get_metric_history` returns no `quoted_text` and no `filing_date` (C3 puts them on
the edge and the document), so §6.1 steps 1 and 5 need `get_fact_evidence` per observation —
2,704 calls, 2.7 s live, on by default. With `with_evidence=False` the points carry
`quarantine_not_evaluated` and `filing_date_unknown` rather than pretending the tests ran.

### Open defect D9 — R8's tolerance compares raw float subtraction *(found by S3-B, verified by orchestrator)*

`story/core/series.py:378` `within_tolerance` says, in its own docstring, *"`<=` and not `<`: a
difference of exactly one printed unit is the rounding, not a movement."* It then compares raw
IEEE-754 subtraction against the tolerance:

```
7.4 - 7.3        = 0.10000000000000053
PERCENT_TOLERANCE = 0.1
raw       <= tol -> False   <- R8 does NOT refuse a one-printed-unit step
round(,9) <= tol -> True    <- the docstring's stated intent
```

**Blast radius measured across the whole corpus: exactly one case** —
`gaap_gross_margin 2020Q1→2020Q2, 7.3 → 7.4`. It is load-bearing: S3-B's quarterly
acceleration census is **8 only because this step survives R8**. Rounded, it is **7**.

S3-B found it, refused to paper over it, and asserted the dependency in a named test rather than
adjusting its own count. Repair deferred until S3-A and S3-C land so the fix and the three
affected test files move once rather than three times.

### S3 accepted — four plan defects, one per detector, plus D9 closed

**Every detector found a defect in the rule it was implementing.** None adjusted its result to
match the number it was given.

| # | Defect | Measured | Corrected in |
| --- | --- | --- | --- |
| **P10** | **§6.6 D1's firing expression left the relative arm unguarded**, so it applied to percent metrics alongside the pp arm. With `pp_min` and `pct_min` one column, a **3.0 pp** bar became a **3.0 % relative** bar — **0.24 pp** at GGM's 8% base, a factor of twelve | **310 candidates literal vs 227 corrected**; GGM 37→11, AGM 36→18, `pct_>120d` 10→4 | plan §6.6 D1 |
| **P11** | §6.6 D2's `max(D1 threshold, σ)` **has no literal meaning** — D1's bar is a disjunction, not a number | implemented as a conjunction; identical on percent metrics | plan §6.6 D2 |
| **P12** | §6.6 D2's low-variance exclusion **excludes nothing**, and its reason is **wrong about the data**: `market_count` does not "alternate ±0", it steps up through 2021–22 then flattens, σ = 3.26 against a one-market tolerance | rule kept, fires on nothing; what removes `market_count` is R8 + R10 + the population floor | plan §6.6 D2 |
| **P13** | §6.6 D2's **"84 reversals across 13 metrics" does not reproduce** under any zero-handling or shape grouping tried | closest 76/12 ignoring comparability, 71/11 honouring it; shipped census **11/9** | plan §6.6 D2 |
| **D9** | **R8 compared raw float subtraction**, failing its own docstring on exactly one corpus case | fixed by rounding to `DELTA_PRECISION`; acceleration census **8→7** quarters, **10→9** all shapes. First suite run after the fix failed exactly three tests and no others — the blast-radius claim reproduced | `story/core/series.py:405` |

**A boundary the fix had to respect:** `DELTA_PRECISION` could not live in `detector_config.py`,
because `core/` may not import a stage (`test_core_never_imports_a_stage_a_provider_or_the_cli`)
and R8 must round to the same places the detectors do. It lives in `story/core/series.py:74`,
with an **AST test asserting exactly one file in `story/` assigns it** — two copies could
silently disagree.

**A distinction worth keeping (S3-A):** polarity (*is this a cost?*) is separated from
`VALUE_SIGN` (*how is it stored?*), because conflating them is the actual bug. `direct_selling_costs`
and `holding_costs` are stored negative (46/46 and 15/15, measured). `adjusted_ebitda` is
negative in 37 of 48 slots and **that is a loss, not a sign convention**. `cost_of_revenue` and
`inventory_valuation_adjustment` have zero observations, so the corpus cannot say — their
candidates carry `metric_sign_convention_unverified` and **no direction at all**.

**Two open items carried forward, not defects:** `CanonicalPoint` carries no `ambiguity_codes`,
so §6.6 D8's "the candidate must surface `pct_120_days_denominator`" is not satisfiable from the
canonical layer — **§10 packages them from the observations instead**, flagged for the S5 packet.
And 112 of `metric_move`'s 227 candidates are twelve-month steps judged against quarter-over-quarter
percentiles; narrowing the scan was rejected (§6.2 makes instant series first-class), so
`period_shape` is a signal and **§6.10 ranking is where an annual restatement should lose**.

### Adversarial review AR2 — detectors *(independent reviewer, 2026-08-04)*

19 lines of attack against the live graph. **The most dangerous finding is that two modules in
this package compute the same arithmetic and reach opposite verdicts.**

#### The one that matters

**§6.6 D1's `delta_pct` and §13.3's `delta_relative` are the same calculation, and only one
refuses the cross-zero case.** `story/core/numerals.py` raises `RelativeChangeAcrossZero` on
`5.2 → -6.3`; `metric_move.py:193` computes it and puts it on the candidate. Reproduced by the
orchestrator — **the plan's own textbook example is a shipping candidate**:

```
cand:metric-move:adjusted-ebitda-margin:opendoor:2022Q2_2022Q3:d0786f1786c5
  delta_pct = -221.153846154   <- §13.3: "arithmetically defined and rhetorically meaningless"
  delta_pp  = -11.5            <- the number that should be read
  warnings  = (nothing about it)
45 of 227 metric_move candidates carry a cross-zero delta_pct.
```

**Why it is dangerous even though no candidate fires because of it** — verified, all 18
pct-only candidates have `crosses_zero=False`: the §13 verifier only ever sees a *draft*.
Ranking, packaging and the planner all read `signals`. **The detector is the only layer that
still knows both numbers.**

#### Confirmed, in repair

| # | Defect | Severity |
| --- | --- | --- |
| **a1** | `delta_pct` across a sign flip, 45/227, no warning — while `numerals` refuses the identical inputs | **(a) wrong number reaching a post** |
| a2 | `delta_pct` is a percent-of-a-percent for margins; the `0.10 × median` floor is USD-shaped and meaningless there. 36/262 carry `|delta_pct| > 200` | (a) |
| a3 | D4 has no sign-convention awareness. A hand-made `adjusted_gross_profit ↔ direct_selling_costs` pair emits `gap = 512M − (−136M) = +648M` — **the sum of a profit and a cost** — as a `co_component` divergence at z=2.57. One line in a tuple away | (a) |
| a4 | The `2 × tol` variance floor defends against a 0.1 pp step and nothing larger. A single 1.05 pp excursion in a flat gap scores **z = 4.899, the maximum possible** | (a) |
| a5 | A `CONFLICT` slot **silently** narrows a divergence population — 25 → 23 with `periods_excluded = 0` and no warning, while the module discloses the R6 case two lines below | (a) |
| b1 | **No completeness marker between retriever and detector.** `unreadable` is a *defaulted* kwarg on three detectors and absent from D4. A single un-paged `get_metric_history` yields **93 candidates and zero refusals**, losing 20 real candidates and giving 3 slots a wrong `n_docs` — §6.10's corroboration input | **(b) incomplete read passing as complete** |
| c1 | The fact-slot key omits `subject_entity_id`, and `records[0]` supplies period/subject/unit/currency **in input order** — so `canonicalize`'s "deterministic in the strong sense" docstring is false for four fields. Latent: one subject live | (c) |
| c3 | D3 and D4 carry no polarity/direction; `acceleration`'s `delta_sign` is the raw arithmetic sign on a metric stored negative | (a) |
| c6 | R8's refusal message prints the **unrounded** difference, explaining the opposite of the decision | (c) |

Not defects, recorded: **c2** — D9's rounding also *creates* one firing (`adjusted_gross_margin
2024Q2→Q3`, raw `−2.999999999999999` against a 3.0 bar); correct, undocumented. **c4** —
D15/D16/D17 are unimplemented so `Audience.INTERNAL` is unreachable; nothing can leak, but §6.5
lists all three as V1 and `SlotCensus` is already the input D16 needs. **b2** — the freshness
gate is reachable only from tests; §7's "every command runs it first" awaits S11.

#### Survived — 21 attacks, including every one that would have been most embarrassing

Cypher-level injection; the `$0.4M` small-base case (floor 5.8M correctly suppresses it); the
four-quarter `pct_>120d` hole (**all four detectors refuse**, `R10/SERIES_GAP`); zero deltas
(cannot extend or reverse a run in any detector); `CONFLICT` as a hole in D1/D2/D3; formula
versions (`2019Q4` refuses `FORMULA_VERSION_UNDECLARED` for **all seven** versioned metrics);
unit/period incompatibility across six rule paths; genuinely unrelated pairs (`R2`); R9 present
on CP↔AGP and correctly absent on CPAI↔CP; cost direction correct on all 15 live cost candidates
in D1/D2; **262/262 distinct ids**; determinism under 6 point-shuffles and 3 record-shuffles —
ids, **every signal value**, and candidate order byte-identical; **no threshold enters a digest**
(loosening `USD_MEASURE` moved the census 227→305 and *every original id survived unchanged*);
**no prose in any of the 19 string signal keys**.

## 8. Founder gates

| # | Question | Status |
| --- | --- | --- |
| D1 | Neo4j connection boundary | **RESOLVED** — see §7 |
| G1 | The local llama.cpp server was not running | **CLOSED 2026-08-03.** Founder started it. `:8080` returns `{"status":"ok"}` serving `/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf` — verified by the orchestrator. `:8081` (embeddings) remains down and is **irrelevant**: embeddings are deferred from V1. S6's two live tests can now run instead of skipping, and S12 can report a real local-Qwen run alongside the recorded one |

---

## 9. Deferred / not implemented

Everything in §1 "Out". Recorded here so it is not rediscovered as an omission.
