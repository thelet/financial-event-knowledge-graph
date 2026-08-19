"""The one module in `story/` that opens an HTTP connection, behind `StoryGenerationProvider`.

Written against the running llama.cpp build recorded in `LOCAL_RUNTIME_VALIDATED.md`
(`b10217`, `ddd4ec1`), not against OpenAI's documentation. The wire shape below was read off
that server *(verified 2026-08-01, re-used unchanged here; this module's own live gate is
`tests/story/test_story_provider_live.py`)*:

    POST /v1/chat/completions
    -> {"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{…}"}}],
        "model":"/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "usage":{"completion_tokens":58,"prompt_tokens":51,"total_tokens":109,
                 "prompt_tokens_details":{"cached_tokens":0}},
        "timings":{…}, "id":"chatcmpl-…", "created":1785587924}

Two facts from that observation shape the module. The server reports `model` as the *file
path* it was started with, so the result records what the server said and the configured name
travels beside it in `metadata` — the distinction `AnswerStore` upstream learned the hard way
and `story/providers/generation_store.py` keeps as two fields. And the envelope carries an
`id`, a `created` stamp and per-request `timings`, so `raw_sha256` cannot be a determinism
check; `content_sha256` is.

**Re-stated from `extraction/providers/local_openai_compatible.py`, not imported** (§15.2).
The transport half — retry bound, backoff, error mapping, the two hashes — is duplicated
knowingly. Two things are deliberately *different*, and both are defects §15.1 asks not to
reproduce:

* **`system` and `prompt` are two messages.** Upstream collapses to a single user turn; a
  planner, a writer and an advisory verifier are three personas, and folding a persona into
  the prompt string hides the structure inside the request digest the replay store is keyed
  by. An empty `system` is refused rather than dropped: on the wire it is indistinguishable
  from having no persona, while in the digest it is a distinct request.
* **`schema_name` reaches the wire.** Upstream hard-codes `"extraction_claim"` for every call,
  so the server's own error messages name the wrong schema and three story call sites would
  be indistinguishable in a capture.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Mapping

import httpx

from story.core.models import GenerationResult, HealthStatus
from story.providers.portable_schema import schema_violations, validate_portable_schema
from story.providers.public import (
    HEALTH_TIMEOUT_CEILING_SECONDS,
    PINNED_TEMPERATURE,
    PROVIDER_LOCAL,
    RETRYABLE_STATUSES,
    StoryProviderConfig,
    StoryProviderConfigurationError,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    StoryProviderTimeout,
    StoryProviderTransportError,
    StoryProviderUnavailable,
    response_byte_ceiling,
)


class StoryOpenAICompatibleProvider:
    """Satisfies `story.contracts.StoryGenerationProvider` against a local server.

    `sleep` is injected so the retry bound is testable without waiting for it, and `client` so
    the whole transport can be driven by `httpx.MockTransport` with no server running — the
    two seams that make `tests/story/test_story_provider.py` offline by construction rather
    than by convention.
    """

    def __init__(
        self,
        config: StoryProviderConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        # Idempotent, and called here so a hand-built config cannot skip the check that only
        # `from_config`/`load_provider_config` would otherwise apply — including the `kind`
        # dispatch, which is the one place an unsupported transport can still be refused.
        self._config = config.validated()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.timeout_seconds)
        self._sleep = sleep if sleep is not None else time.sleep

    # -- lifecycle -------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "StoryOpenAICompatibleProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def provider_id(self) -> str:
        """Which adapter answered. A digest input to `request_identity` and to `story_run_id`
        from S12: a local server answers to any model string, so two providers configured with
        one name would otherwise share a replay row."""
        return PROVIDER_LOCAL

    @property
    def model_id(self) -> str:
        """The *configured* name. Every generation is keyed under this, never under the path
        the server reports — `provider_model_id` carries that."""
        return self._config.model

    @property
    def config(self) -> StoryProviderConfig:
        return self._config

    # -- health ----------------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """`GET /health`, structured for every outcome including a server that is not up.

        Never raises: a local server that is not running is an ordinary state for the freshness
        gate and the CLI to branch on, and a caller that had to catch an exception to find out
        would be one that forgets to.
        """
        url = f"{self._config.base_url}/health"
        timeout = min(self._config.timeout_seconds, HEALTH_TIMEOUT_CEILING_SECONDS)
        try:
            response = self._client.get(
                url, timeout=timeout, headers=self._config.authorization_headers)
        except httpx.TimeoutException as exc:
            return HealthStatus(ok=False, status="timeout", detail=f"{url}: {exc}")
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, status="unavailable", detail=f"{url}: {exc}")
        if response.status_code != 200:
            return HealthStatus(
                ok=False, status="error", detail=f"{url}: HTTP {response.status_code}")
        try:
            status = str((response.json() or {}).get("status", ""))
        except ValueError as exc:
            return HealthStatus(
                ok=False, status="error", detail=f"{url}: unparseable body: {exc}")
        return HealthStatus(ok=status == "ok", status=status or "unknown", detail=None)

    # -- generation ------------------------------------------------------------------------

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float = PINNED_TEMPERATURE,
    ) -> GenerationResult:
        """One non-streaming, schema-constrained completion.

        `max_tokens` has no default: it is a digest input to `request_identity`, so a call site
        that omitted it would silently re-key its own replay rows the day the config moved.
        `temperature` does have one, and it is the pinned constant rather than anything read
        from configuration (§15.1).

        Streaming is a non-goal — a stage needs a whole object before it can read anything out
        of it, and a partial JSON document is not a partial plan.
        """
        body = self.request_body(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        started = time.perf_counter()
        response, attempts = self._post_with_retries(body)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._to_result(response, schema, schema_name, latency_ms, attempts)

    def request_body(
        self,
        *,
        system: str,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float = PINNED_TEMPERATURE,
    ) -> dict[str, Any]:
        """The exact JSON posted. Public so a test can assert on it without a server.

        The schema is refused here rather than at the response, because §15.3's non-portable
        keywords produce no error anywhere else: the grammar drops them and the answer looks
        conformant.
        """
        if not system.strip():
            raise StoryProviderConfigurationError(
                "system must name a persona: an empty system message is indistinguishable on "
                "the wire from sending none, while still entering the request digest, so the "
                "call would collapse to the single-turn shape §15.1 exists to replace")
        if not prompt.strip():
            raise StoryProviderConfigurationError("prompt must not be empty")
        if not schema_name.strip():
            raise StoryProviderConfigurationError(
                "schema_name must be given: it reaches the wire and the store, and it is what "
                "tells a planner request from a writer request in a capture or a stored row")
        validate_portable_schema(schema)
        return {
            "model": self._config.model,
            # Two turns, not one. The order is fixed: a system message after a user message is
            # honoured by some templates and ignored by others.
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
            # Unconditional, and there is no configuration field that could change it:
            # LOCAL_RUNTIME_VALIDATED.md §4 measured a four-field schema request spending its
            # whole budget reasoning and returning empty content with thinking on.
            "chat_template_kwargs": {"enable_thinking": False},
            # The **nested** form. The llama.cpp README documents a top-level `schema`
            # variant that the parser does not read, so it applies no constraint and does so
            # silently — an unconstrained request that looks constrained.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name, "strict": True, "schema": dict(schema)},
            },
        }

    # -- transport -------------------------------------------------------------------------

    def _post_with_retries(self, body: dict) -> tuple[httpx.Response, int]:
        """Bounded retries, transport faults and the five retryable statuses only.

        `max_retries` counts attempts *after* the first, so the default of 2 makes three
        attempts in total, with `0.25 × 2ⁿ` seconds between them.

        **A timeout is not retried.** On a single-slot local server a timeout means the model
        is still working or wedged, and a second 120-second wait behind the first turns one
        slow request into a six-minute stall — the caller can decide far better than this loop
        can, and with three story call sites per candidate the stall multiplies.
        """
        url = f"{self._config.base_url}/v1/chat/completions"
        attempts = self._config.max_retries + 1
        last_error: Exception | None = None
        last_detail = "no attempt made"
        headers = self._config.authorization_headers

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.post(
                    url, json=body, timeout=self._config.timeout_seconds, headers=headers)
            except httpx.TimeoutException as exc:
                raise StoryProviderTimeout(
                    f"{url}: no response within {self._config.timeout_seconds}s") from exc
            except httpx.ConnectError as exc:
                last_error, last_detail = exc, f"connection refused: {exc}"
            except httpx.HTTPError as exc:
                last_error, last_detail = exc, f"transport error: {exc}"
            else:
                if response.status_code == 200:
                    return response, attempt
                last_error, last_detail = None, f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUSES:
                    raise StoryProviderResponseError(
                        f"{url}: {last_detail}: {response.text[:200]}")

            if attempt < attempts:
                self._sleep(0.25 * (2 ** (attempt - 1)))

        if isinstance(last_error, httpx.ConnectError):
            raise StoryProviderUnavailable(
                f"{url}: unreachable after {attempts} attempts: {last_detail}") from last_error
        raise StoryProviderTransportError(
            f"{url}: failed after {attempts} attempts: {last_detail}") from last_error

    # -- response --------------------------------------------------------------------------

    def _refuse_an_oversized_body(self, size: int) -> None:
        """A response body bounded by what the request asked for. Typed, like every other refusal.

        **Reproduced before it was bounded** *(adversarial review, 2026-08-19)*: an 8 MB answer
        was parsed, schema-checked and returned without complaint through *both* adapters, and
        would have landed whole in `generations.jsonl` and in the run's artifacts.

        The bound is here as well as in `openai_responses.py` and for the same reason a fault is
        bounded anywhere: a local server is a process an operator started, not a proof. It is a
        loose bound — `response_byte_ceiling` allows 64 bytes per requested output token — so it
        refuses a body that is categorically wrong rather than one that merely ran long, and
        `max_output_tokens` is the anchor because it is the only number in the request that says
        how much text was invited.

        The body is already in memory by the time this runs; bounding the *read* would mean
        `client.stream` and a second code path on a non-streaming API. What this closes is the
        half that persists — nothing oversized reaches a store, an artifact or a response.
        """
        ceiling = response_byte_ceiling(self._config.max_output_tokens)
        if size <= ceiling:
            return
        raise StoryProviderResponseError(
            f"response body is {size} bytes, over the {ceiling}-byte ceiling this request "
            f"earns at max_output_tokens={self._config.max_output_tokens}; a body that far past "
            "its own budget is not an answer, and it would be stored and served whole")

    def _to_result(
        self,
        response: httpx.Response,
        schema: Mapping[str, Any],
        schema_name: str,
        latency_ms: float,
        attempts: int,
    ) -> GenerationResult:
        raw_bytes = response.content
        self._refuse_an_oversized_body(len(raw_bytes))
        try:
            envelope = json.loads(raw_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoryProviderResponseError(f"response body is not JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise StoryProviderResponseError(
                f"response body is {type(envelope).__name__}, not an object")

        choices = envelope.get("choices") or []
        if not choices:
            raise StoryProviderResponseError("response carries no choices")
        message = (choices[0] or {}).get("message") or {}
        raw_content = message.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise StoryProviderResponseError(
                "response carries no assistant content; "
                f"finish_reason={choices[0].get('finish_reason')!r}")

        try:
            content = json.loads(raw_content)
        except ValueError as exc:
            # Never `{}`. An empty object is a legitimate answer for some schemas, and
            # returning one here would make an unparseable response indistinguishable.
            raise StoryProviderResponseError(
                f"assistant content is not JSON: {exc}: {raw_content[:200]!r}") from exc
        if not isinstance(content, dict):
            raise StoryProviderResponseError(
                f"assistant content is {type(content).__name__}, not an object")

        violations = tuple(schema_violations(content, schema))
        if violations:
            raise StoryProviderSchemaError(
                f"response does not satisfy schema {schema_name!r}: {'; '.join(violations)}",
                violations)

        usage = envelope.get("usage") or {}
        metadata: dict[str, Any] = {
            "schema_name": schema_name,
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "system_fingerprint": envelope.get("system_fingerprint"),
            # What we asked to talk to, beside what answered. The store keys on this one.
            "configured_model": self._config.model,
        }
        # Recorded, never returned as content: the verifier reads `content` and nothing else,
        # and a reasoning trace is prose the deterministic layer has no way to check.
        reasoning = message.get("reasoning_content")
        if reasoning:
            metadata["reasoning_content"] = reasoning
            metadata["reasoning_characters"] = len(reasoning)

        return GenerationResult(
            content=content,
            raw_content=raw_content,
            model_id=str(envelope.get("model") or self._config.model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            latency_ms=latency_ms,
            raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            content_sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            finish_reason=str(choices[0].get("finish_reason") or ""),
            attempts=attempts,
            metadata=metadata,
        )
