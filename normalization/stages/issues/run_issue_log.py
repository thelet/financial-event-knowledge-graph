"""Authoritative per-run issue log.

One owner for this file. Written atomically, before the catalog stage, so a single
pipeline run leaves the derived aggregate catalog correct without a second command.

A run with zero issues still writes an empty file: an absent file and a clean run would
otherwise be indistinguishable, and the catalog builder needs an unambiguous answer.
"""

from __future__ import annotations

from ...core.config import AppConfig
from ...core.models import NormalizationIssue
from ...utils.jsonl import write_jsonl
from .public import IssueLogRequest, IssueLogResult

ISSUE_FILE_SUFFIX = "-issues.jsonl"


class RunIssueLogStage:
    """Writes `normalization_runs/<run_id>-issues.jsonl`."""

    name = "write-issues"

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def run(self, request: IssueLogRequest) -> IssueLogResult:
        unique, duplicates = _dedupe(request.issues)
        rows = [issue.model_dump(mode="json") for issue in unique]

        # Deterministic order, so re-running a run id rewrites identical bytes and the
        # derived catalog stays byte-identical across rebuilds.
        rows.sort(key=lambda r: (r["artifact_id"], r["code"], r["issue_id"]))

        self._config.runs_root.mkdir(parents=True, exist_ok=True)
        path = self._config.runs_root / f"{request.run_id}{ISSUE_FILE_SUFFIX}"

        # write_jsonl stages to a sibling temp file and os.replace()s it, so a crash
        # mid-write cannot leave a truncated file that the catalog would read as complete.
        write_jsonl(path, rows)

        return IssueLogResult(
            path=path,
            issue_count=len(rows),
            duplicate_ids=duplicates,
            written=True,
        )


def _dedupe(issues: list[NormalizationIssue]) -> tuple[list[NormalizationIssue], list[str]]:
    """Collapse repeated issue ids within a run.

    Issue ids are run-scoped, so a rebuild or a resumed run rewrites the same file rather
    than accumulating copies. Duplicates within one run would mean two stages claimed the
    same finding, which is reported rather than silently merged.
    """
    seen: set[str] = set()
    unique: list[NormalizationIssue] = []
    duplicates: list[str] = []
    for issue in issues:
        if issue.issue_id in seen:
            duplicates.append(issue.issue_id)
            continue
        seen.add(issue.issue_id)
        unique.append(issue)
    return unique, duplicates
