# Financial Event Knowledge Graph

A prototype that turns financial documents — annual and quarterly reports, current-event
filings, earnings-call transcripts, press releases, and shareholder letters — into an
explorable knowledge graph of companies, products, technologies, executives, financial
metrics, and business events, where every extracted fact links back to the exact passage
that supports it.

**Status:** planning. No implementation code yet — this repository currently holds the
product direction, architecture, and component-selection documents.

## Documents

| Document | Contents |
| --- | --- |
| [docs/01_PRODUCT_DIRECTION_AND_ARCHITECTURE.md](docs/01_PRODUCT_DIRECTION_AND_ARCHITECTURE.md) | Purpose, design principles, pipelines, canonical document and graph models, interfaces, configuration, phased implementation sequence, success criteria. |
| [docs/02_COMPONENT_OPTIONS.md](docs/02_COMPONENT_OPTIONS.md) | Candidate tools and projects per interface — acquisition, parsing, extraction references, graph engines, graph repositories, visualization, models, embeddings, retrieval — with advantages, limitations, and fit. |
| [docs/03_TARGET_COMBINATIONS.md](docs/03_TARGET_COMBINATIONS.md) | Two concrete prototype combinations (simple modular vs. temporal graph framework), their milestones and tradeoffs, and the recommended route. |
| [docs/04_DATA_ACQUISITION.md](docs/04_DATA_ACQUISITION.md) | Acquisition design for the first anchor company (Opendoor): sources, wave model, tool selection, storage layout, and first-milestone scope. |

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

Phase 1 of the implementation sequence: define the interfaces, canonical models,
configuration format, and run manifests.

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

Tests: `pytest -m "not live"` runs offline; `pytest -m live` hits the real SEC API.

Current corpus: 109 Opendoor filings, 2,019 artifacts, 739.6 MiB.
See [plans/data-fetching/V0_OPENDOOR_FETCH.md](plans/data-fetching/V0_OPENDOOR_FETCH.md).
