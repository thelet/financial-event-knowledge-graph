"""Scoring the event lane against the reviewed event and relationship cases, under both scopes.

STAGE_12. The report this writes is the evidence that the claim path carries events and
relationships through the same provider boundary, scoping rules, validation and abstention
behaviour the metric lanes use.

**Generation and reporting are two commands, and only one of them is reproducible.**
`event-build` talks to the generation server once per distinct request and writes the answers
to `answers/event_v1.jsonl`. `event-report` runs replay-only off that file with `inner=None`,
so a missing answer raises and names the request instead of being scored as a silence. Two
`event-report` runs are byte-identical, `implementation_commit` the only permitted difference —
the rule steps 6, 8, 9 and 11 all hold.

**This module composes and renders; it computes no score.** Every dimension, failure category
and count comes from `event_evaluation.py`. At step 6 a runner duplicated the verdict logic and
printed 46 `WRONG` rows beside an accuracy of 1.000 with every test green; at step 11 three
rendered tables were checked by nothing. Both are why `tests/extraction/test_event_lane_report.py`
compares every rendered cell to the object it renders.

**No duration, token count or timestamp enters the artifact.** STAGE_09 §11.2 settled it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from extraction.core.assembly import assemble_events
from extraction.core.identifiers import event_id
from extraction.core.validation import ONTOLOGY_WARNING, validate
from extraction.stages.narrative import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    EVENT_LANE_NAME,
    EVENT_LANE_VERSION,
    EVENT_MODEL_ABSTENTION_REASONS,
    EVENT_PROMPT_VERSION,
    TEMPERATURE,
    AnswerStore,
    OntologyGuidedEventLane,
    PassageContext,
    ReplayingGenerationProvider,
    offered_vocabulary,
)
from extraction.stages.narrative.events_public import LANE_DECISIONS_WITHOUT_AN_ANSWER
from ontology import load_ontology

from . import hybrid_scope_runner, narrative_runner, runner
from .event_evaluation import (
    DECISION_ORDER,
    DIMENSIONS,
    EVENT_MATCH_DIMENSIONS,
    FAILURE_CATEGORIES,
    PARTICIPANT_DIMENSIONS,
    RELATIONSHIP_MATCH_DIMENSIONS,
    CaseScore,
    GoldEvent,
    GoldParticipant,
    GoldRelationship,
    score_case,
    totals,
)
from .runner import (
    BENCHMARK_VERSION,
    CASES_DIR,
    EM_DASH,
    RATIO_DIGITS,
    REPORTS_DIR,
    anchor as _anchor,
    cell as _cell,
)

REPORT_STEM = "event_relationship_v1"

PACKAGE_ROOT = Path(__file__).resolve().parent
ANSWERS_DIR = PACKAGE_ROOT / "answers"
ANSWER_STORE = ANSWERS_DIR / "event_v1.jsonl"

# The two scopes, in the order they are reported. Both are run, and the reason is not that they
# are expected to differ — see `ROUTING`.
SCOPES: tuple[str, ...] = ("lexical", "hybrid")

# The routing decision this stage had to make, stated where the report renders it so a reader
# gets the rejected options beside the numbers that rejected them. STAGE_12 §4 carries the
# argument; this is the summary the artifact prints, and every count in the "measured" column
# is checked against the run by `tests/extraction/test_event_lane_report.py`.
ROUTING: dict[str, Any] = {
    "chosen": "the declared event category, offered whole",
    "summary": (
        "Every declared event type is offered to the lane on every passage, and the "
        "relationship menu is derived from those types' own `allowed_relationships`. The "
        "mechanism has no threshold, no rank cut and no parameter, which is what makes it the "
        "smallest general one available: there is nothing in it that could have been fitted to "
        "a passage."),
    "rejected": (
        {"option": "relax the metric-only filters in typed selection and the narrative lane",
         "why_not": "necessary and not sufficient. Neither candidate scope offers any gold "
                    "event type on any of these passages — the run measures it below — so "
                    "admitting event concepts through those filters admits nothing here."},
        {"option": "semantic retrieval over the event definitions, using the committed vectors",
         "why_not": "would require moving `top_k` or `min_similarity`, both derived from a "
                    "measured similarity distribution and asserted against the committed "
                    "hybrid-scope report by `tests/extraction/test_hybrid_scoping.py`. Moving "
                    "a configured threshold until three fixtures are reached is fitting the "
                    "configuration to the answer sheet, and it would invalidate the committed "
                    "vector cache the stage 9 report is regenerated from."},
        {"option": "add aliases to the event types",
         "why_not": "all three gold event types declare `aliases = ()` and none is in its own "
                    "case's lexical scope. Transcribing the passages' wording into the "
                    "vocabulary is fitting the ontology to the fixtures — the forbidden answer, "
                    "and the one this stage was told not to give."},
        {"option": "expand protected concepts from detected entities and actions",
         "why_not": "needs an action lexicon that does not exist. Building one from these three "
                    "passages is the alias answer at one remove."},
    ),
    "cost": (
        "the whole category is rendered into every prompt. Measured on the reviewed passages: "
        "the event-type block is 8,566 characters, the prompts run 4,713–4,940 tokens on the "
        "server, and the lane's own estimate leaves 1,793–2,125 tokens of the 8,192-token "
        "slot for an answer, against the 512 one event needs. The estimate is deliberately "
        "pessimistic — it assumes 3.5 characters per token where the measured ratios are "
        "4.39–4.48 — so the real headroom is larger than the budget it hands out."),
    "consequence": (
        "the menu is a function of the vocabulary alone, so it is identical under both "
        "candidate scopes and both scopes replay one stored answer per case. The two columns "
        "below are equal by construction, not by measurement, and the scope comparison this "
        "report can make is a different one: whether either scope would have reached the gold "
        "event types at all."),
}

# What `event-build` observed about answer conformance. Declared rather than derived for the
# reason STAGE_11 gives: a request whose answer never conformed has no row in the store, so the
# only checkable direction is that a conformant answer *is* a row. "Conformant after a retry"
# is not a state this pipeline can reach — `_post_with_retries` retries transport failures and
# 5xx only, and an identical request at temperature 0 cannot change — so it is reported as
# structurally zero rather than measured as zero.
STRUCTURED_OUTPUT: dict[str, Any] = {
    "provider_calls": 6,
    "distinct_requests": 3,
    "requests_issued": 3,
    "conformant_first_attempt": 3,
    "conformant_after_retry": 0,
    "never_conformant": 0,
    "retries_possible": False,
}

# Requests whose answer never conformed, and therefore have no row in the store. Empty, and
# kept rather than deleted so the report can say so and a test can hold the store to it.
UNUSABLE_ANSWERS: tuple[dict[str, str], ...] = ()

# Disagreements between this lane and a reviewed case that look like case questions rather than
# lane failures. **A stage never acts on these.** Editing a case because a lane disagreed with
# it would stop the benchmark measuring anything; each is scored under the case as written,
# which is the harsher reading, and the answer lands in `resolution`.
REVIEW_QUESTIONS: tuple[dict[str, str], ...] = (
    {"case_id": "event-executive-change-ceo-2025",
     "subject": "executive_change.properties.position for the returning chairman",
     "the_case_says": "position: “Chairman of the Board”, with the note quoting “Rabois "
                      "taking on the role of Chairman”",
     "this_run_found": "the passage prints “the role of Chairman” and nowhere prints "
                       "“Chairman of the Board”, so the lane's printed-value rule cannot "
                       "produce the gold string and the property scores wrong",
     "question": "should a property value be the filing's own words, as `raw_text` and "
                 "`population_definition_raw` already are, or the reviewers' normalisation of "
                 "them? The same question decides five rows of the step 11 population table."},
    {"case_id": "event-credit-facility-established-2022",
     "subject": "the facility's entity id",
     "the_case_says": "entity_id: asset_backed_senior_revolving_2022_10",
     "this_run_found": "the passage names the facility “a new asset-backed senior revolving "
                       "credit facility” and prints no year-month suffix, so a readable id "
                       "derived from the printed name cannot reach the gold id",
     "question": "gold's id encodes the establishment year and month. No general rule derives "
                 "that from a name; either the id convention is a resolution decision that "
                 "belongs to an entity-resolution pass V1 does not have, or gold should carry "
                 "the name-derived id. Entity ids are validated by nothing, so this is a "
                 "naming question and not a validity one."},
    {"case_id": "event-workforce-reduction-2020",
     "subject": "workforce_reduction.properties.reason",
     "the_case_says": "reason: “following the outbreak of the COVID-19 pandemic”",
     "this_run_found": "the lane records “outbreak of the COVID-19 pandemic” — the same "
                       "wording without its leading preposition, and a substring of what gold "
                       "expects",
     "question": "the same span-convention question step 11 raised about population wording, "
                 "in a second place: how much of the sentence a quoted attribute should span. "
                 "Recorded as a containment rather than scored on, because a containment rule "
                 "is a comparability rule no plan states."},
)


# -- the report model ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventCase:
    """One reviewed event case, as loaded. No lane involvement yet."""

    case_id: str
    category: str
    lane: str
    source_file: str
    document_id: str
    passage_id: str
    notes: str
    gold_events: tuple[GoldEvent, ...]
    gold_relationships: tuple[GoldRelationship, ...]
    expected_abstentions: tuple[Any, ...]


@dataclass(frozen=True)
class ScopeView:
    name: str
    cases: list[CaseScore]
    totals: dict[str, Any]

    def case(self, case_id: str) -> CaseScore | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


@dataclass
class EventLaneReport:
    benchmark_version: str
    ontology_definition_hash: str
    normalization_corpus: dict[str, Any]
    implementation_commit: str
    lane: dict[str, str]
    model: dict[str, Any]
    answer_store: dict[str, Any]
    vocabulary: dict[str, Any]
    views: dict[str, ScopeView]
    comparison: dict[str, Any]
    case_index: dict[str, dict[str, str]]
    structured_output: dict[str, Any] = field(
        default_factory=lambda: dict(STRUCTURED_OUTPUT))

    def case(self, case_id: str, scope: str = "lexical") -> CaseScore | None:
        return self.views[scope].case(case_id)


# -- loading ---------------------------------------------------------------------------------------


def load_event_cases(cases_dir: Path = CASES_DIR) -> list[EventCase]:
    """Every case that annotates an event or a relationship, ordered by case id.

    Selected by what a case **declares** rather than by its `category` string, so a fourth
    event case added to any file is scored without an edit here and a case relabelled is not
    silently dropped.
    """
    cases: list[EventCase] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            if not (case.get("gold_events") or case.get("gold_relationships")):
                continue
            if not case.get("passage_id"):
                continue
            cases.append(EventCase(
                case_id=case["case_id"],
                category=case.get("category") or "",
                lane=case.get("lane") or "",
                source_file=path.name,
                document_id=case["document_id"],
                passage_id=case["passage_id"],
                notes=" ".join(str(case.get("notes") or "").split()),
                gold_events=tuple(_gold_event(e) for e in (case.get("gold_events") or [])),
                gold_relationships=tuple(
                    _gold_relationship(r) for r in (case.get("gold_relationships") or [])),
                expected_abstentions=tuple(
                    narrative_runner._expected(a) for a in (case.get("abstentions") or [])),
            ))
    return sorted(cases, key=lambda c: c.case_id)


def _gold_event(raw: dict) -> GoldEvent:
    return GoldEvent(
        event_type_id=raw["event_type_id"],
        # Read as stated, including absent. An absent `occurred_on` is the reviewers saying the
        # passage supports none, which is a scored expectation — never a missing annotation to
        # be filled in from `announced_on` or from the filing date (V1 §4.0b).
        occurred_on=raw.get("occurred_on"),
        announced_on=raw.get("announced_on"),
        participants=tuple(
            GoldParticipant(role=p["role"], entity_id=p["entity_id"],
                            entity_type=p["entity_type"])
            for p in (raw.get("participants") or [])),
        properties={str(k): str(v) for k, v in (raw.get("properties") or {}).items()},
        note=" ".join(str(raw.get("note") or "").split()) or None,
    )


def _gold_relationship(raw: dict) -> GoldRelationship:
    return GoldRelationship(
        relationship_id=raw["relationship_id"],
        source_id=raw["source_id"],
        source_type=raw["source_type"],
        target_id=raw["target_id"],
        target_type=raw["target_type"],
        note=" ".join(str(raw.get("note") or "").split()) or None,
    )


# -- composition ------------------------------------------------------------------------------------


def build_lane(ontology, provider, *, context_tokens: int = DEFAULT_CONTEXT_TOKENS):
    """The one composition root for this report.

    Takes no scope, and that is the routing decision rather than an omission — see
    `event_lane`'s module docstring and `ROUTING` above.
    """
    return OntologyGuidedEventLane(
        ontology, provider,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, context_tokens=context_tokens)


def replay_provider(store_path: Path = ANSWER_STORE):
    """A provider that can only replay. `inner=None`, so a miss raises and names the request.

    The model identity is read from the store's own rows rather than from configuration: that
    is what makes the committed file *sufficient*, since a report needing a second file to say
    which model its answers came from could not be regenerated from the answers alone.
    """
    store = AnswerStore(store_path)
    if not len(store):
        raise FileNotFoundError(
            f"the answer store {store_path} is empty or absent; run "
            f"`python -m benchmarks.extraction.v1 event-build` with the generation server up "
            f"before reporting")
    return ReplayingGenerationProvider(store, None, prompt_version=EVENT_PROMPT_VERSION)


def run_event_lane(
    cases: list[EventCase],
    *,
    passages: dict[str, dict],
    ontology,
    provider,
    scopes: dict[str, Any],
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    catalog_root: Path | None = None,
    implementation_commit: str | None = None,
) -> EventLaneReport:
    """Drive the lane over every case under every scope and score it.

    One lane instance per scope even though the lane takes none, because the alternative is a
    report that *claims* the scope makes no difference without ever running the other one. The
    scope is still consulted per case — for what it would have offered — and that measurement
    is the routing evidence.
    """
    vocabulary = offered_vocabulary(ontology)
    views: dict[str, ScopeView] = {}
    for scope_name in SCOPES:
        scope = scopes[scope_name]
        lane = build_lane(ontology, provider, context_tokens=context_tokens)
        scored = [
            _score_case(case, lane=lane, scope=scope, scope_name=scope_name,
                        ontology=ontology, passages=passages, vocabulary=vocabulary)
            for case in cases
        ]
        views[scope_name] = ScopeView(
            name=scope_name, cases=scored, totals=totals(scored, RATIO_DIGITS))

    return EventLaneReport(
        benchmark_version=BENCHMARK_VERSION,
        ontology_definition_hash=ontology.definition_hash,
        normalization_corpus=runner.corpus_identity(passages, catalog_root),
        implementation_commit=implementation_commit or runner.head_commit(),
        lane={"name": EVENT_LANE_NAME, "version": EVENT_LANE_VERSION,
              "prompt_version": EVENT_PROMPT_VERSION},
        model={
            "identity": str(provider.model_id),
            "temperature": TEMPERATURE,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "context_tokens": context_tokens,
            "output_budget": "min(max_output_tokens, context_tokens - estimated prompt tokens)",
            "replay_only": True,
        },
        answer_store=_answer_store_identity(provider),
        vocabulary={
            "event_types": len(vocabulary.event_type_ids),
            "event_type_ids": list(vocabulary.event_type_ids),
            "relationship_predicates": len(vocabulary.relationship_ids),
            "relationship_ids": list(vocabulary.relationship_ids),
            "participant_roles": len(vocabulary.roles),
            "entity_types": len(vocabulary.entity_types),
            "declared_property_names": len(vocabulary.property_names),
            # Counted off the definitions, not declared. It was the literal `0` until
            # 2026-08-02 and the test that checked it compared that literal to itself, so the
            # row the routing argument leans on — "there is no surface a menu could have been
            # fitted to" — was the only number in this table that measured nothing.
            "event_types_declaring_an_alias": sum(
                1 for definition in ontology.registry.by_category("event_type")
                if tuple(definition.aliases or ())),
            "routing": ROUTING,
        },
        views=views,
        comparison=_comparison(views),
        case_index={
            case.case_id: {"category": case.category, "lane": case.lane,
                           "source_file": case.source_file, "notes": case.notes}
            for case in cases
        },
    )


def _score_case(case: EventCase, *, lane, scope, scope_name, ontology, passages, vocabulary):
    row = passages[case.passage_id]
    text = row["text"]
    extraction = lane.extract_passage(
        text,
        context=PassageContext(
            passage_id=case.passage_id,
            document_type=row["document_type"],
            form=row.get("form"),
            filing_date=row.get("filing_date"),
            heading_path=tuple(row.get("heading_path") or ())),
        document_id=row["document_id"])

    offered = tuple(sorted(scope.candidates_for(text)))
    event_types = tuple(sorted(set(offered) & set(vocabulary.event_type_ids)))
    return score_case(
        case_id=case.case_id,
        scope=scope_name,
        passage_id=case.passage_id,
        gold_events=case.gold_events,
        gold_relationships=case.gold_relationships,
        expected_abstentions=case.expected_abstentions,
        events=list(extraction.events),
        relationships=list(extraction.relationships),
        issues=list(extraction.issues),
        request_digest=extraction.request_sha256,
        validation=_validation_census(
            list(extraction.events), list(extraction.relationships),
            ontology=ontology, passages=passages),
        # Two conditions, not one: the anchor resolves in the catalog *and* the quoted span is
        # verbatim inside that passage. The weaker form would call a fabricated quotation on a
        # real passage id grounded evidence.
        resolves_evidence=lambda payload: (
            payload.passage_id in passages
            and payload.raw_text in passages[payload.passage_id]["text"]),
        scope_concept_ids=offered,
        scope_event_type_ids=event_types,
    )


def _validation_census(events, relationships, *, ontology, passages):
    """What happens to these payloads after the lane: assembly, then the vocabulary's verdict.

    V1 §11 criterion 1 is that every emitted claim passes `ontology.validate_claims` with zero
    errors. A report that scored the lane without driving its payloads this far could not say
    whether it holds for events, and no report ever has.
    """
    if not (events or relationships):
        return {"warnings": (), "errors": ()}
    assembly = assemble_events(
        list(events), list(relationships), passage_rows=passages)
    findings = validate(
        assembly.claims, ontology=ontology,
        passages=narrative_runner._PassageSource(passages))
    return {
        "warnings": tuple(sorted(
            f"{f.claim_id or EM_DASH}: {f.detail}"
            for f in findings.warnings if f.code == ONTOLOGY_WARNING)),
        "errors": tuple(sorted(f"{f.code}: {f.detail}" for f in findings.errors)),
    }


def _answer_store_identity(provider) -> dict[str, Any]:
    store = getattr(provider, "store", None)
    path = getattr(store, "path", None)
    if store is None or path is None or not Path(path).is_file():
        return {"path": None, "answers": 0, "bytes": 0, "sha256": None}
    data = Path(path).read_bytes()
    return {
        "path": str(Path(path).relative_to(PACKAGE_ROOT)).replace("\\", "/"),
        "answers": len(store),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _comparison(views: dict[str, ScopeView]) -> dict[str, Any]:
    """What the two scopes did, and the question this lane lets them be compared on.

    Every payload comparison here is expected to come out identical and is computed anyway:
    "the scope makes no difference" is a claim about a run, and a report that asserted it
    without running the second scope would be asserting its own design.
    """
    lexical, hybrid = views["lexical"], views["hybrid"]
    identical_requests = sum(
        1 for case in lexical.cases
        if case.request_digest == hybrid.case(case.case_id).request_digest)
    identical_payloads = sum(
        1 for case in lexical.cases
        if _payload_keys(case) == _payload_keys(hybrid.case(case.case_id)))
    delta = {
        name: round(hybrid.totals["scores"][name] - lexical.totals["scores"][name],
                    RATIO_DIGITS)
        for name in lexical.totals["scores"]
    }
    reach = [
        {"case_id": case.case_id,
         "gold_event_types": sorted({w.event_type_id for w in case.gold_events}),
         "lexical_scope_concepts": len(case.scope_concept_ids),
         "hybrid_scope_concepts": len(hybrid.case(case.case_id).scope_concept_ids),
         "gold_event_types_the_lexical_scope_offers":
             list(case.gold_event_types_in_scope),
         "gold_event_types_the_hybrid_scope_offers":
             list(hybrid.case(case.case_id).gold_event_types_in_scope)}
        for case in lexical.cases
    ]
    return {
        "cases": len(lexical.cases),
        "cases_with_identical_requests": identical_requests,
        "cases_with_identical_payloads": identical_payloads,
        "score_delta_hybrid_minus_lexical": dict(sorted(delta.items())),
        "scope_reachability": reach,
        "gold_event_types_reached_by_any_scope": sum(
            len(entry["gold_event_types_the_lexical_scope_offers"])
            + len(entry["gold_event_types_the_hybrid_scope_offers"]) for entry in reach),
    }


def _payload_keys(case: CaseScore) -> tuple[str, ...]:
    return tuple(sorted(
        [f"event:{e.event_type_id}:{e.occurred_on}:{e.announced_on}:"
         + ",".join(sorted(p.entity_id for p in e.participants)) for e in case.events]
        + [f"rel:{r.relationship_id}:{r.source_id}->{r.target_id}"
           for r in case.relationships]))


# -- building ---------------------------------------------------------------------------------------


def build_report(
    *,
    catalog_root: Path | None = None,
    implementation_commit: str | None = None,
    store_path: Path = ANSWER_STORE,
    provider=None,
) -> EventLaneReport:
    """The whole thing, from disk: cases, corpus, ontology, both scopes, the stored answers."""
    from extraction.providers import EmbeddingConfig, ProviderConfig

    ontology = load_ontology()
    config = hybrid_scope_runner._extraction_config()
    embedding = EmbeddingConfig.from_config(config)
    return run_event_lane(
        load_event_cases(),
        passages=runner.load_passages(catalog_root),
        ontology=ontology,
        provider=provider if provider is not None else replay_provider(store_path),
        scopes=narrative_runner.build_scopes(
            ontology, embedding_model=embedding.model, dimensions=embedding.dimensions),
        context_tokens=ProviderConfig.from_config(config).context_tokens,
        catalog_root=catalog_root,
        implementation_commit=implementation_commit,
    )


# -- rendering: JSON ------------------------------------------------------------------------------


def render_json(report: EventLaneReport) -> str:
    payload = {
        "benchmark_version": report.benchmark_version,
        "ontology_definition_hash": report.ontology_definition_hash,
        "normalization_corpus": report.normalization_corpus,
        "implementation_commit": report.implementation_commit,
        "lane": report.lane,
        "model": report.model,
        "answer_store": report.answer_store,
        "vocabulary": report.vocabulary,
        "dimensions": [{"name": name, "score_key": key, "denominator": denominator}
                       for name, key, denominator in DIMENSIONS],
        "failure_categories": list(FAILURE_CATEGORIES),
        "failure_decision_order": list(DECISION_ORDER),
        "structured_output": report.structured_output,
        "unusable_answers": [dict(entry) for entry in UNUSABLE_ANSWERS],
        "review_questions": [dict(entry) for entry in REVIEW_QUESTIONS],
        "cases": {case_id: dict(entry)
                  for case_id, entry in sorted(report.case_index.items())},
        "views": {name: _view_json(view) for name, view in sorted(report.views.items())},
        "comparison": report.comparison,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _view_json(view: ScopeView) -> dict[str, Any]:
    return {"scope": view.name, "totals": view.totals,
            "cases": [_case_json(case) for case in view.cases]}


def _case_json(case: CaseScore) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "passage_id": case.passage_id,
        "request_digest": case.request_digest,
        "counts": case.counts,
        "scores": case.scores,
        "scope_concept_ids": list(case.scope_concept_ids),
        "scope_event_type_ids": list(case.scope_event_type_ids),
        "gold_event_types_in_scope": list(case.gold_event_types_in_scope),
        "gold_events": [
            {"event_type_id": g.event_type_id, "occurred_on": g.occurred_on,
             "announced_on": g.announced_on,
             "participants": [{"role": p.role, "entity_id": p.entity_id,
                               "entity_type": p.entity_type} for p in g.participants],
             "properties": g.properties}
            for g in case.gold_events
        ],
        "gold_relationships": [
            {"relationship_id": g.relationship_id, "source_id": g.source_id,
             "source_type": g.source_type, "target_id": g.target_id,
             "target_type": g.target_type}
            for g in case.gold_relationships
        ],
        "emitted_events": [
            # The deterministic id, in the artifact rather than only in the payload. It was
            # absent until review 2026-08-02: `event_id` collided on this very corpus (two
            # undated executive changes on one 8-K shared a triple until participants entered
            # the digest), and a report that never printed the id could not have shown a
            # recurrence. Stage 13's catalogs are keyed on it, so it is part of what this
            # report has to make inspectable.
            {"event_id": event_id(
                e.event_type_id, e.occurred_on, e.passage_id,
                tuple((p.role, p.entity_id) for p in e.participants)),
             "event_type_id": e.event_type_id, "occurred_on": e.occurred_on,
             "announced_on": e.announced_on,
             "participants": [{"role": p.role, "entity_id": p.entity_id,
                               "entity_type": p.entity_type, "entity_text": p.entity_text,
                               "named": p.named} for p in e.participants],
             "properties": dict(e.properties),
             "raw_text": e.raw_text,
             # Read, never asserted. The metric report printed `"resolves": true` on every
             # emitted claim including the ones where nothing had computed it.
             "evidence": {"passage_id": e.passage_id, "resolves": resolves}}
            for e, resolves in zip(case.events, case.event_evidence_resolves)
        ],
        "emitted_relationships": [
            {"relationship_id": r.relationship_id, "source_id": r.source_id,
             "source_type": r.source_type, "target_id": r.target_id,
             "target_type": r.target_type, "raw_text": r.raw_text,
             "evidence": {"passage_id": r.passage_id, "resolves": resolves}}
            for r, resolves in zip(case.relationships, case.relationship_evidence_resolves)
        ],
        "matched_events": [
            {"event_type_id": m.event_type_id, "basis": m.basis,
             "shared_entity_ids": m.shared_entity_ids,
             "expected": {"occurred_on": m.expected_occurred_on,
                          "announced_on": m.expected_announced_on,
                          "properties": m.expected_properties},
             "emitted": {"occurred_on": m.emitted_occurred_on,
                         "announced_on": m.emitted_announced_on,
                         "properties": m.emitted_properties},
             "additional_property_names": list(m.additional_property_names),
             "participants": [
                 {"role": p.role, "expected_entity_id": p.expected_entity_id,
                  "emitted_entity_id": p.emitted_entity_id,
                  "expected_entity_type": p.expected_entity_type,
                  "emitted_entity_type": p.emitted_entity_type,
                  "emitted_entity_text": p.emitted_entity_text,
                  "emitted_named": p.emitted_named,
                  "dimensions": {f"{d}_ok": getattr(p, f"{d}_ok")
                                 for d in PARTICIPANT_DIMENSIONS},
                  "dimensions_applicable": {d: p.applies(d) for d in PARTICIPANT_DIMENSIONS}}
                 for p in m.participants],
             "dimensions": {f"{d}_ok": getattr(m, f"{d}_ok") for d in EVENT_MATCH_DIMENSIONS},
             "dimensions_applicable": {d: m.applies(d) for d in EVENT_MATCH_DIMENSIONS}}
            for m in case.matched_events
        ],
        "matched_relationships": [
            {"relationship_id": m.relationship_id, "basis": m.basis,
             "expected": {"source_id": m.expected_source_id,
                          "target_id": m.expected_target_id,
                          "source_type": m.expected_source_type,
                          "target_type": m.expected_target_type},
             "emitted": {"source_id": m.emitted_source_id,
                         "target_id": m.emitted_target_id,
                         "source_type": m.emitted_source_type,
                         "target_type": m.emitted_target_type},
             "dimensions": {f"{d}_ok": getattr(m, f"{d}_ok")
                            for d in RELATIONSHIP_MATCH_DIMENSIONS}}
            for m in case.matched_relationships
        ],
        "missed_events": list(case.missed_events),
        "missed_relationships": list(case.missed_relationships),
        "additional_events": list(case.additional_events),
        "additional_relationships": list(case.additional_relationships),
        "expected_abstentions": [
            {"reason": v.reason, "detail": v.detail, "silence_kept": v.silence_kept,
             "any_issue_recorded": v.any_issue_recorded, "answer_usable": v.answer_usable,
             "code_recorded": v.code_recorded, "honoured": v.honoured}
            for v in case.abstentions
        ],
        "failures": [
            {"category": f.category, "source": f.source, "subject": f.subject,
             "detail": f.detail}
            for f in case.failures
        ],
        "issues_by_code": case.issues_by_code,
        "rejections_by_code": case.rejections_by_code,
        "model_abstentions_by_code": case.model_abstentions_by_code,
        "ontology_warnings": list(case.ontology_warnings),
        "ontology_errors": list(case.ontology_errors),
    }


# -- rendering: Markdown ---------------------------------------------------------------------------


def render_markdown(report: EventLaneReport) -> str:
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    lines: list[str] = [
        "# Event and relationship lane — benchmark v1 results, both candidate scopes",
        "",
        "Generated by `python -m benchmarks.extraction.v1 event-report`, replay-only from "
        f"`{report.answer_store['path']}`. Every number is produced by driving "
        "`OntologyGuidedEventLane` over the reviewed cases and scoring it with "
        "`benchmarks/extraction/v1/event_evaluation.py`. **If this report disagrees with the "
        "lane, the report is wrong.**",
        "",
        "## Identity",
        "",
        "| | |",
        "| --- | --- |",
        f"| benchmark version | `{report.benchmark_version}` |",
        f"| implementation commit | `{report.implementation_commit}` |",
        f"| lane | `{report.lane['name']}` v`{report.lane['version']}`, prompt "
        f"v`{report.lane['prompt_version']}` |",
        f"| model | `{report.model['identity']}` |",
        f"| temperature / max output tokens | {report.model['temperature']} / "
        f"{report.model['max_output_tokens']} |",
        f"| ontology definition hash | `{report.ontology_definition_hash}` |",
        f"| normalized documents | {report.normalization_corpus['documents']} |",
        f"| normalized passages | {report.normalization_corpus['passages']} |",
        f"| `documents.jsonl` sha256 | `{report.normalization_corpus['documents_sha256']}` |",
        f"| answer store | `{report.answer_store['path']}`, "
        f"{report.answer_store['answers']} answers, {report.answer_store['bytes']:,} bytes |",
        f"| answer store sha256 | `{report.answer_store['sha256']}` |",
        "",
        "No duration, token count or timestamp appears in this report or in the answer store. "
        "STAGE_09 §11.2 settled the rule: those numbers move between two identical requests, "
        "so they may be printed by the build command and may not enter an artifact required "
        "to be byte-identical.",
        "",
    ]
    lines += _routing_markdown(report)
    lines += [
        "## Headline — the scored dimensions under both scopes",
        "",
        "Scored independently, because a lane can be right about the event type and wrong "
        "about the day. Denominators are printed because they are small enough here that a "
        "rate without one says almost nothing.",
        "",
        "| Dimension | Denominator | Lexical | Hybrid |",
        "| --- | --- | --- | --- |",
    ]
    for name, key, denominator in DIMENSIONS:
        lines.append(
            f"| {name} | {denominator} "
            f"({lexical.totals['score_denominators'][key]} lex / "
            f"{hybrid.totals['score_denominators'][key]} hyb) | "
            f"**{lexical.totals['scores'][key]:.3f}** | "
            f"**{hybrid.totals['scores'][key]:.3f}** |")
    lines += [
        f"| matched/emitted, events *(not precision)* | matched / emitted events | "
        f"{lexical.totals['scores']['matched_over_emitted_events']:.3f} | "
        f"{hybrid.totals['scores']['matched_over_emitted_events']:.3f} |",
        f"| matched/emitted, relationships *(not precision)* | matched / emitted edges | "
        f"{lexical.totals['scores']['matched_over_emitted_relationships']:.3f} | "
        f"{hybrid.totals['scores']['matched_over_emitted_relationships']:.3f} |",
        "",
        "`matched/emitted` is reported for run-to-run movement and is **never** an accuracy "
        "and never precision. Each case annotates a deliberate subset of its passage, so an "
        "unmatched emitted event is a **non-gold addition** — usually a right answer the case "
        "did not list — and never a false positive (V1 §4.0, STAGE_11 §2).",
        "",
        "**The two date dimensions score abstention as an answer.** An event whose gold states "
        "no `occurred_on` is correct only when the lane states none either: the announcement "
        "date and the occurrence date are different facts about different days, so filling in "
        "the one that is available is wrong rather than generous (V1 §4.0b). This is the one "
        "place in this benchmark where emitting *less* is the scored answer.",
        "",
        "**How a gold payload is paired with an emitted one decides what some of these can "
        "say.** Within an event type, pairs are ranked by shared participant entity ids and "
        "taken greedily; edges the same way over their endpoints. A pair found by shared "
        "participants already agrees about at least one of them, so the participant dimensions "
        "are partly tautologous on those pairs and are not on the others. The census: "
        + ", ".join(
            f"**{count}** {basis.replace('_', ' ')}"
            for basis, count in lexical.totals["event_pairs_by_basis"].items())
        + " for events, "
        + ", ".join(
            f"**{count}** {basis.replace('_', ' ')}"
            for basis, count in lexical.totals["relationship_pairs_by_basis"].items())
        + " for relationships (lexical scope).",
        "",
        "## Counts",
        "",
        "| Measure | Lexical | Hybrid |",
        "| --- | --- | --- |",
    ]
    for label, key in (
            ("cases", "cases"),
            ("distinct passages behind them", "distinct_passages"),
            ("gold events", "gold_events"),
            ("emitted events", "emitted_events"),
            ("matched events", "matched_events"),
            ("missed events", "missed_events"),
            ("non-gold events emitted (unscored, not errors)", "additional_events"),
            ("gold relationships", "gold_relationships"),
            ("emitted relationships", "emitted_relationships"),
            ("matched relationships", "matched_relationships"),
            ("missed relationships", "missed_relationships"),
            ("non-gold relationships emitted (unscored, not errors)",
             "additional_relationships"),
            ("gold participants", "gold_participants"),
            ("gold participants inside a matched event", "matched_participants"),
            ("declared properties emitted that gold does not list", "additional_property_names"),
            ("expected abstentions", "expected_abstentions"),
            ("honoured expected abstentions", "honoured_abstentions"),
            ("expectations whose own code the lane recorded", "code_agreeing_abstentions"),
            ("payloads the lane rejected", "rejected_payloads"),
            ("findings the model chose", "model_abstentions"),
            ("findings the model chose, less lane decisions", "model_chosen_abstentions"),
            ("ontology warnings", "ontology_warnings"),
            ("**ontology validation errors**", "ontology_errors"),
            ("classified failures", "failures")):
        lines.append(f"| {label} | {lexical.totals[key]} | {hybrid.totals[key]} |")
    lines += [
        "",
        "**V1 §11 acceptance criterion 1 for events and relationships: "
        + ("does not hold**" if lexical.totals["ontology_errors"]
           or hybrid.totals["ontology_errors"]
           else "holds** — every emitted event and relationship reached `OntologyClaim` "
                "through `core.assembly.assemble_events` and validated with zero errors "
                "under both scopes")
        + ".",
        "",
        "## Structured-output validity",
        "",
        "| | |",
        "| --- | --- |",
    ]
    for label, key in (("provider calls the lane made", "provider_calls"),
                       ("distinct requests behind them", "distinct_requests"),
                       ("conformant on the first attempt", "conformant_first_attempt"),
                       ("conformant after a retry", "conformant_after_retry"),
                       ("never conformant", "never_conformant")):
        lines.append(f"| {label} | {report.structured_output[key]} |")
    lines += [
        "",
        f"**{report.structured_output['provider_calls']} provider calls behind "
        f"{report.structured_output['distinct_requests']} distinct requests**, because the "
        "lane's menu is a function of the vocabulary and not of the passage, so the two scopes "
        "build the same prompt on every case and replay the same stored answer. "
        "“Conformant after a retry” is structurally zero rather than measured as zero: the "
        "adapter retries transport failures and 5xx only, and an identical request at "
        "temperature 0 cannot change.",
        "",
    ]
    lines += _issue_markdown(report)
    lines += _failure_markdown(report)
    lines += _comparison_markdown(report)
    lines += _review_question_markdown()
    lines += _per_case_markdown(report)
    return "\n".join(lines) + "\n"


def _routing_markdown(report: EventLaneReport) -> list[str]:
    routing = report.vocabulary["routing"]
    comparison = report.comparison
    lines = [
        "## The routing mechanism, and the options it was chosen over",
        "",
        f"**Chosen: {routing['chosen']}.** {routing['summary']}",
        "",
        "| | |",
        "| --- | --- |",
        f"| declared event types offered | {report.vocabulary['event_types']} |",
        f"| of those, declaring any alias | "
        f"{report.vocabulary['event_types_declaring_an_alias']} |",
        f"| relationship predicates derived from their `allowed_relationships` | "
        f"{report.vocabulary['relationship_predicates']} |",
        f"| participant roles / entity types / property names | "
        f"{report.vocabulary['participant_roles']} / "
        f"{report.vocabulary['entity_types']} / "
        f"{report.vocabulary['declared_property_names']} |",
        f"| gold event types either candidate scope offers, over "
        f"{comparison['cases']} cases × 2 scopes | "
        f"**{comparison['gold_event_types_reached_by_any_scope']}** |",
        "",
        "**That last row is the measurement the rejected options turn on**, and it is "
        "re-measured on every regeneration rather than transcribed.",
        "",
        f"What the choice costs: {routing['cost']}",
        "",
        "| Rejected | Why not |",
        "| --- | --- |",
    ]
    for entry in routing["rejected"]:
        lines.append(f"| {entry['option']} | {_cell(entry['why_not'])} |")
    lines += [
        "",
        f"**A consequence worth stating plainly:** {routing['consequence']}",
        "",
        "| Case | Gold event types | Lexical scope offers | Hybrid scope offers | "
        "Concepts offered (lex/hyb) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in comparison["scope_reachability"]:
        lines.append(
            f"| [`{entry['case_id']}`](#{_anchor(entry['case_id'])}) | "
            + ", ".join(f"`{t}`" for t in entry["gold_event_types"])
            + " | "
            + (", ".join(f"`{t}`" for t in
                         entry["gold_event_types_the_lexical_scope_offers"]) or "**none**")
            + " | "
            + (", ".join(f"`{t}`" for t in
                         entry["gold_event_types_the_hybrid_scope_offers"]) or "**none**")
            + f" | {entry['lexical_scope_concepts']}/{entry['hybrid_scope_concepts']} |")
    lines.append("")
    return lines


def _issue_markdown(report: EventLaneReport) -> list[str]:
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    lines = [
        "## Rejections and findings the model chose",
        "",
        "A **rejection** is this lane refusing a payload the model proposed; a **finding the "
        "model chose** is the model declining or flagging something itself. Both are recorded "
        "and they are not the same silence.",
        "",
        "| Code | Rejected (lex/hyb) | Not a rejection (lex/hyb) | May the model choose it? |",
        "| --- | --- | --- | --- |",
    ]
    codes = sorted(set(lexical.totals["issues_by_code"]) | set(hybrid.totals["issues_by_code"]))
    for code in codes:
        lines.append(
            f"| `{code}` | "
            f"{lexical.totals['rejections_by_code'].get(code, 0)}/"
            f"{hybrid.totals['rejections_by_code'].get(code, 0)} | "
            f"{lexical.totals['model_abstentions_by_code'].get(code, 0)}/"
            f"{hybrid.totals['model_abstentions_by_code'].get(code, 0)} | "
            + ("yes" if code in EVENT_MODEL_ABSTENTION_REASONS else "no") + " |")
    if not codes:
        lines.append(f"| {EM_DASH} | {EM_DASH} | {EM_DASH} | the lane recorded no issue |")
    lines += [
        "",
        f"Totals: **{lexical.totals['rejected_payloads']} rejections and "
        f"{lexical.totals['model_abstentions']} findings the model chose** under the lexical "
        f"scope, {hybrid.totals['rejected_payloads']} and "
        f"{hybrid.totals['model_abstentions']} under the hybrid one. "
        f"{sum(1 for code in codes if code in EVENT_MODEL_ABSTENTION_REASONS)} of the "
        f"{len(codes)} codes above are ones the model may choose (the vocabulary offers "
        f"{len(EVENT_MODEL_ABSTENTION_REASONS)}), so the split is read off "
        "`EventIssue.rejected_claim` and never inferred from the code. Subtracting the "
        + ", ".join(f"`{code}`" for code in LANE_DECISIONS_WITHOUT_AN_ANSWER)
        + " decisions the lane takes with no answer in front of it, the model itself chose "
        f"**{lexical.totals['model_chosen_abstentions']}** under the lexical scope and "
        f"**{hybrid.totals['model_chosen_abstentions']}** under the hybrid one.",
        "",
    ]
    return lines


def _failure_markdown(report: EventLaneReport) -> list[str]:
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    lines = [
        "## Failure classification",
        "",
        "One category per failure, decided in the order below, first match wins, so two runs "
        "classify one failure the same way. The classified set is every gold payload the lane "
        "did not emit, every matched pair failing an applicable dimension, and every expected "
        "abstention that was not honoured. Non-gold emissions are counted and left "
        "unclassified, because they are not errors and classifying them would make "
        "`matched/emitted` a precision under another name.",
        "",
        "Decision order: " + " → ".join(f"`{c}`" for c in DECISION_ORDER),
        "",
        "| Category | Lexical | Hybrid |",
        "| --- | --- | --- |",
    ]
    for category in FAILURE_CATEGORIES:
        lines.append(
            f"| `{category}` | {lexical.totals['failures_by_category'][category]} | "
            f"{hybrid.totals['failures_by_category'][category]} |")
    lines += [
        "",
        "| Scope | Case | Category | Subject | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    any_failure = False
    for view in (lexical, hybrid):
        for case in view.cases:
            for failure in case.failures:
                any_failure = True
                lines.append(
                    f"| {view.name} | [`{case.case_id}`](#{_anchor(case.case_id)}) | "
                    f"`{failure.category}` | `{failure.subject}` | {_cell(failure.detail)} |")
    if not any_failure:
        lines.append(f"| {EM_DASH} | {EM_DASH} | {EM_DASH} | {EM_DASH} | no gold payload was "
                     "missed and no matched pair failed a dimension |")
    lines.append("")
    return lines


def _comparison_markdown(report: EventLaneReport) -> list[str]:
    comparison = report.comparison
    lines = [
        "## Lexical versus hybrid",
        "",
        f"The two scopes issue **identical requests on "
        f"{comparison['cases_with_identical_requests']} of {comparison['cases']} cases** and "
        f"produce **identical payloads on {comparison['cases_with_identical_payloads']} of "
        f"{comparison['cases']}**. That is not a measurement of the scopes: this lane's menu "
        "is derived from the vocabulary and no candidate scope is injected into it, so the two "
        "runs are the same run by construction. It is computed and printed anyway, because "
        "asserting it without running the second scope would be asserting the design rather "
        "than the result.",
        "",
        "| Score | Hybrid − lexical |",
        "| --- | --- |",
    ]
    for key in sorted(comparison["score_delta_hybrid_minus_lexical"]):
        lines.append(
            f"| {key} | {comparison['score_delta_hybrid_minus_lexical'][key]:+.3f} |")
    lines += [
        "",
        "The comparison this lane *can* make is the reachability one in the routing section: "
        f"across {comparison['cases']} cases and both scopes, the candidate scopes offer "
        f"**{comparison['gold_event_types_reached_by_any_scope']}** of the gold event types.",
        "",
    ]
    return lines


def _review_question_markdown() -> list[str]:
    lines = [
        "## What this run puts back to the reviewers",
        "",
        "Disagreements between the lane and a reviewed case that look like case questions "
        "rather than lane failures. **A stage never acts on these**: editing a case because a "
        "lane disagreed with it would stop the benchmark measuring anything. Each is scored "
        "under the case as written, which is the harsher reading.",
        "",
    ]
    for entry in REVIEW_QUESTIONS:
        lines += [
            f"**`{entry['case_id']}`** — {entry['subject']}",
            "",
            "| | |",
            "| --- | --- |",
            f"| the case says | {_cell(entry['the_case_says'])} |",
            f"| this run found | {_cell(entry['this_run_found'])} |",
            f"| the question | {_cell(entry['question'])} |",
            "",
        ]
    return lines


def _per_case_markdown(report: EventLaneReport) -> list[str]:
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    lines = [
        "## Per-case results",
        "",
        "| Case | Gold events | Gold edges | Matched (lex/hyb) | Non-gold added (lex/hyb) | "
        "Failures (lex/hyb) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in lexical.cases:
        other = hybrid.case(case.case_id)
        lines.append(
            f"| [`{case.case_id}`](#{_anchor(case.case_id)}) | "
            f"{case.counts['gold_events']} | {case.counts['gold_relationships']} | "
            f"{case.counts['matched_events']}+{case.counts['matched_relationships']}/"
            f"{other.counts['matched_events']}+{other.counts['matched_relationships']} | "
            f"{case.counts['additional_events']}+{case.counts['additional_relationships']}/"
            f"{other.counts['additional_events']}+"
            f"{other.counts['additional_relationships']} | "
            f"{case.counts['failures']}/{other.counts['failures']} |")
    lines.append("")
    for case in lexical.cases:
        lines += _case_markdown(report, case, hybrid.case(case.case_id))
    return lines


def _case_markdown(report, case: CaseScore, other: CaseScore) -> list[str]:
    lines = [
        f"### {case.case_id}",
        "",
        f"- passage: `{case.passage_id}`",
        f"- request digest: "
        + (f"`{case.request_digest}` lexical, `{other.request_digest}` hybrid"
           + (" — **the same request**" if case.request_digest == other.request_digest
              else " — **two different requests**")
           if case.request_digest or other.request_digest
           else "no request was issued"),
        f"- candidate scope: {case.counts['scope_concepts']} concepts lexical, "
        f"{other.counts['scope_concepts']} hybrid; of those "
        f"{case.counts['scope_event_types']}/{other.counts['scope_event_types']} are event "
        f"types and {case.counts['gold_event_types_in_scope']}/"
        f"{other.counts['gold_event_types_in_scope']} are this case's gold event types",
        f"- gold {case.counts['gold_events']} events and "
        f"{case.counts['gold_relationships']} edges · emitted "
        f"{case.counts['emitted_events']} and {case.counts['emitted_relationships']} · "
        f"matched {case.counts['matched_events']} and "
        f"{case.counts['matched_relationships']} · rejected "
        f"{case.counts['rejected_payloads']} · model findings "
        f"{case.counts['model_abstentions']} (lexical)",
        "",
        "#### Gold events (lexical scope)",
        "",
        "| Event type | occurred_on | announced_on | Properties | Verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    matched = {}
    for match in case.matched_events:
        matched.setdefault(match.event_type_id, []).append(match)
    used: dict[str, int] = {}
    for gold in case.gold_events:
        pool = matched.get(gold.event_type_id) or []
        index = used.get(gold.event_type_id, 0)
        match = pool[index] if index < len(pool) else None
        used[gold.event_type_id] = index + 1
        if match is None:
            lines.append(
                f"| `{gold.event_type_id}` | {gold.occurred_on or EM_DASH} | "
                f"{gold.announced_on or EM_DASH} | "
                + (", ".join(f"`{k}`" for k in sorted(gold.properties)) or EM_DASH)
                + " | **MISSED** |")
            continue
        verdict = ("ok" if match.all_ok
                   else "**WRONG**: " + ", ".join(match.failed_dimensions))
        lines.append(
            f"| `{gold.event_type_id}` | "
            f"{_date_cell(match.expected_occurred_on, match.emitted_occurred_on)} | "
            f"{_date_cell(match.expected_announced_on, match.emitted_announced_on)} | "
            + (", ".join(f"`{k}`" for k in sorted(gold.properties)) or EM_DASH)
            + f" | {verdict} |")
    lines.append("")

    participants = [(m, p) for m in case.matched_events for p in m.participants]
    if participants:
        lines += [
            "#### Participants of the matched events (lexical scope)",
            "",
            "| Event type | Role | Expected id | Emitted id | Expected type | Emitted type | "
            "Named |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for match, participant in participants:
            lines.append(
                f"| `{match.event_type_id}` | `{participant.role}` | "
                f"`{participant.expected_entity_id}` | "
                # The *emitted* id, not the expected one. This column rendered
                # `expected_entity_id` until adversarial review caught it 2026-08-02: the
                # table showed gold in the lane's column, so the run's only
                # `participant_wrong` read as agreement while `participant_entity` scored
                # 0.857 three tables above. `_mark` still bolded it, which made the row look
                # deliberate rather than broken.
                + _mark(participant.emitted_entity_id, participant.participant_entity_ok)
                + f" | {participant.expected_entity_type} | "
                + _mark(participant.emitted_entity_type,
                        participant.participant_entity_type_ok)
                + " | "
                + ("yes" if participant.emitted_named
                   else "**unresolved**" if participant.emitted_named is False
                   else EM_DASH)
                + " |")
        lines.append("")

    if case.gold_relationships:
        lines += [
            "#### Gold relationships (lexical scope)",
            "",
            "| Predicate | Expected source → target | Emitted source → target | Verdict |",
            "| --- | --- | --- | --- |",
        ]
        # Positional within a predicate, mirroring the gold-events table above, because a dict
        # keyed on the predicate collapses two gold edges that share one — both rows would then
        # print the last match's verdict *(found by review 2026-08-02; the committed run has one
        # gold edge per predicate, so the defect is latent)*. `_pair_relationships` returns its
        # pairs in gold declaration order, which is what makes the index meaningful.
        edges: dict[str, list] = {}
        for match in case.matched_relationships:
            edges.setdefault(match.relationship_id, []).append(match)
        used_edges: dict[str, int] = {}
        for gold in case.gold_relationships:
            pool = edges.get(gold.relationship_id) or []
            index = used_edges.get(gold.relationship_id, 0)
            match = pool[index] if index < len(pool) else None
            used_edges[gold.relationship_id] = index + 1
            if match is None:
                lines.append(
                    f"| `{gold.relationship_id}` | `{gold.source_id}` → `{gold.target_id}` | "
                    f"{EM_DASH} | **MISSED** |")
                continue
            verdict = ("ok" if match.all_ok
                       else "**WRONG**: " + ", ".join(match.failed_dimensions))
            lines.append(
                f"| `{gold.relationship_id}` | `{gold.source_id}` → `{gold.target_id}` | "
                f"`{match.emitted_source_id}` → `{match.emitted_target_id}` | {verdict} |")
        lines.append("")

    if case.additional_events or case.additional_relationships:
        lines += [
            f"#### Emitted but not annotated ({len(case.additional_events)} events, "
            f"{len(case.additional_relationships)} edges, lexical scope)",
            "",
            "**Non-gold additions, not errors and not verified.** The case annotates a "
            "deliberate subset of its passage, so a correct payload the reviewers did not "
            "list lands here. Nothing in the benchmark scores these.",
            "",
            "| Kind | Payload |",
            "| --- | --- |",
        ]
        for key in case.additional_events:
            lines.append(f"| event | `{key}` |")
        for key in case.additional_relationships:
            lines.append(f"| relationship | `{key}` |")
        lines.append("")

    if case.abstentions:
        lines += [
            "#### Expected abstentions",
            "",
            "| Reason the case names | Silence kept | Answer usable | Honoured | "
            "Same code recorded |",
            "| --- | --- | --- | --- | --- |",
        ]
        for verdict in case.abstentions:
            lines.append(
                f"| `{verdict.reason}` | {'yes' if verdict.silence_kept else '**no**'} | "
                f"{'yes' if verdict.answer_usable else '**no**'} | "
                # A literal `'yes'` until adversarial review caught it 2026-08-02. The column
                # could not say no, so `NO_HEADCOUNT_STATED` read as honoured here while the
                # Counts table said 3 of 4 and the failure table listed that same expectation
                # as `silence_broken` — three renderings of one verdict, one of them a
                # constant, and the constant was the reassuring one.
                f"{'yes' if verdict.honoured else '**no**'} | "
                f"{'yes' if verdict.code_recorded else 'no'} |")
        lines.append("")

    if case.issues_by_code or other.issues_by_code:
        lines += ["#### Issues recorded", "",
                  "| Code | Lexical (rejected/chosen) | Hybrid (rejected/chosen) |",
                  "| --- | --- | --- |"]
        for code in sorted(set(case.issues_by_code) | set(other.issues_by_code)):
            lines.append(
                f"| `{code}` | {case.rejections_by_code.get(code, 0)}/"
                f"{case.model_abstentions_by_code.get(code, 0)} | "
                f"{other.rejections_by_code.get(code, 0)}/"
                f"{other.model_abstentions_by_code.get(code, 0)} |")
        lines.append("")
    return lines


def _date_cell(expected: str | None, emitted: str | None) -> str:
    """Expected beside emitted, with the disagreement marked and absence shown as absence."""
    if expected == emitted:
        return expected or f"{EM_DASH} *(none, correctly)*"
    return f"{expected or EM_DASH} / **{emitted or EM_DASH}**"


def _mark(value, ok: bool) -> str:
    text = f"`{value}`" if value else EM_DASH
    return text if ok else f"**{text}**"


# -- writing ------------------------------------------------------------------------------------


def write_reports(report: EventLaneReport, directory: Path = REPORTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{REPORT_STEM}.json"
    markdown_path = directory / f"{REPORT_STEM}.md"
    json_path.write_text(render_json(report), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
