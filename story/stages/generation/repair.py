"""Who owns a failure, and what a model is told about the ones it owns.

Responsibility: the closed mapping from a refusal code to the stage that must fix it, and the
compact feedback a repair call carries. No control flow — `story/pipeline.py` owns the loop and
the bounds — and no provider, no clock, no I/O.

**Why a table and not a judgment at each call site.** A repair loop that guessed at ownership
would eventually send a model a defect code chose: 24 of the 85 findings in the recorded corpus
are that kind (`citation_reused_for_unrelated_claim`, a rendering precision, an evidence handle),
and asking a writer to repair a span the compiler picked is asking it to fix something it did not
write. The table makes ownership a value a test can be total over.

**Blind retry is not a mechanism here, and the measurement is why.** Across the stored corpus,
restricted to live-to-live repeats of an identical `request_sha256`, the local llama.cpp server
returned byte-identical answers 4 times in 4; the OpenAI adapter returned different ones 2 times
in 2, because it records `temperature: 0.0` and sends `temperature_sent: false` — the field never
reaches the wire for those models. So re-asking an unchanged prompt has no mechanism of action on
the local path and unknown odds on the other. Every function here changes the *prompt*.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping, Sequence

from story.core.models import Draft, VerificationFinding
from story.core.spine import StorySpine


class FailureOwner(str, Enum):
    """Which stage must act on a refusal — or that no model may be asked to.

    A `str` enum for `story.core.models`' reason: the value is what a manifest carries, and a
    provenance record a reader has to map back through an integer is one nobody reads.
    """

    #: The plan states something the evidence contradicts. Repairing the writer would ask it to
    #: contradict its own instructions, which is the shape `story-v1-76da8465cd95` was in.
    PLANNER = "planner"
    #: The answer could not be parsed or compiled. The model wrote it and can write it again.
    WRITER_STRUCTURAL = "writer_structural"
    #: The draft compiled and says something the evidence does not support.
    WRITER_PROSE = "writer_prose"
    #: The package does not hold what the claim needs. No wording fixes that.
    EVIDENCE = "evidence"
    #: Code chose the thing being refused. A model repair here is a category error, and the
    #: run is rejected so the defect is visible rather than papered over.
    CODE_OWNED = "code_owned"


#: Every code the story path can refuse with, and who owns it. **Total by test** —
#: `test_story_repair.py` walks `verification.codes.GATE`, the planner's constants, the writer's
#: and the compiler's, and requires each to appear exactly once.
#:
#: A code absent from this table routes to `CODE_OWNED` at lookup, which is the safe direction:
#: an unrouted refusal reaches a person rather than a model.
ROUTING: Mapping[str, FailureOwner] = {
    # -- the plan says something the measurement denies ---------------------------------------
    "direction_contradicts_spine": FailureOwner.PLANNER,
    "unresolvable_fact_handle": FailureOwner.PLANNER,
    "thesis_empty": FailureOwner.PLANNER,
    "no_key_points": FailureOwner.PLANNER,
    "counterpoint_missing": FailureOwner.PLANNER,
    "counterpoint_ungrounded": FailureOwner.PLANNER,
    "unsupported_comparative": FailureOwner.PLANNER,
    "comparative_not_supported_by_text": FailureOwner.PLANNER,
    # -- the answer would not parse or would not compile ---------------------------------------
    "no_sentences": FailureOwner.WRITER_STRUCTURAL,
    "draft_not_constructible": FailureOwner.WRITER_STRUCTURAL,
    "thesis_abandoned": FailureOwner.WRITER_STRUCTURAL,
    "malformed_slot": FailureOwner.WRITER_STRUCTURAL,
    "template_not_compilable": FailureOwner.WRITER_STRUCTURAL,
    "unknown_slot_handle": FailureOwner.WRITER_STRUCTURAL,
    "unknown_slot_field": FailureOwner.WRITER_STRUCTURAL,
    "field_not_offered_by_row": FailureOwner.WRITER_STRUCTURAL,
    "slot_without_binding": FailureOwner.WRITER_STRUCTURAL,
    "no_legal_rendering": FailureOwner.WRITER_STRUCTURAL,
    "rests_on_without_explanatory_sentence": FailureOwner.WRITER_STRUCTURAL,
    "rests_on_not_a_passage_handle": FailureOwner.WRITER_STRUCTURAL,
    "rests_on_without_explanatory_kind": FailureOwner.WRITER_STRUCTURAL,
    "passage_handle_unknown": FailureOwner.WRITER_STRUCTURAL,
    # -- the draft compiled and the prose overreaches -------------------------------------------
    "unbound_numeral": FailureOwner.WRITER_PROSE,
    "metric_surface_absent_from_text": FailureOwner.WRITER_PROSE,
    "metric_named_in_text_contradicts_binding": FailureOwner.WRITER_PROSE,
    "metric_surface_ambiguous": FailureOwner.WRITER_PROSE,
    "metric_surface_unresolved": FailureOwner.WRITER_PROSE,
    "period_surface_absent_from_text": FailureOwner.WRITER_PROSE,
    "period_named_in_text_contradicts_binding": FailureOwner.WRITER_PROSE,
    "period_mismatch": FailureOwner.WRITER_PROSE,
    "percentage_point_surface_missing": FailureOwner.WRITER_PROSE,
    "connective_sentence_carries_a_claim": FailureOwner.WRITER_PROSE,
    "forward_looking_language": FailureOwner.WRITER_PROSE,
    "unsupported_superlative": FailureOwner.WRITER_PROSE,
    "unsupported_absence_claim": FailureOwner.WRITER_PROSE,
    "unsupported_temporal_ordering": FailureOwner.WRITER_PROSE,
    "foreign_subject_named": FailureOwner.WRITER_PROSE,
    "causal_construction_forbidden": FailureOwner.WRITER_PROSE,
    "causal_attribution_frame_missing": FailureOwner.WRITER_PROSE,
    "conflict_not_disclosed": FailureOwner.WRITER_PROSE,
    "required_warning_absent": FailureOwner.WRITER_PROSE,
    "required_warning_has_no_declared_qualifier": FailureOwner.WRITER_PROSE,
    "required_counterpoint_absent": FailureOwner.WRITER_PROSE,
    "derived_direction_not_stated_in_text": FailureOwner.WRITER_PROSE,
    "derived_fact_orientation_reversed": FailureOwner.WRITER_PROSE,
    "unresolved_entity_named": FailureOwner.WRITER_PROSE,
    "reported_sentence_carries_calculation": FailureOwner.WRITER_PROSE,
    "uncited_factual_sentence": FailureOwner.WRITER_PROSE,
    # -- the package does not hold it ------------------------------------------------------------
    "fact_not_in_package": FailureOwner.EVIDENCE,
    "citation_not_in_package": FailureOwner.EVIDENCE,
    "date_not_in_package": FailureOwner.EVIDENCE,
    "unpopulated_metric": FailureOwner.EVIDENCE,
    "absence_not_provable_from_bounded_package": FailureOwner.EVIDENCE,
    "no_evidence_handle_for_bound_fact": FailureOwner.EVIDENCE,
    "package_content_digest_mismatch": FailureOwner.EVIDENCE,
    "graph_input_digest_mismatch": FailureOwner.EVIDENCE,
    "graph_run_id_mismatch": FailureOwner.EVIDENCE,
}


def owner_of(code: str) -> FailureOwner:
    """Who must act on `code`. Unknown codes are code's, which is the safe direction."""
    return ROUTING.get(code, FailureOwner.CODE_OWNED)


def owners_of(codes: Sequence[str]) -> tuple[FailureOwner, ...]:
    """The owners of a refusal, first-seen order, deduplicated."""
    return tuple(dict.fromkeys(owner_of(code) for code in codes))


def repairable_owner(codes: Sequence[str]) -> FailureOwner | None:
    """The one owner a repair may be routed to, or `None` if no repair is legitimate.

    **A refusal carrying even one code a model may not be asked about is not repaired at all.**
    Mixing them would send a writer a prompt containing a defect code chose, and the honest
    outcome for a run whose findings include an engineering error is that the engineering error
    is visible. `EVIDENCE` is the same case for a different reason: the repair could only succeed
    by inventing what the package does not hold.

    Two *repairable* owners in one refusal also returns `None`. A plan contradiction and a prose
    overreach are two different stages' work, and a loop that picked one would leave the other to
    a second round the bounds do not allow.
    """
    owners = set(owners_of(codes))
    if not owners or owners & {FailureOwner.CODE_OWNED, FailureOwner.EVIDENCE}:
        return None
    if len(owners) != 1:
        return None
    return next(iter(owners))


def planner_feedback(spine: StorySpine | None, codes: Sequence[str], detail: str) -> str:
    """What the planner is told when its plan contradicts what code measured.

    Compact by construction: the verified pair, the verified direction, and the refusal's own
    sentence. No trace, no manifest, no draft — the plan is what is being repaired, and a
    prompt carrying the run's plumbing would invite a plan about the plumbing.
    """
    lines = ["Your plan was refused before the post was written. Keep your angle; fix the claim."]
    if spine is not None and spine.direction_verified:
        lines += [
            "",
            "VERIFIED — measured from the filings, not yours to change:",
            f"  {spine.metric_id}  {spine.from_period} -> {spine.to_period}",
            f"  direction: {spine.direction}",
        ]
    lines += ["", "WHY IT WAS REFUSED:", "  " + detail, "",
              "Rewrite the thesis and any key point that states the direction. Name the same "
              "facts you named before."]
    return "\n".join(lines)


def structural_feedback(codes: Sequence[str], detail: str, offered: Mapping[str, str]) -> str:
    """What the writer is told when its answer could not be parsed or compiled.

    `offered` is every slot the run's rows print, handle to string, so the repair prompt can say
    what a correct sentence may contain rather than only what the last one did wrong.
    """
    lines = ["Your answer could not be turned into a post. The sentences were not the problem "
             "in kind — the way they name figures was.", "", "WHAT WENT WRONG:", "  " + detail]
    if offered:
        lines += ["", "WHAT THIS RUN OFFERS (write the slot, or write the string exactly):"]
        lines += [f"  {slot} -> {value!r}" for slot, value in offered.items()]
    lines += ["", "Write the post again. Every figure and every period must be one of the "
              "strings above, or the slot beside it."]
    return "\n".join(lines)


def prose_feedback(draft: Draft, findings: Sequence[VerificationFinding], limit: int = 5) -> str:
    """What the writer is told when a compiled draft says something the evidence does not support.

    **`VerificationFinding` is used as it stands and nothing is derived.** It already carries the
    code, the sentence index, the character span, the facts, what was expected, what was observed
    and an eleven-member `Remedy`; a repair payload that recomputed any of that would be a second
    opinion about a refusal the verifier already explained.

    Capped at `limit` findings, because a draft with twenty is a draft to reject rather than to
    patch, and a repair prompt longer than the original is one that has stopped being feedback.
    """
    lines = ["Your post was refused by the checker. Fix these sentences and change nothing else."]
    texts = {sentence.index: sentence.text for sentence in draft.sentences}
    shown = [finding for finding in findings if finding.blocking][:limit]
    for finding in shown:
        lines += ["", f"sentence {finding.sentence_index}: {texts.get(finding.sentence_index, '')!r}",
                  f"  problem : {finding.code}",
                  f"  expected: {finding.expected}",
                  f"  you wrote: {finding.observed}",
                  f"  fix     : {finding.remedy.value}"]
    dropped = len([f for f in findings if f.blocking]) - len(shown)
    if dropped > 0:
        lines.append(f"\n({dropped} further problem(s) not listed.)")
    return "\n".join(lines)


__all__ = [
    "ROUTING",
    "FailureOwner",
    "owner_of",
    "owners_of",
    "planner_feedback",
    "prose_feedback",
    "repairable_owner",
    "structural_feedback",
]
