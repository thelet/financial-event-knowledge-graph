"""F0 Part E: the node a non-passage citation lands on, and the properties it carries.

Two things are proved here that no run can prove today, because
`extract-v1-lexical-7f72d6172630` cites nothing but filed passages:

1. a non-passage evidence row projects to an `:EvidenceSource` node and **never** to a
   fabricated `:Passage`;
2. that node and the `EVIDENCED_BY` edge pointing at it agree on a key, so the pair does not
   dangle the day F1 writes the first `xbrl_fact` row.

Both run off `tests/fixtures/evidence_contract/valid_rows.jsonl` — synthetic by necessity, and
marked as such in that directory's README. The filed-passage half of the contract is covered
against the real corpus everywhere else in this package; what needs a fixture is the half the
corpus cannot yet exercise.

The third thing here is `:Entity` instance properties, which *is* real: `entities.yaml`
declares `cik`, `tickers` and `exchange` on `opendoor` and `mic` on `nasdaq`, and the pre-F0
projection dropped all four.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import REAL_RUN_AVAILABLE, REAL_RUN_REASON
from graph.core.inputs import EVIDENCE_ROW_MODELS, EvidenceRow, parse_evidence_row
from graph.core.models import GraphNode
from graph.stages.load.loader import CONCRETE_LABELS
from graph.stages.load.schema import KEY_PROPERTIES
from graph.stages.projection.evidence_sources import (
    EVIDENCE_SOURCE_LABEL,
    EVIDENCE_SOURCE_LABELS,
    evidence_source_of,
    evidence_sources,
)
from graph.stages.projection.nodes import build_nodes
from test_nodes import fixture_provenance  # noqa: F401 - a fixture, used by name

requires_real_run = pytest.mark.skipif(not REAL_RUN_AVAILABLE, reason=REAL_RUN_REASON)


@pytest.fixture(scope="module")
def fixture_nodes(fixture_inputs, ontology, fixture_provenance) -> tuple[GraphNode, ...]:  # noqa: F811
    return build_nodes(fixture_inputs, ontology=ontology, provenance=fixture_provenance)

FIXTURE_ROWS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "evidence_contract" / "valid_rows.jsonl"
)


def fixture_rows():
    return [parse_evidence_row(json.loads(line))
            for line in FIXTURE_ROWS.read_text(encoding="utf-8").splitlines() if line.strip()]


# -- the mapping is closed --------------------------------------------------------------------


def test_every_non_passage_evidence_kind_has_a_source_label():
    """A kind with a reader and no label would project as an unlabelled node — the shape of a
    fact whose origin cannot be told from a filed one. `evidence_sources` refuses instead."""
    passage_kinds = {"normalized_passage", "normalized_table"}
    assert set(EVIDENCE_SOURCE_LABELS) == set(EVIDENCE_ROW_MODELS) - passage_kinds


def test_every_source_label_is_on_the_loader_allowlist():
    """`loader.CONCRETE_LABELS` is hand-maintained, so this is the test that keeps it honest."""
    assert set(EVIDENCE_SOURCE_LABELS.values()) <= set(CONCRETE_LABELS)


def test_the_base_label_key_property_is_snake_cased():
    """`EvidenceSource` is the first multi-word base label. `label.lower() + "_id"` gave
    `evidencesource_id`, which no node carries — every source node would have merged onto one
    null key, and the uniqueness constraint would have protected nothing."""
    assert KEY_PROPERTIES["EvidenceSource"] == "evidence_source_id"


# -- passage rows are untouched ---------------------------------------------------------------


def test_a_filed_passage_row_yields_no_evidence_source():
    """The passage chain is decided by the row's *shape*, not by whether some id is populated."""
    for row in fixture_rows():
        if isinstance(row, EvidenceRow):
            assert evidence_source_of(row) is None


def test_no_source_node_carries_a_passage_id():
    """The specific lie F0 §2.2 exists to prevent: an XBRL fact wearing a passage citation."""
    for source in evidence_sources(fixture_rows()):
        assert "passage_id" not in source.properties


# -- the source nodes themselves ---------------------------------------------------------------


def test_each_non_passage_kind_projects_one_source_node():
    sources = evidence_sources(fixture_rows())
    assert {s.evidence_kind for s in sources} == set(EVIDENCE_SOURCE_LABELS)
    for source in sources:
        assert source.labels == (EVIDENCE_SOURCE_LABEL,
                                 EVIDENCE_SOURCE_LABELS[source.evidence_kind])
        assert source.properties["evidence_source_id"] == source.key
        assert source.key.strip()


def test_two_citations_of_one_source_are_one_node():
    """Deduplication is the source's job, not the claim's: an XBRL fact cited by revenue and by
    gross profit is one tagged value, and two nodes for it would double-count the evidence."""
    rows = [r for r in fixture_rows() if not isinstance(r, EvidenceRow)]
    doubled = evidence_sources(rows + rows)
    assert len(doubled) == len(evidence_sources(rows))


def test_source_identity_is_deterministic_and_order_independent():
    rows = [r for r in fixture_rows() if not isinstance(r, EvidenceRow)]
    forward = [s.key for s in evidence_sources(rows)]
    backward = [s.key for s in evidence_sources(list(reversed(rows)))]
    assert sorted(forward) == sorted(backward)
    assert forward == [s.key for s in evidence_sources(rows)]


def test_a_calculated_source_names_its_inputs_and_not_a_passage():
    """§7.4: a calculated fact cites the arithmetic and its inputs. Citing a filed passage for
    a derived number would say the filing reported it, which is a provenance lie."""
    calculated = [s for s in evidence_sources(fixture_rows())
                  if s.evidence_kind == "calculated"]
    assert len(calculated) == 1
    properties = calculated[0].properties
    assert properties["input_observation_ids"]
    assert isinstance(properties["input_observation_ids"], list)
    assert properties["calculation_expression"]
    assert "passage_id" not in properties and "document_id" not in properties


# -- the real run is unchanged -----------------------------------------------------------------


def test_every_non_passage_kind_is_a_declared_evidenced_by_target(ontology):
    """The edge that carries citations must accept every kind that can be cited.

    Review found `market_data` and `calculated` validated, written and projected as nodes, and
    then refused by `EVIDENCED_BY` — so F8 and every calculated fact were blocked on one line
    of `relationships.yaml` that F0 owned. A node with no edge is a lane stopped at the last
    step, which is the failure F0 exists to prevent.
    """
    predicate = ontology.registry.find("evidenced_by")
    assert set(EVIDENCE_SOURCE_LABELS) <= set(predicate.allowed_target_types)


def test_each_non_passage_kind_yields_one_node_and_one_matching_edge(
        fixture_inputs, ontology, fixture_provenance):  # noqa: F811
    """The pair, end to end — the claim this module used to make and not prove.

    Building the node without building the edge would have hidden exactly the defect above, so
    this drives `build_nodes` and `build_edges` over one synthetic claim per kind and asserts
    the edge's target is the node's key.
    """
    import dataclasses

    from graph.stages.projection.edges import build_edges

    rows = [r for r in fixture_rows() if not isinstance(r, EvidenceRow)]
    assert rows, "the contract fixture must carry non-passage rows"
    for row in rows:
        claim = next(c for c in fixture_inputs.claims)
        stitched = dataclasses.replace(
            fixture_inputs,
            evidence_by_claim_id={claim.claim_id: (dataclasses.replace(row, claim_id=claim.claim_id)
                                                   if dataclasses.is_dataclass(row)
                                                   else row.model_copy(
                                                       update={"claim_id": claim.claim_id}),)},
        )
        nodes = build_nodes(stitched, ontology=ontology, provenance=fixture_provenance)
        sources = [n for n in nodes if n.base_label == EVIDENCE_SOURCE_LABEL]
        assert len(sources) == 1, row.evidence_kind
        edges = [e for e in build_edges(stitched, ontology=ontology,
                                        provenance=fixture_provenance)
                 if e.type == "EVIDENCED_BY"]
        assert len(edges) == 1, row.evidence_kind
        assert edges[0].target_key == sources[0].key, row.evidence_kind


@requires_real_run
def test_the_real_run_projects_no_evidence_source(real_inputs):
    """Zero, and that is the point: every one of the run's evidence rows is a filed passage, so
    Part E must be provable without one. If this ever fails, a lane started emitting
    non-passage evidence and the count belongs in the report."""
    rows = [row for rows in real_inputs.evidence_by_claim_id.values() for row in rows]
    assert rows, "the real run should carry evidence"
    assert evidence_sources(rows) == ()


# -- ontology instance properties ---------------------------------------------------------------


def _entity(nodes, key):
    return next(n for n in nodes if n.key == key)


def test_declared_instance_properties_reach_the_graph(fixture_nodes):
    """`entities.yaml` declares them and the pre-F0 builder read `instance_id`, `concept_id`
    and `label` only — so the graph could not answer "what exchange does OPEN trade on" about a
    fact it already held."""
    opendoor = _entity(fixture_nodes, "opendoor")
    assert opendoor.properties["cik"] == "0001801169"
    assert opendoor.properties["exchange"] == "Nasdaq"
    assert opendoor.properties["tickers"] == "OPEN,OPENL,OPENW,OPENZ"
    assert _entity(fixture_nodes, "nasdaq").properties["mic"] == "XNAS"


def test_an_entity_that_is_not_an_ontology_instance_gains_nothing(fixture_nodes):
    """Only named individuals carry them. A claim participant has no declaration to project,
    and inventing a default would put a value in the graph that no YAML file states."""
    for node in fixture_nodes:
        if node.base_label == "Entity" and not node.properties.get("ontology_instance"):
            assert "cik" not in node.properties
            assert "mic" not in node.properties


def test_no_undeclared_key_leaks_from_the_yaml(fixture_nodes, ontology):
    """The projection copies the instance's `properties` block and nothing else — not its
    `concept_id`, not its description, not any future sibling key.

    The permitted set is read from the ontology rather than listed here, so a property added
    to `entities.yaml` is covered automatically and a property invented by the *projection*
    still fails. Listing them by hand missed `channel_type` on the two disclosure channels.
    """
    declared = {
        name
        for instance in ontology.registry.definitions.instances
        for name in (getattr(instance, "properties", None) or {})
    }
    reserved = {"entity_id", "extraction_entity_id", "resolved", "ontology_instance", "label",
                "entity_type", "entity_text", "scoped_to_event_id"}
    for node in fixture_nodes:
        if node.base_label != "Entity" or not node.properties.get("ontology_instance"):
            continue
        extra = set(node.properties) - reserved - declared
        # Whatever remains must be run provenance, which every node carries.
        assert all(k.startswith(("graph_", "extraction_", "ontology_")) for k in extra), extra
