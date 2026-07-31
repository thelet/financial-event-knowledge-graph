"""Run identity and environment provenance.

Volatile values live here and in the run manifest — never on documents or passages, which
must stay byte-identical across reruns.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

NORMALIZER_VERSION = "1.0.0"
_TRACKED = ("lxml", "sec-parser", "pydantic", "pyyaml", "pytest")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_run_id(config_hash: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{config_hash[:8]}"


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
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def environment() -> dict[str, str]:
    return {"python_version": sys.version.split()[0], "platform": platform.platform()}
