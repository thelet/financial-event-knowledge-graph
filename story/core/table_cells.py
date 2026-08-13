"""Coordinates → the cell they name, inside a flattened markdown passage (§3.3).

Responsibility: given a `:Passage`'s text and a pair of grid indices, return the cell's
stripped content and the exact character span that content occupies in the *original*
passage bytes, so an evidence panel can highlight it and a verifier can compare it. Pure —
no graph, no I/O, standard library only, nothing from `story.stages` — because §3.4's checks
2 through 5 run inside the verifier and must be assertable with no server running.

**Why this module exists at all.** `EVIDENCED_BY.quoted_text` is a bare cell value of median
4 characters, and for 523 of 2,704 observations (19.3%) it occurs more than once in its own
passage — worst case 32 times. Locating evidence by searching for the quote is therefore not
merely fragile, it is *unsatisfiable*: §12's writer gate refuses a quote it finds twice, so
the pathological rows had no citable form at all. The coordinates that do identify the cell
are already on the `:Observation` and were simply never carried into `story/`.

**The constraint that makes a model unable to retype the row, recorded because it is the
whole reason coordinates must be carried rather than inferred.** The column holding the
value and the column holding that value's period header are *different indices*. Flattened
SEC tables put `$` signs and blank spacer cells in their own columns, so a row reads

    | Adjusted Gross Profit (Loss) |  | $ | 84 |  |  | $ | 7 |  |  | $ | (102) | … |

and the header row above it is spaced differently again. Measured over **all 2,690**
table-backed evidence edges in the live graph *(verified 2026-08-13, `bolt://127.0.0.1:7687`,
graph run in `data/graph_runs`)*:

| Lookup | Holds |
| --- | ---: |
| `cells(lines[row_index])[value_column_index] == quoted_text` | 2,690 / 2,690 |
| `cells(lines[row_index])[0] == row_label` | 2,690 / 2,690 |
| `cells(lines[period_header_row_index])[period_header_column_index] == column_label` | 2,690 / 2,690 |
| `cells(lines[period_header_row_index])[`**`value_column_index`**`] == column_label` | **565 / 2,690** |

That last row is the point. Reading the header at the value's own column index is wrong
79% of the time, which is why `period_header_column_index` is a carried property and why
`resolve_header` takes the header column as an argument rather than deriving it from the
cell's. A caller that passes the value column here gets a `$` or an empty string, not a
period label — and it will get it silently, because that is a perfectly well-formed cell.
`tests/story/test_story_table_cells.py::test_the_header_column_is_not_the_value_column`
holds that measurement executable.

**Grid shape, measured rather than assumed.** All 3,002 lines of the 144 distinct
table-backed passages begin and end with `|`; none contains an escaped `\\|`, a tab, a
carriage return or any whitespace other than the ASCII space *(verified 2026-08-13 over the
same pull)*. `split_cells` still handles a line without the delimiters at either end,
because a passage is source-derived text and a shape holding at 3,002/3,002 today is not a
guarantee about the next corpus — but it does not attempt to parse escapes, which would be
inventing a rule the corpus has never exercised.

Row indices count *every* line of the passage, the `| --- |` separator included. They are
positions in `passage_text.split("\\n")`, not positions among "data rows", and the graph's
`row_index` is that same position — which is what makes `metric_label_row_index ==
row_index` on 2,690/2,690.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The flattened-table delimiter. Named because it is looked for in three places — the scan,
#: and the two end tests that decide whether the outer empties are the line's own or a real
#: first and last column — and those three must never disagree about what a boundary is.
CELL_DELIMITER = "|"


class CellOutOfBounds(Exception):
    """A row or column index that names no cell in the passage's grid (§3.4 check 2).

    Its own exception type rather than `IndexError` because a handle arriving from outside —
    a model's citation, a stored package replayed against a re-extracted corpus — is expected
    to be wrong sometimes, and the verifier must distinguish "this handle does not resolve"
    from a bug in the resolver. Callers turn it into `evidence_handle_out_of_bounds`.
    """


@dataclass(frozen=True, slots=True)
class ResolvedCell:
    """One cell of a flattened table, and where its text physically sits in the passage.

    `char_start`/`char_end` bound the **stripped** text, so
    `passage_text[cell.char_start:cell.char_end] == cell.text` holds exactly, including for
    a cell padded with spaces on both sides. A UI highlighting the span therefore highlights
    the numeral and not the padding, and a verifier comparing `quoted_text` to the slice and
    to `text` gets the same answer either way.

    An empty cell — a spacer column, of which these tables have many — resolves with
    `text == ""` and `char_start == char_end` sitting at the *start* of its own field, just
    after the opening delimiter, so a zero-width highlight lands in the right column rather
    than on top of the next one. That is a real cell at a real coordinate, not an error.

    `row_label` is carried alongside because every consumer that resolves a value cell also
    wants the row it came from (§3.4 check 4), and re-deriving it means re-splitting the same
    line. It is `cells[0]` of the cell's own row — 2,690 / 2,690 against the live graph.
    """

    text: str
    char_start: int
    char_end: int
    row_index: int
    column_index: int
    row_label: str


def split_cells(line: str) -> tuple[str, ...]:
    """The grid row a flattened markdown line stands for: split on `|`, drop the delimiters
    at either end, strip each field.

    Leading and trailing empties are dropped only when the line actually opens and closes
    with a delimiter, which is the case for 3,002 / 3,002 corpus lines. Dropping them
    unconditionally would eat a genuinely empty first column from a line written without
    the outer pipes.
    """
    return tuple(text for text, _, _ in _cell_spans(line))


def resolve_cell(
    passage_text: str,
    *,
    row_index: int,
    column_index: int,
) -> ResolvedCell:
    """The cell at `(row_index, column_index)`, with its span in `passage_text`.

    Raises `CellOutOfBounds` when either index names no cell — including negative indices,
    which Python would otherwise resolve by wrapping around to the end of the table and
    returning a plausible wrong cell in silence.
    """
    line, line_start = _line_at(passage_text, row_index)
    spans = _cell_spans(line)

    if column_index < 0 or column_index >= len(spans):
        raise CellOutOfBounds(
            f"column {column_index} is outside row {row_index}, which has {len(spans)} cells"
        )

    text, start, end = spans[column_index]
    return ResolvedCell(
        text=text,
        char_start=line_start + start,
        char_end=line_start + end,
        row_index=row_index,
        column_index=column_index,
        # Always column 0 — §1.3's second invariant is `cells(lines[row_index])[0] ==
        # row_label`, and it held 2,690 / 2,690 with no metric label ever found elsewhere.
        row_label=spans[0][0],
    )


def resolve_header(
    passage_text: str,
    *,
    row_index: int,
    column_index: int,
) -> str:
    """The header cell's text — for a value cell, read at `period_header_row_index` and
    `period_header_column_index`, never at the value's own column (see the module docstring:
    the value column agrees with the header column on 565 / 2,690).

    Text and not a `ResolvedCell` because the header's span is not what anyone highlights;
    §3.4 check 5 compares it to `column_label` and stops there. `resolve_cell` is right
    there for a caller that turns out to need the span.
    """
    return resolve_cell(
        passage_text, row_index=row_index, column_index=column_index
    ).text


# ---------------------------------------------------------------------------------------
# Internals. One scanner produces text and offsets together, so `split_cells` and
# `resolve_cell` can never disagree about where a cell begins.
# ---------------------------------------------------------------------------------------


def _line_at(passage_text: str, row_index: int) -> tuple[str, int]:
    """The `row_index`-th line and its absolute offset in `passage_text`.

    Rows are exactly the elements of `passage_text.split("\\n")` — no trailing-empty
    special case — because that is the rule the 2,690-row measurement used and the rule the
    graph's `row_index` was produced under. A text ending in `\\n` therefore has a final
    empty row, which no corpus passage does *(0 of the 144 distinct table-backed passages
    end in a newline, verified 2026-08-13)*; inventing an exception for a shape the corpus
    has never held would put the resolver and the invariant out of step.

    Scanned rather than `split` + `sum(len)` so the offset returned is the one a slice of
    the original string uses, with no list of every line built to answer for one of them.
    """
    line_count = passage_text.count("\n") + 1
    if row_index < 0 or row_index >= line_count:
        raise CellOutOfBounds(
            f"row {row_index} is outside the passage, which has {line_count} lines"
        )

    start = 0
    for _ in range(row_index):
        start = passage_text.index("\n", start) + 1

    end = passage_text.find("\n", start)
    return (passage_text[start:end], start) if end != -1 else (passage_text[start:], start)


def _cell_spans(line: str) -> tuple[tuple[str, int, int], ...]:
    """`(stripped text, start, end)` per cell, offsets relative to the start of `line`.

    The strip is done by moving two cursors rather than by `str.strip`, because the offsets
    are the product — `str.strip` discards exactly the information a highlight needs.
    """
    fields: list[tuple[int, int]] = []
    cursor = 0
    while True:
        delimiter = line.find(CELL_DELIMITER, cursor)
        if delimiter == -1:
            fields.append((cursor, len(line)))
            break
        fields.append((cursor, delimiter))
        cursor = delimiter + 1

    # Only the delimiters the line actually opens and closes with are dropped — see
    # `split_cells`. `|a|` splits to `['', 'a', '']`; `a|b` splits to `['a', 'b']`.
    if len(fields) >= 2 and line.startswith(CELL_DELIMITER):
        fields = fields[1:]
    if len(fields) >= 1 and line.endswith(CELL_DELIMITER):
        fields = fields[:-1]

    return tuple(_strip_span(line, start, end) for start, end in fields)


def _strip_span(line: str, start: int, end: int) -> tuple[str, int, int]:
    """Trim whitespace off both ends of `line[start:end]`, keeping the offsets.

    Trailing first, then leading, which matters only for an all-whitespace field: the span
    collapses to the field's *start* rather than to its end, so an empty spacer cell — of
    which these tables have many — highlights as a zero-width caret just after its opening
    delimiter instead of sitting on top of the next cell's. Trimming the other way round
    gives an identical answer for every non-empty cell.
    """
    while end > start and line[end - 1].isspace():
        end -= 1
    while start < end and line[start].isspace():
        start += 1
    return line[start:end], start, end
