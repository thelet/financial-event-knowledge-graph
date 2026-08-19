"""The UI-side code catalogue: total over the real tables, and derived from them.

The catalogue exists because `GateEntry` has no `description` and the 28 packaging codes carry
theirs as comments. Its one failure mode is rot — a code added upstream and never described, or
a description kept for a code no stage raises any more — so totality is asserted in **both**
directions for all five families, and the planner's and the writer's sets are recovered from
their own call sites rather than from a list this suite could copy wrong.

Offline. Nothing here needs a database or a model server.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

import pytest

from story.demo_ui import code_catalogue
from story.demo_ui.code_catalogue import (
    CATALOGUE,
    FAMILY_FRESHNESS,
    FAMILY_ORDER,
    FAMILY_PACKAGE_WARNING,
    FAMILY_PLANNER,
    FAMILY_VERIFICATION,
    FAMILY_WRITER,
    catalogue_payload,
    declared_codes,
    explain,
    families_of,
    for_family,
)
from story.core.models import Severity
from story.stages.freshness.freshness_report import RefusalCode
from story.stages.packaging.warning_codes import CATEGORY_OF, KIND_OF, SEVERITY_OF
from story.stages.verification.codes import GATE, GateEntry

PACKAGE = pathlib.Path(code_catalogue.__file__).resolve().parents[2]

#: What the five families held when this catalogue was written, measured 2026-08-04. Pinned so a
#: family that *shrinks* — a code deleted upstream — is as loud as one that grows.
#:
#: `package_warning` went 28 → 29 at S5 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS
#: (`required_fact_does_not_fit`, the plan's one new blocking behaviour) and 29 → 30 at S3a
#: (`concordant_readings_collapsed`, the disclosure that `facts[]` carries one row where several
#: filings state one number identically).
#:
#: `verification_gate` went 85 → 92 at TABLE_CELL_CITATIONS S5, which gave §13.7 the seven
#: checks a citation carrying an evidence handle can fail: §3.4's checks 1–5 and 7, plus
#: `evidence_cell_span_mismatch` for the span the citation pairs with the handle. Six of the
#: seven are new obligations — the verifier gained checks in the change that removed one from
#: §12.
EXPECTED_SIZES = {
    # 92 until DETERMINISTIC_FACT_TOOLS §6 added the seven `derived_*` codes plus
    # `derivation_not_offered`, which §13 raises on a draft as well as §11 on a plan. They are
    # the price of the writer no longer declaring its own arithmetic: what used to be checked as
    # `calculation_does_not_recompute` against the model's own expression is now checked against
    # a fact code computed, and a fact code computed can be misquoted in seven distinct ways.
    # 99 until H1, which added two: `derived_direction_not_stated_in_text`, the fail-closed half
    # of §6's orientation rule — a change verb outside `CHANGE_DIRECTION` made that rule abstain
    # silently, and a change stated as a level had no verb to match at all — and
    # `evidence_scope_binding_declares_a_surface`, for the two binding fields no §13 check
    # resolves against an evidence-scope fact and that `_covering_spans` was nonetheless reading.
    FAMILY_VERIFICATION: 102,
    FAMILY_PACKAGE_WARNING: 30,
    FAMILY_FRESHNESS: 8,
    # 11 until DETERMINISTIC_FACT_TOOLS §5 gave §11 `derivation_not_offered` — the plan asking
    # code for a quantity that was not on the list of derivations the prompt printed.
    FAMILY_PLANNER: 12,
    # 11 until TABLE_CELL_CITATIONS S4 added `unresolvable_evidence_handle` and
    # `evidence_handle_out_of_bounds` — the two ways a citation can fail once it is a handle
    # rather than a retyped quote. Neither quote code was removed; a table-backed fact no longer
    # reaches them and a narrative one still does. 13 → 12 at DETERMINISTIC_FACT_TOOLS §5, which
    # retired `more_than_one_calculation`: it refused a `calculation` array of two, and the
    # writer's schema has no `calculation` at all now, so nothing can raise it. A family that
    # *shrinks* is what this table exists to make loud, and this is the shrink being declared.
    FAMILY_WRITER: 12,
}


# -- totality, which is the whole point ---------------------------------------------------------


@pytest.mark.parametrize("family", FAMILY_ORDER)
def test_the_catalogue_is_total_over_the_family_it_describes(family):
    """A code with no description must fail the build. This is that failure."""
    declared = declared_codes(family)
    described = set(code_catalogue.DESCRIPTIONS[family])
    assert declared - described == set(), (
        f"{family}: undescribed codes — a UI would render the bare code")
    assert described - declared == set(), (
        f"{family}: described codes no stage declares — a stale vocabulary")


@pytest.mark.parametrize("family,size", sorted(EXPECTED_SIZES.items()))
def test_each_family_is_the_size_it_was_when_this_was_written(family, size):
    assert len(declared_codes(family)) == size
    assert len(for_family(family)) == size


def test_the_catalogue_covers_the_verification_gate_exactly():
    """Stated separately from the parametrised rule because `GATE` is the one that matters."""
    assert {code for (family, code) in CATALOGUE if family == FAMILY_VERIFICATION} == set(GATE)


def test_the_catalogue_covers_every_declared_package_warning_exactly():
    assert {code for (family, code) in CATALOGUE
            if family == FAMILY_PACKAGE_WARNING} == set(SEVERITY_OF)


def test_the_freshness_family_is_the_enum_and_not_a_copy_of_it():
    assert declared_codes(FAMILY_FRESHNESS) == {code.value for code in RefusalCode}


def violation_codes(module_name: str, class_name: str) -> set[str]:
    """Every code a generation module actually raises, read from its call sites.

    The planner and the writer declare their codes as bare `UPPER = "lower"` constants and
    export no set, so *"which codes exist"* is only answerable from the constructions. Walking
    the AST answers it without this file holding a second copy of the list.
    """
    path = PACKAGE / "story" / "stages" / "generation" / f"{module_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {
        target.id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    raised: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == class_name and node.args
                and isinstance(node.args[0], ast.Name)):
            raised.add(constants[node.args[0].id])
    return raised


@pytest.mark.parametrize(
    "family,module_name,class_name",
    [(FAMILY_PLANNER, "planner", "PlanViolation"),
     (FAMILY_WRITER, "writer", "DraftViolation")])
def test_every_code_the_stage_raises_is_described(family, module_name, class_name):
    """The rot guard for the two families with no table upstream to read."""
    raised = violation_codes(module_name, class_name)
    assert raised, "the AST scan found no violation construction; it proves nothing"
    assert raised == declared_codes(family)


# -- everything but the sentence comes from the real tables --------------------------------------


def test_severity_remedy_and_section_are_read_from_the_gate_and_never_restated():
    for code, entry in GATE.items():
        row = explain(code, FAMILY_VERIFICATION)
        assert row is not None
        assert (row.severity, row.remedy, row.section, row.blocking) == (
            entry.severity.value, entry.remedy.value, entry.section, entry.blocking)


def test_warning_severity_and_kind_are_read_from_the_packaging_tables():
    for code, severity in SEVERITY_OF.items():
        row = explain(code, FAMILY_PACKAGE_WARNING)
        assert row is not None
        assert row.severity == severity.value
        assert row.warning_kind == KIND_OF[code].value
        assert row.warning_category == CATEGORY_OF[code].value
        assert row.blocking == (severity is Severity.REFUSE)


def test_a_capability_limitation_is_rendered_as_one_and_not_as_a_warning_about_the_evidence():
    """§4 S5's reclassification, at the surface a reader actually meets.

    The three codes fire on every package ever built — no §9 tool reads `:Entity`, none returns
    a relationship, and the corpus holds zero `:EvidenceSource` nodes — so a panel that renders
    them as findings about *this* evidence is telling a reader to distrust evidence that is fine.
    Each is still in the catalogue, still described, still carrying its severity: relabelled,
    never hidden."""
    for code in ("subject_identity_not_read_from_graph", "relationships_unavailable_in_v1",
                 "evidence_sources_absent_in_v1"):
        row = explain(code, FAMILY_PACKAGE_WARNING)
        assert row is not None
        assert row.warning_category == "capability_limitation"
        assert row.description
        assert row.blocking is False


def test_the_catalogue_did_not_grow_a_field_on_the_gate_entry():
    """`story/pipeline.py:verifier_gate_digest()` hashes `GateEntry`'s fields into every demo
    manifest, so adding a description upstream would make a copy-edit mint new run identities.
    That is the reason this catalogue is a separate table, and this is the assertion of it."""
    assert [field.name for field in dataclasses.fields(GateEntry)] == [
        "code", "severity", "remedy", "section"]


# -- the descriptions themselves -----------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_every_description_is_a_sentence_and_not_the_code_spelled_out(key):
    row = CATALOGUE[key]
    assert row.description.strip() == row.description
    assert row.description.endswith(("." , "?"))
    # Longer than the code it explains, by enough to be a sentence rather than a respelling.
    assert len(row.description) >= max(24, len(row.code) + 8)
    assert len(row.description) <= 400, "a paragraph is not a description"
    # A description that is the code with its underscores removed tells a reader nothing they
    # could not read off the code itself.
    assert row.description.lower().rstrip(".") != row.code.replace("_", " ")


def test_no_description_is_reused_across_two_codes_in_one_family():
    """Two codes with one sentence means at least one of them is described wrongly."""
    for family in FAMILY_ORDER:
        descriptions = [row.description for row in for_family(family)]
        assert len(set(descriptions)) == len(descriptions)


# -- lookup --------------------------------------------------------------------------------------


def test_nine_codes_are_shared_between_two_families_and_the_lookup_says_which():
    """Measured, not assumed: a shared code means two different things at two stages.

    Six until TABLE_CELL_CITATIONS S5 gave §13.7 the two handle failures §12 already had. Both
    are deliberately one name at two stages — a reader who meets `unresolvable_evidence_handle`
    in a rejection should not have to learn that the writer and the verifier spell it
    differently — so both need their own sentence here, and the assertion below is what makes a
    copied description fail the build.

    Eight until DETERMINISTIC_FACT_TOOLS §5 and §6 both raise `derivation_not_offered`. §11
    raises it about a **plan** — the model asked for a derivation the prompt never offered, and
    nothing was computed. §13 raises it about a **draft** — a sentence bound a derived fact whose
    request was not on the offer list, which is the same name for a failure one stage later and
    with a computed value already in hand. Two stages, two sentences, as `graph_run_id_mismatch`
    already required.
    """
    shared = sorted({code for (_family, code) in CATALOGUE if len(families_of(code)) > 1})
    assert shared == [
        "citation_quote_not_in_passage", "derivation_not_offered", "event_review_flag",
        "evidence_handle_out_of_bounds", "graph_run_id_mismatch", "plan_names_another_package",
        "unresolvable_evidence_handle", "unresolvable_fact_id", "unresolvable_passage_id"]
    assert families_of("derivation_not_offered") == (FAMILY_VERIFICATION, FAMILY_PLANNER)
    assert (explain("derivation_not_offered", FAMILY_PLANNER).description
            != explain("derivation_not_offered", FAMILY_VERIFICATION).description)
    # `graph_run_id_mismatch` is the sharpest: §7 raises it about the loaded database and
    # §13.13 raises it about a package, and the two want different sentences.
    assert families_of("graph_run_id_mismatch") == (FAMILY_VERIFICATION, FAMILY_FRESHNESS)
    assert (explain("graph_run_id_mismatch", FAMILY_FRESHNESS).description
            != explain("graph_run_id_mismatch", FAMILY_VERIFICATION).description)
    for code in ("unresolvable_evidence_handle", "evidence_handle_out_of_bounds"):
        assert families_of(code) == (FAMILY_VERIFICATION, FAMILY_WRITER)
        assert (explain(code, FAMILY_WRITER).description
                != explain(code, FAMILY_VERIFICATION).description)


def test_a_bare_lookup_resolves_in_precedence_order_and_names_the_family():
    assert explain("unbound_numeral").family == FAMILY_VERIFICATION
    assert explain("citation_quote_not_in_passage").family == FAMILY_VERIFICATION
    assert explain("thesis_empty").family == FAMILY_PLANNER
    assert explain("no_sentences").family == FAMILY_WRITER


def test_an_unknown_code_answers_none_rather_than_inventing_a_sentence():
    assert explain("no_such_code") is None
    assert explain("unbound_numeral", FAMILY_WRITER) is None


def test_an_unknown_family_refuses():
    with pytest.raises(KeyError):
        declared_codes("not_a_family")


# -- the response body ---------------------------------------------------------------------------


def test_the_payload_is_plain_json_and_holds_every_code():
    payload = catalogue_payload()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped == payload
    assert [family["family"] for family in payload["families"]] == list(FAMILY_ORDER)
    assert sum(len(family["codes"]) for family in payload["families"]) == len(CATALOGUE)
    for family in payload["families"]:
        codes = [row["code"] for row in family["codes"]]
        assert codes == sorted(codes), "a rendered table must be stable between runs"
        assert family["description"].strip()
