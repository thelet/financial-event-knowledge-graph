"""D6 — the §8b demo path, driven end to end.

Offline by default and from committed fixtures. The `neo4j`-marked tests at the bottom
re-derive the candidate and the package from the live graph; the `live`-marked one calls the
model server.

**The four stores moved on 2026-08-19 and were re-keyed, not re-recorded.** They now live in
`fixtures/story_demo/local_openai_compatible/`, because MULTI_PROVIDER_OPENAI §5.1 made
`provider_id` a `request_identity` input and `IDENTITY_VERSION` `story-generation-v2` — a
recorded generation is a *provider's*, one provider's rows are a guaranteed miss for another,
and a flat directory would hold files nothing distinguishes by looking at it. Every
`request_sha256` moved (planner `fa975557990f…` -> `de1df732c2d4…`, writer `940d6f6c43e6…` ->
`4e147f30fdb5…`) and **every `raw_content` and `content_sha256` is byte-identical to the
2026-08-18 capture**: each request was rebuilt through the real demo path, looked up under the
old digest and written back under the new one.
`test_the_rekeyed_stores_replay_to_the_bytes_they_replayed_to_before_the_rekey` pins that against
`sha256` values measured in a worktree of `d72ca64`, and the `live`-marked writer test at the
bottom proves the other half: the running server's answer still lands on the new committed key.
So every filename below is relative to that subdirectory, and every claim in the paragraphs that
follow was made about these same bytes.

**The recorded responses in `fixtures/story_demo/local_openai_compatible/generations.jsonl` are
genuine Qwen output.**
Re-captured 2026-08-18 at TABLE_CELL_CITATIONS S7a from `http://127.0.0.1:8080` serving
`/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf`, against the package
`fixtures/story_demo/evidence_package.json` in the same directory — the planner's answer at
`PLANNER_MAX_TOKENS` and the writer's at `length_target: 4`. Two rows, nothing hand-edited,
both `finish_reason: stop`, both lifted whole out of one `--live` run's own
`generations.jsonl`. The demo therefore replays something the model actually produced, and the
manifest names the model it came from. That run cost 7,398 prompt and 1,328 completion tokens
across its two calls, and its disposition is **accepted**.

**Both rows are new, and every earlier row is unreachable.** TABLE_CELL_CITATIONS S3 and S4
moved `PACKAGE_VERSION` 1.2.0 → 1.3.0 and `WRITER_PROMPT_VERSION` 1.3.0 → 1.4.0, and the D4
`package_content_digest` with them — now `76a9c8ac2a2a…` on package `…:4e4363b11373`, rebuilt
from the graph at S7a so its two table facts carry real cell coordinates instead of `null`.
Both prompts embed the package id and the writer's schema changed shape, so every row keyed
under the old rule is unreachable: planner `fa975557990f…`, writer `940d6f6c43e6…`. **All four
stores in this directory share those two request digests**, and all four carry the same live
planner row; what distinguishes them is the writer's answer.

**The 5-in-6 / 1-in-6 split this docstring used to publish did not survive the prompt bump, and
the measurement is recorded rather than the old sentence kept.** Under 1.3.0 six `--live` runs
of this candidate wrote identical prose and moved exactly one field, `calculation.operation`:
five declared `difference` and were accepted, one declared `compare_levels` and was refused.
Under 1.4.0 that variation is gone. **Nineteen consecutive live writer calls — seven full
`python -m story demo --live` runs and twelve direct `write_story` calls against the same
package and plan — returned the byte-identical answer every time** (`content_sha256`
`b14908e636d5…`), always `difference`, always accepted *(measured 2026-08-18)*. The refusal
could not be re-recorded, and no attempt was made to shop for one.

So the two rejected stores below are what they say they are, and one of them changed kind:

* `generations.jsonl` — an accepted run, and the store `config/story.yaml` points the demo at.
  It is now the only outcome observed in nineteen calls, not the majority of six.
* `generations_rejected_recorded.jsonl` — **the 2026-08-13 refusal, with its two citation
  objects migrated and nothing else touched.** Its `{passage_id, quote}` pairs became
  `{"evidence_id": "ev:…#p139:r5c2"}` and `{"evidence_id": "ev:…#p139:r11c2"}` — the two
  handles the package mints for the facts those sentences bind, and the two the live model
  itself wrote — because the 1.4.0 schema has no `passage_id` or `quote` property and the old
  row is rejected before §12 sees it. **The mis-declaration the fixture exists for is
  untouched.** The sentence *"The difference between the two margins is 15.9 percentage
  points."* is a **size with no direction**, while the declaration says `compare_levels` over
  `(gaap, adjusted)` with `left < right` — which recomputes perfectly. §13.14 refuses it as
  `comparative_not_supported_by_text`, exactly the rule `claims._comparison_text_findings`
  states in its own docstring: *"that sentence states a size and no direction, so it is a
  `difference` or a `delta_pp`, not a comparison."* **The model mis-declared the operation;
  nothing in §13 changed.** It is no longer a byte-for-byte capture of one call, and this
  paragraph is where that is said rather than left for a reader to discover.

Keeping the refusal matters more than which store is default: it is the only fixture in this
directory whose *prose and declaration* a real model produced and the verifier caught. The
synthetic pair below proves the check *fires*; this one proves it fires on something a model
actually wrote. The nondeterminism the old split rested on is still a design fact
`story/providers/generation_store.py` states — byte-identical replay is achievable,
byte-identical generation is not promised — and nineteen identical calls do not repeal it.

**Two synthetic stores sit beside them, and neither is a recording.** Both are the genuine
refusal above with exactly one edit, to sentence 2's prose, and the declaration is untouched in
both — so each is a test of what the *words* say against a calculation that still recomputes.
Both had their citations migrated at S7a the same way and for the same reason:

* `generations_accepted_synthetic.jsonl` — *"The GAAP Gross Margin was 15.9 percentage points
  lower than the Adjusted Gross Margin."*, which is true and which the 2026-08-04 recording
  produced verbatim. It drives the accepted branch through a **comparative**, where
  `generations.jsonl` now reaches it through a `difference`; the two exercise different §13.14
  paths to one disposition.
* `generations_rejected_synthetic.jsonl` — the same sentence **reversed**, *"The Adjusted Gross
  Margin was 15.9 percentage points lower than the GAAP Gross Margin."*, false by 15.9 points.
  It is kept rather than dropped as redundant because it fails §13.14 a *different* way from the
  genuine pair: the genuine draft carries no comparative at all, this one carries a comparative
  naming the two sides in the wrong order — *"adjusted_gross_margin before and gaap_gross_margin
  after"*. It is the attack a recomputation on its own would have accepted.

Both are named `synthetic` because they are, and no test presents either as a recording.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from story import cli, pipeline
from story.core.graph_identity import GraphIdentity
from story.core.models import (
    GenerationResult,
    HealthStatus,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.pipeline import (
    ACCEPTED,
    DRAFT_REFUSED,
    REJECTED,
    SELECTION_MODE,
    CandidateNotFound,
    DemoConfig,
    DemoInputs,
    FreshnessRefused,
    StoryDemoError,
    run_demo,
    select_candidate,
)
from story.providers.generation_store import GenerationStore, ReplayingStoryGenerationProvider
from story.providers.public import PROVIDER_LOCAL, PROVIDER_OPENAI
from story.stages.detection import cross_metric_divergence
from story.stages.detection.canonicalization import POLICY_VERSION
from story.stages.freshness import FreshnessReport

REPO_ROOT = Path(__file__).resolve().parents[2]
#: **The stores moved into a provider-named directory on 2026-08-19** (MULTI_PROVIDER_OPENAI
#: §5.3). A recorded generation is a *provider's*: since `story-generation-v2` the request digest
#: covers the adapter, so one provider's rows are a guaranteed miss for another and a flat
#: directory would be a directory of files that cannot be told apart by looking at it.
FIXTURES = Path(__file__).parent / "fixtures" / "story_demo"
STORES = FIXTURES / PROVIDER_LOCAL

#: §8b's manually selected candidate, and the id the demo re-derives and refuses to proceed
#: without. Pinned as a string here because that is exactly how an operator supplies it.
CANDIDATE_ID = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
                "opendoor:2022Q3:9682f1c1c85a")
GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: What the recorded model id is, and what the store's rows are keyed under.
MODEL_ID = "Qwen3.5-9B-Q4_K_M.gguf"

#: The other half of that key since 2026-08-19. Named rather than spelled, and read from
#: `providers/public.py` rather than retyped, so the fixtures and the boundary cannot drift.
PROVIDER_ID = PROVIDER_LOCAL

#: Every artifact §8b names, plus §14's replay store. `post.md` and `rejected.json` are the two
#: that are mutually exclusive, so a reader can tell the disposition from the listing alone.
ALWAYS_WRITTEN = {
    "candidate.json", "evidence_package.json", "editorial_plan.json", "draft.json",
    "verification_report.json", "generations.jsonl", "demo_manifest.json",
}

#: The stores this module's docstring describes. Named rather than spelled at each call site so
#: that "which branch is this test driving" is one word rather than a filename a reader has to
#: compare character by character against another filename.
ACCEPTED_STORE = "generations_accepted_synthetic.jsonl"
REJECTED_STORE = "generations_rejected_synthetic.jsonl"

#: A **recording**, not a synthetic: the one `--live` run in six that the verifier refused, with
#: its citations migrated to the 1.4.0 handle form at S7a and its prose and its declaration
#: untouched — see this module's docstring for why it could not simply be re-run. Kept because
#: it is the only fixture in this directory where a real model made a real mistake and §13
#: caught it — the synthetic pair is two hand-edits of a sentence, which proves the check fires
#: but not that it fires on anything a model actually writes.
REJECTED_RECORDING = "generations_rejected_recorded.jsonl"


def _read(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def demo_inputs() -> DemoInputs:
    """The live 2022Q3 slice as it came off the graph, byte-for-byte.

    Assembled here rather than mocked: every value below was produced by
    `resolve_demo_inputs` against `graph-v1-0483dc6b4b10` and written out unchanged, so a test
    driving `run_demo` over it is driving the same object the live path hands over.

    **`evidence_package.json` was rebuilt from the graph on 2026-08-18** (TABLE_CELL_CITATIONS
    S7a) because the 2026-08-04 capture predates S1: its two table facts carried `cell: null`
    and therefore the *narrative* handle form, which is honest about the file and wrong about
    the corpus. `candidate.json` and `graph_identity.json` re-derived byte-identical and were
    left alone, which is the evidence that only the packaging shape moved.

    **`freshness_report.json` was deliberately not rewritten.** Rebuilding it changes only the
    `detail` strings, which spell the absolute path of the checkout that produced it — the
    committed file names `…/FKG-story-agent-impl` and a rebuild here would name
    `…/Prototyping-Financial-Knowlege-Graph`. Neither is a fact about the corpus, no digest
    covers them, and the live test compares `(name, passed)` pairs, so re-stamping would bake
    one machine's directory layout into a fixture in exchange for nothing.
    """
    return DemoInputs(
        identity=GraphIdentity.model_validate(_read("graph_identity.json")),
        freshness=FreshnessReport.model_validate(_read("freshness_report.json")),
        candidate=StoryCandidate.model_validate(_read("candidate.json")),
        package=StoryEvidencePackage.model_validate(_read("evidence_package.json")),
        detector_versions={cross_metric_divergence.DETECTOR_ID:
                           cross_metric_divergence.DETECTOR_VERSION},
        policy_version=POLICY_VERSION,
    )


def replaying(filename: str = "generations.jsonl") -> ReplayingStoryGenerationProvider:
    """Replay-only, with no inner provider: a miss raises rather than reaching for a GPU."""
    return ReplayingStoryGenerationProvider(
        GenerationStore(STORES / filename), provider_id=PROVIDER_ID, model_id=MODEL_ID)


class CountingProvider:
    """A `StoryGenerationProvider` that records every call and answers none of them.

    Satisfies the protocol structurally and raises on `generate`, which is what makes "refused
    before any model call" an assertion about behaviour rather than about ordering in the
    source: if anything reaches the model, this fails loudly instead of quietly succeeding.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self.calls: list[str] = []

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="counting")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        self.calls.append(schema_name)
        raise AssertionError(f"the model was called for {schema_name!r}")


class BadWriterProvider:
    """Replays the genuine plan, then hands the writer a draft §12 cannot construct.

    The deliberately bad generation: a citation naming an evidence id this package minted for
    no fact. §12 refuses it before §13 runs, which is the `draft_refused` disposition — a
    different outcome from a draft the verifier rejected, and one the demo must not report as
    the same thing.

    **The defect had to move with the contract, and the new one is the same kind of mistake.**
    Until TABLE_CELL_CITATIONS S4 this provider quoted a string absent from the passage it
    named and was refused as `citation_quote_not_in_passage`. Under 1.4.0 a citation has one
    field, `evidence_id`, so that answer no longer reaches §12 at all — `writer_schema` rejects
    the extra properties and the run fails as a schema violation, which is a different stage and
    a different claim. `r99c99` is well-formed, is on the right passage, and names a cell no
    fact in this package was read from: `unresolvable_evidence_handle`, refused by
    `_citations_from` before a span is ever resolved.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self._inner = replaying()

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="bad-writer")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        if schema_name != "story_post_draft":
            return self._inner.generate(
                system=system, prompt=prompt, schema=schema, schema_name=schema_name,
                max_tokens=max_tokens, temperature=temperature)
        content = {
            "title": "Two Gross Margins",
            "sentences": [{
                "text": "Opendoor reported an Adjusted Gross Margin of 3.3 percent.",
                "kind": "reported",
                "fact_bindings": [{
                    "fact_id": "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:3eabe78a6d25",
                    "rendered": "3.3 percent",
                    "metric_surface": "Adjusted Gross Margin",
                    "period_surface": "",
                }],
                "calculation": [],
                "citations": [{
                    "evidence_id": "ev:norm:0001801169:0001801169-22-000108:"
                                   "open-20220930.htm#p139:r99c99",
                }],
            }],
        }
        raw = json.dumps(content, ensure_ascii=False)
        return GenerationResult(
            content=content, raw_content=raw, model_id=MODEL_ID, prompt_tokens=0,
            completion_tokens=0, total_tokens=0, latency_ms=0.0, raw_sha256="",
            content_sha256="", finish_reason="stop", attempts=1)


class ReportingProvider:
    """Replays the committed answers while reporting what a live adapter would report.

    The committed rows deliberately carry **no** token counts, no latency and no request
    parameters — `generation_store.py` argues at length that a row holding them could never be
    byte-identical — so a replay cannot exercise the manifest fields that record them. This
    double supplies exactly those, and nothing else: the plan and the draft are the same objects
    the shipped store holds, so the run reaches the same disposition and the assertions are
    about the record rather than about the prose.

    It is a double and not a mock: it satisfies the protocol, it is driven through `run_demo`,
    and every value it reports is one a real `GenerationResult` carries.
    """

    model_id = MODEL_ID

    def __init__(self, provider_id: str, *, config: Any = None,
                 metadata: Mapping[str, Any] | None = None,
                 tokens: list[tuple[int, int]] | None = None) -> None:
        self.provider_id = provider_id
        self.config = config
        self._metadata = dict(metadata or {})
        self._tokens = list(tokens or [])
        self._inner = replaying()
        self.store = GenerationStore()

    def health(self) -> HealthStatus:
        return self._inner.health()

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        replayed = self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        prompt_tokens, completion_tokens = (
            self._tokens.pop(0) if self._tokens else (0, 0))
        return GenerationResult(
            content=replayed.content, raw_content=replayed.raw_content,
            model_id=replayed.model_id, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens, latency_ms=1.0, raw_sha256="",
            content_sha256=replayed.content_sha256, finish_reason=replayed.finish_reason,
            attempts=1, metadata={**replayed.metadata, **self._metadata})


@pytest.fixture(scope="module")
def config() -> DemoConfig:
    return DemoConfig.load(REPO_ROOT)


# ---------------------------------------------------------------------------------------
# The demo claim, executed
# ---------------------------------------------------------------------------------------


def test_the_demo_runs_end_to_end_from_a_recorded_response_and_writes_every_artifact(
    tmp_path, config
):
    """§8b's claim, executed: package in, plan, draft, deterministic verdict, artifacts out.

    The provider is replay-only with no inner server, so the whole path runs with nothing
    listening on `:8080` — which is the property that makes this a test rather than a probe.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(), config=config,
                       out_dir=tmp_path / "run")

    written = {path.name for path in (tmp_path / "run").iterdir()}
    assert ALWAYS_WRITTEN <= written, sorted(ALWAYS_WRITTEN - written)
    assert outcome.story_run_id.startswith("story-v1-")
    assert outcome.disposition in (ACCEPTED, REJECTED)


def test_verification_actually_executes_rather_than_being_recorded_as_having_run(
    tmp_path, config
):
    """The claim rests on §13 *running*, so the check is that it examined something.

    Asserting `passed is False` would pass equally well against a verifier that returned a
    refusal without looking at the draft. Twelve checks with non-zero `examined` totals and a
    fact ledger built from resolved ids are what say the pass happened.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(), config=config,
                       out_dir=tmp_path / "run")

    assert outcome.verified is not None
    assert len(outcome.verified.checks) == 12
    assert sum(check.examined for check in outcome.verified.checks) > 0
    assert [entry.fact_id for entry in outcome.verified.fact_ledger] != []
    assert outcome.verified.check("identity_and_freshness").examined == 9


def test_the_recorded_qwen_draft_is_rejected_and_the_rejection_names_every_blocking_finding(
    tmp_path, config
):
    """A real model mistake, refused — and what every check had to look at to reach it.

    **This drives `REJECTED_RECORDING`, which is a recording and not a synthetic.** Across six
    `--live` runs of one candidate the model wrote *identical prose* every time and moved exactly
    one field: `calculation.operation`. Five declared `difference` and were accepted; **one
    declared `compare_levels`** — a comparison — over a sentence stating a size and no direction,
    and §13.14 refused it. That run is this fixture, carried forward at S7a with its two
    citations rewritten to the handles the rebuilt package mints and everything the refusal
    turns on left exactly as the model wrote it.

    Asserting the disposition alone would pass against a verifier that looked at nothing, so the
    denominators are asserted with it: the numbers check counted every numeral, the periods check
    resolved a surface for both bindings *and* for the derivation, and the calculation ledger
    holds the recomputed gap — **the declaration is arithmetically fine and it is the prose that
    is refused.** Eleven of twelve checks pass.

    No check was weakened, and none was loosened to let the accepted store through either:
    `test_story_deterministic_verifier.py`'s ten malicious drafts are the guard on that and are
    untouched. What moves between these two fixtures is one enum field in the model's answer, on
    a runtime `story/providers/generation_store.py` documents as non-reproducible across
    processes.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(REJECTED_RECORDING), config=config,
                       out_dir=tmp_path / "run")
    assert outcome.verified is not None

    assert outcome.disposition == REJECTED
    assert outcome.ok is False
    assert [f.code for f in outcome.verified.all_findings] == [
        "comparative_not_supported_by_text"]
    assert [check.name for check in outcome.verified.checks if check.findings] == [
        "language_safety"]
    assert outcome.verified.check("numbers").examined == 5
    assert outcome.verified.check("periods").examined == 3
    assert outcome.verified.check("title").examined == 1
    assert [entry.rendered for entry in outcome.verified.calculation_ledger] == [
        "15.9 percentage points"]
    assert outcome.verified.calculation_ledger[0].recomputed_value == 15.899999999999999


def test_a_rejected_run_writes_its_artifacts_and_writes_no_post(tmp_path, config):
    """A rejection is a completed run, not a failure to run (§8b, §14).

    Driven by the **rejected synthetic** store — see this module's docstring. `rejected.json`
    carries the §13.17 `RejectedDraft` whole, checks included and not only the blocking
    findings, because the `examined` denominators are what say which checks ran.

    The `expected`/`observed` pair is asserted as well as the code, because the genuine
    recording is *also* refused as `comparative_not_supported_by_text` and the code alone would
    no longer say which of §13.14's two failures this store exercises. This one is the
    inversion: a comparative is present and it names the sides the wrong way round.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(REJECTED_STORE),
                       config=config, out_dir=tmp_path / "run")
    written = {path.name for path in (tmp_path / "run").iterdir()}
    rejected = json.loads((tmp_path / "run" / "rejected.json").read_text(encoding="utf-8"))
    blocking = [f for check in rejected["rejection"]["checks"] for f in check["findings"]
                if f["blocking"]]

    assert "rejected.json" in written
    assert "post.md" not in written
    assert rejected["stage"] == "deterministic_verifier"
    assert rejected["disposition"] == REJECTED
    assert len(rejected["rejection"]["checks"]) == 12
    assert outcome.artifacts["rejected.json"]
    # The one edit, caught by the one check that reads the sentence against its calculation.
    assert [f["code"] for f in blocking] == ["comparative_not_supported_by_text"]
    assert blocking[0]["observed"] == \
        "adjusted_gross_margin before and gaap_gross_margin after"


def test_an_accepted_run_writes_the_post_and_no_rejection(tmp_path, config):
    """The accepted branch: the two files are mutually exclusive.

    Driven by the **accepted synthetic** store rather than the shipped one, which since S7a is
    also accepted. Kept pointed here because the two reach the disposition through different
    §13.14 paths: this store's sentence 2 is a **comparative** the calculation supports, the
    shipped recording's is a `difference` carrying no comparative at all, and a branch reached
    one way is not evidence about the other. The branch is real either way: §13 runs in full
    over the draft and returns no finding, and `pipeline._write_run` chooses `post.md` on the
    same condition it always did.

    The prose is rendered from the structured draft and never from the model's own text (§12),
    which is why the post can be asserted to hold a figure the verifier bound.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(ACCEPTED_STORE), config=config,
                       out_dir=tmp_path / "run")
    written = {path.name for path in (tmp_path / "run").iterdir()}

    assert outcome.disposition == ACCEPTED
    assert outcome.ok is True
    assert outcome.verified is not None and outcome.verified.all_findings == ()
    assert "post.md" in written
    assert "rejected.json" not in written
    assert "3.3 percent" in (tmp_path / "run" / "post.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------
# The 2026-08-19 re-key: the answers did not move, only the key did
# ---------------------------------------------------------------------------------------

#: `sha256` of every artifact this demo writes, **measured at `d72ca64`** — the commit before
#: `provider_id` entered `request_identity` — by running each store through `run_demo` in a
#: `git worktree` of that tree and hashing the files. They are pinned here because the whole
#: claim of the re-key is that they did not move: `IDENTITY_VERSION` went to
#: `story-generation-v2`, every `request_sha256` in every committed store changed, and the
#: answers those digests point at are the same bytes they were.
#:
#: `generations.jsonl` is deliberately **not** in this table. It is the re-keyed file itself, so
#: it is the one artifact that must differ; asserting it unchanged would assert the re-key did
#: not happen. `demo_manifest.json` is out for its own reason — it holds the clock.
BYTES_BEFORE_THE_REKEY: dict[str, dict[str, str]] = {
    "generations.jsonl": {
        "candidate.json": "e82e92c75de12282dfd5757c04967924787bdbc77ba38a800023a80df88326c1",
        "evidence_package.json":
            "8b1c59f5308ac03048c706fa9f1bbec8995e06723365b3428aa4b29cb56a2f5d",
        "editorial_plan.json": "f9240c9905b5938a0c5be04f57776cf1dacb075d1daa550d90c3e94e95d24493",
        "draft.json": "526b5d6736e2b262c709c9901a8833406ae2ffae39b4a5c59d021c7e518f58b9",
        "verification_report.json":
            "fe31ad41d39a3723e64ff44f7a395aef3cbc5eb4b7ab96d68920d390c5f8d2fa",
        "post.md": "73a5ec3d87dfb049f87c019702c2318637c75cee7454d52c306ce98a205b4112",
    },
    ACCEPTED_STORE: {
        "editorial_plan.json": "f9240c9905b5938a0c5be04f57776cf1dacb075d1daa550d90c3e94e95d24493",
        "draft.json": "bf8796490a0bad222c01aaca69506a5b90aec7539f38a1fb998ff46e160ca756",
        "verification_report.json":
            "9fe5a18422709da282a0cda03621c9ee00cf428e3985776a9a0c2798d9cfb8d6",
        "post.md": "24ddcdde7ef2fa1866559b0e4253b73d30c7fe0b3fe53de2b29a97ae38475b98",
    },
    REJECTED_RECORDING: {
        "editorial_plan.json": "f9240c9905b5938a0c5be04f57776cf1dacb075d1daa550d90c3e94e95d24493",
        "draft.json": "dc420e51d94f6ff1cb9cd7240630416a433c8c14df88372c4e2712a1c2ea1e27",
        "verification_report.json":
            "ed36f6cd64f2ec11f0663c3dd0a6b19edb068fe55a844c6643e7412c39b9583a",
        "rejected.json": "4957e29429e3ad565092275df8205bf2a3703b20ed844ae1f4b0189442a951f0",
    },
    REJECTED_STORE: {
        "editorial_plan.json": "f9240c9905b5938a0c5be04f57776cf1dacb075d1daa550d90c3e94e95d24493",
        "draft.json": "d3f1e0de123c19074a5373ea322fe156b1a7cf339eb3b35d62219ac1aac168fd",
        "verification_report.json":
            "5134daedd66f277ad6fe5bec1ddce418ad220a63d36c8eb3fa975a373dc5160a",
        "rejected.json": "cfa3b1ec98c26bac2d342f6b6f0328c0d7c66787760fbc2ed1a8d1bd471e186f",
    },
}


@pytest.mark.parametrize("store_name", sorted(BYTES_BEFORE_THE_REKEY))
def test_the_rekeyed_stores_replay_to_the_bytes_they_replayed_to_before_the_rekey(
    store_name, tmp_path, config
):
    """The fixtures were **re-keyed, not re-recorded** (MULTI_PROVIDER_OPENAI §5.1).

    A re-record would have been the easy path and would have proved nothing: it would replace
    the answers this repository has been reasoning about — the genuine refusal, the two
    synthetics derived from it — with whatever the server said today, and every claim in this
    module's docstring would have to be re-measured. Instead each request was rebuilt through
    the real demo path, looked up under the old digest, and written back under the new one with
    `raw_content` carried across untouched.

    So the test is a byte comparison against the tree that *preceded* the change, not a
    self-comparison: `sha256` of every artifact, against the values `d72ca64` produced. If a
    single character of a stored answer had been touched, `draft.json` and `post.md` move.
    """
    import hashlib

    run = tmp_path / "run"
    run_demo(demo_inputs(), provider=replaying(store_name), config=config, out_dir=run)

    for name, expected in sorted(BYTES_BEFORE_THE_REKEY[store_name].items()):
        assert hashlib.sha256((run / name).read_bytes()).hexdigest() == expected, name
    # The one file that had to move, and the reason the others could not be trusted to be
    # unchanged by accident: the store's own bytes now carry `provider_id` and a new key.
    rekeyed = (run / "generations.jsonl").read_text(encoding="utf-8")
    assert f'"provider_id":"{PROVIDER_ID}"' in rekeyed


def test_only_the_request_digest_moved_in_the_committed_stores():
    """Stated over the fixtures themselves, not over a run: the answers are the same objects.

    `content_sha256` is `sha256(raw_content)` (`openai_compatible.py:344`), so recomputing it
    from the committed text and finding the recorded value is what says the row was carried and
    not regenerated — a re-recorded row would agree with itself and differ from this list.
    """
    import hashlib

    recorded = {
        "generations.jsonl": {
            "story_editorial_plan":
                "f0a98de9cce63d4417f73a33e95a6283e1cb7102ebb888115f09603783075320",
            "story_post_draft": "b14908e636d5f48e1ce407c0c9b96e23d4524617bb4473bf91fa7f42a96e7481",
        },
        ACCEPTED_STORE: {
            "story_post_draft": "ae90fed9cc3498f76c011fe37dc7729eee1a51512fca561ca704332d1ee5f907",
        },
        REJECTED_RECORDING: {
            "story_post_draft": "560895e4419777d8a46978ecf6410fb50631ccbadca4eeae9a7c7afddb58e76f",
        },
        REJECTED_STORE: {
            "story_post_draft": "b5a159ec31a2c714d901d44f5a7ad4be931d5ffb09c4c47cf8f71d8da291c733",
        },
    }
    for filename, expected in recorded.items():
        rows = {row["schema_name"]: row for row in (
            json.loads(line) for line in
            (STORES / filename).read_text(encoding="utf-8").splitlines() if line.strip())}
        for schema_name, content_sha256 in expected.items():
            row = rows[schema_name]
            assert row["content_sha256"] == content_sha256, (filename, schema_name)
            assert hashlib.sha256(
                row["raw_content"].encode("utf-8")).hexdigest() == content_sha256
            assert row["provider_id"] == PROVIDER_ID


# ---------------------------------------------------------------------------------------
# Two providers, two runs
# ---------------------------------------------------------------------------------------


class RelabellingProvider:
    """Replays the committed Qwen rows while claiming to be some other adapter.

    A shim and not a second fixture. It computes the request digest under `provider_id` exactly
    as the real provider would, then answers from the committed row regardless — so the two runs
    it drives differ in the `provider_id` that reaches every id and in **nothing else**, which
    is the only way to attribute a difference in the run id to the provider.
    """

    model_id = MODEL_ID

    def __init__(self, provider_id: str, filename: str = "generations.jsonl") -> None:
        self.provider_id = provider_id
        self._inner = replaying(filename)
        self.store = GenerationStore()

    def health(self) -> HealthStatus:
        return self._inner.health()

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        from story.providers.generation_store import StoredGeneration, request_identity

        result = self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        self.store.put(StoredGeneration.from_result(
            result,
            request_sha256=request_identity(
                system=system, prompt=prompt, schema=schema, schema_name=schema_name,
                provider_id=self.provider_id, model_id=self.model_id,
                temperature=temperature, max_tokens=max_tokens),
            provider_id=self.provider_id, model_id=self.model_id, schema_name=schema_name,
            temperature=temperature, max_tokens=max_tokens, prompt_version=""))
        return result


def test_two_runs_alike_but_for_the_provider_mint_two_ids_and_two_directories(
    tmp_path, config
):
    """MULTI_PROVIDER_OPENAI's headline claim: *Qwen and OpenAI never collide.*

    Everything is held equal — one graph, one candidate, one package, one config, **one model
    string**, and the identical recorded answers — and only the adapter moves. The model string
    is held equal on purpose: a local llama.cpp server answers to any of them, so telling the
    two runs apart by name is a defence the runtime does not provide, and that is exactly why
    §5.1 rejected "rely on distinct model names".

    Three consequences are asserted, because they are three different failures. Two
    `request_sha256` — otherwise one provider replays the other's rows. Two `story_run_id` —
    otherwise the manifests are two records of one name. Two **directories** — otherwise §1.6's
    atomic finalisation replaces one run's artifacts with the other's, which is the data loss
    the whole id scheme exists to prevent. The plan and the draft are asserted equal alongside,
    so the test cannot pass because the two runs simply produced different work.
    """
    landing = DemoConfig(**{**vars(config), "out_root": str(tmp_path / "runs")})

    qwen = run_demo(demo_inputs(), provider=RelabellingProvider(PROVIDER_LOCAL),
                    config=landing)
    openai = run_demo(demo_inputs(), provider=RelabellingProvider(PROVIDER_OPENAI),
                      config=landing)

    assert qwen.story_run_id != openai.story_run_id
    assert qwen.directory != openai.directory
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == sorted(
        [qwen.story_run_id, openai.story_run_id])
    # The answers, and therefore the work, are identical: only the label moved.
    assert qwen.plan == openai.plan and qwen.draft == openai.draft
    assert qwen.disposition == openai.disposition == ACCEPTED
    # …and the stores the two runs would write are keyed apart row for row.
    qwen_keys = {row.request_sha256 for row in
                 GenerationStore(qwen.directory / "generations.jsonl").generations()}
    openai_keys = {row.request_sha256 for row in
                   GenerationStore(openai.directory / "generations.jsonl").generations()}
    assert qwen_keys.isdisjoint(openai_keys)
    assert len(qwen_keys) == len(openai_keys) == 2


def test_the_shipped_store_is_a_miss_for_another_provider_rather_than_a_silent_hit(
    tmp_path, config
):
    """The committed Qwen rows must be unreachable from an OpenAI run, and loudly so.

    Driven through `run_demo` rather than through the store directly, because the failure this
    guards against is a *run* that quietly produced a Qwen post and called it OpenAI's.
    """
    from story.providers.generation_store import MissingGenerationError

    mislabelled = ReplayingStoryGenerationProvider(
        GenerationStore(STORES / "generations.jsonl"), provider_id=PROVIDER_OPENAI,
        model_id=MODEL_ID)
    with pytest.raises(MissingGenerationError):
        run_demo(demo_inputs(), provider=mislabelled, config=config, out_dir=tmp_path / "run")


def test_a_provider_with_no_recorded_store_is_refused_by_name(config):
    """§5.3: never a fall-through to a different provider's rows.

    `config/story.yaml` names a store for the local provider and — deliberately — none for
    `openai`, whose fixture is captured from the live run in §10 or does not exist. Asking for
    it on the replay path has to say *that*, rather than fail later on a digest nothing can
    explain.
    """
    assert config.generation_store_for(PROVIDER_LOCAL) is not None
    assert config.generation_store_for(PROVIDER_OPENAI) is None

    with pytest.raises(StoryDemoError) as raised:
        cli._provider(config, live=False, provider_id=PROVIDER_OPENAI)
    assert PROVIDER_OPENAI in str(raised.value)
    assert "--live" in str(raised.value)


def test_the_scalar_store_key_is_read_only_for_the_local_provider(config, tmp_path):
    """The pre-2026-08-19 key, and the boundary of what it is still allowed to answer.

    It stays readable for one release because `story/demo_ui/api.py` still reads the field; the
    mapping is authoritative. What it must never do is answer for a provider it was never about
    — a scalar written when there was one adapter cannot name a second one's store.
    """
    scalar_only = DemoConfig(**{**vars(config), "generation_stores": {},
                                "generation_store": str(tmp_path / "somewhere.jsonl")})

    assert scalar_only.generation_store_for(PROVIDER_LOCAL) == tmp_path / "somewhere.jsonl"
    assert scalar_only.generation_store_for(PROVIDER_OPENAI) is None
    # And the mapping wins where both are present.
    both = DemoConfig(**{**vars(scalar_only),
                         "generation_stores": {PROVIDER_LOCAL: str(tmp_path / "mapped.jsonl")}})
    assert both.generation_store_for(PROVIDER_LOCAL) == tmp_path / "mapped.jsonl"


# ---------------------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------------------


def test_two_runs_over_one_package_agree_on_every_artifact_but_the_clock(tmp_path, config):
    """§21's byte-identity claim, at the demo's scale: replay is reproducible, generation is not.

    Everything except `demo_manifest.json` is compared as **bytes**, and the manifest as a
    mapping with `created_at` removed — the one clock in the package (§14), and the only field
    two runs of one selection over one graph may legitimately differ on. `story_code_commit` is
    left in the comparison rather than excluded: it is derived from the tree, not from a clock,
    and two runs of one tree that disagreed on it would be a finding.
    """
    first = run_demo(demo_inputs(), provider=replaying(), config=config,
                     out_dir=tmp_path / "first")
    second = run_demo(demo_inputs(), provider=replaying(), config=config,
                      out_dir=tmp_path / "second")

    assert first.story_run_id == second.story_run_id
    assert first.disposition == second.disposition
    assert first.artifacts == second.artifacts
    assert (first.plan and second.plan) and first.plan == second.plan
    assert (first.draft and second.draft) and first.draft == second.draft

    names = {path.name for path in (tmp_path / "first").iterdir()}
    assert names == {path.name for path in (tmp_path / "second").iterdir()}
    for name in sorted(names - {"demo_manifest.json"}):
        assert (tmp_path / "first" / name).read_bytes() == \
            (tmp_path / "second" / name).read_bytes(), name

    manifests = [json.loads((tmp_path / where / "demo_manifest.json").read_text(
        encoding="utf-8")) for where in ("first", "second")]
    for manifest in manifests:
        assert manifest.pop("created_at")
    assert manifests[0] == manifests[1]


def test_two_runs_agree_on_the_candidate_the_package_digest_and_the_verdict(tmp_path, config):
    """The three values §8b's claim is made of, stated separately from the byte comparison.

    A byte comparison that passed for the wrong reason — two runs that both wrote nothing —
    would say nothing about the claim. These are the identities a reader quotes.
    """
    first = run_demo(demo_inputs(), provider=replaying(), config=config,
                     out_dir=tmp_path / "first")
    second = run_demo(demo_inputs(), provider=replaying(), config=config,
                      out_dir=tmp_path / "second")
    assert first.verified is not None and second.verified is not None

    assert first.manifest.selection["candidate_ids"] == [CANDIDATE_ID]
    assert second.manifest.selection["candidate_ids"] == [CANDIDATE_ID]
    assert (first.verified.package_identity.package_content_digest
            == second.verified.package_identity.package_content_digest
            == demo_inputs().package.package_content_digest)
    assert first.verified.passed is second.verified.passed
    assert first.verified.all_findings == second.verified.all_findings


def test_the_run_id_is_derived_from_versions_and_digests_and_never_from_the_clock(
    tmp_path, config
):
    """§14: derived, no clock. Two runs an hour apart mint one id and one directory."""
    first = run_demo(demo_inputs(), provider=replaying(), config=config,
                     out_dir=tmp_path / "first", now="2026-08-04T09:00:00+00:00")
    second = run_demo(demo_inputs(), provider=replaying(), config=config,
                      out_dir=tmp_path / "second", now="2026-08-04T10:00:00+00:00")

    assert first.story_run_id == second.story_run_id
    assert first.manifest.created_at != second.manifest.created_at


def test_a_changed_config_mints_a_different_run_id(tmp_path, config):
    """`config_hash` is a digest input, which is what puts `length_target` inside the id.

    §12's length target changes the writer's prompt without changing
    `WRITER_PROMPT_VERSION`, so without the config hash two materially different runs would
    share an id and §1.6's finalisation would put one on top of the other.
    """
    moved = DemoConfig(**{**vars(config), "raw": {**config.raw, "generation": {
        **(config.raw.get("generation") or {}), "length_target": 3}}})
    first = run_demo(demo_inputs(), provider=replaying(), config=config,
                     out_dir=tmp_path / "first")

    assert moved.config_hash() != config.config_hash()
    assert first.story_run_id != pipeline._mint_run_id(
        demo_inputs(), moved, provider=replaying(), results=[])


# ---------------------------------------------------------------------------------------
# The refusals, and what they refuse before
# ---------------------------------------------------------------------------------------


def test_a_failed_freshness_gate_refuses_before_the_model_is_called(tmp_path, config):
    """§7 is the gate the whole pipeline stands behind, and §17.8 is what it catches.

    Driven with a provider that raises on `generate`, so "before any model call" is asserted
    by the model never having been reached rather than by reading the source.
    """
    inputs = demo_inputs()
    refused = inputs.freshness.model_copy(update={
        "checks": tuple(check.model_copy(update={"passed": False})
                        for check in inputs.freshness.checks)})
    provider = CountingProvider()

    with pytest.raises(FreshnessRefused) as raised:
        run_demo(DemoInputs(**{**vars(inputs), "freshness": refused}), provider=provider,
                 config=config, out_dir=tmp_path / "run")

    assert provider.calls == []
    assert not (tmp_path / "run").exists()
    assert raised.value.report.passed is False


def test_a_candidate_id_that_does_not_reproduce_fails_loudly_and_names_what_is_there(config):
    """§8b re-derives the id from the detectors rather than trusting the string it was handed.

    The refusal names every candidate that *is* there, because "no such candidate" over a
    detector run that produced fifteen of them is a message that sends an operator to guess.
    """
    candidate = demo_inputs().candidate
    with pytest.raises(CandidateNotFound) as raised:
        select_candidate({candidate.candidate_id: candidate},
                         "cand:cross-metric-divergence:made-up:opendoor:2022Q3:000000000000")

    assert raised.value.available == (CANDIDATE_ID,)
    assert CANDIDATE_ID in str(raised.value)
    assert "detector version" in str(raised.value)


def test_the_named_candidate_is_returned_unchanged_when_it_does_reproduce(config):
    candidate = demo_inputs().candidate
    assert select_candidate({CANDIDATE_ID: candidate}, CANDIDATE_ID) is candidate


def test_a_draft_the_writer_refuses_is_recorded_as_its_own_disposition(tmp_path, config):
    """§11 and §12 can each refuse before §13 runs, and the demo must not report that as a
    verifier rejection — the verifier never saw the draft."""
    outcome = run_demo(demo_inputs(), provider=BadWriterProvider(), config=config,
                       out_dir=tmp_path / "run")
    rejected = json.loads((tmp_path / "run" / "rejected.json").read_text(encoding="utf-8"))
    written = {path.name for path in (tmp_path / "run").iterdir()}

    assert outcome.disposition == DRAFT_REFUSED
    assert outcome.verified is None
    assert rejected["stage"] == "post_writer"
    assert "unresolvable_evidence_handle" in rejected["codes"]
    assert "verification_report.json" not in written
    assert "draft.json" not in written
    assert "post.md" not in written
    # The plan survived and is on disk: the run got as far as it got, and says so.
    assert "editorial_plan.json" in written


def test_a_replay_only_store_with_no_matching_row_refuses_rather_than_inventing_one(
    tmp_path, config
):
    """The property that makes the committed store sufficient: a miss is an error, not a call.

    Driven by moving `length_target`, which changes the writer's prompt and therefore the
    request digest — the same reason that value is in `config_hash`.
    """
    from story.providers.generation_store import MissingGenerationError

    moved = DemoConfig(**{**vars(config), "length_target": 9})
    with pytest.raises(MissingGenerationError):
        run_demo(demo_inputs(), provider=replaying(), config=moved,
                 out_dir=tmp_path / "run")


# ---------------------------------------------------------------------------------------
# The manifest, and what §8b requires it to record
# ---------------------------------------------------------------------------------------


def test_the_manifest_records_the_selection_mode_the_identities_and_the_disposition(
    tmp_path, config
):
    """§8b's list, read back off disk rather than off the object that wrote it."""
    run_demo(demo_inputs(), provider=replaying(), config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))
    inputs = demo_inputs()

    assert manifest["selection"]["selection_mode"] == SELECTION_MODE
    assert manifest["selection"]["candidate_ids"] == [CANDIDATE_ID]
    assert manifest["demo"]["candidate_id"] == CANDIDATE_ID
    assert manifest["demo"]["package_id"] == inputs.package.package_id
    assert manifest["demo"]["package_content_digest"] == inputs.package.package_content_digest
    assert manifest["demo"]["graph_input_content_digest"] == inputs.identity.input_content_digest
    assert manifest["demo"]["disposition"] == ACCEPTED
    assert manifest["demo"]["generation_mode"] == "replay"
    assert manifest["graph_run_id"] == GRAPH_RUN_ID
    assert manifest["model_id"] == MODEL_ID
    assert manifest["provider_model_id"].endswith(".gguf")
    assert manifest["temperature"] == 0.0
    # Both prompts moved across EVIDENCE_ROLES_AND_SEMANTIC_FACTS S4 and S6a — the planner
    # gained rule 8 and the writer rules 18 and 19 for the three ontology sections — and the
    # writer moved again at TABLE_CELL_CITATIONS S4, to 1.4.0, when its citation stopped being
    # a retyped quote and became an evidence handle. The manifest is where a reader sees which
    # wording produced these rows.
    assert manifest["prompt_versions"] == {"story_editorial_plan": "1.1.0",
                                           "story_post_draft": "1.4.0"}
    assert sorted(manifest["schema_digests"]) == ["story_editorial_plan", "story_post_draft"]
    assert manifest["ranking_policy_version"] == "1.1.0"
    assert manifest["policy_version"] == POLICY_VERSION
    assert manifest["detector_versions"] == {"detector:cross_metric_divergence": "1.0.0"}


def test_the_manifest_records_the_provider_and_both_call_sites_separately(tmp_path, config):
    """MULTI_PROVIDER_OPENAI §5.2: `provider_id`, and provenance **per call site**.

    Before 2026-08-19 the manifest had no provider field at all, and one pair of scalars
    (`model_id`, `provider_model_id`) described the planner and the writer together — so a run
    could not say which adapter produced its post, and the brief's "planner and writer
    provenance separately" had nowhere to land.

    Each block is asserted whole rather than key by key, because a block missing a field is the
    failure this test exists to catch, and `max_tokens` is asserted **different between the two**
    only as far as the configuration makes it so: both call sites are at 2048 today, and what
    matters is that each block carries its own call site's value and its own schema name.
    """
    run_demo(demo_inputs(), provider=replaying(), config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert manifest["provider_id"] == PROVIDER_ID
    assert manifest["planner_provider_model"] == {
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "provider_model_id": "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "prompt_version": "1.1.0",
        "schema_name": "story_editorial_plan",
        "max_tokens": config.planner_max_tokens,
    }
    assert manifest["writer_provider_model"] == {
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "provider_model_id": "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "prompt_version": "1.4.0",
        "schema_name": "story_post_draft",
        "max_tokens": config.writer_max_tokens,
    }
    # The `provider_model_id` in each block came off that call's own result, not off the
    # configuration: the configured name is the basename, the recorded one is the path.
    assert manifest["planner_provider_model"]["provider_model_id"] != MODEL_ID


def test_a_replayed_run_records_that_it_sent_nothing_rather_than_repeating_the_pinned_value(
    tmp_path, config
):
    """`provider_settings` on the deterministic path, and the nulls are the record.

    `temperature: 0.0` is the value §15.1 pins at the call site and is true of the request the
    recorded row was produced by. `temperature_sent`, `reasoning_effort`, `max_output_tokens`
    and `store_responses` are `null` because **this** run issued no request at all — a replay
    has no adapter and no configuration. Copying the recorded run's parameters into this run's
    manifest would be recording somebody else's request as one's own.
    """
    run_demo(demo_inputs(), provider=replaying(), config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert manifest["provider_settings"] == {
        "temperature": 0.0,
        "temperature_sent": None,
        "reasoning_effort": None,
        "max_output_tokens": None,
        "store_responses": None,
    }
    assert manifest["demo"]["generation_mode"] == "replay"


def test_the_manifest_records_a_refusal_of_temperature_rather_than_a_number_never_sent(
    tmp_path, config
):
    """§5.2's third addition, driven by the case that made it necessary.

    OpenAI's reasoning models **refuse** `temperature` — `gpt-5-nano` answers `temperature: 0.0`
    with `400 Unsupported parameter` (measured against the live API 2026-08-19, §3). A manifest
    printing `temperature: 0.0` and nothing else would be stating a value that never reached the
    wire. Both fields are recorded, and they disagree here on purpose.

    The provider is a double carrying a **real** `StoryProviderConfig` rather than a stand-in
    mapping, so what is proved is that the manifest reads the settings the boundary actually
    publishes.
    """
    from story.providers.public import StoryProviderConfig

    openai_like = StoryProviderConfig(
        kind=PROVIDER_OPENAI, base_url="https://api.openai.com/v1", model="gpt-5-nano",
        context_tokens=128000, max_output_tokens=4096, supports_temperature=False,
        reasoning_effort="minimal", store_responses=False)
    provider = ReportingProvider(PROVIDER_OPENAI, config=openai_like,
                                 metadata={"temperature_sent": False})

    run_demo(demo_inputs(), provider=provider, config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert manifest["provider_id"] == PROVIDER_OPENAI
    assert manifest["provider_settings"] == {
        "temperature": 0.0,
        "temperature_sent": False,
        "reasoning_effort": "minimal",
        "max_output_tokens": 4096,
        "store_responses": False,
    }


def test_the_manifest_totals_the_tokens_the_provider_itself_reported(tmp_path, config):
    """`token_totals` is a sum of what came back, not an estimate and not a configured number.

    Asserted against numbers only the provider could have supplied — 101/202 on the planner and
    303/404 on the writer — so a manifest that recomputed them from the package's estimate, or
    quietly reported zeroes, fails rather than looking plausible.
    """
    provider = ReportingProvider(PROVIDER_ID, tokens=[(101, 202), (303, 404)])
    run_demo(demo_inputs(), provider=provider, config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert manifest["token_totals"]["prompt_tokens"] == 404
    assert manifest["token_totals"]["completion_tokens"] == 606
    assert manifest["token_totals"]["total_tokens"] == 1010
    assert manifest["token_totals"]["generation_calls"] == 2
    # The package's own estimate sits beside them and is not confused with them.
    assert manifest["token_totals"]["package_prompt_token_estimate"] == \
        demo_inputs().package.budget.prompt_token_estimate


def test_the_manifest_lists_the_provider_among_the_run_id_inputs(tmp_path, config):
    """§14 asks for the input *list*, and the list is the thing that drifts.

    A reader holding a Qwen directory and an OpenAI directory has to be able to see why they are
    two without re-running either, and the list is where that is stated.
    """
    run_demo(demo_inputs(), provider=replaying(), config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert "provider_id" in manifest["story_run_id_inputs"]
    # Ordered as the digest takes them, so the list can be read against `keys.story_run_id`.
    inputs = manifest["story_run_id_inputs"]
    assert inputs.index("prompt_version") < inputs.index("provider_id") < \
        inputs.index("model_id")


def test_a_call_site_that_never_ran_records_an_empty_block_rather_than_an_invented_one(
    tmp_path, config
):
    """A refused writer leaves no writer provenance, because there was no writer request.

    Filling the block from configuration would put a `provider_model_id` and a `max_tokens` in
    the manifest for a call that was never built — the same argument `_token_totals` makes for
    reporting zeroes across a replay instead of remembered numbers.
    """
    outcome = run_demo(demo_inputs(), provider=BadWriterProvider(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == DRAFT_REFUSED
    assert manifest["planner_provider_model"]["schema_name"] == "story_editorial_plan"
    assert manifest["writer_provider_model"] == {}


def test_the_manifest_is_written_last_and_names_the_hash_of_every_other_artifact(
    tmp_path, config
):
    """§14: the manifest is the completion marker, so it cannot be one of the things it hashes.

    A directory without one is unambiguously incomplete. Full `.partial` staging and
    `os.replace` finalisation are §8b's deferred part of S11 and are not claimed here.
    """
    import hashlib

    outcome = run_demo(demo_inputs(), provider=replaying(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert "demo_manifest.json" not in manifest["artifacts"]
    for name, recorded in manifest["artifacts"].items():
        actual = hashlib.sha256((tmp_path / "run" / name).read_bytes()).hexdigest()
        assert actual == recorded, name
    assert set(manifest["artifacts"]) == set(outcome.artifacts)


def test_the_manifest_records_that_no_verifier_version_exists_rather_than_inventing_one():
    """`story/stages/verification/` declares no `VERIFIER_VERSION`, unlike every other stage.

    Recorded as `null` beside a digest over §13.17's gate table, which moves when a code, a
    severity or a remedy moves. Naming the gap is the point: a made-up version string in the
    one artifact that exists to pin what checked the post would be worse than none.
    """
    import story.stages.verification as verification

    assert not hasattr(verification, "VERIFIER_VERSION")
    assert len(pipeline.verifier_gate_digest()) == 64


def test_the_two_schema_digests_are_the_schemas_the_run_actually_constrained_the_model_with():
    """§11 pins `causal_language` inside the planner's grammar, so the schema is package-derived.

    Two packages with different causal standing are constrained by two different schemas and
    must not digest alike, which a digest taken over a schema fetched without the package would
    quietly get wrong.
    """
    package = demo_inputs().package
    digests = pipeline.schema_digests_for(package)

    assert sorted(digests) == ["story_editorial_plan", "story_post_draft"]
    assert len(set(digests.values())) == 2
    assert all(len(value) == 64 for value in digests.values())


# ---------------------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------------------


def test_the_exit_codes_are_the_houses_zero_one_two():
    assert (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_USAGE) == (0, 1, 2)


def test_the_parser_offers_the_demo_and_ui_verbs_and_the_rest_of_section_20_is_deferred():
    """§8b traded the CLI family away. A verb that parsed and then refused would be a promise.

    **Widened from `== ["demo"]` when INTERACTIVE_DEMO_UI added `ui`**, and no further: `ui`
    serves the interface that plan describes and is implemented, so it is not one of the
    promises §8b refused to make. Every name §20 deferred is still absent, which is the half of
    this test that was ever load-bearing, and it is now asserted by name rather than by
    exclusion — a bare inequality would have to be edited again by the next verb that lands.
    """
    actions = [action for action in cli.build_parser()._actions
               if isinstance(action, __import__("argparse")._SubParsersAction)]
    assert sorted(actions[0].choices) == ["demo", "ui"]
    deferred = {"discover", "package", "plan", "draft", "verify", "runs", "report", "rebuild",
                "doctor", "ask", "issues", "rejected", "inspect", "candidate", "recheck"}
    assert deferred.isdisjoint(actions[0].choices)


def test_a_missing_candidate_id_is_a_usage_error(capsys):
    """`--candidate-id` has no default: §8b's manual selection, stated as a signature."""
    with pytest.raises(SystemExit) as raised:
        cli.main(["demo"])
    assert raised.value.code == cli.EXIT_USAGE


def test_an_unknown_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as raised:
        cli.main(["discover"])
    assert raised.value.code == cli.EXIT_USAGE


def test_the_command_exits_non_zero_and_writes_its_artifacts_when_the_draft_is_rejected(
    tmp_path, monkeypatch, capsys
):
    """The whole verb, with the graph half stubbed at the seam `resolve_demo_inputs` provides.

    Nothing else is replaced: the real `cmd_demo` builds the real replaying provider over the
    **synthetic** store, runs the real generation and verification stages, and renders the real
    refusal. What is stubbed is a database this test is not marked for.
    """
    class Closable:
        def close(self) -> None:
            self.closed = True

    # `generation_stores` and not the scalar `generation_store`: the mapping is authoritative
    # (MULTI_PROVIDER_OPENAI §5.3) and `generation_store_for` reads the scalar only when the
    # mapping names nothing for the provider. Overriding the wrong one here would leave the
    # shipped store in play and this test would silently be about a different fixture.
    synthetic = DemoConfig(**{
        **vars(DemoConfig.load(REPO_ROOT)),
        "generation_stores": {PROVIDER_ID: str(STORES / REJECTED_STORE)}})
    monkeypatch.setattr(cli, "build_story_context", lambda *a, **k: Closable())
    monkeypatch.setattr(cli, "resolve_demo_inputs", lambda *a, **k: demo_inputs())
    monkeypatch.setattr(cli.DemoConfig, "load", classmethod(lambda cls, root: synthetic))

    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID,
                     "--out", str(tmp_path / "run")])
    out = capsys.readouterr().out

    assert code == cli.EXIT_FAILED
    assert "disposition    rejected" in out
    assert "REJECTED by the deterministic verifier — 1 blocking finding(s)" in out
    # Every failure, not the first (§20).
    assert out.count("remedy ") == 1
    assert SELECTION_MODE in out
    assert (tmp_path / "run" / "rejected.json").is_file()
    assert not (tmp_path / "run" / "post.md").exists()


def test_the_command_exits_zero_and_names_the_post_when_the_draft_is_accepted(
    tmp_path, monkeypatch, capsys
):
    """The accepted branch of the same verb, over the **accepted synthetic** store.

    Pointed at that store for the same reason `test_an_accepted_run_writes_the_post_and_no_
    rejection` is: it reaches acceptance through a comparative where the shipped recording
    reaches it through a `difference`, and the branch — exit 0, the post named on stdout,
    `post.md` on disk — is what this test exists for. The config is otherwise the shipped one,
    and the store is swapped the way the rejected test above swaps it.
    """
    class Closable:
        def close(self) -> None:
            self.closed = True

    accepted = DemoConfig(**{
        **vars(DemoConfig.load(REPO_ROOT)),
        "generation_stores": {PROVIDER_ID: str(STORES / ACCEPTED_STORE)}})
    monkeypatch.setattr(cli, "build_story_context", lambda *a, **k: Closable())
    monkeypatch.setattr(cli, "resolve_demo_inputs", lambda *a, **k: demo_inputs())
    monkeypatch.setattr(cli.DemoConfig, "load", classmethod(lambda cls, root: accepted))

    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID,
                     "--out", str(tmp_path / "run")])
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "disposition    accepted" in out
    assert "ACCEPTED — the post is at" in out
    assert (tmp_path / "run" / "post.md").is_file()


# ---------------------------------------------------------------------------------------
# Live — the graph half, and the model
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_inputs():  # type: ignore[no-untyped-def]
    """Freshness, detection and packaging against the running graph. Skipped with no container."""
    from story.context import build_story_context
    from story.pipeline import resolve_demo_inputs

    context = build_story_context(REPO_ROOT)
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        yield resolve_demo_inputs(context, candidate_id=CANDIDATE_ID,
                                  graph_run_id=GRAPH_RUN_ID)
    finally:
        context.close()


@pytest.mark.neo4j
def test_live_the_selected_candidate_reproduces_from_the_detectors(live_inputs):  # type: ignore[no-untyped-def]
    """§8b's four stated values, recomputed rather than copied from the plan."""
    signals = live_inputs.candidate.signals

    assert live_inputs.candidate.candidate_id == CANDIDATE_ID
    assert (signals["left_value"], signals["right_value"]) == (3.3, -12.6)
    assert signals["gap"] == 15.899999999999999
    assert live_inputs.candidate.anchor_period_keys == ("2022Q3",)
    assert live_inputs.freshness.passed is True


@pytest.mark.neo4j
def test_live_the_package_and_the_freshness_report_match_the_committed_fixture(live_inputs):  # type: ignore[no-untyped-def]
    """The fixture is a real slice, and this is what keeps it one.

    `retrieval_trace` is compared by tool and row count rather than whole: `elapsed_ms` is a
    clock and is the one thing a second read of one graph may legitimately differ on.
    """
    fixture = demo_inputs()

    assert live_inputs.package.package_id == fixture.package.package_id
    assert (live_inputs.package.package_content_digest
            == fixture.package.package_content_digest)
    assert live_inputs.candidate == fixture.candidate
    assert live_inputs.identity == fixture.identity
    assert [(c.name, c.passed) for c in live_inputs.freshness.checks] == \
        [(c.name, c.passed) for c in fixture.freshness.checks]
    assert [(e.tool, e.row_count) for e in live_inputs.package.retrieval_trace] == \
        [(e.tool, e.row_count) for e in fixture.package.retrieval_trace]


@pytest.mark.neo4j
def test_live_a_candidate_id_that_the_graph_does_not_produce_refuses(live_inputs):  # type: ignore[no-untyped-def]
    """The loud failure, against the real detector output rather than a one-entry mapping."""
    from story.context import build_story_context
    from story.pipeline import resolve_demo_inputs

    context = build_story_context(REPO_ROOT)
    try:
        with pytest.raises(CandidateNotFound) as raised:
            resolve_demo_inputs(context, candidate_id="cand:metric-move:not-a-thing:x:2022Q3:0",
                                graph_run_id=GRAPH_RUN_ID)
    finally:
        context.close()
    assert len(raised.value.available) == 15


@pytest.mark.neo4j
def test_live_the_demo_runs_end_to_end_from_the_graph_and_the_recorded_store(
    live_inputs, tmp_path, config,  # type: ignore[no-untyped-def]
):
    """Graph half live, model half replayed — the command's own path, with no server needed.

    The package is built from the graph rather than read from the fixture, so this is also what
    says the committed `evidence_package.json` and the store were recorded against each other:
    a package whose digest or warning kinds had drifted would **miss the store entirely**, and a
    package whose digest had drifted after the prompt was rendered would refuse at §13.13.
    Neither happens, and that is the claim. **The disposition is the recording's content, not
    this test's subject** — the two failure modes above are told apart by the store replaying at
    all, and `REJECTED_RECORDING` drives the refusing branch from a fixture whose verdict cannot
    move under it.
    """
    outcome = run_demo(live_inputs, provider=replaying(), config=config,
                       out_dir=tmp_path / "run")

    assert outcome.disposition == ACCEPTED
    assert outcome.verified is not None and outcome.verified.passed is True
    assert [f.code for f in outcome.verified.all_findings] == []
    assert outcome.verified.check("identity_and_freshness").findings == ()
    assert (tmp_path / "run" / "demo_manifest.json").is_file()


@pytest.mark.live
def test_live_the_qwen_writer_still_files_its_answer_under_the_committed_row(config):
    """*Qwen behaviour is unchanged* — proved against the running server, not by replay.

    The re-key claim has two halves and replay only proves one. Replay proves the recorded
    answers survived intact; this proves the **new** digest still points at the request the
    server really answers. The planner's row is replayed so only the writer's call is live —
    the same isolation `config/story.yaml`'s `length_target` measurement uses — and the live
    answer is then looked up **by the committed key**: if `provider_id` had changed anything
    about what is sent, or the re-key had computed the wrong digest, the row would land
    somewhere else and this fails.

    Equality of the *generation* is asserted here where
    `test_live_the_model_server_answers_the_planner_about_the_same_package` deliberately refuses
    to, and the difference is measured rather than assumed: that test records a planner answer
    that moved with the server's request history, while nineteen consecutive live **writer**
    calls against this package and this plan returned `content_sha256 b14908e636d5…` every time
    *(measured 2026-08-18, this module's docstring)*. A failure here is a finding about the
    runtime, not a flaky assertion — and it is the writer, not the planner, that is claimed.
    """
    from story.providers.openai_compatible import StoryOpenAICompatibleProvider
    from story.providers.public import load_provider_config
    from story.stages.generation import plan_story, write_story

    provider_config = load_provider_config(config.raw)
    server = StoryOpenAICompatibleProvider(provider_config)
    if not server.health().ok:
        pytest.skip(f"model server unavailable at {provider_config.base_url}")

    committed = GenerationStore(STORES / "generations.jsonl")
    rows = {row.schema_name: row for row in committed.generations()}
    # Only the planner row is seeded, so the writer's request misses and reaches the server.
    seeded = GenerationStore()
    seeded.put(rows["story_editorial_plan"])
    provider = ReplayingStoryGenerationProvider(
        seeded, server, provider_id=PROVIDER_ID, model_id=MODEL_ID)

    package = demo_inputs().package
    planned = plan_story(package, provider=provider, max_tokens=config.planner_max_tokens)
    write_story(package, planned.plan, provider=provider,
                length_target=config.length_target, max_tokens=config.writer_max_tokens)

    recorded = seeded.get(rows["story_post_draft"].request_sha256)
    assert recorded is not None, "the live answer did not land on the committed key"
    assert recorded.content_sha256 == rows["story_post_draft"].content_sha256
    assert recorded.raw_content == rows["story_post_draft"].raw_content
    assert recorded.provider_id == PROVIDER_ID


@pytest.mark.live
def test_live_the_model_server_answers_the_planner_about_the_same_package(config):
    """`--live`'s planner call, against the running server. **Not retried** (§15.3, §27 D7).

    **It deliberately does not assert that the live answer equals the recorded one, and that
    is a measurement rather than a caution.** Run alone this assertion held four times out of
    four; run after `test_story_provider_live.py`, it failed reproducibly with a different
    thesis for the same request — *"…for the three months ended September 30, 2022."* against
    the recorded *"…for 2022Q3, with the former being negative while the latter remained
    positive."* Temperature is 0.0 and the request digest is identical in both; what differs is
    the server's request history, and `story/providers/generation_store.py` states exactly this:
    byte-identical *generation* is not achievable on this runtime, byte-identical *replay* is.
    An equality assertion here would be a flaky test asserting something the design says is
    false, which is why the demo's deterministic path replays and does not call.

    What is asserted is what `--live` actually promises: the server answers, the answer
    satisfies §15.3's schema, §11 accepts it, and it is a plan about this package.
    """
    from story.providers.openai_compatible import StoryOpenAICompatibleProvider
    from story.providers.public import load_provider_config
    from story.stages.generation import plan_story

    provider_config = load_provider_config(config.raw)
    provider = StoryOpenAICompatibleProvider(provider_config)
    if not provider.health().ok:
        pytest.skip(f"model server unavailable at {provider_config.base_url}")
    package = demo_inputs().package
    live = plan_story(package, provider=provider, max_tokens=config.planner_max_tokens)

    assert live.plan.package_id == package.package_id
    assert live.plan.candidate_id == CANDIDATE_ID
    assert live.plan.key_points != ()
    assert live.plan.model_id == MODEL_ID
    assert live.generation.model_id.endswith(".gguf")
    assert live.generation.total_tokens > 0
