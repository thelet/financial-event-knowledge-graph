"""Command-line entry points.

Each stage is independently runnable:

    python -m acquisition discover
    python -m acquisition resolve      [--filings-run-id RUN]
    python -m acquisition download     [--artifacts-run-id RUN] [--force] [--limit N]
    python -m acquisition build-catalog
    python -m acquisition verify       [--filings-run-id RUN] [--artifacts-run-id RUN]
    python -m acquisition report
    python -m acquisition acquire      # convenience: all of the above in order

`acquire` is a convenience only; the individual stages remain the contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import CatalogBuilder
from .core.config import AppConfig, load_config
from .discovery import FilingDiscoverer
from .discovery import summarize as summarize_filings
from .download import ArtifactDownloader
from .core.manifests import ManifestRepository
from .core.models import ArtifactManifest, FilingManifest, RunRecord
from .report import build_corpus_report
from .resolution import ArtifactResolver
from .resolution import summarize as summarize_artifacts
from .core.runmeta import build_run_metadata, utc_now_iso
from .core.sec_client import SecClient
from .core.storage import LocalRawArtifactStore
from .verify import CorpusVerifier

EXIT_OK = 0
EXIT_FAILED = 1


# --------------------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------------------


def _context(args: argparse.Namespace) -> tuple[AppConfig, ManifestRepository, LocalRawArtifactStore]:
    config = load_config(Path(args.root) if args.root else None)
    manifests = ManifestRepository(config.manifests_root)
    store = LocalRawArtifactStore(config.raw_root, config.tmp_root)
    return config, manifests, store


def _client(config: AppConfig) -> SecClient:
    return SecClient(config.fetch.http)


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _write_run_record(config: AppConfig, record: RunRecord) -> Path:
    config.runs_root.mkdir(parents=True, exist_ok=True)
    path = config.runs_root / f"{record.run.run_id}-{record.run.stage}.json"
    path.write_text(
        json.dumps(record.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> int:
    config, manifests, _ = _context(args)
    company = config.company(args.cik)
    config_hash = config.config_hash()
    run = build_run_metadata("discover", config_hash, root=config.root)

    _banner(f"DISCOVER  {company.name}  CIK {company.cik10}  run {run.run_id}")
    date_to = config.fetch.effective_date_to()
    print(
        f"Forms: {', '.join(config.fetch.forms)}   "
        f"Dates: {config.fetch.date_from} .. {date_to}   "
        f"Amendments: {'included' if args.include_amendments else 'excluded'}\n"
    )

    with _client(config) as client:
        discoverer = FilingDiscoverer(client)
        filings = discoverer.discover(
            company,
            forms=config.fetch.forms,
            date_from=config.fetch.date_from,
            date_to=date_to,
            include_amendments=args.include_amendments,
        )
        requests_made = client.request_count

    print(summarize_filings(filings))

    manifest = FilingManifest(
        run=run,
        company_cik10=company.cik10,
        forms=list(config.fetch.forms),
        date_from=config.fetch.date_from,
        date_to=date_to,
        filings=filings,
    )
    path = manifests.write_filing_manifest(manifest)
    print(f"\nManifest written (immutable): {path}")
    print(f"config_hash={config_hash[:16]}  manifest_hash={manifest.manifest_hash[:16]}")

    _write_run_record(
        config,
        RunRecord(
            run=run,
            started_at=run.created_at,
            finished_at=utc_now_iso(),
            config_snapshot={"forms": config.fetch.forms, "date_from": config.fetch.date_from},
            counts={"filings": len(filings)},
            requests_made=requests_made,
        ),
    )
    return EXIT_OK if filings else EXIT_FAILED


def cmd_resolve(args: argparse.Namespace) -> int:
    config, manifests, _ = _context(args)
    company = config.company(args.cik)
    run_id = args.filings_run_id or manifests.latest_filing_run_id()
    filing_manifest = manifests.read_filing_manifest(run_id)

    config_hash = config.config_hash()
    run = build_run_metadata("resolve", config_hash, root=config.root)

    _banner(f"RESOLVE  from filing manifest {run_id}  run {run.run_id}")
    filings = filing_manifest.filings
    if args.limit:
        filings = filings[: args.limit]
    print(f"Resolving artifacts for {len(filings)} filing(s)...\n")

    def progress(done: int, total: int, _filing) -> None:
        if done % 10 == 0 or done == total:
            print(f"  resolved {done}/{total}", flush=True)

    with _client(config) as client:
        resolver = ArtifactResolver(client, config.fetch.include)
        artifacts, anomalies = resolver.resolve_all(
            filings,
            company.cik,
            max_workers=config.fetch.http.max_concurrency,
            progress=progress,
        )
        requests_made = client.request_count

    print()
    print(summarize_artifacts(artifacts, anomalies))

    manifest = ArtifactManifest(
        run=run,
        filings_run_id=run_id,
        company_cik10=company.cik10,
        artifacts=artifacts,
        anomalies=anomalies,
    )
    path = manifests.write_artifact_manifest(manifest)
    print(f"\nManifest written (immutable): {path}")

    _write_run_record(
        config,
        RunRecord(
            run=run,
            started_at=run.created_at,
            finished_at=utc_now_iso(),
            counts={"artifacts": len(artifacts), "anomalies": len(anomalies)},
            requests_made=requests_made,
        ),
    )
    return EXIT_OK if artifacts else EXIT_FAILED


def cmd_download(args: argparse.Namespace) -> int:
    config, manifests, store = _context(args)
    company = config.company(args.cik)
    artifacts_run_id = args.artifacts_run_id or manifests.latest_artifact_run_id()
    artifact_manifest = manifests.read_artifact_manifest(artifacts_run_id)
    filing_manifest = manifests.read_filing_manifest(artifact_manifest.filings_run_id)

    run = build_run_metadata("download", config.config_hash(), root=config.root)
    _banner(f"DOWNLOAD  from artifact manifest {artifacts_run_id}  run {run.run_id}")

    filings = filing_manifest.filings
    if args.limit:
        filings = filings[: args.limit]
    wanted = {f.filing_id for f in filings}
    artifacts = [a for a in artifact_manifest.artifacts if a.filing_id in wanted]
    print(f"{len(filings)} filing(s), {len(artifacts)} artifact(s)\n")

    def progress(done: int, total: int, outcome) -> None:
        if done % 5 == 0 or done == total or outcome.status == "failed":
            print(f"  {done}/{total}  {outcome.accession}  {outcome.status}", flush=True)

    with _client(config) as client:
        downloader = ArtifactDownloader(client, store, company, run_id=run.run_id)
        summary = downloader.download_all(
            filings,
            artifacts,
            max_workers=config.fetch.http.max_concurrency,
            force=args.force,
            progress=progress,
        )
        requests_made = client.request_count

    print()
    print(summary.render())
    print(f"Requests made: {requests_made}")

    _write_run_record(
        config,
        RunRecord(
            run=run,
            started_at=run.created_at,
            finished_at=utc_now_iso(),
            counts={
                "downloaded": summary.count("downloaded"),
                "repaired": summary.count("repaired"),
                "skipped": summary.count("skipped"),
                "failed": summary.count("failed"),
            },
            bytes_downloaded=summary.bytes_downloaded,
            requests_made=requests_made,
            errors=[f"{o.accession}: {o.reason}" for o in summary.failures],
        ),
    )
    return EXIT_FAILED if summary.failures else EXIT_OK


def cmd_build_catalog(args: argparse.Namespace) -> int:
    config, _, store = _context(args)
    _banner("BUILD_CATALOG")
    result = CatalogBuilder(store, config.catalog_root).build()
    print(result.render())
    return EXIT_OK if result.ok else EXIT_FAILED


def cmd_verify(args: argparse.Namespace) -> int:
    config, manifests, store = _context(args)
    artifacts_run_id = args.artifacts_run_id or manifests.latest_artifact_run_id()
    artifact_manifest = manifests.read_artifact_manifest(artifacts_run_id)
    filings_run_id = args.filings_run_id or artifact_manifest.filings_run_id
    filing_manifest = manifests.read_filing_manifest(filings_run_id)

    _banner(f"VERIFY  filings={filings_run_id}  artifacts={artifacts_run_id}")
    verifier = CorpusVerifier(store, config.catalog_root, check_hashes=not args.skip_hashes)
    report = verifier.verify(filing_manifest, artifact_manifest)
    print(report.render())
    return EXIT_OK if report.ok else EXIT_FAILED


def cmd_report(args: argparse.Namespace) -> int:
    config, manifests, _ = _context(args)
    artifact_manifest = None
    try:
        run_id = args.artifacts_run_id or manifests.latest_artifact_run_id()
        artifact_manifest = manifests.read_artifact_manifest(run_id)
    except FileNotFoundError:
        run_id = "no-manifest"

    _banner("CORPUS REPORT")
    text = build_corpus_report(config.catalog_root, artifact_manifest)
    config.reports_root.mkdir(parents=True, exist_ok=True)
    path = config.reports_root / f"{run_id}-corpus.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nWritten to {path}")
    return EXIT_OK


def cmd_acquire(args: argparse.Namespace) -> int:
    for stage in (cmd_discover, cmd_resolve, cmd_download, cmd_build_catalog, cmd_verify):
        code = stage(args)
        if code != EXIT_OK:
            print(f"\nStopping: {stage.__name__} returned {code}", file=sys.stderr)
            return code
        # Later stages default to the manifests the earlier ones just wrote.
        args.filings_run_id = None
        args.artifacts_run_id = None
    return cmd_report(args)


# --------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m acquisition",
        description="SEC filing acquisition for the financial knowledge graph prototype.",
    )
    parser.add_argument("--root", help="Repository root (default: auto-detect)")
    parser.add_argument("--cik", help="CIK to operate on (default: the only configured one)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler, filings_run_id=None, artifacts_run_id=None,
                         force=False, limit=None, skip_hashes=False, include_amendments=True)
        return sub

    discover = add("discover", cmd_discover, "Build the filing manifest")
    discover.add_argument(
        "--no-amendments", dest="include_amendments", action="store_false",
        help="Exclude /A amendments of configured forms",
    )

    resolve = add("resolve", cmd_resolve, "Build the artifact manifest")
    resolve.add_argument("--filings-run-id")
    resolve.add_argument("--limit", type=int)

    download = add("download", cmd_download, "Download artifacts and finalize filings")
    download.add_argument("--artifacts-run-id")
    download.add_argument("--limit", type=int)
    download.add_argument(
        "--force", action="store_true", help="Re-download even if already finalized"
    )

    add("build-catalog", cmd_build_catalog, "Rebuild filings.jsonl and artifacts.jsonl")

    verify = add("verify", cmd_verify, "Verify the corpus against its manifests")
    verify.add_argument("--filings-run-id")
    verify.add_argument("--artifacts-run-id")
    verify.add_argument(
        "--skip-hashes", action="store_true", help="Skip re-hashing (faster, weaker)"
    )

    report = add("report", cmd_report, "Write the corpus report")
    report.add_argument("--artifacts-run-id")

    acquire = add("acquire", cmd_acquire, "Run every stage in order")
    acquire.add_argument("--limit", type=int)
    acquire.add_argument(
        "--no-amendments", dest="include_amendments", action="store_false"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (FileNotFoundError, FileExistsError, ValueError, KeyError) as exc:
        print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
