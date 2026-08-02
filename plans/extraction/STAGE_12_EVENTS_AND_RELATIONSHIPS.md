# Stage 12 — bounded event and relationship proof

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 12, §13 open decision 2.
**Predecessor:** [STAGE_11_NARRATIVE_EVALUATION.md](STAGE_11_NARRATIVE_EVALUATION.md), commit `7f28364`;
benchmark correction `9cea8a1`.
**Goal:** emit the benchmark's **4 gold events and 2 gold relationships** through the same
provider boundary, scoping rules, validation and abstention behaviour the metric lanes use —
and no more. Anything wider than that subset is a founder gate.

**Status: implemented.** §2 stated three gates; **all three are answered and the section below
records how**, with the two founder decisions that answered the first and third named as such.
§1 is the groundwork, measured 2026-08-02 and verified where it mattered. §4 records the
routing decision and the options it was chosen over, §5 the implementation, §6 the results, and
§7 the corrections — including four things this brief got wrong.

Every number below is in `benchmarks/extraction/v1/reports/event_relationship_v1.{json,md}`,
regenerated from the committed answer store by
`python -m benchmarks.extraction.v1 event-report`, and byte-identical across three consecutive
regenerations *(verified 2026-08-02)*.

---

# 1. Groundwork *(verified 2026-08-02)*

## 1.1 The claim model already carries events and relationships

Nothing needs inventing on the ontology side.

| Thing | Where | State |
| --- | --- | --- |
| `ClaimKind.EVENT` / `.RELATIONSHIP` | `ontology/core/values.py:156` | declared |
| `EventInstance`, `EventParticipantRef`, `RelationshipInstance` | `ontology/core/models.py:401, 393, 417` | declared |
| `OntologyClaim.event` / `.relationship` payload slots | `ontology/core/models.py:434` | declared |
| `validate_event`, `validate_relationship` | `ontology/validation.py:177, 207` | implemented |
| `check_event_participants`, `check_event_temporal`, `check_relationship_instance` | `ontology/core/constraints.py:657, 689, 698` | implemented |
| `event_id()`, `relationship_instance_id()` | `extraction/core/identifiers.py:67, 77` | **written, zero callers, zero tests** |

`claim_id(claim_kind, payload_id)` is kind-agnostic, so `("event", event_id)` yields
`claim:event:{digest12}` with no change. **No new ID scheme is needed** — only a caller.

What is metric-only, and must grow a second shape:

- `LaneClaim` (`extraction/core/models.py:197`) — `extra="forbid"`, requires `metric_id`,
  `value`, `unit`, `period`.
- `assemble` (`extraction/core/assembly.py:208`) hard-codes `claim_kind="metric_observation"`.
- `extraction/core/validation.py` reads only `claim.metric_observation`; `validate_source_lanes`
  and `validate_no_conflicting_duplicates` `continue` past anything else. **`validate_evidence_resolves`
  already uses `payload_evidence`, so evidence checking covers all three kinds today.**

## 1.2 The routing gap, and where exactly it is

Two filters, in series, both narrowing to metric definitions:

```python
# extraction/stages/select/typed_selector.py:234
    metric_hits = tuple(h for h in hits if h.is_metric)
```

```python
# extraction/stages/narrative/narrative_lane.py:324
        if str(getattr(concept.category, "value", concept.category)) != "metric_definition":
            continue
```

The first makes `event-executive-change-ceo-2025` score `NO_CANDIDATE_SIGNAL` and never be
selected. The second makes the narrative lane return `UNRESOLVED_METRIC` before reaching a
provider — which is the "issues no request at all" line in `narrative_lane_v1.md`.

**The scopes are not the problem.** `LexicalOntologyCandidateScope` applies no category
filter at all (`lexical.py:108-133` adds every `hit.concept_ids`, ignoring `is_metric`), and
event types do reach scope in practice: `workforce_reduction` is in the *credit-facility*
case's lexical scope. So step 12's routing extension belongs at these two filters, not in
scoping.

**But scope alone does not reach the gold event types.** Measured:

| case | gold event type | in lexical scope | hybrid rank / score |
| --- | --- | --- | --- |
| `event-executive-change-ceo-2025` | `executive_change` | no | not in head |
| `event-credit-facility-established-2022` | `credit_facility_established` | no | rank 3, **0.4306** — below `min_similarity` 0.45 *and* below `top_k` 2 |
| `event-workforce-reduction-2020` | `workforce_reduction` | no — the passage says "a reduction in workforce", not the alias | not in head |

The event types carry `aliases = ()` — all three of them. Relaxing the metric filter is
therefore **necessary and not sufficient**: it admits event concepts that lexical matching
does not reach for these passages. How the lane is offered the right event type without
fitting the vocabulary to three fixtures is the design question step 12 must answer, and
transcribing aliases off these three passages is the answer it must not give.

## 1.3 Entities

`registry.definitions.instances` holds **four**: `opendoor`, `opendoor_accountable`,
`sec_edgar`, `nasdaq`. The gold names `kaz_nejatian`, `keith_rabois`,
`opendoor_unnamed_subsidiary`, `asset_backed_senior_revolving_2022_10` — none is an instance.

**No minting mechanism exists** anywhere in the repo, and none is required: entity *ids* are
never validated. `check_event_participants` reads `participant.entity_type` and never
`entity_id`; `check_relationship_instance` reads the types and never the ids;
`check_observation_subject` reads `subject_type` and never `subject_entity_id`. Six committed
ontology example fixtures already validate green with ad-hoc ids (`counterparty_bank`,
`facility_2022_senior_revolving`, `opendoor_labs_inc`, …).

`scope_runner.py:162` already records the consequence in prose and filters non-instance gold
ids out of known-instance scoring, so the scope is not scored against a vocabulary that does
not exist.

**Recommendation:** mint readable ids deterministically from the printed name
(`_slug` in `identifiers.py` already does the normalisation), and keep
`opendoor_unnamed_subsidiary` as an explicit unresolved placeholder — the case's
`UNNAMED_ENTITY` abstention says the filing does not support resolving it. Entity
*resolution* is out of scope for V1 and must not be started here.

## 1.4 Nothing scores events or relationships today

`gold_events` and `gold_relationships` are read by exactly three places: `scope_runner._entity_ids`
(which harvests entity ids only, never `event_type_id`), and two integrity tests. `runner.py`
does not load the event cases at all (`TABLE_CATEGORIES` excludes them). `narrative_runner.py`
loads them but `_gold` reads `gold_claims` only, so:

| case | what is scored today | what is invisible |
| --- | --- | --- |
| `event-executive-change-ceo-2025` | 1 expected abstention | 2 gold events, 1 gold relationship |
| `event-credit-facility-established-2022` | 1 gold **metric** (`borrowing_capacity`) + 1 abstention | 1 gold event, 1 gold relationship |
| `event-workforce-reduction-2020` | 1 abstention | 1 gold event |

There is no `GoldEvent` or `GoldRelationship` dataclass. `event-executive-change-ceo-2025`
currently scores `abstention_honoured_rate 1.000` **while emitting nothing at all**, because
`honoured` is satisfied by silence plus any recorded issue.

## 1.5 Architecture rules a new lane inherits

Enforced by executable tests, all of which a step-12 module must pass:

- No module under `extraction/` may import or name the benchmark — four separate guards, the
  strongest reading *string constants* out of the parse tree (`test_narrative_lane.py:1593`),
  with a meta-test proving it catches four spellings (`:1613`).
- `extraction/core/` may not import a stage (`test_narrative_lane.py:1824`,
  `test_lexical_scoping.py:313`).
- Only the one stage named by `PROVIDER_AWARE_STAGE` may import a provider, and only
  `PROVIDER_PORT_MODULE` (`test_provider.py:400`). **A new stage may not name a provider at
  all** — so events must go through the narrative lane's port, not a second one.
- Transitive import allow-list for everything the narrative lane reaches, failing closed
  (`test_narrative_lane.py:1730`), plus a subprocess test that importing the package loads no
  HTTP client (`:1780`).
- Selection scope lives in `config/extraction.yaml`, not in code (`test_typed_selection.py:242`).

**Two rules that do *not* exist for `extraction/` and should not be assumed:** "no stage
imports another stage" and "no import cycles" are enforced for `acquisition/` and
`normalization/` only. Extraction deliberately imports across stages through `public.py`
(`narrative/public.py:39` imports `..tables.public`; `scoping/lexical.py:28` imports
`..select.alias_evidence`). That is the established pattern here.

---

# 2. The three gates, answered *(2026-08-02)*

All three are closed. None of them was closed by this stage's own judgement: two were founder
decisions and the third was an ordinary defect with one right answer. What follows replaces the
"none of them is decided" this section carried, and keeps the original diagnosis because the
diagnosis is what made each answerable.

## 2.1 The CEO event's date — **answered by a founder decision, and the contract changed with it**

*Was:* both `executive_change` gold events stated `occurred_on: "2025-09-11"`, the 8-K's filing
date. That string does not occur in the passage and neither does "September 11"; the passage
prints a dateline, `SAN FRANCISCO, Sept. 10, 2025 (GLOBE NEWSWIRE) -- ... today announced`, and
carries a third date in metadata (`report_date` 2025-09-06).

*Answered:* the founder decision went further than this brief's recommendation, and the extra
step is the one that mattered. The recommendation was `occurred_on: "2025-09-10"` — dating the
event by what its evidence says. What was decided instead is that **the dateline dates the
announcement and not the appointment**, so 2025-09-10 is not an occurrence date either:

- `EventInstance` gained `announced_on` beside `occurred_on` (`ontology/core/models.py:409`),
  and neither is ever inferred from the other;
- `executive_change` declares `required_temporal_any_of: [announced_on, occurred_on]` in place
  of `required_temporal_fields: [occurred_on]`; every other event type is unchanged;
- `check_event_temporal` grew the alternative form (`ontology/core/constraints.py:689`);
- the gold now reads `announced_on: "2025-09-10"` and **no `occurred_on` at all**.

The brief's recommendation would have been wrong. It would have put a date the passage prints
into a field the passage says nothing about, which is the same class of error as the filing
date it was correcting — smaller, and still an inference. V1 §4.0b carries the rule.

**Why it was a gate and no longer is:** the case was unpassable in both directions because the
true answer was *unrepresentable*, not because it was ambiguous. Once the contract could carry
"announced on this day, effective date not stated", there was one correct answer and a lane
could give it. This is the second time a contract in this pipeline could not state a fact the
corpus states — §8a.13's period enum was the first.

## 2.2 The relationship ids — **fixed, as the ordinary defect it was**

*Was:* both gold predicates were written as concept ids (`holds_position_at`), and the registry
keys relationships by `relationship_id`, which is upper-case. `validate_relationship` refused
the gold's own ids with `unknown_relationship_predicate`;
`test_gold_relationship_ids_exist_in_the_ontology` passed only because it asked
`registry.find`, which searches the *concept-id* index.

*Fixed*, in the order this brief proposed: the integrity test now looks the id up the way the
validator does, which turned it red, and the gold ids became `HOLDS_POSITION_AT` and
`BORROWS_UNDER`. A second test drives the whole `RelationshipInstance` through
`ontology.validate_relationship`, so the endpoints are checked too and not only the predicate
(`tests/extraction/test_benchmark_integrity.py:253, 262`).

**The runtime side is now checked from both directions.** `event_lane` resolves predicates
through `registry.relationship` and never `registry.find`
(`test_event_lane.test_the_relationship_menu_is_derived_from_the_event_types_own_declarations`),
and every predicate in the committed report is re-resolved that way by
`test_event_lane_report.test_every_emitted_relationship_predicate_resolves_the_way_the_validator_looks_it_up`.

## 2.3 The gold event properties — **answered by a founder decision, and a consumer now exists**

*Was:* not one of the four gold property names (`new_title`, `borrowing_capacity_usd`,
`final_maturity_date`, `stated_cause`) was in its event type's `allowed_properties`, and
`allowed_properties` had **no consumer anywhere in the repo**.

*Answered*, in the order this brief recommended, with one substantive departure the founder
decided rather than a rename:

| gold, before | after | kind of change |
| --- | --- | --- |
| `new_title` | `position` | rename |
| `final_maturity_date` | `maturity_date` | rename |
| `stated_cause` | `reason` | rename |
| `borrowing_capacity_usd` | **removed** | not a rename — see below |

`borrowing_capacity_usd` was removed rather than renamed because the declared vocabulary offers
`committed_capacity` and `uncommitted_capacity`, and the passage says "borrowing capacity"
unqualified while distinguishing "aggregate borrowing capacity of $12.6 billion" from
"committed borrowing capacity of $7.3 billion" two sentences later. Either rename would have
asserted something the filing does not. The figure is not lost: it is gold as the
`borrowing_capacity` metric observation on the same case.

`allowed_properties` now has **two** consumers, one on each side:
`tests/extraction/test_benchmark_integrity.py:291` holds the gold to it, and
`event_mapping._resolve_properties` holds the *lane* to it — a property the event type does not
declare is dropped with a recorded reason.

**A fourth correction landed with these three and this brief never noticed it.** The
`holds_position_at` gold carried a `properties: {title, start_date}` mapping, and
`RelationshipInstance` has no `properties` field at all — it declares `valid_from` and
`valid_to` and nothing else. The mapping was unrepresentable, and §1.1's table listed
`RelationshipInstance` as "declared" without reading its fields. Removed; the title lives on
the event's `position` property.

# 3. Scope, as built

Deliberately small. The proof is that the boundary works, not that coverage is broad.

- One request shape for events and relationships, through the **same** narrative provider port.
  No second provider and no second adapter: `test_provider.py:400` fixes `narrative` as the one
  stage that may name a provider and forbids any other stage naming one at all, so the four new
  modules live inside `extraction/stages/narrative/` beside the metric lane's five.
- `LaneEvent`, `LaneEventParticipant` and `LaneRelationship` beside `LaneClaim` in
  `extraction/core/models.py`, not a discriminated union — see §5 for the line counts and the
  reason.
- `assemble_events` beside `assemble`, using `event_id` and `relationship_instance_id`, which
  had zero callers and zero tests before this stage.
- The routing decision is §4. It is **not** the extension at the two filters §1.2 proposed —
  that turned out to be necessary and not sufficient, and §4 records the measurement.
- Scoring in a new `event_evaluation.py`, not in `narrative_evaluation.py`: the two share no
  dimension, no matching rule and no failure category, and folding them together would have
  produced a module with two disjoint halves.
- `benchmarks/extraction/v1/reports/event_relationship_v1.{json,md}`, byte-identical on
  regeneration, `implementation_commit` the only varying field.

**Out of scope, and a founder gate if it becomes necessary:** entity resolution, XBRL, graph
construction. *Not* "any event type beyond the three" — §4 explains why the opposite turned out
to be the smaller mechanism.

---

# 4. The routing decision: offer the declared event category whole

**Chosen.** Every declared event type is offered to the lane on every passage, and the
relationship menu is derived from those types' own `allowed_relationships`. No candidate scope
is injected into this lane at all.

**Why it is the smallest general mechanism.** The metric lane takes an
`OntologyCandidateScope` because forty-odd metric definitions cannot all fit one prompt and
lexical or semantic evidence has to narrow them. Nineteen event types can, and every one of
them declares `aliases = ()`, so there is no surface to narrow *on*. A mechanism with no
threshold, no rank cut and no parameter is the smallest available in the strict sense that
matters here: there is nothing in it that could have been fitted to a passage, which is exactly
what the hard constraint asks for.

The relationship half is the same argument through a different declaration. Ten predicates are
named across the event types' `allowed_relationships`, and an edge is emitted only when an
event this passage produced declares its predicate. That gives `allowed_relationships` its
first runtime consumer — before this stage the field was declared, populated, and read by
nothing.

## 4.1 The options rejected, and the measurement that rejected each

| Rejected | Why not |
| --- | --- |
| relax the metric-only filters at `typed_selector.py:234` and `narrative_lane.py:324`, as §1.2 proposed | **necessary and not sufficient, and the run measures it.** Across 3 cases × 2 scopes, the candidate scopes offer **0** of the gold event types. §1.2 measured this for lexical and predicted it for hybrid; the committed report re-measures both every time it is regenerated, and `test_the_routing_evidence_is_measured_rather_than_asserted` fails if it ever stops being true. |
| semantic retrieval over the event definitions, using the committed vectors | requires moving `top_k` or `min_similarity`. Both are derived from a measured similarity distribution and asserted against the committed hybrid-scope report by `tests/extraction/test_hybrid_scoping.py`; moving a configured threshold until three fixtures are reached is fitting the configuration to the answer sheet. It would also need an event-aware concept renderer, which changes the vector cache key and invalidates the stage 9 report's offline path. |
| add aliases to the event types | forbidden, and it is the forbidden answer for a reason: all three gold event types declare `aliases = ()` and none is in its own case's lexical scope, so any alias that reached them would have been transcribed off the passages. |
| expand protected concepts from detected entities and actions | needs an action lexicon that does not exist. Building one from these three passages is the alias answer at one remove. |

## 4.2 What it costs, and the consequence for the scope comparison

*(measured 2026-08-02)* The rendered event-type block is **8,566 characters**. The three
reviewed prompts are 20,785–21,947 characters and **4,713–4,940 tokens** on the running server;
the lane's own pessimistic estimate (3.5 characters per token, against measured ratios of
4.39–4.48) leaves **1,793–2,125** tokens of the 8,192-token slot for an answer, against the 512
`MIN_OUTPUT_TOKENS` one event needs. No request was refused and no answer truncated.

**The menu is a function of the vocabulary alone, so it is identical under both scopes.** Both
were run anyway — asserting "the scope makes no difference" without running the second scope
would be asserting the design rather than the result — and the report records that the two
issue identical requests on 3 of 3 cases and produce identical payloads on 3 of 3, while the
scopes themselves offer *different* concept counts on two of the three, so the agreement is
about the lane and not about the two scopes being one object.

---

# 5. What was built

```text
extraction/stages/narrative/          (beside the metric lane's five, one concern each)
  events_public.py    406  the issue vocabulary, the offered vocabulary, the response schema
  event_prompt.py     207  prompt construction, pure, versioned
  event_lane.py       213  OntologyGuidedEventLane
  event_mapping.py    530  answer -> LaneEvent / LaneRelationship, with refusal
extraction/core/
  models.py           +79  LaneEventParticipant, LaneEvent, LaneRelationship
  identifiers.py      +84  entity_id, unresolved_entity_id; event_id gains participants
  periods.py          +70  parse_printed_date, date_phrases
  assembly.py        +129  to_event, to_relationship, the two claim builders, assemble_events
benchmarks/extraction/v1/
  event_evaluation.py 914  every score, every failure category
  event_runner.py    1247  composition and rendering; computes no score
  answers/event_v1.jsonl   3 answers, 9,497 bytes
tests/extraction/
  test_event_lane.py         the lane and its boundary, offline
  test_event_lane_report.py  the artifact
  test_event_lane_live.py    the live gate, marked `live`
```

**Four modules and not one, and four is the number the existing package already justifies.**
Each mirrors one of the metric lane's concerns exactly: a contract, a pure string builder, an
orchestration, and the boundary that turns an untrusted answer into a typed payload or refuses
it. `event_mapping.py` is the one that must not fold into the lane — every "the model said
something the ontology does not permit" decision lives there and is testable with a literal
dict and no GPU, which is what 24 of the offline tests do. The fifth concern is **shared**:
`answer_store.py` is one durable record for both lanes, keyed by request identity, so neither
lane can find the other's answer.

**Three model classes and not a discriminated union.** `LaneEvent` and `LaneRelationship` share
five fields with `LaneClaim` (`passage_id`, `document_id`, `raw_text`, `source_lane`,
`assertion_type`) and no others; a union over three shapes with `extra="forbid"` would have had
to make every field optional, which is the property that lets a lane emit a claim with no value.

**`assemble_events` asks no ontology and takes no `ontology` argument**, where `assemble` does.
The asymmetry is deliberate and small: `assemble`'s parameter exists for the deferral and
population policies, which are derived from the vocabulary; no §7 policy applies to an event,
so a parameter added for symmetry would suggest a policy is applied that is not. Validation
happens where it happens for observations — `core.validation.validate`, which already covered
all three claim kinds through `payload_evidence`.

## 5.1 The temporal rule, as implemented

- `LaneEvent` carries `occurred_on` and `announced_on`, both optional, and nothing in the
  pipeline copies one into the other. Two offline tests pin both directions, and a third pins
  that a `filing_date` on the prompt context reaches neither field.
- The schema constrains both fields to an enum over **the dates the passage prints**, plus one
  `DATE_NOT_STATED` member. That is the design that removed a whole class of wrong period from
  the metric lane, together with the escape hatch that design later needed: the answer cannot
  state a date, only point at one, and it can say the passage prints none.
- Which dates an event type needs is **asked of the definition** —
  `required_temporal_fields` and `required_temporal_any_of` — and appears nowhere in the lane.
  `test_the_temporal_requirement_is_asked_of_the_definition_not_hard_coded` drives the same
  undated answer under two event types and gets two verdicts.
- `event_id`'s readable date segment carries `occurred_on` only. An announced-only event reads
  `undated`, which is true; promoting the announcement date into it would be the inference §4.0b
  forbids.

---

# 6. Results *(2026-08-02)*

**Every gold event and every gold relationship is emitted, matched, and validates clean.**

| Dimension | Denominator | Lexical | Hybrid |
| --- | --- | --- | --- |
| event type identity | 4 gold events | **1.000** | **1.000** |
| occurrence date *(incl. correct abstention)* | 4 matched | **1.000** | **1.000** |
| announcement date *(incl. correct abstention)* | 4 matched | **1.000** | **1.000** |
| participant role | 7 gold participants | 1.000 | 1.000 |
| participant entity id | 7 | **0.857** | **0.857** |
| participant entity type | 7 | 1.000 | 1.000 |
| event properties | 4 matched declaring any | **0.500** | **0.500** |
| event evidence | 4 matched | 1.000 | 1.000 |
| relationship predicate identity | 2 gold edges | **1.000** | **1.000** |
| relationship source id / type | 2 matched | 1.000 / 1.000 | 1.000 / 1.000 |
| relationship target id | 2 matched | **0.500** | **0.500** |
| relationship target type | 2 matched | 1.000 | 1.000 |
| relationship evidence | 2 matched | 1.000 | 1.000 |
| expected abstentions honoured | 4 | 0.750 | 0.750 |
| abstention code agreement | 4 | 0.000 | 0.000 |
| matched/emitted, events *(not precision)* | 6 emitted | 0.667 | 0.667 |
| matched/emitted, edges *(not precision)* | 4 emitted | 0.500 | 0.500 |

| | Lexical | Hybrid |
| --- | --- | --- |
| gold / emitted / matched events | 4 / 6 / 4 | 4 / 6 / 4 |
| gold / emitted / matched relationships | 2 / 4 / 2 | 2 / 4 / 2 |
| non-gold additions *(unscored, not errors)* | 2 events, 2 edges | 2 events, 2 edges |
| payloads the lane rejected | 4 | 4 |
| findings the model chose | 1 | 1 |
| **ontology validation errors** | **0** | **0** |
| classified failures | 5 | 5 |
| gold event types either candidate scope offers | **0** | **0** |

**V1 §11 acceptance criterion 1 now holds for events and relationships**: every emitted payload
reached `OntologyClaim` through `assemble_events` and validated with zero errors under both
scopes. Nothing had ever driven an event that far.

## 6.1 The five classified failures, and what each one is

| Category | Case | What |
| --- | --- | --- |
| `participant_wrong` | credit facility | the facility's entity id. Gold is `asset_backed_senior_revolving_2022_10`; the lane derives `asset_backed_senior_revolving_credit_facility` from the printed name. **Gold's id encodes a year and month the passage does not print**, and no general rule derives it — a founder question, recorded in the report. |
| `endpoint_wrong` | credit facility | the same id, as the `BORROWS_UNDER` target. One disagreement, two dimensions, and they are scored separately on purpose. |
| `properties_wrong` | executive change | gold `position: "Chairman of the Board"`; the passage prints "the role of **Chairman**" and never "Chairman of the Board". The lane's printed-value rule cannot produce the gold string. A founder question — the same span-convention question §4.0a raises about population wording. |
| `properties_wrong` | workforce reduction | gold `reason: "following the outbreak of the COVID-19 pandemic"`; the lane records "outbreak of the COVID-19 pandemic", a substring. The third instance of the same span-convention question. |
| `silence_broken` | workforce reduction | the case requires a `NO_HEADCOUNT_STATED` abstention and the lane recorded **no issue at all** on that passage. The substance of the expectation is met — no `headcount_reduced` property was emitted, and the passage gives no number — but a bare silence and an abstention are different answers and `ClaimLane`'s own contract says so. |

**Two of the five, and possibly three, are the benchmark's question rather than the lane's**,
and none of them was acted on: editing a case because a lane disagreed with it would stop the
benchmark measuring anything. All three are in the report's "What this run puts back to the
reviewers".

## 6.2 The four non-gold additions, and one of them is a finding

- `executive_change(eric_wu, opendoor)` and its `HOLDS_POSITION_AT` edge — Eric Wu is returning
  to the board in the same sentence, and the case simply does not list him. A correct answer
  the reviewers did not annotate.
- `HOLDS_POSITION_AT(keith_rabois → opendoor)` — the same, for the second gold event.
- `workforce_reduction(opendoor)` on the credit-facility passage — the passage's *first*
  sentence reports one, and the case annotates only the facility. Also correct.

**One declared property was emitted that gold does not list, and it is the one the case argues
against.** The lane records `committed_capacity: "$525 million"` on the credit facility. The
case's own note says calling that figure *committed* asserts something the filing does not: the
passage says "borrowing capacity" unqualified and distinguishes committed from aggregate two
sentences later. It is counted as a non-gold addition and left unscored, because gold is a
deliberate subset and scoring additions would make `matched/emitted` a precision — but it is
**not** a right answer, and this brief records it as the one addition that is wrong.

## 6.3 The residual the rule does not close

`occurred_on` and `announced_on` are two questions and the model sometimes answers one of them
twice. Two witnesses:

- the non-gold `workforce_reduction` on the credit-facility passage: the sentence says "On
  November 2, 2022, the Company **announced** a workforce reduction", which dates the
  announcement and leaves the reduction undated. The lane records both fields as 2022-11-02;
- the live gate's unannotated press release
  (`ef20059583_ex99-1.htm#p2`): a `securities_issuance` carrying 2025-11-21 in both fields.

Neither reaches a gold dimension, and neither is repaired. Narrowing this further needs a way
to check that a sentence *reports an announcement*, which is a reading and not a constraint —
the same shape as the §8a.12 period-attribution residual the metric lane measures rather than
closes. What the lane guarantees instead is that the answer is auditable: each date is a phrase
the passage prints, and the phrase chosen for each field is recorded separately on the payload,
so a copy is visible. The live test asserts the audit trail and prints the residual rather than
asserting the behaviour we would like.

## 6.4 Abstention code agreement is 0 of 4, and that is a vocabulary measurement

The cases say `THIRD_PARTY_ROLE`, `EFFECTIVE_DATE_NOT_STATED`, `UNNAMED_ENTITY` and
`NO_HEADCOUNT_STATED`; none is in this lane's `ISSUE_CODES`. The lane's vocabulary was derived
from what a lane can assess and reuses `NOT_THE_SUBJECT_COMPANY` and `MISSING_REQUIRED_CONTEXT`
from the existing one, per STAGE_10 §4. Adopting the cases' spellings would have scored the
benchmark against itself; step 11 reached 1 of 15 on the same measurement and reported it
rather than fixing it that way, and this stage does the same.

**Two of the four expectations are checkable against the payload rather than against a code,
and both are met.** `EFFECTIVE_DATE_NOT_STATED` is the occurrence-date dimension, which reads
1.000 including two correct abstentions; `UNNAMED_ENTITY` is the unresolved placeholder, which
`test_every_unresolved_participant_is_flagged_and_named_as_a_placeholder` holds to shape.

---

# 7. Corrections to this brief, and to what it was told

| § | What it says | What is true |
| --- | --- | --- |
| §2.1 | recommends `occurred_on: "2025-09-10"`, "the date the passage prints" | **The founder decision went further and the brief's recommendation would have been wrong.** 2025-09-10 dates the *announcement*; putting it in `occurred_on` is a smaller version of the same error the brief was correcting. §2.1 above carries the answer. |
| §1.1 | lists `RelationshipInstance` as "declared", nothing further | It has **no `properties` field**, and the `holds_position_at` gold carried one. A fourth benchmark correction that this brief's own survey missed by reading the class name and not its fields. |
| §3 | "the routing extension at the two filters in §1.2 — the smallest general capability" | The filters are **not** where the answer was. Relaxing them admits event concepts that neither scope reaches on these passages: 0 gold event types in scope across 3 cases × 2 scopes. §4 records the mechanism that does reach them. |
| §3 | "out of scope … any event type beyond the three" | The opposite. Restricting the menu to three types would have needed a rule naming them, which is the forbidden answer; offering all 19 is both more general and smaller. |
| §1.1 | `event_id()` — "written, zero callers, zero tests" | Also **broken for the corpus it was written for.** Its digest was over `(type, date, passage)`, and one 8-K reports two undated executive changes on one passage, so the two events shared an id. Participants are now part of the digest; the extension is additive and the collision is pinned by a test. |
| §1.1 | the identifier table lists three ids | The module docstring listed three of the four it defines. `relationship_instance_id` was missing, as the task brief said, and is now listed with `entity_id`. |

**One thing the task brief said that this run found to be more than it claimed.**
`allowed_properties` was said to be "now enforced by
`tests/extraction/test_benchmark_integrity.py`" — enforced against the *gold*. It now also
constrains the *lane*, which is the direction that matters at runtime: a property an event type
does not declare is dropped with a recorded reason rather than carried into the graph.

**Two prompt-side findings worth carrying forward.**

1. **A boolean beats a sentinel string.** Asked to type a sentinel for a participant the filing
   does not name, the 9B model typed the description instead — "a subsidiary of the Company",
   a perfectly good `entity_text` and a silently wrong id. `named_in_passage` is a field the
   grammar answers reliably, and the description is kept where it belongs.
2. **An abstention reason the model cannot assess gets picked instead of an answer.**
   `EVENT_TEMPORAL_REQUIREMENT_UNMET` was offered as a model-choosable reason; the model
   abstained from an event whose occurrence date the passage prints in the sentence it had just
   quoted, reasoning about a JSON field it must fill as a fact it must have. Withdrawn — which
   is the rule `public.MODEL_ABSTENTION_REASONS` already stated, applied to a code that broke
   it.

**A cosmetic inconsistency, recorded rather than fixed.** `relationship_instance_id` slugs its
endpoints through `_slug`, which folds underscores to hyphens, so `dana_reyes` reads
`dana-reyes` in the id while the payload carries the underscored form. Left alone: `_slug` is
the one readability rule every id in that module goes through and `observation_id` in two
committed reports is built with it, and no two ids `entity_id` mints can differ only by a
character `_slug` folds.

---

# 8. Acceptance

- [x] `pytest -m "not live"` green — **1,832**, up 84 from the 1,748 at `1f37059`. Counts from `--junitxml`.
- [x] `pytest -m live` green with both servers up — **60**, up 11 from 49.
- [x] All 4 gold events and both gold relationships emitted, matched and validated, with zero
      ontology errors under both scopes.
- [x] `event_relationship_v1.{json,md}` byte-identical across three consecutive regenerations;
      `answers/event_v1.jsonl` committed, 3 answers, 9,497 bytes.
- [x] Both scopes run, and the identity between them computed from the run rather than asserted
      from the design.
- [x] No benchmark case id, passage id or fixture wording under `extraction/` — checked by the
      four existing guards and by a fifth that looks for the actual identifiers by value.
- [x] No change to the metric lane, the table lane, the ontology or the benchmark cases. The
      one change outside this stage's own files is `datetime` added to the narrative lane's
      transitive import allow-list, with its reason, because the guard caught it.

**Not done, and stated as not done:** the §6.3 announcement-versus-occurrence residual is
measured and open; three disagreements with the reviewed cases are recorded and unacted on;
`change_kind` is unpopulatable under the printed-value rule because the ontology declares no
enum for it, and no gold asks for it.

# 8. Review findings and repairs *(2026-08-02)*

Adversarial review was run in a clean context and was **stopped by the founder part-way**, so
this section covers what it found before it stopped plus what orchestrator verification found
independently. It is not a complete review pass, and the next stage should not treat it as one.

**A mutation was left applied in the working tree.** `event_runner.py` still carried the
review's `participant_roles + 5` when it stopped. The orchestrator caught it because the suite
was red on a test the review had just added — not because anything announced it. Worth
recording as a process fact: a stopped review can leave the tree in a state its own new tests
condemn, and the tree must be re-run before anything is believed.

## 8.1 Three rendered tables disagreed with the data they render

Every one is the step-11 finding recurring, in a stage whose own module docstring cites step 11.

| Where | Defect | Effect |
| --- | --- | --- |
| `event_runner.py:1153` | the **Emitted id** column rendered `participant.expected_entity_id` | the participants table printed gold in the lane's column, so the run's only `participant_wrong` read as agreement while `participant_entity` scored 0.857 three tables above |
| `event_runner.py:1233` | the **Honoured** column was the literal `'yes'` | it could not say no, so `NO_HEADCOUNT_STATED` read as honoured while the Counts table said 3 of 4 and the failure table listed that same expectation as `silence_broken` — three renderings of one verdict, and the constant was the reassuring one |
| routing table | `participant roles / entity types / property names` was rendered from `report.vocabulary` and compared to nothing | `+5` regenerated a green report reading `22 / 24 / 43` |

`event_types_declaring_an_alias` was also the literal `0` checked by an assertion that the
literal equalled `0` — **the row the entire routing argument rests on was declared rather than
measured**, so an alias added to an event type could not have moved it. All four are now
computed from the objects they describe and checked by
`test_every_per_case_table_cell_is_the_value_it_renders` and
`test_the_routing_vocabulary_counts_are_the_offered_vocabularys_own`.

## 8.2 The deterministic id was absent from the durable report

`event_id` had already collided once on this corpus, and the fix — participants in the digest —
was pinned by a unit test. But the report printed the string `evt:` **zero times**, so a
recurrence would have been invisible in the evidence, and a collision does not fail loudly
downstream: it silently merges two events into one. Stage 13's catalogs are keyed on this id.

Now emitted and pinned by `test_every_emitted_event_carries_a_unique_deterministic_id`, which
also holds the §4.0b property that matters most in an identifier: an event whose passage dates
only its announcement reads `undated` in the readable segment, never the announcement date.
The three executive changes demonstrate both at once:

```text
evt:executive-change:undated:4c7324fecc42
evt:executive-change:undated:5f0724b23967
evt:executive-change:undated:70ee824c0430
```

## 8.3 Fixture sentences had been transcribed into runtime source

`event_mapping.py`, `identifiers.py` and `events_public.py` documented their failure modes by
quoting benchmark passages verbatim. Nothing keyed on the strings and no behaviour was fitted —
but a runtime module that quotes a fixture invites exactly the suspicion the no-fitting rule
exists to foreclose, and the reader cannot check the answer from the file. Rewritten to describe
the failure modes generically. `extraction/core/periods.py` keeps dateline *format* examples,
which are properties of press releases rather than of these three passages.

Also removed: `LaneAbstention`'s docstring asserted the benchmark expects an abstention in "24 of
its cases". It was 24 until the 2026-08-02 gold correction made it 23 — a number in a docstring
is a number nothing regenerates.

## 8.4 What review confirmed rather than broke

Reported because a review that only lists defects is not evidence of quality. Verified by the
orchestrator directly against the corpus and the artifact: the executive-change events carry
`announced_on: 2025-09-10` and `occurred_on: null`, exactly as §4.0b requires; the
credit-facility event carries `occurred_on: 2022-10-19` from "On October 19, 2022 … entered
into" with `announced_on` null, which is the discipline the other case's evidence does not
support; `opendoor_unnamed_subsidiary` stayed an explicit placeholder with `named: false`
rather than being resolved to the parent; `BORROWS_UNDER` resolved through the upper-case
predicate; and `gold_event_types_in_scope: 0` is re-measured on every regeneration rather than
asserted, which is what keeps §4's routing argument honest.

