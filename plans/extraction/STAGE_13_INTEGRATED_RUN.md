# Stage 13 — the integrated run, the catalogs, and the scoping decision

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 13, §4.6, §5.
**Predecessor:** [STAGE_12_EVENTS_AND_RELATIONSHIPS.md](STAGE_12_EVENTS_AND_RELATIONSHIPS.md),
commit `a986321`; gold-convention repair `4482982`.
**Goal:** run all three lanes over the whole normalized corpus, write an immutable manifest
and rebuilt catalogs under `data/extraction_runs/<run_id>/`, verify the run against the
ontology, and **decide lexical versus hybrid** on the extraction evidence steps 11 and 12
already produced.

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
| scoping | lexical **and** hybrid | both run; §6 decides the default |

**The benchmark is not an input.** No module under `extraction/` may name it, and the run
must not read `benchmarks/` for anything. The comparison in §6 is made *from committed
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
| `observations.jsonl` | `obs:{metric}:{subject}:{period_key}:{lane}:{passage_digest12}` |
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
5. **Conflicting duplicates** — same `(metric, subject, period)` with different values, the
   `DUPLICATE_OBSERVATION_CONFLICT` step 11 met.

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
