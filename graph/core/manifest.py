"""What a graph run is called, and what it recorded about itself.

Two concerns, kept together because they are the same question asked at two ends of a run:
*which* projection is this (the id, derived before anything is written) and *what did it
consume and produce* (the manifest, written last).

**The run id is derived, never stamped**, exactly as `extraction/core/run_directory.py:44-59`
derives its own: a readable prefix plus a digest over the inputs that decide the answer —
the projection version, the extraction run, the ontology's definition hash, **and a digest
over the bytes the projection actually read**. Two projections of one input therefore land in
one directory and can be compared at all; a clock or a uuid in the id would guarantee they
never could (V1_GRAPH_PROTOTYPE §4.4).

**Why the content digest is in the id, and not merely in the manifest** *(added 2026-08-03
after review)*. The first three inputs come from `manifest.json`, and
`tests/fixtures/graph/extraction_run/` ships the run's manifest **verbatim** — deliberately,
because a manifest edited to match a 39-row slice would no longer be the run's manifest. The
fixture and the full run therefore agreed on all three and minted the *same* id,
`graph-v1-380c18fe3b9f`, so projecting the fixture into the default runs root removed the
real export from underneath it (`GraphRunWriter.finalize` replaces its destination). Two
different inputs that name one directory is a data-loss bug, not a naming inconvenience.
`input_content_digest` closes it with the only thing that actually distinguishes them: the
bytes. It reads files and never a clock, so the id stays reproducible.

**The manifest is the only artifact here allowed to hold a clock.** `nodes.jsonl` and
`edges.jsonl` carry no timestamp, no path and no environment, which is what makes §4.4's
byte-identity claim checkable; `manifest.json` carries `created_at` and the artifact digests,
which is what lets a reader tell *which* two runs were identical. The same split
`extraction/core/manifest.py` states for its own layer.

**Two commits, two names, and the reason they differ.** `extraction_code_commit` is what the
extraction manifest recorded; `graph_code_commit` is this projection's `git rev-parse HEAD`.
They are stored under distinct names because the first is *not* the commit that produced the
extracted data: G0 measured the run's manifest saying `4d3ae1e` while the code that produced
the rows was `b1d55f4`, because extraction reads HEAD at run time and the run was made from a
working tree one commit behind. Collapsing them into a single `code_commit` would let a
reader believe the graph knows which source produced the facts. It knows two things, and says
so.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from extraction.core.identifiers import digest
from extraction.core.run_directory import COMPLETION_MARKER, file_digest

from .inputs import CATALOG_FILES, ExtractionRunInputs, GraphInputError, RunManifest
from .models import GRAPH_PROJECTION_VERSION

#: The readable half of a graph run id. `graph-v1-<digest12>`, mirroring `extract-v1-...`.
RUN_ID_PREFIX = "graph"

#: What a catalog file with no bytes on disk contributes to the content digest. A named
#: token rather than an empty string, so "the file was absent" and "the file was empty" are
#: different inputs to the digest.
ABSENT = "absent"


class OntologyMismatchError(GraphInputError):
    """The loaded ontology is not the one the extraction run was produced under.

    A hard stop rather than a warning. The graph's node labels, edge predicates and warning
    derivation all come from the ontology, and projecting a run under a different definition
    hash would produce a graph whose provenance block names a vocabulary that never saw the
    claims (§5.5).
    """


def input_content_digest(inputs: ExtractionRunInputs) -> str:
    """A digest over what this projection actually read, not over what it was told it read.

    Three sources, in a fixed order, all of them functions of the input and none of them a
    clock:

    1. the run's completion marker where it has one — extraction's own digest of its
       finished catalogs, and the cheapest single answer available;
    2. the sha256 of each of the seven catalog files, so a run directory with no marker
       (`tests/fixtures/graph/extraction_run/` is a real slice and carries none) is still
       distinguished by its bytes;
    3. the row counts of everything actually loaded, including the two corpus catalogs.

    (3) is not redundant with (2). `ExtractionRunInputs.from_rows` builds a run with no
    directory at all — the path every hand-built projection test takes — and there (1) and
    (2) contribute nothing, so the counts are what keep two different synthetic inputs from
    minting one id. Where a directory exists, (2) dominates and (3) is free.
    """
    parts: list[str] = []
    marker = Path(inputs.directory) / COMPLETION_MARKER
    parts.append(f"{COMPLETION_MARKER}={file_digest(marker) if marker.is_file() else ABSENT}")
    for name in CATALOG_FILES:
        path = Path(inputs.directory) / f"{name}.jsonl"
        parts.append(f"{name}={file_digest(path) if path.is_file() else ABSENT}")
    for name in (*CATALOG_FILES, "passages", "documents"):
        parts.append(f"{name}_rows={len(getattr(inputs, name))}")
    return digest(*parts)


def make_graph_run_id(
    *,
    extraction_run_id: str,
    ontology_definition_hash: str,
    input_digest: str,
    projection_version: str = GRAPH_PROJECTION_VERSION,
) -> str:
    """`graph-v{major}-{digest12}` over the four inputs that decide the projection.

    The major version is in the readable segment even though the digest already covers the
    whole version string — the same redundancy `make_run_id` chooses, and for the same
    reason: a directory listing should say what kind of thing it is holding.

    `input_digest` is required rather than optional. A default would restore exactly the
    collision this parameter exists to prevent, and it would restore it silently, in the one
    call site someone forgot to update (see the module docstring).

    Nothing here reads a clock, an environment variable or a filesystem. Everything that
    changes the *content* of an export is either an input to this digest (the projection
    version, the run, the ontology, the input bytes) or lives in the manifest and not in the
    id.
    """
    if not extraction_run_id.strip():
        raise GraphInputError("extraction_run_id is empty; a graph run id derives from it")
    if not ontology_definition_hash.strip():
        raise GraphInputError("ontology_definition_hash is empty")
    if not input_digest.strip():
        raise GraphInputError(
            "input_digest is empty; without it two different runs sharing one manifest mint "
            "one graph run id, and the second projection removes the first")
    return "-".join((
        RUN_ID_PREFIX,
        "v" + projection_version.split(".")[0],
        digest(projection_version, extraction_run_id, ontology_definition_hash,
               input_digest),
    ))


def utc_now_iso() -> str:
    """The manifest's clock, and the only one in the layer."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def code_commit(root: Path | None = None) -> str | None:
    """`git rev-parse HEAD`, or `None`. Mirrors `extraction/core/manifest.py:44-51`.

    `None` rather than an exception: a checkout without git is a legitimate way to run a
    projection, and the manifest saying "no commit recorded" is more useful than a build that
    refuses to produce an export over it.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root) if root else None,
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def check_ontology_matches_run(
    manifest: RunManifest, *, ontology_id: str, definition_hash: str
) -> None:
    """The run's ontology and the loaded one are the same ontology, or the projection stops."""
    if manifest.ontology_id != ontology_id:
        raise OntologyMismatchError(
            f"run {manifest.run_id} was extracted under ontology {manifest.ontology_id!r}; "
            f"this projection loaded {ontology_id!r}")
    if manifest.ontology_definition_hash != definition_hash:
        raise OntologyMismatchError(
            f"run {manifest.run_id} records ontology_definition_hash "
            f"{manifest.ontology_definition_hash}; the loaded ontology hashes to "
            f"{definition_hash}. The vocabulary changed under a finalized run.")


def _relative_to(path: Path, root: Path | None) -> str:
    """The path as written into the manifest: repo-relative where possible.

    An absolute path is machine-specific and would make two manifests of one projection differ
    for a reason that has nothing to do with the projection. `nodes.jsonl` and `edges.jsonl`
    hold no path at all (§4.4); the manifest holds one because "which directory did this
    read" is a question a reader legitimately asks, and repo-relative answers it portably.
    """
    path = Path(path)
    if root is not None:
        try:
            return str(path.resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass
    return str(path)


def extraction_inputs_block(
    directory: Path,
    manifest: RunManifest,
    *,
    input_digest: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """What this projection read: the run directory, its id, and its completion marker.

    The marker digest is recorded when the directory has one, and recorded as `null` when it
    does not — `tests/fixtures/graph/extraction_run/` is a real slice of a run and carries no
    `run.complete`, which is a documented shape rather than a defect. Hashing the marker is
    the cheapest way for a later reader to ask whether the run this graph was projected from
    still holds the bytes it finished with.

    `input_content_digest` is recorded because it is half of the graph run id: a reader
    holding two directories should be able to recompute why they are two directories without
    re-reading either run.
    """
    marker = Path(directory) / COMPLETION_MARKER
    return {
        "extraction_run_directory": _relative_to(directory, root),
        "extraction_run_id": manifest.run_id,
        "extraction_layout_version": manifest.layout_version,
        "extraction_config_hash": manifest.config_hash,
        "run_complete_sha256": file_digest(marker) if marker.is_file() else None,
        "extraction_created_at": manifest.created_at,
        "input_content_digest": input_digest,
    }


@dataclass(frozen=True)
class GraphRunManifest:
    """One projection, described flatly enough to be read without the code that wrote it.

    Every substantive field is reproducible from the inputs: the counts and the artifact
    digests are functions of the export, the reconciliation is a function of the catalogs and
    the ontology, and the two commits and `created_at` are the only entries a second
    projection at a different moment may legitimately differ on.
    """

    graph_run_id: str
    graph_projection_version: str
    created_at: str
    extraction_run_id: str
    #: From the extraction manifest — see the module docstring on why this is not the commit
    #: that produced the rows, and why it keeps its own name.
    extraction_code_commit: str | None
    #: `git rev-parse HEAD` in the working tree that ran the projection.
    graph_code_commit: str | None
    ontology_id: str
    ontology_version: str
    ontology_definition_hash: str
    corpus: dict[str, Any]
    scope: dict[str, Any]
    counts: dict[str, Any]
    warning_reconciliation: dict[str, Any]
    artifacts: dict[str, str]
    inputs: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        """The bytes written to `manifest.json`, matching extraction's own rendering."""
        return json.dumps(
            self.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_counts(
    *,
    nodes: int,
    edges: int,
    nodes_by_label: Mapping[str, int],
    edges_by_type: Mapping[str, int],
    rejected: int,
) -> dict[str, Any]:
    """The counts block, with both breakdowns sorted so the manifest bytes are stable."""
    return {
        "nodes": nodes,
        "edges": edges,
        "nodes_by_label": {key: nodes_by_label[key] for key in sorted(nodes_by_label)},
        "edges_by_type": {key: edges_by_type[key] for key in sorted(edges_by_type)},
        "rejected": rejected,
    }


__all__ = [
    "ABSENT",
    "GraphRunManifest",
    "OntologyMismatchError",
    "RUN_ID_PREFIX",
    "build_counts",
    "check_ontology_matches_run",
    "code_commit",
    "extraction_inputs_block",
    "input_content_digest",
    "make_graph_run_id",
    "utc_now_iso",
]
