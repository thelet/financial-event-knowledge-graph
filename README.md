# Financial Event Knowledge Graph

A prototype that turns financial documents: annual and quarterly reports, current-event
filings, earnings-call transcripts, press releases, and shareholder letters — into an
explorable knowledge graph of companies, products, technologies, executives, financial
metrics, and business events, where every extracted fact links back to the exact passage
that supports it.

**Status:** six layers implemented and verified — acquisition, normalization, the executable
ontology, claim extraction, the graph projection, and the story agent that writes an investor
post from the graph and refuses to publish one it cannot verify.

| Layer | State |
| --- | --- |
| `acquisition/` | 109 filings, 2,019 artifacts, 739.6 MiB |
| `normalization/` | 294 documents, 12,442 passages (1,935 table), catalogs and reports |
| `ontology/` | `real_estate_marketplace_v1`, `definition_hash e8d4af709be2…`, 26 metrics |
| `extraction/` | `extract-v1-lexical-833f7bcfbce9` — 2,704 observations, 2,714 claims, 6 events from 11,848 candidates |
| `graph/` | `graph-v1-0483dc6b4b10` — 28,836 nodes, 35,600 edges, loaded into Neo4j |
| `story/` | detectors, bounded evidence packages, planner, code-owned derivation, writer, a 12-check deterministic verifier, and a local demo UI |

`pytest -m "not live and not neo4j"` runs **6,175 tests** offline *(measured 2026-08-23)*. The
`neo4j` marker is separate from `live` on purpose — the database is local and free, so
deselecting it means "no container running", not "no network and no cost".

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
| [plans/extraction/V1_CLAIM_EXTRACTION.md](plans/extraction/V1_CLAIM_EXTRACTION.md) | Implemented — candidate selection, table and narrative lanes, ontology validation |
| [plans/graph/V1_GRAPH_PROTOTYPE.md](plans/graph/V1_GRAPH_PROTOTYPE.md) | Implemented — deterministic projection, Neo4j load, 27 verification checks |
| [plans/llm-agent/V1_STORY_AGENT.md](plans/llm-agent/V1_STORY_AGENT.md) | Implemented — detectors, evidence packages, planner, writer, deterministic verifier |
| [plans/llm-agent/INTERACTIVE_DEMO_UI.md](plans/llm-agent/INTERACTIVE_DEMO_UI.md) | Implemented — a loopback-only demo UI over the story pipeline |
| [plans/llm-agent/MULTI_PROVIDER_OPENAI.md](plans/llm-agent/MULTI_PROVIDER_OPENAI.md) | Implemented — a second model provider beside the local one, selectable per run |
| [plans/llm-agent/DETERMINISTIC_FACT_TOOLS.md](plans/llm-agent/DETERMINISTIC_FACT_TOOLS.md) | Implemented — code computes every derived quantity; the model only words it |
| [docs/2026-08-18-live-extraction-implementation-plan](docs/2026-08-18-live-extraction-implementation-plan/IMPLEMENTATION-PLAN.md) | **Planned, not implemented** — a live model path for the extraction lane |

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
        → visual exploration → detectors → bounded evidence package
            → planner → deterministic derivation → writer → verifier → post
```

## Architectural rule

Every concrete tool is an experiment behind a project-owned interface. The durable assets
are the normalized corpus, the canonical models, the ontology versions, the evidence links,
and the component contracts — not any particular provider, framework, or database.

## Next step

A live model path for the **extraction** lane. The story layer can call a model; extraction
cannot — `extraction/context.py` hard-codes `inner=None`, so a store miss is recorded as
`NOT_ATTEMPTED` rather than sent anywhere, and `python -m extraction run` has no `--live` flag.
The audit is
[docs/2026-08-18-extraction-model-path-audit](docs/2026-08-18-extraction-model-path-audit/EXTRACTION-MODEL-PATH-AUDIT.md)
and the plan is
[docs/2026-08-18-live-extraction-implementation-plan](docs/2026-08-18-live-extraction-implementation-plan/IMPLEMENTATION-PLAN.md);
both were re-verified on 2026-08-23 and neither has been started.

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

## Extraction (v1)

Turns normalized passages into ontology-validated claims, with every claim carrying the passage
that supports it. Two lanes: a deterministic table lane, and a model-assisted narrative/event
lane.

```bash
python -m extraction run                # -> data/extraction_runs/<run_id>/
python -m extraction runs               # list runs
python -m extraction inspect <run_id>   # counts, issues, manifest
python -m extraction report <run_id>    # -> markdown reports
python -m extraction verify             # via `graph verify` on the projection
```

Current run `extract-v1-lexical-833f7bcfbce9`: **2,704 observations, 2,714 claims, 6 events**
from 11,848 candidates, with 17,130 recorded issues. 2,690 of the 2,704 observations came from
the deterministic table lane.

**The run made zero provider calls.** The narrative and event lanes ran replay-only against a
cached answer store — not because the answers were cached by choice, but because the lane has no
code path to a model at all. That is the gap the "Next step" section names.

## Graph (v1)

A deterministic projection from an extraction run into nodes and edges, then a load into Neo4j.
The projection is a **pure function returning a value**, so byte-identity between two runs is
checkable rather than asserted.

```bash
python -m graph project <extraction_run_id>   # -> data/graph_runs/<graph_run_id>/
python -m graph load <graph_run_id>           # into the local Neo4j
python -m graph verify <graph_run_id>         # 27 checks
```

Current run `graph-v1-0483dc6b4b10`: **28,836 nodes, 35,600 edges**. Roughly 59% of the nodes
are recorded silence — `:Issue` and `:NotAttempted` — which is deliberate: a candidate that
produced no claim is a fact about the corpus and is kept.

Neo4j runs in Docker; see [docs/05_LOCAL_NEO4J_ENVIRONMENT.md](docs/05_LOCAL_NEO4J_ENVIRONMENT.md).

## Story agent (v1)

Reads the graph, finds a candidate story, packages a bounded slice of evidence, and asks a model
to plan and write an investor post — then refuses to publish one it cannot verify.

```bash
python -m story demo --candidate-id <ID>              # replay a recorded run
python -m story demo --candidate-id <ID> --live       # call a model
python -m story demo --candidate-id <ID> --live --provider openai --model gpt-5.4
python -m story ui                                    # loopback-only demo UI on :8765
```

The rule the whole layer is built around: **code chooses the facts, the model chooses only the
words.** Concretely —

- the model sees one bounded evidence package and has no retriever, no tools and no database;
- every derived quantity is computed by `story/stages/derivation/` from two package facts and
  bound by id; the writer declares no arithmetic of its own;
- a 12-check deterministic verifier with a closed 102-code gate table is the final authority,
  and a draft that fails it produces a recorded rejection rather than a post;
- two providers are selectable per run — a local llama.cpp server and the OpenAI Responses API
  — and which one answered is part of the run's identity, so two otherwise identical runs cannot
  collide.

`OPENAI_API_KEY` is read from the gitignored `.env` or the environment, never from a tracked
file and never sent to the browser.


ui run- 
python -m story ui

llama server-
 ~/llama.cpp/build/bin/llama-server -m ~/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf -ngl 99 -c 8192 --host 127.0.0.1 --port 8080      
  --jinja -fa on --cache-type-k q8_0 --cache-type-v q8_0 > /tmp/llama.log 2>&1 &
