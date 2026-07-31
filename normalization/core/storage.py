"""Normalized-document storage with atomic finalization.

A partially written document must be unambiguously incomplete: stage into a temporary
directory, write the document JSON last, then rename into place. Debris from an interrupted
run is confined to the staging root and is never read.

Storage behaviour is confined here so a different backend can be added without touching any
stage.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterator, Protocol

from .identity import document_relpath
from .models import NormalizedDocument, Passage

PASSAGES_SUFFIX = ".passages.jsonl"


class StorageError(RuntimeError):
    pass


class NormalizedDocumentStore(Protocol):
    def finalize(self, document: NormalizedDocument, passages: list[Passage]) -> Path: ...
    def read_document(self, document_id: str) -> NormalizedDocument | None: ...
    def iter_documents(self) -> Iterator[Path]: ...


class LocalNormalizedStore:
    """Filesystem store rooted at data/normalized, staging under data/normalization_tmp.

    Both roots share a parent so finalization is a same-filesystem rename.
    """

    def __init__(self, root: Path, tmp_root: Path, run_id: str = "run") -> None:
        self.root = Path(root)
        self.tmp_root = Path(tmp_root)
        self.run_id = run_id

    # -- paths -------------------------------------------------------------------------

    def document_dir(self, document: NormalizedDocument) -> Path:
        accession = document.source_artifact_id.split(":")[2]
        return self.root / document_relpath(
            document.cik, document.form_sanitized, document.filing_date, accession
        )

    def document_path(self, document: NormalizedDocument) -> Path:
        return self.document_dir(document) / f"{_safe(document.document_id)}.json"

    def passages_path(self, document: NormalizedDocument) -> Path:
        return self.document_dir(document) / f"{_safe(document.document_id)}{PASSAGES_SUFFIX}"

    # -- writing -----------------------------------------------------------------------

    def finalize(self, document: NormalizedDocument, passages: list[Passage]) -> Path:
        """Write passages first, then the document JSON, then rename atomically."""
        staging = self.tmp_root / self.run_id / _safe(document.document_id)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        name = _safe(document.document_id)
        with (staging / f"{name}{PASSAGES_SUFFIX}").open("w", encoding="utf-8", newline="\n") as handle:
            for passage in passages:
                handle.write(json.dumps(passage.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) + "\n")

        # Written last: its presence in a finalized directory marks the document complete.
        (staging / f"{name}.json").write_text(
            json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        final_dir = self.document_dir(document)
        final_dir.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            target = final_dir / item.name
            os.replace(item, target)
        shutil.rmtree(staging, ignore_errors=True)
        self._cleanup_staging()
        return final_dir / f"{name}.json"

    def _cleanup_staging(self) -> None:
        run_root = self.tmp_root / self.run_id
        try:
            if run_root.is_dir() and not any(run_root.iterdir()):
                run_root.rmdir()
            if self.tmp_root.is_dir() and not any(self.tmp_root.iterdir()):
                self.tmp_root.rmdir()
        except OSError:
            pass

    # -- reading -----------------------------------------------------------------------

    def iter_documents(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        yield from sorted(
            p for p in self.root.rglob("*.json") if not p.name.endswith(PASSAGES_SUFFIX)
        )

    def read_document(self, path: Path) -> NormalizedDocument | None:
        try:
            return NormalizedDocument(**json.loads(Path(path).read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return None

    def read_passages(self, path: Path) -> list[dict]:
        passages_path = Path(str(path).removesuffix(".json") + PASSAGES_SUFFIX)
        if not passages_path.is_file():
            return []
        rows = []
        for line in passages_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows


def _safe(document_id: str) -> str:
    """Filesystem-safe form of a document id; ':' and '/' are not path-safe."""
    return document_id.replace(":", "_").replace("/", "-")
