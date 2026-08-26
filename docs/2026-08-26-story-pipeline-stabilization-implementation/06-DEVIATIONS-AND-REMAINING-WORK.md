# 06 — Deviations from the plan, and what is left

*Verified 2026-08-26.*

---

## 1. Where the implementation departed from `docs/2026-08-26-story-pipeline-stabilization-plan/`

| Plan said | Implemented | Why |
| --- | --- | --- |
| Add `angle` to the planner schema | **Not added** | It was a hedge for editorial framing once `structure` and `prohibited_claims` left. `why_it_matters` already carries that, unvalidated, and a second unvalidated field saying the same thing is exactly what `structure` was |
| Plan validation checks comparatives (`comparative_contradicts_spine`) | **Removed before shipping** | False positive: both sides of a `metric_move` spine are the same metric in different periods, so *"Q2 was higher than Q3"* is a **true** statement of a decrease. Distinguishing them needs the periods resolved on each side of the comparing word, which a plan — binding nothing — does not permit |
| Fix `citation_reused_for_unrelated_claim` in the compiler | **Implemented, then reverted** | It makes the run end at the compiler where it used to end at the verifier with a nameable finding — the exact *"gates ending runs before authoritative verification"* problem this work reduces — and 21 recorded drafts take that shape. Four candidate repairs were measured; the evidence is on `_unused_handle` and in the standing regression test |
| Build the table without `P` rows (writer has no `rests_on`) | **`P` rows kept** | Removing them made a package carrying counter-evidence unplannable: every counterpoint earned `counterpoint_ungrounded`, every empty one `counterpoint_missing`. The writer omits the passage *section* on its own grounds — its schema has nowhere to put a handle — which is a different question from whether the row exists |
| `spine_derivations` takes the first offered triple per operation | **Takes the detector-corroborated orientation** | Taking the first silently emptied `reused_detector_signal` on `cross_metric_divergence` and let a deliberately misreported signal reach the planner. `execute.signal_applies_to` was extracted so both callers ask one rule |
| Phase 0 metrics harness as a committed test file | **Kept as a validation script** | A test that issues live generations is not a test. The seven rates are computed from run directories; the script is in the session scratchpad and the numbers are in `05-LIVE-VALIDATION.md` |

## 2. Behaviour changes worth knowing about

* **`required_warnings` is now the whole admissible set**, deduplicated, rather than the model's
  selection. Strictly safer — a model can no longer silently drop a caveat — and it makes more
  prose mandatory. It is the single largest remaining cause of live rejection.
* **Which orientation a `compare_levels` runs in is code's now.** For
  `cross_metric_divergence` the planner used to choose; `spine_derivations` picks the one the
  detector measured. Both are true statements of the same gap; the prose reads the other way
  round from the historical fixture.
* **A `metric_move` post can no longer contain an `explanatory` sentence.** The vocabulary is
  intact and unreachable; when a story type turns explanatory retrieval on, §6 of
  `02-WRITER-COMPOSITION-CHANGES.md` is the first thing to read.

## 3. Remaining failure classes

| Class | Evidence | Owner |
| --- | --- | --- |
| **Required-warning prose** | `market_count` never accepts on either provider; §13 wants `flagged \| carries a warning \| data-quality` and neither model reliably writes it. One bounded repair with the exact phrase list did not fix it | model — but the prompt could state it far more prominently |
| **Metric labels that are unwritable** | `pct_homes_on_market_gt_120_days`'s ontology label carries `120`, `the market` and `greater than` | **ontology**, upstream. Needs a writable display alias |
| **Prose overreach** | `unbound_numeral`, `connective_sentence_carries_a_claim`, `unsupported_temporal_ordering`, `metric_surface_absent_from_text` | model. These are correct refusals |
| **Repair converts nothing** | 7 attempts, 0 conversions across 11 live runs | design. The failures repair was built for were removed by Phases 1–3; what is left is not one-round-fixable |
| **`citation_reused_for_unrelated_claim`** | order-dependent, dormant | code. Needs `rests_on` on `DraftSentence`, which re-keys `draft_content_sha256`, and a live explanatory candidate to test against |
| **`MissingGenerationError` escapes `run_demo`** | a `LookupError`, uncaught | code. Must be fixed before repair is enabled on the replay path |

## 4. What was deliberately not done

* The Research Agent and any tool-calling loop. `GraphRetriever`, the nine typed tools and
  provider independence are untouched, and `StorySpine` is designed so an agent could produce
  one instead of a detector without changing the writer or verifier interface.
* New detectors or post types.
* Any change to acquisition, normalization, ontology, graph projection or provenance.
* Enabling `want_explanatory_search`.
* Widening recovery to the full `legal_renderings` set — deferred, and there is now a second
  reason not to: it would bind a model that copied the FACTS row's raw spelling and publish
  *"Adjusted Gross Profit of 556000000.0 USD"*.
