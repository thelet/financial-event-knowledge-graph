"""Prompt construction for the event and relationship lane. Pure, versioned, one place.

Pure for the reason `prompt.py` is pure: the prompt is half of the request identity that makes
replay possible, so a prompt varying with the clock, the filesystem or a set's iteration order
would make every stored answer unreachable on the next run. Nothing here reads a file, asks the
time, or iterates an unordered collection.

**What the prompt carries is a closed list**: the passage verbatim, the ontology's declared
event types rendered from their own declarations, the dates the passage prints, the passage's
own metadata, and the response schema. No other passage, no previously extracted payload, no
gold, and no wording that is not either the ontology's or the passage's.

`PassageContext` is the metric lane's, reused rather than re-declared: the passage metadata an
event needs and the metadata a metric observation needs are the same five fields, and two
records of one thing is how two prompts start disagreeing about what a passage is.
"""

from __future__ import annotations

import json

from .events_public import DATE_NOT_STATED, OfferedVocabulary
from .prompt import PassageContext, _HEADING_BUDGET

EVENT_PROMPT_VERSION = "1.0.0"


def render_event_type(definition) -> str:
    """One declared event type as one block, with everything a refusal turns on.

    Every line is read off the definition, and each corresponds to a check in
    `event_mapping.py`: a role the type does not declare, an entity type the role does not
    accept, a property the type does not allow, and a temporal requirement it does state are
    all rejections, and refusing an answer against a rule the prompt never stated would be
    scoring the lane on a rule the model was not given.

    The temporal line is rendered from `required_temporal_fields` and
    `required_temporal_any_of` rather than described in prose, because those two are what the
    boundary asks and the whole point of V1 §4.0b is that which dates an event type needs is
    the event type's business and not the lane's.
    """
    parts = [
        f"- {definition.concept_id}",
        f'label: "{definition.label}"',
        "means: " + " ".join(str(definition.description or "").split()),
        "participants: " + "; ".join(
            f"{participant.role} "
            f"({'required' if participant.required else 'optional'}"
            f", {'exactly one' if participant.cardinality == 'one' else 'one or more'}"
            f", entity_type one of: {', '.join(participant.entity_types)})"
            for participant in (definition.participants or ())),
    ]
    if definition.allowed_properties:
        parts.append("properties you may state: " + ", ".join(definition.allowed_properties))
    required = tuple(definition.required_temporal_fields or ())
    alternatives = tuple(definition.required_temporal_any_of or ())
    clauses = []
    if required:
        clauses.append("must have " + " and ".join(required))
    if alternatives:
        clauses.append("must have at least one of " + " or ".join(alternatives))
    parts.append("dates: " + ("; ".join(clauses) if clauses else "none required"))
    if definition.allowed_relationships:
        parts.append("relationships it may carry: "
                     + ", ".join(definition.allowed_relationships))
    return "\n    ".join(parts)


def render_event_types(definitions) -> str:
    """Definitions in the order given. The caller sorts; sorting twice would hide a caller bug."""
    return "\n".join(render_event_type(definition) for definition in definitions)


def render_predicate(definition) -> str:
    """One declared predicate as one line, with the endpoint types it accepts.

    The endpoint types are here because `event_mapping` refuses an endpoint the predicate does
    not accept, and a prompt that named the predicate without its declarations would be asking
    the model to guess at a rule it is then judged on. Measured 2026-08-02: with the predicates
    listed as bare names, the lane emitted **no** relationship on either passage whose gold
    declares one.
    """
    return (
        f"- {definition.relationship_id}: {definition.label}"
        f" — {' '.join(str(definition.description or '').split())}"
        f"\n    source_type one of: {', '.join(definition.allowed_source_types)}"
        f"\n    target_type one of: {', '.join(definition.allowed_target_types)}"
    )


def render_predicates(definitions) -> str:
    return "\n".join(render_predicate(definition) for definition in definitions)


def build_prompt(
    *,
    text: str,
    definitions,
    predicates,
    vocabulary: OfferedVocabulary,
    context: PassageContext,
    schema: dict,
    date_phrases: tuple[str, ...] = (),
    subject_type: str = "public_company",
) -> str:
    """The exact string sent to the provider. Same inputs, same bytes, always.

    The rules are written as refusals rather than as encouragements and each is paired with a
    check in `event_mapping.py`. Rules 3 and 4 are the announcement-versus-occurrence rule of
    V1 §4.0b stated to the model in the model's own terms; nothing downstream will repair a
    confusion between them, because repairing it would mean deciding which day the filing meant
    without evidence.
    """
    heading = " > ".join(context.heading_path)[:_HEADING_BUDGET]
    return "\n".join((
        "You are extracting CORPORATE EVENTS and RELATIONSHIPS from ONE passage of an SEC "
        f"filing by {context.subject_label}.",
        "",
        "PASSAGE METADATA",
        f"  document type: {context.document_type}",
        f"  filing form: {context.form or 'unknown'}",
        f"  filing date: {context.filing_date or 'unknown'}",
        f"  section: {heading or '(none)'}",
        f"  the filing company: {context.subject_entity_id} "
        f"({context.subject_label}), entity_type {subject_type}",
        "",
        "  The filing date is metadata about the DOCUMENT. It is never an answer to any "
        "question below.",
        "",
        "EVENT TYPES THE ONTOLOGY DECLARES — you may name these and no others:",
        render_event_types(definitions),
        "",
        "RELATIONSHIP PREDICATES — you may name these and no others. Each is declared by an",
        "event type above, so an edge belongs to an event you are reporting:",
        render_predicates(predicates),
        "",
        # The only dates this passage prints. The schema constrains both date fields to exactly
        # this list, so it is shown rather than left implicit: a grammar that silently refuses
        # every other answer would otherwise look like an arbitrary failure.
        "DATES THIS PASSAGE PRINTS — occurrence_date and announcement_date must each be one",
        "of these, or the last line:",
        ("\n".join(f'- "{phrase}"' for phrase in date_phrases)
         if date_phrases else "  (none — this passage prints no calendar date)"),
        f'- "{DATE_NOT_STATED}"',
        "",
        "RULES",
        "  1. Report only events this passage actually reports. Never infer an event from the",
        "     absence of one, from a heading, or from what a filing of this kind usually",
        "     contains. If the passage reports no event, return an empty events list and say",
        "     so in abstentions.",
        "  2. evidence_sentence comes FIRST in every object and must be a SINGLE CONTIGUOUS",
        "     run of characters from the passage — as if dragged with a highlighter from one",
        "     start point to one end point, skipping NOTHING in between. It must be the",
        "     sentence that reports the event. If you cannot copy such a run, do not report",
        "     the event.",
        "  3. THE TWO DATES ARE TWO SEPARATE QUESTIONS ABOUT TWO DIFFERENT DAYS. Answer each",
        "     one on its own and never let one answer the other.",
        "       occurrence_date  — On which day did the thing HAPPEN or BECOME EFFECTIVE?",
        "                          Answer only if the passage says so, in words about the",
        "                          event itself: \"On <date>, a subsidiary entered into\",",
        "                          \"effective <date>\", \"was completed on <date>\".",
        "       announcement_date — On which day did the company SAY SO? Answer only if the",
        "                          passage says so, in words about the telling: a press-release",
        "                          dateline, \"on <date> the Company announced\", \"today",
        "                          announced\" beside a dateline.",
        "     Most passages answer only one of the two. When that happens, give that one and",
        f'     answer "{DATE_NOT_STATED}" for the other. That is a COMPLETE and CORRECT answer,',
        "     and you should still report the event: an event does not need both dates.",
        "     \"On <date>, a subsidiary entered into a facility\" states an occurrence and NO",
        "     announcement. \"On <date> the Company announced a reduction\" states an",
        "     announcement and NO occurrence — the reduction itself is undated. Giving both",
        "     fields the same date is correct only when the passage says BOTH things about",
        "     that day. Never use the filing date or the report date for either: they are",
        "     metadata about the document and evidence about neither.",
        "  4. participants: use only the roles listed for the event type you chose, only an",
        "     entity_type that role accepts, and no more of a role than it allows. Report ONE",
        "     event per change: two people appointed in one sentence are two events, each with",
        "     its own officer, not one event with two.",
        "  5. entity_name is the entity's name as the passage prints it, and",
        "     named_in_passage says whether the passage names it at all.",
        f'       - the filing company itself: name it "{context.subject_label}",',
        "         named_in_passage true, however the passage refers to it;",
        "       - an entity the passage describes but never names — \"a subsidiary of the",
        "         Company\" — copy that description into entity_name and set named_in_passage",
        "         FALSE. Do NOT substitute the filing company for it: that records the fact",
        "         against the wrong entity.",
        "  6. A person's position at ANOTHER company is a fact about that other company. Do",
        "     not attach it to the filing company as a role or an edge. Put it in abstentions.",
        "  7. properties: use only the property names listed for the event type you chose, and",
        "     every value must be text printed in the passage — a quoted phrase, or a date the",
        "     passage prints. Never estimate a number the passage does not give and never",
        "     classify in your own words; omit the property instead and say so in abstentions.",
        "  8. RELATIONSHIPS ARE NOT OPTIONAL. For every event you report, read its",
        "     \"relationships it may carry\" line. For each predicate listed there, if the",
        "     passage states that relation between two entities, emit one relationships entry",
        "     for it. Its endpoints are normally two of the participants you already named, so",
        "     reuse their names, types and named flags; the endpoint types must be ones the",
        "     predicate accepts. Emit no edge that no event you are reporting lists.",
        "  9. Every fact must be supported by the passage alone. Declining is a correct",
        "     answer; guessing is not.",
        "",
        "Answer with JSON matching this schema exactly:",
        json.dumps(schema, sort_keys=True, separators=(",", ":")),
        "",
        "PASSAGE:",
        text,
    ))
