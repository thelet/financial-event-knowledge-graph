# Live model-backed extraction — implementation plan

> **Superseded 2026-08-23 by `docs/2026-08-23-live-model-backed-extraction/00-PLAN.md`.**
> That plan re-verified this one against `ff3b08f` and corrects four load-bearing claims —
> reordering does not change `issue_id` (it changes catalog bytes); digesting the answer store
> into the run id would defeat resume; `prompt_version` is not a request-digest input; and
> multi-directory pytest collection does not error. It also replaces the three provider modes
> with two orthogonal knobs. This document is kept for its history, not as guidance.

**Date:** 2026-08-18. **Baseline commit:** `c0bf241`. **Status: plan only — nothing implemented.**
**Starting point:** `docs/2026-08-18-extraction-model-path-audit/EXTRACTION-MODEL-PATH-AUDIT.md`,
re-verified against the code at this commit. Corrections to the audit are marked **(new)**.

**Re-verified 2026-08-23 at `ff3b08f`: still 0 of 8 stages implemented.** `extraction run` has no
`--force` (S1); nothing dispatches on `provider.kind` and `extraction/providers/` still has no
`api_key` or `Authorization` (S3); `AnswerStore.write()` is still never called from `extraction/`
(S4); both lanes still catch only `(ProviderResponseError, ProviderSchemaError)`
(`narrative_lane.py:267`, `event_lane.py:174`) (S5); `extraction/` still contains no `ThreadPool`,
`asyncio`, `concurrent.futures` or `max_workers` (S6); `story/cli.py` still has no `--graph-run-id`
(S7). **§0–§10 stand as written and need no change.** Only this header moved — the measurements
below were stale enough to mislead, and one of its commands no longer works offline.

### Test baseline to hold green *(re-measured 2026-08-23 at `ff3b08f`)*

**The `-m "not live"` command in the 2026-08-18 header is no longer the offline baseline.** A
`neo4j` marker now exists (`pyproject.toml`, "the Neo4j container is local, free and offline, so
deselecting it is about *no database running*") and covers **177** tests, so `-m "not live"` alone
needs a running container. **Use `-m "not live and not neo4j"`.**

| Command | Result | Wall |
| --- | --- | --- |
| `python -m pytest -m "not live and not neo4j" -o addopts="" -q --tb=line` | **6,175 passed · 250 deselected · 0 skipped · 0 failed** | 220 s |
| `python -m pytest -m "not live" -o addopts="" -q --tb=line` | 6,330 passed · 22 skipped · 73 deselected · 0 failed — **requires the Neo4j container** | 301 s |
| *(2026-08-18, for comparison)* `-m "not live"` | 5,874 passed · 22 skipped · 65 deselected | ~270 s |

The 22 skips under `-m "not live"` are Neo4j tests that skip when the container is absent; under
the offline marker they are deselected instead, which is why that column reads 0.

**Collection totals: 6,425 overall** (was 5,960). Per directory:

| Directory | `-m "not live"` | `-m "not live and not neo4j"` | no marker |
| --- | ---: | ---: | ---: |
| `tests/acquisition` | 397 *(unchanged)* | 397 | 402 |
| `tests/normalization` | 203 *(unchanged)* | 203 | 203 |
| `tests/ontology` | 165 *(unchanged)* | 165 | 165 |
| `tests/extraction` | 1,304 *(unchanged)* | 1,304 | 1,359 |
| `tests/graph` | 691 *(unchanged)* | **654** | 691 |
| `tests/story` | **3,592** (was 3,136) | **3,452** | 3,605 |
| **total** | **6,352** | **6,175** | **6,425** |

All of the growth since 2026-08-18 is `tests/story` — +456 under `-m "not live"`. The other five
directories have not moved at all, which is consistent with 0-of-8: none of this plan's stages has
been started.

### Three procedural gotchas

- **Still true.** `pyproject.toml:27` sets `addopts = "-q"`, so a command-line `-q` yields `-qq` and
  pytest prints **no summary line at all**. Always pass `-o addopts=""`.
- **Still true, and worse than described.** Do **not** name several test directories on one command
  line. Naming all six now produces **11 collection errors** — 4 under `tests/acquisition`, 7 under
  `tests/graph` — and pytest interrupts with only 6,023 of 6,425 collected *(measured 2026-08-23)*.
  The mechanism is as diagnosed — several test directories are rootless (no `__init__.py`), so their
  `conftest.py` files all claim the same top-level module name `conftest` and the first one imported
  shadows the rest — but the 2026-08-18 note named only one pair of victims. **Which `conftest` wins
  depends on the directories named**, so the error text differs run to run: with all six on the line
  it is `tests/story/conftest.py` (`cannot import name 'make_filing_record'`); with
  `tests/graph tests/normalization` it is `tests/normalization/conftest.py`
  (`cannot import name 'REAL_RUN_AVAILABLE'`, 7 errors). **A bare `python -m pytest tests` is fine**
  and collects all 6,425 cleanly — so run the whole suite, or one directory at a time, never a list.
- **Caution, not a current condition.** *(Re-framed 2026-08-23.)* On 2026-08-18 a concurrent session
  was mid-edit and a first full run showed 89 failures, all under `tests/story`, purely from
  half-written files; a re-run after they settled was green. **That is history — the tree is green
  today**, 0 failed on both commands above. The durable lesson is the procedure, not the number:
  a failure count taken while another session is writing measures that session, not your change.
  Re-baseline on a branch before trusting any number here.

---

## 0. Three findings that reorder the work

**(new) A smoke run destroys the graph's source of truth — today, before any of this work.**
`make_run_id` digests only `(RUN_LAYOUT_VERSION, config_hash, corpus_id, ontology_definition_hash)`
(`extraction/core/run_directory.py:44-59`). `ExtractRequest.lanes/limit/document_ids` are **not**
inputs — they reach only the manifest (`extraction/pipeline.py:89-99`). Computed live at this
commit, `--limit 10` yields the identical id `extract-v1-lexical-833f7bcfbce9`, and
`RunDirectoryWriter.finalize` does `if final.exists(): shutil.rmtree(final)` unconditionally
(`run_directory.py:131-134`) with no `--force` and no guard. **The first thing anyone does to test a
live provider — "try ten candidates" — deletes the run the current graph was built from.** This
makes overwrite safety Stage 1, ahead of the provider.

**(new) Transport faults abort the whole run.** Both lanes catch only
`(ProviderResponseError, ProviderSchemaError)` (`narrative_lane.py:267`, `event_lane.py:174`).
`ProviderUnavailable`, `ProviderTimeout` and `ProviderTransportError` propagate out of
`LaneRunner.run` and abort before a single byte is written — and `run` extracts *before*
`writer.begin()` (`pipeline.py:105-112`), so there is no staging directory to inspect. In
replay-only mode this never mattered; over 10,807 live requests it is the dominant failure mode.

**(new) Two properties already exist that shrink the work — one with a sharp limit.** The
`request_identity` digest includes `model_id` (`answer_store.py:96`), and the **integrated run**
filters rows by it (`context.py:88`), so on that path two models' answers coexist in one file with
no collision. **The benchmark path cannot.** `narrative_runner.replay_provider:470-486` constructs
`ReplayingGenerationProvider(store, None)` with no `model_id`, so identity falls back to
`AnswerStore.identity_model_id()`, which returns `None` when rows disagree — and the constructor
then **raises** (`answer_store.py:258-262`). This is deliberate, and the docstring says why: *"a
report that needed a second file to say which model its answers came from could not be regenerated
from the answers alone."* **Consequence: one answer-store file = one model on the benchmark path,
so model comparison needs per-model store files, not just per-model report directories.** Separately,
the benchmark report **already names its model** — `narrative_lane_v1.json` carries a `model` block
with `identity`, `context_tokens`, `max_output_tokens` and the `output_budget` formula, plus an
`answer_store{path,answers,bytes,sha256}` block — so no new report field is needed.

---

## 1. Target extraction architecture

```text
Documents / Passages ──► TypedCandidateSelector ──► 11,848 candidates
                                                          │
                    ┌─────────────────────────────────────┴──────────────┐
                    │                                                    │
              tables lane                              narrative / events lanes
         DeterministicTableClaimLane                   build_prompt → output_budget guard
              (no provider)                                        │
                    │                                    request_identity(prompt, schema,
                    │                                        model_id, temp, max_tokens)
                    │                                              │
                    │                          ┌───────────────────┴───────────────────┐
                    │                          │  CachingGenerationProvider (renamed)  │
                    │                          │  store hit ──────────────► replay     │
                    │                          │  store miss ─► inner.generate() ─► put│
                    │                          │               + incremental flush     │
                    │                          └───────────────────┬───────────────────┘
                    │                                              │
                    │                        build_generation_provider(config)   ← context.py
                    │                              ├── local_openai_compatible (Qwen)
                    │                              └── hosted_openai            (OpenAI)
                    │                                              │
                    └──────────────────────┬───────────────────────┘
                                           ▼
                    response_mapping / event_mapping  (UNCHANGED)
                                           ▼
                    assembly → validation → ontology validate_claims  (UNCHANGED)
                                           ▼
                    lane_outputs.jsonl → 7 catalogs → 5 checks → report.md → run.complete
                                           ▼
                    graph project → graph load --replace → graph verify → story/UI
```

**The operating contract, in one sentence:** a lane asks the configured provider; the store answers
first when it can, the model answers when it cannot, and the run records which happened.

### 1.1 Modes

Three named modes, resolved in `extraction/context.py` and recorded in the manifest:

| Mode | Store | Inner provider | Store miss | Use |
| --- | --- | --- | --- | --- |
| `replay` | read | **none** | `NO_STORED_ANSWER` | today's behaviour; reproducing a recorded run; offline tests |
| `live` | ignored on read | required | call the model | a clean measurement with no cache influence |
| `live_cached` | read | required | call the model, persist the answer | **the default for real runs** — resume, dedup, cost control |

`live_cached` is the mode the corpus run uses. `replay` must remain reachable and must remain the
default for anything that runs in CI, so the offline suite never needs a GPU.

**Rejected:** a boolean `--live`. Three states exist (`replay`, `live`, `live_cached`) and a boolean
cannot express "call the model but do not write to the shared store", which is exactly what a
model-comparison run wants.

---

## 2. Current → target delta

| # | Area | Current | Target |
| --- | --- | --- | --- |
| D1 | Provider wiring | `context.py:132` hard-codes `inner=None`; no CLI flag | `build_generation_provider(config, mode)` mirroring `build_scope` (`context.py:93-103`); mode from config + CLI |
| D2 | Provider `kind` | read nowhere — decorative | load-bearing; dispatches to one of two adapters |
| D3 | OpenAI support | none: no `api_key`, no auth header, `chat_template_kwargs` always sent, `/health` is llama.cpp-only | second adapter `hosted_openai.py` + `HostedOpenAIConfig` sibling |
| D4 | `max_output_tokens` | config value `1024` **not passed** to lanes; they use `DEFAULT_MAX_OUTPUT_TOKENS = 4096` | passed explicitly; the config value becomes true or is removed |
| D5 | Transport faults | propagate; abort the run | caught per candidate; recorded as an issue; run continues |
| D6 | Answer persistence | `put()` in memory; store is **pathless** in the run (`context.py:83`); `write()` never called from `extraction/` | store has a path; incremental flush every N answers and in a `finally` |
| D7 | Resume | none; `begin()` rmtrees `.partial` | resume **is** the store: a rerun re-hits every persisted answer |
| D8 | Run identity | `--limit`/`--lanes`/`--documents` and answer-store bytes invisible | request bounds + answer-store file digests enter the id |
| D9 | Overwrite | `finalize()` rmtrees any existing run | refuses a complete run unless `--force` |
| D10 | Concurrency | fully serial; no config key | bounded `ThreadPoolExecutor` + `RateLimiter`, copied from `acquisition/` |
| D11 | Manifest honesty | `"mode": "replay_only"` and `provider_calls_permitted: 0` are **string/int literals** (`pipeline.py:161`, `:95`) | both derived from the context and the run |
| D12 | `NO_STORED_ANSWER` | emitted on every model-backed candidate | emitted **only** in `replay` mode |
| D13 | Graph run selection | `config/story.yaml:177` hand-edited *(was `:73`; re-located 2026-08-23, value unchanged)* | flag → pin → sole finished projection, refusing ambiguity |
| D14 | Benchmark reports | fixed stem; a second model overwrites the first | `--reports-dir`/label exposed (the `directory` parameter already exists) |

---

## 3. Files and modules expected to change

**New (4):**
- `extraction/providers/hosted_openai.py` — the OpenAI adapter.
- `extraction/providers/http_retry.py` — *only if* the retry loop is byte-identical between the two
  adapters; name it for what it is, never `helpers.py`/`utils.py`.
- `tests/extraction/test_hosted_openai.py`, `tests/extraction/test_run_identity.py`.

**Modified (≈12):**

| File | Change |
| --- | --- |
| `extraction/providers/public.py` | `HostedOpenAIConfig` sibling (SecretStr `api_key`, env layering, refuse a key in YAML); `ProviderConfig.concurrency`/`requests_per_second`; `RunMode` |
| `extraction/providers/__init__.py` | add the adapter to `_LAZY` — **never** an eager re-export |
| `extraction/context.py` | `build_generation_provider`; give the store a path; thread mode |
| `extraction/cli.py` | `--mode`, `--force`, `--concurrency` on `run` |
| `extraction/pipeline.py` | derive `provider.mode` and `provider_calls_permitted`; add bounds + store digests to the id; guard `finalize` |
| `extraction/core/run_directory.py` | `make_run_id` inputs; `finalize(force=…)` refusal |
| `extraction/core/config.py` | expose the new keys |
| `extraction/stages/narrative/answer_store.py` | rename `ReplayingGenerationProvider` → `CachingGenerationProvider`; lock `put`; incremental flush |
| `extraction/stages/narrative/{narrative,event}_lane.py` | catch transport faults → issue |
| `extraction/stages/extract/lane_execution.py` | per-candidate buffer; index-slotted pool |
| `extraction/stages/extract/public.py` | new issue code for a transport failure |
| `config/extraction.yaml` | `provider.mode`, `concurrency`, `requests_per_second`; fix or remove `max_output_tokens` |
| `benchmarks/extraction/v1/__main__.py` | expose `--reports-dir` |
| `config/story.yaml`, `story/cli.py`, `story/pipeline.py` | optional pin + `--graph-run-id` |

**Deliberately unchanged:** `response_mapping.py`, `event_mapping.py`, `core/assembly.py`,
`core/validation.py`, the ontology, `graph/` in its entirety.

---

## 4. Stages, in implementation order

Each stage ends with a green `-m "not live and not neo4j"` suite *(command corrected 2026-08-23;
see the header — `-m "not live"` alone now needs the Neo4j container)*. Stages 1–2 are prerequisites
for touching a provider at all.

### Stage 1 — Make a rerun safe *(no provider work)*

Close the destroy-the-run hazard first, because every later stage requires running extraction.

- `make_run_id` additionally digests a canonical rendering of `ExtractRequest`
  (`lanes`, `limit`, `document_ids`) and the sha256 of each configured answer-store file
  (`ABSENT` when missing) — the exact shape of `graph/core/manifest.py::input_content_digest:93-98`,
  reusing `run_directory.file_digest`.
- `finalize` refuses when the destination exists and is complete, unless `force=True`. Model on
  `graph/stages/projection/export.py:474-478` (`previous_is_complete`) and
  `lifecycle.py::decide_replacement` — **an explicit flag is the only wipe authority; a config key
  may be read and reported but never authorise.**
- `--force` on `extraction run`.
- Manifest records `answer_store_digests`.

*Why first:* today a `--limit` smoke run silently deletes `extract-v1-lexical-833f7bcfbce9`.

### Stage 2 — Make the manifest tell the truth

- `provider.mode` and `bounds.provider_calls_permitted` derived, not literal.
- Add `provider_calls_attempted` / `provider_calls_succeeded` / `answers_replayed`.
- **`tests/extraction/test_integrated_run.py:777-778` must be deliberately amended** — it asserts
  the two literals. It becomes: in `replay` mode, `mode == "replay"` and attempted `== 0`.

*Why second:* everything after this is measured through the manifest.

### Stage 3 — Provider selection and the hosted adapter

- `build_generation_provider(config)` in `context.py`, dispatching on `kind`, mirroring
  `build_scope:93-103`. The CLI passes a **mode**, never a constructed object — `context.py` stays
  the only module naming a concrete adapter.
- `hosted_openai.py` + `HostedOpenAIConfig`: `SecretStr` api_key from env/`.env` (refuse a key in
  YAML, as `story/providers/public.py:189-193` does), `Authorization: Bearer`, no
  `chat_template_kwargs`, health via `GET /v1/models`, `max_completion_tokens`, `Retry-After`
  honoured, a distinct auth/quota error class.
- **No new dependency.** `httpx` already does this; `pyproject.toml` has four dependencies and a
  standard-library-first rule. The OpenAI SDK would buy nothing the sibling adapter does not.
- Pass `max_output_tokens` to the lanes (D4), or delete the config key. Today it is a lie.

**Schema portability is already fine — measured, not assumed.** The extraction schemas use only
`type`/`properties`/`required`/`enum`/`items`/`additionalProperties:false`, with every property in
`required` — exactly OpenAI Structured Outputs' shape. Worst case measured at this commit (all 26
metrics offered, 40 period phrases): **3,433 chars, 9 enums, 119 enum values, largest enum 41.**
Verify against current OpenAI limits, but there is no restructuring to plan for.

### Stage 4 — Modes, caching and persistence

- Rename `ReplayingGenerationProvider` → `CachingGenerationProvider` (`inner=None` stays legal and
  means `replay`).
- Give the run's store a path (`context.py:83` currently builds `AnswerStore()` with none).
- Incremental flush: `write()` is already atomic (`.partial` + `os.replace`, `answer_store.py:225-235`)
  and idempotent, so flushing every N answers and in a `finally` preserves derived-index discipline
  exactly — the file stays whole and sorted, just rewritten more often. O(n) per flush over a few
  thousand rows is acceptable; tune N.
- **`NO_STORED_ANSWER` is emitted only in `replay` mode** (D12), restoring its documented meaning:
  *"a statement about this run's bounds"* (`extraction/stages/extract/public.py:65-67`).

### Stage 5 — Transport-fault policy

- Lanes catch `ProviderUnavailable`/`ProviderTimeout`/`ProviderTransportError` and return a typed
  issue (new code, e.g. `PROVIDER_UNAVAILABLE`, severity `refusal`) instead of propagating.
- A run-level circuit breaker: abort after K consecutive transport failures, so a dead server fails
  in seconds rather than emitting 10,807 issues.
- Because the store persists incrementally (Stage 4), an aborted run loses no model work.

### Stage 6 — Bounded concurrency

Copy `acquisition/`'s pattern rather than inventing one:

- `RateLimiter` (`acquisition/core/sec_client.py:61-88`) — lock-guarded, monotonic reservation,
  injected clock, sleeps outside the lock.
- Index-slotted pool (`acquisition/stages/download/local_download.py:74-91`) — preallocate
  `[None] * n`, workers write by index, `ThreadPoolExecutor` + `as_completed`, progress fired from
  the **collecting** thread.
- Config keys mirroring `config/fetch.yaml:26-27`: `max_concurrency`, `requests_per_second`.

**Two hard prerequisites:**

1. **Fix the delta race.** `LaneRunner._one` computes per-candidate counts by subtracting *shared*
   list lengths (`lane_execution.py:118`, `:130-132`). Under concurrency this mis-attributes counts.
   Each candidate must get its own buffer, merged by the parent.
2. **Reconstruct order exactly.** `lane_outputs.render` emits **insertion order**
   (`lane_outputs.py:45-56`), and `jsonl_catalog._ordinal_id` (`:45-55`) mints `issue_id` /
   `rejection_id` from occurrence position — so reordering issues *changes ids*. Order must be
   restored to `(lane order) × (passage_id order) × (within-candidate emission order)` before
   `render`. Index-slotted collection gives this for free; sorting afterwards does not.

**Bound the pool to the server's actual parallel slots.** A local llama.cpp with `-np 1` turns
concurrency into queueing, and per-request latency rises toward `timeout_seconds` — the one error
path deliberately **not** retried (`local_openai_compatible.py:186-191`,`:201`). Raise
`timeout_seconds` with the pool size, or set `-np` to match.

Also: `AnswerStore.put` must be lock-guarded, and the store must not be written while workers are
in flight — `render()` iterates `sorted(self._answers)` and would raise on a concurrent insert.

### Stage 7 — Downstream selection

- `config/story.yaml demo.graph_run_id` becomes **optional**; add `--graph-run-id` to
  `story demo`/`story ui`. Resolution order: explicit flag → config pin → the sole finished
  projection under `graph_runs_root`, **refusing with the candidate list when there is more than
  one**. This mirrors `graph inspect`'s optional positional (`graph/cli.py:114-131`).
- Keeps the pin authoritative when set (so `config_hash` still moves `story_run_id` deliberately),
  removes the silent-staleness window, adds no new state.

### Stage 8 — Benchmark comparison harness

- Expose the existing `directory` parameter of `write_reports` (`narrative_runner.py:1929-1937`,
  `event_runner.py:1286-1292`) as `--reports-dir`, so a second model does not overwrite the first at
  the fixed stem (`REPORT_STEM`, `narrative_runner.py:101`).
- Expose the existing `store_path` parameter of `build_report` as `--answer-store`. **Required, not
  optional:** one store file holds one model on this path (§0).
- No report-format change needed — the `model` and `answer_store` blocks already exist.
- While here: the module docstring's "Sixteen subcommands, eight of which write" is stale — there
  are **18, of which 9 write**.

---

## 5. Invariants that must remain true

**Architectural (each has a test today):**

1. No stage but the narrative one may name a provider —
   `tests/extraction/test_provider.py::test_no_stage_can_reach_a_provider_except_the_lane_the_contract_names`.
2. Importing the narrative package must not load an HTTP client —
   `test_narrative_lane.py::test_importing_the_narrative_package_does_not_load_an_http_client`
   (a subprocess check on `sys.modules`). **The new adapter must go in `_LAZY`.**
3. The provider package imports no lane and no ontology —
   `test_provider.py::test_the_provider_package_does_not_import_a_lane_or_the_ontology`.
4. Nothing under `extraction/` may name the benchmark answers directory — seven AST guards
   (`test_integrated_run.py:710`, `test_narrative_lane.py:1597`, and five others). **A corpus-scale
   store path must therefore stay in configuration**, exactly as `config/extraction.yaml:38-40`
   does today.
5. `extraction/` must never import `story/` —
   `tests/story/test_story_package_structure.py::test_upstream_packages_never_import_story[extraction]`,
   whose docstring is *"The factual spine must build, test and ship with `story/` deleted."*
   **Story provider code cannot be reused by import.** The repo's own precedent for this exact
   situation is `story/contracts.py:93-98`: *"The adapter is story-owned rather than borrowed …
   The duplication is a handful of lines and is named where it happens."*
6. `core/` never imports a stage; no catch-all module names.

**Behavioural:**

7. Catalogs rebuild byte-identically from the same `lane_outputs.jsonl`
   (`test_integrated_run.py:230`), and `report.md` regenerates byte-identically (`:239`).
8. Model output continues to flow through `response_mapping`/`event_mapping` → `assembly` →
   `validation` → ontology `validate_claims`. **No gate is relaxed to raise graph population.**
   A live run should *increase* substantive refusals and rejections, not decrease them.
9. `run.complete` is written last and lists every artifact. **A resume journal must not live inside
   the finished run directory** — resume is a property of the answer store, which lives outside it.
10. `PROMPT_EXCEEDS_CONTEXT` keeps firing before the store lookup and before any provider call, so
    a context refusal is never confused with a cache miss.
11. A partial run can never present as complete: staging directory, marker last, atomic rename.

---

## 6. Tests required per stage

| Stage | New | Amended |
| --- | --- | --- |
| 1 | `test_run_identity.py`: `--limit` yields a different id; a complete run is refused without `--force`; `--force` replaces; store-digest change moves the id | any test asserting a literal run id |
| 2 | manifest carries derived mode + attempted/succeeded/replayed counts | **`test_integrated_run.py:777-778`** (the two literals) |
| 3 | `test_hosted_openai.py`: body carries auth and **no** `chat_template_kwargs`; key never from YAML; `SecretStr` masks in repr; health hits `/v1/models`; `kind` dispatch; protocol driven not `isinstance` | `test_provider.py::test_the_shipped_config_matches_the_validated_runtime` (pins every `provider:` key) |
| 4 | store flush is atomic and resumable; a killed run re-hits every persisted answer; `NO_STORED_ANSWER` absent in live modes | `test_integrated_run.py` replay assertions |
| 5 | each transport fault becomes an issue, not an abort; circuit breaker trips after K | — |
| 6 | concurrent run produces **byte-identical** `lane_outputs.jsonl` and catalogs vs serial (the decisive test); per-candidate counts correct; `put` under contention | progress-callback tests if any assert order |
| 7 | resolution order flag → pin → sole projection; ambiguity refused with the list | `tests/story/` pinned-id tests |
| 8 | two models write two report sets without collision | — |

**Live tests** (`-m live`) already exist as the template: `tests/extraction/test_{narrative_lane,event_lane,provider,embeddings}_live.py`. Add hosted-provider equivalents, gated the same way, so the offline suite never needs a key or a GPU.

---

## 7. Benchmark / model-comparison procedure

Run **before** any full-corpus commitment. The harness needs only Stage 8's report-path change.

Stage 8 must expose **two** existing parameters, not one: `write_reports(directory=…)`
(`narrative_runner.py:1929`, `event_runner.py:1286`) **and** `build_report(store_path=…)`
(`narrative_runner.py:944-951`, `event_runner.py:540-546`). Both already exist; no verb exposes
either.

```bash
# 1. Baseline — reproduce the committed reports offline, no model.
python -m benchmarks.extraction.v1 narrative-report --reports-dir reports/qwen-8192
python -m benchmarks.extraction.v1 event-report     --reports-dir reports/qwen-8192

# 2. Qwen at a larger context. Start llama.cpp with -c 32768, set
#    provider.context_tokens: 32768, then RECORD into its OWN store:
python -m benchmarks.extraction.v1 narrative-build     --answer-store answers/qwen-32768/narrative_v1.jsonl --reports-dir reports/qwen-32768
python -m benchmarks.extraction.v1 event-build     --answer-store answers/qwen-32768/event_v1.jsonl     --reports-dir reports/qwen-32768

# 3. OpenAI. Set provider.kind + model, export the key, then:
python -m benchmarks.extraction.v1 narrative-build     --answer-store answers/openai-<model>/narrative_v1.jsonl --reports-dir reports/openai-<model>
python -m benchmarks.extraction.v1 event-build     --answer-store answers/openai-<model>/event_v1.jsonl     --reports-dir reports/openai-<model>
```

**Scope of the comparison, measured.** The runners do not each cover all 26 cases:
`narrative_runner.build_report` scores **14** cases (`lane in {narrative, either}`) across both
scopes; `event_runner.build_report` scores **3** (those declaring `gold_events`/`gold_relationships`);
the table runner scores 11 and needs no provider. So a model comparison is decided on **17 cases**,
which is small — treat it as a screen, not a proof, and pair it with the bounded live run in §8
step 2.

Compare across the report sets on the dimensions each lane already scores — **12 for the
narrative lane** (metric identity, value, unit, scale, period, subject, evidence, population
wording, ambiguity codes, expected abstentions honoured, ambiguity preserved, abstention code
agreement) and **16 for the event/relationship lane** (event type identity, occurrence date,
announcement date, three participant dimensions, event properties, event evidence, five
relationship dimensions, abstentions honoured, abstention code agreement). Measured at this
commit from the committed report JSON. Each report already names its own `model.identity`,
`context_tokens`, `max_output_tokens` and `output_budget`, so the comparison needs no new field.

**Two cautions carried from the audit:**

- **Do not expect the context bump to change scores.** Zero `PROMPT_EXCEEDS_CONTEXT` occurred across
  10,807 formed requests, and the guard runs before the call. The context is a *cost/headroom*
  variable here, not a correctness one. Report it as such.
- **Raising `context_tokens` invalidates part of the cache.** `max_tokens` is a digest input and
  `budget = min(ceiling, context_tokens − estimated)`, so every context-bound request re-keys —
  **10 of the 15 current stored answers** would stop matching. Expect step 2 to re-record, and
  budget GPU time for it.

Do **not** point two models at one answer-store file: the benchmark's replay provider derives
its identity from the store's rows and raises when they disagree (§0).

---

## 8. Safe full-corpus rerun procedure

Only after the benchmark decides the model.

```bash
# 0. Preserve the current run — the id will differ, but be explicit.
cp -r data/extraction_runs/extract-v1-lexical-833f7bcfbce9 data/superseded_runs/   # operator step

# 1. Smoke: ten candidates, live_cached. With Stage 1 this CANNOT touch the full run.
python -m extraction run --mode live_cached --limit 10 --lanes narrative,events

# 2. One document end to end.
python -m extraction run --mode live_cached --documents <document_id>

# 3. Full corpus. Resumable: a crash loses no answered request.
python -m extraction run --mode live_cached --concurrency <slots>

# 4. If it dies, simply repeat step 3 — every persisted answer is a store hit.
```

**Sequencing note:** the answer store must be flushed and *committed* before the identity-bearing
run. Because Stage 1 puts store digests in the run id, the id moves while answers are being
recorded — which is correct (the run really did read different bytes) but means the final,
citable run is the one executed against a settled store.

---

## 9. Downstream rebuild / load / verify

Verified against the current CLIs. Steps marked ⚠ are manual today; Stage 7 removes ⚠ at step 5.

```bash
python -m extraction runs                                  # ⚠ read the new extraction id
python -m graph project <extraction_run_id>                # new graph id (always — see below)
python -m graph runs                                       # ⚠ read the new graph id
python -m graph load <graph_run_id> --replace              # ⚠ --replace is MANDATORY
python -m graph verify <graph_run_id>                      # expect 27/27
python -m story ui --graph-run-id <graph_run_id>           # Stage 7; today: hand-edit config
```

**Facts that shape this:**

- **A new extraction run always yields a new graph run id.** `make_graph_run_id` digests
  `input_content_digest`, which hashes `run.complete` + all seven catalogs + row counts
  (`graph/core/manifest.py:75-101`). The graph layer already defends itself against extraction's
  identity defect.
- **`--replace` is mandatory and destroys the previous load.** Neo4j Community hosts one database;
  `decide_replacement` refuses when the DB holds a different run
  (`graph/stages/load/lifecycle.py:523-525`), and `--replace` wipes *everything*. `config/graph.yaml`'s
  `load.replace_policy` is read and reported but **can never wipe**.
- **Graph runs coexist on disk, never in Neo4j.** Old exports are preserved under
  `data/graph_runs/`; restoring one is `graph load <old-id> --replace`.
- **The freshness gate will not catch a new run in a new directory.** Its extraction-comparing
  checks (`package_input_digest`, `extraction_run_contents`) detect *in-place mutation* of the run
  the graph manifest names. What catches a swapped load is the four `*_graph_run_id` checks against
  the pin — which is exactly why Stage 7 matters.
- **`config/story.yaml demo.generation_stores` also goes stale**: a new graph ⇒ different packages ⇒
  different prompts ⇒ every replay lookup misses. Expect `--live` story runs, or re-record.
  *(Renamed 2026-08-23: the key is now plural and keyed per provider at `config/story.yaml:220`,
  because `request_identity` digests the adapter — one provider's recorded rows are a guaranteed
  miss for another. The pre-S12 scalar `demo.generation_store` survives at `:231` as the local
  adapter's fallback only, so a rerun has **two** keys to re-record against, not one.)*

---

## 10. Risks and non-goals

### Risks

| # | Risk | Mitigation in plan |
| --- | --- | --- |
| R1 | A smoke run deletes the current run **before** Stage 1 lands | Stage 1 is first; until then, do not run `extraction run` at all |
| R2 | Transport fault kills a 40-hour run | Stage 5 + Stage 4 persistence: work survives, rerun resumes |
| R3 | Concurrency silently changes issue ids via ordering | Stage 6's decisive test: concurrent vs serial byte-identity |
| R4 | Local server queues under a pool larger than its slots; timeouts are not retried | bound the pool to `-np`; raise `timeout_seconds` with pool size |
| R5 | **Entity resolution is deliberately not attempted** (`event_mapping.py:33-38`) — ids are minted from printed names. Invisible at 14 answered candidates; at ~10.8k it fills the graph with spelling-variant duplicates and per-event unresolved placeholders | **Not solved here.** Must be measured on the benchmark and on a bounded live run *before* committing to the full corpus |
| R6 | Cost/time: ~10,807 requests, serial ≈90–180h at the recorded ~67 tok/s | Stage 6; and the benchmark decides the model first |
| R7 | An OpenAI snapshot roll changes `provider_model_id` while `model_id` (the digest key) stays put | pin a dated snapshot; assert the wire model in a live test |
| R8 | A concurrent session is editing this tree (`prototyping-financial-knowlege-graph-35`, busy, 5d) | branch before starting; re-baseline the suite |
| R9 | Stage 2 changes a manifest field readers trust | called out explicitly; the amended test documents the new meaning |

### Non-goals

- **Not** the Research Agent, post generation, cached posts, or precomputed story questions. The
  graph is the precomputed artifact; posts are live and later.
- **Not** a migration for the existing 10,852 `:NotAttempted` nodes. A successful run replaces them
  naturally: the graph is a whole-run projection, so they disappear by rebuild, replaced by claims,
  substantive refusals and substantive rejections.
- **Not** a distributed job system. Resume is the answer store; the pool is `ThreadPoolExecutor`.
- **Not** entity resolution, scoping recall (the 479 pre-request `UNRESOLVED_METRIC` refusals), or
  any new event type. All are independent of the provider.
- **Not** a change to any validation gate.
- **Not** a new dependency.

---

## Recommendation

Implement in three groups, stopping for a decision after each.

**Group A — safety (Stages 1–2).** Small, self-contained, no provider work, and it removes a live
hazard that exists today. Nothing else should be attempted first, because every later stage runs
extraction and any run currently overwrites the graph's source of truth.

**Group B — capability (Stages 3–5).** Provider selection, the hosted adapter, modes, persistence
and transport-fault policy. At the end of Group B a live run is possible and safe but still serial —
which is fine for the benchmark, which is small.

→ **Decision point: run the model comparison (§7) here, before Group C.** It is cheap, it needs no
concurrency, and it decides both the model and whether the Qwen context bump is worth its
re-recording cost. It should also be used to measure R5 (entity duplication) on real output.

**Group C — scale (Stages 6–8).** Concurrency, downstream selection, benchmark report paths. Only
worth doing once a model has been chosen, and Stage 6 is the only stage with a genuine correctness
risk (ordering ⇒ issue ids), so it deserves the byte-identity test as its acceptance criterion.

Two things worth settling before Group A starts: whether `provider.max_output_tokens` should become
true or be deleted (it is currently a lie), and whether the corpus-scale answer store belongs
outside `benchmarks/` — the AST guards mean its path must stay in configuration either way.
