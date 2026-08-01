"""The local generation provider, without a server.

A fake transport is enough to pin the request body, the retry boundary and the error
taxonomy. It is *not* enough to claim the provider works — that is what
`test_provider_live.py` is for, and the two files are separate so this one keeps passing
under `pytest -m "not live"` on a machine with no model at all.

Every response fixture below is the shape the running llama.cpp build actually returns
*(read off the server 2026-08-01, commit `ddd4ec1`, tag `b10217`)*, including the
`prompt_tokens_details` nesting and the `timings` block that make the envelope digest
unstable.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import socket
from pathlib import Path

import httpx
import pytest
import yaml

from extraction.contracts import GenerationProvider
from extraction.providers import (
    GenerationResult,
    LocalOpenAICompatibleGenerationProvider,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderSchemaError,
    ProviderTimeout,
    ProviderTransportError,
    ProviderUnavailable,
    schema_violations,
)

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "extraction"

CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_id": {"type": "string"},
        "value": {"type": "number"},
        "unit": {"type": "string"},
        "period_end": {"type": "string"},
    },
    "required": ["metric_id", "value", "unit", "period_end"],
    "additionalProperties": False,
}

CONFORMANT = {"metric_id": "homes_sold", "value": 2946, "unit": "homes",
              "period_end": "2025-03-31"}


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


def provider_on(handler, **config_overrides):
    """A provider whose transport is `handler` and whose backoff does not sleep."""
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    config = ProviderConfig(**config_overrides)
    client = httpx.Client(transport=httpx.MockTransport(recording))
    return LocalOpenAICompatibleGenerationProvider(
        config, client=client, sleep=lambda _seconds: None), calls


def free_port() -> int:
    """A port nothing is listening on. Bound and released so the number is genuinely unused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- configuration ---------------------------------------------------------------------------


def test_enable_thinking_true_is_rejected_at_load_time():
    """Not a warning. With thinking on, a four-field schema request spent all 900 tokens
    reasoning and returned empty content (LOCAL_RUNTIME_VALIDATED.md §4). A lane whose budget
    failures present as extraction failures is worse than one that cannot run."""
    with pytest.raises(ProviderConfigurationError) as raised:
        ProviderConfig.from_config({"provider": {"enable_thinking": True}})
    assert "enable_thinking" in str(raised.value)


def test_a_hand_built_config_cannot_smuggle_thinking_past_the_constructor():
    """`from_config` is not the only door. Construction validates too."""
    with pytest.raises(ProviderConfigurationError):
        LocalOpenAICompatibleGenerationProvider(ProviderConfig(enable_thinking=True))


def test_the_shipped_config_matches_the_validated_runtime():
    config = ProviderConfig.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))
    assert config.enable_thinking is False
    assert config.kind == "local_openai_compatible"
    assert config.base_url == "http://127.0.0.1:8080"
    assert config.model == "Qwen3.5-9B-Q4_K_M.gguf"
    assert (config.context_tokens, config.max_output_tokens) == (8192, 1024)
    assert (config.temperature, config.timeout_seconds) == (0.0, 120.0)
    assert config.max_retries == 2


def test_an_output_budget_that_leaves_no_room_for_a_prompt_is_rejected():
    with pytest.raises(ProviderConfigurationError):
        ProviderConfig.from_config(
            {"provider": {"context_tokens": 8192, "max_output_tokens": 8192}})


# -- the request body -------------------------------------------------------------------------


def test_the_request_is_non_streaming_and_carries_the_schema_and_thinking_off():
    provider, calls = provider_on(
        lambda request: httpx.Response(200, json=envelope(json.dumps(CONFORMANT))))
    provider.generate(prompt="read this table", schema=CLAIM_SCHEMA)

    body = json.loads(calls[0].content)
    assert calls[0].url.path == "/v1/chat/completions"
    assert body["stream"] is False
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == CLAIM_SCHEMA
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 1024
    assert body["messages"] == [{"role": "user", "content": "read this table"}]


def test_thinking_off_is_sent_even_though_no_config_value_can_turn_it_on():
    """The flag is unconditional on the wire. `ProviderConfig` refuses to hold `true`, so
    there is no path by which any other value reaches the server."""
    provider, _ = provider_on(lambda request: httpx.Response(200, json=envelope("{}")))
    body = provider.request_body(prompt="x", schema={"type": "object"})
    assert body["chat_template_kwargs"]["enable_thinking"] is False


# -- retries: transport only --------------------------------------------------------------------


def test_a_transport_error_retries_to_the_bound_then_raises():
    def fail(request):
        raise httpx.ReadError("connection reset")

    provider, calls = provider_on(fail, max_retries=2)
    with pytest.raises(ProviderTransportError):
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert len(calls) == 3, "max_retries counts attempts after the first"


def test_a_five_hundred_is_retried_and_a_later_success_is_returned():
    responses = [httpx.Response(503, text="loading model"),
                 httpx.Response(200, json=envelope(json.dumps(CONFORMANT)))]
    provider, calls = provider_on(lambda request: responses.pop(0))
    result = provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert result.attempts == 2 and len(calls) == 2
    assert result.content == CONFORMANT


def test_a_schema_invalid_response_is_not_retried():
    """A model result, not a transport fault. Re-asking at temperature 0 returns the same
    answer and charges for it twice."""
    wrong = json.dumps({"metric_id": "homes_sold", "value": "two thousand",
                        "unit": "homes", "period_end": "2025-03-31"})
    provider, calls = provider_on(lambda request: httpx.Response(200, json=envelope(wrong)))
    with pytest.raises(ProviderSchemaError) as raised:
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert len(calls) == 1, "a schema violation must never be retried"
    assert raised.value.violations
    assert "value" in raised.value.violations[0]


def test_a_missing_required_field_is_a_schema_error_and_is_not_retried():
    partial = json.dumps({"metric_id": "homes_sold", "value": 2946, "unit": "homes"})
    provider, calls = provider_on(lambda request: httpx.Response(200, json=envelope(partial)))
    with pytest.raises(ProviderSchemaError):
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert len(calls) == 1


def test_a_non_retryable_status_raises_immediately():
    provider, calls = provider_on(lambda request: httpx.Response(400, text="bad schema"))
    with pytest.raises(ProviderResponseError):
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert len(calls) == 1


def test_a_timeout_raises_provider_timeout_and_is_not_multiplied():
    """Not retried on purpose: on a single-slot local server a second 120-second wait behind
    the first turns one slow passage into a six-minute stall."""
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    provider, calls = provider_on(slow)
    with pytest.raises(ProviderTimeout):
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert len(calls) == 1


def test_a_refused_connection_reports_unavailable_rather_than_a_generic_fault():
    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    provider, _ = provider_on(refused)
    with pytest.raises(ProviderUnavailable):
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)


# -- strict parsing ------------------------------------------------------------------------------


def test_malformed_json_content_raises_rather_than_returning_an_empty_object():
    """`{}` is a legitimate answer for some schemas. Returning one here would make an
    unparseable response indistinguishable from a real, empty one."""
    provider, _ = provider_on(
        lambda request: httpx.Response(200, json=envelope("{\"metric_id\": \"homes_sold\",")))
    with pytest.raises(ProviderResponseError) as raised:
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert "not JSON" in str(raised.value)


def test_empty_content_raises_and_names_the_finish_reason():
    """The exact failure thinking mode produced: `finish_reason: length`, content `''`."""
    provider, _ = provider_on(lambda request: httpx.Response(
        200, json=envelope("", finish_reason="length")))
    with pytest.raises(ProviderResponseError) as raised:
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert "length" in str(raised.value)


def test_a_response_with_no_choices_raises():
    provider, _ = provider_on(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(ProviderResponseError):
        provider.generate(prompt="x", schema=CLAIM_SCHEMA)


# -- what crosses the boundary --------------------------------------------------------------------


def test_the_result_carries_no_vendor_object():
    provider, _ = provider_on(
        lambda request: httpx.Response(200, json=envelope(json.dumps(CONFORMANT))))
    result = provider.generate(prompt="x", schema=CLAIM_SCHEMA)

    assert isinstance(result, GenerationResult)
    allowed = (str, int, float, bool, dict, tuple, type(None))
    for field in dataclasses.fields(result):
        value = getattr(result, field.name)
        assert isinstance(value, allowed), f"{field.name} is {type(value).__name__}"
    # A dict of primitives, not a response object wearing a dict's clothes.
    assert type(result.content) is dict
    for value in result.metadata.values():
        assert isinstance(value, allowed)
    assert "httpx" not in repr(result)


def test_reasoning_content_is_recorded_but_never_returned_as_content():
    provider, _ = provider_on(lambda request: httpx.Response(200, json=envelope(
        json.dumps(CONFORMANT), reasoning_content="the label or the value?")))
    result = provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert result.content == CONFORMANT
    assert result.metadata["reasoning_content"] == "the label or the value?"
    assert result.metadata["reasoning_characters"] == 23


def test_usage_timing_and_hashes_are_populated():
    provider, _ = provider_on(
        lambda request: httpx.Response(200, json=envelope(json.dumps(CONFORMANT))))
    result = provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (51, 58, 109)
    assert result.latency_ms >= 0.0
    assert len(result.raw_sha256) == 64 and len(result.content_sha256) == 64
    assert result.finish_reason == "stop"
    # The server reports the file path it was started with, not the configured name.
    assert result.model_id.endswith("Qwen3.5-9B-Q4_K_M.gguf")
    assert result.metadata["configured_model"] == "Qwen3.5-9B-Q4_K_M.gguf"


def test_the_content_digest_ignores_the_unstable_envelope():
    """The envelope carries a fresh `id`, `created` and `timings` on every call, so its digest
    cannot be a determinism check. The content digest can."""
    bodies = [envelope(json.dumps(CONFORMANT)), envelope(json.dumps(CONFORMANT))]
    bodies[1]["id"] = "chatcmpl-different"
    bodies[1]["created"] = 1785599999
    provider, _ = provider_on(lambda request: httpx.Response(200, json=bodies.pop(0)))
    first = provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    second = provider.generate(prompt="x", schema=CLAIM_SCHEMA)
    assert first.content_sha256 == second.content_sha256
    assert first.raw_sha256 != second.raw_sha256


# -- health --------------------------------------------------------------------------------------


def test_health_on_a_closed_port_reports_unhealthy_rather_than_raising():
    provider = LocalOpenAICompatibleGenerationProvider(
        ProviderConfig(base_url=f"http://127.0.0.1:{free_port()}", timeout_seconds=2))
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


# -- the protocol, driven through the protocol ------------------------------------------------------


def test_the_provider_is_usable_through_the_generation_provider_protocol():
    """Driven, not `isinstance`-checked. An isinstance assertion against a runtime-checkable
    Protocol verifies method presence and proves almost nothing."""
    provider, _ = provider_on(
        lambda request: httpx.Response(200, json=envelope(json.dumps(CONFORMANT))))
    port: GenerationProvider = provider
    assert port.model_id == "Qwen3.5-9B-Q4_K_M.gguf"
    result = port.generate(prompt="read this table", schema=CLAIM_SCHEMA,
                           max_tokens=256, temperature=0.0)
    assert result.content["metric_id"] == "homes_sold"


# -- the schema subset ------------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ({"metric_id": "homes_sold", "value": 2946, "unit": "homes", "period_end": "2025-03-31"}, 0),
    ({"metric_id": "homes_sold", "value": True, "unit": "homes", "period_end": "x"}, 1),
    ({"metric_id": "homes_sold", "value": 1, "unit": "homes", "period_end": "x", "extra": 1}, 1),
    ({"metric_id": "homes_sold", "value": 1, "unit": "homes"}, 1),
])
def test_the_schema_check_covers_the_keywords_the_extraction_schemas_use(value, expected):
    """A boolean is not a number, even though `bool` subclasses `int`."""
    assert len(schema_violations(value, CLAIM_SCHEMA)) == expected


def test_an_enum_violation_is_caught():
    schema = {"type": "object", "properties": {"unit": {"type": "string",
                                                        "enum": ["homes", "usd"]}}}
    assert schema_violations({"unit": "furlongs"}, schema)
    assert not schema_violations({"unit": "usd"}, schema)


# -- architectural rules, executable ------------------------------------------------------------------


# The one stage that is allowed to know a provider exists, and the only module of the
# provider package it may name. `extraction/contracts.py` has drawn this exception since
# before either lane was written — `OntologyGuidedNarrativeClaimLane (a local generation
# provider, behind its own port)` — and stage 10 is where it became real.
#
# Amended at stage 10 *(2026-08-01)*. The original rule was "no stage, ever", written when the
# table lane was the only stage there was, and it would have failed the narrative lane for
# importing two exception classes.
#
# **This is a narrowly scoped exception, not a stricter rule** *(wording corrected at review
# 2026-08-02, which found it described as "stricter")*. It is strictly weaker: it permits
# something the old rule forbade — `narrative/*` naming `providers.public` — and forbids
# nothing the old rule allowed. What bounds it is the scope, not the strength: exactly one
# stage, exactly one module of the provider package, and the adapter that holds the wire format
# still unreachable from anywhere under `stages/`.
PROVIDER_AWARE_STAGE = "narrative"
PROVIDER_PORT_MODULE = "providers.public"


def test_no_stage_can_reach_a_provider_except_the_lane_the_contract_names():
    """The rule the whole package exists to keep. A stage that *could* reach a provider is
    one that might, and the table lane's determinism would rest on review rather than
    structure.

    What this test checks is the *provider* half: which stage may name which provider module.
    The wire-format half — that no HTTP client is reachable from the narrative lane under any
    name — is not checkable with the fixed list of two strings below, and is checked over the
    transitive import graph by
    `test_narrative_lane.test_nothing_the_narrative_lane_reaches_can_name_a_wire_format`. The
    list here is a fast structural backstop for the two clients this repository actually has;
    it is deliberately not the guarantee.
    """
    offenders = []
    for path in (PACKAGE / "stages").rglob("*.py"):
        stage = path.relative_to(PACKAGE / "stages").parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in ("httpx", "requests"):
                    offenders.append(f"{path.relative_to(REPO)}: {name}")
                elif "provider" in name.lower():
                    allowed = (stage == PROVIDER_AWARE_STAGE
                               and name.endswith(PROVIDER_PORT_MODULE))
                    if not allowed:
                        offenders.append(f"{path.relative_to(REPO)}: {name}")
    assert offenders == []


def test_the_provider_package_does_not_import_a_lane_or_the_ontology():
    """One-way dependency: a provider answers questions, it does not know what they are for."""
    offenders = []
    for path in (PACKAGE / "providers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in ("ontology", "normalization", "acquisition"):
                    offenders.append(f"{path.name}: {name}")
                if "stages" in name:
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
