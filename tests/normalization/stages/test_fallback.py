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
from normalization.stages.parse import TRIGGER_TABLE_CONTENT_LOSS, profile_source
from normalization.stages.select import CatalogSelectStage, SelectRequest

# Real fixtures, chosen because each exercises a different arm of the three-condition rule.
EX10_12 = "tm2017926d1_ex10-12.htm"     # 137 tables, ratio 0.000, coverage 0.50 -> falls back
DEF14A = "ny20064714x1_def14a.htm"      # 375 tables, ratio 0.005, coverage 1.00 -> does not
EX3_1 = "tm2017926d1_ex3-1.htm"         # 323 tables, ratio 0.015, coverage 1.00 -> does not
TENK = "open-20251231.htm"              # coverage 0.38 but ratio 0.807 -> spared by ratio


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


def test_ex10_12_meets_all_three_loss_conditions(repo_config, artifacts):
    cfg = repo_config.normalization.parser.table_content_loss
    profile, ratio, coverage = _measure(repo_config, artifacts[EX10_12])
    assert profile.table_count >= cfg.min_source_tables
    assert ratio <= cfg.max_detected_ratio
    assert coverage < cfg.min_probe_coverage


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


def test_ex10_12_fell_back_and_recorded_why(normalized):
    if EX10_12 not in normalized:
        pytest.skip("EX-10.12 not in the current normalized set")
    document = normalized[EX10_12]
    assert document["parser_name"] == "lxml"
    assert document["parser_fallback_from"] == "sec_html"
    assert document["parser_fallback_reason"] == TRIGGER_TABLE_CONTENT_LOSS


def test_fallback_recovers_the_lost_tables(normalized):
    if EX10_12 not in normalized:
        pytest.skip("EX-10.12 not in the current normalized set")
    assert normalized[EX10_12]["table_count"] > 0


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
    assert decisions == {(True, TRIGGER_TABLE_CONTENT_LOSS)}


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
