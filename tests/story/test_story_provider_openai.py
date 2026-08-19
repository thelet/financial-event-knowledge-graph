"""The OpenAI Responses adapter, the provider registry and the key rule — with no network.

Offline **by construction**, not by convention: every provider below is built over an
`httpx.MockTransport`, and the tests that claim "nothing was sent" assert it against a
transport that fails the test if it is called at all.
`tests/story/test_story_provider_openai_live.py` is the other half, and the only file here that
spends money.

Every response fixture is the shape the real API returned *(read off
`POST https://api.openai.com/v1/responses` on 2026-08-19 with `gpt-5-nano` and
`gpt-4.1-nano`)*, including the `output` list that carries a reasoning item beside the message,
the `input_tokens`/`output_tokens` spelling of `usage`, and the `status: incomplete` /
`incomplete_details: {"reason": "max_output_tokens"}` pair a too-small budget produces.

The planner and writer personas of §15.1 are both here, because two things this file exists to
prove are only observable with more than one: that `schema_name` reaches the wire, and that
**the same schema object goes to both providers unmodified**.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest

from story.contracts import StoryGenerationProvider
from story.core.models import GenerationResult
from story.providers import (
    PINNED_TEMPERATURE,
    PROVIDER_LOCAL,
    PROVIDER_OPENAI,
    StoryOpenAICompatibleProvider,
    StoryOpenAIResponsesProvider,
    StoryProviderConfig,
    StoryProviderConfigurationError,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    StoryProviderTimeout,
    StoryProviderTransportError,
    StoryProviderUnavailable,
    load_provider_config,
    provider_catalogue,
    schema_violations,
    validate_portable_schema,
)
from story.providers.public import (
    ENV_OPENAI_API_KEY,
    ENV_OPENAI_MODEL,
    OPENAI_KEY_MISSING_REASON,
    RETRYABLE_STATUSES,
)

#: Not a real credential, and shaped like one on purpose: the redaction rule this file checks
#: matches `sk-…` tokens, so a needle that did not look like a key would prove nothing.
FAKE_KEY = "sk-proj-notarealkey-000111222333"
NO_ENV_FILE = Path("/nonexistent/.env")

#: §15.3's subset, and the shape §11's planner really asks for. The *same object* is handed to
#: both adapters below; neither is allowed to edit it on the way to a wire.
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

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["headline", "body"],
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


# -- fixtures shaped like the real wire ---------------------------------------------------------


def envelope(content: str, *, status: str = "completed", reasoning: bool = True,
             model: str = "gpt-5-nano-2025-08-07", **overrides) -> dict:
    """A `/responses` body, as the API really returns one (measured 2026-08-19)."""
    message = {
        "id": "msg_68a3",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "annotations": [], "logprobs": [], "text": content}],
    }
    output: list[dict] = []
    if reasoning:
        output.append({"id": "rs_68a3", "type": "reasoning", "summary": [], "content": []})
    output.append(message)
    body = {
        "id": "resp_68a3f0c1",
        "object": "response",
        "created_at": 1785587924,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "model": model,
        "output": output,
        "store": False,
        "usage": {
            "input_tokens": 110,
            "input_tokens_details": {"cached_tokens": 32, "cache_write_tokens": 0},
            "output_tokens": 41,
            "output_tokens_details": {"reasoning_tokens": 17},
            "total_tokens": 151,
        },
    }
    body.update(overrides)
    return body


def api_error(code: str, message: str) -> dict:
    return {"error": {"message": message, "type": "invalid_request_error",
                      "param": None, "code": code}}


def openai_config(*, model_id: str | None = None, key: str | None = FAKE_KEY,
                  config=None, **environ_extra) -> StoryProviderConfig:
    environ = dict(environ_extra)
    if key is not None:
        environ[ENV_OPENAI_API_KEY] = key
    return load_provider_config(config, provider_id=PROVIDER_OPENAI, model_id=model_id,
                                env_path=NO_ENV_FILE, environ=environ)


def provider_on(handler, *, sleeps: list[float] | None = None,
                config: StoryProviderConfig | None = None, **config_kwargs):
    """A provider whose transport is `handler` and whose backoff does not sleep."""
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    config = config or openai_config(**config_kwargs)
    client = httpx.Client(transport=httpx.MockTransport(recording))
    provider = StoryOpenAIResponsesProvider(
        config, client=client,
        sleep=(sleeps.append if sleeps is not None else (lambda _seconds: None)))
    return provider, calls


def answering(content: dict | str = CONFORMANT_PLAN, **envelope_extra):
    text = content if isinstance(content, str) else json.dumps(content)
    return lambda request: httpx.Response(200, json=envelope(text, **envelope_extra))


def refusing(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request may be issued; got {request.method} {request.url}")


def generate(provider, **overrides):
    """One planner request. Every keyword the protocol declares, named."""
    call = dict(system=PLANNER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                schema_name="story_editorial_plan", max_tokens=2048)
    call.update(overrides)
    return provider.generate(**call)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- the request body ---------------------------------------------------------------------------


def test_the_planner_request_is_the_exact_json_the_api_measured():
    """The whole body, asserted as one object rather than field by field.

    `gpt-4.1-mini` declares `supports_temperature: true`, so the pinned temperature is on the
    wire. The `text.format` block is **flat** — `name`, `strict` and `schema` are siblings of
    `type` — which is the one structural difference from the local server's nested
    `response_format.json_schema` and the reason a shared transport was rejected.
    """
    provider, calls = provider_on(answering(), model_id="gpt-4.1-mini")
    generate(provider)

    assert calls[0].url.path == "/v1/responses"
    assert json.loads(calls[0].content) == {
        "model": "gpt-4.1-mini",
        "input": [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": PROMPT},
        ],
        "text": {"format": {"type": "json_schema", "name": "story_editorial_plan",
                            "strict": True, "schema": PLAN_SCHEMA}},
        "max_output_tokens": 2048,
        "store": False,
        "temperature": 0.0,
    }


def test_the_writer_request_omits_the_temperature_the_default_model_refuses():
    """`gpt-5-nano` answers `temperature: 0.0` with HTTP 400 `Unsupported parameter` (measured
    2026-08-19), so the capability is declared per model and the key is simply absent — not
    sent as `null`, which is a value, and not discovered by spending a 400 per run.

    `reasoning.effort` is present for the same reason in reverse: the model declares it, so it
    is sent, and the manifest can state a setting that really reached a wire.
    """
    provider, calls = provider_on(answering(json.dumps({"headline": "h", "body": "b"})))
    generate(provider, system=WRITER_SYSTEM, schema=DRAFT_SCHEMA, schema_name="story_draft",
             max_tokens=1024)

    body = json.loads(calls[0].content)
    assert "temperature" not in body
    assert body["model"] == "gpt-5-nano"
    assert body["reasoning"] == {"effort": "minimal"}
    assert body["max_output_tokens"] == 1024
    assert body["text"]["format"]["name"] == "story_draft"
    assert body["text"]["format"]["schema"] == DRAFT_SCHEMA


def test_no_request_ever_asks_the_provider_to_retain_it():
    """A story request carries the whole evidence package, and this API's default is to keep
    it. There is no configuration key that can turn `store` on."""
    for model_id in ("gpt-5-nano", "gpt-4.1-mini"):
        provider, calls = provider_on(answering(), model_id=model_id)
        generate(provider)
        assert json.loads(calls[0].content)["store"] is False


def test_the_key_travels_as_a_bearer_token_and_appears_in_nothing_else():
    provider, calls = provider_on(answering())
    result = generate(provider)

    assert calls[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in repr(provider.config)
    assert FAKE_KEY not in json.dumps(dict(result.metadata))
    assert FAKE_KEY not in repr(result)


@pytest.mark.parametrize("schema_name", ["story_editorial_plan", "story_draft",
                                         "story_advisory_verification"])
def test_the_schema_name_the_caller_gave_reaches_the_wire(schema_name):
    provider, calls = provider_on(answering())
    generate(provider, schema_name=schema_name)
    assert json.loads(calls[0].content)["text"]["format"]["name"] == schema_name


@pytest.mark.parametrize("field,value", [("system", "  "), ("prompt", ""),
                                         ("schema_name", " ")])
def test_a_request_missing_a_digest_input_is_refused_before_anything_is_sent(field, value):
    provider, calls = provider_on(refusing)
    with pytest.raises(StoryProviderConfigurationError):
        generate(provider, **{field: value})
    assert calls == []


def test_a_non_portable_schema_is_refused_here_rather_than_by_a_four_hundred():
    """Measured: a strict schema missing `additionalProperties: false` comes back **400
    `invalid_json_schema`, param `text.format.schema`**. `validate_portable_schema` already
    refuses it for llama.cpp's very different reason, so the request is never built."""
    schema = {"type": "object", "properties": {"thesis": {"type": "string"}},
              "required": ["thesis"]}
    provider, calls = provider_on(refusing)
    with pytest.raises(StoryProviderConfigurationError) as raised:
        generate(provider, schema=schema)
    assert "additionalProperties" in str(raised.value)
    assert calls == []


# -- one schema, two providers ------------------------------------------------------------------


@pytest.mark.parametrize("schema", [PLAN_SCHEMA, DRAFT_SCHEMA], ids=["plan", "draft"])
def test_the_identical_schema_object_reaches_both_wires_unmodified(schema):
    """The claim §2 makes about the portable subset, executed rather than argued.

    Both adapters are given the *same* dict, both validate it with the same function, and the
    object each puts on its wire compares equal to the original and to the other's. An
    OpenAI-specific schema path would show up here as a difference.
    """
    assert validate_portable_schema(schema) is None

    remote, remote_calls = provider_on(answering(), model_id="gpt-4.1-mini")
    remote.request_body(system=PLANNER_SYSTEM, prompt=PROMPT, schema=schema,
                        schema_name="story_editorial_plan", max_tokens=2048)
    local = StoryOpenAICompatibleProvider(
        StoryProviderConfig(), client=httpx.Client(transport=httpx.MockTransport(refusing)))
    try:
        local_body = local.request_body(system=PLANNER_SYSTEM, prompt=PROMPT, schema=schema,
                                        schema_name="story_editorial_plan", max_tokens=2048)
    finally:
        local.close()
    remote_body = remote.request_body(system=PLANNER_SYSTEM, prompt=PROMPT, schema=schema,
                                      schema_name="story_editorial_plan", max_tokens=2048)

    assert remote_calls == []
    assert remote_body["text"]["format"]["schema"] == schema
    assert local_body["response_format"]["json_schema"]["schema"] == schema
    assert remote_body["text"]["format"]["schema"] == (
        local_body["response_format"]["json_schema"]["schema"])


def test_one_answer_is_checked_against_one_schema_by_one_function():
    """`schema_violations` is not re-implemented per provider: the same answer through both
    adapters produces the same verdict, so a violation means one thing in either store."""
    wrong = dict(CONFORMANT_PLAN, audience="shareholders")
    assert schema_violations(CONFORMANT_PLAN, PLAN_SCHEMA) == []

    remote, _ = provider_on(answering(wrong))
    with pytest.raises(StoryProviderSchemaError) as from_openai:
        generate(remote)
    assert from_openai.value.violations == tuple(schema_violations(wrong, PLAN_SCHEMA))


# -- the missing key, which must cost nothing ----------------------------------------------------


def test_with_no_key_the_catalogue_reports_the_provider_unavailable_with_a_reason():
    options = {option.provider_id: option
               for option in provider_catalogue(env_path=NO_ENV_FILE, environ={})}
    openai = options[PROVIDER_OPENAI]

    assert openai.available is False
    assert openai.unavailable_reason == OPENAI_KEY_MISSING_REASON
    assert ENV_OPENAI_API_KEY in openai.unavailable_reason
    # The models are still listed: "OpenAI is missing" and "OpenAI does not exist" are
    # different facts, and an interface that could not tell them apart would show neither.
    assert [model.model_id for model in openai.models] == ["gpt-5-nano", "gpt-4.1-mini"]
    assert options[PROVIDER_LOCAL].available is True


def test_with_no_key_construction_raises_and_no_request_is_issued():
    """A raise at construction rather than at the first request: by then a package has been
    composed and a prompt assembled, and a missing key would be indistinguishable from a 401."""
    config = openai_config(key=None)
    assert config.api_key is None

    with pytest.raises(StoryProviderConfigurationError) as raised:
        StoryOpenAIResponsesProvider(
            config, client=httpx.Client(transport=httpx.MockTransport(refusing)))
    assert ENV_OPENAI_API_KEY in str(raised.value)


def test_an_empty_key_is_an_absent_key_rather_than_an_override_to_empty():
    """The shape a shell profile leaves behind. §4.3 makes availability turn on a *non-empty*
    key, so `OPENAI_API_KEY=` must not produce an `Authorization: Bearer ` header."""
    config = openai_config(key="   ")
    assert config.api_key is None
    with pytest.raises(StoryProviderConfigurationError):
        StoryOpenAIResponsesProvider(config)


def test_a_config_for_the_other_provider_is_refused_by_this_adapter():
    with pytest.raises(StoryProviderConfigurationError) as raised:
        StoryOpenAIResponsesProvider(StoryProviderConfig())
    assert PROVIDER_OPENAI in str(raised.value)


# -- the key appears nowhere a person can read ---------------------------------------------------


def test_a_four_hundred_and_one_that_echoes_the_key_back_is_redacted():
    """**Measured, not hypothetical.** A real 401 body reads `Incorrect API key provided:
    sk-obvio**********alid` — the first eight characters of the key in clear. Quoting
    `response.text` the way the local adapter safely can would put that in a traceback.
    """
    echo = api_error("invalid_api_key",
                     f"Incorrect API key provided: {FAKE_KEY[:8]}**********{FAKE_KEY[-4:]}. "
                     "You can find your API key at https://platform.openai.com/account/api-keys.")
    provider, calls = provider_on(lambda request: httpx.Response(401, json=echo))

    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    message = str(raised.value)
    assert len(calls) == 1, "401 is not retryable"
    assert FAKE_KEY not in message
    assert FAKE_KEY[:8] not in message
    assert "invalid_api_key" in message


def test_a_transport_error_quoting_the_key_is_redacted():
    def leaky(request):
        raise httpx.ReadError(f"TLS handshake failed for Bearer {FAKE_KEY}")

    provider, _ = provider_on(leaky, max_retries=0)
    with pytest.raises(StoryProviderTransportError) as raised:
        generate(provider)
    assert FAKE_KEY not in str(raised.value)


def test_health_never_carries_the_key_into_its_detail():
    def leaky(request):
        raise httpx.ConnectError(f"cannot connect with {FAKE_KEY}")

    provider, _ = provider_on(leaky)
    status = provider.health()
    assert status.ok is False
    assert FAKE_KEY not in (status.detail or "")


# -- §4.5's error mapping, row by row -------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUSES))
def test_a_retryable_status_is_retried_and_a_later_success_is_returned(status):
    responses = [httpx.Response(status, json=api_error("rate_limit_exceeded", "slow down")),
                 httpx.Response(200, json=envelope(json.dumps(CONFORMANT_PLAN)))]
    provider, calls = provider_on(lambda request: responses.pop(0))
    result = generate(provider)
    assert (result.attempts, len(calls)) == (2, 2)
    assert result.content == CONFORMANT_PLAN


def test_a_retryable_status_surviving_the_bound_is_a_transport_error_with_the_local_backoff():
    slept: list[float] = []
    provider, calls = provider_on(
        lambda request: httpx.Response(429, json=api_error("rate_limit_exceeded", "slow down")),
        sleeps=slept, max_retries=2)
    with pytest.raises(StoryProviderTransportError):
        generate(provider)
    assert len(calls) == 3, "max_retries counts attempts after the first"
    assert slept == [0.25, 0.5]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_a_non_retryable_status_raises_a_response_error_immediately(status_code):
    provider, calls = provider_on(
        lambda request: httpx.Response(status_code, json=api_error("invalid_json_schema",
                                                                   "schema is not strict")),
        max_retries=2)
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert len(calls) == 1
    # The API states the cause in a field; a truncated body would cut it off exactly there.
    assert "invalid_json_schema" in str(raised.value)


def test_a_refused_connection_reports_unavailable_rather_than_a_generic_fault():
    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    provider, _ = provider_on(refused)
    with pytest.raises(StoryProviderUnavailable):
        generate(provider)


def test_a_timeout_is_never_retried():
    """A story candidate makes three requests against a 180-second budget; retrying a timeout
    turns one slow request into a stall the caller cannot see the end of."""
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    provider, calls = provider_on(slow, max_retries=2)
    with pytest.raises(StoryProviderTimeout):
        generate(provider)
    assert len(calls) == 1


def test_an_incomplete_response_names_the_reason_the_api_gave():
    """Measured: a budget too small for the reasoning pass returns **200** with
    `status: incomplete` and an `output` holding a reasoning item and no message at all."""
    provider, _ = provider_on(lambda request: httpx.Response(200, json=envelope(
        "", status="incomplete", incomplete_details={"reason": "max_output_tokens"},
        output=[{"id": "rs_1", "type": "reasoning", "summary": []}])))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "incomplete" in str(raised.value) and "max_output_tokens" in str(raised.value)


def test_a_refusal_is_a_response_error_and_not_a_schema_error():
    """Nothing was returned to check against the schema, so calling it a schema violation would
    tell a reader the model answered badly when it declined to answer."""
    body = envelope("")
    body["output"][-1]["content"] = [{"type": "refusal", "refusal": "I cannot help with that."}]
    provider, _ = provider_on(lambda request: httpx.Response(200, json=body))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "refused" in str(raised.value)


def test_a_response_carrying_a_top_level_error_is_a_response_error():
    provider, _ = provider_on(lambda request: httpx.Response(
        200, json=dict(envelope(""), error={"code": "server_error", "message": "boom"})))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "server_error" in str(raised.value)


def test_a_body_that_is_not_json_raises_a_response_error():
    provider, _ = provider_on(lambda request: httpx.Response(200, text="<html>502</html>"))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "not JSON" in str(raised.value)


def test_an_output_list_with_no_message_item_raises_and_says_what_it_held():
    provider, _ = provider_on(lambda request: httpx.Response(200, json=envelope(
        "", output=[{"id": "rs_1", "type": "reasoning", "summary": []}])))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "reasoning" in str(raised.value)


def test_blank_output_text_raises_rather_than_returning_an_empty_object():
    provider, _ = provider_on(answering(""))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "no output text" in str(raised.value)


def test_output_text_that_is_not_json_raises_rather_than_returning_an_empty_object():
    """`{}` is a legitimate answer for some schemas. Returning one here would make an
    unparseable response indistinguishable from a real, empty one."""
    provider, _ = provider_on(answering('{"thesis": "Adjusted EBITDA'))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "not JSON" in str(raised.value)


def test_output_text_that_is_not_an_object_raises_a_response_error():
    provider, _ = provider_on(answering('["thesis"]'))
    with pytest.raises(StoryProviderResponseError) as raised:
        generate(provider)
    assert "not an object" in str(raised.value)


def test_a_schema_violation_is_never_retried():
    provider, calls = provider_on(answering(dict(CONFORMANT_PLAN, audience="shareholders")),
                                  max_retries=2)
    with pytest.raises(StoryProviderSchemaError) as raised:
        generate(provider)
    assert len(calls) == 1
    assert "audience" in raised.value.violations[0]


# -- what crosses the boundary --------------------------------------------------------------------


def test_usage_is_translated_onto_the_names_the_rest_of_the_system_uses():
    """`input_tokens`/`output_tokens` are this API's words for the same two numbers. Translating
    them here is what makes `token_totals` in a manifest mean one thing whichever provider
    filled it. `reasoning_tokens` is billed and invisible inside `output_tokens`, so a manifest
    without it under-reports what a reasoning run spent."""
    provider, _ = provider_on(answering())
    result = generate(provider)

    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (110, 41, 151)
    assert result.metadata["reasoning_tokens"] == 17
    assert result.metadata["cached_tokens"] == 32


def test_the_configured_name_and_the_dated_one_are_both_recorded():
    """The split the local adapter makes between the configured model and the `.gguf` path the
    server reports. Here the wire value is the dated id, which moves on its own when OpenAI
    reroutes an alias — exactly what `provider_model_id` is for."""
    provider, _ = provider_on(answering())
    result = generate(provider)

    assert provider.model_id == "gpt-5-nano"
    assert result.model_id == "gpt-5-nano-2025-08-07"
    assert result.metadata["configured_model"] == "gpt-5-nano"
    assert result.metadata["schema_name"] == "story_editorial_plan"


def test_the_result_says_whether_a_temperature_was_sent_rather_than_leaving_it_to_be_assumed():
    """Plan §1's finding F3. A manifest recording `temperature: 0.0` for a request that never
    carried one would be a claim about a value nothing sent."""
    nano, _ = provider_on(answering())
    reasoning_result = generate(nano)
    assert reasoning_result.metadata["temperature_sent"] is False
    assert reasoning_result.metadata["reasoning_effort"] == "minimal"

    mini, _ = provider_on(answering(model="gpt-4.1-mini-2025-04-14"), model_id="gpt-4.1-mini")
    pinned_result = generate(mini, temperature=PINNED_TEMPERATURE)
    assert pinned_result.metadata["temperature_sent"] is True
    assert pinned_result.metadata["reasoning_effort"] is None
    assert pinned_result.metadata["store_responses"] is False


def test_the_finish_reason_is_the_status_the_api_gave_and_not_an_invented_stop():
    """There is no `finish_reason` on this wire. `"stop"` would be a value this API never sends,
    borrowed from the other adapter to make two records look alike."""
    provider, _ = provider_on(answering())
    result = generate(provider)
    assert result.finish_reason == "completed"


def test_the_content_digest_ignores_the_unstable_envelope():
    bodies = [envelope(json.dumps(CONFORMANT_PLAN)), envelope(json.dumps(CONFORMANT_PLAN))]
    bodies[1]["id"] = "resp_different"
    bodies[1]["created_at"] = 1785599999
    provider, _ = provider_on(lambda request: httpx.Response(200, json=bodies.pop(0)))
    first, second = generate(provider), generate(provider)
    assert first.content_sha256 == second.content_sha256
    assert first.raw_sha256 != second.raw_sha256


def test_the_result_carries_no_vendor_object():
    provider, _ = provider_on(answering())
    result = generate(provider)

    assert isinstance(result, GenerationResult)
    allowed = (str, int, float, bool, dict, tuple, list, type(None))
    for name in type(result).model_fields:
        assert isinstance(getattr(result, name), allowed), name
    assert type(result.content) is dict
    assert "httpx" not in repr(result)


def test_the_reasoning_item_never_becomes_content():
    """The verifier reads `content` and nothing else, and a reasoning trace is prose the
    deterministic layer has no way to check."""
    provider, _ = provider_on(answering())
    result = generate(provider)
    assert result.content == CONFORMANT_PLAN
    assert result.raw_content == json.dumps(CONFORMANT_PLAN)


def test_the_provider_is_usable_through_the_story_generation_provider_protocol():
    """Driven, not `isinstance`-checked."""
    provider, _ = provider_on(answering())
    port: StoryGenerationProvider = provider
    result = port.generate(system=WRITER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                           schema_name="story_draft", max_tokens=2048,
                           temperature=PINNED_TEMPERATURE)
    assert result.content["audience"] == "external"
    assert port.provider_id == PROVIDER_OPENAI


def test_the_two_adapters_report_different_provider_ids():
    """The property S12 added to the protocol, and the reason it exists: a local server answers
    to any model string, so without this the two would be one row in the replay store."""
    remote, _ = provider_on(answering())
    local = StoryOpenAICompatibleProvider(
        StoryProviderConfig(), client=httpx.Client(transport=httpx.MockTransport(refusing)))
    try:
        assert (local.provider_id, remote.provider_id) == (PROVIDER_LOCAL, PROVIDER_OPENAI)
    finally:
        local.close()


# -- health ---------------------------------------------------------------------------------------


def test_health_asks_the_cheapest_question_that_proves_the_key_and_the_endpoint():
    seen: list[str] = []

    def models(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-5-nano"}]})

    provider, _ = provider_on(models)
    status = provider.health()
    assert status.ok and status.status == "ok"
    assert seen == ["/v1/models"]


def test_health_reports_a_rejected_key_as_a_status_rather_than_raising():
    """"The API is down" and "this key is wrong" are different facts a caller branches on
    differently, and a caller that had to catch an exception to learn either is one that
    forgets to."""
    provider, _ = provider_on(lambda request: httpx.Response(
        401, json=api_error("invalid_api_key", "Incorrect API key provided: sk-abc***xyz")))
    status = provider.health()
    assert status.ok is False and status.status == "unauthorized"
    assert "sk-abc" not in (status.detail or "")


def test_health_on_a_closed_port_reports_unhealthy_rather_than_raising():
    config = openai_config()
    provider = StoryOpenAIResponsesProvider(
        StoryProviderConfig(**{**vars(config),
                              "base_url": f"http://127.0.0.1:{free_port()}",
                              "timeout_seconds": 2}))
    try:
        status = provider.health()
    finally:
        provider.close()
    assert status.ok is False
    assert status.status in ("unavailable", "timeout")


# -- the registry -----------------------------------------------------------------------------


def test_the_catalogue_carries_no_url_no_key_no_environment_value_and_no_timeout():
    """§6's rule, executed against the rendered payload rather than the dataclass: what the
    browser is allowed to know is a provider label, a model id and two capabilities."""
    rendered = json.dumps([option.as_dict() for option in provider_catalogue(
        env_path=NO_ENV_FILE, environ={ENV_OPENAI_API_KEY: FAKE_KEY})])

    assert FAKE_KEY not in rendered
    assert "api.openai.com" not in rendered and "127.0.0.1" not in rendered
    assert "base_url" not in rendered and "timeout" not in rendered
    for option in provider_catalogue(env_path=NO_ENV_FILE,
                                     environ={ENV_OPENAI_API_KEY: FAKE_KEY}):
        assert set(option.as_dict()) == {"provider_id", "label", "available",
                                         "unavailable_reason", "models", "default_model_id"}
        for model in option.as_dict()["models"]:
            assert set(model) == {"model_id", "label", "supports_temperature",
                                  "reasoning_effort"}


def test_with_a_key_the_catalogue_reports_openai_available_and_names_no_reason():
    options = {option.provider_id: option for option in provider_catalogue(
        env_path=NO_ENV_FILE, environ={ENV_OPENAI_API_KEY: FAKE_KEY})}
    openai = options[PROVIDER_OPENAI]

    assert openai.available is True
    assert openai.unavailable_reason == ""
    assert openai.default_model_id == "gpt-5-nano"
    assert openai.label == "OpenAI"


def test_the_catalogue_states_each_models_measured_temperature_capability():
    models = {model.model_id: model for option in provider_catalogue(
        env_path=NO_ENV_FILE, environ={ENV_OPENAI_API_KEY: FAKE_KEY})
        for model in option.models if option.provider_id == PROVIDER_OPENAI}

    assert models["gpt-5-nano"].supports_temperature is False
    assert models["gpt-5-nano"].reasoning_effort == "minimal"
    assert models["gpt-4.1-mini"].supports_temperature is True
    assert models["gpt-4.1-mini"].reasoning_effort is None


def test_the_catalogue_never_raises_on_a_configuration_it_cannot_build():
    """An unconfigured provider is an ordinary state for an interface to render. A catalogue
    that threw would leave the page with nothing to say about the provider that *is* usable."""
    broken = {"provider": {"openai": {"models": [{"supports_temperature": True}]}}}
    options = {option.provider_id: option
               for option in provider_catalogue(broken, env_path=NO_ENV_FILE, environ={})}

    assert options[PROVIDER_OPENAI].available is False
    assert "id" in options[PROVIDER_OPENAI].unavailable_reason
    assert options[PROVIDER_LOCAL].available is True


# -- configuration ----------------------------------------------------------------------------


def test_the_loader_called_as_it_always_was_still_resolves_the_local_server():
    """The hard compatibility requirement: one positional mapping, no keywords, and the CLI,
    the demo API and every pre-S12 test keep their behaviour with no edit."""
    config = load_provider_config(env_path=NO_ENV_FILE, environ={})
    assert config.kind == PROVIDER_LOCAL == config.provider_id
    assert config.base_url == "http://127.0.0.1:8080"
    assert (config.supports_temperature, config.reasoning_effort) == (True, None)
    assert config.store_responses is False


def test_an_openai_key_in_the_environment_never_reaches_the_local_configuration():
    config = load_provider_config(env_path=NO_ENV_FILE,
                                  environ={ENV_OPENAI_API_KEY: FAKE_KEY})
    assert config.api_key is None
    assert config.authorization_headers == {}


def test_a_secret_in_the_nested_openai_block_is_refused_rather_than_read():
    """The file is tracked. The rule already written for the top-level block, extended to the
    one a reader would most naturally put a key in."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        load_provider_config({"provider": {"openai": {"api_key": "sk-committed"}}},
                             env_path=NO_ENV_FILE, environ={})
    assert ENV_OPENAI_API_KEY in str(raised.value)


def test_the_environment_can_override_the_configured_default_model():
    config = openai_config(**{ENV_OPENAI_MODEL: "gpt-4.1-mini"})
    assert config.model == "gpt-4.1-mini"
    assert config.supports_temperature is True


def test_an_explicit_selection_outranks_the_environment():
    """The environment is an operator's default for a machine; the argument is a selection a
    person made from a catalogue this server itself offered."""
    config = openai_config(model_id="gpt-5-nano", **{ENV_OPENAI_MODEL: "gpt-4.1-mini"})
    assert config.model == "gpt-5-nano"


def test_a_model_nobody_declared_is_refused_rather_than_passed_through():
    """Its answer to "may this request carry a temperature?" has never been measured, and
    discovering it costs a failed request per run."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        openai_config(model_id="gpt-4o-mini")
    assert "gpt-4o-mini" in str(raised.value)


@pytest.mark.parametrize("field,value", [
    ("reasoning_effort", "minimal"),
    ("store_responses", True),
    ("supports_temperature", False),
])
def test_a_setting_the_local_transport_cannot_honour_is_refused_rather_than_ignored(field, value):
    """The `kind`-dispatch discipline `public.py` already argues for. A `reasoning_effort` the
    local server drops is the same defect as a schema keyword llama.cpp drops: it looks
    configured, it is not, and the manifest would record it as having been sent."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        StoryProviderConfig(**{field: value}).validated()
    assert PROVIDER_LOCAL in str(raised.value)


def test_a_misspelt_reasoning_effort_is_refused_before_a_package_is_ever_composed():
    with pytest.raises(StoryProviderConfigurationError) as raised:
        load_provider_config(
            {"provider": {"openai": {"models": [
                {"id": "gpt-5-nano", "supports_temperature": False,
                 "reasoning_effort": "lowest"}]}}},
            provider_id=PROVIDER_OPENAI, env_path=NO_ENV_FILE, environ={})
    assert "lowest" in str(raised.value)


def test_the_committed_configuration_resolves_both_providers():
    """The guard on the guards: every assertion above is about defaults, and this is the file a
    run really reads."""
    import yaml

    raw = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "story.yaml").read_text(
            encoding="utf-8"))
    local = load_provider_config(raw, env_path=NO_ENV_FILE, environ={})
    openai = load_provider_config(raw, provider_id=PROVIDER_OPENAI, env_path=NO_ENV_FILE,
                                  environ={ENV_OPENAI_API_KEY: FAKE_KEY})

    assert local.provider_id == PROVIDER_LOCAL and local.model == "Qwen3.5-9B-Q4_K_M.gguf"
    assert openai.provider_id == PROVIDER_OPENAI and openai.model == "gpt-5-nano"
    assert openai.base_url == "https://api.openai.com/v1"
    assert openai.supports_temperature is False and openai.reasoning_effort == "minimal"
    assert "api_key" not in raw["provider"] and "api_key" not in raw["provider"]["openai"]
