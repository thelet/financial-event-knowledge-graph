"""The narrative lane, offline, with a stub provider replaying recorded answers.

Every refusal in STAGE_10 §5 is driven here with a literal dict, which is the property
`response_mapping.py` exists to have: the decisions that matter most are the ones a GPU must
not be needed to test. The live file proves the same lane against the real server.

The stub is a `GenerationProvider` and is used *through* that protocol, never patched in.
Likewise the scope: `FixedScope` satisfies `OntologyCandidateScope`, and the lane takes it as
a constructor argument, which is what step 11 needs in order to run one lane under two scopes.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from extraction.contracts import ClaimLane, GenerationProvider, GenerationResult
from extraction.core.assembly import (
    MISSING_POPULATION_DEFINITION,
    assemble,
    declared_ambiguity_codes,
    deferred_metric_ids,
)
from extraction.core.concept_resolution import fold
from extraction.core.models import (
    NOT_THE_SUBJECT_COMPANY,
    CandidatePassage,
    LaneClaim,
    PeriodRef,
)
from extraction.core.text_spans import locate
from extraction.core.units import unit_for_metric
from extraction.core.validation import ONTOLOGY_WARNING, validate
from extraction.providers.public import ProviderResponseError
from extraction.stages.narrative import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    ISSUE_CODES,
    LANE_NAME,
    MIN_OUTPUT_TOKENS,
    PERIOD_NOT_PRINTED,
    PROMPT_EXCEEDS_CONTEXT,
    PROMPT_VERSION,
    AnswerStore,
    MissingAnswerError,
    OntologyGuidedNarrativeClaimLane,
    PassageContext,
    ReplayingGenerationProvider,
    build_prompt,
    map_answer,
    output_budget,
    period_phrases,
    printed_magnitude,
    request_identity,
    response_schema,
)
from extraction.stages.narrative.public import (
    DEFINITIONAL_NOT_OBSERVATIONAL,
    DERIVED_COMPARISON,
    METRIC_OUT_OF_SCOPE,
    MODEL_ANSWER_UNUSABLE,
    QUOTED_SPAN_NOT_IN_PASSAGE,
    SCALE_NOT_APPLICABLE,
    SCALE_NOT_DECLARED,
    UNIT_CONTRADICTS_ONTOLOGY,
    VALUE_CONTRADICTS_QUOTED_TEXT,
)
from extraction.stages.narrative import answer_store
from extraction.stages.narrative.public import AmbiguousSurface, ambiguous_surfaces_for
from extraction.stages.narrative.response_mapping import MappingContext
from extraction.stages.tables.public import (
    AMBIGUOUS_ALIAS,
    DEFERRED_REQUIRED_SOURCE_LANE,
    MISSING_PERIOD,
    MISSING_REQUIRED_CONTEXT,
    PERIOD_TYPE_MISMATCH,
    UNRESOLVED_METRIC,
)
from ontology import load_ontology

PACKAGE = Path(__file__).resolve().parents[2] / "extraction"

PASSAGE_ID = "norm:0001801169:0001801169-23-000137:q32023formxex992sharehol.htm#p2"
DOCUMENT_ID = PASSAGE_ID.split("#")[0]

# Written to hold the shapes the refusals turn on — a quarter shorthand, a full date, a
# monetary figure with its scale spelled as a word, a count, and a population wording — rather
# than copied from a benchmark case. Nothing under `extraction/` may name the benchmark and
# nothing here needs to.
PASSAGE_TEXT = (
    "Financial highlights from 3Q23 include: "
    "Sold 2,687 homes across our markets. "
    "We also returned to positive Contribution Profit of $43 million, "
    "or 4.4% Contribution Margin. "
    "As of September 30, 2023, 18% of our homes had been listed on the market for more "
    "than 120 days."
)

# Both members of two declared confusion pairs are present on purpose: `homes_sold` beside
# `homes_purchased`, and `gaap_gross_profit` beside `adjusted_gross_profit`. A scope holding
# only the right answer would make the prompt's `distinct_from` and ambiguity-note blocks
# untestable, which is the half of the prompt that changed a wrong answer into a right one.
SCOPE_IDS = (
    "homes_sold", "homes_purchased", "contribution_profit", "contribution_margin",
    "pct_homes_on_market_gt_120_days", "housing_inventory_homes", "gaap_gross_profit",
    "adjusted_gross_profit",
)


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


class InMemoryPassages:
    """A `PassageSource` with no corpus behind it."""

    def __init__(self, rows: dict[str, str]):
        self._rows = rows

    def text_of(self, passage_id): return self._rows.get(passage_id)
    def exists(self, passage_id): return passage_id in self._rows
    def document_of(self, passage_id):
        return passage_id.split("#")[0] if passage_id in self._rows else None


class FixedScope:
    """An `OntologyCandidateScope` that answers the same concepts for any text.

    A stub rather than the real lexical scope so a rejection test can put a concept in scope
    that the passage never names — which is the only way to drive "the model chose something
    the scope did offer, and the ontology still forbids it" apart from "the model invented
    one".
    """

    name = "fixed"

    def __init__(self, concept_ids: tuple[str, ...] = SCOPE_IDS):
        self._ids = tuple(concept_ids)

    def candidates_for(self, text: str) -> tuple[str, ...]:
        return self._ids


class StubProvider:
    """A `GenerationProvider` replaying one recorded answer, and recording what it was asked.

    Not a mock: it satisfies the protocol and is driven through it. `prompts` and `schemas`
    are kept so a test can assert on the request without a second code path building one.
    """

    model_id = "stub-model"

    def __init__(self, content: dict | None = None, *, error: Exception | None = None):
        self._content = content if content is not None else {"claims": [], "abstentions": []}
        self._error = error
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    def generate(self, *, prompt, schema, max_tokens=1024, temperature=0.0) -> GenerationResult:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if self._error is not None:
            raise self._error
        raw = json.dumps(self._content, sort_keys=True)
        return GenerationResult(
            content=self._content, raw_content=raw, model_id=self.model_id,
            prompt_tokens=1234, completion_tokens=567, total_tokens=1801,
            latency_ms=98.7, raw_sha256="0" * 64, content_sha256="1" * 64,
            finish_reason="stop", attempts=1, metadata={})


def claim_finding(**overrides) -> dict:
    base = {
        "evidence_sentence": "Sold 2,687 homes across our markets.",
        "metric_id": "homes_sold",
        "statement_type": "reported_level",
        "subject": "the_filing_company",
        "value_text": "2,687",
        "value": 2687,
        "scale": "units",
        "unit": "homes",
        "period_kind": "duration",
        "period_label": "3Q23",
        "population_text": "",
    }
    base.update(overrides)
    return base


def population_finding(**overrides) -> dict:
    base = {
        "evidence_sentence": (
            "As of September 30, 2023, 18% of our homes had been listed on the market for "
            "more than 120 days."),
        "metric_id": "pct_homes_on_market_gt_120_days",
        "statement_type": "reported_level",
        "subject": "the_filing_company",
        "value_text": "18%",
        "value": 18,
        "scale": "units",
        "unit": "percent",
        "period_kind": "instant",
        "period_label": "September 30, 2023",
        "population_text": "18% of our homes had been listed on the market for more than 120 days",
    }
    base.update(overrides)
    return base


def money_finding(**overrides) -> dict:
    base = {
        "evidence_sentence": (
            "We also returned to positive Contribution Profit of $43 million, "
            "or 4.4% Contribution Margin."),
        "metric_id": "contribution_profit",
        "statement_type": "reported_level",
        "subject": "the_filing_company",
        "value_text": "$43 million",
        "value": 43,
        "scale": "millions",
        "unit": "USD",
        "period_kind": "duration",
        "period_label": "3Q23",
        "population_text": "",
    }
    base.update(overrides)
    return base


def context(**overrides) -> MappingContext:
    base = dict(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, passage_text=PASSAGE_TEXT,
        scope_concept_ids=frozenset(SCOPE_IDS), deferred_metric_ids=frozenset(),
    )
    base.update(overrides)
    return MappingContext(**base)


def build_lane(ontology, content=None, *, scope=None, error=None):
    provider = StubProvider(content, error=error)
    lane = OntologyGuidedNarrativeClaimLane(
        ontology, scope or FixedScope(), provider, deferred_metric_ids(ontology))
    return lane, provider


def run(lane) -> object:
    return lane.extract_passage(
        PASSAGE_TEXT,
        context=PassageContext(
            passage_id=PASSAGE_ID, document_type="shareholder_letter", form="8-K",
            filing_date="2023-11-02", heading_path=("EX-99.2",)),
        document_id=DOCUMENT_ID)


# -- (1) the protocol -----------------------------------------------------------------------------


def test_the_lane_is_usable_through_the_claim_lane_protocol(ontology):
    """Driven through `ClaimLane`, never asserted with `isinstance` against it."""
    lane_object, _ = build_lane(ontology, {"claims": [claim_finding()], "abstentions": []})
    lane: ClaimLane = lane_object
    candidate = CandidatePassage(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
        document_type="shareholder_letter", passage_kind="narrative", lane="narrative",
        reason="exact_alias")

    assert lane.name == LANE_NAME
    assert lane.version
    assert lane.supports(candidate)
    assert not lane.supports(candidate.model_copy(update={"passage_kind": "table"}))

    result = lane.extract(candidate, PASSAGE_TEXT)
    assert result.passage_id == PASSAGE_ID
    assert result.lane == LANE_NAME
    assert [c.metric_id for c in result.claims] == ["homes_sold"]


def test_the_provider_is_used_through_its_protocol(ontology):
    lane, provider = build_lane(ontology)
    port: GenerationProvider = provider
    assert port.model_id == "stub-model"
    run(lane)
    assert len(provider.prompts) == 1


# -- (2)-(6) the rejection boundary ------------------------------------------------------------------


def test_a_concept_outside_the_scope_is_rejected_and_recorded(ontology):
    """Remapping it to the nearest concept in scope is §7's forbidden move."""
    extraction = map_answer(
        {"claims": [claim_finding(metric_id="adjusted_ebitda")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    issue = extraction.issues[0]
    assert issue.code == METRIC_OUT_OF_SCOPE
    assert issue.rejected_claim
    assert issue.raw_finding["metric_id"] == "adjusted_ebitda"


def test_a_unit_contradicting_the_ontology_is_rejected(ontology):
    extraction = map_answer(
        {"claims": [claim_finding(unit="percent")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    assert extraction.issues[0].code == UNIT_CONTRADICTS_ONTOLOGY


def test_a_period_type_contradicting_the_ontology_is_rejected(ontology):
    """`homes_sold` is a duration metric; an instant reading of it is wrong, not roundable."""
    extraction = map_answer(
        {"claims": [claim_finding(period_kind="instant")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    assert extraction.issues[0].code == PERIOD_TYPE_MISMATCH


def test_a_quoted_span_absent_from_the_passage_is_rejected_as_fabricated(ontology):
    extraction = map_answer(
        {"claims": [claim_finding(
            evidence_sentence="Sold 2,687 homes in our forty-four markets.")],
         "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    assert extraction.issues[0].code == QUOTED_SPAN_NOT_IN_PASSAGE


def test_a_span_composed_from_separated_parts_is_rejected(ontology):
    """Both halves are verbatim and the join is not. Accepting a composed quotation would
    accept "X increased" welded to a figure from another sentence, which is fabricated
    evidence assembled out of true fragments."""
    composed = "Financial highlights from 3Q23 include: or 4.4% Contribution Margin."
    extraction = map_answer(
        {"claims": [claim_finding(evidence_sentence=composed)], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == QUOTED_SPAN_NOT_IN_PASSAGE


def test_a_metric_deferred_to_an_absent_lane_is_rejected_from_the_ontology(ontology):
    """`gaap_gross_profit` declares `xbrl` first and the corpus has no XBRL lane. Derived,
    never a literal list: the assertion below is about what the ontology declares."""
    deferred = deferred_metric_ids(ontology)
    assert "gaap_gross_profit" in deferred

    extraction = map_answer(
        {"claims": [claim_finding(metric_id="gaap_gross_profit", unit="USD",
                                  value_text="$43 million", value=43, scale="millions",
                                  evidence_sentence=(
                                      "We also returned to positive Contribution Profit of "
                                      "$43 million, or 4.4% Contribution Margin."))],
         "abstentions": []},
        ontology=ontology, context=context(deferred_metric_ids=deferred))
    assert extraction.claims == []
    assert extraction.issues[0].code == DEFERRED_REQUIRED_SOURCE_LANE


def test_a_value_that_contradicts_its_own_quotation_is_rejected(ontology):
    extraction = map_answer(
        {"claims": [claim_finding(value=2688)], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == VALUE_CONTRADICTS_QUOTED_TEXT


def test_a_scale_on_a_count_is_rejected(ontology):
    """§8a.5: multiplying `Homes sold` by a thousand produced 2,462,000 homes in a quarter."""
    extraction = map_answer(
        {"claims": [claim_finding(scale="thousands")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == SCALE_NOT_APPLICABLE


def test_a_scale_no_word_in_the_passage_declares_is_rejected(ontology):
    extraction = map_answer(
        {"claims": [money_finding(scale="billions")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == SCALE_NOT_DECLARED


# A letter that declares its scale once and then prints bare figures. §8a.6 measured the same
# shape for tables: 9 of 74 KPI table passages declare the scale outside the table.
BLOCK_SCALE_PASSAGE = (
    "Financial highlights from 3Q23 (in millions, except homes sold): "
    "We also returned to positive Contribution Profit of $43."
)


def test_a_scale_declared_once_for_the_passage_is_accepted_and_recorded_as_weaker(ontology):
    """The passage-wide fallback, kept and stated in prompt rule 4.

    Deleting it left the whole offline suite green *(mutation M34)* while refusing every letter
    that writes "(in millions)" once. What makes it safe is that the two readings are not
    recorded as the same thing: beside the figure is `inline_prose`, elsewhere in the passage is
    `preceding_context`, so a report can say which claims rest on which."""
    extraction = map_answer(
        {"claims": [money_finding(
            evidence_sentence="We also returned to positive Contribution Profit of $43.",
            value_text="$43")],
         "abstentions": []},
        ontology=ontology, context=context(passage_text=BLOCK_SCALE_PASSAGE))
    assert extraction.claims, [(i.code, i.detail) for i in extraction.issues]
    claim = extraction.claims[0]
    assert claim.value == 43_000_000
    assert claim.scale.scale == "millions"
    assert claim.scale.location == "preceding_context"
    assert "million" in claim.scale.declaration_text
    assert claim.extractor_metadata["scale_declaration_location"] == "preceding_context"


def test_a_scale_printed_beside_the_figure_is_recorded_as_the_stronger_reading(ontology):
    lane, _ = build_lane(ontology, {"claims": [money_finding()], "abstentions": []})
    claim = run(lane).claims[0]
    assert claim.scale.location == "inline_prose"
    assert claim.extractor_metadata["scale_declaration_location"] == "inline_prose"


def test_a_figure_about_another_party_is_not_emitted_as_the_registrants(ontology):
    extraction = map_answer(
        {"claims": [claim_finding(subject="another_party")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == NOT_THE_SUBJECT_COMPANY


def test_a_metric_declared_about_a_market_is_not_emitted_about_the_company(ontology):
    """`mortgage_rate` declares `geographic_market`. A letter quoting it is quoting a housing
    statistic, and attaching the registrant asserts a measurement no filing makes."""
    text = "Mortgage rates reached 8% as of September 30, 2023."
    extraction = map_answer(
        {"claims": [{
            "evidence_sentence": text, "metric_id": "mortgage_rate",
            "statement_type": "reported_level", "subject": "the_filing_company",
            "value_text": "8%", "value": 8, "scale": "units", "unit": "percent",
            "period_kind": "instant", "period_label": "September 30, 2023",
            "population_text": ""}],
         "abstentions": []},
        ontology=ontology,
        context=context(passage_text=text, scope_concept_ids=frozenset({"mortgage_rate"})))
    assert extraction.issues[0].code == NOT_THE_SUBJECT_COMPANY


def test_a_change_and_a_definition_are_refused_as_non_observations(ontology):
    extraction = map_answer(
        {"claims": [claim_finding(statement_type="period_over_period_change"),
                    claim_finding(statement_type="definition_only")],
         "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    assert [i.code for i in extraction.issues] == [
        DERIVED_COMPARISON, DEFINITIONAL_NOT_OBSERVATIONAL]


def test_a_span_that_names_nothing_is_refused(ontology):
    """A quotation that locates a number and identifies nothing supports nothing."""
    extraction = map_answer(
        {"claims": [claim_finding(evidence_sentence="2,687")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == MISSING_REQUIRED_CONTEXT


def test_a_period_the_passage_does_not_state_cannot_be_emitted(ontology):
    """The measured failure: right metric, right value, wrong year, every other check
    passing. The phrase must be printed and `core.periods` must be able to read it."""
    extraction = map_answer(
        {"claims": [claim_finding(period_label="the third quarter")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.issues[0].code == MISSING_PERIOD


# A comparative MD&A paragraph: the prior-period phrase is printed too, so choosing it is a
# representable answer that every other check passes. This is the residual §8a.12 asks step 11
# to score, and the shape the recorded attribution below exists to make scoreable.
COMPARATIVE_PASSAGE = (
    "For the three months ended September 30, 2021, revenue increased. "
    "Homes sold were 5,988 in the period. "
    "This compares with the three months ended September 30, 2020, when the market was "
    "materially different for us."
)


def test_a_prior_period_phrase_is_representable_and_is_recorded_as_reached_for(ontology):
    """Stated as it is, not as the docstring used to claim it.

    `period_label`'s enum removes every period the passage does not print — including the
    wrong-year answer that produced six claims dated a year early. It does **not** remove a
    prior-period phrase the passage *does* print, so a 2020 label on a 2021 figure is still
    representable *(review 2026-08-02 found the docstring calling it unrepresentable)*. What
    the lane owes instead is a measurement: which phrase was chosen and how far the model
    reached for it."""
    findings = [
        claim_finding(evidence_sentence="Homes sold were 5,988 in the period.",
                      value_text="5,988", value=5988,
                      period_label="three months ended September 30, 2021"),
        claim_finding(evidence_sentence="Homes sold were 5,988 in the period.",
                      value_text="5,988", value=5988,
                      period_label="three months ended September 30, 2020"),
    ]
    extraction = map_answer({"claims": findings, "abstentions": []},
                            ontology=ontology, context=context(
                                passage_text=COMPARATIVE_PASSAGE))
    reported, prior = extraction.claims
    assert reported.period.key == "2021Q3"
    assert prior.period.key == "2020Q3", "the prior-period answer is representable, not refused"

    # Both are reached for from outside the quoted sentence, and not equally far.
    for claim in (reported, prior):
        assert claim.extractor_metadata["period_label_in_evidence"] is False
    assert (abs(prior.extractor_metadata["period_label_distance_from_evidence"])
            > abs(reported.extractor_metadata["period_label_distance_from_evidence"]))


def test_the_chosen_period_phrase_is_recorded_with_its_position(ontology):
    """The measurable that makes §8a.12 a scored dimension rather than a hope. Numbers about a
    claim, never an anchor: §8a.11's rule is that evidence is the passage id."""
    lane, _ = build_lane(ontology, {"claims": [population_finding()], "abstentions": []})
    metadata = run(lane).claims[0].extractor_metadata
    assert metadata["period_label"] == "September 30, 2023"
    assert metadata["period_label_in_evidence"] is True
    assert metadata["period_label_distance_from_evidence"] == 0
    assert PASSAGE_TEXT[metadata["period_label_char_start"]:].startswith("September 30, 2023")


# -- the period the passage does not print, which the enum could not say ---------------------------

# The shape step 11 measured on `q42021formxex992sharehol.htm#p10`, reduced to its essentials:
# a letter that reports a quarter and a full year and prints a phrase for the quarter only. The
# enum offered `4Q21` and nothing else, so the model labelled the full-year figure `4Q21` and
# two values collided under one deterministic observation id.
UNREPRESENTABLE_PERIOD_PASSAGE = (
    "In 4Q21 we delivered Contribution Profit of $152 million. "
    "For the year, we delivered Contribution Profit of $525 million."
)


def test_the_passage_prints_no_phrase_for_the_period_the_figure_belongs_to(ontology):
    """The premise, measured rather than assumed: this is why the enum needed a new member."""
    assert period_phrases(UNREPRESENTABLE_PERIOD_PASSAGE) == ("4Q21",)


def test_the_enum_can_say_that_no_printed_phrase_gives_this_figures_period(ontology):
    """`PERIOD_NOT_PRINTED` is offered on every passage, whatever it prints."""
    lane, provider = build_lane(ontology)
    lane.extract_passage(
        UNREPRESENTABLE_PERIOD_PASSAGE,
        context=PassageContext(passage_id=PASSAGE_ID, document_type="shareholder_letter"),
        document_id=DOCUMENT_ID)
    enum = provider.schemas[0]["properties"]["claims"]["items"]["properties"]["period_label"]
    assert enum["enum"] == ["4Q21", PERIOD_NOT_PRINTED]
    assert PERIOD_NOT_PRINTED in provider.prompts[0]


def test_choosing_it_refuses_the_claim_with_missing_period(ontology):
    """The member is an abstention wearing a claim's shape, not a period.

    Without this the only representable answers on the full-year figure were wrong ones, and
    the model gave one: `4Q21` on a full-year total, which passed every other check.
    """
    finding = money_finding(
        evidence_sentence="For the year, we delivered Contribution Profit of $525 million.",
        value_text="$525 million", value=525, period_label=PERIOD_NOT_PRINTED)
    extraction = map_answer(
        {"claims": [finding], "abstentions": []},
        ontology=ontology,
        context=context(passage_text=UNREPRESENTABLE_PERIOD_PASSAGE))
    assert extraction.claims == []
    assert [i.code for i in extraction.issues] == [MISSING_PERIOD]
    assert extraction.issues[0].rejected_claim is True


def test_it_is_decided_before_the_period_type_check(ontology):
    """A model declining to name a period has not also made a claim about that period's kind.

    Reversing the two produces `PERIOD_TYPE_MISMATCH` on an answer that named no period, which
    would classify the one failure this member exists to allow under a category about a
    disagreement the answer never had.
    """
    finding = population_finding(period_kind="duration",
                                period_label=PERIOD_NOT_PRINTED)
    extraction = map_answer({"claims": [finding], "abstentions": []},
                            ontology=ontology, context=context())
    assert [i.code for i in extraction.issues] == [MISSING_PERIOD]


def test_the_wrong_printed_phrase_is_still_representable(ontology):
    """The new member does not make a wrong period impossible and must not be read as doing so.

    A comparative paragraph still prints its prior-period phrase; the enum still offers it.
    What changed is that "none of these" is now sayable, not that the wrong one is not.
    """
    finding = claim_finding(evidence_sentence="Homes sold were 5,988 in the period.",
                            value_text="5,988", value=5988,
                            period_label="three months ended September 30, 2020")
    extraction = map_answer({"claims": [finding], "abstentions": []},
                            ontology=ontology,
                            context=context(passage_text=COMPARATIVE_PASSAGE))
    assert extraction.claims[0].period.key == "2020Q3"


# -- the output budget fits the slot the request is issued into ------------------------------------


def test_the_output_budget_leaves_the_prompt_room_in_the_context_slot(ontology):
    """Prompt plus budget never exceeds the slot, at any prompt length.

    The property the fixed 4,096 could not hold. Step 11 issued a request whose prompt was
    4,211 tokens with a 4,096-token budget against an 8,192-token slot, and the answer was cut
    mid-object at the slot rather than at the budget.
    """
    for length in (0, 1_000, 10_000, 16_713, 20_000, 28_000):
        budget, estimated = output_budget(
            "x" * length, ceiling=DEFAULT_MAX_OUTPUT_TOKENS,
            context_tokens=DEFAULT_CONTEXT_TOKENS)
        assert estimated + budget <= DEFAULT_CONTEXT_TOKENS, length
        assert budget <= DEFAULT_MAX_OUTPUT_TOKENS, length


def test_the_token_estimate_is_never_lower_than_the_servers_own_count(ontology):
    """The estimate is only safe if it over-counts, and that is measured, not assumed.

    The ratios measured on the running server over the 13 benchmark prompts run from 3.57 to
    4.55 characters per token. The estimator divides by 3.5, so the recorded extremes are
    checked here against the counts the server returned — a tokenizer change that pushed a real
    prompt below 3.5 characters per token would make the budget able to overflow the slot
    again, and this is where that shows up. The second pair is the one request that truncates,
    counted with its chat template (4,311) rather than by `/tokenize` (4,299), because the
    template is what the estimate has to cover.
    """
    for characters, server_tokens in ((17_170, 4_806), (15_618, 4_311), (7_896, 1_906)):
        _, estimated = output_budget(
            "x" * characters, ceiling=DEFAULT_MAX_OUTPUT_TOKENS,
            context_tokens=DEFAULT_CONTEXT_TOKENS)
        assert estimated >= server_tokens, (characters, server_tokens, estimated)


def test_the_budget_is_a_pure_function_of_the_prompt(ontology):
    """It is digested into the request identity, so a budget that drifted would orphan the
    store."""
    first = output_budget("a" * 5_000, ceiling=4096, context_tokens=8192)
    second = output_budget("a" * 5_000, ceiling=4096, context_tokens=8192)
    assert first == second


def test_the_lane_asks_the_provider_for_the_budget_it_computed(ontology):
    """Driven through the provider protocol rather than read off the lane."""
    class BudgetRecordingProvider(StubProvider):
        def __init__(self):
            super().__init__()
            self.max_tokens: list[int] = []

        def generate(self, *, prompt, schema, max_tokens=1024, temperature=0.0):
            self.max_tokens.append(max_tokens)
            return super().generate(prompt=prompt, schema=schema, max_tokens=max_tokens,
                                    temperature=temperature)

    provider = BudgetRecordingProvider()
    lane = OntologyGuidedNarrativeClaimLane(
        ontology, FixedScope(), provider, deferred_metric_ids(ontology))
    run(lane)
    expected, _ = output_budget(provider.prompts[0], ceiling=DEFAULT_MAX_OUTPUT_TOKENS,
                                context_tokens=DEFAULT_CONTEXT_TOKENS)
    assert provider.max_tokens == [expected]
    assert expected < DEFAULT_MAX_OUTPUT_TOKENS or len(provider.prompts[0]) < 14_000


def test_a_request_that_cannot_fit_is_not_issued_at_all(ontology):
    """Refused before the provider, and said out loud.

    A prompt that leaves less than one claim's worth of room produces a truncated answer with
    certainty. Issuing it anyway spends the call to learn what the character count already
    said, and records it as a model result — `MODEL_ANSWER_UNUSABLE` — when no model was
    involved.
    """
    lane, provider = build_lane(ontology)
    lane_with_a_tiny_slot = OntologyGuidedNarrativeClaimLane(
        ontology, FixedScope(), provider, deferred_metric_ids(ontology),
        context_tokens=2048)
    extraction = lane_with_a_tiny_slot.extract_passage(
        PASSAGE_TEXT,
        context=PassageContext(passage_id=PASSAGE_ID, document_type="shareholder_letter"),
        document_id=DOCUMENT_ID)
    assert provider.prompts == [], "no request may be issued"
    assert [i.code for i in extraction.issues] == [PROMPT_EXCEEDS_CONTEXT]
    assert extraction.claims == []
    assert str(MIN_OUTPUT_TOKENS) in extraction.issues[0].detail


def test_the_refusal_to_issue_is_in_the_declared_vocabulary():
    assert PROMPT_EXCEEDS_CONTEXT in ISSUE_CODES


# -- ambiguity preservation, enforced and not merely requested -----------------------------------


# Both concepts in scope, the ontology declaring "gross margin" ambiguous between them, and the
# sentence printing the bare wording: the exact shape observed live, where the model abstained
# on the Adjusted pair and the lane emitted `gaap_gross_margin` 9.8% anyway.
MARGIN_PASSAGE = (
    "Financial highlights from 3Q23 include: "
    "Revenue of $980 million, or 9.8% gross margin. "
    "Adjusted Gross Margin was 12.1% for the same period."
)
MARGIN_SCOPE = frozenset({"gaap_gross_margin", "adjusted_gross_margin"})


def margin_finding(**overrides) -> dict:
    base = {
        "evidence_sentence": "Revenue of $980 million, or 9.8% gross margin.",
        "metric_id": "gaap_gross_margin",
        "statement_type": "reported_level",
        "subject": "the_filing_company",
        "value_text": "9.8%",
        "value": 9.8,
        "scale": "units",
        "unit": "percent",
        "period_kind": "duration",
        "period_label": "3Q23",
        "population_text": "",
    }
    base.update(overrides)
    return base


def margin_context(ontology, **overrides) -> MappingContext:
    return context(
        passage_text=MARGIN_PASSAGE,
        scope_concept_ids=MARGIN_SCOPE,
        ambiguous_surfaces=ambiguous_surfaces_for(ontology, MARGIN_SCOPE),
        **overrides)


def test_the_ontology_declares_this_pair_ambiguous(ontology):
    """The fixture below is only meaningful while the vocabulary says so. Asserted rather than
    assumed, so a vocabulary edit fails here instead of quietly disarming the next test."""
    surfaces = {s.surface: s.concept_ids for s in ambiguous_surfaces_for(ontology, MARGIN_SCOPE)}
    # Both wordings reach this pair: the bare noun and the two-word form. Either is enough to
    # trigger the check, and listing both is the vocabulary's answer, not a test convenience.
    assert surfaces == {
        "gross margin": ("adjusted_gross_margin", "gaap_gross_margin"),
        "margin": ("adjusted_gross_margin", "gaap_gross_margin"),
    }


def test_a_bare_ambiguous_wording_cannot_select_a_concept(ontology):
    """The live failure, refused. Ambiguity preservation is a hard constraint everywhere else
    in this pipeline; the prompt asked for it and nothing checked it *(review 2026-08-02)*."""
    extraction = map_answer(
        {"claims": [margin_finding()], "abstentions": []},
        ontology=ontology, context=margin_context(ontology))
    assert extraction.claims == []
    issue = extraction.issues[0]
    assert issue.code == AMBIGUOUS_ALIAS
    assert issue.rejected_claim
    assert "gross margin" in issue.detail


def test_the_printed_qualifier_settles_it_and_the_claim_is_emitted(ontology):
    """The other half: refusing the qualified case too would make the lane useless, and the
    check has to be shown to accept what the ontology says is unambiguous."""
    extraction = map_answer(
        {"claims": [margin_finding(
            metric_id="adjusted_gross_margin",
            evidence_sentence="Adjusted Gross Margin was 12.1% for the same period.",
            value_text="12.1%", value=12.1)],
         "abstentions": []},
        ontology=ontology, context=margin_context(ontology))
    assert [c.metric_id for c in extraction.claims] == ["adjusted_gross_margin"], \
        [(i.code, i.detail) for i in extraction.issues]


def test_the_qualifier_must_be_in_the_quoted_sentence_not_merely_the_passage(ontology):
    """`MARGIN_PASSAGE` prints "Adjusted" one sentence later. A rule that searched the whole
    passage would find it there and accept the very claim above."""
    extraction = map_answer(
        {"claims": [margin_finding(metric_id="adjusted_gross_margin")], "abstentions": []},
        ontology=ontology, context=margin_context(ontology))
    assert extraction.claims == []
    assert extraction.issues[0].code == AMBIGUOUS_ALIAS


def test_an_unambiguous_wording_is_untouched_by_the_ambiguity_check(ontology):
    """`homes_sold` shares the declared-ambiguous "homes" with three siblings, and the letter
    prints "Sold". The check must not turn every prose claim into an abstention."""
    lane, _ = build_lane(ontology, {
        "claims": [claim_finding(), money_finding(), population_finding()],
        "abstentions": []})
    extraction = run(lane)
    assert [c.metric_id for c in extraction.claims] == [
        "homes_sold", "contribution_profit", "pct_homes_on_market_gt_120_days"], \
        [(i.code, i.detail) for i in extraction.issues]


def test_the_lane_enforces_the_same_wordings_its_prompt_warns_about(ontology):
    """One derivation read twice. A prompt warning about a list the boundary does not hold is
    how an instruction becomes advisory again."""
    lane, provider = build_lane(ontology)
    run(lane)
    warned = [line for line in provider.prompts[0].splitlines()
              if line.startswith('- "') and "may be any of" in line]
    derived = ambiguous_surfaces_for(ontology, frozenset(SCOPE_IDS))
    assert warned and len(warned) == len(derived)
    for surface in derived:
        assert f'"{surface.surface}"' in provider.prompts[0]


def test_every_rejection_code_is_in_the_declared_vocabulary(ontology):
    """A code the vocabulary does not declare would be invisible to step 11's classification."""
    findings = [
        claim_finding(metric_id="adjusted_ebitda"), claim_finding(unit="percent"),
        claim_finding(period_kind="instant"), claim_finding(evidence_sentence="nowhere"),
        claim_finding(scale="thousands"), claim_finding(value=1),
        claim_finding(period_label="never printed"),
        claim_finding(statement_type="forward_guidance"),
    ]
    extraction = map_answer({"claims": findings, "abstentions": []},
                            ontology=ontology, context=context())
    assert extraction.claims == []
    assert {issue.code for issue in extraction.issues} <= ISSUE_CODES


# -- (7) silence is not abstention -------------------------------------------------------------------


def test_an_empty_answer_produces_abstentions_and_still_names_the_passage(ontology):
    lane, _ = build_lane(ontology, {"claims": [], "abstentions": [{
        "evidence_sentence": "Sold 2,687 homes across our markets.",
        "reason": DEFINITIONAL_NOT_OBSERVATIONAL,
        "metric_ids": ["homes_sold"],
        "detail": "the passage explains the measure"}]})
    result = run(lane).as_lane_result()
    assert result.passage_id == PASSAGE_ID
    assert result.claims == ()
    assert result.abstentions[0].reason == DEFINITIONAL_NOT_OBSERVATIONAL
    assert result.abstentions[0].candidate_metric_ids == ("homes_sold",)


def test_an_abstentions_metric_ids_are_filtered_to_the_scope(ontology):
    """A reason code attached to a concept that was never on the menu would put a concept into
    step 11's classification that this passage never considered *(mutation M12)*."""
    extraction = map_answer(
        {"claims": [], "abstentions": [{
            "evidence_sentence": "Sold 2,687 homes across our markets.",
            "reason": UNRESOLVED_METRIC,
            "metric_ids": ["homes_sold", "adjusted_ebitda", "revenue"],
            "detail": "nothing settles which"}]},
        ontology=ontology, context=context())
    issue = extraction.issues[0]
    assert issue.metric_ids == ("homes_sold",)
    # The raw object still holds what the model said: a filtered view is not a rewritten record.
    assert issue.raw_finding["metric_ids"] == ["homes_sold", "adjusted_ebitda", "revenue"]


def test_a_rejection_stays_distinguishable_from_an_abstention_through_the_protocol(ontology):
    """§5's distinction, preserved across `as_lane_result`.

    A `QUOTED_SPAN_NOT_IN_PASSAGE` is this lane catching a model; an abstention is the model
    behaving. Flattened into one `LaneAbstention`, step 11 could not score them apart, which is
    the whole reason both are recorded *(review 2026-08-02)*."""
    lane_object, _ = build_lane(ontology, {
        "claims": [claim_finding(evidence_sentence="Sold 2,687 homes in forty-four markets.")],
        "abstentions": [{
            "evidence_sentence": "Sold 2,687 homes across our markets.",
            "reason": DEFINITIONAL_NOT_OBSERVATIONAL,
            "metric_ids": ["homes_sold"],
            "detail": "the passage explains the measure"}]})
    lane: ClaimLane = lane_object
    candidate = CandidatePassage(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
        document_type="shareholder_letter", passage_kind="narrative", lane="narrative",
        reason="exact_alias")

    result = lane.extract(candidate, PASSAGE_TEXT)
    by_reason = {a.reason: a for a in result.abstentions}
    assert by_reason[QUOTED_SPAN_NOT_IN_PASSAGE].rejected_claim is True
    assert by_reason[DEFINITIONAL_NOT_OBSERVATIONAL].rejected_claim is False


def test_a_passage_with_no_metric_in_scope_abstains_rather_than_going_silent(ontology):
    lane, provider = build_lane(ontology, scope=FixedScope(("opendoor",)))
    result = run(lane).as_lane_result()
    assert provider.prompts == [], "no request should be made when nothing is in scope"
    assert result.claims == ()
    assert result.abstentions[0].reason == UNRESOLVED_METRIC


def test_an_unusable_answer_is_recorded_rather_than_raised(ontology):
    """A model result, not a fault. A transport failure would propagate instead."""
    lane, _ = build_lane(ontology, error=ProviderResponseError("content is not JSON"))
    result = run(lane).as_lane_result()
    assert result.claims == ()
    assert result.abstentions[0].reason == MODEL_ANSWER_UNUSABLE


# -- (8) end to end through assembly and both validators ---------------------------------------------


def test_every_emitted_claim_survives_assembly_and_both_validators(ontology):
    lane, _ = build_lane(ontology, {
        "claims": [claim_finding(), money_finding(), population_finding()],
        "abstentions": []})
    extraction = run(lane)
    assert len(extraction.claims) == 3, [i.detail for i in extraction.issues]

    result = assemble(list(extraction.claims), ontology=ontology)
    assert result.rejected == []
    assert len(result.claims) == 3

    findings = validate(result.claims, ontology=ontology,
                        passages=InMemoryPassages({PASSAGE_ID: PASSAGE_TEXT}))
    assert findings.ok, [(f.code, f.detail) for f in findings.errors]
    # Not "no warnings": the ontology prefers a table for `contribution_profit`, and reading it
    # out of a letter is weaker evidence rather than a wrong claim. The assertion is that the
    # only warnings are that one, named — a silent `assert not warnings` passed for weeks only
    # because `validate` was dropping the ontology's half of the answer.
    assert {f.code for f in findings.warnings} == {ONTOLOGY_WARNING}
    assert all("unpreferred_source_lane" in f.detail for f in findings.warnings)
    assert [f for f in findings.warnings if "contribution_profit" in f.detail]


def test_the_ontologys_warnings_reach_the_caller(ontology):
    """A warning the validator swallows is a warning step 11 cannot score.

    `unpreferred_source_lane` is the whole point: it says this lane read a metric the ontology
    expects from a table, which is exactly the dimension a second lane is being measured on.
    The live gate reported "0 errors, 0 warnings" while the ontology was raising four of these
    *(found by review 2026-08-02)*."""
    lane, _ = build_lane(ontology, {"claims": [money_finding()], "abstentions": []})
    claims = assemble(list(run(lane).claims), ontology=ontology).claims
    assert claims

    relayed = validate(claims, ontology=ontology,
                       passages=InMemoryPassages({PASSAGE_ID: PASSAGE_TEXT}))
    raw = ontology.validate_claims(claims)
    assert raw.warnings, "this fixture is only meaningful while the ontology warns about it"
    assert len([f for f in relayed.warnings if f.code == ONTOLOGY_WARNING]) == len(raw.warnings)
    assert relayed.ok, "a warning is not an error"


def test_the_emitted_claim_carries_the_values_the_passage_prints(ontology):
    lane, _ = build_lane(ontology, {"claims": [money_finding()], "abstentions": []})
    claim = run(lane).claims[0]
    # The scale is applied to money and recorded with where the word was found.
    assert claim.value == 43_000_000
    assert (claim.unit, claim.currency) == ("USD", "USD")
    assert (claim.scale.scale, claim.scale.location) == ("millions", "inline_prose")
    assert claim.period.key == "2023Q3"
    assert claim.source_lane == LANE_NAME
    # The passage's own bytes, not the model's transcription.
    assert claim.raw_text in PASSAGE_TEXT
    assert claim.extractor_metadata["value_text"] == "$43 million"
    assert claim.extractor_metadata["prompt_version"] == PROMPT_VERSION


def test_no_volatile_statistic_reaches_a_claim(ontology):
    """Latency and token counts are operational. A claim carrying them could not appear in an
    artifact required to be byte-identical."""
    lane, _ = build_lane(ontology, {"claims": [claim_finding()], "abstentions": []})
    extraction = run(lane)
    metadata = json.dumps(extraction.claims[0].extractor_metadata)
    for banned in ("latency", "prompt_tokens", "completion_tokens", "total_tokens"):
        assert banned not in metadata
    # They are still available, on the object that is never persisted.
    assert lane.stats[0].completion_tokens == 567
    assert lane.stats[0].latency_ms > 0


# -- (9) the §7 policies, applied in assembly for both lanes -------------------------------------------


def test_the_recognition_point_ambiguity_travels_with_every_homes_sold_claim(ontology):
    """§7.3, derived from the ontology rather than from a metric id written down in code."""
    assert declared_ambiguity_codes(ontology, "homes_sold") == ("homes_sold_recognition_point",)
    lane, _ = build_lane(ontology, {"claims": [claim_finding()], "abstentions": []})
    result = assemble(list(run(lane).claims), ontology=ontology)
    assert result.claims[0].extractor_metadata["ambiguity_codes"] == [
        "homes_sold_recognition_point"]


def test_the_population_wording_is_carried_verbatim(ontology):
    """§7.1. Three filed wordings, no filing reconciles them, so the wording travels."""
    lane, _ = build_lane(ontology, {"claims": [population_finding()], "abstentions": []})
    claim = run(lane).claims[0]
    assert claim.population_definition_raw in PASSAGE_TEXT

    result = assemble([claim], ontology=ontology)
    population = result.claims[0].metric_observation.population
    assert population.definition_raw == claim.population_definition_raw


def test_a_population_metric_without_its_wording_is_refused_by_the_lane(ontology):
    extraction = map_answer(
        {"claims": [population_finding(population_text="")], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    assert extraction.issues[0].code == MISSING_POPULATION_DEFINITION


def test_a_population_wording_the_passage_does_not_print_is_refused(ontology):
    """§7.1 exists to record the **filed** wording, so an invented one is worse than none.

    `population_definition_raw` is the one model-authored free-text field that reaches an
    `OntologyClaim` body, and `validate_quoted_text` inspects `raw_text` only — so a boundary
    that accepted a wording it had not found in the passage let a fabricated denominator pass
    assembly and verify with nothing left to catch it *(mutation M11, review 2026-08-02)*."""
    extraction = map_answer(
        {"claims": [population_finding(
            population_text="18% of the homes in our nationwide portfolio")],
         "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims == []
    assert extraction.issues[0].code == MISSING_POPULATION_DEFINITION
    assert "not printed in the passage" in extraction.issues[0].detail


def test_the_population_wording_carried_is_the_passages_slice_not_the_answers(ontology):
    """Retyped whitespace is a transcription artifact; what is recorded is the filing's bytes,
    for the same reason `raw_text` is."""
    text = ("As of September 30, 2023, 18% of our  homes\nin inventory had been listed "
            "on the market for more than 120 days.")
    extraction = map_answer(
        {"claims": [population_finding(
            evidence_sentence=("18% of our homes in inventory had been listed on the market "
                               "for more than 120 days."),
            population_text="18% of our homes in inventory")],
         "abstentions": []},
        ontology=ontology,
        context=context(passage_text=text,
                        scope_concept_ids=frozenset({"pct_homes_on_market_gt_120_days"})))
    assert extraction.claims, [(i.code, i.detail) for i in extraction.issues]
    wording = extraction.claims[0].population_definition_raw
    assert wording == "18% of our  homes\nin inventory"
    assert wording in text


def test_a_population_metric_without_its_wording_is_refused_again_by_assembly(ontology):
    """Enforced at both ends on purpose: the lane is the only thing that can read the wording
    out of a passage, and assembly is the only thing both lanes go through."""
    claim = LaneClaim(
        metric_id="pct_homes_on_market_gt_120_days", value=18, unit="percent",
        period=PeriodRef(instant_date="2023-09-30"),
        source_lane=LANE_NAME, passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
        raw_text="18% of our homes")
    result = assemble([claim], ontology=ontology)
    assert result.claims == []
    assert result.rejected[0].reason == MISSING_POPULATION_DEFINITION


def test_assembly_still_emits_both_observations_rather_than_a_supersedes_edge(ontology):
    lane, _ = build_lane(ontology, {"claims": [claim_finding()], "abstentions": []})
    first = run(lane).claims[0]
    second = first.model_copy(update={
        "passage_id": "norm:a:b:tenq.htm#p4", "document_id": "norm:a:b:tenq.htm"})
    result = assemble([first, second], ontology=ontology)
    assert len({c.metric_observation.observation_id for c in result.claims}) == 2
    assert all("supersedes" not in str(c.extractor_metadata).lower() for c in result.claims)


# -- (10) the evidence anchor does not move -------------------------------------------------------------


def test_no_sub_passage_identifier_is_ever_minted(ontology):
    """§8a.11: a span is a check and a provenance note inside a passage-anchored claim, never
    an anchor of its own. A `#p10:s3` would not resolve in `passages.jsonl` and `verify` would
    fail it by construction."""
    lane, _ = build_lane(ontology, {
        "claims": [claim_finding(), money_finding(), population_finding()],
        "abstentions": []})
    result = assemble(list(run(lane).claims), ontology=ontology)
    for claim in result.claims:
        for reference in claim.payload_evidence:
            assert reference.passage_id == PASSAGE_ID
            assert reference.char_start is None and reference.char_end is None
            assert claim.metric_observation.observation_id.count(PASSAGE_ID) == 0


def test_every_evidence_anchor_resolves_in_the_real_passage_catalog(ontology, repo_config):
    """Driven against the corpus, not a fixture: the check that would catch a synthetic
    anchor is worth nothing against a passage source that was built to contain it."""
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    rows = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    if PASSAGE_ID not in rows:
        pytest.skip(f"{PASSAGE_ID} is not in this corpus")

    text = rows[PASSAGE_ID]["text"]
    lane, _ = build_lane(ontology, {"claims": [{
        "evidence_sentence": "Sold 2,687 homes which generated $980 million of revenue",
        "metric_id": "homes_sold", "statement_type": "reported_level",
        "subject": "the_filing_company", "value_text": "2,687", "value": 2687,
        "scale": "units", "unit": "homes", "period_kind": "duration",
        "period_label": "3Q23", "population_text": ""}], "abstentions": []})
    extraction = lane.extract_passage(
        text,
        context=PassageContext(passage_id=PASSAGE_ID, document_type="shareholder_letter"),
        document_id=rows[PASSAGE_ID]["document_id"])
    assert extraction.claims, [i.detail for i in extraction.issues]

    result = assemble(list(extraction.claims), ontology=ontology, passage_rows=rows)

    class CatalogPassages:
        def text_of(self, passage_id):
            row = rows.get(passage_id)
            return row["text"] if row else None

        def exists(self, passage_id):
            return passage_id in rows

        def document_of(self, passage_id):
            row = rows.get(passage_id)
            return row["document_id"] if row else None

    findings = validate(result.claims, ontology=ontology, passages=CatalogPassages())
    assert findings.ok, [(f.code, f.detail) for f in findings.errors]
    assert not findings.warnings, [(f.code, f.detail) for f in findings.warnings]
    for claim in result.claims:
        for reference in claim.payload_evidence:
            assert reference.passage_id in rows


# -- (11)(12) the prompt is pure, versioned, and carries the declarations -------------------------------


def test_prompt_construction_is_pure(ontology):
    concepts = [ontology.registry.find(c) for c in ("homes_sold", "homes_purchased")]
    schema = response_schema(("homes_sold", "homes_purchased"), ("homes",), ("3Q23",))
    passage_context = PassageContext(
        passage_id=PASSAGE_ID, document_type="shareholder_letter", form="8-K",
        filing_date="2023-11-02", heading_path=("EX-99.2",))
    first = build_prompt(text=PASSAGE_TEXT, concepts=concepts, context=passage_context,
                         schema=schema, period_phrases=("3Q23",))
    second = build_prompt(text=PASSAGE_TEXT, concepts=concepts, context=passage_context,
                          schema=schema, period_phrases=("3Q23",))
    assert first == second
    assert PROMPT_VERSION


def test_the_prompt_carries_the_distinct_from_siblings_the_scope_supplied(ontology):
    """The scope pulls confusion siblings in precisely so the model can be told "this is
    `homes_sold`, and `homes_purchased` is a different metric it is not"."""
    lane, provider = build_lane(ontology)
    run(lane)
    prompt = provider.prompts[0]
    assert "NOT the same metric as" in prompt
    assert "homes_purchased" in prompt
    assert "homes_sold" in prompt


def test_the_prompt_carries_the_ontologys_ambiguity_notes(ontology):
    """Declared on the alias, not on either concept, so a per-concept rendering misses them —
    and without the note the model read a bare "gross profit" as the adjusted metric."""
    lane, provider = build_lane(ontology)
    run(lane)
    prompt = provider.prompts[0]
    assert "AMBIGUOUS" in prompt
    assert "Adjusted" in prompt


def _rule_block(prompt: str) -> str:
    """The numbered rules, without the schema dump or the concept renderings around them."""
    body = prompt.split("\nRULES\n", 1)[1]
    return body.split("\nAnswer with JSON", 1)[0]


def test_every_field_the_rules_constrain_is_a_field_the_model_is_given(ontology):
    """A rule naming a field the schema lacks is an instruction the model cannot follow.

    Three of ten rules constrained a `quoted_span`; the schema field is `evidence_sentence`
    and always was *(review 2026-08-02)*. Checked by walking the rule text rather than by
    grepping for one name, so the next renamed field fails here too."""
    lane, provider = build_lane(ontology)
    run(lane)
    schema = provider.schemas[0]
    items = [schema["properties"][key]["items"] for key in ("claims", "abstentions")]
    known = {name for item in items for name in item["properties"]}
    for item in items:
        for spec in item["properties"].values():
            known.update(spec.get("enum", ()) or ())
            known.update((spec.get("items") or {}).get("enum", ()) or ())

    named = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", _rule_block(provider.prompts[0])))
    assert named, "the rules stopped naming any field, which is not an improvement"
    assert named <= known, sorted(named - known)


def test_the_rules_point_at_the_period_phrases_where_they_are_actually_rendered(ontology):
    """Rule 6 said "listed below"; the phrases are rendered above it."""
    lane, provider = build_lane(ontology)
    run(lane)
    prompt = provider.prompts[0]
    assert prompt.index("PERIOD PHRASES THIS PASSAGE STATES") < prompt.index("\nRULES\n")
    rules = _rule_block(prompt)
    assert "listed above" in rules and "listed below" not in rules


def test_the_prompt_carries_the_passage_and_nothing_from_another(ontology):
    lane, provider = build_lane(ontology)
    run(lane)
    prompt = provider.prompts[0]
    assert PASSAGE_TEXT in prompt
    assert "shareholder_letter" in prompt and "2023-11-02" in prompt


def test_the_schema_offers_only_the_scope_and_the_passages_own_periods(ontology):
    lane, provider = build_lane(ontology)
    run(lane)
    schema = provider.schemas[0]
    item = schema["properties"]["claims"]["items"]["properties"]
    assert set(item["metric_id"]["enum"]) == set(SCOPE_IDS)
    # The passage's own phrases, then the one answer that is not a phrase — see
    # `test_the_enum_can_say_that_no_printed_phrase_gives_this_figures_period`.
    assert item["period_label"]["enum"] == [
        "3Q23", "September 30, 2023", PERIOD_NOT_PRINTED]
    assert item["unit"]["enum"] == ["USD", "homes", "percent"]
    # `evidence_sentence` is generated first: quote, then answer.
    assert list(item)[0] == "evidence_sentence"


def test_a_deferred_metric_stays_on_the_menu_so_naming_it_is_measurable(ontology):
    """Removing it would turn a recorded `DEFERRED_REQUIRED_SOURCE_LANE` into an
    impossibility, which hides the model's behaviour rather than measuring it."""
    lane, provider = build_lane(ontology)
    run(lane)
    enum = provider.schemas[0]["properties"]["claims"]["items"]["properties"]["metric_id"]["enum"]
    assert "gaap_gross_profit" in enum
    assert "gaap_gross_profit" in deferred_metric_ids(ontology)


# -- (13) replay ------------------------------------------------------------------------------------------


def test_replay_from_the_store_reproduces_the_claims_byte_for_byte(ontology, tmp_path):
    """Byte-identical *replay*, which is achievable, and not byte-identical *generation*,
    which STAGE_09 established the local runtime does not promise."""
    store = AnswerStore(tmp_path / "answers.jsonl")
    recording = ReplayingGenerationProvider(
        store, StubProvider({"claims": [claim_finding(), money_finding()], "abstentions": []}),
        prompt_version=PROMPT_VERSION)
    lane = OntologyGuidedNarrativeClaimLane(
        ontology, FixedScope(), recording, deferred_metric_ids(ontology))
    first = run(lane)
    store.write()

    replay_only = ReplayingGenerationProvider(
        AnswerStore(tmp_path / "answers.jsonl"), None, model_id="stub-model")
    replayed_lane = OntologyGuidedNarrativeClaimLane(
        ontology, FixedScope(), replay_only, deferred_metric_ids(ontology))
    second = run(replayed_lane)

    assert [c.model_dump() for c in first.claims] == [c.model_dump() for c in second.claims]
    assert replayed_lane.stats[0].replayed


def test_the_persisted_answer_holds_no_volatile_statistic(tmp_path):
    store = AnswerStore(tmp_path / "answers.jsonl")
    provider = ReplayingGenerationProvider(store, StubProvider({"claims": [], "abstentions": []}),
                         prompt_version=PROMPT_VERSION)
    provider.generate(prompt="p", schema={"type": "object"}, max_tokens=64)
    rendered = store.render()

    for banned in ("latency", "prompt_tokens", "completion_tokens", "total_tokens",
                   "raw_sha256", "attempts", "timings", "created"):
        assert banned not in rendered, f"{banned} reached the persisted record"
    assert json.loads(rendered.splitlines()[0])["content_sha256"] == "1" * 64


def test_the_store_is_a_rebuilt_index(tmp_path):
    """Rows sorted by request digest and the file rewritten whole. An append-only log of
    answers grows a duplicate every time a passage is re-read."""
    path = tmp_path / "nested" / "answers.jsonl"
    store = AnswerStore(path)
    provider = ReplayingGenerationProvider(store, StubProvider({"claims": [], "abstentions": []}))
    for prompt in ("b", "a", "c"):
        provider.generate(prompt=prompt, schema={"type": "object"}, max_tokens=64)
    store.write()

    digests = [json.loads(line)["request_sha256"] for line in path.read_text().splitlines()]
    assert digests == sorted(digests)
    assert not list(path.parent.glob("*.partial"))

    store.write()
    assert path.read_text() == store.render()


def test_the_store_stages_and_renames_rather_than_writing_in_place(tmp_path, monkeypatch):
    """Atomic finalization, asserted as a rename of a complete file onto a target that did not
    exist yet.

    "No `.partial` is left behind" is satisfied by never creating one, so a store writing
    straight to the target passed that check unchanged *(mutation M24, review 2026-08-02)*.
    What has to be true is that the target is only ever produced by `os.replace` of a fully
    written staging file — a reader that finds the file finds all of it or none of it."""
    path = tmp_path / "nested" / "answers.jsonl"
    store = AnswerStore(path)
    ReplayingGenerationProvider(store, StubProvider({"claims": [], "abstentions": []})).generate(
        prompt="p", schema={"type": "object"}, max_tokens=64)

    renames = []
    real_replace = os.replace

    def recording_replace(source, destination):
        renames.append((Path(source), Path(destination),
                        Path(source).read_text(encoding="utf-8"),
                        Path(destination).exists()))
        real_replace(source, destination)

    monkeypatch.setattr(answer_store.os, "replace", recording_replace)
    store.write()

    assert renames, "write() never renamed anything into place: it wrote the target directly"
    source, destination, staged, target_existed_first = renames[0]
    assert destination == path
    assert source != path, "the staging file must not be the target"
    assert staged == store.render(), "the staging file was renamed before it was complete"
    assert not target_existed_first, "the target existed before the rename"
    assert path.read_text(encoding="utf-8") == store.render()
    assert not list(path.parent.glob("*.partial"))


def test_a_replay_only_store_refuses_rather_than_reaching_for_a_server(tmp_path):
    provider = ReplayingGenerationProvider(
        AnswerStore(tmp_path / "empty.jsonl"), None, model_id="stub-model")
    with pytest.raises(MissingAnswerError):
        provider.generate(prompt="unseen", schema={"type": "object"})


def test_an_empty_store_with_no_model_id_refuses_to_be_constructed(tmp_path):
    """The fallback below reads the identity off the rows, and an empty file has none."""
    with pytest.raises(ValueError):
        ReplayingGenerationProvider(AnswerStore(tmp_path / "empty.jsonl"), None)


# The two identifiers the live runtime actually reports. `/v1/models` answers with the absolute
# path; `config/extraction.yaml` names the basename, and `request_identity` digests the second.
WIRE_MODEL_ID = "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf"
CONFIGURED_MODEL_ID = "Qwen3.5-9B-Q4_K_M.gguf"


class WireNamedStubProvider(StubProvider):
    """A provider whose reported model id is not the one the run identifies it by."""

    model_id = WIRE_MODEL_ID


def test_the_stored_answer_states_the_identity_it_is_keyed_under(tmp_path):
    """A replay reconstructed from the file alone must hit, and used to miss every row.

    The record stored the *wire* id while the digest was taken over the configured one, so
    anything configured from the file's own `model_id` keyed its lookups on a string the
    digest was never computed with — `REPLAY MISS` on every row, verified against the live
    server *(review 2026-08-02)*. The offline suite could not see it because the stub reports
    the same id it is identified by; this provider does not."""
    path = tmp_path / "answers.jsonl"
    store = AnswerStore(path)
    recording = ReplayingGenerationProvider(
        store, WireNamedStubProvider({"claims": [], "abstentions": []}),
        model_id=CONFIGURED_MODEL_ID, prompt_version=PROMPT_VERSION)
    recorded = recording.generate(prompt="p", schema={"a": 1}, max_tokens=64)
    store.write()

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["model_id"] == CONFIGURED_MODEL_ID, "the file must state its own digest key"
    assert row["provider_model_id"] == WIRE_MODEL_ID
    assert row["request_sha256"] == request_identity(
        prompt="p", schema={"a": 1}, model_id=CONFIGURED_MODEL_ID,
        temperature=0.0, max_tokens=64)

    # Reconstructed from the file and nothing else: no configuration, no server, no argument.
    replay_only = ReplayingGenerationProvider(AnswerStore(path), None)
    replayed = replay_only.generate(prompt="p", schema={"a": 1}, max_tokens=64)
    assert replayed.metadata["replayed"] is True
    assert replayed.content_sha256 == recorded.content_sha256
    # And the replayed result still reports what the *server* called itself, so a claim built
    # from a replay carries the same `provider_model_id` the recorded run put on it.
    assert replayed.model_id == recorded.model_id == WIRE_MODEL_ID


def test_the_request_identity_covers_everything_that_decided_the_answer():
    base = dict(prompt="p", schema={"a": 1}, model_id="m", temperature=0.0, max_tokens=64)
    identity = request_identity(**base)
    assert identity == request_identity(**{**base, "schema": {"a": 1}})
    for field, value in (("prompt", "q"), ("model_id", "n"),
                         ("temperature", 0.7), ("max_tokens", 128)):
        assert request_identity(**{**base, field: value}) != identity
    # The schema is half of what was asked: two runs differing only in the enum the model was
    # constrained to are two different requests, and a digest that ignored the schema would
    # replay one passage's answer for another's grammar *(mutation M25, review 2026-08-02)*.
    for schema in ({"a": 2}, {"b": 1}, {}, {"a": 1, "b": 2}):
        assert request_identity(**{**base, "schema": schema}) != identity, schema
    # Key order is not part of what was asked.
    assert request_identity(**{**base, "schema": {"a": 1, "b": 2}}) == request_identity(
        **{**base, "schema": {"b": 2, "a": 1}})


# -- the readers the boundary depends on ---------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("2,687", (2687.0, None)),
    ("$43 million", (43.0, "millions")),
    ("$(49) million", (-49.0, "millions")),
    ("(6.2)%", (-6.2, None)),
    ("17,164 homes", (17164.0, None)),
    ("$6.6 billion", (6.6, "billions")),
    ("9.8%", (9.8, None)),
])
def test_a_printed_figure_is_read_as_printed(text, expected):
    assert printed_magnitude(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("$43 million (Contribution Profit)", (43.0, "millions")),
    ("9.8% gross margin (GAAP)", (9.8, None)),
    ("2,687 homes (resale)", (2687.0, None)),
    ("Adjusted Gross Profit (Loss) of $96 million", (96.0, "millions")),
])
def test_an_incidental_parenthetical_is_not_an_accounting_negative(text, expected):
    """The accounting convention applies to parentheses that wrap the *number*.

    A cell holds a bare figure and a sentence holds a figure with its label, so a rule written
    as "the phrase contains a bracket" reads `$43 million (Contribution Profit)` as a loss of
    43 million — right metric, right magnitude, wrong sign, and every downstream check passing
    *(found by review 2026-08-02)*. The parenthesised forms above stay negative."""
    assert printed_magnitude(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("$(49) million", (-49.0, "millions")),
    ("(6.2)%", (-6.2, None)),
    ("(17,340)", (-17340.0, None)),
])
def test_a_parenthesised_number_is_still_negative(text, expected):
    assert printed_magnitude(text) == expected


@pytest.mark.parametrize("text", [
    "$149.7 million to $169.7 million",   # two figures, no defensible choice
    "several",
    "",
])
def test_an_unreadable_figure_is_not_guessed_at(text):
    assert printed_magnitude(text) is None


def test_the_passages_own_periods_are_read_by_core_periods():
    phrases = period_phrases(
        "During the three months ended September 30, 2021 and in 4Q21, as of "
        "December 31, 2021, revenue grew.")
    assert phrases[0] == "three months ended September 30, 2021"
    assert "4Q21" in phrases and "December 31, 2021" in phrases


def test_a_period_the_text_only_names_vaguely_yields_no_phrase():
    assert period_phrases("During the third quarter, revenue grew.") == ()


def test_a_retyped_span_is_recorded_as_the_passages_own_bytes(ontology):
    """The model retypes; the corpus stores. What is recorded is always the slice of the
    passage, so `validate_quoted_text` compares the catalog against itself."""
    text = "As of September 30, 2023, 18% of our  homes\nhad been listed on the market."
    extraction = map_answer(
        {"claims": [{
            "evidence_sentence": "18% of our homes had been listed on the market.",
            "metric_id": "pct_homes_on_market_gt_120_days",
            "statement_type": "reported_level", "subject": "the_filing_company",
            "value_text": "18%", "value": 18, "scale": "units", "unit": "percent",
            "period_kind": "instant", "period_label": "September 30, 2023",
            "population_text": "18% of our homes had been listed on the market"}],
         "abstentions": []},
        ontology=ontology,
        context=context(passage_text=text,
                        scope_concept_ids=frozenset({"pct_homes_on_market_gt_120_days"})))
    assert extraction.claims, [i.detail for i in extraction.issues]
    assert extraction.claims[0].raw_text == "18% of our  homes\nhad been listed on the market."


# The passage prints curly quotes *before* the sentence the claim is read from, which is the
# only shape that exposes the defect: `fold` deletes them, so offsets taken over the folded
# string index the original one character short per deleted character.
CURLY_QUOTE_PASSAGE = (
    "Footnote “1” and note “2” apply to 3Q23. "
    "Sold 2,687 homes across our “core” markets."
)


def test_a_folded_match_records_the_sentence_and_not_a_shifted_slice(ontology):
    """The recorded span must EQUAL the sentence, not merely be found somewhere in the passage.

    `raw_text in text` cannot detect this: a slice shifted by two characters is still a
    contiguous run of the passage, so `validate_quoted_text` stayed silent while the claim
    carried `'. Sold 2,687 homes across our “core” mark'` *(found by review 2026-08-02;
    5,967 of the corpus's 12,442 passages contain a fold-deleted character)*."""
    expected = "Sold 2,687 homes across our “core” markets."
    extraction = map_answer(
        {"claims": [claim_finding(
            # Straight quotes, as a model retypes them: this forces the folded path.
            evidence_sentence='Sold 2,687 homes across our "core" markets.')],
         "abstentions": []},
        ontology=ontology, context=context(passage_text=CURLY_QUOTE_PASSAGE))
    assert extraction.claims, [i.detail for i in extraction.issues]
    claim = extraction.claims[0]
    assert claim.raw_text == expected
    assert claim.extractor_metadata["span_match"] == "folded"
    start = claim.extractor_metadata["span_char_start"]
    end = claim.extractor_metadata["span_char_end"]
    assert CURLY_QUOTE_PASSAGE[start:end] == expected


def test_the_span_readers_index_map_survives_a_deleting_fold():
    """The unit-level statement of the same thing, at the layer that owns it.

    Two curly quotes before the match and two inside it: four fold-deleted characters, of
    which two precede the start offset. The exact path cannot match — the candidate is retyped
    with straight quotes — so this drives the folded path on purpose."""
    text = 'Note “1”. Sold 2,687 “homes” today.'
    span = locate(text, 'Sold 2,687 "homes" today.', fold=fold)
    assert (span.text, span.basis) == ('Sold 2,687 “homes” today.', "folded")
    assert text[span.start:span.end] == 'Sold 2,687 “homes” today.'
    assert text[span.start] == "S"


def test_the_fold_is_character_wise_so_the_index_map_is_a_map():
    """`text_spans.normalize` applies the transform one character at a time and calls the
    result a map back to the original. That is only sound while the fold is character-wise, so
    it is asserted against the real fold rather than assumed of a callable."""
    sample = 'Note “1” — as of March 31, 2023, 59% of our homes were listed.'
    assert "".join(fold(character) for character in sample) == fold(sample)


def test_a_span_containing_no_fold_deleted_character_is_found_exactly(ontology):
    """The exact path still reports itself as exact; the folded path is the weaker answer."""
    extraction = map_answer(
        {"claims": [claim_finding()], "abstentions": []},
        ontology=ontology, context=context())
    assert extraction.claims[0].extractor_metadata["span_match"] == "exact"


def test_the_unit_rule_is_one_the_ontology_accepts(ontology):
    """The invariant a shared unit rule exists to keep. `check_observation_unit` allows a
    metric's declared unit and its `allowed_units` and nothing else, so a lane emitting
    anything outside that set produces claims that cannot pass §4.5's verify."""
    offenders = []
    for metric in ontology.registry.by_category("metric_definition"):
        unit, _ = unit_for_metric(metric)
        allowed = set(metric.allowed_units) | {metric.unit}
        if unit is not None and unit not in allowed:
            offenders.append((metric.concept_id, unit, sorted(allowed)))
    assert offenders == []


# -- architectural rules, executable -----------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _string_literals(path: Path) -> list[str]:
    """Every string constant in the file except the docstrings.

    Docstrings are excluded because they are prose: several modules under `extraction/` say in
    words that they carry no benchmark, and a check that read those as path literals would be
    a check nobody could keep green. Comments are not in the tree at all, which is correct —
    a comment cannot open a directory.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_nothing_under_extraction_imports_or_names_the_benchmark():
    """The hard constraint: benchmark gold may not reach a runtime decision.

    Checked as an import rule *and* over the file's string constants, because a
    `Path(...) / "benchmarks"` needs no import. The literal half used to be a substring test
    against the source text, which made it quote-sensitive: writing the same path with single
    quotes passed it *(mutation M31, review 2026-08-02)*. Reading the constants out of the
    parse tree makes quoting irrelevant, which is the only version of this check worth having.
    """
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        for name in _imports(path):
            if "benchmark" in name.lower():
                offenders.append(f"{path.relative_to(PACKAGE)}: imports {name}")
        for literal in _string_literals(path):
            if "benchmark" in literal.lower():
                offenders.append(f"{path.relative_to(PACKAGE)}: names {literal!r}")
    assert offenders == []


def test_the_benchmark_guard_would_notice_a_path_written_any_way(tmp_path):
    """The guard above, driven against files that hold what it is looking for.

    Written because the previous version was unfalsifiable by inspection: it looked green and
    two of these four spellings walked straight through it."""
    for index, source in enumerate((
            "PATH = 'benchmarks/extraction/v1'\n",
            'PATH = "benchmarks/extraction/v1"\n',
            "ROOT = Path(__file__) / 'benchmarks'\n",
            "def f():\n    return {'dir': 'BENCHMARKS'}\n")):
        path = tmp_path / f"offender_{index}.py"
        path.write_text(source, encoding="utf-8")
        assert any("benchmark" in literal.lower() for literal in _string_literals(path)), source
    clean = tmp_path / "clean.py"
    clean.write_text('"""This module names no benchmark."""\nX = "passages.jsonl"\n',
                     encoding="utf-8")
    assert not [l for l in _string_literals(clean) if "benchmark" in l.lower()]


# Everything outside `extraction` that the narrative lane, and anything it reaches, may name.
# An **allowlist**, because the banned list it replaces was unfalsifiable: `import http.client`
# passed it untouched, and so would `aiohttp`, `httpcore` or `pycurl` *(mutation M32a, review
# 2026-08-02)*. Naming what may be imported is a list that fails closed; naming what may not is
# a list that fails open, and this rule exists precisely to hold when nobody is looking.
#
# Every entry is standard library and none of them can open a socket, with two exceptions
# stated rather than smuggled in: `ontology` is the vocabulary this whole package exists to
# serve and is pure data plus validation, and `pydantic` is where `LaneClaim` is declared.
# Neither carries a transport.
NARRATIVE_IMPORT_ALLOWLIST = frozenset({
    "__future__", "dataclasses", "hashlib", "json", "os", "pathlib", "re", "typing",
    "ontology", "pydantic",
    # `narrative_lane.output_budget` rounds a character count up to a token estimate. Standard
    # library arithmetic, no socket *(added 2026-08-02 with the prompt-aware output budget)*.
    "math",
    # `core.periods.parse_printed_date` validates a printed day against its month through
    # `datetime.date`, so "February 31, 2022" is refused rather than formatted. The calendar
    # is standard library and opens nothing *(added 2026-08-02 with the event lane's date
    # reader; the guard caught it, which is what an allowlist that fails closed is for)*.
    "datetime",
})


def _absolute_imports(path: Path, module: str) -> set[str]:
    """Imported module names, with relative imports resolved against `module`'s package.

    A package's `__init__.py` *is* its package, so `from .answer_store import X` in
    `narrative/__init__.py` resolves one level shallower than the same statement in a sibling
    module. Getting that wrong makes every intra-package edge look foreign, which is loud
    rather than silent — but it also stops the walk from ever leaving the directory.
    """
    package = (module if path.name == "__init__.py"
               else (module.rsplit(".", 1)[0] if "." in module else module))
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    found.add(node.module)
                continue
            parts = package.split(".")
            base = parts[:len(parts) - (node.level - 1)] or parts[:1]
            found.add(".".join(base + ([node.module] if node.module else [])))
    return found


def _first_party_module(name: str) -> tuple[str, Path] | None:
    """`name` as a module under `extraction/`, or None if it is not one of ours."""
    if name != "extraction" and not name.startswith("extraction."):
        return None
    relative = Path(*name.split(".")[1:])
    for candidate in (PACKAGE / relative.with_suffix(".py"), PACKAGE / relative / "__init__.py"):
        if candidate.is_file():
            return name, candidate
    return None


def _reachable_from(roots: list[tuple[str, Path]]) -> dict[str, set[str]]:
    """The transitive import graph from `roots`, following first-party modules only.

    Returns each reached module's *foreign* imports — the names that are not ours. Walking the
    graph is what makes this a check over the import graph rather than over one directory: an
    HTTP client pulled in by something the lane imports is exactly as reachable as one the lane
    imports itself, and a per-directory check cannot see the difference.
    """
    seen: dict[str, set[str]] = {}
    queue = list(roots)
    while queue:
        module, path = queue.pop()
        if module in seen:
            continue
        foreign: set[str] = set()
        for name in _absolute_imports(path, module):
            ours = _first_party_module(name)
            if ours is None:
                foreign.add(name)
            elif ours[0] not in seen:
                queue.append(ours)
        seen[module] = foreign
    return seen


def _narrative_roots() -> list[tuple[str, Path]]:
    roots = []
    for path in sorted((PACKAGE / "stages" / "narrative").rglob("*.py")):
        relative = path.relative_to(PACKAGE).with_suffix("")
        parts = [part for part in relative.parts if part != "__init__"]
        roots.append(("extraction." + ".".join(parts), path))
    return roots


def test_nothing_the_narrative_lane_reaches_can_name_a_wire_format():
    """No HTTP client, under any name, anywhere the lane's imports lead.

    The rule the previous version *claimed* and did not check. It compared each file's import
    names against a fixed list of seven strings, so `import http.client` in `narrative_lane.py`
    left the whole suite green *(mutation M32a)*, as would `aiohttp`, `httpcore` or `pycurl`.
    This walks the transitive graph and holds every module it reaches — the narrative package
    and, through it, `providers.public`, `contracts` and `core` — to an allowlist of standard
    library modules, none of which can open a socket.

    **The graph followed is the one the modules write, not the one the interpreter builds.**
    Measured 2026-08-02: importing `extraction.stages.narrative` in a fresh interpreter does
    load `httpx`, because `extraction/providers/__init__.py` re-exports the adapter and Python
    executes a package's `__init__` before its submodule. No narrative module names it and none
    can reach it by following an import statement; the reachability is the port package's
    convenience re-export, and it is recorded here rather than defined away.
    """
    graph = _reachable_from(_narrative_roots())
    offenders = sorted(
        f"{module}: {name}"
        for module, foreign in graph.items()
        for name in foreign
        if name.split(".")[0] not in NARRATIVE_IMPORT_ALLOWLIST)
    assert offenders == [], (
        "an import outside the allowlist. If it is legitimate, add it there with a reason — "
        "the point of an allowlist is that the addition is visible in a diff")
    # The walk must have left the package it started in, or it is proving nothing about depth.
    assert "extraction.providers.public" in graph and "extraction.core.periods" in graph


def test_the_import_graph_check_would_notice_an_http_client(tmp_path):
    """The check above, driven against the four spellings that walked through its predecessor.

    An executable rule nobody has seen fail is a rule that might be checking nothing."""
    for index, statement in enumerate(("import http.client", "import aiohttp",
                                       "from httpcore import connect", "import pycurl as c")):
        path = tmp_path / f"wire_{index}.py"
        path.write_text(statement + "\n", encoding="utf-8")
        foreign = _absolute_imports(path, "extraction.stages.narrative.x")
        assert foreign, statement
        assert [n for n in foreign if n.split(".")[0] not in NARRATIVE_IMPORT_ALLOWLIST], \
            statement


def test_the_import_graph_check_resolves_relative_imports():
    """The graph is only transitive if `from ...providers.public import X` resolves. If it did
    not, every first-party edge would look foreign and the allowlist would fail loudly rather
    than silently — but the depth assertion above would be meaningless."""
    lane = PACKAGE / "stages" / "narrative" / "narrative_lane.py"
    resolved = _absolute_imports(lane, "extraction.stages.narrative.narrative_lane")
    assert "extraction.providers.public" in resolved
    assert "extraction.core.periods" in resolved
    assert "extraction.stages.narrative.public" in resolved


def test_importing_the_narrative_package_does_not_load_an_http_client():
    """The import-graph test above reads source; this one reads `sys.modules`.

    The two disagreed. No narrative module names `httpx` and none reaches it by following an
    import statement, yet importing the package loaded it anyway: `extraction/providers/`
    re-exported its adapters eagerly, and Python runs a package's `__init__` before any
    submodule of it, so `from ...providers.public import ...` dragged the wire format in.
    "No HTTP client is reachable from a lane at runtime" was false in the interpreter while
    true in the source, and only a fresh interpreter can tell the difference — inside the
    suite `httpx` is already loaded by the provider tests.

    The adapters are resolved lazily now. This test is what keeps that from being undone.
    """
    probe = (
        "import sys; import extraction.stages.narrative; "
        "print('httpx' in sys.modules or 'httpcore' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True,
        cwd=str(PACKAGE.parent), check=True)
    assert result.stdout.strip() == "False", result.stdout + result.stderr


def test_the_narrative_lane_reaches_a_provider_only_through_the_port():
    """It may know the provider's error vocabulary — that is the port — and may not know the
    adapter that holds the wire format."""
    for module, path in _narrative_roots():
        for name in _absolute_imports(path, module):
            if "provider" in name.lower():
                assert name == "extraction.providers.public", f"{module}: {name}"


def test_no_provider_is_reachable_from_the_table_lane():
    """Unchanged by this stage and re-asserted here: the deterministic lane stays offline."""
    for path in (PACKAGE / "stages" / "tables").rglob("*.py"):
        for name in _imports(path):
            assert "provider" not in name.lower(), f"{path.name}: {name}"


def test_core_never_imports_a_stage():
    for path in (PACKAGE / "core").rglob("*.py"):
        for name in _imports(path):
            assert ".stages" not in name and not name.startswith("stages"), name
