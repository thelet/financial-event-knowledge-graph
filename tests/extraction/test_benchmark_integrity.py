"""The benchmark must be checkable before anything is scored against it.

A gold file that cites a passage which no longer exists, or a metric the ontology does not
define, is worse than no benchmark: it fails a correct lane. Everything mechanically
checkable is checked here. What cannot be checked mechanically — that a period label was
read correctly, that a population wording is the one the filing used — is what `reviewed:
true` and the manual pass are for, and this module deliberately does not pretend otherwise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from ontology import load_ontology

BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "extraction" / "v1"
CASES_DIR = BENCHMARK_DIR / "cases"

# Metrics whose first source lane is xbrl, which the normalized corpus does not contain.
# A gold claim for one of these would contradict V1_CLAIM_EXTRACTION §3.1.
DEFERRED_METRICS = frozenset({
    "revenue", "gaap_gross_profit", "cost_of_revenue", "inventory_balance",
    "inventory_valuation_adjustment", "homes_under_resale_contract",
})


def _load_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for case in document["cases"]:
            case["_file"] = path.name
            cases.append(case)
    return cases


CASES = _load_cases()


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus; run `python -m normalization run` first")
    rows = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    return rows


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


def _gold_claims(case: dict) -> list[dict]:
    return case.get("gold_claims") or []


def case_ids() -> list[str]:
    return [c["case_id"] for c in CASES]


# -- the benchmark's own shape -------------------------------------------------------------


def test_case_ids_are_unique():
    ids = case_ids()
    assert len(ids) == len(set(ids))


def test_every_case_is_marked_reviewed():
    """`reviewed: false` means the values have not been checked against the passage, and
    scoring against them would measure the benchmark's errors rather than the lane's."""
    unreviewed = [c["case_id"] for c in CASES if not c.get("reviewed")]
    assert unreviewed == []


def test_scope_matches_what_the_readme_advertises():
    claims = sum(len(_gold_claims(c)) for c in CASES)
    documents = {c["document_id"] for c in CASES}
    assert 10 <= len(documents) <= 15, f"{len(documents)} documents"
    assert 25 <= len(CASES) <= 40, f"{len(CASES)} cases"
    assert 60 <= claims <= 120, f"{claims} gold claims"


def test_required_categories_are_all_present():
    required = {
        "deterministic_kpi_table", "sparse_reconciliation_table", "shareholder_letter_prose",
        "population_wording", "formula_drift", "negative_or_abstention",
        "event_or_relationship",
    }
    assert required <= {c["category"] for c in CASES}


def test_negative_cases_exist_in_meaningful_number():
    """A benchmark of positives only measures recall and rewards a lane that emits
    everything."""
    negatives = [c for c in CASES if not _gold_claims(c) and not c.get("gold_events")]
    assert len(negatives) >= 4


# -- every reference resolves --------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_passage_id_resolves_in_the_corpus(case, passages):
    assert case["passage_id"] in passages, f"{case['case_id']} cites a passage that does not exist"


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_passage_belongs_to_the_declared_document(case, passages):
    assert passages[case["passage_id"]]["document_id"] == case["document_id"]


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_referenced_scale_and_comparison_passages_resolve(case, passages):
    scale = case.get("scale_declaration") or {}
    if scale.get("passage_id"):
        assert scale["passage_id"] in passages
    if case.get("compare_against"):
        assert case["compare_against"] in passages


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_declared_scale_text_is_actually_in_the_passage_it_cites(case, passages):
    scale = case.get("scale_declaration") or {}
    if not scale.get("text"):
        return
    target = scale.get("passage_id", case["passage_id"])
    assert scale["text"] in passages[target]["text"], (
        f"{case['case_id']}: scale declaration not found in {target}"
    )


# -- every gold claim is consistent with the ontology and the passage ------------------------


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_gold_metric_ids_exist_in_the_ontology(case, ontology):
    for claim in _gold_claims(case):
        assert ontology.registry.find(claim["metric_id"]) is not None, (
            f"{case['case_id']}: unknown metric {claim['metric_id']}"
        )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_no_gold_claim_uses_a_deferred_xbrl_metric(case):
    """These belong in `abstentions`, not `gold_claims` — their lane does not exist."""
    for claim in _gold_claims(case):
        assert claim["metric_id"] not in DEFERRED_METRICS, (
            f"{case['case_id']}: {claim['metric_id']} requires the xbrl lane"
        )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_every_gold_claim_carries_exactly_one_period_shape(case):
    for claim in _gold_claims(case):
        duration = "period_start" in claim and "period_end" in claim
        instant = "instant_date" in claim
        assert duration != instant, (
            f"{case['case_id']}/{claim['metric_id']}: needs a duration or an instant, not both"
        )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_gold_values_actually_occur_in_the_cited_passage(case, passages):
    """The check that catches a transcription slip. The printed form is the value before
    any scale is applied, so a claim of 54,000,000 under a millions declaration must find
    "54" in the text."""
    text = passages[case["passage_id"]]["text"]
    for claim in _gold_claims(case):
        printed = _printed_form(claim)
        assert printed in text, (
            f"{case['case_id']}/{claim['metric_id']}: {printed!r} "
            f"(from value {claim['value']}) does not occur in {case['passage_id']}"
        )


def _printed_form(claim: dict) -> str:
    value = abs(claim["value"])
    scale = {"thousands": 1000, "millions": 1_000_000, "billions": 1_000_000_000}
    if claim.get("scale_applied"):
        value = value / scale[claim["scale_applied"]]
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,}"


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_population_wording_is_quoted_from_the_passage(case, passages):
    """The 120-day denominator is only useful if it is what the filing actually said."""
    text = passages[case["passage_id"]]["text"]
    for claim in _gold_claims(case):
        population = claim.get("population") or {}
        raw = population.get("definition_raw")
        if raw:
            assert raw in text, (
                f"{case['case_id']}: population wording not found verbatim in the passage"
            )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_the_120_day_metric_always_carries_its_population(case):
    """Ontology §16.1: without the denominator the value is not comparable to any other."""
    for claim in _gold_claims(case):
        if claim["metric_id"] == "pct_homes_on_market_gt_120_days":
            assert (claim.get("population") or {}).get("definition_raw"), (
                f"{case['case_id']}: the 120-day metric needs population.definition_raw"
            )


# -- events and relationships ---------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_gold_event_types_and_roles_exist_in_the_ontology(case, ontology):
    for event in case.get("gold_events") or []:
        definition = ontology.registry.find(event["event_type_id"])
        assert definition is not None, f"{case['case_id']}: unknown event {event['event_type_id']}"
        declared = {p.role for p in (definition.participants or [])}
        used = {p["role"] for p in event.get("participants", [])}
        assert used <= declared, (
            f"{case['case_id']}/{event['event_type_id']}: roles {used - declared} not declared"
        )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_gold_relationship_ids_exist_in_the_ontology(case, ontology):
    for relationship in case.get("gold_relationships") or []:
        assert ontology.registry.find(relationship["relationship_id"]) is not None, (
            f"{case['case_id']}: unknown relationship {relationship['relationship_id']}"
        )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_every_abstention_states_a_reason(case):
    for abstention in case.get("abstentions") or []:
        assert abstention.get("reason"), f"{case['case_id']}: abstention without a reason"
        assert re.fullmatch(r"[A-Z][A-Z_]+", abstention["reason"]), (
            f"{case['case_id']}: reason {abstention['reason']!r} should be an upper snake-case code"
        )


@pytest.mark.parametrize("case", CASES, ids=case_ids())
def test_capture_instead_text_is_quoted_from_the_passage(case, passages):
    """An abstention that names what to capture instead must name it in the passage's words.

    Added 2026-08-02 with the `population-portfolio-mdna-fy2023-10k` correction, which is
    what it would have caught: that case's `capture_instead.text` read "... greater than 120
    days (as measured from initial listing date)", parenthesised, which is the FY2021 10-K
    wording the ontology carries as `source_evidence` for the metric — not the FY2023
    passage the case cites, which prints the clause without parentheses. The quote was from
    a different filing and nothing noticed, because `capture_instead` was the one annotation
    field no integrity check read. Gold claims' `population.definition_raw` was already held
    to this rule; abstentions were not.
    """
    text = passages[case["passage_id"]]["text"]
    for abstention in case.get("abstentions") or []:
        capture = abstention.get("capture_instead") or {}
        quoted = capture.get("text")
        if quoted:
            assert quoted in text, (
                f"{case['case_id']}: capture_instead.text is not verbatim in "
                f"{case['passage_id']}"
            )


def test_the_fy2023_mdna_paragraph_is_observational_not_merely_definitional():
    """The founder decision of 2026-08-02, pinned to the passage that settles it.

    The case used to expect a `DEFINITIONAL_NOT_OBSERVATIONAL` abstention on the grounds
    that "the value for the period is reported in the KPI table at open-20231231.htm#p105,
    not here". That is false about this passage, and this test states why in the only terms
    that matter: the sentence supplies all four things an observation needs — metric,
    subject, instant and value — in the passage the case cites. A figure also appearing in a
    KPI table does not withdraw the prose evidence; two passages may independently support
    one reported observation.

    Parametrised over nothing on purpose. This is not a shape rule that should hold for
    every case; it is one adjudicated reading, and it is written down so that reverting the
    annotation without revisiting the passage fails.
    """
    case = next(c for c in CASES if c["case_id"] == "population-portfolio-mdna-fy2023-10k")
    rows = {}
    catalog = Path(__file__).resolve().parents[2] / "data" / "normalization_catalog"
    if not (catalog / "passages.jsonl").is_file():
        pytest.skip("no normalized corpus")
    for line in (catalog / "passages.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    text = rows[case["passage_id"]]["text"]

    # The sentence itself, verbatim. Metric ("such homes", anaphoric to the 120-day metric
    # named in the sentence before), instant ("As of December 31, 2023"), value ("18%") and
    # denominator ("our portfolio") are all printed here.
    assert ("As of December 31, 2023, such homes represented 18% of our portfolio" in text)
    assert "One such metric is our percentage of homes" in text, (
        "the antecedent of \"such homes\" must be in the same passage or the sentence is "
        "not self-contained evidence")

    reasons = {a["reason"] for a in (case.get("abstentions") or [])}
    assert "DEFINITIONAL_NOT_OBSERVATIONAL" not in reasons, (
        "the passage reports a value; expecting silence here scores a correct lane as wrong")

    claims = _gold_claims(case)
    assert len(claims) == 1, f"expected exactly one gold claim, found {len(claims)}"
    claim = claims[0]
    assert claim["metric_id"] == "pct_homes_on_market_gt_120_days"
    assert claim["value"] == 18
    assert claim["unit"] == "percent"
    assert claim["instant_date"] == "2023-12-31"
    assert claim["subject_entity_id"] == "opendoor"

    # §7.1: the wording, not a normalisation of it. "our portfolio" is one of the three
    # unreconciled denominators, and the shareholder letter reports the same 18% for the
    # same date as "our homes" — so a definition_raw that lost the wording would merge two
    # series the filings never reconciled.
    raw = claim["population"]["definition_raw"]
    assert "our portfolio" in raw, raw
    assert raw in text


def test_the_broader_market_figure_is_neither_gold_nor_a_scored_abstention(ontology):
    """21% belongs to the MLS, and V1 has nowhere to put it.

    The founder's condition was that the comparison figure becomes a second gold claim only
    if the ontology, the subject model and the benchmark schema *already* support the
    broader market as a distinct subject. They do not, and this records the three checks
    that establish it rather than leaving the omission to look like an oversight.
    """
    metric = ontology.registry.metric("pct_homes_on_market_gt_120_days")
    assert metric.subject_types == ("company",), metric.subject_types
    assert metric.population.comparison_population, (
        "the market side is modelled as comparison context on the population, which is "
        "where it stays until an entity model exists for it")

    subjects = {c["subject_entity_id"] for case in CASES for c in _gold_claims(case)}
    assert subjects == {"opendoor"}, (
        f"the benchmark has never scored a non-Opendoor subject; found {sorted(subjects)}")

    case = next(c for c in CASES if c["case_id"] == "population-portfolio-mdna-fy2023-10k")
    values = {c["value"] for c in _gold_claims(case)}
    assert 21 not in values, "21% is the broader market's, not Opendoor's"
    assert (case.get("abstentions") or []) == [], (
        "left unscored for V1, so it is not an expected abstention either")
