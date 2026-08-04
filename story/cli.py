"""Command-line entry point.

    python -m story [--root REPO] [--runs-root R] demo --candidate-id ID [--out DIR] [--live]

**One verb, and the rest of §20's family is deferred rather than stubbed.** §8b traded away
`discover`, `package`, `plan`, `draft`, `verify`, `runs`, `report`, `rebuild`, `doctor`, `ask`,
`issues`, `rejected`, `inspect`, `candidate` and `recheck` along with the production run
lifecycle they act on. A subcommand that parsed and then said "not implemented" would be a
promise; there is nothing here that a reader could mistake for one.

`--candidate-id` is required and there is no default. That is §8b's manual selection stated as
a signature: the demo does not route through §6.10's ranking, and the one argument it takes is
the choice a human made. The id is **re-derived from the detectors** and the run refuses if it
does not reproduce.

`--out DIR` names the run directory itself; without it the run lands in
`<out_root>/<story_run_id>/` from `config/story.yaml`, under gitignored `data/`.

`--live` calls the model server instead of replaying the committed answer store. It may fail —
the local runtime's planner is unreliable, measured — and a failure is reported and **not
retried**: §15.3 and §27's D7 both say a schema violation is a result, not a fault.

Parses, renders, maps exit codes. No freshness, detection, packaging, generation or
verification logic of its own — the verb is one call into `pipeline` and one rendering of what
it returned. `EXIT_OK/EXIT_FAILED/EXIT_USAGE = 0/1/2`, long kebab-case flags only, no `--json`
flag, matching `graph/cli.py` and `extraction/cli.py`.

**Exits non-zero when the run produced no accepted post** — a draft the verifier rejected, a
plan or draft §11/§12 refused, a stale graph, or a candidate id that no longer reproduces. A
rejection is still a complete run and still writes its artifacts; the exit code says the demo
has no post, not that nothing happened.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .context import build_story_context
from .core.graph_identity import GraphIdentityError
from .pipeline import (
    ACCEPTED,
    MANIFEST_FILENAME,
    POST_FILENAME,
    REJECTED,
    SELECTION_MODE,
    DemoConfig,
    FreshnessRefused,
    StoryDemoError,
    resolve_demo_inputs,
    run_demo,
)
from .providers.generation_store import (
    GenerationStore,
    MissingGenerationError,
    ReplayingStoryGenerationProvider,
)
from .providers.public import StoryProviderError, load_provider_config
from .stages.packaging import PackagingError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _provider(config: DemoConfig, *, live: bool):
    """The composition root for the model side, and the only place a transport is named.

    Both modes are the same object: `ReplayingStoryGenerationProvider` over a store, with an
    inner HTTP provider under `--live` and `None` without one. That is deliberate rather than
    tidy — with a store on both paths a live run captures what it generated and can be replayed
    afterwards, and a replay-only run raises `MissingGenerationError` rather than quietly
    reaching for a GPU.

    `httpx` enters `sys.modules` only on the live path: the adapter is imported inside the
    branch that needs it, the way `extraction/providers/__init__.py` resolves its adapters.
    """
    provider_config = load_provider_config(config.raw)
    if live:
        from .providers.openai_compatible import StoryOpenAICompatibleProvider

        return ReplayingStoryGenerationProvider(
            GenerationStore(), StoryOpenAICompatibleProvider(provider_config),
            model_id=provider_config.model)
    store = GenerationStore(config.resolved_path(config.generation_store))
    if not len(store):
        raise StoryDemoError(
            f"the recorded answer store {config.resolved_path(config.generation_store)} holds "
            "no generation; the deterministic demo replays what the model produced and cannot "
            "invent it — pass --live to call the server instead")
    return ReplayingStoryGenerationProvider(store, model_id=provider_config.model)


def cmd_demo(args) -> int:
    root = Path(args.root) if args.root else None
    config = DemoConfig.load(root if root is not None else Path.cwd())
    context = build_story_context(
        root, graph_runs_root=Path(args.runs_root) if args.runs_root else None)
    try:
        inputs = resolve_demo_inputs(
            context, candidate_id=args.candidate_id, graph_run_id=config.graph_run_id)
    finally:
        # Closed whether or not the gate passed: a run that refuses at §7 still holds a driver.
        context.close()

    print(f"candidate      {inputs.candidate.candidate_id}")
    print(f"selection      {SELECTION_MODE}")
    print(f"graph run      {inputs.identity.graph_run_id}  ({len(inputs.freshness.checks)} "
          f"freshness checks passed)")
    print(f"package        {inputs.package.package_id}")
    print(f"package digest {inputs.package.package_content_digest}")
    print(f"budget         prompt {inputs.package.budget.prompt_token_estimate} tokens, "
          f"artifact {inputs.package.budget.artifact_token_estimate}, "
          f"{len(inputs.package.facts)} facts, "
          f"{len(inputs.package.primary_passages)} primary passages")

    outcome = run_demo(
        inputs, provider=_provider(config, live=args.live), config=config,
        out_dir=Path(args.out) if args.out else None, live=args.live)

    print(f"story run      {outcome.story_run_id}")
    print(f"directory      {outcome.directory}")
    print(f"disposition    {outcome.disposition}")
    for name in sorted(outcome.artifacts):
        print(f"  {name:<26} {outcome.artifacts[name][:16]}")
    print(f"  {MANIFEST_FILENAME:<26} (written last — the completion marker)")

    if outcome.disposition == ACCEPTED:
        print(f"\nACCEPTED — the post is at {outcome.directory / POST_FILENAME}")
        return EXIT_OK
    if outcome.disposition == REJECTED and outcome.verified is not None:
        blocking = [f for f in outcome.verified.all_findings if f.blocking]
        print(f"\nREJECTED by the deterministic verifier — {len(blocking)} blocking finding(s) "
              f"across {len(outcome.verified.checks)} checks. No post is written.")
        for check in outcome.verified.checks:
            print(f"  {check.describe()}")
        # Every failure, not the first: a draft wrong in two ways is exactly the case a
        # first-failure report would hide (§20). Walked per check rather than over
        # `all_findings`, because a finding carries no sentence index when it is about the
        # draft as a whole and the check it came from is the only thing that says what it is
        # about — `title` and `disclosures` both report with `sentence_index=None`.
        for check in outcome.verified.checks:
            for finding in check.findings:
                if not finding.blocking:
                    continue
                where = ("whole draft" if finding.sentence_index is None
                         else f"sentence {finding.sentence_index}")
                print(f"\n  {check.name}: {finding.code} ({where}, "
                      f"remedy {finding.remedy.value})")
                print(f"    expected  {finding.expected}")
                print(f"    observed  {finding.observed}")
                print(f"    {finding.explanation}")
        return EXIT_FAILED
    print(f"\n{outcome.disposition.upper()} — no draft reached the verifier.")
    for code in outcome.refusal_codes:
        print(f"  {code}")
    print(f"  {outcome.refusal}")
    return EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m story", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="repository root (default: the working directory)")
    parser.add_argument("--runs-root", help="where graph runs are read from "
                                            "(default: data/graph_runs)")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser(
        "demo", help="run the §8b demo end to end over one manually selected candidate")
    demo.add_argument("--candidate-id", required=True,
                      help="the candidate to demonstrate. Re-derived from the detectors and "
                           "refused if it does not reproduce")
    demo.add_argument("--out", help="the run directory (default: <out_root>/<story_run_id>)")
    demo.add_argument("--live", action="store_true",
                      help="call the model server instead of replaying the recorded store. "
                           "May fail, and a failure is not retried (§15.3)")
    demo.set_defaults(handler=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except FreshnessRefused as exc:
        # Every refused check, not the first: §7's report exists so an operator can see which
        # of the fourteen moved, and a one-line summary would send them to guess.
        print(str(exc), file=sys.stderr)
        print(exc.report.describe(), file=sys.stderr)
        return EXIT_FAILED
    except (
        StoryDemoError,
        # Each of these already names the run, the id or the request it is about, so it is
        # reported as one line rather than as a traceback.
        GraphIdentityError,
        PackagingError,
        StoryProviderError,
        MissingGenerationError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except FileNotFoundError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "build_parser", "main"]
