# 05 — Live validation

*Bounded live generation, both providers, real `metric_move` candidates off the loaded graph.
Measured 2026-08-26. Neo4j container `fkg-neo4j` was read only; no volume was touched and the
graph was not reloaded.*

---

## 1. The headline

| | before this work | after |
| --- | --- | --- |
| Live `metric_move` runs reaching the authoritative verifier | **0 of 2** | **11 of 11** |
| Live runs refused at the writer's structural gate | 2 of 2 | **0** |
| Live runs whose plan contradicted the facts and reached the writer anyway | 1 of 2 | **0** |

Both original failures are eliminated. The two runs the brief was written about —
`story-v1-1daff167348f` (`thesis_abandoned`) and `story-v1-76da8465cd95` (six `rests_on` codes) —
cannot happen under this contract: the first because the slot grammar is optional, the second
because `rests_on` is gone.

## 2. The seven rates

Six candidates per provider, one run each, all `metric_move`, three distinct metrics.

| Rate | local `Qwen3.5-9B-Q4_K_M` | `openai` `gpt-5.4` |
| --- | --- | --- |
| planner-valid | **6/6 (100%)** | **5/5 (100%)** |
| writer-contract-valid | **6/6 (100%)** | **5/5 (100%)** |
| compile-success | **6/6 (100%)** | **5/5 (100%)** |
| verifier-pass | 3/6 (50%) | 1/5 (20%) |
| **accepted-post** | **3/6 (50%)** | **1/5 (20%)** |
| repair-attempt | 3/6 | 4/5 |
| repair-success | **0** | **0** |
| provider failures | 0 | 0 |

*(The OpenAI run reports five: the harness's sixth candidate was the one excluded in §4.)*

**The architecture reached its target and the prose did not.** Every run got a valid plan, a
valid writer answer, a compiled draft and an authoritative verdict. What blocks acceptance is
now entirely what the verifier is for.

## 3. By candidate, which is the more useful cut

| Candidate | local | openai |
| --- | --- | --- |
| `housing_inventory_homes` | **3/3 accepted** | 1/3 accepted |
| `market_count` | **0/3** | 0/2 |

`market_count`'s package carries a claim-qualifying warning (`unpreferred_source_lane`), so §13
requires the post to state it with one of `flagged | carries a warning | data-quality`. Neither
model reliably does, and one bounded repair with the exact phrase list did not fix it. That is a
genuine model failure against a rule the prompt states and the prompt prints the phrases for.

## 4. One candidate is structurally unwritable, and that is a finding

`pct_homes_on_market_gt_120_days` was excluded from the corpus after measurement, with the
reason recorded rather than the candidate quietly dropped.

Its ontology label is:

```text
Percentage of homes "on the market" for greater than 120 days (at period end)
```

which contains `120` (`unbound_numeral`), `the market` (`foreign_subject_named`) and
`greater than` (`unsupported_comparative`). **Any post about this metric is unwritable**, whether
the model types the label or writes `{{F1.metric}}` — the slot inserts the same string.

Neither weakening those three checks nor asking a model to work around them is right. The fix is
a writable display alias for that metric in the ontology, which is upstream of this work and out
of scope for it.

## 5. Failures fixed *because* of live validation

Three defects were found only by running this, and none would have shown up offline:

1. **An unsatisfiable title rule.** Rule 12 said to write the period *"in the compact form the
   candidate id uses (2022Q3)"*, and half the live candidates are instant-dated (`2025-06-30`).
   Qwen obeyed, wrote `Opendoor 2025Q2`, and was refused twice for a title the rule had asked
   for. The prompt now prints the package's own period keys. One candidate went from rejected to
   accepted on that change alone.
2. **An ungroundable counterpoint.** gpt-5.4 lost 2 of 6 runs to `plan_not_constructible` by
   writing a counterpoint on a package with no counter-evidence. The prompt now says so in words,
   and `plan_not_constructible` routes to planner repair.
3. **A duplicated warning.** A package raises one warning per *fact*, so two `warned` readings
   put `unpreferred_source_lane` in `required_warnings` twice and §13 wanted the phrase said
   twice in a four-sentence post. Deduplicated.

## 6. Provider comparison

The architecture is **not** tuned to one model: both providers achieve 100% on planner-valid,
writer-contract-valid and compile-success. They differ only at the verifier, and they differ in
*which* prose rules they break, not in whether they can use the contract at all.

That is the property the plan set out to establish, and it is the one that matters for adding
post types later.

## 7. Honest limits of this measurement

* **Eleven live runs is a bounded validation, not a study.** One run per candidate per provider.
* The local server is byte-reproducible at temperature 0, so a repeated local run is one sample,
  not several. OpenAI never receives the temperature field at all.
* Two distinct metrics carry the accepted/rejected split almost entirely; a third was excluded
  for §4's reason. A wider corpus would need more of the graph's candidates promoted.
* **Repair has not yet earned its cost on this corpus**: it fired seven times and converted
  nothing. See `03-REPAIR-ROUTING.md` §6.
