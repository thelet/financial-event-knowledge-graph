"""Deterministic identity, canonical model round trips, and the volatile-field rule."""

from __future__ import annotations

import pytest

from normalization.core import identity
from normalization.core.models import (
    ContentBlock,
    NormalizedDocument,
    NormalizedSection,
    Passage,
    SourceLocator,
)


# -- identity ----------------------------------------------------------------------------


def test_document_id_shape():
    assert identity.document_id(1801169, "0001801169-26-000010", "open.htm") == (
        "norm:0001801169:0001801169-26-000010:open.htm"
    )


def test_document_id_matches_derivation_from_artifact_id():
    direct = identity.document_id(1801169, "0001801169-26-000010", "open.htm")
    derived = identity.document_id_from_artifact_id(
        "sec:0001801169:0001801169-26-000010:open.htm"
    )
    assert direct == derived


def test_document_id_is_stable_when_content_changes():
    """Source bytes carry no weight in the id: it is the same logical document, revised."""
    a = identity.document_id(1801169, "0001801169-26-000010", "open.htm")
    b = identity.document_id(1801169, "0001801169-26-000010", "open.htm")
    assert a == b


@pytest.mark.parametrize("accession", ["", "nope", "0001801169-26-0000", "000180116926000010"])
def test_document_id_rejects_bad_accession(accession):
    with pytest.raises(ValueError):
        identity.document_id(1801169, accession, "x.htm")


def test_child_ids_are_positional_and_readable():
    doc = identity.document_id(1801169, "0001801169-26-000010", "open.htm")
    assert identity.section_id(doc, 2).endswith("#s2")
    assert identity.block_id(doc, 41).endswith("#b41")
    assert identity.passage_id(doc, 7).endswith("#p7")


def test_ids_reject_negative_sequences():
    doc = identity.document_id(1801169, "0001801169-26-000010", "open.htm")
    with pytest.raises(ValueError):
        identity.block_id(doc, -1)


def test_derivation_id_is_deterministic_and_input_sensitive():
    kwargs = dict(
        selection_policy_version="v1", parser_name="sec_html", parser_version="0.58.1",
        normalizer_version="1.0.0", passage_strategy_name="section_aware",
        passage_strategy_version="1.0.0", config_hash="c" * 64, source_content_sha256="s" * 64,
    )
    first = identity.derivation_id(**kwargs)
    assert first == identity.derivation_id(**kwargs)
    assert first != identity.derivation_id(**{**kwargs, "parser_name": "lxml"})
    assert first != identity.derivation_id(**{**kwargs, "source_content_sha256": "t" * 64})


def test_canonical_hash_ignores_key_order():
    assert identity.canonical_hash({"a": 1, "b": 2}) == identity.canonical_hash({"b": 2, "a": 1})


# -- models ------------------------------------------------------------------------------


def _document(**overrides) -> NormalizedDocument:
    base = dict(
        document_id="norm:0001801169:0001801169-26-000009:x.htm",
        source_artifact_id="sec:0001801169:0001801169-26-000009:x.htm",
        filing_id="sec:0001801169:0001801169-26-000009",
        cik=1801169, cik10="0001801169", company_name="Opendoor", form="8-K",
        form_sanitized="8-K", filing_date="2026-02-19", original_filename="x.htm",
        source_url="https://www.sec.gov/x", source_path="p/x.htm",
        document_type="earnings_release", selection_policy_version="v1",
        parser_name="sec_html", parser_version="0.58.1", normalizer_version="1.0.0",
        config_hash="c" * 64, source_content_sha256="s" * 64, content_sha256="h" * 64,
        derivation_id="d" * 64,
    )
    base.update(overrides)
    return NormalizedDocument(**base)


def test_document_round_trip():
    document = _document(sections=[NormalizedSection(
        section_id="s", section_sequence=0, title="root", level=0)])
    restored = NormalizedDocument(**document.model_dump(mode="json"))
    assert restored == document


VOLATILE = ("run_id", "normalized_at", "code_commit", "created_at", "timestamp")


@pytest.mark.parametrize("field", VOLATILE)
def test_document_has_no_volatile_field(field):
    """These would make byte-identical reruns impossible (v1 plan section 12)."""
    assert field not in _document().model_dump(mode="json")


@pytest.mark.parametrize("field", VOLATILE)
def test_passage_has_no_volatile_field(field):
    passage = Passage(
        passage_id="p", passage_sequence=0, document_id="d", source_artifact_id="a",
        filing_id="f", cik10="0001801169", company_name="Opendoor", form="8-K",
        filing_date="2026-02-19", document_type="earnings_release",
        source_url="https://www.sec.gov/x", text="t", char_count=1,
        passage_kind="narrative", section_id="s", content_sha256="h" * 64,
        passage_strategy_name="section_aware", passage_strategy_version="1.0.0",
        parser_name="sec_html", parser_version="0.58.1", normalizer_version="1.0.0",
        derivation_id="d" * 64,
    )
    assert field not in passage.model_dump(mode="json")


def test_locator_has_no_byte_offsets_into_source():
    """The plan deliberately promises block-relative offsets only."""
    fields = set(SourceLocator.model_fields)
    assert {"char_start", "char_end", "fragment_sha256", "heading_path"} <= fields
    assert "byte_start" not in fields and "source_offset" not in fields


def test_content_block_round_trip():
    block = ContentBlock(
        block_id="b", block_sequence=0, section_id="s", block_type="paragraph",
        text="hello", char_count=5, block_sha256="x" * 64,
        locator=SourceLocator(artifact_id="a", block_sequence=0, fragment_sha256="f" * 64),
    )
    assert ContentBlock(**block.model_dump(mode="json")) == block
