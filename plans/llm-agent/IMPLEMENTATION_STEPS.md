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
| **Status** | PLANNED |

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
| **Status** | PLANNED |

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
| **Status** | PLANNED |

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
| **Status** | PLANNED |

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
| **Status** | PLANNED |

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
| **Status** | PLANNED |

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
| — | recon | 1 read-only | n/a |

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

## 8. Founder gates

| # | Question | Status |
| --- | --- | --- |
| D1 | Neo4j connection boundary | **RESOLVED** — see above |

---

## 9. Deferred / not implemented

Everything in §1 "Out". Recorded here so it is not rediscovered as an omission.
