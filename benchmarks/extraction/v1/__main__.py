"""CLI for the extraction benchmark v1.

Sixteen subcommands, eight of which write anything:

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

    python -m benchmarks.extraction.v1 narrative-build       generate, needs the server
    python -m benchmarks.extraction.v1 narrative-report      replay-only, offline
    python -m benchmarks.extraction.v1 narrative <case_id>   one case, both scopes

    python -m benchmarks.extraction.v1 event-build           generate, needs the server
    python -m benchmarks.extraction.v1 event-report          replay-only, offline
    python -m benchmarks.extraction.v1 event <case_id>       one event case, both scopes

`evaluate` exists because the common question during development is "did a number move",
and answering it should not require a dirty working tree. The scope commands are separated
from the lane commands rather than folded into `case`: they cover all 26 cases while the lane
covers 11, and one command that silently answered about a different set depending on its
argument would be worse than two.

`hybrid-build`, `narrative-build` and `event-build` are the only commands in this file that
touch a network.
Each is separate from its report command on purpose: a report must be regenerable offline from
committed artifacts — the vector caches for one, the answer store for the other — and a single
command that quietly reached for a server on a miss would make "the committed report
reproduces" an untested claim. `narrative-build` is also where the operational statistics live:
tokens, latency and rate are printed here and enter no artifact (STAGE_09 §11.2).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import event_runner, hybrid_scope_runner, narrative_runner, scope_runner
from .runner import MATCH_DIMENSIONS, REPO_ROOT, build_report, write_reports

LANE_COMMANDS = ("report", "evaluate", "case", "claims")
SCOPE_COMMANDS = ("scope-report", "scope", "scope-diff")
HYBRID_COMMANDS = ("hybrid-report", "hybrid-build", "hybrid")
NARRATIVE_COMMANDS = ("narrative-report", "narrative-build", "narrative")
EVENT_COMMANDS = ("event-report", "event-build", "event")


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
    subcommands.add_parser(
        "narrative-report", help="regenerate reports/narrative_lane_v1.{json,md}, offline")
    subcommands.add_parser(
        "narrative-build", help="generate the narrative answers from the generation server")
    subcommands.add_parser(
        "event-report", help="regenerate reports/event_relationship_v1.{json,md}, offline")
    subcommands.add_parser(
        "event-build", help="generate the event answers from the generation server")
    for name, help_text in (("case", "one table case in detail"),
                            ("claims", "every emitted observation for one case"),
                            ("scope", "the candidate scope for one case"),
                            ("scope-diff", "expected vs included concepts for one case"),
                            ("hybrid", "the hybrid candidate scope for one case"),
                            ("narrative", "one narrative case under both scopes"),
                            ("event", "one event case under both scopes")):
        sub = subcommands.add_parser(name, help=help_text)
        sub.add_argument("case_id")

    args = parser.parse_args(argv)
    if args.command in EVENT_COMMANDS:
        return _run_event_command(args)
    if args.command in NARRATIVE_COMMANDS:
        return _run_narrative_command(args)
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


# -- the narrative lane ---------------------------------------------------------------------


def _run_narrative_command(args) -> int:
    if args.command == "narrative-build":
        return _build_narrative_answers()

    started = time.perf_counter()
    report = narrative_runner.build_report()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if args.command == "narrative-report":
        json_path, markdown_path = narrative_runner.write_reports(report)
        for path in (json_path, markdown_path):
            print(f"wrote {_display(path)}")
        print(f"replay: {report.answer_store['answers']} stored answers, both scopes, "
              f"{elapsed_ms:.0f} ms, no network")
        _print_narrative_totals(report)
        return 0

    if report.case(args.case_id) is None:
        print(f"no narrative case {args.case_id!r}. Known cases:", file=sys.stderr)
        for known in report.views["lexical"].cases:
            print(f"  {known.case_id}", file=sys.stderr)
        return 2
    _print_narrative_case(report, args.case_id)
    return 0


def _build_narrative_answers() -> int:
    """Generate once per distinct request, write the store, then report from the store alone.

    The report is built a second time, replay-only, rather than reused from the generating run.
    That is the point of the command split: if the written file is not sufficient to rebuild
    the report, this command fails here rather than a test failing later.

    Timings and token counts are printed here and enter no artifact. STAGE_09 §11.2 settled
    that rule, and step 10 measured the same passage at 1,850 and 1,182 completion tokens on
    two runs whose claims were byte-identical.
    """
    import yaml

    from extraction.providers import LocalOpenAICompatibleGenerationProvider, ProviderConfig
    from extraction.stages.narrative import AnswerStore, ReplayingGenerationProvider
    from extraction.stages.narrative.prompt import PROMPT_VERSION

    config = ProviderConfig.from_config(yaml.safe_load(
        (REPO_ROOT / "config" / "extraction.yaml").read_text(encoding="utf-8")))
    provider = LocalOpenAICompatibleGenerationProvider(config)
    health = provider.health()
    print(f"generation server {config.base_url}: {health.status}"
          + (f" ({health.detail})" if health.detail else ""))
    if not health.ok:
        provider.close()
        return 2

    store = AnswerStore(narrative_runner.ANSWER_STORE)
    recording = ReplayingGenerationProvider(
        store, provider, model_id=config.model, prompt_version=PROMPT_VERSION)
    print(f"answer store holds {len(store)} answers before this run")

    started = time.perf_counter()
    try:
        narrative_runner.build_report(provider=recording)
    finally:
        provider.close()
    elapsed = time.perf_counter() - started

    path = store.write(narrative_runner.ANSWER_STORE)
    print(f"wrote {_display(path)}  {len(store)} answers, "
          f"{path.stat().st_size:,} bytes")
    print(f"generation: {elapsed:.0f} s wall clock for both scopes "
          "(session-dependent, recorded nowhere)")

    report = narrative_runner.build_report()
    json_path, markdown_path = narrative_runner.write_reports(report)
    for path in (json_path, markdown_path):
        print(f"wrote {_display(path)}")
    _print_narrative_totals(report)
    return 0


def _print_narrative_totals(report) -> None:
    lexical = report.views["lexical"].totals
    hybrid = report.views["hybrid"].totals
    print(f"benchmark                   {report.benchmark_version}")
    print(f"implementation commit       {report.implementation_commit}")
    print(f"model                       {report.model['identity']}")
    print(f"{'':28}{'lexical':>10}{'hybrid':>10}")
    for name, key, _ in narrative_runner.DIMENSIONS:
        print(f"{name:28}{lexical['scores'][key]:>10.3f}"
              f"{hybrid['scores'][key]:>10.3f}"
              f"   n={lexical['score_denominators'][key]}/"
              f"{hybrid['score_denominators'][key]}")
    print(f"{'matched/emitted':28}{lexical['scores']['matched_over_emitted']:>10.3f}"
          f"{hybrid['scores']['matched_over_emitted']:>10.3f}"
          "   (not precision: gold is a deliberate subset)")
    for label, key in (("gold observations", "gold_observations"),
                       ("emitted observations", "emitted_observations"),
                       ("matched observations", "matched_observations"),
                       ("unrequired emitted", "unrequired_observations"),
                       ("rejected claims", "rejected_claims"),
                       ("model abstentions", "model_abstentions"),
                       ("ontology warnings", "ontology_warnings"),
                       ("ontology errors", "ontology_errors"),
                       ("assembly rejections", "assembly_rejections"),
                       ("classified failures", "failures")):
        print(f"{label:28}{lexical[key]:>10}{hybrid[key]:>10}")
    print("failures by category        " + ", ".join(
        f"{category}={lexical['failures_by_category'][category]}/"
        f"{hybrid['failures_by_category'][category]}"
        for category in narrative_runner.FAILURE_CATEGORIES
        if lexical['failures_by_category'][category]
        or hybrid['failures_by_category'][category]) or "none")
    print("issues by code              " + ", ".join(
        f"{code}={lexical['issues_by_code'].get(code, 0)}/"
        f"{hybrid['issues_by_code'].get(code, 0)}"
        for code in sorted(set(lexical["issues_by_code"]) | set(hybrid["issues_by_code"]))))
    print(f"scopes identical on         {report.comparison['cases_with_identical_scope']}"
          f" of {report.comparison['cases']} cases")


def _print_narrative_case(report, case_id: str) -> None:
    entry = report.case_index[case_id]
    print(f"case        {case_id}")
    print(f"category    {entry['category']}  lane {entry['lane']}  "
          f"(cases/{entry['source_file']})")
    for scope in narrative_runner.SCOPES:
        case = report.case(case_id, scope)
        print()
        print(f"-- {scope} scope " + "-" * 60)
        print(f"passage     {case.passage_id}")
        print("counts      " + "  ".join(f"{k}={v}" for k, v in case.counts.items()))
        print("scores      " + "  ".join(
            f"{k}={v:.3f}" for k, v in sorted(case.scores.items())))
        print("scope       " + ", ".join(case.scope_concept_ids))
        matched = {(m.metric_id, m.period_key): m for m in case.matched}
        print("gold observations")
        for gold in case.gold:
            match = matched.get((gold.metric_id, gold.period_key))
            if match is None:
                print(f"  MISS  {gold.metric_id}@{gold.period_key}"
                      f"  expected {gold.value} {gold.unit}")
                continue
            failed = match.failed_dimensions
            verdict = "ok  " if not failed else "WRONG"
            print(f"  {verdict}  {gold.metric_id}@{gold.period_key}"
                  f"  expected {gold.value} {gold.unit}"
                  f"  emitted {match.emitted_value} {match.emitted_unit}"
                  + (f"  failed: {', '.join(failed)}" if failed else ""))
        if case.unrequired:
            print(f"emitted but not annotated ({len(case.unrequired)}) - unrequired, "
                  "not errors")
            for metric_id, period_key in case.unrequired:
                print(f"        {metric_id}@{period_key}")
        if case.failures:
            print("failures")
            for failure in case.failures:
                print(f"  {failure.category:24} {failure.metric_id}"
                      f"@{failure.period_key or '-'}  {failure.detail}")
        if case.abstentions:
            print("expected abstentions")
            for verdict in case.abstentions:
                print(f"  {'ok  ' if verdict.honoured else 'MISS'}  {verdict.reason:32}"
                      f" concepts={list(verdict.concept_ids)}"
                      f" claimed={list(verdict.claimed_concept_ids)}")
        if case.issues_by_code:
            print("issues      " + ", ".join(
                f"{code}={count}" for code, count in case.issues_by_code.items()))
        for attribution in case.attributions:
            print(f"  period    {attribution.metric_id}@{attribution.period_key}"
                  f"  label={attribution.period_label!r}"
                  f"  in_evidence={attribution.in_evidence}"
                  f"  distance={attribution.distance}"
                  f"  comparative={attribution.chose_comparative}")


# -- the event and relationship lane ---------------------------------------------------------


def _run_event_command(args) -> int:
    if args.command == "event-build":
        return _build_event_answers()

    started = time.perf_counter()
    report = event_runner.build_report()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if args.command == "event-report":
        json_path, markdown_path = event_runner.write_reports(report)
        for path in (json_path, markdown_path):
            print(f"wrote {_display(path)}")
        print(f"replay: {report.answer_store['answers']} stored answers, both scopes, "
              f"{elapsed_ms:.0f} ms, no network")
        _print_event_totals(report)
        return 0

    if report.case(args.case_id) is None:
        print(f"no event case {args.case_id!r}. Known cases:", file=sys.stderr)
        for known in report.views["lexical"].cases:
            print(f"  {known.case_id}", file=sys.stderr)
        return 2
    _print_event_case(report, args.case_id)
    return 0


def _build_event_answers() -> int:
    """Generate once per distinct request, write the store, then report from the store alone.

    The report is built a second time, replay-only, rather than reused from the generating
    run. That is the point of the command split: if the written file is not sufficient to
    rebuild the report, this command fails here rather than a test failing later.
    """
    import yaml

    from extraction.providers import LocalOpenAICompatibleGenerationProvider, ProviderConfig
    from extraction.stages.narrative import (
        EVENT_PROMPT_VERSION,
        AnswerStore,
        ReplayingGenerationProvider,
    )

    config = ProviderConfig.from_config(yaml.safe_load(
        (REPO_ROOT / "config" / "extraction.yaml").read_text(encoding="utf-8")))
    provider = LocalOpenAICompatibleGenerationProvider(config)
    health = provider.health()
    print(f"generation server {config.base_url}: {health.status}"
          + (f" ({health.detail})" if health.detail else ""))
    if not health.ok:
        provider.close()
        return 2

    store = AnswerStore(event_runner.ANSWER_STORE)
    recording = ReplayingGenerationProvider(
        store, provider, model_id=config.model, prompt_version=EVENT_PROMPT_VERSION)
    print(f"answer store holds {len(store)} answers before this run")

    started = time.perf_counter()
    try:
        event_runner.build_report(provider=recording)
    finally:
        provider.close()
    elapsed = time.perf_counter() - started

    path = store.write(event_runner.ANSWER_STORE)
    print(f"wrote {_display(path)}  {len(store)} answers, {path.stat().st_size:,} bytes")
    print(f"generation: {elapsed:.0f} s wall clock for both scopes "
          "(session-dependent, recorded nowhere)")

    report = event_runner.build_report()
    json_path, markdown_path = event_runner.write_reports(report)
    for path in (json_path, markdown_path):
        print(f"wrote {_display(path)}")
    _print_event_totals(report)
    return 0


def _print_event_totals(report) -> None:
    lexical = report.views["lexical"].totals
    hybrid = report.views["hybrid"].totals
    print(f"benchmark                   {report.benchmark_version}")
    print(f"implementation commit       {report.implementation_commit}")
    print(f"model                       {report.model['identity']}")
    print(f"event types offered         {report.vocabulary['event_types']} "
          f"(aliases declared: {report.vocabulary['event_types_declaring_an_alias']})")
    print(f"{'':44}{'lexical':>10}{'hybrid':>10}")
    for name, key, _ in event_runner.DIMENSIONS:
        print(f"{name:44}{lexical['scores'][key]:>10.3f}{hybrid['scores'][key]:>10.3f}"
              f"   n={lexical['score_denominators'][key]}/"
              f"{hybrid['score_denominators'][key]}")
    for label, key in (("gold events", "gold_events"),
                       ("emitted events", "emitted_events"),
                       ("matched events", "matched_events"),
                       ("non-gold events added", "additional_events"),
                       ("gold relationships", "gold_relationships"),
                       ("emitted relationships", "emitted_relationships"),
                       ("matched relationships", "matched_relationships"),
                       ("non-gold relationships added", "additional_relationships"),
                       ("rejected payloads", "rejected_payloads"),
                       ("model findings", "model_abstentions"),
                       ("ontology warnings", "ontology_warnings"),
                       ("ontology errors", "ontology_errors"),
                       ("classified failures", "failures"),
                       ("gold event types any scope offers", "gold_event_types_in_scope")):
        print(f"{label:44}{lexical[key]:>10}{hybrid[key]:>10}")
    print("failures by category        " + (", ".join(
        f"{category}={lexical['failures_by_category'][category]}/"
        f"{hybrid['failures_by_category'][category]}"
        for category in event_runner.FAILURE_CATEGORIES
        if lexical['failures_by_category'][category]
        or hybrid['failures_by_category'][category]) or "none"))
    print("issues by code              " + (", ".join(
        f"{code}={lexical['issues_by_code'].get(code, 0)}/"
        f"{hybrid['issues_by_code'].get(code, 0)}"
        for code in sorted(set(lexical["issues_by_code"]) | set(hybrid["issues_by_code"])))
        or "none"))


def _print_event_case(report, case_id: str) -> None:
    entry = report.case_index[case_id]
    print(f"case        {case_id}")
    print(f"category    {entry['category']}  lane {entry['lane']}  "
          f"(cases/{entry['source_file']})")
    for scope in event_runner.SCOPES:
        case = report.case(case_id, scope)
        print()
        print(f"-- {scope} scope " + "-" * 60)
        print(f"passage     {case.passage_id}")
        print(f"request     {case.request_digest}")
        print("counts      " + "  ".join(f"{k}={v}" for k, v in case.counts.items()))
        print("gold events")
        matched = {}
        for match in case.matched_events:
            matched.setdefault(match.event_type_id, []).append(match)
        used = {}
        for gold in case.gold_events:
            pool = matched.get(gold.event_type_id) or []
            index = used.get(gold.event_type_id, 0)
            used[gold.event_type_id] = index + 1
            match = pool[index] if index < len(pool) else None
            if match is None:
                print(f"  MISS  {gold.event_type_id}  occurred_on={gold.occurred_on} "
                      f"announced_on={gold.announced_on}")
                continue
            failed = match.failed_dimensions
            print(f"  {'ok  ' if not failed else 'WRONG'}  {gold.event_type_id}"
                  f"  occurred_on {gold.occurred_on}/{match.emitted_occurred_on}"
                  f"  announced_on {gold.announced_on}/{match.emitted_announced_on}"
                  f"  basis={match.basis}"
                  + (f"  failed: {', '.join(failed)}" if failed else ""))
            for participant in match.participants:
                print(f"          {participant.role}: "
                      f"{participant.expected_entity_id} / "
                      f"{participant.emitted_entity_id}"
                      + ("" if participant.emitted_named else "  [unresolved]"))
        edges = {m.relationship_id: m for m in case.matched_relationships}
        for gold in case.gold_relationships:
            match = edges.get(gold.relationship_id)
            if match is None:
                print(f"  MISS  {gold.relationship_id}  {gold.source_id}->{gold.target_id}")
                continue
            failed = match.failed_dimensions
            print(f"  {'ok  ' if not failed else 'WRONG'}  {gold.relationship_id}"
                  f"  {gold.source_id}->{gold.target_id}"
                  f"  emitted {match.emitted_source_id}->{match.emitted_target_id}"
                  + (f"  failed: {', '.join(failed)}" if failed else ""))
        if case.additional_events or case.additional_relationships:
            print("non-gold additions (unscored, not errors)")
            for key in case.additional_events:
                print(f"        event {key}")
            for key in case.additional_relationships:
                print(f"        edge  {key}")
        if case.failures:
            print("failures")
            for failure in case.failures:
                print(f"  {failure.category:26} {failure.subject}  {failure.detail}")
        if case.issues_by_code:
            print("issues      " + ", ".join(
                f"{code}={count}" for code, count in case.issues_by_code.items()))


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
