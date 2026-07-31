"""DOWNLOAD: fetch artifacts and finalize filings atomically.

Consumes a resolved artifact manifest. Per filing: stage, download, hash, write metadata,
finalize. Writes nothing to the global catalogs -- those are rebuilt separately from
per-filing metadata (v0 plan section 9).
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import identity
from .config import CompanyConfig
from .models import ArtifactRecord, FilingMetadata, FilingRecord, StoredArtifact
from .runmeta import FETCHER_VERSION, dependency_versions, utc_now_iso
from .sec_client import SecClient
from .storage import LocalRawArtifactStore

STATUS_DOWNLOADED = "downloaded"
STATUS_SKIPPED = "skipped"
STATUS_REPAIRED = "repaired"
STATUS_FAILED = "failed"


@dataclass
class FilingOutcome:
    filing_id: str
    accession: str
    status: str
    artifact_count: int = 0
    bytes_downloaded: int = 0
    reason: str | None = None


@dataclass
class DownloadSummary:
    outcomes: list[FilingOutcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def bytes_downloaded(self) -> int:
        return sum(o.bytes_downloaded for o in self.outcomes)

    @property
    def failures(self) -> list[FilingOutcome]:
        return [o for o in self.outcomes if o.status == STATUS_FAILED]

    def render(self) -> str:
        lines = [
            f"Downloaded : {self.count(STATUS_DOWNLOADED)}",
            f"Repaired   : {self.count(STATUS_REPAIRED)}",
            f"Skipped    : {self.count(STATUS_SKIPPED)}  (already valid and complete)",
            f"Failed     : {self.count(STATUS_FAILED)}",
            f"Bytes      : {self.bytes_downloaded / 1_048_576:.1f} MiB",
        ]
        for failure in self.failures:
            lines.append(f"  FAILED {failure.accession}: {failure.reason}")
        return "\n".join(lines)


class ArtifactDownloader:
    """Downloads and finalizes filings from a resolved artifact manifest."""

    def __init__(
        self,
        client: SecClient,
        store: LocalRawArtifactStore,
        company: CompanyConfig,
        *,
        run_id: str,
    ) -> None:
        self._client = client
        self._store = store
        self._company = company
        self._run_id = run_id

    def download_all(
        self,
        filings: Iterable[FilingRecord],
        artifacts: Iterable[ArtifactRecord],
        *,
        max_workers: int = 4,
        force: bool = False,
        progress: Callable[[int, int, FilingOutcome], None] | None = None,
    ) -> DownloadSummary:
        filing_list = list(filings)
        by_accession: dict[str, list[ArtifactRecord]] = defaultdict(list)
        for artifact in artifacts:
            by_accession[artifact.accession].append(artifact)

        summary = DownloadSummary(outcomes=[None] * len(filing_list))  # type: ignore[list-item]

        def work(index: int) -> None:
            filing = filing_list[index]
            summary.outcomes[index] = self.download_filing(
                filing, by_accession.get(filing.accession, []), force=force
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(work, i): i for i in range(len(filing_list))}
            done = 0
            for future in as_completed(futures):
                future.result()
                done += 1
                if progress:
                    progress(done, len(filing_list), summary.outcomes[futures[future]])

        return summary

    # -- one filing --------------------------------------------------------------------

    def download_filing(
        self,
        filing: FilingRecord,
        artifacts: list[ArtifactRecord],
        *,
        force: bool = False,
    ) -> FilingOutcome:
        if not artifacts:
            return FilingOutcome(
                filing_id=filing.filing_id,
                accession=filing.accession,
                status=STATUS_FAILED,
                reason="no artifacts resolved for this filing",
            )

        existing = None if force else self._store.inspect_finalized_filing(filing.filing_dir)
        already_complete = existing is not None and self._is_complete(
            filing, existing, artifacts
        )
        if already_complete:
            return FilingOutcome(
                filing_id=filing.filing_id,
                accession=filing.accession,
                status=STATUS_SKIPPED,
                artifact_count=len(existing.artifacts),
            )

        was_present = self._store.filing_dir(filing.filing_dir).exists()
        staging = self._store.prepare_temporary_filing(self._run_id, filing.accession)
        try:
            stored, downloaded_bytes = self._download_artifacts(staging, artifacts)
            metadata = self._build_metadata(filing, stored)
            # Written last: its presence inside a finalized directory is the completion
            # marker for the whole filing.
            self._store.write_filing_metadata(staging, metadata)
            self._store.finalize_filing(staging, filing.filing_dir)
        except Exception as exc:  # noqa: BLE001 - reported per filing, run continues
            self._store.discard_staging(staging)
            return FilingOutcome(
                filing_id=filing.filing_id,
                accession=filing.accession,
                status=STATUS_FAILED,
                reason=f"{type(exc).__name__}: {exc}",
            )

        return FilingOutcome(
            filing_id=filing.filing_id,
            accession=filing.accession,
            status=STATUS_REPAIRED if was_present else STATUS_DOWNLOADED,
            artifact_count=len(stored),
            bytes_downloaded=downloaded_bytes,
        )

    def _download_artifacts(
        self, staging: Path, artifacts: list[ArtifactRecord]
    ) -> tuple[list[StoredArtifact], int]:
        stored: list[StoredArtifact] = []
        total = 0
        for artifact in artifacts:
            destination = self._store.artifact_destination(staging, artifact.stored_path)
            outcome = self._client.download_to(artifact.source_url, destination)
            total += outcome.size_bytes
            stored.append(
                StoredArtifact(
                    artifact_id=artifact.artifact_id,
                    artifact_kind=artifact.artifact_kind,
                    role=artifact.role,
                    exhibit_type=artifact.exhibit_type,
                    description=artifact.description,
                    sequence=artifact.sequence,
                    original_filename=artifact.original_filename,
                    stored_path=artifact.stored_path,
                    media_type=artifact.media_type,
                    file_extension=artifact.file_extension,
                    size_bytes=outcome.size_bytes,
                    sha256=outcome.sha256,
                    source_url=artifact.source_url,
                    http_status=outcome.status,
                    etag=outcome.etag,
                    last_modified=outcome.last_modified,
                    download_attempts=outcome.attempts,
                    downloaded_at=utc_now_iso(),
                    verification_status="verified",
                    low_processing_priority=artifact.low_processing_priority,
                )
            )
        return stored, total

    def _build_metadata(
        self, filing: FilingRecord, stored: list[StoredArtifact]
    ) -> FilingMetadata:
        return FilingMetadata(
            filing_id=filing.filing_id,
            source=filing.source,
            cik=self._company.cik,
            cik10=filing.cik10,
            company_name=filing.company_name,
            tickers=list(filing.tickers),
            accession=filing.accession,
            accession_nodash=filing.accession_nodash,
            form=filing.form,
            form_sanitized=filing.form_sanitized,
            is_amendment=filing.is_amendment,
            amends_accession=filing.amends_accession,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            acceptance_datetime=filing.acceptance_datetime,
            items=list(filing.items),
            file_number=filing.file_number,
            act=filing.act,
            film_number=filing.film_number,
            is_xbrl=filing.is_xbrl,
            is_inline_xbrl=filing.is_inline_xbrl,
            size_reported=filing.size_reported,
            source_index_url=filing.source_index_url,
            source_index_headers_url=filing.source_index_headers_url,
            run_id=self._run_id,
            fetched_at=utc_now_iso(),
            fetcher_version=FETCHER_VERSION,
            tool_versions=dependency_versions(),
            artifacts=stored,
        )

    def _is_complete(
        self,
        filing: FilingRecord,
        metadata: FilingMetadata,
        expected: list[ArtifactRecord],
    ) -> bool:
        """All four skip conditions from v0 plan section 6 must hold."""
        expected_ids = {a.artifact_id for a in expected}
        actual_ids = {a.artifact_id for a in metadata.artifacts}
        if expected_ids != actual_ids:
            return False
        return not self._store.verify_filing_contents(filing.filing_dir, metadata)
