"""CLI for the extraction benchmark v1.

Four subcommands, one of which writes anything:

    python -m benchmarks.extraction.v1 report            regenerate both reports
    python -m benchmarks.extraction.v1 evaluate          print totals, write nothing
    python -m benchmarks.extraction.v1 case <case_id>    one case in detail
    python -m benchmarks.extraction.v1 claims <case_id>  every emitted observation

`evaluate` exists because the common question during development is "did a number move",
and answering it should not require a dirty working tree.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .runner import MATCH_DIMENSIONS, build_report, write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.extraction.v1",
        description="Score the deterministic table lane against the reviewed table cases.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("report", help="regenerate reports/table_lane_v1.{json,md}")
    subcommands.add_parser("evaluate", help="print totals; write nothing")
    for name, help_text in (("case", "one case in detail"),
                            ("claims", "every emitted observation for one case")):
        sub = subcommands.add_parser(name, help=help_text)
        sub.add_argument("case_id")

    args = parser.parse_args(argv)
    report = build_report()

    if args.command == "report":
        json_path, markdown_path = write_reports(report)
        for path in (json_path, markdown_path):
            print(f"wrote {_display(path)}")
        return 0

    if args.command == "evaluate":
        _print_totals(report)
        return 0

    case = report.case(args.case_id)
    if case is None:
        print(f"no table case {args.case_id!r}. Known cases:", file=sys.stderr)
        for known in report.cases:
            print(f"  {known.case_id}", file=sys.stderr)
        return 2

    if args.command == "case":
        _print_case(case)
    else:
        _print_claims(case)
    return 0


def _display(path: Path) -> str:
    """A short path when the run is inside the tree, the absolute one otherwise.

    Cosmetic, and therefore must not be able to fail: this runs after both files are already
    on disk, where raising would report a successful write as a crash. `os.path.relpath`
    raises on Windows across drives, so even that is guarded.
    """
    try:
        relative = os.path.relpath(path, Path.cwd())
    except (OSError, ValueError):
        return str(path)
    return relative if not relative.startswith("..") else str(path)


def _print_totals(report) -> None:
    totals = report.totals
    scores = totals["scores"]
    print(f"benchmark                   {report.benchmark_version}")
    print(f"implementation commit       {report.implementation_commit}")
    print(f"ontology definition hash    {report.ontology_definition_hash}")
    print(f"candidate tables evaluated  {totals['cases']}")
    print(f"gold observations           {totals['gold_observations']}")
    print(f"emitted observations        {totals['emitted_observations']}")
    print(f"matched observations        {totals['matched_observations']}")
    print(f"metric recall               {scores['metric_recall']:.3f}")
    print(f"matched/emitted             {scores['matched_over_emitted']:.3f}"
          "   (not precision: gold is a deliberate subset)")
    for dimension in MATCH_DIMENSIONS:
        print(f"{dimension + ' accuracy':28}{scores[f'{dimension}_accuracy']:.3f}")
    if totals["issues_by_code"]:
        print("issues by code              " + ", ".join(
            f"{code}={count}" for code, count in totals["issues_by_code"].items()))
    misses = [(c.case_id, k) for c in report.cases for k in c.missed]
    if misses:
        print("misses                      " + ", ".join(
            f"{k.metric_id}@{k.period_key} ({case_id})" for case_id, k in misses))


def _print_case(case) -> None:
    print(f"case        {case.case_id}")
    print(f"category    {case.category}  (cases/{case.source_file})")
    print(f"passage     {case.passage_id}")
    print(f"document    {case.document_id}  ({case.document_type})")
    print(f"table       {case.table_id}")
    print("counts      " + "  ".join(f"{k}={v}" for k, v in case.counts.items()))
    print("scores      " + "  ".join(
        f"{k}={v:.3f}" for k, v in sorted(case.scores.items())))
    print()
    print("gold observations")
    matched = {(m.metric_id, m.period_key): m for m in case.matched}
    for gold in case.gold:
        match = matched.get((gold.metric_id, gold.period_key))
        if match is None:
            print(f"  MISS  {gold.metric_id}@{gold.period_key}"
                  f"  expected {gold.value} {gold.unit}")
            continue
        failed = [name for name, ok in (
            ("value", match.value_ok), ("unit", match.unit_ok), ("scale", match.scale_ok),
            ("period", match.period_ok), ("subject", match.subject_ok),
            ("evidence", match.evidence_ok)) if not ok]
        verdict = "ok  " if not failed else "WRONG"
        print(f"  {verdict}  {gold.metric_id}@{gold.period_key}"
              f"  expected {gold.value} {gold.unit}  emitted {match.emitted_value}"
              f" {match.emitted_unit}" + (f"  failed: {', '.join(failed)}" if failed else ""))
    if case.unmatched_emitted:
        print()
        print(f"emitted but not listed in gold ({len(case.unmatched_emitted)}) "
              "- not errors, gold is a deliberate subset")
    if case.issues:
        print()
        print("issues")
        for issue in case.issues:
            row = "-" if issue.row_index is None else issue.row_index
            print(f"  {issue.code:32} row {row}  {issue.raw_label or ''}")


def _print_claims(case) -> None:
    print(f"{case.case_id}: {len(case.emitted)} emitted observations")
    gold_keys = {(g.metric_id, g.period_key) for g in case.gold}
    for emitted in case.emitted:
        listed = "gold " if (emitted.metric_id, emitted.period_key) in gold_keys else "     "
        print(f"  {listed} {emitted.metric_id}@{emitted.period_key}"
              f"  {emitted.value} {emitted.unit}"
              f"  scale={emitted.scale} ({emitted.scale_source})"
              f"  raw={emitted.raw_text!r}"
              f"  row={emitted.row_index} col={emitted.value_column_index}"
              f"  label={emitted.row_label!r}")


if __name__ == "__main__":
    raise SystemExit(main())
