"""Command-line entry point.

    python -m story [--root REPO] [--runs-root R] demo --candidate-id ID [--out DIR] [--live]
    python -m story [--root REPO] [--runs-root R] ui [--host H] [--port P]

**Two verbs, and the rest of §20's family is deferred rather than stubbed.** §8b traded away
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

`ui` serves INTERACTIVE_DEMO_UI's local single-page interface on a loopback address and blocks
until interrupted. It is **also** the composition root for the interface: the story context and
the demo config are passed in as zero-argument factories, so `story/demo_ui/` never imports
`story.context` and the driver stays out of its import closure
(`tests/story/test_story_package_structure.py` walks that closure and would fail otherwise).
Nothing is constructed until an endpoint asks for it, which is why the server starts with no
database running.

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
from .demo_ui import DEFAULT_HOST, DEFAULT_PORT, api, serve
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


def cmd_ui(args) -> int:
    """Serve the demo interface, and own the two objects the endpoints would otherwise build.

    Both factories are lazy and memoised in `built`: a `StoryContext` opens a Bolt connection,
    and building one at startup would mean the interface could not be opened — or its own
    tests run — without Neo4j up. The context is closed on the way out whether or not anything
    asked for one, which is the same `finally` `cmd_demo` uses around the freshness gate.
    """
    root = Path(args.root) if args.root else None
    runs_root = Path(args.runs_root) if args.runs_root else None
    built: dict[str, object] = {}

    def story_context():
        if "context" not in built:
            built["context"] = build_story_context(root, graph_runs_root=runs_root)
        return built["context"]

    def demo_config():
        if "config" not in built:
            built["config"] = DemoConfig.load(root if root is not None else Path.cwd())
        return built["config"]

    def story_pipeline():
        # Imported here and passed as a service rather than imported by `demo_ui`, because
        # `story/pipeline.py` names `story.context` under TYPE_CHECKING and
        # `test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage` reads a guarded
        # import exactly as a plain one. A module-level import in `api.py` would put `neo4j`
        # in the interface's closure and fail that test — the composition root is the one
        # place allowed to know.
        from story import pipeline

        return pipeline

    # The endpoints register on demand and not at import time: the process-wide router is
    # asserted to hold nothing under `/demo/` (pytest imports every test module before running
    # one), so importing `api` may not have a side effect.
    api.register_endpoints()

    try:
        serve(root=root if root is not None else Path.cwd(), host=args.host, port=args.port,
              services={"story_context": story_context, "demo_config": demo_config,
                        "story_pipeline": story_pipeline})
    except ValueError as exc:
        # The one thing `serve` refuses outright is a non-loopback bind (§1, and the brief).
        print(f"{exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"cannot serve on {args.host}:{args.port} — {exc.strerror or exc}",
              file=sys.stderr)
        return EXIT_FAILED
    finally:
        context = built.get("context")
        if context is not None:
            context.close()
    return EXIT_OK


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

    ui = sub.add_parser(
        "ui", help="serve the interactive demo interface on a loopback address")
    ui.add_argument("--host", default=DEFAULT_HOST,
                    help=f"loopback host to bind (default: {DEFAULT_HOST}). A non-loopback "
                         f"address is refused rather than bound")
    ui.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"port to bind (default: {DEFAULT_PORT})")
    ui.set_defaults(handler=cmd_ui)
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
