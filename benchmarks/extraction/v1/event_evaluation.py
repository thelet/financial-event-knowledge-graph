"""Scoring the event lane against the reviewed event and relationship cases.

Every score in the event report is computed here and nowhere else; `event_runner.py` composes
and renders and never recomputes a verdict. The defect that discipline exists against is on the
record twice: at step 6 a runner duplicated the verdict logic and printed 46 `WRONG` rows
beside an accuracy of 1.000, and at step 11 three rendered tables were checked by nothing.

**Dimensions are scored independently**, as V1 §4.0 requires, because a lane can be right about
the event type and wrong about the day, and a blended score hides exactly that. `DIMENSIONS`
states all thirteen with the denominator each is taken over.

**The two date dimensions score abstention as an answer.** An event whose gold leaves
`occurred_on` absent is correct only when the lane leaves it absent too: under V1 §4.0b the
announcement date and the occurrence date are different facts about different days, so a lane
that fills in the one it has is wrong, not generous. This is the one place in this benchmark
where emitting *less* is the scored answer.

**`matched_over_emitted` is a ratio and never precision**, for the reason step 6 refuses the
word: each case names a deliberate subset of its passage's events, so an unmatched emitted
event is usually a right answer the case did not list. Unmatched emissions are **non-gold
additions**, never false positives, and they are counted and left unclassified.

It lives under `benchmarks/` and not under `extraction/` because its vocabulary *is* the
benchmark's: gold payloads, expected abstentions and failure categories are things the reviewed
cases declare, not things a lane knows about. `tests/extraction/test_narrative_lane.py` also
fails any module under `extraction/` that so much as names this directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from extraction.stages.narrative.events_public import (
    LANE_DECISIONS_WITHOUT_AN_ANSWER,
    MODEL_ANSWER_UNUSABLE,
    PROMPT_EXCEEDS_CONTEXT,
)

# The two codes that mean "this passage produced no answer to score". Either way there is
# nothing finer to say about it, and an expectation must never be scored as honoured on the
# strength of a silence neither the model nor the lane chose — the flattery step 11 caught this
# benchmark committing.
_NO_USABLE_ANSWER: frozenset[str] = frozenset({MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT})

# Per matched event pair. Each is a separate question the pair can be asked, and each is asked
# only where the reviewed case declares something to compare — a case that annotates no
# property has no property wording to get wrong, and scoring it correct would inflate the ratio
# with pairs that were never at risk.
EVENT_MATCH_DIMENSIONS: tuple[str, ...] = (
    "occurrence_date", "announcement_date", "properties", "event_evidence")

# Per gold participant inside a matched pair. Three, not one: a lane can find the right person
# in the wrong role, or the right role with the wrong type, and a single participant score
# would report those as one failure of an unnamed kind.
PARTICIPANT_DIMENSIONS: tuple[str, ...] = (
    "participant_role", "participant_entity", "participant_entity_type")

# Per matched relationship pair.
RELATIONSHIP_MATCH_DIMENSIONS: tuple[str, ...] = (
    "source_id", "target_id", "source_type", "target_type", "relationship_evidence")

DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("event type identity", "event_type_identity",
     "matched gold events / gold events"),
    ("occurrence date (incl. correct abstention)", "occurrence_date_accuracy",
     "occurrence_date_ok / matched events"),
    ("announcement date (incl. correct abstention)", "announcement_date_accuracy",
     "announcement_date_ok / matched events"),
    ("participant role", "participant_role_accuracy",
     "roles found / gold participants in matched events"),
    ("participant entity id", "participant_entity_accuracy",
     "entity ids equal / gold participants whose role was found"),
    ("participant entity type", "participant_entity_type_accuracy",
     "entity types equal / gold participants whose role was found"),
    ("event properties", "properties_accuracy",
     "every gold property present and equal / matched events declaring any"),
    ("event evidence", "event_evidence_accuracy",
     "anchor resolves and span is verbatim / matched events"),
    ("relationship predicate identity", "predicate_identity",
     "matched gold relationships / gold relationships"),
    ("relationship source id", "source_id_accuracy", "source_id_ok / matched relationships"),
    ("relationship target id", "target_id_accuracy", "target_id_ok / matched relationships"),
    ("relationship source type", "source_type_accuracy",
     "source_type_ok / matched relationships"),
    ("relationship target type", "target_type_accuracy",
     "target_type_ok / matched relationships"),
    ("relationship evidence", "relationship_evidence_accuracy",
     "anchor resolves and span is verbatim / matched relationships"),
    ("expected abstentions honoured", "abstention_honoured_rate",
     "honoured expected abstentions / expected abstentions"),
    ("abstention code agreement", "abstention_code_agreement",
     "expectations whose own code the lane recorded / expected abstentions"),
)

# -- failure categories, and the order they are decided in ---------------------------------------

SCHEMA_OR_PARSE_FAILURE = "schema_or_parse_failure"
EVIDENCE_UNGROUNDED = "evidence_ungrounded"
EVENT_NOT_IDENTIFIED = "event_not_identified"
PREDICATE_NOT_IDENTIFIED = "predicate_not_identified"
PARTICIPANT_WRONG = "participant_wrong"
ENDPOINT_WRONG = "endpoint_wrong"
OCCURRENCE_DATE_WRONG = "occurrence_date_wrong"
ANNOUNCEMENT_DATE_WRONG = "announcement_date_wrong"
PROPERTIES_WRONG = "properties_wrong"
SILENCE_BROKEN = "silence_broken"

FAILURE_CATEGORIES: tuple[str, ...] = (
    SCHEMA_OR_PARSE_FAILURE, EVIDENCE_UNGROUNDED, EVENT_NOT_IDENTIFIED,
    PREDICATE_NOT_IDENTIFIED, PARTICIPANT_WRONG, ENDPOINT_WRONG, OCCURRENCE_DATE_WRONG,
    ANNOUNCEMENT_DATE_WRONG, PROPERTIES_WRONG, SILENCE_BROKEN,
)

# Printed in the report so two runs classify one failure the same way (STAGE_11 §4). It runs
# from "the answer could not be read at all", through "the answer was about the wrong thing",
# to "the answer was about the right thing and got a field wrong": a date complaint about an
# event quoted from a sentence that is not in the passage would name the wrong problem.
DECISION_ORDER: tuple[str, ...] = FAILURE_CATEGORIES

_DIMENSION_CATEGORY: dict[str, str] = {
    "event_evidence": EVIDENCE_UNGROUNDED,
    "relationship_evidence": EVIDENCE_UNGROUNDED,
    "participant_role": PARTICIPANT_WRONG,
    "participant_entity": PARTICIPANT_WRONG,
    "participant_entity_type": PARTICIPANT_WRONG,
    "source_id": ENDPOINT_WRONG,
    "target_id": ENDPOINT_WRONG,
    "source_type": ENDPOINT_WRONG,
    "target_type": ENDPOINT_WRONG,
    "occurrence_date": OCCURRENCE_DATE_WRONG,
    "announcement_date": ANNOUNCEMENT_DATE_WRONG,
    "properties": PROPERTIES_WRONG,
}

MISSED_GOLD = "missed_gold"
WRONG_MATCH = "wrong_match"
OVER_EMITTED = "over_emitted"

EM_DASH_ID = "—"


# -- what a case declares -------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldParticipant:
    role: str
    entity_id: str
    entity_type: str


@dataclass(frozen=True)
class GoldEvent:
    """One reviewed event, as the case file states it.

    `occurred_on` and `announced_on` are both carried and are both meaningful when absent: an
    absent `occurred_on` is the reviewers stating that the passage does not date the
    occurrence, which is a scored expectation and not a gap in the annotation.
    """

    event_type_id: str
    occurred_on: str | None
    announced_on: str | None
    participants: tuple[GoldParticipant, ...]
    properties: dict[str, str]
    note: str | None

    @property
    def entity_ids(self) -> frozenset[str]:
        return frozenset(p.entity_id for p in self.participants)


@dataclass(frozen=True)
class GoldRelationship:
    """One reviewed edge. `relationship_id` is the upper-case predicate the registry keys by."""

    relationship_id: str
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    note: str | None

    @property
    def endpoint_ids(self) -> frozenset[str]:
        return frozenset({self.source_id, self.target_id})


# -- what a run produced -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ParticipantRecord:
    """One gold participant beside whatever filled its role, dimension by dimension."""

    role: str
    expected_entity_id: str
    emitted_entity_id: str | None
    expected_entity_type: str
    emitted_entity_type: str | None
    emitted_entity_text: str | None
    emitted_named: bool | None
    participant_role_ok: bool
    participant_entity_ok: bool
    participant_entity_type_ok: bool

    def applies(self, dimension: str) -> bool:
        """The two id dimensions are only questions once the role was found.

        Scoring `participant_entity` on a role the lane never filled would charge the same
        omission twice and put a second failure under a category that names something else.
        """
        if dimension == "participant_role":
            return True
        return self.participant_role_ok


@dataclass(frozen=True)
class EventMatch:
    """A gold event beside the emitted event paired with it.

    `basis` records **how** the pair was found, because the matching rule decides what some of
    the dimensions can possibly say. Pairs found by entity overlap agree about at least one
    participant by construction; pairs found by type alone agree about nothing but the type.
    """

    event_type_id: str
    basis: str
    shared_entity_ids: int
    expected_occurred_on: str | None
    emitted_occurred_on: str | None
    expected_announced_on: str | None
    emitted_announced_on: str | None
    expected_properties: dict[str, str]
    emitted_properties: dict[str, str]
    #: Property names the lane emitted that gold does not declare. Counted, never scored: gold
    #: is a deliberate subset, so an extra declared property is an addition and not an error.
    additional_property_names: tuple[str, ...]
    participants: tuple[ParticipantRecord, ...]
    raw_text: str
    occurrence_date_ok: bool
    announcement_date_ok: bool
    properties_ok: bool
    event_evidence_ok: bool

    def applies(self, dimension: str) -> bool:
        if dimension == "properties":
            return bool(self.expected_properties)
        return dimension in EVENT_MATCH_DIMENSIONS

    @property
    def all_ok(self) -> bool:
        return (all(getattr(self, f"{d}_ok")
                    for d in EVENT_MATCH_DIMENSIONS if self.applies(d))
                and all(getattr(p, f"{d}_ok")
                        for p in self.participants
                        for d in PARTICIPANT_DIMENSIONS if p.applies(d)))

    @property
    def failed_dimensions(self) -> tuple[str, ...]:
        failed = [d for d in EVENT_MATCH_DIMENSIONS
                  if self.applies(d) and not getattr(self, f"{d}_ok")]
        failed += sorted({d for p in self.participants for d in PARTICIPANT_DIMENSIONS
                          if p.applies(d) and not getattr(p, f"{d}_ok")})
        return tuple(failed)


@dataclass(frozen=True)
class RelationshipMatch:
    relationship_id: str
    basis: str
    expected_source_id: str
    emitted_source_id: str
    expected_target_id: str
    emitted_target_id: str
    expected_source_type: str
    emitted_source_type: str
    expected_target_type: str
    emitted_target_type: str
    raw_text: str
    source_id_ok: bool
    target_id_ok: bool
    source_type_ok: bool
    target_type_ok: bool
    relationship_evidence_ok: bool

    def applies(self, dimension: str) -> bool:
        return dimension in RELATIONSHIP_MATCH_DIMENSIONS

    @property
    def all_ok(self) -> bool:
        return all(getattr(self, f"{d}_ok") for d in RELATIONSHIP_MATCH_DIMENSIONS)

    @property
    def failed_dimensions(self) -> tuple[str, ...]:
        return tuple(d for d in RELATIONSHIP_MATCH_DIMENSIONS
                     if not getattr(self, f"{d}_ok"))


@dataclass(frozen=True)
class AbstentionVerdict:
    """Whether one required silence was kept, and what the lane said instead.

    `honoured` is a conjunction of checkable conditions and deliberately not a code comparison.
    The case files and the lane do not share an abstention vocabulary and step 11 measured that
    disagreement rather than removing it by copying names across; the same choice is made here.
    `code_recorded` reports the comparison anyway, as its own rate.

    **Three of the four expectations these cases declare name no concept and no field**, so the
    conjunction below is weak on them and the report says so at its denominator. What their
    substance asks — that no occurrence date be asserted where the passage states none, and
    that no headcount be estimated — is scored by the date dimensions and by the property
    dimension, where it is checkable against the payload rather than against a code.
    """

    reason: str
    detail: str
    silence_kept: bool
    any_issue_recorded: bool
    answer_usable: bool
    code_recorded: bool
    honoured: bool


@dataclass(frozen=True)
class Failure:
    category: str
    source: str
    subject: str
    detail: str


@dataclass
class CaseScore:
    """Everything one (case, scope) pair produced, scored."""

    case_id: str
    scope: str
    passage_id: str
    request_digest: str | None
    gold_events: tuple[GoldEvent, ...]
    gold_relationships: tuple[GoldRelationship, ...]
    expected_abstentions: tuple[Any, ...]
    events: tuple[Any, ...]
    relationships: tuple[Any, ...]
    event_evidence_resolves: tuple[bool, ...]
    relationship_evidence_resolves: tuple[bool, ...]
    matched_events: tuple[EventMatch, ...]
    matched_relationships: tuple[RelationshipMatch, ...]
    missed_events: tuple[str, ...]
    missed_relationships: tuple[str, ...]
    additional_events: tuple[str, ...]
    additional_relationships: tuple[str, ...]
    abstentions: tuple[AbstentionVerdict, ...]
    failures: tuple[Failure, ...]
    issues_by_code: dict[str, int]
    rejections_by_code: dict[str, int]
    model_abstentions_by_code: dict[str, int]
    rejected_payloads: int
    model_abstentions: int
    #: What each candidate scope offered this passage, and how much of that was an event type.
    #: The evidence behind the routing decision: neither scope reaches the gold event types, so
    #: relaxing a category filter would have been necessary and not sufficient (STAGE_12 §4).
    scope_concept_ids: tuple[str, ...]
    scope_event_type_ids: tuple[str, ...]
    gold_event_types_in_scope: tuple[str, ...]
    ontology_warnings: tuple[str, ...]
    ontology_errors: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def honoured_abstentions(self) -> int:
        return sum(1 for verdict in self.abstentions if verdict.honoured)

    @property
    def code_agreeing_abstentions(self) -> int:
        return sum(1 for verdict in self.abstentions if verdict.code_recorded)

    @property
    def model_chosen_abstentions(self) -> int:
        """Non-rejections the model actually chose, less the lane's own decisions."""
        return self.model_abstentions - sum(
            self.model_abstentions_by_code.get(code, 0)
            for code in LANE_DECISIONS_WITHOUT_AN_ANSWER)


def score_case(
    *,
    case_id: str,
    scope: str,
    passage_id: str,
    gold_events: tuple[GoldEvent, ...],
    gold_relationships: tuple[GoldRelationship, ...],
    expected_abstentions: tuple[Any, ...],
    events: list,
    relationships: list,
    issues: list,
    request_digest: str | None,
    validation: dict[str, tuple[str, ...]],
    resolves_evidence,
    scope_concept_ids: tuple[str, ...],
    scope_event_type_ids: tuple[str, ...],
) -> CaseScore:
    """One (case, scope) pair, scored on every dimension at once.

    `resolves_evidence` is passed in rather than computed: what makes a passage id resolvable
    and a span verbatim belongs to the caller's corpus, exactly as the metric scorer already
    argues.
    """
    ordered_events = tuple(sorted(
        events, key=lambda e: (e.event_type_id, sorted(p.entity_id for p in e.participants))))
    ordered_relationships = tuple(sorted(
        relationships, key=lambda r: (r.relationship_id, r.source_id, r.target_id)))

    event_pairs = _pair_events(gold_events, ordered_events)
    matched_events = tuple(
        _event_match(want, got, basis, resolves_evidence)
        for want, got, basis in event_pairs)
    relationship_pairs = _pair_relationships(gold_relationships, ordered_relationships)
    matched_relationships = tuple(
        _relationship_match(want, got, basis, resolves_evidence)
        for want, got, basis in relationship_pairs)

    paired_events = {id(got) for _, got, _ in event_pairs}
    paired_relationships = {id(got) for _, got, _ in relationship_pairs}
    matched_gold_events = {id(want) for want, _, _ in event_pairs}
    matched_gold_relationships = {id(want) for want, _, _ in relationship_pairs}

    usable = not any(getattr(i, "code", "") in _NO_USABLE_ANSWER for i in issues)
    verdicts = tuple(
        _abstention_verdict(expected, events=ordered_events,
                            relationships=ordered_relationships, issues=issues,
                            gold_events=gold_events, usable=usable)
        for expected in expected_abstentions)

    missed_events = tuple(
        _event_key(want) for want in gold_events if id(want) not in matched_gold_events)
    missed_relationships = tuple(
        _relationship_key(want) for want in gold_relationships
        if id(want) not in matched_gold_relationships)
    additional_events = tuple(sorted(
        _emitted_event_key(got) for got in ordered_events if id(got) not in paired_events))
    additional_relationships = tuple(sorted(
        _relationship_key(got) for got in ordered_relationships
        if id(got) not in paired_relationships))

    failures = classify_failures(
        gold_events=gold_events, gold_relationships=gold_relationships,
        matched_events=matched_events, matched_relationships=matched_relationships,
        missed_events=missed_events, missed_relationships=missed_relationships,
        abstentions=verdicts, usable=usable, issues=issues)

    rejected = sum(1 for issue in issues if getattr(issue, "rejected_claim", False))
    score = CaseScore(
        case_id=case_id,
        scope=scope,
        passage_id=passage_id,
        request_digest=request_digest,
        gold_events=gold_events,
        gold_relationships=gold_relationships,
        expected_abstentions=expected_abstentions,
        events=ordered_events,
        relationships=ordered_relationships,
        event_evidence_resolves=tuple(
            bool(resolves_evidence(event)) for event in ordered_events),
        relationship_evidence_resolves=tuple(
            bool(resolves_evidence(edge)) for edge in ordered_relationships),
        matched_events=matched_events,
        matched_relationships=matched_relationships,
        missed_events=missed_events,
        missed_relationships=missed_relationships,
        additional_events=additional_events,
        additional_relationships=additional_relationships,
        abstentions=verdicts,
        failures=failures,
        issues_by_code=_census(issues, rejected_claim=None),
        rejections_by_code=_census(issues, rejected_claim=True),
        model_abstentions_by_code=_census(issues, rejected_claim=False),
        rejected_payloads=rejected,
        model_abstentions=len(issues) - rejected,
        scope_concept_ids=tuple(scope_concept_ids),
        scope_event_type_ids=tuple(scope_event_type_ids),
        gold_event_types_in_scope=tuple(sorted(
            {want.event_type_id for want in gold_events} & set(scope_event_type_ids))),
        ontology_warnings=tuple(validation.get("warnings", ())),
        ontology_errors=tuple(validation.get("errors", ())),
    )
    score.counts = {
        "gold_events": len(gold_events),
        "emitted_events": len(ordered_events),
        "matched_events": len(matched_events),
        "missed_events": len(missed_events),
        "additional_events": len(additional_events),
        "gold_relationships": len(gold_relationships),
        "emitted_relationships": len(ordered_relationships),
        "matched_relationships": len(matched_relationships),
        "missed_relationships": len(missed_relationships),
        "additional_relationships": len(additional_relationships),
        "gold_participants": sum(len(want.participants) for want in gold_events),
        "additional_property_names": sum(
            len(match.additional_property_names) for match in matched_events),
        "expected_abstentions": len(expected_abstentions),
        "honoured_abstentions": score.honoured_abstentions,
        "code_agreeing_abstentions": score.code_agreeing_abstentions,
        "rejected_payloads": rejected,
        "model_abstentions": score.model_abstentions,
        "model_chosen_abstentions": score.model_chosen_abstentions,
        "failures": len(failures),
        "ontology_warnings": len(score.ontology_warnings),
        "ontology_errors": len(score.ontology_errors),
        "scope_concepts": len(scope_concept_ids),
        "scope_event_types": len(scope_event_type_ids),
        "gold_event_types_in_scope": len(score.gold_event_types_in_scope),
    }
    score.scores = scores([score])
    return score


# -- matching ------------------------------------------------------------------------------------


def _pair_events(gold_events, emitted):
    """Gold events to emitted events, within one event type, most-shared-participants first.

    **Stated because the rule decides what some dimensions can say.** Two candidate pairings
    exist and both are worse. Requiring the participant sets to be equal would make the
    participant dimensions tautologies — every matched pair would agree by construction and a
    wrong entity id would show up as a missing event instead of as a wrong participant.
    Matching on the event type alone would pair the wrong executive change with the wrong
    officer on a passage that reports two.

    So: within a type, every (gold, emitted) pair is ranked by how many participant entity ids
    they share, ties broken by the order gold declares and the order the lane emitted, and
    pairs are taken greedily. `EventMatch.basis` records which it was — `shared_participants`
    or `event_type_only` — and the report prints the census, so a reader can see how much of
    each dimension rests on a pairing that already agreed.

    **The pairs are returned in gold declaration order, not in the order they were taken.**
    Selection is greedy over shared participants, so the take order is a ranking; the report
    walks a case's gold events in declaration order and reads `matched_events` positionally,
    and two gold events of one type with different overlap counts would have handed each gold
    row the other one's verdict *(found by review 2026-08-02; invisible on the committed run,
    where both `executive_change` pairs share two participants and the two orders coincide)*.
    """
    pairs = []
    for gold_index, want in enumerate(gold_events):
        for emitted_index, got in enumerate(emitted):
            if got.event_type_id != want.event_type_id:
                continue
            shared = len(want.entity_ids & {p.entity_id for p in got.participants})
            pairs.append((-shared, gold_index, emitted_index, want, got))
    taken_gold: set[int] = set()
    taken_emitted: set[int] = set()
    chosen = []
    for negative_shared, gold_index, emitted_index, want, got in sorted(
            pairs, key=lambda entry: entry[:3]):
        if gold_index in taken_gold or emitted_index in taken_emitted:
            continue
        taken_gold.add(gold_index)
        taken_emitted.add(emitted_index)
        chosen.append((gold_index, want, got,
                       "shared_participants" if -negative_shared else "event_type_only"))
    return [entry[1:] for entry in sorted(chosen, key=lambda entry: entry[0])]


def _pair_relationships(gold_relationships, emitted):
    """The same rule for edges, over the two endpoint ids, and the same gold ordering."""
    pairs = []
    for gold_index, want in enumerate(gold_relationships):
        for emitted_index, got in enumerate(emitted):
            if got.relationship_id != want.relationship_id:
                continue
            shared = len(want.endpoint_ids & {got.source_id, got.target_id})
            pairs.append((-shared, gold_index, emitted_index, want, got))
    taken_gold: set[int] = set()
    taken_emitted: set[int] = set()
    chosen = []
    for negative_shared, gold_index, emitted_index, want, got in sorted(
            pairs, key=lambda entry: entry[:3]):
        if gold_index in taken_gold or emitted_index in taken_emitted:
            continue
        taken_gold.add(gold_index)
        taken_emitted.add(emitted_index)
        chosen.append((gold_index, want, got,
                       "shared_endpoints" if -negative_shared else "predicate_only"))
    return [entry[1:] for entry in sorted(chosen, key=lambda entry: entry[0])]


def _event_match(want: GoldEvent, got, basis: str, resolves_evidence) -> EventMatch:
    by_role: dict[str, Any] = {}
    for participant in got.participants:
        by_role.setdefault(participant.role, participant)
    participants = tuple(
        ParticipantRecord(
            role=expected.role,
            expected_entity_id=expected.entity_id,
            emitted_entity_id=getattr(by_role.get(expected.role), "entity_id", None),
            expected_entity_type=expected.entity_type,
            emitted_entity_type=getattr(by_role.get(expected.role), "entity_type", None),
            emitted_entity_text=getattr(by_role.get(expected.role), "entity_text", None),
            emitted_named=getattr(by_role.get(expected.role), "named", None),
            participant_role_ok=expected.role in by_role,
            participant_entity_ok=(
                getattr(by_role.get(expected.role), "entity_id", None) == expected.entity_id),
            participant_entity_type_ok=(
                getattr(by_role.get(expected.role), "entity_type", None)
                == expected.entity_type),
        )
        for expected in want.participants
    )
    emitted_properties = dict(got.properties or {})
    return EventMatch(
        event_type_id=want.event_type_id,
        basis=basis,
        shared_entity_ids=len(want.entity_ids & {p.entity_id for p in got.participants}),
        expected_occurred_on=want.occurred_on,
        emitted_occurred_on=got.occurred_on,
        expected_announced_on=want.announced_on,
        emitted_announced_on=got.announced_on,
        expected_properties=dict(want.properties),
        emitted_properties=emitted_properties,
        additional_property_names=tuple(sorted(
            set(emitted_properties) - set(want.properties))),
        participants=participants,
        raw_text=got.raw_text,
        # Absent is an answer. Gold that states no occurrence date is the reviewers saying the
        # passage supports none, so a lane filling one in is wrong (V1 §4.0b).
        occurrence_date_ok=want.occurred_on == got.occurred_on,
        announcement_date_ok=want.announced_on == got.announced_on,
        # Containment, not equality, and the asymmetry is the benchmark's own annotation
        # policy: gold names a deliberate subset, so an extra declared property is an addition
        # rather than a disagreement. Every gold property must be present and equal.
        properties_ok=all(emitted_properties.get(name) == value
                          for name, value in want.properties.items()),
        event_evidence_ok=bool(resolves_evidence(got)),
    )


def _relationship_match(want: GoldRelationship, got, basis, resolves_evidence):
    return RelationshipMatch(
        relationship_id=want.relationship_id,
        basis=basis,
        expected_source_id=want.source_id,
        emitted_source_id=got.source_id,
        expected_target_id=want.target_id,
        emitted_target_id=got.target_id,
        expected_source_type=want.source_type,
        emitted_source_type=got.source_type,
        expected_target_type=want.target_type,
        emitted_target_type=got.target_type,
        raw_text=got.raw_text,
        source_id_ok=want.source_id == got.source_id,
        target_id_ok=want.target_id == got.target_id,
        source_type_ok=want.source_type == got.source_type,
        target_type_ok=want.target_type == got.target_type,
        relationship_evidence_ok=bool(resolves_evidence(got)),
    )


# -- aggregation ------------------------------------------------------------------------------------


def fractions(cases: list[CaseScore]) -> dict[str, tuple[int, int]]:
    """Every score as `(hits, denominator)`. **The only place any of them is counted.**

    Returned as fractions rather than ratios so the report can print the denominator beside the
    number: a rate over 2 and a rate over 20 read identically at three decimal places, and this
    benchmark's denominators are small enough that the difference is the whole story.
    """
    matched_events = [m for case in cases for m in case.matched_events]
    matched_relationships = [m for case in cases for m in case.matched_relationships]
    participants = [p for m in matched_events for p in m.participants]
    expected = sum(len(case.expected_abstentions) for case in cases)

    counted: dict[str, tuple[int, int]] = {
        "event_type_identity": (
            len(matched_events), sum(len(case.gold_events) for case in cases)),
        "predicate_identity": (
            len(matched_relationships),
            sum(len(case.gold_relationships) for case in cases)),
        "matched_over_emitted_events": (
            len(matched_events), sum(len(case.events) for case in cases)),
        "matched_over_emitted_relationships": (
            len(matched_relationships), sum(len(case.relationships) for case in cases)),
        "abstention_honoured_rate": (
            sum(case.honoured_abstentions for case in cases), expected),
        "abstention_code_agreement": (
            sum(case.code_agreeing_abstentions for case in cases), expected),
    }
    for dimension in EVENT_MATCH_DIMENSIONS:
        applicable = [m for m in matched_events if m.applies(dimension)]
        counted[f"{dimension}_accuracy"] = (
            sum(1 for m in applicable if getattr(m, f"{dimension}_ok")), len(applicable))
    for dimension in RELATIONSHIP_MATCH_DIMENSIONS:
        counted[f"{dimension}_accuracy"] = (
            sum(1 for m in matched_relationships if getattr(m, f"{dimension}_ok")),
            len(matched_relationships))
    for dimension in PARTICIPANT_DIMENSIONS:
        applicable = [p for p in participants if p.applies(dimension)]
        counted[f"{dimension}_accuracy"] = (
            sum(1 for p in applicable if getattr(p, f"{dimension}_ok")), len(applicable))
    return dict(sorted(counted.items()))


def scores(cases: list[CaseScore], digits: int = 6) -> dict[str, float]:
    """`fractions` as ratios. Nothing else in this repository divides one of these."""
    return {name: _ratio(hits, total, digits)
            for name, (hits, total) in fractions(cases).items()}


def totals(cases: list[CaseScore], digits: int = 6) -> dict[str, Any]:
    """Everything the headline shows, derived from the per-case scores and nothing else."""
    issues_by_code: dict[str, int] = {}
    rejections_by_code: dict[str, int] = {}
    model_abstentions_by_code: dict[str, int] = {}
    failures_by_category = {category: 0 for category in FAILURE_CATEGORIES}
    for case in cases:
        for source, target in ((case.issues_by_code, issues_by_code),
                               (case.rejections_by_code, rejections_by_code),
                               (case.model_abstentions_by_code, model_abstentions_by_code)):
            for code, count in source.items():
                target[code] = target.get(code, 0) + count
        for failure in case.failures:
            failures_by_category[failure.category] += 1

    matches = [m for case in cases for m in case.matched_events]
    edges = [m for case in cases for m in case.matched_relationships]
    return {
        "cases": len(cases),
        "distinct_passages": len({case.passage_id for case in cases}),
        "gold_events": sum(len(case.gold_events) for case in cases),
        "emitted_events": sum(len(case.events) for case in cases),
        "matched_events": len(matches),
        "missed_events": sum(len(case.missed_events) for case in cases),
        "additional_events": sum(len(case.additional_events) for case in cases),
        "gold_relationships": sum(len(case.gold_relationships) for case in cases),
        "emitted_relationships": sum(len(case.relationships) for case in cases),
        "matched_relationships": len(edges),
        "missed_relationships": sum(len(case.missed_relationships) for case in cases),
        "additional_relationships": sum(
            len(case.additional_relationships) for case in cases),
        "gold_participants": sum(
            len(want.participants) for case in cases for want in case.gold_events),
        "matched_participants": sum(len(m.participants) for m in matches),
        "additional_property_names": sum(len(m.additional_property_names) for m in matches),
        # How each pair was found. Printed because the pairing rule decides what the
        # participant and endpoint dimensions can possibly say — see `_pair_events`.
        "event_pairs_by_basis": {
            basis: sum(1 for m in matches if m.basis == basis)
            for basis in ("shared_participants", "event_type_only")},
        "relationship_pairs_by_basis": {
            basis: sum(1 for m in edges if m.basis == basis)
            for basis in ("shared_endpoints", "predicate_only")},
        "expected_abstentions": sum(len(case.expected_abstentions) for case in cases),
        "honoured_abstentions": sum(case.honoured_abstentions for case in cases),
        "code_agreeing_abstentions": sum(case.code_agreeing_abstentions for case in cases),
        "rejected_payloads": sum(case.rejected_payloads for case in cases),
        "model_abstentions": sum(case.model_abstentions for case in cases),
        "model_chosen_abstentions": sum(case.model_chosen_abstentions for case in cases),
        "ontology_warnings": sum(len(case.ontology_warnings) for case in cases),
        "ontology_errors": sum(len(case.ontology_errors) for case in cases),
        "failures": sum(len(case.failures) for case in cases),
        "failures_by_category": failures_by_category,
        "issues_by_code": dict(sorted(issues_by_code.items())),
        "rejections_by_code": dict(sorted(rejections_by_code.items())),
        "model_abstentions_by_code": dict(sorted(model_abstentions_by_code.items())),
        # The routing evidence, aggregated: how many gold event types either candidate scope
        # would have offered this lane if it had asked one.
        "gold_event_types_in_scope": sum(
            len(case.gold_event_types_in_scope) for case in cases),
        "distinct_gold_event_types": len(
            {want.event_type_id for case in cases for want in case.gold_events}),
        "scores": scores(cases, digits),
        "score_denominators": {name: total for name, (_, total) in fractions(cases).items()},
        "score_numerators": {name: hits for name, (hits, _) in fractions(cases).items()},
    }


# -- the failure classifier ----------------------------------------------------------------------


def classify_failures(
    *, gold_events, gold_relationships, matched_events, matched_relationships,
    missed_events, missed_relationships, abstentions, usable, issues,
) -> tuple[Failure, ...]:
    """Total and disjoint over the disagreements this benchmark can adjudicate.

    The classified set, following step 11's correction to STAGE_11 §4 rather than its letter:

    1. every gold event or relationship the lane did not emit;
    2. every matched pair failing at least one applicable dimension;
    3. every expected abstention that was not honoured.

    Everything else the lane emitted is a **non-gold addition** and is counted unclassified.
    Classifying those as failures would turn `matched_over_emitted` into what it is not — a
    precision under another name — which V1 §4.0 and STAGE_11 §2 both refuse.

    One category per failure, decided in `DECISION_ORDER`, first match wins.
    """
    failures: list[Failure] = []
    codes = {getattr(issue, "code", "") for issue in issues}

    for key in missed_events:
        failures.append(Failure(
            category=SCHEMA_OR_PARSE_FAILURE if not usable else EVENT_NOT_IDENTIFIED,
            source=MISSED_GOLD,
            subject=key,
            detail=("the answer for this passage did not parse or did not conform, so no "
                    "finer classification is available" if not usable else
                    f"no event of this type was emitted; the lane recorded {sorted(codes)}"),
        ))
    for key in missed_relationships:
        failures.append(Failure(
            category=SCHEMA_OR_PARSE_FAILURE if not usable else PREDICATE_NOT_IDENTIFIED,
            source=MISSED_GOLD,
            subject=key,
            detail=("the answer for this passage did not parse or did not conform"
                    if not usable else
                    "no relationship with this predicate was emitted"),
        ))
    for match in matched_events:
        if match.all_ok:
            continue
        failures.append(Failure(
            category=_first_in_order(
                {_DIMENSION_CATEGORY[d] for d in match.failed_dimensions}),
            source=WRONG_MATCH,
            subject=_event_match_key(match),
            detail="matched event failed " + ", ".join(match.failed_dimensions),
        ))
    for match in matched_relationships:
        if match.all_ok:
            continue
        failures.append(Failure(
            category=_first_in_order(
                {_DIMENSION_CATEGORY[d] for d in match.failed_dimensions}),
            source=WRONG_MATCH,
            subject=match.relationship_id,
            detail="matched relationship failed " + ", ".join(match.failed_dimensions),
        ))
    for verdict in abstentions:
        if verdict.honoured:
            continue
        failures.append(Failure(
            category=(SCHEMA_OR_PARSE_FAILURE if not verdict.answer_usable
                      else SILENCE_BROKEN),
            source=OVER_EMITTED,
            subject=verdict.reason,
            detail=("the case requires this silence and the lane produced an answer it could "
                    "not use" if not verdict.answer_usable else
                    "the case requires this silence and the lane recorded no issue at all: "
                    "a bare silence, not an abstention"),
        ))
    return tuple(sorted(
        failures,
        key=lambda f: (DECISION_ORDER.index(f.category), f.subject, f.detail)))


def _first_in_order(categories: set[str]) -> str:
    return next(category for category in DECISION_ORDER if category in categories)


# -- the pieces -------------------------------------------------------------------------------------


def _abstention_verdict(expected, *, events, relationships, issues, gold_events, usable):
    """Three conditions, and the report states which of them these cases can actually exercise.

    `silence_kept` is only a real condition where the case annotates no gold payload at all: on
    a case that also annotates events, "the lane emitted events" is what it was supposed to do
    and the expectation is about something narrower, which is scored on the payload dimensions
    instead.
    """
    silence_kept = bool(gold_events) or not (events or relationships)
    any_issue_recorded = bool(issues)
    code_recorded = any(getattr(i, "code", "") == expected.reason for i in issues)
    return AbstentionVerdict(
        reason=expected.reason,
        detail=expected.detail,
        silence_kept=silence_kept,
        any_issue_recorded=any_issue_recorded,
        answer_usable=usable,
        code_recorded=code_recorded,
        honoured=silence_kept and any_issue_recorded and usable,
    )


def _census(issues, *, rejected_claim: bool | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        if rejected_claim is not None and (
                bool(getattr(issue, "rejected_claim", False)) is not rejected_claim):
            continue
        code = getattr(issue, "code", "")
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _event_key(want: GoldEvent) -> str:
    return f"{want.event_type_id}({','.join(sorted(want.entity_ids)) or EM_DASH_ID})"


def _emitted_event_key(got) -> str:
    ids = sorted(p.entity_id for p in got.participants)
    return f"{got.event_type_id}({','.join(ids) or EM_DASH_ID})"


def _event_match_key(match: EventMatch) -> str:
    ids = sorted(p.expected_entity_id for p in match.participants)
    return f"{match.event_type_id}({','.join(ids) or EM_DASH_ID})"


def _relationship_key(entry) -> str:
    return f"{entry.relationship_id}({entry.source_id}->{entry.target_id})"


def _ratio(hit: int, total: int, digits: int) -> float:
    """1.0 for an empty denominator: nothing was asked for and nothing was missed."""
    return 1.0 if total == 0 else round(hit / total, digits)
