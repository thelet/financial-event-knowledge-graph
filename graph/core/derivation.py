"""The two things the run does not record, re-derived from what it does record.

Both were established at G0 against `extract-v1-lexical-2422c4252c07` and both are *checked*
rather than asserted — a derived number nobody compares to an independently recorded one is
an invention with extra steps.

1. **`observation_id`, recomputed end to end.** No catalog carries `period_key`, but the
   id-bearing grid coordinates survive as `extractor_metadata.metric_label_row_index` /
   `period_header_column_index`, so the whole id is reproducible. *Verified at G0: 2,707 of
   2,707, zero mismatches.* §4.1, §5.4 point 3.
2. **Per-claim validation warnings.** The run's 186 `unpreferred_source_lane` warnings exist
   only as a manifest aggregate: `CheckFinding` carries a `claim_id`
   (`extraction/stages/verify/public.py:49-53`) and `as_row()` writes counts, discarding the
   findings (`:70-80`). The graph reconstructs each `MetricObservation` and calls the
   ontology's own `validate_observation()` — the same validator the run used — then
   reconciles the total against the manifest. §5.5.

Neither reimplements a rule. `PeriodRef.key` and `observation_id` are imported from
extraction; the warning rule belongs to the ontology and is invoked, not copied.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from extraction.core.identifiers import observation_id as build_observation_id
from extraction.core.models import PeriodRef
from ontology.contracts import Ontology
from ontology.core.models import EvidenceReference, MetricObservation, Population

from .inputs import (
    ClaimRow,
    EvidenceRowT,
    GraphInputError,
    ObservationRow,
    RunManifest,
)

#: The `extractor_metadata` keys that reproduce a table reading's grid coordinates.
#: **`value_column_index` is a different column** and reproduces only 582 of 2,707 ids
#: (§5.4 point 3). Named here so the wrong one cannot be substituted by accident.
ROW_INDEX_KEY = "metric_label_row_index"
COLUMN_INDEX_KEY = "period_header_column_index"

#: The check whose aggregate §5.5 reconciles against.
ONTOLOGY_VALIDATION_CHECK = "ontology_validation"


class ObservationIdMismatch(GraphInputError):
    """A recomputed `observation_id` differs from the one the catalog supplied."""


class WarningCountMismatch(GraphInputError):
    """The derived warning total and the manifest's aggregate disagree."""


class ObservationValidationError(GraphInputError):
    """Re-validating a catalogued observation produced an *error*, not a warning.

    A claim that reached a catalog is error-free by construction (§1.3, and the run's own
    `ontology_validation` check records 2,717 examined / 0 failures). One appearing here
    means the graph reconstructed the observation wrongly or the ontology changed under a
    finalized run — either way, not something to project.
    """


def period_key(observation: ObservationRow) -> str:
    """`PeriodRef.key`, driven by the model itself rather than reimplemented.

    `extraction/core/models.py:134-149` is the algorithm — `FY2023`, `2023Q4`, `2023-12-31`,
    else `{start}_{end}`. Importing it is the whole point: a second copy of a period rule is
    how an id scheme drifts from the ids already on disk.
    """
    return PeriodRef(
        period_start=observation.period_start,
        period_end=observation.period_end,
        instant_date=observation.instant_date,
    ).key


def structural_position(claim: ClaimRow) -> tuple[str, ...]:
    """The grid coordinates an `observation_id` digests, or `()` for a narrative reading.

    Keys are **omitted, not nulled** when absent (§5.4 point 1), so presence is the test.
    An empty tuple reproduces the plain `digest(passage_id)` byte for byte
    (`identifiers.py:87-90`), which is why narrative ids need no special case.
    """
    metadata = claim.extractor_metadata
    if ROW_INDEX_KEY in metadata and COLUMN_INDEX_KEY in metadata:
        return (f"row={metadata[ROW_INDEX_KEY]}", f"column={metadata[COLUMN_INDEX_KEY]}")
    return ()


def recompute_observation_id(observation: ObservationRow, claim: ClaimRow) -> str:
    """Rebuild the whole id from the catalogs: metric, subject, period, lane, position.

    `source_lane` — the ontology's vocabulary — is the lane segment, not the routing `lane`
    (§2.3 trap 1); the two are different strings and only one is in the id.
    """
    return build_observation_id(
        observation.metric_id,
        observation.subject_entity_id,
        period_key(observation),
        observation.source_lane,
        observation.passage_id,
        structural_position(claim),
    )


def mismatched_observation_ids(
    observations: Sequence[ObservationRow], claims: Mapping[str, ClaimRow]
) -> tuple[tuple[str, str], ...]:
    """Every `(supplied, recomputed)` pair that disagrees. Empty on a healthy run."""
    mismatches: list[tuple[str, str]] = []
    for observation in observations:
        claim = claims.get(observation.claim_id)
        if claim is None:
            raise GraphInputError(
                f"{observation.observation_id}: no claim row for {observation.claim_id!r}")
        recomputed = recompute_observation_id(observation, claim)
        if recomputed != observation.observation_id:
            mismatches.append((observation.observation_id, recomputed))
    return tuple(mismatches)


def check_observation_ids(
    observations: Sequence[ObservationRow], claims: Mapping[str, ClaimRow]
) -> None:
    """Raise on any divergence between the graph's reading and extraction's identity rule."""
    mismatches = mismatched_observation_ids(observations, claims)
    if mismatches:
        supplied, recomputed = mismatches[0]
        raise ObservationIdMismatch(
            f"{len(mismatches)} observation id(s) do not recompute; first: catalog "
            f"{supplied!r} vs recomputed {recomputed!r}")


# -- warning derivation ----------------------------------------------------------------


def _evidence_reference(row: EvidenceRowT) -> EvidenceReference:
    """The evidence row as the ontology's boundary model.

    Char offsets are absent by construction — the catalog writer drops them (§2.3 trap 3) —
    so they are left at their defaults rather than reconstructed from somewhere else.

    Written from the row's *own* fields rather than from a fixed list (F0 Part B): the seven
    evidence kinds have six row shapes between them, and naming `passage_id` unconditionally
    would make this raise on an `xbrl_fact` row that legitimately has no such column. Every
    key a row carries is a field of `EvidenceReference` by construction — the writer builds
    the row from one — so this is a projection, not a translation, and a field that stopped
    lining up would fail here on `extra="forbid"` rather than be dropped.
    """
    carried = row.model_dump()
    carried.pop("claim_id", None)
    carried.pop("claim_kind", None)
    carried.pop("evidence_index", None)
    return EvidenceReference(**carried)


def reconstruct_observation(
    observation: ObservationRow, evidence: Sequence[EvidenceRowT]
) -> MetricObservation:
    """A `MetricObservation` from the two catalogs that hold one, and nothing invented.

    **`population_role` and `population_confidence` are never set.** They exist in no catalog,
    and constructing a `Population` for their sake would emit the model defaults `baseline`
    and `medium` on every observation — a provenance the extractor never wrote, in the layer
    whose rule is "fail loudly, never default" (§5.4). A `Population` is built only when
    `population_definition_raw` is non-null, and carries only that field.

    `reported_at`, `dimensions` and `reporting_basis` are likewise absent from the catalogs
    and left unset. Review re-ran the derivation with each of them varied and got 186 every
    time — `_warn_on_unpreferred_lane` reads only `source_lane` and the metric definition
    (`ontology/validation.py:287-306`) — so omitting them changes no answer and inventing
    them would change what the graph claims the run said.
    """
    fields = {}
    if observation.population_definition_raw is not None:
        fields["population"] = Population(
            definition_raw=observation.population_definition_raw)
    return MetricObservation(
        observation_id=observation.observation_id,
        metric_id=observation.metric_id,
        subject_entity_id=observation.subject_entity_id,
        subject_type=observation.subject_type,
        value=observation.value,
        unit=observation.unit,
        currency=observation.currency,
        period_start=observation.period_start,
        period_end=observation.period_end,
        instant_date=observation.instant_date,
        source_lane=observation.source_lane,
        evidence=tuple(_evidence_reference(row) for row in evidence),
        assertion_type=observation.assertion_type,
        confidence=observation.confidence,
        **fields,
    )


def derive_warnings(
    observations: Sequence[ObservationRow],
    evidence: Sequence[EvidenceRowT],
    ontology: Ontology,
) -> dict[str, tuple[str, ...]]:
    """`claim_id -> warning codes`, for the claims that carry one.

    Only warned claims appear, so `len(result)` is the number of warned observations and
    `sum(len(codes))` is the number of warnings. Both are 186 on this run because every
    warned observation carries exactly one code; they are different quantities and §5.5
    reconciles on the second.
    """
    by_claim: dict[str, list[EvidenceRowT]] = {}
    for row in evidence:
        by_claim.setdefault(row.claim_id, []).append(row)

    warnings: dict[str, tuple[str, ...]] = {}
    for observation in observations:
        rebuilt = reconstruct_observation(
            observation, by_claim.get(observation.claim_id, ()))
        result = ontology.validate_observation(rebuilt)
        if result.errors:
            first = result.errors[0]
            raise ObservationValidationError(
                f"{observation.observation_id}: re-validation produced an error "
                f"({first.code}: {first.message}); a catalogued claim is error-free by "
                "construction (§1.3)")
        if result.warnings:
            warnings[observation.claim_id] = tuple(
                sorted(issue.code for issue in result.warnings))
    return warnings


def warning_total(derived: Mapping[str, Sequence[str]]) -> int:
    """Warnings, not warned claims — the quantity the manifest's aggregate counts."""
    return sum(len(codes) for codes in derived.values())


def reconcile_warning_count(
    derived: Mapping[str, Sequence[str]], manifest: RunManifest
) -> int:
    """Assert the derived total equals the manifest's, and say what the check does not prove.

    The equality is the point *and* it is weaker than it looks, which is why the message
    states both numbers and both denominators: the manifest's total comes from
    `validate_claims` over all **2,717 claims** including the 6 events and 4 relationships
    (`extraction/stages/verify/public.py:38`), while this derivation covers the **2,707
    observations** only. The two agree today because this ontology produces no event- or
    relationship-level warning. A future one would break the equality for a *stated* reason
    rather than an apparently mysterious one (§5.5).
    """
    total = warning_total(derived)
    expected = manifest.check(ONTOLOGY_VALIDATION_CHECK).warnings
    if total != expected:
        raise WarningCountMismatch(
            f"derived {total} warning(s) over {len(derived)} warned observation(s); "
            f"manifest.verification[{ONTOLOGY_VALIDATION_CHECK}].warnings is {expected}. "
            "The denominators differ: the manifest counts warnings over all "
            f"{manifest.counts.get('claims', 'all')} claims including events and "
            f"relationships, while this derivation covers the "
            f"{manifest.counts.get('observations', 'observation')} observations only. "
            "A disagreement is therefore either a changed observation rule or a new "
            "event/relationship-level warning — not a rounding difference.")
    return total


__all__ = [
    "COLUMN_INDEX_KEY",
    "ONTOLOGY_VALIDATION_CHECK",
    "ObservationIdMismatch",
    "ObservationValidationError",
    "ROW_INDEX_KEY",
    "WarningCountMismatch",
    "check_observation_ids",
    "derive_warnings",
    "mismatched_observation_ids",
    "period_key",
    "recompute_observation_id",
    "reconcile_warning_count",
    "reconstruct_observation",
    "structural_position",
    "warning_total",
]
