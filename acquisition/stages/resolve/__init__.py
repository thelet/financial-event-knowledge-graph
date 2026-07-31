"""RESOLVE: expand each filing into its exact artifact set.

The SGML header parser is an implementation detail and is not exported.
"""

from .public import (
    ANOMALY_CASE_COLLISION,
    ANOMALY_HEADER_NOT_IN_INDEX,
    ANOMALY_INDEX_NOT_IN_HEADER,
    ANOMALY_MULTIPLE_PRIMARY,
    ANOMALY_NO_PRIMARY,
    ANOMALY_PARSE_FAILED,
    ANOMALY_UNRECOGNIZED_TYPE,
    ResolveRequest,
    ResolveResult,
    ResolveStage,
)
from .sgml_resolution import SgmlResolveStage
from .summary import summarize

__all__ = [
    "ANOMALY_CASE_COLLISION",
    "ANOMALY_HEADER_NOT_IN_INDEX",
    "ANOMALY_INDEX_NOT_IN_HEADER",
    "ANOMALY_MULTIPLE_PRIMARY",
    "ANOMALY_NO_PRIMARY",
    "ANOMALY_PARSE_FAILED",
    "ANOMALY_UNRECOGNIZED_TYPE",
    "ResolveRequest",
    "ResolveResult",
    "ResolveStage",
    "SgmlResolveStage",
    "summarize",
]
