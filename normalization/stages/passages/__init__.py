"""PASSAGES: build deterministic passages from a normalized document."""

from .public import (
    EMPTY_BLOCK,
    EXCLUSION_REASONS,
    HEADING_AS_METADATA,
    PAGE_FURNITURE,
    POLICY_EXCLUDED,
    PassageStrategy,
)
from .section_passages import SectionAwarePassageStrategy

__all__ = [
    "PassageStrategy", "SectionAwarePassageStrategy", "EXCLUSION_REASONS",
    "PAGE_FURNITURE", "EMPTY_BLOCK", "HEADING_AS_METADATA", "POLICY_EXCLUDED",
]
