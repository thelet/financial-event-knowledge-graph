"""DISCOVER: build the filing-level manifest from the SEC submissions API.

Exports the contract plus the default concrete implementation. Provider internals --
submissions-array transposition, continuation files, row conversion -- stay private.
"""

from .public import DiscoverRequest, DiscoverResult, DiscoverStage
from .sec_discovery import SecDiscoverStage
from .summary import summarize

__all__ = [
    "DiscoverRequest",
    "DiscoverResult",
    "DiscoverStage",
    "SecDiscoverStage",
    "summarize",
]
