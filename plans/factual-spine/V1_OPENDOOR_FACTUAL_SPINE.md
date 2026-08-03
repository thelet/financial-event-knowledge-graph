# V1 — Opendoor's factual spine

Completing the factual foundation so the graph can support insightful investor posts rather
than isolated filing facts.

**Status:** planned, not implemented. Written 2026-08-03 against extraction run
`extract-v1-lexical-2422c4252c07` and graph run `graph-v1-886059d862ce`.

| | |
| --- | --- |
| Predecessors | [V1_CLAIM_EXTRACTION](../extraction/V1_CLAIM_EXTRACTION.md) · [V1_GRAPH_PROTOTYPE](../graph/V1_GRAPH_PROTOTYPE.md) · [STAGE13_GRAPH_INPUT_HANDOFF](../graph/STAGE13_GRAPH_INPUT_HANDOFF.md) |
| Companion | [SOURCE_AND_CONTRACT_HANDOFF.md](SOURCE_AND_CONTRACT_HANDOFF.md) — source choices, credentials, licensing |
| Anchor | Opendoor Technologies Inc., CIK `0001801169`, Nasdaq OPEN |

---

## 0. The answer first

The graph is **not** mostly isolated filing facts. It holds 2,707 metric observations across 17
metrics with complete provenance to filed passages, and rebuilds byte-for-byte. What it lacks is
almost entirely **one thing repeated in five places**: *nothing has been asked of a language
model at corpus scale, and nothing outside the SEC narrative corpus has been ingested at all.*

Five findings reorder the work the brief assumed:

1. **The narrative and event lanes were never run.** 10,807 distinct model requests were
   scoped; **14 were issued**. 2,690 of 2,717 claims are deterministic table output. The "6
   events / 4 relationships" figure is a replay-coverage number produced by 3 answered passages,
   not an extraction-quality result. *(verified — §1.5)*
2. **Earnings releases and shareholder letters are already a classified source lane** — 45
   `earnings_release`, 26 `shareholder_letter`, 22 `supplemental`, all normalized, none dropped.
   The planned F4 stage is therefore mostly *precision repair*, not ingestion: only **22 of the
   45** `earnings_release` documents sit on a filing carrying SEC item 2.02. *(verified — §4)*
3. **SEC Companyfacts cannot supply two of the six deferred metrics, and the ontology's mapping
   for a third is wrong.** Companyfacts exposes `dei`, `us-gaap` and `ecd` only — **no `open:`
   extension concepts at all** — and `us-gaap:Revenues`, recorded in the ontology as
   `verified: true`, **does not exist** in Opendoor's facts. *(measured live against the SEC API,
   2026-08-03 — §2)*
4. **The evidence contract blocks every new source lane.** `validate_evidence_resolves` requires
   a `passage_id` on every evidence reference and emits `EVIDENCE_MISSING` otherwise — so an
   XBRL fact, a price bar, or a transcript utterance cannot enter the catalogs today, however
   well-formed. This single constraint gates F1, F6 and F8. *(verified — §1.6)*
5. **Guidance already exists in the ontology and its own rule is unsatisfiable.**
   `guidance_issuance` declares `low_value`/`high_value`, and its `inference_restrictions`
   demands `assertion_type` other than `reported` — but `AssertionType` has no member that
   means "guided". *(verified — §5)*

The recommended first stage is therefore **F0 (contract extension), not F1 (XBRL)** — because
three later lanes all fail at the same gate, and fixing it once is cheaper than discovering it
three times.

---

## 1. Current factual-spine inventory

All counts measured directly from committed artifacts on 2026-08-03 unless marked otherwise.

### 1.1 Corpus and documents — implemented

| | Count | Source |
| --- | --- | --- |
| Filings | 109 — 8-K 75, 10-Q 19, DEF 14A 7, 10-K 6, 8-K/A 2 | `data/catalog/filings.jsonl` |
| Date range | 2020-04-30 → 2026-06-12 | same |
| Artifacts | 2,019 (asset 1,060, xbrl 364, exhibit 268, primary 109, index_header 109, full_submission 109) | `data/catalog/artifacts.jsonl` |
| Normalized documents | 294 | `data/normalization_catalog/documents.jsonl` |
| Passages | 12,442 — 10,507 narrative, 1,935 table | `data/normalization_catalog/passages.jsonl` |
| Storage | raw 746 MB · normalized 105 MB · extraction 33 MB · graph 74 MB | `du -sh` |

Document types, all 294: `narrative_primary` 109 · `material_agreement` 64 ·
`earnings_release` 45 · `shareholder_letter` 26 · `supplemental` 22 · `governance` 14 ·
`other` 7 · `structured_exhibit` 7.

`config/fetch.yaml` scopes acquisition to four forms (10-K, 10-Q, 8-K, DEF 14A) from
2020-01-01. **No S-1 is in the corpus**; 8-K/A arrives as a side effect of the 8-K scope.

### 1.2 Metrics — 17 of 26 populated

| Metric | Observations | | Metric | Observations |
| --- | ---: | --- | --- | ---: |
| `contribution_margin` | 304 | | `homes_sold` | 124 |
| `gaap_gross_margin` | 304 | | `homes_purchased` | 102 |
| `adjusted_ebitda` | 296 | | `pct_homes_on_market_gt_120_days` | 89 |
| `adjusted_ebitda_margin` | 294 | | `contribution_profit_after_interest` | 45 |
| `contribution_profit` | 268 | | `holding_costs` | 18 |
| `direct_selling_costs` | 197 | | `homes_under_contract` | 9 |
| `adjusted_gross_margin` | 180 | | `borrowing_capacity` | 1 |
| `market_count` | 177 | | | |
| `adjusted_gross_profit` | 172 | | | |
| `housing_inventory_homes` | 127 | | | |

**Nine metrics are unpopulated, not six.** The brief's "six deferred" is correct as far as it
goes, but three more are empty for a different reason:

| Metric | Why empty | Category |
| --- | --- | --- |
| `revenue`, `gaap_gross_profit`, `cost_of_revenue`, `inventory_balance`, `inventory_valuation_adjustment`, `homes_under_resale_contract` | `source_lane_preferences[0] == "xbrl"`; the XBRL lane does not exist. 358 `DEFERRED_REQUIRED_SOURCE_LANE` refusals recorded. | **intentionally deferred** |
| `acquisition_contracts` | narrative-first, `forbidden_source_lanes: [xbrl]` | **blocked by the unrun narrative lane** |
| `mortgage_rate` | narrative-first, macroeconomic | same |
| `home_price_appreciation` | narrative-first, macroeconomic | same |

The deferral is enforced, not merely documented: the run's `forbidden_lanes` verification
asserts *"no xbrl-first metric carries a normalized-lane observation"* over 2,707 observations
with 0 failures. The six-metric set is derived at runtime by
`extraction/core/assembly.py:81 deferred_metric_ids(ontology)` and read by both the verifier and
`graph/stages/projection/nodes.py` — **shipping an XBRL lane un-defers all six with no code edit
in either place.**

### 1.3 Events and relationships — present but barely exercised

Declared: **19 event types**, **30 relationship predicates**. Produced: **6 events of 3 types**
(`executive_change` ×3, `workforce_reduction` ×2, `credit_facility_established` ×1) and
**4 relationships of 2 predicates** (`HOLDS_POSITION_AT` ×3, `BORROWS_UNDER` ×1), from
**exactly 3 passages in 3 documents**.

Two structural limits, both verified:

- **Only 10 of 30 predicates are reachable *by the event lane*.** The lane's relationship menu is
  derived from event types' `allowed_relationships`, and 20 predicates appear in no event type —
  including `SUBSIDIARY_OF` / `HAS_SUBSIDIARY`, which the ontology itself says are sourced from
  EX-21.1, a structured exhibit already in the corpus, plus `LISTED_ON`, `PARTY_TO`,
  `REGULATED_BY`, `COMPETES_WITH` and `SUPERSEDES`. Six of those 20 are nonetheless emitted by
  the graph projection directly (`EVIDENCED_BY`, `HAS_OBSERVATION`, `OBSERVATION_OF_SUBJECT`,
  `DISTINCT_FROM`, `PARTICIPATES_IN`, `RECONCILES_TO`, all `ontology_declared: true`), so the
  true tally is **16 of 30 reachable, 8 actually reached, 14 unreachable by any current path**.
- **`earnings_release` is structurally unemittable.** It requires
  `[occurred_on, period_start, period_end]`; `LaneEvent` has no `period_start`/`period_end`
  fields at all. The single most frequent disclosure act in the corpus is guaranteed to be
  refused with `EVENT_TEMPORAL_REQUIREMENT_UNMET` the moment a real run happens.

All four emitted relationships carry `valid_from: null` / `valid_to: null`, though 20 of 30
predicates declare `temporal: true`.

### 1.4 Graph — implemented and verified

28,836 nodes · 35,603 edges · 12 edge types. Nodes: `Issue` 17,127 · `Passage` 8,776 ·
`Observation` 2,707 · `Document` 185 · `Metric` 26 · `Entity` 9 · `Event` 6. Edges: `FOUND_IN`
17,127 · `PART_OF` 8,776 · `EVIDENCED_BY` 2,713 · `HAS_OBSERVATION` 2,707 ·
`OBSERVATION_OF_SUBJECT` 2,707 · `CONCERNS_METRIC` 1,520 · `DISTINCT_FROM` 36 ·
`PARTICIPATES_IN` 10 · `HOLDS_POSITION_AT` 3 · `RECONCILES_TO` 2 · `BORROWS_UNDER` 1 ·
`PLACEHOLDER_FOR` 1.

Working provenance walk, verified against `edges.jsonl`:

```
(:Metric)-[:HAS_OBSERVATION]->(:Observation)-[:EVIDENCED_BY]->(:Passage)-[:PART_OF]->(:Document)
(:Entity)-[:PARTICIPATES_IN]->(:Event)-[:EVIDENCED_BY]->(:Passage)-[:PART_OF]->(:Document)
```

`:Passage` nodes **do** carry full `text`, so an evidence panel can be rendered from the graph
alone. Ontology-declared edges are distinguished from projection scaffolding by a literal
`ontology_declared` boolean; exactly four types are projection-local
(`PART_OF`, `FOUND_IN`, `CONCERNS_METRIC`, `PLACEHOLDER_FOR`).

**Underused:** ontology *instance properties are dropped by projection*. `cik`, `tickers`,
`exchange` are declared on the `opendoor` instance and `mic: XNAS` on `nasdaq`, but
`graph/stages/projection/nodes.py` reads only `instance_id`, `concept_id` and `label`. The graph
cannot currently answer "what exchange does OPEN trade on". `nasdaq`, `sec_edgar` and
`opendoor_accountable` are degree-0 nodes.

### 1.5 Narrative inference coverage — the dominant gap

| | Measured |
| --- | ---: |
| Candidates scoped | 11,848 (tables 503, narrative 3,072, events 8,273) |
| Distinct model request digests | **10,807** |
| Requests issued | **14** |
| Recorded answers available | 15 (12 narrative, 3 event) |
| `NO_STORED_ANSWER` issues | 10,852 — 63.4% of all 17,127 issues |
| Claims from the model | **27** of 2,717 — narrative 17, events 10 (tables 2,690) |

`provider.mode: replay_only`, `provider_calls_permitted: 0`. `extraction/context.py` hardcodes
`inner=None`, so **no code path in `extraction/` can reach a live model**; the only live wiring
is in `benchmarks/` and `*_live.py` tests. There is no `--generate` flag on the CLI.

Event-lane candidates by document type: `narrative_primary` 6,919 · `earnings_release` 772 ·
`shareholder_letter` 582. By selection reason: `document_type_prior` **5,201** · `exact_alias`
2,048 · `ambiguous_alias` 545 · `heading_prior` 479. The 5,201 selected on document type alone
are 63% of the event lane and 48% of all remaining model work — the single largest cost lever.

Provider is local: llama.cpp, `Qwen3.5-9B-Q4_K_M.gguf`, measured ~67 tok/s. **There is no
dollar cost, only wall-clock and GPU time.** No latency or token counts are persisted anywhere —
deliberately, because they would break byte-identical rebuilds.

### 1.6 Contract limitations for investor-post generation

| Capability | State | Evidence |
| --- | --- | --- |
| Source-lane provenance | **implemented** — `source_lane` (ontology) *and* `lane` (routing), both carried, neither mapped onto the other | |
| Deterministic, readable IDs | **implemented** — `obs:{metric}:{subject}:{period}:{lane}:{digest12}`, fully recomputable (2,707/2,707) | |
| Evidence anchored to non-passage sources | **missing** — `validate_evidence_resolves` requires `passage_id`; `EvidenceReference` already *declares* `xbrl_concept`, `accession`, `source_url`, `disclosure_channel_id` but three validators refuse them | `extraction/core/validation.py:79-92` |
| Forecast vs actual | **missing** at claim level — `AssertionType` = `reported\|calculated\|classified\|inferred` | `ontology/core/values.py:79` |
| Value ranges | **missing** — `value: float\|int`, `extra="forbid"` on both models | |
| Fact equivalence / canonical-vs-alternate | **missing, deliberately** — `SUPERSEDES` declared, never emitted | `assembly.py:360` |
| Confidence | **declared, dead** — `null` on all 2,717 claims | |
| Market / price data | **absent entirely** | §7 |
| Guidance | **event type only, unsatisfiable rule** | §5 |
| Graph read/query API | **absent** — `GraphStore` is write-only (`wipe`, `ensure_constraints`, `write_nodes`, `write_edges`); `graph/queries/` does not exist | `graph/contracts.py:48-67` |
| Future-period guard | **absent** — no constraint compares a period against a filing date | `ontology/core/constraints.py` |

Test suite: **2,530 tests run with no external dependency** (`-m "not live and not neo4j"`);
2,567 collect under `-m "not live"`, of which 37 need a running Neo4j container; 2,627 total.
*The README's "717 tests" and "extraction: planned only" are stale — see §16.*

---

## 2. XBRL lane

### 2.1 What is actually available — measured, not assumed

Two independent probes on 2026-08-03:

**Repository:** 364 artifacts are classified `xbrl`, but they are **taxonomy linkbases only** —
97 `.xsd`, 97 `_lab.xml`, 97 `_pre.xml`, 43 `_def.xml`, 27 `_cal.xml`, and **3 instance
documents**, all pre-merger IPOB 10-Qs (`ipob-20200331.xml`, `ipob-20200630.xml`,
`ipob-20200930.xml`). Zero `*_htm.xml`, zero `R*.htm`, zero `Financial_Report.xlsx` — excluded
on purpose by `config/fetch.yaml: render_artifacts: false`. **There are no post-merger fact
values on disk.**

**SEC Companyfacts API** (`data.sec.gov/api/xbrl/companyfacts/CIK0001801169.json`, 1.30 MB):

| Taxonomy | Concepts |
| --- | ---: |
| `us-gaap` | 340 |
| `ecd` | 7 |
| `dei` | 2 |
| **`open` (company extension)** | **0 — absent entirely** |

Mapping the six deferred metrics against reality:

| Metric | Ontology mapping | Companyfacts result |
| --- | --- | --- |
| `revenue` | `us-gaap:Revenues`, `verified: true` | ❌ **concept does not exist.** Actual: `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`, 78 facts |
| `gaap_gross_profit` | `us-gaap:GrossProfit` | ✅ 78 facts, 10-K/10-Q |
| `cost_of_revenue` | `us-gaap:CostOfRevenue` | ✅ 70 facts |
| `inventory_balance` | `us-gaap:InventoryRealEstate` | ✅ 44 facts (instant) |
| `inventory_valuation_adjustment` | `open:InventoryRealEstate…DecreaseIncrease` (exact) + `us-gaap:InventoryAdjustments` (related) | ⚠️ extension absent; `us-gaap:InventoryAdjustments` present, 30 facts. Also `us-gaap:InventoryWriteDown`, 50 facts |
| `homes_under_resale_contract` | `open:InventoryRealEstateInResaleContract` (exact) | ❌ **absent from Companyfacts** |

**Correction to record.** `metrics.yaml` marks `us-gaap:Revenues` as `verified: true`. It is
wrong for this issuer. The check that would have caught it — resolving the label against the
issuer's own facts rather than against the US-GAAP taxonomy — was never run. This is exactly the
class of error `plans/ontology/` asks to be recorded rather than quietly fixed.

**Therefore: both sources are needed.** Companyfacts covers four metrics cleanly and cheaply
(one HTTP request, no per-filing parsing). The two extension-concept metrics require
filing-instance inline XBRL, which means either flipping `render_artifacts: true` to acquire
`*_htm.xml`, or parsing inline XBRL out of primary `.htm` documents already on disk.

### 2.2 Fact shape, and the hazards inside it

A Companyfacts fact carries `start`, `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`, `frame`.
Instants omit `start` entirely — the duration/instant distinction is structural and maps cleanly
onto `PeriodRef` (`period_start`/`period_end` vs `instant_date`), which already models exactly
this.

Measured on `RevenueFromContractWithCustomerExcludingAssessedTax`: **44 distinct `(start, end)`
periods, of which 30 are reported more than once.** FY2020 appears three times:

| `val` | form | `fy` | `accn` | `filed` |
| ---: | --- | ---: | --- | --- |
| 2,583,121,000 | 10-K | 2020 | 0001801169-21-000011 | 2021-03-04 |
| 2,583,000,000 | 10-K | 2021 | 0001801169-22-000027 | 2022-02-24 (comparative) |
| 2,583,000,000 | 10-K | 2022 | 0001801169-23-000024 | 2023-02-23 (comparative) |

This is the restatement-versus-rounding problem in its purest form, and it arrives on day one of
the XBRL lane. **Do not deduplicate by `(concept, period)` and keep the newest.** The selection
rule must be explicit and recorded: *prefer the fact whose `accn` is the original filing for that
period* (`fy`/`fp` matching the period), *and emit later restatements as separate observations*.
Companyfacts' `frame` field is only populated on a subset and is a CY-normalized label, not a
fiscal one — use it as a cross-check, never as the period.

Amended filings (`10-K/A`, `10-Q/A`) appear in `form`; Opendoor's corpus has 2 `8-K/A` and no
amended periodic reports, but the lane must not assume that.

Dimensions: Companyfacts is **dimensionally flattened** — it returns consolidated totals only.
Any segment-level figure requires filing-instance XBRL. For the six metrics this is acceptable;
record it as a known bound rather than discovering it later.

Units and scale: Companyfacts values are **already in base units** (`USD`, `shares`), unlike the
table lane where scale is declared in a header and applied by the lane. An XBRL observation
therefore carries `scale: null` legitimately — and this collides directly with the existing
`scale`-bearing table observations (§8).

### 2.3 The lane, end to end

```
Companyfacts JSON  ─┐
                    ├─► cache (content-addressed, atomic)  ─► fact selection ─► concept mapping
filing-instance XBRL┘        + fetched_at + source_url         (per metric)     (ontology-driven)
   ─► context/period normalization ─► unit & scale ─► observation assembly ─► validation
   ─► Stage-13 catalogs ─► graph
```

Every stage above is an existing shape. The **only** genuinely new machinery is a fact reader
and a concept→metric resolver; everything downstream is reuse. Concept mapping must read
`external_mappings` from the ontology (`mapping_system` ∈ {`standard_us_gaap`,
`opendoor_extension`}, `mapping_type == "exact"`) rather than hard-coding six concept names —
matching how `deferred_metric_ids` is already derived.

### 2.4 Reconciliation with table observations — keep both

An XBRL revenue fact and a table-derived revenue reading are **two readings of one fact**, not a
duplicate to collapse.

**The `observation_id` scheme does not keep XBRL facts distinct — it collapses them, and this
must be fixed before F2 emits anything.** *(verified 2026-08-03)* The id is
`obs:{metric}:{subject}:{period}:{lane}:{digest12}` where the digest covers only `passage_id` and
`structural_position`. An XBRL fact has neither, and `digest()` over an empty argument list is
the constant `e3b0c44298fc`:

```
obs:revenue:opendoor:FY2020:xbrl:e3b0c44298fc    # original 10-K, 2,583,121,000
obs:revenue:opendoor:FY2020:xbrl:e3b0c44298fc    # FY2021 10-K comparative, 2,583,000,000
```

Every XBRL fact for one `(metric, subject, period)` mints the **same id**. The FY2020 revenue
triple that §2.2 requires as three observations would become one id three times — tripping
`duplicate_identities` or silently overwriting, which is precisely the "silently discarding
whichever arrived second" failure the identifier module was written to prevent.

**Required fix, at F1/F2:** the XBRL lane must supply a `structural_position` that carries the
fact's filing identity — `accn`, plus `fy`/`fp` — so that three readings of FY2020 revenue are
three ids. This is the XBRL analogue of the grid coordinates the table lane supplies, and it uses
the existing mechanism rather than a new one. `MetricObservation.reported_at` (already declared,
currently unpopulated) is the natural home for the fact's `filed` date.

With that fix, nothing merges them, and nothing should in this phase.

Criteria for asserting equivalence are in §8. For this phase the rule is: **emit both, mark
neither canonical, and make the pair visible.**

**Do not assume XBRL supersedes a table observation.** For the four metrics where both exist,
XBRL is exact and the table is often millions-rounded — but the table reading is what the company
*presented to investors*, and a post quoting "$2.58 billion" is quoting the presentation, not the
tag.

### 2.5 Tests and fixtures

- A committed Companyfacts slice for CIK 0001801169 covering the six metrics, saved as a real
  fixture (the corpus rule: real filed values, never hand-edited).
- Benchmark cases: one duration concept, one instant concept, one restated period (the FY2020
  triple above), one company-extension concept absent from Companyfacts, one amended filing.
- An executable assertion that every `mapping_type: exact` external mapping in `metrics.yaml`
  resolves against the committed fixture — **this is the test that would have caught
  `us-gaap:Revenues`**, and it is the highest-value single test in this plan.
- Live-marked test hitting the real API, deselected by default.

---

## 3. Full narrative and event run

### 3.1 The bound, and what lifting it requires

10,807 distinct request digests exist; 14 have answers. Six things are required, in dependency
order:

1. **A live-provider path in the pipeline.** `extraction/context.py` pins `inner=None`. The
   wiring belongs in `context.py`, the sanctioned composition root. *(The rule is narrower than
   "stages may not import providers" — `narrative_lane.py:35` and `event_lane.py:32` both import
   the provider **port**. What is enforced, by
   `test_the_narrative_lane_reaches_a_provider_only_through_the_port` and
   `test_importing_the_narrative_package_does_not_load_an_http_client`, is that a lane reaches a
   provider only through the port and never loads an adapter.)* Keep the replay store in front so
   the 14 existing answers are reused and the new run is a strict superset.
2. **Freeze the digest inputs before spending GPU hours.** The request digest is
   `sha256(IDENTITY_VERSION, prompt, schema, model_id, temperature, max_tokens)`. `max_tokens` is
   *computed*, not configured — from `DEFAULT_MAX_OUTPUT_TOKENS`, `_CHARACTERS_PER_TOKEN`,
   `_REQUEST_OVERHEAD_TOKENS` and `provider.context_tokens`. Editing any of them silently
   orphans every stored answer while touching no prompt text. Pin them, and add a test that
   fails on change rather than a comment.
3. **Resumability, which does not exist and is a hard blocker at this duration.**
   `RunDirectoryWriter.begin()` deletes any prior partial by design, and `lane_outputs.jsonl` is
   rendered as one string after the whole run returns. A crash at request 9,000 loses all 9,000.
   **The lowest-friction fix respects the existing architecture: make the run write through the
   answer store.** `AnswerStore` is already digest-keyed and already does stage-then-rename;
   giving `ReplayingGenerationProvider` a live `inner` plus periodic `store.write()` makes the
   answer store the checkpoint. `run_directory.py`'s all-or-nothing discipline stays untouched.
4. **Trim the event lane before paying for it.** 8,273 of 11,345 model-backed candidates are
   event-lane, and 5,201 of those were selected by `document_type_prior` alone. This is a
   `config/selection_policy.yaml` change, not a model change, and it is the largest single
   lever on runtime.
5. **Fix `earnings_release` first** (§1.3), or the most common disclosure event in the corpus
   produces a wall of `EVENT_TEMPORAL_REQUIREMENT_UNMET`.
6. **Expect verification to change character.** The current 5/5 pass with 0 failures is largely a
   statement about the table lane (2,690 of 2,717 claims). A real run exercises
   `QUOTED_SPAN_NOT_IN_PASSAGE` and the ontology validators at ~1,000× the current volume.

### 3.2 Cost and runtime — method, not a guess

No latency or token counts are persisted anywhere (deliberately). The only measured throughput
in the repository is **~67 tok/s** (247 tokens in 3.69 s, `LOCAL_RUNTIME_VALIDATED.md`), with
benchmark prompts at 3,529–4,806 prompt tokens and a worst measured completion of 3,601 tokens.

**Estimate as follows, and publish the measurement rather than the estimate:**

```
runtime ≈ Σ over candidates (prompt_tokens/prefill_rate + completion_tokens/67)
```

Run a **timed 100-call pilot** stratified across the three document types, record prefill rate,
mean completion length and abstention rate, then multiply. Do this *before* committing to a full
run and record the result here.

**No runtime band is published in this plan on purpose.** Prefill rate is not measured anywhere
in the repository, and prompts run 3,529–4,806 tokens, so the prefill term — likely the dominant
one at ~10,800 requests — is currently unknown. Any range stated now would be an inference
wearing the clothes of a measurement, in a section that exists to insist on the difference.

### 3.3 Acceptance criteria

- Every one of the 10,807 digests has a stored answer, or a recorded refusal that is not
  `NO_STORED_ANSWER`.
- The run is resumable: killing it mid-run and restarting re-issues only unanswered digests,
  demonstrated with a real kill and a real restart.
- Re-running with an unchanged answer store issues **zero** new requests and produces
  byte-identical catalogs.
- `acquisition_contracts`, `mortgage_rate`, `home_price_appreciation` are populated or carry a
  recorded reason why not.
- A reviewed sample (≥50 claims, ≥20 events) is scored against hand annotation, and the
  event-type and predicate coverage counts are published against the declared 19 and 30.

**Do not regenerate answers after unrelated code changes.** The digest already guarantees this
for prompt-neutral edits; §3.1 item 2 is what keeps the guarantee true.

---

## 4. Earnings releases and shareholder letters

**This lane largely exists.** `config/selection_policy.yaml` rule `earnings-and-letters` matches
role slugs `ex99-01/02/03` and includes all 93 EX-99.x artifacts — zero dropped —
and `canonical_normalizer.py` maps them to `earnings_release` / `shareholder_letter` /
`supplemental`. **21 complete quarterly release + letter + supplement triples** exist,
2021-05-11 through 2026-05-07. The Q4 2020 release (`0001801169-21-000010`, 2021-03-04) has
neither a letter nor a supplement and is itself one of the 22 item-2.02 `earnings_release`
documents, not an addition to them.

### 4.1 The real problem: the type is a slot number, not a semantic label

EX-99.1 means "first exhibit under Item 601(b)(99)". Opendoor uses it for earnings releases,
board-appointment press releases, listing notices, de-SPAC announcements — and one 219,024-char
litigation *Stipulation of Settlement*.

Measured against SEC item codes:

| `document_type` | Documents | On a filing carrying item 2.02 |
| --- | ---: | ---: |
| `earnings_release` | 45 | **22** |
| `shareholder_letter` | 26 | 21 |
| `supplemental` | 22 | 21 |

23 of 45 `earnings_release` documents are not earnings releases. `config/extraction.yaml` routes
lane selection on exactly this field, so the imprecision propagates into extraction — and none of
the mistyped documents carries a `needs_review` flag.

### 4.2 What F4 should actually do

1. **Split the type using `items`, which is already in the data.** Item `2.02` separates the 22
   true earnings sets from the 23 false positives with zero misses in this corpus. The selection
   rule engine already supports `items_any`. `SelectedArtifact.items` exists and
   `_document_type(artifact)` already receives the whole artifact — it simply does not consult
   `items`. The change is smaller than a new plumbing path: it is a rule edit plus one lookup.
2. **Add a `press_release` type** for the 23 announcement-shaped EX-99.1s, and route the
   stipulation out of `earnings_release` — it is the largest single document in that type and
   will dominate any retrieval over it.
3. **Make `document_type` an enum.** Eight values are documented in prose and consumed by config;
   nothing enforces them.
4. **Flag the residual risk** — EX-99.x on a filing without item 2.02 should carry a
   classification flag so the imprecision is visible in the catalog rather than latent.

### 4.3 Double counting, identity, hierarchy

- **8-K carriers are already separate documents with no duplicated content.** Zero duplicate
  `content_sha256` groups across all 294 documents. The 22 earnings carriers are ~4 KB cover
  pages with 13 passages and 4 boilerplate tables (SEC header, securities table, exhibit index);
  no financial figures repeat from the exhibits.
- **One genuine exception:** accession `0001104659-21-010984` (2021-02-02, item 2.02) has **no
  EX-99 at all** — preliminary FY2020 results live in the 8-K body and are typed
  `narrative_primary`. It is the only earnings disclosure in the corpus carried by an 8-K body,
  and any "find all earnings documents" rule keyed on exhibit role will miss it.
- **Source hierarchy** for the same figure: EX-99.1 release table → EX-99.2 letter prose →
  10-Q/10-K body. Record the ordering; do not use it to drop anything (§8).
- **Company-hosted equivalents are out of scope for this phase.** SEC copies exist for every
  release; ingesting a second copy invites duplicate-document work for no new fact. The one
  exception already known to the ontology is `accountable.opendoor.com`, declared as a
  `discovery_only` disclosure channel — which by its own constraint may not support a historical
  claim.

---

## 5. Guidance and outlook model

### 5.1 What exists

`guidance_issuance` is a declared event type with `participants: [reporter, period]`,
`allowed_properties: [guided_metric, low_value, high_value, unit, currency]`, and
`sec_item_codes: ["2.02", "7.01"]`. Its description states the intent precisely: *"Kept apart
from earnings_release so a guided figure is never mistaken for a reported one."*

Nothing else exists. No guidance claim kind, no guidance metric, no range-valued observation.
The narrative lane **actively refuses** guidance: `FORWARD_GUIDANCE` is a declared statement type
routed straight to a `GUIDANCE_NOT_REPORTED` abstention.

### 5.2 Three defects to fix before building on it

1. **The rule is unsatisfiable.** `inference_restrictions` demands *"assertion_type other than
   `reported`"*, but `AssertionType` offers only `calculated` (which triggers required
   calculation fields), `classified` and `inferred` — all of which misdescribe a guided figure.
   Add a `GUIDED` member, or amend the YAML. As written the rule cannot be complied with, so it
   also cannot be tested.
2. **The range is untyped.** `LaneEvent.properties` is `dict[str, str]`; `EventInstance.properties`
   is `dict[str, Any]`. `low_value: "1.0 billion"` would be accepted silently, bypassing the
   scale discipline every observation lane enforces.
3. **There is no future-period guard anywhere.** No constraint compares a period against a filing
   date; an observation with `period_end: 2027-12-31` validates today. The run happens to be
   clean — 0 of 2,707 observations are forward-looking, minimum lag +15 days — but that is the
   lanes behaving, not the contract enforcing. **Add the guard regardless of guidance work:** it
   is precisely the check that catches a guided figure mislabelled `reported`.

### 5.3 Recommended model — event-first, not observation-first

**Do not add `GuidanceStatement`, `GuidanceMetric`, `GuidanceRange`, `GuidancePeriod`,
`GuidanceRevision` and `GuidanceStatus` as six new node types.** Five of the six are properties
of one thing, and the ontology already has the one thing.

| Brief's concept | Recommendation |
| --- | --- |
| `GuidanceStatement` | **reuse** `guidance_issuance` event — already declared, already offered to the event lane |
| `GuidanceMetric` | **reuse** the `guided_metric` property, constrained to a declared `metric_id` |
| `GuidanceRange` | **promote** `low_value`/`high_value` off the untyped bag into typed fields; a point guidance sets both to the same value |
| `GuidancePeriod` | **reuse** the existing `period` participant (`fiscal_period` entity type) |
| `GuidanceRevision` | **defer** — a revision is a *relationship between two guidance events*, and needs the same evidence rules as `SUPERSEDES` (§8). Do not add it before there is more than one guidance event to relate. |
| `GuidanceStatus` | **defer** — `maintained/raised/lowered/withdrawn/replaced` is a derived comparison of two events, not a filed fact. Deriving it requires the revision link that is itself deferred. |

Qualitative guidance ("we expect holding periods to lengthen") has no numeric range and should be
recorded as a guidance event with `guided_metric` set and no `low_value`/`high_value`, or refused
— **not** coerced into a number.

### 5.4 Actual-versus-guidance comparison must be deterministic

**Do not let an LLM decide whether a guided figure and an actual are comparable.** The check is
mechanical and the fields already exist. Two values are comparable only if **all** hold:

`metric_id` equal · `unit` equal (after scale application) · `currency` equal ·
`period_type` equal · period identical (`period_start`+`period_end`, or `instant_date`) ·
`gaap_status` equal · `population` equal where the metric declares one.

Only then may an outcome be computed, and its vocabulary must include **`not_comparable`** as a
first-class result, not an error. One trap to handle explicitly: for non-GAAP metrics,
`MetricFormulaVersion` is date-ranged and resolved by `as_of = period_end or instant_date` — a
future-dated guidance value can fail with *"no formula version covers {as_of}"* unless formula
validity ranges are open-ended or the lookup is special-cased.

### 5.5 Corpus available

246 passages sit under a heading containing "Outlook" — but `heading_path` is **cumulative**, so
passages titled "Conference Call and Webcast Details" or "Use of Non-GAAP Financial Measures"
count merely for sitting under "First Quarter 2021 Outlook". All 246 are in `earnings_release`,
and only **89** are in the narrative candidate set (192 in the event set). A broader lexical
sweep matches 520 passages.

*All three figures are my own regex measurements and are recall-oriented upper bounds, not
verified guidance counts.* The tightest defensible statement is that **the narrative lane's real
guidance exposure is on the order of 89 passages, not 250** — and that the current pipeline is
not merely refusing guidance, it is **mostly not reaching it**, because the lane never ran.

### 5.6 Ontology changes required

| Change | Kind | Required for |
| --- | --- | --- |
| `AssertionType.GUIDED` | **Python** (`values.py`) — enums are shape, not content | making the existing rule satisfiable |
| Typed `low_value`/`high_value`/`unit`/`currency` on the event payload | **Python** (`extraction/core/models.py`) | scale discipline |
| Future-period constraint | **Python** (`constraints.py`) | keeping guidance out of the observation set |
| `guidance_issuance` property tightening | **YAML only** | |

New *entity types* and new *event types* are pure YAML edits — the loader is table-driven and
"never knows a concept name". New *claim kinds* are not: `ClaimKind` has three members and
`ontology/validation.py` dispatches through hardcoded three-entry dicts. **This is the argument
for keeping guidance an event rather than a fourth claim kind.**

---

## 6. Earnings-call transcripts

### 6.1 A finding that changes the shape of this stage

**Opendoor no longer holds a traditional earnings conference call.** It has replaced it with a
*"Financial Open House"* — a video event streamed on `investor.opendoor.com`, Robinhood, YouTube
and X, including a live shareholder Q&A segment, with replay and materials posted afterwards.
*(verified 2026-08-03 via IR announcements and coverage of the Q2 2026 and Q4 2025 events.)*

So "earnings-call transcript" is **not a uniform artifact across this corpus**. Early quarters
are analyst-Q&A calls; recent ones are video events with shareholder questions. Speaker-role
segmentation (executive / analyst / operator) does not transfer cleanly across that boundary, and
a schema assuming "operator → prepared remarks → analyst Q&A" will misfit the recent events.

### 6.2 Source options

**SEC EDGAR is not a source.** Full-text search for `"transcript"` on CIK 0001801169 returns
**12 hits, none of which is an earnings-call transcript** — they are incidental word usages in
agreements, a 2020 SPAC 425 communication, and the 2025 litigation stipulation. *(verified
2026-08-03 against `efts.sec.gov`.)* Opendoor has never furnished a transcript to the SEC.

**Every terms-of-service and retention claim in the table below is `(unverified)`.** None of these
pages' terms were retrieved and read; FMP's terms page returns 403 to a non-browser client. The
table records *what must be checked*, not what is known. **No source decision may be made from
this table alone** — see the companion handoff document.

| Source | Cost | OPEN coverage | Storage / redistribution | Credential | Status |
| --- | --- | --- | --- | --- | --- |
| **Company IR webcast/replay** | free | replay retention window **(unverified — must be checked; it is the sole reason this route is not the default)** | company site terms *(unverified)*; audio, not text | none | verified to exist |
| Local ASR (Whisper) on the public webcast | free + GPU | bounded by the retention window above | derived text from a public broadcast | none | see §6.3 |
| Motley Fool / Insider Monkey / Investing.com / Globe and Mail | free to read | multi-year | scraping/republication terms *(unverified)* | none | verified to carry OPEN transcripts |
| Seeking Alpha | paywalled | multi-year | copying terms *(unverified)* | account | — |
| Quartr | paid | multi-year | terms *(unverified)* | yes | — |
| **Financial Modeling Prep** transcript API | tiers **(unverified)** | claims full transcripts | a separate Data Display and Licensing Agreement is reportedly required to display or redistribute *(unverified — sourced from search result text, not from the terms page)* | API key | API existence verified |
| AlphaSense / S&P Capital IQ / LSEG / FactSet | enterprise | excellent | per-seat *(unverified)* | yes | disproportionate to a prototype |

### 6.3 Copyright landscape — stated plainly, not as legal advice

An earnings call is **three different things** for this purpose, and collapsing them is the
mistake to avoid:

| Layer | Status | Consequence |
| --- | --- | --- |
| **Prepared remarks** | Read from a script the company authored and fixed. That script is the company's own copyrighted work. | This is the densest source of quotable management explanation, **and the layer with the clearest owner.** A "it was only spoken, so it is not fixed" argument does not apply here. |
| **Q&A** | Extemporaneous. Not fixed by the speaker at the moment of utterance. | The weakest copyright claim of the three — and the layer the executive-only rule (§6.4) restricts most heavily anyway. |
| **Transcriber's edited transcript** | Carries an editorial contribution that transcript vendors do assert rights over. | Why "the words were public" does not make a vendor's transcript free to redistribute. |

So the practical line is **processing locally versus republishing verbatim**, applied to all three
layers — not a claim that any layer is unprotected.

**Recommendation:** acquire transcripts under a paid API whose terms permit local processing
(FMP is the leading candidate, terms unread), **store the full text locally, never republish it
verbatim**, and constrain the post generator to short attributed quotations. **Fallback:** local
ASR over the public IR webcast, accepting the retention window and ASR speaker-attribution error.

Both routes depend on facts not yet checked. **This is the one stage that must not begin on
inference.**

**This is the one stage that should not begin without a founder decision** — see §14.

### 6.4 If and when it is built

- **Identity:** `transcript:{cik10}:{fiscal_period}:{event_date}:{provider}` — provider in the id
  because two providers' transcripts of one call are genuinely different documents, not
  duplicates to merge.
- **Passage identity:** reuse the existing `#p{n}` convention so transcript passages are
  addressable by the same evidence machinery.
- **Speaker segmentation** with an explicit `speaker_role` ∈ {`executive`, `analyst`,
  `operator`, `shareholder`, `unknown`} and a `section` ∈ {`prepared_remarks`, `qa`}.
- **The hard rule: an analyst question is not a company claim.** Only utterances with
  `speaker_role == executive` may produce a metric observation, a guidance event or a company
  relationship. Enforce it in validation, not in a prompt. An analyst's premise ("your margins
  fell 300 bps") must never become an observation.
- **Corrections and versions:** transcript providers issue revisions. Keep both, link by
  `SUPERSEDES` only under the evidence rules of §8.
- Transcripts are a **new `SourceLane` member** — a Python enum edit, and therefore a contract
  change (§9).

---

## 7. Historical stock-price lane

### 7.1 What exists — nothing

No market-data dependency, no config key, no credential, no calendar, no price concept. The
repository's four dependencies are `httpx`, `pydantic`, `PyYAML`, `neo4j`. `tickers` exists only
as a display string, and the repository explicitly forbids using it as an identifier: *"CIK is
the canonical issuer identity. Tickers are display values only."*

What the ontology **does** already have, unused: `listed_security` and `common_stock` instrument
types with `ticker`/`exchange` properties, a `stock_exchange` entity type, a `nasdaq` instance
with `mic: XNAS`, and the `ISSUED` and `LISTED_ON` predicates. Zero edges of either exist. A
price lane can hang off `(:PublicCompany)-[:ISSUED]->(:CommonStock)-[:LISTED_ON]->(:StockExchange)`
without inventing a single concept.

### 7.2 Provider — measured today, not assumed

| Option | Result on 2026-08-03 |
| --- | --- |
| **Stooq direct CSV** (`stooq.com/q/d/l/?s=open.us&i=d`) | ❌ **now returns a JavaScript proof-of-work anti-bot challenge**, not CSV. The widely-recommended "no-key CSV" route is dead headlessly. *(measured)* |
| **Yahoo chart endpoint** (`query1.finance.yahoo.com/v8/finance/chart/OPEN`) | ✅ **1,532 daily sessions, 2020-06-18 → 2026-07-24**, OHLCV + adjusted close present, no key. *(measured)* |
| Alpha Vantage / Tiingo / Polygon / FMP / EODHD / Twelve Data | free tiers exist, all require a key; specific 2026 limits **unverified** |

1,414 of those sessions fall in 2020-12-04 → 2026-07-24, matching an independent estimate of
~1,418 trading days for that window, so the series is complete rather than sparse. **Caveat,
stated plainly:** the endpoint is undocumented
and Yahoo's terms discourage automated access. It is the right choice for a local prototype and
the wrong one for anything published — record that, and put a credentialed provider behind the
same adapter interface so swapping is a composition-root change.

**Corporate actions:** Opendoor has had **no split, reverse split or dividend** in the window.
Two independent measurements agree: the Yahoo `events` object is empty, and SEC Companyfacts
shows shares outstanding rising monotonically 577.2 M (2021-02) → 958.3 M (2026-02) with no
step-down. *The ontology declares a `reverse_stock_split` event type anyway, correctly — it
rebases every per-share figure — and the lane must handle one even though none has occurred.*
OPEN's Yahoo history starts **2020-06-18**, six months before the 2020-12-18 de-SPAC close:
**128 sessions of pre-merger IPOB pricing appear under the OPEN symbol** *(measured)*. That is a
different security and must be segmented off explicitly, not silently treated as Opendoor's
history. The series also ends 2026-07-24, ten days before this plan's date — expect provider lag
and record `fetched_at` on every row.

**Trading calendar:** derive sessions from the price series itself. It is exact for this purpose
(a session exists iff the benchmark traded), needs no dependency, and honours the
standard-library-first default. `pandas-market-calendars` and `exchange_calendars` both pull
pandas and a large dependency tree for a fact the data already contains.

**Benchmarks:** `SPY` for broad market. For housing, `ITB` (US home construction) or `XHB`
(homebuilders) — both ETFs with the same free daily access. Case-Shiller via FRED is monthly and
lagged two months, so it is useful narrative context and **useless for an event window**.

### 7.3 The after-hours trap — measured, and currently wrong in the repository

SEC publishes `acceptanceDateTime` in **Eastern Time**, the EDGAR system's reference timezone
*(verified 2026-08-03 against EDGAR API documentation, which states filing timestamps carry an
explicit ET offset such as `-05:00`/`-04:00`)*. This repository stores the value verbatim from the
SEC submissions feed — including a trailing `Z` that asserts UTC.

The corpus distribution corroborates it. Of 109 filings: hour **16 → 71**, 17 → 14, 06–09 → 19,
14 → 1, 19 → 1, 21 → 3. Hour 16 ET is immediately after the 16:00 close — the classic
earnings-release slot. Read as UTC, hour 16 would be midday ET and hour 06 would be 01:00 ET,
implausible for both. *(The distribution is corroboration; the timezone itself rests on the
documentation, not on this inference.)*

**This is a defect to fix at F8, and it silently corrupts every event window if it is not.**
Assignment rule:

- Announcement timestamp = `acceptance_datetime`, parsed as **America/New_York**.
- If it falls at or after 16:00 ET, or on a non-session day, the event's **day 0 is the next
  trading session**. Otherwise day 0 is the session in progress.
- Record the rule's output as an explicit `assigned_session` field, never recompute it ad hoc.

### 7.4 Windows, and what they are allowed to say

Derived windows: ±1, 3, 5, 20 trading days, with a pre-event baseline and a benchmark-relative
(excess) return. Every one is a **calculated** fact:

- `assertion_type: calculated`, `source_lane: calculated`.
- It cites its **inputs' identifiers and the arithmetic**, never a passage. A price movement has
  no filed passage to quote, and citing the 8-K passage for a return would be a provenance lie.
- Provider provenance (`provider`, `endpoint`, `fetched_at`, `source_url`, row hash) attaches to
  the `PriceObservation`, which is the authoritative record; windows are derived and rebuilt.

**Do not represent price movement as proof of causation.** The edge name in §9 is
`REACTED_AROUND` — temporal co-location, nothing more — and every window must carry a
`confounders_not_controlled` caveat that the evidence package surfaces verbatim. Where more than
one event shares a session, the window must say so rather than attributing the move to one.

Company-reported facts and market observations stay in separate node types and separate lanes,
and no query may present a price-derived number as a company-reported one.

---

## 8. Cross-source reconciliation

### 8.1 The measured baseline

Across the current 2,707 observations there are **538 distinct `(metric, subject, period)`
groups**; 461 hold more than one observation, and **42 hold more than one distinct value**.
Of those 42, 36 are thousands-vs-millions rounding and 6 are `scale: null` vs `units`.

Four are **same document, same lane, same metric, same period**:

| Metric | Period | Values | Document | Lane |
| --- | --- | --- | --- | --- |
| `market_count` | 2021-03-31 | 27 vs 44 | `open-20220331.htm` | table |
| `market_count` | 2022-03-31 | 45 vs 53 | `open-20230331.htm` | table |
| `market_count` | 2023-03-31 | 53 vs 50 | `open-20240331.htm` | table |
| `pct_homes_on_market_gt_120_days` | 2023-12-31 | 55 vs 18 | `q42023formxex992sharehol.htm` | narrative |

The existing `conflicting_duplicates` check passed 2,707/0 because `observation_identity`
includes the passage tuple, and these sit on different passages of one document. That blind spot
is real and will widen as lanes are added.

**But these four are extraction defects, not issuer disagreement — and the distinction is
critical.** *(verified 2026-08-03 by reading the source passages.)* All four are the same
column-period mis-assignment. Passage `…q42023formxex992sharehol.htm#p20` is a multi-period table
whose header runs `Three Months Ended | December 31, 2023 | September 30, 2023 | June 30, 2023 |
March 31, 2023 | December 31, 2022 | Year Ended 2023 | 2022`. The lane dated a **December 31,
2022** column to **2023-12-31**. The same document's prose (`#p9`) states the correct figure
outright: *"As of December 31, 2023, 18% of our homes had been listed on the market for more than
120 days."* The `market_count` cases are identical in shape — a `Year Ended December 31,` column
group inheriting a sibling `March 31,` instant. Two further groups above 2% (`housing_inventory_homes`
2023-12-31 = 5,326 vs 12,788, where 12,788 is the 2022 column) are the same bug.

**Consequence for this plan:** these must be fixed in the table/narrative lanes, not surfaced as
counter-evidence. Presenting a known-wrong column reading to an investor post as legitimate
issuer disagreement would be worse than omitting it. F9 must therefore separate *extraction
defects* from *genuine cross-source disagreement*, and the four above belong in the first
category. Fixing the multi-header column-group binding is a prerequisite to F9, not an output
of it.

### 8.2 What to build

1. **An advisory equivalence check keyed on `(metric, subject, period)` alone**, separate from
   the existing run-failing check. It flags **42** groups at zero tolerance and **12** above 2%
   relative difference. It must be **advisory** — it names candidates for review, it does not
   merge and it does not fail the run.
2. **Rank by `scale` before comparing — magnitude alone does not separate the two populations.**
   *(verified)* Of the 12 groups above 2%, **6 are `{thousands, millions}` rounding pairs**
   (`adjusted_ebitda` 2021Q1: −2,141,000 vs −2,000,000, a 7% gap that is pure presentation) and
   6 are the column mis-assignment of §8.1. No relative-difference threshold divides them. What
   does divide them is already on every row: `scale` and `scale_location`. A check that groups by
   scale first removes all 36 rounding pairs **with no schema change at all**.
3. **A precision field remains worth adding, but as a second step, not the first.** Recording the
   significant figures a source printed makes the rounding relationship explicit rather than
   inferred from a scale label, and an XBRL lane emitting `2,583,121,000` beside a table's
   `2,583,000,000` is exactly where an inferred relationship gets thin. Sequence it after the
   scale-ranked check has been measured, so its value is demonstrated rather than assumed.
3. **Source precedence, recorded but not applied.** Order: XBRL (exact, tagged) → EX-99.1 release
   table → 10-K/10-Q body table → EX-99.2 letter prose → transcript utterance. Use it to *rank*
   candidates in an evidence package, never to delete an observation.

### 8.3 The relationships the brief asks about

| Predicate | Status | Verdict for this phase |
| --- | --- | --- |
| `SUPERSEDES` | **already declared** (`metric_observation → metric_observation`, `temporal: true`, `inference_level: derived`, `evidence_required: false`); **never emitted** | **Defer emission.** Its deferral is currently load-bearing on an untested assumption — that filing-date ordering plus a value change implies restatement. The data says otherwise: all 42 filing-date-ordered value changes in this run are rounding, so a naive rule would mark 42 non-restatements as restatements. Do not emit until the precision field exists. |
| `SAME_REPORTED_FACT` | not declared | **Projection-local for this phase.** The equivalence check produces a *review candidate*, not an asserted fact. Materialize it as a derived edge with `ontology_declared: false` if it helps queries; do not add it to the ontology. |
| `RESTATES` | not declared | **Deferred pending evidence rules.** Redundant with `SUPERSEDES` unless the two are given distinct semantics, and neither can be emitted safely yet. |
| `GUIDES_FOR` | not declared | **Needed** if guidance ships — links a `guidance_issuance` event to the `fiscal_period` it targets. But the event's `period` participant already expresses this via `PARTICIPATES_IN`. **Prefer the existing participant; do not add the predicate.** |
| `ACTUALIZES` | not declared | **Deferred.** It is the result of the deterministic comparison in §5.4, which is a *computation over two facts*, not a filed relationship. Materialize as a query result first; promote to an edge only if queries prove it needed. |
| `EXPLAINS` | not declared | **Needed for post quality, deferred for evidence reasons.** Linking a management statement to a metric observation is the highest-value edge for investor posts — and the easiest to get wrong, because "explains" is a causal claim read out of prose. Requires transcript or narrative extraction to exist first. Revisit after F7. |

**None of these is added to the ontology in this phase.** `SUPERSEDES` is already there and stays
unemitted; the rest earn their place with evidence rules or stay out.

### 8.4 Unresolved conflicts

A conflict that survives review is a **fact about the corpus**, not a defect to hide. Represent it
by keeping both observations and attaching a review record. The graph already makes duplication
askable (`V1_GRAPH_PROTOTYPE` Q10); this phase makes it *reviewed*.

---

## 9. Graph changes

Minimum additions. Every proposal validated against the current ontology; reuse preferred to
addition throughout.

### 9.1 Nodes

| Proposed | Verdict |
| --- | --- |
| `GuidanceStatement` | **Reject** — reuse the declared `guidance_issuance` event; it projects as `:Event` today |
| `Transcript` | **Accept** as a `:Document` with a new `document_type`, **not** a new base label. It is a document with passages; the entire evidence chain already works |
| `Speaker` | **Reuse `:Entity:Person`** for executives (already projected — 3 exist). Analysts are external and unresolved; carry `speaker_role` as a passage property rather than minting entity nodes for people the corpus cannot identify |
| `TradingSession` | **Accept** — small, closed, and the natural key for window arithmetic |
| `PriceObservation` | **Accept** — must be a distinct base label from `:Observation`, so no query can mistake a market price for a company-reported figure |
| `MarketIndex` | **Accept**, minimal — or reuse `:Entity:ListedSecurity`, which is declared and unused |
| `CalculatedReturn` | **Accept** — carries `assertion_type: calculated` and cites inputs, not passages |

### 9.2 Relationships

| Proposed | Verdict |
| --- | --- |
| `ISSUED_GUIDANCE` | **Reject** — `PARTICIPATES_IN` with `role: reporter` already expresses it and is already emitted |
| `GUIDES_FOR` | **Reject** — the event's `period` participant covers it (§8.3) |
| `ACTUALIZES` | **Defer** (§8.3) |
| `SPOKE_IN` | **Accept if transcripts ship** — `(:Entity:Person)-[:SPOKE_IN]->(:Passage)`; projection-local |
| `EXPLAINS` | **Defer** (§8.3) |
| `REACTED_AROUND` | **Accept**, projection-local, `ontology_declared: false`, with the causation caveat baked into its properties |
| `BENCHMARKED_AGAINST` | **Accept**, projection-local |

### 9.3 Required plumbing

- **Extend the evidence contract** so `xbrl_fact`, `external_page` and a transcript kind resolve
  without a `passage_id`. `EvidenceReference` already declares the fields and `claims.yaml`
  already declares the evidence types with their `required_fields`; three validators refuse them
  (`validate_evidence_resolves`, `graph/core/citations.required_passage_id`,
  `edges._evidence_edges`). **This is F0 and it gates F1, F6 and F8.**
- **Project ontology instance properties.** `cik`, `tickers`, `exchange`, `mic` are declared and
  dropped. Small, contained change in `nodes.py`.
- **New `SourceLane` members** for transcripts and market data — Python enum edits, hence
  contract changes requiring a version bump.
- **Two hand-maintained allowlists** (`loader.CONCRETE_LABELS`,
  `schema.CONSTRAINED_RELATIONSHIP_TYPES`) must be extended for every new label and predicate.
  They could instead be derived from the ontology registry, preserving the guarantee and removing
  the manual step — the same argument `deferred_metric_ids` already wins.

Neo4j remains a rebuildable derived store. Stage-13-style catalogs remain the authoritative input.

---

## 10. Post-generation readiness

Data and retrieval handoff only. **No GraphRAG, no agent, no post writer in this plan.**

`V1_GRAPH_PROTOTYPE` §9 already specifies the contract, and it is more defensible than the
sketch in `docs/01`. Build to it:

```
TopicSelector → GraphRetriever → StoryEvidencePackage → PostGenerator → CitationVerifier
```

Nothing implements `GraphRetriever` today — `GraphStore` is write-only and the only Cypher read in
the repository is a count reconciliation inside load verification.

### `StoryEvidencePackage`

| Field | Contents |
| --- | --- |
| `structured_facts` | metric histories, each observation with `source_lane`, period, precision, `assertion_type` |
| `events` | event timeline with `occurred_on` / `announced_on` kept distinct |
| `guidance` | guidance events with the deterministic comparison result (§5.4), including `not_comparable` |
| `price_reactions` | `CalculatedReturn`s with benchmark-relative figures and the causation caveat |
| `primary_passages` | full passage text for every cited fact — resolved, or the package is refused |
| `supporting_passages` | management explanations, bounded |
| `counter_evidence` | genuine cross-source disagreement from §8, surfaced rather than suppressed — **never a known extraction defect**, which must be fixed upstream rather than presented as issuer disagreement (§8.1) |
| `provenance` | run ids, ontology definition hash, code commits |
| `warnings` | `ambiguity_codes`, `validation_state: warned`, `resolved: false` entities, events dated only by `announced_on`, differing population definitions |
| `compatibility_checks` | the §5.4 result and the §8 equivalence verdicts, as data |

Retrieval must be **bounded by construction** — `max_hops ≤ 2`, `max_nodes`, explicit
`edge_types` — never an unbounded traversal. `OBSERVATION_OF_SUBJECT` must be excluded from
default traversal: the `opendoor` node has degree ≥ 2,707 and will produce a hairball instantly.

Three statement classes stay apart, as §9 of the graph plan already requires: **reported** (cite
the passage), **calculated** (show inputs and arithmetic, never cite a passage for the result),
**inference** (labelled, never cited, never presented as a filed figure). Market data needs a
fourth treatment — it is neither filed nor derived from filed facts — and gets it via its own
lane and node type.

---

## 11. Implementation stages

Sequence adjusted from the brief on repository evidence. The two changes that matter: **F0 is a
contract *extension*, not just a freeze** (three later stages fail without it), and **F4 moves
earlier and shrinks**, because the source lane already exists and only its precision is wrong.

| | Stage | Goal | Depends on | Provider / credentials | Parallel with |
| --- | --- | --- | --- | --- | --- |
| **F0** | Contract extension & spine audit | Non-passage evidence resolves; future-period guard; `AssertionType.GUIDED`; precision field; instance properties projected | — | none | — |
| **F1** | XBRL acquisition & raw-fact catalog | Companyfacts + filing-instance facts cached with provenance | F0 | SEC API (no key) | F3, F4 |
| **F2** | XBRL mapping & the six metrics | Six deferred metrics populated; mapping test that catches `us-gaap:Revenues` | F1 | none | F3, F4 |
| **F3** | Full narrative/event corpus run | 10,807 requests answered; resumable; event/predicate coverage measured | F0, plus `earnings_release` fix and event-lane trim | local GPU | F1, F2 |
| **F4** | Release/letter precision repair | `item 2.02` split; `press_release` type; `document_type` enum | — | none | F1, F2, F3 |
| **F5** | Guidance model & extraction | Guidance events with typed ranges; deterministic comparison | F0, F3, F4 | local GPU | F8 |
| **F6** | Transcript acquisition & normalization | Transcripts as documents with speakers and sections | F0, **founder decision** | **yes — see §14** | F8 |
| **F7** | Transcript claim/explanation extraction | Executive-only claims; analyst questions refused | F6 | local GPU | F8 |
| **F8** | Stock prices & event windows | Daily OHLCV, sessions, windows, ET timestamp fix | F0 | none (Yahoo) | F5, F6, F7 |
| **F9** | Cross-source reconciliation | Advisory equivalence check (scale-ranked); review records | F2, F3, F5, F8, **and the §8.1 column-period fix** | none | — |
| **F10** | Graph projection & load changes | New nodes/edges; allowlists; schema version bump | F9 | Neo4j | — |
| **F11** | Factual-spine quality review | Corpus-scale validation; published coverage | F10 | none | — |
| **F12** | Story-evidence retrieval contract | `GraphRetriever` + `StoryEvidencePackage`, bounded | F11 | Neo4j | — |

Per-stage detail for the three that carry the most risk:

**F0 — contract extension.** *Inputs:* current contracts. *Files:*
`extraction/core/validation.py`, `extraction/core/models.py`,
`extraction/stages/catalog/jsonl_catalog.py`, `ontology/core/values.py`,
`ontology/core/constraints.py`, `graph/core/citations.py`, `graph/core/inputs.py`,
`graph/stages/projection/{nodes,edges}.py`. *(`required_passage_id` is applied to claim, issue
and rejection rows, not only evidence rows, and `ClaimRow.passage_id` is a required `str` — both
must change with it.)* *Outputs:* extended evidence resolution, new enum members, future-period
constraint, projected instance properties. *Tests:* a non-passage evidence reference validates; a
future-dated observation is refused; a synthetic XBRL-anchored claim passes validation.

*Acceptance:* the current run reproduces **the same 2,707 observation ids, values, units and
periods**, with any new field additive. **Not byte-identical** — `_observation_row` writes a
fixed-key dict, so any added key changes `observations.jsonl` and cascades into node properties.
The expected hash change must be recorded, not treated as a regression. *Founder gate:* yes —
this changes committed contracts and bumps versions.

**F3 — full narrative run.** *Acceptance:* §3.3. *Founder gate:* yes, before the GPU spend, on
the pilot measurement. *Risk:* the largest single time commitment in the plan.

**F6 — transcripts.** *Founder gate:* **blocking.** Do not begin without a source decision.

---

## 12. Benchmarks and validation

Benchmark cases follow the existing `benchmarks/extraction/v1/cases/*.yaml` convention — real
corpus fixtures, committed, never hand-edited.

| Area | Case |
| --- | --- |
| XBRL mapping | every `mapping_type: exact` mapping resolves against a committed Companyfacts fixture **(the `us-gaap:Revenues` regression test)** |
| Context/period selection | one duration, one instant, one fiscal-year-vs-calendar mismatch |
| Duplicate XBRL facts | the FY2020 revenue triple across three accessions |
| Guidance | a range, a point, a qualitative statement, a revision, one `not_comparable` pair |
| Transcript attribution | an executive statement and an analyst question in one passage; the question must not become a claim |
| Events across source types | one event announced in an 8-K and described in a letter |
| Price windows | a normal session, a half-day, a gap, a multi-event session |
| After-hours alignment | a 16:16 ET filing → next session; a 09:00 ET filing → same session |
| Cross-source equivalence | the 36 `{thousands, millions}` pairs must be suppressed **by scale, not by magnitude** — 6 of them exceed 2% relative difference, so no threshold separates them from the §8.1 column defects |
| Column-period binding | the 6 measured mis-assignments of §8.1 (4 same-doc contradictions + 2 further >2% groups) must be refused or correctly dated once the multi-header fix lands |
| Graph provenance | every fact type resolves to evidence, including non-passage evidence |
| Deterministic rebuild | two full runs produce byte-identical catalogs |

**Corpus-scale checks, not only benchmark recall:**

- Every observation's period ends on or before its source filing date (0 violations expected).
- Every XBRL observation's concept appears in the committed mapping set.
- No transcript observation carries `speaker_role != executive`.
- Every `CalculatedReturn` resolves to a `TradingSession` that exists in the price series.
- Distinct `(metric, period)` groups with >2% disagreement are enumerated and reviewed, not
  merely counted.
- Coverage published against denominators: metrics 26, event types 19, predicates 30.

---

## 13. Cost, runtime and storage

Formulas with a measurement step, not invented totals.

| Quantity | Method | Known input |
| --- | --- | --- |
| SEC API traffic | 1 Companyfacts request (1.30 MB measured) + 1 per filing-instance document | ~25 periodic filings |
| XBRL storage | Companyfacts 1.30 MB + instance docs × ~300 KB | 3 existing IPOB instances are 160–342 KB |
| Narrative inference | `Σ (prompt_tokens/prefill_rate + completion_tokens/67 tok/s)` over 10,793 unanswered digests | **measure with a 100-call pilot before committing** |
| Transcript storage | ~22 calls × ~60 KB text | trivial; dominated by licensing, not bytes |
| Transcript model calls | ~22 transcripts × passages per transcript | measure after F6 |
| Price storage | 1,414 sessions × ~4 symbols × ~80 B ≈ **< 1 MB** | measured session count |
| Graph growth | current 28,836 nodes / 35,603 edges + ~1,400 sessions + ~5,700 price observations + windows | XBRL adds ≤ ~340 observations |
| Rebuild time | measure `graph project` + `graph load` on the current run first — **no baseline is recorded today** | — |

Current storage: raw 746 MB, normalized 105 MB, extraction 33 MB, graph 74 MB. **The price and
XBRL lanes are negligible; the narrative run is the only material cost, and it is time, not
money** — the provider is local.

---

## 14. Open decisions

| # | Decision | Recommendation | Alternatives & consequences | Latest stage |
| --- | --- | --- | --- | --- |
| D1 | **Transcript source and licensing** | Paid API permitting local processing (FMP leading candidate), full text stored locally, **never republished verbatim** | Free aggregators — ToS prohibit scraping. Local ASR — free and low-risk but only ~1 year of replay history. Skip transcripts — loses management explanation, the richest post material | **before F6 (blocking)** |
| D2 | Is paid transcript access acceptable? | Yes, if it is a low monthly tier | If no, D1 collapses to ASR with a 1-year window, or transcripts are dropped | before F6 |
| D3 | **Stock-price provider** | Yahoo chart endpoint for the prototype, behind a swappable adapter | Stooq is **no longer viable headlessly** (measured). Keyed providers add credential friction but are publishable | before F8 |
| D4 | Ingest company-hosted releases when SEC copies exist? | **No.** SEC copies exist for all; a second copy adds duplicate-document work and no new fact | Yes — needed only if company-only material is later required | before F4 |
| D5 | **Add guidance to the ontology now?** | **Yes, minimally** — fix `AssertionType`, type the range, keep it an event. Do not add six node types | Defer entirely — but the narrative run (F3) will then keep discarding ~250+ outlook passages that must be re-run later | before F5 |
| D6 | Historical depth for prices | Full history from 2020-12; it is < 1 MB | Shorter window saves nothing measurable | before F8 |
| D7 | External housing context in the first milestone? | **No.** `mortgage_rate` and `home_price_appreciation` are narrative-sourced and will populate from F3 at no extra cost | Adding FRED/Case-Shiller is a new lane with new provenance rules for two metrics | before F9 |
| D8 | Fix `render_artifacts: false` or parse inline XBRL from disk? | Fetch Companyfacts first (covers 4 of 6); decide the instance-document route at F1 once the two extension concepts are scoped | Flipping the flag re-fetches artifacts across 109 filings | at F1 |

---

## 15. Out of scope

Broad peer-company ingestion · full housing-market graph · analyst consensus estimates · news
ingestion · social-media data · production scheduling · automatic public posting · custom graph
UI · unrestricted GraphRAG agents · **causal attribution** · autonomous investment
recommendations · segment-level XBRL dimensions · intraday prices · options and short interest ·
entity resolution beyond the existing placeholder scheme.

Peer companies and housing-market context are deferred on evidence: nothing in the repository or
the corpus requires them for an Opendoor factual spine, and both multiply provenance rules before
the single-issuer case is proven. `config/companies.yaml` holds one CIK, and the role-slug
document heuristics of §4 are known to be Opendoor-filer-agent-specific — **adding a second
issuer would degrade them sharply**, which is itself an argument for finishing the first.

---

## 16. Documents to correct

Not part of any stage; listed so the sweep is not forgotten.

- **`README.md`** — states extraction is *"planned only"* and *"717 tests"*. Both are stale:
  extraction, graph projection and Neo4j load are implemented, and 2,567 tests collect offline.
- **`plans/graph/V1_GRAPH_PROTOTYPE.md` §5.1** — still specifies a `./neo4j_data/` bind mount
  that does not exist; named volumes are used. `docs/05` records this as an open item.
- **`ontology/.../metrics.yaml`** — `us-gaap:Revenues` is marked `verified: true` and is wrong
  for this issuer (§2.1). Correct at F2, with the reason recorded.

## 17. Defects this plan discovered in existing work

Found while writing, not previously recorded. None is fixed here; each is named so it is not
rediscovered.

| Defect | Where | Severity |
| --- | --- | --- |
| `us-gaap:Revenues` mapping is wrong for this issuer, and marked `verified` | `metrics.yaml` | blocks F2 |
| XBRL observations would all share one id — `digest()` over no parts is a constant | `identifiers.py` + any XBRL lane | blocks F2 |
| Six column-period mis-assignments from multi-header tables, live in the graph | table + narrative lanes | blocks F9 |
| `acceptance_datetime` stored as `Z` but published in ET | `sec_discovery.py` | blocks F8 |
| `LaneEvent` cannot carry `period_start`/`period_end`, so `earnings_release` is unemittable | `extraction/core/models.py` | blocks F3 |
| `guidance_issuance.inference_restrictions` demands an `AssertionType` value that does not exist | `events.yaml` + `values.py` | blocks F5 |
| Ontology instance properties (`cik`, `tickers`, `exchange`, `mic`) declared and dropped | `projection/nodes.py` | minor |
| `document_type: earnings_release` is 22/45 precise, with no review flag | `canonical_normalizer.py` | blocks F4 |
| No constraint prevents a future-dated observation | `constraints.py` | latent |
