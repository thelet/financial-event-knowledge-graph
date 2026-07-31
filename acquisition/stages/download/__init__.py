"""DOWNLOAD: fetch artifacts and finalize filings atomically."""

from .stage import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_REPAIRED,
    STATUS_SKIPPED,
    DownloadRequest,
    DownloadResult,
    LocalDownloadStage,
)

__all__ = [
    "DownloadRequest",
    "DownloadResult",
    "LocalDownloadStage",
    "STATUS_DOWNLOADED",
    "STATUS_FAILED",
    "STATUS_REPAIRED",
    "STATUS_SKIPPED",
]
