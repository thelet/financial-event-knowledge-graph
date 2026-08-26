# 01 — Planner changes

*Verified 2026-08-26.*

---

## 1. The truth boundary

`story/core/spine.py` — `StorySpine`, 17 fields, frozen and slotted, built by
`spine_for(candidate, package, derived, *, causal_language=None)`. It imports
`story.core.models`, `story.core.lexicon` and the standard library, and no stage — which is
what makes it a `core/` type and what would let a Research Agent produce one instead of a
detector without changing anything downstream.

`direction` is **read off `candidate.signals["direction"]`**, never recomputed from the sign of
a delta. The detector got it from `detector_config.quantity_direction(metric_id, delta)`, which
consults the metric's sign convention: `direct_selling_costs` is stored negative on 46 of 46
values, so *"costs rose"* is a fall in the stored number. `direction_polarity` is
`lexicon.change_direction(direction)` rather than a second translation table, and
`__post_init__` refuses a spine whose three direction spellings disagree.

`spine_for` returns `None` — no spine, no pre-plan derivation, run unchanged — for a
non-`metric_move` story type, a package/candidate mismatch, more than one metric id, other than
two anchor periods, no unique packaged fact per `(metric_id, period_key)`, or mismatched units.

**Which derivations execute before the planner.** `pipeline.spine_derivations(candidate,
package, offered)`: every offered triple whose operation has a `DETECTOR_SIGNALS` key the
candidate actually published, one per operation, choosing the orientation
`execute.signal_applies_to` corroborates.

That last clause was a correction. Taking the *first* offered triple made
`cross_metric_divergence` pick the un-measured orientation, which silently emptied
`reused_detector_signal` and let a deliberately misreported signal reach the planner instead of
being refused. `signal_applies_to` was extracted from `_signal_applies` so both callers ask one
rule.

For the demo `metric_move` candidate the result is `absolute_change` and `percentage_change` —
exactly the two the Qwen planner used to request, and exactly the two a correct planner *must*
request. `crossed_zero` is offered and not taken: `signals["crosses_zero"]` is `False`.

## 2. The contract: 19 leaves → 7

```json
{"thesis": "…", "why_it_matters": "…", "uncertainty": "…",
 "key_points": [{"claim": "…", "facts": ["F1"]}],
 "counterpoint": "", "counterpoint_facts": []}
```

Not one identifier the model must retype. `facts` holds two-character handles printed beside
their values.

| Field | Disposition | Filled by |
| --- | --- | --- |
| `causal_language` | **REMOVED** | `causal_language_for(package)` — it was already re-stamped over the model's answer |
| `requested_derivations` (3 leaves) | **REMOVED** | `spine_derivations` before the call |
| `required_citation_passage_ids` | **CODE_DERIVED** | `PackagedFact.passage_id` for the facts a point names |
| `statement_class` | **CODE_DERIVED** | `calculated` if any `D` handle, else `reported` |
| `required_warnings` | **CODE_PINNED** | `claim_qualifying_warnings`, **deduplicated** |
| `structure`, `prohibited_claims` | **REMOVED** | nothing read them |
| `unusable_evidence` (2 leaves) | **REMOVED** | code knows which counter items a plan used |
| `angle` | **not added** | the plan proposed it; `why_it_matters` already carries it, unvalidated, and a second unvalidated field saying the same thing is what `structure` was |

`EditorialPlan` the **type** is unchanged. Every stored `editorial_plan.json` still loads and the
demo UI's plan panel is untouched.

**A `D` handle resolves to its two input observations**, because §11 restricts
`required_fact_ids` to ids beginning `obs:`. **A passage handle grounds a counterpoint and never
a key point** (`passages_ground`): counter-evidence is typed as passages, and a package whose
counter passage backs no packaged fact cannot be answered with a fact handle at all.

## 3. Semantic plan validation

`planner.spine_violations(plan, spine)` — one code, `direction_contradicts_spine`, over
`thesis` and every `key_points[].claim`.

It applies `story/core/lexicon.py`'s `CHANGE_DIRECTION` — the same closed, tested vocabulary
`verification/derived_facts._direction_findings` already applies to every draft sentence, one
stage earlier. Silence is **not** a violation: a plan whose job is the angle may legitimately
not state a direction, and copying §13's stronger rule up would refuse valid plans.

### Two false positives found and removed while writing it

1. **Comparatives are not checked.** A change verb states which way a quantity moved and needs no
   word order. A comparative states an ordering of two named sides, and both sides of this spine
   are the *same metric in different periods* — *"the third quarter was higher than the second"*
   contradicts a decrease, *"the second was higher than the third"* states it correctly.
   Telling them apart needs the periods resolved on each side, which `claims` can do for a draft
   sentence only because §13 makes such a sentence bind a `compare_levels` fact naming both. A
   plan binds nothing.
2. **`ORDERING_WORDS`** — `set(CHANGE_DIRECTION) & set(COMPARATIVE_DIRECTION)`, today exactly
   `{higher, lower}` — is skipped, because those words reached the check through the *change*
   map too. Derived rather than typed out, so a word added to either map joins it.

Measured on the real recorded plans: `story-v1-76da8465cd95` (*"rose"* over 556 → 110) is
refused; `story-v1-1daff167348f` passes; `"improved"`, `"did not rise"` and
`"higher in Q2 than Q3"` all pass.

## 4. The `core/lexicon.py` move

`story/stages/generation/` may not import `story/stages/verification/`, so the direction
vocabulary was unreachable from a plan-side check. The three lexicons, their three lookups, the
scanners, `LexicalMatch`, `negated` and the compile helpers moved to `story/core/lexicon.py` and
are re-exported from `language.py`, so every existing import site resolves unchanged.

Two constraints preserved and carried across with their comments: the read-only Cypher scan
case-folds every string constant in `story/` (which is why `"dropped"` is present and `"drop"`
is not), and `_CLAUSE`'s `\.(?!\d)` guard is a measured fix.

## 5. Two defects found live and fixed

| Symptom | Cause | Fix |
| --- | --- | --- |
| gpt-5.4 lost 2 of 6 runs to `plan_not_constructible` | The model wrote a counterpoint on a package with **no counter-evidence**; `Counterpoint`'s validator refuses a claim grounded in nothing. The prompt printed `COUNTER-EVIDENCE (0 items)` and rule 5 said *"where there is none, leave it empty"* — too easy to read past | The section now states the instruction in words when there is none. And `plan_not_constructible` is routed to **planner repair**: the correction is exact and statable in one sentence, which is what makes it a repair rather than a rejection |
| both providers lost `market_count` runs to `required_warning_absent` | A package raises one warning per *fact*, so two `warned` readings put `unpreferred_source_lane` in `required_warnings` **twice**, and §13 wanted the qualifying phrase said twice in a four-sentence post | Deduplicated. A caveat stated twice is not two caveats |
