"""The story provider, the portable schema subset and the replay store — with no server.

A fake transport is enough to pin the request body, the retry boundary, the error taxonomy
and the digest the replay store keys on. It is *not* enough to claim the provider works —
that is `test_story_provider_live.py`, and the two files are separate so this one keeps
passing under `pytest -m "not live"` on a machine with no model at all.

Every response fixture below is the shape the running llama.cpp build actually returns
*(read off the server 2026-08-01, commit `ddd4ec1`, tag `b10217`, and recorded in
`tests/extraction/test_provider.py`)*, including the `prompt_tokens_details` nesting and the
`timings` block that make the envelope digest unstable.

The three personas of §15.1 appear throughout rather than one generic prompt, because the
extension this step exists for — a `system` distinct from the `prompt`, and a `schema_name`
that reaches the wire — is only observable when more than one of them is in play.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from story.contracts import StoryGenerationProvider
from story.core.models import GenerationResult
from story.providers import (
    GenerationStore,
    MissingGenerationError,
    PINNED_TEMPERATURE,
    ReplayingStoryGenerationProvider,
    StoredGeneration,
    StoryOpenAICompatibleProvider,
    StoryProviderConfig,
    StoryProviderConfigurationError,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    StoryProviderTimeout,
    StoryProviderTransportError,
    StoryProviderUnavailable,
    load_provider_config,
    request_identity,
    schema_violations,
    validate_portable_schema,
)
from story.providers.public import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    RETRYABLE_STATUSES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: §15.3's subset and nothing else: `type`, `required`, `properties`,
#: `additionalProperties: false`, `enum`, `items`. Shaped like the editorial plan of §11 so the
#: fixture is the thing the planner will really ask for.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string"},
        "audience": {"type": "string", "enum": ["external", "internal"]},
        "key_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "statement_class": {
                        "type": "string",
                        "enum": ["reported", "calculated", "explanatory"],
                    },
                },
                "required": ["claim", "statement_class"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["thesis", "audience", "key_points"],
    "additionalProperties": False,
}

CONFORMANT_PLAN = {
    "thesis": "Adjusted EBITDA crossed zero in 2022Q3.",
    "audience": "external",
    "key_points": [
        {"claim": "Adjusted EBITDA was negative $211 million.",
         "statement_class": "reported"},
    ],
}

PLANNER_SYSTEM = "You are an editorial planner. You may use only the package you are given."
WRITER_SYSTEM = "You are a writer. Every sentence must bind to a fact in the package."
PROMPT = "Package pkg:metric-move-adjusted-ebitda-opendoor-2022q2-2022q3:0123456789ab"


def envelope(content: str, *, finish_reason: str = "stop", **message_extra) -> dict:
    """A llama.cpp `/v1/chat/completions` body, shaped as the server really returns one."""
    message = {"role": "assistant", "content": content}
    message.update(message_extra)
    return {
        "choices": [{"finish_reason": finish_reason, "index": 0, "message": message}],
        "created": 1785587924,
        "model": "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "system_fingerprint": "b1-ddd4ec1",
        "object": "chat.completion",
        "usage": {"completion_tokens": 58, "prompt_tokens": 51, "total_tokens": 109,
                  "prompt_tokens_details": {"cached_tokens": 0}},
        "id": "chatcmpl-nLMMqOMzBtVqdZCXzAmijZqvuX114ttI",
        "timings": {"predicted_n": 58, "predicted_ms": 916.214},
    }


def provider_on(handler, *, sleeps: list[float] | None = None,
                config: StoryProviderConfig | None = None, **config_overrides):
    """A provider whose transport is `handler` and whose backoff does not sleep."""
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    config = config or StoryProviderConfig(**config_overrides)
    client = httpx.Client(transport=httpx.MockTransport(recording))
    provider = StoryOpenAICompatibleProvider(
        config, client=client,
        sleep=(sleeps.append if sleeps is not None else (lambda _seconds: None)))
    return provider, calls


def answering(content: dict | str = CONFORMANT_PLAN):
    text = content if isinstance(content, str) else json.dumps(content)
    return lambda request: httpx.Response(200, json=envelope(text))


def generate(provider, **overrides):
    """One planner request. Every keyword the protocol declares, named."""
    call = dict(system=PLANNER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                schema_name="story_editorial_plan", max_tokens=512)
    call.update(overrides)
    return provider.generate(**call)


def free_port() -> int:
    """A port nothing is listening on. Bound and released so the number is genuinely unused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- the request body --------------------------------------------------------------------------


def test_the_request_carries_the_system_and_the_prompt_as_two_separate_messages():
    """The one extension §15.1 requires over extraction, which collapses to a single user turn.

    Folding the persona into the prompt string would hide the structure inside the request
    digest, and three call sites would be three prompts rather than three roles.
    """
    provider, calls = provider_on(answering())
    generate(provider)

    body = json.loads(calls[0].content)
    assert body["messages"] == [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": PROMPT},
    ]


def test_the_request_is_non_streaming_pins_temperature_and_turns_thinking_off():
    provider, calls = provider_on(answering())
    generate(provider, max_tokens=512)

    body = json.loads(calls[0].content)
    assert calls[0].url.path == "/v1/chat/completions"
    assert body["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert body["stream"] is False
    assert body["temperature"] == 0.0 == PINNED_TEMPERATURE
    assert body["max_tokens"] == 512
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "schema_name", ["story_editorial_plan", "story_draft", "story_advisory_verification"])
def test_the_schema_name_the_caller_gave_reaches_the_wire(schema_name):
    """Extraction hard-codes `extraction_claim` for every call; three story personas would be
    indistinguishable in a capture, and the server names the wrong schema in its own errors."""
    provider, calls = provider_on(answering())
    generate(provider, schema_name=schema_name)

    json_schema = json.loads(calls[0].content)["response_format"]["json_schema"]
    assert json_schema["name"] == schema_name


def test_the_schema_travels_in_the_nested_response_format_the_parser_actually_reads():
    """The llama.cpp README documents a top-level `schema` variant the parser does not read,
    so it applies no constraint and does so silently."""
    provider, calls = provider_on(answering())
    generate(provider)

    body = json.loads(calls[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == PLAN_SCHEMA
    assert "schema" not in body["response_format"], "the top-level form constrains nothing"


def test_a_request_with_no_persona_is_refused_before_anything_is_sent():
    """An empty system message is indistinguishable on the wire from sending none, while still
    entering the request digest as a distinct value."""
    provider, calls = provider_on(answering())
    with pytest.raises(StoryProviderConfigurationError) as raised:
        generate(provider, system="   ")
    assert "persona" in str(raised.value)
    assert calls == []


def test_a_request_with_no_schema_name_is_refused_before_anything_is_sent():
    provider, calls = provider_on(answering())
    with pytest.raises(StoryProviderConfigurationError):
        generate(provider, schema_name="")
    assert calls == []


def test_no_authorization_header_is_sent_when_no_key_is_configured():
    """The local server needs none, and a header built from an empty secret is how a
    placeholder reaches a real endpoint."""
    provider, calls = provider_on(answering())
    generate(provider)
    assert "authorization" not in {name.lower() for name in calls[0].headers}


def test_a_key_from_the_environment_is_sent_as_a_bearer_token_and_never_printed(tmp_path):
    config = load_provider_config(
        env_path=tmp_path / "absent.env", environ={ENV_API_KEY: "sk-not-a-real-key"})
    provider, calls = provider_on(answering(), config=config)
    generate(provider)

    assert calls[0].headers["Authorization"] == "Bearer sk-not-a-real-key"
    assert "sk-not-a-real-key" not in repr(config)


# -- retries: transport faults only ---------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUSES))
def test_a_retryable_status_is_retried_and_a_later_success_is_returned(status):
    responses = [httpx.Response(status, text="loading model"),
                 httpx.Response(200, json=envelope(json.dumps(CONFORMANT_PLAN)))]
    provider, calls = provider_on(lambda request: responses.pop(0))
    result = generate(provider)
    assert (result.attempts, len(calls)) == (2, 2)
    assert result.content == CONFORMANT_PLAN


def test_a_transport_error_retries_to_the_bound_then_raises_with_exponential_backoff():
    def fail(request):
        raise httpx.ReadError("connection reset")

    slept: list[float] = []
    provider, calls = provider_on(fail, sleeps=slept, max_retries=2)
    with pytest.raises(StoryProviderTransportError):
        generate(provider)
    assert len(calls) == 3, "max_retries counts attempts after the first"
    assert slept == [0.25, 0.5]


def test_a_timeout_is_never_retried():
    """On a single-slot local server a second 120-second wait behind the first turns one slow
    request into a six-minute stall, and a story candidate makes three requests."""
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    provider, calls = provider_on(slow, max_retries=2)
    with pytest.raises(StoryProviderTimeout):
        generate(provider)
    assert len(calls) == 1


def test_a_schema_violation_is_never_retried():
    """A model result, not a transport fault. Re-asking at temperature 0 returns the same
    answer and charges for it twice."""
    wrong = dict(CONFORMANT_PLAN, audience="shareholders")
    provider, calls = provider_on(answering(wrong), max_retries=2)
    with pytest.raises(StoryProviderSchemaError) as raised:
        generate(provider)
    assert len(calls) == 1
    assert raised.value.violations
    assert "audience" in raised.value.violations[0]


def test_a_missing_required_field_is_a_schema_violation_and_is_not_retried():
    provider, calls = provider_on(
        answering({"thesis": "t", "audience": "external"}), max_retries=2)
    with pytest.raises(StoryProviderSchemaError):
        generate(provider)
    assert len(calls) == 1


def test_a_violation_nested_inside_an_array_item_is_found():
    wrong = dict(CONFORMANT_PLAN,
                 key_points=[{"claim": "c", "statement_class": "speculative"}])
    provider, _ = provider_on(answering(wrong))
    with pytest.raises(StoryProviderSchemaError) as raised:
        generate(provider)
    assert "key_points[0].statement_class" in raised.value.violations[0]


def test_a_non_retryable_status_raises_immediately():
    provider, calls = provider_on(
        lambda request: httpx.Response(400, text="bad request"), max_retries=2)
    with pytest.raises(StoryProviderResponseError):
        generate(provider)
    assert len(calls) == 1


def test_a_refused_connection_reports_unavailable_rather_than_a_generic_fault():
    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    provider, _ = provider_on(refused)
    with pytest.raises(StoryProviderUnavailable):
        generate(provider)


# -- malformed responses, each a distinct typed failure ---------------------------------------


def test_a_body_that_is_not_json_raises_a_response_error():
    provider, _ = provider_on(lambda request: httpx.Response(200, text="<html>502</html>"))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "not JSON" in str(raised.value)


def test_a_body_carrying_no_choices_raises_a_response_error():
    provider, _ = provider_on(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "choices" in str(raised.value)


def test_blank_content_raises_and_names_the_finish_reason():
    """The exact failure thinking mode produced: `finish_reason: length`, content `''`."""
    provider, _ = provider_on(
        lambda request: httpx.Response(200, json=envelope("", finish_reason="length")))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "length" in str(raised.value)


def test_content_that_is_not_json_raises_rather_than_returning_an_empty_object():
    """`{}` is a legitimate answer for some schemas. Returning one here would make an
    unparseable response indistinguishable from a real, empty one."""
    provider, _ = provider_on(answering('{"thesis": "Adjusted EBITDA'))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "not JSON" in str(raised.value)


def test_content_that_is_not_an_object_raises_a_response_error():
    provider, _ = provider_on(answering('["thesis"]'))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "not an object" in str(raised.value)


# -- what crosses the boundary ----------------------------------------------------------------


def test_the_result_carries_no_vendor_object():
    provider, _ = provider_on(answering())
    result = generate(provider)

    assert isinstance(result, GenerationResult)
    allowed = (str, int, float, bool, dict, tuple, list, type(None))
    for name in type(result).model_fields:
        assert isinstance(getattr(result, name), allowed), name
    assert type(result.content) is dict
    assert "httpx" not in repr(result)


def test_usage_hashes_and_both_model_identifiers_are_recorded():
    provider, _ = provider_on(answering())
    result = generate(provider)

    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (51, 58, 109)
    assert result.latency_ms >= 0.0
    assert len(result.raw_sha256) == 64 and len(result.content_sha256) == 64
    assert result.finish_reason == "stop"
    # The server reports the file path it was started with, not the configured name.
    assert result.model_id.endswith("Qwen3.5-9B-Q4_K_M.gguf")
    assert result.metadata["configured_model"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert result.metadata["schema_name"] == "story_editorial_plan"


def test_the_content_digest_ignores_the_unstable_envelope():
    """The envelope carries a fresh `id`, `created` and `timings` on every call, so its digest
    cannot be a determinism check. The content digest can."""
    bodies = [envelope(json.dumps(CONFORMANT_PLAN)), envelope(json.dumps(CONFORMANT_PLAN))]
    bodies[1]["id"] = "chatcmpl-different"
    bodies[1]["created"] = 1785599999
    provider, _ = provider_on(lambda request: httpx.Response(200, json=bodies.pop(0)))
    first, second = generate(provider), generate(provider)
    assert first.content_sha256 == second.content_sha256
    assert first.raw_sha256 != second.raw_sha256


def test_reasoning_content_is_recorded_but_never_returned_as_content():
    provider, _ = provider_on(lambda request: httpx.Response(200, json=envelope(
        json.dumps(CONFORMANT_PLAN), reasoning_content="which quarter did it cross?")))
    result = generate(provider)
    assert result.content == CONFORMANT_PLAN
    assert result.metadata["reasoning_content"] == "which quarter did it cross?"
    assert result.metadata["reasoning_characters"] == 27


def test_the_provider_is_usable_through_the_story_generation_provider_protocol():
    """Driven, not `isinstance`-checked. An isinstance assertion against a runtime-checkable
    Protocol verifies method presence and proves almost nothing."""
    provider, _ = provider_on(answering())
    port: StoryGenerationProvider = provider
    result = port.generate(system=WRITER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                           schema_name="story_draft", max_tokens=512, temperature=0.0)
    assert result.content["audience"] == "external"
    assert port.health().status in ("ok", "unknown")


# -- health -----------------------------------------------------------------------------------


def test_health_on_a_closed_port_reports_unhealthy_rather_than_raising():
    provider = StoryOpenAICompatibleProvider(
        StoryProviderConfig(base_url=f"http://127.0.0.1:{free_port()}", timeout_seconds=2))
    try:
        status = provider.health()
    finally:
        provider.close()
    assert status.ok is False
    assert status.status in ("unavailable", "timeout")
    assert status.detail


def test_health_reports_ok_when_the_server_says_so():
    provider, _ = provider_on(lambda request: httpx.Response(200, json={"status": "ok"}))
    status = provider.health()
    assert status.ok and status.status == "ok"


def test_health_reports_a_loading_server_as_not_ok():
    """llama.cpp answers 503 while the model is still loading."""
    provider, _ = provider_on(
        lambda request: httpx.Response(503, json={"status": "loading model"}))
    assert provider.health().ok is False


# -- the portable schema subset, refused at build time ----------------------------------------


@pytest.mark.parametrize("keyword,value", [
    ("minimum", 0), ("maximum", 100), ("pattern", "^[A-Z]+$"), ("minItems", 1),
    ("maxItems", 3), ("format", "date"), ("description", "the thesis"), ("default", ""),
])
def test_a_schema_using_a_non_portable_keyword_is_refused_when_the_request_is_built(
        keyword, value):
    """llama.cpp's GBNF builder drops these silently and the local checker ignores them, so the
    request would be unconstrained without saying so. Better to fail before sending it."""
    schema = {
        "type": "object",
        "properties": {"thesis": {"type": "string", keyword: value}},
        "required": ["thesis"],
        "additionalProperties": False,
    }
    provider, calls = provider_on(answering())
    with pytest.raises(StoryProviderConfigurationError) as raised:
        generate(provider, schema=schema)
    assert keyword in str(raised.value)
    assert calls == [], "nothing may be sent under a schema that constrains nothing"


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf", "allOf", "$ref", "prefixItems"])
def test_a_schema_using_a_composition_keyword_is_refused(keyword):
    with pytest.raises(StoryProviderConfigurationError) as raised:
        validate_portable_schema({keyword: [{"type": "string"}]})
    assert keyword in str(raised.value)


def test_an_object_that_leaves_a_property_optional_is_refused():
    """§15.3: an optional property is one the model silently omits on the hard cases."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        validate_portable_schema({
            "type": "object",
            "properties": {"thesis": {"type": "string"}, "caveat": {"type": "string"}},
            "required": ["thesis"],
            "additionalProperties": False,
        })
    assert "caveat" in str(raised.value)


def test_an_object_that_admits_additional_properties_is_refused():
    with pytest.raises(StoryProviderConfigurationError) as raised:
        validate_portable_schema({
            "type": "object",
            "properties": {"thesis": {"type": "string"}},
            "required": ["thesis"],
        })
    assert "additionalProperties" in str(raised.value)


def test_an_array_with_no_items_schema_is_refused():
    with pytest.raises(StoryProviderConfigurationError):
        validate_portable_schema({"type": "array"})


def test_an_empty_enum_is_refused():
    with pytest.raises(StoryProviderConfigurationError):
        validate_portable_schema({"type": "string", "enum": []})


def test_the_shape_the_planner_really_asks_for_is_within_the_subset():
    """The guard on the guards: every refusal above is worthless if nothing passes."""
    assert validate_portable_schema(PLAN_SCHEMA) is None


@pytest.mark.parametrize("value,expected", [
    (CONFORMANT_PLAN, 0),
    (dict(CONFORMANT_PLAN, audience="nobody"), 1),
    (dict(CONFORMANT_PLAN, thesis=17), 1),
    (dict(CONFORMANT_PLAN, extra="x"), 1),
    ({"thesis": "t", "audience": "external"}, 1),
])
def test_the_response_checker_covers_the_keywords_the_story_schemas_use(value, expected):
    assert len(schema_violations(value, PLAN_SCHEMA)) == expected


def test_a_boolean_is_not_a_number_even_though_bool_subclasses_int():
    schema = {"type": "object", "properties": {"delta": {"type": "number"}},
              "required": ["delta"], "additionalProperties": False}
    assert schema_violations({"delta": True}, schema)
    assert not schema_violations({"delta": 1.5}, schema)


# -- configuration ----------------------------------------------------------------------------


def test_an_unrecognised_provider_kind_is_refused_rather_than_defaulted():
    """`config.provider.kind` exists upstream and is never dispatched on (§15.1)."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        StoryProviderConfig.from_config({"provider": {"kind": "openai"}})
    assert "kind" in str(raised.value)


def test_a_secret_in_a_configuration_file_is_refused_rather_than_read():
    """The file is tracked. A configuration that can hold a key is one that eventually does."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        StoryProviderConfig.from_config({"provider": {"api_key": "sk-committed"}})
    assert ENV_API_KEY in str(raised.value)


def test_an_output_budget_that_leaves_no_room_for_a_prompt_is_rejected():
    with pytest.raises(StoryProviderConfigurationError):
        StoryProviderConfig.from_config(
            {"provider": {"context_tokens": 8192, "max_output_tokens": 8192}})


def test_the_defaults_are_the_validated_local_runtime():
    config = load_provider_config(env_path=Path("/nonexistent/.env"), environ={})
    assert config.kind == "local_openai_compatible"
    assert config.base_url == "http://127.0.0.1:8080"
    assert config.model == "Qwen3.5-9B-Q4_K_M.gguf"
    assert (config.context_tokens, config.max_output_tokens) == (8192, 1024)
    assert (config.timeout_seconds, config.max_retries) == (120.0, 2)
    assert config.api_key is None


def test_the_environment_wins_over_the_env_file_which_wins_over_the_defaults(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{ENV_BASE_URL}=http://127.0.0.1:9999\n{ENV_MODEL}='from-the-file.gguf'\n",
        encoding="utf-8")
    config = load_provider_config(
        env_path=env_file, environ={ENV_MODEL: "from-the-environment.gguf"})
    assert config.base_url == "http://127.0.0.1:9999"
    assert config.model == "from-the-environment.gguf"


def test_a_temperature_cannot_be_configured_at_all():
    """§15.1 pins it at the call site. A field would let a YAML edit re-key the whole store."""
    assert "temperature" not in {f.name for f in StoryProviderConfig.__dataclass_fields__.values()}
    assert PINNED_TEMPERATURE == 0.0


# -- the request identity the store is keyed by -----------------------------------------------


def identity(**overrides) -> str:
    call = dict(system=PLANNER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                schema_name="story_editorial_plan", model_id="Qwen3.5-9B-Q4_K_M.gguf",
                temperature=PINNED_TEMPERATURE, max_tokens=512)
    call.update(overrides)
    return request_identity(**call)


def test_the_same_request_produces_the_same_identity():
    assert identity() == identity()
    assert len(identity()) == 64


@pytest.mark.parametrize("field,value", [
    ("system", WRITER_SYSTEM),
    ("prompt", "a different package"),
    ("schema_name", "story_draft"),
    ("model_id", "Qwen3.5-9B-Q8_0.gguf"),
    ("temperature", 0.2),
    ("max_tokens", 513),
])
def test_changing_any_digest_input_changes_the_identity(field, value):
    assert identity(**{field: value}) != identity()


def test_a_changed_schema_changes_the_identity_but_a_reordered_one_does_not():
    """A JSON Schema is a mapping: two stages building it through different code paths must
    produce one key, while a genuinely different grammar must produce another."""
    reordered = dict(reversed(list(PLAN_SCHEMA.items())))
    assert identity(schema=reordered) == identity()
    assert identity(schema=dict(PLAN_SCHEMA, required=["thesis", "audience"])) != identity()


def test_two_personas_asking_the_same_question_do_not_collide_on_one_row():
    """The store-level proof of the digest property above: three call sites, one package."""
    store = GenerationStore()
    provider, _ = provider_on(answering())
    replaying = ReplayingStoryGenerationProvider(store, provider, model_id="test-model")

    generate(replaying, system=PLANNER_SYSTEM, schema_name="story_editorial_plan")
    generate(replaying, system=WRITER_SYSTEM, schema_name="story_draft")

    assert len(store) == 2
    assert {row.schema_name for row in store.generations()} == {
        "story_editorial_plan", "story_draft"}


# -- the replay store -------------------------------------------------------------------------


def stored(**overrides) -> StoredGeneration:
    fields = dict(
        request_sha256=identity(),
        content_sha256="c" * 64,
        model_id="Qwen3.5-9B-Q4_K_M.gguf",
        provider_model_id="/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        schema_name="story_editorial_plan",
        prompt_version="story-planner:1.0.0",
        temperature=PINNED_TEMPERATURE,
        max_tokens=512,
        finish_reason="stop",
        raw_content=json.dumps(CONFORMANT_PLAN),
    )
    fields.update(overrides)
    return StoredGeneration(**fields)


def test_a_stored_generation_is_replayed_without_touching_the_inner_provider():
    store = GenerationStore()
    store.put(stored())

    def refuse(request):
        raise AssertionError("a replayed request must not reach the transport")

    provider, calls = provider_on(refuse)
    replaying = ReplayingStoryGenerationProvider(store, provider)
    result = generate(replaying)

    assert calls == []
    assert result.content == CONFORMANT_PLAN
    assert result.metadata["replayed"] is True
    assert result.model_id.endswith("Qwen3.5-9B-Q4_K_M.gguf")


def test_a_replayed_result_invents_no_token_count_and_no_latency():
    """Deliberately zeroes. Plausible numbers would make a replayed result indistinguishable
    from a generated one in exactly the place a reader wants to tell them apart."""
    store = GenerationStore()
    store.put(stored())
    result = generate(ReplayingStoryGenerationProvider(store, model_id="Qwen3.5-9B-Q4_K_M.gguf"))
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (0, 0, 0)
    assert (result.latency_ms, result.attempts, result.raw_sha256) == (0.0, 0, "")


def test_a_miss_with_no_inner_provider_raises_and_names_the_digest_it_missed_on():
    """A replay that quietly reaches for a server is not a replay, and every miss has to be
    recorded with the request it would have issued."""
    store = GenerationStore()
    replaying = ReplayingStoryGenerationProvider(store, model_id="Qwen3.5-9B-Q4_K_M.gguf")
    with pytest.raises(MissingGenerationError) as raised:
        generate(replaying)
    assert raised.value.request_sha256 == identity()
    assert identity() in str(raised.value)


def test_a_miss_with_an_inner_provider_generates_and_records_what_it_asked():
    store = GenerationStore()
    provider, calls = provider_on(answering())
    replaying = ReplayingStoryGenerationProvider(
        store, provider, prompt_version="story-planner:1.0.0")
    result = generate(replaying)

    assert len(calls) == 1
    row = store.get(identity())
    assert row is not None
    assert row.raw_content == result.raw_content
    assert row.content_sha256 == result.content_sha256
    assert row.model_id == "Qwen3.5-9B-Q4_K_M.gguf", "the configured name, not the path"
    assert row.provider_model_id.endswith(".gguf") and "/" in row.provider_model_id
    assert (row.schema_name, row.prompt_version) == (
        "story_editorial_plan", "story-planner:1.0.0")


def test_a_second_identical_request_is_served_from_the_store_the_first_one_filled():
    store = GenerationStore()
    provider, calls = provider_on(answering())
    replaying = ReplayingStoryGenerationProvider(store, provider)
    first, second = generate(replaying), generate(replaying)

    assert len(calls) == 1
    assert first.content_sha256 == second.content_sha256
    assert second.metadata["replayed"] is True


def test_the_row_holds_nothing_that_changes_between_two_identical_requests():
    """Latency, token counts, attempts and the envelope's `id`/`created`/`timings` are
    operational statistics: a record containing them could never be byte-identical."""
    volatile = {"latency_ms", "prompt_tokens", "completion_tokens", "total_tokens",
                "attempts", "raw_sha256", "id", "created", "timings"}
    assert volatile & set(stored().as_row()) == set()


def test_the_file_is_rendered_in_digest_order_and_rebuilt_whole(tmp_path):
    store = GenerationStore()
    for name in ("story_draft", "story_editorial_plan", "story_advisory_verification"):
        store.put(stored(request_sha256=identity(schema_name=name), schema_name=name))

    rendered = store.render()
    digests = [json.loads(line)["request_sha256"] for line in rendered.splitlines()]
    assert digests == sorted(digests)

    path = store.write(tmp_path / "generations.jsonl")
    assert not (tmp_path / "generations.jsonl.partial").exists()
    assert GenerationStore(path).render() == rendered
    assert len(GenerationStore(path)) == 3


def test_a_written_store_is_sufficient_to_replay_from_on_its_own(tmp_path):
    """The configuration `story rebuild` runs under: no server, no model id passed in, and the
    file states which model its rows were keyed under."""
    store = GenerationStore()
    store.put(stored())
    path = store.write(tmp_path / "generations.jsonl")

    reloaded = GenerationStore(path)
    assert reloaded.identity_model_id() == "Qwen3.5-9B-Q4_K_M.gguf"
    result = generate(ReplayingStoryGenerationProvider(reloaded))
    assert result.content == CONFORMANT_PLAN


def test_a_store_whose_rows_disagree_about_the_model_states_no_identity():
    """Picking one silently would turn every row of the other into a miss."""
    store = GenerationStore()
    store.put(stored())
    store.put(stored(request_sha256="f" * 64, model_id="some-other-model.gguf"))
    assert store.identity_model_id() is None
    with pytest.raises(ValueError):
        ReplayingStoryGenerationProvider(store)


def test_a_replay_only_provider_reports_health_without_a_server():
    """A replay-only run is fully able to proceed; reporting it unhealthy would make the
    freshness gate refuse the one configuration that provably needs nothing running."""
    status = ReplayingStoryGenerationProvider(GenerationStore(), model_id="m").health()
    assert status.ok is True and status.status == "replay"


def test_the_replaying_provider_is_usable_through_the_protocol():
    store = GenerationStore()
    store.put(stored())
    port: StoryGenerationProvider = ReplayingStoryGenerationProvider(
        store, model_id="Qwen3.5-9B-Q4_K_M.gguf")
    result = port.generate(system=PLANNER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                           schema_name="story_editorial_plan", max_tokens=512,
                           temperature=PINNED_TEMPERATURE)
    assert result.content["thesis"].startswith("Adjusted EBITDA")


# -- the guarantee the lazy indirection exists to make good -----------------------------------


def test_importing_the_providers_package_pulls_in_neither_an_http_client_nor_a_driver():
    """Measured in a fresh interpreter, not argued about (§15.2).

    Python runs a package's `__init__` before any submodule of it, so an eager re-export here
    would put `httpx` and `neo4j` into `sys.modules` for a stage that names neither — true in
    the source, false in the interpreter, and invisible to an import-graph test.
    """
    probe = ("import story.providers, sys; "
             "print(int('httpx' in sys.modules), int('neo4j' in sys.modules))")
    finished = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True)
    assert finished.stdout.strip() == "0 0", finished.stdout


def test_the_names_the_package_exports_all_resolve():
    """A lazy `__getattr__` is a place for a typo to live until the day it is imported."""
    import story.providers as providers

    for name in providers.__all__:
        assert getattr(providers, name) is not None, name
    with pytest.raises(AttributeError):
        providers.SomethingNobodyDeclared
