# v1 — Graph Prototype

**Status:** planned, nothing implemented. Written 2026-08-02 against commit `a986321`, while
extraction step 13 is still in progress in another working tree.

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

**The single most important input fact:** the extraction run this plan consumes **does not
exist yet** *(verified 2026-08-02: `data/extraction_runs/` is absent, and `extraction/` has no
`catalog` stage, no `context.py`, no `pipeline.py` and no `cli.py`)*. Everything this plan says
about claim *payloads* is read off committed, typed, `extra="forbid"` pydantic models and is
firm. Everything it says about catalog *files* — names, row shapes, which payloads live in
which file — is a proposal that §2 marks pending and stage G0 confirms.
[STAGE13_GRAPH_INPUT_HANDOFF.md](STAGE13_GRAPH_INPUT_HANDOFF.md) is the checklist for that
confirmation.

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
| `HOLDS_POSITION_AT` | declared, `person → company` | as proposed |
| `BORROWS_UNDER` | declared, `company\|subsidiary → facility` | as proposed |
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

## 1.2 There is no extraction run, no catalog stage, and no orchestrator *(verified)*

| Expected by `V1_CLAIM_EXTRACTION` §4 | Present at `a986321`? |
| --- | --- |
| `extraction/stages/{select,tables,narrative,scoping}` | yes |
| `extraction/core/{models,identifiers,assembly,validation,periods,numbers,…}` | yes |
| `extraction/stages/assemble/`, `stages/verify/`, `stages/catalog/` | **no** — assembly and validation live in `core/`, and no catalog writer exists |
| `extraction/context.py`, `pipeline.py`, `cli.py`, `__main__.py` | **no** |
| `data/extraction_runs/<run_id>/` | **no** — the directory does not exist |
| `extraction_manifests/` | **no** — but `config/extraction.yaml` already declares `paths.manifests_root: "extraction_manifests"` |

Step 13 folds `catalog` and the run manifest into itself (`V1_CLAIM_EXTRACTION` §9). So the
graph layer's entire input surface is produced by the step that is running now. That is the
reason G0 exists and the reason §2 is split into confirmed and pending.

What *is* firm today is the payload shape, because it is typed and frozen:
`OntologyClaim`, `MetricObservation`, `EventInstance`, `RelationshipInstance`,
`EvidenceReference` and `Population` are all pydantic models with `extra="forbid"`
(`ontology/core/models.py:333-476`). A catalog can rename its files; it cannot add a field to
an observation without changing that module.

## 1.3 Claims are already validated, and validation state is not binary *(verified)*

`extraction/core/validation.py` fails a run on four codes — `EVIDENCE_UNRESOLVED`,
`EVIDENCE_MISSING`, `FORBIDDEN_SOURCE_LANE`, `DUPLICATE_OBSERVATION_CONFLICT`, plus any
`ontology.validate_claims` error — and **relays two kinds of warning** rather than dropping
them: `QUOTED_TEXT_NOT_IN_PASSAGE` and `ONTOLOGY_WARNING` (which carries
`unpreferred_source_lane`, the vocabulary saying a metric it expects from a table arrived from
prose).

So a claim that reaches a catalog is error-free by construction, and may still carry a
warning. The graph must be able to say which (§5.5) — "0 errors" and "clean" are different
statements, and `validation.py:152-181` exists because they were once conflated.

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
counter (`extraction/core/identifiers.py:93-101`). The committed benchmark shows the result:
the 2022 credit facility's borrower is `opendoor_unnamed_subsidiary`
(`benchmarks/extraction/v1/reports/event_relationship_v1.json`, case
`event-credit-facility-established-2022`). Every other filing that says "a subsidiary of the
Company" mints that same string. Keyed naively, **one Neo4j node would accumulate the debt
obligations of every unnamed Opendoor subsidiary in the corpus** — which is precisely the
mis-attribution the placeholder was invented to prevent. §4.2 is the fix and §14.1 is the
decision behind it.

**(b) Named entities are name-slugs, and the same name is the same node.**
`_resolve_entity` (`extraction/stages/narrative/event_mapping.py:458-483`) returns a declared
ontology instance id when the whole printed name folds to one, and otherwise `entity_id(name)`
— the slug of whatever the passage printed. Entity resolution is explicitly not attempted. Two
directions of error follow, and the same benchmark case shows both: the lane emitted the
facility as `asset_backed_senior_revolving_credit_facility` (from the generic phrase) while the
reviewed gold calls it `asset_backed_senior_revolving_2022_10`. Generic descriptions **over**-
merge distinct facilities; varied spellings **under**-merge one facility. V1 accepts this and
measures it (§10, criterion 8) rather than resolving it.

**(c) Two filings reporting one fact are two observations, on purpose.**
`observation_id` ends in a digest of the passage id (`identifiers.py:53-67`), so a letter
rounding to `$38 million` and a reconciliation table stating `38,228` thousand stay distinct.
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
`neo4j_data/` *(verified)*.

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

# 2. Inputs from Stage 13 — confirmed and pending

## 2.1 Confirmed, because it is committed and typed

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
| Manifest root for extraction | `config/extraction.yaml` → `paths.manifests_root: "extraction_manifests"` | file naming still pending |
| Run-id format used by the other two layers | `{UTC}Z-{config_hash[:8]}`, `normalization/core/runmeta.py:24-26` | extraction is expected to match |
| Atomic finalization convention | temp dir → completion marker last → rename (`normalization/core/storage.py:62-87`) | the graph run reuses it |

## 2.2 Pending — G0 must confirm each against the finalized run

| # | Question | This plan's working assumption | Blast radius if wrong |
| --- | --- | --- | --- |
| P1 | Catalog file names and directory | `data/extraction_runs/<run_id>/{claims,observations,evidence,issues}.jsonl` per §4.6 | reader only |
| P2 | Are events and relationships in `claims.jsonl`, or in files of their own? | one `claims.jsonl` holding all three `claim_kind`s | reader only |
| P3 | Row shape: a serialized `OntologyClaim`, or a flattened projection? | `claim.model_dump()`, keys sorted | reader only |
| P4 | Does `observations.jsonl` duplicate the payload inside `claims.jsonl` or index it? | derived index; `claims.jsonl` is authoritative | projection reads claims, not observations |
| P5 | What is a row of `issues.jsonl`? Abstentions, policy rejections, deferrals, validation warnings — one file or several? | one file, each row carrying a `code`, a `passage_id` where it has one, and a discriminator | `:Issue` node properties, query Q9 |
| P6 | Is per-claim validation state recorded, or only run-level? | warnings recorded per `claim_id` | §5.5; if absent, `has_warning` cannot be set and Q9 loses a slice |
| P7 | Which `extractor_metadata` keys survive into the catalog? | at least `ambiguity_codes`, `scale`, `scale_location`, `row_label`, `column_label`; narrative adds `period_label*` | §5.4 property whitelist |
| P8 | Manifest filename, and does it carry ontology `definition_hash`, code commit, config hash, normalization run id, scoping strategy and counts? | yes, all six | graph manifest provenance |
| P9 | Final `scoping.strategy` — step 13 decides lexical vs hybrid | recorded, not assumed | provenance only |
| P10 | Are `deferred` metrics and `rejected` claims written at all? | yes, into `issues.jsonl` | Q9, acceptance criterion 5 |
| P11 | Real counts: observations, events, relationships, cited passages | unknown; the table lane emitted 410 observations over 11 benchmark cases, and selection produced 503 table + 3,072 narrative candidates | §7.4 hairball thresholds |

**Rule for handling a pending field: fail loudly, never default.** The reader (§11, G1) is a
pydantic model with `extra="forbid"` over each catalog row. An unexpected field stops the
projection with the field name, rather than being silently dropped into a graph that then
looks complete. A field this plan expects and does not find is reported the same way. Nothing
in the projection substitutes a default for a missing input.

---

# 3. The graph model

## 3.1 Node types

Every node carries a base label plus a concrete label. The base label is what uniqueness
constraints and generic queries attach to; the concrete label is what Browser styling and
readable Cypher use.

| Base label | Concrete labels | Key property | Source |
| --- | --- | --- | --- |
| `:Entity` | `:Company`, `:PublicCompany`, `:Subsidiary`, `:Person`, `:Facility`, `:Agreement`, `:Instrument`, `:GeographicMarket`, `:Product`, `:Feature`, `:Regulator`, `:StockExchange`, `:DisclosureChannel` | `entity_id` | ontology `instances` (4) + claim participants and relationship endpoints |
| `:Observation` | — | `observation_id` | `claim_kind: metric_observation` |
| `:Event` | — | `event_id` | `claim_kind: event` |
| `:Metric` | — | `metric_id` | ontology `metric_definition` (26) |
| `:Passage` | — | `passage_id` | `passages.jsonl`, **only passages some claim or issue cites** |
| `:Document` | — | `document_id` | `documents.jsonl`, only documents of cited passages |
| `:Issue` | — | `issue_id` (projection-minted, §4.3) | `issues.jsonl` |

Concrete entity labels are derived from the payload's own `entity_type` / `subject_type`
string, PascalCased, **with the ontology's `is_a` ancestors added** — so `opendoor` is
`:Entity:PublicCompany:Company` and a subsidiary is `:Entity:Subsidiary:Company`. That makes
`MATCH (c:Company)` find every company without the query needing to know the subtype list,
which is what `registry.ancestors()` already exists to answer.

A second, non-exclusive label `:Unresolved` marks any entity node the filing did not name
(§4.2). Styling it distinctly in Browser is the cheapest possible guard against reading a
placeholder as an answer.

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
| `:Observation` | `observation_id` | `obs:{metric}:{subject}:{period_key}:{lane}:{passage_digest12}` |
| `:Event` | `event_id` | `evt:{type}:{occurred_on\|undated}:{digest12(passage, sorted participants)}` |
| `:Metric` | `metric_id` | ontology concept id |
| `:Passage` | `passage_id` | `norm:{cik}:{accession}:{file}#p{n}` |
| `:Document` | `document_id` | `norm:{cik}:{accession}:{file}` — the same string **without** the `#p{n}` suffix, which is why `PART_OF` can be derived without a lookup |
| `:Entity` (named) | `entity_id` | ontology instance id, else the name slug |

Readable ids are the point, not an accident — they surface in evidence panels and in the
Browser's node captions, and a graph keyed on UUIDs would be unusable in exactly the tool this
plan is optimising for.

Relationship edges are keyed for idempotence on
`relationship_instance_id = rel:{predicate}:{source}:{target}:{digest12(passage)}`, which
`extraction/core/identifiers.py:141-161` already mints. The loader `MERGE`s on that property,
never on the endpoint pair alone — two filings asserting `BORROWS_UNDER` between the same pair
are two evidenced assertions, and collapsing them would discard a citation.

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

## 4.3 Issue ids

`issues.jsonl` rows have no id today (P5). The projection mints
`issue:{code_slug}:{digest12(passage_id, code, detail, sorted candidate ids)}` — deterministic,
readable, and stable across rebuilds of the same run. If Stage 13 turns out to write an id of
its own, G0 adopts it and this rule is deleted rather than kept alongside.

## 4.4 Determinism

The projection is a **pure function** of (extraction run catalogs, ontology definitions,
normalization catalogs, projection version). It must contain no timestamp, no random value, no
dictionary-iteration-order dependence, and no clock. It writes `nodes.jsonl` and `edges.jsonl`
with `sort_keys=True` and a declared sort order (nodes by `(label_base, key)`, edges by
`(type, source_key, target_key, edge_key)`), matching `normalization/utils/jsonl.py`.

**Acceptance:** two projections of one extraction run produce byte-identical exports, and two
loads of one export produce identical node, edge and property counts (§10).

---

# 5. Neo4j design

## 5.1 Deployment

Local Neo4j 5 Community in Docker, data in `./neo4j_data/` — already gitignored. Bolt on
`localhost:7687`, Browser on `localhost:7474`. Credentials from the environment, never in
`config/graph.yaml`.

*(unverified — confirm at G2)*: Community Edition supports `IS UNIQUE` constraints but not
`IS NODE KEY` or existence constraints, and hosts a single user database. This plan is designed
so Community suffices: uniqueness constraints only, NOT NULL enforced by the projection models
and re-checked by a post-load Cypher assertion, and rebuilds wipe the one database rather than
creating a new one.

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
| `MetricObservation.population` — **four** fields (`models.py:364-370`) | all four projected: `population_definition_raw`, `population_definition_normalized`, `population_role`, `population_confidence`. §7.1's whole point is that two differing `definition_raw` values are **not one series**, so it must be a first-class property, and Q2 filters on it. `definition_normalized` is projected even though V1 never populates it — dropping a declared field quietly is the failure §2.2's "fail loudly, never default" rule exists to prevent, and it would be this section doing it. |
| `extractor_metadata` | a whitelist of scalar keys promoted to properties (`scale`, `scale_location`, `row_label`, `column_label`, `period_label`, `period_label_char_start`, `period_label_in_evidence`, `period_label_distance_from_evidence` — all four period fields of `V1_CLAIM_EXTRACTION` §8a.12, since `period_label_char_start` is exactly what §7.2's evidence highlighting needs), `ambiguity_codes` as a string list, and everything else preserved as `extractor_metadata_json` so nothing is lost. |

Values: `MetricObservation.value` is `float \| int \| str \| bool`. It is stored under its own
type in `value`, with `value_text` holding the printed form from `quoted_text`/`raw_text` where
one exists. Scale is already applied by the lane — claims carry absolute USD — and the graph
does not re-scale anything.

## 5.5 Temporal, provenance and validation properties

**Temporal.** ISO strings are authoritative, because they are what the catalogs contain and
what byte-identity is checked against: `period_start`, `period_end`, `instant_date`,
`period_key`, `occurred_on`, `announced_on`, `valid_from`, `valid_to`. Typed `date()` twins are
added *only* where range queries need them — `occurred_on_date`, `announced_on_date`,
`period_end_date` — and are derived, never authoritative.

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

**Validation state.** `validation_state` ∈ `{clean, warned}` plus `warning_codes: [...]`, and a
non-exclusive `:Warned` label for Browser styling. A run's errors never reach the graph, because
a claim with an error never reaches a catalog — but `unpreferred_source_lane` and
`QUOTED_TEXT_NOT_IN_PASSAGE` do, and §1.3 is the reason they must be visible rather than
averaged away. Depends on P6.

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
`MALFORMED_EVENT_PROPERTY` (§5.4 — a property value Neo4j cannot store as a scalar).

Failing rather than skipping is the whole point: a graph that silently drops 3% of its claims
looks exactly like a graph that did not. The file exists so the reason is auditable when it
happens.

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

The corpus is single-company, so the hairball is structural: potentially thousands of
observations all one hop from `opendoor` via `OBSERVATION_OF_SUBJECT`. Three rules:

1. **never** `MATCH (n) RETURN n` — the pack has no unbounded query, and Browser's node limit
   is set in the saved settings;
2. every observation query is filtered by metric, period or document, and `LIMIT`ed;
3. `OBSERVATION_OF_SUBJECT` is the edge to *not* traverse when exploring. The interesting fan-out
   is `Metric → Observation → Passage → Document`, which is narrow at every step.

If P11 comes back with more observations than expected, G5 records the real counts and adjusts
the limits — it does not redesign the model.

---

# 8. The query pack

Committed as `graph/queries/*.cypher`, one file per query with a comment header, and loaded as
Browser favourites. Each is also an executable test at G4 asserting the returned *shape*, not
the row count (which depends on the run).

| # | View | Sketch |
| --- | --- | --- |
| Q1 | Company overview | `MATCH (c:Entity {entity_id:'opendoor'})<-[:OBSERVATION_OF_SUBJECT]-(o:Observation)<-[:HAS_OBSERVATION]-(m:Metric) RETURN m.metric_id, count(o), min(o.period_key), max(o.period_key) ORDER BY m.metric_id` |
| Q2 | Metric timeline | `MATCH (m:Metric {metric_id:$metric})-[:HAS_OBSERVATION]->(o:Observation) RETURN o.period_key, o.value, o.unit, o.source_lane, o.population_definition_raw, o.validation_state ORDER BY o.period_key` — **grouped by `population_definition_raw`**, because §7.1 forbids presenting two denominators as one series |
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
4. **Every fact links back to claim and evidence provenance.** Every `:Observation`, `:Event`
   and every ontology-declared edge carries a non-null `claim_id`, `extraction_run_id`,
   `ontology_definition_hash` and at least one resolvable `passage_id`; every `passage_id` in
   the graph exists in `data/normalization_catalog/passages.jsonl`.
5. **Nothing unsupported loads as an accepted fact.** Abstentions, policy rejections and
   deferred metrics appear only as `:Issue`, with no edge into the fact graph; the count of
   `:Issue` nodes equals the row count of `issues.jsonl`; no `:Observation` exists whose
   `claim_id` is absent from `claims.jsonl`.
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
10. **No custom frontend.** The graph is inspectable through Browser using only the committed
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

| Stage | Work | Gate |
| --- | --- | --- |
| **G0** | Inspect the finalized Stage 13 run and **freeze the graph input contract**. Answer every P-item in §2.2 in [STAGE13_GRAPH_INPUT_HANDOFF.md](STAGE13_GRAPH_INPUT_HANDOFF.md); copy a small committed fixture (a handful of claims of all three kinds, their passages, a manifest) into `tests/fixtures/graph/`. Correct this plan where reality contradicts it, with the reason. | Every P-item answered or explicitly still-open with a named owner; fixture committed; no plan statement left contradicted |
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

**14.2 — Do warned claims load?**
*Recommendation: yes, with `validation_state: warned`, `warning_codes`, and a `:Warned` label
styled with a red border.* The alternative — withholding them — makes the graph look cleaner
than the extraction was, which is the failure mode §1.3 exists to prevent. Depends on P6: if
Stage 13 records warnings only at run level, this degrades to a run-level note and G0 says so.

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
