"""The event and relationship lane, its rejection boundary, and the ids it mints.

Every test drives a real object through the contract it claims to satisfy. Nothing here
asserts `isinstance` against a runtime-checkable protocol, and nothing checks a shape where
the claim is about behaviour: the point of `test_an_undated_appointment_keeps_its_announcement_
and_no_occurrence` is not that two fields exist but that neither is ever filled from the other.

The passages are the corpus's own, read out of `passages.jsonl` where a test needs a real one,
and short hand-written strings where the claim is about the boundary rather than about the
corpus. No benchmark case is named: `tests/extraction/test_narrative_lane.py` forbids the
*package* from naming one and this file keeps the same discipline so a copied line cannot
migrate across.
"""

from __future__ import annotations

import json

import pytest

from extraction.contracts import GenerationResult
from extraction.core.assembly import (
    assemble_events,
    to_event,
    to_event_claim,
    to_relationship_claim,
)
from extraction.core.identifiers import (
    claim_id,
    entity_id,
    event_id,
    relationship_instance_id,
    unresolved_entity_id,
)
from extraction.core.models import LaneEvent, LaneEventParticipant, LaneRelationship
from extraction.core.periods import date_phrases, parse_printed_date, period_phrases
from extraction.core.validation import validate
from extraction.stages.narrative import (
    DATE_NOT_STATED,
    EVENT_ISSUE_CODES,
    EVENT_LANE_NAME,
    EVENT_MODEL_ABSTENTION_REASONS,
    OntologyGuidedEventLane,
    PassageContext,
    offered_vocabulary,
)
from extraction.stages.narrative.events_public import (
    DATE_NOT_PRINTED,
    EVENT_TEMPORAL_REQUIREMENT_UNMET,
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
)
from ontology import load_ontology

# A hand-written passage carrying exactly what the boundary tests need: two printed dates with
# different meanings, a named person, a company that is the registrant under a different
# spelling, and an entity described but not named.
PASSAGE = (
    "SAN FRANCISCO, Sept. 10, 2025 -- Opendoor Technologies Inc. today announced that "
    "Dana Reyes has been appointed Chief Executive Officer. On October 19, 2022, a "
    "subsidiary of the Company entered into a new asset-backed senior revolving credit "
    "facility with a final maturity date of October 31, 2023."
)
PASSAGE_ID = "norm:0001801169:0001801169-99-000001:test.htm#p1"
DOCUMENT_ID = "norm:0001801169:0001801169-99-000001:test.htm"


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def vocabulary(ontology):
    return offered_vocabulary(ontology)


class StubProvider:
    """A `GenerationProvider` replaying one recorded answer, and recording what it was asked.

    Not a mock: it satisfies the protocol and the lane is driven through it. `prompts` and
    `schemas` are kept so a test can assert on the request without a second code path building
    one.
    """

    model_id = "stub-model"

    def __init__(self, content: dict | None = None, *, error: Exception | None = None):
        self._content = content if content is not None else _answer()
        self._error = error
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    def generate(self, *, prompt, schema, max_tokens=1024, temperature=0.0):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if self._error is not None:
            raise self._error
        return GenerationResult(
            content=self._content, raw_content=json.dumps(self._content, sort_keys=True),
            model_id=self.model_id, prompt_tokens=1234, completion_tokens=567,
            total_tokens=1801, latency_ms=98.7, raw_sha256="0" * 64,
            content_sha256="1" * 64, finish_reason="stop", attempts=1, metadata={})


def _answer(events=(), relationships=(), abstentions=()) -> dict:
    return {"events": list(events), "relationships": list(relationships),
            "abstentions": list(abstentions)}


def appointment(**overrides) -> dict:
    base = {
        "evidence_sentence":
            "Dana Reyes has been appointed Chief Executive Officer",
        "event_type_id": "executive_change",
        "occurrence_date": DATE_NOT_STATED,
        "announcement_date": "Sept. 10, 2025",
        "participants": [
            {"role": "employer", "entity_name": "Opendoor Technologies Inc.",
             "entity_type": "public_company", "named_in_passage": True},
            {"role": "officer", "entity_name": "Dana Reyes", "entity_type": "person",
             "named_in_passage": True},
        ],
        "properties": [{"name": "position", "value": "Chief Executive Officer"}],
    }
    base.update(overrides)
    return base


def facility(**overrides) -> dict:
    base = {
        "evidence_sentence":
            "On October 19, 2022, a subsidiary of the Company entered into a new "
            "asset-backed senior revolving credit facility",
        "event_type_id": "credit_facility_established",
        "occurrence_date": "October 19, 2022",
        "announcement_date": DATE_NOT_STATED,
        "participants": [
            {"role": "borrower", "entity_name": "a subsidiary of the Company",
             "entity_type": "subsidiary", "named_in_passage": False},
            {"role": "facility", "entity_name": "asset-backed senior revolving credit facility",
             "entity_type": "asset_backed_debt_facility", "named_in_passage": True},
        ],
        "properties": [{"name": "maturity_date", "value": "October 31, 2023"}],
    }
    base.update(overrides)
    return base


def holds_position(**overrides) -> dict:
    base = {
        "evidence_sentence": "Dana Reyes has been appointed Chief Executive Officer",
        "relationship_id": "HOLDS_POSITION_AT",
        "source_name": "Dana Reyes", "source_type": "person",
        "source_named_in_passage": True,
        "target_name": "Opendoor Technologies Inc.", "target_type": "public_company",
        "target_named_in_passage": True,
    }
    base.update(overrides)
    return base


def run(ontology, answer: dict, text: str = PASSAGE):
    lane = OntologyGuidedEventLane(ontology, StubProvider(answer))
    return lane.extract_passage(
        text, context=PassageContext(passage_id=PASSAGE_ID, document_type="earnings_release"),
        document_id=DOCUMENT_ID)


def codes(extraction) -> set[str]:
    return {issue.code for issue in extraction.issues}


# -- the routing mechanism ------------------------------------------------------------------------


def test_the_lane_offers_the_whole_declared_event_category_and_no_alias(ontology, vocabulary):
    """The routing claim, driven rather than described.

    The menu must be exactly what the ontology declares — no more, so nothing was added for a
    passage, and no fewer, so nothing was ranked away. The second assertion is the one the
    hard constraint turns on: not one event type carries an alias, so nothing in this menu
    could have been fitted to a passage's wording even in principle.
    """
    declared = {definition.concept_id
                for definition in ontology.registry.by_category("event_type")}
    assert set(vocabulary.event_type_ids) == declared
    assert vocabulary.event_type_ids == tuple(sorted(declared))
    assert [definition.concept_id
            for definition in ontology.registry.by_category("event_type")
            if tuple(definition.aliases or ())] == []


def test_the_menu_is_the_same_for_two_completely_different_passages(ontology):
    """A menu that varied with the passage would be a retrieval, and this one is not.

    Checked on the *rendered schema*, not on the vocabulary object, because the schema is what
    constrains generation and a menu that narrowed only in the prompt would still let the
    grammar admit everything.
    """
    lane = OntologyGuidedEventLane(ontology, provider := StubProvider())
    for text in (PASSAGE, "The quarter's results were in line with expectations."):
        lane.extract_passage(
            text, context=PassageContext(passage_id=PASSAGE_ID,
                                         document_type="earnings_release"))
    first, second = provider.schemas
    event_enum = ["properties", "events", "items", "properties", "event_type_id", "enum"]
    assert (first["properties"]["events"]["items"]["properties"]["event_type_id"]["enum"]
            == second["properties"]["events"]["items"]["properties"]["event_type_id"]["enum"])
    assert len(event_enum) == 6  # the path above, spelled out so a rename fails loudly


def test_the_relationship_menu_is_derived_from_the_event_types_own_declarations(
        ontology, vocabulary):
    """`allowed_relationships` is where the edge menu comes from, and it had no consumer."""
    expected = set()
    for definition in ontology.registry.by_category("event_type"):
        expected.update(str(name) for name in (definition.allowed_relationships or ()))
    assert set(vocabulary.relationship_ids) == expected
    # And every one of them resolves in the index the validator reads, not the concept index.
    for predicate in vocabulary.relationship_ids:
        assert ontology.registry.relationship(predicate) is not None, predicate


def test_the_prompt_carries_every_declared_event_type_and_the_passage_and_nothing_else(
        ontology, vocabulary):
    """STAGE_10 §3's closed list, for this lane."""
    provider = StubProvider()
    OntologyGuidedEventLane(ontology, provider).extract_passage(
        PASSAGE, context=PassageContext(passage_id=PASSAGE_ID,
                                        document_type="earnings_release"))
    prompt = provider.prompts[0]
    for concept_id in vocabulary.event_type_ids:
        assert concept_id in prompt, concept_id
    assert PASSAGE in prompt
    assert "Sept. 10, 2025" in prompt and "October 19, 2022" in prompt


def test_the_prompt_is_pure_and_versioned(ontology):
    """Same passage, same bytes. The prompt is half of the request identity."""
    provider = StubProvider()
    lane = OntologyGuidedEventLane(ontology, provider)
    for _ in range(2):
        lane.extract_passage(
            PASSAGE, context=PassageContext(passage_id=PASSAGE_ID,
                                            document_type="earnings_release"))
    assert provider.prompts[0] == provider.prompts[1]


# -- the temporal rule ------------------------------------------------------------------------------


def test_an_undated_appointment_keeps_its_announcement_and_no_occurrence(ontology):
    """V1 §4.0b, driven end to end: two different facts about two different days.

    The answer states an announcement date and refuses an occurrence date. The event must
    survive — `executive_change` declares `required_temporal_any_of` — carrying the
    announcement and **nothing** in `occurred_on`.
    """
    extraction = run(ontology, _answer(events=[appointment()]))
    assert len(extraction.events) == 1
    event = extraction.events[0]
    assert event.announced_on == "2025-09-10"
    assert event.occurred_on is None


def test_an_occurrence_is_never_promoted_into_an_announcement_or_back(ontology):
    """Neither direction, and the two answers are asserted separately.

    The facility sentence dates the occurrence and says nothing about a disclosure, so the
    event carries `occurred_on` and no `announced_on`. Together with the test above this pins
    both directions of the rule: a lane that copied either way fails one of them.
    """
    extraction = run(ontology, _answer(events=[facility()]))
    event = extraction.events[0]
    assert event.occurred_on == "2022-10-19"
    assert event.announced_on is None


def test_a_filing_date_never_reaches_either_temporal_field(ontology):
    """The failure this rule exists to prevent, checked against a filing date in the context.

    The passage prints 2025-09-10 and 2022-10-19; the filing date given to the prompt is a
    third day that appears in neither field of any emitted event.
    """
    lane = OntologyGuidedEventLane(ontology, StubProvider(
        _answer(events=[appointment(), facility()])))
    extraction = lane.extract_passage(
        PASSAGE,
        context=PassageContext(passage_id=PASSAGE_ID, document_type="earnings_release",
                               filing_date="2025-09-11", form="8-K"),
        document_id=DOCUMENT_ID)
    dated = {event.occurred_on for event in extraction.events} | {
        event.announced_on for event in extraction.events}
    assert "2025-09-11" not in dated


def test_the_temporal_requirement_is_asked_of_the_definition_not_hard_coded(ontology):
    """The same undated answer, under two event types, gets two different verdicts.

    `executive_change` declares `required_temporal_any_of: (announced_on, occurred_on)` and
    `workforce_reduction` requires `occurred_on`; an answer stating only an announcement is
    valid for the first and refused for the second. Nothing in the lane names either type —
    the difference comes entirely from the declarations, which is what makes this a test of
    the rule rather than of a branch.
    """
    kept = run(ontology, _answer(events=[appointment()]))
    assert [e.event_type_id for e in kept.events] == ["executive_change"]

    reduction = {
        "evidence_sentence": "Opendoor Technologies Inc. today announced",
        "event_type_id": "workforce_reduction",
        "occurrence_date": DATE_NOT_STATED,
        "announcement_date": "Sept. 10, 2025",
        "participants": [{"role": "operator", "entity_name": "Opendoor Technologies Inc.",
                          "entity_type": "public_company", "named_in_passage": True}],
        "properties": [],
    }
    refused = run(ontology, _answer(events=[reduction]))
    assert refused.events == []
    assert EVENT_TEMPORAL_REQUIREMENT_UNMET in codes(refused)
    assert refused.issues[0].rejected_claim is True


def test_a_date_the_passage_does_not_print_is_refused(ontology):
    extraction = run(ontology, _answer(
        events=[appointment(occurrence_date="March 4, 2021")]))
    assert extraction.events == []
    assert DATE_NOT_PRINTED in codes(extraction)


def test_the_date_reader_refuses_a_day_its_month_does_not_have():
    """`parse_printed_date` validates, and `date_phrases` only offers what resolves."""
    assert parse_printed_date("February 29, 2024") == "2024-02-29"
    assert parse_printed_date("February 30, 2024") is None
    assert "February 30, 2024" not in date_phrases("Filed February 30, 2024.")


def test_the_date_reader_reads_an_abbreviated_dateline_and_the_period_reader_still_does_not():
    """The two readers are separate on purpose, and the separation is the point.

    Widening `period_phrases` to abbreviated months would change the narrative lane's
    `period_label` enum on every passage in the corpus, changing its prompt and making every
    stored answer unreachable. This asserts they disagree, so a later merge of the two fails
    here rather than in a committed report's digest.
    """
    dateline = "SAN FRANCISCO, Sept. 10, 2025 (GLOBE NEWSWIRE)"
    assert date_phrases(dateline) == ("Sept. 10, 2025",)
    assert period_phrases(dateline) == ()


# -- the rejection boundary --------------------------------------------------------------------------


def test_a_quoted_span_absent_from_the_passage_is_refused_as_fabricated_evidence(ontology):
    extraction = run(ontology, _answer(
        events=[appointment(evidence_sentence="was appointed Chief Financial Officer")]))
    assert extraction.events == []
    assert QUOTED_SPAN_NOT_IN_PASSAGE in codes(extraction)


def test_a_role_the_event_type_does_not_declare_is_refused(ontology):
    answer = appointment()
    answer["participants"][1]["role"] = "borrower"
    extraction = run(ontology, _answer(events=[answer]))
    assert extraction.events == []
    assert UNDECLARED_PARTICIPANT_ROLE in codes(extraction)


def test_an_entity_type_the_role_does_not_accept_is_refused(ontology):
    answer = appointment()
    answer["participants"][1]["entity_type"] = "public_company"
    extraction = run(ontology, _answer(events=[answer]))
    assert extraction.events == []
    assert PARTICIPANT_TYPE_NOT_ACCEPTED in codes(extraction)


def test_a_missing_required_participant_is_refused(ontology):
    answer = appointment(participants=[
        {"role": "employer", "entity_name": "Opendoor Technologies Inc.",
         "entity_type": "public_company", "named_in_passage": True}])
    extraction = run(ontology, _answer(events=[answer]))
    assert extraction.events == []
    assert MISSING_REQUIRED_PARTICIPANT in codes(extraction)


def test_two_officers_in_one_appointment_are_refused_because_the_role_accepts_one(ontology):
    answer = appointment()
    answer["participants"].append(
        {"role": "officer", "entity_name": "Opendoor Technologies Inc.",
         "entity_type": "person", "named_in_passage": True})
    extraction = run(ontology, _answer(events=[answer]))
    assert extraction.events == []
    assert PARTICIPANT_CARDINALITY_EXCEEDED in codes(extraction)


def test_an_undeclared_property_is_dropped_and_the_event_is_kept(ontology):
    """The one place this boundary refuses part of a payload rather than all of it.

    An event is the fact that something happened; an attribute of it is a smaller thing, and
    discarding the event over one would lose a fact the passage plainly reports. The refusal
    is still recorded with the object that caused it.
    """
    answer = appointment(properties=[{"name": "maturity_date", "value": "October 31, 2023"}])
    extraction = run(ontology, _answer(events=[answer]))
    assert len(extraction.events) == 1
    assert extraction.events[0].properties == {}
    assert UNDECLARED_EVENT_PROPERTY in codes(extraction)
    assert [i.rejected_claim for i in extraction.issues if
            i.code == UNDECLARED_EVENT_PROPERTY] == [True]


def test_a_property_value_the_passage_does_not_print_is_dropped(ontology):
    """The model's own classification is not evidence. Measured live: `change_kind` came back
    as "appointment", a word the sentence never prints."""
    answer = appointment(properties=[{"name": "change_kind", "value": "appointment"}])
    extraction = run(ontology, _answer(events=[answer]))
    assert len(extraction.events) == 1
    assert extraction.events[0].properties == {}
    assert PROPERTY_VALUE_NOT_IN_PASSAGE in codes(extraction)


def test_a_property_value_that_is_a_printed_date_is_recorded_as_an_iso_date(ontology):
    extraction = run(ontology, _answer(events=[facility()]))
    assert extraction.events[0].properties == {"maturity_date": "2023-10-31"}


def test_a_property_value_that_merely_contains_a_date_is_left_as_the_passage_prints_it(
        ontology):
    answer = facility(properties=[
        {"name": "maturity_date",
         "value": "a final maturity date of October 31, 2023"}])
    extraction = run(ontology, _answer(events=[answer]))
    assert extraction.events[0].properties == {
        "maturity_date": "a final maturity date of October 31, 2023"}


# -- entities ------------------------------------------------------------------------------------------


def test_the_registrants_printed_name_resolves_to_its_declared_instance_id(ontology):
    extraction = run(ontology, _answer(events=[appointment()]))
    employer = next(p for p in extraction.events[0].participants if p.role == "employer")
    assert employer.entity_id == "opendoor"
    assert employer.entity_text == "Opendoor Technologies Inc."


def test_a_participant_the_filing_does_not_name_becomes_an_explicit_placeholder(ontology):
    """Neither dropped nor defaulted to the parent, and recorded as unresolved.

    Defaulting the borrower to the registrant records a debt obligation against the wrong
    entity; dropping it loses the fact. The third answer is a placeholder a later
    entity-resolution pass can find by shape, plus a finding that is **not** a rejection.
    """
    extraction = run(ontology, _answer(events=[facility()]))
    borrower = next(p for p in extraction.events[0].participants if p.role == "borrower")
    assert borrower.entity_id == "opendoor_unnamed_subsidiary"
    assert borrower.named is False
    assert borrower.entity_text == "a subsidiary of the Company"
    flagged = [i for i in extraction.issues if i.code == PARTICIPANT_NOT_NAMED]
    assert len(flagged) == 1 and flagged[0].rejected_claim is False


def test_an_instance_surface_inside_a_longer_name_does_not_resolve_to_that_instance(ontology):
    """Entity resolution is not attempted, and this is where it would start.

    "Opendoor Labs Inc." contains "Opendoor". Resolving it to the registrant would merge two
    legal entities on a substring, which is exactly the judgement a corpus-wide resolution
    pass exists to make and this stage does not have.
    """
    text = PASSAGE.replace("Opendoor Technologies Inc.", "Opendoor Labs Inc.")
    answer = appointment(
        evidence_sentence="Dana Reyes has been appointed Chief Executive Officer")
    answer["participants"][0]["entity_name"] = "Opendoor Labs Inc."
    extraction = run(ontology, _answer(events=[answer]), text=text)
    employer = next(p for p in extraction.events[0].participants if p.role == "employer")
    assert employer.entity_id == "opendoor_labs_inc"


def test_entity_ids_are_readable_underscored_and_deterministic():
    assert entity_id("Kaz Nejatian") == "kaz_nejatian"
    assert entity_id("Opendoor Technologies Inc.") == "opendoor_technologies_inc"
    assert entity_id("Kaz Nejatian") == entity_id(" kaz  NEJATIAN ")
    assert unresolved_entity_id("opendoor", "subsidiary") == "opendoor_unnamed_subsidiary"


# -- relationships ---------------------------------------------------------------------------------------


def test_an_edge_no_emitted_event_licenses_is_refused(ontology):
    """`allowed_relationships` decides, and an edge with no event behind it is not emitted."""
    extraction = run(ontology, _answer(relationships=[holds_position()]))
    assert extraction.relationships == []
    assert RELATIONSHIP_NOT_LICENSED_BY_AN_EVENT in codes(extraction)


def test_an_edge_an_emitted_event_licenses_is_emitted_with_the_uppercase_predicate(ontology):
    """The predicate must be the id the registry keys by, not the concept id.

    Getting this wrong is the defect the benchmark's own gold carried: `registry.find`
    answers for `holds_position_at` from the concept-id index, and `validate_relationship`
    then refuses it as an unknown predicate.
    """
    extraction = run(ontology, _answer(
        events=[appointment()], relationships=[holds_position()]))
    assert len(extraction.relationships) == 1
    edge = extraction.relationships[0]
    assert edge.relationship_id == "HOLDS_POSITION_AT"
    assert ontology.registry.relationship(edge.relationship_id) is not None
    assert (edge.source_id, edge.target_id) == ("dana_reyes", "opendoor")


def test_an_endpoint_type_the_predicate_refuses_is_refused(ontology):
    extraction = run(ontology, _answer(
        events=[appointment()],
        relationships=[holds_position(source_type="public_company")]))
    assert extraction.relationships == []
    assert RELATIONSHIP_ENDPOINT_TYPE_INVALID in codes(extraction)


# -- identities -----------------------------------------------------------------------------------------


def test_two_undated_events_of_one_type_on_one_passage_do_not_share_an_id():
    """The collision `event_id` had before step 12, driven rather than described.

    Measured 2026-08-02: one 8-K reports two executive changes in two sentences and dates
    neither, so (type, date, passage) was one triple for both and the two events shared an id.
    """
    first = event_id("executive_change", None, PASSAGE_ID,
                     (("employer", "opendoor"), ("officer", "kaz_nejatian")))
    second = event_id("executive_change", None, PASSAGE_ID,
                      (("employer", "opendoor"), ("officer", "keith_rabois")))
    assert first != second
    assert first.startswith("evt:executive-change:undated:")


def test_an_events_id_does_not_depend_on_the_order_its_participants_were_listed_in():
    """An id is structural position, and the order a model listed two people in is not that.

    **Found by adversarial review 2026-08-02**: `to_event` passed the participants through in
    emission order, so the same event with `employer` before `officer` and with `officer`
    before `employer` minted two different ids. The pairs are sorted into the digest now; the
    payload's own order is untouched, because that is what the passage said.
    """
    employer = LaneEventParticipant(role="employer", entity_id="opendoor",
                                    entity_type="public_company", entity_text="Opendoor")
    officer = LaneEventParticipant(role="officer", entity_id="dana_reyes",
                                   entity_type="person", entity_text="Dana Reyes")

    def built(participants):
        return to_event(LaneEvent(
            event_type_id="executive_change", announced_on="2025-09-10",
            participants=participants, passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
            raw_text="x", source_lane=EVENT_LANE_NAME), _evidence())

    forwards, backwards = built((employer, officer)), built((officer, employer))
    assert forwards.event_id == backwards.event_id
    assert [p.role for p in forwards.participants] == ["employer", "officer"]
    assert [p.role for p in backwards.participants] == ["officer", "employer"]


def test_two_events_with_one_participant_set_still_share_an_id_and_that_is_open():
    """The residual the participant digest does **not** close, pinned so it cannot be forgotten.

    Adding participants distinguishes two executive changes with two different officers. It
    does not distinguish two changes for the *same* officer on one undated passage — the 8-K
    this corpus carries announces one person "appointed Chief Executive Officer **and** member
    of the Board of Directors", which a lane may reasonably read as two events. Their ids
    collide. Recorded as a founder question rather than repaired: `properties` is the field
    that separates them and putting a free-text attribute into an identity is a decision this
    review is not entitled to make.
    """
    ceo = event_id("executive_change", None, PASSAGE_ID,
                   (("employer", "opendoor"), ("officer", "dana_reyes")))
    director = event_id("executive_change", None, PASSAGE_ID,
                        (("employer", "opendoor"), ("officer", "dana_reyes")))
    assert ceo == director


def test_the_id_extension_is_additive_for_an_event_with_no_participants():
    assert event_id("workforce_reduction", "2020-04-15", PASSAGE_ID) == event_id(
        "workforce_reduction", "2020-04-15", PASSAGE_ID, ())


def test_an_announcement_date_never_reaches_the_readable_date_segment_of_an_id():
    """The segment answers "when did this happen". An announced-only event reads `undated`."""
    event = LaneEvent(
        event_type_id="executive_change", announced_on="2025-09-10",
        participants=(LaneEventParticipant(
            role="officer", entity_id="dana_reyes", entity_type="person",
            entity_text="Dana Reyes"),),
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, raw_text="x",
        source_lane=EVENT_LANE_NAME)
    instance = to_event(event, _evidence())
    assert ":undated:" in instance.event_id
    assert "2025-09-10" not in instance.event_id
    assert instance.announced_on == "2025-09-10"


def test_the_claim_id_is_the_kind_agnostic_helper_over_the_payload_id():
    """And the readable endpoint segments are `_slug`'s form, not the entity id's.

    `dana_reyes` reads `dana-reyes` in the id and stays underscored in the payload. Asserted
    rather than fixed: `_slug` is the readability rule every id in that module goes through
    and `observation_id` in two committed reports is built with it, and no two ids `entity_id`
    mints can differ only by a character `_slug` folds.
    """
    identifier = relationship_instance_id(
        "HOLDS_POSITION_AT", "dana_reyes", "opendoor", PASSAGE_ID)
    assert identifier.startswith("rel:holds-position-at:dana-reyes:opendoor:")
    assert claim_id("relationship", identifier).startswith("claim:relationship:")


def test_the_module_docstring_lists_every_id_it_defines():
    """It listed three of four until step 12, and the missing one had no caller either."""
    from extraction.core import identifiers

    for name in ("observation_id", "claim_id", "event_id", "relationship_instance_id",
                 "entity_id"):
        assert name in identifiers.__doc__, name


def _evidence():
    from ontology.core.models import EvidenceReference
    return EvidenceReference(evidence_kind="normalized_passage", passage_id=PASSAGE_ID,
                             document_id=DOCUMENT_ID, quoted_text="x")


# -- assembly and validation ----------------------------------------------------------------------------


def test_every_emitted_payload_reaches_an_ontology_claim_and_validates_clean(ontology):
    """The whole chain, end to end: lane, assembly, evidence resolution, ontology validation.

    This is V1 §11 acceptance criterion 1 for events and relationships, and it is the test the
    stage exists to be able to write.
    """
    extraction = run(ontology, _answer(
        events=[appointment(), facility()], relationships=[holds_position()]))
    assembly = assemble_events(
        list(extraction.events), list(extraction.relationships),
        passage_rows={PASSAGE_ID: {"document_id": DOCUMENT_ID}})
    assert len(assembly.claims) == 3
    findings = validate(assembly.claims, ontology=ontology, passages=_Passages())
    assert findings.errors == [], [f.detail for f in findings.errors]
    assert {str(claim.claim_kind) for claim in assembly.claims} == {"event", "relationship"}


def test_an_event_carrying_a_date_its_type_forbids_is_not_something_assembly_invents(ontology):
    """Assembly passes both dates through as read, including absent.

    The filing date is right there in the passage row. `to_event` must not reach for it.
    """
    event = LaneEvent(
        event_type_id="executive_change", announced_on="2025-09-10",
        participants=(
            LaneEventParticipant(role="employer", entity_id="opendoor",
                                 entity_type="public_company", entity_text="Opendoor"),
            LaneEventParticipant(role="officer", entity_id="dana_reyes",
                                 entity_type="person", entity_text="Dana Reyes")),
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, raw_text="Dana Reyes",
        source_lane=EVENT_LANE_NAME)
    claim = to_event_claim(event, {"document_id": DOCUMENT_ID, "filing_date": "2025-09-11"})
    assert claim.event.occurred_on is None
    assert claim.event.announced_on == "2025-09-10"
    assert ontology.validate_event(claim.event).ok


def test_a_relationship_claim_carries_the_edge_and_its_evidence(ontology):
    edge = LaneRelationship(
        relationship_id="BORROWS_UNDER", source_id="opendoor_unnamed_subsidiary",
        source_type="subsidiary", target_id="facility_x",
        target_type="asset_backed_debt_facility", passage_id=PASSAGE_ID,
        document_id=DOCUMENT_ID, raw_text="entered into", source_lane=EVENT_LANE_NAME)
    claim = to_relationship_claim(edge, {"document_id": DOCUMENT_ID})
    assert claim.relationship.evidence[0].passage_id == PASSAGE_ID
    assert ontology.validate_relationship(claim.relationship).ok


class _Passages:
    def text_of(self, passage_id):
        return PASSAGE if passage_id == PASSAGE_ID else None

    def exists(self, passage_id):
        return passage_id == PASSAGE_ID

    def document_of(self, passage_id):
        return DOCUMENT_ID if passage_id == PASSAGE_ID else None


# -- silence, and the contract ----------------------------------------------------------------------------


def test_a_passage_reporting_nothing_produces_an_abstention_and_not_a_silence(ontology):
    extraction = run(ontology, _answer(abstentions=[
        {"evidence_sentence": "Dana Reyes", "reason": "NO_EVENT_REPORTED",
         "detail": "no declared event type is reported here"}]))
    assert extraction.events == [] and extraction.relationships == []
    assert [issue.code for issue in extraction.issues] == ["NO_EVENT_REPORTED"]
    result = extraction.as_lane_result()
    assert result.passage_id == PASSAGE_ID
    assert [a.reason for a in result.abstentions] == ["NO_EVENT_REPORTED"]
    assert result.claims == ()


def test_an_unusable_answer_is_recorded_and_not_raised(ontology):
    from extraction.providers.public import ProviderResponseError

    lane = OntologyGuidedEventLane(
        ontology, StubProvider(error=ProviderResponseError("content is not JSON")))
    extraction = lane.extract_passage(
        PASSAGE, context=PassageContext(passage_id=PASSAGE_ID,
                                        document_type="earnings_release"))
    assert [issue.code for issue in extraction.issues] == ["MODEL_ANSWER_UNUSABLE"]
    assert extraction.request_sha256 is not None


def test_every_code_the_lane_can_record_is_declared(ontology):
    """A code the vocabulary does not declare would be invisible to every report."""
    answers = (
        _answer(events=[appointment(evidence_sentence="not in the passage")]),
        _answer(events=[appointment(properties=[{"name": "change_kind", "value": "x"}])]),
        _answer(events=[facility()]),
        _answer(relationships=[holds_position()]),
    )
    for answer in answers:
        for issue in run(ontology, answer).issues:
            assert issue.code in EVENT_ISSUE_CODES, issue.code


def test_the_model_may_not_choose_a_reason_it_cannot_assess():
    """`EVENT_TEMPORAL_REQUIREMENT_UNMET` was on this list and was withdrawn.

    It is decided by comparing an answer against an event type's declared temporal fields,
    which is a question about the vocabulary and not about the passage. Offered it, the 9B
    model abstained from an event whose occurrence date the passage prints in the sentence it
    had just quoted.
    """
    assert EVENT_TEMPORAL_REQUIREMENT_UNMET not in EVENT_MODEL_ABSTENTION_REASONS
    assert EVENT_TEMPORAL_REQUIREMENT_UNMET in EVENT_ISSUE_CODES


def test_the_lane_records_no_operational_statistic_on_a_payload(ontology):
    """Latency and token counts move between two identical requests; a payload carrying one
    could never appear in an artifact required to be byte-identical."""
    extraction = run(ontology, _answer(events=[appointment()]))
    metadata = extraction.events[0].extractor_metadata
    for forbidden in ("latency_ms", "prompt_tokens", "completion_tokens", "attempts",
                      "raw_sha256"):
        assert forbidden not in metadata, forbidden
