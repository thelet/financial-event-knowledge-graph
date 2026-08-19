"""The OpenAI adapter against the real API. Four requests, two models, no mocks.

Marked `live` file-wide, so `pytest -m "not live"` stays green on a machine with no key.
Mocks cannot satisfy this file by design: `test_story_provider_openai.py` proves the wire
contract, and this one proves the wire contract was *right* — that `text.format` really is
flat, that `store: false` and `reasoning.effort` are really accepted, and above all that the
per-model `supports_temperature` in `config/story.yaml` is a measurement rather than a belief.

**A skip is the correct outcome when no key is configured.** §4.3 makes an absent
`OPENAI_API_KEY` an ordinary state, and a suite that failed on a machine without a credential
would be a suite nobody can run.

*Recorded 2026-08-19 from this worktree, with the key in the gitignored `.env`: all four
requests below were issued and every assertion held.* Two findings from that run are recorded
rather than asserted, because they are facts about the models and not about this adapter:

* `gpt-5-nano` at `reasoning_effort: minimal` answered `crossed_zero: false` for a metric that
  moved from +$56m to −$211m — **wrong**, where `gpt-4.1-nano` on the identical prompt answered
  `true`. The schema was satisfied in both cases. This file therefore asserts conformance and
  the translation, never the analysis; the §10 comparison is where an answer's quality belongs.
* `usage.output_tokens_details.reasoning_tokens` came back **0** for the completed `minimal`
  call, so a non-zero reasoning count is not something a passing run may require.
"""

from __future__ import annotations

import dataclasses

import pytest

from story.providers.openai_responses import StoryOpenAIResponsesProvider
from story.providers.portable_schema import schema_violations
from story.providers.public import (
    PINNED_TEMPERATURE,
    PROVIDER_OPENAI,
    StoryProviderResponseError,
    load_provider_config,
)

pytestmark = pytest.mark.live

#: Small, every property required, `additionalProperties: false` — §15.3's rule, which is also
#: OpenAI strict Structured Outputs' own requirement. Handed to the API unmodified.
MOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "metric": {"type": "string"},
        "direction": {"type": "string", "enum": ["increase", "decrease", "unchanged"]},
        "crossed_zero": {"type": "boolean"},
    },
    "required": ["metric", "direction", "crossed_zero"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a financial analyst. Answer only from the numbers you are given, and never "
    "explain the cause of a change."
)
PROMPT = (
    "Opendoor Technologies reported adjusted EBITDA of $56 million in 2022Q2 and negative "
    "$211 million in 2022Q3. Describe the move in adjusted EBITDA."
)


def config_for(model_id: str | None = None):
    config = load_provider_config(provider_id=PROVIDER_OPENAI, model_id=model_id)
    if config.api_key is None:
        pytest.skip("OPENAI_API_KEY is not set; the live OpenAI gate has nothing to talk to")
    return config


@pytest.fixture(scope="module")
def reasoning_provider():
    """`gpt-5-nano` — the configured default, and the model that refuses a temperature."""
    with StoryOpenAIResponsesProvider(config_for()) as provider:
        yield provider


@pytest.fixture(scope="module")
def reasoning_result(reasoning_provider):
    """One request, shared by every assertion about it. Four live calls in this file, not ten."""
    return reasoning_provider.generate(
        system=SYSTEM, prompt=PROMPT, schema=MOVE_SCHEMA,
        schema_name="story_metric_move_probe", max_tokens=700,
        temperature=PINNED_TEMPERATURE)


def test_the_real_endpoint_reports_itself_reachable_with_this_key(reasoning_provider):
    status = reasoning_provider.health()
    assert status.ok, status.detail
    assert status.status == "ok"


def test_one_real_schema_constrained_response_conforms_to_the_schema_it_was_given(
        reasoning_result):
    """The whole adapter through the real transport: two turns, a flat `text.format`, a strict
    schema and `store: false`.

    `direction` is the assertion that matters — it is derivable from the two numbers in the
    prompt and from nothing else. `crossed_zero` is deliberately *not* asserted; see the module
    docstring for what the two models actually answered.
    """
    assert schema_violations(reasoning_result.content, MOVE_SCHEMA) == []
    assert reasoning_result.content["direction"] == "decrease"
    assert reasoning_result.attempts == 1
    assert reasoning_result.finish_reason == "completed"


def test_the_dated_model_id_comes_back_beside_the_configured_one(reasoning_result):
    """The role the `.gguf` path plays locally: what answered, beside what was asked for."""
    assert reasoning_result.model_id.startswith("gpt-5-nano-")
    assert reasoning_result.model_id != "gpt-5-nano", "the API returns a dated id"
    assert reasoning_result.metadata["configured_model"] == "gpt-5-nano"


def test_the_usage_the_api_reported_is_translated_and_not_invented(reasoning_result):
    """`input_tokens`/`output_tokens` arrive under those names and leave under the ones every
    manifest and every stored row already uses."""
    assert reasoning_result.prompt_tokens > 0
    assert reasoning_result.completion_tokens > 0
    assert reasoning_result.total_tokens == (
        reasoning_result.prompt_tokens + reasoning_result.completion_tokens)
    assert reasoning_result.metadata["reasoning_tokens"] is not None
    assert reasoning_result.latency_ms > 0.0


def test_the_default_model_really_is_called_without_a_temperature(reasoning_result):
    assert reasoning_result.metadata["temperature_sent"] is False
    assert reasoning_result.metadata["reasoning_effort"] == "minimal"
    assert reasoning_result.metadata["store_responses"] is False


def test_the_temperature_pinned_alternative_really_accepts_the_pinned_temperature():
    """`gpt-4.1-mini` is offered as the determinism-sensitive choice (§11), which is only true
    if `temperature: 0.0` reaches it and is accepted."""
    with StoryOpenAIResponsesProvider(config_for("gpt-4.1-mini")) as provider:
        result = provider.generate(
            system=SYSTEM, prompt=PROMPT, schema=MOVE_SCHEMA,
            schema_name="story_metric_move_probe", max_tokens=256,
            temperature=PINNED_TEMPERATURE)

    assert schema_violations(result.content, MOVE_SCHEMA) == []
    assert result.metadata["temperature_sent"] is True
    assert result.metadata["reasoning_effort"] is None
    assert result.model_id.startswith("gpt-4.1-mini-")


def test_the_configured_capability_is_the_apis_behaviour_and_not_a_belief():
    """The one measurement the whole design rests on, re-taken against the live API.

    `config/story.yaml` declares `gpt-5-nano: supports_temperature: false`. This deliberately
    *lies* to the adapter — a config claiming the opposite — and asserts that the API refuses
    the request. If OpenAI ever starts accepting a temperature on this model, this test goes
    green-to-red and the configured claim can be corrected rather than quietly outliving the
    fact it was taken from.
    """
    claims_it_can = dataclasses.replace(config_for(), supports_temperature=True)
    with StoryOpenAIResponsesProvider(claims_it_can) as provider:
        with pytest.raises(StoryProviderResponseError) as raised:
            provider.generate(
                system=SYSTEM, prompt=PROMPT, schema=MOVE_SCHEMA,
                schema_name="story_metric_move_probe", max_tokens=256,
                temperature=PINNED_TEMPERATURE)

    message = str(raised.value)
    assert "400" in message and "temperature" in message
    assert claims_it_can.api_key.get_secret_value() not in message
