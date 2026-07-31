"""Immutable manifest storage.

A manifest is written once and never modified (v0 plan section 10). Writing to an existing
path is an error, not an overwrite. Every discovery run creates a new run_id; download may
reuse an existing manifest by run_id.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import canonical_hash
from .models import ArtifactManifest, FilingManifest

FILINGS_DIR = "filings"
ARTIFACTS_DIR = "artifacts"


class ManifestExistsError(FileExistsError):
    """Refused to overwrite an existing manifest."""


class ManifestNotFoundError(FileNotFoundError):
    pass


def compute_manifest_hash(manifest: FilingManifest | ArtifactManifest) -> str:
    """SHA-256 over the manifest body with the hash field itself excluded."""
    payload = manifest.model_dump(mode="json")
    payload.pop("manifest_hash", None)
    return canonical_hash(payload)


class ManifestRepository:
    """Filesystem-backed manifest store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths -------------------------------------------------------------------------

    def filing_manifest_path(self, run_id: str) -> Path:
        return self.root / FILINGS_DIR / f"{run_id}.json"

    def artifact_manifest_path(self, run_id: str) -> Path:
        return self.root / ARTIFACTS_DIR / f"{run_id}.json"

    # -- writing -----------------------------------------------------------------------

    def write_filing_manifest(self, manifest: FilingManifest) -> Path:
        return self._write(self.filing_manifest_path(manifest.run.run_id), manifest)

    def write_artifact_manifest(self, manifest: ArtifactManifest) -> Path:
        return self._write(self.artifact_manifest_path(manifest.run.run_id), manifest)

    def _write(self, path: Path, manifest: FilingManifest | ArtifactManifest) -> Path:
        if path.exists():
            raise ManifestExistsError(
                f"Manifest already exists and manifests are immutable: {path}"
            )
        manifest.manifest_hash = compute_manifest_hash(manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temporary sibling so an interrupted write cannot leave a truncated
        # manifest that later looks authoritative.
        staging = path.with_suffix(".json.tmp")
        staging.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        staging.replace(path)
        return path

    # -- reading -----------------------------------------------------------------------

    def read_filing_manifest(self, run_id: str) -> FilingManifest:
        return FilingManifest(**self._read(self.filing_manifest_path(run_id)))

    def read_artifact_manifest(self, run_id: str) -> ArtifactManifest:
        return ArtifactManifest(**self._read(self.artifact_manifest_path(run_id)))

    def _read(self, path: Path) -> dict:
        if not path.is_file():
            raise ManifestNotFoundError(f"No manifest at {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # -- discovery ---------------------------------------------------------------------

    def list_filing_run_ids(self) -> list[str]:
        return self._list(self.root / FILINGS_DIR)

    def list_artifact_run_ids(self) -> list[str]:
        return self._list(self.root / ARTIFACTS_DIR)

    def _list(self, directory: Path) -> list[str]:
        if not directory.is_dir():
            return []
        # run_id begins with a compact UTC timestamp, so lexical order is chronological.
        return sorted(p.stem for p in directory.glob("*.json"))

    def latest_filing_run_id(self) -> str:
        ids = self.list_filing_run_ids()
        if not ids:
            raise ManifestNotFoundError(
                f"No filing manifests in {self.root / FILINGS_DIR}; run discover first"
            )
        return ids[-1]

    def latest_artifact_run_id(self) -> str:
        ids = self.list_artifact_run_ids()
        if not ids:
            raise ManifestNotFoundError(
                f"No artifact manifests in {self.root / ARTIFACTS_DIR}; run resolve first"
            )
        return ids[-1]
