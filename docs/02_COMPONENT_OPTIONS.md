# COMPONENT_OPTIONS.md

## Candidate Tools, Projects, and Their Roles

This document lists candidate implementations that may fill the interfaces defined in the provider-independent architecture.

The purpose is not to select every component immediately. It is to identify what can be reused, what requires adaptation, and where each project belongs.

---

# 1. Source acquisition and financial parsing

## EdgarTools

### What it does

A Python library for retrieving and working with public company filings, filing metadata, exhibits, XBRL statements, ownership forms, and other regulatory documents.

### Potential role

It can implement:

* `DocumentSource`;
* `DocumentFetcher`;
* parts of `DocumentParser`;
* company and filing metadata resolution.

### Advantages

* financial-domain-specific;
* useful high-level Python API;
* handles multiple filing types;
* can retrieve exhibits and structured financial information;
* reduces the amount of custom regulatory-source integration.

### Limitations

* it does not build the knowledge graph;
* filings still require normalization into our canonical document format;
* earnings-call transcripts require another source;
* complex layout and table preservation may need additional parsing.

### Fit

Strong first choice for the initial filing acquisition adapter.

---

## sec-parser

### What it does

Parses regulatory HTML filings into semantically structured elements such as headings, paragraphs, tables, and sections.

### Potential role

It can implement part of `DocumentParser`.

### Advantages

* preserves more filing structure than plain text conversion;
* useful for exact section and passage provenance;
* suitable for chunking filings by logical section.

### Limitations

* focused on parsing rather than discovery and downloading;
* may need to be combined with another fetcher;
* normalization and financial event extraction remain custom.

### Fit

Useful when the initial high-level parser loses too much document structure.

---

## General PDF and document parsers

### What they do

Convert PDFs and other file formats into text, Markdown, layout blocks, or structured representations.

### Potential role

They can implement `DocumentParser` for:

* investor presentations;
* shareholder letters;
* annual-report PDFs;
* press-release PDFs.

### Advantages

* support sources beyond regulatory HTML;
* some preserve tables, headings, and page coordinates.

### Limitations

* financial PDFs often contain complex layouts;
* extracted content can require substantial cleanup;
* parser output must still be normalized.

### Fit

Add only after regulatory HTML and transcript ingestion work.

---

# 2. Financial knowledge-extraction references

## FinKario

### What it does

FinKario is an open financial knowledge-graph construction project that separates relatively stable company attributes from evolving market and company events. Its public repository includes scripts for attribute schema extraction, attribute knowledge extraction, event extraction, triple extraction, entity refinement, graph combination, and graph-based retrieval.

### Potential role

It is most useful as a reference or source of implementation ideas for:

* `ExtractionStrategy`;
* `OntologyProvider`;
* `EntityResolver`;
* event/attribute graph separation;
* graph-combination logic.

### Advantages

* finance-specific;
* has actual source code;
* explicitly models events separately from stable attributes;
* offers extraction and refinement stages;
* demonstrates a complete research pipeline rather than only a paper design.

### Limitations

* built around equity-research reports rather than US filings and transcripts;
* likely contains corpus-specific assumptions;
* research code rather than a polished reusable framework;
* the graph and retrieval implementation may be tightly coupled to its selected tools;
* requires careful review before code reuse.

### Fit

Best source of domain-specific extraction concepts. It should be inspected and selectively adapted, not treated as the entire application.

---

## FinReflectKG

### What it does

A financial graph-construction framework built from annual company filings. It uses table-aware parsing, schema-guided extraction, and several extraction modes, including single-pass, multi-pass, and reflection-based extraction. It also evaluates graph quality using rule checks, statistical validation, and model-based evaluation.

### Potential role

Reference design for:

* extraction strategies;
* graph-quality evaluation;
* reflection and retry workflows;
* schema compliance;
* benchmark construction.

### Advantages

* directly based on regulatory filings;
* strongly focused on extraction quality;
* supports multiple accuracy/cost tradeoffs;
* includes evaluation as part of the construction process.

### Limitations

* more complex than required for the first prototype;
* reflection workflows increase model calls and runtime;
* not a complete visual application;
* may require adaptation to event-centric rather than primarily annual-report knowledge.

### Fit

Useful for the second, more robust extraction combination.

---

# 3. Graph-construction engines

## Graphiti

### What it does

Graphiti is an open-source temporal knowledge-graph framework for incrementally extracting and maintaining entities and facts from episodes of information.

It supports developer-defined entity and edge types through typed models, configurable graph drivers, and hosted or local OpenAI-compatible language-model endpoints. It relies heavily on reliable structured output for extraction and deduplication.

### Potential role

It may implement large portions of:

* `ExtractionStrategy`;
* `EntityResolver`;
* temporal fact management;
* graph assembly;
* hybrid retrieval;
* provenance from facts to source episodes.

### Advantages

* temporal relationships;
* incremental updates;
* source episodes;
* custom entity and edge types;
* entity deduplication;
* hybrid graph and semantic search;
* compatible with several graph backends;
* replaceable model client.

### Limitations

* not a complete visualization application;
* imposes its own conceptual model;
* local models must reliably produce structured output;
* financial events still require a custom ontology;
* exact document/passages and application-level metadata require design work;
* replacing Graphiti later may be harder if its internal types leak into the rest of the code.

### Fit

Strong candidate for a graph-building adapter, provided it remains behind our own `GraphBuilder` and `GraphRepository` interfaces.

---

## LightRAG

### What it does

LightRAG builds entity-relation graphs from documents and combines graph retrieval with vector retrieval. It supports a local server, a Web UI, local-model integration, and multiple graph-storage implementations, including a default NetworkX-based option and graph-database adapters.

### Potential role

It may implement:

* a simple `ExtractionStrategy`;
* graph-aware retrieval;
* local graph persistence;
* an early document and graph exploration interface.

### Advantages

* easy local experimentation;
* lightweight default storage;
* existing Web UI;
* local-model integration;
* several retrieval modes;
* useful baseline for comparing generic graph extraction.

### Limitations

* generic entity-and-relation model;
* weaker domain ontology and temporal semantics;
* less natural fit for rich event properties;
* evidence and exact passage modeling require extensions;
* likely to produce noisy concept graphs from long financial documents.

### Fit

Good baseline and fast experiment, but not the strongest canonical financial graph builder.

---

## Neo4j LLM Knowledge Graph Builder

### What it does

An open-source application that transforms uploaded documents and web sources into a graph, stores it in Neo4j, and provides graph visualization and graph/vector question-answering. It can run locally and includes configuration for multiple language-model providers, including local-model setups.

### Potential role

It may provide:

* a reference frontend;
* a working ingestion application;
* graph visualization;
* graph/vector retrieval comparisons;
* a rapid initial demo shell.

### Advantages

* relatively complete application;
* document upload;
* graph construction;
* graph visualization;
* chat;
* source-document nodes;
* broad model configuration.

### Limitations

* heavier application architecture;
* may be harder to separate into our desired interfaces;
* generic graph schema;
* event ontology and event timelines require substantial customization;
* the graph backend is fixed to its selected database;
* local-model integrations should be validated rather than assumed flawless.

### Fit

Useful as a UI and full-application reference, but likely not the cleanest architecture foundation for rapid component replacement.

---

# 4. Graph repositories

## Neo4j

### What it does

A property-graph database with typed node labels, typed relationships, arbitrary properties, indexes, graph queries, vector capabilities, and a broad tooling ecosystem.

### Potential role

It can implement `GraphRepository`.

### Advantages

* natural representation of entities, events, passages, and typed relationships;
* rich query language;
* good inspection and debugging tools;
* widespread integrations;
* appropriate for graph traversals and evidence paths;
* can run locally.

### Limitations

* requires a database process;
* adds schema, indexing, and operational concerns;
* replacing it requires maintaining a clean repository abstraction;
* some advanced tooling may have separate licensing or edition constraints.

### Fit

Best general-purpose graph backend when transparency, querying, and visual inspection are priorities.

---

## FalkorDB and embedded Falkor variants

### What they do

Graph-database implementations using Cypher-like querying and optimized graph operations. Some variants can be run with relatively little infrastructure.

### Potential role

They can implement `GraphRepository`, particularly for Graphiti-backed experiments.

### Advantages

* lighter local deployment options;
* graph query support;
* suitable for embedded or compact experiments;
* supported by some graph-construction frameworks.

### Limitations

* smaller ecosystem;
* fewer mature inspection and visualization tools;
* migration and compatibility should be tested;
* licensing and deployment characteristics require review for later product use.

### Fit

Strong lightweight option for the initial local spike.

---

## NetworkX or file-backed graphs

### What they do

Represent graph nodes and edges in Python memory and serialize them to local files.

### Potential role

They can implement an early `GraphRepository`.

### Advantages

* very lightweight;
* no database process;
* easy debugging;
* direct access to graph algorithms;
* simple graph export and experimentation.

### Limitations

* limited concurrent querying;
* weaker persistence and indexing;
* inefficient for large text properties;
* graph retrieval APIs must be built;
* not ideal for interactive application workloads at scale.

### Fit

Good for unit tests, small experiments, and validating ontology designs before adopting a database.

---

# 5. Visualization

## Cytoscape.js

### What it does

A browser graph-visualization library supporting graph layouts, styling, interaction, filtering, and extensions.

### Potential role

Frontend graph canvas.

### Advantages

* mature graph-specific API;
* suitable for node expansion and filtering;
* flexible styling;
* integrates with React through wrappers or custom components;
* supports graph algorithms and several layouts.

### Limitations

* requires custom application UI;
* timeline, evidence panel, and cluster UX must be implemented;
* very large graphs require careful rendering strategies.

### Fit

Strong default choice for a custom prototype UI.

---

## Sigma.js

### What it does

A WebGL-oriented graph renderer designed for efficient network visualization.

### Potential role

High-performance graph canvas.

### Advantages

* good rendering performance;
* visually attractive large-network displays;
* suitable for clusters and exploration.

### Limitations

* more application logic must be built around it;
* some advanced graph editing and layout features require extra libraries;
* evidence and timeline UX remain custom.

### Fit

Good when visual scale and smooth rendering matter more than graph-editing features.

---

## Existing framework UIs

LightRAG and the Neo4j graph-builder project include existing graph-oriented interfaces.

### Potential role

* temporary visualization;
* design reference;
* source of reusable components;
* early proof-of-concept interface.

### Limitation

Their UI models are coupled to their own backends and graph semantics.

### Fit

Use for early inspection, but keep the final visualization behind our own API.

---

# 6. Language-model layer

## Local OpenAI-compatible model servers

### What they do

Expose locally running models through a common chat and structured-generation API.

Graphiti explicitly supports local or hosted OpenAI-compatible endpoints, although it warns that structured extraction works best with models that reliably honor JSON schemas.

### Potential role

Implement `LanguageModel`.

### Advantages

* local privacy;
* no per-call external cost;
* easy model replacement through configuration;
* compatible with several frameworks.

### Limitations

* smaller models may be inconsistent with structured output;
* extraction quality must be measured;
* long financial documents require chunking;
* local inference may be slow;
* model-specific prompt tuning may be required.

### Fit

Strong for experimentation and later post generation. Extraction should be benchmarked against a stronger reference model.

---

## Hosted model APIs

### What they do

Provide larger models with generally stronger structured extraction and reasoning.

### Potential role

Alternative `LanguageModel` adapter or benchmark model.

### Advantages

* more reliable schema adherence;
* stronger event extraction;
* faster initial validation;
* useful for establishing an expected quality ceiling.

### Limitations

* external cost;
* privacy and data-transfer considerations;
* provider dependence;
* API and model changes.

### Fit

Useful as an optional extraction strategy and comparison baseline, not as an architectural dependency.

---

# 7. Embeddings

## Local embedding models

### What they do

Create vector representations for passages, entities, facts, and queries.

### Potential role

Implement `EmbeddingModel`.

### Advantages

* inexpensive local operation;
* stable;
* small storage footprint;
* replaceable;
* useful for hybrid graph and semantic retrieval.

### Limitations

* financial-domain retrieval quality should be evaluated;
* embedding dimensions and distance metrics affect storage compatibility;
* switching models generally requires re-embedding.

### Fit

Appropriate from the first prototype, provided embedding metadata is versioned.

---

# 8. Retrieval approaches

## Simple graph retrieval

Retrieve:

* selected node;
* direct neighbors;
* related events;
* source passages;
* limited paths.

### Fit

Best first implementation.

---

## Hybrid graph and vector retrieval

Combines:

* graph traversal;
* semantic similarity;
* full-text matching;
* optional reranking.

Graphiti and LightRAG both provide versions of hybrid retrieval.

### Fit

Useful after graph construction and evidence quality are validated.

---

## Rule-constrained evidence paths

Retrieve only paths matching financially meaningful patterns and ending at evidence passages.

### Fit

Later enhancement for explanations, graph questions, and post generation.

---

# 9. Recommended abstraction boundaries

Regardless of which projects are selected, their native objects should be hidden behind:

```text
DocumentSource
DocumentFetcher
RawDocumentStore
DocumentParser
DocumentNormalizer
NormalizedDocumentStore
NormalizedDocumentLoader
PassageSelectionStrategy
LanguageModel
LanguageModelFactory
EmbeddingModel
OntologyProvider
ExtractionStrategy
EntityResolver
GraphBuilder
GraphRepository
GraphRetriever
```

Framework-specific data should be converted at adapter boundaries.

This prevents a proof-of-concept choice from becoming the permanent architecture accidentally.
