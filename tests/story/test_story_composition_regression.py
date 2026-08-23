"""S7 — the 22 rejected runs, recompiled through the deterministic boundary, and the A/B.

`docs/2026-08-23-deterministic-draft-compiler/02-IMPLEMENTATION-PLAN.md` §S7. Every `rejected`
run under `data/story_demo/` — all 22 of them — re-expressed as **sentence templates carrying
the same intended claim as the recorded prose**, compiled by `story/stages/composition/`, and
driven through the unchanged `DeterministicVerifier`.

**The headline: 6 of the 22 are accepted, 16 stay refused, and the 74 recorded blocking findings
become 26.** Every one of the 16 is named below with the reason it did not improve.

The rule that makes the number honest: a template may re-word for the slot grammar and may fix a
bookkeeping error, and may **not** change what the sentence asserts. Where the recorded sentence
was factually wrong the case is `expect: "refused"` and the corpus says so.

The A/B, one row per run, `old` being the count of blocking findings the recorded
`verification_report.json` holds:

    run                    old now      blocking findings now, and the old ones they replace
    ---------------------- --- -------- --------------------------------------------------------
    story-v1-01dcfff9e128   11 refused  citation_reused_for_unrelated_claim x2
                                        was: binding_rendering_is_not_one_numeral x3,
                                        calculation_does_not_recompute,
                                        connective_sentence_carries_a_claim,
                                        metric_surface_absent_from_text,
                                        metric_surface_ambiguous, metric_surface_unresolved x2,
                                        unbound_numeral, unsupported_comparative

    story-v1-0e9fccbb7fcb    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-0fdd6feabc00    6 accepted (none)
                                        was: calculation_result_surface_mismatch,
                                        comparative_not_supported_by_text,
                                        period_named_in_text_contradicts_binding,
                                        period_unresolvable, unbound_numeral x2

    story-v1-2405d9b03c5e    7 refused  citation_reused_for_unrelated_claim x2
                                        was: calculation_result_surface_mismatch,
                                        citation_reused_for_unrelated_claim x2,
                                        comparative_not_supported_by_text, period_mismatch,
                                        period_named_in_text_contradicts_binding,
                                        unbound_numeral

    story-v1-36848882656f    1 accepted (none)
                                        was: package_content_digest_mismatch

    story-v1-3a0e9609dfa5    1 accepted (none)
                                        was: number_outside_tolerance

    story-v1-3f434de5edbb    2 accepted (none)
                                        was: derived_unit_mismatch, unbound_numeral

    story-v1-50c0f3c4a1c2    2 refused  citation_reused_for_unrelated_claim
                                        was: citation_reused_for_unrelated_claim,
                                        comparative_not_supported_by_text

    story-v1-5bf86e6fd2aa    6 refused  unbound_numeral x4
                                        was: citation_reused_for_unrelated_claim x2,
                                        unbound_numeral x4

    story-v1-5c67f125038c    1 accepted (none)
                                        was: package_content_digest_mismatch

    story-v1-5e62c41ddd1e    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-7a6a17e412e0    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-836e9d47b261    4 refused  calculated_sentence_without_calculation,
                                        citation_reused_for_unrelated_claim, unbound_numeral x2
                                        was: citation_reused_for_unrelated_claim,
                                        derived_operation_not_supported, derived_unit_mismatch,
                                        unbound_numeral

    story-v1-8f59e09fccb8    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-9911b4d86ec3    3 accepted (none)
                                        was: metric_surface_unresolved x3

    story-v1-9d82316fe30f    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-d77f67988132    6 refused  citation_reused_for_unrelated_claim x2
                                        was: citation_reused_for_unrelated_claim,
                                        comparative_not_supported_by_text, unbound_numeral x4

    story-v1-d8bf3d1b19bf    2 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text,
                                        package_content_digest_mismatch

    story-v1-d9bc7b2cab33    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-e101d5b08b3c    1 refused  comparative_not_supported_by_text
                                        was: comparative_not_supported_by_text

    story-v1-e3c263b28b02    7 refused  citation_reused_for_unrelated_claim
                                        was: citation_reused_for_unrelated_claim x4,
                                        connective_sentence_carries_a_claim, unbound_numeral x2

    story-v1-f7667486551e    8 refused  citation_reused_for_unrelated_claim x2
                                        was: citation_reused_for_unrelated_claim x4,
                                        metric_surface_ambiguous x2, unbound_numeral x2

**Why each of the 16 stays refused**

* **8 reversed comparatives** — `0e9fccbb7fcb`, `5e62c41ddd1e`, `7a6a17e412e0`, `8f59e09fccb8`,
  `9d82316fe30f`, `d8bf3d1b19bf`, `d9bc7b2cab33`, `e101d5b08b3c`. One sentence Qwen wrote in
  eight runs: *"The Adjusted Gross Margin was 15.9 percentage points lower than the GAAP Gross
  Margin."* 3.3 is 15.9 points **higher** than -12.6. The model still chooses which handle goes
  on which side of the direction word, so the lie is still writable — and
  `test_the_reversed_comparatives_are_all_still_refused` is the guard that says so out loud.
  This is the outcome the whole change must not have: if one of these ever becomes `accepted`,
  verification was weakened.
* **6 explanatory sentences resting on a span already spent** — `01dcfff9e128`, `2405d9b03c5e`,
  `50c0f3c4a1c2`, `d77f67988132`, `e3c263b28b02`, `f7667486551e`, all
  `citation_reused_for_unrelated_claim`. This is the code the compiler was expected to remove and
  **does not**: it mints one citation per bound fact and never carries one forward, which is why
  `e3c263b28b02` drops from four findings to one and `f7667486551e` from four to two — but an
  *explanatory* sentence binds no fact by construction, so it takes a handle off the passage row,
  and when every handle of that passage is already spent `compile._unused_handle` falls back to
  the first rather than leaving the claim uncited. §13.7 then reads the prose and refuses. The
  refusal is correct: this package's slice holds one passage, the margin table, which states the
  two levels and defines neither metric. Both alternatives were measured and are worse —
  `rests_on: []` lands as `uncited_factual_sentence`, and re-kinding the sentence `connective`
  lands as `connective_sentence_carries_a_claim`. **The old run hid the fault** behind a fact
  binding whose `rendered` span was the word *"divergence"*; the new boundary makes that
  impossible and the real fault surfaces. That is a tightening, not a regression.
* **`5bf86e6fd2aa` — invariant R4, and the floor of the boundary.** Sentences 0 and 3 carry no
  numeral and name two quarters. `SLOT_WITHOUT_BINDING` refuses a `{{F1.period}}` with no
  `{{F1}}` beside it, so the phrase can only be the model's own prose and its year is
  `unbound_numeral`. Not expressible at all, and refused for the same code as before.
* **`836e9d47b261` — the plan asked for `trend_direction`**, which `derivation/offers.py` has
  withdrawn from the offer set, so `execute_all` refuses it and no row exists for the direction
  claim to bind.

**Three recorded findings this corpus does not reproduce, said plainly.**
`package_content_digest_mismatch` (`36848882656f`, `5c67f125038c`, `d8bf3d1b19bf`) is
environmental — a stale package replayed — and out of the compiler's scope. Each candidate is
replayed here against **one** committed package, chosen as the variant whose
`package_content_digest` recomputes today, so the code cannot fire. Recomputing all 22 recorded
packages finds 9 that no longer hash to their own digest against only 3 recorded findings, which
says the digest function moved after those runs; that is a fact about the corpus, not about this
change.

**Fixtures.** The divergence candidate reuses `tests/story/fixtures/story_demo/`; the two
metric-move candidates are committed under `rejected_corpus/`, copied from a run directory whose
`demo_manifest.json` lists the file in its `artifacts` map. The facts, values and observation ids
are identical across each candidate's package variants — only the token `budget` block, the
package id and (for `e101d5b08b3c`'s `package_version` 1.2.0 slice) the evidence-handle scheme
differ. Each run's own `editorial_plan.json` is committed byte-for-byte under `plans/`, because
the plan is one of the four inputs `verify` judges against and a plan borrowed from another run
would be judging these templates against a story nobody asked for. Two fields are re-keyed on
load, exactly as `test_story_composition._recorded_plan` does it: `candidate_id` and `package_id`
are the package's, so a plan recorded against a superseded variant does not read as
`plan_names_another_package`.

**`derivations`, and why a case may carry one.** Nine of the 22 plans are `prompt_version` 1.1.0
and predate `requested_derivations` — those runs declared the derivation on the *sentence*, as a
model-authored `Calculation`, which is the path `calculation_does_not_recompute` and
`calculation_result_surface_mismatch` name. Under the new boundary a derived value exists only
where the plan asked for it, so a case whose recorded `verification_report.json` holds a
`calculation_ledger` entry names the derivation the new boundary would have to request in its
place, taken from `offers(package, candidate)`. It is recorded per case in `cases.json` and is
the one input here that is neither the recorded artifact nor the model's words. Two mappings are
not identities and are called out: the ledger's `compare_levels` over a **two-period** metric
move is not an offerable operation, and `absolute_change` is the offered operation that states
the same gap; `5c67f125038c`'s ledger operation is the string `difference`, which is not an
operation at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.evidence_slice import passages_backing_facts
from story.core.models import (
    DerivationRequest,
    EditorialPlan,
    SentenceKind,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.stages.composition import SentenceTemplate, compile_draft, slot_table
from story.stages.derivation.execute import execute_all
from story.stages.derivation.offers import offers
from story.stages.detection import detector_config
from story.stages.verification.deterministic import DeterministicVerifier

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "rejected_corpus"

#: One committed JSON list rather than 22 files. The deliverable is a **table** — a reader
#: compares rows, and `git diff` on one file shows a changed expectation beside the rows it did
#: not change. The packages and the plans stay separate files because those are artifacts copied
#: off disk and re-serialising them here would put a second, editable copy of a recorded thing
#: in the tree.
CASES: tuple[dict, ...] = tuple(
    json.loads((CORPUS / "cases.json").read_text(encoding="utf-8")))

#: `world` names the directory holding the candidate's `candidate.json` and
#: `evidence_package.json`. The divergence candidate's is the demo fixture the writer and
#: composition suites already replay; nothing is duplicated for it.
WORLDS = {
    "story_demo": FIXTURES / "story_demo",
    "metric_move_adjusted_gross_profit": CORPUS / "metric_move_adjusted_gross_profit",
    "metric_move_homes_purchased": CORPUS / "metric_move_homes_purchased",
}

#: The eleven codes §5.2 of the architecture document claims become structurally unreachable
#: once the compiler owns the numeral, the surfaces and the span — the same list
#: `test_story_composition.py` holds, asserted here over the **real corpus** rather than over
#: hand-written fixtures.
UNREACHABLE_ON_THE_COMPILER_PATH = (
    "number_outside_tolerance",
    "sign_disagreement",
    "unit_mismatch",
    "currency_symbol_on_non_monetary_unit",
    "binding_rendering_is_not_one_numeral",
    "binding_span_does_not_match_text",
    "metric_surface_unresolved",
    "metric_surface_ambiguous",
    "metric_binding_mismatch",
    "period_unresolvable",
    "period_mismatch",
)

#: The eight runs of §5.1's highest-value catch. Named here so the guard test cannot be made to
#: pass by a corpus edit that quietly re-labels one of them.
REVERSED_COMPARATIVE_RUNS = (
    "story-v1-0e9fccbb7fcb",
    "story-v1-5e62c41ddd1e",
    "story-v1-7a6a17e412e0",
    "story-v1-8f59e09fccb8",
    "story-v1-9d82316fe30f",
    "story-v1-d8bf3d1b19bf",
    "story-v1-d9bc7b2cab33",
    "story-v1-e101d5b08b3c",
)

IDS = [case["run"] for case in CASES]


def _world(name: str) -> tuple[StoryEvidencePackage, StoryCandidate]:
    directory = WORLDS[name]
    return (
        StoryEvidencePackage.model_validate_json(
            (directory / "evidence_package.json").read_text(encoding="utf-8")),
        StoryCandidate.model_validate_json(
            (directory / "candidate.json").read_text(encoding="utf-8")),
    )


def _plan(case: dict, package: StoryEvidencePackage) -> EditorialPlan:
    """The run's own recorded plan, re-keyed onto the committed package.

    Only `candidate_id` and `package_id` are rewritten, and only because §13.13 refuses a plan
    naming another package — the same two fields `planner.plan_from` fills from the package and
    never from the model. `requested_derivations` is replaced only where the case names one,
    which is the 1.1.0 plans that have no such field at all.
    """
    raw = json.loads((CORPUS / "plans" / f"{case['run']}.json").read_text(encoding="utf-8"))
    plan = EditorialPlan.model_validate({
        **raw, "candidate_id": package.candidate_id, "package_id": package.package_id})
    if not case["derivations"]:
        return plan
    by_handle = {f"F{position}": fact.observation_id
                 for position, fact in enumerate(package.facts, start=1)}
    return plan.model_copy(update={"requested_derivations": tuple(
        DerivationRequest(operation=request["operation"],
                          from_fact_id=by_handle[request["from"]],
                          to_fact_id=by_handle[request["to"]])
        for request in case["derivations"])})


def _compile(case: dict):
    """The case's world, its real derived facts, and the compiled draft — no model in the loop.

    `causal_language` is read off the recorded plan rather than recomputed through
    `planner.causal_language_for`. §11 computes that field from the package before the call and
    the model never chooses it, so the recorded value **is** the code-computed one — and reading
    it back keeps this module a replay of the run rather than a re-plan of it, and keeps a
    regression corpus for the composition stage free of an import of the generation stage.
    """
    package, candidate = _world(case["world"])
    plan = _plan(case, package)
    executed = execute_all(
        plan.requested_derivations, package, candidate,
        direction=detector_config.quantity_direction,
        causal_language=plan.causal_language,
        causal_marker_fact_ids=(),
        offered=offers(package, candidate))
    derived = (*executed.facts, *executed.evidence_scope_facts)
    passages = passages_backing_facts(package)
    templates = tuple(
        SentenceTemplate(index, template["text"], SentenceKind(template["kind"]),
                         rests_on=tuple(template["rests_on"]))
        for index, template in enumerate(case["templates"]))
    compiled = compile_draft(templates, package, plan, derived_facts=derived,
                             passages=passages, title="Opendoor",
                             model_id="qwen3.5-9b", prompt_version="3.0.0")
    return compiled, package, plan, derived, passages


def _verify(case: dict):
    compiled, package, plan, derived, _passages = _compile(case)
    return compiled, DeterministicVerifier().verify(
        compiled.draft, package, plan, derived_facts=derived)


# ---------------------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------------------


def test_the_corpus_holds_every_rejected_run_once():
    """22 runs, three candidates, and a committed plan for each.

    The count is the measurement §2.1 of the architecture document published: of 50 recorded
    runs, 35 produced a draft and 22 of those were refused. A corpus that quietly dropped the
    inconvenient ones would make every number below meaningless.
    """
    assert len(CASES) == 22
    assert len(set(IDS)) == 22
    assert {case["world"] for case in CASES} == set(WORLDS)
    for case in CASES:
        assert (CORPUS / "plans" / f"{case['run']}.json").exists(), case["run"]


def test_every_template_set_reproduces_every_recorded_sentence():
    """One template per recorded sentence, in the recorded order.

    A draft is judged whole — `required_warning_absent`, `required_counterpoint_absent` and the
    plan-coverage checks all read the sentence list — so a case that kept only the sentences that
    were refused would be replaying a different post and would earn a disposition that says
    nothing about the run it is named for.
    """
    for case in CASES:
        assert len(case["templates"]) == len(case["recorded_prose"]), case["run"]
        assert case["recorded_prose"], case["run"]


def test_every_case_declares_an_outcome_the_vocabulary_holds():
    for case in CASES:
        assert case["expect"] in {"accepted", "refused"}, case["run"]
        assert bool(case["expect_codes"]) is (case["expect"] == "refused"), case["run"]
        assert case["why"].strip(), case["run"]


# ---------------------------------------------------------------------------------------
# 1 — the compiler builds the metadata, with no model input
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_binding_field_came_from_the_row_and_not_from_the_template(case):
    """The claim of the whole change, asserted per binding over the real corpus.

    Four fields the writer used to retype are checked against the slot table the compiler filled
    from: the numeral occupies a span the compiler **wrote** (a `SlotFill` for the value slot of
    the same handle, not a span located by searching the model's prose), both surfaces are
    strings that row offers, and every citation handle belongs to a row this sentence names.
    """
    compiled, package, _plan, derived, passages = _compile(case)
    rows = {row.handle: row for row in slot_table(package, derived, passages)}
    value_fills = {(fill.sentence_index, fill.char_start, fill.char_end): fill
                   for fill in compiled.slots if fill.field == ""}

    for sentence, template in zip(compiled.draft.sentences, case["templates"]):
        assert "{{" not in sentence.text and "}}" not in sentence.text
        for binding in sentence.fact_bindings:
            key = (sentence.index, binding.char_start, binding.char_end)
            assert key in value_fills, (case["run"], sentence.index, binding.rendered)
            fill = value_fills[key]
            row = rows[fill.handle]
            assert row.fact_id == binding.fact_id
            assert sentence.text[binding.char_start:binding.char_end] == binding.rendered
            assert binding.rendered == row.offers[""]
            assert binding.metric_surface in set(row.offers.values())
            assert binding.period_surface in set(row.offers.values())

        citable = {handle
                   for row in rows.values()
                   if row.handle in {fill.handle for fill in compiled.slots
                                     if fill.sentence_index == sentence.index}
                   or row.handle in template["rests_on"]
                   for handle in row.evidence_handles}
        assert {citation.evidence_handle for citation in sentence.citations} <= citable, (
            case["run"], sentence.index)


# ---------------------------------------------------------------------------------------
# 2 and 3 — the verifier's answer, per case
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_verifier_returns_the_recorded_expectation(case):
    """The A/B, executed. `expect` is what the verifier said when this corpus was built.

    For a refused case the expectation is the **full** blocking list and not a subset, so a new
    finding appearing on a case that already refuses is a failure here rather than a silent
    change of reason.
    """
    _compiled, verified = _verify(case)
    blocking = sorted(finding.code for finding in verified.all_findings if finding.blocking)

    assert verified.passed is (case["expect"] == "accepted"), (case["run"], blocking)
    assert blocking == sorted(case["expect_codes"]), case["run"]


def test_the_reversed_comparatives_are_all_still_refused():
    """§5.1's highest-value catch, over all eight runs that wrote it.

    Not folded into the parametrised test above: this is the one outcome the change must not
    have, and it is worth a failure that names *itself* rather than one that names a corpus row.
    An `accepted` here means the verifier stopped reading the prose.
    """
    for run in REVERSED_COMPARATIVE_RUNS:
        case = next(entry for entry in CASES if entry["run"] == run)
        _compiled, verified = _verify(case)

        assert verified.passed is False, run
        assert [finding.code for finding in verified.all_findings if finding.blocking] == [
            "comparative_not_supported_by_text"], run


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_no_compiled_case_can_violate_the_numeral_or_surface_checks(case):
    """§5.3's insurance, moved from hand-written fixtures onto the corpus that produced the bug.

    `metric_surface_unresolved` fired 5 times in this corpus and `metric_surface_ambiguous` 3;
    `binding_rendering_is_not_one_numeral` 3 and `number_outside_tolerance` once. All eleven
    stay in the verifier and stay reachable for a replayed artifact or a hand-built draft — what
    this asserts is that a draft **the compiler built** cannot raise one. The residual risk is a
    compiler bug, and an empty list per code over 22 real runs is what holds it.
    """
    _compiled, verified = _verify(case)

    offending = [(finding.code, finding.observed) for finding in verified.all_findings
                 if finding.code in UNREACHABLE_ON_THE_COMPILER_PATH]
    assert offending == [], case["run"]


# ---------------------------------------------------------------------------------------
# The headline
# ---------------------------------------------------------------------------------------


def test_the_headline_numbers_are_what_the_module_docstring_publishes():
    """6 accepted, 16 refused, 74 recorded blocking findings down to 26.

    A number in a document nobody executes is a number that goes stale. This is the test that
    fails when the table above stops being true — including if it moves in the *good* direction,
    because a jump in the accepted count is a claim that has to be re-argued case by case
    against the rule that a template may not change what a sentence asserts.
    """
    accepted = [case["run"] for case in CASES if case["expect"] == "accepted"]
    refused = [case["run"] for case in CASES if case["expect"] == "refused"]
    recorded = sum(len(case["recorded_codes"]) for case in CASES)
    remaining = sum(len(case["expect_codes"]) for case in CASES)

    assert len(accepted) == 6, accepted
    assert len(refused) == 16, refused
    assert (recorded, remaining) == (74, 26)
    # §2.2 of the architecture document counted 74 blocking findings over these runs, twice, by
    # two readers. The corpus agreeing to the finding is what says it is the same 22 runs.
    assert set(REVERSED_COMPARATIVE_RUNS) <= set(refused)


def test_the_only_reasons_a_case_stays_refused_are_the_four_the_docstring_names():
    """Sixteen refusals, four reasons, and no fifth one hiding in the corpus.

    The failure this guards is a case quietly re-labelled `refused` with a new code because it
    became inconvenient — which would keep every count above true and make the table a lie.
    """
    reasons = {
        "comparative_not_supported_by_text",       # the reversed claim, 8 runs
        "citation_reused_for_unrelated_claim",     # an explanatory sentence on a spent span
        "unbound_numeral",                         # R4: a numeral-free sentence naming a period
        "calculated_sentence_without_calculation",  # trend_direction, withdrawn from the offers
    }

    seen = {code for case in CASES for code in case["expect_codes"]}

    assert seen == reasons, seen ^ reasons
