"""The key policy, and in particular §4.2 — the one place the graph mints a key.

The unresolved-entity rule is the piece of this layer most likely to be argued about, so it
is tested against the real placeholder the run contains (`opendoor_unnamed_subsidiary`, the
borrower of the 2022 credit facility and the source of `BORROWS_UNDER`) rather than against
an invented string.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph.core.inputs import EventRow, Participant
from graph.core.models import (
    BASE_LABELS,
    GRAPH_PROJECTION_VERSION,
    GraphEdge,
    GraphExport,
    GraphNode,
)
from graph.core.keys import (
    NodeKeyError,
    PlaceholderAttributionError,
    derived_edge_key,
    document_key_of_passage,
    entity_node_key,
    is_placeholder,
    owning_event_id,
    relationship_edge_key,
    relationship_endpoint_key,
    require_node_key,
    unresolved_node_key,
)

PLACEHOLDER = "opendoor_unnamed_subsidiary"
FACILITY_EVENT = "evt:credit-facility-established:2022-10-19:4b384d2ba0fa"


def _event(event_id: str, *, passage_id: str, entity_id: str = PLACEHOLDER) -> EventRow:
    """A minimal event row carrying one participant. Only the fields the rule reads matter.

    "Minimal" is now every declared field, several of them explicitly `None`: the readers
    stopped defaulting absent fields (§2.2's "nothing substitutes a default for a missing
    input"), so a row that omits `announced_on` no longer means "no announcement date" — it
    means the writer stopped emitting the column, and the reader says so.
    """
    return EventRow(
        event_id=event_id, claim_id=f"claim:event:{event_id[-12:]}",
        event_type_id="credit_facility_established", occurred_on=None, announced_on=None,
        occurrence_date_text=None, announcement_date_text=None, dates_equal=False,
        review_flag=None, evidence_quoted_text=None,
        participants=(Participant(role="borrower", entity_id=entity_id,
                                  entity_type="subsidiary"),),
        properties={}, assertion_type="reported", passage_id=passage_id,
        document_id=passage_id.split("#")[0], document_type="narrative_primary",
        lane="events")


# -- placeholder detection -------------------------------------------------------------


@pytest.mark.parametrize("entity_id,expected", [
    (PLACEHOLDER, True),
    ("opendoor_unnamed_company", True),
    ("opendoor", False),
    ("asset_backed_senior_revolving_credit_facility", False),
    ("keith_rabois", False),
])
def test_is_placeholder_reads_the_only_signal_that_survives(entity_id, expected) -> None:
    assert is_placeholder(entity_id) is expected


def test_unresolved_node_key_is_the_extraction_id_scoped_to_one_event() -> None:
    key = unresolved_node_key(PLACEHOLDER, FACILITY_EVENT)
    assert key == f"{PLACEHOLDER}#{FACILITY_EVENT}"
    assert key.startswith(PLACEHOLDER)  # the extraction id survives verbatim, §4.2


def test_two_events_yield_two_keys() -> None:
    """The whole point of §4.2: one node per event, never one per corpus."""
    first = unresolved_node_key(PLACEHOLDER, FACILITY_EVENT)
    second = unresolved_node_key(PLACEHOLDER, "evt:credit-facility-established:2023-06-01:aaaaaaaaaaaa")
    assert first != second
    assert len({first, second}) == 2


def test_a_named_entity_keeps_its_extraction_id() -> None:
    assert entity_node_key("opendoor") == "opendoor"
    assert entity_node_key("opendoor", event_id=FACILITY_EVENT) == "opendoor"


def test_a_placeholder_without_its_event_is_an_error_not_a_guess() -> None:
    with pytest.raises(PlaceholderAttributionError, match="§4.2"):
        entity_node_key(PLACEHOLDER)


# -- relationship endpoints share the event's key --------------------------------------


def test_a_placeholder_relationship_endpoint_lands_on_the_events_node(
        fixture_inputs) -> None:
    relationship = next(row for row in fixture_inputs.relationships
                        if row.relationship_id == "BORROWS_UNDER")
    event = next(row for row in fixture_inputs.events if row.event_id == FACILITY_EVENT)

    endpoint = relationship_endpoint_key(
        relationship.source_id, relationship, fixture_inputs.events)
    participant = entity_node_key(event.participants[0].entity_id, event_id=event.event_id)
    assert endpoint == participant == f"{PLACEHOLDER}#{FACILITY_EVENT}"


def test_a_named_relationship_endpoint_is_untouched(fixture_inputs) -> None:
    relationship = next(row for row in fixture_inputs.relationships
                        if row.relationship_id == "BORROWS_UNDER")
    assert relationship_endpoint_key(
        relationship.target_id, relationship, fixture_inputs.events
    ) == "asset_backed_senior_revolving_credit_facility"


def test_an_ambiguous_placeholder_endpoint_raises(fixture_inputs) -> None:
    relationship = next(row for row in fixture_inputs.relationships
                        if row.relationship_id == "BORROWS_UNDER")
    twin = _event("evt:credit-facility-established:2022-10-19:ffffffffffff",
                  passage_id=relationship.passage_id)
    with pytest.raises(PlaceholderAttributionError, match="exactly one"):
        relationship_endpoint_key(
            relationship.source_id, relationship, [*fixture_inputs.events, twin])


def test_an_unattributable_placeholder_endpoint_raises(fixture_inputs) -> None:
    relationship = next(row for row in fixture_inputs.relationships
                        if row.relationship_id == "BORROWS_UNDER")
    with pytest.raises(PlaceholderAttributionError, match="0 events"):
        relationship_endpoint_key(relationship.source_id, relationship, [])


def test_owning_event_id_matches_on_participation_not_on_passage() -> None:
    events = [_event("evt:a", passage_id="norm:x:y:z.htm#p1"),
              _event("evt:b", passage_id="norm:x:y:z.htm#p2", entity_id="opendoor")]
    assert owning_event_id(PLACEHOLDER, events) == "evt:a"


# -- the remaining keys ----------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_id_is_never_a_node_key(value) -> None:
    with pytest.raises(NodeKeyError):
        require_node_key(value, what="passage_id")


def test_a_document_key_is_its_passages_key_without_the_suffix() -> None:
    passage = "norm:0001801169:0001801169-22-000108:open-20220930.htm#p117"
    assert document_key_of_passage(passage) == (
        "norm:0001801169:0001801169-22-000108:open-20220930.htm")


def test_a_passage_id_with_no_suffix_is_refused() -> None:
    with pytest.raises(NodeKeyError, match="not a passage id"):
        document_key_of_passage("norm:0001801169:0001801169-22-000108:open-20220930.htm")


def test_every_fixture_passage_derives_its_own_document(fixture_inputs) -> None:
    for passage in fixture_inputs.passages:
        assert document_key_of_passage(passage.passage_id) == passage.document_id


# -- edge keys -------------------------------------------------------------------------


def test_a_relationship_edge_is_keyed_on_the_id_the_run_supplies(fixture_inputs) -> None:
    relationship = fixture_inputs.relationships[0]
    assert relationship_edge_key(relationship.relationship_instance_id) == (
        relationship.relationship_instance_id)


def test_a_derived_edge_key_is_readable_and_deterministic() -> None:
    key = derived_edge_key("EVIDENCED_BY", "obs:a", "norm:x#p1")
    assert key == "EVIDENCED_BY:obs:a:norm:x#p1"
    assert key == derived_edge_key("EVIDENCED_BY", "obs:a", "norm:x#p1")


def test_a_discriminated_edge_key_keeps_the_readable_part_and_adds_a_digest() -> None:
    borrower = derived_edge_key("PARTICIPATES_IN", "opendoor", "evt:x", "borrower")
    guarantor = derived_edge_key("PARTICIPATES_IN", "opendoor", "evt:x", "guarantor")
    assert borrower != guarantor
    assert borrower.startswith("PARTICIPATES_IN:opendoor:evt:x:")
    assert len(borrower.rsplit(":", 1)[1]) == 12


def test_an_edge_key_refuses_an_empty_endpoint() -> None:
    with pytest.raises(NodeKeyError):
        derived_edge_key("PART_OF", "norm:x#p1", "")


# -- the node and edge contract the projection stages build against --------------------


def test_a_node_leads_with_its_base_label_and_keeps_the_is_a_chain_order() -> None:
    node = GraphNode(
        key=f"{PLACEHOLDER}#{FACILITY_EVENT}", base_label="Entity",
        labels=("Entity", "Subsidiary", "Company", "Unresolved"),
        properties={"extraction_entity_id": PLACEHOLDER, "resolved": False})
    assert node.labels[0] == node.base_label
    # Not sorted: `:Subsidiary:Company` is the specialization order, not the alphabet.
    assert node.labels != tuple(sorted(node.labels))


@pytest.mark.parametrize("labels,base_label", [
    (("Subsidiary", "Entity"), "Entity"),          # base label not first
    (("Entity", "Entity"), "Entity"),              # repeated label
    ((), "Entity"),                                # no labels at all
])
def test_a_malformed_label_set_is_refused(labels, base_label) -> None:
    with pytest.raises(ValidationError):
        GraphNode(key="opendoor", base_label=base_label, labels=labels, properties={})


def test_a_node_key_and_base_label_are_both_constrained() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        GraphNode(key="  ", base_label="Entity", labels=("Entity",), properties={})
    with pytest.raises(ValidationError, match="Claim"):
        GraphNode(key="claim:x", base_label="Claim", labels=("Claim",), properties={})


def test_the_declared_sort_order_is_what_determinism_rests_on() -> None:
    nodes = [
        GraphNode(key="obs:b", base_label="Observation", labels=("Observation",),
                  properties={}),
        GraphNode(key="opendoor", base_label="Entity", labels=("Entity",), properties={}),
        GraphNode(key="obs:a", base_label="Observation", labels=("Observation",),
                  properties={}),
    ]
    edges = [
        GraphEdge(edge_key="B", type="PART_OF", source_key="norm:x#p1",
                  source_base_label="Passage", target_key="norm:x",
                  target_base_label="Document", properties={}),
        GraphEdge(edge_key="A", type="EVIDENCED_BY", source_key="obs:a",
                  source_base_label="Observation", target_key="norm:x#p1",
                  target_base_label="Passage", properties={}),
    ]
    export = GraphExport(nodes=tuple(nodes), edges=tuple(edges)).sorted()
    assert [node.key for node in export.nodes] == ["opendoor", "obs:a", "obs:b"]
    assert [edge.edge_key for edge in export.edges] == ["A", "B"]
    assert export.sorted() == export  # idempotent
    assert export.projection_version == GRAPH_PROJECTION_VERSION


def test_an_edge_endpoint_label_must_be_a_declared_base_label() -> None:
    with pytest.raises(ValidationError, match="Claim"):
        GraphEdge(edge_key="k", type="EVIDENCED_BY", source_key="claim:x",
                  source_base_label="Claim", target_key="norm:x#p1",
                  target_base_label="Passage", properties={})
    assert set(BASE_LABELS) == {"Entity", "Metric", "Observation", "Event", "Passage",
                                "Document", "Issue"}
