"""Scoring the narrative lane against the reviewed prose cases, under both candidate scopes.

STAGE_11. The report this writes is the extraction evidence step 13 decides the lexical-versus-
hybrid default on; step 9 measured reachability and could not settle it.

**Generation and reporting are two commands, and only one of them is reproducible.**
`narrative-build` talks to the generation server once per distinct request and writes the
answers to `answers/narrative_v1.jsonl`. `narrative-report` runs replay-only off that file with
`inner=None`, so a missing answer raises and names the request instead of being scored as a
silence. Two `narrative-report` runs are byte-identical, `implementation_commit` the only
permitted difference — the same rule steps 6, 8 and 9 hold.

**It lives under `benchmarks/` because it must**, on the same terms as `runner.py`: it reads
gold YAML, and `tests/extraction/test_typed_selection.py` parses every module under
`extraction/` and fails on a benchmark import or path literal. The dependency runs one way.

**This module composes and renders; it computes no score.** Every dimension, every failure
category and every attribution comes from `narrative_evaluation.py`, which in turn takes the
six per-match dimensions from the table lane's own `evaluation.compare`. The defect this
discipline exists against is on the record: at step 6 a runner duplicated the verdict logic
and printed 46 `WRONG` rows beside an accuracy of 1.000 with every test green.

**No duration, token count or timestamp enters the artifact.** STAGE_09 §11.2 settled it and
STAGE_10 §7 restates it; the same rule is why the answer store holds no `attempts` and no
`latency_ms`. Operational statistics are printed by `narrative-build` and stay there.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from extraction.core.assembly import (
    assemble,
    declared_ambiguity_codes,
    deferred_metric_ids,
)
from extraction.core.periods import (
    DURATION,
    INSTANT,
    period_phrases,
    reporting_period_keys,
    resolve_period_phrase,
)
from extraction.core.validation import ONTOLOGY_WARNING, validate
from extraction.stages.narrative import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    TEMPERATURE,
    AnswerStore,
    OntologyGuidedNarrativeClaimLane,
    PassageContext,
    ReplayingGenerationProvider,
    request_identity,
)
from extraction.providers.public import ProviderResponseError
from extraction.stages.narrative.prompt import PROMPT_VERSION
from extraction.stages.narrative.public import (
    LANE_NAME,
    LANE_VERSION,
    MODEL_ABSTENTION_REASONS,
)
from extraction.stages.scoping import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_TOP_K,
    LexicalOntologyCandidateScope,
)
from extraction.stages.select import AliasIndex
from ontology import load_ontology

from . import hybrid_scope_runner, runner
from .narrative_evaluation import (
    DECISION_ORDER,
    DIMENSIONS,
    FAILURE_CATEGORIES,
    LANE_DECISIONS_WITHOUT_AN_ANSWER,
    SCORED_MATCH_DIMENSIONS,
    CaseScore,
    ExpectedAbstention,
    GoldObservation,
    MatchRecord,
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
    gold_period_key as _gold_period_key,
)

REPORT_STEM = "narrative_lane_v1"

PACKAGE_ROOT = Path(__file__).resolve().parent
ANSWERS_DIR = PACKAGE_ROOT / "answers"
ANSWER_STORE = ANSWERS_DIR / "narrative_v1.jsonl"

# The two scopes, in the order they are reported. `lexical` first because it is the shipped
# `scoping.strategy` and the baseline hybrid has to beat.
SCOPES: tuple[str, ...] = ("lexical", "hybrid")

# Which reviewed cases this lane is answerable for. STAGE_11 §1: every `narrative` case, plus
# the one whose lane is `either`. Table cases are step 6's and are not re-scored.
NARRATIVE_LANES = frozenset({"narrative", "either"})

# -- declared data -----------------------------------------------------------------------------
# Transcriptions and observations that the report states rather than recomputes. Each is
# checked by `tests/extraction/test_narrative_lane_report.py` against the run's own output, so
# a stale transcription fails rather than misleads.

# `negative-ambiguous-alias-bare-gross-profit` is the `lane: either` case, and its passage is a
# **table** passage. `OntologyGuidedNarrativeClaimLane.supports()` requires
# `passage_kind == "narrative"`, so typed selection would never offer this passage to this
# lane; the report reaches it through `extract_passage`, which is the same code path without
# the routing question. Recorded because a reader would otherwise take a narrative-lane score
# on it as evidence about a passage the pipeline routes elsewhere.
TABLE_PASSAGE_CASE = "negative-ambiguous-alias-bare-gross-profit"

# The two wordings step 8 measured as lexically unreachable and step 9 measured semantically.
# This is the first stage that can say whether *reaching* the concept produced a correct claim,
# which is the question STAGE_11 §5 asks these probes to answer.
# Disagreements between the lane and a reviewed case that looked like case errors rather than
# lane errors. Stage 11 recorded them and **did not act on them**: STAGE_11 forbids changing
# the benchmark, and a stage that edited a case because its own lane disagreed would have
# stopped measuring anything. They are stated with the passage's own words so a founder can
# settle them, and the `resolution` field is where that answer lands.
#
# The entry below is kept **after** being settled rather than deleted, because the record of a
# lane and a benchmark disagreeing — and of which one turned out to be wrong — is worth more
# than a report that only ever shows agreement.
REVIEW_QUESTIONS: tuple[dict[str, str], ...] = (
    {"case_id": "population-portfolio-mdna-fy2023-10k",
     "the_case_says": "gold_claims: [] with an expected DEFINITIONAL_NOT_OBSERVATIONAL "
                      "abstention, on the grounds that “the value for the period is "
                      "reported in the KPI table at open-20231231.htm#p105, not here”",
     "this_run_found": "the lane emitted pct_homes_on_market_gt_120_days = 18 percent at "
                       "2023-12-31, quoting the passage's own sentence “As of December 31, "
                       "2023, such homes represented 18% of our portfolio, compared to 21% "
                       "for the broader market”",
     "question": "the paragraph both defines the metric and states a value for the period. "
                 "If the sentence above is observational, the case's expected abstention is "
                 "wrong and this claim is gold; if it is not, the lane is mining a definition.",
     "resolution": "**Settled 2026-08-02 — the case was wrong and the lane was right.** "
                   "Founder decision: the passage is observational. The case now carries the "
                   "18% observation as gold with population.definition_raw “such homes "
                   "represented 18% of our portfolio”, and the "
                   "DEFINITIONAL_NOT_OBSERVATIONAL abstention is gone. The comparison figure "
                   "21% stays unscored — the broader market is not a subject V1 can carry. "
                   "Nothing in the lane changed: the answer store is byte-identical and this "
                   "report was rebuilt by replay. See V1_CLAIM_EXTRACTION §4.0a."},
)

PARAPHRASE_PROBE_CONCEPT = "pct_homes_on_market_gt_120_days"
PARAPHRASE_PROBES: tuple[dict[str, str], ...] = (
    {"case_id": "letter-prose-inventory-and-120d-q2-2022",
     "wording": "5% of our homes were listed on the market for more than 120 days",
     "note": "Lexically unreachable: the ontology's widest surface says “had been "
             "listed” and the letter says “were listed”."},
    {"case_id": "population-our-homes-in-inventory-q1-2023",
     "wording": "59% of our homes in inventory had been listed on the market for more than "
                "120 days",
     "note": "Lexically unreachable: “in inventory” is interposed between "
             "“homes” and “had been listed”. §8a.11 measured this "
             "sentence at rank 0 alone and rank 2 inside its 2,046-character passage."},
)

# One request in thirteen produced an answer that could not be used, and a request whose
# answer never conformed has **no row in the store** — `ReplayingGenerationProvider` only
# records what `generate` returned. So the report could not be rebuilt offline at all until
# the failure was declared here, which is the honest place for it: the store is a record of
# answers, and this was not an answer.
#
# `UnusableAnswer` is replayed by `_ReplayWithRecordedFailures` raising the same
# `ProviderResponseError` the live adapter raised, so the lane takes the same
# `MODEL_ANSWER_UNUSABLE` path it took live and the case scores the same way. Nothing here
# repairs the request or shortens it: STAGE_11 forbids moving a number by changing the lane,
# and an output budget is part of the lane.
#
# **The cause, corrected 2026-08-02 after review, and the correction is the finding.** The
# report used to say the prompt had outgrown the worst case
# `narrative_lane.DEFAULT_MAX_OUTPUT_TOKENS` was sized against, so that prompt plus budget
# could not fit. **Prompt length is not the discriminator.** Tokenizing all 13 benchmark
# prompts on the running server: this one is 4,199 tokens and
# `letter-prose-run-together-kpi-row-q1-2025` is **larger** at 4,706, and that one finished
# 496 tokens short of the slot. What separates them is completion length — this passage is a
# table passage with 17 metric concepts in scope and a dozen labelled rows, and the model tried
# to emit a claim for each.
#
# The output budget is now prompt-aware (`narrative_lane.output_budget`), so a request can no
# longer ask for more room than the slot has left; that makes the failure honest rather than
# making it go away, and this table records what was actually observed each time. Re-issued
# against the running server on 2026-08-02 under the new budget: prompt 4,311 tokens (server
# count, including the chat template), completion 3,601 — the budget exactly — total 7,912 of
# 8,192, `finish_reason: length`. The slot was **not** the limit this time. The completion is
# simply longer than any budget this slot can give it.
UNUSABLE_ANSWERS: tuple[dict[str, str], ...] = (
    {"request_sha256":
        "cc28533dafcba552e3e5402ade11c50a440fb29fbcf2a2ea671eabffeac40b1e",
     "case_id": "negative-ambiguous-alias-bare-gross-profit",
     "scopes": "lexical and hybrid — one request, because the two scopes agree on this case",
     "finish_reason": "length",
     "detail": "assistant content is not JSON: Expecting value: line 291 column 19 "
               "(char 9749)",
     "cause": "the completion ran to the whole 3,601-token budget and stopped mid-object. "
              "Prompt plus budget is 7,912 of the 8,192-token slot, so the slot is no longer "
              "the constraint and prompt length is not the discriminator: "
              "`letter-prose-run-together-kpi-row-q1-2025` has a *larger* prompt (4,806 "
              "tokens against this one's 4,299) and conforms. It is a table passage with 17 "
              "metric concepts in scope and a dozen labelled rows, and the model tries to emit "
              "a claim for each"},
)

# What `narrative-build` observed about answer conformance. Declared rather than derived for
# the reason above, and held to the store's own size by the report tests in the one direction
# that can be checked: a conformant answer is a row.
#
# **"Conformant after a retry" is not a state this pipeline can reach**, and STAGE_11 §2 asks
# for it. `LocalOpenAICompatibleGenerationProvider._post_with_retries` retries transport
# failures and 5xx only; an unparseable or schema-violating answer raises on the first
# response and is never retried, because repeating an identical request at temperature 0
# cannot change it. The column is reported as structurally zero rather than measured as zero.
#
# `provider_calls` and `distinct_requests` differ for two reasons worth stating: the two
# scopes build the same prompt on 13 of the 14 cases, and two cases — `population-our-homes-
# q4-2021` and `letter-prose-multiple-metrics-q4-2021` — annotate **the same passage**, so
# they share one request and one answer.
STRUCTURED_OUTPUT: dict[str, Any] = {
    "provider_calls": 26,
    "distinct_requests": 13,
    "requests_issued": 13,
    "conformant_first_attempt": 12,
    "conformant_after_retry": 0,
    "never_conformant": 1,
    "retries_possible": False,
}


# -- the report model ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NarrativeCase:
    """One reviewed prose case, as loaded. No lane involvement yet."""

    case_id: str
    category: str
    lane: str
    source_file: str
    document_id: str
    passage_id: str
    notes: str
    gold: tuple[GoldObservation, ...]
    expected_abstentions: tuple[ExpectedAbstention, ...]


@dataclass(frozen=True)
class ScopeView:
    """Every case scored under one scope."""

    name: str
    cases: list[CaseScore]
    totals: dict[str, Any]

    def case(self, case_id: str) -> CaseScore | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


@dataclass(frozen=True)
class ScopeDifference:
    """One case where the two scopes did not offer the lane the same concepts.

    `reached_only_by` is the finding STAGE_11 §5 calls the strongest evidence either way: a
    concept only one scope supplies that produces a *correct* claim is the strongest argument
    for that scope, and one that produces a wrong claim is the strongest argument against.
    """

    case_id: str
    only_lexical: tuple[str, ...]
    only_hybrid: tuple[str, ...]
    lexical_claims: tuple[str, ...]
    hybrid_claims: tuple[str, ...]
    claims_only_lexical: tuple[str, ...]
    claims_only_hybrid: tuple[str, ...]
    gold_keys: tuple[str, ...]


@dataclass
class NarrativeLaneReport:
    benchmark_version: str
    ontology_definition_hash: str
    normalization_corpus: dict[str, Any]
    implementation_commit: str
    lane: dict[str, str]
    model: dict[str, Any]
    answer_store: dict[str, Any]
    scopes: dict[str, Any]
    views: dict[str, ScopeView]
    comparison: dict[str, Any]
    probes: list[dict[str, Any]]
    # `case_id -> {category, lane, source_file, notes}`. Carried on the report rather than
    # re-read from YAML at render time: a renderer that reloads the case files is a renderer
    # that can describe a case the run never scored.
    case_index: dict[str, dict[str, str]]
    structured_output: dict[str, Any] = field(
        default_factory=lambda: dict(STRUCTURED_OUTPUT))

    def case(self, case_id: str, scope: str = "lexical") -> CaseScore | None:
        return self.views[scope].case(case_id)


# -- loading -------------------------------------------------------------------------------------


def load_narrative_cases(cases_dir: Path = CASES_DIR) -> list[NarrativeCase]:
    """Every case this lane is answerable for, ordered by case id."""
    cases: list[NarrativeCase] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            if case.get("lane") not in NARRATIVE_LANES or not case.get("passage_id"):
                continue
            cases.append(NarrativeCase(
                case_id=case["case_id"],
                category=case.get("category") or "",
                lane=case["lane"],
                source_file=path.name,
                document_id=case["document_id"],
                passage_id=case["passage_id"],
                notes=" ".join(str(case.get("notes") or "").split()),
                gold=tuple(_gold(g) for g in (case.get("gold_claims") or [])),
                expected_abstentions=tuple(
                    _expected(a) for a in (case.get("abstentions") or [])),
            ))
    return sorted(cases, key=lambda c: c.case_id)


def _gold(raw: dict) -> GoldObservation:
    population = raw.get("population") or {}
    return GoldObservation(
        metric_id=raw["metric_id"],
        period_key=_gold_period_key(raw),
        value=raw["value"],
        # The case file omits `scale_applied` when no scale was applied; `evaluation.py` reads
        # that omission as "units", so the report states it rather than leaving a null a reader
        # would have to interpret.
        unit=raw["unit"],
        scale_applied=raw.get("scale_applied") or "units",
        currency=raw.get("currency"),
        subject_entity_id=raw["subject_entity_id"],
        population_definition_raw=population.get("definition_raw"),
        # §7.3, as the reviewers declared it. Read since 2026-08-02: six gold observations
        # carry `pct_120_days_denominator` and nothing had ever compared them to anything.
        ambiguity_codes=tuple(sorted(raw.get("ambiguity_codes") or ())),
        note=" ".join(str(raw.get("note") or "").split()) or None,
    )


def _expected(raw: dict) -> ExpectedAbstention:
    candidates = tuple(raw.get("candidates") or ())
    metrics = tuple(raw.get("metrics") or ())
    return ExpectedAbstention(
        reason=str(raw.get("reason") or ""),
        detail=" ".join(str(raw.get("detail") or "").split()),
        concept_ids=tuple(sorted(set(candidates) | set(metrics))),
        ambiguity_candidates=tuple(sorted(candidates)),
        required_lane=raw.get("required_lane"),
    )


# -- composition ------------------------------------------------------------------------------


def build_scopes(ontology, *, embedding_model: str, dimensions: int):
    """Both scopes, from the committed vector caches, with no server involved.

    The hybrid half reuses `hybrid_scope_runner.build_scopes` rather than rebuilding the index:
    step 9's report and this one must rank against the same vectors, or "hybrid" would mean two
    different things in two committed artifacts.
    """
    lexical = LexicalOntologyCandidateScope(ontology, AliasIndex.from_ontology(ontology))
    hybrid, _, _ = hybrid_scope_runner.build_scopes(
        ontology, provider=None, model_id=embedding_model, dimensions=dimensions)
    return {"lexical": lexical, "hybrid": hybrid}


def build_lane(ontology, scope, provider, *, context_tokens: int = DEFAULT_CONTEXT_TOKENS):
    """The one composition root for this report. The scope is the only thing that varies.

    STAGE_10 §3 put the injection point here on purpose: the lexical-versus-hybrid comparison
    is only meaningful if no line of lane code differs between the two runs.

    `context_tokens` comes from `config/extraction.yaml` through `build_report`, because the
    output budget is now derived from it and a report that sized its requests against a
    different slot than the server offers would be measuring something else.
    """
    return OntologyGuidedNarrativeClaimLane(
        ontology, scope, provider, deferred_metric_ids(ontology),
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, context_tokens=context_tokens)


class _ReplayWithRecordedFailures:
    """Replay-only, plus the answers that never conformed and therefore have no row.

    A decorator over `ReplayingGenerationProvider` rather than a change to it. The store is a
    record of *answers*; a truncated response was not an answer, and teaching a durable record
    to hold non-answers is a change to the lane's contract that this stage is not allowed to
    make and does not need. What it needs is for a replay to take the same path the live run
    took, which means raising the same `ProviderResponseError` the adapter raised — the lane
    then records the same `MODEL_ANSWER_UNUSABLE` with the same detail.

    Declared failures are keyed by request digest, so a prompt change makes them stale
    visibly: the digest stops matching, the store has no row either, and the replay raises
    `MissingAnswerError` naming the request.

    **A declared failure and a stored answer are mutually exclusive, and that is enforced
    here** *(review 2026-08-02)*. The whole justification above is that an unusable answer has
    no row; nothing checked it, and `generate` consults the declared failures first, so one
    entry pointing at a request the store *does* answer silently deletes that answer from the
    run. Mutating this file that way moved metric identity from 0.526/0.579 to 0.316/0.368 and
    made four `DUPLICATE_OBSERVATION_CONFLICT` errors disappear, with the suite green. The
    check is in the constructor rather than in `generate` because a stale declaration should
    fail the report, not one request in it.
    """

    def __init__(self, inner: ReplayingGenerationProvider,
                 failures: tuple[dict[str, str], ...]) -> None:
        self._inner = inner
        self._failures = {entry["request_sha256"]: entry for entry in failures}
        answered = {digest for digest in self._failures if inner.store.get(digest) is not None}
        if answered:
            raise ValueError(
                "a request declared in UNUSABLE_ANSWERS also has a stored answer, so the "
                "declaration would override a real one: "
                + ", ".join(f"{digest} ({self._failures[digest]['case_id']})"
                            for digest in sorted(answered)))

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def store(self):
        return self._inner.store

    @property
    def recorded_failures(self) -> dict[str, dict[str, str]]:
        return dict(self._failures)

    def generate(self, *, prompt: str, schema: dict, max_tokens: int = 1024,
                 temperature: float = 0.0):
        identity = request_identity(
            prompt=prompt, schema=schema, model_id=self._inner.model_id,
            temperature=temperature, max_tokens=max_tokens)
        failure = self._failures.get(identity)
        if failure is not None:
            raise ProviderResponseError(failure["detail"])
        return self._inner.generate(
            prompt=prompt, schema=schema, max_tokens=max_tokens, temperature=temperature)


def replay_provider(store_path: Path = ANSWER_STORE):
    """A provider that can only replay. `inner=None`, so a miss raises and names the request.

    The model identity is read from the store's own rows rather than from
    `config/extraction.yaml`. That is what makes the committed file *sufficient*: a report that
    needed a second file to say which model its answers came from could not be regenerated from
    the answers alone.
    """
    store = AnswerStore(store_path)
    if not len(store):
        raise FileNotFoundError(
            f"the answer store {store_path} is empty or absent; run "
            f"`python -m benchmarks.extraction.v1 narrative-build` with the generation server "
            f"up before reporting")
    return _ReplayWithRecordedFailures(
        ReplayingGenerationProvider(store, None, prompt_version=PROMPT_VERSION),
        UNUSABLE_ANSWERS)


# -- running -------------------------------------------------------------------------------------


def run_narrative_lane(
    cases: list[NarrativeCase],
    *,
    passages: dict[str, dict],
    ontology,
    provider,
    scopes: dict[str, Any],
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    catalog_root: Path | None = None,
    implementation_commit: str | None = None,
) -> NarrativeLaneReport:
    """Drive the lane over every case under every scope and score it.

    One lane instance per scope, one `extract_passage` per case. `extract_passage` rather than
    the protocol's `extract` because a `CandidatePassage` carries no filing form, date or
    heading path, and a prompt that says "unknown" for all three is a worse-informed prompt than
    the catalog can support. Both go through the same code (`narrative_lane.extract`).
    """
    views: dict[str, ScopeView] = {}
    for scope_name in SCOPES:
        scope = scopes[scope_name]
        lane = build_lane(ontology, scope, provider, context_tokens=context_tokens)
        scored = [
            _score_case(case, lane=lane, scope=scope, ontology=ontology, passages=passages,
                        scope_name=scope_name)
            for case in cases
        ]
        views[scope_name] = ScopeView(
            name=scope_name, cases=scored, totals=totals(scored, RATIO_DIGITS))

    return NarrativeLaneReport(
        benchmark_version=BENCHMARK_VERSION,
        ontology_definition_hash=ontology.definition_hash,
        normalization_corpus=runner.corpus_identity(passages, catalog_root),
        implementation_commit=implementation_commit or runner.head_commit(),
        lane={"name": LANE_NAME, "version": LANE_VERSION, "prompt_version": PROMPT_VERSION},
        model={
            "identity": str(provider.model_id),
            "temperature": TEMPERATURE,
            # A ceiling, not the budget. The budget is `narrative_lane.output_budget`, which
            # subtracts an estimate of the prompt from the slot, so prompt plus budget cannot
            # exceed `context_tokens`. Both are part of the request identity, and both are
            # configuration rather than measurement, which is why they may appear here.
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "context_tokens": context_tokens,
            "output_budget": "min(max_output_tokens, context_tokens - estimated prompt tokens)",
            "replay_only": True,
        },
        answer_store=_answer_store_identity(provider),
        scopes={
            "lexical": {"name": "lexical"},
            "hybrid": {"name": "hybrid", "top_k": DEFAULT_TOP_K,
                       "min_similarity": DEFAULT_MIN_SIMILARITY},
        },
        views=views,
        comparison=_comparison(views),
        probes=_probes(views),
        case_index={
            case.case_id: {"category": case.category, "lane": case.lane,
                           "source_file": case.source_file, "notes": case.notes}
            for case in cases
        },
    )


def _score_case(
    case: NarrativeCase, *, lane, scope, ontology, passages, scope_name
) -> CaseScore:
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

    return score_case(
        case_id=case.case_id,
        scope=scope_name,
        passage_id=case.passage_id,
        gold=case.gold,
        expected_abstentions=case.expected_abstentions,
        claims=list(extraction.claims),
        issues=list(extraction.issues),
        scope_concept_ids=tuple(sorted(_scope_metric_ids(scope, text, ontology))),
        request_digest=extraction.request_sha256,
        validation=_validation_census(
            list(extraction.claims), ontology=ontology, passages=passages),
        # §2's evidence dimension is two conditions, not one: the anchor resolves in the
        # catalog *and* the quoted span is verbatim inside that passage. The table lane's
        # `compare` takes the verdict as an argument precisely so a lane that quotes can be
        # held to the stronger condition through the same function.
        resolves_evidence=lambda claim: (
            claim.passage_id in passages
            and claim.raw_text in passages[claim.passage_id]["text"]),
        reporting_keys=reporting_period_keys(text),
        sentence_period_keys=_period_keys_of,
        # §7.3's codes as `core.assembly` attaches them, passed as a function for the same
        # reason `resolves_evidence` is: the scorer holds the benchmark's vocabulary, not the
        # ontology's.
        ambiguity_codes_for=lambda metric_id: declared_ambiguity_codes(ontology, metric_id),
    )


def _scope_metric_ids(scope, text: str, ontology) -> set[str]:
    """The metric concepts the injected scope offered for this passage.

    Asked of the same scope object the lane was constructed with, and narrowed the same way
    `narrative_lane._candidate_metrics` narrows it — entity concepts are the subject of an
    observation, not something one can be made about — so the list the report compares is the
    list the prompt carried.
    """
    found = set()
    for concept_id in scope.candidates_for(text):
        concept = ontology.registry.find(concept_id)
        if concept is None:
            continue
        if str(getattr(concept.category, "value", concept.category)) == "metric_definition":
            found.add(concept_id)
    return found


def _validation_census(claims, *, ontology, passages) -> dict[str, tuple[str, ...]]:
    """What happens to these claims after the lane: §7's policies, then the vocabulary's verdict.

    `unpreferred_source_lane` is the warning STAGE_11 §2 asks for and the one that was invisible
    until step 10 relayed it: a metric whose ontology entry prefers `normalized_table`, read out
    of prose. It is not an error — a preference is not a prohibition — and how often it happens
    is a finding about lane routing.

    Errors and assembly rejections are counted beside it because V1 §11 criterion 1 is "every
    emitted claim passes `ontology.validate_claims` with zero errors". A report that scored the
    lane without driving its claims this far could not say whether that criterion holds, and
    step 10's live gate only measured it on three passages that are not in the benchmark.
    """
    if not claims:
        return {"warnings": (), "errors": (), "assembly_rejections": ()}
    assembly = assemble(list(claims), ontology=ontology, passage_rows=passages)
    findings = validate(assembly.claims, ontology=ontology, passages=_PassageSource(passages))
    return {
        "warnings": tuple(sorted(
            f"{f.claim_id or EM_DASH}: {f.detail}"
            for f in findings.warnings if f.code == ONTOLOGY_WARNING)),
        "errors": tuple(sorted(f"{f.code}: {f.detail}" for f in findings.errors)),
        "assembly_rejections": tuple(sorted(
            f"{r.metric_id}: {r.reason}: {r.detail}" for r in assembly.rejected)),
    }


class _PassageSource:
    """A `PassageSource` over the loaded catalog, so evidence resolution is not simulated."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows

    def text_of(self, passage_id):
        row = self._rows.get(passage_id)
        return row["text"] if row else None

    def exists(self, passage_id):
        return passage_id in self._rows

    def document_of(self, passage_id):
        row = self._rows.get(passage_id)
        return row["document_id"] if row else None


def _period_keys_of(text: str) -> frozenset[str]:
    """Every period key a piece of text states in a form `core.periods` can resolve."""
    keys = set()
    for phrase in period_phrases(text or ""):
        for kind in (DURATION, INSTANT):
            resolved = resolve_period_phrase(phrase, period_type=kind)
            if resolved is not None:
                keys.add(resolved.key)
    return frozenset(keys)


def _answer_store_identity(provider) -> dict[str, Any]:
    """Size, digest and row count of the file the report was replayed from.

    STAGE_11 §3 asks for the size to be recorded. The digest is here for the same reason every
    other artifact in this repository carries one: a report that cannot say which bytes it read
    is not evidence that those bytes produce it.
    """
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


# -- the lexical-versus-hybrid comparison ---------------------------------------------------------


def _comparison(views: dict[str, ScopeView]) -> dict[str, Any]:
    """Claim by claim, what each scope reached that the other did not.

    Two questions, kept apart because they have different answers. *Scope* difference is which
    concepts the lane was offered; *claim* difference is what it emitted. A concept only one
    scope supplies and that produces no claim moved nothing, and a report that showed only the
    scope difference would present it as if it had.
    """
    lexical, hybrid = views["lexical"], views["hybrid"]
    differences: list[ScopeDifference] = []
    for case in lexical.cases:
        other = hybrid.case(case.case_id)
        if other is None:
            continue
        lexical_scope, hybrid_scope = set(case.scope_concept_ids), set(other.scope_concept_ids)
        lexical_claims = {f"{c.metric_id}@{c.period.key}" for c in case.claims}
        hybrid_claims = {f"{c.metric_id}@{c.period.key}" for c in other.claims}
        if lexical_scope == hybrid_scope and lexical_claims == hybrid_claims:
            continue
        differences.append(ScopeDifference(
            case_id=case.case_id,
            only_lexical=tuple(sorted(lexical_scope - hybrid_scope)),
            only_hybrid=tuple(sorted(hybrid_scope - lexical_scope)),
            lexical_claims=tuple(sorted(lexical_claims)),
            hybrid_claims=tuple(sorted(hybrid_claims)),
            claims_only_lexical=tuple(sorted(lexical_claims - hybrid_claims)),
            claims_only_hybrid=tuple(sorted(hybrid_claims - lexical_claims)),
            gold_keys=tuple(sorted(f"{g.metric_id}@{g.period_key}" for g in case.gold)),
        ))

    # **Prompt identity, not scope identity.** This inferred "the two runs are the same run"
    # from the metric-definition concepts alone, which is a weaker statement than the one the
    # prose beside it makes: the prompt also carries the passage, its metadata, the ambiguity
    # notes and the schema. `request_identity` is the digest the answer store is keyed on, so
    # it settles the question exactly *(strengthened after review 2026-08-02; the strong claim
    # holds — 13 of 14 digests are identical and the odd one out is
    # `letter-prose-inventory-and-120d-q2-2022`)*.
    identical_prompt_cases = sum(
        1 for case in lexical.cases
        if case.request_digest == hybrid.case(case.case_id).request_digest)
    # Cases where neither scope reached a provider, counted separately: they have the same
    # (absent) digest and so are "the same run" trivially, and saying so keeps the identity
    # count from reading as evidence about prompts that were never built.
    cases_without_a_request = sum(
        1 for case in lexical.cases
        if case.request_digest is None
        and hybrid.case(case.case_id).request_digest is None)
    delta = {
        name: round(hybrid.totals["scores"][name] - lexical.totals["scores"][name],
                    RATIO_DIGITS)
        for name in lexical.totals["scores"]
    }
    higher = sorted(name for name, value in delta.items() if value > 0)
    lower = sorted(name for name, value in delta.items() if value < 0)
    added = _added_claims(lexical, hybrid, differences)
    regressions = _per_claim_regressions(lexical, hybrid)
    return {
        "cases": len(lexical.cases),
        "recommendation": _recommendation(
            differences=differences, added=added, regressions=regressions),
        # Named for what they are. A ratio can rise because its *denominator* changed — adding
        # one correct match took `value_accuracy` from 9/10 to 10/11, and the old key called
        # that "improved by hybrid" although hybrid fixed no value error *(review 2026-08-02)*.
        # The per-claim lists below are the ones that say whether a dimension moved.
        "score_ratios_higher_under_hybrid": higher,
        "score_ratios_lower_under_hybrid": lower,
        "gold_claims_only_hybrid_reached": len(
            [c for c in added if c["scope"] == "hybrid" and c["is_gold"]]),
        "gold_claims_only_lexical_reached": len(
            [c for c in added if c["scope"] == "lexical" and c["is_gold"]]),
        "claims_added_by_one_scope": added,
        "per_claim_regressions": regressions,
        # The number that decides how much the rest of this section can possibly say. Two
        # scopes that build the same prompt get the same answer — literally the same stored
        # row, since the request digest covers the prompt.
        "cases_with_identical_scope": identical_prompt_cases,
        "cases_without_a_request": cases_without_a_request,
        "cases_with_a_difference": len(differences),
        "score_delta_hybrid_minus_lexical": dict(sorted(delta.items())),
        "differences": [
            {
                "case_id": d.case_id,
                "concepts_only_lexical": list(d.only_lexical),
                "concepts_only_hybrid": list(d.only_hybrid),
                "claims_only_lexical": list(d.claims_only_lexical),
                "claims_only_hybrid": list(d.claims_only_hybrid),
                "gold": list(d.gold_keys),
                "claims_only_hybrid_that_are_gold": [
                    key for key in d.claims_only_hybrid if key in d.gold_keys],
                "claims_only_lexical_that_are_gold": [
                    key for key in d.claims_only_lexical if key in d.gold_keys],
            }
            for d in differences
        ],
    }


def _added_claims(lexical: ScopeView, hybrid: ScopeView, differences) -> list[dict[str, Any]]:
    """Every claim one scope emitted and the other did not, checked dimension by dimension.

    **This is what the recommendation now rests on, and the aggregate ratios are not.** A
    ratio moves when its denominator moves: adding one correct match took `value_accuracy`
    from 9/10 to 10/11, which the old comparison listed as a score "improved by hybrid"
    although hybrid corrected no value *(review 2026-08-02)*. Whether the added claim is
    correct is a per-claim question and is answered per claim, off the `MatchRecord` the
    scorer built — never re-derived here.
    """
    views = {"lexical": lexical, "hybrid": hybrid}
    added: list[dict[str, Any]] = []
    for difference in differences:
        for scope_name, keys in (("lexical", difference.claims_only_lexical),
                                 ("hybrid", difference.claims_only_hybrid)):
            case = views[scope_name].case(difference.case_id)
            matched = {f"{m.metric_id}@{m.period_key}": m for m in case.matched}
            for key in keys:
                match = matched.get(key)
                added.append({
                    "scope": scope_name,
                    "case_id": difference.case_id,
                    "claim": key,
                    "is_gold": key in difference.gold_keys,
                    "clean_on_every_scored_dimension": bool(match and match.all_ok),
                    "failed_dimensions": list(match.failed_dimensions) if match else [],
                })
    return added


def _per_claim_regressions(lexical: ScopeView, hybrid: ScopeView) -> list[dict[str, Any]]:
    """Gold observations one scope got right on every dimension and the other did not.

    The only evidence that can say a dimension *fell*, as opposed to a ratio moving. Symmetric
    on purpose: hybrid losing a clean match matters exactly as much as lexical losing one.
    """
    regressions: list[dict[str, Any]] = []
    for case in lexical.cases:
        other = hybrid.case(case.case_id)
        clean = {name: {f"{m.metric_id}@{m.period_key}" for m in view.case(
            case.case_id).matched if m.all_ok}
            for name, view in (("lexical", lexical), ("hybrid", hybrid))}
        for scope_name, lost_by in (("hybrid", clean["lexical"] - clean["hybrid"]),
                                    ("lexical", clean["hybrid"] - clean["lexical"])):
            for key in sorted(lost_by):
                regressions.append({
                    "case_id": case.case_id, "claim": key, "no_longer_clean_under": scope_name})
        assert other is not None
    return regressions


def _recommendation(*, differences, added, regressions) -> dict[str, Any]:
    """The step 13 recommendation, derived from per-claim verdicts rather than from ratios.

    STAGE_11 §7 asks for a recommendation *including* "the benchmark still does not distinguish
    them" if that is the result. Every branch below is decided by counting claims whose
    `MatchRecord` says what happened to them, so the verdict cannot say "correct" about a claim
    the scorer marked wrong — which is what it did until 2026-08-02, when the only dimensions
    scored were six and the two the added claim actually failed were not among them.
    """
    gold_hybrid = [c for c in added if c["scope"] == "hybrid" and c["is_gold"]]
    gold_lexical = [c for c in added if c["scope"] == "lexical" and c["is_gold"]]
    clean_hybrid = [c for c in gold_hybrid if c["clean_on_every_scored_dimension"]]
    clean_lexical = [c for c in gold_lexical if c["clean_on_every_scored_dimension"]]
    dirty_hybrid = [c for c in gold_hybrid if not c["clean_on_every_scored_dimension"]]

    if not differences:
        verdict = "indistinguishable"
        summary = ("the two scopes offered the lane the same concepts on every case and "
                   "produced the same claims; extraction quality does not distinguish them "
                   "either, and the default should be settled on cost or on reachability")
    elif regressions:
        verdict = "mixed"
        summary = (f"{len(regressions)} gold observation(s) are clean under one scope and not "
                   "the other; the benchmark distinguishes the scopes and does not favour one")
    elif clean_hybrid and not clean_lexical:
        verdict = "hybrid, on narrow evidence"
        summary = (
            f"the scopes differ on {len(differences)} case; on it, the concept only hybrid "
            f"supplies produced {len(clean_hybrid)} gold claim(s) clean on every scored "
            "dimension. That is the strongest evidence STAGE_11 §5 says hybrid can offer, and "
            "it rests on one concept in one passage")
    elif dirty_hybrid and not clean_lexical:
        verdict = "hybrid reaches more and is not clean on what it reaches"
        summary = (
            f"the scopes differ on {len(differences)} case; on it, the concept only hybrid "
            f"supplies produced {len(dirty_hybrid)} gold claim(s), none of them clean on every "
            "scored dimension — "
            + "; ".join(f"{c['claim']} fails {', '.join(c['failed_dimensions'])}"
                        for c in dirty_hybrid)
            + ". Hybrid buys reach, and this benchmark does not show it buying correctness")
    else:
        verdict = "lexical"
        summary = ("the concepts hybrid adds produced no gold claim the lexical scope did "
                   "not already reach, so the addition buys nothing measurable here")
    return {
        "verdict": verdict,
        "summary": summary,
        "evidence_width": f"{len(differences)} case(s) where the scopes differ",
        "gold_claims_added_clean": len(clean_hybrid) + len(clean_lexical),
        "gold_claims_added_not_clean": len(dirty_hybrid),
        "per_claim_regressions": len(regressions),
    }


def _probes(views: dict[str, ScopeView]) -> list[dict[str, Any]]:
    """The two `pct_homes_on_market_gt_120_days` paraphrases, carried forward from step 9.

    Step 9 could say whether each scope *reached* the concept. This is the first stage that can
    say whether reaching it produced a correct claim, which is the whole question the probes
    were kept for.
    """
    probes: list[dict[str, Any]] = []
    for probe in PARAPHRASE_PROBES:
        entry: dict[str, Any] = {
            "case_id": probe["case_id"],
            "concept_id": PARAPHRASE_PROBE_CONCEPT,
            "wording": probe["wording"],
            "note": probe["note"],
            "scopes": {},
        }
        for scope_name in SCOPES:
            case = views[scope_name].case(probe["case_id"])
            if case is None:
                continue
            gold = [g for g in case.gold if g.metric_id == PARAPHRASE_PROBE_CONCEPT]
            matched = [m for m in case.matched if m.metric_id == PARAPHRASE_PROBE_CONCEPT]
            entry["scopes"][scope_name] = {
                "in_scope": PARAPHRASE_PROBE_CONCEPT in case.scope_concept_ids,
                "gold_observations": len(gold),
                "claims_emitted": sum(
                    1 for c in case.claims if c.metric_id == PARAPHRASE_PROBE_CONCEPT),
                "matched_gold": len(matched),
                "correct_on_every_dimension": all(m.all_ok for m in matched) and bool(matched),
                # Named, so "correct on every dimension" cannot be read as "correct" about a
                # dimension the report was not scoring. Every one of these pairs failed
                # `population` once §7.1 became a scored dimension.
                "failed_dimensions": sorted(
                    {d for m in matched for d in m.failed_dimensions}),
                "failures": [
                    {"category": f.category, "detail": f.detail}
                    for f in case.failures if f.metric_id == PARAPHRASE_PROBE_CONCEPT],
            }
        probes.append(entry)
    return probes


# -- building ---------------------------------------------------------------------------------


def build_report(
    *,
    catalog_root: Path | None = None,
    implementation_commit: str | None = None,
    store_path: Path = ANSWER_STORE,
    provider=None,
) -> NarrativeLaneReport:
    """The whole thing, from disk: cases, corpus, ontology, both scopes, the stored answers.

    `provider` defaults to replay-only. Passing one is what `narrative-build` does, and it is
    the only path in this module that can reach a server.
    """
    from extraction.providers import EmbeddingConfig, ProviderConfig

    ontology = load_ontology()
    config = hybrid_scope_runner._extraction_config()
    embedding = EmbeddingConfig.from_config(config)
    return run_narrative_lane(
        load_narrative_cases(),
        passages=runner.load_passages(catalog_root),
        ontology=ontology,
        provider=provider if provider is not None else replay_provider(store_path),
        scopes=build_scopes(
            ontology, embedding_model=embedding.model, dimensions=embedding.dimensions),
        # The slot the requests are sized against, read from configuration rather than
        # defaulted, so a report and the server it was generated from cannot disagree about it.
        context_tokens=ProviderConfig.from_config(config).context_tokens,
        catalog_root=catalog_root,
        implementation_commit=implementation_commit,
    )


# -- rendering: JSON -----------------------------------------------------------------------------


def render_json(report: NarrativeLaneReport) -> str:
    payload = {
        "benchmark_version": report.benchmark_version,
        "ontology_definition_hash": report.ontology_definition_hash,
        "normalization_corpus": report.normalization_corpus,
        "implementation_commit": report.implementation_commit,
        "lane": report.lane,
        "model": report.model,
        "answer_store": report.answer_store,
        "scopes": report.scopes,
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
        "probes": report.probes,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _view_json(view: ScopeView) -> dict[str, Any]:
    return {
        "scope": view.name,
        "totals": view.totals,
        "cases": [_case_json(case) for case in view.cases],
    }


def _case_json(case: CaseScore) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "passage_id": case.passage_id,
        "counts": case.counts,
        "scores": case.scores,
        "scope_concept_ids": list(case.scope_concept_ids),
        "gold": [
            {"metric_id": g.metric_id, "period_key": g.period_key, "value": g.value,
             "unit": g.unit, "scale_applied": g.scale_applied, "currency": g.currency,
             "subject_entity_id": g.subject_entity_id,
             "population_definition_raw": g.population_definition_raw}
            for g in case.gold
        ],
        "emitted": [
            {"metric_id": c.metric_id, "period_key": c.period.key, "value": c.value,
             "unit": c.unit, "currency": c.currency,
             "scale": c.scale.scale if c.scale else "units",
             "scale_source": c.scale.location if c.scale else "metric_default",
             "subject_entity_id": c.subject_entity_id,
             "population_definition_raw": c.population_definition_raw,
             # What the *lane* carried. §7.3 attaches the ontology's codes at assembly, so this
             # is empty on every claim; the `matched` rows below carry both readings.
             "lane_claim_ambiguity_codes": list(c.ambiguity_codes),
             "raw_text": c.raw_text,
             # Read, never asserted. This printed `true` on every emitted claim, including the
             # eighteen unmatched ones where evidence resolution had never been computed
             # *(review 2026-08-02)*.
             "evidence": {"passage_id": c.passage_id, "resolves": resolves}}
            for c, resolves in zip(case.claims, case.claim_evidence_resolves)
        ],
        "duplicate_claim_keys": list(case.duplicate_claim_keys),
        "matched": [
            {"metric_id": m.metric_id, "period_key": m.period_key,
             "expected": {"value": m.expected_value, "unit": m.expected_unit,
                          "scale": m.expected_scale,
                          "subject_entity_id": m.expected_subject_entity_id,
                          "population_definition_raw": m.expected_population,
                          "ambiguity_codes": list(m.expected_ambiguity_codes)},
             "emitted": {"value": m.emitted_value, "unit": m.emitted_unit,
                         "scale": m.emitted_scale,
                         "subject_entity_id": m.emitted_subject_entity_id,
                         "population_definition_raw": m.emitted_population,
                         "ambiguity_codes": list(m.emitted_ambiguity_codes),
                         "lane_claim_ambiguity_codes": list(m.lane_claim_ambiguity_codes)},
             "population_contained_in_expected": m.population_contained_in_expected,
             "dimensions": {f"{d}_ok": getattr(m, f"{d}_ok")
                            for d in SCORED_MATCH_DIMENSIONS},
             "dimensions_applicable": {d: m.applies(d) for d in SCORED_MATCH_DIMENSIONS}}
            for m in case.matched
        ],
        "missed": [{"metric_id": m, "period_key": p} for m, p in case.missed],
        "unrequired_emitted": [{"metric_id": m, "period_key": p} for m, p in case.unrequired],
        "expected_abstentions": [
            {"reason": v.reason, "concept_ids": list(v.concept_ids),
             "ambiguity_candidates": list(v.ambiguity_candidates),
             "claimed_anyway": list(v.claimed_concept_ids),
             "silence_kept": v.silence_kept, "any_issue_recorded": v.any_issue_recorded,
             "answer_usable": v.answer_usable, "code_recorded": v.code_recorded,
             "honoured": v.honoured}
            for v in case.abstentions
        ],
        "failures": [
            {"category": f.category, "source": f.source, "metric_id": f.metric_id,
             "period_key": f.period_key, "detail": f.detail}
            for f in case.failures
        ],
        "period_attribution": [
            {"metric_id": a.metric_id, "period_key": a.period_key,
             "period_label": a.period_label, "in_evidence": a.in_evidence,
             "distance_from_evidence": a.distance, "char_start": a.char_start,
             "chose_comparative": a.chose_comparative,
             "sentence_states_its_own_period": a.sentence_states_its_own_period,
             "reporting_period_keys": list(a.reporting_period_keys),
             "matched_gold": a.matched_gold}
            for a in case.attributions
        ],
        "issues_by_code": case.issues_by_code,
        "rejections_by_code": case.rejections_by_code,
        "model_abstentions_by_code": case.model_abstentions_by_code,
        "ontology_warnings": list(case.ontology_warnings),
        "ontology_errors": list(case.ontology_errors),
        "assembly_rejections": list(case.assembly_rejections),
    }


# -- rendering: Markdown ---------------------------------------------------------------------------


def render_markdown(report: NarrativeLaneReport) -> str:
    lines: list[str] = [
        "# Narrative lane — benchmark v1 results, both candidate scopes",
        "",
        "Generated by `python -m benchmarks.extraction.v1 narrative-report`, replay-only from "
        f"`{report.answer_store['path']}`. Every number is produced by driving "
        "`OntologyGuidedNarrativeClaimLane` over the reviewed prose cases and scoring it with "
        "`benchmarks/extraction/v1/narrative_evaluation.py`, which takes its six per-match "
        "dimensions from the table lane's own `evaluation.compare`. **If this report disagrees "
        "with the lane, the report is wrong** — STAGE_11 forbids changing the lane, the "
        "prompt, the schema, the scopes, the ontology or the cases to move a number here.",
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
        "STAGE_09 §11.2 settled the rule and STAGE_10 §7 restates it: those numbers move "
        "between two identical requests, and step 10 measured the same letter passage at 1,850 "
        "and 1,182 completion tokens on two runs whose claims were byte-identical. Generation "
        "took roughly 30 s per request on the recorded session; that figure is "
        "session-dependent and is not a measurement this artifact makes.",
        "",
        "## Headline — the scored dimensions under both scopes",
        "",
        "**Twelve rows, where §4.0 names eight, and every one of the eight is here.** Two are "
        "added: §7.1's population wording and §7.3's ambiguity codes, neither of which any "
        "report scored until 2026-08-02 — with the result that seven matched "
        "`pct_homes_on_market_gt_120_days` pairs disagreed with gold about the denominator and "
        "all seven scored clean. Two more come from splitting the dimension §4.0 calls "
        "*ambiguity*, which was measuring whether a required silence was kept for **any** "
        "stated reason, over fifteen expectations of which exactly one names ambiguity "
        "candidates. Denominators are printed because a rate over 1 and a rate over 15 read "
        "identically at three decimal places.",
        "",
        "| Dimension | Denominator | Lexical | Hybrid |",
        "| --- | --- | --- | --- |",
    ]
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    for name, key, denominator in DIMENSIONS:
        lines.append(
            f"| {name} | {denominator} "
            f"({lexical.totals['score_denominators'][key]} lex / "
            f"{hybrid.totals['score_denominators'][key]} hyb) | "
            f"**{lexical.totals['scores'][key]:.3f}** | "
            f"**{hybrid.totals['scores'][key]:.3f}** |")
    lines += [
        f"| matched/emitted *(not precision)* | matched / emitted | "
        f"{lexical.totals['scores']['matched_over_emitted']:.3f} | "
        f"{hybrid.totals['scores']['matched_over_emitted']:.3f} |",
        f"| matched/emitted, distinct passages *(not precision)* | matched / emitted, each "
        f"passage once | "
        f"{lexical.totals['scores']['matched_over_distinct_emitted']:.3f} | "
        f"{hybrid.totals['scores']['matched_over_distinct_emitted']:.3f} |",
        "",
        "`matched/emitted` is reported for run-to-run movement and is **never** an accuracy "
        "and never precision. Each case annotates a deliberate subset of its passage's claims, "
        "so an unmatched emitted claim is **unrequired** — usually a right answer the case "
        "did not list — and never a false positive (§4.0, STAGE_11 §2).",
        "",
        "**Two reviewed cases annotate one passage, so anything counted per claim is counted "
        "twice.** `population-our-homes-q4-2021` and `letter-prose-multiple-metrics-q4-2021` "
        f"both annotate `q42021formxex992sharehol.htm#p10`, so the "
        f"{lexical.totals['cases']} cases cover "
        f"{lexical.totals['distinct_passages']} distinct passages and that passage's claims, "
        f"{lexical.totals['ontology_warnings'] - lexical.totals['ontology_warnings_distinct']} "
        "of the ontology warnings and "
        f"{lexical.totals['ontology_errors'] - lexical.totals['ontology_errors_distinct']} of "
        "the ontology errors enter every total twice. Gold and matched observations are per "
        "case and are unaffected. The distinct row above is the same ratio with the double "
        "counting taken out, and the Counts table below states both forms of every count the "
        "duplication reaches. This was disclosed for the errors only until 2026-08-02.",
        "",
        "**Two of the dimensions are tautologies under this matching rule, in this report "
        "and in the committed table-lane report.** A predicted claim is matched to a gold claim "
        "on `(metric_id, period key)`, so a matched pair agrees about its metric and its period "
        "by construction and `period_accuracy` cannot read anything but 1.000. The real period "
        "measurement is the `period_wrong` row of the failure classification — a gold "
        "observation the lane emitted under a *different* period — together with the "
        "§8a.12 attribution table below. `table_lane_v1.json`'s `period_accuracy: 1.000` "
        "has the same structure and should be read the same way.",
        "",
        "**Where a lane emits two claims under one key, the matcher chooses, and the rule is "
        "now stated.** `evaluation.evaluate_case` indexes claims into a dict, which keeps the "
        "*last* one silently; this report's own matcher keeps the **first in emission order** "
        "and records every key where a choice was made. The rule matters: on "
        "`letter-prose-multiple-metrics-q4-2021` the lane emitted `contribution_profit` at "
        "both $152 million and $525 million under `2021Q4`, and dict ordering had been "
        "discarding the $152 million figure that equals gold. Emission order is used because "
        "it knows nothing about gold — picking the claim that agrees with the case would make "
        "`value_accuracy` unable to fall. Keys where the choice was made: "
        f"**{lexical.totals['duplicate_claim_keys']}** (lexical) / "
        f"**{hybrid.totals['duplicate_claim_keys']}** (hybrid), listed per case below, and "
        "`value_accuracy` must be read beside that number wherever it appears.",
        "",
        "## Counts",
        "",
        "| Measure | Lexical | Hybrid |",
        "| --- | --- | --- |",
    ]
    for label, key in (
            ("cases", "cases"),
            ("distinct passages behind them", "distinct_passages"),
            ("gold observations", "gold_observations"),
            ("emitted observations", "emitted_observations"),
            ("emitted observations, each passage counted once", "emitted_observations_distinct"),
            ("matched observations", "matched_observations"),
            ("missed observations", "missed_observations"),
            ("unrequired emitted (unscored, not errors)", "unrequired_observations"),
            ("keys where two claims collided and the matcher chose", "duplicate_claim_keys"),
            ("matched pairs carrying an ambiguity code the case does not declare",
             "matched_carrying_undeclared_ambiguity_codes"),
            ("expected abstentions", "expected_abstentions"),
            ("honoured expected abstentions", "honoured_abstentions"),
            ("expectations whose own code the lane recorded", "code_agreeing_abstentions"),
            ("expectations that name ambiguity candidates", "ambiguity_expectations"),
            ("claims the lane rejected", "rejected_claims"),
            ("abstentions the model chose", "model_abstentions"),
            ("abstentions the model chose, less lane decisions", "model_chosen_abstentions"),
            ("ontology warnings", "ontology_warnings"),
            ("ontology warnings, each passage counted once", "ontology_warnings_distinct"),
            ("ontology validation errors", "ontology_errors"),
            ("ontology validation errors, each passage counted once", "ontology_errors_distinct"),
            ("claims refused by a §7 policy at assembly", "assembly_rejections"),
            ("classified failures", "failures")):
        lines.append(f"| {label} | {lexical.totals[key]} | {hybrid.totals[key]} |")
    lines += ["", "## Structured-output validity", "", "| | |", "| --- | --- |"]
    for label, key in (("provider calls the lane made", "provider_calls"),
                       ("distinct requests behind them", "distinct_requests"),
                       ("conformant on the first attempt", "conformant_first_attempt"),
                       ("conformant after a retry", "conformant_after_retry"),
                       ("never conformant", "never_conformant")):
        lines.append(f"| {label} | {report.structured_output[key]} |")
    lines += [
        "",
        "**26 provider calls behind 13 distinct requests, for two separate reasons.** The two "
        "scopes offer the lane the same concepts on 13 of the 14 cases, so they build the same "
        "prompt and therefore the same request digest; and two cases — "
        "`population-our-homes-q4-2021` and `letter-prose-multiple-metrics-q4-2021` — annotate "
        "**the same passage**, so they share one request and one answer between them. One case "
        "(`event-executive-change-ceo-2025`) issues no request at all: neither scope offers it "
        "a metric concept, and the lane refuses before reaching a provider.",
        "",
        "**“Conformant after a retry” is structurally zero, not measured as zero, and "
        "STAGE_11 §2 asks for it as if it were a state this pipeline can reach.** "
        "`LocalOpenAICompatibleGenerationProvider._post_with_retries` retries transport failures "
        "and 5xx statuses only; an unparseable or schema-violating answer raises on the first "
        "response and is never retried, because repeating an identical request at temperature 0 "
        "cannot change it. The lane records such an answer as `MODEL_ANSWER_UNUSABLE` and moves "
        "on.",
        "",
        "### Answers that never conformed",
        "",
        "| Case | Finish reason | Detail |",
        "| --- | --- | --- |",
    ]
    for entry in UNUSABLE_ANSWERS:
        lines.append(
            f"| [`{entry['case_id']}`](#{_anchor(entry['case_id'])}) | "
            f"`{entry['finish_reason']}` | {_cell(entry['detail'])} |")
    if not UNUSABLE_ANSWERS:
        lines.append(f"| {EM_DASH} | {EM_DASH} | every request the lane issued produced a "
                     "conformant answer, and every one of them has a row in the store |")
    lines.append("")
    for entry in UNUSABLE_ANSWERS:
        lines += [f"**`{entry['case_id']}`** — {entry['cause']}. It is recorded as declared "
                  "data because a request whose answer never conformed has no row in the "
                  "answer store, so the report could not otherwise be rebuilt offline at all — "
                  "and the case scores as `schema_or_parse_failure`, which is what that "
                  "category is for.", ""]
    lines += [
        "**The output budget is prompt-aware, and the cause statement this section used to "
        "carry was wrong** *(corrected 2026-08-02)*. It said the one truncated answer was "
        "caused by a prompt larger than the worst case the fixed 4,096-token budget had been "
        "sized against. Tokenizing all 13 prompts on the running server shows prompt length is "
        "not the discriminator: `negative-ambiguous-alias-bare-gross-profit` is 4,199 prompt "
        "tokens and `letter-prose-run-together-kpi-row-q1-2025` is **larger** at 4,706 and "
        "finished 496 tokens short of the slot. Completion length was the discriminator. The "
        "budget is now `min(4096, context_tokens − estimated prompt tokens)`, computed from "
        "the prompt's character count against a ratio below every measured one, so a request "
        "that structurally cannot fit is refused with `PROMPT_EXCEEDS_CONTEXT` instead of "
        "being issued to be truncated. That makes the failure honest; it does not make a long "
        "completion short.",
        "",
    ]
    lines += [
        "## Rejection rate by reason, and abstentions the model chose",
        "",
        "The distinction step 10 built into the protocol, scored here for the first time. A "
        "**rejection** is this lane refusing a claim the model proposed; an **abstention** is "
        "the model declining. Both are silences and they are not the same silence.",
        "",
        "| Code | Rejected claims (lex/hyb) | Not a rejected claim (lex/hyb) | "
        "May the model choose it? |",
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
            + ("yes" if code in MODEL_ABSTENTION_REASONS else "no") + " |")
    lines += [
        "",
        f"Totals: **{lexical.totals['rejected_claims']} rejections and "
        f"{lexical.totals['model_abstentions']} model-chosen abstentions** under the lexical "
        f"scope, {hybrid.totals['rejected_claims']} and "
        f"{hybrid.totals['model_abstentions']} under the hybrid one. "
        # Counted, not transcribed. This read "Ten of the codes above" — which was the size of
        # the constant, not the number of rows in the table that are in it *(review
        # 2026-08-02: 12 rows, 8 of them in a constant of 10)*.
        f"{sum(1 for code in codes if code in MODEL_ABSTENTION_REASONS)} of the "
        f"{len(codes)} codes above are in `MODEL_ABSTENTION_REASONS` (which has "
        f"{len(MODEL_ABSTENTION_REASONS)} members, so "
        f"{len(MODEL_ABSTENTION_REASONS) - sum(1 for c in MODEL_ABSTENTION_REASONS if c in codes)}"
        " of them never appear here) and can be produced by either side, so the split is "
        "read off `NarrativeIssue.rejected_claim` — the flag step 10 added for exactly this "
        "measurement — and never inferred from the code. The largest single line is "
        "`DEFERRED_REQUIRED_SOURCE_LANE`: the lane deliberately keeps XBRL-preferred metrics on "
        "the prompt's menu so that naming one is a recorded refusal rather than an "
        "impossibility, and the model names them often.",
        "",
        "**“Not a rejected claim” is not quite “the model chose it”, and the "
        "difference shows up on three codes.** `rejected_claim` is False for "
        + ", ".join(f"`{c}`" for c in LANE_DECISIONS_WITHOUT_AN_ANSWER)
        + ", and none is a decision the model made: the scope offered no metric for the "
        "passage, or the prompt could not fit the context slot — both decided before a request "
        "is built — or an answer could not be used, decided after one. Those entries are lane "
        "decisions taken without a model answer to reject, on either side of a request rather "
        f"than in it. Subtracting them, the model itself chose "
        f"**{lexical.totals['model_chosen_abstentions']}** abstentions under the lexical scope "
        f"and **{hybrid.totals['model_chosen_abstentions']}** under the hybrid one — "
        "`model_chosen_abstentions` in the JSON, so a consumer can check it rather than read "
        "it here *(it existed only in this paragraph until 2026-08-02)*. The flag distinguishes "
        "two states and the protocol needs three; recorded rather than worked around, because "
        "widening it is a change to the lane.",
        "",
    ]
    lines += _failures_markdown(report)
    lines += _period_attribution_markdown(report)
    lines += _warning_census_markdown(report)
    lines += _comparison_markdown(report)
    lines += _probe_markdown(report)
    lines += _population_span_markdown(report)
    lines += _review_questions_markdown()
    lines += _per_case_markdown(report)
    return "\n".join(lines) + "\n"


def _population_span_markdown(report: NarrativeLaneReport) -> list[str]:
    """Why `population_accuracy` reads 0.000, computed rather than asserted.

    §7.1 scoring arrived in step 11 and immediately read zero, which invites being read as
    "the lane loses the denominator". Every emitted and expected wording is laid out here
    instead, because the pattern across them says something different and a reader should be
    able to check it rather than take this report's word for it. Nothing here is a hand-copied
    number: the rows, the counts and the sentence that follows them are all derived from the
    matches, so a run where the pattern stops holding says so.
    """
    rows: list[tuple[str, str, MatchRecord]] = []
    for scope in SCOPES:
        for case in report.views[scope].cases:
            for match in case.matched:
                if match.applies("population"):
                    rows.append((scope, case.case_id, match))

    if not rows:
        return []

    failing = [r for r in rows if not r[2].population_ok]
    contained = [r for r in failing if r[2].population_contained_in_expected]

    lines = [
        "## Why `population_accuracy` is 0.000",
        "",
        f"{len(rows)} matched observations across both scopes declare a population; "
        f"**{len(failing)}** disagree with gold. Of those, "
        f"**{len(contained)}** emit a string that is a *substring* of what gold expects.",
        "",
        "| scope | case | lane emitted | gold expects | emitted ⊂ expected |",
        "| --- | --- | --- | --- | --- |",
    ]
    for scope, case_id, match in rows:
        mark = "yes" if match.population_contained_in_expected else "**no**"
        lines.append(
            f"| {scope} | [`{case_id}`](#{case_id}) | {_cell(match.emitted_population)} | "
            f"{_cell(match.expected_population)} | {mark} |")

    lines += [""]
    if len(contained) == len(failing) and failing:
        lines += [
            "**Every disagreement has the same shape, and it is not a lost denominator.** "
            "Both sides quote the passage verbatim; they disagree about how much of the "
            "sentence `population.definition_raw` should span — the lane records the "
            "denominator phrase, the gold records the whole clause containing it. The "
            "requirement §7.1 exists to protect still holds under the lane's wordings: "
            "`our homes`, `our portfolio` and `our homes in inventory` remain distinct, so "
            "no two series are merged. Read this 0.000 as a span convention that has not "
            "been settled, **not** as evidence that population wording was dropped.",
            "",
            "**Which convention is right is a founder decision, not this stage's.** Changing "
            "either side moves the score on every row above. Recorded, unacted on, exactly "
            "like the annotation question below. See `V1_CLAIM_EXTRACTION.md` §4.0a.",
            "",
        ]
    elif failing:
        lines += [
            "**The disagreements no longer share one shape** — at least one emitted wording "
            "is not contained in the expected one, so the span-convention reading above does "
            "not cover this run. Each `no` row is a candidate lost denominator and should be "
            "read on its own.",
            "",
        ]
    return lines


def _review_questions_markdown() -> list[str]:
    lines = [
        "## What this run puts back to the reviewers",
        "",
        "Disagreements between the lane and a reviewed case that look like case errors rather "
        "than lane errors. **A stage never acts on these.** STAGE_11 forbids changing the "
        "benchmark, and a stage that edited a case because its own lane disagreed with it "
        "would have stopped measuring anything. Until a founder answers, each is scored under "
        "the case as written, which is the harsher reading. Answers are recorded in place "
        "rather than by deleting the question.",
        "",
    ]
    for entry in REVIEW_QUESTIONS:
        lines += [
            f"**`{entry['case_id']}`**",
            "",
            "| | |",
            "| --- | --- |",
            f"| the case said | {_cell(entry['the_case_says'])} |",
            f"| this run found | {_cell(entry['this_run_found'])} |",
            f"| the question | {_cell(entry['question'])} |",
            f"| resolution | {_cell(entry.get('resolution', 'open — not yet settled'))} |",
            "",
        ]
    return lines


def _failures_markdown(report: NarrativeLaneReport) -> list[str]:
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    lines = [
        "## Failure classification",
        "",
        "One category per failure, decided in the order below, first match wins, so two runs "
        "classify one failure the same way. The order runs from “the answer could not be "
        "read at all”, through “the answer was about the wrong thing”, to "
        "“the answer was about the right thing and got a field wrong”.",
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
        "**What is classified, and where this departs from STAGE_11 §4.** The brief asks "
        "for “every gold claim not matched, *and every emitted claim not in gold*” to "
        "land in a category. The second half contradicts §2 of the same brief and is not "
        "implemented: an unmatched emitted claim is **unrequired**, never a false positive, "
        "because each case annotates a deliberate subset of its passage. Classifying all of "
        "them as failures would make `matched_over_emitted` a precision under another name. "
        "The classified set is therefore: every gold observation the lane did not emit; every "
        "matched observation failing one of the six dimensions; and every emitted claim that "
        "breaks a silence a case explicitly requires. The remaining "
        f"{lexical.totals['unrequired_observations']} (lexical) / "
        f"{hybrid.totals['unrequired_observations']} (hybrid) unrequired emitted keys are "
        "counted and left unclassified, which is what the benchmark's own annotation policy "
        "permits it to say.",
        "",
        "| Scope | Case | Category | Metric | Period | Detail |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for view in (lexical, hybrid):
        for case in view.cases:
            for failure in case.failures:
                lines.append(
                    f"| {view.name} | [`{case.case_id}`](#{_anchor(case.case_id)}) | "
                    f"`{failure.category}` | `{failure.metric_id}` | "
                    f"{failure.period_key or EM_DASH} | {_cell(failure.detail)} |")
    lines.append("")
    return lines


def _period_attribution_markdown(report: NarrativeLaneReport) -> list[str]:
    lines = [
        "## Period attribution — the §8a.12 residual, measured",
        "",
        "`period_label` is an enum over the phrases the passage prints, so a period the "
        "passage never names is unrepresentable. It does **not** make a wrong period "
        "unrepresentable: a comparative paragraph prints its prior-period phrase too, and "
        "choosing it passes the period-type check, the value check, the evidence check and "
        "`ontology.validate_claims`. This table is what step 10 could not close.",
        "",
        "| | Lexical | Hybrid |",
        "| --- | --- | --- |",
    ]
    for label, key in (
            ("claims emitted", "claims"),
            ("period phrase inside the quoted evidence sentence", "label_in_evidence"),
            ("period resolved to something other than the passage's reporting period",
             "chose_comparative"),
            ("quoted sentence states a period of its own",
             "sentence_states_its_own_period"),
            ("greatest distance from the evidence sentence (characters)",
             "max_distance_from_evidence")):
        lines.append(
            f"| {label} | {report.views['lexical'].totals['period_attribution'][key]} | "
            f"{report.views['hybrid'].totals['period_attribution'][key]} |")
    lines += [
        "",
        "**`chose_comparative` is an upper bound, not a count of errors.** The passage's "
        "reporting period is `core.periods.reporting_period_keys` — the latest end date of "
        "each type the passage prints — and on a passage that prints a *maturity* date rather "
        "than a period end, that is not a reporting period at all. "
        "`event-credit-facility-established-2022` is one: its latest printed date is the "
        "facility's final maturity, so the correctly dated claim for the establishment date "
        "counts as comparative here. Read the per-claim table, not the total.",
        "",
        "**What the totals actually say about §8a.12.** The period phrase sits inside the "
        "quoted evidence sentence for a minority of claims, and the model reaches a long way "
        "for it otherwise — up to 1,354 characters backwards. The failure this produces is "
        "not usually a *comparative* period; it is the reporting period attached to a figure "
        "that belongs to some other one. `letter-prose-multiple-metrics-q4-2021` is the clean "
        "example: the letter reports both a quarter and a full year, the passage prints no "
        "phrase that resolves to a full year, so `period_label`'s enum cannot express FY2021 "
        "at all — and the model attached `4Q21` to the full-year figures rather than "
        "abstaining with `MISSING_PERIOD`. Narrowing the enum to printed phrases removed the "
        "invented-period failure and converted the unrepresentable-period case into a "
        "wrong-period one.",
        "",
        "Per claim, under the lexical scope. `distance` is signed characters from the quoted "
        "evidence sentence — negative before it, positive after it, 0 inside it.",
        "",
        "| Case | Metric | Period | Chosen phrase | In evidence | Distance | Comparative? |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in report.views["lexical"].cases:
        for attribution in case.attributions:
            lines.append(
                f"| [`{case.case_id}`](#{_anchor(case.case_id)}) | "
                f"`{attribution.metric_id}` | {attribution.period_key} | "
                f"{_cell(attribution.period_label)} | "
                f"{'yes' if attribution.in_evidence else 'no'} | "
                f"{EM_DASH if attribution.distance is None else attribution.distance} | "
                f"{'**yes**' if attribution.chose_comparative else 'no'} |")
    lines.append("")
    return lines


def _warning_census_markdown(report: NarrativeLaneReport) -> list[str]:
    lines = [
        "## Ontology warning census",
        "",
        "`unpreferred_source_lane`, relayed by `core.validation.validate` since step 10. A "
        "narrative claim for a metric whose ontology entry prefers `normalized_table` is not an "
        "error — a preference is not a prohibition — and how often it happens is a "
        "finding about lane routing rather than about this lane.",
        "",
        "| Scope | Case | Warning |",
        "| --- | --- | --- |",
    ]
    any_warning = False
    for view in (report.views["lexical"], report.views["hybrid"]):
        for case in view.cases:
            for warning in case.ontology_warnings:
                any_warning = True
                lines.append(
                    f"| {view.name} | [`{case.case_id}`](#{_anchor(case.case_id)}) | "
                    f"{_cell(warning)} |")
    if not any_warning:
        lines.append(f"| {EM_DASH} | {EM_DASH} | no warning was raised on any claim |")
    lines += [
        "",
        "Errors and §7 policy rejections are counted beside the warnings because V1 §11's "
        "first acceptance criterion is that every emitted claim passes "
        "`ontology.validate_claims` with zero errors. Step 10's live gate measured that on "
        "three passages chosen from the corpus and not from the benchmark; this is the same "
        "check over the reviewed cases, and **the criterion "
        + ("does not hold**." if report.views["lexical"].totals["ontology_errors"]
           or report.views["hybrid"].totals["ontology_errors"]
           else "holds**: no emitted claim produced an ontology error under either scope.")
        + "",
        "",
        "**Counts here are per case and two cases annotate one passage**, so every warning and "
        "every error on `q42021formxex992sharehol.htm#p10` is counted twice. The Counts table "
        "above states both forms. Distinct: "
        f"{report.views['lexical'].totals['ontology_warnings_distinct']} warnings and "
        f"{report.views['lexical'].totals['ontology_errors_distinct']} errors under the "
        "lexical scope.",
        "",
        "| Scope | Case | §7 policy rejection or ontology error |",
        "| --- | --- | --- |",
    ]
    any_failure = False
    for view in (report.views["lexical"], report.views["hybrid"]):
        for case in view.cases:
            for entry in list(case.assembly_rejections) + list(case.ontology_errors):
                any_failure = True
                lines.append(f"| {view.name} | `{case.case_id}` | {_cell(entry)} |")
    if not any_failure:
        lines.append(f"| {EM_DASH} | {EM_DASH} | none: every emitted claim reached "
                     "`OntologyClaim` and validated clean |")
    lines.append("")
    return lines


def _comparison_markdown(report: NarrativeLaneReport) -> list[str]:
    comparison = report.comparison
    lines = [
        "## Lexical versus hybrid",
        "",
        f"The two scopes issue **the identical request on "
        f"{comparison['cases_with_identical_scope']} of {comparison['cases']} cases**. That is "
        "the number that bounds everything else here, and it is now the *request digest* "
        "rather than the concept list: the digest covers the prompt, the schema, the model and "
        "the sampling parameters, so two cases sharing one is not an inference that the runs "
        "agree — it is the same stored answer. On those cases the two scopes are not being "
        "compared.",
        "",
        "| Score | Lexical | Hybrid | Hybrid − lexical | Denominator (lex/hyb) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key in sorted(report.views["lexical"].totals["scores"]):
        lines.append(
            f"| {key} | {report.views['lexical'].totals['scores'][key]:.3f} | "
            f"{report.views['hybrid'].totals['scores'][key]:.3f} | "
            f"{comparison['score_delta_hybrid_minus_lexical'][key]:+.3f} | "
            f"{report.views['lexical'].totals['score_denominators'][key]}/"
            f"{report.views['hybrid'].totals['score_denominators'][key]} |")
    lines += [
        "",
        "**A ratio in that table can move because its denominator moved, and one of them "
        "does.** Adding a single correct match takes `value_accuracy` from 9/10 to 10/11, "
        "which is a rise of +0.009 that corrects no value error at all. The comparison "
        "therefore does **not** read a moved ratio as a dimension improving, and the "
        "recommendation below is decided per claim *(corrected 2026-08-02; the previous "
        "version listed `value_accuracy` among the scores hybrid improved)*.",
        "",
        "### Recommendation for step 13",
        "",
        f"**{comparison['recommendation']['verdict']}** — "
        f"{comparison['recommendation']['summary']}. Evidence width: "
        f"{comparison['recommendation']['evidence_width']}. Every word of that verdict is "
        "decided by counting claims whose `MatchRecord` says what happened to them: gold "
        f"claims one scope adds that are clean on **every** scored dimension "
        f"({comparison['recommendation']['gold_claims_added_clean']}), gold claims it adds "
        f"that are not ({comparison['recommendation']['gold_claims_added_not_clean']}), and "
        "gold observations clean under one scope and not the other "
        f"({comparison['recommendation']['per_claim_regressions']}).",
        "",
        "| Scope | Case | Claim | Gold? | Clean on every scored dimension | Fails |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in comparison["claims_added_by_one_scope"]:
        lines.append(
            f"| {entry['scope']} | [`{entry['case_id']}`](#{_anchor(entry['case_id'])}) | "
            f"`{entry['claim']}` | {'yes' if entry['is_gold'] else 'no'} | "
            f"{'yes' if entry['clean_on_every_scored_dimension'] else '**no**'} | "
            + (", ".join(f"`{d}`" for d in entry["failed_dimensions"]) or EM_DASH) + " |")
    if not comparison["claims_added_by_one_scope"]:
        lines.append(f"| {EM_DASH} | {EM_DASH} | {EM_DASH} | {EM_DASH} | {EM_DASH} | "
                     "neither scope emitted a claim the other did not |")
    lines += [
        "",
        "One qualification the numbers do not carry on their own: the evidence is **one "
        "concept in one passage**. `top_k` is 2 and the second "
        "`pct_homes_on_market_gt_120_days` paraphrase sits at rank 2, so hybrid does not "
        "reach it either and the ontology §16.1 wording gap is not closed by this "
        "configuration.",
        "",
        "### Claim by claim, where the scopes differ",
        "",
    ]
    if not comparison["differences"]:
        lines += ["Nowhere. The two scopes produced the same scope and the same claims on "
                  "every case.", ""]
        return lines
    for difference in comparison["differences"]:
        lines += [
            f"**`{difference['case_id']}`**",
            "",
            "| | |",
            "| --- | --- |",
            "| concepts only lexical offers | "
            + (", ".join(f"`{c}`" for c in difference["concepts_only_lexical"]) or EM_DASH)
            + " |",
            "| concepts only hybrid offers | "
            + (", ".join(f"`{c}`" for c in difference["concepts_only_hybrid"]) or EM_DASH)
            + " |",
            "| claims only lexical emits | "
            + (", ".join(f"`{c}`" for c in difference["claims_only_lexical"]) or EM_DASH)
            + " |",
            "| claims only hybrid emits | "
            + (", ".join(f"`{c}`" for c in difference["claims_only_hybrid"]) or EM_DASH)
            + " |",
            "| of those, gold | "
            + (", ".join(f"`{c}`" for c in difference["claims_only_hybrid_that_are_gold"])
               or EM_DASH)
            + " |",
            "",
        ]
    return lines


def _probe_markdown(report: NarrativeLaneReport) -> list[str]:
    lines = [
        "## Named probes — the two `pct_homes_on_market_gt_120_days` paraphrases",
        "",
        "Steps 8 and 9 measured whether each scope *reaches* this concept. This is the first "
        "stage that can say whether reaching it produced a correct claim.",
        "",
        "| Case | Scope | In scope | Gold | Claims | Matched | Correct on every scored "
        "dimension | Fails |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for probe in report.probes:
        for scope_name in SCOPES:
            entry = probe["scopes"].get(scope_name)
            if entry is None:
                continue
            lines.append(
                f"| [`{probe['case_id']}`](#{_anchor(probe['case_id'])}) | {scope_name} | "
                f"{'yes' if entry['in_scope'] else '**no**'} | {entry['gold_observations']} | "
                f"{entry['claims_emitted']} | {entry['matched_gold']} | "
                f"{'yes' if entry['correct_on_every_dimension'] else '**no**'} | "
                + (", ".join(f"`{d}`" for d in entry["failed_dimensions"]) or EM_DASH) + " |")
    lines.append("")
    for probe in report.probes:
        lines += [f"- `{probe['case_id']}`: “{probe['wording']}” — "
                  f"{probe['note']}", ""]
    return lines


def _per_case_markdown(report: NarrativeLaneReport) -> list[str]:
    lines = ["## Per-case results", "",
             "Gold, emitted, matched and failures under each scope. Cases where the two "
             "scopes offer the lane the **same concepts** — the same set, not merely the same "
             "number of them — build the same prompt and therefore replay one stored answer. "
             "The request digest below each case is what settles it.",
             "",
             "| Case | Category | Gold | Emitted (lex/hyb) | Matched (lex/hyb) | "
             "Failures (lex/hyb) |",
             "| --- | --- | --- | --- | --- | --- |"]
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    for case in lexical.cases:
        other = hybrid.case(case.case_id)
        lines.append(
            f"| [`{case.case_id}`](#{_anchor(case.case_id)}) | "
            f"{report.case_index[case.case_id]['category']} | {case.counts['gold']} | "
            f"{case.counts['emitted']}/{other.counts['emitted']} | "
            f"{case.counts['matched']}/{other.counts['matched']} | "
            f"{case.counts['failures']}/{other.counts['failures']} |")
    lines.append("")

    for case in lexical.cases:
        lines += _case_markdown(case, hybrid.case(case.case_id))
    return lines


def _case_markdown(case: CaseScore, other: CaseScore) -> list[str]:
    lines = [
        f"### {case.case_id}",
        "",
        f"- passage: `{case.passage_id}`",
        f"- candidate metric concepts: {len(case.scope_concept_ids)} lexical, "
        f"{len(other.scope_concept_ids)} hybrid"
        + ("" if set(case.scope_concept_ids) == set(other.scope_concept_ids)
           else " — **the scopes differ here**"),
        f"- request digest: "
        + (f"`{case.request_digest}` lexical, `{other.request_digest}` hybrid"
           + (" — **the same request**" if case.request_digest == other.request_digest
              else " — **two different requests**")
           if case.request_digest or other.request_digest
           else "no request was issued: the scope offered no metric concept"),
        f"- gold {case.counts['gold']} · emitted {case.counts['emitted']} · matched "
        f"{case.counts['matched']} · unrequired {case.counts['unrequired']} · "
        f"rejected {case.counts['rejected_claims']} · model abstentions "
        f"{case.counts['model_abstentions']} (lexical)",
    ]
    if case.duplicate_claim_keys or other.duplicate_claim_keys:
        lines.append(
            "- **the lane emitted two claims under one key here, so the matcher chose**: "
            + ", ".join(f"`{k}`" for k in
                        sorted(set(case.duplicate_claim_keys) | set(other.duplicate_claim_keys)))
            + ". The first in emission order is scored; the other is counted as unrequired. "
              "Every number below that rests on the chosen claim rests on that rule.")
    if case.case_id == TABLE_PASSAGE_CASE:
        lines.append(
            "- **this case's passage is a `table` passage.** `supports()` requires "
            "`passage_kind == \"narrative\"`, so typed selection would never route it to this "
            "lane; the report reaches it through `extract_passage`, the same code without the "
            "routing question. STAGE_11 §1 asks for the `lane: either` case to be scored "
            "and does not say this.")
    lines += ["", "#### Gold observations (lexical scope)", "",
              "| Metric | Period | Expected | Emitted | Unit | Scale | Verdict |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    matched = {(m.metric_id, m.period_key): m for m in case.matched}
    if not case.gold:
        lines.append(f"| {EM_DASH} | {EM_DASH} | {EM_DASH} | {EM_DASH} | {EM_DASH} | "
                     f"{EM_DASH} | this case annotates no gold claim |")
    for gold in case.gold:
        match = matched.get((gold.metric_id, gold.period_key))
        if match is None:
            lines.append(
                f"| `{gold.metric_id}` | {gold.period_key} | {_number(gold.value)} | "
                f"{EM_DASH} | {gold.unit} | {gold.scale_applied} | **MISSED** |")
            continue
        verdict = ("ok" if match.all_ok
                   else "**WRONG**: " + ", ".join(match.failed_dimensions))
        unit = (match.emitted_unit if match.unit_ok
                else f"{match.expected_unit} / {match.emitted_unit}")
        scale = (match.emitted_scale if match.scale_ok
                 else f"{match.expected_scale} / {match.emitted_scale}")
        lines.append(
            f"| `{gold.metric_id}` | {gold.period_key} | {_number(match.expected_value)} | "
            f"{_number(match.emitted_value)} | {unit} | {scale} | {verdict} |")
    lines.append("")

    if case.unrequired:
        lines += [
            f"#### Emitted but not annotated ({len(case.unrequired)}, lexical scope)",
            "",
            "**Unrequired, not errors and not verified.** The case annotates a deliberate "
            "subset of its passage, so a correct claim for an observation the reviewers did "
            "not list lands here. Nothing in the benchmark scores these.",
            "",
            "| Metric | Period |",
            "| --- | --- |",
        ]
        for metric_id, period_key in case.unrequired:
            lines.append(f"| `{metric_id}` | {period_key} |")
        lines.append("")

    if case.expected_abstentions:
        lines += [
            "#### Expected abstentions",
            "",
            "| Reason the case names | Concepts | Claimed anyway | Answer usable | "
            "Honoured | Same code recorded |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for verdict in case.abstentions:
            lines.append(
                f"| `{verdict.reason}` | "
                + (", ".join(f"`{c}`" for c in verdict.concept_ids) or EM_DASH) + " | "
                + (", ".join(f"`{c}`" for c in verdict.claimed_concept_ids) or EM_DASH)
                + f" | {'yes' if verdict.answer_usable else '**no**'}"
                f" | {'yes' if verdict.honoured else '**no**'} | "
                f"{'yes' if verdict.code_recorded else 'no'} |")
        lines.append("")

    populations = [m for m in case.matched if m.applies("population")]
    ambiguities = [m for m in case.matched if m.applies("ambiguity_codes")]
    if populations or ambiguities:
        lines += [
            "#### §7 policy dimensions on the matched pairs (lexical scope)",
            "",
            "| Metric | Period | Population expected | Population emitted | Ambiguity codes "
            "expected | attached at assembly | on the lane claim |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for match in sorted(set(populations) | set(ambiguities),
                            key=lambda m: (m.metric_id, m.period_key)):
            lines.append(
                f"| `{match.metric_id}` | {match.period_key} | "
                f"{_cell(match.expected_population or EM_DASH)} | "
                + ("" if match.population_ok else "**")
                + _cell(match.emitted_population or EM_DASH)
                + ("" if match.population_ok else "**") + " | "
                + (", ".join(f"`{c}`" for c in match.expected_ambiguity_codes) or EM_DASH)
                + " | "
                + (", ".join(f"`{c}`" for c in match.emitted_ambiguity_codes) or EM_DASH)
                + " | "
                + (", ".join(f"`{c}`" for c in match.lane_claim_ambiguity_codes) or EM_DASH)
                + " |")
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


def _number(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    return f"{value:,}"


# -- writing -------------------------------------------------------------------------------------


def write_reports(
    report: NarrativeLaneReport, directory: Path = REPORTS_DIR
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{REPORT_STEM}.json"
    markdown_path = directory / f"{REPORT_STEM}.md"
    json_path.write_text(render_json(report), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
