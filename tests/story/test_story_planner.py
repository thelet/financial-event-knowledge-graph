"""§11's planner: the rules it enforces, the ones it cannot, and one run against the real model.

The package fixture is **not** invented. S5's builder had not landed when this file was
written, so the package below is constructed by hand from the S0 contract with values read out
of `data/graph_runs/graph-v1-0483dc6b4b10/{nodes,edges}.jsonl` on 2026-08-04: the two 2022Q3
margin observations of the demo candidate, the `EVIDENCED_BY` quotes `"3.3"` and `"(12.6)"`,
the passage they were read from (`…q32022formxex991earningsre.htm#p23`, 2,564 characters,
`passage_kind: table`), the `DEFERRED_REQUIRED_SOURCE_LANE` refusal on `cost_of_revenue` in
the same filing (`issue:04d6f106266e`, passage `#p11`) as the counter-evidence item, and the
`attributable to` sentence in `#p18` as the explanatory excerpt. Constructing it here is what
the contract is for; the ids are real so that a later run against S5's builder can be compared
against them rather than against a placeholder.

**Everything normal runs against a fake `StoryGenerationProvider`** driven through the
protocol, plus one replay through the shipped `ReplayingStoryGenerationProvider`. One test at
the end is marked `live` and talks to the real Qwen server; it skips when nothing is listening.
"""

from __future__ import annotations

import ast
import hashlib
import json
import socket
from pathlib import Path
from typing import Any, Mapping

import pytest

from story.core.models import (
    BudgetParameters,
    CausalLanguage,
    Conflict,
    ConflictCluster,
    Counterpoint,
    EditorialPlan,
    GenerationResult,
    HealthStatus,
    KeyPoint,
    MetricAmbiguity,
    PackageBudget,
    PackagedDocument,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    PackagedSubject,
    PackagedWarning,
    Severity,
    StatementClass,
    StoryEvidencePackage,
    UnusableEvidence,
    UnusableReason,
)
from story.contracts import StoryGenerationProvider
from story.providers.generation_store import GenerationStore, ReplayingStoryGenerationProvider
from story.providers.portable_schema import PORTABLE_KEYWORDS, validate_portable_schema
from story.providers.public import (
    PINNED_TEMPERATURE,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    load_provider_config,
)
from story.stages.generation.planner import (
    CAUSAL_LANGUAGE_NOT_COMPUTED,
    COUNTER_EVIDENCE_UNACCOUNTED,
    COUNTERPOINT_MISSING,
    COUNTERPOINT_UNGROUNDED,
    NO_KEY_POINTS,
    PLAN_NOT_CONSTRUCTIBLE,
    THESIS_EMPTY,
    UNKNOWN_UNUSABLE_ID,
    UNKNOWN_WARNING_CODE,
    UNRESOLVABLE_FACT_ID,
    UNRESOLVABLE_PASSAGE_ID,
    EditorialPlanRejected,
    causal_language_for,
    causal_marker_hits,
    cited_spans,
    editorial_plan_from,
    plan_story,
    plan_violations,
)
from story.stages.generation.prompts import (
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    PLANNER_SYSTEM,
    planner_prompt,
    planner_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANNER_MODULE = REPO_ROOT / "story" / "stages" / "generation" / "planner.py"

CANDIDATE_ID = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
                "opendoor:2022Q3:9682f1c1c85a")
PACKAGE_ID = "pkg:cross-metric-divergence-adjusted-gross-margin-opendoor-2022q3:4f21c9a0b7de"
DOCUMENT_ID = "norm:0001801169:0001801169-22-000106:q32022formxex991earningsre.htm"
PASSAGE_ID = DOCUMENT_ID + "#p23"
COUNTER_PASSAGE_ID = DOCUMENT_ID + "#p11"
EXPLANATORY_PASSAGE_ID = DOCUMENT_ID + "#p18"
ADJUSTED_FACT_ID = "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:a9b3fa99773d"
GAAP_FACT_ID = "obs:gaap-gross-margin:opendoor:2022Q3:normalized-table:cfa61af17de1"

#: The first 240 characters of the real `#p23` reconciliation table, kept as one string so the
#: rendering test can assert the planner is shown a table and told how much of it was cut.
PASSAGE_TEXT = (
    "|  |  | Three Months Ended September 30, |  | Nine Months Ended September 30, |\n"
    "| (in millions, except percentages and homes sold, or as noted) |  | 2022 |  | 2021 |\n"
    "| Gross (loss) profit (GAAP) |  | $ | (425) |  | $ | 202 |\n"
    "| Gross Margin |  | (12.6) | % |  | 8.9 | % |\n"
    "| Adjusted Gross Profit |  | $ | 110 |  | $ | 233 |\n"
    "| Adjusted Gross Margin |  | 3.3 | % |  | 10.3 | % |"
)

#: `issue:04d6f106266e` — what this filing would not let the run say. Excerpted, as §10.2.1
#: point 2 requires of every counter-evidence row.
COUNTER_TEXT = (
    "| REVENUE | $ | 3,361 |  | $ | 2,266 |\n"
    "| COST OF REVENUE | 3,786 |  | 2,064 |\n"
    "| GROSS (LOSS) PROFIT | (425) |  | 202 |\n"
    "[refused: DEFERRED_REQUIRED_SOURCE_LANE — cost_of_revenue requires a source lane this "
    "corpus does not contain]"
)

#: The real `#p18` sentence. It carries `attributable to`, which is a §13.10 marker, and it is
#: definitional rather than causal — exactly why marker presence is a permission and not a
#: licence.
EXPLANATORY_TEXT = (
    "We do so by including revenue generated from homes sold (and adjacent services) in the "
    "period and only the expenses that are directly attributable to such home sales, even if "
    "such expenses were recognized in prior periods."
)


# -- the package, by hand from the S0 contract ------------------------------------------------


def divergence_package(**overrides: Any) -> StoryEvidencePackage:
    """The 2022Q3 cross-metric divergence candidate's package: 3.3 vs -12.6, a 15.9 pp gap."""
    fields: dict[str, Any] = dict(
        package_id=PACKAGE_ID,
        candidate_id=CANDIDATE_ID,
        detector_id="detector:cross_metric_divergence",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id="graph-v1-0483dc6b4b10",
        graph_projection_version="1.2.0",
        extraction_run_id="extract-v1-lexical-833f7bcfbce9",
        run_complete_sha256="1cc8f7b01c040531" + "0" * 48,
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash=(
            "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34"),
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor",
                                entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(
            PackagedFact(
                observation_id=ADJUSTED_FACT_ID,
                metric_id="adjusted_gross_margin", metric_label="Adjusted Gross Margin",
                period_key="2022Q3", period_start="2022-07-01", period_end="2022-09-30",
                shape="duration", value=3.3, unit="percent", printed_form="3.3",
                row_label="Adjusted Gross Margin", column_label="2022",
                source_lane="normalized_table", validation_state="clean",
                passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, quoted_text="3.3",
                source_url="https://www.sec.gov/Archives/edgar/data/1801169/"
                           "000180116922000106/q32022formxex991earningsre.htm"),
            PackagedFact(
                observation_id=GAAP_FACT_ID,
                metric_id="gaap_gross_margin", metric_label="Gross Margin",
                period_key="2022Q3", period_start="2022-07-01", period_end="2022-09-30",
                shape="duration", value=-12.6, unit="percent", printed_form="(12.6)",
                row_label="Gross Margin", column_label="2022",
                source_lane="normalized_table", validation_state="clean",
                passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, quoted_text="(12.6)"),
        ),
        metrics=(
            PackagedMetric(metric_id="adjusted_gross_margin", label="Adjusted Gross Margin",
                           unit="percent", period_type="duration",
                           distinct_from=("gaap_gross_margin",),
                           ambiguities=(MetricAmbiguity(
                               code="NON_GAAP_ADJUSTMENT_BASIS",
                               description="Adjustments are defined by the company",
                               impact="Not comparable across issuers"),)),
            PackagedMetric(metric_id="gaap_gross_margin", label="Gross Margin",
                           unit="percent", period_type="duration",
                           distinct_from=("adjusted_gross_margin",)),
        ),
        primary_passages=(
            PackagedPassage(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=PASSAGE_TEXT,
                            char_count=2564, passage_kind="table",
                            heading_path=("EX-99.1", "Non-GAAP Financial Measures",
                                          "RECONCILIATION OF GAAP TO NON-GAAP MEASURES")),
        ),
        counter_evidence=(
            PackagedPassage(passage_id=COUNTER_PASSAGE_ID, document_id=DOCUMENT_ID,
                            text=COUNTER_TEXT, char_count=2273, passage_kind="table",
                            excerpted=True, char_start=380, char_end=380 + len(COUNTER_TEXT)),
        ),
        warnings=(
            PackagedWarning(code="filing_date_unknown", severity=Severity.ANNOTATE,
                            subject_ids=(CANDIDATE_ID,),
                            detail="the candidate's own filing date is not knowable"),
            PackagedWarning(code="counter_evidence_same_document", severity=Severity.ANNOTATE,
                            subject_ids=(COUNTER_PASSAGE_ID,),
                            detail="the refusal is in a neighbouring table of the same filing"),
        ),
        documents=(PackagedDocument(document_id=DOCUMENT_ID, form="8-K",
                                    filing_date="2022-11-03",
                                    document_type="earnings_release", title="EX-99.1"),),
        budget=PackageBudget(artifact_token_estimate=1180, prompt_token_estimate=1046,
                             section_counts={"facts": 2, "primary_passages": 1,
                                             "counter_evidence": 1},
                             parameters=BudgetParameters()),
    )
    fields.update(overrides)
    return StoryEvidencePackage(**fields)


def package_without_counter_evidence() -> StoryEvidencePackage:
    return divergence_package(counter_evidence=(), warnings=(
        PackagedWarning(code="filing_date_unknown", severity=Severity.ANNOTATE,
                        subject_ids=(CANDIDATE_ID,),
                        detail="the candidate's own filing date is not knowable"),))


def package_with_a_causal_excerpt(text: str = EXPLANATORY_TEXT) -> StoryEvidencePackage:
    return divergence_package(explanatory_passages=(
        PackagedPassage(passage_id=EXPLANATORY_PASSAGE_ID, document_id=DOCUMENT_ID, text=text,
                        char_count=2285, passage_kind="narrative", excerpted=True,
                        char_start=1120, char_end=1120 + len(text),
                        query_terms=("adjusted gross margin", "2022Q3"), score=4.81),))


# -- a valid answer, and the fakes that return it ---------------------------------------------


def valid_answer(**overrides: Any) -> dict[str, Any]:
    """What a conformant model returns for `divergence_package()`."""
    content: dict[str, Any] = {
        "thesis": "Opendoor's two definitions of gross margin were 15.9 points apart in 2022Q3.",
        "why_it_matters": "The adjusted figure is positive while the GAAP figure is not.",
        "key_points": [
            {"claim": "GAAP gross margin was (12.6)% in the third quarter of 2022.",
             "required_fact_ids": [GAAP_FACT_ID],
             "required_citation_passage_ids": [PASSAGE_ID],
             "statement_class": "reported"},
            {"claim": "Adjusted gross margin was 3.3% over the same period.",
             "required_fact_ids": [ADJUSTED_FACT_ID],
             "required_citation_passage_ids": [PASSAGE_ID],
             "statement_class": "reported"},
        ],
        "counterpoints": [
            {"claim": "The same filing refuses to state cost of revenue as a metric, so the "
                      "reconciliation cannot be independently rebuilt from the package.",
             "required_fact_ids": [],
             "required_citation_passage_ids": [COUNTER_PASSAGE_ID]},
        ],
        "required_warnings": ["filing_date_unknown"],
        "causal_language": "forbidden",
        "uncertainty": "Only one filing backs both figures.",
        "structure": ["What happened", "Why the two numbers differ", "What is not known"],
        "prohibited_claims": ["Any statement that the adjustment explains the gap."],
        "unusable_evidence": [],
    }
    content.update(overrides)
    return content


class FakePlanProvider:
    """A `StoryGenerationProvider` with a fixed answer and a record of what it was asked.

    Not a mock library and not a subclass: the protocol is structural, so the way to prove the
    planner uses nothing but the protocol is to hand it an object that implements exactly the
    protocol and nothing else.
    """

    def __init__(self, content: Mapping[str, Any] | None = None,
                 raises: Exception | None = None, model_id: str = "Qwen3.5-9B-Q4_K_M.gguf"):
        self.content = dict(content if content is not None else valid_answer())
        self.raises = raises
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "schema_name": schema_name, "max_tokens": max_tokens,
                           "temperature": temperature})
        if self.raises is not None:
            raise self.raises
        # `raw_content` really is the serialisation of `content`, because the replay store
        # stores the string and parses it back: a fake whose two fields disagreed would make
        # `test_the_plan_replays_from_a_recorded_generation_with_no_server_running` pass
        # against a store that never held the answer.
        raw = json.dumps(self.content, sort_keys=True, separators=(",", ":"))
        return GenerationResult(
            content=self.content, raw_content=raw,
            model_id="/models/Qwen3.5-9B-Q4_K_M.gguf",
            prompt_tokens=940, completion_tokens=212, total_tokens=1152, latency_ms=1834.0,
            raw_sha256="a" * 64,
            content_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            finish_reason="stop", attempts=1,
            metadata={"schema_name": schema_name, "configured_model": self.model_id})

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ok", detail="fake")


def plan_with(provider: StoryGenerationProvider, package: StoryEvidencePackage):
    """Every test goes through this, annotated with the protocol, so the surface under test is
    the contract rather than the concrete fake."""
    return plan_story(package, provider=provider, max_tokens=PLANNER_MAX_TOKENS)


# -- rule: no graph, no retrieval, no tools ---------------------------------------------------


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_planner_module_reaches_no_graph_no_retrieval_and_no_detection():
    """§11: the planner has a provider and a package, and no way to ask for anything else."""
    imported = module_imports(PLANNER_MODULE)
    for forbidden in ("neo4j", "httpx", "requests"):
        assert not any(name.split(".")[0] == forbidden for name in imported), imported
    for stage in ("story.stages.retrieval", "story.stages.detection", "story.stages.ranking",
                  "story.stages.freshness", "story.stages.packaging",
                  "story.stages.verification"):
        assert not any(name.startswith(stage) for name in imported), imported
    assert "story.providers.neo4j_connection" not in imported


def test_the_planner_takes_a_package_a_provider_and_a_token_budget_and_nothing_else():
    """A signature with no room for a term list is the §11 correction stated structurally."""
    import inspect

    parameters = inspect.signature(plan_story).parameters
    assert list(parameters) == ["package", "provider", "max_tokens"]
    assert parameters["max_tokens"].default is inspect.Parameter.empty
    assert "temperature" not in parameters


def test_the_prompt_names_no_tool_and_asks_for_no_search_terms():
    prompt = planner_prompt(divergence_package())
    for tool in ("search_passages", "find_counter_evidence", "get_fact_evidence", "terms["):
        assert tool not in prompt and tool not in PLANNER_SYSTEM


def test_the_same_package_renders_the_same_prompt_every_time():
    """The prompt is half of the request identity the replay store keys on (§14)."""
    package = divergence_package()
    assert planner_prompt(package) == planner_prompt(divergence_package())
    assert planner_prompt(package) != planner_prompt(package_with_a_causal_excerpt())


def test_the_prompt_shows_the_real_ids_and_says_how_much_of_a_passage_it_cut():
    prompt = planner_prompt(divergence_package())
    assert ADJUSTED_FACT_ID in prompt and GAAP_FACT_ID in prompt
    assert PASSAGE_ID in prompt and COUNTER_PASSAGE_ID in prompt
    assert "3.3 percent" in prompt and "-12.6 percent" in prompt
    assert f"of {2564}" in prompt, "a truncated passage must say what it is a part of"
    assert "filing_date_unknown" in prompt


# -- rule: §15.3's portable subset ------------------------------------------------------------


@pytest.mark.parametrize("causal_language", list(CausalLanguage))
def test_the_planner_schema_is_inside_the_portable_subset(causal_language):
    validate_portable_schema(planner_schema(causal_language=causal_language))


def walk_schema(schema: Mapping[str, Any], path: str = "$"):
    yield path, schema
    for name, subschema in (schema.get("properties") or {}).items():
        yield from walk_schema(subschema, f"{path}.{name}")
    if isinstance(schema.get("items"), Mapping):
        yield from walk_schema(schema["items"], f"{path}[]")


def test_the_planner_schema_uses_no_keyword_llama_cpp_would_silently_drop():
    """`minItems`, `pattern` and `anyOf` are dropped by the grammar *and* ignored by the local
    checker, so a schema using one is unconstrained without saying so (§15.3)."""
    schema = planner_schema(causal_language=CausalLanguage.FORBIDDEN)
    for path, subschema in walk_schema(schema):
        assert set(subschema) <= PORTABLE_KEYWORDS, f"{path}: {sorted(subschema)}"


def test_every_object_in_the_planner_schema_forbids_extras_and_requires_every_property():
    """S6's three structural rules, asserted on the shipped schema rather than assumed."""
    schema = planner_schema(causal_language=CausalLanguage.FORBIDDEN)
    for path, subschema in walk_schema(schema):
        if subschema.get("type") == "object":
            assert subschema.get("additionalProperties") is False, path
            assert sorted(subschema["required"]) == sorted(subschema["properties"]), path
        if subschema.get("type") == "array":
            assert isinstance(subschema.get("items"), Mapping), path


def test_the_schema_admits_exactly_the_causal_language_code_computed():
    """§15.3 cannot express a bound, so "not yours to choose" is a one-member enum."""
    forbidden = planner_schema(causal_language=CausalLanguage.FORBIDDEN)
    reported = planner_schema(causal_language=CausalLanguage.REPORTED_ONLY)
    assert forbidden["properties"]["causal_language"]["enum"] == ["forbidden"]
    assert reported["properties"]["causal_language"]["enum"] == ["reported_only"]


def test_the_unusable_evidence_reason_is_an_enum_and_not_a_free_string():
    """§11 point 2. A free string in a regime that cannot enforce `pattern` is a box to fill."""
    schema = planner_schema(causal_language=CausalLanguage.FORBIDDEN)
    reason = schema["properties"]["unusable_evidence"]["items"]["properties"]["reason"]
    assert reason["enum"] == [member.value for member in UnusableReason]


# -- rule: causal_language is computed by code ------------------------------------------------


def test_causal_language_is_forbidden_when_no_cited_span_carries_a_marker():
    assert causal_language_for(divergence_package()) is CausalLanguage.FORBIDDEN


def test_causal_language_is_reported_only_when_a_cited_span_carries_a_marker():
    package = package_with_a_causal_excerpt()
    assert causal_language_for(package) is CausalLanguage.REPORTED_ONLY
    hits = causal_marker_hits(package)
    assert [(hit.marker, hit.negated) for hit in hits] == [("attributable to", False)]


def test_a_marker_outside_every_cited_span_does_not_license_reported_only():
    """§13.10 condition 2: a passage is thousands of characters and only the cited span counts.

    The passage below is not excerpted — it is in the package whole because a fact is bound to
    it — so its far end is not a span anything cites.
    """
    package = divergence_package(primary_passages=(
        PackagedPassage(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                        text=PASSAGE_TEXT + "\nThe decline was due to inventory write-downs.",
                        char_count=2564, passage_kind="table"),))
    assert causal_language_for(package) is CausalLanguage.FORBIDDEN
    assert causal_marker_hits(package) == ()


def test_a_negated_causal_marker_does_not_license_reported_only():
    """37 passages in this corpus say "not as a result of"; conditions 1-4 all pass on one."""
    package = package_with_a_causal_excerpt(
        "The Purchaser decided to enter into this Agreement not as a result of any general "
        "solicitation.")
    assert causal_language_for(package) is CausalLanguage.FORBIDDEN
    assert [hit.negated for hit in causal_marker_hits(package)] == [True]


def test_a_causal_marker_in_a_facts_own_quoted_span_licenses_reported_only():
    package = divergence_package(facts=(
        PackagedFact(observation_id=GAAP_FACT_ID, metric_id="gaap_gross_margin",
                     metric_label="Gross Margin", period_key="2022Q3", shape="duration",
                     value=-12.6, unit="percent", source_lane="normalized_table",
                     validation_state="clean", passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                     quoted_text="(12.6), which declined due to inventory write-downs"),))
    assert causal_language_for(package) is CausalLanguage.REPORTED_ONLY
    # The fact's own `EVIDENCED_BY` quote is a cited span; so is the excerpted counter-evidence
    # row. The un-excerpted primary passage is not.
    assert [(span.source_id, span.kind) for span in cited_spans(package)] == [
        (GAAP_FACT_ID, "fact_quote"), (COUNTER_PASSAGE_ID, "excerpt")]
    assert [hit.source_id for hit in causal_marker_hits(package)] == [GAAP_FACT_ID]


def test_the_planner_stamps_the_computed_causal_language_over_the_models_answer():
    """§11: computed by code before the call, never chosen by the model — and never read back."""
    plan = editorial_plan_from(valid_answer(causal_language="reported_only"),
                               divergence_package(), model_id="Qwen3.5-9B-Q4_K_M.gguf")
    assert plan.causal_language is CausalLanguage.FORBIDDEN


def test_the_request_carries_the_pinned_causal_language_and_temperature_zero():
    provider = FakePlanProvider()
    plan_with(provider, divergence_package())
    call = provider.calls[0]
    assert call["temperature"] == PINNED_TEMPERATURE == 0.0
    assert call["schema_name"] == PLANNER_SCHEMA_NAME
    assert call["max_tokens"] == PLANNER_MAX_TOKENS
    assert call["schema"]["properties"]["causal_language"]["enum"] == ["forbidden"]
    assert call["system"] == PLANNER_SYSTEM


# -- rule: every id must resolve in the package -----------------------------------------------


def test_a_plan_naming_a_fact_id_the_package_does_not_hold_is_rejected():
    answer = valid_answer()
    answer["key_points"][0]["required_fact_ids"] = ["obs:adjusted-ebitda:invented:2022Q3"]
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert UNRESOLVABLE_FACT_ID in raised.value.codes


def test_a_plan_naming_a_passage_id_the_package_does_not_hold_is_rejected():
    answer = valid_answer()
    answer["key_points"][1]["required_citation_passage_ids"] = [DOCUMENT_ID + "#p99"]
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert UNRESOLVABLE_PASSAGE_ID in raised.value.codes


def test_a_plan_requiring_a_warning_the_package_does_not_carry_is_rejected():
    answer = valid_answer(required_warnings=["single_source", "filing_date_unknown"])
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert raised.value.codes == (UNKNOWN_WARNING_CODE,)


def test_a_plan_with_no_key_point_is_rejected():
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(valid_answer(key_points=[])), divergence_package())
    assert NO_KEY_POINTS in raised.value.codes


# -- rule: counterpoints, and the three things that close the vacuity hole --------------------


def test_a_plan_with_no_counterpoint_is_rejected_when_the_package_holds_counter_evidence():
    """§15.3 forbids `minItems`, so "non-empty" is a post-hoc code check or it is nothing."""
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(valid_answer(counterpoints=[])), divergence_package())
    assert COUNTERPOINT_MISSING in raised.value.codes


def test_a_counterpoint_resting_on_no_counter_evidence_id_is_rejected():
    """§11 point 1: ids drawn from `counter_evidence`, not from anywhere in the package."""
    answer = valid_answer(counterpoints=[
        {"claim": "Margins vary.", "required_fact_ids": [ADJUSTED_FACT_ID],
         "required_citation_passage_ids": [PASSAGE_ID]}])
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert COUNTERPOINT_UNGROUNDED in raised.value.codes


def test_a_counterpoint_carrying_no_id_at_all_is_rejected_at_construction():
    """`{claim: "Margins vary.", required_fact_ids: []}` satisfied the first draft literally."""
    answer = valid_answer(counterpoints=[
        {"claim": "Margins vary.", "required_fact_ids": [],
         "required_citation_passage_ids": []}])
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert raised.value.codes == (PLAN_NOT_CONSTRUCTIBLE,)
    assert "counterpoint" in str(raised.value)


def test_a_counterpoint_grounded_in_a_fact_read_from_a_counter_evidence_passage_is_accepted():
    package = divergence_package(facts=(
        *divergence_package().facts,
        PackagedFact(observation_id="obs:gross-profit:opendoor:2022Q3:normalized-table:aa01",
                     metric_id="gaap_gross_profit", metric_label="Gross (loss) profit",
                     period_key="2022Q3", shape="duration", value=-425.0, unit="USD",
                     scale="millions", source_lane="normalized_table",
                     validation_state="clean", passage_id=COUNTER_PASSAGE_ID,
                     document_id=DOCUMENT_ID, quoted_text="(425)"),))
    answer = valid_answer(counterpoints=[
        {"claim": "The filing's own cost of revenue row is refused, so the reconciliation "
                  "cannot be rebuilt.",
         "required_fact_ids": ["obs:gross-profit:opendoor:2022Q3:normalized-table:aa01"],
         "required_citation_passage_ids": []}])
    plan = plan_with(FakePlanProvider(answer), package).plan
    assert plan.counterpoints[0].required_fact_ids == (
        "obs:gross-profit:opendoor:2022Q3:normalized-table:aa01",)


def test_a_counter_evidence_item_the_plan_neither_used_nor_declared_unusable_is_rejected():
    package = divergence_package(counter_evidence=(
        *divergence_package().counter_evidence,
        PackagedPassage(passage_id=DOCUMENT_ID + "#p12", document_id=DOCUMENT_ID,
                        text="[refused: UNRESOLVED_METRIC — 'Basic' matches no metric]",
                        char_count=1804, excerpted=True, char_start=0, char_end=55),))
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(valid_answer()), package)
    assert COUNTER_EVIDENCE_UNACCOUNTED in raised.value.codes
    assert DOCUMENT_ID + "#p12" in str(raised.value)


def test_an_unused_counter_evidence_item_declared_with_an_enum_reason_is_accepted():
    package = divergence_package(counter_evidence=(
        *divergence_package().counter_evidence,
        PackagedPassage(passage_id=DOCUMENT_ID + "#p12", document_id=DOCUMENT_ID,
                        text="[refused: UNRESOLVED_METRIC — 'Basic' matches no metric]",
                        char_count=1804, excerpted=True, char_start=0, char_end=55),))
    answer = valid_answer(unusable_evidence=[
        {"id": DOCUMENT_ID + "#p12", "reason": "outside_thesis_scope"}])
    plan = plan_with(FakePlanProvider(answer), package).plan
    assert plan.unusable_evidence == (
        UnusableEvidence(id=DOCUMENT_ID + "#p12",
                         reason=UnusableReason.OUTSIDE_THESIS_SCOPE),)


def test_a_free_text_reason_for_unusable_evidence_is_refused_by_the_grammar():
    """§11 point 2 is enforced twice, and the schema gets there first — which is the point of
    making it an enum rather than a string: the grammar cannot emit the free text at all."""
    answer = valid_answer(unusable_evidence=[
        {"id": COUNTER_PASSAGE_ID, "reason": "it did not fit the narrative"}])
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert any("is not one of" in violation and "reason" in violation
               for violation in raised.value.violations)


def test_a_free_text_reason_is_still_refused_when_the_schema_check_is_bypassed():
    """The second enforcement: `UnusableReason` is a frozen enum, so a stored row replayed from
    an older schema cannot smuggle one past `editorial_plan_from`."""
    answer = valid_answer(unusable_evidence=[
        {"id": COUNTER_PASSAGE_ID, "reason": "it did not fit the narrative"}])
    with pytest.raises(EditorialPlanRejected) as raised:
        editorial_plan_from(answer, divergence_package(), model_id="Qwen3.5-9B-Q4_K_M.gguf")
    assert raised.value.codes == (PLAN_NOT_CONSTRUCTIBLE,)
    assert "reason" in str(raised.value)


def test_unusable_evidence_naming_an_item_the_package_does_not_hold_is_rejected():
    answer = valid_answer(unusable_evidence=[
        {"id": "psg:not-in-this-package", "reason": "different_population"}])
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert "unusable_evidence names" in str(raised.value)


def test_a_package_with_no_counter_evidence_accepts_a_plan_with_no_counterpoint():
    plan = plan_with(FakePlanProvider(valid_answer(counterpoints=[])),
                     package_without_counter_evidence()).plan
    assert plan.counterpoints == ()


# -- rule: malformed output fails clearly, and is never retried --------------------------------


def test_content_that_is_not_json_fails_as_a_response_error_and_is_asked_for_once():
    provider = FakePlanProvider(raises=StoryProviderResponseError(
        "assistant content is not JSON: Expecting value: line 1 column 1"))
    with pytest.raises(StoryProviderResponseError):
        plan_with(provider, divergence_package())
    assert len(provider.calls) == 1


def test_an_answer_missing_a_required_property_fails_as_a_schema_error_and_is_never_retried():
    answer = valid_answer()
    del answer["why_it_matters"]
    provider = FakePlanProvider(answer)
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(provider, divergence_package())
    assert any("why_it_matters" in violation for violation in raised.value.violations)
    assert len(provider.calls) == 1


def test_an_answer_of_the_wrong_shape_fails_as_a_schema_error_rather_than_a_plan_rejection():
    provider = FakePlanProvider(valid_answer(key_points="two of them"))
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(provider, divergence_package())
    assert any("expected array" in violation for violation in raised.value.violations)


def test_a_statement_class_outside_the_enum_fails_as_a_schema_error():
    answer = valid_answer()
    answer["key_points"][0]["statement_class"] = "causal"
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(FakePlanProvider(answer), divergence_package())
    assert any("statement_class" in violation for violation in raised.value.violations)


def test_a_schema_violation_and_a_plan_rejection_are_different_failures():
    """One is a statement about the runtime, the other about §11; a caller must tell them apart."""
    assert not issubclass(EditorialPlanRejected, StoryProviderSchemaError)
    assert not issubclass(StoryProviderSchemaError, EditorialPlanRejected)


# -- the accepted plan ------------------------------------------------------------------------


def test_a_conformant_answer_becomes_a_plan_whose_every_id_resolves_in_the_package():
    package = divergence_package()
    planned = plan_with(FakePlanProvider(), package)
    assert plan_violations(planned.plan, package) == ()
    assert planned.plan.candidate_id == CANDIDATE_ID
    assert planned.plan.package_id == PACKAGE_ID
    assert planned.plan.prompt_version == PLANNER_PROMPT_VERSION
    assert planned.plan.model_id == "Qwen3.5-9B-Q4_K_M.gguf"
    assert planned.plan.key_points[0].statement_class is StatementClass.REPORTED
    assert planned.plan.required_warnings == ("filing_date_unknown",)
    assert planned.generation.total_tokens == 1152


def test_the_identity_fields_come_from_the_package_and_not_from_the_model():
    """A model that returned a different candidate id could not re-key the plan."""
    answer = valid_answer()
    answer["thesis"] = "cand:invented:9999"
    plan = plan_with(FakePlanProvider(answer), divergence_package()).plan
    assert plan.candidate_id == CANDIDATE_ID and plan.package_id == PACKAGE_ID


def test_a_hand_written_plan_is_judged_by_the_same_function_as_a_generated_one():
    """`plan_violations` is pure, so S9 can run it against a plan no model produced."""
    package = divergence_package()
    plan = EditorialPlan(
        candidate_id=CANDIDATE_ID, package_id=PACKAGE_ID, thesis="A gap of 15.9 points.",
        why_it_matters="Two definitions disagree.",
        key_points=(KeyPoint(claim="GAAP gross margin was (12.6)%.",
                             required_fact_ids=(GAAP_FACT_ID,),
                             required_citation_passage_ids=(PASSAGE_ID,),
                             statement_class=StatementClass.REPORTED),),
        counterpoints=(Counterpoint(claim="cost of revenue is refused in this filing",
                                    required_citation_passage_ids=(COUNTER_PASSAGE_ID,)),),
        causal_language=CausalLanguage.REPORTED_ONLY)
    assert [violation.code for violation in plan_violations(plan, package)] == [
        CAUSAL_LANGUAGE_NOT_COMPUTED]


def test_the_plan_replays_from_a_recorded_generation_with_no_server_running():
    """§14: byte-identical *replay* is the achievable claim, and the planner is inside it."""
    package = divergence_package()
    store = GenerationStore()
    recording = ReplayingStoryGenerationProvider(
        store, FakePlanProvider(), prompt_version=PLANNER_PROMPT_VERSION)
    first = plan_with(recording, package).plan

    replaying: StoryGenerationProvider = ReplayingStoryGenerationProvider(
        store, model_id="Qwen3.5-9B-Q4_K_M.gguf")
    second = plan_with(replaying, package).plan
    assert second == first
    assert len(store) == 1


def test_a_conflict_and_an_ambiguity_reach_the_prompt_because_the_post_must_carry_them():
    package = divergence_package(conflicts=(
        Conflict(slot="(gaap_gross_margin, 2022Q3)",
                 clusters=(ConflictCluster(value=-12.6, document_ids=(DOCUMENT_ID,)),),
                 classification="presentation_rounding",
                 resolution_rule="R8_presentation_tolerance"),))
    prompt = planner_prompt(package)
    assert "presentation_rounding" in prompt
    assert "NON_GAAP_ADJUSTMENT_BASIS" in prompt


# -- the real server ---------------------------------------------------------------------------


def server_is_listening(base_url: str, timeout_seconds: float = 1.0) -> bool:
    host, _, port = base_url.rsplit("/", 1)[-1].partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=timeout_seconds):
            return True
    except OSError:
        return False


#: Every code `plan_violations` can return. A live rejection must be one of these — a rejection
#: naming something else would mean this stage refused for a reason §11 does not have.
KNOWN_VIOLATION_CODES = frozenset({
    UNRESOLVABLE_FACT_ID, UNRESOLVABLE_PASSAGE_ID, COUNTERPOINT_MISSING,
    COUNTERPOINT_UNGROUNDED, COUNTER_EVIDENCE_UNACCOUNTED, UNKNOWN_WARNING_CODE,
    UNKNOWN_UNUSABLE_ID, CAUSAL_LANGUAGE_NOT_COMPUTED, THESIS_EMPTY, NO_KEY_POINTS,
    PLAN_NOT_CONSTRUCTIBLE})


@pytest.mark.live
def test_the_real_model_plans_this_candidate_and_the_rules_judge_what_comes_back():
    """S7 through the real transport, judged by every rule above.

    **This test does not assert that the model produces an acceptable plan, because it does
    not reliably produce one** — and that is the measurement worth having rather than a
    tolerance worth hiding. Across 23 live calls against the running Qwen3.5-9B-Q4_K_M on
    2026-08-04, same package, same schema, temperature 0.0:

    * **6 accepted** — 1,494 prompt tokens, 870-899 completion tokens, ~12 s, one attempt,
      three key points and one counterpoint resting on the `cost_of_revenue` refusal;
    * **9 refused by §11** with `counterpoint_missing` at 752 completion tokens: the model
      wrote `counterpoints: []` against a package carrying counter-evidence, which is exactly
      the vacuity §11's correction exists to catch, now observed rather than predicted;
    * **7 lost to a repetition loop** — all 2,048 output tokens spent repeating a passage id
      inside a free-text field, `finish_reason: length`, raised as
      `StoryProviderResponseError` and never retried;
    * **1 truncated** at the old `max_tokens: 1024`, which is why that constant is now 2048.

    The identical request produced all three outcomes at different times, and re-running two
    prompts that differ by one character inverted the result an hour later with no edit in
    between. `generation_store.py` already states the reason: byte-identical *generation* is
    not achievable on this runtime (`cached_tokens: 1516` on a 1,520-token prompt),
    byte-identical *replay* is.

    Two earlier failures were fixed rather than tolerated and both are recorded where the fix
    is: `max_tokens: 1024` truncated the object mid-JSON (`prompts.PLANNER_MAX_TOKENS`), and
    before rule 1 named which id prefix belongs in which array the model put the passage id in
    `required_fact_ids`.

    So what is asserted is what must hold on every run: the grammar constrained the answer, the
    plan's `causal_language` is the one code computed, exactly one attempt was made, and the
    outcome is either an accepted plan with no violations or a refusal naming a §11 code.

    Skips cleanly when no server is listening: the local llama.cpp process is single-slot and
    shared with the factual-spine session, and a suite that failed because a GPU was busy is a
    suite nobody runs.
    """
    from story.providers.openai_compatible import StoryOpenAICompatibleProvider

    config = load_provider_config()
    if not server_is_listening(config.base_url):
        pytest.skip(f"no model server listening on {config.base_url}")

    package = divergence_package()
    with StoryOpenAICompatibleProvider(config) as provider:
        try:
            planned = plan_with(provider, package)
        except EditorialPlanRejected as rejected:
            # A refusal is a correct outcome, not a flake — but it must name a §11 code, or
            # this stage refused for a reason §11 does not have.
            assert set(rejected.codes) <= KNOWN_VIOLATION_CODES, rejected.codes
            pytest.skip(f"the live model's plan was refused by §11: {rejected.codes}")
        except StoryProviderResponseError as unparseable:
            # The repetition loop above. Clean, one attempt, no plan.
            pytest.skip(f"the live model returned no usable object: {unparseable}")

    # Reached only when the model's answer survived §11.
    assert plan_violations(planned.plan, package) == ()
    assert planned.plan.causal_language is CausalLanguage.FORBIDDEN
    assert planned.plan.counterpoints, "the package carries counter-evidence"
    assert planned.plan.key_points
    assert planned.plan.model_id == provider.model_id
    # `stop`, not `length`: the whole object arrived. This is the assertion the first live run
    # failed, and the reason `PLANNER_MAX_TOKENS` is 2048.
    assert planned.generation.finish_reason == "stop"
    assert planned.generation.attempts == 1
    assert planned.generation.prompt_tokens + planned.generation.completion_tokens < 8192
