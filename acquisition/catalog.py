"""BUILD_CATALOG: rebuild global indexes from per-filing metadata.

Per-filing _filing.json is the source of truth; filings.jsonl and artifacts.jsonl are
derived indexes (v0 plan section 9). Nothing appends to them during concurrent downloads,
and they are never consulted to decide what to download.

`documents.jsonl` and `passages.jsonl` are deliberately not produced here -- those names
are reserved for the parsing and normalization phases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import FilingMetadata
from .storage import LocalRawArtifactStore

FILINGS_CATALOG = "filings.jsonl"
ARTIFACTS_CATALOG = "artifacts.jsonl"


@dataclass
class CatalogResult:
    filings_path: Path
    artifacts_path: Path
    filing_count: int
    artifact_count: int
    duplicate_filing_ids: list[str] = field(default_factory=list)
    duplicate_artifact_ids: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.duplicate_filing_ids or self.duplicate_artifact_ids or self.unreadable
        )

    def render(self) -> str:
        lines = [
            f"Filings   : {self.filing_count:>6}  -> {self.filings_path}",
            f"Artifacts : {self.artifact_count:>6}  -> {self.artifacts_path}",
        ]
        if self.duplicate_filing_ids:
            lines.append(f"DUPLICATE filing ids: {len(self.duplicate_filing_ids)}")
            lines.extend(f"  {i}" for i in self.duplicate_filing_ids[:10])
        if self.duplicate_artifact_ids:
            lines.append(f"DUPLICATE artifact ids: {len(self.duplicate_artifact_ids)}")
            lines.extend(f"  {i}" for i in self.duplicate_artifact_ids[:10])
        if self.unreadable:
            lines.append(f"UNREADABLE filing metadata: {len(self.unreadable)}")
            lines.extend(f"  {p}" for p in self.unreadable[:10])
        return "\n".join(lines)


class CatalogBuilder:
    """Deterministic rebuild of the global catalogs."""

    def __init__(self, store: LocalRawArtifactStore, catalog_root: Path) -> None:
        self._store = store
        self._catalog_root = Path(catalog_root)

    def build(self) -> CatalogResult:
        metadata_list, unreadable = self._load_all()

        filing_rows: list[dict[str, Any]] = []
        artifact_rows: list[dict[str, Any]] = []
        for metadata, filing_dir in metadata_list:
            filing_rows.append(_filing_row(metadata, filing_dir))
            artifact_rows.extend(_artifact_rows(metadata, filing_dir))

        filing_rows.sort(key=lambda r: (r["cik10"], r["filing_date"], r["accession"]))
        artifact_rows.sort(
            key=lambda r: (
                r["cik10"],
                r["filing_date"],
                r["accession"],
                # Sequence-less artifacts (full submission, index header) sort last.
                r["sequence"] if r["sequence"] is not None else 1_000_000,
                r["original_filename"],
            )
        )

        filing_rows, duplicate_filings = _dedupe(filing_rows, "filing_id")
        artifact_rows, duplicate_artifacts = _dedupe(artifact_rows, "artifact_id")

        filings_path = _write_jsonl(self._catalog_root / FILINGS_CATALOG, filing_rows)
        artifacts_path = _write_jsonl(self._catalog_root / ARTIFACTS_CATALOG, artifact_rows)

        return CatalogResult(
            filings_path=filings_path,
            artifacts_path=artifacts_path,
            filing_count=len(filing_rows),
            artifact_count=len(artifact_rows),
            duplicate_filing_ids=duplicate_filings,
            duplicate_artifact_ids=duplicate_artifacts,
            unreadable=unreadable,
        )

    def _load_all(self) -> tuple[list[tuple[FilingMetadata, str]], list[str]]:
        loaded: list[tuple[FilingMetadata, str]] = []
        unreadable: list[str] = []
        for path in self._store.iter_finalized_filings():
            try:
                metadata = FilingMetadata(**json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError) as exc:
                unreadable.append(f"{path}: {exc}")
                continue
            relative = path.parent.relative_to(self._store.raw_root).as_posix()
            loaded.append((metadata, relative))
        return loaded, unreadable


def _filing_row(metadata: FilingMetadata, filing_dir: str) -> dict[str, Any]:
    return {
        "filing_id": metadata.filing_id,
        "source": metadata.source,
        "cik10": metadata.cik10,
        "company_name": metadata.company_name,
        "tickers": list(metadata.tickers),
        "accession": metadata.accession,
        "form": metadata.form,
        "form_sanitized": metadata.form_sanitized,
        "filing_date": metadata.filing_date,
        "report_date": metadata.report_date,
        "acceptance_datetime": metadata.acceptance_datetime,
        "items": list(metadata.items),
        "is_amendment": metadata.is_amendment,
        "amends_accession": metadata.amends_accession,
        "is_xbrl": metadata.is_xbrl,
        "is_inline_xbrl": metadata.is_inline_xbrl,
        "artifact_count": len(metadata.artifacts),
        "total_bytes": sum(a.size_bytes for a in metadata.artifacts),
        "filing_dir": filing_dir,
        "run_id": metadata.run_id,
        "fetched_at": metadata.fetched_at,
    }


def _artifact_rows(metadata: FilingMetadata, filing_dir: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in metadata.artifacts:
        rows.append(
            {
                "artifact_id": artifact.artifact_id,
                "filing_id": metadata.filing_id,
                "cik10": metadata.cik10,
                "accession": metadata.accession,
                "form": metadata.form,
                "filing_date": metadata.filing_date,
                "items": list(metadata.items),
                "artifact_kind": artifact.artifact_kind,
                "role": artifact.role,
                "exhibit_type": artifact.exhibit_type,
                "description": artifact.description,
                "sequence": artifact.sequence,
                "original_filename": artifact.original_filename,
                "stored_path": f"{filing_dir}/{artifact.stored_path}",
                "media_type": artifact.media_type,
                "file_extension": artifact.file_extension,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
                "source_url": artifact.source_url,
                "http_status": artifact.http_status,
                "downloaded_at": artifact.downloaded_at,
                "verification_status": artifact.verification_status,
                "low_processing_priority": artifact.low_processing_priority,
            }
        )
    return rows


def _dedupe(rows: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop duplicates by canonical identity, reporting rather than silently collapsing."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    duplicates: list[str] = []
    for row in rows:
        identifier = row[key]
        if identifier in seen:
            duplicates.append(identifier)
            continue
        seen.add(identifier)
        unique.append(row)
    return unique, duplicates


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    """Atomic write. sort_keys makes output byte-identical for identical input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    with staging.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    staging.replace(path)
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a catalog, raising on the first malformed line."""
    rows: list[dict[str, Any]] = []
    if not Path(path).is_file():
        return rows
    with Path(path).open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: malformed JSONL: {exc}") from exc
    return rows
