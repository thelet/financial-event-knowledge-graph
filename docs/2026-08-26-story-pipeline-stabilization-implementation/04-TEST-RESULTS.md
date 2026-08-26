# 04 — Test results

*Measured 2026-08-26 on branch `feat/story-pipeline-stabilization`.*

---

## 1. The suite

Baseline before any change, at `81399f3`: the whole offline suite green (`pytest tests/ -m "not
live and not neo4j"`, exit 0).

After: see §5 for the final count. Nothing was marked `xfail`, nothing was skipped to pass, and
no assertion was loosened to accommodate a source defect — three defects found by tests were
fixed in the source instead (§4).

## 2. No weakening of deterministic verification — enforced, not promised

| Property | Value before | Value after |
| --- | --- | --- |
| `len(GATE)` | 102 | **102** |
| Severity census | 96 REFUSE / 2 WARN / 4 ANNOTATE | **identical** |
| `verifier_gate_digest()` | `5f2ebd9619a5f3ef…` | **`5f2ebd9619a5f3ef…`** |

The digest is byte-identical to the value recorded in `demo_manifest.json` of the pre-change
live runs (`story-v1-1daff167348f`), which is the strongest available proof that the gate table
did not move.

**Two checks were strengthened**, both by extending an existing code rather than adding one:

* `forward_looking_language` now catches `will`/`would`/`shall` + verb. `"it will recover next
  year"` previously passed with zero findings, verified both through recovery and through a
  hand-authored slot template.
* Deriving `kind` makes three wrong labels unrepresentable and closes a live gap: a
  derived-fact-bearing sentence labelled `reported` verified clean, because
  `reported_sentence_carries_calculation` fires on a `Calculation` the compiler never builds.

## 3. New coverage

| Area | File | Notes |
| --- | --- | --- |
| Story spine | `test_story_spine.py` (18) | Driven from the real committed artifacts. S1: `spine.direction == "decrease" == quantity_direction(metric_id, to − from)`, recomputed from the spine's own values. S2: every `DerivedFact`'s `display_semantics` agrees with `direction_polarity`. Plus the `direct_selling_costs` sign-convention inversion and the unverified-direction case |
| Lexicon move | `test_story_lexicon.py` (28) | Asserts **identity** (not equality) of every moved name through `language`, drives all three scanners through both module paths, re-asserts the three totality claims at the new home, and proves the read-only Cypher scan still reaches the new file |
| Slot recovery | `test_story_composition_recovery.py` (51) | The headline test replays the recorded Qwen answer end to end. Text-preserving property over every sentence of every stored draft. Ambiguity: two rows offering one literal, and one row's literal twice — neither is bound. `$556.0 million` is not recovered. A sentence already carrying `{{` passes through |
| `kind` derivation | same | All four kinds plus the mixed F+D case |
| Plan/spine agreement | `test_story_planner.py` | Both **real recorded plans**: `story-v1-76da8465cd95` refused `direction_contradicts_spine`, `story-v1-1daff167348f` passes. Plus silence, `None`-polarity words, negation, `ORDERING_WORDS`, unverified direction |
| Handle resolution | same | `F` → observation + passage + `reported`; `D` → its two **input** observations + `calculated`; unknown handle → `unresolvable_fact_handle`; passage handle grounds a counterpoint and not a key point |
| Repair | `test_story_demo.py` | Bounds respected; no blind retry (a repair's `request_sha256` differs); `call_sites` records every attempt |
| Composition diagnostics | `test_demo_ui_code_catalogue.py` | The AST totality walk now covers `CompositionViolation(...)`; the composition family is total and each code renders `blocking: true` |

## 4. Three defects the tests found, fixed in the source

1. **`spine_derivations` picked the wrong orientation.** Taking the first offered triple silently
   emptied `reused_detector_signal` on `cross_metric_divergence` and let a deliberately
   misreported detector signal reach the planner instead of being refused. Fixed by extracting
   `execute.signal_applies_to` so the selector and the cross-check ask one rule.
2. **A package with counter-evidence became unplannable.** Building the slot table without `P`
   rows left no way to ground a counterpoint: every one earned `counterpoint_ungrounded`, every
   empty one `counterpoint_missing`. Fixed by keeping the rows and having the writer omit the
   passage *section* on its own grounds.
3. **A stale module docstring** in `story/stages/generation/writer.py` described a return type
   and four refusals that no longer exist. Rewritten rather than left to mislead.

## 5. Final counts

| Selection | Result |
| --- | --- |
| `pytest tests/ -m "not live and not neo4j"` | **6,753 results, 0 failures** |
| `pytest tests/story -m "neo4j"` (against the running `fkg-neo4j` container, read only) | **140 results, 0 failures** |

`test_story_demo.py` — the whole-pipeline integration file — was the last to migrate. Its golden
artifact-digest tables had to be recomputed **three** times: once for the new prompts and
schemas, again when a live run turned up the counter-evidence defect and moved the planner
request digest, and once more when the planner gained its `COMPUTED FIGURES` section.

Live-marked tests were additionally exercised against the running llama.cpp server during the
writer and planner migration; both reached their own documented decision points rather than
being skipped.

### One flake, named rather than absorbed

`tests/graph/test_export_determinism.py::test_the_real_run_is_byte_identical_across_two_seeded_processes`
failed **once**, in a run made while a live validation was competing for the same machine, and
passed on immediate re-run and in every other run. It projects 28,836 nodes in two subprocesses
under different `PYTHONHASHSEED`s and compares bytes.

`git diff --stat main HEAD -- graph/ normalization/ extraction/ acquisition/ ontology/` is
**empty** — this branch changes nothing that test exercises. Recorded as a pre-existing flake
under load rather than a regression, and not investigated further because it is outside this
work's scope. It is worth someone's attention on its own.

## 6. Replay and artifact compatibility

* `EditorialPlan` and `Draft` are unchanged as types. Every stored `editorial_plan.json` and
  `draft.json` still loads; the demo UI's plan, draft and evidence panels are untouched.
* `writer.draft_from` / `draft_violations` remain as the pre-3.0.0 reader for the 50 recorded
  drafts, called by nothing on the live path.
* The three writer codes 4.0.0 retired (`malformed_slot`, `rests_on_without_explanatory_sentence`,
  `rests_on_not_a_passage_handle`) stay declared and catalogued, because
  `story-v1-76da8465cd95/rejected.json` names all three and the UI still renders that run. The
  totality test now permits exactly this named set and nothing else.
* The three replay stores were re-recorded. `content_sha256` and `raw_content` are byte-identical
  to the previous recording throughout — **only the request keys moved**, which is what a prompt
  and schema change is supposed to do.
* No historical run directory under `data/story_demo/` was rewritten.
