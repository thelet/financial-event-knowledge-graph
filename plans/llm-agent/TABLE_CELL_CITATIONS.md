# Table-cell citations: structural evidence handles for table-backed facts

**The answer, first.** Every coordinate this repair needs **already exists in the graph** and is
**already correct**. Nothing upstream of `story/` has to change. `story/stages/retrieval/cypher.py`
fetches `row_label` and `column_label` but not the five index properties beside them, and
`ObservationRecord` has no field to put them in, so the story layer discards a complete,
verified cell identity and then asks a 9B model to reconstruct it by retyping bytes out of a
flattened markdown table.

That is the whole defect. It is a story-layer omission, not a schema gap.

---

## 1. What was measured (verified 2026-08-13, live Neo4j, graph run in `data/graph_runs`)

### 1.1 The corpus is table evidence almost exclusively

| Quantity | Count | Share |
| --- | ---: | ---: |
| `EVIDENCED_BY` edges from `:Observation` | 2,704 | |
| …carrying `evidence.table_id` (**table-backed**) | **2,690** | 99.5% |
| …carrying no `table_id` (**narrative**) | **14** | 0.5% |
| `quoted_text` present on the edge | 2,704 / 2,704 | 100% |

Total `EVIDENCED_BY` in the graph is 2,710; the six not counted above hang off claim types other
than `:Observation` and are out of this repair's scope.

### 1.2 The current contract is unsatisfiable for a fifth of the evidence

`quoted_text` is a bare cell value. Its length distribution:

| Quote length (chars) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Edges | 13 | 459 | 851 | 378 | 760 | 158 | 37 | 30 |

| Quantity | Count | Share |
| --- | ---: | ---: |
| Observations whose `quoted_text` occurs **exactly once** in its passage | 2,181 | 80.7% |
| Observations whose `quoted_text` occurs **more than once** | **523** | **19.3%** |
| Worst case | 32 occurrences | |

`story/stages/generation/writer.py:410` is `if len(occurrences) != 1:`. Zero occurrences raises
`citation_quote_not_in_passage`; more than one raises `citation_quote_ambiguous_in_passage`. The
model supplies no offsets — they are *derived* from the unique occurrence.

So for those 523 the writer prompt renders `cited in passage … quoting "7"` and §12 then refuses
that exact quote as ambiguous. **The instruction and the gate contradict each other**; no model
output satisfies both. Worked example, verified:

```
obs:adjusted-gross-profit:opendoor:2023Q2:normalized-table:3aed5b68c370
  value 7000000.0 USD, scale millions, quoted_text '7'
  passage …q32023formxex991earningsre.htm#p24 contains '7' 27 times
  row: Adjusted Gross Profit (Loss) |  | $ | 84 |  |  | $ | 7 |  |  | $ | (102) | …
```

Affected metrics include both candidates run in the demo this week:

| metric | un-citable / total observations |
| --- | ---: |
| market_count | 137 / 176 |
| pct_homes_on_market_gt_120_days | 70 / 88 |
| contribution_profit | 63 / 268 |
| housing_inventory_homes | 44 / 126 |
| gaap_gross_margin | 43 / 304 |
| adjusted_ebitda | 40 / 296 |
| **adjusted_gross_margin** | 34 / 180 |
| **adjusted_gross_profit** | 24 / 172 |

This is also *already known* in the repository under a different name: `Remedy`'s docstring
(`story/core/models.py:307`) records `column_label_ambiguous_in_passage` as firing on **61.3% of
table observations**. Labels alone cannot identify a cell. The indices that can were never wired.

### 1.3 The coordinates exist, and they resolve exactly

`:Observation` carries, on **2,690 of 2,690** table-backed rows:

`row_index`, `row_label`, `value_column_index`, `column_label`,
`period_header_row_index`, `period_header_column_index`, `metric_label_row_index`,
`scale_location`, `scale_source`, `subject_basis`

`EVIDENCED_BY` carries `table_id` (2,690) and `block_ids` (2,710). `:Passage` carries its own
`table_id` on 503 of 8,776 passages and `passage_kind = 'table'`.

Four invariants were checked against **every** table-backed edge, by splitting `Passage.text` on
`\n`, splitting each line on `|`, and stripping:

| Invariant | Holds |
| --- | ---: |
| `cells(lines[row_index])[value_column_index] == quoted_text` | **2,690 / 2,690** |
| `cells(lines[row_index])[0] == row_label` | **2,690 / 2,690** |
| `cells(lines[period_header_row_index])[period_header_column_index] == column_label` | **2,690 / 2,690** |
| `metric_label_row_index == row_index` | **2,690 / 2,690** |

Note the fourth column of that check: `cells(lines[period_header_row_index])[value_column_index]`
matches `column_label` on only **565 / 2,690**. The value column and the header column are
*different indices* because of `$` and blank spacer cells — which is precisely why a model cannot
retype the row and why `period_header_column_index` must be carried rather than inferred.

### 1.4 The cell coordinate is a perfect key — this is what makes handles sound

Grouping every table-backed observation by `(passage_id, row_index, value_column_index)`:

| Quantity | Count |
| --- | ---: |
| Distinct cells | 2,690 |
| Cells mapping to more than one `period_key` | **0** |
| Cells mapping to more than one `metric_id` | **0** |

A cell identifies exactly one fact. That is the guarantee check 7 of §3.4 rests on: a handle
cannot accidentally name two facts, so "the handle this package minted for `F`" is well defined,
and citing a different cell is always detectable.

### 1.5 What this repair does **not** fix — measured, not assumed

`column_label_ambiguous_in_passage` (§13.7.1, 179 of 485 `(passage_id, column_label)` pairs,
61.3% of observations) is a *label-grain* check. Carrying `period_header_column_index` helps but
does not close it:

| `(passage_id, column_label)` pairs mapping to >1 `period_key` | 179 |
| --- | ---: |
| …which the header **column index** separates | 87 |
| …which it does **not** separate | **92** |

So this repair must **not** claim to fix §13.7.1, and S5 must leave that check's behaviour alone.
Ninety-two pairs share a header column index while spanning more than one period; why they do is
not yet understood and is deliberately out of scope here.

### 1.6 Narrative evidence is already safe

All **14 / 14** narrative quotes are whole sentences occurring **exactly once** in their passage
(e.g. `'As of December 31, 2021, only 8% of our homes had been listed on the m…'`). The existing
span path is correct for them and is kept unchanged.

---

## 2. Where the identity is lost

| Stage | File | State |
| --- | --- | --- |
| Extraction / normalization | outside `story/` | **Correct. Not touched.** |
| Graph | outside `story/` | **Correct. Not touched.** |
| Story retrieval | `story/stages/retrieval/cypher.py` — `METRIC_HISTORY` **and** `FACT_EVIDENCE_FOR_OBSERVATION` | Returned `row_label`, `column_label`, `table_id`, `block_ids`, `passage_table_id`, `passage_kind`. **Omitted all five indices.** |
| Story contract | **`story/core/series.py:128`** | `ObservationRecord` carried **no cell identity at all** — not even the labels. |
| Row → record | **`record_from_rows`, `story/stages/detection/canonicalization.py:319`** | Nothing to map the coordinates into. |
| Packaging | `story/stages/packaging/` | `PackagedFact` carries `quoted_text` only. |
| Writer contract | `story/stages/generation/prompts.py:804` | Model emits `{passage_id, quote}` — a retyped byte string. |
| §12 gate | `story/stages/generation/writer.py:410` | `len(occurrences) != 1`. |
| §13.7 Rule A | `story/stages/verification/citations.py:257` | Matches the **package's** quote — holds 2,704/2,704, safe. |
| §13.7 Rule B | `story/stages/verification/citations.py:376` | Matches the package quote inside the **model's** span — exposed. |

---

## 3. Design

### 3.1 The handle

Each packaged fact gets one deterministic, readable evidence handle, derived from stable identity
and structural position per the repository's ID rule:

```
ev:<passage_id>:r<row_index>c<value_column_index>       # table-backed
ev:<passage_id>:span:<metric_id>:<period_key>           # narrative
(none)                                                  # no filed passage (§13.7.2)
```

Readable because it surfaces in the evidence panel and in rejections. Deterministic because it is
a pure function of coordinates already digested into the package.

> **Corrected at S3 (2026-08-13). The narrative form was written here as `ev:<passage_id>:span`
> and that is not a key.** §1.4 measured uniqueness for *table cells* and the form above
> generalised it to spans without measuring. The 14 narrative observations sit in **6** distinct
> passages — one carries 6 of them, one 3, one 2 — so the passage-only form names up to six facts
> at once, and it does so in a package that exists today:
> `cand:cross-metric-divergence:adjusted-gross-profit-contribution-profit:opendoor:2021Q4:727148801299`
> carries `adjusted_gross_profit` and `contribution_profit` for 2021Q4, both read out of
> `…q42021formxex992sharehol.htm#p10`. Adding the span's *char offsets* would not have fixed it:
> `adjusted_gross_profit` and `adjusted_gross_margin` 2021Q4 quote the **same 66-character
> sentence at the same offset**, so one sentence really does evidence two facts and no structural
> coordinate separates them. The handle therefore names the fact's **slot**, which §6.1 guarantees
> is one canonical fact. The table form is unchanged — §1.4's measurement stands for it.
>
> A fact evidenced by an `:EvidenceSource` (§13.7.2) gets **no handle**, rather than one minted
> from `evidence_source_id`: V1 refuses that citation path outright, and a citable-looking token
> for a path that always refuses is worse than a fact the writer cannot cite.

### 3.2 The model's new contract

`citations[]` items become `{"evidence_id": "..."}`. The model binds a sentence to **fact ids and
evidence handles** and nothing else. It no longer retypes source text, and it never had offsets.

`fact_bindings[].rendered` is **unchanged** — that is the numeral in the model's *own* sentence,
checked against its own text, and is not affected by table flattening.

### 3.3 What code does instead

`story/core/table_cells.py` (new, pure, no graph, no I/O): given passage text and coordinates,
return the cell, its char span, its row label and its column header. This is the function whose
four invariants §1.3 measured at 2,690/2,690.

### 3.4 §13.7 over handles — what still refuses

Verification gets **stronger**, not weaker. For a citation handle `H` bound to fact `F` in
sentence `S`:

| # | Check | Refusal code |
| --- | --- | --- |
| 1 | `H` names a passage in the writer's slice | `unresolvable_evidence_handle` |
| 2 | `H`'s coordinates are within the passage grid | `evidence_handle_out_of_bounds` |
| 3 | resolved cell text == `F.quoted_text` | `evidence_cell_value_mismatch` |
| 4 | `cells[0]` == `F.row_label` | `evidence_row_label_mismatch` |
| 5 | header cell == `F.column_label` | `evidence_column_label_mismatch` |
| 6 | `reconstruct_table_quote(cell, scale, unit) == F.value` | `table_quote_does_not_reconstruct` *(exists)* |
| 7 | `H` is the handle **this package minted for `F`** | `evidence_handle_not_for_fact` |

Check 7 is sound because §1.4 measured the cell coordinate as a perfect key: 2,690 distinct
cells, none mapping to two periods or two metrics.

**§13.7.1's `column_label_ambiguous_in_passage` is not touched.** §1.5 measured that the header
column index separates only 87 of its 179 ambiguous pairs, so this repair has no standing to
change it. S5 leaves `_column_findings` exactly as it is.

Check 7 is what makes "wrong cell, wrong metric, wrong period, wrong value, unrelated evidence"
refuse: a handle for another cell either is not in the package (1) or is not `F`'s (7).

**Why this is not a weakening.** The model was never the authority on which bytes support a fact —
it was being asked to *retype* an identity the package already knew. What is removed is a typing
test. Everything that decides whether a sentence is true is untouched: §13.1 numeral binding,
§13.3 unit and percentage-point bounds, §13.4 period grammar, §13.5 metric surfaces, §13.7.1
reconstruction, §11's `counter_evidence_unaccounted`. Checks 3–6 above are new obligations that
did not exist before.

---

## 4. Stages

| Stage | Owns | Depends on |
| --- | --- | --- |
| **S1** Carry the coordinates | `retrieval/cypher.py`, `core/series.py`, `detection/canonicalization.py` | — |
| **S2** The cell resolver | `core/table_cells.py` + tests | — |
| **S3** Mint handles | `core/models.py` (`TableCellRef`, `PackagedFact.cell`, `PackagedFact.evidence_handle`, `StoryEvidencePackage.facts_by_evidence_handle`), `packaging/` | S1, S2 |
| **S4** §12 writer contract | `generation/prompts.py`, `generation/writer.py` | S3 |
| **S5** §13.7 over handles | `verification/citations.py`, `verification/codes.py` | S3, S2 |
| **S6** UI cell rendering | `demo_ui/` (`package_view.py`, `api.py`, `static/`) | S3, S2 |
| **S7** Re-record + end-to-end + adversarial review | fixtures, `plans/` | all |

S1 and S2 are disjoint and run in parallel. Everything after is sequential — a previous wave lost
work to two agents committing in one worktree, and that is not repeated.

`PACKAGE_VERSION` moves 1.2.0 → **1.3.0** and `WRITER_PROMPT_VERSION` 1.3.0 → **1.4.0**. Both
re-key the replay store by design; S7 re-records under `--live`.

> **What the version bump actually cost, measured at S3 (2026-08-13).** Less than the paragraph
> above expected, and in a different place. The committed demo package's `package_id` is *stored
> data* and does not recompute offline, so the recorded planner and writer generations still hit
> and the offline demo did **not** need re-recording at S3. What did break is §13.13's
> `package_content_digest_mismatch`: two new fields on `PackagedFact` change the serialized shape,
> so `fixtures/story_demo/evidence_package.json` no longer hashed to its own stored digest. It was
> re-stamped in place — same rows, same ids, digest recomputed — which is what keeps the eight
> offline demo and UI tests green.
>
> That fixture predates S1, so its two **table** facts carry `cell: null` and therefore the
> *narrative* handle form. That is honest about the artifact and wrong about the corpus, and it is
> S7's to fix: `test_live_the_package_and_the_freshness_report_match_the_committed_fixture` and
> `test_the_d4_package_is_byte_identical_to_the_one_the_accepted_path_builds` (both `neo4j`-marked,
> both outside the offline suite) fail until S7 rebuilds the fixture from the graph and re-records
> the store against it.

## 5. Tests

- The four §1.3 invariants, as fixtures committed from the real corpus.
- The `'7'` case end-to-end: previously impossible, must now generate **and** verify.
- Wrong-cell, wrong-row, wrong-column, wrong-period and foreign-handle citations each refuse with
  the named code.
- Narrative evidence keeps the span path; all 14 still verify.
- The AST scan (`test_story_retrieval_cypher.py`) still passes: new fields are named returns on a
  fixed statement, no new traversal, no APOC, explicit `LIMIT`.
- No new unbounded traversal; S1 adds fields to existing `RETURN` lists only.

## 6. Rejected

| Option | Why not |
| --- | --- |
| Loosen §12 to accept a quote that *reconstructs* to the fact's value | Fixes the 0-occurrence branch and leaves the 523 ambiguous ones permanently un-citable. Wrong fix; would have shipped a fifth of the corpus still dead. |
| Fuzzy / whitespace-insensitive substring match | Turns a byte-exact evidence claim into a similarity score, and still cannot disambiguate 27 copies of `'7'`. |
| Have the model emit `row_index`/`column_index` directly | Moves the retyping problem to integers and invites off-by-one hallucination. Code owns coordinates. |
| Change extraction/normalization to emit richer `quoted_text` | Not required — §1.3 proves the story layer can reconstruct the reference from data already present. Recorded in §7 as a longer-term item, not done here. |
| Re-flatten tables at packaging time into unique strings | Rewrites source bytes, violating the raw-bytes-preserved rule. |

## 7. Deferred upstream improvement (not implemented here)

`quoted_text` on `EVIDENCED_BY` is a bare cell value with no structural qualifier. A future
extraction change could carry a `cell_ref` alongside it so consumers need no reconstruction. This
repair does not require it and does not do it; `story/` reconstructs from properties already
present, verified at 2,690/2,690.

## 8. Out of scope

The context-window / token-budget work is a **separate track** and is deliberately not combined
with this one. They share only the file they both broke in.
