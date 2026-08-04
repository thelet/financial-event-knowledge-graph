# V1 — Story agent

Connecting a local LLM to the Opendoor graph so it can discover, evidence, draft and verify
investor posts — without ever being the source of a number.

**Status:** planned, nothing implemented. Written 2026-08-03 in worktree
`FKG-llm-agent-plan` on branch `plan/llm-graph-agent`. **Rebased onto `edc2d5a` after the
factual-spine session landed F0 (15 commits); §0d records what that changed and what it
closed.** Measured against extraction run `extract-v1-lexical-833f7bcfbce9`, graph run
`graph-v1-0483dc6b4b10`, ontology `2.0.0` / `bb94f522ba12…`, graph projection `1.2.0`, and the
live Neo4j 5.26.28 Community instance at `bolt://localhost:7687`.

| | |
| --- | --- |
| Predecessors | [V1_GRAPH_PROTOTYPE](../graph/V1_GRAPH_PROTOTYPE.md) §9, §11 G6 · [V1_CLAIM_EXTRACTION](../extraction/V1_CLAIM_EXTRACTION.md) · [STAGE_07_LOCAL_PROVIDER](../extraction/STAGE_07_LOCAL_PROVIDER.md) |
| Concurrent | [V1_OPENDOOR_FACTUAL_SPINE](../factual-spine/V1_OPENDOOR_FACTUAL_SPINE.md) — a separate session is building F0–F12 on `main` |
| Companion | [WORKSTREAM_BOUNDARY.md](WORKSTREAM_BOUNDARY.md) — file ownership, shared surfaces, integration points |
| Anchor | Opendoor Technologies Inc., CIK `0001801169`, Nasdaq OPEN |

```text
data/graph_runs/<graph_run_id>/  +  Neo4j 5.26 Community
  → deterministic detectors        → StoryCandidate            (no model)
    → deterministic ranking        → ranked candidates          (no model)
      → bounded evidence builder   → StoryEvidencePackage       (no model)
        → editorial planner        → EditorialPlan              (model, schema-constrained)
          → constrained writer     → Draft                      (model, schema-constrained)
            → deterministic gate   → VerifiedDraft | RejectedDraft
              → model-assisted verifier                          (advisory only)
```

Facts marked *(verified)* were read on 2026-08-03 by the command or `file:line` recorded
beside them. Facts marked *(measured live)* came from a read-only probe of the running
Neo4j container. Facts marked *(unverified)* are design intent a stage must confirm.

Counts read from `data/` are reproducible only while that directory exists — it is
gitignored (`.gitignore:3`) and, as §0b records, it was once rewritten under an unchanged run
id while this plan was being written.

---

## 0. The answer first

1. **The graph was stale and nothing detected it; F0 has since rebuilt it, and nothing still
   detects it.** The defect was real: Neo4j held 2,707 `:Observation` nodes under
   `graph-v1-886059d862ce` while the extraction directory it named held 2,704, with the run
   *id* unchanged so every id-based check passed. **As of `edc2d5a` the graph is consistent** —
   `graph-v1-0483dc6b4b10` records `run_complete_sha256 = 1cc8f7b0…` and the file hashes
   `1cc8f7b0…` *(verified)*, and Neo4j holds 2,704 observations under that id *(measured live)*.
   **The staleness gate is still L0 and still ships first**, because nothing in the repository
   compares those two values on the way back in — the condition is repaired, not prevented, and
   §17.8 shows every other check in this plan passes a fact from a superseded run. *(§1.1)*

2. **The graph already carries everything a citation needs; it carries nothing a story needs.**
   `:Observation → :EVIDENCED_BY → :Passage{text} → :PART_OF → :Document{source_url,
   accession}` resolves for every fact, and `evidence.quoted_text` is a literal substring of
   its passage on 2,714/2,714 rows. But there is no retrieval code, no bounding primitive, no
   `max_hops`, no `LIMIT` in any code path, and `graph/contracts.py` declares no read
   protocol. G6 is a row in a table. *(verified — §1.2, §1.3)*

3. **Semantic search is not the bottleneck, and adding it now would break `graph verify`.**
   The fulltext index `passage_text` over `:Passage.text` already exists *(measured live)*.
   Adding an embedding property would change every passage node's `content_digest`
   (`graph/stages/load/verification.py:371`) and adding embedding nodes would break the node
   count check — both are assertions the graph load already makes. **V1 ships no embeddings.**
   §8 gives the sidecar design for when fulltext is measured to be insufficient.

4. **Seven detectors are buildable today; four are vapour.** `metric_move`, `trend_reversal`,
   `acceleration`, `cross_metric_divergence` (z-score form only), `inventory_risk`,
   `leadership_change`, `coverage_gap` all fire on real data with real numbers. Guidance
   revision, guidance-vs-actual, stock reaction and entity-network detectors have **zero**
   supporting rows and must not appear in V1. *(verified — §6)*

5. **`neo4j-graphrag` cannot be adopted without contradicting a written repository decision.**
   Its core install requires `numpy>=2.0` and `scipy>=1.13`; `plans/extraction/STAGE_09_HYBRID_SCOPING.md`
   §1 rejects exactly that — *"`numpy` is present in the environment but is **not** a declared
   dependency and must not become one."* The recommendation is a code-owned tool layer on the
   already-pinned `neo4j` 6.2.0 driver, borrowing graphrag's *query shapes* rather than its
   package. **No new dependency.** *(verified — §4)*

6. **Neo4j Community cannot give the agent a read-only database user.** `SHOW ROLES` returns
   `Neo.ClientError.Statement.UnsupportedAdministrationCommand` and `SHOW USERS` reports
   `roles: null` *(measured live)*. Read-only is therefore an **application-level** property
   in V1: code-owned parameterised Cypher, no string interpolation, no generated Cypher at
   all. §16 gives the server-side options and why each is deferred.

**The recommended first stage is therefore L0 — the staleness gate and the contract freeze —
not a retriever.** A package built from a graph that no longer matches its inputs would produce
a post citing a fact that has been retracted, and §17's adversarial pass shows that every other
check in this plan passes such a post.

---

## 0b. What the repository contradicted while this was written

| # | The brief or a draft assumed | The repository shows |
| --- | --- | --- |
| 1 | 2,707 accepted observations | **2,704** in the extraction run as of 12:20 UTC; 2,707 in the loaded graph. Both numbers are real and they describe different things |
| 2 | 4 relationship claims, 6 events, 185 documents, 8,776 passages | All four confirmed exactly |
| 3 | 26 metric definitions, 17 populated | Confirmed. The 17 counts moved by ±1 in the re-run: `market_count` 177→176, `housing_inventory_homes` 127→126, `pct_homes_on_market_gt_120_days` 89→88 |
| 4 | The graph holds conflicting values worth writing about (`market_count` 27 vs 44, inventory 5,326 vs 12,788) | **True of the loaded graph, false of the current extraction.** All three vanished in the re-run. The current run holds 36 multi-valued slots, 10 with spread >1%, and **all 10 are thousands-vs-millions rounding**. There is no semantic conflict in the current corpus |
| 5 | An `extraction_run_id` identifies a run | It does not. `extract-v1-lexical-2422c4252c07` names two different sets of bytes. Only `run.complete`'s sha256 distinguishes them, and the graph manifest already records it — nothing reads it back |
| 6 | Vector indexes may be Enterprise-only | `db.index.vector.queryNodes` (mode `READ`) and `db.index.vector.queryRelationships` are present on this Community build *(measured live)*. Availability was never the obstacle; §0.3 is |
| 7 | The ontology's `adjusted_gross_profit` formula constrains margin ordering | It does not reproduce the corpus. `adjusted_gross_margin < gaap_gross_margin` in 16 of 26 quarters, because the v2 expression omits the prior-period cohort term. An ordering-based detector would fire on 62% of quarters and be wrong every time. Filed in §18 |
| 8 | `assertion_type` distinguishes reported from guided facts | All 2,704 observations are `reported`. The ontology's `guidance_issuance.inference_restrictions` demands an `assertion_type` that `AssertionType` does not offer, so the rule is unsatisfiable and untestable. Filed in §18 |

Item 4 is the one that changed this plan's shape. A `fact_conflict` detector was going to lead
the spike; it is now an internal data-quality candidate (§6, D15) because the corpus has no
story of that kind to tell.

---

## 0c. What the adversarial review found

An independent adversarial pass over the first draft, verified against the corpus and the live
server, found **eleven shape-changing defects and fourteen wrong numbers**. They are listed
rather than quietly fixed, because three of them were load-bearing arguments and a reader who
saw only the corrected text would not know which parts of this plan had already been wrong once.

Every one has the same structure: **a correct number taken over one population, then used to
justify a rule that operates on a different one.** That is one reviewable habit, not eleven
mistakes, and correcting it changed no design decision — the deterministic spine, the explicit
fact bindings, the refusal of a repair loop, and shipping §7 first all survived.

| # | The draft claimed | Measured | Fixed in |
| --- | --- | --- | --- |
| 1 | "median cited passage 698 characters … 8 primaries with context ≈ 5,800 tokens — it fits" | 698 is the median over **all 8,776** graph passages. The median passage that **backs an observation** is **2,144.5** characters (n=150). Eight primaries with ±1 context is **~12,900 tokens** — it does not fit the 6,000-token budget, and at p90 it does not fit the server's whole 8,192-token context | §10.2, D3 |
| 2 | §13.7 Rule A steps 3–4 "stop a right number being read off the wrong row" | **24 of 32 distinct `column_label` values map to more than one `period_key`.** Within a single passage, 179 of 485 `(passage_id, column_label)` pairs are ambiguous, covering **1,656 of 2,704 observations (61.3%)**. And 523 quotes occur more than once in their own passage, so "verbatim occurrence" locates nothing | §13.7 |
| 3 | `neo4j` confined to `stages/retrieval/`; the no-write test greps `retrieval/` | §7's freshness gate reads Neo4j. As written, L0 cannot be built without failing L1's own structural test, and the plan's primary read-only guarantee did not cover the first stage | §16, §21 |
| 4 | §10.2 bounds the package | It bounds **7 of 16 sections**. `metrics[]`, `formula_windows[]`, `relationships[]`, `warnings[]`, `conflicts[]`, `compatibility[]` and `retrieval_trace[]` were unbounded, and `compatibility[]` is O(n²) over a 26-quarter series | §10.2 |
| 5 | D9 fires "when a group holds a `seniority_tier` position" | **`seniority_tier` does not exist anywhere in the repository.** `events.yaml:197` declares `allowed_properties: [change_kind, position, effective_date]`. The rule was exactly as unsatisfiable as the ontology defect this plan files at §18 | §6.6 D9 |
| 6 | D4 pair `housing_inventory_homes ↔ homes_sold (20)` | `housing_inventory_homes` is **126/126 instant**; `homes_sold` has **0** instants. The overlap is **zero**, and §6.9 R3 must refuse every pair. "20" was one series' length, not the two series' overlap | §6.6 D4 |
| 7 | `lost_qualifier` is motivated by `pct_>120d` and `contribution_profit` | `contribution_profit` has **no `ambiguities` and `population: None`**. Its cohort caveat lives only in `formulas.yaml` under `adjustment_components[].note`, which §10's `formula_windows[]` did not carry. Half the check's motivation was invisible to it | §10, §13.16 |
| 8 | `story_run_id` identifies a run | It omits `--limit`, `--candidates`, `--detectors`, `--since`, `--until`, `temperature`, `max_tokens`, schema digests and `provider_model_id`. `story run --limit 3` and `--limit 20` mint the same id, and `os.replace` then silently overwrites — **the same defect this plan discovered in `extraction_run_id`** | §14 |
| 9 | §13 covers the draft | Nothing constrained a **numeral-free, non-causal `connective` sentence**. Three false ones pass every check, e.g. *"the only quarter with a negative adjusted gross margin"* — `adjusted_gross_margin` is negative in **2022Q4 (−3.2) and 2023Q1 (−3.3)** | §13.14 |
| 10 | §13.10 B's four conditions license only reported causation | They are co-presence tests, not linkage or polarity tests. **37 passages carry a negated causal construction** (*"not as a result of…"*) and **367 carry two or more distinct markers**, so a sentence can join two terms the passage never joins | §13.10 |
| 11 | "the planner and writer have no tools", answering "a post writer selecting its own evidence" | True, but D3's own recommended fix — give the writer only the passages the plan cites — let a **model filter the next model's universe**. "No tools" and "no influence over the evidence set" are not the same property | §10.2, §12 |

Fourteen wrong numbers, corrected in place: §1.2's two `file:line` references; §1.3's `:Issue`
ratio (6:1 → **1.95:1** against passages), claim-free passages (8,757 → **8,624**) and passage
counts (151/153 → **150/152**); §1.4's RANGE breakdown (26 → **27**); §4's dependency count
(+10 → **7 net new**); §6.3 F1's "largest single move" (**AGP −$446M and CP −$444M are larger**)
and F8's quarter count (6 → **7**); §6.6 D1's `polarity` field (**does not exist**); §6.10's
reversal and delta counts (69/289/16/34 → **84/298/18/35**); §12's collision percentages
(**measured over the corpus, not over a package that does not exist yet**); §13.5's group name;
and §28's offline test count (2,530 → **2,567 here, 2,698 on `main`**).

---

## 0d. What F0 changed under this plan

The factual-spine session landed F0 in fifteen commits (`df50be9..edc2d5a`) while this plan was
being reviewed. This branch was rebased onto it and every load-bearing measurement re-taken.
**Four of this plan's blockers closed, one of its arguments became wrong, and one of its own
§18 findings turned out to be overstated.** No design decision changed.

### Closed by F0

| This plan said | F0 landed | Effect |
| --- | --- | --- |
| The graph is stale; the recorded `run_complete_sha256` no longer matches *(§0.1, §18)* | Extraction rebuilt to `extract-v1-lexical-833f7bcfbce9`, graph to `graph-v1-0483dc6b4b10`, both loaded. Digests match *(verified)* | **Condition repaired.** §7 stays L0 — nothing prevents recurrence |
| `guidance_issuance.inference_restrictions` demands an `assertion_type` `AssertionType` does not offer, so the rule is **unsatisfiable and untestable** *(§18)* | `AssertionType.GUIDED` added, plus `EventTypeDefinition.forbidden_assertion_types: [reported]` and `check_event_assertion_type` | **Closed and now enforced**, not prose |
| No future-period guard exists anywhere; `period_end: 2027-12-31` validates *(§6.8, §18)* | `check_future_period` — carrier-relative, never wall-clock; `GUIDED` exempt unconditionally, `CALCULATED` exempt only for metrics named in `constraints.yaml` | **Closed as a contract.** **But it abstains on the whole current corpus**: `reported_at` is populated by no lane, and the check returns early without a carrier date. A story agent may not assume no observation is future-dated |
| `value: float` cannot hold "4 to 6%", blocking guidance *(§6.5 D10, §6.8)* | The range lives on the **event**, not the observation: `guidance_issuance.properties` gains a typed `property_contract` — `guided_metric`, `low_value`, `high_value`, `unit`, `currency`, with lone-bound, inverted-range, scale-in-string and undeclared-metric all refused | **The schema blocker is gone; the design this plan asked for was rejected in favour of a better one.** D10/D11 remain blocked on the **lane**, which emits no `guidance_issuance` event |
| The multi-header period bug re-dates `market_count` *(implicit in §0b item 4)* | `_labels_repeat` discriminates parallel from sequential column groups on printed labels; 92 of 1,935 table passages disagreed, 87 repeating and 5 distinct | `market_count` 44 moved from `2021-03-31` to `2021-12-31` (and four more, two of them beyond the six reported). The flattened-grid passage is now refused as `PERIOD_NOT_GROUNDED_IN_PASSAGE` |

### Made wrong by F0

**§13.9's justification, not its rule.** This plan argued a `calculated` sentence must carry no
citation because *"there is no computed comparison anywhere in the graph… a passage citation on
a computed number is therefore always a provenance lie."* F0 added `EvidenceKind.CALCULATED` and
`MARKET_DATA`, gave them graph nodes, and added them to `EVIDENCED_BY.allowed_target_types`. A
calculated fact is now **first-class and citable**. The rule survives and is stronger: the
ontology declares `calculated.optional_fields: []` — no filed-passage field is permitted — and
it is enforced in three places. **The prohibition is now an ontology invariant rather than a
story-layer policy**, and the "evidence panel renders the expression and both input observation
ids" design is now the contract's own shape. Rewritten at §13.9.

### A §18 finding of this plan's own that was overstated

§18 filed that the three `HOLDS_POSITION_AT` ids share digest `0365d72eac21`, *"three
semantically distinct relationships share one digest; only the readable segment separates
them."* **The ids are unique** — they differ in the `{source}` segment — and
`duplicate_identities` passes 25,321/0. The digest is a *passage* discriminator by design, and
`relationship_instance_id` was not what commit `3d32c40` fixed. The row is restated at §18 as
what it actually is: a digest that carries no discrimination, which is a readability wart, not
a collision. Recorded rather than deleted, because this plan's §6.11 leans on the same scheme.

### Numbers that moved

| | Before | Now |
| --- | ---: | ---: |
| observations / evidence / issues / rejected | 2,707 / 2,717 / 17,127 / 46 | **2,704 / 2,714 / 17,130 / 49** |
| graph nodes / edges | 28,836 / 35,603 | 28,836 / **35,600** |
| `:Warned` observations | 186 | **185** |
| base labels / live labels / relationship types | 7 / 22 / 12 | **8 / 23 / 12** |
| graph projection version | 1.1.0 | **1.2.0** |
| ontology semantic version / definition hash | 1.0.0 / `e8d4af70…` | **2.0.0** / `bb94f522…` |
| offline tests | 2,567 | **2,723**, 0 skipped |

### What did **not** change, re-verified

537 fact-slots, **36 multi-valued, 10 above 1% spread, all thousands-vs-millions rounding** —
so §0b item 4 stands and there is still no semantic conflict to write about. §13.7.1's column
measurements are **identical to the digit**: 32 distinct `column_label` values, 24 mapping to
more than one `period_key`, 179 of 485 `(passage, column)` pairs ambiguous, covering 1,656 of
2,704 observations, 523 quotes repeated in their own passage. 150 backing passages, median
2,144.5 characters, 77,626 tokens — §10.2.1 stands. Every spike fixture F1–F6 reproduces
exactly, and `adjusted_gross_margin` is still negative in two quarters, which is what grounds
§13.14's superlative attack. `executive_change` still has no seniority field and `inventory` is
still in no `mutually_distinct_group`, so both §18 rows stay open.

---

## 1. What the repository already decides

### 1.1 The graph was stale against its own inputs, and could be again *(verified 2026-08-03)*

**F0 has since rebuilt both runs and the condition is cleared** — `graph-v1-0483dc6b4b10`
records `run_complete_sha256 = 1cc8f7b0…` and the file hashes `1cc8f7b0…`, with 2,704
observations loaded under that id *(verified after the rebase; §0d)*. What follows is the
state that motivated §7, kept because the mechanism that allowed it is unchanged.

| Field | Graph manifest recorded | Extraction directory held |
| --- | --- | --- |
| `extraction_run_id` | `extract-v1-lexical-2422c4252c07` | `extract-v1-lexical-2422c4252c07` |
| `run_complete_sha256` | `7c921bc5028032bd68d4d3a78a8bad28783319c93c1bb67b1e196a2a48131d8d` | `75f47628926324f22a0a0449702cd71dafb96ff0eeebc8ed08cfaf3df8761156` |
| `extraction_created_at` | `2026-08-02T16:02:44+00:00` | `2026-08-03T12:20:47+00:00` |
| `extraction_code_commit` | `4d3ae1e8e2…` | `66a1a0005d…` |
| observations | 2,707 (loaded) | 2,704 |

Read with `json.load` over `data/graph_runs/graph-v1-886059d862ce/manifest.json` and
`hashlib.sha256` over `data/extraction_runs/extract-v1-lexical-2422c4252c07/run.complete`,
both since superseded. The `:GraphLoad` marker reported `node_count: 28836`,
`edge_count: 35603`, `completed_at: 2026-08-03T01:06:24+00:00`; it now reports
`graph-v1-0483dc6b4b10`, `28836 / 35600`, `completed_at: 2026-08-03T16:37:44+00:00`
*(measured live)*.

**The mechanism is untouched.** A run directory can still be regenerated in place, the graph
manifest still records the digest that would reveal it, and nothing still reads that digest
back. F0 repaired an instance; §7 is what prevents the next one.

The graph layer already computes everything needed to detect this — `manifest.py:75-101`
builds `input_content_digest` from exactly these inputs — and never compares them on the way
back in. §7's `package_input_digest_mismatch` is the missing half.

### 1.2 The evidence path is solid; the retrieval layer does not exist *(verified)*

Present and guaranteed:

- `:Passage.text` is the **full** passage text, uncapped (`graph/stages/projection/nodes.py:710`).
- `:Passage.source_url`, `:Document.source_url`, `:Document.accession` are required strings
  (`graph/core/inputs.py:256, 271, 506, 521`), and the URL is denormalised onto evidence,
  passage and document — a citation needs zero joins to reach a URL.
- Passage ids are `{document_id}#p{sequence}`, contiguous `0..n-1` in all 294 documents, so
  previous/next context is string arithmetic on the id (`graph/core/keys.py:163-174`).
- Every `:Observation` and `:Event` has at least one `EVIDENCED_BY` edge, re-checked post-load
  (`graph/stages/load/verification.py:948-956`).

Absent:

- No read protocol. `graph/contracts.py` declares `GraphProjection` (implemented) and
  `GraphStore` (write-only, implemented nowhere).
- No `max_hops`, `max_nodes`, degree cap or `LIMIT` in any code path. The anti-hairball rules
  are prose for query authors (`V1_GRAPH_PROTOTYPE.md:1099-1101`).
- No `graph/queries/*.cypher`. The ten-query pack is specified and does not exist.
- `SubgraphRequest`, `Subgraph`, `EvidencePackage`, `GraphRetriever`, `CitationVerifier` are
  names in a document; zero lines of Python.

### 1.3 The hairball is measured, and one edge type is the whole problem *(verified)*

Exactly one `subject_entity_id` exists in the run, so the `opendoor` `:Entity` has degree
≥ 2,704 before any other edge. `OBSERVATION_OF_SUBJECT` is the edge a bounded retriever must
never traverse. `:Issue` is the largest node population at 17,130 — 1.95× the 8,776 passages
and 6.3× the observations — and **10,852 of them are `NO_STORED_ANSWER`**, a record that a
question was never asked, because the run had `provider_calls_permitted: 0`. **8,624 of the
cited passages are touched by no claim at all.** Any retriever seeded from a passage or an
issue drowns unless it filters on `:NotAttempted`.

The counterweight: **all 2,704 observations are backed by only 150 distinct passages in ~48
documents, 310,507 characters — about 77,600 tokens.** The whole quantitative spine fits in
one large context window. The 8,776 figure is the passage set touched by claims *or* issues;
only **152** are touched by claims. §10.2 depends on the distinction, and the first draft of
this plan got it wrong.

### 1.4 Indexes and edition, measured live 2026-08-03

30 indexes exist: 27 RANGE (7 node key + 12 relationship `edge_key` + 8 query indexes),
2 LOOKUP, **1 FULLTEXT (`passage_text` on `:Passage.text`)**, **0 VECTOR**. 19 constraints,
none touching `accession`.

Query-serving RANGE indexes: `obs_metric`, `obs_period`, `obs_subject`, `obs_lane`,
`evt_type`, `evt_occurred`, `psg_document`, `iss_code`.

**No index exists on** `Document.filing_date`, `Document.form`, `Document.accession`,
`Observation.claim_id`, `Event.announced_on`, or `Passage.passage_kind`. Every tool in §9 that
filters on those does a label scan. At 185 documents and 8,776 passages that is acceptable; it
is recorded so it is not rediscovered as a mystery when the corpus grows.

Edition: `Neo4j Kernel 5.26.28 community`. `db.index.vector.queryNodes` mode `READ` and
`db.index.vector.createNodeIndex` mode `SCHEMA` are both present. `SHOW ROLES` →
`Neo.ClientError.Statement.UnsupportedAdministrationCommand`. `SHOW USERS` → one user `neo4j`
with `roles: null`.

### 1.5 The provider boundary already exists and is nearly right *(verified)*

`extraction/providers/` holds `public.py` (config + a six-class error taxonomy),
`local_openai_compatible.py` (httpx → `POST /v1/chat/completions`, `response_format:
json_schema` with `strict: true`), and `local_openai_compatible_embeddings.py`. The protocols
live in `extraction/contracts.py:122-158`. Defaults: llama.cpp at `127.0.0.1:8080` serving
`Qwen3.5-9B-Q4_K_M.gguf`, embeddings at `:8081` serving `Qwen3-Embedding-0.6B-f16.gguf`
(1024-dim), temperature `0.0`, no API key anywhere.

What is directly reusable, and what is missing, is in §15. The single blocking gap:
`generate(*, prompt: str, …)` collapses to one user turn, so a planner and a verifier cannot
carry different system prompts without hiding the distinction inside the prompt string.

### 1.6 Conventions the plan must not violate *(verified)*

- **argparse only**, `python -m <package> <verb>`, no console_scripts, no short flags,
  kebab-case long flags, `EXIT_OK/EXIT_FAILED/EXIT_USAGE = 0/1/2`, no `--json` flag anywhere.
- **Run ids are content digests with no clock**: `extract-v1-lexical-<digest12>`,
  `graph-v1-<digest12>`.
- **Atomic finalisation**: stage into `<id>.partial/`, write the marker last, `os.replace`.
  A refused run lands in `<id>.rejected/` and never on top of a good one.
- **No stage resumes.** `extraction/core/run_directory.py:97-108` argues against it explicitly.
- **No shared CLI helper module.** `_banner` is triplicated and `EXIT_*` quadruplicated on
  purpose (`extraction/core/config.py:34-43`).
- **`tests/graph/test_graph_package_structure.py:32-41` bans**, transitively, from anywhere
  under `graph/`: `openai`, `anthropic`, `langchain`, `llama_index`, `instructor`,
  `sentence_transformers`, `transformers`, `torch`, `numpy`, `pandas`, `chromadb`,
  `qdrant_client`, `faiss`, `graphiti`, `networkx`. A new package needs the same guard.
- **`CATCH_ALL_NAMES`** forbids `implementation.py`, `service.py`, `helpers.py`, `utils.py`,
  `misc.py`, `common.py`, `base.py`.
- Test names are English sentences; fixtures are real corpus rows, never hand-edited; `live`
  is applied file-wide via `pytestmark` in `*_live.py`.

---

## 2. What "connect the LLM to the graph" means here

Ten capabilities, and only three of them are the model's job.

| # | Capability | Who does it | Why |
| --- | --- | --- | --- |
| 1 | Deterministic graph queries | **Code** | Cypher is owned, parameterised and tested. A generated query is a query nobody reviewed |
| 2 | Passage search (lexical) | **Code** | `db.index.fulltext.queryNodes` with an ontology-derived query. The model supplies terms, never a query string that reaches Lucene unescaped |
| 3 | Bounded graph expansion | **Code** | The bound is the contract. §9 gives per-tool row and hop ceilings |
| 4 | Tool routing | **Model** (research mode only) | Choosing *which* parameterised tool answers a question is a language task. Choosing what it returns is not |
| 5 | Story candidate discovery | **Code** | §6. A model ranking its own story ideas is a model choosing its own evidence |
| 6 | Evidence-package construction | **Code** | §10. The package is the model's entire universe; it must not be able to widen it |
| 7 | Editorial planning | **Model**, schema-constrained | §11. Thesis, structure and emphasis are judgment |
| 8 | Post drafting | **Model**, schema-constrained | §12. Prose is the point |
| 9 | Factual verification | **Code**, with a model second opinion | §13. Deterministic authority over every number, date, identity and citation |
| 10 | Interactive Q&A | **Model** routing, **code** answering | §19 |

The line: **the model chooses words and emphasis; code chooses facts.** Every number, unit,
period, metric identity, entity identity and citation in a published post is a value copied
out of a `StoryEvidencePackage` by an explicit binding that the verifier checks. The model
never types a digit that is not already in the package.

---

## 3. Should the LLM generate Cypher?

Three options, evaluated against the measured environment.

| Option | Verdict |
| --- | --- |
| **A. No generated Cypher — predefined parameterised tools only** | **Adopted for V1, for both post generation and research mode.** |
| **B. Bounded read-only text-to-Cypher for developer research** | Deferred to L12+ behind explicit opt-in. Design recorded in §16.4 so it is not reinvented badly |
| **C. Unrestricted text-to-Cypher** | Out of scope, permanently |

**Why A also covers research mode, which the brief expected to be B.** Community edition
gives no read-only user (§1.4), so the only barrier between a generated query and a write is
application code. `neo4j-graphrag`'s `Text2CypherRetriever` does run `EXPLAIN` and refuses any
plan whose `query_type != "r"` — that is a real guarantee and the right pattern — but it bounds
neither cost nor rows, and a read-only cartesian product over a node with degree 2,704 will
run to memory exhaustion without writing a byte. Given that §9's fifteen tools already answer
the developer questions this corpus can support, B buys a marginal capability at the cost of
the one property that makes the system trustworthy.

**What B would require if adopted later** (§16.4): explicit `--allow-generated-cypher`,
`EXPLAIN` before execution with `summary.query_type == "r"` enforced, a keyword and procedure
blocklist as defence in depth, `neo4j.Query(text, timeout=…)` on every statement, a hard
`LIMIT` injected into the returned plan, the generated query printed before it runs, and a
hard prohibition on the path being reachable from `story run`. It must never be a fallback
when a tool fails — a failed tool is an answer.

---

## 4. Framework choice

Compared against current primary sources on 2026-08-03 and against this repository's rules.

| | Fits an existing graph | Cypher control | Vector + hybrid | Attribution | Local model | Core deps | Free-form risk |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Driver + custom tool layer** | Perfect | Total | DIY | Total | Already works | **0 new** | None |
| `neo4j-graphrag` 1.18.0 | Good — no schema assumptions | Full via `retrieval_query` | Both, with score fusion | DIY | Yes, no SDK needed | 10 core, **7 net new**, incl. `numpy`, `scipy`, `pypdf` | Low |
| LlamaIndex property graph | Poor | Good via `CypherTemplateRetriever` | Vector only, on its own index | Weak | Yes | ~29 | Medium |
| Microsoft GraphRAG | **No Neo4j support at all** | N/A | Its own store | Rebuilt by LLM | LiteLLM | 25 + 7 siblings + `azure-*` | N/A |
| Graphiti | **No** — hard-coded labels | None | Its own indices | Its own model | Yes | 7, incl. hard `openai` + `posthog` | **High at construction** |

**Recommendation: the official `neo4j` driver (already pinned, `neo4j>=6.2,<7`) plus a
code-owned tool layer. No new dependency.**

The decisive argument is not preference, it is precedent. `plans/extraction/STAGE_09_HYBRID_SCOPING.md`
§1 already fought and settled this exact trade for the embedding stack: *"Rejected the Python
stack: three gigabytes of new dependency … The repository's dependency set stays `httpx`,
`pydantic`, `PyYAML`. `numpy` is present in the environment but is **not** a declared
dependency and must not become one."* `neo4j-graphrag`'s core install declares
`numpy>=2.0,<3.0` and `scipy>=1.13,<2.0`. Adopting it reverses a written, argued decision in
order to obtain roughly forty lines of Cypher.

Those forty lines are worth borrowing as **code**. graphrag's hybrid query shape — a
`CALL { … UNION … }` over `db.index.vector.queryNodes` and `db.index.fulltext.queryNodes`,
with each arm min-max normalised by its own maximum before fusion — is the correct shape and
should be reimplemented in `story/stages/retrieval/` with attribution in a comment when §8's
embeddings land. Lucene scores are unbounded and not comparable to the vector score's `[0,1]`;
that is exactly why the normalisation exists and why naive fusion is wrong.

Two graphrag behaviours are worth recording as traps to avoid in our own code:

- With neither `return_properties` nor `retrieval_query`, its retrievers `RETURN node { .* }` —
  every property of the matched node. Against `:Passage`, that is the full text plus source
  URL plus eight provenance properties per hit. **Every tool in §9 returns a named field list.**
- It passes bare query strings to `execute_query`, so no statement carries a timeout. **Every
  statement in §9 is wrapped in `neo4j.Query(text, timeout=…)`.**

And one correctness trap that applies to us directly: graphrag does no Lucene escaping and
catches `ParseException` after the fact. Financial surfaces are exactly the strings that break
Lucene — `:` in `norm:0001801169:…`, `/` in `8-K/A`, `-` in date ranges, `"` in row labels.
**§9's `search_passages` escapes before calling and has a fixture for each of those four.**

---

## 5. Package shape and the model's universe

```text
story/
  contracts.py              StoryDetector, EvidenceBuilder, GenerationProvider protocols;
                            typing + story.core.models only, nothing else
  core/
    models.py               StoryCandidate, StoryEvidencePackage, EditorialPlan, Draft,
                            VerifiedDraft, RejectedDraft, DraftFinding
    keys.py                 candidate_id, package_id, story_run_id — deterministic, readable
    manifest.py             StoryRunManifest; the only clock in the package
    series.py               canonical series + comparability (§6.1, §6.9)
    numerals.py             printed-form ⇄ canonical-value reconstruction (§13.1)
    periods.py              the closed period-surface grammar (§13.4)
  stages/
    freshness/              the staleness gate (§7)                    — reads Neo4j + manifests
    retrieval/              the fifteen tools (§9)   ← owns every Cypher string; imports no driver
    detection/              the seven detectors (§6)
    ranking/                deterministic scoring and dedup (§6.10)
    packaging/              StoryEvidencePackage builder (§10)
    generation/             planner + writer (§11, §12)                — the only model callers
    verification/           deterministic gate + model adjudicator (§13)
  providers/                public.py, local_openai_compatible.py      — the only HTTP client
                            neo4j_connection.py    ← the only module that imports the driver
  context.py  pipeline.py  cli.py  __main__.py
config/story.yaml
tests/story/
plans/llm-agent/
```

**Judgment on proportion.** Measured across the repository, eight stage directories is
**house-standard, not an outlier**:

| package | stage dirs | `.py` files | lines |
| --- | --- | --- | --- |
| `graph/` | 2 | 26 | 9,172 |
| `acquisition/` | 6 | 38 | 4,375 |
| `normalization/` | 7 | 45 | 5,191 |
| `extraction/` | **8** | 66 | 13,513 |

The first draft compared only against `graph/` — the smallest package in the tree — and then
wrote a defensive paragraph. Eight is exactly `extraction/`'s count and above the mean of 5.75.
The real proportion question is §23's fourteen implementation stages against a package with
zero lines today, and §23 answers it: L0–L6 and L9 need no model server, and L0 alone
(the staleness gate) has standalone value.

**Correction, 2026-08-03 (implementation, decision D1).** This tree originally put the driver
import in `retrieval/`. It is in **`story/providers/neo4j_connection.py`**: `core/` must stay
free of Neo4j and of environment concerns, `providers/` is already the boundary where an
external system is reached, and retrieval receives a `ReadQueryExecutor` by injection so no
tool constructs a driver. Retrieval still owns every Cypher string — it just does not own the
connection.

Each directory is still the boundary of a different failure. `freshness` refuses a
run; `providers` is the only place a driver or an HTTP client is imported; `retrieval` owns
every Cypher string and receives its executor injected; `detection` and `ranking` are separate
because §6.10 forbids a model from ranking and keeping them together invites one function to do
both; `packaging` is the wall the model cannot see past; `generation` is the only place a
prompt exists; `verification` must be constructible without either a database or a model, so a
report can be rebuilt from a stored artifact. Collapsing `detection` into `ranking` or
`packaging` into `retrieval` would each merge a "code decides" boundary with a "model consumes"
one. `core/numerals.py` and `core/periods.py` are ~150 lines each and are separate from
`models.py` because they are the two places a rounding or a grammar bug becomes a wrong
published number.

**No new dependency.** `neo4j` 6.2.0, `httpx`, `pydantic` and `PyYAML` are already pinned and
are all that is needed. `numpy` is not, and must not become one (§4).

---

## 6. Story discovery

```text
canonical series (§6.1)
  → detectors (§6.2–§6.8)      → StoryCandidate
    → ranking and dedup (§6.10)  → ranked candidates
      → evidence expansion (§10)   → StoryEvidencePackage
```

### 6.1 The canonical-value policy — `canon-policy:1.0.0`

Observations are grouped into fact-slots keyed `(metric_id, period_key)`, where `period_key` is
**the graph's own `PeriodRef.key`** — `2022Q2`, `FY2021`, `2023-12-31`, or `{start}_{end}` only
where none of those apply. *(Correction 2026-08-04, S2: this said the key is `instant_date` or
`f"{start}_{end}"`. It is bijective with the real key over this corpus — 74 distinct values
either way, so no census moves — but the strings differ and so does every id built from them.
`story/core/periods.py` imports `PeriodRef` rather than restating the rule, the call
`graph/core/derivation.py:67-79` makes for the reason it gives.)* **537 slots exist over 2,704 observations;
36 hold more than one distinct value** *(verified 2026-08-03)*.

1. **Quarantine flattened-table narrative reads.** A `source_lane == "normalized_narrative"`
   observation whose *(corrected 2026-08-04, S2: this said `lane == "narrative"`; the stored
   value is `SourceLane.NORMALIZED_NARRATIVE`, on 14 of 2,704 rows. A guard written to the
   plan's spelling would read as a rule that simply never fires — which is exactly how the
   §7 contents check stayed invisible)*
   evidence `quoted_text`, after masking `Month DD, YYYY` literals, contains ≥3 consecutive
   numeric tokens separated only by non-alphabetic characters is quarantined: kept in the
   graph, excluded from the series, and emitted as a `lane_defect` warning on any candidate
   whose slot it touches. The date mask is load-bearing — without it *"As of December 31,
   2023, 18% of our homes…"* is falsely quarantined. *(In the 2026-08-02 snapshot this
   quarantined exactly 3 rows, all from `…q42023formxex992sharehol.htm#p20`; the current run
   already drops them, so the rule fires on nothing today and exists as a guard.)*
2. **Cluster by presentation tolerance.** `tol = 1e6` (USD printed in millions), `1e3` (USD
   in thousands), `0.1` (percent), `1` (counts). Values within `tol` are one reading. **This
   collapses all 36 multi-valued slots to a single cluster on the current run.**
3. One cluster → `ok`.
4. More than one cluster → **document-support majority**: distinct `document_id` count per
   cluster; top cluster wins if it has ≥2 documents and ≥2× the runner-up, status
   `resolved_by_majority`; otherwise status `conflict` and **the slot emits no series value**.
5. **Representative observation** — sort by `(scale_precision, filing_date, observation_id)`,
   precision ordered `thousands|units < millions < None`, earliest filing wins ties. That is
   the reading the market saw first, and the one whose text a post will quote.
6. Every canonical point carries `supporting_observation_ids`, `minority_observation_ids`,
   `quarantined_observation_ids`, `n_docs`, `first_filed`, `last_filed`.

**Measured on the current run: 537 slots → 537 `ok`, 0 `resolved_by_majority`, 0 `conflict`.**
Rejected alternatives: *prefer most recent filing* (loses to earliest — the majority rule
already handles restatement and the earliest filing is the text the post quotes); *prefer the
table lane* (would discard the legitimate narrative reading of `adjusted_ebitda 2022Q2`);
*refuse every multi-valued slot* (would delete 36 slots including the whole 2022 crisis).

### 6.2 The canonical quarterly series *(verified 2026-08-03, current run)*

```
              19Q4 20Q1 20Q2 20Q3 20Q4 21Q1 21Q2 21Q3 21Q4 22Q1 22Q2 22Q3 22Q4 23Q1 23Q2 23Q3 23Q4 24Q1 24Q2 24Q3 24Q4 25Q1 25Q2 25Q3 25Q4 26Q1
adj_ebitda $M   —  -28  -22  -21  -27   -2   26   35    0  176  218 -211 -351 -341 -168  -49  -69  -50   -5  -38  -49  -30   23  -33  -43  -31
gaap_gm %     5.9  7.3  7.4 10.6 15.4 13.0 13.4  8.9  7.1 10.4 11.6 -12.6 2.5  5.4  7.5  9.8  8.3  9.7  8.5  7.6  7.8  8.6  8.2  7.2  7.7 10.0
adj_gm %      5.6  7.1  6.9  9.8 15.4 13.0 13.5 10.3  7.3  9.9 13.2  3.3 -3.2 -3.3  0.4  8.6  7.6  8.8 10.2  7.2  6.9  8.7  8.6  7.0  6.1  9.3
contrib_m %   1.5  3.1  2.7  5.9 12.6 10.2 10.8  7.5  4.0  6.4 10.1 -0.7 -7.2 -7.7 -4.6  4.4  3.4  4.8  6.3  3.8  3.5  4.7  4.4  2.2  1.0  4.4
```

`adjusted_ebitda` covers 25 quarters (`2019Q4` absent); the three margin series cover a gapless
26 quarters `2019Q4 → 2026Q1`. `homes_purchased` covers 15 quarters from `2022Q3`.
`housing_inventory_homes` covers 23 instants; `pct_homes_on_market_gt_120_days` covers 15.

### 6.3 Spike fixtures — real candidates, verified on the current run

| # | Story | Numbers *(verified 2026-08-03)* |
| --- | --- | --- |
| **F1** | Adjusted EBITDA sign reversal | `2022Q2 = +$218M` → `2022Q3 = −$211M`; Δ = **−$429M**. The largest move that **crosses zero**; `adjusted_gross_profit` (−$446M) and `contribution_profit` (−$444M) are larger in absolute terms in the same quarter, and all three are one event |
| **F2** | GAAP gross margin goes negative | `2022Q2 = 11.6%` → `2022Q3 = −12.6%`, **−24.2 pp**. The only negative GAAP gross margin in 26 quarters |
| **F3** | Adjusted-vs-GAAP margin wedge | `2022Q3`: AGM `3.3%` vs GGM `−12.6%`, gap **+15.9 pp**. **z = +3.95 over a 25-quarter population** *(corrected 2026-08-04, S3-C)*. The raw 26-quarter figure is z = 4.02 and the pooled all-shapes figure is 4.38 — see §6.6 D4 for why 25 and why one shape |
| **F4** | Inventory drawdown | `2022-09-30 = 16,873` homes → `2023-06-30 = 3,558`, **−78.9% in three quarters** |
| **F5** | Aging inventory doubles | `pct_>120d`: `2024-09-30 = 23%` → `2024-12-31 = 46%`, **+23 pp**, then 27 → 36 → 51 through `2025-09-30` |
| **F6** | Acquisition pullback | `homes_purchased`: `2025Q1 = 3,609` → `2025Q3 = 1,169`, **−67.6% in two quarters** |
| **F7** | Leadership reset | 2025-09-10, one passage: Kaz Nejatian → CEO, Keith Rabois → Chairman, Eric Wu → board. `occurred_on` is **null** on all three |
| **F8** | Contribution-profit identity closes | `contribution_profit = adjusted_gross_profit + direct_selling_costs + holding_costs` residual is **exactly $0** in all **7** quarters where `holding_costs` is reported (2024Q1–Q3, 2025Q1–Q3, 2026Q1). **Caveat:** this is the sign-flipped, three-term form of `formulas.yaml`'s declared **four**-term expression, which separates current-period from prior-period holding costs. D17 must check the ontology's identity, not this one — see §6.6 |

**F1–F3 are the recommended spike.** They share an anchor quarter, exercise a sign flip, a
percentage-point move and a cross-metric divergence, and every fact resolves to a passage
quoting it. F8 is the determinism check for §6.1, not a story.

**A fixture the brief expected and the data does not support:** a `market_count` restatement
(27 vs 44 at `2021-03-31`). It exists only in the stale loaded graph.

### 6.4 `StoryCandidate` contract

```python
@dataclass(frozen=True)
class StoryCandidate:
    candidate_id: str            # §6.11
    detector_id: str             # "detector:metric_move"
    detector_version: str        # "1.0.0"
    policy_version: str          # "canon-policy:1.0.0"
    graph_run_id: str
    subject_entity_id: str       # "opendoor" — the only value today
    story_type: str              # closed enum, one per detector
    metric_ids: tuple[str, ...]  # sorted
    event_ids: tuple[str, ...]   # sorted
    anchor_period_keys: tuple[str, ...]
    anchor_observation_ids: tuple[str, ...]   # sorted; the digest inputs
    signals: Mapping[str, float | int | str | bool]
    warnings: tuple[str, ...]
    evidence_request: EvidenceRequest         # what §10 must fetch
    audience: str                             # "external" | "internal"
```

**`StoryCandidate` carries no prose.** No `thesis_hypothesis` field, deliberately: the brief
asked for one and it is the seam through which a detector's guess becomes a post's claim. The
detector asserts what moved, by how much, and which passages back it; the thesis is §11's
output and is derived from the *package*, not from the candidate. If a hypothesis is wanted for
triage, it belongs on the ranked-candidate row as `detector_headline` — a rendered template
string, not free text, and never passed to the writer.

`materiality`, `novelty`, `evidence_quality`, `ambiguity_penalty` and `repetition_penalty` are
**not** candidate fields either. They are ranking outputs (§6.10), computed over the whole
candidate set. A candidate that carried its own score would let a detector rank itself.

### 6.5 Detector status

| # | Detector | Status | Blocker |
| --- | --- | --- | --- |
| D1 | `metric_move` | **NOW** | — |
| D2 | `trend_reversal` | **NOW** | — |
| D3 | `acceleration` | **NOW** | — |
| D4 | `cross_metric_divergence` (z-score form) | **NOW** | ordering form is invalid — §18 |
| D5 | `event_metric_proximity` | PARTIAL | 6 events over 8 years |
| D6 | `repeated_related_events` | PARTIAL | one real pair |
| D7 | `financing_liquidity` | BLOCKED | 1 `borrowing_capacity` point; a billions-scale lane bug; no facility events |
| D8 | `inventory_risk` | **NOW** | — |
| D9 | `leadership_change` | **NOW** (exactly one candidate) | — |
| D10 | `guidance_revision` | BLOCKED — **on the lane only, as of F0** | No lane emits a `guidance_issuance` event. The *schema* blocker is gone: the range is typed on the event as `low_value`/`high_value`/`unit`/`currency`/`guided_metric` |
| D11 | `guidance_vs_actual` | BLOCKED | D10, and the guided quantities are mostly SBC, which is not one of the 26 declared metrics — `guided_metric` must name a declared `metric_definition` |
| D12 | `stock_reaction` | BLOCKED | no price source anywhere in the repository |
| D13 | `management_language_shift` | PARTIAL | text exists in `passages.jsonl`; no lexical projection |
| D14 | `entity_network` | BLOCKED | 6 entities, 4 edges, one hub |
| D15 | `fact_conflict` | **NOW**, `audience: internal` | — |
| D16 | `coverage_gap` | **NOW**, `audience: internal` | — |
| D17 | `formula_closure_break` | **NOW**, a guard not a story | — |

**V1 implements D1, D2, D3, D4, D8, D9, D15, D16, D17.** D5 and D6 are implemented at L3 with
their thin-data warnings mandatory. D7, D10–D12, D14 are **not implemented** — a detector with
no supporting rows is a promise, not a feature.

### 6.6 Detector specifications — the seven that ship

**D1 `metric_move`.** Inputs: two canonical Points of one metric, comparable under §6.9.
```
Δ    = v1 − v0
Δpct = Δ / |v0| × 100      only if |v0| ≥ floor(metric), else null
Δpp  = Δ                   when unit == percent
fire if (unit == percent and |Δpp| ≥ pp_min) or (|Δpct| ≥ pct_min or |Δ| ≥ abs_min)
```
Thresholds are each metric family's own p75 of |QoQ|, not a global constant:

| family | `pct_min` / `pp_min` | `abs_min` | p50 / p75 / p90 of \|QoQ\| |
| --- | --- | --- | --- |
| USD profit measures | 65% | $50M | AGP 42/65/154%; CP 63/105/148%; AEBITDA 51/99/660% |
| the four margins | 3.0 pp | — | AGM 2.4/3.2/6.5; CM 2.4/3.4/6.7; GGM 1.5/2.9/4.8; AEM 2.5/4.1/6.0 |
| `pct_>120d` | 19 pp | — | 12/19/23 — genuinely ten times noisier than the margins |
| home counts | 40% | 1,000 homes | homes_sold 22/40/46%; homes_purchased 33/49/53% |
| `market_count` | 6% | 3 markets | 0/5.7/13.3 — the median move is literally zero |

`floor(metric) = 0.10 × median(|value|)` over the metric's own history. Without it,
`adjusted_ebitda 2021Q4 → 2022Q1` reads `$0.4M → $176M = +43,900%` and the top five candidates
by percentage are all division by noise.

*False positives in this corpus:* the small-base cases above, and **sign convention** —
`direct_selling_costs` values are negative, so "costs rose" is a *decrease*. **`metrics.yaml`
has no `polarity` key** *(verified — the first draft invented one)*; the nearest declared field
is `metric_category`. So the polarity table is **story-owned**, declared in `config/story.yaml`
as an explicit `metric_id → revenue|cost|ratio|count` map with a test asserting it covers all
26 metrics. The writer is never allowed to infer direction from the sign.

**D2 `trend_reversal`.** Fire at `i` when `min_run` prior deltas all oppose `sign(d[i])` and
`|d[i]| ≥ max(D1 threshold, 1.0 × σ(d))`. At `min_run = 2` with no magnitude gate the corpus
yields **84 reversals across 13 metrics** — only 13 metrics have ≥3 consecutive deltas at all —
because a noisy series alternates. **That figure does not reproduce** *(2026-08-04)* under any
zero-handling or shape grouping tried — closest measured is 76/12 over adjacent quarterly rows
ignoring comparability, and 71/11 honouring R1–R10. Recorded rather than fitted to; the live
census under the shipped rule is **11 candidates across 9 metrics**.

The σ gate reduces it to the ones worth writing. Exclude any metric whose `σ(d)` is under one
presentation unit — *(corrected 2026-08-04: this rule **excludes nothing**, and its stated
reason is wrong about the data. `market_count` does not "alternate ±0"; it steps up through
2021–22 and then goes flat, σ = 3.26 raw and 4.53 after R1–R10 against a one-market tolerance,
and no metric at any shape has σ below its presentation unit. Implemented as written and fires
on nothing; what actually removes `market_count` is R8 plus R10 plus the 8-delta population
floor.)* **Zero-delta handling must be specified, not left to the implementation**: a delta
of exactly 0 has no sign and breaks a run rather than continuing or reversing it.

**D3 `acceleration`.** Three consecutive same-sign deltas with monotone **increasing** `|d|` and
`|d[i]| ≥ 1.5 × |d[i−2]|`. Under that literal spec the corpus yields **7 firings across 5 metrics**
*(measured 2026-08-04 at R3; it was 8 across 6 until D9 was fixed — one window,
`gaap_gross_margin 2020Q1–2020Q4`, existed only because R8 compared raw float subtraction and
so failed to refuse a one-printed-unit opening step. Over all shapes R3 admits: 9 across 6)*, and — a correction — **none of them is a deceleration**, because the monotone-increasing
condition cannot fire on one. The first draft's dedup example (*"the 2023Q1 decelerations of
AGP, CP, CM and AGM are the same event four times"*) describes candidates this detector cannot
produce. **Decision: implement acceleration only in V1.** A deceleration detector needs its own
rule (monotone *decreasing* `|d|`, same sign) and its own threshold, and it is where the
correlated-lineage dedup would actually be needed — collapse on shared anchor quarter plus
`formulas.yaml` `component_metrics` lineage, with `correlated_metric_ids[]`.

**D4 `cross_metric_divergence`.** `gap[t] = v_a[t] − v_b[t]`; `z[t] = (gap[t] − μ)/σ` over the
pair's own history; fire at `|z| ≥ 1.5`. **Do not build the ordering form.**

**Two qualifications this section did not state, both measured at S3-C:**

1. **The population must be one `PeriodShape`.** Pooled over all four shapes the AGM↔GGM pair
   has 43 comparable periods and 2022Q3 scores **4.38** — a quarter's wedge measured against six
   fiscal years and twelve YTD windows. Each shape is scored against its own population, with a
   floor of 8 points (§6.10's "below ~8 points `magnitude_z` is not meaningful"). Live, only
   `quarter` ever clears it.
2. **A variance floor of `2 × tol`**, or a flat gap disturbed by one 0.1 pp rounding step scores
   4.9. Refuses nothing live; driven synthetically both ways.

Pairs, with **overlapping-quarter counts raw → after R1–R10**: AGM↔GGM **26 → 25**,
CM↔AGM 26 → 25, CP↔AGP 26 → 25, CPAI↔CP 11 → 10, `homes_purchased`↔`homes_sold` 15 → 15.

**Why 25 and not 26** *(corrected 2026-08-04)*: **all seven versioned metrics declare
`valid_from: 2020-01-01`** *(verified — `adjusted_gross_margin` and `gaap_gross_margin` both
resolve to `None` at `2019-12-31`)*, so `2019Q4` lies in no declared window for any of them and
`comparable` refuses it as `FORMULA_VERSION_UNDECLARED`. §6.9's P6 correction named the three
pre-2020 slots of `adjusted_gross_profit` alone; **the same boundary applies to all seven.**
The plan's own arithmetic reproduces exactly over 26 points — the difference is the rule being
honoured, not the sum.

**R6 is a rule about two points; a z-score is a claim about a distribution.** Applied pointwise
a cross-metric pair never trips R6 at all, since both sides sit on one date — yet a CP↔AGP mean
spanning 2020Q1–2026Q1 averages **two definitions of its own denominator** across AGP's v1/v2
boundary. So a period enters an anchor's population only when, for each metric, its point is
`comparable` with that metric's point *at the anchor*. Live this splits CP↔AGP into a v1
population of 8 and a v2 population of 17, each carrying
`divergence_population_excludes_periods`. Refusing the whole pair was rejected: it deletes a
shipping pair, and the set `comparable` licenses is already the right one.
Require `σ(gap) ≥ 2 × tol`, or a 0.1 pp rounding difference produces an enormous z.
The output must state the definitional relation, not just the numbers.

**`housing_inventory_homes ↔ homes_sold` is removed from the pair list** *(verified live at
S3-C: the two share **not one period key**, so the pair yields neither a candidate nor even an
R3 refusal)*.
`housing_inventory_homes` is **126/126 `instant`**; `homes_sold` has **zero** instants. The
overlap is zero and §6.9 R3 refuses every pair. The first draft's "(20)" was `homes_sold`'s own
quarterly count, not the pair's overlap. An inventory-versus-sales relationship is real and
belongs to D8's `sell_through` ratio, which pairs an instant with the *following* duration
deliberately and labels the result `derived: true`.

**A comparability gap R1–R8 permits and should not.** R2 licenses any two metrics sharing a
`mutually_distinct_group`, and `profit_measures` contains `contribution_profit` — so the
shipping `CP↔AGP` pair compares a **cohort** measure against a **period** measure. `formulas.yaml`
records the reason: contribution profit subtracts *"holding costs incurred in prior periods on
homes sold in the period"*, so *"a Contribution Profit value is NOT a slice of any single
period's expenses."* §6.9 gains **R9** and §10.1 gains a mandatory `cohort_vs_period_basis`
warning.

**D8 `inventory_risk`.** Four independent sub-signals; severity is the count that fire.
```
S1 aging     Δpp(pct_>120d) ≥ 19
S2 level     pct_>120d ≥ 40
S3 turnover  sell_through[t] = homes_sold[t] / housing_inventory_homes[t−1]; fire on ≤ −25% rel
S4 cover     cover[t] = homes_purchased[t] / homes_sold[t]; fire on crossing 1.0 or |Δ| ≥ 0.30
```
Real hits: `2022Q4` (S2 55%, S4 cover 0.46 from 0.98), `2023Q1` (S2 59%), `2024Q4` (S1 +23 pp,
S2 46%), `2025Q2` (S4 cover 0.41 from 1.23 — the sharpest inversion), `2025Q3` (S2 51%, S4 0.46).

**Two corrections to the sub-signals as first drafted.** S4 fires **~9 times** across the 15
overlapping quarters, not the 3 the draft implied — the `|Δcover| ≥ 0.30` arm is far looser
than the crossing arm and needs either a higher threshold or removal, and the crossing arm
alone is the one carrying the meaning. And **S1 has no adjacency requirement**: `pct_>120d` has
a four-quarter hole between `2021-12-31` (8%) and `2022-12-31` (55%), so S1 reads a **+47 pp**
"aging jump" that is a year of change. Every delta-based sub-signal must require its two points
to be **consecutive in the canonical series**, not merely adjacent in the sparse one — a rule
that belongs in §6.9 and applies to D1, D2 and D3 equally.
**S3 and S4 are derived ratios, not observations** — labelled `derived: true` with their two
source Points, never rendered as if a filing printed them. The corpus itself refuses printed
derivations: 174 `DERIVED_CHANGE_COLUMN` + 12 `DERIVED_COMPARISON` issues. Exclude
`homes_under_contract` from the composite: 7 points, three interior gaps, every one
single-document. Every `pct_>120d` observation carries `ambiguity_codes:
["pct_120_days_denominator"]` and the candidate must surface it.

**D9 `leadership_change`.** Group `executive_change` events by `(passage_id, announced_on ??
occurred_on)`; fire when a group holds a senior position. One candidate exists.

**Correction: there is no `seniority_tier` field.** The first draft's firing rule named one and
`grep -rn seniority ontology/ extraction/ graph/` returns nothing; `events.yaml:197` declares
`allowed_properties: [change_kind, position, effective_date]`, and the three real events carry
only `position` as free text (`"Chief Executive Officer"`, `"Chairman"`, `"member of the Board
of Directors"`). That made D9's rule exactly as unsatisfiable as the `guidance_issuance` defect
this plan files at §18 — a rule naming a field that does not exist.

So seniority is **story-owned**: a `config/story.yaml` lexicon of normalised position surfaces
(`chief executive officer`, `chief financial officer`, `chairman`, `president`, `board of
directors`, …) matched against `position` after `normalize_alias`, with an unmatched position
producing a candidate carrying `position_unrecognised` rather than being dropped. Proposing it
to the ontology as a real `seniority_tier` on `executive_change` is a §18 item, not a
prerequisite.

Mandatory warnings: `occurred_on` is null on all three (**no effective date is knowable**),
`date_basis: announced`, and `predecessor_unknown` — no departure event exists, so the
candidate cannot say who was replaced.

**D15 `fact_conflict`** and **D16 `coverage_gap`** ship as `audience: internal`. D15 classifies
each multi-valued slot as `presentation_rounding` (36 today), `lane_defect_flattened_table`,
`year_only_column_ambiguity`, or `genuine_restatement` (**0 today**). D16 reports stale metrics
(`contribution_profit_after_interest` last reported `2022Q2`, 15 quarters ago, after 11
continuous quarters), interior holes, and per-empty-metric refusal attribution from
`issues.jsonl` — `revenue` is 100% `DEFERRED_REQUIRED_SOURCE_LANE`, and it is the denominator
of four populated margin metrics.

**D17 `formula_closure_break`** is a guard: any non-zero residual where all components are
present **blocks** the affected candidates rather than producing one. It evaluates
`formulas.yaml`'s **declared** expression — for `contribution_profit_v1` that is the four-term
cohort form separating current-period from prior-period holding costs — and not F8's collapsed
three-term convenience identity. §13.9 requires a `formula_version_id` that
`check_formula_for_date` accepts, so a guard checking a different identity than the ontology
declares would certify an arithmetic the verifier then rejects.

### 6.7 Event proximity may never imply causation, structurally

D5's output schema has `relation` as a closed enum with exactly one member,
`"temporal_proximity"`; signed `quarter_offset` and `days_between` fields; **no** `explains`,
`driver`, `impact`, `cause` or `because` field anywhere; a literal constant
`causal_claim_supported: false`; and two mandatory fields — `co_occurring_events[]` and
`co_occurring_moves[]` — listing *every* other event and metric move in the window. For
`2022Q4` that is both the credit facility and the workforce reduction alongside eight metric
moves. **A candidate that names one event and one metric while seven others moved is the
causal-attribution failure; making the alternatives a required field makes it visible.**

The prompt is not where this is enforced. §13.10 refuses the causal wording independently.

### 6.8 Detectors blocked by future data — the extension points

| Detector | Attaches to | Unblocked by |
| --- | --- | --- |
| D10, D11 guidance | `AssertionType.GUIDED` on the observation; `guidance_issuance` events carrying `guided_metric`, `low_value`, `high_value`, `unit`, `currency` | **A guidance lane, and only that.** F0 closed the contract half: the assertion type exists, `forbidden_assertion_types: [reported]` is enforced, and the range is typed and validated (lone bound, inverted range and scale-in-string all refused). 75 forward-looking quantified passages exist and are still refused. **The observation schema was never the right home for a range and this plan asked for the wrong thing** — a guided range is a property of the guidance event, not of an observation |
| D12 stock reaction | a `market_price` metric, `subject_types: [listed_security]` | A price acquisition stage. 276 passages match price language and **all** are SPAC/warrant boilerplate |
| D7 financing | a `borrowing_capacity` series | A billions-scale fix in the narrative lane (`"$12.6 billion"` was rejected as `VALUE_CONTRADICTS_QUOTED_TEXT`), a `population_definition` for aggregate/committed/drawn, and facility events |
| D14 entity network | ≥2 subjects | Entity resolution, deliberately deferred |
| peer / market divergence | a second `subject_entity_id` | Any peer corpus. `mortgage_rate` and `home_price_appreciation` are declared and empty |

Each detector module declares `REQUIRED_FACTS` as a tuple of predicates over the package; a
detector whose requirements are unmet is **skipped with a recorded reason**, never silently
absent. That is the mechanism by which the factual-spine work turns detectors on without this
plan being rewritten.

### 6.9 Period comparability — `comparable(A, B) → Ok | Refuse(reason)`

```
R1 SUBJECT   A.subject_entity_id == B.subject_entity_id
R2 METRIC    same metric_id; or, for a declared cross-metric intent, both metrics appear
             together in a formulas.yaml component_metrics list OR share a
             mutually_distinct_groups group. Different groups → Refuse(UNRELATED_METRICS).
             Note the direction: shared group membership makes two metrics comparable as a
             DIVERGENCE pair precisely because the ontology says they must never be merged.
R3 SHAPE     shape(A) == shape(B), derived structurally, not parsed:
               instant      instant_date is not null
               quarter      start.day == 1, start.month in {1,4,7,10}, end.month == start.month+2
               fiscal_year  Jan-01 .. Dec-31, same year
               ytd_6m/9m    Jan-01 .. Jun-30 / Sep-30
               other        everything else → Refuse(UNCOMPARABLE_SHAPE)
             2020-01-01..2020-03-31 is both a quarter and a YTD-3M; classify as quarter.
             The 3 cross-year windows (2022-04-01..2022-12-31, 2022-07-01..2023-03-31,
             2022-10-01..2023-06-30; 6 rows) are excluded from every comparison.
R4 UNIT      A.unit == B.unit. SCALE IS NOT CHECKED — value is already scale-applied.
R5 CURRENCY  A.currency == B.currency (both null is agreement).
R6 FORMULA   PER METRIC, resolve_version(metric, date) must agree across BOTH dates.
             Resolution is BY OBSERVATION DATE. A duration straddling a boundary ->
             Refuse(FORMULA_VERSION_STRADDLES). An instant resolves on its own
             instant_date and cannot straddle.
             A period in NO declared window -> Refuse(FORMULA_VERSION_UNDECLARED).
             ** "per metric" is a correction, 2026-08-04 (S2, measured). ** Written as
             "resolve_version(metric, period_end) for each side must agree", R6 compares
             two different metrics' version ids on a cross-metric pair -- and
             adjusted_gross_margin_v1 can never equal gaap_gross_margin_v1. Literally
             applied it refuses EVERY cross-metric comparison, including this plan's own
             recommended spike F3 and the shipping CP<->AGP divergence pair. Evaluated
             per metric across both dates it is identical on every same-metric pair, so
             nothing else moves.
R7 CANONICAL both sides in {ok, resolved_by_majority}. A conflict slot has no value.
R8 TOLERANCE for a MOVEMENT claim, |A − B| must exceed max(tol(A), tol(B)), else
             Refuse(WITHIN_PRESENTATION_TOLERANCE) — the two filings just rounded differently.
R9 BASIS     a cohort measure may not be differenced against a period measure without a
             cohort_vs_period_basis warning. contribution_profit and
             contribution_profit_after_interest are cohort measures: formulas.yaml states
             "a Contribution Profit value is NOT a slice of any single period's expenses."
             R1-R8 as first drafted permitted the shipping CP<->AGP pair silently.
R10 ADJACENCY a MOVEMENT or ACCELERATION claim requires its points to be CONSECUTIVE in the
             canonical series, not merely adjacent in a sparse one. Without it,
             pct_>120d reads a +47 pp "quarterly jump" across the four-quarter hole
             between 2021-12-31 (8%) and 2022-12-31 (55%).
```

Three latent gaps in R3 and R6, recorded so they are not rediscovered:

- **R3's quarter test does not require `end.day` to be the last day of the month**, so
  `2022-04-01..2022-06-15` would classify as a quarter. Zero rows today — the only
  non-canonical windows are the three cross-year ones — so it is latent, not live.
- **R6 is undefined for instants.** `resolve_version(metric, period_end)` has no answer when
  `period_end` is null. Harmless today because `adjusted_gross_profit` is the only versioned
  metric and it is a duration metric; it needs a rule before `inventory_balance` or
  `borrowing_capacity` is versioned.
- **Nothing checks `population_definition_raw`.** It is non-null on exactly the 88
  `pct_>120d` rows, and **3 of those 88 disagree with the other 85** (`"our homes"` ×2,
  `"our portfolio"` ×1). A comparison can therefore straddle two denominators. §10.1 makes
  this a warning rather than a refusal — a defensible choice, stated here as one.

**R6 quantified.** `adjusted_gross_profit` is the only metric with two formula versions: v1
`2020-01-01..2021-12-31`, v2 `2022-01-01→`. 46 slots, 172 observations, **383 same-shape pairs
— reproduced exactly at S2**.

**Correction, 2026-08-04 (S2, measured).** This paragraph said "v1 (17 slots, 47 observations)
… 185 (48.3%) forbidden". That census is **internally inconsistent**: three slots — `FY2018`,
`FY2019`, `2019Q4`, 5 observations — end *before* v1's `valid_from` of `2020-01-01` and
therefore lie in **no declared window** *(verified: the ontology declares only
`adjusted_gross_profit_v1` 2020-01-01..2021-12-31 and `_v2` 2022-01-01→)*. Counting them as v1
reproduces 185/48.3%/17/47 and the "1 adjacent-quarter + 4 year-over-year" figure exactly — but
only by asserting that v1's restructuring adjustment applied in 2018, which nothing declares.

**The measured answer is 198 forbidden (51.7%)**: 160 `FORMULA_VERSION_MISMATCH` + 38
`FORMULA_VERSION_UNDECLARED`. The mismatches are still precisely **1 adjacent-quarter
(2021Q4→2022Q1) and 4 year-over-year** — the five that span the boundary the 2022 crisis story
wants to cross. The 38 undeclared all touch the three pre-2020 slots, and are refused rather
than clamped because C4 forbids a silent fallback. The detector refuses them and the writer is
told why, quoting `formulas.yaml`'s note about the restructuring adjustment.

### 6.10 Ranking and deduplication — deterministic, no model

**No LLM is the ranker in V1, and no LLM is a tie-breaker either.** A model that orders story
candidates is a model choosing its own evidence one step earlier than the writer would.

```
score = 0.40·clip(|z|/4)         magnitude against the metric's own delta history
      + 0.20·clip(|Δ|/p90(|Δ|))  magnitude in the metric's own units   <- founder-corrected, §6.10.1
      + 0.15·clip(n_docs/5)      corroboration across distinct filings
      + 0.10·novelty             1/(1+prior candidates for this metric+detector), plus
                                 is_first_occurrence (e.g. the first negative value ever)
      + 0.10·recency             quarters from 2026Q1, normalised
      − 0.15·warning_count
      − 0.20·single_source       n_docs == 1
      − 0.25·repetition          cosine over the candidate's (metric_ids, period_keys, story_type)
                                 against the last N accepted posts' candidates
```

### 6.10.1 Founder-approved correction — the magnitude-units term *(2026-08-04, gate G2)*

| | |
| --- | --- |
| **Old formula** | `0.20 · clip(Δpct / p90)` |
| **New formula** | `0.20 · clip(\|Δ\| / p90(\|Δ\|))`, where `p90` is over **absolute step magnitudes for that metric under the accepted canonical series and adjacency policy** |
| **Affected population** | **130 of 262 candidates (49%) carry no `delta_pct` at all** *(measured 2026-08-04)*, so the old term was silent for half the corpus |
| **F1 ranking** | **16th of 262 under the new term; 37th under the old** — out of the top decile (26), penalised for having crossed zero. F2 5th, F3 6th |

**Why the old formula could not stand.** It is silent wherever `delta_pct` is suppressed, and
`delta_pct` is suppressed **for factual-safety reasons that remain unchanged**: a relative
change across a sign flip is what §13.3 calls *"arithmetically defined and rhetorically
meaningless"*, and `story/core/numerals.relative_change_across_zero` refuses it. The detector
therefore publishes no `delta_pct` when a step crosses zero, when `v0 == 0`, or for any
`unit == percent` metric. **That rule is not relaxed by this correction** — the ranking term was
changed *around* it, not the other way about. `adjusted_ebitda 2022Q2→2022Q3`, δ = `−429,000,000`,
the largest sign flip in the corpus, has `delta_pct = None` by design.

**Why this form.** Every defect this corpus produced on the relative form came from the **base
it divides by**, never from the percentile: P10's factor of twelve on the margin bars,
`adjusted_ebitda 2021Q4→2022Q1` reading `+43,900%` off a `$0.4M` base, and the `−221%` of the
cross-zero case. Dividing by the metric's own p90 does the within-metric normalising the
relative form was there for, **without an unstable starting-value denominator**. It also matches
this line's own descriptor — *"magnitude in the metric's own units"* — which `Δpct/p90`, a
relative measure, never did.

**Rejected:** restoring `Δpct/p90` literally (silent on 49%); a hybrid relative-where-available
form (two magnitude scales in one weighted term make `total` incomparable across candidates,
which defeats a ranking); dropping the term (loses "how big is this *for this metric*").

**Versioning.** §14 requires `story_run_id` to cover **every behaviour-changing input**. The
ranking formula decides which candidate becomes a post and is therefore behaviour-changing —
and the contract as built had **no ranking version at all**: `story_run_id` covers
`detector_versions` and the canonicalisation `policy_version`, neither of which moves when a
scoring term changes. **`RANKING_POLICY_VERSION` is introduced at `1.1.0`** (`1.0.0` being the
`Δpct/p90` form that never shipped) and added to the `story_run_id` digest, so this correction
is visible in run identity rather than silently re-ranking a rerun.

Weights are a stated starting point, not a derivation, and `config/story.yaml` owns them so a
change is a change of record. Measured to calibrate the gates: across **298 consecutive-quarter
deltas there are 18 with |z| > 2 and 35 with |z| > 1.5**. So `|z| > 2` selects the top 6% — the
headline gate — and 1.5 is the "worth a paragraph" gate. Only **13** metrics have three or more
consecutive deltas, which is the population every z-score in this plan is computed over.

**Signals that must NOT become score terms**, and why, so the mistake is not made twice:
`ambiguity_count` carries zero ranking information because both codes are metric-constant
(`homes_sold_recognition_point` on all 124 `homes_sold` rows,
`pct_120_days_denominator` on all 88 `pct_>120d` rows). They are a mandatory caveat on the
candidate, not a discriminator. `magnitude_z` is suppressed to `null`, not computed, when a
metric has fewer than ~8 points — `holding_costs` (4 deltas), `homes_under_contract` (4),
`borrowing_capacity` (0).

**Deduplication** runs before scoring: collapse on shared anchor period + `component_metrics`
lineage (§6.6 D3), and on `(passage_id, date)` for events (the three `executive_change` rows
are one story, not three).

### 6.11 Candidate identity

Matching `extraction/core/identifiers.py` — readable segments, then a 12-character SHA-256
digest over `\x1f`-joined parts:

```
candidate_id = cand:{detector_slug}:{scope_slug}:{subject}:{anchor}:{digest12}

digest12 = digest(detector_id, detector_version, policy_version,
                  *sorted(metric_ids), *sorted(anchor_input_ids))
```

Readable segments never carry uniqueness. The digest covers **structural** inputs only — ids,
metric names, version strings — never a value, never a threshold, never anything a model
produced. Anchor ids are sorted so input ordering cannot change the id. `detector_version` and
`policy_version` are inside the digest so a threshold or canonicalisation change **mints a new
candidate** rather than silently mutating one.

Worked examples (digests computed with the repository's own `digest`, against the 2026-08-02
snapshot — recompute at L3 and record the drift):

```
cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:6a6933d6866d
cand:metric-move:gaap-gross-margin:opendoor:2022Q2_2022Q3:2f8f85e0d337
cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:opendoor:2022Q3:dcc6148df2bd
cand:inventory-risk:pct-homes-on-market-gt-120-days:opendoor:2024Q3_2024Q4:afb3b432a3e6
cand:leadership-change:executive-change:opendoor:2025-09-10:e1308acd294e
```

The leadership anchor is the **announcement** date, so it differs from the event id, which
reads `evt:executive-change:undated:…` because `occurred_on` is null. The candidate carries
`date_basis: announced` so the two reconcile.

---

## 7. The staleness gate — L0, and it ships first

Three checks, in the `freshness` stage, runnable with no model and no detector:

1. **Graph vs extraction.** `sha256(run.complete)` of the directory the graph manifest names
   must equal `manifest.inputs.run_complete_sha256`. Mismatch → `package_input_digest_mismatch`,
   **refuses the package, not the draft.** Failing today (§1.1).
2. **Neo4j vs export.** The `:GraphLoad` marker's `graph_run_id` and the distinct
   `graph_run_id` across `:Observation` must equal the export directory's. Also compare
   `node_count`/`edge_count` on the marker against the manifest's `counts`.
3. **Ontology.** `ontology_definition_hash` on the nodes must equal the loaded ontology's.
   `graph/core/manifest.py:163-175` already raises `OntologyMismatchError` on the way in.

`python -m story doctor` runs all three and exits non-zero on any. **Every command that builds
a package or a draft runs it first**, and there is no flag to skip it — `config/story.yaml`
cannot authorise it, in the same spirit as `graph/cli.py:192-195`.

**The gate guards forward only, and that is not sufficient.** An accepted post at
`accepted/<candidate_id>.md` carries values and is never revisited; after a graph rebuild, §7
correctly refuses `story rebuild` and leaves the published file on disk, unmarked, holding
numbers from a run the gate now rejects — §17.8's failure one step later. So §20 gains
**`python -m story recheck [STORY_RUN_ID]`**: re-resolve every `fact_ledger` entry of every
accepted post against the *current* graph, and write `retractions.jsonl` naming each post,
each fact whose value moved or vanished, and the old and new readings. It exits non-zero when
any accepted post is affected. A post is not retracted automatically — that is a judgment —
but it can never be silently wrong, which is the property §7 exists to provide.

This is the one deliverable that has value before anything else is written, and it is the only
check in this plan that catches §17.8.

---

## 8. Semantic retrieval — deferred, with the reason and the design

**V1 ships no embeddings.** Three reasons, in order of weight.

1. **Writing them into Neo4j would break `graph verify`.**
   `graph/stages/load/verification.py:371` computes a per-node `content_digest` over sorted
   properties, and the report compares node counts against the export. An `embedding` property
   on `:Passage` changes 8,776 digests; a separate `:PassageEmbedding` label changes the node
   count. Both are checks the graph load already makes and the other workstream relies on.
2. **The vectors would be wiped on every reload.** The graph is rebuilt per run by design.
   An embedding stored in Neo4j is derived state living in a store that is deliberately
   disposable.
3. **`numpy` is a declared non-dependency** (§4), and brute-force cosine over 8,776 × 1024
   floats in pure Python is roughly 9M multiply-adds — seconds per query, fine for batch and
   marginal for interactive.

**What V1 uses instead:** the `passage_text` FULLTEXT index that already exists *(measured
live)*, queried with terms drawn from the ontology's own alias index for the candidate's
metrics, plus deterministic filters on `document_id`, `document_type` and `filing_date`. The
corpus is 8,776 passages of domain-specific finance prose with a controlled vocabulary already
built — this is the case lexical search is good at.

**The L2 extension point, if fulltext is measured insufficient.** A sidecar store owned by the
story package, never in Neo4j:

```text
data/story_embeddings/<embedding_run_id>/
  vectors.jsonl        {"passage_id": ..., "vector": [...]}   sorted by passage_id
  manifest.json        embedding_model_id, dimensions, normalisation, text_preparation_version,
                       graph_run_id, passage_content_digest, created_at
```

`embedding_run_id = embed-v1-<digest12>` over `(model_id, dimensions, text_preparation_version,
sorted passage ids + their content digests)`. A dimension or model change mints a new id; a
package naming a stale one is refused, the same shape as §7. Scope: **passages only** — not
entities, metrics, events or candidates, all of which are already exactly addressable by id and
gain nothing from approximate matching.

Text preparation must be deterministic and versioned. Note the measured hazard from
`extraction/providers/local_openai_compatible_embeddings.py:29-38`: the local server's vectors
**depend on the request that preceded them in the slot** — embedding `A, C, C` yields two
different `C` vectors, differing up to 1.3e-4 per component. So `vectors.jsonl` is not
byte-reproducible and its manifest must say so rather than claiming an identity it does not have.

**Hybrid retrieval, when it arrives**, fuses four things and each has a fixed role:
fulltext supplies lexical recall over controlled vocabulary; vector similarity supplies
paraphrase recall; graph relationships supply the *only* path from a passage back to a fact;
deterministic filters (`document_type`, `filing_date`, period window) bound the candidate set
before either search runs. Scores are min-max normalised per arm before fusion, because a
Lucene score is unbounded and a vector score is `[0,1]`.

**Semantic search may never be the source of a number.** It selects explanatory passages for
`kind: explanatory` sentences only. Every numeral in a draft is bound to a graph fact by id
(§13.1), and no retrieval path can introduce one.

---

## 9. Retrieval tools

Fifteen parameterised tools. **All Cypher is code-owned, string-literal, and parameterised;
no tool interpolates a caller value into a query string.** Every statement is issued as
`neo4j.Query(text, timeout=config.query_timeout_seconds)` via `execute_query(...,
routing_=neo4j.RoutingControl.READ)` — with `RoutingControl.READ` recorded as *intent*, not
as a control, because on a single Community instance it enforces nothing (§16).

Every tool returns a named field list, never `node { .* }`. Every tool declares
`max_rows`, and returns `truncated: bool` rather than silently capping. Every tool that returns
a fact returns its `observation_id`/`event_id` and its `passage_id`/`document_id`/`source_url`
— a fact without a citation handle is not a return value.

| Tool | Inputs | Max rows | Hops | Available now |
| --- | --- | --- | --- | --- |
| `list_metrics` | — | 26 | 0 | yes |
| `get_metric_definition` | `metric_id` | 1 | 0 | yes |
| `get_metric_history` | `metric_id`, `shape`, `since`, `until` | 200 | 1 | yes |
| `compare_metric_periods` | `metric_id`, two `period_key`s | 2 | 1 | yes |
| `find_metric_changes` | `metric_ids`, `shape`, thresholds | 200 | 1 | yes |
| `find_cross_metric_divergence` | metric pair, `shape` | 200 | 1 | yes |
| `get_events_in_window` | `since`, `until` | 50 | 1 | yes (6 events) |
| `get_event_timeline` | `event_type_id?` | 50 | 2 | yes |
| `get_fact_evidence` | `observation_id` \| `event_id` | 10 | 2 | yes |
| `get_passage_context` | `passage_id`, `before`, `after` (each ≤ 3) | 7 | 1 | yes |
| `search_passages` | `terms[]`, `document_types[]`, `since`, `until` | 25 | 1 | yes (fulltext) |
| `find_related_entities` | `entity_id` | 25 | 2 | yes (9 entities) |
| `find_counter_evidence` | `metric_id`, `period_key` | 25 | 2 | yes |
| `get_guidance_history` | `metric_id` | — | — | **no** — returns `Unavailable(reason)` |
| `compare_guidance_to_actual` | `metric_id`, `period_key` | — | — | **no** — `Unavailable(reason)` |
| `get_price_reaction` | `date`, `window` | — | — | **no** — `Unavailable(reason)` |

`build_story_evidence_package` is deliberately **not** a tool. It is §10's builder, called by
the pipeline, never by a model — a model that can call it can widen its own universe.

**Universal constraints.** Allowed **base** labels — all eight of them:
`Metric, Observation, Event, Passage, Document, Entity, Issue, EvidenceSource`. Allowed
relationships — all twelve: `HAS_OBSERVATION, EVIDENCED_BY, PART_OF, PARTICIPATES_IN,
OBSERVATION_OF_SUBJECT, RECONCILES_TO, DISTINCT_FROM, HOLDS_POSITION_AT, BORROWS_UNDER,
PLACEHOLDER_FOR, FOUND_IN, CONCERNS_METRIC`.

**The allowlist is over base labels, and it matches by presence, never by exact label set.**
The live graph carries **23 labels and 12 relationship types** *(measured live)*, because
`graph/stages/projection/nodes.py` adds ontology-derived secondary labels (`Person`,
`PublicCompany`, `Company`, `Subsidiary`, `CreditFacility`, `StockExchange`, …), evidence-kind
labels (`XbrlFact`, `FilingMetadata`, `ExternalPage`, `MarketData`, `Calculated`) and status
labels (`Warned` 185, `NotAttempted` 10,852, `Rejected` 49, `Unresolved` 1) alongside the
operational `GraphLoad` marker. **A test whitelisting by exact set would reject the nodes this
plan depends on** — §9 excludes `:NotAttempted`, §10.1 surfaces `:Warned`, §7 reads
`:GraphLoad`.

Two of the eight base labels were missing from the first draft and both matter. `Issue` was used
throughout the prose and never declared. **`EvidenceSource` is new at F0** — the eighth base
label, for evidence that names no filed passage. **Zero such nodes exist today** (all 2,714
evidence rows are `normalized_passage` or `normalized_table`), which is exactly why it must be
in the allowlist now: a tool that allowlists by presence will start silently dropping citations
the day the XBRL lane emits one, and a silently dropped citation is the failure §13.7 exists to
prevent. §13.7's Rule C is its verification counterpart.
**`:Issue`, `FOUND_IN` and `CONCERNS_METRIC` are reachable only by `find_counter_evidence` and
`story issues`**, and every query touching them excludes `:NotAttempted` — 10,852 of 17,130
issues record a question never asked, and a retriever that surfaces them is reporting the run's
own bounds as a finding about Opendoor. **`OBSERVATION_OF_SUBJECT` is never traversed
outward from `:Entity`**: `opendoor` has degree ≥ 2,704 and one hop is the whole graph.

**Error and ambiguity states**, uniform across tools: `Ok(rows, truncated)`,
`NotFound(id)`, `Ambiguous(candidates)` — e.g. a metric surface resolving to two members of a
`mutually_distinct_group` — `Unavailable(reason)` for the three blocked tools, and
`Refused(code, detail)` for a comparability violation under §6.9. **`Unavailable` is an answer
and is rendered as one.** The agent says "the graph holds no guidance data, and here is the
refusal that records why", never a silence the model fills.

**Lucene escaping in `search_passages`.** The query string is built from an escaped term list,
never from model output verbatim. Reserved characters `+ - && || ! ( ) { } [ ] ^ " ~ * ? : \ /`
are escaped — which covers the four financial surfaces that break Lucene, and blocks
field-prefix injection (`text:…`) because `:` is escaped.

**Characters are not enough: `AND`, `OR`, `NOT` and `TO` are Lucene operators and are words.**
A model-supplied `terms[]` of `["margin", "NOT", "gross"]` becomes a boolean exclusion — a
change to the query's *meaning*, not its filter, and a model-controlled channel for silently
suppressing evidence, which is the failure §11 exists to prevent. It cannot escape
`:Passage.text` and cannot write, so the severity is low and the fix is one line: reserved
words are quoted or dropped. **Five fixtures**, not four: a passage id, `8-K/A`, a quoted row
label, a date range, and a bare `NOT`.

---

## 10. The evidence package

`StoryEvidencePackage`, version `1.0.0`. Serializable, hashed, reproducible, and **the model's
entire universe**.

```
identity        package_id, package_version, candidate_id, detector_id + version,
                policy_version, graph_run_id, graph_projection_version, extraction_run_id,
                run_complete_sha256, ontology_id, ontology_definition_hash, package_content_digest
subject         entity_id, entity_text, resolved, labels
facts[]         observation_id, metric_id, metric_label, period_key, period_start/end |
                instant_date, shape, value, unit, currency, scale, scale_location,
                printed_form, row_label, column_label, source_lane, validation_state,
                warning_codes, ambiguity_codes, passage_id, document_id, source_url, quoted_text
metrics[]       full definition rows for every metric referenced: unit, allowed_units,
                period_type, aliases, distinct_from, mutually_distinct_groups membership,
                ambiguities (code, description, impact), population, percentage_min/max
formula_windows[]  metric_id, version_id, valid_from, valid_to, expression, component_metrics,
                   adjustment_components[] WITH THEIR `note` TEXT, and basis: cohort|period
events[]        event_id, event_type_id, occurred_on, announced_on, date_basis, review_flag,
                properties (verbatim strings), participants with roles, passage_id, quoted_text
relationships[] relationship_instance_id, predicate, source/target entity ids and types,
                valid_from, valid_to, quoted_text
evidence_sources[]      evidence_source_id, evidence_kind, labels, and the kind's own fields
                        (accession/xbrl_concept | provider/instrument_id/session_date/... |
                        input_observation_ids/calculation_expression/calculation_version).
                        EMPTY today; a leaf with no PART_OF edge -- see §13.7.2
primary_passages[]      passage_id, document_id, text, char_count, heading_path, section_id,
                        passage_kind, source_url
context_passages[]      the ±1 neighbours of each primary passage, same shape
explanatory_passages[]  fulltext hits, each with its query terms and score, same shape
counter_evidence[]      other values in a used fact-slot; adjacent-period values of a used
                        metric; refused readings touching a used identity; contradicting
                        passages from find_counter_evidence
warnings[]      code, severity, subject_ids, detail — the union of §10.1
conflicts[]     slot, clusters with values and documents, classification, resolution_rule
compatibility[] every comparability decision made and its rule id (§6.9)
documents[]     document_id, form, filing_date, report_date, accession, source_url,
                document_type, title, content_sha256
                NOTE: built only from CITED PASSAGES. An :EvidenceSource carrying a
                document_id does NOT imply that :Document node exists (§13.7.2)
retrieval_trace[]  tool name, parameters, row count, truncated, elapsed_ms
budget          token_estimate, per-section counts, and every cap that bound
```

### 10.1 Warnings the package must carry, and they are all computable today

`validation_state: warned` on any used fact (185 observations carry `unpreferred_source_lane`);
`ambiguity_codes` on any used fact, with the ontology's own `description` and `impact` text;
`resolved: false` on any entity mentioned; `occurred_on` absent on any event used;
`review_flag` on any event used; differing `population_definition_raw` between two compared
facts; a `formula_windows` boundary crossed; `single_source` where `n_docs == 1`; and the
conflict classification for any used slot.

### 10.2 Bounds

**Every section is bounded. The first draft bounded seven of sixteen** (§0c item 4), which is
how `compatibility[]` — specified as "every comparability decision made", O(n²) over a
26-quarter series — got into a package with a token budget and no cap.

| Section | Default | Ceiling |
| --- | --- | --- |
| structured facts | 5–12 | 24 |
| events | 1–5 | 8 |
| relationships | 0–4 | 8 |
| **primary passages** | **2–4** | **6** |
| context passages | ±1 per primary | ±2 |
| explanatory passages | 1–3, **excerpted** | 5 |
| counter-evidence | 1–3, **excerpted** | 6 |
| documents | derived | 20 |
| metrics[] | one per referenced metric | 8 |
| formula_windows[] | one per referenced metric-period | 8 |
| warnings[] | — | 20 |
| conflicts[] | — | 8 |
| compatibility[] | **only decisions the candidate's own comparisons made** | 12 |
| retrieval_trace[] | — | 40 |
| **total token estimate** | **≤ 5,000** | **6,000** |
| graph hops from any seed | 2 | 2 |

### 10.2.1 The passage budget, measured properly

The first draft of this section sized passages from a median of 698 characters and concluded
that eight primaries with context is ~5,800 tokens. **698 is the median over all 8,776 graph
passages; the median passage that actually backs an observation is 2,144.5 characters**
(n = 150, mean 2,070, max 4,300) — because a table passage *is* a markdown table and tables
are long. Corrected arithmetic:

| configuration | ≈ tokens |
| --- | --- |
| 1 backing passage, median | 536 |
| 1 backing passage + ±1 context | ~1,600 |
| **8 primaries + ±1 context (the first draft's recommendation)** | **~12,900** |
| 4 primaries + ±1 context | ~6,400 |
| **3 primaries + ±1 context** | **~4,800** |

The local runtime is `-c 8192` with `max_output_tokens 1024` (`config/extraction.yaml`,
verified 2026-08-01). So the first draft's package **exceeded its own budget at every
percentile and exceeded the server's entire context at p90**, before the system prompt.

**Three changes follow, and together they are the resolution of D3.**

1. **Primary passages default to 2–4, ceiling 6** — the table above.
2. **Explanatory and counter-evidence passages are excerpted, not shipped whole**: a
   ±400-character window around the matched span, with `char_start`/`char_end` into the full
   `:Passage.text` so a citation still resolves to the byte and the evidence panel can fetch
   the rest. Excerpting is *not* applied to a passage a fact is bound to — §13.7's Rule A
   needs the whole table.
3. **The planner and the writer see different slices of one package.** The planner gets facts,
   metrics, events, warnings, conflicts and *excerpts*; the writer gets the accepted plan plus
   the **full text of every passage any bound fact cites**, chosen by fact binding, **not by
   the plan's `required_citation_passage_ids`**.

Point 3 is a correction, not a detail. The first draft said "give the writer the plan plus only
the passages the plan cites" — and `required_citation_passage_ids` is *model output*, so that
would have let the planner filter the writer's universe (§0c item 11). §2's line is that the
model chooses words and code chooses facts; **the writer's passage set is therefore derived
from fact bindings by code**, and the plan can only order and emphasise what is already there.

Sizing sanity check: all 2,704 observations are backed by 150 passages totalling ~77,600
tokens. A single story needs about 6% of that.

### 10.3 Identity and reproducibility

`package_id = pkg:{candidate_slug}:{digest12}` over `(package_version, candidate_id,
graph_run_id, run_complete_sha256, ontology_definition_hash, sorted fact ids, sorted passage
ids, budget parameters)`. `package_content_digest` is `sha256` over the canonical JSON of the
whole package with `sort_keys=True, separators=(",", ":")` — the repository's compact encoding
(`graph/stages/projection/export.py:203-205`). Rebuilding a package from the same graph run
must produce the same digest, and a test asserts it.

---

## 11. The editorial planner

The first model call. Temperature `0.0`, schema-constrained, and it sees **only the package**.

Output schema (portable subset — see §15.3 for why `minimum`/`maximum` cannot be used):

```
thesis                      string
why_it_matters              string
key_points[]                {claim, required_fact_ids[], required_citation_passage_ids[],
                             statement_class: reported|calculated|explanatory}
counterpoints[]             {claim, required_fact_ids[], required_citation_passage_ids[]}
required_warnings[]         warning codes from the package that MUST appear in the post
causal_language             enum: forbidden | reported_only
uncertainty                 string
structure[]                 section headings in order
prohibited_claims[]         string
unusable_evidence[]         {id, reason}   — package items the plan deliberately did not use
```

**`required_fact_ids` and `required_citation_passage_ids` must all resolve in the package**;
a plan naming anything else is rejected before the writer ever runs. `causal_language` is
`reported_only` **only** when the package contains a passage whose cited span carries a causal
marker (§13.10); otherwise the planner's own schema forces `forbidden`, and that is computed by
code before the call, not chosen by the model.

**`counterpoints` is required to be non-empty when the package's `counter_evidence` is
non-empty**, and `unusable_evidence` must account for every counter-evidence item not used.

**That rule is satisfiable vacuously as first drafted, and three things are needed to close
it.** §15.3 prohibits `minItems`, so "non-empty" cannot be expressed in the schema at all and
is a post-hoc code check whose only outcome is rejection; nothing constrained content, so
`{claim: "Margins vary.", required_fact_ids: []}` satisfied it literally; and **no §13 check
required a counterpoint to reach the draft** — §12's prohibitions covered a dropped
`required_warning` but not a dropped counterpoint.

1. Every `counterpoint` must carry **at least one `required_fact_id` or
   `required_citation_passage_id` drawn from `counter_evidence`**. A counterpoint grounded in
   nothing is not a counterpoint.
2. `unusable_evidence[].reason` is an **enum**, not a free string — `superseded_by_later_filing`,
   `different_period_shape`, `different_population`, `immaterial_at_stated_precision`,
   `outside_thesis_scope` — matching §15.3's rule that every constraint is an enum. A free
   string in a regime that cannot enforce `pattern` is a box to be filled, not a decision.
3. §13 gains `required_counterpoint_absent`: every plan counterpoint must appear as a draft
   sentence binding at least one of its `required_fact_ids`. **REFUSE.** §22 keeps
   "counter-evidence handling" as a human-judged dimension because fairness of representation
   is not mechanically checkable — but *presence* now is.

**Correction, 2026-08-03 (adversarial review AR1): the evidence set is not entirely
model-free, and §11's guarantee has to be stated more narrowly.**

This section, and §17.13, rest on the claim that the model cannot influence which evidence
exists. For structured facts that holds — detectors and the packaging builder select them by
code. For **explanatory passages it does not**, and the measurement is unambiguous:
`search_passages` takes a model-supplied `terms[]` and returns `ORDER BY score DESC LIMIT 25`,
so adding terms **re-ranks and evicts**. Adding three innocuous terms to a two-term query
dropped **18 of the 25** passages the original query returned.

The operator channel is genuinely closed — escaping was proved effective with numbers
(escaped `margin "NOT" gross` → 3,318 hits, unescaped → 372, an exclusion). What remains open
is ordinary top-k displacement, which no amount of escaping addresses.

Three consequences, and S7 must be built to them:

1. **The planner may not choose search terms.** Terms are derived by code from the candidate's
   metric aliases and period surfaces (§6.4's `EvidenceRequest`), never authored by a model.
2. **`counter_evidence` may never come from `search_passages`.** It comes from
   `find_counter_evidence`, which is keyed on `(metric_id, period_key)` and takes no free text —
   otherwise §11's non-empty-counterpoints rule is satisfiable by a term list that ranks the
   inconvenient passage out of the top 25.
3. **The retrieval trace must record the exact terms and the `truncated` flag** for every
   explanatory search, so a reviewer can see what the bound cut.

The narrower true claim: **numbers, identities, periods and counter-evidence are model-free;
explanatory passage *ranking* is not, and is bounded and traced instead.**

The planner may not retrieve. It has no tools.

---

## 12. The post writer

Second model call. Receives: the package, the **accepted** plan, a style profile, and a length
target. Emits a structured draft, not prose:

```
DraftSentence:
  index, text, kind: reported|calculated|explanatory|connective
  fact_bindings[]:  {fact_id, rendered, char_start, char_end, metric_surface, period_surface}
  calculation:      {operation, input_observation_ids[], expression, result_rendered,
                     formula_version_id} | null
  citations[]:      {passage_id, document_id, char_start, char_end}
```

**Why the draft is structured and not prose.** Matching a bare numeral back to a fact is
hopeless. Measured **over the whole corpus** — the only population that exists, since §10's
builder is unimplemented — a bare number is ambiguous **98.9%** of the time, number + unit
**97.9%**, and number + unit + period **97.2%**. Restricted to a single candidate's own slice
the figures fall to roughly 62% / 61% / 1.4%, and it is the last of those that carries the
argument: **only period disambiguates, and only within a bounded set.** *(The first draft
quoted the second set as "measured over the package"; the package does not exist yet, so the
corpus figures are the honest ones and the conclusion is unchanged and stronger.)* A verifier
that reverse-engineers which fact the model meant is wrong far more often than not. So the writer *declares* its bindings
with character offsets, and the verifier **checks the binding it was handed and never guesses
one**. Any numeral in `text` not covered by a binding is `unbound_numeral` — refused.

The writer must not: retrieve anything; call the graph; change the thesis; add a fact not in
the package; imply causality unless the plan says `reported_only` *and* §13.10's four
conditions hold; render an unresolved entity as a name; or omit a `required_warning`.

**Style is separate from facts.** `config/story.yaml` holds a named `style_profile` (voice,
sentence length, house conventions, whether to use figures or words for small numbers). It is
passed as a distinct system-prompt section and is *never* mixed with the evidence. A style
change must not be able to change a number, and a test asserts that two drafts generated from
one package under two style profiles have identical `fact_bindings` fact ids.

---

## 13. Verification

**Deterministic authority over every structured fact. The model may only tighten, never
loosen.** The deterministic layer runs first and its REFUSE stands regardless of what the model
says.

### 13.1 Numbers — REFUSE

Tokenise numerals in the published text; every one must be covered by a `fact_binding.rendered`
span, a `calculation.result_rendered` span, a period surface, or an explicit `literal_ok`
allowlist (ordinals, the metric's own `threshold_value` such as "120 days"). Reconstruct
`V_draft`: negate on parentheses or a leading minus; strip `,` and `$`; multiply by
`{thousand/k: 1e3, million/m: 1e6, billion/bn: 1e9}`; **never** multiply by the fact's `scale`
— corpus values are already canonical (`adjusted_ebitda 2020Q4 = −27075000.0` was printed
`"(27,075)"` in a thousands table).

**Tolerance is printed-precision half-ulp.** With `d` = significant figures *as the draft wrote
them* and `e = floor(log10|V_fact|)`:

```
accept iff  ||V_draft| − |V_fact||  ≤  0.5 × 10^(e − d + 1)
            and the sign agrees, checked separately
```

**Correction, 2026-08-04 (S9a, measured).** This formula was written `|V_draft − V_fact|` and
**contradicted its own worked case**. A published numeral is usually *unsigned* — `"$27.1
million loss"` carries the sign in the word *loss*, which is prose the tokeniser does not
parse. Parsed as `+27,100,000` against a fact of `−27,075,000`, the signed form gives
**54,175,000** and blows the ±50,000 window; only the magnitude comparison gives the **25,000**
the worked case below asserts. So magnitudes are compared, and **sign agreement is a separate
check** applied when the numeral itself carries a sign (a parenthesis or a minus). Left as
written it would have failed every negative-valued fact in the corpus — **825** of 2,704
observations are negative.

i.e. the fact rounds to the draft's own numeral at the draft's own precision. `"$27.1 million"`
against `−27,075,000` gives `d = 3`, window ±50,000, |Δ| = 25,000 → PASS. `"$27 million"` gives
`d = 2`, window ±500,000 → PASS. Plus an **over-precision WARN** when `d` exceeds the
significant figures in the fact's printed form, and a **hedge guard**: `approximately`, `about`,
`roughly` relax nothing and are recorded so §13.16 can check the hedge is not doing work the
number cannot support.

### 13.2 Units and currency

Map the draft's surface to a unit (`$`/`million` → USD; `%` → percent; `homes`; `markets`;
`contracts`) and refuse on disagreement with `Observation.unit` or with the metric's
`allowed_units`. The corpus has exactly four units, so the map is closed and total.
`currency` is non-null on exactly the 997 USD rows; a currency symbol on a non-monetary unit is
a REFUSE, and a monetary unit with a null currency is a REFUSE that fires on zero rows today
and exists so an XBRL lane cannot introduce one silently.

### 13.3 Percentages — the single most likely factual error, REFUSE

43% of observations are `unit: percent` and four of seventeen populated metrics are margins in
one `mutually_distinct_group`. **The verifier never infers which quantity a change sentence
claims — the draft declares it and the verifier recomputes it.**

```
delta_pp       v2 − v1                    rendered must carry "percentage point(s)" | "pp"
delta_bps      (v2 − v1) × 100            rendered must carry "bps" | "basis points"
delta_relative (v2 − v1)/|v1| × 100       rendered must carry "%" AND a relative marker
```

**Surface gate:** for a percent-unit metric, refuse any change sentence where a `%`-suffixed
numeral sits in the same clause as a change verb unless the rendering carries an explicit
`percentage point|pp|bps|basis point` token or an explicit relative marker with
`operation == delta_relative`. *"Margin fell 3%"* is not ambiguous-but-probably-fine; it is
unresolvable. **The gap between the two readings is `100/|v1|` and is base-dependent**
*(clarified 2026-08-04 — this section said "a factor of nineteen", which is `100/5.2`, true of
the falling case it describes and not a constant)*: on `adjusted_ebitda_margin` `5.2 → 2.2` the
readings differ by **19.2×**, and on the same pair read forward, `2.2 → 5.2`, by **45.5×**. The
smaller the earlier value, the wider the gap — which is exactly when a margin sentence is most
tempting to write.

**Second gate:** a change of a percent metric may never be a `reported` sentence. No
observation in the package is a change — the extraction refused every one it saw
(174 `DERIVED_CHANGE_COLUMN` + 12 `DERIVED_COMPARISON`).

**Third gate:** `relative_change_across_zero` is refused outright when `sign(v1) ≠ sign(v2)` or
`v1 == 0`. `adjusted_ebitda_margin` crosses zero six times across its 26 quarters;
`(−6.3 − 5.2)/|5.2| = −221%` is arithmetically defined and rhetorically meaningless. Only
`delta_pp` and `delta_bps` are permitted there.

Worked, on F3's quarter: the passage says verbatim *"As a percentage of revenue, Adjusted
EBITDA was 5.2% in 2Q22 versus 2.2% in 2Q21."* — two levels, no change. So
*"improved 3.0 percentage points"* passes as a calculation citing nothing; *"improved 136%"*
passes as `delta_relative`; *"improved 3%"* is refused; and *"improved 3.0 percentage points,
as the filing noted"* is refused because the passage states levels, not a change.

### 13.4 Periods — REFUSE

Resolve `period_surface` through a **closed grammar**, never a free date parser
(`"Q2 2022" | "2Q22" | "the second quarter of 2022"` → `(duration, 2022-04-01, 2022-06-30)`;
`"1H23"`; `"9M23"`; `"fiscal 2022"`; `"as of year-end 2023"` → instant). `"the quarter"` is
`UNRESOLVABLE` and refused. Then require **exact equality on both endpoints and on kind**.

**Matching `period_end` alone would be catastrophic**: 188 `(metric, period_end)` pairs in the
package carry more than one `period_key`, and the readings differ in sign —
`adjusted_ebitda` ending `2022-09-30` is `+$183M` for the nine-month YTD and `−$211M` for Q3.
Quarter-vs-YTD conflation is refused with its own code `period_shape_conflated`, because that
is the finding a writer can act on. A calculation whose inputs have different shapes is
`incomparable_periods`.

### 13.5 Metric identity — REFUSE

Normalise the metric surface, resolve through the ontology alias index with **longest match
wins**, then refuse if: the surface resolves to nothing; the bound metric is not in the
resolved set; the resolved set has >1 member sharing a `mutually_distinct_group`; or the
surface is in `declared_ambiguous`.

**Eight surfaces resolve to more than one metric**, all declared ambiguous:

| surface | resolves to | group |
| --- | --- | --- |
| `margin` | all four margins | `margin_measures` |
| `gross margin` | adjusted_gross_margin, **gaap_gross_margin** | `margin_measures` |
| `gross profit` | adjusted_gross_profit, gaap_gross_profit | `profit_measures` |
| `contribution` | contribution_margin, contribution_profit, contribution_profit_after_interest | `profit_measures` |
| `homes` | 4 home counts | `home_counts` |
| `contracts` | acquisition_contracts, homes_under_contract | `home_counts` |
| `under contract` | homes_under_contract, homes_under_resale_contract | `buy_side_vs_sell_side_contracts` |
| `inventory` | housing_inventory_homes, inventory_balance | **none — a gap, §18** |

Note the consequence: `gaap_gross_margin`'s own label *is* `"Gross Margin"`, so the surface a
writer would naturally use is the ambiguous one. A post must say "GAAP gross margin". That is
the right outcome — the filings themselves put both under a shared heading.

**Longest match is mandatory**, not an optimisation: `"gross margin" ⊂ "adjusted gross
margin"`, `"adjusted ebitda" ⊂ "adjusted ebitda margin"`, `"contribution profit" ⊂
"contribution profit after interest"`, `"revenue" ⊂ "cost of revenue"`. A left-to-right
first-match scanner assigns *"adjusted gross margin was 13.2%"* to `gaap_gross_margin` — a
wrong metric with a plausible number.

### 13.6 Subject identity — REFUSE, plainly

All 2,704 observations carry `subject_entity_id: "opendoor"`. The graph holds nine entities:
`opendoor`, three persons, a credit facility, an unresolved subsidiary, and three
infrastructure entities. **Any named subject other than Opendoor is an automatic refusal
today.** No Zillow, no Offerpad, no Redfin, no index, no "the market" — `mortgage_rate` and
`home_price_appreciation` are declared and empty. **A comparative post is not verifiable and
must not be drafted.**

### 13.7 Citation support — two rules, because the evidence is two different things

Measured: 2,690 table observations have `quoted_text` of median **4 characters**, all bare
numerals, none carrying `%` or `$`. The 14 narrative observations, 6 events and 4 relationships
have full sentences (median 99–157 characters).

**Rule A — table facts.** Support means *positional reconstruction*, not entailment. A
4-character quote `"2.2"` entails nothing; what it supports exactly is the value.
```
1. quoted_text occurs verbatim in Passage.text                 (holds 2,714/2,714)
2. reconstruct(quoted_text, scale, unit) == value              (holds 2,690/2,690)
3. row_label is a licensed surface for metric_id               (alias index)
4. column_label resolves UNIQUELY to period_key WITHIN THIS PASSAGE   (§13.7.1)
5. the sentence's own metric and period surfaces pass §13.5 and §13.4
```

#### 13.7.1 Why step 4 needs "uniquely, within this passage" — and what it costs

The first draft wrote step 4 as *"column_label resolves to period_key"* and claimed steps 3–4
*"stop a right number being read off the wrong row."* Measured, they do not:

- **24 of 32 distinct `column_label` values map to more than one `period_key`.** `"2021"` maps
  to 11, `"2022"` to 11, `"2020"` to 8.
- Restricted to a single passage — the only scope in which the check is meaningful —
  **179 of 485 `(passage_id, column_label)` pairs are ambiguous, covering 1,656 of 2,704
  observations (61.3%).**
- **523 of 2,704 `quoted_text` strings occur more than once in their own passage.** At a median
  of 4 characters, "verbatim occurrence" locates a value in the document but not in the grid.

The concrete failure is the first row of `observations.jsonl`: `adjusted_ebitda_margin`,
`column_label: "2020"`, period `2020-01-01_2020-06-30` — a half-year. The same passage carries
`2020-04-01_2020-06-30` under the same label. All five of the draft's steps pass while the
sentence names the quarter and the value is the half-year — **exactly the conflation §13.4
calls catastrophic and §17.2 lists as an attack.** The draft's Rule A checked the extractor's
own recorded labels against each other, so an upstream column misalignment was laundered into
a verified citation.

**The rule, corrected.** Step 4 refuses when `(passage_id, column_label)` maps to more than one
`period_key` in the package, with code `column_label_ambiguous_in_passage`. That is a REFUSE on
61.3% of table observations, and the number is not a reason to weaken the check — it is the
measurement that says a bare year-column citation is not evidence of a period.

Two escape hatches, both deterministic and both narrow:

1. **A distinguishing sibling label.** When the passage carries a second label that resolves
   uniquely (`"September 30, 2022"` alongside `"2022"`), the binding may name it instead and
   the citation cites that column. **Measured, this rescues almost nothing: 9 of the 179
   ambiguous pairs, covering 22 of the 1,656 observations (1.3%)** *(verified against
   `extract-v1-lexical-833f7bcfbce9`)*. The first draft claimed it was "available on the
   passages that matter most — quarterly tables generally label at least one column fully";
   that was an assumption, and it is false. Keep the hatch because it is free and correct where
   it applies, but **hatch 2 carries the load.**
2. **`year_only_column_ambiguity` classification.** §6.6 D15 already detects exactly this shape
   and the first draft never connected it to the verifier. When D15 classifies the slot and
   §6.1 step 4 resolved it by document majority, the binding may proceed **with the
   classification and the minority reading rendered in the evidence panel**.

Anything else is refused, and `RejectedDraft.remedy` gains `REBIND_TO_DISTINGUISHING_COLUMN`.

**A table-backed sentence may not paraphrase the passage** — it may state the number, the
metric, the period and the subject, and nothing else.

**Rule B — narrative facts and all `explanatory` sentences.** Support means span containment
plus lexical grounding: the cited span must exist, contain the evidence span, carry the number
if the sentence carries one, and every content word of the assertion must appear in the span,
resolve through a licensed alias, or be in the connective lexicon. REFUSE on span, quote,
marker and number; WARN on paraphrase distance and escalate to §13.16.

#### 13.7.2 Rule C — evidence that names no filed passage

F0 added a base label `:EvidenceSource` and five kinds that cite no passage: `xbrl_fact`,
`filing_metadata`, `external_page`, `market_data`, `calculated`. **Zero such nodes exist
today** — all 2,714 evidence rows are `normalized_passage` or `normalized_table` — but the
contract exists, and Rules A and B both begin "the cited span must exist in `Passage.text`",
so both become *partial functions* the day the XBRL lane lands.

The shape matters: an `:EvidenceSource` is a **leaf**. It has no `PART_OF` edge, and
`:Document` nodes are built only from cited passages — so an `:XbrlFact` carrying a
`document_id` property does **not** imply that `:Document` node exists. `:MarketData` and
`:Calculated` carry no `document_id` at all, by design, *"because nobody filed it."*

```
Fact -[:EVIDENCED_BY]-> (:Passage) -[:PART_OF]-> (:Document)     Rules A and B
Fact -[:EVIDENCED_BY]-> (:EvidenceSource:XbrlFact)               Rule C — chain ends here
```

**Rule C: support is coordinate reconstruction or input recursion, never span containment.**

| kind | what "supported" means |
| --- | --- |
| `xbrl_fact` | the binding names `accession` + `xbrl_concept`, and the value reconstructs from the fact's own unit and dimensions. Cite `source_url`, which the kind now requires |
| `filing_metadata` | `accession` resolves and the asserted property is one the filing header carries |
| `external_page` | `source_url` + `disclosure_channel_id` + `fetched_at` all present; the channel must be one `constraints.yaml` permits for a historical claim |
| `market_data` | `provider` + `instrument_id` + `session_date` + `row_identity` identify one quote; **`disclosure_channel_id` canonicality decides whether it may support a historical claim at all** |
| `calculated` | recurse: every `input_observation_id` resolves in the package and each is itself verified under Rule A, B or C. The expression is recomputed (§13.9) |

**V1 behaviour: `Unavailable`, not silence.** Until a lane emits one, a binding to an
`:EvidenceSource` returns `Unavailable(reason)` from §9's tools and refuses the draft with
`evidence_kind_not_supported_in_v1`. That is deliberate: an unimplemented rule that silently
passes is worse than one that refuses, and this is the cheapest possible way to keep the hole
visible until F1 makes it real.

### 13.8 Event properties are strings, not facts — REFUSE any numeric binding

The six events carry `properties: dict[str, str]` of free text —
`headcount_reduced: "approximately 550 employees"`, `charge_amount: "approximately $15
million"`, `committed_capacity: "$525 million"`. None is a typed value. **A `fact_binding` may
not point at an event property.** The only permitted use is quoting the property string
verbatim with the event's evidence passage cited. The one exception is real:
`borrowing_capacity = 525,000,000.0` *is* an observation, so that figure has a numeric binding
while the headcount does not.

Two further event rules: **three of six events have `occurred_on: null`** (all three
`executive_change`), so a draft asserting an effective date is `date_not_in_package`; and one
event carries `review_flag: ANNOUNCEMENT_EQUALS_OCCURRENCE`, which must be annotated and may
never be presented as evidence of anything.

### 13.9 Reported versus calculated — REFUSE

A `calculated` sentence **must** carry a `Calculation` and **must not carry a passage
citation**; a `reported` sentence must not carry a `Calculation`.

**The rule is now the ontology's, not this plan's — and that is a correction.** The first draft
justified it from the corpus: *"there is no computed comparison anywhere in the graph, because
the extraction refused all 186 it saw, so a passage citation on a computed number is always a
provenance lie."* F0 made that reasoning obsolete by adding `EvidenceKind.CALCULATED` and
`MARKET_DATA`, giving them graph nodes and adding them to `EVIDENCED_BY.allowed_target_types`.
A calculated fact is now first-class and **citable**. What F0 also did is declare the
prohibition properly: `claims.yaml` gives `calculated` the fields
`input_observation_ids, calculation_expression, calculation_version` and
**`optional_fields: []`** — *"No filed-passage field is permitted. A calculated value that
cites a passage is claiming the filing said something it did not."* It is enforced three times
over: the row model has no `passage_id` column and `extra="forbid"`, the catalog writer never
emits one, and the validator raises `EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND`.

So the check is unchanged in effect and stronger in standing: **a passage citation on a
calculated sentence is an ontology violation, not a house rule.** The premise "no computed
comparison exists in the graph" remains true of the current run — no lane emits one — but it is
no longer a property of the schema, and nothing in this plan may rest on it.

Recompute with exact arithmetic and **compare after rounding to the draft's precision, never by
equality**. *(Correction 2026-08-04: this section used `5.2 − 2.2` as the float-residue example
and that subtraction is **exactly 3.0** in IEEE-754. The hazard is real and the instance was
not. Real residues from this corpus: `adjusted_gross_margin` `9.9 − 7.3 = 2.6000000000000005`,
`13.2 − 9.9 = 3.299999999999999`, and — a spike value — `3.3 − 13.2 = −9.899999999999999`.)*
Require ≥2 resolving inputs sharing **unit and period shape** and passing §13.4 — *(corrected
2026-08-04, D5: this said "sharing **metric** and unit", which **refuses this plan's own
recommended spike**. `cross_metric_divergence` subtracts `gaap_gross_margin` from
`adjusted_gross_margin`, and the metrics differing **is** the story; a same-metric rule kills
every cross-metric comparison the D4 detector exists to find. Shared unit and period shape is
the constraint that actually matters — it stops a percent being differenced against a count, or
a quarter against a fiscal year.)* — and require a
`formula_version_id` that `check_formula_for_date` accepts **for the period computed over, not
the filing date**.

**Five assertion types now, not four.** `AssertionType` gained `GUIDED` at F0
(`REPORTED, CALCULATED, CLASSIFIED, INFERRED, GUIDED`). A `guided` observation is not a
reported one and may never be rendered as a level the company achieved; §13.15 governs it. All
2,704 observations in the current run are still `reported`.

The calculation is not hidden, and the panel design is now the contract's own shape: the
`calculated` evidence kind carries exactly `calculation_expression`, `calculation_version` and
`input_observation_ids`, which is what the evidence panel renders — the expression, both input
`observation_id`s (human-readable by construction), and each input's own passage. The sentence
itself cites no passage.

### 13.10 Causation — the crux

**Two mechanisms, distinguished structurally rather than lexically.**

**A. LLM-originated causation — banned unconditionally.** Any sentence of kind `calculated` or
`connective`, or any sentence with no citation, may not contain a causal construction:
`because (of)`, `caused`, `due to`, `as a result of`, `attributable to`, `drove`, `led to`,
`resulted in`, `stemmed from`, `contributed to`, `owing to`, `was impacted by`, `is why`,
`explains`, `reflects`, `reflecting`, `thanks to`, `the driver of`, `on the back of`.

**B. Reported causation — permitted under six conjunctive conditions.** A sentence of kind
`explanatory` may assert causation iff (1) it carries an **attribution frame** naming the
source in the sentence itself (`the company said/stated`, `management attributed`, `the filing
states that`, `according to the {10-K, 10-Q, shareholder letter, earnings release}`); (2) the
causal marker appears **inside the cited span**, not merely somewhere in the passage — passages
run to thousands of characters and whole-passage containment would let any marker license any
claim; (3) both the cause term and the effect term appear inside the cited span; (4) the frame's
noun matches the cited document's `document_type`.

**Conditions (5) and (6) exist because the first four are co-presence tests, not linkage or
polarity tests.** Both holes are live in this corpus:

5. **Polarity.** The cited span must not negate the marker. **37 passages carry a negated
   causal construction** — e.g. `…tm2017926d1_ex10-5.htm#p6`: *"The Purchaser decided to enter
   into this Agreement **not as a result of** any general solicitation…"* A span containing
   `as a result of`, both terms, a frame and a matching document type satisfies conditions 1–4
   while the filing asserts the **opposite**. Refuse when a negation token (`not`, `no`,
   `never`, `rather than`, `other than`) precedes the marker within the same clause.
6. **Linkage.** The marker must syntactically join *the same two terms the sentence joins*.
   **367 passages carry two or more distinct causal markers.** Given a span holding "X rose due
   to Y" and "Z fell as a result of W", a sentence asserting "the company attributed Z to Y"
   satisfies marker-in-span, cause-in-span and effect-in-span, and is false. The cheap
   deterministic form: cause and effect must fall on the **same side of the same marker
   occurrence** — cause within N characters after the marker, effect before it — and a span
   containing more than one marker requires the binding to name which occurrence it relies on.

Neither hole has a model backstop today: §13.16's `temporal_association_turned_causal` is
advisory, and §13.17 states there is no WARN tier for causation. Conditions 5 and 6 are
therefore deterministic REFUSEs, and the linkage test is the weakest check in this plan —
recorded as such rather than presented as solved.

Condition B is cheap to satisfy because the corpus is rich in quotable causal language: `due
to` appears in 558 passages, `result of` 439, `as a result of` 388, `attributable to` 355,
`led to` 175.

**C. Hedged association — permitted without a passage** using a closed lexicon
(`coincided with`, `alongside`, `over the same period`, `in the same quarter`) and only when
every fact involved is bound. Since the package holds no external subject (§13.6), pairing a
company fact with an external one is refused anyway today.

**The worked distinction that shows why B needs condition 2.** The 2020 workforce-reduction
event carries `properties.reason = "outbreak of the COVID-19 pandemic"`, and its quote reads
*"On April 15, 2020, we carried out a reduction in workforce **following** the outbreak of the
COVID-19 pandemic."* So:

| Draft | Verdict |
| --- | --- |
| "…**because of** the pandemic." | REFUSE — banned construction, no frame. The filing says "following", which is temporal |
| "**The company stated** that the reduction **followed** the outbreak…" | PASS — frame present, marker in span, both terms in span, document type matches |
| "**The company attributed** the reduction **to** the pandemic." | **REFUSE** — the frame is correct but `attributed…to` is not in the cited span. This is the exact substitution the check exists to catch |

There is no WARN tier for causation. A hedge is not partial credit.

### 13.11 Unresolved entities — REFUSE

The package holds one:
`opendoor_unnamed_subsidiary#evt:credit-facility-established:2022-10-19:…`, with
`resolved: false`, `entity_text: "a subsidiary of the Company"`, and a
`PARTICIPANT_NOT_NAMED` issue. Its only permitted surfaces are its own `entity_text` verbatim
or a licensed indefinite paraphrase. **"Opendoor entered into the facility" is a refusal** —
the borrower is the subsidiary, and the graph refuses to guess. Detection is by id, not by
heuristic: any entity id containing `#evt:` is scoped-unresolved by construction.

### 13.12 Conflicting facts — REFUSE unless disclosed, with a measured materiality rule

Derive `conflicted_slots` from the package at verification time, never from a constant — a
hard-coded count would pass a draft verified against a different run.

```
if slot not conflicted:                                          PASS
if every other value rounds to the same thing at the draft's own
   significant figures:                                          ANNOTATE (immaterial)
if the sentence carries a disclosure clause bound to the slot:    PASS
else:                                                             REFUSE
```

The precision-relative rule is the only one that gets both cases right. On the current run all
36 conflicted slots are rounding twins that vanish at 2–3 significant figures — e.g.
`adjusted_ebitda 2021Q1` holds `−2,141,000` and `−2,000,000`, so "$2.1 million" must disclose
and "$2 million" need not. On the stale graph, `market_count 2021-03-31` held 27 and 44, which
separate at one significant figure and could never be used silently.

The disclosure clause is a closed template bound to the slot so it cannot be pasted
decoratively, and the verifier checks that both values appear, that the chosen value is the
bound observation's, and that the named document is its document.

### 13.13 Run consistency — REFUSE

Every `fact_binding` id must resolve in the package; the draft's `verified_against` block must
equal the package's identity block exactly; the package's `run_complete_sha256` must match the
extraction directory (§7 — **passing as of F0, and this check is what proves it**); no binding
may name a refused reading (reuse `refused_identities`,
`graph/stages/load/verification.py:427`); and any binding to one of the 185 `:Warned`
observations must surface the warning in the evidence panel.

The identity block, re-pinned after the F0 rebase (§0d):

```
graph_run_id                  "graph-v1-0483dc6b4b10"
extraction_run_id             "extract-v1-lexical-833f7bcfbce9"
run_complete_sha256           "1cc8f7b01c040531..."   <- the field that actually identifies
ontology_definition_hash      "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34"
ontology_semantic_version     "2.0.0"
graph_projection_version      "1.2.0"
package_content_digest        sha256 over the package's own fact rows, sorted
```

The previous values -- `graph-v1-886059d862ce`, `extract-v1-lexical-2422c4252c07`,
`e8d4af70...`, `1.1.0` -- survive in this plan's own history, which is the point of §7: a
package naming them must be **refused, not repaired**. `ontology_semantic_version` is in the
block because F0 moved it to `2.0.0` for a breaking reason (`normalized_table` gained a
required `passage_id`, `xbrl_fact` gained a required `source_url`) after two incompatible
vocabularies had both been calling themselves `1.0.0` -- a version string that stopped
distinguishing them is exactly the failure this check exists to catch.

### 13.14 Numeral-free sentences — the hole the first draft left open

Every check above is reached through a numeral, a citation, a calculation or a causal
construction. **A `connective` sentence with none of those was unconstrained**, and three false
ones pass the whole of §13:

| Attack | Why it is false in this corpus |
| --- | --- |
| *"That was the only quarter in which the company reported a negative adjusted gross margin."* | `adjusted_gross_margin` is negative in **2022Q4 (−3.2) and 2023Q1 (−3.3)**. The same sentence about *GAAP* gross margin is true (2022Q3 only), so the form is unfalsifiable by inspection |
| *"Contribution profit held up better than adjusted gross profit through the downturn."* | **This verdict was backwards and is corrected (2026-08-04, D5): the sentence is TRUE by $2M.** The 2022Q2→Q3 fall is **−$444M** for CP against **−$446M** for AGP, and a smaller fall *is* holding up better. The mechanism the row exists to justify stands untouched — the comparison is over *deltas*, which nothing evaluates, so the sentence is unverifiable either way — but a worked example whose own arithmetic contradicts its verdict is worse than no example |
| *"Opendoor has not reported revenue growth since 2022."* | `revenue` has **zero** observations. §17.7's `unpopulated_metric` code is reached through §13.1 step 2 — i.e. through a numeral — so an absence claim has nothing to bind and nothing to refuse |
| *"The board changes took effect before the quarter closed."* | All three `executive_change` events have `occurred_on: null`. §13.8 refuses an asserted effective *date*; this asserts an effective *ordering* |

**The rule.** A `connective` sentence may contain no **claim** — only transition, structure and
reference to what adjacent sentences already established. Four constructions are refused
outright wherever they appear, in any sentence kind, with or without a numeral:

```
superlative / uniqueness   only | sole | first | last | never | always | unprecedented
                           | worst | best | largest | smallest | record | peak | trough
comparative across facts   more | less | better | worse | faster | slower | higher | lower
                           | outpaced | held up | lagged      -- when not inside a Calculation
existence / absence        has not | did not | no longer | has yet to | remains the only
temporal ordering          before | after | until | since | by the time   -- when relating
                           two package items rather than naming a period
```

Each is permitted only as an **explicit claim with machinery behind it**:

- a **superlative** requires `kind: calculated` with `operation: extremum`, the full comparison
  set as `input_observation_ids`, and the series window stated in the sentence. The verifier
  recomputes the extremum over that exact set. *"The only quarter"* over a 26-quarter series
  means 26 input ids, and the adjusted-gross-margin attack dies on recomputation.
- a **comparative** requires `operation: compare_deltas` (or `compare_levels`) with both sides'
  input ids, and the verifier recomputes both and checks the direction. The $2M case fails.
- an **absence claim** requires `operation: absence` naming the metric and window; the verifier
  confirms the package's own coverage, and an unpopulated metric returns `unpopulated_metric`
  with the refusal reason from `issues.jsonl` rather than licensing the sentence.
- a **temporal ordering** requires both dates to be non-null in the package. The three
  `executive_change` events cannot satisfy it, which is the correct outcome.

This is the largest single addition the adversarial review forced, and it is where V1 is most
likely to be over-strict rather than under-strict: some legitimate connective prose will be
refused. That is the right direction for the failure to point, and §22's readability score is
where the cost shows up.

### 13.15 Forward-looking language — REFUSE, and the extension point F0 built

**All 2,704 observations are `assertion_type: reported`.** No lane emits `guidance_issuance`
and no observation is `guided`. So today **any forward-looking construction in a draft is an
unconditional REFUSE**: `expects | guidance | outlook | forecasts | targets | anticipates |
projects | will be | on track to | guided to | plans to reach | full-year target`. There is
nothing in the package that could support one and nothing that could contradict one. The
narrative lane's own two `GUIDANCE_NOT_REPORTED` refusals are the citable evidence that the
question was asked and declined.

**What F0 built, and where the check attaches when a lane lands.** The contract is complete:
`AssertionType.GUIDED` exists; `guidance_issuance` declares
`forbidden_assertion_types: [reported]` and a typed `property_contract` over `guided_metric`,
`low_value`, `high_value`, `unit`, `currency`; a lone bound, an inverted range, a scale carried
inside a string (`"1.0 billion"`) and a `guided_metric` naming an undeclared concept are each
refused with their own code; and `check_future_period` exempts `GUIDED` unconditionally while
refusing a `reported` observation whose period ends after its carrier filing.

So `guidance_vs_actual` becomes, without further contract work: given a `guidance_issuance`
event and an observation for the same `(guided_metric, period)`, the comparison is a §13.9
`Calculation` citing the event id and the observation id — **never a passage** — and the
sentence must name both the guided range and the actual, never one alone. Three additional
rules this plan owns:

- **A `guided` observation may never be rendered as a level the company achieved.** It is a
  statement about intent, and the sentence must carry the guidance frame.
- **A qualitative guidance event carries no number**, and the draft may not invent one. The
  contract permits qualitative guidance as the absence of both bounds; there is no field for
  the band text itself, so "mid-single digit" cannot even be quoted from the event (§18).
- **`check_future_period` abstains on the current corpus** because `reported_at` is unpopulated.
  A story agent may not infer "no observation is future-dated" from the guard's existence, and
  §13.4's period rules stand on their own.

### 13.16 The model-assisted verifier

Runs **after** the deterministic layer, on sentences that already passed. Emits structured
findings, never a verdict. Output schema is flat, one array, `additionalProperties: false`,
every property required, all constraints as `enum` (§15.3).

```
findings[]: {sentence_index: integer,
             check: enum[overstatement, temporal_association_turned_causal, lost_qualifier,
                         explanation_not_in_cited_passages, counter_evidence_misrepresented,
                         no_finding],
             verdict: enum[supported, weakened, unsupported, contradicted, not_applicable],
             severity: enum[high, medium, low],
             quoted_draft_span: string, quoted_passage_span: string, explanation: string}
```

**`quoted_draft_span` and `quoted_passage_span` must be verbatim substrings** of the draft
sentence and the cited passage. The adjudicator checks that mechanically and **discards any
finding whose spans do not literally occur**, recording the discard rather than treating it as
a pass. That converts an unfalsifiable model opinion into a checkable pointer, and it is the
same trick the extraction lane already uses. `no_finding` + `not_applicable` exists so a clean
sentence produces a row — an empty array is indistinguishable from a truncated generation.

| Check | What it is shown | Adjudication |
| --- | --- | --- |
| `overstatement` | the sentence and its bound facts rendered as `metric · period · value · unit`; **no passage** | WARN only. The deterministic layer already proved the numbers. Targets "collapsed", "record", "consistently" |
| `temporal_association_turned_causal` | the sentence and the full cited span | Second opinion on §13.10 only. A deterministic REFUSE is never overturned. Catches `following` → `because of` where the marker *is* in the span but means something weaker |
| `lost_qualifier` | the sentence, the passage, the metric's `ambiguities` and `population` blocks, **and its `formula_windows[]` entry including `adjustment_components[].note`** | **REFUSE on `unsupported`+`high`.** The highest-value model check. `pct_>120d` carries a denominator ambiguity with `impact: high` on all 88 rows and three incompatible readings — that half works today. **The second half did not:** `contribution_profit` has **no `ambiguities` and `population: None`**, and its cohort caveat lives only in `formulas.yaml` under `adjustment_components[].note`, which the first draft's `formula_windows[]` did not carry. Widening that schema is what makes the check see what motivates it. Adding `population`/`ambiguities` to `contribution_profit` in the ontology is the better fix and is a §18 item |
| `explanation_not_in_cited_passages` | the sentence and every cited span, labelled with document type and filing date | REFUSE on `contradicted`, WARN on `unsupported`. Runs only where §13.7 Rule B raised a paraphrase WARN |
| `counter_evidence_misrepresented` | the sentence, the cited span, every other value in the used slot, and adjacent-period values | REFUSE on `contradicted`. The corpus supplies the trap: a passage reading *"Contribution Margin … was 4.0% versus 12.6% in 4Q20"* — both numbers in the span, only one a package fact for that period |

Adjudication rules: spans verified first; the model never overrides a deterministic verdict and
can only downgrade a PASS; any numeral in `explanation` not present in either quoted span
discards the finding; `severity` is advisory input, never output; `model_id`, `prompt_version`,
`content_sha256` and `raw_sha256` are recorded on every finding.

### 13.17 The gate

Every deterministic code in §13.1–§13.15 is REFUSE except: `over_precision` and
`paraphrase_distance` (WARN); `event_review_flag`, `conflict_immaterial_at_stated_precision`
and `warned_observation_used` (ANNOTATE). Model findings REFUSE only for `lost_qualifier`
`unsupported`+`high`, and `contradicted` on the two passage-relative checks.

**One REFUSE refuses the draft, not the sentence.** Publishing a post with a hole is worse than
publishing nothing, and a per-sentence gate invites the generator to delete the sentence rather
than fix it. WARNs do not block but must all be acknowledged in the accepted artifact.

**`RejectedDraft` must be actionable**: per finding, the check, the code, the sentence index and
character span, the offending substring, what the package holds rendered, and a **`remedy`
enum** — `REBIND_TO_FACT | RESTATE_AS_CALCULATION | ADD_PERCENTAGE_POINT_QUALIFIER |
NARROW_METRIC_SURFACE | ADD_PERIOD_QUALIFIER | ADD_CONFLICT_DISCLOSURE | ADD_ATTRIBUTION_FRAME |
REMOVE_CAUSAL_CONSTRUCTION | DROP_SENTENCE | REBUILD_PACKAGE` — plus, for rebind and narrow, up
to five package facts that *would* satisfy the sentence, so the fix is a choice rather than a
search. An enum is what makes a repair loop dispatchable and lets a human read five rejections
as five verbs.

`VerifiedDraft` carries `draft_content_sha256`, the package identity block, per-check results
with `examined` as a denominator (a draft with no numeric sentences must not report
"numbers: PASS"), every WARN acknowledged, every ANNOTATE, a `fact_ledger` (the evidence panel)
and a `calculation_ledger`. `passed` is **derived, not stored** — the discipline from
`graph/core/verification_report.py:217`.

---

## 14. State and persistence

**No generated prose or model interpretation is ever written into `data/extraction_runs/` or
`data/graph_runs/`, and the story package never writes to Neo4j.**

```text
data/story_runs/<story_run_id>/          gitignored by .gitignore:3 — no change needed
  candidates.jsonl        ranking.jsonl
  packages/<package_id>.json
  plans/<candidate_id>.json
  drafts/<candidate_id>.json
  verification/<candidate_id>.json
  accepted/<candidate_id>.md            the published text, and only when accepted
  rejected.jsonl          issues.jsonl
  generations.jsonl       the answer store — see below
  report.md
  manifest.json                         WRITTEN LAST — the completion marker
  <id>.partial/                         staging, os.replace'd into place
  <id>.rejected/                        a refused run, never on top of a good one
```

`story_run_id = story-v1-<digest12>` over `(story_layout_version, graph_run_id,
run_complete_sha256, ontology_definition_hash, config_hash, prompt_version, model_id,
provider_model_id, temperature, max_tokens, schema_digests, detector_versions, policy_version,
ranking_policy_version,
selection)` where `selection` is the normalised, sorted tuple of every argument that changes
*what is in the run* — `--limit`, `--candidates`, `--detectors`, `--since`, `--until` — plus the
§10.2 budget parameters. Derived, **no clock** — matching `graph/core/manifest.py:104-139`.

**The last seven inputs are a correction.** The first draft's digest covered neither the
selection flags nor the sampling parameters, so `story run --limit 3` and `story run --limit 20`
minted **the same id** — and §1.6's atomic finalisation would then `os.replace` the second over
the first, silently. That is precisely the defect this plan discovered in `extraction_run_id`
(§0b item 5, §18 row 2), reproduced in its own design. `provider_model_id` is in the digest
because the extraction data shows it is a filesystem path
(`/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf`): swapping the GGUF behind an unchanged
`model_id` changes every generation, and without it nothing would notice.

A test asserts the property rather than the field list, because the field list is the thing that
drifted: **two runs differing in any one digest input get different ids; two differing in none
get the same id.**

**A note on §21's byte-identity claim.** It holds in V1 because `generations.jsonl` replays the
generation server and embeddings are deferred. Once §8's sidecar lands, retrieval order derives
from vector scores the local server does not reproduce exactly (§8's measured 1.3e-4 drift), so
`story rebuild` would report a byte difference that is not a defect. **§8 must land with a
`retrieval_order` recorded in the package and replayed from it**, or the determinism claim has
to be narrowed at that point. Recorded now rather than discovered at L2.

**Git:** nothing under `data/` is tracked, so no `.gitignore` change is needed. What *is*
tracked: `config/story.yaml`, `tests/story/fixtures/` (a real slice, never hand-edited, copied
byte-for-byte from a finished run), and a small committed answer store for replay tests — the
pattern `benchmarks/extraction/v1/answers/*.jsonl` already establishes. **No prototype
database.** A directory of JSONL is what every other stage uses, it diffs, and it needs no
process running.

`generations.jsonl` is the replay mechanism, lifted directly from
`extraction/stages/narrative/answer_store.py`: rows keyed by
`request_identity(prompt, schema, model_id, temperature, max_tokens)` with `IDENTITY_VERSION`
prefix, storing `request_sha256, content_sha256, model_id, provider_model_id, temperature,
max_tokens, prompt_version, finish_reason, raw_content` — and **deliberately not** latency,
token counts, attempt counts or the envelope's `id`/`created`/`timings`, because those change
on every identical request and a record containing them could never be byte-identical. Token
counts and cost belong in the manifest and the report, not in the identity store.

The manifest records: `story_run_id`, layout version, the full package identity block, config
hash, story code commit, `prompt_version` per call site, model id and provider model id,
temperature, max tokens, schema digests, detector ids and versions, `policy_version`, counts,
token totals, and `created_at` — the only clock in the package.

---

## 15. Provider abstraction

### 15.1 Reuse, extend, new

**Reuse as-is** (copy the shape, do not import — see §15.2): the six-class error taxonomy and
its one axis, transport faults are retryable and a schema violation is the model's answer;
`ProviderSchemaError` carrying `violations` and **never retried**; `HealthStatus` as a
structured value, never an exception; `RETRYABLE_STATUSES = {429,500,502,503,504}` with
`max_retries` counting attempts *after* the first, exponential `0.25 × 2ⁿ` backoff, injected
`sleep`, and **timeout never retried**; the 10-second health-probe ceiling; the two-hash
discipline (`raw_sha256` unstable, `content_sha256` stable — measured, not assumed); the whole
`AnswerStore` replay mechanism; `temperature = 0.0` as a pinned constant at the call site, not
a config knob.

**Extend:**

- **`generate` must take a system prompt.** Today it is `generate(*, prompt: str, …)` collapsing
  to one user turn. A planner and a verifier need different personas, and folding them into the
  prompt string hides the structure inside the request digest. `story/contracts.py` declares
  `generate(*, system: str, prompt: str, schema: dict, schema_name: str, max_tokens: int,
  temperature: float)`.
- **`schema_name` must reach the wire.** It is currently hard-coded to `"extraction_claim"` for
  every protocol-path call.
- **Schema violation coverage.** The hand-rolled checker covers `type`, `required`,
  `properties`, `additionalProperties: false`, `enum`, `items` and *silently ignores* everything
  else. §15.3 makes that survivable by restricting the schemas rather than widening the checker.
- **Manifest provider block.** Extraction records `model_id` and `context_tokens` only. For an
  agent whose output is prose, `prompt_version`, `temperature`, `max_tokens`, `base_url` and
  the schema digest are exactly what a reader will want.
- **Provider selection.** `config.provider.kind` exists and is never dispatched on. Copy
  `build_scope`'s pattern (`extraction/context.py:93-103`): a recognised value or a typed raise
  at construction, never a fall-through to a default.
- **Config layering.** Extraction has no `.env`/environ overlay. `graph/stages/load/connection.py:186-211`
  is the tested pattern to lift when a keyed provider arrives. **No API key is hardcoded, ever;
  the `.env` convention already exists and `.env.example` is tracked with a blank value.**

**Must be new:** a reranker (nothing exists; `STAGE_09` §10 explicitly excluded it, and it
would need a third server, a third model and a third port — the honest first move for relevance
ordering is fulltext score plus deterministic filters); multi-turn state, if the agent ever
becomes a loop; a token-budget helper at the boundary (`output_budget()` lives inside
`narrative_lane.py` and is unreachable).

### 15.2 Why `story/providers/` is a copy, not an import

Every top-level package in this repository is self-contained; the only cross-package import is
`ontology`, and even that is deferred inside a function. `graph/`'s structural test allows only
`extraction.core.`, `ontology.core.` and `ontology.contracts` as upstream surfaces —
`extraction.providers` and `extraction.contracts` are **not** shared surfaces, and
`GenerationResult` lives in the latter. `extraction/core/config.py:34-43` already records the
repository's answer to exactly this trade: re-state six lines rather than reach across a
boundary.

So `story/contracts.py` declares its own `GenerationResult`, structurally identical, and
**`tests/story/test_provider_contract.py` asserts the two field sets match** — the repo-native
way to prevent drift without an import. The transport half (~90 lines of retry, error taxonomy,
health, hashing) is duplicated knowingly and the duplication is named in the module docstring.

Whichever shape is chosen, the `providers/__init__.py` lazy-import indirection
(`extraction/providers/__init__.py:44-56`) must be preserved, or the "no HTTP client reachable
from a stage" guarantee reverts to being true only on paper.

### 15.3 Structured output on the local runtime — the portable subset

llama.cpp's JSON-Schema-to-GBNF conversion **skips unsupported keywords silently**, and the
repository's own `schema_violations` ignores anything outside its six keywords. So a schema
using `minimum`, `pattern`, `anyOf` or `$ref` would be neither enforced by the grammar nor
caught by the local check — it would simply not apply.

**Every schema in §11, §12 and §13.16 is restricted to: flat `properties`, `type`, `required`,
`items`, `enum`, `additionalProperties: false`.** Numeric bounds are expressed as `enum` or
re-checked by the adjudicator. `anyOf`, `oneOf`, `$ref`, `prefixItems`, `pattern`,
`minItems`/`maxItems` and `format` are prohibited, and a test asserts every shipped schema uses
only the permitted keywords. Every property inside an object is `required` — an optional
property is one the model silently omits on the hard cases.

Request shape: `response_format: {"type": "json_schema", "json_schema": {"name": ...,
"strict": true, "schema": ...}}` — the **nested** form. The llama.cpp server README documents a
top-level `schema` variant that the parser does not read, so it applies no constraint at all
and does so silently.

Determinism defaults: `temperature = 0.0` everywhere; `max_tokens` is a determinism input
because it enters the request digest, not a budget knob; retries only on transport faults;
schema failure is a result, not a retry.

---

## 16. Security and query limits

**The agent is read-only against Neo4j, and in V1 that is an application-level property.**

| Control | Status |
| --- | --- |
| Read-only database user | **Impossible in Community.** `SHOW ROLES` → `UnsupportedAdministrationCommand`; `SHOW USERS` → `roles: null` *(measured live 2026-08-03)*. Every Community user has write and admin-procedure capability |
| Code-owned parameterised Cypher | **The primary control.** No tool interpolates a caller value into a query string; no generated Cypher exists (§3) |
| Query allowlist | Labels and relationship types enumerated per tool (§9); `:Issue` reachable from two entry points only; `OBSERVATION_OF_SUBJECT` never traversed outward |
| Max rows | Per-tool, declared, with `truncated: bool` returned rather than a silent cap |
| Max path length | 2 hops, structurally — no tool contains a variable-length pattern |
| Query timeout | `neo4j.Query(text, timeout=…)` on every statement, from `config/story.yaml` |
| No APOC | None of `db.index.fulltext.*` or `db.index.vector.*` needs it |
| No write clauses | Enforced by a test that greps **every Cypher constant in `story/`** — not just `stages/retrieval/` — for `CREATE\|MERGE\|SET\|DELETE\|REMOVE\|DROP\|LOAD CSV`. The first draft scoped this to `retrieval/` while §7's freshness gate also issues Cypher, so the plan's primary read-only guarantee did not cover the stage it says ships first |
| No schema discovery from user input | No tool takes a label or property name as a parameter |
| Logging | Query name and parameter *keys* logged; parameter values and `NEO4J_PASSWORD` never. `SecretStr` already masks the password in tracebacks (`connection.py:135-145`) |

**Server-side controls, deferred with reasons.** `server.databases.read_only=<db>` is available
in Community and is genuine write refusal — but it applies to everyone, so the ingest pipeline
would need the setting flipped and a restart. The clean separation is a read-only serving
instance restored from the ingest instance, and that is a milestone, not a V1 task.
`db.transaction.timeout` and `db.memory.transaction.max` are the cost backstop that bounds a
*read-only* runaway query, which application controls do not; both are `neo4j.conf` settings
requiring a restart, because dynamic configuration change is itself Enterprise-only. **Recorded
as open decision D5.**

`RoutingControl.READ` is set on every call. **Correction, measured 2026-08-03 at S0c against
the live 5.26.28 instance:** this plan called it "intent, not a control … on a single Community
instance it enforces nothing". That is too pessimistic. A write clause issued under
`routing_=READ` is refused **by the server** with
`Neo.ClientError.Statement.AccessMode: Writing in read access mode not allowed` — and the
identical statement succeeds under `routing_=WRITE`, so the refusal is access mode and not the
statement. It is a real per-transaction write barrier.

What the driver documentation's caveat does still mean: it is not access *control*, because
nothing stops code from choosing `WRITE` routing. So story has **two** independent controls, not
one — the structural scan proving no story Cypher contains a write clause, and the server
refusing one at runtime if it ever did.

**16.4 If bounded text-to-Cypher is ever added** (§3, option B): explicit
`--allow-generated-cypher`; `EXPLAIN` first with `summary.query_type == "r"` enforced; a
keyword and procedure blocklist as defence in depth; `Query(text, timeout=…)`; a `LIMIT`
injected into the plan; the generated query printed before it runs; and **unreachable from
`story run`**. It must never be a fallback when a tool fails.

---

## 17. Adversarial pass — how a plausible draft beats a naive verifier

Each one uses this corpus specifically, and names the check that catches it.

1. **Right number, wrong margin.** *"Gross margin was 15.4% in Q4 2020."* Both
   `gaap_gross_margin` and `adjusted_gross_margin` are `15.4` at `2020Q4` — and both are `13.0`
   at `2021Q1` and `8.4` at `FY2024`. A number-first verifier passes either. → §13.5 rule 3;
   the number check cannot catch this, which is why binding is explicit.
2. **Right number, wrong period shape.** *"Adjusted EBITDA was $183 million in the period ended
   September 2022."* True of the nine-month YTD; the same `period_end` also holds
   `2022Q3 = −$211M`. The natural reading has the opposite sign. → §13.4 endpoint-pair
   equality; 188 `(metric, period_end)` pairs are exposed.
3. **Percentage-point laundering.** *"Adjusted EBITDA margin improved 3% year over year."*
   Both levels are real and `5.2 − 2.2 = 3.0` is real arithmetic, so a verifier that recomputes
   the delta passes it — but the sentence reads as +3% relative, which is `2.266`. → §13.3
   surface gate, applied before the arithmetic is consulted.
4. **The rounding conflict that hides a real one.** A rule that always suppresses rounding
   conflicts would equally suppress a 27-vs-44. → §13.12's precision-relative materiality test
   is the only rule that gets both right.
5. **Citing a passage that contains a better number.** *"Contribution margin was 4.0% in Q4
   2021"* citing a passage reading *"…was 4.0% versus 12.6% in 4Q20."* Every deterministic
   check passes; the sentence is a selective read of a span whose whole point is the collapse.
   → §13.16 `counter_evidence_misrepresented`, which is shown the adjacent-period values.
6. **"Following" laundered into "because of".** The event's `reason` property says the
   pandemic, so a verifier checking "is the cause in the package?" passes — but the *filing*
   says "following", and `reason` is the extractor's field, not the filing's word. → §13.10
   condition 2.
7. **A number for a metric with zero observations.** *"Revenue fell to $1.2 billion."*
   `revenue` has a `:Metric` node, a label, an external mapping and **170 `CONCERNS_METRIC`
   edges** — which a naive verifier reads as 170 supporting passages. They are 170 places
   revenue was *refused*. → §13.1 step 2 plus an explicit `unpopulated_metric` code so the
   rejection says why.
8. **A right fact from a wrong run.** *"Opendoor operated in 44 markets at end-March 2021."*
   A real node in `graph-v1-886059d862ce` with a real passage, a real quote and a real
   document — and, in the run that superseded it, **44 belongs to `2021-12-31`, not
   `2021-03-31`**, because F0's multi-header fix re-dated it. Every check in §13.1–§13.12
   passes on the stale node. → **§7 alone catches it.** This attack is no longer hypothetical
   in either direction: it was live when written, and the fix that resolved it is exactly the
   kind of upstream correction that will happen again.
9. **Resurrecting a refused reading.** A `(metric, period, passage)` identity the extractor
   refused as `AMBIGUOUS_COLUMN_ALIGNMENT` can be satisfied from a *different* passage's value.
   → §13.13, reusing `refused_identities`.
10. **Naming the unnamed borrower.** *"In October 2022, Opendoor entered a $525 million
    facility."* Every fact is real; the borrower is an unresolved subsidiary. → §13.11 rule 2.
11. **A hedge doing work.** *"…approximately 550 employees, roughly 18% of its workforce."*
    Both strings are exact substrings of the event properties and of the passage, but neither
    is a typed value and "approximately" is the *filing's* word, not a licence to round. →
    §13.8: event properties may only be quoted verbatim.
12. **Unbounded context by accident.** A retriever returning `node { .* }` on `:Passage` ships
    the full text plus eight provenance properties per hit. → §9's named field lists.
13. **A post writer selecting its own evidence.** `build_story_evidence_package` is not a tool
    (§9) and neither the planner nor the writer has any tool (§11, §12). **But "no tools" and
    "no influence over the evidence set" are not the same property**, and the first draft's own
    D3 answer breached the second: giving the writer "only the passages the plan cites" let a
    model filter the next model's universe, since `required_citation_passage_ids` is model
    output. → §10.2.1 point 3 — **the writer's passage set is derived from fact bindings by
    code.**
14. **Silent omission of counter-evidence.** §11 requires `counterpoints` to be non-empty when
    `counter_evidence` is. **As first drafted that was satisfiable with one ungrounded
    sentence**, §15.3 could not express "non-empty" in the schema at all, and no §13 check
    required a counterpoint to survive into the draft. → §11's three additions, of which
    `required_counterpoint_absent` (REFUSE) is the one that actually closes it.
15. **Mutable model output in the factual graph.** → §14: the story package never writes to
    Neo4j and never into `data/extraction_runs/` or `data/graph_runs/`.
16. **An unreproducible post.** → §14's `generations.jsonl` replays by request digest, and the
    manifest pins the package identity, prompt version, model, temperature and schema digests.
17. **Depending on data that does not exist.** → §6.5's status table and §9's three
    `Unavailable(reason)` tools; a detector whose `REQUIRED_FACTS` are unmet is skipped with a
    recorded reason rather than silently absent.
18. **A false sentence carrying no number.** *"The only quarter with a negative adjusted gross
    margin"* — it is negative in two. Nothing in the first draft's §13 constrained a
    numeral-free, non-causal `connective` sentence. → §13.14, the largest change the review
    forced.
19. **A causal claim the filing negates, or joins differently.** 37 passages carry a negated
    causal construction and 367 carry two or more markers; the first draft's four conditions
    were co-presence tests. → §13.10 conditions 5 and 6.
20. **A published post silently invalidated by a graph rebuild.** §7 guards forward only. →
    `story recheck` and `retractions.jsonl` (§7).
21. **Architecture too large for a prototype.** → measured against the repository, eight stage
    directories is exactly `extraction/`'s count and above the mean (§5). The live question is
    fourteen implementation stages against zero lines of code, and §23 answers it: L0–L6 and L9
    need no model server, and L0 alone has standalone value.

---

## 18. Defects this plan discovered in existing work

None is fixed here; each is named so it is not rediscovered.

| Defect | Where | Severity |
| --- | --- | --- |
| Loaded graph is stale against its extraction inputs; no check compares the recorded `run_complete_sha256` on the way back in | `graph/core/manifest.py:75-101` computes it; nothing reads it back | **blocks every story stage** |
| `extraction_run_id` is not an identity — the directory was regenerated under the same id | `extraction/core/run_directory.py` | high; makes id-based consistency checks vacuous |
| `formulas.yaml`'s `adjusted_gross_profit` v2 expression omits the prior-period cohort term, so `adjusted_gross_margin < gaap_gross_margin` in 16 of 26 quarters | `ontology/versions/…/definitions/formulas.yaml` | high; invalidates any ordering-based consistency detector |
| ~~`guidance_issuance.inference_restrictions` demands an `assertion_type` that `AssertionType` does not offer~~ | ontology | **CLOSED at F0** — `AssertionType.GUIDED` + `forbidden_assertion_types`, enforced and tested |
| ~~`value: float` cannot hold a guided range~~ | ontology / events | **CLOSED at F0**, differently and better: the range is typed on `guidance_issuance.properties`. **But a qualitative band text ("mid-single digit") still has no field** — the contract permits qualitative guidance only as the *absence* of both bounds, so the phrase itself is unstorable | latent, blocks a faithful D10 |
| Narrative lane rejects billions-scale figures: `"$12.6 billion"` refused as `VALUE_CONTRADICTS_QUOTED_TEXT` with `"value 12600 is not the magnitude printed"` | extraction narrative lane | blocks D7 |
| ~~No future-period guard exists anywhere~~ | `ontology/core/constraints.py` | **CLOSED at F0** — `check_future_period`, carrier-relative. **But it abstains on the entire current corpus**: `reported_at` is populated by no lane and the check returns early without a carrier date. The guard is a contract, not yet a property of the data | latent |
| `inventory` resolves to `housing_inventory_homes` and `inventory_balance` but is in **no** `mutually_distinct_group`, so `check_alias_collisions` never fires on it | `constraints.yaml` | medium |
| `adjusted_ebitda` and `adjusted_ebitda_margin` declare no `distinct_from` at all; 13 of 26 metrics have none | `metrics.yaml` | medium; §13.5 must union `distinct_from` with group membership |
| `AMBIGUOUS_COLUMN_ALIGNMENT` identities are recovered by regexing prose out of `Issue.detail` because `concept_ids` is `[]` on all five | `graph/stages/load/verification.py:225` | inherited fragility |
| `graph/context.py:8-15` still argues at length that "there is no `config/graph.yaml`" — there is | docstring | minor |
| Three `HOLDS_POSITION_AT` rows carry digest suffix `0365d72eac21`. **This plan overstated it: the ids are unique** (they differ in the `{source}` segment) and `duplicate_identities` passes 25,321/0. The digest is a passage discriminator by design. What remains is that the digest segment carries no discrimination, so it reads like identity and is not | `extraction/core/identifiers.py:343` | low; a readability wart, not a collision |
| `contribution_profit` has no `ambiguities` and `population: None`; its cohort caveat exists only as a `formulas.yaml` `adjustment_components[].note` | `metrics.yaml` | medium; blocks half of §13.15's `lost_qualifier` |
| `executive_change` has no seniority field — `allowed_properties: [change_kind, position, effective_date]`; seniority must be inferred from free-text `position` | `events.yaml:197` | medium; §6.6 D9 carries a story-owned lexicon instead |
| `README.md` says 717 tests; 2,567 collect offline here and 2,698 on `main` | `README.md:19` | minor |

---

## 19. Interactive research mode

`python -m story ask "How did inventory aging change?"` — a **secondary** workflow, not a chat
UI, and not part of the acceptance criteria.

The model's only job is routing: it emits a structured tool call — tool name plus typed
parameters, schema-constrained — against §9's fifteen tools. Code executes it. The answer
renders in four labelled blocks that are never merged:

```
FACTS          rows from the graph, each with metric, period, value, unit and its id
EXPLANATION    passage excerpts, each with passage_id, document form and filing date
WARNINGS       ambiguities, conflicts, single-source, unresolved entities, staleness
MISSING        what the graph does not hold, and the refusal that records why
```

`MISSING` is not an error path. When a question needs guidance or a stock price, the answer is
the `Unavailable(reason)` from §9 plus the relevant `issues.jsonl` refusal — *"the graph holds
no guidance data; the narrative lane refused two forward-looking passages as
`GUIDANCE_NOT_REPORTED`"*. **The model may never answer from its own memory**, and a routing
response that produces no tool call renders `MISSING` rather than prose.

Research mode uses no generated Cypher (§3) and shares §16's controls exactly.

---

## 20. The CLI

```
python -m story [--root REPO] [--runs-root R] doctor
python -m story [--root REPO] [--runs-root R] discover  [--detectors D,...] [--since S] [--until U] [--limit N]
python -m story inspect   [STORY_RUN_ID]
python -m story runs
python -m story candidate CANDIDATE_ID [--run STORY_RUN_ID]
python -m story package   CANDIDATE_ID [--run STORY_RUN_ID]
python -m story plan      CANDIDATE_ID [--run STORY_RUN_ID]
python -m story draft     CANDIDATE_ID [--run STORY_RUN_ID]
python -m story verify    [STORY_RUN_ID]
python -m story recheck   [STORY_RUN_ID]
python -m story run       [--limit N] [--candidates ID,...]
python -m story ask       "QUESTION"
python -m story issues    [--code C] [--limit N]
python -m story rejected  [--code C] [--limit N]
python -m story report    [STORY_RUN_ID] [--write]
python -m story rebuild   [STORY_RUN_ID]
```

argparse; top-level options precede the subcommand; long kebab-case flags only, no short flags;
comma-joined lists rather than `nargs="+"`; `EXIT_OK/EXIT_FAILED/EXIT_USAGE = 0/1/2`; no
`--json` flag — `inspect`, `package`, `plan` and `draft` print JSON unconditionally because they
*are* documents, with `indent=2, sort_keys=True, ensure_ascii=False`.

**Exits non-zero when:** `doctor` finds any staleness; `discover` produces zero candidates *or*
a detector is skipped for unmet requirements without `--allow-skipped`; `verify` has any
failing check — printing **every** failure, not the first, because a draft wrong in two ways is
exactly the case a first-failure exit would hide; **`plan` or `draft` produces an artifact the
next stage would reject** (§11's unresolvable-id case, which the first draft left with no
defined exit behaviour); `run` ends with any draft rejected; `recheck` finds any accepted post
affected by a graph change; `rebuild` finds a byte difference; `report` differs from the
committed file without `--write`. Typed domain refusals print one line and never a traceback.

**A note on `plan` and `draft` failing.** With `temperature = 0.0` and a deterministic request
digest, re-running produces the identical rejected artifact, and §27 D7 forbids a repair loop —
so the operator's only moves are to change the package (widen the candidate, fix the graph) or
to change a prompt version. That is the intended behaviour and the exit code says so; it is
recorded because "the pipeline has no defined state here" was a real gap.

**Naming notes.** `discover` is a soft collision with `python -m acquisition discover`;
accepted, because `run`/`runs`/`inspect`/`verify`/`report` already repeat across packages by
design. `ask` is chosen over `research ask` — one word, imperative, matching the house style.
Deliberately avoided: `generate`, `write`, `publish`, `chat`, `serve` — none matches the "one
call into `pipeline` or one read of a finished directory" contract every `cli.py` states, and
`publish` collides with an existing out-of-scope item.

---

## 21. Testing

Fake and recorded providers in the normal suite; live tests file-wide `pytestmark` in
`*_live.py`; Neo4j tests per-test `@pytest.mark.neo4j`; a session `pytest.skip` for the
gitignored real run, with the run id **pinned, not discovered**.

| Area | Tests |
| --- | --- |
| Structure | no forbidden import reachable from `story/` (the same AST closure walk); **`neo4j` confined to `stages/retrieval/` and `stages/freshness/`** — two stages, because §7's gate reads the `:GraphLoad` marker, and the first draft's one-stage rule would have made L0 unbuildable without failing L1's own test; `httpx` confined to `providers/`; `graph/` never imports `story`; `contracts.py` imports only typing and `story.core.models`; no stage imports another; no catch-all module names; the stages package holds exactly the stages that exist |
| Cypher tools | every constant is parameterised (no f-string, no `%`, no `+` into a query); no write keyword in any constant; every statement carries a timeout; `max_rows` enforced and `truncated` returned; Lucene escaping over the four hostile surfaces; `OBSERVATION_OF_SUBJECT` never traversed outward; `:NotAttempted` excluded |
| Determinism | candidate ids stable across runs and over input reordering; a `detector_version` bump mints a new id; package digest stable; two projections of one package are byte-identical |
| Detectors | fixture-driven, one committed real slice per detector; thresholds asserted against the measured distributions in §6.6; D17 residual is exactly zero on the six overlapping quarters |
| Ranking | ordering stable and total; `ambiguity_count` is not a score term; `magnitude_z` is null below 8 points |
| Packaging | every bound in §10.2 enforced; token estimate never exceeds the cap; every fact resolves to a passage to a document to a URL |
| Verification | one test per code in §13; `"$27.1 million"` vs `−27075000.0`; `"(341)"`; `5.2 → 2.2` as pp and as relative; `"improved 3%"` refused; quarter-vs-YTD refused; `"gross margin"` refused as ambiguous; a causal marker outside the cited span refused; `opendoor_unnamed_subsidiary` refused as a name; a conflicted slot without disclosure refused; **a package whose `run_complete_sha256` disagrees refused** |
| Model layer | malformed structured output; a schema violation is not retried; a finding whose spans do not occur is discarded, not passed; provider timeout not retried; provider unavailable is a structured state |
| Replay | a full `story run` reproduces byte-identically from `generations.jsonl` with the provider unreachable |
| Version mismatch | graph run mismatch, ontology hash mismatch, embedding version mismatch (once §8 lands) each refuse with their own code |
| End-to-end | the F1–F3 spike, offline, from committed fixtures and a committed answer store |

Every schema shipped uses only the §15.3 keyword subset, asserted by a test.

---

## 22. Evaluation

Ten dimensions, scored **separately**. No composite quality score — a single number would let a
readable post with a wrong denominator outrank a plain one that is right.

| Dimension | Measure |
| --- | --- |
| Factual precision | fraction of bound numerals that survive §13.1–§13.4 on an independent recheck |
| Citation precision | fraction of citations whose span actually supports the sentence, human-judged |
| Citation completeness | fraction of material factual sentences carrying a citation |
| Candidate interestingness | founder 1–5 on the ranked list, before any draft exists |
| Thesis quality | founder 1–5 on the editorial plan alone |
| Novelty | overlap with the last N accepted posts, and founder judgment |
| Counter-evidence handling | was material counter-evidence represented, omitted, or misstated |
| Causal restraint | count of causal constructions, each classified LLM-originated / reported / hedged |
| Readability | founder 1–5 |
| Duplication | pairwise candidate-overlap across accepted posts |

The gold set is small and honest: **the F1–F3 spike plus five founder-labelled
accept/reject drafts, including at least two deliberately wrong drafts drawn from §17** — a
percentage-point laundering and a stale-run fact. A verifier benchmark with no negatives
measures nothing.

Founder gates are at L3 (are these candidates worth writing about?), L7 (is this thesis right?)
and L11 (would you publish this?). The rubric is one page and every row is one of the ten
dimensions above.

---

## 23. Implementation stages

| Stage | Goal | Depends on | Parallel-safe | Founder gate |
| --- | --- | --- | --- | --- |
| **L0** | Package skeleton, `config/story.yaml`, structural tests, **the staleness gate (§7)** and `story doctor` | — | **yes** | — |
| **L1** | The fifteen retrieval tools (§9), read-only, bounded, timed, with `Unavailable` states | L0 | **yes** | — |
| **L2** | Canonical series + comparability (§6.1, §6.9); fulltext `search_passages`; **embeddings deferred, decision D4 taken here** | L1 | **yes** | — |
| **L3** | `StoryCandidate` + D1, D2, D3, D4, D8, D9, D15, D16, D17 | L2 | **yes** | **candidate list** |
| **L4** | Deterministic ranking and dedup (§6.10) | L3 | **yes** | — |
| **L5** | `StoryEvidencePackage` builder, bounds, hashing, reproducibility (§10) | L4 | **yes** | — |
| **L6** | Provider abstraction + structured-output client + `AnswerStore` replay (§15) | L0 | **yes** | — |
| **L7** | Editorial planner (§11) | L5, L6 | needs the local server | **thesis** |
| **L8** | Constrained writer (§12), style profile separated from facts | L7 | needs the local server | — |
| **L9** | Deterministic verifier and the gate (§13.1–§13.15, §13.17), incl. `story recheck` | L5, L8 | **yes** | — |
| **L10** | Model-assisted verifier and adjudicator (§13.16) | L9, L6 | needs the local server | — |
| **L11** | End-to-end `story run` on F1–F3, offline replay, `report.md` | L9, L10 | **yes** | **publish?** |
| **L12** | Interactive `story ask` (§19) | L1, L6 | **yes** | — |
| **L13** | Evaluation harness, gold set, founder rubric (§22) | L11 | **yes** | — |

Each stage ends with a green offline suite and its own narrow commit. **Rollback boundary:**
every stage is additive within `story/`; nothing outside `story/`, `config/story.yaml`,
`tests/story/` and `plans/llm-agent/` is touched at any stage, so any stage can be reverted by
reverting its commit.

**L0–L6 and L9 need no model server at all.** That is most of the work, and it is the answer to
"fastest convincing local prototype": the deterministic spine is buildable and testable
offline, and the model is added last to two well-bounded call sites.

---

## 24. Parallelism with the factual-spine workstream

The other session owns `extraction/`, `ontology/`, `graph/`, `normalization/`, `config/` (except
`story.yaml`) and `plans/factual-spine/`. This workstream owns `story/`, `config/story.yaml`,
`tests/story/` and `plans/llm-agent/`. **There is no shared file to edit** — including the
structural tests, because the "graph must never import story" assertion lives in
`tests/story/test_story_package_structure.py` and walks `graph/` from there rather than
extending `tests/graph/`.

| Safe to build now | Depends on factual-spine work |
| --- | --- |
| Staleness gate, retrieval tools, canonical series, comparability, D1–D4/D8/D9/D15–D17, ranking, evidence-package schema, citation model, provider abstraction, deterministic verifier, CLI skeleton, fake-provider harness, evaluation harness | D10/D11 (**guidance lane only** — the contract landed at F0), D12 (price acquisition stage), D7 (billions-scale fix + facility events), D13 (lexical projection), D14 and peer divergence (entity resolution + a second subject), §13.7.2 Rule C (an XBRL or market-data lane), §8 embeddings (unaffected) |

**Integration boundary.** The story package consumes only: the graph export directory, the
`:` labels and relationship types in §9's allowlist, the ontology through `ontology.core.`, and
`extraction.core.` for period-key derivation. New metrics, new events, new lanes and new
documents arrive as **more rows under the same contract** and require no story-side change —
that is the point of §6.8's `REQUIRED_FACTS` mechanism.

Three changes on the other side **would** require coordination, and each is worth a note now:
a new node label or relationship type (extend §9's allowlist), a change to
`assertion_type`'s members (§6.8 D10 depends on one being added), and a change to the
observation value model to carry ranges (same). None is a file conflict; all three are contract
changes, and `WORKSTREAM_BOUNDARY.md` is where they get recorded when they happen.

---

## 25. Out of scope

| Out of scope | Why |
| --- | --- |
| Automatic publishing anywhere | Already out of scope in the factual-spine plan; a verified draft is a file |
| A chat UI or a web server | §19 is a CLI verb. A UI is a different project |
| Unrestricted text-to-Cypher | §3 |
| Writing anything to Neo4j | §14. The graph is rebuilt per run and holds no story state |
| Embeddings, in V1 | §8, with the design recorded for L2+ |
| A reranker | §15.1. Needs a third server and a third model to reorder ~25 fulltext hits |
| Multi-company or peer comparison | §13.6. One subject exists |
| Streaming generation | A verifier needs a whole draft; a partial draft is not a partial post |
| Resumable story runs | The repository has an argued refusal of resume (`run_directory.py:97-108`); a story run is minutes, not hours |
| Fine-tuning or training anything | The whole design is retrieval and constraint |

---

## 26. Rejected options

| Option | Why rejected |
| --- | --- |
| `neo4j-graphrag` as a dependency | Core install requires `numpy` and `scipy`; `STAGE_09_HYBRID_SCOPING` §1 already rejected exactly that, with the argument written down. Its query shapes are borrowed as code instead (§4) |
| LlamaIndex property graph | `llama-index-graph-stores-neo4j` pins `neo4j>=5.16,<6`, a hard conflict with the repository's `neo4j>=6.2,<7`. Its store also assumes `__Node__`/`__Entity__` labels and calls `apoc.meta.data` on refresh |
| Microsoft GraphRAG | Does not read Neo4j at all. Parquet + LanceDB, unconditional `azure-*` dependencies, Python ≥3.11, and it re-derives communities with an LLM — discarding the determinism this graph exists to provide |
| Graphiti | Hard-codes `Entity`/`Episodic`/`RELATES_TO` with no label mapping; `Neo4jDriver.__init__` schedules 31 `CREATE INDEX` statements on construction and `build_indices_and_constraints(delete_existing=True)` drops **every** index in the database. `openai` and `posthog` are hard dependencies |
| Embeddings stored on `:Passage` nodes | Changes 8,776 `content_digest` values and breaks `graph verify` (§8) |
| A `thesis_hypothesis` field on `StoryCandidate` | It is the seam through which a detector's guess becomes a post's claim (§6.4) |
| An LLM ranker, or an LLM tie-breaker | A model that orders candidates is a model choosing its own evidence one step earlier (§6.10) |
| `build_story_evidence_package` as a model-callable tool | A model that can call it can widen its own universe (§9) |
| Matching a draft's numbers to facts by search | Ambiguous 62.5% of the time at two significant figures. The draft declares its bindings and the verifier checks them (§12) |
| Per-sentence rejection | Invites the generator to delete the offending sentence rather than fix it (§13.17) |
| A prototype database (SQLite, DuckDB) for agent state | Every other stage uses a directory of JSONL; it diffs and needs no process (§14) |
| A shared `cli_common.py` | The repository triplicates `_banner` and quadruplicates `EXIT_*` on purpose (§1.6) |
| Importing `extraction.providers` | Would be the repository's first cross-pipeline import; `extraction.contracts` is not a shared surface (§15.2) |
| `python -m story publish` | Collides with an existing out-of-scope item and implies an action this system does not take |

---

## 27. Open decisions

| # | Decision | Recommendation | Alternatives and consequences | Latest stage |
| --- | --- | --- | --- | --- |
| **D1** | Package name — `story/` | **`story/`** | `narration/` reads oddly as a module; `agent/` is a catch-all name saying nothing about what it produces | **before L0 (blocking)** |
| **D2** | Rebuild the graph before any story work? | **Yes** — the staleness gate refuses it anyway, and every §17 check except §7 passes a stale fact | Proceed on the stale graph and treat §7 as advisory: §17.8 shows a real, citable, retracted fact would publish | **before L1 (blocking)** |
| **D3** | Package token budget vs the running server's 8,192-token context | **RESOLVED by measurement (§10.2.1).** Primary passages 2–4 (ceiling 6); explanatory and counter-evidence excerpted to ±400 characters; planner and writer get different slices, and **the writer's passage set is derived from fact bindings by code, never from the plan's `required_citation_passage_ids`** | The first draft's answer (8 primaries, writer sees only what the plan cites) both overran the context at every percentile and let a model filter the next model's universe. Restarting llama.cpp with a larger context remains available and costs VRAM on a 16 GiB card | **taken at L5** |
| **D4** | Embeddings in V1 | **No** — §8's three reasons | Add them and either break `graph verify` or accept a sidecar store plus pure-Python cosine at seconds per query, before fulltext has been measured to be insufficient | **at L2** |
| **D5** | Server-side read-only enforcement | **Defer** — application controls plus `db.transaction.timeout` in `neo4j.conf` | A read-only serving instance is the only real answer and it is a milestone; Community offers no read-only user at all | **before first non-local use** |
| **D6** | `audience: internal` candidates (D15, D16) in the same run as external ones | **Same run, flagged** — they are the honest data-quality output and a separate run would hide them | Separate runs; they would stop being read | **at L3** |
| **D7** | Does a `RejectedDraft` trigger an automatic repair loop? | **No in V1** — write the rejection, stop. The `remedy` enum makes a loop possible later | A repair loop risks the generator learning to satisfy the checker rather than the evidence | **at L9** |

**Not a founder decision, recorded so it is not mistaken for one:** the story package holds no
state of its own between runs, writes nothing to Neo4j, and can be deleted and rebuilt from the
graph run plus `config/story.yaml` plus the committed answer store.

---

## 28. Documents to correct

Not part of any stage; listed so the sweep is not forgotten.

- `README.md:19` — "717 tests"; **2,723** pass offline as of F0, 0 skipped.
- `graph/stages/load/loader.py:76-77` — doc-comment still says `Warned 186, Rejected 46` and
  `FOUND_IN 17,127 · EVIDENCED_BY 2,713 · HAS_OBSERVATION 2,707`. Now 185 / 49 / 17,130 /
  2,710 / 2,704.
- `graph/stages/load/schema.py:126-127` — still cites the retired `graph-v1-886059d862ce`.
- `plans/factual-spine/F0_IMPLEMENTATION_REPORT.md` — worth recording that
  `check_future_period` abstains on the entire corpus, because `reported_at` is populated by no
  lane. The guard is correct and currently fires on nothing.
- `graph/context.py:8-15` — argues at length that `config/graph.yaml` does not exist. It does.
- `plans/graph/V1_GRAPH_PROTOTYPE.md:1329` — G6's scope is superseded by this plan; add a
  pointer rather than rewriting it.
- `plans/factual-spine/V1_OPENDOOR_FACTUAL_SPINE.md` §8.1 — states that of 42 conflicted slots,
  36 are thousands-vs-millions rounding and 6 are `scale: null` vs `units`. Against the current
  run there are **36 slots, 10 above 1% spread, and all 10 are rounding**; the "6" were period
  resolutions that the 2026-08-03 re-run already fixed.
- `config/graph.yaml` — worth a note that `schema_version` says nothing about whether the
  loaded data still matches its inputs (§7).
