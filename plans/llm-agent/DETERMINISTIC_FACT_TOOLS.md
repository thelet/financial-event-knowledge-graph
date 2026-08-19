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
