# Financial Event Knowledge Graph Prototype

## Product Direction and Target Architecture

## 1. Purpose

The goal is to build a flexible prototype that transforms financial documents into an explorable knowledge graph.

The prototype should make it possible to:

* ingest annual reports, quarterly reports, current-event filings, earnings-call transcripts, press releases, and shareholder letters;
* extract companies, products, executives, technologies, financial metrics, business events, and relationships;
* connect every extracted fact or event to its original supporting passage;
* explore the graph visually;
* inspect related events before and after a selected event;
* identify clusters of related companies, products, technologies, and events;
* later ask natural-language questions about the graph;
* later generate short, understandable, evidence-backed investor posts.

The first objective is graph construction and exploration. Question answering and post generation will be added after the graph-building process is proven.

---

## 2. Design principles

### Prototype first

The system should optimize for:

* fast experimentation;
* easy local execution;
* clear component boundaries;
* simple debugging;
* visible intermediate outputs;
* replacing components without rewriting the full pipeline.

It does not initially need:

* production scalability;
* real-time ingestion;
* distributed processing;
* multi-user support;
* high availability;
* complex security;
* complete market coverage.

### Replaceable components

Every major capability should be defined through a protocol, abstract class, or stable interface.

Concrete tools should be implemented as adapters behind those interfaces.

For example:

```text
DocumentFetcher
    ├── ProviderAFetcher
    ├── ProviderBFetcher
    └── LocalFixtureFetcher
```

The rest of the system should depend on `DocumentFetcher`, not on a specific provider.

The same principle applies to:

* storage;
* document parsing;
* normalization;
* chunking;
* extraction;
* language models;
* embeddings;
* graph construction;
* graph persistence;
* retrieval;
* visualization APIs.

### Canonical internal models

External tools and providers should not pass their native output directly through the entire system.

The project should define its own canonical models for:

* source documents;
* normalized documents;
* passages;
* entities;
* events;
* relationships;
* evidence;
* graph mutations;
* extraction runs.

Each adapter converts between an external system and these internal models.

This protects the architecture from provider-specific formats.

### Explicit pipelines

The system should use explicit, independently runnable pipelines rather than one large application workflow.

Initial pipelines:

1. Data acquisition pipeline.
2. Graph-generation pipeline.
3. Graph inspection and export pipeline.

Later pipelines:

4. Graph question-answering pipeline.
5. Investor-post generation pipeline.
6. Incremental update pipeline.

Each pipeline should be runnable from a script or command-line entry point and should save its intermediate results.

---

## 3. High-level architecture

```text
External document sources
           │
           ▼
    Data acquisition pipeline
           │
           ▼
      Raw document storage
           │
           ▼
 Parsing and normalization layer
           │
           ▼
  Canonical normalized documents
           │
           ▼
     Graph-generation pipeline
           │
    ┌──────┴────────┐
    ▼               ▼
Extraction       Resolution
entities,        deduplication,
events, facts    normalization
    │               │
    └──────┬────────┘
           ▼
     Graph assembler
           │
           ▼
      Graph repository
           │
    ┌──────┴───────────┐
    ▼                  ▼
Graph inspection    Graph visualization
and validation      and exploration
```

Later:

```text
Graph repository
      │
      ├── Question retrieval
      │       └── Language model answer
      │
      └── Event/subgraph retrieval
              └── Investor-post generation
```

---

## 4. Data acquisition pipeline

The acquisition pipeline is responsible only for obtaining and normalizing documents. It should not contain graph-specific logic.

### Inputs

* company universe;
* requested document types;
* requested date range;
* fetch strategy;
* raw-storage strategy;
* normalization strategy.

### Processing stages

```text
Company manifest
      │
      ▼
Document discovery
      │
      ▼
Document download
      │
      ▼
Raw persistence
      │
      ▼
Parsing
      │
      ▼
Normalization
      │
      ▼
Normalized persistence
```

### Main interfaces

#### `DocumentSource`

Discovers available documents.

Responsibilities:

* search by company;
* search by document type;
* search by period or date;
* return document metadata;
* provide a stable external identifier.

#### `DocumentFetcher`

Downloads or retrieves the source material.

Responsibilities:

* fetch HTML, text, PDF, JSON, audio transcript, or structured data;
* apply rate limits and retry rules;
* return source bytes and metadata;
* avoid graph-specific processing.

#### `RawDocumentStore`

Stores the original downloaded material.

Possible strategies:

* local filesystem;
* object storage;
* remote file store;
* test fixture store.

#### `DocumentParser`

Converts raw source formats into parsed content.

Examples of responsibilities:

* HTML section extraction;
* PDF text extraction;
* table extraction;
* transcript speaker separation;
* heading detection;
* metadata recovery.

#### `DocumentNormalizer`

Converts parsed output into the project's canonical normalized format.

The normalized representation should be independent of the original provider.

#### `NormalizedDocumentStore`

Stores normalized documents.

The first implementation can use local files. A future implementation may use remote object storage, a document database, or another durable store.

---

## 5. Canonical document model

A normalized document should contain:

```text
NormalizedDocument
- document_id
- external_id
- company_id
- ticker
- document_type
- publication_date
- reporting_period
- source_url
- source_metadata
- sections[]
- raw_storage_reference
- parser_name
- parser_version
- content_hash
```

Each section contains passages:

```text
Passage
- passage_id
- document_id
- section_id
- section_title
- sequence_number
- speaker
- text
- table_context
- page_number
- character_offsets
- metadata
```

This model allows graph extraction to work identically regardless of whether the passage originated from:

* an annual report;
* a quarterly report;
* an event filing;
* an earnings-call transcript;
* a press release;
* a shareholder letter.

---

## 6. Graph-generation pipeline

The graph-generation pipeline reads only canonical normalized data.

It should not know how the documents were originally downloaded.

### Inputs

* normalized-document loader;
* chunking strategy;
* extraction strategy;
* language-model strategy;
* ontology/schema strategy;
* entity-resolution strategy;
* graph-assembly strategy;
* graph repository.

### Processing stages

```text
Load normalized documents
          │
          ▼
Select and prepare passages
          │
          ▼
Extract candidate entities/events/relations
          │
          ▼
Validate structured output
          │
          ▼
Normalize and resolve identities
          │
          ▼
Link claims to evidence
          │
          ▼
Construct graph mutations
          │
          ▼
Persist graph
```

### Main interfaces

#### `NormalizedDocumentLoader`

Loads normalized documents from a storage strategy.

Initial strategy:

* local normalized files.

Future strategies:

* cloud object storage;
* document database;
* remote API.

#### `PassageSelectionStrategy`

Decides which content should be processed.

Examples:

* process every passage;
* process selected filing sections;
* prioritize earnings and guidance sections;
* ignore boilerplate;
* combine consecutive transcript turns.

#### `ExtractionStrategy`

Defines how knowledge is extracted.

Possible implementations:

* single-pass entity and relationship extraction;
* separate entity, event, and relation extraction;
* schema-guided extraction;
* multi-pass extraction;
* reflection and validation extraction;
* rule-assisted extraction;
* imported prebuilt graph data.

The pipeline should depend on an extraction interface, not on a specific extraction methodology.

#### `LanguageModel`

Provides structured model inference.

Responsibilities:

* receive prompts and structured-output schemas;
* execute generation;
* expose model capabilities;
* report usage and latency;
* remain independent from graph logic.

#### `LanguageModelFactory`

Creates a configured language-model implementation based on runtime settings.

Example:

```text
LanguageModelFactory.create(
    provider=config.provider,
    model=config.model,
    purpose="event_extraction"
)
```

Different model instances may be selected for:

* entity extraction;
* event extraction;
* relationship extraction;
* entity resolution;
* summarization;
* post generation.

#### `OntologyProvider`

Defines the allowed domain model.

Responsibilities:

* node types;
* relationship types;
* required properties;
* valid source-target combinations;
* event categories;
* validation rules;
* prompt descriptions and examples.

The ontology should be versioned so experiments can compare different graph designs.

#### `EntityResolver`

Maps candidate mentions to canonical entities.

Responsibilities:

* aliases;
* ticker/company matching;
* product-name normalization;
* executive-name normalization;
* duplicate detection;
* confidence scoring.

#### `GraphAssembler`

Converts validated extraction results into canonical graph mutations.

It should not directly depend on the selected graph database.

#### `GraphRepository`

Persists and queries graph data.

Responsibilities:

* create or update nodes;
* create or update relationships;
* store node and edge properties;
* connect evidence;
* retrieve neighborhoods and paths;
* export the graph;
* delete or rebuild an extraction run.

Possible implementations:

* local graph database;
* embedded graph engine;
* file-backed graph;
* hosted graph database.

---

## 7. Canonical graph model

The initial graph should contain two connected layers.

### Stable knowledge layer

Relatively persistent entities:

* companies;
* products;
* technologies;
* executives;
* business segments;
* industries;
* markets;
* customers;
* suppliers;
* competitors;
* regulations.

### Event layer

Time-sensitive developments:

* earnings results;
* guidance changes;
* product launches;
* product delays;
* customer wins or losses;
* partnerships;
* acquisitions;
* capacity expansions;
* supply constraints;
* regulatory events;
* management changes;
* strategy changes;
* risk disclosures.

### Evidence layer

Every extracted claim should connect to its origin:

```text
Entity or Event
      │
      └── SUPPORTED_BY
                │
             Passage
                │
             PART_OF
                │
             Document
```

### Event-to-event links

Potential relations include:

* `PRECEDES`;
* `FOLLOWS`;
* `CONTRADICTS`;
* `UPDATES`;
* `RESPONDS_TO`;
* `DEVELOPS_INTO`;
* `POSSIBLY_CONTRIBUTES_TO`;
* `SHARES_DRIVER_WITH`.

Explicit source statements and inferred relationships must be distinguishable.

---

## 8. Intermediate artifacts

Every stage should save inspectable outputs.

Examples:

```text
data/
  raw/
  parsed/
  normalized/
  extraction/
  resolution/
  graph_mutations/
  graph_exports/
  evaluations/
```

Each extraction run should record:

* run ID;
* input documents;
* ontology version;
* prompt version;
* extraction-strategy version;
* model configuration;
* timestamp;
* validation failures;
* processing duration;
* resulting node and edge counts.

This allows experiments to be reproduced and compared.

---

## 9. Configuration

Strategies should be selected through configuration rather than code edits.

Example:

```yaml
data_pipeline:
  source: source_adapter_a
  raw_store: local_files
  parser: parser_a
  normalizer: canonical_v1
  normalized_store: local_files

graph_pipeline:
  document_loader: local_files
  passage_selector: financial_sections_v1
  extraction_strategy: event_first_v1
  ontology: semiconductor_v1
  entity_resolver: alias_and_llm_v1
  graph_repository: local_graph_a

models:
  entity_extraction: model_profile_a
  event_extraction: model_profile_b
  resolution: model_profile_a
```

Changing a strategy should not require changes in pipeline orchestration.

---

## 10. Initial user experience

The initial prototype should provide:

* an interactive graph;
* colors and icons by entity type;
* search by company, product, executive, and event;
* filters by date, company, node type, relationship type, and document type;
* cluster visualization;
* node expansion;
* event timelines;
* source-document links;
* exact supporting passages;
* explicit versus inferred labels.

The first version does not need a sophisticated chat system.

A later version will add:

* natural-language graph questions;
* evidence-path retrieval;
* answers with citations;
* investor-post generation from a selected event or subgraph.

---

## 11. Initial implementation sequence

### Phase 1 — Contracts and canonical models

Define:

* interfaces;
* canonical documents;
* canonical extraction outputs;
* canonical graph mutations;
* configuration;
* run manifests.

### Phase 2 — Data acquisition

Implement:

* one source adapter;
* local raw storage;
* one parser;
* canonical normalization;
* local normalized storage.

### Phase 3 — Graph extraction

Implement:

* local normalized-data loader;
* one ontology;
* one extraction strategy;
* model factory;
* validation;
* entity resolution;
* graph assembly.

### Phase 4 — Graph persistence and inspection

Implement:

* one local graph repository;
* graph export;
* basic inspection queries;
* extraction-run comparison.

### Phase 5 — Visualization

Implement:

* graph canvas;
* filters;
* node details;
* evidence panel;
* event timeline;
* clusters.

### Phase 6 — Retrieval and generation

Later add:

* graph-question interface;
* evidence-path retrieval;
* response grounding;
* investor-post generation.

---

## 12. Success criteria

The architecture is successful when:

1. A document provider can be replaced without changing graph extraction.
2. Local storage can be replaced with remote storage through another adapter.
3. A parser can be replaced without changing the normalized model.
4. A language model can be replaced through configuration.
5. An extraction approach can be replaced without changing the graph repository.
6. The ontology can be versioned and exchanged.
7. The graph backend can be replaced without changing upstream extraction.
8. Each graph fact links to its source passage.
9. Intermediate outputs are easy to inspect.
10. The full graph can be rebuilt from stored normalized documents.
