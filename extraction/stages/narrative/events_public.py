"""Public contract for the ontology-guided event and relationship lane.

Four things and nothing else, the same four `public.py` holds for the metric lane: the issue
vocabulary, the vocabulary the ontology offers a passage, the response schema the model is
constrained to, and the structured result a passage produces.

**Why this lane lives in the narrative package rather than in a stage of its own.** Exactly one
stage may name a provider — `tests/extraction/test_provider.py` fixes it as this one and
forbids any other stage naming a provider *at all* — so events either come through this port
or through a second adapter the same test forbids. Four modules beside the metric lane's five,
mirroring its concerns one for one: a contract, a pure prompt builder, an orchestration, and
the boundary that turns an untrusted answer into a typed payload or refuses it.

**The vocabulary is the whole event category, and that is the routing decision.** All nineteen
declared event types carry `aliases = ()`, so there is no surface for the lexical scope to
match and nothing a semantic ranker tuned on metric text reaches reliably. Nineteen is small
enough that offering the category entire *is* the retrieval: it has no threshold, no rank cut
and no parameter to fit, which is what makes it the smallest general mechanism available. The
relationship menu is then derived from the event types' own `allowed_relationships`, which
gives that declaration its first runtime consumer.

**The abstention vocabulary is this lane's, not the benchmark's.** Codes are reused from
`stages/tables/public.py` and `core/models.py` wherever the meaning is identical, per
STAGE_10 §4, and named for what a lane can assess otherwise. The reviewed cases spell some of
their expectations differently; that disagreement is a measurement the report reports, not a
thing to fix by copying names across.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.models import (
    NOT_THE_SUBJECT_COMPANY,
    LaneAbstention,
    LaneEvent,
    LaneRelationship,
    LaneResult,
)
from ..tables.public import MISSING_REQUIRED_CONTEXT
from .public import (
    MODEL_ANSWER_UNUSABLE,
    PROMPT_EXCEEDS_CONTEXT,
    QUOTED_SPAN_NOT_IN_PASSAGE,
)

EVENT_LANE_NAME = "normalized_narrative_events"
EVENT_LANE_VERSION = "1.0.0"

# -- issue codes -------------------------------------------------------------------------------

# The answer named an event type, a role, an entity type, a property or a predicate the
# ontology does not declare for what it was attached to. Five codes rather than one because
# each names a different disagreement with the vocabulary, and a report that collapsed them
# could not say which declaration the model was working against.
EVENT_TYPE_OUT_OF_SCOPE = "EVENT_TYPE_OUT_OF_SCOPE"
UNDECLARED_PARTICIPANT_ROLE = "UNDECLARED_PARTICIPANT_ROLE"
PARTICIPANT_TYPE_NOT_ACCEPTED = "PARTICIPANT_TYPE_NOT_ACCEPTED"
MISSING_REQUIRED_PARTICIPANT = "MISSING_REQUIRED_PARTICIPANT"
PARTICIPANT_CARDINALITY_EXCEEDED = "PARTICIPANT_CARDINALITY_EXCEEDED"
UNDECLARED_EVENT_PROPERTY = "UNDECLARED_EVENT_PROPERTY"

# A property value that is not printed in the passage. Property values are the one free-text
# field an event payload carries into the graph, so the same rule the metric lane applies to
# `population_definition_raw` applies here: it must be a span of the passage, or an ISO date
# derived from one, or it does not travel.
PROPERTY_VALUE_NOT_IN_PASSAGE = "PROPERTY_VALUE_NOT_IN_PASSAGE"

# The answer chose a date the passage does not print. The schema constrains the choice to the
# printed dates, so this is the defensive half of that constraint rather than its enforcement.
DATE_NOT_PRINTED = "DATE_NOT_PRINTED"

# The event type declares a temporal requirement the passage does not support. **Asked of the
# definition, never hard-coded**: `required_temporal_fields` must all be present and at least
# one `required_temporal_any_of` must be, and which fields those are is the event type's
# business. An event refused here is one the vocabulary would refuse anyway; refusing at the
# boundary is what keeps a recorded reason attached to it.
EVENT_TEMPORAL_REQUIREMENT_UNMET = "EVENT_TEMPORAL_REQUIREMENT_UNMET"

# A participant the filing describes and does not name. **Not a rejection** — the event is
# still emitted, carrying an explicit unresolved placeholder — but a recorded finding, because
# an unresolved endpoint that looked like a resolved one is how a debt obligation gets
# attributed to the wrong entity.
PARTICIPANT_NOT_NAMED = "PARTICIPANT_NOT_NAMED"

# The edge names a predicate that no event type this answer emitted declares in its
# `allowed_relationships`. V1 scopes edges to the events that license them: the ontology says
# which predicates an event type can carry, and an edge with no event behind it is a graph
# assertion this stage is not chartered to make.
RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT = "RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT"

# An endpoint type the predicate does not accept, per `allowed_source_types` /
# `allowed_target_types`.
RELATIONSHIP_ENDPOINT_TYPE_INVALID = "RELATIONSHIP_ENDPOINT_TYPE_INVALID"

# The passage reports no event of any declared type. A first-class answer: silence and
# abstention are different answers and `ClaimLane`'s contract already says so.
NO_EVENT_REPORTED = "NO_EVENT_REPORTED"

# A fact about a third party rather than about the filing company — a prior employer named in
# an appointment announcement is the shape this exists for. Imported rather than re-spelled:
# the metric lane refuses the same thing under the same name.
THIRD_PARTY = NOT_THE_SUBJECT_COMPANY

# What the *model* may choose, deliberately narrower than `ISSUE_CODES`. Every other code is a
# decision the mapping boundary makes about an answer, and offering the model a reason it
# cannot assess invites it to pick one instead of answering.
#
# **`EVENT_TEMPORAL_REQUIREMENT_UNMET` was offered here and was withdrawn** *(measured
# 2026-08-02)*. It is decided by comparing an answer against an event type's declared
# `required_temporal_fields`, which is a question about the vocabulary rather than about the
# passage. Offered it, the 9B model abstained from a workforce reduction whose occurrence date
# the passage prints in the sentence it quoted — "the passage … does not state when the company
# announced this event" — reading a JSON field it must fill as a fact it must have. Withdrawing
# it is the same rule this list already states, applied to a code that broke it.
MODEL_ABSTENTION_REASONS: tuple[str, ...] = (
    NO_EVENT_REPORTED,
    PARTICIPANT_NOT_NAMED,
    THIRD_PARTY,
    MISSING_REQUIRED_CONTEXT,
)

ISSUE_CODES = frozenset(MODEL_ABSTENTION_REASONS) | frozenset({
    EVENT_TEMPORAL_REQUIREMENT_UNMET, EVENT_TYPE_OUT_OF_SCOPE, UNDECLARED_PARTICIPANT_ROLE, PARTICIPANT_TYPE_NOT_ACCEPTED,
    MISSING_REQUIRED_PARTICIPANT, PARTICIPANT_CARDINALITY_EXCEEDED,
    UNDECLARED_EVENT_PROPERTY, PROPERTY_VALUE_NOT_IN_PASSAGE,
    DATE_NOT_PRINTED, RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT,
    RELATIONSHIP_ENDPOINT_TYPE_INVALID, QUOTED_SPAN_NOT_IN_PASSAGE,
    MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT,
})

# Codes recorded with `rejected_claim` False that the model nevertheless did not choose: they
# are decisions the lane took with no answer in front of it, on either side of a request rather
# than in it. The metric lane's report subtracts the same three; this list is its analogue and
# is declared here so the report reads it rather than restating it.
LANE_DECISIONS_WITHOUT_AN_ANSWER: tuple[str, ...] = (
    MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT)

# -- the answer's own sentinels ------------------------------------------------------------------

# The one date answer that is not a date the passage prints. **A date enum without it cannot
# express the true answer**, which is the same defect V1 §8a.13 found in `period_label` and
# §4.0b found in `required_temporal_fields`: a press release that dates its own announcement
# and never states when the appointment takes effect must be answerable, and every other
# member of the enum would be a fabrication. Mapped to "this field is absent", never to the
# other date.
DATE_NOT_STATED = "(the passage does not state this date)"

# The one entity answer that is not a name. A participant a filing describes but declines to
# identify is a participant the
# filing describes and refuses to identify; the alternatives are dropping the participant and
# defaulting it to the parent, and the second records a debt against the wrong entity.
ENTITY_NOT_NAMED = "(the passage does not name this entity)"


@dataclass(frozen=True)
class OfferedVocabulary:
    """Everything the ontology offers a passage, and the whole of what the prompt may carry.

    Built by `offered_vocabulary` from the loaded definitions and from nothing else — no
    passage, no ranking, no configuration. That is what makes the routing claim checkable: the
    menu is a function of the vocabulary alone, so it cannot have been fitted to a passage.
    """

    #: Every declared event type, sorted. The category entire — see the module docstring.
    event_type_ids: tuple[str, ...]
    #: Every participant role any event type declares.
    roles: tuple[str, ...]
    #: Every entity type any participant role accepts, plus every endpoint type the offered
    #: predicates accept, so an answer can name an endpoint that is not also a participant.
    entity_types: tuple[str, ...]
    #: Every property name any event type declares.
    property_names: tuple[str, ...]
    #: Every predicate the offered event types declare in `allowed_relationships`.
    relationship_ids: tuple[str, ...]
    #: `(instance_id, printed surface)` for every entity instance this ontology declares.
    #: Carried so the mapping boundary can recognise the registrant's own printed name without
    #: importing a registry — the four instances are the whole of what entity *identity* means
    #: in V1, and everything else a passage names becomes a slug.
    instance_surfaces: tuple[tuple[str, str], ...] = ()

    def __len__(self) -> int:
        return len(self.event_type_ids)


def offered_vocabulary(ontology) -> OfferedVocabulary:
    """The declared event category, and the roles, types, properties and edges it implies.

    Every tuple below is read off the definitions. Nothing is named here, which is the
    property that makes this routing rather than a lexicon: adding an event type to the
    ontology puts it on the menu with no edit, and no wording from any passage can reach it.
    """
    event_types = sorted(ontology.registry.by_category("event_type"),
                         key=lambda definition: definition.concept_id)
    roles: set[str] = set()
    entity_types: set[str] = set()
    properties: set[str] = set()
    predicates: set[str] = set()
    for definition in event_types:
        for participant in definition.participants or ():
            roles.add(str(participant.role))
            entity_types.update(str(t) for t in participant.entity_types)
        properties.update(str(name) for name in (definition.allowed_properties or ()))
        predicates.update(str(name) for name in (definition.allowed_relationships or ()))

    for predicate in sorted(predicates):
        declaration = ontology.registry.relationship(predicate)
        if declaration is None:
            continue
        entity_types.update(str(t) for t in declaration.allowed_source_types)
        entity_types.update(str(t) for t in declaration.allowed_target_types)

    surfaces = sorted(
        (instance.instance_id, surface)
        for instance in ontology.registry.definitions.instances
        for surface in {instance.instance_id, str(getattr(instance, "label", "") or "")}
        if surface
    )
    return OfferedVocabulary(
        event_type_ids=tuple(definition.concept_id for definition in event_types),
        roles=tuple(sorted(roles)),
        entity_types=tuple(sorted(entity_types)),
        property_names=tuple(sorted(properties)),
        relationship_ids=tuple(sorted(predicates)),
        instance_surfaces=tuple(surfaces),
    )


@dataclass(frozen=True)
class EventIssue:
    """A structured non-payload, carrying enough to reproduce the decision.

    `raw_finding` is the model's own object, untouched, for the reason STAGE_10 §5 gives: a
    rejection without the thing that was rejected cannot be reviewed.
    """

    code: str
    detail: str
    passage_id: str
    event_type_ids: tuple[str, ...] = ()
    quoted_span: str | None = None
    raw_finding: dict[str, Any] | None = None
    #: True when the model proposed a payload and this lane refused it; False when the model
    #: itself declined, or when the lane recorded a finding about a payload it still emitted.
    rejected_claim: bool = False


@dataclass
class EventExtraction:
    """Everything one passage produced: events, edges, and explained silences alike."""

    passage_id: str
    events: list[LaneEvent] = field(default_factory=list)
    relationships: list[LaneRelationship] = field(default_factory=list)
    issues: list[EventIssue] = field(default_factory=list)
    #: The digest of the request that produced this, or None where none was issued. Determined
    #: by `answer_store.request_identity` over (prompt, schema, model, sampling parameters), so
    #: it is a property of the run rather than a measurement of it and may enter a
    #: byte-identical artifact.
    request_sha256: str | None = None

    @property
    def rejected(self) -> tuple[EventIssue, ...]:
        return tuple(issue for issue in self.issues if issue.rejected_claim)

    def counts_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return dict(sorted(counts.items()))

    def as_lane_result(self) -> LaneResult:
        """The `ClaimLane` view.

        `LaneResult.claims` is typed as metric claims and stays empty: this lane emits no
        metric observation and pretending otherwise would put an event into a field every
        downstream consumer reads as one. What crosses the protocol is the passage, the lane
        name and the abstentions — enough for a caller to see that the passage was read and
        what it produced, with the events themselves read off `EventExtraction`, exactly as
        `NarrativeExtraction` is the richer view the metric report reads.
        """
        return LaneResult(
            passage_id=self.passage_id,
            lane=EVENT_LANE_NAME,
            claims=(),
            abstentions=tuple(
                LaneAbstention(
                    reason=issue.code,
                    detail=issue.detail,
                    passage_id=issue.passage_id,
                    candidate_metric_ids=(),
                    row_label=None,
                    rejected_claim=issue.rejected_claim,
                )
                for issue in self.issues
            ),
        )


def response_schema(
    vocabulary: OfferedVocabulary, date_phrases: tuple[str, ...] = ()
) -> dict:
    """The JSON Schema the provider constrains generation with.

    Three arrays rather than one tagged union, for the reason `public.response_schema` gives:
    a single array would need every field of every shape present on every element, and a
    grammar that forces a field to exist is a grammar that gets it invented.

    Every property is required inside its own object. llama.cpp builds a GBNF grammar from
    this and an optional property is the one a model silently omits on the hard cases; an empty
    string is a statement this lane can check, while a missing key is a hole it would have to
    guess about.

    **The two date fields are enums over the dates the passage prints, plus `DATE_NOT_STATED`.**
    This is the same design that removed a whole class of wrong period from the metric lane, and
    the same escape hatch that design later needed: the answer cannot state a date, only point
    at one the passage prints, and it can say that the passage prints none. Nothing maps one
    date field onto the other, here or anywhere downstream — they are different facts about
    different days (V1_CLAIM_EXTRACTION §4.0b).

    A passage that prints no date at all degenerates both enums to `[DATE_NOT_STATED]`. That is
    correct and it is structural: an event type requiring `occurred_on` is then unanswerable on
    that passage, and the refusal says so rather than inventing a filing date.
    """
    dates = [*date_phrases, DATE_NOT_STATED]
    # `named_in_passage` is a field rather than a sentinel string, and the difference was
    # measured *(2026-08-02)*. Asked to type `ENTITY_NOT_NAMED` for a participant the filing
    # describes and does not name, the 9B model typed the description instead — "a subsidiary
    # of the Company" — which is a perfectly good `entity_text` and a silently wrong id. A
    # boolean is a question a constrained grammar answers reliably, and it keeps the printed
    # description where it belongs instead of trading it for a sentinel.
    participant = {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": list(vocabulary.roles)},
            "entity_name": {"type": "string"},
            "entity_type": {"type": "string", "enum": list(vocabulary.entity_types)},
            "named_in_passage": {"type": "boolean"},
        },
        "required": ["role", "entity_name", "entity_type", "named_in_passage"],
        "additionalProperties": False,
    }
    event_property = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": list(vocabulary.property_names)},
            "value": {"type": "string"},
        },
        "required": ["name", "value"],
        "additionalProperties": False,
    }
    event_item = {
        "type": "object",
        # `evidence_sentence` first, for the reason the metric schema puts it first: llama.cpp
        # emits properties in schema order, so the filing's own sentence is copied before
        # anything is asserted about it. Quote, then answer.
        "properties": {
            "evidence_sentence": {"type": "string"},
            "event_type_id": {"type": "string", "enum": list(vocabulary.event_type_ids)},
            "occurrence_date": {"type": "string", "enum": dates},
            "announcement_date": {"type": "string", "enum": dates},
            "participants": {"type": "array", "items": participant},
            "properties": {"type": "array", "items": event_property},
        },
        "required": ["evidence_sentence", "event_type_id", "occurrence_date",
                     "announcement_date", "participants", "properties"],
        "additionalProperties": False,
    }
    relationship_item = {
        "type": "object",
        "properties": {
            "evidence_sentence": {"type": "string"},
            "relationship_id": {"type": "string",
                                "enum": list(vocabulary.relationship_ids)},
            "source_name": {"type": "string"},
            "source_type": {"type": "string", "enum": list(vocabulary.entity_types)},
            "source_named_in_passage": {"type": "boolean"},
            "target_name": {"type": "string"},
            "target_type": {"type": "string", "enum": list(vocabulary.entity_types)},
            "target_named_in_passage": {"type": "boolean"},
        },
        "required": ["evidence_sentence", "relationship_id", "source_name", "source_type",
                     "source_named_in_passage", "target_name", "target_type",
                     "target_named_in_passage"],
        "additionalProperties": False,
    }
    abstention_item = {
        "type": "object",
        "properties": {
            "evidence_sentence": {"type": "string"},
            "reason": {"type": "string", "enum": list(MODEL_ABSTENTION_REASONS)},
            "detail": {"type": "string"},
        },
        "required": ["evidence_sentence", "reason", "detail"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "events": {"type": "array", "items": event_item},
            "relationships": {"type": "array", "items": relationship_item},
            "abstentions": {"type": "array", "items": abstention_item},
        },
        "required": ["events", "relationships", "abstentions"],
        "additionalProperties": False,
    }
