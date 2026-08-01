"""The provider boundary: configuration, errors, and the one type that crosses it.

`extraction/providers/` is the only package in the repository allowed to know that an HTTP
wire format exists. Everything it hands back is declared here, and every field of
`GenerationResult` is a primitive, a dict or a tuple — a vendor object reaching a lane would
make the lane's determinism a matter of review rather than structure.

The error hierarchy separates *transport* faults from *model* results, because only the first
kind may be retried. A response that parses but does not satisfy the requested schema is the
model's answer; asking again is not a correction, it is a second sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import GenerationResult
from typing import Any

DEFAULT_KIND = "local_openai_compatible"

# The validated runtime. LOCAL_RUNTIME_VALIDATED.md §1, measured 2026-08-01.
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MODEL = "Qwen3.5-9B-Q4_K_M.gguf"
DEFAULT_CONTEXT_TOKENS = 8192
DEFAULT_MAX_OUTPUT_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2


class ProviderError(RuntimeError):
    """Base of every failure this boundary is allowed to raise."""


class ProviderConfigurationError(ProviderError):
    """The configuration itself is unusable. Raised at load time, never at call time."""


class ProviderUnavailable(ProviderError):
    """Nothing is listening, or the server refused the connection."""


class ProviderTimeout(ProviderError):
    """The request exceeded its per-request budget."""


class ProviderTransportError(ProviderError):
    """A transport-level fault that survived every permitted attempt."""


class ProviderResponseError(ProviderError):
    """A response arrived and could not be understood.

    Covers a non-retryable HTTP status, a missing choice, and content that is not JSON. Never
    a silent `{}` — an empty object is a legitimate model answer for some schemas, so
    conflating the two would make an unparseable response indistinguishable from a real one.
    """


class ProviderSchemaError(ProviderError):
    """Valid JSON that does not satisfy the requested schema.

    A model result, not a fault. Deliberately not retried: the server was asked for
    schema-constrained output and answered, and re-asking at temperature 0 would return the
    same thing while charging for it twice.
    """

    def __init__(self, message: str, violations: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.violations = violations


@dataclass(frozen=True)
class HealthStatus:
    """Structured, never an exception.

    A local server that is not running is an ordinary state for a caller to branch on — a
    lane deciding whether it can run at all should not have to catch an exception to find
    out.
    """

    ok: bool
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    """The declarative half, loaded from `config/extraction.yaml` under `provider:`.

    `enable_thinking` exists only so that turning it on is an explicit, rejected act rather
    than a silent default. See `validated()`.
    """

    kind: str = DEFAULT_KIND
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    enable_thinking: bool = False

    @classmethod
    def from_config(cls, config: dict) -> "ProviderConfig":
        """Load and validate. A bad configuration fails here, not on the first request."""
        provider = config.get("provider") or {}
        loaded = cls(
            kind=str(provider.get("kind", DEFAULT_KIND)),
            base_url=str(provider.get("base_url", DEFAULT_BASE_URL)).rstrip("/"),
            model=str(provider.get("model", DEFAULT_MODEL)),
            context_tokens=int(provider.get("context_tokens", DEFAULT_CONTEXT_TOKENS)),
            max_output_tokens=int(
                provider.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
            temperature=float(provider.get("temperature", DEFAULT_TEMPERATURE)),
            timeout_seconds=float(provider.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            max_retries=int(provider.get("max_retries", DEFAULT_MAX_RETRIES)),
            enable_thinking=bool(provider.get("enable_thinking", False)),
        )
        return loaded.validated()

    def validated(self) -> "ProviderConfig":
        """Reject a configuration that cannot produce structured output.

        `enable_thinking: true` is refused outright rather than warned about. Measured
        2026-08-01 (LOCAL_RUNTIME_VALIDATED.md §4): with thinking on, a four-field schema
        request spent all 900 tokens reasoning and returned empty content; with it off the
        same request returned conformant JSON in 59 tokens. The failure presents as an
        extraction failure rather than a budget one, which is the worst shape a defect can
        take — a lane that cannot run is better than one that silently reads nothing.

        Revisiting is a per-request decision for a case that provably needs deliberation,
        never a global default.
        """
        if self.enable_thinking:
            raise ProviderConfigurationError(
                "provider.enable_thinking must be false: with thinking enabled a four-field "
                "schema request consumed its entire 900-token budget reasoning and returned "
                "empty content (LOCAL_RUNTIME_VALIDATED.md §4). If one passage genuinely "
                "needs deliberation, enable it for that request, not for the provider."
            )
        if not self.base_url:
            raise ProviderConfigurationError("provider.base_url is required")
        if self.max_retries < 0:
            raise ProviderConfigurationError("provider.max_retries must not be negative")
        if self.max_output_tokens <= 0:
            raise ProviderConfigurationError("provider.max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ProviderConfigurationError("provider.timeout_seconds must be positive")
        if self.max_output_tokens >= self.context_tokens:
            raise ProviderConfigurationError(
                f"provider.max_output_tokens ({self.max_output_tokens}) leaves no room for a "
                f"prompt in provider.context_tokens ({self.context_tokens})"
            )
        return self
