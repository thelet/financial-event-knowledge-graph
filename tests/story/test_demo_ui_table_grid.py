"""Table evidence as a highlighted cell in its grid — the payload, and the markup it becomes.

**The claim under test is "the *right* cell is highlighted", not "a cell is highlighted".** Every
assertion below names the cell it expects by its row label, its period header and its value, and
the two facts it drives are the two in the committed demo package, whose handles are
`ev:…open-20220930.htm#p139:r5c2` (`'(12.6)'`, row `Gross Margin`) and `…:r11c2` (`'3.3'`, row
`Adjusted Gross Margin`). A test that asserted a `<td>` existed, or that some cell carried
`is-cited`, would pass with the highlight on the wrong number.

**Half of this file runs the real renderer.** `app.js` cannot be imported — it looks up sixty
elements at module scope — so `cellGrid` and `make` are lifted out by the markers they carry and
run under `node` against a payload `story.demo_ui.package_view` really built, with a fifty-line
`document` shim. The markup asserted at the bottom is therefore markup the shipped file produced,
not a description of what it ought to produce. Where there is no JavaScript engine on the path
the node half **skips rather than pretending** — the rule `test_demo_ui_number_format.py` already
applies, for the same reason.

The Python half needs no engine and is the stronger of the two: it drives `table_grid` and
`package_view` over the committed fixture and over hand-built passages that put the coordinates
somewhere they cannot resolve.

One `neo4j`-marked test at the bottom drives the same code over **every** table-backed evidence
edge in the loaded graph, because two facts in one fixture cannot show that a renderer handles
a 33-column table or a row whose header sits in a column the value does not.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from story.core.models import (
    EvidenceRole,
    PackagedPassage,
    PassageCitation,
    StoryEvidencePackage,
    TableCellRef,
)
from story.core.table_cells import split_cells
from story.demo_ui import package_view, table_grid

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "story" / "fixtures" / "story_demo" / "evidence_package.json"
APP = REPO_ROOT / "story" / "demo_ui" / "static" / "app.js"

#: The passage both demo facts are read from, and the two cells they name. Spelled out here
#: rather than read off the fixture, because a test that took its expectation from the same file
#: it is checking would pass after the fixture drifted.
PASSAGE_ID = "norm:0001801169:0001801169-22-000108:open-20220930.htm#p139"
GAAP_HANDLE = f"ev:{PASSAGE_ID}:r5c2"
ADJUSTED_HANDLE = f"ev:{PASSAGE_ID}:r11c2"

#: `(handle, row, column, row label, period header, value)` — the whole claim, per fact.
EXPECTED_CELLS = (
    (GAAP_HANDLE, 5, 2, "Gross Margin", "2022", "(12.6)"),
    (ADJUSTED_HANDLE, 11, 2, "Adjusted Gross Margin", "2022", "3.3"),
)


@pytest.fixture(scope="module")
def package() -> StoryEvidencePackage:
    return StoryEvidencePackage.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def passage(package: StoryEvidencePackage) -> PackagedPassage:
    return next(row for row in package.primary_passages if row.passage_id == PASSAGE_ID)


@pytest.fixture(scope="module")
def rows(package: StoryEvidencePackage) -> list[dict]:
    return package_view.passage_rows(package)


def row_for(rows: list[dict], passage_id: str) -> dict:
    return next(row for row in rows if row["passage_id"] == passage_id)


# ---------------------------------------------------------------------------------------
# The cell each fact resolves to — named, not counted.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handle, row_index, column_index, row_label, header, value", EXPECTED_CELLS)
def test_a_fact_marks_the_cell_its_handle_names_and_no_other(
    package, passage, handle, row_index, column_index, row_label, header, value
) -> None:
    """The identity chain end to end: handle → fact → cell → row label, header, value, span."""
    fact = package.facts_by_evidence_handle()[handle]
    mark = table_grid.fact_mark(fact, passage)

    assert mark["resolved"] is True and mark["unplaced_reason"] == ""
    assert (mark["row_index"], mark["column_index"]) == (row_index, column_index)
    assert mark["row_label"] == row_label
    assert mark["column_header"] == header
    assert mark["text"] == value
    # The span is the thing the panel highlights and §13.7's Rule B reads, so it must land on
    # the value's own bytes and not on the padding around them.
    assert passage.text[mark["char_start"]:mark["char_end"]] == value
    assert (mark["matches_value"], mark["matches_row_label"], mark["matches_column_label"]) \
        == (True, True, True)


def test_the_marked_cell_in_the_grid_is_the_one_the_handle_names(rows, package) -> None:
    """Not "a cell is marked" — *this* cell, found by walking the grid the panel is sent."""
    grid = row_for(rows, PASSAGE_ID)["grid"]
    marks = row_for(rows, PASSAGE_ID)["cell_marks"]

    marked = [(row["row_index"], cell["column_index"], cell["text"],
               marks[index]["evidence_handle"])
              for row in grid["rows"]
              for cell in row["cells"]
              for index in cell["marked_by"]]
    assert sorted(marked) == [
        (5, 2, "(12.6)", GAAP_HANDLE),
        (11, 2, "3.3", ADJUSTED_HANDLE),
    ]


def test_the_row_label_beside_a_marked_cell_is_the_line_item_the_fact_records(rows) -> None:
    """A highlighted number means nothing without the row it sits on — so the row is checked."""
    grid = row_for(rows, PASSAGE_ID)["grid"]
    labels = {row["row_index"]: row["cells"][0]["text"] for row in grid["rows"]}
    assert labels[5] == "Gross Margin"
    assert labels[11] == "Adjusted Gross Margin"
    assert all(row["cells"][0]["is_row_label"] for row in grid["rows"])


def test_the_header_cell_the_grid_flags_is_the_period_header_and_not_the_value_column(
    rows, package
) -> None:
    """`period_header_column_index` is a carried property because it is usually *not* the value's
    column — 2,125 of 2,690 table-backed observations differ. Here they agree, and the grid must
    still flag the cell at the *header* coordinates rather than at the value's."""
    row = row_for(rows, PASSAGE_ID)
    grid, marks = row["grid"], row["cell_marks"]
    flagged = {(line["row_index"], cell["column_index"], cell["text"])
               for line in grid["rows"] for cell in line["cells"] if cell["header_for"]}
    assert flagged == {(3, 2, "2022")}
    assert {mark["header_row_index"] for mark in marks} == {3}
    assert {mark["header_column_index"] for mark in marks} == {2}
    assert [line["row_index"] for line in grid["rows"] if line["is_period_header"]] == [3]


# ---------------------------------------------------------------------------------------
# The grid itself: shape, spacers, the rule row.
# ---------------------------------------------------------------------------------------


def test_the_grid_is_the_passage_line_for_line_and_cell_for_cell(rows, passage) -> None:
    """Row indices are positions in `passage_text.split("\\n")`, separator row included — which
    is what makes `r5` in a handle mean line 5 and not "the fifth data row"."""
    grid = row_for(rows, PASSAGE_ID)["grid"]
    lines = passage.text.split("\n")
    assert grid["row_count"] == len(lines) == 23
    assert grid["column_count"] == 17
    assert [line["row_index"] for line in grid["rows"]] == list(range(23))
    for line, text in zip(grid["rows"], lines):
        assert [cell["text"] for cell in line["cells"]] == list(split_cells(text))


def test_every_cells_span_slices_its_own_text_out_of_the_passage(rows, passage) -> None:
    """391 cells, each one checked. The offsets are what a highlight is drawn from, and an
    off-by-one that landed on the neighbouring column would still look like a highlight."""
    grid = row_for(rows, PASSAGE_ID)["grid"]
    checked = 0
    for line in grid["rows"]:
        for cell in line["cells"]:
            assert passage.text[cell["char_start"]:cell["char_end"]] == cell["text"]
            checked += 1
    assert checked == grid["cell_count"] == 391


def test_a_spacer_column_is_a_real_column_and_is_kept(rows) -> None:
    """72.0% of the corpus's table cells are empty. Collapsing them would renumber the grid and
    `r5c2` would stop naming the cell it names — so row 5 keeps all seventeen of its columns."""
    grid = row_for(rows, PASSAGE_ID)["grid"]
    row_five = next(line for line in grid["rows"] if line["row_index"] == 5)
    assert [cell["text"] for cell in row_five["cells"]] == [
        "Gross Margin", "", "(12.6)", "%", "", "8.9", "%", "", "4.7", "%", "", "10.9", "%",
        "", "", "", ""]
    assert [cell["empty"] for cell in row_five["cells"]][1] is True
    assert grid["empty_cells"] == 257
    # An empty cell's span is a zero-width caret just after its own delimiter, not a run of
    # padding and not the next cell's position.
    spacer = row_five["cells"][1]
    assert spacer["char_start"] == spacer["char_end"]
    assert spacer["char_end"] < row_five["cells"][2]["char_start"]


def test_the_separator_line_is_flagged_as_a_rule_and_still_occupies_its_row_index(rows) -> None:
    grid = row_for(rows, PASSAGE_ID)["grid"]
    assert [line["row_index"] for line in grid["rows"] if line["is_rule"]] == [1]


def test_a_narrative_passage_has_no_grid_and_says_so_on_its_marks(package) -> None:
    """8,273 of the corpus's 8,776 passages are prose. They keep the span path, unchanged."""
    prose = PackagedPassage(
        passage_id="p:prose", document_id="d", text="Only 8% of our homes had been listed.",
        char_count=36, passage_kind="narrative", role=EvidenceRole.PRIMARY_SUPPORT)
    assert table_grid.grid_of(prose) is None
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE]
    mark = table_grid.fact_mark(fact, prose)
    assert mark["resolved"] is False
    assert mark["unplaced_reason"] == table_grid.NOT_A_TABLE


# ---------------------------------------------------------------------------------------
# Where the coordinates do not resolve. Each of these is a §13.7 refusal made visible.
# ---------------------------------------------------------------------------------------


def test_a_fact_with_no_cell_is_placed_nowhere_rather_than_guessed_at(package, passage) -> None:
    """The shape the fixture carried before S7a rebuilt it: a table fact with `cell: null`."""
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE].model_copy(update={"cell": None})
    mark = table_grid.fact_mark(fact, passage)
    assert mark["resolved"] is False
    assert mark["unplaced_reason"] == table_grid.NO_CELL
    assert mark["row_index"] is None and mark["column_index"] is None
    assert table_grid.grid_of(passage, marks=[mark])["rows"][5]["cells"][2]["marked_by"] == []


def test_coordinates_off_the_grid_report_the_reason_instead_of_dropping_the_mark(
    package, passage
) -> None:
    """`evidence_handle_out_of_bounds`'s cause, rendered. A dropped mark would leave an unmarked
    grid, and a reader would read that as *"nothing was cited"* — the opposite of the truth."""
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE].model_copy(
        update={"cell": TableCellRef(row_index=999, value_column_index=2,
                                     period_header_row_index=3, period_header_column_index=2)})
    mark = table_grid.fact_mark(fact, passage)
    assert mark["resolved"] is False
    assert "row 999 is outside the passage" in mark["unplaced_reason"]


def test_an_excerpted_table_is_marked_uncoordinatable_rather_than_marked_in_the_wrong_place(
    package, passage
) -> None:
    """**The trap this branch exists for.** §10.2.1 windows explanatory and counter-evidence
    passages to ±400 characters. `resolve_cell` on a window returns a perfectly well-formed
    *wrong* cell for the package's coordinates and raises nothing, so a panel that just called it
    would highlight a plausible lie. `coordinates_apply` is false and the mark says why."""
    window = passage.model_copy(update={"text": "\n".join(passage.text.split("\n")[4:8]),
                                        "excerpted": True, "char_start": 380})
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE]
    mark = table_grid.fact_mark(fact, window)
    assert mark["resolved"] is False
    assert mark["unplaced_reason"] == table_grid.EXCERPTED
    grid = table_grid.grid_of(window, marks=[mark])
    assert grid["coordinates_apply"] is False
    assert all(not cell["marked_by"] for line in grid["rows"] for cell in line["cells"])


def test_a_disagreement_between_the_package_and_its_own_passage_is_shown_and_not_smoothed(
    package, passage
) -> None:
    """Checks 3–5 of §3.4 have causes, and a reader must be able to see one. This states no
    verdict — the verifier refuses it — but a panel that printed only the resolved cell would
    hide the fact that the package records a different value for it."""
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE].model_copy(
        update={"quoted_text": "(99.9)", "row_label": "Somewhere Else"})
    mark = table_grid.fact_mark(fact, passage)
    assert mark["resolved"] is True
    assert mark["text"] == "(12.6)" and mark["declared_value_text"] == "(99.9)"
    assert mark["matches_value"] is False
    assert mark["matches_row_label"] is False
    assert mark["matches_column_label"] is True


# ---------------------------------------------------------------------------------------
# Citations: the handle and the span are two answers, and both are rendered.
# ---------------------------------------------------------------------------------------


def citation_for(package, passage, handle: str, *, start=None, end=None) -> PassageCitation:
    fact = package.facts_by_evidence_handle()[handle]
    mark = table_grid.fact_mark(fact, passage)
    return PassageCitation(
        passage_id=passage.passage_id, document_id=passage.document_id,
        char_start=mark["char_start"] if start is None else start,
        char_end=mark["char_end"] if end is None else end,
        evidence_handle=handle)


def test_an_honest_citation_lands_on_its_own_cell(package, passage) -> None:
    citation = citation_for(package, passage, GAAP_HANDLE)
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE]
    mark = table_grid.citation_mark(citation, passage, fact,
                                    sentence_index=2, sentence_text="…")
    assert (mark["row_index"], mark["column_index"]) == (5, 2)
    assert mark["row_label"] == "Gross Margin" and mark["column_header"] == "2022"
    assert mark["span_matches_cell"] is True
    assert mark["cited_text"] == "(12.6)" == mark["text"]
    assert mark["sentence_index"] == 2


def test_a_citation_whose_bytes_are_another_cells_is_rendered_as_the_disagreement_it_is(
    package, passage
) -> None:
    """`evidence_cell_span_mismatch`, drawn. The handle is `Gross Margin`'s and the span is
    `Adjusted Gross Margin`'s, and a panel showing only one of them would render a verified
    handle over unverified bytes as though nothing were wrong."""
    other = table_grid.fact_mark(package.facts_by_evidence_handle()[ADJUSTED_HANDLE], passage)
    citation = citation_for(package, passage, GAAP_HANDLE,
                            start=other["char_start"], end=other["char_end"])
    mark = table_grid.citation_mark(
        citation, passage, package.facts_by_evidence_handle()[GAAP_HANDLE],
        sentence_index=0, sentence_text="…")
    assert mark["span_matches_cell"] is False
    assert (mark["row_index"], mark["column_index"]) == (5, 2), "the handle's cell is drawn"
    assert mark["text"] == "(12.6)"
    assert mark["cited_text"] == "3.3", "and the bytes it actually highlights are shown too"


def test_a_handle_no_fact_minted_renders_as_a_citation_with_no_cell(package, passage) -> None:
    """§3.4 check 1, `unresolvable_evidence_handle`. The row stays; the cell does not appear."""
    citation = PassageCitation(passage_id=passage.passage_id, document_id=passage.document_id,
                               char_start=0, char_end=4, evidence_handle="ev:nowhere:r0c0")
    mark = table_grid.citation_mark(citation, passage, None,
                                    sentence_index=1, sentence_text="…")
    assert mark["resolved"] is False and mark["fact_id"] is None
    assert "no fact in this package minted this evidence id" in mark["unplaced_reason"]
    assert mark["evidence_handle"] == "ev:nowhere:r0c0"


def test_the_citation_span_is_rebased_onto_the_packaged_text(package, passage) -> None:
    """Citation offsets are absolute into the full `:Passage.text`; a packaged passage carries
    `char_start`. Indexing the packaged text with the absolute offset was correct only because
    `char_start` is 0 for every passage a citation can reach today."""
    shifted = passage.model_copy(update={"char_start": 1000})
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE]
    mark = table_grid.fact_mark(fact, shifted)
    assert mark["char_start"] == 1000 + 492 and mark["char_end"] == 1000 + 498
    citation = PassageCitation(passage_id=shifted.passage_id, document_id=shifted.document_id,
                               char_start=mark["char_start"], char_end=mark["char_end"],
                               evidence_handle=GAAP_HANDLE)
    placed = table_grid.citation_mark(citation, shifted, fact,
                                      sentence_index=0, sentence_text="…")
    assert placed["cited_text"] == "(12.6)"
    assert placed["span_matches_cell"] is True


# ---------------------------------------------------------------------------------------
# The join, and the one property that makes `marked_by` usable.
# ---------------------------------------------------------------------------------------


def test_marked_by_indexes_the_mark_list_the_same_payload_carries(rows) -> None:
    """The grid points at marks by position, so the two must be built from one sequence. A
    serialiser that patched `grid` afterwards would leave every index one place out."""
    row = row_for(rows, PASSAGE_ID)
    for line in row["grid"]["rows"]:
        for cell in line["cells"]:
            for index in cell["marked_by"]:
                mark = row["cell_marks"][index]
                assert (mark["row_index"], mark["column_index"]) \
                    == (line["row_index"], cell["column_index"])
                assert mark["text"] == cell["text"]


def test_both_endpoints_get_the_grid_from_the_same_function(package) -> None:
    """§4 S6's rule, one layer down: two serialisers agreeing by convention is how the role
    field drifted. `passage_rows` is the only builder, and `extra_marks` is how the endpoint
    that holds a draft adds its citations without patching the grid behind it."""
    plain = row_for(package_view.passage_rows(package), PASSAGE_ID)
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE]
    citation = citation_for(package, next(p for p in package.primary_passages), GAAP_HANDLE)
    extra = table_grid.marks_by_passage([table_grid.citation_mark(
        citation, package.primary_passages[0], fact, sentence_index=3, sentence_text="…")])
    withdraft = row_for(package_view.passage_rows(package, extra_marks=extra), PASSAGE_ID)

    assert len(plain["cell_marks"]) == 2
    assert len(withdraft["cell_marks"]) == 3
    assert withdraft["cell_marks"][2]["kind"] == "citation"
    # The value cell now carries two marks — the fact and the citation — and both indices
    # resolve to it.
    cell = withdraft["grid"]["rows"][5]["cells"][2]
    assert len(cell["marked_by"]) == 2
    assert {withdraft["cell_marks"][i]["kind"] for i in cell["marked_by"]} \
        == {"fact", "citation"}


def test_the_facts_the_model_is_shown_carry_the_handle_a_refusal_will_name(package) -> None:
    """The handle is the model's whole citation vocabulary, so it must be legible in the panel
    that shows what the model was given — otherwise a reader cannot match `ev:…:r5c2` in a
    rejection to any line on the page."""
    facts = package_view.model_facts(package, display=str)
    observed = next(group for group in facts["groups"] if group["group"] == "observed_facts")
    handles = {row["evidence_handle"] for row in observed["rows"]}
    assert handles == {GAAP_HANDLE, ADJUSTED_HANDLE}
    for row in observed["rows"]:
        assert row["cell"]["value_column_index"] == 2
        assert row["row_label"] and row["column_label"] == "2022"


# ---------------------------------------------------------------------------------------
# The markup, produced by the shipped renderer under `node`.
# ---------------------------------------------------------------------------------------

#: A `document` small enough to read and faithful enough for what `cellGrid` uses:
#: `createElement`, `className`, `classList`, `dataset`, `textContent`, `title`, `append`.
#: Anything the renderer reached for that is not here would throw rather than pass quietly.
DOM_SHIM = """
const document = {
  createElement(tag) {
    return {
      tag, className: '', textContent: '', title: '', dataset: {}, children: [],
      classList: {
        _owner: null,
        add(...names) { this._owner.className =
          (this._owner.className ? this._owner.className + ' ' : '') + names.join(' '); },
      },
      append(...kids) { this.children.push(...kids); },
    };
  },
};
function attach(node) { node.classList._owner = node; return node; }
const _create = document.createElement;
document.createElement = (tag) => attach(_create(tag));

function serialise(node, depth) {
  const pad = '  '.repeat(depth);
  const attributes = [
    node.className ? ` class="${node.className}"` : '',
    // camelCase -> kebab-case, which is what a real `dataset` write produces as an attribute
    // — `dataset.markIndex` is `data-mark-index`, and `app.js` queries it by that spelling.
    ...Object.entries(node.dataset).map(
      ([k, v]) => ` data-${k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())}="${v}"`),
    node.title ? ` title="${node.title}"` : '',
  ].join('');
  if (!node.children.length) {
    return `${pad}<${node.tag}${attributes}>${node.textContent}</${node.tag}>`;
  }
  const inner = node.children.map((kid) => serialise(kid, depth + 1)).join('\\n');
  return `${pad}<${node.tag}${attributes}>\\n${inner}\\n${pad}</${node.tag}>`;
}
"""


def block(begin: str, end: str) -> str:
    source = APP.read_text(encoding="utf-8")
    start = source.find(begin)
    finish = source.find(end)
    assert start >= 0, f"app.js no longer carries {begin!r}"
    assert finish > start, f"app.js no longer carries {end!r} after {begin!r}"
    return source[start:finish]


def render_grid(grid: dict) -> str:
    """The real `cellGrid`, run over a real payload, serialised to HTML."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no JavaScript engine on the path; this file will not pretend otherwise")
    script = "\n".join([
        DOM_SHIM,
        block("/* BEGIN DOM MAKE", "/* END DOM MAKE */"),
        block("/* BEGIN CELL GRID", "/* END CELL GRID */"),
        f"const grid = {json.dumps(grid)};",
        "process.stdout.write(serialise(cellGrid(grid), 0));",
    ])
    finished = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, check=False)
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def test_the_shipped_renderer_highlights_the_gaap_cell_and_not_its_neighbour(rows) -> None:
    """The strongest assertion this environment allows: real `app.js`, real payload, real
    markup, and the check is *which* `<td>` came back marked."""
    row = row_for(rows, PASSAGE_ID)
    rendered = render_grid(row["grid"])
    lines = rendered.split("\n")
    cited = [line.strip() for line in lines if 'class="is-cited"' in line]
    assert len(cited) == 2
    assert any(line.startswith('<td class="is-cited" data-column-index="2" data-mark-index=')
               and ">(12.6)</td>" in line for line in cited), cited
    assert any(">3.3</td>" in line for line in cited), cited
    # Every other numeral in the table is *not* marked — including `8.9`, the same row's next
    # period, which is the neighbour a wrong column index would land on.
    assert all(">8.9</td>" not in line for line in cited)
    assert '<td class="is-empty"' in rendered, "a spacer column is drawn, not collapsed"
    assert '<th class="grid-index">5</th>' in rendered, "the row ruler names row 5"


def test_the_rendered_row_five_reads_as_the_gross_margin_line(rows) -> None:
    """The row the highlight sits on, as markup: label, spacer, cited value, unit."""
    rendered = render_grid(row_for(rows, PASSAGE_ID)["grid"])
    body = rendered.split('<tr data-row-index="5">')[1].split("</tr>")[0]
    cells = [line.strip() for line in body.split("\n") if line.strip().startswith("<t")]
    assert cells[0] == '<th class="grid-index">5</th>'
    assert cells[1] == '<td class="is-row-label" data-column-index="0">Gross Margin</td>'
    assert cells[2] == '<td class="is-empty" data-column-index="1"></td>'
    assert cells[3].startswith('<td class="is-cited" data-column-index="2"')
    assert cells[3].endswith(">(12.6)</td>")
    assert cells[4] == '<td data-column-index="3">%</td>'


def test_the_period_header_cell_is_drawn_as_the_header_and_the_row_as_a_header_row(rows) -> None:
    rendered = render_grid(row_for(rows, PASSAGE_ID)["grid"])
    header_row = rendered.split('<tr class="is-period-header" data-row-index="3">')[1]
    assert '<td class="is-period-header-cell" data-column-index="2">2022</td>' in header_row


# ---------------------------------------------------------------------------------------
# The whole corpus, live.
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def table_evidence():  # type: ignore[no-untyped-def]
    """Every table-backed evidence edge in the loaded graph, with its passage text.

    `verify_connectivity` first for `test_story_retrieval.py`'s measured reason: the handshake
    fails in 0.0s against a refused port while a query retries for 35 seconds.
    """
    from story.context import build_story_context

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        yield context.executor.read(
            """
            MATCH (o:Observation)-[e:EVIDENCED_BY]->(p:Passage)
            WHERE e.table_id IS NOT NULL
            RETURN o.observation_id AS observation_id, o.row_index AS row_index,
                   o.value_column_index AS value_column_index,
                   o.period_header_row_index AS period_header_row_index,
                   o.period_header_column_index AS period_header_column_index,
                   o.row_label AS row_label, o.column_label AS column_label,
                   e.quoted_text AS quoted_text, p.passage_id AS passage_id,
                   p.text AS text, p.passage_kind AS passage_kind
            LIMIT 5000
            """,
            {}, timeout_seconds=300.0)
    finally:
        context.close()


def _as_fact_and_passage(row):  # type: ignore[no-untyped-def]
    """The two shapes `fact_mark` reads, built from one graph row.

    `SimpleNamespace` rather than `PackagedFact`/`PackagedPassage`: this test is about the
    renderer against 2,690 real coordinate sets, and constructing two fully validated models
    per row would be a test of the model constructors.
    """
    passage = SimpleNamespace(passage_id=row["passage_id"], document_id="d", text=row["text"],
                              passage_kind=row["passage_kind"], char_start=0, excerpted=False)
    fact = SimpleNamespace(
        observation_id=row["observation_id"], evidence_handle="ev:probe",
        metric_label="m", period_key="P", row_label=row["row_label"],
        column_label=row["column_label"], quoted_text=row["quoted_text"],
        cell=TableCellRef(row_index=row["row_index"],
                          value_column_index=row["value_column_index"],
                          period_header_row_index=row["period_header_row_index"],
                          period_header_column_index=row["period_header_column_index"]))
    return fact, passage


@pytest.mark.neo4j
def test_live_every_table_backed_fact_marks_its_own_cell(
    table_evidence,  # type: ignore[no-untyped-def]
) -> None:
    """2,690 of 2,690, and the assertion is on the *cell*, not on "it resolved".

    Two facts in one committed fixture cannot show this: they sit in a 17-column table whose
    period header happens to share the value's column. Across the corpus the two differ on
    2,125 of 2,690 rows, so a renderer that read the header at the value's own column would
    pass the fixture and be wrong four times in five here.
    """
    assert len(table_evidence) == 2690, "the corpus moved; this number is the measurement"
    wrong = []
    for row in table_evidence:
        fact, passage = _as_fact_and_passage(row)
        placed = table_grid.fact_mark(fact, passage)
        if not (placed["resolved"]
                and placed["text"] == row["quoted_text"]
                and placed["row_label"] == row["row_label"]
                and placed["column_header"] == row["column_label"]
                and row["text"][placed["char_start"]:placed["char_end"]] == row["quoted_text"]):
            wrong.append((row["observation_id"], placed["unplaced_reason"] or placed["text"]))
    assert wrong == [], wrong[:5]


@pytest.mark.neo4j
def test_live_the_grid_a_panel_is_sent_covers_the_whole_passage_however_wide(
    table_evidence,  # type: ignore[no-untyped-def]
) -> None:
    """The widest evidenced table is 33 columns and 69.5% of its cells are empty. Both numbers
    are why the ruler and the kept spacers exist, and both are asserted rather than described."""
    grids = {}
    for row in table_evidence:
        if row["passage_id"] in grids:
            continue
        _, passage = _as_fact_and_passage(row)
        grids[row["passage_id"]] = table_grid.grid_of(passage)

    assert len(grids) == 144, "every table-backed passage in the corpus"
    for passage_id, grid in grids.items():
        assert grid["column_count"] == max(len(line["cells"]) for line in grid["rows"])
        assert grid["cell_count"] == sum(len(line["cells"]) for line in grid["rows"])
    assert max(grid["column_count"] for grid in grids.values()) == 33
    cells = sum(grid["cell_count"] for grid in grids.values())
    empty = sum(grid["empty_cells"] for grid in grids.values())
    assert (cells, empty) == (58914, 40917)


def test_a_cell_carrying_two_marks_is_addressable_by_both_of_them(package) -> None:
    """The bug this pins was live and would have been invisible: a value cell ordinarily carries
    **two** marks — the packaged fact and the sentence citing it — and `data-mark-index` held
    only the first, so "jump to the cited cell" from a citation row selected nothing. The
    attribute is now the whole list, matched with `~=`.
    """
    passage = package.primary_passages[0]
    fact = package.facts_by_evidence_handle()[GAAP_HANDLE]
    citation = citation_for(package, passage, GAAP_HANDLE)
    marks = [table_grid.fact_mark(fact, passage),
             table_grid.citation_mark(citation, passage, fact,
                                      sentence_index=0, sentence_text="…")]
    grid = table_grid.grid_of(passage, marks=marks)
    assert grid["rows"][5]["cells"][2]["marked_by"] == [0, 1]

    rendered = render_grid(grid)
    cited = next(line.strip() for line in rendered.split("\n")
                 if 'class="is-cited"' in line and ">(12.6)</td>" in line)
    assert 'data-mark-index="0 1"' in cited, cited
    # And the selector `app.js` uses is the one that matches a list.
    source = APP.read_text(encoding="utf-8")
    assert 'td[data-mark-index~="' in source
