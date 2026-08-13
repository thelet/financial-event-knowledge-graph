"""§1.3's four invariants and §3.3's resolver, against the corpus they were measured on.

**Every table row below is a copy of a live graph row, not an authored example.**
`tests/story/fixtures/table_cells_corpus.json` holds six `(passage_text, coordinates,
expected cell / row label / column label)` tuples pulled from `bolt://127.0.0.1:7687` on
2026-08-13, each chosen for a shape that breaks a different plausible-looking resolver; the
`_why_this_row` field on each records which. The tests read the fixture and run offline —
nothing here opens a connection, and nothing is marked `live` or `neo4j`, because a resolver
that can only be checked with a database running is a resolver nobody checks.

The claim the fixture stands in for was measured on the full population and is recorded in
the fixture's `_provenance.note`: the resolver was run against **all 2,690** table-backed
evidence edges on 2026-08-13 and agreed on the value cell, the row label, the header cell and
`passage_text[char_start:char_end] == text` at 2,690 / 2,690, while the same header lookup at
`value_column_index` agreed at 565 / 2,690. Six rows in the repository plus that run is the
proportionate trade: committing 2,690 passages would be committing the graph.

What is deliberately *not* tested by asserting a shape: there is no `isinstance` check on
`ResolvedCell` and no assertion that the module exports certain names. Every test drives a
resolution and compares it to something the corpus already says.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story.core.table_cells import (
    CellOutOfBounds,
    ResolvedCell,
    resolve_cell,
    resolve_header,
    split_cells,
)

FIXTURE = Path(__file__).parent / "fixtures" / "table_cells_corpus.json"

_FIXTURE = json.loads(FIXTURE.read_text(encoding="utf-8"))
#: The six real rows, in fixture order. Loaded at import so `pytest.mark.parametrize` can id
#: each case by its observation id — a failure names the observation, not "case 3".
CORPUS_ROWS = _FIXTURE["rows"]


def _id(row: dict) -> str:
    return row["observation_id"].split(":", 2)[-1]


# -- §1.3's four invariants, on real rows ---------------------------------------------------


@pytest.mark.parametrize("row", CORPUS_ROWS, ids=_id)
def test_the_value_cell_resolves_to_the_edges_quoted_text(row):
    """Invariant 1: `cells(lines[row_index])[value_column_index] == quoted_text`.

    Measured 2,690 / 2,690. This is the whole of §3.4 check 3 — if it fails, a citation
    handle points at bytes that do not support the fact.
    """
    cell = resolve_cell(
        row["passage_text"],
        row_index=row["row_index"],
        column_index=row["value_column_index"],
    )
    assert cell.text == row["quoted_text"]


@pytest.mark.parametrize("row", CORPUS_ROWS, ids=_id)
def test_the_resolved_cell_carries_the_observations_row_label(row):
    """Invariant 2: `cells(lines[row_index])[0] == row_label`. §3.4 check 4."""
    cell = resolve_cell(
        row["passage_text"],
        row_index=row["row_index"],
        column_index=row["value_column_index"],
    )
    assert cell.row_label == row["row_label"]


@pytest.mark.parametrize("row", CORPUS_ROWS, ids=_id)
def test_the_header_cell_resolves_to_the_observations_column_label(row):
    """Invariant 3, read at the *header* column. §3.4 check 5."""
    header = resolve_header(
        row["passage_text"],
        row_index=row["period_header_row_index"],
        column_index=row["period_header_column_index"],
    )
    assert header == row["column_label"]


@pytest.mark.parametrize("row", CORPUS_ROWS, ids=_id)
def test_the_metric_label_row_is_the_value_row(row):
    """Invariant 4: `metric_label_row_index == row_index`, 2,690 / 2,690.

    Asserted on the fixture rather than on the resolver because it is a property of the
    *graph*, and it is what lets a handle carry one row index instead of two. If a future
    corpus breaks it, this fails and §3.1's handle shape has to grow a second index.
    """
    assert row["metric_label_row_index"] == row["row_index"]


# -- the constraint the whole repair exists for ---------------------------------------------


def test_the_header_column_is_not_the_value_column():
    """Reading the header at the value's own column returns the wrong cell (§1.3's note).

    On the full corpus the two agree on only 565 / 2,690 rows: `$` signs and blank spacer
    cells occupy their own columns, so the value's index has drifted right of its header's by
    the time the row is reached. Five of the six fixture rows have `value_column_index !=
    period_header_column_index`, and for each of those the value-column lookup returns
    something that is *not* the period label — usually `$` or an empty spacer, which is why
    the mistake would pass any "did I get a well-formed cell back" check.

    This is the executable form of "a model cannot retype the row": the two indices are not
    derivable from each other, so they must both be carried.
    """
    differing = [r for r in CORPUS_ROWS
                 if r["value_column_index"] != r["period_header_column_index"]]
    assert len(differing) >= 1, "fixture must exercise the differing-index case"

    for row in differing:
        wrong = resolve_header(
            row["passage_text"],
            row_index=row["period_header_row_index"],
            column_index=row["value_column_index"],
        )
        assert wrong != row["column_label"], row["observation_id"]

    agreeing = [r for r in CORPUS_ROWS
                if r["value_column_index"] == r["period_header_column_index"]]
    assert len(agreeing) >= 1, "fixture must exercise the 565/2,690 minority too"


def test_the_ambiguous_quote_the_repair_exists_for_resolves_to_one_cell():
    """The plan's worked example (§1.2): `quoted_text` `'7'` occurring 27 times.

    §12's writer gate refuses a quote it finds more than once, so this observation had no
    citable form at all under the byte-matching contract. Coordinates give exactly one span,
    and it is *not* the first occurrence — which is what a `passage_text.find(quote)` fallback
    would have returned, silently citing an unrelated cell.
    """
    row = next(r for r in CORPUS_ROWS if r["quoted_text"] == "7")
    passage = row["passage_text"]
    assert passage.count("7") == 27

    cell = resolve_cell(
        passage, row_index=row["row_index"], column_index=row["value_column_index"]
    )
    assert cell.text == "7"
    assert cell.char_start != passage.find("7")
    assert passage[cell.char_start:cell.char_end] == "7"


# -- the span, which is the part a UI highlights --------------------------------------------


@pytest.mark.parametrize("row", CORPUS_ROWS, ids=_id)
def test_the_span_bounds_the_stripped_text_in_the_original_passage(row):
    """`passage_text[char_start:char_end] == cell.text`, on real rows.

    Every corpus cell is padded — `| 1,068 |` — so a span that bounded the *field* rather
    than the stripped text would still look right in a diff and would highlight the padding.
    The character either side of the span is asserted to be a space or a delimiter, which is
    what makes this a test of the trim and not just of a consistent pair of numbers.
    """
    passage = row["passage_text"]
    cell = resolve_cell(
        passage, row_index=row["row_index"], column_index=row["value_column_index"]
    )
    assert passage[cell.char_start:cell.char_end] == cell.text
    assert passage[cell.char_start - 1] in " |"
    assert passage[cell.char_end] in " |"


@pytest.mark.parametrize("row", CORPUS_ROWS, ids=_id)
def test_every_cell_of_every_row_slices_back_to_its_own_text(row):
    """The span identity over the *whole* passage, not just the cited cell.

    Spacer columns, `$` columns and the `| --- |` separator line included: the offsets
    accumulate across lines, so an off-by-one in the line scan would show up on a late row
    and nowhere else. Also pins `split_cells` and `resolve_cell` to the same answer — they
    share a scanner precisely so they cannot drift.
    """
    passage = row["passage_text"]
    lines = passage.split("\n")
    for row_index, line in enumerate(lines):
        texts = split_cells(line)
        for column_index, text in enumerate(texts):
            cell = resolve_cell(passage, row_index=row_index, column_index=column_index)
            assert cell.text == text
            assert passage[cell.char_start:cell.char_end] == cell.text
            assert cell.row_index == row_index
            assert cell.column_index == column_index


def test_an_empty_spacer_cell_resolves_to_an_empty_span_inside_its_own_field():
    """A blank column is a real cell at a real coordinate, not an error and not a skip.

    These tables are full of them — they are half the reason the value column and the header
    column diverge — so a resolver that raised on one, or that returned the neighbouring
    cell's span, would break the very rows this module was written for. The empty span must
    still land *inside* the field it came from, so a highlight of nothing sits in the right
    place rather than at offset 0.
    """
    line = "| a |    | b |"
    assert split_cells(line) == ("a", "", "b")

    cell = resolve_cell(line, row_index=0, column_index=1)
    assert cell.text == ""
    assert cell.char_start == cell.char_end
    assert line.index("|", line.index("a")) < cell.char_start < line.rindex("|", 0, line.index("b"))


def test_a_cell_with_no_padding_keeps_the_whole_field():
    """The trim must not eat a character when there is nothing to trim."""
    cell = resolve_cell("|a|bb|", row_index=0, column_index=1)
    assert (cell.text, cell.char_start, cell.char_end) == ("bb", 3, 5)


def test_spans_on_a_later_line_are_absolute_offsets_into_the_passage():
    """Offsets are into `passage_text`, never into the line, and count the newlines."""
    passage = "| a |\n| --- |\n| bb |"
    cell = resolve_cell(passage, row_index=2, column_index=0)
    assert cell.text == "bb"
    assert passage[cell.char_start:cell.char_end] == "bb"
    assert cell.char_start == passage.rindex("bb")


# -- the grid rule ---------------------------------------------------------------------------


def test_the_outer_delimiters_are_dropped_and_the_inner_fields_are_kept():
    """§1.3's splitting rule, including a cell whose content contains no digits."""
    assert split_cells("| Adjusted Gross Profit (Loss) |  | $ | 84 |") == (
        "Adjusted Gross Profit (Loss)", "", "$", "84",
    )


def test_a_line_without_outer_delimiters_keeps_its_first_and_last_field():
    """Dropping the outer empties unconditionally would eat a genuinely empty first column.

    No corpus line has this shape — 3,002 / 3,002 open and close with `|` *(verified
    2026-08-13)* — so this is a guard on the rule, not on the corpus: the drop is conditional
    on the delimiters actually being there.
    """
    assert split_cells("a|b") == ("a", "b")
    assert split_cells("|a|b") == ("a", "b")
    assert split_cells("a|b|") == ("a", "b")


def test_a_row_of_nothing_but_spacers_is_a_row_of_empty_cells():
    """The corpus opens several passages with one; it has cells, they are just all empty."""
    assert split_cells("|  |  |  |") == ("", "", "")


def test_rows_are_the_lines_of_the_passage_including_the_separator():
    """`row_index` counts every line, `| --- |` included — which is why the graph's header
    row for a table whose first two lines are a blank row and a rule is not row 0."""
    passage = "|  |  |\n| --- |\n| Q1 | 3 |"
    assert resolve_cell(passage, row_index=1, column_index=0).text == "---"
    assert resolve_cell(passage, row_index=2, column_index=1).text == "3"


# -- out of bounds (§3.4 check 2) -------------------------------------------------------------


PASSAGE = "| a | b |\n| c | d |"


@pytest.mark.parametrize(
    "row_index, column_index",
    [(2, 0), (99, 0), (-1, 0)],
    ids=["one past the last row", "far past the last row", "negative row"],
)
def test_a_row_outside_the_grid_raises_cell_out_of_bounds(row_index, column_index):
    with pytest.raises(CellOutOfBounds):
        resolve_cell(PASSAGE, row_index=row_index, column_index=column_index)


@pytest.mark.parametrize(
    "row_index, column_index",
    [(0, 2), (0, 99), (0, -1)],
    ids=["one past the last column", "far past the last column", "negative column"],
)
def test_a_column_outside_the_grid_raises_cell_out_of_bounds(row_index, column_index):
    with pytest.raises(CellOutOfBounds):
        resolve_cell(PASSAGE, row_index=row_index, column_index=column_index)


def test_a_negative_index_is_refused_rather_than_wrapping_to_the_far_end():
    """Python would resolve `-1` to the last row and hand back a plausible wrong cell.

    A handle whose coordinates went negative is a bug or a forged citation, and either way
    §3.4 check 2 must refuse it. The assertion is that the *last* cell is not what comes
    back — an exception type alone would not catch a resolver that wrapped and then happened
    to raise for another reason.
    """
    last = resolve_cell(PASSAGE, row_index=1, column_index=1)
    assert last.text == "d"
    with pytest.raises(CellOutOfBounds):
        resolve_cell(PASSAGE, row_index=-1, column_index=-1)


def test_out_of_bounds_names_the_coordinate_and_the_extent_it_missed():
    """The message is read by a human looking at a rejected citation, so it says what the
    grid actually holds rather than only that something was wrong."""
    with pytest.raises(CellOutOfBounds, match="row 7.*2 lines"):
        resolve_cell(PASSAGE, row_index=7, column_index=0)
    with pytest.raises(CellOutOfBounds, match="column 5.*row 0.*2 cells"):
        resolve_cell(PASSAGE, row_index=0, column_index=5)


def test_resolve_header_refuses_the_same_coordinates_resolve_cell_refuses():
    """The header path is not a second, laxer entry point into the grid."""
    with pytest.raises(CellOutOfBounds):
        resolve_header(PASSAGE, row_index=9, column_index=0)
    with pytest.raises(CellOutOfBounds):
        resolve_header(PASSAGE, row_index=0, column_index=9)


# -- the value type ---------------------------------------------------------------------------


def test_a_resolved_cell_cannot_be_mutated_after_the_fact():
    """Frozen because a `ResolvedCell` is evidence: it travels into a package, a prompt and a
    rejection message, and a span that could be edited downstream is a span nobody can cite."""
    cell = resolve_cell(PASSAGE, row_index=0, column_index=0)
    with pytest.raises(Exception):
        cell.text = "z"  # type: ignore[misc]


def test_the_same_coordinates_resolve_to_an_equal_cell_every_time():
    """Value equality, which is what lets §3.4 check 7 compare a resolved handle to the one
    the package minted without caring which call produced it."""
    first = resolve_cell(PASSAGE, row_index=1, column_index=0)
    second = resolve_cell(PASSAGE, row_index=1, column_index=0)
    assert first == second
    assert first == ResolvedCell(
        text="c", char_start=first.char_start, char_end=first.char_end,
        row_index=1, column_index=0, row_label="c",
    )
