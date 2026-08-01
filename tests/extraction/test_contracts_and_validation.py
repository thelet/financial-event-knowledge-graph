"""Contracts, identities, assembly and validation.

Protocol conformance is tested by driving objects *through* the protocol. An `isinstance`
assertion against a runtime-checkable Protocol verifies method presence and proves almost
nothing, so it is not used as the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from extraction.contracts import ClaimLane, PassageSource
from extraction.core import identifiers
from extraction.core.assembly import assemble, deferred_metric_ids, to_claim
from extraction.core.models import (
    AMBIGUOUS_ALIAS,
    EXACT_ALIAS,
    CandidatePassage,
    LaneAbstention,
    LaneClaim,
    LaneResult,
    PeriodRef,
    ScaleDeclaration,
)
from extraction.core.validation import (
    DUPLICATE_OBSERVATION_CONFLICT,
    EVIDENCE_UNRESOLVED,
    FORBIDDEN_SOURCE_LANE,
    validate,
)
from ontology import load_ontology

PACKAGE = Path(__file__).resolve().parents[2] / "extraction"


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


class InMemoryPassages:
    """A `PassageSource` with no corpus behind it."""

    def __init__(self, rows: dict[str, str]):
        self._rows = rows

    def text_of(self, passage_id): return self._rows.get(passage_id)
    def exists(self, passage_id): return passage_id in self._rows
    def document_of(self, passage_id):
        return passage_id.split("#")[0] if passage_id in self._rows else None


PASSAGE_ID = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p14"
DOCUMENT_ID = PASSAGE_ID.split("#")[0]


def make_lane_claim(**overrides) -> LaneClaim:
    base = dict(
        metric_id="homes_sold", value=2946, unit="homes",
        period=PeriodRef(period_start="2025-01-01", period_end="2025-03-31"),
        source_lane="normalized_table", passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
        raw_text="2,946",
    )
    base.update(overrides)
    return LaneClaim(**base)


# -- deterministic identities ---------------------------------------------------------------


def test_observation_id_is_readable_and_reproducible():
    first = identifiers.observation_id(
        "homes_sold", "opendoor", "2025Q1", "normalized_table", PASSAGE_ID)
    second = identifiers.observation_id(
        "homes_sold", "opendoor", "2025Q1", "normalized_table", PASSAGE_ID)
    assert first == second
    assert first.startswith("obs:homes-sold:opendoor:2025Q1:normalized-table:")


def test_the_passage_digest_keeps_two_filings_of_one_fact_distinct():
    """A letter rounds to $38 million what the reconciliation states as 38,228 thousand.
    An id built only from (metric, subject, period) collapses them and silently discards
    whichever arrived second - and with it the restatement question §16.5 defers."""
    letter = identifiers.observation_id(
        "adjusted_gross_profit", "opendoor", "2020Q4", "normalized_narrative",
        "norm:x:y:letter.htm#p10")
    table = identifiers.observation_id(
        "adjusted_gross_profit", "opendoor", "2020Q4", "normalized_table",
        "norm:x:y:release.htm#p27")
    assert letter != table


def test_digest_parts_cannot_collide_by_concatenation():
    assert identifiers.digest("a", "bc") != identifiers.digest("ab", "c")


# -- protocol conformance, driven through the protocol ---------------------------------------


def test_a_lane_is_usable_through_the_claim_lane_protocol():
    class StubLane:
        name = "stub"
        version = "1.0.0"

        def supports(self, candidate): return candidate.passage_kind == "table"

        def extract(self, candidate, text):
            return LaneResult(passage_id=candidate.passage_id, lane=self.name,
                              claims=(make_lane_claim(),))

    lane: ClaimLane = StubLane()
    candidate = CandidatePassage(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, document_type="earnings_release",
        passage_kind="table", lane="tables", reason=EXACT_ALIAS)
    assert lane.supports(candidate)
    result = lane.extract(candidate, "2,946")
    assert result.claims[0].metric_id == "homes_sold"


def test_a_lane_returns_a_result_even_when_it_reads_nothing():
    """Silence and abstention are different answers, and 24 benchmark cases expect the
    second."""
    result = LaneResult(
        passage_id=PASSAGE_ID, lane="tables",
        abstentions=(LaneAbstention(
            reason=AMBIGUOUS_ALIAS, detail="bare 'gross profit'", passage_id=PASSAGE_ID,
            candidate_metric_ids=("gaap_gross_profit", "adjusted_gross_profit")),))
    assert result.claims == ()
    assert result.abstentions[0].reason == AMBIGUOUS_ALIAS


def test_passage_source_port_is_usable_without_a_corpus():
    source: PassageSource = InMemoryPassages({PASSAGE_ID: "Homes sold 2,946"})
    assert source.exists(PASSAGE_ID)
    assert "2,946" in source.text_of(PASSAGE_ID)
    assert not source.exists("norm:nope#p1")


# -- assembly --------------------------------------------------------------------------------


def test_deferred_metrics_come_from_the_ontology_not_a_hard_coded_list(ontology):
    """Adding an XBRL lane later must need no edit here, and a vocabulary change must not
    be able to silently disagree with the code."""
    deferred = deferred_metric_ids(ontology)
    assert {"revenue", "gaap_gross_profit", "cost_of_revenue", "inventory_balance",
            "inventory_valuation_adjustment", "homes_under_resale_contract"} == set(deferred)
    # xbrl as a *fallback* preference keeps a metric in scope.
    assert "market_count" not in deferred
    assert "borrowing_capacity" not in deferred


def test_a_deferred_metric_is_recorded_rather_than_dropped(ontology):
    result = assemble([make_lane_claim(metric_id="revenue", value=1153, unit="usd")],
                      ontology=ontology)
    assert result.claims == []
    assert len(result.deferred) == 1
    entry = result.deferred[0]
    assert (entry.metric_id, entry.status, entry.reason, entry.required_lane) == (
        "revenue", "deferred", "UNAVAILABLE_REQUIRED_SOURCE_LANE", "xbrl")


def test_assembly_carries_the_population_wording_verbatim(ontology):
    wording = 'Percentage of homes “on the market” for greater than 120 days (at period end)'
    claim = to_claim(make_lane_claim(
        metric_id="pct_homes_on_market_gt_120_days", value=27, unit="percent",
        period=PeriodRef(instant_date="2025-03-31"),
        population_definition_raw=wording))
    assert claim.metric_observation.population.definition_raw == wording


def test_assembly_records_scale_and_labels_in_extractor_metadata(ontology):
    claim = to_claim(make_lane_claim(
        metric_id="contribution_profit", value=54_000_000, unit="usd", currency="USD",
        row_label="Contribution Profit", column_label="March 31, 2025",
        scale=ScaleDeclaration(scale="millions", location="preceding_context")))
    metadata = claim.extractor_metadata
    assert metadata["scale"] == "millions"
    assert metadata["scale_location"] == "preceding_context"
    assert metadata["column_label"] == "March 31, 2025"


def test_assembly_never_emits_a_supersedes_edge(ontology):
    """A per-passage extractor cannot see the other filing. §16.5 defers this to the graph
    layer, so both observations are emitted and kept distinct instead."""
    restated = make_lane_claim(passage_id="norm:a:b:tenk.htm#p9", document_id="norm:a:b:tenk.htm")
    original = make_lane_claim(passage_id="norm:a:b:tenq.htm#p4", document_id="norm:a:b:tenq.htm")
    result = assemble([restated, original], ontology=ontology)
    assert len(result.claims) == 2
    ids = {c.metric_observation.observation_id for c in result.claims}
    assert len(ids) == 2
    assert all("supersedes" not in str(c.extractor_metadata).lower() for c in result.claims)


# -- validation --------------------------------------------------------------------------------


def test_a_dangling_evidence_anchor_fails_the_run(ontology):
    """The ontology cannot catch this: `evidence_types` declare field names, not grammar,
    and its own fixtures use ids that do not match the corpus."""
    claim = to_claim(make_lane_claim())
    result = validate([claim], ontology=ontology, passages=InMemoryPassages({}))
    assert not result.ok
    assert any(f.code == EVIDENCE_UNRESOLVED for f in result.errors)


def test_a_resolvable_anchor_passes(ontology):
    claim = to_claim(make_lane_claim())
    result = validate([claim], ontology=ontology,
                      passages=InMemoryPassages({PASSAGE_ID: "Homes sold | 2,946"}))
    assert result.ok, [f"{f.code}: {f.detail}" for f in result.errors]


def test_a_forbidden_source_lane_fails_the_run(ontology):
    """`homes_sold` forbids xbrl; 9 of 26 metrics forbid a lane outright."""
    claim = to_claim(make_lane_claim(source_lane="xbrl"))
    result = validate([claim], ontology=ontology,
                      passages=InMemoryPassages({PASSAGE_ID: "x"}))
    assert any(f.code == FORBIDDEN_SOURCE_LANE for f in result.errors)


def test_identical_readings_of_one_fact_do_not_conflict(ontology):
    claim = to_claim(make_lane_claim())
    twin = to_claim(make_lane_claim())
    result = validate([claim, twin], ontology=ontology,
                      passages=InMemoryPassages({PASSAGE_ID: "x"}))
    assert not any(f.code == DUPLICATE_OBSERVATION_CONFLICT for f in result.errors)


def test_one_id_carrying_two_values_fails_the_run(ontology):
    """A collision with a *different* value means the id scheme lost a distinction it was
    supposed to keep."""
    claim = to_claim(make_lane_claim(value=2946))
    conflicting = to_claim(make_lane_claim(value=9999))
    result = validate([claim, conflicting], ontology=ontology,
                      passages=InMemoryPassages({PASSAGE_ID: "x"}))
    assert any(f.code == DUPLICATE_OBSERVATION_CONFLICT for f in result.errors)


# -- architectural rules, executable ------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_contracts_stay_dependency_light():
    """No HTTP, storage, manifest, configuration or provider import belongs in the public
    contract."""
    imported = _imports(PACKAGE / "contracts.py")
    banned = {"httpx", "requests", "yaml", "json", "pathlib", "sqlite3", "openai"}
    assert not (imported & banned), imported & banned


def test_extraction_never_imports_normalization_internals():
    """It reads the corpus through the PassageSource port, not through storage classes."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        for name in _imports(path):
            if name.startswith("normalization.") and "public" not in name:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def _code_without_docstrings(path: Path) -> str:
    """Executable code only.

    Same treatment the ontology's structural tests use: "no implementation named in the
    contract" is a rule about code, and a docstring drawing the lane hierarchy to explain
    *why* the boundary exists is exactly the kind of comment this repository asks for.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_no_lane_implementation_is_named_in_the_contract():
    """Naming one in executable contract code would make the boundary decorative."""
    code = _code_without_docstrings(PACKAGE / "contracts.py")
    for name in ("DeterministicTableClaimLane", "OntologyGuidedNarrativeClaimLane",
                 "llama", "openai", "Qwen"):
        assert name not in code, f"{name} is named in executable contract code"
