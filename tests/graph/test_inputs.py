"""The seven row shapes, the manifest, the corpus subset, and every join they rest on.

Everything here drives the readers through their real inputs. A test that asserted a field
list against a hand-typed dict would only prove the test and the model were written by the
same person; these parse the committed slice of `extract-v1-lexical-2422c4252c07` and, where
the run is present, the run itself.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from conftest import (
    FIXTURE_CATALOG,
    FIXTURE_RUN,
    REAL_RUN_AVAILABLE,
    REAL_RUN_REASON,
)
from graph.core.inputs import (
    CatalogRowConflict,
    CatalogSetMismatch,
    ClaimRow,
    DocumentRow,
    EventRow,
    EvidenceRow,
    ExtractionRunInputs,
    GraphInputError,
    IssueRow,
    MissingCatalogError,
    ObservationRow,
    Participant,
    PassageRow,
    RejectedClaimRow,
    RelationshipRow,
    RunManifest,
    VerificationCheck,
    check_cross_catalog_consistency,
    check_evidence_claim_ids,
    check_payload_ids,
    join_rejections_to_issues,
    load_run,
)

requires_real_run = pytest.mark.skipif(not REAL_RUN_AVAILABLE, reason=REAL_RUN_REASON)

CATALOG_MODELS = {
    "claims": ClaimRow,
    "observations": ObservationRow,
    "events": EventRow,
    "relationships": RelationshipRow,
    "evidence": EvidenceRow,
    "issues": IssueRow,
    "rejected_claims": RejectedClaimRow,
}

#: Row counts of the committed fixture, from its README's provenance table.
FIXTURE_COUNTS = {"claims": 39, "observations": 30, "events": 5, "relationships": 4,
                  "evidence": 39, "issues": 45, "rejected_claims": 9}


def raw_rows(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (FIXTURE_RUN / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line]


# -- the seven shapes ------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CATALOG_MODELS))
def test_every_catalog_row_parses_from_the_fixture(name: str) -> None:
    rows = raw_rows(name)
    parsed = [CATALOG_MODELS[name](**row) for row in rows]
    assert len(parsed) == FIXTURE_COUNTS[name]
    # Round-tripping proves the model carries every field, not merely that it accepted one.
    assert parsed[0].model_dump().keys() == rows[0].keys()


def test_participants_carry_exactly_three_keys() -> None:
    event = EventRow(**raw_rows("events")[0])
    assert set(Participant.model_fields) == {"role", "entity_id", "entity_type"}
    assert all(isinstance(p, Participant) for p in event.participants)


@pytest.mark.parametrize("name", sorted(CATALOG_MODELS))
def test_an_unknown_field_fails(name: str) -> None:
    row = {**raw_rows(name)[0], "surprise_column": 1}
    with pytest.raises(ValidationError, match="surprise_column"):
        CATALOG_MODELS[name](**row)


@pytest.mark.parametrize("name,required", [
    ("claims", "claim_id"), ("observations", "observation_id"), ("events", "event_id"),
    ("relationships", "relationship_instance_id"), ("evidence", "claim_id"),
    ("issues", "issue_id"), ("rejected_claims", "rejection_id"),
])
def test_a_missing_required_field_fails(name: str, required: str) -> None:
    row = {key: value for key, value in raw_rows(name)[0].items() if key != required}
    with pytest.raises(ValidationError, match=required):
        CATALOG_MODELS[name](**row)


#: Every field the seven catalog readers, the manifest readers and the corpus subset declare
#: was **optional with a default** until 2026-08-03, and every one of them is present on every
#: row of both the run and the fixture *(verified: one distinct key tuple per catalog file)*.
#: A default could therefore only ever fire on a row a writer stopped emitting — silently.
NO_DEFAULTS_MODELS = (
    ClaimRow, ObservationRow, EventRow, RelationshipRow, EvidenceRow, IssueRow,
    RejectedClaimRow, Participant, PassageRow, DocumentRow, RunManifest,
    VerificationCheck,
)


@pytest.mark.parametrize("model", NO_DEFAULTS_MODELS, ids=lambda m: m.__name__)
def test_no_reader_field_has_a_default(model) -> None:
    """§2.2: "Nothing substitutes a default for a missing input", made executable.

    A nullable field is spelled `X | None` and stays **required**, so `null` must actually be
    written for the reader to accept the row. That distinction is the whole point: "the writer
    filed no currency" and "the writer no longer emits a currency column" are different
    events, and only one of them is data.
    """
    defaulted = sorted(name for name, field in model.model_fields.items()
                       if not field.is_required())
    assert defaulted == [], f"{model.__name__} still defaults {defaulted}"


@pytest.mark.parametrize("name,dropped", [
    ("events", "participants"),
    ("events", "occurred_on"),
    ("events", "review_flag"),
    ("observations", "ambiguity_codes"),
    ("observations", "currency"),
    ("evidence", "quoted_text"),
    ("evidence", "block_ids"),
    ("issues", "concept_ids"),
    ("rejected_claims", "raw_finding"),
    ("relationships", "valid_from"),
    ("claims", "confidence"),
])
def test_a_field_the_writer_stopped_emitting_now_fails(name: str, dropped: str) -> None:
    """The regression this closes: `participants` silently defaulting to `()`.

    An events writer that stopped emitting the column would have produced a graph with six
    `:Event` nodes, zero participants, ten missing `PARTICIPATES_IN` edges and no message.
    """
    row = {key: value for key, value in raw_rows(name)[0].items() if key != dropped}
    assert dropped in raw_rows(name)[0], f"{dropped} is not a {name} column"
    with pytest.raises(ValidationError, match=dropped):
        CATALOG_MODELS[name](**row)


def test_a_nullable_field_still_accepts_an_explicit_null() -> None:
    """Required is not the same as non-null. `null` is a filed answer and stays legal."""
    row = {**raw_rows("events")[0], "occurred_on": None, "review_flag": None,
           "announced_on": None}
    event = EventRow(**row)
    assert event.occurred_on is None and event.review_flag is None


def test_rows_are_frozen() -> None:
    claim = ClaimRow(**raw_rows("claims")[0])
    with pytest.raises(ValidationError):
        claim.passage_id = "something else"


def test_an_empty_passage_id_reads_but_is_not_a_key() -> None:
    """§2.3 trap 2: `""` is legal in the catalog. The reader must not crash on it."""
    row = {**raw_rows("claims")[0], "passage_id": "", "document_id": "",
           "document_type": ""}
    claim = ClaimRow(**row)
    assert claim.passage_id == ""

    from graph.core.keys import NodeKeyError, passage_node_key

    with pytest.raises(NodeKeyError):
        passage_node_key(claim.passage_id)


def test_no_reader_declares_a_population_role_or_confidence() -> None:
    """§5.4: neither field exists in any catalog, so no reader may offer one."""
    forbidden = {"population_role", "population_confidence"}
    for model in (*CATALOG_MODELS.values(), PassageRow, DocumentRow, RunManifest):
        assert not forbidden & set(model.model_fields), model.__name__


# -- the manifest ----------------------------------------------------------------------


def test_manifest_parses_strictly_and_exposes_the_verification_aggregate() -> None:
    raw = json.loads((FIXTURE_RUN / "manifest.json").read_text(encoding="utf-8"))
    manifest = RunManifest(**raw)
    assert manifest.run_id == "extract-v1-lexical-2422c4252c07"
    assert manifest.ontology_id == "real_estate_marketplace_v1"
    assert manifest.counts["observations"] == 2707
    assert manifest.ontology_validation_warnings == 186
    assert manifest.check("ontology_validation").passed is True

    with pytest.raises(GraphInputError, match="no verification check"):
        manifest.check("a_check_that_does_not_exist")

    with pytest.raises(ValidationError, match="finished_at"):
        RunManifest(**{**raw, "finished_at": "2026-08-02T17:00:00+00:00"})


# -- the corpus subset -----------------------------------------------------------------


def test_corpus_readers_take_a_declared_subset_and_ignore_the_rest() -> None:
    raw_passage = json.loads(
        (FIXTURE_CATALOG / "passages.jsonl").read_text(encoding="utf-8").splitlines()[0])
    raw_document = json.loads(
        (FIXTURE_CATALOG / "documents.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(raw_passage) == 31 and len(raw_document) == 35

    passage = PassageRow(**raw_passage)
    document = DocumentRow(**raw_document)
    assert set(PassageRow.model_fields) == {
        "passage_id", "document_id", "passage_kind", "text", "section_id", "heading_path",
        "table_id", "char_count", "source_url"}
    assert set(DocumentRow.model_fields) == {
        "document_id", "form", "filing_date", "report_date", "accession", "source_url",
        "document_type", "title", "company_name", "cik10"}
    assert passage.passage_id == raw_passage["passage_id"]
    assert document.form == raw_document["form"]


def test_a_corpus_row_missing_a_needed_field_still_fails() -> None:
    """Ignoring unknown keys is not the same as accepting unknown data."""
    raw = json.loads(
        (FIXTURE_CATALOG / "passages.jsonl").read_text(encoding="utf-8").splitlines()[0])
    with pytest.raises(ValidationError, match="text"):
        PassageRow(**{key: value for key, value in raw.items() if key != "text"})


# -- loading and the joins -------------------------------------------------------------


def test_loading_the_fixture_reads_every_catalog_and_the_corpus(fixture_inputs) -> None:
    for name, expected in FIXTURE_COUNTS.items():
        assert len(getattr(fixture_inputs, name)) == expected, name
    assert len(fixture_inputs.passages) == 6
    assert len(fixture_inputs.documents) == 6
    assert fixture_inputs.claims_by_id["claim:event:a5823d3b7c19"].claim_kind == "event"
    assert len(fixture_inputs.evidence_by_claim_id) == 39


def test_a_missing_catalog_names_the_file(tmp_path) -> None:
    with pytest.raises(MissingCatalogError, match="manifest.json"):
        load_run(tmp_path)


def test_cross_catalog_agreement_holds_on_the_fixture(fixture_inputs) -> None:
    check_cross_catalog_consistency(
        claims=fixture_inputs.claims, observations=fixture_inputs.observations,
        events=fixture_inputs.events, relationships=fixture_inputs.relationships,
        evidence=fixture_inputs.evidence)


def test_a_disagreeing_pair_fails_and_names_the_id_and_field(fixture_inputs) -> None:
    observation = fixture_inputs.observations[0]
    claim = fixture_inputs.claims_by_id[observation.claim_id]
    tampered = claim.model_copy(update={"document_type": "not_what_the_payload_says"})

    with pytest.raises(CatalogRowConflict) as caught:
        check_cross_catalog_consistency(
            claims=[tampered], observations=[observation], events=(), relationships=(),
            evidence=[row for row in fixture_inputs.evidence
                      if row.claim_id == claim.claim_id])
    assert caught.value.identifier == observation.claim_id
    assert caught.value.field == "document_type"
    assert observation.claim_id in str(caught.value)
    assert "document_type" in str(caught.value)


def test_an_event_that_contradicts_its_claim_is_refused(fixture_inputs) -> None:
    """The 10-row gap between `claims.jsonl` and `observations.jsonl` is checked too."""
    event = fixture_inputs.events[0]
    tampered = event.model_copy(update={"lane": "tables"})
    with pytest.raises(CatalogRowConflict) as caught:
        check_cross_catalog_consistency(
            claims=fixture_inputs.claims, observations=(), events=[tampered],
            relationships=(), evidence=())
    assert caught.value.identifier == event.claim_id
    assert caught.value.field == "lane"
    assert "events.jsonl" in str(caught.value)


def test_payload_id_set_equality_is_enforced(fixture_inputs) -> None:
    with pytest.raises(CatalogSetMismatch, match="payload"):
        check_payload_ids(
            fixture_inputs.claims, fixture_inputs.observations[:-1],
            fixture_inputs.events, fixture_inputs.relationships)


def test_evidence_claim_id_set_equality_is_enforced(fixture_inputs) -> None:
    with pytest.raises(CatalogSetMismatch, match="evidence"):
        check_evidence_claim_ids(fixture_inputs.claims, fixture_inputs.evidence[:-1])


def test_an_observation_citing_an_unknown_claim_is_refused(fixture_inputs) -> None:
    orphan = fixture_inputs.observations[0].model_copy(
        update={"claim_id": "claim:metric-observation:000000000000"})
    with pytest.raises(CatalogSetMismatch, match="absent from claims.jsonl"):
        check_cross_catalog_consistency(
            claims=fixture_inputs.claims, observations=[orphan], events=(),
            relationships=(), evidence=fixture_inputs.evidence)


def test_the_rejection_issue_join_is_nine_of_nine_on_the_fixture(fixture_inputs) -> None:
    join = fixture_inputs.rejection_issue_join
    assert (join.mirrored, join.total, len(join.unmirrored)) == (9, 9, 0)
    for rejection, issue in join.pairs:
        assert rejection.rejection_id.split(":")[1] == issue.issue_id.split(":")[1]
        assert rejection.code == issue.code and rejection.passage_id == issue.passage_id
        assert issue.rejected_claim is True


def test_an_assemble_origin_rejection_has_no_mirror_and_does_not_crash(
        fixture_inputs) -> None:
    """`jsonl_catalog.py:98-121` writes no issue row for an `assemble` refusal."""
    orphan = fixture_inputs.rejected_claims[0].model_copy(update={
        "rejection_id": "rej:ffffffffffff", "refused_by": "assemble",
        "lane": "assemble", "raw_finding": None})
    join = join_rejections_to_issues(
        fixture_inputs.issues, [*fixture_inputs.rejected_claims, orphan])
    assert (join.mirrored, join.total) == (9, 10)
    assert join.unmirrored == (orphan,)


def test_a_malformed_id_is_refused_by_the_join(fixture_inputs) -> None:
    broken = fixture_inputs.rejected_claims[0].model_copy(
        update={"rejection_id": "09c456d460d1"})
    with pytest.raises(GraphInputError, match="rej:<hex>"):
        join_rejections_to_issues(fixture_inputs.issues, [broken])


def test_hand_built_inputs_need_no_disk(fixture_inputs) -> None:
    """`from_rows` is the path a projection test takes with no run directory at all."""
    inputs = ExtractionRunInputs.from_rows(manifest=fixture_inputs.manifest)
    assert inputs.claims == () and inputs.rejection_issue_join.total == 0


# -- the real run ----------------------------------------------------------------------


@requires_real_run
def test_the_real_run_parses_with_the_counts_its_manifest_records(real_inputs) -> None:
    counts = real_inputs.manifest.counts
    assert len(real_inputs.claims) == counts["claims"] == 2717
    assert len(real_inputs.observations) == counts["observations"] == 2707
    assert len(real_inputs.events) == counts["events"] == 6
    assert len(real_inputs.relationships) == counts["relationships"] == 4
    assert len(real_inputs.evidence) == counts["evidence_references"] == 2717
    assert len(real_inputs.issues) == counts["issues"] == 17127
    assert len(real_inputs.rejected_claims) == counts["rejected_claims"] == 46
    assert len(real_inputs.passages) == 12442 and len(real_inputs.documents) == 294


@requires_real_run
def test_the_rejection_issue_join_is_forty_six_of_forty_six_on_the_real_run(
        real_inputs) -> None:
    join = real_inputs.rejection_issue_join
    assert (join.mirrored, join.total, len(join.unmirrored)) == (46, 46, 0)
    assert {rejection.refused_by for rejection, _ in join.pairs} == {"lane"}
