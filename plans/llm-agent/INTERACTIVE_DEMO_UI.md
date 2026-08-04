# Interactive demo UI — implementation plan

A local, single-page interface over the accepted story-agent pipeline: explore the graph, run
discovery on demand, watch it happen, select a candidate, inspect its facts and sources, edit
the prompts, generate a post, and watch the deterministic verifier judge it.

**Written 2026-08-04** from worktree `FKG-story-agent-impl`, branch `impl/story-agent-v1`, base
`dd0fb39`. Companion to [V1_STORY_AGENT.md](V1_STORY_AGENT.md) and
[IMPLEMENTATION_STEPS.md](IMPLEMENTATION_STEPS.md).

**This is a working demo UI, not a mockup.** Every number, highlight and trace event resolves
from a real artifact or a real read-only query.

---

## 1. Architecture — and the dependency question that decided it

**Selected: stdlib `http.server` + a zero-dependency vanilla-JS single page.** Run with
`python -m story ui`.

The deciding fact, measured 2026-08-04: `fastapi`, `uvicorn` and `starlette` **are installed in
the conda environment but are not declared in `pyproject.toml`** —

```
dependencies = ["httpx>=0.27", "pydantic>=2.6", "PyYAML>=6.0", "neo4j>=6.2,<7"]
```

`pyproject.toml` is owned by the factual-spine workstream and this workstream may not edit it
(WORKSTREAM_BOUNDARY §2). Building on FastAPI would mean the demo depends on a package the
project does not declare — an undeclared dependency is worse than an honest stdlib one, and
declaring it is an ownership violation. The founder-gate test *"a new major dependency is
unavoidable"* is therefore **not met**: `http.server` serves JSON, SSE and static files for a
single local user without ceremony.

| Option | Verdict |
| --- | --- |
| **stdlib `http.server` + vanilla JS** | **Selected.** Zero new dependencies, no `pyproject.toml` edit, first in the brief's preference order |
| FastAPI + uvicorn | Installed but undeclared. Rejected — would require editing a file this workstream does not own |
| A JS graph library (cytoscape, d3, vis) | Rejected. No npm in this repo, and a CDN makes a *local* demo depend on the network. The canvas renderer below is ~400 lines and deterministic |
| A heavy app framework | Rejected by the brief and unnecessary |

**Graph rendering is hand-written**: canvas 2D, a seeded deterministic force-directed layout, no
`Math.random()` anywhere. Determinism matters for the same reason it does everywhere else in
this pipeline — a layout that moves between runs cannot be screenshotted, tested, or trusted.

### Package shape

```
story/demo_ui/__init__.py        public surface
story/demo_ui/server.py          ThreadingHTTPServer, routing, SSE
story/demo_ui/api.py             endpoint handlers -> plain dicts
story/demo_ui/projection.py      bounded graph overview + focused subgraphs
story/demo_ui/discovery.py       all four detectors + ranking, emitting trace events
story/demo_ui/trace.py           the structured event type and the emitter
story/demo_ui/prompt_presets.py  presets + the editable/fixed split
story/demo_ui/static/            index.html, app.js, graph.js, style.css
```

`demo_ui` imports `story.pipeline`, `story.stages.*` and `story.contracts` — never the reverse.
A structural test asserts no accepted stage imports `story.demo_ui`, so the UI cannot become
load-bearing for the pipeline.

---

## 2. What already exists, and what has to be built

Measured against the tree at `dd0fb39`.

| Need | Exists? |
| --- | --- |
| Four detectors | **Yes** — `detect_metric_moves_from_load`, `detect_trend_reversals_from_load`, `detect_acceleration_from_load`, `detect_cross_metric_divergence`. The demo path runs **only D4**; the UI runs all four |
| Ranking with named components | **Yes** — `rank_candidates`, `ScoreBreakdown`, `RANKING_POLICY_VERSION = 1.1.0`, components `magnitude_z`, `magnitude_units`, `novelty`, plus a `suppressed` map giving *the reason* a component is absent |
| Bounded evidence package | **Yes** — `BoundedEvidencePackageBuilder`, with `budget.parameters`, `section_counts`, `caps_hit` |
| Planner / writer / verifier | **Yes** — `run_demo` drives all three |
| Read-only graph access | **Yes** — `BoundedGraphRetriever` over `ReadQueryExecutor`, nine bounded tools, AST-scanned for write clauses |
| Artifacts | **Yes** — eight files incl. `rejected.json`; **`trace_events.jsonl` is new** |
| An HTTP server | **No** — built here |
| A graph projection for display | **No** — built here (§4) |
| Discovery as a streamable operation | **No** — `resolve_demo_inputs` is one blocking call; the UI needs staged events |

**`ScoreBreakdown.suppressed` is the single most valuable thing the UI inherits.** It carries a
per-component *reason* a term is missing, so the interface can say "no magnitude term: fewer
than eight prior deltas" instead of rendering a silent zero. That is the difference between an
honest score panel and a fabricated one.

---

## 3. The trace rule — how it is enforced, not just stated

**No hidden chain-of-thought, ever.** The panel is called **Process trace**.

A trace event may only be one of two things, and the type system says so:

1. **An operational event emitted by our own code** — stage, status, counts, timing, and real
   entity ids. Emitted by `trace.py` at points *we* instrument; the model cannot author one.
2. **A structured-output summary** derived from a field the model already returned under schema
   constraint (`plan.thesis`, `len(draft.sentences)`, a finding's `code`). Rendered from the
   parsed object, never from free text.

Enforcement, so this is a property rather than a promise:

- `TraceEvent` is a frozen model with a closed `stage` enum and no free-text field the model can
  reach. The `message` is composed by our code from counts and ids.
- **`GenerationResult.raw_content` is never routed to a trace event or to any endpoint**, and a
  test asserts that no response body contains it.
- `trace_events.jsonl` is written from the same typed events, so the artifact and the UI cannot
  diverge.
- A test asserts every emitted `stage` is in the closed set and every `highlight_node_ids` entry
  resolves to a real id in the package, the candidate, or the projection.

**Highlights are never faked.** `graph_highlights.node_ids` must resolve against ids the run
actually produced; the test above is the guard.

---

## 4. Graph projection strategy

28,837 nodes cannot be drawn. The overview is a **deterministic bounded projection**, disclosed
as such in the header.

- **Overview** — the company entity, all metrics, document hubs, and a deterministic sample of
  observations/passages, with stable `ORDER BY` on ids and an explicit `LIMIT`. Target 300–800
  nodes. Dense regions aggregate into a single node carrying a count until zoomed.
- **Story neighbourhood** — the selected candidate's metrics, their observations for the
  candidate's periods, and the entity.
- **Evidence neighbourhood** — the package's facts, their passages, and those passages'
  documents; `quoted_text` rides on the `EVIDENCED_BY` relationship.

Every query: `RoutingControl.READ`, an explicit `timeout_seconds`, an explicit `LIMIT`, no
variable-length unbounded path, stable ordering, and a Cypher constant that is an `ast.Constant`
— reusing the same executor and the same AST scan §16 already enforces. **No Cypher from the
browser, ever**: the client names a projection and passes bound parameters.

Levels of detail: overview → cluster → candidate neighbourhood → evidence neighbourhood. Labels
on hover, selection, or sufficient zoom; hub nodes keep labels; everything else is a point.

---

## 5. API

Handlers return plain JSON-serialisable dicts validated against the same pydantic models the
pipeline uses.

| Endpoint | Purpose |
| --- | --- |
| `GET  /demo/graph/overview` | bounded projection + counts + snapshot identity + legend |
| `GET  /demo/graph/subgraph?candidate_id=…&view=story\|evidence` | focused neighbourhood |
| `POST /demo/story-suggestions` | start discovery; returns a `run_id` |
| `GET  /demo/story-suggestions/{run_id}/events` | **SSE** stream of `TraceEvent` |
| `GET  /demo/story-suggestions/{run_id}` | ranked candidates once complete |
| `GET  /demo/candidates/{candidate_id}` | candidate detail, score breakdown, facts |
| `POST /demo/evidence-package` | build/rebuild and hash a package |
| `GET  /demo/prompt-presets` | presets + the editable/fixed split |
| `POST /demo/generate` | start generation; returns a `run_id` |
| `GET  /demo/runs/{run_id}/events` | **SSE** stream for planner/writer/verifier |
| `GET  /demo/runs/{run_id}` | outcome: plan, draft, verification, post |
| `GET  /demo/runs/{run_id}/sources` | grouped sources with quoted text |

**SSE over `http.server`** on a `ThreadingHTTPServer`: one thread per stream, events flushed as
they are emitted, a terminal event closing the stream. Simpler than WebSockets and adequate for
one local user.

Suggestions are **not** computed on page load. Before the button is pressed the panel explains
that suggestions are derived from metric histories, comparability rules and deterministic
ranking.

---

## 6. Prompts — editable vs immutable

Two visually distinct regions. **Editable user instructions**: planner instructions, writer
instructions, style guidance, presets (Investor summary · Why it matters · Data-first analysis ·
Neutral research note · Concise social post · Custom), duplicate, edit, reset, diff against
default, save for the session.

**Fixed factual-safety rules**, rendered read-only and not accepted from the client under any
key: the bounded-evidence rule, citation requirements, the numeral-binding rule, the
reported-vs-calculated split, and every §13 obligation.

The server is authoritative. The client sends only `planner_instructions`,
`writer_instructions`, `style_guidance` — each length-bounded — and the server **composes** the
effective prompt with the fixed sections in fixed positions. A test asserts that a request
attempting to overwrite a fixed section changes nothing in the composed prompt.

The user cannot disable bounded evidence, citations, deterministic verification,
unsupported-number rejection, period checking, metric checking or stale-package checking —
because none of those is reachable from the request body. **Verification runs on the server
after generation regardless of what was asked for.**

Custom text requesting unsupported behaviour (*invent a reason*, *remove citations*,
*exaggerate*, *add facts not in evidence*) raises a visible advisory warning. It is a warning,
not a block: the deterministic verifier is what actually stops it, and pretending the prompt
filter is the safety boundary would misrepresent where authority lives.

Editing a prompt changes `request_identity`, so it **misses the replay store and requires
`--live`**. The UI states this rather than silently returning a stale draft.

---

## 7. Honest labels

Rendered verbatim in the interface:

- Story selection can be manual for the demo (`selection_mode: manual_demo_candidate`).
- **Ranking scores are prioritisation heuristics, not probabilities.**
- Process traces show pipeline activity, **not private model chain-of-thought**.
- Deterministic verification remains authoritative.
- The graph view is a **bounded visual projection**, not the whole graph.

Coverage figures are reported only where genuinely computed — claim coverage, citation coverage,
numerical-support coverage, blocking findings, warnings — and **never** as precision, recall or
confidence. Float residue is formatted for display (`15.899999999999999` → `15.9`) while the
underlying value stays exact in the inspectable panel; the *rendered* string is what §13 checks.

---

## 8. Waves

1. Event contract + trace, projection queries, server skeleton, static shell.
2. Discovery with streamed trace, candidate list/detail, facts + package preview, prompt editor.
3. Generation flow, planner + draft visualisation, verification panel, final post + sources.
4. Integration, fake-provider and live-Qwen runs, performance, adversarial review, repairs.

## 9. Tests

`tests/story/test_demo_ui_*.py`, offline by default: projection bounds/ordering/no-write/timeouts;
discovery event order and id resolution; score labels not probabilities; prompt immutability and
size bounds; fake-provider end-to-end accepted **and** rejected; source link resolution
sentence → fact → citation → passage → document; and the security set — no `raw_content` in any
response, no secrets, no client Cypher, id validation, bounded prompt text, safe rendering.

## 10. Out of scope

Publishing. Authentication. Multi-user state. Writing anything to Neo4j or to an authoritative
catalog. Model-assisted verification (S10 remains deferred).
