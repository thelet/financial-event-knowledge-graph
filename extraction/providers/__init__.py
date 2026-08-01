"""Providers: the only place an HTTP wire format is known.

Two of them now — generation and embeddings, two local servers on two ports — sharing one
error hierarchy and one retry rule. Nothing under `extraction/stages/` may import this
package, and an executable test enforces it. A lane that *could* reach a provider is one that
might, and the deterministic table lane's guarantee would then rest on review rather than
structure. The hybrid scope takes an `EmbeddingProvider` by the contract in
`extraction.contracts`, so it never sees this package either.
"""

from .local_openai_compatible import (
    LocalOpenAICompatibleGenerationProvider,
    schema_violations,
)
from .local_openai_compatible_embeddings import (
    LocalOpenAICompatibleEmbeddingProvider,
)
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
