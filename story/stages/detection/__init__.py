"""S2 and S3 — the canonical layer every detector stands on, and the four detectors on it.

`canonicalization.py` is §6.1's policy: it reads observations through the injected
`GraphRetriever` and turns them into the `CanonicalPoint`s of `story/core/series.py`. The
comparability rules R1–R10 are **not** here, because `core/series.py` is where a type and the
rules about it belong and because S5's packaging needs them without importing a stage.

`detector_config.py` holds the *parameters* of detection — thresholds, polarity, sign
convention, floors — and none of its arithmetic. The four detectors of §6.6 hold the arithmetic
and nothing else:

    metric_move.py               D1 — one step is large for this metric
    trend_reversal.py            D2 — a run of steps turned, by more than σ
    acceleration.py              D3 — three same-sign steps of growing magnitude
    cross_metric_divergence.py   D4 — two definitions of one quantity stopped agreeing

A constant that must agree across detectors lives in `detector_config`; a constant only one
detector can use lives with that detector. `DELTA_PRECISION` is the sharpest case — R8 in
`core/series.py` rounds a difference to it before testing a presentation tolerance, so `core/`
owns the literal, `detector_config` binds it, and every detector reads it from there.

**Three names per detector are deliberately not re-exported here**: `DETECTOR_ID`,
`DETECTOR_VERSION` and `STORY_TYPE`, along with the refusal codes two detectors each declare
(`SERIES_INCOMPLETE`, `POPULATION_TOO_SMALL`, `LOW_VARIANCE_PRESENTATION_NOISE`). A flat
re-export would either collide or mint a second, package-level name for a value §6.11 digests
into every `candidate_id`. Reach them through the module — `from
story.stages.detection.acceleration import DETECTOR_ID` — where the answer is unambiguous.

No model, no ranking, no prose, at either stage. §6 puts a model nowhere in this path and this
is where that would first be tempting.
"""

from __future__ import annotations

from story.stages.detection.acceleration import (
    MAGNITUDE_RATIO,
    RUN_LENGTH,
    WINDOW_POINTS,
    AccelerationRefusal,
    AccelerationResult,
    accelerates,
    delta_between,
    delta_sign,
    detect_acceleration,
    detect_acceleration_from_load,
)
from story.stages.detection.canonicalization import (
    POLICY_VERSION,
    ObservationLoad,
    SlotCensus,
    canonicalize,
    canonicalize_slot,
    census,
    is_flattened_table_read,
    load_observations,
    record_from_rows,
)
from story.stages.detection.cross_metric_divergence import (
    DIVERGENCE_PAIRS,
    DIVERGENCE_SHAPES,
    POPULATION_EXCLUDES_PERIODS,
    RELATION_UNDECLARED,
    RULE_ID,
    SERIES_ABSENT,
    VARIANCE_FLOOR_MULTIPLE,
    Z_MIN,
    DefinitionalRelation,
    DivergencePair,
    DivergenceRefusal,
    DivergenceResult,
    GapPoint,
    RelationKind,
    definitional_relation,
    detect_cross_metric_divergence,
)
from story.stages.detection.detector_config import (
    COMPARABLE_SHAPES,
    DELTA_PRECISION,
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    DIRECTION_UNCHANGED,
    FAMILY_THRESHOLDS,
    FLOOR_FRACTION,
    METRIC_FAMILY,
    METRIC_POLARITY,
    MIN_DELTA_POPULATION,
    POLARITY_UNDECLARED,
    SIGN_CONVENTION_UNVERIFIED,
    THRESHOLD_UNMEASURED,
    VALUE_SIGN,
    FamilyThresholds,
    MetricFamily,
    MetricPolarity,
    ValueSign,
    family_of,
    polarity_of,
    quantity_direction,
    round_delta,
    thresholds_for,
    value_floor,
    value_sign_of,
)
from story.stages.detection.metric_move import (
    ARM_ABSOLUTE,
    ARM_PERCENTAGE_POINTS,
    ARM_RELATIVE_PERCENT,
    PERCENT_UNIT,
    MetricMoveRefusal,
    MetricMoveResult,
    MoveMeasurement,
    candidate_from_move,
    detect_metric_moves,
    detect_metric_moves_from_load,
    measure_move,
)
from story.stages.detection.trend_reversal import (
    MIN_RUN,
    SIGMA_MULTIPLE,
    TrendReversalRefusal,
    TrendReversalResult,
    detect_trend_reversals,
    detect_trend_reversals_from_load,
    reverses,
)

__all__ = [
    "ARM_ABSOLUTE",
    "ARM_PERCENTAGE_POINTS",
    "ARM_RELATIVE_PERCENT",
    "COMPARABLE_SHAPES",
    "DELTA_PRECISION",
    "DIRECTION_DECREASE",
    "DIRECTION_INCREASE",
    "DIRECTION_UNCHANGED",
    "DIVERGENCE_PAIRS",
    "DIVERGENCE_SHAPES",
    "FAMILY_THRESHOLDS",
    "FLOOR_FRACTION",
    "MAGNITUDE_RATIO",
    "METRIC_FAMILY",
    "METRIC_POLARITY",
    "MIN_DELTA_POPULATION",
    "MIN_RUN",
    "PERCENT_UNIT",
    "POLARITY_UNDECLARED",
    "POLICY_VERSION",
    "POPULATION_EXCLUDES_PERIODS",
    "RELATION_UNDECLARED",
    "RULE_ID",
    "RUN_LENGTH",
    "SERIES_ABSENT",
    "SIGMA_MULTIPLE",
    "SIGN_CONVENTION_UNVERIFIED",
    "THRESHOLD_UNMEASURED",
    "VALUE_SIGN",
    "VARIANCE_FLOOR_MULTIPLE",
    "WINDOW_POINTS",
    "Z_MIN",
    "AccelerationRefusal",
    "AccelerationResult",
    "DefinitionalRelation",
    "DivergencePair",
    "DivergenceRefusal",
    "DivergenceResult",
    "FamilyThresholds",
    "GapPoint",
    "MetricFamily",
    "MetricMoveRefusal",
    "MetricMoveResult",
    "MetricPolarity",
    "MoveMeasurement",
    "ObservationLoad",
    "RelationKind",
    "SlotCensus",
    "TrendReversalRefusal",
    "TrendReversalResult",
    "ValueSign",
    "accelerates",
    "candidate_from_move",
    "canonicalize",
    "canonicalize_slot",
    "census",
    "definitional_relation",
    "delta_between",
    "delta_sign",
    "detect_acceleration",
    "detect_acceleration_from_load",
    "detect_cross_metric_divergence",
    "detect_metric_moves",
    "detect_metric_moves_from_load",
    "detect_trend_reversals",
    "detect_trend_reversals_from_load",
    "family_of",
    "is_flattened_table_read",
    "load_observations",
    "measure_move",
    "polarity_of",
    "quantity_direction",
    "record_from_rows",
    "reverses",
    "round_delta",
    "thresholds_for",
    "value_floor",
    "value_sign_of",
]
