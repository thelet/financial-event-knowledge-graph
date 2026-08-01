"""Claim extraction: normalized passages to validated ontology claims.

Provider-independent by rule. The deterministic table lane needs no model at all; the
narrative lane's generation provider dies at its own adapter boundary, and nothing
downstream imports a vendor type or knows a model was involved.
"""

from .contracts import (
    ClaimLane,
    EmbeddingProvider,
    GenerationProvider,
    OntologyCandidateScope,
    PassageSource,
    PipelineStage,
    StageResult,
)

__all__ = [
    "ClaimLane",
    "EmbeddingProvider",
    "GenerationProvider",
    "OntologyCandidateScope",
    "PassageSource",
    "PipelineStage",
    "StageResult",
]
