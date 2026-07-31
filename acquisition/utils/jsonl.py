"""JSONL reading and writing.

Generic and stateless: no application state, no domain concept, no stage ownership. Lives
here rather than in the catalog stage because the catalog, verify, and report stages all
need it, and a stage must not import another stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    """Atomic write with sorted keys, so identical input yields identical bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    with staging.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    staging.replace(path)
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, raising on the first malformed line."""
    rows: list[dict[str, Any]] = []
    path = Path(path)
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: malformed JSONL: {exc}") from exc
    return rows
