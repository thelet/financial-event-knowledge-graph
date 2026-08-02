"""The run manifest: immutable, and the one place a run records its environment.

Everything volatile lives here and nowhere else (V1_CLAIM_EXTRACTION §5, STAGE_13 §7). The
catalogs and `report.md` carry no timestamp, no duration, no token count and no absolute path,
which is what lets two runs of the same inputs be compared byte for byte; the manifest carries
the clock, the interpreter and the commit, which is what lets a reader tell *which* two runs
they were.

So the manifest is deliberately **not** part of the byte-identity claim. A run whose manifest
differed only in `created_at` is the same run; a run whose `catalog_digests` differ is not, and
those digests are in here precisely so that question has an answer without re-reading the
files.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "1.0.0"
_TRACKED = ("pydantic", "pyyaml", "httpx", "pytest")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dependency_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in _TRACKED:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            continue
    return out


def code_commit(root: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root) if root else None,
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def environment() -> dict[str, str]:
    return {"python_version": sys.version.split()[0], "platform": platform.platform()}


@dataclass
class RunManifest:
    """What a run was, what it consumed, and what it decided about itself.

    Flat and explicit. A manifest that stored a reference to a config object would be a
    manifest you had to have the code to read, and the point of writing one is that a directory
    can be understood three months later by someone holding only the directory.
    """

    run_id: str
    extractor_version: str
    layout_version: str
    config_hash: str
    code_commit: str | None
    created_at: str
    ontology_id: str
    ontology_definition_hash: str
    corpus: dict[str, Any]
    scope: dict[str, Any]
    lanes: list[dict[str, Any]]
    provider: dict[str, Any]
    bounds: dict[str, Any]
    counts: dict[str, Any]
    verification: list[dict[str, Any]]
    catalog_digests: dict[str, str]
    environment: dict[str, str] = field(default_factory=environment)
    dependencies: dict[str, str] = field(default_factory=dependency_versions)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
