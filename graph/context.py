"""Composition root.

The single place a concrete implementation is named, matching the three packages before it.
At G1 there is exactly one — `CatalogProjection` — and no store: `contracts.GraphStore` is
declared and implemented nowhere, which is what keeps `neo4j` out of a package that does not
need it yet (V1_GRAPH_PROTOTYPE §11).

**Where paths come from, and why there is no `config/graph.yaml`.** The two inputs a
projection reads are extraction's run directory and normalization's catalog, and
`config/extraction.yaml` already states where both live — reading it here means the graph
cannot drift from the layer it consumes. The one path the graph owns, `data/graph_runs/`, is
stated below. A `config/graph.yaml` belongs with G2's connection settings (Bolt URI,
credentials, batch size), which is the point at which configuration stops being a single
directory name; inventing the file now would put one constant in a document nothing else
reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extraction.core.config import ExtractionConfig, load_config
from ontology import DEFAULT_ONTOLOGY_ID, load_ontology

#: Resolved against the repository root, beside `data/extraction_runs/`. Gitignored with the
#: rest of `data/` — a graph run is derived output, rebuildable from the run and the ontology.
GRAPH_RUNS_DIRNAME = "data/graph_runs"


@dataclass(frozen=True)
class GraphContext:
    """Everything a projection needs that is not the run being projected.

    Frozen and small on purpose: the run directory is an *argument* to the pipeline rather
    than a member here, because one context projects any number of runs and a context that
    named one would make "project this other run" mean "build a second context".
    """

    config: ExtractionConfig
    ontology: Any
    graph_runs_root: Path

    @property
    def root(self) -> Path:
        """The repository root. `code_commit` and the manifest's relative paths use it."""
        return self.config.root

    @property
    def extraction_runs_root(self) -> Path:
        return self.config.runs_root

    @property
    def normalization_catalog(self) -> Path:
        return self.config.catalog_root

    def resolve_extraction_run(self, run: Path | str) -> Path:
        """A run directory, given either a path or a bare run id.

        Both spellings exist in the wild — `data/extraction_runs/extract-v1-lexical-2422c…`
        is what a shell tab-completes, the id is what a manifest records — and resolving them
        here keeps the pipeline from having two entry points that differ only in that.
        """
        candidate = Path(run)
        if candidate.is_dir():
            return candidate
        return self.extraction_runs_root / str(run)


def build_graph_context(
    root: Path | None = None,
    *,
    ontology_id: str = DEFAULT_ONTOLOGY_ID,
    graph_runs_root: Path | str | None = None,
) -> GraphContext:
    """Load the configuration and the ontology. Nothing is read from a run yet."""
    config = load_config(root)
    resolved = (Path(graph_runs_root) if graph_runs_root is not None
                else config.root / GRAPH_RUNS_DIRNAME)
    return GraphContext(
        config=config,
        ontology=load_ontology(ontology_id),
        graph_runs_root=resolved,
    )


__all__ = ["GRAPH_RUNS_DIRNAME", "GraphContext", "build_graph_context"]
