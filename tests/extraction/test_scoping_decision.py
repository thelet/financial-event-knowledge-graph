"""The lexical-versus-hybrid decision, and the properties that make it worth citing.

STAGE_13 §6. Three things are checked and they are different things: that the committed report
is what a fresh build renders, that every number in it is looked up from a committed evaluation
report rather than computed here, and that `config/extraction.yaml` records the outcome the
report reached rather than a value someone typed.

**The last one is the point.** A decision report that said `lexical` beside a configuration
that said `hybrid` would be worse than no report, because both would look authoritative.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from benchmarks.extraction.v1 import scoping_decision

REPO = Path(__file__).resolve().parents[2]
COMMITTED_JSON = scoping_decision.REPORTS_DIR / f"{scoping_decision.REPORT_STEM}.json"
COMMITTED_MARKDOWN = scoping_decision.REPORTS_DIR / f"{scoping_decision.REPORT_STEM}.md"
PINNED_COMMIT = "0" * 40

COMMIT_IN_JSON = re.compile(r'^(\s*"implementation_commit": ")[^"]*(",?)$', re.MULTILINE)
COMMIT_IN_MARKDOWN = re.compile(r"^\| implementation commit \| `[^`]*` \|$", re.MULTILINE)

# What may never appear in an artifact that has to be byte-identical on regeneration.
FORBIDDEN = ("latency", "elapsed", "duration_ms", "created_at", "generated_at",
             "prompt_tokens", "completion_tokens")


@pytest.fixture(scope="module")
def decision():
    return scoping_decision.build_decision(implementation_commit=PINNED_COMMIT)


@pytest.fixture(scope="module")
def committed() -> dict:
    if not COMMITTED_JSON.is_file():
        pytest.skip("the decision report is not committed")
    return json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))


def test_the_committed_json_is_what_a_fresh_build_renders(decision):
    if not COMMITTED_JSON.is_file():
        pytest.skip("the decision report is not committed")
    fresh = COMMIT_IN_JSON.sub(r"\1<commit>\2", scoping_decision.render_json(decision))
    on_disk = COMMIT_IN_JSON.sub(
        r"\1<commit>\2", COMMITTED_JSON.read_text(encoding="utf-8"))
    assert on_disk.encode("utf-8") == fresh.encode("utf-8")


def test_the_committed_markdown_is_what_a_fresh_build_renders(decision):
    if not COMMITTED_MARKDOWN.is_file():
        pytest.skip("the decision report is not committed")
    blank = "| implementation commit | `<commit>` |"
    fresh = COMMIT_IN_MARKDOWN.sub(blank, scoping_decision.render_markdown(decision))
    on_disk = COMMIT_IN_MARKDOWN.sub(
        blank, COMMITTED_MARKDOWN.read_text(encoding="utf-8"))
    assert on_disk.encode("utf-8") == fresh.encode("utf-8")


def test_building_it_twice_is_byte_identical(tmp_path):
    def generate(directory: Path):
        built = scoping_decision.build_decision(implementation_commit=PINNED_COMMIT)
        json_path, markdown_path = scoping_decision.write_reports(built, directory)
        return json_path.read_bytes(), markdown_path.read_bytes()

    assert generate(tmp_path / "first") == generate(tmp_path / "second")


def test_no_volatile_value_enters_the_artifact(decision):
    rendered = scoping_decision.render_json(decision) + scoping_decision.render_markdown(
        decision)
    for token in FORBIDDEN:
        assert token not in rendered, token


# -- the numbers come from the committed reports ------------------------------------------------


def test_every_rate_is_looked_up_from_a_committed_report(decision):
    """No score is computed here. The source reports are the authority on every rate."""
    reports = scoping_decision.load_reports()
    hybrid = reports[scoping_decision.HYBRID_SCOPE_REPORT]
    narrative = reports[scoping_decision.NARRATIVE_REPORT]
    events = reports[scoping_decision.EVENT_REPORT]
    assert (decision.reachability["lexical_required_concept_recall"]
            == hybrid["views"]["lexical"]["totals"]["scores"]["required_concept_recall"])
    assert (decision.reachability["hybrid_required_concept_recall"]
            == hybrid["views"]["hybrid"]["totals"]["scores"]["required_concept_recall"])
    assert (decision.metric_extraction["cases_with_identical_scope"]
            == narrative["comparison"]["cases_with_identical_scope"])
    assert (decision.event_extraction["cases_with_identical_requests"]
            == events["comparison"]["cases_with_identical_requests"])


def test_the_module_computes_no_score_of_its_own():
    """An AST check: no division anywhere, so no rate can have been derived here.

    Path joins are the one exception and they are recognised by shape — `Path / "literal"` —
    the same exemption `test_narrative_lane_report` and `test_event_lane_report` already make
    for their runners. Anything else dividing is a rate this file would be inventing.
    """
    path_names = {"PACKAGE_ROOT", "REPORTS_DIR", "REPO", "directory", "path", "root"}

    def is_path_join(node: ast.BinOp) -> bool:
        left = node.left
        while isinstance(left, ast.BinOp):
            left = left.left
        return isinstance(left, ast.Name) and left.id in path_names

    tree = ast.parse(
        (REPO / "benchmarks" / "extraction" / "v1" / "scoping_decision.py").read_text(
            encoding="utf-8"))
    offenders = [
        ast.dump(node) for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv))
        and not is_path_join(node)
    ]
    assert offenders == []


def test_the_width_is_the_count_of_cases_that_could_distinguish_the_scopes(decision):
    reports = scoping_decision.load_reports()
    narrative = reports[scoping_decision.NARRATIVE_REPORT]["comparison"]
    events = reports[scoping_decision.EVENT_REPORT]["comparison"]
    assert decision.width["reviewed_cases_compared"] == (
        narrative["cases"] + events["cases"])
    assert decision.width["reviewed_cases_that_could_distinguish_the_scopes"] == (
        narrative["cases_with_a_difference"]
        + events["cases"] - events["cases_with_identical_requests"])
    assert decision.width["reviewed_cases_that_could_distinguish_the_scopes"] == 1
    assert decision.width["required_concepts_that_distinguish_the_scopes"] == 1
    # 49, not 50: `recon-shareholder-letter-table-as-prose` became a must-refuse case at
    # F0, so its gold claims no longer contribute a required concept to compare.
    assert decision.width["required_concepts_compared"] == 49


def test_the_verdict_states_its_width_and_calls_itself_weak(decision):
    assert decision.verdict["strength"] == "weak"
    width = decision.width["reviewed_cases_that_could_distinguish_the_scopes"]
    assert f"{width} of {decision.width['reviewed_cases_compared']} reviewed cases" in (
        decision.verdict["why_weak"])
    markdown = scoping_decision.render_markdown(decision)
    assert "Strength: weak" in markdown
    assert decision.verdict["why_weak"] in markdown


def test_the_verdict_follows_from_the_criteria_and_not_from_a_literal(decision):
    holds = {entry["criterion"]: entry["holds"] for entry in decision.criteria}
    adopt = holds["adds_a_clean_gold_claim"] and holds["comparison_is_wider_than_one_case"]
    assert decision.verdict["adoption_conditions_met"] is adopt
    assert decision.verdict["default"] == ("hybrid" if adopt else "lexical")


def test_the_one_gold_claim_hybrid_adds_is_recorded_as_not_clean(decision):
    """The step 11 correction, carried forward. The first pass called it clean and it is not."""
    assert decision.metric_extraction["gold_claims_only_hybrid_reached"] == 1
    assert decision.metric_extraction["gold_claims_added_clean"] == 0
    assert decision.metric_extraction["gold_claims_added_not_clean"] == 1
    assert decision.metric_extraction["per_claim_regressions"] == 0


def test_the_event_lane_contributes_no_evidence_and_the_report_says_why(decision):
    assert decision.event_extraction["cases_with_identical_requests"] == (
        decision.event_extraction["cases"])
    assert decision.event_extraction["score_deltas_that_are_nonzero"] == {}
    assert "takes no candidate scope" in scoping_decision.render_markdown(decision)


# -- the configuration records the outcome -------------------------------------------------------


def test_the_configured_strategy_is_the_verdict_the_report_reached(decision):
    document = yaml.safe_load(
        (REPO / "config" / "extraction.yaml").read_text(encoding="utf-8"))
    assert document["scoping"]["strategy"] == decision.verdict["default"]


def test_the_decision_reads_only_committed_reports(monkeypatch):
    """No lane, no ontology, no corpus, no server. It is reproducible from the repository."""
    source = (REPO / "benchmarks" / "extraction" / "v1" / "scoping_decision.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "ontology" not in imported
    assert not any(name.startswith("extraction") for name in imported), imported
    assert not any(name in ("httpx", "requests") for name in imported)


def test_a_missing_source_report_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        scoping_decision.build_decision(
            directory=tmp_path, implementation_commit=PINNED_COMMIT)


def test_every_number_in_the_markdown_is_a_number_the_decision_holds(decision):
    known: set[str] = set()

    def collect(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            known.add(str(value))
        elif isinstance(value, dict):
            for inner in value.values():
                collect(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                collect(inner)

    for block in (decision.reachability, decision.metric_extraction,
                  decision.event_extraction, decision.width, decision.corpus_cost,
                  decision.criteria, decision.verdict):
        collect(block)
    unexplained = []
    for line in scoping_decision.render_markdown(decision).splitlines():
        if not line.startswith("| ") or set(line) <= set("| -"):
            continue
        for cell in (part.strip() for part in line.strip("|").split("|")):
            if re.fullmatch(r"-?\d+(\.\d+)?", cell) and cell not in known:
                unexplained.append((cell, line))
    assert unexplained == [], unexplained[:5]
