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
EXPECTED_SIZES = {
    FAMILY_VERIFICATION: 85,
    FAMILY_PACKAGE_WARNING: 30,
    FAMILY_FRESHNESS: 8,
    FAMILY_PLANNER: 11,
    FAMILY_WRITER: 11,
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


def test_six_codes_are_shared_between_two_families_and_the_lookup_says_which():
    """Measured, not assumed: a shared code means two different things at two stages."""
    shared = sorted({code for (_family, code) in CATALOGUE if len(families_of(code)) > 1})
    assert shared == [
        "citation_quote_not_in_passage", "event_review_flag", "graph_run_id_mismatch",
        "plan_names_another_package", "unresolvable_fact_id", "unresolvable_passage_id"]
    # `graph_run_id_mismatch` is the sharpest: §7 raises it about the loaded database and
    # §13.13 raises it about a package, and the two want different sentences.
    assert families_of("graph_run_id_mismatch") == (FAMILY_VERIFICATION, FAMILY_FRESHNESS)
    assert (explain("graph_run_id_mismatch", FAMILY_FRESHNESS).description
            != explain("graph_run_id_mismatch", FAMILY_VERIFICATION).description)


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
