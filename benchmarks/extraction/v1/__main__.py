"""CLI for the extraction benchmark v1.

Ten subcommands, four of which write anything:

    python -m benchmarks.extraction.v1 report                regenerate the table-lane reports
    python -m benchmarks.extraction.v1 evaluate              print totals, write nothing
    python -m benchmarks.extraction.v1 case <case_id>        one table case in detail
    python -m benchmarks.extraction.v1 claims <case_id>      every emitted observation

    python -m benchmarks.extraction.v1 scope-report          regenerate the scope reports
    python -m benchmarks.extraction.v1 scope <case_id>       one case's candidate scope
    python -m benchmarks.extraction.v1 scope-diff <case_id>  expected vs included

    python -m benchmarks.extraction.v1 hybrid-report         regenerate the hybrid reports
    python -m benchmarks.extraction.v1 hybrid-build          fill the vector caches, then above
    python -m benchmarks.extraction.v1 hybrid <case_id>      one case's hybrid scope

`evaluate` exists because the common question during development is "did a number move",
and answering it should not require a dirty working tree. The scope commands are separated
from the lane commands rather than folded into `case`: they cover all 26 cases while the lane
covers 11, and one command that silently answered about a different set depending on its
argument would be worse than two.

`hybrid-build` is the only command in this file that touches a network. It is separate from
`hybrid-report` on purpose: the report must be regenerable offline from the committed vector
caches, and a single command that quietly reached for a server on a cache miss would make
"the committed report reproduces" an untested claim.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import hybrid_scope_runner, scope_runner
from .runner import MATCH_DIMENSIONS, build_report, write_reports

LANE_COMMANDS = ("report", "evaluate", "case", "claims")
SCOPE_COMMANDS = ("scope-report", "scope", "scope-diff")
HYBRID_COMMANDS = ("hybrid-report", "hybrid-build", "hybrid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.extraction.v1",
        description="Score the deterministic table lane and the lexical candidate scope "
                    "against the reviewed cases.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("report", help="regenerate reports/table_lane_v1.{json,md}")
    subcommands.add_parser("evaluate", help="print lane totals; write nothing")
    subcommands.add_parser(
        "scope-report", help="regenerate reports/lexical_scope_v1.{json,md}")
    subcommands.add_parser(
        "hybrid-report", help="regenerate reports/hybrid_scope_v1.{json,md}, offline")
    subcommands.add_parser(
        "hybrid-build", help="fill the vector caches from the embedding server, then report")
    for name, help_text in (("case", "one table case in detail"),
                            ("claims", "every emitted observation for one case"),
                            ("scope", "the candidate scope for one case"),
                            ("scope-diff", "expected vs included concepts for one case"),
                            ("hybrid", "the hybrid candidate scope for one case")):
        sub = subcommands.add_parser(name, help=help_text)
        sub.add_argument("case_id")

    args = parser.parse_args(argv)
    if args.command in HYBRID_COMMANDS:
        return _run_hybrid_command(args)
    if args.command in SCOPE_COMMANDS:
        return _run_scope_command(args)
    return _run_lane_command(args)


# -- the table lane -------------------------------------------------------------------------


def _run_lane_command(args) -> int:
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


# -- the candidate scope --------------------------------------------------------------------


def _run_scope_command(args) -> int:
    report = scope_runner.build_report()

    if args.command == "scope-report":
        json_path, markdown_path = scope_runner.write_reports(report)
        for path in (json_path, markdown_path):
            print(f"wrote {_display(path)}")
        return 0

    case = report.case(args.case_id)
    if case is None:
        print(f"no case {args.case_id!r}. Known cases:", file=sys.stderr)
        for known in report.cases:
            print(f"  {known.case.case_id}", file=sys.stderr)
        return 2

    if args.command == "scope":
        _print_scope(case)
    else:
        _print_scope_diff(case)
    return 0


def _print_scope(case) -> None:
    print(f"case        {case.case.case_id}")
    print(f"category    {case.case.category}  lane {case.case.lane}  "
          f"(cases/{case.case.source_file})")
    print(f"passage     {case.case.passage_id}")
    print(f"scope size  {len(case.scope)}  ({len(case.scope.protected_ids)} protected)")
    print("reasons     " + "  ".join(
        f"{reason}={count}" for reason, count in case.scope.counts_by_reason().items()
        if count))
    print()
    for candidate in case.scope.concepts:
        surfaces = ", ".join(candidate.surfaces)
        print(f"  {candidate.concept_id:36} {','.join(candidate.reasons)}"
              + (f"   [{surfaces}]" if surfaces else ""))
    if case.scope.expansions:
        print()
        print("confusion-group expansions")
        for expansion in case.scope.expansions:
            print(f"  {expansion.concept_id} -> {expansion.sibling_id}")


def _print_scope_diff(case) -> None:
    """What the reviewers expect beside what the scope admits, and nothing else.

    Two columns rather than a set difference in both directions: a candidate the gold set does
    not name is not an error — the scope is recall-oriented on purpose and stage 9 ranks it —
    while a gold concept the scope does not hold is unrecoverable downstream.
    """
    present = set(case.scope.concept_ids)
    print(f"case        {case.case.case_id}")
    print(f"passage     {case.case.passage_id}")
    print(f"scope size  {len(case.scope)}")
    print()
    print("expected by gold")
    expected = list(case.case.gold_metric_ids) + list(case.known_instance_ids)
    if not expected:
        print("  (none - this case asserts an abstention or a non-metric annotation)")
    for concept_id in expected:
        verdict = "in   " if concept_id in present else "MISS "
        reasons = ",".join(case.scope.reasons_for(concept_id)) or "-"
        print(f"  {verdict} {concept_id:36} {reasons}")

    if case.case.ambiguous_alias_candidates:
        print()
        print("ambiguous-alias candidates the case says must all survive")
        for concept_id in case.case.ambiguous_alias_candidates:
            verdict = "in   " if concept_id in present else "MISS "
            print(f"  {verdict} {concept_id}")

    print()
    print("included beyond gold (not errors - the scope only adds, stage 9 ranks)")
    for candidate in case.scope.concepts:
        if candidate.concept_id in expected:
            continue
        print(f"        {candidate.concept_id:36} {','.join(candidate.reasons)}")

    if case.asymmetric_pairs:
        print()
        print("CONFUSABLE PAIRS HELD ASYMMETRICALLY")
        for pair in case.asymmetric_pairs:
            print(f"  {pair[0]} / {pair[1]}")


# -- the hybrid scope -----------------------------------------------------------------------


def _run_hybrid_command(args) -> int:
    """The three commands that involve the embedding index.

    Timings are printed here and never written to the report: STAGE_09 §6 asks for both
    durations and byte-identical regeneration, and those cannot both hold. See
    `hybrid_scope_runner`'s module docstring.
    """
    if args.command == "hybrid-build":
        return _build_vector_caches()

    started = time.perf_counter()
    report = hybrid_scope_runner.build_report()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if args.command == "hybrid-report":
        json_path, markdown_path = hybrid_scope_runner.write_reports(report)
        for path in (json_path, markdown_path):
            print(f"wrote {_display(path)}")
        print(f"cache reuse: whole report from the committed caches in {elapsed_ms:.0f} ms, "
              f"no network")
        print(f"verdict: {report.decision['verdict']}")
        return 0

    case = report.case(args.case_id)
    if case is None:
        print(f"no case {args.case_id!r}. Known cases:", file=sys.stderr)
        for known in report.views["hybrid"].cases:
            print(f"  {known.case.case_id}", file=sys.stderr)
        return 2
    _print_hybrid(report, case)
    return 0


def _build_vector_caches() -> int:
    """Embed whatever is missing, save both caches, and report what it cost.

    The only network-touching command in this CLI. It builds the report afterwards so that a
    build which leaves the caches incomplete fails here rather than in a test.
    """
    from extraction.providers import (
        EmbeddingConfig,
        LocalOpenAICompatibleEmbeddingProvider,
    )
    from ontology import load_ontology

    from . import runner

    config = EmbeddingConfig.from_config(hybrid_scope_runner._extraction_config())
    provider = LocalOpenAICompatibleEmbeddingProvider(config)
    health = provider.health()
    print(f"embedding server {config.base_url}: {health.status}"
          + (f" ({health.detail})" if health.detail else ""))
    if not health.ok:
        return 2

    ontology = load_ontology()
    started = time.perf_counter()
    scope, concepts, texts = hybrid_scope_runner.build_scopes(
        ontology, provider=provider, model_id=config.model, dimensions=config.dimensions)

    passages = runner.load_passages()
    cases = scope_runner.load_scope_cases()
    embedded = [passages[case.passage_id]["text"] for case in cases]
    labels = [case.case_id for case in cases]
    for probe in list(scope_runner.PROBES) + list(hybrid_scope_runner.PARAPHRASE_PROBES) + [
            hybrid_scope_runner.FORMULA_PROBE]:
        embedded.append(probe["text"])
        labels.append(probe["name"])
    texts.vectors_for(embedded, labels=labels)
    # The ablation arm of §2.1 renders one concept differently; embedding it here keeps the
    # report's offline path complete.
    from extraction.stages.scoping import concept_vectors

    concept_vectors(ontology, concepts, include_raw_variants=True)
    elapsed = time.perf_counter() - started

    concept_path = concepts.save()
    text_path = texts.save()
    added = concepts.added + texts.added
    print(f"cache build: {added} vectors embedded in {elapsed:.1f} s"
          + (f" ({elapsed / added * 1000:.0f} ms each)" if added else " (all cached)"))
    print(f"wrote {_display(concept_path)}  {len(concepts)} entries")
    print(f"wrote {_display(text_path)}  {len(texts)} entries")
    print(f"cache_key {concepts.cache_key}")

    report = hybrid_scope_runner.build_report()
    json_path, markdown_path = hybrid_scope_runner.write_reports(report)
    for path in (json_path, markdown_path):
        print(f"wrote {_display(path)}")
    print(f"verdict: {report.decision['verdict']}")
    return 0


def _print_hybrid(report, case) -> None:
    case_id = case.case.case_id
    neighbours = report.neighbours[case_id]
    print(f"case        {case_id}")
    print(f"passage     {case.case.passage_id}")
    print("scope size  " + "  ".join(
        f"{view}={len(report.views[view].case(case_id).scope)}"
        for view in hybrid_scope_runner.VIEWS))
    print(f"top_k       {report.selection['top_k']}  "
          f"min_similarity {report.selection['min_similarity']}")
    print()
    print("ranking head")
    selected = {n.concept_id for n in neighbours.selected}
    for neighbour in neighbours.head:
        mark = "*" if neighbour.concept_id in selected else " "
        print(f"  {mark} {neighbour.rank:3} {neighbour.similarity:.4f} "
              f"{neighbour.concept_id}")
    if neighbours.gold_ranks:
        print()
        print("gold metrics in the ranking")
        for concept_id, rank, similarity in neighbours.gold_ranks:
            print(f"    {rank:3} {similarity:.4f} {concept_id}")
    print()
    for candidate in case.scope.concepts:
        print(f"  {candidate.concept_id:36} {','.join(candidate.reasons)}")


# -- shared -------------------------------------------------------------------------------


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
