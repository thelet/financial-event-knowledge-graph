"""Deterministic slot recovery: the model's prose, unaltered, becoming a compilable template.

`docs/2026-08-26-story-pipeline-stabilization-plan/03-WRITER-AND-COMPOSITION-STABILIZATION.md`.
Three claims are load-bearing and everything else supports them.

* **`test_the_recorded_writer_answer_becomes_an_accepted_post`** — the answer the 9B actually
  returned on `story-v1-1daff167348f`, a run that ended `draft_refused`, recovered against that
  run's own slot table, compiled, and verified with **zero findings at any severity**. The three
  compiled sentences are byte-identical to the model's prose. No prompt change, no model change,
  no rule relaxed.
* **`test_recovery_never_changes_a_character_the_model_wrote`** — recovery is text-preserving by
  construction, asserted over the recorded prose of all 22 rejected runs and, where the run
  directories are present, over every sentence of every stored `draft.json`. The template with
  each slot expanded back through `SlotRow.offers` is the input, byte for byte.
* **`test_a_literal_two_rows_could_claim_is_left_alone`** and its sibling — the safety property.
  Recovery **never guesses**. Where a span could be two facts, or one fact's figure appears
  twice, the literal stays exactly where the model put it, a diagnostic is recorded, and §13
  refuses it as `unbound_numeral` with an exact span. A second stage that decided which fact a
  numeral is would be a second authority over §13.1's question.

**Fixtures.** The candidate's package and candidate are already committed under
`fixtures/rejected_corpus/metric_move_adjusted_gross_profit/` and are **byte-identical** to
`data/story_demo/story-v1-1daff167348f/evidence_package.json` *(sha256 compared 2026-08-26)*, so
nothing is duplicated for them. The run's own `editorial_plan.json` and the recorded writer
answer are committed under `fixtures/recovery_corpus/`, following
`test_story_composition_regression.py`'s precedent: the plan is one of the four inputs `verify`
judges against, and a plan borrowed from another run would judge these sentences against a story
nobody asked for. The derived facts are **not** committed — the real derivation stage rebuilds
them from the plan, and its output was compared against the run's stored `derived_facts.json`
and is identical *(verified 2026-08-26)*. A hand-built `DerivedFact` would let recovery agree
with a row the derivation stage would never mint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.evidence_slice import passages_backing_facts
from story.core.models import (
    EditorialPlan,
    EvidenceRole,
    KeyPoint,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    SentenceKind,
    StatementClass,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.stages.composition import (
    CompositionRefused,
    SLOT_PATTERN,
    VALUE_CLAIMED_BY_TWO_ROWS,
    VALUE_FIELD,
    VALUE_OCCURS_TWICE,
    SentenceTemplate,
    SlotRow,
    compile_draft,
    derive_kind,
    normalize_templates,
    recover_sentence,
    slot_table,
)
from story.stages.derivation.execute import execute_all
from story.stages.derivation.offers import offers
from story.stages.detection import detector_config
from story.stages.generation.planner import causal_language_for
from story.stages.verification.deterministic import DeterministicVerifier

from conftest import make_package, make_plan

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "rejected_corpus"
RECOVERY = FIXTURES / "recovery_corpus"
RUN = "story-v1-1daff167348f"

#: The run directories, present only where a demo has been executed. `data/` is gitignored, so
#: every claim that must hold from a clean checkout is made against the committed corpus and the
#: sweep over the run directories is an extra rather than the proof.
RUNS_ROOT = Path(__file__).resolve().parents[2] / "data" / "story_demo"

WORLDS = {
    "story_demo": FIXTURES / "story_demo",
    "metric_move_adjusted_gross_profit": CORPUS / "metric_move_adjusted_gross_profit",
    "metric_move_homes_purchased": CORPUS / "metric_move_homes_purchased",
}

CASES: tuple[dict, ...] = tuple(
    json.loads((CORPUS / "cases.json").read_text(encoding="utf-8")))


def _world(name: str) -> tuple[StoryEvidencePackage, StoryCandidate]:
    directory = WORLDS[name]
    return (
        StoryEvidencePackage.model_validate_json(
            (directory / "evidence_package.json").read_text(encoding="utf-8")),
        StoryCandidate.model_validate_json(
            (directory / "candidate.json").read_text(encoding="utf-8")),
    )


PACKAGE, CANDIDATE = _world("metric_move_adjusted_gross_profit")


def _plan() -> EditorialPlan:
    """The run's own recorded plan, re-keyed onto the committed package.

    The two identity fields are the package's, exactly as `planner.plan_from` fills them and as
    `test_story_composition._recorded_plan` re-keys them: a plan recorded against a superseded
    package variant would otherwise read as `plan_names_another_package` and replace every
    finding this file is about with one about bookkeeping.
    """
    raw = json.loads(
        (RECOVERY / f"{RUN}.editorial_plan.json").read_text(encoding="utf-8"))
    return EditorialPlan.model_validate({
        **raw, "candidate_id": PACKAGE.candidate_id, "package_id": PACKAGE.package_id})


PLAN = _plan()


def _derived():
    """This run's derived facts, from the real derivation stage over the recorded plan."""
    result = execute_all(
        PLAN.requested_derivations, PACKAGE, CANDIDATE,
        direction=detector_config.quantity_direction,
        causal_language=causal_language_for(PACKAGE),
        causal_marker_fact_ids=(),
        offered=offers(PACKAGE, CANDIDATE))
    assert result.refusals == (), [item.code for item in result.refusals]
    return (*result.facts, *result.evidence_scope_facts)


DERIVED = _derived()
PASSAGES = passages_backing_facts(PACKAGE)
ROWS = slot_table(PACKAGE, DERIVED, PASSAGES)

#: The answer the 9B returned on the run that ended `draft_refused`. Read rather than typed:
#: the claim is about *these* characters.
ANSWER = json.loads((RECOVERY / f"{RUN}.writer_answer.json").read_text(encoding="utf-8"))
WRITTEN = tuple(sentence["text"] for sentence in ANSWER["sentences"])


def expand(text: str, rows) -> str:
    """A template with every slot replaced by the string its row offers.

    The inverse of recovery, written here rather than borrowed from `compile._substitute`
    because the claim under test is about `SlotRow.offers` and not about the compiler: if the
    two ever disagree, the headline test is what says so.
    """
    by_handle = {row.handle: row for row in rows}
    return SLOT_PATTERN.sub(
        lambda match: by_handle[match.group(1)].offers[match.group(2) or VALUE_FIELD], text)


# -- the headline ------------------------------------------------------------------------------


def test_the_recorded_writer_answer_becomes_an_accepted_post() -> None:
    """The refused run's own answer, recovered, compiled, and verified clean.

    `story-v1-1daff167348f` ended `draft_refused`: the 9B wrote three true sentences in plain
    prose and the writer's structural gate had no slot to bind. Recovery finds that every figure
    it wrote is a string the slot table already offers, and the same three sentences reach the
    reader unaltered with every numeral inserted by code.

    The `kind` of each is derived here too, and the third one is the measurement of §4: the
    model labelled it `calculated` and was right, but a model that had labelled it `reported`
    would have verified clean, because `reported_sentence_carries_calculation` fires on
    `sentence.calculation is not None` and the 3.0.0 compiler never builds one.
    """
    normalized = normalize_templates(WRITTEN, ROWS)

    assert [template.text for template in normalized.templates] == [
        "{{F1}} {{F1.metric}} in {{F1.period}}.",
        "{{F2}} {{F2.metric}} in {{F2.period}}.",
        "{{D1.metric}} {{D1.direction}} {{D1}} from {{D1.from_period}} to {{D1.to_period}}.",
    ]
    assert [template.kind for template in normalized.templates] == [
        SentenceKind.REPORTED, SentenceKind.REPORTED, SentenceKind.CALCULATED]
    assert normalized.ambiguous == ()
    assert normalized.hand_written == ()

    compiled = compile_draft(
        normalized.templates, PACKAGE, PLAN,
        derived_facts=DERIVED, passages=PASSAGES, title=ANSWER["title"])
    assert [sentence.text for sentence in compiled.draft.sentences] == list(WRITTEN)

    verified = DeterministicVerifier().verify(
        compiled.draft, PACKAGE, PLAN, derived_facts=DERIVED)
    assert verified.all_findings == (), [
        (finding.code, finding.severity.value) for finding in verified.all_findings]
    assert verified.passed


def test_every_numeral_the_accepted_post_shows_was_inserted_by_code() -> None:
    """The property the stage exists for, on the run recovery was designed against.

    Each `FactBinding`'s `rendered` is a literal slice of the sentence at the span the compiler
    wrote, and the string came off a trusted row rather than out of the model's typing — even
    though the model *did* type those characters. That is the point of matching only what a row
    offers: the model's spelling and the row's are the same string, so agreeing with it costs
    nothing and disagreeing with it refuses.
    """
    normalized = normalize_templates(WRITTEN, ROWS)
    compiled = compile_draft(
        normalized.templates, PACKAGE, PLAN,
        derived_facts=DERIVED, passages=PASSAGES, title=ANSWER["title"])
    offered = {row.offers.get(VALUE_FIELD) for row in ROWS}
    bound = 0
    for sentence in compiled.draft.sentences:
        for binding in sentence.fact_bindings:
            bound += 1
            assert sentence.text[binding.char_start:binding.char_end] == binding.rendered
            assert binding.rendered in offered
    assert bound == 3


# -- text preservation -------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["run"] for case in CASES])
def test_recovery_never_changes_a_character_the_model_wrote(case: dict) -> None:
    """Every recorded sentence of all 22 rejected runs, recovered and expanded back.

    The corpus is the models' own prose — three providers, four prompt versions — and none of it
    was written for the slot grammar. That is what makes it the right population: recovery has to
    be safe on sentences nobody shaped for it.

    Expansion goes through `SlotRow.offers`, which is the same map `compile._substitute` reads,
    so an equality here is the substitution contract and not a restatement of the code that
    produced the template.
    """
    package, candidate = _world(case["world"])
    rows = slot_table(package, (), passages_backing_facts(package))
    normalized = normalize_templates(tuple(case["recorded_prose"]), rows)
    for original, template in zip(case["recorded_prose"], normalized.templates):
        assert expand(template.text, rows) == original


def test_the_corpus_measurement_is_what_it_was_measured_to_be() -> None:
    """The counts behind the property above, so it cannot go vacuous unnoticed.

    Measured 2026-08-26 over `cases.json`: **79 recorded sentences, 33 of them rewritten into
    templates, 0 ambiguous literals.** Zero is the interesting number — two rows rendering alike
    is constructible and this corpus does not hold it, so the safety property is tested on a
    hand-built package and recorded here as not yet observed in the wild.
    """
    sentences = rewritten = ambiguous = 0
    for case in CASES:
        package, _ = _world(case["world"])
        rows = slot_table(package, (), passages_backing_facts(package))
        normalized = normalize_templates(tuple(case["recorded_prose"]), rows)
        sentences += len(normalized.templates)
        rewritten += sum(template.text != original for original, template
                         in zip(case["recorded_prose"], normalized.templates))
        ambiguous += len(normalized.ambiguous)
    assert (sentences, rewritten, ambiguous) == (79, 33, 0)


def test_recovery_preserves_every_stored_draft_sentence() -> None:
    """The same property over every `draft.json` on disk, where the runs have been executed.

    Skipped rather than fixtured because `data/` is gitignored and the committed corpus above
    already carries the claim. Measured 2026-08-26 over the 40 stored drafts: **133 sentences,
    79 of them rewritten into templates, 0 changed by a character.**
    """
    if not RUNS_ROOT.is_dir():
        pytest.skip("no run directories on this checkout; the committed corpus carries the claim")
    seen = rewritten = 0
    for directory in sorted(RUNS_ROOT.iterdir()):
        draft = directory / "draft.json"
        if not draft.is_file():
            continue
        package = StoryEvidencePackage.model_validate_json(
            (directory / "evidence_package.json").read_text(encoding="utf-8"))
        rows = slot_table(package, (), passages_backing_facts(package))
        # The raw JSON and not `Draft.model_validate_json`: drafts recorded before
        # `PassageCitation.evidence_handle` became required do not load, and the claim under
        # test is about the sentence text, which every one of them carries.
        sentences = json.loads(draft.read_text(encoding="utf-8"))["sentences"]
        texts = tuple(sentence["text"] for sentence in sentences)
        for original, template in zip(texts, normalize_templates(texts, rows).templates):
            seen += 1
            rewritten += template.text != original
            assert expand(template.text, rows) == original
    assert seen and rewritten, "the sweep found nothing to prove the property on"


# -- the safety property -----------------------------------------------------------------------


def flat_package() -> StoryEvidencePackage:
    """One metric, two quarters, the same figure in both — two rows that render alike.

    Hand-built because no package in the corpus holds it, and it is not an exotic shape: a
    metric flat across two quarters produces exactly this, and *"which quarter is this sentence
    about"* then has no answer in the digits.
    """
    facts = tuple(
        PackagedFact(
            observation_id=f"obs:adjusted-ebitda:{period}",
            metric_id="adjusted_ebitda",
            metric_label="Adjusted EBITDA",
            period_key=period,
            period_start=start,
            period_end=end,
            shape="duration",
            value=56_000_000.0,
            unit="USD",
            currency="USD",
            scale="millions",
            source_lane="normalized_table",
            validation_state="ok",
            passage_id="psg:1",
            document_id="doc:1",
            quoted_text="56",
        )
        for period, start, end in (
            ("2022Q2", "2022-04-01", "2022-06-30"),
            ("2022Q3", "2022-07-01", "2022-09-30")))
    return make_package(
        facts=facts,
        metrics=(PackagedMetric(metric_id="adjusted_ebitda", label="Adjusted EBITDA",
                                unit="USD"),),
        primary_passages=(PackagedPassage(
            passage_id="psg:1", document_id="doc:1", text="Adjusted EBITDA 56 56",
            char_count=21, role=EvidenceRole.PRIMARY_SUPPORT),))


def test_a_literal_two_rows_could_claim_is_left_alone() -> None:
    """`$56 million` where two facts render alike: no binding, and a diagnostic instead.

    Recovery could bind either and the compiled text would be identical — which is precisely why
    it must not. `FactBinding.fact_id` is what §13.1 recomputes the value against and what the
    evidence panel highlights, so a coin toss here publishes a citation to the wrong quarter's
    cell under a numeral that happens to match.
    """
    package = flat_package()
    rows = slot_table(package)
    assert {row.offers[VALUE_FIELD] for row in rows} == {"$56 million"}

    text = "Adjusted EBITDA was $56 million in the second quarter of 2022."
    normalized = normalize_templates((text,), rows)

    assert normalized.templates[0].text == text
    assert normalized.recovered == ()
    assert len(normalized.ambiguous) == 1
    record = normalized.ambiguous[0]
    assert record.code == VALUE_CLAIMED_BY_TWO_ROWS
    assert record.literal == "$56 million"
    assert record.handles == ("F1", "F2")
    assert text[record.char_start:record.char_end] == "$56 million"


def test_the_verifier_and_not_recovery_is_what_refuses_the_unbound_literal() -> None:
    """The other half of the safety property: refusing is §13's job, and §13 does it.

    Recovery raises nothing and produces a template. The numeral it declined to claim reaches
    the verifier as prose, and `unbound_numeral` names it with an exact span — which is a
    repairable finding pointing at the digits, where a compile-time refusal would have pointed
    at a rule.
    """
    package = flat_package()
    rows = slot_table(package)
    plan = make_plan(
        candidate_id=package.candidate_id, package_id=package.package_id,
        key_points=(KeyPoint(claim="Adjusted EBITDA was $56 million in 2022Q2.",
                             required_fact_ids=("obs:adjusted-ebitda:2022Q2",),
                             required_citation_passage_ids=("psg:1",),
                             statement_class=StatementClass.REPORTED),))
    normalized = normalize_templates(
        ("Adjusted EBITDA was $56 million in the second quarter of 2022.",), rows)
    compiled = compile_draft(normalized.templates, package, plan, title="Opendoor")
    verified = DeterministicVerifier().verify(compiled.draft, package, plan)

    codes = [finding.code for finding in verified.all_findings if finding.blocking]
    assert "unbound_numeral" in codes, codes


def test_one_rows_figure_written_twice_binds_neither_occurrence() -> None:
    """The row is unambiguous and the span is not — and a `FactBinding` declares a span.

    Binding the first occurrence would decide, silently, which of two identical numerals a
    reader is meant to check; the second would then be `unbound_numeral` anyway, with the post
    already carrying a citation under the wrong one.
    """
    text = ("$556 million was the second quarter's Adjusted Gross Profit, and "
            "$556 million is what the reconciliation shows.")
    normalized = normalize_templates((text,), ROWS)

    assert normalized.templates[0].text == text
    assert normalized.recovered == ()
    assert [record.code for record in normalized.ambiguous] == [
        VALUE_OCCURS_TWICE, VALUE_OCCURS_TWICE]
    assert {record.handles for record in normalized.ambiguous} == {("F1",)}
    for record in normalized.ambiguous:
        assert text[record.char_start:record.char_end] == "$556 million"


def test_a_sentence_that_already_carries_a_slot_is_passed_through_untouched() -> None:
    """Recovery never mixes modes, and the reason is what a half-recovered sentence would say.

    *"{{F1}} of $556 million"* would reach the reader as the same figure twice with one of them
    bound, and the compiler has no way to know which one a §13.1 finding is about. The model
    wrote a template or it wrote prose.
    """
    text = "{{F1}} Adjusted Gross Profit in the second quarter of 2022."
    normalized = normalize_templates((text,), ROWS)

    assert normalized.templates[0].text == text
    assert normalized.recovered == ()
    assert normalized.hand_written == (0,)
    assert normalized.templates[0].kind is SentenceKind.REPORTED


def test_a_near_miss_spelling_is_refused_rather_than_rewritten() -> None:
    """`$556.0 million` is the same value and is not the row's string, so it is not recovered.

    This is `03` §3's one stated trade-off, kept deliberately. Matching the wider
    `legal_renderings` set would recover it — and would also recover `556000000.0 USD`, which
    `legal_renderings(F1)` really does offer *(verified 2026-08-26)*, publishing prose nobody
    wrote. The cost is a refusal on a near miss, with feedback naming the exact string to use.
    """
    text = "$556.0 million Adjusted Gross Profit in the second quarter of 2022."
    normalized = normalize_templates((text,), ROWS)

    assert normalized.templates[0].text == text
    assert normalized.recovered == ()
    assert normalized.ambiguous == ()
    # Not an anchor, so no field slot either: a period slot on a row this sentence never bound
    # is `slot_without_binding`, which would name the wrong fault.
    assert normalized.templates[0].kind is SentenceKind.CONNECTIVE


def test_an_invented_figure_is_not_recovered() -> None:
    """`$999 million` occurs in no row, so nothing claims it and nothing is recorded.

    A sentence with nothing recoverable is not an error — it becomes a `connective`, and §13
    then has both `unbound_numeral` and `connective_sentence_carries_a_claim` to say about it.
    """
    text = "$999 million Adjusted Gross Profit in the second quarter of 2022."
    normalized = normalize_templates((text,), ROWS)

    assert normalized.templates[0].text == text
    assert normalized.recovered == ()
    assert normalized.ambiguous == ()

    compiled = compile_draft(normalized.templates, PACKAGE, PLAN, derived_facts=DERIVED,
                             passages=PASSAGES, title="Opendoor 2022Q3")
    verified = DeterministicVerifier().verify(
        compiled.draft, PACKAGE, PLAN, derived_facts=DERIVED)
    codes = sorted({finding.code for finding in verified.all_findings if finding.blocking})
    assert codes == ["connective_sentence_carries_a_claim", "unbound_numeral"]


def test_a_figure_inside_a_longer_run_of_characters_is_not_a_match() -> None:
    """The standalone rule, which is what keeps a match from being read out of another number.

    `US$556 million` and `$556 millionths` both contain the row's string and neither states the
    row's figure. Without the guard the compiler would substitute a slot rendering the same
    characters back — text-preserving, and a lie about which figure the sentence carries.

    Each probe asserts the offered string really is a substring first, so the test cannot pass
    by the match never having been possible.
    """
    for text in ("US$556 million was the total.",
                 "$556 millionths of the total.",
                 "The ratio was 0.$556 million."):
        assert "$556 million" in text, text
        normalized = normalize_templates((text,), ROWS)
        assert normalized.templates[0].text == text, text
        assert normalized.recovered == (), text


def test_a_sentence_final_full_stop_does_not_block_a_period_slot() -> None:
    """The one exception in the standalone rule, and the measurement that forced it.

    Every period surface this corpus offers ends in a year, so `…of 2022.` is the ordinary
    shape. Guarding `.` unconditionally would refuse the period slot in the plan's own worked
    example; a decimal point still guards, because a digit sits on its far side.
    """
    normalized = normalize_templates(
        ("$556 million Adjusted Gross Profit in the second quarter of 2022.",), ROWS)
    assert normalized.templates[0].text.endswith("{{F1.period}}.")


def test_a_field_two_anchored_rows_offer_alike_is_taken_deterministically() -> None:
    """Rule 4: identical string, identical span, either row — so pick one and record the other.

    A field slot mints no `FactBinding` (`compile._bindings_from` iterates value slots only), so
    the compiled draft is byte-identical whichever row wins and the surfaces the bindings
    declare are read off the rows rather than off the text. What matters is that the choice is
    the same on every build and that the rival is visible in `composition.json`.
    """
    text = ("$556 million and $110 million of Adjusted Gross Profit were reported "
            "in the second quarter of 2022 and the third quarter of 2022.")
    first = normalize_templates((text,), ROWS)
    again = normalize_templates((text,), ROWS)
    assert first.templates[0].text == again.templates[0].text

    metric = [one for one in first.recovered if one.field == "metric"]
    assert len(metric) == 1
    assert metric[0].handle == "F1", "sorted by handle, so F1 wins the tie with F2"
    assert metric[0].also_offered_by == ("{{F2.metric}}",)
    assert expand(first.templates[0].text, ROWS) == text


# -- the kind table ----------------------------------------------------------------------------


@pytest.mark.parametrize("template,rests_on,expected", [
    ("{{F1}} {{F1.metric}} in {{F1.period}}.", (), SentenceKind.REPORTED),
    ("{{D1.metric}} {{D1.direction}} {{D1}} from {{D1.from_period}} to {{D1.to_period}}.",
     (), SentenceKind.CALCULATED),
    ("{{F1}} fell to {{F2}}, a fall of {{D1}}.", (), SentenceKind.CALCULATED),
    ("The filing describes the measure.", ("P1",), SentenceKind.EXPLANATORY),
    ("The quarter was unusual.", (), SentenceKind.CONNECTIVE),
    ("{{F1.metric}} is a non-GAAP measure.", (), SentenceKind.CONNECTIVE),
    ("{{P1}} says so.", ("P1",), SentenceKind.EXPLANATORY),
], ids=["observed", "derived", "mixed", "rests_on", "no-slot", "field-only", "passage-slot"])
def test_the_kind_derivation_table(
    template: str, rests_on: tuple[str, ...], expected: SentenceKind
) -> None:
    """§4's rule, every branch, against the run's own rows.

    `mixed` is the case the plan called out for a decision: a sentence naming an observed and a
    derived value slot is `calculated`, and the shape is **not** refused here. Such a sentence is
    separately fragile and §13 is what judges it — withholding a compile to force that refusal is
    the compiler acting as a verifier.

    `field-only` is why the rule keys on *value* slots: a sentence naming a metric surface binds
    nothing, so it carries no claim §13.9 can be asked about. `passage-slot` is why a `{{P1}}`
    does not count as one: a passage row offers no value, and calling the sentence `reported`
    would add a second refusal saying it claimed a figure it never claimed.
    """
    assert derive_kind(template, ROWS, rests_on=rests_on) is expected


def test_deriving_the_kind_closes_the_gap_a_model_written_label_left_open() -> None:
    """A derived-bearing sentence labelled `reported` verifies **clean** today. That is the gap.

    `reported_sentence_carries_calculation` fires on `sentence.calculation is not None`, and the
    3.0.0 compiler never builds a `Calculation` — `grep -rn "Calculation"
    story/stages/composition/` returns nothing *(verified 2026-08-26)*. So the check that should
    catch the wrong label has nothing to fire on. This test asserts both halves: the mislabelled
    draft still passes, and recovery cannot produce it.
    """
    template = ("{{D1.metric}} {{D1.direction}} {{D1}} from {{D1.from_period}} "
                "to {{D1.to_period}}.")
    mislabelled = compile_draft(
        (SentenceTemplate(index=0, text=template, kind=SentenceKind.REPORTED),),
        PACKAGE, PLAN, derived_facts=DERIVED, passages=PASSAGES, title="Opendoor 2022Q3")
    verified = DeterministicVerifier().verify(
        mislabelled.draft, PACKAGE, PLAN, derived_facts=DERIVED)
    assert verified.passed, "the gap is real; if this fails the verifier grew the check"
    assert mislabelled.draft.sentences[0].calculation is None

    assert derive_kind(template, ROWS) is SentenceKind.CALCULATED


# -- the compiler / verifier citation disagreement ---------------------------------------------


def _rested_on(index: int, handle: str) -> SentenceTemplate:
    return SentenceTemplate(index=index, text="The filing describes the measure.",
                            kind=SentenceKind.EXPLANATORY, rests_on=(handle,))


def _reports(index: int) -> SentenceTemplate:
    return SentenceTemplate(index=index, text="{{F1}} {{F1.metric}} in {{F1.period}}.",
                            kind=SentenceKind.REPORTED)


def _compile(*templates: SentenceTemplate):
    return compile_draft(templates, PACKAGE, PLAN, derived_facts=DERIVED,
                         passages=PASSAGES, title="Opendoor 2022Q3")


def _verifier() -> DeterministicVerifier:
    return DeterministicVerifier()


def _verify(*templates: SentenceTemplate):
    return _verifier().verify(
        _compile(*templates).draft, PACKAGE, PLAN, derived_facts=DERIVED)


def test_the_compiler_and_the_verifier_still_disagree_about_a_reused_citation() -> None:
    """**A recorded disagreement, not a passing behaviour**, and the record of why it stands.

    `compile._unused_handle` gives an `explanatory` sentence's `rests_on` citation the evidence
    handle of a fact read from that passage — a table **cell**. `citations._reuse_findings` then
    refuses the second use of an identical `(passage_id, char_start, char_end)` by a sentence
    binding nothing that passage evidences, and an explanatory sentence binds nothing by
    construction. So the predicate is one that kind of sentence can never satisfy, and which of
    the two is *second* decides the run.

    Four repairs were measured (2026-08-26) and each is blocked or worse: a passage-scoped handle
    earns `unresolvable_evidence_handle`; the cell handle with a widened span earns
    `evidence_cell_span_mismatch`; refusing in the compiler was **implemented and reverted**,
    because it moves the failure earlier than the authoritative verifier and 21 recorded drafts
    take that shape; and making §13.7 vacuous for explanatory sentences is the weakening the
    brief forbids. The expressive fix — `rests_on` on `DraftSentence`, so a paraphrase-rest is
    visible to §13.7 — re-keys `draft_content_sha256` and needs a live explanatory candidate to
    test against, and no package in the corpus carries an explanatory passage at all.

    **Unreachable in this phase**, which is why it is deferred rather than forced:
    `normalize_templates` authors `rests_on` and authors it empty.
    """
    forwards = _verify(_reports(0), _rested_on(1, "P1"))
    backwards = _verify(_rested_on(0, "P1"), _reports(1))

    assert [finding.code for finding in forwards.all_findings] == [
        "citation_reused_for_unrelated_claim"]
    assert not forwards.passed
    assert backwards.all_findings == ()
    assert backwards.passed, "the same two sentences, reordered"


def test_two_passages_are_never_the_reuse_shape() -> None:
    """The control: the finding is about the span, not about the pair of kinds.

    An explanatory sentence resting on the *other* passage passes in either order, which is what
    makes the case above a disagreement between two deterministic stages rather than a rule
    about explanatory sentences.
    """
    assert _verify(_reports(0), _rested_on(1, "P2")).passed


def test_recovery_cannot_construct_the_reuse_shape_in_this_phase() -> None:
    """Why the disagreement is deferred rather than fixed: `rests_on` is `()` on every template.

    `normalize_templates` authors `rests_on`, and it authors it empty — `03` §2 measured
    `want_explanatory_search` off in every detector, so `package.explanatory_passages` is empty
    in every run that exists and no sentence can honestly rest on a passage. `_citations_for`
    dedupes within a sentence and reads no earlier sentence's citations, so with no `rests_on`
    the *"same span, second claim"* shape is not constructible at all.
    """
    normalized = normalize_templates(
        (*WRITTEN, "The filing describes the measure."), ROWS)
    assert all(template.rests_on == () for template in normalized.templates)
    assert all(template.kind is not SentenceKind.EXPLANATORY
               for template in normalized.templates)


# -- the seam ----------------------------------------------------------------------------------


def test_the_index_is_positional_and_never_the_models() -> None:
    """`Draft` requires `0..n-1` in order, so the caller assigns them and the model does not."""
    normalized = normalize_templates(("One.", "Two.", "Three."), ROWS)
    assert [template.index for template in normalized.templates] == [0, 1, 2]


def test_recovery_raises_nothing_on_braces_the_grammar_cannot_read() -> None:
    """Malformed braces stay `compile_draft`'s `template_not_compilable`, and one authority owns
    the slot grammar. Recovery passes the sentence through with its braces intact."""
    text = "{{F1 was the figure."
    normalized = normalize_templates((text,), ROWS)
    assert normalized.templates[0].text == text
    assert normalized.hand_written == (0,)


def test_the_diagnostics_render_to_plain_json() -> None:
    """`story/pipeline.py` writes this into `composition.json`, so it has to survive `json.dumps`.

    The audit value of the artifact is that a reader can tell what the model wrote from what code
    recovered, which needs the spans into the **model's own sentence** — not into the template
    and not into the compiled draft, both of which `SlotFill` already covers.
    """
    normalized = normalize_templates(WRITTEN, ROWS)
    rendered = json.loads(json.dumps(normalized.as_json()))
    assert rendered["ambiguous"] == []
    assert rendered["hand_written"] == []
    first = rendered["recovered"][0]
    assert first == {"sentence_index": 0, "handle": "F1", "field": "", "literal": "$556 million",
                     "char_start": 0, "char_end": 12, "also_offered_by": []}
    assert WRITTEN[0][first["char_start"]:first["char_end"]] == first["literal"]


def test_recover_sentence_answers_for_one_sentence_at_a_time() -> None:
    """The per-sentence entry point, which is what `normalize_templates` concatenates."""
    one = recover_sentence(7, WRITTEN[0], ROWS)
    assert one.template.index == 7
    assert one.template.text == "{{F1}} {{F1.metric}} in {{F1.period}}."
    assert not one.hand_written
    assert {slot.sentence_index for slot in one.recovered} == {7}


def test_a_row_offering_nothing_anchors_nothing() -> None:
    """A row with no value offer is skipped rather than matched on the empty string.

    `occurrences` returns `()` for an empty needle, and `SlotRow.offers` omits a field the row
    has no legal value for rather than carrying `""` — but a recovery pass that relied on the
    second would break the moment a row carried an empty string for another reason.
    """
    rows = (SlotRow(handle="F1", fact_id="obs:x", kind=ROWS[0].kind, offers={}),)
    normalized = normalize_templates(("Nothing here.",), rows)
    assert normalized.recovered == ()
    assert normalized.templates[0].text == "Nothing here."
