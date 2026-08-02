"""Command-line entry points.

    python -m extraction run           [--lanes L,...] [--limit N] [--documents ID,...]
    python -m extraction runs                                   # list finished runs
    python -m extraction inspect       [RUN_ID]                 # the manifest, readably
    python -m extraction claim         ID     [--run RUN_ID]    # one claim, with its evidence
    python -m extraction filter        [--metric M] [--event E] [--relationship R]
                                       [--document D] [--case-passage P] [--limit N]
    python -m extraction issues        [--code C] [--lane L] [--limit N]
    python -m extraction rejected      [--code C] [--limit N]
    python -m extraction report        [RUN_ID] [--write]       # regenerate report.md
    python -m extraction rebuild       [RUN_ID]                 # rebuild catalogs, compare bytes

Parses, renders, maps exit codes. No selection, extraction, catalog or verification logic of
its own — every verb here is one call into `pipeline` or one read of a finished run directory.

**A run whose self-verification failed exits non-zero.** The checks exist to be believed; a
green exit code over a red check would teach a reader to skip the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context import build_extraction_context
from .core.config import load_config
from .core.run_directory import IncompleteRunError, RunDirectory, list_runs
from .pipeline import (
    load_lane_outputs,
    rebuild_catalogs,
    regenerate_report,
    run as run_pipeline,
    run_id_for,
)
from .stages.catalog import CATALOG_FILES, CLAIMS, EVENTS, EVIDENCE, ISSUES, OBSERVATIONS
from .stages.catalog.public import REJECTED, RELATIONSHIPS
from .stages.extract import LANES, ExtractRequest
from .stages.report import REPORT_FILENAME

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _progress(lane: str, done: int, total: int) -> None:
    if done % 500 == 0 or done == total:
        print(f"  {lane}: {done}/{total}", flush=True)


def _runs_root(args) -> Path:
    return load_config(Path(args.root) if args.root else None).runs_root


def _resolve_run(args) -> Path:
    """The named run, or the only finished one, or an error naming what is available."""
    root = _runs_root(args)
    if getattr(args, "run", None):
        return root / args.run
    if getattr(args, "run_id", None):
        return root / args.run_id
    finished = list_runs(root)
    if len(finished) == 1:
        return root / finished[0]
    if not finished:
        raise FileNotFoundError(f"no finished run under {root}; run `python -m extraction run`")
    raise ValueError(
        f"{len(finished)} finished runs under {root}; name one: {', '.join(finished)}")


def _rows(directory: RunDirectory, name: str) -> list[dict]:
    return directory.rows(name)


# -- verbs ---------------------------------------------------------------------------------


def cmd_run(args) -> int:
    context = build_extraction_context(
        Path(args.root) if args.root else None, progress=_progress)
    _banner(f"extraction run {run_id_for(context)}")
    print(f"  corpus     {context.corpus.identity().corpus_id}")
    print(f"  ontology   {context.ontology.definition_hash[:16]}…")
    print(f"  scope      {context.scoping.strategy}")
    print(f"  answers    {len(context.answers)} recorded, replay only")
    outcome = run_pipeline(context, ExtractRequest(
        lanes=tuple(args.lanes.split(",")) if args.lanes else LANES,
        document_ids=tuple(args.documents.split(",")) if args.documents else None,
        limit=args.limit))
    _banner("catalogs")
    for name, count in outcome.catalogs.counts().items():
        print(f"  {count:>8}  {name}")
    _banner("verification")
    for check in outcome.verification.checks:
        mark = "ok " if check.passed else "FAIL"
        print(f"  {mark}  {check.check:<24} examined {check.examined:>6}  "
              f"failures {len(check.failures):>4}  warnings {len(check.warnings):>4}")
    print(f"\n  {outcome.path}")
    return EXIT_OK if outcome.ok else EXIT_FAILED


def cmd_runs(args) -> int:
    root = _runs_root(args)
    finished = list_runs(root)
    if not finished:
        print(f"no finished run under {root}")
        return EXIT_OK
    for run_id in finished:
        manifest = RunDirectory(root / run_id).manifest()
        print(f"{run_id}  corpus {manifest['corpus']['corpus_id']}  "
              f"claims {manifest['counts']['claims']}  "
              f"scope {manifest['scope']['strategy']}  "
              f"verified {'yes' if all(c['passed'] for c in manifest['verification']) else 'NO'}")
    return EXIT_OK


def cmd_inspect(args) -> int:
    directory = RunDirectory(_resolve_run(args))
    manifest = directory.manifest()
    _banner(f"run {manifest['run_id']}")
    for key in ("extractor_version", "layout_version", "config_hash", "code_commit",
                "created_at", "ontology_id", "ontology_definition_hash"):
        print(f"  {key:<26} {manifest[key]}")
    for block in ("corpus", "scope", "provider", "bounds", "counts"):
        _banner(block)
        for key, value in sorted(manifest[block].items()):
            print(f"  {key:<34} {value}")
    _banner("verification")
    for check in manifest["verification"]:
        print(f"  {'ok ' if check['passed'] else 'FAIL'}  {check['check']:<24} "
              f"examined {check['examined']:>6}  failures {check['failures']:>4}  "
              f"warnings {check['warnings']:>4}")
    changed = directory.unchanged()
    _banner("files")
    for name in sorted(directory.files):
        print(f"  {'CHANGED' if name in changed else 'ok     '}  {name}")
    return EXIT_OK if not changed else EXIT_FAILED


def cmd_claim(args) -> int:
    directory = RunDirectory(_resolve_run(args))
    claims = {row["claim_id"]: row for row in _rows(directory, CLAIMS)}
    row = claims.get(args.claim_id)
    if row is None:
        print(f"no claim {args.claim_id} in {directory.run_id}")
        return EXIT_FAILED
    _banner(row["claim_id"])
    print(json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False))
    payload_file = {"metric_observation": OBSERVATIONS, "event": EVENTS,
                    "relationship": RELATIONSHIPS}[row["claim_kind"]]
    for payload in _rows(directory, payload_file):
        if payload.get("claim_id") == row["claim_id"]:
            _banner(payload_file)
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    _banner(EVIDENCE)
    for reference in _rows(directory, EVIDENCE):
        if reference.get("claim_id") == row["claim_id"]:
            print(json.dumps(reference, indent=2, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


def cmd_filter(args) -> int:
    """Claims narrowed by any of the five axes. Every filter is an `and`."""
    directory = RunDirectory(_resolve_run(args))
    payloads: list[tuple[str, dict]] = []
    if args.metric:
        payloads += [(OBSERVATIONS, row) for row in _rows(directory, OBSERVATIONS)
                     if row.get("metric_id") == args.metric]
    if args.event:
        payloads += [(EVENTS, row) for row in _rows(directory, EVENTS)
                     if row.get("event_type_id") == args.event]
    if args.relationship:
        payloads += [(RELATIONSHIPS, row) for row in _rows(directory, RELATIONSHIPS)
                     if row.get("relationship_id") == args.relationship]
    if not payloads:
        payloads = [(name, row) for name in (OBSERVATIONS, EVENTS, RELATIONSHIPS)
                    for row in _rows(directory, name)]
    if args.document:
        payloads = [(name, row) for name, row in payloads
                    if args.document in str(row.get("document_id") or "")]
    if args.passage:
        payloads = [(name, row) for name, row in payloads
                    if args.passage in str(row.get("passage_id") or "")]
    print(f"{len(payloads)} payloads in {directory.run_id}")
    for name, row in payloads[:args.limit]:
        print(f"\n-- {name}")
        print(json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


def cmd_issues(args) -> int:
    directory = RunDirectory(_resolve_run(args))
    rows = _rows(directory, ISSUES)
    if args.code:
        rows = [row for row in rows if row.get("code") == args.code]
    if args.lane:
        rows = [row for row in rows if row.get("lane") == args.lane]
    census: dict[str, int] = {}
    for row in rows:
        census[str(row.get("code"))] = census.get(str(row.get("code")), 0) + 1
    _banner(f"{len(rows)} issues in {directory.run_id}")
    for code, count in sorted(census.items()):
        print(f"  {count:>8}  {code}")
    for row in rows[:args.limit]:
        print(f"\n  {row['code']}  {row['lane']}  {row['passage_id']}\n    {row['detail']}")
    return EXIT_OK


def cmd_rejected(args) -> int:
    directory = RunDirectory(_resolve_run(args))
    rows = _rows(directory, REJECTED)
    if args.code:
        rows = [row for row in rows if row.get("code") == args.code]
    _banner(f"{len(rows)} refused payloads in {directory.run_id}")
    for row in rows[:args.limit]:
        print(json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


def cmd_report(args) -> int:
    from ontology import load_ontology

    path = _resolve_run(args)
    markdown = regenerate_report(path, ontology=load_ontology())
    existing = (path / REPORT_FILENAME).read_text(encoding="utf-8")
    identical = markdown == existing
    print(f"regenerated {len(markdown)} characters; "
          f"{'byte-identical to' if identical else 'DIFFERS from'} the committed "
          f"{REPORT_FILENAME}")
    if args.write and not identical:
        (path / REPORT_FILENAME).write_text(markdown, encoding="utf-8", newline="\n")
        print(f"wrote {path / REPORT_FILENAME}")
    return EXIT_OK if identical or args.write else EXIT_FAILED


def cmd_rebuild(args) -> int:
    """Rebuild the catalogs from the persisted lane outputs and compare bytes.

    The claim STAGE_13 §7 asks a run to demonstrate rather than assert, as a verb anyone can
    run against a finished directory.
    """
    from ontology import load_ontology

    context = build_extraction_context(Path(args.root) if args.root else None)
    path = _resolve_run(args)
    rebuilt = rebuild_catalogs(path, ontology=load_ontology(), corpus=context.corpus)
    _banner(f"rebuild {path.name}")
    differences = 0
    for name in CATALOG_FILES:
        original = (path / name).read_text(encoding="utf-8")
        same = original == rebuilt[name]
        differences += 0 if same else 1
        print(f"  {'identical' if same else 'DIFFERS  '}  {name:<24} "
              f"{len(rebuilt[name].splitlines()):>7} rows")
    outputs = load_lane_outputs(path)
    print(f"\n  rebuilt from {len(outputs.outcomes)} recorded candidate outcomes")
    return EXIT_OK if differences == 0 else EXIT_FAILED


# -- parsing ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m extraction")
    parser.add_argument("--root", help="repository root; defaults to the one holding config/")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="extract, catalog, verify, report")
    run.add_argument("--lanes", help=f"comma-separated; default {','.join(LANES)}")
    run.add_argument("--limit", type=int, help="candidates per lane, for a smoke run")
    run.add_argument("--documents", help="comma-separated document ids")
    run.set_defaults(func=cmd_run)

    runs = subparsers.add_parser("runs", help="list finished runs")
    runs.set_defaults(func=cmd_runs)

    inspect = subparsers.add_parser("inspect", help="a run's manifest and file digests")
    inspect.add_argument("run_id", nargs="?")
    inspect.set_defaults(func=cmd_inspect)

    claim = subparsers.add_parser("claim", help="one claim, its payload and its evidence")
    claim.add_argument("claim_id")
    claim.add_argument("--run")
    claim.set_defaults(func=cmd_claim)

    filtered = subparsers.add_parser("filter", help="payloads by metric, event, edge, document")
    filtered.add_argument("--metric")
    filtered.add_argument("--event")
    filtered.add_argument("--relationship")
    filtered.add_argument("--document")
    filtered.add_argument("--passage", help="substring of a passage id")
    filtered.add_argument("--limit", type=int, default=10)
    filtered.add_argument("--run")
    filtered.set_defaults(func=cmd_filter)

    issues = subparsers.add_parser("issues", help="recorded refusals and diagnostics")
    issues.add_argument("--code")
    issues.add_argument("--lane")
    issues.add_argument("--limit", type=int, default=10)
    issues.add_argument("--run")
    issues.set_defaults(func=cmd_issues)

    rejected = subparsers.add_parser("rejected", help="payloads a lane or a policy refused")
    rejected.add_argument("--code")
    rejected.add_argument("--limit", type=int, default=10)
    rejected.add_argument("--run")
    rejected.set_defaults(func=cmd_rejected)

    report = subparsers.add_parser("report", help="regenerate report.md and compare bytes")
    report.add_argument("run_id", nargs="?")
    report.add_argument("--write", action="store_true")
    report.set_defaults(func=cmd_report)

    rebuild = subparsers.add_parser("rebuild", help="rebuild catalogs and compare bytes")
    rebuild.add_argument("run_id", nargs="?")
    rebuild.set_defaults(func=cmd_rebuild)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except IncompleteRunError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
