"""Section-aware passage strategy.

Rejected alternatives and why:

- **Fixed-size overlapping chunks.** Overlap duplicates evidence, so the same sentence
  appears in two passages under two ids — corrosive for an evidence-linked graph.
- **Semantic/embedding grouping.** Requires an embedding model, making normalization
  depend on a provider.

Invariant: every passage-eligible block is assigned to exactly one passage unless the
policy explicitly excludes it, and every exclusion records a reason.
"""

from __future__ import annotations

from ...core.identity import passage_id as make_passage_id, sha256_text
from ...core.models import (
    ContentBlock,
    ExcludedBlock,
    NormalizedDocument,
    NormalizedSection,
    Passage,
)
from ...utils.text import split_sentences
from .public import EMPTY_BLOCK, HEADING_AS_METADATA, PAGE_FURNITURE, POLICY_EXCLUDED

STRATEGY_NAME = "section_aware"
STRATEGY_VERSION = "1.0.0"

_FURNITURE = {"page_header", "page_number"}


class SectionAwarePassageStrategy:
    name = STRATEGY_NAME
    version = STRATEGY_VERSION

    def __init__(
        self,
        *,
        target_chars: int = 1500,
        max_chars: int = 4000,
        min_chars: int = 200,
        excluded_block_types: list[str] | None = None,
    ) -> None:
        self._target = target_chars
        self._max = max_chars
        self._min = min_chars
        self._excluded = set(excluded_block_types or ["page_header", "page_number", "heading", "image"])

    def build(self, document: NormalizedDocument) -> tuple[list[Passage], list[ExcludedBlock]]:
        sections = {s.section_id: s for s in document.sections}
        passages: list[Passage] = []
        excluded: list[ExcludedBlock] = []
        pending: list[ContentBlock] = []
        current_section: str | None = None

        def flush() -> None:
            nonlocal pending
            if pending and current_section:
                passages.extend(
                    self._emit(document, sections[current_section], pending, len(passages))
                )
            pending = []

        for block in document.blocks:
            reason = self._exclusion_reason(block)
            if reason is not None:
                excluded.append(
                    ExcludedBlock(block_id=block.block_id, block_type=block.block_type, reason=reason)
                )
                continue

            # Never cross a section boundary.
            if current_section is not None and block.section_id != current_section:
                flush()
            current_section = block.section_id

            # A table is always its own passage: splitting a financial table makes it
            # unreadable, and merging it with prose makes both harder to quote.
            if block.block_type == "table":
                flush()
                current_section = block.section_id
                passages.extend(
                    self._emit(document, sections[block.section_id], [block], len(passages), kind="table")
                )
                continue

            # Flush before the run would exceed the hard maximum, so only a single
            # over-long block ever needs splitting.
            if pending and sum(b.char_count for b in pending) + block.char_count > self._max:
                flush()
                current_section = block.section_id
            pending.append(block)
            if sum(b.char_count for b in pending) >= self._target:
                flush()
                current_section = block.section_id
        flush()

        for index, passage in enumerate(passages):
            passage.passage_sequence = index
            passage.passage_id = make_passage_id(document.document_id, index)
        return passages, excluded

    # -- helpers -----------------------------------------------------------------------

    def _exclusion_reason(self, block: ContentBlock) -> str | None:
        if not block.text.strip() and block.block_type != "table":
            return EMPTY_BLOCK
        if block.block_type in _FURNITURE:
            return PAGE_FURNITURE
        if block.block_type == "heading":
            return HEADING_AS_METADATA
        if block.block_type in self._excluded:
            return POLICY_EXCLUDED
        return None

    def _emit(
        self,
        document: NormalizedDocument,
        section: NormalizedSection,
        blocks: list[ContentBlock],
        start_index: int,
        kind: str = "narrative",
    ) -> list[Passage]:
        """One or more passages from a run of blocks inside a single section."""
        text = "\n\n".join(b.text for b in blocks if b.text)
        if kind == "table" and blocks[0].table is not None:
            text = blocks[0].table.markdown or blocks[0].table.plain_text

        if len(text) <= self._max:
            return [self._passage(document, section, blocks, text, start_index, kind)]

        # Over-long: split on sentence boundaries, never mid-word. A table is never split.
        if kind == "table":
            passage = self._passage(document, section, blocks, text, start_index, kind)
            passage.flags.append("oversize_table_passage")
            return [passage]

        out: list[Passage] = []
        buffer = ""
        consumed = 0
        for sentence in split_sentences(text):
            if buffer and len(buffer) + len(sentence) + 1 > self._max:
                out.append(self._fragment(document, section, blocks, buffer.strip(),
                                          consumed, start_index + len(out), kind))
                consumed += len(buffer)
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}".strip()
        if buffer.strip():
            out.append(self._fragment(document, section, blocks, buffer.strip(),
                                      consumed, start_index + len(out), kind))
        return out

    def _fragment(
        self,
        document: NormalizedDocument,
        section: NormalizedSection,
        blocks: list[ContentBlock],
        text: str,
        offset: int,
        index: int,
        kind: str,
    ) -> Passage:
        """A slice of an over-long run, attributed to only the blocks it overlaps."""
        spans: list[tuple[ContentBlock, int, int]] = []
        cursor = 0
        for block in blocks:
            start, end = cursor, cursor + block.char_count
            spans.append((block, start, end))
            cursor = end + 2  # the "\n\n" join between blocks
        lo, hi = offset, offset + len(text)
        overlapping = [(b, s, e) for b, s, e in spans if s < hi and e > lo]
        if not overlapping:
            overlapping = [spans[0]]

        passage = self._passage(document, section, [b for b, _, _ in overlapping],
                                text, index, kind)
        passage.locators = [
            b.locator.model_copy(update={
                "char_start": max(0, lo - s),
                "char_end": min(b.char_count, hi - s),
            })
            for b, s, _ in overlapping
        ]
        return passage

    def _passage(
        self,
        document: NormalizedDocument,
        section: NormalizedSection,
        blocks: list[ContentBlock],
        text: str,
        index: int,
        kind: str,
    ) -> Passage:
        flags: list[str] = []
        if len(text) < self._min:
            flags.append("short_passage")
        table_block = next((b for b in blocks if b.table is not None), None)
        return Passage(
            passage_id=make_passage_id(document.document_id, index),
            passage_sequence=index,
            document_id=document.document_id,
            source_artifact_id=document.source_artifact_id,
            filing_id=document.filing_id,
            cik10=document.cik10,
            company_name=document.company_name,
            form=document.form,
            filing_date=document.filing_date,
            report_date=document.report_date,
            items=list(document.items),
            artifact_role=document.artifact_role,
            exhibit_type=document.exhibit_type,
            document_type=document.document_type,
            source_url=document.source_url,
            text=text,
            char_count=len(text),
            passage_kind=kind,
            section_id=section.section_id,
            heading_path=list(section.heading_path),
            block_ids=[b.block_id for b in blocks],
            table_id=table_block.block_id if table_block else None,
            locators=[b.locator for b in blocks],
            content_sha256=sha256_text(text),
            passage_strategy_name=self.name,
            passage_strategy_version=self.version,
            parser_name=document.parser_name,
            parser_version=document.parser_version,
            normalizer_version=document.normalizer_version,
            derivation_id=document.derivation_id,
            flags=flags,
        )
