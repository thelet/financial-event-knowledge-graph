"""The two derivations, checked against numbers the run recorded independently.

`observation_id` recomputation and warning re-derivation are both re-*derivations*, not
inventions — but that claim is only worth anything if it is tested against the run's own
answer. So: every fixture row, every real row where the run is present, and the manifest
aggregate the warning total must reconcile with.
"""

from __future__ import annotations

import pytest

from conftest import REAL_RUN_AVAILABLE, REAL_RUN_REASON
from graph.core.derivation import (
    COLUMN_INDEX_KEY,
    ROW_INDEX_KEY,
    ObservationIdMismatch,
    WarningCountMismatch,
    check_observation_ids,
    derive_warnings,
    mismatched_observation_ids,
    period_key,
    recompute_observation_id,
    reconstruct_observation,
    reconcile_warning_count,
    structural_position,
    warning_total,
)

requires_real_run = pytest.mark.skipif(not REAL_RUN_AVAILABLE, reason=REAL_RUN_REASON)

#: The fixture's warned observations, from its README's derivation table.
FIXTURE_WARNED_CLAIMS = {
    "claim:metric-observation:78dcfb719539", "claim:metric-observation:df70552c0f23",
    "claim:metric-observation:ec98a02e1677", "claim:metric-observation:5c015f5c2740",
    "claim:metric-observation:1695ebeafddb", "claim:metric-observation:f8970bff3aed",
    "claim:metric-observation:4137f2a36dfc", "claim:metric-observation:239937eb9239",
    "claim:metric-observation:ea8c43aa5dc2",
}


# -- observation ids -------------------------------------------------------------------


def test_every_fixture_observation_id_recomputes(fixture_inputs) -> None:
    assert mismatched_observation_ids(
        fixture_inputs.observations, fixture_inputs.claims_by_id) == ()
    check_observation_ids(fixture_inputs.observations, fixture_inputs.claims_by_id)


def test_the_recomputed_id_is_built_from_the_grid_coordinates(fixture_inputs) -> None:
    """§5.4 point 3: `metric_label_row_index` / `period_header_column_index`, not the value
    column."""
    table = next(row for row in fixture_inputs.observations
                 if row.source_lane == "normalized_table")
    claim = fixture_inputs.claims_by_id[table.claim_id]
    position = structural_position(claim)
    assert position == (f"row={claim.extractor_metadata[ROW_INDEX_KEY]}",
                        f"column={claim.extractor_metadata[COLUMN_INDEX_KEY]}")
    assert recompute_observation_id(table, claim) == table.observation_id


def test_a_narrative_reading_has_no_structural_position(fixture_inputs) -> None:
    narrative = next(row for row in fixture_inputs.observations
                     if row.source_lane == "normalized_narrative")
    claim = fixture_inputs.claims_by_id[narrative.claim_id]
    assert structural_position(claim) == ()
    assert recompute_observation_id(narrative, claim) == narrative.observation_id


def test_the_value_column_is_not_the_period_header_column(fixture_inputs) -> None:
    """The measured trap: `value_column_index` reproduces only 582 of 2,707 ids."""
    table = next(row for row in fixture_inputs.observations
                 if row.source_lane == "normalized_table")
    metadata = fixture_inputs.claims_by_id[table.claim_id].extractor_metadata
    if "value_column_index" in metadata:
        assert metadata["value_column_index"] != metadata[COLUMN_INDEX_KEY]


@pytest.mark.parametrize("start,end,instant,expected", [
    (None, None, "2022-06-30", "2022-06-30"),
    ("2023-01-01", "2023-12-31", None, "FY2023"),
    ("2022-04-01", "2022-06-30", None, "2022Q2"),
    ("2022-04-01", "2022-12-31", None, "2022-04-01_2022-12-31"),
])
def test_period_key_follows_the_extraction_algorithm(
        fixture_inputs, start, end, instant, expected) -> None:
    row = fixture_inputs.observations[0].model_copy(
        update={"period_start": start, "period_end": end, "instant_date": instant})
    assert period_key(row) == expected


def test_a_tampered_id_is_caught(fixture_inputs) -> None:
    tampered = fixture_inputs.observations[0].model_copy(
        update={"observation_id": "obs:not-the-real-id"})
    with pytest.raises(ObservationIdMismatch, match="do not recompute"):
        check_observation_ids([tampered], fixture_inputs.claims_by_id)


@requires_real_run
def test_every_real_observation_id_recomputes(real_inputs) -> None:
    assert len(real_inputs.observations) == 2707
    assert mismatched_observation_ids(
        real_inputs.observations, real_inputs.claims_by_id) == ()


# -- warnings --------------------------------------------------------------------------


def test_the_fixture_yields_nine_warned_observations(fixture_inputs, ontology) -> None:
    derived = derive_warnings(
        fixture_inputs.observations, fixture_inputs.evidence, ontology)
    assert set(derived) == FIXTURE_WARNED_CLAIMS
    assert warning_total(derived) == 9
    assert {code for codes in derived.values() for code in codes} == {
        "unpreferred_source_lane"}


def test_reconstruction_never_invents_a_population_role_or_confidence(
        fixture_inputs) -> None:
    """§5.4: both would be pydantic defaults the extractor never wrote.

    The populated case is built by copying a fixture row rather than selecting one: all 30
    fixture observations carry `population_definition_raw: null` *(measured 2026-08-02)*,
    while 89 of the real run's 2,707 do not. The real-run twin below covers the filed case.
    """
    without = fixture_inputs.observations[0]
    with_population = without.model_copy(
        update={"population_definition_raw": "homes sold in markets active all quarter"})

    rebuilt = reconstruct_observation(with_population, ())
    assert rebuilt.population is not None
    assert rebuilt.population.model_fields_set == {"definition_raw"}
    assert reconstruct_observation(without, ()).population is None
    assert "population" not in reconstruct_observation(without, ()).model_fields_set


@requires_real_run
def test_a_filed_population_carries_only_its_raw_definition(real_inputs) -> None:
    filed = [row for row in real_inputs.observations
             if row.population_definition_raw is not None]
    assert len(filed) == 89
    for row in filed:
        population = reconstruct_observation(row, ()).population
        assert population is not None
        assert population.model_fields_set == {"definition_raw"}
        assert population.definition_raw == row.population_definition_raw


def test_reconstruction_carries_the_evidence_row(fixture_inputs) -> None:
    observation = fixture_inputs.observations[0]
    evidence = fixture_inputs.evidence_by_claim_id[observation.claim_id]
    rebuilt = reconstruct_observation(observation, evidence)
    assert len(rebuilt.evidence) == 1
    assert rebuilt.evidence[0].passage_id == evidence[0].passage_id
    assert rebuilt.evidence[0].quoted_text == evidence[0].quoted_text
    # No char offsets survive the catalog writer (§2.3 trap 3); none are invented here.
    assert rebuilt.evidence[0].char_start is None


def test_reconcile_raises_on_a_wrong_manifest_count_and_says_why(
        fixture_inputs, ontology) -> None:
    derived = derive_warnings(
        fixture_inputs.observations, fixture_inputs.evidence, ontology)
    checks = tuple(
        check.model_copy(update={"warnings": 999}) if check.check == "ontology_validation"
        else check for check in fixture_inputs.manifest.verification)
    wrong = fixture_inputs.manifest.model_copy(update={"verification": checks})

    with pytest.raises(WarningCountMismatch) as caught:
        reconcile_warning_count(derived, wrong)
    message = str(caught.value)
    assert "9 warning(s)" in message and "999" in message
    assert "denominators differ" in message
    assert "2717" in message and "2707" in message


@requires_real_run
def test_the_real_run_yields_one_hundred_and_eighty_six_warnings_and_reconciles(
        real_inputs, ontology) -> None:
    derived = derive_warnings(real_inputs.observations, real_inputs.evidence, ontology)
    assert warning_total(derived) == 186
    assert len(derived) == 186  # one code each, so warned observations equal warnings
    assert {code for codes in derived.values() for code in codes} == {
        "unpreferred_source_lane"}
    assert reconcile_warning_count(derived, real_inputs.manifest) == 186
    assert real_inputs.manifest.ontology_validation_warnings == 186
