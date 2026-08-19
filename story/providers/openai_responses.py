"""The second module in `story/` that opens an HTTP connection: OpenAI's Responses API.

Written against the real endpoint, not against documentation. Every shape below was read off
`POST https://api.openai.com/v1/responses` *(verified 2026-08-19, plan §3 and re-probed while
writing this module)*:

    -> {"status":"completed", "incomplete_details":null,
        "model":"gpt-5-nano-2025-08-07", "id":"resp-…", "created_at":…,
        "output":[{"type":"reasoning","summary":[],…},
                  {"type":"message","status":"completed",
                   "content":[{"type":"output_text","text":"{…}"}]}],
        "usage":{"input_tokens":110,"input_tokens_details":{"cached_tokens":0},
                 "output_tokens":41,"output_tokens_details":{"reasoning_tokens":0},
                 "total_tokens":151}}

Four measurements shape this module, and each of them contradicts something a reader might
otherwise assume from `openai_compatible.py`:

* **`text.format` is flat.** `name`, `strict` and `schema` are siblings of `type`, *not* nested
  under a `json_schema` key the way llama.cpp requires. The same schema object goes to both
  providers unmodified — that is the point of the portable subset — but the envelope around it
  differs, and there is no OpenAI-specific schema path here.
* **`usage` uses different words for the same numbers.** `input_tokens`/`output_tokens`, not
  `prompt_tokens`/`completion_tokens`. Translating them is this adapter's job, so that
  `token_totals` in a manifest means one thing whichever provider filled it.
* **`temperature` is model-dependent.** `gpt-5-nano` answers a `temperature` with HTTP 400
  `Unsupported parameter`; `gpt-4.1-nano` accepts `0.0`. So the pinned temperature is sent only
  when the resolved model declares the capability, and `metadata["temperature_sent"]` records
  what happened — a manifest claiming `temperature: 0.0` for a request that never carried one
  would be plan §1's finding F3 reproduced rather than fixed.
* **A 401 body echoes part of the key back.** Measured: `Incorrect API key provided:
  sk-obvio**********alid`. The first eight characters of a real key are a real disclosure, so
  every message this module builds from a response body goes through `_redacted` first. That is
  why the error text is not simply `response.text[:200]` as it is locally, where no key exists.

There is no `finish_reason` on the wire. The honest analogue is the response `status`, and it
is recorded as such rather than translated into the local server's `"stop"` — a value this API
never produces.

**Re-stated from `openai_compatible.py`, not shared with it.** The retry bound, the backoff,
the never-retry-a-timeout rule and the two hashes are the same lines twice. Factoring them into
a base class would put the two wire formats behind one inheritance chain and make the *shape*
of a request a template-method detail; §15.2 already settled this trade the other way for the
extraction transport, and the repository's rule is structural typing with no inheritance
requirement. What is duplicated is named here so a reader does not take it for an accident.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping

import httpx

from story.core.models import GenerationResult, HealthStatus
from story.providers.portable_schema import schema_violations, validate_portable_schema
from story.providers.public import (
    HEALTH_TIMEOUT_CEILING_SECONDS,
    OPENAI_KEY_MISSING_REASON,
    PINNED_TEMPERATURE,
    PROVIDER_OPENAI,
    RETRYABLE_STATUSES,
    StoryProviderConfig,
    StoryProviderConfigurationError,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    StoryProviderTimeout,
    StoryProviderTransportError,
    StoryProviderUnavailable,
)

#: Any bearer-looking token, not just the configured one. OpenAI's own 401 echoes a *partial*
#: key (`sk-obvio**********alid`), which an exact-substring replacement would miss entirely.
_KEY_SHAPED = re.compile(r"sk-[A-Za-z0-9_\-*]+")


class StoryOpenAIResponsesProvider:
    """Satisfies `story.contracts.StoryGenerationProvider` against `POST /responses`.

    `client` and `sleep` are injected for the same two reasons the local adapter injects them:
    `httpx.MockTransport` makes `tests/story/test_story_provider_openai.py` offline *by
    construction* rather than by convention, and a test for the retry bound must not wait for
    it. The default client is built here rather than shared with the local provider — two base
    URLs and two timeouts, and a pooled connection to a local process is not one to reuse for
    TLS to an API.
    """

    def __init__(
        self,
        config: StoryProviderConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._config = config.validated()
        if self._config.kind != PROVIDER_OPENAI:
            raise StoryProviderConfigurationError(
                f"{type(self).__name__} serves {PROVIDER_OPENAI!r}, not "
                f"{self._config.kind!r}: the two adapters send structurally different requests, "
                "so a config pointed at the wrong one would key its generations under a "
                "provider that never answered")
        # §4.3: an absent key is an ordinary state for the *catalogue* to render and a raise
        # here, before a client is built. A provider that could be constructed without one
        # would discover it at the first request — after a package was composed and a prompt
        # assembled — and would have to be told apart from a genuine 401.
        if not self._api_key:
            raise StoryProviderConfigurationError(OPENAI_KEY_MISSING_REASON)
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.timeout_seconds)
        self._sleep = sleep if sleep is not None else time.sleep

    # -- lifecycle -------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "StoryOpenAIResponsesProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def provider_id(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model_id(self) -> str:
        """The *configured* name — `gpt-5-nano`, never the dated `gpt-5-nano-2025-08-07` the
        API answers with. Every generation is keyed under this one; the dated id travels as
        `provider_model_id`, the role the `.gguf` path plays locally, and it moves on its own
        the day OpenAI reroutes an alias."""
        return self._config.model

    @property
    def config(self) -> StoryProviderConfig:
        return self._config

    @property
    def _api_key(self) -> str:
        key = self._config.api_key
        return "" if key is None else key.get_secret_value().strip()

    # -- health ----------------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """`GET /models`, the cheapest call that proves the key and the endpoint together.

        Never raises, for the reason the local `health` gives. A **rejected** key is reported
        as `status="unauthorized"` rather than thrown, because "the API is down" and "this key
        is wrong" are different facts a caller branches on differently; an **absent** key never
        reaches here at all, because construction refused it, and the catalogue reports that
        one as an unavailable provider with a reason.
        """
        url = f"{self._config.base_url}/models"
        timeout = min(self._config.timeout_seconds, HEALTH_TIMEOUT_CEILING_SECONDS)
        try:
            response = self._client.get(
                url, timeout=timeout, headers=self._config.authorization_headers)
        except httpx.TimeoutException as exc:
            return HealthStatus(ok=False, status="timeout", detail=self._redacted(f"{url}: {exc}"))
        except httpx.HTTPError as exc:
            return HealthStatus(
                ok=False, status="unavailable", detail=self._redacted(f"{url}: {exc}"))
        if response.status_code in (401, 403):
            return HealthStatus(ok=False, status="unauthorized",
                                detail=f"{url}: HTTP {response.status_code}")
        if response.status_code != 200:
            return HealthStatus(
                ok=False, status="error", detail=f"{url}: HTTP {response.status_code}")
        return HealthStatus(ok=True, status="ok", detail=None)

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
        """One non-streaming, schema-constrained response. The local adapter's contract exactly.

        `max_tokens` has no default here either: it is a digest input to `request_identity`,
        and the two providers must agree about which values are inputs or the same logical call
        keys differently depending on who answers it.
        """
        body = self.request_body(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        started = time.perf_counter()
        response, attempts = self._post_with_retries(body)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._to_result(response, schema, schema_name, latency_ms, attempts, body)

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

        The schema is validated with the **same** `validate_portable_schema` the local provider
        uses, and reaches the wire unmodified. That is not a convenience: OpenAI's strict
        Structured Outputs requires `additionalProperties: false` and every property
        `required`, which is what §15.3's subset already demanded of llama.cpp for a different
        reason, and a schema missing it is refused here with the same message rather than by a
        400 that names `text.format.schema` (measured, plan §3).
        """
        if not system.strip():
            raise StoryProviderConfigurationError(
                "system must name a persona: an empty system message is indistinguishable on "
                "the wire from sending none, while still entering the request digest")
        if not prompt.strip():
            raise StoryProviderConfigurationError("prompt must not be empty")
        if not schema_name.strip():
            raise StoryProviderConfigurationError(
                "schema_name must be given: it reaches the wire and the store, and it is what "
                "tells a planner request from a writer request in a capture or a stored row")
        validate_portable_schema(schema)

        body: dict[str, Any] = {
            "model": self._config.model,
            # Two turns, as locally. The Responses API takes `input` rather than `messages`,
            # and accepts a `system` role in it (measured 2026-08-19, HTTP 200).
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            # **Flat.** `name`/`strict`/`schema` are siblings of `type` here; the nested
            # `json_schema: {…}` form the local server requires is rejected by this API.
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                },
            },
            "max_output_tokens": int(max_tokens),
            # Unconditional, and there is no configuration key that could turn it on: a story
            # request carries the whole evidence package, and this API's default is to retain.
            "store": bool(self._config.store_responses),
        }
        # Sent only where it is accepted. `gpt-5-nano` answers a temperature with a 400, so the
        # capability is declared per model in configuration and verified against the API rather
        # than discovered once per run by spending a failed request on it.
        if self._config.supports_temperature:
            body["temperature"] = float(temperature)
        if self._config.reasoning_effort is not None:
            body["reasoning"] = {"effort": self._config.reasoning_effort}
        return body

    # -- transport -------------------------------------------------------------------------

    def _post_with_retries(self, body: dict) -> tuple[httpx.Response, int]:
        """Bounded retries over transport faults and the five retryable statuses only.

        The local module's rule, unchanged and for the same reasons: `max_retries` counts
        attempts *after* the first, backoff is `0.25 × 2ⁿ` seconds, and **a timeout is never
        retried** — a second full budget queued behind the first turns one slow request into a
        multi-minute stall, and a story candidate makes three requests.
        """
        url = f"{self._config.base_url}/responses"
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
                last_error, last_detail = exc, f"connection refused: {self._redacted(str(exc))}"
            except httpx.HTTPError as exc:
                last_error, last_detail = exc, f"transport error: {self._redacted(str(exc))}"
            else:
                if response.status_code == 200:
                    return response, attempt
                last_error, last_detail = None, f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUSES:
                    raise StoryProviderResponseError(
                        f"{url}: {last_detail}: {self._api_error(response)}")

            if attempt < attempts:
                self._sleep(0.25 * (2 ** (attempt - 1)))

        if isinstance(last_error, httpx.ConnectError):
            raise StoryProviderUnavailable(
                f"{url}: unreachable after {attempts} attempts: {last_detail}") from last_error
        raise StoryProviderTransportError(
            f"{url}: failed after {attempts} attempts: {last_detail}") from last_error

    # -- response --------------------------------------------------------------------------

    def _to_result(
        self,
        response: httpx.Response,
        schema: Mapping[str, Any],
        schema_name: str,
        latency_ms: float,
        attempts: int,
        body: Mapping[str, Any],
    ) -> GenerationResult:
        raw_bytes = response.content
        try:
            envelope = json.loads(raw_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoryProviderResponseError(f"response body is not JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise StoryProviderResponseError(
                f"response body is {type(envelope).__name__}, not an object")

        error = envelope.get("error")
        if error:
            raise StoryProviderResponseError(
                f"response carries an error: {self._redacted(json.dumps(error))[:200]}")

        status = str(envelope.get("status") or "")
        if status != "completed":
            # Measured: a budget too small for the reasoning pass returns 200 with
            # `status: incomplete`, `incomplete_details: {"reason": "max_output_tokens"}` and an
            # `output` holding a reasoning item and **no message at all**. Reporting that as
            # "no assistant content" would name the symptom and hide the cause.
            reason = (envelope.get("incomplete_details") or {}).get("reason")
            raise StoryProviderResponseError(
                f"response status is {status!r} rather than 'completed'"
                + (f": {reason}" if reason else ""))

        raw_content = self._output_text(envelope)
        try:
            content = json.loads(raw_content)
        except ValueError as exc:
            # Never `{}`. An empty object is a legitimate answer for some schemas, and returning
            # one here would make an unparseable response indistinguishable from a real one.
            raise StoryProviderResponseError(
                f"output text is not JSON: {exc}: {raw_content[:200]!r}") from exc
        if not isinstance(content, dict):
            raise StoryProviderResponseError(
                f"output text is {type(content).__name__}, not an object")

        violations = tuple(schema_violations(content, schema))
        if violations:
            raise StoryProviderSchemaError(
                f"response does not satisfy schema {schema_name!r}: {'; '.join(violations)}",
                violations)

        usage = envelope.get("usage") or {}
        metadata: dict[str, Any] = {
            "schema_name": schema_name,
            # What we asked to talk to, beside what answered. The store keys on this one.
            "configured_model": self._config.model,
            "response_id": envelope.get("id"),
            "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
            # Billed, and invisible in `completion_tokens`. A reasoning model's cost is mostly
            # here, so a manifest without it under-reports what a run spent.
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
                "reasoning_tokens"),
            # The three settings §5.2's `provider_settings` block is built from. Recorded as
            # what was *sent*, read off the body rather than off the config, so the manifest
            # cannot claim a temperature that no request carried.
            "temperature_sent": "temperature" in body,
            "reasoning_effort": self._config.reasoning_effort,
            "store_responses": bool(self._config.store_responses),
        }

        return GenerationResult(
            content=content,
            raw_content=raw_content,
            # The dated id the API returned, e.g. `gpt-5-nano-2025-08-07`.
            model_id=str(envelope.get("model") or self._config.model),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            latency_ms=latency_ms,
            raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            content_sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            # There is no `finish_reason` on this wire. `status` is the honest analogue, and it
            # is recorded rather than translated into the local server's `"stop"`, which this
            # API never sends.
            finish_reason=status,
            attempts=attempts,
            metadata=metadata,
        )

    def _output_text(self, envelope: Mapping[str, Any]) -> str:
        """The assistant's text, out of a list that also carries reasoning items.

        `output` is a *list of items*: a reasoning model emits `[reasoning, message]` and a
        non-reasoning one `[message]` (measured). Only the message item's `output_text` parts
        are the answer; a reasoning item is recorded nowhere as prose, for the reason the local
        adapter gives — the verifier reads `content` and nothing else, and a reasoning trace is
        prose the deterministic layer has no way to check.
        """
        output = envelope.get("output")
        if not isinstance(output, list) or not output:
            raise StoryProviderResponseError("response carries no output items")

        message = next(
            (item for item in output
             if isinstance(item, Mapping) and item.get("type") == "message"), None)
        if message is None:
            kinds = [item.get("type") for item in output if isinstance(item, Mapping)]
            raise StoryProviderResponseError(
                f"response carries no message item; output holds {kinds}")

        parts = message.get("content")
        if not isinstance(parts, list):
            raise StoryProviderResponseError("message item carries no content list")

        refusal = next(
            (part for part in parts
             if isinstance(part, Mapping) and part.get("type") == "refusal"), None)
        if refusal is not None:
            # A refusal is a well-formed answer that is not the answer. It is a response error
            # and not a schema error: nothing was returned to check against the schema.
            raise StoryProviderResponseError(
                f"model refused: {self._redacted(str(refusal.get('refusal') or ''))[:200]}")

        text = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, Mapping) and part.get("type") == "output_text")
        if not text.strip():
            raise StoryProviderResponseError(
                f"response carries no output text; message status="
                f"{message.get('status')!r}")
        return text

    # -- keeping the key out of everything a person can read --------------------------------

    def _api_error(self, response: httpx.Response) -> str:
        """`error.code: error.message` when the body says so, the redacted body when it does not.

        Preferred over `response.text[:200]` because this API states the cause in a field —
        `invalid_json_schema`, `Unsupported parameter: 'temperature'` — and a truncated JSON
        blob would cut it off exactly where it matters.
        """
        try:
            error = (response.json() or {}).get("error") or {}
        except ValueError:
            return self._redacted(response.text)[:200]
        if not isinstance(error, Mapping):
            return self._redacted(response.text)[:200]
        code = str(error.get("code") or error.get("type") or "")
        message = str(error.get("message") or "")
        joined = f"{code}: {message}".strip(": ") or self._redacted(response.text)
        return self._redacted(joined)[:200]

    def _redacted(self, text: str) -> str:
        """Every key-shaped token out, before a string can reach a message, a log or a payload.

        Both halves are needed. The exact configured key is replaced because `httpx` puts a URL
        and sometimes a header into a transport error; the `sk-…` pattern is replaced because
        OpenAI's own 401 returns a *partially* masked key (`sk-obvio**********alid`, measured
        2026-08-19) that shares its first eight characters with the real one and would survive
        an exact-substring replacement.
        """
        key = self._api_key
        if key:
            text = text.replace(key, "***")
        return _KEY_SHAPED.sub("sk-***", text)
