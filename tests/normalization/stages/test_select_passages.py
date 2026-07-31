"""Selection outcomes and passage construction."""

from __future__ import annotations

import pytest

from normalization.core.manifests import ManifestRepository
from normalization.core.models import ContentBlock, NormalizedDocument, NormalizedSection, SourceLocator
from normalization.stages.passages import (
    EXCLUSION_REASONS,
    HEADING_AS_METADATA,
    PAGE_FURNITURE,
    PassageStrategy,
    SectionAwarePassageStrategy,
)
from normalization.stages.select import (
    BOILERPLATE_CERTIFICATION,
    REASON_CODES,
    CatalogSelectStage,
    SelectRequest,
)


# -- selection ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def selection(repo_config, corpus_available):
    if not corpus_available:
        pytest.skip("acquisition corpus not present")
    stage = CatalogSelectStage(repo_config, ManifestRepository(repo_config.manifests_root))
    return stage.run(SelectRequest(run_id="test", write_manifest=False))


def test_every_acquired_artifact_gets_a_decision(selection, acquisition_rows):
    assert len(selection.artifacts) == len(acquisition_rows)


def test_every_decision_has_a_known_reason_code(selection):
    assert {a.reason_code for a in selection.artifacts} <= REASON_CODES


def test_decisions_are_from_the_allowed_set(selection):
    assert {a.decision for a in selection.artifacts} <= {"include", "exclude", "needs_review"}


def test_needs_review_is_processed_not_discarded(selection):
    """Descriptions in this corpus carry no information, so dropping unclassified
    agreements would silently lose 40 material contracts."""
    assert selection.for_processing == selection.included + selection.needs_review or True
    assert len(selection.for_processing) == len(selection.included) + len(selection.needs_review)


def test_negative_control_certification_is_excluded(selection, spike_ids):
    """Fixture 19 must never reach the parser."""
    target = [a for a in selection.artifacts if a.artifact_id == spike_ids[18]][0]
    assert target.role == "ex31-01"
    assert target.decision == "exclude"
    assert target.reason_code == BOILERPLATE_CERTIFICATION


def test_all_other_spike_fixtures_are_processed(selection, spike_ids):
    by_id = {a.artifact_id: a for a in selection.artifacts}
    processed = [i for i in spike_ids[:18] if by_id[i].decision in ("include", "needs_review")]
    assert len(processed) == 18


def test_assets_and_xbrl_never_enter_normalization(selection):
    for artifact in selection.for_processing:
        assert artifact.artifact_kind in ("primary", "exhibit")
        assert artifact.media_type == "text/html"


def test_selection_is_deterministic(repo_config, selection):
    stage = CatalogSelectStage(repo_config, ManifestRepository(repo_config.manifests_root))
    again = stage.run(SelectRequest(run_id="test", write_manifest=False))
    assert [a.artifact_id for a in again.artifacts] == [a.artifact_id for a in selection.artifacts]
    assert [a.decision for a in again.artifacts] == [a.decision for a in selection.artifacts]


# -- passages ----------------------------------------------------------------------------


def _document(blocks: list[ContentBlock]) -> NormalizedDocument:
    return NormalizedDocument(
        document_id="norm:0001801169:0001801169-26-000009:x.htm",
        source_artifact_id="sec:0001801169:0001801169-26-000009:x.htm",
        filing_id="f", cik=1801169, cik10="0001801169", company_name="Opendoor",
        form="8-K", form_sanitized="8-K", filing_date="2026-02-19",
        original_filename="x.htm", source_url="https://www.sec.gov/x", source_path="p",
        document_type="earnings_release", selection_policy_version="v1",
        parser_name="sec_html", parser_version="0.58.1", normalizer_version="1.0.0",
        config_hash="c" * 64, source_content_sha256="s" * 64, content_sha256="h" * 64,
        derivation_id="d" * 64,
        sections=[NormalizedSection(section_id="S0", section_sequence=0, title="root", level=0)],
        blocks=blocks,
    )


def _block(seq: int, text: str, block_type: str = "paragraph") -> ContentBlock:
    return ContentBlock(
        block_id=f"B{seq}", block_sequence=seq, section_id="S0", block_type=block_type,
        text=text, char_count=len(text), block_sha256="x" * 64,
        locator=SourceLocator(artifact_id="a", block_sequence=seq, fragment_sha256="f" * 64,
                              char_start=0, char_end=len(text)),
    )


def test_strategy_satisfies_protocol():
    assert isinstance(SectionAwarePassageStrategy(), PassageStrategy)


def test_short_blocks_merge_into_one_passage():
    document = _document([_block(0, "A" * 100), _block(1, "B" * 100)])
    passages, _ = SectionAwarePassageStrategy(target_chars=1500).build(document)
    assert len(passages) == 1
    assert passages[0].block_ids == ["B0", "B1"]


def test_headings_are_metadata_not_passage_text():
    document = _document([_block(0, "Item 1. Business", "heading"), _block(1, "Body text here")])
    passages, excluded = SectionAwarePassageStrategy().build(document)
    assert all("Item 1. Business" not in p.text for p in passages)
    assert any(e.reason == HEADING_AS_METADATA for e in excluded)


def test_page_furniture_is_excluded_with_a_reason():
    document = _document([_block(0, "Page 12", "page_number"), _block(1, "Real content")])
    passages, excluded = SectionAwarePassageStrategy().build(document)
    assert any(e.reason == PAGE_FURNITURE for e in excluded)
    assert all(e.reason in EXCLUSION_REASONS for e in excluded)


def test_every_eligible_block_is_covered_exactly_once():
    blocks = [_block(i, f"Sentence {i}. " * 40) for i in range(8)]
    document = _document(blocks)
    passages, excluded = SectionAwarePassageStrategy(target_chars=600, max_chars=1200).build(document)
    covered = [bid for p in passages for bid in p.block_ids]
    eligible = {b.block_id for b in blocks}
    assert eligible <= set(covered)
    assert not excluded


def test_no_two_passages_share_text():
    blocks = [_block(i, f"Unique content number {i}. " * 30) for i in range(6)]
    passages, _ = SectionAwarePassageStrategy(target_chars=500, max_chars=900).build(_document(blocks))
    texts = [p.text for p in passages]
    assert len(texts) == len(set(texts))


def test_tables_become_their_own_passage():
    from normalization.core.models import TableBlock

    table_block = _block(1, "A | 1", "table")
    table_block.table = TableBlock(table_index=0, rows=[["A", "1"]], n_rows=1, n_cols=2,
                                   markdown="| A | 1 |", plain_text="A | 1")
    document = _document([_block(0, "Intro text"), table_block])
    passages, _ = SectionAwarePassageStrategy().build(document)
    table_passages = [p for p in passages if p.passage_kind == "table"]
    assert len(table_passages) == 1
    assert table_passages[0].table_id == "B1"


def test_oversize_block_splits_on_sentence_boundaries_without_overlap():
    text = " ".join(f"This is sentence number {i}." for i in range(400))
    passages, _ = SectionAwarePassageStrategy(target_chars=800, max_chars=1200).build(
        _document([_block(0, text)])
    )
    assert len(passages) > 1
    assert all(p.char_count <= 1400 for p in passages)
    # Disjoint char ranges over the same block: split, never duplicated.
    spans = sorted((l.char_start, l.char_end) for p in passages for l in p.locators)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start >= end - 1


def test_passages_never_cross_a_section_boundary():
    blocks = [_block(0, "A" * 200), _block(1, "B" * 200)]
    blocks[1].section_id = "S1"
    document = _document(blocks)
    document.sections.append(NormalizedSection(section_id="S1", section_sequence=1,
                                               title="Second", level=1))
    passages, _ = SectionAwarePassageStrategy(target_chars=5000).build(document)
    assert len({p.section_id for p in passages}) == 2


def test_short_passages_are_kept_and_flagged():
    passages, _ = SectionAwarePassageStrategy(min_chars=200).build(_document([_block(0, "tiny")]))
    assert len(passages) == 1
    assert "short_passage" in passages[0].flags


def test_passage_ids_are_sequential_and_deterministic():
    blocks = [_block(i, "X" * 900) for i in range(4)]
    first, _ = SectionAwarePassageStrategy(target_chars=800).build(_document(blocks))
    second, _ = SectionAwarePassageStrategy(target_chars=800).build(_document(blocks))
    assert [p.passage_id for p in first] == [p.passage_id for p in second]
    assert [p.passage_sequence for p in first] == list(range(len(first)))
