"""Public contract for the VERIFY stage.

`Finding` and `VerificationReport` are the result payload, so they live here. The report
renders itself for the same reason `DownloadSummary` does: a type displaying its own
contents is not a separate concern.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

SEVERITY_ERROR = "ERROR"

SEVERITY_WARNING = "WARNING"

@dataclass
class Finding:
    severity: str
    check: str
    detail: str

@dataclass
class VerificationReport:
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def add(self, severity: str, check: str, detail: str) -> None:
        self.findings.append(Finding(severity, check, detail))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self, max_per_check: int = 8) -> str:
        lines = ["VERIFICATION REPORT", "=" * 60, ""]
        for key in sorted(self.stats):
            lines.append(f"  {key:<40} {self.stats[key]:>8}")
        lines.append("")

        if not self.findings:
            lines.append("No findings. Corpus is consistent.")
            return "\n".join(lines)

        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        for finding in self.findings:
            grouped[(finding.severity, finding.check)].append(finding.detail)

        for severity in (SEVERITY_ERROR, SEVERITY_WARNING):
            entries = {k: v for k, v in grouped.items() if k[0] == severity}
            if not entries:
                continue
            lines.append(f"{severity}S ({sum(len(v) for v in entries.values())})")
            lines.append("-" * 60)
            for (_, check), details in sorted(entries.items()):
                lines.append(f"  {check}  ({len(details)})")
                for detail in details[:max_per_check]:
                    lines.append(f"      {detail}")
                if len(details) > max_per_check:
                    lines.append(f"      ... and {len(details) - max_per_check} more")
            lines.append("")

        lines.append("PASS" if self.ok else "FAIL")
        return "\n".join(lines)

@dataclass(frozen=True)
class VerifyRequest:
    filings_run_id: str | None = None
    artifacts_run_id: str | None = None
    check_hashes: bool = True

@dataclass(frozen=True)
class VerifyResult:
    report: VerificationReport
    filings_run_id: str
    artifacts_run_id: str

    @property
    def ok(self) -> bool:
        return self.report.ok


@runtime_checkable
class VerifyStage(Protocol):
    """Any object that can check the corpus against its manifests."""

    @property
    def name(self) -> str: ...

    def run(self, request: VerifyRequest) -> VerifyResult: ...
