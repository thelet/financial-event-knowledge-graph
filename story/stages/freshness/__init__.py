"""§7's staleness gate: refuse to proceed when the loaded graph no longer matches its inputs.

The one deliverable that has value before anything else in this package is written, and the
only check in the plan that catches §17.8 — a published post holding numbers from a run the
graph no longer contains. Three checks (`gate.py`), four read statements (`loaded_graph.py`),
one structured verdict (`freshness_report.py`); no writes, no model, no detector.

Three modules rather than one because two of the three concerns are worth isolating: the report
is a value with no idea what a database is, and the Cypher is confined to the single module the
write-keyword scan has to read. What is left in `gate.py` is the comparison, which is where the
plan's argument lives. Splitting further — a module per check — would be one file per function.

Entry point:

    report = check_freshness(executor=…, graph_run_id=…, graph_runs_root=…, root=…)
    if not report.passed:
        print(report.describe())   # every refusal names its code, its expectation, its finding
"""

from __future__ import annotations

from story.stages.freshness.freshness_report import (
    FreshnessCheck,
    FreshnessReport,
    RefusalCode,
)
from story.stages.freshness.gate import (
    check_freshness,
    extraction_input_checks,
    loaded_graph_checks,
)
from story.stages.freshness.loaded_graph import (
    FRESHNESS_STATEMENTS,
    FRESHNESS_TIMEOUT_SECONDS,
    LoadedGraph,
    LoadMarkerRow,
    read_loaded_graph,
)

__all__ = [
    "FRESHNESS_STATEMENTS",
    "FRESHNESS_TIMEOUT_SECONDS",
    "FreshnessCheck",
    "FreshnessReport",
    "LoadMarkerRow",
    "LoadedGraph",
    "RefusalCode",
    "check_freshness",
    "extraction_input_checks",
    "loaded_graph_checks",
    "read_loaded_graph",
]
