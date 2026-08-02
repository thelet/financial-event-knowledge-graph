"""A run directory, staged and finalized so a partial one is unambiguously incomplete.

The rule this module exists to enforce (V1_CLAIM_EXTRACTION §5): stage into a temporary
directory, write the completion marker last, rename into place. Everything else here follows
from wanting that rule to be checkable rather than described.

**The marker is a manifest of the files, not an empty flag.** `run.complete` lists every other
file in the directory with its digest, so "this run finished" and "this run still holds what it
finished with" are the same question. An empty sentinel would answer only the first, and the
first is the one a crash is least likely to get wrong.

**The run id is derived, never stamped.** `make_run_id` takes the config hash, the corpus
identity and the ontology's definition hash; two runs over the same inputs land on the same
path, which is the property that lets them be compared at all. A timestamp would guarantee
they never could.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import RUN_LAYOUT_VERSION
from .identifiers import digest

COMPLETION_MARKER = "run.complete"
MANIFEST_FILENAME = "manifest.json"
PARTIAL_SUFFIX = ".partial"


class IncompleteRunError(RuntimeError):
    """A run directory was opened and it never finished.

    An error rather than a warning with a partial read. The whole point of writing the marker
    last is that a reader can tell; a loader that shrugged and returned what it found would
    make the discipline decorative.
    """


def make_run_id(
    *, config_hash: str, corpus_id: str, ontology_definition_hash: str, strategy: str
) -> str:
    """`extract-v1-{strategy}-{digest12}`. Deterministic and readable, in that order.

    The strategy is in the readable segment even though the config hash already covers it.
    Redundant by design: a directory listing that says which candidate scope produced a run is
    worth a duplicated input, and every other id in this layer is built the same way — a
    readable prefix that aids a human and a digest that carries the uniqueness.
    """
    return "-".join((
        "extract",
        "v" + str(RUN_LAYOUT_VERSION).split(".")[0],
        str(strategy),
        digest(RUN_LAYOUT_VERSION, config_hash, corpus_id, ontology_definition_hash),
    ))


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_marker(files: dict[str, str]) -> str:
    """`{sha256}  {name}` per line, sorted. The bytes are part of the byte-identity claim."""
    return "".join(f"{files[name]}  {name}\n" for name in sorted(files))


@dataclass(frozen=True)
class RunPaths:
    root: Path
    run_id: str

    @property
    def final(self) -> Path:
        return self.root / self.run_id

    @property
    def staging(self) -> Path:
        return self.root / f"{self.run_id}{PARTIAL_SUFFIX}"


class RunDirectoryWriter:
    """Writes into `<run_id>.partial/` and renames it into place, marker last.

    Deliberately dumb about content: it takes names and strings. Deciding what a catalog holds
    is the catalog stage's business, and a writer that knew would be a second place the file
    list is stated.
    """

    def __init__(self, root: Path, run_id: str) -> None:
        self.paths = RunPaths(Path(root), run_id)
        self._written: dict[str, str] = {}

    def begin(self) -> Path:
        """A fresh staging directory. Any earlier partial is removed rather than resumed.

        Resuming would mean trusting files whose producer crashed, and there is no record of
        how far it got — the marker is exactly what does not exist yet.
        """
        staging = self.paths.staging
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        self._written = {}
        return staging

    def write(self, name: str, text: str) -> Path:
        path = self.paths.staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        self._written[name] = file_digest(path)
        return path

    @property
    def written(self) -> dict[str, str]:
        return dict(self._written)

    def finalize(self) -> Path:
        """Marker last, then rename. Returns the final directory.

        The destination is removed *after* the staging directory is complete, so a run id
        never names an incomplete directory: either the previous complete run is there, or the
        new complete one is.
        """
        marker = render_marker(self._written)
        (self.paths.staging / COMPLETION_MARKER).write_text(
            marker, encoding="utf-8", newline="\n")
        final = self.paths.final
        if final.exists():
            shutil.rmtree(final)
        os.replace(self.paths.staging, final)
        return final


class RunDirectory:
    """A finished run directory, refusing to open one that is not."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        marker = self.path / COMPLETION_MARKER
        if not marker.is_file():
            raise IncompleteRunError(
                f"{self.path} has no {COMPLETION_MARKER}; it is a partial run and its "
                "catalogs may be truncated")
        self.files: dict[str, str] = {}
        for line in marker.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recorded, name = line.split("  ", 1)
                self.files[name] = recorded

    @property
    def run_id(self) -> str:
        return self.path.name

    def read(self, name: str) -> str:
        return (self.path / name).read_text(encoding="utf-8")

    def rows(self, name: str) -> list[dict]:
        return [json.loads(line)
                for line in self.read(name).splitlines() if line.strip()]

    def manifest(self) -> dict:
        return json.loads(self.read(MANIFEST_FILENAME))

    def unchanged(self) -> list[str]:
        """Names whose bytes no longer match the marker. Empty is the healthy answer."""
        return [name for name, recorded in sorted(self.files.items())
                if not (self.path / name).is_file()
                or file_digest(self.path / name) != recorded]


def list_runs(root: Path) -> list[str]:
    """Finished run ids under `root`, sorted. Partial directories are not runs."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        entry.name for entry in root.iterdir()
        if entry.is_dir() and (entry / COMPLETION_MARKER).is_file())
