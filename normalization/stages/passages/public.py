"""Public contract for passage generation.

A *strategy* protocol, like the parser: passage building has no independent run-to-disk
semantics. Character-based, never tokenizer-based — making normalization depend on an
extraction model's tokenizer would couple this layer to a provider, which is exactly what
it exists to prevent. Token counts may appear in reports as diagnostics only.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...core.models import ExcludedBlock, NormalizedDocument, Passage

# Why a block was not turned into passage content.
PAGE_FURNITURE = "PAGE_FURNITURE"
EMPTY_BLOCK = "EMPTY_BLOCK"
HEADING_AS_METADATA = "HEADING_AS_METADATA"
POLICY_EXCLUDED = "POLICY_EXCLUDED"
EXCLUSION_REASONS = frozenset({PAGE_FURNITURE, EMPTY_BLOCK, HEADING_AS_METADATA, POLICY_EXCLUDED})


@runtime_checkable
class PassageStrategy(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def build(self, document: NormalizedDocument) -> tuple[list[Passage], list[ExcludedBlock]]: ...
