# v1 — Document Normalization

**Status:** plan. Nothing implemented.
**Scope:** turn the acquired raw SEC corpus into a deterministic, evidence-preserving
normalized corpus that different graph-building and extraction strategies can consume.

Consumes the corpus built by
[../data-fetching/V0_OPENDOOR_FETCH.md](../data-fetching/V0_OPENDOOR_FETCH.md):
109 filings, 2,019 artifacts, 746 MB, verified.

```text
Raw SEC artifacts → selection → parsing → canonical normalization
                  → passage generation → normalized-corpus verification
```

**Out of scope, and enforced by a structural test:** graph construction, entity or event
extraction, ontology design, Graphiti, Neo4j, embeddings, vector search, RAG, question
answering, post generation, transcript or peer-company acquisition, OCR or vision
processing, XBRL interpretation.

The normalization layer must not import or assume any graph database, LLM provider, or
event ontology. Its output is the stable boundary: extraction strategies may be replaced
without reacquiring or reparsing SEC data.

Facts marked *(measured)* come from profiling all 377 narrative HTML artifacts in the
acquired corpus on 2026-07-31; the profiling code was a disposable spike, not planned
implementation.

---

# 1. What the corpus actually looks like

Everything in this plan follows from these measurements. They overturned three assumptions.

## 1.1 There are no semantic headings *(measured)*

**Zero `<h1>`–`<h4>` elements across all 377 narrative artifacts.** Only 73 use `<b>` or
`<strong>`, median 0. Headings exist only as styling:

```html
<span style="...font-weight:700...">Market Overview</span>
```

Heading detection therefore requires font-weight/size analysis. This single fact dominates
the parser choice (§4): any tool that relies on semantic markup will produce a flat
document, and flat documents destroy the section provenance the evidence layer needs.

## 1.2 Inline XBRL brings heavy hidden-content contamination *(measured)*

| Form | n | median text | median tables | median `ix:` tags | median `display:none` elements |
| --- | --- | --- | --- | --- | --- |
| 10-K | 6 | 481,318 | 82 | 1,684 | 5,575 |
| 10-Q | 19 | 183,908 | 59 | 1,006 | 4,655 |
| 8-K | 75 | 4,124 | 8 | 27 | 1 |
| DEF 14A | 7 | 250,502 | 283 | 137 | 0 |

The FY2025 10-K alone carries **7,245 hidden elements holding 27,772 characters**. Inline
XBRL appears **only in primary documents** (94 of 109); no exhibit contains any. Hidden
content must be dropped before text extraction or it pollutes every passage.

## 1.3 EX-99.2 is text-rich, not image-heavy — earlier claim withdrawn *(measured)*

The v0 plan and doc 04 recorded that EX-99.2 shareholder letters were "largely rendered as
images, so text yield is likely low", flagged unverified. **That was wrong.**

| Role | n | median text chars | median yield | median images |
| --- | --- | --- | --- | --- |
| ex99-02 | 26 | **53,079** | **81.8%** | 27 |
| ex99-03 | 22 | 11,966 | 77.3% | 7 |
| ex99-01 | 45 | 27,086 | 11.4% | 0 |
| primary | 109 | 5,876 | 13.4% | 0 |

EX-99.2 has the **highest text yield of any role in the corpus**. The JPGs are supplementary
charts sitting beside full narrative text, not substitutes for it. The Q4-2025 letter is
42 KB of HTML yielding 37,728 characters at 85.8%.

Consequence: EX-99.2 becomes a **high-value v1 input**, not a deferred image problem. OCR
and vision processing stay out of scope with more confidence than before, and doc 04 §11
must be corrected.

## 1.4 There is no duplicate-content problem *(measured)*

Across all 377 narrative artifacts: **zero byte-identical pairs and zero
normalized-text-identical pairs.** Comparing each earnings 8-K primary against its own
EX-99.1 (45 pairs), word overlap is 30–42%, and inspection shows that overlap is cover-page
boilerplate — registrant name, address, file number — not duplicated disclosure.

Consequence: v1 builds **no deduplication system**. It records content hashes so duplication
can be detected later if a second issuer or amended filings introduce it (§9).

## 1.5 Markup era is mixed, and legacy `<font>` persists *(measured)*

91 of 377 artifacts begin with an XML declaration (the inline-XBRL ones); 286 do not; 70
carry a DOCTYPE. Legacy `<font>` markup is not confined to old filings — the **2026** Q4
earnings release contains 1,417 `<font>` tags, and a 2021 DEF 14A contains 2,083.

**Gotcha to encode:** `lxml.html.fromstring` raises `ValueError: Unicode strings with
encoding declaration are not supported` when handed a `str` for the 91 XML-declared files.
Parsers must read **bytes**.

## 1.6 Tables carry content that text extraction alone loses *(measured)*

6,517 tables across the artifacts v1 would select. The extreme case:

- **EX-21.1 subsidiary list**: 1 table, 68 rows, **511 characters of text**. Flatten the
  table and the document is effectively empty. This is the strongest argument against
  text-only normalization.
- **DEF 14A 2026**: 375 tables, most of them layout scaffolding rather than data.
- **EX-3.1 charter**: 323 tables; **EX-10.12**: 137 tables in a 60 KB agreement.

Layout tables and data tables must be distinguished, and table structure must survive.

## 1.7 Naive text extraction corrupts words *(measured)*

`lxml`'s `text_content()` concatenates adjacent inline elements without separators,
producing tokens like `ASSETSFor`, `ActivitiesNet`, `BackgroundOpendoor`. 238 such
artifacts appear in the FY2025 10-K alone. Any custom extractor must insert block
separators deliberately; this is a correctness requirement, not cosmetics.

---

# 2. Selection outcome under the proposed policy *(measured)*

Applying §3's policy to all 2,019 acquired artifacts:

| Outcome | Count |
| --- | --- |
| **selected** | **294** |
| excluded — asset (image) | 1,060 |
| excluded — xbrl | 364 |
| excluded — index_header | 109 |
| excluded — full_submission | 109 |
| excluded — boilerplate role (EX-31.x/32.x/23.1) | 83 |

Selected corpus: **15,195,663 characters**, **6,517 tables**, text per document
min 511 / p50 15,618 / p90 162,729 / max 572,060. Rough passage estimate at ~1,500
characters: **~10,000 passages**.

By form: 8-K 203, 10-Q 44, 10-K 34, DEF 14A 7, 8-K/A 6.
By role: primary 109, ex99-01 45, ex99-02 26, ex99-03 22, ex10-01 15, ex21-01 7, ex4-01 4.

---

# 3. Architecture

Same principles as acquisition, same shapes, so the two packages read alike.

```text
normalization/
├── cli.py            argparse, rendering, exit codes
├── contracts.py      PipelineStage protocol (re-used shape)
├── context.py        composition root
├── pipeline.py       stage ordering and abort semantics
├── core/
│   ├── models.py     canonical normalized models (NORMALIZED_MODELS.md)
│   ├── identity.py   deterministic document/section/block/passage IDs
│   ├── config.py     selection policy, parser, passage strategy, versions
│   ├── storage.py    atomic finalization for normalized documents
│   ├── manifests.py  immutable run manifests
│   └── runmeta.py    run provenance
├── utils/
│   └── text.py       whitespace normalization, char counting
└── stages/
    ├── select/    public.py  catalog_selection.py
    ├── parse/     public.py  sec_html_parser.py  lxml_parser.py  tables.py
    ├── normalize/ public.py  canonical_normalizer.py
    ├── passages/  public.py  section_passages.py
    ├── catalog/   public.py  jsonl_catalog.py
    └── verify/    public.py  corpus_verification.py
```

Five files inside `parse/` because parsing is where the real work is: two parser
implementations behind one protocol, plus table handling as a genuinely separate concern
(§6). Every other stage gets `public.py` plus one implementation — the same proportionality
rule the acquisition package follows.

Dependency direction, enforced by a structural test as in acquisition:

```text
core, utils, contracts  ←  stages  ←  pipeline  ←  context  ←  cli
```

Normalization reads the acquisition catalogs and raw files, and imports **nothing** from
the `acquisition` package. The interface between the two is the on-disk catalog plus the
raw bytes — that is what makes each replaceable.

---

# 4. Parsing: recommendation and evidence

## 4.1 Options evaluated

| Option | Verdict |
| --- | --- |
| **sec-parser 0.58.1** | **Recommended default** — see below |
| **lxml** direct | Recommended **fallback** and second implementation |
| BeautifulSoup + html5lib | Rejected: 10–40× slower on a 2.8 MB 10-K, no heading inference |
| trafilatura | Rejected: built for news boilerplate removal; discards filing tables and structure |
| docling | Rejected for v1: heavyweight, PDF-oriented, unnecessary for HTML-only input |
| markdownify / html-to-markdown | Rejected: flattens to Markdown with no block identity or locators |
| inscriptis | Rejected: layout-faithful plain text, but no blocks, no tables, no locators |

## 4.2 sec-parser spike results *(measured)*

Ran `Edgar10QParser().parse()` over representative artifacts:

| Document | raw text | text minus hidden | sec-parser text | coverage | elements | empty% | tables |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10-K primary | 543,609 | 515,837 | 496,472 | 96% | 925 | 2% | 68 |
| 10-Q primary | 171,368 | 156,814 | 145,503 | 93% | 433 | 3% | 43 |
| 8-K primary | 4,773 | 4,505 | 4,724 | 105% | 34 | 0% | 4 |
| EX-99.1 | 37,492 | 37,492 | 37,607 | 100% | 131 | 45% | 9 |
| EX-99.2 | 37,728 | 37,728 | 38,178 | 101% | 96 | 54% | 0 |
| DEF 14A | 339,636 | 339,636 | 342,155 | 101% | 1,347 | 47% | 2 |

It parsed **all six without error**, at 0.4 s for a 2.8 MB 10-K. It classified 337
`TitleElement`s in the 10-K with plausible text (`Mission`, `Our Company`,
`Market Overview`). Coverage above 100% reflects sec-parser inserting the separators §1.7
says are required — a point in its favour, not a defect.

**Strengths that matter here:** style-based title detection (the §1.1 problem), automatic
`display:none` filtering (the §1.2 problem), `html_tag.table_to_markdown()`,
`get_approx_table_metrics()`, `is_table_of_content()`, and `get_source_code()` for
fragment-level provenance.

**Weaknesses, stated plainly:**

1. Last PyPI release **2024-06-09**; repository commits continue but nothing ships.
2. Ships **only** `Edgar10QParser` — there is no 10-K parser. Top-section detection is
   10-Q-shaped and found just 7 `TopSectionTitle` in a 10-K.
3. `EmptyElement` noise reaches 45–54% on exhibits and DEF 14A.
4. No character offsets into the source file.
5. 4–7% text divergence on the largest documents, direction not fully attributed.

## 4.3 Recommendation

**Use sec-parser for block segmentation and heading classification only; derive section
hierarchy ourselves.**

This takes its genuine strength (style-based title detection, which nothing else in the
Python ecosystem does for SEC HTML) and sidesteps its worst weakness (no 10-K parser, weak
top-section detection). Our normalizer builds the section tree from the ordered sequence of
title elements and their levels, which works identically for 10-K, 10-Q, 8-K, DEF 14A, and
exhibits — a uniform rule rather than a form-specific one.

`EmptyElement`s are dropped at the adapter boundary. sec-parser types never leave
`sec_html_parser.py`.

**`LxmlDocumentParser` is built in the same milestone**, not deferred. Two reasons: it is
the fallback when sec-parser raises, and having a second implementation from day one is the
only way to know the parser protocol is actually a seam rather than a sec-parser-shaped
hole. It uses explicit block separators (§1.7) and its own font-weight heuristic.

Pin `sec-parser==0.58.1`. If it rots, the protocol and the second implementation are
already in place.

## 4.4 Parser contract

```python
class DocumentParser(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str: ...
    def supports(self, artifact: SelectedArtifact) -> bool: ...
    def parse(self, artifact: SelectedArtifact) -> ParsedDocument: ...
```

## 4.5 Parser modes and fallback triggers

Two parsers with a vague "fallback when it fails" rule would make output
non-deterministic in a way nobody could reason about. Three explicit modes:

| Mode | Behaviour |
| --- | --- |
| `normal` | Default parser only. The fallback runs **only** on a hard failure below |
| `fallback` | Fallback parser only, for a named artifact set. Used to reproduce or compare |
| `comparison` | Both parsers run over the same inputs; outputs are diffed and reported. Never used for the authoritative corpus |

**Hard failure — the only conditions that trigger fallback in `normal` mode:**

1. the parser raises;
2. it returns zero blocks;
3. it returns zero blocks carrying text;
4. `source_content_sha256` does not match the acquisition catalog;
5. canonical validation of its output fails;
6. extracted text is below a configured fraction of the document's text (default 60%),
   measured against a plain lxml extraction of the same bytes;
7. **`TABLE_CONTENT_LOSS`** — genuine table-related content loss, added after the spike.

**The table-content-loss trigger requires all three conditions**, because a low detection
ratio on its own is normal: SEC filings use tables for page layout constantly and that text
usually survives as prose.

```text
source has >= 20 tables                    (min_source_tables)
AND parser recognized <= 10% of them       (max_detected_ratio)
AND sampled source-table cells are missing (probe_coverage < 0.60)
    from the parsed output
-> fall back to LxmlDocumentParser
```

Probes are the first cell of at least 18 characters from each table in document order —
deterministic, because a random sample would make the fallback decision and therefore the
corpus non-reproducible. Thresholds measured across the 18 spike fixtures:

| Fixture | source tables | detected ratio | probe coverage | outcome |
| --- | --- | --- | --- | --- |
| EX-10.12 | 137 | 0.000 | **0.50** | **falls back** |
| DEF 14A 2026 | 375 | 0.005 | 1.00 | no — content survives as prose |
| EX-3.1 | 323 | 0.015 | 1.00 | no — content survives as prose |
| FY2025 10-K | 83 | 0.807 | 0.38 | no — spared by the ratio condition |

The selected parser, the parser it replaced, and the reason are recorded on the document
(`parser_name`, `parser_fallback_from`, `parser_fallback_reason`), carried into
`documents.jsonl`, and summarized in the corpus report. One authoritative parser output per
document; outputs are never merged.

**Weak hierarchy is not a hard failure.** A document that parses but yields a shallow or
uncertain section tree gets the `hierarchy_uncertain` flag and a warning for review. It does
**not** silently switch parsers — a parser swap driven by a soft quality signal would make
the corpus a mixture nobody could explain.

Every fallback is recorded: the document carries the parser that actually produced it, and
the run manifest lists `fallback_document_ids` with the triggering condition.

Deferred formats, reported explicitly rather than silently skipped: PDF (none in the
corpus), plain text, images, XBRL XML.

## 4.6 Defensive heading and section inference

§1.1 means the section tree is *inferred*, and §17.2 names that as the central risk. The
normalizer therefore treats inference as fallible by construction:

- **Reject heading candidates inside data tables.** A styled cell in a financial table is
  not a section heading. Candidates inside a table are demoted to normal blocks unless the
  table is classified `layout` and the candidate is its only text.
- **Prefer known filing labels over styling.** `Part I`, `Part II`, `Item 1`,
  `Item 1A`, `Item 7`, `Risk Factors`, `Management's Discussion` and their siblings are
  matched by pattern and take precedence over any font-weight signal. These are the real
  semantic anchors in 10-K and 10-Q, and they are stable across every markup era in the
  corpus. Recorded as `heading_source: filing_label`.
- **Normalize impossible level jumps.** A level-1 heading followed by level-4 collapses to
  level-2; depth never increases by more than one step.
- **Record confidence per heading** (`heading_confidence`), derived from how many
  independent signals agree — filing label, font weight, font size, position.
- **Degrade honestly.** When confidence is low across a document, emit a flat structure
  under a single synthetic root rather than an invented hierarchy, and flag
  `hierarchy_uncertain`. A shallow true structure is more useful to extraction than a deep
  false one.
- **Always emit a synthetic root** so every block belongs to a section, including documents
  with no detectable headings at all.
- **Report** every document flagged `hierarchy_uncertain` in the corpus report and in the
  manual-review artifact.

---

# 5. Provenance and source traceability

The evidence panel must eventually show: the exact passage, its section title, the filing,
the exhibit, and a link to the source. That is the requirement; byte offsets are not.

**Byte offsets are not promised.** sec-parser cannot supply them, and reconstructing them
across an HTML rewrite would be unreliable. The locator recorded instead:

| Component | Source | Purpose |
| --- | --- | --- |
| `artifact_id` | acquisition catalog | ties to raw bytes and `source_url` |
| `block_sequence` | parse order, 0-based | stable ordering and addressing |
| `tag_name` | parser element | coarse type check |
| `fragment_sha256` | `get_source_code()` of the element | detects source drift; re-locates the block if HTML is re-parsed |
| `heading_path` | ancestry of section titles | human-readable location |
| `char_start` / `char_end` | offsets **within the block's own text** | exact quoting inside a block |
| `table_index` | position among tables | addresses tables directly |

`char_start`/`char_end` are offsets into the normalized block text, never into the source
file. That is honest and sufficient: quoting a passage exactly requires locating it within
its block, not within the original HTML.

`source_url` and `original_filename` come straight from the acquisition catalog, so every
passage resolves to a live EDGAR URL without normalization storing one.

---

# 6. Table handling

Preservation and usability, not semantic interpretation. No metric extraction in v1.

**One authoritative representation, two derived renderings.**

1. **Structured rows** — `rows: list[list[str]]`, plus `header_rows`, `n_rows`, `n_cols`,
   and a merged-cell note. **This is authoritative.**
2. **Markdown** — a pure deterministic function of `rows`, for LLM extraction and review.
3. **Plain text** — a pure deterministic function of `rows`, for text accounting.

Storing three representations without naming one authoritative invites silent drift.
Verification therefore recomputes the Markdown and plain-text renderings from `rows` and
compares them to the stored values (§13 check 16). A table passage references `table_id` and
carries exactly one rendering as its text.

**Layout versus data tables — conservative by default.** DEF 14A carries 375 tables that are
mostly page scaffolding, but a legal or narrative table is not scaffolding merely because it
holds no digits. Classification records `table_kind: data | layout | mixed | unknown` with a
`kind_confidence`, and:

- **`unknown` is the default whenever the heuristic is not confident.** Guessing is worse
  than admitting uncertainty.
- Classification **never removes a table** from normalized content, and never changes how
  the table is stored. It is metadata for downstream consumers, nothing more.
- `mixed` covers a table holding both a data-like region and prose-only regions at the same
  level. It is provisional: if the spike never produces one, drop it.
- The spike **must review classification explicitly** on EX-21.1 (fixture 15), both proxies
  (13, 14), EX-10.12 (17) and EX-3.1 (18) — the four cases where a wrong call would do the
  most damage.

The EX-21.1 case is the reason for all of this caution: 511 characters of text and 68 table
rows, so any rule that discards a class of table empties the document.

Also planned: caption capture from the preceding title block; surrounding context via
`heading_path`; inline-XBRL cell values kept as displayed text (not interpreted — §10);
continuation tables left unmerged in v1 and flagged; very wide tables (>12 columns)
flagged `wide_table` for review rather than reshaped.

---

# 7. Passage generation

## 7.1 Strategies compared

| Strategy | Assessment |
| --- | --- |
| Fixed-size overlapping chunks | Rejected for v1. Overlap duplicates evidence, so the same sentence appears in two passages with two IDs — corrosive for an evidence-linked graph. Ignores the section structure §1.1 costs real effort to recover. |
| **Section-aware, structure-preserving** | **Recommended.** Uses the hierarchy the parser recovers, keeps passages inside one section, carries heading context. |
| Semantic/embedding-based grouping | Rejected for v1. Requires an embedding model, making normalization depend on a provider — the exact coupling this layer exists to prevent. |

## 7.2 Recommended rules

- Never cross a section boundary.
- Target **1,500 characters**, hard maximum **4,000**.
- Merge consecutive short blocks within a section until the target is reached.
- Split a single over-long block on sentence boundaries; never mid-word.
- **No overlap, stated precisely.** *Every passage-eligible block is assigned to exactly
  one passage, unless the versioned passage policy explicitly excludes it, and every
  exclusion records a reason.* The stronger phrasing — "every character belongs to exactly
  one passage" — is false and would fail its own verification: page headers, page numbers,
  empty blocks and heading blocks are deliberately not passage content. Headings become
  passage *metadata* via `heading_path`, not passage text. Exclusion reasons:
  `PAGE_FURNITURE`, `EMPTY_BLOCK`, `HEADING_AS_METADATA`, `POLICY_EXCLUDED`.
- Each passage carries `heading_path` as context without embedding it in the text.
- **Tables become their own passage**, carrying the Markdown rendering and a link to the
  `TableBlock`. A financial table split across passages is unreadable.
- Footnotes and list items stay with their parent block.
- Passages below 200 characters that cannot be merged are kept and flagged `short_passage`.

```python
class PassageStrategy(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str: ...
    def build(self, document: NormalizedDocument) -> list[Passage]: ...
```

Estimated output: **~10,000 passages** over the 294 selected artifacts.

---

# 8. Selection stage

Policy is data, not code: a versioned rule list in `config/selection_policy.yaml`, matched
against acquisition-catalog metadata. Filenames are never used.

Rules match on `artifact_kind`, `role`, `exhibit_type`, `form`, `items`, `media_type`,
`description`, `low_processing_priority`, `size_bytes`, and a measured `text_chars`.

```yaml
version: v1
default: exclude
rules:
  - id: primary-narrative
    when: {artifact_kind: primary, media_type: text/html}
    decision: include
    reason: PRIMARY_NARRATIVE
  - id: earnings-and-letters
    when: {role: [ex99-01, ex99-02, ex99-03]}
    decision: include
    reason: EARNINGS_MATERIAL
  - id: certifications
    when: {role: [ex31-01, ex31-02, ex32-01, ex32-02, ex23-01]}
    decision: exclude
    reason: BOILERPLATE_CERTIFICATION
  - id: text-floor
    when: {max_text_chars: 400, max_tables: 0}
    decision: exclude
    reason: BELOW_TEXT_FLOOR
```

**EX-10.* needs more than a role match.** The EX-10 range covers everything from an
executive offer letter to a credit agreement to a routine lease, and the role slug alone
(`ex10-01`, `ex10-38`) says nothing about which. Rules for this range match on the SGML
`description` and the filing's item codes as well:

```yaml
  - id: agreements-leadership
    when: {role_prefix: ex10-, description_matches: ["offer letter", "employment",
           "separation", "transition", "indemnif"]}
    decision: include
    reason: MATERIAL_AGREEMENT
  - id: agreements-capital
    when: {role_prefix: ex10-, items_any: ["1.01", "2.03", "3.02"]}
    decision: include
    reason: MATERIAL_AGREEMENT
  - id: agreements-unclassified
    when: {role_prefix: ex10-}
    decision: needs_review
    reason: NEEDS_REVIEW
```

An unclassified agreement becomes `NEEDS_REVIEW` rather than being quietly included or
quietly dropped — the surface where an unfamiliar exhibit type is meant to appear.

Every artifact gets an outcome with a **reason code** — nothing is silently dropped.
Reason codes: `PRIMARY_NARRATIVE`, `EARNINGS_MATERIAL`, `MATERIAL_AGREEMENT`,
`GOVERNANCE_DOCUMENT`, `STRUCTURED_EXHIBIT`, `BOILERPLATE_CERTIFICATION`,
`NON_NARRATIVE_MEDIA`, `XBRL_LANE`, `ARCHIVAL_ONLY`, `BELOW_TEXT_FLOOR`,
`NEEDS_REVIEW`.

`NEEDS_REVIEW` covers artifacts matching no rule, and unclassified EX-10 agreements.

**Correction, made during implementation *(measured 2026-07-31)*.** An earlier draft said
`NEEDS_REVIEW` artifacts were "excluded from processing but listed in the report". Two
measurements against the real corpus overturned that:

- **SEC exhibit descriptions carry no information.** Every one of them is the exhibit
  number — `"EXHIBIT 10.12"`, `"ex-10.1"`. Zero informative descriptions across all
  exhibits in the corpus. The `description_matches` rule above can therefore never fire on
  this issuer's filings.
- **Item codes classify only 24 of 64 EX-10 artifacts.** Forty carry no usable item signal;
  fixture 17's filing, a material agreement, has items `8.01, 9.01`.

Excluding them from processing would silently drop 40 material contracts — exactly the
documents an event graph wants. So `NEEDS_REVIEW` now means **normalize and flag**: the
artifact is processed, its document carries the `needs_review` flag, and it is listed first
in the corpus report. Only `exclude` prevents processing.

Processed corpus is therefore 234 `include` + 60 `needs_review` = **294**, matching §2.

---

# 9. Duplicate and repeated content

Given §1.4, v1 **detects and records, and builds nothing more**:

- `content_sha256` on every normalized document (normalized text, lowercased,
  whitespace-collapsed).
- `block_sha256` on every block.
- A verification check reporting any collision.

Legitimate repeated disclosure — risk factors recurring across years, boilerplate cover
pages — is expected and is **not** treated as a defect. Nothing is deleted; provenance is
preserved for every copy. Near-duplicate detection is deferred until evidence demands it,
which the current corpus does not provide.

---

# 10. Image-heavy documents and the XBRL boundary

**Image-heavy.** §1.3 removes the urgency but not the rule. Flag
`requires_image_processing` when `text_chars < 500` **and** `images > 0`, or when
`text_chars / images < 200`. No artifact in the current corpus trips the first condition;
EX-99.3 (2,208 chars, 5 images) approaches the second and is a useful calibration case. A
deferred `ImageProcessor` protocol is declared and left unimplemented. The decision to add
OCR or a vision model comes later, from measured missing content — not from assumption.

**XBRL.** Narrative normalization and structured facts stay in separate lanes. v1
**preserves the link** from a normalized document to its filing's XBRL artifacts
(`related_xbrl_artifact_ids`) and does not interpret them. Inline-XBRL values inside
narrative text are kept as displayed text and are **not** parsed into typed facts.

A future `structured_facts` pipeline would produce `StructuredFact` records
(company, concept, value, unit, period, dimensions, filing, source artifact) from the
`EX-101.*` linkbases and inline markup. **Not part of this plan.** The rule it protects:
no language model should be asked to read a number that is already tagged.

---

# 11. Storage layout

```text
config/
├── selection_policy.yaml          # TRACKED, versioned
└── normalization.yaml             # TRACKED — parser, passage strategy, thresholds

normalization_manifests/           # TRACKED, immutable, one per run
├── <run_id>.json                  # AUTHORITATIVE run manifest
└── <run_id>-selection.json        # AUTHORITATIVE selection outcome, all 2,019

data/
├── normalized/sec/<cik10>/<form>/<date>_<accession>/
│   ├── <document_id>.json         # AUTHORITATIVE document + sections + blocks
│   └── <document_id>.passages.jsonl   # AUTHORITATIVE
├── normalization_runs/
│   ├── <run_id>.json              # run outcome
│   └── <run_id>-issues.jsonl      # AUTHORITATIVE issues, incl. parse failures
├── normalization_catalog/         # DERIVED, rebuildable
│   ├── documents.jsonl
│   ├── passages.jsonl
│   ├── selection.jsonl
│   └── issues.jsonl
└── normalization_reports/<run_id>-corpus.md
```

**Authoritative versus derived, corrected.** An earlier draft called every catalog derived
and every per-document file authoritative. That does not hold: a selection record exists for
artifacts that were **excluded** and therefore have no document, and an issue record exists
for artifacts whose parse **failed** and therefore have no document either. Neither can be
rebuilt from per-document files.

| Authoritative | Derived |
| --- | --- |
| selection manifest (per run, immutable) | `selection.jsonl` |
| normalized document JSON | `documents.jsonl` |
| per-document passages JSONL | `passages.jsonl` |
| per-run issues JSONL | `issues.jsonl` |
| run manifest | corpus reports |

The selection manifest is immutable per run, exactly like acquisition's filing manifest —
the same pattern, for the same reason.

**Format decision: one JSON per document, plus per-document passages JSONL, plus derived
JSONL catalogs.** Rejected alternatives: Parquet (not inspectable by eye, and the schema is
still moving); SQLite/DuckDB as the authoritative store (a binary blob is a poor
authoritative format for a corpus meant to be diffed and rebuilt). DuckDB may be added
later *over* the JSONL, exactly as acquisition planned — a query convenience, never the
source of truth.

Mirrors acquisition's rule: **per-document files are authoritative; catalogs are derived
indexes**, rebuilt from them, never appended to concurrently. `documents.jsonl` and
`passages.jsonl` finally take the names acquisition deliberately reserved.

Atomic finalization identical to acquisition: stage into `data/normalization_tmp/`, write
the document JSON last, `os.replace()` into position, quarantine-then-rename on repair.

---

# 12. Deterministic identities

IDs derive from stable source identity and structural position. No random UUIDs, readable
because they appear in evidence panels.

```text
document_id  norm:{cik10}:{accession}:{original_filename}
section_id   {document_id}#s{section_sequence}
block_id     {document_id}#b{block_sequence}
passage_id   {document_id}#p{passage_sequence}
```

`document_id` derives from the acquisition `artifact_id`, so the join back is total.

**Version behaviour, stated explicitly:**

| Change | Effect on IDs | Effect on output |
| --- | --- | --- |
| Source bytes unchanged, all versions unchanged | identical | byte-identical |
| Parser version changes | block/passage IDs **may** change | outputs change; recorded in the run |
| Normalizer version changes | section/block IDs may change | outputs change |
| Passage strategy changes | passage IDs change; document/section/block IDs do **not** | only passages rebuild |
| **Source bytes change** | `document_id` is **stable**; block and passage IDs may change if structure changed | `source_content_sha256` changes, content rebuilt |

The last row corrects an earlier draft that claimed a source change alters every ID. It does
not: `document_id` derives from CIK, accession and original filename, none of which depend
on content. That is the right behaviour — it is the same logical document, revised. The
change is visible through `source_content_sha256`, and acquisition already treats a
differing re-fetch as a new version of the same artifact.

Because block sequence depends on the parser, IDs are only stable *within* a version set.
Every output therefore records the version tuple, and verification refuses to mix outputs
produced under different tuples in one catalog.

**Documents and passages carry deterministic fields only:**

```text
selection_policy_version, parser_name, parser_version, normalizer_version,
passage_strategy_name, passage_strategy_version, config_hash,
source_content_sha256, content_sha256, derivation_id
```

**`run_id`, timestamps and `code_commit` are deliberately absent from documents and
passages.** They change on every run — or, for `code_commit`, on every unrelated commit —
so including them would make byte-identical reruns impossible, contradicting check 5. They
live in the run manifest, which records `produced_document_ids`, so "which run produced
this document?" remains answerable by lookup rather than by embedding a volatile value in
the corpus.

This is not theoretical. In acquisition, `_filing.json` carries `run_id` and `fetched_at`,
and those fields propagate into the catalog; a repair of three filings therefore changed the
catalog bytes while the corpus was semantically unchanged, and verification did not notice.
Normalization does not repeat that.

`derivation_id` is a single hash over the version tuple, `config_hash` and
`source_content_sha256` — one value that answers "was this produced the same way?" without
a field-by-field comparison.

The normalized corpus is fully rebuildable from the acquisition corpus. `data/normalized/`
is gitignored; config and manifests are tracked.

---

# 13. Verification and reporting

Executable checks, non-zero exit on failure:

1. Every selected artifact has a normalized document or a recorded failure with a reason.
2. Every normalized document links to an existing acquisition `artifact_id`.
3. Every passage links to its source blocks and document.
4. IDs are deterministic — recomputing from inputs reproduces them.
5. Re-running with unchanged inputs and versions is byte-identical.
6. Section and block ordering is stable across runs.
7. Documents with suspiciously low text for their size are flagged.
8. Duplicate output IDs are rejected.
9. `source_content_sha256` matches the acquisition catalog's `sha256`.
10. Unsupported formats are reported, never silently skipped.
11. Image-heavy artifacts are flagged, never recorded as empty.
12. Catalogs match the files on disk, both directions.
13. Interrupted runs leave no finalized partial document.
14. No graph, extraction, embedding, or LLM code exists in the package.
15. All outputs in a catalog share one version tuple (`derivation_id` comparison).
16. Table `markdown` and `plain_text` recompute from `rows` and match the stored values.
17. Every passage-eligible block is in exactly one passage; every excluded block carries an
    exclusion reason (§7).
18. No authoritative document or passage contains `run_id`, a timestamp, or `code_commit` —
    the fields that would break check 5.
19. Every fallback-parsed document records its parser and triggering condition.
20. Documents flagged `hierarchy_uncertain` are reported, not silently accepted.

**Corpus report:** selected vs excluded with reason codes; documents by form and role;
parser success rate by parser; section, block, passage and table counts; character
distributions; low-text documents; image-heavy artifacts; duplicate hashes; failures and
warnings; and a per-parser comparison when both have run.

---

# 14. Implementation sequence

Each step ends green.

1. Package skeleton, config, `contracts.py`, structural tests.
2. Canonical models (`NORMALIZED_MODELS.md`) with round-trip tests.
3. Deterministic identity module — pure functions, tested first.
4. Selection stage against the real acquisition catalog; review the 294/2,019 outcome.
5. `SecHtmlDocumentParser` behind the protocol; adapter boundary tests.
6. Table handling.
7. `LxmlDocumentParser` fallback — proves the protocol is a real seam.
8. Canonical normalizer, including section-tree derivation from title levels.
9. Passage strategy.
10. Storage with atomic finalization; catalog builder.
11. Verification, corpus report.
12. **Spike run over the 19 fixtures** ([SPIKE_CORPUS.md](SPIKE_CORPUS.md)) and manual
    review.
13. **Stop.** Implementation ends here. The full run over the 294 selected artifacts is a
    separate decision, taken only after §16 passes and the review in §15 is read.

---

# 15. Manual review process

The spike output is reviewed by hand before any full run. The reviewer gets one generated
`data/normalization_reports/<run_id>-review.md` per run containing, per fixture: title,
section tree, first blocks of each section, every table rendered as Markdown, passage
boundaries with character counts, and flags raised.

Reviewers record findings in `normalization_review.jsonl`:

```text
{artifact_id, run_id, parser, reviewer, dimension, verdict, note}
```

Dimensions: `title`, `section_hierarchy`, `paragraph_order`, `table_readability`,
`missing_content`, `duplicated_content`, `boilerplate_contamination`, `traceability`,
`passage_coherence`, `passage_size`, `image_detection`, `parser_failure`.

Because the file is keyed by run and parser, two parser or passage-strategy versions can be
diffed directly.

---

# 16. Acceptance criteria before the full run

1. **The 18 selected fixtures** normalize without error under the default parser.
   Fixture 19 (EX-31.1) is the negative control: it must be **excluded at selection** with
   reason `BOILERPLATE_CERTIFICATION` and must never reach the parser. A separate
   parser-only test may run it directly to confirm the parser handles certifications, but
   that is a parser test, not part of normalization.
2. Section hierarchy is judged correct for the 10-K, both 10-Qs, and both DEF 14As.
3. EX-21.1 yields every subsidiary as structured table rows, not empty text. *(Corrected:
   the document has 68 `<tr>` but only 4 subsidiaries; the rest are spacer rows.)*
4. Hidden inline-XBRL content appears in **no** passage.
5. No passage contains run-together tokens of the `ASSETSFor` kind (§1.7).
6. Every fixture passage resolves to a source block, artifact, and live EDGAR URL.
7. Both parsers run over the 18 selected fixtures in `comparison` mode and the diff is
   recorded.
8. Re-running is byte-identical — which requires §12's exclusion of volatile fields.
9. Manual review records no unresolved `missing_content` or `boilerplate_contamination`.
10. Table markdown for the Q4-2025 EX-99.1 financial tables is judged readable.
11. Table classification is explicitly reviewed on fixtures 13, 14, 15, 17, 18.
12. No fixture triggers fallback in `normal` mode; any that does is investigated before the
    full run.

---

# 17. Risks

**17.1 sec-parser staleness.** No release since 2024-06-09, no 10-K parser. Mitigated by
using it only for block segmentation and heading classification, deriving hierarchy
ourselves, pinning the version, and shipping the lxml fallback in the same milestone.

**17.2 Heading inference is the whole game.** With zero semantic headings, a wrong
font-weight threshold silently produces flat documents. Mitigated by fixtures spanning
2020–2026 markup eras and by making hierarchy a reviewed dimension.

**17.3 Layout-table misclassification.** 375 tables in one DEF 14A. Misclassifying data as
layout loses content. Mitigated by keeping both kinds and recording the decision.

**17.4 Passage size versus financial tables.** A table exceeding 4,000 characters cannot be
split without becoming unreadable. v1 keeps it whole and flags `oversize_table_passage`.

**17.5 Volume.** 15.2 M characters and ~10,000 passages. Manageable, but the 10-K at
572,060 characters will dominate runtime; parsing is ~0.4 s per large document, so a full
run should be minutes, not hours.

**17.6 ID stability across parser upgrades.** Block IDs move when the parser changes. This
is accepted and documented rather than engineered around; the version tuple makes it
visible, and passages can be rebuilt without re-parsing when only the strategy changes.

---

# 18. Open decisions — resolved

| # | Question | Decision |
| --- | --- | --- |
| 1 | Which artifacts enter v1? | The 294 in §2: primaries, EX-99.x, material agreements, governance and structured exhibits. Certifications, consents, assets, XBRL, full submissions, index headers excluded |
| 2 | First parser? | `sec-parser==0.58.1`, used for block segmentation and heading classification only |
| 3 | Fallback parser? | Yes — `LxmlDocumentParser`, built in the same milestone. Three modes (`normal`/`fallback`/`comparison`) with six explicit hard-failure triggers; weak hierarchy warns, never switches parsers (§4.5) |
| 4 | Canonical model? | [NORMALIZED_MODELS.md](NORMALIZED_MODELS.md) |
| 5 | Table representation? | Structured rows **authoritative**; Markdown and plain text derived and re-verified. `table_kind: data/layout/mixed/unknown`, defaulting to `unknown`, never removing a table |
| 6 | Source locator? | Block sequence, tag, fragment hash, heading path, char range **within block**. No byte offsets |
| 7 | Passage construction? | Section-aware, ~1,500 chars, no overlap, tables as their own passage. Invariant is per passage-eligible **block**, not per character (§7) |
| 8 | Duplicate marking? | Content hashes recorded and checked; no dedup system (§1.4 shows none needed) |
| 9 | Image-heavy exhibits? | Flag only. EX-99.2 is text-rich (§1.3), so no OCR in v1 |
| 10 | Storage format? | One JSON per document + passages JSONL + derived JSONL catalogs |
| 11 | Authoritative vs derived? | Authoritative: selection manifest, document JSON, passages JSONL, per-run issues, run manifest. Derived: all four aggregate catalogs and reports (§11) |
| 12 | Complete normalized document? | Valid document JSON present in a finalized directory, all blocks present, source hash matching acquisition |
| 13 | Atomic finalization? | Staging directory, document JSON written last, `os.replace()`, quarantine-then-rename repair |
| 14 | Spike fixtures? | The 19 in [SPIKE_CORPUS.md](SPIKE_CORPUS.md) |
| 15 | Criteria before full run? | §16 |

## Genuinely needs founder input

Nothing blocks implementation. Two calls are worth confirming because they are judgment,
not fact:

- **Passage target of 1,500 characters** is chosen for financial event extraction, where
  an event and its numbers usually sit within a few paragraphs. If the first extraction
  experiments favour larger context, this changes — and only passages rebuild, not
  documents.
- **Excluding EX-31/32 certifications and EX-23 consents (83 artifacts)** is a judgment
  that their content is boilerplate. They stay in the acquired corpus and can be added by
  editing one policy file if that proves wrong.
