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
story/demo_ui/runs.py            in-memory run registry + the id patterns
story/demo_ui/api.py             endpoint handlers -> plain dicts
story/demo_ui/projection.py      bounded graph overview + focused subgraphs
story/demo_ui/discovery.py       all four detectors + ranking, emitting trace events
story/demo_ui/trace.py           the structured event type and the emitter
story/demo_ui/prompt_presets.py  presets + the editable/fixed split
story/demo_ui/static/            index.html, app.js, graph.js, style.css
```

`runs.py` was not in this plan's first draft and is not a split for symmetry: a run's status,
its event list and the lock the SSE threads read it under are a cohesive concern that belongs
to neither the event type nor the socket, and putting it in `server.py` would have made the
transport the owner of the pipeline's state. It is also where the two client-supplied id
patterns live, so `RUN_ID_PATTERN` sits beside the code that mints the ids it matches.

`demo_ui` imports `story.pipeline`, `story.stages.*` and `story.contracts` — never the reverse.
A structural test asserts no accepted stage imports `story.demo_ui`, so the UI cannot become
load-bearing for the pipeline.

**And `demo_ui` imports no composition root either.** `story/cli.py:cmd_ui` builds the
`StoryContext` and the `DemoConfig` and passes them as lazy zero-argument factories in
`DemoUiApp.services`, under the keys `story_context` and `demo_config`. That is a rule and not a
preference: `tests/story/test_story_package_structure.py::
test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage` exempts five named entry
modules and walks the import closure of everything else, so a `demo_ui` module importing
`story.context` would put `neo4j` in that closure and fail the suite. It also means the server
starts, and its tests run, with no database running.

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

### Two corrections, found while building `api.py` *(verified 2026-08-04)*

**1. §1's "`api.py` calls `register(...)` at import time" is wrong, and the endpoints are
registered by an explicit `api.register_endpoints(router=None)` instead.**
`tests/story/test_demo_ui_server.py::test_the_process_wide_router_carries_the_liveness_route_
and_nothing_of_section_five` asserts the process-wide `ROUTER` holds no `/demo/` template, and
pytest imports every test module before it runs any of them — so an import-time registration
makes that committed test pass or fail on collection order. `register_endpoints` is idempotent
and defaults to the process-wide router, so the composition root's call is one line.

**2. `DemoUiApp.services` needs a third key, `story_pipeline`, for the same reason it needs the
first two.** `story/pipeline.py` names `story.context` under `TYPE_CHECKING`, and
`tests/story/test_story_package_structure.py::test_no_driver_is_reachable_from_the_contract_
the_core_or_any_stage` reads a guarded import exactly as it reads a plain one. Measured against
that test's own `closure()`: with `story/demo_ui/api.py` importing `story.pipeline`, the walked
set reaches `story.providers.neo4j_connection imports neo4j` and the suite fails; without it,
it does not. `importlib` is not the escape — that test's docstring names hiding an import behind
it as a defeat of the check. So `story/cli.py:cmd_ui` owns the third factory:

```python
from .demo_ui import api

def story_pipeline():
    from story import pipeline
    return pipeline

api.register_endpoints()
serve(root=…, host=…, port=…, services={"story_context": story_context,
                                        "demo_config": demo_config,
                                        "story_pipeline": story_pipeline})
```

**Both are integration edits to `story/cli.py`, which the endpoint workstream does not own.**
Until they land, `python -m story ui` serves the static shell and `/demo-ui/health` only, and
`POST /demo/generate` would answer `503 pipeline_unavailable` — which is the state
`test_the_pipeline_is_refused_cleanly_until_the_composition_root_supplies_it` pins.

### What the endpoints actually return

| Endpoint | Body |
| --- | --- |
| `GET /demo/graph/overview` | `projection.build_projection` payload + `available_projections`, `available_views`, `honest_labels`. `?projection=` selects one of three names; anything else is `400 unknown_projection` **before** the executor is called |
| `GET /demo/graph/subgraph` | the same payload shape with `view`, `candidate_id`, `metric_ids`, `anchor_period_keys`. The bound parameters are the *candidate's*, resolved from a discovery run this process performed |
| `POST /demo/story-suggestions` | `202` + `run_id`, `events_url`, `result_url`, `filters` |
| `GET /demo/story-suggestions/{run_id}` | `run`, `ready`, `result` (= `DiscoveryResult.as_dict()`), `error`, `failure` |
| `GET /demo/candidates/{candidate_id}` | `suggestion` (with `score.suppressed`), `candidate`, `warnings` explained, `package_built`, `facts` |
| `POST /demo/evidence-package` | identity, digest, `rebuilt`, `previous_digest`, `digest_stable`, budget, counts, facts, passages, documents, freshness |
| `GET /demo/prompt-presets` | `prompt_presets.presets_payload(baseline_length_target=config.length_target)`, unchanged |
| `POST /demo/generate` | `202` + `run_id`, three URLs, `prompt` = `ComposedPrompts.as_dict()`, `style_delivery`. `409 edited_prompt_requires_live` carries the same `prompt` block beside the error |
| `GET /demo/runs/{run_id}` | `run`, `ready`, `error`, `outcome` — disposition, `rendered_as`, artifacts, manifest, plan, draft, verification, `post` **or** `rejection`, `cost`, `trace_events` |
| `GET /demo/runs/{run_id}/sources` | documents → passages → citations (with the quote sliced from the passage by the citation's own span) and facts, plus `unresolved` |
| both `/events` | SSE: `event: trace` frames carrying `TraceEvent.model_dump(mode="json")`, then exactly one `event: end` carrying `Run.summary()` |

**Three honesty fields that are not decoration.** `cost.measured` is `false` on a replay with
`prompt_tokens`/`completion_tokens`/`total_tokens`/`latency_ms` as `null` and a reason — the
recorded store stores none of them, and a zero would be a fabricated measurement in an
investor-facing panel. `package.retrieval_timing.measured` is `false` for the same reason
one level down. `manifest.provider_model_id` is reduced to the model *filename*: the local
runtime reports an absolute `.gguf` path that names the operator's home directory, and a test
caught it reaching a response body.

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

## 9b. Wave 4 repairs — the backend half *(2026-08-05, each reproduced before it was fixed)*

Wave 4's adversarial review found seven things the earlier waves got wrong. All seven were
measured against the running server on `graph-v1-0483dc6b4b10`; none was predicted from reading.
`story/pipeline.py` and the accepted stages were not touched.

| # | What was wrong | What it is now |
| --- | --- | --- |
| F1 | `POST /demo/evidence-package` refused **19 of the 20** rows `POST /demo/story-suggestions` had just served, with `candidate_not_reproducible` — *"the detectors did not reproduce that candidate from this graph run"*. **The message was false**: the cause was `resolve_demo_inputs` re-deriving with §6.6's D4 alone (§8b's narrowing), not the candidate | `story/demo_ui/candidate_resolution.py` re-derives through **all four** detectors and packages with the same `BoundedEvidencePackageBuilder`. All four families package (measured below). `candidate_not_reproducible` can now only be raised after four detectors ran, and carries which ran and how many candidates they minted; `candidate_not_packageable` is a separate refusal for a candidate that *was* re-derived |
| F2 | A third path leak, on two paths: `_scrub_paths` never reached `DiscoveryResult.as_dict()` (four `freshness.checks[].detail` strings carried the operator's home directory on the **success** path), and `_ABSOLUTE_PATH` structurally cannot match `//localhost`, so `"bolt://localhost:7687 database=neo4j"` survived on both endpoints | The discovery result is scrubbed whole; `_URI_AUTHORITY` reduces any URI to its scheme (`bolt://<host> database=neo4j`). Swept: every endpoint, success and error, is clean of `/mnt/`, `/home/`, `/Users/`, `bolt://`, `neo4j://`, `NEO4J_`, `api_key`, `127.0.0.1` |
| F3 | `graph_highlights.node_ids` carried period keys for 8 of 12 stages and `trace.py` composed `f"{len(node_ids)} nodes"`. Measured against the overview payload: comparability 0 of 24 resolved, grouping 0 of 24 | `GraphHighlights.period_keys` is its own field, counted as *"N period keys"*. Period keys resolve **24 of 24** in the coverage projection as `period:<key>`; node ids resolve fully for every non-detector stage. The detector stages' residual misses are `obs:` ids outside the bounded 20-per-metric overview sample — real nodes, disclosed bound |
| F4 | `discovery.TRACE_STAGES` mapped `metric_history → ranking`, so the stream emitted `ranking` twice with different numbers | `metric_history` is a stage of `trace.py`'s closed discovery set, ordered before `grouping` and `ranking`. No two discovery stages share a trace stage, and a test asserts it |
| F10 | `drafting · running` (seq 5) preceded `planning · passed` (seq 6) | `planning · passed` is emitted where it is observed — the moment the writer call opens, which is what makes that call reachable. It closes exactly once |
| F11/F12 | Three rows rendered two populations as one number (`processed 5 · refused 85`); `ranking`'s `refused` was a hardcoded `0` | `TraceCounts` carries a unit per count from a closed `COUNT_UNITS`; `ranking` omits `refused` because `RankingResult` has none to read |
| F7/F8/F16 | An accepted run with an unreadable `post.md` returned a silent `null`; `/sources` flattened the four §10 passage lists with no `role`; `discovery_run_id` was last-writer-wins | `post_error` + `artifacts_complete`; `role`, `role_description`, `is_counter_evidence`, `counts.by_role` (zeros kept) and `requested`; attribution is first-writer with `discovery_run_ids` beside it |

**What each detector family can do, measured 2026-08-05.** Package: all four, in ~3 s each
(one graph load shared by four detectors and the builder).

| Family | Package | Generate (replay) | Generate (live Qwen) |
| --- | --- | --- | --- |
| `metric_move` | 2 facts, 2 primary passages, 2 counter-evidence | `generation_not_recorded` — the committed store holds only D4's answers | reached the planner; §11 refused a truncated JSON plan at 2048 tokens |
| `trend_reversal` | 4 facts, 4 primary passages | `generation_not_recorded` | reached the writer; §12 refused — the writer prompt is 8,599 tokens against an 8,192 context |
| `acceleration` | 4 facts, 4 primary passages | `generation_not_recorded` | reached the writer; §12 refused, same context limit |
| `cross_metric_divergence` | 5 facts, 3 primary passages | **accepted**, `post.md` rendered, verifier passed | reached the writer; §12 refused |

Two honest consequences, neither hidden: the recorded store holds one candidate's answers, so
the other three families need `--live`; and the local 8,192-token runtime cannot hold the writer
prompt for the larger packages, so a *live* post is not produced for them today. That is a
runtime bound, not a resolver bound — the package, the plan and the model call all happen.

**The D4 package built through the new resolver is byte-identical** to the one
`resolve_demo_inputs` built on 2026-08-04 (`package_content_digest b01a8d573f08…`), and
`detector_versions` carries the minting detector alone so `story_run_id` matches the CLI's for
the same candidate. A `neo4j`-marked test asserts both.

## 10. Out of scope

Publishing. Authentication. Multi-user state. Writing anything to Neo4j or to an authoritative
catalog. Model-assisted verification (S10 remains deferred).
