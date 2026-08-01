"""The deterministic table lane, on real corpus fixtures.

Every fixture is a table shape the corpus actually contains. Synthetic tables appear only
where the corpus has no example of the failure being guarded — a malformed layout, and a
pre-fix mojibake cell.
"""

from __future__ import annotations

import ast
import collections
import json
from pathlib import Path

import pytest
import yaml

from extraction.core.assembly import assemble, deferred_metric_ids
from extraction.core.models import PeriodRef
from extraction.core.validation import validate
from extraction.stages.select import AliasIndex
from extraction.stages.tables import (
    DeterministicTableClaimLane,
    analyse,
    parse_markdown_table,
)
from extraction.stages.tables.evaluation import GoldClaim, evaluate_case, summarise
from extraction.stages.tables.public import (
    AMBIGUOUS_ALIAS,
    DEFERRED_REQUIRED_SOURCE_LANE,
    DERIVED_CHANGE_COLUMN,
    ISSUE_CODES,
    PRECEDING_CONTEXT,
    TABLE_HEADER,
    UNRESOLVED_METRIC,
    UNSUPPORTED_TABLE_SHAPE,
)
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "extraction"
CASES_DIR = REPO / "benchmarks" / "extraction" / "v1" / "cases"
TABLE_CATEGORIES = {"deterministic_kpi_table", "sparse_reconciliation_table", "formula_drift"}

KPI_Q1_2025 = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p14"
SCALE_Q1_2025 = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p13"
RECON_Q1_2021 = "norm:0001801169:0001801169-21-000016:q12021form8-kxexhibit991.htm#p20"
RECON_Q4_2020 = "norm:0001801169:0001801169-21-000010:q42020form8-kxexhibit991.htm#p27"
SCALE_Q4_2020 = "norm:0001801169:0001801169-21-000010:q42020form8-kxexhibit991.htm#p26"
TENQ_Q1_2025 = "norm:0001801169:0001801169-25-000038:open-20250331.htm#p96"


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def lane(ontology):
    return DeterministicTableClaimLane(
        ontology, AliasIndex.from_ontology(ontology), deferred_metric_ids(ontology))


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    rows = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    return rows


def run(lane, passages, passage_id, preceding_id=None):
    row = passages[passage_id]
    preceding = passages.get(preceding_id) if preceding_id else None
    return lane.extract_table(
        row["text"], passage_id=passage_id, table_id=row["table_id"],
        block_ids=tuple(row["block_ids"]),
        preceding_text=preceding["text"] if preceding else None,
        preceding_passage_id=preceding_id)


def claim_for(result, metric_id, period_key):
    return next((c for c in result.claims
                 if c.metric_id == metric_id and c.period.key == period_key), None)


def issue_for(result, label):
    return next((i for i in result.issues if i.raw_label == label), None)


# -- table shapes ----------------------------------------------------------------------------


def test_standard_kpi_table(lane, passages):
    """Nine in-scope metrics across five period columns, counts and money and percentages."""
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    assert [c.column_label for c in result.header.period_columns] == [
        "March 31, 2025", "December 31, 2024", "September 30, 2024",
        "June 30, 2024", "March 31, 2024"]
    assert claim_for(result, "homes_sold", "2025Q1").value == 2946
    assert claim_for(result, "homes_purchased", "2025Q1").value == 3609
    assert claim_for(result, "contribution_margin", "2025Q1").value == 4.7


def test_interior_columns_are_not_shifted(lane, passages):
    """The alignment bug this lane was built to avoid. Header dates sit at columns 2,4,6,8,10
    while `Homes sold` values sit at 2,5,8,11,14 — positional indexing drops the third
    column and reports every later quarter under the wrong period."""
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    assert claim_for(result, "homes_sold", "2024Q3").value == 3615
    assert claim_for(result, "homes_sold", "2024Q2").value == 4078
    assert claim_for(result, "homes_sold", "2024Q1").value == 3078


def test_reconciliation_table(lane, passages):
    result = run(lane, passages, RECON_Q1_2021)
    assert claim_for(result, "adjusted_gross_profit", "2021Q1").value == 97_038_000
    assert claim_for(result, "contribution_profit", "2021Q1").value == 76_146_000
    assert claim_for(result, "direct_selling_costs", "2021Q1").value == -17_340_000


def test_scale_from_the_table_header(lane, passages):
    result = run(lane, passages, RECON_Q1_2021)
    claim = claim_for(result, "adjusted_gross_profit", "2021Q1")
    assert claim.scale.scale == "thousands"
    assert claim.extractor_metadata["scale_source"] == TABLE_HEADER


def test_scale_from_the_preceding_narrative_passage(lane, passages):
    """9 of 74 KPI-bearing tables declare the scale in the passage before. Without it every
    dollar figure is a million times too small."""
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    claim = claim_for(result, "contribution_profit", "2025Q1")
    assert claim.value == 54_000_000
    assert claim.scale.scale == "millions"
    assert claim.extractor_metadata["scale_source"] == PRECEDING_CONTEXT
    assert claim.extractor_metadata["preceding_context_passage_id"] == SCALE_Q1_2025


def test_a_table_local_declaration_wins_over_the_preceding_passage(lane, passages):
    """Precedence is not arbitrary: a table stating its own scale states it for itself,
    while a preceding paragraph may introduce several tables."""
    result = run(lane, passages, RECON_Q1_2021, SCALE_Q1_2025)
    claim = claim_for(result, "adjusted_gross_profit", "2021Q1")
    assert claim.scale.scale == "thousands"
    assert claim.extractor_metadata["scale_source"] == TABLE_HEADER


def test_split_currency_and_value_cells(lane, passages):
    """`| $ | 97,132 |` — the sigil and the magnitude are different cells."""
    result = run(lane, passages, RECON_Q1_2021)
    claim = claim_for(result, "adjusted_gross_profit", "2021Q1")
    assert claim.raw_text == "97,038"
    # `USD`, as the ontology spells it. `usd` is in no part of the vocabulary and
    # `check_observation_unit` refuses it; this line asserted the lower-case spelling
    # until 2026-08-02, which is how the lane kept emitting it.
    assert claim.unit == "USD" and claim.currency == "USD"


def test_split_value_and_percent_cells(lane, passages):
    """`| 13.0 | % |` — likewise."""
    result = run(lane, passages, RECON_Q1_2021)
    claim = claim_for(result, "adjusted_gross_margin", "2021Q1")
    assert claim.value == 13.0 and claim.unit == "percent"
    assert claim.scale is None or claim.scale.scale == "units"


def test_multiple_period_header_rows_with_two_duration_groups(lane, passages):
    """Two groups over one row of bare years, identical end dates. The colspan that put
    "Three Months Ended" above the first two years does not survive the Markdown, so
    position alone hands 2019 to the annual group."""
    result = run(lane, passages, RECON_Q4_2020, SCALE_Q4_2020)
    assert [c.period.key for c in result.header.period_columns] == [
        "2020Q4", "2019Q4", "FY2020", "FY2019"]
    assert claim_for(result, "adjusted_gross_profit", "2020Q4").value == 38_228_000
    assert claim_for(result, "adjusted_gross_profit", "FY2020").value == 211_274_000
    assert claim_for(result, "adjusted_gross_margin", "2019Q4").value == 5.6


def test_instant_and_duration_rows_in_one_table(lane, passages):
    """`Homes in inventory (at period end)` is a stock in a table whose other rows are
    flows. The column decides when; the row decides what kind."""
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    stock = claim_for(result, "housing_inventory_homes", "2025-03-31")
    flow = claim_for(result, "homes_sold", "2025Q1")
    assert stock.period.is_instant and stock.value == 7080
    assert not flow.period.is_instant


def test_negative_values_in_parentheses(lane, passages):
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    assert claim_for(result, "adjusted_ebitda", "2025Q1").value == -30_000_000
    assert claim_for(result, "adjusted_ebitda_margin", "2024Q2").value == -0.3


def test_curly_quotes_in_a_metric_label_resolve(lane, passages):
    """The ontology writes the label with straight quotes; every filing prints curly ones.
    Before the typography fold this metric resolved in no table at all."""
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    claim = claim_for(result, "pct_homes_on_market_gt_120_days", "2025-03-31")
    assert claim.value == 27 and claim.unit == "percent"
    assert "“on the market”" in claim.population_definition_raw


def test_a_change_column_never_becomes_an_observation(lane, passages):
    result = run(lane, passages, TENQ_Q1_2025)
    assert any(c.is_change_column for c in result.header.period_columns)
    assert any(i.code == DERIVED_CHANGE_COLUMN for i in result.issues)
    assert all(c.period.key != "unknown" for c in result.claims)


def test_rows_with_and_without_a_change_value_both_align(lane, passages):
    """`Homes sold` prints 2,946 / 3,078 / (132); the 120-day row prints 27% / 15% and
    leaves the change cell blank. Both must align without guessing."""
    result = run(lane, passages, TENQ_Q1_2025)
    assert claim_for(result, "homes_sold", "2025Q1").value == 2946
    assert claim_for(result, "pct_homes_on_market_gt_120_days", "2025-03-31").value == 27
    assert claim_for(result, "pct_homes_on_market_gt_120_days", "2024-03-31").value == 15


# -- abstentions -------------------------------------------------------------------------------


def test_an_ambiguous_alias_produces_no_claim(lane, passages):
    """`Inventory impairment – Current Period(1)` matches only ambiguous surfaces. No claim,
    and the issue names the candidates so the decision is reproducible."""
    result = run(lane, passages, RECON_Q1_2021)
    issue = issue_for(result, "Inventory impairment – Current Period(1)")
    assert issue is not None and issue.code == AMBIGUOUS_ALIAS
    assert issue.candidate_concept_ids
    assert issue.row_index is not None and issue.normalized_label


def test_a_deferred_metric_is_never_emitted_from_a_table(lane, passages, ontology):
    """`Gross profit (GAAP)` is readable and must not be read: its first source lane is
    xbrl, which this corpus does not contain."""
    result = run(lane, passages, RECON_Q1_2021)
    issue = issue_for(result, "Gross profit (GAAP)")
    assert issue.code == DEFERRED_REQUIRED_SOURCE_LANE and issue.required_lane == "xbrl"
    emitted = {c.metric_id for c in result.claims}
    assert not (emitted & deferred_metric_ids(ontology))


def test_a_partial_label_match_does_not_become_a_claim(lane, passages):
    """"Contribution Profit per Home Sold" is a $31 per-home figure. Substring matching
    reads it as `contribution_profit` and emits $31,000 as a quarterly total."""
    result = run(lane, passages, RECON_Q1_2021)
    assert issue_for(result, "Contribution Profit per Home Sold") is not None
    assert all(c.value != 31_000 for c in result.claims)


def test_a_metric_split_across_rows_is_not_totalled(lane, passages):
    """Two holding-cost rows the filing never totals. Emitting either as `holding_costs`
    asserts a figure the filing does not report."""
    result = run(lane, passages, RECON_Q1_2021)
    assert issue_for(result, "Holding costs on sales – Current Period(4)(5)") is not None
    assert "holding_costs" not in {c.metric_id for c in result.claims}


def test_every_issue_code_is_declared(lane, passages):
    for passage_id in (KPI_Q1_2025, RECON_Q1_2021, RECON_Q4_2020, TENQ_Q1_2025):
        result = run(lane, passages, passage_id)
        assert {i.code for i in result.issues} <= ISSUE_CODES


def test_an_issue_carries_enough_to_reproduce_the_decision(lane, passages):
    result = run(lane, passages, RECON_Q1_2021)
    for issue in result.issues:
        assert issue.passage_id and issue.detail
        if issue.code in {AMBIGUOUS_ALIAS, UNRESOLVED_METRIC, DEFERRED_REQUIRED_SOURCE_LANE}:
            assert issue.raw_label and issue.row_index is not None


# -- synthetic guards ----------------------------------------------------------------------------


def test_a_dash_is_not_converted_to_zero(lane):
    """`—` in a reconciliation means the adjustment did not apply. Nil is neither a
    reported zero nor a missing value, and emitting 0 asserts a figure the filing does not
    contain."""
    table = ("|  |  | Three Months Ended December 31, |  |  |\n"
             "| --- | --- | --- | --- | --- |\n"
             "| (in thousands) |  | 2020 |  | 2019 |\n"
             "| Adjusted Gross Profit |  | — |  | 70,607 |")
    result = lane.extract_table(table, passage_id="norm:x:y:z.htm#p1")
    # The dashed column yields nothing at all; the column beside it is unaffected, which is
    # what shows the dash was skipped rather than the row abandoned.
    assert claim_for(result, "adjusted_gross_profit", "2020Q4") is None
    assert claim_for(result, "adjusted_gross_profit", "2019Q4").value == 70_607_000
    assert 0 not in {c.value for c in result.claims}


def test_a_mojibake_numeric_cell_still_parses(lane):
    """The corpus is clean, but a rebuild predating the encoding fix produces `97,132Â`."""
    table = ("|  |  | Three Months Ended |  |  |\n"
             "| --- | --- | --- | --- | --- |\n"
             "| (in thousands) |  | March 31, 2021 |  | March 31, 2020 |\n"
             "| Adjusted Gross Profit |  | 97,132Â |  | 88,848Â |")
    result = lane.extract_table(table, passage_id="norm:x:y:z.htm#p1")
    claim = claim_for(result, "adjusted_gross_profit", "2021Q1")
    assert claim is not None and claim.value == 97_132_000
    assert claim_for(result, "adjusted_gross_profit", "2020Q1").value == 88_848_000


def test_a_malformed_table_is_reported_not_crashed(lane):
    result = lane.extract_table("| just one row |", passage_id="norm:x:y:z.htm#p1")
    assert result.claims == []
    assert result.issues[0].code == UNSUPPORTED_TABLE_SHAPE


def test_a_table_with_no_period_header_emits_no_claim(lane):
    table = ("| Metric | Value |\n| --- | --- |\n| Homes sold | 2,946 |\n| Homes purchased | 3,609 |")
    result = lane.extract_table(table, passage_id="norm:x:y:z.htm#p1")
    assert result.claims == []
    assert any(i.code in ISSUE_CODES for i in result.issues)


# -- grid invariants -------------------------------------------------------------------------------


def test_original_cell_positions_survive_column_collapsing(passages):
    grid = parse_markdown_table(passages[KPI_Q1_2025]["text"])
    collapsed = [c for row in grid.rows for c in row if c.collapsed_index >= 0]
    assert collapsed
    for cell in collapsed:
        assert cell.column_index >= cell.collapsed_index
    assert len(grid.surviving_columns) < grid.width, "nothing was collapsed"


def test_evidence_records_original_not_collapsed_positions(lane, passages):
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    claim = claim_for(result, "homes_sold", "2024Q3")
    metadata = claim.extractor_metadata
    grid = parse_markdown_table(passages[KPI_Q1_2025]["text"])
    cell = grid.cell(metadata["row_index"], metadata["value_column_index"])
    assert cell is not None and cell.text.strip() == "3,615"


# -- determinism and boundaries ----------------------------------------------------------------------


def test_two_runs_produce_byte_identical_output(lane, passages):
    def serialise():
        result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
        return json.dumps(
            [c.model_dump(mode="json") for c in result.claims], sort_keys=True).encode()
    assert serialise() == serialise()


def test_no_provider_is_reachable_from_the_table_lane():
    """Not a mock check — an import check. A lane that could reach a provider is one that
    might, and the deterministic guarantee would then depend on review rather than
    structure."""
    banned = ("openai", "httpx", "requests", "llama", "torch", "transformers",
              "sentence_transformers", "socket", "urllib")
    for path in (PACKAGE / "stages" / "tables").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name.startswith(b) for b in banned), f"{path.name}: {name}"
                assert "provider" not in name.lower(), f"{path.name}: {name}"
                assert "benchmark" not in name.lower(), f"{path.name}: {name}"


def test_the_lane_emits_lane_claims_and_not_ontology_claims(lane, passages):
    """Assembly is a later stage. Keeping the split is what lets the lane be tested against
    a table with no ontology claim machinery in the way."""
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    assert result.claims
    for claim in result.claims:
        assert type(claim).__name__ == "LaneClaim"
        assert not hasattr(claim, "claim_kind")


def test_source_lane_and_assertion_type(lane, passages):
    """`reported`, because that is the only value `AssertionType` contains.

    This asserted `explicitly_reported` until 2026-08-02, and so encoded the defect rather
    than the contract: the string is not in the ontology's enum, and every claim carrying it
    raised `ValidationError` the moment it reached `MetricObservation`. Nothing noticed for
    five stages because no test drove a table-lane claim past `LaneClaim` -- see
    `test_every_table_lane_claim_survives_assembly_and_both_validators`, which now does.
    """
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    for claim in result.claims:
        assert claim.source_lane == "normalized_table"
        assert claim.assertion_type == "reported"


def test_subject_basis_is_recorded(lane, passages):
    result = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    for claim in result.claims:
        assert claim.subject_entity_id == "opendoor"
        assert claim.extractor_metadata["subject_basis"] == "registrant_metadata"


def test_every_evidence_reference_resolves(lane, passages):
    for passage_id, preceding in ((KPI_Q1_2025, SCALE_Q1_2025), (RECON_Q1_2021, None)):
        result = run(lane, passages, passage_id, preceding)
        for claim in result.claims:
            assert claim.passage_id in passages
            assert claim.extractor_metadata["table_id"] == passages[passage_id]["table_id"]
            for block_id in claim.extractor_metadata["block_ids"]:
                assert block_id in passages[passage_id]["block_ids"]


# -- benchmark evaluation --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evaluation(lane, passages):
    by_document = collections.defaultdict(dict)
    for row in passages.values():
        by_document[row["document_id"]][row["passage_sequence"]] = row

    def period_key(gold):
        if gold.get("instant_date"):
            return gold["instant_date"]
        return PeriodRef(period_start=gold.get("period_start"),
                         period_end=gold.get("period_end")).key

    results = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]:
            if case["lane"] != "tables" or case["category"] not in TABLE_CATEGORIES:
                continue
            row = passages[case["passage_id"]]
            preceding = by_document[row["document_id"]].get(row["passage_sequence"] - 1)
            result = lane.extract_table(
                row["text"], passage_id=case["passage_id"], table_id=row["table_id"],
                block_ids=tuple(row["block_ids"]),
                preceding_text=preceding["text"] if preceding else None,
                preceding_passage_id=preceding["passage_id"] if preceding else None)
            gold = [GoldClaim(g["metric_id"], g["value"], g["unit"], period_key(g),
                              g["subject_entity_id"], g.get("scale_applied"),
                              g.get("currency"))
                    for g in (case.get("gold_claims") or [])]
            results.append(evaluate_case(
                case["case_id"], case["passage_id"], result.claims, gold, result.issues,
                resolves_evidence=lambda c: c.passage_id in passages))
    return summarise(results)


def test_benchmark_recall(evaluation):
    assert evaluation.metric_recall >= 0.93, evaluation.as_report()


@pytest.mark.parametrize(
    "dimension", ["value", "unit", "scale", "period", "subject", "evidence"])
def test_every_matched_claim_is_right_on_every_dimension(evaluation, dimension):
    assert evaluation.accuracy(dimension) == 1.0, (
        f"{dimension}: {evaluation.accuracy(dimension):.3f}\n{evaluation.as_report()}")


def test_the_only_remaining_miss_is_the_documented_ontology_gap(evaluation):
    """"Homes sold in period" is a surface form the ontology does not carry. The benchmark
    case says so itself. Broadening the alias to pass would be fitting the vocabulary to a
    fixture, so it stays a recorded gap."""
    missing = {m for case in evaluation.cases for m in case.missing}
    assert missing <= {"homes_sold@2021Q1", "homes_sold@2020Q4", "homes_sold@2020Q1"}


def test_per_case_results_are_available(evaluation):
    assert len(evaluation.cases) >= 10
    for case in evaluation.cases:
        assert case.case_id and case.passage_id


# -- stage 6b: duration groups bind to columns by date shape -------------------------------

Q4_2023 = "norm:0001801169:0001801169-24-000015:q42023formxex991earningsre.htm#p16"


def test_seven_columns_over_two_duration_groups_resolve_by_date_shape(lane, passages):
    """The shape even division cannot split. Five full dates under `Three Months Ended`
    beside two bare years under `Year Ended December 31,` — a full-date column carries its
    own month and day so it belongs to the phrase supplying none, and a bare year is
    unusable without one so it belongs to the phrase supplying it."""
    result = run(lane, passages, Q4_2023)
    assert not result.header.group_assignment_ambiguous
    assert [(c.column_label, c.period.key) for c in result.header.period_columns] == [
        ("December 31, 2023", "2023Q4"),
        ("September 30, 2023", "2023Q3"),
        ("June 30, 2023", "2023Q2"),
        ("March 31, 2023", "2023Q1"),
        ("December 31, 2022", "2022Q4"),
        ("2023", "FY2023"),
        ("2022", "FY2022"),
    ]


@pytest.mark.parametrize(
    "column_label,period_key",
    [("December 31, 2022", "2022Q4"), ("March 31, 2023", "2023Q1"),
     ("June 30, 2023", "2023Q2")])
def test_the_three_columns_that_were_read_as_annual_are_now_quarterly(
    lane, passages, column_label, period_key
):
    """These three produced 21 observations under twelve-month durations. None were gold, so
    every score stayed at 1.000 and the defect was invisible to the benchmark."""
    result = run(lane, passages, Q4_2023)
    column = next(c for c in result.header.period_columns if c.column_label == column_label)
    assert column.period.key == period_key
    assert column.period.period_start and column.period.period_end
    months = (int(column.period.period_end[:4]) * 12 + int(column.period.period_end[5:7])) - (
        int(column.period.period_start[:4]) * 12 + int(column.period.period_start[5:7]))
    assert months == 2, "a quarter spans three months inclusive"


def test_the_twenty_one_wrong_period_observations_are_gone(lane, passages):
    """The regression this stage exists for."""
    result = run(lane, passages, Q4_2023)
    offenders = [
        c for c in result.claims
        if c.column_label in ("December 31, 2022", "March 31, 2023", "June 30, 2023")
        and c.period.key in ("FY2022", "FY2023")
    ]
    assert offenders == []
    assert not any(c.period.key.startswith("FY") and "," in (c.column_label or "")
                   for c in result.claims), "a full-date column may never yield a fiscal year"


def test_bare_year_columns_still_take_the_month_and_day_from_their_group(lane, passages):
    result = run(lane, passages, Q4_2023)
    annual = [c for c in result.header.period_columns if c.column_label in ("2023", "2022")]
    assert [c.period.key for c in annual] == ["FY2023", "FY2022"]
    for column in annual:
        assert column.period.period_end.endswith("-12-31")


def test_uniform_bare_years_still_divide_evenly(lane, passages):
    """The discriminator says nothing when every column has the same shape, and the Q4 2020
    reconciliation must keep working: four bare years under two date-supplying phrases."""
    result = run(lane, passages, RECON_Q4_2020, SCALE_Q4_2020)
    assert not result.header.group_assignment_ambiguous
    assert [c.period.key for c in result.header.period_columns] == [
        "2020Q4", "2019Q4", "FY2020", "FY2019"]


def test_an_unresolvable_uneven_grouping_abstains(lane):
    """Three columns of mixed shape over two phrases that both supply a month and day. The
    shapes cannot separate them and 3 does not divide by 2, so nothing is emitted."""
    table = ("|  |  | Three Months Ended June 30, |  | Year Ended December 31, |  |\n"
             "| --- | --- | --- | --- | --- | --- |\n"
             "| (in thousands) |  | 2023 |  | March 31, 2023 |  | 2022 |\n"
             "| Adjusted Gross Profit |  | 1,000 |  | 2,000 |  | 3,000 |")
    result = lane.extract_table(table, passage_id="norm:x:y:z.htm#p1")
    assert result.claims == []
    assert [i.code for i in result.issues] == ["AMBIGUOUS_COLUMN_ALIGNMENT"]
    assert result.header.group_assignment_ambiguous


def test_previously_correct_cases_are_unchanged_by_the_discriminator(lane, passages):
    """Guards the fix against being a regression somewhere else."""
    kpi = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    assert claim_for(kpi, "homes_sold", "2024Q3").value == 3615
    assert claim_for(kpi, "contribution_profit", "2025Q1").value == 54_000_000
    recon = run(lane, passages, RECON_Q1_2021)
    assert claim_for(recon, "adjusted_gross_profit", "2021Q1").value == 97_038_000
    tenq = run(lane, passages, TENQ_Q1_2025)
    assert claim_for(tenq, "homes_sold", "2025Q1").value == 2946


# -- the whole way through, which is what neither defect above survived ----------------------


class _CatalogPassages:
    """A `PassageSource` over the real catalog, so evidence resolution is not simulated."""

    def __init__(self, rows: dict[str, dict]):
        self._rows = rows

    def text_of(self, passage_id):
        row = self._rows.get(passage_id)
        return row["text"] if row else None

    def exists(self, passage_id):
        return passage_id in self._rows

    def document_of(self, passage_id):
        row = self._rows.get(passage_id)
        return row["document_id"] if row else None


def test_every_table_lane_claim_survives_assembly_and_both_validators(lane, passages, ontology):
    """The test whose absence hid two defects for five stages.

    Every other test in this file stops at `LaneClaim`, because that is the lane's contract
    and `assemble` belongs to another module. The gap that opened is that *nothing* drove a
    table-lane claim the rest of the way, so two values the ontology cannot accept sat in
    every claim the lane emitted and no suite noticed:

    - `assertion_type="explicitly_reported"`, which is not in `AssertionType` -- all 28 claims
      from the Q1 2025 KPI table raised `ValidationError` at `MetricObservation`;
    - `unit="usd"`, where every monetary metric declares
      `allowed_units: ['USD', 'USD_thousands', 'USD_millions']` and `check_observation_unit`
      refuses the lower-case spelling.

    Both are one-word defects and neither could be caught by asserting on a `LaneClaim`, which
    is why this test asserts on the far end instead. Step 13's integrated run is the first
    thing that would have hit them in anger.
    """
    checked = 0
    for passage_id, preceding_id in ((KPI_Q1_2025, SCALE_Q1_2025),
                                     (RECON_Q1_2021, None),
                                     (RECON_Q4_2020, SCALE_Q4_2020),
                                     (TENQ_Q1_2025, None)):
        extraction = run(lane, passages, passage_id, preceding_id)
        assert extraction.claims, passage_id

        result = assemble(list(extraction.claims), ontology=ontology, passage_rows=passages)
        assert result.claims, f"{passage_id}: assembly produced nothing"
        assert not result.rejected, [str(r) for r in result.rejected][:3]

        ontology_result = ontology.validate_claims(list(result.claims))
        assert not ontology_result.errors, \
            f"{passage_id}: {[str(e) for e in ontology_result.errors][:3]}"

        findings = validate(list(result.claims), ontology=ontology,
                            passages=_CatalogPassages(passages))
        assert not findings.errors, f"{passage_id}: {[str(e) for e in findings.errors][:3]}"
        checked += len(result.claims)

    assert checked > 50, f"only {checked} claims reached the validators"


def test_the_unit_a_monetary_claim_carries_is_a_unit_the_ontology_declares(lane, passages,
                                                                          ontology):
    """`usd` is not in the vocabulary anywhere, and the lane used to emit it.

    Asserted against the ontology rather than against the literal `"USD"` so that a
    vocabulary that renamed its monetary unit would fail here rather than silently disagree
    with the lane.
    """
    extraction = run(lane, passages, KPI_Q1_2025, SCALE_Q1_2025)
    monetary = [c for c in extraction.claims if c.currency]
    assert monetary

    for claim in monetary:
        metric = ontology.registry.metric(claim.metric_id)
        allowed = set(metric.allowed_units) | {metric.unit}
        assert claim.unit in allowed, f"{claim.metric_id}: {claim.unit!r} not in {sorted(allowed)}"


def test_the_population_wording_is_carried_for_whatever_metric_declares_one(lane, passages,
                                                                           ontology):
    """§7.1 keyed on the vocabulary, not on one metric's name.

    The lane wrote `metric_id == "pct_homes_on_market_gt_120_days"` -- the literal the rest
    of it is careful never to write. A second metric declaring a population would have lost
    its filed wording silently, which is the one thing §7.1 exists to prevent.
    """
    declaring = {c.concept_id for c in ontology.registry.by_category("metric_definition")
                 if getattr(c, "population", None)}
    assert declaring, "no metric declares a population; this test would prove nothing"

    for passage_id, preceding_id in ((KPI_Q1_2025, SCALE_Q1_2025), (TENQ_Q1_2025, None)):
        for claim in run(lane, passages, passage_id, preceding_id).claims:
            if claim.metric_id in declaring:
                assert claim.population_definition_raw, claim.metric_id
            else:
                assert claim.population_definition_raw is None, claim.metric_id
