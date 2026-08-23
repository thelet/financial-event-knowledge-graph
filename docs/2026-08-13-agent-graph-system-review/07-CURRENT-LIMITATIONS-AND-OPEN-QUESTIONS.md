# 07 — Current Limitations and Open Questions

**Audit date:** 2026-08-13. **Code baseline:** commit `33b0d7f`.

Every limitation below is **demonstrated by the current implementation** — by a measured count, a
run that was executed, or a line of code read. Nothing here is a design opinion, and nothing here
proposes a solution. Solutions are a later decision.

Format, per the brief:

```text
Current behavior:
Evidence:
Why it matters:
```

---

## A. The corpus and the graph

### A1. The graph's model-derived content is 24 rows

**Current behavior.** The extraction run that built the loaded graph made **zero provider calls**.
The narrative and event lanes ran in replay-only mode against 15 cached answers, and 14 requests
were actually issued.

**Evidence.** `data/extraction_runs/extract-v1-lexical-833f7bcfbce9/manifest.json` records
`provider.mode: "replay_only"`, `bounds.provider_calls_permitted: 0`,
`recorded_answers_available: 15`. Measured outcome: 11,848 candidates evaluated, **14 requests
issued**, 10,852 `NO_STORED_ANSWER` issues (91.6% of candidates). Of 2,704 observations, **2,690
(99.5%) come from the deterministic table lane** and 14 from the narrative lane; the event lane
produced 6 events and 4 relationship claims.

**Why it matters.** Any statement about the narrative or event lanes' corpus-scale behaviour rests
on 15 recorded answers. The graph is, in practice, a deterministic table-reading of the corpus with
a 24-row model-assisted fringe.

### A2. 59% of the graph is recorded silence, and most of it is a budget artifact

**Current behavior.** 17,130 of 28,836 nodes are `:Issue`. Of those, 10,852 carry `:NotAttempted`
(`code = NO_STORED_ANSWER`).

**Evidence.** Live label counts, 2026-08-13. `:NotAttempted` is 63% of all `:Issue` nodes and 38%
of all nodes in the graph. `graph/stages/projection/nodes.py::NOT_ATTEMPTED_CODE` sets the label
precisely so this class can be excluded.

**Why it matters.** Any coverage analysis that counts `:Issue` without filtering `:NotAttempted` is
measuring the run's provider budget, not the corpus. The label exists because the authors expected
exactly that mistake.

### A3. Nine of 26 metrics are empty, including `revenue`

**Current behavior.** 17 metrics carry observations. `acquisition_contracts`,
`home_price_appreciation` and `mortgage_rate` are in scope and empty; `cost_of_revenue`,
`gaap_gross_profit`, `homes_under_resale_contract`, `inventory_balance`,
`inventory_valuation_adjustment` and **`revenue`** are `in_scope_v1: false`.

**Evidence.** `MATCH (m:Metric) OPTIONAL MATCH (m)-[:HAS_OBSERVATION]->(o) RETURN m.metric_id,
count(o)`, run 2026-08-13. The six out-of-scope metrics are those whose first source-lane
preference is `xbrl`, and no XBRL lane exists. `metrics.yaml` records the finding behind it:
*"across 336 custom `open:` XBRL elements in every 10-K and 10-Q FY2020-Q1 2026, there is NO tag
for any operating KPI or non-GAAP measure."*

**Why it matters.** `revenue` is the most obvious metric a reader would query for, and the
verifier has a dedicated refusal code, `unpopulated_metric`, motivated by the measured trap that
`revenue` carries 170 `CONCERNS_METRIC` edges — *"170 places revenue was refused."* Two metrics
(`home_price_appreciation`, `mortgage_rate`) are fully orphaned nodes with no edge at all.

### A4. Four relationship claims, three of them the same predicate

**Current behavior.** The graph holds exactly four relationship-claim edges: one `BORROWS_UNDER`
and three `HOLDS_POSITION_AT`, all three of the latter minted from one passage.

**Evidence.** Live query returning all four rows, with `relationship_instance_id`, `claim_id` and
`passage_id`. The ontology declares **30** relationship predicates; the projection emits **12**, of
which only 2 are ever populated by a claim.

**Why it matters.** `relationships[]` in every evidence package is structurally empty and disclosed
as `relationships_unavailable_in_v1`. No retrieval tool returns a relationship at all.

### A5. Six events, three of them undated, one flagged

**Current behavior.** The graph holds 6 `:Event` nodes: one `credit_facility_established`, three
`executive_change` (all `occurred_on: null`), two `workforce_reduction` (one carrying
`review_flag: ANNOUNCEMENT_EQUALS_OCCURRENCE`).

**Evidence.** Live query returning all six with participants.

**Why it matters.** The writer's operation grammar excludes `temporal_order` **specifically
because** all three `executive_change` events carry `occurred_on: null` — a documented consequence
of this data, not a general design choice.

### A6. `:EvidenceSource` exists as a contract with zero rows

**Current behavior.** The base label and its uniqueness constraint exist; no node carries it. All
2,714 evidence rows are `normalized_passage` or `normalized_table`.

**Evidence.** `SHOW CONSTRAINTS` lists `evidencesource_key`; `MATCH (n:EvidenceSource) RETURN
count(n)` → 0. Five of the seven discriminated evidence shapes (`xbrl_fact`, `filing_metadata`,
`external_page`, `market_data`, `calculated`) have no rows.

**Why it matters.** Citation Rule C is refused outright (`evidence_kind_not_supported_in_v1`), and
`get_price_reaction` answers `Unavailable` with the reason *"the graph holds no
`:EvidenceSource:MarketData` node."*

### A7. Character offsets stop at the normalization catalog

**Current behavior.** `passages.jsonl` carries `char_start`, `char_end` and `fragment_sha256` per
locator. The extraction catalog writer drops them, so the graph's `EVIDENCED_BY` locates a passage
and a table block but **not a character range**.

**Evidence.** `graph/core/inputs.py:31-36` records the drop explicitly; the `EVIDENCED_BY`
properties read live are `quoted_text`, `table_id`, `block_ids`, `source_url` — no offsets.
Upstream, `SourceLocator`'s docstring records that offsets into the *source file* are absent
because *"sec-parser cannot supply them reliably."*

**Why it matters.** Citation checking has to work from `quoted_text` and grid coordinates rather
than from a span, which is the root of limitation C3.

### A8. Determinism holds only at a fixed git commit, and the run id does not cover the commit

**Current behavior.** Re-projecting the same extraction run today produces the **same
`graph_run_id`** and **byte-different artifacts**.

**Evidence.** Executed in this audit into a scratch directory: identical counts and warning
reconciliation, identical id `graph-v1-0483dc6b4b10`, and `nodes.jsonl` sha256 `c07d293c7480…`
against the committed `3a6b8ccb4f3c…`. All 28,836 nodes and 35,600 edges differ by exactly one
property, `graph_code_commit`. `python -m graph verify` against the re-projection then fails two
checks: `node_property_values_match_export` (missing 28,836, unexpected 28,836) and
`relationship_property_values_match_export` (35,600 / 35,600).

**Why it matters.** `make_graph_run_id` digests `(projection_version, extraction_run_id,
ontology_definition_hash, input_content_digest)` and **not** the code commit, while
`GraphRunWriter.finalize` removes and replaces its destination on the recorded justification
*"same id means same inputs and the same code, so the two are byte-identical."* Running
`python -m graph project extract-v1-lexical-833f7bcfbce9` today would silently overwrite the loaded
run's matching export. **The identical defect exists in
`extraction/core/run_directory.py::make_run_id` and has already fired**: the id
`extract-v1-lexical-2422c4252c07` names 2,707 observations in
`plans/graph/STAGE13_GRAPH_INPUT_HANDOFF.md` and holds 2,704 on disk.

---

## B. Detection, ranking and evidence

### B1. Four detectors, and 87% of candidates come from one of them

**Current behavior.** `metric_move` produces 227 of 262 candidates; `cross_metric_divergence` 15,
`trend_reversal` 11, `acceleration` 9.

**Evidence.** A full discovery run over `graph-v1-0483dc6b4b10`, executed 2026-08-13.

**Why it matters.** The story space the system can currently see is, in practice, "a metric moved".
There is no event detector, so all 262 candidates carry empty `event_ids`, and the event half of
the deduplication rule is unimplemented as a direct consequence.

### B2. Detector thresholds are module constants, not configuration

**Current behavior.** `config/story.yaml` has four keys: `version`, `provider`, `generation`,
`demo`. There is no `detectors:`, no `thresholds:`, no `weights:`.

**Evidence.** Neither `story/stages/detection/` nor `story/stages/ranking/` imports `yaml`, `json`,
`open(`, `Path` or `os`. Every threshold is a literal in `detector_config.py`; every weight is a
literal in `scoring.py`. The reason is recorded: *"a detector that could not run without a config
file on disk could not be unit-tested from a clean checkout."*

**Why it matters.** Retuning a threshold is a code change and a `DETECTOR_VERSION` bump, which
re-keys every candidate id. That is the intended behaviour, but it means threshold exploration is
not a configuration exercise.

### B3. The CLI demo bypasses ranking entirely

**Current behavior.** `python -m story demo` requires `--candidate-id` with no default, runs **D4
only**, and records `selection_mode: "manual_demo_candidate"`.

**Evidence.** `story/pipeline.py::resolve_demo_inputs` calls `detect_cross_metric_divergence` and
nothing else; `SELECTION_MODE = "manual_demo_candidate"` at `pipeline.py:115`. Ranking is exercised
only by the UI's discovery endpoint.

**Why it matters.** The scoring model (`RANKING_POLICY_VERSION 1.1.0`, eight weighted terms) has
never selected a story that was then generated through the CLI path. It is a live capability with
one consumer.

### B4. One ranking term is permanently zero

**Current behavior.** The `repetition` term carries a weight of −0.25 and is unreachable.

**Evidence.** `scoring.py` — `AcceptedPost` has no store and `accepted_history` defaults to `()` at
every call site.

**Why it matters.** The score as computed today is a 7-term score, not the 8-term score its
constants describe.

### B5. The token budget is a target, not a bound, once protected sections are involved

**Current behavior.** The trim loop pursues `max_total_tokens = 5000` but stops when only protected
sections remain; a package can be recorded above its own budget.

**Evidence.** `data/story_demo/story-v1-28033af11f4b/evidence_package.json` records
`prompt_token_estimate 5178` against `parameters.max_total_tokens 5000`, having already dropped 4
context passages and 4 diagnostic passages. `PROTECTED_SECTIONS` covers `facts`, `identity_facts`,
`semantic_facts`, `comparability_facts`.

**Why it matters.** The refusal (`package_exceeds_token_ceiling`) is against
`MAX_TOTAL_TOKENS_CEILING = 6000`, not against `max_total_tokens`. The gap between 5,000 and 6,000
is a zone where the budget is exceeded and nothing refuses.

### B6. The evidence package's subject has no graph provenance

**Current behavior.** `PackagedSubject.entity_text` and `labels` come from the caller; `resolved` is
inferred from the observations' `subject_entity_id`. Disclosed as
`subject_identity_not_read_from_graph` on every package.

**Evidence.** The warning appears in all four recorded packages. The reason is real and recorded:
`opendoor` carries 2,704 `OBSERVATION_OF_SUBJECT` edges, so `:Entity` is not an allowlisted label
in the retrieval layer at all.

**Why it matters.** The post's subject name is the one identity in the finished artifact that was
not read from the graph.

### B7. The model can influence one section of the evidence, and the claim was corrected in code

**Current behavior.** `explanatory_passages` are ranked by `search_passages`, whose
`ORDER BY score DESC LIMIT 25` means additional terms re-rank and evict.

**Evidence.** `planner.py`'s docstring: *"adding three innocuous terms to a two-term query dropped
18 of the 25 passages the original query returned"* (measured 2026-08-03). The mitigation is that
terms are derived by `query_terms.derive_terms` from the candidate and the ontology, and
`refuse_undeclared_terms` is asserted against the argument actually passed.

**Why it matters.** The stronger claim *"the model cannot influence the evidence set at all"* is
**false**, and the repository says so. In the demo candidate `want_explanatory_search=False`, so
`search_passages` is not called — but the surface exists.

---

## C. Generation and verification

### C1. There is no runtime prompt-versus-context guard

**Current behavior.** `context_tokens: 8192` is validated only against `max_output_tokens`. Nothing
measures a rendered prompt before sending it.

**Evidence.** `story/providers/public.py::StoryProviderConfig.validated` contains the only use of
`context_tokens`. `openai_compatible.py::request_body` posts `max_tokens` as configured and does
not measure the prompt. `section_bounds.estimate_tokens` is used only to bound the *package*. The
only assertion that a request fits is in **live tests, after the fact**. The extraction layer has
an equivalent guard (`narrative_lane.py::output_budget`, `PROMPT_EXCEEDS_CONTEXT`); the story layer
does not.

**Why it matters.** The historical failure (a ~9.4k writer request against an 8,192 context) is
mitigated **indirectly**, by the package budget. Measured today, the writer request is 3,858–4,376
estimated tokens plus 2,048 output — it fits, with roughly 1,300 tokens of margin on the larger
package. But that package carries **2** primary passages and `max_primary_passages` is **4**; at a
median backing passage of 2,144.5 characters, four whole passages would add roughly 1,075 tokens
and take the request past the margin. **No code would refuse it; it would be issued and
truncated.**

Compounding it: the safety argument at `section_bounds.py:108-111` was written against
`max_output_tokens: 1024`, and the configured value is now **2048**, halving the headroom that
comment reasoned from.

### C2. The token estimator runs about 9% low

**Current behavior.** `CHARS_PER_TOKEN = 4`, and the budget arithmetic uses it.

**Evidence.** On the one live run, the planner request reported `prompt_tokens: 3050` against a
rebuild estimate of 2,782. The module names it an estimate and says why no tokeniser ships
(`tiktoken` is on the forbidden-import list; a GGUF tokeniser would make the budget depend on which
model file is loaded).

**Why it matters.** The headroom argument in C1 does not carry the error bar.

### C3. The table-cell citation contract is unsatisfiable for 19.3% of the evidence

**Current behavior.** `writer.py` refuses a quote it finds more than once in a passage, while the
writer prompt instructs the model to quote exactly that text.

**Evidence.** Measured against the live graph: of 2,704 `EVIDENCED_BY` edges, **523 (19.3%)** have
a `quoted_text` that occurs more than once in its own passage; the worst case occurs 32 times.
`quoted_text` has a **median length of 4 characters**. Affected metrics include both demo
candidates (`adjusted_gross_margin` 34/180, `adjusted_gross_profit` 24/172, `market_count`
137/176). `plans/llm-agent/TABLE_CELL_CITATIONS.md` states it: *"The instruction and the gate
contradict each other; no model output satisfies both."*

`story/core/table_cells.py` exists at commit `33b0d7f` and **is not yet imported by any
verification module** — the repair is in flight, not landed.

**Why it matters.** For roughly a fifth of the graph's facts, no draft can currently satisfy both
the prompt and the gate.

### C4. Reading a period header at the value's own column is wrong 79% of the time

**Current behavior.** A table fact's period header sits at `period_header_column_index`, which
differs from `value_column_index` on most rows.

**Evidence.** Over all 2,690 table-backed observations:
`cells(lines[period_header_row_index])[period_header_column_index] == column_label` holds 2,690 /
2,690, while the same lookup at `value_column_index` holds only **565 / 2,690**.

**Why it matters.** The failure is **silent** — a `$` sign or an empty spacer cell is a perfectly
well-formed cell, so a wrong reading returns a plausible string rather than an error. This is why
`graph/core/derivation.py:39-44` names `period_header_column_index` and not `value_column_index` as
the id-bearing coordinate.

### C5. Two causation conditions could not be implemented, and the closure refuses real sentences

**Current behavior.** A span carrying more than one causal marker is refused outright
(`causal_marker_ambiguous_in_span`).

**Evidence.** `claims.py`'s docstring: `DraftSentence`, `FactBinding` and `Calculation` carry no
cause-term, effect-term or marker-occurrence selector, so the plan's conditions 3 and 6 cannot be
expressed. **367 passages carry two or more causal markers.**

**Why it matters.** The check refuses true sentences and *"never accepts a false one, which is the
direction §13.14 says the failure should point"* — a stated over-strictness, not a bug, but it
bounds what can be written about 367 passages.

### C6. `paraphrase_distance` is a WARN nothing adjudicates

**Current behavior.** The citation Rule B lexical-grounding check emits a WARN that escalates to a
stage which does not exist.

**Evidence.** `citations.py::_rule_b` — the WARN is *"carried into the accepted artifact and
acknowledged there rather than adjudicated."*

**Why it matters.** An accepted post can carry an unadjudicated grounding warning. Combined with
C7, there is no second opinion of any kind.

### C7. There is no model verifier at all, and two holes have no backstop

**Current behavior.** The advisory model verifier was dropped entirely.

**Evidence.** `story/pipeline.py:23` — *"§13's deterministic layer is the only authority here and
§8b dropped S10's advisory verifier entirely."* Corroborated in
`plans/llm-agent/IMPLEMENTATION_STEPS.md:900,923`. Causation has **no WARN tier** among its six
codes.

**Why it matters.** This is the intended design and the deterministic gate is strong (85 codes, 79
blocking, severity unreachable from any call site). But two named holes —
`paraphrase_distance` and `temporal_association_turned_causal` — were designed to escalate to a
model layer that no longer exists.

### C8. No `VERIFIER_VERSION` exists

**Current behavior.** A digest over the 85-entry gate table stands in; the manifest writes
`"verifier_version": null`.

**Evidence.** `story/pipeline.py::verifier_gate_digest`, and `demo.verifier_version` in every
recorded `demo_manifest.json`.

**Why it matters.** A change to verifier *logic* that leaves the gate table untouched moves nothing
in `story_run_id`. The code names this as a gap rather than pretending otherwise.

### C9. Remedies are reported and nothing consumes them

**Current behavior.** Every blocking finding carries a `Remedy` (`DROP_SENTENCE`,
`REBIND_TO_FACT`, `NARROW_METRIC_SURFACE`, …). Nothing reads them.

**Evidence.** `story/pipeline.py::run_demo` has one decision line and no rewrite path.
`codes.py`'s docstring also records that the `Remedy` enum cannot express two remedies the checks
need, and that one substitute *"reads slightly wrong."*

**Why it matters.** There is no repair loop of any kind. A rejected draft is final for that run.

### C10. Nothing refuses a fact binding on a calculated sentence

**Current behavior.** A `calculated` sentence carrying a `fact_binding` is not refused.

**Evidence.** `deterministic.py::GROUNDED_SENTENCE_KINDS` — named in the code as *"a separate hole,
recorded rather than closed here."*

**Why it matters.** It is a documented gap in the gate's closure, and it is the only one the code
names as unclosed rather than deliberately over-strict.

---

## D. Operations and tooling

### D1. The story CLI has two verbs

**Current behavior.** `python -m story demo` and `python -m story ui`, and nothing else.

**Evidence.** `story/cli.py`'s docstring names what was traded away: `discover`, `package`, `plan`,
`draft`, `verify`, `runs`, `report`, `rebuild`, `doctor`, `ask`, `issues`, `rejected`, `inspect`,
`candidate`, `recheck` — *"A subcommand that parsed and then said 'not implemented' would be a
promise."*

**Why it matters.** To run a single stage today a developer uses the UI's HTTP endpoints or the
Python API directly. There is no `story runs`, so the recorded runs under `data/story_demo/` are
inspectable only by reading files.

### D2. The story run directory constant does not match where runs are written

**Current behavior.** `story/context.py:49` defines `STORY_RUNS_DIRNAME = "data/story_runs"`, and
`story/core/{keys,manifest,models}.py` describe run artifacts as living there. No such directory
exists; every run is under `data/story_demo/`.

**Evidence.** `ls data/` — no `story_runs`. `config/story.yaml`'s `demo.out_root` is
`data/story_demo`, and `story/demo_ui/runs.py:11` reads there.

**Why it matters.** Four docstrings point a reader at a path that does not exist.

### D3. Six modules' docstrings assert that `config/story.yaml` does not exist

**Current behavior.** `story/context.py:15`, `story/providers/public.py:29`,
`story/providers/neo4j_connection.py:73`, `story/stages/ranking/scoring.py:21`,
`story/stages/freshness/loaded_graph.py:57` and `story/demo_ui/projection.py:90` all say the file
is deferred.

**Evidence.** The file exists and `story/pipeline.py::DemoConfig.load` reads it
(`CONFIG_FILENAME = "story.yaml"`).

**Why it matters.** The substantive claim each protects — that no threshold or weight is read from
it — remains true, but a reader checking the claim finds the premise false.

### D4. The README describes a much earlier system

**Current behavior.** `README.md` states *"Claim extraction is planned and not yet built"* and
marks extraction **"planned only"**.

**Evidence.** `extraction/` is 66 files / 13,547 lines with a finished run on disk; `graph/`,
`story/` and a UI are built on top of it. The README's ontology `definition_hash e8d4af709be2…` is
also stale — every current manifest carries `bb94f522ba12…`.

**Why it matters.** The repository's front door describes three layers where there are six.

### D5. Every measured number in the graph and extraction plans is a few off

**Current behavior.** The plans describe run `extract-v1-lexical-2422c4252c07` with 2,707
observations, 2,717 claims, 186 warnings, 17,127 issues, 46 rejections, 35,603 edges.

**Evidence.** The live system runs `extract-v1-lexical-833f7bcfbce9` with **2,704 / 2,714 / 185 /
17,130 / 49 / 35,600**. Separately, `plans/extraction/STAGE_13_INTEGRATED_RUN.md` records
`conflicting_duplicates FAIL — 4` and *"the run still exits non-zero, and it should"*; on disk all
five checks pass and the run exits 0, and 6 of the 8 recorded catalog digests differ.

**Why it matters.** A reader checking any figure in the plans against the artifacts will find a
mismatch, and the plans give no indication that the run was re-made.

### D6. The ontology's own description carries a stale passage count that cannot be corrected cheaply

**Current behavior.** `ontology.yaml` states "294 documents, **14,203 passages**"; the real count is
12,442 (the pre-encoding-fix number).

**Evidence.** The string sits inside `OntologyMetadata.description`, which **is part of the hashed
snapshot**. Correcting it moves `definition_hash` and invalidates every stamped artifact — the
extraction run, the graph run, all 28,836 nodes and 35,600 relationships.

**Why it matters.** It is a documentation error with a data-migration cost.

### D7. Benchmark counts disagree with the benchmark, and the test cannot catch it

**Current behavior.** The benchmark README advertises 69 gold claims and 23 expected abstentions;
the YAML holds **68 and 25**.

**Evidence.** Measured from `benchmarks/extraction/v1/cases/*.yaml`. The README's own narrative
describes a correction that took claims 68 → 69 and abstentions 24 → 23, so the YAML matches
neither the before nor the after state.
`tests/extraction/test_benchmark_integrity.py::test_scope_matches_what_the_readme_advertises`
asserts **ranges** (`60 <= claims <= 120`) and, despite its name, never compares against the README.

**Why it matters.** The one benchmark in the repository has an unflagged annotation drift and a
guard that structurally cannot detect it. **Not changed as part of this audit.**

### D8. The benchmark reports were generated at a commit not in the current history

**Current behavior.** All six committed report pairs carry
`implementation_commit e3c4da07f446194c2e274b7f19d6e7d6025796c0`.

**Evidence.** That commit is not reachable from HEAD's recent history.

**Why it matters.** The reports describe a tree state that cannot be checked out to reproduce them,
despite the stated property *"regenerating at the same commit is byte-identical."*

### D9. There is no benchmark for the story agent or the verifier

**Current behavior.** `benchmarks/` contains `extraction/v1/` and nothing else.

**Evidence.** Directory listing. The verification suite is 118 hand-built adversarial drafts in
`tests/story/test_story_deterministic_verifier.py`, not a scored benchmark.

**Why it matters.** There is no measured precision/recall for the gate, and no way to score a
change to it beyond "the 118 tests still pass".

---

## E. The UI

### E1. Seven of eight discovery filters have no control

**Current behavior.** The server accepts and validates `subject_entity_id`, `metric_ids`,
`story_types`, `period_from`, `period_to`, `external_only`, `min_score` and `max_suggestions`. The
browser sends `{max_suggestions: 50}`.

**Evidence.** `discovery.py::DiscoveryFilters` and `api.py::_filters_from` against `app.js:1196`.
`gate: false` and `length_target` are likewise live server capabilities with no browser control.

**Why it matters.** "What the UI can do" and "what the server can do" are different sets.

### E2. There is no run history, so the recorded runs are invisible

**Current behavior.** `GET /demo/runs/{run_id}` returns the run just performed. Nothing lists or
reads the existing `data/story_demo/` directories.

**Evidence.** `DemoState` is process-local memory (`api.py:349-357`) and `RunRegistry` is in-memory
(`runs.py:8-12`).

**Why it matters.** The four recorded runs — including both accepted posts and both refusal modes —
are reachable only from the filesystem.

### E3. There is no free-text search, and the reason is a data fact

**Current behavior.** No search box exists.

**Evidence.** `discovery.py:426-434` states it: *"a candidate carries no prose at all (§6.4 keeps
editorial text off it), so there is nothing to search but ids"*, and *"a `StoryCandidate` names
observations, not documents"*, so filing and document filters are refused rather than offered as
controls that would silently do nothing.

**Why it matters.** It is a deliberate refusal, not an omission — but it means the only way to find
a story is to run discovery and read 50 rows.

### E4. There is no node expansion

**Current behavior.** Double-click / expand is not implemented. The only navigation into more graph
is the candidate-scoped subgraph and clicking a cluster disc.

**Evidence.** `app.js` §4.1 — no expand handler exists.

**Why it matters.** The graph canvas is three fixed projections plus two candidate-scoped views; it
is not explorable.

### E5. The overview drops nine metrics and synthesises the company

**Current behavior.** The overview shows 17 metric nodes of 26, and the company node is not read
from the graph.

**Evidence.** `OVERVIEW_STATEMENT`'s `MATCH` is not optional (`projection.py:196`);
`COVERAGE_STATEMENT` uses `OPTIONAL MATCH` specifically to keep the nine. The company node carries
`synthesised: true`, `read_from_graph: false`. On `coverage`, **every** edge is derived (75
synthesised nodes, 554 synthesised edges).

**Why it matters.** The default view is missing `revenue` and shows a company node that is a
Python-side construction. Both are flagged in the payload and drawn hollow-and-dashed, so the UI is
honest about it — but a reader who does not know the convention sees a graph with a company in it.

### E6. Two prompt-panel headings are one rule behind

**Current behavior.** `prompt_presets.py` says "seven planner rules / seventeen writer rules" in
nine places, and the panel headings read *"Planner rules 1-7"* and *"Writer rules 1-17"*.

**Evidence.** Measured live: **8 and 18**. `numbered_rules()` parses them from the constants, so the
payload is correct — but neither new rule has an entry in `_PLANNER_RULE_CODES` /
`_WRITER_RULE_CODES`, so **rule 18 renders with empty `refusal_codes` under a heading claiming
17**.

**Why it matters.** It is the one documentation drift in this repository with a user-visible
symptom.

### E7. The UI has its own Cypher layer, and it had to

**Current behavior.** Graph visualization uses `story/demo_ui/projection.py` (5 statements); only
discovery, candidate re-derivation and packaging use the shared retrieval layer.

**Evidence.** `projection.py:9-12` — the nine retrieval tools return flat rows of named scalars
only; `test_no_cypher_statement_returns_a_whole_node` forbids a whole node and `plain_value` refuses
a `neo4j.graph.Entity`, so **no retrieval tool can return an edge**, and a graph view cannot be
assembled through that surface.

**Why it matters.** Two Cypher layers exist over one graph. Both are inside `story/` and both are
scanned by the same test, which is the mitigation — but a schema change has two homes.

---

## F. Process

### F1. The audit was performed against a moving working tree

**Current behavior.** `git status` was clean at session start and dirty by 14:00, with a concurrent
session landing the table-cell-citation work.

**Evidence.** File mtimes advanced during the audit (`story/core/models.py` at 14:10:25,
`tests/story/test_story_contracts.py` at 14:10:36). Three identical offline runs returned **18 → 6
→ 3** failures over half an hour. `ListAgents` shows a second interactive session on this
repository, busy.

**Why it matters.** Every code reference in this documentation set is pinned to commit `33b0d7f`,
read from a `git archive` extraction. The working-tree test numbers in `03` §12.2 describe a
work-in-progress state, not the committed baseline.

---

## Open questions

These are places where the code does not provide enough evidence to determine intended behaviour.
Each is a question, not a recommendation.

1. **Is the extraction run's replay-only bound a deliberate operating point or an unfinished
   step?** `provider_calls_permitted: 0` with 15 recorded answers produced 10,852 `:NotAttempted`
   issues. Nothing in the code says whether the next run is expected to be unbounded, or whether
   the deterministic table lane is intended to remain the graph's substance.

2. **What is `data/story_runs/` supposed to be?** Four docstrings describe a production run
   lifecycle writing there. `config/story.yaml` writes to `data/story_demo/` and scopes itself to
   *"the §8b demo path and nothing else"*. Whether `data/story_runs/` is a deferred production path
   or an abandoned one is not stated.

3. **Should `graph_code_commit` be an input to `graph_run_id`, or should it stop being written onto
   rows?** The code contains the justification for `finalize()` replacing its destination and the
   property that falsifies it, but no decision either way.

4. **Is the `revenue` metric expected to stay out of scope?** It is `in_scope_v1: false` because
   its first source lane is `xbrl` and no XBRL lane exists, and the measurement behind that
   (no `open:` XBRL tag for any operating KPI) concerns *custom* elements. Whether standard
   `us-gaap:Revenues` is reachable is not addressed anywhere in the repository.

5. **What is the intended relationship between `PACKAGE_VERSION` and the recorded fixtures?** The
   version is a `package_id` digest input, so any bump re-keys every recorded run and every replay
   store entry. The re-record procedure exists (it was executed at S7) but the policy — when a bump
   is worth the re-record — is not written down.

6. **Is the demo UI intended to grow a run history, or is process-local state the design?**
   `runs.py`'s docstring describes the registry as in-memory without saying whether that is
   temporary.

7. **What happens to the 523 non-unique `quoted_text` cases once the table-cell work lands?** The
   in-flight change adds `evidence_handle` and `TableCellRef`, but at commit `33b0d7f` nothing in
   `story/stages/verification/` imports `table_cells.py`, so the verifier-side contract is not yet
   visible.

8. **Is `paraphrase_distance` meant to remain unadjudicated?** It was designed to escalate to a
   model layer that was then removed. Whether it should become blocking, be removed, or wait is not
   recorded.

9. **Why do the benchmark's gold counts differ from both the before and after states its own
   README narrates?** The most likely source is identified
   (`test_the_broader_market_figure_is_neither_gold_nor_a_scored_abstention`, which leaves a second
   figure unscored), but the intended count is not stated anywhere.

10. **Is the 8,192-token context a fixed constraint or a current one?** Several bounds
    (`max_primary_passages`, `max_total_tokens`, the ceilings) are sized against it, and the
    arithmetic in `section_bounds.py` was written against a `max_output_tokens` value that has since
    doubled. Whether the constraint or the model is expected to move is not addressed.
