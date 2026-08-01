"""Ontology candidate scoping: which concepts a lane may consider, and why.

Two scopes. `LexicalOntologyCandidateScope` is offline and produces protected reasons only;
`HybridOntologyCandidateScope` wraps it and adds `semantic_neighbour` candidates from an
`EmbeddingProvider`. The second may only add to the first — `public.py` states the rule and
`hybrid.py` is built so that no code path can break it.
"""

from .concept_rendering import (
    RENDERER_VERSION,
    TEXT_NORMALIZATION_VERSION,
    normalize_for_embedding,
    render_concept,
    render_concepts,
)
from .hybrid import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_TOP_K,
    HybridOntologyCandidateScope,
    ScopingConfig,
    SemanticNeighbour,
    concept_vectors,
)
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
    SEMANTIC_NEIGHBOUR,
    STABLE_CORE,
    STABLE_CORE_CONCEPTS,
    TABLE_LABEL,
    CandidateScope,
    ConfusionExpansion,
    ScopedConcept,
)
from .vector_cache import (
    CacheIdentity,
    CachedVector,
    MissingVectorError,
    StaleVectorCacheError,
    VectorCache,
    VectorCacheError,
    cosine,
    decode_vector,
    encode_vector,
    text_key,
)

__all__ = [
    "AMBIGUOUS_ALIAS", "CANONICAL_LABEL", "CONFUSION_SIBLING", "CacheIdentity",
    "CachedVector", "CandidateScope", "ConfusionExpansion", "DEFAULT_MIN_SIMILARITY",
    "DEFAULT_TOP_K", "EXACT_ALIAS", "HybridOntologyCandidateScope", "KNOWN_INSTANCE",
    "LexicalOntologyCandidateScope", "MissingVectorError", "NORMALIZED_ALIAS",
    "PROTECTED_REASONS", "RENDERER_VERSION", "SCOPE_NAME", "SCOPE_REASONS", "SCOPE_VERSION",
    "SEMANTIC_NEIGHBOUR", "STABLE_CORE", "STABLE_CORE_CONCEPTS", "ScopedConcept",
    "ScopingConfig", "SemanticNeighbour", "StaleVectorCacheError", "TABLE_LABEL",
    "TEXT_NORMALIZATION_VERSION", "VectorCache", "VectorCacheError", "concept_vectors",
    "cosine", "decode_vector", "encode_vector", "normalize_for_embedding", "render_concept",
    "render_concepts", "text_key",
]
