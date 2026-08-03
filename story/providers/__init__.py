"""Providers: the only place in `story/` an external system is reached.

Two of them as of S6 — Bolt at S0c and an OpenAI-compatible HTTP server here — and they share
one property that the rest of the package is defined by not having: they know a wire format.
`story/core/` must stay constructible with no database, no model server and no environment,
and `story/stages/` reaches both of these through a protocol in `story/contracts.py` rather
than by importing anything here.

**Nothing is imported eagerly by this file, and that rule is the point.** Python runs a
package's `__init__` before any submodule of it, so a single eager `from .neo4j_connection
import …` would put `neo4j` into `sys.modules` for a stage that names only
`providers.public`, and `from .openai_compatible import …` would do the same for `httpx`.
`extraction/providers/__init__.py:44-56` records the measurement that forced the indirection
upstream: an eager re-export made "no HTTP client is reachable from a lane" true in the source
and false in the interpreter, where the import-graph test could not see it. §15.2 requires
that indirection to be preserved, and it is — S0 kept it by re-exporting nothing at all, and
S6 keeps the same guarantee while giving the composition root the names it needs.

**The Neo4j adapter is deliberately absent from `_LAZY`.** An entry would create a second name
for it — `story.providers.open_read_executor` — and
`tests/story/test_story_package_structure.py::
test_the_composition_root_is_the_only_module_outside_providers_that_reaches_the_adapter`
matches on the dotted module name, so the alias would be a way past the rule that nothing but
`story/context.py` may build a driver. A convenience that defeats a structural test is not a
convenience. `story/context.py` imports the adapter module directly and that stays true.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GenerationStore",
    "MissingGenerationError",
    "PINNED_TEMPERATURE",
    "ReplayingStoryGenerationProvider",
    "StoredGeneration",
    "StoryOpenAICompatibleProvider",
    "StoryProviderConfig",
    "StoryProviderConfigurationError",
    "StoryProviderError",
    "StoryProviderResponseError",
    "StoryProviderSchemaError",
    "StoryProviderTimeout",
    "StoryProviderTransportError",
    "StoryProviderUnavailable",
    "load_provider_config",
    "request_identity",
    "schema_violations",
    "validate_portable_schema",
]

#: Resolved on first attribute access. `public` and `portable_schema` carry no wire format and
#: could safely be imported eagerly, but they are routed the same way so that the rule reads as
#: one rule: this file imports nothing until something is asked for.
_LAZY = {
    "GenerationStore": ".generation_store",
    "MissingGenerationError": ".generation_store",
    "ReplayingStoryGenerationProvider": ".generation_store",
    "StoredGeneration": ".generation_store",
    "request_identity": ".generation_store",
    "StoryOpenAICompatibleProvider": ".openai_compatible",
    "PINNED_TEMPERATURE": ".public",
    "StoryProviderConfig": ".public",
    "StoryProviderConfigurationError": ".public",
    "StoryProviderError": ".public",
    "StoryProviderResponseError": ".public",
    "StoryProviderSchemaError": ".public",
    "StoryProviderTimeout": ".public",
    "StoryProviderTransportError": ".public",
    "StoryProviderUnavailable": ".public",
    "load_provider_config": ".public",
    "schema_violations": ".portable_schema",
    "validate_portable_schema": ".portable_schema",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
