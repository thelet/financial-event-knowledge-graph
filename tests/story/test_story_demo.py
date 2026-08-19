"""D6 — the §8b demo path, driven end to end.

Offline by default and from committed fixtures. The `neo4j`-marked tests at the bottom
re-derive the candidate and the package from the live graph; the `live`-marked one calls the
model server.

**The four local stores moved on 2026-08-19 and were re-keyed, not re-recorded.** They now live in
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

**All five stores were re-keyed again later the same day, to `story-generation-v3`, and again
not re-recorded.** An adversarial review of the change above found that `reasoning_effort` and
whether `temperature` reached the body were *not* digest inputs, so five structurally different
OpenAI requests shared one `request_sha256` — the same collision one level down, and the effort
is a setting plan §10 records being changed *during* the live comparison. The technique was the
one described above and the evidence is the same test:
`test_the_rekeyed_stores_replay_to_the_bytes_they_replayed_to_before_the_rekey` still compares
against `d72ca64`'s `sha256` values and still passes, which is the strongest available statement
that two consecutive re-keys moved no answer. The digests moved once more (local planner
`de1df732c2d4…` -> `bbf37f44ea9f…`, local writer `4e147f30fdb5…` -> `5ccb62deb13a…`, OpenAI
planner `7843b7b0af58…` -> `38599e59fec3…`, OpenAI writer `092257797e1d…` -> `28b89c6cd6ef…`)
and every row gained a `temperature_sent` and a `reasoning_effort` stating how it was
parameterised. *(Measured 2026-08-19: 10 rows re-keyed, 10 `content_sha256` unchanged.)*

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

**`fixtures/story_demo/openai/generations.jsonl` is a recording too, and it is OpenAI's**
*(captured 2026-08-19)*. Two rows, lifted whole out of one `python -m story demo --live
--provider openai --model gpt-5.4` run against `https://api.openai.com/v1/responses` over the
same candidate, the same graph run and the same evidence package every store above was recorded
against. Nothing is hand-edited; the only thing that has moved since is the `story-generation-v3`
re-key above, which changed each row's `request_sha256` and added the two fields recording how
the request was parameterised — every `raw_content` and every `content_sha256` is still the
API's own, and the file this was carried from hashed to `sha256 1c9898136c4789…`.

| field | value |
| --- | --- |
| `provider_id` | `openai` |
| `model_id` (the identity the rows are keyed under) | `gpt-5.4` |
| `provider_model_id` (what the API called itself) | `gpt-5.4-2026-03-05` |
| rows | `story_editorial_plan`, `story_post_draft` |
| `finish_reason` | `completed` — the Responses API's `status`, not the local server's `stop` |
| `temperature` / `max_tokens` | `0.0` / `2048`, the same digest inputs the Qwen rows carry |
| disposition of the run it came from | **`rejected`**, 7 blocking findings |
| findings | `unbound_numeral` ×2, `metric_surface_ambiguous` ×1, `citation_reused_for_unrelated_claim` ×4 |

**What it proves that the Qwen stores cannot.** That the OpenAI adapter's translation produced a
plan §11 accepted and a draft §12 could construct — so the same schema pair, the same prompts and
the same portable subset reached a second server unmodified and came back usable. That a
recorded row states its own provider and is a **guaranteed miss** for the other one, in both
directions, on real files rather than on a relabelled copy. And that §13 is provider-blind on
real recorded content: the same draft verifies to the same findings whichever adapter's rows
delivered it, which is the brief's claim tested on two genuine recordings instead of a
synthetic pair.

**The rejection is the result and it is reported as one.** No verifier rule and no evidence
contract was changed to make it pass (MULTI_PROVIDER_OPENAI §10).

**It is deliberately not in `config/story.yaml`'s `demo.generation_stores`.** The shipped
configuration names no OpenAI store, so the demo UI honestly answers `provider_requires_live`
for OpenAI and that guarantee stays tested
(`test_a_provider_with_no_recorded_store_is_refused_by_name`). Tests point at this file
explicitly instead.
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
    DISPOSITIONS,
    DRAFT_REFUSED,
    PLAN_REFUSED,
    PROVIDER_FAILED,
    REJECTED,
    SELECTION_MODE,
    STAGE_PLANNER,
    STAGE_WRITER,
    CandidateNotFound,
    DemoConfig,
    DemoInputs,
    FreshnessRefused,
    StoryDemoError,
    run_demo,
    select_candidate,
)
from story.providers.generation_store import (
    GenerationStore,
    ReplayingStoryGenerationProvider,
    RequestIdentity,
    StoredGeneration,
)
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
#: The OpenAI recording, captured 2026-08-19 from the §10 live comparison — see the module
#: docstring for its provenance. Reached by path from here and **not** through
#: `config/story.yaml`: the shipped configuration deliberately names no OpenAI store so that the
#: demo UI's `provider_requires_live` answer stays true and stays tested.
OPENAI_STORES = FIXTURES / PROVIDER_OPENAI

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

#: The two `story-generation-v3` added to the key on 2026-08-19, as the **local** server answers
#: them: it always accepts `PINNED_TEMPERATURE`, and it has no `reasoning` parameter at all — a
#: configuration that claimed otherwise is refused by `StoryProviderConfig.validated()`. Every
#: double in this module is a local provider, so every one of them keys under these.
TEMPERATURE_SENT = True
REASONING_EFFORT: str | None = None

#: `gpt-5.4`'s, from `config/story.yaml` and from plan §10: `supports_temperature: false`, so no
#: temperature reaches the body, and `reasoning_effort: none` — the value §10 records being
#: *changed during the live comparison*, which is the measurement that makes the effort a key
#: input rather than a tidy one. The committed OpenAI store is keyed under these.
OPENAI_TEMPERATURE_SENT = False
OPENAI_REASONING_EFFORT = "none"

#: The OpenAI recording's own identity, and both halves of it. `OPENAI_MODEL_ID` is the
#: *configured* name the rows are keyed under; `OPENAI_PROVIDER_MODEL_ID` is the dated id the
#: API answered with, which is what `provider_model_id` exists to notice moving.
OPENAI_MODEL_ID = "gpt-5.4"
OPENAI_PROVIDER_MODEL_ID = "gpt-5.4-2026-03-05"

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


class RefusedPlannerProvider:
    """Answers the planner with a schema-valid plan §11 refuses, and reports what it cost.

    **This is a real failure, reproduced rather than invented.** `gpt-5-nano` answered the live
    §10 comparison with exactly this mistake on 2026-08-19 — `unusable_evidence` naming
    `obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:485954cf98db`, an observation id
    that is not an item of this package — and `plan_violations` refused it as
    `unknown_unusable_id` before the writer ran. That run is `data/story_demo/
    story-v1-b949ecf8bbd6`, and the manifest it wrote is what the accounting fix is measured
    against.

    The token counts are the double's own and are deliberately non-zero: the whole claim is
    that a refused call is still a call the manifest has to account for, and a provider
    reporting zeroes could not tell a recorded refusal from an un-recorded one.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self._inner = replaying()
        self.calls: list[str] = []
        #: A real one, filled the way a live run fills it, so the run writes a
        #: `generations.jsonl` a test can count against the manifest's own accounting. The
        #: defect this fixture exercises is precisely a disagreement between those two files.
        self.store = GenerationStore()

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="refused-planner")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        self.calls.append(schema_name)
        if schema_name != "story_editorial_plan":
            raise AssertionError(
                f"the writer was called for {schema_name!r} after the plan was refused")
        replayed = self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        content = {
            **replayed.content,
            "unusable_evidence": [{
                "id": "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:485954cf98db",
                "reason": "outside_thesis_scope",
            }],
        }
        raw = json.dumps(content, ensure_ascii=False)
        result = GenerationResult(
            content=content, raw_content=raw, model_id="qwen-wire-name", prompt_tokens=1101,
            completion_tokens=202, total_tokens=1303, latency_ms=12.0, raw_sha256="",
            content_sha256="", finish_reason="stop", attempts=1)
        self.store.put(StoredGeneration.from_result(
            result,
            # Every key input in one object since `story-generation-v3`, for the reason
            # `RequestIdentity` records: a second, hand-assembled argument list for the row was
            # a second chance to key it under something the digest was not taken over.
            identity=RequestIdentity.of(
                system=system, prompt=prompt, schema=schema, schema_name=schema_name,
                provider_id=self.provider_id, model_id=self.model_id,
                temperature=temperature, temperature_sent=TEMPERATURE_SENT,
                reasoning_effort=REASONING_EFFORT, max_tokens=max_tokens),
            prompt_version=""))
        return result


class UnreachableProvider:
    """Never answers. The other half of the accounting claim, and the one that must stay empty.

    A transport fault produced no generation at all — nothing was returned, nothing was stored,
    nothing was billed — so a manifest that recorded a call for it would be inventing one. That
    is the failure mode the fix has to avoid while recording the refusals that *did* generate.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    def health(self) -> HealthStatus:
        return HealthStatus(ok=False, status="unavailable")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        from story.providers.public import StoryProviderUnavailable

        raise StoryProviderUnavailable("http://127.0.0.1:8080/v1/chat/completions: unreachable")


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


class FaultingWriterProvider:
    """Answers the planner and reports what it cost, then never answers the writer.

    The other half of "which call was in flight". The disposition is the same
    `provider_failed` `UnreachableProvider` produces, so everything that says *where* has to
    come from the fault rather than from the disposition — which is the claim
    `ProviderFault.stage` exists to carry.

    It is also the only state in which a fault has a real cost: the planner's call was made,
    answered and billed before the writer's never came back. A panel or a manifest that read
    "this run spent nothing" off the disposition would be wrong about exactly this run, and the
    non-zero token counts below are what make that a failing test rather than a hypothetical.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self._inner = ReportingProvider(PROVIDER_ID, tokens=[(1101, 202)])
        self.calls: list[str] = []

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="faulting-writer")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        from story.providers.public import StoryProviderTransportError

        self.calls.append(schema_name)
        if schema_name == "story_post_draft":
            raise StoryProviderTransportError(
                "http://127.0.0.1:8080/v1/chat/completions: HTTP 503 after 3 attempts")
        return self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)


class AdapterSchemaErrorProvider:
    """Raises the schema error an *adapter* raises: real violations, and no `GenerationResult`.

    This is the case the disposition rule had to decide (`pipeline._provider_failure`). The
    response arrived, it parsed as JSON and it missed the schema — a model's answer, judged —
    but the adapter refuses mid-translation, before it has built a result to attach. Keying the
    disposition on the missing result would report `provider_failed` for a server that really
    answered, so the class decides and the missing result decides only the accounting.

    The violation string is `openai_compatible.py`'s own shape, not an invented one.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="adapter-schema-error")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        from story.providers.public import StoryProviderSchemaError

        raise StoryProviderSchemaError(
            f"response does not satisfy schema {schema_name!r}: thesis: required property is "
            "missing", ("thesis: required property is missing",))


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
#: **Unchanged when the stores were re-keyed a second time**, to `story-generation-v3` on
#: 2026-08-19, and that is why the table is worth more than a self-comparison: it is still
#: measured against `d72ca64`, so it now says two consecutive re-keys moved no answer rather
#: than one. Two re-keys is also where a self-comparison would have gone quietly wrong — the
#: second one could have agreed with a first that had already damaged something.
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

    **It covers the `story-generation-v3` re-key of 2026-08-19 as well**, at no cost and with
    no new numbers: the same four stores were carried across a second version bump by the same
    technique, and the comparison is still against `d72ca64`. That is the point of pinning a
    tree rather than a previous run — a second re-key that agreed with a damaged first one
    would pass a self-comparison and fails this.
    """
    import hashlib

    run = tmp_path / "run"
    run_demo(demo_inputs(), provider=replaying(store_name), config=config, out_dir=run)

    for name, expected in sorted(BYTES_BEFORE_THE_REKEY[store_name].items()):
        assert hashlib.sha256((run / name).read_bytes()).hexdigest() == expected, name
    # The one file that had to move, and the reason the others could not be trusted to be
    # unchanged by accident: the store's own bytes now carry `provider_id`, the two settings
    # `story-generation-v3` added, and a key that moved under each of the two versions.
    rekeyed = (run / "generations.jsonl").read_text(encoding="utf-8")
    assert f'"provider_id":"{PROVIDER_ID}"' in rekeyed
    assert '"temperature_sent":true' in rekeyed and '"reasoning_effort":null' in rekeyed


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
    #: The OpenAI recording, added to this table by the `story-generation-v3` re-key. Its
    #: numbers were measured on the live `gpt-5.4` run of §10 (2026-08-19) and are what makes
    #: the claim "re-keyed, not re-recorded" checkable for the one store whose answers no local
    #: server could reproduce — an accidental re-record here would be undetectable otherwise.
    openai_recorded = {
        "story_editorial_plan":
            "efe245b6626fce785ae565ef9f6f3b3e1e21e778973f1a6ccf6f676e6785ea2e",
        "story_post_draft": "42179bc76ef54304c5086f5b49d37e3d46d4d524b9f683bdb0bc5db6cc90d2c0",
    }
    for directory, provider_id, table in (
            (STORES, PROVIDER_ID, recorded),
            (OPENAI_STORES, PROVIDER_OPENAI, {"generations.jsonl": openai_recorded})):
        for filename, expected in table.items():
            rows = {row["schema_name"]: row for row in (
                json.loads(line) for line in
                (directory / filename).read_text(encoding="utf-8").splitlines() if line.strip())}
            for schema_name, content_sha256 in expected.items():
                row = rows[schema_name]
                assert row["content_sha256"] == content_sha256, (filename, schema_name)
                assert hashlib.sha256(
                    row["raw_content"].encode("utf-8")).hexdigest() == content_sha256
                assert row["provider_id"] == provider_id


#: Every committed store, with the identity `story-generation-v3` says each row must state.
#: Read as a table because that is what the repair added: before 2026-08-19 the first two
#: columns were the whole of a row's stated key, and the last two were settings that changed the
#: request body and reached no digest at all.
COMMITTED_STORES: tuple[tuple[Path, str, str, bool, str | None], ...] = (
    (STORES / "generations.jsonl", PROVIDER_LOCAL, MODEL_ID, True, None),
    (STORES / ACCEPTED_STORE, PROVIDER_LOCAL, MODEL_ID, True, None),
    (STORES / REJECTED_RECORDING, PROVIDER_LOCAL, MODEL_ID, True, None),
    (STORES / REJECTED_STORE, PROVIDER_LOCAL, MODEL_ID, True, None),
    (OPENAI_STORES / "generations.jsonl", PROVIDER_OPENAI, OPENAI_MODEL_ID, False, "none"),
)


@pytest.mark.parametrize("path, provider_id, model_id, temperature_sent, reasoning_effort",
                         COMMITTED_STORES, ids=lambda v: getattr(v, "name", None))
def test_every_committed_row_states_how_its_request_was_parameterised(
    path, provider_id, model_id, temperature_sent, reasoning_effort
):
    """`story-generation-v3`, stated over the files: a row that cannot say is a row that lies.

    The values are not this test's invention. The local server accepts `PINNED_TEMPERATURE` and
    has no reasoning parameter — `StoryProviderConfig.validated()` refuses a configuration that
    claims otherwise — while `config/story.yaml` declares `gpt-5.4` as
    `supports_temperature: false, reasoning_effort: none`, measured against the API (plan §4.4).
    So the OpenAI rows must differ from the Qwen rows in **both** of the fields that were
    missing from the key, which is exactly why one digest could not stand for both.
    """
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(rows) == 2
    for row in rows:
        assert row["provider_id"] == provider_id
        assert row["model_id"] == model_id
        assert row["temperature_sent"] is temperature_sent
        assert row["reasoning_effort"] == reasoning_effort
        # Stored beside `temperature`, never instead of it: the pinned value is what the call
        # site asked for and `temperature_sent` is whether it reached the wire.
        assert row["temperature"] == 0.0


def test_the_five_settings_that_shared_one_key_before_v3_now_have_five(config):
    """The collision the review reproduced, closed on the committed OpenAI request.

    Not a synthetic request: the system, prompt, schema and budget below are the ones the real
    planner call site builds for the committed evidence package, and the identity under the
    committed settings is the key the committed row is actually filed under. The other four are
    configurations `config/story.yaml` and plan §10 make reachable for this same model —
    including `medium`, which is what `gpt-5.4` was run at *before* the effort was changed to
    `none` during the live comparison. Under `story-generation-v2` all five were one digest.
    """
    from story.stages.generation.planner import causal_language_for
    from story.stages.generation.prompts import (
        PLANNER_MAX_TOKENS, PLANNER_SCHEMA_NAME, PLANNER_SYSTEM, planner_prompt, planner_schema)

    package = demo_inputs().package
    call = dict(system=PLANNER_SYSTEM, prompt=planner_prompt(package),
                schema=planner_schema(causal_language=causal_language_for(package)),
                schema_name=PLANNER_SCHEMA_NAME, provider_id=PROVIDER_OPENAI,
                model_id=OPENAI_MODEL_ID, temperature=0.0,
                max_tokens=config.planner_max_tokens)
    variants = [
        (OPENAI_TEMPERATURE_SENT, OPENAI_REASONING_EFFORT),   # what was recorded
        (False, "medium"),
        (False, "high"),
        (False, None),
        (True, OPENAI_REASONING_EFFORT),
    ]
    keys = [RequestIdentity.of(**call, temperature_sent=sent, reasoning_effort=effort).sha256
            for sent, effort in variants]

    assert len(set(keys)) == 5, "two of these five requests still share a replay row"
    committed = {row.request_sha256 for row in
                 GenerationStore(OPENAI_STORES / "generations.jsonl").generations()}
    # The recorded settings hit the committed row; the four the run did not use are misses.
    assert keys[0] in committed
    assert set(keys[1:]).isdisjoint(committed)
    assert PLANNER_MAX_TOKENS == config.planner_max_tokens


def test_a_row_that_claims_a_different_provider_is_refused_rather_than_replayed(tmp_path):
    """Repair 2, on a **doctored copy of a real fixture** rather than a hand-built store.

    The doctoring is exactly what the 2026-08-19 review did: rewrite every row's `provider_id`
    and `model_id` in the committed OpenAI recording to claim the local Qwen provider, and leave
    `request_sha256` alone. Before the fix an `openai`/`gpt-5.4` lookup returned a **HIT** and
    the run replayed a row that says it came from somewhere else, while every docstring in
    `generation_store.py` claimed the file states its own key.

    A raise and not a miss, and the difference matters: a miss falls through to a server or to
    `MissingGenerationError`, both of which report "not here" about a row that is here and is
    wrong. The message names the fields that disagree, because a run whose store contradicts
    itself is a run somebody has to go and look at the file for.
    """
    from story.providers.generation_store import MislabelledGenerationError

    doctored = tmp_path / "generations.jsonl"
    doctored.write_text("".join(
        json.dumps({**json.loads(line), "provider_id": PROVIDER_LOCAL, "model_id": MODEL_ID},
                   ensure_ascii=False, separators=(",", ":")) + "\n"
        for line in (OPENAI_STORES / "generations.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()), encoding="utf-8")

    store = GenerationStore(doctored)
    assert len(store) == 2, "the digests are untouched: the rows are still findable"

    provider = ReplayingStoryGenerationProvider(
        store, provider_id=PROVIDER_OPENAI, model_id=OPENAI_MODEL_ID,
        temperature_sent=OPENAI_TEMPERATURE_SENT, reasoning_effort=OPENAI_REASONING_EFFORT)
    with pytest.raises(MislabelledGenerationError) as raised:
        run_demo(demo_inputs(), provider=provider, config=DemoConfig.load(REPO_ROOT),
                 out_dir=tmp_path / "run")

    assert set(raised.value.differences) == {"provider_id", "model_id"}
    assert PROVIDER_LOCAL in str(raised.value) and PROVIDER_OPENAI in str(raised.value)
    # Never a `MissingGenerationError`: a run that treated this as a miss would reach for a
    # server, and the one thing a self-contradicting store must not produce is a quiet carry-on.
    assert not isinstance(raised.value, LookupError)


@pytest.mark.parametrize("field, doctored", [
    # Every value below is one the *other* provider's rows really carry, or a real second call
    # site — a doctoring that could plausibly happen by concatenating two files, which is the
    # case a digest alone cannot catch.
    ("temperature_sent", False),
    ("reasoning_effort", "none"),
    ("schema_name", "story_advisory_verification"),
    ("max_tokens", 4096),
])
def test_a_row_that_misstates_any_digest_input_it_carries_is_refused(field, doctored, tmp_path):
    """The comparison covers every digest input the row restates, not only the two ids.

    `system`, `prompt` and `schema` are digest inputs the row deliberately does **not** carry —
    §21 keeps the file free of anything large or volatile — so they are checked by the digest
    and by nothing else. These four can be checked twice, and are.
    """
    from story.providers.generation_store import MislabelledGenerationError

    path = tmp_path / "generations.jsonl"
    rows = [json.loads(line) for line in
            (STORES / "generations.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    path.write_text("".join(
        json.dumps({**row, field: doctored}, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows), encoding="utf-8")

    with pytest.raises(MislabelledGenerationError) as raised:
        run_demo(demo_inputs(), provider=replaying_from(path),
                 config=DemoConfig.load(REPO_ROOT), out_dir=tmp_path / "run")
    assert raised.value.differences == (field,)


def replaying_from(path: Path) -> ReplayingStoryGenerationProvider:
    """A replay-only provider over an arbitrary store file, keyed as the local server."""
    return ReplayingStoryGenerationProvider(
        GenerationStore(path), provider_id=PROVIDER_ID, model_id=MODEL_ID,
        temperature_sent=TEMPERATURE_SENT, reasoning_effort=REASONING_EFFORT)


# ---------------------------------------------------------------------------------------
# Two providers, two runs
# ---------------------------------------------------------------------------------------


class RelabellingProvider:
    """Replays the committed Qwen rows while claiming to be some other adapter.

    A shim and not a second fixture. It computes the request digest under `provider_id` exactly
    as the real provider would, then answers from the committed row regardless — so the two runs
    it drives differ in the `provider_id` that reaches every id and in **nothing else**, which
    is the only way to attribute a difference in the run id to the provider.

    `path`, `recorded_provider_id` and `model_id` default to the Qwen store so every call
    written before the OpenAI recording existed reads as it did. Pointed at
    `openai/generations.jsonl` they let the *same* shim carry a genuine OpenAI draft under a
    local label, which is how §13's provider-blindness is proved on real recorded content
    instead of on a relabelled copy of one provider's answer.
    """

    def __init__(self, provider_id: str, filename: str = "generations.jsonl", *,
                 path: Path | None = None, recorded_provider_id: str = PROVIDER_ID,
                 model_id: str = MODEL_ID) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self._inner = ReplayingStoryGenerationProvider(
            GenerationStore(path if path is not None else STORES / filename),
            provider_id=recorded_provider_id, model_id=model_id)
        self.store = GenerationStore()

    def health(self) -> HealthStatus:
        return self._inner.health()

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        from story.providers.generation_store import RequestIdentity, StoredGeneration

        result = self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        self.store.put(StoredGeneration.from_result(
            result,
            # Every key input in one object since `story-generation-v3`, for the reason
            # `RequestIdentity` records: a second, hand-assembled argument list for the row was
            # a second chance to key it under something the digest was not taken over.
            identity=RequestIdentity.of(
                system=system, prompt=prompt, schema=schema, schema_name=schema_name,
                provider_id=self.provider_id, model_id=self.model_id,
                temperature=temperature, temperature_sent=TEMPERATURE_SENT,
                reasoning_effort=REASONING_EFFORT, max_tokens=max_tokens),
            prompt_version=""))
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


def openai_replaying() -> ReplayingStoryGenerationProvider:
    """The committed OpenAI recording, replay-only. A miss raises rather than calling the API.

    `inner=None` is what makes "no network" a property of the object rather than a claim in a
    comment: there is nothing for it to fall through to, so a request the file does not hold
    fails with `MissingGenerationError` instead of reaching `api.openai.com`.
    """
    return ReplayingStoryGenerationProvider(
        GenerationStore(OPENAI_STORES / "generations.jsonl"),
        provider_id=PROVIDER_OPENAI, model_id=OPENAI_MODEL_ID)


def test_the_committed_openai_recording_replays_offline_to_the_run_it_was_captured_from(
    tmp_path, config
):
    """The §10 live `gpt-5.4` run, reproduced from its own rows with nothing running.

    Everything asserted below was measured on the live run
    (`data/story_demo/story-v1-50c0f3c4a1c2`, 2026-08-19): the draft reached §13, §13 refused
    it, and the seven blocking findings are the three codes in the table in this module's
    docstring. The rejection is the result and no rule was changed to avoid it
    (MULTI_PROVIDER_OPENAI §10).
    """
    outcome = run_demo(demo_inputs(), provider=openai_replaying(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == REJECTED
    assert outcome.verified is not None and not outcome.verified.passed
    assert sum(1 for f in outcome.verified.all_findings if f.blocking) == 7
    assert {f.code for f in outcome.verified.all_findings} == {
        "unbound_numeral", "metric_surface_ambiguous", "citation_reused_for_unrelated_claim"}
    # Both call sites answered, and each block names the *dated* id the API reported rather
    # than the configured one — the distinction `provider_model_id` exists to keep.
    assert manifest["provider_id"] == PROVIDER_OPENAI
    for block in ("planner_provider_model", "writer_provider_model"):
        assert manifest[block]["provider_id"] == PROVIDER_OPENAI
        assert manifest[block]["model_id"] == OPENAI_MODEL_ID
        assert manifest[block]["provider_model_id"] == OPENAI_PROVIDER_MODEL_ID
    # A replay sent nothing, so it says so rather than repeating a pinned value it never sent.
    assert manifest["provider_settings"]["temperature_sent"] is None


def test_the_committed_openai_recording_states_its_own_provider_and_model(config):
    """A store that does not say which adapter it came from cannot be replayed from the file.

    Read off the file rather than off a run: `identity_provider_id` and `identity_model_id`
    return `None` when the rows disagree, so a store that mixed two providers' answers would
    fail here rather than silently answer half its lookups.
    """
    store = GenerationStore(OPENAI_STORES / "generations.jsonl")

    assert len(store) == 2
    assert store.identity_provider_id() == PROVIDER_OPENAI
    assert store.identity_model_id() == OPENAI_MODEL_ID
    assert {row.schema_name for row in store.generations()} == {
        "story_editorial_plan", "story_post_draft"}
    assert {row.provider_model_id for row in store.generations()} == {
        OPENAI_PROVIDER_MODEL_ID}
    # The Responses API has no `finish_reason`; `status` is what the adapter records, and the
    # local server's `stop` never appears in an OpenAI row.
    assert {row.finish_reason for row in store.generations()} == {"completed"}


def test_each_providers_committed_rows_are_a_miss_under_the_other_in_both_directions(
    tmp_path, config
):
    """Two real recordings, two identities, no overlap — and a loud failure either way round.

    The existing test covers Qwen's rows under an OpenAI label. This one adds the direction that
    only became testable when a genuine OpenAI recording existed, and asserts the digests are
    disjoint as *files* as well, so the guarantee does not rest on running anything.
    """
    from story.providers.generation_store import MissingGenerationError

    qwen_keys = {row.request_sha256 for row in
                 GenerationStore(STORES / "generations.jsonl").generations()}
    openai_keys = {row.request_sha256 for row in
                   GenerationStore(OPENAI_STORES / "generations.jsonl").generations()}
    assert qwen_keys.isdisjoint(openai_keys)

    as_local = ReplayingStoryGenerationProvider(
        GenerationStore(OPENAI_STORES / "generations.jsonl"),
        provider_id=PROVIDER_LOCAL, model_id=OPENAI_MODEL_ID)
    with pytest.raises(MissingGenerationError):
        run_demo(demo_inputs(), provider=as_local, config=config, out_dir=tmp_path / "local")

    as_openai = ReplayingStoryGenerationProvider(
        GenerationStore(STORES / "generations.jsonl"),
        provider_id=PROVIDER_OPENAI, model_id=MODEL_ID)
    with pytest.raises(MissingGenerationError):
        run_demo(demo_inputs(), provider=as_openai, config=config, out_dir=tmp_path / "openai")


@pytest.mark.parametrize(
    "label, path, recorded_provider_id, model_id, disposition",
    [
        ("qwen", STORES / "generations.jsonl", PROVIDER_LOCAL, MODEL_ID, ACCEPTED),
        ("openai", OPENAI_STORES / "generations.jsonl", PROVIDER_OPENAI, OPENAI_MODEL_ID,
         REJECTED),
    ],
)
def test_the_verifier_reaches_the_same_verdict_whichever_provider_delivered_the_draft(
    tmp_path, config, label, path, recorded_provider_id, model_id, disposition
):
    """MULTI_PROVIDER_OPENAI §8's last row, on **two real recordings** rather than a synthetic
    pair.

    Each provider's genuinely recorded answer is carried through `run_demo` twice — once under
    `local_openai_compatible`, once under `openai` — and the draft and the whole verification
    report are compared as **bytes**. §13 takes a draft, a package and a plan and knows nothing
    about a transport, so a difference here would mean something provider-dependent had reached
    it; asserting it over a Qwen draft the verifier *accepts* and an OpenAI draft it *rejects*
    is what keeps the claim from being true only on the easy side.

    The run ids must still differ, because the provider is a `story_run_id` input: identical
    verification and distinct identity are both required, and this is the one test that holds
    them together.
    """
    def run(provider_id: str, out: str):
        return run_demo(
            demo_inputs(),
            provider=RelabellingProvider(provider_id, path=path,
                                         recorded_provider_id=recorded_provider_id,
                                         model_id=model_id),
            config=config, out_dir=tmp_path / out)

    local = run(PROVIDER_LOCAL, f"{label}-local")
    openai = run(PROVIDER_OPENAI, f"{label}-openai")

    assert local.disposition == openai.disposition == disposition
    assert local.story_run_id != openai.story_run_id
    for name in ("draft.json", "verification_report.json"):
        assert (tmp_path / f"{label}-local" / name).read_bytes() == \
            (tmp_path / f"{label}-openai" / name).read_bytes()
    assert local.verified is not None and openai.verified is not None
    assert local.verified.passed == openai.verified.passed == (disposition == ACCEPTED)
    assert [f.code for f in local.verified.all_findings] == \
        [f.code for f in openai.verified.all_findings]


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
    recorded row was produced by. `temperature_sent`, `reasoning_effort`,
    `max_output_tokens_ceiling` and `store_responses` are `null`, and
    `max_output_tokens_sent` is empty, because **this** run issued no request at all — a replay
    has no adapter and no configuration. Copying the recorded run's parameters into this run's
    manifest would be recording somebody else's request as one's own.

    `max_output_tokens` was **one** field until 2026-08-19 and it was the wrong one: it reported
    the configured ceiling for requests whose bodies carried the call site's budget. See
    `_provider_settings` — the pair below is what replaced it, and the empty list is this run
    saying it sent no body rather than a `2048` it never sent.
    """
    run_demo(demo_inputs(), provider=replaying(), config=config, out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert manifest["provider_settings"] == {
        "temperature": 0.0,
        "temperature_sent": None,
        "reasoning_effort": None,
        "max_output_tokens_ceiling": None,
        "max_output_tokens_sent": [],
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
        # **The two numbers that used to be one, and they differ** — which is the whole reason
        # the field was split on 2026-08-19. 4096 is `provider.openai.max_output_tokens`, the
        # ceiling; 2048 is `generation.writer_max_tokens`/`planner_max_tokens`, what the two
        # bodies actually carried. Recording only the first under a docstring promising "what
        # the request was actually parameterised with" was a claim about a value never sent, in
        # the same block that exists because `temperature` was one.
        "max_output_tokens_ceiling": 4096,
        "max_output_tokens_sent": [2048],
        "store_responses": False,
    }
    assert config.planner_max_tokens == config.writer_max_tokens == 2048
    assert openai_like.max_output_tokens == 4096


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
    """A refused *plan* leaves no writer provenance, because there was no writer request.

    Filling the block from configuration would put a `provider_model_id` and a `max_tokens` in
    the manifest for a call that was never built — the same argument `_token_totals` makes for
    reporting zeroes across a replay instead of remembered numbers.

    **This test used to be driven by `BadWriterProvider` and asserted the wrong thing.** That
    provider's writer *does* run and *does* answer; what §12 refuses is the answer. Reading
    "the block is empty" off that run was reading the defect below as the contract. The
    provider here refuses at §11, so the writer genuinely never ran, which is the only state
    the empty block is allowed to mean.
    """
    provider = RefusedPlannerProvider()
    outcome = run_demo(demo_inputs(), provider=provider, config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == PLAN_REFUSED
    assert provider.calls == ["story_editorial_plan"]
    assert manifest["planner_provider_model"]["schema_name"] == "story_editorial_plan"
    assert manifest["writer_provider_model"] == {}


def test_a_refused_plan_is_still_a_call_the_manifest_accounts_for(tmp_path, config):
    """§11 refused an answer the model gave, and the run paid for it. **Measured defect.**

    Before 2026-08-19 `run_demo` appended to `results` only on the success path, so a run whose
    planner produced a schema-valid answer that §11 then refused recorded *no* generation at
    all. The real run is `data/story_demo/story-v1-b949ecf8bbd6` (`gpt-5-nano`, live,
    `plan_refused`): its own `generations.jsonl` holds one row, and the manifest it wrote beside
    it read `generation_calls: 0`, `total_tokens: 0` and `planner_provider_model: {}`. The store
    on disk knew the call happened and the manifest did not.

    The numbers below come only from the provider, so a manifest that recomputed them or
    quietly reported zeroes fails rather than looking plausible.
    """
    outcome = run_demo(demo_inputs(), provider=RefusedPlannerProvider(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == PLAN_REFUSED
    assert "unknown_unusable_id" in outcome.refusal_codes
    assert manifest["token_totals"]["generation_calls"] == 1
    assert manifest["token_totals"]["prompt_tokens"] == 1101
    assert manifest["token_totals"]["completion_tokens"] == 202
    assert manifest["token_totals"]["total_tokens"] == 1303
    # And the provenance of the call that was made, read off that call's own result: the wire
    # name the double reported, never the configured one.
    assert manifest["planner_provider_model"] == {
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "provider_model_id": "qwen-wire-name",
        "prompt_version": "1.1.0",
        "schema_name": "story_editorial_plan",
        "max_tokens": 2048,
    }
    # The row the provider stored and the call the manifest counts are the same event.
    rows = (tmp_path / "run" / "generations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == manifest["token_totals"]["generation_calls"] == 1


def test_a_refused_draft_is_still_a_call_the_manifest_accounts_for(tmp_path, config):
    """The §12 half of the same claim, and the second measured run.

    `data/story_demo/story-v1-98a0c8e10720` (`gpt-4.1-mini`, live, `draft_refused`) wrote two
    rows to its `generations.jsonl` and a manifest reading `generation_calls: 1` — the planner's
    call counted, the writer's refused call dropped, and `writer_provider_model: {}` for a
    writer that had answered.
    """
    outcome = run_demo(demo_inputs(), provider=BadWriterProvider(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))
    written = {path.name for path in (tmp_path / "run").iterdir()}

    assert outcome.disposition == DRAFT_REFUSED
    assert manifest["token_totals"]["generation_calls"] == 2
    assert manifest["planner_provider_model"]["schema_name"] == "story_editorial_plan"
    assert manifest["writer_provider_model"]["schema_name"] == "story_post_draft"
    # No draft was constructible, so there is no `draft.json` — the call is accounted for, the
    # artifact is not invented.
    assert "draft.json" not in written


def test_a_stage_that_never_got_an_answer_records_no_call_at_all(tmp_path, config):
    """The other shape, and the one a fix could easily get wrong.

    A transport fault returned nothing: no result, no stored row, nothing billed. The manifest
    must say zero rather than reach into a store or synthesise a block from configuration —
    which is why the result travels *on the refusal* and a `StoryProviderUnavailable` carries
    none.

    **The disposition asserted here was `plan_refused` until 2026-08-19, and that was the
    defect, not the contract.** This run reaches no planner: it is `provider_failed` now, and
    the accounting claim — zero calls, zero tokens, two empty blocks — is unchanged by the
    rename, which is the point of asserting it in the same test.
    """
    outcome = run_demo(demo_inputs(), provider=UnreachableProvider(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == PROVIDER_FAILED
    assert manifest["token_totals"]["generation_calls"] == 0
    assert manifest["token_totals"]["total_tokens"] == 0
    assert manifest["planner_provider_model"] == {}
    assert manifest["writer_provider_model"] == {}


def test_a_call_the_provider_never_answered_is_not_recorded_as_a_stage_refusing_it(
    tmp_path, config
):
    """**Reproduced live on 2026-08-19, and this is the run that produced the disposition.**

    `python -m story demo --provider openai --live` with a rejected key finished
    `disposition: plan_refused` and wrote a `rejected.json` reading `"stage":
    "editorial_planner"` — about a request that got an HTTP 401 and reached no planner at all.
    Two false claims: that a planner ran, and that something refused an answer. `run_demo`'s own
    docstring argues four dispositions exist so a §11 refusal is not reported as a verifier
    rejection; the same argument makes a 401 its own fifth.

    Everything asserted below is the artifact saying what happened rather than what class was
    caught: the stage was *attempting*, no answer was produced, and the typed error is the
    taxonomy's name for the boundary that raised. `codes` stays empty — a refusal code is
    something a model's answer earns.
    """
    outcome = run_demo(demo_inputs(), provider=UnreachableProvider(), config=config,
                       out_dir=tmp_path / "run")
    rejected = json.loads((tmp_path / "run" / "rejected.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == PROVIDER_FAILED
    assert outcome.fault is not None
    assert outcome.fault.stage == STAGE_PLANNER
    assert outcome.fault.error_class == "StoryProviderUnavailable"
    assert outcome.refusal_codes == ()
    assert rejected["disposition"] == PROVIDER_FAILED
    assert rejected["stage"] == STAGE_PLANNER
    assert rejected["codes"] == []
    assert rejected["provider_fault"] == {
        "stage": STAGE_PLANNER,
        "error_class": "StoryProviderUnavailable",
        "answer_produced": False,
    }
    # No plan, no draft, no verification — and the artifact never claims one ran.
    assert "rejection" not in rejected
    assert manifest["demo"]["disposition"] == PROVIDER_FAILED
    assert manifest["demo"]["provider_fault"]["stage"] == STAGE_PLANNER
    assert not (tmp_path / "run" / "editorial_plan.json").exists()
    assert not (tmp_path / "run" / "post.md").exists()


def test_a_fault_at_the_writer_is_distinguishable_from_one_at_the_planner(tmp_path, config):
    """One disposition, two stages, and the run that proves the cost is not read off either.

    The brief's requirement stated as a test: which call was in flight is *data*, not a second
    disposition. Both runs below are `provider_failed`; only `stage` separates them.

    The writer run is also the honest-accounting case that a naive fix gets wrong. Its planner
    call was made, answered and billed — 1 303 tokens — before the writer's call failed, so the
    manifest must report one call and a filled planner block beside an empty writer one. A
    `provider_failed` run is not a run that spent nothing; it is a run that got no answer to the
    call named in `stage`.
    """
    planner_fault = run_demo(demo_inputs(), provider=UnreachableProvider(), config=config,
                             out_dir=tmp_path / "planner")
    writer = FaultingWriterProvider()
    writer_fault = run_demo(demo_inputs(), provider=writer, config=config,
                            out_dir=tmp_path / "writer")
    manifest = json.loads(
        (tmp_path / "writer" / "demo_manifest.json").read_text(encoding="utf-8"))
    rejected = json.loads((tmp_path / "writer" / "rejected.json").read_text(encoding="utf-8"))

    assert planner_fault.disposition == writer_fault.disposition == PROVIDER_FAILED
    assert planner_fault.fault.stage == STAGE_PLANNER
    assert writer_fault.fault.stage == STAGE_WRITER
    assert writer_fault.fault.error_class == "StoryProviderTransportError"
    assert writer.calls == ["story_editorial_plan", "story_post_draft"]
    assert rejected["stage"] == STAGE_WRITER
    # The planner's call happened and is accounted for; the writer's did not and is not.
    assert manifest["token_totals"]["generation_calls"] == 1
    assert manifest["token_totals"]["total_tokens"] == 1303
    assert manifest["planner_provider_model"]["schema_name"] == "story_editorial_plan"
    assert manifest["writer_provider_model"] == {}
    # The plan the run did get is on disk. A fault does not retract what already arrived.
    assert (tmp_path / "writer" / "editorial_plan.json").is_file()
    assert not (tmp_path / "writer" / "draft.json").exists()


def test_a_stage_refusing_its_own_answer_keeps_the_disposition_it_had(tmp_path, config):
    """The half that must not move. §11 stays `plan_refused`; §12 stays `draft_refused`.

    Both of these refuse an answer the model produced, which is a judgement about a generation
    and is exactly what the two dispositions have always meant. `fault` is `None` on both — the
    field is the marker for "no answer came back", and a stage that refused one had an answer.
    """
    refused_plan = run_demo(demo_inputs(), provider=RefusedPlannerProvider(), config=config,
                            out_dir=tmp_path / "plan")
    refused_draft = run_demo(demo_inputs(), provider=BadWriterProvider(), config=config,
                             out_dir=tmp_path / "draft")

    assert refused_plan.disposition == PLAN_REFUSED
    assert refused_plan.fault is None
    assert "unknown_unusable_id" in refused_plan.refusal_codes
    assert refused_draft.disposition == DRAFT_REFUSED
    assert refused_draft.fault is None
    assert refused_draft.refusal_codes != ()
    for run in ("plan", "draft"):
        rejected = json.loads((tmp_path / run / "rejected.json").read_text(encoding="utf-8"))
        assert "provider_fault" not in rejected
        assert rejected["codes"] != []


def test_a_schema_violation_is_the_models_answer_wherever_it_was_raised(tmp_path, config):
    """The line `_provider_failure` draws, asserted from the side that made it a decision.

    An adapter raises `StoryProviderSchemaError` while translating a response it has parsed and
    checked, before any `GenerationResult` exists — so this refusal carries violations and no
    result. Sorting it by the missing result would call it `provider_failed`, which would say
    "the provider did not produce an answer" about a server that answered with JSON somebody
    could name the violations of. It stays the stage's own disposition, and only the accounting
    records the absence: zero calls, because no result was ever built to count.
    """
    outcome = run_demo(demo_inputs(), provider=AdapterSchemaErrorProvider(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))
    rejected = json.loads((tmp_path / "run" / "rejected.json").read_text(encoding="utf-8"))

    assert outcome.disposition == PLAN_REFUSED
    assert outcome.fault is None
    assert outcome.refusal_codes == ("thesis: required property is missing",)
    assert rejected["stage"] == STAGE_PLANNER
    assert "provider_fault" not in rejected
    assert manifest["token_totals"]["generation_calls"] == 0


def test_the_dispositions_are_a_closed_set_the_run_reports_from(tmp_path, config):
    """Five values, declared once, and every one of them reachable from this file's doubles.

    A list stated in more than one place is a list that ends up different lengths in different
    places, which is how a browser came to render a disposition set that had four members while
    `pipeline` had five. The demo UI reads `DISPOSITIONS` rather than restating it, and this is
    the end that says the constant is complete.
    """
    reached = {
        run_demo(demo_inputs(), provider=provider, config=config,
                 out_dir=tmp_path / name).disposition
        for name, provider in (("accepted", replaying(ACCEPTED_STORE)),
                               ("rejected", replaying(REJECTED_STORE)),
                               ("plan", RefusedPlannerProvider()),
                               ("draft", BadWriterProvider()),
                               ("fault", UnreachableProvider()))
    }

    assert reached == set(DISPOSITIONS)
    assert len(DISPOSITIONS) == len(set(DISPOSITIONS)) == 5


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


def test_the_command_names_the_call_that_got_no_answer_and_not_a_stage_that_refused(
    tmp_path, monkeypatch, capsys
):
    """The CLI half of the `provider_failed` disposition, over the whole verb.

    The exit code does not move — a run with no post is a failure whichever way it failed — so
    what this asserts is the *summary*, which is the part an operator reads. "No draft reached
    the verifier" is true of a refused plan and of a 401 alike, and printing only that sent
    somebody looking for the model's mistake in a run where no model was reached. The stage
    named here is the call that was attempted and the class is the boundary that raised, which
    together say whether a key, a URL or a budget wants fixing.
    """
    class Closable:
        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(cli, "build_story_context", lambda *a, **k: Closable())
    monkeypatch.setattr(cli, "resolve_demo_inputs", lambda *a, **k: demo_inputs())
    monkeypatch.setattr(cli, "_provider", lambda *a, **k: UnreachableProvider())

    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID,
                     "--out", str(tmp_path / "run")])
    out = capsys.readouterr().out

    assert code == cli.EXIT_FAILED
    assert f"disposition    {PROVIDER_FAILED}" in out
    assert f"the {STAGE_PLANNER} call got no answer" in out
    assert "StoryProviderUnavailable" in out
    assert "no draft reached the verifier" not in out
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
    # No `temperature_sent` and no `reasoning_effort`: they are read off `server.config`, so a
    # live answer that landed on the committed key proves the re-key computed the same digest
    # the running configuration does.
    provider = ReplayingStoryGenerationProvider(
        seeded, server, provider_id=PROVIDER_ID, model_id=MODEL_ID)

    package = demo_inputs().package
    planned = plan_story(package, provider=provider, max_tokens=config.planner_max_tokens)
    write_story(package, planned.plan, provider=provider,
                length_target=config.length_target, max_tokens=config.writer_max_tokens)

    # `row` and not `get`: this reads the file by digest, and `get` since 2026-08-19 wants the
    # identity that produced the digest so it can check the row against it. Here the digest is
    # the *committed* one and the row is the *live* one, which is the entire question.
    recorded = seeded.row(rows["story_post_draft"].request_sha256)
    assert recorded is not None, "the live answer did not land on the committed key"
    assert recorded.content_sha256 == rows["story_post_draft"].content_sha256
    assert recorded.raw_content == rows["story_post_draft"].raw_content
    assert recorded.provider_id == PROVIDER_ID
    # …and the two `story-generation-v3` settings the live adapter's own `StoryProviderConfig`
    # resolved, which is what makes this a check on the *new* key rather than on the old one:
    # nothing below is passed in, it is read off the running server's configuration.
    assert (recorded.temperature_sent, recorded.reasoning_effort) == (
        rows["story_post_draft"].temperature_sent,
        rows["story_post_draft"].reasoning_effort) == (True, None)


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
