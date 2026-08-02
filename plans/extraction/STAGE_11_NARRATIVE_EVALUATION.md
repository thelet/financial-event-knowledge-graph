# Stage 11 — narrative benchmark evaluation

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 11, §4.0.
**Predecessors:** [STAGE_09_HYBRID_SCOPING.md](STAGE_09_HYBRID_SCOPING.md) (both scopes),
[STAGE_10_NARRATIVE_LANE.md](STAGE_10_NARRATIVE_LANE.md) (the lane), commit `76c8079`.
**Goal:** score the narrative lane on the reviewed benchmark, on the same eight dimensions
the table lane was scored on, **under both candidate scopes**, and produce the evidence
step 13 decides the default on.

This stage measures. It does not tune. If a score is poor, the report says so and the cause
is classified; it does not become a reason to revise the prompt until the number moves —
that is fitting the lane to the gold, and this benchmark is the acceptance instrument, not a
training set.

---

# 1. What is scored

The 13 cases whose `lane` is `narrative`, plus the one whose lane is `either`, plus every
negative case that expects an abstention from prose. Table cases are step 6's and are not
re-scored here.

**Both scopes, same lane, same prompt, same model.** `LexicalOntologyCandidateScope` and
`HybridOntologyCandidateScope` are injected in turn. This is the comparison §9 step 13
decides the runtime default on, and step 9 deliberately left open because reachability alone
could not settle it: hybrid reached required-concept recall 0.980 against lexical's 0.959,
critical-concept recall and ambiguity preservation were 1.000 under both, and the +25% cost
budget did not discriminate anywhere in `top_k` 1–8. Extraction quality is the better
instrument, and this is where it is taken.

# 2. The eight dimensions, plus what prose adds

Scored independently, as §4.0 requires, because a claim can be right about the metric and
wrong about the period and a blended score hides exactly that.

| | |
| --- | --- |
| metric identity | the gold metric was claimed |
| value | the scaled magnitude matches |
| unit | matches the ontology's spelling, now that both lanes agree on it (`76c8079`) |
| scale | the magnitude factor applied matches |
| period | the period key matches |
| subject | entity and type match |
| evidence | the cited `passage_id` resolves, and the quoted span is verbatim in it |
| ambiguity | a case expecting an abstention got one, over **all** the surface's concepts |

**This row is wrong and §8 replaces it with three.** "A case expecting an abstention got one"
is not ambiguity: fifteen expectations, one of which names ambiguity candidates, and the
implementation scored the conjunction as an ambiguity rate. It is now an abstention-honouring
rate, a code-agreement rate, and ambiguity preservation over its own denominator of 1. Two
further dimensions — §7.1's population wording and §7.3's ambiguity codes — belong in this
table and were in neither report; §8 carries all twelve.

Prose adds five the table lane had no need of:

- **structured-output validity** — answers conforming to the schema on the first attempt,
  after a retry, and never;
- **rejection rate by reason**, over the ten categories of §4, separated from **model-chosen
  abstentions** — step 10 made these distinguishable through the protocol and this is what
  the distinction was for;
- **period attribution error (§8a.12)** — the residual step 10 could not close. `period_label`
  is an enum over the phrases the passage prints, so a wrong year is no longer free-form; but
  MD&A prose prints the comparative period too, so the model can still reach for it. Score
  how often it does, and record the distance between the chosen phrase and the quoted
  evidence sentence, which step 10 already records on every claim;
- **ontology warnings** — the `unpreferred_source_lane` warnings that were invisible until
  step 10 relayed them. A narrative claim for a metric that prefers `normalized_table` is not
  an error, and how often it happens is a finding about lane routing;
- **tokens, latency, generation rate** — operational, and see §5.

**`matched_over_emitted` is not precision** and must not be called it, for the same reason
step 6 refuses the word: each case names a deliberate subset of its passage's claims, so an
unmatched claim is usually a right answer the case did not list. Unmatched claims are
**unrequired**, never false positives.

# 3. Determinism — the report is built from persisted answers

Generation is not reproducible (§11.1 of the step 9 brief, and step 10's live gate measured
the same thing: identical claims, differing token counts). The report therefore is **not**
built by generating.

1. A `narrative-build` command generates once per (case, scope) and writes answers to the
   step 10 `AnswerStore`, keyed by request identity.
2. `narrative-report` runs **replay-only** off that store and writes
   `benchmarks/extraction/v1/reports/narrative_lane_v1.{json,md}`.
3. Two consecutive `narrative-report` runs are byte-identical, `implementation_commit` the
   only permitted difference — the same rule as steps 6, 8 and 9.

The answer store is committed under `benchmarks/extraction/v1/answers/` for the same reason
the vectors are: without it the report cannot be regenerated offline, and an artifact nobody
can rebuild is not evidence. Record its size.

**No duration, token count or timestamp may enter the byte-identical artifact.** Step 9 §11.2
settled this. Operational statistics are printed by the command and may be summarised in the
Markdown prose only as ranges explicitly marked session-dependent — step 10 measured the same
letter passage at 1,850 and 1,182 completion tokens on two runs with byte-identical claims.

# 4. Failure classification — ten categories

Every gold claim not matched, and every emitted claim not in gold, lands in exactly one:

| Code | Meaning |
| --- | --- |
| `metric_not_identified` | the gold metric was neither claimed nor abstained over |
| `metric_misidentified` | a different metric was claimed for the same printed figure |
| `value_wrong` | metric and period right, magnitude wrong |
| `scale_wrong` | magnitude wrong by a declared factor — the §8a.5 failure in prose |
| `period_wrong` | metric and value right, period wrong — the §8a.12 residual |
| `unit_wrong` | unit or currency disagrees with the ontology |
| `subject_wrong` | entity or subject type disagrees |
| `evidence_ungrounded` | the quoted span is not verbatim in the passage |
| `ambiguity_collapsed` | a declared-ambiguous surface resolved to one concept |
| `schema_or_parse_failure` | the answer did not conform, or did not parse |

One category per failure, decided in a stated order, so two runs classify the same failure
the same way. Print the order in the report.

# 5. The report

`benchmarks/extraction/v1/reports/narrative_lane_v1.{json,md}`, generated under
`benchmarks/`, never under `extraction/` — the existing test parses every module under
`extraction/` and fails on a benchmark import or path literal.

Structure, per scope and then compared:

- headline: the eight dimensions, the five additions, case and claim counts;
- per case: gold, emitted, matched, unmatched, abstentions, rejections with reasons, and
  every failure with its category;
- **the lexical-versus-hybrid comparison**, claim by claim: what each scope reached that the
  other did not, and whether it was gold. A concept only hybrid supplies that produces a
  correct claim is the strongest evidence hybrid can offer; one that produces a wrong claim
  is the strongest evidence against;
- the §8a.12 period-attribution table;
- the ontology-warning census;
- named probes, as steps 6 and 8 carry: the two `pct_homes_on_market_gt_120_days` paraphrases
  step 9 measured, since this is the first stage that can say whether reaching a concept
  produced a correct claim.

Commands, added to the existing CLI:

```bash
python -m benchmarks.extraction.v1 narrative-build     # generate, needs the server
python -m benchmarks.extraction.v1 narrative-report    # replay-only, offline
python -m benchmarks.extraction.v1 narrative <case_id> # one case, both scopes
```

# 6. Tests

1. The report regenerates byte-identically, twice, offline, from the committed store.
2. The committed report matches a fresh replay — the step 6 defect, which this project has
   now seen twice, in the report and then in a README.
3. No duration, token count or timestamp appears in the JSON.
4. Every scored dimension is computed in exactly one place and the Markdown reads it rather
   than recomputing it — step 6's other defect, where the runner duplicated the verdicts and
   46 `WRONG` rows sat beside a score of 1.000.
5. The failure classifier is total and disjoint: every unmatched gold claim and every
   unrequired emitted claim gets exactly one category.
6. A replay with a missing answer raises, naming the request — it never scores a silence as
   an abstention.
7. Nothing under `extraction/` imports the benchmark.
8. `matched_over_emitted` never appears under the name precision, in any artifact.

Eleven more were added after review found each of these eight passing while the artifact was
wrong. They are listed with the defect each defends against in §11.2, and every one was proven
by re-applying the mutation that had gone undetected. The three worth naming here: the
Markdown must read every score out of `totals` and derive none (checked by substituting an
impossible value and re-rendering, not by grepping for a division); every number in every
rendered table is compared to the object it renders; and a declared unusable answer may never
name a request the store answers.

# 7. Acceptance

- [x] `pytest -m "not live"` green — **1,580**, up 43 from the 1,537 the first pass reached
      and 77 from the 1,503 at `76c8079`. Counts from `--junitxml`; `pytest -q` prints no
      summary in this environment.
- [x] `pytest -m live` green — **49**, unchanged.
- [x] `narrative_lane_v1.{json,md}` written and byte-reproducible offline: two full
      regenerations are byte-identical, and a build with `socket.socket` replaced by a raising
      stub completes, so the offline claim is demonstrated rather than asserted.
- [x] Both scopes scored, and the comparison stated with a recommendation for step 13 —
      **hybrid reaches more and is not clean on what it reaches** (§9 below). This is a
      *changed* recommendation; the first pass said "hybrid, on narrow evidence" and was
      wrong, for the reason §9 gives.
- [x] The lane changed twice, deliberately, and both are correctness fixes rather than
      benchmark tuning — see §12. Everything else outside `benchmarks/` and `tests/` is
      documentation.

**A poor score is a result, not a failure of this stage.** Report it, classify it, and leave
the lane alone. Revising the prompt because a benchmark number moved is the one thing this
stage must not do. §12 states why the two lane changes are not that.

# 8. Results *(2026-08-02, after the §12 rebuild)*

Every number below is in `benchmarks/extraction/v1/reports/narrative_lane_v1.{json,md}` and is
regenerated from the committed answer store.

**Twelve dimensions, where §2 named eight.** The eight are all there. Two are added — §7.1's
population wording and §7.3's ambiguity codes, which no report scored until review found it —
and the one §2 called "ambiguity" is split into the three things it was measuring at once.
Denominators are printed because a rate over 1 and a rate over 15 read identically at three
decimal places.

| Dimension | Denominator (lex/hyb) | Lexical | Hybrid |
| --- | --- | --- | --- |
| metric identity | 19/19 gold | 0.526 | **0.579** |
| value | 10/11 matched | 0.800 | 0.818 |
| unit | 10/11 matched | 1.000 | 1.000 |
| scale | 10/11 matched | 1.000 | 1.000 |
| period | 10/11 matched | 1.000 | 1.000 |
| subject | 10/11 matched | 1.000 | 1.000 |
| evidence | 10/11 matched | 1.000 | 1.000 |
| **population wording (§7.1)** | 3/4 matched pairs whose case declares one | **0.000** | **0.000** |
| **ambiguity codes (§7.3)** | 2/3 matched pairs whose case declares any | 1.000 | 1.000 |
| expected abstentions honoured | 15/15 | 0.867 | 0.867 |
| **ambiguity preserved** | **1/1** | 0.000 | 0.000 |
| **abstention code agreement** | 15/15 | 0.067 | 0.067 |
| matched/emitted *(not precision)* | 23/24 emitted | 0.435 | 0.458 |
| matched/emitted, distinct passages *(not precision)* | 17/18 emitted | 0.588 | 0.611 |

**`period` and the metric half of a match are tautologies**, here and in the committed
table-lane report. Claims are matched to gold on `(metric_id, period key)`, so a matched pair
agrees about both by construction and `period_accuracy` cannot read anything else.
`table_lane_v1.json`'s `period_accuracy: 1.000` has been read as a measurement and is not one;
that report now carries a `score_caveats` block saying so. The real period measurement is
`period_wrong` in the failure classification and §8a.12's attribution table.

**Population wording is 0.000 and that is the headline result of this stage.** Every matched
`pct_homes_on_market_gt_120_days` pair disagrees with gold about the filed denominator: gold
carries the whole clause (`only 8% of our homes had been listed on the market for more than
120 days`) and the lane carries the noun phrase inside it (`our homes`). On every one of them
the emitted wording is a *substring* of the expected one — reported as
`population_contained_in_expected` rather than scored on, because §7.1 says verbatim and a
containment rule is a comparability rule the plan does not state. Whether the reviewers want
the clause or the noun phrase is a founder decision; what is not in doubt is that until
2026-08-02 the two disagreed and the dimension scored 1.000 by not existing.

**`ambiguity preserved` rests on one expectation and reads 0.000.** Fifteen expected
abstentions, of which exactly **one** names ambiguity candidates
(`negative-ambiguous-alias-bare-gross-profit`), and that one is the request whose answer never
conformed — so `AMBIGUITY_COLLAPSED` is structurally unreachable and the rate is 0/1 rather
than a measurement of ambiguity handling. Reported at its true denominator instead of being
folded into a rate over 15.

**`abstention code agreement` is 1 of 15.** The case files and the lane do not share an
abstention vocabulary (`UNAVAILABLE_REQUIRED_SOURCE_LANE`, `AMBIGUOUS_COLUMN_ORDER`,
`SCALE_SOURCE_ONLY`, `THIRD_PARTY_ROLE` and three more are not in
`stages/narrative/public.ISSUE_CODES`), so this is a measurement of the two vocabularies
against each other and not of the lane. It is reported separately for exactly that reason;
`honoured` remains a conjunction of checkable conditions and not a code comparison.

## 8.1 Counts

| | Lexical | Hybrid |
| --- | --- | --- |
| cases | 14 | 14 |
| distinct passages behind them | 13 | 13 |
| gold observations | 19 | 19 |
| emitted observations | 23 | 24 |
| emitted, each passage counted once | 17 | 18 |
| matched | 10 | 11 |
| missed | 9 | 8 |
| unrequired emitted *(unscored, not errors)* | 13 | 13 |
| keys where two claims collided and the matcher chose | 0 | 0 |
| claims the lane rejected | 45 | 43 |
| abstentions the model chose | 43 | 43 |
| …less lane decisions taken with no answer to reject | 39 | 39 |
| ontology warnings | 15 (10 distinct) | 15 (10 distinct) |
| **ontology validation errors** | **0** | **0** |
| classified failures | 15 | 15 |

**Two reviewed cases annotate one passage.** `population-our-homes-q4-2021` and
`letter-prose-multiple-metrics-q4-2021` both annotate `q42021formxex992sharehol.htm#p10`, so
the 14 cases cover 13 distinct passages and every claim, warning and error on that passage
enters the per-case totals twice. Gold and matched observations are per case and are
unaffected. Both forms of every affected count are now reported, including a second
`matched/emitted` — until 2026-08-02 the duplication was disclosed for the errors alone.

# 9. Lexical versus hybrid — the recommendation, restated

**The two scopes issue the identical request on 13 of the 14 cases.** Measured as the request
digest the answer store is keyed on, not inferred from the concept lists: the digest covers
the prompt, the schema, the model and the sampling parameters, so on those 13 the two "runs"
are literally the same stored answer. One case (`event-executive-change-ceo-2025`) issues no
request under either scope. The comparison rests on one case.

On that case — `letter-prose-inventory-and-120d-q2-2022` — hybrid supplies
`pct_homes_on_market_gt_120_days`, which lexical cannot reach, and the lane emits a claim for
it: 5 percent at 2022-06-30, matching gold on value, unit, scale, period, subject and
evidence, and **failing on population wording**. Gold declares
`5% of our homes were listed on the market for more than 120 days`; the claim carries
`our homes`.

**Recommendation: hybrid reaches more and is not clean on what it reaches.** The first pass of
this stage recommended "hybrid, on narrow evidence" on the strength of a claim it described as
"correct on every dimension". That description was true of the six dimensions then being
scored and false of the claim: population wording and ambiguity codes were not scored, and
population wording is exactly what it gets wrong. The recommendation is restated on the fuller
set rather than preserved.

Three qualifications belong with it:

1. The evidence is one concept in one passage. Nothing else in the benchmark distinguishes the
   two scopes at all, and no gold observation is clean under one scope and not the other.
2. `value_accuracy` and `metric_identity_accuracy` are both **higher** under hybrid, and
   neither means hybrid corrected anything. Adding one matched claim moves the denominator:
   value goes 8/10 → 9/11. The report no longer lists a moved ratio as a dimension hybrid
   improved, and the recommendation is decided per claim.
3. **The §16.1 wording gap is not closed.** The second paraphrase probe,
   `population-our-homes-in-inventory-q1-2023`, is unreached under *both* scopes: the concept
   sits at rank 2 in the passage-level ranking and `top_k` is 2, so the cap excludes it.
   §8a.11 predicted this and this run confirms it end to end.

# 10. What this run found, beyond the scores

**V1 §11 acceptance criterion 1 now holds for the narrative lane.** Zero ontology validation
errors under both scopes, against four `DUPLICATE_OBSERVATION_CONFLICT` errors before the §12
rebuild. The cause of those four was the §8a.12 residual — the letter reports a quarter and a
full year, the passage prints no phrase resolving to a full year, and the model attached
`4Q21` to the full-year figures. §12.1 gave the schema a way to say "no printed phrase gives
this figure's period"; the model uses it, the full-year figures are refused rather than
misdated, and the collision is gone.

**One answer still never conforms, and the stated cause was wrong.**
`negative-ambiguous-alias-bare-gross-profit` truncates with `finish_reason: length`. The first
pass attributed this to a prompt larger than the worst case the fixed 4,096-token output
budget had been sized against. **Prompt length is not the discriminator** *(measured
2026-08-02 by tokenizing all 13 prompts on the running server)*:
`letter-prose-run-together-kpi-row-q1-2025` has a **larger** prompt — 4,806 tokens against
this passage's 4,299 — and conforms. Completion length is the discriminator. §12.2 made the
budget prompt-aware, and re-issued under it the same request runs 4,311 prompt + 3,601
completion = 7,912 of the 8,192-token slot: the slot is no longer the constraint and the
answer still truncates. Recorded as declared data in `narrative_runner.UNUSABLE_ANSWERS` and
replayed by raising the same `ProviderResponseError`, because a request whose answer never
conformed has no row in the answer store. A declared failure and a stored answer are now
enforced to be disjoint — see §11.

**Period attribution, measured.** 5 of 23 lexical claims (6 of 24 hybrid) took their period
phrase from inside the quoted evidence sentence; the rest reached up to 1,354 characters
backwards. Only 1 of 23 resolved to something other than the passage's reporting period, and
that count is an upper bound — `reporting_period_keys` is the latest printed end date of each
type, which on `event-credit-facility-established-2022` is a *maturity* date, so a correctly
dated claim counts as comparative there. The dominant failure is not the comparative period.

**Two of the four `period_wrong` failures are the unrepresentable-FY2021 shape, not three.**
The four are `adjusted_gross_margin@FY2021`, `adjusted_gross_profit@FY2021`,
`adjusted_gross_profit@2020Q4` and `pct_homes_on_market_gt_120_days@2022-12-31`. The third is
a plain comparative miss — `4Q20` **is** printed on that passage and resolves — and the fourth
is on a different passage entirely. The first pass of this brief and V1 §8a.13 both said
three; both are corrected.

**Step 10's definitional gate result was structural, and this run shows the non-structural
case.** `population-portfolio-mdna-fy2023-10k` is a 10-K MD&A definitional passage that *does*
print resolvable periods, and the lane emitted a claim from it. Whether that is a lane failure
or a case error is a question for the reviewers and is recorded in the report's "What this run
puts back to the reviewers" section: the passage's own sentence is "As of December 31, 2023,
such homes represented 18% of our portfolio", which reads observational, while the case says
the value "is reported in the KPI table at open-20231231.htm#p105, not here". **Not acted
on** — changing a benchmark annotation is a founder decision. It is scored under the case as
written, which is the harsher reading, and the disclosure is kept.

**One matched pair carries an ambiguity code its case does not declare.**
`recon-shareholder-letter-table-as-prose` annotates `pct_homes_on_market_gt_120_days` with no
`ambiguity_codes`, while the ontology declares `pct_120_days_denominator` on the metric and
§7.3 attaches it at assembly. Counted and reported; **not** scored against the lane, which had
no part in the disagreement. The §7 dimensions are asked only where the reviewed case declares
something, for exactly this reason.

**`NarrativeIssue.rejected_claim` distinguishes two states where three are needed.**
`UNRESOLVED_METRIC` and `PROMPT_EXCEEDS_CONTEXT` (decided before a request exists) and
`MODEL_ANSWER_UNUSABLE` (decided after an answer proved unusable) all carry
`rejected_claim: False`, so a naive reading counts them as abstentions the model chose. The
corrected figure is now in the JSON as `model_chosen_abstentions` — 39 under both scopes
against 43 non-rejections — rather than in the Markdown prose alone, where no consumer could
check it. Recorded, not worked around: widening the flag is a change to the lane.

# 11. Corrections to this brief and to the first pass

## 11.1 To the brief as written

| §  | What the brief says | What is true |
| --- | --- | --- |
| §1, §5 | "roughly 28 generations (14 cases × 2 scopes)" | **13 distinct requests, 26 provider calls.** The scopes agree on 13 of 14 cases so the requests are identical; two cases annotate the *same passage* and share one request; and one case (`event-executive-change-ceo-2025`) issues no request at all, because neither scope offers it a metric concept and the lane refuses before reaching a provider. |
| §2 | structured-output validity counts answers conformant "after a retry" | **Not a reachable state.** `_post_with_retries` retries transport failures and 5xx only; an unparseable or schema-violating answer raises on the first response and is never retried, because an identical request at temperature 0 cannot change. Reported as structurally zero, not measured as zero. |
| §2 | eight dimensions, one of them "ambiguity" | **Twelve, and "ambiguity" was three things.** §7.1's population wording and §7.3's ambiguity codes were scored by nothing, and the ambiguity dimension was an abstention-honouring rate over fifteen expectations of which one names ambiguity candidates. See §8. |
| §4 | "every gold claim not matched, **and every emitted claim not in gold**, lands in exactly one" category | The second half contradicts §2 of the same brief, which forbids treating an unmatched emitted claim as a false positive. Classifying all unrequired keys as failures would make `matched_over_emitted` a precision under another name. The classified set is: missed gold, matched-but-wrong, and claims that break a silence a case requires. |
| §1 | the `lane: either` case is scored beside the narrative ones | Its passage `q12021form8-kxexhibit991.htm#p20` has `passage_kind: table`. `OntologyGuidedNarrativeClaimLane.supports()` requires `narrative`, so typed selection would never route it here; the report reaches it through `extract_passage` and says so. It is also the one request that truncates. |
| §7 | "No change to the lane" | **Two changes, both correctness fixes, both in §12.** A schema that cannot express a true state and a request that structurally cannot fit are defects, not scores. |

A fifth about a *predecessor*: the table-lane report's numbers are `metric_recall`,
`matched_over_emitted` and six per-match dimensions — it never scored `ambiguity`, which §4.0
names as one of the eight. That report now says so in its prose and in a `score_caveats` block,
V1 §4.0 is corrected, and STAGE_06 §9 records it.

## 11.2 To the first pass of this stage *(found by adversarial review, 2026-08-02)*

Each is fixed, each has a test that fails without the fix, and each test was proven by
re-applying the reviewer's mutation and watching the suite go red.

| # | Defect | Fix and test |
| --- | --- | --- |
| A1 | `UNUSABLE_ANSWERS` was consulted before the answer store and nothing checked the two were disjoint, so one entry pointing at an answered request silently deleted that answer — metric identity 0.526/0.579 → 0.316/0.368, four ontology errors gone, suite green | disjointness enforced in `_ReplayWithRecordedFailures.__init__`; `test_a_declared_unusable_answer_never_shadows_a_stored_one` |
| A2 | three Markdown tables — the lexical-vs-hybrid comparison step 13 reads, Counts, and the failure census — were checked by nothing; `+0.4` on the comparison produced a row contradicting the headline with the suite green | `test_every_rendered_table_number_is_the_number_it_renders` compares all four tables cell by cell against the object each renders |
| A3 | "the runner computes no score of its own" was an AST grep for `Div`/`FloorDiv`; `statistics.fmean(...) * 0.5` passed it and printed `evidence_accuracy 0.500` beside verdicts that make it impossible | `test_the_markdown_reads_every_score_out_of_totals_and_derives_none` substitutes an impossible value into `totals["scores"]` and re-renders; the grep is kept as the cheap half |
| A4 | nothing pinned a failure to a category: reversing `DECISION_ORDER` and making `_classify_miss` fall back to `VALUE_WRONG` both changed the artifact and stayed green | tuple equality on `DECISION_ORDER`, the printed order parsed back out of the Markdown, five named `(case, metric, category)` triples, and classifier unit tests for the fallback and for order precedence |
| A5 | `"resolves": true` was a literal on all 28 emitted claims including the 18 unmatched ones, where evidence resolution had never been computed | computed per claim in the scorer and read by the renderer; `test_evidence_resolution_is_read_for_every_emitted_claim_not_asserted` |
| B1 | §7.1 and §7.3 were scored by nothing | two new scored dimensions, applicability driven by what the case declares; five tests |
| B2 | `_recommendation` inferred "no dimension fell" from aggregate ratio deltas, so one added match listed `value_accuracy` as improved by hybrid | per-claim comparison, `claims_added_by_one_scope` and `per_claim_regressions` in the JSON, the ratio keys renamed to say they are ratios |
| B3 | the `ambiguity` dimension measured whether any issue was recorded anywhere | renamed to `abstention_honoured_rate`, code agreement reported separately, ambiguity preservation scored over its own denominator of 1 |
| B4 | `by_key` was a dict comprehension, so the *last* claim under a key won silently and the only `value_wrong` was that choice | `_index_by_key` keeps the first in emission order and records every collision; disclosed wherever `value_accuracy` appears |
| B5 | two cases annotate one passage and only the errors disclosed it | distinct forms of every affected count, a second `matched/emitted`, and the disclosure in the headline |
| B6 | `cases_with_identical_scope` inferred prompt identity from metric concepts | `request_identity` threaded onto `NarrativeExtraction` and compared directly; 13 of 14, `letter-prose-inventory-and-120d-q2-2022` the sole difference |

# 12. The two lane changes, and why they are not benchmark tuning

Both are defects the benchmark *found*; neither moves a number by changing what the lane is
asked to do.

## 12.1 `period_label` could not express a period the passage does not print

Step 10 narrowed `period_label` to the phrases a passage prints, which removed every invented
period. It also removed the model's only way to say **"none of these is this figure's
period"** — a state the corpus has. On `q42021formxex992sharehol.htm#p10` the phrases are
exactly `['December 31, 2021', '4Q21', '4Q20']`, the letter reports a quarter *and* a full
year, and the model labelled "For the year, we delivered Contribution Profit of $525
million…" as `4Q21`. Result: `contribution_profit` at both $152M and $525M for 2021Q4 and
`contribution_margin` at both 4.0 and 6.5 — four `DUPLICATE_OBSERVATION_CONFLICT` errors, and
V1 §11 criterion 1 failing.

`public.PERIOD_NOT_PRINTED` is now the enum's last member on every passage, rule 6 names it,
and `response_mapping._resolve_period` maps it to `MISSING_PERIOD` — checked **before** the
period-type test, because a model declining to name a period has not also made a claim about
that period's kind. `PROMPT_VERSION` is 1.2.0, so every answer produced under 1.1.0 is
unreachable rather than silently re-used.

This is a correctness fix: the schema could not express a true state, and the only
representable answers were wrong ones. It does **not** make a wrong period unrepresentable —
a comparative paragraph still prints its prior-period phrase and the enum still offers it.

## 12.2 The output budget could not fit the largest prompts

`DEFAULT_MAX_OUTPUT_TOKENS` was a fixed 4,096 against an 8,192-token slot, justified by a
comment reasoning from a measured worst-case prompt of 3,529 tokens. Prompts had outgrown
that, so a request could be issued whose prompt plus budget did not fit — and the first pass
read the one truncation as being caused by that. It was not: the largest prompt conforms.

`narrative_lane.output_budget` now returns `min(ceiling, context_tokens − estimated prompt
tokens)`, the estimate being the prompt's character count over a ratio (3.5) below every one
measured on the running server (3.57–4.55 across all 13 prompts). It is pure and deterministic
because `max_tokens` is part of the request digest the answer store is keyed on. A request
whose budget falls below `MIN_OUTPUT_TOKENS` is refused with a new `PROMPT_EXCEEDS_CONTEXT`
issue rather than issued to be truncated — a lane decision taken with no model involved, and
recorded as such.

The truncating request still truncates, now demonstrably at the budget and not at the slot,
and the report says so. Making a long completion short is not something a budget can do.

## 12.3 What the rebuild moved, and why

Every number below is lexical/hybrid, before → after. "Before" is the first pass of this
stage, at `PROMPT_VERSION` 1.1.0 with the fixed budget.

| | Before | After | Cause |
| --- | --- | --- | --- |
| ontology validation errors | 4/4 | **0/0** | §12.1. The full-year figures are refused with `MISSING_PERIOD` instead of being dated `4Q21`, so no two claims collide under one observation id. **V1 §11 criterion 1 now holds.** |
| emitted observations | 28/30 | 23/24 | §12.1. Five claims the model can no longer date are refused. Four of the five were unrequired; one was the duplicate `contribution_profit`. |
| value | 0.900/0.909 | 0.800/0.818 | Two causes in opposite directions. `contribution_profit@2021Q4` becomes **correct** (152M, matching gold, where the duplicate previously displaced it), and `recon-shareholder-letter-table-as-prose` becomes **wrong on two** — 12,788 for `housing_inventory_homes` where gold is 5,326, and 55 for `pct_homes_on_market_gt_120_days` where gold is 18, both read from the wrong column of a run-together KPI row. That case's answer changed because the prompt changed; it is a generation move, not a scoring one, and it is reported rather than tuned away. |
| metric identity | 0.526/0.579 | 0.526/0.579 | Unchanged, and *which* ten matched changed: the FY2021 observations were never matched under either prompt. |
| unit, scale, period, subject, evidence | 1.000 | 1.000 | Unchanged. |
| population wording | *not scored* | 0.000/0.000 | B1. The dimension did not exist. |
| ambiguity codes | *not scored* | 1.000/1.000 | B1. §7.3 attaches them at assembly and they match what the cases declare; the *lane claim* carries none, which the report now shows beside it. |
| ambiguity → abstentions honoured | 0.867/0.867 | 0.867/0.867 | B3. Same conjunction, honest name. |
| ambiguity preserved | *folded into the above* | 0.000/0.000, n=1 | B3. |
| abstention code agreement | *reported per case only* | 0.067/0.067 | B3. |
| matched/emitted | 0.357/0.367 | 0.435/0.458 | Fewer emitted claims (§12.1), same matched count. Not an improvement in anything: the denominator shrank. |
| classified failures | 12/11 | 15/15 | Three population-wording failures appear (B1) and the categories redistribute; `metric_misidentified` 2/2 → 5/6. |
| period attribution: claims | 28/30 | 23/24 | §12.1. |
| period attribution: comparative | 2/3 | 1/1 | §12.1 plus the recon case's changed answer. |
| answer store | 12 answers, 57,850 bytes | 12 answers, 57,850 bytes | Regenerated whole at `PROMPT_VERSION` 1.2.0; the digests all changed, the row count did not. |
| scopes issuing one request | 13 of 14 *(by concept)* | 13 of 14 *(by digest)* | B6. The stronger claim holds. |

# 13. Non-goals

Events and relationships (step 12). The integrated run and catalogs (step 13). Changing the
prompt, the schema, the scopes, the ontology or the benchmark **to move a number**. A second
model.
