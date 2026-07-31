"""RESOLVE: expand each filing into the exact set of artifacts to download.

Types come from the SGML header in <accession>-index-headers.html, which is authoritative.
index.json contributes expected sizes only -- its `type` field is an icon reference
(v0 plan sections 13.1-13.2).

The header lists SEC-generated rendering output (R*.htm, FilingSummary.xml,
MetaLinks.json, report.css, Show.js, -xbrl.zip, *_htm.xml) alongside filer-submitted
documents, marked with DESCRIPTION "IDEA: ...". These are derived from the filing rather
than part of it and are excluded unless include.render_artifacts is enabled (v0 plan
section 13.7).
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from .core import identity
from .core.config import IncludeConfig
from .core.models import ArtifactRecord, FilingRecord, ResolutionAnomaly
from .core.sec_client import SecClient
from .sgml import ParsedHeader, SgmlParseError, parse_index_headers

SOURCE_SUBDIR = "source"

ANOMALY_UNRECOGNIZED_TYPE = "unrecognized_type"
ANOMALY_NO_PRIMARY = "no_primary_document"
ANOMALY_MULTIPLE_PRIMARY = "multiple_primary_documents"
ANOMALY_HEADER_NOT_IN_INDEX = "header_document_missing_from_index"
ANOMALY_INDEX_NOT_IN_HEADER = "index_file_not_in_header"
ANOMALY_CASE_COLLISION = "case_insensitive_filename_collision"
ANOMALY_PARSE_FAILED = "header_parse_failed"


class ArtifactResolver:
    """Resolves the artifact set for filings using authoritative SEC metadata."""

    def __init__(self, client: SecClient, include: IncludeConfig) -> None:
        self._client = client
        self._include = include

    # -- single filing -----------------------------------------------------------------

    def resolve_filing(
        self, filing: FilingRecord, cik: int | str
    ) -> tuple[list[ArtifactRecord], list[ResolutionAnomaly]]:
        anomalies: list[ResolutionAnomaly] = []

        header_name = identity.index_headers_filename(filing.accession)
        header_url = identity.artifact_url(cik, filing.accession, header_name)
        raw_header = self._client.get_bytes(header_url).content.decode(
            "utf-8", errors="replace"
        )
        try:
            parsed = parse_index_headers(raw_header)
        except SgmlParseError as exc:
            anomalies.append(
                ResolutionAnomaly(
                    filing_id=filing.filing_id,
                    accession=filing.accession,
                    kind=ANOMALY_PARSE_FAILED,
                    detail=str(exc),
                )
            )
            return [], anomalies

        index_sizes = self._fetch_index_sizes(cik, filing.accession)
        artifacts = self._build_artifacts(filing, cik, parsed, index_sizes, anomalies)
        anomalies.extend(_cross_check(filing, parsed, index_sizes, artifacts))
        return artifacts, anomalies

    def _fetch_index_sizes(self, cik: int | str, accession: str) -> dict[str, int | None]:
        """Filename -> reported size from the archive directory listing."""
        url = identity.index_json_url(cik, accession)
        payload: dict[str, Any] = json.loads(self._client.get_bytes(url).content)
        sizes: dict[str, int | None] = {}
        for item in payload.get("directory", {}).get("item", []) or []:
            name = item.get("name")
            if not name:
                continue
            raw = item.get("size")
            sizes[name] = int(raw) if isinstance(raw, str) and raw.isdigit() else (
                raw if isinstance(raw, int) else None
            )
        return sizes

    def _build_artifacts(
        self,
        filing: FilingRecord,
        cik: int | str,
        parsed: ParsedHeader,
        index_sizes: dict[str, int | None],
        anomalies: list[ResolutionAnomaly],
    ) -> list[ArtifactRecord]:
        artifacts: list[ArtifactRecord] = []

        for document in parsed.documents:
            kind, role, low_priority = identity.classify_artifact(
                document.type,
                document.filename,
                document.sequence,
                filing.form,
                document.description,
            )
            if kind == identity.KIND_RENDER_ARTIFACT and not self._include.render_artifacts:
                continue
            if kind == identity.KIND_ASSET and not self._include.assets:
                continue
            if kind == identity.KIND_OTHER:
                anomalies.append(
                    ResolutionAnomaly(
                        filing_id=filing.filing_id,
                        accession=filing.accession,
                        kind=ANOMALY_UNRECOGNIZED_TYPE,
                        detail=f"TYPE={document.type!r} filename={document.filename!r}",
                    )
                )
            artifacts.append(
                _make_artifact(
                    filing=filing,
                    cik=cik,
                    filename=document.filename,
                    kind=kind,
                    role=role,
                    exhibit_type=document.type or None,
                    description=document.description,
                    sequence=document.sequence,
                    expected_size=index_sizes.get(document.filename),
                    low_priority=low_priority,
                )
            )

        if self._include.full_submission:
            name = identity.full_submission_filename(filing.accession)
            artifacts.append(
                _make_artifact(
                    filing=filing,
                    cik=cik,
                    filename=name,
                    kind=identity.KIND_FULL_SUBMISSION,
                    role=None,
                    exhibit_type=None,
                    description="Complete submission text file",
                    sequence=None,
                    expected_size=index_sizes.get(name),
                    low_priority=True,
                )
            )

        if self._include.index_header:
            name = identity.index_headers_filename(filing.accession)
            artifacts.append(
                _make_artifact(
                    filing=filing,
                    cik=cik,
                    filename=name,
                    kind=identity.KIND_INDEX_HEADER,
                    role=None,
                    exhibit_type=None,
                    description="SGML header index; evidence for every role assignment",
                    sequence=None,
                    expected_size=index_sizes.get(name),
                    low_priority=True,
                )
            )

        return artifacts

    # -- many filings ------------------------------------------------------------------

    def resolve_all(
        self,
        filings: Iterable[FilingRecord],
        cik: int | str,
        *,
        max_workers: int = 4,
        progress: "callable[[int, int, FilingRecord], None] | None" = None,
    ) -> tuple[list[ArtifactRecord], list[ResolutionAnomaly]]:
        filing_list = list(filings)
        results: list[tuple[list[ArtifactRecord], list[ResolutionAnomaly]]] = [
            ([], []) for _ in filing_list
        ]

        def work(index: int) -> None:
            results[index] = self.resolve_filing(filing_list[index], cik)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(work, i): i for i in range(len(filing_list))}
            done = 0
            for future in as_completed(futures):
                future.result()
                done += 1
                if progress:
                    progress(done, len(filing_list), filing_list[futures[future]])

        artifacts: list[ArtifactRecord] = []
        anomalies: list[ResolutionAnomaly] = []
        for filing_artifacts, filing_anomalies in results:
            artifacts.extend(filing_artifacts)
            anomalies.extend(filing_anomalies)
        return artifacts, anomalies


def _make_artifact(
    *,
    filing: FilingRecord,
    cik: int | str,
    filename: str,
    kind: str,
    role: str | None,
    exhibit_type: str | None,
    description: str | None,
    sequence: int | None,
    expected_size: int | None,
    low_priority: bool,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=identity.artifact_id(cik, filing.accession, filename),
        filing_id=filing.filing_id,
        cik10=filing.cik10,
        accession=filing.accession,
        artifact_kind=kind,
        role=role,
        exhibit_type=exhibit_type,
        description=description,
        sequence=sequence,
        original_filename=filename,
        stored_path=f"{SOURCE_SUBDIR}/{filename}",
        source_url=identity.artifact_url(cik, filing.accession, filename),
        media_type=identity.media_type_for(filename),
        file_extension=identity.file_extension(filename),
        expected_size=expected_size,
        low_processing_priority=low_priority,
    )


def _cross_check(
    filing: FilingRecord,
    parsed: ParsedHeader,
    index_sizes: dict[str, int | None],
    artifacts: list[ArtifactRecord],
) -> list[ResolutionAnomaly]:
    """Report ambiguity rather than resolving it silently."""
    anomalies: list[ResolutionAnomaly] = []

    def anomaly(kind: str, detail: str) -> ResolutionAnomaly:
        return ResolutionAnomaly(
            filing_id=filing.filing_id,
            accession=filing.accession,
            kind=kind,
            detail=detail,
        )

    primaries = [a for a in artifacts if a.artifact_kind == identity.KIND_PRIMARY]
    if not primaries:
        anomalies.append(anomaly(ANOMALY_NO_PRIMARY, f"form={filing.form}"))
    elif len(primaries) > 1:
        names = ", ".join(a.original_filename for a in primaries)
        anomalies.append(anomaly(ANOMALY_MULTIPLE_PRIMARY, names))

    header_names = {d.filename for d in parsed.documents}
    if index_sizes:
        for name in sorted(header_names - set(index_sizes)):
            anomalies.append(anomaly(ANOMALY_HEADER_NOT_IN_INDEX, name))

    # Case-insensitive filesystems (WSL /mnt/c is one) would collapse two files differing
    # only in case. Detect it at resolve time rather than losing bytes at download time.
    folded: dict[str, list[str]] = defaultdict(list)
    for artifact in artifacts:
        folded[artifact.original_filename.lower()].append(artifact.original_filename)
    for lowered, names in sorted(folded.items()):
        if len(names) > 1:
            anomalies.append(anomaly(ANOMALY_CASE_COLLISION, f"{lowered}: {names}"))

    return anomalies


def summarize(
    artifacts: list[ArtifactRecord], anomalies: list[ResolutionAnomaly]
) -> str:
    if not artifacts:
        return "No artifacts resolved."

    by_kind: dict[str, int] = defaultdict(int)
    bytes_by_kind: dict[str, int] = defaultdict(int)
    for artifact in artifacts:
        by_kind[artifact.artifact_kind] += 1
        bytes_by_kind[artifact.artifact_kind] += artifact.expected_size or 0

    lines = [
        f"{'KIND':<18} {'COUNT':>7} {'EXPECTED MiB':>14}",
        f"{'-' * 18} {'-' * 7} {'-' * 14}",
    ]
    for kind in sorted(by_kind):
        lines.append(
            f"{kind:<18} {by_kind[kind]:>7} {bytes_by_kind[kind] / 1_048_576:>14.1f}"
        )
    total = sum(bytes_by_kind.values())
    lines.extend(
        [
            f"{'-' * 18} {'-' * 7} {'-' * 14}",
            f"{'TOTAL':<18} {len(artifacts):>7} {total / 1_048_576:>14.1f}",
        ]
    )

    if anomalies:
        counts: dict[str, int] = defaultdict(int)
        for item in anomalies:
            counts[item.kind] += 1
        lines.append("")
        lines.append(f"Anomalies flagged for review ({len(anomalies)}):")
        for kind in sorted(counts):
            lines.append(f"  {kind:<38} {counts[kind]:>5}")
    else:
        lines.append("")
        lines.append("No anomalies.")

    return "\n".join(lines)
