"""Scoring the table lane against the reviewed table cases.

Eight dimensions, each scored independently, because a claim can be right about the metric
and wrong about the period and a blended score hides exactly that. Gold is passed in; this
module imports no benchmark, and an executable test enforces it for the whole package.

A predicted claim is matched to a gold claim on (metric_id, period key). Matching on value
would let a lane score well by emitting the right numbers under the wrong periods, which is
the failure mode ordinal column alignment exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.models import LaneClaim


@dataclass(frozen=True)
class GoldClaim:
    metric_id: str
    value: float
    unit: str
    period_key: str
    subject_entity_id: str
    scale_applied: str | None = None
    currency: str | None = None


@dataclass
class CaseResult:
    case_id: str
    passage_id: str
    gold: int = 0
    emitted: int = 0
    matched: int = 0
    value_ok: int = 0
    unit_ok: int = 0
    scale_ok: int = 0
    period_ok: int = 0
    subject_ok: int = 0
    evidence_ok: int = 0
    missing: tuple[str, ...] = ()
    # Emitted but not in gold. NOT an error list: the cases name a deliberate subset of each
    # table, so a correct claim for an unlisted period lands here too.
    unlisted: tuple[str, ...] = ()
    issues: dict[str, int] = field(default_factory=dict)


@dataclass
class TableLaneEvaluation:
    cases: list[CaseResult] = field(default_factory=list)
    issues_by_code: dict[str, int] = field(default_factory=dict)

    @property
    def gold_total(self) -> int:
        return sum(c.gold for c in self.cases)

    @property
    def emitted_total(self) -> int:
        return sum(c.emitted for c in self.cases)

    @property
    def matched_total(self) -> int:
        return sum(c.matched for c in self.cases)

    @property
    def metric_recall(self) -> float:
        return _ratio(self.matched_total, self.gold_total)

    @property
    def matched_over_emitted(self) -> float:
        """Deliberately not called precision.

        Precision is not measurable against this benchmark. Each case names a subset of its
        table's claims — the Q1 2025 KPI table has 10 gold entries and 45 correct claims —
        so an unmatched claim is usually a right answer the case did not list. Reported as a
        ratio for movement between runs, never as an accuracy.
        """
        return _ratio(self.matched_total, self.emitted_total)

    def accuracy(self, dimension: str) -> float:
        return _ratio(sum(getattr(c, f"{dimension}_ok") for c in self.cases),
                      self.matched_total)

    def as_report(self) -> str:
        lines = [
            f"candidate tables evaluated  {len(self.cases)}",
            f"gold observations           {self.gold_total}",
            f"emitted observations        {self.emitted_total}",
            f"metric recall               {self.metric_recall:.3f}",
            f"matched/emitted             {self.matched_over_emitted:.3f}"
            "   (not precision: gold is a deliberate subset)",
        ]
        for dimension in ("value", "unit", "scale", "period", "subject", "evidence"):
            lines.append(f"{dimension + ' accuracy':28}{self.accuracy(dimension):.3f}")
        if self.issues_by_code:
            lines.append("issues by code              " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.issues_by_code.items())))
        return "\n".join(lines)


def evaluate_case(
    case_id: str,
    passage_id: str,
    claims: list[LaneClaim],
    gold: list[GoldClaim],
    issues,
    *,
    resolves_evidence,
) -> CaseResult:
    result = CaseResult(case_id=case_id, passage_id=passage_id,
                        gold=len(gold), emitted=len(claims))
    by_key = {(c.metric_id, c.period.key): c for c in claims}

    missing: list[str] = []
    for want in gold:
        key = (want.metric_id, want.period_key)
        got = by_key.get(key)
        if got is None:
            missing.append(f"{want.metric_id}@{want.period_key}")
            continue
        result.matched += 1
        if _close(got.value, want.value):
            result.value_ok += 1
        if got.unit == want.unit:
            result.unit_ok += 1
        if _scale_of(got) == (want.scale_applied or "units"):
            result.scale_ok += 1
        if got.period.key == want.period_key:
            result.period_ok += 1
        if got.subject_entity_id == want.subject_entity_id:
            result.subject_ok += 1
        if resolves_evidence(got):
            result.evidence_ok += 1

    gold_keys = {(g.metric_id, g.period_key) for g in gold}
    result.missing = tuple(sorted(missing))
    result.unlisted = tuple(sorted(
        f"{m}@{p}" for (m, p) in by_key if (m, p) not in gold_keys))
    for issue in issues:
        result.issues[issue.code] = result.issues.get(issue.code, 0) + 1
    return result


def summarise(cases: list[CaseResult]) -> TableLaneEvaluation:
    evaluation = TableLaneEvaluation(cases=list(cases))
    for case in cases:
        for code, count in case.issues.items():
            evaluation.issues_by_code[code] = evaluation.issues_by_code.get(code, 0) + count
    return evaluation


def _scale_of(claim: LaneClaim) -> str:
    if claim.scale is None:
        return "units"
    return claim.scale.scale


def _close(a: float, b: float) -> bool:
    """Exact for integers, tolerant of float representation for percentages."""
    if a == b:
        return True
    return abs(float(a) - float(b)) <= max(abs(float(b)) * 1e-9, 1e-9)


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator
