"""Canonical normalization: ParsedDocument -> NormalizedDocument + Passages.

The section tree is *inferred*, because this corpus contains zero semantic headings. That
makes inference fallible by construction, so this module is deliberately defensive:

- known filing labels (`Part I`, `Item 1A`, `Risk Factors`) beat any styling signal;
- heading candidates inside data tables are rejected;
- impossible level jumps are normalized;
- every heading carries a confidence;
- when confidence is low across a document, the structure degrades to a flat synthetic
  root rather than inventing nesting.

A shallow true structure is more useful to extraction than a deep false one.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...core.config import AppConfig
from ...core.identity import (
    block_id as make_block_id,
    derivation_id as make_derivation_id,
    document_id_from_artifact_id,
    section_id as make_section_id,
    sha256_bytes,
    sha256_text,
)
from ...core.models import (
    ContentBlock,
    NormalizationIssue,
    NormalizedDocument,
    NormalizedSection,
    ObjectStats,
    ParsedBlock,
    ParsedDocument,
    ParserComparison,
    SelectedArtifact,
    SourceLocator,
    TableBlock,
)
from ...core.runmeta import NORMALIZER_VERSION
from ...core.storage import NormalizedDocumentStore
from ..parse.public import (
    MODE_COMPARISON,
    MODE_FALLBACK,
    TRIGGER_COVERAGE,
    TRIGGER_EMPTY,
    TRIGGER_NO_BLOCKS,
    TRIGGER_SOURCE_HASH,
    TRIGGER_TABLE_CONTENT_LOSS,
    DocumentParser,
    ParserError,
)
from ..parse.source_profile import SourceProfile, profile_source
from ..passages.public import PassageStrategy
from .public import NormalizeRequest, NormalizeResult

SYNTHETIC_ROOT_TITLE = "(document)"

_DOCUMENT_TYPE_BY_ROLE = {
    "ex99-01": "earnings_release",
    "ex99-02": "shareholder_letter",
    "ex99-03": "supplemental",
    "ex21-01": "structured_exhibit",
}


class DocumentNormalizer:
    """Builds one NormalizedDocument from one ParsedDocument. Pure apart from config."""

    version = NORMALIZER_VERSION

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        headings = config.normalization.headings
        self._label_patterns = [re.compile(p, re.IGNORECASE) for p in headings.filing_label_patterns]
        self._min_weight = headings.min_font_weight
        self._max_heading_chars = headings.max_heading_chars
        self._min_mean_confidence = headings.min_mean_confidence

    # -- public ------------------------------------------------------------------------

    def normalize(
        self,
        artifact: SelectedArtifact,
        parsed: ParsedDocument,
        *,
        source_sha256: str,
        passage_strategy_name: str,
        passage_strategy_version: str,
        fallback_from: str | None = None,
        fallback_reason: str | None = None,
    ) -> NormalizedDocument:
        doc_id = document_id_from_artifact_id(artifact.artifact_id)

        headings = self._classify_headings(parsed.blocks)
        flat = self._should_flatten(headings)
        sections, section_of_block = self._build_sections(doc_id, parsed.blocks, headings, flat)
        blocks = self._build_blocks(doc_id, artifact, parsed.blocks, sections, section_of_block)

        for section in sections:
            section.block_ids = [b.block_id for b in blocks if b.section_id == section.section_id]
            section.char_count = sum(b.char_count for b in blocks if b.section_id == section.section_id)

        content_text = "\n".join(b.text for b in blocks if b.text)
        flags = self._flags(artifact, parsed, blocks, headings, flat)

        derivation = make_derivation_id(
            selection_policy_version=artifact.policy_version,
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            normalizer_version=self.version,
            passage_strategy_name=passage_strategy_name,
            passage_strategy_version=passage_strategy_version,
            config_hash=self._config.config_hash(),
            source_content_sha256=source_sha256,
        )

        return NormalizedDocument(
            document_id=doc_id,
            source_artifact_id=artifact.artifact_id,
            filing_id=artifact.filing_id,
            cik=artifact.cik,
            cik10=artifact.cik10,
            company_name=artifact.company_name,
            tickers=artifact.tickers,
            form=artifact.form,
            form_sanitized=artifact.form_sanitized,
            filing_date=artifact.filing_date,
            report_date=artifact.report_date,
            acceptance_datetime=artifact.acceptance_datetime,
            items=artifact.items,
            is_amendment=artifact.is_amendment,
            amends_accession=artifact.amends_accession,
            artifact_role=artifact.role,
            exhibit_type=artifact.exhibit_type,
            original_filename=artifact.original_filename,
            source_url=artifact.source_url,
            source_path=artifact.source_path,
            document_type=_document_type(artifact),
            title=parsed.title,
            sections=sections,
            blocks=blocks,
            related_xbrl_artifact_ids=artifact.related_xbrl_artifact_ids,
            selection_policy_version=artifact.policy_version,
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            normalizer_version=self.version,
            config_hash=self._config.config_hash(),
            source_content_sha256=source_sha256,
            content_sha256=sha256_text(content_text),
            derivation_id=derivation,
            parser_fallback_from=fallback_from,
            parser_fallback_reason=fallback_reason,
            flags=flags,
            stats=_stats(blocks, sections, artifact.size_bytes),
        )

    # -- heading inference -------------------------------------------------------------

    def _classify_headings(self, blocks: list[ParsedBlock]) -> dict[int, tuple[int, float, str]]:
        """block_sequence -> (level, confidence, heading_source).

        Filing labels win outright: `Part I` and `Item 1A` are stable across every markup
        era in this corpus, while font weight is not.
        """
        out: dict[int, tuple[int, float, str]] = {}
        for block in blocks:
            if block.block_type == "table":
                continue
            text = block.text.strip()
            if not text or len(text) > self._max_heading_chars:
                continue

            label_level = self._filing_label_level(text)
            if label_level is not None:
                out[block.block_sequence] = (label_level, 0.95, "filing_label")
                continue

            if block.block_type != "heading":
                continue

            weight = block.source_style.font_weight
            signals = 0
            if weight is not None and weight >= self._min_weight:
                signals += 1
            if block.source_style.font_size and block.source_style.font_size >= 11:
                signals += 1
            if not text.endswith((".", ";", ",")):
                signals += 1
            if len(text) <= 80:
                signals += 1
            confidence = min(0.9, 0.15 * signals + 0.2)
            level = block.level if isinstance(block.level, int) and 1 <= block.level <= 6 else 3
            out[block.block_sequence] = (level, confidence, "styled_text")
        return out

    def _filing_label_level(self, text: str) -> int | None:
        for index, pattern in enumerate(self._label_patterns):
            if pattern.search(text):
                # `Part ...` is the outermost anchor; everything else sits one level in.
                return 1 if index == 0 else 2
        return None

    def _should_flatten(self, headings: dict[int, tuple[int, float, str]]) -> bool:
        """Degrade honestly when the styling signal is too weak to trust."""
        if not headings:
            return True
        mean = sum(c for _, c, _ in headings.values()) / len(headings)
        return mean < self._min_mean_confidence

    def _build_sections(
        self,
        doc_id: str,
        blocks: list[ParsedBlock],
        headings: dict[int, tuple[int, float, str]],
        flat: bool,
    ) -> tuple[list[NormalizedSection], dict[int, str]]:
        """Sections in document order, with a synthetic root that always exists."""
        root = NormalizedSection(
            section_id=make_section_id(doc_id, 0),
            section_sequence=0,
            title=SYNTHETIC_ROOT_TITLE,
            level=0,
            parent_section_id=None,
            heading_block_id=None,
            heading_path=[],
            heading_source="synthetic_root",
            heading_confidence=1.0,
        )
        sections = [root]
        section_of_block: dict[int, str] = {}
        current = root
        stack: list[NormalizedSection] = [root]

        for block in blocks:
            heading = None if flat else headings.get(block.block_sequence)
            if heading is not None:
                level, confidence, source = heading
                # Never let depth grow by more than one step: an H1 followed by an H4
                # becomes H2, so a styling accident cannot invent three levels.
                max_level = min(level, stack[-1].level + 1)
                level = max(1, max_level)

                while len(stack) > 1 and stack[-1].level >= level:
                    stack.pop()
                parent = stack[-1]
                section = NormalizedSection(
                    section_id=make_section_id(doc_id, len(sections)),
                    section_sequence=len(sections),
                    title=block.text.strip(),
                    level=level,
                    parent_section_id=parent.section_id,
                    heading_block_id=make_block_id(doc_id, block.block_sequence),
                    heading_path=[*parent.heading_path, block.text.strip()],
                    heading_source=source,  # type: ignore[arg-type]
                    heading_confidence=confidence,
                )
                sections.append(section)
                stack.append(section)
                current = section
            section_of_block[block.block_sequence] = current.section_id
        return sections, section_of_block

    # -- blocks ------------------------------------------------------------------------

    def _build_blocks(
        self,
        doc_id: str,
        artifact: SelectedArtifact,
        parsed_blocks: list[ParsedBlock],
        sections: list[NormalizedSection],
        section_of_block: dict[int, str],
    ) -> list[ContentBlock]:
        by_id = {s.section_id: s for s in sections}
        blocks: list[ContentBlock] = []
        table_index = 0

        for parsed in parsed_blocks:
            section_id = section_of_block.get(parsed.block_sequence, sections[0].section_id)
            section = by_id[section_id]

            table_block = None
            locator_table_index = None
            if parsed.table is not None:
                table_block = TableBlock(
                    table_index=table_index,
                    rows=parsed.table.rows,
                    header_rows=parsed.table.header_rows,
                    n_rows=parsed.table.n_rows,
                    n_cols=parsed.table.n_cols,
                    has_merged_cells=parsed.table.has_merged_cells,
                    markdown=parsed.table.markdown,
                    plain_text=parsed.table.plain_text,
                    table_kind=parsed.table.table_kind,
                    kind_confidence=parsed.table.kind_confidence,
                    caption=parsed.table.caption,
                    flags=parsed.table.flags,
                )
                locator_table_index = table_index
                table_index += 1

            block_type = parsed.block_type
            if block_type == "heading" and parsed.block_sequence not in section_of_block:
                block_type = "paragraph"

            blocks.append(
                ContentBlock(
                    block_id=make_block_id(doc_id, parsed.block_sequence),
                    block_sequence=parsed.block_sequence,
                    section_id=section_id,
                    block_type=block_type,
                    text=parsed.text,
                    char_count=len(parsed.text),
                    level=parsed.level,
                    block_sha256=sha256_text(parsed.text),
                    locator=SourceLocator(
                        artifact_id=artifact.artifact_id,
                        block_sequence=parsed.block_sequence,
                        tag_name=parsed.tag_name,
                        fragment_sha256=parsed.fragment_sha256,
                        heading_path=list(section.heading_path),
                        char_start=0,
                        char_end=len(parsed.text),
                        table_index=locator_table_index,
                    ),
                    table=table_block,
                )
            )
        return blocks

    # -- flags -------------------------------------------------------------------------

    def _flags(
        self,
        artifact: SelectedArtifact,
        parsed: ParsedDocument,
        blocks: list[ContentBlock],
        headings: dict[int, tuple[int, float, str]],
        flat: bool,
    ) -> list[str]:
        flags: list[str] = []
        text_chars = sum(b.char_count for b in blocks)
        image_count = sum(1 for b in blocks if b.block_type == "image")

        if artifact.decision == "needs_review":
            flags.append("needs_review")
        if flat:
            flags.append("hierarchy_uncertain")
        if text_chars < self._config.normalization.image_heavy.min_text_chars and image_count:
            flags.append("requires_image_processing")
        elif image_count and text_chars / image_count < self._config.normalization.image_heavy.min_chars_per_image:
            flags.append("requires_image_processing")
        if artifact.size_bytes and text_chars / artifact.size_bytes < 0.02:
            flags.append("low_text_yield")
        if any(b.table and "wide_table" in b.table.flags for b in blocks):
            flags.append("wide_table")
        return flags


def _document_type(artifact: SelectedArtifact) -> str:
    if artifact.artifact_kind == "primary":
        return "narrative_primary"
    role = (artifact.role or "").lower()
    if role in _DOCUMENT_TYPE_BY_ROLE:
        return _DOCUMENT_TYPE_BY_ROLE[role]
    if role.startswith("ex10-"):
        return "material_agreement"
    if role.startswith(("ex3-", "ex4-", "ex19-")):
        return "governance"
    return "other"


def _stats(blocks: list[ContentBlock], sections: list[NormalizedSection], size_bytes: int) -> ObjectStats:
    tables = [b.table for b in blocks if b.table is not None]
    char_count = sum(b.char_count for b in blocks)
    return ObjectStats(
        char_count=char_count,
        block_count=len(blocks),
        section_count=len(sections),
        table_count=len(tables),
        data_table_count=sum(1 for t in tables if t.table_kind == "data"),
        layout_table_count=sum(1 for t in tables if t.table_kind == "layout"),
        image_count=sum(1 for b in blocks if b.block_type == "image"),
        max_section_depth=max((s.level for s in sections), default=0),
        text_yield_pct=round(100 * char_count / size_bytes, 2) if size_bytes else 0.0,
    )


# --------------------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------------------


class CanonicalNormalizeStage:
    """Per-artifact: parse, normalize, build passages, finalize atomically.

    Owns the loop because a document is only meaningful once all three collaborators have
    contributed; a half-normalized document has no reason to exist.
    """

    name = "normalize"

    def __init__(
        self,
        config: AppConfig,
        default_parser: DocumentParser,
        fallback_parser: DocumentParser,
        passage_strategy: PassageStrategy,
        store: NormalizedDocumentStore,
    ) -> None:
        self._config = config
        self._default = default_parser
        self._fallback = fallback_parser
        self._passages = passage_strategy
        self._store = store
        self._normalizer = DocumentNormalizer(config)

    def run(self, request: NormalizeRequest) -> NormalizeResult:
        result = NormalizeResult(attempted=len(request.artifacts))
        total = len(request.artifacts)

        for index, artifact in enumerate(request.artifacts, start=1):
            if request.progress:
                request.progress(index, total, artifact.artifact_id)
            try:
                self._process(artifact, request, result)
            except Exception as exc:  # noqa: BLE001 - recorded per artifact, run continues
                result.issues.append(
                    NormalizationIssue(
                        issue_id=f"{request.run_id}:{artifact.artifact_id}",
                        run_id=request.run_id,
                        artifact_id=artifact.artifact_id,
                        severity="error",
                        code="PARSE_FAILED",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
        return result

    # -- one artifact ------------------------------------------------------------------

    def _process(self, artifact: SelectedArtifact, request: NormalizeRequest, result: NormalizeResult) -> None:
        raw = self._read(artifact)
        actual_sha = sha256_bytes(raw)
        if actual_sha != artifact.source_sha256:
            raise ParserError(
                TRIGGER_SOURCE_HASH,
                f"source hash mismatch: catalog {artifact.source_sha256[:12]} vs disk {actual_sha[:12]}",
            )

        loss_cfg = self._config.normalization.parser.table_content_loss
        profile = profile_source(
            raw,
            probe_count=loss_cfg.probe_count,
            min_probe_length=loss_cfg.min_probe_length,
        )
        reference_chars = profile.text_chars

        if request.mode == MODE_COMPARISON:
            result.comparisons.append(self._compare(artifact, raw))

        primary = self._fallback if request.mode == MODE_FALLBACK else self._default
        secondary = None if request.mode == MODE_FALLBACK else self._fallback

        parsed, used_fallback, trigger = self._parse_with_policy(
            artifact, raw, primary, secondary, reference_chars, profile
        )
        if used_fallback:
            result.fallbacks.append(
                {"artifact_id": artifact.artifact_id, "parser": parsed.parser_name, "trigger": trigger or ""}
            )
            result.issues.append(
                NormalizationIssue(
                    issue_id=f"{request.run_id}:{artifact.artifact_id}:fallback",
                    run_id=request.run_id,
                    artifact_id=artifact.artifact_id,
                    severity="warning",
                    code="PARSER_FALLBACK",
                    detail=f"fell back to {parsed.parser_name} after {trigger}",
                )
            )

        document = self._normalizer.normalize(
            artifact,
            parsed,
            source_sha256=actual_sha,
            passage_strategy_name=self._passages.name,
            passage_strategy_version=self._passages.version,
            fallback_from=primary.name if used_fallback else None,
            fallback_reason=trigger if used_fallback else None,
        )
        passages, excluded = self._passages.build(document)
        document.stats.passage_count = len(passages)

        self._store.finalize(document, passages)
        result.documents.append(document)

        # `needs_review` is deliberately absent: the select stage owns that finding, so it
        # is recorded even for artifacts that never reach the parser.
        for flag, code in (
            ("hierarchy_uncertain", "HIERARCHY_UNCERTAIN"),
            ("requires_image_processing", "IMAGE_HEAVY"),
            ("low_text_yield", "LOW_TEXT_YIELD"),
        ):
            if flag in document.flags:
                result.issues.append(
                    NormalizationIssue(
                        issue_id=f"{request.run_id}:{artifact.artifact_id}:{flag}",
                        run_id=request.run_id,
                        artifact_id=artifact.artifact_id,
                        document_id=document.document_id,
                        severity="warning",
                        code=code,
                        detail=f"{document.original_filename}: {flag}",
                    )
                )

    def _parse_with_policy(
        self,
        artifact: SelectedArtifact,
        raw: bytes,
        primary: DocumentParser,
        secondary: DocumentParser | None,
        reference_chars: int,
        profile: SourceProfile,
    ) -> tuple[ParsedDocument, bool, str | None]:
        """Parse with `primary`, falling back only on an approved hard failure."""
        try:
            parsed = primary.parse(artifact, raw)
            trigger = self._hard_failure(parsed, reference_chars, profile)
            if trigger is None:
                return parsed, False, None
        except ParserError as exc:
            trigger = exc.trigger
        except Exception as exc:  # noqa: BLE001 - any parser crash is a hard failure
            trigger = f"parser_exception:{type(exc).__name__}"

        if secondary is None:
            raise ParserError(trigger, f"{primary.name} failed and no fallback is configured")
        return secondary.parse(artifact, raw), True, trigger

    def _hard_failure(
        self, parsed: ParsedDocument, reference_chars: int, profile: SourceProfile
    ) -> str | None:
        if not parsed.blocks:
            return TRIGGER_EMPTY
        if not any(b.text for b in parsed.blocks):
            return TRIGGER_NO_BLOCKS
        threshold = self._config.normalization.parser.min_source_coverage
        if reference_chars and parsed.text_chars / reference_chars < threshold:
            return TRIGGER_COVERAGE
        if self._table_content_lost(parsed, profile):
            return TRIGGER_TABLE_CONTENT_LOSS
        return None

    def _table_content_lost(self, parsed: ParsedDocument, profile: SourceProfile) -> bool:
        """All three conditions, never any one alone.

        A low detection ratio by itself is normal: SEC filings use tables for page layout
        and that text usually survives as prose. Only when substantial table structure
        exists, the parser recognized almost none of it, AND sampled cell content is
        actually missing from the output is this content loss.
        """
        cfg = self._config.normalization.parser.table_content_loss
        if profile.table_count < cfg.min_source_tables:
            return False
        parser_tables = sum(1 for b in parsed.blocks if b.table is not None)
        if profile.detected_ratio(parser_tables) > cfg.max_detected_ratio:
            return False
        parsed_text = " ".join(b.text for b in parsed.blocks)
        return profile.probe_coverage(parsed_text) < cfg.min_probe_coverage

    def _compare(self, artifact: SelectedArtifact, raw: bytes) -> ParserComparison:
        def measure(parser: DocumentParser):
            try:
                doc = parser.parse(artifact, raw)
                return (
                    len(doc.blocks), doc.text_chars,
                    sum(1 for b in doc.blocks if b.block_type == "table"),
                    sum(1 for b in doc.blocks if b.block_type == "heading"),
                    [],
                )
            except Exception as exc:  # noqa: BLE001
                return 0, 0, 0, 0, [f"{parser.name} failed: {type(exc).__name__}: {exc}"]

        d_blocks, d_chars, d_tables, d_heads, d_notes = measure(self._default)
        f_blocks, f_chars, f_tables, f_heads, f_notes = measure(self._fallback)
        return ParserComparison(
            artifact_id=artifact.artifact_id,
            default_parser=self._default.name,
            fallback_parser=self._fallback.name,
            default_blocks=d_blocks, fallback_blocks=f_blocks,
            default_chars=d_chars, fallback_chars=f_chars,
            default_tables=d_tables, fallback_tables=f_tables,
            default_headings=d_heads, fallback_headings=f_heads,
            char_ratio=round(f_chars / d_chars, 3) if d_chars else 0.0,
            notes=[*d_notes, *f_notes],
        )

    def _read(self, artifact: SelectedArtifact) -> bytes:
        path = Path(self._config.acquisition_raw_root) / artifact.source_path
        if not path.is_file():
            raise FileNotFoundError(f"Raw artifact missing: {path}")
        return path.read_bytes()
