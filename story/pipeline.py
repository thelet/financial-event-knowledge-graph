"""The §8b demo path, end to end, and nothing wider.

Responsibility: ordering, and the artifacts that record what happened. Every step below is a
call into a stage that already exists — this module reimplements none of them, holds no
threshold, no prompt and no verification rule, and reaches no database or model server of its
own. It owns two things a stage cannot: the order, and the run's identity.

**The claim this path demonstrates, exactly** (IMPLEMENTATION_STEPS §8b):

    Given a *manually selected* deterministic graph-derived story candidate, the system builds
    a bounded evidence package, uses an LLM to plan and draft an investor post, and
    *deterministically verifies* its numbers, periods, identities and citations.

**What it does not claim, and what nothing here does.** Autonomous selection of the best story
— the candidate id is an argument and `selection_mode` records `manual_demo_candidate`, which
is the honest framing rather than a weaker one, because §6.10's ranking places this candidate
6th of 262. Complete discovery across the corpus. A production run lifecycle: §14's `.partial`
staging, `os.replace` finalisation, resumability and the `<id>.rejected/` directory are §8b's
deferred 80% of S11, and this module writes into the run directory directly with the manifest
written **last** as the completion marker — a directory without one is unambiguously
incomplete, which is the part of §1.6 the demo does keep. Interactive research mode.
Model-assisted factual authority: §13's deterministic layer is the only authority here and
§8b dropped S10's advisory verifier entirely. Embeddings or semantic search. Publishing.

**Two entry points, and the split is a real boundary rather than a convenience.**

    resolve_demo_inputs(...)   the graph half: freshness gate, detection, packaging.
                               Needs Neo4j and a projected run on disk. Deterministic.
    run_demo(inputs, ...)      the model half: planner, writer, verifier, artifacts.
                               A pure function of its inputs and its provider — no database,
                               no filesystem read, one directory written.

Everything downstream of the package is therefore drivable from a committed package and a
committed answer store with nothing running, which is what makes the determinism proof a test
rather than an assertion. The freshness gate is the first thing `resolve_demo_inputs` does and
`run_demo` refuses inputs whose gate did not pass, so a stale graph cannot reach a model
through either door.

**Nothing here writes to Neo4j or to any authoritative catalog.** §14: no generated prose in
`data/extraction_runs/`, none in `data/graph_runs/`, none in the graph. The artifacts go to the
demo directory and nowhere else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

import yaml

from story.core.graph_identity import GraphIdentity, read_graph_identity
from story.core.keys import story_run_id as mint_story_run_id
from story.core.manifest import StoryRunManifest, build_manifest
from story.core.models import (
    Draft,
    EditorialPlan,
    GenerationResult,
    RunSelection,
    StoryCandidate,
    StoryEvidencePackage,
    VerifiedDraft,
    canonical_json,
)
from story.core.series import build_series
from story.providers.public import PINNED_TEMPERATURE, StoryProviderError
from story.stages.detection import (
    POLICY_VERSION,
    canonicalize,
    detect_cross_metric_divergence,
    load_observations,
)
# `DETECTOR_ID` and `DETECTOR_VERSION` are deliberately not re-exported by the detection
# package — a flat alias would mint a second package-level name for a value §6.11 digests into
# every `candidate_id` — so they are reached through the module that declares them.
from story.stages.detection import cross_metric_divergence
from story.stages.freshness import FreshnessReport, check_freshness
from story.stages.generation import (
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    WRITER_MAX_TOKENS,
    WRITER_PROMPT_VERSION,
    WRITER_SCHEMA_NAME,
    DEFAULT_LENGTH_TARGET,
    DraftRejected,
    EditorialPlanRejected,
    PlannedStory,
    WrittenStory,
    causal_language_for,
    plan_story,
    planner_schema,
    render_markdown,
    write_story,
    writer_schema,
)
from story.stages.packaging import BoundedEvidencePackageBuilder
from story.stages.ranking import RANKING_POLICY_VERSION
from story.stages.retrieval.graph_tools import BoundedGraphRetriever
from story.stages.verification import GATE, DeterministicVerifier, rejection_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    # A type, not a dependency. `resolve_demo_inputs` reads `executor`, `graph_runs_root` and
    # `root` off whatever it is handed and constructs nothing, so this module has no runtime
    # need for the composition root — and importing it anyway would pull the Bolt driver into
    # the closure of a module that never opens a connection
    # (`test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage`). `story/cli.py`
    # is where a context is actually built, and it is the only module here that does.
    from story.context import StoryContext

#: §8b, and the field a reader checks first. The demo does not route through §6.10's ranking
#: and must not be read as claiming ranking chose this candidate.
SELECTION_MODE = "manual_demo_candidate"

#: The demo's own layout. Flat and one file per stage output, because §14's nested
#: `packages/`, `plans/`, `drafts/` directories exist to hold *many* candidates and this path
#: runs exactly one. `demo_manifest.json` is written last and is the completion marker.
CANDIDATE_FILENAME = "candidate.json"
PACKAGE_FILENAME = "evidence_package.json"
PLAN_FILENAME = "editorial_plan.json"
DRAFT_FILENAME = "draft.json"
VERIFICATION_FILENAME = "verification_report.json"
POST_FILENAME = "post.md"
REJECTED_FILENAME = "rejected.json"
#: §14's replay mechanism, written so a `--live` run can be replayed afterwards. Not in §8b's
#: artifact list, and written anyway: without it a live run is a result nobody can reproduce.
GENERATIONS_FILENAME = "generations.jsonl"
MANIFEST_FILENAME = "demo_manifest.json"

#: What the run ended as. Four values, not two: §11 and §12 can each refuse before §13 runs,
#: and folding those into `rejected` would report "the verifier rejected this draft" about a
#: draft the verifier never saw.
ACCEPTED = "accepted"
REJECTED = "rejected"
PLAN_REFUSED = "plan_refused"
DRAFT_REFUSED = "draft_refused"

CONFIG_FILENAME = "story.yaml"


class StoryDemoError(RuntimeError):
    """Base of every refusal this path raises. Each names what it refused and why.

    A refusal is not a result: nothing is written and no directory is created, so an operator
    cannot mistake a run that never happened for one that produced no post. The two dispositions
    that *are* results — a plan or draft §11/§12 refused, and a draft §13 rejected — write their
    full artifact set instead.
    """


class DemoConfigurationError(StoryDemoError):
    """`config/story.yaml` is missing, unreadable, or not the document this loader reads."""


class FreshnessRefused(StoryDemoError):
    """§7's gate refused. Carries the report so the caller prints every check, not the first."""

    def __init__(self, report: FreshnessReport) -> None:
        super().__init__(
            f"the freshness gate refused {report.graph_run_id}: "
            + ", ".join(check.code.value for check in report.refusals))
        self.report = report


class CandidateNotFound(StoryDemoError):
    """The named candidate is not one the detectors produced from this graph.

    Raised rather than falling back to "something close", and this is the whole reason the
    demo re-derives the candidate instead of trusting the string it was handed: a copied id
    that no longer reproduces means the detector, the policy or the graph moved, and a demo
    that quietly packaged a different candidate would be demonstrating nothing.
    """

    def __init__(self, candidate_id: str, available: tuple[str, ...]) -> None:
        super().__init__(
            f"no candidate {candidate_id!r} among the {len(available)} the detectors produced "
            f"from this graph; a candidate id digests its detector version, its policy version "
            f"and its anchor observations (§6.11), so an id that no longer reproduces means one "
            f"of those moved. Available: " + ", ".join(available))
        self.candidate_id = candidate_id
        self.available = available


# -- configuration -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoConfig:
    """`config/story.yaml`, plus the hash that puts it in `story_run_id`.

    `raw` is kept whole for the same reason `extraction/core/config.py:ExtractionConfig` keeps
    it: `config_hash` must cover what was on disk rather than what this dataclass models, so a
    key added to the file and not read here still moves the run id.
    """

    root: Path
    raw: dict[str, Any]
    graph_run_id: str
    out_root: str
    generation_store: str
    length_target: int
    planner_max_tokens: int
    writer_max_tokens: int

    @classmethod
    def load(cls, root: Path) -> "DemoConfig":
        path = Path(root) / "config" / CONFIG_FILENAME
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise DemoConfigurationError(f"{path} could not be read: {exc}") from exc
        except yaml.YAMLError as exc:
            raise DemoConfigurationError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise DemoConfigurationError(f"{path} must be a mapping, not a "
                                         f"{type(document).__name__}")
        generation = document.get("generation") or {}
        demo = document.get("demo") or {}
        try:
            return cls(
                root=Path(root),
                raw=document,
                graph_run_id=str(demo["graph_run_id"]),
                out_root=str(demo.get("out_root", "data/story_demo")),
                generation_store=str(demo.get("generation_store", "")),
                length_target=int(generation.get("length_target",
                                                 DEFAULT_LENGTH_TARGET)),
                planner_max_tokens=int(generation.get("planner_max_tokens",
                                                      PLANNER_MAX_TOKENS)),
                writer_max_tokens=int(generation.get("writer_max_tokens",
                                                     WRITER_MAX_TOKENS)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DemoConfigurationError(f"{path}: {type(exc).__name__}: {exc}") from exc

    def config_hash(self) -> str:
        """`sha256` over the document **as parsed** — a `story_run_id` input (§14).

        Not as written, which this docstring claimed until the TABLE_CELL_CITATIONS review:
        `canonical_json` is taken over `self.raw`, the mapping `yaml.safe_load` returned, so
        comments and formatting never reach the digest. Measured 2026-08-18 — rewriting the
        `length_target` commentary moved the file's own sha256 and left `config_hash` at
        `b8488b32076b9b1d…`. That is the useful behaviour, since a corrected comment must not
        re-key every run, but a reader who believed the old sentence would have left a false
        statement standing in a hashed file rather than fix it.

        `canonical_json` and `digest`, the pair `story/core/keys.py` already uses, rather than
        `extraction.core.config.canonical_hash`: one hashing rule for the package, and reaching
        upstream for it would pull that module's YAML loader and path model in with it.
        """
        return hashlib.sha256(canonical_json(self.raw).encode("utf-8")).hexdigest()

    def resolved_path(self, configured: str) -> Path:
        """An absolute entry wins outright; a relative one resolves against the root.

        The same rule `story/context.py:_under` and `GraphIdentity.extraction_run_path` apply,
        so a reader does not have to learn a third one.
        """
        path = Path(configured)
        return path if path.is_absolute() else self.root / path


# -- the graph half ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoInputs:
    """Everything the model half needs, and every version it must record.

    Deterministic in full: two resolutions over one graph produce the same candidate id, the
    same `package_content_digest` and the same version strings, with no clock anywhere. That is
    what lets `run_demo` be tested against a committed copy of this and still be the same code
    the live path runs.
    """

    identity: GraphIdentity
    freshness: FreshnessReport
    candidate: StoryCandidate
    package: StoryEvidencePackage
    detector_versions: Mapping[str, str]
    policy_version: str


def select_candidate(candidates: Mapping[str, StoryCandidate], candidate_id: str
                     ) -> StoryCandidate:
    """The manual selection, and the loud failure. §8b's `manual_demo_candidate`.

    A plain lookup with a typed miss, kept as a function so the refusal can be tested without a
    database: the value of re-deriving the id is entirely in what happens when it does not
    match, and that is the branch worth exercising.
    """
    found = candidates.get(candidate_id)
    if found is None:
        raise CandidateNotFound(candidate_id, tuple(sorted(candidates)))
    return found


def resolve_demo_inputs(
    context: StoryContext,
    *,
    candidate_id: str,
    graph_run_id: str,
) -> DemoInputs:
    """Freshness gate, then detection, then the bounded package. Refuses before it reads.

    The gate is first and is not advisory: §17.8's failure is a published post holding numbers
    from a run the graph no longer contains, and every check it runs is cheaper than the load
    that follows. Only §6.6's D4 detector runs — the demo's candidate is a cross-metric
    divergence, and running the other three would spend a full canonical pass to produce
    candidates nothing selects.

    Observations are loaded **whole and paged to completeness** by `load_observations`, which is
    the reason the builder is handed records rather than being allowed to fetch its own: §9's
    `get_metric_history` is bounded at 200 rows, five metrics exceed it, and only canonical
    series construction proves the page walk finished.
    """
    identity = read_graph_identity(context.graph_runs_root, graph_run_id)
    report = check_freshness(
        executor=context.executor, graph_run_id=graph_run_id,
        graph_runs_root=context.graph_runs_root, root=context.root)
    if not report.passed:
        raise FreshnessRefused(report)

    retriever = BoundedGraphRetriever(context.executor)
    load = load_observations(retriever)
    points = canonicalize(load.records)
    detected = detect_cross_metric_divergence(build_series(points), graph_run_id=graph_run_id)
    candidate = select_candidate(
        {found.candidate_id: found for found in detected.candidates}, candidate_id)

    builder = BoundedEvidencePackageBuilder(
        retriever, identity=identity, records=load.records, points=points,
        unreadable=load.unreadable)
    package = builder.build(candidate, candidate.evidence_request)
    return DemoInputs(
        identity=identity,
        freshness=report,
        candidate=candidate,
        package=package,
        detector_versions={cross_metric_divergence.DETECTOR_ID:
                           cross_metric_divergence.DETECTOR_VERSION},
        policy_version=POLICY_VERSION,
    )


# -- the model half ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoOutcome:
    """What the run produced, named so the CLI has nothing to decide.

    `verified` is `None` exactly when §13 never ran — a plan or a draft §11/§12 refused — and
    the disposition says which. `ok` is `disposition == ACCEPTED` and nothing else: a rejected
    draft with a correct structured explanation is a valid demo result and a non-zero exit, and
    conflating "the run worked" with "the post passed" is how a demo starts overclaiming.
    """

    story_run_id: str
    directory: Path
    disposition: str
    manifest: StoryRunManifest
    plan: EditorialPlan | None = None
    draft: Draft | None = None
    verified: VerifiedDraft | None = None
    refusal: str = ""
    refusal_codes: tuple[str, ...] = ()
    artifacts: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.disposition == ACCEPTED


def run_demo(
    inputs: DemoInputs,
    *,
    provider: Any,
    config: DemoConfig,
    out_dir: Path | None = None,
    live: bool = False,
    now: str | None = None,
) -> DemoOutcome:
    """Plan, write, verify, and write the directory. One candidate, one pass, no retry.

    `provider` is anything satisfying `story.contracts.StoryGenerationProvider` — the replaying
    provider over a committed store on the deterministic path, the HTTP one under `--live`.
    Neither is constructed here: this module names no transport, and the composition happens in
    `story/cli.py`.

    **Nothing is retried.** §15.3 and §27's D7: a schema violation is the model's answer, not a
    transport fault, and re-asking at temperature 0 returns the same thing while charging for it
    twice. §11 and §12's refusals are the same kind of answer and are recorded as dispositions
    rather than retried around.

    `now` is injectable so the determinism proof can hold the one clock still. It is the only
    clock in the run, and it enters no id: `story_run_id` is derived from versions, digests and
    the selection, exactly as `graph/core/manifest.py:104-139` derives its own.
    """
    if not inputs.freshness.passed:
        # Re-asserted rather than assumed. `resolve_demo_inputs` already refused, but this
        # function is separately callable — and "the gate ran before the model did" is the
        # guarantee §7 exists for, so it is checked at the door a model call comes through.
        raise FreshnessRefused(inputs.freshness)

    package = inputs.package
    results: list[GenerationResult] = []
    planned: PlannedStory | None = None
    written: WrittenStory | None = None
    verified: VerifiedDraft | None = None
    disposition = ACCEPTED
    refusal = ""
    refusal_codes: tuple[str, ...] = ()

    try:
        planned = plan_story(package, provider=provider,
                             max_tokens=config.planner_max_tokens)
        results.append(planned.generation)
    except (EditorialPlanRejected, StoryProviderError) as exc:
        disposition, refusal, refusal_codes = PLAN_REFUSED, str(exc), _codes_of(exc)

    if planned is not None:
        try:
            written = write_story(
                package, planned.plan, provider=provider,
                length_target=config.length_target, max_tokens=config.writer_max_tokens)
            results.append(written.generation)
        except (DraftRejected, StoryProviderError) as exc:
            disposition, refusal, refusal_codes = DRAFT_REFUSED, str(exc), _codes_of(exc)

    if planned is not None and written is not None:
        # The three freshness arguments are the *expected* identity §13.13 pins, supplied by
        # what resolved it. Passing the package's own values would make the check compare a
        # document with itself; these come from the graph run the gate just verified.
        verified = DeterministicVerifier(
            graph_run_id=inputs.identity.graph_run_id,
            run_complete_sha256=inputs.identity.run_complete_sha256,
            ontology_definition_hash=inputs.identity.ontology_definition_hash,
        ).verify(written.draft, package, planned.plan)
        disposition = ACCEPTED if verified.passed else REJECTED

    story_run = _mint_run_id(inputs, config, provider=provider, results=results)
    directory = Path(out_dir) if out_dir is not None else (
        config.resolved_path(config.out_root) / story_run)
    manifest = _write_run(
        directory, inputs=inputs, config=config, story_run=story_run,
        disposition=disposition, planned=planned, written=written, verified=verified,
        refusal=refusal, refusal_codes=refusal_codes, results=results, provider=provider,
        live=live, now=now)
    return DemoOutcome(
        story_run_id=story_run, directory=directory, disposition=disposition,
        manifest=manifest, plan=planned.plan if planned else None,
        draft=written.draft if written else None, verified=verified,
        refusal=refusal, refusal_codes=refusal_codes,
        artifacts=manifest.artifacts)


def _codes_of(exc: Exception) -> tuple[str, ...]:
    """The structured codes a refusal carries, whatever shape its class chose.

    `EditorialPlanRejected` and `DraftRejected` expose `codes`; `StoryProviderSchemaError`
    exposes `violations`. Read defensively rather than branched on by class, because the
    disposition is the same either way and a fourth refusal type should not need this function
    edited to be *recorded* — only to be understood.
    """
    codes = getattr(exc, "codes", None)
    if codes:
        return tuple(str(code) for code in codes)
    return tuple(str(v) for v in getattr(exc, "violations", ()) or ())


# -- identity and the manifest -------------------------------------------------------------------


def schema_digests_for(package: StoryEvidencePackage) -> dict[str, str]:
    """The two schemas this run constrained the model with, digested (§14).

    The planner's schema is built from the package rather than fetched: §11 pins
    `causal_language` to one value inside the grammar, so two packages with different causal
    standing are constrained by two different schemas and must not digest alike.
    """
    return {
        PLANNER_SCHEMA_NAME: _digest(planner_schema(
            causal_language=causal_language_for(package))),
        WRITER_SCHEMA_NAME: _digest(writer_schema()),
    }


def verifier_gate_digest() -> str:
    """A digest over §13.17's gate table, standing in for a verifier version that does not exist.

    **`story/stages/verification/` declares no `VERIFIER_VERSION`**, unlike every detector, the
    canonicalisation policy, the two prompts and §6.10's ranking — so §8b's "record the verifier
    version" has nothing to record. Deriving one from the gate is not the same thing and is not
    presented as one: it moves when a code, a severity or a remedy moves, which is most of what
    a version is for, and it is computed from the stage rather than invented beside it. A real
    `VERIFIER_VERSION` belongs in that stage and is named as a gap rather than added from here.
    """
    return _digest({code: [entry.severity.value, entry.remedy.value, entry.section]
                    for code, entry in sorted(GATE.items())})


def _digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _run_selection(candidate_id: str, detector_versions: Mapping[str, str]) -> RunSelection:
    """One candidate, one detector, limit 1 — the selection, exactly as it was made.

    In `story_run_id` because §14's correction was that `--limit 3` and `--limit 20` minted one
    id. The demo takes neither flag, and recording the selection anyway is what keeps a second
    demo over a second candidate from landing in the first one's directory.
    """
    return RunSelection(limit=1, candidate_ids=(candidate_id,),
                        detector_ids=tuple(sorted(detector_versions)))


def _mint_run_id(inputs: DemoInputs, config: DemoConfig, *, provider: Any,
                 results: list[GenerationResult]) -> str:
    """§14's `story-v1-<digest12>`, over versions and digests and no clock.

    Two §14 fields hold one value each where this path has two, and both are recorded rather
    than silently halved:

    * `prompt_version` — the planner's and the writer's, joined. §14 wrote the field before
      §15.1 split one call into three call sites, and dropping one would let a writer prompt
      bump mint the same run id.
    * `max_tokens` — the planner's. The writer's reaches the digest through `config_hash`,
      which covers `config/story.yaml` as written, so neither is invisible.

    `ranking_policy_version` is passed even though the demo does not route through §6.10:
    `story_run_id` requires it, and founder gate G2's whole finding was that a scoring change
    had nothing in the id to show for it. A demo that omitted it would reintroduce the gap in
    the one artifact that exists to close it.
    """
    return mint_story_run_id(
        graph_run_id=inputs.identity.graph_run_id,
        run_complete_sha256=inputs.identity.run_complete_sha256 or "",
        ontology_definition_hash=inputs.identity.ontology_definition_hash,
        config_hash=config.config_hash(),
        prompt_version=(f"planner={PLANNER_PROMPT_VERSION};"
                        f"writer={WRITER_PROMPT_VERSION}"),
        model_id=_model_id(provider),
        provider_model_id=_provider_model_id(provider, results),
        temperature=PINNED_TEMPERATURE,
        max_tokens=config.planner_max_tokens,
        schema_digests=schema_digests_for(inputs.package),
        detector_versions=dict(inputs.detector_versions),
        policy_version=inputs.policy_version,
        ranking_policy_version=RANKING_POLICY_VERSION,
        selection=_run_selection(inputs.candidate.candidate_id, inputs.detector_versions),
        budget=inputs.package.budget.parameters,
    )


def _model_id(provider: Any) -> str:
    """What the run *called* the model — the identity every stored generation is keyed under."""
    return str(getattr(provider, "model_id", "") or "")


def _provider_model_id(provider: Any, results: list[GenerationResult]) -> str:
    """What the server called itself, or the configured name when nothing answered.

    The two differ and §14 says why: the server reports an absolute `.gguf` path while the
    configuration names the basename, and swapping the file behind an unchanged `model_id`
    changes every generation. Read off the first result rather than configured, because a
    configured value cannot notice the swap.
    """
    for result in results:
        if result.model_id:
            return result.model_id
    return _model_id(provider)


def _token_totals(results: list[GenerationResult], package: StoryEvidencePackage
                  ) -> dict[str, Any]:
    """What the run spent, and what the package estimated it would.

    Zeroes across a replayed run are the answer, not a gap: `generation_store` deliberately
    stores no token count, latency or attempt count because those change on every identical
    request and a record holding them could never be byte-identical. A reader seeing zeroes is
    reading a replay.
    """
    return {
        "prompt_tokens": sum(r.prompt_tokens for r in results),
        "completion_tokens": sum(r.completion_tokens for r in results),
        "total_tokens": sum(r.total_tokens for r in results),
        "generation_calls": len(results),
        "package_prompt_token_estimate": package.budget.prompt_token_estimate,
        "package_artifact_token_estimate": package.budget.artifact_token_estimate,
    }


def _counts(inputs: DemoInputs, written: WrittenStory | None,
            verified: VerifiedDraft | None) -> dict[str, Any]:
    package = inputs.package
    counts: dict[str, Any] = {
        "facts": len(package.facts),
        "primary_passages": len(package.primary_passages),
        "context_passages": len(package.context_passages),
        "counter_evidence": len(package.counter_evidence),
        "package_warnings": len(package.warnings),
        "freshness_checks": len(inputs.freshness.checks),
        "sentences": len(written.draft.sentences) if written else 0,
    }
    if verified is not None:
        counts["verification_checks"] = len(verified.checks)
        counts["findings"] = len(verified.all_findings)
        counts["blocking_findings"] = sum(1 for f in verified.all_findings if f.blocking)
    return counts


# -- the directory -------------------------------------------------------------------------------


def _render_json(payload: Any) -> str:
    """§20's document encoding — `indent=2, sort_keys=True, ensure_ascii=False`, newline-ended.

    Deliberately not `canonical_json`'s compact form: these files are read by a human and
    diffed by git, and a digest input is neither.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_run(
    directory: Path,
    *,
    inputs: DemoInputs,
    config: DemoConfig,
    story_run: str,
    disposition: str,
    planned: PlannedStory | None,
    written: WrittenStory | None,
    verified: VerifiedDraft | None,
    refusal: str,
    refusal_codes: tuple[str, ...],
    results: list[GenerationResult],
    provider: Any,
    live: bool,
    now: str | None,
) -> StoryRunManifest:
    """Write every artifact, then the manifest, in that order.

    The manifest is **last** and is the completion marker (§14): a demo directory without one
    is unambiguously incomplete. `post.md` is written only on an acceptance and `rejected.json`
    only when something refused, so the two can never both be present and a reader can tell the
    disposition from the file listing alone.
    """
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}

    def write(name: str, text: str) -> None:
        (directory / name).write_text(text, encoding="utf-8")
        artifacts[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    write(CANDIDATE_FILENAME, _render_json(inputs.candidate.model_dump(mode="json")))
    write(PACKAGE_FILENAME, _render_json(inputs.package.model_dump(mode="json")))
    if planned is not None:
        write(PLAN_FILENAME, _render_json(planned.plan.model_dump(mode="json")))
    if written is not None:
        write(DRAFT_FILENAME, _render_json(written.draft.model_dump(mode="json")))
    if verified is not None:
        write(VERIFICATION_FILENAME, _render_json(verified.model_dump(mode="json")))
    if disposition == ACCEPTED and written is not None:
        # Rendered from the structured draft and never from the model's prose (§12).
        write(POST_FILENAME, render_markdown(written.draft))
    if disposition != ACCEPTED:
        write(REJECTED_FILENAME, _render_json(_rejection_payload(
            inputs, disposition, verified, refusal, refusal_codes)))
    store = getattr(provider, "store", None)
    if store is not None and len(store):
        write(GENERATIONS_FILENAME, store.render())

    manifest = build_manifest(
        story_run_id=story_run,
        config_hash=config.config_hash(),
        graph_run_id=inputs.identity.graph_run_id,
        graph_projection_version=inputs.identity.graph_projection_version,
        # The graph run and the extraction run each recorded their own commit; this manifest is
        # not a place to guess at either, and `build_manifest` fills `story_code_commit` from
        # the tree that ran this. Three commits, and only one of them is knowable from here.
        graph_code_commit=None,
        extraction_run_id=inputs.identity.extraction_run_id,
        extraction_code_commit=None,
        run_complete_sha256=inputs.identity.run_complete_sha256 or "",
        ontology_id=inputs.identity.ontology_id,
        ontology_version=inputs.identity.ontology_version,
        ontology_definition_hash=inputs.identity.ontology_definition_hash,
        prompt_versions={PLANNER_SCHEMA_NAME: PLANNER_PROMPT_VERSION,
                         WRITER_SCHEMA_NAME: WRITER_PROMPT_VERSION},
        model_id=_model_id(provider),
        provider_model_id=_provider_model_id(provider, results),
        temperature=PINNED_TEMPERATURE,
        max_tokens=config.planner_max_tokens,
        schema_digests=schema_digests_for(inputs.package),
        detector_versions=dict(inputs.detector_versions),
        policy_version=inputs.policy_version,
        ranking_policy_version=RANKING_POLICY_VERSION,
        selection={
            **_run_selection(inputs.candidate.candidate_id,
                             inputs.detector_versions).model_dump(mode="json"),
            # §8b's field, and the one a reader checks first. It belongs in `selection` because
            # that is what the block is: how this run chose what it worked on.
            "selection_mode": SELECTION_MODE,
        },
        budget=inputs.package.budget.parameters.model_dump(mode="json"),
        counts=_counts(inputs, written, verified),
        token_totals=_token_totals(results, inputs.package),
        artifacts=artifacts,
        story_run_id_inputs=list(_run_id_input_names()),
        root=config.root,
        now=now,
    )
    payload = {
        **manifest.as_dict(),
        # The demo's own block. §14's `StoryRunManifest` is the run-lifecycle manifest and has
        # no field for a demo's disposition or for the package it was about; extending that
        # frozen record from here would put a §8b concept into `story/core/`, which the whole
        # of §1 keeps free of one. Everything else above is §14's, unchanged.
        "demo": {
            "candidate_id": inputs.candidate.candidate_id,
            "selection_mode": SELECTION_MODE,
            "package_id": inputs.package.package_id,
            "package_content_digest": inputs.package.package_content_digest,
            "graph_input_content_digest": inputs.identity.input_content_digest,
            "verifier_gate_digest": verifier_gate_digest(),
            "verifier_version": None,
            "disposition": disposition,
            "generation_mode": "live" if live else "replay",
            "length_target": config.length_target,
            "writer_max_tokens": config.writer_max_tokens,
            "freshness_passed": inputs.freshness.passed,
        },
    }
    (directory / MANIFEST_FILENAME).write_text(_render_json(payload), encoding="utf-8")
    return manifest


def _rejection_payload(inputs: DemoInputs, disposition: str, verified: VerifiedDraft | None,
                       refusal: str, refusal_codes: tuple[str, ...]) -> dict[str, Any]:
    """Why this run produced no post — one file for all three ways that happens.

    A §13 rejection carries `RejectedDraft` whole, checks included: a rejection that dropped its
    WARNs and ANNOTATEs would report the draft's worst sentence instead of the draft, and the
    `examined` denominators are what say which checks even ran. A §11 or §12 refusal has no
    verification to carry and says so in `stage`, so the two are never read as one another.
    """
    payload: dict[str, Any] = {
        "candidate_id": inputs.candidate.candidate_id,
        "package_id": inputs.package.package_id,
        "disposition": disposition,
        "stage": {PLAN_REFUSED: "editorial_planner", DRAFT_REFUSED: "post_writer",
                  REJECTED: "deterministic_verifier"}.get(disposition, disposition),
        "codes": list(refusal_codes),
        "detail": refusal,
    }
    if verified is not None:
        payload["rejection"] = rejection_for(verified).model_dump(mode="json")
    return payload


def _run_id_input_names() -> tuple[str, ...]:
    """The digest input list *as it was used* — §14 asks for the list, not the algorithm.

    Recorded because §14's correction was about the list: a reader holding two directories must
    be able to see why they are two without re-running either.
    """
    return (
        "story_layout_version", "graph_run_id", "run_complete_sha256",
        "ontology_definition_hash", "config_hash", "prompt_version", "model_id",
        "provider_model_id", "temperature", "max_tokens", "schema_digests",
        "detector_versions", "policy_version", "ranking_policy_version", "selection", "budget",
    )


__all__ = [
    "ACCEPTED",
    "CANDIDATE_FILENAME",
    "DRAFT_FILENAME",
    "DRAFT_REFUSED",
    "GENERATIONS_FILENAME",
    "MANIFEST_FILENAME",
    "PACKAGE_FILENAME",
    "PLAN_FILENAME",
    "PLAN_REFUSED",
    "POST_FILENAME",
    "REJECTED",
    "REJECTED_FILENAME",
    "SELECTION_MODE",
    "VERIFICATION_FILENAME",
    "CandidateNotFound",
    "DemoConfig",
    "DemoConfigurationError",
    "DemoInputs",
    "DemoOutcome",
    "FreshnessRefused",
    "StoryDemoError",
    "resolve_demo_inputs",
    "run_demo",
    "schema_digests_for",
    "select_candidate",
    "verifier_gate_digest",
]
