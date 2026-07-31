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

This module parses arguments, builds requests, calls one stage or the pipeline, renders
output, and maps results to exit codes. It performs no SEC calls, no storage operations, no
catalog or verification logic, and owns no stage ordering.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .context import AcquisitionContext, build_acquisition_context
from .pipeline import AcquisitionRequest
from .stages.catalog import CatalogRequest
from .stages.discover import DiscoverRequest
from .stages.discover import summarize as summarize_filings
from .stages.download import DownloadRequest
from .stages.report import ReportRequest
from .stages.resolve import ResolveRequest
from .stages.resolve import summarize as summarize_artifacts
from .stages.verify import VerifyRequest

EXIT_OK = 0
EXIT_FAILED = 1


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _resolve_progress(done: int, total: int, _filing) -> None:
    if done % 10 == 0 or done == total:
        print(f"  resolved {done}/{total}", flush=True)


def _download_progress(done: int, total: int, outcome) -> None:
    if done % 5 == 0 or done == total or outcome.status == "failed":
        print(f"  {done}/{total}  {outcome.accession}  {outcome.status}", flush=True)


def _render_discover(result) -> None:
    print(summarize_filings(result.filings))
    print(f"\nManifest written (immutable): {result.manifest_path}")
    print(
        f"config_hash={result.config_hash[:16]}  "
        f"manifest_hash={result.manifest_hash[:16]}"
    )


def _render_resolve(result) -> None:
    print()
    print(summarize_artifacts(result.artifacts, result.anomalies))
    print(f"\nManifest written (immutable): {result.manifest_path}")


def _render_download(result) -> None:
    print()
    print(result.summary.render())
    print(f"Requests made: {result.requests_made}")


def _context(args: argparse.Namespace) -> AcquisitionContext:
    return build_acquisition_context(Path(args.root) if args.root else None)


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> int:
    with _context(args) as context:
        company = context.config.company(args.cik)
        _banner(f"DISCOVER  {company.name}  CIK {company.cik10}")
        print(
            f"Forms: {', '.join(context.config.fetch.forms)}   "
            f"Dates: {context.config.fetch.date_from} .. "
            f"{context.config.fetch.effective_date_to()}   "
            f"Amendments: {'included' if args.include_amendments else 'excluded'}\n"
        )
        result = context.discover.run(
            DiscoverRequest(cik=args.cik, include_amendments=args.include_amendments)
        )
        _render_discover(result)
        return EXIT_OK if result.ok else EXIT_FAILED


def cmd_resolve(args: argparse.Namespace) -> int:
    with _context(args) as context:
        _banner("RESOLVE")
        result = context.resolve.run(
            ResolveRequest(
                cik=args.cik,
                filings_run_id=args.filings_run_id,
                limit=args.limit,
                progress=_resolve_progress,
            )
        )
        print(f"Resolved artifacts for {result.filing_count} filing(s).")
        _render_resolve(result)
        return EXIT_OK if result.ok else EXIT_FAILED


def cmd_download(args: argparse.Namespace) -> int:
    with _context(args) as context:
        _banner("DOWNLOAD")
        result = context.download.run(
            DownloadRequest(
                cik=args.cik,
                artifacts_run_id=args.artifacts_run_id,
                limit=args.limit,
                force=args.force,
                progress=_download_progress,
            )
        )
        print(f"{result.filing_count} filing(s), {result.artifact_count} artifact(s)")
        _render_download(result)
        return EXIT_OK if result.ok else EXIT_FAILED


def cmd_build_catalog(args: argparse.Namespace) -> int:
    with _context(args) as context:
        _banner("BUILD_CATALOG")
        result = context.catalog.run(CatalogRequest())
        print(result.render())
        return EXIT_OK if result.ok else EXIT_FAILED


def cmd_verify(args: argparse.Namespace) -> int:
    with _context(args) as context:
        result = context.verify.run(
            VerifyRequest(
                filings_run_id=args.filings_run_id,
                artifacts_run_id=args.artifacts_run_id,
                check_hashes=not args.skip_hashes,
            )
        )
        _banner(
            f"VERIFY  filings={result.filings_run_id}  artifacts={result.artifacts_run_id}"
        )
        print(result.report.render())
        return EXIT_OK if result.ok else EXIT_FAILED


def cmd_report(args: argparse.Namespace) -> int:
    with _context(args) as context:
        _banner("CORPUS REPORT")
        result = context.report.run(ReportRequest(artifacts_run_id=args.artifacts_run_id))
        print(result.text)
        print(f"\nWritten to {result.path}")
        return EXIT_OK if result.ok else EXIT_FAILED


def cmd_acquire(args: argparse.Namespace) -> int:
    """Delegates the entire flow to the pipeline. No ordering lives here."""
    with _context(args) as context:
        renderers = {
            "discover": _render_discover,
            "resolve": _render_resolve,
            "download": _render_download,
            "build-catalog": lambda r: print(r.render()),
            "verify": lambda r: print(r.report.render()),
            "report": lambda r: print(f"Report written to {r.path}"),
        }
        result = context.pipeline.run(
            AcquisitionRequest(
                cik=args.cik,
                include_amendments=args.include_amendments,
                limit=args.limit,
                resolve_progress=_resolve_progress,
                download_progress=_download_progress,
                on_stage_start=lambda name: _banner(name.upper()),
                on_stage_finish=lambda name, outcome: renderers[name](outcome),
            )
        )
        if not result.ok:
            print(f"\nStopping: stage '{result.failed_stage}' failed", file=sys.stderr)
            return EXIT_FAILED
        return EXIT_OK


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
        sub.set_defaults(
            handler=handler,
            filings_run_id=None,
            artifacts_run_id=None,
            force=False,
            limit=None,
            skip_hashes=False,
            include_amendments=True,
        )
        return sub

    discover = add("discover", cmd_discover, "Build the filing manifest")
    discover.add_argument(
        "--no-amendments",
        dest="include_amendments",
        action="store_false",
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
