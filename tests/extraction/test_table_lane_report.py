"""The committed table-lane report is a claim, and these tests are what make it one.

A report generated once and never checked is a screenshot. Four of these tests exist so it
cannot quietly stop being true: regeneration is byte-identical, the committed JSON *and* the
committed Markdown match a fresh run byte for byte, and the totals equal what `evaluation.py`
produces when driven independently. The byte comparisons are deliberate — comparing parsed
JSON left the serialisation unenforced, and substring-checking the Markdown left 1008 lines
of prose and numbers unverified.

The report is derived, never authoritative. If a number here disagrees with the lane, the
report is wrong — STAGE_06 §7 makes changing extraction to move a number a gate failure.
"""

from __future__ import annotations

import ast
import collections
import json
import re
from pathlib import Path

import pytest
import yaml

from benchmarks.extraction.v1 import runner
from extraction.core.assembly import deferred_metric_ids
from extraction.core.models import PeriodRef
from extraction.stages.select import AliasIndex
from extraction.stages.tables import DeterministicTableClaimLane
from extraction.stages.tables.evaluation import GoldClaim, evaluate_case, summarise
from extraction.stages.tables.public import ISSUE_CODES
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
EXTRACTION_PACKAGE = REPO / "extraction"
COMMITTED_JSON = runner.REPORTS_DIR / f"{runner.REPORT_STEM}.json"
COMMITTED_MARKDOWN = runner.REPORTS_DIR / f"{runner.REPORT_STEM}.md"

# Fixed so a report generated in a test never depends on the working tree's HEAD; the real
# commit is the one field allowed to vary, and §3 says the determinism check holds it still.
PINNED_COMMIT = "0" * 40

# `implementation_commit` is the one field the committed reports are allowed to differ on, so
# the byte comparisons below blank exactly that line in both texts and nothing else. Anchored
# on the key, not on the sha, so a mutated commit line cannot pass as a normalised one.
COMMIT_IN_JSON = re.compile(r'^(\s*"implementation_commit": ")[^"]*(",?)$', re.MULTILINE)
COMMIT_IN_MARKDOWN = re.compile(r"^\| implementation commit \| `[^`]*` \|$", re.MULTILINE)


def _blank_commit_in_json(text: str) -> str:
    return COMMIT_IN_JSON.sub(r"\1<commit>\2", text)


def _blank_commit_in_markdown(text: str) -> str:
    return COMMIT_IN_MARKDOWN.sub("| implementation commit | `<commit>` |", text)


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    return runner.load_passages(repo_config.catalog_root)


@pytest.fixture(scope="module")
def report(passages, ontology, repo_config):
    return runner.run_table_lane(
        runner.load_table_cases(), passages=passages, ontology=ontology,
        catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)


def test_regenerating_twice_is_byte_identical(tmp_path, passages, ontology, repo_config):
    """Two full generations, not one report rendered twice.

    Rendering the same object twice would only prove `json.dumps` is a function. The point
    is that the lane, the scoring and the ordering are all deterministic end to end.
    """
    def generate(directory: Path) -> tuple[bytes, bytes]:
        built = runner.run_table_lane(
            runner.load_table_cases(), passages=passages, ontology=ontology,
            catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)
        json_path, markdown_path = runner.write_reports(built, directory)
        return json_path.read_bytes(), markdown_path.read_bytes()

    first = generate(tmp_path / "first")
    second = generate(tmp_path / "second")
    assert first[0] == second[0]
    assert first[1] == second[1]


def test_the_committed_json_is_byte_for_byte_what_a_fresh_run_renders(report):
    """Bytes, not `json.loads`.

    Parsing both sides would compare the data and ignore the serialisation, so the mandated
    `indent=2, sort_keys=True, ensure_ascii=False` and the trailing newline would be
    unenforced — the committed file could be rewritten as one compact line, or with its keys
    in emission order, and nothing would notice. Only the `implementation_commit` line is
    blanked; every other byte must match.
    """
    committed = _blank_commit_in_json(COMMITTED_JSON.read_text(encoding="utf-8"))
    fresh = _blank_commit_in_json(runner.render_json(report))
    assert committed.encode("utf-8") == fresh.encode("utf-8")


def test_the_committed_markdown_is_byte_for_byte_what_a_fresh_run_renders(report):
    """The Markdown is the artifact people actually read, and until now nothing verified it.

    Substring checks for case ids and issue codes pass on 19 lines of fabricated prose that
    happens to name them. This compares the whole file, so any edited number, verdict, table
    row or paragraph fails until the report is regenerated.
    """
    committed = _blank_commit_in_markdown(COMMITTED_MARKDOWN.read_text(encoding="utf-8"))
    fresh = _blank_commit_in_markdown(runner.render_markdown(report))
    assert committed.encode("utf-8") == fresh.encode("utf-8")


def test_totals_equal_the_evaluation_run_independently(passages, ontology):
    """Scored a second time, from the case YAML, without touching the runner's report model.

    Duplicated on purpose. If the runner ever started massaging a number on the way into the
    report, a test that asked the runner for its own answer could not tell.
    """
    lane = DeterministicTableClaimLane(
        ontology, AliasIndex.from_ontology(ontology), deferred_metric_ids(ontology))
    by_document = collections.defaultdict(dict)
    for row in passages.values():
        by_document[row["document_id"]][row["passage_sequence"]] = row

    results = []
    for path in sorted(runner.CASES_DIR.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]:
            if case["lane"] != "tables" or case["category"] not in runner.TABLE_CATEGORIES:
                continue
            row = passages[case["passage_id"]]
            preceding = by_document[row["document_id"]].get(row["passage_sequence"] - 1)
            extraction = lane.extract_table(
                row["text"], passage_id=case["passage_id"], table_id=row["table_id"],
                block_ids=tuple(row["block_ids"]),
                preceding_text=preceding["text"] if preceding else None,
                preceding_passage_id=preceding["passage_id"] if preceding else None)
            gold = [
                GoldClaim(g["metric_id"], g["value"], g["unit"],
                          g["instant_date"] if g.get("instant_date") else PeriodRef(
                              period_start=g.get("period_start"),
                              period_end=g.get("period_end")).key,
                          g["subject_entity_id"], g.get("scale_applied"), g.get("currency"))
                for g in (case.get("gold_claims") or [])]
            results.append(evaluate_case(
                case["case_id"], case["passage_id"], extraction.claims, gold,
                extraction.issues, resolves_evidence=lambda c: c.passage_id in passages))

    evaluation = summarise(results)
    totals = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))["totals"]
    assert totals["cases"] == len(evaluation.cases)
    assert totals["gold_observations"] == evaluation.gold_total
    assert totals["emitted_observations"] == evaluation.emitted_total
    assert totals["matched_observations"] == evaluation.matched_total
    assert totals["issues_by_code"] == dict(sorted(evaluation.issues_by_code.items()))
    assert totals["scores"]["metric_recall"] == round(
        evaluation.metric_recall, runner.RATIO_DIGITS)
    assert totals["scores"]["matched_over_emitted"] == round(
        evaluation.matched_over_emitted, runner.RATIO_DIGITS)
    for dimension in runner.MATCH_DIMENSIONS:
        assert totals["scores"][f"{dimension}_accuracy"] == round(
            evaluation.accuracy(dimension), runner.RATIO_DIGITS)


def test_every_passage_id_in_the_report_resolves(passages):
    """A report is evidence only if a reader can get from a number back to the filing."""
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    for case in committed["cases"]:
        assert case["passage_id"] in passages, case["case_id"]
        row = passages[case["passage_id"]]
        assert case["table_id"] == row["table_id"]
        assert case["document_id"] == row["document_id"]
        for emitted in case["emitted"]:
            assert emitted["evidence"]["passage_id"] in passages
            assert emitted["evidence"]["resolves"] is True


def test_every_issue_code_is_declared():
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    codes = {i["code"] for case in committed["cases"] for i in case["issues"]}
    assert codes
    assert codes <= ISSUE_CODES
    assert set(committed["totals"]["issues_by_code"]) <= ISSUE_CODES


def test_the_three_known_misses_are_the_only_misses(report):
    """The §8a.8 ontology gap, and nothing else.

    Both directions matter. A new miss is a regression; a known miss that stopped being one
    means the report's declared data is stale, which is just as wrong.
    """
    actual = {(case.case_id, key.metric_id, key.period_key)
              for case in report.cases for key in case.missed}
    declared = {(m["case_id"], m["metric_id"], m["period_key"])
                for m in report.known_misses}
    assert actual == declared
    assert declared == {
        ("recon-q1-2021-holding-costs-split-rows", "homes_sold", "2021Q1"),
        ("recon-q1-2021-holding-costs-split-rows", "homes_sold", "2020Q4"),
        ("recon-q1-2021-holding-costs-split-rows", "homes_sold", "2020Q1"),
    }

    # The rest of each declared miss — row index, row label, issue code and candidates — is
    # hand-transcribed from the plan, so it is checked against the issue the lane actually
    # emits for that row. Without this the keys were verified and the story around them was
    # not: a wrong row index or a stale candidate list would have survived.
    for miss in report.known_misses:
        case = report.case(miss["case_id"])
        assert case is not None, miss["case_id"]
        at_row = [i for i in case.issues if i.row_index == miss["row_index"]]
        assert len(at_row) == 1, (miss["row_index"], [i.code for i in at_row])
        issue = at_row[0]
        assert issue.code == miss["issue_code"]
        assert issue.raw_label == miss["row_label"]
        assert list(issue.candidate_concept_ids) == miss["candidate_concept_ids"]
        # The point of the §8a.8 gap: the metric is one of the candidates the lane could not
        # choose between, not a metric it failed to see at all.
        assert miss["metric_id"] in issue.candidate_concept_ids


def test_per_observation_verdicts_agree_with_the_aggregate_scores(report):
    """The two halves of the report must be the same measurement.

    Per-observation verdicts and per-dimension accuracies used to come from two independent
    comparisons, so every observation could read `**WRONG**: value` while `value_accuracy`
    read 1.000 and every test still passed. Both now come from `evaluation.compare`; this is
    what makes that structural fact observable.
    """
    matches = [m for case in report.cases for m in case.matched]
    matched_total = report.totals["matched_observations"]
    assert len(matches) == matched_total

    for dimension in runner.MATCH_DIMENSIONS:
        accuracy = report.totals["scores"][f"{dimension}_accuracy"]
        assert sum(1 for m in matches if getattr(m, f"{dimension}_ok")) == round(
            accuracy * matched_total), dimension

    for case in report.cases:
        for dimension in runner.MATCH_DIMENSIONS:
            accuracy = case.scores[f"{dimension}_accuracy"]
            assert sum(1 for m in case.matched if getattr(m, f"{dimension}_ok")) == round(
                accuracy * case.counts["matched"]), (case.case_id, dimension)


def test_the_report_states_which_of_the_eight_dimensions_it_does_not_score(report):
    """§4.0 names eight and this report scores seven, which nothing said until 2026-08-02.

    The absence is checked as an absence — no score here is named for ambiguity — so that
    adding one would fail this test and force the caveat to be removed with it, rather than
    leaving a report claiming a gap it no longer has.
    """
    import json

    # Rendered from the run, not read off disk: a caveat checked only against the committed
    # file is a caveat the generator can drop without a test noticing until regeneration.
    payload = json.loads(runner.render_json(report))
    caveats = {entry["dimension"]: entry for entry in payload["score_caveats"]}
    assert "ambiguity" in caveats
    assert caveats["ambiguity"]["state"] == "not scored"

    scored = set(payload["totals"]["scores"])
    for case in payload["cases"]:
        scored |= set(case["scores"])
    assert not any("ambig" in name for name in scored), scored
    assert {"metric_recall", "matched_over_emitted"} <= scored
    assert len(scored) == 8, sorted(scored)

    assert "Seven numbers, not eight" in runner.render_markdown(report)


def test_the_report_marks_period_accuracy_as_the_tautology_it_is(report):
    """`evaluate_case` keys `by_key` on `(metric_id, period.key)` and `compare` then tests
    `claim.period.key == gold.period_key`. Demonstrated, not asserted: every matched pair
    agrees by construction, so the number cannot fall."""
    import json

    payload = json.loads(runner.render_json(report))
    caveats = {entry["dimension"]: entry for entry in payload["score_caveats"]}
    assert caveats["period_accuracy"]["state"] == "tautology, not a measurement"
    assert payload["totals"]["scores"]["period_accuracy"] == 1.0

    matches = [m for case in report.cases for m in case.matched]
    assert matches
    assert all(m.period_ok for m in matches), (
        "a matched pair that disagreed about its period would mean the matching key changed")
    assert all(m.metric_id == m.metric_id for m in matches)

    assert "is a tautology and is not a measurement" in runner.render_markdown(report)


def test_the_markdown_names_every_case_and_every_issue_code(report):
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    for case in report.cases:
        assert case.case_id in markdown
    for code in report.totals["issues_by_code"]:
        assert code in markdown


def test_benchmarks_is_importable_and_extraction_imports_nothing_from_it():
    """The layout constraint that decided where this runner lives, as an executable rule.

    Not an import check on `benchmarks` alone — that would pass with the runner sitting
    inside `extraction/`. The second half is what pins the direction.
    """
    import benchmarks
    import benchmarks.extraction.v1

    assert Path(benchmarks.__file__).parent.name == "benchmarks"
    assert callable(runner.load_table_cases)

    offenders = []
    for path in EXTRACTION_PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("benchmark" in name for name in names):
                offenders.append(f"{path.relative_to(REPO)}: {names}")
        if "benchmarks/extraction" in source:
            offenders.append(f"{path.relative_to(REPO)}: benchmark path literal")
    assert offenders == []
