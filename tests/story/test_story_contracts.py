"""The contract types and the five protocols, driven rather than inspected.

**No test here asserts `isinstance(stub, SomeProtocol)` and calls that a check.** A
`runtime_checkable` `Protocol` compares attribute *names* and nothing else — not signatures,
not return types — so such an assertion passes for an object whose `generate` takes no
arguments and returns `None`. Every protocol below is therefore exercised by building the
smallest conforming implementation and *calling* it through a function annotated with the
protocol, which is the only thing that proves the surface is usable.

The rest of the file covers the two properties S0 exists to freeze: every type survives a
JSON round trip (§14 writes all of them to `data/story_runs/`), and the invariants that make
an inconsistent artifact unrepresentable — a citation that is neither a passage nor an
evidence source, a rejection with nothing blocking, a `passed` that could disagree with the
findings beneath it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pydantic
import pytest

from story.contracts import (
    DraftVerifier,
    EvidenceBuilder,
    GraphRetriever,
    ReadQueryExecutor,
    StoryDetector,
    StoryGenerationProvider,
)
from story.core.models import (
    CITATION_ADAPTER,
    Audience,
    CheckOutcome,
    CheckResult,
    Counterpoint,
    Draft,
    EditorialPlan,
    EvidenceRequest,
    EvidenceSourceCitation,
    FactBinding,
    GenerationResult,
    HealthStatus,
    PackageIdentity,
    PassageCitation,
    RejectedDraft,
    Remedy,
    RetrievalOutcome,
    RetrievalResult,
    RetrievalTraceEntry,
    Severity,
    StoryCandidate,
    StoryEvidencePackage,
    VerificationFinding,
    VerifiedDraft,
)

from conftest import make_candidate, make_draft, make_package, make_plan


# ---------------------------------------------------------------------------------------
# Protocols, driven
# ---------------------------------------------------------------------------------------


class OneMoveDetector:
    """The smallest thing that is a `StoryDetector`."""

    detector_id = "detector:metric_move"
    version = "1.0.0"

    def detect(self, series: object) -> Sequence[StoryCandidate]:
        return (make_candidate(signals={"points": len(list(series))}),)


class CountingRetriever:
    """The smallest thing that is a `GraphRetriever`, and it touches no database."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        self.calls.append((tool, dict(parameters)))
        if tool == "get_fact_evidence":
            return RetrievalResult(outcome=RetrievalOutcome.OK,
                                   rows=({"observation_id": parameters["observation_id"]},))
        return RetrievalResult(outcome=RetrievalOutcome.UNAVAILABLE,
                               reason="evidence_kind_not_supported_in_v1")

    def trace(self) -> Sequence[RetrievalTraceEntry]:
        return tuple(
            RetrievalTraceEntry(tool=tool, parameters=parameters, row_count=1,
                                truncated=False, elapsed_ms=0.4)
            for tool, parameters in self.calls)


class RecordingExecutor:
    """A `ReadQueryExecutor` with a dict where the database would be.

    That it can exist at all is the point of the protocol: no story stage needs a driver
    installed to be tested, and nothing vendor-shaped can reach one because the surface only
    hands back plain dicts.
    """

    def __init__(self) -> None:
        self.statements: list[tuple[str, Mapping[str, Any], float]] = []
        self.closed = False

    def read(self, statement: str, parameters: Mapping[str, Any], *,
             timeout_seconds: float) -> tuple[dict[str, Any], ...]:
        self.statements.append((statement, dict(parameters), timeout_seconds))
        return ({"metric_id": parameters["metric_id"], "value": -211_000_000.0},)

    def verify_connectivity(self) -> HealthStatus:
        return HealthStatus(ok=True, status="reachable", detail="bolt://127.0.0.1:7687")

    def close(self) -> None:
        self.closed = True


class PassThroughBuilder:
    def build(self, candidate: StoryCandidate,
              request: EvidenceRequest) -> StoryEvidencePackage:
        return make_package(candidate_id=candidate.candidate_id)


class EchoProvider:
    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        self.seen.append({"system": system, "prompt": prompt, "schema_name": schema_name,
                          "max_tokens": max_tokens, "temperature": temperature})
        return GenerationResult(
            content={"thesis": prompt}, raw_content="{}", model_id="qwen3.5-9b",
            prompt_tokens=1, completion_tokens=1, total_tokens=2, latency_ms=1.0,
            raw_sha256="a" * 64, content_sha256="b" * 64, finish_reason="stop", attempts=1)

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")


class AlwaysCleanVerifier:
    def verify(self, draft: Draft, package: StoryEvidencePackage,
               plan: EditorialPlan) -> VerifiedDraft:
        return VerifiedDraft(
            candidate_id=draft.candidate_id,
            package_identity=package.identity,
            draft_content_sha256="c" * 64,
            checks=(CheckResult(name="numbers", examined=len(draft.sentences)),))


def run_detector(detector: StoryDetector, series: object) -> Sequence[StoryCandidate]:
    """Annotated with the protocol, so the call site is the surface under test."""
    return detector.detect(series)


def fetch_evidence(retriever: GraphRetriever, observation_id: str) -> RetrievalResult:
    return retriever.call("get_fact_evidence", {"observation_id": observation_id})


def build_package(builder: EvidenceBuilder, candidate: StoryCandidate) -> StoryEvidencePackage:
    return builder.build(candidate, candidate.evidence_request)


def plan_a_story(provider: StoryGenerationProvider, prompt: str) -> GenerationResult:
    return provider.generate(system="You are an editor.", prompt=prompt, schema={},
                             schema_name="editorial_plan", max_tokens=1024, temperature=0.0)


def verify_a_draft(verifier: DraftVerifier, draft: Draft,
                   package: StoryEvidencePackage, plan: EditorialPlan) -> VerifiedDraft:
    return verifier.verify(draft, package, plan)


def test_a_detector_driven_through_its_protocol_returns_candidates():
    candidates = run_detector(OneMoveDetector(), [1, 2, 3])
    assert [c.detector_id for c in candidates] == ["detector:metric_move"]
    assert candidates[0].signals["points"] == 3


def test_a_retriever_driven_through_its_protocol_answers_and_records_what_it_did():
    retriever = CountingRetriever()
    result = fetch_evidence(retriever, "obs:adjusted-ebitda:b")
    assert result.outcome is RetrievalOutcome.OK
    assert result.rows[0]["observation_id"] == "obs:adjusted-ebitda:b"
    assert [entry.tool for entry in retriever.trace()] == ["get_fact_evidence"]


def test_a_retriever_reports_unavailable_rather_than_raising_for_an_unsupported_kind():
    """§13.7.2: until a lane emits an `:EvidenceSource`, the answer is `Unavailable(reason)` —
    an unimplemented rule that silently passes is worse than one that refuses."""
    result = CountingRetriever().call("get_evidence_source", {"evidence_source_id": "es:1"})
    assert result.outcome is RetrievalOutcome.UNAVAILABLE
    assert result.reason == "evidence_kind_not_supported_in_v1"
    assert result.rows == ()


READ_ONE_METRIC = "MATCH (m:Metric {metric_id: $metric_id}) RETURN m.metric_id AS metric_id"


def read_rows(executor: ReadQueryExecutor, metric_id: str) -> tuple[dict[str, Any], ...]:
    """Annotated with the protocol. Note there is nowhere to put a value except `parameters`."""
    return executor.read(READ_ONE_METRIC, {"metric_id": metric_id}, timeout_seconds=5.0)


def test_a_read_executor_driven_through_its_protocol_returns_plain_dicts():
    """No `neo4j.Record`, no `neo4j.Node`, nothing vendor-shaped reaches a story stage — the
    rule `extraction/providers/public.py` states for `GenerationResult`."""
    executor = RecordingExecutor()
    rows = read_rows(executor, "adjusted_ebitda")
    assert rows == ({"metric_id": "adjusted_ebitda", "value": -211_000_000.0},)
    assert all(type(row) is dict for row in rows)


def test_a_read_executor_is_handed_a_literal_statement_and_a_separate_parameter_map():
    """The statement the executor saw is the constant, character for character: a caller that
    wanted to interpolate would have to change the statement itself, which is what S1's
    structural scan refuses."""
    executor = RecordingExecutor()
    read_rows(executor, "adjusted_ebitda")
    statement, parameters, _ = executor.statements[0]
    assert statement == READ_ONE_METRIC
    assert parameters == {"metric_id": "adjusted_ebitda"}
    assert "adjusted_ebitda" not in statement


def test_every_read_carries_an_explicit_timeout():
    """§16 requires one on every statement, and the protocol gives `timeout_seconds` no
    default — a call site cannot omit it and still typecheck or run."""
    import inspect

    parameter = inspect.signature(RecordingExecutor.read).parameters["timeout_seconds"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty

    executor = RecordingExecutor()
    read_rows(executor, "adjusted_ebitda")
    assert executor.statements[0][2] == 5.0

    with pytest.raises(TypeError):
        executor.read(READ_ONE_METRIC, {"metric_id": "x"})


def test_a_read_executor_reports_connectivity_as_a_value_and_can_be_closed():
    """A database that is not running is an ordinary state for the freshness gate to branch
    on, not an exception for it to catch."""
    executor = RecordingExecutor()
    health = executor.verify_connectivity()
    assert isinstance(health, HealthStatus) and health.ok is True
    executor.close()
    assert executor.closed is True


def test_a_builder_driven_through_its_protocol_returns_a_package_for_its_candidate():
    candidate = make_candidate()
    package = build_package(PassThroughBuilder(), candidate)
    assert package.candidate_id == candidate.candidate_id


def test_a_provider_driven_through_its_protocol_receives_a_separate_system_prompt():
    """§15.1's one required extension over the extraction provider: a planner, a writer and a
    verifier are three personas, and folding them into `prompt` hides the structure inside the
    request digest the replay store is keyed by."""
    provider = EchoProvider()
    result = plan_a_story(provider, "Adjusted EBITDA crossed zero.")
    assert result.content["thesis"] == "Adjusted EBITDA crossed zero."
    assert provider.seen[0]["system"] == "You are an editor."
    assert provider.seen[0]["schema_name"] == "editorial_plan", (
        "§15.1: schema_name must reach the wire, not be hard-coded at the call site")
    assert provider.seen[0]["temperature"] == 0.0


def test_a_provider_reports_health_as_a_value_rather_than_raising():
    assert plan_a_story(EchoProvider(), "x") is not None
    assert EchoProvider().health().ok is True


def test_a_verifier_driven_through_its_protocol_returns_a_decision():
    package = make_package()
    verified = verify_a_draft(AlwaysCleanVerifier(), make_draft(), package, make_plan())
    assert verified.passed is True
    assert verified.package_identity == package.identity


def test_the_provider_result_carries_the_same_fields_extraction_records():
    """§15.2: `story` restates `GenerationResult` rather than importing
    `extraction.contracts`, which is not a shared surface. This is the repo-native way to stop
    the two drifting — a test, not an import."""
    from dataclasses import fields

    from extraction.contracts import GenerationResult as UpstreamGenerationResult

    assert set(GenerationResult.model_fields) == {
        f.name for f in fields(UpstreamGenerationResult)}


# ---------------------------------------------------------------------------------------
# Serialization round trips
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [make_candidate(), make_package(), make_plan(), make_draft()],
    ids=["candidate", "package", "plan", "draft"])
def test_a_contract_type_survives_a_json_round_trip_unchanged(value):
    """§14 writes every one of these to `data/story_runs/` and reads them back."""
    restored = type(value).model_validate_json(value.model_dump_json())
    assert restored == value


def test_a_package_round_trips_through_its_canonical_json_byte_for_byte():
    """§10.3's reproducibility claim: rebuilding a package from one graph run must produce the
    same digest, so the encoding has to be stable before anything else can be."""
    from story.core.keys import package_content_digest

    package = make_package()
    stamped = package.with_content_digest(package_content_digest(package.digestible_payload()))
    restored = StoryEvidencePackage.model_validate_json(stamped.model_dump_json())

    assert restored == stamped
    assert package_content_digest(restored.digestible_payload()) == \
        stamped.package_content_digest


def test_the_content_digest_does_not_cover_the_field_that_holds_it():
    """Otherwise stamping the package would invalidate the value just stamped."""
    package = make_package()
    assert package.digestible_payload() == \
        package.with_content_digest("f" * 64).digestible_payload()


def test_the_package_identity_block_is_derived_from_the_package_and_not_a_second_copy():
    package = make_package()
    identity = package.identity
    assert isinstance(identity, PackageIdentity)
    assert identity.graph_run_id == package.graph_run_id
    assert set(PackageIdentity.model_fields) <= set(StoryEvidencePackage.model_fields)


# ---------------------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------------------


def test_a_candidate_may_not_carry_a_thesis():
    """§6.4's deliberate omission, enforced by `extra="forbid"` rather than by a comment: a
    free-text hypothesis on a candidate is the seam through which a detector's guess becomes a
    post's claim."""
    with pytest.raises(pydantic.ValidationError):
        make_candidate(thesis_hypothesis="Margins collapsed because of rates.")


def test_a_candidate_may_not_carry_its_own_score():
    """§6.4 again: ranking is computed over the whole candidate set (§6.10), and a candidate
    holding a score would let a detector rank itself."""
    for field in ("materiality", "novelty", "evidence_quality", "score", "rank"):
        with pytest.raises(pydantic.ValidationError):
            make_candidate(**{field: 1.0})


def test_a_candidate_refuses_unsorted_metric_ids():
    """§6.4 declares these sorted; this is what makes the declaration true rather than a
    comment. `candidate_id` sorts its own digest inputs, so the id is safe either way — the
    row written to `candidates.jsonl` is what would otherwise be two byte sequences for one
    candidate."""
    with pytest.raises(pydantic.ValidationError, match="must be sorted"):
        make_candidate(metric_ids=("gaap_gross_margin", "adjusted_gross_margin"))


def test_a_candidate_refuses_a_repeated_anchor_observation():
    with pytest.raises(pydantic.ValidationError, match="repeat"):
        make_candidate(anchor_observation_ids=("obs:a", "obs:a"))


def test_a_candidate_is_frozen():
    candidate = make_candidate()
    with pytest.raises(pydantic.ValidationError):
        candidate.story_type = "something_else"


def test_an_internal_candidate_says_so_in_a_closed_vocabulary():
    """§6.5: `fact_conflict` and `coverage_gap` ship as `audience: internal`, and §6.10 forbids
    an internal candidate outranking an external one for post generation."""
    assert make_candidate(audience="internal").audience is Audience.INTERNAL
    with pytest.raises(pydantic.ValidationError):
        make_candidate(audience="everyone")


def test_a_citation_is_either_a_passage_or_an_evidence_source_and_never_neither():
    """§13.7.2 makes these two different kinds of support — span containment for a filed
    passage, coordinate reconstruction for an `:EvidenceSource`. Modelled as a discriminated
    union so "neither" is not a value that can exist and have to be caught downstream."""
    passage = CITATION_ADAPTER.validate_python(
        {"kind": "passage", "passage_id": "psg:1", "document_id": "doc:1",
         "char_start": 0, "char_end": 12, "evidence_handle": "ev:psg:1:r0c1"})
    assert isinstance(passage, PassageCitation)

    source = CITATION_ADAPTER.validate_python(
        {"kind": "evidence_source", "evidence_source_id": "es:1", "evidence_kind": "xbrl_fact"})
    assert isinstance(source, EvidenceSourceCitation)

    with pytest.raises(pydantic.ValidationError):
        CITATION_ADAPTER.validate_python({"kind": "passage", "evidence_source_id": "es:1"})
    with pytest.raises(pydantic.ValidationError):
        CITATION_ADAPTER.validate_python({"char_start": 0, "char_end": 12})
    with pytest.raises(pydantic.ValidationError):
        CITATION_ADAPTER.validate_python({})


def test_a_passage_citation_refuses_a_span_that_ends_before_it_starts():
    with pytest.raises(pydantic.ValidationError):
        PassageCitation(passage_id="psg:1", document_id="doc:1", char_start=9, char_end=4,
                        evidence_handle="ev:psg:1:r0c1")


def test_a_passage_citation_refuses_to_be_built_without_an_evidence_handle():
    """Required with no default, for `PackagedPassage.role`'s reason: every default was a claim
    about evidence. `None` meant *"no package minted a handle for these bytes"*, and stating it
    turned §13.7's whole §3.4 family off with nothing weaker underneath on a table fact."""
    with pytest.raises(pydantic.ValidationError):
        PassageCitation(passage_id="psg:1", document_id="doc:1",  # type: ignore[call-arg]
                        char_start=0, char_end=12)


def test_a_fact_binding_refuses_a_span_that_ends_before_it_starts():
    with pytest.raises(pydantic.ValidationError):
        FactBinding(fact_id="obs:a", rendered="x", char_start=5, char_end=5,
                    metric_surface="m", period_surface="p")


def test_a_packaged_fact_must_name_a_passage_or_an_evidence_source():
    """§13.7's rules all begin "the cited span must exist", and Rule C's chain ends at an
    `:EvidenceSource`. A fact with neither has no citation chain for §13.7 to check."""
    from story.core.models import PackagedFact

    fields = make_package().facts[0].model_dump()
    # `evidence_handle` goes with the passage: it is derived from it, and re-validating a dumped
    # row that has lost its passage while keeping the handle minted from it is refused one rule
    # earlier, as a stated handle disagreeing with the row's own coordinates (S3 of
    # TABLE_CELL_CITATIONS). Dropping it is what a caller changing the evidence chain must do.
    fields.update(passage_id=None, evidence_source_id=None, evidence_handle=None)
    with pytest.raises(pydantic.ValidationError, match="citation chain"):
        PackagedFact(**fields)


def test_a_counterpoint_grounded_in_nothing_is_not_a_counterpoint():
    """§11 point 1. `{claim: "Margins vary.", required_fact_ids: []}` satisfied the first
    draft's non-emptiness rule literally, and §15.3 forbids `minItems` in the schema, so the
    constraint cannot be expressed to the model at all."""
    with pytest.raises(pydantic.ValidationError, match="counter_evidence"):
        Counterpoint(claim="Margins vary.")
    assert Counterpoint(claim="Margins vary.", required_fact_ids=("obs:a",)).claim


def test_an_unusable_evidence_reason_comes_from_a_closed_list():
    """§11 point 2: a free string in a regime that cannot enforce `pattern` is a box to be
    filled, not a decision."""
    from story.core.models import UnusableEvidence, UnusableReason

    assert UnusableEvidence(id="psg:2", reason="different_period_shape").reason is \
        UnusableReason.DIFFERENT_PERIOD_SHAPE
    with pytest.raises(pydantic.ValidationError):
        UnusableEvidence(id="psg:2", reason="did not feel relevant")


def test_a_draft_refuses_sentences_whose_indexes_are_not_in_order():
    """§13's findings address a sentence by index; a gap makes them unaddressable."""
    sentences = make_draft().sentences
    renumbered = (sentences[0].model_copy(update={"index": 4}),)
    with pytest.raises(pydantic.ValidationError, match="not 0"):
        Draft(candidate_id="c", package_id="p", sentences=renumbered)


def refusal(**overrides: Any) -> VerificationFinding:
    fields: dict[str, Any] = dict(code="number_mismatch", severity=Severity.REFUSE,
                                  sentence_index=0, remedy=Remedy.REBIND_TO_FACT,
                                  blocking=True)
    fields.update(overrides)
    return VerificationFinding(**fields)


def test_a_refusal_that_did_not_block_is_not_a_state_a_finding_can_hold():
    """§13.17: one REFUSE refuses the draft. A finding that recorded a REFUSE and a
    `blocking: false` would be a gate that ran and did nothing."""
    with pytest.raises(pydantic.ValidationError, match="always blocks"):
        refusal(blocking=False)


def test_a_warning_may_never_block():
    """§13.17: WARNs do not block but must all be acknowledged in the accepted artifact."""
    with pytest.raises(pydantic.ValidationError, match="never blocks"):
        refusal(severity=Severity.WARN, remedy=Remedy.DROP_SENTENCE, blocking=True)
    assert refusal(severity=Severity.WARN, remedy=Remedy.DROP_SENTENCE, blocking=False)


def test_a_finding_may_suggest_at_most_five_replacement_facts():
    """§13.17: up to five package facts that *would* satisfy the sentence, so the fix is a
    choice rather than a search."""
    assert refusal(suggested_fact_ids=tuple(f"obs:{n}" for n in range(5)))
    with pytest.raises(pydantic.ValidationError, match="caps them at"):
        refusal(suggested_fact_ids=tuple(f"obs:{n}" for n in range(6)))


def test_the_remedy_vocabulary_holds_the_one_section_13_7_1_adds_to_section_13_17():
    """The two sections of the plan disagree, and the narrower remedy is the only actionable
    answer to `column_label_ambiguous_in_passage` — which §13.7.1 measures as firing on 61.3%
    of table observations."""
    assert Remedy.REBIND_TO_DISTINGUISHING_COLUMN in set(Remedy)


def test_a_check_that_examined_nothing_reports_not_applicable_rather_than_pass():
    """§13.17's denominator rule, in one line: a draft with no numeric sentences must not be
    able to report `numbers: PASS`."""
    assert CheckResult(name="numbers", examined=0).outcome is CheckOutcome.NOT_APPLICABLE
    assert CheckResult(name="numbers", examined=3).outcome is CheckOutcome.PASS
    assert CheckResult(name="numbers", examined=3,
                       findings=(refusal(),)).outcome is CheckOutcome.FAIL


def test_a_verified_draft_derives_passed_from_its_findings_and_cannot_store_it():
    """The discipline `graph/core/verification_report.py:217` states. `passed: true` beside a
    blocking finding is a state this type cannot hold, because it holds no `passed` at all."""
    identity = make_package().identity
    clean = VerifiedDraft(candidate_id="c", package_identity=identity,
                          draft_content_sha256="a" * 64,
                          checks=(CheckResult(name="numbers", examined=2),))
    assert clean.passed is True
    assert "passed" not in VerifiedDraft.model_fields

    with pytest.raises(pydantic.ValidationError):
        VerifiedDraft(candidate_id="c", package_identity=identity,
                      draft_content_sha256="a" * 64, passed=True)

    failing = clean.model_copy(update={
        "checks": (CheckResult(name="numbers", examined=2, findings=(refusal(),)),)})
    assert failing.passed is False


def test_a_verified_draft_separates_the_warnings_to_acknowledge_from_the_annotations():
    identity = make_package().identity
    verified = VerifiedDraft(
        candidate_id="c", package_identity=identity, draft_content_sha256="a" * 64,
        findings=(
            refusal(code="over_precision", severity=Severity.WARN,
                    remedy=Remedy.DROP_SENTENCE, blocking=False),
            refusal(code="event_review_flag", severity=Severity.ANNOTATE,
                    remedy=Remedy.ADD_ATTRIBUTION_FRAME, blocking=False),
        ))
    assert verified.passed is True
    assert [f.code for f in verified.acknowledged_warnings] == ["over_precision"]
    assert [f.code for f in verified.annotations] == ["event_review_flag"]


def test_asking_a_verification_for_a_check_it_never_ran_fails_loudly():
    """A test asserting on a check that did not run must fail rather than silently assert
    nothing — `VerificationReport.check` makes the same argument."""
    verified = VerifiedDraft(candidate_id="c", package_identity=make_package().identity,
                             draft_content_sha256="a" * 64,
                             checks=(CheckResult(name="numbers", examined=1),))
    assert verified.check("numbers").examined == 1
    with pytest.raises(KeyError, match="percentages"):
        verified.check("percentages")


def test_a_rejection_with_nothing_blocking_is_an_acceptance_in_the_wrong_directory():
    """§13.17 and §14: `<id>.rejected/` is where nothing looks for a publishable draft."""
    identity = make_package().identity
    with pytest.raises(pydantic.ValidationError, match="no blocking finding"):
        RejectedDraft(candidate_id="c", package_identity=identity,
                      draft_content_sha256="a" * 64,
                      findings=(refusal(severity=Severity.WARN,
                                        remedy=Remedy.DROP_SENTENCE, blocking=False),))

    rejected = RejectedDraft(candidate_id="c", package_identity=identity,
                             draft_content_sha256="a" * 64, findings=(refusal(),))
    assert rejected.passed is False


def test_a_rejection_reads_as_a_list_of_verbs():
    """§13.17: *"an enum is what makes a repair loop dispatchable and lets a human read five
    rejections as five verbs."*"""
    rejected = RejectedDraft(
        candidate_id="c", package_identity=make_package().identity,
        draft_content_sha256="a" * 64,
        findings=(refusal(), refusal(code="metric_ambiguous",
                                     remedy=Remedy.NARROW_METRIC_SURFACE), refusal()))
    assert rejected.remedies == (Remedy.REBIND_TO_FACT, Remedy.NARROW_METRIC_SURFACE)


def test_an_evidence_request_states_relevance_and_never_size():
    """The detector's whole influence over the package. Nothing on it can widen §10.2's
    bounds, which is what keeps the model's universe code-chosen."""
    assert set(EvidenceRequest.model_fields) == {
        "metric_ids", "period_keys", "observation_ids", "event_ids",
        "want_counter_evidence", "want_explanatory_search"}
    assert EvidenceRequest().want_counter_evidence is True, (
        "a candidate that suppressed its own counter-evidence would be a detector deciding "
        "what the writer may weigh")


def test_the_budget_block_records_which_caps_actually_bound():
    """§10.2. "the model did not see it" must be a statement in the artifact rather than an
    inference from two counts."""
    package = make_package()
    assert package.budget.artifact_token_estimate == 536
    assert package.budget.prompt_token_estimate == 402
    assert package.budget.caps_hit == ()
    assert package.budget.parameters.max_total_tokens == 5000
