"""Raw artifact storage with atomic filing completion.

A partially downloaded filing must be unambiguously incomplete (v0 plan section 6). The
guarantee here: a filing directory under the raw root either does not exist, or contains a
complete artifact set with a valid _filing.json. Debris from interrupted runs is confined
to the staging root and is never read.

Storage behavior is confined to this module so that a cloud implementation can be added
later without touching the pipeline stages.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterator, Protocol

from .models import FilingMetadata

FILING_METADATA_NAME = "_filing.json"
SOURCE_SUBDIR = "source"

# Derived catalog filenames. Here rather than in the catalog stage because verify and
# report also address these files, and stages must not import one another.
FILINGS_CATALOG = "filings.jsonl"
ARTIFACTS_CATALOG = "artifacts.jsonl"


class FilingIntegrityError(RuntimeError):
    pass


class RawArtifactStore(Protocol):
    """Interface for raw artifact storage. Local filesystem is the only implementation."""

    def filing_dir(self, filing_relpath: str) -> Path: ...
    def prepare_temporary_filing(self, run_id: str, accession: str) -> Path: ...
    def artifact_destination(self, filing_root: Path, stored_path: str) -> Path: ...
    def write_filing_metadata(self, filing_root: Path, metadata: FilingMetadata) -> Path: ...
    def finalize_filing(self, staging_dir: Path, filing_relpath: str) -> Path: ...
    def inspect_finalized_filing(self, filing_relpath: str) -> FilingMetadata | None: ...
    def discard_staging(self, staging_dir: Path) -> None: ...
    def iter_finalized_filings(self) -> Iterator[Path]: ...


class LocalRawArtifactStore:
    """Filesystem-backed store rooted at data/raw, staging under data/tmp.

    Both roots share a parent so that finalization is a same-filesystem directory rename.
    """

    def __init__(self, raw_root: Path, tmp_root: Path) -> None:
        self.raw_root = Path(raw_root)
        self.tmp_root = Path(tmp_root)

    # -- paths -------------------------------------------------------------------------

    def filing_dir(self, filing_relpath: str) -> Path:
        return self.raw_root / filing_relpath

    def metadata_path(self, filing_relpath: str) -> Path:
        return self.filing_dir(filing_relpath) / FILING_METADATA_NAME

    def artifact_destination(self, filing_root: Path, stored_path: str) -> Path:
        destination = (filing_root / stored_path).resolve()
        root = filing_root.resolve()
        # Guard against a filename in SEC metadata escaping the filing directory.
        if root != destination and root not in destination.parents:
            raise FilingIntegrityError(
                f"Artifact path escapes its filing directory: {stored_path!r}"
            )
        return destination

    # -- staging -----------------------------------------------------------------------

    def prepare_temporary_filing(self, run_id: str, accession: str) -> Path:
        staging = self.tmp_root / run_id / accession
        if staging.exists():
            shutil.rmtree(staging)
        (staging / SOURCE_SUBDIR).mkdir(parents=True, exist_ok=True)
        return staging

    def discard_staging(self, staging_dir: Path) -> None:
        shutil.rmtree(staging_dir, ignore_errors=True)

    def cleanup_run_staging(self, run_id: str) -> None:
        """Remove a run's staging root once it holds nothing.

        Leaves anything still present alone: surviving debris is the signal that a run was
        interrupted, and verification reports it.
        """
        run_root = self.tmp_root / run_id
        if run_root.is_dir() and not any(run_root.iterdir()):
            run_root.rmdir()
        if self.tmp_root.is_dir() and not any(self.tmp_root.iterdir()):
            self.tmp_root.rmdir()

    # -- finalization ------------------------------------------------------------------

    def write_filing_metadata(self, filing_root: Path, metadata: FilingMetadata) -> Path:
        """Write _filing.json. Must be the last write into a staging directory."""
        path = filing_root / FILING_METADATA_NAME
        path.write_text(
            json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def finalize_filing(self, staging_dir: Path, filing_relpath: str) -> Path:
        """Atomically move a completed staging directory into place.

        If a directory already occupies the target, it is quarantined first so the rename
        target is always absent, and removed only after the rename succeeds. No destructive
        delete precedes a successful replacement.
        """
        if not (staging_dir / FILING_METADATA_NAME).is_file():
            raise FilingIntegrityError(
                f"Refusing to finalize {staging_dir}: {FILING_METADATA_NAME} is missing"
            )

        final = self.filing_dir(filing_relpath)
        final.parent.mkdir(parents=True, exist_ok=True)

        quarantine: Path | None = None
        if final.exists():
            quarantine = staging_dir.parent / f"quarantine-{final.name}"
            if quarantine.exists():
                shutil.rmtree(quarantine)
            os.replace(final, quarantine)

        try:
            os.replace(staging_dir, final)
        except OSError:
            if quarantine is not None and not final.exists():
                os.replace(quarantine, final)  # restore the previous state
            raise

        if quarantine is not None:
            shutil.rmtree(quarantine, ignore_errors=True)
        return final

    # -- inspection --------------------------------------------------------------------

    def inspect_finalized_filing(self, filing_relpath: str) -> FilingMetadata | None:
        """Return metadata for a finalized filing, or None if absent or unreadable."""
        path = self.metadata_path(filing_relpath)
        if not path.is_file():
            return None
        try:
            return FilingMetadata(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return None

    def iter_finalized_filings(self) -> Iterator[Path]:
        """Yield every _filing.json under the raw root, in deterministic order."""
        if not self.raw_root.is_dir():
            return
        yield from sorted(self.raw_root.rglob(FILING_METADATA_NAME))

    # -- integrity ---------------------------------------------------------------------

    def verify_filing_contents(
        self, filing_relpath: str, metadata: FilingMetadata
    ) -> list[str]:
        """Check that every recorded artifact exists with the recorded size and hash."""
        problems: list[str] = []
        root = self.filing_dir(filing_relpath)
        for artifact in metadata.artifacts:
            path = root / artifact.stored_path
            if not path.is_file():
                problems.append(f"missing file: {artifact.stored_path}")
                continue
            actual_size = path.stat().st_size
            if actual_size != artifact.size_bytes:
                problems.append(
                    f"size mismatch: {artifact.stored_path} "
                    f"expected {artifact.size_bytes}, found {actual_size}"
                )
                continue
            actual_hash = sha256_file(path)
            if actual_hash != artifact.sha256:
                problems.append(
                    f"hash mismatch: {artifact.stored_path} "
                    f"expected {artifact.sha256[:12]}..., found {actual_hash[:12]}..."
                )
        return problems


def sha256_file(path: Path, chunk_size: int = 1_048_576) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
