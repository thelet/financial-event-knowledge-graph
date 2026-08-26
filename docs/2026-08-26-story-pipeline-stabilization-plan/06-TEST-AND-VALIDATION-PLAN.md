# 06 — Test and validation plan

*Designed before implementation, as asked. Verified 2026-08-26.*

---

## 1. The seven rates

Measured separately, because a single "accepted" number hides which stage is failing.

| Rate | Numerator | Denominator | Today (metric_move, live) |
| --- | --- | --- | --- |
| planner-valid | plans passing `plan_violations` **and** the spine check | runs reaching the planner | n/a — the spine check does not exist. 1 of 2 live plans was factually inverted |
| writer-contract-valid | answers passing `templates_from` **after** normalization | runs reaching the writer | **0 of 2** live |
| compile-success | drafts leaving `compile_draft` | runs reaching the compiler | 0 of 2 (never reached) |
| verifier-pass | `verified.passed` | runs reaching the verifier | 0 of 2 (never reached) |
| accepted-post | disposition `accepted` | all runs | **0 of 2** |
| repair-attempt | runs issuing ≥1 repair call | all runs | n/a |
| repair-success | repairs whose run ends `accepted` | repair attempts | n/a |

The harness lives in `tests/story/test_story_stabilization_metrics.py` and reads run directories,
so it works on both replayed and live corpora with no code path of its own.

---

## 2. The corpus

**Do not make success depend on one candidate.** The brief is right and the corpus is thin: the
58 stored runs cover only **3 distinct candidates** — `cross-metric-divergence` (42 runs),
`metric-move:adjusted-gross-profit` (14), `metric-move:homes-purchased` (2) (verified).

| Tier | Contents | Purpose |
| --- | --- | --- |
| **A — replay, committed** | The 14 `adjusted_gross_profit` and 2 `homes_purchased` runs' recorded model answers, replayed through the new pipeline | Regression. Every accepted run stays accepted; every recorded refusal is re-classified |
| **B — recovery corpus** | Every recorded writer answer in the corpus (40 drafts, 124 sentences), fed through recovery | Property tests **S4**–**S6** at scale, offline, no provider |
| **C — adversarial, hand-authored** | The 10 cases in `03` §3 plus the reversed-comparative proof | Prove the verifier still refuses what it should |
| **D — live, bounded** | **≥ 3 `metric_move` candidates × 2 providers × 5 runs = 30 generations** | The reliability numbers that matter |

**Tier D needs more candidates than the corpus holds.** `metric_move` fires on the graph run
already present; the detector is deterministic and re-runnable, so widening the candidate set is
a discovery step, not new engineering. **If fewer than 3 distinct `metric_move` candidates can be
produced from the current graph, that is itself a finding and the threshold in `00` §6 must be
restated against what exists.** This is the plan's most uncertain input.

---

## 3. Provider comparison

Both, always, reported separately:

| Provider | Note |
| --- | --- |
| `local_openai_compatible` / Qwen3.5-9B-Q4_K_M | Byte-reproducible at temperature 0 in 4 of 4 measured live repeats. A per-candidate result is one sample, not five — **cap it at 1 run per candidate and say so**, rather than reporting five identical answers as five successes |
| `openai` / one model | `temperature_sent: false`, `reasoning_effort: minimal` — genuinely non-deterministic (2 of 2 measured repeats differed). Five runs per candidate here are five samples |

The asymmetry is real and must be reported, not averaged away. An architecture that only works
on one of them has not been stabilized.

---

## 4. The test matrix

### Planner

| # | Test | Phase |
| --- | --- | --- |
| P1 | Cannot invert a known direction — `story-v1-76da8465cd95`'s **real recorded plan** is refused `direction_contradicts_spine` | 5 |
| P2 | A correct-direction plan passes | 5 |
| P3 | A plan stating no direction passes (the deliberate asymmetry) | 5 |
| P4 | `direction_verified=False` disables only the direction check | 5 |
| P5 | `None`-polarity words (`improved`, `widened`, `turned`) never raise it | 5 |
| P6 | A negated construction (`"did not rise"`) does not raise it | 5 |
| P7 | Cannot change deterministic values — `value_not_in_package` on an invented figure in a claim | 5 |
| P8 | Every removed field is reconstructed correctly by code, checked against all 17 accepted plans | 5 |
| P9 | A repaired plan names the same fact handles as the original | 6 |
| P10 | The 2.0.0 schema is inside `portable_schema`'s subset | 5 |
| P11 | **S1**, **S2** | 4 |

### Writer and composition

| # | Test | Phase |
| --- | --- | --- |
| W1 | A slot-based answer still compiles and verifies identically (the accepted regression) | 3 |
| W2 | **The Qwen recorded answer, recovered, compiles and verifies with 0 findings** — the phase acceptance test | 3 |
| W3 | **S4** — text preserved, property test over tier B | 3 |
| W4 | **S5** — two rows offering one literal refuses; one row's literal twice in a sentence refuses | 3 |
| W5 | **S6** — only offered strings are substituted | 3 |
| W6 | An unbindable numeral still fails (`unbound_numeral` on `2022`) | 3 |
| W7 | A near-miss spelling (`$556.0 million`) **refuses recovery** and does not silently rewrite prose | 3 |
| W8 | **S7** — the `kind` derivation table, all four kinds and the two mixed cases | 3 |
| W9 | A D-bearing sentence can no longer be classified `reported` (the closed gap) | 3 |
| W10 | A sentence already carrying `{{` bypasses recovery entirely | 3 |
| W11 | The writer prompt contains 0 numerals outside FACTS and DERIVED FACTS | 2 |
| W12 | The four `rests_on` codes are unreachable under the 4.0.0 schema | 2 |
| W13 | `rests_on` and `explanatory` still work when a package **does** carry explanatory passages (a synthetic package) — the route is dormant, not deleted | 2 |

### Repair

The 12 tests in `04` §8, phase 6. **Test 12 — `MissingGenerationError` becomes a disposition —
lands before repair is enabled anywhere.**

### Compilation and verifier

| # | Test | Phase |
| --- | --- | --- |
| V1 | The accepted regression stays accepted, byte-identical `post.md` | every phase |
| V2 | The reversed comparative stays rejected (`comparative_not_supported_by_text`) | every phase |
| V3 | The compiler/verifier citation disagreement: with `explanatory` dropped, `citation_reused_for_unrelated_claim` is not constructible from any `reported`/`calculated` draft | 2 |
| V4 | The **order-dependence** of that code is captured as a standing regression, so the eventual fix has a test waiting | 2 |
| V5 | Deterministic percentage rendering — the five-row tolerance table | 1 |
| V6 | `DerivedFact.result` keeps full precision while the rendering rounds | 1 |
| V7 | The forward-looking gap now refuses; the four existing probes still refuse | 1 |
| V8 | **S10** — `verifier_gate_digest()` and `len(GATE) == 102` pinned | every phase |
| V9 | Severity census: 96 REFUSE / 2 WARN / 4 ANNOTATE, asserted | every phase |
| V10 | The full adversarial suite (tier C) | 3 |

**V8 and V9 are the "no weakening" enforcement.** They are cheap, they are exact, and they are
what makes the hard constraint testable rather than promised.

### UI and trace

| # | Test | Phase |
| --- | --- | --- |
| U1 | The 9 composition codes have real descriptions and `blocking: true` | 3 |
| U2 | The catalogue AST walk covers `CompositionViolation(...)` | 3 |
| U3 | `compiling_draft` is in **both** `STAGES_BY_PHASE` and `STAGE_LABELS` — a `model_dump` test, since the mismatch raises at serialisation, not construction | 3 |
| U4 | The refusing stage is accurate for all 7 dispositions | 3 |
| U5 | Repair attempts are visible in the trace and the cost panel | 6 |
| U6 | `test_no_response_carries_an_absolute_path` still passes after `call_sites` replaces the two manifest scalars | 6 |

U6 is the one that catches the path leak the redaction loop's own docstring records as having
already shipped once.

---

## 5. What the corpus cannot tell us

Stated plainly rather than hidden in a threshold.

1. **Slot compliance is unmeasured across models.** Two providers, two different failure modes,
   one day, one candidate. Phase 8's tier D is the first real measurement, and the recovery
   design deliberately makes the answer matter less.
2. **Three candidates is a thin corpus.** Tier D's threshold assumes at least three distinct
   `metric_move` candidates can be produced. If not, the threshold changes.
3. **`homes_purchased` is nearly untested** — 2 runs. It is a `count` metric, so
   `DERIVED_PRESENTATION_DECIMALS[homes] = 0` and `scaled_money` does not apply. It should be in
   tier D specifically because it exercises a different unit path.
4. **No candidate in the corpus has counter-evidence or an explanatory passage**, so the
   counterpoint and explanatory routes are untested by anything but synthetic packages (W13).
5. **`ValueSign.UNVERIFIED` metrics are untested end to end.** `cost_of_revenue` and
   `inventory_valuation_adjustment` have zero observations, so `direction_verified=False` can
   only be tested synthetically (P4).

---

## 6. The stability gate

`metric_move` is stable enough to move on to new post types when **all** of these hold on the
tier A + tier D corpus:

| Criterion | Threshold | Measured by |
| --- | --- | --- |
| Verifier checks weakened | **0** | V8, V9 |
| Factual direction inversions reaching the writer | **0** | P1, and the metrics harness |
| Unsupported numbers in an accepted post | **0** | V1, V10 — this is `unbound_numeral`, already a REFUSE |
| Invalid citation bindings in an accepted post | **0** | V3 |
| accepted-post rate, replay (tier A) | **≥ 90%** | harness |
| accepted-post rate, live, per provider | **≥ 70%**, none below **50%** | harness, tier D |
| writer-contract-valid rate after recovery | **≥ 95%** | harness |
| repair-attempt rate | reported, **not thresholded** | harness |

The last line is deliberate. A low repair rate means the contract works; a high one means it does
not and the repair is masking it. Thresholding it would create an incentive to hide the
diagnostic.

**And one release criterion that is not a number:** every remaining refusal in tier D must be
classifiable into one of the five owners in `04` §2, with no residual bucket. A failure nobody
owns is a failure nobody will fix.
