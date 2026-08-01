"""Before the table lane is built, prove its inputs are sufficient.

For every benchmark table case, the six things a claim needs must be reachable from what
selection hands the lane: the metric label, the period headers, the unit, the scale, the
subject, and a resolvable evidence reference. A lane cannot be blamed for a claim it had no
way to make, and finding a gap now is cheaper than discovering it as a benchmark failure
with an ambiguous cause.

The check is deliberately about *reachability*, not correctness. That the string
"(In millions…)" is reachable from the candidate says the lane can find the scale; whether
it applies it correctly is what step 6 measures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from extraction.core import numbers, periods
from extraction.stages.select import AliasIndex, SelectionPolicy, TypedCandidateSelector
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
CASES_DIR = REPO / "benchmarks" / "extraction" / "v1" / "cases"
TABLE_CATEGORIES = {"deterministic_kpi_table", "sparse_reconciliation_table", "formula_drift"}


def _table_cases() -> list[dict]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]:
            if case["category"] in TABLE_CATEGORIES and case["lane"] == "tables":
                cases.append(case)
    return cases


CASES = _table_cases()
IDS = [c["case_id"] for c in CASES]


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    rows = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    return rows


@pytest.fixture(scope="module")
def alias_index():
    return AliasIndex.from_ontology(load_ontology())


@pytest.fixture(scope="module")
def candidates(passages):
    ontology = load_ontology()
    policy = SelectionPolicy.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))
    selector = TypedCandidateSelector(policy, AliasIndex.from_ontology(ontology), ontology)
    result = selector.select_from_rows(list(passages.values()))
    return {c.passage_id: c for c in result.by_lane("tables")}


def test_the_benchmark_has_table_cases_to_check():
    assert len(CASES) >= 8, f"only {len(CASES)} table cases"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_case_passage_was_selected_for_the_table_lane(case, candidates):
    assert case["passage_id"] in candidates, (
        f"{case['case_id']}: selection did not offer this passage to the table lane")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_metric_label_is_resolvable_from_a_table_row_label(case, passages, alias_index):
    """Every gold claim's metric must be reachable from a row label through the ontology's
    own alias index - the mechanism the lane will use - not from the value, which several
    rows in these tables share.

    Checked through the alias index rather than by string similarity to the metric id. The
    row for `pct_homes_on_market_gt_120_days` reads "Percentage of homes "on the market" for
    greater than 120 days (at period end)" and the one for `housing_inventory_homes` reads
    "Homes in inventory (at period end)"; neither resembles its id, and a test that expected
    it to would be testing the naming convention rather than the resolution path.
    """
    text = passages[case["passage_id"]]["text"]
    row_labels = [m.strip() for m in re.findall(r"^\|\s*([^|]{2,120}?)\s*\|", text, re.M)]
    assert row_labels, f"{case['case_id']}: no row labels parsed out of the table"

    reachable: set[str] = set()
    for label in row_labels:
        for hit in alias_index.hits(label):
            reachable.update(hit.concept_ids)

    for claim in case.get("gold_claims") or []:
        assert claim["metric_id"] in reachable, (
            f"{case['case_id']}/{claim['metric_id']}: no row label resolves to it. "
            f"Row labels: {row_labels[:8]}")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_period_headers_are_present_and_resolvable(case, passages):
    """A column label alone is not enough when the header carries two groups of different
    length - the Q4 2020 reconciliation is exactly that shape."""
    text = passages[case["passage_id"]]["text"]
    for claim in case.get("gold_claims") or []:
        column = claim.get("column_label")
        if not column:
            continue
        anchor = column.split(",")[-1].strip() if "," in column else column
        assert anchor in text or column in text, (
            f"{case['case_id']}: column label {column!r} not present in the passage")
        resolved = periods.resolve(
            column, group_header=column, row_label=claim.get("note", ""))
        assert resolved is not None or periods.parse_date(column), (
            f"{case['case_id']}: {column!r} carries no resolvable date")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_unit_is_determinable_for_every_gold_claim(case):
    """Counts, percentages and dollars are distinguished by the row label and the sigil
    columns, both of which the passage carries."""
    for claim in case.get("gold_claims") or []:
        assert claim["unit"] in {"homes", "usd", "percent", "markets"}, claim["unit"]
        if claim["unit"] == "usd":
            assert claim.get("currency") == "USD", (
                f"{case['case_id']}/{claim['metric_id']}: a monetary claim needs a currency")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_scale_is_reachable_from_the_candidate(case, passages, candidates):
    """The requirement selection exists to satisfy. 65 of 74 KPI-bearing tables declare the
    scale inside the table; 9 declare it in the passage before, and for those the candidate
    must carry its predecessor or the scale is unreachable."""
    needs_scale = any(c.get("scale_applied") for c in (case.get("gold_claims") or []))
    if not needs_scale:
        return

    candidate = candidates[case["passage_id"]]
    in_table = numbers.parse_scale_declaration(passages[case["passage_id"]]["text"])
    from_preceding = None
    if candidate.preceding_passage_id:
        preceding = passages.get(candidate.preceding_passage_id)
        if preceding:
            from_preceding = numbers.parse_scale_declaration(preceding["text"])

    assert in_table or from_preceding, (
        f"{case['case_id']}: no scale declaration reachable from the candidate")

    declared = case.get("scale_declaration") or {}
    if declared.get("location") == "preceding_passage":
        assert from_preceding, (
            f"{case['case_id']}: declared as preceding_passage but not reachable there")
        assert from_preceding[0] == declared_scale(case), (
            f"{case['case_id']}: preceding passage declares {from_preceding[0]}")
    elif declared.get("location") == "in_table":
        assert in_table, f"{case['case_id']}: declared as in_table but not found there"


def declared_scale(case: dict) -> str:
    for claim in case.get("gold_claims") or []:
        if claim.get("scale_applied"):
            return claim["scale_applied"]
    return "units"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_subject_is_determinable(case):
    """A single-anchor corpus, so the subject is Opendoor unless the passage says otherwise -
    but it must be stated on the claim rather than assumed downstream."""
    for claim in case.get("gold_claims") or []:
        assert claim["subject_entity_id"] == "opendoor"
        assert claim["subject_type"] in {"public_company", "company"}


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_evidence_reference_resolves_and_contains_the_value(case, passages):
    """The anchor must point at a passage that exists and actually contains what is claimed.
    The ontology cannot check this - its evidence types declare field names, not grammar."""
    assert case["passage_id"] in passages
    text = passages[case["passage_id"]]["text"]
    for claim in case.get("gold_claims") or []:
        printed = _printed(claim)
        assert printed in text, (
            f"{case['case_id']}/{claim['metric_id']}: {printed!r} not in the cited passage")


def _printed(claim: dict) -> str:
    value = abs(claim["value"])
    factor = {"thousands": 1000, "millions": 1_000_000, "billions": 1_000_000_000}
    if claim.get("scale_applied"):
        value = value / factor[claim["scale_applied"]]
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return f"{value:,}"



