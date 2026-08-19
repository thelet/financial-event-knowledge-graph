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
    # **Was `"id" in …` until 2026-08-19, and that passed by accident**: the substring occurs in
    # the sentence's ordinary prose, so it proved nothing about the reason naming anything. The
    # reason names the *setting*, which is the only thing it is now allowed to name — see
    # `UNUSABLE_CONFIG_REASON` and the leak that produced it.
    assert options[PROVIDER_OPENAI].unavailable_reason.startswith("provider.openai.models")
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


# -- what a browser may be told about a configuration that did not resolve -----------------------
#
# Every test in this section was written against a reproduction. Before the fix each of them
# found the offending *value* inside `ProviderOption.unavailable_reason`, which `app.js` renders
# both as the option's label and as a line in `#provider-notice`.


def catalogue_of(environ: dict, config=None) -> dict:
    return {option.provider_id: option
            for option in provider_catalogue(config, env_path=NO_ENV_FILE, environ=environ)}


def test_an_undeclared_model_name_from_the_environment_never_reaches_the_catalogue():
    """`STORY_OPENAI_MODEL`'s **value** in a browser, reproduced 2026-08-19.

    The catalogue rendered `str(exc)` and the refusal quotes the model it could not find, so
    `STORY_OPENAI_MODEL=internal-codename-orion-7` came back as *"provider.openai model
    'internal-codename-orion-7' is not one of […]"* in the option label and in the notice — a
    name an operator may not have published, carried out of their environment by a payload whose
    own docstring forbids exactly that.

    The exception is unchanged and still names it. The boundary is what moved.
    """
    secret_model = "internal-codename-orion-7"
    option = catalogue_of({ENV_OPENAI_API_KEY: FAKE_KEY,
                           ENV_OPENAI_MODEL: secret_model})[PROVIDER_OPENAI]

    assert option.available is False
    assert secret_model not in json.dumps(option.as_dict())
    # The *setting* is named, because "OpenAI is misconfigured" and "OpenAI does not exist" are
    # still different facts and the interface still has to tell them apart.
    assert option.unavailable_reason.startswith("provider.openai.models")
    # And the operator's half is intact: the raise still quotes the value.
    with pytest.raises(StoryProviderConfigurationError) as raised:
        load_provider_config(provider_id=PROVIDER_OPENAI, env_path=NO_ENV_FILE,
                             environ={ENV_OPENAI_API_KEY: FAKE_KEY,
                                      ENV_OPENAI_MODEL: secret_model})
    assert secret_model in str(raised.value)


def test_an_unreadable_environment_number_never_reaches_the_catalogue():
    """The same leak on the *local* provider and through a different raise site.

    `STORY_LLM_CONTEXT_TOKENS=many` raised "must be an integer, got 'many'", and that string was
    the local option's whole `unavailable_reason`. Any `STORY_LLM_*` value could ride out this
    way; the fix is at the boundary rather than at the four messages, which is why this asserts
    on a second raise site than the test above.
    """
    marker = "not-a-number-BUT-A-SECRET"
    option = catalogue_of({"STORY_LLM_CONTEXT_TOKENS": marker})[PROVIDER_LOCAL]

    assert option.available is False
    assert marker not in json.dumps(option.as_dict())
    assert option.unavailable_reason.startswith("STORY_LLM_CONTEXT_TOKENS")


def test_no_reason_the_catalogue_can_produce_carries_a_configured_value():
    """The general form, across every refusal reachable from a configuration.

    A per-case test would only ever cover the raise sites somebody thought of. This drives six
    shapes through the catalogue and asserts the payload holds none of the six needles — which
    is the property `ProviderOption`'s docstring claims and did not have.
    """
    needles = {
        "model": ({ENV_OPENAI_API_KEY: FAKE_KEY, ENV_OPENAI_MODEL: "NEEDLE-model"}, None),
        "int": ({"STORY_LLM_MAX_RETRIES": "NEEDLE-retries"}, None),
        "float": ({"STORY_LLM_TIMEOUT_SECONDS": "NEEDLE-timeout"}, None),
        "url": ({"STORY_LLM_BASE_URL": ""}, None),
        "effort": ({}, {"provider": {"reasoning_effort": "NEEDLE-effort"}}),
        "models": ({}, {"provider": {"openai": {"models": "NEEDLE-models"}}}),
    }
    for name, (environ, config) in needles.items():
        rendered = json.dumps([option.as_dict()
                               for option in catalogue_of(environ, config).values()])
        assert "NEEDLE" not in rendered, f"{name} leaked a configured value into the catalogue"


# -- a key that cannot become a header ------------------------------------------------------------


#: Reproduced by an adversarial review 2026-08-19. Each one used to escape the error taxonomy:
#: `httpx` encodes a header value as ASCII, so the first three raise `UnicodeEncodeError` from
#: inside `client.post`/`client.get` — neither `httpx.TimeoutException` nor `httpx.HTTPError` —
#: and `UnicodeEncodeError.args[1]` is the whole key. The fourth reached h11, which reports the
#: **bytes repr** of the header, escaping the newline past `_redacted`'s exact-substring half.
UNUSABLE_KEYS = [
    "kľúč-SECRET-9999",
    "sk-LEAKME-ари-0004",
    "sk-em—dash-SECRET",
    "GLORP_TOPSECRET_2026\nX: 1",
]


@pytest.mark.parametrize("key", UNUSABLE_KEYS)
def test_a_key_that_cannot_be_a_header_is_refused_when_it_is_loaded(key):
    with pytest.raises(StoryProviderConfigurationError) as raised:
        load_provider_config(provider_id=PROVIDER_OPENAI, env_path=NO_ENV_FILE,
                             environ={ENV_OPENAI_API_KEY: key})
    message = str(raised.value)
    assert ENV_OPENAI_API_KEY in message
    # No part of the key, not even the offending character: this is the one refusal in the
    # module whose message may not quote what it refused.
    for fragment in ("SECRET", "LEAKME", "GLORP", "TOPSECRET", "kľúč", "ари"):
        assert fragment not in message


@pytest.mark.parametrize("key", UNUSABLE_KEYS)
def test_a_key_that_cannot_be_a_header_never_reaches_a_request_or_a_health_probe(key):
    """The two escapes, closed at construction.

    `generate()` raised outside its six classes and `health()` raised at all — against a
    docstring that says it never does. Both are unreachable now because the provider cannot be
    built, which is the same argument §4.3 already makes for an absent key.
    """
    from pydantic import SecretStr

    config = StoryProviderConfig(kind=PROVIDER_OPENAI, base_url="https://api.openai.com/v1",
                                 model="gpt-5-nano", context_tokens=128000,
                                 max_output_tokens=4096, supports_temperature=False,
                                 reasoning_effort="minimal", api_key=SecretStr(key))
    with pytest.raises(StoryProviderConfigurationError):
        StoryOpenAIResponsesProvider(
            config, client=httpx.Client(transport=httpx.MockTransport(refusing)))


def test_a_usable_key_is_still_accepted_whole():
    """The guard on the guard: the refusal is a character class, not a length or a prefix."""
    config = openai_config(key="sk-proj-Abc_123-XYZ*")
    assert config.authorization_headers == {"Authorization": "Bearer sk-proj-Abc_123-XYZ*"}


def test_the_redaction_also_covers_the_escaped_spelling_of_a_key():
    """Defence in depth, and only that — see `_redacted`'s docstring for why it cannot be more.

    The needle is a **legal** key: printable ASCII throughout, so the refusal above does not
    reach it, and holding no `sk-` prefix, so the pattern half cannot save it either. A
    backslash in it is enough — h11 and `repr` both render one as two, and the exact-substring
    half of `_redacted` then matches nothing. That is the same escaping that let a
    control-character key out in full; the difference is that this one is a key an operator may
    really hold, which is why the one-line replacement earns its place after the refusal.
    """
    leaky = "GLORP\\TOPSECRET_2026"
    provider, _ = provider_on(answering(), key=leaky)
    try:
        # What h11 and `repr` render that header as: the one backslash becomes two.
        escaped = leaky.encode("unicode_escape").decode("ascii")
        assert escaped != leaky, "the needle must actually be escaped, or this proves nothing"
        message = provider._redacted(f"Illegal header value b'Bearer {escaped}'")
        assert "GLORP" not in message and "TOPSECRET" not in message
    finally:
        provider.close()


# -- `provider.openai.models`, dispatched on rather than defaulted --------------------------------


def test_an_empty_model_list_takes_openai_out_of_the_build_rather_than_restoring_the_defaults():
    """`models: []` used to hand back `['gpt-5-nano', 'gpt-4.1-mini']`.

    An operator who empties the list has said something; the loader answered with two models
    they did not declare and no indication that anything had been ignored.
    """
    from story.providers.public import openai_model_options

    assert openai_model_options({"openai": {"models": []}}) == ()
    option = catalogue_of({ENV_OPENAI_API_KEY: FAKE_KEY},
                          {"provider": {"openai": {"models": []}}})[PROVIDER_OPENAI]
    assert option.available is False
    assert option.models == ()


def test_a_models_key_that_is_not_a_list_is_refused_rather_than_replaced_by_the_defaults():
    """`models: "x"` used to resolve to the built-in pair, so a typo produced a configuration
    that worked and was not the one written down."""
    from story.providers.public import openai_model_options

    with pytest.raises(StoryProviderConfigurationError) as raised:
        openai_model_options({"openai": {"models": "gpt-5-nano"}})
    assert "must be a list" in str(raised.value)


def test_an_absent_models_key_is_the_one_case_that_reaches_the_measured_defaults():
    """The case the constant exists for: a caller with no configuration file at all."""
    from story.providers.public import openai_model_options

    assert [option.model_id for option in openai_model_options({})] == [
        "gpt-5-nano", "gpt-4.1-mini"]


# -- the local refusals, reachable from a file ----------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("reasoning_effort", "high"),
    ("supports_temperature", False),
    ("store_responses", True),
])
def test_a_local_setting_that_means_nothing_is_refused_from_a_configuration_file(field, value):
    """`validated()`'s local refusals could not fire from `config/story.yaml`.

    Verified before the fix: `load_provider_config({"provider": {"reasoning_effort": "high"}})`
    returned `reasoning_effort=None`. `from_config`'s local branch never read the three S12
    fields, so the value was **silently dropped** and the refusal beside it — whose comment says
    it exists to catch a setting that "looks configured and is not" — could only be reached from
    a hand-built dataclass. Which is the failure it describes, in the code written to prevent it.
    """
    with pytest.raises(StoryProviderConfigurationError) as raised:
        load_provider_config({"provider": {field: value}}, env_path=NO_ENV_FILE, environ={})
    assert PROVIDER_LOCAL in str(raised.value)


def test_a_local_setting_that_is_neither_true_nor_false_is_refused_rather_than_coerced():
    """Never `bool(value)`: `bool("false")` is `True`, and this key decides whether the pinned
    temperature reaches the wire at all."""
    with pytest.raises(StoryProviderConfigurationError) as raised:
        load_provider_config({"provider": {"supports_temperature": "false"}},
                             env_path=NO_ENV_FILE, environ={})
    assert "true or false" in str(raised.value)


# -- `provider.default` ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [{"a": 1}, ["openai"], 7])
def test_a_provider_default_that_is_not_a_provider_id_is_refused_rather_than_stringified(value):
    """`default: {a: 1}` reached `GET /demo/providers` as `default_provider_id: "{'a': 1}"`.

    It failed safe further down, which is luck rather than design, and the payload was nonsense
    either way. This module dispatches on `kind` rather than defaulting; the key that chooses
    which `kind` is reached for gets the same treatment.
    """
    from story.providers.public import default_provider_id

    with pytest.raises(StoryProviderConfigurationError) as raised:
        default_provider_id({"provider": {"default": value}})
    assert "provider.default" in str(raised.value)


def test_a_string_provider_default_is_still_read_and_a_missing_one_still_falls_back():
    from story.providers.public import default_provider_id

    assert default_provider_id({"provider": {"default": PROVIDER_OPENAI}}) == PROVIDER_OPENAI
    assert default_provider_id({"provider": {"kind": PROVIDER_LOCAL}}) == PROVIDER_LOCAL
    assert default_provider_id({}) == PROVIDER_LOCAL


# -- a response body bounded by what the request asked for ----------------------------------------


def test_a_response_far_past_its_own_budget_is_refused_rather_than_parsed_and_stored():
    """An 8 MB `output_text` was parsed, schema-checked and returned without complaint.

    Pre-existing, and S12 is what makes it matter: before a second provider the only endpoint
    was a process on loopback. The bound is `response_byte_ceiling(max_output_tokens)` — the
    request's own budget is the only number that says how much text was invited.
    """
    from story.providers.public import response_byte_ceiling

    config = openai_config()
    ceiling = response_byte_ceiling(config.max_output_tokens)
    huge = dict(CONFORMANT_PLAN, thesis="x" * (ceiling + 1))
    provider, calls = provider_on(answering(huge), config=config)
    try:
        with pytest.raises(StoryProviderResponseError) as raised:
            generate(provider)
    finally:
        provider.close()
    assert "ceiling" in str(raised.value)
    assert calls, "the request was issued; it is the response that is refused"


def test_the_local_adapter_bounds_its_response_too():
    """A local server is a process an operator started, not a proof."""
    from story.providers.public import response_byte_ceiling

    local = load_provider_config(env_path=NO_ENV_FILE, environ={})
    ceiling = response_byte_ceiling(local.max_output_tokens)
    body = json.dumps(dict(CONFORMANT_PLAN, thesis="x" * (ceiling + 1)))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"finish_reason": "stop",
                                "message": {"role": "assistant", "content": body}}],
                   "model": local.model, "usage": {}})))
    provider = StoryOpenAICompatibleProvider(local, client=client)
    try:
        with pytest.raises(StoryProviderResponseError) as raised:
            provider.generate(system=PLANNER_SYSTEM, prompt=PROMPT, schema=PLAN_SCHEMA,
                              schema_name="story_editorial_plan", max_tokens=2048)
    finally:
        provider.close()
    assert "ceiling" in str(raised.value)


def test_an_ordinary_answer_is_nowhere_near_the_bound():
    """The guard on the guard: a bound that refused a real response would be a bug of its own.

    The committed planner answer is three orders of magnitude inside it, which is the margin the
    64-bytes-per-token anchor was chosen for.
    """
    from story.providers.public import response_byte_ceiling

    config = openai_config()
    provider, _ = provider_on(answering(), config=config)
    try:
        result = generate(provider)
    finally:
        provider.close()
    assert result.content == CONFORMANT_PLAN
    assert len(result.raw_content) * 100 < response_byte_ceiling(config.max_output_tokens)
