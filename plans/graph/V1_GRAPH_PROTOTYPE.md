# v1 — Graph Prototype

**Status:** G0 complete, nothing implemented. Written 2026-08-02 against `a986321` while
extraction step 13 was still running, then **corrected at G0 against the finalized run**
(`b1d55f4`, run `extract-v1-lexical-2422c4252c07`). §2 now records measurements, not
assumptions; §0b lists what the real run contradicted.

**Scope:** project the finalized extraction run into a rebuildable Neo4j graph that can be
inspected in Neo4j Browser, and stop there. The deliverable is a graph a founder can open,
click an observation in, and reach the filed sentence that supports it — with every fact
carrying the claim and evidence it came from.

```text
data/extraction_runs/<run_id>/*.jsonl   (Stage 13, pending)
  → deterministic graph projection      (nodes.jsonl + edges.jsonl, no database import)
    → Neo4j, wiped and rebuilt per run  (the only module that imports a driver)
      → Neo4j Browser: styling, saved Cypher, evidence traversal
        → later: bounded subgraph retrieval for LLM post generation (contract only)
```

**The single most important input fact, as of G0:** the run exists, is complete, and is
**not** shaped the way this plan first assumed. `data/extraction_runs/extract-v1-lexical-2422c4252c07/`
holds eleven files; all ten content digests in its `run.complete` marker verify
*(`sha256sum -c run.complete` → 10× OK, 2026-08-02)*. But **no catalog row is a `model_dump()`
of any ontology model** — every row is a hand-built flat projection
(`extraction/stages/catalog/jsonl_catalog.py:176-300`), adding fields the typed models forbid
and dropping fields they declare. A reader written against `ontology.core.models` with
`extra="forbid"` fails on all 2,717 claim rows. The graph layer needs its own row models, and
§2.3 gives them.

[STAGE13_GRAPH_INPUT_HANDOFF.md](STAGE13_GRAPH_INPUT_HANDOFF.md) now records the answers rather
than the questions.

Facts marked *(verified)* were read on 2026-08-02 by the command or file:line recorded beside
them — from the committed repository at `a986321`, **except** the corpus counts and catalog
field lists, which come from the untracked working `data/` tree because `.gitignore:3` excludes
it. That distinction matters and is flagged again in §2.1: a number read off a working
directory is reproducible only while that directory exists. Facts marked *(unverified)* are
design intent, or an external property (a Neo4j edition capability) that G0–G2 must confirm.

## Corrections made before this plan was committed *(2026-08-02)*

An independent pass over every repository claim in the first draft found eight errors. They are
listed rather than quietly fixed, because two of them were load-bearing arguments and a reader
who saw only the corrected text would not know which parts of this plan had already been wrong
once.

| What the draft said | What the repository shows |
| --- | --- |
| `REPORTED_IN` targets `disclosure_channel`, "which is `accountable.opendoor.com`, **not** a filing" — and the edge is therefore unavailable | **Two** channel instances are declared: `opendoor_accountable` *and* `sec_edgar` (`canonicality: canonical`, `channel_type: regulatory_filing`). The edge is available. The conclusion survives on a different argument (§14.5) and the premise did not. |
| "four of eleven [proposed edges] exist as proposed" | Two. The draft's own table said so; the sentence above it did not. The split is 2 declared as proposed / 1 with a different direction / 8 not declared. |
| The loader's `MATCH` on endpoints makes a dangling edge "fail the load" | `MATCH` filters, it does not raise. The draft asserted a guarantee it did not have, and acceptance criterion 2 inherited it. §6.3 now counts written rows per batch. |
| `CALL { MATCH (n) DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS` | With no driving clause the subquery runs **once**, unbatched, over the whole database — the exact failure the clause was chosen to prevent. §6.2 moves the `MATCH` outside. |
| `size((u)--())` in Q8 | Removed in Neo4j 5.0; `COUNT { (u)--() }` replaces it. |
| `EventInstance.properties` is `dict[str, str]` | `dict[str, Any]` (`models.py:427`). The string discipline is something the projection must enforce, not something it inherits. |
| `Population` flattens to three properties | It has four fields; `definition_normalized` was being dropped by the same section that forbids silent dropping elsewhere. |
| Passage catalog has 30 fields; corpus counts "read from the committed repository at `a986321`" | 31 fields. And `data/` is gitignored, so those counts come from the untracked working tree — reproducible only while it exists. |

Two smaller ones: the ban-list attribution in §1.6 named the wrong test for three of four
modules, and §4.1 gave `:Document` the passage-id grammar. Both corrected in place.

## 0b. What the finalized Stage 13 run contradicted *(G0, 2026-08-02)*

Measured against `data/extraction_runs/extract-v1-lexical-2422c4252c07/`, produced by
extraction at `b1d55f4`. Seven working assumptions were wrong. Each is corrected in place
below; listed here because a plan whose assumptions were silently overwritten teaches nothing.

| # | The plan assumed | The run shows |
| --- | --- | --- |
| 1 | catalog rows are `claim.model_dump()` (P3) | **Hand-built flat dicts.** `claims.jsonl` adds `document_id`, `document_type`, `lane`, `passage_id`, `payload_id` and **omits the payload and the evidence entirely**. The full `OntologyClaim` exists nowhere on disk; reconstructing one is a three-file join on `claim_id`. §2.3 |
| 2 | four catalog files (P1) | **Eleven files**, including `events.jsonl`, `relationships.jsonl`, `rejected_claims.jsonl`, `lane_outputs.jsonl` (31,706 rows), `report.md` and the `run.complete` marker |
| 3 | events and relationships live in `claims.jsonl` *or* their own files (P2) | **Both.** `claims.jsonl` carries the 6 event and 4 relationship claim rows; the payload files carry the payloads *plus two fields computed nowhere else* — `dates_equal` and `review_flag`. Neither file alone is sufficient. |
| 4 | observations may duplicate or contradict claims (P4) | A genuine derived index: all seven shared fields agree byte-for-byte on all 2,707 rows, and 2,717 − 2,707 = exactly the 6 events + 4 relationships |
| 5 | the projection mints `:Issue` ids (§4.3) | **`issues.jsonl` already carries `issue_id`**, unique across all 17,127 rows. The minting rule is deleted, as §4.3 said it would be if Stage 13 supplied one. |
| 6 | per-claim validation warnings are recorded (P6) | **They are not.** 186 `unpreferred_source_lane` warnings exist only as a manifest aggregate; `CheckFinding.claim_id` is discarded before anything is written (`extraction/stages/verify/public.py:70-80`). Resolved by re-derivation, not by reading — §5.5. |
| 7 | observations carry a `period_key` | **No such field.** Periods are `period_start`/`period_end`/`instant_date`. The key must be recomputed with `extraction/core/models.py:134-149`, and cross-checked against the segment already inside `observation_id`. §4.1 |

**An independent review of G0 then found 25 further problems in this document** — 10 wrong
statements, 6 misleading ones, 9 risks — all corrected in place above and below. The two that
mattered most were both places where the plan would have manufactured data: §5.4 promised to
project `population_role` and `population_confidence`, which exist in no catalog and would have
been emitted as pydantic defaults; and §7.4 reasoned about the graph's size from 153 passages
when the real figure, once issue-cited passages are counted, is **8,776**. Two more were
corrections in the plan's favour: every `observation_id` *is* recomputable from the catalogs
(§4.1), and the `issues`↔`rejected_claims` join *does* exist at id level (§2.2 P5). One was an
argument built on a superseded fixture (§1.4b), which the repository had already decided the
other way.

Two further facts that change what the graph must *not* do, neither of which the plan had
anticipated at all:

- **A refusal is per-reading or per-property, never per-node.** `PARTICIPANT_NOT_NAMED`
  (severity `refusal`) *kept* its event and recorded a placeholder; `PROPERTY_VALUE_NOT_IN_PASSAGE`
  (severity `rejection`, `rejected_claim: true`) *kept* its event and dropped one property.
  Suppressing a fact because an issue names its passage would silently delete legitimate graph
  facts. §10 criterion 5 is rewritten around this.
- **10,852 of the 17,127 issues are `NO_STORED_ANSWER`** — the narrative and event lanes replayed
  15 recorded provider answers with `provider_calls_permitted: 0`, so most candidates were never
  attempted. That is a coverage fact about the *run*, not a finding about the corpus, and §3.1
  gives it its own label so it cannot be read as one.

---

# 1. What the repository already decides

## 1.1 The ontology already declares the graph vocabulary *(verified)*

`ontology/versions/real_estate_marketplace_v1/definitions/relationships.yaml` declares **30
predicates**, and they are not only entity-to-entity: `HAS_OBSERVATION`,
`OBSERVATION_OF_SUBJECT`, `EVIDENCED_BY`, `PARTICIPATES_IN`, `REPORTED_IN`, `SUPERSEDES`,
`COMPUTED_FROM`, `USES_FORMULA_VERSION`, `RECONCILES_TO` and `DISTINCT_FROM` are declared with
`allowed_source_types` / `allowed_target_types` naming `metric_definition`,
`metric_observation`, `event_type` and `normalized_passage`.

**This plan therefore invents almost no edge names.** The brief that commissioned it proposed
a list; measured against the vocabulary, **two of the eleven exist as proposed, one exists with
a different direction and different endpoint types, and eight are not declared at all**:

| Proposed | Verdict | What this plan uses |
| --- | --- | --- |
| `HOLDS_POSITION_AT` | declared, `person → [company, public_company]` | as proposed |
| `BORROWS_UNDER` | declared, `company\|public_company\|subsidiary → ` five **agreement types** (`credit_facility`, `revolving_credit_facility`, `asset_backed_debt_facility`, `term_debt_facility`, `credit_agreement`) — note `credit_agreement` is not a facility | as proposed |
| `HAS_OBSERVATION` | declared — but **`metric_definition → metric_observation`**, not company → observation | as declared; the company side is `OBSERVATION_OF_SUBJECT` |
| `OBSERVES_METRIC` | not declared; it is `HAS_OBSERVATION` reversed | dropped — one direction, the declared one |
| `SUPPORTED_BY` | not declared; the citation edge is `EVIDENCED_BY` | `EVIDENCED_BY` |
| `DISCLOSED_IN` | not declared. `REPORTED_IN` exists and targets `disclosure_channel` — of which the ontology declares **two** instances, `sec_edgar` (`canonical`, `regulatory_filing`) and `opendoor_accountable` (`discovery_only`). A channel is not a filing | no direct observation→document edge; `(:Observation)-[:EVIDENCED_BY]->(:Passage)-[:PART_OF]->(:Document)`. Whether to also emit `REPORTED_IN → sec_edgar` is §14.5 |
| `ANNOUNCED_EVENT` / `EXPERIENCED_EVENT` | not declared, and they would encode the announcement/occurrence distinction in the *edge type* | `PARTICIPATES_IN` with a `role` property; the two dates stay `Event.announced_on` / `Event.occurred_on` (§1.4) |
| `INVOLVES_PERSON` | not declared, and redundant | `PARTICIPATES_IN` |
| `FOR_PERIOD` | not declared. `fiscal_period` is a declared entity type but **no predicate connects an observation to one** | period stays on the observation as properties; a `:FiscalPeriod` node is an open decision (§14.3) |
| `HAS_ISSUE` | not declared, and correctly so — an issue is an extraction artifact, not an ontology fact | projection-local `(:Issue)-[:FOUND_IN]->(:Passage)` |

Two consequences run through the rest of this plan. First, **direction is not a style choice**
— `allowed_source_types` fixes it, and a projection that reverses an edge is asserting
something the vocabulary does not. Second, every edge the projection creates that is *not* in
`relationships.yaml` must be visibly marked as projection scaffolding (§3.3), so a reader can
never mistake plumbing for a filed fact.

`RECONCILES_TO` and `DISTINCT_FROM` are worth loading from the vocabulary itself rather than
from claims. `DISTINCT_FROM` carries the research's most load-bearing finding — which metrics
must never be merged — and putting it in the graph makes an accidental merge visible as a
contradiction rather than invisible as a missing rule.

## 1.2 The extraction run exists and is complete *(verified at G0)*

`data/extraction_runs/extract-v1-lexical-2422c4252c07/`, eleven files, produced by the
pipeline at `extraction/pipeline.py:102`.

**`run.complete` is a real completion marker, not a flag.** It is `sha256sum` format — one
`{digest}  {filename}` line per file, sorted by name, covering all ten other files including
`manifest.json` (`extraction/core/run_directory.py:66-68`, written last at `:129-130`, then
`os.replace(staging, final)` at `:131-134`). `RunDirectory.__init__` refuses a directory
without it (`:141-147`). Verified 2026-08-02: `sha256sum -c run.complete` → 10 × OK.

**But complete does not mean verified**, and the graph loader must not confuse them:
`pipeline.py:126` runs verification and `pipeline.py:174` finalizes unconditionally, so a run
with failing checks still produces a marked directory. The signal is
`manifest["verification"][*]["passed"]`. For this run all five checks passed —
`evidence_resolution` 2,717/0, `ontology_validation` 2,717/0 errors + **186 warnings**,
`forbidden_lanes` 2,707/0, `duplicate_identities` 25,324/0, `conflicting_duplicates` 2,707/0.

**The payload models are still the vocabulary's, and still frozen** — `OntologyClaim`,
`MetricObservation`, `EventInstance`, `RelationshipInstance`, `EvidenceReference` and
`Population` are `extra="forbid"` pydantic models (`ontology/core/models.py:333-476`). What
changed at G0 is that the *catalogs are not those models serialized*. §2.3.

**One provenance imprecision, recorded rather than propagated.** The manifest records
`code_commit: 4d3ae1e`, one commit before the finalized `b1d55f4`. The data is nonetheless
`b1d55f4`'s output: that commit's own message states the numbers this run carries — 2,707
observations ("down 8"), 2,717 claims, 186 warnings, 25,324 duplicate identities — and
`code_commit` is read from `git rev-parse HEAD` at run time
(`extraction/core/manifest.py:44-51`), so a run executed from a working tree that was committed
afterwards attributes itself to the previous commit. The graph manifest will record **both**
the manifest's `code_commit` and the repository HEAD it was projected at, rather than silently
carrying a commit that does not contain the guard which produced the data.

## 1.3 Claims are already validated, and validation state is not binary *(verified)*

`extraction/core/validation.py` fails a run on four codes — `EVIDENCE_UNRESOLVED`,
`EVIDENCE_MISSING`, `FORBIDDEN_SOURCE_LANE`, `DUPLICATE_OBSERVATION_CONFLICT`, plus any
`ontology.validate_claims` error — and **relays two kinds of warning** rather than dropping
them: `QUOTED_TEXT_NOT_IN_PASSAGE` and `ONTOLOGY_WARNING` (which carries
`unpreferred_source_lane`, the vocabulary saying a metric it expects from a table arrived from
prose).

So a claim that reaches a catalog is error-free by construction, and may still carry a
warning. The graph must be able to say which (§5.5) — "0 errors" and "clean" are different
statements, and `extraction/core/validation.py:192-221` exists because they were once conflated (the reasoning is in its docstring at `:197-203`, the relay at `:216-220`).

Separately, three things a run produces are **not** claims and must never load as facts:
`LaneAbstention` (a lane's recorded silence), `PolicyRejection` (a §7 policy refusing a lane
claim) and `DeferredMetric` (a metric whose required lane does not exist). They are the reason
the `:Issue` node exists.

## 1.4 Identity: three hazards, all measured, none of them bugs

The extraction id scheme is deliberate and this plan does not ask to change it. But two of its
properties become *graph* problems the moment ids are used as node keys.

**(a) Every unnamed subsidiary in the corpus collapses into one node.**
`unresolved_entity_id(subject_entity_id, entity_type)` returns
`f"{entity_id(subject)}_unnamed_{entity_id(entity_type)}"` — no passage, no event, no
counter (`extraction/core/identifiers.py:125-133`). In the finalized run it appears once, as the
borrower of `evt:credit-facility-established:2022-10-19:4b384d2ba0fa` and as the `source_id` of
`rel:borrows-under:opendoor-unnamed-subsidiary:asset-backed-senior-revolving-credit-facility:a129a14c7956`
*(verified at G0)*. One occurrence is not a refutation: every future filing that says "a
subsidiary of the Company" mints that same string, and keyed naively **one Neo4j node would
accumulate the debt obligations of every unnamed Opendoor subsidiary in the corpus** — the
mis-attribution the placeholder was invented to prevent. §4.2 is the fix and §14.1 the decision.

**The catalogs make this harder than the lane models did**, and G0 measured it: the catalog's
participant object is exactly `{role, entity_id, entity_type}`
(`jsonl_catalog.py:224-261`), so `LaneEventParticipant.named` and `entity_text` — both of which
exist on the lane model (`extraction/core/models.py:230-249`) — **do not survive into any
catalog**. The `_unnamed_` substring in the id is the only placeholder signal that reaches the
graph. Detecting placeholders by id shape is therefore not a convenience; it is the sole
available mechanism, which is exactly what the extraction docstring says the uniform
placeholder exists to provide. The printed description survives only for *relationship*
endpoints, as `extractor_metadata.source_name` / `target_name` on the 4 relationship claims.

**(b) Named entities are name-slugs, and the same name is the same node.**
`_resolve_entity` (`extraction/stages/narrative/event_mapping.py:458-483`) returns a declared
ontology instance id when the whole printed name folds to one, and otherwise `entity_id(name)`
— the slug of whatever the passage printed. Entity resolution is explicitly not attempted.

**The draft argued this from a superseded annotation, and the correction cuts the other way**
*(found by review, 2026-08-02)*. It cited the benchmark gold calling the 2022 facility
`asset_backed_senior_revolving_2022_10` against the lane's
`asset_backed_senior_revolving_credit_facility`. That gold was **changed by a founder decision
before this plan was written**: `benchmarks/extraction/v1/cases/06_events_and_relationships.yaml:27-33`
records that the old id "encodes a year-month the passage never prints", and the live gold at
`:180` and `:208` now uses the lane's id. So the repository decided in favour of the printed
name, and citing the retired id as evidence of an over-merge was wrong.

**The hazard is real anyway, and the argument for it is structural rather than anecdotal.**
`entity_id` is a pure function of the printed string, so two filings describing one facility in
different words mint two nodes, and two different facilities described in the same generic words
mint one. Nothing in the pipeline can tell either case from a correct answer. V1 accepts that
and *measures* it (§10, criterion 8) rather than resolving it — which is the same position, now
resting on the mechanism instead of on a stale fixture.

**(c) Two filings reporting one fact are two observations, on purpose.**
`observation_id` ends in `digest(passage_id, *structural_position)` — the passage plus, for a
table reading, its grid coordinates (`identifiers.py:69-99`, fed from
`LaneClaim.structural_position` at `extraction/core/models.py:248-265`). So a letter rounding to
`$38 million` and a reconciliation table stating `38,228` thousand stay distinct, and so do two
cells of one table.

**That discriminator is precisely why the conflict guard cannot key on the id**, and Stage 13's
last commit turns on the distinction. `DeterministicTableClaimLane._refuse_rows_that_disagree_with_themselves`
(`extraction/stages/tables/deterministic_lane.py:174-229`) keys on the *structural* identity
`(metric_id, subject_entity_id, period.key, source_lane, passage_id)` — grid position
deliberately excluded — and refuses **both** cells when one row yields two different values for
one identity. Keyed on `observation_id`, the two cells would look like two legitimate readings
and the contradiction would be invisible. The graph inherits the result, not the mechanism: the
refused readings are absent from `observations.jsonl` and present in `issues.jsonl` as
`AMBIGUOUS_COLUMN_ALIGNMENT`, verified at G0 to be a complete refusal (§2.4).
`SUPERSEDES` is declared in the ontology and is deliberately **not** emitted by extraction —
`V1_CLAIM_EXTRACTION` §7.5 defers it to "a graph-layer policy over a complete set of
observations". This plan **also does not emit it** (§12): a policy that decides which filing
supersedes which is a research question, and V1's job is to make the duplication visible
(query Q9) so that question can be asked with evidence.

## 1.5 The evidence boundary is the passage, and it does not move *(verified)*

`V1_CLAIM_EXTRACTION` §8a.11 is explicit: a claim cites a `passage_id` that resolves in
`passages.jsonl`, and **no synthetic sub-passage anchor may be created**. Quoted spans travel
as `EvidenceReference.quoted_text`, checked against the passage text as a warning, never as an
anchor.

The graph inherits this unchanged: `:Passage` is the finest-grained evidence node there is.
Quoted text is a *property* of the citing fact, not a node, and character offsets are
properties used for highlighting, not identity.

## 1.6 The repository already argues for Neo4j, and already ignores its data directory

`docs/02_COMPONENT_OPTIONS.md:313-341` evaluates Neo4j as the `GraphRepository` and concludes
it is the "best general-purpose graph backend when transparency, querying, and visual
inspection are priorities". `docs/03_TARGET_COMBINATIONS.md:386-395` recommends starting with a
narrow custom extraction strategy and a local graph repository, and implementing Graphiti only
later as a second `GraphBuilder` adapter for comparison. `.gitignore:34` already lists
`neo4j_data/` *(verified)*. **That gitignore entry turned out to be moot** — the store cannot
live on this working tree at all, because `/mnt/c` is NTFS over WSL 9p and Neo4j's files hit
locking and ownership failures there. The data lives in named Docker volumes instead; §5.1
records the correction.

Nothing in the repository argues against Neo4j, and no requirement was found that Neo4j Browser
cannot satisfy for a first look. **No custom viewer is planned** (§12). The one thing this plan
does *not* adopt from doc 03 is Graphiti — it is a second builder for a later comparison, and
building two before the first has been looked at would be the premature commitment doc 03 warns
about.

Two structural tests already ban graph-database imports, and they ban different lists
*(verified)*: `tests/normalization/test_pipeline_structure.py:180-184` bans `graphiti`,
`neo4j`, `py2neo` and `cypher` inside `normalization/`;
`tests/ontology/test_package_structure.py:16-23` bans `neo4j`, `networkx`, `rdflib`,
`owlready2` and `sparqlwrapper` inside `ontology/`. Each walks only its own package root, no
test enumerates top-level repository directories, `pyproject.toml` declares no package list,
and nothing under `tests/extraction/` forbids graph construction — so a new top-level `graph/`
package violates none of them. §11 adds the mirror-image tests so it stays that way.

---

# 2. Inputs from Stage 13 — measured at G0

Every P-item of the pre-run draft is now answered against
`data/extraction_runs/extract-v1-lexical-2422c4252c07/`. §2.1 is the part fixed by typed code;
§2.2 is the answer table; §2.3 is the row contract the readers implement; §2.4 records the
refusal check.

## 2.1 Fixed by committed, typed code

| Input | Where it is fixed | Note |
| --- | --- | --- |
| `OntologyClaim` fields | `ontology/core/models.py:451-476` | `claim_id`, `claim_kind`, one of three payloads, `assertion_type`, `confidence`, `evidence`, `extractor_metadata` |
| `MetricObservation` fields | `models.py:373-398` | incl. `population`, `dimensions`, `reporting_basis`, `source_lane`, `input_observation_ids` |
| `EventInstance` fields | `models.py:409-431` | **two dates**, `participants`, `properties`, `reported_metric_observation_ids` |
| `RelationshipInstance` fields | `models.py:434-448` | endpoints + types, `context_id`, `valid_from`/`valid_to`; **no `properties` field** |
| `EvidenceReference` fields | `models.py:333-361` | `evidence_kind`, `passage_id`, `document_id`, `table_id`, `block_ids`, char offsets, `quoted_text`, `source_url`, `xbrl_concept`, `accession`, `disclosure_channel_id` |
| Observation / claim / event / relationship id grammars | `extraction/core/identifiers.py` | §4.1 |
| Predicate vocabulary and endpoint types | `definitions/relationships.yaml` (30) | §3.2 |
| Event types, participants, allowed properties | `definitions/events.yaml` (19) | §3.2 |
| Entity, role, instrument, agreement types | `entities.yaml`, `roles.yaml`, `instruments.yaml` | §3.1 |
| Ontology `definition_hash` | `e8d4af709be2…` | goes in the graph manifest |
| Passage / document catalog schema | `data/normalization_catalog/*.jsonl` — **not committed** (`.gitignore:3` is `data/`), so this row was measured against the working corpus on 2026-08-02, not against `a986321` | 31 and 35 fields (`{31: 12442}` — every row); ordering deterministic, keys sorted; passages 12,442, documents 294 |
| Atomic finalization | staging `<run_id>.partial/` → digests → `run.complete` last → `os.replace` (`extraction/core/run_directory.py:82-135`) | the graph run reuses the pattern |

## 2.2 P1–P11, answered *(G0, 2026-08-02)*

| # | Answer |
| --- | --- |
| **P1** | `data/extraction_runs/extract-v1-lexical-2422c4252c07/`. **Eleven files**: `claims`, `observations`, `evidence`, `events`, `relationships`, `issues`, `rejected_claims`, `lane_outputs` (all `.jsonl`), `manifest.json`, `report.md`, `run.complete`. The run id is `extract-v1-lexical-{hash12}` — digested from `(RUN_LAYOUT_VERSION, config_hash, corpus_id, ontology_definition_hash)` (`run_directory.py:58`); `strategy` appears in the readable prefix only and is called "redundant by design" at `:49-52`, **not** the other layers' `{UTC}Z-{hash8}`. It contains no clock, which is why two runs produce one directory name. |
| **P2** | **Both.** `claims.jsonl` holds all 2,717 claim rows including 6 `event` and 4 `relationship`. `events.jsonl` / `relationships.jsonl` hold the payloads. `{claims.payload_id}` equals `{events.event_id}` and `{relationships.relationship_instance_id}` exactly, both directions. |
| **P3** | **Not `model_dump()`.** Hand-built dicts, `jsonl_catalog.py:176-300`. §2.3 gives every field. |
| **P4** | `observations.jsonl` is a genuine **derived index**: all seven shared fields agree byte-for-byte across all 2,707 rows; the 10-row gap from `claims.jsonl` is exactly the 6 events + 4 relationships. But it is not redundant — it carries 17 fields `claims.jsonl` lacks. Read both. |
| **P5** | One file, 13 keys, uniform across all 17,127 rows, **already carrying a unique `issue_id`**. The four categories are discriminated by `severity`: `not_attempted` 10,852, `refusal` 6,228, `rejection` 46, `diagnostic` 1. `rejected_claim` (bool) is the join flag to `rejected_claims.jsonl`, and **there is an id-level join after all** *(corrected by review)*: `issue_id` and `rejection_id` are both `_ordinal_id` over the same parts tuple (`jsonl_catalog.py:84-96`), so they share a hex — **46 of 46 verified**. The join holds only for `refused_by: "lane"` rejections; an `assemble`-origin rejection writes no mirroring issue row at all (`:98-121`), and this run happens to have none. Still: do not sum the two files. |
| **P6** | **Not recorded.** The 186 `unpreferred_source_lane` warnings survive only as a manifest aggregate; `CheckFinding.claim_id` is discarded at `extraction/stages/verify/public.py:70-80`. Resolved by re-derivation — §5.5. |
| **P7** | **38 distinct keys in 8 shapes.** All four §8a.12 period fields are present on the 17 narrative claims. Keys are **omitted, not nulled**, when absent — 84 table claims carry `scale_source` but no `scale` key at all. §5.4. |
| **P8** | `manifest.json` inside the run directory (not `extraction_manifests/`). Carries `run_id`, `created_at`, `config_hash`, `code_commit`, `extractor_version`, `layout_version`, `ontology_id` + `ontology_definition_hash`, `corpus` (content-addressed `corpus_id`, **no upstream normalization run id**), `scope`, `lanes`, `provider`, `bounds`, `counts`, `verification`, `catalog_digests`, `environment`, `dependencies`. **No `finished_at`.** |
| **P9** | `lexical` — `scope: {strategy: lexical, scope_name: lexical, scope_version: 1.0.0}`. |
| **P10** | Deferred metrics: yes, 358 `DEFERRED_REQUIRED_SOURCE_LANE` issues. Rejections: yes, 46 rows in **`rejected_claims.jsonl`**, mirrored as 46 `severity: rejection` rows in `issues.jsonl`. **Do not sum them — one finding, two files, two different ids.** |
| **P11** | 2,707 observations · 2,717 claims · 2,717 evidence rows (one per claim, `evidence_index` always 0) · 6 events · 4 relationships · 17,127 issues · 46 rejections · **17 distinct metrics** · **1 subject** (`opendoor`) · **153 cited passages** · **49 cited documents** · 144 table ids · 2 source lanes (`normalized_table` 2,690, `normalized_narrative` 17) · 75 distinct period triples. |

**The rule survives the corrections: fail loudly, never default.** The readers (§11, G1) are
`extra="forbid"` models over each row *as the catalog actually writes it*. An unexpected field
stops the projection by name. Nothing substitutes a default for a missing input.

## 2.3 The row contract the readers implement

**Seven** row models, because there are seven row shapes *(the draft said six and omitted
`rejected_claims.jsonl`; corrected by review)*. Field lists are exact
(`extraction/stages/catalog/jsonl_catalog.py`, line ranges beside each).

| File | Rows | Keys |
| --- | --- | --- |
| `claims.jsonl` `:176-189` | 2,717 | `claim_id`, `claim_kind`, `payload_id`, `lane`, `passage_id`, `document_id`, `document_type`, `assertion_type`, `confidence`, `extractor_metadata` |
| `observations.jsonl` `:192-221` | 2,707 | **24 keys, not "claims plus"** — it drops `claim_kind`, `payload_id` and `extractor_metadata`. Seven shared with `claims.jsonl` (`claim_id`, `lane`, `passage_id`, `document_id`, `document_type`, `assertion_type`, `confidence`) plus `observation_id`, `metric_id`, `subject_entity_id`, `subject_type`, `value`, `unit`, `currency`, `period_start`, `period_end`, `instant_date`, `population_definition_raw`, `source_lane`, `ambiguity_codes`, `scale`, `scale_location`, `row_label`, `column_label` |
| `events.jsonl` `:224-261` | 6 | `event_id`, `claim_id`, `event_type_id`, `occurred_on`, `announced_on`, `occurrence_date_text`, `announcement_date_text`, `dates_equal`, `review_flag`, `participants[{role,entity_id,entity_type}]`, `properties`, `assertion_type`, `passage_id`, `document_id`, `document_type`, `evidence_quoted_text`, `lane` |
| `relationships.jsonl` `:264-282` | 4 | `relationship_instance_id`, `claim_id`, `relationship_id`, `source_id`, `source_type`, `target_id`, `target_type`, `valid_from`, `valid_to`, `assertion_type`, `passage_id`, `document_id`, `document_type`, `lane` |
| `evidence.jsonl` `:285-300` | 2,717 | `claim_id`, `claim_kind`, `evidence_index`, `evidence_kind`, `passage_id`, `document_id`, `table_id`, `block_ids`, `source_url`, `quoted_text` |
| `issues.jsonl` `:86-90` | 17,127 | `issue_id`, `code`, `severity`, `lane`, `passage_id`, `document_id`, `document_type`, `concept_ids`, `row_label`, `quoted_span`, `request_sha256`, `rejected_claim`, `detail` |
| `rejected_claims.jsonl` `:92-121` | 46 | the 12 issue fields minus `issue_id`, plus `rejection_id`, `refused_by`, `raw_finding` — **a seventh shape** *(added by review; the draft said six)*. Projected as `:Issue:Rejected`, carrying `raw_finding` only as an opaque `raw_finding_json` string: it is the **pre-validation model output** and its `value`/`unit`/`scale` are frequently the reason the claim was refused. It must never be read as fact. |

Five traps the readers must encode, all measured:

1. **`lane` and `source_lane` are different vocabularies for one claim.** `lane` is the routing
   lane (`tables` / `narrative` / `events`, `jsonl_catalog.py:141-151`); `source_lane` is the
   ontology's (`normalized_table` / `normalized_narrative`). The graph carries both and never
   maps one to the other.
2. **`passage_id`, `document_id`, `document_type` can be empty strings, not null**
   (`jsonl_catalog.py:170-173`) — legal for a calculated observation. Zero occurrences in this
   run; the reader still must not treat `""` as a valid node key.
3. **No char offsets in `evidence.jsonl`.** `char_start`/`char_end` are dropped. Spans survive
   only as `extractor_metadata.span_char_start`/`span_char_end` on 23 claims — **17 narrative +
   6 event**, which is every narrative claim in the run — and on none of the 2,690 table claims. §7.2's highlighting is therefore narrative-only.
4. **`evidence.jsonl`'s identity is `(claim_id, passage_id)`**, not `claim_id` alone
   (`catalog/public.py:49`). One evidence row per claim today; do not build on that.
5. **`confidence` is null on all 2,717 rows**, and `value` is a float on all 2,707 — including
   counts like `market_count`. The graph stores the float and does not re-type it.

## 2.4 The conflict guard, verified complete *(G0)*

The refusal is recorded as `AMBIGUOUS_COLUMN_ALIGNMENT` — 5 issue rows, of which **4 are the
same-identity-different-value guard** and 1 is an unrelated whole-table refusal for
`group_assignment_ambiguous`. The four are two metrics × two nine-month periods on
`…q32023formxex991earningsre.htm#p29`, e.g. `adjusted_ebitda` at `2023-01-01_2023-09-30`
holding both −49,000,000 and −558,000,000.

**Checked at G0, and clean:** for each of the four refused identities, zero observations exist
in `observations.jsonl` with that `(metric, subject, period, lane, passage)`, and zero carry the
refused values. The whole-table refusal's passage has no observations at all. Passage `#p29`
retains 6 observations — two metrics × three **nine-month rolling** durations
(`2022-04-01_2022-12-31`, `2022-07-01_2023-03-31`, `2022-10-01_2023-06-30`), which are not the
periods that collided.

**The graph inherits a clean input and must not re-litigate it.** No projection rule filters
observations by issue; the refusal already happened upstream. What the graph *does* add is
criterion 10 (§10), which re-runs the membership check after loading, so a projection bug that
resurrected a refused reading would fail the build rather than ship a contradiction.

---

# 3. The graph model

## 3.1 Node types

Every node carries a base label plus a concrete label. The base label is what uniqueness
constraints and generic queries attach to; the concrete label is what Browser styling and
readable Cypher use.

| Base label | Concrete labels | Key property | Source |
| --- | --- | --- | --- |
| `:Entity` | the PascalCase of the payload's own `entity_type` **plus its `is_a` ancestors** — so `:PublicCompany:Company`, `:Subsidiary:Company`, `:Person`, `:AssetBackedDebtFacility:CreditFacility:CreditAgreement:Agreement`, … | `entity_id` | ontology `instances` (4) + claim participants and relationship endpoints |
| `:Observation` | — | `observation_id` | `claim_kind: metric_observation` |
| `:Event` | — | `event_id` | `claim_kind: event` |
| `:Metric` | — | `metric_id` | ontology `metric_definition` (26) |
| `:Passage` | — | `passage_id` | `passages.jsonl`, only passages some claim or issue cites — **8,776** *(153 by claims, 8,757 by issues; measured at G0)* |
| `:Document` | — | `document_id` | `documents.jsonl`, only documents of cited passages — **185** *(49 by claims; measured at G0)* |
| `:Issue` | `:NotAttempted` on the 10,852 `NO_STORED_ANSWER` rows; `:Rejected` on the 46 a `rejected_claims.jsonl` row mirrors | `issue_id` (**supplied by the run**, §4.3), or `rejection_id` for an unmirrored rejection | `issues.jsonl` **plus** any `rejected_claims.jsonl` row `issues.jsonl` does not mirror |

Concrete entity labels are derived from the payload's own `entity_type` / `subject_type`
string, PascalCased, **with the ontology's `is_a` ancestors added** — so `opendoor` is
`:Entity:PublicCompany:Company` and a subsidiary is `:Entity:Subsidiary:Company`. That makes
`MATCH (c:Company)` find every company without the query needing to know the subtype list,
which is what `registry.ancestors()` exists to answer.

**Two corrections from review, either of which would have broken the builder:**

1. **`ancestors()` takes a concept id, not an instance id.** `ancestors('public_company')` →
   `('company',)`, but `ancestors('opendoor')` → `()`. The builder must resolve an instance to
   its `concept_id` first, then ask for ancestors.
2. **`entity_type` is not always an `entity_type` concept.** The 13 declared entity types
   include no facility and no instrument: the run's facility participant carries
   `entity_type: asset_backed_debt_facility`, which is an **`agreement_type`**, and
   `financial_instrument` is a `financial_instrument_type`. The draft's `:Facility`,
   `:Agreement` and `:Instrument` labels named no declared type at all. The rule is therefore
   stated over *whatever category the concept belongs to*: the label is the concept id, and
   `is_a` chains resolve inside `agreement_type` and `financial_instrument_type` exactly as
   inside `entity_type`. A type the registry does not know is an `UNDECLARED_ENDPOINT_TYPE`
   rejection (§6.4), never a guessed label.

A second, non-exclusive label `:Unresolved` marks any entity node the filing did not name
(§4.2). Styling it distinctly in Browser is the cheapest possible guard against reading a
placeholder as an answer.

**An `assemble`-origin rejection gets an `:Issue:Rejected` node too** *(added 2026-08-03 after
review)*. `jsonl_catalog.py:98-121` writes **no** `issues.jsonl` row for such a refusal, so a
builder that iterated `issues.jsonl` alone projected it nowhere — no node, no label, no count —
which §10 criterion 5 forbids: refusals must *appear*. It is keyed on its `rejection_id` (the
run's own id, §4.3) and carries **no `issue_id` property**, because it has no issue row; that
absence is the signal that the two files are never summed (§2.2 P10). Zero such rows in this
run, so no count in this document moves.

**No node property is ever null.** In Cypher `SET n += {k: null}` *removes* `k`, so a null in
`nodes.jsonl` describes a property the loaded graph will not have. The projection drops it, as
`edges.jsonl` always did — 53,951 such properties were exported before this was made uniform
*(measured 2026-08-03)*. Absence and null are one fact in the source too: `extractor_metadata`
omits keys rather than nulling them (§5.4 point 1).

**Deliberately not nodes:**

| Not a node | Why |
| --- | --- |
| `:Claim` | `claim_id = f(payload_id)` — a claim is 1:1 with its payload, so a Claim node would double the graph and add a hop to every evidence path for no information. `claim_id` is a property of the observation, event, or relationship edge. |
| A relationship claim | Neo4j has no edge-on-edge. A `RelationshipInstance` is projected as the edge it describes, carrying `claim_id`, `passage_id`, `document_id` and `quoted_text` as edge properties. Reifying was rejected (§13). |
| `:Period` / `:FiscalPeriod` | No ontology predicate connects an observation to a period, and `period_key` grouping in Cypher answers the timeline queries without one. Open decision §14.3. |
| A quoted span | §1.5 — the evidence boundary is the passage. |
| `:Run` | Run provenance is a property on every node and edge plus a graph manifest on disk; a run node invites `MATCH (n)-[:IN_RUN]->` on every query for nothing a property does not give. |

## 3.2 Edge types

**Ontology-declared** — name, direction and endpoint types come from `relationships.yaml`.
The projection reads them from the registry rather than hard-coding a list, so a vocabulary
edit cannot silently disagree with the code (the same rule `deferred_metric_ids` follows).

**One wrinkle review found in that plan, and G1 found the correction was itself half wrong:**
five endpoint type names in `relationships.yaml` — `metric_definition`, `metric_observation`,
`event_type`, `relationship_type`, `metric_formula` — are not registry concepts, and
`registry.concept(...)` raises `ConceptNotFoundError` for each. They are exactly the structural
predicates §1.1 is built on (`HAS_OBSERVATION`, `EVIDENCED_BY`, `PARTICIPATES_IN`, `SUPERSEDES`).

**Four of the five are `ConceptCategory` members. `metric_observation` is a `ClaimKind`**
*(measured at G1: `'metric_observation' in [c.value for c in ConceptCategory]` is False)*.
Resolving against `ConceptCategory` alone — which is what this section said to do — would still
have rejected `HAS_OBSERVATION`, `OBSERVATION_OF_SUBJECT` and `EVIDENCED_BY`, i.e. the whole
backbone the wrinkle exists to protect. The validator resolves a name as a concept, a
`ConceptCategory`, **or** a `ClaimKind`, and only a name that is none of the three raises
`UNDECLARED_ENDPOINT_TYPE`.

**Endpoint checking is subtype-aware, and that is the ontology's own rule rather than a
widening.** The run's facility participant carries `entity_type: asset_backed_debt_facility`
while `PARTICIPATES_IN` declares `credit_facility`; exact membership would drop a filed
participant. `ConceptRegistry.accepts_type` (`ontology/registry.py:176-183`) already resolves
`is_a` chains for exactly this reason — "a slot declaring `company` accepts `public_company`
… without this, every declaration would have to enumerate its own subtypes and would silently
rot". The projection asks that method rather than reimplementing membership. The widening is
one-directional: a type the registry does not know at all is still an error.

| Edge | From → To | Emitted from |
| --- | --- | --- |
| `HAS_OBSERVATION` | `:Metric` → `:Observation` | every metric-observation claim |
| `OBSERVATION_OF_SUBJECT` | `:Observation` → `:Entity` | `subject_entity_id` |
| `EVIDENCED_BY` | `:Observation`, `:Event` → `:Passage` | each `EvidenceReference` |
| `PARTICIPATES_IN` | `:Entity` → `:Event` | each `EventParticipantRef`, `role` on the edge |
| `RECONCILES_TO`, `DISTINCT_FROM` | `:Metric` → `:Metric` | the **ontology**, not a claim |
| `BORROWS_UNDER`, `LENDS_UNDER`, `SECURED_BY`, `GUARANTEED_BY`, `HOLDS_POSITION_AT`, `PARTNERED_WITH`, `COMPETES_WITH`, `ACQUIRED`, `ISSUED`, `LISTED_ON`, `OPERATES_IN`, `OFFERS`, `AVAILABLE_IN`, `PARTY_TO`, `SUBSIDIARY_OF`, `HAS_SUBSIDIARY`, `REGULATED_BY`, `CUSTOMER_OF`, `SUPPLIER_TO`, `FEATURE_OF` | as declared | relationship claims, whichever the lane emits |
| `REPORTED_IN` | `:Observation`, `:Event` → `:DisclosureChannel` | **not emitted in V1**, and the reason is that it would be constant, not that it is unavailable. `sec_edgar` is a declared canonical channel and every V1 fact came through it, so the edge would attach the same hub to every observation and discriminate nothing. It earns its place when a second channel actually produces claims — and `V1_CLAIM_EXTRACTION` §7.2 makes `opendoor_accountable` discovery-only and uncitable, so that is not V1. §14.5 |
| `SUPERSEDES`, `COMPUTED_FROM`, `USES_FORMULA_VERSION` | — | **not emitted in V1** (§1.4c, §12) |

**Projection-local** — plumbing, not filed facts. Each carries `ontology_declared: false`;
declared edges carry `ontology_declared: true`. Four, and no more without a stated reason:

| Edge | From → To | Why it is needed |
| --- | --- | --- |
| `PART_OF` | `:Passage` → `:Document` | "all claims from one filing" and every evidence panel |
| `FOUND_IN` | `:Issue` → `:Passage` | issues and abstentions are located, not free-floating |
| `CONCERNS_METRIC` | `:Issue` → `:Metric` | an `AMBIGUOUS_ALIAS` issue names its candidate metrics; without this, Q9 cannot say which metric a silence was about |
| `PLACEHOLDER_FOR` | `:Unresolved` entity → `:Entity` | connects an unnamed participant to the parent it was described relative to, **without** asserting identity (§4.2) |

## 3.3 The rule that keeps the two apart

A reader looking at the graph must be able to tell a filed fact from projection scaffolding
without consulting this document. Three mechanisms, all cheap:

1. `ontology_declared` on every edge;
2. `assertion_type` on every fact-bearing node and edge, carried through from the claim
   (`reported` / `calculated` / `classified` / `inferred`), plus `assertion: ontology_definition`
   on the `DISTINCT_FROM` / `RECONCILES_TO` edges that come from the vocabulary rather than a
   filing;
3. `:Issue` nodes have **no** edge into the fact graph other than `FOUND_IN` and
   `CONCERNS_METRIC`. An abstention can never be reached by following evidence from an
   observation, because it is not evidence for anything.

---

# 4. Identity and deduplication

## 4.1 Node keys are the extraction ids, unchanged

| Node | Key | Grammar |
| --- | --- | --- |
| `:Observation` | `observation_id` | `obs:{metric}:{subject}:{period_key}:{lane}:{digest12(passage_id, *structural_position)}` |
| `:Event` | `event_id` | `evt:{type}:{occurred_on\|undated}:{digest12(passage, sorted participants)}` |
| `:Metric` | `metric_id` | ontology concept id |
| `:Passage` | `passage_id` | `norm:{cik}:{accession}:{file}#p{n}` |
| `:Document` | `document_id` | `norm:{cik}:{accession}:{file}` — the same string **without** the `#p{n}` suffix, which is why `PART_OF` can be derived without a lookup |
| `:Entity` (named) | `entity_id` | ontology instance id, else the name slug |

Readable ids are the point, not an accident — they surface in evidence panels and in the
Browser's node captions, and a graph keyed on UUIDs would be unusable in exactly the tool this
plan is optimising for.

Relationship edges are keyed for idempotence on `relationship_instance_id`, which the run
supplies as a first-class column. The loader `MERGE`s on that property, never on the endpoint
pair alone — two filings asserting `BORROWS_UNDER` between the same pair are two evidenced
assertions, and collapsing them would discard a citation.

**`period_key` is computed, not read** *(corrected at G0)*. No catalog carries it; observations
carry `period_start` / `period_end` / `instant_date` only. The projection recomputes it with
`PeriodRef.key`'s algorithm (`extraction/core/models.py:134-149` — `FY2023`, `2023Q4`,
`2023-12-31`, else `{start}_{end}`). 75 distinct period triples; 2,304 durations, 403 instants.

**And the check is the whole id, not just the period segment** *(strengthened at G0)*. Because
the id-bearing grid coordinates survive into `extractor_metadata` (§5.4 point 3), G1 recomputes
every `observation_id` end to end — metric, subject, recomputed period key, lane, and
`digest(passage_id, "row=…", "column=…")` — and asserts it equals the id the catalog supplied.
**Verified at G0: 2,707 of 2,707, zero mismatches.** That check subsumes the period check and
turns any divergence between the graph's reading of an observation and extraction's own identity
rule into a build failure, rather than a node quietly keyed on a stale id.

## 4.2 Unresolved entities are never merged

**Policy.** A participant with `named: false` is projected as its own node, keyed
`{extraction_placeholder_id}#{event_id}` — one node per event, never one node per corpus.
It carries:

```text
:Entity:Subsidiary:Unresolved {
  entity_id:            "opendoor_unnamed_subsidiary#evt:credit-facility-established:2022-10-19:4b384d2ba0fa",
  extraction_entity_id: "opendoor_unnamed_subsidiary",   -- what the run said, kept verbatim
  entity_text:          "a subsidiary of the Company",   -- what the filing printed
  resolved:             false
}
```

and one `PLACEHOLDER_FOR` edge to `opendoor`, which records *what it was described relative
to* and asserts nothing about which subsidiary it is.

**Why the projection mints a key extraction did not.** Extraction is per-passage and cannot
see the other filing; its placeholder is correct at its own scale. A graph is corpus-scale,
and at that scale the same string is an assertion that all these are one company. Choosing to
over-split rather than over-merge follows the repository's existing bias: `distinct_from`
exists to stop concepts collapsing into each other, and an accidental merge is silent while an
accidental split is visible and countable. `extraction_entity_id` is retained precisely so a
later entity-resolution pass can find every placeholder by shape — the property the extraction
docstring says the uniform placeholder exists to provide.

This is §14.1, a founder decision, and it is the one place this plan deviates from an
extraction id.

## 4.3 Issue ids — supplied, not minted *(corrected at G0)*

The draft minted an issue id and said the rule would be deleted if Stage 13 supplied one. It
does: `issue_id` (`issue:{12 hex}`), unique across all 17,127 rows. **The minting rule is
deleted.** The projection uses `issue_id` as the `:Issue` key.

One property to record rather than rely on: `issue_id` and `rejection_id` are *ordinal* ids —
`digest(code, lane, passage_id, row_label, detail, occurrence_number)` where the occurrence
number counts position within `lane_outputs.jsonl` (`jsonl_catalog.py:45-55`, `:81-96`). They
are stable for a given run and are **not content addresses**: inserting an unrelated issue
earlier in the same group renumbers later ones. So they are valid node keys within a rebuild of
one run — which is all V1 needs — and must not be used to diff issues across two different
runs. §12's exclusion of incremental loading is what keeps that safe.

## 4.4 Determinism

The projection is a **pure function** of (extraction run catalogs, ontology definitions,
normalization catalogs, projection version). It must contain no timestamp, no random value, no
dictionary-iteration-order dependence, and no clock. It writes `nodes.jsonl` and `edges.jsonl`
with `sort_keys=True` and a declared sort order (nodes by `(label_base, key)`, edges by
`(type, source_key, target_key, edge_key)`), matching `normalization/utils/jsonl.py`.

**Acceptance:** two projections of one extraction run produce byte-identical exports, and two
loads of one export produce identical node, edge and property counts (§10). The two projections
must run in **two processes with different `PYTHONHASHSEED` values** *(added 2026-08-03)*: a
comparison made inside one interpreter shares that interpreter's seed, so a builder that
iterated a `set` into a property would produce the same wrong order twice and the check would
pass. `tests/graph/test_export_determinism.py` shells out for exactly this reason.

**The graph run id derives from the input bytes, not only from the input's name.**
`graph-v1-<digest12>` digests the projection version, the extraction run id, the ontology
definition hash **and** `input_content_digest` — the completion marker, the sha256 of each of
the seven catalog files, and the loaded row counts. Without the fourth term,
`tests/fixtures/graph/extraction_run/` — which ships the run's manifest verbatim, deliberately
— minted the same id as the full run, and projecting the fixture removed the real export
(§6.4's note on `.rejected/` records the sibling failure). All four terms are functions of the
input; none of them is a clock, so byte-identity is unaffected.

---

# 5. Neo4j design

## 5.1 Deployment

Local Neo4j **5.26.28** Community in Docker, container `fkg-neo4j`, loopback only. Bolt on
`localhost:7687`, Browser on `localhost:7474`. Credentials from `.env` (`NEO4J_URI`,
`NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`), never in `config/graph.yaml`.

**Data lives in named Docker volumes `fkg_neo4j_data` / `fkg_neo4j_logs`, not in `./neo4j_data/`**
*(corrected 2026-08-02 by the environment build; this section and §1.6 both said otherwise)*.
The reason is specific to this machine and worth recording: the working tree is on `/mnt/c`,
NTFS through WSL's 9p mount, where Neo4j's store files hit file-locking and ownership failures.
A bind mount into the repository is therefore not available, and the gitignored `./neo4j_data/`
path the plan assumed never comes into existence.

Two consequences that bite silently if forgotten: `compose.yaml` pins `name: fkg`, and removing
it re-derives the volume names from the directory — which yields a *silently empty* database
rather than an error. And a shell started before Docker Desktop's install lacks the `docker`
group, so commands need `sg docker -c '<cmd>'` or a fresh shell.

**Community's limits, now measured** *(verified 2026-08-03 against the running `fkg-neo4j`,
Neo4j Kernel 5.26.28 community, driver 6.2.0; carried as "(unverified)" since G0 because
testing it means creating constraints, which the environment build declined to do)*. Four
probes were issued on `:__CapabilityProbe` and dropped again; the server's own words:

| Probe | Result |
| --- | --- |
| `CREATE CONSTRAINT … REQUIRE n.probe_id IS UNIQUE` | **accepted.** `SHOW CONSTRAINTS` reports type `UNIQUENESS`; it also brings a backing `RANGE` index carrying the constraint's own name |
| `CREATE CONSTRAINT … REQUIRE n.probe_key IS NODE KEY` | **refused**, `Neo.DatabaseError.Schema.ConstraintCreationFailed` — *"Unable to create Constraint( type='NODE KEY', schema=(:__CapabilityProbe {probe_key}) ): Node Key constraint requires Neo4j Enterprise Edition."* |
| `CREATE CONSTRAINT … REQUIRE n.probe_id IS NOT NULL` | **refused**, `Neo.DatabaseError.Schema.ConstraintCreationFailed` — *"Unable to create Constraint( type='NODE PROPERTY EXISTENCE', schema=(:__CapabilityProbe {probe_id}) ): Property existence constraint requires Neo4j Enterprise Edition."* |
| `SHOW DATABASES` / `CREATE DATABASE probe_db` | `neo4j` (`standard`) and `system` (`system`), both online — **one user database**. Creating a second is refused: `Neo.ClientError.Statement.UnsupportedAdministrationCommand` — *"Unsupported administration command: CREATE DATABASE probe_db."* |

So the design holds as written: uniqueness constraints only, NOT NULL enforced by the
projection models and re-checked by a post-load Cypher assertion, and rebuilds wipe the one
database rather than creating a new one. Two details the probes added to the assumption. First,
`IF NOT EXISTS` does **not** soften the two Enterprise refusals — a constraint that does not
exist still fails to be created — so there is no "try the strong form, fall back" path to write.
Second, the refusals arrive as `DatabaseError`, not `ClientError`, so "this edition cannot do
that" and "the server broke" share an exception class; only the message distinguishes them.
`CREATE FULLTEXT INDEX … IF NOT EXISTS` was probed in the same pass and is accepted, which is
what §5.3's `passage_text` index depends on.

## 5.2 Constraints

```cypher
CREATE CONSTRAINT entity_key      IF NOT EXISTS FOR (n:Entity)      REQUIRE n.entity_id      IS UNIQUE;
CREATE CONSTRAINT observation_key IF NOT EXISTS FOR (n:Observation) REQUIRE n.observation_id IS UNIQUE;
CREATE CONSTRAINT event_key       IF NOT EXISTS FOR (n:Event)       REQUIRE n.event_id       IS UNIQUE;
CREATE CONSTRAINT metric_key      IF NOT EXISTS FOR (n:Metric)      REQUIRE n.metric_id      IS UNIQUE;
CREATE CONSTRAINT passage_key     IF NOT EXISTS FOR (n:Passage)     REQUIRE n.passage_id     IS UNIQUE;
CREATE CONSTRAINT document_key    IF NOT EXISTS FOR (n:Document)    REQUIRE n.document_id    IS UNIQUE;
CREATE CONSTRAINT issue_key       IF NOT EXISTS FOR (n:Issue)       REQUIRE n.issue_id       IS UNIQUE;
```

The constraint is on the **base** label, so the same `entity_id` cannot exist twice under two
concrete labels — which is the accidental-merge-adjacent failure a per-concrete-label
constraint would miss.

## 5.3 Indexes

```cypher
CREATE INDEX obs_metric   IF NOT EXISTS FOR (n:Observation) ON (n.metric_id);
CREATE INDEX obs_period   IF NOT EXISTS FOR (n:Observation) ON (n.period_key);
CREATE INDEX obs_subject  IF NOT EXISTS FOR (n:Observation) ON (n.subject_entity_id);
CREATE INDEX obs_lane     IF NOT EXISTS FOR (n:Observation) ON (n.source_lane);
CREATE INDEX evt_type     IF NOT EXISTS FOR (n:Event)       ON (n.event_type_id);
CREATE INDEX evt_occurred IF NOT EXISTS FOR (n:Event)       ON (n.occurred_on);
CREATE INDEX psg_document IF NOT EXISTS FOR (n:Passage)     ON (n.document_id);
CREATE INDEX iss_code     IF NOT EXISTS FOR (n:Issue)       ON (n.code);
CREATE FULLTEXT INDEX passage_text IF NOT EXISTS FOR (n:Passage) ON EACH [n.text];
```

The full-text index is what makes Browser search useful without Bloom.

## 5.4 Property placement

**Neo4j cannot store a nested map or a heterogeneous list.** Three payload fields need a
stated policy:

| Field | Policy |
| --- | --- |
| `EventInstance.properties` — declared `dict[str, Any]` (`models.py:427`), though the lane's own `LaneEvent.properties` is `dict[str, str]` and the values are verbatim filed strings | one property per declared key, prefixed: `prop_committed_capacity: "$525 million"`. Plus `property_names: ["committed_capacity", "maturity_date"]` so a query can find events carrying a property without knowing its name. **The projection coerces to string and never parses a number** — `"$525 million"` and `"approximately 550 employees"` are the filing's words, and a graph that silently typed them would assert a measurement the extractor refused to make. The `Any` matters: the type does not guarantee a string, so this is an enforcement the projection performs rather than one it inherits. A non-scalar value is a `MALFORMED_EVENT_PROPERTY` rejection (§6.4), not a silent `str()`. |
| `MetricObservation.population` — four model fields, **one of which reaches the catalogs** | **Only `population_definition_raw` is projected**, because only it exists in the run (89 non-null of 2,707). `population_role` and `population_confidence` appear in no catalog, and reconstructing them would emit the model defaults `baseline` and `medium` on every observation — inventing a provenance the extractor never wrote, in the section that invokes "fail loudly, never default". `definition_normalized` is likewise absent and likewise not projected. `V1_CLAIM_EXTRACTION` §7.1's whole point is that two differing `definition_raw` values are **not one series**, so that field is first-class and Q2 filters on it. *(Corrected by review: the draft promised all four, which for two of them meant fabricating a default.)* |
| `extractor_metadata` — **38 keys in 8 shapes** *(measured at G0: shape sizes 16/17/14/18/11/20/19/9, counts 2397/209/84/9/6/4/4/4)* | `observations.jsonl` already promotes `scale`, `scale_location`, `row_label`, `column_label`, `ambiguity_codes` to columns, so the graph reads them there. From `claims.extractor_metadata` the projection additionally promotes all four §8a.12 period fields (`period_label`, `period_label_char_start`, `period_label_in_evidence`, `period_label_distance_from_evidence`), the grid coordinates (`row_index`, `value_column_index`, `period_header_row_index`, `period_header_column_index`, `metric_label_row_index`), `subject_basis`, `scale_source`, `prompt_version`, `provider_model_id`, `scope`, and `source_name`/`target_name` on relationship claims. Everything else is preserved as `extractor_metadata_json`. |

**Three properties of `extractor_metadata` the readers must encode, all measured at G0:**

1. **Keys are omitted, not nulled.** 84 table claims carry `scale_source` but no `scale` key at
   all; `ambiguity_codes` appears only when non-empty (213 of 2,717). Every promoted field must
   be optional-with-default, or the reader breaks on a shape it should accept.
2. **Three names for one concept across lanes.** The table lane writes `scale_source`; the
   narrative lane writes `scale_declaration_location` and `scale_declared_in`. The projection
   carries all three under their own names and does **not** unify them — collapsing them would
   assert an equivalence nobody has established, and `observations.scale_location` is the field
   the graph actually queries.
3. **The id-bearing coordinates *are* in `extractor_metadata`, and the draft said they were not.**
   `deterministic_lane.py:365-366` mints the id from `(row_index, column.column_index)` and
   `:382-383` writes those same two values as `metric_label_row_index` and
   `period_header_column_index`. *Verified at G0 by recomputation:* feeding
   `structural_position = (f"row={metric_label_row_index}", f"column={period_header_column_index}")`
   into `observation_id` reproduces **2,707 of 2,707 ids exactly, zero mismatches**. Note
   `value_column_index` is a *different* column and reproduces only 582 — the two are not
   interchangeable. This makes §4.1's determinism check far stronger than planned.

Values: `MetricObservation.value` is `float \| int \| str \| bool` in the model and **a float on
all 2,707 rows** in this run, including counts like `market_count`. It is stored as given, with
`value_text` holding the printed form where `extractor_metadata` supplies one (17 narrative
claims). Scale is already applied by the lane — claims carry absolute USD — and the graph does
not re-scale anything.

## 5.5 Temporal, provenance and validation properties

**Temporal.** ISO strings are authoritative, because they are what the catalogs contain and
what byte-identity is checked against: `period_start`, `period_end`, `instant_date`,
`period_key`, `occurred_on`, `announced_on`, `valid_from`, `valid_to`.

**Typed `date()` twins are a G2 loader concern, not a projection concern** *(amended
2026-08-03; the draft added `occurred_on_date`, `announced_on_date` and `period_end_date` as
projection properties, and G1 built them before measuring what they held)*. The measurement:

| Twin | What it held | Null | Information added |
| --- | --- | --- | --- |
| `period_end_date` | a byte-identical copy of `period_end` on all 2,707 observations | 403 — exactly the instants | none |
| `occurred_on_date` | a byte-identical copy of `occurred_on` on all 6 events | 3 of 6 | none |
| `announced_on_date` | a byte-identical copy of `announced_on` on all 6 events | 2 of 6 | none |

*(Measured 2026-08-03 on `data/graph_runs/graph-v1-380c18fe3b9f/nodes.jsonl`, the last export
that carried them; "byte-identical" is an equality check over every row, not a sample.)*

The projection holds no database, so it cannot call `date()`; a "typed twin" written by a
process with no type system is a second string under a name that promises a temporal type.
Worse, `period_end_date` was null on exactly the 403 rows a range query most needs a date on —
an instant observation has an `instant_date` and no `period_end` — so the property was
misleading as well as empty. The loader is the layer that has `date()`, and it can apply it to
the **authoritative** ISO field it already reads; §6.3's `SET n += row.properties` becomes one
extra `SET` per temporal field. The three properties are therefore removed from the projection
and this section no longer asks for them. Nothing is lost: `date(n.period_end)` is available to
any query, and to the loader, from the field the catalog actually filed.

**The five event fields that exist only in `events.jsonl` are carried, not dropped** *(added by
review, which caught the projection spec silently discarding what §0b called load-bearing)*:
`dates_equal`, `review_flag`, `occurrence_date_text`, `announcement_date_text` and
`evidence_quoted_text` all become `:Event` properties. `review_flag` is the **only** carrier of
`ANNOUNCEMENT_EQUALS_OCCURRENCE` — one event in this run — and an event whose two dates coincide
is exactly the case a reader must be able to see rather than infer. The two `*_date_text` fields
hold the filing's own wording for each date, including the honest
`"(the passage does not state this date)"`, which is what makes an absent date legible instead
of merely blank.

**Announcement is not occurrence, in the graph too.** Both dates are stored, either may be
null, and **neither is ever filled from the other or from a document's `filing_date`**
(`V1_CLAIM_EXTRACTION` §4.0b). Consequences the graph must respect:

- there is no single `date` property on `:Event`, and no timeline query may invent one — Q3
  returns `coalesce(occurred_on, announced_on)` **beside** a `dated_by` column naming which
  field it used;
- the Browser caption for an event states which date it is showing;
- an event whose passage dates only the announcement has `occurred_on: null` and an `event_id`
  reading `undated`. That is the true answer, and a graph that hid it would be worse than one
  that showed a gap.

**Provenance**, on every node and every edge:

| Property | Value |
| --- | --- |
| `extraction_run_id` | the Stage 13 run |
| `graph_run_id` | this projection's run |
| `graph_projection_version` | bumped when the projection changes shape |
| `ontology_id`, `ontology_version`, `ontology_definition_hash` | `real_estate_marketplace_v1`, `1.0.0`, `e8d4af709be2…` |
| `extraction_code_commit`, `graph_code_commit` | git commits |
| `source_lane` | on observations: `normalized_table` / `normalized_narrative` |
| `claim_id` | on observations, events, and relationship edges |

**Validation state — re-derived, because the run does not record it** *(G0)*.

`validation_state` ∈ `{clean, warned}`, `warning_codes: [...]`, and a non-exclusive `:Warned`
label — **on `:Event` as well as `:Observation`** *(added 2026-08-03; the draft gave them to
observations only, so `WHERE x.validation_state = 'clean'`, the obvious way to ask for
trustworthy facts, silently excluded all six events)*. Every event is `clean` with
`warning_codes: []`, and that is a measurement rather than a default: the manifest's 186 come
from `validate_claims` over all 2,717 claims and not one of them is an event, because this
ontology declares no event-level warning rule — the same fact this section's last paragraph
records as the reason the two denominators agree today.

The run's 186 `unpreferred_source_lane` warnings exist **only** as a manifest aggregate:
`CheckFinding` carries a `claim_id` (`extraction/stages/verify/public.py:49-53`) and
`as_row()` writes counts, discarding the findings (`:70-80`). Nothing in the run directory
associates a warning with a claim.

**The projection therefore re-derives it, and proves the derivation.** It reconstructs each
`MetricObservation` from `observations.jsonl` + `evidence.jsonl` and calls the ontology's own
`validate_observation()` — the same validator the run used, not a reimplementation of its rule —
then asserts the resulting warning count equals
`manifest.verification[ontology_validation].warnings`.

*Verified at G0*: replaying all 2,707 observations reproduces **exactly 186 warnings, all
`unpreferred_source_lane`** — `market_count` 176, `housing_inventory_homes` 3, and seven other
metrics at one each. The manifest says 186. The counts agree, so the derivation is checked
rather than asserted, and **G1 fails the build if they ever disagree**.

**The derivation is robust, and its equality check is not.** Independent review re-ran the
reconstruction with `population`, `evidence`, `reported_at`, `dimensions`, `reporting_basis` and
`confidence` varied, and with `assertion_type='calculated'`: every variant returned 186, because
`_warn_on_unpreferred_lane` (`ontology/validation.py:287-306`) reads only `source_lane` and the
metric definition. **But the manifest's 186 comes from `validate_claims` over all 2,717 claims**,
including the 6 events and 4 relationships (`extraction/stages/verify/public.py:38`). The two
agree today only because no event- or relationship-level warning exists in this ontology. G1
therefore asserts the equality **and** records that its denominators differ, so that a future
event-level warning fails the build for a stated reason rather than an apparently mysterious one.

This is derivation, not invention: no new fact is created, the rule belongs to the ontology, and
the answer is cross-checked against the run's own recorded total. Had the counts disagreed, the
correct move would have been to carry no per-node warning state and record the aggregate —
which is what §14.2 now says explicitly.

---

# 6. Import strategy

## 6.1 Two artifacts, one direction

```text
graph project  <extraction_run_id>   ->  data/graph_runs/<graph_run_id>/{nodes,edges}.jsonl + manifest.json
graph load     <graph_run_id>        ->  Neo4j (wipe, constrain, load, verify)
graph verify   <graph_run_id>        ->  exits non-zero on any inconsistency
```

Splitting projection from loading is what makes the whole graph model testable with no database
running, and it is the reason `graph/stages/projection/` may not import a driver (§11). The
export is also the comparison artifact: rebuild determinism is checked on files, not on a
database.

## 6.2 Full replacement, not incremental

V1 loads **one extraction run into an empty database**. `load` refuses to run against a
database whose `graph_run_id` differs from the one being loaded unless `--replace` is given, and
`--replace` wipes first:

```cypher
MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS;
```

Two properties of that statement are not cosmetic. The `MATCH` must sit **outside** the
subquery — with no driving clause the subquery receives a single input row and runs once, in
one transaction, over the whole database, which is precisely the heap exhaustion the batching
clause exists to avoid. And `IN TRANSACTIONS` is only permitted in an implicit (auto-commit)
transaction, so the loader must issue the wipe through a session's auto-commit path, not inside
a managed-transaction function. *(unverified — confirm both against the installed Neo4j 5 at G2;
5.23+ also accepts the shorter `CALL (n) { … }` form.)*

Incremental update is out of scope (§12) and is a genuinely different problem — it needs a
retirement policy for facts a later run no longer emits, and inventing one before a single run
has been looked at would be guessing.

## 6.3 Loading

Constraints and indexes first — a `MERGE` without a backing constraint is a table scan and, at
scale, a duplicate-node generator. Then batched `UNWIND`, one transaction per batch of ~1,000
rows, `MERGE` on key and `SET n += row.properties`:

```cypher
UNWIND $rows AS row
MERGE (n:Observation {observation_id: row.key})
SET n += row.properties
WITH n, row CALL apoc.create.addLabels(n, row.labels) YIELD node RETURN count(node);
```

APOC is *not* assumed. Dynamic labels are the only thing it would buy, and the projection can
avoid needing it by grouping rows by their exact label set and emitting one statement per group
— a handful of statements, since the label sets are finite. **Decision: no APOC dependency**;
the loader groups by label set. Recorded because reaching for APOC is the obvious move and the
default-to-the-standard-library rule applies to Cypher procedures too.

Edges are loaded after all nodes, matching endpoints by key:

```cypher
UNWIND $rows AS row
MATCH (s:Entity {entity_id: row.source_key})
MATCH (t:Event  {event_id:  row.target_key})
MERGE (s)-[r:PARTICIPATES_IN {edge_key: row.edge_key}]->(t)
SET r += row.properties
RETURN count(r) AS written;
```

`MATCH` rather than `MERGE` on endpoints, deliberately: an edge whose endpoint is missing must
not quietly conjure an empty node.

**But `MATCH` alone does not fail — it filters.** A row whose endpoints do not exist produces
zero rows and no error, so a loader that only issued the statement above would *silently drop*
exactly the edges this design says it refuses. The loader therefore compares `written` against
`size($rows)` for every batch and aborts on any shortfall, naming the offending `edge_key`s.
That comparison is the check; the `MATCH` is only what makes the shortfall detectable. Recorded
at this length because the first draft of this plan asserted the guarantee and did not have it.

## 6.4 Malformed and rejected records

The projection validates every input row against a typed reader. A row it cannot project is
written to `rejected.jsonl` with `{row_index, file, code, detail}` and **the projection exits
non-zero**. Codes: `UNKNOWN_FIELD`, `UNKNOWN_CLAIM_KIND`, `UNKNOWN_PREDICATE`,
`UNDECLARED_ENDPOINT_TYPE`, `EVIDENCE_PASSAGE_NOT_IN_CATALOG`, `MISSING_REQUIRED_KEY`,
`MALFORMED_EVENT_PROPERTY` (§5.4 — a property value Neo4j cannot store as a scalar),
`PROVENANCE_KEY_COLLISION` (§5.5 — a run-provenance key that shadows a filed fact key).

Failing rather than skipping is the whole point: a graph that silently drops 3% of its claims
looks exactly like a graph that did not. The file exists so the reason is auditable when it
happens.

**A refusal is written beside the run id, never on top of it** *(added 2026-08-03 after
review)*: `rejected.jsonl` lands in `data/graph_runs/<graph_run_id>.rejected/`, and
`data/graph_runs/<graph_run_id>/` is not touched. G1 originally finalized a rejection over the
run id itself, which removed a complete projection's `nodes.jsonl`, `edges.jsonl` and
`manifest.json` and left only the rejection — a failed run destroying a good one. A rejection
is a record of why there is no projection; it may not occupy the name a projection would have.

## 6.5 Reproducibility checks

| Check | How |
| --- | --- |
| Projection determinism | project twice, compare `sha256` of `nodes.jsonl` and `edges.jsonl` |
| Load determinism | load twice into a wiped database, compare per-label node counts and per-type edge counts |
| Idempotence | load the same export twice **without** wiping; counts must not change |
| Manifest | `data/graph_runs/<id>/manifest.json` records both source run ids, both code commits, the ontology hash, projection version, and every count — written last, after `nodes.jsonl` and `edges.jsonl`, following the repository's completion-marker convention |

---

# 7. Inspection

## 7.1 Neo4j Browser first

Browser ships with every edition, speaks Cypher, renders results as a graph, shows node and
relationship properties on click, and saves queries as favourites. It satisfies every
inspection requirement in the brief. Bloom and Explore are **not** part of the first milestone
— *(unverified: whether the local install includes a Bloom licence)* — and G4 revisits them
only if a specific need survives the query pack.

## 7.2 Styling

A committed `graph/browser/style.grass` (Browser's stylesheet format), plus the captions:

| Label | Caption | Colour |
| --- | --- | --- |
| `:PublicCompany` / `:Company` | `entity_id` | dark blue |
| `:Subsidiary` | `entity_id` | mid blue |
| `:Unresolved` | `entity_text` | **grey, dashed border** — a placeholder must not look like an answer |
| `:Person` | `entity_id` | green |
| `:Facility` / `:Agreement` | `entity_id` | amber |
| `:Metric` | `metric_id` | purple |
| `:Observation` | `value` + `unit` | light purple; `:Warned` gets a red border |
| `:Event` | `event_type_id` | orange |
| `:Passage` | `passage_id` tail (`#p117`) | grey |
| `:Document` | `form` + `filing_date` | dark grey |
| `:Issue` | `code` | red |

## 7.3 Hiding the technical layer

`:Passage`, `:Document` and `:Issue` are technical for most looks. Rather than hiding them by
configuration, **every query in the pack is scoped** so they appear only when asked for. The
default landing query (Q1) returns no passages; Q6 exists precisely to pull the evidence for
one selected thing.

## 7.4 Avoiding the hairball

Now a measured problem rather than a predicted one *(G0)*. **Every one of the 2,707
observations hangs off a single `:Entity` — there is exactly one `subject_entity_id` in the
whole run** — so `opendoor` has degree ≥2,707 before any event or relationship edge. And
`:Issue` outnumbers every other node type six to one: 17,127 issues against 2,707 observations,
of which 10,852 are `NO_STORED_ANSWER`.

Three rules:

1. **never** `MATCH (n) RETURN n` — the pack has no unbounded query, and Browser's node limit
   is set in the saved settings;
2. every observation query is filtered by metric, period or document, and `LIMIT`ed;
3. `OBSERVATION_OF_SUBJECT` is the edge to *not* traverse when exploring. The interesting fan-out
   is `Metric → Observation → Passage → Document`, which is narrow at every step **on the
   evidence side**: 17 metrics, **153** claim-cited passages, **49** claim-cited documents.
   *(Corrected by review)* the graph as a whole holds **8,776 passages and 185 documents**,
   because issues cite 8,757 passages the claims never touch. Almost all of that mass hangs off
   `:NotAttempted`. Q6 and Q7 traverse from a claim and so stay in the 153/49 neighbourhood; only
   an issue-first query reaches the rest, which is rule 4's job.
4. **`:NotAttempted` is excluded from every default view.** The 10,852 `NO_STORED_ANSWER` issues
   record that the run never asked the model, because it replayed 15 stored answers with
   `provider_calls_permitted: 0`. That is a fact about this run's bounds, not about the corpus,
   and mixing it into an issues view would drown the 6,228 real refusals. They are loaded — the
   count must reconcile — and labelled so they can be left out.

---

# 8. The query pack

Committed as `graph/queries/*.cypher`, one file per query with a comment header, and loaded as
Browser favourites. Each is also an executable test at G4 asserting the returned *shape*, not
the row count (which depends on the run).

| # | View | Sketch |
| --- | --- | --- |
| Q1 | Company overview | `MATCH (c:Entity {entity_id:'opendoor'})<-[:OBSERVATION_OF_SUBJECT]-(o:Observation)<-[:HAS_OBSERVATION]-(m:Metric) RETURN m.metric_id, count(o), min(o.period_key), max(o.period_key) ORDER BY m.metric_id` |
| Q2 | Metric timeline | `MATCH (m:Metric {metric_id:$metric})-[:HAS_OBSERVATION]->(o:Observation) RETURN o.period_key, o.value, o.unit, o.source_lane, o.population_definition_raw, o.validation_state ORDER BY o.period_key` — **grouped by `population_definition_raw`**, because `V1_CLAIM_EXTRACTION` §7.1 forbids presenting two denominators as one series |
| Q3 | Events over time | `MATCH (e:Event) RETURN e.event_type_id, e.occurred_on, e.announced_on, CASE WHEN e.occurred_on IS NULL THEN 'announced' ELSE 'occurred' END AS dated_by ORDER BY coalesce(e.occurred_on, e.announced_on)` |
| Q4 | Event participants | `MATCH (x:Entity)-[p:PARTICIPATES_IN]->(e:Event {event_id:$id}) RETURN e, p.role, x, x.resolved` |
| Q5 | Credit facilities and borrowers | `MATCH (b:Entity)-[r:BORROWS_UNDER]->(f:Entity) OPTIONAL MATCH (l:Entity)-[:LENDS_UNDER]->(f) RETURN f.entity_id, b.entity_id, b.resolved, collect(l.entity_id), r.passage_id` |
| Q6 | Observation → evidence | `MATCH (o:Observation {observation_id:$id})-[:EVIDENCED_BY]->(p:Passage)-[:PART_OF]->(d:Document) RETURN o.value, o.unit, o.value_text, p.passage_id, p.text, d.form, d.filing_date, d.source_url` |
| Q7 | All claims from one filing | `MATCH (d:Document {document_id:$id})<-[:PART_OF]-(p:Passage)<-[:EVIDENCED_BY]-(x) RETURN labels(x), coalesce(x.metric_id, x.event_type_id), x.claim_id, p.passage_id` |
| Q8 | Unresolved / suspicious entities | `MATCH (u:Unresolved) OPTIONAL MATCH (u)-[:PLACEHOLDER_FOR]->(parent) RETURN u.extraction_entity_id, u.entity_text, parent.entity_id, COUNT { (u)--() } AS degree ORDER BY degree DESC` — `COUNT {}`, not `size((u)--())`, which was removed in Neo4j 5.0 — plus a second query listing named entities whose `entity_id` was reached from more than one distinct `entity_text` (§1.4b) |
| Q9 | Issues and validation warnings | `MATCH (i:Issue) RETURN i.code, count(*) ORDER BY count(*) DESC` and `MATCH (o:Observation:Warned) RETURN o.observation_id, o.warning_codes, o.metric_id, o.source_lane` |
| Q10 | Same fact, two filings | `MATCH (m:Metric)-[:HAS_OBSERVATION]->(o:Observation) WITH m.metric_id AS metric, o.period_key AS period, collect(o) AS os WHERE size(os) > 1 RETURN metric, period, [x IN os \| [x.value, x.source_lane, x.observation_id]]` — the restatement question §1.4c defers, made askable |

Q10 is not in the brief's list and is the one this plan adds: `SUPERSEDES` is deferred to the
graph layer, and the graph's first useful contribution is to show the founder exactly how often
the corpus reports one metric-period twice, and whether the values agree.

---

# 9. Future LLM post generation — interface only

Not implemented in V1. Planned to the level of a contract so the graph model does not have to
change to accommodate it later.

```text
TopicSelector   → GraphRetriever → EvidencePackage → PostGenerator → CitationVerifier
```

| Step | Contract | Constraint that already exists |
| --- | --- | --- |
| Select a topic or trigger | a metric + period, an event id, or an entity id | none |
| Retrieve a bounded subgraph | `SubgraphRequest(seed, max_hops≤2, max_nodes, edge_types, since, until)` → `Subgraph(nodes, edges)`; **bounded by construction**, never an unbounded traversal | §7.4 |
| Retrieve supporting passages | every fact in the subgraph resolved to its `passage_id`s, then to `passages.jsonl` text | §1.5 — the anchor is the passage, and it resolves or the package is refused |
| Produce an evidence package | `EvidencePackage(facts[], passages[], provenance{run ids, ontology hash, commits}, caveats[])`, serializable and diffable | §5.5 |
| Generate a post | consumes only the package; the model never queries the graph directly | the same provider boundary the narrative lane already enforces |
| Verify statements and citations | every number in the draft must appear in a package fact, and every citation must name a `passage_id` in the package; anything else is refused, not warned | `validate_quoted_text` is the same idea one layer down |

**Three classes of statement, kept apart in the package and in the post.** The graph already
carries the discriminator, which is why this is a contract and not a research project:

| Class | Source | Marker |
| --- | --- | --- |
| Reported fact | an observation or event with `assertion_type: reported` and a passage | cite the passage |
| Calculated comparison | computed by the generator from two or more reported facts (a QoQ delta) | show the inputs' observation ids and the arithmetic; never cite a passage for the result |
| Inference | anything else | labelled as inference, never cited, and **never** presented as a filed figure |

**Caveats the package must carry, because the graph knows them and a reader will not:**
`ambiguity_codes` on any observation used; differing `population_definition_raw` if two
observations of the same metric are compared; `validation_state: warned`; `resolved: false` on
any entity mentioned; and an event dated only by `announced_on`.

---

# 10. Validation and acceptance

A build is accepted when all of the following hold, each with an executable check.

1. **Rebuild succeeds** from the finalized Stage 13 run, with a non-zero count of observations,
   events, relationships, passages and documents.
2. **No dangling references.** Zero edges whose endpoint is missing — enforced by §6.3's
   per-batch `written` vs `size($rows)` comparison, **not** by `MATCH` semantics — and zero
   `:Observation`/`:Event` without an `EVIDENCED_BY` edge:
   `MATCH (x) WHERE (x:Observation OR x:Event) AND NOT (x)-[:EVIDENCED_BY]->() RETURN count(x)` → 0.
3. **Stable across two rebuilds.** Byte-identical `nodes.jsonl` and `edges.jsonl`; identical
   per-label and per-type counts after two loads; no id differs.
4. **Every fact links back to claim and evidence provenance.** *(Scope corrected 2026-08-03:
   the criterion demanded a `claim_id` and a `passage_id` on every ontology-declared edge, and
   38 ontology-declared edges correctly carry neither. The design was right and this text was
   never swept.)*
   - **Claim-sourced facts** — every `:Observation`, every `:Event`, and every ontology-declared
     edge built from a claim row (`HAS_OBSERVATION`, `OBSERVATION_OF_SUBJECT`, `EVIDENCED_BY`,
     `PARTICIPATES_IN`, and the entity-to-entity predicates of `relationships.jsonl`) — carry a
     non-null `claim_id`, `extraction_run_id`, `ontology_definition_hash` and at least one
     resolvable `passage_id`.
   - **Vocabulary-sourced edges** — the 36 `DISTINCT_FROM` and 2 `RECONCILES_TO` edges, read
     from `metrics.yaml` rather than from a filing — carry `assertion: ontology_definition`
     and **no** `claim_id`, `passage_id`, `document_id` or `assertion_type`. No claim asserted
     them and no passage evidences them; a `claim_id` on one would be a fabricated citation,
     which is a worse failure than the missing field the original criterion asked for.
   - Both branches are executable and both are asserted
     (`tests/graph/test_edges.py::test_fact_bearing_edges_carry_claim_passage_document_and_assertion`);
     the test previously `continue`d past the second family, which is how the contradiction
     survived a green suite.
   - Either way, every `passage_id` in the graph exists in
     `data/normalization_catalog/passages.jsonl`.
5. **Nothing unsupported loads as an accepted fact — and nothing supported is suppressed.**
   Abstentions, refusals, rejections and deferrals appear only as `:Issue`, with no edge into
   the fact graph; `count(:Issue)` equals the 17,127 rows of `issues.jsonl`; no `:Observation`
   exists whose `claim_id` is absent from `claims.jsonl`.
   **The converse is equally a criterion**, because G0 measured that a refusal is per-reading or
   per-property and never per-node: the event carrying `PARTICIPANT_NOT_NAMED` and the **four**
   events carrying `PROPERTY_VALUE_NOT_IN_PASSAGE` (across two passages — three on
   `…ef20055426_ex99-1.htm#p2`, one on `…open-20220930.htm#p117`; *review corrected the draft's
   "three"*) **must be present** as `:Event` nodes. A
   projection that dropped a fact because an issue names its passage would fail this criterion,
   not satisfy it.
6. **Announcement stays distinct from occurrence.** No `:Event` has `occurred_on` equal to a
   value that appears only in `announced_on` in the catalog, and no query in the pack collapses
   the two into an unlabelled date. Executable: for every event, the projected pair equals the
   catalog pair exactly, including nulls.
7. **Unresolved entities are not guessed.** Every participant with `named: false` produces a
   `:Unresolved` node with `resolved: false`, a per-event key, and no `SUBSIDIARY_OF` or other
   identity-asserting edge. Executable: `MATCH (u:Unresolved)-[r]->() WHERE type(r) <> 'PLACEHOLDER_FOR' RETURN count(r)` → 0.
8. **Merge hazards are reported, not hidden.** G5 publishes counts for: entity ids reached from
   more than one distinct `entity_text`; entity ids appearing under more than one `entity_type`;
   `:Unresolved` nodes by parent; and metric-period pairs with more than one observation (Q10).
   These are findings, not failures — the criterion is that the numbers exist and are in the
   report.
9. **The query pack runs.** All ten queries execute against the loaded graph and return the
   declared shape.
10. **No refused reading is resurrected.** For each of the four `AMBIGUOUS_COLUMN_ALIGNMENT`
    conflict-guard identities recorded in `issues.jsonl` (§2.4), zero `:Observation` nodes carry
    that `(metric_id, subject_entity_id, period_key, source_lane, passage_id)`. Checked after
    loading, not assumed from the input — the input is clean, and this catches a projection bug
    that manufactured a node the extractor refused.
11. **Warned nodes match the run's own count.** The re-derived warning set (§5.5) has exactly
    `manifest.verification[ontology_validation].warnings` members — 186 for this run — and every
    one is attached to an `:Observation` that exists.
12. **No custom frontend.** The graph is inspectable through Browser using only the committed
    stylesheet and query pack.

---

# 11. Implementation stages

Package layout, following the three existing packages and no further:

```text
graph/
  contracts.py              GraphProjection, GraphStore protocols; no driver, no HTTP, no config
  core/
    models.py               GraphNode, GraphEdge, GraphExport
    inputs.py               typed readers over the Stage 13 catalogs; extra="forbid"
    keys.py                 node and edge keys, incl. the unresolved-entity rule (§4.2)
  stages/
    projection/             catalogs + ontology -> nodes.jsonl / edges.jsonl   (no driver import)
    load/                   the only module that imports neo4j                  (constraints, batches)
    verify/                 post-load Cypher assertions (§10)
  queries/*.cypher
  browser/style.grass
  context.py  pipeline.py  cli.py  __main__.py
config/graph.yaml
```

Judgment on proportion: three stage packages, not six. `projection` is one cohesive concern
(~400–600 lines across a node builder and an edge builder), `load` is small but must be alone
because it is the only place a driver may be imported, and `verify` is a list of assertions that
belongs beside neither. `core/keys.py` is separate from `core/models.py` because §4.2's policy is
the piece most likely to be argued about and it deserves its own tests.

Dependency: the official `neo4j` Python driver. It removes real work — Bolt framing, connection
pooling, transaction retry on transient failures — that would otherwise be hand-rolled. Nothing
else is added; no APOC (§6.3), no OGM, no graph library.

**Both environment prerequisites are now satisfied** *(2026-08-02; this paragraph previously said
neither was, which was true when G1 began and is stale)*. Driver `neo4j>=6.2,<7` is installed and
declared; Docker Desktop 4.84.0 with WSL integration provides `docker`. A verified authenticated
Bolt connection reports **5.26.28 community**, the database survives a restart, and it is empty —
0 nodes, 0 relationships, so G3 loads into a known-clean store rather than onto residue.

G0 and G1 were database-free by design and are where the graph model is actually tested, so
neither waited on this.

| Stage | Work | Gate |
| --- | --- | --- |
| **G0** | **DONE.** Inspected the finalized run, answered P1–P11 (§2.2), recorded seven contradicted assumptions (§0b), committed a real fixture under `tests/fixtures/graph/`. | Met: every P-item answered; fixture committed; §0b records each correction and its reason |
| **G1** | `graph/core` + `graph/stages/projection`: typed readers, node and edge builders, deterministic export, `rejected.jsonl`. No database. | `nodes.jsonl`/`edges.jsonl` byte-identical across two runs on the fixture; every §3.2 edge derived from the registry, not a literal list; unresolved-entity policy tested; offline suite green |
| **G2** | Neo4j 5 Community in Docker, `config/graph.yaml`, constraints and indexes, batched loader, wipe-and-replace. | Constraints created; a fixture export loads; loading twice without wiping changes no count; loading with a missing endpoint fails |
| **G3** | Load the real Opendoor run; validate counts and provenance. | Acceptance criteria 1–7 pass on the real run; counts recorded (they are §2.2 P11's answer) |
| **G4** | Browser configuration: `style.grass`, the ten-query pack, saved favourites. | All ten execute and return the declared shape; a founder can reach a filed sentence from an observation in two clicks |
| **G5** | Graph-quality review, written up as a document beside this plan. | Criterion 8's four counts published, with named examples; every surprise recorded whether or not it is fixed |
| **G6** | Bounded retrieval contract for LLM post generation — `contracts.py` protocols, `EvidencePackage` model, and a test that a package's every fact resolves to a passage. **No generation, no prompts, no model.** | Contract committed; the retriever protocol has a conformance test; a package built from the fixture verifies |

Each stage ends with a green offline suite and its own narrow commit, matching the repository's
process rule.

**Structural tests to add at G1/G2**, mirroring the ones the other packages already carry:

- no module under `graph/stages/projection/` or `graph/core/` imports `neo4j` — transitively;
- `graph/contracts.py` imports no driver, no HTTP client, no config, no storage;
- nothing in `acquisition/`, `normalization/`, `ontology/` or `extraction/` imports `graph`;
- `graph/` imports `extraction`'s and `ontology`'s public contracts only, never a stage
  internal;
- no stage imports another stage; no cycles.

---

# 12. Out of scope

Stated so it is not re-opened mid-build:

| Out of scope | Why |
| --- | --- |
| **Entity resolution** | §1.4b. Deciding two spellings are one entity needs a corpus-wide pass with its own evidence standard. V1 measures the hazard (criterion 8) and leaves the placeholder shape a resolver can find. |
| **`SUPERSEDES`** | §1.4c. Restatement policy is a research question; Q10 makes it askable. |
| **Calculated observations, `COMPUTED_FROM`, `USES_FORMULA_VERSION`** | Extraction emits no calculated observation in V1. |
| **Incremental / multi-run graphs** | §6.2. Needs a retirement policy that cannot be designed before a single run has been looked at. |
| **A custom graph viewer or frontend** | §1.6 and §7.1. No requirement was found that Browser cannot meet. |
| **Bloom / Explore perspectives** | Not part of the first milestone; G4 revisits only against a stated need. |
| **Graphiti as a second builder** | `docs/03` plans it as a *comparison*. Building it before the first graph exists is the premature commitment doc 03 warns against. |
| **Vector search / embeddings in the graph** | Retrieval in V1 is graph traversal from a named seed. Embeddings stay where `V1_CLAIM_EXTRACTION` §4.2a scoped them. |
| **RAG, question answering, post generation** | G6 delivers the contract only. |
| **XBRL facts and the six deferred metrics** | No lane exists; they appear as `:Issue` deferrals, not gaps. |
| **Any change to extraction, normalization, ontology or acquisition** | The graph is a consumer. If a graph need implies an extraction change, it becomes a founder decision, not a quiet edit. |
| **Multiple companies** | The corpus is Opendoor. |

---

# 13. Rejected options

| Option | Why rejected |
| --- | --- |
| Invent the brief's edge names (`SUPPORTED_BY`, `DISCLOSED_IN`, `OBSERVES_METRIC`, `ANNOUNCED_EVENT`) | The ontology declares `EVIDENCED_BY`, `REPORTED_IN`, `HAS_OBSERVATION` and `PARTICIPATES_IN` with fixed directions and endpoint types. A second vocabulary for the same facts is how a graph and its ontology drift apart. §1.1. |
| A `:Claim` node | `claim_id = f(payload_id)`; the node would be 1:1 with its payload, doubling the graph and adding a hop to every evidence path. Carried as a property instead. |
| Reify relationship claims as nodes | Every entity-to-entity path becomes two hops, node count roughly doubles, and Browser exploration gets materially worse — to gain an evidence edge that an edge *property* plus a validation query already provides. |
| Key unresolved entities on the extraction placeholder id | Merges every unnamed subsidiary in the corpus into one node carrying everyone's debt. §4.2. |
| Resolve unnamed subsidiaries against the EX-21.1 subsidiary list | An inference the filing does not support — the benchmark case says so explicitly and expects the abstention. |
| Emit `SUPERSEDES` during projection | Same reason extraction refused it, one layer up: a policy nobody has decided yet. §1.4c. |
| Load abstentions and rejections as facts with a `rejected: true` flag | A property is not a barrier. One forgotten `WHERE` clause and a refused claim is a reported number. Separate label, no edges into the fact graph. |
| Parse event property strings into numbers (`"$525 million"` → `525000000`) | The extractor deliberately kept the filing's words; a graph that typed them would assert a measurement nobody made. §5.4. |
| Start with Bloom or a custom viewer | No requirement found that Browser cannot meet, and both cost more than the query pack. §7.1. |
| APOC for dynamic labels | Grouping export rows by label set removes the need. §6.3. |
| An embedded store (NetworkX, file-backed) for the first pass | `docs/02` is right that it is good for tests, and it is: the projection stage *is* the file-backed graph, and it is where the model is tested. But inspection is the point of this milestone, and that is what Neo4j is for. |
| Incremental load with per-run node versions | §6.2. |

---

# 14. Open founder decisions

Five, and only where the repository genuinely cannot answer.

**14.1 — Unresolved entities: split per event, or keep extraction's single placeholder?**
*Recommendation: split per event* (§4.2). One node per event, `resolved: false`,
`extraction_entity_id` retained, `PLACEHOLDER_FOR` to the parent. The cost is that the graph
mints a key extraction did not, and the projection must document it. The alternative merges
every unnamed subsidiary in the corpus into one node, which is the mis-attribution the
placeholder exists to prevent. **Decide before G1** — it is `core/keys.py`'s central rule.

**14.2 — Do warned claims load? — RESOLVED at G0, default applied.**
Yes, with `validation_state: warned`, `warning_codes` and a `:Warned` label. P6 turned out the
worse way — the run records no per-claim warning — but the state is **re-derivable from the
ontology's own validator and cross-checks exactly against the manifest's 186** (§5.5). The
recommended default is therefore applied without inventing anything. Had the counts disagreed,
the fallback was to carry the aggregate only, and G1 still fails the build if they ever do.

**14.3 — A `:FiscalPeriod` node, or period as properties?**
*Recommendation: properties only for V1.* No ontology predicate connects an observation to a
period, `period_key` grouping answers Q2 and Q3, and a node type invented for a query Cypher
already answers is scaffolding that later has to be maintained. Revisit at G5 if the timeline
views actually want it. Reversible: adding the node later is a projection change, not a model
change.

**14.4 — How much of the ontology loads when no claim uses it?**
*Recommendation: all 26 metrics plus `RECONCILES_TO` and `DISTINCT_FROM`, and nothing else.*
Metrics with no observations show as coverage gaps, which is useful, and `DISTINCT_FROM` puts
the "never merge these" finding into the graph. Loading the remaining 75 of the ontology's 131
concept definitions *(verified 2026-08-02: 131 = 26 metrics + 30 relationships + 19 event types
+ 13 entity types + 10 agreement types + 8 roles + 8 formula versions + 5 instruments +
5 evidence types + 4 status types + 3 claim types)* as unattached nodes would add clutter with
no query behind it. Cheap either way; stated because it changes
what the first screen looks like.

**14.5 — Emit `REPORTED_IN → sec_edgar` on every filed fact?**
*Recommendation: no, for V1.* The edge is legitimately available — `sec_edgar` is a declared
`disclosure_channel` with `canonicality: canonical` — and this plan's first draft wrongly said
the only channel was the company dashboard. Corrected, the argument changes but the answer does
not: in V1 every fact came through EDGAR, so the edge would be constant across the entire graph
— one hub attached to everything, discriminating nothing, and adding a hop to exploration.
Canonicality is better carried as a property if it is wanted at all. The edge becomes worth
emitting the moment a second channel produces claims, and `V1_CLAIM_EXTRACTION` §7.2 explicitly
makes `opendoor_accountable` discovery-only and uncitable, so that is not V1. Cheap to reverse:
one projection rule.

**Not a founder decision, recorded so it is not mistaken for one:** the graph is rebuilt from
scratch per run and holds no state of its own. Nothing in it needs migrating, backing up, or
preserving, which is what makes every decision above reversible at the cost of one rebuild.
