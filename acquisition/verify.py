"""VERIFY: cross-check manifests, filesystem, per-filing metadata, and catalogs.

Makes "zero missing files" precise at the artifact level rather than only at the filing
level (v0 plan section 4). Exits non-zero on any failure.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .core.models import ArtifactManifest, FilingManifest
from .core.storage import (
    ARTIFACTS_CATALOG,
    FILING_METADATA_NAME,
    FILINGS_CATALOG,
    LocalRawArtifactStore,
)
from .utils.jsonl import read_jsonl

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"


@dataclass
class Finding:
    severity: str
    check: str
    detail: str


@dataclass
class VerificationReport:
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def add(self, severity: str, check: str, detail: str) -> None:
        self.findings.append(Finding(severity, check, detail))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self, max_per_check: int = 8) -> str:
        lines = ["VERIFICATION REPORT", "=" * 60, ""]
        for key in sorted(self.stats):
            lines.append(f"  {key:<40} {self.stats[key]:>8}")
        lines.append("")

        if not self.findings:
            lines.append("No findings. Corpus is consistent.")
            return "\n".join(lines)

        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        for finding in self.findings:
            grouped[(finding.severity, finding.check)].append(finding.detail)

        for severity in (SEVERITY_ERROR, SEVERITY_WARNING):
            entries = {k: v for k, v in grouped.items() if k[0] == severity}
            if not entries:
                continue
            lines.append(f"{severity}S ({sum(len(v) for v in entries.values())})")
            lines.append("-" * 60)
            for (_, check), details in sorted(entries.items()):
                lines.append(f"  {check}  ({len(details)})")
                for detail in details[:max_per_check]:
                    lines.append(f"      {detail}")
                if len(details) > max_per_check:
                    lines.append(f"      ... and {len(details) - max_per_check} more")
            lines.append("")

        lines.append("PASS" if self.ok else "FAIL")
        return "\n".join(lines)


class CorpusVerifier:
    def __init__(
        self,
        store: LocalRawArtifactStore,
        catalog_root: Path,
        *,
        check_hashes: bool = True,
    ) -> None:
        self._store = store
        self._catalog_root = Path(catalog_root)
        self._check_hashes = check_hashes

    def verify(
        self, filing_manifest: FilingManifest, artifact_manifest: ArtifactManifest
    ) -> VerificationReport:
        report = VerificationReport()

        expected_filings = {f.filing_id: f for f in filing_manifest.filings}
        expected_artifacts_by_filing: dict[str, list] = defaultdict(list)
        for artifact in artifact_manifest.artifacts:
            expected_artifacts_by_filing[artifact.filing_id].append(artifact)

        report.stats["manifest.filings"] = len(expected_filings)
        report.stats["manifest.artifacts"] = len(artifact_manifest.artifacts)
        report.stats["manifest.anomalies"] = len(artifact_manifest.anomalies)

        self._check_manifest_linkage(filing_manifest, artifact_manifest, report)
        found = self._check_filings(expected_filings, expected_artifacts_by_filing, report)
        self._check_orphan_directories(expected_filings, report)
        self._check_staging(report)
        self._check_catalogs(found, report)
        self._check_resolution_anomalies(artifact_manifest, report)
        return report

    # -- checks ------------------------------------------------------------------------

    def _check_manifest_linkage(
        self,
        filing_manifest: FilingManifest,
        artifact_manifest: ArtifactManifest,
        report: VerificationReport,
    ) -> None:
        if artifact_manifest.filings_run_id != filing_manifest.run.run_id:
            report.add(
                SEVERITY_ERROR,
                "manifest_linkage",
                f"artifact manifest was resolved from filing run "
                f"{artifact_manifest.filings_run_id}, not {filing_manifest.run.run_id}",
            )
        filing_ids = {f.filing_id for f in filing_manifest.filings}
        orphans = {
            a.filing_id for a in artifact_manifest.artifacts if a.filing_id not in filing_ids
        }
        for orphan in sorted(orphans):
            report.add(
                SEVERITY_ERROR, "artifact_without_filing", f"{orphan} not in filing manifest"
            )

    def _check_filings(
        self,
        expected_filings: dict,
        expected_artifacts_by_filing: dict[str, list],
        report: VerificationReport,
    ) -> dict[str, set[str]]:
        finalized = 0
        artifacts_ok = 0
        found_artifact_ids: dict[str, set[str]] = {}

        for filing_id, filing in sorted(expected_filings.items()):
            metadata = self._store.inspect_finalized_filing(filing.filing_dir)
            if metadata is None:
                path = self._store.metadata_path(filing.filing_dir)
                detail = (
                    f"{filing.accession}: no valid {FILING_METADATA_NAME} at {path}"
                    if self._store.filing_dir(filing.filing_dir).exists()
                    else f"{filing.accession}: filing directory absent ({filing.filing_dir})"
                )
                report.add(SEVERITY_ERROR, "filing_not_finalized", detail)
                continue

            finalized += 1

            if metadata.filing_id != filing_id:
                report.add(
                    SEVERITY_ERROR,
                    "filing_id_mismatch",
                    f"{filing.filing_dir}: metadata says {metadata.filing_id}",
                )
            if metadata.form_sanitized != filing.form_sanitized:
                report.add(
                    SEVERITY_ERROR,
                    "incorrect_form_path",
                    f"{filing.accession}: directory form {filing.form_sanitized} "
                    f"vs metadata {metadata.form_sanitized}",
                )

            expected_ids = {a.artifact_id for a in expected_artifacts_by_filing[filing_id]}
            actual_ids = {a.artifact_id for a in metadata.artifacts}
            found_artifact_ids[filing_id] = actual_ids

            for missing in sorted(expected_ids - actual_ids):
                report.add(
                    SEVERITY_ERROR,
                    "artifact_missing_from_metadata",
                    missing.rsplit(":", 1)[-1] + f"  ({filing.accession})",
                )
            for extra in sorted(actual_ids - expected_ids):
                report.add(
                    SEVERITY_ERROR,
                    "unexpected_artifact_in_metadata",
                    extra.rsplit(":", 1)[-1] + f"  ({filing.accession})",
                )

            if self._check_hashes:
                problems = self._store.verify_filing_contents(filing.filing_dir, metadata)
                for problem in problems:
                    report.add(
                        SEVERITY_ERROR, "artifact_integrity", f"{filing.accession}: {problem}"
                    )
                if not problems:
                    artifacts_ok += len(metadata.artifacts)

            self._check_stray_files(filing.filing_dir, metadata, report, filing.accession)

        report.stats["filings.finalized"] = finalized
        report.stats["artifacts.verified"] = artifacts_ok
        return found_artifact_ids

    def _check_stray_files(
        self, filing_relpath: str, metadata, report: VerificationReport, accession: str
    ) -> None:
        """Files on disk that no metadata record accounts for."""
        root = self._store.filing_dir(filing_relpath)
        if not root.is_dir():
            return
        recorded = {a.stored_path for a in metadata.artifacts}
        recorded.add(FILING_METADATA_NAME)
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in recorded:
                report.add(
                    SEVERITY_ERROR, "unexpected_file_on_disk", f"{accession}: {relative}"
                )

    def _check_orphan_directories(
        self, expected_filings: dict, report: VerificationReport
    ) -> None:
        """Finalized filings on disk that the manifest does not expect."""
        expected_dirs = {f.filing_dir for f in expected_filings.values()}
        for path in self._store.iter_finalized_filings():
            relative = path.parent.relative_to(self._store.raw_root).as_posix()
            if relative not in expected_dirs:
                report.add(
                    SEVERITY_WARNING,
                    "filing_not_in_manifest",
                    f"{relative} (from an earlier or wider run)",
                )

    def _check_staging(self, report: VerificationReport) -> None:
        """Staging debris means an interrupted run, not a corrupt corpus."""
        tmp_root = self._store.tmp_root
        if not tmp_root.is_dir():
            return
        leftovers = [p for p in tmp_root.iterdir() if p.is_dir()]
        if leftovers:
            report.stats["staging.leftover_dirs"] = len(leftovers)
            report.add(
                SEVERITY_WARNING,
                "incomplete_staging_directory",
                f"{len(leftovers)} directory(ies) under {tmp_root}; safe to delete",
            )

    def _check_catalogs(
        self, found_artifact_ids: dict[str, set[str]], report: VerificationReport
    ) -> None:
        filings_path = self._catalog_root / FILINGS_CATALOG
        artifacts_path = self._catalog_root / ARTIFACTS_CATALOG

        if not filings_path.is_file() or not artifacts_path.is_file():
            report.add(
                SEVERITY_WARNING,
                "catalog_absent",
                "run build-catalog to generate filings.jsonl and artifacts.jsonl",
            )
            return

        try:
            filing_rows = read_jsonl(filings_path)
            artifact_rows = read_jsonl(artifacts_path)
        except ValueError as exc:
            report.add(SEVERITY_ERROR, "corrupt_catalog", str(exc))
            return

        report.stats["catalog.filings"] = len(filing_rows)
        report.stats["catalog.artifacts"] = len(artifact_rows)

        for rows, key, name in (
            (filing_rows, "filing_id", "filings.jsonl"),
            (artifact_rows, "artifact_id", "artifacts.jsonl"),
        ):
            counts = Counter(row.get(key) for row in rows)
            for identifier, count in counts.items():
                if count > 1:
                    report.add(
                        SEVERITY_ERROR, "duplicate_id_in_catalog", f"{name}: {identifier}"
                    )

        catalog_artifact_ids = {row.get("artifact_id") for row in artifact_rows}
        disk_artifact_ids = {i for ids in found_artifact_ids.values() for i in ids}
        for missing in sorted(disk_artifact_ids - catalog_artifact_ids):
            report.add(
                SEVERITY_ERROR,
                "catalog_divergence",
                f"on disk but not in artifacts.jsonl: {missing}",
            )
        for extra in sorted(catalog_artifact_ids - disk_artifact_ids):
            report.add(
                SEVERITY_ERROR,
                "catalog_divergence",
                f"in artifacts.jsonl but not on disk: {extra}",
            )

    def _check_resolution_anomalies(
        self, artifact_manifest: ArtifactManifest, report: VerificationReport
    ) -> None:
        """Resolution-time ambiguity surfaces as warnings, not failures (v0 plan 12)."""
        counts = Counter(a.kind for a in artifact_manifest.anomalies)
        for kind, count in sorted(counts.items()):
            report.add(
                SEVERITY_WARNING, f"resolution_{kind}", f"{count} filing(s) flagged at resolve"
            )
