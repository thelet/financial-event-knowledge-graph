# Stage 7 — the real local generation provider

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 7, §4.3.
**Runtime already validated:** [LOCAL_RUNTIME_VALIDATED.md](LOCAL_RUNTIME_VALIDATED.md).
**Goal:** one adapter satisfying `extraction.contracts.GenerationProvider`, against the
running local server. No lane, no prompt strategy, no benchmark scoring — those are steps 10
and 11.

---

# 1. Scope

Implement `LocalOpenAICompatibleGenerationProvider`:

```text
extraction/providers/
  __init__.py
  public.py                    ProviderConfig, ProviderError hierarchy, GenerationResult
  local_openai_compatible.py   the adapter
```

`extraction/providers/` is a new package. It is the **only** place in the repository allowed
to know an HTTP wire format exists. `extraction/stages/tables/**` must remain provider-free —
its import test already enforces that and must keep passing.

# 2. Configuration

Defaults exactly as validated, in `config/extraction.yaml` under a new `provider:` key:

```yaml
provider:
  kind: local_openai_compatible
  base_url: http://127.0.0.1:8080
  model: Qwen3.5-9B-Q4_K_M.gguf
  context_tokens: 8192
  max_output_tokens: 1024
  temperature: 0.0
  timeout_seconds: 120
  max_retries: 2
  enable_thinking: false
```

**`enable_thinking` must default to `false` and configuration validation must reject `true`.**
With thinking on, a four-field schema request spent all 900 tokens reasoning and returned
empty content; with it off the same request returned conformant JSON in 59 tokens. A lane
whose budget failures present as extraction failures is worse than one that cannot run.

Rejection is at config-load time with a named error, not a warning.

# 3. Behaviour required

| Concern | Requirement |
| --- | --- |
| health check | `GET /health`; returns structured status, never raises for a normal down server |
| generation | `POST /v1/chat/completions`, **non-streaming** |
| JSON Schema mode | `response_format: {type: json_schema, json_schema: {...strict...}}` |
| thinking | always sends `chat_template_kwargs: {enable_thinking: false}` |
| strict parsing | response content parsed with `json.loads`; a parse failure is a typed error, never a silent `{}` |
| retries | bounded, transport/5xx only. **Never** retry a schema-invalid response — that is a model result, not a transport fault |
| timeouts | per-request, from config |
| errors | typed hierarchy: `ProviderUnavailable`, `ProviderTimeout`, `ProviderTransportError`, `ProviderResponseError`, `ProviderSchemaError` |
| raw hashing | sha256 of the raw response body, recorded |
| usage/timing | prompt/completion tokens, wall-clock ms, model id |
| no leakage | returns `GenerationResult`, a plain dataclass. No `httpx`/`requests` object, no raw dict, escapes |

`reasoning_content`, when present, may be recorded in metadata but never returned as content.

# 4. Dependency

`httpx` is already a project dependency (`pyproject.toml`) and is used by `acquisition`.
Reuse it. Add nothing.

# 5. Tests

**Offline** (`tests/extraction/test_provider.py`, no marker):
1. `enable_thinking: true` in config fails validation with a named error.
2. Default config has thinking off and matches the validated runtime values.
3. Request body: non-streaming, carries the schema, carries `enable_thinking: false`.
4. A transport error retries up to the bound, then raises `ProviderTransportError`.
5. A schema-invalid response raises `ProviderSchemaError` and is **not** retried.
6. A timeout raises `ProviderTimeout`.
7. Malformed JSON content raises `ProviderResponseError`, never returns `{}`.
8. `GenerationResult` carries no vendor object — assert its field types are primitives.
9. Health check on a closed port returns unhealthy rather than raising.
10. The provider satisfies `GenerationProvider` driven **through** the protocol.
11. Import test: nothing under `extraction/stages/` imports `extraction.providers`.

A fake HTTP transport is fine for these. **Mocks do not satisfy the live gate.**

**Live** (`tests/extraction/test_provider_live.py`, `@pytest.mark.live`):
12. `/health` succeeds against the running server.
13. One non-trivial request — a real benchmark table passage, schema with metric/value/unit/
    period — returns schema-conformant JSON.
14. Usage and timing are populated and non-zero.
15. Raw response hash is stable across two identical requests at temperature 0.

# 6. Live gate (must be demonstrated, not asserted)

- server health succeeds;
- one non-trivial benchmark request succeeds;
- structured output parses and conforms;
- the parsed output reaches `assemble` and produces an `OntologyClaim`;
- `ontology.validate_claims` runs on it;
- extraction evidence validation runs on it;
- runtime and token statistics recorded.

# 7. Acceptance

- [ ] `pytest -m "not live"` green; count rises by the offline tests only.
- [ ] `pytest -m live` green with the server up.
- [ ] `tests/extraction/test_table_lane.py::test_no_provider_is_reachable_from_the_table_lane` still passes.
- [ ] Live gate demonstrated with recorded numbers.
- [ ] No change to any lane, the ontology, benchmark cases, or the stage-6 reports.

# 8. Non-goals

- The narrative lane, prompts, candidate scoping (steps 8–10).
- Benchmark scoring of model output (step 11).
- Streaming, batching, concurrency, model comparison, or a second provider.
