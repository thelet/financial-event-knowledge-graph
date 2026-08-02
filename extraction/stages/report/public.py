"""Public contract for the run report: the numbers, computed once, from the run directory.

**Every number here is derived from the persisted files and from the ontology, and from
nothing else.** That is not a style preference. Steps 11 and 12 both shipped rendered tables
that no test checked, and step 12's disagreed with the data they rendered — found by
adversarial review, not by the suite. The split this module enforces is the fix: `build_report`
computes plain data from `claims.jsonl`, `observations.jsonl`, `events.jsonl`,
`relationships.jsonl`, `issues.jsonl`, `rejected_claims.jsonl` and `lane_outputs.jsonl`, and
`markdown_report.render` formats it and computes nothing. A test then reads every rendered cell
back and asserts it is the value it renders, and a second test recomputes the data from the
files.

**The in-scope metric list comes from the ontology, never from a list here.** STAGE_13 §4: all
20 in-scope metrics with their claim counts *including the zeros*, which is the number a
coverage table exists to show. In scope means "the first entry of `source_lane_preferences` is
not the lane this corpus lacks", which is `assembly.deferred_metric_ids`' complement — asked of
the vocabulary, so adding a metric changes the table with no edit here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REPORT_FILENAME = "report.md"

# Codes whose no-claim candidates are listed passage by passage in the report rather than only
# counted. A ceiling, not a filter: above it the report gives the count and names the catalog
# that holds every row, because a corpus-scale run produces five figures of them and a document
# that pasted all of them in would be a worse record than the file already is.
PASSAGE_LIST_CEILING = 25


@dataclass
class RunReport:
    """Plain data. No formatting, no derivation at render time.

    Every field is a list of dicts or a dict of scalars so that a renderer can only look values
    up. A renderer that could compute is a renderer that can disagree with the run.
    """

    run_id: str
    headline: dict[str, Any] = field(default_factory=dict)
    corpus: dict[str, Any] = field(default_factory=dict)
    ontology: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    bounds: dict[str, Any] = field(default_factory=dict)
    metric_coverage: list[dict[str, Any]] = field(default_factory=list)
    deferred_metrics: list[dict[str, Any]] = field(default_factory=list)
    no_claim_by_code: list[dict[str, Any]] = field(default_factory=list)
    no_claim_passages: dict[str, list[str]] = field(default_factory=dict)
    by_lane: list[dict[str, Any]] = field(default_factory=list)
    by_document_type: list[dict[str, Any]] = field(default_factory=list)
    by_claim_kind: list[dict[str, Any]] = field(default_factory=list)
    unselected: list[dict[str, Any]] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    temporal_residual: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    scoping: dict[str, Any] = field(default_factory=dict)
    follow_up: list[str] = field(default_factory=list)
