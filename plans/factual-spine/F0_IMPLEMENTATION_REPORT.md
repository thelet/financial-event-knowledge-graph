# F0 — implementation report

What was built against [F0_CONTRACT_EXTENSION.md](F0_CONTRACT_EXTENSION.md), what it measured,
and what it changed. Written 2026-08-03 on `impl/f0-factual-spine-contracts`, base `df50be9`.

**F0 is complete.** No later stage was started: nothing ingests XBRL, transcripts or prices, no
provider call was issued, and no guidance is extracted.

---

## 1. The headline

| | Before | After |
| --- | ---: | ---: |
| Observations | 2,707 | **2,704** |
| Claims / evidence rows | 2,717 | 2,714 |
| Issues | 17,127 | 17,130 |
| Rejected claims | 46 | 49 |
| Graph nodes / edges | 28,836 / 35,603 | 28,836 / **35,600** |
| Offline tests | 2,530 | **2,720** passed, 1 skipped, 0 failed |
| Neo4j tests | 37 | 37 passed |
| Graph verification | 27/27 | **27/27** |
| Ontology `definition_hash` | `e8d4af709be2…` | `337e0e595 34d…` |
| Graph projection version | 1.1.0 | 1.2.0 |
| Extraction run | `…-2422c4252c07` | `…-7f72d6172630` |
| Graph run | `…-886059d862ce` | `…-eeba753149e9` |

---

## 2. Part A — the six defects, and the two more the fix caught

### Root cause: two bugs, not one

**Table lane.** Colspan does not survive normalization (`normalization/stages/parse/tables.py:43`
records a `has_merged` boolean and discards the span), so
`header_analysis._assign_groups` reconstructs the binding. Its even-division rule split
`open-20220331.htm#p122`'s four date columns 2+2 while the headings sat at columns 6 and 8 and
the true split was 1+3 — emitting the Dec-31-2021 market count of 44 as instant `2021-03-31`.

Positional binding reads that table correctly but runs last, deliberately: the Q4 2020
reconciliation is bound *wrongly* by position and *correctly* by even division, and from the
collapsed grid the two layouts are indistinguishable.

**The discriminator is the date labels.** Two column groups shown side by side must repeat their
periods (`2020 2019 2020 2019`); a single descending run of distinct labels cannot be two
parallel groups. Measured across all 1,935 table passages: the rules disagree on **92** tables —
**87 repeating** (even division correct) and **5 distinct** (positional correct). The 5 are the
*Expansion into New Markets* table in each Q1 10-Q.

**Narrative lane.** The other three defects come from one shareholder-letter passage typed
`narrative` whose text is a flattened seven-period grid. Not fixable in header analysis, and
editing the recorded answer would make the replay store a fiction — so it became a refusal,
`PERIOD_NOT_GROUNDED_IN_PASSAGE`, raised when the span a period is *grounded on* falls inside a
run of ≥3 period phrases separated only by whitespace.

Checked against the grounded span rather than the passage, which is what keeps the same
document's `#p9` alive: its correct 18% is grounded at char 222 in prose while the chart run
starts at 1939. **A passage-level rule would have removed a correct observation.**

### Before / after

| Metric | Period | Was | Now | Passage |
| --- | --- | ---: | --- | --- |
| `market_count` | 2021-03-31 | 44 | **re-dated to 2021-12-31** | `open-20220331#p122` |
| `market_count` | 2022-03-31 | 53 | **re-dated to 2022-12-31** | `open-20230331#p109` |
| `market_count` | 2023-03-31 | 50 | **re-dated to 2023-12-31** | `open-20240331#p112` |
| `market_count` | 2023-12-31 | 53 | **removed (refused)** | `…sharehol#p20` |
| `pct_homes_on_market_gt_120_days` | 2023-12-31 | 55 | **removed (refused)** | `…sharehol#p20` |
| `housing_inventory_homes` | 2023-12-31 | 12,788 | **removed (refused)** | `…sharehol#p20` |
| `market_count` | 2020-03-31 | 21 | **re-dated to 2020-12-31** | `open-20210331#p144` — *not among the six* |
| `market_count` | 2024-03-31 | 50 | **re-dated to 2024-12-31** | `open-20250331#p101` — *not among the six* |

**8 ids disappeared, 5 appeared, 2,699 stable.** The last two rows are the fix being general
rather than targeted; they are reported, not hidden. The correct value in every one of the six
groups survives with its id unchanged (27, 45, 53, 50, 18, 5,326).

### The separation the spec demanded, proved in two stages

| Stage | Observations changed |
| --- | --- |
| Pre-F0 → Part A applied | 8 removed, 5 re-dated, **2,699 ids byte-stable** |
| Part A applied → Parts B–E applied | **0 rows differ in any field** |

The second row is the one that matters: **the contract expansion moved no data at all.** Every
field of all 2,704 rows is identical across the ontology, evidence, identity and projection
changes. That is what makes "six observations disappeared" and "the contracts widened"
separable causes rather than one hash change.

---

## 3. Parts B–E

**B — evidence contract.** Discriminated by kind, driven from `claims.yaml` rather than
hard-coded. New `market_data` and `calculated` kinds. A `passage_id` may not be supplied for a
non-passage kind at all — refused by the row shape, the writer and the validator. Two deviations
from the plan, both recorded in the YAML: `normalized_table` requires `passage_id` *as well as*
`table_id` (the plan's "either" would have skipped passage resolution — a weakening), and
`xbrl_fact` requires `source_url` while `external_page` requires `fetched_at`.

**C — identity.** `digest()` over all-blank parts now raises instead of returning the constant
`e3b0c44298fc`, which every XBRL observation of one (metric, period) would otherwise have shared.
`xbrl_structural_position` supplies accession, fy, fp, concept, context, unit and dimensions.
`OBSERVATION_IDENTITY_VERSION = 1.1.0`, deliberately **not** a digest input — folding it in would
have moved all 2,704 existing ids.

**D — ontology.** `AssertionType.GUIDED` makes `guidance_issuance`'s own rule satisfiable for the
first time. Typed guidance ranges. A future-period rule that refuses a *reported* observation
whose period ends after its filing, while permitting reporting lag (measured +15 to +1,045 days),
guided future periods and named calculated cases. `transcript` and `market_data` source lanes.

**E — graph.** `:EvidenceSource` nodes with one concrete label per kind, deduplicated by source,
disagreements refused rather than merged. Ontology instance properties projected — `opendoor` now
carries `cik`, `tickers`, `exchange`; `nasdaq` carries `mic: XNAS`; the disclosure channels carry
`canonicality`.

---

## 4. Defects found while implementing

| # | Defect | Where | Status |
| --- | --- | --- | --- |
| 1 | `KEY_PROPERTIES` derived `label.lower() + "_id"`, giving `evidencesource_id` — a property no node carries. The uniqueness constraint and the loader's `MERGE` would have been on a null key, merging every source node into one. | `graph/stages/load/schema.py` | **fixed** — snake-case aware, still derived |
| 2 | The plan attributed the ontology hash move to the new enum members. Measured: enums are hash-neutral; the cause is `optional_fields` plus the new evidence-type declarations. | `F0_CONTRACT_EXTENSION.md` §5 | **corrected in the plan** |
| 3 | Two graph tests pinned the extraction code commit and run id as literals — values that legitimately move on every re-run. | `tests/graph/` | **fixed** — read from the fixture's own manifest |

---

## 5. Versions moved, and why

| Version | Moved | Reason |
| --- | --- | --- |
| Ontology `definition_hash` | `e8d4af709be2…` → `337e0e595 34d…` | `optional_fields` on `EvidenceTypeDefinition` + new evidence types + `GUIDED` + new lanes |
| Graph projection | 1.1.0 → **1.2.0** | new node label, new node properties |
| Graph schema | 1.1.0 → **unchanged number, 8 node constraints not 7** | `EvidenceSource` joined `BASE_LABELS` |
| Observation identity | — → **1.1.0** | additive; no existing id moves |
| `RUN_LAYOUT_VERSION` | **not moved** | no existing catalog row gained a field; `rebuild` reports all seven byte-identical. Bump when F1 emits the first non-passage row. |

Old catalogs are **not** readable under the new ontology, and that is enforced, not incidental:
`OntologyMismatchError` refuses to project a run whose manifest records a different hash, and
`StaleVectorCacheError` refuses a cache keyed on one. Both fired during this work and both were
cleared by regenerating the derived artifact, never by relaxing the check.

---

## 6. Verification actually run

```
python -m extraction run          5/5 self-verifications ok
python -m extraction rebuild      all seven catalogs byte-identical
python -m graph project           28,836 nodes / 35,600 edges, warnings reconciled 185/185
python -m graph load --replace    8 node constraints, 12 relationship constraints, 9 indexes
python -m graph verify            PASSED — 0 of 27 checks failed
pytest -m "not live and not neo4j"          2,720 passed, 1 skipped
FKG_GRAPH_TESTS_MAY_WIPE=1 pytest -m neo4j  37 passed
```

Plus a corpus-level audit (`f0_audit_artifacts/`) proving the six defects are gone, the six
correct values present, and the id set moved exactly as predicted — a prediction registered
*before* the implementing agents reported.

---

## 7. Known limits of this evidence

Stated plainly, because they bound what the green suite means.

1. **The run holds zero non-passage evidence rows** (24 `normalized_passage`, 2,690
   `normalized_table`). Every claim about XBRL, market-data and calculated evidence — the
   contract, the node, the identity — is proved against **synthetic contract fixtures**, not
   against filed data. That is unavoidable at F0 and is why the fixtures exist, but it means F1
   is the first real test of Part B and Part C.
2. **`:EvidenceSource` node count is 0** in the loaded graph. The label, its constraint and its
   allowlist entry are exercised; the node is not.
3. **Intermediate commits are not independently green.** The five agents worked in one shared
   tree, so the six commits split the work by ownership but only the tip was verified. The two
   data checkpoints in §2 are real and were measured separately; the per-commit test state was
   not.
4. **`PERIOD_NOT_GROUNDED_IN_PASSAGE` has a forward cost.** 111 of the 3,072 selected narrative
   passages carry a flattened grid — a 3.6% ceiling F3 will pay. One recorded answer picked the
   right column by luck and would now be refused. Refusing a lucky guess is intended; the count
   is what to watch.

---

## 8. Founder decision outstanding

**The benchmark gold case `recon-shareholder-letter-table-as-prose` is now unmeetable by
construction.** It annotates `#p20` — the passage the lane now correctly refuses — with the two
correct values from columns the flattening destroyed.

| | Before | After |
| --- | ---: | ---: |
| Value accuracy | 0.818 | **1.000** |
| `value_wrong` errors | 1 | **0** |
| Metric-identity recall | 0.550 | **0.450** |

Two gold observations moved from *matched-but-wrong* to *missed*. The trade is honest — the lane
cannot know which of seven flattened columns a figure came from — but the case will sit as a
permanent recall penalty until it is re-scoped or reclassified as a passage the lane must refuse.
**No benchmark case was changed here.**

---

## 9. What may begin now

- **F1 / F2 (XBRL)** — unblocked. The evidence gate and the identity collision are both closed,
  and `deferred_metric_ids` still derives the six from the ontology, so the lane un-defers them
  with no edit in the verifier or the projection.
- **F3 (narrative run)** — the `earnings_release` blocker is *not* fixed here (`LaneEvent` still
  has no `period_start`/`period_end`); that remains F3's own prerequisite. The pilot measurement
  should precede any GPU commitment.
- **F4 (release precision)** — never depended on F0; may start at any time.

F1/F2, F3 and F4 can proceed in parallel: they touch different lanes and share only the catalogs.
