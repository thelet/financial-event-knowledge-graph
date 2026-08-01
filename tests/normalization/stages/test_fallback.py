"""Table-content-loss fallback: it must fire on real loss and stay quiet otherwise.

Uses real corpus artifacts, because the whole point of the trigger is that synthetic HTML
cannot reproduce what SEC filing agents actually emit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from normalization.context import build_normalization_context, build_parser
from normalization.core.manifests import ManifestRepository
from normalization.core.models import ParsedBlock, ParsedDocument
from normalization.stages.parse import TRIGGER_TABLE_CONTENT_LOSS, profile_source
from normalization.stages.select import CatalogSelectStage, SelectRequest

from factories import make_artifact

# Real fixtures, chosen because each exercises a different arm of the three-condition rule.
#
# EX10_12 used to be the *positive* case here: 137 tables, ratio 0.000, coverage 0.50, so it
# fell back to lxml. That fallback was a false positive caused by the latin-1 decoding defect
# fixed in `parse_html_bytes`. The probes are clean ASCII drawn from the source, but the
# parsed text had `Â` inserted at every NBSP *inside* those same strings, so the substring
# match failed and the gate read corruption as missing content. With the encoding corrected,
# coverage is 1.000 -- every sampled cell is present -- and none of the nine documents that
# used to fall back does so any more.
EX10_12 = "tm2017926d1_ex10-12.htm"     # 137 tables, ratio 0.000, coverage 1.00 -> does not
DEF14A = "ny20064714x1_def14a.htm"      # 375 tables, ratio 0.005, coverage 1.00 -> does not
EX3_1 = "tm2017926d1_ex3-1.htm"         # 323 tables, ratio 0.015, coverage 1.00 -> does not
TENK = "open-20251231.htm"              # coverage 0.38 but ratio 0.807 -> spared by ratio

# Every document that fell back before the encoding fix. None may fall back now.
FORMERLY_FELL_BACK = [
    "tm2017926d1_ex10-6.htm", "tm2017926d1_ex10-7.htm", "tm2017926d1_ex10-8.htm",
    "tm2017926d1_ex10-9.htm", "tm2017926d1_ex10-10.htm", "tm2017926d1_ex10-11.htm",
    "tm2017926d1_ex10-12.htm", "tm2038661d2_ex10-14.htm", "ef20048700_ex10-1.htm",
]


@pytest.fixture(scope="module")
def artifacts(repo_config, corpus_available):
    if not corpus_available:
        pytest.skip("acquisition corpus not present")
    stage = CatalogSelectStage(repo_config, ManifestRepository(repo_config.manifests_root))
    result = stage.run(SelectRequest(run_id="fallback-test", write_manifest=False))
    return {a.original_filename: a for a in result.for_processing}


def _measure(repo_config, artifact):
    raw = (repo_config.acquisition_raw_root / artifact.source_path).read_bytes()
    cfg = repo_config.normalization.parser.table_content_loss
    profile = profile_source(raw, probe_count=cfg.probe_count, min_probe_length=cfg.min_probe_length)
    parsed = build_parser("sec_html", repo_config).parse(artifact, raw)
    tables = sum(1 for b in parsed.blocks if b.table is not None)
    text = " ".join(b.text for b in parsed.blocks)
    return profile, profile.detected_ratio(tables), profile.probe_coverage(text)


@pytest.mark.parametrize("filename", FORMERLY_FELL_BACK)
def test_formerly_falling_back_documents_keep_all_their_table_content(
    repo_config, artifacts, filename
):
    """The nine false positives. Two conditions still hold -- these really are layout-table
    documents the parser models as prose -- but the content is all there, so the gate that
    only fires on all three must stay quiet."""
    if filename not in artifacts:
        pytest.skip(f"{filename} not in the current selection")
    cfg = repo_config.normalization.parser.table_content_loss
    profile, ratio, coverage = _measure(repo_config, artifacts[filename])
    assert profile.table_count >= cfg.min_source_tables
    assert ratio <= cfg.max_detected_ratio           # still barely any table detection ...
    assert coverage >= cfg.min_probe_coverage        # ... but nothing is missing


@pytest.mark.parametrize("filename", [DEF14A, EX3_1])
def test_content_surviving_as_prose_does_not_trigger_fallback(repo_config, artifacts, filename):
    """A low detection ratio alone is normal: SEC tables are often page layout."""
    cfg = repo_config.normalization.parser.table_content_loss
    profile, ratio, coverage = _measure(repo_config, artifacts[filename])
    assert profile.table_count >= cfg.min_source_tables
    assert ratio <= cfg.max_detected_ratio           # detection is low ...
    assert coverage >= cfg.min_probe_coverage        # ... but the content is all there


def test_high_detection_ratio_spares_a_document(repo_config, artifacts):
    """The 10-K has weak probe coverage but the parser found most of its tables."""
    cfg = repo_config.normalization.parser.table_content_loss
    _, ratio, _ = _measure(repo_config, artifacts[TENK])
    assert ratio > cfg.max_detected_ratio


def test_probe_selection_is_deterministic(repo_config, artifacts):
    raw = (repo_config.acquisition_raw_root / artifacts[EX10_12].source_path).read_bytes()
    assert profile_source(raw).probes == profile_source(raw).probes


def test_profile_is_parser_independent(repo_config, artifacts):
    """The profile measures the source, so it cannot be biased by a parser's choices."""
    raw = (repo_config.acquisition_raw_root / artifacts[EX3_1].source_path).read_bytes()
    profile = profile_source(raw)
    assert profile.table_count > 0 and profile.probes
    assert profile.detected_ratio(0) == 0.0
    assert profile.probe_coverage("") == 0.0
    assert profile.probe_coverage(" ".join(profile.probes)) == 1.0


def test_empty_source_profile_never_triggers():
    profile = profile_source(b"<html><body><p>No tables at all.</p></body></html>")
    assert profile.table_count == 0
    assert profile.detected_ratio(0) == 1.0     # nothing to lose
    assert profile.probe_coverage("") == 1.0


# -- end-to-end through the pipeline -----------------------------------------------------


@pytest.fixture(scope="module")
def normalized(repo_config):
    catalog = repo_config.catalog_root / "documents.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus; run `python -m normalization spike` first")
    return {
        json.loads(l)["original_filename"]: json.loads(l)
        for l in catalog.read_text(encoding="utf-8").splitlines() if l.strip()
    }


@pytest.mark.parametrize("filename", FORMERLY_FELL_BACK)
def test_formerly_falling_back_documents_no_longer_fall_back(normalized, filename):
    if filename not in normalized:
        pytest.skip(f"{filename} not in the current normalized set")
    document = normalized[filename]
    assert document["parser_name"] == "sec_html"
    assert document["parser_fallback_reason"] is None


def test_no_document_in_the_corpus_falls_back(normalized):
    """The corrected baseline: PARSER_FALLBACK is 0 across all 294 documents. If this ever
    fires again it is a real finding, not the encoding defect returning."""
    fell_back = {n: d["parser_fallback_reason"] for n, d in normalized.items()
                 if d.get("parser_fallback_reason")}
    assert fell_back == {}


@pytest.mark.parametrize("filename", [DEF14A, EX3_1])
def test_documents_whose_content_survived_did_not_fall_back(normalized, filename):
    if filename not in normalized:
        pytest.skip(f"{filename} not in the current normalized set")
    document = normalized[filename]
    assert document["parser_name"] == "sec_html"
    assert document["parser_fallback_reason"] is None


def test_only_genuine_loss_falls_back(normalized):
    fell_back = [d for d in normalized.values() if d.get("parser_fallback_reason")]
    assert all(d["parser_fallback_reason"] == TRIGGER_TABLE_CONTENT_LOSS for d in fell_back)


def test_parser_selection_is_deterministic(repo_config, artifacts):
    """Same bytes and config must always select the same parser."""
    context = build_normalization_context(run_id="det-test", config=repo_config)
    artifact = artifacts[EX10_12]
    raw = (repo_config.acquisition_raw_root / artifact.source_path).read_bytes()
    cfg = repo_config.normalization.parser.table_content_loss
    profile = profile_source(raw, probe_count=cfg.probe_count, min_probe_length=cfg.min_probe_length)
    stage = context.normalize
    decisions = {
        stage._parse_with_policy(
            artifact, raw, context.default_parser, context.fallback_parser,
            profile.text_chars, profile,
        )[1:]
        for _ in range(3)
    }
    assert decisions == {(False, None)}


# -- the trigger must still fire on genuine loss -------------------------------------------


class _LosesTableContent:
    """A parser that reads the prose and silently drops every table.

    Synthetic on purpose. No real corpus document loses table content any more, so the only
    honest way to keep the positive arm of the trigger under test is to construct a parser
    that genuinely loses it. Contriving HTML instead would test sec-parser's quirks rather
    than the gate.
    """

    name = "loses_tables"
    version = "1.0.0"

    def supports(self, artifact) -> bool:
        return True

    def parse(self, artifact, raw: bytes) -> ParsedDocument:
        # Sized to clear the source-coverage trigger, which `_hard_failure` checks first. A
        # parser that emitted only a sentence would fall back for being short rather than
        # for losing tables, and the test would pass for the wrong reason.
        prose = "Prose that survived the parse, while the schedule rows did not. " * 40
        return ParsedDocument(
            artifact_id=artifact.artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            blocks=[
                ParsedBlock(
                    block_sequence=0,
                    block_type="paragraph",
                    text=prose,
                    fragment_sha256="0" * 64,
                )
            ],
        )


SYNTHETIC_LOSS_HTML = (
    "<html><body>"
    + "".join(
        f"<table><tr><td>Distinctive schedule row {i} of the annex to this agreement</td>"
        f"</tr></table>"
        for i in range(25)
    )
    + "</body></html>"
).encode("utf-8")


def test_synthetic_table_content_loss_still_triggers_and_falls_back(repo_config):
    """All three conditions met by construction: 25 source tables, zero detected, and probe
    text absent from the output."""
    cfg = repo_config.normalization.parser.table_content_loss
    profile = profile_source(
        SYNTHETIC_LOSS_HTML, probe_count=cfg.probe_count, min_probe_length=cfg.min_probe_length
    )
    assert profile.table_count >= cfg.min_source_tables
    assert profile.probes, "the fixture must yield probes or it proves nothing"

    context = build_normalization_context(run_id="synthetic-loss", config=repo_config)
    artifact = make_artifact()
    parsed, fell_back, trigger = context.normalize._parse_with_policy(
        artifact, SYNTHETIC_LOSS_HTML, _LosesTableContent(), context.fallback_parser,
        profile.text_chars, profile,
    )
    assert fell_back is True
    assert trigger == TRIGGER_TABLE_CONTENT_LOSS
    assert parsed.parser_name != "loses_tables", "the fallback parser must have produced this"


def test_synthetic_loss_is_detected_by_the_gate_itself(repo_config):
    """The gate in isolation, so a failure points at the rule rather than the plumbing."""
    cfg = repo_config.normalization.parser.table_content_loss
    profile = profile_source(
        SYNTHETIC_LOSS_HTML, probe_count=cfg.probe_count, min_probe_length=cfg.min_probe_length
    )
    lost = _LosesTableContent().parse(make_artifact(), SYNTHETIC_LOSS_HTML)
    text = " ".join(b.text for b in lost.blocks)
    assert profile.detected_ratio(0) <= cfg.max_detected_ratio
    assert profile.probe_coverage(text) < cfg.min_probe_coverage


def test_lxml_parser_is_deterministic_on_a_table_heavy_document(repo_config, artifacts):
    """Guards the id()-reuse bug that made block counts vary between runs."""
    parser = build_parser("lxml", repo_config)
    artifact = artifacts[EX10_12]
    raw = (repo_config.acquisition_raw_root / artifact.source_path).read_bytes()
    counts = {len(parser.parse(artifact, raw).blocks) for _ in range(3)}
    assert len(counts) == 1


def test_substantive_ex21_rows_survive(normalized, repo_config, artifacts):
    """68 <tr> in the source, but only four hold a subsidiary; spacer rows are not records."""
    name = "a2025ex211xlistofsubsidiar.htm"
    if name not in normalized:
        pytest.skip("EX-21.1 not in the current normalized set")
    document = json.loads(
        (repo_config.normalized_root / normalized[name]["document_path"]).read_text()
    )
    tables = [b for b in document["blocks"] if b.get("table")]
    assert tables, "the subsidiary list must survive as a table"
    rows = tables[0]["table"]["rows"]
    substantive = [r for r in rows if r and r[0].strip()]
    assert len(substantive) == 4
    assert all("SUBI" in r[0] or "Opendoor" in r[0] for r in substantive)
