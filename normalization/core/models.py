"""Canonical normalized models.

Schemas from plans/normalization/NORMALIZED_MODELS.md. Three representations are kept
strictly apart:

    parser-native   ->  canonical in-memory  ->  persisted schema

No parser type appears in any model here. That is the rule that lets sec-parser be replaced
without touching anything downstream.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

Decision = Literal["include", "exclude", "needs_review"]
TableKind = Literal["data", "layout", "mixed", "unknown"]
HeadingSource = Literal["styled_text", "filing_label", "synthetic_root"]
Severity = Literal["error", "warning", "info"]

BLOCK_TYPES = (
    "heading",
    "paragraph",
    "list_item",
    "table",
    "table_caption",
    "footnote",
    "page_header",
    "page_number",
    "image",
    "other",
)


# --------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------


class SelectedArtifact(BaseModel):
    """One acquired artifact with its selection decision.

    Produced for *every* acquired artifact, included or not, so nothing is silently
    dropped. Only artifacts with decision == "include" reach the parser.
    """

    artifact_id: str
    filing_id: str
    cik: int
    cik10: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)

    accession: str
    form: str
    form_sanitized: str
    filing_date: str
    report_date: str | None = None
    acceptance_datetime: str | None = None
    items: list[str] = Field(default_factory=list)
    is_amendment: bool = False
    amends_accession: str | None = None

    artifact_kind: str
    role: str | None = None
    exhibit_type: str | None = None
    description: str | None = None
    original_filename: str
    source_path: str
    source_url: str
    media_type: str
    file_extension: str
    size_bytes: int
    source_sha256: str

    decision: Decision
    reason_code: str
    matched_rule: str | None = None
    policy_version: str

    related_xbrl_artifact_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Parser output — ephemeral, never persisted
# --------------------------------------------------------------------------------------


class SourceStyle(BaseModel):
    """The only styling that crosses the adapter boundary.

    These five fields exist because heading inference needs them and for no other reason.
    A parser that cannot supply one leaves it null — never a passthrough dict.
    """

    font_weight: int | None = None
    font_size: float | None = None
    text_align: str | None = None
    display: str | None = None
    source_element_id: str | None = None


class ParsedTable(BaseModel):
    """`rows` is authoritative; `markdown` and `plain_text` are derived from it."""

    rows: list[list[str]] = Field(default_factory=list)
    header_rows: int = 0
    n_rows: int = 0
    n_cols: int = 0
    has_merged_cells: bool = False
    markdown: str = ""
    plain_text: str = ""
    table_kind: TableKind = "unknown"
    kind_confidence: float = 0.0
    caption: str | None = None
    flags: list[str] = Field(default_factory=list)


class ParsedBlock(BaseModel):
    block_sequence: int
    block_type: str
    text: str
    level: int | None = None
    tag_name: str | None = None
    fragment_sha256: str
    table: ParsedTable | None = None
    image_count: int = 0
    source_style: SourceStyle = Field(default_factory=SourceStyle)


class ParsedDocument(BaseModel):
    """Ephemeral. Lives in memory between the parse and normalize stages only."""

    artifact_id: str
    parser_name: str
    parser_version: str
    blocks: list[ParsedBlock] = Field(default_factory=list)
    title: str | None = None
    warnings: list[str] = Field(default_factory=list)
    parse_duration_ms: int = 0

    @property
    def text_chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)


# --------------------------------------------------------------------------------------
# Canonical normalized document
# --------------------------------------------------------------------------------------


class SourceLocator(BaseModel):
    """Provenance for one block.

    Byte offsets into the source file are deliberately absent: sec-parser cannot supply
    them reliably. `char_start`/`char_end` are offsets within the block's own normalized
    text, which is what quoting a passage exactly actually requires.
    """

    artifact_id: str
    block_sequence: int
    tag_name: str | None = None
    fragment_sha256: str
    heading_path: list[str] = Field(default_factory=list)
    char_start: int = 0
    char_end: int = 0
    table_index: int | None = None


class TableBlock(BaseModel):
    table_index: int
    rows: list[list[str]] = Field(default_factory=list)
    header_rows: int = 0
    n_rows: int = 0
    n_cols: int = 0
    has_merged_cells: bool = False
    markdown: str = ""
    plain_text: str = ""
    table_kind: TableKind = "unknown"
    kind_confidence: float = 0.0
    caption: str | None = None
    flags: list[str] = Field(default_factory=list)


class ContentBlock(BaseModel):
    block_id: str
    block_sequence: int
    section_id: str
    block_type: str
    text: str
    char_count: int
    level: int | None = None
    block_sha256: str
    locator: SourceLocator
    table: TableBlock | None = None


class NormalizedSection(BaseModel):
    section_id: str
    section_sequence: int
    title: str
    level: int
    parent_section_id: str | None = None
    heading_block_id: str | None = None
    block_ids: list[str] = Field(default_factory=list)
    char_count: int = 0
    heading_path: list[str] = Field(default_factory=list)
    heading_source: HeadingSource = "styled_text"
    heading_confidence: float = 0.0


class ObjectStats(BaseModel):
    char_count: int = 0
    block_count: int = 0
    section_count: int = 0
    passage_count: int = 0
    table_count: int = 0
    data_table_count: int = 0
    layout_table_count: int = 0
    image_count: int = 0
    max_section_depth: int = 0
    text_yield_pct: float = 0.0


class NormalizedDocument(BaseModel):
    """Authoritative per-document record.

    Carries deterministic derivation metadata ONLY. No run_id, no timestamp, no
    code_commit: those change on every run (or every unrelated commit) and would make
    byte-identical reruns impossible. Run identity lives in the run manifest, which
    records produced_document_ids.
    """

    schema_version: int = SCHEMA_VERSION

    document_id: str
    source_artifact_id: str
    filing_id: str

    cik: int
    cik10: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)

    form: str
    form_sanitized: str
    filing_date: str
    report_date: str | None = None
    acceptance_datetime: str | None = None
    items: list[str] = Field(default_factory=list)
    is_amendment: bool = False
    amends_accession: str | None = None

    artifact_role: str | None = None
    exhibit_type: str | None = None
    original_filename: str
    source_url: str
    source_path: str
    document_type: str

    title: str | None = None
    sections: list[NormalizedSection] = Field(default_factory=list)
    blocks: list[ContentBlock] = Field(default_factory=list)

    related_xbrl_artifact_ids: list[str] = Field(default_factory=list)

    selection_policy_version: str
    parser_name: str
    parser_version: str
    normalizer_version: str
    config_hash: str
    source_content_sha256: str
    content_sha256: str
    derivation_id: str

    # Which parser produced this document and why, when it was not the default. Both are
    # deterministic functions of the source and the configuration, so they do not break
    # byte-identical reruns.
    parser_fallback_from: str | None = None
    parser_fallback_reason: str | None = None

    flags: list[str] = Field(default_factory=list)
    stats: ObjectStats = Field(default_factory=ObjectStats)


# --------------------------------------------------------------------------------------
# Passages
# --------------------------------------------------------------------------------------


class Passage(BaseModel):
    passage_id: str
    passage_sequence: int
    document_id: str
    source_artifact_id: str
    filing_id: str

    # Denormalized filing context: extraction and evidence panels need company, form,
    # date and item codes with every passage, and a consumer forced to join three files
    # to display one quote will eventually skip the join.
    cik10: str
    company_name: str
    form: str
    filing_date: str
    report_date: str | None = None
    items: list[str] = Field(default_factory=list)
    artifact_role: str | None = None
    exhibit_type: str | None = None
    document_type: str
    source_url: str

    text: str
    char_count: int
    passage_kind: str
    section_id: str
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    table_id: str | None = None

    locators: list[SourceLocator] = Field(default_factory=list)
    content_sha256: str
    passage_strategy_name: str
    passage_strategy_version: str
    parser_name: str
    parser_version: str
    normalizer_version: str
    derivation_id: str
    flags: list[str] = Field(default_factory=list)


class ExcludedBlock(BaseModel):
    """A block the passage policy deliberately left out, with its reason.

    Required by the passage invariant: every passage-eligible block is in exactly one
    passage unless explicitly excluded, and every exclusion is recorded.
    """

    block_id: str
    block_type: str
    reason: str


# --------------------------------------------------------------------------------------
# Runs and issues
# --------------------------------------------------------------------------------------


class NormalizationIssue(BaseModel):
    issue_id: str
    run_id: str
    artifact_id: str
    document_id: str | None = None
    severity: Severity
    code: str
    detail: str


class NormalizationRun(BaseModel):
    run_id: str
    stage: str
    created_at: str
    finished_at: str | None = None
    config_hash: str
    selection_policy_version: str
    parser_name: str
    parser_version: str
    normalizer_version: str
    passage_strategy_name: str
    passage_strategy_version: str
    parser_mode: str
    code_commit: str | None = None
    python_version: str
    platform: str
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    produced_document_ids: list[str] = Field(default_factory=list)
    fallback_document_ids: list[dict[str, str]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SelectionManifest(BaseModel):
    """Authoritative selection outcome. Immutable, one per run.

    Cannot be derived from per-document files: it covers artifacts that were excluded and
    therefore have no document.
    """

    schema_version: int = SCHEMA_VERSION
    run_id: str
    created_at: str
    policy_version: str
    config_hash: str
    manifest_hash: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    artifacts: list[SelectedArtifact] = Field(default_factory=list)


class ParserComparison(BaseModel):
    """One artifact parsed by both implementations, for comparison mode."""

    artifact_id: str
    default_parser: str
    fallback_parser: str
    default_blocks: int
    fallback_blocks: int
    default_chars: int
    fallback_chars: int
    default_tables: int
    fallback_tables: int
    default_headings: int
    fallback_headings: int
    char_ratio: float
    notes: list[str] = Field(default_factory=list)


def model_dump_stable(model: BaseModel) -> dict[str, Any]:
    """JSON-mode dump. Used everywhere so serialization is identical across call sites."""
    return model.model_dump(mode="json")
