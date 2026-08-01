"""Lexical ontology candidate scoping: which concepts a lane may consider, and why."""

from .lexical import SCOPE_NAME, SCOPE_VERSION, LexicalOntologyCandidateScope
from .public import (
    AMBIGUOUS_ALIAS,
    CANONICAL_LABEL,
    CONFUSION_SIBLING,
    EXACT_ALIAS,
    KNOWN_INSTANCE,
    NORMALIZED_ALIAS,
    PROTECTED_REASONS,
    SCOPE_REASONS,
    STABLE_CORE,
    STABLE_CORE_CONCEPTS,
    TABLE_LABEL,
    CandidateScope,
    ConfusionExpansion,
    ScopedConcept,
)

__all__ = [
    "AMBIGUOUS_ALIAS", "CANONICAL_LABEL", "CONFUSION_SIBLING", "CandidateScope",
    "ConfusionExpansion", "EXACT_ALIAS", "KNOWN_INSTANCE", "LexicalOntologyCandidateScope",
    "NORMALIZED_ALIAS", "PROTECTED_REASONS", "SCOPE_NAME", "SCOPE_REASONS", "SCOPE_VERSION",
    "STABLE_CORE", "STABLE_CORE_CONCEPTS", "ScopedConcept", "TABLE_LABEL",
]
