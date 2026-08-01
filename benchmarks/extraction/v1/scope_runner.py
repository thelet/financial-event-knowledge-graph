"""Scoring the lexical ontology candidate scope against every reviewed case, durably.

A scope is defined for any passage, so unlike the table-lane report this one runs over all 26
cases — narrative, negative and event cases included. What it measures is *reachability*: can
the lane that reads this passage see the concept the reviewers say is in it, and can it see
the concept that would be confused with it.

**It lives under `benchmarks/` because it must.** It reads gold YAML, and runtime extraction
code may not reference the benchmark
(`tests/extraction/test_typed_selection.py::test_runtime_selection_never_imports_the_benchmark`
parses every module under `extraction/` and fails on an import or a path literal). The
dependency runs one way: this module imports the scope, the scope knows nothing of it.

Everything here is derived or declared, never timed: `implementation_commit` is the only field
allowed to differ between two runs. No timestamps, no durations, no absolute paths — a diff of
two reports is a diff of behaviour.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from extraction.stages.scoping import (
    CONFUSION_SIBLING,
    SCOPE_REASONS,
    CandidateScope,
    LexicalOntologyCandidateScope,
)
from extraction.stages.select import AliasIndex
from ontology import load_ontology

from . import runner
from .runner import BENCHMARK_VERSION, CASES_DIR, REPORTS_DIR, EM_DASH, RATIO_DIGITS

REPORT_STEM = "lexical_scope_v1"

# The pairs an aggregate hides. Each is two concepts the ontology declares `distinct_from`
# each other and that filings spell almost identically; a scope that admits one and not the
# other leaves the lane no way to notice it is reading the wrong one, and the merge the
# declaration exists to prevent happens silently. Transcribed from STAGE_08 §5, and checked
# against the ontology by `test_lexical_scoping.py` so a stale transcription fails.
CRITICAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("acquisition_contracts", "homes_under_contract"),
    ("contribution_profit", "contribution_margin"),
    ("gaap_gross_margin", "adjusted_gross_margin"),
    ("gaap_gross_profit", "adjusted_gross_profit"),
    ("homes_sold", "homes_purchased"),
)

# STAGE_08 §5 requires these three by name. They are probes, not cases: short strings handed
# straight to the scope, so a reader can see the scope's answer for the exact wording the
# table lane cannot resolve without reading 400 lines of per-case output.
PROBES: tuple[dict[str, str], ...] = (
    {"name": "Homes sold in period",
     "text": "Homes sold in period",
     "note": "The §8a.8 row label. The table lane abstains here with AMBIGUOUS_ALIAS over "
             "four concepts because the whole label matches no alias and the widest surface "
             "inside it is the declared-ambiguous \u201chomes\u201d. The scope keeps all four "
             "and additionally records `homes_sold` as the one whose own name is present, "
             "which is the evidence a narrative lane needs and a deterministic reader is not "
             "entitled to act on."},
    {"name": "Gross Margin",
     "text": "Gross Margin",
     "note": "A bare prose mention. The shared resolver reads this as `gaap_gross_margin` by "
             "canonical label \u2014 correct for a table cell whose entire contents are the "
             "metric's name \u2014 and the scope still admits `adjusted_gross_margin`, because "
             "in prose the qualifier \u201cAdjusted\u201d may sit anywhere in the sentence. "
             "The resolver's answer never narrows the scope."},
    {"name": "Gross profit",
     "text": "Gross profit",
     "note": "The `aliases.yaml` ambiguity in its purest form: both `gaap_gross_profit` and "
             "`adjusted_gross_profit`, neither preferred, plus the `contribution_profit` "
             "sibling both are declared distinct from."},
)

# -- declared data ---------------------------------------------------------------------------
# The gate STAGE_08 §8 sets is required-concept recall 1.000. This run does not reach it. The
# two misses are stated here rather than described in prose so that
# `test_lexical_scoping.py::test_the_declared_misses_are_the_only_misses` fails in both
# directions: a new miss is a regression, and a miss that stopped being one means this block is
# stale. Neither the ontology nor the scope was changed to close them — broadening either to
# fit two fixtures is the failure mode this repository's conventions name explicitly.

KNOWN_MISS_REASON = (
    "`pct_homes_on_market_gt_120_days` carries five surface forms in the ontology, and the "
    "widest of them \u2014 \u201chomes had been listed on the market for more than 120 days\u201d "
    "\u2014 is the only one long enough to catch a prose sentence. Both passages below phrase "
    "the same fact differently and neither is a whole-phrase match: one interposes \u201cin "
    "inventory\u201d between \u201chomes\u201d and \u201chad been listed\u201d, the other says "
    "\u201cwere listed\u201d rather than \u201chad been listed\u201d *(verified 2026-08-01 by "
    "running this benchmark against the normalized corpus)*. No declared-ambiguous surface "
    "reaches the metric either: bare \u201chomes\u201d resolves to the four home-count concepts "
    "and not to this percentage. Nor does the confusion group \u2014 no metric declares itself "
    "`distinct_from` this one, so no sibling expansion can pull it in. Left open deliberately: "
    "a sixth and seventh alias transcribed from these two sentences would be fitting the "
    "vocabulary to the fixtures, and a substring or token-overlap rule in the scope would be "
    "the ranking stage 9 owns arriving one stage early. This is the wording variance "
    "ontology \u00a716.1 already records for this metric, now measured: it is what an "
    "embedding-backed `HybridOntologyCandidateScope` is for."
)

KNOWN_MISSES: tuple[dict[str, Any], ...] = (
    {"case_id": "letter-prose-inventory-and-120d-q2-2022",
     "concept_id": "pct_homes_on_market_gt_120_days",
     "passage_wording": "5% of our homes were listed on the market for more than 120 days",
     "closest_alias": "homes had been listed on the market for more than 120 days",
     "reason": KNOWN_MISS_REASON},
    {"case_id": "population-our-homes-in-inventory-q1-2023",
     "concept_id": "pct_homes_on_market_gt_120_days",
     "passage_wording":
         "59% of our homes in inventory had been listed on the market for more than 120 days",
     "closest_alias": "homes had been listed on the market for more than 120 days",
     "reason": KNOWN_MISS_REASON},
)


# -- the report model --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeCase:
    """One reviewed case reduced to what a scope can be held to. No scope involvement yet."""

    case_id: str
    category: str
    lane: str
    source_file: str
    document_id: str
    passage_id: str
    gold_metric_ids: tuple[str, ...]
    gold_instance_ids: tuple[str, ...]
    ambiguous_alias_candidates: tuple[str, ...]


@dataclass(frozen=True)
class CriticalCheck:
    """One confusable pair, for one case, with the gold member that put it in question."""

    gold_concept_id: str
    partner_concept_id: str
    gold_in_scope: bool
    partner_in_scope: bool

    @property
    def ok(self) -> bool:
        return self.gold_in_scope and self.partner_in_scope


@dataclass(frozen=True)
class CaseScopeReport:
    case: ScopeCase
    scope: CandidateScope
    missed_metric_ids: tuple[str, ...]
    # Gold entity ids narrowed to the ones the ontology actually declares as instances. A
    # gold event names `kaz_nejatian`; the ontology carries no such instance, and scoring the
    # scope for not containing one would be scoring it against a vocabulary that does not
    # exist.
    known_instance_ids: tuple[str, ...]
    missed_instance_ids: tuple[str, ...]
    critical: tuple[CriticalCheck, ...]
    ambiguity_preserved: bool | None
    missing_ambiguity_candidates: tuple[str, ...]
    # Pairs a *directly matched* member failed to complete. Measured on direct membership
    # rather than on presence, because sibling expansion is deliberately one hop: §3 defines
    # `confusion_sibling` as a sibling of an included metric, and taking the closure would let
    # one ambiguous "margin" pull most of the vocabulary in. `letter-prose-inventory-and-120d-
    # q2-2022` is what made the distinction necessary — `adjusted_gross_profit` is in that
    # scope only as a sibling of `adjusted_gross_margin`, and its own sibling
    # `gaap_gross_profit` is absent. That is not the failure this measures: nothing in that
    # passage names either profit measure, so no lane can read one as the other.
    asymmetric_pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ProbeReport:
    name: str
    text: str
    note: str
    scope: CandidateScope


@dataclass
class LexicalScopeReport:
    benchmark_version: str
    ontology_definition_hash: str
    normalization_corpus: dict[str, Any]
    implementation_commit: str
    scope: dict[str, str]
    cases: list[CaseScopeReport]
    probes: list[ProbeReport]
    totals: dict[str, Any]
    known_misses: tuple[dict[str, Any], ...] = KNOWN_MISSES

    def case(self, case_id: str) -> CaseScopeReport | None:
        return next((c for c in self.cases if c.case.case_id == case_id), None)


# -- loading -----------------------------------------------------------------------------------


def load_scope_cases(cases_dir: Path = CASES_DIR) -> list[ScopeCase]:
    """Every reviewed case with a passage id, ordered by case id.

    All of them, not just the table ones: a scope is defined for any passage, and the negative
    and event cases are where a scope that quietly returned everything would be visible.
    """
    cases: list[ScopeCase] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            if not case.get("passage_id"):
                continue
            cases.append(ScopeCase(
                case_id=case["case_id"],
                category=case.get("category") or "",
                lane=case.get("lane") or "",
                source_file=path.name,
                document_id=case["document_id"],
                passage_id=case["passage_id"],
                gold_metric_ids=tuple(sorted(
                    {g["metric_id"] for g in (case.get("gold_claims") or [])})),
                gold_instance_ids=tuple(sorted(_entity_ids(case))),
                ambiguous_alias_candidates=tuple(sorted(
                    _ambiguous_alias_candidates(case))),
            ))
    return sorted(cases, key=lambda c: c.case_id)


def _entity_ids(case: dict) -> set[str]:
    """Every entity a gold annotation names, wherever it names it.

    Claims carry `subject_entity_id`; events carry participants with `entity_id`;
    relationships carry endpoints. Walked generically rather than per shape so a case file
    that grows a new gold section is covered without this function being edited into
    agreement with it.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"subject_entity_id", "entity_id", "object_entity_id"} and value:
                    found.add(str(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for section in ("gold_claims", "gold_events", "gold_relationships"):
        walk(case.get(section))
    return found


def _ambiguous_alias_candidates(case: dict) -> set[str]:
    """The concepts an `AMBIGUOUS_ALIAS` abstention says the lane must not choose between."""
    candidates: set[str] = set()
    for abstention in case.get("abstentions") or []:
        if abstention.get("reason") == "AMBIGUOUS_ALIAS":
            candidates.update(str(c) for c in (abstention.get("candidates") or ()))
    return candidates


# -- running ------------------------------------------------------------------------------------


def run_scope(
    cases: list[ScopeCase],
    *,
    passages: dict[str, dict],
    ontology,
    catalog_root: Path | None = None,
    implementation_commit: str | None = None,
) -> LexicalScopeReport:
    """Drive the scope over every case and score reachability."""
    scope = LexicalOntologyCandidateScope(ontology, AliasIndex.from_ontology(ontology))
    instance_ids = {i.instance_id for i in ontology.registry.definitions.instances}

    case_reports = [
        _case_report(case, scope.scope_for(passages[case.passage_id]["text"]), instance_ids)
        for case in cases
    ]
    probes = [
        ProbeReport(name=p["name"], text=p["text"], note=p["note"],
                    scope=scope.scope_for(p["text"]))
        for p in PROBES
    ]
    return LexicalScopeReport(
        benchmark_version=BENCHMARK_VERSION,
        ontology_definition_hash=ontology.definition_hash,
        normalization_corpus=runner.corpus_identity(passages, catalog_root),
        implementation_commit=implementation_commit or runner.head_commit(),
        scope={"name": scope.name, "version": scope.version},
        cases=case_reports,
        probes=probes,
        totals=_totals(case_reports),
    )


def _case_report(case: ScopeCase, scope: CandidateScope, instance_ids) -> CaseScopeReport:
    present = set(scope.concept_ids)

    critical: list[CriticalCheck] = []
    for pair in CRITICAL_PAIRS:
        for concept_id, partner in (pair, (pair[1], pair[0])):
            if concept_id in case.gold_metric_ids:
                critical.append(CriticalCheck(
                    gold_concept_id=concept_id, partner_concept_id=partner,
                    gold_in_scope=concept_id in present, partner_in_scope=partner in present))

    directly_matched = {
        c.concept_id for c in scope.concepts if set(c.reasons) - {CONFUSION_SIBLING}}
    asymmetric = tuple(
        pair for pair in CRITICAL_PAIRS
        if (pair[0] in directly_matched or pair[1] in directly_matched)
        and not (pair[0] in present and pair[1] in present)
    )

    missing_ambiguity = tuple(
        c for c in case.ambiguous_alias_candidates if c not in present)
    known_instances = tuple(i for i in case.gold_instance_ids if i in instance_ids)

    return CaseScopeReport(
        case=case,
        scope=scope,
        missed_metric_ids=tuple(m for m in case.gold_metric_ids if m not in present),
        known_instance_ids=known_instances,
        missed_instance_ids=tuple(i for i in known_instances if i not in present),
        critical=tuple(sorted(critical, key=lambda c: (c.gold_concept_id,
                                                      c.partner_concept_id))),
        ambiguity_preserved=(None if not case.ambiguous_alias_candidates
                             else not missing_ambiguity),
        missing_ambiguity_candidates=missing_ambiguity,
        asymmetric_pairs=asymmetric,
    )


def _totals(cases: list[CaseScopeReport]) -> dict[str, Any]:
    sizes = [len(c.scope) for c in cases]

    required_total = sum(len(c.case.gold_metric_ids) for c in cases)
    required_hit = required_total - sum(len(c.missed_metric_ids) for c in cases)

    critical_checks = [check for c in cases for check in c.critical]
    critical_hit = sum(1 for check in critical_checks if check.ok)

    instance_total = sum(len(c.known_instance_ids) for c in cases)
    instance_hit = instance_total - sum(len(c.missed_instance_ids) for c in cases)

    ambiguity_cases = [c for c in cases if c.ambiguity_preserved is not None]
    ambiguity_hit = sum(1 for c in ambiguity_cases if c.ambiguity_preserved)

    counts: dict[str, int] = {reason: 0 for reason in sorted(SCOPE_REASONS)}
    for case in cases:
        for reason, count in case.scope.counts_by_reason().items():
            counts[reason] += count

    return {
        "cases": len(cases),
        "candidates": sum(sizes),
        "counts_by_reason": counts,
        "confusion_expansions": sum(len(c.scope.expansions) for c in cases),
        "scope_size": {
            "mean": round(statistics.mean(sizes), RATIO_DIGITS) if sizes else 0.0,
            "median": round(statistics.median(sizes), RATIO_DIGITS) if sizes else 0.0,
            "min": min(sizes) if sizes else 0,
            "max": max(sizes) if sizes else 0,
        },
        "denominators": {
            "required_concepts": required_total,
            "critical_checks": len(critical_checks),
            "known_instances": instance_total,
            "ambiguity_cases": len(ambiguity_cases),
        },
        "asymmetric_critical_pairs": sum(len(c.asymmetric_pairs) for c in cases),
        "scores": {
            "required_concept_recall": _ratio(required_hit, required_total),
            "critical_concept_recall": _ratio(critical_hit, len(critical_checks)),
            "known_instance_recall": _ratio(instance_hit, instance_total),
            "ambiguity_preservation": _ratio(ambiguity_hit, len(ambiguity_cases)),
        },
    }


def _ratio(hit: int, total: int) -> float:
    """1.0 for an empty denominator: nothing was asked for and nothing was missed.

    Rounded before serialisation for the same reason the table-lane report rounds — an
    unrounded ratio makes a committed file sensitive to the last bits of a float division.
    """
    return 1.0 if total == 0 else round(hit / total, RATIO_DIGITS)


def build_report(*, catalog_root: Path | None = None,
                 implementation_commit: str | None = None) -> LexicalScopeReport:
    """The whole thing, from disk: cases, corpus, ontology, scope, scores."""
    return run_scope(
        load_scope_cases(),
        passages=runner.load_passages(catalog_root),
        ontology=load_ontology(),
        catalog_root=catalog_root,
        implementation_commit=implementation_commit,
    )


# -- rendering -----------------------------------------------------------------------------------


def render_json(report: LexicalScopeReport) -> str:
    payload = {
        "benchmark_version": report.benchmark_version,
        "ontology_definition_hash": report.ontology_definition_hash,
        "normalization_corpus": report.normalization_corpus,
        "implementation_commit": report.implementation_commit,
        "scope": report.scope,
        "critical_pairs": [list(pair) for pair in CRITICAL_PAIRS],
        "probes": [_probe_json(p) for p in report.probes],
        "cases": [_case_json(c) for c in report.cases],
        "totals": report.totals,
        "known_misses": [dict(m) for m in report.known_misses],
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _scope_json(scope: CandidateScope) -> list[dict[str, Any]]:
    return [
        {"concept_id": c.concept_id, "reasons": list(c.reasons),
         "surfaces": list(c.surfaces), "protected": c.protected}
        for c in scope.concepts
    ]


def _probe_json(probe: ProbeReport) -> dict[str, Any]:
    return {
        "name": probe.name,
        "text": probe.text,
        "note": probe.note,
        "scope_size": len(probe.scope),
        "candidates": _scope_json(probe.scope),
        "confusion_expansions": [
            {"concept_id": e.concept_id, "sibling_id": e.sibling_id}
            for e in probe.scope.expansions],
    }


def _case_json(case: CaseScopeReport) -> dict[str, Any]:
    return {
        "case_id": case.case.case_id,
        "category": case.case.category,
        "lane": case.case.lane,
        "source_file": case.case.source_file,
        "passage_id": case.case.passage_id,
        "document_id": case.case.document_id,
        "scope_size": len(case.scope),
        "counts_by_reason": case.scope.counts_by_reason(),
        "candidates": _scope_json(case.scope),
        "protected": list(case.scope.protected_ids),
        "confusion_expansions": [
            {"concept_id": e.concept_id, "sibling_id": e.sibling_id}
            for e in case.scope.expansions],
        "gold_metric_ids": list(case.case.gold_metric_ids),
        "missed_metric_ids": list(case.missed_metric_ids),
        "gold_instance_ids": list(case.case.gold_instance_ids),
        "known_instance_ids": list(case.known_instance_ids),
        "missed_instance_ids": list(case.missed_instance_ids),
        "critical_checks": [
            {"gold_concept_id": c.gold_concept_id,
             "partner_concept_id": c.partner_concept_id,
             "gold_in_scope": c.gold_in_scope,
             "partner_in_scope": c.partner_in_scope,
             "ok": c.ok}
            for c in case.critical],
        "asymmetric_critical_pairs": [list(p) for p in case.asymmetric_pairs],
        "ambiguity": {
            "expected_candidates": list(case.case.ambiguous_alias_candidates),
            "missing": list(case.missing_ambiguity_candidates),
            "preserved": case.ambiguity_preserved,
        },
    }


def render_markdown(report: LexicalScopeReport) -> str:
    totals = report.totals
    scores = totals["scores"]
    sizes = totals["scope_size"]
    lines: list[str] = [
        "# Lexical ontology candidate scope \u2014 benchmark v1 results",
        "",
        "Generated by `python -m benchmarks.extraction.v1 scope-report`. Every number here is "
        "produced by driving `LexicalOntologyCandidateScope` over the passage of every "
        "reviewed case and comparing what it admits against what the reviewers annotated. "
        "**If this report disagrees with the scope, the report is wrong** \u2014 changing "
        "extraction to improve a number here is a gate failure, not a fix.",
        "",
        "The scope never removes anything. There is no threshold, no top-k and no filter in "
        "it, so every candidate below is protected and stage 9's embeddings may only add "
        "(STAGE_08 \u00a73).",
        "",
        "## Identity",
        "",
        "| | |",
        "| --- | --- |",
        f"| benchmark version | `{report.benchmark_version}` |",
        f"| implementation commit | `{report.implementation_commit}` |",
        f"| scope | `{report.scope['name']}` v`{report.scope['version']}` |",
        f"| ontology definition hash | `{report.ontology_definition_hash}` |",
        f"| normalized documents | {report.normalization_corpus['documents']} |",
        f"| normalized passages | {report.normalization_corpus['passages']} |",
        f"| `documents.jsonl` sha256 | `{report.normalization_corpus['documents_sha256']}` |",
        "",
        "## Acceptance",
        "",
        "STAGE_08 \u00a78 sets three gates. Two hold; one does not, and is reported as a "
        "failure rather than smoothed over.",
        "",
        "| Gate | Required | Measured | Denominator | |",
        "| --- | --- | --- | --- | --- |",
    ]
    gates = (
        ("required-concept recall", "required_concept_recall", "required_concepts"),
        ("critical-concept recall", "critical_concept_recall", "critical_checks"),
        ("ambiguity preservation", "ambiguity_preservation", "ambiguity_cases"),
    )
    for label, key, denominator in gates:
        value = scores[key]
        lines.append(
            f"| {label} | 1.000 | **{value:.3f}** | "
            f"{totals['denominators'][denominator]} | "
            + ("PASS" if value >= 1.0 else "**FAIL**") + " |")
    lines += [
        f"| known-instance recall | \u2014 | {scores['known_instance_recall']:.3f} | "
        f"{totals['denominators']['known_instances']} | reported |",
        "",
        "`known-instance recall` carries no gate because it cannot fail as posed: the only "
        "ontology instance any gold annotation names is `opendoor`, and `opendoor` is one of "
        "the three stable-core concepts every scope contains unconditionally. It is reported "
        "so the denominator is visible rather than implied \u2014 the `known_instance` reason "
        "code's own count below is the number that would move if instance matching broke.",
        "",
        "## Scope size",
        "",
        "| | |",
        "| --- | --- |",
        f"| cases | {totals['cases']} |",
        f"| candidates admitted | {totals['candidates']} |",
        f"| mean | {sizes['mean']:.3f} |",
        f"| median | {sizes['median']:.1f} |",
        f"| min | {sizes['min']} |",
        f"| max | {sizes['max']} |",
        f"| confusion-group expansions | {totals['confusion_expansions']} |",
        f"| critical pairs held asymmetrically | {totals['asymmetric_critical_pairs']} |",
        "",
        "A pair is counted asymmetric when the passage matched one member directly \u2014 by "
        "an alias, a canonical label, a row label or an ambiguous surface \u2014 and the "
        "other is not in scope at all. That is the state in which a lane cannot tell it is "
        "reading the wrong one, and zero is the only acceptable value; the confusion-group "
        "expansion is what guarantees it. Pairs whose only member arrived as somebody else's "
        "sibling are not counted: sibling expansion is one hop by design (\u00a73 defines it "
        "as a sibling of an included metric), and taking the closure would let one ambiguous "
        "\u201cmargin\u201d pull most of the vocabulary into every scope.",
        "",
        "## Candidates by reason",
        "",
        "Counted across all cases; one candidate carrying two reasons is counted under both, "
        "so these sum to more than the candidate total.",
        "",
        "| Reason | Candidates |",
        "| --- | --- |",
    ]
    for reason, count in totals["counts_by_reason"].items():
        lines.append(f"| `{reason}` | {count} |")
    lines += ["", "## Named probes", ""]
    lines += _probes_markdown(report)
    lines += _misses_markdown(report)
    lines += _critical_markdown(report)
    lines += _cases_markdown(report)
    return "\n".join(lines) + "\n"


def _probes_markdown(report: LexicalScopeReport) -> list[str]:
    lines = [
        "The three wordings STAGE_08 \u00a75 requires by name, each handed to the scope as its "
        "whole text.",
        "",
    ]
    for probe in report.probes:
        lines += [
            f"### `{probe.name}`",
            "",
            probe.note,
            "",
            f"{len(probe.scope)} candidates:",
            "",
            "| Concept | Reasons | Surfaces |",
            "| --- | --- | --- |",
        ]
        for candidate in probe.scope.concepts:
            lines.append(
                f"| `{candidate.concept_id}` | "
                + ", ".join(f"`{r}`" for r in candidate.reasons) + " | "
                + (", ".join(_cell(s) for s in candidate.surfaces) or EM_DASH) + " |")
        lines.append("")
    return lines


def _misses_markdown(report: LexicalScopeReport) -> list[str]:
    lines = [
        f"## Misses ({len(report.known_misses)})",
        "",
        "| Case | Concept | Wording in the passage | Closest alias the ontology carries |",
        "| --- | --- | --- | --- |",
    ]
    for miss in report.known_misses:
        lines.append(
            f"| `{miss['case_id']}` | `{miss['concept_id']}` | "
            f"{_cell(miss['passage_wording'])} | {_cell(miss['closest_alias'])} |")
    lines.append("")
    for reason in sorted({m["reason"] for m in report.known_misses}):
        lines += [reason, ""]
    return lines


def _critical_markdown(report: LexicalScopeReport) -> list[str]:
    lines = [
        "## Confusable pairs",
        "",
        "Scored separately because an aggregate hides them. A check exists for every case in "
        "which a gold claim names one member of a pair; it passes only when **both** members "
        "are in scope.",
        "",
        "| Pair | Checks | Passed | Cases where a direct match left it incomplete |",
        "| --- | --- | --- | --- |",
    ]
    for pair in CRITICAL_PAIRS:
        members = set(pair)
        checks = [c for case in report.cases for c in case.critical
                  if c.gold_concept_id in members]
        asymmetric = [case.case.case_id for case in report.cases
                      if pair in case.asymmetric_pairs]
        lines.append(
            f"| `{pair[0]}` / `{pair[1]}` | {len(checks)} | "
            f"{sum(1 for c in checks if c.ok)} | "
            + (", ".join(f"`{c}`" for c in asymmetric) or "none") + " |")
    lines.append("")
    return lines


def _cases_markdown(report: LexicalScopeReport) -> list[str]:
    lines = [
        "## Per-case results",
        "",
        "| Case | Lane | Scope | Gold metrics | Missed | Expansions |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in report.cases:
        lines.append(
            f"| [`{case.case.case_id}`](#{_anchor(case.case.case_id)}) | {case.case.lane} | "
            f"{len(case.scope)} | {len(case.case.gold_metric_ids)} | "
            f"{len(case.missed_metric_ids)} | {len(case.scope.expansions)} |")
    lines.append("")

    for case in report.cases:
        lines += _case_markdown(case)
    return lines


def _case_markdown(case: CaseScopeReport) -> list[str]:
    counts = case.scope.counts_by_reason()
    lines = [
        f"## {case.case.case_id}",
        "",
        f"- category: `{case.case.category}` \u00b7 lane `{case.case.lane}` "
        f"(from `cases/{case.case.source_file}`)",
        f"- passage: `{case.case.passage_id}`",
        f"- scope size {len(case.scope)} \u00b7 all {len(case.scope.protected_ids)} protected "
        f"\u00b7 {len(case.scope.expansions)} confusion-group expansions",
        "- reasons: " + ", ".join(f"`{r}` {n}" for r, n in counts.items() if n),
        "- gold metrics: " + (", ".join(f"`{m}`" for m in case.case.gold_metric_ids)
                              or "none"),
        "- missed: " + (", ".join(f"`{m}`" for m in case.missed_metric_ids) or "none"),
    ]
    if case.case.ambiguous_alias_candidates:
        lines.append(
            "- ambiguity preservation: "
            + ("preserved" if case.ambiguity_preserved else "**LOST**")
            + " over " + ", ".join(f"`{c}`" for c in case.case.ambiguous_alias_candidates))
    lines += [
        "",
        "| Concept | Reasons | Surfaces |",
        "| --- | --- | --- |",
    ]
    for candidate in case.scope.concepts:
        lines.append(
            f"| `{candidate.concept_id}` | "
            + ", ".join(f"`{r}`" for r in candidate.reasons) + " | "
            + (", ".join(_cell(s) for s in candidate.surfaces) or EM_DASH) + " |")
    lines.append("")
    if case.scope.expansions:
        lines += [
            "Confusion-group expansions: "
            + ", ".join(f"`{e.concept_id}` \u2192 `{e.sibling_id}`"
                        for e in case.scope.expansions),
            "",
        ]
    return lines


def _anchor(case_id: str) -> str:
    return case_id.lower().replace(" ", "-")


def _cell(text: str | None) -> str:
    """Markdown table cells cannot carry a pipe or a newline."""
    if not text:
        return EM_DASH
    return text.replace("|", "\\|").replace("\n", " ").strip()


# -- writing --------------------------------------------------------------------------------------


def write_reports(
    report: LexicalScopeReport, directory: Path = REPORTS_DIR
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{REPORT_STEM}.json"
    markdown_path = directory / f"{REPORT_STEM}.md"
    json_path.write_text(render_json(report), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
