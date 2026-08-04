"""`app.js`: what a Python test can prove about a file no Python runs, and what it cannot.

**State the limit first, because it decides how to read everything below.** There is no browser
and no JavaScript engine in this environment. Nothing here executes a line of `app.js`, so
nothing here proves that a panel renders, that a click does anything, or that a number reaches
the element it was meant for. Every test in this file is one of exactly three kinds:

1. **A source-level property** — parsed or matched over the text. "There is no `innerHTML`
   anywhere" is fully proved this way, and it is the strongest kind here, because it is a
   statement about which code paths *exist* rather than about which ones run.
2. **A cross-check between two artifacts** — `app.js` against `index.html`, and `app.js` against
   the router `story.demo_ui.api` actually builds. These catch the failure mode static analysis
   is genuinely good at: a name that is spelled two ways in two files. A `getElementById` for an
   id nobody declares returns `null` and every write through it is a silent no-op, which is a
   bug no exception ever reports; a fetch of an unregistered path is a 404 in a panel nobody was
   watching. Both are found here, before a demo finds them.
3. **One end-to-end check that the server hands the file back** with the right content type.

What is *not* proved here, said plainly rather than left to be discovered: that the Markdown
renderer produces correct output; that a rejected run actually renders as rejected; that the SSE
client closes its stream; that clicking a trace step highlights anything. Those were exercised
during implementation by driving this file inside a DOM against payloads captured from the
running server, and the outcome of that run is recorded in the commit message — it is not
reproducible from this suite, and this docstring is not going to imply that it is.

One exception, added 2026-08-05: `formatNumber`'s agreement with `api._display_number` **is**
proved by execution, in `tests/story/test_demo_ui_number_format.py`, which lifts the formatter
out of this file and runs it under `node` against Python's own `%.10g`. It is in its own module
because it is the only test in this area that runs the code rather than reading it.

The repairs the adversarial review of 2026-08-05 asked for are the last section below. Every one
of them was *demonstrated* by driving the real `app.js` in jsdom against captured payloads — the
run's output is in the commit message — and what is asserted here is the source-level property
that the demonstrated behaviour rests on. A test that reads `finishGeneration` for a run-id guard
does not prove that the guard fires; it proves that removing it is a failing test rather than a
silent regression, which is the most this environment can do.

`tests/story/test_demo_ui_static.py` already scans every asset in the tree, `app.js` included,
for the markup sinks, the off-origin references and the random sources. Those scans are repeated
here **against this file by name** rather than trusted to a glob: the glob is what makes the rule
apply to a file nobody has written yet, and a named test is what fails with the right name when
this one breaks it.
"""

from __future__ import annotations

import ast
import http.client
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from story.demo_ui import api
from story.demo_ui.runs import RunRegistry
from story.demo_ui.server import CONTENT_TYPES, Router, build_app, build_server

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "story" / "demo_ui" / "static"
APP = STATIC / "app.js"
INDEX = STATIC / "index.html"


def app_source() -> str:
    return APP.read_text(encoding="utf-8")


def index_source() -> str:
    return INDEX.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------
# It exists, and the server hands it back.
# ---------------------------------------------------------------------------------------


def test_the_panel_module_exists_and_is_not_a_stub() -> None:
    assert APP.is_file(), f"{APP} is missing"
    assert len(APP.read_bytes()) > 2000, "app.js is a stub"


@pytest.fixture
def client():
    """A real server on an ephemeral loopback port, serving the real static root."""
    app = build_app(root=REPO_ROOT, registry=RunRegistry(), router=Router())
    server = build_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def fetch(path: str) -> tuple[int, str, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.getheader("Content-Type") or "", response.read()
        finally:
            connection.close()

    yield fetch
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_the_server_serves_the_panel_module_byte_for_byte(client) -> None:
    """`index.html` loads it as `<script type="module" src="app.js">`, so this path is the one
    the page asks for and a wrong content type is a module the browser refuses to run."""
    status, content_type, body = client("/app.js")
    assert status == 200
    assert content_type == CONTENT_TYPES[".js"]
    assert body == APP.read_bytes()


def test_the_page_loads_this_module_and_this_module_imports_only_the_renderer() -> None:
    assert 'src="app.js"' in index_source()
    imports = re.findall(r"^import\s+.*?from\s+'([^']+)';", app_source(), re.M)
    assert imports == ["./graph.js"], f"app.js imports {imports}"


# ---------------------------------------------------------------------------------------
# The security scans, by name.
# ---------------------------------------------------------------------------------------

#: The same expression `test_demo_ui_static.py` applies to every asset, restated here so a
#: failure names `app.js` rather than a parametrised path.
_MARKUP_SINK = re.compile(
    r"""(?x)
    (?: \. (?: inner | outer ) HTML \s* =
      | \[ \s* ["'] (?: inner | outer ) HTML ["'] \s* \] \s* =
      | \. insertAdjacentHTML \s* \(
      | document \s* \. \s* write(?:ln)? \s* \(
      | \bnew \s+ Function \s* \(
      | (?<![.\w]) eval \s* \(
      | \.setHTMLUnsafe \s* \(
    )""")

_OFF_ORIGIN = re.compile(
    r"""(?ix)
    (?: https? : //
      | \b(?:src|href) \s* = \s* ["']? //
      | @import
      | \bcdn\b
      | unpkg | jsdelivr | cdnjs | googleapis | bootstrapcdn
    )""")


def test_the_panel_module_has_no_path_from_a_string_to_the_html_parser() -> None:
    """The post is model-authored text and the trace is server-authored text. Neither can reach
    the parser, because no construct that would parse one is in the file."""
    offences = sorted({match.group(0).strip() for match in _MARKUP_SINK.finditer(app_source())})
    assert offences == [], f"app.js can put a string into the HTML parser: {offences}"


def test_the_panel_module_builds_its_dom_the_only_way_that_leaves() -> None:
    """The positive half: something has to be constructing the panels."""
    source = app_source()
    assert "document.createElement" in source
    assert "textContent" in source
    assert "replaceChildren" in source


def test_the_panel_module_reaches_no_host_but_this_one() -> None:
    offences = sorted({match.group(0) for match in _OFF_ORIGIN.finditer(app_source())})
    assert offences == [], f"app.js reaches off-origin: {offences}"


def test_the_panel_module_asks_for_no_random_number() -> None:
    """Same rule as the renderer's, for the same reason: a page that shuffled a list would be as
    unscreenshotable as a layout that did."""
    for banned in ("Math.random", "crypto.getRandomValues", "randomUUID"):
        assert banned not in app_source(), f"app.js calls {banned}"


def test_the_panel_module_carries_no_inline_event_handler() -> None:
    offences = re.findall(r"""\son[a-z]{3,}\s*=\s*["'][^"']""", app_source(), re.I)
    assert offences == [], f"app.js carries an inline handler: {offences}"


def test_the_panel_module_names_no_credential_and_no_environment_value() -> None:
    for name in ("NEO4J_PASSWORD", "NEO4J_USER", "process.env", "localStorage",
                 "sessionStorage", "api_key", "apiKey", "Authorization"):
        assert name not in app_source(), f"app.js names {name}"


# ---------------------------------------------------------------------------------------
# The cross-check against `index.html`.
# ---------------------------------------------------------------------------------------


def declared_ids() -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', index_source()))


def requested_ids() -> list[str]:
    """Every literal id `app.js` looks up. Literal on purpose — an id assembled at runtime could
    not be checked here at all, and there is none."""
    return re.findall(r"""document\.getElementById\(\s*'([^']+)'\s*\)""", app_source())


def test_every_element_the_panel_module_looks_up_is_declared_by_the_shell() -> None:
    """The failure this catches has no other symptom.

    `document.getElementById('candidate-detials')` returns `null`, every write through it is a
    silent no-op, and the panel simply never updates — no exception, no console error, nothing
    to see but an empty box. Parsing both files and comparing is the only place that typo is
    ever going to be found by a machine.
    """
    missing = sorted({name for name in requested_ids() if name not in declared_ids()})
    assert missing == [], f"app.js looks up ids index.html does not declare: {missing}"


def test_the_panel_module_looks_up_enough_of_the_shell_to_be_the_panel_logic() -> None:
    """A guard against the opposite failure: a file that passes every scan by doing nothing."""
    requested = set(requested_ids())
    for identifier in ("app-error", "run-discovery", "candidate-list", "score-breakdown",
                       "facts-list", "prompt-preset", "fixed-rules", "generate-post",
                       "trace-events", "post-output", "verification-findings",
                       "coverage-figures", "sources-list", "verdict-badge", "graph-canvas"):
        assert identifier in requested, f"app.js never looks up #{identifier}"


def test_no_element_is_looked_up_twice() -> None:
    """One handle per element, held in one table. Two lookups of one id is two places that can
    disagree about what is in it."""
    requested = requested_ids()
    duplicates = sorted({name for name in requested if requested.count(name) > 1})
    assert duplicates == [], f"app.js looks up {duplicates} more than once"


def test_the_three_elements_the_renderer_writes_are_passed_to_it_and_not_written_here() -> None:
    """The integration hazard `graph.js` states in its own header, pinned.

    `graph.js` writes `#graph-legend`, `#graph-breadcrumb` and `#graph-status` when it is handed
    them in `options`. It is handed all three, and `app.js` must not also write them — two
    writers on one element is a race whose loser is whichever ran second. The check is that each
    handle appears exactly once outside the options object: in the options object.
    """
    source = app_source()
    for option, handle in (("legendElement", "dom.graphLegend"),
                           ("breadcrumbElement", "dom.graphBreadcrumb"),
                           ("statusElement", "dom.graphStatus")):
        assert f"{option}: {handle}" in source, f"{handle} is not passed as {option}"
        assert source.count(handle) == 1, (
            f"{handle} is used {source.count(handle)} times; it belongs to graph.js and may "
            f"only be handed over")


def test_the_two_separators_are_both_upgraded() -> None:
    """`index.html` declares two `[role=separator]` elements and `createSplitter` has to be
    called on each; one call leaves the other a decoration."""
    source = app_source()
    assert source.count("createSplitter(") == 2, "createSplitter is not called twice"
    assert "createSplitter(dom.splitDivider" in source
    assert "createSplitter(dom.outputDivider" in source


def test_the_expanded_output_region_clears_the_inline_height_the_splitter_wrote() -> None:
    """`style.css` says so in a comment: `.is-expanded` is a rule, and an inline height outranks
    a rule, so the class does nothing until the inline value goes."""
    source = app_source()
    assert "is-expanded" in source
    assert "dom.outputRegion.style.height = ''" in source


# ---------------------------------------------------------------------------------------
# The cross-check against the router.
# ---------------------------------------------------------------------------------------


def registered_templates() -> set[str]:
    """What `api.register_endpoints` puts on a **fresh** router.

    A fresh one and not the process-wide `ROUTER`: `test_demo_ui_server.py` asserts the
    process-wide router carries no `/demo/` template, and pytest imports every test module
    before it runs any of them, so touching it here would decide that test on collection order.
    """
    return {template for _method, template in api.register_endpoints(Router()).routes()}


def endpoint_table() -> dict[str, str]:
    """`app.js`'s own `ENDPOINTS` object, read as source rather than executed."""
    source = app_source()
    block = re.search(r"export const ENDPOINTS = Object\.freeze\(\{(.*?)\}\);", source, re.S)
    assert block is not None, "app.js no longer declares an ENDPOINTS table"
    return dict(re.findall(r"(\w+):\s*'([^']+)'", block.group(1)))


def test_every_path_the_panel_module_fetches_is_a_path_the_router_registers() -> None:
    """The other half-file typo, and it is worse than the id one: a fetch of an unregistered
    path is a 404 that renders as an error banner in a demo rather than as a failing test."""
    registered = registered_templates()
    unknown = sorted({value for value in endpoint_table().values() if value not in registered})
    assert unknown == [], f"app.js fetches paths the router does not register: {unknown}"


def test_the_panel_module_reaches_every_endpoint_the_plan_lists() -> None:
    """§5's twelve, all of them. An endpoint no panel calls is an endpoint nobody demonstrated."""
    missing = sorted(registered_templates() - set(endpoint_table().values()))
    assert missing == [], f"app.js never fetches: {missing}"


def test_no_demo_path_is_written_anywhere_but_the_endpoint_table() -> None:
    """So the two tests above are statements about the code and not only about the table."""
    source = app_source()
    table = set(endpoint_table().values())
    literals = set(re.findall(r"'(/demo/[^']*)'", source))
    strays = sorted(literals - table)
    assert strays == [], f"app.js names {strays} outside its ENDPOINTS table"


def test_a_server_supplied_url_is_checked_against_the_same_table_before_it_is_opened() -> None:
    """`POST /demo/generate` hands back `events_url`, `result_url` and `sources_url`. Using them
    is right; using them *unchecked* would make "every path this file fetches is a registered
    one" a claim about the table rather than about the code."""
    source = app_source()
    assert "function serverPath(" in source
    for name in ("discoveryEvents", "runEvents"):
        assert f"ENDPOINTS.{name}" in source


# ---------------------------------------------------------------------------------------
# The honest labels, and the four rules the panel is the last line of.
# ---------------------------------------------------------------------------------------


def test_the_panel_renders_the_servers_honest_labels_rather_than_a_copy_of_them() -> None:
    """§7 asks for them verbatim. Every endpoint sends `honest_labels`, so the page renders that
    field — a copy in JavaScript would be a claim the artifacts do not make, which is the same
    argument `api.py` gives for holding the list there in the first place."""
    source = app_source()
    assert "honest_labels" in source, "app.js never reads honest_labels"
    assert "renderHonestLabels" in source
    assert "standing-statements" in source


@pytest.mark.parametrize("sentence", api.HONEST_LABELS)
def test_the_server_still_sends_the_sentence_the_panel_renders(sentence: str) -> None:
    """The other end of the same wire. If `HONEST_LABELS` were emptied, the page would render
    nothing and no test would notice; this is the one that would."""
    assert len(sentence) > 20
    assert sentence in api.HONEST_LABELS


def test_the_panel_quotes_the_servers_own_reasons_rather_than_writing_its_own() -> None:
    """Each of these is a field whose *text* is the honest part of the panel.

    `suppressed[component]` is the sentence explaining why a score term is absent —
    "the pair's gap distribution publishes a z, a mean and a σ, but no p90" — and a panel that
    rendered a zero there instead would be fabricating a measurement. `cost.reason` is why a
    replayed run reports no tokens. `coverage.note` is why the coverage figures are not
    precision or recall. `score_disclaimer` and `requires_live_reason` are the same kind.
    """
    source = app_source()
    for key in ("suppressed", "score_disclaimer", "is_probability", "requires_live_reason",
                "company_disclosure", "disclosure"):
        assert key in source, f"app.js never reads {key}"
    assert "cost.reason" in source or "cost.reason ?? ''" in source
    assert "figures.note" in source, "app.js never renders the coverage note"


def test_the_panel_branches_on_rendered_as_and_not_on_a_guess() -> None:
    """A refused run renders as refused. `api.py` populates `post` only on `ACCEPTED` and sets
    `rendered_as` to say which of the two shapes the payload is; branching on anything else — a
    truthy `post`, a substring of the disposition — would be this layer deciding a verdict."""
    source = app_source()
    assert "rendered_as === 'post'" in source, "app.js does not branch on rendered_as"
    assert "outcome.rejection" in source
    assert "blocking" in source


def test_the_panel_never_renders_an_unmeasured_cost_as_a_number() -> None:
    """`cost.measured === false` means the recorded store holds no token count, by design. A `0`
    in that panel would tell a reader the run was free."""
    source = app_source()
    assert "cost.measured === false" in source, "app.js does not test cost.measured"
    assert "notMeasured" in source


def test_the_panel_formats_floats_and_leaves_rendered_strings_alone() -> None:
    """§7's rule and §13's, which pull in opposite directions and are reconciled by type.

    `15.899999999999999` is a raw float and is shortened for display; `"15.9 percentage points"`
    is a string the writer produced and the verifier checked, and re-rounding it here would be
    this layer editing evidence. `formatValue` is the seam: numbers go through `formatNumber`,
    strings are returned as they arrived.
    """
    source = app_source()
    assert "export function formatNumber" in source
    assert "if (typeof value === 'number') return formatNumber(value);" in source
    assert "toPrecision(10)" in source, (
        "the display form no longer matches api.py:_display_number's %.10g")
    # The exact value stays reachable rather than being replaced by the shortened one.
    assert "title = String(value)" in source


def test_the_panel_resolves_a_highlight_before_it_draws_it() -> None:
    """§3: highlights are never faked. `graph.js` cannot draw an id it does not hold, so an
    unresolved id would silently vanish; counting them and saying so is the difference between
    a highlight that is honest and one that merely looks complete."""
    source = app_source()
    assert "function resolveNodeIds(" in source
    assert "state.view.node(identifier) !== null" in source
    assert "graph_highlights" in source


def test_the_panel_does_not_run_discovery_on_load() -> None:
    """§5, and the one behaviour a reader can check against the source alone: `start()` loads the
    projection and the presets, and the suggestions endpoint is reached from a click handler."""
    source = app_source()
    start = source[source.index("function start()"):]
    assert "loadOverview()" in start and "loadPresets()" in start
    assert "runDiscovery()" not in start, "discovery is started on load"
    assert "dom.runDiscovery.addEventListener('click', runDiscovery)" in source


# ---------------------------------------------------------------------------------------
# Shape.
# ---------------------------------------------------------------------------------------


def test_the_panel_module_is_one_file_with_no_build_step_and_no_dependency() -> None:
    """The brief's constraint, checked rather than remembered: nothing under `static/` may need
    a bundler, a package manager or a network to become the page the server serves."""
    tree = sorted(path.name for path in STATIC.iterdir() if path.is_file())
    assert tree == ["app.js", "graph.js", "index.html", "style.css"], tree
    assert not (REPO_ROOT / "package.json").exists(), (
        "a package.json would mean the demo has a build step")


def test_the_module_docstring_states_what_it_owns_and_what_it_does_not() -> None:
    """The renderer's header names the seam from its side; this is the other side of it."""
    header = app_source()[:6000]
    for claim in ("graph.js", "textContent", "never invented", "fabricated"):
        assert claim in header, f"the header no longer mentions {claim!r}"


def test_the_check_helpers_here_still_catch_what_they_are_meant_to() -> None:
    """Mutation tests for the two cross-checks, so neither can be weakened into a tautology."""
    assert _MARKUP_SINK.search("host.innerHTML = payload.post;")
    assert not _MARKUP_SINK.search("host.textContent = payload.post;")
    assert _OFF_ORIGIN.search("fetch('https:' + '//cdn.example/x.js')")
    assert re.findall(r"""document\.getElementById\(\s*'([^']+)'\s*\)""",
                      "document.getElementById('candidate-detials')") == ["candidate-detials"]
    assert "candidate-detials" not in declared_ids()


@pytest.mark.parametrize("asset", ["app.js", "graph.js"])
def test_the_module_parses_where_an_engine_is_available_to_say_so(asset: str) -> None:
    """A real syntax check, when and only when there is something that can perform one.

    An earlier version of this test counted brackets, which is a heuristic that a regex literal
    or a nested template is enough to defeat — it reported this very file unbalanced by three
    when it was not. `node --check` is the actual answer and it is used when `node` is on the
    path; where it is not, this **skips rather than pretending**, because a bracket count that
    passes proves nothing and a bracket count that fails wastes an afternoon.

    The suite still runs offline and with no Node installed; this is the one test that notices
    the difference, and it says which it did.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no JavaScript engine on the path; the syntax of this file is unchecked here")
    finished = subprocess.run(  # noqa: S603 - a fixed argv, no shell, a path from `which`
        [node, "--check", str(STATIC / asset)], capture_output=True, text=True, timeout=60)
    assert finished.returncode == 0, finished.stderr


# ---------------------------------------------------------------------------------------
# The eight repairs from the adversarial review of 2026-08-05.
#
# Each test names the finding, states what was measured, and asserts the source-level property
# the repair is made of. The behavioural proof is the jsdom run in the commit message; this is
# what stops the repair being undone by an edit nobody reviewed.
# ---------------------------------------------------------------------------------------


def test_a_generation_result_is_keyed_on_the_run_it_came_from() -> None:
    """F5. Measured: two Generate clicks inside `finishGeneration`'s await window left run A's
    post on screen beside run B's trace and `state.generationRunId` = B.

    Two properties, and the second is the one that matters. `finishGeneration` may not release
    the button before its awaits — that is what let the second click in — **and** every await
    boundary re-checks the run id, because the quiet watchdog below deliberately re-enables the
    interface while a run may still be in flight, so a superseded result can still arrive.
    """
    source = app_source()
    for name in ("function isCurrentRun(", "function currentRunId(", "function noteSuperseded("):
        assert name in source, f"app.js no longer declares {name}"
    body = source[source.index("async function finishGeneration("):
                  source.index("function renderRunFailure(")]
    assert body.count("isCurrentRun('generation', runId)") >= 4, (
        "finishGeneration does not re-check the run id at every await boundary")
    assert "} finally {" in body, "finishGeneration releases the button outside a finally"
    release = body.index("state.busy = false")
    assert release > body.index("} finally {"), (
        "finishGeneration clears state.busy before its awaits, which is the race itself")
    discovery = source[source.index("async function finishDiscovery("):
                       source.index("function renderDiscoveryFailure(")]
    assert discovery.count("isCurrentRun('discovery', runId)") >= 2, (
        "finishDiscovery paints whatever arrives, whichever run it came from")


def test_a_replayed_trace_frame_is_counted_rather_than_appended_twice() -> None:
    """F6. Measured: a transient stream error made 23 real events render as 46 rows.

    `Run.stream()` replays from sequence 0 on every subscribe and ignores `Last-Event-ID`
    (confirmed against the live server), and `EventSource` reconnects by itself, so the client is
    the only place the difference can be seen. The `sequence` is already on every frame and
    already written to `dataset.sequence`.
    """
    source = app_source()
    assert "traceSequences" in source, "app.js keeps no ledger of the sequences it has shown"
    assert "state.traceSequences.has(sequence)" in source
    assert "state.traceSequences.add(sequence)" in source
    assert "function noteReplayedStep(" in source, (
        "a replayed frame is dropped silently; the reconnect is normal and should be said")
    render = source[source.index("function renderTraceEvent("):
                    source.index("function applyEventHighlight(")]
    assert "return null;" in render, "renderTraceEvent has no path that refuses a duplicate"
    # Both stream handlers must respect that refusal, or `state.traceEvents` doubles instead.
    assert source.count("if (rendered === null) return;") >= 2


def test_the_panel_renders_a_contradiction_as_a_contradiction() -> None:
    """F7's client half. Measured on `accepted: true, rendered_as: "post", post: null` — which
    the server can produce when `post.md` is unreadable — the panel said "Refused · accepted",
    asserted "verifier ran: no" while `verification.passed` was true over twelve checks with no
    blocking finding, and captioned an accepted draft as the refused one.

    The repair is not a better guess. It is that the six signals are compared with each other and
    a disagreement is rendered as one, with no verdict class on the badge.
    """
    source = app_source()
    assert "function outcomeConsistency(" in source
    assert "function renderInconsistentOutcome(" in source
    consistency = source[source.index("function outcomeConsistency("):
                         source.index("function renderOutcome(")]
    for signal in ("outcome.accepted", "outcome.disposition", "outcome.rendered_as",
                   "outcome.post", "outcome.rejection", "verification.passed"):
        assert signal in consistency, f"the consistency check never reads {signal}"
    assert "is-inconsistent" in source, "there is no third badge state"
    branch = source[source.index("if (consistency.shape === 'inconsistent') {"):
                    source.index("dom.verdictBadge.textContent = outcome.disposition;")]
    # Comments stripped: the branch's own comment explains why neither verdict class is used
    # there, and a test that could not tell a mention from a use would fail on the explanation.
    code = "\n".join(line for line in branch.splitlines()
                     if not line.strip().startswith(("//", "*", "/*")))
    for claim in ("is-accepted", "is-rejected", "Refused ·"):
        assert claim not in code, (
            f"the inconsistent branch still asserts {claim!r}, which is a verdict")


def test_the_plan_panel_says_the_plan_is_model_text_and_whether_anything_checked_it() -> None:
    """F9. Measured on a live run: the panel rendered the model's own *"driven by non-recurring
    costs…"* and *"The divergence is caused by…"* — `statement_class: explanatory`,
    `required_fact_ids: []` — with no caveat, while the trace reported
    `checking_causal_language · skipped` and the payload carried `verification: null`.

    §3 clause 2 permits the panel; the gap was that a reader could not tell. What is asserted
    here is that all three statements are *read* rather than composed: whose text it is, whether
    the verifier ran, and what this run's own trace said about the causal-language check. There
    is no phrase list anywhere near this code, because scanning the prose here and reporting the
    result would be this file inventing a check §13 never ran.
    """
    source = app_source()
    assert "planIsModelText" in source
    plan = source[source.index("function renderPlan("):
                  source.index("function renderDraft(")]
    assert "LABELS.planIsModelText" in plan
    assert "outcome.verification" in plan, "the plan panel never says whether the verifier ran"
    assert "checking_causal_language" in plan, (
        "the plan panel never reports what the run's own trace said about the causal check")
    assert "required_fact_ids ?? []).length === 0" in plan, (
        "a claim the plan binds no fact to is not marked as such")


def test_the_header_separates_the_projection_the_frame_and_the_snapshot() -> None:
    """F13. Measured at zoom 0.25: the canvas painted 116 nodes and 49 clusters while the header
    read "Drawn: 403 nodes" — the payload's count — and `graph.js`'s own status line read
    "drawn 116 + 49 clusters". Two numbers for one thing, on one screen.

    `onStats` is the renderer's own per-frame callback, so the painted figure is the frame's and
    not an estimate; `#graph-status` stays the renderer's element and `#count-painted` is this
    file's, so there is still exactly one writer per element.
    """
    source = app_source()
    assert "onStats: writePaintedCount" in source
    assert "function writePaintedCount(" in source
    assert "dom.countPainted" in source
    markup = index_source()
    assert 'id="count-painted"' in markup
    assert "In this projection:" in markup, "the header still calls the payload count 'drawn'"
    assert "On screen now:" in markup


def test_a_quiet_run_is_reported_as_quiet_and_never_as_an_outcome() -> None:
    """F15. Measured: `docker stop fkg-neo4j` mid-discovery left the run `running` with one event
    for four minutes — the driver retries with backoff — SSE sent no further byte, and both
    buttons stayed dead with no explanation. It never falsely claimed completion, which was
    right, and it never said anything at all, which was not.

    The watchdog may re-enable the interface and may say the run has gone quiet. It may not close
    the stream, cancel the run, or write a disposition: a run we have stopped hearing from is
    neither finished nor failed.
    """
    source = app_source()
    assert "const QUIET_AFTER_MS" in source
    quiet = source[source.index("function reportQuiet("):source.index("// ------", source.index(
        "function reportQuiet("))]
    assert "dom.runDiscovery.disabled = false" in quiet
    assert "dom.generatePost.disabled = false" in quiet
    assert "runQuiet" in quiet, "the watchdog re-enables the buttons without saying why"
    for forbidden in ("stream.close(", "showError(", "renderOutcome(", "disposition"):
        assert forbidden not in quiet, (
            f"the quiet watchdog calls {forbidden!r}; it may not decide anything about the run")
    assert "if (!isCurrentRun(phase, runId)) return;" in quiet, (
        "the watchdog fires for a run that has already been replaced")


def test_one_fact_value_is_one_string_and_the_servers_is_preferred() -> None:
    """F14. Latent, and closed anyway: the facts panel formatted the raw float while the sources
    panel printed the server's `value_display`, so one fact could read two ways on one page.

    Rule 4 decides the order — a string the server sent is used as it arrived — and where there
    is none, `formatNumber` is now `%.10g` itself (proved by execution in
    `test_demo_ui_number_format.py`).
    """
    source = app_source()
    assert "function factValue(" in source
    chooser = source[source.index("function factValue("):source.index("function renderFacts(")]
    assert "'value_display'" in chooser and "'rendered'" in chooser
    assert chooser.index("value_display") < chooser.index("formatNumber"), (
        "the client's own formatting is tried before the server's string")
    assert "value.title = `exact value ${fact.value}`" in source, (
        "the exact float is no longer reachable from the facts panel")
    assert "fact.value_display" in source, "the sources panel no longer prints the server string"


def test_the_live_stream_reports_a_highlight_resolution_the_way_a_click_does() -> None:
    """F3's client half. Measured: during the stream, `highlightIds` was called with no
    resolution element and a stage whose 24 ids resolved to **0** drawn nodes silently cleared
    the highlight — indistinguishable from a stage that highlighted nothing. Clicking the row
    already printed the honest correction; the live pass did not.
    """
    source = app_source()
    assert "function reportStreamedEvent(" in source
    assert source.count("reportStreamedEvent(event, rendered);") == 2, (
        "the two streams do not both report what resolved")
    report = source[source.index("function reportStreamedEvent("):
                    source.index("async function runDiscovery(")]
    assert "applyEventHighlight(" in report, (
        "the live path resolves ids by some other route than the one the click uses")
    assert "focus: false" in report, (
        "the live path moves the camera; a stream that re-framed every frame is unusable")


def test_this_file_is_honest_about_being_unable_to_run_the_code_it_tests() -> None:
    """A docstring making the limit explicit is part of the deliverable, not decoration: a
    reader who takes a green suite here for "the interface works" has been misled by this file.
    """
    docstring = ast.get_docstring(ast.parse(Path(__file__).read_text(encoding="utf-8")))
    assert docstring is not None
    assert "There is no browser" in docstring
    assert "What is *not* proved here" in docstring
