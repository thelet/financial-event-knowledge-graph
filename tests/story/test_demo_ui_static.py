"""The static assets: what they must contain, what they must never contain, and that they serve.

No browser is available in this environment, so nothing here executes a line of JavaScript. That
is a real limit and it is stated rather than papered over: these are **source-level** properties,
checked by parsing and by pattern, plus one end-to-end check that the standard-library server
hands the files back with the right content type.

Three of them are worth reading first.

* `test_no_asset_reaches_any_host_but_this_one` is the no-CDN rule. A local demo that pulls a
  graph library off a network is a demo that stops working on a train, and every argument for
  hand-writing the renderer rests on this staying true.
* `test_no_asset_assigns_innerHTML_or_any_other_markup_sink` is the injection rule. The post is
  model-authored text; a Markdown renderer that reaches for `innerHTML` hands a model the ability
  to run script in the page. The scan is textual and cannot prove that a given renderer escapes
  its input — what it *can* prove is that no code path exists that would let unescaped text reach
  the HTML parser at all, which is the stronger and simpler property. Its own mutation test,
  `test_the_markup_sink_scan_catches_the_spellings_it_is_meant_to`, is what keeps it from being
  weakened by a one-character edit.
* `test_graph_js_contains_no_random_source` is the determinism rule, and it is checked the same
  way the Cypher scan checks the read-only rule: against the source, not against a promise.

**Every scan walks `story/demo_ui/static/` as a directory.** `app.js` does not exist as this file
is written — the panel logic is a later step — and the moment it lands it is covered by all of
them without an edit here.
"""

from __future__ import annotations

import http.client
import re
import threading
from pathlib import Path

import pytest

from story.demo_ui.runs import RunRegistry
from story.demo_ui.server import CONTENT_TYPES, Router, build_app, build_server

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "story" / "demo_ui" / "static"

#: The files this step owns. `app.js` is deliberately absent: it is another step's file, and a
#: test that required it would fail for a reason that is not about the assets that exist.
REQUIRED_ASSETS = ("index.html", "style.css", "graph.js")

ASSET_SUFFIXES = (".html", ".css", ".js")


def assets() -> list[Path]:
    """Every asset in the static tree, whoever wrote it."""
    return sorted(path for path in STATIC.rglob("*") if path.suffix in ASSET_SUFFIXES)


def asset_ids(path: Path) -> str:
    return path.name


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------
# The files exist, and the server hands them back.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_ASSETS)
def test_the_asset_exists_and_is_not_empty(name: str) -> None:
    path = STATIC / name
    assert path.is_file(), f"{name} is missing from {STATIC}"
    assert len(path.read_bytes()) > 500, f"{name} is a stub"


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


@pytest.mark.parametrize("path,name", [
    ("/", "index.html"),
    ("/index.html", "index.html"),
    ("/style.css", "style.css"),
    ("/graph.js", "graph.js"),
])
def test_the_server_serves_the_asset_byte_for_byte(client, path: str, name: str) -> None:
    """Including `/`, which is what the placeholder page used to answer before this step."""
    status, content_type, body = client(path)
    assert status == 200
    assert content_type == CONTENT_TYPES[(STATIC / name).suffix]
    assert body == (STATIC / name).read_bytes()


def test_the_placeholder_page_is_gone_now_that_the_interface_exists(client) -> None:
    _, _, body = client("/")
    assert b"has not been built into" not in body


# ---------------------------------------------------------------------------------------
# No network but this one.
# ---------------------------------------------------------------------------------------

#: Anything that would reach off-origin: a scheme, a protocol-relative URL, or a CSS import.
#: `data:` is not here — an inlined asset touches no host — and neither is a bare `//`, which is
#: every line comment in the JavaScript.
_OFF_ORIGIN = re.compile(
    r"""(?ix)
    (?: https? : //          # an absolute http(s) URL
      | \b(?:src|href) \s* = \s* ["']? // # a protocol-relative reference
      | @import
      | \bcdn\b
      | unpkg | jsdelivr | cdnjs | googleapis | bootstrapcdn
    )""")

#: The one absolute URL that is allowed to appear, and only in a place that is not a fetch: an
#: XML namespace or a documentation link inside a comment would both be prose. There is none
#: today, so the list is empty and the rule is simply "no host".
_ALLOWED_OFF_ORIGIN: tuple[str, ...] = ()


@pytest.mark.parametrize("path", assets(), ids=asset_ids)
def test_no_asset_reaches_any_host_but_this_one(path: Path) -> None:
    """No CDN, no webfont, no protocol-relative script. The demo runs with the cable pulled."""
    text = path.read_text(encoding="utf-8")
    offences = [
        match.group(0) for match in _OFF_ORIGIN.finditer(text)
        if match.group(0) not in _ALLOWED_OFF_ORIGIN
    ]
    assert offences == [], f"{path.name} reaches off-origin: {offences}"


def test_the_scan_for_off_origin_references_catches_what_it_is_meant_to() -> None:
    """The scan's own mutation test. A rule with none can be weakened by a one-character edit."""
    for mutation in (
        '<script src="https://cdn.jsdelivr.net/npm/d3"></script>',
        '<script src="//unpkg.com/cytoscape"></script>',
        '@import url("theme.css");',
        "@font-face { src: url(https://fonts.googleapis.com/x.woff2); }",
    ):
        assert _OFF_ORIGIN.search(mutation), f"{mutation!r} would not be flagged"
    for allowed in ('<script type="module" src="app.js">',
                    'background: url(data:image/png;base64,AA)'):
        assert not _OFF_ORIGIN.search(allowed), f"{allowed!r} is local and was flagged"


def test_index_loads_the_panel_module_and_nothing_else(client) -> None:
    """One module, by relative path. An inline script body would also defeat the page's CSP."""
    text = read("index.html")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", text, re.S | re.I)
    assert scripts, "index.html loads no module at all"
    for attributes, body in scripts:
        assert body.strip() == "", "an inline script body is not allowed on this page"
        assert 'type="module"' in attributes
        assert re.search(r'src="[a-z0-9_.-]+"', attributes), attributes


# ---------------------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------------------


def test_graph_js_contains_no_random_source() -> None:
    """The layout is seeded from node ids. A random number in it would make every screenshot,
    and every comparison against yesterday's, meaningless."""
    text = read("graph.js")
    for banned in ("Math.random", "crypto.getRandomValues", "randomUUID"):
        assert banned not in text, f"graph.js calls {banned}"


@pytest.mark.parametrize("path", assets(), ids=asset_ids)
def test_no_asset_anywhere_reaches_for_a_random_number(path: Path) -> None:
    """Widened past `graph.js` on purpose: a panel that shuffled a list would be as unscreenshot
    -able as a layout that did."""
    text = path.read_text(encoding="utf-8")
    assert "Math.random" not in text, f"{path.name} calls Math.random"


def test_the_layout_seed_is_derived_from_the_node_id() -> None:
    """The positive half of the determinism rule: something has to be doing the seeding."""
    text = read("graph.js")
    assert "export function hash32" in text
    assert "hash32(node.id)" in text
    assert "layoutIterations" in text


# ---------------------------------------------------------------------------------------
# Injection.
# ---------------------------------------------------------------------------------------

#: Every way a string can reach the HTML parser, plus the two script-from-string constructors.
#: `insertAdjacentHTML` and `document.write` are here because forbidding `innerHTML` alone moves
#: the problem rather than solving it.
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


@pytest.mark.parametrize("path", assets(), ids=asset_ids)
def test_no_asset_assigns_innerHTML_or_any_other_markup_sink(path: Path) -> None:
    """The rendering path is escape-safe because there is no unescaping path in it.

    **What this proves and what it does not.** It proves no asset contains a construct that turns
    a string into markup or into code — so no server value, and no model-authored sentence, can
    reach the HTML parser however it was assembled. It does *not* prove that a given Markdown
    renderer produces correct output, or that a `textContent` write is placed where the author
    intended; only a browser could show that, and there is none here. The stronger guarantee is
    the structural one: `textContent` and `createElement` cannot parse markup, so escaping is not
    a step that can be forgotten.
    """
    text = path.read_text(encoding="utf-8")
    offences = sorted({match.group(0).strip() for match in _MARKUP_SINK.finditer(text)})
    assert offences == [], (
        f"{path.name} can put a string into the HTML parser or into a compiler: {offences}. "
        "Build with createElement/textContent; clear with replaceChildren().")


def test_the_markup_sink_scan_catches_the_spellings_it_is_meant_to() -> None:
    """Mutation test. Each of these is a real way the rule has been evaded in real code."""
    for mutation in (
        "node.innerHTML = post.text;",
        'card["innerHTML"] = value;',
        "host.outerHTML = markup;",
        "list.insertAdjacentHTML('beforeend', row);",
        "document.write(payload);",
        "const render = new Function('text', body);",
        "eval(fromServer);",
        "target.setHTMLUnsafe(text);",
    ):
        assert _MARKUP_SINK.search(mutation), f"{mutation!r} is a markup sink and was not flagged"
    for allowed in (
        "cell.textContent = String(value);",
        "host.replaceChildren();",
        "const parsed = JSON.parse(body);",
        "// innerHTML is never used here",
        "element.evaluate(x);",
    ):
        assert not _MARKUP_SINK.search(allowed), f"{allowed!r} is safe and was flagged"


@pytest.mark.parametrize("path", assets(), ids=asset_ids)
def test_no_asset_carries_an_inline_event_handler(path: Path) -> None:
    """`onclick="…"` is script in an attribute; every listener here is added with
    `addEventListener`, which is also what the page's CSP allows."""
    text = path.read_text(encoding="utf-8")
    offences = re.findall(r"""\son[a-z]{3,}\s*=\s*["'][^"']""", text, re.I)
    assert offences == [], f"{path.name} carries an inline handler: {offences}"


def test_no_asset_imports_a_module_named_by_a_value() -> None:
    """A dynamic `import(expression)` would let a server string decide what code runs."""
    for path in assets():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?<![.\w])import\s*\(([^)]*)\)", text):
            argument = match.group(1).strip()
            assert argument.startswith(("'", '"')), (
                f"{path.name} imports a module named by an expression: {argument!r}")


def test_the_page_declares_a_policy_that_forbids_off_origin_loads_at_runtime() -> None:
    """Belt and braces: the scans above are the guard that works in every browser, and this is
    the one that works at runtime in the browser the demo is driven in. `require-trusted-types-
    for` is enforced by Chromium and ignored elsewhere, which is why it is not the only guard."""
    text = read("index.html")
    policy = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', text)
    assert policy is not None, "index.html declares no Content-Security-Policy"
    directives = policy.group(1)
    assert "default-src 'none'" in directives
    assert "script-src 'self'" in directives
    assert "connect-src 'self'" in directives
    assert "require-trusted-types-for 'script'" in directives
    assert "unsafe-eval" not in directives
    assert "script-src 'self' 'unsafe-inline'" not in directives


# ---------------------------------------------------------------------------------------
# The DOM contract `app.js` codes against.
# ---------------------------------------------------------------------------------------

#: Pinned here because `app.js` is written against them by another step and a rename that broke
#: it would otherwise be found by a person clicking, not by the suite. Grouped as the layout
#: groups them.
REQUIRED_IDS = (
    # Header and identity.
    "app-header", "snapshot-run-id", "snapshot-completed-at", "header-company", "pipeline-state",
    "count-drawn-nodes", "count-drawn-edges", "count-total-nodes", "count-total-edges",
    "projection-disclosure", "company-disclosure", "app-error",
    "settings", "setting-aggregate", "setting-labels", "setting-motion", "setting-synthesised",
    # Graph region.
    "workspace", "graph-region", "split-divider", "graph-toolbar",
    "view-full", "view-story", "view-evidence", "graph-breadcrumb",
    "graph-zoom-in", "graph-zoom-out", "graph-fit", "graph-reset",
    "graph-stage", "graph-canvas", "graph-empty", "graph-legend", "graph-status",
    "node-detail-card", "node-detail-title", "node-detail-close", "node-detail-derived",
    "node-detail-properties",
    # Side panel.
    "side-panel", "panel-tabs", "tab-story", "tab-facts", "tab-prompts", "tab-trace",
    "panel-story", "panel-facts", "panel-prompts", "panel-trace",
    "run-discovery", "discovery-state", "discovery-explainer", "candidate-list",
    "candidate-detail", "candidate-title", "candidate-properties", "score-breakdown",
    "build-package", "package-summary", "facts-list", "package-bounds",
    "prompt-preset", "prompt-reset", "prompt-diff-toggle", "prompt-diff",
    "planner-instructions", "writer-instructions", "style-guidance", "prompt-lengths",
    "prompt-warnings", "fixed-rules", "replay-notice", "generate-post", "trace-events",
    # Output region.
    "output-divider", "output-region", "output-tabs", "tab-post", "tab-verification",
    "tab-sources", "panel-post", "panel-verification", "panel-sources",
    "post-output", "post-meta", "verdict-badge", "output-expand",
    "verification-summary", "verification-findings", "coverage-figures", "sources-list",
    # Standing statements.
    "standing-statements",
)


def declared_ids() -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', read("index.html")))


@pytest.mark.parametrize("identifier", REQUIRED_IDS)
def test_the_shell_declares_the_container_app_js_codes_against(identifier: str) -> None:
    assert identifier in declared_ids(), f"index.html no longer declares #{identifier}"


def test_no_identifier_is_declared_twice() -> None:
    found = re.findall(r'\bid="([^"]+)"', read("index.html"))
    duplicates = sorted({name for name in found if found.count(name) > 1})
    assert duplicates == [], f"getElementById would be ambiguous for {duplicates}"


def test_every_tab_button_points_at_a_panel_that_exists() -> None:
    """The tab skeleton is a contract too: `app.js` toggles `hidden` on what `data-panel` names."""
    text = read("index.html")
    ids = declared_ids()
    controls = re.findall(r'data-panel="([^"]+)"', text)
    assert len(controls) == 7, f"expected seven tabs, found {controls}"
    for panel in controls:
        assert panel in ids, f"a tab points at #{panel}, which does not exist"
    for match in re.finditer(r'role="tab"[^>]*aria-controls="([^"]+)"', text):
        assert match.group(1) in ids


def test_the_two_separators_name_the_regions_they_resize() -> None:
    """`createSplitter` reads `data-axis` and `data-target` off the element, so the shell decides
    what moves and the module decides how."""
    text = read("index.html")
    separators = re.findall(r'<div id="([^"]+)"[^>]*role="separator"[^>]*>', text)
    assert sorted(separators) == ["output-divider", "split-divider"]
    assert 'data-axis="x"' in text and 'data-target="graph-region"' in text
    assert 'data-axis="y"' in text and 'data-target="output-region"' in text


def test_the_stylesheet_carries_the_palette_the_renderer_reads() -> None:
    """`graph.js` resolves these with getComputedStyle so a legend swatch and the disc it
    describes cannot drift apart. A renamed property would silently grey the whole picture."""
    css = read("style.css")
    javascript = read("graph.js")
    for index in range(8):
        assert f"--graph-palette-{index}:" in css
    for name in ("--graph-surface", "--graph-edge", "--graph-label", "--graph-label-halo",
                 "--graph-selection", "--graph-highlight", "--graph-tooltip",
                 "--graph-tooltip-ink"):
        assert f"{name}:" in css, f"style.css no longer defines {name}"
        assert name in javascript, f"graph.js no longer reads {name}"
    assert "--graph-palette-${index}" in javascript


def test_the_renderer_exports_the_api_the_panel_step_calls() -> None:
    """The other half of the contract. Named here so a rename shows up as a failing test rather
    than as a blank canvas."""
    text = read("graph.js")
    for name in ("createGraphView", "createSplitter", "hash32", "DEFAULTS"):
        assert re.search(rf"^export (?:function|const) {name}\b", text, re.M), (
            f"graph.js no longer exports {name}")
    methods = (
        "setPayload", "pushView", "popTo", "back", "breadcrumb", "payload", "node", "edge",
        "edgesOf", "legend", "select", "selection", "hovered", "highlight", "clearHighlight",
        "highlighted", "focusOn", "fit", "zoomBy", "camera", "setCamera", "resetLayout",
        "setOptions", "resize", "render", "stats", "destroy",
    )
    for method in methods:
        assert f"view.{method} = function" in text, f"graph.js no longer defines view.{method}"


# ---------------------------------------------------------------------------------------
# The honest labels.
# ---------------------------------------------------------------------------------------

#: §7 of INTERACTIVE_DEMO_UI.md, rendered verbatim, plus the two the payload's own disclosure
#: fields are about. Checked as sentences rather than as keywords: "not probabilities" is the
#: whole point of the line and a paraphrase that lost it would pass a keyword test.
STANDING_STATEMENTS = (
    "bounded visual projection of the loaded graph, not the whole graph",
    "Story selection can be manual for the demo",
    "Ranking scores are prioritisation heuristics, not probabilities",
    "Process traces show pipeline activity, not private model chain-of-thought",
    "Deterministic verification remains authoritative",
    "computed from a stored property, not read as its own record",
    "The graph stores no nulls, so an absent property means the value was not recorded",
    "No credential, provider setting or environment value is shown anywhere in this interface",
)


@pytest.mark.parametrize("sentence", STANDING_STATEMENTS)
def test_the_shell_states_the_thing_it_is_not(sentence: str) -> None:
    assert sentence in read("index.html"), f"index.html no longer says: {sentence!r}"


def test_the_header_leaves_room_for_the_payloads_own_disclosure_rather_than_restating_it() -> None:
    """The projection sends `disclosure` and `company_disclosure`; the shell renders them. What it
    must not do is hard-code the snapshot's node count, which is 28,837 today and will not be
    after the next load."""
    text = read("index.html")
    assert 'id="projection-disclosure"' in text
    assert 'id="company-disclosure"' in text
    assert "28,837" not in text and "28837" not in text, (
        "the snapshot's node count is hard-coded into the page and will go stale")


def test_the_derived_marking_is_a_visible_style_and_not_only_a_word() -> None:
    """A synthesised node is dashed on the canvas and its legend swatch is dashed too. Both
    behaviours are checked at their source, because neither can be seen from Python."""
    assert "node.synthesised && preferences.markSynthesised" in read("graph.js")
    assert "is-derived" in read("graph.js")
    assert ".legend-swatch.is-derived" in read("style.css")
    assert "border-style: dashed" in read("style.css")


def test_the_detail_card_says_absent_rather_than_filling_a_gap() -> None:
    """Neo4j stores no nulls (WORKSTREAM_BOUNDARY §4.2), so the card needs a way to render a
    property that is not there. The class exists and the sentence explaining it is on the page."""
    assert ".is-absent" in read("style.css")
    assert "an absent property means the value was not recorded" in read("index.html")


def test_no_asset_names_a_credential_or_an_environment_value() -> None:
    """The API sends none and the interface must not invent a place to put one."""
    banned = ("NEO4J_PASSWORD", "NEO4J_USER", "process.env", "localStorage.setItem",
              "api_key", "apiKey", "Authorization")
    for path in assets():
        text = path.read_text(encoding="utf-8")
        for name in banned:
            assert name not in text, f"{path.name} names {name}"


# ---------------------------------------------------------------------------------------
# The one thing a Python test can say about the payload the renderer was written for.
# ---------------------------------------------------------------------------------------


def test_the_renderer_reads_the_legend_from_the_payload_rather_than_from_a_type_list() -> None:
    """The node types in this graph are `metric`, `observation`, `passage`, `document`,
    `company` and `period`. **None of them appears in `graph.js`**, and that is the test: the
    legend, the colours and the level-of-detail ranking are all functions of what the payload
    declares, so a projection that grows a type needs no edit to the renderer."""
    text = read("graph.js")
    payload_types = ("'metric'", '"metric"', "'observation'", "'passage'", "'document'",
                     "'company'", "'period'", "'has_observation'", "'evidenced_by'",
                     "'about_subject'", "'part_of'")
    offences = [name for name in payload_types if name in text]
    assert offences == [], f"graph.js hard-codes payload type names: {offences}"
    assert "payload?.legend" in text, "the legend is not read from the payload"


def test_the_static_tree_is_json_free_so_no_fixture_can_be_mistaken_for_live_data() -> None:
    """A committed payload under `static/` would be servable, and a screenshot of it would look
    exactly like a screenshot of the real graph. There is no such file."""
    strays = [path.name for path in STATIC.rglob("*.json")]
    assert strays == [], f"the static tree holds a data file: {strays}"
    # And nothing in the assets embeds one either.
    for path in assets():
        text = path.read_text(encoding="utf-8")
        for marker in ('"graph_run_id"', '"content_digest"', '"nodes_by_type"'):
            assert marker not in text, f"{path.name} embeds a payload fragment: {marker}"
