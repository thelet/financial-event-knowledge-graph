# TARGET_COMBINATIONS.md

## Proposed Prototype Implementations

The following combinations target the same provider-independent architecture.

The first emphasizes fast results and low engineering cost.

The second emphasizes a richer temporal graph, stronger provenance, and more controlled financial extraction.

---

# Option A — Simple modular prototype

## Objective

Produce a useful graph quickly while preserving enough abstraction to replace components later.

## Combination

```text
Regulatory filing adapter
        │
        ▼
Local raw and normalized files
        │
        ▼
Simple financial document parser
        │
        ▼
Canonical normalized passages
        │
        ▼
Schema-guided custom extraction
        │
        ▼
Configurable language-model adapter
        │
        ▼
Basic entity normalization
        │
        ▼
Local property-graph repository
        │
        ▼
Custom graph API
        │
        ▼
Thin browser graph interface
```

## Suggested characteristics

### Acquisition

* use one mature filing-retrieval library;
* support 10-K, 10-Q, and earnings-related 8-K filings;
* save original documents locally;
* normalize into JSONL or Parquet;
* add transcripts through a separate adapter only after filing ingestion works.

### Extraction

Use a narrow custom extraction pipeline rather than a full graph framework.

Run separate steps:

1. entity extraction;
2. event extraction;
3. relationship extraction;
4. simple entity normalization;
5. graph mutation generation.

Initial ontology:

* Company;
* Product;
* Technology;
* Executive;
* FinancialMetric;
* BusinessSegment;
* FinancialEvent;
* Passage;
* Document.

Initial event types:

* EarningsResult;
* GuidanceChange;
* ProductLaunch;
* SupplyConstraint;
* CapacityExpansion;
* Partnership;
* LeadershipChange;
* RiskDisclosure.

### Graph repository

Use one local property-graph database through a repository adapter.

The graph database should store:

* canonical node IDs;
* typed relationships;
* dates;
* confidence;
* source references;
* passage nodes;
* extraction-run IDs.

### Visualization

Use a custom browser graph library with:

* node colors by type;
* search;
* filters;
* expansion;
* event details;
* evidence drawer;
* basic clusters.

### Language models

Use a `LanguageModelFactory`.

Allow separate configuration for:

* extraction;
* normalization;
* later explanation;
* later post generation.

Start with one local model and retain the ability to compare it with a hosted model.

---

## Advantages

* minimal framework coupling;
* easiest architecture to understand;
* every intermediate result is visible;
* ontology can change rapidly;
* graph backend can be replaced;
* extraction prompts can be changed independently;
* no need to understand a large graph framework;
* best for learning what data model actually works.

## Disadvantages

* temporal invalidation must be added manually;
* deduplication is initially basic;
* hybrid retrieval is not included automatically;
* more custom extraction orchestration;
* more frontend work than using a complete graph-builder application.

## Engineering effort

Moderate, but controlled.

Most work is straightforward:

* adapters;
* canonical schemas;
* prompts;
* graph insertion;
* basic UI.

There is less hidden complexity than adopting a large framework.

## Recommended first milestone

Use:

* three semiconductor companies;
* one annual report each;
* two quarterly reports each;
* earnings-related event filings;
* approximately 100 manually reviewed passages.

Success criteria:

* at least 30 useful event nodes;
* exact evidence for every event;
* clear product/company/event connections;
* graph survives rebuilds;
* extraction strategy can be changed by configuration.

## Best fit

Choose this option when the primary objective is:

> Learn quickly, retain full control, and avoid committing to a graph framework before the ontology is stable.

---

# Option B — Temporal financial graph framework

## Objective

Use an existing temporal graph-construction engine to reduce the amount of custom graph maintenance and retrieval logic.

## Combination

```text
Regulatory filing and transcript adapters
                  │
                  ▼
      Local canonical document store
                  │
                  ▼
     Financial extraction preparation
                  │
                  ▼
  Temporal graph-construction framework
   with custom entity and edge ontology
                  │
                  ▼
     Local graph-database backend
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
 temporal graph search   custom graph API
                            │
                            ▼
                   custom graph interface
```

Graphiti is the leading current candidate for the temporal graph-construction role because it supports custom entity/edge types, configurable graph drivers, incremental episodes, temporal facts, and local OpenAI-compatible models.

FinKario and FinReflectKG should inform the financial extraction and validation design rather than being adopted as complete application frameworks.

## Suggested characteristics

### Acquisition and normalization

Same provider-independent acquisition pipeline as Option A.

The graph framework must never fetch filings directly.

It receives only canonical passages or structured extraction inputs.

### Graph construction

Implement `GraphBuilder` using a temporal graph framework adapter.

The adapter converts:

```text
Canonical Passage
Canonical Ontology
Canonical Model Configuration
```

into the framework's:

* source episode;
* entity types;
* edge types;
* extraction process;
* temporal facts.

The rest of the codebase should not import framework-native entities.

### Ontology

Define typed financial entities and edges using the framework's custom ontology capability.

Stable layer:

* Company;
* Product;
* Technology;
* Executive;
* BusinessSegment;
* Market.

Event layer:

* FinancialEvent;
* GuidanceChange;
* EarningsResult;
* ProductEvent;
* SupplyEvent;
* RegulatoryEvent.

Evidence remains a first-class canonical concept, even when the framework represents it as an episode.

### Model layer

Use our own `LanguageModel` and factory abstraction where the framework permits injection.

Where the framework requires a native client:

* create a bridge adapter;
* keep model configuration outside the framework;
* record model and prompt versions separately;
* avoid provider-specific environment variables throughout business logic.

### Graph storage

Start with the lightest locally supported graph backend.

Keep all graph access behind `GraphRepository`.

Do not query the backend directly from extraction or frontend modules.

### Retrieval

Initially use the framework's built-in:

* semantic search;
* fact search;
* graph traversal;
* temporal validity;
* source-episode references.

Later add our own financial evidence-path retriever above it.

---

## Advantages

* stronger temporal semantics;
* source episodes and provenance;
* incremental updates;
* more advanced entity deduplication;
* fact invalidation and supersession;
* hybrid retrieval;
* less custom graph-maintenance logic;
* closer to the eventual event-evolution graph.

## Disadvantages

* more framework behavior to understand;
* local-model structured output may be unreliable;
* framework abstractions may not perfectly match financial events;
* custom frontend still required;
* adapter boundary must be enforced carefully;
* framework upgrades may affect extraction behavior;
* replacing the framework later is harder than replacing the raw graph backend.

## Engineering effort

Higher than Option A.

Extra work includes:

* studying framework internals;
* mapping canonical models to framework-native models;
* preserving exact passage provenance;
* controlling custom ontology behavior;
* handling local-model validation failures;
* preventing framework-specific concepts from leaking into core code.

## Recommended first milestone

Use:

* one company;
* one annual filing;
* one quarterly filing;
* one earnings transcript or earnings release;
* 20–40 passages.

Prove:

1. custom Company, Product, FinancialEvent, and Passage-compatible concepts;
2. temporal event dates;
3. source-to-fact provenance;
4. entity reuse across multiple documents;
5. replacement or invalidation of a changed fact;
6. local model ingestion;
7. retrieval of a fact plus its supporting source.

Only expand the corpus after this works reliably.

## Best fit

Choose this option when the primary objective is:

> Reach a richer temporal and provenance-aware architecture sooner, accepting more initial framework integration work.

---

# Recommended choice

## Start with Option A, while designing for Option B

The safest implementation route is:

1. Build the provider-independent contracts and canonical models.
2. Implement the data-acquisition pipeline once.
3. Build the first graph using a narrow custom extraction strategy and local graph repository.
4. Implement Graphiti as a second `GraphBuilder` adapter.
5. Run both builders on the same normalized corpus.
6. Compare graph quality, extraction reliability, runtime, and development effort.

This prevents a premature commitment.

The comparison can be:

```yaml
graph_pipeline:
  builder: simple_schema_extractor
```

versus:

```yaml
graph_pipeline:
  builder: temporal_framework_adapter
```

Both should output or expose the same canonical concepts:

* entities;
* events;
* relations;
* evidence;
* graph IDs;
* extraction-run metadata.

## Why not begin exclusively with the framework option?

The desired financial ontology is not yet stable.

Building a small direct pipeline first will reveal:

* which event types matter;
* what evidence needs to be stored;
* how much duplication occurs;
* which relation types are useful;
* what the local model can extract;
* what users actually need to see in the graph.

After that, it will be much easier to evaluate whether a temporal framework reduces work or introduces constraints.

## Why still test the framework early?

Temporal validity, incremental updates, source episodes, and entity resolution are difficult to implement well.

Graphiti already addresses many of those concerns and supports custom types and interchangeable graph drivers.

It should therefore be tested as soon as the canonical models and first reference extraction set exist.

---

# Final target architecture

The likely final prototype can combine the strengths of both approaches:

```text
Reusable acquisition pipeline
            │
            ▼
Canonical local document corpus
            │
            ▼
Configurable extraction pipeline
   ┌────────┴─────────┐
   ▼                  ▼
Direct schema      Temporal framework
extractor          adapter
   └────────┬─────────┘
            ▼
Canonical graph interface
            │
            ▼
Replaceable graph repository
            │
     ┌──────┴────────┐
     ▼               ▼
Graph UI        Retrieval service
                     │
              Later: Q&A and posts
```

The project should regard all concrete tools as experiments.

The durable assets should be:

* the normalized financial corpus;
* canonical document models;
* ontology versions;
* reviewed extraction examples;
* graph contracts;
* evidence links;
* evaluation datasets;
* component interfaces.

Those assets remain useful even when every provider and framework is replaced.
