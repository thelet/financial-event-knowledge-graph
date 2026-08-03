"""`evidence.jsonl` has one row shape per kind, and the graph reads it that way (F0 Part B).

The reader half of the contract. What it holds:

* the ten-key passage row is unchanged, so the committed real slice parses exactly as before;
* each non-passage kind gets its own reader, so a fabricated `passage_id` on an XBRL or
  calculated row is a **parse** failure — there is no check to forget;
* an undeclared kind stops the load rather than being read as a passage row;
* `cited_passage_ids` no longer refuses a claim whose evidence names no passage, and still
  refuses one whose evidence *does* and whose own `passage_id` is empty.

The rows come from `tests/fixtures/evidence_contract/`, which is synthetic and says so. The
real slice under `tests/fixtures/graph/` stays untouched — F0 adds no evidence to any run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import FIXTURE_RUN
from extraction.stages.catalog.jsonl_catalog import _EVIDENCE_ROW_FIELDS
from graph.core.citations import (
    EmptyPassageCitationError,
    cited_passage_ids,
    cites_a_passage,
)
from graph.core.derivation import _evidence_reference
from graph.core.inputs import (
    EVIDENCE_ROW_MODELS,
    CalculatedEvidenceRow,
    ClaimRow,
    EvidenceRow,
    ExtractionRunInputs,
    MarketDataEvidenceRow,
    ObservationRow,
    RunManifest,
    UnknownEvidenceKindError,
    XbrlEvidenceRow,
    cited_passage_of,
    parse_evidence_row,
)

CONTRACT = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_contract"


def contract_rows() -> list[dict]:
    return [json.loads(line) for line
            in (CONTRACT / "valid_rows.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def invalid_rows() -> list[dict]:
    return json.loads((CONTRACT / "invalid_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> RunManifest:
    return RunManifest(
        **json.loads((FIXTURE_RUN / "manifest.json").read_text(encoding="utf-8")))


# -- the reader dispatches on the declared kind ---------------------------------------------


def test_every_kind_the_writer_can_write_has_a_reader():
    """The two halves of the row contract, held to each other.

    `jsonl_catalog._EVIDENCE_ROW_FIELDS` is the writer's declaration and
    `EVIDENCE_ROW_MODELS` is the reader's. §0b keeps them separate on purpose — the graph
    declares the shape the catalog *emits*, not the model behind it — so the guarantee has to
    be a test rather than a shared import.
    """
    assert set(_EVIDENCE_ROW_FIELDS) == set(EVIDENCE_ROW_MODELS)


def test_each_reader_declares_exactly_the_keys_the_writer_writes():
    for kind, fields in _EVIDENCE_ROW_FIELDS.items():
        declared = set(EVIDENCE_ROW_MODELS[kind].model_fields)
        expected = set(fields) | {"claim_id", "claim_kind", "evidence_index", "evidence_kind"}
        assert declared == expected, kind


@pytest.mark.parametrize("row", contract_rows(), ids=lambda row: row["evidence_kind"])
def test_a_row_of_every_kind_parses_into_the_reader_its_kind_selects(row):
    parsed = parse_evidence_row(row)
    assert isinstance(parsed, EVIDENCE_ROW_MODELS[row["evidence_kind"]])
    assert parsed.claim_id == row["claim_id"]


@pytest.mark.parametrize("row", contract_rows(), ids=lambda row: row["evidence_kind"])
def test_every_row_becomes_an_evidence_reference_of_its_own_kind(row):
    """`derive_warnings` reconstructs references from rows; it must not break on a new kind."""
    reference = _evidence_reference(parse_evidence_row(row))
    assert str(reference.evidence_kind) == row["evidence_kind"]


def test_the_two_passage_kinds_share_one_reader():
    assert EVIDENCE_ROW_MODELS["normalized_passage"] is EvidenceRow
    assert EVIDENCE_ROW_MODELS["normalized_table"] is EvidenceRow


def test_the_committed_real_slice_still_parses_as_the_passage_row_it_always_was():
    """39 rows of a real run. F0 Part B changed the evidence *catalog* and not this shape."""
    rows = [json.loads(line) for line
            in (FIXTURE_RUN / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    parsed = [parse_evidence_row(row) for row in rows]
    assert parsed and all(isinstance(row, EvidenceRow) for row in parsed)


# -- what the reader refuses -----------------------------------------------------------------


@pytest.mark.parametrize("case", invalid_rows(), ids=lambda case: case["case"])
def test_an_invalid_row_is_refused_at_parse_time(case):
    if case["expects"] == "unknown_kind":
        with pytest.raises(UnknownEvidenceKindError, match=case["row"]["evidence_kind"]):
            parse_evidence_row(case["row"])
        return
    with pytest.raises(ValidationError) as raised:
        parse_evidence_row(case["row"])
    assert case["field"] in str(raised.value), case["why"]


def test_a_calculated_row_cannot_be_given_a_passage_id_at_all():
    """Stated once more directly, because it is the rule the contract exists for."""
    with pytest.raises(ValidationError, match="passage_id"):
        CalculatedEvidenceRow(
            claim_id="claim:x", claim_kind="metric_observation", evidence_index=0,
            evidence_kind="calculated", input_observation_ids=("obs:a",),
            calculation_expression="a - b", calculation_version="v1",
            passage_id="norm:x#p1")


def test_cited_passage_of_answers_none_for_every_non_passage_kind():
    for row in contract_rows():
        parsed = parse_evidence_row(row)
        if row["evidence_kind"] in ("normalized_passage", "normalized_table"):
            assert cited_passage_of(parsed) == row["passage_id"]
        else:
            assert cited_passage_of(parsed) is None


# -- citations ------------------------------------------------------------------------------


def make_claim(claim_id: str, passage_id: str) -> ClaimRow:
    return ClaimRow(
        claim_id=claim_id, claim_kind="metric_observation", payload_id="obs:x",
        lane="tables", passage_id=passage_id,
        document_id=passage_id.split("#")[0] if passage_id else "",
        document_type="", assertion_type="reported", confidence=None, extractor_metadata={})


def make_observation(claim: ClaimRow) -> ObservationRow:
    """The payload row `check_payload_ids` requires every claim to have.

    Its provenance columns are copied from the claim, because `check_claims_agree_with_
    observations` asserts they are byte-identical — including the empty `passage_id` a claim
    evidenced by a non-passage kind carries.
    """
    return ObservationRow(
        observation_id=claim.payload_id, claim_id=claim.claim_id, metric_id="homes_sold",
        subject_entity_id="opendoor", subject_type="company", value=1.0, unit="homes",
        currency=None, period_start=None, period_end=None, instant_date="2021-03-31",
        population_definition_raw=None, source_lane="normalized_table", lane=claim.lane,
        assertion_type=claim.assertion_type, confidence=claim.confidence,
        passage_id=claim.passage_id, document_id=claim.document_id,
        document_type=claim.document_type, ambiguity_codes=(), scale=None,
        scale_location=None, row_label=None, column_label=None)


def inputs_with(manifest, claim: ClaimRow, evidence_row: dict) -> ExtractionRunInputs:
    """One claim, its observation and one evidence row, through the real join checks."""
    return ExtractionRunInputs.from_rows(
        manifest=manifest, claims=[claim], observations=[make_observation(claim)],
        evidence=[parse_evidence_row({**evidence_row, "claim_id": claim.claim_id})])


def test_a_claim_evidenced_only_by_a_market_row_cites_no_passage_and_is_not_refused(manifest):
    """The blocker F0 Part B exists to remove.

    `claims.jsonl` writes `passage_id: ""` for such a claim, and `required_passage_id` used to
    refuse it — which made an XBRL or market-data fact unprojectable however well formed it
    was.
    """
    market = next(r for r in contract_rows() if r["evidence_kind"] == "market_data")
    inputs = inputs_with(manifest, make_claim("claim:market", ""), market)
    assert not cites_a_passage(inputs, inputs.claims[0])
    assert cited_passage_ids(inputs) == ()


def test_a_claim_whose_evidence_does_name_a_passage_still_may_not_have_an_empty_one(manifest):
    """The guarantee §10 criterion 4 depends on, unweakened.

    This is the case the refusal was written for: the evidence locates a passage and the claim
    row does not, so the two disagree and the fact is not projectable. Told apart from the case
    above by reading the evidence, never by trusting the empty string.
    """
    passage = next(r for r in contract_rows() if r["evidence_kind"] == "normalized_passage")
    inputs = inputs_with(manifest, make_claim("claim:hole", ""), passage)
    assert cites_a_passage(inputs, inputs.claims[0])
    with pytest.raises(EmptyPassageCitationError, match="claim:hole"):
        cited_passage_ids(inputs)


def test_a_passage_backed_claim_is_still_collected(manifest):
    passage = next(r for r in contract_rows() if r["evidence_kind"] == "normalized_passage")
    inputs = inputs_with(
        manifest, make_claim("claim:ok", passage["passage_id"]), passage)
    assert cited_passage_ids(inputs) == (passage["passage_id"],)


def test_the_real_slice_cites_the_same_passages_it_always_did(fixture_inputs):
    """A count, so the widened union cannot quietly drop or add a citation.

    Every row of the committed slice is passage evidence, so the answer must be exactly what it
    was before the contract was discriminated.
    """
    cited = cited_passage_ids(fixture_inputs)
    assert len(cited) == len(set(cited))
    assert all(isinstance(row, EvidenceRow) for row in fixture_inputs.evidence)
    assert cited == tuple(sorted(
        {claim.passage_id for claim in fixture_inputs.claims}
        | {issue.passage_id for issue in fixture_inputs.issues}
        | {row.passage_id for row in fixture_inputs.rejected_claims}
        | {row.passage_id for row in fixture_inputs.evidence if row.passage_id is not None}))


# -- typing ---------------------------------------------------------------------------------


def test_the_non_passage_readers_are_not_evidence_rows():
    """`isinstance(row, EvidenceRow)` is the passage test; a shared base would break it."""
    xbrl = next(r for r in contract_rows() if r["evidence_kind"] == "xbrl_fact")
    parsed = parse_evidence_row(xbrl)
    assert isinstance(parsed, XbrlEvidenceRow)
    assert not isinstance(parsed, EvidenceRow)
    assert not isinstance(parsed, MarketDataEvidenceRow)
