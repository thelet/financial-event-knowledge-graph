# 02 — Planner stabilization

*Field-by-field disposition of the current 19-leaf planner contract, the proposed schema, and
the plan-validation boundary. Verified 2026-08-26.*

---

## 1. Current schema

`planner_schema(causal_language=)`, `PLANNER_PROMPT_VERSION` 1.2.0 — 19 leaves, 11 required
top-level properties, 9 numbered rules, ~752-token system prompt, ~2,042-token user prompt.

```text
thesis                                     string
why_it_matters                             string
key_points[].claim                         string
key_points[].required_fact_ids[]           string   ← obs: digests, retyped
key_points[].required_citation_passage_ids[] string ← norm: ids, retyped
key_points[].statement_class               enum(3)
counterpoints[].claim                      string
counterpoints[].required_fact_ids[]        string
counterpoints[].required_citation_passage_ids[] string
requested_derivations[].operation          enum(7)
requested_derivations[].from_fact_id       string   ← obs: digest, retyped
requested_derivations[].to_fact_id         string   ← obs: digest, retyped
required_warnings[]                        string
causal_language                            enum(1)  ← already pinned by code
uncertainty                                string
structure[]                                string
prohibited_claims[]                        string
unusable_evidence[].id                     string
unusable_evidence[].reason                 enum(5)
```

---

## 2. Field-by-field disposition

| Field | Disposition | What constructs it instead, and the evidence |
| --- | --- | --- |
| `thesis` | **MODEL_AUTHORED** | The claim of the post. Now *validated* against the spine — §4 |
| `why_it_matters` | **MODEL_AUTHORED** | Rendered into the writer prompt (`prompts.py:1254`); genuinely editorial |
| `key_points[].claim` | **MODEL_AUTHORED** | The editorial content |
| `key_points[].required_fact_ids[]` | **MODEL_AUTHORED, respelled** | Keep the choice, drop the digest: the planner names **handles** (`F1`, `D1`) from a table the prompt prints, and code resolves them. `slot_table` already mints those handles deterministically (`slot_table.py:79`) |
| `key_points[].required_citation_passage_ids[]` | **CODE_DERIVED** | `planner._referenced_passage_ids` already computes passage-from-fact via `{fact.observation_id: fact.passage_id}` (`planner.py:437-448`). The plan's answer is a lookup that can only be right or wrong, never informative |
| `key_points[].statement_class` | **CODE_DERIVED** | A point naming only `F` rows is `reported`; one naming a `D` row is `calculated`. Nothing downstream reads it except the writer prompt. Observed wrong in the corpus: `story-v1-76da8465cd95`'s key point 3 is `explanatory` in a package with **zero explanatory passages** |
| `counterpoints[]` (3 leaves) | **CODE_PINNED empty for this slice**, MODEL_AUTHORED otherwise | `plan_violations` already refuses a counterpoint that rests on nothing drawn from `counter_evidence`. Both corpus packages carry **0 counter-evidence items** (verified), so for `metric_move` the field is constrained to `[]` and the schema pins it. When counter-evidence exists the field returns unchanged |
| `requested_derivations[]` (3 leaves) | **REMOVED for `metric_move`**; elsewhere **respelled as an offer index** | The spine executes what the detector fired on. `candidate.signals["fired_on"] == "pct,abs"` and `execute.py:78`'s `DETECTOR_SIGNALS` already pair those signals with `absolute_change` / `percentage_change`. Removing it makes `derivation_not_offered` unreachable from a plan |
| `required_warnings[]` | **CODE_PINNED** | `planner.claim_qualifying_warnings(codes, package)` already computes the admissible set by filtering `WarningKind.BUILD_PROVENANCE`. Pin it to that whole set. Both corpus packages yield the same 5 codes, all build-provenance, so the pinned value is `()` — and §13's `required_warning_absent` keeps its teeth for packages that carry a real qualifier |
| `causal_language` | **REMOVED from the schema** | Already `CODE_PINNED`: a one-member enum, and `plan_story` **re-stamps** it over the model's answer regardless (`test_the_planner_stamps_the_computed_causal_language_over_the_models_answer`). A field the model cannot influence does not belong in its grammar. `causal_language_not_computed` becomes unreachable |
| `uncertainty` | **MODEL_AUTHORED** | Editorial hedging, rendered to the writer |
| `structure[]` | **REMOVED** | Rendered into the writer prompt at `prompts.py:1258-1259` and **validated by nothing**. Observed garbage: gpt-5-nano returned the schema's own property names as the structure (verified). Sentence order is the writer's job and §13 checks the result |
| `prohibited_claims[]` | **REMOVED** | Rendered at `prompts.py:1277` and read by no check anywhere (verified by grep). The real guard is `language_safety` — 5 closed lexicons, 96 REFUSE codes. A free-text hint that nothing enforces is a field that can only mislead |
| `unusable_evidence[]` (2 leaves) | **CODE_PINNED empty for this slice** | Exists only to account for unused counter-evidence. With 0 counter-evidence there is nothing to account for; `counter_evidence_unaccounted` and `unknown_unusable_id` become unreachable |

**Summary: 19 leaves → 7.** Removed 5, code-derived 2, code-pinned 3 (2 of them empty in this
slice), respelled 2 to handles, kept 5 genuinely editorial.

---

## 3. Proposed stabilized schema

`PLANNER_PROMPT_VERSION` 2.0.0, inside `portable_schema`'s subset (every object requires every
property, no `null`, no `anyOf`):

```json
{"type": "object", "additionalProperties": false,
 "required": ["thesis", "why_it_matters", "angle", "key_points", "uncertainty"],
 "properties": {
   "thesis":         {"type": "string"},
   "why_it_matters": {"type": "string"},
   "angle":          {"type": "string"},
   "uncertainty":    {"type": "string"},
   "key_points": {"type": "array", "items": {
     "type": "object", "additionalProperties": false,
     "required": ["claim", "facts"],
     "properties": {
       "claim": {"type": "string"},
       "facts": {"type": "array", "items": {"type": "string"}}}}}}}
```

Seven leaves. **Not one identifier the model must retype** — `facts` holds `F1`/`D1` handles,
two characters each, printed in the prompt beside their values.

`EditorialPlan` the **type** keeps every field it has today. Removed schema fields are filled by
`editorial_plan_from` with code's answers, so `Draft`, the verifier, the demo UI and every
stored artifact are untouched. That is the same technique the 3.0.0 writer change used and the
reason it cost so little.

`angle` is new and is a hedge worth naming: with `structure` and `prohibited_claims` gone, the
planner has fewer places to say *how* the post should read. `angle` is one free-text sentence
rendered into the writer prompt. **Unvalidated, like the fields it replaces** — the honest
position is that it earns its place only if the live measurement in `06` shows prose quality
dropping without it, and it should be dropped if not.

---

## 4. The plan-validation boundary

### The three approaches, decided

| Approach | Verdict |
| --- | --- |
| **A — textual semantic validation** of plan prose against the spine | **Adopt as a backstop.** Cheaper than the audit assumed: `language.change_verbs(text)` + `language.change_direction(term)` already exist, are pure string functions, and the map is asserted **total** over `numerals.CHANGE_VERBS` by `tests/story/test_story_verification_derived.py:445`. ~100 words with tri-state polarity (`True` up, `False` down, `None` = states no direction). No new vocabulary is needed |
| **B — the plan never authors the factual spine** | **Adopt as primary.** The spine states `from`, `to`, `direction`, and the derived facts. The planner is shown them as *given* and asked for interpretation. This is what stops the problem rather than detecting it |
| **C — structured locked fields + editorial prose** | **This is B, expressed as a schema.** The proposed schema in §3 is exactly that: no direction field, no value field, no derivation triple — nothing factual the planner can get wrong by construction |

**Why keep A when B exists.** `thesis` is free prose and is rendered verbatim into the writer
prompt. B stops the planner from *authoring* the direction; it cannot stop it from *writing* a
contradictory sentence. The check is 20 lines over an existing tested lexicon, and it is not
"NLP-parsing arbitrary prose" — it is the same closed lexicon the verifier already applies to
every draft sentence (`derived_facts._direction_findings`), applied one stage earlier.

### The check

```text
location   story/stages/generation/planner.py, inside plan_violations(..., spine=)
           called from run_demo, which holds candidate, package, offers, spine and plan
inputs     plan.thesis, every key_points[].claim, and StorySpine
```

| New code | Fires when | Repairable? |
| --- | --- | --- |
| `direction_contradicts_spine` | a change verb in `thesis` or a `claim` has a polarity, and it is not `spine.direction_polarity` | **Yes — planner repair.** The feedback is fully structured: the verified pair, the verified direction, and the offending word |
| `comparative_contradicts_spine` | a comparative (`higher`, `exceeded`, `below`, …) resolves through `COMPARATIVE_DIRECTION` against the wrong side of the spine's two readings | **Yes — planner repair** |
| `value_not_in_package` | a numeral in `thesis` or a `claim` matches no packaged value at its own precision, via `numerals.compare_token_to_fact` | **Yes — planner repair** |

**Deliberately not added: a "direction must be stated" rule.** The verifier's
`derived_direction_not_stated_in_text` exists because a *draft sentence* binding a directional
derived fact must name the direction. A plan may legitimately be silent about direction — its
job is the angle. Copying the stronger rule up would refuse valid plans. This asymmetry is
recorded because the verifier's own docstrings warn about the opposite mistake: a lexicon used
as a *filter for claims* rather than a *trigger for checks* silently disabled six comparative
synonyms until it was flipped (`claims.py:529-556`).

**`direction_verified is False` disables the direction check and only that check.** When
`quantity_direction` returned `None` (`ValueSign.UNVERIFIED` — `cost_of_revenue`,
`inventory_valuation_adjustment`), the spine says so and the check abstains rather than
guessing, matching the detector's own behaviour.

---

## 5. The dependency problem, and the resolution

`language.CHANGE_DIRECTION`, `COMPARATIVE_DIRECTION` and `CROSSING_TERMS` live in
`story/stages/verification/language.py`. `story/stages/generation/` **may not import it**
(`test_no_stage_imports_another_stage`, which derives the stage name from the module path).

| Option | Assessment |
| --- | --- |
| Pass the lexicon in from `run_demo` | Works (it is the existing pattern for `offered` and `slots`) but a 100-entry mapping threaded through a composition root as an argument is plumbing, not a boundary |
| Copy the vocabulary into the generation stage | Rejected. The repository's precedent for a necessary copy is a *checked* one (`slot_table.TWO_PERIOD_OPERATIONS` is asserted equal to the derivation stage's). A 100-word copy asserted equal is 100 words in two places |
| **Move the three lexicons and their three lookup functions to `story/core/lexicon.py`** and re-export from `verification/language.py` | **Recommended.** A closed word list carrying application meaning is what `core/` is for, and `story/core/numerals.py` **already holds** `CHANGE_VERBS` — a 13-word list `language.CHANGE_DIRECTION` is asserted total over. The vocabulary is already split across two layers; this reduces it to one home with one re-export |

The move touches no behaviour and no test assertion beyond import paths. It must preserve the
two constraints recorded in `language.py`'s own comments: the Cypher read-only scan case-folds
every string constant in `story/`, which is why `"drop"` is absent while `"dropped"` is present,
and why `"contract"`/`"contracts"` are absent. Moving the constants moves them **into the same
scanned package**, so the constraint is unchanged — but the test that enforces it
(`test_no_string_constant_anywhere_in_the_package_could_be_a_write_statement`) must be confirmed
to cover `story/core/` as well as `story/stages/`, which it does by name.

---

## 6. What the planner prompt gains

One new section, printed before FACTS:

```text
VERIFIED CHANGE (code computed this; it is not yours to restate or contradict)
  metric     adjusted_gross_profit (Adjusted Gross Profit)
  from       2022Q2   $556 million    [F1]
  to         2022Q3   $110 million    [F2]
  direction  decrease
  computed   [D1] decreased by $446 million     (absolute change)
             [D2] decreased by 80.2%            (percentage change)
  You may decide what this means and what matters about it. You may not say it rose.
```

Every string in it is a value code already holds: `renderings.observed_figure`,
`renderings.derived_figure`, `renderings.direction_phrase`, and the detector's `direction` word.
The handles are the slot table's.

**And two sections leave.** `DERIVATIONS OFFERED` disappears for `metric_move` (code chose), and
the id columns shrink — `_fact_lines` prints `[F1]` beside `[obs:…]` rather than requiring the
digest to be spelled back.

---

## 7. Errors, and whether they are repairable

| Code | Stage | Owner | Repair route |
| --- | --- | --- | --- |
| `direction_contradicts_spine` | plan validation | **model (planner)** | planner repair, max 1 |
| `comparative_contradicts_spine` | plan validation | **model (planner)** | planner repair, max 1 |
| `value_not_in_package` | plan validation | **model (planner)** | planner repair, max 1 |
| `unresolvable_fact_handle` (replaces `unresolvable_fact_id`) | plan validation | **model (planner)** | planner repair, max 1 |
| `thesis_empty`, `no_key_points` | plan validation | **model (planner)** | planner repair, max 1 |
| `plan_not_constructible` | construction | **model (planner)** | **no** — a schema-conformant answer that will not build is a contract fault, not a wording one |
| `counterpoint_*`, `counter_evidence_unaccounted`, `unknown_unusable_id`, `unknown_warning_code`, `derivation_not_offered`, `causal_language_not_computed` | — | — | **unreachable for `metric_move`** once the fields are pinned or removed. Kept registered and tested, exactly as the eight pre-3.0.0 writer codes are, because `cross_metric_divergence` and packages with counter-evidence still reach them |

---

## 8. Tests this section requires

1. A plan whose thesis says `"rose"` over a `decrease` spine is refused with
   `direction_contradicts_spine` — driven from `story-v1-76da8465cd95`'s **real recorded plan**.
2. A plan whose thesis says `"fell"` over the same spine passes.
3. A plan whose thesis states no direction at all passes (the asymmetry in §4).
4. A spine with `direction_verified=False` does not raise the direction code, and does raise the
   others.
5. `"improved"` / `"widened"` / `"turned"` — `None`-polarity words — never raise it.
6. A negated construction (`"did not rise"`) does not raise it, via `language.negated`.
7. For every removed field: code constructs the same value the corpus's accepted plans carried.
   Specifically `required_citation_passage_ids` recomputed from `required_fact_ids` equals what
   each of the 17 accepted runs' plans stated.
8. `S1` — `spine.direction == quantity_direction(metric_id, to_value - from_value)`.
9. `S2` — every spine `DerivedFact`'s `display_semantics` agrees with `direction_polarity`.
10. The proposed schema passes `portable_schema` (every object requires every property; the six
    permitted keywords only).
11. A repaired plan names the same `facts` handles as the original — a repair may not change the
    evidence.
