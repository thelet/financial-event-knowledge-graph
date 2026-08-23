# The deterministic draft compiler — implementation plan

**Companion to** `01-TARGET-ARCHITECTURE.md`, which is the source of truth for contracts,
invariants and ownership. This document is the order of work.

**Baseline, measured before any change** *(2026-08-23)*: `pytest tests/story` →
**3,604 passed, 1 skipped** in 146 s. Every stage below ends with that suite green.

---

## 1. Stages

Eight commits. Stages 1–3 add code without changing behaviour; stage 4 is the contract switch;
stages 5–8 are wiring, evidence and cleanup. Each names its owner file so two workers never
touch one module.

### S1 — `story/core/renderings.py`: one emitter, in core

**Why first:** the compiler and the prompt printer must produce the *same* string. Today the
producers are private functions inside a stage, and two of them already disagree.

| Moves from | to `story/core/renderings.py` |
| --- | --- |
| `prompts.metric_surfaces_for(package, metric_id)` | `metric_surfaces(package, metric_id)` |
| `prompts.period_surface_for(fact)` | folded into `period_surface(...)` |
| `derivation/operations.period_surface_hint(period)` | folded into `period_surface(...)` |
| `prompts._observed_figure`, `prompts._derived_figure`, `prompts._scaled_money`, `prompts._DERIVED_FIGURE_FORMATS`, `prompts._SCALE_WORDS`, `prompts._DIRECTIONAL_SEMANTICS` | `legal_renderings(row, package) -> tuple[str, ...]` |

New public surface: `legal_renderings`, `metric_surfaces`, `period_surface`,
`direction_phrase(derived)`.

`story/stages/generation/__init__.py` keeps re-exporting `metric_surfaces_for` and
`period_surface_for` as thin aliases, so no existing import breaks.

**The two emitters disagree and must be collapsed** *(audited 2026-08-23)*:
`operations.period_surface_hint` writes `"fiscal year 2022"` and an ISO instant `"2022-09-30"`;
`prompts.period_surface_for` writes `"fiscal 2022"` and `"September 30, 2022"`. Both round-trip
through `verification/period_grammar.resolve`, so either is legal. **Pick `prompts`' worded
forms** — a sentence carries `"September 30, 2022"`, not an ISO date — and record that this
changes `DerivedFact.period_surface_hint` for fiscal-year and instant rows.

*(Corrected after running it, 2026-08-23: it changes **three** shapes, not two. YTD_6M also
moves, `"the six months ended June 30, 2022"` → `"the first half of 2022"`. Quarters and YTD_9M
are byte-identical, which is every shape any recorded run has produced — landed in `4508c60`.)*

* Files: `story/core/renderings.py` (new), `story/stages/generation/prompts.py`,
  `story/stages/generation/__init__.py`, `story/stages/derivation/operations.py`,
  `story/stages/derivation/execute.py`.
* Tests: move `test_story_writer.py`'s surface tests to a new `tests/story/test_story_renderings.py`;
  keep the existing tests passing through the aliases. Add
  `test_every_rendering_core_emits_is_one_the_verifier_admits` — drive every `legal_renderings`
  output through `numerals.tokenize_numerals` and `derived_facts.DERIVED_SURFACES`, replacing the
  checked-copy test with a checked-agreement test.
* **Proof required, not asserted:** the demo candidate's writer prompt and planner prompt must
  hash identically before and after (they are quarters only, so no emitter row moves). Run it and
  paste the two digests into the commit message. If a digest moves, the replay stores re-key and
  the change is no longer behaviour-free — stop and say so.

### S2 — `story/core/evidence_slice.py`: the slice and the handle → span resolution

| Moves from | to `story/core/evidence_slice.py` |
| --- | --- |
| `writer.writer_passages(package)` | `passages_backing_facts(package)` |
| `writer._span_for(...)` | `span_for_handle(fact, passage) -> Span \| HandleUnresolved` |

`writer.writer_passages` stays as an alias (it is in `story.stages.generation.__all__` and is
named in a §10.2.1 test). `writer._citations_from` keeps its violation vocabulary and calls the
core resolver.

* Files: `story/core/evidence_slice.py` (new), `story/stages/generation/writer.py`.
* Tests: `tests/story/test_story_evidence_handles.py` gains the core-level cases; the writer's
  existing citation tests are unchanged and must stay green.

### S3 — `story/stages/composition/`: the compiler, with no caller

```text
story/stages/composition/
    __init__.py     the stage's exports
    public.py       SentenceTemplate, SlotRow, SlotKind, SlotFill, CompiledDraft,
                    CompositionRefused, CompositionViolation, the refusal codes
    slot_table.py   slot_table(package, derived_facts, passages) -> tuple[SlotRow, ...]
    compile.py      compile_draft(templates, package, plan, derived_facts, passages) -> CompiledDraft
```

Imports `story.core.*` and nothing else. Enforced automatically by
`tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` — `stage_of`
derives the stage name from the module path (`:383`), so a new package under `story/stages/` is
covered the moment it exists, with no list to update.

Refusal codes (all new, all named for the *template's* failure, never for a §13 code):
`unknown_slot_handle`, `unknown_slot_field`, `field_not_offered_by_row`, `slot_without_binding`,
`no_legal_rendering`, `no_evidence_handle_for_bound_fact`, `passage_handle_unknown`,
`rests_on_without_explanatory_kind`, `template_not_compilable`.

* Tests: `tests/story/test_story_composition.py` (new). Substitution offsets under multi-byte
  characters and repeated slots; every refusal code reachable; `SlotRow.offers` omits a field the
  row has no legal value for; the compiled `Draft` satisfies `FactBinding` construction.
* **This stage is written against hand-built templates only.** No prompt change, no pipeline
  change, suite still 3,604.

### S4 — the contract switch: writer schema and prompt, `WRITER_PROMPT_VERSION = "3.0.0"`

The single hotspot. **One owner for `prompts.py` and `writer.py` together** — the rule numbering,
the rule→code map and the schema move as one thing or not at all.

* `prompts.writer_schema()` → `{title, sentences[{text, kind, rests_on}]}`.
* `WRITER_SYSTEM` rewritten: the rules about copying `rendered`, `metric_surface`,
  `period_surface` and `evidence_id` (today's 3, 4, 5, 6, 7) collapse into two — *"write a slot,
  not a number"* and *"code cites for you"*. Expect the count to fall from 17 to ~13.
* `_writer_fact_lines` / `_derived_fact_lines` print **handles and the slots each row offers**,
  from `slot_table`, instead of *"write one of …"* instructions.
* `writer.draft_from` splits: `templates_from(content, plan) -> tuple[SentenceTemplate, ...]`
  keeps the schema re-check, the `no_sentences` and `thesis_abandoned` rules (the latter now
  computed over the template's slot handles), and the `plan_names_another_package` guard.
  `write_story` returns `WrittenStory(templates=…, generation=…)`.
* `draft_from`, `draft_violations` and their violation codes **stay**, unused by the pipeline, as
  the parser for the 50 recorded `draft.json` artifacts and the pre-3.0.0 stores. A test asserts
  they still parse the committed fixtures.

**Coupled files that must move in the same commit** — each is pinned by a test that will fail
loudly and correctly:

| File | What breaks |
| --- | --- |
| `story/demo_ui/prompt_presets.py:265` `_WRITER_RULE_CODES` | keyed by writer rule *number* |
| `tests/story/test_demo_ui_prompts.py:248` | `len(WRITER_RULES) == 17`, numbers `1..17` |
| `tests/story/test_demo_ui_prompts.py:204` | `numbered_rules(system_text)[:18] == numbered_rules(WRITER_SYSTEM)` |
| `tests/story/test_story_writer.py:737` | `set(sentence_item["required"]) == {"text","kind","fact_bindings","citations"}` |
| `tests/story/test_story_ontology_facts.py:468` | `WRITER_PROMPT_VERSION == "2.2.0"` literal |

### S5 — pipeline wiring

`story/pipeline.py::run_demo`, between `write_story` (L685) and `DeterministicVerifier.verify`
(L701):

```python
written  = write_story(package, plan, provider=provider, derived_facts=derived, …)
compiled = compile_draft(written.templates, package, plan,
                         derived_facts=derived, passages=passages_backing_facts(package))
verified = DeterministicVerifier(…).verify(compiled.draft, package, plan, derived_facts=derived)
```

* New disposition `COMPOSITION_REFUSED`, beside `DRAFT_REFUSED`, written into
  `rejected.json` with `stage: "draft_compiler"` and the typed codes.
* New artifact `composition.json` — the `SlotFill` provenance — added to `_write_run` and to the
  manifest's `artifacts` map.
* `schema_digests_for` picks up the new writer schema automatically; `_mint_run_id`'s
  `prompt_version` string carries `writer=3.0.0`. **Every `story_run_id` changes.** That is
  correct and must be said in the commit message.
* Files: `story/pipeline.py` only.

### S6 — demo UI

* Trace: one new stage between `drafting` and `verifying` — `compiling_draft` — composed from the
  existing closed `counts`/`status` vocabulary so
  `test_demo_ui_trace.py::test_no_field_accepts_free_text` keeps holding.
* `demo_ui/code_catalogue.py`: the nine composition codes, so a compile-time refusal is as
  diagnosable in the panel as a §13 one. This is the answer to the reporting regression named in
  the architecture document §5.3.
* `static/app.js` `renderDraft` (L3033-3038): the binding chip already reads
  `binding.rendered`, `binding.metric_surface`, `binding.period_surface` — **all three still
  exist and still carry the same meaning**, so the chip does not change. Add the template text
  under the sentence, read from `composition.json`, so a reader can see what the model wrote.
* Files: `story/demo_ui/api.py`, `story/demo_ui/code_catalogue.py`,
  `story/demo_ui/static/app.js`. Owner must not be the S4 owner's file set.

### S7 — the regression corpus and the A/B

`tests/story/fixtures/rejected_corpus/` — built from the 22 rejected runs' committed
`draft.json` + `verification_report.json`, one JSON case per recorded failure:

```json
{ "run": "story-v1-9911b4d86ec3", "code": "metric_surface_unresolved",
  "prose": "Opendoor reported a GAAP Gross Margin of -12.6 percent for the third quarter of 2022.",
  "template": "Opendoor reported a {{F2.metric}} of {{F2}} for {{F2.period}}.",
  "expect": "accepted" }
```

`test_story_composition_regression.py` asserts, per case:

1. the intended prose is representable as a template (assemble it and compare to `prose`);
2. the compiler builds the metadata with no model input;
3. `DeterministicVerifier` returns the expected disposition;
4. the paired **semantic** case is still refused with the same code.

The eight cases that must stay refused, and the reason each is not bookkeeping:

| Case | Recorded in | Must still refuse |
| --- | --- | --- |
| the reversed comparative, `"Adjusted Gross Margin was 15.9 percentage points lower than the GAAP Gross Margin"` | 8 runs | `comparative_not_supported_by_text` |
| `"−12.6 percentage points lower"` where the gap is 15.9 | `story-v1-01dcfff9e128` | a numeral not from a slot → `unbound_numeral` |
| a derived id bound to a level's span | `story-v1-3a0e9609dfa5` | not representable: the slot inserts the derived value |
| a superlative, an absence claim, a causal claim, a forward-looking claim | `tests/story/test_story_deterministic_verifier.py` attacks | unchanged codes |

**The A/B, reported as a number.** For each of the 22 rejected runs: replay the stored draft
through the verifier (the *old* boundary, already recorded) and compile the same intended prose
through the *new* boundary. Report the table:

```text
run                     old codes                              new disposition
story-v1-9911b4d86ec3   metric_surface_unresolved ×3           accepted
story-v1-5bf86e6fd2aa   unbound_numeral ×2                     accepted
story-v1-d8bf3d1b19bf   comparative_not_supported_by_text      REFUSED  (correctly)
…
```

The number to publish is *how many previous refusals disappear because the bookkeeping is now
code-owned* — and, equally, how many do not.

### S8 — fixtures, digests, docs

* Re-author the writer row of the three committed stores. The writer key moves because `schema`
  and `prompt` are both `request_identity` digest inputs (`generation_store.py:334-352`);
  `prompt_version` is deliberately **not** one, so bumping the version alone changes nothing.
  Hand-authored and labelled synthetic, with the reason, following the
  `generations_rejected_synthetic.jsonl` precedent. A live re-record is a follow-up, not a
  blocker.
  * `tests/story/fixtures/story_demo/local_openai_compatible/generations.jsonl`
  * `tests/story/fixtures/story_demo/local_openai_compatible/generations_rejected_synthetic.jsonl`
  * `tests/story/fixtures/story_demo/openai/generations.jsonl`
* Update the three golden hash tables in `tests/story/test_story_demo.py`:
  `ARTIFACTS_OF_THE_LIVE_RECORDING` (L913), `ARTIFACTS_OF_THE_SYNTHETIC_STORES` (L935),
  `COMMITTED_CONTENT_DIGESTS` (L1006). **Recompute, never hand-edit to make a test pass.**
* Correct `deterministic.py:1307`'s docstring — the sentence it makes true.
* Sweep for stale references: `plans/llm-agent/V1_STORY_AGENT.md` §12,
  `plans/llm-agent/DETERMINISTIC_FACT_TOOLS.md` §5,
  `docs/2026-08-13-agent-graph-system-review/02-STORY-AGENT-AND-MODEL-PIPELINE.md`.

---

## 2. Migration path from the existing writer schema

| Concern | Answer |
| --- | --- |
| `Draft` / `DraftSentence` / `FactBinding` / `PassageCitation` types | **unchanged.** No field added, none removed. |
| `draft_content_sha256` | unchanged inputs, so a draft with the same sentences and bindings hashes the same. The `SlotFill` provenance goes in a separate artifact for exactly this reason. |
| the 50 recorded `data/story_demo/*/draft.json` | still load, still parse through the retained `draft_from`. |
| the demo UI | reads `binding.{fact_id,rendered,metric_surface,period_surface}` and `citation.{evidence_handle,passage_id,char_start,char_end}` — all still present and unchanged in meaning. |
| the verifier | receives the same type from the same call site. Zero rule changes. |
| the three replay fixtures | **re-authored.** The writer row's key moves; the planner row's does not, provided `PLANNER_SYSTEM`, `planner_schema()` and `planner_prompt()` are untouched — and they are. |
| `story_run_id` | changes for every run, because `prompt_version` and `schema_digests` are inputs. Expected. |

---

## 3. Test changes

| File | Change |
| --- | --- |
| `tests/story/test_story_renderings.py` | **new** — the core emitter, its round-trip through the verifier's recognisers |
| `tests/story/test_story_composition.py` | **new** — slot table, substitution, every refusal code |
| `tests/story/test_story_composition_regression.py` | **new** — the corpus and the A/B of §S7 |
| `tests/story/test_story_writer.py` (86 tests) | schema-shape tests rewritten; the citation tests survive unchanged; binding-violation tests move to composition where the rule moved |
| `tests/story/test_story_deterministic_verifier.py` (136 tests) | **no assertion changes.** Its 8 `FactBinding(`, 23 `DraftSentence(` and 17 `Calculation(` sites are hand-built drafts, which is exactly the path that must keep working. |
| `tests/story/test_story_verification_derived.py` (86 tests) | same — unchanged |
| `tests/story/conftest.py` | `make_draft` unchanged; add `make_templates` |
| `tests/story/test_demo_ui_prompts.py` | rule count and rule→code map |
| `tests/story/test_story_ontology_facts.py:468` | version literal |
| `tests/story/test_story_demo.py` | three golden hash tables |
| `tests/story/test_story_package_structure.py` | no change needed — `stage_of` derives stage names from paths, and `STORY_MODULES` is a glob |

**One new architectural test, and it is the one that holds §5.3 of the architecture document:**
`test_no_compiled_draft_can_violate_the_numeral_or_surface_checks` — every fixture template
compiled and driven through `DeterministicVerifier`, asserting an empty finding list for the
eleven codes the architecture claims become structurally unreachable. A tautology asserted is a
tautology that stays true.

---

## 4. Success criteria

1. `pytest tests/story` green at every stage; ≥ 3,604 passing at the end.
2. The writer's schema carries **three** properties per sentence and no object array.
3. `rendered`, `metric_surface`, `period_surface` and every citation are compiler-authored on the
   pipeline path, proved by a test that greps the writer's schema for those names.
4. The A/B table of §S7 is published with real run ids and real codes.
5. The reversed comparative is still refused, and the four §13.1/§13.2 numeral checks are still
   present, still reachable, and still fire on a hand-built draft.
6. The demo candidate runs end to end offline from the re-authored fixtures and writes
   `post.md`.

---

## 5. Non-goals, stated so they are not attempted

* **No Research Agent, no tool-calling loop, no retrieval from the writer or the compiler.**
* **No extraction change.**
* **No verifier rule change.** `story/stages/verification/` sees one docstring correction and
  nothing else. If a check needs to change to make a stage pass, the stage is wrong.
* **No planner contract change.** Its output is already editorial selection.
* **No new derivation operation.** Seven, and the eighth is still not this work.
* **No redesign of anything else in `story/`** — ranking, detection, packaging, freshness,
  retrieval and the demo UI's non-draft panels are untouched.
* **A numeral-free sentence still cannot name a period.** R4 in the architecture document. The
  limitation is the verifier's coverage mechanism, it predates this work, and closing it would be
  a verifier change.
* **Percent-versus-percentage-point remains unrepresentable rather than checked**, as
  `DerivationOperation` already makes it. Nothing here re-opens it.

---

## 6. Risks

| Risk | Mitigation |
| --- | --- |
| The prompt grows a slot grammar a 9B model cannot follow | The grammar is two forms, `{{H}}` and `{{H.field}}`, and every legal slot is printed under its row. If a live run shows it cannot, that is a measurement to record — not a reason to hand a field back to the model. |
| S1's emitter collapse silently moves a prompt digest | Hash the demo prompts before and after and paste both. Stop if they move. |
| Hand-authored fixture rows drift from what a real model would emit | Labelled synthetic; a live re-record is scheduled as follow-up; the A/B corpus is built from *recorded* drafts, not from synthetic ones. |
| The compiler becomes a second verifier by accretion | R7, plus the structural test that it may not import `story/stages/verification/`. |
| Two workers edit `prompts.py` | S4 owns `prompts.py` and `writer.py`; S6 owns the demo UI; S1 touches `prompts.py` first and lands before S4 starts. |
