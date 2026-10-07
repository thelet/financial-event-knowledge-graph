# 00 — Executive summary

*Audit of the story generation pipeline. Analysis only; nothing in `story/` was changed.
Every claim below is marked **(verified)** with how it was checked, or **(unverified)**.
Measurements taken 2026-08-26 against commit `fbac777` and the 58 run directories in
`data/story_demo/`.*

**A note on the output location.** The brief asked for `DOCUMENTATION/…`. The repository's
existing documentation folder is `docs/`, and the instruction was to *use the existing
documentation structure*, so this audit sits at
`docs/2026-08-26-story-pipeline-responsibility-audit/` beside the five other dated folders.

---

## 1. The answer first

**Three of the four problems the brief hypothesises are real, and one is already solved.**

The brief lists five candidate architectural problems. Measured against the code:

| # | Hypothesis | Verdict |
| --- | --- | --- |
| 1 | Planner factual fidelity | **Real, and unguarded.** The detector computes `direction: "decrease"` onto `StoryCandidate.signals` *before* the planner runs. The planner is never shown it and no check compares the plan against it (verified). |
| 2 | Writer thesis fidelity | **Real, but misdiagnosed.** `thesis_abandoned` fired in case 1 because the model emitted **no slots at all**, not because it changed the thesis. The code is behaving correctly; the *code name* points at the wrong repair (verified). |
| 3 | Too much machine bookkeeping on the model | **Largely fixed already, on 2026-08-23.** `WRITER_PROMPT_VERSION` 3.0.0 removed `fact_bindings[].fact_id`, `.rendered`, `.metric_surface`, `.period_surface` and `citations[].evidence_id`. The writer's output schema is now **4 leaf fields**, down from **15** at its 1.1.0 peak; the planner's is still **19** (verified). |
| 4 | Insufficient validation between planner and writer | **Real.** `planner.plan_violations` is 100% referential-integrity: unknown ids, unoffered derivations, missing counterpoints, the pinned `causal_language`. **Nothing compares a claim's words against the numbers it names** (verified, `story/stages/generation/planner.py:298-402`). |
| 5 | Missing repair/retry boundaries | **Real, and deliberate.** `run_demo` is documented as *"One candidate, one pass, no retry"* (`story/pipeline.py:620`). The only retry anywhere is the transport's `max_retries: 2` over five HTTP statuses (verified). |

**And the single most important measurement in this audit:**

> The `metric_move` candidate that both live runs failed on — `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d` — **compiles and verifies with `passed: True` and zero findings of any severity** when the writer emits mechanically correct templates (verified 2026-08-26 by driving `compile_draft` + `DeterministicVerifier` over the run's own recorded package, plan and derived facts; reproduction in `06-ARCHITECTURAL-OPTIONS.md` §7).

Verification is **not** the bottleneck for this candidate. Template compliance is. Both live
3.0.0 runs died at the writer's own structural gate and the authoritative verifier never ran.

---

## 2. The pipeline, and where each authority sits

Composition root: `story/pipeline.py:611` `run_demo`. It is the only module that orders stages.

```text
freshness gate                    deterministic   story/stages/freshness/gate.py
   ↓  (refuses before any model call)
evidence package                  deterministic   story/stages/packaging/
   ↓
offers(package, candidate)        deterministic   story/stages/derivation/offers.py:167
   ↓  ← computed BEFORE the planner; the planner may request only from this list
▶ MODEL CALL 1 — planner          story_editorial_plan, temperature 0.0
   ↓
plan_violations(plan, package,    deterministic   story/stages/generation/planner.py:298
                offered=)                          REFERENTIAL ONLY — see §4 below
   ↓
execute_all(requested)            deterministic   story/stages/derivation/execute.py:154
   ↓  → DerivedFact + EvidenceScopeFact; result, unit, display_semantics all code-owned
slot_table(package, derived,      deterministic   story/stages/composition/slot_table.py:79
           passages)                               → F1..Fn, D1..Dm, P1..Pk
   ↓
▶ MODEL CALL 2 — writer           story_post_draft, temperature 0.0
   ↓
templates_from(...)               deterministic   story/stages/generation/writer.py:498
   ↓  ← GATE. Raises DraftRejected. 15 codes. THE VERIFIER IS DOWNSTREAM OF THIS.
compile_draft(...)                deterministic   story/stages/composition/compile.py:101
   ↓  ← GATE. Raises CompositionRefused. 9 codes.
DeterministicVerifier.verify(...) deterministic   story/stages/verification/deterministic.py
   ↓  ← 102 codes, 12 named checks, 96 REFUSE / 2 WARN / 4 ANNOTATE
accepted | rejected
```

Two model calls. Both at temperature 0.0. There is no model verifier and no third call
(verified — `grep` for `provider.generate` finds exactly the two call sites).

Seven dispositions: `accepted`, `rejected`, `plan_refused`, `derivation_refused`,
`draft_refused`, `composition_refused`, `provider_failed` (`story/pipeline.py:221`).

---

## 3. Where the recent failures originate

### Case 1 — `story-v1-1daff167348f`, Qwen3.5-9B, live, `thesis_abandoned`

The model returned correct, readable prose with **not one slot in it**:

```json
{"text": "$556 million Adjusted Gross Profit in the second quarter of 2022.",
 "kind": "reported", "rests_on": []}
```

`_thesis_violations` resolves handles to fact ids; with no handles, the named set is empty,
the plan's set is not, and the refusal is `thesis_abandoned: … the templates name no fact at
all` (verified from `rejected.json`).

The prose is *true*. The plan is *correct* — the Qwen plan says "fell from 556 million USD to
110 million USD". The model simply ignored the slot grammar.

### Case 2 — `story-v1-76da8465cd95`, gpt-5-nano, live, 6 × `rests_on_*`

The model used slots correctly and put **fact and derived handles into `rests_on`**:

```json
{"text": "{{F2}} {{F2.metric}} in {{F2.period}} remains higher …", "kind": "reported",
 "rests_on": ["F2"]}
```

`P1` and `P2` *were* in that run's slot table (verified by rebuilding it). The model was shown
passage handles and used fact handles anyway.

**And the plan it was writing from is factually inverted**: thesis *"adjusted gross profit
**rose** from 2022Q2 to 2022Q3"*, from 556 → 110.

---

## 4. Classification — the brief's five labels, applied

| Observation | Class | Evidence |
| --- | --- | --- |
| Case 1: model wrote no slots | **MODEL FAILURE** + **PROMPT/CONTRACT FAILURE** | Qwen ignored 14 rules and a worked example. Recorded 2026-08-23: the same model flips between compliant and non-compliant on prompt *length* near rule 6, at temperature 0 |
| Case 1: refusal is named `thesis_abandoned` | **BUG (diagnosis quality, not behaviour)** | The code is right to refuse; the name sends a repair at the thesis when the fault is "no slot grammar used". Nothing is functionally wrong |
| Case 2: fact handles in `rests_on` | **MODEL FAILURE** | Rule 6 packs *"you write no citations"*, *"explanatory only"*, *"passage handles only"* and *"`{{P2}}` is not a slot"* into one paragraph |
| Case 2: planner said "rose" for 556 → 110 | **ARCHITECTURAL RESPONSIBILITY PROBLEM** | `candidate.signals.direction == "decrease"` exists before the planner call, is not printed to it, and is not checked after it |
| `verifier ran: no` on both | **ARCHITECTURAL RESPONSIBILITY PROBLEM** | By design (`pipeline.py:735-760`), but the design puts a 15-code model-contract gate *upstream* of the authoritative 102-code verifier |
| `unbound_numeral` on the literal `2022` | **EXPECTED SAFETY REJECTION** | The model typed a period into prose instead of using a period slot. Refusing it is correct |
| `citation_reused_for_unrelated_claim` on an `explanatory` sentence | **ARCHITECTURAL RESPONSIBILITY PROBLEM** | The *compiler* chooses that citation (`compile.py:_unused_handle`), then §13.7 refuses it. Two code-owned stages disagreeing — no model involved. 15 of 85 recorded findings |
| The nine composition codes are absent from the demo UI catalogue | **BUG** | `code_catalogue.declared_codes` has 5 families and none holds them; a `composition_refused` run renders each code with an empty description and `blocking: false`, which is false |
| `{{D2}}` renders `80.215827338%` | **BUG (cosmetic, code-owned)** | `renderings.derived_figure` falls through to `"{value}%"` on the raw float. Caught only as `over_precision`, which is **WARN**, non-blocking |
| `deterministic.py:1307` names `period_surface_hint` as the compiler's source | **stale comment, not a defect** | The compiler reads `SlotRow.offers["to_period"]`; a test asserts the two agree (`test_story_composition.py:351`) |

**Nothing here is a correctness bug in verification.** The verifier did what it was built to do
in every case where it ran.

---

## 5. What is structurally fragile

1. **The writer's structural gate sits above the authoritative verifier.** 15 codes in
   `writer.py` and 9 in `composition/public.py` can end a run before §13 sees anything. Every
   one of those 24 is a statement about the *model's answer*, not about the *claim* — so a
   perfectly true post can be discarded without any factual check ever running, and an untrue
   one never gets its factual refusal recorded.

2. **The slot grammar is the only interface, and compliance is unmeasured.** Two providers, two
   different non-compliance modes, on the same package, on the same day. There is no test, no
   metric and no artifact that records "did this model use slots at all". The refusal codes
   conflate it with other faults.

3. **The planner can contradict arithmetic and nothing notices.** See §4 and
   `03-FAILURE-AND-GATE-MAP.md` §5.

4. **`citation_reused_for_unrelated_claim` is now reachable purely from code.** An `explanatory`
   sentence resting on a passage already cited by an earlier fact binding gets the same span
   again and binds no fact — the exact predicate §13.7 refuses. The compiler's own docstring
   hands the question to the verifier, and the verifier says no (verified by running case 2 with
   only `rests_on` mechanically repaired).

5. **The composition stage is invisible to the demo UI.** No trace stage, no disposition
   handling, no code family. A `composition_refused` run is diagnosable only from `rejected.json`.

---

## 6. Which issues are model quality, and which are the boundary

**Model quality (the model has everything it needs and gets it wrong):**

* Ignoring the slot grammar entirely (case 1).
* Putting fact handles in `rests_on` (case 2).
* Emitting the schema's own field names as `structure: ["thesis", "why_it_matters", …]`
  (case 2's plan — verified).
* Writing "higher" on a comparison the derived row says runs the other way.

**Model/code boundary (the program knows the answer and asks the model anyway, or asks it
nothing and then judges it):**

* **Direction.** `signals.direction`, `signals.delta`, `signals.delta_pct` are computed by the
  detector. The planner is shown neither, and its thesis is never compared to them.
* **Period order.** `story/core/periods.py:months_between` exists; the planner is shown
  `2022Q2` and `2022Q3` as bare strings and must infer which is later.
* **Citation choice for an explanatory sentence.** Code picks it; code then refuses it.
* **Sentence `kind`.** The model declares it, and `reported_vs_calculated` refuses the mismatch.
  The slot table already knows whether a sentence names an `F` row or a `D` row.
* **Which derivations to request.** The planner copies triples from a list code generated, and
  code checks the copy by string equality. The only genuinely editorial part is *how many*.

**Still genuinely the model's, and correctly so:** the thesis, what is interesting, which facts
to emphasise, the counterpoint, the uncertainty, the wording, the sentence order, and — the one
irreducible one — **which side of a comparing word each figure goes on** (rule 10 says so in as
many words, and `comparative_not_supported_by_text` is the check that guards it).

---

## 7. The corrections this audit forced

Per the repository's standing rule that corrections are worth more than a clean narrative:

1. **"Too much bookkeeping is on the model" is no longer the primary problem.** It was, until
   2026-08-23. The writer schema is 4 leaves. Continuing to attack it will not fix these runs.
2. **The nine composition codes are *not* in `demo_ui/code_catalogue.py`.** A prior session
   recorded that they were. They are not (verified: `declared_codes` covers 5 families,
   102 + 30 + 8 + 12 + 15 codes, none of them `unknown_slot_handle` et al.).
3. **`thesis_abandoned` did not mean the thesis was abandoned.** In case 1 the plan and the
   prose agree completely.
4. **The writer's gate, not the verifier, is what these runs hit.** Any argument about
   "the verifier is too strict" is unsupported by case 1 and case 2: it never ran.

---

## 8. Reading order

| File | Answers |
| --- | --- |
| `01-PIPELINE-CONTRACTS.md` | The stage table and the field-by-field inventory of every model-facing schema |
| `02-MODEL-VS-DETERMINISTIC-RESPONSIBILITIES.md` | A–E classification, current owner, and the writer/planner complexity measurements |
| `03-FAILURE-AND-GATE-MAP.md` | Every code by stage, gate ordering, why `verifier ran: no`, the planner validation boundary |
| `04-REPAIR-LOOP-FEASIBILITY.md` | Existing retries, the five-way error taxonomy, whether the feedback structure already exists, repair granularity |
| `05-RESEARCH-AGENT-FEASIBILITY.md` | The nine tools that already exist, the graph access boundary, the minimum research brief |
| `06-ARCHITECTURAL-OPTIONS.md` | Options A–E, the comparison matrix, the two case studies, test coverage, and the 20 questions answered |
