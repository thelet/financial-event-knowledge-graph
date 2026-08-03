"""The node projection, tested against the plan's declared counts and its ten hard rules.

Every number asserted here was measured at G0 against `extract-v1-lexical-7f72d6172630` and is
recorded in V1_GRAPH_PROTOTYPE §3.1 / STAGE13_GRAPH_INPUT_HANDOFF §1. The fixture tests run
from a clean checkout; the ones that assert the run's own totals skip when `data/` — which is
gitignored — is absent.

The rules under test are the ones a plausible-looking projection gets wrong: suppressing a fact
because an issue names its passage, merging every unnamed subsidiary in the corpus into one
node, filling an absent `occurred_on` from `announced_on`, parsing `"$525 million"`, or
inventing a `population_role` the extractor never wrote.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from conftest import REAL_RUN_AVAILABLE, REAL_RUN_REASON
from graph.core.derivation import ObservationIdMismatch, WarningCountMismatch, period_key
from graph.core.inputs import ExtractionRunInputs
from graph.core.keys import PLACEHOLDER_MARKER, UNRESOLVED_SEPARATOR
from graph.core.models import GRAPH_PROJECTION_VERSION, BASE_LABELS, GraphNode
from graph.stages.projection.nodes import (
    EVENT_PROPERTY_PREFIX,
    NOT_ATTEMPTED_CODE,
    PROVENANCE_KEYS,
    MalformedEventPropertyError,
    NodeProjectionError,
    ProvenanceCollisionError,
    UndeclaredEntityTypeError,
    build_nodes,
)

requires_real_run = pytest.mark.skipif(not REAL_RUN_AVAILABLE, reason=REAL_RUN_REASON)

CONFLICT_GUARD_PASSAGE = (
    "norm:0001801169:0001801169-23-000137:q32023formxex991earningsre.htm#p29")

#: The four `AMBIGUOUS_COLUMN_ALIGNMENT` identities the extractor refused (§2.4, fixture
#: README). `(metric_id, subject, period_key, source_lane)` on the passage above.
REFUSED_IDENTITIES = frozenset({
    ("adjusted_ebitda", "opendoor", "2022-01-01_2022-09-30", "normalized_table"),
    ("adjusted_ebitda", "opendoor", "2023-01-01_2023-09-30", "normalized_table"),
    ("adjusted_ebitda_margin", "opendoor", "2022-01-01_2022-09-30", "normalized_table"),
    ("adjusted_ebitda_margin", "opendoor", "2023-01-01_2023-09-30", "normalized_table"),
})

UNRESOLVED_ENTITY_ID = "opendoor_unnamed_subsidiary"
CREDIT_FACILITY_EVENT = "evt:credit-facility-established:2022-10-19:4b384d2ba0fa"

#: The counts §3.1 declares for the real run, and the fixture's own.
REAL_COUNTS = {"Entity": 9, "Metric": 26, "Observation": 2704, "Event": 6,
               "Passage": 8776, "Document": 185, "Issue": 17130}
FIXTURE_COUNTS = {"Entity": 9, "Metric": 26, "Observation": 30, "Event": 5,
                  "Passage": 6, "Document": 6, "Issue": 45}


# -- provenance and the two builds -------------------------------------------------------


def provenance_for(inputs: ExtractionRunInputs, ontology) -> dict[str, str]:
    """§5.5's eight fields, exactly as a composition root would assemble them."""
    return {
        "extraction_run_id": inputs.manifest.run_id,
        "graph_run_id": "graph-test-0000",
        "graph_projection_version": GRAPH_PROJECTION_VERSION,
        "ontology_id": ontology.metadata.ontology_id,
        "ontology_version": ontology.metadata.semantic_version,
        "ontology_definition_hash": ontology.definition_hash,
        "extraction_code_commit": inputs.manifest.code_commit,
        "graph_code_commit": "0000000000000000000000000000000000000000",
    }


@pytest.fixture(scope="module")
def fixture_provenance(fixture_inputs, ontology) -> dict[str, str]:
    return provenance_for(fixture_inputs, ontology)


@pytest.fixture(scope="module")
def fixture_nodes(fixture_inputs, ontology, fixture_provenance) -> tuple[GraphNode, ...]:
    return build_nodes(fixture_inputs, ontology=ontology, provenance=fixture_provenance)


@pytest.fixture(scope="module")
def real_nodes(real_inputs, ontology) -> tuple[GraphNode, ...]:
    return build_nodes(real_inputs, ontology=ontology,
                       provenance=provenance_for(real_inputs, ontology))


def by_label(nodes) -> dict[str, list[GraphNode]]:
    grouped: dict[str, list[GraphNode]] = {}
    for node in nodes:
        grouped.setdefault(node.base_label, []).append(node)
    return grouped


def labelled(nodes, label: str) -> list[GraphNode]:
    return [node for node in nodes if label in node.labels]


def one(nodes, base_label: str, key: str) -> GraphNode:
    return next(n for n in nodes if n.base_label == base_label and n.key == key)


# -- counts ------------------------------------------------------------------------------


def test_the_fixture_yields_every_declared_node_type(fixture_nodes) -> None:
    counts = {label: len(rows) for label, rows in by_label(fixture_nodes).items()}
    assert counts == FIXTURE_COUNTS
    assert set(counts) <= set(BASE_LABELS)


def test_no_claim_and_no_fiscal_period_node_is_created(fixture_nodes) -> None:
    """§3.1's "deliberately not nodes" — a Claim node would double the graph."""
    every_label = {label for node in fixture_nodes for label in node.labels}
    assert "Claim" not in every_label
    assert "FiscalPeriod" not in every_label and "Period" not in every_label


def test_every_node_key_is_unique_within_its_label(fixture_nodes) -> None:
    pairs = [(node.base_label, node.key) for node in fixture_nodes]
    assert len(set(pairs)) == len(pairs)


@requires_real_run
def test_the_real_run_yields_the_counts_the_plan_declares(real_nodes) -> None:
    assert {label: len(rows) for label, rows in by_label(real_nodes).items()} == REAL_COUNTS


@requires_real_run
def test_the_real_run_yields_the_declared_secondary_label_counts(
        real_nodes, real_inputs) -> None:
    """`:NotAttempted` 10,852 (handoff §4.9), `:Warned` 186 (§5.5), `:Rejected` 46 (P10).

    `NOT_ATTEMPTED_CODE and "NotAttempted"` used to stand where the label does. It reads as a
    guard on the constant and is a no-op — a non-empty string `and` a non-empty string is the
    second string — so it asserted nothing about the constant and only obscured the label.
    The relationship it looked like it was checking is checked below, for real: the label is
    on exactly the rows whose `code` is that constant.
    """
    not_attempted = labelled(real_nodes, "NotAttempted")
    assert len(not_attempted) == 10852
    assert {node.properties["code"] for node in not_attempted} == {NOT_ATTEMPTED_CODE}
    assert len(not_attempted) == len(
        [row for row in real_inputs.issues if row.code == NOT_ATTEMPTED_CODE])
    assert len(labelled(real_nodes, "Warned")) == 185
    assert len(labelled(real_nodes, "Rejected")) == 49
    assert len(labelled(real_nodes, "Unresolved")) == 1


# -- rule 9: provenance ------------------------------------------------------------------


def test_every_node_carries_the_whole_provenance_block(
        fixture_nodes, fixture_provenance) -> None:
    for node in fixture_nodes:
        for key in PROVENANCE_KEYS:
            assert node.properties[key] == fixture_provenance[key], (node.base_label, key)


def test_per_fact_provenance_comes_from_the_rows(fixture_nodes, fixture_inputs) -> None:
    """§5.5: `claim_id`, `passage_id`, `document_id`, `source_lane`, `assertion_type`."""
    for node in by_label(fixture_nodes)["Observation"]:
        row = next(o for o in fixture_inputs.observations
                   if o.observation_id == node.key)
        assert node.properties["claim_id"] == row.claim_id
        assert node.properties["passage_id"] == row.passage_id
        assert node.properties["document_id"] == row.document_id
        assert node.properties["source_lane"] == row.source_lane
        assert node.properties["assertion_type"] == row.assertion_type
        # §2.3 trap 1: routing lane and ontology lane are different strings, both carried.
        assert node.properties["lane"] == row.lane


def test_a_missing_provenance_field_stops_the_build(fixture_inputs, ontology) -> None:
    incomplete = provenance_for(fixture_inputs, ontology)
    del incomplete["ontology_definition_hash"]
    with pytest.raises(NodeProjectionError, match="ontology_definition_hash"):
        build_nodes(fixture_inputs, ontology=ontology, provenance=incomplete)


def test_a_non_scalar_provenance_value_stops_the_build(fixture_inputs, ontology) -> None:
    nested = provenance_for(fixture_inputs, ontology) | {"lanes": ["tables", "narrative"]}
    with pytest.raises(NodeProjectionError, match="scalar"):
        build_nodes(fixture_inputs, ontology=ontology, provenance=nested)


@pytest.mark.parametrize("poisoned", ["source_lane", "assertion_type", "claim_id", "lane",
                                      "passage_id", "code"])
def test_a_provenance_key_that_shadows_a_filed_fact_is_a_hard_error(
        fixture_inputs, ontology, poisoned) -> None:
    """R5: provenance used to `dict.update` over the facts and win, on every node.

    `_properties` did `merged.update(shared)`, so a provenance mapping carrying
    `source_lane`, `assertion_type` or `claim_id` — all of which `_checked_provenance`
    accepts, because it deliberately permits a superset of §5.5's eight keys — replaced the
    filed value on every node in the export with one supplied by the composition root. The
    edge builder did the opposite and let the fact win, so the same poisoned mapping
    produced two different graphs from one run.

    Neither order is defensible, so neither is chosen: a collision is a programming mistake
    in the caller and now stops the build by name.
    """
    poison = provenance_for(fixture_inputs, ontology) | {poisoned: "POISON"}
    with pytest.raises(ProvenanceCollisionError, match=poisoned):
        build_nodes(fixture_inputs, ontology=ontology, provenance=poison)


def test_provenance_never_overwrites_a_filed_value(
        fixture_nodes, fixture_inputs) -> None:
    """The property the hard error protects, asserted on the clean build."""
    for node in by_label(fixture_nodes)["Observation"]:
        row = next(o for o in fixture_inputs.observations if o.observation_id == node.key)
        assert node.properties["source_lane"] == row.source_lane
        assert node.properties["assertion_type"] == row.assertion_type
        assert node.properties["claim_id"] == row.claim_id


def test_no_node_property_is_null(fixture_nodes) -> None:
    """R6: nodes now follow the edges' rule — a stored null is a fiction.

    In Cypher `SET n += {k: null}` *removes* `k`, so a null in `nodes.jsonl` describes a
    property the loaded graph will not have. 53,951 of them were exported before this change.
    Absence is the same fact and is what a G2 load would produce.
    """
    for node in fixture_nodes:
        for name, value in node.properties.items():
            assert value is not None, (node.base_label, node.key, name)


@requires_real_run
def test_no_real_node_property_is_null(real_nodes) -> None:
    for node in real_nodes:
        for name, value in node.properties.items():
            assert value is not None, (node.base_label, node.key, name)


# -- rule 8 and the Neo4j property model -------------------------------------------------


def _property_shape_violations(nodes) -> list[tuple[str, str, object]]:
    """Every property value Neo4j could not store: nested, or a mixed / non-string list."""
    bad: list[tuple[str, str, object]] = []
    for node in nodes:
        for name, value in node.properties.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                continue
            bad.append((node.base_label, name, value))
    return bad


def test_no_property_is_a_nested_map_or_a_mixed_list(fixture_nodes) -> None:
    assert _property_shape_violations(fixture_nodes) == []


@requires_real_run
def test_no_real_property_is_a_nested_map_or_a_mixed_list(real_nodes) -> None:
    assert _property_shape_violations(real_nodes) == []


def test_event_properties_are_flattened_to_prefixed_strings(fixture_nodes) -> None:
    """§5.4: one property per declared key, plus `property_names`, and never a parsed number."""
    node = one(fixture_nodes, "Event", CREDIT_FACILITY_EVENT)
    assert node.properties[f"{EVENT_PROPERTY_PREFIX}committed_capacity"] == "$525 million"
    assert node.properties[f"{EVENT_PROPERTY_PREFIX}maturity_date"] == "2023-10-31"
    assert node.properties["property_names"] == ["committed_capacity", "maturity_date"]
    assert "properties" not in node.properties


def test_a_filed_amount_is_never_parsed_into_a_number(fixture_nodes) -> None:
    for node in by_label(fixture_nodes)["Event"]:
        for name, value in node.properties.items():
            if name.startswith(EVENT_PROPERTY_PREFIX):
                assert isinstance(value, str), name


def test_a_nested_event_property_is_rejected_not_stringified(
        fixture_inputs, ontology, fixture_provenance) -> None:
    """The refusal §5.4 asks for: `MALFORMED_EVENT_PROPERTY`, not a silent `str()` of a dict."""
    event = fixture_inputs.events[0]
    broken = event.model_copy(update={
        "properties": dict(event.properties) | {"terms": {"rate": "SOFR + 3.5%"}}})
    inputs = dataclasses.replace(
        fixture_inputs, events=(broken, *fixture_inputs.events[1:]))

    with pytest.raises(MalformedEventPropertyError) as caught:
        build_nodes(inputs, ontology=ontology, provenance=fixture_provenance)
    assert "terms" in str(caught.value) and "dict" in str(caught.value)


# -- rule 7: the two event dates -----------------------------------------------------------


def test_both_event_dates_are_carried_independently_including_nulls(
        fixture_nodes, fixture_inputs) -> None:
    """§5.5: neither is ever filled from the other, and never from a filing date.

    A null date is expressed as an **absent property**, not a stored null: in Cypher
    `SET n += {occurred_on: null}` removes the key, so the two are one fact and only one of
    them is what a load would produce. The `*_date_text` fields are what make the absence
    legible; the assertion below is that nothing filled the gap, not that a null survived.
    """
    for row in fixture_inputs.events:
        node = one(fixture_nodes, "Event", row.event_id)
        assert node.properties.get("occurred_on") == row.occurred_on
        assert node.properties.get("announced_on") == row.announced_on
    announced_only = one(fixture_nodes, "Event", "evt:executive-change:undated:4c7324fecc42")
    assert "occurred_on" not in announced_only.properties
    assert announced_only.properties["announced_on"] == "2025-09-10"
    occurred_only = one(fixture_nodes, "Event", CREDIT_FACILITY_EVENT)
    assert "announced_on" not in occurred_only.properties
    assert occurred_only.properties["occurred_on"] == "2022-10-19"
    assert "date" not in occurred_only.properties


def test_no_typed_date_twin_is_projected(fixture_nodes) -> None:
    """§5.5, amended: `date()` twins are a G2 loader concern, not a projection concern.

    Measured before removal: `occurred_on_date`, `announced_on_date` and `period_end_date`
    were byte-identical string copies of the fields they twinned — the projection holds no
    database, so it could not apply `date()` — and `period_end_date` was null on all 403
    instant observations, the rows a range query most needs a date on. The loader can apply
    `date()` to the authoritative ISO field; a copy that carries no information cannot.
    """
    twins = {"occurred_on_date", "announced_on_date", "period_end_date", "instant_date_date",
             "valid_from_date", "valid_to_date"}
    for node in fixture_nodes:
        assert twins.isdisjoint(node.properties), (node.base_label, node.key)


def test_events_carry_the_same_validation_properties_as_observations(fixture_nodes) -> None:
    """§5.5: `WHERE x.validation_state = 'clean'` must not silently exclude every event.

    Clean on all of them, and clean is a measurement rather than an assumption: the manifest's
    186 warnings come from `validate_claims` over all 2,717 claims and not one of them is an
    event, because this ontology declares no event-level warning rule.
    """
    events = by_label(fixture_nodes)["Event"]
    assert len(events) == 5
    for node in events:
        assert node.properties["validation_state"] == "clean"
        assert node.properties["warning_codes"] == []
        assert "Warned" not in node.labels
    observation = by_label(fixture_nodes)["Observation"][0]
    assert {"validation_state", "warning_codes"} <= set(observation.properties)


def test_the_five_event_only_fields_survive(fixture_nodes, fixture_inputs) -> None:
    """§0b item 3: computed nowhere else in the run, so dropping them loses them."""
    for row in fixture_inputs.events:
        node = one(fixture_nodes, "Event", row.event_id)
        assert node.properties["dates_equal"] == row.dates_equal
        assert node.properties.get("review_flag") == row.review_flag
        assert node.properties.get("occurrence_date_text") == row.occurrence_date_text
        assert node.properties.get("announcement_date_text") == row.announcement_date_text
        assert node.properties.get("evidence_quoted_text") == row.evidence_quoted_text
    flagged = one(fixture_nodes, "Event", "evt:workforce-reduction:2022-11-02:3a2153734d24")
    assert flagged.properties["review_flag"] == "ANNOUNCEMENT_EQUALS_OCCURRENCE"
    assert flagged.properties["dates_equal"] is True


@requires_real_run
def test_every_real_event_pair_equals_the_catalog_pair(real_nodes, real_inputs) -> None:
    """§10 criterion 6, executable: the projected pair equals the catalog pair, nulls too."""
    projected = {n.key: (n.properties.get("occurred_on"), n.properties.get("announced_on"))
                 for n in by_label(real_nodes)["Event"]}
    assert projected == {row.event_id: (row.occurred_on, row.announced_on)
                         for row in real_inputs.events}


# -- rule 1: unresolved participants ------------------------------------------------------


def test_the_unresolved_participant_is_keyed_per_event(fixture_nodes) -> None:
    unresolved = labelled(fixture_nodes, "Unresolved")
    assert len(unresolved) == 1
    node = unresolved[0]
    assert node.key == f"{UNRESOLVED_ENTITY_ID}{UNRESOLVED_SEPARATOR}{CREDIT_FACILITY_EVENT}"
    assert node.properties["resolved"] is False
    assert node.properties["extraction_entity_id"] == UNRESOLVED_ENTITY_ID
    assert node.labels == ("Entity", "Subsidiary", "Company", "Unresolved")


def test_two_events_yield_two_unresolved_nodes(fixture_inputs, ontology) -> None:
    """§4.2: one placeholder node per event, never one per corpus.

    Built by hand because the run contains exactly one such event — and one event cannot show
    the difference between per-event and per-corpus keying, which is the whole policy.
    """
    event = next(e for e in fixture_inputs.events
                 if any(PLACEHOLDER_MARKER in p.entity_id for p in e.participants))
    claim = fixture_inputs.claims_by_id[event.claim_id]
    evidence = fixture_inputs.evidence_by_claim_id[event.claim_id][0]

    second_event_id = event.event_id.replace("2022-10-19", "2023-04-01")
    second_claim_id = claim.claim_id + "ff"
    inputs = ExtractionRunInputs.from_rows(
        manifest=fixture_inputs.manifest,
        claims=(claim, claim.model_copy(update={
            "claim_id": second_claim_id, "payload_id": second_event_id})),
        events=(event, event.model_copy(update={
            "event_id": second_event_id, "claim_id": second_claim_id})),
        evidence=(evidence, evidence.model_copy(update={"claim_id": second_claim_id})),
        passages=fixture_inputs.passages, documents=fixture_inputs.documents)

    nodes = build_nodes(inputs, ontology=ontology,
                        provenance=provenance_for(inputs, ontology))
    unresolved = labelled(nodes, "Unresolved")
    assert {node.key for node in unresolved} == {
        f"{UNRESOLVED_ENTITY_ID}{UNRESOLVED_SEPARATOR}{event.event_id}",
        f"{UNRESOLVED_ENTITY_ID}{UNRESOLVED_SEPARATOR}{second_event_id}",
    }
    assert all(node.properties["extraction_entity_id"] == UNRESOLVED_ENTITY_ID
               for node in unresolved)
    assert all(node.properties["resolved"] is False for node in unresolved)


def test_no_entity_text_is_invented(fixture_nodes, fixture_inputs) -> None:
    """§1.4a: `named` and `entity_text` reach no catalog.

    The only filed wording that survives is `source_name` / `target_name` on the four
    relationship claims, so every `entity_text` in the graph must be one of those strings and
    an entity reached only as an event participant or an ontology instance must carry none.
    """
    filed = set()
    for relationship in fixture_inputs.relationships:
        metadata = fixture_inputs.claims_by_id[relationship.claim_id].extractor_metadata
        filed.update(metadata[key] for key in ("source_name", "target_name")
                     if key in metadata)

    for node in by_label(fixture_nodes)["Entity"]:
        assert "entity_text_variants" not in node.properties
        if "entity_text" in node.properties:
            assert node.properties["entity_text"] in filed

    # Reached only as an ontology instance — nothing filed a name for it.
    assert "entity_text" not in one(fixture_nodes, "Entity", "sec_edgar").properties
    assert "entity_text" not in one(fixture_nodes, "Entity", "nasdaq").properties


def test_the_unresolved_node_carries_only_the_filed_wording(fixture_nodes) -> None:
    node = labelled(fixture_nodes, "Unresolved")[0]
    assert node.properties["entity_text"] == "a subsidiary of the Company"


# -- §3.1's label chains ------------------------------------------------------------------


def test_an_instance_resolves_to_its_concept_before_ancestors_are_asked_for(
        fixture_nodes, ontology) -> None:
    """Trap (a): `ancestors('opendoor')` is `()`; `ancestors('public_company')` is not."""
    assert ontology.registry.ancestors("opendoor") == ()
    node = one(fixture_nodes, "Entity", "opendoor")
    assert node.labels == ("Entity", "PublicCompany", "Company")
    assert node.properties["ontology_instance"] is True
    assert node.properties["entity_type"] == "public_company"


def test_an_agreement_type_resolves_its_own_is_a_chain(fixture_nodes, ontology) -> None:
    """Trap (b): the facility's `entity_type` is an `agreement_type`, not an `entity_type`."""
    assert ontology.registry.find("asset_backed_debt_facility").category == "agreement_type"
    node = one(fixture_nodes, "Entity", "asset_backed_senior_revolving_credit_facility")
    assert node.labels == ("Entity", "AssetBackedDebtFacility", "CreditFacility",
                           "CreditAgreement", "Agreement")


def test_an_undeclared_type_is_a_refusal_not_a_guessed_label(
        fixture_inputs, ontology, fixture_provenance) -> None:
    relationship = fixture_inputs.relationships[0]
    inputs = dataclasses.replace(fixture_inputs, relationships=(
        relationship.model_copy(update={"target_type": "special_purpose_vehicle"}),
        *fixture_inputs.relationships[1:]))
    with pytest.raises(UndeclaredEntityTypeError, match="special_purpose_vehicle"):
        build_nodes(inputs, ontology=ontology, provenance=fixture_provenance)


def test_every_entity_source_reaches_a_node(fixture_nodes, fixture_inputs) -> None:
    """§3.1: instances, participants, endpoints and observation subjects, all four."""
    keys = {node.key for node in by_label(fixture_nodes)["Entity"]}
    extraction_ids = {node.properties["extraction_entity_id"]
                      for node in by_label(fixture_nodes)["Entity"]}
    for instance in ("opendoor", "sec_edgar", "nasdaq", "opendoor_accountable"):
        assert instance in keys
    for observation in fixture_inputs.observations:
        assert observation.subject_entity_id in keys
    for event in fixture_inputs.events:
        for participant in event.participants:
            assert participant.entity_id in extraction_ids
    for relationship in fixture_inputs.relationships:
        assert {relationship.source_id, relationship.target_id} <= extraction_ids


# -- rules 3 and 4: the two derivations ----------------------------------------------------


def test_a_tampered_observation_id_stops_the_build(
        fixture_inputs, ontology, fixture_provenance) -> None:
    """Rule 3: every id is recomputed and compared, and a divergence is a build failure."""
    first = fixture_inputs.observations[0]
    inputs = dataclasses.replace(fixture_inputs, observations=(
        first.model_copy(update={"observation_id": "obs:stale-id"}),
        *fixture_inputs.observations[1:]))
    with pytest.raises(ObservationIdMismatch):
        build_nodes(inputs, ontology=ontology, provenance=fixture_provenance)


def test_warned_observations_carry_state_codes_and_a_label(fixture_nodes) -> None:
    warned = labelled(fixture_nodes, "Warned")
    assert len(warned) == 9  # the fixture README's derivation table
    for node in warned:
        assert node.properties["validation_state"] == "warned"
        assert node.properties["warning_codes"] == ["unpreferred_source_lane"]
    clean = [n for n in by_label(fixture_nodes)["Observation"] if "Warned" not in n.labels]
    assert len(clean) == 21
    for node in clean:
        assert node.properties["validation_state"] == "clean"
        assert node.properties["warning_codes"] == []


@requires_real_run
def test_the_real_run_warns_exactly_the_manifest_count(real_nodes, real_inputs) -> None:
    warned = labelled(real_nodes, "Warned")
    assert len(warned) == real_inputs.manifest.ontology_validation_warnings == 185
    assert {code for node in warned for code in node.properties["warning_codes"]} == {
        "unpreferred_source_lane"}


@requires_real_run
def test_the_reconciliation_actually_runs_on_the_whole_run(real_inputs, ontology) -> None:
    """Proof that §5.5's cross-check is live, not merely importable."""
    checks = tuple(
        check.model_copy(update={"warnings": 999}) if check.check == "ontology_validation"
        else check for check in real_inputs.manifest.verification)
    tampered = dataclasses.replace(
        real_inputs, manifest=real_inputs.manifest.model_copy(
            update={"verification": checks}))
    with pytest.raises(WarningCountMismatch):
        build_nodes(tampered, ontology=ontology,
                    provenance=provenance_for(real_inputs, ontology))


# -- rule 2: the population fields ---------------------------------------------------------


def test_no_population_role_or_confidence_is_ever_projected(
        fixture_nodes, fixture_inputs) -> None:
    filed = {row.observation_id: row.population_definition_raw
             for row in fixture_inputs.observations}
    for node in by_label(fixture_nodes)["Observation"]:
        assert "population_role" not in node.properties
        assert "population_confidence" not in node.properties
        assert "population_definition_normalized" not in node.properties
        # Present exactly where the catalog filed one — a null becomes no property at all,
        # which is what a `SET n += {...}` load would leave behind.
        assert node.properties.get("population_definition_raw") == filed[node.key]


@requires_real_run
def test_only_the_filed_population_definitions_are_projected(real_nodes) -> None:
    filed = [node for node in by_label(real_nodes)["Observation"]
             if node.properties.get("population_definition_raw") is not None]
    assert len(filed) == 88  # §5.4: 88 non-null of 2,704


# -- rule 5: no issue-based suppression -----------------------------------------------------


def test_an_issue_never_suppresses_the_event_on_its_passage(
        fixture_nodes, fixture_inputs) -> None:
    """Handoff §4.2: a refusal is per-reading or per-property, never per-node."""
    event_passages = {node.properties["passage_id"]
                      for node in by_label(fixture_nodes)["Event"]}
    for code in ("PROPERTY_VALUE_NOT_IN_PASSAGE", "PARTICIPANT_NOT_NAMED"):
        cited = {row.passage_id for row in fixture_inputs.issues if row.code == code}
        assert cited, code
        assert cited <= event_passages, code


@requires_real_run
def test_the_five_flagged_events_are_present_on_the_real_run(
        real_nodes, real_inputs) -> None:
    """§10 criterion 5: four `PROPERTY_VALUE_NOT_IN_PASSAGE` and one `PARTICIPANT_NOT_NAMED`."""
    property_issues = [r for r in real_inputs.issues
                       if r.code == "PROPERTY_VALUE_NOT_IN_PASSAGE"]
    participant_issues = [r for r in real_inputs.issues
                          if r.code == "PARTICIPANT_NOT_NAMED"]
    assert len(property_issues) == 4 and len(participant_issues) == 1

    events = by_label(real_nodes)["Event"]
    assert len(events) == 6
    flagged = {row.passage_id for row in property_issues + participant_issues}
    assert flagged <= {node.properties["passage_id"] for node in events}


def test_issues_are_keyed_on_the_supplied_id_and_labelled(fixture_nodes, fixture_inputs)\
        -> None:
    """§4.3: the minting rule is deleted; `issue_id` is the key."""
    issues = by_label(fixture_nodes)["Issue"]
    assert {node.key for node in issues} == {row.issue_id for row in fixture_inputs.issues}
    not_attempted = labelled(fixture_nodes, "NotAttempted")
    assert {node.properties["code"] for node in not_attempted} == {NOT_ATTEMPTED_CODE}
    assert len(not_attempted) == 1  # the fixture's single `NO_STORED_ANSWER` row


def test_a_rejection_travels_as_an_opaque_string_never_as_fact(
        fixture_nodes, fixture_inputs) -> None:
    """§2.3: `raw_finding` is pre-validation model output. It is carried, never read."""
    rejected = labelled(fixture_nodes, "Rejected")
    assert len(rejected) == fixture_inputs.rejection_issue_join.mirrored == 9
    for node in rejected:
        assert node.labels[0] == "Issue"
        assert node.labels[-1] == "Rejected"
        assert node.properties["rejection_id"].startswith("rej:")
        assert isinstance(node.properties["raw_finding_json"], str)
        assert json.loads(node.properties["raw_finding_json"])
        assert "raw_finding" not in node.properties


# -- rule 6: no refused reading is resurrected -----------------------------------------------


def _observation_identities(nodes) -> set[tuple[str, str, str, str, str]]:
    return {(node.properties["metric_id"], node.properties["subject_entity_id"],
             node.properties["period_key"], node.properties["source_lane"],
             node.properties["passage_id"])
            for node in by_label(nodes)["Observation"]}


def test_no_refused_conflict_guard_reading_becomes_a_node(fixture_nodes) -> None:
    """§10 criterion 10 at projection time — a regression guard, the input is already clean."""
    identities = _observation_identities(fixture_nodes)
    for metric, subject, period, lane in REFUSED_IDENTITIES:
        assert (metric, subject, period, lane, CONFLICT_GUARD_PASSAGE) not in identities


def test_the_kept_readings_on_the_same_passage_survive(fixture_nodes) -> None:
    """The converse, and the reason a blanket issue filter would be wrong: 6 kept on `#p29`."""
    kept = [node for node in by_label(fixture_nodes)["Observation"]
            if node.properties["passage_id"] == CONFLICT_GUARD_PASSAGE]
    assert len(kept) == 6
    assert {node.properties["period_key"] for node in kept} == {
        "2022-04-01_2022-12-31", "2022-07-01_2023-03-31", "2022-10-01_2023-06-30"}


@requires_real_run
def test_no_refused_reading_is_resurrected_on_the_real_run(real_nodes) -> None:
    identities = _observation_identities(real_nodes)
    for metric, subject, period, lane in REFUSED_IDENTITIES:
        assert (metric, subject, period, lane, CONFLICT_GUARD_PASSAGE) not in identities


# -- passages, documents and metrics ---------------------------------------------------------


def test_passages_are_the_cited_ones_and_documents_are_theirs(
        fixture_nodes, fixture_inputs) -> None:
    cited = ({row.passage_id for row in fixture_inputs.claims}
             | {row.passage_id for row in fixture_inputs.issues})
    assert {node.key for node in by_label(fixture_nodes)["Passage"]} == cited
    documents = {fixture_inputs.passages_by_id[passage].document_id for passage in cited}
    assert {node.key for node in by_label(fixture_nodes)["Document"]} == documents


def test_a_cited_passage_missing_from_the_catalog_stops_the_build(
        fixture_inputs, ontology, fixture_provenance) -> None:
    inputs = dataclasses.replace(fixture_inputs, passages=fixture_inputs.passages[1:],
                                 passages_by_id={
                                     row.passage_id: row
                                     for row in fixture_inputs.passages[1:]})
    with pytest.raises(NodeProjectionError, match="absent from passages.jsonl"):
        build_nodes(inputs, ontology=ontology, provenance=fixture_provenance)


def test_all_twenty_six_metrics_are_nodes_with_a_scope_flag(fixture_nodes) -> None:
    """§14.4: a metric with no observation is a coverage gap worth seeing."""
    metrics = by_label(fixture_nodes)["Metric"]
    assert len(metrics) == 26
    in_scope = [node for node in metrics if node.properties["in_scope_v1"]]
    assert len(in_scope) == 20
    deferred = {node.key for node in metrics if not node.properties["in_scope_v1"]}
    assert deferred == {"cost_of_revenue", "gaap_gross_profit", "homes_under_resale_contract",
                        "inventory_balance", "inventory_valuation_adjustment", "revenue"}
    observed = {node.properties["metric_id"]
                for node in by_label(fixture_nodes)["Observation"]}
    assert observed < {node.key for node in metrics}


def test_the_period_key_is_recomputed_onto_every_observation(
        fixture_nodes, fixture_inputs) -> None:
    """§4.1: no catalog carries `period_key`; it is `PeriodRef.key`'s algorithm, not a copy."""
    for row in fixture_inputs.observations:
        node = one(fixture_nodes, "Observation", row.observation_id)
        assert node.properties["period_key"] == period_key(row)


# -- rule 10: determinism ---------------------------------------------------------------------


def test_two_projections_of_one_run_are_identical(
        fixture_inputs, ontology, fixture_provenance) -> None:
    """§4.4's precondition. Byte-identity of the export is the export stage's test; this is
    the part the node builder owns — same nodes, same order, same properties."""
    first = build_nodes(fixture_inputs, ontology=ontology, provenance=fixture_provenance)
    second = build_nodes(fixture_inputs, ontology=ontology, provenance=fixture_provenance)
    assert first == second
    assert ([json.dumps(node.properties, sort_keys=True, default=str) for node in first]
            == [json.dumps(node.properties, sort_keys=True, default=str) for node in second])


def test_every_collection_valued_property_is_sorted_or_filed_order(fixture_nodes) -> None:
    """Rule 10: a set may never reach a property unsorted.

    Only the properties built *from* a set are listed. `allowed_units`,
    `source_lane_preferences` and `subject_types` are declared ontology tuples whose order is
    meaningful — `source_lane_preferences` is a *preference* order — and sorting them would
    destroy information rather than add determinism they already have.
    """
    for node in fixture_nodes:
        for name in ("property_names", "participant_roles", "warning_codes",
                     "entity_text_variants", "entity_type_variants"):
            value = node.properties.get(name)
            if value is not None:
                assert value == sorted(value), (node.key, name)
