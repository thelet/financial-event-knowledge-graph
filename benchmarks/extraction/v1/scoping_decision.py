"""The lexical-versus-hybrid decision, made from committed reports and from nothing else.

STAGE_13 §6, and the reason this module lives here rather than under `extraction/`: the
comparison is made **from committed evaluation reports by reporting code**, never by the
pipeline. Four AST guards assert that nothing under `extraction/` imports or names this
directory, so a run cannot reach a gold annotation, a case file or a score — and the decision
still has to be made from measurements rather than from taste. This module is where those two
requirements meet.

**It reads four committed JSON reports and computes no score of its own.** Every rate below is
looked up from `lexical_scope_v1.json`, `hybrid_scope_v1.json`, `narrative_lane_v1.json` and
`event_relationship_v1.json`; the only arithmetic here is counting how many cases and concepts
distinguish the two scopes, which the source reports do not state and which is the *width* this
decision has to report beside its result. It runs no lane, issues no request, loads no ontology
and needs no server, which is what makes the decision reproducible by anyone holding the
repository.

**The width is reported beside the result, and that is the point of the exercise.** The two
scopes issue identical request digests on 13 of 14 narrative cases and on all 3 event cases,
so a comparison of extraction quality rests on **one case**. A decision resting on one case is
a weak decision whichever way it goes, and this report labels it one rather than presenting a
single case as a verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import runner

PACKAGE_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = PACKAGE_ROOT / "reports"
REPORT_STEM = "scoping_decision_v1"
BENCHMARK_VERSION = "v1"

LEXICAL_SCOPE_REPORT = "lexical_scope_v1.json"
HYBRID_SCOPE_REPORT = "hybrid_scope_v1.json"
NARRATIVE_REPORT = "narrative_lane_v1.json"
EVENT_REPORT = "event_relationship_v1.json"
SOURCES: tuple[str, ...] = (
    LEXICAL_SCOPE_REPORT, HYBRID_SCOPE_REPORT, NARRATIVE_REPORT, EVENT_REPORT)

LEXICAL = "lexical"
HYBRID = "hybrid"

# The four conditions the decision turns on, in the order they are argued. Each is answered
# `True`/`False` from a number in a committed report; the verdict is a function of the four and
# of nothing a reader has to take on trust.
#
# Criteria 1 and 3 are the *adoption* conditions: hybrid becomes the default only if it adds a
# gold claim that is clean on every scored dimension **and** the comparison rests on more than
# one case. 2 and 4 are recorded because they are the two things a reader would otherwise
# assume — that hybrid broke nothing, and that hybrid's corpus-scale cost is known.
CRITERIA: tuple[tuple[str, str], ...] = (
    ("adds_a_clean_gold_claim",
     "hybrid reaches at least one gold claim that lexical misses and is clean on every "
     "scored dimension"),
    ("no_per_claim_regression",
     "no claim is clean under lexical and not clean under hybrid"),
    ("comparison_is_wider_than_one_case",
     "more than one reviewed case can distinguish the two scopes at all"),
    ("corpus_cost_is_reproducible_offline",
     "hybrid's corpus-scale cost can be measured from committed artifacts, with no server"),
)


@dataclass
class ScopingDecision:
    benchmark_version: str
    implementation_commit: str | None
    ontology_definition_hash: str
    sources: dict[str, str] = field(default_factory=dict)
    reachability: dict[str, Any] = field(default_factory=dict)
    metric_extraction: dict[str, Any] = field(default_factory=dict)
    event_extraction: dict[str, Any] = field(default_factory=dict)
    width: dict[str, Any] = field(default_factory=dict)
    criteria: list[dict[str, Any]] = field(default_factory=list)
    verdict: dict[str, Any] = field(default_factory=dict)
    corpus_cost: dict[str, Any] = field(default_factory=dict)
    open_questions: list[str] = field(default_factory=list)


def _missed_pairs(view: dict) -> set[tuple[str, str]]:
    """`(case, concept)` for every required concept a view's scope did not reach."""
    return {
        (str(case["case_id"]), str(concept))
        for case in view["cases"]
        for key in ("missed_metric_ids", "missed_instance_ids")
        for concept in (case.get(key) or ())
    }


def load_reports(directory: Path = REPORTS_DIR) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for name in SOURCES:
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} is missing; the decision is made from committed reports and cannot "
                "be made without them")
        reports[name] = json.loads(path.read_text(encoding="utf-8"))
    return reports


def build_decision(
    *, directory: Path = REPORTS_DIR, implementation_commit: str | None = None
) -> ScopingDecision:
    reports = load_reports(directory)
    commit = implementation_commit or runner._head_commit()
    lexical_scope = reports[LEXICAL_SCOPE_REPORT]
    hybrid_scope = reports[HYBRID_SCOPE_REPORT]
    narrative = reports[NARRATIVE_REPORT]
    events = reports[EVENT_REPORT]

    decision = ScopingDecision(
        benchmark_version=BENCHMARK_VERSION,
        implementation_commit=commit,
        ontology_definition_hash=str(narrative["ontology_definition_hash"]),
        sources={name: str(reports[name]["implementation_commit"]) for name in SOURCES},
    )

    hybrid_views = hybrid_scope["views"]
    decision.reachability = {
        "denominator": hybrid_views[LEXICAL]["totals"]["denominators"]["required_concepts"],
        "lexical_required_concept_recall":
            hybrid_views[LEXICAL]["totals"]["scores"]["required_concept_recall"],
        "hybrid_required_concept_recall":
            hybrid_views[HYBRID]["totals"]["scores"]["required_concept_recall"],
        "lexical_only_report_recall":
            lexical_scope["totals"]["scores"]["required_concept_recall"],
        "lexical_scope_size_mean": hybrid_views[LEXICAL]["totals"]["scope_size"]["mean"],
        "hybrid_scope_size_mean": hybrid_views[HYBRID]["totals"]["scope_size"]["mean"],
        "hybrid_stage_09_criterion_1_holds": hybrid_scope["decision"]["criteria"][0]["holds"],
    }

    comparison = narrative["comparison"]
    recommendation = comparison["recommendation"]
    decision.metric_extraction = {
        "cases": comparison["cases"],
        "cases_with_identical_scope": comparison["cases_with_identical_scope"],
        "cases_with_a_difference": comparison["cases_with_a_difference"],
        "cases_without_a_request": comparison["cases_without_a_request"],
        "gold_claims_only_hybrid_reached": comparison["gold_claims_only_hybrid_reached"],
        "gold_claims_only_lexical_reached": comparison["gold_claims_only_lexical_reached"],
        "gold_claims_added_clean": recommendation["gold_claims_added_clean"],
        "gold_claims_added_not_clean": recommendation["gold_claims_added_not_clean"],
        "per_claim_regressions": recommendation["per_claim_regressions"],
        "verdict_recorded_at_step_11": recommendation["verdict"],
        "differences": [dict(entry) for entry in comparison["differences"]],
    }

    event_comparison = events["comparison"]
    decision.event_extraction = {
        "cases": event_comparison["cases"],
        "cases_with_identical_requests": event_comparison["cases_with_identical_requests"],
        "cases_with_identical_payloads": event_comparison["cases_with_identical_payloads"],
        "gold_event_types_reached_by_any_scope":
            event_comparison["gold_event_types_reached_by_any_scope"],
        "score_deltas_that_are_nonzero": {
            key: value
            for key, value in event_comparison["score_delta_hybrid_minus_lexical"].items()
            if value},
    }

    decision.width = {
        "reviewed_cases_that_could_distinguish_the_scopes":
            comparison["cases_with_a_difference"]
            + event_comparison["cases"] - event_comparison["cases_with_identical_requests"],
        "reviewed_cases_compared": comparison["cases"] + event_comparison["cases"],
        # Counted from the per-case misses rather than read off a numerator, because the
        # committed scope report records `denominators` and `scores` and no numerator. A
        # (case, concept) pair lexical misses and hybrid does not is exactly one required
        # concept the two scopes disagree about.
        "required_concepts_that_distinguish_the_scopes": len(
            _missed_pairs(hybrid_views[LEXICAL]) - _missed_pairs(hybrid_views[HYBRID])),
        "required_concepts_only_lexical_reaches": len(
            _missed_pairs(hybrid_views[HYBRID]) - _missed_pairs(hybrid_views[LEXICAL])),
        "required_concepts_compared":
            hybrid_views[LEXICAL]["totals"]["denominators"]["required_concepts"],
    }

    # The corpus-cost half. Stated as an absence with its reason, which is the honest form:
    # STAGE_13 §6 lists corpus cost under both scopes as evidence, and one of the two cannot be
    # produced from what this repository commits.
    decision.corpus_cost = {
        "lexical_runs_offline": True,
        "hybrid_runs_offline": False,
        "why": (
            "The hybrid scope ranks a passage against concept vectors, so a corpus-scale run "
            "needs an embedding of every candidate passage. This repository commits the "
            "reviewed cases' vectors and no corpus-scale cache, and `config/extraction.yaml` "
            "deliberately declares no cache root for one. Hybrid's corpus cost is therefore "
            "unmeasured, and stated as unmeasured rather than estimated."),
        "committed_text_vectors": len(
            json.loads((PACKAGE_ROOT / "vectors" / "texts.json").read_text(
                encoding="utf-8"))["entries"]),
        "corpus_passages": narrative["normalization_corpus"]["passages"],
    }

    holds = {
        "adds_a_clean_gold_claim":
            decision.metric_extraction["gold_claims_added_clean"] > 0,
        "no_per_claim_regression":
            decision.metric_extraction["per_claim_regressions"] == 0,
        "comparison_is_wider_than_one_case":
            decision.width["reviewed_cases_that_could_distinguish_the_scopes"] > 1,
        "corpus_cost_is_reproducible_offline":
            bool(decision.corpus_cost["hybrid_runs_offline"]),
    }
    decision.criteria = [
        {"criterion": name, "statement": statement, "holds": holds[name]}
        for name, statement in CRITERIA
    ]

    adopt = holds["adds_a_clean_gold_claim"] and holds["comparison_is_wider_than_one_case"]
    decision.verdict = {
        "default": HYBRID if adopt else LEXICAL,
        "strength": "weak",
        "why_weak": (
            "The two scopes issue identical request digests on "
            f"{comparison['cases_with_identical_scope']} of {comparison['cases']} narrative "
            f"cases and on {event_comparison['cases_with_identical_requests']} of "
            f"{event_comparison['cases']} event cases — the event lane takes no scope at all — "
            "so the whole extraction comparison rests on "
            f"{decision.width['reviewed_cases_that_could_distinguish_the_scopes']} of "
            f"{decision.width['reviewed_cases_compared']} reviewed cases. A decision resting "
            "on one case is a weak decision whichever way it goes."),
        "adoption_conditions_met": adopt,
    }
    decision.open_questions = [
        "Population wording is the dimension the one added claim fails, and whether gold's "
        "whole clause or the lane's noun phrase is right is a founder decision that would "
        "change this verdict if it went the other way.",
        "The second paraphrase probe is unreached under *both* scopes because the concept "
        "sits at rank 2 and `top_k` is 2. Raising `top_k` to 3 takes required-concept recall "
        "to 1.000 in the committed sensitivity sweep and is not tested against extraction "
        "quality at all.",
        "Hybrid's corpus-scale cost is unmeasured. Measuring it needs an embedding cache this "
        "repository does not commit, which is a build decision rather than a scoring one.",
    ]
    return decision


# -- rendering ----------------------------------------------------------------------------------


def render_json(decision: ScopingDecision) -> str:
    payload = {
        "benchmark_version": decision.benchmark_version,
        "implementation_commit": decision.implementation_commit,
        "ontology_definition_hash": decision.ontology_definition_hash,
        "sources": decision.sources,
        "reachability": decision.reachability,
        "metric_extraction": decision.metric_extraction,
        "event_extraction": decision.event_extraction,
        "width": decision.width,
        "corpus_cost": decision.corpus_cost,
        "criteria": decision.criteria,
        "verdict": decision.verdict,
        "open_questions": decision.open_questions,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _table(headers, rows) -> list[str]:
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)
    return lines


def _cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "**no**"
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in sorted(value.items())) or "none"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "—"
    return str(value)


def render_markdown(decision: ScopingDecision) -> str:
    verdict = decision.verdict
    width = decision.width
    lines = [
        "# The lexical-versus-hybrid scoping decision",
        "",
        f"**Default: `{verdict['default']}`. Strength: {verdict['strength']}.**",
        "",
        verdict["why_weak"],
        "",
        "Made from the four committed evaluation reports and from nothing else — no fresh "
        "benchmark run, no gold annotation reaching a runtime decision, and no number this "
        "file computes for itself. The pipeline does not and cannot make this comparison: "
        "four AST guards forbid anything under `extraction/` from reaching these reports.",
        "",
        "## The width of the comparison, beside its result",
        "",
    ]
    lines.extend(_table(
        ("", "Value"),
        [("reviewed cases that could distinguish the scopes at all",
          width["reviewed_cases_that_could_distinguish_the_scopes"]),
         ("reviewed cases compared", width["reviewed_cases_compared"]),
         ("required concepts that distinguish the scopes",
          width["required_concepts_that_distinguish_the_scopes"]),
         ("required concepts compared", width["required_concepts_compared"])]))
    lines.extend(["", "## Reachability", "",
                  "From the committed scope reports. Reachability is what step 9 measured and "
                  "is *not* extraction quality — a concept the scope reaches is a concept the "
                  "lane may then get wrong.", ""])
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), decision.reachability[key])
         for key in sorted(decision.reachability)]))
    lines.extend(["", "## Metric extraction", "",
                  "From the narrative lane report, both scopes.", ""])
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), decision.metric_extraction[key])
         for key in sorted(decision.metric_extraction) if key != "differences"]))
    if decision.metric_extraction["differences"]:
        lines.extend(["", "### The cases where the two scopes differ at all", ""])
        lines.extend(_table(
            ("Case", "Concepts only hybrid", "Claims only hybrid",
             "…of which gold", "Claims only lexical"),
            [(entry["case_id"], entry["concepts_only_hybrid"],
              entry["claims_only_hybrid"], entry["claims_only_hybrid_that_are_gold"],
              entry["claims_only_lexical"])
             for entry in decision.metric_extraction["differences"]]))
    lines.extend(["", "## Event and relationship extraction", "",
                  "The event lane takes no candidate scope: all nineteen declared event types "
                  "carry no alias, and the lane offers the declared category entire. Both "
                  "scopes were run anyway, because asserting \"the scope makes no difference\" "
                  "without running the second scope would be asserting the design rather than "
                  "the result.", ""])
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), decision.event_extraction[key])
         for key in sorted(decision.event_extraction)]))
    lines.extend(["", "## Corpus cost", "", decision.corpus_cost["why"], ""])
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), decision.corpus_cost[key])
         for key in sorted(decision.corpus_cost) if key != "why"]))
    lines.extend(["", "## The four conditions, answered", "",
                  "Hybrid becomes the default only if criteria 1 and 3 both hold. 2 and 4 are "
                  "recorded because they are what a reader would otherwise assume.", ""])
    lines.extend(_table(
        ("Criterion", "Holds", "Statement"),
        [(entry["criterion"], entry["holds"], entry["statement"])
         for entry in decision.criteria]))
    lines.extend(["", "## What this leaves open", ""])
    lines.extend(f"- {question}" for question in decision.open_questions)
    lines.extend(["", "## Provenance", "",
                  "The commit below is the only field permitted to move between two "
                  "regenerations of this report; everything else is a function of the four "
                  "committed reports it reads.", ""])
    lines.extend(_table(
        ("", "Value"),
        [("implementation commit", f"`{decision.implementation_commit}`"),
         ("ontology definition hash", f"`{decision.ontology_definition_hash}`"),
         *((f"source `{name}`", f"`{commit}`")
           for name, commit in sorted(decision.sources.items()))]))
    lines.append("")
    return "\n".join(lines)


def write_reports(decision: ScopingDecision, directory: Path = REPORTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{REPORT_STEM}.json"
    markdown_path = directory / f"{REPORT_STEM}.md"
    json_path.write_text(render_json(decision), encoding="utf-8")
    markdown_path.write_text(render_markdown(decision), encoding="utf-8")
    return json_path, markdown_path
