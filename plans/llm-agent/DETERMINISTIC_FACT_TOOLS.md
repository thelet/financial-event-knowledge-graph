# S13 — Deterministic fact tools: code computes, the model only words it

**Status:** plan, committed as a checkpoint before implementation. *(2026-08-19)*

## 1. The answer, first

Add a narrow, code-owned derivation stage between the planner and the writer. The planner
**requests** derivations by naming an operation and package fact ids; code **validates and
executes** them; the results become `DerivedFact`s that the writer binds exactly as it binds an
observed fact. The writer stops declaring arithmetic entirely — `Calculation` leaves the writer
schema.

The target property, stated so it can be tested: **every numeral in an accepted post is either a
packaged observation or a derived fact code computed. No numeral is ever the model's own
arithmetic.**

## 2. A correction to the premise, measured before designing *(2026-08-19)*

The brief describes *"`$446M` correctly derived from `$556M → $110M` but rejected as
`unbound_numeral`"*. **That is not what happened**, and the difference changes the design.

Four recorded runs of `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`
exist under `data/story_demo/`. In the two that reached the verifier
(`story-v1-2405d9b03c5e`, `story-v1-d77f67988132`), the report says:

| what | evidence |
| --- | --- |
| `$446 million` was **covered**, by `Calculation.result_rendered` | `deterministic.py:575-598`'s third covering mechanism |
| the arithmetic **recomputed cleanly** | `calculation_ledger[0].recomputed_value = 446000000.0` |
| `unbound_numeral` fired on the literal **`2022`** | `verification_report.json` → `checks[].findings[]`, REFUSE / `REBIND_TO_FACT` |
| because the model left `Calculation.period_surface` **empty** | while its text read *"in the third quarter of 2022"* |

So the failure was never the calculation. A `calculated` sentence carries no `fact_bindings`, so
the *only* place it may declare a period is `Calculation.period_surface` — one optional-looking
string the model has to remember to fill. `prompts.py:650-656` records this same defect being
half-repaired once already: the first wording told the writer *not to name the period*, which
that note itself calls *"the wrong end to fix it from"*.

**This is why the fix is structural rather than another prompt rule.** A derived fact bound as an
ordinary `FactBinding` carries `period_surface` **per binding**, and code fills it from the
derivation's own `from_period`/`to_period`. The model cannot forget a field it no longer writes.

Two other measurements shape the work:

* **The candidate is unreachable from `python -m story demo`.** `resolve_demo_inputs`
  (`pipeline.py:381-409`) runs only `detect_cross_metric_divergence`, so a `metric_move` id raises
  `CandidateNotFound`. Only `demo_ui/candidate_resolution.py:rederive_candidates` runs all four
  detectors. §9's end-to-end run needs that widened first.
* **The detector already holds the answer**: the candidate's `signals` carry
  `delta = -446000000.0`, `delta_pct = -80.215827338`, `direction = "decrease"`,
  `crosses_zero = false` — but **not** the two endpoint values, which come from the package's
  facts. So "reuse the detector's result" is possible for the *result* and not for the *inputs*.

## 3. Where derived facts may live — the constraint that decides the architecture

**They may not go in `StoryEvidencePackage.facts`.** Three reasons, all structural:

1. `package_content_digest` is a `story_run_id` input. A package whose contents depended on a
   model call would make the run id depend on the model's output.
2. §2's line is *"Nothing a model produced may enter one"*, and the planner **selects** the
   derivations.
3. `PackagedFact` requires `passage_id` or `evidence_source_id` (`models.py:865-871`) — a derived
   fact has neither.

So `DerivedFact` is a **separate type in a separate artifact**, `derived_facts.json`, produced by
a new stage and carried alongside the package into the writer and the verifier.

`package_view.FACT_GROUPS` already declares a `derived_facts` group reading `package.facts`
filtered on `FactKind.DERIVED` (`package_view.py:320`). That group is repointed at the new
artifact; the `FactKind.DERIVED` member stays where it is, unused, with its comment corrected.

## 4. The stage

```
story/stages/derivation/
    public.py         DerivationRequest, DerivedFact, DerivationRefused, the operation enum
    offers.py         what this candidate and package can support — the tool availability set
    operations.py     the seven operations, each a pure function over validated inputs
    execute.py        request -> validate -> execute -> DerivedFact, or a typed refusal
```

`story/stages/derivation/` imports `story.core` and nothing else — no stage imports another
(`test_story_package_structure.py`).

### 4.1 The operations

Seven, and each is implemented **only** because an existing verification rule can already check
its result:

| operation | inputs | result unit | existing rule it rests on |
| --- | --- | --- | --- |
| `absolute_change` | 2, same metric, adjacent periods | input unit | `difference` in `OPERATION_INPUTS` |
| `percentage_change` | 2, same metric, non-percent unit | `percent` | `delta_relative`, and it **refuses across zero** (`numerals.RelativeChangeAcrossZero`) |
| `percentage_point_change` | 2, same metric, `percent` unit | `percentage_points` | `delta_pp`, `operation_result_surfaces` |
| `compare_levels` | 2, comparable, same period | input unit | `compare_levels`, §13.14's direction check |
| `ratio` | 2, same unit, denominator non-zero | `multiple` | `ratio` |
| `crossed_zero` | 2, same metric | boolean | the candidate's own `crosses_zero` signal |
| `trend_direction` | 2, same metric | direction word | `detector_config.quantity_direction` |

`percentage_change` and `percentage_point_change` are **separate operations, never one with a
flag**. The unit decides which is legal and the other is refused — that is the percent-versus-
percentage-point confusion made unrepresentable rather than checked.

Arithmetic is **not reimplemented**. Every operation delegates to what already exists:
`numerals.delta_pp`, `numerals.delta_relative`, `numerals.relative_change_across_zero`,
`detector_config.round_delta`, `detector_config.quantity_direction`, `series.comparable`.

### 4.2 Validation, before any arithmetic

Every request is refused unless all of these hold. A refusal is typed and recorded; it never
falls through to a computed value.

* both fact ids resolve **in the package** (never a free-text value, never an id from elsewhere);
* `series.comparable(left, right, claim=…)` returns `Ok` — R1–R10, the existing rules;
* units agree and admit the operation (§4.1's table);
* subject agrees;
* period shapes agree, and for the two-period operations the periods are **adjacent** (R10);
* a formula-versioned metric has one formula version across both inputs (R6);
* the operation is in the **offer set** for this candidate (§4.3).

### 4.3 Tool availability — an offer set, not a calculator

Before the planner runs, code computes `offers(package, candidate)`: every `(operation, left,
right)` triple that would pass §4.2. It is a pure function of the package and the candidate, with
no model input, so it is deterministic and may be printed into the planner prompt.

The planner may request **only** a triple from that list. A request outside it is
`derivation_not_offered` — refused, not attempted. There is no generic calculator, no expression
string, no free-text formula anywhere in the schema.

Offers are capped (`max_derivations`, a `BudgetParameters` field, so it reaches both digests).

### 4.4 The derived fact

```json
{
  "fact_id": "fact:derived:absolute-change:opendoor:adjusted-gross-profit:2022Q2-2022Q3:<digest12>",
  "fact_kind": "derived",
  "operation": "absolute_change",
  "from_fact_id": "obs:adjusted-gross-profit:opendoor:2022Q2:normalized-table:0c4364ebbc44",
  "to_fact_id":   "obs:adjusted-gross-profit:opendoor:2022Q3:normalized-table:4d66ef7200e9",
  "from_period": "2022Q2", "to_period": "2022Q3",
  "from_value": 556000000.0, "to_value": 110000000.0,
  "result": -446000000.0,
  "unit": "USD", "currency": "USD",
  "display_semantics": "decreased by",
  "metric_id": "adjusted_gross_profit",
  "metric_surfaces": ["Adjusted Gross Profit"],
  "period_surface_hint": "the third quarter of 2022",
  "comparability_rule_ids": ["R1", "R2", "R3", "R4", "R5", "R7", "R10"],
  "source": "derivation_tool", "tool_version": "1.0.0",
  "reused_detector_signal": "delta"
}
```

**The id carries a digest, departing from the brief's example, and the reason is the repository's
own rule**: readable segments never carry uniqueness, the digest does
(`story/core/keys.py:14-22`). Segments are operation, subject, metric slug and the period pair;
`digest12` covers `(operation, sorted input fact ids, package_id, tool_version)`. Without it two
packages over one metric-and-period pair would mint one id for two different computations.

`reused_detector_signal` is §7's requirement: when the candidate's own `signals` already hold the
exact quantity, the tool **asserts equality against it** and records that it did. A disagreement
is a refusal, not a silent preference — two code paths computing one number and differing is a
defect in one of them.

## 5. Model interaction

```
package + candidate -> offers()  ->  planner (requested_derivations[])
                                       -> validate + execute  ->  DerivedFact[]
                                          -> writer (package + derived facts)
                                             -> deterministic verifier
```

The planner schema gains `requested_derivations[]`, each entry `{operation, from_fact_id,
to_fact_id}` with `operation` a closed `enum` — inside §15.3's portable subset, so both providers
constrain it identically. **No native tool-calling, on either provider**: a structured field
followed by deterministic execution replays exactly, and `request_identity` already digests the
schema. This is the brief's stated preference and it is also the only shape that keeps Qwen and
OpenAI on one contract.

The writer receives the derived facts in a new `DERIVED FACTS` prompt section, printed in the same
shape as `FACTS`, and binds them with an ordinary `FactBinding`. **`calculation` is removed from
the writer schema**, and `WRITER_OPERATIONS` with it.

## 6. Verifier — nothing weakened, four things extended

`PackageIndex` learns a second map, `derived`, and `fact(fact_id)` resolves either. Then:

| rule | change |
| --- | --- |
| §13.1 `unbound_numeral` | unchanged. A derived fact binds like any other, so its numeral is covered by the binding span — the mechanism that already exists. |
| §13.1 `number_outside_tolerance` | the bound value is compared against `DerivedFact.result` through the same `compare_token_to_fact`. |
| §13.2 units | `DerivedFact.unit` may be `percentage_points` or `multiple`, which no observation may be. `CHANGE_SURFACES` stops being a blanket refusal and becomes a refusal *against a level*. |
| §13.4 periods | a binding to a derived fact must resolve a period surface agreeing with **`to_period`**, and an orientation word must agree with `from_period → to_period`. |
| §13.9 | `calculated_sentence_without_calculation` and `calculated_sentence_cites_passage` are **replaced**: a `calculated` sentence now carries derived-fact bindings and cites its inputs' evidence. `Calculation` stays in the model for artifact back-compatibility and is refused if a writer emits one. |
| §13.7 citations | a citation supporting a derived fact is one that supports **an input fact** of it. `_support_findings` and `_coverage_findings` learn that; `evidence_handle_not_for_fact` accepts an input's handle. **No handle is ever minted for a derived fact** — the brief's requirement that citations stay attached to observed facts. |

**New codes** (each in the closed `GATE` table, each REFUSE): `derivation_not_offered`,
`derived_fact_not_in_run`, `derived_fact_orientation_reversed`, `derived_result_mismatch`,
`derived_inputs_incomparable`, `derived_operation_not_supported`, `derived_unit_mismatch`.

Every existing refusal in §1's list stays reachable and is tested: wrong fact id, wrong
orientation, wrong sign, wrong unit, percent-vs-pp, unsupported operation, result mismatch, the
opposite comparison, incomparable inputs.

## 7. Evidence-absence facts

A second derived kind, `fact_kind: evidence_scope`, minted **by code from the package alone** —
not requested by the planner, because absence is not a calculation:

```
fact:evidence-scope:no-supported-causal-explanation:<package-digest12>
claim: no_supported_causal_explanation_in_package
```

Minted **only** when the package deterministically establishes it: `explanatory_passages` is empty
**and** `causal_language` is `FORBIDDEN` **and** no fact carries a causal marker. Its statement is
about the evidence, never about the world — *"the evidence in this package supplies no
explanation for the change"*, never *"there was no cause"*. It carries **no citation** and mints
no evidence handle, which is exactly the failure the brief names: today that sentence reuses a
financial-table citation as though the table said it. `counter_evidence_cited_as_support` and
`citation_reused_for_unrelated_claim` stop being the only things standing between the draft and
that claim.

## 8. UI

Trace: four new generation stages between `planning` and `drafting` — `offering_derivations`,
`executing_derivations`, `validating_derivations`, `derived_facts_added`. Messages are composed
from the closed `counts`/`status` vocabulary as every stage already is; **no free text**, so
`test_demo_ui_trace.py::test_no_field_accepts_free_text` keeps holding.

`Facts sent to the model` → the existing `derived_facts` group is populated. Each row shows
operation, input fact ids, result, unit, from/to periods, comparability rule ids, a
`deterministic · tool-generated` badge, and whether the final draft bound it (from
`VerifiedDraft.fact_ledger`, which the panel does not read today). Clicking an input id calls the
existing `revealFact`, so a derived fact links back to its inputs and through them to the table
cell — the derived row itself registers in `state.factRows` under its own id so the click target
exists.

## 9. What this costs, stated plainly

**Both fixtures must be re-recorded, and cannot be re-keyed.** The planner and writer schemas and
both prompts change, so the request digest moves *because the question changed* — the recorded
answers are answers to a different question, and carrying them across would be a lie. This is
unlike S12's two re-keys, which were valid precisely because the request was unchanged.

Re-recording is live: llama.cpp on `127.0.0.1:8080` and the OpenAI key in `.env`. Prompt versions
bump; `PACKAGE_VERSION` does not, because the package does not change.

Then §2's candidate runs end to end under both providers, and the result is reported as it comes.
**The target is not an accepted post.** It is that no numeral in any post is the model's
arithmetic.

## 10. Rejected options

| option | why not |
| --- | --- |
| Derived facts inside `StoryEvidencePackage.facts` | §3 — it would put model-selected content inside a run-id digest and break §2's line. |
| Native provider tool-calling | Two providers, two tool protocols, and a replay store keyed on a request shape that would then differ per provider. The brief prefers the structured field; so does `request_identity`. |
| Keep `Calculation` and let the writer fill `period_surface` correctly | This is the repair that was already tried and is recorded as *"the wrong end to fix it from"*. It leaves the number's correctness resting on the model. |
| A generic `evaluate(expression)` tool | An expression string is arbitrary Python by another name, and nothing downstream could check it. |
| Mint derived facts for every offered triple, with no planner request | Cheaper and worse: the offer set is combinatorial, most of it is irrelevant to the post, and the token budget is §10.2's. The planner choosing is what keeps the set small and relevant. |
| One `percentage_change` operation with a `points: bool` flag | Makes the confusion representable. Two operations make it a refusal at request time. |

---

## 11. Results — the fixtures re-recorded and both providers run *(2026-08-19)*

**The answer, first.** No numeral in any post any provider produced is the model's arithmetic
any more. On all four live runs below, the planner **requested** a derivation, code executed it
and asserted it against the detector's own signal, and every computed figure the writer stated
was bound by a `fact:derived:` id. Not one of the four was **accepted**, and every remaining
refusal is a statement about *words* or about a gap this section names.

### 11.1 What was re-recorded, and what could not be

Five stores existed; four are committed now. Everything below is a live capture unless it says
otherwise, and nothing was re-keyed.

| store | provider / model | disposition | findings |
| --- | --- | --- | --- |
| `local_openai_compatible/generations.jsonl` | Qwen3.5-9B-Q4_K_M.gguf | **rejected** | `metric_surface_unresolved` ×3 |
| `local_openai_compatible/generations_accepted_synthetic.jsonl` | *hand-authored from the row above* | **accepted** | none |
| `local_openai_compatible/generations_rejected_synthetic.jsonl` | *hand-authored from the row above* | **rejected** | `comparative_not_supported_by_text` |
| `openai/generations.jsonl` | `gpt-5.4` / `gpt-5.4-2026-03-05` | **rejected** | `unbound_numeral` ×2, `metric_surface_ambiguous`, `citation_reused_for_unrelated_claim` ×4 |

`generations_rejected_recorded.jsonl` was **deleted**. It existed because the shipped store was
accepted and something had to carry a refusal a real model earned; the shipped store is that
refusal now, so the file would have been a byte-for-byte duplicate.

**`tests/story/fixtures/story_demo/evidence_package.json` had to be rebuilt from the graph, and
§4.3 is why.** `max_derivations` is a `BudgetParameters` field, `BudgetParameters` is inside
`digest_parts()`, and so the live path builds `pkg:…:6943b6e1436a` where the committed fixture
held `pkg:…:4e4363b11373`. Both prompts embed the package id, so **every store recorded live
would have missed the fixture on every lookup**. The plan did not say this and it should have:
§9 says *"`PACKAGE_VERSION` does not [bump], because the package does not change"*, which is
true about the version and false about the digest. The two packages differ in
`budget.parameters.max_derivations`, `budget.artifact_token_estimate` (6636 → 6642),
`package_id` and `package_content_digest`, **and in nothing else**.

### 11.2 The demo candidate under Qwen — and a prompt regression, measured

`cand:cross-metric-divergence:…:9682f1c1c85a`. Four consecutive `--live` runs returned a
byte-identical `generations.jsonl` (`sha256 1e26754afa26930f…`). 8,370 prompt and 1,540
completion tokens; the pre-S13 run of the same candidate cost 7,398 and 1,328.

What worked: the planner requested `compare_levels` out of the four `offers()` printed, the
tool computed `-15.9 percentage_points` / `lower than`, and the writer bound it with
`period_surface "the third quarter of 2022"`. The `numbers` check examined six numerals and
refused none; the `periods` check resolved all three surfaces; **no `unbound_numeral` and no
period or orientation finding appears at all.**

What failed: the model wrote the **unit** into every `metric_surface` — `"percent"`,
`"percent"`, `"percentage_points"`. Repairing only those three strings and touching nothing else
accepts, so they were the whole of what it got wrong.

**This is a regression in writer prompt 2.0.0 and it is not the model drifting.** A control run
of the same candidate against the same server in a `git worktree` of `643935f` is still
**accepted** and its `draft.json` and `post.md` hash to the values `d72ca64` recorded. An
ablation over 2.0.0's two new sections *(four live writer calls)*:

| writer prompt | `metric_surface` values Qwen produced |
| --- | --- |
| 2.0.0, DERIVED FACTS + EVIDENCE SCOPE | `percent`, `percent`, `percentage_points` |
| 2.0.0, DERIVED FACTS only | `percent`, `percent`, `percentage_points` |
| 2.0.0, EVIDENCE SCOPE only | `percent`, `percent`, `percentage points` |
| 2.0.0, neither section populated | `GAAP Gross Margin`, `Adjusted Gross Margin`, … |

Either new section alone is enough, so this is a 9B model degrading as the prompt lengthens
rather than anything either section *says*. All six `length_target` values 3–8 were then
measured live and **every one is refused**, so there was nothing to move `config/story.yaml` to
and it stays at 4. **Nothing in `story/stages/` was changed to make a recording pass.**

### 11.3 §2's driving candidate, both providers

`cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`, reachable from
`python -m story demo` since 643935f. The pre-S13 column is a live Qwen run of the same
candidate in a worktree of `643935f`, made today so the comparison is against the same server.

| | pre-S13 (Qwen, `643935f`) | S13 (Qwen) | S13 (`gpt-5.4`) |
| --- | --- | --- | --- |
| derivation requested | — (no such field) | `absolute_change` 2022Q2→2022Q3 | `trend_direction` 2022Q2→2022Q3 |
| derivation executed | — | yes, `-446,000,000 USD`, `decreased by`, asserted against the detector's `delta` | yes, `decrease`, asserted against the detector's `direction` |
| what the writer bound | nothing — a `calculation` object it filled itself | `fact:derived:absolute-change:…:155d76ed704a`, `period_surface "the third quarter of 2022"` | `fact:derived:trend-direction:…:faf1a1d72ef9`, rendered as a *period string* |
| blocking findings | **6** | **2** | **4** |
| codes | `unbound_numeral` ×2 (on the retyped `556000000.0` and `110000000.0`), `period_unresolvable`, `period_named_in_text_contradicts_binding`, `calculation_result_surface_mismatch`, `comparative_not_supported_by_text` | `unbound_numeral` ×1, `derived_unit_mismatch` | `unbound_numeral`, `derived_unit_mismatch`, `derived_operation_not_supported`, `citation_reused_for_unrelated_claim` |
| tokens (prompt / completion) | 8,887 / 1,433 | 10,454 / 1,559 | 10,127 / 1,304 |
| disposition | rejected | rejected | rejected |

**Gone, and stated plainly:** `period_unresolvable`, `period_named_in_text_contradicts_binding`,
`calculation_result_surface_mismatch` and `comparative_not_supported_by_text` no longer appear
on this candidate, and neither do the two `unbound_numeral`s the old draft earned by retyping
its own inputs inside a sentence that could carry no bindings. §2's diagnosis was right about
the mechanism and the structural repair removed it.

**Not gone, and this is the finding §2 did not predict.** `unbound_numeral` still fires — on the
literal `2022` of the **`from` period**:

> *"Adjusted Gross Profit decreased by 446000000.0 USD from the second quarter of 2022 to the
> third quarter of 2022."* — refused at `char_start 78`, the first `2022`.

A binding declares **one** `period_surface`, and §6 fixes it to `to_period`
(`_derived_fact_lines`: *"The period surface is `to_period`'s"*). A two-period derived fact whose
sentence names both periods therefore has one covered year and one uncovered one, **by
construction**. `gpt-5.4` hit the identical wall in its own wording. The plan's claim that *"the
model cannot forget a field it no longer writes"* is true, and it is not sufficient: the field
it no longer writes could hold only one period, and so can the binding that replaced it.

### 11.4 Two defects this run found, reported and not repaired

Both are in `story/stages/**`, which this packet may not change.

1. **`trend_direction` is offered to the planner and can never be bound.** `offers()` publishes
   it, `execute_all` computes it, and §13 then refuses any draft that binds it with
   `derived_operation_not_supported` — the deliberate departure `fd55045` records, because the
   verifier cannot recompute a stored sign convention. `gpt-5.4` requested it and was refused for
   choosing something it was offered. An operation the verifier will always refuse should not be
   in the offer set; the two should agree in one place.
2. **A derived fact carries a `metric_surface` its own package may refuse.** The demo candidate's
   `compare_levels` in the planner's chosen orientation has `metric_id: gaap_gross_margin`, whose
   only package surface is `"Gross Margin"` — ambiguous, so `_derived_fact_lines` prints *"no
   surface names this metric uniquely in this package — do not write about this fact"* for a fact
   the plan's own key point requires the writer to state. The offer set and the writable-surface
   rule disagree, and the model is left with no legal way to write a figure it was asked for.

`resolve_demo_inputs`'s docstring in `story/pipeline.py` is also stale — it still says *"Only
§6.6's D4 detector runs — the demo's candidate is a cross-metric divergence"*, which 643935f
replaced with per-id dispatch.

### 11.5 What is no longer true elsewhere

`TABLE_CELL_CITATIONS.md` §S7a's results block describes `generations_rejected_recorded.jsonl`
and the nineteen identical 1.4.0 writer calls. Both were accurate when written and neither is
true of the tree now; that block is a dated record and was left standing rather than rewritten,
and this section is where the supersession is stated.

### 11.6 Suite

`python -m pytest tests/story -m 'not live and not neo4j' -o addopts='' -q` → **3389 passed, 153
deselected**. The `live` and `neo4j` marks were run separately: **152 passed, 1 skipped**.

---

## 12. The repair packet — §11.3 and §11.4 closed, and both candidates accepted *(2026-08-19)*

**The answer, first.** The four defects §11 reported are fixed, and both candidates now run end
to end to **accepted** under live Qwen — including
`cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`, the candidate §2
was written about, which went `6 -> 2 -> 0` blocking findings across the three stages of this
plan. **No verification rule was weakened.** Two rules were made *stricter* and the rest of the
repair is in the prompt and in the offer set. §11's tables are dated records of what was true
that morning; this section supersedes them.

### 12.1 `unbound_numeral` on the `from` period — coverage widened, checking widened with it

`_covering_spans` now covers **both** of a derived fact's periods, not only the `to_period` its
binding declares. The two windows are read off the *fact* — the binding can carry one surface
and the derivation knows two — and matched through §13.4's closed grammar rather than by string,
so `"Q2 2022"` and `"the second quarter of 2022"` are one answer.

**Coverage did not become trust, and the pairing is the point.** §13.1 asks *"can a reader tell
which claim this numeral belongs to"*; §13.4 asks *"is the claim true"*.
`_derived_period_grounding_findings` replaces the generic prose rule for a derived binding and
asks two questions where the old one asked one: the sentence must name `to_period`, **and** every
period it names must be one of the derivation's two. That second half is new — the *any*-rule it
replaced passed a sentence naming Q3 and Q1 as soon as Q3 agreed — and it is what stops the
widened coverage from licensing a window nothing computed. Four of the five tests for it fail
against `243a5e0`.

### 12.2 `trend_direction` — withdrawn from the offer set, still refused by the verifier

`OFFERABLE_OPERATIONS` is §4.1's seven minus `trend_direction`, and `offers.validate` refuses a
request for it with the reason. **Withdrawn rather than made checkable**, and the argument is
G3's: the verifier could only recompute the word by being handed
`detector_config.quantity_direction` from the same composition root that handed it to the
producer, which is a verifier a caller can configure into agreement with the thing it is
checking. Nothing is lost — the operation yields **no numeral** (`result=None`, unit `direction`),
and the same direction over the same two facts already reaches the writer as `absolute_change`'s
`display_semantics`, attached to a number the verifier does recompute.
`derived_facts.RECOMPUTABLE` is unchanged, so a `trend_direction` fact arriving from a replayed
artifact is still `derived_operation_not_supported`. The agreement is asserted generally: every
operation `offers()` may print is executed and driven through `integrity_findings`, and the test
fails if the union of operations exercised is not the whole offer set.

### 12.3 A derived fact's `metric_surface` — the prompt was withholding one the verifier accepts

Not "the fact should not be offered": **the prompt was wrong**. `MetricAliasIndex.from_package`
indexes `(metric_id, label, *aliases)` and `normalise` maps `_` to a space, so `"GAAP Gross
Margin"` has always resolved uniquely through the `gaap_gross_margin` **id** entry — it is what
the committed accepted draft binds. `metric_surfaces_for` offered only `label` and `aliases`, and
that metric's label `"Gross Margin"` is dropped as a sub-phrase of `"Adjusted Gross Margin"`. So
the FACTS row and the DERIVED FACTS row both printed *"do not write about this fact"* for a
metric the plan required, while rule 4 told the model to write *"GAAP gross margin"* — no legal
answer existed. The id is offered now, last, filtered by the same sub-phrase rule as everything
else, and a test asserts the two ends agree in both directions over the **real** package.

### 12.4 The writer-prompt regression — three live drafts, not an inference

§11.2 concluded the regression was *"a 9B model degrading as the prompt lengthens rather than
anything either section says"*. **That was wrong**, and the ablation that follows is why: with
§12.3's one-line change and no other, the three `metric_surface` values went from `percent`,
`percent`, `percentage_points` to `gaap gross margin`, `Adjusted Gross Margin`, `gaap gross
margin` on the same server, the same package and the same plan. The prompt length did not move.

| writer prompt | what Qwen produced | disposition |
| --- | --- | --- |
| 2.0.0 | `metric_surface`: `percent`, `percent`, `percentage_points` | rejected, `metric_surface_unresolved` ×3 |
| + the metric id offered | the three surfaces above, and `rendered` `"15.9 percentage_points"` | refused at §12, `binding_rendering_not_in_text` |
| + `figure:` line, words | the same, `rendered` `"15.9 percentage points"` | **accepted** |

The second row is the second defect in the same line: the DERIVED FACTS row printed
`{result} {unit}` under a `metric_id`, which is a FACTS reading's shape, and the model copied the
machine spelling into `rendered` while writing the words in its text. The row now hands the
figure over as one quoted string a sentence can carry, names the refused spelling beside it, and
prints a monetary figure at the scale its inputs were filed at (`$446 million`, exact division
only). That last part was measured too: shown `$446000000.0` — a legal surface — Qwen wrote
`"446000000.0 USD"`, which carries no unit surface and is `derived_unit_mismatch`; shown
`$446 million` it wrote `$446 million`, and §2's candidate reached `accepted`.

`WRITER_PROMPT_VERSION` is **2.1.0**. `PLANNER_PROMPT_VERSION` did not move, and the planner row
in the committed store is byte-for-byte the row §11's re-record captured.

### 12.5 The stores, re-recorded live again

| store | provider / model | disposition | findings |
| --- | --- | --- | --- |
| `local_openai_compatible/generations.jsonl` | Qwen3.5-9B-Q4_K_M.gguf | **accepted** | none |
| `local_openai_compatible/generations_rejected_synthetic.jsonl` | *the row above, two metrics swapped* | **rejected** | `comparative_not_supported_by_text` |
| `openai/generations.jsonl` | `gpt-5.4` / `gpt-5.4-2026-03-05` | **rejected** | `unbound_numeral` ×2, `citation_reused_for_unrelated_claim` ×4, `connective_sentence_carries_a_claim` |

`generations_accepted_synthetic.jsonl` was **deleted**: the shipped recording is the accepted
branch now, so the file could only have been a hand-authored accepted store beside a real one —
the duplication `generations_rejected_recorded.jsonl` was deleted for at 243a5e0. Four
consecutive `--live` runs produced a byte-identical `generations.jsonl`
(`sha256 8645d1a95a533b29…`).

**`gpt-5.4` is still refused, and that is the result rather than a target.** Its
`metric_surface_ambiguous` is gone — §12.3's repair reaching a second provider — and what
remains is citation discipline: it requested no derivation at all this time, wrote six sentences,
carried a period into a `connective` one, and re-cited a table span in four sentences that bind
nothing.

### 12.6 §2's candidate, end to end

| | pre-S13 | S13 (`243a5e0`) | this packet |
| --- | --- | --- | --- |
| blocking findings | **6** | **2** | **0** |
| codes | `unbound_numeral` ×2, `period_unresolvable`, `period_named_in_text_contradicts_binding`, `calculation_result_surface_mismatch`, `comparative_not_supported_by_text` | `unbound_numeral`, `derived_unit_mismatch` | — |
| disposition | rejected | rejected | **accepted** |

> *"Adjusted Gross Profit decreased by $446 million from the second quarter of 2022 to the third
> quarter of 2022."*

Both periods named, both covered, both checked; the figure bound to
`fact:derived:absolute-change:…`; both input cells cited. `story-v1-cf084bb0eb87`.

### 12.7 Suite

`python -m pytest tests/story -m 'not live and not neo4j' -o addopts='' -q` → **3397 passed, 153
deselected**. The `live` and `neo4j` marks were run separately against both real servers:
**152 passed, 1 skipped**. Repository-wide offline: **6120 passed, 250 deselected**.

---

## 13. Packet H1 — the second half of §1's sentence, which was not true *(2026-08-19)*

**The answer, first.** §1's target property has two halves. *"Every numeral in an accepted post
is either a packaged observation or a derived fact code computed"* held under adversarial review
and still holds. *"…and the model only chooses how to express that fact"* did **not**: an
adversarial review reproduced four ways for the sentence around a correct number to say the
opposite thing, name a different metric, or state a change as a level, each **accepted with zero
findings**. All four are closed, every one with a test that fails against `ead0290`, and **no
existing check was weakened** — several were made stricter, and two fixture sentences had to be
rewritten because they are sentences the strengthened rules refuse.

Both candidates still run end to end to **accepted** under live Qwen.

### 13.1 What was accepted, and what the four have in common

| # | the sentence, bound to a derivation whose number is right | why nothing fired |
| --- | --- | --- |
| F1 | *"Adjusted gross **margin** fell $446 million in the third quarter of 2022."* | `_derived_metric_findings` reads the **declared** `metric_surface`; the rule that reads the *prose* lives in the observed branch behind `GROUNDED_SENTENCE_KINDS`, and a derived binding `continue`s past it on every sentence kind |
| F1 | *"**Revenue** fell $446 million in the third quarter of 2022."* | as above — a metric the package does not carry |
| F2 | *"Adjusted gross profit **grew** $446 million…"* (also `climbed`, `surged`, `gained`, `jumped`, `expanded`) | `orientation_findings` read `CHANGE_DIRECTION`'s thirteen words to decide whether it ran |
| F2 | *"Adjusted gross profit **was** $446 million in the third quarter of 2022."* | a change stated as a level: no verb at all to match |
| F3 | *"Adjusted gross profit **turned negative** between…"* over `did_not_cross` | `SEMANTIC_DIRECTION[CROSSED_ZERO] = None`, no numeral for §13.1, and `ungrounded_words` reached only an `EvidenceScopeFact` |
| F4 | *"…no explanation for the **92 percent** collapse to **7** from **9999**."* | `_covering_spans` consumed `binding.period_surface` from every binding, including one no check resolves |

**F1, F2 and F3 are one fault with three faces — a rule whose vocabulary, or whose sentence-kind
gate, decided whether the rule ran — and F4 is its mirror: a *licence* granted on a
justification that was false for one binding kind.** The repository already knew the first. `COMPARATIVE_TERMS`' own comment records R8 measuring it for
comparatives — *"ordinary investor English walks around a closed word list, which is why R8 also
stopped treating a lexicon hit as the trigger"* — and `comparative_direction`'s docstring makes
`None` a refusal. The change-verb half was written the opposite way one release later, and §13's
metric- and period-grounding rules were given a sentence-kind gate whose premise §6 then removed
and whose comment says so: *"a `calculated` sentence's metric grounding is a separate question
nobody has measured"*. S13 moved every derived figure into a `calculated` sentence. That
unmeasured question became the whole guard on which metric a code-computed number is attributed
to.

### 13.2 F1 — the prose is read for a derived binding too, on every sentence kind

`_derived_metric_grounding_findings` is §13.5's prose rule with R2's admissible set:
`{metric_id, from_metric_id}`, the same pair `_derived_metric_findings` accepts for the
declaration, so a `compare_levels` sentence may name either side and the committed accepted
draft names both. Same two codes as the observed rule and the same alias index, because it is
the same fault. It runs whatever the sentence's kind — `_derived_period_findings`' precedent, and
for that function's reason — and it is counted in `metric_identity.examined`, which goes 5 → 6 on
the demo candidate.

**A fixture sentence is now refused, and it should be.**
`test_story_deterministic_verifier.py`'s `GAP_TEXT` read *"The gap between the two measures was
15.9 percentage points."* — a sentence naming neither metric. The observed half of this rule has
refused that shape since R8, writer rule 10 requires *"name each figure's metric on its own side
of the comparing word"*, and the live accepted draft names both margins. The fixture was
disagreeing with the corpus; it now reads *"The gap between adjusted gross margin and GAAP gross
margin was 15.9 percentage points."*, with no comparative and no change verb, so `claims.py`'s
note that this sentence *"asserts no direction at all"* stays true of it.

### 13.3 F2 — the change-verb rule fails closed, and the lexicon is no longer the guarantee

Two changes, and the order matters:

1. **The rule stopped depending on the vocabulary.** A directional derivation whose sentence
   states no word carrying the computed direction is `derived_direction_not_stated_in_text`
   (REFUSE, new). This is what catches the level sentence, which no width of lexicon could.
2. **`CHANGE_DIRECTION` was widened** from 13 words to 151 so that the refusal lands on prose
   that says *nothing* rather than on prose that says the right thing differently. `None` is now
   argued as a boundary rather than a list: desirability (`improved`, `worsened`, `strengthened`,
   `weakened`, `recovered`), magnitude (`widened`, `narrowed`), or a sign change with no
   direction (`turned`, `swung`, `flipped`). `drop`/`drops`/`dropping` are absent for
   `STATE_TERMS`' §16 reason and `contract`/`contracts` because `"contracts"` is a
   `DECLARED_AMBIGUOUS` metric surface.

**A cost, named rather than discovered.** `orientation_findings` scans the whole sentence for
each binding, so one sentence stating two derivations that moved opposite ways — *"X fell $446
million while Y rose $12 million"* — is refused on both. Attributing a verb to one of two
bindings needs clause attribution neither this module nor `FactBinding` has, which is
`language.py`'s recorded reason for §13.10's conditions 3 and 6. The shape predates H1; the
wider lexicon makes it likelier.

**A `display_semantics` with no direction now refuses every direction word and demands none.**
`UNCHANGED` says the quantity did not move; `DIRECTION_UNVERIFIABLE` says the metric's sign
convention was never measured, so *nobody* established which way it went. (`EQUAL_TO` and `TIMES`
are the map's other `None`s and belong to `compare_levels` and `ratio`, which this rule returns
on before reaching the question.) Both abstained entirely before. This is a strengthening beyond
what the review asked for, and it is the same fault: the fact that says *"the direction is
unknown"* was the one over which any direction word passed.

### 13.4 F3 — `crossed_zero` gets the prose rule its sibling operations have

`CROSSING_TERMS` and `_crossing_findings`, on `_direction_findings`' shape: a phrase asserting
the opposite is `derived_fact_orientation_reversed`, and **no phrase at all** is
`derived_direction_not_stated_in_text`. Total by construction — every member states a polarity —
and asserted by test, which is `COMPARATIVE_DIRECTION`'s discipline.

**Withdrawing the operation from the offer set was the alternative and it was rejected.** That is
what §12.2 did to `trend_direction`, on the ground that the verifier could not recompute the word
without importing a sibling stage's sign-convention table. This verifier *can* recompute this
word, from two values it already holds (`_result_findings` does); only the prose was unguarded,
and the repair for unguarded prose is a guard.

**A collision this found, reported and not repaired.** The most natural true wording of the
negative branch — *"did not cross zero"* — is refused, and not by the new rule: `"did not"` is a
§13.14 `ABSENCE_TERMS` member and the sentence earns `unsupported_absence_claim`, a check that
predates S13 and that H1 may not weaken. So on `did_not_cross` the writable surfaces are the ones
that state the fact positively — *"remained positive"*, *"stayed positive"*, *"on the same side
of zero"* — and every negated spelling collides. That is a real narrowing of what the operation
can say, in the conservative direction, and it is a reason to ask whether `crossed_zero` earns
its place in the offer set rather than a reason to loosen either rule.

### 13.5 F4 — the coverage licence, and the decision about §7's fact

**The hole.** `_covering_spans`' own docstring justified covering a declared period surface with
*"covering it here is not trusting it: `_check_periods` resolves the declared surface"*. For an
evidence-scope binding that sentence was false — `_check_periods`, `_check_units` and
`_check_metric_identity` all resolve neither branch and `continue` — so a model-written string
licensed §13.1 coverage for whatever numerals it contained. Closed at both ends:
`_period_surface_is_checked` narrows the licence to *"a surface some check resolves"*, and
`scope_findings` refuses a scope binding that declares either surface at all
(`evidence_scope_binding_declares_a_surface`, REFUSE, new). §12.1's two-period coverage widening
is untouched and is driven again by its own test.

**The second half, decided: §7's fact is inert as a binding target today, it is documented as
inert, and it is kept.** `prompts._evidence_scope_lines` prints the statement and deliberately
not the `fact_id`, and writer system rule 17 reads *"it is a limit on what you may write and
never a sentence to write"*. So §7's claim above — that this makes
`counter_evidence_cited_as_support` *"stop being the only thing standing between the draft and
that claim"* — **is not true of the tree**, and this sentence is the correction.

Measured on both live runs today: `derived_facts.json` carries **one** `evidence_scope_facts`
row in each, and **neither draft binds it** — the model obeys rule 17, which is the section
working as written and is also why nothing downstream of the binding path can be exercised by a
model run.

Making it bindable was considered and rejected here: it means printing an id, adding a writer
rule that permits the sentence, and **reversing rule 17** — a change to what the product is
willing to publish, not a repair — and it re-records both fixtures live and moves
`WRITER_PROMPT_VERSION` for it. Against that, the machinery is worth carrying: the verifier is
authoritative independently of the writer (`codes.py`'s own argument for
`operation_not_recomputable`, likewise unreachable from `WRITER_OPERATIONS`), a draft can arrive
from a replayed artifact or a later prompt, and a rule written only once a prompt invites the
failure is a rule written after the incident. What H1 refused to leave standing is the
*asymmetry* the review named — unreachable machinery guarding a reachable hole. The hole is
closed; the machinery is now documented as unreachable, in `scope_findings` and here.

### 13.6 What this cost, stated plainly

| | before H1 | after |
| --- | --- | --- |
| `GATE` | 99 codes | **101** — `derived_direction_not_stated_in_text`, `evidence_scope_binding_declares_a_surface` |
| `CHANGE_DIRECTION` | 13 words | **151** |
| `CROSSING_TERMS` | — | **44 phrases**, total on polarity |
| fixture sentences rewritten | — | 2: `GAP_TEXT` names its metrics, `scope_sentence` declares neither surface |

**No prompt changed, and the proof is a hash.** `WRITER_PROMPT_VERSION` and
`PLANNER_PROMPT_VERSION` are untouched, no fixture was re-recorded, and the live re-run of the
demo candidate returned a `generations.jsonl` hashing to `8645d1a95a533b29…` — byte-for-byte
§12.5's committed store. Everything H1 changed is downstream of the model's answer.

Two committed artifact digests did move and neither is a model output:
`verification_report.json` and the synthetic's `rejected.json` both carry
`metric_identity.examined`, which went 5 → 6 when the new grounding rule started counting what it
checked. The alternative was a check that runs and does not say so, which is the discipline
`DeterministicVerifier`'s own docstring states for freshness.

### 13.7 Both candidates, end to end and live

| | §12 | H1 |
| --- | --- | --- |
| `cand:cross-metric-divergence:…:9682f1c1c85a` | accepted | **accepted**, `story-v1-79fd9ddc03fe` |
| `cand:metric-move:…:86ba9e13455d` | accepted, `story-v1-cf084bb0eb87` | **accepted**, `story-v1-cf084bb0eb87` |

> *"Adjusted Gross Profit decreased by $446 million from the second quarter of 2022 to the third
> quarter of 2022."*

Same draft, same story run id, and now three more rules stand behind it: the sentence names the
metric the derivation is of, states the direction `display_semantics` computed, and would be
refused if it stated any other.

### 13.8 Suite

`python -m pytest tests/story -m 'not live and not neo4j' -o addopts='' -q` → **3423 passed, 153
deselected** (3397 before H1; **+25** from H1 and one from a concurrent
`PackagedFact.fact_kind` narrowing this packet did not author, and 20 of the 25 fail against
`ead0290`). The `live` and
`neo4j` marks were run separately against both real servers: **152 passed, 1 skipped**.
Repository-wide offline: **6146 passed**.
