# Ontology v1 — implementation plan and report

**Specification:** [OPENDOOR_CONCEPT_AND_METRIC_RESEARCH.md](OPENDOOR_CONCEPT_AND_METRIC_RESEARCH.md)
and the five artifacts in [research/](research/).

**Scope:** the executable ontology layer and its validation/runtime access. No extraction,
no graph, no persistence.

*Sections 1–8 are the plan, written before implementation. Sections 9 onward are the report,
completed after.*

---

# 1. What this package is, and is not

It is a **library**, not a pipeline. Acquisition and normalization are staged pipelines with
`stages/`, `pipeline.py` and a CLI; an ontology has no stages and no run — it loads,
resolves, answers questions and validates. Copying the pipeline shape here would be
symmetry for its own sake, which `CLAUDE.md` explicitly warns against.

What it keeps from repository convention: a thin public contract, one composition root,
declarative data separated from runtime code, typed models, no `utils` dumping ground, no
`implementation.py`, and structural tests that enforce the boundaries.

**Hard independence rule:** the package must not import — directly or transitively — any
LLM client, graph driver, embedding library, vector store, prompt framework, or the
`acquisition` / `normalization` packages. Evidence is referenced by **id and small boundary
model**, never by importing a normalization class. A structural test enforces this.

# 2. Package structure

```text
ontology/
├── __init__.py
├── contracts.py          Ontology protocol + ConceptRegistry protocol
├── context.py            composition root: load_ontology(ontology_id) -> Ontology
├── registry.py           concept/alias/relationship lookup over a resolved snapshot
├── validation.py         claim, observation and event validation
├── serialization.py      canonical snapshot + deterministic definition hash
├── core/
│   ├── values.py         enums (categories, units, period types, mapping types, lanes…)
│   ├── identifiers.py    id rules + canonical hashing
│   ├── errors.py         OntologyError, ValidationIssue, ValidationResult
│   ├── models.py         typed definition models + runtime claim/observation models
│   └── constraints.py    machine-checkable constraint implementations
└── versions/
    └── real_estate_marketplace_v1/
        ├── __init__.py
        ├── public.py     the one public entry point for this version
        ├── loader.py     YAML -> typed models -> resolved snapshot
        ├── ontology.yaml       identity, semantic version, research provenance
        ├── entities.yaml       entity types + instances (markets, channels)
        ├── roles.yaml          contextual roles
        ├── instruments.yaml    instruments and agreements
        ├── metrics.yaml        metric definitions
        ├── formulas.yaml       metric formula versions
        ├── events.yaml         event types
        ├── relationships.yaml  typed relationships
        ├── constraints.yaml    declarative constraint parameters
        ├── external_mappings.yaml  FIBO + XBRL + US-GAAP
        ├── aliases.yaml        alias -> concept, with ambiguity
        ├── README.md
        └── examples/
            ├── valid/*.yaml     12 fixtures
            └── invalid/*.yaml   10 rejection fixtures
```

**YAML is authoritative.** Python implements schemas, loading, validation and access — never
a second copy of the ontology. A test asserts no concept id is hard-coded in Python outside
enums and test fixtures.

# 3. Concept categories

Eleven categories, each with its own typed model rather than one blob accepting arbitrary
properties:

| Category | Model | Why distinct |
| --- | --- | --- |
| `entity_type` | `EntityTypeDefinition` | has `is_a`, may be abstract |
| `role_type` | `RoleTypeDefinition` | has player types, context types, time-bounding |
| `financial_instrument_type` | `InstrumentTypeDefinition` | issuer/holder semantics |
| `agreement_type` | `AgreementTypeDefinition` | party roles |
| `metric_definition` | `MetricDefinition` | unit, period, threshold, population |
| `metric_formula` | `MetricFormulaVersion` | date-ranged expression |
| `event_type` | `EventTypeDefinition` | participants, temporal fields |
| `relationship_type` | `RelationshipDefinition` | endpoints, inference level |
| `evidence_type` | `EvidenceTypeDefinition` | required fields |
| `claim_type` | `ClaimTypeDefinition` | which payload it carries |
| `status_type` | `StatusTypeDefinition` | enumerated states |

# 4. The distinctions the research says must not collapse

These are the reason the package exists, so each gets an explicit enforcement mechanism:

| Distinction | Enforcement |
| --- | --- |
| Homes purchased ≠ acquisition contracts ≠ homes under contract | `mutually_distinct` constraint group; alias-collision check fails the load |
| Company KPI ≠ macro indicator | `MetricCategory` + a constraint forbidding `EconomicIndicator` mapping on non-macro metrics |
| Reported ≠ calculated | `AssertionType`; calculated requires formula version + input observations |
| GAAP ≠ non-GAAP | `GaapStatus` on every metric; margin/profit pairs kept separate |
| Adjusted Gross Profit drift | two `MetricFormulaVersion`s with disjoint date ranges; resolution is by observation date |
| 120-day population ambiguity | `population_definition_raw` per observation, `population_confidence` on the definition |
| Opendoor value ≠ market comparison | `Population.role` = `baseline` \| `comparison`, separate observations |
| Entity ≠ role | `Lender`/`Borrower` are `role_type`, never `entity_type`; a constraint rejects them as entity types |
| Live dashboard ≠ canonical evidence | `Canonicality.discovery_only` + a constraint rejecting it as historical SEC evidence |

# 5. Determinism

`definition_hash` = SHA-256 over the canonical JSON of the **resolved** snapshot with
volatile fields excluded. No timestamps, run ids or code commits in the authoritative
snapshot — the same rule the normalization layer already enforces on documents and passages,
for the same reason.

Loading and serializing unchanged definitions twice must be byte-identical. Tested.

# 6. Metric model

`MetricDefinition` carries: id, canonical label, description, category, aliases, value type,
unit, period type, GAAP status, source-lane preferences, population definition, threshold +
comparison operator + threshold unit + measurement start, validity dates, external mappings,
ambiguities, and `distinct_from`.

`MetricFormulaVersion` carries: id, metric id, valid-from/to, expression, component metrics,
adjustment components (included and **excluded**), terminology, source evidence.

`MetricObservation` (runtime) carries: id, metric, subject, value, unit, period fields,
population, dimensions, reporting basis, source lane, evidence, assertion type, confidence,
and — for calculated observations — formula version, input observation ids and expression.

# 7. Implementation sequence

1. `core/values.py`, `core/identifiers.py`, `core/errors.py`
2. `core/models.py` — definition models, then runtime models
3. `core/constraints.py`
4. YAML definitions for `real_estate_marketplace_v1`
5. `versions/.../loader.py` — parse, resolve, validate, snapshot
6. `registry.py`, `serialization.py`, `validation.py`
7. `contracts.py`, `context.py`, `versions/.../public.py`
8. Example fixtures — 12 valid, 10 invalid
9. Tests, then the full existing suite
10. README + this report's results sections

# 8. Acceptance

The 21 criteria in the task brief. Each is mapped to a test in §14.

---

# 9. Final package structure

Built as planned in §2, with two additions and one removal.

```text
ontology/                             2,734 lines of Python
├── __init__.py                       re-exports load_ontology and the contracts
├── contracts.py                      Ontology, ConceptRegistry, OntologyLoader protocols
│                                     + OntologyDefinitions, the loader/registry boundary
├── context.py                        composition root: load_ontology, build_ontology
├── registry.py                       concept, alias, formula and taxonomy lookup
├── validation.py                     definition-time and claim-time orchestration
├── serialization.py                  canonical snapshot + definition_hash
├── examples.py                       ADDED — reader for the claim fixtures
├── core/
│   ├── values.py                     16 enums + KNOWN_UNITS, MONETARY_UNITS
│   ├── identifiers.py                id rules, canonical JSON/hash, alias normalization
│   ├── errors.py                     ValidationIssue/Result + 33 error codes
│   ├── models.py                     21 definition models + 7 runtime models
│   └── constraints.py                22 checks, parameterized from constraints.yaml
└── versions/real_estate_marketplace_v1/    2,581 lines of YAML
    ├── public.py                     ONTOLOGY_ID, DEFINITION_DIR, EXAMPLES_DIR, load_definitions
    ├── loader.py                     YAML -> typed models, table-driven
    ├── README.md
    ├── 12 YAML files                 claims.yaml ADDED; see §11
    └── examples/valid (12), examples/invalid (12)
```

**Added `examples.py`.** Fixtures needed a reader, and a fixture is a claim — claims are
version-independent, so it belongs to the library rather than to the version package. The
version package supplies only the directory.

**Added `claims.yaml`.** §2 listed eleven concept categories but only ten files. Evidence,
claim and status types had no home. One file for all three, rather than three files of a
dozen lines each: none is large enough to justify its own, and splitting them would be the
symmetry-for-its-own-sake that `CLAUDE.md` warns against. Recorded as a deviation in §11.

**Removed `formula_version_id()`** from `identifiers.py`. It derived `<metric>__<version>`
as a fallback for formula entries with no explicit id, but every entry declares one, so it
was dead code implementing a second id scheme. Two schemes for one thing is worse than a
little duplication; see §10.

# 10. Decisions made

**A library, not a pipeline.** No `stages/`, no `pipeline.py`, no CLI. Acquisition and
normalization advance state through ordered phases and have a run id; an ontology has
nothing to advance. `load_ontology()` returns an object that answers questions.

**Loading and validation are separate.** `YamlDefinitionLoader.load()` parses and attaches;
`context.build_ontology()` validates and indexes. This is why `validate_definitions` takes
`OntologyDefinitions` rather than a directory — definitions built in a test face exactly the
same checks as definitions read from disk, which is what let §13's constraint tests be
written against purpose-built ontologies instead of hoping the real one contains a violation.

**Checks in Python, parameters in YAML.** `core/constraints.py` contains no concept id;
every id it acts on arrives through `ConstraintParameters`, loaded from `constraints.yaml`.
Adding `homes_sold` to a mutually-distinct group is a data edit. A structural test asserts
that no concept id appears in executable Python outside the version package.

**One id scheme for formulas.** YAML declares `concept_id: adjusted_gross_profit_v1`
explicitly. The alternative — deriving it from `metric_id` and `version` — guarantees the
two cannot drift apart, but it produced `adjusted_gross_profit__1` and left two live schemes
when a YAML file also declared an id. Explicit ids, one scheme, no hidden construction.

**Alias resolution returns every candidate.** "gross profit" denotes both the GAAP measure
and the adjusted one; the ontology's job is to say so. `resolve_alias` returns both, ordered
by concept id so the answer is reproducible, and `is_ambiguous` says whether a caller must
supply context.

**Acknowledged ambiguity is a finding, not a defect.** An alias resolving to two members of
a mutually-distinct group fails the load — *unless* `aliases.yaml` marks it
`ambiguous: true`, in which case it is recorded at INFO severity. Seven of the shipped
aliases are in this state. Silently merging the two concepts is the failure; declaring that
a phrase is genuinely ambiguous is correct behaviour.

**Subtype-aware type checking.** A metric declaring `subject_types: [company]` accepts
`public_company`, and a relationship endpoint declaring `company` accepts `subsidiary`.
Without this, every declaration would have to enumerate its own subtypes and would silently
start rejecting valid claims when a subtype is added.

**Evidence rules live with the payload.** `validate_claim` does not re-check evidence at
claim level. Each payload validator decides: a calculated observation needs none, because it
is re-derivable from its formula version and inputs; a reported one is only as good as the
passage it came from. A blanket claim-level check contradicted that rule from a place that
knew less than the rule did — found by fixture `valid/05`, see §15.

**Formula resolution is by value date, never filing date.** A 2024 10-K restating FY2021
must use the FY2021 formula for the FY2021 column. `formula_for(metric_id, as_of)` documents
this explicitly because passing the filing date is the natural mistake and returns a
plausible wrong answer.

**Forbidden lanes are errors; unpreferred lanes are warnings.** A GAAP figure read from a
table instead of XBRL is weaker evidence, not a wrong claim. An operating KPI claiming an
XBRL source is a wrong claim: the research checked all 97 taxonomy files and no such tag
exists.

# 11. Deviations from the research recommendation

| Deviation | Why |
| --- | --- |
| `claims.yaml` groups three categories in one file | Evidence, claim and status types total 12 entries. Three files would be structure without content. |
| 19 event types, not the 18 in the brief | `operations_pause` and `product_discontinuation` both appear in the corpus and neither fits an existing type. Splitting `executive_change` into appointment/departure/role-change would have hit 18 by fragmenting one concept, which is worse. |
| 30 relationships, not ~29 | Same reason; the count was approximate in the brief. |
| 26 metrics, not the 21 required | The 21 required are all present. Five more (`cost_of_revenue`, `holding_costs`, `direct_selling_costs`, `homes_under_resale_contract`, `adjusted_ebitda_margin`) are named inputs of required formulas — a formula referencing an undeclared component fails the load, so they are not optional. |
| Roles carry no FIBO mapping for `partner` | FIBO BE models the legal partnership *form*, which is not a distribution partnership between two corporations. Recorded as `no_mapping`. |
| `Canonicality` is validated but not a typed field | Only `disclosure_channel` instances carry it, and instances use a free-form property map. The loader rejects an unknown value, so the enum is load-bearing without forcing a per-category instance model. |

# 12. Concepts included and deferred

**Included** — 131 concepts:

| Category | Count |
| --- | --- |
| relationship_type | 30 |
| metric_definition | 26 |
| event_type | 19 |
| entity_type | 13 |
| agreement_type | 10 |
| role_type | 8 |
| metric_formula | 8 |
| financial_instrument_type | 5 |
| evidence_type | 5 |
| status_type | 4 |
| claim_type | 3 |

Plus 4 instances (`opendoor`, `opendoor_accountable`, `sec_edgar`, `nasdaq`) and 17 alias
entries expanding to 267 indexed surface forms.

**External mappings** — 35, every one `verified: true`:

| System | Count | | Type | Count |
| --- | --- | --- | --- | --- |
| FIBO | 20 | | exact | 19 |
| standard US-GAAP | 7 | | no_mapping | 9 |
| Opendoor XBRL extension | 7 | | related | 4 |
| DEI metadata | 1 | | broader / narrower | 2 |
| | | | pattern_only | 1 |

16 of the 20 FIBO mappings carry a URI; the other 4 are stated absences. Every URI came from
`research/fibo_candidate_mappings.jsonl` and was checked against the published module. None
was constructed by analogy.

**Deferred:**

- **Named market instances.** The corpus reports a market *count* far more often than it
  names metros. The `geographic_market` type and `market_count` metric exist; populating
  individual markets is extraction work.
- **Named subsidiaries.** `SUBSIDIARY_OF` and the `subsidiary` type exist. EX-21.1 holds 68
  `<tr>` but only 4 substantive subsidiaries, so this is a small extraction task, not an
  ontology one.
- **Named lenders.** Same reasoning — the role and relationship exist; the counterparties
  come from EX-10 exhibits.
- **Per-instrument warrant classes.** OPENW and OPENZ are listed instruments; enumerating
  them adds nothing until instrument-level facts are extracted.
- **`FND/Arrangements` FIBO module.** Not inspected during research, so partnership and
  employment agreements carry no FIBO mapping rather than a guessed one.

# 13. Validation rules

**Definition-time — 13 rules; any error fails the load.**

| Code | Rejects |
| --- | --- |
| `duplicate_concept_id` | two concepts with one id |
| `unknown_reference` | any `is_a`, `subject_types`, `distinct_from`, `component_metrics`, alias target or mapping target that does not resolve |
| `invalid_inheritance` | `is_a` crossing category boundaries |
| `inheritance_cycle` | a cyclic `is_a` chain |
| `role_declared_as_entity_type` | `lender` and friends appearing in `entities.yaml` |
| `unknown_unit` | a unit outside `KNOWN_UNITS`, or a default not in `allowed_units` |
| `external_uri_present_for_no_mapping` | recording an absence and a target at once — almost always an invented URI |
| `invalid_external_mapping` | a non-absence mapping with neither URI nor label |
| `company_kpi_mapped_as_macro_indicator` | FIBO `EconomicIndicator` on anything but a `macroeconomic` metric |
| `alias_collision_between_distinct_concepts` | an undeclared alias spanning a mutually-distinct group |
| `formula_version_date_overlap` | two versions of one metric covering the same date |
| `formula_component_not_a_metric` | a formula input that is not a declared metric |
| `invalid_relationship_endpoint` | an endpoint type that does not exist; a declared inverse that does not point back (warning) |

**Claim-time — 18 rules; never fails the load, only the claim.**

| Code | Rejects |
| --- | --- |
| `unknown_reference` | unknown metric, event type, or a claim whose kind does not match its payload |
| `instant_metric_requires_instant_date` / `duration_metric_requires_period_start_and_end` | a value with no time |
| `period_end_before_period_start` | a reversed period |
| `observation_unit_not_allowed_for_metric` | a home count reported in dollars |
| `monetary_value_requires_currency` | a dollar amount with no currency |
| `subject_type_not_allowed_for_metric` | a company metric measured on a person (subtype-aware) |
| `source_lane_not_allowed_for_metric` | an operating KPI claiming an XBRL source |
| `percentage_outside_documented_range` | a portfolio share above 100 % |
| `calculated_observation_requires_inputs_and_formula` | a calculated value that cannot be re-derived |
| `reported_observation_must_not_carry_calculation_fields` | a figure claiming to be both read and computed |
| `no_formula_version_covers_observation_date` | applying the FY2022 formula to an FY2021 value |
| `missing_evidence` | a reported claim with nothing to cite, or evidence with no anchor |
| `invalid_evidence_offsets` | `char_start` without `char_end`, or a non-advancing range |
| `discovery_only_channel_cited_as_canonical_evidence` | the live dashboard cited for a filed period |
| `confidence_outside_0_1` | a confidence that is not a probability |
| `event_missing_required_temporal_field` | an earnings release with no period |
| `event_participant_invalid` | a market entry with no market; a role the type does not declare; a wrong entity type; a second participant in a single-cardinality role |
| `unknown_relationship_predicate` | a predicate the ontology never declared |
| `invalid_relationship_endpoint` | a person as borrower under a facility |
| `role_requires_context_entity` / `role_player_type_not_allowed` | a lender relationship with no facility, or a player type the role forbids |

One warning-severity code, `unpreferred_source_lane`, marks a permitted but non-preferred
lane.

# 14. Test coverage

**116 ontology tests, all passing in 1.8 s.**

| Module | Tests | Covers |
| --- | --- | --- |
| `test_loading.py` | 11 | load succeeds; every section populated; hash and snapshot byte-identical across loads; no timestamp in the snapshot; hash changes when a definition changes; missing file, malformed entry (reports *all* bad fields, not the first), orphaned mapping block, unknown ontology id |
| `test_registry.py` | 14 | concept and metric lookup; canonical labels resolve without being repeated as aliases; case and quote insensitivity; ambiguous aliases return every candidate; stable order; formula resolution by date at four points; AGP drift visible in the components, including the *excluded* restructuring line; subtype chain; instance lookup |
| `test_definition_constraints.py` | 15 | every definition-time rule, each on an ontology built to violate it; plus the shipped ontology declares no role as an entity type and maps `EconomicIndicator` only to macro metrics |
| `test_claim_validation.py` | 23 | every claim-time rule; forbidden lane is an error while unpreferred is a warning; calculated observations need no evidence; baseline and comparison are two observations of one definition; batch accumulation |
| `test_examples.py` | 6 | all 12 valid fixtures validate; all 12 invalid fixtures fail *with the codes they declare*; every fixture explains itself; an invalid fixture with no declared codes is refused |
| `test_research_distinctions.py` | 23 | one test per row of the §4 table, asserted against the **shipped** ontology — a rule never triggered because the data forgot to declare it protects nothing |
| `test_package_structure.py` | 24 | no LLM, graph, embedding, vector-store, dataframe, database, `acquisition` or `normalization` import in any module *or transitively*; no `implementation.py`/`utils.py`; only `context.py` names a version; no concept id in executable Python; nothing outside imports `loader.py`; protocols satisfied; YAML outweighs Python 3:1 in the version package |

Full suite: **677 passed, 5 live deselected** (was 561 before this work).

# 15. Results

The package loads, validates, resolves, queries and validates example claims end to end.

```
<LoadedOntology real_estate_marketplace_v1 v1.0.0 3372c5777c1d>
definition_hash: 3372c5777c1d474ce932a16bb60faa04b92f97562ce2c733f271a0763adf4ddb
snapshot:        89,548 bytes, byte-identical across loads
definition validation: OK — 0 errors, 0 warnings, 7 info
```

The 7 info-level findings are the acknowledged ambiguous aliases: `homes`, `contracts`,
`under contract`, `gross profit`, `gross margin`, `margin`, `contribution`. Each spans a
mutually-distinct group *on purpose* and is declared `ambiguous: true`.

All 24 fixtures behave as declared: 12 valid claims validate, 12 invalid claims fail with
the specific codes they name.

**Four defects the work surfaced, each found by a test or fixture rather than by reading:**

1. **Dangling references in the YAML** — `regulator_role.context_types` named a
   `regulatory_event` that was never defined, and `homes_purchased.distinct_from` named
   `offers_made`. Caught by `check_references` on the first load attempt. Both removed.

2. **`check_observation_subject` was not subtype-aware.** Every metric declares
   `subject_types: [company]`, so observations about Opendoor as a `public_company` were
   rejected — four valid fixtures failed at once. Fixed by routing the check through
   `registry.accepts_type`, the same subtype-aware predicate relationship endpoints use.

3. **`validate_claim` re-checked evidence at claim level**, contradicting the payload rule
   that a calculated observation needs none. Found by `valid/05`. The blanket check was
   removed; each payload validator now owns the decision, with a comment saying why.

4. **Formula versions leaked their metric's label into the alias index.**
   `contribution_profit_v1` is labelled "Contribution Profit (Loss)", so resolving that
   phrase returned both the metric and the dated formula behind it. Found by
   `test_canonical_label_resolves_without_being_repeated_as_an_alias`. Formula versions are
   now excluded from the alias index in both `registry.py` and `validation.py`.

Two smaller corrections: `tests/ontology/factories.py` was renamed
`ontology_factories.py` because `tests/normalization/factories.py` already occupies that
module name on `pythonpath`; and the structural tests were changed to inspect the module
with docstrings stripped, because "no concept id in Python" is a rule about executable code
— a docstring naming `homes_sold` to explain *why* a rule exists is exactly the kind of
comment this repository asks for.

# 16. Open questions before extraction

Recorded in the ontology as `Ambiguity` entries, not resolved here.

1. **The 120-day denominator (high impact).** Three filed wordings — "our portfolio"
   (10-K/10-Q MD&A), "our homes" (shareholder letters), "our homes in inventory" (Q1 2023
   letter). Whether the denominator is listed homes or all owned homes is never stated
   identically. Every observation must carry `population.definition_raw` verbatim until a
   filing settles it.

2. **Acquisition contracts at two frequencies (medium).** Reported weekly on
   accountable.opendoor.com and quarterly in filings. Whether that is one definition at two
   frequencies or two measures is not stated. Until it is, the weekly series is
   discovery-only and cannot be cited.

3. **Homes-sold recognition point (medium).** No filing defines whether a home counts at
   title transfer, closing, or contract completion. This matters at quarter boundaries.

4. **`open:InventoryRealEstateInResaleContract` unit (medium).** Label "Under contract for
   sale", observed value 781. A home count and dollars-in-thousands are both plausible from
   the label; resolve from the XBRL context ref before using the value.

5. **Restatement handling.** `SUPERSEDES` exists and is `derived`, but nothing yet decides
   *which* observation supersedes which when a 10-K restates a prior year. That is a policy
   the extractor needs before it emits two observations of the same metric-period pair.

6. **Partnership termination.** `partnership_announcement` records
   `inference_restrictions`: absence from later filings is not a termination. There is no
   `partnership_termination` event type because no such disclosure was found in the corpus.
   If one appears, it needs a type rather than an inferred end date.
