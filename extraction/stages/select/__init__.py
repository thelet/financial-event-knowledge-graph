"""Typed candidate selection: which passages each lane sees, and why."""

from .alias_evidence import AliasHit, AliasIndex
from .public import (
    EXCLUSION_REASONS,
    INCLUSION_REASONS,
    REASON_CODES,
    CandidateSelector,
    SelectionRequest,
    SelectionResult,
)
from .typed_selector import LanePolicy, SelectionPolicy, TypedCandidateSelector

__all__ = [
    "AliasHit", "AliasIndex", "CandidateSelector", "EXCLUSION_REASONS", "INCLUSION_REASONS",
    "LanePolicy", "REASON_CODES", "SelectionPolicy", "SelectionRequest", "SelectionResult",
    "TypedCandidateSelector",
]
