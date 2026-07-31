"""Catalog determinism and corpus verification."""

from __future__ import annotations

import json

import pytest

from acquisition.catalog import CatalogBuilder
from acquisition.core.storage import ARTIFACTS_CATALOG, FILINGS_CATALOG
from acquisition.utils.jsonl import read_jsonl
from acquisition.core.manifests import ManifestRepository
from acquisition.core.models import ArtifactManifest, FilingManifest
from acquisition.core.runmeta import build_run_metadata
from acquisition.core.storage import FILING_METADATA_NAME, LocalRawArtifactStore
from acquisition.verify import CorpusVerifier

from conftest import make_artifact_record, make_filing_record, write_finalized_filing

CIK = 1801169


@pytest.fixture
def store(tmp_path):
    return LocalRawArtifactStore(tmp_path / "raw", tmp_path / "tmp")


@pytest.fixture
def catalog_root(tmp_path):
    return tmp_path / "catalog"


@pytest.fixture
def corpus(store):
    """Three finalized filings on disk."""
    specs = [
        ("0001801169-26-000009", "8-K", "2026-02-19", ["2.02", "7.01"]),
        ("0001801169-26-000010", "10-K", "2026-02-19", []),
        ("0001104659-20-132667", "8-K", "2020-12-07", ["5.02"]),
    ]
    filings = []
    for accession, form, date, items in specs:
        filing = make_filing_record(
            accession=accession, form=form, filing_date=date, items=items
        )
        write_finalized_filing(
            store.raw_root,
            filing,
            {"primary.htm": f"body-{accession}".encode(), "ex99.htm": b"exhibit"},
        )
        filings.append(filing)
    return filings


def _manifests(filings, tmp_path):
    filing_manifest = FilingManifest(
        run=build_run_metadata("discover", "0" * 64, run_id="20260731T000000Z-aaaaaaaa"),
        company_cik10="0001801169",
        forms=["8-K", "10-K"],
        date_from="2020-01-01",
        date_to="2026-12-31",
        filings=filings,
    )
    artifacts = []
    for filing in filings:
        for name in ("primary.htm", "ex99.htm"):
            artifacts.append(make_artifact_record(filing, name))
    artifact_manifest = ArtifactManifest(
        run=build_run_metadata("resolve", "0" * 64, run_id="20260731T000100Z-aaaaaaaa"),
        filings_run_id=filing_manifest.run.run_id,
        company_cik10="0001801169",
        artifacts=artifacts,
    )
    return filing_manifest, artifact_manifest


# -- Catalog -----------------------------------------------------------------------------


def test_catalog_builds_from_filing_metadata(store, catalog_root, corpus):
    result = CatalogBuilder(store, catalog_root).build()
    assert result.ok
    assert result.filing_count == 3
    assert result.artifact_count == 6
    assert (catalog_root / FILINGS_CATALOG).is_file()
    assert (catalog_root / ARTIFACTS_CATALOG).is_file()


def test_catalog_rebuild_is_byte_identical(store, catalog_root, corpus):
    builder = CatalogBuilder(store, catalog_root)
    builder.build()
    first = (catalog_root / FILINGS_CATALOG).read_bytes()
    first_artifacts = (catalog_root / ARTIFACTS_CATALOG).read_bytes()

    builder.build()
    assert (catalog_root / FILINGS_CATALOG).read_bytes() == first
    assert (catalog_root / ARTIFACTS_CATALOG).read_bytes() == first_artifacts


def test_catalog_rows_are_sorted_chronologically(store, catalog_root, corpus):
    CatalogBuilder(store, catalog_root).build()
    rows = read_jsonl(catalog_root / FILINGS_CATALOG)
    keys = [(r["cik10"], r["filing_date"], r["accession"]) for r in rows]
    assert keys == sorted(keys)
    assert rows[0]["filing_date"] == "2020-12-07"


def test_artifact_rows_are_sorted(store, catalog_root, corpus):
    CatalogBuilder(store, catalog_root).build()
    rows = read_jsonl(catalog_root / ARTIFACTS_CATALOG)
    keys = [
        (
            r["cik10"],
            r["filing_date"],
            r["accession"],
            r["sequence"] if r["sequence"] is not None else 1_000_000,
            r["original_filename"],
        )
        for r in rows
    ]
    assert keys == sorted(keys)


def test_catalog_is_queryable_as_plain_jsonl(store, catalog_root, corpus):
    CatalogBuilder(store, catalog_root).build()
    with (catalog_root / ARTIFACTS_CATALOG).open() as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    earnings = [r for r in rows if "2.02" in r["items"] and r["role"] == "ex99-01"]
    assert earnings


def test_catalog_carries_item_codes_and_provenance(store, catalog_root, corpus):
    CatalogBuilder(store, catalog_root).build()
    rows = read_jsonl(catalog_root / FILINGS_CATALOG)
    by_accession = {r["accession"]: r for r in rows}
    assert by_accession["0001801169-26-000009"]["items"] == ["2.02", "7.01"]
    assert by_accession["0001104659-20-132667"]["items"] == ["5.02"]
    for row in rows:
        assert row["filing_dir"].startswith("sec/0001801169/")


def test_artifact_rows_preserve_filenames_and_urls(store, catalog_root, corpus):
    CatalogBuilder(store, catalog_root).build()
    rows = read_jsonl(catalog_root / ARTIFACTS_CATALOG)
    for row in rows:
        assert row["original_filename"] in row["stored_path"]
        assert row["source_url"].startswith("https://www.sec.gov/Archives/")
        assert row["sha256"] and len(row["sha256"]) == 64


def test_catalog_reports_unreadable_metadata(store, catalog_root, corpus):
    broken = next(store.iter_finalized_filings())
    broken.write_text("{ not json")
    result = CatalogBuilder(store, catalog_root).build()
    assert not result.ok
    assert result.unreadable


def test_catalog_reports_duplicate_ids(store, catalog_root, corpus):
    """Two directories claiming the same filing_id must be reported, not merged."""
    source = next(store.iter_finalized_filings())
    payload = json.loads(source.read_text())
    clone_dir = store.raw_root / "sec/0001801169/8-K/2026-02-19_0001801169-26-000009-copy"
    (clone_dir / "source").mkdir(parents=True)
    for artifact in payload["artifacts"]:
        (clone_dir / artifact["stored_path"]).write_bytes(b"x")
    (clone_dir / FILING_METADATA_NAME).write_text(json.dumps(payload))

    result = CatalogBuilder(store, catalog_root).build()
    assert result.duplicate_filing_ids
    assert not result.ok


def test_catalog_on_empty_corpus(store, catalog_root):
    result = CatalogBuilder(store, catalog_root).build()
    assert result.ok
    assert result.filing_count == 0


def test_no_catalog_temp_files_remain(store, catalog_root, corpus):
    CatalogBuilder(store, catalog_root).build()
    assert not list(catalog_root.glob("*.tmp"))


def test_read_jsonl_raises_on_corruption(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"a":1}\nnot json\n')
    with pytest.raises(ValueError, match="malformed JSONL"):
        read_jsonl(path)


# -- Verify ------------------------------------------------------------------------------


def _verify(store, catalog_root, corpus, tmp_path, build=True):
    if build:
        CatalogBuilder(store, catalog_root).build()
    filing_manifest, artifact_manifest = _manifests(corpus, tmp_path)
    return CorpusVerifier(store, catalog_root).verify(filing_manifest, artifact_manifest)


def test_verify_passes_on_a_consistent_corpus(store, catalog_root, corpus, tmp_path):
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert report.ok, report.render()
    assert report.stats["filings.finalized"] == 3


def test_verify_detects_a_missing_filing(store, catalog_root, corpus, tmp_path):
    import shutil

    shutil.rmtree(store.filing_dir(corpus[0].filing_dir))
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert not report.ok
    assert any(f.check == "filing_not_finalized" for f in report.errors)


def test_verify_detects_a_missing_artifact(store, catalog_root, corpus, tmp_path):
    (store.filing_dir(corpus[0].filing_dir) / "source" / "ex99.htm").unlink()
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert not report.ok
    assert any(f.check == "artifact_integrity" for f in report.errors)


def test_verify_detects_a_corrupted_artifact(store, catalog_root, corpus, tmp_path):
    target = store.filing_dir(corpus[0].filing_dir) / "source" / "primary.htm"
    target.write_bytes(b"tampered content of a different length")
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert not report.ok
    assert any("size mismatch" in f.detail or "hash mismatch" in f.detail
               for f in report.errors)


def test_verify_detects_a_same_length_corruption(store, catalog_root, corpus, tmp_path):
    """Size alone is not enough; hashes must be recomputed."""
    target = store.filing_dir(corpus[0].filing_dir) / "source" / "ex99.htm"
    original = target.read_bytes()
    target.write_bytes(b"X" * len(original))
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert any("hash mismatch" in f.detail for f in report.errors)


def test_verify_detects_unexpected_files_on_disk(store, catalog_root, corpus, tmp_path):
    (store.filing_dir(corpus[0].filing_dir) / "source" / "stray.htm").write_bytes(b"?")
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert not report.ok
    assert any(f.check == "unexpected_file_on_disk" for f in report.errors)


def test_verify_detects_invalid_metadata(store, catalog_root, corpus, tmp_path):
    (store.filing_dir(corpus[0].filing_dir) / FILING_METADATA_NAME).write_text("{ bad")
    filing_manifest, artifact_manifest = _manifests(corpus, tmp_path)
    report = CorpusVerifier(store, catalog_root).verify(filing_manifest, artifact_manifest)
    assert any(f.check == "filing_not_finalized" for f in report.errors)


def test_verify_detects_catalog_divergence(store, catalog_root, corpus, tmp_path):
    CatalogBuilder(store, catalog_root).build()
    rows = read_jsonl(catalog_root / ARTIFACTS_CATALOG)
    (catalog_root / ARTIFACTS_CATALOG).write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in rows[:-1]) + "\n"
    )
    report = _verify(store, catalog_root, corpus, tmp_path, build=False)
    assert not report.ok
    assert any(f.check == "catalog_divergence" for f in report.errors)


def test_verify_detects_corrupt_catalog(store, catalog_root, corpus, tmp_path):
    CatalogBuilder(store, catalog_root).build()
    (catalog_root / FILINGS_CATALOG).write_text("{ not json\n")
    report = _verify(store, catalog_root, corpus, tmp_path, build=False)
    assert not report.ok
    assert any(f.check == "corrupt_catalog" for f in report.errors)


def test_verify_detects_duplicate_ids_in_catalog(store, catalog_root, corpus, tmp_path):
    CatalogBuilder(store, catalog_root).build()
    rows = read_jsonl(catalog_root / FILINGS_CATALOG)
    (catalog_root / FILINGS_CATALOG).write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in [*rows, rows[0]]) + "\n"
    )
    report = _verify(store, catalog_root, corpus, tmp_path, build=False)
    assert any(f.check == "duplicate_id_in_catalog" for f in report.errors)


def test_verify_detects_artifact_missing_from_metadata(store, catalog_root, corpus, tmp_path):
    filing_manifest, artifact_manifest = _manifests(corpus, tmp_path)
    artifact_manifest.artifacts.append(
        make_artifact_record(corpus[0], "never-downloaded.htm")
    )
    report = CorpusVerifier(store, catalog_root).verify(filing_manifest, artifact_manifest)
    assert any(f.check == "artifact_missing_from_metadata" for f in report.errors)


def test_verify_detects_manifest_linkage_mismatch(store, catalog_root, corpus, tmp_path):
    filing_manifest, artifact_manifest = _manifests(corpus, tmp_path)
    artifact_manifest.filings_run_id = "some-other-run"
    report = CorpusVerifier(store, catalog_root).verify(filing_manifest, artifact_manifest)
    assert any(f.check == "manifest_linkage" for f in report.errors)


def test_verify_warns_on_staging_debris(store, catalog_root, corpus, tmp_path):
    (store.tmp_root / "interrupted-run" / "acc" / "source").mkdir(parents=True)
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert report.ok  # a warning, not a failure
    assert any(f.check == "incomplete_staging_directory" for f in report.warnings)


def test_verify_warns_on_filings_outside_the_manifest(store, catalog_root, corpus, tmp_path):
    extra = make_filing_record(accession="0001801169-25-000090", filing_date="2025-11-06")
    write_finalized_filing(store.raw_root, extra, {"primary.htm": b"x"})
    report = _verify(store, catalog_root, corpus, tmp_path)
    assert any(f.check == "filing_not_in_manifest" for f in report.warnings)


def test_verify_report_renders_and_states_outcome(store, catalog_root, corpus, tmp_path):
    text = _verify(store, catalog_root, corpus, tmp_path).render()
    assert "VERIFICATION REPORT" in text
    assert "filings.finalized" in text


def test_verify_warns_when_catalog_absent(store, catalog_root, corpus, tmp_path):
    report = _verify(store, catalog_root, corpus, tmp_path, build=False)
    assert report.ok
    assert any(f.check == "catalog_absent" for f in report.warnings)


def test_manifest_roundtrip_through_repository(tmp_path, corpus):
    """Manifests survive serialization without losing item codes or identities."""
    repo = ManifestRepository(tmp_path / "manifests")
    filing_manifest, artifact_manifest = _manifests(corpus, tmp_path)
    repo.write_filing_manifest(filing_manifest)
    repo.write_artifact_manifest(artifact_manifest)

    loaded = repo.read_filing_manifest(filing_manifest.run.run_id)
    assert [f.items for f in loaded.filings] == [f.items for f in corpus]
    assert [f.filing_id for f in loaded.filings] == [f.filing_id for f in corpus]
