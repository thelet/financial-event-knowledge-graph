# Live model-backed extraction — implementation plan

**Date:** 2026-08-23. **Baseline commit:** `ff3b08f`. **Status: plan only. No code, config, data,
test or artifact was modified while writing it.**

**Relationship to earlier documents.** `docs/2026-08-18-extraction-model-path-audit/` established
*why* narrative and event candidates never reached a model.
`docs/2026-08-18-live-extraction-implementation-plan/` proposed eight stages against commit
`c0bf241`. **This document supersedes that plan.** Every claim below was re-verified against the
code at `ff3b08f` by five independent read-only inspections, and four of the earlier plan's load-
bearing claims turned out to be wrong or incomplete. They are corrected in §0 rather than quietly
restated, because two of them change the design.

---

## 0. Seven verified findings that shape the design

Each is marked *(verified 2026-08-23)* with how it was checked. Nothing here is inferred from
documentation or from the earlier plan.

### F1 — A smoke run still deletes the graph's source of truth *(verified: code read)*

`make_run_id` digests exactly `(RUN_LAYOUT_VERSION, config_hash, corpus_id,
ontology_definition_hash)` (`extraction/core/run_directory.py:44-59`). `ExtractRequest.lanes`,
`.limit` and `.document_ids` never reach it — they are passed to `bounds_block` and land only in
the manifest (`extraction/pipeline.py:89-99`, `:110`). `finalize()` then does, unconditionally
(`run_directory.py:131-134`):

```python
final = self.paths.final
if final.exists():
    shutil.rmtree(final)
os.replace(self.paths.staging, final)
```

There is no `--force`, no `previous_is_complete` consultation, no refusal. `python -m extraction
run --limit 10` mints the *same* id as the full-corpus run and replaces
`data/extraction_runs/extract-v1-lexical-833f7bcfbce9` — the only extraction run on disk, and the
one `data/graph_runs/graph-v1-0483dc6b4b10` was projected from. **This hazard exists today, before
any of this work.** It is why overwrite safety is Stage 1.

### F2 — Reordering does **not** change issue ids. It changes catalog bytes *(verified: code read; corrects the 2026-08-18 plan)*

The earlier plan asserted that concurrent completion would reorder issues and therefore *change
`issue_id`/`rejection_id`*, and made byte-identity the acceptance criterion for that reason. The
mechanism is different. `_ordinal_id` (`extraction/stages/catalog/jsonl_catalog.py:45-55`) counts
occurrences of `parts = (code, lane, passage_id, row_label, detail)`
(`jsonl_catalog.py:84-85`), and candidates are unique per `(passage_id, lane)`
(`extraction/stages/select/typed_selector.py:142-176`). Every issue is constructed with its own
candidate's `passage_id`, so **every group of identical `parts` lives inside one candidate** and
the occurrence counter is candidate-local. Ids are invariant under inter-candidate reordering.

What reordering *does* break is the file bytes: `lane_outputs.render` emits insertion order
(`extraction/stages/extract/lane_outputs.py:45-56`), the catalogs iterate that file's order
(`jsonl_catalog.py:70-96`), and `catalog_digests` follows (`pipeline.py:169`). Worse,
`report.md` is largely order-insensitive (`run_report.py` sorts or aggregates at `:36`, `:60`,
`:153`, `:163`, `:183`, `:205`), so a reordered run would produce **an identical report over
different catalog bytes** — a silent divergence.

Two consequences for the plan: the byte-identity test must compare `lane_outputs.jsonl` and all
seven catalog digests and **must not** rely on `report.md`; and the id-invariance rests on a
property no test asserts — that no issue is attributed to a passage other than its candidate's.
The table lane already reads `preceding_passage_id` (`lane_execution.py:158-166`) and is one line
from breaking it. **Add that test.**

### F3 — Digesting answer-store contents into the run id would defeat resume *(corrects the 2026-08-18 plan)*

The earlier plan put "the sha256 of each configured answer-store file" into `make_run_id` (its
Stage 1) *and* promised "rerunning is idempotent … every persisted answer is a store hit" (its §8
step 4). These contradict. If the cache is a run-id input and the cache grows during the run, then
the resumed rerun computes a **different** id, lands in a different directory, and resume stops
being idempotent — it becomes a directory generator.

The rule this plan adopts instead:

> **A cache may not change the result, so a cache is never a run-id input.** A *read-only answer
> store* may change the result — in `replay` mode it is the only source of answers — so its digest
> *is* an input.

That separates the two roles cleanly, and §3 makes them two different configuration keys.

### F4 — `prompt_version` is not part of the request digest *(verified: code read; corrects `prompt.py`'s own docstring)*

`request_identity` digests exactly six values, `\x1f`-joined:
`("v1", prompt, json.dumps(schema, sort_keys=True, separators=(",",":")), model_id,
f"{float(temperature):.6f}", str(int(max_tokens)))` — `answer_store.py:81-98`. `prompt_version` is
a *stored row field only* (`answer_store.py:49-52`, `:120`). `prompt.py:9-11` says "`PROMPT_VERSION`
is part of that identity"; that is true only indirectly — the constant is never rendered into the
prompt text, so **bumping it re-keys nothing, and editing the prompt text re-keys everything
whether or not it is bumped.**

This matters for run identity (§5): the run id must move when the prompt moves, and the version
constant is the only readable handle on that. So the constant becomes a run-id input *and* a
pinned-fixture test forces it to be bumped when the rendered text changes.

### F5 — The local server has one slot, and client concurrency against it buys ~1.2× *(verified: measured 2026-08-23 against the running server)*

`llama-server` is up on `127.0.0.1:8080` right now serving
`/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf`. Measured from its own endpoints and
timings:

| Measurement | Value | How |
| --- | --- | --- |
| `total_slots` | **1** | `GET /props` |
| `n_ctx` per slot | **16,384** | `GET /props`, `GET /slots` |
| Generation throughput | **73.6 tok/s** | 600-token completion, `timings.predicted_per_second` |
| Prompt ingestion | **3,070 tok/s** | 9,138-token prompt, `timings.prompt_per_second` |
| 1 request, 300 tokens | **4.88 s** wall | `curl` |
| 4 concurrent, same request | **16.06 s** wall | `curl … &` ×4 |
| GPU | RTX 5060 Ti, 16,311 MiB, 6,155 MiB in use | `nvidia-smi` |

Four concurrent requests took 16.06 s against 4 × 4.88 = 19.5 s serial: **a 1.21× speedup, not
4×.** With one slot the server queues, and per-request latency rose from 4.9 s to ~16 s — straight
toward `timeout_seconds`, which is **the one error the adapter deliberately never retries**
(`local_openai_compatible.py:186-191`, `:201-203`).

Therefore: **client-side concurrency against the local server is not a speedup until the server is
restarted with `-np N`.** The validated runtime records no `-np`
(`plans/extraction/LOCAL_RUNTIME_VALIDATED.md:82-86`), and llama.cpp divides `-c` across slots, so
`-np 4` at the current `-c 16384` would give each slot 4,096 tokens — below the 8,192 the prompt
budget is computed against (`narrative_lane.py:236-249`). Raising `-np` and `-c` together is a GPU
memory question, and §7 makes it a measurement rather than an assumption.

Recomputed serial cost with today's numbers: a typical request is ~3,000–6,400 prompt tokens
(~1–2 s to ingest) plus 1,800–3,981 completion tokens (~25–54 s to generate), so **~27–56 s per
request; 10,807 requests ≈ 81–168 hours.** The audit's 90–180 h estimate stands.

### F6 — A single transport fault aborts the whole run and loses everything *(verified: code read)*

Both model-backed lanes catch only `(ProviderResponseError, ProviderSchemaError)` — model results
— at `narrative_lane.py:267` and `event_lane.py:174`. `ProviderTimeout`, `ProviderUnavailable` and
`ProviderTransportError` (`providers/public.py:50-59`) are caught **nowhere** in `extraction/`.
`LaneRunner._one` catches only `MissingAnswerError` (`lane_execution.py:189`); `pipeline.run` has
no `try`; `cmd_run` has no `try`. And `pipeline.run` extracts the **entire corpus in memory before
`writer.begin()`** (`pipeline.py:106-112`), so nothing is on disk to inspect.

Combined with the fact that `AnswerStore.write()` is **never called from `extraction/`** — the only
callers are `benchmarks/extraction/v1/__main__.py:448` and `:624` — one dead socket at hour 40
discards forty hours of GPU time with a traceback. Under `inner=None` this is latent. The moment a
live provider is wired in, it becomes the dominant failure mode.

### F7 — The answer store at corpus scale is ~48 MB, so flush-by-rewrite is the wrong shape *(verified: measured from the committed stores)*

The 15 committed rows average **4,489 bytes** (max 8,766), because each row carries `raw_content`.
Projected to 10,807 rows: **≈48.5 MB**. `AnswerStore.write()` renders and rewrites the whole file
atomically (`answer_store.py:217-235`) and never appends (`:160-167`). The earlier plan's
"flush every N answers" would rewrite up to 48 MB a few hundred times — ~10 GB of I/O — and each
rewrite is O(n) in a file that is still growing. §4 uses an append-only journal instead, which is
also what this repository's own convention already prescribes: *per-item metadata is authoritative;
global catalogs are derived indexes, rebuilt, never appended to concurrently.*

Also measured, confirming the audit: **10 of the 15 stored answers carry `max_tokens < 4096`**
(1793, 1870, 2125, 3134, 3158, 3364, 3490, 3581, 3687, 3861) and would stop matching if
`context_tokens` moves, because `max_tokens` is a digest input and
`budget = min(ceiling, context_tokens − estimated)`.

---

## 1. Target extraction architecture

```text
data/normalization_catalog/passages.jsonl      294 docs · 12,442 passages
        │
        ▼  TypedCandidateSelector (config/selection_policy.yaml + AliasIndex)
   11,848 candidates, one per (passage_id, lane)
        │
   ┌────┴───────────────────────────────┐
   │                                    │
 tables lane                  narrative / events lanes
 DeterministicTableClaimLane   build_prompt → output_budget guard  ← PROMPT_EXCEEDS_CONTEXT
   (no provider)                        │      (still before every request)
   │                          request_identity(prompt, schema, model_id, temp, max_tokens)
   │                                    │
   │                    ┌───────────────┴────────────────────────────┐
   │                    │      CachingGenerationProvider             │
   │                    │  read stores  ─hit→ replayed result        │
   │                    │  read cache   ─hit→ replayed result        │
   │                    │  miss + mode=replay  → MissingAnswerError  │
   │                    │  miss + mode=live    → inner.generate()    │
   │                    │                        → append to cache   │
   │                    └───────────────┬────────────────────────────┘
   │                                    │
   │                 build_generation_provider(config, mode)   ← extraction/context.py
   │                        ├── kind: local_openai_compatible  (Qwen, llama.cpp)
   │                        └── kind: openai                   (hosted, Responses API)
   │                                    │
   └──────────────┬─────────────────────┘
                  ▼   bounded pool: N workers, rate-limited, index-slotted merge
   response_mapping / event_mapping        UNCHANGED
                  ▼
   core/assembly → core/validation → ontology validate_claims    UNCHANGED
                  ▼
   lane_outputs.jsonl → 7 catalogs → 5 checks → report.md
                  ▼
   run health gate:  healthy → finalize()          → <run_id>/       + run.complete
                     unhealthy → finalize_rejected() → <run_id>.rejected/  (no marker)
                  ▼
   python -m graph project → load --replace → verify → story --graph-run-id
```

**The operating contract in one sentence:** a lane asks the configured provider; the recorded
stores answer first, the cache answers second, the model answers when neither can, and the
manifest records which happened for every candidate.

### 1.1 Provider architecture — extend the existing abstraction, add one sibling adapter

**The port does not change.** `GenerationProvider` (`extraction/contracts.py:122-138`) requires
`model_id: str` and `generate(*, prompt, schema, max_tokens=1024, temperature=0.0) ->
GenerationResult`. Both adapters and the caching decorator satisfy it as written. Nothing
downstream of the lane learns that a model was involved.

**`kind` becomes load-bearing.** Today it is written at `providers/public.py:106,121`, never read,
and not even validated — `ProviderConfig(kind="nonsense")` is accepted (`validated()`,
`public.py:134-167`, does not check it). Target: a `SUPPORTED_KINDS` frozenset, an unknown kind
refused at load, and a single dispatch in `extraction/context.py`, mirroring the shape of
`build_scope` (`context.py:93-103`). The CLI passes a *mode*, never a constructed provider, so
`context.py` remains the only module that names a concrete adapter.

**One config class, per-kind refusals.** `ProviderConfig` gains the fields below. The precedent is
`story/providers/public.py:511-534`, which refuses settings that are meaningless for the selected
kind rather than ignoring them.

| Field | Kinds | Source | Notes |
| --- | --- | --- | --- |
| `kind` | both | YAML | validated against `SUPPORTED_KINDS` |
| `base_url` | both | YAML | `https://api.openai.com/v1` for hosted |
| `model` | both | YAML / `--model` | **the digest identity**; pin a dated snapshot for hosted |
| `context_tokens` | both | YAML | client-side guard; the local server currently offers 16,384 (F5) |
| `max_output_tokens` | both | YAML | **must actually reach the lanes** — see D4 |
| `temperature` | both | YAML | 0.0 |
| `timeout_seconds`, `max_retries` | both | YAML | |
| `enable_thinking` | local only | YAML | refused for `openai` |
| `api_key` | openai only | **environment / `.env` only** | never from YAML — refuse the key there |
| `supports_temperature` | openai only | declared per model | measured, not guessed |
| `reasoning_effort` | openai only | declared per model | refused for local |
| `max_concurrency`, `requests_per_second` | both | YAML / `--concurrency` | §1.6; **not** a run-id input |

**Which OpenAI API — decision and rejected option.**

*Recommended:* the **Responses API** (`POST /responses`), mirroring
`story/providers/openai_responses.py`. Its wire shape was measured against the real endpoint on
2026-08-19 and four non-obvious facts are already recorded there: `text.format` is **flat**
(`name`/`strict`/`schema` siblings of `type`), not nested under `json_schema`; usage is
`input_tokens`/`output_tokens`; `temperature` is model-dependent and `gpt-5-nano` answers one with
HTTP 400; and a 401 body echoes the first eight characters of the key back, which is why every
message built from a response body must go through a redactor rather than
`response.text[:200]` as the local adapter does (`local_openai_compatible.py:214`). Adopting
Responses inherits that paid-for knowledge.

*Rejected:* OpenAI **Chat Completions**, whose body is nearly identical to the local adapter's
(`response_format: {type: json_schema, json_schema: {name, strict, schema}}` is already the exact
shape sent at `local_openai_compatible.py:174-177`), so the diff would be smaller. Rejected because
it re-derives against a second, undocumented-here envelope for a saving of perhaps forty lines, and
because the gpt-5 family is Responses-first. **If the benchmark picks a `gpt-4.1`-class model this
should be revisited** — the trade is genuinely close.

**Duplication is deliberate and named.** `extraction/` may never import `story/`
(`tests/story/test_story_package_structure.py:347`, parametrized over five packages, docstring:
*"The factual spine must build, test and ship with `story/` deleted."*). The retry bound, the
backoff, the never-retry-a-timeout rule and the two content hashes will be the same lines twice.
That is the trade `story/providers/openai_responses.py:44-48` already made explicitly for the same
reason; the new module must say so in its docstring rather than leave it to look accidental.

**Three defects to fix while here, each currently a lie in the manifest or on the wire:**

- **D4 — `max_output_tokens` is dead configuration.** `config/extraction.yaml:56` says `1024`;
  `context.py:137-141` passes only `context_tokens`; the lanes fall back to their own
  `DEFAULT_MAX_OUTPUT_TOKENS = 4096` (`narrative_lane.py:74`, re-exported at `event_lane.py:44-50`).
  There are *two* constants of that name with different values — `providers/public.py:26` is 1024.
  The effective ceiling in the recorded run was 4,096. Either pass the configured value or delete
  the key; passing it is right, because a hosted model needs a different budget and today that
  would mean editing a module constant.
- **`schema_name` is hard-coded** to `"extraction_claim"` for both lanes
  (`local_openai_compatible.py:160`). Narrative and event requests are indistinguishable in a
  capture, and on a hosted API the 400 body names the schema. Thread the lane's own name through.
- **`ProviderConfig.from_config` silently drops unknown keys** (`public.py:116-132`), so an
  `api_key:` accidentally written into YAML would be ignored rather than refused. It must be
  refused, as `story/providers/public.py:921-928` refuses `api_key`/`api_token`/`authorization`/
  `token`.

**Schema portability needs no work.** Both builders — `narrative/public.py:325`
`response_schema(metric_ids, units, period_phrases)` and `narrative/events_public.py:301`
`response_schema(vocabulary, dates)` — emit only `type`, `properties`, `required`, `enum`, `items`
and `additionalProperties: false`, with no `minItems`/`pattern`/`anyOf`/`$ref` anywhere
*(verified 2026-08-23 by grep over `extraction/stages/`)*. That is exactly OpenAI strict Structured
Outputs' subset. Extraction has no `validate_portable_schema` equivalent (story's lives at
`story/providers/portable_schema.py`, 168 lines); restating a small one in
`extraction/providers/` turns "portable by inspection" into "portable by test".

### 1.2 Live vs replay — two knobs, not three modes

**Recommended contract:**

| Knob | Values | Meaning |
| --- | --- | --- |
| `provider.mode` | `replay` \| `live` | **May this run open a socket?** |
| `run.answer_cache` | a path \| `null` | **Where may this run write what the model said?** |

That is two settings, each meaning exactly one thing, and together they express every behaviour
that has a use:

| mode | answer_cache | Behaviour | Use |
| --- | --- | --- | --- |
| `replay` | `null` | stores authoritative; miss → `NO_STORED_ANSWER` | today's behaviour; CI; the offline suite; reproducing a recorded run |
| `replay` | a path | stores + cache read; miss → `NO_STORED_ANSWER` | re-deriving catalogs from a completed live run without a GPU |
| `live` | a path | miss → call the model, append the answer | **the default for real runs** — resume, dedup, cost control |
| `live` | `null` | miss → call the model, keep nothing | a clean measurement with no cache influence and no resume |

`replay` stays the default so nothing that runs offline needs a GPU or a key.

**Rejected:** the earlier plan's three named modes `replay` / `live` / `live_cached`. Three modes
encode a two-dimensional choice in one dimension, and the third state ("call the model but do not
write to the shared store") is expressed here by pointing `answer_cache` at a different file or at
`null` — which is also how you re-ask an identical request you distrust. Two orthogonal knobs are
smaller *and* strictly more expressive.

**Consequence for `NO_STORED_ANSWER`.** It becomes reachable **only** when `mode: replay`. Its own
docstring already claims it is *"a statement about this run's bounds"*
(`extraction/stages/extract/public.py:65-67`); today that is false, because it is the normal
outcome of every model-backed candidate. After this change it is true again.

### 1.3 The AnswerStore's role after the fix — two files with two different jobs

The current single key `run.answer_stores` conflates a *corpus of recorded answers that determines
the result* with *a place to put expensive work*. Split it:

| Key | Read | Written | Tracked | Run-id input | Purpose |
| --- | --- | --- | --- | --- | --- |
| `run.answer_stores` (exists) | yes | **never** | committed | **yes** (file digests) | the 15 reviewed-case answers; in `replay` mode these *are* the model |
| `run.answer_cache` (new) | yes | yes, append-only | gitignored, under `data/` | **no** (F3) | avoid repeating a request; survive a crash; resume |

Recommended default: `data/answer_cache/<model-slug>/answers.jsonl`. One file per model — not
because the digest requires it (`model_id` is a `request_identity` input, `answer_store.py:96`, so
two models can coexist in one file on the *integrated* path, which filters by model at
`context.py:88`) but because the **benchmark** path cannot: its replay provider passes no
`model_id`, identity falls back to `AnswerStore.identity_model_id()`, that returns `None` when rows
disagree, and the constructor then raises (`answer_store.py:203-212`, `:258-262`). One file per
model keeps both paths usable and keeps a model comparison legible.

**Persistence is append-only, and the sorted file is a derived index.** F7 measured the corpus-scale
store at ≈48 MB, so re-rendering it every N answers is the wrong shape. Instead:

- `AnswerStore.append(answer, path)` — one `json.dumps` line, `flush()`, to an open handle. O(1).
  The row format is unchanged, so the appended file loads with the existing `load()` (later rows
  win on a repeated digest, `answer_store.py:180-189`).
- The loader must **skip an unparseable trailing line** rather than raise: a crash mid-write leaves
  a torn last row, and that is the normal case this exists to survive.
- `python -m extraction answers compact <path>` rewrites it sorted and deduplicated through the
  existing atomic `write()` (`.partial` + `os.replace`, `answer_store.py:225-235`). Compaction is
  optional, never required for correctness.

This is the repository's own stated discipline applied unchanged: *per-item metadata is
authoritative; global catalogs are derived indexes, rebuilt, never appended to concurrently.*

**Rename `ReplayingGenerationProvider` → `CachingGenerationProvider`** (`answer_store.py:238`).
`inner=None` stays legal and is what `mode: replay` builds. The old name would be a false statement
about the class's main job. Call sites: `extraction/context.py:132`,
`benchmarks/extraction/v1/__main__.py:437,613`, and the tests that name it.

**What the cache is explicitly not.** It is not a store of future post-generation questions, and
nothing in this design pre-generates a question. The graph is the precomputed artifact; the
Research Agent asks its questions live, later, against the graph.

### 1.4 Resume and crash safety

**Resume is the cache, and nothing else.** There is no journal inside the run directory, no
checkpoint file, no partial catalog. That preserves the property the whole layer is built on: a run
directory is either complete or it is unambiguously not.

The chain that makes ~10k requests survivable:

1. **Every answer is durable the moment it arrives** (§1.3 append). A crash at hour 40 loses at most
   one in-flight request.
2. **A rerun is a cache replay.** Re-running the identical command re-forms the identical request
   digests (`request_identity` is a pure function of prompt, schema, model, temperature and
   max_tokens) and every previously answered candidate is a hit. Idempotent because the cache is
   *not* a run-id input (F3): the rerun lands in the same directory and produces the same bytes.
3. **A transport fault becomes a typed issue, not a traceback** (F6). Both lanes must catch
   `ProviderTimeout` / `ProviderUnavailable` / `ProviderTransportError` and return a new issue code
   `PROVIDER_CALL_FAILED`.
4. **A circuit breaker stops a dead server fast.** After K consecutive transport faults (config,
   default ~20) the run aborts rather than writing 10,807 identical issues. The cache is already
   durable, so aborting costs nothing but the current request.
5. **Partial work cannot masquerade as complete.** A run whose `PROVIDER_CALL_FAILED` count exceeds
   a configured tolerance does **not** call `finalize()`. It calls `finalize_rejected()`, which
   stages into `<run_id>.rejected/` and never writes `run.complete` — exactly the mechanism
   `graph/stages/projection/export.py:506-538` already uses for a rejected projection. `RunDirectory`
   refuses a directory with no marker (`run_directory.py:141-152`) and `list_runs` skips it
   (`:175-182`), so a rejected run is invisible to every downstream reader.

**`run.complete` semantics are unchanged and stay trustworthy**: marker written last, listing every
file with its digest, then an atomic rename.

### 1.5 Run identity and overwrite safety

Two independent mechanisms. Neither substitutes for the other.

**(a) Identity — add one digest, mirroring the graph layer.** `make_run_id`
(`run_directory.py:44-59`) gains a fifth input, `input_identity_digest`, built the way
`graph/core/manifest.py:75-101` builds `input_content_digest` — ordered `name=value` parts, `ABSENT`
for a missing file, then one `digest(*parts)`:

| Part | Why it is in |
| --- | --- |
| `EXTRACTOR_VERSION`, `RUN_LAYOUT_VERSION` | code identity, semantically versioned |
| `PROMPT_VERSION`, `EVENT_PROMPT_VERSION` | the prompt is the request; F4 |
| `mode`, `provider.kind`, `model_id` | a Qwen run and an OpenAI run are different runs |
| `context_tokens`, `max_output_tokens`, `temperature` | they change `max_tokens`, which is a request-digest input |
| canonical rendering of `ExtractRequest` (`lanes`, `limit`, `document_ids`) | F1 — this is the smoke-run hazard |
| sha256 of each `run.answer_stores` file, or `ABSENT` | in `replay` mode these determine the answers |

**Deliberately excluded, each with its reason:**

- **The answer cache's contents.** F3: including it makes resume non-idempotent. A cache may not
  change the result, therefore it may not name the result.
- **`code_commit`.** It would move the id on a docs-only commit, destroy comparability between two
  runs of the same inputs, and be unreproducible from a dirty tree. It stays in the manifest, where
  it already is (`pipeline.py:145`). The gap it leaves — a code change that alters output without a
  version bump — is closed by pinned-fixture tests (§6) that fail when the rendered prompt or the
  built schema changes, forcing the bump.
- **Concurrency.** Excluded *because* a test asserts it cannot change the bytes (§6, Stage 6). If
  that test ever fails, the exclusion is what makes the failure meaningful.

**(b) Overwrite refusal — the flag is the only authority.** `finalize()` grows `force: bool = False`
and refuses when the destination exists and holds `run.complete`. `--force` on
`python -m extraction run` is the only thing that authorises replacement; a config key may be read
and reported but may never authorise. This is `decide_replacement`'s rule restated
(`graph/stages/load/lifecycle.py:465-485`: *"config/graph.yaml cannot, and absence never does"*),
and `graph/cli.py:192-195` is the wording to copy.

Belt and braces is correct here: (a) means two materially different runs rarely collide at all;
(b) means that when they do, the answer is a refusal rather than an `rmtree`.

### 1.6 Throughput and concurrency

**The measured picture (F5): concurrency is a server change first and a client change second.** The
running llama.cpp has one slot; four concurrent requests returned 1.21× the serial throughput while
per-request latency tripled toward the un-retried timeout. So the plan must deliver both halves, and
must not claim a speedup the server cannot give.

Client side, in `extraction/stages/extract/lane_execution.py` only:

- **Per-candidate result buffers.** `_one` currently computes its counts by subtracting *shared*
  list lengths (`lane_execution.py:118`, `:130-132`). Under a pool this mis-attributes claims to the
  wrong candidate and can suppress or fabricate a `SILENT_NO_FINDING` issue (`:133-140`). Each
  candidate gets its own `ExtractResult`; the parent merges in index order.
- **Index-slotted collection**, copied from `acquisition/stages/download/local_download.py:74-91`:
  preallocate `[None] * n`, workers write only their own index, `ThreadPoolExecutor` +
  `as_completed`, and fire progress from the **collecting** thread so the callback needs no lock.
  Merging in submission order — never `as_completed` order — is what preserves the file bytes (F2).
- **Lanes stay sequential** in the fixed `(tables, narrative, events)` order (`lane_execution.py:90`);
  the pool is inside one lane's candidate loop. The `passage_id` sort and the `limit` prefix stay
  before dispatch (`:97-99`), so the pool cannot change *which* candidates run.
- **A rate limiter restated in `extraction/core/`.** `acquisition/core/sec_client.py:61-79` is the
  right 18 lines — lock-guarded, monotonic, injected clock, sleeps outside the lock — but it
  **cannot be imported**: `sec_client` imports `httpx` at module top, and
  `tests/extraction/test_provider.py:401-432` forbids any module under `extraction/stages/` from
  reaching `httpx` under any name. A copy in `extraction/core/rate_limiter.py`, naming the original,
  is the compliant move.
- **Cache writes are lock-guarded.** `AnswerStore.put` is an unguarded dict assignment
  (`answer_store.py:214-215`) and `render()`/`answers()` iterate `sorted(self._answers)`
  (`:201`, `:217-223`) — a concurrent insert during either raises. Both model-backed lanes share one
  provider and one store (`context.py:130-141`).

Server side, stated as an experiment rather than a setting (§7 step 0): raising `-np` divides `-c`
across slots in llama.cpp, so `-np 4` at today's `-c 16384` leaves 4,096 tokens per slot — below the
8,192 the prompt budget assumes (`narrative_lane.py:236-249`). `-np` and `-c` must rise together,
which is a KV-cache memory question on a 16 GB card with ~10 GB free. **Measure it; do not assume
it.** And raise `timeout_seconds` with the pool size, because a queued request's latency rises
toward the one error that is never retried.

For a hosted provider the constraint is the opposite one — rate limits and cost, not slots — so
`requests_per_second` carries the bound there and `429` is already retried with backoff
(`local_openai_compatible.py:49`, `:212-217`); the hosted adapter must additionally honour
`Retry-After`.

### 1.7 The validation boundary does not move

Model output continues to flow, unchanged, through `response_mapping.map_answer` /
`event_mapping.map_answer` → `core/assembly` → `core/validation` → the ontology's `validate_claims`
→ the five run checks (`extraction/stages/verify/public.py:26-29`). **No gate is relaxed to raise
graph population.** The expected effect of a successful live run is that substantive refusals and
rejections *rise*, because 10,852 questions that were never asked start getting answers that the
gates then judge.

Three boundary points that keep it intact:

- The new `PROVIDER_CALL_FAILED` issue is emitted by the **lane**, in the same shape as the existing
  `MODEL_ANSWER_UNUSABLE`, and never reaches mapping or validation.
- The run-health gate (§1.4 item 5) is **not** a sixth entry in `CHECKS`. `verify()` takes
  `(catalogs, ontology, passages)` (`run_verification.py:45`) and is about claim content; run health
  is about the run. It belongs in `pipeline.run`, deciding `finalize` vs `finalize_rejected`.
- `PROMPT_EXCEEDS_CONTEXT` keeps firing **before** `request_identity` and before any provider call
  (`narrative_lane.py:236-249`, `event_lane.py:155-166`), so a context refusal is never confusable
  with a cache miss or a live call. Its count staying at 0 is a monitored invariant, not an
  assumption — see §10.

---

## 2. Current → target delta

| # | Area | Current *(verified 2026-08-23)* | Target |
| --- | --- | --- | --- |
| D1 | Provider wiring | `context.py:132-133` hard-codes `inner=None`; `cmd_run` never passes `provider=` and there is no flag (`cli.py:271-277`) | `build_generation_provider(config, mode)` in `context.py`; `--mode`, `--model`, `--concurrency`, `--force` on `run` |
| D2 | `provider.kind` | read nowhere; `validated()` does not even check it | `SUPPORTED_KINDS`, refused at load, dispatched in exactly one place |
| D3 | Hosted OpenAI | impossible: no `api_key` field, no `Authorization` header anywhere in `extraction/`, `chat_template_kwargs` sent unconditionally (`local_openai_compatible.py:171-173`), `/health` is llama.cpp-only | sibling adapter `extraction/providers/hosted_openai.py` (Responses API), key from env only, redacted errors, health via `GET /models` |
| D4 | `max_output_tokens` | configured `1024` never reaches a lane; effective ceiling is the module constant `4096`; **two constants share the name with different values** | passed from config into both lanes; one meaning |
| D5 | `schema_name` | hard-coded `"extraction_claim"` for both lanes | the lane's own name reaches the wire |
| D6 | Transport faults | `ProviderTimeout`/`Unavailable`/`TransportError` caught nowhere; abort the run before anything is staged (F6) | caught per candidate → `PROVIDER_CALL_FAILED`; circuit breaker on K consecutive |
| D7 | Answer persistence | `put()` in memory only; the run's store is pathless (`context.py:83`); `write()` never called from `extraction/` | new `run.answer_cache`; append-only, flushed per answer; torn last line tolerated |
| D8 | Resume | none; `begin()` rmtrees any `.partial` and refuses to resume | resume **is** the cache; a rerun is a cache replay and is idempotent |
| D9 | Run identity | 4 inputs; bounds, provider identity, mode and prompt versions all invisible → `--limit 10` mints the full-corpus id (F1) | `+ input_identity_digest` (§1.5a); cache contents, code commit and concurrency deliberately excluded |
| D10 | Overwrite | `finalize()` rmtrees any existing run unconditionally | refuses a complete destination unless `--force`; unhealthy runs land in `<run_id>.rejected/` |
| D11 | Manifest honesty | `"mode": "replay_only"` (`pipeline.py:161`) and `provider_calls_permitted: 0` (`:95`) are literals | both derived; add `provider_calls_attempted` / `_succeeded` / `_failed` / `answers_replayed` / `answers_from_cache` |
| D12 | `NO_STORED_ANSWER` | the normal outcome for every model-backed candidate (10,852 of them) | reachable only when `mode: replay` |
| D13 | Concurrency | fully serial; no key anywhere; per-candidate counts computed from shared list lengths | bounded pool + rate limiter in `lane_execution.py` only; `max_concurrency`, `requests_per_second` |
| D14 | Graph run selection | `config/story.yaml:177` hand-edited — the **only** functional pin in shipped code or config | `--graph-run-id` on `story demo`/`ui`; flag → pin → sole finished projection, refusing ambiguity |
| D15 | Benchmark comparison | `write_reports(directory=)` and `build_report(store_path=)` exist but no verb exposes either; `REPORT_STEM` is a module constant | `--reports-dir` and `--answer-store` on the four build/report verbs |

---

## 3. Files and modules expected to change

**New (5 source, 4 test):**

| Path | Why |
| --- | --- |
| `extraction/providers/hosted_openai.py` | the OpenAI adapter; must be registered in `_LAZY` (`providers/__init__.py:52-56`), never eagerly re-exported |
| `extraction/providers/portable_schema.py` | assert the emitted schema stays inside the portable subset — today true by inspection only |
| `extraction/core/rate_limiter.py` | ~18 lines restated from `acquisition/core/sec_client.py:61-79`; cannot be imported (§1.6) |
| `tests/extraction/test_hosted_openai.py`, `test_run_identity.py`, `test_answer_cache.py`, `test_concurrent_extraction.py` | one per stage that adds a new guarantee |

**Modified (≈14):**

| File | Change |
| --- | --- |
| `extraction/core/run_directory.py` | `make_run_id` gains `input_identity_digest`; `finalize(force=…)` refusal; `finalize_rejected()` |
| `extraction/pipeline.py` | build the identity digest; derive the provider block; run-health gate choosing `finalize` vs `finalize_rejected` |
| `extraction/context.py` | `build_generation_provider`; open the answer cache; thread the mode |
| `extraction/cli.py` | `--mode`, `--model`, `--concurrency`, `--force` on `run`; a new `answers compact` verb; the replay-only banner at `:91` becomes truthful |
| `extraction/providers/public.py` | `SUPPORTED_KINDS`; `api_key` from env only; per-kind refusals; refuse secrets in YAML; concurrency fields; one `DEFAULT_MAX_OUTPUT_TOKENS` |
| `extraction/providers/__init__.py` | add the adapter to `_LAZY` |
| `extraction/providers/local_openai_compatible.py` | `schema_name` parameter reaches the lane's name; no other change |
| `extraction/stages/narrative/answer_store.py` | rename to `CachingGenerationProvider`; `append()`; tolerant load; lock `put` |
| `extraction/stages/narrative/{narrative,event}_lane.py` | catch the three transport faults → issue; accept `max_output_tokens`; pass `schema_name` |
| `extraction/stages/extract/lane_execution.py` | per-candidate buffers; index-slotted pool; rate limiter; completion-counter progress |
| `extraction/stages/extract/public.py` | `PROVIDER_CALL_FAILED`; correct the `NO_STORED_ANSWER` docstring |
| `extraction/core/config.py` | `run.answer_cache`, provider concurrency keys |
| `config/extraction.yaml` | `provider.mode`, `provider.openai:` block, `max_concurrency`, `requests_per_second`, `run.answer_cache`; fix the stale "four AST guards" comment (there are **eight**) |
| `benchmarks/extraction/v1/__main__.py` | `--answer-store` and `--reports-dir` on the four build/report verbs; dispatch on `kind` |
| `config/story.yaml`, `story/cli.py`, `story/pipeline.py` | optional pin + `--graph-run-id` |

**Deliberately unchanged:** `response_mapping.py`, `event_mapping.py`, `core/assembly.py`,
`core/validation.py`, `extraction/stages/verify/`, the ontology, and `graph/` in its entirety.

---

## 4. Stages, in implementation order

Each stage ends with a green `python -m pytest -o addopts="" -m "not live and not neo4j" -q`.
*(Note: `-q` on the command line plus `addopts = "-q"` in `pyproject.toml:27` gives `-qq`, which
prints no summary line — always pass `-o addopts=""`. Naming several test directories on one
command line works fine; the 2026-08-18 plan's "11 collection errors" warning did **not** reproduce
on 2026-08-23.)*

Baseline to hold green, re-measured 2026-08-23 at `ff3b08f`: **6,175 collected, 250 deselected**
under `-m "not live and not neo4j"` — acquisition 397, extraction 1,304, graph 654, normalization
203, ontology 165, story 3,452.

### Stage 1 — Make a rerun safe *(no provider work)*

Close F1 first. Until this lands, **do not run `python -m extraction run` at all**: any invocation,
including `--limit 10`, replaces `extract-v1-lexical-833f7bcfbce9`.

- `make_run_id` gains `input_identity_digest` (§1.5a), built in the shape of
  `graph/core/manifest.py:75-101` and reusing `run_directory.file_digest`.
- `finalize(force=False)` refuses when the destination exists and holds `run.complete`;
  `finalize_rejected()` stages to `<run_id>.rejected/` and writes no marker.
- `--force` on `run`, worded after `graph/cli.py:192-195`.
- The manifest records `input_identity_digest` and `answer_store_digests`.

### Stage 2 — Make the manifest tell the truth

- `provider.mode` and `bounds.provider_calls_permitted` derived from the context, not literals.
- Add `provider_calls_attempted`, `_succeeded`, `_failed`, `answers_replayed`, `answers_from_cache`.
- **`tests/extraction/test_integrated_run.py:777-778` is deliberately amended** — it currently
  asserts both literals. It becomes: in `replay` mode, `mode == "replay"` and attempted `== 0`.

Everything after this is measured through the manifest, which is why it comes before the provider.

### Stage 3 — Provider selection, the hosted adapter, and the three lies

- `build_generation_provider(config, mode)` in `context.py`, dispatching on `kind`.
- `hosted_openai.py` + the config surface in §1.1, with a redactor for every message built from a
  response body.
- D4 (`max_output_tokens` reaches the lanes), D5 (`schema_name`), and the `from_config`
  unknown-key refusal.
- `portable_schema.py` turns the measured portability into a test.
- **No new dependency.** `httpx` is already one of four (`pyproject.toml`); the OpenAI SDK would buy
  nothing a sibling adapter does not, and the repo's rule is standard-library-first.

### Stage 4 — Modes, the answer cache, and persistence

- Two knobs (§1.2); `NO_STORED_ANSWER` restricted to `replay`.
- `run.answer_cache`; `AnswerStore.append`; tolerant loader; `extraction answers compact`.
- Rename `ReplayingGenerationProvider` → `CachingGenerationProvider`.

At the end of Stage 4 a live run is possible, safe, resumable — and still serial, which is fine,
because the benchmark that comes next is small.

### Stage 5 — Transport-fault policy and the run-health gate

- The three transport faults become `PROVIDER_CALL_FAILED` per candidate.
- Circuit breaker after K consecutive faults.
- `pipeline.run` chooses `finalize` or `finalize_rejected` on the failure count.

→ **Decision point. Run the model comparison (§7) here, before Stage 6.** It needs no concurrency,
it decides the model, and it is the only cheap place to measure R5 (entity duplication) on real
output.

### Stage 6 — Bounded concurrency

- Per-candidate buffers; index-slotted pool; rate limiter; completion-counter progress (§1.6).
- Config `max_concurrency` / `requests_per_second`, CLI `--concurrency`.
- Acceptance is the byte-identity test, not a wall-clock number.

Stage 6 is the only stage with a genuine correctness risk, and it is worth doing *after* a model is
chosen, because against a one-slot local server it currently buys 1.21× (F5).

### Stage 7 — Downstream selection

- `demo.graph_run_id` becomes optional; `--graph-run-id` on `story demo` and `story ui`; resolution
  order **flag → config pin → the sole finished projection**, refusing with the candidate list when
  there is more than one. The precedent to copy is `extraction/cli.py:61-74` (*"The named run, or
  the only finished one, or an error naming what is available"*), which is better than
  `graph inspect`'s inline version (`graph/cli.py:114-124`) because it distinguishes "none" from
  "many".
- The pin stays authoritative when set, so `config_hash` still moves `story_run_id` deliberately.

### Stage 8 — Benchmark comparison harness

- Expose the two parameters that already exist: `write_reports(directory=…)`
  (`narrative_runner.py:1929`, `event_runner.py:1286`, `runner.py:990`) as `--reports-dir`, and
  `build_report(store_path=…)` (`narrative_runner.py:944-950`, `event_runner.py:540-546`) as
  `--answer-store`. **`--answer-store` is required, not optional**, on the build verbs: one store
  file holds one model on the benchmark path (§1.3).
- Dispatch on `kind` in `_build_narrative_answers` / `_build_event_answers`, which today name
  `LocalOpenAICompatibleGenerationProvider` directly (`__main__.py:428`, `:604`).
- **Two blockers this stage must clear, both discovered 2026-08-23:**
  1. `_answer_store_identity` does `Path(path).relative_to(PACKAGE_ROOT)`
     (`narrative_runner.py:687`, `event_runner.py:480`), which raises `ValueError` on any store
     outside `benchmarks/extraction/v1/` — **after** generation has been paid for. Either keep
     per-model stores under that package or make the identity fall back to an absolute path.
  2. `UNUSABLE_ANSWERS` (`narrative_runner.py:205-220`) is declared data about **one model's**
     conformance failure on one request digest, and `:441-444` raises if a declared-unusable digest
     also has a real answer. A second model that answers that request correctly trips it. The
     declaration must become per-model.
- Also stale and worth fixing here: the module docstring says "Sixteen subcommands, eight of which
  write"; there are **18, of which 9 write** *(counted 2026-08-23)*.

---

## 5. Invariants that must remain true

**Architectural — each already has an executable test:**

1. No stage but the narrative one may reach a provider, and only through `providers.public` —
   `tests/extraction/test_provider.py:401`, `test_narrative_lane.py:1809`.
2. Importing `extraction.stages.narrative` must not put an HTTP client in `sys.modules` —
   `test_narrative_lane.py:1785` (a real subprocess check). **The hosted adapter must go in `_LAZY`.**
3. The provider package imports no lane, no ontology, no `acquisition`, nothing named `stages` —
   `test_provider.py:435`. This is also why the rate limiter is copied rather than imported.
4. Nothing under `extraction/` may import **or name** the benchmark answers directory — **eight**
   AST guards, not the four `config/extraction.yaml:29` claims: `test_lexical_scoping.py:291`,
   `test_hybrid_scoping.py:510`, `test_narrative_lane.py:1597`, `test_narrative_lane_report.py:518`,
   `test_table_lane_report.py:308`, `test_typed_selection.py:266`, `test_integrated_run.py:710`, and
   `test_event_lane_report.py:1065` (which forbids the benchmark's case/passage/document id
   **values** from appearing in any `extraction/` source). **A corpus-scale cache path therefore
   stays in configuration**, which is what `run.answer_cache` is.
5. `extraction/` must never import `story/` — `tests/story/test_story_package_structure.py:347`.
   Story provider code is a model to restate, never a module to import.
6. `extraction/core/` never imports a stage; no catch-all module names.

**Behavioural:**

7. Catalogs rebuild byte-identically from the same `lane_outputs.jsonl`
   (`test_integrated_run.py:230`) and `report.md` regenerates byte-identically (`:239`).
8. Model output flows through `response_mapping`/`event_mapping` → `assembly` → `validation` →
   ontology `validate_claims`, unweakened. A live run should *raise* substantive refusals and
   rejections, never lower a gate to raise graph population.
9. **A partial run can never present as complete.** Stage into `<run_id>.partial/`, marker last,
   atomic rename; an unhealthy run lands in `<run_id>.rejected/` with no marker. **No resume state
   lives inside a run directory** — resume is a property of the answer cache, which lives under
   `data/`.
10. `PROMPT_EXCEEDS_CONTEXT` fires before `request_identity` and before any provider call, so a
    context refusal is never confused with a cache miss or a live call.
11. **Every issue is attributed to its own candidate's `passage_id`.** This is what keeps
    `_ordinal_id`'s occurrence counters candidate-local and `issue_id`/`rejection_id` invariant under
    reordering (F2). It is true today and **no test asserts it**; the table lane already reads
    `preceding_passage_id` (`lane_execution.py:158-166`) and is one line from breaking it silently.
    Stage 6 must add that test.
12. A cache may not change the result, therefore a cache is never a run-id input (F3).
13. Only an operator-typed flag may destroy data. A config key may be read and reported and may
    never authorise (`graph/stages/load/lifecycle.py:465-485`).

---

## 6. Tests required at each stage

| Stage | New | Amended |
| --- | --- | --- |
| 1 | `test_run_identity.py`: `--limit 10` yields a **different** id from a full run; a complete destination is refused without `--force`; `--force` replaces; a changed read-store digest moves the id; `finalize_rejected` writes no marker and `list_runs` skips it | any test asserting a derived run id |
| 2 | manifest carries derived mode and the five new counters | **`test_integrated_run.py:777-778`** — the two literals; the docstring must record why the assertion changed |
| 3 | `test_hosted_openai.py`: body carries `Authorization` and **no** `chat_template_kwargs`; a key in YAML is refused; the key never appears in a `repr` or in an error built from a response body; health hits `/models`; `kind` dispatch selects the right adapter; the schema round-trips the portable check. **Driven through the protocol, never `isinstance`.** Plus: the configured `max_output_tokens` reaches both lanes; `schema_name` differs per lane | `test_provider.py::test_the_shipped_config_matches_the_validated_runtime` (it pins every `provider:` key) |
| 4 | `test_answer_cache.py`: an appended answer is readable by a fresh `AnswerStore`; a **torn last line** loads as the rows before it; a killed run re-hits every persisted answer and produces the identical run id; `compact` is idempotent and byte-stable; `NO_STORED_ANSWER` is unreachable when `mode: live` | `test_integrated_run.py` replay assertions; the hard-coded `model_id="Qwen3.5-9B-Q4_K_M.gguf"` at `test_integrated_run.py:755` |
| 5 | each of the three transport faults becomes one `PROVIDER_CALL_FAILED` issue and the run continues; the breaker trips after K consecutive; a run over the failure tolerance lands in `<run_id>.rejected/` and `RunDirectory` refuses to open it | — |
| 6 | **the decisive test:** a concurrent run and a serial run over the same cache produce byte-identical `lane_outputs.jsonl` and identical digests for all seven catalogs — explicitly **not** compared via `report.md`, which is order-insensitive enough to hide the divergence (F2); per-candidate counts correct under contention; `put`/`append` under contention; invariant 11 asserted directly | progress-callback tests, if any assert an index rather than a completion count |
| 7 | resolution order flag → pin → sole projection; ambiguity refused **with the candidate list**; zero projections refused differently from many | `tests/story/` pinned-id tests |
| 8 | two models write two report sets and two stores without collision; a store outside the benchmark package does not raise late; per-model `UNUSABLE_ANSWERS` | `test_narrative_lane_report.py:105,119,133-142`, `test_event_lane_report.py:136,143` — these pin the committed reports byte-for-byte to the committed store and must be **parametrized over model**, not merely repathed |

**Live tests** (`-m live`) already exist as the template — `tests/extraction/test_{provider,
narrative_lane,event_lane,embeddings}_live.py`, all of which read `config/extraction.yaml` and
**skip** when the server is unhealthy rather than failing. Add hosted equivalents gated the same
way, skipping when no key is present, so the offline suite never needs a GPU or a credential.

---

## 7. Benchmark and model-comparison procedure

Run this **before** committing to a full-corpus run. It needs Stages 1–5 and 8, not Stage 6.

### 7.0 Two prerequisites that are measurements, not settings

- **Confirm what the local server actually offers.** *(measured 2026-08-23: `total_slots` 1,
  `n_ctx` 16,384, 73.6 tok/s generation, 3,070 tok/s prompt ingestion, 4 concurrent = 1.21×.)* If
  concurrency is wanted later, restart with `-np N` **and** a proportionally larger `-c`, then
  re-measure — llama.cpp divides `-c` across slots, and a slot below 8,192 breaks the prompt budget.
- **Note that `config/extraction.yaml:55` says `context_tokens: 8192` while the server offers
  16,384.** That is a client-side guard, not a limit, and raising it is a *cost/headroom* decision.

### 7.1 The comparison itself

```bash
# 1. Baseline — reproduce the committed reports offline, no model, no key.
python -m benchmarks.extraction.v1 narrative-report --reports-dir reports/qwen-8192
python -m benchmarks.extraction.v1 event-report     --reports-dir reports/qwen-8192

# 2. Qwen at a larger context. Restart llama.cpp with -c 32768, set provider.context_tokens: 32768,
#    then RECORD into its own store (a shared store cannot hold two models — §1.3).
python -m benchmarks.extraction.v1 narrative-build \
    --answer-store benchmarks/extraction/v1/answers/qwen-32768/narrative_v1.jsonl \
    --reports-dir  reports/qwen-32768
python -m benchmarks.extraction.v1 event-build \
    --answer-store benchmarks/extraction/v1/answers/qwen-32768/event_v1.jsonl \
    --reports-dir  reports/qwen-32768

# 3. OpenAI. Set provider.kind: openai and the model; export OPENAI_API_KEY; then the same two.
python -m benchmarks.extraction.v1 narrative-build \
    --answer-store benchmarks/extraction/v1/answers/openai-<model>/narrative_v1.jsonl \
    --reports-dir  reports/openai-<model>
python -m benchmarks.extraction.v1 event-build \
    --answer-store benchmarks/extraction/v1/answers/openai-<model>/event_v1.jsonl \
    --reports-dir  reports/openai-<model>
```

### 7.2 What the gold screen can and cannot decide

Measured from the committed case corpus 2026-08-23: **26 reviewed cases**, of which the narrative
runner scores **14** (`lane in {narrative, either}`), the event runner **3**, and the table runner
11 with no provider. **So a model comparison on the gold cases is decided on 17 cases.** That is a
screen, not a proof — treat a difference of one case as noise.

The dimensions are already emitted and need no new field: **12 narrative** (`metric_identity`,
`value`, `unit`, `scale`, `period`, `subject`, `evidence`, `population`, `ambiguity_codes`,
`abstention_honoured`, `ambiguity_preserved`, `abstention_code_agreement` —
`narrative_evaluation.py:106-125`) and **16 event/relationship** (`event_type_identity`,
`occurrence_date`, `announcement_date`, three participant dimensions, `properties`,
`event_evidence`, five relationship dimensions, `abstention_honoured`, `abstention_code_agreement` —
`event_evaluation.py:63-95`). Each report already carries a `model` block with `identity`,
`context_tokens`, `max_output_tokens` and the `output_budget` formula, plus an
`answer_store{path,answers,bytes,sha256}` block.

### 7.3 The bounded live sample — the half the gold screen cannot give

Seventeen cases cannot answer the questions that actually decide a 100-hour run. After Stage 5,
run the *same bounded corpus slice* under each candidate model:

```bash
python -m extraction run --mode live --model <model> --documents <the same 3 document ids> \
    --lanes narrative,events
```

Because Stage 1 puts `--documents` and the model into the run id, each lands in its own directory
and none of them can touch the full-corpus run. Compare, per model, from the manifests and catalogs:

| Measure | Where it comes from | Why it decides |
| --- | --- | --- |
| claims per candidate | `counts` | raw yield |
| substantive refusal mix | `issues.jsonl` by code | a model that refuses well is better than one that guesses |
| rejection mix | `rejected_claims.jsonl` | `QUOTED_SPAN_NOT_IN_PASSAGE` etc. measure grounding |
| `MODEL_ANSWER_UNUSABLE` count | `issues.jsonl` | schema conformance on the wire |
| **distinct entity ids minted per named entity** | `events.jsonl` / `relationships.jsonl` | **R5** — the risk that scales worst |
| tokens in/out, wall clock, cost | new manifest counters (Stage 2) | the 100-hour question |
| `PROMPT_EXCEEDS_CONTEXT` count | `issues.jsonl` | must stay 0; see below |

### 7.4 Two cautions carried forward, both verified

- **Context size was not the previous failure and must not be reported as if it were.** Zero
  `PROMPT_EXCEEDS_CONTEXT` across 17,130 issues covering all 10,807 formed requests, and the guard
  runs *before* the call. Context is a cost and headroom variable here. Keep the count in every
  comparison table precisely so the distinction stays visible.
- **Raising `context_tokens` invalidates part of the recorded corpus.** `max_tokens` is a
  `request_identity` input and `budget = min(ceiling, context_tokens − estimated)`, so every
  context-bound request re-keys. **10 of the 15 committed answers carry `max_tokens < 4096`**
  *(measured 2026-08-23: 1793, 1870, 2125, 3134, 3158, 3364, 3490, 3581, 3687, 3861)* and would stop
  matching. Step 2 above therefore re-records; budget GPU time for it.

---

## 8. Safe full-corpus rerun procedure

Only after §7 has chosen a model. Every step below is safe *because* Stage 1 landed; before Stage 1,
step 1 alone would destroy the current run.

```bash
# 0. Belt and braces, even though the ids will now differ.
cp -r data/extraction_runs/extract-v1-lexical-833f7bcfbce9 data/superseded_runs/

# 1. Smoke: ten candidates. Its own run id, cannot touch the full run.
python -m extraction run --mode live --limit 10 --lanes narrative,events

# 2. One document end to end.
python -m extraction run --mode live --documents <document_id>

# 3. Full corpus. Resumable: every answered request is already durable.
python -m extraction run --mode live --concurrency <server slots>

# 4. If it dies, repeat step 3 verbatim. Every persisted answer is a cache hit, the run id is
#    unchanged, and the result is byte-identical to an uninterrupted run.

# 5. Optional: compact the cache once the run has settled.
python -m extraction answers compact data/answer_cache/<model-slug>/answers.jsonl
```

**Expected cost, recomputed with today's measurements (F5):** ~27–56 s per request serial;
10,807 requests ≈ **81–168 hours** single-threaded against the one-slot local server. Concurrency
helps only after the server is restarted with more slots. A hosted model removes the slot
constraint and replaces it with a rate limit and a bill — which is precisely what §7.3 measures
before the commitment is made.

**Sequencing note.** Because the answer cache is deliberately *not* a run-id input (F3), the run id
does not drift while answers accumulate, and steps 3 and 4 land in the same directory. That is the
property that makes "just run it again" the whole resume story.

---

## 9. Downstream rebuild, load, verify

Verified against the current CLIs 2026-08-23. ⚠ marks a manual step; Stage 7 removes the ⚠ at step 5.
Note that `graph load` and `graph verify` take **neither** `--root` nor `--runs-root` — they read
`config/graph.yaml` plus `.env`, and need the Neo4j container up.

```bash
python -m extraction runs                          # ⚠ read the new extraction run id
python -m graph project <extraction_run_id>        # prints the derived graph run id
python -m graph runs                               # ⚠ read it back
python -m graph load <graph_run_id> --replace      # ⚠ --replace is the ONLY wipe authority
python -m graph verify <graph_run_id>              # 27 checks; len(self.checks), not a literal
python -m story ui --graph-run-id <graph_run_id>   # Stage 7. Today: hand-edit config/story.yaml:177
```

**Five facts that shape this, all verified:**

- **A new extraction run yields a new graph run id iff one of four inputs moves** —
  `make_graph_run_id` digests `(GRAPH_PROJECTION_VERSION, extraction_run_id,
  ontology_definition_hash, input_content_digest)` (`graph/core/manifest.py:104-139`), where
  `input_content_digest` hashes `run.complete`, all seven catalogs and their row counts
  (`:75-101`). It is a **required** parameter, and the module docstring records the data-loss
  incident that made it required. The graph layer already defends itself against extraction's
  identity defect; Stage 1 stops extraction relying on that.
- **`--replace` is mandatory and destroys the previous load.** Neo4j Community hosts one database;
  `decide_replacement` refuses when the database holds a different run id or any unidentified row
  (`graph/stages/load/lifecycle.py:493-525`), and `config/graph.yaml`'s `load.replace_policy` is
  read, reported in the refusal text, and **can never wipe**.
- **Graph runs coexist on disk, never in Neo4j.** Old exports stay under `data/graph_runs/`;
  restoring one is `graph load <old-id> --replace`.
- **The story pin is the only functional pin in the repository.** `config/story.yaml:177`, read at
  `story/pipeline.py:321`. Of 148 references to `graph-v1-0483dc6b4b10` across 67 files, **exactly
  one** is functional; `story/` contains 20 and every one is prose. What *will* break are 34
  hard-coded test constants and 16 committed fixture values — notably
  `tests/graph/test_graph_verification.py:928` and `tests/graph/test_loader.py:74`, which read
  `data/graph_runs/graph-v1-0483dc6b4b10` off disk directly. **Keep the old export directory** until
  those are repointed.
- **The freshness gate detects a swapped graph run, but not before the CLI does.**
  `resolve_demo_inputs` calls `read_graph_identity` on the line *before* `check_freshness`
  (`story/pipeline.py:449-452`), so a pin naming a directory that no longer exists raises
  `GraphIdentityError` and the 14-check report is never printed. If the old directory is still
  there, checks 8–11 fail with `GRAPH_RUN_ID_MISMATCH`. Either way it refuses; the message differs.

**The cost nobody should be surprised by:** re-projecting invalidates the entire committed story
generation store. `graph_run_id` is not a `request_identity` input directly, but it digests into
`package_id` (`story/core/keys.py:145-189`), `package_id` is rendered into the prompt
(`story/stages/generation/prompts.py:365`), and the prompt is digested
(`story/providers/generation_store.py:259-353`). So **every recorded row becomes a guaranteed
miss** and `python -m story demo` needs `--live` and a re-record after a rebuild. Since S12 the key
is `demo.generation_stores`, plural and keyed per provider (`config/story.yaml:220`), with the
pre-S12 scalar surviving at `:231` as the local adapter's fallback only — so a rebuild has two keys
to re-record against, not one.

---

## 10. Risks and non-goals

### Risks

| # | Risk | Mitigation in this plan |
| --- | --- | --- |
| R1 | **A run today destroys the graph's source of truth** (F1) — `--limit 10` mints the full-corpus id and `finalize` rmtrees | Stage 1 is first. **Until it lands, do not run `python -m extraction run` for any reason.** |
| R2 | A transport fault kills a 40-hour run and loses every answer with it (F6) | Stage 4 durability + Stage 5 fault policy: the work survives, the rerun resumes |
| R3 | Concurrency changes catalog bytes silently — and `report.md` would still match (F2) | Stage 6's acceptance test compares `lane_outputs.jsonl` and all seven catalog digests, never the report; plus invariant 11 gets its first test |
| R4 | Concurrency against the local server buys 1.21×, and queued latency climbs toward the one un-retried error (F5) | Bound the pool to the server's slots; raise `timeout_seconds` with the pool; §7.0 makes `-np`/`-c` a measurement |
| R5 | **Entity resolution is deliberately not attempted** (`event_mapping.py:33-38`) — ids are minted from printed names by `core/identifiers.entity_id`, and unresolved participants are keyed per event. Invisible at 14 answered candidates; at ~10.8k it fills the graph with spelling variants | **Not solved here, and not hidden.** §7.3 measures it on a bounded live run *before* the full-corpus commitment. If it is bad, that is a separate piece of work and this plan does not pretend otherwise |
| R6 | 81–168 hours and/or a bill | §7 decides the model first; §8 is resumable so the clock can be spent in pieces |
| R7 | An OpenAI snapshot roll changes the served model while `model_id` — the digest key — stays put | Pin a dated snapshot; record `provider_model_id` (already a stored row field, `answer_store.py:41-48`) and assert it in a live test |
| R8 | The hosted 401 body echoes the first eight characters of the key (measured by the story layer 2026-08-19) | Every message built from a response body goes through a redactor; the local adapter's `response.text[:200]` must not be copied |
| R9 | Stage 2 changes a manifest field that readers trust | Called out explicitly; the amended test documents the new meaning |
| R10 | Benchmark fixtures pin the committed reports byte-for-byte to the committed store, and `UNUSABLE_ANSWERS` is one model's declared conformance failure | Stage 8 parametrizes them over model rather than repathing them |
| R11 | The working tree carries 12 modified documents and another session has been active here | Branch before starting; re-baseline the suite on the branch. A failure count taken while another session writes measures that session |

### Non-goals

- **Not the Research Agent, post generation, cached posts, or precomputed story questions.** The
  graph is the precomputed artifact; posts are generated live and later, by a separate agent using
  graph and source tools. Nothing in this plan pre-generates a question, and the answer cache is a
  cache of *extraction requests*, never of future post questions.
- **Not a migration for the 10,852 `:NotAttempted` nodes.** The graph is a whole-run projection with
  no incremental update, so a successful run replaces them by rebuild: each answered candidate
  becomes a claim, a substantive refusal, or a substantive validation rejection. The count should
  collapse toward zero and the refusal and rejection counts should rise. The only change needed is
  D12 — `NO_STORED_ANSWER` must stop being the normal outcome of a live run, so that the label goes
  back to meaning "replay mode, and this question has no recorded answer".
- **Not a distributed job system.** Resume is the answer cache; the pool is a `ThreadPoolExecutor`
  bounded by the server's slots.
- **Not entity resolution** (R5), **not scoping recall** (the 479 pre-request `UNRESOLVED_METRIC`
  refusals, which are independent of the provider and would survive a full live run), **not any new
  event type** (`offered_vocabulary` already offers all 19 declared types; the graph holds 3 purely
  because 3 event prompts were answered).
- **Not a change to any validation gate.** Not one.
- **Not a new dependency.** `httpx` already ships.
- **Not a rewrite of extraction.** Every stage above extends something that exists.

---

## Recommendation

Implement in three groups, stopping for a decision after each. Nine of the ten deliverables above
are already decided; the two open questions are named at the end.

**Group A — safety (Stages 1–2), ~2 commits.** Small, self-contained, no provider work, and it
closes a hazard that exists *today*: any `extraction run` invocation currently deletes
`extract-v1-lexical-833f7bcfbce9`, the directory `graph-v1-0483dc6b4b10` was projected from. Nothing
else should be attempted first, because every later stage runs extraction. Group A is also the only
group whose value does not depend on which model wins.

**Group B — capability (Stages 3–5), ~4 commits.** Provider selection, the hosted adapter, the two
knobs, the answer cache, and the transport-fault policy. At the end of Group B a live corpus run is
possible, safe and resumable — and still serial, which is fine, because the thing that comes next is
small.

→ **Decision point: run the model comparison (§7) here.** It is cheap, it needs no concurrency, and
it decides three things at once: which model, whether the Qwen context bump earns its re-recording
cost, and how bad R5 is on real output. Do not skip §7.3 in favour of the 17-case gold screen alone;
17 cases cannot answer a 100-hour question.

**Group C — scale and handoff (Stages 6–8), ~3 commits.** Concurrency, downstream selection,
benchmark report paths. Worth doing only once a model is chosen — and against today's one-slot
server, Stage 6 buys 1.21× until `llama-server` is restarted with more slots, so the server change
and the client change must be planned as one piece of work.

**Two things worth settling before Group A starts:**

1. **Where the corpus-scale answer cache lives.** Recommended: `data/answer_cache/<model-slug>/
   answers.jsonl`, gitignored, path stated in `config/extraction.yaml` — the eight AST guards mean
   it must stay in configuration either way, and `benchmarks/…/answers/` is the benchmark's
   committed 15-row corpus and should not become a 48 MB scratch file.
2. **Whether the OpenAI adapter targets Responses or Chat Completions.** Recommended: Responses,
   because the story layer has already measured that endpoint's four surprises against the real
   API. If the benchmark's winner turns out to be a `gpt-4.1`-class model, Chat Completions becomes
   the smaller diff and the decision is worth reopening — the trade is close, and it is the one
   design choice here that a measurement should settle rather than a preference.
