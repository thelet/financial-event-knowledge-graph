"""The full acquisition pipeline.

Owns stage ordering and abort semantics, and nothing else. It performs no HTTP and no
filesystem work, and knows nothing of any stage's internals -- it depends only on the
`PipelineStage` protocol and on each stage's public request/result types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .contracts import PipelineStage
from .stages.catalog import CatalogRequest, CatalogResult, CatalogStage
from .stages.discover import DiscoverRequest, DiscoverResult, DiscoverStage
from .stages.download import DownloadRequest, DownloadResult, DownloadStage
from .stages.report import ReportRequest, ReportResult, ReportStage
from .stages.resolve import ResolveRequest, ResolveResult, ResolveStage
from .stages.verify import VerifyRequest, VerifyResult, VerifyStage

# Stages run before report. Report runs afterwards and is not gated on its own success,
# matching the original `acquire` command exactly.
MAIN_STAGE_ORDER = ("discover", "resolve", "download", "build-catalog", "verify")


@dataclass(frozen=True)
class AcquisitionRequest:
    cik: str | None = None
    include_amendments: bool = True
    limit: int | None = None
    force: bool = False
    check_hashes: bool = True
    resolve_progress: Callable[..., None] | None = None
    download_progress: Callable[..., None] | None = None
    on_stage_start: Callable[[str], None] | None = None
    on_stage_finish: Callable[[str, object], None] | None = None


@dataclass
class AcquisitionResult:
    discover: DiscoverResult | None = None
    resolve: ResolveResult | None = None
    download: DownloadResult | None = None
    catalog: CatalogResult | None = None
    verify: VerifyResult | None = None
    report: ReportResult | None = None
    failed_stage: str | None = None
    completed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed_stage is None


class AcquisitionPipeline:
    """discover -> resolve -> download -> build-catalog -> verify -> report."""

    def __init__(
        self,
        discover: DiscoverStage,
        resolve: ResolveStage,
        download: DownloadStage,
        catalog: CatalogStage,
        verify: VerifyStage,
        report: ReportStage,
    ) -> None:
        self._discover = discover
        self._resolve = resolve
        self._download = download
        self._catalog = catalog
        self._verify = verify
        self._report = report

    @property
    def stages(self) -> tuple[PipelineStage, ...]:
        return (
            self._discover,
            self._resolve,
            self._download,
            self._catalog,
            self._verify,
            self._report,
        )

    def run(self, request: AcquisitionRequest) -> AcquisitionResult:
        result = AcquisitionResult()

        def step(stage: PipelineStage, stage_request: object):
            """Run one stage and record the outcome. The caller decides whether to stop."""
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

        result.discover = step(
            self._discover,
            DiscoverRequest(cik=request.cik, include_amendments=request.include_amendments),
        )
        if not result.discover.ok:
            return result

        result.resolve = step(
            self._resolve,
            ResolveRequest(
                cik=request.cik,
                filings_run_id=result.discover.run_id,
                limit=request.limit,
                progress=request.resolve_progress,
            ),
        )
        if not result.resolve.ok:
            return result

        result.download = step(
            self._download,
            DownloadRequest(
                cik=request.cik,
                artifacts_run_id=result.resolve.run_id,
                limit=request.limit,
                force=request.force,
                progress=request.download_progress,
            ),
        )
        if not result.download.ok:
            return result

        result.catalog = step(self._catalog, CatalogRequest())
        if not result.catalog.ok:
            return result

        result.verify = step(
            self._verify,
            VerifyRequest(
                filings_run_id=result.discover.run_id,
                artifacts_run_id=result.resolve.run_id,
                check_hashes=request.check_hashes,
            ),
        )
        if not result.verify.ok:
            return result

        result.report = step(
            self._report, ReportRequest(artifacts_run_id=result.resolve.run_id)
        )
        return result
