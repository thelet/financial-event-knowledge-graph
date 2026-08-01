"""Typed candidate selection, scored against the benchmark and the real corpus."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from extraction.core.models import INCLUSION_REASONS
from extraction.stages.select import (
    EXCLUSION_REASONS,
    REASON_CODES,
    AliasIndex,
    SelectionPolicy,
    TypedCandidateSelector,
)
from extraction.stages.select.evaluation import GoldCase, evaluate
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
CASES_DIR = REPO / "benchmarks" / "extraction" / "v1" / "cases"
PACKAGE = REPO / "extraction"


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def selector(ontology):
    policy = SelectionPolicy.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))
    return TypedCandidateSelector(policy, AliasIndex.from_ontology(ontology), ontology)


@pytest.fixture(scope="module")
def rows(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    return [json.loads(l) for l in catalog.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture(scope="module")
def result(selector, rows):
    return selector.select_from_rows(rows)


@pytest.fixture(scope="module")
def gold() -> list[GoldCase]:
    cases: list[GoldCase] = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]:
            metrics = {g["metric_id"] for g in (case.get("gold_claims") or [])}
            ambiguity = any(
                a.get("reason") == "AMBIGUOUS_ALIAS" for a in (case.get("abstentions") or []))
            cases.append(GoldCase(
                case_id=case["case_id"], passage_id=case["passage_id"],
                document_id=case["document_id"], document_type="",
                lane=case["lane"], metric_ids=frozenset(metrics),
                expects_ambiguity_abstention=ambiguity))
    return cases


# -- the corrected corpus baseline -----------------------------------------------------------


def test_selection_runs_over_the_corrected_corpus(rows, result):
    """294 documents, 12,442 passages, 1,935 table passages, PARSER_FALLBACK 0."""
    assert len(rows) == 12_442
    assert sum(1 for r in rows if r["passage_kind"] == "table") == 1_935
    assert len({r["document_id"] for r in rows}) == 294
    # One decision per (passage, lane), both lanes.
    assert len(result.candidates) == 12_442 * 2


def test_every_passage_gets_exactly_one_reason_per_lane(result):
    """A passage's absence must be as auditable as its presence."""
    seen: dict[tuple[str, str], int] = {}
    for candidate in result.candidates:
        key = (candidate.passage_id, candidate.lane)
        seen[key] = seen.get(key, 0) + 1
    assert set(seen.values()) == {1}


def test_every_reason_is_a_declared_code(result):
    assert {c.reason for c in result.candidates} <= REASON_CODES


# -- typing by all four axes ------------------------------------------------------------------


def test_a_narrative_passage_never_reaches_the_table_lane(result):
    offenders = [c for c in result.by_lane("tables") if c.passage_kind != "table"]
    assert offenders == []


def test_shareholder_letters_reach_the_narrative_lane_and_not_the_table_lane(result):
    """26 letters produce 1 table passage between them, so the letter lane is prose."""
    letters = [c for c in result.candidates if c.document_type == "shareholder_letter"]
    assert [c for c in letters if c.selected and c.lane == "narrative"]
    assert [c for c in letters if c.selected and c.lane == "tables"] == []


def test_contract_and_governance_tables_are_excluded_with_their_own_codes(result):
    """Semantic routing: recitals and governance schedules carry no operating metric. The
    saving is small after the encoding correction - 78 and 44 table passages - so the code
    must say *why*, not merely that they were dropped."""
    reasons = {c.document_type: c.reason for c in result.candidates
               if c.lane == "tables" and c.document_type in
               {"material_agreement", "governance"} and c.passage_kind == "table"}
    assert reasons["material_agreement"] == "contract_boilerplate"
    assert reasons["governance"] == "governance_boilerplate"


def test_material_agreement_table_volume_matches_the_corrected_corpus(rows):
    """78, not the 1,130 the pre-fix corpus showed. Guards against the obsolete figure
    creeping back into a justification."""
    count = sum(1 for r in rows
                if r["passage_kind"] == "table" and r["document_type"] == "material_agreement")
    assert count == 78


# -- the scale-context requirement -------------------------------------------------------------


def test_every_selected_table_candidate_carries_its_preceding_passage(result):
    """9 of 74 KPI-bearing tables declare their magnitude scale in the passage before. A
    candidate without its predecessor makes that form unreadable, and every dollar figure in
    it comes out a million times too small."""
    selected = result.by_lane("tables")
    assert selected, "no table candidates at all"
    without = [c for c in selected if not c.preceding_passage_id]
    assert without == [], f"{len(without)} table candidates lack scale context"


def test_the_q1_2025_table_carries_the_passage_holding_its_scale(result):
    """The concrete case: #p14 is the table, #p13 holds
    "(In millions, except percentages, homes sold, ...)"."""
    table = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p14"
    candidate = next(c for c in result.by_lane("tables") if c.passage_id == table)
    assert candidate.preceding_passage_id.endswith("#p13")


# -- what retrieval may never remove -------------------------------------------------------------


def test_ambiguous_aliases_are_carried_not_filtered(selector, ontology):
    """A bare "gross profit" must reach a lane. Filtering it looks precise and destroys the
    abstention the benchmark scores."""
    row = _row(text="| Gross profit (GAAP) |  | $ | 97,132 |", passage_kind="table",
               document_type="earnings_release")
    candidate = _table_candidate(selector, row)
    assert candidate.selected
    assert {"gaap_gross_profit", "adjusted_gross_profit"} <= set(candidate.matched_metric_ids)


def test_confusion_group_siblings_are_added_to_the_candidate_set(selector):
    """A passage naming homes_sold must let the lane see homes_purchased too, or it cannot
    tell that the row in front of it is the other one."""
    row = _row(text="| Homes sold |  | 2,946 |", passage_kind="table",
               document_type="earnings_release")
    candidate = _table_candidate(selector, row)
    assert "homes_sold" in candidate.matched_metric_ids
    assert "homes_purchased" in candidate.matched_metric_ids


def test_a_table_row_label_outranks_a_body_mention(selector):
    """`table_label` is the most specific inclusion reason, so counting by reason answers
    "why did this get in" rather than "what did it touch"."""
    row = _row(text="| Homes sold |  | 2,946 |", passage_kind="table",
               document_type="earnings_release")
    assert _table_candidate(selector, row).reason == "table_label"


# -- benchmark evaluation ------------------------------------------------------------------------


def test_selection_reaches_every_gold_concept(result, gold):
    evaluation = evaluate(result.candidates, gold)
    assert evaluation.required_concept_recall == 1.0, (
        f"missed {evaluation.missed_concepts}\n{evaluation.as_report()}")


def test_selection_reaches_every_gold_instance(result, gold):
    evaluation = evaluate(result.candidates, gold)
    assert evaluation.known_instance_recall == 1.0, (
        f"missed {evaluation.missed_instances[:6]}\n{evaluation.as_report()}")


def test_ambiguity_cases_survive_selection(result, gold):
    evaluation = evaluate(result.candidates, gold)
    assert evaluation.ambiguity_preservation == 1.0


def test_evaluation_reports_every_dimension_the_plan_asks_for(result, gold):
    evaluation = evaluate(result.candidates, gold)
    assert evaluation.candidates_by_lane.keys() == {"tables", "narrative"}
    assert evaluation.candidates_by_document_type
    assert evaluation.exclusions_by_reason
    assert set(evaluation.exclusions_by_reason) <= EXCLUSION_REASONS
    # Recall-oriented by design, so this is reported rather than bounded tightly. It is the
    # number that says what a recall gain cost.
    assert 0.0 <= evaluation.false_positive_density < 1.0


def test_missed_concepts_are_named_not_just_counted():
    """A number says a policy is 94% good; a list says which metric it cannot see."""
    gold = [GoldCase("c", "norm:x#p1", "norm:x", "", "tables", frozenset({"homes_sold"}))]
    evaluation = evaluate([], gold)
    assert evaluation.missed_concepts == ("homes_sold",)


# -- selection must not consult the answers --------------------------------------------------------


def test_runtime_selection_never_imports_the_benchmark():
    """A policy that read gold annotations would be measuring itself against its own
    output. `evaluation.py` scores after the fact and imports no benchmark either - the
    gold set is passed in."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("benchmark" in n for n in names):
                offenders.append(f"{path.relative_to(REPO)}: {names}")
        if "benchmarks/extraction" in source:
            offenders.append(f"{path.relative_to(REPO)}: benchmark path literal")
    assert offenders == []


def test_selection_policy_lives_in_config_not_code():
    """Scope in config, never in code - the rule the other two packages already follow."""
    source = (PACKAGE / "stages" / "select" / "typed_selector.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for document_type in ("earnings_release", "shareholder_letter", "material_agreement"):
        assert document_type not in code, f"{document_type} hard-coded in the selector"


# -- helpers ------------------------------------------------------------------------------------


def _row(*, text: str, passage_kind: str, document_type: str) -> dict:
    return {
        "passage_id": "norm:0001801169:0001801169-25-000037:x.htm#p1",
        "document_id": "norm:0001801169:0001801169-25-000037:x.htm",
        "document_type": document_type, "passage_kind": passage_kind,
        "passage_sequence": 1, "text": text, "heading_path": [],
    }


def _table_candidate(selector, row):
    result = selector.select_from_rows([row])
    return next(c for c in result.candidates if c.lane == "tables")
