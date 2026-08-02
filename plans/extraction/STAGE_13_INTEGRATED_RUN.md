# Stage 13 — the integrated run, the catalogs, and the scoping decision

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 13, §4.6, §5.
**Predecessor:** [STAGE_12_EVENTS_AND_RELATIONSHIPS.md](STAGE_12_EVENTS_AND_RELATIONSHIPS.md),
commit `a986321`; gold-convention repair `4482982`.
**Goal:** run all three lanes over the whole normalized corpus, write an immutable manifest
and rebuilt catalogs under `data/extraction_runs/<run_id>/`, verify the run against the
ontology, and **decide lexical versus hybrid** on the extraction evidence steps 11 and 12
already produced.

**Status: done, 2026-08-02.** §11–§17 record what was built, the run's numbers, four
corrections to this brief, the two defects the run's own verification found, and the decision.
Read §14 before trusting §1–§10: two statements in them are wrong and are corrected there.
**§18 closes §13.2** — `observation_id` now digests grid position, `duplicate_identities` goes
from 82 failures to 0, and `conflicting_duplicates` still reports its 4. §12's numbers predate
both `23216f8` and §18; where they disagree with §18.5, §18.5 is the measurement.

**This stage produces inspectable extractions, not only scores.** A run directory whose
`report.md` is good and whose `claims.jsonl` cannot be read back claim by claim has not
satisfied step 13.

---

# 1. What the run consumes, and what it must not

| Input | Source | Note |
| --- | --- | --- |
| passages | `data/normalization_catalog/passages.jsonl` | 12,442 passages, 294 documents |
| ontology | `real_estate_marketplace_v1`, `definition_hash e8d4af709be2…` | 131 concepts, 26 metrics, 20 in scope |
| table lane | `DeterministicTableClaimLane` | offline, no provider |
| narrative lane | `OntologyGuidedNarrativeClaimLane` | provider, replay where an answer exists |
| event lane | `OntologyGuidedEventLane` | same provider port |
| scoping | lexical **and** hybrid | ~~both run~~ — **corrected, see §14**: hybrid cannot be run over the corpus offline, and §6 never needed it to be |

**The benchmark is not an input.** No module under `extraction/` may name it, and the run
must not read `benchmarks/` for anything. *(Corrected in §14: this and §1.1 contradict each
other. Settled in favour of §1.1, narrowed to answers — a request digest and a raw response,
never a case, a gold claim or an expected abstention, and the path stated in configuration.)* The comparison in §6 is made *from committed
benchmark reports* by the reporting code, never by the pipeline.

## 1.1 Provider policy for this stage

The founder's constraint: **do not regenerate provider answers unless a changed prompt or
schema makes it necessary.** Neither changed in `4482982` — the repair touched gold, a report
renderer and tests. So:

- `PROMPT_VERSION` and `EVENT_PROMPT_VERSION` are unchanged, therefore every stored request
  digest is unchanged, therefore the committed answer stores replay.
- The corpus is far larger than the benchmark's 26 cases, so **most passages have no stored
  answer**. A run over the whole corpus must either call the provider for them or record them
  as not attempted. Calling the provider ~3,000 times is not what this stage is for.

**Decision: the run is bounded by what has an answer, and says so.** Every candidate passage
is selected, scoped and routed for real; a passage whose request has no stored answer is
recorded in `issues.jsonl` with `NO_STORED_ANSWER` and appears in the report's coverage table.
The table lane, which needs no provider, runs over the **whole** corpus. This keeps the run
honest — the alternative is a run that silently looks complete because it only visited what
was cheap.

# 2. The run directory

```text
data/extraction_runs/<run_id>/
  lane_outputs.jsonl     the authoritative per-item record; the catalogs are built from it (§14)
  manifest.json          immutable; config hash, code commit, ontology hash, corpus id, environment
  claims.jsonl           every accepted OntologyClaim, all three kinds
  observations.jsonl     the metric_observation payloads
  events.jsonl           the event payloads
  relationships.jsonl    the relationship payloads
  evidence.jsonl         one row per evidence reference, joinable to a claim id
  issues.jsonl           every recorded refusal and diagnostic, with its code
  rejected_claims.jsonl  payloads a lane produced and validation refused, with the reason
  report.md              the human artifact
```

`<run_id>` is deterministic and readable, derived from the config hash and corpus id — not a
timestamp, or two runs of the same inputs could not be compared by path.

**Atomic finalization** (§Determinism): stage into `<run_id>.partial/`, write the completion
marker last, rename into place. A partial result must be unambiguously incomplete.

**Catalogs are derived indexes, rebuilt and never appended to.** Per-item metadata is
authoritative. A rebuild from the same persisted lane outputs must be byte-identical, and §7
proves it.

# 3. Identities in the durable outputs

Every row in every catalog carries the deterministic id of what it describes:

| Catalog | Key |
| --- | --- |
| `claims.jsonl` | `claim:{kind}:{digest12}` |
| `observations.jsonl` | `obs:{metric}:{subject}:{period_key}:{lane}:{position_digest12}` — digest over `(passage_id, row, column)`; see §18 |
| `events.jsonl` | `evt:{type}:{occurred_on-or-undated}:{digest12}` |
| `relationships.jsonl` | `rel:{predicate}:{source}:{target}:{digest12}` |
| `evidence.jsonl` | its claim id, plus `passage_id` |

Step 12 found `event_id` colliding on the real corpus and found both id fields missing from
the durable report. **Duplicate identity is therefore a first-class verification here** (§5),
not an assumption.

# 4. Coverage the report must state

- **All 20 in-scope metrics**, each with its claim count — including the ones that produced
  **zero**, which is the number a coverage table exists to show. The 6 xbrl-first metrics are
  listed separately as deferred, with `DEFERRED_REQUIRED_SOURCE_LANE`.
- **Every candidate passage that produced no claim, with the reason.** Grouped by reason code
  so the tail is readable: `NO_STORED_ANSWER`, `UNRESOLVED_METRIC`, `AMBIGUOUS_ALIAS`,
  `MISSING_PERIOD`, `DEFERRED_REQUIRED_SOURCE_LANE`, and the rest of `ISSUE_CODES`.
- Counts by lane, by document type, and by claim kind.

## 4.1 The temporal residual, recorded not fixed

Step 12 left one known behaviour: on a non-gold `workforce_reduction` the model answers both
date questions with a single printed phrase from a sentence that says only "announced". Per
the founder's instruction this stage **does not build a classifier**. It must instead:

- emit an explicit diagnostic in `issues.jsonl` for every event carrying both `occurred_on`
  and `announced_on` **equal to each other** — the shape the residual takes;
- keep the raw evidence and each field's chosen phrase visible in `events.jsonl`;
- name a general announcement-versus-occurrence verifier as follow-up in the report.

Equal dates are not proof of the defect — a change can be announced the day it takes effect —
so the diagnostic is a *flag for review*, not an error, and the report must say which.

# 5. Verification the run performs on itself

Each is a run-level check whose result goes in the manifest and the report:

1. **Evidence resolution** — every evidence `passage_id` exists in the corpus and the quoted
   span, where present, occurs in it.
2. **Ontology validation** — every claim through `validate_claim`; errors and warnings counted
   by code. V1 §11 criterion 1 requires **zero errors**.
3. **Forbidden lanes** — no observation carries a `source_lane` its metric forbids. The 6
   xbrl-first metrics must produce no normalized-lane observation.
4. **Duplicate identities** — no two distinct payloads share an id, in any catalog. A
   collision merges two facts silently, which is why it is checked rather than trusted.
5. **Conflicting duplicates** — same `(metric, subject, period)` on one passage and one lane,
   with different values; the `DUPLICATE_OBSERVATION_CONFLICT` step 11 met. *(This is what §5
   always said and what the code did **not** do: it keyed on `observation_id` until §18. The
   two agree now, and the difference stopped being cosmetic the moment the id gained a row
   discriminator — see §18.3.)*

# 6. The scoping decision

Decided here, from evidence already committed — **not** from a fresh benchmark run, and never
from gold reaching runtime.

| Evidence | Source |
| --- | --- |
| reachability | `lexical_scope_v1.json`, `hybrid_scope_v1.json` — 0.960 vs 0.980 required-concept recall |
| metric extraction quality | `narrative_lane_v1.json`, both scopes |
| event/relationship extraction | `event_relationship_v1.json`, both scopes |
| corpus cost | this run, both scopes: scope size, candidates, requests |

**The standing recommendation, to be confirmed or overturned by the numbers**: *hybrid reaches
more and is not clean on what it reaches.* The two scopes issue identical request digests on
13 of 14 narrative cases and on **all** event cases (the event lane takes no scope at all), so
the entire metric comparison is one case. The report must state the comparison's *width*
beside its result — a decision resting on one case is a weak decision and must be labelled
one, whichever way it goes.

`config/extraction.yaml` records the outcome. Changing the default is a configuration change,
not a code change.

# 7. Determinism

- The run writes lane outputs to the staging directory; the catalogs are built **from those
  persisted outputs**.
- **Build the catalogs twice from the same persisted lane outputs and prove byte identity.**
  This is the claim step 13 has to demonstrate, not assert.
- No timestamp, duration, token count or absolute path in any catalog or in `report.md`.
  `manifest.json` is the one place a run may record its environment, and it is immutable.
- Regenerating `report.md` from a finished run directory is byte-identical.

# 8. Tests

1. A run directory has every file §2 names, and the completion marker is written last.
2. A partial run is unambiguously incomplete — no marker, and the loader refuses it.
3. Catalogs rebuild byte-identically from the same lane outputs.
4. Every catalog row carries the id §3 requires; a row without one fails.
5. No duplicate id in any catalog.
6. The coverage table names all 20 in-scope metrics, zeros included, computed from the
   ontology rather than listed.
7. Every no-claim candidate has a reason code drawn from `ISSUE_CODES`.
8. The five §5 verifications each fail when their condition is violated.
9. `report.md` numbers are computed from the catalogs and checked — the step 11 and step 12
   finding, in a stage that writes more tables than either.
10. Nothing under `extraction/` reads `benchmarks/`.

# 9. Out of scope, and a founder gate if it becomes necessary

XBRL, graph construction, Neo4j, Graphiti, RAG, semantic search, UI, investor posts, entity
resolution, a larger-model comparison, and any broadening of the 4-event/2-relationship proof.

# 10. Deferred findings, recorded for later review

Not fixed here; none blocks a trustworthy catalog.

- The announcement-versus-occurrence verifier (§4.1) — needs a way to read that a sentence
  *reports* an announcement, which is a reading rather than a constraint.
- The step 12 adversarial review was **stopped part-way** and is incomplete; roughly the first
  third of the mutation list ran. Stage 12 has not had a full adversarial pass.
- §4.0a's `population.definition_raw` span convention remains open. Step 12's property-span
  decision (`4482982`) settles the *property* case only and deliberately does not imply it.
- `relationship_instance_id` slugs underscores to hyphens in its readable segment — cosmetic.
- `change_kind` is unpopulatable under the printed-value rule; no gold asks for it.

---

# 11. What was built *(2026-08-02)*

| Module | Responsibility |
| --- | --- |
| `extraction/core/config.py` | `config/extraction.yaml` as one document, the run's paths, and a `config_hash` over exactly what was on disk |
| `extraction/core/corpus.py` | `passages.jsonl` parsed once; rows for the selector, text for the lanes, the `PassageSource` port for `verify`, and the corpus identity a run id derives from |
| `extraction/core/run_directory.py` | `<run_id>.partial/` → marker last → rename; `RunDirectory` refuses a directory with no marker |
| `extraction/core/manifest.py` | the immutable manifest and the only place environment is recorded |
| `extraction/stages/extract/` | `public.py` (issue vocabulary, `RunIssue`, `PassageOutcome`), `lane_execution.py` (the three lanes over the candidates), `lane_outputs.py` (the durable record and its only reader) |
| `extraction/stages/catalog/` | `public.py` (seven files, the identity of a row, `render`), `jsonl_catalog.py` (rows from persisted lane outputs through `assemble`) |
| `extraction/stages/verify/` | the five §5 checks, each with a denominator |
| `extraction/stages/report/` | `run_report.py` computes, `markdown_report.py` formats and computes nothing |
| `extraction/context.py` | composition root; the only place a provider is built |
| `extraction/pipeline.py` | ordering, and nothing else |
| `extraction/cli.py`, `__main__.py` | `run`, `runs`, `inspect`, `claim`, `filter`, `issues`, `rejected`, `report`, `rebuild` |
| `benchmarks/extraction/v1/scoping_decision.py` | §6, from the four committed reports, outside `extraction/` by rule |

Offline suite **1,913 passed** (was 1,839), 60 live deselected. *(1,924 after §18.)*

# 12. The run *(2026-08-02)*

`extract-v1-lexical-2422c4252c07` — deterministic, derived from the config hash, the corpus
identity `294d-12442p-9992c4eb1888` and the ontology hash `e8d4af709be2…`. No timestamp.

| | Value |
| --- | --- |
| candidates | **11,848** — 503 tables, 3,072 narrative, 8,273 events |
| candidates that produced a claim | 154 |
| claims | **2,725** — 2,715 observations, 6 events, 4 relationships |
| evidence references | 2,725 |
| issues | 16,930 |
| refused payloads | 46 |
| recorded answers replayed | **14 of 15** |
| in-scope metrics with at least one observation | 17 of 20 |
| events flagged for temporal review | 1 |

**Zero on three in-scope metrics**, which is the number the coverage table exists to show:
`acquisition_contracts`, `home_price_appreciation`, `mortgage_rate`. All six xbrl-first metrics
emitted nothing and 358 `DEFERRED_REQUIRED_SOURCE_LANE` refusals were recorded against them, so
the deferral is demonstrated rather than assumed.

**The fifteenth recorded answer is unreachable under the lexical scope and that is correct**
*(verified 2026-08-02 by driving both scopes over the passage)*: `bc72502e…` is the
hybrid-scoped request for `letter-prose-inventory-and-120d-q2-2022`; under lexical the same
passage issues `48a4447d…` and replays it. Nothing is lost and nothing is silent.

Issues by code: `NO_STORED_ANSWER` 10,852 · `UNRESOLVED_METRIC` 4,811 · `AMBIGUOUS_ALIAS` 461 ·
`DEFERRED_REQUIRED_SOURCE_LANE` 358 · `MISSING_PERIOD` 229 · `DERIVED_CHANGE_COLUMN` 174 ·
`DERIVED_COMPARISON` 12 · `DEFINITIONAL_NOT_OBSERVATIONAL` 11 · `QUOTED_SPAN_NOT_IN_PASSAGE` 10 ·
`PROPERTY_VALUE_NOT_IN_PASSAGE` 4 · `GUIDANCE_NOT_REPORTED` 2 ·
`VALUE_CONTRADICTS_QUOTED_TEXT` 2 · `ANNOUNCEMENT_EQUALS_OCCURRENCE` 1 ·
`AMBIGUOUS_COLUMN_ALIGNMENT` 1 · `PARTICIPANT_NOT_NAMED` 1 · `UNIT_CONTRADICTS_ONTOLOGY` 1.

## 12.1 Determinism, demonstrated

Two independent full runs, same inputs. **The hashes below are the `c687bea` run's**; the current
ones are in §18.5, where the three files no payload id reaches — `events.jsonl`,
`relationships.jsonl`, `rejected_claims.jsonl` — still carry these exact digests.

```text
IDENTICAL lane_outputs.jsonl       1cfc36b33dc11252c918f9c6c4924704d0bd11d6adeb73d8b896b5686779a05b
IDENTICAL claims.jsonl             06fb19a304020d9555cd1b55902e5d225de236e7370f24ede9b65231297e0257
IDENTICAL observations.jsonl       2f1057c2fb617472073495973f0a14d00596661dfdc2b589c4e277080ac5d994
IDENTICAL events.jsonl             b1eaed303fc6af24bfb4b331e4e0d5e40d659929d2f8413de9c61f14698166b8
IDENTICAL relationships.jsonl      bf095051992e7315e03ac8a6c17eabbf59192cae05eba4bdde9ebf97437f0104
IDENTICAL evidence.jsonl           adf94df939dfb521ba478527cadfc2860ede41170920f8dab880a0ac58ff4972
IDENTICAL issues.jsonl             c828b30257538da57a647e39aaaeaa5ad6d4a95454c0a1d42011c478327671e7
IDENTICAL rejected_claims.jsonl    45f6c2562ff8956c286121803baa809176fa7980ea354ea2b541c842764e3238
IDENTICAL report.md                6bdbed5b7ed2f1acf896e69b40fef00da9ef78acfee83c1870f5c0703abd978d
```

`manifest.json` differs in `created_at` and in nothing else, which is the design: it is the one
file permitted to record the clock, and it is therefore not part of the byte-identity claim.
`run.complete` differs with it, because the marker digests the manifest.

`python -m extraction rebuild` rebuilds all seven catalogs from `lane_outputs.jsonl` and reports
`identical` on each. `python -m extraction report` regenerates `report.md` byte-identically.

**YAML comments are not configuration.** Rewriting the comments in `config/extraction.yaml`
left the run id unchanged, because `config_hash` digests the parsed document. Correct, and worth
recording: a run id that moved when a comment moved would be useless for comparison.

## 12.2 The five self-verifications — three pass, two fail

**Superseded twice; kept as the record of what the first run found.** The counts below are the
`c687bea` run. `23216f8` (header spans) moved them to 25,344 rows / 82 / 4, and §18 moves them
again to 25,344 / **0** / 4. §18.5 is the current measurement.

| Check | Result | Examined | Findings |
| --- | --- | --- | --- |
| evidence resolution | **pass** | 2,725 anchors | 0 |
| ontology validation | **pass** | 2,725 claims | 0 errors, **186 warnings**, all `unpreferred_source_lane` |
| forbidden lanes | **pass** | 2,715 observations | 0 |
| duplicate identities | **FAIL** | 25,151 rows | 105 `DUPLICATE_IDENTITY` — 71 in `claims.jsonl`, 25 in `observations.jsonl`, 9 in `evidence.jsonl` |
| conflicting duplicates | **FAIL** | 2,715 observations | 9 `DUPLICATE_OBSERVATION_CONFLICT` |

V1 §11 criterion 1 holds: **zero ontology validation errors over the whole corpus**, across all
three claim kinds. Criterion 2 holds: every evidence anchor resolves. Criterion 3 holds.
Criterion 4 holds (§12.1). Criterion 5 holds (§12). The run exits non-zero because two checks
fail, and it should.

# 13. Defects the run found

## 13.1 The table lane misdates columns when a group phrase spans only part of a header — **open**

Two shapes, both found by §5's checks and neither visible to the 11 reviewed table cases, all of
which score `period_accuracy 1.000`.

**A group phrase is applied to every period column, because the lane has no notion of span.**

`open-20210630.htm#p145`. Header row A is `June 30, | March 31, | Year Ended December 31,`; row B
is `2021 | 2021 | 2020 | 2019 | 2018`. §8a.7a's shape rule needs *exactly one phrase of each
kind* and there are three phrases, so it falls back to even division and gives every bare year to
`Year Ended December 31,`. Emitted:

| value | emitted period | correct period |
| --- | --- | --- |
| 39 | 2021-12-31 | 2021-06-30 |
| 27 | 2021-12-31 | 2021-03-31 |
| 21, 21, 18 | 2020/2019/2018-12-31 | correct |

Two of five values misdated, and the two collide, which is how the check caught it.

`q32023formxex991earningsre.htm#p29`. One phrase — `Nine Months Ended September 30,` — over five
full-date quarterly columns and two bare years. One phrase is not "one of each kind" either, so
even division gives all seven to it, and the five quarterly columns come out as nine-month
durations: `September 30, 2022` → `2022-01-01…2022-09-30`, `December 31, 2022` →
`2022-04-01…2022-12-31`, and so on. **10 observations misdated in that one passage.**

**Blast radius, measured on the run:** 67 (passage, metric, period) triples are emitted more than
once across **21 table passages** and **138 observations**; 59 of the 67 carry equal values and
merge harmlessly, 8 carry different values and are the 9 flagged conflicts. Separately, 10
observations carry a full-date column label under a duration longer than 100 days — all in the
one earnings-release passage above.

**Not fixed here, deliberately.** Header span analysis is stage 6 work: it changes
`benchmarks/extraction/v1/reports/table_lane_v1.{json,md}`, whose bytes a committed test pins, and
it needs its own measurement pass. Step 13's charter is to run what exists and verify it, and the
verification did exactly what it was built to do. **Founder decision:** whether to re-open stage 6.

## 13.2 `observation_id` is not unique within one passage — **fixed, see §18**

`obs:{metric}:{subject}:{period_key}:{lane}:{passage_digest12}` carried no row discriminator.
`identifiers.py` argued the passage digest at length and argued it entirely about *cross-passage*
collisions — "two filings routinely report the same metric for the same period". It did not
cover two cells of one table reporting the same metric for the same period.

**Two claims in this section were wrong and are corrected in §18.1:** the count (67 was measured
before the §13.1 header-span repair; the defect is 58 ids over 12 passages at `23216f8`), and
"adding a row discriminator would change every observation id in two committed reports" — no
committed benchmark report contains an observation id at all *(verified 2026-08-02:
`grep -o "obs:[a-z0-9:_.-]*" benchmarks/extraction/v1/reports/*.json` matches nothing)*.

## 13.3 A code's severity is not a function of the code — **fixed**

The first draft of the report mapped each issue code to one severity and printed whichever row it
saw last. `AMBIGUOUS_ALIAS` is a refusal when the table lane declines a row and a rejection when
the narrative lane refuses a proposed claim, so the table said `rejection` for 461 occurrences of
which 458 are refusals. The column is now a sorted set and a test asserts it against the rows.

## 13.4 A miss lost the scope size it had just computed — **fixed**

`MissingAnswerError` leaves the narrative lane before `NarrativeExtraction` is built, so 2,582 of
3,072 narrative candidates recorded no scope size and the reported mean was 0.278 over a
denominator of 490 — a number about the passages that happened to fail *early*, presented as a
number about the corpus. The lane now re-raises the miss with the scope size attached. Measured
over all 3,072: **mean 6.519, min 0, max 19**.

# 14. Corrections to this plan

**§1 and §1.1 contradict each other and §1.1 wins, narrowed.** §1 says the run "must not read
`benchmarks/` for anything"; §1.1 says "the committed answer stores replay". Both cannot hold.
Settled: what a run reads is a store of *answers* — a request digest, a model, a finish reason
and a raw response — and never a case, a gold claim or an expected abstention. The path is
`config/extraction.yaml` `run.answer_stores`; no module under `extraction/` names it, four AST
guards enforce that, and `test_what_a_run_loads_is_answers_and_holds_no_gold_annotation` asserts
the loaded rows carry no gold field. A run with the list empty is legal and extracts through the
table lane alone.

**§1's "scoping: lexical and hybrid — both run" is not achievable offline and was not done.**
The hybrid scope embeds every candidate passage; this repository commits 26 case vectors and no
corpus-scale cache, and `config/extraction.yaml` deliberately declares no cache root (STAGE_09
§11.5). `extraction/context.py` raises `ScopeUnavailableError` at construction rather than
failing on the first passage. This costs nothing: the founder's own constraint says the §6
comparison is made by reporting code from committed reports and never by the pipeline, so the
decision never needed a second corpus run. Hybrid's corpus cost is recorded as **unmeasured**,
with the reason, rather than estimated.

**§2's file list omits `lane_outputs.jsonl`, which §7 requires.** The catalogs are built from the
persisted lane outputs, so a directory holding only the catalogs cannot rebuild itself. The file
is in the run directory and in the completion marker.

**§4's "every candidate passage that produced no claim" is answered by `issues.jsonl`, and the
report carries the complete grouping.** 11,694 candidates produced no claim; a document that
pasted all of them in would be a worse record than the file already is. `report.md` states every
code with its candidate count, its occurrence count, its severities and its lanes — no code is
omitted or truncated — and lists the passages in full for every code with at most 25 candidates.
A test asserts each group against the outcomes it describes.

**The event lane had no route to the corpus and one had to be built.** STAGE_12 §1.2 named the
gap and left it: the narrative selection policy requires a *metric* alias hit, which excludes
`ef20055426_ex99-1.htm#p2` — an 8-K reporting two executive changes — for having no metric in it.
`config/extraction.yaml` gains an `events` lane with `require_alias_evidence: false`, and
`LanePolicy` gains one optional field. `excluded_document_types` still applies, so contract and
governance filings stay out: V1 §9 bounds events to a representative subset and explicitly not to
a sweep of the 64 material agreements. Result: 8,273 event candidates, and all three reviewed
event passages reachable.

**Two step-4 tests changed and both were right to.** `test_selection_runs_over_the_corrected_corpus`
asserted `12,442 × 2` candidates and now asserts `12,442 × len(policy.lanes)`, read off the policy
rather than written down.

# 15. The scoping decision *(§6)*

**Default: `lexical`. Strength: weak.** Written to `config/extraction.yaml` and to
`benchmarks/extraction/v1/reports/scoping_decision_v1.{json,md}`, computed from the four
committed evaluation reports by code no pipeline module can reach.

**The width, stated beside the result:** the two scopes issue identical request digests on 13 of
14 narrative cases and on 3 of 3 event cases — the event lane takes no scope at all — so the
whole extraction comparison rests on **1 of 17 reviewed cases**, and the reachability difference
is **1 of 50 required concepts**.

| Criterion | Holds |
| --- | --- |
| hybrid adds a gold claim that is clean on every scored dimension | **no** — it adds one and it fails population wording |
| hybrid causes no per-claim regression | yes |
| more than one reviewed case can distinguish the scopes | **no** — one |
| hybrid's corpus cost is measurable from committed artifacts | **no** |

Adoption needs criteria 1 and 3; neither holds. **This is not a decision against hybrid on
quality** — it causes no regression and reaches one concept lexical cannot. It is a decision that
one unclean case is not enough evidence to move a default, and the report says so in those words.
The measurement most likely to change the answer is raising `scoping.hybrid.top_k` to 3, which
takes required-concept recall to 1.000 in the committed sensitivity sweep and has never been
scored against extraction quality.

# 16. What §4.1 found

Exactly one event in the run carries `occurred_on == announced_on`:
`evt:workforce-reduction:2022-11-02:3a2153734d24` on `open-20220930.htm#p117`, both dates read
from the phrase `November 2, 2022` in *"On November 2, 2022, the Company announced a workforce
reduction of approximately 550 employees…"*. This is the step 12 residual, on a different passage,
reproduced end to end.

Recorded and not fixed, as instructed: one `ANNOUNCEMENT_EQUALS_OCCURRENCE` diagnostic in
`issues.jsonl` with severity `diagnostic` and `rejected_claim: false`; `events.jsonl` carries
`occurrence_date_text`, `announcement_date_text`, `dates_equal`, `review_flag` and
`evidence_quoted_text`; the report says in words that equal dates are **not proof of a defect**
because a change can be announced the day it takes effect, and names the general
announcement-versus-occurrence verifier as follow-up.

# 17. Still open, for the founder

1. **§13.1** — the table lane's header span gap. Addressed at `23216f8`; the residue is §18.4's
   four value disagreements, which are a *header* defect and not an id defect.
2. ~~**§13.2**~~ — done, §18.
3. **§6's population-wording question**, carried from step 11: gold's whole clause or the lane's
   noun phrase. It is the dimension the one hybrid-only claim fails, and answering it the other
   way flips §15's verdict.
4. **`top_k` 2 → 3**, never scored against extraction quality.
5. Whether a corpus-scale embedding cache is worth building, which is what "measure hybrid's
   corpus cost" actually costs.
6. **§18.4's four value disagreements** — a header-analysis defect the id repair deliberately
   did not touch and deliberately did not hide.
7. **§18.6's two recorded observations**, neither fixed here.

# 18. `observation_id` gains a structural row discriminator *(2026-08-02, §13.2 closed)*

**Result.** `duplicate_identities` goes from **82 failures to 0** over the same 25,344 catalog
rows, and `conflicting_duplicates` **still reports 4**, which is the point: the repair separates
two grid cells that were one id, and it does not thereby unsee that they disagree.

| | before (`23216f8`) | after |
| --- | --- | --- |
| `observation_id` | `obs:{metric}:{subject}:{period}:{lane}:{digest12(passage_id)}` | `obs:{metric}:{subject}:{period}:{lane}:{digest12(passage_id, row, column)}` |
| observation rows / distinct ids | 2,715 / 2,657 | 2,715 / **2,715** |
| ids describing two grid cells | **58**, over 12 passages | **0** |
| `duplicate_identities` | FAIL — 82 over 25,344 rows (58 `claims.jsonl`, 20 `observations.jsonl`, 4 `evidence.jsonl`) | **pass** — 0 over 25,344 |
| `conflicting_duplicates` | FAIL — 4 over 2,715 | **FAIL — 4 over 2,715** *(unchanged, and required to be)* |

## 18.1 The defect, and two things §13.2 got wrong

The docstring in `identifiers.py` argued the passage digest at length and argued it **entirely**
about cross-passage collisions. Within one passage, one table row reports one metric under
several period columns, and the passage digest cannot tell those columns apart:

    obs:adjusted-ebitda:opendoor:2022-01-01_2022-09-30:normalized-table:98849a208451
        183000000.0  row "Adjusted EBITDA", column "2022"
       -211000000.0  row "Adjusted EBITDA", column "September 30, 2022"

**§13.2's count of 67 was stale**, measured before the §13.1 header-span repair. Re-measured at
`23216f8`: **58** ids over **12** passages *(verified 2026-08-02 by
`rm -rf data/extraction_runs && python -m extraction run` and grouping
`observations.jsonl` by `observation_id`)*.

**§13.2's blocker did not exist.** It said adding a discriminator "would change every observation
id in two committed reports". No committed benchmark report contains an observation id — or any
payload id — at all *(verified 2026-08-02:
`grep -o "obs:[a-z0-9:_.-]*" benchmarks/extraction/v1/reports/*.json` and `grep -c "claim:"`
both match nothing; the two files that mention `observation_id` mention it in prose about
`reported_metric_observation_ids`)*. The repair changed **no** committed report content.

## 18.2 The discriminator: grid position, and nothing a model produced

`LaneClaim` gains two optional fields, `row_index` and `column_index`, and a
`structural_position` property that renders them as `("row=4", "column=12")`. The table lane
fills them from what it already had — the data row carrying the metric label, and
`PeriodColumn.column_index`, the *header* column. `observation_id` takes them as a trailing
`structural_position` tuple and digests them after the passage id.

Three rejected alternatives, with reasons:

| Rejected | Why |
| --- | --- |
| a row counter incremented as the lane iterates | not structural — it is a function of visit order, so an unrelated change to which rows abstain renumbers ids that did not move |
| the value cell's `column_index` | shifts row by row with layout spacing (`_value_cells`), so one cell read from tables padded differently mints two ids |
| including `table_id` in the position | a run calls `extract` (which supplies none) while the benchmark calls `extract_table` with the catalog's real block id — two callers, one cell, two ids. The grid's identity is already in the digest as `passage_id`, which *is* what a table passage is. |

**Additive, on the `event_id` precedent.** An empty `structural_position` reproduces the previous
digest byte for byte, because `digest(passage_id)` and `digest(*[passage_id])` are the same call.
Of the run's 2,715 lane claims, 2,698 are table claims that now state a position and **17 are
narrative claims that state none and whose ids did not move**.

## 18.3 The trap: the conflict check keyed on the id it was about to lose

`validate_no_conflicting_duplicates` keyed on `observation.observation_id`. Every one of the 4
disagreements is **between two columns of one table row** — exactly the pair the discriminator
now separates — so leaving the check alone would have taken it from 4 failures to 0 and called
the run clean over an unrepaired defect.

**The brief for this repair asserted the check "keys on (metric, subject, period), not on id".
It did not; it keyed on the id.** What *did* already say the right thing is this plan's §5 and
`verify/public.py`'s own `DESCRIPTIONS`, which has rendered
`no (metric, subject, period, lane, passage) is reported twice with different values` into every
`report.md` this stage has produced. The code was the thing out of step with both.

`core/validation.py` now has `observation_identity(observation)` returning
`(metric, subject, period_start, period_end, instant_date, lane, passages)` — the identity the
id carried *before* the discriminator — and the check keys on that. Not broadened to
(metric, subject, period) alone, which the id scheme's own founding example forbids: *(measured
on this run: keyed with the passage, **4** failures; dropping the passage, **124**; dropping
passage and lane, **89**)*. The finding's message now names **both** ids, since a reader given
one can no longer derive the other.

## 18.4 What the 4 conflicts are, and why they stay

All four are in `q32023formxex991earningsre.htm#p29`, all four pair a bare-year column against a
`September 30,` column, and all four are a **header-analysis** defect, not an id defect:

| metric | period | value under `2023` | value under `September 30, 2023` |
| --- | --- | --- | --- |
| `adjusted_ebitda` | 2023-01-01…2023-09-30 | -558,000,000 | -49,000,000 |
| `adjusted_ebitda_margin` | 2023-01-01…2023-09-30 | -9.2 | -5.0 |
| `adjusted_ebitda` | 2022-01-01…2022-09-30 | 183,000,000 | -211,000,000 |
| `adjusted_ebitda_margin` | 2022-01-01…2022-09-30 | 1.4 | -6.3 |

Two columns are being resolved to one nine-month duration and at most one of them can be right.
That is §13.1's residue and it is **recorded, not fixed** — a table-lane header question with its
own measurement pass, and out of scope for an identity repair.

## 18.5 Verification, and the two-run byte-identity proof

`rm -rf data/extraction_runs && python -m extraction run`, twice, at this working tree:

| Check | Result | Examined | Findings |
| --- | --- | --- | --- |
| evidence resolution | **pass** | 2,725 anchors | 0 failures, 0 warnings |
| ontology validation | **pass** | 2,725 claims | 0 errors, 186 warnings (all `unpreferred_source_lane`) |
| forbidden lanes | **pass** | 2,715 observations | 0 |
| duplicate identities | **pass** | 25,344 rows | **0** *(was 82)* |
| conflicting duplicates | **FAIL** | 2,715 observations | **4** `DUPLICATE_OBSERVATION_CONFLICT` *(§18.4)* |

The run still exits non-zero, and it should.

```text
IDENTICAL lane_outputs.jsonl       65365198cf8e8c06b5eb9416a89c5191d52af5e452ba15c5d2b94a24340d39d5
IDENTICAL claims.jsonl             5653e156c24138e86ae39455f9d621a6d01045d355bbf642c634f1c409525cb3
IDENTICAL observations.jsonl       26ce953da38621177521584fcda8049df2a2987e828ba2bca0e52f4ac1f46e5c
IDENTICAL events.jsonl             b1eaed303fc6af24bfb4b331e4e0d5e40d659929d2f8413de9c61f14698166b8
IDENTICAL relationships.jsonl      bf095051992e7315e03ac8a6c17eabbf59192cae05eba4bdde9ebf97437f0104
IDENTICAL evidence.jsonl           694750942d9579de37323d941b79282642f71c3dd36d6256d139855e55b70923
IDENTICAL issues.jsonl             eb246f14653fae5b7aede0b3c85823d9d9d84cd0a2b5126e676cf2a75ed72400
IDENTICAL rejected_claims.jsonl    45f6c2562ff8956c286121803baa809176fa7980ea354ea2b541c842764e3238
IDENTICAL report.md                b54c2109f6ece580418a2dd0b6468b59cf445599838db109ced7d92b5e28b933
```

`manifest.json` differs in `created_at` alone (`15:24:49Z` vs `15:25:42Z`); `run.complete`
differs with it because the marker digests the manifest. `events.jsonl`,
`relationships.jsonl` and `rejected_claims.jsonl` carry the **same hashes as §12.1** — no event,
edge or rejection payload has an observation id in it, so the repair could not touch them, and
the fact that it did not is checkable rather than argued.

## 18.6 Recorded, not fixed

1. **The committed benchmark reports were stale at `23216f8`.** All twelve recorded
   `implementation commit c687bea…` — the commit *before* HEAD — because `23216f8` changed the
   table lane's header analysis and did not regenerate them. Regenerating all six report
   commands here moved exactly that field in ten files and that field plus four `source …json`
   rows in `scoping_decision_v1.{json,md}`. **Every scored number in every report is byte-for-byte
   unchanged**, which is a second, independent statement that neither `23216f8` nor this repair
   moved a benchmark result.
2. **The lane records grid coordinates in two places now.** `extractor_metadata` keeps
   `row_index`, `value_column_index`, `period_header_column_index` and `metric_label_row_index`
   for debugging; `LaneClaim.row_index` / `column_index` are the id's inputs. The duplication is
   deliberate — an id must not be derived from a free-form dict whose keys can be renamed without
   anything noticing — but it is duplication, and a later pass could make the metadata read from
   the fields rather than restate them.

## 18.7 Files changed

| File | Change |
| --- | --- |
| `extraction/core/identifiers.py` | `observation_id` takes `structural_position`; docstrings record the within-passage defect and the additivity rule |
| `extraction/core/models.py` | `LaneClaim.row_index`, `LaneClaim.column_index`, `LaneClaim.structural_position` |
| `extraction/core/assembly.py` | `to_observation` passes the claim's structural position |
| `extraction/stages/tables/deterministic_lane.py` | the table lane states its grid position on every claim |
| `extraction/core/validation.py` | `observation_identity`; the conflict check keys on it, not on the id; both ids in the message |
| `tests/extraction/test_contracts_and_validation.py` | six tests (§18.8) |
| `benchmarks/extraction/v1/reports/*` | regenerated; only the recorded commit moved (§18.6) |

Offline suite **1,924 passed** (was 1,918), 60 live passed.

## 18.8 The tests

| Test | Claim |
| --- | --- |
| `test_two_grid_positions_in_one_passage_cannot_share_an_observation_id` | two real grid positions from `…#p29` mint different ids, and differ only in the digest |
| `test_an_observation_id_is_recomputed_identically_in_a_second_process` | stable in-process and across a `subprocess` boundary — the check that catches a per-process `hash()` salt |
| `test_a_claim_with_no_grid_position_keeps_the_id_it_had_before_the_repair` | additivity, pinned to the literal `obs:homes-sold:opendoor:2025Q1:normalized-narrative:dd50cffa6513`, and the five-argument call still works |
| `test_the_passage_that_collided_now_yields_one_id_per_grid_cell` | the real lane over the real colliding passage through `assemble`: every id distinct, every claim positioned |
| `test_the_row_discriminator_does_not_silence_the_conflict_check` | two cells with different ids and different values are still one `DUPLICATE_OBSERVATION_CONFLICT`, naming both ids |
| `test_two_passages_reporting_one_fact_differently_are_not_a_conflict` | the check was not broadened past the passage |
