"""Providers: the only place in `story/` an external system is reached.

Two of them by the end of V1 — Bolt at S0c and an OpenAI-compatible HTTP server at S6 — and
they share one property that the rest of the package is defined by not having: they know a
wire format. `story/core/` must stay constructible with no database, no model server and no
environment, and `story/stages/` reaches both of these through a protocol in
`story/contracts.py` rather than by importing anything here.

**Nothing is re-exported from this file, deliberately.** Python runs a package's `__init__`
before any submodule of it, so `from story.providers.local_openai_compatible import …` would
import `neo4j` too if this file named `neo4j_connection`. `graph/stages/load/__init__.py`
takes the same decision for the same reason, and `extraction/providers/__init__.py` records
the measurement that forced it: an eager re-export put `httpx` into `sys.modules` for a lane
that names no HTTP client, and the import-graph test could not see it.
"""

from __future__ import annotations
