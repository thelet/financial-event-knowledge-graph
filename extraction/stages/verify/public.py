"""Public contract for the checks a run performs on itself.

Five checks (STAGE_13 §5), each with an id, a result and its findings, so the manifest and the
report state the same five outcomes rather than two prose summaries that could disagree.

**A check that cannot fail is not a check.** Each of the five has a test that violates its
condition and asserts it goes red — STAGE_13 §8.8 — which is the only evidence that a green
result means anything. `passed` is therefore computed from `failures`, never set.

Two of the five are advisory and say so. `conflicting_duplicates` and `forbidden_lanes` fail a
run; `evidence_resolution` and `ontology_validation` fail a run; `duplicate_identities` fails a
run. Warnings are counted separately and never flip a result, because a warning that failed a
run would stop anyone recording one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EVIDENCE_RESOLUTION = "evidence_resolution"
ONTOLOGY_VALIDATION = "ontology_validation"
FORBIDDEN_LANES = "forbidden_lanes"
DUPLICATE_IDENTITIES = "duplicate_identities"
CONFLICTING_DUPLICATES = "conflicting_duplicates"

CHECKS: tuple[str, ...] = (
    EVIDENCE_RESOLUTION, ONTOLOGY_VALIDATION, FORBIDDEN_LANES,
    DUPLICATE_IDENTITIES, CONFLICTING_DUPLICATES,
)

# What each check is for, in one line, rendered into the report beside its result so a reader
# does not have to hold five definitions in their head. Stated once, here, because a second
# spelling in the renderer is a second thing to keep true.
DESCRIPTIONS: dict[str, str] = {
    EVIDENCE_RESOLUTION:
        "every evidence passage_id exists in the corpus and every quoted span occurs in it",
    ONTOLOGY_VALIDATION:
        "every claim through validate_claims; V1 §11 criterion 1 requires zero errors",
    FORBIDDEN_LANES:
        "no observation carries a source lane its metric forbids, and no xbrl-first metric "
        "carries a normalized-lane observation",
    DUPLICATE_IDENTITIES:
        "no two distinct payloads share an id, in any catalog",
    CONFLICTING_DUPLICATES:
        "no (metric, subject, period, lane, passage) is reported twice with different values",
}


@dataclass(frozen=True)
class CheckFinding:
    code: str
    detail: str
    claim_id: str | None = None


@dataclass
class CheckResult:
    check: str
    failures: list[CheckFinding] = field(default_factory=list)
    warnings: list[CheckFinding] = field(default_factory=list)
    # What the check looked at. A "0 failures" over a denominator of 0 is not the same answer
    # as "0 failures" over 5,412, and a report that printed only the first number would let an
    # empty run look verified.
    examined: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_row(self) -> dict:
        return {
            "check": self.check,
            "description": DESCRIPTIONS[self.check],
            "passed": self.passed,
            "examined": self.examined,
            "failures": len(self.failures),
            "warnings": len(self.warnings),
            "failures_by_code": _census(self.failures),
            "warnings_by_code": _census(self.warnings),
        }


@dataclass
class VerificationResult:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    def by_id(self, check: str) -> CheckResult:
        for result in self.checks:
            if result.check == check:
                return result
        raise KeyError(check)

    def as_rows(self) -> list[dict]:
        return [check.as_row() for check in self.checks]


def _census(findings: list[CheckFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
    return dict(sorted(counts.items()))
