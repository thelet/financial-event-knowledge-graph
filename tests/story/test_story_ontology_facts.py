"""S4 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS — the ontology's declarations, and the wire.

**The claim S4 makes is not that the package holds semantic facts; it is that the model
receives them.** §5 of the plan says so in as many words — *"tests must inspect the serialised
provider request, not only the package object"* — so the central tests here drive `plan_story`
and `write_story` through a real `StoryOpenAICompatibleProvider` over `httpx.MockTransport` and
read their assertions out of the JSON body that was actually posted. A test over
`package.semantic_facts` proves the packager built a row; only the body proves the row reached a
prompt, and every step between the two — the section list, the rendering, the §10.2 trim — is a
place it could have been lost.

Three other things are pinned:

1. **The ontology is the source and the `:Metric` node is not** (C4). The two carry the same
   description text on this run, so the test compares against `registry.metric(...)` rather than
   against a literal — equal text is not the same source.
2. **No company description is invented.** The corpus holds none, the ontology holds none, and
   the row that says so has to reach the wire: a model shown nothing supplies one from its own
   weights.
3. **The comparability facts come from §6.9's own machinery**, including *which rules were
   evaluated* — `series.rules_evaluated` reading `series.RULE_ORDER`, not a second list.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ontology import load_ontology

from story.core.models import (
    DerivationRequest,
    DerivedFact,
    EditorialPlan,
    PackagedMetric,
    PackagedSubject,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.core.spine import spine_for
from story.core.periods import story_period
from story.core.series import (
    RULE_ORDER,
    CanonicalPoint,
    CanonicalStatus,
    ClaimKind,
    Ok,
    Refuse,
    authority_from,
    rules_evaluated,
)
from story.providers import (
    PINNED_TEMPERATURE,
    StoryOpenAICompatibleProvider,
    StoryProviderConfig,
)
from story.core.evidence_slice import passages_backing_facts
from story.stages.composition import slot_table
from story.stages.generation import (
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    WRITER_MAX_TOKENS,
    WRITER_PROMPT_VERSION,
    plan_story,
    write_story,
)
from story.stages.generation.prompts import (
    OFFER_HEADING,
    PASSAGE_ROW,
    VERIFIED_CHANGE_HEADING,
    planner_prompt,
    writer_prompt,
)
from story.stages.packaging import ontology_facts

from conftest import (  # type: ignore[import-not-found]
    ONTOLOGY_ID,
    ONTOLOGY_VERSION,
    make_package,
    make_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

ONTOLOGY = load_ontology(ONTOLOGY_ID)
REGISTRY = ONTOLOGY.registry
AUTHORITY = authority_from(ONTOLOGY.definitions, REGISTRY)
SOURCE = ontology_facts.ontology_source(ONTOLOGY_ID, ONTOLOGY_VERSION)


def packaged(metric_id: str) -> PackagedMetric:
    """A `PackagedMetric` the way `_add_metrics` builds one — from the ontology."""
    definition = REGISTRY.metric(metric_id)
    return PackagedMetric(
        metric_id=metric_id,
        label=definition.label,
        description=definition.description or None,
        unit=definition.unit,
        allowed_units=tuple(definition.allowed_units),
        period_type=getattr(definition.period_type, "value", definition.period_type),
        aliases=tuple(definition.aliases),
    )


# ---------------------------------------------------------------------------------------
# What the ontology actually supplies
# ---------------------------------------------------------------------------------------


def test_a_metrics_semantic_facts_are_the_ontologys_declarations_and_not_the_graphs():
    """C4, driven rather than asserted about.

    `housing_inventory_homes` is the plan's own example and the one whose *instant* semantics a
    duration surface would misstate. Every statement below is checked against the registry, so a
    packager that started reading the `:Metric` node — whose `description` property carries the
    same text on this run — would still pass a text check and fail this one the day the two
    diverge, which is the only day it matters.
    """
    definition = REGISTRY.metric("housing_inventory_homes")
    facts = ontology_facts.semantic_facts(
        [packaged("housing_inventory_homes")], REGISTRY, source=SOURCE)
    by_attribute = {fact.attribute: fact for fact in facts}

    assert by_attribute["definition"].value == definition.description
    assert definition.description in by_attribute["definition"].statement
    assert by_attribute["unit"].value == definition.unit == "homes"
    assert by_attribute["period_semantics"].value == "instant"
    assert "single moment" in by_attribute["period_semantics"].statement
    assert "never summed across periods" in by_attribute["period_semantics"].statement
    assert by_attribute["display_name"].value == definition.label
    # This ontology declares no population, no numerator and no denominator for it, so there is
    # no source-backed scope rule and none is invented.
    assert not [name for name in by_attribute if name.startswith("scope")]
    assert all(fact.authoritative and not fact.editable for fact in facts)
    assert all(fact.source == SOURCE for fact in facts)


def test_every_semantic_fact_carries_an_empty_citation_list_and_that_is_measured():
    """§4 S4 asks for citation handles; this ontology can supply none, and it says so.

    Two of the 26 metrics carry `source_evidence`, and both entries hold an accession, a form, a
    filing date and a quote with **no `passage_id`, no `document_id` and no span**.
    `PassageCitation` requires all three and `EvidenceSourceCitation` would name an
    `:EvidenceSource` this corpus does not have, so the quote travels in the statement and the
    handle stays empty. Filling it would be the fabrication §13.7 exists to refuse.
    """
    backed = [metric for metric in ONTOLOGY.definitions.metrics if metric.source_evidence]
    assert [metric.concept_id for metric in backed] == [
        "homes_under_contract", "pct_homes_on_market_gt_120_days"]
    assert all(evidence.passage_id is None and evidence.document_id is None
               for metric in backed for evidence in metric.source_evidence)

    facts = ontology_facts.semantic_facts(
        [packaged(metric.concept_id) for metric in backed], REGISTRY, source=SOURCE)
    assert facts
    assert all(fact.citations == () for fact in facts)
    # The filed sentence is carried, with its filing named in `source` rather than as a handle.
    quoted = [fact for fact in facts
              if fact.attribute == "scope_filed" and "on the market" in fact.statement]
    assert quoted and "10-K" in quoted[0].source


def test_every_semantic_fact_id_in_one_package_is_unique():
    """`pct_homes_on_market_gt_120_days` is the one metric with a population, a numerator/
    denominator pair **and** a filed quote — three scope rows — so it is the metric a single
    `scope` attribute would have minted one id three times for."""
    facts = ontology_facts.semantic_facts(
        [packaged("pct_homes_on_market_gt_120_days"), packaged("homes_under_contract")],
        REGISTRY, source=SOURCE)
    ids = [fact.fact_id for fact in facts]

    assert len(set(ids)) == len(ids), ids
    assert sum(1 for fact in facts if fact.attribute.startswith("scope")) == 4


def test_an_alias_that_is_the_label_produces_no_row_because_it_states_nothing():
    """`gaap_gross_margin`'s only alias **is** its label, so the row would have read *"Gross
    Margin also appears in filings as 'Gross Margin'"* — 85 tokens of nothing."""
    facts = ontology_facts.semantic_facts(
        [packaged("gaap_gross_margin")], REGISTRY, source=SOURCE)

    assert REGISTRY.metric("gaap_gross_margin").aliases == ("Gross Margin",)
    assert "aliases" not in {fact.attribute for fact in facts}
    # …and a metric with a genuinely different alias keeps its row.
    inventory = ontology_facts.semantic_facts(
        [packaged("housing_inventory_homes")], REGISTRY, source=SOURCE)
    assert "aliases" in {fact.attribute for fact in inventory}


def test_a_formula_version_row_names_the_version_in_force_on_the_candidates_own_dates():
    """`adjusted_gross_profit` is the only twice-versioned metric in this ontology, and a
    candidate whose anchors straddle the boundary gets **both** rows — the same fact
    `formula_window_boundary_crossed` warns about, stated as the two definitions rather than as
    a caution about them."""
    straddling = ontology_facts.semantic_facts(
        [packaged("adjusted_gross_profit")], REGISTRY, source=SOURCE,
        anchor_dates=("2021-06-30", "2022-09-30"))

    assert [fact.value for fact in straddling if fact.attribute == "formula_version"] == [
        "adjusted_gross_profit_v1", "adjusted_gross_profit_v2"]
    one_side = ontology_facts.semantic_facts(
        [packaged("adjusted_gross_profit")], REGISTRY, source=SOURCE,
        anchor_dates=("2022-09-30",))
    assert [fact.value for fact in one_side if fact.attribute == "formula_version"] == [
        "adjusted_gross_profit_v2"]


def test_the_fact_ids_are_deterministic_and_readable():
    """Not random, and legible in an evidence panel — `story/core/keys.py`'s own rule."""
    first = ontology_facts.semantic_facts(
        [packaged("gaap_gross_margin")], REGISTRY, source=SOURCE)
    second = ontology_facts.semantic_facts(
        [packaged("gaap_gross_margin")], REGISTRY, source=SOURCE)

    assert [fact.fact_id for fact in first] == [fact.fact_id for fact in second]
    assert "sem:gaap_gross_margin:definition" in {fact.fact_id for fact in first}


# ---------------------------------------------------------------------------------------
# Subject identity — and the description that does not exist
# ---------------------------------------------------------------------------------------


def test_subject_identity_is_read_from_the_ontology_instance_and_names_its_authority():
    facts = ontology_facts.identity_facts(make_package().subject, REGISTRY, source=SOURCE)
    by_attribute = {fact.attribute: fact for fact in facts}

    assert [fact.attribute for fact in facts] == list(ontology_facts.IDENTITY_ATTRIBUTES)
    assert by_attribute["legal_name"].value == "Opendoor Technologies Inc."
    assert by_attribute["ticker"].value == "OPEN"
    assert "Nasdaq" in by_attribute["ticker"].statement
    assert by_attribute["entity_type"].value == "public_company"
    assert "0001801169" in by_attribute["entity_type"].statement
    # Authoritative because it is the ontology's declaration and not the caller's `entity_text`;
    # never editable, because §4 S6 keeps ontology facts out of reach of the prompt UI.
    assert all(fact.editable is False for fact in facts)
    assert by_attribute["legal_name"].authoritative is True
    assert by_attribute["legal_name"].source == SOURCE


def test_no_company_description_exists_and_the_package_says_so_rather_than_inventing_one():
    """§4 S4's hardest line, and the corpus's answer to it.

    Measured 2026-08-05 against `graph-v1-0483dc6b4b10`: the `opendoor` `ConceptInstance` carries
    `cik`, `tickers` and `exchange`; the `:Entity` node carries twenty properties and no
    description; and no `:Entity` node in the graph has a `description` key at all. So the row
    exists, `available` is false, `value` is empty, and the statement forbids supplying one.
    """
    facts = ontology_facts.identity_facts(make_package().subject, REGISTRY, source=SOURCE)
    description = next(fact for fact in facts if fact.attribute == "description")

    assert description.available is False
    assert description.value == ""
    assert description.authoritative is False
    assert description.statement == ontology_facts.NO_DESCRIPTION_STATEMENT
    assert "Do not describe what the company does" in description.statement
    # Nothing in the *available* rows says what Opendoor does. The unavailable row is excluded
    # from this sweep on purpose: it is the one place those words legitimately appear, as the
    # prohibition — *"do not describe what the company does, what it sells, or what market it
    # operates in"* — and a sweep that caught its own instruction would force the instruction to
    # be written in words a model is less likely to obey.
    joined = " ".join(fact.statement for fact in facts if fact.available).lower()
    for invented in ("real estate", "ibuyer", "home", "buys", "sells", "platform"):
        assert invented not in joined, invented


def test_a_subject_the_ontology_does_not_declare_falls_back_and_stops_being_authoritative():
    """The fallback is the caller's `entity_text`, and a fact built from it says it is not
    authoritative — `subject_identity_not_read_from_graph` is about exactly that string."""
    subject = PackagedSubject(entity_id="offerpad", entity_text="Offerpad Solutions Inc.",
                              resolved=False)
    facts = ontology_facts.identity_facts(subject, REGISTRY, source=SOURCE)
    by_attribute = {fact.attribute: fact for fact in facts}

    assert by_attribute["legal_name"].value == "Offerpad Solutions Inc."
    assert by_attribute["legal_name"].authoritative is False
    assert by_attribute["legal_name"].source == "package.subject.entity_text"
    assert by_attribute["ticker"].available is False


# ---------------------------------------------------------------------------------------
# Comparability — §6.9's machinery, rendered
# ---------------------------------------------------------------------------------------


def point(metric_id: str, period_key: str, value: float,
          unit: str = "percent") -> CanonicalPoint:
    start, end = {"2022Q2": ("2022-04-01", "2022-06-30"),
                  "2022Q3": ("2022-07-01", "2022-09-30")}[period_key]
    return CanonicalPoint(
        metric_id=metric_id, period=story_period(start, end, None),
        subject_entity_id="opendoor", status=CanonicalStatus.OK, value=value, unit=unit,
        scale="units", currency=None, representative_observation_id="obs:1", n_docs=1)


def test_rules_evaluated_is_the_orders_own_and_stops_where_the_refusal_did():
    """A comparison refused at R3 never reached R4, so a fact claiming *"R1-R10 were applied"*
    would be false about every refusal. One table, two consumers: `comparable` iterates
    `RULE_ORDER` and this reads it."""
    assert [rule_id for rule_id, _rule in RULE_ORDER] == [
        "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R10"]
    assert rules_evaluated(Refuse("R3", "UNCOMPARABLE_SHAPE", "x")) == ("R1", "R2", "R3")
    # R9 is not a gate — it produces the warnings an `Ok` carries — so it is last and only there.
    assert rules_evaluated(Ok())[-1] == "R9"


def test_a_permitted_comparison_states_the_rules_that_were_evaluated():
    fact = ontology_facts.comparison_fact(
        ClaimKind.DIVERGENCE,
        point("adjusted_gross_margin", "2022Q3", 3.3),
        point("gaap_gross_margin", "2022Q3", -12.6),
        Ok(),
        source=SOURCE)

    assert fact.rule_id == "§6.9/divergence"
    assert fact.metric_ids == ("adjusted_gross_margin", "gaap_gross_margin")
    assert "may be compared" in fact.statement
    assert "R1, R2, R3, R4, R5, R6, R7, R8, R10, R9" in fact.statement
    assert fact.authoritative is True and fact.editable is False


def test_a_refused_comparison_names_the_rule_that_refused_it_and_says_it_may_not_be_made():
    fact = ontology_facts.comparison_fact(
        ClaimKind.MOVEMENT,
        point("gaap_gross_margin", "2022Q3", -12.6),
        point("gaap_gross_margin", "2022Q2", 11.6),
        Refuse("R8", "BELOW_TOLERANCE", "the two filings just rounded differently"),
        source=SOURCE)

    assert fact.rule_id == "R8"
    assert "may NOT be compared" in fact.statement
    assert "the two filings just rounded differently" in fact.statement
    assert "Rules evaluated, in order: R1, R2, R3, R4, R5, R6, R7, R8." in fact.statement


def test_the_standing_rules_are_the_ontologys_distinctness_and_percentage_points():
    """Both are rules a planner needs *before* it writes, which is why they are not
    `CompatibilityDecision`s: a decision answers a comparison already made."""
    metrics = [packaged("adjusted_gross_margin"), packaged("gaap_gross_margin")]
    facts = ontology_facts.standing_facts(metrics, AUTHORITY, source=SOURCE)
    by_rule = {fact.rule_id: fact for fact in facts}

    assert set(by_rule) == {ontology_facts.RULE_MUTUALLY_DISTINCT,
                            ontology_facts.RULE_PERCENTAGE_POINTS}
    distinct = by_rule[ontology_facts.RULE_MUTUALLY_DISTINCT]
    assert distinct.metric_ids == ("adjusted_gross_margin", "gaap_gross_margin")
    assert "may never be merged" in distinct.statement
    assert "percentage points" in by_rule[ontology_facts.RULE_PERCENTAGE_POINTS].statement


def test_one_percent_metric_alone_gets_no_percentage_point_rule():
    """A package that cannot express the confusion does not need to be warned about it."""
    facts = ontology_facts.standing_facts(
        [packaged("gaap_gross_margin")], AUTHORITY, source=SOURCE)

    assert [fact.rule_id for fact in facts] == []


# ---------------------------------------------------------------------------------------
# The wire — §5's requirement, and the only proof S4's claim is true
# ---------------------------------------------------------------------------------------


def envelope(content: str) -> dict:
    return {
        "choices": [{"finish_reason": "stop", "index": 0,
                     "message": {"role": "assistant", "content": content}}],
        "created": 1785587924,
        "model": "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "object": "chat.completion",
        "usage": {"completion_tokens": 8, "prompt_tokens": 51, "total_tokens": 59},
    }


def capturing(answer: dict):  # type: ignore[no-untyped-def]
    """A real provider over a mock transport, plus the list of requests it posted."""
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json=envelope(json.dumps(answer)))

    provider = StoryOpenAICompatibleProvider(
        StoryProviderConfig(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    return provider, posted


def ontology_package():  # type: ignore[no-untyped-def]
    """The smallest package that carries all three ontology-fact sections."""
    subject = make_package().subject
    metrics = [packaged("adjusted_ebitda")]
    return make_package(
        metrics=tuple(metrics),
        semantic_facts=ontology_facts.semantic_facts(metrics, REGISTRY, source=SOURCE),
        identity_facts=ontology_facts.identity_facts(subject, REGISTRY, source=SOURCE),
        comparability_facts=(ontology_facts.comparison_fact(
            ClaimKind.MOVEMENT,
            point("adjusted_ebitda", "2022Q3", -211.0, unit="USD_millions"),
            point("adjusted_ebitda", "2022Q2", 218.0, unit="USD_millions"),
            Ok(), source=SOURCE),))


#: The planner's answer under 2.0.0: seven leaves, and a key point that names a *handle*.
#:
#: **Twelve fields the double used to supply are gone, and the surviving one changed shape.** It
#: carried `required_citation_passage_ids`, `statement_class`, `required_warnings`,
#: `causal_language`, `structure`, `prohibited_claims`, `unusable_evidence` and
#: `requested_derivations` — every one of them either a lookup code already had or a field
#: nothing read. `editorial_plan_from` fills all of them, so the `EditorialPlan` these tests
#: drive through is the same object it always was. What the model still writes is prose plus
#: `F1`, which is why `slots=` is now passed at every call site below: an unresolvable handle is
#: `unresolvable_fact_handle` and the plan never reaches the writer.
PLANNER_ANSWER = {
    "thesis": "Adjusted EBITDA crossed zero.",
    "why_it_matters": "It is the only sign reversal in the series.",
    "uncertainty": "",
    "key_points": [{"claim": "Adjusted EBITDA was negative in 2022Q3.",
                    "facts": ["F1"]}],
    "counterpoint": "",
    "counterpoint_facts": [],
}

#: The writer's answer under 4.0.0: a title and sentence text, and there is no third field.
#:
#: **Seven fields the double used to supply are gone, and this file is where that reads most
#: plainly.** It carried a `fact_id`, a `rendered`, a `metric_surface`, a `period_surface` and an
#: `evidence_id` — five strings a model had to get exactly right about a fact the prompt had
#: already described to it — and then, under 3.0.0, a `kind` and a `rests_on`. The slot table
#: resolves `F1` to the same observation, code fills the figure and the period from the row, and
#: the citation is minted from that fact's own handle; `kind` and `rests_on` went the same way,
#: because the compiler reads a sentence's kind off the rows its slots name and cites from them.
#: What is left here is a sentence.
WRITER_ANSWER = {
    "title": "Adjusted EBITDA in 2022Q3",
    "sentences": [{"text": "Adjusted EBITDA was {{F1}} in {{F1.period}}."}],
}


def rows_for(package):  # type: ignore[no-untyped-def]
    """The slot table the composition root would have passed, built once per call site.

    2.0.0 made `slots` load-bearing for the *planner* as well as the writer: `F1` is resolved
    back to `obs:adjusted-ebitda:b` through this table, and a call that omitted it would refuse
    the plan as `unresolvable_fact_handle` before any of the assertions below were reached.
    """
    return slot_table(package, (), passages_backing_facts(package))


def user_message(request: httpx.Request) -> str:
    body = json.loads(request.content)
    assert body["temperature"] == PINNED_TEMPERATURE
    return body["messages"][1]["content"]


def test_the_planner_request_on_the_wire_carries_every_semantic_identity_and_comparison_fact():
    """§5: *"only the wire proves it."*

    Driven through the real provider and read out of the posted JSON, because every step between
    `package.semantic_facts` and `messages[1].content` — the section list, the rendering, the
    §10.2 trim — is a place a declaration could be lost without a package-level test noticing.
    """
    package = ontology_package()
    provider, posted = capturing(PLANNER_ANSWER)
    planned = plan_story(package, provider=provider, max_tokens=PLANNER_MAX_TOKENS,
                         slots=rows_for(package))

    prompt = user_message(posted[0])
    for fact in package.semantic_facts:
        assert fact.statement in prompt, fact.attribute
    for fact in package.identity_facts:
        assert fact.statement in prompt, fact.attribute
    for fact in package.comparability_facts:
        assert fact.statement in prompt, fact.rule_id
    assert "METRIC SEMANTICS" in prompt
    assert "COMPANY IDENTITY" in prompt
    assert "COMPARISON RULES" in prompt
    # 2.0.0 is the seven-leaf schema and the VERIFIED CHANGE section; 1.2.0 was
    # DETERMINISTIC_FACT_TOOLS' offer set. The literal is kept beside the constant so a version
    # that stops moving is as loud as one that moves for the wrong reason.
    assert planned.plan.prompt_version == PLANNER_PROMPT_VERSION == "2.0.0"


def test_the_writer_request_on_the_wire_carries_the_same_declarations_verbatim():
    """One rendering, two personas. A definition that differed between the model that plans a
    claim and the model that writes it would be two meanings for one number."""
    package = ontology_package()
    provider, posted = capturing(WRITER_ANSWER)
    written = write_story(package, make_plan(package_id=package.package_id),
                          provider=provider,
                          slots=rows_for(package), length_target=4,
                          max_tokens=WRITER_MAX_TOKENS)

    prompt = user_message(posted[0])
    for fact in (*package.semantic_facts, *package.identity_facts,
                 *package.comparability_facts):
        assert fact.statement in prompt, fact.fact_id
    # 4.0.0 is the two-leaf schema: `kind` and `rests_on` left it the way `fact_bindings` and
    # `citations` left it at 3.0.0 and `calculation` at 2.0.0. 2.1.0 was `metric_surfaces_for`
    # offering the metric id; 1.4.0 was TABLE_CELL_CITATIONS S4's evidence handle; 1.3.0 was S6's
    # warning phrases. The literal is kept beside the constant so a version that stops moving is
    # as loud as one that moves for the wrong reason.
    #
    # **`written` no longer carries a draft, and that is the contract rather than an accident**:
    # `write_story` returns the sentences the model wrote, and the `Draft` is what
    # `story/stages/composition/` makes of them. The version is asserted on the constant, which
    # is what reaches the wire and the manifest.
    assert WRITER_PROMPT_VERSION == "4.0.0"
    assert written.sentences and not hasattr(written, "draft")
    # Strings, not objects. 4.0.0 removed `SentenceTemplate` along with the two fields it held
    # beside the text, so what a caller gets back is what the model typed.
    assert all(isinstance(sentence, str) for sentence in written.sentences)


@pytest.mark.parametrize("stage", ["planner", "writer"])
def test_the_unavailable_description_reaches_the_wire_marked_unavailable(stage: str):
    """The row exists **because** it is unavailable. A model shown nothing about what the
    company does supplies an answer from its weights; a model shown `NOT AVAILABLE` and the
    reason has been told not to, and §4 S6's panel can say the corpus was asked."""
    package = ontology_package()
    if stage == "planner":
        provider, posted = capturing(PLANNER_ANSWER)
        plan_story(package, provider=provider, max_tokens=PLANNER_MAX_TOKENS,
                   slots=rows_for(package))
    else:
        provider, posted = capturing(WRITER_ANSWER)
        write_story(package, make_plan(package_id=package.package_id),
                    provider=provider,
                    slots=rows_for(package), length_target=4,
                    max_tokens=WRITER_MAX_TOKENS)

    prompt = user_message(posted[0])
    assert "NOT AVAILABLE - " + ontology_facts.NO_DESCRIPTION_STATEMENT in prompt
    # Every *available* row is rendered without the marker, so the marker means one thing.
    available = [fact.statement for fact in package.identity_facts if fact.available]
    assert available
    for statement in available:
        assert "NOT AVAILABLE - " + statement not in prompt


def test_no_ontology_fact_id_reaches_either_prompt():
    """§11 rule 1 admits only ids beginning `obs:` in `required_fact_ids`, so a rendered `sem:`
    or `idn:` id is an invitation to a rejection. The ids stay on the package, for the panel."""
    package = ontology_package()
    provider, posted = capturing(PLANNER_ANSWER)
    plan_story(package, provider=provider, max_tokens=PLANNER_MAX_TOKENS,
               slots=rows_for(package))
    writer_provider, writer_posted = capturing(WRITER_ANSWER)
    write_story(package, make_plan(package_id=package.package_id),
                provider=writer_provider,
                slots=rows_for(package), length_target=4,
                max_tokens=WRITER_MAX_TOKENS)

    both = user_message(posted[0]) + user_message(writer_posted[0])
    for fact in (*package.semantic_facts, *package.identity_facts,
                 *package.comparability_facts):
        assert fact.fact_id not in both, fact.fact_id


# ---------------------------------------------------------------------------------------
# The sections a prompt prints, and the two it stopped printing
#
# Here rather than beside the rendering helpers for the reason the wire tests above are here:
# the question this file asks is *what does the model receive*, and a section that stopped
# being rendered is the same kind of change as a declaration that stopped reaching a prompt.
# Driven from `data/story_demo/h1-metricmove/`, the committed run whose plan §11's correction
# was written against — its spine is 556 -> 110 and `story-v1-76da8465cd95` wrote *"rose"* over
# it, which is what VERIFIED CHANGE exists to make unstateable.
# ---------------------------------------------------------------------------------------

METRIC_MOVE_RUN = REPO_ROOT / "data" / "story_demo" / "h1-metricmove"


def metric_move_run():  # type: ignore[no-untyped-def]
    """That run's candidate, package, derived facts and plan, read off its own artifacts."""
    def read(name: str) -> dict:
        return json.loads((METRIC_MOVE_RUN / name).read_text(encoding="utf-8"))

    derivation = read("derived_facts.json")
    return (
        StoryCandidate.model_validate(read("candidate.json")),
        StoryEvidencePackage.model_validate(read("evidence_package.json")),
        tuple(DerivedFact.model_validate(row) for row in derivation["facts"]),
        EditorialPlan.model_validate(read("editorial_plan.json")),
    )


def test_the_verified_change_section_is_printed_from_a_spine_and_from_nothing_else():
    """The measurement the planner is shown so that it cannot be the one to work it out.

    `quantity_direction` reads the metric's sign convention, so *which way it went* is not
    recoverable from the sign of a delta — a planner given two floats six lines apart was being
    asked for an inference it had no basis for, and `story-v1-76da8465cd95` got it backwards and
    reached a model with a factually inverted plan.

    The strings are asserted to be the ones the rows above already print, because a figure
    spelled one way under FACTS and another way here is two figures to a 9B model. Nothing is
    retyped: the expected surfaces are read off the slot table this call was handed.
    """
    candidate, package, derived, _plan = metric_move_run()
    spine = spine_for(candidate, package, derived)
    assert spine is not None, "the committed metric_move run must still bind a spine"
    rows = {row.fact_id: row for row in slot_table(
        package, derived, passages_backing_facts(package))}

    prompt = planner_prompt(package, slots=rows.values(), spine=spine, derived_facts=derived)
    section = prompt[prompt.index(VERIFIED_CHANGE_HEADING):].split("\n\n")[0]

    assert spine.metric_id in section
    for fact_id, period in ((spine.from_fact_id, spine.from_period),
                            (spine.to_fact_id, spine.to_period)):
        row = rows[fact_id]
        assert f"[{row.handle}]" in section
        assert row.offers[""] in section          # the figure, spelled as FACTS spells it
        assert period in section
    # The detector's own word, not the sign of `to_value - from_value`.
    assert spine.direction_verified and f"direction  {spine.direction}" in section
    assert all(f"[{rows[fact.fact_id].handle}]" in section for fact in derived)

    # And nothing at all without one: the heading is the whole section, so a run with no spine
    # prints no measurement rather than an empty claim about one.
    assert VERIFIED_CHANGE_HEADING not in planner_prompt(package, slots=rows.values(),
                                                         derived_facts=derived)


def test_the_planner_is_shown_no_offer_list_when_nothing_is_left_to_ask_for():
    """An offer the planner may not take is an invitation to a refusal.

    `story/core/spine.py` executes the derivations the detector fired on *before* this call, so
    for a detector-defined story the offer set is empty and the section is omitted entirely —
    not printed as *"(none)"*. The heading used to render either way, and a 1.2.0 prompt showed
    the model a `requested_derivations` field with nothing legal to put in it.
    """
    _candidate, package, derived, _plan = metric_move_run()
    rows = slot_table(package, derived, passages_backing_facts(package))

    assert OFFER_HEADING not in planner_prompt(package, slots=rows, derived_facts=derived)
    # The heading is still rendered where a caller does hand over an offer, so the omission
    # above is a statement about the offer set and not about the section having been deleted.
    offer = DerivationRequest(operation=derived[0].operation,
                              from_fact_id=derived[0].from_fact_id,
                              to_fact_id=derived[0].to_fact_id)
    assert OFFER_HEADING in planner_prompt(package, slots=rows, offered=(offer,),
                                           derived_facts=derived)


def test_the_writer_is_shown_no_passages_because_the_pipeline_hands_it_none():
    """5,925 characters of table that hold no figure the writer may write.

    Every passage in the section prints numerals off a filed table, and under 4.0.0 not one of
    them is a figure the model is allowed to type: a figure comes from a slot or from a row's own
    printed string. The section survived to let an `explanatory` sentence rest on a passage, and
    `rests_on` left the schema at 4.0.0 — so `story/pipeline.py` builds its slot table with no
    passage rows at all (`slot_table(package, derived, ())`), `write_story` reads the answer off
    those rows, and `writer_prompt` omits the heading rather than printing it over nothing.
    """
    _candidate, package, derived, plan = metric_move_run()
    rows = slot_table(package, derived, passages_backing_facts(package))
    pipeline_rows = slot_table(package, derived, ())

    assert [row.handle for row in pipeline_rows if row.kind == PASSAGE_ROW] == []
    without = writer_prompt(package, plan, (), slots=pipeline_rows, derived_facts=derived)
    assert "PASSAGES" not in without

    # What it costs when it is printed, measured rather than asserted from memory. The claim is
    # the omission's size, so the two prompts differ by the section and by nothing else.
    with_passages = writer_prompt(package, plan, package.primary_passages, slots=rows,
                                  derived_facts=derived)
    assert "PASSAGES" in with_passages
    assert len(with_passages) - len(writer_prompt(
        package, plan, (), slots=rows, derived_facts=derived)) > 5_000
