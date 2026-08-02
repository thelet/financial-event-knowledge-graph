"""The seven node types V1_GRAPH_PROTOTYPE §3.1 declares, built from catalog fields only.

One builder per base label, each a pure function of (catalogs, ontology, provenance). What the
module refuses to do is as much a part of its contract as what it emits:

* **It invents no field.** `population_role`, `population_confidence` and an event
  participant's `entity_text` exist in no catalog (§5.4, §1.4a). They are not defaulted, not
  reconstructed and not guessed. `entity_text` appears on an entity node only where the run
  actually recorded one — `extractor_metadata.source_name` / `target_name`, on the 4
  relationship claims and nowhere else.
* **It suppresses nothing because an issue names its passage.** A refusal in this run is
  per-reading or per-property, never per-node (handoff §4.2): the event carrying
  `PARTICIPANT_NOT_NAMED` and the four carrying `PROPERTY_VALUE_NOT_IN_PASSAGE` keep their
  `:Event` nodes. §10 criterion 5 makes the converse a failure too.
* **It re-derives only what the run recorded and can be checked against.** Every
  `observation_id` is recomputed and compared (§4.1, 2,707/2,707 at G0); the per-claim warning
  state is replayed through the ontology's own validator and reconciled against the manifest's
  aggregate (§5.5, 186).
* **It creates no `:Claim` and no `:FiscalPeriod` node** (§3.1's "deliberately not nodes").

Neo4j's property model is enforced here rather than at the writer: no nested map and no
heterogeneous list ever reaches a property, and **no null**. A property set to null in Cypher
is a property removed, so an exported null describes a graph the loader would not produce;
`_properties` drops it, exactly as `edges._properties` already did (§5.4). A run-provenance
key that collides with a filed fact key is a hard refusal rather than a silent overwrite —
see `ProvenanceCollisionError`. `EventInstance.properties` is flattened to `prop_<name>`
strings and a non-scalar value is a `MALFORMED_EVENT_PROPERTY` refusal, not a silent `str()`
of a dict (§5.4, §6.4).

Determinism (§4.4): no clock, no uuid, no random value, and every collection that becomes a
property is sorted before it does. Nodes are returned in builder order and left unsorted — the
export applies `NODE_SORT_KEY`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

from extraction.core.assembly import deferred_metric_ids
from ontology.contracts import Ontology

from ...core.derivation import (
    check_observation_ids,
    derive_warnings,
    period_key,
    reconcile_warning_count,
)
from ...core.citations import cited_passage_ids, documents_of
from ...core.inputs import (
    ClaimRow,
    DocumentRow,
    EventRow,
    ExtractionRunInputs,
    GraphInputError,
    IssueRow,
    PassageRow,
    RejectedClaimRow,
)
from ...core.keys import (
    document_node_key,
    entity_node_key,
    event_node_key,
    is_placeholder,
    issue_row_key,
    metric_node_key,
    observation_node_key,
    passage_node_key,
    relationship_endpoint_key,
    require_node_key,
)
from ...core.models import GraphNode

# -- refusals, named with §6.4's codes --------------------------------------------------


class NodeProjectionError(GraphInputError):
    """A row the node builder cannot project. Carries §6.4's code for `rejected.jsonl`."""

    code = "MISSING_REQUIRED_KEY"


class UndeclaredEntityTypeError(NodeProjectionError):
    """A type string the ontology does not declare.

    §3.1: the concrete entity labels *are* the concept id and its `is_a` ancestors, so a type
    the registry cannot resolve has no label — and guessing one (`:Facility`, `:Instrument`)
    is exactly the mistake review caught in the plan's own draft.
    """

    code = "UNDECLARED_ENDPOINT_TYPE"


class MalformedEventPropertyError(NodeProjectionError):
    """An `EventInstance.properties` value Neo4j cannot store as a scalar (§5.4)."""

    code = "MALFORMED_EVENT_PROPERTY"


class PassageNotInCatalogError(NodeProjectionError):
    """A row cites a passage `passages.jsonl` does not contain."""

    code = "EVIDENCE_PASSAGE_NOT_IN_CATALOG"


class DocumentNotInCatalogError(NodeProjectionError):
    """A cited passage names a document `documents.jsonl` does not contain."""

    code = "EVIDENCE_PASSAGE_NOT_IN_CATALOG"


# -- constants --------------------------------------------------------------------------

#: §5.5's provenance block. Required rather than optional: a node without it cannot answer
#: "which run said this", which §10 criterion 4 makes an acceptance test.
PROVENANCE_KEYS = (
    "extraction_run_id",
    "graph_run_id",
    "graph_projection_version",
    "ontology_id",
    "ontology_version",
    "ontology_definition_hash",
    "extraction_code_commit",
    "graph_code_commit",
)

#: §5.4's promotion list, plus the two narrative spellings of the scale-declaration concept
#: (point 2 — carried under their own names, deliberately *not* unified) and `value_text`,
#: which holds the printed form on the 17 narrative claims. Everything not named here survives
#: as `extractor_metadata_json`, so nothing is lost and nothing is silently renamed.
PROMOTED_METADATA_KEYS = (
    "period_label",
    "period_label_char_start",
    "period_label_in_evidence",
    "period_label_distance_from_evidence",
    "row_index",
    "value_column_index",
    "period_header_row_index",
    "period_header_column_index",
    "metric_label_row_index",
    "subject_basis",
    "scale_source",
    "scale_declaration_location",
    "scale_declared_in",
    "prompt_version",
    "provider_model_id",
    "scope",
    "source_name",
    "target_name",
    "value_text",
)

#: §5.4: one Neo4j property per declared event property key, prefixed so a filing's own
#: vocabulary can never collide with a projection field.
EVENT_PROPERTY_PREFIX = "prop_"

#: The 10,852 rows of this run that record a question never put to a provider
#: (`provider_calls_permitted: 0`), not a finding about the corpus (handoff §4.9). Labelled so
#: every view can exclude them without re-deriving the reason.
NOT_ATTEMPTED_CODE = "NO_STORED_ANSWER"

_SCALARS = (str, int, float, bool)


class _EntityVocabulary(Protocol):
    """The registry surface the entity builder needs.

    Declared locally because `ontology.contracts.ConceptRegistry` under-declares it: `find`,
    `ancestors` and `by_category` are on the protocol, but `instance()` and `definitions` —
    which §3.1's "ontology `instances` (4)" requires — are on `InMemoryConceptRegistry` only.
    Naming the gap here is cheaper and more honest than reaching through `Any`, and it keeps
    the ontology package unmodified.
    """

    def find(self, concept_id: str) -> Any | None: ...

    def ancestors(self, concept_id: str) -> tuple[str, ...]: ...

    def by_category(self, category: str) -> tuple[Any, ...]: ...

    def instance(self, instance_id: str) -> Any | None: ...

    @property
    def definitions(self) -> Any: ...


# -- the entry point ---------------------------------------------------------------------


def build_nodes(
    inputs: ExtractionRunInputs,
    *,
    ontology: Ontology,
    provenance: Mapping[str, Any],
) -> tuple[GraphNode, ...]:
    """Every §3.1 node for one extraction run.

    Returned **unsorted**: `GraphExport.sorted()` owns §4.4's declared order, and sorting twice
    would put the same rule in two places. Content is deterministic regardless — no builder
    iterates a set or an unordered mapping into a property.
    """
    shared = _checked_provenance(provenance)
    registry: _EntityVocabulary = ontology.registry  # type: ignore[assignment]
    cited = cited_passage_ids(inputs)
    return (
        *_metric_nodes(ontology, registry, shared),
        *_observation_nodes(inputs, ontology, shared),
        *_event_nodes(inputs, shared),
        *_entity_nodes(inputs, registry, shared),
        *_passage_nodes(inputs, cited, shared),
        *_document_nodes(inputs, cited, shared),
        *_issue_nodes(inputs, shared),
    )


# -- shared helpers ----------------------------------------------------------------------


def _checked_provenance(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """§5.5's eight fields, present and scalar, or the build stops.

    A superset is accepted — a caller recording an extra run fact is not an error — but a
    missing field is, because a node that cannot name its run is unusable for §10 criterion 4
    and the omission would only surface after a load.
    """
    missing = [key for key in PROVENANCE_KEYS if key not in provenance]
    if missing:
        raise NodeProjectionError(
            f"provenance is missing {missing}; §5.5 requires all of {list(PROVENANCE_KEYS)}")
    checked: dict[str, Any] = {}
    for key in sorted(provenance):
        value = provenance[key]
        if not isinstance(value, _SCALARS):
            raise NodeProjectionError(
                f"provenance[{key!r}] is {type(value).__name__}; a node property must be a "
                "scalar (§5.4)")
        checked[key] = value
    return checked


class ProvenanceCollisionError(NodeProjectionError):
    """A run-provenance key and a filed fact key are the same name.

    Not resolvable in either direction, so it is not resolved. Letting provenance win — what
    `merged.update(shared)` did until 2026-08-03 — overwrites a filed `source_lane`,
    `assertion_type` or `claim_id` on **every** node in the export with a value that came
    from the composition root; letting the fact win silently drops the provenance a node is
    required to carry (§5.5). `_checked_provenance` deliberately accepts a superset of
    §5.5's eight keys, so the collision is reachable from a caller, and a caller supplying a
    key that shadows a catalog field has made a programming mistake, not filed data.
    """

    code = "PROVENANCE_KEY_COLLISION"


def _properties(shared: Mapping[str, Any], **fields: Any) -> dict[str, Any]:
    """One node's properties: its own fields, plus the run provenance every node carries.

    **A `None` value contributes no property**, the same rule `edges._properties` applies and
    for the same reason: in Cypher `SET n += {k: null}` *removes* `k`, so an exported null is
    a property the loaded graph will not have — a fiction the artifact carries and the
    database does not. Dropping it here makes `nodes.jsonl` say what a G2 load would produce
    (53,951 such properties on the real run before this change). "Absent" and "null" were
    already one fact in the source too: `extractor_metadata` omits keys rather than nulling
    them (§5.4 point 1).
    """
    # Checked on the declared field names, not on the surviving ones: a fact that happens to
    # be null on this row still owns its name, and a collision that only fires on the rows
    # where the field is populated is the worst of both behaviours.
    collisions = sorted(set(fields) & set(shared))
    if collisions:
        raise ProvenanceCollisionError(
            f"provenance key(s) {collisions} collide with filed fact properties on this "
            "node; §5.5's provenance block and a catalog field may not share a name")
    merged = {key: value for key, value in fields.items() if value is not None}
    merged.update(shared)  # scalar and non-null by `_checked_provenance`.
    return merged


def _string_list(values: Iterable[Any], *, what: str) -> list[str]:
    """A homogeneous list of strings, in the order given. Neo4j stores no mixed list."""
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise NodeProjectionError(
                f"{what} holds a {type(value).__name__}; a Neo4j list must be homogeneous")
        out.append(value)
    return out


def _one_or_variants(properties: dict[str, Any], name: str, values: Iterable[str]) -> None:
    """A single value keeps its own name; a real disagreement is surfaced, never resolved.

    §10 criterion 8 makes "entity ids under more than one type" and "reached from more than one
    `entity_text`" *findings to publish*, not build failures. Picking a winner would hide the
    finding; refusing to build would fail on something the plan says to report. So the scalar
    property exists only when the run is unambiguous, and otherwise the sorted variants and an
    explicit conflict flag take its place.
    """
    distinct = sorted(set(values))
    if len(distinct) == 1:
        properties[name] = distinct[0]
    elif distinct:
        properties[f"{name}_variants"] = distinct
        properties[f"{name}_conflict"] = True


def _pascal_case(concept_id: str) -> str:
    """`asset_backed_debt_facility` -> `AssetBackedDebtFacility` (§3.1)."""
    parts = [part for part in concept_id.split("_") if part]
    if not parts:
        raise NodeProjectionError(f"{concept_id!r} yields no label")
    return "".join(part[0].upper() + part[1:] for part in parts)


def _manifest_describes_these_rows(inputs: ExtractionRunInputs) -> bool:
    """Whether `manifest.counts` counts *these* rows, so §5.5's reconciliation is meaningful.

    `tests/fixtures/graph/` ships the run's manifest **verbatim** — its README says so
    deliberately, because a manifest edited to match a slice would no longer be the run's
    manifest. Its `counts` therefore describe 2,707 observations beside 30 fixture rows, and
    reconciling a 9-warning slice against the run's 186 would fail for a reason that is not a
    defect. The reconciliation runs when the row counts say the input is the whole run, and the
    real-run test proves it does run there.
    """
    counts = inputs.manifest.counts
    return (counts.get("observations") == len(inputs.observations)
            and counts.get("claims") == len(inputs.claims))


def _promoted_metadata(claim: ClaimRow) -> dict[str, Any]:
    """§5.4's promoted keys, plus everything else as one opaque JSON string.

    Keys are **omitted, not nulled** in `extractor_metadata` (§5.4 point 1), and that is
    preserved: a promoted key absent from the claim contributes no property rather than a null
    one, so "this lane never wrote a scale" stays distinguishable from "the scale was null".
    """
    metadata = claim.extractor_metadata
    promoted: dict[str, Any] = {}
    for key in PROMOTED_METADATA_KEYS:
        if key not in metadata:
            continue
        value = metadata[key]
        if isinstance(value, _SCALARS) or value is None:
            promoted[key] = value
        else:
            promoted[key] = _string_list(value, what=f"{claim.claim_id}.{key}")
    rest = {key: value for key, value in metadata.items()
            if key not in PROMOTED_METADATA_KEYS}
    if rest:
        promoted["extractor_metadata_json"] = json.dumps(rest, sort_keys=True)
    return promoted


# -- :Metric -----------------------------------------------------------------------------


def _metric_nodes(
    ontology: Ontology, registry: _EntityVocabulary, shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """All 26 metric definitions, including the ones no claim reached.

    §14.4: a metric with no observation is a **coverage gap**, and a coverage gap that is not
    in the graph cannot be seen. `in_scope_v1` separates the 20 this corpus can answer from the
    6 whose first source lane is `xbrl` — read from the ontology through the same
    `deferred_metric_ids` the extractor used, so a vocabulary edit cannot silently disagree
    with either layer.
    """
    deferred = deferred_metric_ids(ontology)
    nodes: list[GraphNode] = []
    for metric in registry.by_category("metric_definition"):
        properties = _properties(
            shared,
            metric_id=metric.concept_id,
            label=metric.label,
            description=metric.description,
            metric_category=str(metric.metric_category),
            value_type=str(metric.value_type),
            unit=metric.unit,
            allowed_units=_string_list(metric.allowed_units, what="allowed_units"),
            period_type=str(metric.period_type),
            gaap_status=str(metric.gaap_status),
            subject_types=_string_list(metric.subject_types, what="subject_types"),
            source_lane_preferences=[str(lane) for lane in metric.source_lane_preferences],
            forbidden_source_lanes=[str(lane) for lane in metric.forbidden_source_lanes],
            aliases=_string_list(metric.aliases, what="aliases"),
            in_scope_v1=metric.concept_id not in deferred,
        )
        nodes.append(GraphNode(
            key=metric_node_key(metric.concept_id),
            base_label="Metric",
            labels=("Metric",),
            properties=properties,
        ))
    return tuple(nodes)


# -- :Observation ------------------------------------------------------------------------


def _observation_nodes(
    inputs: ExtractionRunInputs, ontology: Ontology, shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """One node per `observations.jsonl` row — every row, and only rows.

    No filter by issue: §2.4 measured the input clean, and re-litigating a refusal here would
    either drop a kept reading or resurrect a refused one. §10 criterion 10 re-checks the
    membership after loading precisely because the projection is the thing that could break it.
    """
    check_observation_ids(inputs.observations, inputs.claims_by_id)
    warnings = derive_warnings(inputs.observations, inputs.evidence, ontology)
    if _manifest_describes_these_rows(inputs):
        reconcile_warning_count(warnings, inputs.manifest)

    nodes: list[GraphNode] = []
    for row in inputs.observations:
        claim = inputs.claims_by_id[row.claim_id]
        codes = warnings.get(row.claim_id, ())
        properties = _properties(
            shared,
            observation_id=row.observation_id,
            claim_id=row.claim_id,
            metric_id=row.metric_id,
            subject_entity_id=row.subject_entity_id,
            subject_type=row.subject_type,
            value=row.value,
            unit=row.unit,
            currency=row.currency,
            period_start=row.period_start,
            period_end=row.period_end,
            instant_date=row.instant_date,
            period_key=period_key(row),
            # No `period_end_date` twin. §5.5 asked for one; measured, it was a byte-identical
            # copy of `period_end` carrying zero information, and null on all 403 instant
            # observations — the rows a range query most needs a date on. Applying `date()` is
            # the G2 loader's job over the authoritative ISO field, and the plan now says so.
            # §5.4: only the one population field the catalogs carry. `population_role` and
            # `population_confidence` are pydantic defaults, not filed facts, and are absent.
            population_definition_raw=row.population_definition_raw,
            source_lane=row.source_lane,
            lane=row.lane,
            assertion_type=row.assertion_type,
            confidence=row.confidence,
            passage_id=row.passage_id,
            document_id=row.document_id,
            document_type=row.document_type,
            ambiguity_codes=_string_list(row.ambiguity_codes, what="ambiguity_codes"),
            scale=row.scale,
            scale_location=row.scale_location,
            row_label=row.row_label,
            column_label=row.column_label,
            validation_state="warned" if codes else "clean",
            warning_codes=_string_list(codes, what="warning_codes"),
            **_promoted_metadata(claim),
        )
        labels = ("Observation", "Warned") if codes else ("Observation",)
        nodes.append(GraphNode(
            key=observation_node_key(row.observation_id),
            base_label="Observation",
            labels=labels,
            properties=properties,
        ))
    return tuple(nodes)


# -- :Event ------------------------------------------------------------------------------


def _event_property_value(event: EventRow, name: str, value: Any) -> str:
    """A filed property value as a string, or a refusal.

    §5.4: `"$525 million"` and `"approximately 550 employees"` are the filing's words. Parsing
    either into a number would assert a measurement the extractor deliberately did not make, so
    the coercion is to `str` and never to a type. `None` is refused rather than rendered
    `"None"` — Neo4j has no null property, and a literal `"None"` would read as filed text.
    """
    if not isinstance(value, _SCALARS):
        raise MalformedEventPropertyError(
            f"{event.event_id}: property {name!r} is a {type(value).__name__}; Neo4j stores no "
            "nested map or list as a property value (§5.4)")
    return str(value)


def _event_nodes(
    inputs: ExtractionRunInputs, shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """One node per `events.jsonl` row, with both dates independent and all five §5.5 fields.

    `occurred_on` and `announced_on` are copied, nulls included, and neither is ever filled
    from the other or from a document's `filing_date`. `dates_equal`, `review_flag` and the two
    `*_date_text` fields are computed nowhere else in the run (§0b item 3) — dropping them
    would make an absent date merely blank instead of legibly unstated.
    """
    nodes: list[GraphNode] = []
    for row in inputs.events:
        claim = inputs.claims_by_id[row.claim_id]
        flattened = {
            f"{EVENT_PROPERTY_PREFIX}{name}": _event_property_value(row, name, value)
            for name, value in sorted(row.properties.items())
        }
        properties = _properties(
            shared,
            event_id=row.event_id,
            claim_id=row.claim_id,
            event_type_id=row.event_type_id,
            occurred_on=row.occurred_on,
            announced_on=row.announced_on,
            # No `occurred_on_date` / `announced_on_date` twins — see `_observation_nodes`
            # and the amended §5.5: they were byte-identical copies, and typed dates are a
            # loader concern applied to the authoritative ISO field.
            occurrence_date_text=row.occurrence_date_text,
            announcement_date_text=row.announcement_date_text,
            dates_equal=row.dates_equal,
            review_flag=row.review_flag,
            evidence_quoted_text=row.evidence_quoted_text,
            assertion_type=row.assertion_type,
            passage_id=row.passage_id,
            document_id=row.document_id,
            document_type=row.document_type,
            lane=row.lane,
            # §5.5's two validation properties, on `:Event` as well as on `:Observation`.
            # `clean` on all six, and not because the state was assumed: `derive_warnings`
            # covers observations because **this ontology declares no event-level warning
            # rule at all** — the manifest's 186 come from `validate_claims` over all 2,717
            # claims and none of them is an event (§5.5's own "the denominators differ"
            # note). Without these two properties `WHERE x.validation_state = 'clean'` — the
            # obvious way to ask for trustworthy facts — silently excludes every event,
            # which is the opposite of what the run measured. The day an event-level warning
            # exists, this is where it is filled in.
            validation_state="clean",
            warning_codes=[],
            participant_roles=sorted({p.role for p in row.participants}),
            property_names=sorted(row.properties),
            **flattened,
            **_promoted_metadata(claim),
        )
        nodes.append(GraphNode(
            key=event_node_key(row.event_id),
            base_label="Event",
            labels=("Event",),
            properties=properties,
        ))
    return tuple(nodes)


# -- :Entity -----------------------------------------------------------------------------


@dataclass
class _EntityDraft:
    """One entity node under construction, accumulating the types and names that reach it."""

    key: str
    extraction_entity_id: str
    resolved: bool
    scoped_to_event_id: str | None = None
    concept_ids: set[str] = field(default_factory=set)
    texts: set[str] = field(default_factory=set)
    instance_label: str | None = None
    is_ontology_instance: bool = False


def _entity_labels(
    concept_ids: Sequence[str], registry: _EntityVocabulary, *, unresolved: bool
) -> tuple[str, ...]:
    """`:Entity` plus each concept id and its `is_a` ancestors, most-specific-first (§3.1).

    Both of review's corrections live here. The chain is resolved from the **concept** id, so
    a caller that handed over an *instance* id would get `()` from `ancestors()` and a bare
    `:Entity` — which is why `_entity_nodes` resolves `opendoor` to `public_company` before
    reaching this function. And the chain is resolved from whatever category the concept
    belongs to: the run's facility carries `asset_backed_debt_facility`, an `agreement_type`
    whose chain is `credit_facility -> credit_agreement -> agreement`, and it resolves exactly
    as an `entity_type` does. A type the registry does not know at all is a refusal.
    """
    labels = ["Entity"]
    for concept_id in concept_ids:
        if registry.find(concept_id) is None:
            raise UndeclaredEntityTypeError(
                f"{concept_id!r} is not a concept in this ontology; §3.1 derives entity labels "
                "from the concept and its is_a ancestors, and a guessed label names nothing")
        for name in (concept_id, *registry.ancestors(concept_id)):
            label = _pascal_case(name)
            if label not in labels:
                labels.append(label)
    if unresolved:
        labels.append("Unresolved")
    return tuple(labels)


def _entity_drafts(
    inputs: ExtractionRunInputs, registry: _EntityVocabulary
) -> dict[str, _EntityDraft]:
    """Every entity the graph must hold, from the four places one is named.

    Iteration is over the catalogs in file order and the ontology's own instance order, so the
    accumulated sets are built the same way every run; each set is sorted before it becomes a
    property.
    """
    drafts: dict[str, _EntityDraft] = {}

    def draft_for(key: str, extraction_id: str, *, resolved: bool,
                  event_id: str | None = None) -> _EntityDraft:
        existing = drafts.get(key)
        if existing is None:
            existing = _EntityDraft(key=key, extraction_entity_id=extraction_id,
                                    resolved=resolved, scoped_to_event_id=event_id)
            drafts[key] = existing
        return existing

    # 1. The ontology's own named individuals (§3.1: 4 of them).
    for instance in registry.definitions.instances:
        entry = draft_for(instance.instance_id, instance.instance_id, resolved=True)
        entry.concept_ids.add(instance.concept_id)
        entry.instance_label = instance.label
        entry.is_ontology_instance = True

    # 2. Every observation subject.
    for observation in inputs.observations:
        key = entity_node_key(observation.subject_entity_id)
        draft_for(key, observation.subject_entity_id,
                  resolved=True).concept_ids.add(observation.subject_type)

    # 3. Every event participant, keyed per event when the filing did not name it (§4.2).
    for event in inputs.events:
        for participant in event.participants:
            placeholder = is_placeholder(participant.entity_id)
            key = entity_node_key(participant.entity_id, event_id=event.event_id)
            entry = draft_for(key, participant.entity_id, resolved=not placeholder,
                              event_id=event.event_id if placeholder else None)
            entry.concept_ids.add(participant.entity_type)

    # 4. Every relationship endpoint. `source_name` / `target_name` are the **only** place a
    #    filing's own wording for an entity survives into a catalog (§1.4a), so this is the one
    #    loop that may set `entity_text` at all.
    for relationship in inputs.relationships:
        metadata = inputs.claims_by_id[relationship.claim_id].extractor_metadata
        endpoints = ((relationship.source_id, relationship.source_type, "source_name"),
                     (relationship.target_id, relationship.target_type, "target_name"))
        for entity_id, entity_type, name_key in endpoints:
            key = relationship_endpoint_key(entity_id, relationship, inputs.events)
            placeholder = is_placeholder(entity_id)
            entry = draft_for(
                key, entity_id, resolved=not placeholder,
                event_id=key.partition("#")[2] if placeholder else None)
            entry.concept_ids.add(entity_type)
            text = metadata.get(name_key)
            if isinstance(text, str) and text.strip():
                entry.texts.add(text)
    return drafts


def _entity_nodes(
    inputs: ExtractionRunInputs, registry: _EntityVocabulary, shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """§3.1's `:Entity` nodes: ontology instances, claim participants, endpoints, subjects."""
    nodes: list[GraphNode] = []
    drafts = _entity_drafts(inputs, registry)
    for key in sorted(drafts):
        entry = drafts[key]
        # An instance id resolves to its concept before ancestors are asked for — trap (a).
        concept_ids = sorted(entry.concept_ids)
        properties = _properties(
            shared,
            entity_id=entry.key,
            # Equal to `entity_id` everywhere except §4.2's per-event keys, and kept there so a
            # later entity-resolution pass can find every placeholder by shape.
            extraction_entity_id=entry.extraction_entity_id,
            resolved=entry.resolved,
            ontology_instance=entry.is_ontology_instance,
        )
        if entry.instance_label is not None:
            properties["label"] = entry.instance_label
        if entry.scoped_to_event_id is not None:
            properties["scoped_to_event_id"] = entry.scoped_to_event_id
        _one_or_variants(properties, "entity_type", concept_ids)
        _one_or_variants(properties, "entity_text", entry.texts)
        nodes.append(GraphNode(
            key=require_node_key(entry.key, what="entity_id"),
            base_label="Entity",
            labels=_entity_labels(concept_ids, registry, unresolved=not entry.resolved),
            properties=properties,
        ))
    return tuple(nodes)


# -- :Passage and :Document ---------------------------------------------------------------


def _passage_nodes(
    inputs: ExtractionRunInputs, cited: Sequence[str], shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """Only cited passages — 8,776 of the corpus's 12,442 (§3.1).

    The set comes from `graph.core.citations`, which the edge builder's `PART_OF` also uses,
    so a passage that gets a node always gets an edge and vice versa.
    """
    nodes: list[GraphNode] = []
    for passage_id in cited:
        row: PassageRow | None = inputs.passages_by_id.get(passage_id)
        if row is None:
            raise PassageNotInCatalogError(
                f"{passage_id!r} is cited by this run but absent from passages.jsonl")
        properties = _properties(
            shared,
            passage_id=row.passage_id,
            document_id=row.document_id,
            passage_kind=row.passage_kind,
            text=row.text,
            section_id=row.section_id,
            heading_path=_string_list(row.heading_path, what=f"{passage_id}.heading_path"),
            table_id=row.table_id,
            char_count=row.char_count,
            source_url=row.source_url,
        )
        nodes.append(GraphNode(
            key=passage_node_key(row.passage_id),
            base_label="Passage",
            labels=("Passage",),
            properties=properties,
        ))
    return tuple(nodes)


def _document_nodes(
    inputs: ExtractionRunInputs, cited: Sequence[str], shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """The documents of the cited passages — 185 (§3.1), 49 of them cited by a claim."""
    document_ids = documents_of(inputs, cited)
    nodes: list[GraphNode] = []
    for document_id in document_ids:
        row: DocumentRow | None = inputs.documents_by_id.get(document_id)
        if row is None:
            raise DocumentNotInCatalogError(
                f"{document_id!r} owns a cited passage but is absent from documents.jsonl")
        properties = _properties(
            shared,
            document_id=row.document_id,
            form=row.form,
            filing_date=row.filing_date,
            report_date=row.report_date,
            accession=row.accession,
            source_url=row.source_url,
            document_type=row.document_type,
            title=row.title,
            company_name=row.company_name,
            cik10=row.cik10,
        )
        nodes.append(GraphNode(
            key=document_node_key(row.document_id),
            base_label="Document",
            labels=("Document",),
            properties=properties,
        ))
    return tuple(nodes)


# -- :Issue ------------------------------------------------------------------------------


def _issue_nodes(
    inputs: ExtractionRunInputs, shared: Mapping[str, Any]
) -> tuple[GraphNode, ...]:
    """One node per `issues.jsonl` row, plus one per rejection that no issue row mirrors.

    Two non-exclusive labels. `:NotAttempted` marks the `NO_STORED_ANSWER` rows so a view can
    exclude a bound of *this run* from a finding about the corpus. `:Rejected` marks the rows a
    `rejected_claims.jsonl` row mirrors, joined on the shared hex rather than re-derived.

    **An `assemble`-origin rejection writes no issue row** (`jsonl_catalog.py:98-121`), and
    until 2026-08-03 this builder called that "a documented shape, not a loss" and emitted
    nothing for it — no node, no label, no count. That was wrong on its own terms: §10
    criterion 5 requires refusals to *appear* as `:Issue`, and a refusal visible in neither
    file the graph reads is a silently dropped refusal, which is the one thing §6.4 exists to
    prevent. It is keyed on `rejection_id`, which the row does have (§4.3's rule — the run's
    own id, never a minted one). It carries **no `issue_id` property**, because it has no
    issue row; that absence is the honest signal that the two files are not summable (§2.2
    P10), where a fabricated one would erase the distinction the plan insists on. Zero such
    rows in this run, so no count moves — this is the code path that stops being silent the
    day one appears.
    """
    rejection_by_issue: dict[str, RejectedClaimRow] = {
        issue.issue_id: rejection
        for rejection, issue in inputs.rejection_issue_join.pairs
    }
    nodes: list[GraphNode] = []
    for row in (*inputs.issues, *inputs.rejection_issue_join.unmirrored):
        key = issue_row_key(row)
        rejection = (rejection_by_issue.get(key) if isinstance(row, IssueRow) else row)
        properties = _properties(
            shared,
            **({"issue_id": row.issue_id} if isinstance(row, IssueRow) else {}),
            code=row.code,
            severity=row.severity,
            lane=row.lane,
            passage_id=row.passage_id,
            document_id=row.document_id,
            document_type=row.document_type,
            concept_ids=_string_list(row.concept_ids, what=f"{key}.concept_ids"),
            row_label=row.row_label,
            quoted_span=row.quoted_span,
            request_sha256=row.request_sha256,
            rejected_claim=row.rejected_claim,
            detail=row.detail,
        )
        labels = ["Issue"]
        if row.code == NOT_ATTEMPTED_CODE:
            labels.append("NotAttempted")
        if rejection is not None:
            labels.append("Rejected")
            properties["rejection_id"] = rejection.rejection_id
            properties["refused_by"] = rejection.refused_by
            if rejection.raw_finding is not None:
                # **Pre-validation model output, and never to be read as fact.** Its
                # `value` / `unit` / `scale` are frequently the reason the claim was refused
                # (§2.3). It travels as one opaque JSON string so no query can reach a field of
                # it by accident and mistake a refused reading for a filed one.
                properties["raw_finding_json"] = json.dumps(
                    rejection.raw_finding, sort_keys=True)
        nodes.append(GraphNode(
            key=key,
            base_label="Issue",
            labels=tuple(labels),
            properties=properties,
        ))
    return tuple(nodes)


__all__ = [
    "EVENT_PROPERTY_PREFIX",
    "NOT_ATTEMPTED_CODE",
    "PROMOTED_METADATA_KEYS",
    "PROVENANCE_KEYS",
    "DocumentNotInCatalogError",
    "MalformedEventPropertyError",
    "NodeProjectionError",
    "PassageNotInCatalogError",
    "ProvenanceCollisionError",
    "UndeclaredEntityTypeError",
    "build_nodes",
]
