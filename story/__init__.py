"""The story agent: deterministic discovery, bounded evidence, constrained generation.

Reads the graph a completed projection loaded and never writes to it (V1_STORY_AGENT §16).
Everything a model is allowed to see is assembled by code first — the package in
`core/models.py` is the model's entire universe — and everything a model produces is checked
against that package before it can be published.

**Boundaries.** This package imports `ontology`, `ontology.core.*`, `ontology.contracts`,
`extraction.core.*`, `graph.core.*` and `graph.contracts`, and nothing else first-party.
Never `extraction.stages`, `extraction.providers`, `extraction.contracts`, `normalization.*`,
`acquisition.*` or `graph.stages.*`; and nothing upstream may import this package.
`tests/story/test_story_package_structure.py` enforces all of that rather than documenting it.

At S0 the package is contracts only: the frozen types, the deterministic id scheme and the
run manifest. No stage exists yet, and none is created as an empty placeholder.
"""

from __future__ import annotations

#: The layout of `data/story_runs/<story_run_id>/` (§14). Bumped when the directory shape
#: changes, and a digest input to `story_run_id`, so a layout change mints new directories
#: rather than writing a new shape on top of an old one.
STORY_LAYOUT_VERSION = "1.0.0"

__all__ = ["STORY_LAYOUT_VERSION"]
