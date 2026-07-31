"""Immutable manifest storage.

A manifest is written once and never modified. Writing to an existing path is an error,
not an overwrite — the same rule the acquisition package follows.
"""

from __future__ import annotations

import json
from pathlib import Path

from .identity import canonical_hash
from .models import NormalizationRun, SelectionManifest


class ManifestExistsError(FileExistsError):
    """Refused to overwrite an existing manifest."""


class ManifestNotFoundError(FileNotFoundError):
    pass


class ManifestRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths -------------------------------------------------------------------------

    def selection_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}-selection.json"

    def run_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    # -- writing -----------------------------------------------------------------------

    def write_selection_manifest(self, manifest: SelectionManifest) -> Path:
        payload = manifest.model_dump(mode="json")
        payload.pop("manifest_hash", None)
        manifest.manifest_hash = canonical_hash(payload)
        return self._write(self.selection_path(manifest.run_id), manifest.model_dump(mode="json"))

    def write_run_manifest(self, run: NormalizationRun) -> Path:
        return self._write(self.run_path(run.run_id), run.model_dump(mode="json"), replace=True)

    def _write(self, path: Path, payload: dict, *, replace: bool = False) -> Path:
        if path.exists() and not replace:
            raise ManifestExistsError(
                f"Manifest already exists and manifests are immutable: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_suffix(".json.tmp")
        staging.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        staging.replace(path)
        return path

    # -- reading -----------------------------------------------------------------------

    def read_selection_manifest(self, run_id: str) -> SelectionManifest:
        path = self.selection_path(run_id)
        if not path.is_file():
            raise ManifestNotFoundError(f"No selection manifest at {path}")
        return SelectionManifest(**json.loads(path.read_text(encoding="utf-8")))

    def read_run_manifest(self, run_id: str) -> NormalizationRun:
        path = self.run_path(run_id)
        if not path.is_file():
            raise ManifestNotFoundError(f"No run manifest at {path}")
        return NormalizationRun(**json.loads(path.read_text(encoding="utf-8")))

    def list_selection_run_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name[: -len("-selection.json")] for p in self.root.glob("*-selection.json"))

    def latest_selection_run_id(self) -> str:
        ids = self.list_selection_run_ids()
        if not ids:
            raise ManifestNotFoundError(f"No selection manifests in {self.root}")
        return ids[-1]
