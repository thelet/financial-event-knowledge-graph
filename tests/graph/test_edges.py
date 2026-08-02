"""The edge projection: names, directions and refusals, checked against the vocabulary.

Every assertion here is either a count measured from the committed fixture / the real run,
or a rule read back out of `relationships.yaml` through the registry. Nothing asserts a
hard-coded edge list, because the module under test is not allowed to hold one either.

Two corpora, per `conftest`: the fixture runs from a clean checkout, the real run's numbers
skip when `data/` is absent.
"""

from __future__ import annotations

import pytest

from conftest import REAL_RUN_AVAILABLE, REAL_RUN_REASON
from graph.core.inputs import (
    ClaimRow,
    EventRow,
    EvidenceRow,
    ExtractionRunInputs,
    Participant,
    RelationshipRow,
)
from graph.core.citations import cited_passage_ids
from graph.core.keys import PlaceholderAttributionError, entity_node_key
from graph.core.models import EDGE_SORT_KEY, GraphEdge
from graph.stages.projection.edges import (
    CONCERNS_METRIC,
    DISTINCT_FROM,
    EVENT_ENDPOINT_TYPE,
    EVIDENCED_BY,
    FOUND_IN,
    HAS_OBSERVATION,
    LOCAL_ENDPOINT_TYPE,
    METRIC_ENDPOINT_TYPE,
    OBSERVATION_ENDPOINT_TYPE,
    OBSERVATION_OF_SUBJECT,
    ONTOLOGY_DECLARED,
    ONTOLOGY_DEFINITION_ASSERTION,
    PARTICIPATES_IN,
    PART_OF,
    PLACEHOLDER_FOR,
    PROJECTION_LOCAL_EDGE_TYPES,
    RECONCILES_TO,
    EdgeVocabulary,
    Endpoint,
    EndpointTypeNotAllowedError,
    MalformedEdgePropertyError,
    UndeclaredEndpointTypeError,
    UndeclaredPredicateError,
    build_edges,
    edge_counts,
)
from graph.stages.projection.nodes import build_nodes

requires_real_run = pytest.mark.skipif(not REAL_RUN_AVAILABLE, reason=REAL_RUN_REASON)

#: What a composition root supplies (§5.5). Fixed values, because two builds must be equal.
PROVENANCE = {
    "extraction_run_id": "extract-v1-lexical-2422c4252c07",
    "graph_run_id": "graph-v1-test",
    "graph_projection_version": "1.0.0",
    "ontology_id": "real_estate_marketplace_v1",
    "ontology_version": "1.0.0",
    "ontology_definition_hash": "e8d4af709be2",
    "extraction_code_commit": "4d3ae1e",
    "graph_code_commit": "b1d55f4",
}

#: Counts measured from `tests/fixtures/graph/` *(2026-08-02)*. 30 observations, 5 events
#: with 9 participants, 4 relationship claims, 45 issues, 6 cited passages.
FIXTURE_COUNTS = {
    "BORROWS_UNDER": 1,
    "CONCERNS_METRIC": 29,
    "DISTINCT_FROM": 36,
    "EVIDENCED_BY": 35,
    "FOUND_IN": 45,
    "HAS_OBSERVATION": 30,
    "HOLDS_POSITION_AT": 3,
    "OBSERVATION_OF_SUBJECT": 30,
    "PARTICIPATES_IN": 9,
    "PART_OF": 6,
    "PLACEHOLDER_FOR": 1,
    "RECONCILES_TO": 2,
}

#: Counts measured from `extract-v1-lexical-2422c4252c07` *(2026-08-02)*.
#:
#: `EVIDENCED_BY` is 2,713 = 2,707 observations + 6 events; the 4 relationship evidence rows
#: are **not** edges — Neo4j has no edge-on-edge, so a relationship's evidence lives in the
#: edge's own `quoted_text` (§3.1).
#:
#: `PARTICIPATES_IN` is **10**, not the 11 the commissioning brief expected: the run's six
#: events carry 2+2+2+2+1+1 participants *(counted from `events.jsonl`)*.
REAL_COUNTS = {
    "BORROWS_UNDER": 1,
    "CONCERNS_METRIC": 1520,
    "DISTINCT_FROM": 36,
    "EVIDENCED_BY": 2713,
    "FOUND_IN": 17127,
    "HAS_OBSERVATION": 2707,
    "HOLDS_POSITION_AT": 3,
    "OBSERVATION_OF_SUBJECT": 2707,
    "PARTICIPATES_IN": 10,
    "PART_OF": 8776,
    "PLACEHOLDER_FOR": 1,
    "RECONCILES_TO": 2,
}

#: §3.2: declared but deliberately not emitted in V1, each for its own reason.
NOT_EMITTED = ("REPORTED_IN", "SUPERSEDES", "COMPUTED_FROM", "USES_FORMULA_VERSION")

#: §4.2 / §10 criterion 7: the edges that would say *who* an unresolved entity is.
IDENTITY_ASSERTING = ("SUBSIDIARY_OF", "HAS_SUBSIDIARY", "ACQUIRED", "PARTY_TO")

#: The five endpoint names `registry.concept()` raises on (§3.2's wrinkle).
STRUCTURAL_ENDPOINT_NAMES = (
    "metric_definition", "metric_observation", "event_type", "relationship_type",
    "metric_formula",
)

#: How a declared endpoint type name maps onto a base label, used to check direction
#: independently of the module under test.
_EVIDENCE_ENDPOINT_NAMES = frozenset(
    {"normalized_passage", "normalized_table", "xbrl_fact", "filing_metadata",
     "external_page"})


def _base_label_of(type_name: str) -> str:
    if type_name == METRIC_ENDPOINT_TYPE:
        return "Metric"
    if type_name == OBSERVATION_ENDPOINT_TYPE:
        return "Observation"
    if type_name == EVENT_ENDPOINT_TYPE:
        return "Event"
    if type_name in _EVIDENCE_ENDPOINT_NAMES:
        return "Passage"
    return "Entity"


@pytest.fixture(scope="session")
def fixture_edges(fixture_inputs, ontology) -> tuple[GraphEdge, ...]:
    return build_edges(fixture_inputs, ontology=ontology, provenance=PROVENANCE)


@pytest.fixture(scope="session")
def real_edges(real_inputs, ontology) -> tuple[GraphEdge, ...]:
    return build_edges(real_inputs, ontology=ontology, provenance=PROVENANCE)


@pytest.fixture(scope="session")
def vocabulary(ontology) -> EdgeVocabulary:
    return EdgeVocabulary(ontology.registry)


def _of_type(edges, edge_type: str) -> tuple[GraphEdge, ...]:
    return tuple(edge for edge in edges if edge.type == edge_type)


# -- counts ------------------------------------------------------------------------------


def test_fixture_edge_counts_by_type(fixture_edges):
    assert edge_counts(fixture_edges) == FIXTURE_COUNTS


@requires_real_run
def test_real_run_edge_counts_by_type(real_edges):
    assert edge_counts(real_edges) == REAL_COUNTS


@requires_real_run
def test_real_run_evidenced_by_covers_every_observation_and_event(real_inputs, real_edges):
    """§10 criterion 2: no `:Observation` or `:Event` without an `EVIDENCED_BY`."""
    sources = {edge.source_key for edge in _of_type(real_edges, EVIDENCED_BY)}
    assert {row.observation_id for row in real_inputs.observations} <= sources
    assert {row.event_id for row in real_inputs.events} <= sources
    assert len(sources) == len(real_inputs.observations) + len(real_inputs.events)


@requires_real_run
def test_real_run_participant_count_is_ten_not_eleven(real_inputs, real_edges):
    """Measured, not assumed: the brief expected 11, `events.jsonl` holds 10."""
    assert sum(len(event.participants) for event in real_inputs.events) == 10
    assert len(_of_type(real_edges, PARTICIPATES_IN)) == 10


def test_part_of_covers_every_cited_passage(fixture_inputs, fixture_edges):
    """§3.1: a `:Passage` node exists for every passage a claim or issue cites."""
    cited = cited_passage_ids(fixture_inputs)
    assert len(cited) == 6
    assert {edge.source_key for edge in _of_type(fixture_edges, PART_OF)} == set(cited)
    for edge in _of_type(fixture_edges, PART_OF):
        # §4.1: the document id is the passage id without its `#p{n}` suffix.
        assert edge.source_key.startswith(edge.target_key + "#p")


@requires_real_run
def test_real_run_cited_passages_and_documents_match_the_plan(real_inputs, real_edges):
    """§3.1 measured 8,776 passages over 185 documents. Both fall out of `PART_OF`."""
    assert len(cited_passage_ids(real_inputs)) == 8776
    part_of = _of_type(real_edges, PART_OF)
    assert len({edge.target_key for edge in part_of}) == 185


def test_edges_are_returned_unsorted_but_every_key_is_unique(fixture_edges):
    """Both halves asserted. The name claimed "unsorted" and only checked uniqueness.

    Unsortedness is not cosmetic: `GraphExport.sorted()` owns §4.4's declared order, and a
    builder that happened to emit sorted output would let a second owner of that order creep
    in unnoticed. The builder emits in *build* order — observations, events, relationships,
    the vocabulary's own edges, then the projection-local ones — which is not
    `EDGE_SORT_KEY` order, and this is where that stays true.
    """
    keys = [edge.edge_key for edge in fixture_edges]
    assert len(set(keys)) == len(keys)

    order = [EDGE_SORT_KEY(edge) for edge in fixture_edges]
    assert order != sorted(order), "the builder is emitting sorted edges; §4.4's order has "\
                                   "two owners again"
    assert sorted(order) == sorted(EDGE_SORT_KEY(edge) for edge in fixture_edges)


def test_predicates_the_plan_excludes_are_absent(fixture_edges, real_edges_if_available):
    for edges in (fixture_edges, real_edges_if_available):
        if edges is None:
            continue
        present = {edge.type for edge in edges}
        assert present.isdisjoint(NOT_EMITTED)


@pytest.fixture(scope="session")
def real_edges_if_available(request):
    if not REAL_RUN_AVAILABLE:
        return None
    return request.getfixturevalue("real_edges")


# -- rule 1: endpoint types validated against the registry --------------------------------


def test_the_five_structural_endpoint_names_resolve_rather_than_raising(
    ontology, vocabulary
):
    """§3.2's wrinkle, and the one place the plan's wording is slightly off.

    All five raise from `registry.concept(...)`. Four are `ConceptCategory` names; the fifth,
    `metric_observation`, is a **`ClaimKind`** value and belongs to no category — so a
    validator that resolved only against `ConceptCategory` would still reject
    `HAS_OBSERVATION`, `OBSERVATION_OF_SUBJECT` and `EVIDENCED_BY`.
    """
    from ontology.core.errors import ConceptNotFoundError
    from ontology.core.values import ConceptCategory

    for name in STRUCTURAL_ENDPOINT_NAMES:
        with pytest.raises(ConceptNotFoundError):
            ontology.registry.concept(name)
        vocabulary.resolve_endpoint_type(name)  # must not raise

    categories = {str(value) for value in ConceptCategory}
    assert "metric_observation" not in categories
    assert set(STRUCTURAL_ENDPOINT_NAMES) - {"metric_observation"} <= categories


def test_every_endpoint_name_in_the_vocabulary_resolves(ontology, vocabulary):
    """No declared predicate names a type the projection cannot resolve at all."""
    for relationship_id in vocabulary.predicate_ids:
        definition = vocabulary.predicate(relationship_id)
        for name in (*definition.allowed_source_types, *definition.allowed_target_types):
            vocabulary.resolve_endpoint_type(name)


def test_an_unknown_endpoint_type_is_refused(vocabulary):
    with pytest.raises(UndeclaredEndpointTypeError):
        vocabulary.resolve_endpoint_type("unicorn_type")
    with pytest.raises(UndeclaredEndpointTypeError):
        vocabulary.resolve_endpoint_type(LOCAL_ENDPOINT_TYPE)


def test_an_undeclared_predicate_is_refused(vocabulary):
    with pytest.raises(UndeclaredPredicateError):
        vocabulary.predicate("OBSERVES_METRIC")


def test_declared_edge_directions_match_the_registry(fixture_edges, vocabulary):
    """Direction is read back out of `relationships.yaml`, not asserted from a table here."""
    declared = [edge for edge in fixture_edges if edge.properties[ONTOLOGY_DECLARED]]
    assert declared
    for edge in declared:
        definition = vocabulary.predicate(edge.type)
        sources = {_base_label_of(name) for name in definition.allowed_source_types}
        targets = {_base_label_of(name) for name in definition.allowed_target_types}
        assert edge.source_base_label in sources, edge
        assert edge.target_base_label in targets, edge


def test_a_hand_built_reversed_edge_fails(vocabulary):
    """`HAS_OBSERVATION` is `metric_definition -> metric_observation`, one way only."""
    metric = Endpoint(key="adjusted_ebitda", base_label="Metric",
                      type_name=METRIC_ENDPOINT_TYPE)
    observation = Endpoint(key="obs:x", base_label="Observation",
                           type_name=OBSERVATION_ENDPOINT_TYPE)
    vocabulary.check(HAS_OBSERVATION, metric, observation)
    with pytest.raises(EndpointTypeNotAllowedError):
        vocabulary.check(HAS_OBSERVATION, observation, metric)


@pytest.mark.parametrize(
    "edge_type, source_type, target_type",
    [
        (OBSERVATION_OF_SUBJECT, OBSERVATION_ENDPOINT_TYPE, "public_company"),
        (EVIDENCED_BY, OBSERVATION_ENDPOINT_TYPE, "normalized_table"),
        (PARTICIPATES_IN, "person", EVENT_ENDPOINT_TYPE),
        ("HOLDS_POSITION_AT", "person", "public_company"),
        ("BORROWS_UNDER", "subsidiary", "asset_backed_debt_facility"),
    ],
)
def test_reversing_any_asymmetric_predicate_fails(
    vocabulary, edge_type, source_type, target_type
):
    source = Endpoint(key="a", base_label="Entity", type_name=source_type)
    target = Endpoint(key="b", base_label="Entity", type_name=target_type)
    vocabulary.check(edge_type, source, target)
    with pytest.raises(EndpointTypeNotAllowedError):
        vocabulary.check(edge_type, target, source)


def test_participates_in_accepts_a_subtype_of_a_declared_endpoint(
    ontology, vocabulary, fixture_edges
):
    """The run's facility participant is an `asset_backed_debt_facility`.

    `PARTICIPATES_IN.allowed_source_types` lists `credit_facility` and not that subtype, so
    exact membership — the rule `ontology/core/constraints.py:726` applies to relationship
    *claims* — would drop a filed participant. Endpoint checking widens through `is_a`.
    """
    definition = vocabulary.predicate(PARTICIPATES_IN)
    assert "asset_backed_debt_facility" not in definition.allowed_source_types
    assert "credit_facility" in definition.allowed_source_types
    assert "credit_facility" in ontology.registry.ancestors("asset_backed_debt_facility")

    facility = [edge for edge in _of_type(fixture_edges, PARTICIPATES_IN)
                if edge.source_key == "asset_backed_senior_revolving_credit_facility"]
    assert len(facility) == 1
    assert facility[0].properties["role"] == "facility"


def test_an_entity_type_the_registry_does_not_know_is_refused(fixture_inputs, ontology):
    inputs = _synthetic_relationship_inputs(
        fixture_inputs, events=("one",), source_type="unicorn")
    with pytest.raises(UndeclaredEndpointTypeError):
        build_edges(inputs, ontology=ontology, provenance=PROVENANCE)


# -- rule 2: unresolved endpoints get no identity-asserting edge --------------------------


PLACEHOLDER_ID = "opendoor_unnamed_subsidiary"
PLACEHOLDER_EVENT = "evt:credit-facility-established:2022-10-19:4b384d2ba0fa"
PLACEHOLDER_KEY = f"{PLACEHOLDER_ID}#{PLACEHOLDER_EVENT}"


def test_placeholder_uses_the_per_event_key_everywhere_it_appears(fixture_edges):
    """§4.2: the unnamed borrower and the `BORROWS_UNDER` source are one node."""
    assert entity_node_key(PLACEHOLDER_ID, event_id=PLACEHOLDER_EVENT) == PLACEHOLDER_KEY

    participates = [edge for edge in _of_type(fixture_edges, PARTICIPATES_IN)
                    if edge.source_key.startswith(PLACEHOLDER_ID)]
    borrows = _of_type(fixture_edges, "BORROWS_UNDER")
    placeholder_for = _of_type(fixture_edges, PLACEHOLDER_FOR)

    assert [edge.source_key for edge in participates] == [PLACEHOLDER_KEY]
    assert [edge.source_key for edge in borrows] == [PLACEHOLDER_KEY]
    assert [edge.source_key for edge in placeholder_for] == [PLACEHOLDER_KEY]
    # The bare extraction id is never a key on its own (§4.2's whole point).
    assert not any(edge.source_key == PLACEHOLDER_ID or edge.target_key == PLACEHOLDER_ID
                   for edge in fixture_edges)


def test_placeholder_for_points_at_the_parent_and_asserts_nothing_else(fixture_edges):
    edge, = _of_type(fixture_edges, PLACEHOLDER_FOR)
    assert edge.target_key == "opendoor"
    assert edge.properties["extraction_entity_id"] == PLACEHOLDER_ID
    assert edge.properties["event_id"] == PLACEHOLDER_EVENT
    assert edge.properties["resolved"] is False
    assert edge.properties[ONTOLOGY_DECLARED] is False


def test_no_identity_asserting_edge_touches_the_placeholder(fixture_edges):
    """§10 criterion 7, expressed over the edges rather than over Cypher."""
    touching = {edge.type for edge in fixture_edges
                if PLACEHOLDER_KEY in (edge.source_key, edge.target_key)}
    assert touching == {PLACEHOLDER_FOR, PARTICIPATES_IN, "BORROWS_UNDER"}
    assert touching.isdisjoint(IDENTITY_ASSERTING)


def test_a_placeholder_attributable_to_no_event_raises(fixture_inputs, ontology):
    inputs = _synthetic_relationship_inputs(fixture_inputs, events=())
    with pytest.raises(PlaceholderAttributionError):
        build_edges(inputs, ontology=ontology, provenance=PROVENANCE)


def test_a_placeholder_attributable_to_two_events_raises(fixture_inputs, ontology):
    """Guessing here would attach a borrowing obligation to whichever event sorted first."""
    inputs = _synthetic_relationship_inputs(fixture_inputs, events=("one", "two"))
    with pytest.raises(PlaceholderAttributionError):
        build_edges(inputs, ontology=ontology, provenance=PROVENANCE)


def test_a_placeholder_attributable_to_exactly_one_event_resolves(fixture_inputs, ontology):
    inputs = _synthetic_relationship_inputs(fixture_inputs, events=("one",))
    edges = build_edges(inputs, ontology=ontology, provenance=PROVENANCE)
    borrows, = _of_type(edges, "BORROWS_UNDER")
    assert borrows.source_key == f"{PLACEHOLDER_ID}#evt:synthetic:one"


# -- rule 3: relationship identity is the supplied instance id ----------------------------


def test_relationship_edges_are_keyed_on_the_instance_id(fixture_inputs, fixture_edges):
    by_key = {edge.edge_key: edge for edge in fixture_edges}
    for row in fixture_inputs.relationships:
        edge = by_key[row.relationship_instance_id]
        assert edge.type == row.relationship_id
        assert edge.properties["claim_id"] == row.claim_id
        assert edge.properties["relationship_instance_id"] == row.relationship_instance_id


def test_two_filings_asserting_one_predicate_between_one_pair_stay_two_edges(
    fixture_inputs, ontology
):
    """§4.1: collapsing them on the endpoint pair would discard a citation."""
    inputs = _synthetic_relationship_inputs(
        fixture_inputs, events=("one",), instance_ids=("rel:a", "rel:b"))
    edges = build_edges(inputs, ontology=ontology, provenance=PROVENANCE)
    borrows = _of_type(edges, "BORROWS_UNDER")
    assert len(borrows) == 2
    assert {edge.edge_key for edge in borrows} == {"rel:a", "rel:b"}
    assert len({(edge.source_key, edge.target_key) for edge in borrows}) == 1


def test_a_relationship_edge_carries_its_quoted_text(fixture_edges):
    """Neo4j has no edge-on-edge, so an edge's evidence lives in its properties (§3.1)."""
    for edge in _of_type(fixture_edges, "BORROWS_UNDER") + _of_type(
            fixture_edges, "HOLDS_POSITION_AT"):
        assert edge.properties["quoted_text"].strip()


# -- rule 4: no issue-based suppression ---------------------------------------------------


def test_an_event_whose_passage_carries_a_refusal_still_gets_its_edges(
    fixture_inputs, fixture_edges
):
    """Handoff §4.2: a refusal is per-reading or per-property, never per-node."""
    codes = {issue.code for issue in fixture_inputs.issues}
    assert {"PARTICIPANT_NOT_NAMED", "PROPERTY_VALUE_NOT_IN_PASSAGE"} <= codes

    participates = {edge.target_key for edge in _of_type(fixture_edges, PARTICIPATES_IN)}
    evidenced = {edge.source_key for edge in _of_type(fixture_edges, EVIDENCED_BY)}
    for event in fixture_inputs.events:
        assert event.event_id in participates
        assert event.event_id in evidenced


def test_every_observation_keeps_its_edges_regardless_of_issues(
    fixture_inputs, fixture_edges
):
    observed = {edge.target_key for edge in _of_type(fixture_edges, HAS_OBSERVATION)}
    subjects = {edge.source_key for edge in _of_type(fixture_edges, OBSERVATION_OF_SUBJECT)}
    ids = {row.observation_id for row in fixture_inputs.observations}
    assert observed == ids
    assert subjects == ids


def test_issue_edges_never_reach_the_fact_graph(fixture_inputs, fixture_edges):
    """§3.3 point 3: an abstention is reachable by nothing that follows evidence."""
    issue_keys = {row.issue_id for row in fixture_inputs.issues}
    for edge in fixture_edges:
        if edge.source_key in issue_keys:
            assert edge.type in (FOUND_IN, CONCERNS_METRIC)
        assert edge.target_key not in issue_keys


def test_concerns_metric_only_names_declared_metrics(fixture_inputs, fixture_edges, ontology):
    from ontology.core.models import MetricDefinition

    issues = {row.issue_id: row for row in fixture_inputs.issues}
    concerns = _of_type(fixture_edges, CONCERNS_METRIC)
    assert concerns
    for edge in concerns:
        assert isinstance(ontology.registry.find(edge.target_key), MetricDefinition)
        # Neither endpoint is the passage, so the location is carried as a property (Q9).
        assert edge.properties["passage_id"] == issues[edge.source_key].passage_id
        assert edge.properties["document_id"] == issues[edge.source_key].document_id

    # Undeclared candidates are left off, not silently promoted.
    named = {cid for row in fixture_inputs.issues for cid in row.concept_ids}
    undeclared = {cid for cid in named
                  if not isinstance(ontology.registry.find(cid), MetricDefinition)}
    assert undeclared.isdisjoint({edge.target_key for edge in concerns})


def test_found_in_covers_every_issue(fixture_inputs, fixture_edges):
    found = _of_type(fixture_edges, FOUND_IN)
    assert {edge.source_key for edge in found} == {
        row.issue_id for row in fixture_inputs.issues}
    for edge in found:
        assert edge.target_base_label == "Passage"


# -- rule 4b: the two stages agree about which passages exist -----------------------------


def test_a_rejection_only_passage_gets_both_its_node_and_its_part_of_edge(
    fixture_inputs, ontology
):
    """R7: one `cited_passage_ids`, so a node and its `PART_OF` can no longer disagree.

    Before `graph.core.citations`, the node builder's union included
    `rejected_claims.jsonl` and the edge builder's did not — so a passage cited *only* by a
    rejection got a `:Passage` node with no `PART_OF` edge to its document, an orphan in the
    one direction §3.1 says a passage must be reachable from. Zero occurrences on the real
    run (all four unions are the same 8,776 passages), which is exactly why it needed a
    constructed case.

    The same run also carries an `assemble`-origin rejection, which `issues.jsonl` never
    mirrors — R8's case — so the two are asserted together.
    """
    passage = fixture_inputs.passages[0]
    rejection = fixture_inputs.rejected_claims[0].model_copy(update={
        "rejection_id": "rej:ffffffffffff", "refused_by": "assemble", "lane": "assemble",
        "raw_finding": None, "passage_id": passage.passage_id,
        "document_id": passage.document_id})
    inputs = ExtractionRunInputs.from_rows(
        manifest=fixture_inputs.manifest, rejected_claims=(rejection,),
        passages=fixture_inputs.passages, documents=fixture_inputs.documents)

    assert inputs.rejection_issue_join.unmirrored == (rejection,)
    assert cited_passage_ids(inputs) == (passage.passage_id,)

    nodes = build_nodes(inputs, ontology=ontology, provenance=PROVENANCE)
    edges = build_edges(inputs, ontology=ontology, provenance=PROVENANCE)

    passages = [node for node in nodes if node.base_label == "Passage"]
    assert [node.key for node in passages] == [passage.passage_id]
    part_of = _of_type(edges, PART_OF)
    assert [edge.source_key for edge in part_of] == [passage.passage_id]
    assert part_of[0].target_key == passage.document_id
    # And the document node the edge points at exists too.
    assert passage.document_id in {node.key for node in nodes
                                   if node.base_label == "Document"}

    # R8: the unmirrored rejection is an `:Issue:Rejected` node keyed on its rejection_id,
    # reachable from its passage by the same `FOUND_IN` every other issue gets.
    issues = [node for node in nodes if node.base_label == "Issue"]
    assert [node.key for node in issues] == ["rej:ffffffffffff"]
    assert issues[0].labels == ("Issue", "Rejected")
    assert issues[0].properties["refused_by"] == "assemble"
    # No `issue_id`: there is no issue row, and inventing one would erase §2.2 P10's rule
    # that the two files are never summed.
    assert "issue_id" not in issues[0].properties
    found_in = _of_type(edges, FOUND_IN)
    assert [(edge.source_key, edge.target_key) for edge in found_in] == [
        ("rej:ffffffffffff", passage.passage_id)]


def test_an_empty_passage_citation_is_refused_by_both_stages(fixture_inputs, ontology):
    """One empty-string policy, and it is the strict one (§2.3 trap 2, §10 criterion 4).

    The node builder raised and the edge builder silently skipped, so the same malformed row
    either stopped the run or quietly removed a citation depending on which stage saw it.
    """
    from graph.core.citations import EmptyPassageCitationError

    blank = fixture_inputs.issues[0].model_copy(update={"passage_id": "  "})
    inputs = ExtractionRunInputs.from_rows(
        manifest=fixture_inputs.manifest, issues=(blank,),
        passages=fixture_inputs.passages, documents=fixture_inputs.documents)
    for build in (build_nodes, build_edges):
        with pytest.raises(EmptyPassageCitationError, match="empty passage_id"):
            build(inputs, ontology=ontology, provenance=PROVENANCE)


# -- rule 5: provenance on every edge -----------------------------------------------------


def test_every_edge_carries_the_run_provenance(fixture_edges):
    for edge in fixture_edges:
        for key, value in PROVENANCE.items():
            assert edge.properties[key] == value
        assert isinstance(edge.properties[ONTOLOGY_DECLARED], bool)


def test_fact_bearing_edges_carry_claim_passage_document_and_assertion(fixture_edges):
    """§10 criterion 4, as amended: the requirement is on **claim-sourced** facts.

    The criterion originally demanded a non-null `claim_id` and a resolvable `passage_id` on
    *every* ontology-declared edge. The 36 `DISTINCT_FROM` and 2 `RECONCILES_TO` edges are
    ontology-declared and come from `metrics.yaml`, not from a filing — no claim asserted
    them and no passage evidences them — so the criterion was contradicted by a design that
    is right. It now scopes the claim/passage requirement to claim-sourced facts and requires
    `assertion: ontology_definition` on the vocabulary-sourced ones instead.

    This test asserts **both** branches rather than skipping the second one, which is how the
    contradiction survived a green suite.
    """
    ontology_sourced = {RECONCILES_TO, DISTINCT_FROM}
    claim_sourced = vocabulary_sourced = 0
    for edge in fixture_edges:
        if not edge.properties[ONTOLOGY_DECLARED]:
            continue
        if edge.type in ontology_sourced:
            vocabulary_sourced += 1
            assert edge.properties["assertion"] == ONTOLOGY_DEFINITION_ASSERTION
            for absent in ("claim_id", "passage_id", "document_id", "assertion_type"):
                assert absent not in edge.properties, (edge.type, absent)
            continue
        claim_sourced += 1
        for field in ("claim_id", "passage_id", "document_id", "assertion_type"):
            assert edge.properties[field].strip(), (edge.type, field)
        # An edge may not claim both provenances: `assertion` says "the vocabulary said so".
        assert "assertion" not in edge.properties, edge.type
    assert vocabulary_sourced == 38  # 36 DISTINCT_FROM + 2 RECONCILES_TO
    assert claim_sourced


def test_evidenced_by_carries_the_filed_evidence_not_only_its_address(
    fixture_inputs, fixture_edges
):
    """§1.5 / §9: an `EVIDENCED_BY` edge must hold what was quoted, not just where.

    `evidence.jsonl` carries `quoted_text` (non-empty on all 2,717 rows), `table_id` (2,690)
    and `block_ids` (2,717). The edge used to emit `evidence_kind`, `source_url` and
    `passage_id` only, so an `:Observation` — whose node holds no quotation either — lost the
    filed sentence entirely, while an `:Event` kept its own `evidence_quoted_text` and a
    relationship kept `quoted_text` on its edge. Same fact class, three different answers.
    """
    by_claim = {row.claim_id: row for row in fixture_inputs.evidence}
    edges = _of_type(fixture_edges, EVIDENCED_BY)
    assert edges
    for edge in edges:
        row = by_claim[edge.properties["claim_id"]]
        assert edge.properties["quoted_text"] == row.quoted_text
        assert edge.properties["quoted_text"].strip()
        assert edge.properties["block_ids"] == list(row.block_ids)
        assert edge.properties.get("table_id") == row.table_id


@requires_real_run
def test_every_real_evidenced_by_edge_carries_its_quoted_text(real_inputs, real_edges):
    """All 2,713, measured — `quoted_text` is non-empty on all 2,717 evidence rows."""
    edges = _of_type(real_edges, EVIDENCED_BY)
    assert len(edges) == 2713
    assert all(edge.properties["quoted_text"].strip() for edge in edges)
    with_table = [edge for edge in edges if "table_id" in edge.properties]
    # 2,690 of 2,717 evidence rows carry a table id; 4 of them belong to relationship
    # claims, which get no `EVIDENCED_BY` edge at all (§3.1's edge-on-edge note).
    assert len(with_table) == len(
        [row for row in real_inputs.evidence
         if row.table_id is not None
         and row.claim_id not in {r.claim_id for r in real_inputs.relationships}])


def test_relationship_edges_carry_lane_and_both_endpoint_types(
    fixture_inputs, fixture_edges
):
    """§2.3 trap 1 on the third payload catalog: `lane` is carried, like everywhere else.

    `relationships.jsonl` has `lane` on all 4 rows and `source_type` / `target_type` on all
    4; observations and events carried theirs and relationships carried none of the three.
    `inputs.py` states both vocabularies are always carried, so this was the module
    contradicting the reader's own docstring.
    """
    by_key = {edge.edge_key: edge for edge in fixture_edges}
    assert fixture_inputs.relationships
    for row in fixture_inputs.relationships:
        edge = by_key[row.relationship_instance_id]
        assert edge.properties["lane"] == row.lane
        assert edge.properties["source_type"] == row.source_type
        assert edge.properties["target_type"] == row.target_type
        # `lane` is the routing vocabulary and `assertion_type` the ontology's; both present,
        # neither mapped onto the other.
        assert edge.properties["assertion_type"] == row.assertion_type


def test_projection_local_edges_are_marked_and_are_exactly_four(fixture_edges):
    local = {edge.type for edge in fixture_edges
             if edge.properties[ONTOLOGY_DECLARED] is False}
    assert local == set(PROJECTION_LOCAL_EDGE_TYPES)
    declared = {edge.type for edge in fixture_edges
                if edge.properties[ONTOLOGY_DECLARED] is True}
    assert declared.isdisjoint(local)


def test_evidenced_by_records_the_kind_that_cited_the_passage(fixture_inputs, fixture_edges):
    kinds = {row.claim_id: row.evidence_kind for row in fixture_inputs.evidence}
    for edge in _of_type(fixture_edges, EVIDENCED_BY):
        assert edge.properties["evidence_kind"] == kinds[edge.properties["claim_id"]]


# -- the two edges that come from the vocabulary ------------------------------------------


def test_distinct_from_and_reconciles_to_come_from_the_ontology(fixture_edges, ontology):
    from ontology.core.models import MetricDefinition
    from ontology.core.values import ConceptCategory

    metrics = [c for c in ontology.registry.by_category(
        str(ConceptCategory.METRIC_DEFINITION)) if isinstance(c, MetricDefinition)]
    declared_distinct = {(m.concept_id, other) for m in metrics for other in m.distinct_from}
    declared_reconciles = {(m.concept_id, m.reconciles_to)
                           for m in metrics if m.reconciles_to}

    assert {(e.source_key, e.target_key)
            for e in _of_type(fixture_edges, DISTINCT_FROM)} == declared_distinct
    assert {(e.source_key, e.target_key)
            for e in _of_type(fixture_edges, RECONCILES_TO)} == declared_reconciles
    assert len(declared_distinct) == 36
    assert len(declared_reconciles) == 2


def test_ontology_sourced_edges_carry_the_ontology_definition_assertion(fixture_edges):
    """§3.3 point 2: these came from the vocabulary, not from a filing."""
    edges = _of_type(fixture_edges, DISTINCT_FROM) + _of_type(fixture_edges, RECONCILES_TO)
    assert edges
    for edge in edges:
        assert edge.properties["assertion"] == ONTOLOGY_DEFINITION_ASSERTION
        assert edge.properties[ONTOLOGY_DECLARED] is True
        assert "claim_id" not in edge.properties
        assert "assertion_type" not in edge.properties
        assert edge.source_base_label == edge.target_base_label == "Metric"


@requires_real_run
def test_ontology_sourced_edge_counts_do_not_depend_on_the_run(fixture_edges, real_edges):
    for edge_type in (DISTINCT_FROM, RECONCILES_TO):
        assert len(_of_type(fixture_edges, edge_type)) == len(
            _of_type(real_edges, edge_type))


# -- rule 6: determinism ------------------------------------------------------------------


def test_two_builds_of_one_run_are_equal(fixture_inputs, ontology):
    first = build_edges(fixture_inputs, ontology=ontology, provenance=PROVENANCE)
    second = build_edges(fixture_inputs, ontology=ontology, provenance=PROVENANCE)
    assert first == second
    assert [edge.edge_key for edge in first] == [edge.edge_key for edge in second]


#: Every module that contributes to `nodes.jsonl` / `edges.jsonl`. The grep used to cover
#: `edges.py` alone, which is one of five places a clock could have reached the artifacts —
#: `nodes.py` builds half of them, `export.py` renders both, and `keys.py` / `derivation.py`
#: compute values that land in them. `manifest.py` is deliberately **not** here: it owns the
#: only clock in the layer (§4.4), and the artifacts it writes are not the artifacts under
#: this rule.
DETERMINISM_CRITICAL_MODULES = (
    "graph.stages.projection.edges",
    "graph.stages.projection.nodes",
    "graph.stages.projection.export",
    "graph.core.keys",
    "graph.core.derivation",
    "graph.core.citations",
)

FORBIDDEN_SOURCES = ("import random", "import uuid", "import time", "from datetime",
                     "import datetime", "import neo4j", "datetime.now", "time.time",
                     "uuid4", "os.urandom")


@pytest.mark.parametrize("dotted", DETERMINISM_CRITICAL_MODULES)
def test_the_module_imports_no_clock_or_random_source(dotted):
    """§4.4 made executable rather than documented, over every module that can break it."""
    import importlib

    module = importlib.import_module(dotted)
    source = open(module.__file__, encoding="utf-8").read()
    for forbidden in FORBIDDEN_SOURCES:
        assert forbidden not in source, f"{dotted}: {forbidden}"


# -- rule 7: Neo4j property rules ---------------------------------------------------------


def test_no_edge_property_is_a_map_or_a_heterogeneous_list(fixture_edges):
    for edge in fixture_edges:
        for name, value in edge.properties.items():
            assert value is not None, name
            if isinstance(value, list):
                assert len({type(item) for item in value}) <= 1
                assert all(isinstance(item, (str, int, float, bool)) for item in value)
            else:
                assert isinstance(value, (str, int, float, bool)), (name, type(value))


def test_a_nested_provenance_value_is_refused(fixture_inputs, ontology):
    with pytest.raises(MalformedEdgePropertyError):
        build_edges(fixture_inputs, ontology=ontology,
                    provenance={**PROVENANCE, "environment": {"python": "3.13"}})


def test_a_heterogeneous_provenance_list_is_refused(fixture_inputs, ontology):
    with pytest.raises(MalformedEdgePropertyError):
        build_edges(fixture_inputs, ontology=ontology,
                    provenance={**PROVENANCE, "lanes": ["tables", 3]})


# -- synthetic inputs ---------------------------------------------------------------------


def _synthetic_relationship_inputs(
    fixture_inputs: ExtractionRunInputs,
    *,
    events: tuple[str, ...],
    source_type: str = "subsidiary",
    instance_ids: tuple[str, ...] = ("rel:synthetic",),
) -> ExtractionRunInputs:
    """A minimal run: one passage, `len(events)` events, `len(instance_ids)` relationships.

    Built from typed rows rather than from a temporary directory so the placeholder-
    attribution cases — zero events, two events — can exist at all: neither shape occurs in
    the real corpus, and a fixture edited to contain one would no longer be a real slice.
    The manifest is the fixture's own, unmodified.
    """
    passage = "norm:0001801169:0001801169-99-000001:synthetic.htm#p1"
    document = "norm:0001801169:0001801169-99-000001:synthetic.htm"

    claims: list[ClaimRow] = []
    evidence: list[EvidenceRow] = []
    event_rows: list[EventRow] = []
    relationship_rows: list[RelationshipRow] = []

    def _anchor(claim_id: str, kind: str, payload_id: str) -> None:
        claims.append(ClaimRow(
            claim_id=claim_id, claim_kind=kind, payload_id=payload_id, lane="events",
            passage_id=passage, document_id=document, document_type="narrative_primary",
            assertion_type="reported", confidence=None, extractor_metadata={}))
        evidence.append(EvidenceRow(
            claim_id=claim_id, claim_kind=kind, evidence_index=0,
            evidence_kind="normalized_passage", passage_id=passage, document_id=document,
            table_id=None, block_ids=("b1",), source_url="https://example.invalid/f.htm",
            quoted_text="a subsidiary of the Company entered into a facility"))

    for suffix in events:
        event_id = f"evt:synthetic:{suffix}"
        claim_id = f"claim:event:{suffix}"
        _anchor(claim_id, "event", event_id)
        event_rows.append(EventRow(
            event_id=event_id, claim_id=claim_id,
            event_type_id="credit_facility_established", occurred_on="2022-10-19",
            announced_on=None, occurrence_date_text=None, announcement_date_text=None,
            dates_equal=False, review_flag=None, evidence_quoted_text=None,
            participants=(Participant(
                role="borrower", entity_id=PLACEHOLDER_ID, entity_type="subsidiary"),),
            properties={}, assertion_type="reported", passage_id=passage,
            document_id=document, document_type="narrative_primary", lane="events"))

    for index, instance_id in enumerate(instance_ids):
        claim_id = f"claim:relationship:{index}"
        _anchor(claim_id, "relationship", instance_id)
        relationship_rows.append(RelationshipRow(
            relationship_instance_id=instance_id, claim_id=claim_id,
            relationship_id="BORROWS_UNDER", source_id=PLACEHOLDER_ID,
            source_type=source_type,
            target_id="asset_backed_senior_revolving_credit_facility",
            target_type="asset_backed_debt_facility", assertion_type="reported",
            valid_from=None, valid_to=None,
            passage_id=passage, document_id=document,
            document_type="narrative_primary", lane="events"))

    return ExtractionRunInputs.from_rows(
        manifest=fixture_inputs.manifest, claims=claims, events=event_rows,
        relationships=relationship_rows, evidence=evidence)
