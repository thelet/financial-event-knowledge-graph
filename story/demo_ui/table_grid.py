"""A table passage as the grid it was flattened from, with the cited cell placed in it.

Responsibility: turn a `PackagedPassage` whose `passage_kind` is `table` into rows and cells a
panel can draw, and place a fact's or a citation's evidence inside that grid at the coordinates
the package minted. Pure — a passage, a fact, a citation in, dictionaries out. No HTTP, no
graph, no run state, and **no second cell resolver**: `story.core.table_cells.split_cells` and
`resolve_cell` are imported, because a panel that highlighted a cell the verifier did not check
would be a third opinion about where the evidence is.

**Why a bare quote was not enough, in the corpus's own numbers.** Before this module the
evidence panel rendered a citation as `“(12.6)”  [431–437]` — a numeral and two offsets, with
no way to see which line item it sat on or which period stood over it. That is the same defect
TABLE_CELL_CITATIONS §1.2 measured on the writer's side: `EVIDENCED_BY.quoted_text` has a
median length of 4 characters and occurs more than once in its own passage for 523 of 2,704
observations, so the quote alone does not identify the evidence *to a reader* either. What
makes a number mean something is its row label and the period header above it, and both are
one `resolve_cell` away.

**Measured over the live graph (verified 2026-08-18, `bolt://…:7687`, graph run
`graph-v1-0483dc6b4b10`), because all three numbers decided how this renders:**

| Quantity | Value |
| --- | ---: |
| `:Passage` rows with `passage_kind = 'table'` | 503 |
| …distinct `passage_kind` values in the whole graph | 2 (`table`, `narrative`) |
| Widest table | **46** columns |
| Median table width | 13 columns |
| Tables at 20 columns or wider | 111 / 503 |
| Passages whose lines disagree about how many cells they have | **0 / 503** |
| Cells that are empty once stripped | **142,056 / 197,183 — 72.0%** |

Three consequences, and each is a rendering decision rather than a preference:

* **Every column is drawn, including the empty ones.** Seventy-two percent of cells hold
  nothing, and it is tempting to collapse them. A spacer column is a real column: the handle
  `…:r5c2` counts it, and `period_header_column_index` differs from `value_column_index` on
  2,125 of 2,690 table-backed observations *precisely because* `$` signs and blanks occupy
  columns of their own. Collapsing them would renumber the grid and make the handle unreadable
  against the picture it names.
* **The grid carries its own index ruler.** With 46 columns and most of them blank, "find
  column 2" is not something an eye does by counting; the row index and the column index are
  emitted as data so the panel can print them along the edges. The ruler is what turns
  `r5c2` — the string a refusal message quotes — into a place a reader can point at.
* **Rows are not ragged, but nothing here assumes it.** `column_count` is the widest row and
  short rows simply end; a grid built from a re-extracted corpus that did go ragged renders as
  ragged rather than throwing.

**Offsets on the wire are absolute into the full `:Passage.text`**, which is the coordinate
system `PassageCitation.char_start` already uses, so a panel can compare the two without
rebasing. `PackagedPassage.char_start` is the offset of `text[0]` and is added here.

**An excerpted passage gets a grid and no marks, and that is the honest answer.** §10.2.1
point 2 windows explanatory and counter-evidence passages to ±400 characters, and a window cut
out of a flattened table has its own row and column numbering — `resolve_cell` would return a
perfectly well-formed *wrong* cell for the package's coordinates, in silence. `coordinates_apply`
is false on such a grid and every mark on it reports why it could not be placed. A passage a
fact is bound to is never excerpted (`PackagedPassage`'s own docstring), so this branch is a
guard against a shape the corpus does not hold today rather than a case it exercises.

**What this module does not do.** It states no verdict. `matches_*` on a mark is a *displayed*
comparison between what the package declares and what its own passage text resolves to, so a
reader can see the disagreement; §13.7's checks 3–5 and `evidence_cell_span_mismatch` are the
authority on whether that disagreement refuses a draft, and they run in
`story/stages/verification/citations.py` whether or not anything renders.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from story.core.table_cells import CellOutOfBounds, resolve_cell, split_cells

#: The `:Passage.passage_kind` this module draws. The graph holds exactly two values —
#: `table` (503) and `narrative` (8,273), verified 2026-08-18 — so an unrecognised kind is
#: rendered as prose rather than guessed at.
TABLE_KIND = "table"

#: A markdown rule row (`| --- | --- | …`). Recognised so the panel can draw it as a rule
#: instead of as a row of dashes — and **not** skipped: it occupies a row index, and
#: `row_index` counts every line of the passage including this one, which is what makes
#: `r5` in a handle mean line 5 of `passage_text.split("\n")`.
_RULE_CELL = re.compile(r"^-{3,}$")

#: Why a mark has no place in the grid. One sentence each, written here so a panel prints the
#: server's reason rather than composing its own.
NO_CELL = ("this fact carries no table coordinates, so nothing places it in a grid — its "
           "evidence is a run of prose located by span")
NOT_A_TABLE = "this passage is not a flattened table, so it has no grid to place a cell in"
EXCERPTED = ("this passage is carried as a ±400-character window (§10.2.1), so its rows and "
             "columns are not the ones the package's coordinates were minted against")


def grid_of(passage: Any, *, marks: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any] | None:
    """The passage's grid, with each mark's cell flagged in place — or `None` for prose.

    `marks` are the rows `fact_mark` and `citation_mark` return. They are passed in rather
    than looked up because this module holds no package: the join between a passage and the
    facts read out of it belongs to `package_view`, and the join between a passage and the
    citations pointing into it belongs to the endpoint that has the draft.

    A cell knows which marks land on it (`marked_by`) and which marks read it as their period
    header (`header_for`), both as indices into `marks`. Indices rather than copies so a wide
    table does not carry the same mark forty-six times, and so the panel's "jump to the cited
    cell" and its mark list are keyed on one thing.
    """
    if getattr(passage, "passage_kind", None) != TABLE_KIND:
        return None

    text = passage.text
    origin = int(getattr(passage, "char_start", 0) or 0)
    excerpted = bool(getattr(passage, "excerpted", False))

    marked: dict[tuple[int, int], list[int]] = {}
    headers: dict[tuple[int, int], list[int]] = {}
    header_rows: set[int] = set()
    for index, mark in enumerate(marks):
        if not mark.get("resolved"):
            continue
        marked.setdefault((mark["row_index"], mark["column_index"]), []).append(index)
        header_row = mark.get("header_row_index")
        if header_row is None:
            continue
        headers.setdefault((header_row, mark["header_column_index"]), []).append(index)
        header_rows.add(header_row)

    rows: list[dict[str, Any]] = []
    for row_index, line in enumerate(text.split("\n")):
        # Every cell goes back through `resolve_cell` rather than through a scanner of this
        # module's own. It re-walks the passage per cell, which is a few thousand string
        # operations on a 2 KB passage and buys the one property that matters: the panel and
        # the verifier cannot disagree about where a cell begins, because there is one
        # function that decides and this is not it.
        texts = split_cells(line)
        cells = []
        for column_index in range(len(texts)):
            resolved = resolve_cell(text, row_index=row_index, column_index=column_index)
            cells.append({
                "column_index": column_index,
                "text": resolved.text,
                "empty": resolved.text == "",
                "is_row_label": column_index == 0,
                # Absolute into the full `:Passage.text` — see the module docstring.
                "char_start": origin + resolved.char_start,
                "char_end": origin + resolved.char_end,
                "marked_by": marked.get((row_index, column_index), []),
                "header_for": headers.get((row_index, column_index), []),
            })
        rows.append({
            "row_index": row_index,
            "label": texts[0] if texts else "",
            "is_rule": _is_rule(texts),
            "is_period_header": row_index in header_rows,
            "cells": cells,
        })

    column_count = max((len(row["cells"]) for row in rows), default=0)
    return {
        "passage_id": passage.passage_id,
        "row_count": len(rows),
        "column_count": column_count,
        "column_indices": list(range(column_count)),
        "empty_cells": sum(1 for row in rows for cell in row["cells"] if cell["empty"]),
        "cell_count": sum(len(row["cells"]) for row in rows),
        "char_origin": origin,
        "coordinates_apply": not excerpted,
        "coordinates_note": EXCERPTED if excerpted else (
            "row and column indices are positions in this passage's own text, and are the "
            "coordinates an evidence id names"),
        "rows": rows,
    }


def fact_mark(fact: Any, passage: Any) -> dict[str, Any]:
    """Where one packaged fact's value sits in its passage's grid.

    The handle travels on the mark because it is the model's citation vocabulary since S4: a
    refusal message names `ev:…:r5c2` and nothing else, so the panel must print that string
    beside the cell it means or a reader cannot match the two.
    """
    return _placed(
        {
            "kind": "fact",
            "fact_id": fact.observation_id,
            "evidence_handle": fact.evidence_handle,
            "label": f"{fact.metric_label} · {fact.period_key}",
            "sentence_index": None,
            "sentence_text": "",
            "declared_row_label": fact.row_label or "",
            "declared_column_label": fact.column_label or "",
            "declared_value_text": fact.quoted_text or "",
            "span_char_start": None,
            "span_char_end": None,
            "cited_text": "",
            "span_resolved": None,
        },
        passage,
        fact.cell,
    )


def citation_mark(
    citation: Any, passage: Any, fact: Any, *, sentence_index: int, sentence_text: str
) -> dict[str, Any]:
    """Where one citation points, resolved two ways so a disagreement is visible.

    A `PassageCitation` carries **both** an `evidence_handle` and a character span, and §13.7's
    `evidence_cell_span_mismatch` exists because those two can name different parts of the
    passage — the handle verified, the highlighted bytes not. The panel must not choose one:
    the cell comes from the handle's fact, `span_char_start`/`span_char_end` come from the
    citation, and `span_matches_cell` says whether they are the same place.

    `fact` is the fact the handle resolves to, or `None` when it resolves to nothing — which is
    §3.4 check 1, `unresolvable_evidence_handle`, and renders as a citation with no cell rather
    than as a missing row.
    """
    handle = getattr(citation, "evidence_handle", None)
    # The bytes the citation's own span covers, rebased: citation offsets are absolute into the
    # full `:Passage.text` and `PackagedPassage.char_start` is the offset of `text[0]`. Sliced
    # here rather than at the endpoint so the span and the cell it is compared against are read
    # out of one function.
    origin = int(getattr(passage, "char_start", 0) or 0)
    start = citation.char_start - origin
    end = citation.char_end - origin
    inside = 0 <= start < end <= len(passage.text)
    mark = _placed(
        {
            "kind": "citation",
            "fact_id": None if fact is None else fact.observation_id,
            "evidence_handle": handle,
            "label": ("cited evidence" if fact is None
                      else f"{fact.metric_label} · {fact.period_key}"),
            "sentence_index": sentence_index,
            "sentence_text": sentence_text,
            "declared_row_label": "" if fact is None else (fact.row_label or ""),
            "declared_column_label": "" if fact is None else (fact.column_label or ""),
            "declared_value_text": "" if fact is None else (fact.quoted_text or ""),
            "span_char_start": citation.char_start,
            "span_char_end": citation.char_end,
            "cited_text": passage.text[start:end] if inside else "",
            "span_resolved": inside,
        },
        passage,
        None if fact is None else fact.cell,
    )
    if fact is None:
        mark["unplaced_reason"] = (
            "no fact in this package minted this evidence id, so it names no cell"
            if handle else
            "this citation states no evidence id, so there is no cell for it to name")
    mark["span_matches_cell"] = (
        None if not mark["resolved"]
        else (citation.char_start == mark["char_start"]
              and citation.char_end == mark["char_end"]))
    return mark


def marks_by_passage(
    marks: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group marks by the passage they were built against, preserving order.

    Here rather than at each call site because `grid_of` takes a *list* whose positions are
    what `marked_by` indexes, so the grouping and the grid must be built from the same
    sequence or the indices point at the wrong mark.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for mark in marks:
        grouped.setdefault(mark["passage_id"], []).append(dict(mark))
    return grouped


# ---------------------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------------------


def _placed(base: dict[str, Any], passage: Any, cell: Any) -> dict[str, Any]:
    """Resolve `cell` inside `passage` and fold the answer into `base`.

    One function for both mark kinds, because "the coordinates do not resolve" has exactly one
    set of causes and a second copy of them would drift. Every failure leaves the mark in the
    same shape — `resolved: false` and a sentence — so a panel never branches on which key is
    present.
    """
    mark = {
        **base,
        "passage_id": passage.passage_id,
        "document_id": passage.document_id,
        "row_index": None,
        "column_index": None,
        "header_row_index": None,
        "header_column_index": None,
        "row_label": "",
        "column_header": "",
        "text": "",
        "char_start": None,
        "char_end": None,
        "resolved": False,
        "unplaced_reason": "",
        "matches_row_label": None,
        "matches_column_label": None,
        "matches_value": None,
        "span_matches_cell": None,
    }
    if cell is None:
        mark["unplaced_reason"] = NO_CELL
        return mark
    if getattr(passage, "passage_kind", None) != TABLE_KIND:
        mark["unplaced_reason"] = NOT_A_TABLE
        return mark
    if getattr(passage, "excerpted", False):
        mark["unplaced_reason"] = EXCERPTED
        return mark

    origin = int(getattr(passage, "char_start", 0) or 0)
    try:
        resolved = resolve_cell(passage.text,
                                row_index=cell.row_index,
                                column_index=cell.value_column_index)
        header = resolve_cell(passage.text,
                              row_index=cell.period_header_row_index,
                              column_index=cell.period_header_column_index)
    except CellOutOfBounds as off_grid:
        # The coordinates are the package's own, so this is the package and the passage text it
        # carries disagreeing about the table — §13.7's `evidence_handle_out_of_bounds`. Shown
        # rather than swallowed: a panel that dropped the mark would render an unmarked grid
        # and a reader would read that as "nothing was cited".
        mark["unplaced_reason"] = str(off_grid)
        return mark

    mark.update({
        "row_index": resolved.row_index,
        "column_index": resolved.column_index,
        "header_row_index": cell.period_header_row_index,
        "header_column_index": cell.period_header_column_index,
        "row_label": resolved.row_label,
        "column_header": header.text,
        "text": resolved.text,
        "char_start": origin + resolved.char_start,
        "char_end": origin + resolved.char_end,
        "resolved": True,
    })
    # Displayed comparisons, not verdicts — see the module docstring. `None` where the package
    # declares nothing to compare against, which is not the same answer as `False`.
    mark["matches_row_label"] = (
        None if not mark["declared_row_label"]
        else mark["declared_row_label"] == resolved.row_label)
    mark["matches_column_label"] = (
        None if not mark["declared_column_label"]
        else mark["declared_column_label"] == header.text)
    mark["matches_value"] = (
        None if not mark["declared_value_text"]
        else mark["declared_value_text"] == resolved.text)
    return mark


def _is_rule(texts: Sequence[str]) -> bool:
    """A `| --- | --- |` separator: at least one dashed cell and nothing else non-empty."""
    dashed = [text for text in texts if text]
    return bool(dashed) and all(_RULE_CELL.match(text) for text in dashed)


__all__ = [
    "EXCERPTED",
    "NOT_A_TABLE",
    "NO_CELL",
    "TABLE_KIND",
    "citation_mark",
    "fact_mark",
    "grid_of",
    "marks_by_passage",
]
