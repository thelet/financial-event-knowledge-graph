"""The discriminated evidence contract (F0 Part B).

Three things are proved here and nothing else is:

1. **Every declared kind validates**, from a committed fixture row, with no finding.
2. **Every way of breaking the contract is refused by name** — a missing required field, a
   field belonging to another kind, and above all a filed-passage anchor on a source that is
   not a filed passage.
3. **Nothing about passage evidence changed.** The real run's references produce exactly the
   findings they produced before, which is none.

The fixture rows are synthetic and say so (`tests/fixtures/evidence_contract/README.md`): no
lane emits an XBRL fact, a market row or a calculated value yet, and F0 ingests nothing. A
contract with no instances is a contract nothing holds, so the instances are written by hand
and the pipeline is driven over them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extraction.core.validation import (
    EVIDENCE_FIELD_MISSING,
    EVIDENCE_KIND_MIXED,
    EVIDENCE_KIND_UNKNOWN,
    EVIDENCE_MISSING,
    EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND,
    EVIDENCE_UNRESOLVED,
    _EvidenceContract,
    validate_evidence_resolves,
)
from extraction.stages.catalog.jsonl_catalog import (
    _EVIDENCE_ROW_COMMON,
    _EVIDENCE_ROW_FIELDS,
    UnknownEvidenceKindError,
    _evidence_rows,
)
from ontology import load_ontology
from ontology.core.models import EvidenceReference, MetricObservation, OntologyClaim
from ontology.core.values import EvidenceKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_contract"
REAL_RUN = (Path(__file__).resolve().parents[2] / "data" / "extraction_runs"
            / "extract-v1-lexical-2422c4252c07")

#: The passage the fixture's two passage rows name. Present so those rows resolve; every other
#: fixture kind must validate against a corpus that holds nothing at all.
FIXTURE_PASSAGES = {
    "norm:0001801169:0001801169-21-000021:open-20210331.htm#p1": "we operated in 27 markets",
    "norm:0001801169:0001801169-21-000021:open-20210331.htm#p2": "Markets | 27",
}


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


class InMemoryPassages:
    def __init__(self, rows: dict[str, str]):
        self._rows = rows

    def text_of(self, passage_id): return self._rows.get(passage_id)
    def exists(self, passage_id): return passage_id in self._rows
    def document_of(self, passage_id):
        return passage_id.split("#")[0] if passage_id in self._rows else None


#: The row keys that belong to the *claim*, not to the reference. `evidence_kind` is common to
#: every row and is also a field of the reference, so it is not in this set.
_CLAIM_KEYS = frozenset({"claim_id", "claim_kind", "evidence_index"})


def reference_of(row: dict) -> EvidenceReference:
    """A catalog row back as the reference it was written from."""
    return EvidenceReference(
        **{key: value for key, value in row.items() if key not in _CLAIM_KEYS})


def claim_with(*references: EvidenceReference, claim_id: str = "claim:test") -> OntologyClaim:
    return OntologyClaim(
        claim_id=claim_id, claim_kind="metric_observation", evidence=references)


def valid_rows() -> list[dict]:
    return [json.loads(line)
            for line in (FIXTURES / "valid_rows.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_cases(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


# -- the contract is read from the vocabulary, not written here ----------------------------


def test_the_contract_is_derived_entirely_from_the_ontology(ontology):
    """No field list in Python. Change `claims.yaml` and the checker changes with it."""
    contract = _EvidenceContract.of(ontology)
    assert contract.required["xbrl_fact"] == {"xbrl_concept", "accession", "source_url"}
    assert contract.required["calculated"] == {
        "input_observation_ids", "calculation_expression", "calculation_version"}
    assert contract.permitted["calculated"] == contract.required["calculated"]


def test_the_passage_kinds_are_the_kinds_that_require_a_passage(ontology):
    contract = _EvidenceContract.of(ontology)
    assert contract.passage_kinds == {"normalized_passage", "normalized_table"}


def test_the_filed_passage_fields_are_the_ones_no_other_kind_declares(ontology):
    """`document_id`, `quoted_text` and `source_url` are *not* in it, deliberately.

    An XBRL fact names a document, quotes text and carries a URL — treating those as
    filed-passage anchors would refuse honest evidence and teach a reader that the rule is
    about field names rather than about sources.
    """
    contract = _EvidenceContract.of(ontology)
    assert contract.filed_passage_fields == {
        "passage_id", "table_id", "block_ids", "char_start", "char_end"}


def test_every_evidence_kind_the_enum_declares_is_declared_by_the_ontology(ontology):
    """An enum member the vocabulary has never heard of has no contract and no row shape."""
    contract = _EvidenceContract.of(ontology)
    assert {str(kind) for kind in EvidenceKind} == set(contract.permitted)


def test_every_evidence_kind_has_a_catalog_row_shape():
    assert {str(kind) for kind in EvidenceKind} == set(_EVIDENCE_ROW_FIELDS)


def test_the_common_row_keys_are_the_claim_keys_plus_the_discriminator():
    assert set(_EVIDENCE_ROW_COMMON) == _CLAIM_KEYS | {"evidence_kind"}


def test_a_kind_the_ontology_does_not_declare_is_refused_rather_than_waved_through():
    """Reachable only with a trimmed vocabulary — which is the point: an unknown kind is
    refused *because* nothing can check it, not because the enum happens to be closed."""

    class OneKindOnly:
        class registry:
            @staticmethod
            def by_category(category):
                assert category == "evidence_type"
                return [definition for definition in
                        load_ontology().registry.by_category("evidence_type")
                        if str(definition.evidence_kind) == "normalized_passage"]

    claim = claim_with(EvidenceReference(
        evidence_kind="xbrl_fact", accession="a", xbrl_concept="c", source_url="u"))
    findings = validate_evidence_resolves([claim], InMemoryPassages({}), ontology=OneKindOnly())
    assert codes(findings) == {EVIDENCE_KIND_UNKNOWN}


# -- every declared kind validates ---------------------------------------------------------


@pytest.mark.parametrize("row", valid_rows(), ids=lambda row: row["evidence_kind"])
def test_a_valid_row_of_every_kind_validates_with_no_finding(row, ontology):
    claim = claim_with(reference_of(row), claim_id=row["claim_id"])
    findings = validate_evidence_resolves(
        [claim], InMemoryPassages(FIXTURE_PASSAGES), ontology=ontology)
    assert findings == [], [f"{f.code}: {f.detail}" for f in findings]


def test_the_fixture_covers_every_declared_kind():
    assert {row["evidence_kind"] for row in valid_rows()} == set(_EVIDENCE_ROW_FIELDS)


@pytest.mark.parametrize("row", valid_rows(), ids=lambda row: row["evidence_kind"])
def test_a_valid_row_is_exactly_what_the_writer_writes_for_its_reference(row):
    """Round trip: row → reference → row. The fixture is not allowed to drift from the writer."""
    reference = reference_of(row)
    claim = claim_with(reference, claim_id=row["claim_id"])
    written = _evidence_rows(claim)
    assert written == [row]


# -- every way of breaking it is refused by name --------------------------------------------


@pytest.mark.parametrize("case", load_cases("invalid_references.json"),
                         ids=lambda case: case["case"])
def test_an_invalid_reference_is_refused_with_the_codes_it_earns(case, ontology):
    claim = claim_with(EvidenceReference(**case["reference"]))
    findings = validate_evidence_resolves(
        [claim], InMemoryPassages(FIXTURE_PASSAGES), ontology=ontology)
    assert codes(findings) == set(case["expected_codes"]), (
        case["why"], [f"{f.code}: {f.detail}" for f in findings])


def test_a_claim_with_no_evidence_at_all_still_fails(ontology):
    """Unchanged from before the contract existed, including its exemption."""
    reported = OntologyClaim(claim_id="claim:none", claim_kind="metric_observation")
    findings = validate_evidence_resolves(
        [reported], InMemoryPassages({}), ontology=ontology)
    assert codes(findings) == {EVIDENCE_MISSING}


def test_a_calculated_observation_with_no_evidence_is_still_exempt(ontology):
    """The one exemption `validate_evidence_resolves` has always made, kept."""
    observation = MetricObservation(
        observation_id="obs:x", metric_id="homes_sold", subject_entity_id="opendoor",
        value=1, unit="homes", source_lane="calculated", assertion_type="calculated")
    claim = OntologyClaim(claim_id="claim:calc", claim_kind="metric_observation",
                          metric_observation=observation, assertion_type="calculated")
    assert validate_evidence_resolves([claim], InMemoryPassages({}), ontology=ontology) == []


def test_the_message_names_the_field_and_the_kind(ontology):
    """A refusal a reader cannot act on is a refusal that gets suppressed."""
    claim = claim_with(EvidenceReference(evidence_kind="xbrl_fact", accession="a"))
    detail = validate_evidence_resolves(
        [claim], InMemoryPassages({}), ontology=ontology)[0].detail
    assert "xbrl_fact" in detail and "source_url" in detail and "xbrl_concept" in detail


# -- passage evidence is untouched -----------------------------------------------------------


def test_a_passage_reference_still_has_to_resolve(ontology):
    claim = claim_with(EvidenceReference(
        evidence_kind="normalized_passage",
        passage_id="norm:x:y:z.htm#p1", document_id="norm:x:y:z.htm"))
    assert codes(validate_evidence_resolves(
        [claim], InMemoryPassages({}), ontology=ontology)) == {EVIDENCE_UNRESOLVED}


def test_a_non_passage_kind_is_not_resolved_against_the_passage_catalog(ontology):
    """It has no passage to resolve, and an empty corpus must not make it fail."""
    row = next(r for r in valid_rows() if r["evidence_kind"] == "market_data")
    claim = claim_with(reference_of(row))
    assert validate_evidence_resolves([claim], InMemoryPassages({}), ontology=ontology) == []


@pytest.mark.skipif(not REAL_RUN.is_dir(),
                    reason="data/extraction_runs is gitignored; the run is not on this machine")
def test_every_reference_in_the_real_run_still_validates(ontology, repo_config):
    """The regression that matters: the corpus, through the new contract, unchanged.

    Measured 2026-08-03 on `extract-v1-lexical-2422c4252c07` as Part A had left it —
    2,714 references, 2,690 `normalized_table` and 24 `normalized_passage`, **0 findings**.
    Asserted as "no finding of any kind" rather than against a pinned count, because the
    count is Part A's to move and the claim here is that widening the contract moved nothing.
    """
    from extraction.context import build_extraction_context
    from extraction.pipeline import load_lane_outputs
    from extraction.stages.catalog.jsonl_catalog import build as build_catalogs

    context = build_extraction_context(None)
    catalogs = build_catalogs(
        load_lane_outputs(REAL_RUN), ontology=ontology, corpus=context.corpus)
    references = [r for claim in catalogs.claims for r in claim.payload_evidence]
    assert len(references) > 2_000, "the run should carry thousands of references"
    assert {str(r.evidence_kind) for r in references} <= {
        "normalized_passage", "normalized_table"}

    findings = validate_evidence_resolves(
        catalogs.claims, context.corpus, ontology=ontology)
    assert findings == [], [f"{f.code}: {f.detail}" for f in findings[:5]]


def test_an_unknown_kind_stops_the_writer_rather_than_being_written_as_a_passage():
    """`_evidence_rows` refuses what it cannot shape. The alternative is a passage-shaped row
    for something that is not a passage, which every reader downstream would believe."""

    class Fake:
        claim_id = "claim:x"
        claim_kind = "metric_observation"
        evidence_kind = "transcript_utterance"

        @property
        def payload_evidence(self):
            return (self,)

    with pytest.raises(UnknownEvidenceKindError, match="transcript_utterance"):
        _evidence_rows(Fake())


# -- the two halves of the field contract stay in step ---------------------------------------


def test_every_written_field_is_permitted_by_the_vocabulary(ontology):
    """The writer may narrow what a kind carries; it may never widen it.

    A column the vocabulary does not permit would be written, read and projected without any
    check ever looking at it — the failure this contract exists to remove, arriving through
    the back door.
    """
    contract = _EvidenceContract.of(ontology)
    for kind, fields in _EVIDENCE_ROW_FIELDS.items():
        assert set(fields) <= contract.permitted[kind], kind


def test_every_required_field_survives_into_the_catalog(ontology):
    """A required field the writer dropped would make every rebuilt row invalid."""
    contract = _EvidenceContract.of(ontology)
    for kind, fields in _EVIDENCE_ROW_FIELDS.items():
        assert contract.required[kind] <= set(fields), kind
