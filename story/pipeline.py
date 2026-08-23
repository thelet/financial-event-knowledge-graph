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
    run_demo(inputs, ...)      the model half: planner, derivation, writer, verifier,
                               artifacts. A pure function of its inputs and its provider — no
                               database, no filesystem read, one directory written.

The derivation stage sits between the plan and the draft and has no model in it at all
(DETERMINISTIC_FACT_TOOLS §5). It is here rather than inside either generation stage for this
module's stated reason: it owns the order, and *"code computes every quantity and the model only
words it"* is an ordering claim — the offer set must be built before the planner is asked, and
the facts must exist before the writer is.

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
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import yaml

from story.core.graph_identity import GraphIdentity, read_graph_identity
from story.core.keys import story_run_id as mint_story_run_id
from story.core.manifest import StoryRunManifest, build_manifest
from story.core.models import (
    DerivedFact,
    Draft,
    EditorialPlan,
    EvidenceScopeFact,
    GenerationResult,
    RunSelection,
    StoryCandidate,
    StoryEvidencePackage,
    VerifiedDraft,
    canonical_json,
)
from story.core.series import build_series
from story.providers.public import (
    # The local adapter's id, under the name it has carried since it was the only one. Packet A
    # aliases `PROVIDER_LOCAL` onto it; imported under the stable name so this module does not
    # depend on which of the two the boundary happens to export.
    KIND_LOCAL_OPENAI_COMPATIBLE as PROVIDER_LOCAL,
    PINNED_TEMPERATURE,
    StoryProviderError,
    StoryProviderSchemaError,
)
from story.core.evidence_slice import passages_backing_facts
from story.stages.composition import (
    CompiledDraft,
    CompositionRefused,
    compile_draft,
    slot_table,
)
from story.stages.derivation.execute import execute_all
from story.stages.derivation.offers import offers
from story.stages.derivation.public import TOOL_VERSION as DERIVATION_TOOL_VERSION
from story.stages.derivation.public import DerivationResult
from story.stages.detection import (
    POLICY_VERSION,
    canonicalize,
    detect_acceleration,
    detect_cross_metric_divergence,
    detect_metric_moves,
    detect_trend_reversals,
    load_observations,
)
# `DETECTOR_ID` and `DETECTOR_VERSION` are deliberately not re-exported by the detection
# package — a flat alias would mint a second package-level name for a value §6.11 digests into
# every `candidate_id` — so they are reached through the module that declares them.
from story.stages.detection import (
    acceleration,
    cross_metric_divergence,
    detector_config,
    metric_move,
    trend_reversal,
)
from story.stages.freshness import FreshnessReport, check_freshness
from story.stages.generation import (
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    WRITER_MAX_TOKENS,
    WRITER_PROMPT_VERSION,
    WRITER_SCHEMA_NAME,
    DEFAULT_LENGTH_TARGET,
    PLAIN_INVESTOR_STYLE,
    DraftRejected,
    EditorialPlanRejected,
    PlannedStory,
    WrittenStory,
    causal_language_for,
    causal_marker_hits,
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
#: DETERMINISTIC_FACT_TOOLS §3 — every quantity code computed for this run, and §7's
#: evidence-scope facts beside them. **A separate artifact and not a section of
#: `evidence_package.json`**, because the planner selects the derivations and §2's line is that
#: nothing a model produced may enter a package: `package_content_digest` is a `story_run_id`
#: input, so a package whose contents depended on a model call would make the run id depend on
#: the model's output. Written whenever the derivation stage ran at all, refusals included —
#: a file recording only the successes could not answer *"what did the plan ask for?"*.
DERIVED_FACTS_FILENAME = "derived_facts.json"
#: S5's artifact: the templates the writer answered with, and which slot of each became which
#: span of the compiled sentence. The one place a reader can see the sentence *before* code
#: filled it in, which is what makes "the model wrote the words, code wrote the numbers" a thing
#: an operator can check rather than a claim this file makes.
COMPOSITION_FILENAME = "composition.json"
MANIFEST_FILENAME = "demo_manifest.json"

#: What the run ended as. Five values, not two: §11 and §12 can each refuse before §13 runs,
#: and folding those into `rejected` would report "the verifier rejected this draft" about a
#: draft the verifier never saw.
#:
#: `provider_failed` is the fifth and was added 2026-08-19 after a live run with a rejected key
#: came back as `plan_refused`, with a `rejected.json` naming `editorial_planner` as the stage
#: that refused, for a request that reached no planner and got no answer. That is the same
#: overclaim the four exist to prevent, one boundary further out: a 401, a closed port and a
#: timeout are not a model's judgement about anything, and a disposition that says a stage
#: refused is a claim about what a model answered.
#: `derivation_refused` is the sixth and was added 2026-08-19 with DETERMINISTIC_FACT_TOOLS §5.
#: It is **not** reachable from a model's answer, and that is exactly why it is not folded into
#: `plan_refused`: a triple outside the offer set is refused by `plan_violations` before the
#: writer runs and ends the run as `plan_refused`, and everything still inside the offer set has
#: already passed every clause of §4.2 by construction. What is left is §4.4's detector-signal
#: assertion — the candidate's own `delta` or `gap` disagreeing with what the operation computed
#: — which is *"two code paths computing one number and differing"*, a defect in one of them and
#: never a judgement about a plan. Recording it as `plan_refused` would blame the model for a
#: disagreement between two deterministic computations, and dropping the derivation and writing
#: anyway would hand the writer a plan resting on a quantity that does not exist.
ACCEPTED = "accepted"
REJECTED = "rejected"
PLAN_REFUSED = "plan_refused"
DERIVATION_REFUSED = "derivation_refused"
DRAFT_REFUSED = "draft_refused"
#: The draft compiler refused the templates the writer returned — a slot naming a row that does
#: not exist, or a row that offers no legal string for the field the template asked it for. Its
#: own disposition and not `DRAFT_REFUSED`, because the two name different repairs: a refused
#: draft is a model that wrote the wrong thing, and a refused composition is a model that asked
#: for a value no trusted row can supply. A repair loop that could not tell them apart would
#: send the writer back to fix a sentence that is already right.
COMPOSITION_REFUSED = "composition_refused"
PROVIDER_FAILED = "provider_failed"

#: The closed set, declared once so every surface that renders it — the demo UI's outcome
#: payload, its tests, the CLI — reads the list from the module that owns it rather than
#: restating it and drifting by one value, which is exactly how `provider_failed` could have
#: shipped to a browser that had never heard of it.
DISPOSITIONS: tuple[str, ...] = (
    ACCEPTED, REJECTED, PLAN_REFUSED, DERIVATION_REFUSED, DRAFT_REFUSED, COMPOSITION_REFUSED,
    PROVIDER_FAILED)

#: The four stages a run can end at, under the names `rejected.json` has always written.
STAGE_PLANNER = "editorial_planner"
STAGE_DERIVATION = "derivation_tool"
STAGE_WRITER = "post_writer"
STAGE_COMPILER = "draft_compiler"
STAGE_VERIFIER = "deterministic_verifier"

#: Which stage *refused*, per disposition. `PROVIDER_FAILED` is deliberately absent: no stage
#: refused a provider fault, and which call was in flight when the transport failed is a fact
#: about the run rather than about the disposition — it travels on `ProviderFault` instead. A
#: table rather than a chain of conditionals because the demo UI renders the same three names
#: and reads them from here (see `demo_ui/api.py:_pipeline`).
REFUSING_STAGE: Mapping[str, str] = {
    PLAN_REFUSED: STAGE_PLANNER,
    DERIVATION_REFUSED: STAGE_DERIVATION,
    DRAFT_REFUSED: STAGE_WRITER,
    COMPOSITION_REFUSED: STAGE_COMPILER,
    REJECTED: STAGE_VERIFIER,
}

CONFIG_FILENAME = "story.yaml"


class StoryDemoError(RuntimeError):
    """Base of every refusal this path raises. Each names what it refused and why.

    A refusal is not a result: nothing is written and no directory is created, so an operator
    cannot mistake a run that never happened for one that produced no post. The dispositions
    that *are* results — a plan or draft §11/§12 refused, a draft §13 rejected, and a call the
    provider never answered — write their full artifact set instead.
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
    #: `demo.generation_store` — the **scalar** key, and the local provider's store when the
    #: mapping below does not name one. `generation_stores` is authoritative and
    #: `generation_store_for` is the accessor **every** call site now uses: the demo UI's own
    #: moved on 2026-08-19, so the reason this field used to give for surviving — "it is what
    #: `story/demo_ui/api.py` still reads" — is no longer true. What it is still for is a
    #: `config/story.yaml` written before the mapping existed: `generation_store_for` falls back
    #: to it for the local provider and for no other, so an older file resolves the same store.
    generation_store: str
    #: `demo.generation_stores` — provider id -> path (MULTI_PROVIDER_OPENAI §5.3). Authoritative.
    generation_stores: dict[str, str]
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
        stores = demo.get("generation_stores") or {}
        if not isinstance(stores, Mapping):
            raise DemoConfigurationError(
                f"{path}: demo.generation_stores must be a mapping of provider id to path, not "
                f"a {type(stores).__name__}")
        try:
            return cls(
                root=Path(root),
                raw=document,
                graph_run_id=str(demo["graph_run_id"]),
                out_root=str(demo.get("out_root", "data/story_demo")),
                generation_store=str(demo.get("generation_store", "")),
                generation_stores={str(key): str(value) for key, value in stores.items()},
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

    def generation_store_for(self, provider_id: str) -> Path | None:
        """The recorded store for one adapter, or `None` when none is recorded for it.

        `demo.generation_stores` is **authoritative** (MULTI_PROVIDER_OPENAI §5.3): a store is a
        provider's, because since `story-generation-v2` a row is keyed on the adapter that
        produced it and one provider's rows are a guaranteed miss for another. The scalar
        `demo.generation_store` is read only for the local provider and only when the mapping
        does not name one — it is the pre-2026-08-19 key, and it is not consulted for any other
        provider id.

        **It is no longer read anywhere but here**, which corrects the reason this method used
        to give for the scalar's survival: the demo UI's `_provider_for` called
        `config.generation_store` directly until 2026-08-19 and now calls this method, so the
        key is not dead but the sentence naming that call site was stale. What keeps it is
        backward compatibility with a `config/story.yaml` written before `generation_stores`
        existed, and nothing else.

        `None` and not a fallback. Falling through to another provider's file would produce a
        run whose every request missed, reported as a `MissingGenerationError` about a digest
        rather than as "OpenAI has no recorded store" — so the caller raises and names the
        provider instead.
        """
        configured = self.generation_stores.get(provider_id)
        if configured is None and provider_id == PROVIDER_LOCAL:
            configured = self.generation_store
        return self.resolved_path(configured) if configured else None


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
    that follows.

    **One detector runs, and it is the one the requested candidate id names** — `_detect_for`
    dispatches on the id's own slug since 643935f, so `metric_move`, `crossed_zero` and
    `unusual_level` candidates are reachable from `python -m story demo` and not only from the
    demo UI. This docstring said *"only §6.6's D4 detector runs — the demo's candidate is a
    cross-metric divergence"* until 2026-08-19, which was true of the code it was written for
    and false of the code beneath it. Running all four would spend a full canonical pass to
    produce candidates nothing selects, which is the part that has not changed.

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
    detected, detector_versions = _detect_for(candidate_id, points, graph_run_id=graph_run_id)
    candidate = select_candidate({found.candidate_id: found for found in detected}, candidate_id)

    builder = BoundedEvidencePackageBuilder(
        retriever, identity=identity, records=load.records, points=points,
        unreadable=load.unreadable)
    package = builder.build(candidate, candidate.evidence_request)
    return DemoInputs(
        identity=identity,
        freshness=report,
        candidate=candidate,
        package=package,
        detector_versions=detector_versions,
        policy_version=POLICY_VERSION,
    )


#: The detector each candidate id names, keyed by the slug `story.core.keys.candidate_id` puts
#: in its second segment — `slug(detector_id.split(":", 1)[-1])`, so `detector:metric_move`
#: reads `metric-move`. Every value is `(module, run)`; `run` takes the canonical points and
#: returns the candidates, which is the one shape the four detectors do **not** share —
#: `detect_cross_metric_divergence` wants a series where the other three want points.
_DETECTORS: dict[str, tuple[Any, Any]] = {
    "metric-move": (metric_move,
                    lambda points, run: detect_metric_moves(points, graph_run_id=run)),
    "acceleration": (acceleration,
                     lambda points, run: detect_acceleration(points, graph_run_id=run)),
    "trend-reversal": (trend_reversal,
                       lambda points, run: detect_trend_reversals(points, graph_run_id=run)),
    "cross-metric-divergence": (
        cross_metric_divergence,
        lambda points, run: detect_cross_metric_divergence(build_series(points),
                                                           graph_run_id=run)),
}


def _detect_for(candidate_id: str, points: Any, *, graph_run_id: str
                ) -> tuple[Sequence[StoryCandidate], dict[str, str]]:
    """Run **the one detector the requested id names**, and record the version that ran.

    §8b's demo ran `detect_cross_metric_divergence` and nothing else, with a stated reason:
    *"running the other three would spend a full canonical pass to produce candidates nothing
    selects"*. That reason is still right, and it is **not** an argument for the divergence
    detector specifically — it is an argument against running four. Hard-coding one of them made
    every `metric_move`, `acceleration` and `trend_reversal` candidate unreachable from
    `python -m story demo`, which is how
    `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d` came to have
    four recorded runs and no way to reproduce one from the CLI
    *(measured 2026-08-19, DETERMINISTIC_FACT_TOOLS §2)*.

    Dispatching on the id's own detector segment keeps the cost at one detector and makes all
    four reachable. The segment is not parsed hopefully: an id naming no known detector is
    refused here, with the four names, rather than producing an empty candidate set and a
    `CandidateNotFound` that would blame the id for naming a candidate that was never looked for.
    """
    segments = candidate_id.split(":")
    slug = segments[1] if len(segments) > 1 else ""
    found = _DETECTORS.get(slug)
    if found is None:
        raise CandidateNotFound(candidate_id, tuple(sorted(_DETECTORS)))
    module, run = found
    return (tuple(run(points, graph_run_id).candidates),
            {module.DETECTOR_ID: module.DETECTOR_VERSION})


# -- the model half ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderFault:
    """Which call went out and did not come back, and what the boundary raised.

    Two fields and no message. `stage` is the call site that was *attempting* — not a stage
    that judged anything — so a fault during the writer call is distinguishable from one during
    the planner call without the disposition having to encode it; the run id, the manifest and
    the panel all keep one `provider_failed`, and this says where. `error_class` is the type
    name from `providers/public.py`'s taxonomy, which already separates "nothing is listening"
    from "the budget ran out" from "a response arrived and could not be understood", and is the
    most specific true thing available without quoting an exception's text — that text names
    the server's address and stays in `rejected.json` and the log, as it does everywhere else.
    """

    stage: str
    error_class: str

    def as_dict(self) -> dict[str, str | bool]:
        """The fault as every surface renders it, including the fact it exists to record.

        `answer_produced: false` is stated rather than left to be inferred from an absent block:
        a reader of `rejected.json` looking at a run with no `codes` and no verification has, in
        every other disposition, been looking at a refusal *of an answer*. This one is not, and
        that is the sentence the whole disposition exists to make sayable.
        """
        return {"stage": self.stage, "error_class": self.error_class,
                "answer_produced": False}


@dataclass(frozen=True)
class DemoOutcome:
    """What the run produced, named so the CLI has nothing to decide.

    `verified` is `None` exactly when §13 never ran — a plan or a draft §11/§12 refused, or a
    provider fault — and the disposition says which. `ok` is `disposition == ACCEPTED` and
    nothing else: a rejected draft with a correct structured explanation is a valid demo result
    and a non-zero exit, and conflating "the run worked" with "the post passed" is how a demo
    starts overclaiming.

    `fault` is non-`None` **exactly** when the disposition is `PROVIDER_FAILED`, and it is a
    separate field rather than a fifth value stuffed into `refusal_codes` because a code list
    is what a model's answer produced and a fault produced no answer at all.
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
    fault: ProviderFault | None = None
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

    **A fault is not an answer, and gets its own disposition rather than a stage's.** The
    transport *does* retry inside the adapter, within its own bound; what arrives here has
    exhausted that, and calling it `plan_refused` would say a planner judged something when no
    planner ever ran. `_provider_failure` draws the line and argues where.

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
    # DETERMINISTIC_FACT_TOOLS §4.3 — computed **before** the planner, from the package and the
    # candidate and nothing else, and passed to the planner and to the executor as one tuple.
    # This module is the composition root for the derivation stage in the same way it already is
    # for the two model calls: `offers` lives in a stage the generation stage may not import, and
    # a second list computed downstream is the one way the printed offers and the checked offers
    # could disagree.
    offered = offers(package, inputs.candidate)
    results: list[GenerationResult] = []
    planned: PlannedStory | None = None
    written: WrittenStory | None = None
    #: The compiler's answer, kept beside `written` rather than replacing it. The writer's
    #: templates and the draft code built from them are two artifacts and the manifest records
    #: both: a reader asking *"did the model write that number or did code?"* has to be able to
    #: see the sentence before it was filled in.
    compiled: CompiledDraft | None = None
    derived: tuple[DerivedFact | EvidenceScopeFact, ...] = ()
    derivation: DerivationResult | None = None
    #: The two call sites' own results, kept **separately from the disposition** because a
    #: refused stage still made a call. `planned`/`written` are `None` on a refusal by
    #: definition — there is no plan and no draft — and reading the manifest's accounting off
    #: them recorded a run that spent nothing, which was measurably false.
    planner_result: GenerationResult | None = None
    writer_result: GenerationResult | None = None
    verified: VerifiedDraft | None = None
    disposition = ACCEPTED
    refusal = ""
    refusal_codes: tuple[str, ...] = ()
    fault: ProviderFault | None = None

    try:
        planned = plan_story(package, provider=provider, offered=offered,
                             max_tokens=config.planner_max_tokens)
        planner_result = planned.generation
    except EditorialPlanRejected as exc:
        disposition, refusal, refusal_codes = PLAN_REFUSED, str(exc), _codes_of(exc)
        planner_result = _generation_of(exc)
    except StoryProviderError as exc:
        disposition, refusal, refusal_codes, fault = _provider_failure(
            exc, refused=PLAN_REFUSED, stage=STAGE_PLANNER)
        planner_result = _generation_of(exc)
    if planner_result is not None:
        results.append(planner_result)

    if planned is not None:
        # §5's stage, between plan and draft. No provider, no clock, no network: the plan's
        # requests in, `DerivedFact`s out, and §7's evidence-scope facts minted from the package
        # alone beside them. It cannot raise a `StoryProviderError` and has no `try` around it
        # for that reason — every refusal it produces is a value on the result.
        derivation = execute_all(
            planned.plan.requested_derivations, package, inputs.candidate,
            # The composition root's three arguments, each one a thing the derivation stage
            # needs and may not import: a sibling stage owns the sign convention, and another
            # owns what counts as causal language. `public.DirectionOracle` argues why passing
            # the real function beats restating its 26-row table.
            direction=detector_config.quantity_direction,
            causal_language=causal_language_for(package),
            causal_marker_fact_ids=_causal_marker_fact_ids(package),
            offered=offered)
        derived = (*derivation.facts, *derivation.evidence_scope_facts)
        if derivation.refusals:
            disposition = DERIVATION_REFUSED
            refusal = "; ".join(
                f"{item.code.value}: {item.detail}" for item in derivation.refusals)
            refusal_codes = tuple(item.code.value for item in derivation.refusals)

    if planned is not None and disposition != DERIVATION_REFUSED:
        # The composition root's job again, and for the reason it computes `offered` above: the
        # slot table is the writer's whole vocabulary, `story/stages/generation/` may not import
        # `story/stages/composition/`, and a second table built downstream is the one way the
        # rows the model was shown and the rows the compiler resolves could disagree. One table,
        # printed into the prompt and read back by the compiler.
        slots = slot_table(package, derived, passages_backing_facts(package))
        try:
            written = write_story(
                package, planned.plan, provider=provider, derived_facts=derived,
                slots=slots,
                length_target=config.length_target, max_tokens=config.writer_max_tokens)
            writer_result = written.generation
        except DraftRejected as exc:
            disposition, refusal, refusal_codes = DRAFT_REFUSED, str(exc), _codes_of(exc)
            writer_result = _generation_of(exc)
        except StoryProviderError as exc:
            disposition, refusal, refusal_codes, fault = _provider_failure(
                exc, refused=DRAFT_REFUSED, stage=STAGE_WRITER)
            writer_result = _generation_of(exc)
        if writer_result is not None:
            results.append(writer_result)

    if planned is not None and written is not None:
        # S5 of docs/2026-08-23-deterministic-draft-compiler. The one stage between the model's
        # answer and the verifier, and it is deterministic: the templates in, a `Draft` whose
        # renderings, surfaces, spans and citations were all chosen by code out. It is a
        # separate `try` from the writer's because a refusal here is a different disposition —
        # the model answered, and what it asked for could not be filled.
        try:
            compiled = compile_draft(
                written.templates, package, planned.plan,
                derived_facts=derived, passages=passages_backing_facts(package),
                # The four fields no template carries and no slot can fill. `title` is the
                # model's prose and travels on `WrittenStory` rather than through the compiler's
                # substitution, because §13.15 gives a title no binding and refuses every
                # numeral in one — a slot there would insert exactly such a numeral. The other
                # three identify what wrote the sentences, and they are this module's to supply
                # for the reason every other cross-stage value here is: the compiler may not
                # import the generation stage to ask.
                title=written.title,
                # The provider's **identity** model id, not `GenerationResult.model_id`, which
                # is the wire value. The local server answers with the path it loaded the
                # weights from — `/home/<user>/models/…/Qwen3.5-9B-Q4_K_M.gguf` — and
                # `Draft.model_id` is rendered into the demo's API responses, where
                # `test_no_response_carries_an_absolute_path` refuses an operator's home
                # directory. `write_story` read it off the provider for the same reason.
                model_id=getattr(provider, "model_id", "") or (
                    writer_result.model_id if writer_result is not None else ""),
                style_profile_id=PLAIN_INVESTOR_STYLE.profile_id,
                prompt_version=WRITER_PROMPT_VERSION)
        except CompositionRefused as exc:
            disposition = COMPOSITION_REFUSED
            refusal = str(exc)
            refusal_codes = tuple(violation.code for violation in exc.violations)

    if planned is not None and compiled is not None:
        # The three freshness arguments are the *expected* identity §13.13 pins, supplied by
        # what resolved it. Passing the package's own values would make the check compare a
        # document with itself; these come from the graph run the gate just verified.
        verified = DeterministicVerifier(
            graph_run_id=inputs.identity.graph_run_id,
            run_complete_sha256=inputs.identity.run_complete_sha256,
            ontology_definition_hash=inputs.identity.ontology_definition_hash,
        ).verify(compiled.draft, package, planned.plan, derived_facts=derived)
        disposition = ACCEPTED if verified.passed else REJECTED

    story_run = _mint_run_id(inputs, config, provider=provider, results=results)
    directory = Path(out_dir) if out_dir is not None else (
        config.resolved_path(config.out_root) / story_run)
    manifest = _write_run(
        directory, inputs=inputs, config=config, story_run=story_run,
        disposition=disposition, planned=planned, written=written, compiled=compiled,
        verified=verified, derivation=derivation, offered=offered,
        planner_result=planner_result, writer_result=writer_result,
        refusal=refusal, refusal_codes=refusal_codes, fault=fault, results=results,
        provider=provider, live=live, now=now)
    return DemoOutcome(
        story_run_id=story_run, directory=directory, disposition=disposition,
        manifest=manifest, plan=planned.plan if planned else None,
        draft=compiled.draft if compiled else None, verified=verified,
        refusal=refusal, refusal_codes=refusal_codes, fault=fault,
        artifacts=manifest.artifacts)


def _causal_marker_fact_ids(package: StoryEvidencePackage) -> tuple[str, ...]:
    """Which of this package's facts carry a §13.10 causal marker, negated ones included.

    §7's third minting condition, and it is **not** redundant with the second. `causal_language`
    is `FORBIDDEN` when no cited span carries a marker its own clause does not negate; this asks
    the narrower question of whether a fact carries one *at all*, because a package whose table
    quote reads *"was not driven by"* is forbidden and still carries a marker, and the sentence
    *"this package supplies no explanation"* would be false about it. So the negated hits are
    kept here where `causal_language_for` drops them.

    Intersected with the package's own facts because `causal_marker_hits` also reports hits in
    passage excerpts, whose `source_id` is a passage id — §7's condition is about facts, and
    `explanatory_passages` being empty is the condition that covers the passages.
    """
    fact_ids = {fact.observation_id for fact in package.facts}
    return tuple(sorted({hit.source_id for hit in causal_marker_hits(package)} & fact_ids))


def _provider_failure(
    exc: StoryProviderError, *, refused: str, stage: str,
) -> tuple[str, str, tuple[str, ...], ProviderFault | None]:
    """Sort one `StoryProviderError` into "the model answered" or "nothing answered".

    **The line is the class, not the carried result, and the two are not the same line.**
    `StoryProviderSchemaError` means one thing wherever it is raised: a response arrived, it
    parsed as JSON, and the JSON does not satisfy the schema the request pinned. That is a
    model's answer being refused — §15.3's own words, "a schema violation is the model's answer,
    not a transport fault" — so it keeps the stage's disposition and its `violations` go on as
    codes, which are real and were emitted by a real answer.

    The subtlety the accounting fix left behind is that **only a stage-raised one carries a
    `GenerationResult`**: an adapter raises it mid-translation, before the result object exists,
    so nothing is appended to `results` and the manifest honestly counts no call. Keying the
    disposition on that absence instead — the obvious alternative, and the one this function
    exists to reject — would report `provider_failed` for a response the server really sent and
    the adapter really parsed, which is a fresh falsehood of exactly the kind `provider_failed`
    was added to remove. The absence belongs to the *accounting*, where it already lives; the
    disposition belongs to *what happened*.

    Everything else in the taxonomy — configuration, unavailable, timeout, transport, response —
    is a run that made a request and got no usable answer out of it, including a 401 with a
    perfectly well-formed error body: an error envelope is not an answer to the question asked.
    A configuration error is the outlier in that list, since nothing was sent at all, and it is
    still not worth a sixth disposition: it is *further* from a model's judgement rather than
    nearer to one, and `error_class` already separates "never sent" from "sent and unanswered"
    for the one reader who needs the difference.
    """
    if isinstance(exc, StoryProviderSchemaError):
        return refused, str(exc), _codes_of(exc), None
    # No codes. A fault emitted none, and `_codes_of` would happily read `violations` off a
    # class that does not have them and hand back an empty tuple that looked like a decision.
    return PROVIDER_FAILED, str(exc), (), ProviderFault(
        stage=stage, error_class=type(exc).__name__)


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


def _generation_of(exc: Exception) -> GenerationResult | None:
    """The generation a refusal refused, or `None` when nothing was ever generated.

    **The distinction is the whole point and it is measured, not theoretical.** §11 and §12
    refuse an answer the model *gave*: the request went out, the tokens were spent, the
    provider's store holds the row. §14's accounting used to be assembled from `planned` and
    `written`, which are `None` on exactly those refusals — so
    `data/story_demo/story-v1-b949ecf8bbd6` (a real `gpt-5-nano` run, 2026-08-19) wrote a
    `generations.jsonl` holding its planner row beside a manifest reading
    `generation_calls: 0`, `total_tokens: 0` and `planner_provider_model: {}`, and
    `story-v1-98a0c8e10720` reported one call for a run that made two.

    A `StoryProviderUnavailable`, a `StoryProviderTimeout`, a `StoryProviderTransportError` or
    a `StoryProviderResponseError` never got an answer, and an adapter's own
    `StoryProviderSchemaError` never built a `GenerationResult` — all of them arrive here with
    no `generation` and record nothing, which is the truthful zero. Read through `getattr` for
    `_codes_of`'s reason: the shape belongs to the refusal's class, and a fifth refusal type
    should not need this function edited to be *recorded*.
    """
    generation = getattr(exc, "generation", None)
    return generation if isinstance(generation, GenerationResult) else None


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
        provider_id=_provider_id(provider),
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


def _provider_id(provider: Any) -> str:
    """Which adapter ran. Read defensively; a blank one is refused where the id is minted.

    `getattr` and not an attribute access, for the reason `_model_id` uses it: the object here
    may be a decorator (`ObservedProvider`, `EditedSystemProvider`) or a test double, and a
    protocol Python does not enforce at runtime is not a guarantee that the attribute exists.

    Nothing is defaulted. `mint_story_run_id` refuses a blank `provider_id` through `_require`,
    and that refusal is the point: a decorator that forgot to forward the value would otherwise
    mint a run id and a directory that claim the run had no provider, and — worse — a Qwen run
    and an OpenAI run through two forgetful decorators would collide on one id again, which is
    exactly the defect (F2) the input was added to close.
    """
    return str(getattr(provider, "provider_id", "") or "")


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


def _call_site_provenance(provider: Any, result: GenerationResult | None, *,
                          prompt_version: str, schema_name: str, max_tokens: int
                          ) -> dict[str, Any]:
    """One call site's provenance, read off **that call's own result** (§5.2).

    The planner and the writer are two requests and the brief asks for them separately; the
    manifest's four scalars describe one model and cover both. `provider_model_id` is the value
    *this* request came back with — on a replay, the row's recorded wire value — rather than the
    first result's or the configured name, because the whole reason that field exists is to
    notice a model swapped behind an unchanged `model_id`, and reading one call's answer for
    another's would defeat it.

    `{}` when the call site produced no result. A run whose planner refused never built a writer
    request, and a block assembled from configuration would record a request that was never
    made — the same argument `_token_totals` makes for reporting zeroes instead of remembered
    numbers.

    **"Produced no result" is narrower than "was refused", and conflating the two was a
    defect.** §11 and §12 refuse an answer that arrived; the request was built, the tokens were
    spent and this block describes it. Only a stage that never got an answer back — a
    transport fault, a timeout, a plan made from another package — leaves it empty. The result
    therefore reaches this function from `run_demo`'s own `planner_result`/`writer_result`
    rather than from `planned`/`written`, which are `None` on precisely the refusals that did
    make a call.
    """
    if result is None:
        return {}
    return {
        "provider_id": _provider_id(provider),
        "model_id": _model_id(provider),
        "provider_model_id": result.model_id or _model_id(provider),
        "prompt_version": prompt_version,
        "schema_name": schema_name,
        "max_tokens": int(max_tokens),
    }


def _provider_settings(provider: Any, results: list[GenerationResult],
                       max_output_tokens_sent: list[int]) -> dict[str, Any]:
    """What the request was actually parameterised with — §5.2's third manifest addition.

    `temperature` is the value the call site pins (§15.1). `temperature_sent` is whether it
    reached the wire, and it is a **separate** field because the two genuinely differ: OpenAI's
    reasoning models refuse the parameter — `gpt-5-nano` answers a `temperature: 0.0` with
    `400 Unsupported parameter` (measured 2026-08-19, MULTI_PROVIDER_OPENAI §3) — so a manifest
    that printed only `temperature: 0.0` would be making a claim about a value never sent.

    `null` rather than a guess wherever the adapter states nothing. A replay-only run sent no
    request at all, so `temperature_sent` is `null` and the three adapter settings are `null`
    with it; that is the truthful record, and inventing `true` from the fact that the *recorded*
    row carried a temperature would be recording the original run's parameters as this one's.
    The settings are read off the provider's own `StoryProviderConfig` through `getattr`, for
    `_provider_id`'s reason: the object may be a decorator or a double.

    **`max_output_tokens` was one field and is now two, because the one it was did not match
    this docstring's own sentence** *(2026-08-19, found by review)*. It reported the config's
    `max_output_tokens` — 4096 for OpenAI, 2048 locally — while the body carried
    `max_output_tokens: 2048`, which is the *call site's* `max_tokens` (`generation.planner_max_tokens`
    and `generation.writer_max_tokens`, §15.3). Those are two different numbers and both are
    worth keeping, so both are recorded under names that say which is which rather than one
    being made to stand for the other:

    * `max_output_tokens_ceiling` — the configured cap. Not what any request carried, but what
      `StoryProviderConfig.validated()` checks against `context_tokens`, and the bound a call
      site could not have exceeded. A run whose ceiling moved is a run whose transport changed.
    * `max_output_tokens_sent` — what the bodies actually carried, and the only one that answers
      the question this block is for.

    `…_sent` is a **list** and not a scalar because `max_tokens` is a per-call-site parameter
    while this block is per-run: a scalar would have to pick the planner's budget or the
    writer's and call it the run's. It holds the budgets of the calls that *came back* — the
    same rule `_call_site_provenance` follows, so a §11-refused run reports the planner's budget
    alone and does not invent the writer's — and it is **empty for a replay**, gated on the same
    absent `config` as the three fields above it rather than on a second test, because a run
    with no adapter issued no body and a budget it never sent is exactly the claim this repair
    removed.
    """
    config = getattr(provider, "config", None)
    sent: bool | None = None
    for result in results:
        # The adapter's own report, and the first place looked: it is the only thing that knows
        # what it put in the body, and a per-model capability flag can be right about the model
        # while the request that was actually built was different.
        if "temperature_sent" in (result.metadata or {}):
            sent = bool(result.metadata["temperature_sent"])
            break
    if sent is None:
        declared = getattr(config, "supports_temperature", None)
        sent = None if declared is None else bool(declared)
    return {
        "temperature": float(PINNED_TEMPERATURE),
        "temperature_sent": sent,
        "reasoning_effort": getattr(config, "reasoning_effort", None),
        "max_output_tokens_ceiling": getattr(config, "max_output_tokens", None),
        "max_output_tokens_sent": ([] if config is None
                                   else sorted(set(max_output_tokens_sent))),
        "store_responses": getattr(config, "store_responses", None),
    }


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
            compiled: CompiledDraft | None,
            verified: VerifiedDraft | None, derivation: DerivationResult | None,
            offered: Sequence[Any]) -> dict[str, Any]:
    """What the run held, counted. The four derivation rows answer four different questions.

    `derivations_offered` is what code put in front of the planner, `derivations_requested` is
    what the plan asked for, `derived_facts` is what came back and `derivation_refusals` is what
    did not. Three of the four would be derivable from the artifacts and the fourth would not:
    the offer set is not written anywhere, and a run that recorded only *"one derived fact"*
    could not say whether the planner chose one of twelve or one of one.

    `evidence_scope_facts` is counted separately from `derived_facts` for `DerivationResult`'s
    own reason: §7's kind is minted by code from the package and is never requested, so folding
    it into the derived count would let a reader ask which plan request produced it.
    """
    package = inputs.package
    counts: dict[str, Any] = {
        "facts": len(package.facts),
        "primary_passages": len(package.primary_passages),
        "context_passages": len(package.context_passages),
        "counter_evidence": len(package.counter_evidence),
        "package_warnings": len(package.warnings),
        "freshness_checks": len(inputs.freshness.checks),
        # The writer's count and not the compiler's, deliberately: a run whose templates were
        # refused wrote sentences and produced no draft, and a zero here would report that it
        # wrote nothing. `slots_filled` is the compiler's own half of the same question.
        "sentences": len(written.templates) if written else 0,
        "slots_filled": len(compiled.slots) if compiled else 0,
        "derivations_offered": len(offered),
        "derivations_requested": 0 if derivation is None else (
            len(derivation.facts) + len(derivation.refusals)),
        "derived_facts": 0 if derivation is None else len(derivation.facts),
        "derivation_refusals": 0 if derivation is None else len(derivation.refusals),
        "evidence_scope_facts": (
            0 if derivation is None else len(derivation.evidence_scope_facts)),
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
    compiled: CompiledDraft | None,
    verified: VerifiedDraft | None,
    #: `None` exactly when the derivation stage never ran — a planner refusal or a provider
    #: fault. An empty `DerivationResult` is a different thing and says so: the stage ran, the
    #: plan requested nothing, and §7 established no absence either.
    derivation: DerivationResult | None,
    #: What `offers` put in front of the planner. Counted rather than written: the offer set is
    #: a pure function of the package and the candidate, both of which are artifacts here, so a
    #: reader can rebuild it exactly — and a run that also wrote it out would be storing a
    #: derivable list beside the two inputs it derives from.
    offered: Sequence[Any],
    #: Each call site's own result, and **not** `planned.generation`/`written.generation`: those
    #: exist only when the stage was accepted, and a refused stage still made a request whose
    #: provenance §5.2 asks for. `None` means no request came back, which is the only thing
    #: that leaves the block empty.
    planner_result: GenerationResult | None,
    writer_result: GenerationResult | None,
    refusal: str,
    refusal_codes: tuple[str, ...],
    #: Non-`None` exactly on `PROVIDER_FAILED`, and the only thing that knows which call was in
    #: flight — the disposition deliberately does not encode it.
    fault: ProviderFault | None,
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
    if derivation is not None:
        write(DERIVED_FACTS_FILENAME, _render_json(_derived_facts_payload(derivation)))
    if compiled is not None:
        write(DRAFT_FILENAME, _render_json(compiled.draft.model_dump(mode="json")))
        # S5. The provenance the `Draft` deliberately does not carry: which slot of which
        # template became which span. It is a separate artifact rather than a field on
        # `DraftSentence` because `Draft.digestible_payload()` feeds `draft_content_sha256`, and
        # a field on the type would re-key every artifact already written for a value nothing
        # verifies.
        write(COMPOSITION_FILENAME, _render_json(_composition_payload(written, compiled)))
    if verified is not None:
        write(VERIFICATION_FILENAME, _render_json(verified.model_dump(mode="json")))
    if disposition == ACCEPTED and compiled is not None:
        # Rendered from the structured draft and never from the model's prose (§12).
        write(POST_FILENAME, render_markdown(compiled.draft))
    if disposition != ACCEPTED:
        write(REJECTED_FILENAME, _render_json(_rejection_payload(
            inputs, disposition, verified, refusal, refusal_codes, fault)))
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
        provider_id=_provider_id(provider),
        model_id=_model_id(provider),
        provider_model_id=_provider_model_id(provider, results),
        temperature=PINNED_TEMPERATURE,
        max_tokens=config.planner_max_tokens,
        schema_digests=schema_digests_for(inputs.package),
        planner_provider_model=_call_site_provenance(
            provider, planner_result,
            prompt_version=PLANNER_PROMPT_VERSION, schema_name=PLANNER_SCHEMA_NAME,
            max_tokens=config.planner_max_tokens),
        writer_provider_model=_call_site_provenance(
            provider, writer_result,
            prompt_version=WRITER_PROMPT_VERSION, schema_name=WRITER_SCHEMA_NAME,
            max_tokens=config.writer_max_tokens),
        provider_settings=_provider_settings(
            provider, results,
            # The budgets the requests that came back actually carried, taken from the two
            # call-site results rather than from the config: a refused planner still spent its
            # own budget, and a writer that never ran never had one.
            [budget for result, budget in ((planner_result, config.planner_max_tokens),
                                           (writer_result, config.writer_max_tokens))
             if result is not None]),
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
        counts=_counts(inputs, written, compiled, verified, derivation, offered),
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
            # The one derivation version there is, and it has a real home rather than a derived
            # stand-in: `derivation/public.py` declares `TOOL_VERSION`, every `fact:derived:` id
            # digests it, and a redefinition therefore mints new facts instead of silently
            # re-meaning existing ones. That is precisely what `verifier_version: None` above
            # records the verifier as **not** having, so the two sit beside each other on
            # purpose.
            "derivation_tool_version": DERIVATION_TOOL_VERSION,
            "disposition": disposition,
            # `null` on every other disposition. Here as well as in `rejected.json` because the
            # manifest is the file a run directory is *indexed* by — it is the completion marker
            # and the thing a later reader opens first — and a run reading `provider_failed` with
            # no other field in this block would be a run whose manifest cannot say which of its
            # two calls never came back.
            "provider_fault": None if fault is None else fault.as_dict(),
            "generation_mode": "live" if live else "replay",
            "length_target": config.length_target,
            "writer_max_tokens": config.writer_max_tokens,
            "freshness_passed": inputs.freshness.passed,
        },
    }
    (directory / MANIFEST_FILENAME).write_text(_render_json(payload), encoding="utf-8")
    return manifest


def _composition_payload(written: WrittenStory, compiled: CompiledDraft) -> dict[str, Any]:
    """The templates the model returned, and the fills code made from them.

    Two lists rather than one nested structure, for the reason `_derived_facts_payload` keeps
    its rows flat: a fill names its sentence by index, and a reader diffing two runs wants to
    see *which fill moved* rather than to walk a tree to find it.

    The template text is stored with its `{{slots}}` intact. That is the whole point of the
    artifact — `draft.json` beside it holds the same sentence with every slot resolved, and the
    pair is the evidence for which half of the sentence each author wrote.
    """
    return {
        "prompt_version": WRITER_PROMPT_VERSION,
        "templates": [
            {"index": template.index, "text": template.text,
             "kind": template.kind.value, "rests_on": list(template.rests_on)}
            for template in written.templates
        ],
        "fills": [
            {"sentence_index": fill.sentence_index, "handle": fill.handle,
             "field": fill.field, "inserted": fill.inserted,
             "char_start": fill.char_start, "char_end": fill.char_end}
            for fill in compiled.slots
        ],
    }


def _derived_facts_payload(derivation: DerivationResult) -> dict[str, Any]:
    """§3's artifact: the facts, §7's facts, the refusals, and the version that computed them.

    Three lists rather than one, for the reason `DerivationResult` keeps three fields. A derived
    fact was requested and granted; an evidence-scope fact was never requested at all, because
    absence is not a calculation; a refusal was requested and not granted. One list carrying all
    three would leave a reader unable to ask which plan request produced a row, and that
    question has a different answer for each kind.

    `tool_version` is written at the top level as well as on every row. The rows carry it because
    it is a digest input to their ids; the file carries it because a run whose plan requested
    nothing has no row to read it off, and *"which tool version did this run have"* is still a
    question about that run.
    """
    return {
        "tool_version": DERIVATION_TOOL_VERSION,
        "facts": [fact.model_dump(mode="json") for fact in derivation.facts],
        "evidence_scope_facts": [
            fact.model_dump(mode="json") for fact in derivation.evidence_scope_facts],
        "refusals": [
            {"code": refusal.code.value, "operation": refusal.operation,
             "from_fact_id": refusal.from_fact_id, "to_fact_id": refusal.to_fact_id,
             "rule_id": refusal.rule_id, "detail": refusal.detail}
            for refusal in derivation.refusals],
    }


def _rejection_payload(inputs: DemoInputs, disposition: str, verified: VerifiedDraft | None,
                       refusal: str, refusal_codes: tuple[str, ...],
                       fault: ProviderFault | None = None) -> dict[str, Any]:
    """Why this run produced no post — one file for all four ways that happens.

    A §13 rejection carries `RejectedDraft` whole, checks included: a rejection that dropped its
    WARNs and ANNOTATEs would report the draft's worst sentence instead of the draft, and the
    `examined` denominators are what say which checks even ran. A §11 or §12 refusal has no
    verification to carry and says so in `stage`, so the two are never read as one another.

    **A provider fault says a different sentence in the same field.** `stage` is read off the
    fault rather than off the disposition, because it names the call that was *attempted* and
    not a stage that refused anything — and the `provider_fault` block beside it states, in
    data, that no answer was produced and what the boundary raised. `codes` stays `[]`: a
    refusal code is something a model's answer earned, and inventing one here — even a
    plausible `provider_unavailable` — would put a string into the panel's code catalogue that
    no stage in this repository declares.
    """
    payload: dict[str, Any] = {
        "candidate_id": inputs.candidate.candidate_id,
        "package_id": inputs.package.package_id,
        "disposition": disposition,
        "stage": (fault.stage if fault is not None
                  else REFUSING_STAGE.get(disposition, disposition)),
        "codes": list(refusal_codes),
        "detail": refusal,
    }
    if fault is not None:
        payload["provider_fault"] = fault.as_dict()
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
        "ontology_definition_hash", "config_hash", "prompt_version", "provider_id", "model_id",
        "provider_model_id", "temperature", "max_tokens", "schema_digests",
        "detector_versions", "policy_version", "ranking_policy_version", "selection", "budget",
    )


__all__ = [
    "ACCEPTED",
    "CANDIDATE_FILENAME",
    "DISPOSITIONS",
    "DERIVATION_REFUSED",
    "COMPOSITION_FILENAME",
    "COMPOSITION_REFUSED",
    "DERIVED_FACTS_FILENAME",
    "DRAFT_FILENAME",
    "DRAFT_REFUSED",
    "GENERATIONS_FILENAME",
    "MANIFEST_FILENAME",
    "PACKAGE_FILENAME",
    "PLAN_FILENAME",
    "PLAN_REFUSED",
    "POST_FILENAME",
    "PROVIDER_FAILED",
    "REFUSING_STAGE",
    "REJECTED",
    "REJECTED_FILENAME",
    "SELECTION_MODE",
    "STAGE_DERIVATION",
    "STAGE_PLANNER",
    "STAGE_VERIFIER",
    "STAGE_WRITER",
    "VERIFICATION_FILENAME",
    "CandidateNotFound",
    "DemoConfig",
    "DemoConfigurationError",
    "DemoInputs",
    "DemoOutcome",
    "FreshnessRefused",
    "ProviderFault",
    "StoryDemoError",
    "resolve_demo_inputs",
    "run_demo",
    "schema_digests_for",
    "select_candidate",
    "verifier_gate_digest",
]
