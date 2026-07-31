"""Command-line entry points.

    python -m normalization select
    python -m normalization normalize   [--fixtures FILE] [--mode MODE] [--limit N]
    python -m normalization build-catalog
    python -m normalization verify
    python -m normalization report
    python -m normalization spike       [--mode comparison]   # fixtures + full pipeline
    python -m normalization run                                # all 294 selected

Parses, renders, maps exit codes. No parsing, storage, or ordering logic of its own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .context import NormalizationContext, build_normalization_context
from .core.models import NormalizationRun
from .core.runmeta import (
    NORMALIZER_VERSION,
    code_commit,
    dependency_versions,
    environment,
    make_run_id,
    utc_now_iso,
)
from .pipeline import NormalizationRequest
from .stages.catalog import CatalogRequest
from .stages.report import ReportRequest
from .stages.select import SelectRequest
from .stages.select.catalog_selection import render_summary as render_selection
from .stages.verify import VerifyRequest
from .utils.jsonl import write_jsonl

EXIT_OK = 0
EXIT_FAILED = 1
DEFAULT_FIXTURES = "plans/normalization/spike_fixtures.txt"


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _context(args: argparse.Namespace, run_id: str) -> NormalizationContext:
    return build_normalization_context(
        Path(args.root) if args.root else None, run_id=run_id
    )


def _run_id(context: NormalizationContext) -> str:
    return make_run_id(context.config.config_hash())


def _read_fixtures(root: Path, path: str) -> list[str]:
    target = Path(path) if Path(path).is_absolute() else root / path
    if not target.is_file():
        raise FileNotFoundError(f"Fixture list not found: {target}")
    return [
        line.strip()
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _progress(done: int, total: int, artifact_id: str) -> None:
    if done % 5 == 0 or done == total:
        print(f"  {done}/{total}  {artifact_id.split(':')[-1][:48]}", flush=True)


def _write_run(context: NormalizationContext, run_id: str, mode: str, result) -> Path:
    normalize = getattr(result, "normalize", None) or result
    documents = getattr(normalize, "documents", [])
    issues = getattr(normalize, "issues", [])
    run = NormalizationRun(
        run_id=run_id,
        stage="pipeline",
        created_at=utc_now_iso(),
        finished_at=utc_now_iso(),
        config_hash=context.config.config_hash(),
        selection_policy_version=context.config.policy.version,
        parser_name=context.default_parser.name,
        parser_version=context.default_parser.version,
        normalizer_version=NORMALIZER_VERSION,
        passage_strategy_name=context.passage_strategy.name,
        passage_strategy_version=context.passage_strategy.version,
        parser_mode=mode,
        code_commit=code_commit(context.config.root),
        **environment(),
        dependency_versions=dependency_versions(),
        counts={
            "documents": len(documents),
            "issues": len(issues),
            "errors": len([i for i in issues if i.severity == "error"]),
        },
        produced_document_ids=sorted(d.document_id for d in documents),
        fallback_document_ids=getattr(normalize, "fallbacks", []),
        errors=[i.detail for i in issues if i.severity == "error"],
    )
    context.config.runs_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        context.config.runs_root / f"{run_id}-issues.jsonl",
        [i.model_dump(mode="json") for i in issues],
    )
    return context.manifests.write_run_manifest(run)


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def cmd_select(args: argparse.Namespace) -> int:
    context = _context(args, "select")
    run_id = _run_id(context)
    _banner(f"SELECT  policy {context.config.policy.version}  run {run_id}")
    artifact_ids = _read_fixtures(context.config.root, args.fixtures) if args.fixtures else None
    result = context.select.run(SelectRequest(run_id=run_id, artifact_ids=artifact_ids))
    print(render_selection(result))
    print(f"\nfor processing: {len(result.for_processing)}  (include {len(result.included)}"
          f" + needs_review {len(result.needs_review)})")
    print(f"Selection manifest: {result.manifest_path}")
    return EXIT_OK if result.ok else EXIT_FAILED


def cmd_pipeline(args: argparse.Namespace, *, fixtures: str | None, mode: str) -> int:
    context = _context(args, "pipeline")
    run_id = _run_id(context)
    artifact_ids = _read_fixtures(context.config.root, fixtures) if fixtures else None
    context.store.run_id = run_id

    _banner(f"NORMALIZE  run {run_id}  mode {mode}  "
            f"parser {context.default_parser.name}/{context.fallback_parser.name}")
    if artifact_ids:
        print(f"Fixture set: {len(artifact_ids)} artifact(s)\n")

    result = context.pipeline.run(
        NormalizationRequest(
            run_id=run_id,
            artifact_ids=artifact_ids,
            mode=mode,
            check_hashes=not args.skip_hashes,
            normalize_progress=_progress,
            on_stage_start=lambda name: print(f"\n--- {name} ---"),
        )
    )

    if result.select:
        print(f"selected for processing: {len(result.select.for_processing)}")
    if result.normalize:
        n = result.normalize
        print(f"documents: {len(n.documents)}  issues: {len(n.issues)}  "
              f"errors: {len(n.failed)}  fallbacks: {len(n.fallbacks)}")
        for issue in n.failed[:10]:
            print(f"  ERROR {issue.artifact_id.split(':')[-1]}: {issue.detail}")
    if result.catalog:
        print(result.catalog.render())
    if result.verify:
        print(result.verify.report.render())
    if result.report:
        print(f"\ncorpus report : {result.report.corpus_path}")
        print(f"review report : {result.report.review_path}")
        if result.report.comparison_path:
            print(f"comparison    : {result.report.comparison_path}")

    _write_run(context, run_id, mode, result)
    if not result.ok:
        print(f"\nStopping: stage '{result.failed_stage}' failed", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def cmd_normalize(args: argparse.Namespace) -> int:
    return cmd_pipeline(args, fixtures=args.fixtures, mode=args.mode)


def cmd_spike(args: argparse.Namespace) -> int:
    return cmd_pipeline(args, fixtures=args.fixtures or DEFAULT_FIXTURES, mode=args.mode)


def cmd_run(args: argparse.Namespace) -> int:
    """Full corpus. Deliberately a separate command from `spike`."""
    return cmd_pipeline(args, fixtures=None, mode=args.mode)


def cmd_build_catalog(args: argparse.Namespace) -> int:
    context = _context(args, "catalog")
    _banner("BUILD_CATALOG")
    result = context.catalog.run(CatalogRequest())
    print(result.render())
    return EXIT_OK if result.ok else EXIT_FAILED


def cmd_verify(args: argparse.Namespace) -> int:
    context = _context(args, "verify")
    _banner("VERIFY")
    result = context.verify.run(
        VerifyRequest(selection_run_id=args.selection_run_id, check_hashes=not args.skip_hashes)
    )
    print(result.report.render())
    return EXIT_OK if result.ok else EXIT_FAILED


def cmd_report(args: argparse.Namespace) -> int:
    context = _context(args, "report")
    _banner("REPORT")
    documents = []
    for path in context.store.iter_documents():
        document = context.store.read_document(path)
        if document is not None:
            documents.append(document)
    run_id = args.selection_run_id or _run_id(context)
    result = context.report.run(ReportRequest(run_id=run_id, documents=documents))
    print(f"corpus report : {result.corpus_path}")
    print(f"review report : {result.review_path}")
    return EXIT_OK


# --------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m normalization",
        description="Document normalization for the financial knowledge graph prototype.",
    )
    parser.add_argument("--root", help="Repository root (default: auto-detect)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, help_text: str):
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler, fixtures=None, mode="normal",
                         skip_hashes=False, selection_run_id=None, limit=None)
        return sub

    select = add("select", cmd_select, "Apply the selection policy and write its manifest")
    select.add_argument("--fixtures", help="Restrict to a fixture list file")

    normalize = add("normalize", cmd_normalize, "Run the pipeline over selected artifacts")
    normalize.add_argument("--fixtures")
    normalize.add_argument("--mode", choices=["normal", "fallback", "comparison"], default="normal")
    normalize.add_argument("--skip-hashes", action="store_true")

    spike = add("spike", cmd_spike, "Run the pipeline over the spike fixture set")
    spike.add_argument("--fixtures")
    spike.add_argument("--mode", choices=["normal", "fallback", "comparison"], default="normal")
    spike.add_argument("--skip-hashes", action="store_true")

    full = add("run", cmd_run, "Run the pipeline over the whole selected corpus")
    full.add_argument("--mode", choices=["normal", "fallback", "comparison"], default="normal")
    full.add_argument("--skip-hashes", action="store_true")

    add("build-catalog", cmd_build_catalog, "Rebuild the derived catalogs")

    verify = add("verify", cmd_verify, "Verify the normalized corpus")
    verify.add_argument("--selection-run-id")
    verify.add_argument("--skip-hashes", action="store_true")

    report = add("report", cmd_report, "Write corpus and review reports")
    report.add_argument("--selection-run-id")
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
