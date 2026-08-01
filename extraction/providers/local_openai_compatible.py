"""An OpenAI-compatible local server, behind the `GenerationProvider` port.

Written against the running llama.cpp build recorded in LOCAL_RUNTIME_VALIDATED.md
(`b10217`, `ddd4ec1`), not against OpenAI's documentation. The wire shape below was read off
that server *(verified 2026-08-01)*:

    POST /v1/chat/completions
    -> {"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{…}"}}],
        "model":"/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "usage":{"completion_tokens":58,"prompt_tokens":51,"total_tokens":109,
                 "prompt_tokens_details":{"cached_tokens":0}},
        "timings":{…}, "id":"chatcmpl-…", "created":1785587924}

Two facts from that observation shape this module. The server reports `model` as the *file
path* it was started with, not the configured name, so the result records what the server
said rather than what we asked for. And the envelope carries an `id`, a `created` stamp and
per-request `timings`, so its digest cannot be a determinism check — see
`GenerationResult`'s docstring.

The schema conformance check is deliberately small and local. llama.cpp constrains
generation with a grammar built from the schema, so a violation is rare; the check exists so
that a violation is a *typed* result rather than a claim built from a missing field, and a
JSON Schema library would be a dependency bought to restate what the grammar already
enforces.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

import httpx

from .public import (
    GenerationResult,
    HealthStatus,
    ProviderConfig,
    ProviderResponseError,
    ProviderSchemaError,
    ProviderTimeout,
    ProviderTransportError,
    ProviderUnavailable,
)

# Transient. Everything else the server can say is a statement about the request, and
# repeating an identical request cannot change it.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# A health probe that waits two minutes has stopped being a health probe. The generation
# timeout is sized for a 9B model producing a thousand tokens; liveness is not.
HEALTH_TIMEOUT_CEILING_SECONDS = 10.0

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


class LocalOpenAICompatibleGenerationProvider:
    """Satisfies `extraction.contracts.GenerationProvider` against a local server.

    Note one divergence from the protocol's annotation: `generate` returns a
    `GenerationResult`, not a `dict`. The contract predates this stage and says `-> dict`;
    STAGE_07 §3 requires a plain dataclass so that no raw response mapping escapes the
    boundary. Correcting the annotation would mean `extraction/contracts.py` importing this
    package, inverting the dependency the contract exists to prevent, so the stronger
    runtime guarantee is kept and the annotation left alone.
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        # `validated()` is idempotent, and calling it here means a hand-built config cannot
        # slip past the check that only `from_config` would otherwise apply.
        self._config = config.validated()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.timeout_seconds)
        self._sleep = sleep if sleep is not None else time.sleep

    # -- lifecycle -------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LocalOpenAICompatibleGenerationProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def model_id(self) -> str:
        return self._config.model

    @property
    def config(self) -> ProviderConfig:
        return self._config

    # -- health ----------------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """`GET /health`. Structured for every outcome, including a server that is not up."""
        url = f"{self._config.base_url}/health"
        timeout = min(self._config.timeout_seconds, HEALTH_TIMEOUT_CEILING_SECONDS)
        try:
            response = self._client.get(url, timeout=timeout)
        except httpx.TimeoutException as exc:
            return HealthStatus(False, "timeout", f"{url}: {exc}")
        except httpx.HTTPError as exc:
            return HealthStatus(False, "unavailable", f"{url}: {exc}")
        if response.status_code != 200:
            return HealthStatus(False, "error", f"{url}: HTTP {response.status_code}")
        try:
            status = str((response.json() or {}).get("status", ""))
        except ValueError as exc:
            return HealthStatus(False, "error", f"{url}: unparseable body: {exc}")
        return HealthStatus(status == "ok", status or "unknown", None)

    # -- generation ------------------------------------------------------------------------

    def generate(
        self,
        *,
        prompt: str,
        schema: dict,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> GenerationResult:
        """One non-streaming, schema-constrained completion.

        Streaming is a non-goal: a lane needs a whole object before it can read anything out
        of it, and a partial JSON document is not a partial claim.
        """
        body = self.request_body(
            prompt=prompt, schema=schema, max_tokens=max_tokens, temperature=temperature)
        started = time.perf_counter()
        response, attempts = self._post_with_retries(body)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._to_result(response, schema, latency_ms, attempts)

    def request_body(
        self,
        *,
        prompt: str,
        schema: dict,
        max_tokens: int | None = None,
        temperature: float | None = None,
        schema_name: str = "extraction_claim",
    ) -> dict[str, Any]:
        """The exact JSON posted. Public so a test can assert on it without a server."""
        return {
            "model": self._config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": (
                self._config.temperature if temperature is None else float(temperature)),
            "max_tokens": (
                self._config.max_output_tokens if max_tokens is None else int(max_tokens)),
            "stream": False,
            # Always sent, never read from config: `ProviderConfig` refuses to hold `true`,
            # so this is the only value that can reach the wire.
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }

    # -- transport -------------------------------------------------------------------------

    def _post_with_retries(self, body: dict) -> tuple[httpx.Response, int]:
        """Bounded retries, transport and 5xx only.

        `max_retries` counts attempts *after* the first, so the default of 2 makes three
        attempts in total.

        A timeout is not retried. On a single-slot local server a timeout means the model is
        still working or wedged, and a second 120-second wait behind the first turns one slow
        passage into a six-minute stall — the caller can decide far better than this loop can.
        """
        url = f"{self._config.base_url}/v1/chat/completions"
        attempts = self._config.max_retries + 1
        last_error: Exception | None = None
        last_detail = "no attempt made"

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.post(
                    url, json=body, timeout=self._config.timeout_seconds)
            except httpx.TimeoutException as exc:
                raise ProviderTimeout(
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
                    raise ProviderResponseError(
                        f"{url}: {last_detail}: {response.text[:200]}")

            if attempt < attempts:
                self._sleep(0.25 * (2 ** (attempt - 1)))

        if isinstance(last_error, httpx.ConnectError):
            raise ProviderUnavailable(
                f"{url}: unreachable after {attempts} attempts: {last_detail}") from last_error
        raise ProviderTransportError(
            f"{url}: failed after {attempts} attempts: {last_detail}") from last_error

    # -- response --------------------------------------------------------------------------

    def _to_result(
        self, response: httpx.Response, schema: dict, latency_ms: float, attempts: int
    ) -> GenerationResult:
        raw_bytes = response.content
        try:
            envelope = json.loads(raw_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderResponseError(f"response body is not JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise ProviderResponseError(
                f"response body is {type(envelope).__name__}, not an object")

        choices = envelope.get("choices") or []
        if not choices:
            raise ProviderResponseError("response carries no choices")
        message = (choices[0] or {}).get("message") or {}
        raw_content = message.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise ProviderResponseError(
                "response carries no assistant content; "
                f"finish_reason={choices[0].get('finish_reason')!r}")

        try:
            content = json.loads(raw_content)
        except ValueError as exc:
            # Never `{}`. An empty object is a legitimate answer for some schemas, and
            # returning one here would make an unparseable response indistinguishable.
            raise ProviderResponseError(
                f"assistant content is not JSON: {exc}: {raw_content[:200]!r}") from exc
        if not isinstance(content, dict):
            raise ProviderResponseError(
                f"assistant content is {type(content).__name__}, not an object")

        violations = tuple(schema_violations(content, schema))
        if violations:
            raise ProviderSchemaError(
                f"response does not satisfy the requested schema: {'; '.join(violations)}",
                violations)

        usage = envelope.get("usage") or {}
        metadata: dict[str, Any] = {
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "system_fingerprint": envelope.get("system_fingerprint"),
            "configured_model": self._config.model,
        }
        # Recorded, never returned as content. §3: the ontology never interprets this dict.
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


def schema_violations(value: Any, schema: dict, path: str = "$") -> list[str]:
    """The subset of JSON Schema this boundary asserts, and nothing more.

    `type`, `required`, `properties`, `additionalProperties: false`, `enum` and `items` — the
    keywords the extraction schemas actually use. Anything else in a schema is ignored rather
    than mis-enforced, because a check that silently means something other than the grammar
    the server was handed is worse than no check.
    """
    findings: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str):
        allowed = _JSON_TYPES.get(expected)
        if allowed is not None:
            # `bool` is a subclass of `int`; a boolean is not a number here.
            wrong_type = not isinstance(value, allowed) or (
                isinstance(value, bool) and expected in ("number", "integer"))
            if wrong_type:
                return [f"{path}: expected {expected}, got {type(value).__name__}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        findings.append(f"{path}: {value!r} is not one of {enum}")

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or ():
            if name not in value:
                findings.append(f"{path}: required property {name!r} is missing")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    findings.append(f"{path}: unexpected property {name!r}")
        for name, subschema in properties.items():
            if name in value and isinstance(subschema, dict):
                findings.extend(schema_violations(value[name], subschema, f"{path}.{name}"))

    items = schema.get("items")
    if isinstance(value, (list, tuple)) and isinstance(items, dict):
        for index, element in enumerate(value):
            findings.extend(schema_violations(element, items, f"{path}[{index}]"))

    return findings
