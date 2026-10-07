# 04 — Repair-loop feasibility

*Whether the current architecture could support `writer → verifier → structured failure →
writer repair → verifier`. Analysis only; nothing implemented. Verified 2026-08-26.*

---

## 1. Answer first

**The data structures are ready; the control flow is not; and the evidence says a blind retry
would be useless against the local model and a coin-flip against OpenAI.**

| Question | Answer |
| --- | --- |
| Is verifier output structured enough to become repair feedback? | **Yes.** `VerificationFinding` already carries 13 fields including `sentence_index`, `char_start`/`char_end`, `fact_ids`, `citation_ids`, `expected`, `observed`, and an 11-member `Remedy` enum. Nothing would need deriving |
| Could a repair preserve the same evidence? | **Yes, trivially.** Package, plan, derived facts and slot table are all values in `run_demo`'s scope and none depends on the draft |
| Would the replay store cope? | **Yes.** `prompt` is one of the 10 `request_identity` digest inputs, so a repair prompt keys to a new row automatically |
| Would a *blind* retry help? | **No.** Measured: the local server returned byte-identical answers to identical requests in 4 of 4 live repeats |
| Would a *feedback* retry help? | Plausible for a bounded class — but the largest failure class in the 3.0.0 era is *"the model did not use the slot grammar at all"*, which is a prompt problem, not a repair problem |
| What blocks it structurally? | Two things: the verifier is downstream of two gates that end the run first, and `run_demo` is documented and tested as one pass |

---

## 2. Every retry mechanism that exists today

Searched exhaustively (`grep -rn "retry\|retries\|backoff\|attempt" story/`).

| Kind | Exists? | Where | Bound |
| --- | --- | --- | --- |
| **Provider / network** | **Yes** | `openai_compatible.py:230` and `openai_responses.py:269`, `_post_with_retries` | `DEFAULT_MAX_RETRIES = 2`; `RETRYABLE_STATUSES = {429, 500, 502, 503, 504}` |
| Schema-generation retry | **No** | — | `contracts.py:165`: *"A schema violation is the model's answer and is **never** retried"* |
| Timeout retry | **No** | — | same sentence: *"a timeout is never retried either"* |
| Planner retry | **No** | — | `pipeline.py:620`: *"One candidate, one pass, no retry"* |
| Writer retry | **No** | — | same |
| Verifier retry | **No** | — | the verifier is pure and total |
| Whole-story rerun | **Manual only** | the CLI / demo UI can be invoked again | a rerun mints a new `story_run_id` |

**What a transport retry preserves:** everything. It re-POSTs the identical body — same
candidate, package, plan, facts, temperature, `max_tokens`. It is invisible above the adapter;
`attempts` is deliberately excluded from the stored generation row because it *"changes on every
identical request"* (`generation_store.py:19`).

**What a whole-story rerun preserves:** the candidate and the graph run. It rebuilds the package
(deterministically — `package_content_digest` is stable), re-derives the offers, and re-issues
both model calls. It does **not** preserve the plan or the draft, because both are model output.

**There is no mechanism anywhere that re-issues one model call while holding the other's output
fixed.** A repair loop would be the first.

---

## 3. Failure stability at temperature 0 — measured, not assumed

Every stored generation row carries `request_sha256` (a digest of system + prompt + schema +
schema_name + provider_id + model_id + temperature + temperature_sent + reasoning_effort +
max_tokens) and `content_sha256`. So identical requests are directly identifiable.

Across the 58 run directories, **restricted to runs whose `generation_mode` is `live`**:

| Request digest | Schema | Model | Live issues | Distinct answers |
| --- | --- | --- | --- | --- |
| `09a35306998e` | writer | Qwen3.5-9B local | 2 | **1** |
| `3a3f02f76f16` | planner | Qwen3.5-9B local | 2 | **1** |
| `c509d99ed253` | writer | Qwen3.5-9B local | 2 | **1** |
| `fe2a239b1130` | planner | Qwen3.5-9B local | 2 | **1** |
| `49ffa2031413` | planner | gpt-5-nano | 2 | **2** |
| `3193b6e35888` | planner | gpt-5.4 | 2 | **2** |

**Local llama.cpp: 4 of 4 identical. OpenAI: 2 of 2 different.**

The reason is in the stored rows and is not a mystery: the OpenAI adapter sends
`temperature_sent: false` and `reasoning_effort: minimal` — **temperature 0.0 is recorded but
never transmitted**, because the Responses API rejects it for these models. So "temperature 0"
is only true of the local path.

The two OpenAI differences are semantic, not cosmetic. `3193b6e35888` (gpt-5.4, planner):

```text
"claim": "Opendoor reported Adjusted Gross Margin of 3.3 percent in 2022Q3."
"claim": "Opendoor reported Adjusted Gross Margin of 3.3 percent for 2022Q3."
```

`49ffa2031413` (gpt-5-nano, planner) differs in the whole counterpoint claim.

> **Caveat, stated because the number is small.** Six repeated live digests is not a study. A
> further 7 digests differ across runs but involve replayed or hand-authored fixture rows and
> are **not** evidence of model variance — the `story-v1-d0d1be4a4f0e` / `story-v1-f60f161af4c3`
> pair in particular is a deliberately hand-authored reversal used as a proof in the 2026-08-23
> work. Excluding them is why the live-only table above is the one that counts.

**The consequence for repair design is direct:**

* Against the **local** model, `same prompt → same model → same settings` returns the same
  answer. A blind retry is pure cost. Only a *changed prompt* can change the outcome.
* Against **OpenAI**, a blind retry is a lottery with unknown odds and no guarantee the second
  answer is better — one of the two observed variations was a materially different counterpoint.
* **A repair retry is a different mechanism**, because the prompt changes. It is the only form
  of retry that has a mechanism of action on the local path at all.

---

## 4. Error taxonomy for repair

Every current code, classified into the brief's five categories.

### 4.1 Safe writer-repair candidate

The draft is wrong, the plan and evidence are fine, and the fix is a rewording the model can
make from a compact instruction.

| Code | Stage | Why it is repairable |
| --- | --- | --- |
| `unbound_numeral` | verifier | The model typed a figure or a period; the fix is *"use `{{F1.period}}` here"* and the slot is known |
| `metric_surface_absent_from_text` | verifier | *"name the metric in sentence 1"*; the legal surfaces are `SlotRow.offers["metric"]` |
| `metric_named_in_text_contradicts_binding` | verifier | same, with the wrong name identified |
| `metric_surface_ambiguous` / `_unresolved` | verifier | `NARROW_METRIC_SURFACE` — the alias index knows the unambiguous forms |
| `period_surface_absent_from_text` / `period_named_in_text_contradicts_binding` | verifier | `ADD_PERIOD_QUALIFIER` |
| `percentage_point_surface_missing` | verifier | `ADD_PERCENTAGE_POINT_QUALIFIER` |
| `connective_sentence_carries_a_claim` | verifier | `DROP_SENTENCE` or re-kind |
| `forward_looking_language`, `unsupported_superlative`, `unsupported_absence_claim`, `unsupported_temporal_ordering`, `foreign_subject_named` | verifier | prose-only; the offending span is on the finding |
| `causal_construction_forbidden` | verifier | `REMOVE_CAUSAL_CONSTRUCTION` |
| `conflict_not_disclosed`, `required_warning_absent`, `required_counterpoint_absent` | verifier | an omission; the required phrase list is `WARNING_QUALIFIER_PHRASES` |
| `over_precision`, `paraphrase_distance` | verifier | non-blocking today |
| `malformed_slot`, `template_not_compilable` | writer gate / compiler | syntactic; the grammar is two forms |
| `slot_without_binding`, `field_not_offered_by_row`, `unknown_slot_handle`, `unknown_slot_field`, `no_legal_rendering` | compiler | the row's *actual* offers are printable in the feedback |
| `rests_on_without_explanatory_sentence`, `rests_on_not_a_passage_handle`, `passage_handle_unknown`, `rests_on_without_explanatory_kind` | writer gate / compiler | **case 2's entire failure** |
| `citation_reused_for_unrelated_claim` | verifier | **only partly** — see §4.4; often the compiler's choice, not the model's |

### 4.2 Planner-repair candidate

The draft faithfully carries a plan that is wrong.

| Code | Why it is the planner's |
| --- | --- |
| `unsupported_comparative`, `comparative_not_supported_by_text` | The thesis asserts a direction the facts contradict. **Case 2 exactly.** Repairing the writer would make it write a sentence its own plan denies |
| `thesis_abandoned` **when it genuinely means that** | The writer wrote about different facts than the plan required — but see §4.5 |
| `derivation_not_offered` | the plan asked for a triple that does not exist |
| `causal_language_not_computed` | the plan overrode a code-computed field |
| `counterpoint_missing`, `counterpoint_ungrounded`, `counter_evidence_unaccounted` | the plan's structure, not its prose |
| `unknown_warning_code`, `unknown_unusable_id` | plan-level id errors |
| `reported_sentence_carries_calculation`, `percent_change_reported_not_calculated` | usually a `statement_class` the plan set wrongly |

### 4.3 Retrieval / research failure — return to evidence, not to writing

| Code | Why |
| --- | --- |
| `fact_not_in_package`, `citation_not_in_package`, `date_not_in_package` | the claim needs evidence this package does not hold |
| `absence_not_provable_from_bounded_package` | structurally unprovable from a 12-fact cap |
| `unpopulated_metric` | the metric has no readings |
| `evidence_kind_not_supported_in_v1` | out of scope |
| `no_evidence_handle_for_bound_fact` | the package minted no handle |
| `package_content_digest_mismatch`, `graph_input_digest_mismatch`, `graph_run_id_mismatch` | `REBUILD_PACKAGE` — the remedy enum already says so |
| every `freshness` code | the graph is stale |

### 4.4 Deterministic / code responsibility problem — a repair loop would be the wrong tool

| Code | Why |
| --- | --- |
| `citation_reused_for_unrelated_claim` | **15 of 85 recorded findings.** The compiler picked the span (`_unused_handle`'s fallback) and §13.7 refuses it. Asking the model to fix it is asking it to fix code's choice |
| `evidence_cell_span_mismatch`, `evidence_cell_value_mismatch`, `evidence_row_label_mismatch`, `evidence_column_label_mismatch`, `evidence_handle_not_for_fact`, `unresolvable_evidence_handle`, `evidence_handle_out_of_bounds` | all handle plumbing, all code-owned since 3.0.0 |
| `derived_result_mismatch`, `derived_unit_mismatch`, `calculation_does_not_recompute`, `calculation_result_surface_mismatch`, `derived_fact_orientation_reversed` | the derivation stage computes all of these |
| `over_precision` on `{{D2}}` → `80.215827338%` | `renderings.derived_figure`'s float fallback |
| `binding_rendering_is_not_one_numeral`, `binding_span_does_not_match_text` | offsets are code's since 3.0.0 |

### 4.5 Hard rejection — retrying the same evidence/model is unsafe or pointless

| Code / situation | Why |
| --- | --- |
| `thesis_abandoned` **as observed in case 1** | The model produced *no slots at all*. This is not a repairable draft; it is a model that did not engage the contract. Retrying the same prompt at temperature 0 against the local server returns the same answer (§3) |
| `no_sentences` | as above |
| `plan_names_another_package`, `candidate_id_mismatch`, `package_id_mismatch` | wiring faults, not content |
| `mutually_distinct_group_ambiguity`, `row_label_not_licensed_for_metric`, `column_label_ambiguous_in_passage` | the corpus cannot answer |
| `relative_change_across_zero`, `percent_change_ambiguous`, `incomparable_periods`, `period_shape_conflated`, `derived_inputs_incomparable` | the arithmetic is genuinely undefined; no wording fixes it |
| `operation_not_recomputable`, `extremum_expression_not_supported`, `calculation_operation_not_supported` | the verifier cannot check this class at all |
| every `StoryProvider*Error` | already retried, or deliberately not |

**The corpus under this taxonomy**, computed by mapping every code above onto the 85 findings in
the 40 stored verification reports (verified 2026-08-26; every finding classified, none left
over):

| Category | Findings | Share |
| --- | --- | --- |
| 4.1 safe writer-repair | **43** | 51% |
| 4.4 code responsibility | **24** | 28% |
| 4.2 planner-repair | **14** | 16% |
| 4.3 retrieval | 3 | 4% |
| 4.5 hard | 1 | 1% |

The single largest bucket after writer-repair is **code responsibility**, which no repair loop
addresses. Caveat: these are mostly pre-3.0.0 drafts, so the 4.1 share is inflated by
bookkeeping codes the compiler has since made unreachable — the 2026-08-23 A/B measured those
going to zero.

---

## 5. Feedback format — what exists versus what would need deriving

A compact repair message of the shape the brief sketches:

| Desired field | Exists today? | Source |
| --- | --- | --- |
| `error_code` | **yes** | `VerificationFinding.code` |
| `problematic_sentence` | **yes** | `sentence_index` + `char_start`/`char_end`; the text is `draft.sentences[i].text` |
| `expected_fact_ids` | **yes** | `suggested_fact_ids`, and `fact_ids` for what was bound |
| `actual_fact_ids` | **yes** | `fact_ids` |
| `expected_direction` | **yes, indirectly** | `DerivedFact.display_semantics` — a 12-member enum whose values are the phrases; the finding's `expected` already renders it in prose |
| `actual_direction` | **yes** | `observed` — e.g. `'higher'` on `unsupported_comparative` |
| `allowed_passages` | **yes** | the `P` rows of the slot table; also `writer_passages(package)` |
| `prohibited_claim` | **yes** | `plan.prohibited_claims` and the `language_safety` check's own span |
| a machine-dispatchable next action | **yes** | `Remedy`, 11 members, already on every finding |
| the offending template, pre-substitution | **yes** | `composition.json` (`_composition_payload`) records `templates[]` and `fills[]` |

**Nothing would need to be derived.** Verified from a real report
(`story-v1-01dcfff9e128/verification_report.json`):

```json
{"code": "binding_rendering_is_not_one_numeral", "severity": "REFUSE", "blocking": true,
 "sentence_index": 2, "char_start": 0, "char_end": 21,
 "fact_ids": ["obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:3eabe78a6d25"],
 "citation_ids": [], "suggested_fact_ids": [],
 "expected": "exactly one numeral in the rendered span",
 "observed": "Adjusted Gross Margin",
 "explanation": "§13.1 compares the draft's numeral against the fact at the draft's own precision…",
 "remedy": "REBIND_TO_FACT"}
```

A four-line repair instruction is constructible from that row alone. **No debug trace is needed
and none should be sent** — the finding is already the compact form.

**What the writer gate and compiler emit is weaker.** `DraftViolation` and
`CompositionViolation` carry two fields, `code` and `detail`, with everything else embedded in
an English sentence. Case 2's `rejected.json` `detail` is a **1,100-character single string** of
six concatenated violations. Dispatchable by `code`, but the sentence index and the offending
handle are only recoverable by parsing prose. If repair feedback is ever built, that is the one
place a structural change would be needed — and it is small: both types already have a `code`
field, so adding `sentence_index` and `handle` is additive.

---

## 6. Repair granularity

### A — regenerate the entire draft

* **Preserves:** package, plan, derived facts, slot table, offers — all of them, because none
  depends on the draft.
* **Loses:** every sentence, including correct ones. Sentence indices are re-assigned.
* **Easiest with existing schemas?** **Yes, by a wide margin.** `write_story` already takes
  `(package, plan, provider, derived_facts, slots, length_target, max_tokens)`. A repair is that
  call with a longer prompt. No type changes at all.
* **Provenance safety:** perfect. The new draft is compiled from scratch by the same compiler.

### B — repair only the failed sentences

* **Preserves:** everything, plus the surviving sentences verbatim.
* **Problem 1:** `Draft` requires `sentences` indexed `0..n-1` in order
  (`SentenceTemplate.index` docstring). Splicing means re-indexing, which changes
  `draft_content_sha256` for untouched sentences and therefore every recorded finding's
  `sentence_index`.
* **Problem 2:** several checks are **cross-sentence**. `citation_reused_for_unrelated_claim`
  compares against *earlier* sentences; `_unused_handle` picks a citation based on what earlier
  sentences already cited; `required_warning_absent`, `required_counterpoint_absent` and
  `thesis_abandoned` are draft-wide. Repairing one sentence can create or destroy a finding in
  another.
* **Problem 3:** there is no partial-compile entry point. `compile_draft` takes the whole
  template sequence and mints citations with a shared `already_cited` set.
* **Easiest?** No. It is the hardest of the three and the one most likely to produce a draft
  that verifies differently than either of its halves.

### C — re-run planner + writer

* **Preserves:** candidate, evidence package (digest-stable), offers.
* **Loses:** the plan, and therefore the derived facts, and therefore the slot table's `D` rows
  and their handles. `D1` may mean a different derivation in the second attempt.
* **Cost:** two model calls instead of one.
* **When it is the *only* correct choice:** category 4.2. Case 2's inverted thesis cannot be
  repaired at the writer, because a writer told *"do not say higher"* while holding a plan whose
  thesis says *"rose"* is being asked to contradict its instructions.

**The evidence does not make any option impossible.** It does show that **A is by far the
cheapest to build** (zero type changes) and that **B is the one the current cross-sentence
checks actively fight**. A single recommendation is deferred to `06-ARCHITECTURAL-OPTIONS.md`.

---

## 7. What the repair loop would have to be given

Not a design, an inventory of what `run_demo` already holds at the moment the verifier returns:

```python
inputs.candidate     # signals: direction, delta, delta_pct
package              # facts, passages, warnings, metrics, comparability
offered              # the legal derivation triples
planned.plan         # thesis, key points, counterpoints, requested derivations
derived              # DerivedFact + EvidenceScopeFact, with display_semantics
slots                # the slot table the model was shown
written.templates    # the pre-substitution sentences
compiled.draft       # the post-substitution draft with all bindings
compiled.slots       # SlotFill provenance per substitution
verified             # 85-field-per-finding structured verdict
```

Every one of these is a local variable in one function. **No new plumbing, no new stage import,
no new artifact would be required to construct a repair prompt.** The obstacle is entirely the
control flow and the single-pass contract, both of which are asserted by tests in
`tests/story/test_story_demo.py`.
