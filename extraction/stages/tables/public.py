"""Public contract for the deterministic table lane.

Issue codes are as much of the output as the claims are. A row that produced no claim must
say which of ten reasons applies and carry enough to reproduce the decision — the benchmark
scores 24 abstentions, and a lane that cannot explain a silence cannot be scored on one.
"""

from __future__ import annotations

from dataclasses import dataclass

# -- issue codes ---------------------------------------------------------------------------
AMBIGUOUS_ALIAS = "AMBIGUOUS_ALIAS"
UNRESOLVED_METRIC = "UNRESOLVED_METRIC"
DEFERRED_REQUIRED_SOURCE_LANE = "DEFERRED_REQUIRED_SOURCE_LANE"
MISSING_PERIOD = "MISSING_PERIOD"
PERIOD_TYPE_MISMATCH = "PERIOD_TYPE_MISMATCH"
MISSING_UNIT = "MISSING_UNIT"
INVALID_NUMBER = "INVALID_NUMBER"
AMBIGUOUS_COLUMN_ALIGNMENT = "AMBIGUOUS_COLUMN_ALIGNMENT"
UNSUPPORTED_TABLE_SHAPE = "UNSUPPORTED_TABLE_SHAPE"
MISSING_REQUIRED_CONTEXT = "MISSING_REQUIRED_CONTEXT"
DERIVED_CHANGE_COLUMN = "DERIVED_CHANGE_COLUMN"

ISSUE_CODES = frozenset({
    AMBIGUOUS_ALIAS, UNRESOLVED_METRIC, DEFERRED_REQUIRED_SOURCE_LANE, MISSING_PERIOD,
    PERIOD_TYPE_MISMATCH, MISSING_UNIT, INVALID_NUMBER, AMBIGUOUS_COLUMN_ALIGNMENT,
    UNSUPPORTED_TABLE_SHAPE, MISSING_REQUIRED_CONTEXT, DERIVED_CHANGE_COLUMN,
})

# -- where a scale came from -----------------------------------------------------------------
TABLE_HEADER = "table_header"
PRECEDING_CONTEXT = "preceding_context"
METRIC_DEFAULT = "metric_default"
SCALE_SOURCES = frozenset({TABLE_HEADER, PRECEDING_CONTEXT, METRIC_DEFAULT})

# -- how the subject was decided ---------------------------------------------------------------
REGISTRANT_METADATA = "registrant_metadata"
SUBJECT_BASES = frozenset({REGISTRANT_METADATA})


@dataclass(frozen=True)
class TableCellRef:
    """Where something sits in the source table, in original coordinates."""

    row_index: int
    column_index: int
    text: str


@dataclass(frozen=True)
class TableIssue:
    """A structured non-claim, carrying enough to reproduce the decision."""

    code: str
    detail: str
    passage_id: str
    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    raw_label: str | None = None
    normalized_label: str | None = None
    candidate_concept_ids: tuple[str, ...] = ()
    required_lane: str | None = None
    raw_value: str | None = None
