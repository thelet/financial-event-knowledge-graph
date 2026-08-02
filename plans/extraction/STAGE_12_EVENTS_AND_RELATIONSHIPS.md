# Stage 12 — bounded event and relationship proof

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 12, §13 open decision 2.
**Predecessor:** [STAGE_11_NARRATIVE_EVALUATION.md](STAGE_11_NARRATIVE_EVALUATION.md), commit `7f28364`;
benchmark correction `9cea8a1`.
**Goal:** emit the benchmark's **4 gold events and 2 gold relationships** through the same
provider boundary, scoping rules, validation and abstention behaviour the metric lanes use —
and no more. Anything wider than that subset is a founder gate.

**Status: blocked on three gates before implementation.** §2 states them. §1 is the
groundwork, measured 2026-08-02 and verified where it mattered.

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

# 2. Three gates, and none of them is decided

## 2.1 Founder gate — the CEO event's date is not in its passage *(verified 2026-08-02)*

Both `executive_change` gold events state `occurred_on: "2025-09-11"`. The passage's dateline
reads **`SAN FRANCISCO, Sept. 10, 2025 (GLOBE NEWSWIRE) -- … today announced`**. The string
`2025-09-11` does not occur in the passage; neither does `September 11`.

2025-09-11 is the **filing date** of the 8-K. The passage carries a third date in its metadata,
`report_date` 2025-09-06.

This is a gate because it makes the case unpassable in both directions. §11 forbids the lane
from inferring an unsupported period, so a lane that refuses to date the event beyond what the
passage prints is behaving correctly and scores wrong; a lane that emits the printed
2025-09-10 also scores wrong. The two readings — *the date the announcement states* versus
*the date the filing was made* — are both defensible and they score differently.

**Recommendation:** `occurred_on: "2025-09-10"`, the date the passage prints, on the same
principle §7.1 applies to population wording and §8a.12/§12b.1 applied to periods: an
observation is dated by what its evidence says, and filing metadata is not evidence about
when a thing happened. Not applied. A benchmark change is the founder's.

## 2.2 Defect — both gold relationship ids are in a form the validator rejects *(verified 2026-08-02)*

`RelationshipDefinition` carries **two** identifiers: `concept_id` (`holds_position_at`) and
`relationship_id` (`HOLDS_POSITION_AT`), and the registry keys relationships by the uppercase
one.

```text
registry.relationship('holds_position_at')  -> None
registry.relationship('HOLDS_POSITION_AT')  -> RelationshipDefinition
registry.find('holds_position_at')          -> RelationshipDefinition   # concept-id index
```

Driven through the real validator:

```text
validate_relationship(relationship_id='holds_position_at')
  ERROR unknown_relationship_predicate — 'holds_position_at' is not a declared relationship
validate_relationship(relationship_id='HOLDS_POSITION_AT')
  ERROR missing_evidence            — (the test instance has none; the predicate resolved)
```

The gold writes the lowercase form for both `holds_position_at` and `borrows_under`, so **a
correct lane emitting the gold's own ids would fail ontology validation.**

`test_gold_relationship_ids_exist_in_the_ontology` passes only because it calls
`registry.find`, which searches the *concept-id* index — not the lookup the validator uses.
This is the same class of defect as the `usd`/`USD` spelling found in step 6b: a gold field
and a runtime vocabulary agreeing by coincidence of index rather than by identity.

**Not a founder gate** — no concept meaning changes, and there is one correct answer, not two
readings. It is an ordinary defect and step 12 fixes it: the integrity test must look the id
up the way the validator does, which turns it red, and then the gold ids and any runtime
mapping are corrected to the form the validator accepts.

## 2.3 Open — gold event properties are undeclared, and nothing checks them

The gold uses `new_title`, `borrowing_capacity_usd`, `final_maturity_date`, `stated_cause`.
`EventTypeDefinition.allowed_properties` declares, respectively,
`[change_kind, position, effective_date]`,
`[committed_capacity, uncommitted_capacity, maturity_date, interest_rate, currency]`,
`[headcount_reduced, percentage_of_workforce, charge_amount, reason, currency]`.

**Not one gold property name is a declared one, and `allowed_properties` has no consumer
anywhere in the repo** — nothing validates it, which is why this has never failed.

Two ways to be consistent, and they are not equally cheap. Renaming the gold to the declared
names is a benchmark edit with no meaning change. Adding an `allowed_properties` check is a
new ontology constraint that would need every committed example fixture re-checked.

**Recommendation:** rename the gold properties to the declared vocabulary and add the
constraint, in that order, so the constraint lands green. Neither is started. If the declared
names turn out to be wrong for what the filings actually print, that is a §16-class ontology
question and a founder gate.

---

# 3. Scope, once the gates are answered

Deliberately small. The proof is that the boundary works, not that coverage is broad.

- One request shape for events and relationships, through the **same** narrative provider port —
  no second provider, no second adapter (§1.5 forbids it).
- `LaneEvent` / `LaneRelationship` beside `LaneClaim` in `extraction/core/models.py`, or a
  discriminated union — decided at implementation from what `assemble` needs, and stated with
  line counts per §Proportion.
- `assemble` gains two branches, using the `event_id` / `relationship_instance_id` helpers that
  already exist.
- The routing extension at the two filters in §1.2 — the **smallest general** capability that
  reaches these six, never a benchmark case id in runtime code.
- Scoring: `GoldEvent` / `GoldRelationship` and their dimensions in `narrative_evaluation.py`.
- `benchmarks/extraction/v1/reports/event_relationship_v1.{json,md}`, byte-identical on
  regeneration, `implementation_commit` the only varying field.

**Out of scope, and a founder gate if it becomes necessary:** entity resolution, any event
type beyond the three, XBRL, graph construction.
