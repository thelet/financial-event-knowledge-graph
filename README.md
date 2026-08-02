# Financial Event Knowledge Graph

A prototype that turns financial documents — annual and quarterly reports, current-event
filings, earnings-call transcripts, press releases, and shareholder letters — into an
explorable knowledge graph of companies, products, technologies, executives, financial
metrics, and business events, where every extracted fact links back to the exact passage
that supports it.

**Status:** three layers implemented and verified — acquisition, normalization, and the
executable ontology. Claim extraction is planned and not yet built.

| Layer | State |
| --- | --- |
| `acquisition/` | 109 filings, 2,019 artifacts, 739.6 MiB |
| `normalization/` | 294 documents, 12,442 passages (1,935 table), catalogs and reports |
| `ontology/` | `real_estate_marketplace_v1`, `definition_hash e8d4af709be2…`, 26 metrics |
| extraction | **planned only** — see the plan below |

`pytest -m "not live"` runs 717 tests offline.

## Documents

| Document | Contents |
| --- | --- |
| [docs/01_PRODUCT_DIRECTION_AND_ARCHITECTURE.md](docs/01_PRODUCT_DIRECTION_AND_ARCHITECTURE.md) | Purpose, design principles, pipelines, canonical document and graph models, interfaces, configuration, phased implementation sequence, success criteria. |
| [docs/02_COMPONENT_OPTIONS.md](docs/02_COMPONENT_OPTIONS.md) | Candidate tools and projects per interface — acquisition, parsing, extraction references, graph engines, graph repositories, visualization, models, embeddings, retrieval — with advantages, limitations, and fit. |
| [docs/03_TARGET_COMBINATIONS.md](docs/03_TARGET_COMBINATIONS.md) | Two concrete prototype combinations (simple modular vs. temporal graph framework), their milestones and tradeoffs, and the recommended route. |
| [docs/04_DATA_ACQUISITION.md](docs/04_DATA_ACQUISITION.md) | Acquisition design for the first anchor company (Opendoor): sources, wave model, tool selection, storage layout, and first-milestone scope. |
| [docs/05_LOCAL_NEO4J_ENVIRONMENT.md](docs/05_LOCAL_NEO4J_ENVIRONMENT.md) | The one service this project runs locally: Neo4j 5.26 LTS Community in Docker, its credentials, and the checks that prove Bolt and Browser answer. |

## Plans

| Plan | Status |
| --- | --- |
| [plans/data-fetching/V0_OPENDOOR_FETCH.md](plans/data-fetching/V0_OPENDOOR_FETCH.md) | Implemented and validated — 109 filings, 2,019 artifacts |
| [plans/data-fetching/REFACTOR_STAGE_STRUCTURE.md](plans/data-fetching/REFACTOR_STAGE_STRUCTURE.md) | Implemented — stage-oriented package structure |
| [plans/normalization/V1_DOCUMENT_NORMALIZATION.md](plans/normalization/V1_DOCUMENT_NORMALIZATION.md) | Implemented — 294 documents, 12,442 passages. §16b records an encoding defect the first run shipped, and its correction |
| [plans/ontology/OPENDOOR_CONCEPT_AND_METRIC_RESEARCH.md](plans/ontology/OPENDOOR_CONCEPT_AND_METRIC_RESEARCH.md) | Complete — concept and metric research behind the first ontology |
| [plans/ontology/ONTOLOGY_V1_IMPLEMENTATION.md](plans/ontology/ONTOLOGY_V1_IMPLEMENTATION.md) | Implemented — `real_estate_marketplace_v1`, 24 fixtures behaving as declared |
| [plans/extraction/V1_CLAIM_EXTRACTION.md](plans/extraction/V1_CLAIM_EXTRACTION.md) | **Planned, not implemented** — candidate selection, table and narrative lanes, ontology validation |

## Anchor company

The first corpus is **Opendoor Technologies Inc.** (CIK `0001801169`, Nasdaq: OPEN) — 665
filings covering 2020-01-31 → 2026-07-29. See
[docs/04_DATA_ACQUISITION.md](docs/04_DATA_ACQUISITION.md).

Note that docs 01 and 03 still carry a semiconductor-shaped example ontology from before the
anchor was chosen. Doc 04 §7 records the required revision to `real_estate_marketplace_v1`.

## Core idea in one picture

```text
Documents → acquisition → canonical normalized passages → extraction
    → entity resolution → graph mutations → graph repository
        → visual exploration (later: Q&A and investor posts)
```

## Architectural rule

Every concrete tool is an experiment behind a project-owned interface. The durable assets
are the normalized corpus, the canonical models, the ontology versions, the evidence links,
and the component contracts — not any particular provider, framework, or database.

## Next step

Claim extraction. The plan is
[plans/extraction/V1_CLAIM_EXTRACTION.md](plans/extraction/V1_CLAIM_EXTRACTION.md); the
first build step is a manually reviewed extraction benchmark, then the deterministic table
lane.

## Acquisition (v0)

Raw SEC filing acquisition for the anchor company. Five independently runnable stages:

```bash
python -m acquisition discover        # -> manifests/filings/<run_id>.json    (immutable)
python -m acquisition resolve         # -> manifests/artifacts/<run_id>.json  (immutable)
python -m acquisition download        # -> data/raw/... + _filing.json        (atomic)
python -m acquisition build-catalog   # -> data/catalog/*.jsonl               (derived)
python -m acquisition verify          # exits non-zero on any inconsistency
python -m acquisition report          # -> data/reports/<run_id>-corpus.md

python -m acquisition acquire         # convenience: all of the above in order
```

Scope lives in `config/fetch.yaml` and `config/companies.yaml` — never in code.
Rerunning `download` is safe and re-fetches nothing that is already valid.

Package layout: `core/` shared infrastructure, `stages/` one package per pipeline stage,
`contracts.py` the stage protocol, `context.py` the composition root, `pipeline.py` the
ordering. A stage is replaceable by swapping it in `context.py`; the pipeline never depends
on a concrete stage class.

Tests: `pytest -m "not live"` runs offline; `pytest -m live` hits the real SEC API.

Current corpus: 109 Opendoor filings, 2,019 artifacts, 739.6 MiB.
See [plans/data-fetching/V0_OPENDOOR_FETCH.md](plans/data-fetching/V0_OPENDOOR_FETCH.md).

## Normalization (v1)

Turns selected raw artifacts into a deterministic, evidence-preserving normalized corpus.

```bash
python -m normalization select          # -> normalization_manifests/<run_id>-selection.json
python -m normalization normalize       # -> data/normalized/... (atomic, per-document)
python -m normalization build-catalog   # -> data/normalization_catalog/*.jsonl (derived)
python -m normalization verify          # exits non-zero on any inconsistency
python -m normalization report          # -> data/normalization_reports/<run_id>-*.md

python -m normalization run             # convenience: the whole pipeline in order
```

Authoritative: the selection manifest, per-document JSON, per-document passages JSONL, the
per-run issue file, and the run manifest. Derived and always rebuilt: the four catalogs and
the reports.

Current corpus: **294 documents, 12,442 passages** (10,507 narrative, 1,935 table), with 60
`NEEDS_REVIEW` and 30 `HIERARCHY_UNCERTAIN` issues and no parser fallbacks. Two independent
full runs produce byte-identical catalogs.

The first full run shipped an encoding defect that corrupted every non-ASCII character —
`V1_DOCUMENT_NORMALIZATION.md` §16b records what it did, why it happened, why the existing
checks missed it, and the corrected counts. The passage counts above supersede the earlier
14,203 / 2,987.

## Ontology (v1)

A library, not a pipeline: it loads, validates and answers questions about a versioned
vocabulary. `real_estate_marketplace_v1` is declared entirely in YAML under
`ontology/versions/real_estate_marketplace_v1/definitions/` — the Python only reads it.

```python
from ontology import load_ontology

ontology = load_ontology()            # <LoadedOntology real_estate_marketplace_v1 v1.0.0>
ontology.definition_hash              # e8d4af709be2… — changes only when the YAML does
ontology.registry.resolve_alias("Adjusted Gross Margin")
ontology.validate_claim(claim)        # -> ValidationResult
```

26 metrics, of which 20 are sourceable from the normalized narrative and table lanes; the
other six name XBRL as their first source lane and wait on an XBRL lane that does not exist
yet. See `V1_CLAIM_EXTRACTION.md` §3.
