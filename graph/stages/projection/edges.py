"""Edges: what connects to what, with direction taken from the vocabulary and not from taste.

`build_edges` is a pure function of (run catalogs, ontology, provenance). It emits
`GraphEdge` values referencing node **keys** computed with `graph.core.keys`; it builds no
nodes and validates no node's existence — a separate stage does that, and doing it here
would make the two disagree about whose job it is.

Two families, kept visibly apart by `ontology_declared` (§3.3):

* **ontology-declared** — `HAS_OBSERVATION`, `OBSERVATION_OF_SUBJECT`, `EVIDENCED_BY`,
  `PARTICIPATES_IN`, `RECONCILES_TO`, `DISTINCT_FROM` and whichever entity-to-entity
  predicates the relationship claims actually use (this run: `HOLDS_POSITION_AT` ×3,
  `BORROWS_UNDER` ×1). Name, direction and endpoint types are **read from
  `relationships.yaml` through the registry**, never from a list in this file: §1.1's whole
  point is that a vocabulary edit must not be able to silently disagree with the code.
* **projection-local** — exactly four (`PART_OF`, `FOUND_IN`, `CONCERNS_METRIC`,
  `PLACEHOLDER_FOR`), each marked `ontology_declared: false` so a reader can never mistake
  plumbing for a filed fact.

Not emitted, each for a stated reason: `REPORTED_IN` (§14.5 — every V1 fact came through
`sec_edgar`, so the edge would be constant and discriminate nothing), `SUPERSEDES`,
`COMPUTED_FROM`, `USES_FORMULA_VERSION` (§1.4c, §12).

**Three things measured here that the plan states differently.** Recorded rather than
smoothed over, because each one would otherwise look like a bug in this module
*(verified 2026-08-02 against `extract-v1-lexical-2422c4252c07` and the loaded ontology)*:

1. §3.2 says the five unresolvable endpoint names are `ConceptCategory` names. Four are —
   `metric_definition`, `metric_formula`, `event_type`, `relationship_type`. The fifth,
   `metric_observation`, is a **`ClaimKind`** value and is in no `ConceptCategory`. Resolving
   only against `ConceptCategory` would therefore still reject `HAS_OBSERVATION`,
   `OBSERVATION_OF_SUBJECT`, `EVIDENCED_BY` and `SUPERSEDES` — the backbone §3.2 says the
   rule exists to protect. `_STRUCTURAL_ENDPOINT_TYPES` is built from both enums.
2. `PARTICIPATES_IN.allowed_source_types` lists `credit_facility` and **not**
   `asset_backed_debt_facility`, which is the `entity_type` the run's facility participant
   actually carries. Exact membership — the rule
   `ontology/core/constraints.py:726-735` applies to *relationship claims* — would drop a
   filed participant. So endpoint checking here is **subtype-aware**: a type is allowed if it
   or any of its `is_a` ancestors appears in the allowed list. That is what
   `registry.ancestors()` exists for (§3.1), and the widening is one-directional: a type
   the registry does not know at all is still `UNDECLARED_ENDPOINT_TYPE`.
3. §10/the handoff imply 11 event participants; the run has **10** across its 6 events
   (2+2+2+2+1+1). The number is measured from `events.jsonl`, not asserted from the plan.

Determinism (§4.4): no clock, no uuid, no random, and every collection walked is either an
input tuple or an explicitly sorted sequence. Edges come back **unsorted** — `GraphExport`
owns the declared order — but the *content* is a pure function of the inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ontology.contracts import ConceptRegistry, Ontology
from ontology.core.models import MetricDefinition, RelationshipDefinition
from ontology.core.values import ClaimKind, ConceptCategory

from graph.core.citations import cited_passage_ids
from graph.core.inputs import (
    EventRow,
    EvidenceRow,
    ExtractionRunInputs,
    GraphInputError,
    IssueRow,
    ObservationRow,
    RejectedClaimRow,
)
from graph.core.keys import (
    PLACEHOLDER_MARKER,
    derived_edge_key,
    document_key_of_passage,
    entity_node_key,
    event_node_key,
    is_placeholder,
    issue_row_key,
    metric_node_key,
    observation_node_key,
    passage_node_key,
    relationship_edge_key,
    relationship_endpoint_key,
    require_node_key,
)
from graph.core.models import GraphEdge

# -- the vocabulary's own names, used as endpoint types --------------------------------

#: Endpoint names that name a *structure* rather than a concept. Derived from the two
#: declared enums instead of written out, so a new claim kind or concept category is picked
#: up by the same rule that admits the present five (§3.2, and correction 1 above).
_STRUCTURAL_ENDPOINT_TYPES: frozenset[str] = frozenset(
    {str(value) for value in ConceptCategory} | {str(value) for value in ClaimKind}
)

#: The endpoint type a `:Metric` node presents. `relationships.yaml` writes exactly this
#: string in `HAS_OBSERVATION`, `RECONCILES_TO` and `DISTINCT_FROM`.
METRIC_ENDPOINT_TYPE = str(ConceptCategory.METRIC_DEFINITION)

#: The endpoint type an `:Observation` node presents — a `ClaimKind`, not a category.
OBSERVATION_ENDPOINT_TYPE = str(ClaimKind.METRIC_OBSERVATION)

#: The endpoint type an `:Event` node presents.
EVENT_ENDPOINT_TYPE = str(ConceptCategory.EVENT_TYPE)

#: The endpoint type of a **projection-local** edge: none, because no declaration exists to
#: check it against. Deliberately a value `resolve_endpoint_type` refuses, so a local
#: endpoint handed to a declared edge by mistake fails loudly instead of being validated
#: against a plausible-looking type this module invented for it.
LOCAL_ENDPOINT_TYPE = ""

#: The predicates this stage derives rather than reads from a relationship claim. Named so
#: the registry lookup that validates them is visible; the *definitions* still come from the
#: registry, and a vocabulary that dropped one of these would fail at build time.
HAS_OBSERVATION = "HAS_OBSERVATION"
OBSERVATION_OF_SUBJECT = "OBSERVATION_OF_SUBJECT"
EVIDENCED_BY = "EVIDENCED_BY"
PARTICIPATES_IN = "PARTICIPATES_IN"
RECONCILES_TO = "RECONCILES_TO"
DISTINCT_FROM = "DISTINCT_FROM"

#: §3.2's four, and no more without a stated reason.
PART_OF = "PART_OF"
FOUND_IN = "FOUND_IN"
CONCERNS_METRIC = "CONCERNS_METRIC"
PLACEHOLDER_FOR = "PLACEHOLDER_FOR"

PROJECTION_LOCAL_EDGE_TYPES = (PART_OF, FOUND_IN, CONCERNS_METRIC, PLACEHOLDER_FOR)

#: §3.3: the marker that keeps a filed fact and projection plumbing apart.
ONTOLOGY_DECLARED = "ontology_declared"

#: §3.3 point 2: the assertion carried by an edge that comes from the vocabulary rather than
#: from a filing. Deliberately not `assertion_type` — that field means "how a *claim* came to
#: be believed", and these edges are not claims.
ONTOLOGY_DEFINITION_ASSERTION = "ontology_definition"


# -- refusals --------------------------------------------------------------------------


class EdgeProjectionError(GraphInputError):
    """Base for every refusal this stage makes."""


class UndeclaredPredicateError(EdgeProjectionError):
    """A relationship claim names a predicate `relationships.yaml` does not declare."""

    code = "UNDECLARED_PREDICATE"


class UndeclaredEndpointTypeError(EdgeProjectionError):
    """An endpoint type is neither a registry concept nor a structural vocabulary name.

    §3.1's rule, restated for edges: a type the registry does not know is a rejection, never
    a guessed label.
    """

    code = "UNDECLARED_ENDPOINT_TYPE"


class EndpointTypeNotAllowedError(EdgeProjectionError):
    """A declared type appears in a position the predicate does not allow.

    This is what catches a reversed edge. `HAS_OBSERVATION` is
    `metric_definition -> metric_observation`; emitting it the other way round asserts that
    an observation has a metric definition, which the vocabulary does not say.
    """

    code = "ENDPOINT_TYPE_NOT_ALLOWED"


class DuplicateEdgeKeyError(EdgeProjectionError):
    """Two different edges were minted with one `edge_key`.

    The loader `MERGE`s on `edge_key`, so a collision would silently discard one of them.
    An *identical* repeat is not an error — `CONCERNS_METRIC` and `PLACEHOLDER_FOR` are
    set-like by construction — but a repeat that disagrees on anything is.
    """


class MalformedEdgePropertyError(EdgeProjectionError):
    """A property value Neo4j cannot store on an edge (§5.4, §7 of the hard rules).

    Nested maps and heterogeneous lists are rejected here rather than at load time, where
    the failure would name a driver call instead of a fact.
    """


# -- properties ------------------------------------------------------------------------

_SCALARS = (str, int, float, bool)


def _check_scalar(name: str, value: Any) -> Any:
    """Neo4j stores scalars and homogeneous lists of scalars. Nothing else."""
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not all(isinstance(item, _SCALARS) for item in items):
            raise MalformedEdgePropertyError(
                f"edge property {name!r} is a list containing a non-scalar: {items!r}")
        kinds = {type(item) for item in items}
        # `bool` is a subclass of `int`; a mixed [True, 1] list is heterogeneous to a reader
        # even though Neo4j would take it, so it is refused with the rest.
        if len(kinds) > 1:
            raise MalformedEdgePropertyError(
                f"edge property {name!r} is a heterogeneous list: {items!r}")
        return items
    raise MalformedEdgePropertyError(
        f"edge property {name!r} is a {type(value).__name__}, which Neo4j cannot store on "
        "an edge; flatten it or drop it (§5.4)")


def _properties(
    provenance: Mapping[str, Any], *, ontology_declared: bool, **extra: Any
) -> dict[str, Any]:
    """The caller's run provenance, plus this edge's own fields, flattened and checked.

    `None` values are dropped rather than written: in Cypher, setting a property to null
    removes it, so a stored `None` is a fiction the export would carry and the database
    would not. An absent `quoted_text` and a `quoted_text: null` are the same fact.
    """
    merged: dict[str, Any] = {}
    for key in sorted(provenance):
        value = provenance[key]
        if value is None:
            continue
        merged[key] = _check_scalar(key, value)
    merged[ONTOLOGY_DECLARED] = ontology_declared
    for key in sorted(extra):
        value = extra[key]
        if value is None:
            continue
        merged[key] = _check_scalar(key, value)
    return merged


# -- the endpoint validator ------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    """One end of an edge: the node key, the base label, and the *vocabulary* type name.

    The type name is what `allowed_source_types` / `allowed_target_types` are checked
    against. It is not the base label and not the concrete label — `opendoor` is an
    `:Entity` node whose endpoint type is `public_company`. It defaults to
    `LOCAL_ENDPOINT_TYPE` because a projection-local endpoint has no declared type and
    claiming one would be this module inventing vocabulary.
    """

    key: str
    base_label: str
    type_name: str = LOCAL_ENDPOINT_TYPE


class EdgeVocabulary:
    """`relationships.yaml`, read through the registry, as the authority on every edge.

    Built once per projection: `by_category('relationship_type')` is a tuple scan, and the
    30 predicates are consulted once per edge.
    """

    def __init__(self, registry: ConceptRegistry) -> None:
        self._registry = registry
        self._predicates: dict[str, RelationshipDefinition] = {}
        for concept in registry.by_category(str(ConceptCategory.RELATIONSHIP_TYPE)):
            if isinstance(concept, RelationshipDefinition):
                self._predicates[concept.relationship_id] = concept

    @property
    def predicate_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._predicates))

    def predicate(self, relationship_id: str) -> RelationshipDefinition:
        definition = self._predicates.get(relationship_id)
        if definition is None:
            raise UndeclaredPredicateError(
                f"{relationship_id!r} is not declared in relationships.yaml; declared: "
                f"{self.predicate_ids}")
        return definition

    def resolve_endpoint_type(self, type_name: str) -> None:
        """A name must be a registry concept **or** a structural vocabulary name (§3.2).

        The `or` is the whole correction: `registry.concept('metric_observation')` raises,
        and without this branch the projection would reject `HAS_OBSERVATION`,
        `OBSERVATION_OF_SUBJECT` and `EVIDENCED_BY` — its own backbone.
        """
        if not type_name or not type_name.strip():
            raise UndeclaredEndpointTypeError("an empty string is not an endpoint type")
        if type_name in _STRUCTURAL_ENDPOINT_TYPES:
            return
        if self._registry.find(type_name) is not None:
            return
        raise UndeclaredEndpointTypeError(
            f"{type_name!r} is neither a concept in the registry nor one of the structural "
            f"endpoint names {tuple(sorted(_STRUCTURAL_ENDPOINT_TYPES))}")

    def _permitted(self, type_name: str, allowed: Sequence[str]) -> bool:
        if type_name in allowed:
            return True
        if type_name in _STRUCTURAL_ENDPOINT_TYPES:
            # Structural names have no `is_a` chain; exact membership is the only test.
            return False
        return any(ancestor in allowed for ancestor in self._registry.ancestors(type_name))

    def check(self, relationship_id: str, source: Endpoint, target: Endpoint) -> None:
        """Both ends, in the direction the vocabulary declares. Raises, never warns."""
        definition = self.predicate(relationship_id)
        self.resolve_endpoint_type(source.type_name)
        self.resolve_endpoint_type(target.type_name)
        if not self._permitted(source.type_name, definition.allowed_source_types):
            raise EndpointTypeNotAllowedError(
                f"{relationship_id}: {source.type_name!r} ({source.key}) is not allowed as "
                f"source; allowed: {list(definition.allowed_source_types)}")
        if not self._permitted(target.type_name, definition.allowed_target_types):
            raise EndpointTypeNotAllowedError(
                f"{relationship_id}: {target.type_name!r} ({target.key}) is not allowed as "
                f"target; allowed: {list(definition.allowed_target_types)}")


# -- collection ------------------------------------------------------------------------


class _Collector:
    """Accumulates edges, refusing a key collision and tolerating an identical repeat."""

    def __init__(self) -> None:
        self._by_key: dict[str, GraphEdge] = {}
        self._order: list[GraphEdge] = []

    def add(self, edge: GraphEdge) -> None:
        existing = self._by_key.get(edge.edge_key)
        if existing is not None:
            if existing == edge:
                return
            raise DuplicateEdgeKeyError(
                f"edge_key {edge.edge_key!r} was minted twice with different content: "
                f"{existing!r} vs {edge!r}")
        self._by_key[edge.edge_key] = edge
        self._order.append(edge)

    def edges(self) -> tuple[GraphEdge, ...]:
        return tuple(self._order)


def _declared_edge(
    vocabulary: EdgeVocabulary,
    *,
    edge_type: str,
    source: Endpoint,
    target: Endpoint,
    edge_key: str,
    provenance: Mapping[str, Any],
    **extra: Any,
) -> GraphEdge:
    """One ontology-declared edge, endpoint-checked before it is built."""
    vocabulary.check(edge_type, source, target)
    return GraphEdge(
        edge_key=edge_key,
        type=edge_type,
        source_key=source.key,
        source_base_label=source.base_label,
        target_key=target.key,
        target_base_label=target.base_label,
        properties=_properties(provenance, ontology_declared=True, **extra),
    )


def _local_edge(
    *,
    edge_type: str,
    source: Endpoint,
    target: Endpoint,
    provenance: Mapping[str, Any],
    **extra: Any,
) -> GraphEdge:
    """One projection-local edge. No registry check — there is no declaration to check."""
    return GraphEdge(
        edge_key=derived_edge_key(edge_type, source.key, target.key),
        type=edge_type,
        source_key=source.key,
        source_base_label=source.base_label,
        target_key=target.key,
        target_base_label=target.base_label,
        properties=_properties(provenance, ontology_declared=False, **extra),
    )


# -- per-fact provenance ---------------------------------------------------------------


def _fact_provenance(row: Any) -> dict[str, Any]:
    """`claim_id`, `passage_id`, `document_id`, `assertion_type` — where the row has them.

    Read off whichever catalog row the edge came from rather than re-joined through
    `claims.jsonl`: `graph.core.inputs` already asserted the two agree on exactly these
    fields, so a second lookup would add a hop and prove nothing.
    """
    fields: dict[str, Any] = {}
    for name in ("claim_id", "passage_id", "document_id", "assertion_type"):
        value = getattr(row, name, None)
        if value is not None and str(value).strip():
            fields[name] = value
    return fields


def _first_quoted_text(evidence: Sequence[EvidenceRow]) -> str | None:
    """The filing's own words for a relationship claim (§3.1, plan §3.1's edge-on-edge note).

    Neo4j has no edge-on-edge, so a relationship edge's evidence must live in its own
    properties. The first row carrying text wins; rows are already in catalog order, which
    is the run's order and therefore stable.
    """
    for row in evidence:
        if row.quoted_text is not None and row.quoted_text.strip():
            return row.quoted_text
    return None


# -- ontology-declared builders --------------------------------------------------------


def _observation_endpoint(observation: ObservationRow) -> Endpoint:
    return Endpoint(
        key=observation_node_key(observation.observation_id),
        base_label="Observation",
        type_name=OBSERVATION_ENDPOINT_TYPE,
    )


def _metric_endpoint(metric_id: str) -> Endpoint:
    return Endpoint(
        key=metric_node_key(metric_id),
        base_label="Metric",
        type_name=METRIC_ENDPOINT_TYPE,
    )


def _event_endpoint(event: EventRow) -> Endpoint:
    return Endpoint(
        key=event_node_key(event.event_id),
        base_label="Event",
        type_name=EVENT_ENDPOINT_TYPE,
    )


def _passage_endpoint(passage_id: str, *, evidence_kind: str) -> Endpoint:
    """A `:Passage` node, typed by the *evidence kind* that cites it.

    `EVIDENCED_BY.allowed_target_types` is exactly `EvidenceKind` — `normalized_passage`,
    `normalized_table`, … — so the honest endpoint type is the kind the run recorded, not a
    constant `normalized_passage` that would make a table citation look like prose.
    """
    return Endpoint(
        key=passage_node_key(passage_id),
        base_label="Passage",
        type_name=evidence_kind,
    )


def _entity_endpoint(key: str, type_name: str) -> Endpoint:
    return Endpoint(key=key, base_label="Entity", type_name=type_name)


def _observation_edges(
    inputs: ExtractionRunInputs,
    vocabulary: EdgeVocabulary,
    provenance: Mapping[str, Any],
    collector: _Collector,
) -> None:
    """`HAS_OBSERVATION`, `OBSERVATION_OF_SUBJECT` and `EVIDENCED_BY` for every observation."""
    for observation in inputs.observations:
        subject = _observation_endpoint(observation)
        metric = _metric_endpoint(observation.metric_id)
        fact = _fact_provenance(observation)

        collector.add(_declared_edge(
            vocabulary,
            edge_type=HAS_OBSERVATION,
            source=metric,
            target=subject,
            edge_key=derived_edge_key(HAS_OBSERVATION, metric.key, subject.key),
            provenance=provenance,
            source_lane=observation.source_lane,
            **fact,
        ))

        entity = _entity_endpoint(
            entity_node_key(observation.subject_entity_id), observation.subject_type)
        collector.add(_declared_edge(
            vocabulary,
            edge_type=OBSERVATION_OF_SUBJECT,
            source=subject,
            target=entity,
            edge_key=derived_edge_key(OBSERVATION_OF_SUBJECT, subject.key, entity.key),
            provenance=provenance,
            **fact,
        ))

        _evidence_edges(inputs, vocabulary, provenance, collector,
                        claim_id=observation.claim_id, source=subject, fact=fact)


def _evidence_edges(
    inputs: ExtractionRunInputs,
    vocabulary: EdgeVocabulary,
    provenance: Mapping[str, Any],
    collector: _Collector,
    *,
    claim_id: str,
    source: Endpoint,
    fact: Mapping[str, Any],
) -> None:
    """One `EVIDENCED_BY` per distinct passage the claim cites.

    §2.3 trap 4: evidence identity is `(claim_id, passage_id)`, not `claim_id` alone. §4.1
    keys `EVIDENCED_BY` on the `(type, source, target)` triple, so two rows of one claim
    naming one passage would collide — and by the declared identity they cannot exist. The
    collision is refused by name rather than silently collapsed.
    """
    seen: dict[str, str] = {}
    for row in inputs.evidence_by_claim_id.get(claim_id, ()):
        if row.passage_id is None or not row.passage_id.strip():
            raise EdgeProjectionError(
                f"{claim_id}: evidence row {row.evidence_index} has no passage_id, so the "
                "claim cannot be cited to a passage (§1.5); an empty id is never a node key")
        previous = seen.get(row.passage_id)
        if previous is not None:
            if previous == row.evidence_kind:
                continue
            raise EdgeProjectionError(
                f"{claim_id}: two evidence rows cite {row.passage_id!r} with different "
                f"kinds ({previous!r}, {row.evidence_kind!r}); evidence identity is "
                "(claim_id, passage_id) (§2.3 trap 4)")
        seen[row.passage_id] = row.evidence_kind

        passage = _passage_endpoint(row.passage_id, evidence_kind=row.evidence_kind)
        collector.add(_declared_edge(
            vocabulary,
            edge_type=EVIDENCED_BY,
            source=source,
            target=passage,
            edge_key=derived_edge_key(EVIDENCED_BY, source.key, passage.key),
            provenance=provenance,
            evidence_kind=row.evidence_kind,
            source_url=row.source_url,
            # The filed sentence itself, and the two locators that say *where inside the
            # passage* it was read. Carried because §1.5's evidence boundary is the passage
            # and §9's EvidencePackage is assembled from this edge: an `:Observation` whose
            # only evidence is an edge naming a passage id makes a reader open the filing to
            # see what was actually quoted, while an `:Event` — which keeps
            # `evidence_quoted_text` on its node — does not. `quoted_text` is non-empty on
            # all 2,717 evidence rows and `table_id` on 2,690 *(verified 2026-08-02)*.
            quoted_text=row.quoted_text,
            table_id=row.table_id,
            block_ids=list(row.block_ids),
            **{k: v for k, v in fact.items() if k != "passage_id"},
            passage_id=row.passage_id,
        ))


def _event_edges(
    inputs: ExtractionRunInputs,
    vocabulary: EdgeVocabulary,
    provenance: Mapping[str, Any],
    collector: _Collector,
) -> None:
    """`PARTICIPATES_IN` per participant, plus the event's own `EVIDENCED_BY`."""
    for event in inputs.events:
        target = _event_endpoint(event)
        fact = _fact_provenance(event)

        for participant in event.participants:
            # §4.2: a placeholder is keyed *per event*, and this is the event. Passing
            # `event_id` unconditionally keeps named participants on their plain ids.
            key = entity_node_key(participant.entity_id, event_id=event.event_id)
            source = _entity_endpoint(key, participant.entity_type)
            collector.add(_declared_edge(
                vocabulary,
                edge_type=PARTICIPATES_IN,
                source=source,
                target=target,
                # One entity may legitimately hold two roles in one event, so the role is a
                # structural discriminator on the key (§4.1's rule for repeatable edges).
                edge_key=derived_edge_key(
                    PARTICIPATES_IN, source.key, target.key, participant.role),
                provenance=provenance,
                role=participant.role,
                event_type_id=event.event_type_id,
                **fact,
            ))

        _evidence_edges(inputs, vocabulary, provenance, collector,
                        claim_id=event.claim_id, source=target, fact=fact)


def _relationship_edges(
    inputs: ExtractionRunInputs,
    vocabulary: EdgeVocabulary,
    provenance: Mapping[str, Any],
    collector: _Collector,
) -> None:
    """One edge per relationship claim, named and directed by the claim's own predicate.

    Identity is `relationship_instance_id` (§4.1): two filings asserting one predicate
    between one pair are two evidenced assertions, and merging them on the endpoint pair
    would discard a citation.
    """
    for relationship in inputs.relationships:
        source = _entity_endpoint(
            relationship_endpoint_key(relationship.source_id, relationship, inputs.events),
            relationship.source_type)
        target = _entity_endpoint(
            relationship_endpoint_key(relationship.target_id, relationship, inputs.events),
            relationship.target_type)
        collector.add(_declared_edge(
            vocabulary,
            edge_type=relationship.relationship_id,
            source=source,
            target=target,
            edge_key=relationship_edge_key(relationship.relationship_instance_id),
            provenance=provenance,
            relationship_instance_id=relationship.relationship_instance_id,
            # §2.3 trap 1, applied to the third payload catalog: `lane` is the routing
            # vocabulary and `source_type` / `target_type` the ontology's, and observations
            # and events already carry theirs. A relationship edge that dropped `lane` would
            # be the one fact class whose row cannot be traced back to the lane that wrote
            # it; the two endpoint types are what the vocabulary check was made against and
            # are not recoverable from the endpoint keys.
            lane=relationship.lane,
            source_type=relationship.source_type,
            target_type=relationship.target_type,
            valid_from=relationship.valid_from,
            valid_to=relationship.valid_to,
            quoted_text=_first_quoted_text(
                inputs.evidence_by_claim_id.get(relationship.claim_id, ())),
            **_fact_provenance(relationship),
        ))


def _metric_definition_edges(
    ontology: Ontology,
    vocabulary: EdgeVocabulary,
    provenance: Mapping[str, Any],
    collector: _Collector,
) -> None:
    """`RECONCILES_TO` and `DISTINCT_FROM`, taken from the vocabulary and not from a claim.

    §1.1: `DISTINCT_FROM` carries the research's most load-bearing finding — which metrics
    must never be merged — so putting it in the graph makes an accidental merge visible as a
    contradiction instead of invisible as a missing rule. `DISTINCT_FROM` is declared
    symmetric; the projection emits it **exactly as each metric declares it** and does not
    synthesise the reverse, because a synthesised edge would claim the vocabulary said
    something it did not. Where both metrics declare each other, both edges exist.
    """
    metrics = [concept
               for concept in ontology.registry.by_category(
                   str(ConceptCategory.METRIC_DEFINITION))
               if isinstance(concept, MetricDefinition)]
    for metric in sorted(metrics, key=lambda m: m.concept_id):
        source = _metric_endpoint(metric.concept_id)
        if metric.reconciles_to:
            target = _metric_endpoint(metric.reconciles_to)
            collector.add(_declared_edge(
                vocabulary,
                edge_type=RECONCILES_TO,
                source=source,
                target=target,
                edge_key=derived_edge_key(RECONCILES_TO, source.key, target.key),
                provenance=provenance,
                assertion=ONTOLOGY_DEFINITION_ASSERTION,
            ))
        for other in metric.distinct_from:
            target = _metric_endpoint(other)
            collector.add(_declared_edge(
                vocabulary,
                edge_type=DISTINCT_FROM,
                source=source,
                target=target,
                edge_key=derived_edge_key(DISTINCT_FROM, source.key, target.key),
                provenance=provenance,
                assertion=ONTOLOGY_DEFINITION_ASSERTION,
            ))


# -- projection-local builders ---------------------------------------------------------


def _passage_document_edges(
    inputs: ExtractionRunInputs, provenance: Mapping[str, Any], collector: _Collector
) -> None:
    """`PART_OF`, derived from the id grammar rather than a catalog lookup (§4.1).

    The passage set comes from `graph.core.citations`, which is also what the node builder
    asks — one function, so a passage that gets a node always gets its edge.
    """
    for passage_id in cited_passage_ids(inputs):
        source = Endpoint(key=passage_node_key(passage_id), base_label="Passage")
        target = Endpoint(key=document_key_of_passage(passage_id), base_label="Document")
        collector.add(_local_edge(
            edge_type=PART_OF, source=source, target=target, provenance=provenance))


def _issue_edges(
    inputs: ExtractionRunInputs,
    ontology: Ontology,
    provenance: Mapping[str, Any],
    collector: _Collector,
) -> None:
    """`FOUND_IN` for every issue, and `CONCERNS_METRIC` where `concept_ids` names a metric.

    §3.3 point 3: these are the *only* two edges an `:Issue` gets. An abstention must never
    be reachable by following evidence from an observation, because it is not evidence for
    anything — and §10 criterion 5's converse holds too: an issue never suppresses a fact.

    "Every issue" includes the `assemble`-origin rejections `issues.jsonl` never mirrors
    (§2.2 P10): `nodes.py` projects those as `:Issue:Rejected` nodes keyed on their
    `rejection_id`, and a node with no edge to the passage its refusal is about would be
    an `:Issue` the graph cannot navigate to. Zero such rows in this run, so no count moves.
    """
    for issue in (*inputs.issues, *inputs.rejection_issue_join.unmirrored):
        source = Endpoint(key=issue_row_key(issue), base_label="Issue")
        passage = Endpoint(key=passage_node_key(issue.passage_id), base_label="Passage")
        # No `passage_id` property: it is `target_key`, and a property that only restates an
        # endpoint is the same failure as a comment that restates its line.
        collector.add(_local_edge(
            edge_type=FOUND_IN, source=source, target=passage, provenance=provenance,
            code=issue.code, severity=issue.severity, lane=issue.lane,
            document_id=issue.document_id, rejected_claim=issue.rejected_claim,
        ))
        for concept_id in _declared_metric_ids(issue, ontology):
            metric = Endpoint(key=metric_node_key(concept_id), base_label="Metric")
            # `passage_id` *is* carried here: neither endpoint is the passage, and Q9 asks
            # which metric a silence on a given filing was about (§3.2).
            collector.add(_local_edge(
                edge_type=CONCERNS_METRIC, source=source, target=metric,
                provenance=provenance, code=issue.code, severity=issue.severity,
                passage_id=issue.passage_id, document_id=issue.document_id,
            ))


def _declared_metric_ids(
    issue: IssueRow | RejectedClaimRow, ontology: Ontology
) -> tuple[str, ...]:
    """The `concept_ids` that name a declared metric, in catalog order, without repeats.

    An issue's `concept_ids` are candidates the extractor was weighing, and not all of them
    are metrics — `UNRESOLVED_METRIC` in particular names strings the registry never
    resolved. Only a declared `MetricDefinition` gets an edge; the rest are left on the
    `:Issue` node for whoever builds it.
    """
    resolved: list[str] = []
    for concept_id in issue.concept_ids:
        if concept_id in resolved:
            continue
        if isinstance(ontology.registry.find(concept_id), MetricDefinition):
            resolved.append(concept_id)
    return tuple(resolved)


def _placeholder_edges(
    inputs: ExtractionRunInputs, provenance: Mapping[str, Any], collector: _Collector
) -> None:
    """`PLACEHOLDER_FOR`: what an unnamed participant was described *relative to*.

    §4.2: it records the parent the filing described the entity against and asserts nothing
    about which entity it is. The parent is read off the placeholder id itself — the
    `{subject}_unnamed_{type}` grammar `extraction/core/identifiers.py:123-133` mints — because
    `named` and `entity_text` reach no catalog (handoff §4.8) and the id shape is the only
    surviving signal.

    Derived from event participants, which is the only place a placeholder enters the graph:
    a relationship endpoint that names one resolves, by `relationship_endpoint_key`, to the
    very same per-event node, so it would produce the same edge and not a second one.
    """
    for event in inputs.events:
        for participant in event.participants:
            if not is_placeholder(participant.entity_id):
                continue
            parent_id, _, _ = participant.entity_id.partition(PLACEHOLDER_MARKER)
            require_node_key(
                parent_id, what=f"parent of placeholder {participant.entity_id!r}")
            # The parent's own type is not asserted here: this stage knows the placeholder's
            # type, not the parent's, and `PLACEHOLDER_FOR` is projection-local anyway, so
            # no declaration would check it (§4.2 — the edge records what the entity was
            # described relative to and nothing about who either party is).
            source = Endpoint(
                key=entity_node_key(participant.entity_id, event_id=event.event_id),
                base_label="Entity")
            target = Endpoint(key=parent_id, base_label="Entity")
            collector.add(_local_edge(
                edge_type=PLACEHOLDER_FOR,
                source=source,
                target=target,
                provenance=provenance,
                extraction_entity_id=participant.entity_id,
                event_id=event.event_id,
                role=participant.role,
                resolved=False,
            ))


# -- the stage ---------------------------------------------------------------------------


def build_edges(
    inputs: ExtractionRunInputs,
    *,
    ontology: Ontology,
    provenance: Mapping[str, Any],
) -> tuple[GraphEdge, ...]:
    """Every edge of one projection, unsorted but deterministic in content.

    Unsorted because `GraphExport.sorted()` owns §4.4's declared order and two owners of one
    order is how byte-identity quietly stops holding. Deterministic because every collection
    walked here is an input tuple in catalog order or an explicitly sorted sequence.

    Endpoint *existence* is deliberately not checked: this stage does not build nodes, and a
    module that both invented a key and asserted the key was projected would be checking
    itself. A later stage compares the two sets.
    """
    vocabulary = EdgeVocabulary(ontology.registry)
    collector = _Collector()

    _observation_edges(inputs, vocabulary, provenance, collector)
    _event_edges(inputs, vocabulary, provenance, collector)
    _relationship_edges(inputs, vocabulary, provenance, collector)
    _metric_definition_edges(ontology, vocabulary, provenance, collector)

    _passage_document_edges(inputs, provenance, collector)
    _issue_edges(inputs, ontology, provenance, collector)
    _placeholder_edges(inputs, provenance, collector)

    return collector.edges()


def edge_counts(edges: Iterable[GraphEdge]) -> dict[str, int]:
    """`type -> count`, sorted by type. A reporting helper, and what the tests assert on."""
    counts: dict[str, int] = {}
    for edge in edges:
        counts[edge.type] = counts.get(edge.type, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


__all__ = [
    "CONCERNS_METRIC",
    "DISTINCT_FROM",
    "DuplicateEdgeKeyError",
    "EVIDENCED_BY",
    "EVENT_ENDPOINT_TYPE",
    "EdgeProjectionError",
    "EdgeVocabulary",
    "Endpoint",
    "EndpointTypeNotAllowedError",
    "FOUND_IN",
    "HAS_OBSERVATION",
    "LOCAL_ENDPOINT_TYPE",
    "MalformedEdgePropertyError",
    "METRIC_ENDPOINT_TYPE",
    "OBSERVATION_ENDPOINT_TYPE",
    "OBSERVATION_OF_SUBJECT",
    "ONTOLOGY_DECLARED",
    "ONTOLOGY_DEFINITION_ASSERTION",
    "PARTICIPATES_IN",
    "PART_OF",
    "PLACEHOLDER_FOR",
    "PROJECTION_LOCAL_EDGE_TYPES",
    "RECONCILES_TO",
    "UndeclaredEndpointTypeError",
    "UndeclaredPredicateError",
    "build_edges",
    "edge_counts",
]
