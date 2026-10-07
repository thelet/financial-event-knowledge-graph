# 02 — Writer and composition changes

*Verified 2026-08-26.*

---

## 1. The contract: 4 leaves → 2

```json
{"title": "…", "sentences": [{"text": "…"}]}
```

`kind` and `rests_on` are gone. `WrittenStory.templates` became `WrittenStory.sentences:
tuple[str, ...]`, and the generation stage no longer constructs a `SentenceTemplate` at all —
building one needs a derived `kind`, the derivation reads the slot rows, and that is
`story/stages/composition/`'s question. `templates_from` became `sentences_from` (two refusals:
`no_sentences`, `draft_not_constructible`), and `thesis_violations` is now public and called by
the composition root **after** recovery.

That last point is not a refactor: running the thesis check on the raw answer is what refused
`story-v1-1daff167348f`, whose every figure was correct. A model writing plain prose names no
handle until recovery has put one there.

## 2. Deterministic slot recovery

`story/stages/composition/recovery.py`. For each sentence carrying no `{{`:

1. **Anchor pass** — find occurrences of `SlotRow.offers[""]` for every non-passage row, matched
   standalone.
2. **Field pass** — for rows anchored *in this sentence only*, find that row's other offered
   strings. Overlaps skipped, longest first.
3. Emit the template; derive `kind` from the rows the result names.

**Ambiguity never guesses.** Where one span could be two rows' value, or one row's value occurs
twice in a sentence, **nothing is bound**, the literal is left alone, and a diagnostic is
recorded in `composition.json`. The numeral then reaches §13.1 as `unbound_numeral` — a refusal
with an exact span, from the stage that is supposed to be the authority.

**Only strings a row already offers are matched**, never the wider `legal_renderings` set. That
makes recovery **text-preserving by construction**, and there is a second reason measured while
building it: `legal_renderings(F1)` includes `"556000000.0 USD"`, so a wider match would bind a
model that copied the FACTS row's raw spelling and publish *"Adjusted Gross Profit of
556000000.0 USD"* — legal, and unreadable.

### The proof

`story-v1-1daff167348f`'s recorded answer, recovered:

```text
in : $556 million Adjusted Gross Profit in the second quarter of 2022.
out: {{F1}} {{F1.metric}} in {{F1.period}}.
in : Adjusted Gross Profit decreased by $446 million from the second quarter of 2022 to the third quarter of 2022.
out: {{D1.metric}} {{D1.direction}} {{D1}} from {{D1.from_period}} to {{D1.to_period}}.

TEXT PRESERVED: True     VERIFIER: passed=True, zero findings at any severity
```

### The adversarial suite — nine of ten refuse correctly

| Case | Outcome |
| --- | --- |
| the true post | **accepted**, 0 findings |
| direction inverted in prose | REFUSE `derived_fact_orientation_reversed` |
| right figure, wrong period | REFUSE `unbound_numeral` + `period_named_in_text_contradicts_binding` |
| invented figure `$999 million` | recovery refuses — no offered value occurs |
| `$556.0 million` (real value, other spelling) | recovery refuses — prose is not rewritten |
| bare year `in 2022` | REFUSE `unbound_numeral` |
| `", the worst on record"` | REFUSE `unsupported_superlative` |
| `"because the market cooled"` | REFUSE `foreign_subject_named` + `causal_construction_forbidden` |
| figures swapped between periods | REFUSE ×2 |
| **`"and it will recover next year"`** | **passed — a pre-existing verifier gap, now fixed (§5)** |

## 3. `kind` derivation

```text
any value slot naming a D row → calculated
else any value slot           → reported
else rests_on present         → explanatory   (unreachable in this phase)
else                          → connective
```

**Strictly stronger than asking the model.** Three wrong labels become unrepresentable, and one
live gap closes: a derived-fact-bearing sentence labelled `reported` verifies clean today,
because `reported_sentence_carries_calculation` fires on `sentence.calculation is not None` and
**the 3.0.0 compiler never builds a `Calculation`** (`grep -rn "Calculation"
story/stages/composition/` returns nothing; 20 of 40 stored drafts carry one, all pre-3.0.0).

## 4. `explanatory` and `rests_on` retired for this phase

Four measurements: **0 of 17** accepted runs ever used an explanatory sentence; `want_explanatory_search`
is `False` on every detector so no package carries an explanatory passage; the `P` rows a draft
could name are the *tables facts were read from*; and the route is refused by §13.7 anyway (§6).

The vocabulary is not deleted. `SentenceKind.EXPLANATORY`, the `P` rows, the compiler's
`rests_on` handling and the four guarding codes all stay, unreachable and still tested.

**The passage rows stay in the slot table**, and the writer omits the `PASSAGES` *section* on its
own grounds — its schema has nowhere to put a passage handle. Building the table without `P`
rows made a package carrying counter-evidence unplannable, which is a different question from
whether a sentence can name one.

Prompt for the demo candidate: **11,179 → 5,246 characters**. The removed section printed **182
numerals, none legal**; the model may write four.

## 5. Code-owned fixes

| Fix | Before | After |
| --- | --- | --- |
| Derived presentation precision | `{{D2}}` rendered `80.215827338%` — `round_delta` reuses `series.DELTA_PRECISION = 9`, a *comparison* constant, as a presentation one. `over_precision` cannot fire on a derived fact and is WARN anyway | `DERIVED_PRESENTATION_DECIMALS` — percent 1, percentage_points 1, multiple 2, homes/markets 0; USD unchanged (`scaled_money` owns it). Rounding at **rendering only**; `DerivedFact.result` keeps full precision. Verifier-safe: §13.1's window is computed from the draft's own precision |
| Forward-looking gap | `"it will recover next year"` passed with **zero findings**; `FORWARD_LOOKING_TERMS` held the literal `"will be"` and no pattern | A bounded `will`/`would`/`shall` + verb pattern. **Extends the existing code** — `verifier_gate_digest()` and `len(GATE) == 102` unchanged |
| Composition diagnostics | the 9 composition codes were in no `code_catalogue` family; a `composition_refused` run rendered each with an empty description and **`blocking: false`**, which is untrue | `FAMILY_COMPOSITION`, read off the module by constant name; the disposition branch added; the AST totality walk extended to `CompositionViolation(...)` |
| Trace | no `compiling_draft` stage | `compiling_draft` and `repairing` added to **both** `STAGES_BY_PHASE` and `STAGE_LABELS` — a stage in one and not the other raises `KeyError` at `model_dump` time, inside the worker |

## 6. The citation disagreement — implemented, reverted, recorded

`compile._unused_handle` gives an `explanatory` sentence's `rests_on` citation the evidence
handle of a fact read from that passage — a table **cell**. §13.7 then refuses the second use of
an identical span by a sentence binding nothing that passage evidences, and an explanatory
sentence binds nothing **by construction**. So the predicate is one that kind of sentence can
never satisfy, and it is order-dependent: `reported`-then-`explanatory` fails, the reverse passes.

**Four repairs were measured. Each is blocked or worse:**

1. a passage-scoped handle earns `unresolvable_evidence_handle` — the verifier resolves handles
   through `package.facts_by_evidence_handle()`;
2. the cell handle with a widened span earns `evidence_cell_span_mismatch`;
3. **refusing in the compiler was implemented and reverted** — it makes the run end at the
   compiler where it used to end at the verifier with a nameable finding, which is precisely the
   *"gates ending runs before authoritative verification"* problem this work exists to reduce,
   and **21 recorded drafts** take that shape;
4. making §13.7 vacuous for explanatory sentences is the weakening the brief forbids.

The expressive fix is `rests_on` on `DraftSentence`, so a paraphrase-rest is visible to §13.7 as
something other than a bound fact's cell. It is additive but re-keys `draft_content_sha256`, and
**no package in the corpus carries an explanatory passage**, so there is no live candidate to
test it against. Deferred with its evidence rather than guessed at. Unreachable in this phase
regardless: `normalize_templates` authors `rests_on` empty.

## 7. A title rule that was unsatisfiable

Rule 12 told the model to write the period *"in the compact form the candidate id uses
(2022Q3)"*. `claims.check_title` licenses a numeral only inside an exact occurrence of a period
key **the package carries** — and half the live `metric_move` candidates are instant-dated, so
their keys are `2025-06-30`, not `2025Q2`.

Measured live against three of them: Qwen followed the rule, wrote *"Opendoor 2025Q2"*, and
earned `unbound_numeral` twice — on `2025` and on `2` — **for a title the rule had asked for**.

The writer prompt now prints a `TITLE PERIODS` section listing the package's own period keys,
the way every other legal string in that prompt is printed. One candidate went from rejected to
accepted on that change alone.
