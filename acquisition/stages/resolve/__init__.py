"""RESOLVE: expand each filing into its exact artifact set.

The SGML header parser is an implementation detail of this stage and is not exported.
"""

from .stage import ResolveRequest, ResolveResult, SgmlResolveStage, summarize

__all__ = ["ResolveRequest", "ResolveResult", "SgmlResolveStage", "summarize"]
