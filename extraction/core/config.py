"""The extraction layer's own configuration, and the identity a run derives from it.

`config/extraction.yaml` was already read by four call sites, each reaching into the raw
mapping for the one block it needed — `ProviderConfig.from_config`, `EmbeddingConfig`,
`ScopingConfig`, `SelectionPolicy`. This module does not replace any of them. It loads the
document once, hands each of those its own block, and adds the two things a *run* needs and
none of them can supply: the paths the run reads and writes, and a `config_hash` over exactly
what was on disk — including keys this code does not read, so a configuration change can never
be invisible to a run id.

**No stage is imported here.** The four block loaders live with the things they configure and
are called by the composition root, not by this module; `core/` importing a stage is the one
direction the layout forbids.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "extraction.yaml"

# Bumped when the *shape* of a run directory changes in a way that makes an older directory
# unreadable by this code. It enters the run id, so a rebuilt run lands beside the old one
# rather than silently overwriting a directory whose files mean something else.
RUN_LAYOUT_VERSION = "1.0.0"


def canonical_hash(payload: Any) -> str:
    """SHA-256 over a canonical JSON rendering, the same rule the other two packages use.

    Re-stated here rather than imported. `normalization.core.identity` is the nearest
    implementation and an executable test forbids extraction importing normalization's
    internals; six lines of standard library is the cheaper half of that trade.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExtractionPaths:
    """Where a run reads and writes. Relative paths resolve against the repository root.

    `answer_stores` is the one entry that points outside `data/`. A run replays the committed
    provider answers rather than regenerating them, and the files holding those answers are
    committed source, not run output — so the path is configuration, stated once, and the
    pipeline never spells it. See `plans/extraction/STAGE_13_INTEGRATED_RUN.md` §1.1 and the
    correction recorded beside it: the run reads *answers*, which are provider output, and
    never reads a gold case file. An executable test asserts the second half.
    """

    data_root: str = "data"
    normalization_catalog: str = "data/normalization_catalog"
    runs_root: str = "data/extraction_runs"
    answer_stores: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionConfig:
    """The whole document, plus the paths and the hash a run needs.

    `raw` is kept because `config_hash` must cover what was on disk rather than what this
    dataclass models. A key added to the file by a later stage and not read here still moves
    the run id, which is the property that makes "same inputs, same path" mean something.
    """

    root: Path
    version: str
    paths: ExtractionPaths
    raw: dict[str, Any]

    def config_hash(self) -> str:
        return canonical_hash(self.raw)

    def _resolve(self, configured: str) -> Path:
        path = Path(configured)
        return path if path.is_absolute() else self.root / path

    @property
    def data_root(self) -> Path:
        return self._resolve(self.paths.data_root)

    @property
    def catalog_root(self) -> Path:
        return self._resolve(self.paths.normalization_catalog)

    @property
    def runs_root(self) -> Path:
        return self._resolve(self.paths.runs_root)

    @property
    def answer_store_paths(self) -> tuple[Path, ...]:
        return tuple(self._resolve(entry) for entry in self.paths.answer_stores)


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__).resolve().parent).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config" / CONFIG_FILENAME).is_file():
            return candidate
    raise FileNotFoundError(f"config/{CONFIG_FILENAME} not found above {current}")


def load_raw_config(root: Path | None = None) -> dict[str, Any]:
    """The document as written. The four block loaders take this, not `ExtractionConfig`."""
    resolved = Path(root) if root is not None else find_repo_root()
    text = (resolved / "config" / CONFIG_FILENAME).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError(f"config/{CONFIG_FILENAME} must be a mapping")
    return document


def load_config(root: Path | None = None) -> ExtractionConfig:
    resolved = Path(root) if root is not None else find_repo_root()
    raw = load_raw_config(resolved)
    paths = raw.get("paths") or {}
    run = raw.get("run") or {}
    return ExtractionConfig(
        root=resolved,
        version=str(raw.get("version", "v1")),
        paths=ExtractionPaths(
            data_root=str(paths.get("data_root", "data")),
            normalization_catalog=str(
                paths.get("normalization_catalog", "data/normalization_catalog")),
            runs_root=str(paths.get("extraction_runs", "data/extraction_runs")),
            answer_stores=tuple(str(entry) for entry in (run.get("answer_stores") or ())),
        ),
        raw=raw,
    )
