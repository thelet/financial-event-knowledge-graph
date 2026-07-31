"""Composition root.

The single place concrete implementations are named. Replacing a parser, a passage
strategy, or a whole stage means changing this file, not the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core.config import AppConfig, load_config
from .core.manifests import ManifestRepository
from .core.storage import LocalNormalizedStore
from .pipeline import NormalizationPipeline
from .stages.catalog import CatalogStage, JsonlCatalogStage
from .stages.issues import IssueLogStage, RunIssueLogStage
from .stages.normalize import CanonicalNormalizeStage, NormalizeStage
from .stages.parse import DocumentParser, LxmlDocumentParser, SecHtmlDocumentParser
from .stages.passages import PassageStrategy, SectionAwarePassageStrategy
from .stages.report import MarkdownReportStage, ReportStage
from .stages.select import CatalogSelectStage, SelectStage
from .stages.verify import CorpusVerifyStage, VerifyStage

_PARSERS = {"sec_html": SecHtmlDocumentParser, "lxml": LxmlDocumentParser}


@dataclass
class NormalizationContext:
    config: AppConfig
    store: LocalNormalizedStore
    manifests: ManifestRepository
    default_parser: DocumentParser
    fallback_parser: DocumentParser
    passage_strategy: PassageStrategy
    select: SelectStage
    normalize: NormalizeStage
    issues: IssueLogStage
    catalog: CatalogStage
    verify: VerifyStage
    report: ReportStage
    pipeline: NormalizationPipeline


def build_parser(name: str, config: AppConfig) -> DocumentParser:
    """Small factory: the near-term need is exactly two implementations."""
    if name not in _PARSERS:
        raise ValueError(f"Unknown parser {name!r}; available: {sorted(_PARSERS)}")
    tables = config.normalization.tables
    kwargs = dict(
        max_heading_chars=config.normalization.headings.max_heading_chars,
        wide_table_columns=tables.wide_table_columns,
        layout_max_rows=tables.layout_max_rows,
        layout_max_cols=tables.layout_max_cols,
    )
    if name == "lxml":
        kwargs["min_font_weight"] = config.normalization.headings.min_font_weight
    return _PARSERS[name](**kwargs)


def build_normalization_context(
    root: Path | None = None,
    *,
    run_id: str = "run",
    config: AppConfig | None = None,
    store: LocalNormalizedStore | None = None,
    manifests: ManifestRepository | None = None,
    default_parser: DocumentParser | None = None,
    fallback_parser: DocumentParser | None = None,
    passage_strategy: PassageStrategy | None = None,
    select: SelectStage | None = None,
    normalize: NormalizeStage | None = None,
    issues: IssueLogStage | None = None,
    catalog: CatalogStage | None = None,
    verify: VerifyStage | None = None,
    report: ReportStage | None = None,
) -> NormalizationContext:
    config = config or load_config(root)
    store = store or LocalNormalizedStore(config.normalized_root, config.tmp_root, run_id)
    manifests = manifests or ManifestRepository(config.manifests_root)

    # --- concrete implementation selection happens here and nowhere else ---
    default_parser = default_parser or build_parser(config.normalization.parser.default, config)
    fallback_parser = fallback_parser or build_parser(config.normalization.parser.fallback, config)
    passages_cfg = config.normalization.passages
    passage_strategy = passage_strategy or SectionAwarePassageStrategy(
        target_chars=passages_cfg.target_chars,
        max_chars=passages_cfg.max_chars,
        min_chars=passages_cfg.min_chars,
        excluded_block_types=passages_cfg.excluded_block_types,
    )

    select = select or CatalogSelectStage(config, manifests)
    normalize = normalize or CanonicalNormalizeStage(
        config, default_parser, fallback_parser, passage_strategy, store
    )
    issues = issues or RunIssueLogStage(config)
    catalog = catalog or JsonlCatalogStage(config, store, manifests)
    verify = verify or CorpusVerifyStage(config, store, manifests)
    report = report or MarkdownReportStage(config, store)

    return NormalizationContext(
        config=config, store=store, manifests=manifests,
        default_parser=default_parser, fallback_parser=fallback_parser,
        passage_strategy=passage_strategy,
        select=select, normalize=normalize, issues=issues, catalog=catalog,
        verify=verify, report=report,
        pipeline=NormalizationPipeline(
            select, normalize, issues, catalog, verify, report
        ),
    )
