"""An untrusted model answer to typed events and edges, or to recorded refusals.

Responsibility: decide, for each object the model returned, whether it becomes a `LaneEvent` or
a `LaneRelationship` or an `EventIssue`. Boundaries: it is handed a parsed dict, the passage
text and an ontology. It never calls a provider, never opens a file and never sees a prompt,
which is what lets every refusal below be tested with a literal dict and no GPU.

**Nothing here repairs.** Every condition rejects the finding, records the raw object that
caused it, and stops. The three repairs a reader will reach for are the three this module
exists to refuse:

**Dating an event to the document that reports it.** The filing date, the report date and the
dateline are three different days on the executive-change 8-K in this corpus, and the passage
supports exactly one of them as an *announcement*. `occurred_on` and `announced_on` are read
from the passage or left absent; neither is ever derived from the other or from metadata
(V1_CLAIM_EXTRACTION §4.0b). Which of them an event type needs is asked of the definition —
`required_temporal_fields` and `required_temporal_any_of` — and is nowhere written down here.

**Substituting the parent for an unnamed participant.** A filing that describes a participant
and declines to identify it — a subsidiary named only as one — must not have the registrant
put in its place: that records a debt obligation against the wrong entity. Dropping it loses
the fact, so it becomes an explicit unresolved placeholder and a recorded finding.

**Attaching a third party's role to the registrant.** An appointment announcement often names
the incoming officer's *previous* employer in the same sentence as the new post, so a boundary
keyed on the job title attaches a role at another company to the filer. Rule 6 of the prompt
asks the model to abstain; nothing here can enforce it, and the report measures whether it did.

Neither failure is described with a sentence from a benchmark passage. The corpus is what these
rules were learned from, but a module that quotes a fixture invites the reader to wonder
whether the behaviour was fitted to it, and the answer has to be visibly no.

Entity **resolution** is deliberately not attempted. A printed name becomes a readable id
through `core.identifiers.entity_id`, and a name that *is* an instance this ontology declares
becomes that instance's id. Deciding that two different spellings denote one entity needs a
corpus-wide pass V1 does not have, and no ontology constraint reads an entity id anyway — only
entity types are validated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.concept_resolution import fold
from ...core.identifiers import entity_id, unresolved_entity_id
from ...core.models import LaneEvent, LaneEventParticipant, LaneRelationship
from ...core.periods import date_phrases, parse_printed_date
from ...core.text_spans import LocatedSpan, locate
from .events_public import (
    DATE_NOT_PRINTED,
    DATE_NOT_STATED,
    ENTITY_NOT_NAMED,
    EVENT_LANE_NAME,
    EVENT_TEMPORAL_REQUIREMENT_UNMET,
    EVENT_TYPE_OUT_OF_SCOPE,
    MISSING_REQUIRED_PARTICIPANT,
    PARTICIPANT_CARDINALITY_EXCEEDED,
    PARTICIPANT_NOT_NAMED,
    PARTICIPANT_TYPE_NOT_ACCEPTED,
    PROPERTY_VALUE_NOT_IN_PASSAGE,
    QUOTED_SPAN_NOT_IN_PASSAGE,
    RELATIONSHIP_ENDPOINT_TYPE_INVALID,
    RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT,
    UNDECLARED_EVENT_PROPERTY,
    UNDECLARED_PARTICIPANT_ROLE,
    EventExtraction,
    EventIssue,
    OfferedVocabulary,
)

# The two temporal fields a `LaneEvent` can carry, and the names the ontology's event types use
# for them. Read from the definition's own requirement list, so an event type asking for a
# field this lane cannot supply — `earnings_release` requires `period_start` and `period_end` —
# is refused with its own requirement quoted back, rather than emitted incomplete.
_TEMPORAL_FIELDS: tuple[str, ...] = ("occurred_on", "announced_on")


@dataclass(frozen=True)
class EventMappingContext:
    """Everything the boundary needs that is not the answer itself.

    A frozen record rather than eight parameters, because the same context maps every finding
    in one answer and threading it through by hand is how one check ends up asking a different
    passage than another.
    """

    passage_id: str
    document_id: str
    passage_text: str
    vocabulary: OfferedVocabulary
    subject_entity_id: str = "opendoor"
    subject_type: str = "public_company"
    extractor_metadata: dict[str, Any] = field(default_factory=dict)


def map_answer(content: dict, *, ontology, context: EventMappingContext) -> EventExtraction:
    """The whole boundary. One parsed answer in, payloads and recorded refusals out.

    Events are mapped before relationships because an edge is licensed by an event: the
    ontology declares `allowed_relationships` on the event type, and an edge whose predicate no
    surviving event declares is refused rather than emitted on its own.
    """
    extraction = EventExtraction(passage_id=context.passage_id)

    for finding in _objects(content.get("events")):
        outcome, findings = _map_event(finding, ontology=ontology, context=context)
        extraction.issues.extend(findings)
        if outcome is not None:
            extraction.events.append(outcome)

    licensed = _licensed_predicates(extraction.events, ontology)
    for finding in _objects(content.get("relationships")):
        outcome, issue = _map_relationship(
            finding, ontology=ontology, context=context, licensed=licensed)
        if issue is not None:
            extraction.issues.append(issue)
        if outcome is not None:
            extraction.relationships.append(outcome)

    for finding in _objects(content.get("abstentions")):
        extraction.issues.append(_map_abstention(finding, context=context))

    return extraction


# -- one proposed event ---------------------------------------------------------------------------


def _map_event(
    finding: dict, *, ontology, context: EventMappingContext
) -> tuple[LaneEvent | None, list[EventIssue]]:
    """Every refusal, in the order that makes one explainable.

    Ordered most-fundamental first: which event type, then the evidence, then who took part,
    then when, then what was said about it. A property complaint about an event type the model
    invented would name the wrong problem.

    Returns the event and *a list* of findings rather than one, because an emitted event can
    still carry a recorded finding: an unnamed participant is not a rejection and must not be
    silent either.
    """
    reject = _rejector(finding, context)
    type_id = _text(finding.get("event_type_id"))

    if not type_id or type_id not in context.vocabulary.event_type_ids:
        return None, [reject(EVENT_TYPE_OUT_OF_SCOPE,
                             f"{type_id!r} is not an event type this ontology declares")]

    definition = ontology.registry.event_type(type_id)
    if definition is None:
        return None, [reject(EVENT_TYPE_OUT_OF_SCOPE,
                             f"{type_id!r} is not a concept in the loaded ontology",
                             type_id)]

    span = _locate(context.passage_text, _text(finding.get("evidence_sentence")))
    if span is None:
        return None, [reject(QUOTED_SPAN_NOT_IN_PASSAGE,
                             "the quoted span is not present in the passage", type_id)]

    participants, notes, participant_error = _resolve_participants(
        finding, definition=definition, context=context, reject=reject)
    if participant_error is not None:
        return None, [participant_error]

    dates, date_error = _resolve_dates(finding, context=context, reject=reject,
                                       type_id=type_id)
    if date_error is not None:
        return None, [date_error]

    unmet = _unmet_temporal_requirement(definition, dates)
    if unmet:
        return None, [reject(EVENT_TEMPORAL_REQUIREMENT_UNMET, unmet, type_id)]

    properties, property_refusals = _resolve_properties(
        finding, definition=definition, context=context, reject=reject, type_id=type_id)
    notes.extend(property_refusals)

    return LaneEvent(
        event_type_id=type_id,
        occurred_on=dates["occurred_on"],
        announced_on=dates["announced_on"],
        participants=participants,
        properties=properties,
        passage_id=context.passage_id,
        document_id=context.document_id,
        # The passage's own bytes, never the model's transcription, so `validate_quoted_text`
        # compares the catalog against itself.
        raw_text=span.text,
        source_lane=EVENT_LANE_NAME,
        assertion_type="reported",
        extractor_metadata={
            **context.extractor_metadata,
            "occurrence_date_text": _text(finding.get("occurrence_date")),
            "announcement_date_text": _text(finding.get("announcement_date")),
            "span_match": span.basis,
            "span_char_start": span.start,
            "span_char_end": span.end,
        },
    ), notes


def _resolve_participants(finding, *, definition, context, reject):
    """The four checks `check_event_participants` makes, applied before an event is emitted.

    Applied here as well as there on purpose. The ontology would reject the same event at
    validation; refusing it at the boundary is what keeps a stated reason attached to the
    refusal and keeps V1 §11's "zero ontology errors" a property of the lane rather than of
    luck.
    """
    declared = {participant.role: participant for participant in (definition.participants or ())}
    resolved: list[LaneEventParticipant] = []
    notes: list[EventIssue] = []
    seen: dict[str, int] = {}

    for raw in _objects(finding.get("participants")):
        role = _text(raw.get("role"))
        expected = declared.get(role)
        if expected is None:
            return (), [], reject(
                UNDECLARED_PARTICIPANT_ROLE,
                f"{definition.concept_id} declares no participant role {role!r} "
                f"(declared: {sorted(declared)})",
                definition.concept_id)
        entity_type = _text(raw.get("entity_type"))
        if entity_type not in expected.entity_types:
            return (), [], reject(
                PARTICIPANT_TYPE_NOT_ACCEPTED,
                f"role {role!r} does not accept entity type {entity_type!r} "
                f"(allowed: {list(expected.entity_types)})",
                definition.concept_id)
        seen[role] = seen.get(role, 0) + 1
        if expected.cardinality == "one" and seen[role] > 1:
            return (), [], reject(
                PARTICIPANT_CARDINALITY_EXCEEDED,
                f"role {role!r} accepts one participant and the answer gives {seen[role]}",
                definition.concept_id)

        printed = _text(raw.get("entity_name"))
        identifier, named = _resolve_entity(
            printed, entity_type, context=context,
            named_in_passage=raw.get("named_in_passage"))
        resolved.append(LaneEventParticipant(
            role=role, entity_id=identifier, entity_type=entity_type,
            entity_text=printed, named=named))
        if not named:
            notes.append(EventIssue(
                code=PARTICIPANT_NOT_NAMED,
                detail=f"the passage describes the {role!r} of "
                       f"{definition.concept_id} and does not name it; recorded as the "
                       f"unresolved placeholder {identifier!r} for entity resolution",
                passage_id=context.passage_id,
                event_type_ids=(definition.concept_id,),
                quoted_span=_text(finding.get("evidence_sentence")) or None,
                raw_finding=dict(raw),
                rejected_claim=False,
            ))

    for role, expected in sorted(declared.items()):
        if expected.required and not seen.get(role):
            return (), [], reject(
                MISSING_REQUIRED_PARTICIPANT,
                f"{definition.concept_id} requires a {role!r} participant and the answer "
                "names none",
                definition.concept_id)
    return tuple(resolved), notes, None


def _resolve_dates(finding, *, context, reject, type_id):
    """Both dates, each read from a phrase the passage prints, and never from each other.

    The schema constrains each field to the printed dates plus `DATE_NOT_STATED`, so the
    check below is the defensive half of a constraint the grammar already enforces. It is kept
    because the grammar is the provider's and this boundary must hold with a stub, a replayed
    answer, or a different server behind it.
    """
    printed = set(date_phrases(context.passage_text))
    values: dict[str, str | None] = {}
    for field_name, key in (("occurred_on", "occurrence_date"),
                            ("announced_on", "announcement_date")):
        answer = _text(finding.get(key))
        if not answer or answer == DATE_NOT_STATED:
            values[field_name] = None
            continue
        phrase = " ".join(answer.split())
        if phrase not in printed:
            return {}, reject(
                DATE_NOT_PRINTED,
                f"{key} {phrase[:60]!r} is not a date this passage prints "
                f"({sorted(printed)})",
                type_id)
        values[field_name] = parse_printed_date(phrase)
    return values, None


def _unmet_temporal_requirement(definition, dates: dict[str, str | None]) -> str | None:
    """The event type's own requirement, asked of the event type. Never hard-coded.

    `required_temporal_fields` must all be present; `required_temporal_any_of`, when declared,
    needs at least one. A required field this payload cannot carry at all — `earnings_release`
    asks for `period_start` and `period_end` — is unmet by the same rule and says so, which is
    a truer answer than emitting an event with the field silently absent.
    """
    missing = [name for name in (definition.required_temporal_fields or ())
               if not dates.get(name)]
    if missing:
        return (f"{definition.concept_id} requires {missing} and the passage states "
                f"{[name for name in _TEMPORAL_FIELDS if dates.get(name)] or 'no date'}")
    alternatives = tuple(definition.required_temporal_any_of or ())
    if alternatives and not any(dates.get(name) for name in alternatives):
        return (f"{definition.concept_id} requires at least one of {list(alternatives)} and "
                "the passage states neither")
    return None


def _resolve_properties(finding, *, definition, context, reject, type_id):
    """Declared names only, and every value must be printed in the passage.

    A property value is the one free-text field an event carries into the graph, so it gets the
    rule `population_definition_raw` gets on the metric side: the passage's own slice or
    nothing. A value that is exactly a printed calendar date is recorded as the ISO date it
    resolves to, so every date in a payload has one form — the same normalisation `occurred_on`
    and `announced_on` receive, applied for the same reason.

    **A refused property drops the property, not the event**, and that is a deliberate
    departure from the metric lane, where a refusal drops the claim. A metric observation *is*
    its value, so a value that cannot be supported leaves nothing behind; an event is the fact
    that something happened, and an unsupportable attribute of it is a smaller thing than the
    event. Dropping the event over one would discard a fact the passage plainly reports. It is
    still a refusal and still recorded with the object that caused it — measured
    2026-08-02, the 9B model answered `change_kind: "appointment"`, its own classification of a
    sentence that prints no such word, and the executive change itself was fine.
    """
    allowed = set(definition.allowed_properties or ())
    resolved: dict[str, str] = {}
    refusals: list[EventIssue] = []
    for raw in _objects(finding.get("properties")):
        name = _text(raw.get("name"))
        if name not in allowed:
            refusals.append(reject(
                UNDECLARED_EVENT_PROPERTY,
                f"{definition.concept_id} does not declare a {name!r} property "
                f"(allowed: {sorted(allowed)}); the property is dropped and the event kept",
                type_id))
            continue
        value = _text(raw.get("value"))
        located = _locate(context.passage_text, value) if value else None
        if located is None:
            refusals.append(reject(
                PROPERTY_VALUE_NOT_IN_PASSAGE,
                f"the value {value[:60]!r} given for {name!r} is not printed in the passage; "
                "the property is dropped and the event kept",
                type_id))
            continue
        resolved[name] = _as_date_if_it_is_one(located.text)
    return resolved, refusals


def _as_date_if_it_is_one(text: str) -> str:
    """A printed calendar date as ISO; anything else unchanged.

    Applied only when the whole value *is* the date — "October 31, 2023" becomes
    `2023-10-31`, while "following the outbreak of the COVID-19 pandemic" and "recorded on
    October 31, 2023 in the notes" are left exactly as the passage prints them. A value that
    merely contains a date is prose, and rewriting prose is not normalisation.
    """
    collapsed = " ".join(text.split())
    phrases = date_phrases(collapsed)
    if len(phrases) == 1 and phrases[0] == collapsed:
        return parse_printed_date(collapsed) or collapsed
    return collapsed if collapsed != text else text


# -- one proposed relationship --------------------------------------------------------------------


def _licensed_predicates(events, ontology) -> frozenset[str]:
    """The predicates the events this passage produced are declared to be able to carry."""
    licensed: set[str] = set()
    for event in events:
        definition = ontology.registry.event_type(event.event_type_id)
        if definition is not None:
            licensed.update(str(name) for name in (definition.allowed_relationships or ()))
    return frozenset(licensed)


def _map_relationship(finding, *, ontology, context, licensed):
    reject = _rejector(finding, context)
    predicate = _text(finding.get("relationship_id"))

    if predicate not in context.vocabulary.relationship_ids:
        return None, reject(
            RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT,
            f"{predicate!r} is not a predicate any declared event type carries")
    if predicate not in licensed:
        return None, reject(
            RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT,
            f"no event this passage produced declares {predicate!r} in its "
            "allowed_relationships, so the edge has no event behind it")

    # Keyed by `relationship_id`, the upper-case one, because that is the index
    # `validate_relationship` reads. `registry.find` searches the concept-id index instead and
    # answers for `holds_position_at`, which the validator then refuses as an unknown
    # predicate — the defect the benchmark's own gold carried until 2026-08-02.
    definition = ontology.registry.relationship(predicate)
    if definition is None:
        return None, reject(
            RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT,
            f"{predicate!r} does not resolve in the relationship index")

    span = _locate(context.passage_text, _text(finding.get("evidence_sentence")))
    if span is None:
        return None, reject(QUOTED_SPAN_NOT_IN_PASSAGE,
                            "the quoted span is not present in the passage")

    source_type = _text(finding.get("source_type"))
    target_type = _text(finding.get("target_type"))
    if source_type not in definition.allowed_source_types:
        return None, reject(
            RELATIONSHIP_ENDPOINT_TYPE_INVALID,
            f"{source_type!r} is not allowed as source of {predicate} "
            f"(allowed: {list(definition.allowed_source_types)})")
    if target_type not in definition.allowed_target_types:
        return None, reject(
            RELATIONSHIP_ENDPOINT_TYPE_INVALID,
            f"{target_type!r} is not allowed as target of {predicate} "
            f"(allowed: {list(definition.allowed_target_types)})")

    source, _ = _resolve_entity(
        _text(finding.get("source_name")), source_type, context=context,
        named_in_passage=finding.get("source_named_in_passage"))
    target, _ = _resolve_entity(
        _text(finding.get("target_name")), target_type, context=context,
        named_in_passage=finding.get("target_named_in_passage"))
    return LaneRelationship(
        relationship_id=predicate,
        source_id=source,
        source_type=source_type,
        target_id=target,
        target_type=target_type,
        passage_id=context.passage_id,
        document_id=context.document_id,
        raw_text=span.text,
        source_lane=EVENT_LANE_NAME,
        assertion_type="reported",
        extractor_metadata={
            **context.extractor_metadata,
            "source_name": _text(finding.get("source_name")),
            "target_name": _text(finding.get("target_name")),
            "span_match": span.basis,
        },
    ), None


# -- entities ---------------------------------------------------------------------------------------


def _resolve_entity(printed: str, entity_type: str, *, context, named_in_passage=None):
    """A printed name to a readable id, and whether the filing named it at all.

    Three answers, in order:

    1. the answer says the passage does not name it, or no name arrived at all — an explicit
       unresolved placeholder, `named` False. The printed description is kept on the
       participant as `entity_text` so a later entity-resolution pass has the words to work
       from;
    2. a name that **is** a declared instance of this ontology, compared whole after folding —
       that instance's id, so the registrant reaches the graph as `opendoor` and not as
       `opendoor_technologies_inc`;
    3. anything else — the slug of the printed name.

    Step 2 compares the *whole* name rather than searching for an instance surface inside it.
    Searching would resolve "Opendoor Labs Inc." to `opendoor`, which is exactly the
    entity-resolution judgement this stage is not chartered to make.
    """
    name = " ".join((printed or "").split())
    if named_in_passage is False or not name or name == ENTITY_NOT_NAMED:
        return unresolved_entity_id(context.subject_entity_id, entity_type), False
    folded = _folded(name)
    for instance_id, surface in context.vocabulary.instance_surfaces:
        if folded == _folded(surface):
            return instance_id, True
    return entity_id(name), True


def _folded(text: str) -> str:
    return " ".join(fold(text or "").lower().replace(".", " ").replace(",", " ").split())


# -- an abstention the model chose -------------------------------------------------------------------


def _map_abstention(finding: dict, *, context: EventMappingContext) -> EventIssue:
    """Recorded as offered. The schema constrains the reason, so there is nothing to refuse."""
    quoted = _text(finding.get("evidence_sentence"))
    located = _locate(context.passage_text, quoted) if quoted else None
    return EventIssue(
        code=_text(finding.get("reason")) or "NO_EVENT_REPORTED",
        detail=_text(finding.get("detail")),
        passage_id=context.passage_id,
        quoted_span=located.text if located else None,
        raw_finding=dict(finding),
        rejected_claim=False,
    )


# -- answer shape --------------------------------------------------------------------------------------


def _locate(text: str, candidate: str) -> LocatedSpan | None:
    """`core.text_spans.locate` with this package's typographic fold bound in."""
    return locate(text, candidate, fold=fold)


def _rejector(finding: dict, context: EventMappingContext):
    def reject(code: str, detail: str, type_id: str = "") -> EventIssue:
        return EventIssue(
            code=code,
            detail=detail,
            passage_id=context.passage_id,
            event_type_ids=(type_id,) if type_id else (),
            quoted_span=_text(finding.get("evidence_sentence")) or None,
            raw_finding=dict(finding),
            rejected_claim=True,
        )
    return reject


def _objects(value) -> tuple[dict, ...]:
    return tuple(item for item in (value or ()) if isinstance(item, dict))


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""
