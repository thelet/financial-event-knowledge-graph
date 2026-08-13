"""S3 — the handle a citation binds to, minted from coordinates the package already carries.

Offline, from two committed pulls of the real graph. `fixtures/table_cells_corpus.json` is S2's
six table rows (the same file, not a copy) and `fixtures/narrative_evidence_corpus.json` is
**every** narrative-evidenced observation in the graph, all 14, pulled 2026-08-13. Nothing here
opens a connection: §13.7 has to resolve a handle from a stored package with no server running,
so the tests that stand for it must too.

**What is proved here rather than asserted.** A handle is only worth having if it survives the
round trip §14 makes every package take, resolves to the fact's *own* bytes, and cannot name two
facts. Each of those is driven — the package is dumped to JSON and read back, the handle is
looked up in the package's own index, and the cell it names is resolved with
`story.core.table_cells` and compared to `quoted_text`. No test asserts that a field exists.

**The plan's narrative handle is wrong and this file is where that is recorded executably.**
TABLE_CELL_CITATIONS §3.1 specified `ev:<passage_id>:span` for narrative evidence on the
strength of §1.4 — which measured uniqueness for *table cells* and never for spans. The 14
narrative observations sit in **6** passages, so that form names up to six facts at once, and
`cand:cross-metric-divergence:adjusted-gross-profit-contribution-profit:opendoor:2021Q4:
727148801299` is a package built from this graph today that carries two of them. The shipped
form names the fact's slot, `(metric_id, period_key)`. §3.1 now carries the correction; these
three tests are what hold it: `test_the_plans_passage_only_span_handle_would_name_six_facts_at_once`,
`test_char_offsets_would_not_have_separated_them_either` and
`test_two_periods_of_one_metric_in_one_passage_are_separately_citable`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.models import (
    BudgetParameters,
    EvidenceRole,
    PackageBudget,
    PackagedFact,
    PackagedPassage,
    PackagedSubject,
    StoryEvidencePackage,
    TableCellRef,
)
from story.core.table_cells import resolve_cell, resolve_header

FIXTURES = Path(__file__).parent / "fixtures"

#: S2's six table rows, each a real `(:Observation)-[:EVIDENCED_BY]->(:Passage)` triple chosen
#: for a shape that breaks a different plausible resolver — read from S2's own fixture rather
#: than copied, so a correction to one file cannot leave the other stale.
TABLE_ROWS = json.loads((FIXTURES / "table_cells_corpus.json").read_text(encoding="utf-8"))["rows"]

_NARRATIVE = json.loads(
    (FIXTURES / "narrative_evidence_corpus.json").read_text(encoding="utf-8"))
NARRATIVE_ROWS = _NARRATIVE["rows"]

#: The plan's worked example (§1.2): `quoted_text` is `'7'` and occurs 27 times in its passage,
#: so the old contract — find the quote, and refuse it if it is not unique — had no satisfiable
#: answer for it.
SEVEN = next(row for row in TABLE_ROWS if row["quoted_text"] == "7")

#: The passage carrying six narrative observations, and the two facts a real package puts
#: together: `adjusted_gross_profit` and `contribution_profit`, both 2021Q4, both read out of
#: `…q42021formxex992sharehol.htm#p10`, quoting two different sentences.
CROWDED_PASSAGE = "norm:0001801169:0001801169-22-000026:q42021formxex992sharehol.htm#p10"


def _table_id(row: dict) -> str:
    return row["observation_id"].split(":", 2)[-1]


def _narrative_id(row: dict) -> str:
    return f"{row['metric_id']}@{row['passage_id'].rsplit('#', 1)[-1]}"


def fact_from_table_row(row: dict, **overrides) -> PackagedFact:
    """A `PackagedFact` carrying exactly what the graph carries for this observation."""
    fields = dict(
        observation_id=row["observation_id"],
        metric_id=row["metric_id"],
        metric_label=row["metric_id"].replace("_", " ").title(),
        period_key=row["period_key"],
        shape="quarter",
        value=1.0,
        unit="USD",
        source_lane="normalized_table",
        validation_state="clean",
        passage_id=row["passage_id"],
        document_id=row["passage_id"].rsplit("#p", 1)[0],
        quoted_text=row["quoted_text"],
        row_label=row["row_label"],
        column_label=row["column_label"],
        cell=TableCellRef(
            row_index=row["row_index"],
            value_column_index=row["value_column_index"],
            period_header_row_index=row["period_header_row_index"],
            period_header_column_index=row["period_header_column_index"],
        ),
    )
    fields.update(overrides)
    return PackagedFact(**fields)  # type: ignore[arg-type]


def fact_from_narrative_row(row: dict, **overrides) -> PackagedFact:
    fields = dict(
        observation_id=row["observation_id"],
        metric_id=row["metric_id"],
        metric_label=row["metric_id"].replace("_", " ").title(),
        period_key=row["period_key"],
        shape="quarter",
        value=1.0,
        unit="percent",
        source_lane="normalized_narrative",
        validation_state="clean",
        passage_id=row["passage_id"],
        document_id=row["passage_id"].rsplit("#p", 1)[0],
        quoted_text=row["quoted_text"],
    )
    fields.update(overrides)
    return PackagedFact(**fields)  # type: ignore[arg-type]


def package_of(*facts: PackagedFact, passages: tuple[PackagedPassage, ...] = ()
               ) -> StoryEvidencePackage:
    """The smallest package that can hold these facts, with the identity block filled with
    the real run's values so nothing here has to invent a graph run."""
    return StoryEvidencePackage(
        package_id="pkg:test:0000",
        candidate_id="cand:test:0000",
        detector_id="detector:metric_move",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id="graph-v1-0483dc6b4b10",
        graph_projection_version="1.2.0",
        extraction_run_id="extract-v1-lexical-833f7bcfbce9",
        run_complete_sha256="1cc8f7b01c0405311e70f5306635809c73685ed8e079cb35b92a7c36b8179d8e",
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash="bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34",
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor", entity_text="Opendoor", resolved=True),
        facts=facts,
        primary_passages=passages,
        budget=PackageBudget(prompt_token_estimate=1, artifact_token_estimate=1,
                             parameters=BudgetParameters()),
    )


def passage_of(row: dict) -> PackagedPassage:
    return PackagedPassage(
        passage_id=row["passage_id"],
        document_id=row["passage_id"].rsplit("#p", 1)[0],
        text=row["passage_text"],
        char_count=len(row["passage_text"]),
        passage_kind="table",
        role=EvidenceRole.PRIMARY_SUPPORT,
    )


# -- §3.1's two forms -----------------------------------------------------------------------


@pytest.mark.parametrize("row", TABLE_ROWS, ids=_table_id)
def test_a_table_backed_fact_is_named_by_its_cell(row):
    """`ev:<passage_id>:r<row_index>c<value_column_index>` — §3.1, on real coordinates.

    The literal string is asserted rather than rebuilt with the producer's own function: a test
    that formats the handle the way the code formats it would keep passing through a format
    change that broke every stored citation.
    """
    fact = fact_from_table_row(row)
    assert fact.evidence_handle == (
        f"ev:{row['passage_id']}:r{row['row_index']}c{row['value_column_index']}")


@pytest.mark.parametrize("row", NARRATIVE_ROWS, ids=_narrative_id)
def test_a_narrative_fact_is_named_by_its_passage_and_its_slot(row):
    """`ev:<passage_id>:span:<metric_id>:<period_key>` — *not* the plan's passage-only form,
    for the reason the next two tests measure."""
    fact = fact_from_narrative_row(row)
    assert fact.evidence_handle == (
        f"ev:{row['passage_id']}:span:{row['metric_id']}:{row['period_key']}")


def test_the_plans_passage_only_span_handle_would_name_six_facts_at_once():
    """§3.1's `ev:<passage_id>:span` is not a key, and the corpus says so.

    Fourteen narrative observations, **six** passages: one carries six of them, one three, one
    two. §1.4 measured a cell as a perfect key and the plan generalised that to spans without
    measuring. The shipped form separates all fourteen.
    """
    passages = {row["passage_id"] for row in NARRATIVE_ROWS}
    assert len(NARRATIVE_ROWS) == 14
    assert len(passages) == 6

    plan_handles = {f"ev:{row['passage_id']}:span" for row in NARRATIVE_ROWS}
    assert len(plan_handles) == 6

    shipped = {fact_from_narrative_row(row).evidence_handle for row in NARRATIVE_ROWS}
    assert len(shipped) == 14


def test_char_offsets_would_not_have_separated_them_either():
    """The obvious repair — put the span's offsets in the handle — does not work.

    `adjusted_gross_profit` and `adjusted_gross_margin` 2021Q4 quote the **same 66-character
    sentence at the same offset**: *"Adjusted gross profit was $279 million and 7.3% of revenue
    in 4Q21"*. One sentence really does evidence two facts, so no structural coordinate can tell
    them apart and the discriminator has to come from the fact. That is why the narrative handle
    carries the fact's slot and why the table handle, where §1.4 measured the cell as a perfect
    key, needs nothing of the sort.
    """
    sharing = [row for row in NARRATIVE_ROWS
               if row["passage_id"] == CROWDED_PASSAGE
               and row["metric_id"] in ("adjusted_gross_profit", "adjusted_gross_margin")]

    assert len(sharing) == 2
    assert len({row["quoted_text"] for row in sharing}) == 1
    assert len({row["quote_char_start"] for row in sharing}) == 1
    assert len({fact_from_narrative_row(row).evidence_handle for row in sharing}) == 2


def test_two_periods_of_one_metric_in_one_passage_are_separately_citable():
    """The other half of the discriminator, and the reason the handle names a *slot*.

    A quarterly table prints two periods of one metric side by side, so *"the same metric, out
    of the same passage, for two periods"* is the ordinary shape — it is what
    `test_story_deterministic_verifier.py` builds for `adjusted_gross_margin` 2022Q3 and 2022Q4.
    Those rows carry cells and so take the cell form; a fact with no coordinates would not, and
    `(passage_id, metric_id)` alone would have named both. §6.1 keeps one canonical fact per
    `(metric_id, period_key)`, which is what makes the slot the finest grain guaranteed unique.
    """
    row = next(r for r in NARRATIVE_ROWS if r["metric_id"] == "adjusted_gross_profit")
    later = {**row, "observation_id": row["observation_id"].replace("2021Q4", "2022Q1"),
             "period_key": "2022Q1"}

    package = package_of(fact_from_narrative_row(row), fact_from_narrative_row(later))

    assert sorted(package.facts_by_evidence_handle()) == [
        f"ev:{row['passage_id']}:span:adjusted_gross_profit:2021Q4",
        f"ev:{row['passage_id']}:span:adjusted_gross_profit:2022Q1",
    ]


def test_two_narrative_facts_from_one_passage_are_separately_citable():
    """The pair a package built from this graph today actually carries.

    `cand:cross-metric-divergence:adjusted-gross-profit-contribution-profit:opendoor:2021Q4:
    727148801299` holds `adjusted_gross_profit` and `contribution_profit` for 2021Q4, both read
    out of `…#p10`, quoting sentences at character 996 and character 1,359 *(verified live
    2026-08-13 by building all 242 packages D1 and D4 produce)*. Under the plan's form this
    package could not have been built at all.
    """
    rows = [row for row in NARRATIVE_ROWS
            if row["passage_id"] == CROWDED_PASSAGE
            and row["metric_id"] in ("adjusted_gross_profit", "contribution_profit")]
    assert len(rows) == 2

    package = package_of(*(fact_from_narrative_row(row) for row in rows))
    index = package.facts_by_evidence_handle()

    assert len(index) == 2
    for row in rows:
        handle = f"ev:{CROWDED_PASSAGE}:span:{row['metric_id']}:{row['period_key']}"
        assert index[handle].observation_id == row["observation_id"]


# -- the handle resolves to the fact's own bytes, from the package alone ---------------------


@pytest.mark.parametrize("row", TABLE_ROWS, ids=_table_id)
def test_the_handle_resolves_to_the_facts_own_cell_with_no_graph(row):
    """§3.4's checks 3, 4 and 5, run the way §13.7 will have to run them: package in, no server.

    The package is dumped to JSON and read back first, because that is the state a verifier
    meets it in (§14) — a handle that only exists on the in-memory object would resolve here and
    nowhere that matters.
    """
    stored = json.dumps(package_of(fact_from_table_row(row),
                                   passages=(passage_of(row),)).model_dump(mode="json"))
    package = StoryEvidencePackage.model_validate(json.loads(stored))

    fact = package.facts_by_evidence_handle()[
        f"ev:{row['passage_id']}:r{row['row_index']}c{row['value_column_index']}"]
    passage = package.primary_passages[0]

    cell = resolve_cell(passage.text, row_index=fact.cell.row_index,
                        column_index=fact.cell.value_column_index)
    assert cell.text == fact.quoted_text
    assert cell.row_label == fact.row_label
    assert resolve_header(passage.text, row_index=fact.cell.period_header_row_index,
                          column_index=fact.cell.period_header_column_index) == fact.column_label


def test_the_quote_that_occurs_27_times_resolves_somewhere_a_search_would_not():
    """The case that motivated the repair, end to end through a package.

    `'7'` occurs 27 times in `…q32023formxex991earningsre.htm#p24`. The old contract asked the
    model to name the fact's evidence by retyping that byte, and §12 then refused it as
    ambiguous — no output satisfied both. The handle resolves to character 1,492 of that passage;
    `passage.find('7')` lands on character 711 — the `7` inside the `170` of the GAAP
    gross-profit row above it, a different metric in a different period — which is what the
    fallback everybody reaches for would have returned.
    """
    package = package_of(fact_from_table_row(SEVEN), passages=(passage_of(SEVEN),))
    passage = package.primary_passages[0]
    fact = package.facts_by_evidence_handle()[
        f"ev:{SEVEN['passage_id']}:r{SEVEN['row_index']}c{SEVEN['value_column_index']}"]

    assert passage.text.count("7") == SEVEN["quote_occurrences_in_passage"] == 27

    cell = resolve_cell(passage.text, row_index=fact.cell.row_index,
                        column_index=fact.cell.value_column_index)
    assert cell.text == "7"
    assert passage.text[cell.char_start:cell.char_end] == "7"
    assert (cell.char_start, passage.text.find("7")) == (1492, 711)


@pytest.mark.parametrize("row", TABLE_ROWS, ids=_table_id)
def test_the_handle_and_the_coordinates_survive_the_round_trip(row):
    """§14 writes the package and reads it back; the handle has to be the same string on the
    other side, because a stored draft's citations name it."""
    fact = fact_from_table_row(row)
    restored = PackagedFact.model_validate_json(fact.model_dump_json())

    assert restored == fact
    assert restored.evidence_handle == fact.evidence_handle
    assert restored.cell == fact.cell


def test_the_handle_is_in_the_serialized_package():
    """Stored, not derived on read — an evidence panel reads the artifact, and a rejection that
    names a handle has to be greppable in `evidence_package.json`."""
    payload = package_of(fact_from_table_row(SEVEN)).model_dump(mode="json")

    assert payload["facts"][0]["evidence_handle"] == (
        f"ev:{SEVEN['passage_id']}:r{SEVEN['row_index']}c{SEVEN['value_column_index']}")
    assert payload["facts"][0]["cell"]["period_header_column_index"] == (
        SEVEN["period_header_column_index"])


# -- what cannot be constructed -------------------------------------------------------------


def test_a_stated_handle_that_names_another_cell_is_refused():
    """The handle is derived from the row's own coordinates, and a caller may not overrule it.

    This is the one thing a stored derived field must not allow: two authorities for one fact,
    with the citation contract believing the string and the evidence panel believing the
    coordinates.
    """
    fact = fact_from_table_row(SEVEN)
    with pytest.raises(ValueError, match="was stated"):
        PackagedFact.model_validate({**fact.model_dump(),
                                     "evidence_handle": f"ev:{SEVEN['passage_id']}:r0c0"})


def test_restating_the_handle_the_row_already_mints_is_accepted():
    """Round-tripping a package restates it on every read, so agreement cannot be an error."""
    fact = fact_from_table_row(SEVEN)
    assert PackagedFact.model_validate(fact.model_dump()) == fact


def test_a_fact_with_no_filed_passage_gets_no_handle():
    """§13.7.2: an `:EvidenceSource` is a leaf with no `PART_OF` edge, so the fact has no
    passage and no cell to name.

    It is given `None` rather than an `ev:<evidence_source_id>:…` token, because V1 refuses that
    citation path outright (`evidence_kind_not_supported_in_v1`) and a citable-looking handle
    for a path that always refuses is worse than a fact the writer cannot cite. Zero such rows
    exist in this corpus; the shape exists so the XBRL lane cannot arrive by widening it.
    """
    fact = PackagedFact(
        observation_id="obs:xbrl:1", metric_id="adjusted_ebitda", metric_label="Adjusted EBITDA",
        period_key="2022Q3", shape="quarter", value=1.0, unit="USD",
        source_lane="xbrl", validation_state="clean", evidence_source_id="evs:xbrl:1")

    assert fact.evidence_handle is None
    assert package_of(fact).facts_by_evidence_handle() == {}


def test_a_negative_coordinate_is_refused():
    """Python resolves `-1` by wrapping to the end of the table, so a negative index is a
    plausible wrong cell rather than an error — `table_cells.resolve_cell` refuses it and this
    refuses it a step earlier, where the package is built."""
    with pytest.raises(ValueError, match="not a grid position"):
        TableCellRef(row_index=-1, value_column_index=3,
                     period_header_row_index=3, period_header_column_index=2)


def test_a_half_stated_cell_is_not_constructible():
    """The four coordinates are one thing. 2,690 observations carry all of them, 14 carry none,
    **0 carry some**; a row holding a `row_index` and no `value_column_index` would look citable
    and resolve to the wrong cell."""
    with pytest.raises(ValueError):
        TableCellRef(row_index=10, value_column_index=7)  # type: ignore[call-arg]


def test_two_facts_claiming_one_cell_are_refused():
    """§3.4 check 7 asks *"is this the handle the package minted for `F`"*, which is a question
    only while handle → fact is a function.

    A collision here is a **defect and not a naming clash**: §1.4 grouped all 2,690 table-backed
    observations by `(passage_id, row_index, value_column_index)` and found 2,690 distinct
    cells, none mapping to two periods and none to two metrics. So two facts claiming one cell
    means one of them was read out of a cell it does not occupy — and a package that shipped
    both would let a sentence bound to the wrong fact pass the check written to catch exactly
    that. Refused at construction rather than deduplicated, because there is no way to choose.
    """
    first = fact_from_table_row(SEVEN)
    second = fact_from_table_row(
        SEVEN, observation_id="obs:adjusted-gross-profit:opendoor:2023Q3:normalized-table:dead",
        period_key="2023Q3", value=84.0)

    with pytest.raises(ValueError, match="is minted for two facts"):
        package_of(first, second)


def test_the_index_skips_nothing_it_can_answer_for():
    """One entry per fact that has a handle, and the fact it was minted for."""
    facts = tuple(fact_from_table_row(row) for row in TABLE_ROWS)
    index = package_of(*facts).facts_by_evidence_handle()

    assert len(index) == len(facts)
    assert {handle: fact.observation_id for handle, fact in index.items()} == {
        fact.evidence_handle: fact.observation_id for fact in facts}


# -- corroboration is not evidence -----------------------------------------------------------


def test_a_corroborating_source_gets_no_handle_of_its_own():
    """§6.1 collapses concordant readings into one canonical fact, and the handle names the
    reading the package actually carries.

    A corroborating id is a second *source*, not a second piece of evidence for the sentence:
    the package holds no passage for it, resolved no cell in it and verified nothing about it.
    Minting a handle for one would put a citable token in the model's slice that §13.7 could
    not check against anything.
    """
    fact = fact_from_table_row(
        SEVEN,
        corroborating_observation_ids=("obs:adjusted-gross-profit:opendoor:2023Q2:"
                                       "normalized-table:9033fdb92fac",),
        corroborating_passage_ids=(
            "norm:0001801169:0001801169-23-000097:open-20230630.htm#p123",),
    )
    index = package_of(fact).facts_by_evidence_handle()

    assert list(index) == [
        f"ev:{SEVEN['passage_id']}:r{SEVEN['row_index']}c{SEVEN['value_column_index']}"]
    assert fact.corroborating_passage_ids[0] not in fact.evidence_handle
