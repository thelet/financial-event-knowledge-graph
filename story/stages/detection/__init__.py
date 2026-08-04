"""S2 — the canonical layer every detector stands on. §6.1's policy and nothing above it.

One module today, `canonicalization.py`: it reads observations through the injected
`GraphRetriever` and turns them into the `CanonicalPoint`s of `story/core/series.py`. The
detectors of §6.5 land beside it at S3 and consume the series; the comparability rules R1–R10
are **not** here, because `core/series.py` is where a type and the rules about it belong and
because S5's packaging needs them without importing a stage.

No model, no ranking, no prose. §6 puts a model nowhere in this path and this stage is where
that would first be tempting.
"""

from __future__ import annotations

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

__all__ = [
    "POLICY_VERSION",
    "ObservationLoad",
    "SlotCensus",
    "canonicalize",
    "canonicalize_slot",
    "census",
    "is_flattened_table_read",
    "load_observations",
    "record_from_rows",
]
