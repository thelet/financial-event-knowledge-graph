# Extraction model-path audit — why narrative/event candidates never reached a model

**Audit date:** 2026-08-18. **Read-only.** No code, config, data, artifact or migration was
modified. **Repo state:** `74f4f1c` (working tree carries unrelated in-flight `story/demo_ui/`
work). **Subject run:** `data/extraction_runs/extract-v1-lexical-833f7bcfbce9` →
`data/graph_runs/graph-v1-0483dc6b4b10`. Both unchanged since 2026-08-03.

**Re-verified 2026-08-23 at `ff3b08f`. The conclusion stands unchanged and is not superseded:**
`extraction/context.py:132-133` still hard-codes `inner=None`, `python -m extraction run` still has
no `--live` flag, `extraction/context.py:83` still builds a pathless `AnswerStore()`, and
`grep -rn "ThreadPool\|asyncio\|concurrent.futures"` over `extraction/` still returns nothing. The
subject run is byte-frozen. Five *pointers* had moved and are corrected in place below (§6.3, §8);
nothing else in this document changed.

---

## 1. Executive conclusion

**The narrative and event candidates were never sent to Qwen because the extraction pipeline has
no code path that can send them to anything.**

`extraction/context.py:132-133` — the sole composition root — constructs the provider as:

```python
resolved_provider = provider or ReplayingGenerationProvider(
    answers, inner=None, model_id=provider_config.model, prompt_version=PROMPT_VERSION)
```

`inner=None` is a hard-coded literal. With no inner provider, a store miss raises
`MissingAnswerError` (`extraction/stages/narrative/answer_store.py:288-292`), which
`lane_execution.py:189-195` records as `NO_STORED_ANSWER` / `NOT_ATTEMPTED`. `extraction/cli.py::cmd_run`
never passes `provider=`, and `python -m extraction run` has **no `--live` flag**.

`LocalOpenAICompatibleGenerationProvider` exists and works, but is constructed in exactly two
non-test places, both under `benchmarks/extraction/v1/__main__.py` (lines 428, 604). **It is never
constructed in the extraction pipeline.**

Three corrections to the working hypothesis:

| Belief | Finding |
| --- | --- |
| `provider_calls_permitted: 0` recorded a deliberate run bound | It is a **hard-coded literal** in `extraction/pipeline.py::bounds_block` (line 95). It is written as `0` on every run and reflects no configuration. It is a *statement of the architecture*, not a measurement or a setting. |
| The context window blocked candidates | **Zero** `PROMPT_EXCEEDS_CONTEXT` issues across 17,130. The guard exists, runs *before* the provider call, and never fired. |
| Missing stored answers were the cause | They are the **mechanism**, not the cause. The store is missing answers because nothing in the pipeline can generate one. |

The 15 stored answers exist because the **benchmark** runners (`narrative-build`, `event-build`)
wire a live provider over the 26 reviewed cases and persist the result. There is no code path
anywhere in the repository that runs the 12,442-passage corpus through a live model.

---

## 2. Current extraction architecture

### 2.1 End-to-end flow

```text
data/normalization_catalog/passages.jsonl        294 docs · 12,442 passages
   ↓  TypedCandidateSelector (config/selection_policy.yaml + AliasIndex)
11,848 candidates, routed per lane            (+14 'unselected' rows)
   ├── tables    → DeterministicTableClaimLane        NO PROVIDER
   └── narrative/events → LexicalOntologyCandidateScope → build_prompt
                          ↓
                     output_budget(prompt, ceiling, context_tokens)   ← CONTEXT GUARD
                          ↓ budget < 512 → PROMPT_EXCEEDS_CONTEXT (never fired)
                     request_identity(prompt, schema, model_id, temperature, max_tokens)
                          ↓
                     ReplayingGenerationProvider.generate
                          ├── store hit  → replayed GenerationResult
                          └── store miss + inner=None → MissingAnswerError
                                              ↓
                                    NO_STORED_ANSWER / NOT_ATTEMPTED
   ↓  response_mapping / event_mapping  (untrusted dict → typed claim | issue)
   ↓  core/assembly + core/validation + ontology validate_claims
lane_outputs.jsonl (authoritative, 31,706 rows)
   ↓  read back from disk, then 7 derived catalogs, 5 self-checks, report.md
   ↓  run.complete written LAST
data/extraction_runs/<run_id>/
   ↓  python -m graph project <run_id>          PURE
data/graph_runs/<graph_run_id>/{nodes,edges}.jsonl
   ↓  python -m graph load <graph_run_id> --replace
Neo4j → story freshness gate → detection → packaging → planner/writer → UI
```

### 2.2 Provider selection and instantiation

* `ProviderConfig.from_config` (`extraction/providers/public.py:117`) reads `provider:` from
  `config/extraction.yaml` and validates at load time.
* **`kind` is never dispatched on.** `grep` for `.kind ==` across `extraction/` and `benchmarks/`
  returns nothing. Setting `kind: openai` changes nothing.
* The composition root uses only `provider_config.model` (digest identity) and
  `provider_config.context_tokens` (passed to both lanes). **`max_output_tokens` is not passed** —
  the lanes fall back to `DEFAULT_MAX_OUTPUT_TOKENS = 4096`
  (`extraction/stages/narrative/narrative_lane.py:74`). So `config/extraction.yaml`'s
  `max_output_tokens: 1024` is **dead configuration for the integrated run**; it applies only to a
  live `LocalOpenAICompatibleGenerationProvider`, which the run never builds.

### 2.3 Replay vs live

`ReplayingGenerationProvider` is a decorator, not a branch. With `inner=None` the store is
authoritative and a miss raises. With an inner provider it calls through and `store.put()`s the
result — **in memory only**. `AnswerStore.write()` exists (`answer_store.py:225`) but is **never
called from `extraction/`**; only `benchmarks/` persists. Worse, `extraction/context.py::load_answers`
builds a **pathless** store (`AnswerStore()`, line 83), so even an injected live provider would have
nowhere to write.

### 2.4 Validation after the model

Model output is never trusted: `response_mapping.map_answer` / `event_mapping.map_answer` convert a
parsed dict to typed claims or typed refusals. `event_mapping.py:8-38` states three repairs it
refuses to make — dating an event from document metadata, substituting a parent for an unnamed
participant, attaching a third party's role to the registrant. **Entity resolution is deliberately
not attempted.** Then `core/assembly` → `core/validation` → the ontology's `validate_claims`.

---

## 3. What happened in the graph-producing run

`data/extraction_runs/extract-v1-lexical-833f7bcfbce9/manifest.json`:

| Field | Value |
| --- | --- |
| `provider.mode` | **`replay_only`** |
| `provider.model_id` | `Qwen3.5-9B-Q4_K_M.gguf` |
| `provider.context_tokens` | `8192` |
| `provider.recorded_answers` | `15` |
| `bounds.provider_calls_permitted` | `0` — hard-coded literal, see §1 |
| `bounds.candidate_limit_per_lane` | `null` (no limit) |
| `bounds.documents_requested` | `null` (whole corpus) |
| `counts.candidates` | `11,848` |
| `code_commit` | `f8ebd3a9…` |

Measured from `lane_outputs.jsonl` (11,848 `outcome` rows):

| Lane | Outcomes | Answered | `NO_STORED_ANSWER` | Refused before any request |
| --- | ---: | ---: | ---: | ---: |
| tables | 503 | — (no provider) | 0 | — |
| narrative | 3,072 | **11** | 2,582 | **479** (all `UNRESOLVED_METRIC`) |
| events | 8,273 | **3** | 8,270 | 0 |

* **Distinct model request digests formed: 10,807** (narrative 2,593 + events 8,214).
* **Answered: 14** of 15 stored answers were consumed; all from the store.
* **HTTP requests attempted: 0.** `report.md:7` states it: *"The narrative and event lanes replay
  recorded provider answers and issue no new request."*

`request_issued` (`extract/public.py:162`) is set from `extraction.request_sha256 is not None`
(`lane_execution.py:231`) — it means **"a request was formed and answered"**, not "a socket was
opened". Under `replay_only` the two diverge, and the report's `requests_issued` field is a misnomer
worth knowing about.

### The three-way distinction the question asks for

| Category | Count | Evidence |
| --- | ---: | --- |
| **Not attempted** — no request ever left the process | **10,852** | `NO_STORED_ANSWER`, severity `not_attempted` |
| **Refused locally before a request was formed** | **479** | narrative `UNRESOLVED_METRIC`; scope offered no concept, so no prompt was built |
| **Model answered, downstream validation rejected** | **49** | severity `rejection`: `QUOTED_SPAN_NOT_IN_PASSAGE` 10, `PERIOD_NOT_GROUNDED_IN_PASSAGE` 9, `PROPERTY_VALUE_NOT_IN_PASSAGE` 4, `DEFERRED_REQUIRED_SOURCE_LANE` 17, others 9 |
| **Model answered, lane refused the finding** | 6,228 total refusals, of which ~560 narrative/event | `DERIVED_COMPARISON`, `DEFINITIONAL_NOT_OBSERVATIONAL`, `GUIDANCE_NOT_REPORTED`, `PARTICIPANT_NOT_NAMED`, … |
| **Model attempted and failed (transport/schema)** | **0** | no `MODEL_ANSWER_UNUSABLE`, no provider error codes anywhere |

The 14 answered candidates produced all 14 narrative observations, all 6 events and all 4
relationship claims in the graph. The other 2,690 observations came from the deterministic table
lane.

---

## 4. Why content did or did not reach the model — root cause tree

```text
Narrative/event candidate did not become a claim
├── (10,852) No answer in the store, and no server to ask
│   └── ROOT CAUSE: extraction/context.py:132 hard-codes inner=None
│       ├── CLI cannot override — cmd_run never passes provider=, no --live flag
│       ├── Store held 15 answers, produced by benchmarks/ over 26 reviewed cases
│       ├── Even with a provider injected, answers could not persist
│       │   (load_answers builds a pathless AnswerStore; .write() never called in extraction/)
│       └── NOT caused by: context limits (0 refusals), token budgets, candidate_limit
│                          (null), documents_requested (null), or any config flag
├── (479) Lexical scope offered zero concepts → UNRESOLVED_METRIC, no prompt built
│   └── A scoping-recall limitation, independent of the provider
├── (~560) Model answered; the lane refused the finding on ontology grounds
└── (49) Model answered; validation rejected the payload
```

**`provider_calls_permitted: 0` is a consequence of the architecture, not a cause.** No
configuration value anywhere disables live calls, because no configuration value enables them.

---

## 5. Qwen context-window assessment

**The context window was not a factor in this run, and enlarging it would not have changed the
outcome.**

Evidence:

1. **The guard exists and runs before the provider call.** `narrative_lane.py:236-249` and
   `event_lane.py:155-166` compute `output_budget(prompt, ceiling=4096, context_tokens=8192)` and
   emit `PROMPT_EXCEEDS_CONTEXT` when the remainder falls below `MIN_OUTPUT_TOKENS = 512`. This is
   evaluated **before** `request_identity` and before `provider.generate`.
2. **It never fired.** `PROMPT_EXCEEDS_CONTEXT` appears **0 times** in 17,130 issues. Because the
   check precedes the store lookup, this covers all **10,807** formed requests, not just the 15
   recorded ones.
3. **Recorded headroom.** The 15 stored answers carry `max_tokens` of 4096 ×5, 3861, 3687, 3581,
   3490, 3364, 3158, 3134, 2125, 1870, 1793. Since `budget = min(4096, 8192 − estimated)`, the
   largest prompt in the store is ≈ **6,399 estimated tokens against an 8,192 slot**, still leaving
   1,793 for output — 3.5× the 512 floor.
4. The estimator deliberately **over-counts** (`_CHARACTERS_PER_TOKEN = 3.5`, below every measured
   ratio of 3.57–4.55) plus `_REQUEST_OVERHEAD_TOKENS = 128`, so it errs toward refusing.

### The consequence that *does* matter for a rerun

`max_tokens` is a **digest input** to `request_identity` (`answer_store.py:96`). Raising
`context_tokens` from 8,192 to 32K/64K changes `budget = min(4096, context_tokens − estimated)` for
every prompt where `estimated > 4096`. **That re-keys those requests and invalidates the stored
answers.** Of the 15 stored answers, **10 have `max_tokens < 4096`** and would stop matching. The
5 at the 4,096 ceiling would survive.

So enlarging the context is not merely useless for the recorded run — it is mildly destructive to
the replay corpus unless the answers are re-recorded.

---

## 6. Current live-provider capability

### 6.1 Can the pipeline make real calls today?

**No.** `python -m extraction run` cannot, for the reasons in §1. The only live paths are
`python -m benchmarks.extraction.v1 narrative-build` and `event-build`, which are scoped to the
reviewed cases, not the corpus.

### 6.2 Implementations that exist

| Class | File | Used by |
| --- | --- | --- |
| `LocalOpenAICompatibleGenerationProvider` | `extraction/providers/local_openai_compatible.py:66` | benchmarks + live tests only |
| `LocalOpenAICompatibleEmbeddingProvider` | `extraction/providers/local_openai_compatible_embeddings.py:73` | hybrid scoping (no committed vector cache) |
| `ReplayingGenerationProvider` | `extraction/stages/narrative/answer_store.py:238` | the pipeline, always with `inner=None` |

### 6.3 Is the adapter usable against the OpenAI API?

**Not as written.** Three concrete blockers, all in `extraction/providers/`:

1. **No authentication of any kind.** `grep -n "api_key|Authorization|headers"` over
   `extraction/providers/*.py` returns **no matches**. `ProviderConfig` (line 99) has fields
   `kind, base_url, model, context_tokens, max_output_tokens, temperature, timeout_seconds,
   max_retries, enable_thinking` — and no key. (Contrast `story/providers/public.py`, which has
   `api_key: SecretStr`, `ENV_API_KEY = "STORY_LLM_API_KEY"` and `authorization_headers`. The
   story layer solved this; extraction did not.) **Understated as of 2026-08-23:** S12 has
   since landed a *second* story adapter — `story/providers/openai_responses.py`, written
   against OpenAI's Responses API — with `ENV_OPENAI_API_KEY = "OPENAI_API_KEY"`
   (`story/providers/public.py:122`) and `--provider` / `--model` flags on
   `python -m story demo` (`story/cli.py:301,304`). The gap between the two layers is now
   wider, not narrower *(verified 2026-08-23)*.
2. **A llama.cpp-only body field is sent unconditionally.** `request_body` (line 163) always emits
   `"chat_template_kwargs": {"enable_thinking": False}`. OpenAI rejects unknown body parameters.
3. **`model` is a GGUF filename**, and `kind` is decorative — nothing dispatches on it.

`response_format: {type: json_schema, json_schema: {name, strict: true, schema}}` is the correct
OpenAI Structured Outputs shape, so the schema half would likely transfer; the transport half
would not.

### 6.4 Where each setting lives

| Setting | Location | Reaches the integrated run? |
| --- | --- | --- |
| `base_url`, `model`, `temperature`, `timeout_seconds`, `max_retries`, `enable_thinking` | `config/extraction.yaml` `provider:` | only via `ProviderConfig`; the run uses `model` for digest identity only |
| `context_tokens` | same | **yes** — passed to both lanes |
| `max_output_tokens` | same | **no** — lanes use `DEFAULT_MAX_OUTPUT_TOKENS = 4096` |
| answer stores | `config/extraction.yaml` `run.answer_stores` | **yes** — "the whole of the run's provider policy" |
| API key | **nowhere** | — |
| concurrency | **nowhere** — the run is fully serial | — |
| provider-call limit | **nowhere** — no such control exists | — |

### 6.5 Is live disabled by a flag?

**No.** There is no flag. The config comment at `config/extraction.yaml:19-23` states the policy as
architecture: *"A run **regenerates no provider answer**: it replays what was recorded."* And
`cmd_run` prints a hard-coded banner: `f"  answers    {len(context.answers)} recorded, replay only"`.

---

## 7. What a real rerun would require (configuration/commands only — nothing performed)

### 7.1 Both paths need the same missing piece first

Neither a local Qwen nor an OpenAI run is reachable by configuration alone. **Code changes are
required** in at least these places:

1. `extraction/context.py` — construct a live provider and pass it as `inner=`.
2. `extraction/cli.py` — a `--live` flag on `run` (the story CLI's `--live` is the precedent).
3. `extraction/context.py::load_answers` — give the store a writable path, and call
   `AnswerStore.write()` after the run, or all model work is lost at process exit.

### 7.2 Local Qwen with a larger context

* Start llama.cpp with `-c 32768` (or 65536) on `127.0.0.1:8080` serving the same GGUF.
* `config/extraction.yaml` → `provider.context_tokens: 32768`.
* **Expect the 15 stored answers to stop matching** for the 10 whose budget was context-bound (§5).
* `provider.max_output_tokens` will still be ignored by the lanes; the effective ceiling stays
  `DEFAULT_MAX_OUTPUT_TOKENS = 4096` unless `build_extraction_context` is changed to pass it.
* **Changing `context_tokens` changes `config_hash`, which changes the extraction run id** — see
  §9 for why that is the *safe* direction.

### 7.3 External OpenAI API

Everything in 7.1 and 7.2, plus a new or extended adapter providing: an `api_key` field sourced
from the environment (not a tracked file), an `Authorization: Bearer` header, suppression of
`chat_template_kwargs`, and an OpenAI model id. Because `kind` is not dispatched on, a second
adapter also needs a selection mechanism.

### 7.4 Order of operations once the above exists

```bash
python -m extraction run                      # WRITES data/extraction_runs/<run_id>/
python -m graph project <run_id>              # -> data/graph_runs/<graph_run_id>/
python -m graph load <graph_run_id> --replace # -> Neo4j
python -m graph verify <graph_run_id>         # 27 checks (re-run 2026-08-23: 27, all PASS)
# then edit config/story.yaml demo.graph_run_id  (see §8)
python -m story ui
```

---

## 8. Downstream propagation

**"Rerun extraction" is not enough.** Every arrow below is a separate manual invocation, and one
configuration value must be hand-edited.

| Step | Command | Automatic? | Pinning |
| --- | --- | --- | --- |
| extraction catalogs + verification + `run.complete` | `python -m extraction run` | within the run, yes | writes `<run_id>/`; see §9 |
| graph projection | `python -m graph project <run_id>` | **no — names the run** | new catalog bytes → new `input_content_digest` → **new `graph_run_id`** |
| graph load | `python -m graph load <id> --replace` | **no** | `--replace` is the only wipe authority; a different run id in the DB is refused |
| graph verification | `python -m graph verify <id>` | **no** | 27 checks *(re-run 2026-08-23 against `graph-v1-0483dc6b4b10`: `0 of 27 checks failed`; the count is `len(self.checks)` at `graph/core/verification_report.py:238`, not a literal, and `build_report` is documented as "the twenty-seven checks")* |
| story freshness / detection / packaging | — | **no** | **`config/story.yaml:177  graph_run_id: graph-v1-0483dc6b4b10` must be edited by hand** (read at `story/pipeline.py:321`) *(line numbers verified 2026-08-23; the value is unchanged)* |
| demo UI | `python -m story ui` | inherits the config | reads the same pinned value |

`config/story.yaml:177` is the **only functional pin**. If it is not updated, the story layer looks
for a graph run directory that may no longer exist and
`story/core/graph_identity.py::read_graph_identity` raises. If it *is* updated, `config_hash` moves,
which re-keys `story_run_id` and the story replay stores — the recorded demo runs under
`data/story_demo/` become unreachable by digest. The key naming those stores is now
`demo.generation_stores` (`config/story.yaml:220`), **plural and keyed per provider** since S12: a
recorded row is keyed on the adapter that produced it, so one provider's rows are a guaranteed miss
for another. The pre-S12 scalar `demo.generation_store` survives at `config/story.yaml:231` as the
local adapter's fallback only *(verified 2026-08-23)*.

Secondary (non-functional but stale afterwards): `tests/graph/conftest.py:26 REAL_RUN_ID`,
`tests/fixtures/graph/extraction_run/manifest.json`, and **20 docstring and comment censuses across
14 modules** *(re-counted 2026-08-23; the earlier "roughly twenty" was an estimate, never measured)*
— `story/stages/detection/*` 8 mentions in 6 files, `story/stages/packaging/*` 8 in 4,
`story/stages/retrieval/metric_metadata.py` 1, and `story/demo_ui/{projection,table_grid,api}.py` 4
— that quote measurements against `graph-v1-0483dc6b4b10`. The last two `demo_ui` modules and the
`retrieval` one were not in the 2026-08-18 list.

---

## 9. Known blockers and risks before a full rerun

### 9.1 Run identity — the sharpest risk

`make_run_id` (`extraction/core/run_directory.py:44`) digests
`(RUN_LAYOUT_VERSION, config_hash, corpus_id, ontology_definition_hash)`. **Neither the answer-store
contents nor the code commit is an input.**

So populating the answer stores and rerunning with an unchanged `config/extraction.yaml` produces
**the same run id**, and `RunDirectoryWriter.finalize` (line 131-134) does:

```python
final = self.paths.final
if final.exists():
    shutil.rmtree(final)
os.replace(self.paths.staging, final)
```

**A rerun silently destroys and replaces `extract-v1-lexical-833f7bcfbce9` — the directory the
current graph was projected from.** There is no `--replace` guard and no refusal.

This defect class has already fired once in this repository:
`plans/graph/STAGE13_GRAPH_INPUT_HANDOFF.md` describes `extract-v1-lexical-2422c4252c07` with 2,707
observations, while `data/superseded_runs/extract-v1-lexical-2422c4252c07/` holds 2,704.

*Mitigating detail:* any realistic rerun changes `context_tokens` or `answer_stores` in
`config/extraction.yaml`, which moves `config_hash` and therefore the run id. The hazard is
specifically the "same config, new answers" case — which is exactly the shape of "record answers,
then rerun".

### 9.2 No resume, no checkpoint

`RunDirectoryWriter.begin` (line 97-108): *"A fresh staging directory. Any earlier partial is
removed rather than resumed."* Catalogs are written once, at the end, after the whole corpus is
processed. **A crash at hour 40 of a long run loses everything**, and because the answer store is
never persisted by the pipeline (§2.3), it loses the model work too.

### 9.3 Cost and throughput

* **Fully serial.** No `ThreadPool`, no `asyncio`, no `concurrent.futures` anywhere in
  `extraction/`. No concurrency setting exists.
* **10,807 requests** would need to be issued.
* Recorded local throughput: **~67 tokens/s** (`plans/extraction/LOCAL_RUNTIME_VALIDATED.md:38`),
  with observed completions of 1,800–3,981 tokens. At roughly 30–60 s per request that is on the
  order of **90–180 hours of wall clock**, single-threaded, with no checkpoint.

### 9.4 Retry behaviour

`_post_with_retries`: `max_retries = 2` → **3 attempts**, exponential backoff `0.25 × 2ⁿ`,
retrying only `{429, 500, 502, 503, 504}` and transport faults. **A schema-invalid answer is never
retried** — it is a model result (`ProviderSchemaError`). Timeouts are not retried. This is correct
but means a flaky endpoint surfaces as a hard failure mid-run, with no checkpoint to resume from.

### 9.5 Correctness risks that scale badly

* **Entity resolution is deliberately not attempted** (`event_mapping.py:33-38`). Ids are minted
  from printed names by `core/identifiers.entity_id`. At 14 answered candidates this was invisible;
  at 10,807 the graph would accumulate many spelling-variant duplicate entities.
* **Unresolved participants are keyed per event** in the graph
  (`opendoor_unnamed_subsidiary#evt:…`, `graph/core/keys.py`). Deliberate — *"over-splitting is
  visible and countable; over-merging is silent"* — but it guarantees proliferation at scale.
* **No representation gap found.** `offered_vocabulary` (`events_public.py:187`) offers **all 19**
  declared event types with no filtering; nothing is named in code. The graph holds only 3
  (`credit_facility_established` 1, `executive_change` 3, `workforce_reduction` 2) purely because
  only 3 event prompts were ever answered. In particular, the story layer's claim that
  *"no lane emits a `guidance_issuance` event yet"* is a statement about **data**, not capability —
  `guidance_issuance` is declared and is on the menu.
* **`config/extraction.yaml provider.max_output_tokens: 1024` is not read by the run** (§2.2). Any
  reasoning that assumes a 1,024-token output ceiling applied to this run is wrong; the effective
  ceiling was 4,096.

### 9.6 Duplicate / replay behaviour

`AnswerStore` is keyed by request digest and rebuilt whole, never appended (line 161-166), so
re-recording is idempotent. 59 event requests were formed twice in the recorded run (8,273 formed
vs 8,214 distinct) and would collapse to one store key each — harmless, but it means "requests
formed" slightly overstates distinct model work.

---

## 10. `Issue` / `NotAttempted` assessment

**Origin.** One `RunIssue` per refusal, rejection, abstention or diagnostic, written to
`issues.jsonl` and projected by `graph/stages/projection/nodes.py::_issue_nodes` into `:Issue`
nodes with exactly two edges: `FOUND_IN` → the passage, `CONCERNS_METRIC` → each declared metric.

**Why projected.** So a coverage gap is visible in the graph rather than being an absence.

**Which are real failures vs bookkeeping** (of 17,130):

| Class | Count | Meaning |
| --- | ---: | --- |
| `:NotAttempted` (`NO_STORED_ANSWER`) | **10,852** | **Pure bookkeeping** — a question never asked. Says nothing about the filings. |
| `refusal` | 6,228 | Real: the lane or the ontology declined. 4,524 are table-lane `UNRESOLVED_METRIC`. |
| `rejection` | 49 | Real: a produced claim failed validation. Carries `raw_finding_json`. |
| `diagnostic` | 1 | `ANNOUNCEMENT_EQUALS_OCCURRENCE`. |

**After a successful rerun.** Every `:NotAttempted` row whose request gets answered disappears and
is replaced either by a claim or by a *substantive* refusal/rejection. The 10,852 figure should
collapse toward zero; the refusal and rejection counts should rise. Because the graph is a whole-run
projection with no incremental update, this happens by rebuild, not by mutation.

**Do the consumers distinguish diagnostic state from company knowledge?** **Yes, at every layer
checked:**

* `graph/core/models.py` gives `:NotAttempted` its own label precisely so a view can exclude it
  (`nodes.py:836`).
* `story/stages/retrieval/cypher.py` excludes it explicitly — `AND NOT issue:NotAttempted` at lines
  579 and 630 — and the module docstring records why: *"10,852 of 17,130 issues record a question
  that was never asked."*
* `story/demo_ui/projection.py` does not query `:Issue` at all.

The risk is therefore not in the pipeline but in **ad-hoc analysis**: any query that counts
`:Issue` without filtering `:NotAttempted` is measuring the run's provider policy, not the corpus.

---

## 11. Recommended next investigation / decisions

No implementation proposed. Five decisions are needed before any plan is written:

1. **Where should live generation live?** The recording capability exists in `benchmarks/` and the
   run capability in `extraction/`. Decide whether `extraction run --live` becomes a first-class
   path, or whether a corpus-scale *recording* verb feeds the existing replay run. The second
   preserves the replay-determinism property the whole layer is built around.
2. **Answer-store persistence and identity.** A corpus-scale store is ~10,807 rows, not 15. Decide
   where it lives (`benchmarks/…/answers/` is a benchmark directory), whether it is tracked, and
   how it is written incrementally so a crash does not lose hours of GPU time.
3. **Run-identity safety** (§9.1). Decide whether `make_run_id` should digest the answer-store
   contents and/or the code commit, or whether `finalize()` should refuse an existing directory.
   This should be settled *before* a long run, not after it overwrites the current one.
4. **Local Qwen vs OpenAI.** Local needs a context bump plus re-recording (§5); OpenAI needs auth,
   header changes and adapter selection (§6.3). Cost, data-egress and reproducibility all differ.
5. **Scoping recall.** 479 narrative candidates were refused before any prompt because the lexical
   scope offered no concept. That is independent of the provider and would remain after a full
   live run. Decide whether it is in scope for this work.

**One thing worth confirming before anything else:** whether the ~90–180 hour serial estimate
(§9.3) is acceptable, or whether concurrency is a prerequisite. It changes the shape of every other
decision above.
