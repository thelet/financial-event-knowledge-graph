# real_estate_marketplace_v1

The executable ontology for the Opendoor financial-event knowledge graph. Grounded in the
normalized SEC corpus (294 documents, 14,203 passages, FY2020 – Q1 2026) and in
[`OPENDOOR_CONCEPT_AND_METRIC_RESEARCH.md`](../../../plans/ontology/OPENDOOR_CONCEPT_AND_METRIC_RESEARCH.md).

**131 concepts** in 11 categories, **4 instances**, **17 alias entries** (267 surface forms
indexed), **35 external mappings**, all verified.

## Using it

```python
from ontology import load_ontology

ontology = load_ontology()                      # loads, validates, indexes, hashes
ontology.definition_hash                        # 3372c5777c1d…  stable across runs

registry = ontology.registry
registry.metric("contribution_profit").reconciles_to          # 'gaap_gross_profit'
registry.resolve_alias("gross profit")                        # both candidates, not a guess
registry.is_ambiguous("gross profit")                         # True
registry.formula_for("adjusted_gross_profit", "2021-06-30")   # v1 — by VALUE date

result = ontology.validate_claim(claim)
result.ok, result.codes, result.render()
```

`load_ontology` is the only entry point. Nothing outside this package should import
`loader.py` — `public.py` is the contract, exactly as each stage package in `acquisition/`
and `normalization/` has one.

## Files

```text
real_estate_marketplace_v1/
├── public.py        the only entry point outside callers use
├── loader.py        YAML -> typed models
├── definitions/     the ontology itself, 12 YAML files
└── examples/        24 claim fixtures, all executed by tests
```

Declarative data and code live in separate directories on purpose: the YAML *is* the
ontology, the Python merely reads it, and a test fails if either leaks into the other's
directory. Adding a metric is a `definitions/` edit; Python never holds a second copy
(enforced by `tests/ontology/test_package_structure.py`).

| `definitions/` file | Holds |
| --- | --- |
| `ontology.yaml` | identity, semantic version, research provenance |
| `entities.yaml` | 13 entity types + 4 named instances |
| `roles.yaml` | 8 contextual roles |
| `instruments.yaml` | 5 instrument types, 10 agreement types |
| `metrics.yaml` | 26 metric definitions |
| `formulas.yaml` | 8 dated formula versions |
| `events.yaml` | 19 event types |
| `relationships.yaml` | 30 typed relationships |
| `claims.yaml` | evidence, claim and status types |
| `external_mappings.yaml` | FIBO mappings for non-metric concepts |
| `aliases.yaml` | surface forms that need cross-concept handling |
| `constraints.yaml` | parameters for the machine-checkable constraints |

Fixtures live in `examples/`, not in `definitions/` — a claim is data *about* the ontology,
not part of it.

Metric mappings live inline in `metrics.yaml`, next to the metric they qualify.
`external_mappings.yaml` covers everything else, so a FIBO review happens in one file.

## The distinctions this ontology exists to protect

Each has an enforcement mechanism, not a comment.

| Distinction | Mechanism |
| --- | --- |
| Homes sold ≠ purchased ≠ under contract ≠ acquisition contracts ≠ inventory | `mutually_distinct_groups`; an undeclared alias collision fails the load |
| Buy-side vs sell-side "under contract" | separate concepts + `distinct_from` |
| Company KPI ≠ macro indicator | `MetricCategory`; `EconomicIndicator` mapping rejected on non-macro metrics |
| GAAP ≠ non-GAAP | `GaapStatus`; `reconciles_to` is never an equivalence |
| Profit ≠ margin | different `value_type`, mutual `distinct_from` |
| Adjusted Gross Profit definition drift | two formula versions with disjoint date ranges; resolution by **value** date |
| 120-day denominator ambiguity | three `raw_variants`, `confidence: low`, a high-impact `Ambiguity` |
| Opendoor's figure ≠ the market's | `Population.role` = `baseline` \| `comparison`, two observations |
| Entity ≠ role | roles live in `roles.yaml`; declaring one as an entity type fails the load |
| Reported ≠ calculated | `AssertionType`; each rejects the other's fields |
| Live dashboard ≠ citable evidence | `canonicality: discovery_only`; citing it for a filed period is an error |

## What is deliberately absent

- **No FIBO URI was invented.** Where FIBO has no class — warrants, convertible notes,
  geographic markets, commercial partnership roles, and every operating KPI — the mapping is
  recorded as `no_mapping` with the module that was inspected. A stated absence is a
  finding; an omission would look like the question was never asked.
- **No XBRL tag was assumed.** `forbidden_source_lanes: [xbrl]` on the operating KPIs and
  non-GAAP measures records a measured result: 336 custom `open:` elements across 97 taxonomy
  files contain no tag for any of them.
- **No ambiguity was silently resolved.** The 120-day denominator, the acquisition-contracts
  period semantics, the homes-sold recognition point and the resale-contract unit are
  recorded as `Ambiguity` entries with an impact rating.

## Changing it

1. Edit the YAML.
2. `python -m pytest tests/ontology` — the load, the constraints, the fixtures and the
   structural rules all run in under two seconds.
3. The `definition_hash` changes only when a definition changes. Reordering entries,
   reformatting, or editing a `notes:` field does not.
