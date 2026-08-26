# 06 — Architectural options

*Five approaches compared against the implementation. **No winner is chosen here.** The two
case studies, the test inventory and the twenty questions follow the comparison.
Verified 2026-08-26.*

---

## 1. Option A — continue improving the current planner/writer contracts

**What it is.** Keep two model calls, one pass, no repair. Fix failures by editing prompts,
tightening schemas, and moving individual fields to code.

**What the code says for it.**

* The mechanism is proven. The 2026-08-23 change did exactly this and the A/B it recorded
  (`docs/2026-08-23-deterministic-draft-compiler/02-IMPLEMENTATION-PLAN.md` §7) took blocking
  findings from 74 to 26 across 22 replayed cases, with every bookkeeping code going to zero.
* Four of the remaining model-authored machine fields are one small change each
  (`02-MODEL-VS-DETERMINISTIC-RESPONSIBILITIES.md` §7).
* No new stage, no new artifact, no manifest change, no UI change.
* The unguarded direction check is the single highest-value item and it needs one function and
  one argument (`plan_violations(…, candidate=)`).

**What the code says against it.**

* **It does not address case 1 at all.** The model emitted no slots. There is no field left to
  move; the failure is engagement with the contract, and the recorded evidence
  (2026-08-23, and again 2026-08-26) is that Qwen3.5-9B flips between compliant and
  non-compliant on *prompt length near rule 6* at a temperature where its answers are otherwise
  byte-reproducible.
* The writer's request is already **~4,364 tokens against `context_tokens: 8192`**. There is
  room, but the failure mode is sensitivity to what is *in* those tokens, not to how many.
* It leaves the two structural problems untouched: the model-contract gate above the
  authoritative verifier, and the compiler-vs-§13.7 citation collision.

**Failure classes it addresses:** category 4.4 (code responsibility) and part of 4.2 (planner).
**Failure classes it does not:** 4.5 (hard), and the slot-compliance class that produced case 1.

---

## 2. Option B — current pipeline + verifier repair loop

**What it is.** `planner → writer → verifier → structured feedback → writer repair → verifier`,
bounded.

**What the code says for it.**

* The feedback structure exists in full — 13 fields per finding, an 11-member `Remedy` enum,
  spans, fact ids and citation ids. **Nothing would need deriving**
  (`04-REPAIR-LOOP-FEASIBILITY.md` §5).
* Everything a repair needs is a live local variable in `run_demo` when the verifier returns.
* `request_identity` digests `prompt`, so a repair call keys to its own store row automatically.
* Option A's "regenerate the whole draft" form needs **zero type changes**.

**What the code says against it.**

* **It would not have run on either recent failure.** Both died at `templates_from`, upstream of
  the verifier. A repair loop that begins at the verifier begins after the point where these
  runs ended. Making it help would first require moving the writer's structural gate below the
  verifier, or giving the writer gate its own repair — a different mechanism.
* **24 of 85 recorded findings are code-responsibility** (category 4.4, measured). Sending
  `citation_reused_for_unrelated_claim` back to the model asks it to repair a span the compiler
  chose.
* **A blind retry is measurably useless on the local path.** 4 of 4 repeated live requests to
  the llama.cpp server returned identical answers. Only a *changed* prompt has a mechanism.
* Sentence-level repair fights the cross-sentence checks
  (`04-REPAIR-LOOP-FEASIBILITY.md` §6B) and would need a partial-compile entry point the
  compiler does not have.
* `pipeline.py` records provenance for exactly two model calls.

**Failure classes it addresses:** most of category 4.1, if the loop is placed where those codes
fire. **Does not address:** 4.2 without a planner repair, 4.3, 4.4, 4.5.

---

## 3. Option C — simplify the writer contract further

**What it is.** Code takes ownership of bindings, citation mapping, numeric rendering, derived
facts and remaining bookkeeping; the writer generates prose.

**What the code says.** **This is largely done.** Since `WRITER_PROMPT_VERSION` 3.0.0 the writer
emits `title`, `text`, `kind`, `rests_on` — four leaves — and the compiler builds every
`FactBinding`, `PassageCitation` and `Calculation`. The verified compile of the metric_move
candidate shows nine binding/citation values, none from the model.

**What is left to take.**

| Remaining | Effort | Effect |
| --- | --- | --- |
| `sentences[].kind` | derive from which slot rows a template names | removes 2 writer-gate codes and one §13.9 check |
| `rests_on` as an index rather than a handle | small | removes `rests_on_not_a_passage_handle` — **case 2's failure** |
| the compiler's explanatory-citation choice | change `_unused_handle`, or teach §13.7 about a passage rest | removes the largest code-owned finding class |
| `{{D2}}` → `80.215827338%` | round in `derived_figure` | removes 8 recorded `over_precision` warnings |

**The honest limit.** After all four, the writer's schema would be `title`, `text` and a
sentence classification code could derive — i.e. **prose and nothing else**. And case 1 would
still fail, because a model that writes no slots writes no slots regardless of how few other
fields it is asked for.

**Failure classes it addresses:** 4.4 almost entirely, part of 4.1.
**Does not address:** 4.2 (planner inversion), the slot-compliance class.

---

## 4. Option D — Research Agent + deterministic tools + simple writer

**What it is.** Replace the single-shot planner with a tool-using agent over the graph, producing
a validated research brief, then the existing writer and verifier.

**What the code says for it.**

* **The tool layer already exists and is already the right shape.** `GraphRetriever.call(tool,
  parameters)` with nine tools, declared parameters, per-tool row caps, a closed result
  vocabulary and a mandatory trace. Three more tools are declared-and-unavailable with reasons.
* **Raw Cypher is not needed and could not be granted** — four families of passing tests forbid
  it (`05-RESEARCH-AGENT-FEASIBILITY.md` §4).
* Everything downstream of the plan is reusable unchanged: offers, derivation, slot table,
  compiler, verifier, replay store.
* The minimum semantic output is about **five** fields against the planner's current nineteen.
* It is the only option that addresses **why the planner said "rose"**: an agent that calls
  `compare_metric_periods` or is handed `quantity_direction`'s answer is not inferring a
  direction from two raw floats printed six lines apart.

**What the code says against it.**

* **It does not touch the writer at all**, and the writer is where both recent runs failed.
* The seven derivation operations are not yet tool-shaped — they take a `ValidatedDerivation`,
  not two ids.
* `pipeline.py`'s two-call provenance, `demo_ui/trace.py`'s 14 fixed generation stages, and
  `_token_totals`' list-of-two assumption all encode the current shape.
* `StoryEvidencePackage` changes role from *input* to *result*, which touches
  `package_content_digest` — a `story_run_id` input.
* Cost: N tool calls plus reasoning per run instead of one planner call.

**Failure classes it addresses:** 4.2 and 4.3 well. **Does not address:** 4.1, 4.4, 4.5, and the
slot-compliance class.

---

## 5. Option E — Research Agent + repair loop

Union of D and B. Addresses 4.1, 4.2 and 4.3; still does not address 4.4 (code responsibility)
or the slot-compliance class, and inherits both of B's structural obstacles and all of D's.
It is the largest change and the only one that would leave *no* current failure class
un-attacked except the two that are code's own.

---

## 6. Comparison matrix

Qualitative, each cell grounded in the implementation. No scores.

| Criterion | A: current | B: + repair | C: simpler writer | D: research agent | E: agent + repair |
| --- | --- | --- | --- | --- | --- |
| **Reuses the graph** | unchanged | unchanged | unchanged | unchanged — `BoundedGraphRetriever` is already the whole surface | unchanged |
| **Reuses the evidence package** | unchanged | unchanged | unchanged | **role changes** — becomes a result, touching `package_content_digest`, a run-id input | role changes |
| **Removes model bookkeeping** | already at 4 writer leaves; 4 more fields available | none — repair does not move ownership | to prose-only; the last mechanical field is `kind` | planner side: 19 → ~5 | both sides |
| **Handles planner factual inversion** | only with a new check; the data is already in `run_demo` | only with a *planner* repair; a writer repair would contradict its own plan | no | **yes** — a tool answers the direction instead of the model inferring it | yes |
| **Supports richer multi-fact posts** | limited — `max_facts: 12`, `max_derivations: 12`, one pass | limited | limited | **best** — an agent can widen retrieval within the same caps | best |
| **Implementation complexity** | lowest — one function, one argument | medium — control flow, feedback assembly, 2 stage-gate moves, N-call provenance | low-medium — 4 localised changes | **highest** — new stage, package role, trace shape, provenance | highest |
| **Deterministic auditability** | **highest** — 2 calls, 10-input digest, byte-reproducible replay | good — each repair keys its own row; run count varies | highest | **weakest** — N tool calls per run; `retrieval_trace[]` exists but run identity spans a variable number of generations | weakest |
| **Expected token cost** | ~4.4k writer + ~2.8k planner | +1 writer call per repair round | slightly lower (shorter prompts) | N tool calls + reasoning; unbounded without a cap | highest |
| **Failure surface** | 15 writer + 9 compiler + 102 verifier codes | + repair-loop states (budget exhausted, oscillation, repair-made-it-worse) | −2 writer codes, −1 §13 check | + tool-call errors, retrieval refusals, brief validation; **−12 planner codes** | union |
| **Fixes case 1 (no slots emitted)** | **no** | no — never reaches the loop | **no** | **no** | no |
| **Fixes case 2 (`rests_on` misuse)** | partly — as an index, yes | yes, if the loop covers the writer gate | **yes** | no | yes |
| **Fixes case 2's inverted plan** | yes, with a check | with a planner repair | no | **yes** | yes |

**The row that matters most:** *no option fixes case 1.* Slot-grammar compliance is a
prompt-and-model property, and it is the failure mode the 3.0.0 boundary created. Any
architecture chosen here should be chosen knowing it does not answer that question, and that the
question needs its own measurement (§9).

---

## 7. Case study 1 — `story-v1-1daff167348f`

```text
provider  local_openai_compatible          model  Qwen3.5-9B-Q4_K_M.gguf
mode      live                             temperature 0.0, sent: true
prompt    story_editorial_plan 1.2.0 · story_post_draft 3.0.0
```

| Step | Outcome |
| --- | --- |
| candidate | `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`; `signals.direction = "decrease"`, `delta = -446000000.0`, `delta_pct = -80.215827338` |
| evidence | 2 facts (556000000.0 / 110000000.0 USD), 2 primary passages, 5 warnings, 0 counter-evidence |
| offers | 3 triples: `absolute_change`, `percentage_change`, `crossed_zero`, all 2022Q2 → 2022Q3 |
| plan | **correct.** *"Opendoor's Adjusted Gross Profit fell from 556 million USD in 2022Q2 to 110 million USD in 2022Q3."* 3 key points, 2 derivations requested |
| plan validation | passed |
| derivations | 2 `DerivedFact`s + 1 `EvidenceScopeFact`. `display_semantics: "decreased by"` on both |
| slot table | F1, F2, D1, D2, P1, P2 |
| writer request | ~4.4k tokens, 14 rules, worked example, 12 sections |
| **returned structure** | three sentences, **zero slots** |
| **first failing gate** | `templates_from` → `thesis_abandoned` |

The model's answer, verbatim:

```json
{"title": "Opendoor 2022Q3", "sentences": [
 {"text": "$556 million Adjusted Gross Profit in the second quarter of 2022.",
  "kind": "reported", "rests_on": []},
 {"text": "$110 million Adjusted Gross Profit in the third quarter of 2022.",
  "kind": "reported", "rests_on": []},
 {"text": "Adjusted Gross Profit decreased by $446 million from the second quarter of 2022 to the third quarter of 2022.",
  "kind": "calculated", "rests_on": []}]}
```

Every figure is right. Every period is right. The direction is right. `$446 million` is exactly
what `{{D1}}` would have inserted. It is, as prose, the post.

> **Would the proposed repair loop have repaired this?** **Probably, and it is the wrong
> mechanism.** The feedback would be mechanical — *"sentence 0: write `{{F1}}` where you wrote
> `$556 million`"* — and the mapping is exact, because the model's strings are the slot values.
> But a blind re-ask is provably useless here (4 of 4 identical live repeats), and a repair that
> has to rewrite every sentence of a correct draft is a prompt failure being paid for at
> inference time, once per run, forever.
>
> **Would moving machine bookkeeping into code make this failure cease to exist?** **No — this
> failure *is* the bookkeeping already being in code.** Before 3.0.0 this exact answer would have
> been accepted as prose and then refused for `unbound_numeral` on `2022`, or accepted outright.
> The boundary is what makes a typed figure illegal. That is the intended safety property
> working; the cost is that the model must learn a grammar, and this one did not.

---

## 8. Case study 2 — `story-v1-76da8465cd95`

```text
provider  openai                           model  gpt-5-nano
mode      live                             temperature 0.0 RECORDED BUT NOT SENT; reasoning_effort: minimal
prompt    story_editorial_plan 1.2.0 · story_post_draft 3.0.0
```

Same candidate, same package, same 3 offers.

| Step | Outcome |
| --- | --- |
| plan | **factually inverted.** *"Opendoor's adjusted gross profit **rose** from 2022Q2 to 2022Q3…"*; key point 1: *"Adjusted gross profit for 2022Q3 is **higher** than 2022Q2"*; `structure` = the schema's own field names; 1 derivation requested |
| plan validation | **passed** — all 12 checks are referential |
| derivations | 1 `DerivedFact`, `display_semantics: "decreased by"` — *directly contradicting the plan it was derived for* |
| writer | used slots correctly; put `F2`, `D1`, `F1` in `rests_on` |
| **first failing gate** | `templates_from` → `rests_on_without_explanatory_sentence` ×2, `rests_on_not_a_passage_handle` ×4 |

> **Would the repair loop have repaired this?** **The structural half, yes; the factual half,
> no.** `rests_on: ["F2"]` → `[]` is mechanical. But the sentence underneath is
> *"…remains higher in the third quarter of 2022 than the second quarter of 2022"*, and the
> writer is carrying out its plan. A writer repair told *"do not say higher"* is told to
> contradict its own instructions. This is category 4.2: **planner repair, or nothing.**
>
> **Would moving machine bookkeeping into code make this failure cease to exist?** **The
> structural half, yes. The factual half, no — and the factual half is worse.**
>
> Measured: recompiling the model's own four sentences with **only `rests_on` mechanically
> repaired** (`["F2"]`→`[]`, `["D1"]`→`[]`, `["F1","F2"]`→`["P1"]`) compiles cleanly and the
> verifier returns five blocking findings:
>
> ```text
> REFUSE unbound_numeral                     s0  observed '2022'
> REFUSE unbound_numeral                     s2  observed '2022'
> REFUSE metric_surface_absent_from_text     s1  observed '$446 million decreased by the second quarter of 2022 to the third quarter of 2022.'
> REFUSE citation_reused_for_unrelated_claim s2  observed 'no fact binding at all'
> REFUSE unsupported_comparative             s0  observed 'higher'
> ```
>
> Two of those are the model typing periods in prose (correct refusals). One is a sentence that
> names no metric. **One is the compiler's own citation choice** (`02-MODEL-VS-DETERMINISTIC-RESPONSIBILITIES.md` §6). And one —
> `unsupported_comparative` on `'higher'` — is **the verifier catching the planner's factual
> inversion**. The system does defend against it; it just never got the chance, because the
> writer gate ended the run first.

### The control: the same candidate, done right

Compiled from three hand-written templates over the run's own recorded package, plan and derived
facts (verified 2026-08-26):

```text
[0] reported   Opendoor reported Adjusted Gross Profit of $556 million in the second quarter of 2022.
[1] reported   It reported Adjusted Gross Profit of $110 million in the third quarter of 2022.
[2] calculated Between the second quarter of 2022 and the third quarter of 2022, Adjusted Gross
               Profit decreased by $446 million.

passed: True        findings: none, at any severity
```

**The candidate is writable, the evidence supports it, and the verifier accepts it.** Both live
runs failed for reasons that have nothing to do with whether this post can be made.

---

## 9. Tests — what would protect a redesign, and what is missing

3,861 passing, 1 skipped, 0 failing across `tests/story/` (run 2026-08-26, exit 0).
53 files, ~44,800 lines.

### Coverage that exists

| Area the brief named | Where | Strength |
| --- | --- | --- |
| Deterministic derived facts | `test_story_derivation.py` (42), `test_story_verification_derived.py` (69) | **strong** — every operation, every refusal code, orientation reversal |
| Period ordering | `test_story_series.py` (47), `test_story_renderings.py` | **strong** — `months_between`, shape classification, the closed period grammar |
| Metric direction | `test_story_detector_metric_move.py` (43), `test_story_derivation.py` | **strong for the derivation**; **absent for the plan** — see below |
| Plan factual consistency | — | **none** |
| Thesis preservation | `test_story_writer.py` — `_thesis_violations` cases | **medium** — tests the handle-set rule, not the semantics |
| Fact bindings | `test_story_composition.py` (33), `test_story_writer.py` (91) | **strong** — every binding field is asserted to come from the row |
| Passage bindings | `test_story_evidence_handles.py`, `test_story_composition.py` | **strong** |
| Citation rendering | `test_story_deterministic_verifier.py` (115), `test_story_composition.py` | **strong** |
| Verifier error codes | `test_story_deterministic_verifier.py`, `test_demo_ui_code_catalogue.py` | **strong** — the catalogue is asserted total over an AST walk of every `PlanViolation(...)` and `DraftViolation(...)` |
| Generation provider abstraction | `test_story_provider.py` (73), `test_story_provider_openai.py` (72), `test_story_contracts.py` (42) | **strong** — six error classes, the retry axis, protocol conformance driven through behaviour |
| Replayability | `test_story_demo.py` (65), `test_story_provider.py` | **strong** — `request_identity`'s ten inputs, store round-trips, committed digests |
| Story-run determinism | `test_story_demo.py` | **strong** — golden artifact digest tables, an injected clock |
| Architecture rules | `test_story_package_structure.py` (20) | **strong** — import direction, no stage-to-stage import, no cycles, one composition root |
| Cypher safety | `test_story_retrieval_cypher.py` | **strong** — read-only, bounded, parameterised, allowlisted, plus a meta-test that the scan is not vacuous |
| A/B of the compiler boundary | `test_story_composition_regression.py` (9 tests over a 22-case corpus) | **strong** — replays every recorded rejection through the new boundary |

### Missing coverage, stated separately

1. **No test asserts a plan agrees with the facts it names.** Nothing compares a `thesis` or
   `claim` against `candidate.signals["direction"]`, `PackagedFact.value`, or
   `DerivedFact.display_semantics`. A test asserting *"a plan saying `rose` over 556 → 110 is
   rejected"* would fail today.
2. **No test measures slot-grammar compliance.** There is no fixture, metric or assertion for
   *"the model emitted at least one slot"*, which is exactly what case 1 failed. `live`-marked
   tests exist (5 files) but assert the chain runs, not that the answer used the contract.
3. **The composition codes are outside the catalogue totality test.** The AST walk covers
   `PlanViolation(...)` and `DraftViolation(...)` only, so the nine `CompositionViolation(...)`
   codes are unprotected — and are in fact missing from the UI catalogue.
4. **No test covers the `composition_refused` disposition end-to-end in the UI.**
   `test_story_demo.py:2218` reaches the disposition; nothing asserts what the API renders.
5. **No trace stage for the compiler.** `demo_ui/trace.py:STAGES_BY_PHASE` lists 14 generation
   stages and none is `compiling_draft`, so a compile-time refusal is invisible on the timeline.
6. **No test pins the derived-figure precision.** `{{D2}}` → `80.215827338%` passes everything;
   `over_precision` is WARN.
7. **No repeated-request determinism test.** The variance measured in
   `04-REPAIR-LOOP-FEASIBILITY.md` §3 was found by mining stored artifacts, not by a test.

**Which of these would protect a redesign:** 1 and 2 protect any option; 3–5 protect C and B;
6 is independent; 7 is what any repair-loop decision should rest on.

**Nothing in this audit changed a test.**

---

## 10. The twenty questions

1. **Why is the model responsible for each machine-facing field it emits?**
   For four fields, historical convenience — the verifier needed metadata and asking the model
   was easiest. Five of those were removed on 2026-08-23. What remains is either the product
   (`text`, `title`, `thesis`, `claim`) or a genuinely semantic selection (`rests_on`'s choice,
   `required_fact_ids`), with one exception: `sentences[].kind`.

2. **Which of those fields can code already derive?**
   `key_points[].required_citation_passage_ids`, `key_points[].statement_class`,
   `requested_derivations[]` (all three fields), `required_warnings[]`, `sentences[].kind`.
   Plus the thing that is not a field: the direction of the move.

3. **Can the planner currently contradict deterministic facts?**
   **Yes.** Observed in `story-v1-76da8465cd95`.

4. **What prevents it?**
   Nothing at plan time. Downstream, `unsupported_comparative` and
   `comparative_not_supported_by_text` catch it — **if** the writer carries the inversion into
   prose **and** the draft survives the writer gate.

5. **If nothing prevents it, where could validation occur?**
   In `run_demo` immediately after `plan_story`, or inside `plan_violations` with the candidate
   passed in. Everything needed — `signals["direction"]`, both values, both periods, `offered`,
   and the 12-member `DisplaySemantics` vocabulary to match prose against — is already in scope.

6. **Why can a writer failure prevent the authoritative verifier from running?**
   `templates_from` raises `DraftRejected`, so `written` is `None`, so `compiled` is `None`, so
   the verifier's guard `planned is not None and compiled is not None` is false. The 15 writer
   codes and 9 compiler codes are gates placed above the 102-code verifier.

7. **Which failures would a retry loop actually solve?**
   Category 4.1 — **43 of 85** recorded findings (51%), measured — and only if the loop is
   placed where those codes fire. Neither recent failure is among them as the pipeline stands.

8. **Which failures should never be solved with retries?**
   Category 4.4 (code responsibility, **24 findings** — the model did not choose the thing
   being refused) and category 4.5 (hard: `no_sentences`, `thesis_abandoned`-as-observed, incomparable
   periods, digest mismatches, transport faults). Also every case where a blind retry is used
   against the local server: 4 of 4 repeats returned identical answers.

9. **Is verifier output structured enough to become repair feedback?**
   **Yes, completely.** 13 fields per finding including spans, fact ids, citation ids, `expected`,
   `observed`, and an 11-member `Remedy`. The *writer gate's* output is weaker — two fields, with
   the sentence index buried in prose.

10. **Could repairs preserve the exact same factual evidence?**
    **Yes.** Package, plan, derived facts, offers and slot table are all values that do not
    depend on the draft.

11. **Should a writer repair receive the original plan or a validated plan?**
    Evidence, not a recommendation: with the *original* plan, case 2's repair is asked to
    contradict its instructions. With a *validated* plan, the run needs a planner repair first,
    which is a second mechanism. The corpus contains at least one plan (`story-v1-76da8465cd95`)
    where "original" is provably the wrong answer.

12. **Which failures require returning to research/retrieval rather than writing?**
    Category 4.3: `fact_not_in_package`, `citation_not_in_package`, `date_not_in_package`,
    `unpopulated_metric`, `absence_not_provable_from_bounded_package`,
    `no_evidence_handle_for_bound_fact`, every digest mismatch (`REBUILD_PACKAGE` is already the
    declared remedy), and every freshness code.

13. **What reusable graph/retrieval functions could become Research Agent tools?**
    Nine already are. Near-misses needing a thin wrapper: the seven derivation operations,
    `months_between`, `quantity_direction`, `series.comparable`, `offers`. Absent by design:
    anything resembling `get_graph_neighbours`.

14. **Would a Research Agent need raw Neo4j access?**
    **No**, and it could not be given it without deleting four families of passing tests.

15. **Which parts of the existing evidence-package architecture remain useful?**
    All of it. Under option D its *role* changes from input to result, which touches
    `package_content_digest` — a `story_run_id` input — and that is the one real cost.

16. **Could the writer be reduced to mostly prose generation?**
    **It already is** — 4 leaves, of which 2 are prose. One more (`kind`) is derivable.

17. **What would be lost by doing so?**
    Nothing that has been measured. The 2026-08-23 A/B took blocking findings from 74 to 26 with
    no check weakened and no artifact type changed. What was *gained* was a new failure mode:
    the model must now use a grammar, and two providers failed to on the same day.

18. **Which architecture best supports future multi-fact/economic-connection posts?**
    **D or E.** The current package is capped at 12 facts, 12 derivations, 4 relationships and
    5,000 tokens, chosen before the plan exists. A multi-fact post needs evidence selected
    *because of* the thesis, which is what an agent's retrieval loop is for. A, B and C all
    inherit a fixed pre-plan package.

19. **Which architecture keeps deterministic verification authoritative?**
    **All five keep it authoritative, and A and C keep it most reliably *reached*.** The
    verifier is downstream of everything in every option. What differs is how often a run gets
    to it — and today, 2 of the 2 recent live runs did not.

20. **What is the smallest vertical experiment that could distinguish these approaches?**
    Stated as evidence, not as a recommendation. The cheapest measurement that separates the
    options is **slot-grammar compliance across providers and prompt wordings on one candidate**
    — because §6's matrix shows the compliance failure is the one thing no architectural option
    addresses, and the choice between A/C (small, cheap) and D/E (large) should not be made
    before knowing whether the writer's contract is usable at all by the models on hand. It
    needs no code change: run `metric_move` live against Qwen and one OpenAI model, N times
    each, and count runs whose answer contains at least one `{{`. A second, equally cheap
    measurement is to run the recorded corpus's 22 cases through a hypothetical
    `direction`-check to count how many plans it would have refused.

---

## 11. What this audit did not do

* Did not choose an architecture.
* Did not implement, refactor, change a prompt, change a schema, change verifier behaviour, add
  retries, or modify a test.
* Did not run a live model. The two case studies are read from artifacts recorded on 2026-08-26;
  the compile-and-verify experiments in §8 use those runs' own committed packages, plans and
  derived facts and touch no provider.
