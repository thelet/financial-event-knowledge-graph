"""Scoring the narrative lane against the reviewed prose cases.

The eight dimensions of V1_CLAIM_EXTRACTION §4.0, scored independently, plus the failure
classifier STAGE_11 §4 asks for. Every score in the narrative report is computed here and
nowhere else; `narrative_runner.py` composes and renders and never recomputes a verdict.

**The six per-match dimensions are the table lane's own, imported rather than restated.**
`extraction/stages/tables/evaluation.py` already scores value, unit, scale, period, subject
and evidence against a `LaneClaim`, and a `LaneClaim` is what both lanes emit. STAGE_11 asks
for the two reports to be comparable; the strongest available form of that is the same
function, not a second one written to the same description. What this module adds is what
prose has and a table does not: the two §7 policy dimensions, three separate readings of what
one dimension used to call "ambiguity", a ten-category failure classifier, and the §8a.12
period-attribution reading.

**Twelve dimensions, not eight, and the four extra are corrections rather than additions**
*(2026-08-02, after review)*. §7.1's population wording and §7.3's ambiguity codes were scored
by nothing, so seven matched `pct_homes_on_market_gt_120_days` pairs that disagreed with gold
about the denominator all scored clean; and the dimension §4.0 calls "ambiguity" was measuring
whether a required silence was kept for any stated reason, over fifteen expectations of which
exactly one names ambiguity candidates. The first is now two scored dimensions and the second
is three separately reported rates, each with its denominator printed. `DIMENSIONS` states all
twelve.

**It lives under `benchmarks/` and not under `extraction/`.** Not only because
`tests/extraction/test_typed_selection.py` parses every module under `extraction/` and fails
on a benchmark import — the table lane's scorer lives under `extraction/` and passes that
test by taking gold as an argument. The reason is that this scorer's vocabulary *is* the
benchmark's: expected abstentions, gold populations and ten failure categories are things
the reviewed cases declare, not things the lane knows about.

**Two dimensions are tautologies under this matching rule and are reported as such.** A
predicted claim is matched to a gold claim on `(metric_id, period key)`, so a matched pair
agrees about its metric and its period by construction: `period_accuracy` cannot be anything
but 1.000, and neither can the metric half. That is equally true of the committed table-lane
report, where `period_accuracy: 1.000` has been read as a measurement. The real period
measurement is `period_wrong` in the failure classification — a gold observation the lane
claimed under a different period — and the §8a.12 attribution table. Both are computed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from extraction.core.models import LaneClaim
from extraction.stages.narrative.public import (
    METRIC_OUT_OF_SCOPE,
    MISSING_POPULATION_DEFINITION,
    MODEL_ANSWER_UNUSABLE,
    PROMPT_EXCEEDS_CONTEXT,
    QUOTED_SPAN_NOT_IN_PASSAGE,
    SCALE_NOT_APPLICABLE,
    SCALE_NOT_DECLARED,
    UNIT_CONTRADICTS_ONTOLOGY,
    VALUE_CONTRADICTS_QUOTED_TEXT,
    VALUE_NOT_IN_QUOTED_SPAN,
)
from extraction.stages.tables.evaluation import GoldClaim, compare, evaluate_case
from extraction.stages.tables.public import (
    INVALID_NUMBER,
    MISSING_PERIOD,
    MISSING_UNIT,
    PERIOD_TYPE_MISMATCH,
    UNRESOLVED_METRIC,
)

# Codes the lane records with `rejected_claim` False that the model nevertheless did not
# choose. `NarrativeIssue.rejected_claim` distinguishes two states where three are needed: it
# is False both when the model declined and when the *lane* decided without a model answer to
# reject — before a request exists (`UNRESOLVED_METRIC`, `PROMPT_EXCEEDS_CONTEXT`) or after an
# answer proved unusable (`MODEL_ANSWER_UNUSABLE`). Subtracting them is the corrected figure,
# and it is computed here so the JSON carries it rather than the Markdown alone *(review
# 2026-08-02: it existed only in the rendered prose, where no consumer could check it)*.
LANE_DECISIONS_WITHOUT_AN_ANSWER: tuple[str, ...] = (
    MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT, UNRESOLVED_METRIC)

# The six `evaluation.compare` computes per matched pair. `metric_identity` and the abstention
# readings are not among them: the first is recall and the rest are scored per expectation,
# not per match.
MATCH_DIMENSIONS: tuple[str, ...] = (
    "value", "unit", "scale", "period", "subject", "evidence")

# The two prose adds, and the reason they are here rather than in `compare`
# *(added 2026-08-02, after review found the report scoring neither)*.
#
# §7.1 makes the filed denominator wording part of what a `pct_homes_on_market_gt_120_days`
# observation *is*, and §7.3 makes the ontology's declared ambiguity codes travel with every
# observation of the metrics that declare them. Neither is a dimension a table row has, so
# neither is in the table lane's `compare` — and until this stage neither was scored anywhere.
# The consequence was measurable: all seven matched `pct_homes_on_market_gt_120_days` pairs
# disagreed with gold about the population wording, and every one of them scored clean.
PROSE_MATCH_DIMENSIONS: tuple[str, ...] = ("population", "ambiguity_codes")

SCORED_MATCH_DIMENSIONS: tuple[str, ...] = MATCH_DIMENSIONS + PROSE_MATCH_DIMENSIONS

# Every scored dimension: its label, the key it is stored under, and the denominator it is
# taken over. Stated as data because the JSON, the Markdown and the CLI all print this list,
# and three transcriptions of it is how a report starts describing a denominator it does not
# use.
#
# **§4.0 names eight and this is twelve.** The eight are all here. What is added is the two §7
# policies above, and the split of the one §4.0 calls "ambiguity" into the three separate
# things it was measuring at once — see `AbstentionVerdict`. A dimension named for something it
# does not measure is worse than a missing one, because it reads as evidence.
DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("metric identity", "metric_identity_accuracy",
     "matched gold observations / gold observations"),
    ("value", "value_accuracy", "value_ok / matched observations"),
    ("unit", "unit_accuracy", "unit_ok / matched observations"),
    ("scale", "scale_accuracy", "scale_ok / matched observations"),
    ("period", "period_accuracy", "period_ok / matched observations"),
    ("subject", "subject_accuracy", "subject_ok / matched observations"),
    ("evidence", "evidence_accuracy", "evidence_ok / matched observations"),
    ("population wording (§7.1)", "population_accuracy",
     "population_ok / matched observations whose metric declares a population"),
    ("ambiguity codes (§7.3)", "ambiguity_codes_accuracy",
     "ambiguity_codes_ok / matched observations that declare any"),
    ("expected abstentions honoured", "abstention_honoured_rate",
     "honoured expected abstentions / expected abstentions"),
    ("ambiguity preserved", "ambiguity_preserved_rate",
     "honoured / expected abstentions that name ambiguity candidates"),
    ("abstention code agreement", "abstention_code_agreement",
     "expectations whose own code the lane recorded / expected abstentions"),
)

# -- the ten failure categories, and the order they are decided in ------------------------------

SCHEMA_OR_PARSE_FAILURE = "schema_or_parse_failure"
AMBIGUITY_COLLAPSED = "ambiguity_collapsed"
EVIDENCE_UNGROUNDED = "evidence_ungrounded"
METRIC_MISIDENTIFIED = "metric_misidentified"
METRIC_NOT_IDENTIFIED = "metric_not_identified"
PERIOD_WRONG = "period_wrong"
SUBJECT_WRONG = "subject_wrong"
UNIT_WRONG = "unit_wrong"
SCALE_WRONG = "scale_wrong"
VALUE_WRONG = "value_wrong"

FAILURE_CATEGORIES: tuple[str, ...] = (
    SCHEMA_OR_PARSE_FAILURE, AMBIGUITY_COLLAPSED, EVIDENCE_UNGROUNDED, METRIC_MISIDENTIFIED,
    METRIC_NOT_IDENTIFIED, PERIOD_WRONG, SUBJECT_WRONG, UNIT_WRONG, SCALE_WRONG, VALUE_WRONG,
)

# The order categories are tried in, printed in the report so two runs classify one failure
# the same way (STAGE_11 §4). It runs from "the answer could not be read at all", through
# "the answer was about the wrong thing", to "the answer was about the right thing and got a
# field wrong" — a unit complaint about a figure quoted from a sentence that is not in the
# passage would name the wrong problem.
DECISION_ORDER: tuple[str, ...] = FAILURE_CATEGORIES

# Which lane issue code, recorded against the gold metric, decides which category for a gold
# observation the lane never emitted. Derived from the vocabulary rather than from prose: each
# of these is a refusal `response_mapping.py` makes, and the category is what that refusal
# means for the observation that went missing.
_CODE_CATEGORY: dict[str, str] = {
    QUOTED_SPAN_NOT_IN_PASSAGE: EVIDENCE_UNGROUNDED,
    VALUE_NOT_IN_QUOTED_SPAN: EVIDENCE_UNGROUNDED,
    VALUE_CONTRADICTS_QUOTED_TEXT: EVIDENCE_UNGROUNDED,
    MISSING_PERIOD: PERIOD_WRONG,
    PERIOD_TYPE_MISMATCH: PERIOD_WRONG,
    UNIT_CONTRADICTS_ONTOLOGY: UNIT_WRONG,
    MISSING_UNIT: UNIT_WRONG,
    SCALE_NOT_APPLICABLE: SCALE_WRONG,
    SCALE_NOT_DECLARED: SCALE_WRONG,
    INVALID_NUMBER: VALUE_WRONG,
    METRIC_OUT_OF_SCOPE: METRIC_MISIDENTIFIED,
    MISSING_POPULATION_DEFINITION: METRIC_NOT_IDENTIFIED,
    # No request was issued, so nothing about the answer is known. The same category a
    # non-conforming answer lands in, for the same reason: no finer classification exists.
    PROMPT_EXCEEDS_CONTEXT: SCHEMA_OR_PARSE_FAILURE,
}

# The two codes that mean "this passage produced no answer to score". `MODEL_ANSWER_UNUSABLE`
# is an answer that could not be used; `PROMPT_EXCEEDS_CONTEXT` is a request that was never
# issued because it could not fit. Either way there is nothing finer to say about the passage,
# and an expectation cannot be scored as honoured on the strength of a silence neither the
# model nor the lane chose.
_NO_USABLE_ANSWER: frozenset[str] = frozenset({MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT})

# Where a failure was found. Not a category — a failure is a disagreement with the reviewed
# case, and these say which of the three shapes of disagreement it was.
MISSED_GOLD = "missed_gold"
WRONG_MATCH = "wrong_match"
OVER_EMITTED = "over_emitted"

# The metric slot on a failure that is about a required silence rather than about a concept.
# An em dash rather than an empty string so a reader of the table sees a deliberate absence.
EM_DASH_METRIC = "—"


# -- what a case declares -------------------------------------------------------------------


@dataclass(frozen=True)
class GoldObservation:
    """One reviewed prose observation, as the case file states it.

    `population_definition_raw` is carried because §7.1 makes it part of what a
    `pct_homes_on_market_gt_120_days` observation *is*: two claims whose denominator wording
    differs are not one series, so a report that showed only the value would show two
    identical-looking 18% readings as interchangeable.
    """

    metric_id: str
    period_key: str
    value: float
    unit: str
    scale_applied: str
    currency: str | None
    subject_entity_id: str
    population_definition_raw: str | None
    # §7.3, as the case declares it. Six gold observations carry `pct_120_days_denominator`
    # and nothing scored them until 2026-08-02.
    ambiguity_codes: tuple[str, ...]
    note: str | None

    def as_gold_claim(self) -> GoldClaim:
        return GoldClaim(self.metric_id, self.value, self.unit, self.period_key,
                         self.subject_entity_id, self.scale_applied, self.currency)


@dataclass(frozen=True)
class ExpectedAbstention:
    """One silence the reviewers require, with the concepts it names.

    `concept_ids` merges the case files' two spellings — `metrics:` on a
    source-lane abstention and `candidates:` on an ambiguity one — because for scoring they
    mean one thing: no claim may name these. `ambiguity_candidates` stays separate, since §2's
    ambiguity dimension is specifically about a declared-ambiguous surface resolving to one
    concept and a deferred metric is a different finding.
    """

    reason: str
    detail: str
    concept_ids: tuple[str, ...]
    ambiguity_candidates: tuple[str, ...]
    required_lane: str | None


# -- what a run produced --------------------------------------------------------------------


@dataclass(frozen=True)
class MatchRecord:
    """A gold observation beside the claim that matched it, dimension by dimension.

    The six booleans come from `evaluation.compare` and are never recomputed here. Two
    implementations of one comparison can disagree silently — 46 rows reading WRONG beside an
    accuracy of 1.000, which is the defect the table-lane report shipped with at step 6.
    """

    metric_id: str
    period_key: str
    expected_value: float
    emitted_value: float
    expected_unit: str
    emitted_unit: str
    expected_scale: str
    emitted_scale: str
    expected_subject_entity_id: str
    emitted_subject_entity_id: str
    expected_population: str | None
    emitted_population: str | None
    # What the ontology's §7.3 policy attaches at assembly, and what the lane claim carried
    # before it. They differ — the lane emits none and `core.assembly.to_claim` supplies them —
    # and both are recorded so the report cannot show one and be read as showing the other.
    expected_ambiguity_codes: tuple[str, ...]
    emitted_ambiguity_codes: tuple[str, ...]
    lane_claim_ambiguity_codes: tuple[str, ...]
    raw_text: str
    value_ok: bool
    unit_ok: bool
    scale_ok: bool
    period_ok: bool
    subject_ok: bool
    evidence_ok: bool
    population_ok: bool
    ambiguity_codes_ok: bool
    # A weaker relation than equality, reported beside `population_ok` and never scored on.
    # Every disagreement measured so far is of this shape — the lane quotes "our homes" where
    # the case quotes the whole clause containing it — and a bare 0.000 would not say so.
    population_contained_in_expected: bool

    def applies(self, dimension: str) -> bool:
        """Whether a dimension is a question this pair can be asked.

        The six from `compare` always are. The two §7 dimensions are asked only where **the
        reviewed case declares something**: a metric with no annotated denominator has no
        denominator wording to get wrong, and scoring it as correct would inflate the ratio
        with pairs that were never at risk.

        Driven by gold rather than symmetrically on purpose. `recon-shareholder-letter-table-
        as-prose` declares no ambiguity codes while the ontology attaches
        `pct_120_days_denominator` to the metric, so a symmetric rule charges the lane a
        failure for a disagreement between the benchmark and the vocabulary that the lane had
        no part in. That disagreement is real and is reported — `carries_undeclared_codes`
        below, and the census in the report — as a question for the reviewers rather than as a
        score against the lane.
        """
        if dimension == "population":
            return self.expected_population is not None
        if dimension == "ambiguity_codes":
            return bool(self.expected_ambiguity_codes)
        return dimension in MATCH_DIMENSIONS

    @property
    def carries_undeclared_codes(self) -> bool:
        """The assembled claim carries an ambiguity code the reviewed case does not declare."""
        return bool(set(self.emitted_ambiguity_codes) - set(self.expected_ambiguity_codes))

    @property
    def all_ok(self) -> bool:
        return all(getattr(self, f"{d}_ok")
                   for d in SCORED_MATCH_DIMENSIONS if self.applies(d))

    @property
    def failed_dimensions(self) -> tuple[str, ...]:
        return tuple(d for d in SCORED_MATCH_DIMENSIONS
                     if self.applies(d) and not getattr(self, f"{d}_ok"))


@dataclass(frozen=True)
class AbstentionVerdict:
    """Whether one required silence was kept, and what the lane said instead.

    `honoured` is deliberately a conjunction of four checkable things and not a code
    comparison. The case files and the lane do not share an abstention vocabulary — the cases
    say `UNAVAILABLE_REQUIRED_SOURCE_LANE`, `AMBIGUOUS_COLUMN_ORDER`, `SCALE_SOURCE_ONLY`,
    `HYPOTHETICAL_NOT_ASSERTED`, `THIRD_PARTY_ROLE`, `UNNAMED_ENTITY` and
    `NO_HEADCOUNT_STATED`, none of which is in `stages/narrative/public.ISSUE_CODES` — so
    scoring on the code would score the vocabularies against each other rather than the lane
    against the case. `code_recorded` reports the comparison anyway, as information, and it is
    now its own reported rate rather than a column nothing aggregates.

    **This is not an ambiguity measurement and was reported as one until 2026-08-02.** Of the
    fifteen expectations the reviewed prose cases declare, exactly **one** names ambiguity
    candidates; the other fourteen span eleven other reason kinds — a third-party subject, a
    forward-looking statement, a definitional paragraph, a derived comparison. What the
    conjunction below measures is whether a required silence was kept *for a stated reason*,
    which is an abstention-honouring rate. It is now named one. Ambiguity proper is scored
    separately over the expectations that actually declare candidates, and the report states
    that its denominator is 1.
    """

    reason: str
    concept_ids: tuple[str, ...]
    ambiguity_candidates: tuple[str, ...]
    # Concepts the expectation names that the lane emitted a claim for anyway. Non-empty means
    # the silence was broken in the way the case names.
    claimed_concept_ids: tuple[str, ...]
    # True when the case declares no gold claim at all and the lane emitted none.
    silence_kept: bool
    # True when the lane recorded *any* issue on this passage — a stated silence rather than a
    # bare one. Named for what it tests: it was called `reason_stated`, which reads as "the
    # reason this expectation names was stated" and is a different and stronger claim.
    any_issue_recorded: bool
    # False when the passage's answer could not be used at all. An unusable answer produces no
    # claims and one recorded issue, which would otherwise satisfy every condition above and
    # score a garbage answer as a correct abstention — the exact flattery this benchmark
    # exists to refuse *(found by running it, 2026-08-02)*.
    answer_usable: bool
    # Whether the lane happened to record an issue under this exact code.
    code_recorded: bool
    honoured: bool

    @property
    def failure_count(self) -> int:
        """How many classified failures this expectation contributes.

        One per concept it named that was claimed anyway; otherwise one for the expectation as
        a whole when it was not honoured. Kept here so the classifier and the totality test
        read the same rule from one place.
        """
        if self.honoured:
            return 0
        return len(self.claimed_concept_ids) or 1


@dataclass(frozen=True)
class Failure:
    """One classified disagreement between the lane and a reviewed case."""

    category: str
    source: str
    metric_id: str
    period_key: str | None
    detail: str


@dataclass(frozen=True)
class PeriodAttribution:
    """§8a.12, per emitted claim: which printed phrase was chosen and how far away it was.

    `chose_comparative` is the number STAGE_11 §2 asks for. A passage's reporting period is
    the latest end date of each type (`core.periods.reporting_period_keys`); a claim resolving
    to anything else took a phrase the passage prints for a *comparative* period. That is not
    automatically wrong — a letter legitimately quotes last year's level — so it is reported
    beside `sentence_states_its_own_period`, which says whether the quoted evidence settles it.
    """

    metric_id: str
    period_key: str
    period_label: str
    in_evidence: bool | None
    distance: int | None
    char_start: int | None
    chose_comparative: bool
    sentence_states_its_own_period: bool
    reporting_period_keys: tuple[str, ...]
    matched_gold: bool


@dataclass
class CaseScore:
    """Everything one (case, scope) pair produced, scored."""

    case_id: str
    scope: str
    passage_id: str
    # The digest of the request this case's answer was replayed under, or None where the lane
    # issued none. What makes "the two scopes are the same run on this case" checkable rather
    # than inferred from the concept lists.
    request_digest: str | None
    gold: tuple[GoldObservation, ...]
    expected_abstentions: tuple[ExpectedAbstention, ...]
    claims: tuple[LaneClaim, ...]
    # Whether each claim's evidence resolves, positionally aligned with `claims`. Computed
    # here because it is a scored dimension and the report must not assert one: the JSON used
    # to print `"resolves": true` on every emitted claim, including the eighteen unmatched ones
    # where nothing had computed it *(found by review 2026-08-02)*.
    claim_evidence_resolves: tuple[bool, ...]
    matched: tuple[MatchRecord, ...]
    # Keys under which the lane emitted more than one claim, so the matcher had to choose.
    # Reported wherever a number the choice moved is reported.
    duplicate_claim_keys: tuple[str, ...]
    missed: tuple[tuple[str, str], ...]
    unrequired: tuple[tuple[str, str], ...]
    abstentions: tuple[AbstentionVerdict, ...]
    failures: tuple[Failure, ...]
    attributions: tuple[PeriodAttribution, ...]
    issues_by_code: dict[str, int]
    # The same census split by who decided. Eight codes are in `MODEL_ABSTENTION_REASONS` and
    # can be produced by either side, so the split is read off `NarrativeIssue.rejected_claim`
    # — the flag step 10 added for exactly this measurement — and never inferred from the code.
    rejections_by_code: dict[str, int]
    model_abstentions_by_code: dict[str, int]
    rejected_claims: int
    model_abstentions: int
    scope_concept_ids: tuple[str, ...]
    # What happened to these claims after the lane: §7's policies at assembly, then the
    # ontology's own verdict. V1 §11 criterion 1 is "every emitted claim passes
    # `ontology.validate_claims` with zero errors", and a report that scored the lane without
    # driving its claims that far could not say whether it holds.
    ontology_warnings: tuple[str, ...]
    ontology_errors: tuple[str, ...]
    assembly_rejections: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def expected_abstention_count(self) -> int:
        return len(self.expected_abstentions)

    @property
    def honoured_abstentions(self) -> int:
        return sum(1 for verdict in self.abstentions if verdict.honoured)

    @property
    def model_chosen_abstentions(self) -> int:
        """Non-rejections the model actually chose, less the lane's own decisions."""
        return self.model_abstentions - sum(
            self.model_abstentions_by_code.get(code, 0)
            for code in LANE_DECISIONS_WITHOUT_AN_ANSWER)

    @property
    def code_agreeing_abstentions(self) -> int:
        return sum(1 for verdict in self.abstentions if verdict.code_recorded)

    @property
    def ambiguity_expectations(self) -> tuple[AbstentionVerdict, ...]:
        """The expectations that actually name ambiguity candidates. There is one, in total."""
        return tuple(v for v in self.abstentions if v.ambiguity_candidates)


def score_case(
    *,
    case_id: str,
    scope: str,
    passage_id: str,
    gold: tuple[GoldObservation, ...],
    expected_abstentions: tuple[ExpectedAbstention, ...],
    claims: list[LaneClaim],
    issues: list,
    scope_concept_ids: tuple[str, ...],
    request_digest: str | None,
    validation: dict[str, tuple[str, ...]],
    resolves_evidence,
    reporting_keys: frozenset[str],
    sentence_period_keys,
    ambiguity_codes_for,
) -> CaseScore:
    """One (case, scope) pair, scored on every dimension at once.

    `resolves_evidence`, `sentence_period_keys` and `ambiguity_codes_for` are passed in rather
    than computed: what makes a passage id resolvable, what a sentence states about its own
    period, and what §7.3 attaches to a metric all belong to the caller's corpus and
    vocabulary, exactly as `evaluation.evaluate_case` already argues.
    """
    result = evaluate_case(
        case_id, passage_id, claims, [g.as_gold_claim() for g in gold], issues,
        resolves_evidence=resolves_evidence)

    by_key, duplicate_keys = _index_by_key(claims)
    matched = tuple(
        _match_record(want, by_key[(want.metric_id, want.period_key)], resolves_evidence,
                      ambiguity_codes_for)
        for want in gold if (want.metric_id, want.period_key) in by_key)

    usable = not any(getattr(i, "code", "") in _NO_USABLE_ANSWER for i in issues)
    verdicts = tuple(
        _abstention_verdict(expected, claims=claims, issues=issues, gold=gold, usable=usable)
        for expected in expected_abstentions)

    failures = classify_failures(
        gold=gold, claims=claims, issues=issues, matched=matched, abstentions=verdicts)

    attributions = tuple(
        _attribution(claim, gold=gold, reporting_keys=reporting_keys,
                     sentence_keys=sentence_period_keys(claim.raw_text))
        for claim in sorted(claims, key=lambda c: (c.metric_id, c.period.key)))

    rejected = sum(1 for issue in issues if getattr(issue, "rejected_claim", False))
    ordered_claims = tuple(sorted(claims, key=lambda c: (c.metric_id, c.period.key, c.value)))
    score = CaseScore(
        case_id=case_id,
        scope=scope,
        passage_id=passage_id,
        request_digest=request_digest,
        gold=gold,
        expected_abstentions=expected_abstentions,
        claims=ordered_claims,
        claim_evidence_resolves=tuple(
            bool(resolves_evidence(claim)) for claim in ordered_claims),
        matched=matched,
        duplicate_claim_keys=duplicate_keys,
        missed=_keys(result.missing),
        unrequired=_keys(result.unlisted),
        abstentions=verdicts,
        failures=failures,
        attributions=attributions,
        issues_by_code=dict(sorted(result.issues.items())),
        rejections_by_code=_census(issues, rejected_claim=True),
        model_abstentions_by_code=_census(issues, rejected_claim=False),
        rejected_claims=rejected,
        model_abstentions=len(issues) - rejected,
        scope_concept_ids=tuple(scope_concept_ids),
        ontology_warnings=tuple(validation.get("warnings", ())),
        ontology_errors=tuple(validation.get("errors", ())),
        assembly_rejections=tuple(validation.get("assembly_rejections", ())),
    )
    score.counts = {
        "gold": len(gold),
        "emitted": len(claims),
        "matched": len(matched),
        "missed": len(score.missed),
        "unrequired": len(score.unrequired),
        "expected_abstentions": len(expected_abstentions),
        "honoured_abstentions": score.honoured_abstentions,
        "model_chosen_abstentions": score.model_chosen_abstentions,
        "code_agreeing_abstentions": score.code_agreeing_abstentions,
        "ambiguity_expectations": len(score.ambiguity_expectations),
        "duplicate_claim_keys": len(duplicate_keys),
        "rejected_claims": rejected,
        "model_abstentions": score.model_abstentions,
        "failures": len(failures),
        "ontology_warnings": len(score.ontology_warnings),
        "ontology_errors": len(score.ontology_errors),
        "assembly_rejections": len(score.assembly_rejections),
        "scope_concepts": len(scope_concept_ids),
    }
    score.scores = scores([score])
    return score


def fractions(cases: list[CaseScore]) -> dict[str, tuple[int, int]]:
    """Every score as `(hits, denominator)`. **The only place any of them is counted.**

    Returned as fractions rather than ratios so the report can print the denominator beside
    the number. A dimension whose denominator is 1 and a dimension whose denominator is 11 read
    identically at three decimal places, and one of them is evidence.

    `matched_over_emitted` is **not precision** and is never named one: each case annotates a
    deliberate subset of its passage's claims, so an unmatched claim is usually a right answer
    the case did not list. It is reported for run-to-run movement (§4.0, STAGE_11 §2).

    `matched_over_distinct_emitted` is the same ratio with the double counting taken out. Two
    reviewed cases annotate **one passage**, so that passage's claims are counted once for each
    of them; the distinct form counts each passage's claims once *(added 2026-08-02, review)*.
    """
    matched = sum(len(c.matched) for c in cases)
    expected = sum(c.expected_abstention_count for c in cases)
    ambiguity_expectations = [v for c in cases for v in c.ambiguity_expectations]

    counted: dict[str, tuple[int, int]] = {
        "metric_identity_accuracy": (matched, sum(len(c.gold) for c in cases)),
        "matched_over_emitted": (matched, sum(len(c.claims) for c in cases)),
        "matched_over_distinct_emitted": (
            matched, sum(len(c.claims) for c in _one_case_per_passage(cases))),
        "abstention_honoured_rate": (sum(c.honoured_abstentions for c in cases), expected),
        "abstention_code_agreement": (
            sum(c.code_agreeing_abstentions for c in cases), expected),
        "ambiguity_preserved_rate": (
            sum(1 for v in ambiguity_expectations if v.honoured), len(ambiguity_expectations)),
    }
    for dimension in SCORED_MATCH_DIMENSIONS:
        applicable = [m for c in cases for m in c.matched if m.applies(dimension)]
        counted[f"{dimension}_accuracy"] = (
            sum(1 for m in applicable if getattr(m, f"{dimension}_ok")), len(applicable))
    return dict(sorted(counted.items()))


def scores(cases: list[CaseScore], digits: int = 6) -> dict[str, float]:
    """`fractions` as ratios. Nothing else in this repository divides one of these."""
    return {name: _ratio(hits, total, digits)
            for name, (hits, total) in fractions(cases).items()}


def totals(cases: list[CaseScore], digits: int = 6) -> dict[str, Any]:
    """Everything the headline table shows, derived from the per-case scores and nothing else."""
    issues_by_code: dict[str, int] = {}
    rejections_by_code: dict[str, int] = {}
    model_abstentions_by_code: dict[str, int] = {}
    failures_by_category: dict[str, int] = {category: 0 for category in FAILURE_CATEGORIES}
    for case in cases:
        for code, count in case.issues_by_code.items():
            issues_by_code[code] = issues_by_code.get(code, 0) + count
        for code, count in case.rejections_by_code.items():
            rejections_by_code[code] = rejections_by_code.get(code, 0) + count
        for code, count in case.model_abstentions_by_code.items():
            model_abstentions_by_code[code] = model_abstentions_by_code.get(code, 0) + count
        for failure in case.failures:
            failures_by_category[failure.category] += 1
    attributions = [a for case in cases for a in case.attributions]
    # Two reviewed cases annotate one passage, so every claim, warning and error on it enters
    # the per-case totals twice. The distinct forms count each passage once, and the two are
    # reported side by side wherever the difference reaches a denominator.
    distinct = _one_case_per_passage(cases)
    return {
        "cases": len(cases),
        "distinct_passages": len(distinct),
        "gold_observations": sum(len(c.gold) for c in cases),
        "emitted_observations": sum(len(c.claims) for c in cases),
        "emitted_observations_distinct": sum(len(c.claims) for c in distinct),
        "matched_observations": sum(len(c.matched) for c in cases),
        "missed_observations": sum(len(c.missed) for c in cases),
        "unrequired_observations": sum(len(c.unrequired) for c in cases),
        "duplicate_claim_keys": sum(len(c.duplicate_claim_keys) for c in cases),
        # Matched pairs whose assembled claim carries an ambiguity code the case does not
        # declare. Not scored against the lane: it is a disagreement between the reviewed case
        # and the ontology, and changing a case is a founder decision.
        "matched_carrying_undeclared_ambiguity_codes": sum(
            1 for c in cases for m in c.matched if m.carries_undeclared_codes),
        "expected_abstentions": sum(c.expected_abstention_count for c in cases),
        "honoured_abstentions": sum(c.honoured_abstentions for c in cases),
        "code_agreeing_abstentions": sum(c.code_agreeing_abstentions for c in cases),
        "ambiguity_expectations": sum(len(c.ambiguity_expectations) for c in cases),
        "rejected_claims": sum(c.rejected_claims for c in cases),
        "model_abstentions": sum(c.model_abstentions for c in cases),
        "model_chosen_abstentions": sum(c.model_chosen_abstentions for c in cases),
        "ontology_warnings": sum(len(c.ontology_warnings) for c in cases),
        "ontology_warnings_distinct": sum(len(c.ontology_warnings) for c in distinct),
        "ontology_errors": sum(len(c.ontology_errors) for c in cases),
        "ontology_errors_distinct": sum(len(c.ontology_errors) for c in distinct),
        "assembly_rejections": sum(len(c.assembly_rejections) for c in cases),
        "assembly_rejections_distinct": sum(len(c.assembly_rejections) for c in distinct),
        "failures": sum(len(c.failures) for c in cases),
        "failures_by_category": failures_by_category,
        "issues_by_code": dict(sorted(issues_by_code.items())),
        "rejections_by_code": dict(sorted(rejections_by_code.items())),
        "model_abstentions_by_code": dict(sorted(model_abstentions_by_code.items())),
        "period_attribution": {
            "claims": len(attributions),
            "label_in_evidence": sum(1 for a in attributions if a.in_evidence),
            "chose_comparative": sum(1 for a in attributions if a.chose_comparative),
            "sentence_states_its_own_period": sum(
                1 for a in attributions if a.sentence_states_its_own_period),
            "max_distance_from_evidence": max(
                (abs(a.distance) for a in attributions if a.distance is not None), default=0),
        },
        "scores": scores(cases, digits),
        # The denominator of every score above, so a reader can see which numbers rest on one
        # observation. `ambiguity_preserved_rate` rests on one.
        "score_denominators": {name: total for name, (_, total) in fractions(cases).items()},
        "score_numerators": {name: hits for name, (hits, _) in fractions(cases).items()},
    }


# -- the failure classifier -------------------------------------------------------------------


def classify_failures(
    *, gold, claims, issues, matched, abstentions
) -> tuple[Failure, ...]:
    """Total and disjoint over the disagreements this benchmark can actually adjudicate.

    **What is classified, and the one place this deliberately departs from STAGE_11 §4.**
    The brief says "every gold claim not matched, and *every emitted claim not in gold*"
    lands in a category. The second half cannot be right and is not implemented: §2 of the
    same brief states that an unmatched emitted claim is **unrequired**, never a false
    positive, because each case annotates a deliberate subset of its passage. Putting all of
    them into failure categories would make `matched_over_emitted` a precision under another
    name — the one thing both §2 and §4.0 refuse. So the classified set is:

    1. every gold observation the lane did not emit;
    2. every matched observation failing at least one of the six dimensions;
    3. every expected abstention that was not honoured — one failure per concept it named
       that was claimed anyway, or one for the expectation as a whole when it named none.

    Everything else the lane emitted is counted as `unrequired` and left unclassified, which
    is what the benchmark's own annotation policy permits it to say.

    One category per failure, decided in `DECISION_ORDER`, first match wins.
    """
    unusable = any(getattr(i, "code", "") in _NO_USABLE_ANSWER for i in issues)
    matched_keys = {(m.metric_id, m.period_key) for m in matched}
    failures: list[Failure] = []

    for want in gold:
        if (want.metric_id, want.period_key) in matched_keys:
            continue
        failures.append(_classify_miss(want, claims=claims, issues=issues, unusable=unusable))

    for match in matched:
        if match.all_ok:
            continue
        failures.append(Failure(
            category=_first_in_order(
                {_DIMENSION_CATEGORY[d] for d in match.failed_dimensions}),
            source=WRONG_MATCH,
            metric_id=match.metric_id,
            period_key=match.period_key,
            detail="matched observation failed " + ", ".join(match.failed_dimensions),
        ))

    for verdict in abstentions:
        if verdict.honoured:
            continue
        for concept_id in verdict.claimed_concept_ids:
            failures.append(Failure(
                category=(AMBIGUITY_COLLAPSED if verdict.ambiguity_candidates
                          else _over_emission_category(verdict)),
                source=OVER_EMITTED,
                metric_id=concept_id,
                period_key=None,
                detail=f"the case requires {verdict.reason} over {list(verdict.concept_ids)} "
                       f"and the lane emitted a claim for {concept_id}",
            ))
        if not verdict.claimed_concept_ids:
            # A required silence that was not kept and names no concept to hang the failure
            # on. Three shapes, in `DECISION_ORDER`: the answer was unusable, so there was no
            # abstention to have; the case annotates nothing and the lane emitted something
            # anyway; or the lane was silent where a *stated* silence was required, which
            # `ClaimLane`'s contract already calls a different answer from an abstention.
            failures.append(Failure(
                category=(SCHEMA_OR_PARSE_FAILURE if not verdict.answer_usable
                          else METRIC_MISIDENTIFIED if not verdict.silence_kept
                          else METRIC_NOT_IDENTIFIED),
                source=OVER_EMITTED,
                metric_id=EM_DASH_METRIC,
                period_key=None,
                detail=f"the case requires {verdict.reason} and the lane "
                       + ("produced an answer it could not use"
                          if not verdict.answer_usable
                          else "emitted claims on a case that annotates none"
                          if not verdict.silence_kept
                          else "recorded no issue at all: a bare silence, not an abstention"),
            ))

    return tuple(sorted(
        failures,
        key=lambda f: (DECISION_ORDER.index(f.category), f.metric_id, f.period_key or "")))


_DIMENSION_CATEGORY: dict[str, str] = {
    "evidence": EVIDENCE_UNGROUNDED,
    "subject": SUBJECT_WRONG,
    "unit": UNIT_WRONG,
    "scale": SCALE_WRONG,
    "value": VALUE_WRONG,
    # Unreachable while matching is on (metric_id, period key), and mapped rather than omitted
    # so a future change to the matching rule fails loudly instead of raising a KeyError.
    "period": PERIOD_WRONG,
    # §4 fixes ten categories and these two dimensions arrived after it, so they are mapped
    # into the existing ten rather than given an eleventh. A claim whose declared ambiguity
    # codes are missing has resolved a declared-ambiguous reading to a single one, which is
    # what `ambiguity_collapsed` names. A claim carrying a different denominator wording is,
    # under §7.1, a claim about a different series for the same printed figure — which is what
    # `metric_misidentified` names, and it is the category `MISSING_POPULATION_DEFINITION`'s
    # neighbouring refusal already lands in.
    "ambiguity_codes": AMBIGUITY_COLLAPSED,
    "population": METRIC_MISIDENTIFIED,
}


def _classify_miss(want: GoldObservation, *, claims, issues, unusable: bool) -> Failure:
    """A gold observation the lane never emitted, through `DECISION_ORDER`."""
    named = [i for i in issues if want.metric_id in tuple(getattr(i, "metric_ids", ()) or ())]
    codes = {getattr(i, "code", "") for i in named}
    same_metric = [c for c in claims if c.metric_id == want.metric_id]
    same_figure = [c for c in claims
                   if c.metric_id != want.metric_id and _close(c.value, want.value)]

    if unusable:
        return _miss(want, SCHEMA_OR_PARSE_FAILURE,
                     "the answer for this passage did not parse or did not conform, so no "
                     "finer classification is available")
    if EVIDENCE_UNGROUNDED in {_CODE_CATEGORY.get(code) for code in codes}:
        return _miss(want, EVIDENCE_UNGROUNDED,
                     f"the lane refused this metric over its quotation: {sorted(codes)}")
    if same_figure:
        return _miss(want, METRIC_MISIDENTIFIED,
                     f"{want.value} was claimed as "
                     f"{sorted({c.metric_id for c in same_figure})} instead")
    if same_metric:
        return _miss(want, PERIOD_WRONG,
                     f"claimed for {sorted({c.period.key for c in same_metric})}, "
                     f"not {want.period_key}")
    for code in sorted(codes):
        category = _CODE_CATEGORY.get(code)
        if category is not None:
            return _miss(want, category, f"the lane recorded {code} for this metric")
    return _miss(want, METRIC_NOT_IDENTIFIED,
                 "the gold metric was neither claimed nor abstained over"
                 if not named else
                 f"abstained over with {sorted(codes)}, and no claim was emitted")


def _miss(want: GoldObservation, category: str, detail: str) -> Failure:
    return Failure(category=category, source=MISSED_GOLD, metric_id=want.metric_id,
                   period_key=want.period_key, detail=detail)


def _over_emission_category(verdict: AbstentionVerdict) -> str:
    """A claim for a concept a non-ambiguity expectation names.

    `NOT_THE_SUBJECT_COMPANY` is the one reason with its own dimension; every other expectation
    that names concepts says the passage carries no observation of them here, which is a metric
    attached to a figure that is not one.
    """
    if verdict.reason == "NOT_THE_SUBJECT_COMPANY":
        return SUBJECT_WRONG
    return METRIC_MISIDENTIFIED


def _first_in_order(categories: set[str]) -> str:
    return next(category for category in DECISION_ORDER if category in categories)


# -- the pieces ---------------------------------------------------------------------------------


def _index_by_key(claims) -> tuple[dict[tuple[str, str], LaneClaim], tuple[str, ...]]:
    """One claim per `(metric_id, period key)`, chosen explicitly, plus the keys that collided.

    **The rule is first in emission order, and the reason is that the alternatives are worse.**
    A dict comprehension over the claim list — which is what this was until 2026-08-02 — keeps
    the *last* one silently, and on `letter-prose-multiple-metrics-q4-2021` that displaced the
    $152 million `contribution_profit` that equals gold with the $525 million full-year figure
    the model dated to the same quarter. The only `value_wrong` in the report was therefore a
    property of dict ordering.

    Picking the claim that agrees with gold would fit the matcher to the answer sheet and make
    `value_accuracy` unable to fall. Emission order is the order the model reported the
    passage's figures in and knows nothing about gold, so it is the choice this makes — and
    every key where a choice was made is recorded and disclosed beside the numbers the choice
    moves. The underlying duplicate is itself a finding: two values under one deterministic
    observation id is what `DUPLICATE_OBSERVATION_CONFLICT` reports at the other end.
    """
    indexed: dict[tuple[str, str], LaneClaim] = {}
    collided: set[str] = set()
    for claim in claims:
        key = (claim.metric_id, claim.period.key)
        if key in indexed:
            collided.add(f"{key[0]}@{key[1]}")
            continue
        indexed[key] = claim
    return indexed, tuple(sorted(collided))


def _match_record(
    want: GoldObservation, claim: LaneClaim, resolves_evidence, ambiguity_codes_for
) -> MatchRecord:
    # §7.3 attaches these at assembly (`core.assembly.to_claim`), not in the lane, so the claim
    # the report scores carries none of them. Scoring the pre-assembly tuple would measure the
    # layering rather than the policy, so what is compared is what the policy attaches — and
    # what the lane itself carried is recorded beside it.
    emitted_codes = tuple(sorted(
        set(claim.ambiguity_codes) | set(ambiguity_codes_for(claim.metric_id))))
    expected_codes = tuple(sorted(want.ambiguity_codes))
    expected_population = _collapse(want.population_definition_raw)
    emitted_population = _collapse(claim.population_definition_raw)
    return MatchRecord(
        metric_id=want.metric_id,
        period_key=want.period_key,
        expected_value=want.value,
        emitted_value=claim.value,
        expected_unit=want.unit,
        emitted_unit=claim.unit,
        expected_scale=want.scale_applied,
        emitted_scale=claim.scale.scale if claim.scale else "units",
        expected_subject_entity_id=want.subject_entity_id,
        emitted_subject_entity_id=claim.subject_entity_id,
        expected_population=want.population_definition_raw,
        emitted_population=claim.population_definition_raw,
        expected_ambiguity_codes=expected_codes,
        emitted_ambiguity_codes=emitted_codes,
        lane_claim_ambiguity_codes=tuple(claim.ambiguity_codes),
        raw_text=claim.raw_text,
        # §7.1 says *verbatim*, so the comparison is equality and not containment. Whitespace
        # is collapsed on both sides because a filed wording broken across a line is the same
        # wording; nothing else is normalised, because normalising a denominator is the exact
        # move §7.1 exists to forbid.
        population_ok=expected_population == emitted_population,
        ambiguity_codes_ok=expected_codes == emitted_codes,
        population_contained_in_expected=bool(
            expected_population and emitted_population
            and emitted_population in expected_population),
        **compare(want.as_gold_claim(), claim, evidence_resolves=resolves_evidence(claim)),
    )


def _one_case_per_passage(cases: list[CaseScore]) -> list[CaseScore]:
    """One case per distinct passage, first by case id.

    Two reviewed cases annotate `q42021formxex992sharehol.htm#p10`, so the lane runs on it
    once and every claim, warning and error it produced is counted under both. Gold and
    matched observations are per case and are not affected; anything counted per *claim* is.
    """
    seen: dict[str, CaseScore] = {}
    for case in sorted(cases, key=lambda c: c.case_id):
        seen.setdefault(case.passage_id, case)
    return list(seen.values())


def _collapse(text: str | None) -> str | None:
    return None if text is None else " ".join(str(text).split())


def _abstention_verdict(
    expected: ExpectedAbstention, *, claims, issues, gold, usable: bool
) -> AbstentionVerdict:
    """Four conditions, and the fourth is the one a run had to teach this function.

    `silence_kept` is only a real condition where the case annotates no gold claim at all: on a
    case that also annotates observations, "the lane emitted claims" is what it was supposed to
    do, and the expectation is about the named concepts alone.
    """
    claimed_ids = {c.metric_id for c in claims}
    broken = tuple(sorted(claimed_ids & set(expected.concept_ids)))
    silence_kept = bool(gold) or not claims
    any_issue_recorded = bool(issues)
    code_recorded = any(getattr(i, "code", "") == expected.reason for i in issues)
    return AbstentionVerdict(
        reason=expected.reason,
        concept_ids=expected.concept_ids,
        ambiguity_candidates=expected.ambiguity_candidates,
        claimed_concept_ids=broken,
        silence_kept=silence_kept,
        any_issue_recorded=any_issue_recorded,
        answer_usable=usable,
        code_recorded=code_recorded,
        honoured=not broken and silence_kept and any_issue_recorded and usable,
    )


def _attribution(claim: LaneClaim, *, gold, reporting_keys, sentence_keys) -> PeriodAttribution:
    metadata = claim.extractor_metadata or {}
    gold_keys = {(g.metric_id, g.period_key) for g in gold}
    return PeriodAttribution(
        metric_id=claim.metric_id,
        period_key=claim.period.key,
        period_label=str(metadata.get("period_label") or ""),
        in_evidence=metadata.get("period_label_in_evidence"),
        distance=metadata.get("period_label_distance_from_evidence"),
        char_start=metadata.get("period_label_char_start"),
        chose_comparative=claim.period.key not in reporting_keys,
        sentence_states_its_own_period=bool(sentence_keys),
        reporting_period_keys=tuple(sorted(reporting_keys)),
        matched_gold=(claim.metric_id, claim.period.key) in gold_keys,
    )


def _census(issues, *, rejected_claim: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        if bool(getattr(issue, "rejected_claim", False)) is rejected_claim:
            code = getattr(issue, "code", "")
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _keys(entries) -> tuple[tuple[str, str], ...]:
    """`evaluation.py` reports these as `metric@period` strings; split them back apart."""
    parsed = []
    for entry in entries:
        metric_id, _, period_key = entry.partition("@")
        parsed.append((metric_id, period_key))
    return tuple(sorted(parsed))


def _close(a: float, b: float) -> bool:
    if a == b:
        return True
    return abs(float(a) - float(b)) <= max(abs(float(b)) * 1e-9, 1e-9)


def _ratio(hit: int, total: int, digits: int) -> float:
    """1.0 for an empty denominator: nothing was asked for and nothing was missed."""
    return 1.0 if total == 0 else round(hit / total, digits)
