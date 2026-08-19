"""D6 — the §8b demo path, driven end to end.

Offline by default and from committed fixtures. The `neo4j`-marked tests at the bottom
re-derive the candidate and the package from the live graph; the `live`-marked ones call the
model server.

**Every store here was re-recorded live on 2026-08-19 and none was re-keyed** — twice on that
day, and this docstring describes the second pass. The first was
DETERMINISTIC_FACT_TOOLS §9's, for S13's schema and prompt changes; the second is the repair
packet that followed §11.2's measured regression, which moved `WRITER_PROMPT_VERSION` 2.0.0 ->
**2.1.0**. `PLANNER_PROMPT_VERSION` did **not** move and neither did the planner's rendering, so
the planner row in `local_openai_compatible/generations.jsonl` is byte-for-byte the row the
first pass recorded — same `request_sha256`, same `content_sha256`
(`52b3b181e5e4041d…`) — and only the writer's question and answer changed. That is the
cleanest available statement of what the repair touched.

**The evidence package fixture did not move this time.** S13 rebuilt it because
`max_derivations` entered `BudgetParameters` and therefore `package_content_digest`; the 2.1.0
repair changes a *prompt*, so the live path still builds
`pkg:…:6943b6e1436a` and `evidence_package.json` re-derives byte-identical
(`a347b38c340ce3a8…`). `candidate.json`, `graph_identity.json` and `freshness_report.json` are
untouched for the same reason.

## H3 moved `WRITER_PROMPT_VERSION` to 2.2.0 and **no store was re-recorded** *(2026-08-19)*

The packet expected to re-record both — *"that moves the request digest"* — and it does not.
`request_identity` digests the prompt **text**, the system message and the schema; the version
constant is not one of its ten inputs, and neither store's row even carries it
(`prompt_version` is `""` in every recorded row). 2.2.0's whole change is `_observed_figure`,
which prints a `figure:` line for a **USD** reading filed at a scale word and nothing else — and
this candidate's two facts are `percent` at `units`. So the writer prompt for this package is
byte-identical across the change.

**Measured rather than argued.** A live Qwen run of this candidate on 2026-08-19, after the
change, returned a `generations.jsonl` hashing to `8645d1a95a533b29…` — byte-for-byte the
committed store, for the fourth packet running — and the committed `openai/` store still
replays with the same `request_sha256`s
(`test_the_committed_openai_recording_replays_offline_to_the_run_it_was_captured_from`). Both
stores are the same captures the 2.1.0 pass recorded, and the tables below say so.

**What did move is a version stamp and not an answer.** `Draft.prompt_version` is a field of
`draft.json`, so `draft.json` moved; `verification_report.json` embeds `draft_content_sha256`,
so it moved; `rejected.json` embeds the report; and the manifest's `prompt_versions` and
`writer_provider_model.prompt_version` read `2.2.0`. Every pinned digest in
`ARTIFACTS_OF_THE_LIVE_RECORDING` that is not downstream of that field — `candidate.json`,
`evidence_package.json`, `editorial_plan.json`, `derived_facts.json`, `post.md` and
`generations.jsonl` — is unchanged, which is the sharpest available statement that the model's
answer did not move.

## The Qwen recording, and the finding it now carries

`local_openai_compatible/generations.jsonl` is genuine Qwen output, captured 2026-08-19 from
`http://127.0.0.1:8080` serving `/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf` through
`python -m story demo --candidate-id … --live`. Two rows, nothing hand-edited, both
`finish_reason: stop`, both lifted whole out of one run's own `generations.jsonl`.

| field | value |
| --- | --- |
| `provider_id` / `model_id` | `local_openai_compatible` / `Qwen3.5-9B-Q4_K_M.gguf` |
| `provider_model_id` | `/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf` |
| prompt versions | planner `1.2.0`, writer `2.1.0` |
| tokens | 8,415 prompt, 1,545 completion across the two calls |
| disposition | **`accepted`**, zero findings |

**Four consecutive `--live` runs produced a byte-identical `generations.jsonl`**
(`sha256 8645d1a95a533b29…` every time), so this is a stable answer and not one run's accident.
Every one of them minted `story-v1-79fd9ddc03fe` and wrote the same `draft.json` and `post.md`
the replay above produces.

**It is accepted again, and the two things that were wrong were both in the prompt.** The
2026-08-19 S13 recording was refused on three `metric_surface_unresolved` findings — the model
wrote `"percent"`, `"percent"` and `"percentage_points"` where a metric surface belongs — and
§11.2 recorded, correctly, that no verifier rule should move to fix it. Neither did. What moved
was measured by ablation against this same server *(four live writer calls, 2026-08-19)*:

| writer prompt | `metric_surface` values Qwen produced | disposition |
| --- | --- | --- |
| 2.0.0 | `percent`, `percent`, `percentage_points` | rejected, `metric_surface_unresolved` ×3 |
| 2.0.0 + the metric id offered as a surface | `gaap gross margin`, `Adjusted Gross Margin`, `gaap gross margin` | refused at §12: `binding_rendering_not_in_text` on `"15.9 percentage_points"` |
| 2.1.0, both repairs | the same three surfaces | **accepted**, zero findings |

The first repair is §11.4's second defect: `metric_surfaces_for` offered only a metric's `label`
and `aliases`, `gaap_gross_margin`'s label is `"Gross Margin"` and is dropped as a sub-phrase of
`"Adjusted Gross Margin"`, so the FACTS row **and** the DERIVED FACTS row computed from it both
printed *"do not write about this fact"* for the metric the plan's own key point required — while
`WRITER_SYSTEM` rule 4 told the model to write *"GAAP gross margin"*. `MetricAliasIndex` has
always accepted the id form. The second is the DERIVED FACTS row's figure: it printed
`{result} {unit}` in the machine's spelling, and the model copied `percentage_points` into
`rendered` while writing *"percentage points"* in its text.

**What the recording proves about S13 itself, which is why it is worth keeping.** The planner
requested a derivation (`compare_levels`, `adjusted_gross_margin` -> `gaap_gross_margin`,
2022Q3) out of the four `offers()` printed; code executed it — `-15.9 percentage_points`,
`lower than` — and the writer bound the result by its `fact:derived:` id like any other fact. So
the one computed figure in an accepted post is one **code** produced, and no numeral in it is
the model's arithmetic.

## The one synthetic store, and the one edit that makes it

`generations_rejected_synthetic.jsonl` is the recording above with **one sentence's two sides
swapped** and nothing else: *"The adjusted gross margin was 15.9 percentage points lower than the
GAAP gross margin…"*, which is false by 15.9 points. It carries the recording's planner row byte
for byte, so the plan, the offer set and the derived fact are identical across both files and
only the writer's answer moves. **Rejected**, on exactly one blocking finding —
`comparative_not_supported_by_text`, observed *"adjusted_gross_margin before and
gaap_gross_margin after"* — which is the attack a recomputation on its own would have accepted.

`generations_accepted_synthetic.jsonl` **was deleted**, and for the reason
`generations_rejected_recorded.jsonl` was deleted before it: it existed because the shipped
recording was refused and something here had to reach the accepted branch. The shipped recording
*is* the accepted branch now, so the file could only have been a second accepted store
hand-authored beside a real one.

**So there is currently no rejected *recording* of this candidate under Qwen, and that is
reported rather than manufactured.** The model gets this candidate right. The refusals a real
model still earns are in `openai/generations.jsonl` below, and the refusal machinery over a
hand-built draft is `test_story_deterministic_verifier.py`'s, untouched.

## The OpenAI recording

`openai/generations.jsonl` is a recording too, and it is OpenAI's *(re-recorded 2026-08-19,
second pass)*. Two rows lifted whole out of one `python -m story demo --candidate-id … --live
--provider openai --model gpt-5.4` run against `https://api.openai.com/v1/responses`, over the
same candidate, the same graph run and the same evidence package the Qwen store was recorded
against.

| field | value |
| --- | --- |
| `provider_id` | `openai` |
| `model_id` (the identity the rows are keyed under) | `gpt-5.4` |
| `provider_model_id` (what the API called itself) | `gpt-5.4-2026-03-05` |
| rows | `story_editorial_plan`, `story_post_draft` |
| `finish_reason` | `completed` — the Responses API's `status`, not the local server's `stop` |
| `temperature_sent` / `reasoning_effort` / `max_tokens` | `false` / `none` / `2048` |
| tokens | 8,347 prompt, 1,624 completion |
| disposition | **`rejected`**, 7 blocking findings |
| findings | `unbound_numeral` ×2, `citation_reused_for_unrelated_claim` ×4, `connective_sentence_carries_a_claim` |

**`metric_surface_ambiguous` is gone from its findings**, which is the same repair the Qwen
store measures reaching a second provider: `gpt-5.4` wrote *"gaap gross margin"* where it used to
write a surface §13.5 refused. What it did instead of the previous run: it requested **no**
derivation at all (`derivations_requested: 0`), wrote six sentences where it wrote five, and put
a period in a `connective` sentence. Its two `unbound_numeral`s are on `2022` in sentences that
name a period and bind nothing — the ordinary rule, not a derived-fact one.

**The rejection is the result and it is reported as one.** No verifier rule and no evidence
contract was changed to make either recording pass or fail (MULTI_PROVIDER_OPENAI §10,
DETERMINISTIC_FACT_TOOLS §9).

**It is deliberately not in `config/story.yaml`'s `demo.generation_stores`.** The shipped
configuration names no OpenAI store, so the demo UI honestly answers `provider_requires_live`
for OpenAI and that guarantee stays tested
(`test_a_provider_with_no_recorded_store_is_refused_by_name`). Tests point at this file
explicitly instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    DERIVATION_REFUSED,
    DERIVED_FACTS_FILENAME,
    DISPOSITIONS,
    DRAFT_REFUSED,
    PLAN_REFUSED,
    PROVIDER_FAILED,
    REJECTED,
    SELECTION_MODE,
    STAGE_DERIVATION,
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
from story.stages.derivation.offers import offers
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
#: **`derived_facts.json` joined the set at S13** and is genuinely always written on a run that
#: reached the writer: §3 keeps derived facts out of `StoryEvidencePackage.facts`, so if this
#: file is absent there is nowhere else the run recorded what code computed for it — including
#: the case where the plan requested nothing, which the file states as an empty `facts` list
#: rather than by not existing.
ALWAYS_WRITTEN = {
    "candidate.json", "evidence_package.json", "editorial_plan.json", "derived_facts.json",
    "draft.json", "verification_report.json", "generations.jsonl", "demo_manifest.json",
}

#: The stores this module's docstring describes. Named rather than spelled at each call site so
#: that "which branch is this test driving" is one word rather than a filename a reader has to
#: compare character by character against another filename.
#:
#: **`ACCEPTED_RECORDING` is a recording and `REJECTED_STORE` is not**, and the asymmetry is the
#: state of the tree rather than a preference. The shipped Qwen store is accepted again since the
#: writer prompt's 2.1.0 repair, so `generations_accepted_synthetic.jsonl` was deleted — it would
#: have been a hand-authored accepted store standing beside a real one, which is the duplication
#: `generations_rejected_recorded.jsonl` was deleted for at 243a5e0. Nothing here now carries a
#: Qwen refusal a model earned on this candidate, because the model does not earn one;
#: `openai/generations.jsonl` is the recorded refusal, and `REJECTED_STORE` is one hand edit —
#: the two sides of the comparison swapped — which is what drives the rejected branch.
ACCEPTED_RECORDING = "generations.jsonl"
REJECTED_STORE = "generations_rejected_synthetic.jsonl"


def _read(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def demo_inputs() -> DemoInputs:
    """The live 2022Q3 slice as it came off the graph, byte-for-byte.

    Assembled here rather than mocked: every value below was produced by
    `resolve_demo_inputs` against `graph-v1-0483dc6b4b10` and written out unchanged, so a test
    driving `run_demo` over it is driving the same object the live path hands over.

    **`evidence_package.json` was rebuilt from the graph again on 2026-08-19**
    (DETERMINISTIC_FACT_TOOLS §4.3). S13 gave `BudgetParameters` a `max_derivations` field to
    cap the offer set, that dataclass is inside `digest_parts()`, and so the live path builds
    `pkg:…:6943b6e1436a` where the S7a capture held `pkg:…:4e4363b11373`. Both prompts embed the
    package id, so leaving the old file here would have made every committed row a miss. The two
    packages differ in `budget.parameters.max_derivations`, in
    `budget.artifact_token_estimate` (6636 -> 6642), in `package_id` and in
    `package_content_digest`, and in nothing else — no fact, no passage and no handle moved.

    The S7a rebuild before it had its own reason and is still the reason the *contents* are
    what they are: the 2026-08-04 capture predated S1, so its two table facts carried
    `cell: null` and therefore the narrative handle form. `candidate.json` and
    `graph_identity.json` have now re-derived byte-identical twice, which is the evidence that
    only the packaging shape has ever moved.

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


def recorded_content(schema_name: str) -> dict[str, Any]:
    """One recorded answer's `content`, read by schema name rather than by request digest.

    The digest is exactly what a schema or prompt change moves, and these two answers are wanted
    *because* they are the live Qwen run's own words rather than because they still key. Reading
    by name is honest about that: nothing here claims a replay.
    """
    for line in (STORES / "generations.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["schema_name"] == schema_name:
            return json.loads(row["raw_content"])
    raise AssertionError(f"no recorded {schema_name!r} row")


def compare_levels_request() -> dict[str, str]:
    """The one derivation §2's package supports and the candidate publishes a signal for.

    `gaap_gross_margin` → `adjusted_gross_margin` in 2022Q3, which is the orientation the
    candidate's own `gap` describes: `left_metric_id` is the adjusted margin and `right_metric_id`
    the GAAP one, so `from` is the right side and `to` is the left. Reversed, the derivation is
    still offered and still correct and simply has no signal to be asserted against.
    """
    inputs = demo_inputs()
    for request in offers(inputs.package, inputs.candidate):
        signals = inputs.candidate.signals
        if (request.operation.value == "compare_levels"
                and request.from_fact_id.startswith(f"obs:{signals['right_metric_id']}".replace(
                    "_", "-"))):
            return {"operation": request.operation.value,
                    "from_fact_id": request.from_fact_id,
                    "to_fact_id": request.to_fact_id}
    raise AssertionError("this package offers no compare_levels in the signalled orientation")


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


class DerivingProvider:
    """Answers both calls, so a run reaches the derivation stage between them.

    **Its two answers are the recorded ones, moved onto the contract S13 introduced.** The plan
    is `generations.jsonl`'s own, plus one `requested_derivations` entry; the draft is
    `generations.jsonl`'s own with its third sentence rewritten — the `calculation` gone and an
    ordinary `fact_bindings` entry in its place. Rewriting the recording rather than inventing a
    pair keeps every id, every surface and every citation the live Qwen run produced, so what
    this exercises is the one thing that changed.

    **The derived fact id is read out of the writer's own prompt**, not computed here. That is
    deliberate: a double that recomputed the id would bind a fact whether or not the DERIVED
    FACTS section carried one, and the property worth asserting is that the writer was shown the
    thing it binds.
    """

    model_id = MODEL_ID
    provider_id = PROVIDER_ID

    #: `[fact:derived:…]` as `prompts._derived_fact_lines` prints it.
    DERIVED_ID = re.compile(r"\[(fact:derived:[^\]\s]+)\]")

    def __init__(self, requested: Sequence[Mapping[str, str]]) -> None:
        self.requested = [dict(row) for row in requested]
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="deriving")

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        self.calls.append(schema_name)
        self.prompts[schema_name] = prompt
        if schema_name == "story_editorial_plan":
            content = {**recorded_content("story_editorial_plan"),
                       "requested_derivations": self.requested}
        else:
            content = self._draft(prompt)
        raw = json.dumps(content, ensure_ascii=False)
        return GenerationResult(
            content=content, raw_content=raw, model_id="qwen-wire-name",
            prompt_tokens=1520, completion_tokens=430, total_tokens=1950, latency_ms=11.0,
            raw_sha256="", content_sha256="", finish_reason="stop", attempts=1)

    def _draft(self, prompt: str) -> dict[str, Any]:
        content = json.loads(json.dumps(recorded_content("story_post_draft")))
        for row in content["sentences"]:
            row.pop("calculation", None)
        found = self.DERIVED_ID.findall(prompt)
        gap = content["sentences"][2]
        if found:
            gap["fact_bindings"] = [{
                "fact_id": found[0],
                "rendered": "15.9 percentage points",
                "metric_surface": "Adjusted Gross Margin",
                "period_surface": "the third quarter of 2022"}]
            gap["citations"] = [{"evidence_id": fact.evidence_handle}
                                for fact in demo_inputs().package.facts]
        return content


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
    # **Ten since S13, not nine.** The derived-facts artifact is a run input the identity check
    # reads like every other, so a run that carried one and a run that did not would otherwise
    # be indistinguishable in this denominator. Raised because the check does more, never to
    # match a number a run happened to produce.
    assert outcome.verified.check("identity_and_freshness").examined == 10


def test_the_recorded_qwen_draft_is_accepted_and_every_check_says_what_it_looked_at(
    tmp_path, config
):
    """A real model's draft, accepted — and what every check had to look at to reach it.

    **Re-recorded live on 2026-08-19, second pass, not re-keyed.** The store this replaced was
    the same candidate under writer prompt 2.0.0, refused three times over because the model
    wrote the **unit** into every `metric_surface` it declared (`"percent"`, `"percent"`,
    `"percentage_points"`). Nothing in `story/stages/verification/` moved to clear those
    findings: `metric_surfaces_for` started offering the metric id, which
    `MetricAliasIndex` has always accepted and which the prompt was withholding, and the
    DERIVED FACTS row started handing the figure over in the words a sentence carries. The model
    then wrote the surfaces it was offered. Four consecutive `python -m story demo --live` runs
    against `http://127.0.0.1:8080` returned a byte-identical `generations.jsonl`
    (`sha256 8645d1a95a533b29…`).

    **The disposition alone would pass against a verifier that looked at nothing**, so the
    denominators are asserted with it — and they are the S13 claim: every numeral was examined,
    every period surface resolved, and the one computed figure in the post came from the
    derivation tool rather than from the model.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(ACCEPTED_RECORDING), config=config,
                       out_dir=tmp_path / "run")
    assert outcome.verified is not None

    assert outcome.disposition == ACCEPTED
    assert outcome.ok is True
    assert [f.code for f in outcome.verified.all_findings] == []
    assert [check.name for check in outcome.verified.checks if check.findings] == []
    assert outcome.verified.check("numbers").examined == 6
    assert outcome.verified.check("periods").examined == 3
    assert outcome.verified.check("title").examined == 1
    # 5 until H1 gave a derived binding the prose-grounding rule an observed binding has had
    # since R8. The extra examination is that rule running on the `compare_levels` sentence:
    # before it, a derived binding skipped §13.5's prose check on every sentence kind, and
    # *"Revenue fell $446 million"* over a derivation of adjusted gross profit was accepted.
    assert outcome.verified.check("metric_identity").examined == 6
    # The computed figure is code's, bound by its own id, and recomputed by the verifier against
    # the derivation rather than against the draft's arithmetic — there is none.
    assert [entry.rendered for entry in outcome.verified.calculation_ledger] == [
        "15.9 percentage points"]
    assert outcome.verified.calculation_ledger[0].recomputed_value == -15.9
    assert [entry.fact_id for entry in outcome.verified.fact_ledger][2].startswith(
        "fact:derived:compare-levels:")
    # The three surfaces the 2.0.0 recording got wrong, as the model wrote them under 2.1.0.
    assert [binding.metric_surface
            for sentence in outcome.draft.sentences
            for binding in sentence.fact_bindings] == [
        "gaap gross margin", "Adjusted Gross Margin", "gaap gross margin"]


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

    Driven by the **shipped recording**, which since the writer prompt's 2.1.0 repair reaches
    this branch on its own: the post asserted about below is a real model's answer, not a
    hand-repaired one. It was hand-repaired between the two 2026-08-19 re-records, and that is
    worth remembering rather than erasing — the draft this replaced was refused on three
    `metric_surface_unresolved` findings, and what changed is the prompt that produced it.

    The prose is rendered from the structured draft and never from the model's own text (§12),
    which is why the post can be asserted to hold a figure the verifier bound — and the second
    assertion is the S13 property on the accepted branch: the one computed figure in the post is
    one **code** produced, bound by its `fact:derived:` id, so no numeral in an accepted post is
    the model's arithmetic.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(ACCEPTED_RECORDING), config=config,
                       out_dir=tmp_path / "run")
    written = {path.name for path in (tmp_path / "run").iterdir()}

    assert outcome.disposition == ACCEPTED
    assert outcome.ok is True
    assert outcome.verified is not None and outcome.verified.all_findings == ()
    assert "post.md" in written
    assert "rejected.json" not in written
    post = (tmp_path / "run" / "post.md").read_text(encoding="utf-8")
    assert "3.3 percent" in post
    # S13's property, on the accepted branch and end to end: the one computed figure in the
    # post is bound to a fact **code** produced, and the post carries no numeral that is not
    # either a packaged observation or that derived result.
    assert "15.9 percentage points" in post
    assert [entry.fact_id for entry in outcome.verified.fact_ledger][2].startswith(
        "fact:derived:compare-levels:")
    assert all(binding.fact_id.startswith(("obs:", "fact:derived:"))
               for sentence in outcome.draft.sentences
               for binding in sentence.fact_bindings)
    assert all(sentence.calculation is None for sentence in outcome.draft.sentences)


# ---------------------------------------------------------------------------------------
# The 2026-08-19 re-record: the answers *did* move, because the question did
# ---------------------------------------------------------------------------------------

#: `sha256` of every artifact the **live recording run** wrote, measured in
#: `data/story_demo/story-v1-9911b4d86ec3` on 2026-08-19 from
#: `python -m story demo --candidate-id … --live` against `http://127.0.0.1:8080`.
#:
#: **This table replaces `BYTES_BEFORE_THE_REKEY`, and it is a different kind of claim.** That
#: table pinned artifact hashes measured at `d72ca64` and asserted they had not moved, because
#: MULTI_PROVIDER_OPENAI's two changes were **re-keys**: the request digest moved and the
#: answers did not. DETERMINISTIC_FACT_TOOLS §9 is not a re-key and could not be one — both
#: prompts and both schemas changed, `WRITER_PROMPT_VERSION` went 1.4.0 -> 2.0.0 by *removing*
#: `calculation`, and a 1.4.0 draft carries an object the schema no longer admits. So every
#: pinned value in that table is now the hash of an artifact built from an answer to a question
#: nobody asks any more, and keeping it would have asserted the re-record did not happen.
#:
#: What is pinned instead is the property §21 actually promises and the old table could not
#: state: byte-identical **replay**. These numbers came off the *live* run, and the test below
#: replays the committed store with nothing listening on `:8080` and requires the same bytes —
#: so a fixture whose answer was quietly edited after capture moves `draft.json`, and a
#: pipeline that stopped being deterministic moves everything.
#:
#: **Re-measured 2026-08-19 on the second re-record**, from `story-v1-79fd9ddc03fe`, which four
#: consecutive live runs produced identically. `candidate.json`, `evidence_package.json`,
#: `editorial_plan.json` and `derived_facts.json` hash to what the *first* re-record produced —
#: the planner prompt and the package did not move, so the plan and the derivation are the same
#: bytes — and `draft.json` moved because the writer's question did. `post.md` is here and
#: `rejected.json` is not, which is the accepted branch stated as a file listing.
#:
#: **Two values moved at H3 and no store was re-recorded**, which the module docstring argues
#: at length: `WRITER_PROMPT_VERSION` went 2.1.0 -> 2.2.0, `draft.json` carries that field, and
#: `verification_report.json` carries `draft_content_sha256`. `generations.jsonl` is the same
#: capture — confirmed by a live run against `:8080` after the change — and `post.md` is
#: byte-identical, so the model's answer and the published text are both untouched. That pair of
#: unmoved digests beside the pair that moved is what says which kind of change this was.
ARTIFACTS_OF_THE_LIVE_RECORDING: dict[str, str] = {
    "candidate.json": "e82e92c75de12282dfd5757c04967924787bdbc77ba38a800023a80df88326c1",
    "evidence_package.json": "a347b38c340ce3a8e66f56ce2b4b3c401433b2d3a2bd5b0a45a354bff339bb24",
    "editorial_plan.json": "813eb1829fa6c8fbb7f9dd65f54c1467d3da7f25b4d8bf5c5796bf555b6c25fe",
    "derived_facts.json": "fab099e89f87d5044b12cd626a3097e45986f1b4b6a2c9b161156e1dc6326820",
    "draft.json": "70b77ef91595e0145fb35c349dac39f60c9ec4754d3c96b5dc004cf6b3e4256c",
    # H1 moved this one digest and nothing else in the table: `metric_identity.examined` went
    # 5 -> 6 when the derived prose-grounding rule started counting what it checks. Confirmed by
    # a live re-run against `:8080` whose report differs from the recorded one on that single
    # field and whose `generations.jsonl` is byte-for-byte the row below — no prompt moved.
    "verification_report.json":
        "d01a6c05e6ef5a58f0279dcc421bbd4a12bb3cc6c0e6cb021c833e4f8302704a",
    "post.md": "2afa5fddece71bc2ea51f43ccb1d4d21cec49b90eb48237c40fd79b6e1cd0d26",
    "generations.jsonl": "8645d1a95a533b29ad5d2719e55a7137e3e166b0ff04d786f671153e11d87f68",
}

#: The same, for the one synthetic store, measured on its first replay after it was rebuilt from
#: the 2.1.0 recording. A regression pin and **not** a live measurement, which is why it sits in
#: its own table with its own name: nothing recorded this, it is the recording above with one
#: sentence's two sides swapped.
ARTIFACTS_OF_THE_SYNTHETIC_STORES: dict[str, dict[str, str]] = {
    REJECTED_STORE: {
        "editorial_plan.json": "813eb1829fa6c8fbb7f9dd65f54c1467d3da7f25b4d8bf5c5796bf555b6c25fe",
        "derived_facts.json": "fab099e89f87d5044b12cd626a3097e45986f1b4b6a2c9b161156e1dc6326820",
        "draft.json": "7b66ae62363f5e7e6a03a4f9ec0c4983f74ea7ed6406bf87726e96ba020eafb8",
        # Both moved at H1 and both for one reason: they embed the verification report, whose
        # `metric_identity.examined` went 5 -> 6. The refusal is unchanged — one finding, in
        # `language_safety`, `comparative_not_supported_by_text` — and every other digest in
        # this table is untouched, which is the statement that the writer's answer did not move.
        "verification_report.json":
            "4b2d02099f488b52d1013630b3705dfe63e60644928b07e59338e37e13130a0a",
        "rejected.json": "ed98746ac985c0d0d802bc79109deebb1c82d1a56ff97be085b6552102cbe515",
    },
}


def test_the_committed_recording_replays_offline_to_the_bytes_the_live_run_wrote(
    tmp_path, config
):
    """§21's byte-identical **replay**, measured against the capture rather than against itself.

    The store was written by a live run; this replays it with no inner provider, so nothing can
    reach `:8080`, and requires every artifact to hash to what that run produced. A
    self-comparison — replay twice, compare — would pass against a fixture somebody edited after
    capture, because both replays would read the edit. Comparing against the live run's own
    hashes is what makes "the file still holds what the model said" a failing test.
    """
    import hashlib

    run = tmp_path / "run"
    run_demo(demo_inputs(), provider=replaying(), config=config, out_dir=run)

    for name, expected in sorted(ARTIFACTS_OF_THE_LIVE_RECORDING.items()):
        assert hashlib.sha256((run / name).read_bytes()).hexdigest() == expected, name
    # `demo_manifest.json` is deliberately absent above: it holds the clock. `post.md` **is** in
    # the table since the 2.1.0 re-record — this run is accepted and writes one — and
    # `rejected.json` is absent for the same reason, which is itself part of the comparison.
    assert not (run / "rejected.json").exists()


@pytest.mark.parametrize("store_name", sorted(ARTIFACTS_OF_THE_SYNTHETIC_STORES))
def test_each_synthetic_store_replays_to_the_artifacts_it_was_built_to_produce(
    store_name, tmp_path, config
):
    """The two hand-authored stores, pinned so an edit to one cannot pass unnoticed.

    They are not recordings and this test does not pretend otherwise — see the table's own note.
    What it holds is that the pair differs in exactly one sentence's prose and therefore in
    exactly one artifact chain: `editorial_plan.json` and `derived_facts.json` are the *same
    bytes* in both, because the plan and the derivation are the recording's own and only the
    writer's answer was edited.
    """
    import hashlib

    run = tmp_path / "run"
    run_demo(demo_inputs(), provider=replaying(store_name), config=config, out_dir=run)

    for name, expected in sorted(ARTIFACTS_OF_THE_SYNTHETIC_STORES[store_name].items()):
        assert hashlib.sha256((run / name).read_bytes()).hexdigest() == expected, name
    # The half that says the pair is a pair: both synthetics and the recording share a plan.
    assert (ARTIFACTS_OF_THE_SYNTHETIC_STORES[store_name]["editorial_plan.json"]
            == ARTIFACTS_OF_THE_LIVE_RECORDING["editorial_plan.json"])
    assert (ARTIFACTS_OF_THE_SYNTHETIC_STORES[store_name]["derived_facts.json"]
            == ARTIFACTS_OF_THE_LIVE_RECORDING["derived_facts.json"])


#: What each committed row's `raw_content` hashes to, measured 2026-08-19 after the re-record.
#: `content_sha256` is `sha256(raw_content)` (`openai_compatible.py:344`), so recomputing it from
#: the committed text and finding the recorded value says the row on disk is internally
#: consistent — a row whose answer was edited without its digest being recomputed fails here.
#:
#: **The synthetic shares the recording's planner row byte for byte**, which is the file-level
#: statement of what the tests above assert through a run: one plan, one derivation, two writer
#: answers.
COMMITTED_CONTENT_DIGESTS: dict[str, dict[str, str]] = {
    ACCEPTED_RECORDING: {
        "story_editorial_plan":
            "52b3b181e5e4041ddc14302e4d76077fb660c416a0235645018e93c56abafa97",
        "story_post_draft": "7e36ba674235e20c71c136ca4059be1ef6d0f921c264c233dd553ef0b452f5a7",
    },
    REJECTED_STORE: {
        "story_editorial_plan":
            "52b3b181e5e4041ddc14302e4d76077fb660c416a0235645018e93c56abafa97",
        "story_post_draft": "8651da315a9d38c96f96144998e942692c8dc2e8d9abb3866c0d324e676bc36b",
    },
}


def test_every_committed_row_hashes_to_the_answer_it_carries():
    """Stated over the fixtures themselves, not over a run.

    The OpenAI recording is in the table for the reason it was in the one this replaced: it is
    the store whose answers no local server can reproduce, so an accidental edit to it would be
    undetectable from anywhere else in this suite.
    """
    import hashlib

    openai_recorded = {
        "story_editorial_plan":
            "ebf2f2e0c95550e3045276966fa4ffd75909fbc8eb3f844a2c99484f32d92145",
        "story_post_draft": "1fe6fb4689d14c3e2c3e885f0698edfaa4d041a48f9b4133603a95bace96b2bd",
    }
    for directory, provider_id, table in (
            (STORES, PROVIDER_ID, COMMITTED_CONTENT_DIGESTS),
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


def test_the_synthetic_is_the_recording_with_one_sentence_reversed():
    """The claim this module's docstring makes about the pair, checked against the bytes.

    The synthetic carries the recording's own planner answer unedited, and its writer answer
    differs from the recording's in the sentence that states the gap: the two metrics swap sides,
    so the sentence is false by 15.9 points while every id, every rendering and every citation in
    it stays the recording's. Asserting the planner row is identical is what stops a future edit
    from quietly re-planning one branch, which would make the accepted/rejected comparison a
    comparison of two different runs.

    **It was a pair of synthetics until the writer prompt's 2.1.0 repair.** The accepted one was
    deleted when the recording became accepted again, because a hand-authored accepted store
    beside a real one asserts nothing the real one does not.
    """
    def rows(name: str) -> dict[str, str]:
        return {row["schema_name"]: row["raw_content"] for row in (
            json.loads(line) for line in
            (STORES / name).read_text(encoding="utf-8").splitlines() if line.strip())}

    recording, rejected = rows(ACCEPTED_RECORDING), rows(REJECTED_STORE)

    assert rejected["story_editorial_plan"] == recording["story_editorial_plan"]
    assert rejected["story_post_draft"] != recording["story_post_draft"]
    # The one edit, in full: the two sides of the comparison swap, and the binding follows the
    # metric the sentence now puts first — so the store is a draft a model could have produced.
    assert ("The GAAP gross margin was 15.9 percentage points lower than the Adjusted Gross "
            "Margin") in recording["story_post_draft"]
    assert ("The Adjusted Gross Margin was 15.9 percentage points lower than the GAAP gross "
            "margin") in rejected["story_post_draft"]
    # And the surfaces the 2.0.0 recording got wrong are in neither: the model wrote metric
    # surfaces under 2.1.0, so there was nothing to repair by hand.
    for store in (recording, rejected):
        assert '"metric_surface": "percent"' not in store["story_post_draft"]
        assert '"metric_surface": "percentage_points"' not in store["story_post_draft"]


#: Every committed store, with the identity `story-generation-v3` says each row must state.
#: Read as a table because that is what the repair added: before 2026-08-19 the first two
#: columns were the whole of a row's stated key, and the last two were settings that changed the
#: request body and reached no digest at all.
COMMITTED_STORES: tuple[tuple[Path, str, str, bool, str | None], ...] = (
    (STORES / ACCEPTED_RECORDING, PROVIDER_LOCAL, MODEL_ID, True, None),
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

    inputs = demo_inputs()
    package = inputs.package
    # The same offer set `run_demo` prints, because it is inside the prompt and therefore inside
    # the digest: a request built here without one would key a row no run could ever ask for.
    call = dict(system=PLANNER_SYSTEM,
                prompt=planner_prompt(package, offered=offers(package, inputs.candidate)),
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
    # **The verdict is asserted equal rather than asserted to be a particular word.** What this
    # test attributes to the provider is the *identity*; which verdict two identical answers
    # reach is not its subject, and it has been both `rejected` (the 2.0.0 recording) and
    # `accepted` (the 2.1.0 one) without anything it tests having moved.
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
    """The live `gpt-5.4` run, reproduced from its own rows with nothing running.

    Everything asserted below was measured on the live run
    (`data/story_demo/story-v1-e3c263b28b02`, **re-recorded 2026-08-19**, second pass): the draft
    reached §13, §13 refused it, and the **seven** blocking findings are the three codes in the
    table in this module's docstring.

    **`metric_surface_ambiguous` is gone and nothing in §13.5 moved to lose it.** The writer
    prompt started offering the metric id as a surface, which `MetricAliasIndex` has always
    resolved uniquely, and `gpt-5.4` wrote *"gaap gross margin"* where it used to write a surface
    the alias index refuses. What it did differently on its own account: it requested **no**
    derivation this time, wrote six sentences rather than five, and put a period into a
    `connective` sentence. The rejection is the result and no rule was changed to avoid it
    (MULTI_PROVIDER_OPENAI §10, DETERMINISTIC_FACT_TOOLS §9).
    """
    outcome = run_demo(demo_inputs(), provider=openai_replaying(), config=config,
                       out_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "demo_manifest.json").read_text(encoding="utf-8"))

    assert outcome.disposition == REJECTED
    assert outcome.verified is not None and not outcome.verified.passed
    assert sum(1 for f in outcome.verified.all_findings if f.blocking) == 7
    assert {f.code for f in outcome.verified.all_findings} == {
        "unbound_numeral", "citation_reused_for_unrelated_claim",
        "connective_sentence_carries_a_claim"}
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
        ("qwen", STORES / ACCEPTED_RECORDING, PROVIDER_LOCAL, MODEL_ID, ACCEPTED),
        ("openai", OPENAI_STORES / "generations.jsonl", PROVIDER_OPENAI, OPENAI_MODEL_ID,
         REJECTED),
        ("rejected", STORES / REJECTED_STORE, PROVIDER_LOCAL, MODEL_ID, REJECTED),
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
    it.

    **Three cases, and the two recordings now sit on opposite sides of the verdict.** The Qwen
    recording is accepted since the writer prompt's 2.1.0 repair and the `gpt-5.4` one is still
    refused, so the two rows that carry real adapter answers already cover both branches — which
    is what the third row used to be needed for. It is kept, pointed at the rejected synthetic,
    because a hand-built refusal exercises the reporting path with a *single* finding where the
    OpenAI recording carries seven.

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
    # **`accepted`, and the shipped store is why** — see this module's docstring. The live
    # 2026-08-19 Qwen recording this replays is the second re-record, under writer prompt 2.1.0,
    # and it carries no finding; the rejected branch is asserted from the synthetic store, in
    # `test_a_rejected_run_writes_its_artifacts_and_writes_no_post`.
    assert manifest["demo"]["disposition"] == ACCEPTED
    assert manifest["demo"]["generation_mode"] == "replay"
    assert manifest["graph_run_id"] == GRAPH_RUN_ID
    assert manifest["model_id"] == MODEL_ID
    assert manifest["provider_model_id"].endswith(".gguf")
    assert manifest["temperature"] == 0.0
    # Both prompts moved again at DETERMINISTIC_FACT_TOOLS §5. The planner went 1.1.0 -> 1.2.0
    # for the DERIVATIONS OFFERED section and `requested_derivations[]`; the writer went 1.4.0
    # -> **2.0.0**, a major bump because it is the first version to *remove* a field, and a
    # draft recorded under 1.4.0 carries a `calculation` object the schema no longer admits.
    # The manifest is where a reader sees which wording produced these rows, and these two
    # numbers are why the committed stores had to be re-recorded rather than re-keyed.
    assert manifest["prompt_versions"] == {"story_editorial_plan": "1.2.0",
                                           "story_post_draft": "2.2.0"}
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
        "prompt_version": "1.2.0",
        "schema_name": "story_editorial_plan",
        "max_tokens": config.planner_max_tokens,
    }
    assert manifest["writer_provider_model"] == {
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "provider_model_id": "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
        "prompt_version": "2.2.0",
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
        "prompt_version": "1.2.0",
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
    """Six values, declared once, and every one of them reachable from this file's doubles.

    A list stated in more than one place is a list that ends up different lengths in different
    places, which is how a browser came to render a disposition set that had four members while
    `pipeline` had five. The demo UI reads `DISPOSITIONS` rather than restating it, and this is
    the end that says the constant is complete.

    **`derivation_refused` is the sixth** (DETERMINISTIC_FACT_TOOLS §5). It is not reachable
    from a model's answer at all: a triple outside the offer set is refused by `plan_violations`
    and ends the run as `plan_refused`, and a triple inside it has passed every clause of §4.2
    by construction. What is left is the detector-signal assertion — the candidate's own `gap`
    disagreeing with what the operation computed — so the double below moves the *candidate's*
    signal rather than the model's answer, which is what that disposition is about.
    """
    reached = {
        run_demo(inputs, provider=provider, config=config,
                 out_dir=tmp_path / name).disposition
        for name, inputs, provider in (
            ("accepted", demo_inputs(), replaying(ACCEPTED_RECORDING)),
            ("rejected", demo_inputs(), replaying(REJECTED_STORE)),
            ("plan", demo_inputs(), RefusedPlannerProvider()),
            ("draft", demo_inputs(), BadWriterProvider()),
            ("derivation", misreported_signal_inputs(),
             DerivingProvider([compare_levels_request()])),
            ("fault", demo_inputs(), UnreachableProvider()))
    }

    assert reached == set(DISPOSITIONS)
    assert len(DISPOSITIONS) == len(set(DISPOSITIONS)) == 6


# ---------------------------------------------------------------------------------------
# DETERMINISTIC_FACT_TOOLS §5 — the derivation stage, between the plan and the draft
# ---------------------------------------------------------------------------------------


def misreported_signal_inputs() -> DemoInputs:
    """The same run with the candidate's `gap` signal moved, and nothing else touched.

    A defect in one of two code paths, planted at the only place it can come from: the candidate
    is the detector's output and the package is the packager's, and §4.4 asserts the derived
    result equal to the signal rather than preferring either. `dataclasses.replace` on the frozen
    `DemoInputs` and `model_copy` on the frozen candidate, so nothing else in the run moves.
    """
    inputs = demo_inputs()
    signals = {**inputs.candidate.signals, "gap": 9.9}
    return replace(inputs, candidate=inputs.candidate.model_copy(update={"signals": signals}))


def test_a_run_writes_the_derived_facts_it_computed_and_names_the_file_in_its_manifest(
    tmp_path, config
):
    """§3: the derived facts are a **separate artifact**, and §14 hashes it like every other.

    They may not go in `evidence_package.json`: the planner selects the derivations, and
    `package_content_digest` is a `story_run_id` input, so a package whose contents depended on a
    model call would make the run id depend on the model's output. So the file is its own, and
    the manifest names it, hashes it and counts what went into it.
    """
    provider = DerivingProvider([compare_levels_request()])
    outcome = run_demo(demo_inputs(), provider=provider, config=config,
                       out_dir=tmp_path / "run")

    written = json.loads((outcome.directory / DERIVED_FACTS_FILENAME).read_text("utf-8"))
    assert [fact["operation"] for fact in written["facts"]] == ["compare_levels"]
    assert written["facts"][0]["result"] == 15.9
    assert written["facts"][0]["unit"] == "percentage_points"
    assert written["facts"][0]["display_semantics"] == "higher than"
    assert written["facts"][0]["reused_detector_signal"] == "gap"
    assert written["refusals"] == []
    assert written["tool_version"] == "1.0.0"
    # §7's fact, minted by code from the package alone and never requested.
    assert [row["claim"] for row in written["evidence_scope_facts"]] == [
        "no_supported_causal_explanation_in_package"]

    manifest = json.loads((outcome.directory / "demo_manifest.json").read_text("utf-8"))
    assert DERIVED_FACTS_FILENAME in manifest["artifacts"]
    assert manifest["artifacts"][DERIVED_FACTS_FILENAME] == hashlib.sha256(
        (outcome.directory / DERIVED_FACTS_FILENAME).read_bytes()).hexdigest()
    assert manifest["demo"]["derivation_tool_version"] == "1.0.0"
    assert manifest["counts"]["derivations_offered"] == 4
    assert manifest["counts"]["derivations_requested"] == 1
    assert manifest["counts"]["derived_facts"] == 1
    assert manifest["counts"]["derivation_refusals"] == 0
    assert manifest["counts"]["evidence_scope_facts"] == 1


def test_the_writer_is_shown_the_derived_fact_and_binds_the_id_it_was_shown(tmp_path, config):
    """The seam §5 puts between the two model calls, asserted from both sides.

    The double reads the `fact:derived:` id out of its own prompt rather than recomputing one,
    so a run where the DERIVED FACTS section carried nothing would bind nothing and this would
    fail on the draft rather than passing on a coincidence.
    """
    provider = DerivingProvider([compare_levels_request()])
    outcome = run_demo(demo_inputs(), provider=provider, config=config,
                       out_dir=tmp_path / "run")

    assert provider.calls == ["story_editorial_plan", "story_post_draft"]
    written = json.loads((outcome.directory / DERIVED_FACTS_FILENAME).read_text("utf-8"))
    derived_id = written["facts"][0]["fact_id"]
    assert f"[{derived_id}]" in provider.prompts["story_post_draft"]
    assert 'period surface: write exactly "the third quarter of 2022"' in (
        provider.prompts["story_post_draft"])

    assert outcome.draft is not None
    gap = outcome.draft.sentences[2]
    assert [b.fact_id for b in gap.fact_bindings] == [derived_id]
    assert gap.calculation is None
    assert gap.fact_bindings[0].period_surface == "the third quarter of 2022"


def test_the_offer_set_reaches_the_planner_prompt_and_bounds_what_it_may_ask_for(
    tmp_path, config
):
    """§4.3: the list is printed, and it is the same list the answer is checked against."""
    inputs = demo_inputs()
    provider = DerivingProvider([compare_levels_request()])
    run_demo(inputs, provider=provider, config=config, out_dir=tmp_path / "run")

    prompt = provider.prompts["story_editorial_plan"]
    offered = offers(inputs.package, inputs.candidate)
    assert "DERIVATIONS OFFERED (4 available" in prompt
    for request in offered:
        assert (f'operation "{request.operation.value}"  '
                f'from_fact_id "{request.from_fact_id}"  '
                f'to_fact_id "{request.to_fact_id}"') in prompt


def test_a_derivation_the_detectors_own_signal_contradicts_ends_the_run_and_writes_no_draft(
    tmp_path, config
):
    """§4.4: two code paths computing one number and differing is a defect in one of them.

    It is refused rather than resolved by preference, and the run stops there — the writer is
    never called, because a plan resting on a quantity that does not exist cannot be written.
    Recorded as its own disposition rather than as `plan_refused`, which would blame a model for
    a disagreement between two deterministic computations.
    """
    provider = DerivingProvider([compare_levels_request()])
    outcome = run_demo(misreported_signal_inputs(), provider=provider, config=config,
                       out_dir=tmp_path / "run")

    assert outcome.disposition == DERIVATION_REFUSED
    assert outcome.refusal_codes == ("derived_result_mismatch",)
    assert provider.calls == ["story_editorial_plan"]
    assert outcome.draft is None and outcome.verified is None
    assert not (outcome.directory / "post.md").exists()
    assert not (outcome.directory / "draft.json").exists()

    written = json.loads((outcome.directory / DERIVED_FACTS_FILENAME).read_text("utf-8"))
    assert written["facts"] == []
    assert [row["code"] for row in written["refusals"]] == ["derived_result_mismatch"]

    rejected = json.loads((outcome.directory / "rejected.json").read_text("utf-8"))
    assert rejected["stage"] == STAGE_DERIVATION
    assert rejected["codes"] == ["derived_result_mismatch"]


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
    """The accepted branch of the same verb, over the **shipped recording**.

    The store is named explicitly rather than left to the default it resolves to, so that this
    test and the rejected one above are configured the same way and a store move touches both
    lines. The branch — exit 0, the post named on stdout, `post.md` on disk — is what this test
    exists for.
    """
    class Closable:
        def close(self) -> None:
            self.closed = True

    accepted = DemoConfig(**{
        **vars(DemoConfig.load(REPO_ROOT)),
        "generation_stores": {PROVIDER_ID: str(STORES / ACCEPTED_RECORDING)}})
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
    """The loud failure, against the real detector output rather than a one-entry mapping.

    **227 and not 15** *(re-measured 2026-08-19)*, and the reason is the whole of 643935f.
    `resolve_demo_inputs` used to run `detect_cross_metric_divergence` and nothing else, so the
    15 available ids were that one detector's and every `metric_move` candidate — including
    S13's driving one — was unreachable from `python -m story demo`. It now dispatches on the
    **id's own detector segment**, so the probe below names `metric-move` and is answered with
    that detector's 227 candidates. The number is asserted rather than dropped because "the
    message lists what the graph really produces" is the claim, and it is expected to move
    whenever a detector or the graph does.
    """
    from story.context import build_story_context
    from story.pipeline import resolve_demo_inputs

    context = build_story_context(REPO_ROOT)
    try:
        with pytest.raises(CandidateNotFound) as raised:
            resolve_demo_inputs(context, candidate_id="cand:metric-move:not-a-thing:x:2022Q3:0",
                                graph_run_id=GRAPH_RUN_ID)
        # …and an id naming no detector at all is refused with the four names, before any of
        # them runs, rather than with an empty candidate set that would blame the id for the
        # wrong thing. Inside the `try`, because it needs the same open driver.
        with pytest.raises(CandidateNotFound) as unknown:
            resolve_demo_inputs(context, candidate_id="cand:no-such-detector:x:y:2022Q3:0",
                                graph_run_id=GRAPH_RUN_ID)
    finally:
        context.close()
    assert len(raised.value.available) == 227
    # One detector's candidates and not four, which is the cost argument 643935f kept: the id
    # named `metric-move`, so that is the detector that ran and the only family it can list.
    assert {candidate_id.split(":")[1] for candidate_id in raised.value.available} == {
        "metric-move"}
    assert set(unknown.value.available) == {
        "acceleration", "cross-metric-divergence", "metric-move", "trend-reversal"}


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
    all — and it has moved twice on 2026-08-19 without anything this test is about moving:
    refused under writer prompt 2.0.0, accepted again under 2.1.0. What is asserted alongside is
    the part that is this test's subject: the identity check, which is the one that compares the
    graph-built package against the digest the prompt was rendered over, found nothing.
    """
    outcome = run_demo(live_inputs, provider=replaying(), config=config,
                       out_dir=tmp_path / "run")

    assert outcome.disposition == ACCEPTED
    assert outcome.verified is not None and outcome.verified.passed is True
    assert [f.code for f in outcome.verified.all_findings] == []
    assert outcome.verified.check("identity_and_freshness").findings == ()
    assert outcome.verified.check("identity_and_freshness").examined == 10
    assert (tmp_path / "run" / "demo_manifest.json").is_file()


@pytest.mark.live
def test_live_the_qwen_writer_still_files_its_answer_under_the_committed_row(config):
    """*The committed row is the row the running server produces* — proved by calling it.

    The re-record claim has two halves and replay only proves one. Replay proves the committed
    file still holds what was captured; this proves the request that captured it is the request
    the demo path builds today. The planner's row is replayed so only the writer's call is live
    — the same isolation `config/story.yaml`'s `length_target` measurement uses — and the live
    answer is then looked up **by the committed key**: a digest input that had drifted since the
    capture would land the answer somewhere else and this fails.

    **Rebuilt for DETERMINISTIC_FACT_TOOLS §5, and the two new arguments are the point.**
    `plan_story` is handed the offer set and `write_story` the derived facts, because both are
    printed into their prompts and both are therefore inside `request_identity`. Calling either
    without them would key the request under a prompt with an empty section — a request no run
    can issue — so the arguments are not a convenience here, they are what makes the digest the
    demo path's own.

    Equality of the *generation* is asserted here where
    `test_live_the_model_server_answers_the_planner_about_the_same_package` deliberately refuses
    to, and the difference is measured rather than assumed: that test records a planner answer
    that moved with the server's request history, while the writer's answer to this package and
    this plan reproduced byte for byte across four consecutive `--live` runs on 2026-08-19
    (`generations.jsonl` `sha256 1e26754afa26930f…` every time). A failure here is a finding
    about the runtime, not a flaky assertion — and it is the writer, not the planner, that is
    claimed.
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
    # live answer that landed on the committed key proves the capture and the running
    # configuration compute the same digest.
    provider = ReplayingStoryGenerationProvider(
        seeded, server, provider_id=PROVIDER_ID, model_id=MODEL_ID)

    from story.stages.derivation.execute import execute_all
    from story.stages.detection import detector_config
    from story.stages.generation.planner import causal_language_for

    inputs = demo_inputs()
    package = inputs.package
    offered = offers(package, inputs.candidate)
    planned = plan_story(package, provider=provider, max_tokens=config.planner_max_tokens,
                         offered=offered)
    # The composition root's own wiring, not a second one: `pipeline.run_demo` calls
    # `execute_all` with exactly these arguments, and a hand-rolled substitute here would be a
    # second answer to "what did the writer see".
    derivation = execute_all(
        planned.plan.requested_derivations, package, inputs.candidate,
        direction=detector_config.quantity_direction,
        causal_language=causal_language_for(package),
        causal_marker_fact_ids=pipeline._causal_marker_fact_ids(package),
        offered=offered)
    write_story(package, planned.plan, provider=provider,
                derived_facts=(*derivation.facts, *derivation.evidence_scope_facts),
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
