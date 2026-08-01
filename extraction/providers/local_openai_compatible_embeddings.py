"""An OpenAI-compatible local embedding server, behind the `EmbeddingProvider` port.

A sibling of `local_openai_compatible.py`, not a new pattern: same error hierarchy, same
retry boundary, same rule that nothing vendor-shaped crosses the boundary — a caller gets
tuples of floats and never a response mapping.

Written against the llama.cpp server recorded in STAGE_09 §1.1 (`--pooling last
--embd-normalize 2`, port 8081), read off the wire *(verified 2026-08-01)*:

    POST /v1/embeddings   {"model": "…", "input": ["…", "…"]}
    -> {"model":"…","object":"list","usage":{"prompt_tokens":2,"total_tokens":2},
        "data":[{"embedding":[…1024 floats…],"index":0,"object":"embedding"}, …]}

Three observations shape this module.

**`model` echoes the request string.** Sending `{"model":"m"}` returns `"model":"m"`. So
`model_id` is the configured name and the response's field is never read — a cache keyed on
what the server said would key on what we asked.

**Vectors arrive L2-normalized** (norm 0.9999999863 measured on a live response), which is
what makes a dot product a cosine downstream. Trusting that silently would be a defect, so
`embed` checks the norm and raises rather than returning a vector the caller would treat as a
unit one.

**`data` is not promised in request order.** Each element carries `index`; they are reordered
by it here rather than zipped positionally, because a batch silently permuted would attach
every vector to the wrong text and produce a plausible, wrong similarity table.

**The server is not request-independent, and STAGE_09 §1.1 is wrong about it** *(measured
2026-08-01, and the correction that cost the most to find)*. §1.1 records "two identical
requests returned bit-identical vectors". They do not. The vector depends on the request that
preceded it in the slot: embedding `A`, then `C`, then `C` again gives two different `C`
vectors, while embedding `A, C` and later `A, C` again gives the same one. The difference is
up to 1.3e-4 per component — cosine 0.9999996, far below anything a ranking notices, and
fatal to the claim that a cache rebuild is byte-identical. Batch position matters for the same
reason: a text at position 2 of three differs from the same text sent alone by 3.2e-4.
`cache_prompt: false` does not change any of this; it was tried and removed. What follows for
the cache is in `vector_cache.py`.

An input longer than the server's context returns **HTTP 400** with an `exceed_context_size`
error body *(measured 2026-08-01: 3,002 tokens against `-c 2048`)*, which the shared retry
rule already treats as non-retryable — asking again cannot make the text shorter.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Sequence

import httpx

from .public import (
    EmbeddingConfig,
    HealthStatus,
    ProviderResponseError,
    ProviderTimeout,
    ProviderTransportError,
    ProviderUnavailable,
)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

HEALTH_TIMEOUT_CEILING_SECONDS = 10.0

# How far a returned vector's norm may sit from 1.0 before it is refused. The measured
# deviation on this server is 1.4e-8; 1e-3 is loose enough that a different quantization or a
# different pooling mode still passes, and tight enough that an un-normalized vector — norm 12
# to 30 for this model without `--embd-normalize 2` — cannot.
UNIT_NORM_TOLERANCE = 1e-3


class LocalOpenAICompatibleEmbeddingProvider:
    """Satisfies `extraction.contracts.EmbeddingProvider` against a local server.

    Stateless beyond its client and its config. The vector *cache* is deliberately not here:
    it is a durability contract with its own identity rules and belongs to the stage that
    owns the concept vocabulary, while this class owns one HTTP call.
    """

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._config = config.validated()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.timeout_seconds)
        self._sleep = sleep if sleep is not None else time.sleep

    # -- lifecycle -------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LocalOpenAICompatibleEmbeddingProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def model_id(self) -> str:
        """The configured name, never the response's. See the module docstring."""
        return self._config.model

    @property
    def dimensions(self) -> int:
        return self._config.dimensions

    @property
    def config(self) -> EmbeddingConfig:
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

    # -- embedding -------------------------------------------------------------------------

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Vectors for `texts`, in the order given, batched by `config.batch_size`.

        An empty request returns an empty tuple without touching the network: llama.cpp
        answers `{"input": []}` with HTTP 500, and a caller with nothing to embed has not made
        an error.
        """
        if not texts:
            return ()
        vectors: list[tuple[float, ...]] = []
        batch = self._config.batch_size
        for start in range(0, len(texts), batch):
            vectors.extend(self._embed_batch(tuple(texts[start:start + batch])))
        return tuple(vectors)

    def request_body(self, texts: Sequence[str]) -> dict[str, Any]:
        """The exact JSON posted. Public so a test can assert on it without a server.

        Deliberately just `model` and `input`. **`cache_prompt: false` was tried and removed**
        *(measured 2026-08-01)*: it was reached for to fix the non-determinism below and
        changes nothing at all — with it and without it, the same text embedded after the same
        predecessor gives bit-identical vectors and after a different predecessor differs by
        the same 1.3e-4. A parameter that does not do what it is there for is worse than
        absent, because the next reader will believe it.
        """
        return {"model": self._config.model, "input": list(texts)}

    def _embed_batch(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        response = self._post_with_retries(self.request_body(texts))
        return self._to_vectors(response, len(texts))

    # -- transport -------------------------------------------------------------------------

    def _post_with_retries(self, body: dict) -> httpx.Response:
        """Bounded retries, transport and 5xx only. The generation adapter's rule verbatim.

        A timeout is not retried, for the same reason it is not there: on a single-slot local
        server a timeout means the model is still working, and a second full wait behind the
        first turns one slow batch into a multi-minute stall.
        """
        url = f"{self._config.base_url}/v1/embeddings"
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
                    return response
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

    def _to_vectors(
        self, response: httpx.Response, expected: int
    ) -> tuple[tuple[float, ...], ...]:
        try:
            envelope = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"response body is not JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise ProviderResponseError(
                f"response body is {type(envelope).__name__}, not an object")

        data = envelope.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise ProviderResponseError(
                f"response carries {len(data) if isinstance(data, list) else 'no'} embeddings "
                f"for {expected} inputs")

        ordered: list[tuple[float, ...] | None] = [None] * expected
        for position, element in enumerate(data):
            if not isinstance(element, dict):
                raise ProviderResponseError(
                    f"embedding {position} is {type(element).__name__}, not an object")
            index = element.get("index", position)
            if not isinstance(index, int) or not 0 <= index < expected:
                raise ProviderResponseError(
                    f"embedding {position} carries index {index!r}, outside 0..{expected - 1}")
            if ordered[index] is not None:
                raise ProviderResponseError(f"two embeddings carry index {index}")
            ordered[index] = self._vector(element.get("embedding"), index)

        missing = [i for i, vector in enumerate(ordered) if vector is None]
        if missing:
            raise ProviderResponseError(f"no embedding returned for inputs {missing}")
        return tuple(vector for vector in ordered if vector is not None)

    def _vector(self, raw: Any, index: int) -> tuple[float, ...]:
        if not isinstance(raw, list) or not raw:
            raise ProviderResponseError(f"embedding {index} carries no vector")
        try:
            vector = tuple(float(value) for value in raw)
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError(
                f"embedding {index} carries a non-numeric component: {exc}") from exc
        if len(vector) != self._config.dimensions:
            raise ProviderResponseError(
                f"embedding {index} has {len(vector)} dimensions, not the configured "
                f"{self._config.dimensions}")
        norm = math.sqrt(sum(value * value for value in vector))
        if abs(norm - 1.0) > UNIT_NORM_TOLERANCE:
            raise ProviderResponseError(
                f"embedding {index} has norm {norm:.6f}, not 1.0: the server is not running "
                f"with --embd-normalize 2, and every cosine downstream would be wrong by an "
                f"unknown factor")
        return vector
