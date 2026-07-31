"""Configuration hashing and manifest immutability."""

from __future__ import annotations

import copy

import pytest

from acquisition.config import canonical_hash, load_config
from acquisition.manifests import (
    ManifestExistsError,
    ManifestNotFoundError,
    ManifestRepository,
    compute_manifest_hash,
)
from acquisition.models import ArtifactManifest, FilingManifest
from acquisition.runmeta import build_run_metadata, make_run_id

from conftest import make_filing_record


# -- Config ------------------------------------------------------------------------------


def test_repo_config_loads(repo_config):
    company = repo_config.company()
    assert company.cik10 == "0001801169"
    assert "OPEN" in company.tickers
    assert "10-K" in repo_config.fetch.forms
    assert "@" in repo_config.fetch.http.user_agent


def test_repo_config_paths_are_rooted(repo_config):
    assert repo_config.raw_root == repo_config.root / "data" / "raw"
    assert repo_config.manifests_root == repo_config.root / "manifests"
    # Staging and raw must share a filesystem for the atomic rename to work.
    assert repo_config.tmp_root.parent == repo_config.raw_root.parent


def test_user_agent_must_carry_a_contact_email(repo_config):
    from acquisition.config import HttpConfig

    with pytest.raises(ValueError):
        HttpConfig(user_agent="no-contact-here")


def test_config_hash_is_stable_across_calls(repo_config):
    assert repo_config.config_hash() == repo_config.config_hash()


def test_config_hash_ignores_key_order():
    a = canonical_hash({"x": 1, "y": [1, 2], "z": {"b": 2, "a": 1}})
    b = canonical_hash({"z": {"a": 1, "b": 2}, "y": [1, 2], "x": 1})
    assert a == b


def test_config_hash_changes_when_scope_changes(repo_config):
    before = repo_config.config_hash()
    changed = copy.deepcopy(repo_config)
    changed.raw_fetch["forms"] = [*changed.raw_fetch["forms"], "DEFA14A"]
    assert changed.config_hash() != before


def test_config_hash_covers_company_scope(repo_config):
    before = repo_config.config_hash()
    changed = copy.deepcopy(repo_config)
    changed.raw_companies["companies"].append({"cik": 320193, "name": "Apple Inc."})
    assert changed.config_hash() != before


def test_config_hash_covers_keys_the_code_does_not_read(repo_config):
    """raw_* is hashed, so a future setting still invalidates old manifests."""
    changed = copy.deepcopy(repo_config)
    changed.raw_fetch["some_future_option"] = True
    assert changed.config_hash() != repo_config.config_hash()


def test_missing_config_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path)


def test_company_lookup_by_cik(repo_config):
    assert repo_config.company("1801169").cik == 1801169
    assert repo_config.company("0001801169").cik == 1801169
    with pytest.raises(KeyError):
        repo_config.company(320193)


# -- Run identity ------------------------------------------------------------------------


def test_run_id_embeds_config_fingerprint():
    run_id = make_run_id("abcdef1234567890")
    assert run_id.endswith("-abcdef12")
    assert run_id[8] == "T" and run_id.endswith("-abcdef12")


def test_different_configs_yield_different_run_ids():
    assert make_run_id("a" * 64)[-8:] != make_run_id("b" * 64)[-8:]


def test_run_metadata_captures_environment():
    run = build_run_metadata("discover", "0" * 64)
    assert run.stage == "discover"
    assert run.python_version
    assert run.platform
    assert run.fetcher_version
    assert "pydantic" in run.dependency_versions


# -- Manifests ---------------------------------------------------------------------------


def _filing_manifest(config_hash: str = "0" * 64) -> FilingManifest:
    return FilingManifest(
        run=build_run_metadata("discover", config_hash),
        company_cik10="0001801169",
        forms=["8-K"],
        date_from="2020-01-01",
        date_to="2026-12-31",
        filings=[make_filing_record()],
    )


def test_manifest_write_and_read_roundtrip(tmp_path):
    repo = ManifestRepository(tmp_path)
    manifest = _filing_manifest()
    repo.write_filing_manifest(manifest)

    loaded = repo.read_filing_manifest(manifest.run.run_id)
    assert loaded.filings[0].accession == "0001801169-26-000009"
    assert loaded.manifest_hash == manifest.manifest_hash


def test_manifests_are_immutable(tmp_path):
    repo = ManifestRepository(tmp_path)
    manifest = _filing_manifest()
    repo.write_filing_manifest(manifest)

    with pytest.raises(ManifestExistsError):
        repo.write_filing_manifest(manifest)


def test_manifest_hash_excludes_itself(tmp_path):
    manifest = _filing_manifest()
    first = compute_manifest_hash(manifest)
    manifest.manifest_hash = first
    assert compute_manifest_hash(manifest) == first


def test_manifest_hash_changes_with_content():
    a = _filing_manifest()
    b = _filing_manifest()
    b.filings.append(make_filing_record(accession="0001801169-26-000010", form="10-K"))
    assert compute_manifest_hash(a) != compute_manifest_hash(b)


def test_latest_run_id_is_chronological(tmp_path):
    repo = ManifestRepository(tmp_path)
    for run_id in ("20260101T000000Z-aaaaaaaa", "20260731T120000Z-bbbbbbbb"):
        manifest = _filing_manifest()
        manifest.run.run_id = run_id
        repo.write_filing_manifest(manifest)
    assert repo.latest_filing_run_id() == "20260731T120000Z-bbbbbbbb"


def test_latest_raises_when_no_manifests(tmp_path):
    repo = ManifestRepository(tmp_path)
    with pytest.raises(ManifestNotFoundError):
        repo.latest_filing_run_id()
    with pytest.raises(ManifestNotFoundError):
        repo.latest_artifact_run_id()


def test_reading_unknown_run_id_raises(tmp_path):
    with pytest.raises(ManifestNotFoundError):
        ManifestRepository(tmp_path).read_filing_manifest("nope")


def test_artifact_manifest_links_to_its_filing_run(tmp_path):
    repo = ManifestRepository(tmp_path)
    filings = _filing_manifest()
    repo.write_filing_manifest(filings)

    artifacts = ArtifactManifest(
        run=build_run_metadata("resolve", "0" * 64),
        filings_run_id=filings.run.run_id,
        company_cik10="0001801169",
        artifacts=[],
    )
    repo.write_artifact_manifest(artifacts)
    assert repo.read_artifact_manifest(artifacts.run.run_id).filings_run_id == (
        filings.run.run_id
    )


def test_no_temporary_files_remain_after_write(tmp_path):
    repo = ManifestRepository(tmp_path)
    repo.write_filing_manifest(_filing_manifest())
    assert not list(tmp_path.rglob("*.tmp"))
