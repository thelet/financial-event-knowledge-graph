"""VERIFY: check the normalized corpus against its authoritative inputs."""

from .corpus_verification import CorpusVerifyStage
from .public import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Finding,
    VerificationReport,
    VerifyRequest,
    VerifyResult,
    VerifyStage,
)

__all__ = [
    "CorpusVerifyStage", "Finding", "VerificationReport", "VerifyRequest", "VerifyResult",
    "VerifyStage", "SEVERITY_ERROR", "SEVERITY_WARNING",
]
