# The deterministic draft compiler — target architecture

**Status:** design, written before implementation. *(2026-08-23)*
**Scope:** the generation boundary in `story/` — planner, writer, and the new stage between the
writer and the verifier. **Not** extraction, **not** the Research Agent, **not** any change to
`story/stages/verification/`'s rules.

---

## 1. The answer, first

The writer stops declaring machine metadata and starts writing **sentence templates with
slots**. A deterministic **draft compiler** fills every slot from a trusted row, records the span
it wrote into, and emits the same `Draft` type the verifier already consumes.

```text
writer answer   "GAAP gross margin was {{D1}} lower than {{D1.from_metric}} in {{D1.period}}."
                        ↓  composition stage — deterministic
rendered text   "GAAP gross margin was 15.9 percentage points lower than adjusted gross margin
                 in the third quarter of 2022."
bindings        FactBinding(fact_id=fact:derived:compare-levels:…, rendered="15.9 percentage
                 points", char_start=22, char_end=44,
                 metric_surface="GAAP gross margin", period_surface="the third quarter of 2022")
citations       PassageCitation(…, evidence_handle="ev:…#p133:r7c1"),  ×2, one per input fact
```

The model still decides *which* fact, *which* slot goes on *which* side of "lower than", *what*
the sentence says and *how it reads*. Code decides what the sentence means mechanically.

**The verifier does not change.** It receives a `Draft` built from the same four types
(`Draft`, `DraftSentence`, `FactBinding`, `PassageCitation`) with the same fields, so all 102
gate codes stay reachable, all ~3,600 story tests keep their subject, and every artifact already
on disk still loads. This is the property that makes the change affordable.

---

## 2. What is actually wrong — measured, not asserted

### 2.1 The corpus

50 `story-v1-*` runs under `data/story_demo/`. Dispositions read from
`demo_manifest.json → demo.disposition`; findings read from `verification_report.json →
checks[].findings[]` *(recounted directly 2026-08-23; the count below was produced twice, by two
independent readers, and agrees to the finding)*.

| Disposition | Runs |
| --- | ---: |
| `accepted` | 13 |
| `rejected` (the §13 verifier refused) | 22 |
| `draft_refused` (§12, before the verifier) | 4 |
| `plan_refused` (§11) | 4 |
| `provider_failed` (transport) | 7 |
| **total** | **50** |

35 runs produced a draft the verifier judged. **13 passed, 22 were refused — 63%.**

### 2.2 The 74 blocking findings

| Code | n | What the model got wrong |
| --- | ---: | --- |
| `unbound_numeral` | 18 | see §2.3 — **15 of the 18 are the literal string `2022`** |
| `citation_reused_for_unrelated_claim` | 15 | a table citation carried forward onto a sentence that binds nothing |
| `comparative_not_supported_by_text` | 12 | mixed: 8 are a genuinely reversed claim, 4 are surface resolution |
| `metric_surface_unresolved` | 5 | e.g. `metric_surface="percent"` — the model put the *unit* in the metric field |
| `metric_surface_ambiguous` | 3 | `"Gross Margin"`, which names two package metrics |
| `binding_rendering_is_not_one_numeral` | 3 | `rendered="divergence between Adjusted Gross Margin and GAAP Gross Margin"` |
| `package_content_digest_mismatch` | 3 | environmental — a stale package replayed |
| `connective_sentence_carries_a_claim` | 2 | |
| `period_named_in_text_contradicts_binding` | 2 | |
| `calculation_result_surface_mismatch` | 2 | legacy `Calculation` path |
| `derived_unit_mismatch` | 2 | `"446000000.0 USD"` written where the prompt printed `$446 million` |
| ten codes at 1 each | 10 | |
| **total** | **74** | |

Non-blocking: `over_precision` ×8, `paraphrase_distance` ×2.

### 2.3 The single decisive measurement

Of the 18 `unbound_numeral` refusals, the `observed` field is:

| observed | n |
| --- | ---: |
| `'2022'` | **15** |
| `'-12.6 percentage points'` | 1 |
| `'556000000.0'` | 1 |
| `'110000000.0'` | 1 |

Fifteen blocking refusals — **20% of every blocking finding in the corpus** — are a four-digit
year inside a period phrase the model wrote correctly, uncovered because
`FactBinding.period_surface` is a string the model had to retype and mistyped, or because a
two-period sentence has two years and one field.

`story/stages/generation/prompts.py:1115 period_surface_for` already computes the exact string.
The prompt already prints `period surface: write exactly "the third quarter of 2022"`. The model
is refused for failing to copy back a string code handed it.

### 2.4 The responsibility leak, stated exactly

Every string in the second column below is **already computed by code** and printed into the
writer's prompt as an instruction to copy.

| Writer field | Who computes the correct value today | Where |
| --- | --- | --- |
| `fact_bindings[].rendered` | `prompts._observed_figure` / `prompts._derived_figure` / `_scaled_money` | `prompts.py:1330`, `:1501`, `:1558` |
| `fact_bindings[].metric_surface` | `prompts.metric_surfaces_for(package, metric_id)` | `prompts.py:1052` |
| `fact_bindings[].period_surface` | `prompts.period_surface_for(fact)` / `DerivedFact.period_surface_hint` | `prompts.py:1115`, `operations.py:388` |
| `citations[].evidence_id` | `PackagedFact.evidence_handle`, minted by `models._evidence_handle` | `models.py:659` |
| the comparison's direction word | `DerivedFact.display_semantics`, closed 12-member enum | `models.py:1667` |

Code already computes all of it. **The writer's job is to retype it into a JSON field.** That is
the leak, and it is the whole of it.

Two things are *already* code-owned and stay that way: `char_start`/`char_end` (located by
`writer._occurrences`, `writer.py:483`) and the citation span (`writer._span_for`, `writer.py:583`).
The change extends an existing direction rather than starting a new one.

### 2.5 A documented claim that is not true today, and that this change makes true

`story/stages/verification/deterministic.py:1307` reads:

> …and code fills the surface from `period_surface_hint`. **The model cannot forget a field it no
> longer writes.**

It does not. `prompts.py:1449` prints an instruction; `writer.py:493` reads the answer straight
back out of the model's JSON:

```python
period_surface=str(declared.get("period_surface") or ""),
```

`period_surface` is model-authored on every path today, derived bindings included. The sentence
in that docstring describes the architecture this document specifies, not the one in the
repository. *(Verified by reading both files, 2026-08-23.)* Recorded here rather than quietly
fixed, because it is the clearest statement of the intended design that already exists — and it
was written as though the work were done.

---

## 3. Current architecture

```text
StoryCandidate ──► EvidenceBuilder ──► StoryEvidencePackage
                                             │
                             offers(package, candidate)   ← deterministic, pipeline.py:631
                                             ▼
                            ┌────────── plan_story ──────────┐   model call 1
                            │  EditorialPlan                 │
                            │   · thesis, key points         │
                            │   · required_fact_ids          │
                            │   · requested_derivations      │
                            └────────────────┬───────────────┘
                                             ▼
                             execute_all(...)  → DerivedFact[]     ← deterministic
                                             ▼
                            ┌────────── write_story ─────────┐   model call 2
                            │  Draft                         │
                            │   · text, kind                 │   ← editorial
                            │   · fact_id                    │   ← selection
                            │   · rendered                   │   ← BOOKKEEPING
                            │   · metric_surface             │   ← BOOKKEEPING
                            │   · period_surface             │   ← BOOKKEEPING
                            │   · citations[].evidence_id    │   ← BOOKKEEPING
                            └────────────────┬───────────────┘
                                             ▼
                              DeterministicVerifier.verify(...)
```

**What the planner owns today** (`story/stages/generation/planner.py`, schema at
`prompts.py:228`): `thesis`, `why_it_matters`, `key_points[]` (claim, `required_fact_ids`,
`required_citation_passage_ids`, `statement_class`), `counterpoints[]`,
`requested_derivations[]` (operation + two fact ids, validated against a code-computed offer
set), `required_warnings`, `uncertainty`, `structure`, `prohibited_claims`,
`unusable_evidence`. `causal_language` is pinned to a single-member enum — the value code
computed.

**What the writer owns today** (schema at `prompts.py:975`): `title`, and per sentence `text`,
`kind`, `fact_bindings[] = {fact_id, rendered, metric_surface, period_surface}`,
`citations[] = {evidence_id}`.

**Split by responsibility:**

| Field | Editorial / semantic | Deterministic bookkeeping |
| --- | :---: | :---: |
| `text` (the words) | ● | |
| `kind` | ● | |
| `fact_bindings[].fact_id` | ● (which fact) | |
| which side of a comparison a metric goes on | ● | |
| `fact_bindings[].rendered` | | ● |
| `fact_bindings[].metric_surface` | | ● |
| `fact_bindings[].period_surface` | | ● |
| `citations[].evidence_id` | | ● |
| `char_start` / `char_end` | | ● *(already code)* |
| citation spans | | ● *(already code)* |

**Deterministic derivation already in place** — seven operations in
`story/stages/derivation/`, each existing only because a verifier rule can re-derive its result:
`absolute_change`, `percentage_change`, `percentage_point_change`, `compare_levels`, `ratio`,
`crossed_zero`, `trend_direction` (the last withdrawn from the offer set). A `DerivedFact`
already carries `operation`, `from_fact_id`/`to_fact_id`, `from_period`/`to_period`,
`from_value`/`to_value`, `result`, `unit`, `display_semantics`, `comparability_rule_ids`,
`period_surface_hint`, `tool_version` and a deterministic readable id. **The derived-fact model
needs no new field for this work.**

---

## 4. Target architecture

```text
StoryEvidencePackage ──► offers ──► planner ──► execute_all ──► DerivedFact[]
                                        │                            │
                                        └──────────┬─────────────────┘
                                                   ▼
                                    ┌──── slot table (deterministic) ────┐
                                    │  F1..Fn  package facts             │
                                    │  D1..Dm  this run's derived facts  │
                                    │  P1..Pk  the writer's passage slice│
                                    │  each row: legal rendering(s),     │
                                    │  metric surface, period surface,   │
                                    │  direction word, evidence handle   │
                                    └──────────────┬─────────────────────┘
                                                   ▼
                            ┌────────── write_story ─────────┐   model call 2
                            │  SentenceTemplate[]            │
                            │   · text with {{slots}}        │   ← wording
                            │   · kind                       │   ← editorial
                            │   · rests_on: passage handles  │   ← selection
                            └────────────────┬───────────────┘
                                             ▼
                            ┌───── compile_draft (new stage) ─────┐
                            │  substitute slots, record spans     │
                            │  mint FactBindings                  │
                            │  resolve citations from handles     │
                            │  → Draft  (unchanged type)          │
                            └────────────────┬────────────────────┘
                                             ▼
                              DeterministicVerifier.verify(...)   ← unchanged
```

### 4.1 The slot table — the reusable foundation

One deterministic function of `(package, derived_facts, passages)`. It assigns short,
prompt-legible handles and states, per row, exactly what a sentence may ask for:

```text
F3   homes_purchased · 2022Q3
       {{F3}}          "8,380"
       {{F3.metric}}   "homes purchased"
       {{F3.period}}   "the third quarter of 2022"
       evidence        ev:norm:…#p88:r4c2                (code cites this; you do not)

D1   absolute_change  homes_purchased  2022Q3 → 2022Q4
       {{D1}}             "4,953 homes"
       {{D1.metric}}      "homes purchased"
       {{D1.period}}      "the fourth quarter of 2022"
       {{D1.from_period}} "the third quarter of 2022"
       {{D1.to_period}}   "the fourth quarter of 2022"
       {{D1.direction}}   "decreased by"
       computed from F3 and F4                            (code cites both; you do not)
```

A row that has no legal metric surface, or no legal period surface, offers **no slot** — and a
template naming a slot the row does not offer is refused by the compiler. Today the prompt says
*"do not write about this fact"* and nothing enforces it.

**This is the surface a Research Agent will later produce.** The agent selects or computes facts;
whatever it selects becomes rows in this table; the planner, writer and compiler below it do not
change. The table is the seam because it is a pure function of trusted rows and knows nothing
about where they came from.

### 4.2 What the writer returns

```json
{
  "title": "Opendoor 2022Q4",
  "sentences": [
    {
      "text": "Homes purchased fell to {{F4}} in {{F4.period}} from {{F3}}.",
      "kind": "reported",
      "rests_on": []
    },
    {
      "text": "That is a fall of {{D1}} from {{D1.from_period}} to {{D1.to_period}}.",
      "kind": "calculated",
      "rests_on": []
    },
    {
      "text": "The filing describes the decline as a deliberate reduction in acquisition pace.",
      "kind": "explanatory",
      "rests_on": ["P2"]
    }
  ]
}
```

Three properties per sentence instead of four, and the two remaining object arrays
(`fact_bindings`, `citations`) are gone. `rests_on` is required-and-possibly-empty because
`story/providers/portable_schema.py` forbids optional properties: `required == properties` on
every object, no `null` type, no `pattern`, no `minItems`.

**The template is the reference list.** There is deliberately no separate `facts_used` array. Two
fields that can disagree about which facts a sentence uses is a state worth making
unrepresentable; the placeholders already say it exactly once. *(This departs from the brief's
sketch, which showed both. The principle — the writer references trusted ids and nothing else —
is kept.)*

### 4.3 What the compiler creates

For each sentence, in one left-to-right pass:

1. **Parse** `{{handle}}` and `{{handle.field}}` occurrences. An unknown handle, an unknown
   field, or a field the row does not offer is a typed refusal — the compiler never guesses.
2. **Substitute** each slot with the deterministic string, accumulating the offset shift so the
   span of every substitution in the *final* text is known exactly. No search, no re-location.
3. **Mint one `FactBinding` per value slot** (`{{F3}}`, `{{D1}}`) with
   * `fact_id` — the row's real id (`obs:…` or `fact:derived:…`),
   * `rendered` — the string the compiler inserted,
   * `char_start`/`char_end` — the recorded span,
   * `metric_surface` — the row's own legal surface,
   * `period_surface` — the row's own legal period surface (for a derived row, `to_period`'s).
   A sentence naming `{{F3.period}}` or `{{F3.metric}}` without also naming `{{F3}}` is refused;
   §4.6 R4 states the rule and the reason.
4. **Resolve citations**, by kind, with no model input:
   * `reported` — one `PassageCitation` per bound observation, from that fact's own
     `evidence_handle`, span resolved through `story.core.table_cells.resolve_cell`;
   * `calculated` — one per **input** fact of each bound derived fact (no handle is ever minted
     for a derived fact);
   * `explanatory` — the passages named in `rests_on`, each resolved to the first handle of a
     fact read from that passage that no earlier sentence has cited;
   * `connective` — none.
5. **Refuse or return.** A `Draft`, or a `CompositionRefused` carrying typed violations.

### 4.4 Contracts

```python
# story/stages/composition/public.py

@dataclass(frozen=True, slots=True)
class SentenceTemplate:
    """One sentence as the model wrote it, before any slot is filled."""
    index: int
    text: str                      # carries {{slot}} placeholders
    kind: SentenceKind
    rests_on: tuple[str, ...] = () # passage handles, explanatory only

@dataclass(frozen=True, slots=True)
class SlotRow:
    """One bindable row and every string a template may ask it for."""
    handle: str                    # "F3", "D1", "P2"
    fact_id: str                   # the real, deterministic id
    kind: SlotKind                 # OBSERVED | DERIVED | PASSAGE
    offers: Mapping[str, str]      # {"": "8,380", "metric": …, "period": …}
    evidence_handles: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class CompiledDraft:
    draft: Draft
    slots: tuple[SlotFill, ...]    # provenance: which slot became which span
```

`SlotFill` records `(sentence_index, handle, field, inserted, char_start, char_end)` and is
written as a separate artifact, `composition.json`. It is **not** added to `DraftSentence`:
`Draft.digestible_payload()` feeds `draft_content_sha256`, and a new field on the type would
re-key every artifact already written for a value nothing verifies.

**A base fact** is a `PackagedFact`, unchanged. **A derived fact** is a `DerivedFact`, unchanged.
Neither type gains a field. What is new is the *table* that says what may legally be written for
each, and the compiler that writes it.

### 4.5 Ownership and dependency rules

| Module | May import | Owns |
| --- | --- | --- |
| `story/core/renderings.py` *(new)* | `story.core.models`, stdlib | the legal rendering of a value, the legal metric surface, the legal period surface — **one implementation, used by the prompt printer and the compiler** |
| `story/core/evidence_slice.py` *(new)* | `story.core.*` | the writer's passage slice; the handle → span resolution |
| `story/stages/composition/` *(new)* | `story.core.*` only | slot table, template parsing, substitution, binding and citation minting |
| `story/stages/generation/` | `story.core.*`, `story.contracts`, `story.providers` | prompts, the two model calls, template parsing rules |
| `story/stages/verification/` | `story.core.*` | **unchanged** |
| `story/pipeline.py` | everything | ordering, and nothing else |

The existing structural rules hold and are already enforced by
`tests/story/test_story_package_structure.py`: no stage imports another stage; `core/` imports no
stage. The compiler is a stage and not a `core` module because it holds a substantial cohesive
concern — parsing, substitution, binding, citation — and because a `core` module that both
renders and refuses would be a second authority inside the shared layer.

`story/core/renderings.py` exists because the alternative is a third checked copy. Today
`prompts._DERIVED_FIGURE_FORMATS` is a test-asserted duplicate of
`verification.derived_facts.DERIVED_SURFACES`, and `operations.period_surface_hint` and
`prompts.period_surface_for` are two emitters that already disagree (`"fiscal year 2022"` vs
`"fiscal 2022"`; ISO instant vs worded instant). Both round-trip through the verifier's grammar,
so nothing is broken — but a compiler that picked one would be a third answer. **They collapse
into one emitter, in core, and verification keeps its independent recogniser.**

### 4.6 Invariants

**R1 — Every numeral a reader sees is either compiler-inserted from a trusted row, or refused.**
The compiler inserts from `legal_renderings(row)`; anything else the model typed is uncovered by
`_covering_spans` and refused as `unbound_numeral`. This is
`plans/llm-agent/DETERMINISTIC_FACT_TOOLS.md` §1's target property, now true of *renderings* and
not only of *arithmetic*.

**R2 — A binding's span is written, not found.** The compiler knows the offset because it did the
substitution. `binding_span_does_not_match_text` becomes unreachable on this path.

**R3 — A slot a row does not offer cannot be filled.** A fact with no unique metric surface, or no
period surface in the closed grammar, offers no such slot; the template naming one is refused
with a named code. The prompt's *"do not write about this fact"* stops being advice.

**R4 — A sentence that names a row's period or metric must also bind its value.** A `{{F3.period}}`
with no `{{F3}}` in the same sentence is refused (`slot_without_binding`).

The reason is the verifier's, not the compiler's. `_covering_spans` reads period surfaces **off
bindings**, so a sentence with no binding earns no coverage and its year is `unbound_numeral`.
The compiler cannot supply a value-less `FactBinding` to carry the surface either: `rendered`
must be exactly one numeral (`binding_rendering_is_not_one_numeral`) and
`FactBinding._span_is_usable` forbids a zero-width span. So the state is already unwritable
today — the recorded connective sentence *"This post stays anchored to the cited table for the
third quarter of 2022."* was refused for exactly this. R4 does not create the limitation; it
moves the refusal to compile time, where it names the rule instead of pointing at a year.
**Recorded as a standing limitation** in the implementation plan's non-goals: a numeral-free
sentence still cannot name a period.

**R5 — Citations are minted per bound fact, never carried forward.** The compiler cannot produce
the "same span, second claim" shape that earned 15 blocking findings, because it does not copy a
previous sentence's citation.

**R6 — The compiler fails closed and loudly.** Where it cannot produce a value — no legal
rendering, no handle for a bound fact, a passage with nothing left to cite — it raises a typed
`CompositionRefused`. It never silently omits a citation, because an omission lands as *nothing*
where a refusal would have landed as `uncited_factual_sentence`.

**R7 — The compiler is not a verifier.** It answers "what does this template mean" and nothing
else. It does not check direction against prose, does not check comparison order, does not check
causal language, superlatives, required warnings or counterpoint survival. Those are §13's, and
`story/stages/composition/` may not import `story/stages/verification/`.

---

## 5. What stays the verifier's, and what becomes structurally unreachable

This section exists because "the verifier is not weakened" is a claim that has to survive being
stated precisely.

### 5.1 Checks that keep their full force — the semantic load

None of these reads `rendered`, `metric_surface`, `period_surface` or `evidence_handle` as its
test input; each reads `sentence.text` and compares it to the trusted row.

`unbound_numeral` · `metric_surface_absent_from_text` · `metric_named_in_text_contradicts_binding`
· `period_surface_absent_from_text` · `period_named_in_text_contradicts_binding` ·
`percent_change_ambiguous` · `percent_change_reported_not_calculated` · the whole §13.10 causal
family · `connective_sentence_carries_a_claim` · `unsupported_superlative` ·
`unsupported_comparative` · `unsupported_absence_claim` · `unsupported_temporal_ordering` ·
`comparative_not_supported_by_text` · `derived_fact_orientation_reversed` (the two prose sites) ·
`derived_direction_not_stated_in_text` · `derived_fact_polarity_contradicted` ·
`forward_looking_language` · `foreign_subject_named` · `unresolved_entity_named` ·
`conflict_not_disclosed` · `required_warning_absent` · `required_counterpoint_absent` · every
REBUILD_PACKAGE code in §13.7.

**The highest-value catch in the system is in this list.** `comparative_not_supported_by_text`
fired 12 times, and 8 of those are one sentence Qwen wrote in eight separate runs:

> *"The Adjusted Gross Margin was 15.9 percentage points lower than the GAAP Gross Margin."*

The number is right, the binding is right, and the claim is the opposite of the truth (3.3% is
15.9 points *higher* than −12.6%). Under the new contract the model still chooses which handle
goes on which side of "lower than", so this sentence is still writable and still refused.

### 5.2 Checks that become structurally unreachable on the compiler path

| Check | Why | Cost |
| --- | --- | --- |
| `metric_surface_unresolved`, `metric_surface_ambiguous`, `mutually_distinct_group_ambiguity`, `metric_binding_mismatch` | the declared surface came from `metric_surfaces_for(package, fact.metric_id)`, which is the function `MetricAliasIndex.resolve` is asked to invert | none — the prose rules are untouched |
| `period_unresolvable`, `period_mismatch`, `period_shape_conflated` | the declared surface was generated from the fact's own endpoints | none — the prose rules are untouched |
| `binding_span_does_not_match_text` | the compiler wrote the span | already vacuous on the writer path today |
| `evidence_cell_span_mismatch`, `citation_not_in_package`, `citation_span_not_in_passage`, `citation_does_not_support_fact` (`citations.py:603`) | the citation was derived from the bound fact's own handle | none |
| `evidence_handle_not_for_fact` | one citation per bound fact, from that fact's handle | **see 5.3** |
| `number_outside_tolerance`, `sign_disagreement`, `unit_mismatch`, `currency_symbol_on_non_monetary_unit` | the numeral was inserted from the row | **see 5.3** |
| `binding_rendering_is_not_one_numeral` | every legal rendering is one `tokenize_numerals` token | none |
| `event_property_bound_as_fact`, `fact_not_in_package`, `derived_fact_not_in_run` | the handle set is the bindable set | none |
| `calculated_sentence_without_calculation`, `calculated_sentence_cites_passage` | citations are compiled from the derived fact's inputs | none |

**Unreachable is not the same as removed.** Every one of these checks stays in the verifier and
stays reachable, because a `Draft` can arrive from three other places: a replayed artifact, a
hand-written test draft, and a future caller. The verifier remains the final authority over a
draft it did not build.

### 5.3 The two places where care is required

**The four §13.1/§13.2 numeral checks.** These are not tautologies today: `rendered` is evidence
because §12 requires it to be a literal substring occurring exactly once in the model's own
prose. If the compiler chose a rendering *without* putting it into the text, all four would go
vacuous and the prose numeral would go unchecked entirely — `_covering_spans` builds coverage
from `(rendered, char_start, char_end)`, so a compiler-supplied span would license whatever bytes
it pointed at.

The compiler does not choose a rendering for text it did not write. It **inserts** the string and
records the span it inserted into, so `text[char_start:char_end] == rendered` holds because the
substitution produced both. The reader-facing property survives intact, and the residual risk
narrows from "a 9B model retyped a number" to "the compiler has a bug" — which is what
`test_no_compiled_draft_can_violate_the_numeral_checks` exists to hold, by driving every
compiled fixture through `DeterministicVerifier` and asserting an empty finding list for those
four codes.

**`evidence_handle_not_for_fact` is the check `citations.py` calls the one that carries the
load.** Its measured strength: over 724 ordered pairs of handle-carrying facts, citing both
handles raises it 0 times and citing one raises it 724/724. Making the compiler the sole author
of handles means a compiler bug produces *zero findings* rather than a refusal. R6 is the answer
— the compiler raises where it cannot produce a handle — and the second answer is that check 7
keeps running and keeps its teeth for every replayed draft.

**One reporting regression, named as a decision rather than discovered later.** Refusals that
used to arrive as `VerifiedDraft.findings` (visible in the rejection panel, catalogued in
`demo_ui/code_catalogue.py`) now arrive as composition violations before the verifier runs. The
composition codes are therefore added to the catalogue in the same change, and `composition.json`
is written as a run artifact, so a refused draft is no less diagnosable than it is today.

### 5.4 Two suppression channels this change closes

Both are model-controlled today and become trusted:

* `deterministic.py:364-370` builds a **draft-wide** `period_surfaces` tuple from every binding's
  model-authored `period_surface`, and `claims.check_language` exempts occurrences of those
  phrases from `unsupported_superlative`, `unsupported_comparative` and
  `unsupported_temporal_ordering`. `SUPERLATIVE_TERMS` holds `"first"` and `"last"`;
  `TEMPORAL_TERMS` holds `"before"` and `"after"`. A model that declared a period surface
  containing one of those words suppressed a REFUSE code across the whole draft. Compiler-authored
  surfaces make the exemption derive from the trusted row.
* `citations.py:846` feeds `binding.metric_surface` and `binding.period_surface` into
  `paraphrase_distance`'s allowlist. Same change, on a WARN.

Neither is a rule this work relaxes. Both are strictly tightened.

---

## 6. What the writer still owns, and why

| Owned | Why it may not move to code |
| --- | --- |
| the words, the order, the rhythm | it is the product |
| `kind` | the sentence's own claim about what it is doing; deriving it from bindings would make `connective_sentence_carries_a_claim` unreachable, and that check fired twice in the corpus |
| which fact to state | editorial selection — and the Research Agent's future job, one layer up |
| which handle goes on which side of a comparison | **the semantic claim itself**; §5.1's highest-value refusal depends on the model being able to get it wrong |
| which passage an `explanatory` sentence rests on | a judgment about what supports a claim, not a lookup |
| the title | prose; already checked by `claims.py:815` |
| any numeral typed outside a slot | not owned so much as *permitted and refused* — R1 |

---

## 7. How this prepares for the Research Agent

```text
Research Agent ──► selected / computed facts ──► slot table ──► planner ──► writer ──► compiler ──► verifier
                                                     ▲
                                            the seam that does not move
```

The slot table is a pure function of `(facts, derived facts, passages)`. It does not know whether
those rows came from a bounded package builder or from an agent's tool loop. Three consequences:

1. **The writer contract is agent-independent.** Handles are assigned positionally from whatever
   rows exist. An agent that adds a fact adds a row; no prompt rule, schema field or compiler
   branch changes.
2. **The verifier boundary is already the right one.** The verifier's inputs are
   `(draft, package, plan, derived_facts)` and it re-derives everything it checks. An agent that
   selects facts changes which rows are in the package; it does not change what a check means.
3. **The trust boundary is stated in one place.** A row in the slot table is a row code is willing
   to write into a sentence. When an agent proposes facts, the question "may this be written?"
   has one answer site rather than being spread across a prompt, a writer and a verifier.

What this work deliberately does **not** do: no tool-calling loop, no retrieval from the writer or
compiler, no widening of what the model may see. `writer_passages` still takes no plan argument,
so one model still cannot filter the next model's universe.

---

## 8. Rejected options

| Option | Why not |
| --- | --- |
| Relax the verifier's surface checks | The checks are correct. The 15 `2022` refusals are the verifier doing its job against a contract that made the failure inevitable. |
| Keep the four-field binding and repair `rendered` post-hoc | A repair that edits the model's text is a second author of prose; a repair that edits only the declaration re-opens the gap between what the reader sees and what was checked. |
| Have the writer emit `facts_used[]` **and** a template | Two fields that can disagree. The placeholder set already says it once. |
| Put the compiler in `story/core/` | It refuses, and a shared module that refuses is a second authority inside the layer every stage imports. |
| Let the compiler also check direction, comparison order and causal language | That is a second verifier that can disagree with the first. R7. |
| Emit full fact ids in templates instead of `F1`/`D1` handles | `fact:derived:compare-levels:opendoor:adjusted-gross-margin-gaap-gross-margin:2022Q3:5f8f78fad963` inside a sentence, retyped by a 9B model. The handle is resolved to the real id by code and the real id is what the artifact stores. |
| Derive `kind` from the bindings | Makes `connective_sentence_carries_a_claim` unreachable — a check that fired twice on real drafts. |
| Change the planner's contract in this work | Everything the planner returns is already editorial selection, and its one machine field (`requested_derivations`) is already validated against a code-computed offer set. No leak to close. |

---

## 9. Open decisions, with a recommendation

1. **Does `{{D1.direction}}` ship?** *Recommended: yes.* `DisplaySemantics` values are already the
   English phrases, and offering the slot removes the last direction word the model has to
   reconstruct. It does not weaken §13.14 or `_direction_findings`, because the model still
   chooses whether to use the slot and which metric sits on which side.
2. **Are the three committed replay fixtures re-recorded live, or hand-authored?**
   *Recommended: hand-authored, labelled synthetic.* A live re-record needs the llama.cpp server
   and the OpenAI key; the repository already carries `generations_rejected_synthetic.jsonl` as
   precedent for a hand-built row, and a synthetic row is honest as long as it says so. A live
   re-record should follow when the server is available, and the plan says so.
3. **Does the legacy `draft_from` parser stay?** *Recommended: yes, for one release*, as the path
   the 50 recorded `draft.json` artifacts and the pre-3.0.0 stores replay through, with a test
   that they still parse. Deleting it would make the A/B corpus unbuildable.
