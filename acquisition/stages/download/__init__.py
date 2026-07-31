"""DOWNLOAD: fetch artifacts and finalize filings atomically."""

from .local_download import LocalDownloadStage
from .public import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_REPAIRED,
    STATUS_SKIPPED,
    DownloadRequest,
    DownloadResult,
    DownloadStage,
    DownloadSummary,
    FilingOutcome,
)

__all__ = [
    "DownloadRequest",
    "DownloadResult",
    "DownloadStage",
    "DownloadSummary",
    "FilingOutcome",
    "LocalDownloadStage",
    "STATUS_DOWNLOADED",
    "STATUS_FAILED",
    "STATUS_REPAIRED",
    "STATUS_SKIPPED",
]
