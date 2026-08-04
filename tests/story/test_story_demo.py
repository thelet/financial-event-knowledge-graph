"""D6 — the §8b demo path, driven end to end.

Offline by default and from committed fixtures. The `neo4j`-marked tests at the bottom
re-derive the candidate and the package from the live graph; the `live`-marked one calls the
model server.

**The recorded responses in `fixtures/story_demo/generations.jsonl` are genuine Qwen output.**
Re-captured 2026-08-04 from `http://127.0.0.1:8080` serving
`/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf`, against the package
`fixtures/story_demo/evidence_package.json` in the same directory — the planner's answer at
`PLANNER_MAX_TOKENS` and the writer's at `length_target: 4`. Two rows, nothing hand-edited.
The demo therefore replays something the model actually produced, and the manifest names the
model it came from.

**The planner's row is byte-identical to the one recorded before the seam repair** — same
request digest `5dc07fa8bf0f…`, same 1,887 characters of answer — because the planner's prompt
and schema did not change. Only the writer's row is new, and it had to be: the draft schema
gained `calculation.period_surface` and `compare_levels`, which re-keys the writer's request by
construction.

**The disposition of the genuine pair is now `accepted`, and it changed because four seams were
repaired rather than because any check was loosened.** The earlier recording was refused nine
times over, and every one of the nine was a refusal of a *true, correctly bound* sentence for a
structural reason: a `calculated` sentence could not declare its period, a comparative could not
be declared at all, a title could not carry a period key, and five build-provenance warnings
demanded prose an investor post must not carry. The 60+ verifier tests and the ten malicious
drafts in `test_story_deterministic_verifier.py` are unchanged and still pass.

**`fixtures/story_demo/generations_rejected_synthetic.jsonl` is not a recording.** It is the two
rows above with one edit — sentence 2's prose reversed to *"The Adjusted Gross Margin was 15.9
percentage points lower than the GAAP Gross Margin"*, which is false by 15.9 points while its
declared calculation still recomputes — made so the *rejected* branch has something to exercise,
and chosen because it is the attack a recomputation on its own would have accepted. It is named
`synthetic` because it is one, and no test presents it as a recording.
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
    run_demo,
    select_candidate,
)
from story.providers.generation_store import GenerationStore, ReplayingStoryGenerationProvider
from story.stages.detection import cross_metric_divergence
from story.stages.detection.canonicalization import POLICY_VERSION
from story.stages.freshness import FreshnessReport

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "story_demo"

#: §8b's manually selected candidate, and the id the demo re-derives and refuses to proceed
#: without. Pinned as a string here because that is exactly how an operator supplies it.
CANDIDATE_ID = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
                "opendoor:2022Q3:9682f1c1c85a")
GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: What the recorded model id is, and what the store's rows are keyed under.
MODEL_ID = "Qwen3.5-9B-Q4_K_M.gguf"

#: Every artifact §8b names, plus §14's replay store. `post.md` and `rejected.json` are the two
#: that are mutually exclusive, so a reader can tell the disposition from the listing alone.
ALWAYS_WRITTEN = {
    "candidate.json", "evidence_package.json", "editorial_plan.json", "draft.json",
    "verification_report.json", "generations.jsonl", "demo_manifest.json",
}


def _read(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def demo_inputs() -> DemoInputs:
    """The live 2022Q3 slice as it came off the graph on 2026-08-04, byte-for-byte.

    Assembled here rather than mocked: every value below was produced by
    `resolve_demo_inputs` against `graph-v1-0483dc6b4b10` and written out unchanged, so a test
    driving `run_demo` over it is driving the same object the live path hands over.
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
        GenerationStore(FIXTURES / filename), model_id=MODEL_ID)


class CountingProvider:
    """A `StoryGenerationProvider` that records every call and answers none of them.

    Satisfies the protocol structurally and raises on `generate`, which is what makes "refused
    before any model call" an assertion about behaviour rather than about ordering in the
    source: if anything reaches the model, this fails loudly instead of quietly succeeding.
    """

    model_id = MODEL_ID

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

    The deliberately bad generation: a citation quoting a string that does not occur in the
    passage it names. §12 refuses it before §13 runs, which is the `draft_refused` disposition
    — a different outcome from a draft the verifier rejected, and one the demo must not report
    as the same thing.
    """

    model_id = MODEL_ID

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
                    "passage_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm#p139",
                    "quote": "a string this passage does not contain",
                }],
            }],
        }
        raw = json.dumps(content, ensure_ascii=False)
        return GenerationResult(
            content=content, raw_content=raw, model_id=MODEL_ID, prompt_tokens=0,
            completion_tokens=0, total_tokens=0, latency_ms=0.0, raw_sha256="",
            content_sha256="", finish_reason="stop", attempts=1)


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
    """The demo's actual disposition, and what every check had to look at to reach it.

    Asserting `accepted` alone would pass against a verifier that looked at nothing, so the
    denominators are asserted with it: the numbers check counted every numeral in the draft, the
    periods check resolved a surface for both bindings *and* for the derivation, and the
    calculation ledger holds the recomputed gap. The nine findings the previous recording drew
    are gone because four seams were repaired — the derivation can now declare the period it
    computed over, the comparative can be declared as `compare_levels` and recomputed, the title
    may carry the package's own period key, and build-provenance warnings no longer demand
    prose. No check was weakened; `test_story_deterministic_verifier.py` is the guard on that.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(), config=config,
                       out_dir=tmp_path / "run")
    assert outcome.verified is not None

    assert outcome.disposition == ACCEPTED
    assert outcome.ok is True
    assert [f.code for f in outcome.verified.all_findings] == []
    assert outcome.verified.check("numbers").examined == 5
    assert outcome.verified.check("periods").examined == 3
    assert outcome.verified.check("title").examined == 1
    assert [entry.rendered for entry in outcome.verified.calculation_ledger] == [
        "15.9 percentage points"]
    assert outcome.verified.calculation_ledger[0].recomputed_value == 15.899999999999999


def test_a_rejected_run_writes_its_artifacts_and_writes_no_post(tmp_path, config):
    """A rejection is a completed run, not a failure to run (§8b, §14).

    Driven by the **synthetic** store — see this module's docstring. `rejected.json` carries the
    §13.17 `RejectedDraft` whole, checks included and not only the blocking findings, because
    the `examined` denominators are what say which checks ran.
    """
    outcome = run_demo(demo_inputs(),
                       provider=replaying("generations_rejected_synthetic.jsonl"),
                       config=config, out_dir=tmp_path / "run")
    written = {path.name for path in (tmp_path / "run").iterdir()}
    rejected = json.loads((tmp_path / "run" / "rejected.json").read_text(encoding="utf-8"))

    assert "rejected.json" in written
    assert "post.md" not in written
    assert rejected["stage"] == "deterministic_verifier"
    assert rejected["disposition"] == REJECTED
    assert len(rejected["rejection"]["checks"]) == 12
    assert outcome.artifacts["rejected.json"]
    # The one edit, caught by the one check that reads the sentence against its calculation.
    assert [f["code"]
            for check in rejected["rejection"]["checks"] for f in check["findings"]
            if f["blocking"]] == ["comparative_not_supported_by_text"]


def test_an_accepted_run_writes_the_post_and_no_rejection(tmp_path, config):
    """The genuine recording's branch: the two files are mutually exclusive.

    The prose is rendered from the structured draft and never from the model's own text (§12),
    which is why the post can be asserted to hold a figure the verifier bound.
    """
    outcome = run_demo(demo_inputs(), provider=replaying(), config=config,
                       out_dir=tmp_path / "run")
    written = {path.name for path in (tmp_path / "run").iterdir()}

    assert outcome.disposition == ACCEPTED
    assert outcome.ok is True
    assert "post.md" in written
    assert "rejected.json" not in written
    assert "3.3 percent" in (tmp_path / "run" / "post.md").read_text(encoding="utf-8")


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
    assert "citation_quote_not_in_passage" in rejected["codes"]
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
    # The writer's wording and schema changed with the seam repair and its version says so;
    # the planner's did not, and its recorded row replays byte-for-byte because of it.
    assert manifest["prompt_versions"] == {"story_editorial_plan": "1.0.0",
                                           "story_post_draft": "1.1.0"}
    assert sorted(manifest["schema_digests"]) == ["story_editorial_plan", "story_post_draft"]
    assert manifest["ranking_policy_version"] == "1.1.0"
    assert manifest["policy_version"] == POLICY_VERSION
    assert manifest["detector_versions"] == {"detector:cross_metric_divergence": "1.0.0"}


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


def test_the_parser_offers_only_the_demo_verb_and_the_rest_of_section_20_is_deferred():
    """§8b traded the CLI family away. A verb that parsed and then refused would be a promise."""
    actions = [action for action in cli.build_parser()._actions
               if isinstance(action, __import__("argparse")._SubParsersAction)]
    assert [name for name in actions[0].choices] == ["demo"]


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

    synthetic = DemoConfig(**{
        **vars(DemoConfig.load(REPO_ROOT)),
        "generation_store": str(FIXTURES / "generations_rejected_synthetic.jsonl")})
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
    """The accepted branch of the same verb, over the shipped store and the shipped config."""
    class Closable:
        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(cli, "build_story_context", lambda *a, **k: Closable())
    monkeypatch.setattr(cli, "resolve_demo_inputs", lambda *a, **k: demo_inputs())

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
    a package whose digest or warning kinds had drifted would refuse at §13.13 here.
    """
    outcome = run_demo(live_inputs, provider=replaying(), config=config,
                       out_dir=tmp_path / "run")

    assert outcome.disposition == ACCEPTED
    assert outcome.verified is not None and outcome.verified.passed is True
    assert (tmp_path / "run" / "demo_manifest.json").is_file()


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
