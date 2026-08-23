"""S3 — the deterministic draft compiler: what a row offers, and what a template may do with it.

`docs/2026-08-23-deterministic-draft-compiler/`. Three claims are load-bearing and everything
else here supports them.

* **`test_a_compiled_draft_survives_the_deterministic_verifier_end_to_end`** — the demo
  candidate's own package and plan, the real derivation stage in the middle, hand-written
  templates in, and `DeterministicVerifier` finding *nothing*: not one REFUSE, not one WARN.
  The compiler has no caller in the pipeline yet, so this test is the only place the two halves
  meet.
* **`test_no_compiled_draft_can_violate_the_numeral_or_surface_checks`** — every template
  fixture in this module driven through the verifier, asserting an **empty** finding list for
  the eleven codes §5.2 of the architecture document claims become structurally unreachable on
  this path. A tautology asserted is a tautology that stays true; if the compiler ever writes a
  span it did not insert, this is what says so.
* **`test_the_reversed_comparison_is_still_refused`** — the compiler fills the slots and the
  model still chooses which handle goes on which side of *"lower than"*. §5.1 names this the
  highest-value refusal in the system, and the whole boundary is worthless if filling the slots
  made it unreachable.

**The fixtures are the demo candidate's own**, read from `tests/story/fixtures/story_demo/`,
because the claim *"the metadata code writes is the metadata the verifier accepts"* is only
worth making against the object the pipeline actually hands the writer. The two-period and
word-valued rows the demo does not hold are hand-built here, from `story.core.models` directly:
the slot table takes trusted rows and does not care which stage minted them.

Ground truth for the demo, restated so a failure is readable: `adjusted_gross_margin` 2022Q3 is
**3.3 percent**, `gaap_gross_margin` 2022Q3 is **-12.6 percent**, and the derived `compare_levels`
row runs **from** the adjusted margin **to** the GAAP one with `display_semantics` *"lower
than"*. So the true sentence is *"GAAP gross margin was 15.9 percentage points lower than
adjusted gross margin"* and its mirror image is a lie.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.evidence_slice import passages_backing_facts
from story.core.models import (
    DerivationOperation,
    DerivedFact,
    DisplaySemantics,
    EditorialPlan,
    EvidenceScopeFact,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    SentenceKind,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.stages.composition import (
    FIELD_NOT_OFFERED_BY_ROW,
    NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
    NO_LEGAL_RENDERING,
    PASSAGE_HANDLE_UNKNOWN,
    RESTS_ON_WITHOUT_EXPLANATORY_KIND,
    SLOT_WITHOUT_BINDING,
    TEMPLATE_NOT_COMPILABLE,
    UNKNOWN_SLOT_FIELD,
    UNKNOWN_SLOT_HANDLE,
    CompositionRefused,
    SentenceTemplate,
    SlotKind,
    compile_draft,
    slot_table,
)
# `story.stages.composition.slot_table` is shadowed by the re-exported function of the same
# name, exactly as `story.stages.derivation.offers` is; the submodule's constants are reached
# through a `from … import`, which is unambiguous. The stage's `__init__` records the collision.
from story.stages.composition.slot_table import (
    SAME_PERIOD_OPERATIONS as COMPOSITION_SAME_PERIOD_OPERATIONS,
    TWO_PERIOD_OPERATIONS as COMPOSITION_TWO_PERIOD_OPERATIONS,
)
from story.stages.derivation.execute import execute_all
from story.stages.derivation.offers import (
    OFFERABLE_OPERATIONS,
    SAME_PERIOD_OPERATIONS as DERIVATION_SAME_PERIOD_OPERATIONS,
    TWO_PERIOD_OPERATIONS as DERIVATION_TWO_PERIOD_OPERATIONS,
    offers,
)
from story.stages.detection import detector_config
from story.stages.generation.planner import causal_language_for
from story.stages.verification.deterministic import DeterministicVerifier

from conftest import make_package

FIXTURES = Path(__file__).parent / "fixtures" / "story_demo"

#: The demo candidate's package and candidate, as `tests/story/test_story_demo.py` replays them.
DEMO_PACKAGE = StoryEvidencePackage.model_validate_json(
    (FIXTURES / "evidence_package.json").read_text(encoding="utf-8"))
DEMO_CANDIDATE = StoryCandidate.model_validate_json(
    (FIXTURES / "candidate.json").read_text(encoding="utf-8"))

AGM_ID = "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:3eabe78a6d25"
GGM_ID = "obs:gaap-gross-margin:opendoor:2022Q3:normalized-table:5fde8a274bdc"
AGM_HANDLE = "ev:norm:0001801169:0001801169-22-000108:open-20220930.htm#p139:r11c2"
GGM_HANDLE = "ev:norm:0001801169:0001801169-22-000108:open-20220930.htm#p139:r5c2"


def _recorded_plan() -> EditorialPlan:
    """The planner answer the committed replay store holds, as an `EditorialPlan`.

    Read from the store rather than hand-written, because the plan is one of the four inputs
    `DeterministicVerifier.verify` judges against and a plan invented here would be judging the
    compiler against a story nobody asked for. The two identity fields are the package's, exactly
    as `planner.plan_from` fills them: a model that returned a different candidate id could not
    re-key the artifact.
    """
    for line in (FIXTURES / "local_openai_compatible" / "generations.jsonl").read_text(
            encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["schema_name"] == "story_editorial_plan":
            return EditorialPlan.model_validate({
                **json.loads(row["raw_content"]),
                "candidate_id": DEMO_PACKAGE.candidate_id,
                "package_id": DEMO_PACKAGE.package_id,
            })
    raise AssertionError("the committed store holds no planner row")


DEMO_PLAN = _recorded_plan()


def _demo_derived() -> tuple[DerivedFact | EvidenceScopeFact, ...]:
    """This run's derived facts, from the **real** derivation stage over the recorded plan.

    A test may import any stage, and driving the real one is the point: a hand-built
    `DerivedFact` would let the compiler agree with a row the derivation stage would never mint,
    which is the disagreement between two stages this whole arrangement exists to avoid.
    """
    result = execute_all(
        DEMO_PLAN.requested_derivations, DEMO_PACKAGE, DEMO_CANDIDATE,
        direction=detector_config.quantity_direction,
        causal_language=causal_language_for(DEMO_PACKAGE),
        causal_marker_fact_ids=(),
        offered=offers(DEMO_PACKAGE, DEMO_CANDIDATE))
    assert result.refusals == (), [r.code for r in result.refusals]
    return (*result.facts, *result.evidence_scope_facts)


DEMO_DERIVED = _demo_derived()
DEMO_PASSAGES = passages_backing_facts(DEMO_PACKAGE)


def compile_demo(*templates: SentenceTemplate, **kwargs):
    return compile_draft(templates, DEMO_PACKAGE, DEMO_PLAN,
                         derived_facts=DEMO_DERIVED, passages=DEMO_PASSAGES, **kwargs)


def verify_demo(draft):
    return DeterministicVerifier().verify(
        draft, DEMO_PACKAGE, DEMO_PLAN, derived_facts=DEMO_DERIVED)


# -- hand-built rows the demo does not hold ----------------------------------------------------


def two_period_package() -> StoryEvidencePackage:
    """One metric, two quarters, one table — the shape a two-period derivation is offered over.

    Built from `conftest.make_package` rather than from the corpus because the demo candidate is
    a *cross-metric* comparison in one period and holds no two-period row at all. What is
    exercised here is which period slots a row offers, which is a question about the operation
    and not about the filing.
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
            value=value,
            unit="USD",
            currency="USD",
            scale="millions",
            source_lane="normalized_table",
            validation_state="ok",
            passage_id="psg:1",
            document_id="doc:1",
            quoted_text=quote,
        )
        for period, start, end, value, quote in (
            ("2022Q2", "2022-04-01", "2022-06-30", 56_000_000.0, "56"),
            ("2022Q3", "2022-07-01", "2022-09-30", -211_000_000.0, "(211)"),
        ))
    return make_package(
        facts=facts,
        metrics=(PackagedMetric(metric_id="adjusted_ebitda", label="Adjusted EBITDA",
                                unit="USD"),),
        primary_passages=(PackagedPassage(
            passage_id="psg:1", document_id="doc:1",
            text="Adjusted EBITDA 56 (211)", char_count=24,
            role=make_package().primary_passages[0].role),),
    )


TWO_PERIOD_PACKAGE = two_period_package()


def derived_row(**overrides) -> DerivedFact:
    """A `DerivedFact` over `TWO_PERIOD_PACKAGE`'s two readings, with the fields a test names."""
    fields = dict(
        fact_id="fact:derived:absolute-change:opendoor:adjusted-ebitda:2022Q3:0123456789ab",
        operation=DerivationOperation.ABSOLUTE_CHANGE,
        package_id=TWO_PERIOD_PACKAGE.package_id,
        from_fact_id="obs:adjusted-ebitda:2022Q2",
        to_fact_id="obs:adjusted-ebitda:2022Q3",
        from_period="2022Q2",
        to_period="2022Q3",
        from_value=56_000_000.0,
        to_value=-211_000_000.0,
        result=-267_000_000.0,
        unit="USD",
        currency="USD",
        display_semantics=DisplaySemantics.DECREASED_BY,
        metric_id="adjusted_ebitda",
        from_metric_id="adjusted_ebitda",
        period_surface_hint="the third quarter of 2022",
        tool_version="1.0.0",
    )
    fields.update(overrides)
    return DerivedFact(**fields)


# ---------------------------------------------------------------------------------------
# The slot table
# ---------------------------------------------------------------------------------------


def test_handles_are_assigned_positionally_over_the_three_row_sources():
    """`F` over the package's facts, `D` over this run's derived facts, `P` over the slice.

    One-based and gapless, so that two builds of one run assign the same handles and a stored
    `composition.json` reads back against a rebuilt table.
    """
    rows = slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)

    assert [(row.handle, row.kind, row.fact_id) for row in rows] == [
        ("F1", SlotKind.OBSERVED, AGM_ID),
        ("F2", SlotKind.OBSERVED, GGM_ID),
        ("D1", SlotKind.DERIVED, DEMO_DERIVED[0].fact_id),
        ("P1", SlotKind.PASSAGE, DEMO_PASSAGES[0].passage_id),
    ]


def test_an_evidence_scope_fact_gets_no_handle_and_does_not_consume_one():
    """§7's row is not bindable, and skipping it must not leave a gap in the `D` numbering.

    `writer._bindable_ids` excludes it for the reason a handle would create: a slot resolvable
    here and a binding unresolvable at §13.1. The demo run mints one, so this is the real
    sequence and not a constructed one.
    """
    scope = [row for row in DEMO_DERIVED if isinstance(row, EvidenceScopeFact)]
    assert scope, "the demo run is expected to mint an evidence-scope fact"

    handles = [row.handle for row in slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)
               if row.kind is SlotKind.DERIVED]

    assert handles == ["D1"]
    assert all(row.fact_id != scope[0].fact_id
               for row in slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES))


def test_a_row_omits_a_field_it_has_no_legal_value_for_rather_than_offering_an_empty_one():
    """R3 is a refusal and not a check, and this is the property that makes it one.

    `conftest.make_package` carries no `metrics`, so `metric_surfaces` resolves nothing for its
    one fact — the honest outcome for a package that cannot say what its own reading is of. The
    key is **absent**, so the compiler never has to decide whether `""` meant *"none"*.
    """
    row = slot_table(make_package())[0]

    assert "metric" not in row.offers
    assert set(row.offers) == {"", "period"}
    assert all(value for value in row.offers.values())


def test_a_two_metric_derivation_offers_from_and_to_and_never_a_bare_metric():
    """One name per thing: `.metric` would be a third spelling of a string `.to_metric` already
    holds, and offering a redundant spelling is offering a way to be inconsistent for no gain."""
    row = slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)[2]

    assert row.offers["from_metric"] == "Adjusted Gross Margin"
    assert row.offers["to_metric"] == "gaap gross margin"
    assert "metric" not in row.offers


def test_a_same_metric_derivation_offers_metric_and_never_from_or_to():
    """The mirror image: where `from_metric_id == metric_id` the pair collapses to one name."""
    row = slot_table(TWO_PERIOD_PACKAGE, (derived_row(),))[2]

    assert row.offers["metric"] == "Adjusted EBITDA"
    assert "from_metric" not in row.offers and "to_metric" not in row.offers


def test_a_two_period_derivation_offers_the_two_windows_and_never_a_bare_period():
    """A change across two quarters can name neither of them with one surface, so it names both
    — and `.period` is absent because there is no single window the claim is about."""
    row = slot_table(TWO_PERIOD_PACKAGE, (derived_row(),))[2]

    assert row.offers["from_period"] == "the second quarter of 2022"
    assert row.offers["to_period"] == "the third quarter of 2022"
    assert "period" not in row.offers


def test_a_same_period_derivation_offers_one_period_and_never_the_pair():
    """`compare_levels` compares two readings of one window, so `.from_period` and `.to_period`
    would be two names for one string."""
    row = slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)[2]

    assert row.offers["period"] == "the third quarter of 2022"
    assert "from_period" not in row.offers and "to_period" not in row.offers


def test_the_period_split_agrees_with_the_derivation_stage():
    """The checked copy, checked.

    `story/stages/composition/` may import `story.core.*` only, so the two operation sets that
    decide which period slots a derived row offers are restated in `slot_table.py` rather than
    imported from `story/stages/derivation/offers.py`. This is what keeps the copy honest, on
    the precedent `renderings.NON_NUMERIC_DERIVED_UNITS` set — and it drives the sets through
    the property that matters, not just their equality: every offerable operation falls in
    exactly one of them, so no row can be built that offers neither shape of period.
    """
    assert COMPOSITION_TWO_PERIOD_OPERATIONS == DERIVATION_TWO_PERIOD_OPERATIONS
    assert COMPOSITION_SAME_PERIOD_OPERATIONS == DERIVATION_SAME_PERIOD_OPERATIONS
    for operation in OFFERABLE_OPERATIONS:
        assert ((operation in COMPOSITION_TWO_PERIOD_OPERATIONS)
                != (operation in COMPOSITION_SAME_PERIOD_OPERATIONS)), operation


def test_the_offered_period_is_the_surface_the_derivation_stage_itself_minted():
    """Two routes to one string, and they agree on the run the pipeline performs.

    The row's `.period` is read off the **to** observation's own endpoints, because §13.4
    resolves a surface to endpoints and `DerivedFact.to_period` is a period *key*.
    `execute` fills `period_surface_hint` from `to_point.period` by a different route. A
    disagreement would mean the compiler declares one window and the derivation artifact records
    another, which is exactly the two-emitter fault `story/core/renderings.py` exists to end.
    """
    row = slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)[2]

    assert row.offers["period"] == DEMO_DERIVED[0].period_surface_hint


def test_a_word_valued_derivation_offers_no_value_slot_at_all():
    """`crossed_zero` answers a word, and §13.2 refuses a numeral written for one.

    The row still offers its direction and its two periods — it can be talked about, it cannot
    be counted — which is why this is an absent key rather than an absent row.
    """
    row = slot_table(TWO_PERIOD_PACKAGE, (derived_row(
        operation=DerivationOperation.CROSSED_ZERO, result=None, result_word="crossed zero",
        unit="boolean", currency=None,
        display_semantics=DisplaySemantics.CROSSED_ZERO),))[2]

    assert "" not in row.offers
    assert row.offers["direction"] == "crossed zero"
    assert row.offers["to_period"] == "the third quarter of 2022"


def test_a_derivation_whose_direction_is_unverifiable_offers_no_direction():
    """`DIRECTION_UNVERIFIABLE` is a statement about the measurement, not a phrase a sentence can
    carry: *"…was direction unverifiable by…"* is not English."""
    row = slot_table(TWO_PERIOD_PACKAGE, (derived_row(
        display_semantics=DisplaySemantics.DIRECTION_UNVERIFIABLE),))[2]

    assert "direction" not in row.offers


def test_a_passage_row_offers_no_text_slot_and_carries_the_handles_of_its_facts():
    """`P1` is only ever named in `rests_on`; what it holds is what an explanatory sentence may
    cite, in package order."""
    row = slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)[3]

    assert row.offers == {}
    assert row.evidence_handles == (AGM_HANDLE, GGM_HANDLE)


def test_a_derived_row_carries_its_two_inputs_handles_and_never_one_of_its_own():
    """§6: no handle is ever minted for a derived fact, so a sentence stating one cites the two
    observations it was computed from — `(from, to)`, the order the derivation reads them in."""
    row = slot_table(DEMO_PACKAGE, DEMO_DERIVED, DEMO_PASSAGES)[2]

    assert row.evidence_handles == (AGM_HANDLE, GGM_HANDLE)


# ---------------------------------------------------------------------------------------
# Substitution: the span is written, never found
# ---------------------------------------------------------------------------------------


def test_a_repeated_slot_mints_two_bindings_with_two_unambiguous_spans():
    """R2, in the case that makes it worth having.

    The writer path has to refuse a rendering occurring twice in its own sentence
    (`binding_rendering_ambiguous_in_sentence`), because there is no ground for choosing between
    the occurrences. The compiler knows where it wrote each one, so the same sentence is legal
    and both spans are exact.
    """
    compiled = compile_demo(SentenceTemplate(
        0, "The margin of {{F1}} in {{F1.period}} — {{F1}} — was reported.",
        SentenceKind.REPORTED))
    sentence = compiled.draft.sentences[0]

    assert len(sentence.fact_bindings) == 2
    spans = [(b.char_start, b.char_end) for b in sentence.fact_bindings]
    assert spans[0] != spans[1]
    for binding in sentence.fact_bindings:
        assert sentence.text[binding.char_start:binding.char_end] == binding.rendered


def test_multi_byte_characters_shift_a_span_by_one_each_and_not_by_their_bytes():
    """Offsets are code points, which is what every `char_start` in this repository means.

    An em dash is one character of `str` and three bytes of UTF-8, and §13.1 reads a span back as
    `text[char_start:char_end]`. Two templates differing only in whether their punctuation is
    ASCII must therefore put the following slot at the **same** offset — a byte-oriented count
    would be six adrift here and further in a sentence carrying an accented name.
    """
    ascii_ = compile_demo(SentenceTemplate(
        0, "Margin -- - {{F1}} in {{F1.period}}.", SentenceKind.REPORTED))
    wide = compile_demo(SentenceTemplate(
        0, "Margin —— – {{F1}} in {{F1.period}}.", SentenceKind.REPORTED))

    wide_text = wide.draft.sentences[0].text
    wide_binding = wide.draft.sentences[0].fact_bindings[0]
    assert len(wide_text.encode("utf-8")) == len(wide_text) + 6  # three multi-byte characters
    assert wide_binding.char_start == ascii_.draft.sentences[0].fact_bindings[0].char_start
    assert wide_text[wide_binding.char_start:wide_binding.char_end] == wide_binding.rendered


def test_no_compiled_sentence_carries_a_brace():
    """Nothing the grammar could not read survives into the post: a slot is filled or refused."""
    compiled = compile_demo(
        SentenceTemplate(0, "The {{F2.metric}} was {{F2}} in {{F2.period}}.",
                         SentenceKind.REPORTED),
        SentenceTemplate(1, "It is a plain sentence with no slot at all.",
                         SentenceKind.CONNECTIVE))

    for sentence in compiled.draft.sentences:
        assert "{{" not in sentence.text and "}}" not in sentence.text


def test_the_slot_fills_record_which_slot_became_which_span():
    """`composition.json`'s provenance: every substitution, in the order it was written."""
    compiled = compile_demo(SentenceTemplate(
        0, "The {{F2.metric}} was {{F2}} in {{F2.period}}.", SentenceKind.REPORTED))
    text = compiled.draft.sentences[0].text

    assert [(fill.handle, fill.field) for fill in compiled.slots] == [
        ("F2", "metric"), ("F2", ""), ("F2", "period")]
    for fill in compiled.slots:
        assert fill.sentence_index == 0
        assert text[fill.char_start:fill.char_end] == fill.inserted


# ---------------------------------------------------------------------------------------
# Bindings and citations
# ---------------------------------------------------------------------------------------


def test_a_derived_binding_declares_the_to_metric_and_the_to_period():
    """Both halves read out of `story/stages/verification/deterministic.py` on 2026-08-23.

    `_derived_metric_findings` accepts a surface resolving to either `metric_id` or
    `from_metric_id`, so both would pass — the `to` metric is declared because that is the metric
    the claim is about. `_derived_period_findings` resolves the surface against
    `index.fact(derived.to_fact_id)`'s window, so there `to_period` is the only surface admitted
    and the choice is not a preference.
    """
    compiled = compile_demo(SentenceTemplate(
        0, "The {{D1.to_metric}} was {{D1}} {{D1.direction}} the {{D1.from_metric}} in "
           "{{D1.period}}.", SentenceKind.CALCULATED))
    binding = compiled.draft.sentences[0].fact_bindings[0]

    assert binding.fact_id == DEMO_DERIVED[0].fact_id
    assert binding.metric_surface == "gaap gross margin"       # derived.metric_id, the `to` side
    assert binding.period_surface == "the third quarter of 2022"
    assert binding.rendered == "15.9 percentage points"


def test_a_reported_sentence_cites_the_cell_each_bound_observation_was_read_from():
    compiled = compile_demo(SentenceTemplate(
        0, "The {{F1.metric}} was {{F1}} and the {{F2.metric}} was {{F2}}, both in "
           "{{F1.period}} and {{F2.period}}.", SentenceKind.REPORTED))

    assert [c.evidence_handle for c in compiled.draft.sentences[0].citations] == [
        AGM_HANDLE, GGM_HANDLE]


def test_a_calculated_sentence_cites_the_two_inputs_and_never_the_derived_fact():
    """§6 mints no handle for a derivation, so the citations are its inputs' — and a sentence
    that also binds one of those inputs directly does not cite that cell twice."""
    compiled = compile_demo(SentenceTemplate(
        0, "The {{D1.to_metric}} of {{F2}} was {{D1}} {{D1.direction}} the {{D1.from_metric}} "
           "in {{D1.period}} and {{F2.period}}.", SentenceKind.CALCULATED))
    citations = compiled.draft.sentences[0].citations

    # The bound rows in template order: `{{F2}}` first, then `{{D1}}`, whose `from` input is the
    # adjusted margin and whose `to` input is the GAAP one already cited.
    assert [c.evidence_handle for c in citations] == [GGM_HANDLE, AGM_HANDLE]
    assert len({c.evidence_handle for c in citations}) == len(citations)


def test_a_connective_sentence_cites_nothing():
    compiled = compile_demo(SentenceTemplate(
        0, "The two margins are reported on the same table.", SentenceKind.CONNECTIVE))

    assert compiled.draft.sentences[0].citations == ()


def test_an_explanatory_sentence_walks_past_a_handle_an_earlier_sentence_already_cited():
    """The passage backs two facts; the first sentence cites one, so the second takes the other.

    R5's other half: nothing here copies a previous sentence's citation. What it reads is *which
    handles are spent*, so a second claim resting on the same passage lands on a different span
    where one is available.
    """
    compiled = compile_demo(
        SentenceTemplate(0, "The {{F1.metric}} was {{F1}} in {{F1.period}}.",
                         SentenceKind.REPORTED),
        SentenceTemplate(1, "The filing reports both margins on one table.",
                         SentenceKind.EXPLANATORY, rests_on=("P1",)))

    assert [c.evidence_handle for c in compiled.draft.sentences[0].citations] == [AGM_HANDLE]
    assert [c.evidence_handle for c in compiled.draft.sentences[1].citations] == [GGM_HANDLE]


def test_an_explanatory_sentence_falls_back_rather_than_leaving_a_claim_uncited():
    """Every handle of the passage is spent, and the compiler emits the first one anyway.

    Whether *this* claim may rest on *that* span is `citation_reused_for_unrelated_claim`, a
    semantic judgment §13.7 makes by reading the sentence. Refusing here would pre-empt a check
    with a rule that cannot see the prose (R7); emitting nothing would land as
    `uncited_factual_sentence`, which names the wrong fault.
    """
    compiled = compile_demo(
        SentenceTemplate(0, "The {{F1.metric}} was {{F1}} in {{F1.period}}.",
                         SentenceKind.REPORTED),
        SentenceTemplate(1, "The {{F2.metric}} was {{F2}} in {{F2.period}}.",
                         SentenceKind.REPORTED),
        SentenceTemplate(2, "The filing sets the two out together.",
                         SentenceKind.EXPLANATORY, rests_on=("P1",)))

    assert [c.evidence_handle for c in compiled.draft.sentences[2].citations] == [AGM_HANDLE]


def test_the_citation_span_is_rebased_onto_the_whole_passage_text():
    """§10.2.1 point 2, exactly as `writer._citations_from` does it: `PackagedPassage.char_start`
    is the offset of `text[0]`, and a citation the evidence panel can resolve is absolute."""
    compiled = compile_demo(SentenceTemplate(
        0, "The {{F2.metric}} was {{F2}} in {{F2.period}}.", SentenceKind.REPORTED))
    citation = compiled.draft.sentences[0].citations[0]
    passage = DEMO_PASSAGES[0]

    assert citation.passage_id == passage.passage_id
    assert citation.char_end > citation.char_start
    assert passage.text[citation.char_start - passage.char_start:
                        citation.char_end - passage.char_start] == "(12.6)"


# ---------------------------------------------------------------------------------------
# Every refusal code, reachable
# ---------------------------------------------------------------------------------------

#: One case per refusal, as `(name, code, templates, kwargs)`. A table rather than nine separate
#: functions because the claim being made is the same nine times — *this template earns this
#: code* — and because `test_every_refusal_code_is_reachable` reads the table to assert the set
#: is complete. A code nobody can raise is a catalogue entry describing a system this is not.
REFUSALS: tuple[tuple[str, str, tuple[SentenceTemplate, ...], dict], ...] = (
    (
        "a handle the table has no row for",
        UNKNOWN_SLOT_HANDLE,
        (SentenceTemplate(0, "The margin was {{F9}}.", SentenceKind.REPORTED),),
        {},
    ),
    (
        "a field name outside the grammar",
        UNKNOWN_SLOT_FIELD,
        (SentenceTemplate(0, "The {{F1.colour}} margin was {{F1}}.", SentenceKind.REPORTED),),
        {},
    ),
    (
        "a field this row has no legal value for",
        FIELD_NOT_OFFERED_BY_ROW,
        (SentenceTemplate(0, "The margin was {{D1}} {{D1.to_period}}.",
                          SentenceKind.CALCULATED),),
        {},
    ),
    (
        "a passage handle used as a text slot",
        FIELD_NOT_OFFERED_BY_ROW,
        (SentenceTemplate(0, "The filing said {{P1}}.", SentenceKind.EXPLANATORY),),
        {},
    ),
    (
        "a field slot with no binding for the same handle",
        SLOT_WITHOUT_BINDING,
        (SentenceTemplate(0, "This post is anchored to {{F1.period}}.",
                          SentenceKind.CONNECTIVE),),
        {},
    ),
    (
        "a rests_on on a sentence that is not explanatory",
        RESTS_ON_WITHOUT_EXPLANATORY_KIND,
        (SentenceTemplate(0, "The {{F1.metric}} was {{F1}} in {{F1.period}}.",
                          SentenceKind.REPORTED, rests_on=("P1",)),),
        {},
    ),
    (
        "a rests_on naming no passage row",
        PASSAGE_HANDLE_UNKNOWN,
        (SentenceTemplate(0, "The filing explains the gap.", SentenceKind.EXPLANATORY,
                          rests_on=("P7",)),),
        {},
    ),
    (
        "a brace the grammar cannot read",
        TEMPLATE_NOT_COMPILABLE,
        (SentenceTemplate(0, "The margin was {{f1}.", SentenceKind.REPORTED),),
        {},
    ),
    (
        "a slot in the title",
        TEMPLATE_NOT_COMPILABLE,
        (SentenceTemplate(0, "The {{F1.metric}} was {{F1}} in {{F1.period}}.",
                          SentenceKind.REPORTED),),
        {"title": "Opendoor {{F1}}"},
    ),
)


@pytest.mark.parametrize("name,code,templates,kwargs", REFUSALS,
                         ids=[case[0] for case in REFUSALS])
def test_the_compiler_refuses(name, code, templates, kwargs):
    with pytest.raises(CompositionRefused) as refused:
        compile_demo(*templates, **kwargs)

    assert code in refused.value.codes, refused.value.codes


def test_a_word_valued_row_bound_as_a_numeral_is_no_legal_rendering():
    """The one refusal the demo package cannot produce: it holds no `crossed_zero` row.

    Separate from the table above because it needs its own package and its own derived row, and
    folding a second `compile_draft` call into a table keyed on the demo's would have made the
    table lie about what it compiles.
    """
    row = derived_row(operation=DerivationOperation.CROSSED_ZERO, result=None,
                      result_word="crossed zero", unit="boolean", currency=None,
                      display_semantics=DisplaySemantics.CROSSED_ZERO)

    with pytest.raises(CompositionRefused) as refused:
        compile_draft(
            (SentenceTemplate(0, "Adjusted EBITDA moved {{D1}} in {{D1.to_period}}.",
                              SentenceKind.CALCULATED),),
            TWO_PERIOD_PACKAGE, EditorialPlan(candidate_id=TWO_PERIOD_PACKAGE.candidate_id,
                                              package_id=TWO_PERIOD_PACKAGE.package_id,
                                              thesis="t", why_it_matters="w"),
            derived_facts=(row,))

    assert NO_LEGAL_RENDERING in refused.value.codes, refused.value.codes


def test_a_bound_fact_the_package_minted_no_handle_for_is_refused_and_never_left_uncited():
    """R6, in the shape the invariant argues about.

    §13.7.2's `evidence_source_id` rows carry no `passage_id`, so `models._evidence_handle`
    mints nothing for them. Omitting the citation would land as `uncited_factual_sentence` — a
    finding pointing at the sentence — where the real fault is that this row is not citable at
    all.
    """
    row = make_package().facts[0].model_dump(mode="json")
    row.pop("evidence_handle")
    package = make_package(facts=(PackagedFact.model_validate(
        {**row, "passage_id": None, "evidence_source_id": "es:1"}),))
    assert package.facts[0].evidence_handle is None

    with pytest.raises(CompositionRefused) as refused:
        compile_draft(
            (SentenceTemplate(0, "Adjusted EBITDA was {{F1}} in {{F1.period}}.",
                              SentenceKind.REPORTED),),
            package, EditorialPlan(candidate_id=package.candidate_id,
                                   package_id=package.package_id,
                                   thesis="t", why_it_matters="w"))

    assert NO_EVIDENCE_HANDLE_FOR_BOUND_FACT in refused.value.codes, refused.value.codes


def test_a_binding_whose_row_offers_no_metric_surface_is_refused_at_compile_time():
    """§13.4 and §13.5 read those two fields off **every** binding, so a row that cannot supply
    one is not bindable — and `conftest.make_package` carries no `metrics` to resolve against."""
    package = make_package()

    with pytest.raises(CompositionRefused) as refused:
        compile_draft(
            (SentenceTemplate(0, "Adjusted EBITDA was {{F1}} in {{F1.period}}.",
                              SentenceKind.REPORTED),),
            package, EditorialPlan(candidate_id=package.candidate_id,
                                   package_id=package.package_id,
                                   thesis="t", why_it_matters="w"))

    assert FIELD_NOT_OFFERED_BY_ROW in refused.value.codes, refused.value.codes
    assert "metric" in str(refused.value)


def test_every_refusal_code_is_reachable():
    """The nine codes `public.py` declares, each raised by some template above.

    Kept as a roll-up rather than trusted to the individual tests, because the failure it guards
    is a code being *added* to the vocabulary with nothing able to produce it — which is a
    catalogue entry describing a system this is not.
    """
    from story.stages.composition import public

    declared = {value for name, value in vars(public).items()
                if name.isupper() and isinstance(value, str) and not name.startswith("_")}
    reached = {code for _name, code, _templates, _kwargs in REFUSALS} | {
        NO_LEGAL_RENDERING, NO_EVIDENCE_HANDLE_FOR_BOUND_FACT}

    assert declared == reached, declared ^ reached


def test_a_refusal_names_every_fault_and_not_only_the_first():
    """A caller repairing a template wants the whole list, which is `draft_from`'s arrangement."""
    with pytest.raises(CompositionRefused) as refused:
        compile_demo(
            SentenceTemplate(0, "The margin was {{F9}}.", SentenceKind.REPORTED),
            SentenceTemplate(1, "It rested on {{F1.period}}.", SentenceKind.CONNECTIVE))

    assert set(refused.value.codes) == {UNKNOWN_SLOT_HANDLE, SLOT_WITHOUT_BINDING}


# ---------------------------------------------------------------------------------------
# The two claims the architecture document makes
# ---------------------------------------------------------------------------------------

#: Every template set this module compiles cleanly against the demo package, as
#: `(name, templates)`. The eleven-code test below drives all of them; adding a fixture here
#: extends that assertion for free, which is the point of collecting them.
TEMPLATE_FIXTURES: tuple[tuple[str, tuple[SentenceTemplate, ...]], ...] = (
    (
        "the recorded accepted draft, as templates",
        (
            SentenceTemplate(0, "Opendoor reported a {{F2.metric}} of {{F2}} for "
                                "{{F2.period}}.", SentenceKind.REPORTED),
            SentenceTemplate(1, "Opendoor reported an {{F1.metric}} of {{F1}} for "
                                "{{F1.period}}.", SentenceKind.REPORTED),
            SentenceTemplate(2, "The {{D1.to_metric}} was {{D1}} {{D1.direction}} the "
                                "{{D1.from_metric}} for {{D1.period}}.",
                             SentenceKind.CALCULATED),
        ),
    ),
    (
        "one row bound twice in one sentence",
        (SentenceTemplate(0, "The {{F1.metric}} of {{F1}} in {{F1.period}} — {{F1}} — is the "
                             "adjusted reading.", SentenceKind.REPORTED),),
    ),
    (
        "a sentence carrying a multi-byte character",
        (SentenceTemplate(0, "The {{F2.metric}} — {{F2}} — was reported for "
                             "{{F2.period}}.", SentenceKind.REPORTED),),
    ),
    (
        "a connective sentence with no slot at all",
        (
            SentenceTemplate(0, "The {{F1.metric}} was {{F1}} for {{F1.period}}.",
                             SentenceKind.REPORTED),
            SentenceTemplate(1, "Both readings come from one table.", SentenceKind.CONNECTIVE),
        ),
    ),
    (
        "a derived fact bound beside one of its own inputs",
        (SentenceTemplate(0, "Against the {{F2.metric}} of {{F2}}, the gap was {{D1}} "
                             "{{D1.direction}} the {{D1.from_metric}} for {{D1.period}} and "
                             "{{F2.period}}.", SentenceKind.CALCULATED),),
    ),
)

#: The eleven codes §5.2 of the architecture document claims become structurally unreachable
#: once the compiler owns the numeral, the surfaces and the span. Named rather than derived from
#: a category, so that a check moving between families is a test edit somebody has to justify.
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


@pytest.mark.parametrize("name,templates", TEMPLATE_FIXTURES,
                         ids=[case[0] for case in TEMPLATE_FIXTURES])
def test_no_compiled_draft_can_violate_the_numeral_or_surface_checks(name, templates):
    """§5.3's insurance: a tautology asserted is a tautology that stays true.

    These eleven checks are not weakened by this stage — they stay in the verifier and stay
    reachable for a replayed artifact, a hand-written test draft and any future caller. What
    changes is that a draft *this* module built cannot raise one, because it inserted the numeral
    from `legal_renderings`, read both surfaces off the row and recorded the span it wrote into.
    The residual risk is a compiler bug, and this is what holds it: an empty finding list, per
    code, over every template fixture in this module.
    """
    verified = verify_demo(compile_demo(*templates).draft)

    offending = [(f.code, f.observed) for f in verified.all_findings
                 if f.code in UNREACHABLE_ON_THE_COMPILER_PATH]
    assert offending == []


def test_the_reversed_comparison_is_still_refused():
    """The check the whole boundary protects, and the one it must not make unreachable.

    `comparative_not_supported_by_text` fired 12 times in the 50-run corpus and 8 of those are
    one sentence written in eight separate runs: *"The Adjusted Gross Margin was 15.9 percentage
    points lower than the GAAP Gross Margin."* The number is right, the binding is right, and the
    claim is the opposite of the truth — 3.3 is 15.9 points **higher** than -12.6.

    Under the new contract the model still chooses which handle goes on which side of *"lower
    than"*, so the lie is still writable. Every field around it is now code's: the numeral, both
    metric surfaces, the period surface, the direction word and the two citations. The refusal
    below is therefore the verifier reading the **prose**, which is the division of labour §6
    says the writer keeps.
    """
    truthful = compile_demo(SentenceTemplate(
        0, "The {{D1.to_metric}} was {{D1}} {{D1.direction}} the {{D1.from_metric}} for "
           "{{D1.period}}.", SentenceKind.CALCULATED))
    reversed_ = compile_demo(SentenceTemplate(
        0, "The {{D1.from_metric}} was {{D1}} {{D1.direction}} the {{D1.to_metric}} for "
           "{{D1.period}}.", SentenceKind.CALCULATED))

    # One word of prose apart, and every machine field identical.
    assert (truthful.draft.sentences[0].fact_bindings
            == reversed_.draft.sentences[0].fact_bindings) is False  # spans move with the words
    assert ([c.evidence_handle for c in truthful.draft.sentences[0].citations]
            == [c.evidence_handle for c in reversed_.draft.sentences[0].citations])

    assert verify_demo(truthful.draft).all_findings == ()
    codes = [f.code for f in verify_demo(reversed_.draft).all_findings if f.blocking]
    assert codes == ["comparative_not_supported_by_text"]


def test_a_compiled_draft_survives_the_deterministic_verifier_end_to_end():
    """The demo candidate's own package and plan, the real derivation stage, and no finding.

    Not one REFUSE, not one WARN, not one ANNOTATE — the same bar
    `test_story_writer.py::test_the_writers_own_output_survives_the_deterministic_verifier_end_to_end`
    holds the writer to, over the object the pipeline actually builds rather than a package
    assembled for the test. The compiler has no caller in the pipeline yet (S5 wires it), so
    this is the only place the two halves meet.
    """
    templates = TEMPLATE_FIXTURES[0][1]

    compiled = compile_demo(*templates, title="Opendoor 2022Q3", model_id="qwen3.5-9b",
                            prompt_version="3.0.0")
    verified = verify_demo(compiled.draft)

    assert verified.all_findings == (), [f.code for f in verified.all_findings]
    assert verified.passed is True
    assert [entry.fact_id for entry in verified.fact_ledger] == [
        GGM_ID, AGM_ID, DEMO_DERIVED[0].fact_id]
    assert compiled.draft.sentences[2].text == (
        "The gaap gross margin was 15.9 percentage points lower than the Adjusted Gross Margin "
        "for the third quarter of 2022.")
