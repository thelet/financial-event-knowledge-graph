# 03 — Failure and gate map

*Every refusal code the story path can emit, grouped by the stage that emits it, with the
measured frequency across the 58 run directories in `data/story_demo/`. Verified 2026-08-26.*

---

## 1. The census

58 run directories. Dispositions (read from `demo_manifest.demo.disposition`):

| Disposition | Runs |
| --- | --- |
| `rejected` (verifier said no) | 23 |
| `accepted` | 17 |
| `provider_failed` | 8 |
| `draft_refused` (writer gate) | 6 |
| `plan_refused` | 4 |
| `derivation_refused` | 0 |
| `composition_refused` | 0 |

Providers: Qwen3.5-9B local ×43, gpt-5.4 ×8, gpt-5-nano ×6, gpt-4.1-mini ×1.
Writer prompt versions: 1.4.0 ×24, 2.1.0 ×11, 2.2.0 ×7, 2.0.0 ×7, **3.0.0 ×5**, 1.3.0 ×4.

40 runs carry a `verification_report.json`: **85 findings, 75 blocking.**

**The 3.0.0 era in full** — five runs, and this is the whole post-compiler evidence base:

| Run | Provider | Mode | Disposition | Codes |
| --- | --- | --- | --- | --- |
| `story-v1-d0d1be4a4f0e` | Qwen local | replay | **accepted** | — (0 findings) |
| `story-v1-f60f161af4c3` | Qwen local | replay | rejected | `comparative_not_supported_by_text` (the deliberate reversal proof) |
| `story-v1-fbff79b00830` | Qwen local | live | provider_failed | — (planner call) |
| `story-v1-1daff167348f` | Qwen local | **live** | draft_refused | `thesis_abandoned` |
| `story-v1-76da8465cd95` | gpt-5-nano | **live** | draft_refused | `rests_on_without_explanatory_sentence` ×2, `rests_on_not_a_passage_handle` ×4 |

**Both live 3.0.0 runs died at the writer gate. Neither reached the verifier.**

---

## 2. Codes by emitting stage

### 2.1 Candidate / detection — 0 refusal codes

Detectors do not refuse; they either fire or do not. Refused *pairs* are recorded as warnings
on the candidate (`relative_change_across_zero`, `metric_sign_convention_unverified`) and as
`SERIES_INCOMPLETE` skips inside the detector. A candidate that reaches `run_demo` has already
been selected.

### 2.2 Freshness gate — 8 codes (`story/stages/freshness/gate.py`, `RefusalCode`)

`graph_manifest_unreadable`, `extraction_run_directory_missing`,
`package_input_digest_mismatch`, `graph_unreachable`, `load_incomplete`,
`graph_run_id_mismatch`, `count_mismatch`, `ontology_hash_mismatch`.

Raises `FreshnessRefused` **before any model call**. Re-asserted at the top of `run_demo`
(`pipeline.py:643`) because the function is separately callable.

### 2.3 Evidence package — 30 warning codes, 0 refusals

`story/stages/packaging/warning_codes.py`. Two kinds: `BUILD_PROVENANCE` (about the package's
own plumbing — filtered out of `required_warnings` by `claim_qualifying_warnings`) and claim
qualifiers (which the post must state, enforced by §13 `required_warning_absent`).

### 2.4 Planner — 12 codes (`story/stages/generation/planner.py`)

| Code | Meaning | Retried? | Verifier runs after? |
| --- | --- | --- | --- |
| `unresolvable_fact_id` | plan names a fact the package lacks | no | no |
| `unresolvable_passage_id` | plan names a passage the package lacks | no | no |
| `counterpoint_missing` | counter-evidence present, no counterpoint | no | no |
| `counterpoint_ungrounded` | counterpoint rests on no counter-evidence id | no | no |
| `counter_evidence_unaccounted` | an item neither used nor excused | no | no |
| `unknown_warning_code` | `required_warnings` names a code the package lacks | no | no |
| `unknown_unusable_id` | `unusable_evidence` names an unknown item | no | no |
| `causal_language_not_computed` | plan disagrees with `causal_language_for(package)` | no | no |
| `derivation_not_offered` | triple not in `offers()` (orientation counts) | no | no |
| `thesis_empty` | blank thesis | no | no |
| `no_key_points` | empty `key_points` | no | no |
| `plan_not_constructible` | pydantic construction failed | no | no |

Measured: 1 × `unknown_unusable_id`, 1 × `plan_not_constructible` across 58 runs.

### 2.5 Derivation — 11 codes (`story/stages/derivation/public.py`, `DerivationRefusalCode`)

`derivation_not_offered`, `derived_operation_not_supported`, `derivation_input_not_in_package`,
`derivation_inputs_identical`, `derived_inputs_incomparable`, `derived_unit_mismatch`,
`derivation_period_alignment`, `derived_fact_orientation_reversed`,
`derived_relative_change_across_zero`, `derivation_denominator_zero`, `derived_result_mismatch`.

These are **values on `DerivationResult`, not exceptions** (`pipeline.py:715`). Measured: 0
occurrences in 58 runs — the offer set is doing its job.

### 2.6 Writer structural gate — 15 codes (`story/stages/generation/writer.py`)

| Code | Meaning | 3.0.0-reachable? | Retried? | Verifier runs after? |
| --- | --- | --- | --- | --- |
| `no_sentences` | empty `sentences` | yes | no | **no** |
| `draft_not_constructible` | `kind` outside the enum, `rests_on` not a list | yes | no | **no** |
| `thesis_abandoned` | templates name no fact the plan requires | yes | no | **no** |
| `malformed_slot` | braces the grammar cannot read | yes | no | **no** |
| `rests_on_without_explanatory_sentence` | non-`explanatory` sentence rests on a passage | yes | no | **no** |
| `rests_on_not_a_passage_handle` | `rests_on` entry is not a `P` row | yes | no | **no** |
| `plan_names_another_package` | plan and package are different candidates | yes (pre-call) | no | **no** |
| `unresolvable_fact_id` | | **pre-3.0.0 replay only** | no | no |
| `unresolvable_passage_id` | | pre-3.0.0 replay only | no | no |
| `binding_rendering_not_in_text` | | pre-3.0.0 replay only | no | no |
| `binding_rendering_ambiguous_in_sentence` | | pre-3.0.0 replay only | no | no |
| `unresolvable_evidence_handle` | | pre-3.0.0 replay only | no | no |
| `evidence_handle_out_of_bounds` | | pre-3.0.0 replay only | no | no |
| `citation_quote_not_in_passage` | | pre-3.0.0 replay only | no | no |
| `citation_quote_ambiguous_in_passage` | | pre-3.0.0 replay only | no | no |

The last eight are reachable only through `draft_from`, the retained pre-3.0.0 parser, which
`run_demo` no longer calls. They stay registered because the 50 stored `draft.json` artifacts
still parse through it.

Measured across 58 runs: `rests_on_not_a_passage_handle` ×4, `binding_rendering_not_in_text` ×3,
`citation_quote_not_in_passage` ×2, `rests_on_without_explanatory_sentence` ×2,
`thesis_abandoned` ×1.

### 2.7 Draft compiler — 9 codes (`story/stages/composition/public.py`)

| Code | Meaning | Retried? | Verifier runs after? |
| --- | --- | --- | --- |
| `unknown_slot_handle` | `{{F9}}` where the table stops at `F2` | no | **no** |
| `unknown_slot_field` | `{{F1.colour}}` — outside `SLOT_FIELDS` | no | **no** |
| `field_not_offered_by_row` | the field exists but this row has no legal value | no | **no** |
| `slot_without_binding` | `{{F3.period}}` with no `{{F3}}` (invariant R4) | no | **no** |
| `no_legal_rendering` | `{{D1}}` on a word-valued row | no | **no** |
| `no_evidence_handle_for_bound_fact` | a bound fact whose evidence cannot be cited | no | **no** |
| `passage_handle_unknown` | `rests_on: ["P7"]` where the table stops at `P1` | no | **no** |
| `rests_on_without_explanatory_kind` | compiler's copy of the writer-gate rule | no | **no** |
| `template_not_compilable` | unclosed brace / bad handle shape | no | **no** |

Measured: 0 occurrences in 58 runs — because the writer gate above catches the same faults
first, under different names.

> **Gap (BUG).** None of these nine is in `story/demo_ui/code_catalogue.py`. `declared_codes`
> knows five families — `verification_gate` (102), `package_warning` (30), `freshness_refusal`
> (8), `planner_refusal` (12), `writer_refusal` (15) — and no composition family. `api.py:2002`
> selects the family by disposition and falls through to `FAMILY_VERIFICATION` for
> `composition_refused`, so `_explanations` finds nothing and emits
> `{"description": "", "severity": "", "remedy": "", "blocking": false}`. **`blocking: false`
> is untrue** — the run was refused. The totality test in
> `tests/story/test_demo_ui_code_catalogue.py:151` walks only `PlanViolation(...)` and
> `DraftViolation(...)` constructions, so it does not catch this. There is also **no
> `compiling_draft` trace stage** in `demo_ui/trace.py:STAGES_BY_PHASE`.

### 2.8 Deterministic verifier — 102 codes

96 `REFUSE`, 2 `WARN` (`over_precision`, `paraphrase_distance`), 4 `ANNOTATE`
(`event_review_flag`, `conflict_immaterial_at_stated_precision`, `warned_observation_used`,
`column_label_ambiguity_classified`). Twelve named checks in order:

`identity_and_freshness`, `numbers`, `units`, `percentages`, `periods`, `metric_identity`,
`subject_identity`, `citations`, `reported_vs_calculated`, `language_safety`, `title`,
`disclosures`.

Measured over the 40 stored reports (85 findings):

| n | Severity | Code |
| --- | --- | --- |
| 18 | REFUSE | `unbound_numeral` |
| 15 | REFUSE | `citation_reused_for_unrelated_claim` |
| 13 | REFUSE | `comparative_not_supported_by_text` |
| 8 | WARN | `over_precision` |
| 5 | REFUSE | `metric_surface_unresolved` |
| 3 | REFUSE | `binding_rendering_is_not_one_numeral` |
| 3 | REFUSE | `metric_surface_ambiguous` |
| 3 | REFUSE | `package_content_digest_mismatch` |
| 2 | WARN | `paraphrase_distance` |
| 2 | REFUSE | `connective_sentence_carries_a_claim` |
| 2 | REFUSE | `period_named_in_text_contradicts_binding` |
| 2 | REFUSE | `calculation_result_surface_mismatch` |
| 2 | REFUSE | `derived_unit_mismatch` |
| 1 each | REFUSE | `metric_surface_absent_from_text`, `calculation_does_not_recompute`, `unsupported_comparative`, `period_unresolvable`, `period_mismatch`, `number_outside_tolerance`, `derived_operation_not_supported` |

Most of these are pre-3.0.0 drafts. The A/B measured on 2026-08-23 (recorded in
`docs/2026-08-23-deterministic-draft-compiler/02-IMPLEMENTATION-PLAN.md` §7) showed the
bookkeeping codes going to **zero** under the compiler and the survivors being
`comparative_not_supported_by_text` (reversed claims — genuinely false),
`citation_reused_for_unrelated_claim` (the code-vs-code collision described in `02-MODEL-VS-DETERMINISTIC-RESPONSIBILITIES.md` §6) and
`unbound_numeral` (invariant R4).

### 2.9 Runtime / provider — 6 error classes (`story/providers/public.py`)

`StoryProviderConfigurationError`, `StoryProviderUnavailable`, `StoryProviderTimeout`,
`StoryProviderTransportError`, `StoryProviderResponseError`, `StoryProviderSchemaError`.

Only the transport family is retried, inside the adapter. Measured: 8 `provider_failed` runs.

### 2.10 Model verifier — does not exist

There are exactly two `provider.generate` call sites. There is no model-based verification stage
and none is stubbed.

---

## 3. Gate ordering, exactly

Read from `run_demo` (`story/pipeline.py:645-772`):

```text
1  freshness gate                     raise  FreshnessRefused
2  offers(package, candidate)         pure
3  plan_story  ├─ provider.generate   raise  StoryProviderError   → provider_failed
             ├─ schema check (server + StoryProviderSchemaError)
             ├─ EditorialPlan construction  → plan_not_constructible
             └─ plan_violations       raise  EditorialPlanRejected → plan_refused
4  execute_all(requested)             value refusals              → derivation_refused
5  slot_table(package, derived, passages)   pure
6  write_story ├─ plan_names_another_package (BEFORE the call)
             ├─ provider.generate    raise  StoryProviderError   → provider_failed
             ├─ schema check
             └─ templates_from       raise  DraftRejected        → draft_refused    ◀ 15 codes
7  compile_draft                      raise  CompositionRefused   → composition_refused ◀ 9 codes
8  DeterministicVerifier.verify       returns VerifiedDraft       → accepted | rejected ◀ 102 codes
```

So the brief's guess —

```text
planner → schema validation → writer → writer contract checks → deterministic verifier
```

— is **correct as far as it goes, and misses two stages**: a deterministic derivation stage
between planner and writer, and a deterministic draft compiler between the writer's contract
checks and the verifier. It also misses that `plan_violations` runs between the planner and the
derivation stage, and that `plan_names_another_package` fires *before* the writer's model call
(deliberately — spending a generation to discover it would put a wrong answer in the replay
store under a legitimate-looking request).

---

## 4. Why `verifier ran: no`

The four guards in `run_demo` are strictly nested:

```python
if planned is not None:                             # derivation
if planned is not None and disposition != DERIVATION_REFUSED:   # writer
if planned is not None and written is not None:     # compiler
if planned is not None and compiled is not None:    # verifier      ◀
```

`templates_from` raises `DraftRejected`, so `written` stays `None`, so `compiled` stays `None`,
so the verifier block is skipped. The demo UI reports `verifier_ran: outcome.verified is not
None` (`api.py:2029`), which is then `false`.

The trace confirms it (`trace_events.jsonl`, both cases, verified):

```text
planning                     passed
drafting                     running
offering_derivations         passed
executing_derivations        passed
derived_facts_added          passed
binding_facts_and_citations  skipped     ◀
drafting                     failed      ◀
checking_numbers_and_units   skipped
checking_metrics_and_periods skipped
checking_citation_support    skipped
checking_causal_language     skipped
rendering                    warning
```

### What can therefore exist unverified

**In `editorial_plan.json`:** anything that is referentially valid and semantically false.
Specifically, and observed:

* a thesis that inverts the direction of the move (`story-v1-76da8465cd95`);
* a key point asserting `2022Q3 > 2022Q2` when the package says the opposite;
* a `statement_class` that does not match what the point names;
* a `structure[]` that is the schema's own field names;
* free-prose `uncertainty` and `prohibited_claims` that nothing reads;
* a counterpoint whose *claim* contradicts the facts it is grounded in — grounding is checked,
  agreement is not.

The plan is written to disk and rendered by the demo UI's plan panel regardless.

**In a `draft_refused` artifact:** the model's raw answer is preserved in `generations.jsonl`
(`raw_content`), so a false sentence is durable and inspectable, but it has **no verification
verdict attached** — there is no record anywhere in the run saying whether the prose was true.
`rejected.json` names only the structural codes.

That is the substantive cost of putting a model-contract gate above the authoritative verifier:
**a factually wrong post and a merely malformed one are recorded identically.**

---

## 5. The planner validation boundary

> Can a plan that says `556 → 110 = increase` reach the writer?

**Yes.** Verified: `story-v1-76da8465cd95`'s plan says exactly that and was passed to
`write_story`, which built a prompt from it and called gpt-5-nano.

**Why.** `plan_violations` inspects `required_fact_ids`, `required_citation_passage_ids`,
`required_warnings`, `requested_derivations`, `unusable_evidence`, `causal_language`, and the
emptiness of `thesis` and `key_points`. It never reads the *text* of `thesis` or `claim`. The
whole function is 100 lines and none of them tokenise a claim.

**What deterministic information is available at that point:**

| Available | Where |
| --- | --- |
| `direction: "decrease"` | `inputs.candidate.signals["direction"]` — a closed word from `quantity_direction` |
| `delta: -446000000.0`, `delta_pct: -80.2` | same `signals` mapping |
| Both raw values, both periods, both period shapes | `package.facts[].value`, `.period_key`, `.period_start/end` |
| Chronological order | `core/periods.py:months_between("2022Q2", "2022Q3") == 3` |
| The legal orientation of every derivation | `offered` — already an argument to `plan_violations` |
| The direction *phrase* | after `execute_all`, `DerivedFact.display_semantics == "decreased by"` |
| A direction vocabulary to match prose against | `DisplaySemantics` — 12 members whose values are the English phrases |

Everything a check would need is already in `run_demo`'s local scope when `plan_violations` is
called, except the candidate, which is one field away (`inputs.candidate`).

**The failure was caught later, by the verifier — in a run where the draft reached it.**
Verified 2026-08-26 by recompiling case 2's own sentences with only `rests_on` repaired:

```text
REFUSE  unsupported_comparative  sentence 0
        expected  "operation=compare_levels or compare_deltas with both sides' inputs"
        observed  'higher'
```

So the system does defend against the inversion — but only through the writer faithfully
carrying it into prose, and only if the draft survives the writer gate. In this run it did not.

---

## 6. Retryability and downstream verification, summarised

| Stage | Codes | Currently retried | Downstream verifier runs |
| --- | --- | --- | --- |
| Freshness | 8 | no | no |
| Package | 30 warnings | n/a | n/a |
| Planner + plan validation | 12 | no | no |
| Derivation | 11 | no | no |
| Writer gate | 15 | no | **no** |
| Compiler | 9 | no | **no** |
| Verifier | 102 | no | n/a — it is the verifier |
| Provider transport | 6 classes | **yes, ≤2 attempts, 5 statuses** | depends |

The only retry in the entire story path is `DEFAULT_MAX_RETRIES = 2` over
`RETRYABLE_STATUSES = {429, 500, 502, 503, 504}` inside the two HTTP adapters
(`providers/public.py:86,96`). A schema violation and a timeout are explicitly **never** retried
— `story/contracts.py:165`: *"A schema violation is the model's answer and is never retried; a
timeout is never retried either. Only transport faults are retryable — one axis, six error
classes."*
