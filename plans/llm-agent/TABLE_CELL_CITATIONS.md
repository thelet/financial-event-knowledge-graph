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
| …which the header **column index** fully **separates** (every group → one period) | **71** |
| …which it does **not** separate | **108** |
| …in which the index merely takes more than one value | 87 |

> **Corrected after the adversarial review of S7 (re-derived live 2026-08-18).** This table read
> *"separates 87 / does not separate 92"*. **87 is the count of pairs where
> `period_header_column_index` takes more than one value at all** — *"it distinguishes
> something"* — which is not the same question as *"does grouping by it leave one period in every
> group"*. Re-derived by grouping the 2,690 table-backed evidence rows by
> `(passage_id, column_label)`, taking the 179 that map to more than one `period_key`, and asking
> of each whether every `period_header_column_index` group holds exactly one `period_key`:
> **71 separate, 108 do not**. The old pair summed to 179 as well — 87 + 92 — which is what made
> a number nobody had derived look like one that had been.

So this repair must **not** claim to fix §13.7.1, and S5 must leave that check's behaviour alone.
One hundred and eight pairs are not separated by the header column index; why they are not is
not yet understood and is deliberately out of scope here. The conclusion is unchanged and is if
anything stronger: `_column_findings` is left exactly as it is.

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
column index separates only **71** of its **179** ambiguous pairs, so this repair has no standing
to change it. S5 leaves `_column_findings` exactly as it is.

Check 7 is what makes "wrong cell, wrong metric, wrong period, wrong value, unrelated evidence"
refuse: a handle for another cell either is not in the package (1) or is not `F`'s (7).

> **Measured at S5 (2026-08-18), and the hole was larger than this section says.** §3.4 called
> check 7 the answer to a gap `citation_does_not_support_fact` leaves open. It is not a gap: on
> table evidence that check separated **nothing**. All **144** table-backed passages in the
> corpus evidence more than one observation, covering **2,690 of 2,690** table-backed
> observations, up to **70** in a single passage *(verified live 2026-08-18)*. Driven over real
> packages — the 262 candidates the four detectors offer, packaged from the live graph — there
> are **90** cases where a fact's sentence can cite a neighbouring fact's cell in the same
> passage. All 90 are refused by check 7, and **88 of the 90 raised no blocking finding of any
> kind before it** (the other 2 are narrative-lane facts Rule B's span containment happened to
> catch). Over the same 262 packages, the honest citation §12 builds raised **0** §3.4 findings
> on **564 / 564** facts carrying a handle.
>
> **An eighth check was added, and it is not in the table above** — the table already lists
> seven, and *"a seventh check"* here was a miscount corrected after the adversarial review.
> `evidence_cell_span_mismatch` is the eighth **check** and the seventh new **code**: check 6's
> `table_quote_does_not_reconstruct` already existed, so S5's seven codes are checks 1–5, 7 and
> this one. The commit message counts checks and reads *"an eighth check the plan did not
> list"*; `codes.py` note 11 counts codes and reads *"the seventh"*. Both are right about
> different things and neither is right without the sentence above.
>
> `evidence_cell_span_mismatch` (REFUSE, `REBIND_TO_FACT`): the citation's `char_start` /
> `char_end` must be the span of the cell its handle names. §3.4 reads as though the handle were
> the only thing on a citation row, but `PassageCitation` also carries the offsets the evidence
> panel highlights and §13.7's reuse rule keys on, and checks 1–5 and 7 never look at them — so a
> citation carrying `F`'s handle over another cell's bytes had a verified handle and unverified
> text. §12 derives the span from the handle, so no model can write one; it is the same standing
> as check 2 and is refused rather than assumed away.
>
> **The remedy for checks 2–5 is `REBUILD_PACKAGE`, not `REBIND_TO_FACT`.** The coordinates come
> off the `PackagedFact` and the text off the `PackagedPassage`, so a draft cannot cause any of
> the four and no rebinding fixes one; they say the package disagrees with itself.
>
> **One limit, stated rather than implied.** `PassageCitation.evidence_handle` is optional, so
> §3.4's checks run only when a citation states one. Every citation `writer.draft_from` builds
> carries one — `evidence_id` is the model's only citation field — so they always run on the path
> a model's answer takes. A citation assembled in code can omit it and get §13.7's older, weaker
> tests; closing that means making the field required on `story/core/models.py`, which would
> refuse every hand-built citation in the repository and is not S5's to do.
>
> > **Closed at S7b (2026-08-18), and the sentence above is wrong where it matters most.** There
> > are no *"older, weaker tests"* for a table fact. Rule A step 1 asks whether the package's own
> > `quoted_text` occurs anywhere in the passage — true of **every** citation into that passage,
> > whatever bytes it names — and Rule B does not run. Reproduced on the committed demo package:
> > a citation with `evidence_handle=None` over **one character** of the table passage, bound to
> > `adjusted_gross_margin`, passed §12 and verified `passed=True` with zero findings.
> > `evidence_handle` is now **required with no default**. The population the optionality
> > protected is empty — 564 of 564 packaged facts mint a handle, and the 2,816 semantic,
> > identity and comparability rows across 262 live packages carry 0 citations between them — and
> > the twenty-one hand-built citations in `tests/` each knew which fact they were about. See
> > §3.5.

### 3.5 What the adversarial review of S7 found — three holes, reproduced and closed

Every row below was reproduced against real packages before it was fixed and refuses after.
None of them is a weakening; `_column_findings` was not touched.

| # | The hole | Reproduced on | Now |
| --- | --- | --- | --- |
| 1 | `evidence_cell_span_mismatch` was guarded by `citation.passage_id == passage.passage_id`, justified as *"when it does not, `citation_does_not_support_fact` is already the finding"* | `pkg:metric-move-adjusted-ebitda-opendoor-2021q3-2021q4:87518cfbba68` — one sentence binding 2021Q3 and 2021Q4 adjusted EBITDA, one citation carrying the **2021Q3** handle, the **2021Q4** passage id and the span `[0, 7)` over `'\|  \|  \|'`. **Zero findings.** | The span is checked whenever the citation states a handle, as `(passage_id, char_start, char_end)` against the cell — `evidence_cell_span_mismatch` |
| 2 | `PassageCitation.evidence_handle` optional, so `declared is None` returned before every §3.4 check | the committed demo package — a **one-character** citation with no handle, bound to `adjusted_gross_margin`. §12 clean, `passed=True`, zero findings. | Required with no default; the citation is not constructible |
| 3 | check 7 asks `fact.observation_id not in bound_ids`, so one handle satisfies it for every binding | the committed demo package — one sentence binding both margins, carrying only `ev:…#p139:r11c2`. `passed=True`, zero findings. | Every bound fact must be named by one of the sentence's citations — `uncited_factual_sentence` |

**The justification for hole 1 was false, not merely narrow.** `_support_findings` raises
`citation_does_not_support_fact` only when **no** bound fact was read from the cited passage. A
sentence binding a second fact that *was* read from it never reaches that branch, and the span
went unexamined — a verified handle over another filing's bytes, highlighted in the panel.

**Hole 3 reuses `uncited_factual_sentence` rather than minting a code.** §13.7 already owns
*"a factual sentence with nothing to check it against"*; this is the same claim at fact grain,
and its remedy — `REBIND_TO_FACT` — is already the instruction (cite the fact's handle, or drop
the binding). It stands down where check 7 or check 1 has already fired, so a sentence citing the
*wrong* cell is named once and not twice.

**Driven over the corpus, both ways.** Of the 262 packages the four detectors offer there are
**724** ordered pairs of handle-carrying facts inside one package. A sentence binding both and
citing both handles raises `uncited_factual_sentence` **0** times; citing one raises it
**724 / 724**, naming the uncovered fact every time. The 90 wrong-cell mis-citations §3.4 was
built for still refuse with `evidence_handle_not_for_fact` **and nothing else** — the stand-down
works. The honest citation §12 builds still raises **0** §3.4 findings on **564 / 564** facts.

**One thing this rule is ahead of the prompt on, stated rather than hidden.** Writer rule 9 says
*"give a sentence the evidence id of a fact it binds"*, singular — a model writing a two-fact
sentence with one evidence id satisfies the letter of the rule and is now refused by §13.7. The
verifier is authoritative independently of the writer (`codes.py` note 7 argues the same shape
for `operation_not_recomputable`), so refusing is right; the wording is still worth changing.
**Recommendation: fold *"and one for each fact it binds"* into rule 9 at the next
`WRITER_PROMPT_VERSION` bump.** Not done here because rule text is in `request_identity`, so a
one-word edit re-keys `generations.jsonl` and needs a live re-record — a cost that belongs with
the change that pays for it, not with a repair to §13.

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
| **S6** UI cell rendering | `demo_ui/` (**`table_grid.py`** new, `package_view.py`, `api.py`, `static/`) | S3, S2 |
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

> **Done at S7a (2026-08-18), and one thing the brief required turned out not to be available.**
> The rebuild came first, as S4 said it had to. `resolve_demo_inputs` against
> `graph-v1-0483dc6b4b10` re-derived `candidate.json` and `graph_identity.json`
> **byte-identical**, which is the evidence that only the packaging shape moved; the package
> moved from `…:6a858ae5c031` / digest `0857cc4f951a…` to **`…:4e4363b11373` / digest
> `76a9c8ac2a2a…`**, and its two facts now carry real cells and the table handle form —
> `ev:…open-20220930.htm#p139:r5c2` (gaap, row 5) and `…:r11c2` (adjusted, row 11) — where they
> carried `cell: null` and a `:span:` handle. `freshness_report.json` was deliberately **not**
> re-stamped: a rebuild changes only `detail` strings holding the absolute path of the checkout
> that produced them, no digest covers those bytes, and re-stamping would trade a stale path for
> a different machine's path.
>
> One `--live` run then re-recorded `generations.jsonl` — planner request `fa975557990f…`,
> writer `940d6f6c43e6…`, 7,398 prompt and 1,328 completion tokens, disposition **accepted**,
> zero findings, both handles written back verbatim by the model.
>
> **The 1.3.0-era `compare_levels` refusal could not be re-recorded, and that is a measurement.**
> `fixtures/story_demo/generations_rejected_recorded.jsonl` exists because one live run in six
> under 1.3.0 mis-declared the operation. Under 1.4.0 that variation is gone: **19 consecutive
> live writer calls** — 7 full `--live` demo runs plus 12 direct `write_story` calls over the
> same package and plan — returned the byte-identical answer every time (`content_sha256`
> `b14908e636d5…`), always `difference`, always accepted. The fixture was therefore carried
> forward with only its two citation objects migrated from `{passage_id, quote}` to
> `{evidence_id}` — the same mechanical migration the two synthetic stores got — with its prose
> and its `compare_levels` declaration untouched. It is no longer a byte-for-byte capture of one
> call, and `test_story_demo.py`'s module docstring says so rather than leaving the old claim
> standing. **No gate, prompt or check was altered to reach green.**
>
> Two test-side leftovers of S4's contract change had to move with it, and neither is a
> weakening: `BadWriterProvider` emitted the old `{passage_id, quote}` citation, which 1.4.0's
> schema now rejects *before* §12 — a different stage from the one that test is about — so it
> names a handle no fact minted and is refused as `unresolvable_evidence_handle`; and the
> manifest test pinned `story_post_draft: 1.3.0`.
>
> Offline suite: 5,684 collected, **5,684 passed, 0 failures** (was 29 failures).
> `tests/story -m neo4j`: 138 collected, **138 passed, 0 failures**.

> **Done at S6 (2026-08-18). Table evidence renders as its grid, and four defects were found on
> the way — three of them older than this repair.** A table passage now reaches the panel as
> `grid` (rows, cells, absolute char spans, an index ruler) plus `cell_marks` (one per packaged
> fact and one per citation), both built by the new `story/demo_ui/table_grid.py`, which imports
> `resolve_cell` and `split_cells` rather than resolving anything itself. Driven against the live
> graph, **2,690 of 2,690** table-backed evidence edges place a mark whose resolved value, row
> label and period header all equal the fact's own and whose span slices `quoted_text` exactly;
> all **144** table-backed passages grid without error, the widest at **33** columns.
>
> Rendering decisions came off measurements, not taste: **72.0%** of the corpus's 197,183 table
> cells are empty, so spacer columns are drawn rather than collapsed — `r5c2` counts them — and
> with **111 of 503** tables at twenty columns or wider the grid carries a row-index gutter and a
> column-index ruler, which is what makes the handle in a refusal message findable by eye.
>
> **`_sources_payload` was slicing the packaged passage text with absolute citation offsets.**
> `writer._citations_from` rebases a citation by `PackagedPassage.char_start`, and this endpoint
> indexed `passage.text` with the result. It was right only because `char_start` is 0 for every
> passage a citation can reach today (a passage a fact binds is never excerpted). Fixed and
> pinned; correct by coincidence is not correct.
>
> **`#sources-list .is-blocking` had no rule in the stylesheet.** The panel had been setting that
> class on a citation whose span did not resolve since it was written, and the sheet styled it
> only under `#verification-findings`, so an unresolved citation was drawn exactly like a
> resolved one.
>
> **Two §13.7 catalogue sentences were stale and one was wrong.**
> `citation_quote_not_in_passage` read as though a model had typed the quote — at §13.7 it is the
> *package's own* `quoted_text` — and `table_quote_does_not_reconstruct` said the cell
> "does not reconstruct from the passage's row and column labels", when
> `reconstruct_table_quote` takes `scale` and `unit` and never looks at a label.
>
> **The two strings the brief named are fixed, and rule 9 needed the same treatment.** Writer
> rule 8's codes are now the seven the evidence-id rule can produce; `evidence_handle_not_for_fact`
> went to **rule 9**, not 8, because rule 8 says *carry an id the FACTS section printed* and rule
> 9 says *carry the id of a fact this sentence binds* — which is §3.4 check 7 exactly.
>
> Offline suite: **5,716 passed, 0 failures** (5,684 → +25 new tests, +6 from
> `test_story_package_structure.py`'s per-module parametrization over the new file, +1 from
> `test_story_retrieval_cypher.py`'s). `tests/story -m neo4j`: **140 passed, 0 failures**
> (138 → +2, both driving the renderer over every table-backed edge in the graph).

> **Done at S7b (2026-08-18): the adversarial review's three holes closed, and five stale
> statements swept.** The holes and their reproductions are §3.5; the numbers they were
> re-derived from are §1.5. What moved outside `verification/`:
>
> * `PassageCitation.evidence_handle` is required with no default (`story/core/models.py`), which
>   took the guard out of `writer._citation_violations` and put a handle on 21 hand-built
>   citations in `tests/`. Each knew which fact it was about; the one place it cost anything is
>   `SemanticFact`, whose citation now has to name a handle some `PackagedFact` minted — recorded
>   in `test_story_evidence_roles.py`, population **0** across 262 packages.
> * `_column_findings` was **not touched**, as §1.5 requires. §1.5's own numbers were.
> * `code_catalogue.py`: `citation_quote_not_in_passage` covers Rule B's meaning as well as Rule
>   A's, and `uncited_factual_sentence` covers fact grain as well as sentence grain.
>   `prompt_presets.py`: `uncited_factual_sentence` is on rules 8 **and** 9 — rule 8 is a
>   sentence citing nothing, rule 9 a sentence citing one fact of two.
> * `V1_STORY_AGENT.md` §12 and §13.7 had never been swept since the citation became a handle —
>   the master spec still spelled a citation `{passage_id, document_id, char_start, char_end}`
>   and described *"two rules"* with no handle checks. Both corrected there rather than only
>   here. `INTERACTIVE_DEMO_UI.md`'s `/sources` row documented the exact bug S6 fixed.
> * `config/story.yaml`: **a comment edit breaks nothing.** `DemoConfig.config_hash` is
>   `sha256(canonical_json(yaml.safe_load(text)))`, so comments are dropped before hashing —
>   checked by mutating the header in memory: `b8488b32076b9b1d…` before and after, while the
>   file's own sha256 moves. The recorded run's disposition is **accepted**, the store was
>   re-captured 2026-08-18, and the six-refusal `length_target` table was re-measured live: under
>   prompt 1.4.0 **every target 3–8 constructs a draft and every one is accepted**, three
>   sentences each. `length_target` stays 4 because `generations.jsonl` is keyed to it; moving it
>   is the orchestrator's call and costs a live re-record. `LENGTH_TARGET_VERIFIED_MAX` moved
>   4 → 8 with it: the demo panel had been telling users that a target of 5 is refused by a gate
>   the table path no longer has.
>
> Offline suite: **5,719 passed, 0 failures** (5,716 → +3: the two new §13.7 refusals and the
> required-field test; one test was renamed, not added). `tests/story -m neo4j`: **140 passed,
> 0 failures**, unchanged.

## 5. Tests

- The four §1.3 invariants, as fixtures committed from the real corpus.
- The `'7'` case end-to-end: previously impossible, must now generate **and** verify.
- Wrong-cell, wrong-row, wrong-column, wrong-period and foreign-handle citations each refuse with
  the named code.
- A citation whose span is not its handle's cell refuses **whichever passage it names** (S7b), and
  a citation that names no handle does not build.
- Narrative evidence keeps the span path; all 14 still verify.
- A sentence binding two facts and citing one handle refuses; citing both passes (S7b).
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
