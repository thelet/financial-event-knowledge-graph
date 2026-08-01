"""A Markdown pipe table as an addressable grid, with original positions preserved.

Collapsing empty columns is necessary — HTML layout scatters `$`, the magnitude and `%`
across three cells that mean one value — but it must not be lossy. Every cell keeps its
original row and column index alongside its collapsed index, so evidence points at where the
value actually sits in the source and a misalignment is inspectable rather than merely wrong.

The corpus shape this is built for, from `q12025formxex991earningsre.htm#p14`:

    |  |  | Three Months Ended |  |  |  |  |
    |  |  | March 31, 2025 |  | December 31, 2024 |  |
    | Revenue |  | $ | 1,153 |  |  | $ | 1,084 |
    | Gross Margin |  | 8.6 | % |  | 7.8 | % |

Twenty-nine raw columns, of which four carry the five periods. A reader that indexes on raw
columns aligns Revenue's 1,153 against a different period than Gross Margin's 8.6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-{2,}[\s:|-]*\|?\s*$")


@dataclass(frozen=True)
class Cell:
    """One cell, addressable in both coordinate systems."""

    row_index: int          # original row, including header and separator rows
    column_index: int       # original column, before collapsing
    collapsed_index: int    # position among columns that survived collapsing, else -1
    text: str

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class TableGrid:
    """A parsed pipe table.

    `rows` is ragged on purpose: filings emit rows with different cell counts, and padding
    them to a rectangle would invent cells that the source does not contain and that evidence
    would then point at.
    """

    rows: list[list[Cell]] = field(default_factory=list)
    surviving_columns: tuple[int, ...] = ()
    separator_rows: frozenset[int] = frozenset()
    passage_id: str | None = None
    table_id: str | None = None
    block_ids: tuple[str, ...] = ()

    @property
    def width(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def cell(self, row_index: int, column_index: int) -> Cell | None:
        if 0 <= row_index < len(self.rows):
            row = self.rows[row_index]
            if 0 <= column_index < len(row):
                return row[column_index]
        return None

    def row_label(self, row_index: int) -> str:
        """The first non-empty cell of a row.

        Not simply column 0: several corpus tables indent by leaving the first cell blank,
        and `| | 16 | NON-EXCLUSIVITY ... |` would otherwise yield an empty label.
        """
        for cell in self.rows[row_index] if 0 <= row_index < len(self.rows) else ():
            if not cell.is_empty:
                return cell.text.strip()
        return ""

    def data_rows(self) -> list[int]:
        return [i for i in range(len(self.rows)) if i not in self.separator_rows]

    def collapsed_cells(self, row_index: int) -> list[Cell]:
        """Non-empty cells of a row, in order, each keeping its original column index."""
        if not (0 <= row_index < len(self.rows)):
            return []
        return [c for c in self.rows[row_index] if not c.is_empty]


def parse_markdown_table(
    text: str,
    *,
    passage_id: str | None = None,
    table_id: str | None = None,
    block_ids: tuple[str, ...] = (),
) -> TableGrid:
    """Parse a normalized table passage into a grid.

    Separator rows (`| --- | --- |`) are recorded rather than dropped, because their row
    index is what tells a reader where the header ends.
    """
    grid = TableGrid(passage_id=passage_id, table_id=table_id, block_ids=block_ids)
    separators: set[int] = set()

    for row_index, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        if _SEPARATOR.match(line):
            separators.add(len(grid.rows))
            grid.rows.append([])
            continue
        cells = _split_row(line)
        grid.rows.append([
            Cell(row_index=len(grid.rows), column_index=i, collapsed_index=-1, text=value)
            for i, value in enumerate(cells)
        ])

    grid.separator_rows = frozenset(separators)
    _assign_collapsed_indices(grid)
    return grid


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [part.strip() for part in stripped.split("|")]


def _assign_collapsed_indices(grid: TableGrid) -> None:
    """Drop columns that are empty in every row, and number what is left.

    A column empty throughout is layout — a gutter between a currency sigil and its
    magnitude, or padding to a fixed table width. A column empty in *some* rows is not, and
    must survive: `Gross Margin`'s `%` cell is empty on the `Revenue` row.
    """
    width = grid.width
    surviving: list[int] = []
    for column in range(width):
        if any(
            column < len(row) and not row[column].is_empty
            for index, row in enumerate(grid.rows)
            if index not in grid.separator_rows
        ):
            surviving.append(column)

    position = {column: index for index, column in enumerate(surviving)}
    for row_index, row in enumerate(grid.rows):
        for column, cell in enumerate(row):
            row[column] = Cell(
                row_index=cell.row_index,
                column_index=cell.column_index,
                collapsed_index=position.get(column, -1),
                text=cell.text,
            )
    grid.surviving_columns = tuple(surviving)
