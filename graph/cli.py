"""Command-line entry point.

    python -m graph [--root REPO] [--runs-root R] project RUN [--catalog-directory D]
    python -m graph [--root REPO] [--runs-root R] inspect [GRAPH_RUN_ID]
    python -m graph [--root REPO] [--runs-root R] runs

`--root` and `--runs-root` are **top-level** options and must precede the subcommand;
`argparse` rejects them after it, and this docstring used to show exactly that unusable
form. `--catalog-directory` belongs to `project` and follows it.

`RUN` is either an extraction run directory or a bare run id under `data/extraction_runs/`.
`inspect` prints one projection's manifest; `runs` lists the finished projections under
`data/graph_runs/`.

Parses, renders, maps exit codes. No projection, key, catalog or validation logic of its own —
every verb is one call into `pipeline` or one read of a finished directory.

**A projection that rejected a row exits non-zero**, per §6.4. The rejected rows are printed
and left in `<graph_run_id>.rejected/rejected.jsonl` — beside the run id, never on top of a
complete projection — and that directory carries no `manifest.json`, so nothing downstream
can mistake it for a finished projection. `load` and `verify` are G2 verbs and are absent
rather than stubbed — a verb that printed "not implemented" is a promise the package has not
made.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context import build_graph_context
from .core.inputs import GraphInputError
from .pipeline import project
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except GraphInputError as exc:
        # Every refusal this layer makes is a `GraphInputError` — a dangling endpoint, a
        # duplicate key, a warning count that disagrees, a catalog that changed shape. They
        # are reported as one line rather than a traceback because each one already names the
        # id and the field it is about.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except FileNotFoundError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "build_parser", "main"]
