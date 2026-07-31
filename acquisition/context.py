"""Composition root.

The single place concrete stage implementations are named. Replacing a stage later means
changing this file (or adding a small config switch here), not touching `pipeline.py`.

This is a plain runtime context, not a dependency-injection framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import PipelineStage
from .core.config import AppConfig, load_config
from .core.manifests import ManifestRepository
from .core.sec_client import SecClient
from .core.storage import LocalRawArtifactStore
from .pipeline import AcquisitionPipeline
from .stages.catalog import JsonlCatalogStage
from .stages.discover import SecDiscoverStage
from .stages.download import LocalDownloadStage
from .stages.report import MarkdownReportStage
from .stages.resolve import SgmlResolveStage
from .stages.verify import CorpusVerifyStage


@dataclass
class AcquisitionContext:
    """Everything a command needs, wired together.

    One `SecClient` is shared by every stage. That matters beyond tidiness: the client owns
    the rate limiter, so a shared instance keeps the request rate correct across stage
    boundaries instead of per-stage.
    """

    config: AppConfig
    client: SecClient
    store: LocalRawArtifactStore
    manifests: ManifestRepository
    discover: PipelineStage
    resolve: PipelineStage
    download: PipelineStage
    catalog: PipelineStage
    verify: PipelineStage
    report: PipelineStage
    pipeline: AcquisitionPipeline

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "AcquisitionContext":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def build_acquisition_context(
    root: Path | None = None,
    *,
    config: AppConfig | None = None,
    client: SecClient | None = None,
    store: LocalRawArtifactStore | None = None,
    manifests: ManifestRepository | None = None,
    discover: PipelineStage | None = None,
    resolve: PipelineStage | None = None,
    download: PipelineStage | None = None,
    catalog: PipelineStage | None = None,
    verify: PipelineStage | None = None,
    report: PipelineStage | None = None,
) -> AcquisitionContext:
    """Build the runtime context, constructing the current implementations by default.

    Every collaborator can be overridden, which is how tests inject fakes and how an
    alternative implementation would be introduced.
    """
    config = config or load_config(root)
    client = client or SecClient(config.fetch.http)
    store = store or LocalRawArtifactStore(config.raw_root, config.tmp_root)
    manifests = manifests or ManifestRepository(config.manifests_root)

    # --- concrete implementation selection happens here and nowhere else ---
    discover = discover or SecDiscoverStage(config, client, manifests)
    resolve = resolve or SgmlResolveStage(config, client, manifests)
    download = download or LocalDownloadStage(config, client, store, manifests)
    catalog = catalog or JsonlCatalogStage(config, store)
    verify = verify or CorpusVerifyStage(config, store, manifests)
    report = report or MarkdownReportStage(config, manifests)

    return AcquisitionContext(
        config=config,
        client=client,
        store=store,
        manifests=manifests,
        discover=discover,
        resolve=resolve,
        download=download,
        catalog=catalog,
        verify=verify,
        report=report,
        pipeline=AcquisitionPipeline(
            discover=discover,
            resolve=resolve,
            download=download,
            catalog=catalog,
            verify=verify,
            report=report,
        ),
    )
