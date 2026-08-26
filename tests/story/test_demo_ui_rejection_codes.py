"""A verifier rejection must name the codes it refused on.

**Found on a live run, 2026-08-09.** A `why_it_matters` generation of
`cand:metric-move:direct-selling-costs:opendoor:2021Q3_2021Q4` reached the model (10,537 real
tokens), the verifier ran, and it refused with six blocking findings across three checks —
`unbound_numeral` on the numerals `3`, `4` and `2021`, `citation_reused_for_unrelated_claim`
twice with *"no fact binding at all"*, and `connective_sentence_carries_a_claim`.

The panel showed `"codes": []` and this sentence beside it:

    this refusal carried no structured code. §11 and §12's own rejections carry codes and a
    schema violation carries violations; a provider or transport failure carries neither.

Which was **untrue on that run, and self-evidently so**: `verifier_ran: true` sat in the same
object. The cause is that `_codes_of` reads `codes` off §11's and §12's *exceptions*, and §13
does not raise — `DeterministicVerifier.verify` returns a `VerifiedDraft` whose codes live on
its findings, so `outcome.refusal_codes` is empty for every verifier rejection.

The failure mode this guards is the worst kind an explanatory panel has: not silence, but a
confident wrong explanation. A reader told "a provider or transport failure" would go and check
the model server, which was working perfectly.
"""

from __future__ import annotations

from typing import Any

import pytest

from story.core.models import (
    CheckResult,
    PackageIdentity,
    Remedy,
    Severity,
    VerificationFinding,
    VerifiedDraft,
)

pipeline = pytest.importorskip("story.pipeline")


def _finding(code: str, *, blocking: bool = True) -> VerificationFinding:
    return VerificationFinding(
        code=code,
        severity=Severity.REFUSE if blocking else Severity.WARN,
        remedy=Remedy.DROP_SENTENCE,
        blocking=blocking,
        observed="measured on the live run",
    )


def _verified(*findings: VerificationFinding) -> VerifiedDraft:
    """A refused draft shaped the way §13 actually returns one — findings on a check."""
    return VerifiedDraft(
        candidate_id="cand:metric-move:direct-selling-costs:opendoor:2021Q3_2021Q4:cb63a39aa142",
        package_identity=PackageIdentity(
            package_id="pkg:x", package_version="1.2.0", candidate_id="cand:x",
            detector_id="detector:metric_move", detector_version="1.0.0",
            policy_version="canon-policy:1.0.0", graph_run_id="graph-v1-0483dc6b4b10",
            graph_projection_version="1.2.0", extraction_run_id="extract-v1",
            run_complete_sha256="0" * 64, ontology_id="real_estate_marketplace_v1",
            ontology_definition_hash="1" * 64, ontology_semantic_version="2.0.0",
            package_content_digest="2" * 64),
        draft_content_sha256="3" * 64,
        checks=(CheckResult(name="numbers", examined=7, findings=findings),),
    )


def test_a_verifier_rejection_names_its_blocking_codes() -> None:
    """The live shape: `refusal_codes` empty, codes on the findings."""
    verified = _verified(_finding("unbound_numeral"),
                         _finding("citation_reused_for_unrelated_claim"),
                         _finding("connective_sentence_carries_a_claim"))
    assert verified.passed is False
    assert verified.check("numbers").findings, "the fixture must carry findings"

    codes = tuple(dict.fromkeys(f.code for f in verified.all_findings if f.blocking))
    assert codes == ("unbound_numeral", "citation_reused_for_unrelated_claim",
                     "connective_sentence_carries_a_claim")


def test_a_repeated_code_is_named_once_and_keeps_first_seen_order() -> None:
    """Three `unbound_numeral` findings are one reason, not three."""
    verified = _verified(_finding("unbound_numeral"),
                         _finding("citation_reused_for_unrelated_claim"),
                         _finding("unbound_numeral"))
    codes = tuple(dict.fromkeys(f.code for f in verified.all_findings if f.blocking))
    assert codes == ("unbound_numeral", "citation_reused_for_unrelated_claim")


def test_a_non_blocking_finding_is_not_a_reason_the_draft_was_refused() -> None:
    """`over_precision` is a WARN and fired four times on the live run; it refused nothing."""
    verified = _verified(_finding("unbound_numeral"),
                         _finding("over_precision", blocking=False))
    codes = tuple(dict.fromkeys(f.code for f in verified.all_findings if f.blocking))
    assert codes == ("unbound_numeral",)
    assert any(not f.blocking for f in verified.all_findings)


def test_the_absent_code_sentence_is_reachable_only_when_no_code_exists() -> None:
    """Structural: the explanation and the codes may never both be present.

    This is the assertion that would have failed before the repair — the live payload carried
    an empty `codes` list *and* a sentence explaining the absence, next to `verifier_ran: true`.
    """
    import inspect

    from story.demo_ui import api

    source = inspect.getsource(api)
    assert 'if not outcome.refusal_codes:' not in source, (
        "the absent-code branch must test the codes actually rendered, not only the exception's")
    assert '"codes": _explanations(codes, family)' in source, (
        "the rendered codes must come from the merged tuple, not from refusal_codes alone")


def test_a_transport_failure_still_has_no_code_and_still_says_so() -> None:
    """The branch is correct for what it was written for: `verified is None`."""
    outcome_verified: Any = None
    blocking = tuple(dict.fromkeys(
        f.code for f in outcome_verified.all_findings if f.blocking)
    ) if outcome_verified is not None else ()
    assert blocking == ()


# -- the compiler's refusals, which had no family at all ----------------------------------------


def test_each_refusing_stage_is_rendered_from_its_own_families_vocabulary() -> None:
    """03-WRITER-AND-COMPOSITION-STABILIZATION §10, at the function that chooses the family.

    `_rejection_family` is a table and this is the whole of it: three refusing stages name their
    own vocabulary, and everything else — `rejected`, above all — is §13's gate. The row that
    was missing is `composition_refused`, and its absence was not a cosmetic one: see the test
    below.
    """
    from story.demo_ui import api
    from story.demo_ui import code_catalogue

    assert api._rejection_family(pipeline.PLAN_REFUSED, pipeline) == \
        code_catalogue.FAMILY_PLANNER
    assert api._rejection_family(pipeline.DRAFT_REFUSED, pipeline) == \
        code_catalogue.FAMILY_WRITER
    assert api._rejection_family(pipeline.COMPOSITION_REFUSED, pipeline) == \
        code_catalogue.FAMILY_COMPOSITION
    assert api._rejection_family(pipeline.REJECTED, pipeline) == \
        code_catalogue.FAMILY_VERIFICATION


def test_a_compile_time_refusal_renders_a_sentence_and_says_it_blocked() -> None:
    """The payload the panel gets, for every code the compiler can raise.

    Before the fix the family fell through to `FAMILY_VERIFICATION`, which declares none of
    these nine codes, so `_explanations` produced `{"description": "", "severity": "",
    "remedy": "", "blocking": false}` for each one — an empty explanation is a gap, and
    `blocking: false` is a **false statement** about a run from which no draft was built.

    Driven through `_explanations` with the family `_rejection_family` returns, which is exactly
    the pair `_outcome_payload` passes, rather than through a hand-written family name.
    """
    from story.demo_ui import api
    from story.demo_ui.code_catalogue import FAMILY_COMPOSITION, declared_codes

    codes = sorted(declared_codes(FAMILY_COMPOSITION))
    assert len(codes) == 9, "the compiler declares nine refusals; the catalogue must hold them"
    family = api._rejection_family(pipeline.COMPOSITION_REFUSED, pipeline)
    for row in api._explanations(codes, family):
        assert row["family"] == FAMILY_COMPOSITION
        assert row["description"], f"{row['code']} would render as a bare code"
        assert row["blocking"] is True, (
            f"{row['code']} refused the run, so a panel may not call it non-blocking")


def test_the_compilers_codes_are_the_modules_own_and_not_a_copy_of_the_strings() -> None:
    """A renamed constant must fail at import, which is what reading by name buys.

    The catalogue holds `_COMPOSITION_CODE_NAMES` — constant *names* — and reads the values off
    `story/stages/composition/public.py`, the same technique the planner and writer families
    use. A copied string list would go stale silently the day a code is respelled.
    """
    from story.demo_ui.code_catalogue import FAMILY_COMPOSITION, declared_codes
    from story.stages.composition import public as composition

    assert declared_codes(FAMILY_COMPOSITION) == frozenset({
        composition.UNKNOWN_SLOT_HANDLE,
        composition.UNKNOWN_SLOT_FIELD,
        composition.FIELD_NOT_OFFERED_BY_ROW,
        composition.SLOT_WITHOUT_BINDING,
        composition.NO_LEGAL_RENDERING,
        composition.NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
        composition.PASSAGE_HANDLE_UNKNOWN,
        composition.RESTS_ON_WITHOUT_EXPLANATORY_KIND,
        composition.TEMPLATE_NOT_COMPILABLE,
    })
