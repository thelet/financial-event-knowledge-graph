"""Run identity and environment provenance."""

from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .models import RunMetadata

FETCHER_VERSION = "0.1.0"

_TRACKED_DEPENDENCIES = ("httpx", "pydantic", "pyyaml", "pytest")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_run_id(config_hash: str) -> str:
    """Run identity: sortable timestamp plus a configuration fingerprint.

    Changing configuration changes the second component, so manifests from different
    scopes are visibly different (v0 plan section 10).
    """
    return f"{_utc_compact()}-{config_hash[:8]}"


def dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in _TRACKED_DEPENDENCIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            continue
    return versions


def code_commit(root: Path | None = None) -> str | None:
    """Current git commit, or None outside a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root) if root else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def build_run_metadata(
    stage: str, config_hash: str, *, run_id: str | None = None, root: Path | None = None
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id or make_run_id(config_hash),
        stage=stage,
        created_at=utc_now_iso(),
        config_hash=config_hash,
        code_commit=code_commit(root),
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        fetcher_version=FETCHER_VERSION,
        dependency_versions=dependency_versions(),
    )
