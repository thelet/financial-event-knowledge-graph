# 01 — Pipeline contracts

*Traced against `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`,
the candidate both live runs of 2026-08-26 failed on. Artifacts:
`data/story_demo/story-v1-1daff167348f/` (Qwen) and `…/story-v1-76da8465cd95/` (gpt-5-nano).
All values below are read from those directories or recomputed from them (verified 2026-08-26).*

---

## 1. The runtime path, stage by stage

| Stage | Input | Producer | Consumer | Validation | Failure behaviour |
| --- | --- | --- | --- | --- | --- |
| Freshness gate | `DemoInputs` | `story/stages/freshness/gate.py` | `run_demo` | 14 checks, 8 `RefusalCode`s | raises `FreshnessRefused` — **before any model call** |
| Evidence package | `StoryCandidate` + `EvidenceRequest` | `story/stages/packaging/package_assembly.py` | planner, writer, verifier | budgets in `demo_manifest.budget`; `package_content_digest` | package warnings, never a refusal |
| Derivation offers | `package`, `candidate` | `story/stages/derivation/offers.py:167` | `planner_prompt`, `plan_violations`, `execute_all` | pure function; capped at `max_derivations: 12` | empty list ⇒ prompt says so |
| **Planner (model call 1)** | `PLANNER_SYSTEM` + `planner_prompt(package, offered=)` | `story/stages/generation/planner.py:558` `plan_story` | `plan_violations` | `planner_schema(causal_language=)` server-side; `StoryProviderSchemaError` on violation | `EditorialPlanRejected` ⇒ `plan_refused`; `StoryProviderError` ⇒ `provider_failed` |
| Plan validation | `EditorialPlan`, `package`, `offered` | `planner.py:298` `plan_violations` | `run_demo` | **12 codes, all referential** — §5 | `EditorialPlanRejected` ⇒ `plan_refused` |
| Derivation execution | `plan.requested_derivations` | `story/stages/derivation/execute.py:154` | slot table, writer prompt, verifier | 11 `DerivationRefusalCode`s | refusals are values on `DerivationResult` ⇒ `derivation_refused` |
| Slot table | `package`, `derived`, `passages_backing_facts(package)` | `story/stages/composition/slot_table.py:79` | `writer_prompt`, `compile_draft` | none — total function | a row that can offer nothing still gets a handle |
| **Writer (model call 2)** | `writer_system(style)` + `writer_prompt(…, slots=)` | `story/stages/generation/writer.py` `write_story` | `templates_from` | `writer_schema()` server-side | `StoryProviderError` ⇒ `provider_failed` |
| Writer structural gate | model JSON, `plan`, `slots` | `writer.py:498` `templates_from` | `compile_draft` | **15 codes** — §6 | `DraftRejected` ⇒ `draft_refused`. **Verifier never runs** |
| Draft compiler | `templates`, `package`, `plan`, `derived`, `passages` | `story/stages/composition/compile.py:101` | verifier | **9 codes** — §7 | `CompositionRefused` ⇒ `composition_refused`. **Verifier never runs** |
| Deterministic verifier | `CompiledDraft.draft`, `package`, `plan`, `derived` | `story/stages/verification/deterministic.py` | `run_demo` | **102 codes**, 12 checks | returns `VerifiedDraft`; never raises ⇒ `accepted` / `rejected` |
| Run write | everything above | `pipeline.py:1185` `_write_run` | disk / demo UI | artifact digests in `demo_manifest.artifacts` | — |

**Concrete values for this candidate** (verified): package holds 2 facts, 2 primary passages,
5 warnings, 0 counter-evidence; `offers()` returns **3** triples; the Qwen plan requested 2 and
got 2 `DerivedFact`s plus 1 `EvidenceScopeFact`; the slot table is
**F1, F2, D1, D2, P1, P2** — 6 rows.

---

## 2. The slot table, as the writer actually saw it

Rebuilt from `story-v1-1daff167348f`'s own artifacts (verified):

| Handle | Kind | Slots offered → value |
| --- | --- | --- |
| `F1` | observed | `{{F1}}` → `$556 million` · `{{F1.metric}}` → `Adjusted Gross Profit` · `{{F1.period}}` → `the second quarter of 2022` |
| `F2` | observed | `{{F2}}` → `$110 million` · `{{F2.metric}}` → `Adjusted Gross Profit` · `{{F2.period}}` → `the third quarter of 2022` |
| `D1` | derived (`absolute_change`) | `{{D1}}` → `$446 million` · `{{D1.direction}}` → `decreased by` · `{{D1.metric}}` → `Adjusted Gross Profit` · `{{D1.from_period}}` → `the second quarter of 2022` · `{{D1.to_period}}` → `the third quarter of 2022` |
| `D2` | derived (`percentage_change`) | `{{D2}}` → **`80.215827338%`** · `{{D2.direction}}` → `decreased by` · `{{D2.metric}}`, `{{D2.from_period}}`, `{{D2.to_period}}` |
| `P1` | passage | (no text slot) — `norm:…open-20220630.htm#p133` |
| `P2` | passage | (no text slot) — `norm:…q32022formxex991earningsre.htm#p23` |

`{{D2}}` → `80.215827338%` is a **code-owned rendering defect**. `renderings.derived_figure`
special-cases `USD` and otherwise applies `DERIVED_FIGURE_FORMATS["percent"] = "{value}%"` to
the raw float. Its own docstring says *"Everything else is printed as Python renders the
float."* The only gate that would notice is `over_precision`, which is **WARN, non-blocking**
(verified in `codes.GATE`).

---

## 3. Field-level inventory — planner output (`story_editorial_plan`, `PLANNER_PROMPT_VERSION` 1.2.0)

19 leaf fields. `EditorialPlan` adds `candidate_id`, `package_id`, `prompt_version`, `model_id`,
all stamped by code after the call.

| Field | Schema | Produced by | Semantic meaning | Used by | Could code derive it? |
| --- | --- | --- | --- | --- | --- |
| `thesis` | plan | model | The claim the post makes | `_plan_lines` → writer prompt; `THESIS_EMPTY` | **No** — editorial |
| `why_it_matters` | plan | model | Why a reader should care | writer prompt | **No** — editorial |
| `key_points[].claim` | plan | model | One statement the post must make | writer prompt (prose only) | **No** — editorial |
| `key_points[].required_fact_ids` | plan | model | Which `obs:` ids the point rests on | `_id_violations`, `_thesis_violations` | **Partly** — the candidate's `anchor_observation_ids` already names the pair; *which subset* is editorial |
| `key_points[].required_citation_passage_ids` | plan | model | Which passages back the point | `_id_violations`, `_referenced_passage_ids` | **Yes** — `PackagedFact.passage_id` gives the passage for every fact; nothing else is admissible |
| `key_points[].statement_class` | plan | model | reported / calculated / explanatory | writer prompt; never checked against the claim | **Yes for the first two** — a point naming only `obs:` ids is `reported`; one naming a requested derivation is `calculated` |
| `counterpoints[].claim` | plan | model | The opposing reading | writer prompt; `required_counterpoint_absent` (§13) | **No** — editorial |
| `counterpoints[].required_fact_ids` / `…_passage_ids` | plan | model | Its grounding | `COUNTERPOINT_UNGROUNDED` | **Partly** — the admissible set is `package.counter_evidence`; the choice within it is editorial |
| `requested_derivations[].operation` | plan | model | Which of 7 operations | `DERIVATION_NOT_OFFERED` by triple equality | **Yes** — must be copied verbatim from `offers()` |
| `requested_derivations[].from_fact_id` / `to_fact_id` | plan | model | The two inputs, in order | `DERIVATION_NOT_OFFERED` | **Yes** — same; orientation is part of the offered triple |
| `required_warnings[]` | plan | model | Warnings the prose must state | `UNKNOWN_WARNING_CODE`; §13 `required_warning_absent` | **Yes** — `claim_qualifying_warnings(package)` already computes the admissible set |
| `causal_language` | plan | **code** | forbidden / reported_only | pinned to a 1-member enum; re-stamped after the call | **Already code** — `causal_language_for(package)` |
| `uncertainty` | plan | model | Free prose caveat | writer prompt | **No** — editorial |
| `structure[]` | plan | model | Section order | writer prompt; **never validated** | **No**, but see below |
| `prohibited_claims[]` | plan | model | Claims to avoid | writer prompt; not checked | **No** — editorial |
| `unusable_evidence[].id` / `.reason` | plan | model | Counter-evidence deliberately not used | `COUNTER_EVIDENCE_UNACCOUNTED`, `UNKNOWN_UNUSABLE_ID` | **Partly** — the id set is known; the reason enum is a judgment |

**`structure[]` is unvalidated and was observably garbage.** In `story-v1-76da8465cd95`,
gpt-5-nano returned `structure: ["thesis", "why_it_matters", "key_points", "counterpoints",
"requested_derivations", "required_warnings", "uncertainty", "causal_language", "structure"]` —
the schema's own property names (verified). It reached the writer prompt unchallenged.

**Six of the nineteen fields are things code either already knows or already constrains to a
one-element answer** — `causal_language` (already stamped), `requested_derivations`' three
fields (copy-from-list), `required_citation_passage_ids` (a lookup), `required_warnings`
(a filter of a known set).

---

## 4. Field-level inventory — writer output (`story_post_draft`, `WRITER_PROMPT_VERSION` 3.0.0)

**Four leaf fields.** This is the schema after the 2026-08-23 deterministic-compiler change.

| Field | Schema | Produced by | Semantic meaning | Used by | Could code derive it? |
| --- | --- | --- | --- | --- | --- |
| `title` | draft | model | The heading | `Draft.title`; §13.15 refuses a numeral or claim in it | **No** — prose |
| `sentences[].text` | draft | model | The sentence, with `{{H}}` / `{{H.field}}` slots | `compile_draft` substitution; every §13 prose check | **No** — this is the product |
| `sentences[].kind` | draft | model | reported / calculated / explanatory / connective | citation policy in `_citations_for`; §13.9 `reported_vs_calculated` | **Mostly yes** — a sentence whose only value slots are `F` rows is `reported`, one naming a `D` row is `calculated`, one with `rests_on` and no value slot is `explanatory`, one with neither is `connective`. The slot table already knows |
| `sentences[].rests_on[]` | draft | model | Passage handles an `explanatory` sentence paraphrases | `RESTS_ON_*` (writer), `PASSAGE_HANDLE_UNKNOWN` / `RESTS_ON_WITHOUT_EXPLANATORY_KIND` (compiler), then §13.7 | **Partly** — the admissible set is exactly the `P` rows; *which* passage a paraphrase came from is not derivable, but 3 of the 4 recorded failure modes are about the set, not the choice |

**The schema's history, measured by rendering `writer_schema()` at every version in the git log
of `story/stages/generation/prompts.py`** (verified 2026-08-26):

| Version | Schema leaves | Numbered rules | What the model emitted |
| --- | --- | --- | --- |
| 1.0.0 | 14 | 16 | + `fact_bindings[]` (4), `calculation[]` (5), `citations[].passage_id`/`.quote` |
| 1.1.0 – 1.3.0 | **15** | 17–18 | peak; `calculation[].period_surface` added |
| 1.4.0 | 14 | 18 | citations became `evidence_id` |
| 2.0.0 – 2.2.0 | 8 | 17 | `calculation[]` left the schema |
| **3.0.0** | **4** | **14** | `fact_bindings[]` and `citations[]` left; `rests_on[]` arrived |

So the 3.0.0 change removed **five model-authored machine fields** — `fact_bindings[].fact_id`,
`.rendered`, `.metric_surface`, `.period_surface`, and `citations[].evidence_id` — and added
one, `rests_on[]`. From the 1.1.0 peak the schema has shrunk from 15 leaves to 4.

Separately, the compiler now constructs **fields no model version ever emitted**: every
`char_start`/`char_end` on both bindings and citations, and `PassageCitation.passage_id` and
`.document_id`.

**`Calculation` is not among them.** It left the *schema* at 2.0.0 and was retired outright by
the 3.0.0 compiler: `DraftSentence.calculation` is `None` on every draft the current path
produces, and a `calculated` sentence is now identified by binding a `DerivedFact` instead
(`deterministic.py:2044-2057`, `calculated_sentence_without_calculation`). The §13.9 machinery
that recomputes a `Calculation` stays reachable only from the 20 stored pre-3.0.0 drafts that
carry one.

---

## 5. Field-level inventory — what the compiler constructs

`story/stages/composition/compile.py` builds these from the slot table (verified by running it):

| Type | Field | Source |
| --- | --- | --- |
| `FactBinding` | `fact_id` | `SlotRow.fact_id` of the named handle |
| | `rendered` | the substituted string — `SlotRow.offers[""]` |
| | `char_start` / `char_end` | the span the substitution occupied |
| | `metric_surface` | `SlotRow.offers["metric"]` or `["to_metric"]` |
| | `period_surface` | `SlotRow.offers["period"]` or `["to_period"]` |
| `PassageCitation` | `passage_id`, `document_id` | the passage behind the cited evidence handle |
| | `evidence_handle` | `SlotRow.evidence_handles` (one for `F`, both inputs for `D`) |
| | `char_start` / `char_end` | `evidence_slice.span_for_handle(fact, passage)` |
| `Calculation` | **never built** | *(correction, 2026-08-26)* — `story/stages/composition/` never constructs a `Calculation`; `grep -rn "Calculation" story/stages/composition/` returns nothing. `DraftSentence.calculation` is `None` on every 3.0.0 draft, and the derived-fact binding replaces it. 20 of the 40 stored drafts carry one; all are pre-3.0.0 |
| `SlotFill` | `sentence_index`, `handle`, `field`, `inserted`, `char_start`, `char_end` | recorded to `composition.json` as provenance |

Sentence 3 of the correct draft, compiled (verified):

```text
Between the second quarter of 2022 and the third quarter of 2022, Adjusted Gross Profit
decreased by $446 million.
  bind '$446 million' [101:113]  metric='Adjusted Gross Profit'  period='the third quarter of 2022'
       → fact:derived:absolute-change:opendoor:adjusted-gross-profit:2022Q2-2022Q3:155d76ed704a
  cite …open-20220630.htm#p133 [917:920]      handle ev:…#p133:r10c3
  cite …earningsre.htm#p23 [1033:1036]        handle ev:…#p23:r10c3
```

Zero of those nine values came from the model.

---

## 6. Field-level inventory — `DerivedFact`, the code-owned trusted fact

24 fields, **all** produced by `story/stages/derivation/execute.py` (verified). The ones a
sentence can reach: `result`, `result_word`, `unit`, `currency`, `display_semantics`,
`metric_surfaces`, `period_surface_hint`, `from_period`, `to_period`, `from_value`, `to_value`.
Provenance: `from_fact_id`, `to_fact_id`, `operation`, `comparability_rule_ids`,
`reused_detector_signal`, `tool_version`, `warning_codes`.

`display_semantics` is a `DisplaySemantics` enum whose **values are the English phrases** —
`"decreased by"`, `"lower than"`, `"crossed zero"` — so the direction word a sentence writes is
literally the enum member. `renderings.direction_phrase(derived)` is the single emitter.

`reused_detector_signal` is the field that closes the loop: `execute` cross-checks its own
arithmetic against `candidate.signals` (`delta`, `delta_pct`) and records which signal it
agreed with. **The candidate's `direction` signal is not among the ones cross-checked**
(verified — `DETECTOR_SIGNALS` at `execute.py:78` maps operations to `delta` / `delta_pct`
only).

**Stale reference:** `deterministic.py:1307` says code fills a derived binding's period surface
*"from `period_surface_hint`"*. It does not — `compile.py:_bindings_from` reads
`SlotRow.offers["to_period"]`. The two agree, and a test asserts it
(`test_story_composition.py:351`), so this is a comment that names the wrong route, not a defect.

---

## 7. Field-level inventory — `VerificationFinding`, the verifier's output

13 fields (verified). This matters for `04-REPAIR-LOOP-FEASIBILITY.md`:

| Field | Meaning |
| --- | --- |
| `code` | one of 102 |
| `severity` | `REFUSE` / `WARN` / `ANNOTATE` / `ADVISORY` |
| `blocking` | whether it stops acceptance |
| `sentence_index` | which sentence |
| `char_start` / `char_end` | the exact span |
| `fact_ids` | the facts involved |
| `citation_ids` | the evidence handles involved |
| `expected` | what the check required, in prose |
| `observed` | what the draft said |
| `explanation` | the §-referenced reason |
| `remedy` | one of **11** `Remedy` members |
| `suggested_fact_ids` | a rebinding hint |

Measured distribution of `remedy` over all 85 findings in the 40 stored verification reports:
`REBIND_TO_FACT` 45, `DROP_SENTENCE` 19, `NARROW_METRIC_SURFACE` 9, `ADD_PERIOD_QUALIFIER` 4,
`ADD_PERCENTAGE_POINT_QUALIFIER` 4, `REBUILD_PACKAGE` 3, `RESTATE_AS_CALCULATION` 1.

---

## 8. What the planner is shown, verbatim

The FACTS and DERIVATIONS OFFERED sections of `planner_prompt` for this candidate (verified by
re-rendering):

```text
FACTS
  [obs:adjusted-gross-profit:opendoor:2022Q2:normalized-table:0c4364ebbc44]
      adjusted_gross_profit (Adjusted Gross Profit)  2022Q2  556000000.0 USD
      lane normalized_table  state clean
      cited in passage norm:…open-20220630.htm#p133 quoting "556"
  [obs:adjusted-gross-profit:opendoor:2022Q3:normalized-table:4d66ef7200e9]
      adjusted_gross_profit (Adjusted Gross Profit)  2022Q3  110000000.0 USD
      lane normalized_table  state clean
      cited in passage norm:…q32022formxex991earningsre.htm#p23 quoting "110"

DERIVATIONS OFFERED (3 available; copy a line into requested_derivations exactly as it is
written, or ask for none)
  operation "absolute_change"  from_fact_id "obs:…2022Q2:…0c4364ebbc44"  to_fact_id "obs:…2022Q3:…4d66ef7200e9"
      adjusted_gross_profit 2022Q2 (556000000.0 USD) -> adjusted_gross_profit 2022Q3 (110000000.0 USD)
  operation "percentage_change"  …same pair…
  operation "crossed_zero"       …same pair…
```

**There is no `direction` anywhere in this prompt, and no `delta`.** The planner is given two
raw floats and two period keys and must perform the comparison and the chronology itself.
`planner_prompt` takes `(package, *, offered)` — it never receives the `StoryCandidate`, which
is where `signals` lives (verified, `prompts.py:362`).

---

## 9. Where each authority actually lives, as imports

Enforced by `tests/story/test_story_package_structure.py` (20 tests, all passing):

* `test_no_stage_imports_another_stage` — a stage may import `story.core.*` and nothing else
  from `story/`.
* `test_core_never_imports_a_stage_a_provider_or_the_cli`.
* `test_the_story_package_has_no_import_cycle`.
* `test_the_composition_root_is_the_only_module_outside_providers_that_reaches_the_adapter`.

The practical consequence for this audit: **anything two stages must agree about has to travel
through `story/pipeline.py`.** That is why `offers()` and `slot_table()` are computed in
`run_demo` and passed down. A direction check between planner and facts would have to be built
the same way — which the architecture already supports without change.
