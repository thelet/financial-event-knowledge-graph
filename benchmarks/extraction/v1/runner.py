"""Scoring the deterministic table lane against the reviewed table cases, durably.

The evaluation this writes down already existed — inside a test fixture, where it could not
be read, cited, or diffed between commits. This module composes it into a committed
artifact. `extraction/stages/tables/evaluation.py` still owns every score; nothing here
recomputes a ratio it could ask for.

**It lives under `benchmarks/` because it must.** The runner reads gold YAML and drives the
lane, and runtime extraction code may not reference the benchmark
(`tests/extraction/test_typed_selection.py::test_runtime_selection_never_imports_the_benchmark`
parses every module under `extraction/` and fails on an import or a path literal). The
dependency therefore runs one way: this module imports the lane, the lane knows nothing of
this module.

Everything the report contains is derived or declared, never timed or measured at write
time: `implementation_commit` is the only field allowed to differ between two runs, and it
differs only when the commit does. No timestamps, no durations, no absolute paths — a
diff of two reports is a diff of behaviour.
"""

from __future__ import annotations

import collections
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from extraction.core.assembly import deferred_metric_ids
from extraction.core.models import LaneClaim, PeriodRef
from extraction.stages.select import AliasIndex
from extraction.stages.tables import DeterministicTableClaimLane
from extraction.stages.tables.evaluation import (
    GoldClaim, compare, evaluate_case, summarise,
)
from normalization.core.config import load_config
from ontology import load_ontology

BENCHMARK_VERSION = "v1"

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[2]
CASES_DIR = PACKAGE_ROOT / "cases"
REPORTS_DIR = PACKAGE_ROOT / "reports"
REPORT_STEM = "table_lane_v1"

# The three categories the deterministic lane is answerable for. Prose categories are the
# narrative lane's benchmark and are not scored here.
TABLE_CATEGORIES = frozenset({
    "deterministic_kpi_table", "sparse_reconciliation_table", "formula_drift",
})

# The six per-match dimensions `evaluation.py` scores. With `metric_recall` and
# `matched_over_emitted` these are the eight numbers reported per case and in total.
MATCH_DIMENSIONS = ("value", "unit", "scale", "period", "subject", "evidence")

EM_DASH = "—"

# Ratios are rounded before serialisation. Not for cosmetics: an unrounded ratio would make
# the committed report sensitive to the last bits of a float division, and 6 decimals is
# three more than any number in the plan is quoted to.
RATIO_DIGITS = 6

# -- declared data ---------------------------------------------------------------------------
# Both blocks are transcriptions of V1_CLAIM_EXTRACTION.md, not measurements. They are stated
# here so the report is readable without the plan beside it. §6 test 6 checks every field of
# each declared miss — case, metric, period, row index, issue code and candidate concept ids —
# against the issue the lane actually emits for that row, so a stale transcription fails
# rather than misleads. The shapes below are not checked that way: no case exercises three of
# them, and the one the corpus does exercise is named in `observed_in`.

UNSUPPORTED_TABLE_SHAPES: tuple[dict[str, str], ...] = (
    {"shape": "A single period column",
     "behaviour": "No claims — header detection requires >=2 period-ish cells in a row. No "
                  "corpus KPI table is like this; a future one could be."},
    # Corrected 2026-08-01 from this run's own output. §8a.7 says a wrong answer of this kind
    # is "caught rather than emitted"; the report disproves that in `kpi-table-q4-2023-earnings`
    # with 21 counterexamples, so the claim is stated here as what actually holds.
    {"shape": "Date columns not evenly divisible by duration groups",
     "behaviour": "Falls back to positional group assignment. The period-type check catches "
                  "an instant read as a duration, but NOT a duration read as the wrong "
                  "duration: in kpi-table-q4-2023-earnings, 5 `Three Months Ended` columns "
                  "beside 2 `Year Ended December 31,` columns make the fallback read "
                  "`December 31, 2022` as FY2022 and `March 31, 2023` / `June 30, 2023` as "
                  "twelve-month durations — 21 emitted observations with the wrong period, "
                  "none of them gold, so the case still scores 1.000 on every dimension. A "
                  "wrong answer of this shape is emitted, not caught.",
     "observed_in": "kpi-table-q4-2023-earnings"},
    {"shape": "A row label genuinely spanning several cells",
     "behaviour": "Only the first non-empty cell is read as the label."},
    {"shape": "Nested or merged data cells beyond layout gutters",
     "behaviour": "Unrecognised; the row is refused with AMBIGUOUS_COLUMN_ALIGNMENT rather "
                  "than guessed."},
)

KNOWN_MISS_REASON = (
    "The Q1 2021 reconciliation labels its homes-sold row \u201cHomes sold in period\u201d, a "
    "surface form the ontology does not carry as an alias. It is not simply unresolved: the "
    "whole label matches nothing, so the widest alias hit inside it is the declared-ambiguous "
    "\u201chomes\u201d, and the lane abstains with AMBIGUOUS_ALIAS over four candidate concepts "
    "rather than UNRESOLVED_METRIC *(verified 2026-08-01 by running this benchmark; "
    "V1_CLAIM_EXTRACTION.md \u00a78a.8 states the gap but not the code)*. Left unresolved "
    "deliberately: broadening the alias would be fitting the vocabulary to a fixture. The "
    "benchmark case says so itself \u2014 the label \u201chas to resolve semantically or this row "
    "is missed\u201d \u2014 which makes it work for a narrative or hybrid-scoped lane, not for a "
    "deterministic reader."
)

CASE_OF_KNOWN_MISSES = "recon-q1-2021-holding-costs-split-rows"

# All three are the same row of the same table, read across its three period columns
# *(verified 2026-08-01 by running this benchmark)*.
KNOWN_MISSES: tuple[dict[str, Any], ...] = tuple(
    {"case_id": CASE_OF_KNOWN_MISSES, "metric_id": "homes_sold", "period_key": period,
     "row_label": "Homes sold in period", "row_index": 17,
     "issue_code": "AMBIGUOUS_ALIAS",
     "candidate_concept_ids": ["homes_purchased", "homes_sold", "homes_under_contract",
                               "housing_inventory_homes"],
     "reason": KNOWN_MISS_REASON}
    for period in ("2020Q1", "2020Q4", "2021Q1")
)


# -- the report model --------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldObservation:
    """One reviewed observation, as the case file states it."""

    metric_id: str
    period_key: str
    value: float
    unit: str
    scale_applied: str
    currency: str | None
    subject_entity_id: str
    column_label: str | None

    def as_gold_claim(self) -> GoldClaim:
        return GoldClaim(self.metric_id, self.value, self.unit, self.period_key,
                         self.subject_entity_id, self.scale_applied, self.currency)


@dataclass(frozen=True)
class EmittedObservation:
    """One `LaneClaim`, flattened to what a reader needs to check it against the filing.

    `block_ids` are deliberately absent: they are a property of the passage, identical for
    every claim in a case, and recorded once on the case instead of 410 times.
    """

    metric_id: str
    period_key: str
    value: float
    unit: str
    currency: str | None
    scale: str
    scale_source: str
    subject_entity_id: str
    raw_text: str
    row_label: str | None
    column_label: str | None
    passage_id: str
    row_index: int | None
    value_column_index: int | None
    evidence_resolves: bool
    is_instant: bool


@dataclass(frozen=True)
class IssueRecord:
    """A structured silence: a row that produced no claim, and enough to reproduce why."""

    code: str
    row_index: int | None
    raw_label: str | None
    normalized_label: str | None
    candidate_concept_ids: tuple[str, ...]
    required_lane: str | None
    column_index: int | None
    detail: str


@dataclass(frozen=True)
class MatchRecord:
    """A gold observation set beside the claim that matched it, dimension by dimension."""

    metric_id: str
    period_key: str
    expected_value: float
    emitted_value: float
    expected_unit: str
    emitted_unit: str
    expected_scale: str
    emitted_scale: str
    expected_subject_entity_id: str
    emitted_subject_entity_id: str
    # Where the claim came from in the table. Carried so a WRONG verdict in the Markdown can
    # be taken back to the row and column of the filing without opening the JSON.
    emitted_row_index: int | None
    emitted_column_label: str | None
    value_ok: bool
    unit_ok: bool
    scale_ok: bool
    period_ok: bool
    subject_ok: bool
    evidence_ok: bool

    @property
    def all_ok(self) -> bool:
        return all((self.value_ok, self.unit_ok, self.scale_ok, self.period_ok,
                    self.subject_ok, self.evidence_ok))


@dataclass(frozen=True)
class ObservationKey:
    metric_id: str
    period_key: str


@dataclass
class CaseReport:
    case_id: str
    category: str
    source_file: str
    passage_id: str
    document_id: str
    document_type: str
    table_id: str | None
    block_ids: tuple[str, ...]
    gold: tuple[GoldObservation, ...]
    emitted: tuple[EmittedObservation, ...]
    matched: tuple[MatchRecord, ...]
    missed: tuple[ObservationKey, ...]
    unmatched_emitted: tuple[ObservationKey, ...]
    scores: dict[str, float]
    counts: dict[str, int]
    issues: tuple[IssueRecord, ...]
    issues_by_code: dict[str, int]


@dataclass
class TableLaneReport:
    benchmark_version: str
    ontology_definition_hash: str
    normalization_corpus: dict[str, Any]
    implementation_commit: str
    lane: dict[str, str]
    cases: list[CaseReport]
    totals: dict[str, Any]
    unsupported_table_shapes: tuple[dict[str, str], ...] = UNSUPPORTED_TABLE_SHAPES
    known_misses: tuple[dict[str, Any], ...] = KNOWN_MISSES

    def case(self, case_id: str) -> CaseReport | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


@dataclass(frozen=True)
class BenchmarkCase:
    """A reviewed table case, as loaded — no lane involvement yet."""

    case_id: str
    category: str
    source_file: str
    document_id: str
    passage_id: str
    gold: tuple[GoldObservation, ...]


# -- loading -----------------------------------------------------------------------------------


def load_table_cases(cases_dir: Path = CASES_DIR) -> list[BenchmarkCase]:
    """Every `lane: tables` case in a scored category, ordered by case id."""
    cases: list[BenchmarkCase] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            if case.get("lane") != "tables" or case.get("category") not in TABLE_CATEGORIES:
                continue
            cases.append(BenchmarkCase(
                case_id=case["case_id"],
                category=case["category"],
                source_file=path.name,
                document_id=case["document_id"],
                passage_id=case["passage_id"],
                gold=tuple(_gold_observation(g) for g in (case.get("gold_claims") or [])),
            ))
    return sorted(cases, key=lambda c: c.case_id)


def load_passages(catalog_root: Path | None = None) -> dict[str, dict]:
    root = catalog_root or load_config(REPO_ROOT).catalog_root
    path = root / "passages.jsonl"
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    return rows


def _gold_observation(raw: dict) -> GoldObservation:
    return GoldObservation(
        metric_id=raw["metric_id"],
        period_key=_gold_period_key(raw),
        value=raw["value"],
        unit=raw["unit"],
        # The case file omits `scale_applied` when no scale was applied; `evaluation.py`
        # reads that omission as "units", so the report states it rather than leaving a null
        # a reader would have to interpret.
        scale_applied=raw.get("scale_applied") or "units",
        currency=raw.get("currency"),
        subject_entity_id=raw["subject_entity_id"],
        column_label=raw.get("column_label"),
    )


def _gold_period_key(raw: dict) -> str:
    if raw.get("instant_date"):
        return raw["instant_date"]
    return PeriodRef(period_start=raw.get("period_start"),
                     period_end=raw.get("period_end")).key


# -- running ------------------------------------------------------------------------------------


def run_table_lane(
    cases: list[BenchmarkCase],
    *,
    passages: dict[str, dict],
    ontology,
    catalog_root: Path | None = None,
    implementation_commit: str | None = None,
) -> TableLaneReport:
    """Drive the lane over every case and score it with `evaluation.py`.

    The preceding passage is found the way the lane's own contract expects it: the row in the
    same document at `passage_sequence - 1`. Nine of 74 KPI-bearing tables declare their
    scale there and nowhere else, so a runner that skipped it would report a scale failure
    the lane does not have.
    """
    lane = DeterministicTableClaimLane(
        ontology, AliasIndex.from_ontology(ontology), deferred_metric_ids(ontology))

    by_document: dict[str, dict[int, dict]] = collections.defaultdict(dict)
    for row in passages.values():
        by_document[row["document_id"]][row["passage_sequence"]] = row

    case_reports: list[CaseReport] = []
    case_results = []
    for case in cases:
        row = passages[case.passage_id]
        preceding = by_document[row["document_id"]].get(row["passage_sequence"] - 1)
        extraction = lane.extract_table(
            row["text"],
            passage_id=case.passage_id,
            table_id=row["table_id"],
            block_ids=tuple(row["block_ids"]),
            preceding_text=preceding["text"] if preceding else None,
            preceding_passage_id=preceding["passage_id"] if preceding else None,
        )
        result = evaluate_case(
            case.case_id, case.passage_id, extraction.claims,
            [g.as_gold_claim() for g in case.gold], extraction.issues,
            resolves_evidence=lambda claim: claim.passage_id in passages)
        case_results.append(result)
        case_reports.append(_case_report(case, row, extraction, result, passages))

    evaluation = summarise(case_results)
    return TableLaneReport(
        benchmark_version=BENCHMARK_VERSION,
        ontology_definition_hash=ontology.definition_hash,
        normalization_corpus=_corpus_identity(passages, catalog_root),
        implementation_commit=implementation_commit or _head_commit(),
        lane={"name": DeterministicTableClaimLane.name,
              "version": DeterministicTableClaimLane.version},
        cases=case_reports,
        totals={
            "cases": len(case_reports),
            "gold_observations": evaluation.gold_total,
            "emitted_observations": evaluation.emitted_total,
            "matched_observations": evaluation.matched_total,
            "missed_observations": evaluation.gold_total - evaluation.matched_total,
            "issues": sum(evaluation.issues_by_code.values()),
            "issues_by_code": dict(sorted(evaluation.issues_by_code.items())),
            "scores": _scores(evaluation),
        },
    )


def _case_report(case, row, extraction, result, passages) -> CaseReport:
    emitted = tuple(sorted(
        (_emitted_observation(claim, passages) for claim in extraction.claims),
        # Primary order is (metric_id, period_key) as the plan requires; the row and column
        # indices break ties between two cells that would otherwise sort equal, so the order
        # never depends on emission order surviving a refactor.
        key=lambda o: (o.metric_id, o.period_key, o.row_index or -1,
                       o.value_column_index or -1)))
    by_key = {(c.metric_id, c.period.key): c for c in extraction.claims}
    matched = tuple(sorted(
        (_match_record(gold, by_key[(gold.metric_id, gold.period_key)], passages)
         for gold in case.gold if (gold.metric_id, gold.period_key) in by_key),
        key=lambda m: (m.metric_id, m.period_key)))
    return CaseReport(
        case_id=case.case_id,
        category=case.category,
        source_file=case.source_file,
        passage_id=case.passage_id,
        document_id=row["document_id"],
        document_type=row["document_type"],
        table_id=row["table_id"],
        block_ids=tuple(row["block_ids"]),
        gold=tuple(sorted(case.gold, key=lambda g: (g.metric_id, g.period_key))),
        emitted=emitted,
        matched=matched,
        missed=_keys(result.missing),
        unmatched_emitted=_keys(result.unlisted),
        scores=_scores(summarise([result])),
        counts={"gold": result.gold, "emitted": result.emitted, "matched": result.matched,
                "missed": len(result.missing), "unmatched_emitted": len(result.unlisted),
                "issues": sum(result.issues.values())},
        issues=tuple(sorted(
            (_issue_record(issue) for issue in extraction.issues),
            key=lambda i: (i.code, i.row_index if i.row_index is not None else -1,
                           i.raw_label or ""))),
        issues_by_code=dict(sorted(result.issues.items())),
    )


def _emitted_observation(claim: LaneClaim, passages) -> EmittedObservation:
    metadata = claim.extractor_metadata
    return EmittedObservation(
        metric_id=claim.metric_id,
        period_key=claim.period.key,
        value=claim.value,
        unit=claim.unit,
        currency=claim.currency,
        scale=claim.scale.scale if claim.scale else "units",
        scale_source=claim.scale.location if claim.scale else "metric_default",
        subject_entity_id=claim.subject_entity_id,
        raw_text=claim.raw_text,
        row_label=claim.row_label,
        column_label=claim.column_label,
        passage_id=claim.passage_id,
        row_index=metadata.get("row_index"),
        value_column_index=metadata.get("value_column_index"),
        evidence_resolves=claim.passage_id in passages,
        is_instant=claim.period.is_instant,
    )


def _match_record(gold: GoldObservation, claim: LaneClaim, passages) -> MatchRecord:
    """One gold observation beside its claim, with `evaluation.compare` as the only judge.

    The six booleans are asked for, never recomputed. A second implementation here would let
    the per-observation verdicts and the aggregate accuracies disagree without any test being
    able to notice, which is exactly what a report exists to prevent.
    """
    return MatchRecord(
        metric_id=gold.metric_id,
        period_key=gold.period_key,
        expected_value=gold.value,
        emitted_value=claim.value,
        expected_unit=gold.unit,
        emitted_unit=claim.unit,
        expected_scale=gold.scale_applied,
        emitted_scale=claim.scale.scale if claim.scale else "units",
        expected_subject_entity_id=gold.subject_entity_id,
        emitted_subject_entity_id=claim.subject_entity_id,
        emitted_row_index=claim.extractor_metadata.get("metric_label_row_index"),
        emitted_column_label=claim.column_label,
        **compare(gold.as_gold_claim(), claim,
                  evidence_resolves=claim.passage_id in passages),
    )


def _issue_record(issue) -> IssueRecord:
    return IssueRecord(
        code=issue.code,
        row_index=issue.row_index,
        raw_label=issue.raw_label,
        normalized_label=issue.normalized_label,
        candidate_concept_ids=tuple(issue.candidate_concept_ids),
        required_lane=issue.required_lane,
        column_index=issue.column_index,
        detail=issue.detail,
    )


def _keys(entries) -> tuple[ObservationKey, ...]:
    """`evaluation.py` reports these as `metric@period` strings; split them back apart."""
    parsed = []
    for entry in entries:
        metric_id, _, period_key = entry.partition("@")
        parsed.append(ObservationKey(metric_id=metric_id, period_key=period_key))
    return tuple(sorted(parsed, key=lambda k: (k.metric_id, k.period_key)))


def _scores(evaluation) -> dict[str, float]:
    scores = {
        "metric_recall": round(evaluation.metric_recall, RATIO_DIGITS),
        # Never called precision. Each case names a deliberate subset of its table, so an
        # unmatched claim is usually a right answer the case did not list.
        "matched_over_emitted": round(evaluation.matched_over_emitted, RATIO_DIGITS),
    }
    for dimension in MATCH_DIMENSIONS:
        scores[f"{dimension}_accuracy"] = round(evaluation.accuracy(dimension), RATIO_DIGITS)
    return scores


def _corpus_identity(passages: dict[str, dict], catalog_root: Path | None) -> dict[str, Any]:
    root = catalog_root or load_config(REPO_ROOT).catalog_root
    documents = root / "documents.jsonl"
    document_lines = [l for l in documents.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {
        "documents": len(document_lines),
        "passages": len(passages),
        "table_passages": sum(1 for row in passages.values() if row.get("table_id")),
        "documents_sha256": hashlib.sha256(documents.read_bytes()).hexdigest(),
    }


def _head_commit() -> str:
    """The commit the report describes. The only field allowed to move between runs.

    Raises rather than substituting a placeholder. A report whose identity is `"unknown"` is
    worse than no report: it commits cleanly, passes every test, and cannot be tied to the
    code that produced it. Failing here is recoverable — pass `implementation_commit`
    explicitly if the report is genuinely being built outside a checkout.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            f"cannot determine the commit this report describes (git rev-parse HEAD in "
            f"{REPO_ROOT} failed: {error}); pass implementation_commit explicitly"
        ) from error
    commit = completed.stdout.strip()
    if not commit:
        raise RuntimeError(
            "git rev-parse HEAD produced no commit; pass implementation_commit explicitly")
    return commit



def build_report(*, catalog_root: Path | None = None,
                 implementation_commit: str | None = None) -> TableLaneReport:
    """The whole thing, from disk: cases, corpus, ontology, lane, scores."""
    return run_table_lane(
        load_table_cases(),
        passages=load_passages(catalog_root),
        ontology=load_ontology(),
        catalog_root=catalog_root,
        implementation_commit=implementation_commit,
    )


# -- rendering -----------------------------------------------------------------------------------


# The two corrections review 2026-08-02 required of this committed artifact. Declared data
# rather than prose alone, because the Markdown is read by people and the JSON by step 13.
SCORE_CAVEATS: tuple[dict[str, str], ...] = (
    {"dimension": "ambiguity",
     "state": "not scored",
     "detail": "V1_CLAIM_EXTRACTION §4.0 names eight dimensions scored independently and "
               "this report scores seven of them: metric_recall, matched_over_emitted and "
               "the six per-match dimensions. No score, per case or in total, is named for "
               "ambiguity. The table cases do declare expected abstentions and the "
               "lane does record AMBIGUOUS_ALIAS, so it is measurable here and simply is not "
               "measured. narrative_lane_v1.json scores all of them and states each "
               "denominator."},
    {"dimension": "period_accuracy",
     "state": "tautology, not a measurement",
     "detail": "evaluation.evaluate_case matches a claim to a gold claim on (metric_id, "
               "period key) and compare then tests claim.period.key == gold.period_key, "
               "which is true by construction for every matched pair. The number cannot fall "
               "however the lane dates a figure, and the same holds for the metric half of "
               "metric_recall's numerator. Left at 1.000 rather than re-matched on metric "
               "alone: changing the matching key would change which pairs every other "
               "dimension is computed over, in a committed artifact, to move a number rather "
               "than to fix a lane. The real period evidence in this report is the 21 "
               "wrongly dated emitted observations on kpi-table-q4-2023-earnings, which sit "
               "outside the gold set and are scored by nothing."},
)


def render_json(report: TableLaneReport) -> str:
    payload = {
        "benchmark_version": report.benchmark_version,
        "ontology_definition_hash": report.ontology_definition_hash,
        "normalization_corpus": report.normalization_corpus,
        "implementation_commit": report.implementation_commit,
        "lane": report.lane,
        "cases": [_case_json(case) for case in report.cases],
        "totals": report.totals,
        "unsupported_table_shapes": [dict(s) for s in report.unsupported_table_shapes],
        "known_misses": [dict(m) for m in report.known_misses],
        # What this report's own numbers do not say, in the machine-readable half as well as
        # in the prose. A caveat that lives only in Markdown is a caveat no consumer reads
        # *(added 2026-08-02, review)*.
        "score_caveats": [dict(entry) for entry in SCORE_CAVEATS],
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _case_json(case: CaseReport) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "source_file": case.source_file,
        "passage_id": case.passage_id,
        "document_id": case.document_id,
        "document_type": case.document_type,
        "table_id": case.table_id,
        "block_ids": list(case.block_ids),
        "counts": case.counts,
        "scores": case.scores,
        "gold": [_gold_json(g) for g in case.gold],
        "emitted": [_emitted_json(e) for e in case.emitted],
        "matched": [_match_json(m) for m in case.matched],
        "missed": [{"metric_id": k.metric_id, "period_key": k.period_key}
                   for k in case.missed],
        "unmatched_emitted": [{"metric_id": k.metric_id, "period_key": k.period_key}
                              for k in case.unmatched_emitted],
        "issues": [_issue_json(i) for i in case.issues],
        "issues_by_code": case.issues_by_code,
    }


def _gold_json(gold: GoldObservation) -> dict[str, Any]:
    return {
        "metric_id": gold.metric_id, "period_key": gold.period_key, "value": gold.value,
        "unit": gold.unit, "scale_applied": gold.scale_applied, "currency": gold.currency,
        "subject_entity_id": gold.subject_entity_id, "column_label": gold.column_label,
    }


def _emitted_json(emitted: EmittedObservation) -> dict[str, Any]:
    return {
        "metric_id": emitted.metric_id, "period_key": emitted.period_key,
        "value": emitted.value, "unit": emitted.unit, "currency": emitted.currency,
        "scale": emitted.scale, "scale_source": emitted.scale_source,
        "subject_entity_id": emitted.subject_entity_id, "raw_text": emitted.raw_text,
        "row_label": emitted.row_label, "column_label": emitted.column_label,
        "is_instant": emitted.is_instant,
        "evidence": {
            "passage_id": emitted.passage_id,
            "row_index": emitted.row_index,
            "value_column_index": emitted.value_column_index,
            "resolves": emitted.evidence_resolves,
        },
    }


def _match_json(match: MatchRecord) -> dict[str, Any]:
    return {
        "metric_id": match.metric_id, "period_key": match.period_key,
        "expected": {"value": match.expected_value, "unit": match.expected_unit,
                     "scale": match.expected_scale,
                     "subject_entity_id": match.expected_subject_entity_id},
        "emitted": {"value": match.emitted_value, "unit": match.emitted_unit,
                    "scale": match.emitted_scale,
                    "subject_entity_id": match.emitted_subject_entity_id},
        "dimensions": {"value_ok": match.value_ok, "unit_ok": match.unit_ok,
                       "scale_ok": match.scale_ok, "period_ok": match.period_ok,
                       "subject_ok": match.subject_ok, "evidence_ok": match.evidence_ok},
    }


def _issue_json(issue: IssueRecord) -> dict[str, Any]:
    return {
        "code": issue.code, "row_index": issue.row_index, "raw_label": issue.raw_label,
        "normalized_label": issue.normalized_label,
        "candidate_concept_ids": list(issue.candidate_concept_ids),
        "required_lane": issue.required_lane, "column_index": issue.column_index,
        "detail": issue.detail,
    }


def render_markdown(report: TableLaneReport) -> str:
    totals = report.totals
    scores = totals["scores"]
    lines: list[str] = [
        "# Table lane \u2014 benchmark v1 results",
        "",
        "Generated by `python -m benchmarks.extraction.v1 report`. Every number here is "
        "produced by driving `DeterministicTableClaimLane` over the reviewed table cases and "
        "scoring it with `extraction/stages/tables/evaluation.py`. **If this report "
        "disagrees with the lane, the report is wrong** \u2014 changing extraction to improve a "
        "number here is a gate failure, not a fix.",
        "",
        "## Identity",
        "",
        "| | |",
        "| --- | --- |",
        f"| benchmark version | `{report.benchmark_version}` |",
        f"| implementation commit | `{report.implementation_commit}` |",
        f"| lane | `{report.lane['name']}` v`{report.lane['version']}` |",
        f"| ontology definition hash | `{report.ontology_definition_hash}` |",
        f"| normalized documents | {report.normalization_corpus['documents']} |",
        f"| normalized passages | {report.normalization_corpus['passages']} |",
        f"| table passages | {report.normalization_corpus['table_passages']} |",
        f"| `documents.jsonl` sha256 | `{report.normalization_corpus['documents_sha256']}` |",
        "",
        "## Totals",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        f"| cases | {totals['cases']} |",
        f"| gold observations | {totals['gold_observations']} |",
        f"| emitted observations | {totals['emitted_observations']} |",
        f"| matched observations | {totals['matched_observations']} |",
        f"| missed observations | {totals['missed_observations']} |",
        f"| metric recall | **{scores['metric_recall']:.3f}** |",
        f"| matched/emitted | {scores['matched_over_emitted']:.3f} "
        "(not precision \u2014 gold is a deliberate subset) |",
    ]
    for dimension in MATCH_DIMENSIONS:
        lines.append(
            f"| {dimension} accuracy | **{scores[f'{dimension}_accuracy']:.3f}** |")
    lines += [
        f"| issues | {totals['issues']} |",
        "",
        "Issues by code: "
        + (", ".join(f"`{code}` {count}"
                     for code, count in totals["issues_by_code"].items()) or "none"),
        "",
        "`matched/emitted` is reported for run-to-run movement and is never an accuracy. "
        "Each case names a deliberate subset of its table's claims \u2014 the Q1 2025 KPI table "
        "has 10 gold entries and 45 correct claims \u2014 so an unmatched claim is usually a "
        "right answer the case did not list (benchmark README, \u00a74.0).",
        "",
        "**Seven numbers, not eight, and `ambiguity` is the one missing** *(corrected "
        "2026-08-02)*. \u00a74.0 names eight dimensions scored independently \u2014 metric identity, "
        "value, unit, scale, period, subject, evidence, **ambiguity** \u2014 and this report has "
        "never scored the last of them. What it reports is `metric_recall`, "
        "`matched_over_emitted` and the six per-match dimensions above; the string "
        "\"ambiguity\" appears in `table_lane_v1.json` zero times. The table cases do declare "
        "expected abstentions and this lane does record `AMBIGUOUS_ALIAS`, so the dimension "
        "is measurable here; it is not measured, and the gap is stated rather than filled, "
        "because filling it is a change to a committed artifact this stage is not making. "
        "`narrative_lane_v1.{json,md}` scores it, and states its denominator.",
        "",
        "**`period accuracy` is a tautology and is not a measurement** *(corrected "
        "2026-08-02)*. `evaluation.evaluate_case` matches a predicted claim to a gold claim "
        "on `(metric_id, period key)`, and `compare` then tests "
        "`claim.period.key == gold.period_key` \u2014 true by construction for every matched "
        "pair, so the number cannot fall however the lane dates a figure. The same is true of "
        "the metric half of `metric_recall`'s numerator. Both reports run through this code "
        "and both carry the same 1.000; `narrative_lane_v1.md` says so too. The real period "
        "evidence in this report is the paragraph below, where 21 emitted observations carry "
        "the wrong period and the score reads 1.000 beside them. Not repaired by re-matching "
        "on metric alone: that would change which pairs are compared, and therefore every "
        "other dimension in a committed artifact, to fix a number rather than a lane.",
        "",
        "**Unscored is not the same as verified.** The "
        f"{sum(len(c.unmatched_emitted) for c in report.cases)} unmatched keys below are "
        "outside the gold set and nothing checks them, and at least one table shows what "
        "that hides: "
        "`kpi-table-q4-2023-earnings` puts 5 `Three Months Ended` columns beside 2 "
        "`Year Ended December 31,` columns, which \u00a78a.7 names as the shape whose duration "
        "groups are assigned positionally. The fallback reads `December 31, 2022` as FY2022 "
        "and `March 31, 2023` / `June 30, 2023` as twelve-month durations \u2014 21 emitted "
        "observations with the wrong period, none of them gold, so the case still scores "
        "1.000 on every dimension *(verified 2026-08-01 from this run's own output)*. \u00a78a.7 "
        "says a wrong answer of this kind is \"caught rather than emitted\" because the "
        "period type is checked; that holds for instant-versus-duration and not for a "
        "duration read as the wrong duration, which is what happens here.",
        "",
        "## Per-case results",
        "",
        "| Case | Category | Gold | Emitted | Matched | Missed | Recall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in report.cases:
        lines.append(
            f"| [`{case.case_id}`](#{_anchor(case.case_id)}) | {case.category} | "
            f"{case.counts['gold']} | {case.counts['emitted']} | {case.counts['matched']} | "
            f"{case.counts['missed']} | {case.scores['metric_recall']:.3f} |")
    lines.append("")

    for case in report.cases:
        lines += _case_markdown(case)

    lines += _issues_markdown(report)
    lines += _known_misses_markdown(report)
    lines += _unsupported_shapes_markdown(report)
    return "\n".join(lines) + "\n"


def _case_markdown(case: CaseReport) -> list[str]:
    scores = case.scores
    lines = [
        f"## {case.case_id}",
        "",
        f"- category: `{case.category}` (from `cases/{case.source_file}`)",
        f"- passage: `{case.passage_id}`",
        f"- document: `{case.document_id}` (`{case.document_type}`)",
        f"- table: `{case.table_id}`",
        f"- gold {case.counts['gold']} \u00b7 emitted {case.counts['emitted']} \u00b7 "
        f"matched {case.counts['matched']} \u00b7 missed {case.counts['missed']} \u00b7 "
        f"issues {case.counts['issues']}",
        "- scores: " + " \u00b7 ".join(
            f"{name} {value:.3f}" for name, value in sorted(scores.items())),
        "",
        "### Gold observations",
        "",
        # Row and column identify the cell the claim was read from, so a WRONG verdict can be
        # taken back to the filing without opening the JSON. Blank for a miss: there is no
        # claim, and the row the lane refused is in the Issues table below.
        "| Metric | Period | Expected | Emitted | Unit | Scale | Row | Column | Verdict |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    matched = {(m.metric_id, m.period_key): m for m in case.matched}
    for gold in case.gold:
        match = matched.get((gold.metric_id, gold.period_key))
        if match is None:
            lines.append(
                f"| `{gold.metric_id}` | {gold.period_key} | {_number(gold.value)} | "
                f"{EM_DASH} | {gold.unit} | {gold.scale_applied} | {EM_DASH} | {EM_DASH} | "
                "**MISSED** |")
            continue
        verdict = "ok" if match.all_ok else "**WRONG**: " + ", ".join(
            name for name, ok in (
                ("value", match.value_ok), ("unit", match.unit_ok),
                ("scale", match.scale_ok), ("period", match.period_ok),
                ("subject", match.subject_ok), ("evidence", match.evidence_ok))
            if not ok)
        unit = (match.emitted_unit if match.unit_ok
                else f"{match.expected_unit} / {match.emitted_unit}")
        scale = (match.emitted_scale if match.scale_ok
                 else f"{match.expected_scale} / {match.emitted_scale}")
        lines.append(
            f"| `{gold.metric_id}` | {gold.period_key} | {_number(match.expected_value)} | "
            f"{_number(match.emitted_value)} | {unit} | {scale} | "
            f"{_index(match.emitted_row_index)} | {_cell(match.emitted_column_label)} | "
            f"{verdict} |")
    lines.append("")

    gold_keys = {(g.metric_id, g.period_key) for g in case.gold}
    unlisted = [e for e in case.emitted if (e.metric_id, e.period_key) not in gold_keys]
    if unlisted:
        lines += [
            f"### Emitted but not listed in gold ({len(unlisted)})",
            "",
            "**Not errors.** The case names a deliberate subset of the table, so a correct "
            "claim for a period the case did not list lands here (§4.0). Not verified "
            "either — nothing in the benchmark scores these; see the note under Totals.",
            "",
            f"The JSON's `unmatched_emitted` holds {len(case.unmatched_emitted)} entries "
            "rather than these " + str(len(unlisted)) + ": `evaluation.py` reports distinct "
            "`(metric_id, period_key)` keys, and a table that reports one metric under one "
            "period in two rows collapses to one key.",
            "",
            "| Metric | Period | Value | Unit | Scale | Row label | Column |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for emitted in unlisted:
            lines.append(
                f"| `{emitted.metric_id}` | {emitted.period_key} | "
                f"{_number(emitted.value)} | {emitted.unit} | {emitted.scale} | "
                f"{_cell(emitted.row_label)} | {_cell(emitted.column_label)} |")
        lines.append("")

    if case.issues:
        lines += [
            f"### Issues ({case.counts['issues']})",
            "",
            "| Code | Row | Label | Detail |",
            "| --- | --- | --- | --- |",
        ]
        for issue in case.issues:
            lines.append(
                f"| `{issue.code}` | {_index(issue.row_index)} | "
                f"{_cell(issue.raw_label)} | {_cell(issue.detail)} |")
        lines.append("")
    return lines


def _issues_markdown(report: TableLaneReport) -> list[str]:
    grouped: dict[str, list[tuple[str, IssueRecord]]] = collections.defaultdict(list)
    for case in report.cases:
        for issue in case.issues:
            grouped[issue.code].append((case.case_id, issue))
    lines = ["## Issues by code", ""]
    if not grouped:
        return lines + ["No issues.", ""]
    for code in sorted(grouped):
        entries = sorted(grouped[code],
                         key=lambda e: (e[0], e[1].row_index if e[1].row_index is not None
                                        else -1, e[1].raw_label or ""))
        lines += [
            f"### `{code}` \u2014 {len(entries)}",
            "",
            "| Case | Row | Label | Candidates | Required lane |",
            "| --- | --- | --- | --- | --- |",
        ]
        for case_id, issue in entries:
            candidates = ", ".join(f"`{c}`" for c in issue.candidate_concept_ids) or EM_DASH
            lines.append(
                f"| `{case_id}` | {_index(issue.row_index)} | "
                f"{_cell(issue.raw_label)} | {candidates} | "
                f"{issue.required_lane or EM_DASH} |")
        lines.append("")
    return lines


def _known_misses_markdown(report: TableLaneReport) -> list[str]:
    lines = [
        f"## Known misses ({len(report.known_misses)})",
        "",
        "| Case | Metric | Period | Row | Row label | Issue emitted instead |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for miss in report.known_misses:
        lines.append(
            f"| `{miss['case_id']}` | `{miss['metric_id']}` | {miss['period_key']} | "
            f"{miss['row_index']} | {miss['row_label']} | `{miss['issue_code']}` over "
            + ", ".join(f"`{c}`" for c in miss["candidate_concept_ids"]) + " |")
    lines.append("")
    for reason in sorted({m["reason"] for m in report.known_misses}):
        lines += [reason, ""]
    return lines


def _unsupported_shapes_markdown(report: TableLaneReport) -> list[str]:
    lines = [
        "## Table shapes not yet supported",
        "",
        "Stated rather than discovered later (V1_CLAIM_EXTRACTION.md \u00a78a.7). Where this run "
        "contradicted \u00a78a.7, the behaviour column states what the lane does, not what the "
        "plan says \u2014 see `observed_in`.",
        "",
        "| Shape | Behaviour | Observed in |",
        "| --- | --- | --- |",
    ]
    for shape in report.unsupported_table_shapes:
        observed = shape.get("observed_in")
        lines.append(f"| {shape['shape']} | {_cell(shape['behaviour'])} | "
                     + (f"`{observed}`" if observed else EM_DASH) + " |")
    lines.append("")
    return lines


def _anchor(case_id: str) -> str:
    return case_id.lower().replace(" ", "-")


def _cell(text: str | None) -> str:
    """Markdown table cells cannot carry a pipe or a newline."""
    if not text:
        return EM_DASH
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _ratio(hit: int, total: int) -> float:
    """1.0 for an empty denominator: nothing was asked for and nothing was missed.

    Rounded to `RATIO_DIGITS` before serialisation, because an unrounded ratio makes a
    committed report sensitive to the last bits of a float division and turns a rerun on
    another machine into a diff.
    """
    return 1.0 if total == 0 else round(hit / total, RATIO_DIGITS)


def _index(value: int | None) -> str:
    return EM_DASH if value is None else str(value)


def _number(value: float) -> str:
    """Thousands-separated, and 38556000.0 written as 38,556,000 rather than 38,556,000.0."""
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    return f"{value:,}"


# -- writing --------------------------------------------------------------------------------------


def write_reports(report: TableLaneReport, directory: Path = REPORTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{REPORT_STEM}.json"
    markdown_path = directory / f"{REPORT_STEM}.md"
    json_path.write_text(render_json(report), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


# Published because the other reports in this directory need the same corpus identity, the
# same commit, the same rounding and the same Markdown escaping. A second implementation of any
# of them is how two reports in one directory start describing different runs, or rounding to
# different places, while both look authoritative. `ratio` lives here rather than in
# `scope_runner` because `RATIO_DIGITS` does: the constant and the only function that applies
# it belong together.
#
# `gold_period_key` joined them at step 11. It is the rule that turns a case file's
# `instant_date` or `period_start`/`period_end` into the key claims are matched on, and the
# narrative report matches on exactly the same key or the two reports are not comparable.
corpus_identity = _corpus_identity
head_commit = _head_commit
ratio = _ratio
anchor = _anchor
cell = _cell
gold_period_key = _gold_period_key
