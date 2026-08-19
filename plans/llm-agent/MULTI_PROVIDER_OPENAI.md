# S12 — A second provider: OpenAI beside the local Qwen server

**Status:** plan, committed as a checkpoint before implementation. *(2026-08-19)*

## 1. The answer, first

Add **one** provider adapter beside the existing one, behind the `StoryGenerationProvider`
protocol that is already there. No second pipeline, no second planner, no second writer, no
agent/tool loop. The OpenAI adapter replaces the model and nothing else.

Three things change beyond the adapter, and each is a defect this plan found rather than a
feature it invents:

| # | Finding *(verified 2026-08-19, by reading the code)* | Change |
| --- | --- | --- |
| F1 | `request_identity` digests `system, prompt, schema, schema_name, model_id, temperature, max_tokens` — **no provider**. `generation_store.py:88-140`. Two providers configured with one model string share a replay row. | `provider_id` becomes an eighth digest input; `IDENTITY_VERSION` → `story-generation-v2`. |
| F2 | `story_run_id` digests `model_id` and `provider_model_id` but **no provider** (`core/keys.py:213-300`), and `StoryRunManifest` has no provider field at all (`core/manifest.py:74-116`). | `provider_id` becomes a `story_run_id` input and a manifest field, and joins `story_run_id_inputs`. |
| F3 | Nothing records how the request was parameterised beyond `temperature`/`max_tokens` — and OpenAI's reasoning models **refuse** `temperature` (measured, §3), so the manifest's `temperature: 0.0` would be a claim about a value never sent. | Manifest gains a `provider_settings` block stating what was actually sent. |

Everything else the brief asks for — one evidence package, one planner, one writer, one schema
pair, one deterministic verifier, one run lifecycle — is already true and is preserved by
touching none of it.

## 2. What was verified before designing *(2026-08-19)*

Read from the tree at `6a84426`, not from memory:

| Fact | Where | Consequence |
| --- | --- | --- |
| `StoryProviderConfig.kind` exists and is **dispatched on**; `SUPPORTED_KINDS` holds one value | `providers/public.py:76-78, 208-215` | The provider id already exists as a concept. Reuse it; do not mint a parallel name. |
| The portable schema subset requires `additionalProperties: false` and every property `required` | `providers/portable_schema.py:41-46, 99-128` | Identical to OpenAI strict Structured Outputs' own requirement (§3). **The same schema object goes to both providers unmodified.** |
| `api_key` is environment-only, `SecretStr`, refused from any tracked config file | `providers/public.py:189-193` | The rule for `OPENAI_API_KEY` is already written; extend it, do not restate it. |
| `PINNED_TEMPERATURE = 0.0` is a constant, not a config field | `providers/public.py:69` | Stays. A provider that cannot accept it records that it did not send it. |
| The demo UI builds the provider on the **request** thread, before the worker starts | `demo_ui/api.py:1263-1297, 1892` | Freezing provider+model at *Generate post* needs no new machinery — it needs the selection resolved in `_provider_for` and nowhere else. |
| The UI states "No credential, provider setting or environment value is shown anywhere in this interface" | `static/index.html:104-107`, asserted by `test_demo_ui_app.py:183`, `test_demo_ui_static.py:460` | A provider/model control makes half that sentence false. The copy is **corrected**, not deleted: no credential, no base URL, no environment value — a provider label and a model id, which are already in every manifest the panel renders. |
| Replay fixture is Qwen-only, 2 rows, committed | `tests/story/fixtures/story_demo/generations.jsonl` | Fixtures become provider-specific by directory, and an OpenAI fixture is recorded from the live run in §8. |
| Baseline offline suite | `pytest tests/story -m "not live and not neo4j"` | **2996 passed, 145 deselected, 43s** *(measured 2026-08-19)*. This number must not fall. |
| `openai` is in the **forbidden import list** | `test_story_package_structure.py:46-58` | The SDK is not an option. See §9. |
| The local llama.cpp server is up | `GET 127.0.0.1:8080/health` → 200 *(2026-08-19)* | Qwen-unchanged can be proved live, not only by replay. |

## 3. The OpenAI wire shape, measured rather than assumed *(2026-08-19, live probes with the supplied key)*

`POST https://api.openai.com/v1/responses`, `Authorization: Bearer …`:

```json
{"model": "...", "input": [{"role":"system","content":"..."},{"role":"user","content":"..."}],
 "text": {"format": {"type":"json_schema","name":"<schema_name>","strict":true,"schema":{...}}},
 "max_output_tokens": 2048, "store": false}
```

| Measurement | Result |
| --- | --- |
| `text.format` is **flat** — `name`/`strict`/`schema` are siblings of `type` | confirmed; this is *not* the nested `json_schema:{…}` shape llama.cpp takes |
| A strict schema missing `additionalProperties:false` | **400** `invalid_json_schema`, `param: text.format.schema` — refused at request time, exactly the failure `validate_portable_schema` already prevents |
| `temperature: 0.0` on `gpt-4.1-nano` | accepted, `status: completed` |
| `temperature: 0.0` on `gpt-5-nano` | **400** `Unsupported parameter: 'temperature' is not supported with this model.` |
| `usage` | `{input_tokens, input_tokens_details:{cached_tokens,…}, output_tokens, output_tokens_details:{reasoning_tokens}, total_tokens}` — **not** `prompt_tokens/completion_tokens` |
| `output` | a list; a reasoning model emits `[{"type":"reasoning",…},{"type":"message",…}]`, a non-reasoning one `[{"type":"message",…}]`. Text is `output[i].content[0].text` where `type == "output_text"` |
| top level | `status` (`completed`/`incomplete`), `incomplete_details`, `model` (dated id, e.g. `gpt-4.1-nano-2025-04-14`), `id`, `created_at` |
| `store: false` | accepted — the default is to retain; we opt out, since a run's prompts carry evidence text |

**This repository's own two schemas were posted to the real API and accepted unmodified**
*(verified 2026-08-19)*: `writer_schema()` and both variants of `planner_schema(causal_language=…)`
(`forbidden`, `reported_only`) each returned `200 completed` under `strict: true` with no edit of
any kind. That is the strongest form of the §2 claim about the portable subset — not "the rules
coincide" but "these exact objects are accepted by both servers."

Two facts drive the design: **`usage` names differ**, so translation is the adapter's job; and
**`temperature` is model-dependent**, so it is a declared per-model capability rather than a guess.

The dated `model` string is the natural `provider_model_id` — the same role the `.gguf` path
plays locally, and it moves when OpenAI reroutes an alias, which is exactly what that field is
for.

## 4. Architecture

```
story/contracts.py            StoryGenerationProvider  ── unchanged except one added property
story/providers/public.py     config, error taxonomy, provider registry, availability
story/providers/openai_compatible.py   local llama.cpp     ── unchanged except `provider_id`
story/providers/openai_responses.py    NEW: OpenAI Responses API
story/providers/generation_store.py    replay, now keyed by provider too
```

### 4.1 The one contract addition

Every provider — including `ReplayingStoryGenerationProvider` and the demo UI's two decorators —
exposes:

```python
@property
def provider_id(self) -> str: ...    # "local_openai_compatible" | "openai"
@property
def model_id(self) -> str: ...       # already exists
```

`provider_id` is added to the `StoryGenerationProvider` protocol beside `model_id`'s implicit
use. The decorators (`ObservedProvider`, `EditedSystemProvider`) must forward it, the way they
already forward `store`.

### 4.2 Registry — the only new function in `public.py`

```python
PROVIDER_LOCAL  = KIND_LOCAL_OPENAI_COMPATIBLE   # "local_openai_compatible"
PROVIDER_OPENAI = "openai"
SUPPORTED_KINDS = frozenset({PROVIDER_LOCAL, PROVIDER_OPENAI})

ENV_OPENAI_API_KEY = "OPENAI_API_KEY"      # the brief's name, and the ecosystem's
ENV_OPENAI_MODEL   = "STORY_OPENAI_MODEL"  # optional override of the configured default

@dataclass(frozen=True)
class ProviderOption:          # what the UI is allowed to see. No URL, no key, no env value.
    provider_id: str
    label: str
    available: bool
    unavailable_reason: str    # "" when available; a sentence when not
    models: tuple[ModelOption, ...]
    default_model_id: str

@dataclass(frozen=True)
class ModelOption:
    model_id: str
    label: str
    supports_temperature: bool
    reasoning_effort: str | None   # None for a non-reasoning model

def provider_catalogue(config, *, env_path=DEFAULT_ENV_PATH, environ=None) -> tuple[ProviderOption, ...]
def load_provider_config(config=None, *, provider_id=None, model_id=None, env_path=…, environ=…) -> StoryProviderConfig
```

`load_provider_config` keeps its current signature and behaviour when `provider_id` is omitted:
it resolves the configured default, which is the local server. That is what keeps every existing
call site — CLI, API, tests — behaving exactly as it does today.

`StoryProviderConfig` gains three fields with local-safe defaults: `supports_temperature: bool =
True`, `reasoning_effort: str | None = None`, `store_responses: bool = False`. `validated()`
refuses `reasoning_effort` on the local kind rather than ignoring it — the `kind`-dispatch
discipline `public.py` already argues for.

### 4.3 Availability

OpenAI is **available** iff a non-empty `OPENAI_API_KEY` is resolvable from `.env` or the process
environment. Absent → `available=False`, `unavailable_reason="OPENAI_API_KEY is not set in the
environment or in the repository-root .env"`. Never a raise at catalogue time: an unconfigured
provider is an ordinary state for the UI to render, which is the argument `health()` already makes.

Constructing an OpenAI provider with no key **is** a raise — `StoryProviderConfigurationError`,
before any request is built.

### 4.4 Configuration — `config/story.yaml`

```yaml
provider:
  default: local_openai_compatible          # NEW; existing keys stay where they are
  kind: local_openai_compatible             # unchanged, still the local block
  base_url: http://127.0.0.1:8080
  model: Qwen3.5-9B-Q4_K_M.gguf
  ...
  openai:                                    # NEW block. No key, ever — `from_config` refuses one.
    base_url: https://api.openai.com/v1
    default_model: gpt-5-nano
    timeout_seconds: 180
    max_retries: 2
    max_output_tokens: 4096
    context_tokens: 128000
    models:
      - id: gpt-5-nano       # verified present 2026-08-19; refuses `temperature`
        supports_temperature: false
        reasoning_effort: minimal
      - id: gpt-4.1-mini     # verified present 2026-08-19; accepts `temperature: 0`
        supports_temperature: true
```

Every key here is a `config_hash` input and therefore a `story_run_id` input — including keys
this loader does not read. That is already true and is why the block is safe to add.

### 4.5 Error normalisation — no new classes

| OpenAI condition | Existing class |
| --- | --- |
| no API key, unknown provider id, model not in the configured list, non-portable schema | `StoryProviderConfigurationError` |
| connection refused / DNS | `StoryProviderUnavailable` |
| request exceeded its budget | `StoryProviderTimeout` (never retried, as locally) |
| 429 / 5xx surviving the retry bound | `StoryProviderTransportError` |
| 400/401/403/404, `status: incomplete`, refusal content, missing/blank text, non-JSON text | `StoryProviderResponseError` |
| valid JSON failing `schema_violations` | `StoryProviderSchemaError` |

Retry set and backoff are the local module's, unchanged: `RETRYABLE_STATUSES`, `0.25 × 2ⁿ`, and a
timeout is never retried.

## 5. Identity — and the cost of getting it right

### 5.1 `request_identity` gains `provider_id`; `IDENTITY_VERSION` → `story-generation-v2`

Required by the brief ("Qwen and OpenAI runs must never be mistaken for the same recorded
generation") and independently justified: the two providers send **structurally different
requests** for the same logical inputs, so one digest over both is a digest that means two things.

This re-keys every committed row. **The recorded answers are not re-generated** — the digest is a
pure function of inputs the demo path rebuilds deterministically, so the fixture is *re-keyed
offline*: run the demo with a shim that computes both the old and the new identity, look up under
the old, write under the new. `raw_content` and `content_sha256` stay byte-identical, and that
byte-identity is the test.

### 5.2 `story_run_id` gains `provider_id`, and the manifest gains three fields

`provider_id` joins the digest parts and `_run_id_input_names()`. `StoryRunManifest` gains:

* `provider_id` — which adapter ran;
* `planner_provider_model` / `writer_provider_model` — `{provider_id, model_id, provider_model_id,
  prompt_version, schema_name, max_tokens}` per call site, because the brief asks for planner and
  writer provenance separately and today one pair of scalars covers both;
* `provider_settings` — `{temperature, temperature_sent, reasoning_effort, max_output_tokens,
  store_responses}`, so a run against a model that refuses `temperature` says so rather than
  recording a number it never sent.

`token_totals` already exists and already carries what the provider returned; the OpenAI adapter
must map `input_tokens/output_tokens/total_tokens` onto `prompt_tokens/completion_tokens/
total_tokens` in `GenerationResult` and record `reasoning_tokens` and `cached_tokens` in
`metadata`, the way the local one records `cached_tokens` today.

No committed artifact holds a `story_run_id`, so this changes no fixture — checked: `data/` is
gitignored and the tests assert the *prefix* and the *properties*, not a literal id.

### 5.3 Provider-specific replay fixtures

`config/story.yaml` `demo.generation_store` becomes a mapping keyed by provider id:

```yaml
demo:
  generation_stores:
    local_openai_compatible: tests/story/fixtures/story_demo/local_openai_compatible/generations.jsonl
    openai: tests/story/fixtures/story_demo/openai/generations.jsonl
```

The scalar key stays readable for one release as the local default if that keeps the diff honest;
the mapping is authoritative. Selecting a provider with no recorded store on the replay path is
`ApiError("generation_store_empty")` naming the provider — never a silent fall-through to a
different provider's rows.

## 6. UI

New endpoint, matching the twelve that exist:

```
GET /demo/providers
{"providers": [{"provider_id","label","available","unavailable_reason",
                "models":[{"model_id","label","supports_temperature","reasoning_effort"}],
                "default_model_id"}],
 "default_provider_id": "local_openai_compatible",
 "honest_labels": {...}}
```

It carries **no** base URL, no key, no environment value, no timeout — the fields above and
nothing else. That is the rule `ComposedPrompts.as_dict` already follows.

Two `<select>` controls in `#prompt-preset-row` (the existing flex row), above `#generate-post`:
`#provider-select` and `#model-select`. Changing the provider repopulates the model list. An
unavailable provider renders as a `disabled` option whose label carries the reason, and the reason
is shown in `#provider-notice` — shown, not hidden, because "OpenAI is missing" and "OpenAI does
not exist" are different facts.

`POST /demo/generate` gains two fields, allowlisted beside `candidate_id`/`live`:
`provider_id`, `model_id`. Both are validated against the server-side catalogue — an unknown or
unavailable pair is `invalid_provider_selection` (400), and **no URL or key may ever arrive from
the browser**. The pair is resolved once, in `_provider_for`, on the request thread, and the frozen
`ProviderSelection` is echoed in the 202 body and written to the manifest. The worker never re-reads
the request body, so mid-run change is structurally impossible; a test asserts it by mutating the
catalogue after the 202 and checking the manifest.

Selecting a provider whose recorded store is absent on the replay path returns the existing
409 shape with code `provider_requires_live`, mirroring `edited_prompt_requires_live`.

The standing sentence at `index.html:104-107` is corrected to say what is now true: the interface
shows a provider label and a model id, and shows no credential, no endpoint and no environment
value. `test_demo_ui_app.py` and `test_demo_ui_static.py` move with it, in the same change.

## 7. Work packets

Files are **disjoint by packet** so three can run at once. The contracts in §4.1, §4.2 and §6 are
the interface between them; nobody invents a name not written above.

| Packet | Owns | Must not touch |
| --- | --- | --- |
| **A — provider** | `providers/public.py`, `providers/openai_responses.py` (new), `providers/openai_compatible.py` (add `provider_id`), `providers/__init__.py`, `contracts.py`, `config/story.yaml` provider block, `tests/story/test_story_provider_openai.py` (new), `tests/story/test_story_provider_openai_live.py` (new, `live`) | `pipeline.py`, `core/`, `demo_ui/`, existing fixtures |
| **B — provenance & replay** | `core/keys.py`, `core/manifest.py`, `pipeline.py`, `providers/generation_store.py`, `cli.py` provider composition, fixture re-key + move, `tests/story/test_story_demo.py`, `test_story_keys.py`, `test_story_provider.py` replay block | `providers/public.py`, `providers/openai_responses.py`, `demo_ui/`, `static/` |
| **C — UI** | `demo_ui/api.py`, `demo_ui/static/{index.html,app.js,style.css}`, `tests/story/test_demo_ui_api.py`, `test_demo_ui_app.py`, `test_demo_ui_static.py` | `providers/`, `core/`, `pipeline.py` |
| **D — integration & adversarial review** | reconciliation, the cross-packet tests of §8, and nothing new | — |

## 8. Tests — the claim each one proves

| Claim | Test |
| --- | --- |
| Qwen behaviour is unchanged | the 2996-test baseline stays green; the recorded demo replays to a **byte-identical** `plan/draft/verification/post`, with only `request_sha256` re-keyed |
| Qwen is unchanged *live* | `-m live` run against 127.0.0.1:8080 reproduces the committed `content_sha256` |
| OpenAI translates the planner and writer requests correctly | `httpx.MockTransport` asserts the exact body: flat `text.format`, `strict: true`, the *same* schema object the local provider is given, `max_output_tokens`, no `temperature` for a model declaring `supports_temperature: false` |
| Structured outputs validate against the same schemas | one schema pair, both adapters, `schema_violations` empty |
| Missing `OPENAI_API_KEY` is safe | catalogue reports unavailable with a reason; construction raises `StoryProviderConfigurationError`; **no request is issued** and the key never appears in any error string, artifact or response |
| Provider/model selection reaches the backend | `POST /demo/generate` with a pair → manifest records that pair |
| Selection cannot change mid-run | catalogue mutated after the 202; the finished run still records the frozen pair |
| Manifests record the real provider and model | `provider_id`, planner/writer blocks, `provider_settings`, `token_totals` from the provider's own numbers |
| Qwen and OpenAI never collide | identical `(system, prompt, schema, schema_name, model_id, temperature, max_tokens)` under two provider ids → two `request_sha256`, two `story_run_id`, two directories |
| The UI shows only available providers | catalogue with and without a key → option present/disabled with reason; no URL, no key, no env value anywhere in the payload or the DOM |
| Deterministic verification is provider-blind | the same draft + package + plan through `DeterministicVerifier` under both provider ids → identical findings, identical `passed` |

Mocked/recorded only. Nothing in the default suite spends money: the OpenAI live test is marked
`live` and skips when the key is absent.

## 9. Rejected options

| Option | Why not |
| --- | --- |
| The `openai` Python SDK | `tests/story/test_story_package_structure.py:46-58` forbids importing it, deliberately. It would also add a dependency to replace ~120 lines of `httpx` that mirror a module already in this package. |
| `/v1/chat/completions` for OpenAI too | It would make the two adapters look alike and the request digest lie about what was sent. The brief asks for Responses; the measured shape (§3) fits the existing abstraction with no compromise. |
| Leaving `request_identity` alone and relying on distinct model names | A local server answers to any model string. Collision would present as a silently wrong replay, which is the failure mode the store exists to prevent. |
| A second pipeline / an OpenAI-specific planner | Explicitly out of scope, and it would make the verifier's provider-blindness unprovable. |
| Recording the OpenAI fixture by hand | A fixture that never came from the API proves nothing about translation. It is captured from the §10 live run or it does not exist. |
| Sending `temperature` to every model and catching the 400 | A 400 per run to discover a static fact. Declared per model in config, verified against the API, and recorded in the manifest. |

## 10. The live run, once the packets land

One end-to-end OpenAI run over **the same candidate and the same evidence package** the Qwen
fixture was recorded from, then a table comparing: schema adherence, planner result, writer result,
deterministic-verifier findings, token usage, and accepted/rejected. Its generations are committed
as the provider-specific OpenAI fixture (§5.3).

**No verifier rule and no evidence contract may be changed to make OpenAI pass.** A rejection is a
result, and it is reported as one.

## 11. Open decision, with a recommendation

`config/story.yaml` names `gpt-5-nano` as the OpenAI default. It is a reasoning model, so
`temperature` is not sent and determinism is weaker than the pinned local path's — recorded
honestly in `provider_settings` rather than papered over. **Recommendation: keep `gpt-5-nano` as
the default and offer `gpt-4.1-mini` as the temperature-pinned alternative**, so the demo's default
is the current-generation model while a determinism-sensitive comparison has somewhere to go. The
owner may reverse this by editing one key; both are verified present on the account.
