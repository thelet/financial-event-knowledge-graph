"""The story provider against the real llama.cpp server. One request, three personas apart.

Marked `live` file-wide, so `pytest -m "not live"` stays green on a machine with no model.
Mocks cannot satisfy this file by design: `test_story_provider.py` proves the wire contract,
and this one proves the wire contract was right — that the nested `response_format` really
constrains the grammar, and that a system message and a user message really arrive as two
turns rather than one.

**A skip is the correct outcome when nothing is listening.** The server is a local process an
operator starts by hand, and WORKSTREAM_BOUNDARY §3 records that it is single-slot and shared
with the factual-spine session; a suite that failed because a GPU was busy would be a suite
nobody trusts. The probe is a socket connect with a one-second budget, not a request, because
`Driver.verify_connectivity`'s lesson applies here too — a health call against a dead port can
cost far more than the connect that proves it dead.

Recorded 2026-08-03 from this worktree: **nothing was listening on 127.0.0.1:8080**, so every
test below skipped. The wire shape they assert is the one `tests/extraction/test_provider.py`
measured against llama.cpp `b10217` (`ddd4ec1`) serving Qwen3.5-9B-Q4_K_M on 2026-08-01, and
this file is the gate that will say so in story's own terms the first time the server is up.
"""

from __future__ import annotations

import socket

import pytest

from story.providers.openai_compatible import StoryOpenAICompatibleProvider
from story.providers.portable_schema import schema_violations
from story.providers.public import PINNED_TEMPERATURE, load_provider_config

pytestmark = pytest.mark.live

#: Small, and every property required — §15.3's rule, and the reason the answer is checkable.
#: `enum` rather than a free string on `direction` so the grammar has something to enforce
#: that a plausible-sounding wrong answer would fail.
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


def server_is_listening(base_url: str, timeout_seconds: float = 1.0) -> bool:
    host, _, port = base_url.rsplit("/", 1)[-1].partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=timeout_seconds):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def provider():
    config = load_provider_config()
    if not server_is_listening(config.base_url):
        pytest.skip(f"no model server listening on {config.base_url}")
    with StoryOpenAICompatibleProvider(config) as live:
        yield live


def test_the_running_server_reports_itself_healthy(provider):
    status = provider.health()
    assert status.ok, status.detail
    assert status.status == "ok"


def test_one_real_schema_constrained_generation_conforms_and_answers_the_question(provider):
    """The whole of S6 through the real transport: two turns, a named schema, a strict grammar.

    `crossed_zero` is the assertion that matters — it is derivable from the two numbers in the
    prompt and from nothing else, so a model answering from its own priors gets it wrong.
    """
    result = provider.generate(
        system=SYSTEM,
        prompt=PROMPT,
        schema=MOVE_SCHEMA,
        schema_name="story_metric_move_probe",
        max_tokens=256,
        temperature=PINNED_TEMPERATURE,
    )

    assert schema_violations(result.content, MOVE_SCHEMA) == []
    assert result.content["direction"] == "decrease"
    assert result.content["crossed_zero"] is True
    assert result.finish_reason == "stop"
    assert result.attempts == 1
    # The server reports the file path it was started with, not the configured name.
    assert result.model_id.endswith(".gguf")
    assert result.metadata["configured_model"] == provider.model_id
    assert result.total_tokens > 0
