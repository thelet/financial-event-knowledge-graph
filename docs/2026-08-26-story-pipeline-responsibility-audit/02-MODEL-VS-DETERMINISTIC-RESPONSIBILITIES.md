# 02 — Model vs deterministic responsibilities

*Classification of every responsibility in the pipeline, with its current owner read from the
code. Measurements 2026-08-26 against commit `fbac777` (verified unless marked otherwise).*

---

## 1. The question this document answers

> Which fields are currently model-authored even though the program already has enough
> information to construct them deterministically?

**Short answer: four, and one of them is not a field at all.**

1. `EditorialPlan.key_points[].required_citation_passage_ids` — a lookup from the fact ids.
2. `EditorialPlan.key_points[].statement_class` — determined by which ids the point names.
3. `EditorialPlan.requested_derivations[]` — copied verbatim from a code-generated list, then
   checked by string equality.
4. `DraftSentence.kind` — determined by which slot rows the template names.

And the one that is not a field: **the direction of the metric move.** No plan field carries it;
the model expresses it in `thesis` prose, code computed it before the call, and the two are
never compared.

Everything else the model emits is either genuinely editorial or already code-owned.

---

## 2. A–E classification with current owner

### A. Source truth — reported metric value, filing passage, period, unit

| Responsibility | Current owner | Deterministic in principle? | Evidence |
| --- | --- | --- | --- |
| Metric value | **graph → package** | already | `PackagedFact.value`, `unit`, `currency`, `scale`, `printed_form` |
| The filing passage text | **graph → package** | already | `PackagedPassage.text`, char-bounded, `excerpted` flagged |
| Period identity | **graph → package** | already | `period_key`, `period_start`, `period_end`, `instant_date`, `shape` |
| Which passage evidences which fact | **package** | already | `PackagedFact.passage_id`, `evidence_handle`, `cell` |
| The evidence handle → span | **code** | already | `core/evidence_slice.py:span_for_handle`, 4 named failure reasons |
| Metric surface (display name) | **code** | already | `core/renderings.py:metric_surfaces(package, metric_id)` |
| Period surface (display phrase) | **code** | already | `core/renderings.py:period_surface*` — one emitter since 2026-08-23 |
| Figure surface (`$556 million`) | **code** | already | `renderings.observed_figure` / `derived_figure` / `scaled_money` |

**Nothing in category A is model-authored.** This was true before the 3.0.0 change for the
values, and became true for the *surfaces* with it.

### B. Deterministic derivation — change, percentage, direction, period order, formulas

| Responsibility | Current owner | Deterministic in principle? | Evidence |
| --- | --- | --- | --- |
| Which derivations are legal for this package | **code** | already | `derivation/offers.py:167`, capped, orientation-sensitive |
| Absolute / percentage / percentage-point change | **code** | already | `derivation/operations.py:124,153,180` |
| `compare_levels`, `ratio`, `crossed_zero`, `trend_direction` | **code** | already | `operations.py:202,239,264,291` |
| Direction of a change | **code** | already | `detector_config.quantity_direction(metric_id, delta)` — reads the metric's sign convention, so it is **not** derivable from the number alone |
| Direction *word* in prose | **code** | already | `DisplaySemantics` enum values *are* the phrases; `renderings.direction_phrase` |
| Period ordering | **code** | already | `core/periods.py:months_between`, `story_period`, `classify_shape` |
| Comparability of two readings (R1–R10) | **code** | already | `core/series.py:comparable()`, 10 rules, ids recorded on the `DerivedFact` |
| Formula-version validity for a period | **code** | already | `PackagedFormulaWindow`; §13 `formula_version_not_valid_for_period` |
| Presentation tolerance | **code** | already | `series.presentation_tolerance(unit, scale)` |
| **Which derivations to ask for** | **planner (model)** | **partly** | Constrained to the offered list; *how many* and *which* is editorial |
| **Whether the thesis agrees with the direction** | **nobody** | **yes** | §4 below |

### C. Provenance and bookkeeping — ids, bindings, citations, offsets, handles

| Responsibility | Current owner | Deterministic in principle? | Evidence |
| --- | --- | --- | --- |
| Fact ids (`obs:`, `fact:derived:`) | **code** | already | `core/keys.py`; deterministic digests, never UUIDs |
| Evidence handles (`ev:…:r10c3`) | **code** | already | minted by packaging, resolved by `evidence_slice` |
| Slot handles (`F1`, `D1`, `P1`) | **code** | already | `slot_table.py:79`, positional, gapless, one-based |
| `FactBinding` (all 6 fields) | **code** | already | `compile.py:_bindings_from` |
| `PassageCitation` (all 6 fields) | **code** | already | `compile.py:_citations_for` |
| `Calculation` (all 6 fields) | **code** | already | `compile.py` from the `DerivedFact` |
| Character offsets | **code** | already | recorded during substitution as `SlotFill` |
| Which passage an `explanatory` sentence cites | **code** | already | `compile.py:_unused_handle` — **and this collides with §13.7, see §6** |
| Sentence index | **code** | already | positional; `SentenceTemplate.index` docstring says why the model may not number its own |
| **Which passage handles a paraphrase rests on** | **writer (model)** | **partly** | the *set* is the `P` rows; the *choice* is a claim about what the sentence paraphrases |
| **`required_citation_passage_ids` on a plan** | **planner (model)** | **yes** | `_referenced_passage_ids` already computes passage-from-fact |

**Category C is essentially fully code-owned as of 2026-08-23.** The two model-authored
survivors are `rests_on` and the plan's passage ids.

### D. Editorial reasoning — what is interesting, thesis, why it matters, counterpoint, uncertainty

| Responsibility | Current owner | Deterministic in principle? | Evidence |
| --- | --- | --- | --- |
| Which candidate is worth a post | **code** | already | `ranking/scoring.py`, `candidate_ranking.py` — a scored, explained list |
| Thesis | **planner** | **no** | `EditorialPlan.thesis` |
| Why it matters | **planner** | **no** | |
| Which facts to emphasise | **planner** | **no** — but the admissible set is code-bounded | `required_fact_ids` ⊆ `package.facts` |
| Counterpoint | **planner** | **no** — but must rest on `counter_evidence` | `COUNTERPOINT_UNGROUNDED` |
| Uncertainty | **planner** | **no** | free prose, unvalidated |
| Prohibited claims | **planner** | **no** | free prose, unvalidated |
| Whether causal language is allowed | **code** | already | `causal_language_for(package)`, pinned into a 1-member enum |
| Which warnings must be stated | **code bounds it, model picks** | **yes** | `claim_qualifying_warnings` already filters build-provenance out |
| **Which side of a comparing word each figure goes on** | **writer** | **no, irreducibly** | rule 10 says so; `comparative_not_supported_by_text` is the guard |

### E. Prose generation — wording, order, tone, concision

| Responsibility | Current owner | Deterministic? | Evidence |
| --- | --- | --- | --- |
| Sentence wording | **writer** | no | `sentences[].text` outside slots is copied through untouched (`_template_from` docstring) |
| Sentence order | **writer** | no | positional index assigned by code, order chosen by the model |
| Title | **writer** | no | §13.15 forbids numerals and claims in it |
| Tone / voice | **style profile (code)** | already | `PLAIN_INVESTOR_STYLE`, a separate system-prompt section that *"may never change a figure"* |
| Length | **config** | already | `length_target` in `config/story.yaml`, a `config_hash` input |

---

## 3. Writer contract complexity, measured

All figures rendered from the real `metric_move` package on 2026-08-26 (verified):

| Measure | Value |
| --- | --- |
| System prompt | 6,283 chars / 42 lines / ~1,570 tokens (`WRITER_SYSTEM` alone is 5,939; the style profile adds the rest) |
| User prompt | 11,179 chars / 120 lines / ~2,794 tokens |
| **Total request** | **~4,364 tokens against a `context_tokens: 8192` server** |
| Numbered rules in the system prompt | **14** (plus a worked example added 2026-08-23) |
| Output schema leaf fields | **4** (down from 15 at the 1.1.0 peak — full table in `01-PIPELINE-CONTRACTS.md` §4) |
| Required properties | 2 top-level (`title`, `sentences`); 3 per sentence (`text`, `kind`, `rests_on`) |
| Prompt sections the model must read | **12** (CANDIDATE, PACKAGE, SUBJECT, COMPANY IDENTITY, PLAN, REQUIRED WARNINGS, FACTS, DERIVED FACTS, EVIDENCE SCOPE, METRIC SEMANTICS, COMPARISON RULES, PASSAGES) |
| Identifier namespaces it must understand | **1** — `F`/`D`/`P` handles. It reproduces **no** `obs:`, `fact:`, `norm:` or `ev:` id |
| Identifiers it must reproduce exactly | **handles only**, and only inside `{{ }}` or `rests_on` |

**The cross-field semantic rules.** The brief asks specifically whether the writer must
understand rules like *"only explanatory sentences may use `rests_on`"*. It must. The exact
implementations:

| Rule | Where enforced | Code |
| --- | --- | --- |
| Only an `explanatory` sentence may carry `rests_on` | writer gate | `writer.py:_template_from` → `RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE` |
| …and again in the compiler | compiler | `compile.py:_citations_for` → `RESTS_ON_WITHOUT_EXPLANATORY_KIND` |
| `rests_on` holds only `P` handles | writer gate | `RESTS_ON_NOT_A_PASSAGE_HANDLE` |
| …and again in the compiler | compiler | `PASSAGE_HANDLE_UNKNOWN` |
| A `{{F3.period}}` needs a `{{F3}}` in the same sentence | compiler | `SLOT_WITHOUT_BINDING` (R4) |
| A row offers only some fields | compiler | `FIELD_NOT_OFFERED_BY_ROW` (R3) |
| `{{P2}}` is not a slot | compiler | `FIELD_NOT_OFFERED_BY_ROW` — a passage row has no value slot |
| A word-valued derived row has no numeral | compiler | `NO_LEGAL_RENDERING` |
| A `reported` sentence must name its metric in prose | verifier | §13.5 `metric_surface_absent_from_text` |
| A `calculated` sentence must carry a `Calculation` | verifier | §13.9 |
| A `connective` sentence must carry no claim | verifier | §13 `connective_sentence_carries_a_claim` |

### Does this complexity exist because the model must decide it, or because the verifier needed metadata?

**Both, and the two are now cleanly separated — which is the change of 2026-08-23.**

* The **five fields removed** in 3.0.0 — `fact_bindings[].fact_id`, `.rendered`,
  `.metric_surface`, `.period_surface` and `citations[].evidence_id` — were purely the second
  kind: *"the verifier needs metadata and the easiest historical implementation was to ask the
  model to output it."* (The six-field `calculation[]` had already left the schema at 2.0.0, and
  the schema peaked at **15** leaves in 1.1.0.) The evidence is that removing them changed no
  verifier check
  and no artifact type — `Draft`, `FactBinding`, `PassageCitation` and `Calculation` are
  byte-identical types, and the accepted store still renders the same post.
* What **remains** is of the first kind, with one exception. `text` is the product. `title` is
  prose. `rests_on`'s *choice* is a claim about what a paraphrase came from.
* **The exception is `kind`.** The slot table already determines it in every case the corpus
  contains, and a wrong `kind` currently costs a run twice: once at the writer gate (through
  `rests_on`) and once at §13.9. It is metadata the model is asked for because the verifier
  wants it.

---

## 4. Planner contract complexity, and the `556M → 110M` trace

| Measure | Value |
| --- | --- |
| System prompt | 3,011 chars / 16 lines / ~752 tokens |
| User prompt | 8,170 chars / 76 lines / ~2,042 tokens |
| Numbered rules | **9** |
| Output schema leaf fields | **19** |
| Required properties | 11 top-level, all required (portable-schema rule) |
| Identifier namespaces it must reproduce exactly | **3** — `obs:` ids, `norm:` passage ids, and the three-field derivation triple |

The planner is the model that must **retype 70-character digests**. The writer no longer does.

### The trace the brief asked for

**Where do 556 and 110 originate?**
`graph_run_id: graph-v1-0483dc6b4b10` → normalized table extraction → two observations,
`obs:…2022Q2:normalized-table:0c4364ebbc44` (556000000.0 USD) and
`obs:…2022Q3:normalized-table:4d66ef7200e9` (110000000.0 USD), each with `passage_id`,
`quoted_text: "556"` / `"110"`, `source_lane: normalized_table`, `validation_state: clean`.

**Does deterministic code already know their chronological order?** Yes, three times over:
`PackagedFact.period_start`/`period_end`; `core/periods.py:months_between("2022Q2","2022Q3")`;
and `offers()` only ever emits the earlier→later orientation for a two-period operation.

**Does a derived change exist before planning?** **No** — `execute_all` runs *after* the plan,
because it executes the plan's own requests. But the *offer* exists before, and so does the
detector's measurement.

**Does a direction field exist?** **Yes, and this is the finding.**
`data/story_demo/story-v1-76da8465cd95/candidate.json`:

```json
"signals": {
  "delta": -446000000.0,
  "delta_pct": -80.215827338,
  "direction": "decrease",
  "crosses_zero": false,
  "polarity": "revenue",
  "fired_on": "pct,abs"
}
```

Computed by `metric_move.candidate_from_move` via
`detector_config.quantity_direction(metric_id, delta)`, which reads the metric's sign
convention — so it is authoritative in a way a bare `556 > 110` is not.

**What exactly does the planner see?** `planner_prompt(package, *, offered)`. It receives the
**package**, not the **candidate**. `signals` lives on the candidate. **The direction is not in
the prompt** (verified by rendering it — see `01-PIPELINE-CONTRACTS.md` §8).

**Why was the planner able to output "rose"?** Because nothing stopped it. It was given
`556000000.0` and `110000000.0` as raw floats in two rows 6 lines apart, and asked to write a
thesis. gpt-5-nano wrote:

> *"Opendoor's adjusted gross profit **rose** from 2022Q2 to 2022Q3, with the larger figure
> anchored in the 2022Q2 base and a substantial quarter-over-quarter movement."*

and a key point: *"Adjusted gross profit for 2022Q3 is **higher** than 2022Q2 based on observed
figures in the normalized tables."*

**What validates the plan before the writer receives it?** `plan_violations`, and it checks
**twelve things, all referential** (verified, `planner.py:298-402`):

| Code | Checks |
| --- | --- |
| `thesis_empty` | the string is non-blank |
| `no_key_points` | the list is non-empty |
| `unresolvable_fact_id` / `unresolvable_passage_id` | ids exist in the package |
| `counterpoint_missing` / `counterpoint_ungrounded` | counter-evidence is used and grounded |
| `counter_evidence_unaccounted` / `unknown_unusable_id` | every counter item is used or excused |
| `unknown_warning_code` | warnings are in the package |
| `derivation_not_offered` | the triple is in `offered`, by whole-triple equality |
| `causal_language_not_computed` | matches `causal_language_for(package)` |
| `plan_not_constructible` | pydantic construction |

**Not one of them reads `claim` or `thesis` as text.** A plan asserting `556 → 110 = increase`
passes all twelve.

---

## 5. The three checks the brief hoped for, and where they could live

Stated as location, not as a design decision.

| Check | What it would compare | Data available at that point | Where |
| --- | --- | --- | --- |
| Thesis direction | direction words in `thesis` / `claim` against `candidate.signals["direction"]` | the candidate is in `DemoInputs`; the direction is already a closed word | `plan_violations(…, candidate=)` or a new function called from `run_demo` after `plan_story` |
| Comparative orientation in a key point | `higher`/`lower` against the offered pair's values | `offers()` output plus `PackagedFact.value` | same place |
| Statement class | `statement_class` against which ids the point names | `required_fact_ids` and `requested_derivations` | same place |

The architecture already supports all three without an import-rule change: `run_demo` holds the
candidate, the package, the offers and the plan simultaneously (`pipeline.py:645-720`).

**This audit does not recommend implementing them here.** `06-ARCHITECTURAL-OPTIONS.md` weighs
whether a check, a repair loop, or a research agent is the right response.

---

## 6. The one place two code-owned stages disagree

Not a model problem at all, and it accounts for **15 of the 85 recorded verification findings**
— the second-largest code in the corpus.

`compile.py:_citations_for` gives an `explanatory` sentence *"one citation per `rests_on`
passage"*, chosen by `_unused_handle`: the first evidence handle of that passage no earlier
sentence has cited, **falling back to the first one anyway** if all are used. Its docstring is
explicit that the fallback is deliberate and that *"whether **this** claim may rest on **that**
span"* belongs to the verifier.

`citations.py:_reuse_findings` (§13.7) then refuses exactly that shape: an identical
`(passage_id, char_start, char_end)` already cited by an earlier sentence, where **this**
sentence binds no fact the passage evidences. An `explanatory` sentence binds no fact by
construction.

**Verified 2026-08-26** by recompiling `story-v1-76da8465cd95`'s own four sentences with only
`rests_on` mechanically repaired to `["P1"]` on the explanatory sentence:

```text
REFUSE  citation_reused_for_unrelated_claim  sentence 2
        expected  'a fact read from norm:…open-20220630.htm#p133, as in sentence 1'
        observed  'no fact binding at all'
```

The model chose nothing here. Code chose the span, and code refused it.

---

## 7. Summary table — model-authored fields that code could construct

| Field | Currently | Could be | Cost of moving it |
| --- | --- | --- | --- |
| `key_points[].required_citation_passage_ids` | planner | derived from `required_fact_ids` | removes 1 of the 3 id namespaces the planner must retype |
| `key_points[].statement_class` | planner | derived from which ids the point names | removes an enum the planner gets wrong silently |
| `requested_derivations[]` (3 fields) | planner | index into `offers()` (`"take offer #1"`) | removes the last 70-char digests from the planner's output; `derivation_not_offered` becomes unreachable |
| `required_warnings[]` | planner | already a filtered set; could be *all* of `claim_qualifying_warnings` | removes `unknown_warning_code` |
| `sentences[].kind` | writer | derived from the slot rows the template names | removes 2 of the 4 writer-gate codes and one §13.9 check |
| `sentences[].rests_on[]` | writer | **not fully** — the choice is semantic | but the *set* could be offered as an index rather than a handle |
| direction of the move | **nobody** | `candidate.signals["direction"]` | this is the one that produced a factually inverted plan |

**Deliberately not on this list:** `thesis`, `why_it_matters`, `claim`, `uncertainty`,
`prohibited_claims`, `counterpoints[].claim`, `title`, `sentences[].text`, and which side of a
comparing word a figure goes on. Those are the product.
