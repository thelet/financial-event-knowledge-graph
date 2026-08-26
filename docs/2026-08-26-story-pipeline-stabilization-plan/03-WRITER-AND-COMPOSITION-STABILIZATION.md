# 03 — Writer and composition stabilization

*The largest part of the plan. Verified 2026-08-26 by running the proposed recovery against the
failed runs' own committed artifacts.*

---

## 1. The measurement that decides this section

For `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d` (verified):

| | |
| --- | --- |
| Writer prompt | 11,179 chars / 120 lines |
| **`PASSAGES` section** | **5,924 chars / 58 lines — 53% of the prompt** |
| Numerals printed inside it | **182** (119 distinct) |
| Of those, legal for the model to write | **0** |
| Figures the model may write, in total | 4 |
| `WRITER_SYSTEM` characters serving only the passage route | 1,122 of 5,939 (19%), across 5 of 14 rules |

The `PASSAGES` section prints two whole non-GAAP reconciliation tables — `486`, `159`, `1,021`,
`11.6`, `13.4`, `82`, `(12)`, `556`, `160`, `1,068`, `13.2`, `(100)`, `422`, `8,520` … — and
then forbids all but four of them.

And it exists for one purpose: so an `explanatory` sentence can name a passage in `rests_on`.

---

## 2. `explanatory` and `rests_on` have never worked, and structurally cannot

Four independent measurements (all verified):

1. **0 of 17 accepted runs used an `explanatory` sentence.** Across the 40 stored drafts:
   `reported` 84, `calculated` 37, `explanatory` 9, `connective` 3. The 17 accepted runs used
   only `reported` (34) and `calculated` (17). Not one explanatory or connective sentence has
   ever appeared in an accepted post.
2. **No package in the corpus carries an explanatory passage.** `EvidenceRequest.want_explanatory_search`
   defaults to `False` (`models.py:427`) and **every** detector leaves it off —
   `metric_move.py:502`, `acceleration.py:378`, `cross_metric_divergence.py:769`.
   `evidence_package.py:245` only searches when it is `True`. So `package.explanatory_passages`
   is empty in every run that exists.
3. **The `P` rows are tables, not explanations.** `passages_backing_facts` returns the passages
   *facts were read from*. For this candidate both are non-GAAP reconciliation tables. An
   explanatory claim resting on one is resting on a number grid — which is exactly what
   gpt-5-nano did: *"is defined as GAAP gross profit plus an inventory valuation adjustment"*,
   resting on a table that says no such thing.
4. **The route is refused by the verifier anyway, order-dependently.** See §7.

**Conclusion: for the `metric_move` slice, drop `explanatory` and `rests_on`, and drop the
`PASSAGES` section with them.** This is not removing a capability; it is removing a route that
cannot currently succeed, and whose only measurable effect is 182 forbidden numerals in front of
a model told to write four.

The `SentenceKind.EXPLANATORY` member, the compiler's `rests_on` handling, the `P` rows and the
four `rests_on` codes all **stay in the code**, unreachable from this contract, exactly as the
eight pre-3.0.0 writer codes stay for replayed artifacts. When `want_explanatory_search` is
turned on for a story type that needs it, the route returns — with §7's fix applied first.

---

## 3. Deterministic slot recovery — the proposal, and the proof it works

### What it is

A pure normalization pass between the model's answer and the writer's structural gate. For each
sentence that carries **no** `{{`:

1. **Anchor pass.** Find every occurrence of a string some slot row offers as its *value*
   (`SlotRow.offers[""]`). A span claimed by two different rows, or one row's value occurring
   twice in one sentence, **refuses**. A sentence with no anchor refuses.
2. **Field pass.** For rows anchored *in this sentence only*, find occurrences of the strings
   that row offers for `metric`, `period`, `direction`, `from_period`, `to_period`. Overlaps with
   an anchor are skipped. Where two anchored rows offer the identical string for the identical
   span, either is taken — a field slot mints **no binding** (`compile.py:_bindings_from` only
   iterates value slots), so the compiled draft is byte-identical either way.
3. Emit the template.

### The proof

Run against `story-v1-1daff167348f`'s own recorded writer answer — the run refused
`thesis_abandoned` — using its own committed package, plan and derived facts (verified):

```text
in : $556 million Adjusted Gross Profit in the second quarter of 2022.
out: {{F1}} {{F1.metric}} in {{F1.period}}.

in : $110 million Adjusted Gross Profit in the third quarter of 2022.
out: {{F2}} {{F2.metric}} in {{F2.period}}.

in : Adjusted Gross Profit decreased by $446 million from the second quarter of 2022 to the third quarter of 2022.
out: {{D1.metric}} {{D1.direction}} {{D1}} from {{D1.from_period}} to {{D1.to_period}}.

TEXT PRESERVED   sentence 0 IDENTICAL · sentence 1 IDENTICAL · sentence 2 IDENTICAL
VERIFIER         passed: True — zero findings at any severity
```

**A `draft_refused` run becomes an accepted post**, with no prompt change, no model change, no
rule relaxed, and the model's prose reaching the reader unaltered.

### Why it is safe — the adversarial suite

Ten cases, all run through recover → compile → verify (verified):

| Case | Outcome |
| --- | --- |
| A. the true post | **accepted**, 0 findings |
| B. `"increased by $446 million"` — direction inverted | **REFUSE `derived_fact_orientation_reversed`** (`observed: the sentence says 'increased'`) |
| C. `$110 million … in the second quarter of 2022` — right figure, wrong period | **REFUSE** `unbound_numeral` + `period_named_in_text_contradicts_binding` |
| D. `$999 million` — invented figure | **recovery refuses**: no offered value string occurs |
| E. `$556.0 million` — real value, different spelling | **recovery refuses** (see the trade-off below) |
| F. `"in 2022"` — bare year | recovered elsewhere; **REFUSE `unbound_numeral` on `2022`** |
| G. `", the worst on record"` | **REFUSE `unsupported_superlative`** |
| H. `"because the market cooled"` | **REFUSE** `foreign_subject_named` + `causal_construction_forbidden` |
| I. `"and it will recover next year"` | **PASSES** — a pre-existing verifier gap, see §8 |
| J. figures swapped between periods | **REFUSE** `unbound_numeral` + `period_named_in_text_contradicts_binding` |

Nine of ten behave correctly. Case I is a hole in `FORWARD_LOOKING_TERMS` that is **independent
of recovery** — the identical sentence written as a hand-authored slot template also passes
(verified). It is in scope as a *strengthening* fix (§8).

### The one real trade-off, stated

**Case E.** Recovery matches only strings a row *already offers*, so `$556.0 million` — which
denotes the same value and which `legal_renderings` would accept — is refused rather than
recovered. The alternative is to match the wider `legal_renderings` set, which would recover it
and then **rewrite the model's prose** to `$556 million` on compile.

**Recommendation: keep the narrow rule.** `_template_from`'s existing contract is that *"the text
is copied through untouched — no stripping, no normalising, no brace repair. Every character
outside a slot is the model's own prose and reaches the reader."* A recovery that silently
edits prose breaks a property the codebase is explicit about. The cost is a refusal on a
near-miss spelling, which is a *repairable* structural failure with exact feedback (`04` §4)
— and the writer prompt already prints the exact string to use under every row.

If the live measurement in `06` shows near-miss spellings are the dominant residual failure,
widening to `legal_renderings` becomes a decision with data behind it. It should not be made
without.

### Where it lives

`story/stages/composition/recovery.py` — beside `slot_table` and `compile`, since it consumes
`SlotRow`. `story/stages/generation/` may not import it, so `run_demo` calls it and passes the
result, exactly as it already does for the slot table (`pipeline.py:718-727`).

The machinery it reuses, all existing: `SlotRow.offers`, `slot_table.VALUE_FIELD`,
`SLOT_PATTERN`, and `evidence_slice.occurrences` — a substring-occurrence helper already written
and tested for exactly this shape of search.

---

## 4. Deriving `sentences[].kind`

### The rule

```text
any value slot naming a D row  → calculated
else any value slot            → reported
else rests_on present          → explanatory      (unreachable in this slice)
else                           → connective
```

### Edge cases, measured against the verifier

Every combination compiled and verified (verified):

| Draft | Verdict today | Under the derived rule |
| --- | --- | --- |
| F-only, marked `reported` | passes | same — `reported` |
| F-only, marked `calculated` | **REFUSE** ×2 (`calculated_sentence_without_calculation`, `calculated_sentence_cites_passage`) | **unrepresentable** |
| F-only, marked `connective` | **REFUSE** `connective_sentence_carries_a_claim` | **unrepresentable** |
| D-bearing, marked `calculated` | passes | same — `calculated` |
| **D-bearing, marked `reported`** | **passes** — a gap | **`calculated`** — the gap closes |
| no slot, marked `connective` | passes | same |
| no slot, marked `reported` | passes | becomes `connective`; a claim-carrying one then earns `connective_sentence_carries_a_claim` |
| no slot, marked `calculated` | **REFUSE** `calculated_sentence_without_calculation` | **unrepresentable** |

**Deriving `kind` is strictly stronger than the status quo.** It makes three wrong labels
unrepresentable and closes one live gap: a sentence binding a derived fact and labelled
`reported` verifies clean today, because `reported_sentence_carries_calculation` fires on
`sentence.calculation is not None` and **the 3.0.0 compiler never builds a `Calculation`**
(verified — `grep -rn "Calculation" story/stages/composition/` returns nothing; 20 of 40 stored
drafts carry one, all pre-3.0.0).

### The one case that needs a decision

A sentence naming **both** an `F` value slot and a `D` value slot classifies as `calculated`.
Such a sentence is separately fragile — the `F` row's period and the `D` binding's `to_period`
can disagree in one sentence and earn `period_named_in_text_contradicts_binding` (observed while
testing). The recommendation is to derive `calculated` and let §13 judge the rest, **not** to
refuse the shape at compile time: withholding a compile to force a §13 refusal is the compiler
acting as a verifier, which `_citations_for`'s own docstring names as forbidden.

---

## 5. Why not replace slots with a structured writer interface

The brief's Option C: the writer emits a sequence of typed statements (`fact statement`,
`derived statement`, `connective prose`) and code renders the factual surfaces.

| | Slot DSL + recovery | Structured statements |
| --- | --- | --- |
| Reliability | proven on the failed run; slots become optional | high — no grammar to get wrong |
| Prose freedom | **full** — everything outside a slot is the model's own words, copied untouched | **reduced** — code owns sentence shape, so posts converge on a template |
| Verifier compatibility | **zero change** — produces the same `Draft` | needs a renderer producing the same `Draft`; more new code |
| Compiler reuse | complete | partial |
| Cost | one pure module (~150 lines) | a renderer, a template vocabulary per story type, and a new failure surface |
| Risk it becomes a template generator | none | **high**, and irreversible in practice |

**Recommendation: recovery now, and the structured interface is not the cleaner long-term
boundary — it is a different product.** The thing that makes this pipeline worth building is
that a person wrote the sentence and code proved it. A structured interface moves the sentence
to code. The correct long-term boundary is the current one with the grammar made optional, which
is what recovery does.

---

## 6. The narrowed writer contract

`WRITER_PROMPT_VERSION` 4.0.0:

```json
{"type": "object", "additionalProperties": false,
 "required": ["title", "sentences"],
 "properties": {
   "title": {"type": "string"},
   "sentences": {"type": "array", "items": {
     "type": "object", "additionalProperties": false,
     "required": ["text"],
     "properties": {"text": {"type": "string"}}}}}}
```

Two leaves. Rules 6 (`rests_on`), 7 (`kind`) and most of 14 leave the system prompt; the
`PASSAGES` section leaves the user prompt. The worked example stays — it was measured on
2026-08-23 to be what flipped Qwen into emitting slots at all, and under recovery it becomes a
*preference* rather than a requirement.

The slot rules stay, restated as an offer rather than an obligation: *"Write `{{F1}}` where the
figure goes, or write the figure exactly as the row prints it — code will bind either."*

---

## 7. Code-owned fix 1 — `citation_reused_for_unrelated_claim`

### Traced

Four drafts compiled and verified (verified):

| Draft | Verdict |
| --- | --- |
| `reported`(F1) then `explanatory`(rests_on P1) | **REFUSE `citation_reused_for_unrelated_claim`** |
| `reported`(F1) then `explanatory`(rests_on P2) | passes |
| **`explanatory`(rests_on P1) then `reported`(F1)** | **passes** |
| `reported`(F1) then `reported`(F2) | passes |

**The same two sentences, reordered, verify differently.** That is not a property of the claim.

### Root cause

`compile.py:_unused_handle` gives an `explanatory` sentence's `rests_on` citation the **evidence
handle of a fact read from that passage** — i.e. a table-cell span, `[917:920]`, the three
characters holding `556`. §13.7's `_reuse_findings` then refuses the second use of an identical
`(passage_id, char_start, char_end)` by a sentence that binds no fact — and an `explanatory`
sentence binds none by construction.

So the predicate *"binds nothing that passage evidences"* is one an explanatory sentence can
never satisfy. The compiler's own docstring hands the question to the verifier
(*"whether **this** claim may rest on **that** span is [the verifier's]"*), and the verifier says
no.

### The fix, in two layers

**Immediate (this phase): the route is unreachable.** With `explanatory` and `rests_on` dropped
for `metric_move`, no compiler-minted citation is ever reused — `_citations_for` dedupes within a
sentence and reads no earlier sentence's citations, so the *"same span, second claim"* shape is
not constructible. This accounts for 15 of the 85 recorded findings, the second-largest code in
the corpus.

**Correct (when explanatory returns): cite the passage, not a cell.** A claim paraphrasing what a
passage *says* should carry a span over the passage's own prose, with no cell handle. The
blocker is that `PassageCitation.evidence_handle` is a **required** field (verified), so today
there is no way to express *"this citation is a paraphrase-rest, not a cell reading"*.

Two candidate resolutions, both recorded, neither adopted now:

* Make `evidence_handle` optional and have §13.7 skip the reuse predicate for a citation without
  one. Touches a stored-artifact type — needs a migration story.
* Give the passage-rest its own citation kind in the existing discriminated union
  (`PassageCitation | EvidenceSourceCitation`, already keyed on `kind`). Additive, and the union
  already exists.

**Do not adopt either in this phase.** The route is dead for `metric_move`, and choosing between
them without a live explanatory candidate to test against would be designing from imagination.

---

## 8. Code-owned fix 2 — derived percentage rendering

`{{D2}}` renders **`80.215827338%`** (verified).

### Why

`renderings.derived_figure` special-cases `USD` (via `scaled_money`, which divides in `Decimal`
and keeps the result only when it multiplies back exactly, at `SCALED_MONEY_DECIMALS = 1`) and
for every other unit applies `DERIVED_FIGURE_FORMATS[unit].format(value=magnitude)` to the raw
float. Its own docstring: *"Everything else is printed as Python renders the float."*

The float has nine decimals because `operations.round_delta` uses
**`series.DELTA_PRECISION = 9`** — a *comparison* constant (it exists so `13.2 − 3.3` does not
publish `9.899999999999999`), reused as a *presentation* precision.

### Why nothing catches it

`over_precision` is the only candidate and it **cannot fire on a derived fact**: it compares the
draft's significant figures against the fact's own `quoted_text`, and a derived fact has no
printed form because nobody printed it (`deterministic.py:690-695`, explicit). It is also
**WARN**, non-blocking.

### The fix is verifier-safe, measured

§13.1's tolerance window is computed from the **draft's own precision**, so a rounded figure is
inside it (verified with `compare_token_to_fact` against `80.215827338`):

| Written | d | window | within? |
| --- | --- | --- | --- |
| `80.215827338%` | 11 s.f. | 5e-10 | yes |
| `80.22%` | 4 s.f. | 0.005 | yes |
| **`80.2%`** | 3 s.f. | 0.05 | **yes** |
| `80%` | 1 s.f. | 5.0 | yes |
| `-80.2%` | — | — | **sign disagrees** — correctly refused |

### Proposed policy

A `DERIVED_PRESENTATION_DECIMALS` table in `renderings.py`, beside the existing
`SCALED_MONEY_DECIMALS` and defended by the same kind of argument:

| Unit | Decimals | Rationale |
| --- | --- | --- |
| `percent` | 1 | matches how the corpus prints margins (`13.2 %`, `(12.6) %`) |
| `percentage_points` | 1 | already effectively 1 (`15.9`); the table makes it a rule instead of luck |
| `multiple` | 2 | `1.35x` |
| `homes`, `markets` | 0 | counts are integers |
| `USD` | unchanged | `scaled_money` already owns it, with an exactness guarantee |

Rounding happens **only at rendering**. `DerivedFact.result` keeps its nine decimals, so the
verifier still recomputes against the exact value and the ledger still records it.

---

## 9. Code-owned fix 3 — the forward-looking gap

`"it will recover next year"` passes with **zero findings** (verified, both with recovery and
with a hand-authored slot template — so it is unrelated to anything else in this plan).

`FORWARD_LOOKING_TERMS` holds `"will be"` and 14 other literals; `"will recover"` is not among
them and there is no general pattern. Measured probes:

```text
forward_looking('it will recover next year')        -> []          ← the hole
forward_looking('it will be higher next year')      -> ['will be']
forward_looking('we expect a recovery')             -> ['expect']
forward_looking('guidance for the fourth quarter')  -> ['guidance']
forward_looking('the company is on track to recover')-> ['on track to']
```

**This is a strengthening fix, so it is in scope** — the constraint is not to weaken
verification. The narrow version is to add a bounded auxiliary pattern (`will`/`would`/`should`
followed by a verb) rather than more literals, since a literal list will always have a next hole.
It must be added to `language.py` with the same care the rest of the module shows: the Cypher
read-only scan case-folds every string constant in the package, and `_compile`'s
`(?<![\w-])…(?![\w-])` boundary convention must be preserved.

**Scope note:** this is the only *verifier* change in the plan, it only ever adds refusals, and
it moves `verifier_gate_digest()` **only if a new code is added** — the recommendation is to
extend the existing `forward_looking_language` code rather than add one, so the gate table and
its digest are untouched.

---

## 10. Code-owned fix 4 — composition observability

Correctness-critical fixes are §§7–9. **This one is observability only** and is separated
deliberately.

| Gap | Effect | Fix |
| --- | --- | --- |
| The 9 composition codes are in no `code_catalogue` family | A `composition_refused` run renders every code with an empty description and **`blocking: false`**, which is untrue | Add `FAMILY_COMPOSITION`, read `declared_codes` off `story/stages/composition/public.py` by constant name, as the planner and writer families already do |
| `api.py:2002-2007` selects the family by disposition and falls through to `FAMILY_VERIFICATION` | wrong family for `composition_refused` | Add the branch |
| No `compiling_draft` stage in `trace.py:STAGES_BY_PHASE` | a compile-time refusal is invisible on the timeline | Add the stage **and** its `STAGE_LABELS` entry — a stage present in one and absent from the other constructs fine and raises `KeyError` at `model_dump` time, inside the generation worker |
| The catalogue totality test walks only `PlanViolation(...)` and `DraftViolation(...)` | the gap is not caught | Extend the AST walk to `CompositionViolation(...)` |

Stabilization changes composition behaviour (recovery, derived `kind`), so these must land in the
same phase as the behaviour, not after it.
