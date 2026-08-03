"""Composition root.

The single place a concrete implementation is named, mirroring `graph/context.py`. At S0c
there is exactly one — `Neo4jReadExecutor` — and building it here is the whole of decision D1:
no retrieval tool, no detector and nothing under `story/core/` constructs a driver, so every
stage above this file is written against `contracts.ReadQueryExecutor` and is testable with no
server running.

**The overrides are the dependency injection.** `build_story_context(executor=…)` is how a
test, and later `story run --offline`, supplies a recorded executor without this module
knowing that such a thing exists. Keyword-only and `| None` for the same reason
`build_graph_context` uses that shape: an override that has to be spelled out is one nobody
passes by accident.

**Why there is no `config/story.yaml` here.** S11 owns it, and it will carry the per-tool row
caps, timeouts and budgets §16 and §10.2 specify. Everything this context needs today is
already stated somewhere tracked: the Neo4j block in `config/graph.yaml`, read by
`story/providers/neo4j_connection.py`, and two directory names below. Minting the file now to
hold two constants would give a reader a second place to look for an answer that has not
changed.

Small on purpose. A story run's *inputs* — which graph run, which candidate — are arguments to
the pipeline, not members here; one context serves any number of runs, and a context that
named one would make "run the next candidate" mean "build a second context".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from story.contracts import ReadQueryExecutor
from story.providers.neo4j_connection import (
    StoryNeo4jSettings,
    load_neo4j_settings,
    open_read_executor,
)

#: `story/context.py` -> `story` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: What the freshness gate reads (§7) and what a story run writes (§14). Repository-relative,
#: both gitignored. `data/graph_runs` is also stated in `config/graph.yaml` as
#: `paths.graph_runs_root`, exactly as `graph/context.py` states it as `GRAPH_RUNS_DIRNAME`;
#: `tests/story/test_story_neo4j_adapter.py` asserts the two agree, so the duplication is
#: checked rather than trusted. `data/story_runs` is this package's own and appears nowhere
#: else yet.
GRAPH_RUNS_DIRNAME = "data/graph_runs"
STORY_RUNS_DIRNAME = "data/story_runs"


@dataclass(frozen=True)
class StoryContext:
    """Everything a story run needs that is not the run itself.

    `executor` is typed as the protocol, not as `Neo4jReadExecutor`: this file is the only one
    that knows which implementation is in use, and typing the field concretely would make
    every consumer of the context know it too.
    """

    executor: ReadQueryExecutor
    root: Path
    graph_runs_root: Path
    story_runs_root: Path

    def close(self) -> None:
        """Release the executor. A run that refuses at the freshness gate still closes."""
        self.executor.close()

    def __enter__(self) -> StoryContext:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def build_story_context(
    root: Path | None = None,
    *,
    executor: ReadQueryExecutor | None = None,
    settings: StoryNeo4jSettings | None = None,
    graph_runs_root: Path | str | None = None,
    story_runs_root: Path | str | None = None,
) -> StoryContext:
    """Resolve the paths and open the read executor.

    `executor` short-circuits the driver entirely — nothing is connected, and no environment is
    read — which is how an offline test builds a real context around a fake. `settings` is the
    narrower override: build the real adapter, but against a target the caller named. Passing
    both is not an error; `executor` simply wins, because a caller that supplied one has said
    it does not want a connection opened.
    """
    resolved_root = REPO_ROOT if root is None else Path(root)
    return StoryContext(
        executor=executor if executor is not None else open_read_executor(
            load_neo4j_settings() if settings is None else settings),
        root=resolved_root,
        graph_runs_root=_under(resolved_root, graph_runs_root, GRAPH_RUNS_DIRNAME),
        story_runs_root=_under(resolved_root, story_runs_root, STORY_RUNS_DIRNAME),
    )


def _under(root: Path, override: Path | str | None, default: str) -> Path:
    """An absolute override wins outright; a relative one resolves against `root`."""
    if override is None:
        return root / default
    return Path(override) if Path(override).is_absolute() else root / override


__all__ = [
    "GRAPH_RUNS_DIRNAME",
    "REPO_ROOT",
    "STORY_RUNS_DIRNAME",
    "StoryContext",
    "build_story_context",
]
