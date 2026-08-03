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

#: The corpus row `build_evidence` reads its `table_id` from. Needed since F0 Part B, which
#: began enforcing `claims.yaml`'s long-declared `normalized_table` requirement of a
#: `table_id`: `to_claim(claim)` with no passage row builds a table-lane reference that names
#: no table, which the vocabulary has always said is not a table citation.
TABLE_PASSAGE_ROW = {
    "passage_id": PASSAGE_ID,
    "document_id": DOCUMENT_ID,
    "table_id": DOCUMENT_ID + "#b3",
    "block_ids": [DOCUMENT_ID + "#b3"],
    "source_url": ("https://www.sec.gov/Archives/edgar/data/1801169/000180116925000037/"
                   "q12025formxex991earningsre.htm"),
}


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


# -- the within-passage repair ----------------------------------------------------------------
#
# The corpus had 58 ids describing two grid cells each, across 12 table passages, of which 4
# carried disagreeing values. The four tests below are the four claims the repair makes.


#: The cell pair the defect was measured on. `q32023formxex991earningsre.htm#p29` prints one
#: `Adjusted EBITDA` row under a `2023` column and a `September 30, 2023` column; before the
#: repair both minted `obs:adjusted-ebitda:opendoor:2023-01-01_2023-09-30:normalized-table:
#: 98849a208451` and the run reported them as one observation with two values.
COLLIDING_PASSAGE = (
    "norm:0001801169:0001801169-23-000137:q32023formxex991earningsre.htm#p29")


def test_two_grid_positions_in_one_passage_cannot_share_an_observation_id():
    """The defect, driven through two real grid positions from the passage that showed it."""
    shared = dict(metric_id="adjusted_ebitda", value=0.0, unit="USD", currency="USD",
                  period=PeriodRef(period_start="2023-01-01", period_end="2023-09-30"),
                  source_lane="normalized_table", passage_id=COLLIDING_PASSAGE,
                  document_id=COLLIDING_PASSAGE.split("#")[0], raw_text="(558)")
    # Row 4 of the grid under header columns 6 and 12 — one metric, one subject, one resolved
    # period, two columns. Everything the old id was built from is identical here.
    year_to_date = LaneClaim(**shared, row_index=4, column_index=6, column_label="2023")
    quarter = LaneClaim(**{**shared, "value": -49000000.0}, row_index=4, column_index=12,
                        column_label="September 30, 2023")

    def identify(claim):
        return identifiers.observation_id(
            claim.metric_id, claim.subject_entity_id, claim.period.key, claim.source_lane,
            claim.passage_id, claim.structural_position)

    assert identify(year_to_date) != identify(quarter)
    # And the difference is the position and nothing else: the readable segments still agree,
    # which is what makes the two recognisable as readings of one metric.
    assert identify(year_to_date).rsplit(":", 1)[0] == identify(quarter).rsplit(":", 1)[0]


def test_an_observation_id_is_recomputed_identically_in_a_second_process():
    """Recomputation is stable, and stable across a process boundary.

    In-process equality only proves the function is not reading a clock. A separate
    interpreter proves it is not reading `hash()` either, whose salt is per-process — the one
    plausible way a "deterministic" id could pass every test here and differ between two runs.
    """
    import subprocess
    import sys

    claim = make_lane_claim(row_index=4, column_index=6)
    computed = identifiers.observation_id(
        claim.metric_id, claim.subject_entity_id, claim.period.key, claim.source_lane,
        claim.passage_id, claim.structural_position)
    assert computed == identifiers.observation_id(
        claim.metric_id, claim.subject_entity_id, claim.period.key, claim.source_lane,
        claim.passage_id, claim.structural_position)

    program = (
        "from extraction.core import identifiers;"
        "from extraction.core.models import LaneClaim, PeriodRef;"
        f"c = LaneClaim(metric_id={claim.metric_id!r}, value=2946, unit='homes',"
        " period=PeriodRef(period_start='2025-01-01', period_end='2025-03-31'),"
        f" source_lane={claim.source_lane!r}, passage_id={claim.passage_id!r},"
        f" document_id={claim.document_id!r}, raw_text='2,946',"
        " row_index=4, column_index=6);"
        "print(identifiers.observation_id(c.metric_id, c.subject_entity_id, c.period.key,"
        " c.source_lane, c.passage_id, c.structural_position))")
    other = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                           cwd=str(PACKAGE.parent), check=True)
    assert other.stdout.strip() == computed


def test_a_claim_with_no_grid_position_keeps_the_id_it_had_before_the_repair():
    """Additivity, pinned to a literal.

    A narrative claim has no grid position to state, and the repair must be invisible to it.
    The literal below is what the pre-repair `observation_id` returned for these inputs —
    `sha256(passage_id)[:12]`, because the digest took the passage alone — so this fails if
    the empty position ever stops reproducing it.
    """
    narrative = make_lane_claim(source_lane="normalized_narrative")
    assert narrative.structural_position == ()
    assert identifiers.observation_id(
        "homes_sold", "opendoor", "2025Q1", "normalized_narrative", PASSAGE_ID,
        narrative.structural_position,
    ) == "obs:homes-sold:opendoor:2025Q1:normalized-narrative:dd50cffa6513"
    # And the parameter is genuinely optional: the five-argument call every pre-repair caller
    # made returns the same id.
    assert identifiers.observation_id(
        "homes_sold", "opendoor", "2025Q1", "normalized_narrative", PASSAGE_ID,
    ) == "obs:homes-sold:opendoor:2025Q1:normalized-narrative:dd50cffa6513"


def test_the_passage_that_collided_now_yields_one_id_per_grid_cell(ontology, repo_config):
    """Zero within-passage collisions, on the passage the run measured 8 of them on.

    Driven through the real lane and the real assembler rather than through hand-built claims,
    because the claim under test is about what the *pipeline* emits: `structural_position` has
    to survive the lane, `LaneClaim` and `to_observation` to be worth anything.
    """
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    import json as _json
    from extraction.stages.select import AliasIndex
    from extraction.stages.tables import DeterministicTableClaimLane

    row = None
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.strip() and _json.loads(line)["passage_id"] == COLLIDING_PASSAGE:
            row = _json.loads(line)
            break
    assert row is not None, f"{COLLIDING_PASSAGE} is not in the normalized corpus"

    lane = DeterministicTableClaimLane(
        ontology, AliasIndex.from_ontology(ontology), deferred_metric_ids(ontology))
    extraction = lane.extract_table(row["text"], passage_id=COLLIDING_PASSAGE)
    assert extraction.claims, "the fixture passage must still produce table claims"

    claims = assemble(extraction.claims, ontology=ontology).claims
    ids = [c.metric_observation.observation_id for c in claims]
    assert len(set(ids)) == len(ids), sorted(
        i for i in ids if ids.count(i) > 1)
    # Every one of them states a position, and no two states the same one.
    positions = [(c.row_index, c.column_index) for c in extraction.claims]
    assert all(p != (None, None) for p in positions)
    assert len(set(positions)) == len(positions)


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
    claim = to_claim(make_lane_claim(), TABLE_PASSAGE_ROW)
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


def test_the_row_discriminator_does_not_silence_the_conflict_check(ontology):
    """Two grid cells that disagree are still a conflict, though they no longer share an id.

    This is the trap the repair had to avoid. The 4 disagreements the corpus reports are all
    *between columns of one table row* — exactly the pair `structural_position` now separates —
    so a check keyed on `observation_id` would have gone from 4 failures to 0 and reported a
    clean run over an unfixed defect. Keyed on `observation_identity` instead, which is the
    identity the id carried before the discriminator existed.
    """
    year_to_date = to_claim(make_lane_claim(value=183000000.0, row_index=4, column_index=6))
    quarter = to_claim(make_lane_claim(value=-211000000.0, row_index=4, column_index=12))
    assert (year_to_date.metric_observation.observation_id
            != quarter.metric_observation.observation_id)

    result = validate([year_to_date, quarter], ontology=ontology,
                      passages=InMemoryPassages({PASSAGE_ID: "x"}))
    conflicts = [f for f in result.errors if f.code == DUPLICATE_OBSERVATION_CONFLICT]
    assert len(conflicts) == 1
    # Both ids are named, because a reader given one of them can no longer derive the other.
    assert year_to_date.metric_observation.observation_id in conflicts[0].detail
    assert quarter.metric_observation.observation_id in conflicts[0].detail


def test_two_passages_reporting_one_fact_differently_are_not_a_conflict(ontology):
    """The check is not broadened to (metric, subject, period).

    A letter rounding to $38 million and a table stating 38,228 thousand are two readings of
    one fact that the id scheme keeps distinct on purpose, and calling them a conflict would
    fail every run over the corpus rather than over a defect. *(Measured: keyed on the passage,
    4 failures; without it, 124.)*
    """
    other = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p13"
    here = to_claim(make_lane_claim(value=2946))
    there = to_claim(make_lane_claim(value=2900, passage_id=other,
                                     document_id=other.split("#")[0]))
    result = validate([here, there], ontology=ontology,
                      passages=InMemoryPassages({PASSAGE_ID: "x", other: "y"}))
    assert not any(f.code == DUPLICATE_OBSERVATION_CONFLICT for f in result.errors)


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
