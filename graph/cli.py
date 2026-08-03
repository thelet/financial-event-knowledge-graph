"""Command-line entry point.

    python -m graph [--root REPO] [--runs-root R] project RUN [--catalog-directory D]
    python -m graph [--root REPO] [--runs-root R] inspect [GRAPH_RUN_ID]
    python -m graph [--root REPO] [--runs-root R] runs
    python -m graph load   EXPORT [--replace]
    python -m graph verify EXPORT

`--root` and `--runs-root` are **top-level** options and must precede the subcommand;
`argparse` rejects them after it, and this docstring used to show exactly that unusable
form. `--catalog-directory` belongs to `project` and follows it, `--replace` to `load`.

`RUN` is either an extraction run directory or a bare run id under `data/extraction_runs/`.
`EXPORT` is either a graph run directory or a bare `graph_run_id` under `data/graph_runs/`;
`load` and `verify` read the connection from `config/graph.yaml` plus `.env` (§5.1), which is
why they take neither `--root` nor `--runs-root`. `inspect` prints one projection's manifest;
`runs` lists the finished projections under `data/graph_runs/`.

Parses, renders, maps exit codes. No projection, key, catalog or validation logic of its own —
every verb is one call into `pipeline` or one read of a finished directory.

**A projection that rejected a row exits non-zero**, per §6.4. The rejected rows are printed
and left in `<graph_run_id>.rejected/rejected.jsonl` — beside the run id, never on top of a
complete projection — and that directory carries no `manifest.json`, so nothing downstream
can mistake it for a finished projection.

**`load` and `verify` exist** *(added 2026-08-03 after review; this docstring previously said
they were "G2 verbs and absent rather than stubbed", while `lifecycle.py` was already telling
operators to "re-run with `--replace`" — a flag with no implementation anywhere, and §10's whole
verification sweep had no caller outside the test suite)*. Both exit non-zero on a refusal:
`load` on a run conflict, a short write or a failed post-load check; `verify` on any of §10's
criteria. A `verify` failure prints every failing check rather than the first, because a load
that went wrong in two ways is exactly the case a first-failure exit would hide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context import build_graph_context
from .core.inputs import GraphInputError
from .pipeline import load, project, raise_for_failures, verify
from .stages.load.connection import GraphConnectionError
from .stages.load.lifecycle import GraphLifecycleError
from .stages.load.loader import GraphLoadError
from .stages.load.reader import GraphExportReadError
from .stages.projection.export import MANIFEST_FILENAME

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _context(args):
    return build_graph_context(
        Path(args.root) if args.root else None,
        graph_runs_root=Path(args.runs_root) if args.runs_root else None)


def _finished(root: Path) -> list[str]:
    """Projections that finished. A directory with no manifest did not (§6.5)."""
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir()
                  if entry.is_dir() and (entry / MANIFEST_FILENAME).is_file())


def cmd_project(args) -> int:
    context = _context(args)
    outcome = project(
        context, args.run,
        catalog_directory=Path(args.catalog_directory) if args.catalog_directory else None)

    print(f"graph run   {outcome.graph_run_id}")
    print(f"directory   {outcome.directory}")
    if not outcome.ok:
        print(f"\nREJECTED {len(outcome.rejections)} row(s) — see "
              f"{outcome.directory}/rejected.jsonl")
        for row in outcome.rejections:
            print(f"  {row.code}: {row.detail}")
        return EXIT_FAILED

    counts = outcome.manifest["counts"]
    print(f"nodes       {counts['nodes']}")
    for label, count in counts["nodes_by_label"].items():
        print(f"  :{label:<12} {count}")
    print(f"edges       {counts['edges']}")
    for edge_type, count in counts["edges_by_type"].items():
        print(f"  {edge_type:<24} {count}")
    reconciliation = outcome.manifest["warning_reconciliation"]
    print(f"warnings    derived {reconciliation['derived']}, manifest "
          f"{reconciliation['manifest_total']}, agreed {reconciliation['agreed']}, "
          f"whole run {reconciliation['covers_whole_run']}")
    for name, sha in sorted(outcome.manifest["artifacts"].items()):
        print(f"  {name:<12} {sha}")
    print(f"written     {', '.join(outcome.write_order)}")
    return EXIT_OK


def cmd_runs(args) -> int:
    root = _context(args).graph_runs_root
    finished = _finished(root)
    if not finished:
        print(f"no finished projection under {root}")
        return EXIT_OK
    for name in finished:
        print(name)
    return EXIT_OK


def cmd_inspect(args) -> int:
    root = _context(args).graph_runs_root
    if args.graph_run_id:
        directory = root / args.graph_run_id
    else:
        finished = _finished(root)
        if len(finished) != 1:
            print(f"name one of {finished or 'no finished projections'} under {root}",
                  file=sys.stderr)
            return EXIT_USAGE
        directory = root / finished[0]
    path = directory / MANIFEST_FILENAME
    if not path.is_file():
        print(f"{path} does not exist; that projection did not finish", file=sys.stderr)
        return EXIT_FAILED
    print(json.dumps(json.loads(path.read_text(encoding="utf-8")),
                     indent=2, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


def cmd_load(args) -> int:
    """`graph load EXPORT [--replace]`. §6.2's refusal is the interesting exit, not the success."""
    outcome = load(args.export, replace=args.replace)

    print(f"graph run   {outcome.graph_run_id}")
    print(f"target      {outcome.target.uri} / {outcome.target.database}")
    print(f"decision    {outcome.decision.action} — {outcome.decision.reason}")
    if outcome.wipe is not None:
        print(f"wiped       {outcome.wipe.nodes_deleted} nodes, "
              f"{outcome.wipe.relationships_deleted} relationships")
    print(f"nodes       {outcome.result.nodes_written} in "
          f"{outcome.result.label_set_count} label sets")
    print(f"edges       {outcome.result.edges_written} in "
          f"{outcome.result.edge_group_count} groups")
    print(f"schema      {len(outcome.schema.managed_node_constraints())} node constraints, "
          f"{len(outcome.schema.managed_relationship_constraints())} relationship constraints, "
          f"{len(outcome.schema.managed_indexes())} indexes")
    print(f"marker      {outcome.marker.status} at {outcome.marker.completed_at}")
    # `complete` is written by `complete_load` after everything else returned success, so this
    # is a read of the database's own answer rather than of what this process believes.
    return EXIT_OK if outcome.complete else EXIT_FAILED


def cmd_verify(args) -> int:
    """`graph verify EXPORT`. Prints every check, exits non-zero if any failed (§10)."""
    report = verify(args.export)
    print(report.describe())
    try:
        raise_for_failures(report)
    except GraphLifecycleError:
        # The message repeats what `describe()` just printed check by check, so it is swallowed
        # here and only the exit code carries the verdict onwards.
        return EXIT_FAILED
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m graph", description=__doc__)
    parser.add_argument("--root", help="repository root (default: found from config/)")
    parser.add_argument("--runs-root", help="where graph runs are written "
                                            "(default: data/graph_runs)")
    sub = parser.add_subparsers(dest="command", required=True)

    projected = sub.add_parser("project", help="project one extraction run into JSONL")
    projected.add_argument("run", help="extraction run directory or run id")
    projected.add_argument("--catalog-directory",
                           help="normalization catalog (default: found beside the run)")
    projected.set_defaults(handler=cmd_project)

    listed = sub.add_parser("runs", help="list finished projections")
    listed.set_defaults(handler=cmd_runs)

    inspected = sub.add_parser("inspect", help="print one projection's manifest")
    inspected.add_argument("graph_run_id", nargs="?")
    inspected.set_defaults(handler=cmd_inspect)

    loaded = sub.add_parser("load", help="load one export into Neo4j (§6.2, §6.3)")
    loaded.add_argument("export", help="graph run directory or graph_run_id")
    loaded.add_argument(
        "--replace", action="store_true",
        help="wipe the database first. The only thing that authorises destroying data: "
             "config/graph.yaml cannot, and absence never does (§6.2)")
    loaded.set_defaults(handler=cmd_load)

    verified = sub.add_parser("verify", help="reconcile the loaded graph against an export (§10)")
    verified.add_argument("export", help="graph run directory or graph_run_id")
    verified.set_defaults(handler=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        GraphInputError,
        # The load stage's four refusal families, all of which already name the run, the key or
        # the target they are about: an unresolved or unreachable target, a run conflict or a
        # failed post-load check, a short write, and a malformed export row.
        GraphConnectionError,
        GraphLifecycleError,
        GraphLoadError,
        GraphExportReadError,
    ) as exc:
        # Reported as one line rather than a traceback because each one already names the
        # id and the field it is about.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except FileNotFoundError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "build_parser", "main"]
