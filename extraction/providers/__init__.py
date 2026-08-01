"""Providers: the only place an HTTP wire format is known.

Two of them now — generation and embeddings, two local servers on two ports — sharing one
error hierarchy and one retry rule. Nothing under `extraction/stages/` may import this
package, and an executable test enforces it. A lane that *could* reach a provider is one that
might, and the deterministic table lane's guarantee would then rest on review rather than
structure. The hybrid scope takes an `EmbeddingProvider` by the contract in
`extraction.contracts`, so it never sees this package either.
"""

from typing import Any

from .public import (
    EmbeddingConfig,
    GenerationResult,
    HealthStatus,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
    ProviderSchemaError,
    ProviderTimeout,
    ProviderTransportError,
    ProviderUnavailable,
)

__all__ = [
    "EmbeddingConfig",
    "GenerationResult",
    "HealthStatus",
    "LocalOpenAICompatibleEmbeddingProvider",
    "LocalOpenAICompatibleGenerationProvider",
    "ProviderConfig",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderResponseError",
    "ProviderSchemaError",
    "ProviderTimeout",
    "ProviderTransportError",
    "ProviderUnavailable",
    "schema_violations",
]

# The two adapters are resolved on first attribute access rather than imported here, because
# an eager import made a false claim true only on paper: the narrative lane names nothing but
# `providers.public` — config and the error taxonomy, no wire format — yet importing
# `extraction.stages.narrative` loaded `httpx` anyway, since Python runs a package's
# `__init__` before any submodule of it. The import-graph test could not see that, and
# "no HTTP client is reachable from a lane at runtime" was false in the interpreter while
# true in the source. Measured 2026-08-02: with this indirection, importing the narrative
# package leaves `httpx` out of `sys.modules`.
_LAZY = {
    "LocalOpenAICompatibleGenerationProvider": ".local_openai_compatible",
    "schema_violations": ".local_openai_compatible",
    "LocalOpenAICompatibleEmbeddingProvider": ".local_openai_compatible_embeddings",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
