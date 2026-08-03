"""Which graph run a story run is about to read, taken off the projection's own manifest.

Responsibility: parse `data/graph_runs/<graph_run_id>/manifest.json` into the identity block
§13.13 pins, and refuse with a typed error when the file is absent or does not carry it.
Nothing here opens a database and nothing here decides whether that identity is still *true* —
that is `story/stages/freshness/`, which reads this and compares it against the extraction
directory on disk and against what Neo4j actually holds.

**Why a reader exists here rather than upstream.** `graph/core/manifest.py` writes the manifest
and never reads one back: `GraphRunManifest.render()` is a one-way contract, and the graph
layer has no consumer for the parsed form. The story layer is the first, so the reader lands in
the package that needs it. `graph/core/inputs.py:428` models the *extraction* manifest as
`RunManifest` and is the pattern followed below. To keep the two from drifting,
`tests/story/test_story_freshness.py` asserts that every field declared here is a field
`GraphRunManifest` actually writes — the duplication is checked rather than trusted, the same
trade `story/context.py` makes for `data/graph_runs`.

**Allow-subset (`extra="ignore"`), not `extra="forbid"`.** `RunManifest` forbids extras because
an unknown key in an extraction catalog means the run the graph consumes changed shape and the
projection should stop. This boundary is the one `graph/core/inputs.py:_CorpusRow` describes
instead: `manifest.json` is written by another workstream for several consumers and may
legitimately grow a field the story layer has no opinion about, and refusing to read a graph
because the projection recorded a new diagnostic block would be a false alarm — worse, a
`ValidationError` where §7 promises a structured refusal. So the subset is **declared** rather
than discovered: every field read is named below, and one that disappeared upstream still fails
here as a missing required field. Ignoring unknown keys is not the same as accepting unknown
data.

`run_complete_sha256` is `str | None` because the writer records `null` for an extraction
directory with no completion marker (`graph/core/manifest.py:219`) —
`tests/fixtures/graph/extraction_run/` is a real slice that carries none. A `None` here is not
"the digest matched nothing"; it is "the projection never recorded one", and §7's gate reports
those differently.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from story.core.models import StoryModel

#: The projection writes exactly this name (`graph/stages/projection/writer.py` finalizes it),
#: and it is also the graph run's completion marker: a directory without one is incomplete.
GRAPH_MANIFEST_FILENAME = "manifest.json"


class GraphIdentityError(RuntimeError):
    """The graph run could not be identified: no directory, no manifest, or an unreadable one.

    Its own type rather than `FileNotFoundError` or `ValidationError` so a caller can tell
    "nobody has projected this run" from "the database is down" — different answers for an
    operator, and §7's gate turns this one into a refusal rather than letting it escape.
    """


class _GraphManifestBlock(BaseModel):
    """Allow-subset and frozen. See the module docstring for why extras are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class GraphRunCounts(_GraphManifestBlock):
    """`counts.nodes` and `counts.edges` — what the export claims it wrote.

    `nodes_by_label` and `edges_by_type` are deliberately not read: §7's second check compares
    two totals against the `:GraphLoad` marker, and a per-label comparison is the load stage's
    own verification (`graph/stages/load/verification.py`), already run before the marker was
    written. Re-running it here would be a second, weaker copy of a check that already exists.
    """

    nodes: int
    edges: int


class GraphRunInputs(_GraphManifestBlock):
    """The `inputs` block — what the projection read, and the digest that identifies it.

    `run_complete_sha256` is the field §7 exists for. `input_content_digest` is read too
    because it is half the graph run id and a reader holding two directories should be able to
    say why they are two without re-hashing either.
    """

    extraction_run_directory: str
    extraction_run_id: str
    run_complete_sha256: str | None
    input_content_digest: str


class GraphRunManifestDocument(_GraphManifestBlock):
    """`manifest.json`, as much of it as the story layer reads."""

    graph_run_id: str
    graph_projection_version: str
    extraction_run_id: str
    ontology_id: str
    ontology_version: str
    ontology_definition_hash: str
    counts: GraphRunCounts
    inputs: GraphRunInputs


class GraphIdentity(StoryModel):
    """The identity block §13.13 pins, flat, with the two export counts §7 compares against.

    Flat rather than nested because it is what a package, a manifest and a refusal all quote,
    and a reader chasing `identity.inputs.run_complete_sha256` through two levels to answer
    "which extraction run is this" is one level too many. The nesting stays in the document
    model above, which exists only to parse.

    **`run_complete_sha256` is the field that actually identifies the inputs**, not
    `extraction_run_id`: §18 measured one extraction run id naming two different sets of bytes,
    which is the drift §7 was written to catch and which an id comparison passes.
    """

    graph_run_id: str
    graph_projection_version: str
    extraction_run_id: str
    extraction_run_directory: str
    run_complete_sha256: str | None
    input_content_digest: str
    ontology_id: str
    ontology_version: str
    ontology_definition_hash: str
    node_count: int
    edge_count: int

    @classmethod
    def from_document(cls, document: GraphRunManifestDocument) -> "GraphIdentity":
        return cls(
            graph_run_id=document.graph_run_id,
            graph_projection_version=document.graph_projection_version,
            extraction_run_id=document.extraction_run_id,
            extraction_run_directory=document.inputs.extraction_run_directory,
            run_complete_sha256=document.inputs.run_complete_sha256,
            input_content_digest=document.inputs.input_content_digest,
            ontology_id=document.ontology_id,
            ontology_version=document.ontology_version,
            ontology_definition_hash=document.ontology_definition_hash,
            node_count=document.counts.nodes,
            edge_count=document.counts.edges,
        )

    def extraction_run_path(self, root: Path) -> Path:
        """Where the extraction directory the manifest names lives on *this* machine.

        The manifest records the path repo-relative where it can (`graph/core/manifest.py`'s
        `_relative_to`), precisely so two manifests of one projection do not differ over a home
        directory. An absolute entry is still possible — a projection run against a directory
        outside the repository — and wins outright, which is the same resolution rule
        `story/context.py:_under` uses for its two roots.
        """
        named = Path(self.extraction_run_directory)
        return named if named.is_absolute() else Path(root) / named


def read_graph_identity(graph_runs_root: Path, graph_run_id: str) -> GraphIdentity:
    """The identity of one projected run, or `GraphIdentityError` naming what was missing.

    Every failure carries the path it looked at. A gate that reported "no manifest" without
    saying where it looked would send an operator to guess between `data/graph_runs`, a
    `--graph-runs-root` override and a typo in the run id.
    """
    directory = Path(graph_runs_root) / graph_run_id
    manifest_path = directory / GRAPH_MANIFEST_FILENAME
    if not directory.is_dir():
        raise GraphIdentityError(
            f"no graph run directory at {directory}: nothing has projected {graph_run_id!r} "
            "into this root")
    if not manifest_path.is_file():
        raise GraphIdentityError(
            f"{directory} holds no {GRAPH_MANIFEST_FILENAME}; the manifest is written last, so "
            "this directory is an unfinished projection rather than a run to read")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # `UnicodeDecodeError` is named because it is a `ValueError` and **not** an `OSError`:
        # the two-arm form this line shipped with let a `manifest.json` holding non-UTF-8 bytes
        # raise straight out of §7's gate, which promises a structured refusal from the stage
        # that runs before every command. `json.JSONDecodeError` is a `ValueError` too — the
        # arm that was already here is the reason the gap was not obvious.
        raise GraphIdentityError(
            f"{manifest_path} could not be read: {type(exc).__name__}: {exc}") from exc
    try:
        document = GraphRunManifestDocument.model_validate(payload)
    except ValidationError as exc:
        raise GraphIdentityError(
            f"{manifest_path} is not a graph run manifest this reader understands: {exc}"
        ) from exc
    if document.graph_run_id != graph_run_id:
        raise GraphIdentityError(
            f"{manifest_path} names graph run {document.graph_run_id!r} but sits in a directory "
            f"called {graph_run_id!r}; a run id is derived from its inputs and a directory that "
            "disagrees with its own manifest identifies nothing")
    return GraphIdentity.from_document(document)


__all__ = [
    "GRAPH_MANIFEST_FILENAME",
    "GraphIdentity",
    "GraphIdentityError",
    "GraphRunCounts",
    "GraphRunInputs",
    "GraphRunManifestDocument",
    "read_graph_identity",
]
