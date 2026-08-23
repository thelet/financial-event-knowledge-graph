# Live model-backed extraction — 2026-08-23

| Document | What it is |
| --- | --- |
| [`00-PLAN.md`](00-PLAN.md) | The staged implementation plan. Plan only — nothing implemented. |

**Reading order.** `docs/2026-08-18-extraction-model-path-audit/EXTRACTION-MODEL-PATH-AUDIT.md`
established why narrative and event candidates never reached a model.
`docs/2026-08-18-live-extraction-implementation-plan/IMPLEMENTATION-PLAN.md` was the first plan
against that audit; **`00-PLAN.md` here supersedes it**, correcting four of its load-bearing claims
(§0, findings F2–F5) and simplifying two of its designs (three provider modes become two orthogonal
knobs; the answer store stops being a run-id input).

Every fact in `00-PLAN.md` was re-verified against the code at `ff3b08f` on 2026-08-23, by five
independent read-only inspections plus direct measurement of the running `llama-server`. Facts are
marked *(verified …)* with how they were checked.
