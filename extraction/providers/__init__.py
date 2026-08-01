"""Generation providers: the only place an HTTP wire format is known.

Nothing under `extraction/stages/` may import this package, and an executable test enforces
it. A lane that *could* reach a provider is one that might, and the deterministic table
lane's guarantee would then rest on review rather than structure.
"""

from .local_openai_compatible import (
    LocalOpenAICompatibleGenerationProvider,
    schema_violations,
)
from .public import (
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
    "GenerationResult",
    "HealthStatus",
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
