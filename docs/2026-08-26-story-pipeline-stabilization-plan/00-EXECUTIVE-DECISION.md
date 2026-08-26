# 00 — Executive decision

*Stabilization plan for `metric_move`. Architecture and implementation planning only — no code
changed. Every claim is marked **(verified)** with how it was checked, or **(unverified)**.
Measured 2026-08-26 against commit `fbac777`.*

---

## 1. The decision

**Narrow the writer's contract, recover slots deterministically, and lock the factual spine
before the planner speaks.** Three changes, in that order of value, none of which weakens a
single verifier check.

Two experiments decided it, both run against the failed runs' own committed artifacts.

### Experiment 1 — the Qwen failure is recoverable, byte-for-byte

`story-v1-1daff167348f` returned three correct sentences with **no slots**, and was refused
`thesis_abandoned`. A deterministic recovery pass that replaces each literal with the slot whose
row already offers that exact string produces (verified):

```text
in : $556 million Adjusted Gross Profit in the second quarter of 2022.
out: {{F1}} {{F1.metric}} in {{F1.period}}.

in : Adjusted Gross Profit decreased by $446 million from the second quarter of 2022 to the third quarter of 2022.
out: {{D1.metric}} {{D1.direction}} {{D1}} from {{D1.from_period}} to {{D1.to_period}}.
```

Compiling those templates reproduces the model's prose **byte-identically** and the verifier
returns **`passed: True`, zero findings at any severity**. The refused run becomes an accepted
post with no prompt change, no model change and no rule relaxed.

### Experiment 2 — over half the writer's prompt is a minefield serving a dead route

For this candidate the writer prompt is 11,179 characters. The `PASSAGES` section is
**5,924 of them — 53%** — and it exists solely so an `explanatory` sentence can name a passage
in `rests_on`. Measured (verified):

| Measure | Value |
| --- | --- |
| Numerals printed inside `PASSAGES` | **182** (119 distinct) |
| Of those, legal for the model to write | **0** |
| Figures the model *may* write, in total | **4** |
| System-prompt characters serving only this route | 1,122 of 5,939 (19%), across 5 of 14 rules |
| Accepted runs in the whole corpus that used an `explanatory` sentence | **0 of 17** |
| Packages in the corpus carrying an explanatory passage | **0** — every detector leaves `want_explanatory_search=False` |

The model is shown 182 forbidden numbers, told it may write four, and given a rule set 19% of
which serves a sentence kind that has never appeared in an accepted post and whose evidence is
never retrieved. That is the reliability problem, stated in bytes.

---

## 2. What the three changes are

| # | Change | Fixes | Cost |
| --- | --- | --- | --- |
| **A** | **Narrow the metric_move writer contract** to `reported` / `calculated` / `connective`. Drop `rests_on`, drop the `PASSAGES` section, derive `kind` from the slot rows. Writer schema goes from 4 leaves to **2** (`title`, `sentences[].text`) | The gpt-5-nano failure outright. Halves the prompt. Makes `citation_reused_for_unrelated_claim` structurally unreachable | 4 of 15 writer-gate codes and 3 of 9 compiler codes become unreachable; `explanatory` returns when explanatory retrieval does |
| **B** | **Deterministic slot recovery** — a normalization pass between the model's answer and the structural gate. A literal that exactly equals a string a row already offers becomes that slot; anything ambiguous refuses | The Qwen failure outright. Makes the slot DSL a *convenience* rather than a *requirement* | One new pure module; the safety property is proved by construction (recovery is text-preserving) |
| **C** | **A locked factual spine before the planner**, plus a plan check against it | The inverted `"rose"` plan. Removes 6 of the planner's 19 fields | Derivations for a detector-defined story execute before planning; the planner stops naming `obs:` digests |

**A and B together convert both recent failures into accepted posts.** C prevents the third
failure the audit found, which never reached the verifier because A's failure fired first.

---

## 3. Why this over the alternatives

| Alternative | Why not |
| --- | --- |
| **Repair loops first** | Both recent failures die *before* the authoritative verifier, so a verifier-attached loop never runs. And a blind retry is provably useless on the local path — 4 of 4 repeated live requests to llama.cpp returned identical answers. Repair is worth building, but it is Phase 5, not Phase 1, and after A+B its expected trigger rate on `metric_move` is low |
| **More prompt engineering** | Already the mechanism that produced the current 14 rules and a worked example. Qwen still emitted no slots at temperature 0. Prompt work cannot make a grammar mandatory-and-reliable; recovery makes it optional |
| **Replace the slot DSL with a structured sentence interface (Option C in the brief)** | It would work, and it discards the property that makes the current design good: everything outside a slot is the model's own prose, copied through untouched. A structured interface makes code the author of sentence *shape*, which is where posts start reading like templates. **Recovery gets the reliability without paying that** — see `03` §5 for the comparison |
| **Semantic NLP validation of plan prose as the primary defence** | The lexicon for it already exists and is tested (`language.CHANGE_DIRECTION`, 100+ words, total over `numerals.CHANGE_VERBS` by assertion). So it is cheap. But it is a *check on prose*, and the better boundary is that the plan never authors the direction at all. Do both: C's spine is primary, the lexical check is a 20-line backstop |

---

## 4. What does not change

Stated first, because the constraint is hard.

* `DeterministicVerifier` — **no check weakened, none removed, no severity lowered.** Two checks are *strengthened* (§7).
* `Draft`, `DraftSentence`, `FactBinding`, `PassageCitation`, `Calculation` — no field added or removed.
* The graph, the freshness gate, the evidence package, `package_content_digest`.
* `offers()`, the seven derivation operations, `series.comparable`, R1–R10.
* `slot_table`, `compile_draft`, `evidence_slice`, `renderings` — extended, not replaced.
* `request_identity`'s ten inputs and `IDENTITY_VERSION`.
* No model verifier. No model Cypher. No repair result is accepted without the normal verifier passing.

---

## 5. Corrections to the audit this planning session forced

Per the standing rule that corrections beat a clean narrative:

1. **The compiler never builds a `Calculation`.** `01-PIPELINE-CONTRACTS.md` §5 of the audit
   said it did. `grep -rn "Calculation" story/stages/composition/` returns nothing;
   `DraftSentence.calculation` is `None` on every 3.0.0 draft (verified — 20 of 40 stored drafts
   carry one, all pre-3.0.0). The audit file has been corrected in place.
2. **A sentence binding a derived fact and labelled `reported` verifies clean today** (verified).
   `reported_sentence_carries_calculation` fires on `sentence.calculation is not None`, which the
   3.0.0 path never produces. So deriving `kind` is *strictly stronger* than the status quo, not
   a simplification of it.
3. **`citation_reused_for_unrelated_claim` is order-dependent, which proves it is not semantic.**
   The same two sentences pass when the explanatory one comes first and fail when it comes second
   (verified). Root cause traced in `03` §7: the compiler cites a `rests_on` passage using a
   *table-cell* span belonging to a fact, not the passage's own prose.
4. **A forward-looking gap exists and is unrelated to any of this.** `"it will recover next
   year"` passes with zero findings, because `FORWARD_LOOKING_TERMS` holds `"will be"` and no
   general pattern (verified, with and without recovery involved). Fixing it *strengthens*
   verification and is in scope.

---

## 6. Success threshold

`metric_move` is stable enough to move on when, over the corpus defined in `06` §2:

| Metric | Threshold |
| --- | --- |
| Verifier checks weakened | **0** — enforced by `verifier_gate_digest` and a golden-count test |
| Overall accepted-post rate, replay corpus | **≥ 90%** |
| Overall accepted-post rate, live, per provider (Qwen local **and** one OpenAI model) | **≥ 70%**, and no provider below 50% |
| Writer-contract-valid rate after recovery | **≥ 95%** |
| Runs where a factual direction inversion reaches the writer | **0** |
| Repair-attempt rate | reported, not thresholded — it is the diagnostic, not the goal |

The provider split is the load-bearing one: an architecture that only works on the model it was
tuned against has not been stabilized.

---

## 7. Document map

| File | Contents |
| --- | --- |
| `01-TARGET-ARCHITECTURE.md` | The spine, the ownership map, the invariants, the two Mermaid pipelines |
| `02-PLANNER-STABILIZATION.md` | Field-by-field disposition of all 19 planner fields, the proposed schema, plan validation |
| `03-WRITER-AND-COMPOSITION-STABILIZATION.md` | Contract narrowing, slot recovery, `kind` derivation, the code-owned fixes |
| `04-REPAIR-ROUTING.md` | Ownership-routed bounded repair, feedback payloads, limits, provenance |
| `05-IMPLEMENTATION-PLAN.md` | Phases 0–7, files, types, tests, migration, rollback |
| `06-TEST-AND-VALIDATION-PLAN.md` | The test matrix, the corpus, the metrics harness |
