# F0 — final integration audit

Every filed fact Part A changed, fact by fact, and the benchmark decision that followed.
Written 2026-08-03 on `impl/f0-factual-spine-contracts`, against run
`extract-v1-lexical-833f7bcfbce9` and graph run `graph-v1-0483dc6b4b10`.

---

## 1. Commit reconciliation

The completion report said "twelve commits" and then printed a table of ten. **Twelve is
right; the table was incomplete** — it omitted the two documentation commits written during the
interrupted session.

```
$ git log --oneline df50be9..HEAD
e3c4da0 docs(factual-spine): record the F0 result, and retire a handoff that had gone false
d1f6d6a test(graph): stop six tests skipping themselves into a pass
f8ebd3a fix(evidence): let market data and calculated facts be cited, and close two review holes
dfad325 chore(benchmarks): regenerate the artifacts the ontology hash keyed
feb7bac feat(graph): project evidence sources and the instance properties it was dropping
5cb21de feat(ontology): add guided assertions, typed guidance ranges and a future-period rule
3d32c40 fix(identity): give an external fact a discriminator instead of a constant
8bfd9d1 feat(evidence): support typed non-passage evidence
6274081 fix(extraction): correct multi-header period binding, and refuse a flattened grid
c59818e docs(factual-spine): correct the handoff -- Part E landed nothing      <- omitted
5c5ca7f docs(factual-spine): hand off F0 mid-implementation with the audit evidence  <- omitted
66a1a00 docs(factual-spine): plan F0, and find the six defects are two bugs not one
= 12
```

Two more have landed since (§4), so the branch is at **14** as of this audit.

---

## 2. Every changed filed fact

**8 observation ids disappeared, 5 appeared, 2,699 unchanged.** Nothing else in the corpus moved:
a full field-by-field diff of the 2,699 stable ids shows **zero** differences in value, unit,
currency, period, metric, subject, source lane, scale or assertion type.

### 2a. The five table-lane re-datings

All five are one bug: `_assign_groups` divided the date columns evenly between two group
headings when the true division is 1 + 3. **The observation id's digest suffix is unchanged in
every case** — the digest covers `passage_id` and grid position, neither of which moved, so only
the readable period segment of the id changed. That is the identity scheme behaving correctly
under a period correction.

| # | Metric | Old value @ period | New value @ period | Document · passage | Row | Column (label, grid index) | Group heading before → after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `market_count` | 21 @ **instant 2020-03-31** | 21 @ **FY2020 → instant 2020-12-31** | `open-20210331.htm` · `#p144` | `Number of markets (at period end)` | `2020`, col 4 | `March 31,` → `Year Ended December 31,` |
| 2 | `market_count` | 44 @ **instant 2021-03-31** | 44 @ **FY2021 → instant 2021-12-31** | `open-20220331.htm` · `#p122` | same | `2021`, col 8 | `March 31,` → `Year Ended December 31,` |
| 3 | `market_count` | 53 @ **instant 2022-03-31** | 53 @ **FY2022 → instant 2022-12-31** | `open-20230331.htm` · `#p109` | same | `2022`, col 8 | `March 31,` → `Year Ended December 31,` |
| 4 | `market_count` | 50 @ **instant 2023-03-31** | 50 @ **FY2023 → instant 2023-12-31** | `open-20240331.htm` · `#p112` | same | `2023`, col 8 | `March 31,` → `Year Ended December 31,` |
| 5 | `market_count` | 50 @ **instant 2024-03-31** | 50 @ **FY2024 → instant 2024-12-31** | `open-20250331.htm` · `#p101` | same | `2024`, col 8 | `March 31,` → `Year Ended December 31,` |

Observation ids:

```
#1  obs:market-count:opendoor:2020-03-31:normalized-table:0adfc49f3266
 -> obs:market-count:opendoor:2020-12-31:normalized-table:0adfc49f3266
#2  obs:market-count:opendoor:2021-03-31:normalized-table:849370fa078c
 -> obs:market-count:opendoor:2021-12-31:normalized-table:849370fa078c
#3  obs:market-count:opendoor:2022-03-31:normalized-table:e4b152866fa2
 -> obs:market-count:opendoor:2022-12-31:normalized-table:e4b152866fa2
#4  obs:market-count:opendoor:2023-03-31:normalized-table:9a2d0faedf1f
 -> obs:market-count:opendoor:2023-12-31:normalized-table:9a2d0faedf1f
#5  obs:market-count:opendoor:2024-03-31:normalized-table:de88f2cec7af
 -> obs:market-count:opendoor:2024-12-31:normalized-table:de88f2cec7af
```

**The header grid, measured.** All five tables have the same shape — two group headings above a
row of four bare years, where the first heading governs **one** column and the second governs
**three**:

| Passage | Group headings `(row, col, phrase)` | Date columns `(col, label)` |
| --- | --- | --- |
| `#p144` | `(2, 2, 'March 31,')` · `(2, 4, 'Year Ended December 31,')` | `(2,'2021') (4,'2020') (6,'2019') (8,'2018')` |
| `#p122` | `(2, 6, 'March 31,')` · `(2, 8, 'Year Ended December 31,')` | `(6,'2022') (8,'2021') (10,'2020') (12,'2019')` |
| `#p109` | `(2, 2, 'March 31,')` · `(2, 8, 'Year Ended December 31,')` | `(2,'2023') (8,'2022') (10,'2021') (12,'2020')` |
| `#p112` | `(2, 2, 'March 31,')` · `(2, 8, 'Year Ended December 31,')` | `(2,'2024') (8,'2023') (10,'2022') (12,'2021')` |
| `#p101` | `(2, 2, 'March 31,')` · `(2, 8, 'Year Ended December 31,')` | `(2,'2025') (8,'2024') (10,'2023') (12,'2022')` |

**Correction reason, in one sentence:** four date columns divided evenly between two headings
gave the first heading two columns, so the second date column — a child of
`Year Ended December 31,`, whose heading starts *at that very column* — was dated as a
March-31 instant. The rule now prefers even division only when the date labels repeat
(`2020 2019 2020 2019`, genuinely parallel groups); these labels are a strictly descending run
of distinct years, which cannot be two parallel groups, so positional binding decides.

Every re-dated value is corroborated elsewhere in the corpus: 21 is the Dec-31-2020 market
count, 44 the Dec-31-2021, 53 the Dec-31-2022, 50 the Dec-31-2023 and Dec-31-2024.

### 2b. The three narrative-lane removals

All three come from **one passage**, `q42023formxex992sharehol.htm#p20` — a KPI table the parser
emitted as narrative, with the header itself flattened
(`Three Months Ended Year Ended December 31, December 31, 2023 September 30, 2023 … December 31,
2022 2023 2022`). The model bound the **December 31, 2022** column to `2023-12-31`.

| Metric | Old value @ period | Now | Correct value, and where it survives |
| --- | --- | --- | --- |
| `market_count` | **53** @ instant 2023-12-31 | removed | 50 — 16 other observations |
| `pct_homes_on_market_gt_120_days` | **55** @ instant 2023-12-31 | removed | 18 — 12 other observations, incl. `#p9` of the *same document* in prose |
| `housing_inventory_homes` | **12,788** @ instant 2023-12-31 | removed | 5,326 — 10 other observations |

```
obs:market-count:opendoor:2023-12-31:normalized-narrative:46093176cdf5                -> removed
obs:pct-homes-on-market-gt-120-days:opendoor:2023-12-31:normalized-narrative:46093176cdf5 -> removed
obs:housing-inventory-homes:opendoor:2023-12-31:normalized-narrative:46093176cdf5     -> removed
```

There is no "new id" for these: the values were wrong, not mis-dated, and no rule can recover
which column they came from. **No replacement was invented.** Each figure was already in the
corpus from a source whose columns survived.

**Correction reason:** the passage prints every period phrase inside a run of headings with no
table structure left to bind them to columns, so which figure any of them governs is not
readable. `PERIOD_NOT_GROUNDED_IN_PASSAGE` refuses when *every* printing of the phrase falls
inside such a run — which keeps `#p9`'s correct 18%, where the same date is also printed in an
ordinary sentence.

### 2c. Graph effect

| | Before | After |
| --- | ---: | ---: |
| `:Observation` nodes | 2,707 | 2,704 |
| `HAS_OBSERVATION` / `OBSERVATION_OF_SUBJECT` | 2,707 | 2,704 |
| `EVIDENCED_BY` | 2,713 | 2,710 |
| `:Issue` / `FOUND_IN` | 17,127 | 17,130 |

Live Neo4j now answers `market_count` as 27 @ 2021-03-31, 45 @ 2022-03-31, 53 @ 2023-03-31 and
50 @ 2023-12-31, with **one value per quarter-end** across all 21 of them. Graph verification:
**27/27 checks pass, 0 dangling endpoints.**

---

## 3. The benchmark decision

`recon-shareholder-letter-table-as-prose` is now a **must-refuse case**: `gold_claims: []`, with
`PERIOD_NOT_GROUNDED_IN_PASSAGE` as its expected abstention.

### Why the old gold was wrong

The case expected two observations from `#p20` on the stated reasoning that *"only the leading
value is safely attributable"*. **That assumption was measured false** — the lane read the
December-31-2022 column as 2023-12-31 and produced the three wrong observations above, all of
which reached the graph. There is no rule that takes the leading value and stops, because
nothing in the flattened text marks which run-together number is leading; the header is
flattened too, so "first" is a guess about a lost table rather than a reading of the passage.

A gold answer that rewards extraction here rewards exactly the behaviour that produced the
defects F0 exists to remove.

### Coverage preserved, not weakened

**The refusal rule was not touched.** Both facts keep a positive benchmark from a source whose
columns survived normalization:

| Fact | Positive benchmark | Source |
| --- | --- | --- |
| `pct_homes_on_market_gt_120_days` 18 @ 2023-12-31 | `kpi-table-q4-2023-earnings` (tables) | `q42023formxex991earningsre.htm#p16` — the safely normalized table |
| same | `population-two-wordings-one-passage-q4-2023` (**narrative**) | `q42023formxex992sharehol.htm#p9` — the explicit prose sentence |
| same | `population-portfolio-mdna-fy2023-10k` (narrative) | `open-20231231.htm#p114` — 10-K MD&A prose |
| `housing_inventory_homes` 5,326 @ 2023-12-31 | `kpi-table-q4-2023-earnings` (tables) — **added here** | `q42023formxex991earningsre.htm#p16` |

Both routes the brief named already existed for the 18%. `housing_inventory_homes` was covered
*only* by the reclassified case, so it was added to the safely normalized table — the **same
quarter's EX-99.1, carrying the same row** (`Homes in inventory (at period end) | December 31,
2023 → 5,326`) with its columns intact. The fact keeps its benchmark; the unsafe way of reading
it does not.

### Metrics before and after

**Narrative lane** (lexical, the default scope):

| Score | Pre-F0 | After F0, before reclassification | **After reclassification** |
| --- | ---: | ---: | ---: |
| `metric_identity_accuracy` | 0.550 | 0.450 | **0.500** |
| `value_accuracy` | 0.818 | 1.000 | **1.000** |
| `value_wrong` failures | 1 | 0 | **0** |
| `abstention_honoured_rate` | — | 0.9333 | **0.9375** |
| `abstention_code_agreement` | — | 0.0667 | **0.125** |
| gold observations | 20 | 20 | **18** |
| missed observations | — | 11 | **9** |
| total failures | — | 15 | **13** |

**Table lane:**

| Score | Before | After |
| --- | ---: | ---: |
| `metric_recall` | 0.9388 (46/49) | **0.940 (47/50)** |
| `value_accuracy` | 1.000 | 1.000 |

### Why the recall change is correct

Recall fell 0.550 → 0.450 when the lane started refusing `#p20`, and the reclassification
**recovers it to 0.500** — not by extracting more, but by removing two observations from the
denominator that the lane must not make. That is the honest denominator: a recall score whose
denominator includes facts it is *correct* to refuse measures obedience to a bad instruction,
not capability.

The remaining 0.550 → 0.500 gap is real and stays. It is the price of refusing a passage shape
that occurs in 111 of the 3,072 selected narrative passages, and it buys `value_accuracy`
0.818 → **1.000** with `value_wrong` at zero. **A lane that guesses the column is right about
five sevenths of a quarter and confidently wrong about the rest**, and the wrong answers are
indistinguishable from the right ones downstream — which is precisely how three of them reached
the graph.

Table-lane recall *rose* (0.9388 → 0.940) because the fact moved to a source where it is
attributable, so the same figure is now scored where the lane can actually earn it.

---

## 4. Verification after these two changes

```
pytest -m "not live and not neo4j"          2,723 passed, 0 skipped, 0 failed
FKG_GRAPH_TESTS_MAY_WIPE=1 pytest -m neo4j     37 passed
python -m graph verify                      PASSED — 0 of 27 checks failed
python -m extraction rebuild                all seven catalogs byte-identical
f0_audit.py                                 six defects gone; six survivors present
```

Six benchmark reports and the scoping decision were regenerated; three pinned figures moved with
the gold set and were updated with their reason — the README's required-concept recall gate
(48/50 → 47/49), the scoping-decision comparison width (50 → 49), and the narrative report's
own totals.

**No later stage was started.** Nothing ingests XBRL, transcripts or prices; no provider call was
issued; no guidance is extracted.
