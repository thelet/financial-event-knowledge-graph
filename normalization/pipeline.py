"""The full normalization pipeline.

Owns stage ordering and abort semantics, and nothing else: no HTTP, no filesystem work, no
knowledge of any stage's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .stages.catalog import CatalogRequest, CatalogResult, CatalogStage
from .stages.issues import IssueLogRequest, IssueLogResult, IssueLogStage
from .stages.normalize import NormalizeRequest, NormalizeResult, NormalizeStage
from .stages.report import ReportRequest, ReportResult, ReportStage
from .stages.select import SelectRequest, SelectResult, SelectStage
from .stages.verify import VerifyRequest, VerifyResult, VerifyStage

# Authoritative outputs are complete before anything derived is built:
#   documents/passages (normalize) -> issues (write-issues) -> catalogs -> verify -> report
# The issue file must precede the catalog stage, which rebuilds the aggregate issue
# catalog from it. Writing it afterwards left a fresh run with an empty aggregate.
STAGE_ORDER = ("select", "normalize", "write-issues", "build-catalog", "verify", "report")


@dataclass(frozen=True)
class NormalizationRequest:
    run_id: str
    artifact_ids: list[str] | None = None
    mode: str = "normal"
    check_hashes: bool = True
    review: bool = True
    normalize_progress: Callable[[int, int, str], None] | None = None
    on_stage_start: Callable[[str], None] | None = None
    on_stage_finish: Callable[[str, object], None] | None = None


@dataclass
class NormalizationResult:
    select: SelectResult | None = None
    normalize: NormalizeResult | None = None
    issues: IssueLogResult | None = None
    catalog: CatalogResult | None = None
    verify: VerifyResult | None = None
    report: ReportResult | None = None
    failed_stage: str | None = None
    completed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed_stage is None


class NormalizationPipeline:
    """select -> normalize -> write-issues -> build-catalog -> verify -> report."""

    def __init__(
        self,
        select: SelectStage,
        normalize: NormalizeStage,
        issues: IssueLogStage,
        catalog: CatalogStage,
        verify: VerifyStage,
        report: ReportStage,
    ) -> None:
        self._select = select
        self._normalize = normalize
        self._issues = issues
        self._catalog = catalog
        self._verify = verify
        self._report = report

    @property
    def stages(self) -> tuple:
        return (
            self._select, self._normalize, self._issues,
            self._catalog, self._verify, self._report,
        )

    def run(self, request: NormalizationRequest) -> NormalizationResult:
        result = NormalizationResult()

        def step(stage, stage_request):
            if request.on_stage_start:
                request.on_stage_start(stage.name)
            outcome = stage.run(stage_request)
            if request.on_stage_finish:
                request.on_stage_finish(stage.name, outcome)
            if outcome.ok:
                result.completed.append(stage.name)
            else:
                result.failed_stage = stage.name
            return outcome

        result.select = step(
            self._select, SelectRequest(run_id=request.run_id, artifact_ids=request.artifact_ids)
        )
        if not result.select.ok:
            return result

        result.normalize = step(
            self._normalize,
            NormalizeRequest(
                run_id=request.run_id,
                artifacts=result.select.for_processing,
                mode=request.mode,
                progress=request.normalize_progress,
            ),
        )
        if not result.normalize.ok:
            return result

        # Every authoritative input must exist before anything derived is built. Issues
        # come from every stage that produced them, not only from normalize.
        result.issues = step(
            self._issues,
            IssueLogRequest(
                run_id=request.run_id,
                issues=[*result.select.issues, *result.normalize.issues],
            ),
        )
        if not result.issues.ok:
            return result

        result.catalog = step(self._catalog, CatalogRequest(run_id=request.run_id))
        if not result.catalog.ok:
            return result

        result.verify = step(
            self._verify,
            VerifyRequest(selection_run_id=request.run_id, check_hashes=request.check_hashes),
        )
        if not result.verify.ok:
            return result

        result.report = step(
            self._report,
            ReportRequest(
                run_id=request.run_id,
                documents=result.normalize.documents,
                issues=result.normalize.issues,
                comparisons=result.normalize.comparisons,
                review=request.review,
            ),
        )
        return result
