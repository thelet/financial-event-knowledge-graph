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
import inspect
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
    DerivationOperation,
    DerivationRequest,
    DerivedFact,
    EditorialPlan,
    EvidenceRequest,
    EvidenceRole,
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
    StoryCandidate,
    StoryEvidencePackage,
    UnusableEvidence,
    UnusableReason,
    WarningKind,
)
from story.contracts import StoryGenerationProvider
from story.providers.generation_store import GenerationStore, ReplayingStoryGenerationProvider
from story.providers.portable_schema import PORTABLE_KEYWORDS, validate_portable_schema
from story.providers.public import (
    PINNED_TEMPERATURE,
    PROVIDER_LOCAL,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    load_provider_config,
)
from story.core import lexicon
from story.core.spine import spine_for
from story.stages.generation.planner import (
    CAUSAL_LANGUAGE_NOT_COMPUTED,
    COUNTER_EVIDENCE_UNACCOUNTED,
    DERIVATION_NOT_OFFERED,
    DIRECTION_CONTRADICTS_SPINE,
    COUNTERPOINT_MISSING,
    COUNTERPOINT_UNGROUNDED,
    NO_KEY_POINTS,
    ORDERING_WORDS,
    PLAN_NOT_CONSTRUCTIBLE,
    THESIS_EMPTY,
    UNKNOWN_UNUSABLE_ID,
    UNKNOWN_WARNING_CODE,
    UNRESOLVABLE_FACT_HANDLE,
    UNRESOLVABLE_FACT_ID,
    UNRESOLVABLE_PASSAGE_ID,
    EditorialPlanRejected,
    causal_language_for,
    causal_marker_hits,
    cited_spans,
    editorial_plan_from,
    plan_story,
    plan_violations,
    spine_violations,
)
from story.stages.generation.prompts import (
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    PLANNER_SYSTEM,
    VERIFIED_CHANGE_HEADING,
    planner_prompt,
    planner_schema,
)
# **This file imports three sibling stages and the composition stage, and that is deliberate.**
# `story/stages/generation/` may import none of them (`test_no_stage_imports_another_stage`),
# which is exactly why `slots`, `derived_facts`, `offered` and `spine` are *arguments* to
# `plan_story`. A test standing where `story/pipeline.py` stands is the only place the seam can
# be driven with the values the pipeline really passes.
from story.stages.composition.slot_table import slot_table
from story.stages.derivation.execute import execute
from story.stages.derivation.offers import offers
from story.stages.detection import detector_config
from story.stages.packaging.counter_evidence import MATCH_BASIS_SAME_DOCUMENT

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
                            role=EvidenceRole.PRIMARY_SUPPORT,
                            heading_path=("EX-99.1", "Non-GAAP Financial Measures",
                                          "RECONCILIATION OF GAAP TO NON-GAAP MEASURES")),
        ),
        counter_evidence=(
            PackagedPassage(passage_id=COUNTER_PASSAGE_ID, document_id=DOCUMENT_ID,
                            text=COUNTER_TEXT, char_count=2273, passage_kind="table",
                            role=EvidenceRole.COUNTER_EVIDENCE,
                            match_basis=MATCH_BASIS_SAME_DOCUMENT,
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


#: The reading the counter-evidence passage carries — `(425)`, the GAAP gross profit row of the
#: same reconciliation the refusal sits in.
COUNTER_FACT_ID = "obs:gross-profit:opendoor:2022Q3:normalized-table:aa01"

COUNTER_FACT = PackagedFact(
    observation_id=COUNTER_FACT_ID, metric_id="gaap_gross_profit",
    metric_label="Gross (loss) profit", period_key="2022Q3", shape="duration",
    value=-425.0, unit="USD", scale="millions", source_lane="normalized_table",
    validation_state="clean", passage_id=COUNTER_PASSAGE_ID, document_id=DOCUMENT_ID,
    quoted_text="(425)")


def planning_package(**overrides: Any) -> StoryEvidencePackage:
    """`divergence_package()` plus the one packaged reading its counter-evidence passage carries.

    **This exists because of a measured consequence of 2.0.0, not for convenience.** A plan no
    longer writes `required_citation_passage_ids` — `editorial_plan_from` derives them from
    `PackagedFact.passage_id` — and the slot table `story/pipeline.py` builds carries **no `P`
    rows**, so the only thing a plan can name that is *drawn from* `counter_evidence` is a fact
    that was read from a counter-evidence passage. `divergence_package()` holds no such fact,
    so under 2.0.0 **no plan over it can be accepted at all**: an empty counterpoint earns
    `counterpoint_missing`, one resting on `F1` earns `counterpoint_ungrounded`, and one
    resting on nothing does not construct *(all three verified 2026-08-26)*. That dead end is
    recorded here rather than smoothed over — it is a property of the contract change and not
    of this fixture.
    """
    fields: dict[str, Any] = {"facts": (*divergence_package().facts, COUNTER_FACT)}
    fields.update(overrides)
    return divergence_package(**fields)


def package_with_a_causal_excerpt(text: str = EXPLANATORY_TEXT) -> StoryEvidencePackage:
    return divergence_package(explanatory_passages=(
        PackagedPassage(passage_id=EXPLANATORY_PASSAGE_ID, document_id=DOCUMENT_ID, text=text,
                        char_count=2285, passage_kind="narrative", excerpted=True,
                        role=EvidenceRole.CONTEXT,
                        char_start=1120, char_end=1120 + len(text),
                        query_terms=("adjusted gross margin", "2022Q3"), score=4.81),))


# -- a valid answer, and the fakes that return it ---------------------------------------------


def valid_answer(**overrides: Any) -> dict[str, Any]:
    """What a conformant model returns for `planning_package()` under `PLANNER_PROMPT_VERSION`
    2.0.0.

    Seven leaves and **not one identifier retyped**: `facts` holds the handles the prompt
    printed beside each reading (`F1` adjusted margin, `F2` GAAP margin, `F3` the counter-
    evidence reading), and `editorial_plan_from` resolves them back to `obs:` ids. Twelve fields
    the 1.2.0 answer carried are gone from the grammar and are filled by code — see
    `planner_schema`'s own table for which, and from what.
    """
    content: dict[str, Any] = {
        "thesis": "Opendoor's two definitions of gross margin were 15.9 points apart in 2022Q3.",
        "why_it_matters": "The adjusted figure is positive while the GAAP figure is not.",
        "uncertainty": "Only one filing backs both figures.",
        "key_points": [
            {"claim": "GAAP gross margin was (12.6)% in the third quarter of 2022.",
             "facts": ["F2"]},
            {"claim": "Adjusted gross margin was 3.3% over the same period.",
             "facts": ["F1"]},
        ],
        "counterpoint": "The same filing refuses to state cost of revenue as a metric, so the "
                        "reconciliation cannot be independently rebuilt from the package.",
        "counterpoint_facts": ["F3"],
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
                 raises: Exception | None = None, model_id: str = "Qwen3.5-9B-Q4_K_M.gguf",
                 provider_id: str = PROVIDER_LOCAL):
        self.content = dict(content if content is not None else valid_answer())
        self.raises = raises
        self.model_id = model_id
        # On the protocol since MULTI_PROVIDER_OPENAI §4.1, and defaulted to the boundary's own
        # constant rather than to a retyped string: the replay store keys on it, so a fake that
        # named a provider `providers/public.py` does not would key rows nothing can find.
        self.provider_id = provider_id
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


def plan_with(provider: StoryGenerationProvider, package: StoryEvidencePackage,
              offered: tuple[DerivationRequest, ...] = (), *,
              slots: Any = None,
              derived_facts: tuple[DerivedFact, ...] = (),
              spine: Any = None):
    """Every test goes through this, annotated with the protocol, so the surface under test is
    the contract rather than the concrete fake.

    `slots` defaults to **the table `story/pipeline.py` builds** — `slot_table(package, derived,
    ())`, with no passage rows, because `rests_on` left the writer's contract and a passage no
    sentence can name is a row no prompt should print. A test that says nothing about the table
    therefore gets the one the composition root would hand this stage, and `slots=()` stays
    reachable for the caller-forgot-the-table case.

    `offered` defaults to none, which is what `run_demo` now passes for `metric_move`: the spine
    executed what the detector fired on before this call, so there is nothing left to offer.
    """
    rows = slot_table(package, derived_facts, ()) if slots is None else slots
    return plan_story(package, provider=provider, offered=offered, slots=rows,
                      derived_facts=derived_facts, spine=spine,
                      max_tokens=PLANNER_MAX_TOKENS)


# -- §2's candidate: the one package in this file that supports a *change* --------------------
#
# The divergence package is two metrics in one period, so the only derivations it can support
# are `compare_levels` and `ratio`. DETERMINISTIC_FACT_TOOLS §2's measured failure is a change
# over two periods — `adjusted_gross_profit` from `556,000,000.0` in 2022Q2 to `110,000,000.0`
# in 2022Q3 — and an offer-set test run against a package that cannot express it would be
# testing the wrong shape. Built without table coordinates, because nothing in the planner's
# prompt or its rules resolves a cell: §11 shows the planner ids, values and excerpts.

AGP_PACKAGE_ID = "pkg:metric-move-adjusted-gross-profit-opendoor-2022q2-2022q3:7c1d0a5b93ef"
AGP_CANDIDATE_ID = ("cand:metric-move:adjusted-gross-profit:opendoor:"
                    "2022Q2_2022Q3:86ba9e13455d")
AGP_Q2_ID = "obs:adjusted-gross-profit:opendoor:2022Q2:normalized-table:0c4364ebbc44"
AGP_Q3_ID = "obs:adjusted-gross-profit:opendoor:2022Q3:normalized-table:4d66ef7200e9"


def agp_package(**overrides: Any) -> StoryEvidencePackage:
    def fact(observation_id: str, key: str, start: str, end: str, value: float) -> PackagedFact:
        return PackagedFact(
            observation_id=observation_id, metric_id="adjusted_gross_profit",
            metric_label="Adjusted Gross Profit", period_key=key,
            period_start=start, period_end=end, shape="quarter",
            value=value, unit="USD", currency="USD", scale="millions",
            printed_form=str(int(value / 1e6)), source_lane="normalized_table",
            validation_state="clean", passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
            quoted_text=str(int(value / 1e6)))

    fields: dict[str, Any] = dict(
        package_id=AGP_PACKAGE_ID, candidate_id=AGP_CANDIDATE_ID,
        detector_id="detector:metric_move", detector_version="1.0.0",
        policy_version="canon-policy:1.0.0", graph_run_id="graph-v1-0483dc6b4b10",
        graph_projection_version="1.2.0", extraction_run_id="extract-v1-lexical-833f7bcfbce9",
        run_complete_sha256="1cc8f7b01c040531" + "0" * 48,
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash=(
            "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34"),
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor",
                                entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(fact(AGP_Q2_ID, "2022Q2", "2022-04-01", "2022-06-30", 556_000_000.0),
               fact(AGP_Q3_ID, "2022Q3", "2022-07-01", "2022-09-30", 110_000_000.0)),
        metrics=(PackagedMetric(metric_id="adjusted_gross_profit",
                                label="Adjusted Gross Profit", unit="USD",
                                period_type="duration"),),
        primary_passages=(PackagedPassage(
            passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=PASSAGE_TEXT,
            char_count=2564, passage_kind="table", role=EvidenceRole.PRIMARY_SUPPORT),),
        documents=(PackagedDocument(document_id=DOCUMENT_ID, form="8-K"),),
        budget=PackageBudget(artifact_token_estimate=700, prompt_token_estimate=500,
                             section_counts={"facts": 2}, parameters=BudgetParameters()),
    )
    fields.update(overrides)
    return StoryEvidencePackage(**fields)


def agp_candidate() -> StoryCandidate:
    """The four signals `candidate.json` actually records for §2's candidate."""
    return StoryCandidate(
        candidate_id=AGP_CANDIDATE_ID, detector_id="detector:metric_move",
        detector_version="1.0.0", policy_version="canon-policy:1.0.0",
        graph_run_id="graph-v1-0483dc6b4b10", subject_entity_id="opendoor",
        story_type="metric_move", metric_ids=("adjusted_gross_profit",),
        anchor_period_keys=("2022Q2", "2022Q3"),
        anchor_observation_ids=(AGP_Q2_ID, AGP_Q3_ID),
        signals={"crosses_zero": False, "delta": -446_000_000.0, "delta_pct": -80.215827338,
                 "direction": "decrease", "period_shape": "quarter", "polarity": "revenue"},
        evidence_request=EvidenceRequest(
            metric_ids=("adjusted_gross_profit",), period_keys=("2022Q2", "2022Q3"),
            observation_ids=(AGP_Q2_ID, AGP_Q3_ID)))


def agp_derived(package: StoryEvidencePackage | None = None) -> tuple[DerivedFact, ...]:
    """The `absolute_change` §2 is about, computed by the stage that computes it live.

    Built by `execute` rather than typed out, so the row the planner resolves a `D1` handle
    against is the row the derivation stage really mints — id, inputs and all. A hand-written
    `DerivedFact` would be this file inventing the id the plan then resolves to.
    """
    package = package if package is not None else agp_package()
    candidate = agp_candidate()
    fact = execute(
        DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                          from_fact_id=AGP_Q2_ID, to_fact_id=AGP_Q3_ID),
        package, candidate,
        direction=detector_config.quantity_direction,
        offered=offers(package, candidate))
    assert isinstance(fact, DerivedFact), fact
    return (fact,)


def agp_answer(**overrides: Any) -> dict[str, Any]:
    """A 2.0.0 plan over §2's package. `F1` is the 2022Q2 reading, `F2` the 2022Q3 one.

    The package carries no counter-evidence, so the counterpoint is empty and
    `plan_violations` asks for none.
    """
    content = valid_answer(
        thesis="Adjusted gross profit fell between the second and third quarters of 2022.",
        why_it_matters="It is the largest quarter-over-quarter fall in the series.",
        key_points=[{"claim": "Adjusted gross profit was $110 million in 2022Q3.",
                     "facts": ["F2"]}],
        counterpoint="",
        counterpoint_facts=[])
    content.update(overrides)
    return content


def agp_plan(package: StoryEvidencePackage, **overrides: Any) -> EditorialPlan:
    """A hand-built accepted plan over §2's package.

    **Hand-built on purpose.** The four §11 rules below it — `derivation_not_offered`,
    `unknown_warning_code`, `unresolvable_fact_id`, `unresolvable_passage_id` — guard fields
    2.0.0's grammar no longer has, so no answer can reach them through `editorial_plan_from`.
    They are still implemented, they still judge a plan read back from a 1.2.0 artifact or
    written by hand, and `plan_violations` is pure, so this is what drives them.
    """
    fields: dict[str, Any] = dict(
        candidate_id=package.candidate_id, package_id=package.package_id,
        thesis="Adjusted gross profit fell between the second and third quarters of 2022.",
        why_it_matters="It is the largest quarter-over-quarter fall in the series.",
        key_points=(KeyPoint(claim="Adjusted gross profit was $110 million in 2022Q3.",
                             required_fact_ids=(AGP_Q3_ID,),
                             required_citation_passage_ids=(PASSAGE_ID,),
                             statement_class=StatementClass.REPORTED),),
        causal_language=CausalLanguage.FORBIDDEN)
    fields.update(overrides)
    return EditorialPlan(**fields)


# -- the two recorded runs this change was measured on ----------------------------------------
#
# `data/` is gitignored, so every claim that must hold from a clean checkout is made against the
# fixtures under `tests/story/fixtures/`; these two read the run directories and skip when they
# are absent, exactly as `test_story_composition_recovery.py` does for its sweep. What they buy
# is the only honest form of the spine claim: `story-v1-76da8465cd95` is the run whose plan said
# *"rose"* over 556 -> 110 and reached a writer, and a synthetic plan saying "rose" would prove
# that a string matches a lexicon rather than that this check would have stopped that run.

DEMO_RUNS = REPO_ROOT / "data" / "story_demo"

#: The inverted plan: gpt-5-nano's thesis for a **decrease** opens *"adjusted gross profit rose"*.
INVERTED_RUN = "story-v1-76da8465cd95"
#: The correct plan for the identical candidate: *"fell from 556 million USD … to 110 million"*.
CORRECT_RUN = "story-v1-1daff167348f"


def recorded_run(run: str) -> tuple[StoryEvidencePackage, StoryCandidate, EditorialPlan,
                                    tuple[DerivedFact, ...]]:
    """One recorded run's package, candidate, plan and derived facts, or a skip."""
    directory = DEMO_RUNS / run
    if not directory.is_dir():
        pytest.skip(f"{directory} is absent; data/ is gitignored and no demo has been run here")
    package = StoryEvidencePackage.model_validate_json(
        (directory / "evidence_package.json").read_text(encoding="utf-8"))
    candidate = StoryCandidate.model_validate_json(
        (directory / "candidate.json").read_text(encoding="utf-8"))
    plan = EditorialPlan.model_validate_json(
        (directory / "editorial_plan.json").read_text(encoding="utf-8"))
    derived = tuple(DerivedFact.model_validate(row) for row in json.loads(
        (directory / "derived_facts.json").read_text(encoding="utf-8"))["facts"])
    return package, candidate, plan, derived


def recorded_spine(run: str):
    """That run's `StorySpine`, built by `spine_for` from its own three artifacts."""
    package, candidate, plan, derived = recorded_run(run)
    spine = spine_for(candidate, package, derived,
                      causal_language=causal_language_for(package))
    assert spine is not None, f"{run} is a metric_move run and must have a spine"
    return package, plan, spine


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
    parameters = inspect.signature(plan_story).parameters
    # `offered` is the one addition and it does not weaken the claim: it is a pure function of
    # the package and the candidate, computed before this call by a stage with no model in it,
    # so it is one more thing the plan cannot influence rather than a channel through which it
    # could. There is still no `terms`, no retriever and no configuration parameter.
    # `slots`, `derived_facts` and `spine` joined `offered` at 2.0.0 and are the same kind of
    # argument as it: each is computed by a stage this one may not import, before any model is
    # called, and handed in by `story/pipeline.py`. The claim is unweakened — there is still no
    # `terms`, no retriever and no configuration parameter — and it is now stated four times
    # over, because a table built twice is a table that can disagree with the one the model saw.
    # `feedback` is the repair route's, and it is the one argument here that *is* downstream of
    # a model: it carries a previous attempt's refusal back into the prompt. It cannot widen the
    # evidence — `_feedback_lines` appends prose under its own heading and touches no section
    # above it — and it defaults to `""`, which renders nothing at all so a first attempt keys
    # identically in the replay store to the run that had no repair.
    assert list(parameters) == ["package", "provider", "max_tokens", "offered", "slots",
                                "derived_facts", "spine", "feedback"]
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


def test_the_planner_schema_is_inside_the_portable_subset():
    """`planner_schema()` takes no argument at 2.0.0 — it was parametrised by `causal_language`,
    and that field left the grammar because the model could not influence it."""
    assert list(inspect.signature(planner_schema).parameters) == []
    validate_portable_schema(planner_schema())


def walk_schema(schema: Mapping[str, Any], path: str = "$"):
    yield path, schema
    for name, subschema in (schema.get("properties") or {}).items():
        yield from walk_schema(subschema, f"{path}.{name}")
    if isinstance(schema.get("items"), Mapping):
        yield from walk_schema(schema["items"], f"{path}[]")


def test_the_planner_schema_uses_no_keyword_llama_cpp_would_silently_drop():
    """`minItems`, `pattern` and `anyOf` are dropped by the grammar *and* ignored by the local
    checker, so a schema using one is unconstrained without saying so (§15.3)."""
    for path, subschema in walk_schema(planner_schema()):
        assert set(subschema) <= PORTABLE_KEYWORDS, f"{path}: {sorted(subschema)}"


def test_every_object_in_the_planner_schema_forbids_extras_and_requires_every_property():
    """S6's three structural rules, asserted on the shipped schema rather than assumed."""
    schema = planner_schema()
    for path, subschema in walk_schema(schema):
        if subschema.get("type") == "object":
            assert subschema.get("additionalProperties") is False, path
            assert sorted(subschema["required"]) == sorted(subschema["properties"]), path
        if subschema.get("type") == "array":
            assert isinstance(subschema.get("items"), Mapping), path


def test_the_schema_is_seven_leaves_and_not_one_identifier_the_model_must_retype():
    """2.0.0's whole shape, asserted over the schema rather than described in a docstring.

    **Nineteen leaves became seven**, and the two that used to hold 70-character `obs:` and
    `norm:` digests hold two-character handles instead. Asserted as a *closed* set — a field
    coming back somewhere else is exactly the failure this guards, so the walk covers the whole
    schema and not only the properties that used to hold them.
    """
    schema = planner_schema()
    assert sorted(schema["required"]) == sorted(schema["properties"]) == [
        "counterpoint", "counterpoint_facts", "key_points", "thesis", "uncertainty",
        "why_it_matters"]
    point = schema["properties"]["key_points"]["items"]
    assert sorted(point["required"]) == sorted(point["properties"]) == ["claim", "facts"]
    assert point["properties"]["facts"] == {"type": "array", "items": {"type": "string"}}

    leaves = [path for path, subschema in walk_schema(schema)
              if subschema.get("type") in ("string", "number", "boolean")]
    assert len(leaves) == 7, leaves

    flattened = json.dumps(schema)
    for retired in ("causal_language", "requested_derivations", "operation", "from_fact_id",
                    "to_fact_id", "required_fact_ids", "required_citation_passage_ids",
                    "statement_class", "required_warnings", "structure", "prohibited_claims",
                    "unusable_evidence", "reason", "angle"):
        assert retired not in flattened, retired


# `test_the_schema_admits_exactly_the_causal_language_code_computed` and
# `test_the_unusable_evidence_reason_is_an_enum_and_not_a_free_string` stood here and are
# **retired with the two fields they described**. `causal_language` is no longer in the model's
# grammar at all — `test_the_planner_stamps_the_computed_causal_language_over_the_models_answer`
# below is where "code computes it, the model does not" is still asserted — and
# `unusable_evidence` is code-pinned empty, so there is no `reason` property for an enum to
# constrain. `UnusableReason` remains a frozen enum on `UnusableEvidence`, which is what still
# refuses a free-text reason in a plan read back from a 1.2.0 artifact.


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
                        char_count=2564, passage_kind="table",
                        role=EvidenceRole.PRIMARY_SUPPORT),))
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
    """§11: computed by code before the call, never chosen by the model — and never read back.

    **At 2.0.0 there is no field in the grammar for the model to state it in**, so the answer
    below carries one the way a plan replayed from a 1.2.0 store does: as a key nothing reads.
    The plan's `causal_language` is `causal_language_for(package)` and can be nothing else.
    """
    package = planning_package()
    plan = editorial_plan_from(valid_answer(causal_language="reported_only"), package,
                               model_id="Qwen3.5-9B-Q4_K_M.gguf",
                               slots=slot_table(package, (), ()))
    assert plan.causal_language is CausalLanguage.FORBIDDEN
    assert "causal_language" not in planner_schema()["properties"]


def test_the_request_carries_the_pinned_temperature_the_schema_name_and_the_system_message():
    provider = FakePlanProvider()
    plan_with(provider, planning_package())
    call = provider.calls[0]
    assert call["temperature"] == PINNED_TEMPERATURE == 0.0
    assert call["schema_name"] == PLANNER_SCHEMA_NAME
    assert call["max_tokens"] == PLANNER_MAX_TOKENS
    assert call["schema"] == planner_schema()
    assert call["system"] == PLANNER_SYSTEM


# -- rule: every id must resolve in the package -----------------------------------------------


def test_a_plan_naming_a_handle_this_runs_rows_do_not_print_is_rejected():
    """`unresolvable_fact_handle` — 2.0.0's replacement for `unresolvable_fact_id`.

    The model names `F1`, `D1`; the row is the only authority on which of those exist, so an
    `F9` is refused against *this run's table* rather than against the package. The refusal
    prints the handles that were printed, because a repair has to be able to pick one.
    """
    package = planning_package()
    answer = valid_answer()
    answer["key_points"][0]["facts"] = ["F9"]
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), package)
    assert UNRESOLVABLE_FACT_HANDLE in raised.value.codes
    assert "'F9'" in str(raised.value) and "'F1'" in str(raised.value)


def test_a_passage_handle_is_not_a_fact_handle_and_is_refused_as_one():
    """A `P` row is in the table and is not something a key point may rest on.

    The pipeline prints no `P` rows at all now, so this only arises for a caller that passes
    some — but the rule belongs to `_resolved_facts` rather than to the caller, and a passage
    silently resolving to no fact id would leave a key point resting on nothing.
    """
    package = planning_package()
    rows = slot_table(package, (), package.primary_passages)
    assert [row.handle for row in rows][-1] == "P1"
    answer = valid_answer()
    answer["key_points"][0]["facts"] = ["P1"]
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), package, slots=rows)
    assert UNRESOLVABLE_FACT_HANDLE in raised.value.codes


def test_an_f_handle_resolves_to_its_observation_and_code_looks_the_passage_up():
    """The two fields the model stopped writing, and what code writes instead.

    `required_fact_ids` is the row's `fact_id`; `required_citation_passage_ids` is
    `PackagedFact.passage_id` for exactly those facts, which is the lookup 1.2.0 asked the model
    to perform and could only refuse it for getting wrong. `statement_class` follows the
    handles: only `F` rows named, so `reported`.
    """
    package = planning_package()
    plan = plan_with(FakePlanProvider(), package).plan

    assert [point.required_fact_ids for point in plan.key_points] == [
        (GAAP_FACT_ID,), (ADJUSTED_FACT_ID,)]
    assert [point.required_citation_passage_ids for point in plan.key_points] == [
        (PASSAGE_ID,), (PASSAGE_ID,)]
    assert [point.statement_class for point in plan.key_points] == [
        StatementClass.REPORTED, StatementClass.REPORTED]
    # The counterpoint's handle resolves the same way, onto the counter-evidence passage.
    assert plan.counterpoints[0].required_fact_ids == (COUNTER_FACT_ID,)
    assert plan.counterpoints[0].required_citation_passage_ids == (COUNTER_PASSAGE_ID,)


def test_a_d_handle_resolves_to_its_two_input_observations_and_makes_the_point_calculated():
    """§11 rule 1 restricts `required_fact_ids` to `obs:` ids, and a derived fact is not one.

    So a point built on `D1` requires the two readings `D1` was computed from — which is what
    the point actually rests on — its passages are those two facts', and `statement_class`
    becomes `calculated` because a derived row was named. The derived fact is the derivation
    stage's own, not a hand-written one.
    """
    package, derived = agp_package(), agp_derived()
    answer = agp_answer(key_points=[
        {"claim": "Adjusted gross profit fell by $446 million.", "facts": ["D1"]},
        {"claim": "Adjusted gross profit was $110 million in 2022Q3.", "facts": ["F2"]}])
    plan = plan_with(FakePlanProvider(answer), package, derived_facts=derived).plan

    assert plan.key_points[0].required_fact_ids == (AGP_Q2_ID, AGP_Q3_ID)
    assert plan.key_points[0].required_citation_passage_ids == (PASSAGE_ID,)
    assert plan.key_points[0].statement_class is StatementClass.CALCULATED
    # The `fact:derived:` id itself is never in the plan: §11 has no room for one, and the
    # derived row is reached through its two inputs.
    assert derived[0].fact_id not in str(plan.key_points[0].required_fact_ids)
    # …and the sibling point naming only an `F` row stays `reported`, so the class follows the
    # handles rather than the plan.
    assert plan.key_points[1].statement_class is StatementClass.REPORTED


def test_the_two_id_rules_still_judge_a_plan_read_back_or_written_by_hand():
    """`unresolvable_fact_id` and `unresolvable_passage_id` are unreachable from a 2.0.0 answer
    and are not dead: `plan_violations` is pure, and a 1.2.0 `editorial_plan.json` replayed
    through the demo UI is judged by exactly these two clauses."""
    package = agp_package()
    invented = agp_plan(package, key_points=(KeyPoint(
        claim="Adjusted gross profit was $110 million in 2022Q3.",
        required_fact_ids=("obs:adjusted-ebitda:invented:2022Q3",),
        required_citation_passage_ids=(DOCUMENT_ID + "#p99",),
        statement_class=StatementClass.REPORTED),))
    assert [violation.code for violation in plan_violations(invented, package)] == [
        UNRESOLVABLE_FACT_ID, UNRESOLVABLE_PASSAGE_ID]


def test_a_plan_requiring_a_warning_the_package_does_not_carry_is_rejected():
    """`unknown_warning_code`, driven directly for the reason above: `required_warnings` is
    code-filled at 2.0.0, so no answer can name a warning the package does not carry."""
    package = agp_package()
    plan = agp_plan(package, required_warnings=("single_source",))
    assert [violation.code for violation in plan_violations(plan, package)] == [
        UNKNOWN_WARNING_CODE]


# -- rule: a warning that qualifies no claim may not demand a sentence -------------------------


def _package_with_provenance() -> StoryEvidencePackage:
    """`planning_package()` plus the two build-provenance warnings its real build raises."""
    return planning_package(warnings=(
        PackagedWarning(code="filing_date_unknown", severity=Severity.ANNOTATE,
                        subject_ids=(CANDIDATE_ID,),
                        detail="the candidate's own filing date is not knowable"),
        PackagedWarning(code="token_budget_trimmed", severity=Severity.WARN,
                        kind=WarningKind.BUILD_PROVENANCE,
                        detail="§10.2's 5,000-token estimate was exceeded and rows were cut"),
        PackagedWarning(code="evidence_sources_absent_in_v1", severity=Severity.ADVISORY,
                        kind=WarningKind.BUILD_PROVENANCE,
                        detail="zero :EvidenceSource nodes exist"),
    ))


def test_build_provenance_never_becomes_a_warning_the_post_has_to_state():
    """§10.1's warnings serve two audiences and only one of them is the reader.

    The recorded Qwen run named all five of the package's build-provenance codes in
    `required_warnings`, and §13 then demanded five sentences of plumbing from an investor
    post — five of the nine blocking findings *(measured 2026-08-04)*.

    **At 2.0.0 the model is not asked at all**: `required_warnings` is code-filled from
    `claim_qualifying_warnings` over the package's own codes, which is the same filter applied
    to the whole admissible set rather than to whatever the model happened to name. The answer
    below carries no warning field, and the plan carries the one claim-qualifying code anyway.
    """
    package = _package_with_provenance()
    planned = plan_with(FakePlanProvider(), package)

    assert planned.plan.required_warnings == ("filing_date_unknown",)
    # The provenance did not disappear — it is still on the package, for the evidence panel and
    # the manifest. It simply no longer demands prose.
    assert {warning.code for warning in package.warnings} == {
        "filing_date_unknown", "token_budget_trimmed", "evidence_sources_absent_in_v1"}


def test_a_claim_qualifying_warning_is_still_the_writers_to_state():
    """The disclosure rule is untouched: the filter drops provenance and nothing else."""
    assert plan_with(FakePlanProvider(), _package_with_provenance()).plan.required_warnings == (
        "filing_date_unknown",)


# `test_a_warning_the_package_does_not_carry_is_still_rejected_and_not_quietly_dropped` stood
# here and is **retired on this route only**. It drove `unknown_warning_code` through a model
# answer naming `["token_budget_trimmed", "invented_code"]`, and 2.0.0's grammar has no
# `required_warnings` for a model to name anything in. The rule itself is unchanged and is
# driven directly, against a hand-built plan, by
# `test_a_plan_requiring_a_warning_the_package_does_not_carry_is_rejected` above.


def test_a_warning_that_states_no_kind_is_treated_as_one_the_post_must_state():
    """The default points at disclosure: provenance misfiled refuses loudly, and a qualifier
    misfiled would go unsaid in silence."""
    package = planning_package(warnings=(
        PackagedWarning(code="filing_date_unknown", severity=Severity.ANNOTATE),))
    assert package.warnings[0].kind is WarningKind.CLAIM_QUALIFYING
    assert plan_with(FakePlanProvider(), package).plan.required_warnings == (
        "filing_date_unknown",)


def test_a_plan_with_no_key_point_is_rejected():
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(valid_answer(key_points=[])), planning_package())
    assert NO_KEY_POINTS in raised.value.codes


# -- rule: counterpoints, and the three things that close the vacuity hole --------------------


def test_a_plan_with_no_counterpoint_is_rejected_when_the_package_holds_counter_evidence():
    """§15.3 forbids `minItems`, so "non-empty" is a post-hoc code check or it is nothing.

    At 2.0.0 the empty counterpoint is the empty **string** rather than the empty array: the
    field became prose plus handles, and `editorial_plan_from` reads a blank claim as none.
    """
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(valid_answer(counterpoint="", counterpoint_facts=[])),
                  planning_package())
    assert COUNTERPOINT_MISSING in raised.value.codes


def test_a_counterpoint_resting_on_no_counter_evidence_id_is_rejected():
    """§11 point 1: ids drawn from `counter_evidence`, not from anywhere in the package.

    `F1` is the adjusted-margin reading from the *primary* passage, so the resolved fact and its
    resolved passage are both outside the counter-evidence set.
    """
    answer = valid_answer(counterpoint="Margins vary.", counterpoint_facts=["F1"])
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), planning_package())
    assert COUNTERPOINT_UNGROUNDED in raised.value.codes


def test_a_counterpoint_carrying_no_handle_at_all_is_rejected_at_construction():
    """`{claim: "Margins vary.", required_fact_ids: []}` satisfied the first draft literally.

    The 2.0.0 spelling of the same vacuity is a `counterpoint` with an empty `counterpoint_facts`
    — prose and nothing behind it — and `Counterpoint` still refuses it at construction.
    """
    answer = valid_answer(counterpoint="Margins vary.", counterpoint_facts=[])
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(answer), planning_package())
    assert raised.value.codes == (PLAN_NOT_CONSTRUCTIBLE,)
    assert "counterpoint" in str(raised.value)


def test_a_counterpoint_grounded_in_a_fact_read_from_a_counter_evidence_passage_is_accepted():
    """The accepting half — and under 2.0.0 it is the **only** way a counterpoint is grounded.

    A plan no longer writes passage ids, and the pipeline's slot table prints no `P` rows, so
    naming a fact read from a counter-evidence passage is the whole of what a counterpoint can
    do. `planning_package()`'s docstring records what that costs a package without such a fact.
    """
    plan = plan_with(FakePlanProvider(), planning_package()).plan
    assert plan.counterpoints[0].required_fact_ids == (COUNTER_FACT_ID,)
    assert plan.counterpoints[0].required_citation_passage_ids == (COUNTER_PASSAGE_ID,)


def test_a_counter_evidence_item_the_plan_neither_used_nor_declared_unusable_is_rejected():
    """Still reachable from a 2.0.0 answer, because `unusable_evidence` is pinned empty: a
    second counter-evidence item the plan's handles do not reach is accounted for by nothing."""
    package = planning_package(counter_evidence=(
        *divergence_package().counter_evidence,
        PackagedPassage(passage_id=DOCUMENT_ID + "#p12", document_id=DOCUMENT_ID,
                        text="[refused: UNRESOLVED_METRIC — 'Basic' matches no metric]",
                        char_count=1804, excerpted=True, char_start=0, char_end=55,
                        role=EvidenceRole.COUNTER_EVIDENCE,
                        match_basis=MATCH_BASIS_SAME_DOCUMENT),))
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(FakePlanProvider(valid_answer()), package)
    assert COUNTER_EVIDENCE_UNACCOUNTED in raised.value.codes
    assert DOCUMENT_ID + "#p12" in str(raised.value)


def test_an_unused_counter_evidence_item_declared_with_an_enum_reason_is_accounted_for():
    """`unusable_evidence` is code-pinned empty at 2.0.0, so this drives `plan_violations`
    directly — the clause is unchanged and still judges a 1.2.0 plan read back from disk."""
    package = planning_package(counter_evidence=(
        *divergence_package().counter_evidence,
        PackagedPassage(passage_id=DOCUMENT_ID + "#p12", document_id=DOCUMENT_ID,
                        text="[refused: UNRESOLVED_METRIC — 'Basic' matches no metric]",
                        char_count=1804, excerpted=True, char_start=0, char_end=55,
                        role=EvidenceRole.COUNTER_EVIDENCE,
                        match_basis=MATCH_BASIS_SAME_DOCUMENT),))
    accounted = plan_with(FakePlanProvider(), planning_package()).plan.model_copy(update={
        "package_id": package.package_id,
        "unusable_evidence": (UnusableEvidence(id=DOCUMENT_ID + "#p12",
                                               reason=UnusableReason.OUTSIDE_THESIS_SCOPE),)})
    assert plan_violations(accounted, package) == ()
    # …and with the declaration removed, the same plan is refused. Which half moved is the
    # `unusable_evidence` tuple and nothing else.
    unaccounted = accounted.model_copy(update={"unusable_evidence": ()})
    assert [v.code for v in plan_violations(unaccounted, package)] == [
        COUNTER_EVIDENCE_UNACCOUNTED]


# `test_a_free_text_reason_for_unusable_evidence_is_refused_by_the_grammar` and
# `test_a_free_text_reason_is_still_refused_when_the_schema_check_is_bypassed` stood here and
# are **retired with the field they guarded**: 2.0.0's grammar has no `unusable_evidence`, so
# there is no free-text `reason` for a schema or a parser to refuse. `UnusableReason` is still a
# frozen enum on `UnusableEvidence`, which is what refuses one in a plan read back from a 1.2.0
# artifact, and `story/core/models.py` is where that construction is exercised.


def test_unusable_evidence_naming_an_item_the_package_does_not_hold_is_rejected():
    """`unknown_unusable_id`, driven directly for the same reason as the two above."""
    package = planning_package()
    plan = plan_with(FakePlanProvider(), package).plan.model_copy(update={
        "unusable_evidence": (UnusableEvidence(id="psg:not-in-this-package",
                                               reason=UnusableReason.DIFFERENT_POPULATION),)})
    assert [v.code for v in plan_violations(plan, package)] == [UNKNOWN_UNUSABLE_ID]
    assert "unusable_evidence names" in str(plan_violations(plan, package)[0])


def test_a_package_with_no_counter_evidence_accepts_a_plan_with_no_counterpoint():
    plan = plan_with(FakePlanProvider(valid_answer(counterpoint="", counterpoint_facts=[])),
                     package_without_counter_evidence()).plan
    assert plan.counterpoints == ()


# -- rule: malformed output fails clearly, and is never retried --------------------------------


def test_content_that_is_not_json_fails_as_a_response_error_and_is_asked_for_once():
    provider = FakePlanProvider(raises=StoryProviderResponseError(
        "assistant content is not JSON: Expecting value: line 1 column 1"))
    with pytest.raises(StoryProviderResponseError):
        plan_with(provider, planning_package())
    assert len(provider.calls) == 1


def test_an_answer_missing_a_required_property_fails_as_a_schema_error_and_is_never_retried():
    answer = valid_answer()
    del answer["why_it_matters"]
    provider = FakePlanProvider(answer)
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(provider, planning_package())
    assert any("why_it_matters" in violation for violation in raised.value.violations)
    assert len(provider.calls) == 1


def test_an_answer_of_the_wrong_shape_fails_as_a_schema_error_rather_than_a_plan_rejection():
    provider = FakePlanProvider(valid_answer(key_points="two of them"))
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(provider, planning_package())
    assert any("expected array" in violation for violation in raised.value.violations)


def test_a_field_the_2_0_0_grammar_dropped_is_refused_rather_than_ignored():
    """`additionalProperties: false` is what makes each removal a refusal rather than a hope.

    `test_a_statement_class_outside_the_enum_fails_as_a_schema_error` stood here and is retired
    with its field; what replaces it is stronger. The 1.2.0 answer's own `statement_class` is
    replayed against the 2.0.0 schema and is not tolerated, not ignored and not silently
    dropped — it fails `schema_violations` before a plan exists, which is the treatment any
    other invented field gets.
    """
    answer = valid_answer()
    answer["key_points"][0]["statement_class"] = "reported"
    with pytest.raises(StoryProviderSchemaError) as raised:
        plan_with(FakePlanProvider(answer), planning_package())
    assert any("statement_class" in violation for violation in raised.value.violations)


def test_a_schema_violation_and_a_plan_rejection_are_different_failures():
    """One is a statement about the runtime, the other about §11; a caller must tell them apart."""
    assert not issubclass(EditorialPlanRejected, StoryProviderSchemaError)
    assert not issubclass(StoryProviderSchemaError, EditorialPlanRejected)


# -- the accepted plan ------------------------------------------------------------------------


def test_a_conformant_answer_becomes_a_plan_whose_every_id_resolves_in_the_package():
    package = planning_package()
    planned = plan_with(FakePlanProvider(), package)
    assert plan_violations(planned.plan, package) == ()
    assert planned.plan.candidate_id == CANDIDATE_ID
    assert planned.plan.package_id == PACKAGE_ID
    assert planned.plan.prompt_version == PLANNER_PROMPT_VERSION
    assert planned.plan.model_id == "Qwen3.5-9B-Q4_K_M.gguf"
    assert planned.plan.key_points[0].statement_class is StatementClass.REPORTED
    # **The whole admissible set, not the model's selection.** `required_warnings` is code-filled
    # at 2.0.0 from `claim_qualifying_warnings` over every code the package declares, so both of
    # this package's claim-qualifying warnings are demanded — the 1.2.0 answer named one of them
    # and the other went unsaid, which is the failure mode pinning it removes.
    assert planned.plan.required_warnings == (
        "filing_date_unknown", "counter_evidence_same_document")
    assert planned.generation.total_tokens == 1152


def test_the_identity_fields_come_from_the_package_and_not_from_the_model():
    """A model that returned a different candidate id could not re-key the plan."""
    answer = valid_answer()
    answer["thesis"] = "cand:invented:9999"
    plan = plan_with(FakePlanProvider(answer), planning_package()).plan
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
    package = planning_package()
    store = GenerationStore()
    # The two settings `story-generation-v3` added are stated here because the store is empty
    # and `FakePlanProvider` publishes no `StoryProviderConfig` to read them off. They are the
    # local server's — the pinned temperature reaches the wire, no `reasoning` block is sent.
    recording = ReplayingStoryGenerationProvider(
        store, FakePlanProvider(), temperature_sent=True, reasoning_effort=None,
        prompt_version=PLANNER_PROMPT_VERSION)
    first = plan_with(recording, package).plan
    assert store.identity_provider_id() == PROVIDER_LOCAL

    # **Nothing** is passed: the provider, the model and — since `story-generation-v3` — both
    # request settings are read back off the rows the recording wrote, which is the property
    # that makes a written store sufficient to replay from on its own.
    replaying: StoryGenerationProvider = ReplayingStoryGenerationProvider(store)
    second = plan_with(replaying, package).plan
    assert second == first
    assert len(store) == 1


def test_a_conflict_and_an_ambiguity_reach_the_prompt_because_the_post_must_carry_them():
    package = planning_package(conflicts=(
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
    UNRESOLVABLE_FACT_ID, UNRESOLVABLE_PASSAGE_ID, UNRESOLVABLE_FACT_HANDLE,
    COUNTERPOINT_MISSING, COUNTERPOINT_UNGROUNDED, COUNTER_EVIDENCE_UNACCOUNTED,
    UNKNOWN_WARNING_CODE, UNKNOWN_UNUSABLE_ID, CAUSAL_LANGUAGE_NOT_COMPUTED,
    DIRECTION_CONTRADICTS_SPINE, DERIVATION_NOT_OFFERED, THESIS_EMPTY, NO_KEY_POINTS,
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

    package = planning_package()
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


# ---------------------------------------------------------------------------------------
# DETERMINISTIC_FACT_TOOLS §4.3 and §5 — the offer set, and what may be requested from it
# ---------------------------------------------------------------------------------------


def offer_line(request: DerivationRequest) -> str:
    """One offer, spelled the way the prompt must print it and the answer must spell it back."""
    return (f'  operation "{request.operation.value}"  '
            f'from_fact_id "{request.from_fact_id}"  to_fact_id "{request.to_fact_id}"')


# `as_request` stood here — one offered triple as the JSON object a 1.2.0 answer spelled it back
# as — and is gone with the field. 2.0.0's grammar has no `requested_derivations`, so there is
# nothing left for a model to spell back and nothing to convert for it.


def test_the_derivation_vocabulary_is_exactly_the_seven_operations_and_nothing_else():
    """§4.1: seven, and there is no eighth.

    **The assertion moved off the schema, because `requested_derivations` left the grammar.**
    The planner no longer asks for a derivation at all — `story/core/spine.py` executes what the
    detector fired on before the call — so what is left to hold is the vocabulary itself, and
    the offer set is where it now reaches a caller. `test_an_operation_outside_the_seven_fails_as
    _a_schema_error_and_not_as_a_plan_rejection` is retired with the field: there is no
    `operation` property for a word to be outside of.
    """
    assert [member.value for member in DerivationOperation] == [
        "absolute_change", "percentage_change", "percentage_point_change", "compare_levels",
        "ratio", "crossed_zero", "trend_direction"]
    package, candidate = agp_package(), agp_candidate()
    assert {request.operation for request in offers(package, candidate)} <= set(
        DerivationOperation)
    assert "requested_derivations" not in planner_schema()["properties"]


def test_the_offer_set_reaches_the_prompt_spelled_exactly_as_it_must_be_requested():
    """§4.3: the planner may request only a triple from the list it was shown.

    **`run_demo` passes `offered=()` for `metric_move` now**, so this section is printed only
    for a caller that still has something to offer — a story type whose derivations code has not
    already executed. The rendering rule is unchanged and is asserted here because the day such
    a caller exists is not the day to discover the prompt drifted.

    That promise is a string comparison — `plan_violations` compares whole `DerivationRequest`s
    against the same tuple — so the rendering is checked the way the existing id rule is: every
    offer appears under the three field names it must be spelled back with, and every one of
    them parses back out of the prompt into the object it came from.
    """
    package, candidate = agp_package(), agp_candidate()
    offered = offers(package, candidate)
    prompt = planner_prompt(package, offered=offered)

    # Three, one orientation of the one pair for each of the three operations this package's
    # unit admits. It was six until H2's F5 withdrew the reversed half of every two-period
    # pair, each of which the verifier refused as `derived_fact_orientation_reversed`.
    assert len(offered) == 3, [o.operation.value for o in offered]
    assert f"DERIVATIONS OFFERED ({len(offered)} available" in prompt
    for request in offered:
        assert offer_line(request) in prompt
    # The gloss beside each triple carries the two readings and no id, so a 9B model can tell
    # two offers apart without parsing a digest and cannot read a figure out of the section.
    assert "adjusted_gross_profit 2022Q2 (556000000.0 USD) -> " \
           "adjusted_gross_profit 2022Q3 (110000000.0 USD)" in prompt

    printed = [line for line in prompt.splitlines() if line.startswith('  operation "')]
    assert printed == [offer_line(request) for request in offered]


def test_a_prompt_with_no_offer_prints_no_derivations_section_at_all():
    """The inversion 2.0.0 makes, and the reason for it.

    Until 1.2.0 an empty offer set printed *"DERIVATIONS OFFERED (none; this package supports no
    derivation…)"*, on the rule that an absent section reads as an omission. That rule stands
    where the model may still ask for something. It does not stand here: `run_demo` passes
    `offered=()` for every `metric_move` candidate because the spine has already executed what
    the detector fired on, so a heading offering nothing would be an invitation to a refusal —
    the plan has no field left to request one in either.
    """
    prompt = planner_prompt(divergence_package())
    assert "DERIVATIONS OFFERED" not in prompt


def test_a_2_0_0_plan_requests_no_derivation_at_all_and_code_says_why():
    """The field is not merely unused — it is empty by construction, and this is the assertion.

    `story/core/spine.py` executes the operations the detector fired on **before** the planner
    call, so a plan that could still ask would be asking for a second copy of what already
    exists. `requested_derivations` is therefore `()` whatever the answer says, and the three
    tests below drive `plan_violations` directly because no answer can reach them any more.
    """
    package = agp_package()
    planned = plan_with(FakePlanProvider(agp_answer()), package)
    assert planned.plan.requested_derivations == ()


def test_an_offered_triple_is_granted_by_the_rule_that_still_guards_a_hand_built_plan():
    """The accepting half, so the refusal below is a statement about the offer set and not
    about `requested_derivations` being unusable."""
    package, candidate = agp_package(), agp_candidate()
    offered = offers(package, candidate)
    wanted = tuple(request for request in offered
                   if request.operation is DerivationOperation.ABSOLUTE_CHANGE
                   and request.from_fact_id == AGP_Q2_ID)
    assert wanted

    plan = agp_plan(package, requested_derivations=wanted)
    assert plan.requested_derivations[0].operation is DerivationOperation.ABSOLUTE_CHANGE
    assert plan_violations(plan, package, offered=offered) == ()


def test_a_requested_derivation_outside_the_offer_set_is_refused_before_the_writer_runs():
    """§4.3's refusal, and it fires at §11 rather than at the executor.

    `percentage_point_change` over two USD readings would be refused by §4.2's unit clause too,
    so the plan names a quantity nothing will compute — and discovering that after a writer call
    has been paid for helps nobody. `derivation_not_offered` is spelled the way §6 spells the
    verifier's own gate code, so one fault reads as one string wherever it is reported.
    """
    package, candidate = agp_package(), agp_candidate()
    offered = offers(package, candidate)
    plan = agp_plan(package, requested_derivations=(DerivationRequest(
        operation=DerivationOperation.PERCENTAGE_POINT_CHANGE,
        from_fact_id=AGP_Q2_ID, to_fact_id=AGP_Q3_ID),))

    found = plan_violations(plan, package, offered=offered)
    assert [violation.code for violation in found] == [DERIVATION_NOT_OFFERED]
    assert "percentage_point_change" in str(found[0])


def test_reversing_the_two_ids_of_an_offered_triple_is_a_different_derivation_and_not_the_same():
    """Orientation is part of the triple, so membership is equality and never a set of parts.

    The two orientations are two different derivations — `-446,000,000` against `+446,000,000`,
    two ids, two `display_semantics` — which is why the check has to be an equality on the whole
    request: a membership test over ids and operations separately would accept a triple
    assembled from three offers it was never shown together.

    **Only the forward one is offered, since H2's F5**, and this test is where the membership
    rule and that narrowing meet: a planner spelling an offered triple backwards is
    `derivation_not_offered`, exactly as one naming an unoffered operation is.
    """
    package, candidate = agp_package(), agp_candidate()
    offered = offers(package, candidate)
    forward = DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                                from_fact_id=AGP_Q2_ID, to_fact_id=AGP_Q3_ID)
    reversed_pair = DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                                      from_fact_id=AGP_Q3_ID, to_fact_id=AGP_Q2_ID)

    assert forward in offered and reversed_pair not in offered
    assert forward != reversed_pair
    assert [v.code for v in plan_violations(
        agp_plan(package, requested_derivations=(reversed_pair,)), package,
        offered=offered)] == [DERIVATION_NOT_OFFERED]
    # …and the forward one, the same way, is accepted — so what the code above measures is the
    # orientation and not the shape of the plan it was read off.
    assert plan_violations(agp_plan(package, requested_derivations=(forward,)), package,
                           offered=offered) == ()
    # …and a triple assembled out of the offer set's *parts* rather than copied from a line of
    # it is refused: `ratio` is offered for no pair of this package at all.
    invented = DerivationRequest(operation=DerivationOperation.RATIO,
                                 from_fact_id=AGP_Q2_ID, to_fact_id=AGP_Q3_ID)
    assert [v.code for v in plan_violations(
        agp_plan(package, requested_derivations=(invented,)), package,
        offered=offered)] == [DERIVATION_NOT_OFFERED]


def test_a_plan_requesting_a_derivation_when_none_was_offered_is_refused():
    """The default is empty and it refuses, which is the honest reading rather than a lenient
    one: a plan cannot legitimately request from a list it was never given.

    That default is now also what `run_demo` passes, so this is the state every `metric_move`
    run is judged in — and it is why a 2.0.0 answer carrying no request is the only kind that
    can be accepted.
    """
    package = agp_package()
    plan = agp_plan(package, requested_derivations=(DerivationRequest(
        operation=DerivationOperation.ABSOLUTE_CHANGE,
        from_fact_id=AGP_Q2_ID, to_fact_id=AGP_Q3_ID),))
    assert [v.code for v in plan_violations(plan, package)] == [DERIVATION_NOT_OFFERED]


# ---------------------------------------------------------------------------------------
# 02-PLANNER-STABILIZATION §4 — the spine the plan may not contradict
# ---------------------------------------------------------------------------------------
#
# **The schema is the primary defence and this is the second half.** 2.0.0 carries no direction
# field, no value and no derivation triple, so a plan cannot *author* the factual spine. It can
# still write a sentence stating one, and `thesis` is rendered verbatim into the writer's prompt
# — which is exactly what `story-v1-76da8465cd95` did. `spine_violations` applies
# `story/core/lexicon.py`, the same closed vocabulary `verification/derived_facts` already
# applies to every draft sentence, one stage earlier.


def agp_spine(package: StoryEvidencePackage | None = None,
              candidate: StoryCandidate | None = None):
    """§2's candidate as `spine_for` builds it: 556 -> 110, `decrease`, polarity `False`."""
    package = package if package is not None else agp_package()
    candidate = candidate if candidate is not None else agp_candidate()
    spine = spine_for(candidate, package, agp_derived(package),
                      causal_language=causal_language_for(package))
    assert spine is not None
    return spine


def test_the_recorded_inverted_plan_is_refused_against_its_own_spine():
    """**The run this check exists for**, driven from its own three committed artifacts.

    `story-v1-76da8465cd95`'s plan opens *"Opendoor's adjusted gross profit **rose** from 2022Q2
    to 2022Q3"* over a package whose two readings are 556 and 110. Nothing between there and the
    writer read the claim, and the run reached a model with a factually inverted plan.

    The spine is `spine_for`'s over that run's own candidate, package and derived facts — not a
    hand-built one — so what is measured is that this check would have stopped that run, rather
    than that a string matches a lexicon.
    """
    package, plan, spine = recorded_spine(INVERTED_RUN)
    assert (spine.from_value, spine.to_value) == (556_000_000.0, 110_000_000.0)
    assert spine.direction == "decrease" and spine.direction_polarity is False
    assert "rose" in plan.thesis

    found = spine_violations(plan, spine)
    assert [violation.code for violation in found] == [DIRECTION_CONTRADICTS_SPINE]
    # The feedback is fully structured, which is what makes this a repairable refusal: the
    # offending word, the verified pair and the verified direction are all in the detail.
    detail = found[0].detail
    assert "'rose'" in detail and "decrease" in detail
    assert f"{spine.metric_id} {spine.from_period} -> {spine.to_period}" in detail


def test_the_recorded_correct_plan_passes_against_the_same_spine():
    """The accepting half, and it is the same candidate under a different model.

    `story-v1-1daff167348f`'s thesis says *"fell from 556 million USD … to 110 million USD"* over
    the identical spine. A check that refused this one would be refusing the corpus's best plan.
    """
    package, plan, spine = recorded_spine(CORRECT_RUN)
    assert spine.direction == "decrease"
    assert "fell" in plan.thesis
    assert spine_violations(plan, spine) == ()


def test_a_plan_that_states_no_direction_at_all_is_not_a_contradiction():
    """The asymmetry §4 records deliberately.

    §13's `derived_direction_not_stated_in_text` refuses a *sentence* that binds a directional
    derived fact and names no direction, because such a sentence states a figure the reader
    cannot place. A *plan* has no such duty — its job is the angle — and copying the stronger
    rule up would refuse valid plans.
    """
    package = agp_package()
    silent = agp_plan(package, thesis="Adjusted gross profit was $110 million in 2022Q3.")
    assert spine_violations(silent, agp_spine(package)) == ()


@pytest.mark.parametrize("word", ["improved", "widened", "turned", "reversed"])
def test_a_word_that_states_no_direction_of_the_stored_number_never_raises(word: str):
    """`CHANGE_DIRECTION`'s third state, and it is the reason the map is tri-state.

    `improved`, `widened`, `turned` and `reversed` are change verbs whose polarity is `None`:
    they say something moved, not which way the *stored number* went. A two-state map would have
    had to guess, and guessing here refuses a true plan.
    """
    package = agp_package()
    plan = agp_plan(package, thesis=f"Adjusted gross profit {word} between the two quarters.")
    assert spine_violations(plan, agp_spine(package)) == ()


def test_a_negated_change_verb_is_not_a_contradiction():
    """*"did not rise"* over a fall states the fall. Scoped to the marker's own clause by
    `lexicon.negated`, the same function §13.10 negation-checks a causal marker with."""
    package = agp_package()
    plan = agp_plan(
        package, thesis="Adjusted gross profit did not rise between the two quarters.")
    assert spine_violations(plan, agp_spine(package)) == ()
    # …and the unnegated sentence is refused, so what the assertion above measures is the
    # negation and not the absence of a change verb.
    inverted = agp_plan(package, thesis="Adjusted gross profit rose between the two quarters.")
    assert [v.code for v in spine_violations(inverted, agp_spine(package))] == [
        DIRECTION_CONTRADICTS_SPINE]


def test_an_ordering_word_is_skipped_and_the_set_is_derived_from_the_two_vocabularies():
    """*"Profit was higher in Q2 than in Q3"* is a **true** statement of a decrease.

    A word in both `CHANGE_DIRECTION` and `COMPARATIVE_DIRECTION` does whichever the sentence's
    word order says, and a plan binds nothing that would let this function tell which. The set
    is derived from the two maps rather than typed out, so a term added to either joins it
    automatically — asserted here both ways round.
    """
    assert ORDERING_WORDS == {"higher", "lower"}
    assert ORDERING_WORDS == set(lexicon.CHANGE_DIRECTION) & set(lexicon.COMPARATIVE_DIRECTION)
    package = agp_package()
    for thesis in ("Adjusted gross profit was higher in 2022Q2 than in 2022Q3.",
                   "Adjusted gross profit was lower in 2022Q3 than in 2022Q2."):
        assert spine_violations(agp_plan(package, thesis=thesis), agp_spine(package)) == ()


def test_a_key_points_claim_is_read_as_well_as_the_thesis():
    """Both are free prose and both are rendered into the writer's prompt, so both are checked.

    The corpus's own failure is the case: `story-v1-76da8465cd95`'s first key point says
    *"2022Q3 is higher than 2022Q2"* — an ordering word, correctly skipped — while its thesis
    says *"rose"*. A check reading only one of the two fields would be a check that could be
    walked around by moving the sentence.
    """
    package = agp_package()
    plan = agp_plan(package, key_points=(KeyPoint(
        claim="Adjusted gross profit increased over the two quarters.",
        required_fact_ids=(AGP_Q3_ID,),
        required_citation_passage_ids=(PASSAGE_ID,),
        statement_class=StatementClass.REPORTED),))
    found = spine_violations(plan, agp_spine(package))
    assert [violation.code for violation in found] == [DIRECTION_CONTRADICTS_SPINE]
    assert "key_points[0]" in found[0].detail


def test_an_unverified_direction_abstains_rather_than_guessing():
    """`quantity_direction` answers `None` where a metric's sign convention has no observation to
    measure it from — `cost_of_revenue`, `inventory_valuation_adjustment` — and the spine says so.

    The check abstains there, matching the detector's own behaviour: a check that guessed would
    be inventing the one thing the detector refused to. A `None` spine abstains for the same
    reason, which is what a story type with no spine shape hands this function.
    """
    package = agp_package()
    unverified = agp_candidate().model_copy(update={"signals": {
        "crosses_zero": False, "delta": -446_000_000.0, "delta_pct": -80.215827338,
        "period_shape": "quarter", "polarity": "revenue"}})
    spine = agp_spine(package, unverified)
    assert spine.direction_verified is False and spine.direction_polarity is None

    inverted = agp_plan(package, thesis="Adjusted gross profit rose between the two quarters.")
    assert spine_violations(inverted, spine) == ()
    assert spine_violations(inverted, None) == ()


def test_the_spine_reaches_plan_story_and_refuses_an_inverted_answer_end_to_end():
    """The seam, not the function: `run_demo` computes the spine and hands it to `plan_story`,
    and a plan whose thesis inverts it is refused before a writer is ever called."""
    package, derived = agp_package(), agp_derived()
    answer = agp_answer(
        thesis="Adjusted gross profit rose between the second and third quarters of 2022.")
    provider = FakePlanProvider(answer)
    with pytest.raises(EditorialPlanRejected) as raised:
        plan_with(provider, package, derived_facts=derived, spine=agp_spine(package))
    assert DIRECTION_CONTRADICTS_SPINE in raised.value.codes
    # The generation is attached, because this refusal refuses an answer the run paid for.
    assert raised.value.generation is not None
    assert len(provider.calls) == 1


def test_the_verified_change_section_prints_what_the_planner_may_not_contradict():
    """§6's one new prompt section, and every string in it is a value code already holds.

    The figures are the slot rows' own — a figure spelled one way under FACTS and another way
    here is two figures as far as a 9B model is concerned — the handles are the table's, and the
    direction is the detector's word from `quantity_direction`, which reads the metric's sign
    convention and is therefore not recoverable from the sign of a delta.
    """
    package, derived = agp_package(), agp_derived()
    rows = slot_table(package, derived, ())
    prompt = planner_prompt(package, slots=rows, derived_facts=derived,
                            spine=agp_spine(package))

    assert VERIFIED_CHANGE_HEADING in prompt
    section = prompt.split(VERIFIED_CHANGE_HEADING)[1].split("\n\n")[0]
    assert "adjusted_gross_profit" in section
    assert '$556 million' in section and '$110 million' in section
    assert "[F1]" in section and "[F2]" in section
    assert "direction  decrease" in section
    assert "computed   [D1] decreased by $446 million" in section
    # The handles are printed beside the ids in FACTS too, which is what lets a plan name `F1`
    # while a reader of the stored prompt can still join it to the `obs:` id an evidence panel
    # shows.
    facts = prompt.split("\nFACTS\n")[1].split("\n\n")[0]
    assert f"[F1]  [{AGP_Q2_ID}]" in facts
