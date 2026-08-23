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

**The last section is `story.core.evidence_slice`**, which owns the other half of the same
question: the handle names a fact, and the resolver says where in the passage that fact's
evidence sits. It moved out of `story/stages/generation/writer.py` unchanged so the draft
compiler can resolve the same span under its own vocabulary
(`docs/2026-08-23-deterministic-draft-compiler/01-TARGET-ARCHITECTURE.md` §4.5), and it is tested
here — beside the handle it resolves — rather than in a file of its own. The writer's citation
tests are untouched and still green, which is what says the move cost nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.evidence_slice import (
    EvidenceUnresolved,
    EvidenceUnresolvedReason,
    ResolvedEvidence,
    occurrences,
    passages_backing_facts,
    span_for_handle,
)
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

#: One of the 14 narrative observations, named rather than indexed: the sentence that evidences
#: two facts at once (§3.1's correction above reads it), which is a real quote out of a real
#: filing and therefore a fair thing to look for in a passage that does not contain it.
NARRATIVE_SENTENCE = next(
    row for row in NARRATIVE_ROWS
    if row["quoted_text"].startswith("Adjusted gross profit was $279 million"))

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


# -- the slice and the span, in `story.core.evidence_slice` ----------------------------------

#: The demo candidate's own package, as `tests/story/test_story_demo.py` replays it: two facts
#: read out of one real filing table. Used here rather than a package assembled from the corpus
#: rows, because it is the exact object the pipeline hands the writer.
DEMO_PACKAGE = StoryEvidencePackage.model_validate_json(
    (FIXTURES / "story_demo" / "evidence_package.json").read_text(encoding="utf-8"))


def test_the_slice_is_the_passages_the_packages_facts_were_read_from():
    """§10.2.1 point 3, on the package the demo actually runs.

    Both facts were read from one passage, so the slice is one passage and not two — the
    de-duplication is not a detail of the loop, it is what makes the prompt a function of the
    package.
    """
    slice_ = passages_backing_facts(DEMO_PACKAGE)

    assert [passage.passage_id for passage in slice_] == [
        "norm:0001801169:0001801169-22-000108:open-20220930.htm#p139"]
    assert {fact.passage_id for fact in DEMO_PACKAGE.facts} == {slice_[0].passage_id}


def test_a_passage_no_packaged_fact_was_read_from_is_not_in_the_slice():
    """The counter-evidence case, stated as the property rather than as the loop.

    §10's counter-evidence join is at *document* grain, so a counter-evidence passage that
    evidences no packaged fact is citable-looking and unusable — §13.7 refuses citing one as
    support. It is left out here, which is the same answer §13 gives.
    """
    orphan = PackagedPassage(
        passage_id="norm:0001801169:0001801169-22-000108:open-20220930.htm#p140",
        document_id="norm:0001801169:0001801169-22-000108:open-20220930.htm",
        text="| Homes purchased |  | 8,380 |", char_count=30,
        passage_kind="table", role=EvidenceRole.COUNTER_EVIDENCE)
    widened = DEMO_PACKAGE.model_copy(update={"counter_evidence": (orphan,)})

    assert orphan.passage_id not in {p.passage_id for p in passages_backing_facts(widened)}


def test_the_demo_packages_facts_resolve_to_the_cells_they_were_read_from():
    """The claim the resolver exists to make: the span is the fact's *own* bytes.

    Driven through the slice rather than through a passage handed in beside the fact, because
    that is the path a citation takes — handle → fact → the passage the slice carries → span.
    """
    by_id = {passage.passage_id: passage for passage in passages_backing_facts(DEMO_PACKAGE)}

    resolved = {}
    for fact in DEMO_PACKAGE.facts:
        located = span_for_handle(fact, by_id[fact.passage_id])
        assert isinstance(located, ResolvedEvidence)
        passage = by_id[fact.passage_id]
        assert passage.text[located.char_start:located.char_end] == fact.quoted_text
        resolved[fact.evidence_handle] = passage.text[located.char_start:located.char_end]

    assert resolved == {
        "ev:norm:0001801169:0001801169-22-000108:open-20220930.htm#p139:r11c2": "3.3",
        "ev:norm:0001801169:0001801169-22-000108:open-20220930.htm#p139:r5c2": "(12.6)",
    }


@pytest.mark.parametrize("row", TABLE_ROWS, ids=_table_id)
def test_the_resolver_returns_the_facts_own_bytes_for_every_corpus_row(row):
    """The six rows chosen to break a different plausible resolver, driven through the one that
    ships. `'7'` is the case that matters: a search returns character 711 and the fact's own
    cell is at 1,492."""
    fact = fact_from_table_row(row)
    located = span_for_handle(fact, passage_of(row))

    assert isinstance(located, ResolvedEvidence)
    assert row["passage_text"][located.char_start:located.char_end] == row["quoted_text"]


@pytest.mark.parametrize("row", NARRATIVE_ROWS, ids=_narrative_id)
def test_a_narrative_fact_resolves_to_the_sentence_the_edge_quoted(row):
    """The other path, over all 14 narrative observations.

    **The passage is reconstructed and says so.** `narrative_evidence_corpus.json` carries each
    row's quote and the offset it was found at, not the passage text — so the text here is the
    real quote at its real offset with filler either side. What is asserted is therefore the
    resolver's claim and not the fixture's: searching for the package's own quote lands on the
    offset the graph recorded, which is the only reason a narrative fact needs no coordinates.
    """
    text = "." * row["quote_char_start"] + row["quoted_text"] + " ...and the paragraph goes on."
    passage = PackagedPassage(
        passage_id=row["passage_id"], document_id=row["passage_id"].rsplit("#p", 1)[0],
        text=text, char_count=len(text), passage_kind="narrative",
        role=EvidenceRole.PRIMARY_SUPPORT)

    located = span_for_handle(fact_from_narrative_row(row), passage)

    assert located == ResolvedEvidence(row["quote_char_start"],
                                       row["quote_char_start"] + len(row["quoted_text"]))


# -- the four reasons a handle names no span, each reachable ---------------------------------


def test_coordinates_the_passage_text_does_not_reach_are_unresolved_and_say_which_row():
    """`CELL_OUT_OF_BOUNDS`, and it is **not** a model failure — it cannot be one.

    The coordinates come off the `PackagedFact`, so this is a package and the passage text it
    carries disagreeing about the table: a stored package replayed against a re-extracted
    corpus. The `table_cells.CellOutOfBounds` text is carried through as the detail, because
    *which* row is outside *how many* is the whole of what makes it diagnosable.
    """
    drifted = fact_from_table_row(SEVEN, cell=TableCellRef(
        row_index=20, value_column_index=SEVEN["value_column_index"],
        period_header_row_index=SEVEN["period_header_row_index"],
        period_header_column_index=SEVEN["period_header_column_index"]))

    located = span_for_handle(drifted, passage_of(SEVEN))

    assert isinstance(located, EvidenceUnresolved)
    assert located.reason is EvidenceUnresolvedReason.CELL_OUT_OF_BOUNDS
    assert located.detail == "row 20 is outside the passage, which has 20 lines"


def test_a_spacer_column_is_a_real_cell_holding_nothing_and_resolves_to_no_span():
    """`CELL_EMPTY`, on a real spacer in a real filing table.

    Column 1 of `Adjusted Gross Profit (Loss)` is a well-formed coordinate whose cell is the
    empty string — these flattened tables have many. `quoted_text` is non-empty on 2,704 / 2,704
    evidence edges, so a fact resolving here means its coordinates do not name the value it was
    read from, and there is no span: `PassageCitation` requires `char_end > char_start`.

    Kept apart from the out-of-bounds reason in core even though the writer maps both onto
    `evidence_handle_out_of_bounds`, because the two are different defects and a caller that
    wants to say so should not have to parse a string to tell them apart.
    """
    spacer = fact_from_table_row(SEVEN, cell=TableCellRef(
        row_index=SEVEN["row_index"], value_column_index=1,
        period_header_row_index=SEVEN["period_header_row_index"],
        period_header_column_index=SEVEN["period_header_column_index"]))

    assert resolve_cell(SEVEN["passage_text"], row_index=SEVEN["row_index"],
                        column_index=1).text == ""

    located = span_for_handle(spacer, passage_of(SEVEN))

    assert isinstance(located, EvidenceUnresolved)
    assert located.reason is EvidenceUnresolvedReason.CELL_EMPTY
    assert located.detail.endswith("is empty; there is no span to cite")


def test_a_narrative_quote_the_passage_no_longer_holds_is_unresolved():
    """`NARRATIVE_QUOTE_ABSENT` — the package's own quote, and the package's own passage, no
    longer agreeing. The model never wrote either string, so this is a defect in the evidence
    and the caller must refuse rather than pick a nearby span."""
    absent = fact_from_narrative_row(NARRATIVE_SENTENCE)

    located = span_for_handle(absent, passage_of(SEVEN))

    assert isinstance(located, EvidenceUnresolved)
    assert located.reason is EvidenceUnresolvedReason.NARRATIVE_QUOTE_ABSENT
    assert NARRATIVE_SENTENCE["quoted_text"] not in SEVEN["passage_text"]


def test_a_narrative_quote_naming_two_places_in_its_own_passage_is_unresolved():
    """`NARRATIVE_QUOTE_AMBIGUOUS`, and this is the whole of what it now means.

    All **14 / 14** narrative quotes are whole sentences occurring exactly once in their passage,
    so this does not fire on today's corpus — the fact here is built with a short quote against
    a real table passage to reach the branch at all. It is kept because the requirement is real:
    a narrative fact has no coordinate, so a quote naming two places resolves to no span.

    What is gone is the branch that made the old retyped-quote contract unsatisfiable: a
    **table** fact never reaches this path, and `'7'` — 27 occurrences — resolves by coordinate
    two tests above.
    """
    passage = DEMO_PACKAGE.primary_passages[0]
    assert passage.text.count("Gross Margin") == 2

    ambiguous = fact_from_narrative_row(NARRATIVE_SENTENCE, quoted_text="Gross Margin")

    located = span_for_handle(ambiguous, passage)

    assert isinstance(located, EvidenceUnresolved)
    assert located.reason is EvidenceUnresolvedReason.NARRATIVE_QUOTE_AMBIGUOUS
    assert "occurs 2 times" in located.detail


def test_every_reason_the_resolver_can_give_is_reached_by_a_test_here():
    """The enum is closed, so a fifth member added without a case above is a hole this catches.

    Named for what it holds rather than asserted as a count of a set: the four reasons are the
    four the resolver distinguishes, and the writer maps each onto a §12 code.
    """
    assert set(EvidenceUnresolvedReason) == {
        EvidenceUnresolvedReason.CELL_OUT_OF_BOUNDS,
        EvidenceUnresolvedReason.CELL_EMPTY,
        EvidenceUnresolvedReason.NARRATIVE_QUOTE_ABSENT,
        EvidenceUnresolvedReason.NARRATIVE_QUOTE_AMBIGUOUS,
    }


# -- the scan the resolver and the compiler share --------------------------------------------


def test_the_scan_is_case_sensitive_and_finds_every_start():
    """`occurrences` is public in core because the compiler needs the same scan.

    Case-sensitivity is the load-bearing half: a binding declares the characters of its own
    sentence, and a case-insensitive match would let `"3.3%"` bind a span the sentence spells
    differently — §13.1 would then compare a numeral the draft never wrote.
    """
    passage = DEMO_PACKAGE.primary_passages[0].text

    starts = occurrences(passage, "Gross Margin")
    assert len(starts) == 2
    assert all(passage[start:start + 12] == "Gross Margin" for start in starts)
    assert occurrences(passage, "gross margin") == ()
    assert occurrences(passage, "") == ()
