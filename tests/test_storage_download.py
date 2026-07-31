"""Storage atomicity, download idempotency, and repair. Real filesystem, no network."""

from __future__ import annotations

import json

import pytest

from acquisition.download import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_REPAIRED,
    STATUS_SKIPPED,
    ArtifactDownloader,
)
from acquisition.storage import (
    FILING_METADATA_NAME,
    FilingIntegrityError,
    LocalRawArtifactStore,
    sha256_file,
)

from conftest import FakeSecClient, make_artifact_record, make_filing_record

CIK = 1801169


@pytest.fixture
def store(tmp_path) -> LocalRawArtifactStore:
    return LocalRawArtifactStore(tmp_path / "raw", tmp_path / "tmp")


@pytest.fixture
def filing():
    return make_filing_record()


@pytest.fixture
def artifacts(filing):
    return [
        make_artifact_record(
            filing, "open-20260219.htm", kind="primary", role="primary",
            exhibit_type="8-K", sequence=1,
        ),
        make_artifact_record(filing, "q42025formxex991earningsre.htm"),
        make_artifact_record(
            filing, "chart001.jpg", kind="asset", role=None, exhibit_type="GRAPHIC",
            sequence=9,
        ),
    ]


@pytest.fixture
def downloader(store, company, filing):
    client = FakeSecClient({})
    return ArtifactDownloader(client, store, company, run_id="test-run"), client


# -- Atomic finalization -----------------------------------------------------------------


def test_download_finalizes_a_filing(downloader, store, filing, artifacts):
    dl, _ = downloader
    outcome = dl.download_filing(filing, artifacts)

    assert outcome.status == STATUS_DOWNLOADED
    assert outcome.artifact_count == 3
    root = store.filing_dir(filing.filing_dir)
    assert (root / FILING_METADATA_NAME).is_file()
    assert (root / "source" / "open-20260219.htm").is_file()


def test_original_filenames_are_preserved_under_source(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    names = {p.name for p in (store.filing_dir(filing.filing_dir) / "source").iterdir()}
    assert names == {
        "open-20260219.htm",
        "q42025formxex991earningsre.htm",
        "chart001.jpg",
    }


def test_metadata_records_hashes_that_match_disk(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    metadata = store.inspect_finalized_filing(filing.filing_dir)
    root = store.filing_dir(filing.filing_dir)

    for artifact in metadata.artifacts:
        assert sha256_file(root / artifact.stored_path) == artifact.sha256
        assert (root / artifact.stored_path).stat().st_size == artifact.size_bytes


def test_item_codes_survive_into_metadata(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    metadata = store.inspect_finalized_filing(filing.filing_dir)
    assert metadata.items == ["2.02", "7.01", "9.01"]


def test_response_metadata_is_recorded(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    artifact = store.inspect_finalized_filing(filing.filing_dir).artifacts[0]
    assert artifact.http_status == 200
    assert artifact.etag
    assert artifact.downloaded_at
    assert artifact.verification_status == "verified"


def test_no_staging_debris_after_success(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    leftovers = list(store.tmp_root.rglob("*")) if store.tmp_root.exists() else []
    assert [p for p in leftovers if p.is_file()] == []


def test_finalize_refuses_without_metadata(store, tmp_path):
    staging = store.prepare_temporary_filing("run", "0001801169-26-000009")
    (staging / "source" / "x.htm").write_bytes(b"data")
    with pytest.raises(FilingIntegrityError):
        store.finalize_filing(staging, "sec/0001801169/8-K/2026-02-19_x")


def test_interrupted_download_leaves_nothing_finalized(store, company, filing, artifacts):
    """A failure mid-download must not produce a directory that looks complete."""

    class ExplodingClient(FakeSecClient):
        def download_to(self, url, destination):
            if "chart001" in url:
                raise RuntimeError("connection died")
            return super().download_to(url, destination)

    dl = ArtifactDownloader(ExplodingClient({}), store, company, run_id="interrupted")
    outcome = dl.download_filing(filing, artifacts)

    assert outcome.status == STATUS_FAILED
    assert "connection died" in outcome.reason
    assert store.inspect_finalized_filing(filing.filing_dir) is None
    assert not store.filing_dir(filing.filing_dir).exists()


def test_partial_staging_is_discarded_on_failure(store, company, filing, artifacts):
    class ExplodingClient(FakeSecClient):
        def download_to(self, url, destination):
            if "chart001" in url:
                raise RuntimeError("boom")
            return super().download_to(url, destination)

    dl = ArtifactDownloader(ExplodingClient({}), store, company, run_id="interrupted")
    dl.download_filing(filing, artifacts)
    assert not (store.tmp_root / "interrupted" / filing.accession).exists()


def test_artifact_path_cannot_escape_the_filing_directory(store, tmp_path):
    staging = store.prepare_temporary_filing("run", "0001801169-26-000009")
    with pytest.raises(FilingIntegrityError):
        store.artifact_destination(staging, "../../escaped.htm")


# -- Idempotency and repair --------------------------------------------------------------


def test_rerun_skips_a_valid_filing_without_requests(downloader, store, filing, artifacts):
    dl, client = downloader
    dl.download_filing(filing, artifacts)
    requests_after_first = client.request_count

    outcome = dl.download_filing(filing, artifacts)
    assert outcome.status == STATUS_SKIPPED
    assert client.request_count == requests_after_first  # no new requests


def test_force_redownloads(downloader, store, filing, artifacts):
    dl, client = downloader
    dl.download_filing(filing, artifacts)
    before = client.request_count

    outcome = dl.download_filing(filing, artifacts, force=True)
    assert outcome.status == STATUS_REPAIRED
    assert client.request_count > before


def test_deleting_a_filing_and_rerunning_restores_it_byte_identically(
    downloader, store, filing, artifacts
):
    import shutil

    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    root = store.filing_dir(filing.filing_dir)
    before = {
        p.relative_to(root).as_posix(): sha256_file(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != FILING_METADATA_NAME
    }

    shutil.rmtree(root)
    assert store.inspect_finalized_filing(filing.filing_dir) is None

    outcome = dl.download_filing(filing, artifacts)
    assert outcome.status == STATUS_DOWNLOADED
    after = {
        p.relative_to(root).as_posix(): sha256_file(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != FILING_METADATA_NAME
    }
    assert after == before


def test_corrupted_artifact_triggers_repair(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)

    target = store.filing_dir(filing.filing_dir) / "source" / "open-20260219.htm"
    target.write_bytes(b"CORRUPTED")

    outcome = dl.download_filing(filing, artifacts)
    assert outcome.status == STATUS_REPAIRED
    metadata = store.inspect_finalized_filing(filing.filing_dir)
    assert not store.verify_filing_contents(filing.filing_dir, metadata)


def test_missing_artifact_triggers_repair(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    (store.filing_dir(filing.filing_dir) / "source" / "chart001.jpg").unlink()

    assert dl.download_filing(filing, artifacts).status == STATUS_REPAIRED
    assert (store.filing_dir(filing.filing_dir) / "source" / "chart001.jpg").is_file()


def test_changed_artifact_set_triggers_repair(downloader, store, filing, artifacts):
    """A filing resolved with a different artifact set is not 'already complete'."""
    dl, _ = downloader
    dl.download_filing(filing, artifacts)

    extended = [*artifacts, make_artifact_record(filing, "exhibit992.htm", role="ex99-02")]
    outcome = dl.download_filing(filing, extended)
    assert outcome.status == STATUS_REPAIRED
    assert len(store.inspect_finalized_filing(filing.filing_dir).artifacts) == 4


def test_invalid_metadata_json_triggers_repair(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    (store.filing_dir(filing.filing_dir) / FILING_METADATA_NAME).write_text("{ broken")

    assert store.inspect_finalized_filing(filing.filing_dir) is None
    assert dl.download_filing(filing, artifacts).status == STATUS_REPAIRED
    assert store.inspect_finalized_filing(filing.filing_dir) is not None


def test_repair_preserves_the_old_directory_on_failure(store, company, filing, artifacts):
    """A failed repair must not destroy the previously good copy."""
    good = ArtifactDownloader(FakeSecClient({}), store, company, run_id="good")
    good.download_filing(filing, artifacts)
    original = store.inspect_finalized_filing(filing.filing_dir)

    class ExplodingClient(FakeSecClient):
        def download_to(self, url, destination):
            raise RuntimeError("network gone")

    bad = ArtifactDownloader(ExplodingClient({}), store, company, run_id="bad")
    outcome = bad.download_filing(filing, artifacts, force=True)

    assert outcome.status == STATUS_FAILED
    restored = store.inspect_finalized_filing(filing.filing_dir)
    assert restored is not None
    assert [a.sha256 for a in restored.artifacts] == [a.sha256 for a in original.artifacts]


def test_download_with_no_artifacts_fails_loudly(downloader, filing):
    dl, _ = downloader
    assert dl.download_filing(filing, []).status == STATUS_FAILED


# -- Concurrency -------------------------------------------------------------------------


def test_successful_run_removes_its_staging_root(store, company):
    """Surviving debris must mean interruption, not merely 'a run happened'."""
    filings = [make_filing_record(accession="0001801169-26-000031")]
    artifacts = [make_artifact_record(filings[0], "d.htm")]
    dl = ArtifactDownloader(FakeSecClient({}), store, company, run_id="tidy")
    dl.download_all(filings, artifacts)
    assert not (store.tmp_root / "tidy").exists()


def test_failed_run_keeps_evidence_of_interruption(store, company):
    class ExplodingClient(FakeSecClient):
        def download_to(self, url, destination):
            raise RuntimeError("boom")

    filings = [make_filing_record(accession="0001801169-26-000032")]
    artifacts = [make_artifact_record(filings[0], "d.htm")]
    dl = ArtifactDownloader(ExplodingClient({}), store, company, run_id="broken")
    summary = dl.download_all(filings, artifacts)
    assert summary.count(STATUS_FAILED) == 1


def test_download_all_covers_every_filing(store, company):
    filings = [
        make_filing_record(accession=f"0001801169-26-00001{n}", filing_date="2026-02-19")
        for n in range(5)
    ]
    artifacts = [make_artifact_record(f, f"doc{i}.htm") for i, f in enumerate(filings)]

    dl = ArtifactDownloader(FakeSecClient({}), store, company, run_id="concurrent")
    summary = dl.download_all(filings, artifacts, max_workers=4)

    assert summary.count(STATUS_DOWNLOADED) == 5
    assert len(list(store.iter_finalized_filings())) == 5


def test_iter_finalized_filings_is_deterministic(store, company):
    filings = [
        make_filing_record(accession=f"0001801169-26-00002{n}", filing_date="2026-02-19")
        for n in range(4)
    ]
    artifacts = [make_artifact_record(f, "d.htm") for f in filings]
    dl = ArtifactDownloader(FakeSecClient({}), store, company, run_id="r")
    dl.download_all(filings, artifacts, max_workers=4)

    first = [p.as_posix() for p in store.iter_finalized_filings()]
    second = [p.as_posix() for p in store.iter_finalized_filings()]
    assert first == second == sorted(first)


def test_metadata_is_valid_json(downloader, store, filing, artifacts):
    dl, _ = downloader
    dl.download_filing(filing, artifacts)
    raw = (store.filing_dir(filing.filing_dir) / FILING_METADATA_NAME).read_text()
    assert json.loads(raw)["filing_id"] == filing.filing_id
