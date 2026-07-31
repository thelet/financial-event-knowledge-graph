# Canonical normalized models

Schemas for [V1_DOCUMENT_NORMALIZATION.md](V1_DOCUMENT_NORMALIZATION.md). Pydantic v2,
mirroring the acquisition package's conventions.

Three representations are kept strictly apart:

```text
parser-native          sec_parser.TitleElement, lxml elements
   ↓ adapter boundary — nothing above this line leaves stages/parse/
canonical in-memory    ParsedDocument, NormalizedDocument, Passage
   ↓ serialization
persisted schema       <document_id>.json, *.passages.jsonl, catalogs
```

No parser type appears in a canonical model, in storage, or in any later stage. This is the
rule that lets sec-parser be replaced without touching anything downstream.

---

# 1. Selection

## `SelectedArtifact`

Produced by the select stage; the only input the parse stage accepts.

```text
artifact_id            from the acquisition catalog
filing_id, cik10, company_name, tickers[]
accession, form, form_sanitized
filing_date, report_date, acceptance_datetime
items[]                SEC 8-K item codes
artifact_kind          primary | exhibit
role                   primary | ex99-01 | ex21-01 | ...
exhibit_type           EX-99.1 | ...
description
original_filename
source_path            path under data/raw
source_url             live EDGAR URL
media_type, file_extension
size_bytes
source_sha256          MUST match the acquisition catalog
decision               include | exclude | needs_review
reason_code            PRIMARY_NARRATIVE | BOILERPLATE_CERTIFICATION | ...
policy_version
```

Every acquired artifact produces one row in `selection.jsonl`, included or not. Nothing is
silently dropped.

---

# 2. Parser output

## `ParsedDocument`

Canonical but still parser-shaped: an ordered block list, no section tree yet. The section
tree is the normalizer's job (v1 plan §4.3), so a parser that cannot infer hierarchy is
still usable.

```text
artifact_id
parser_name, parser_version
blocks[]               ParsedBlock, document order
title                  best-effort document title, may be null
warnings[]             string
parse_duration_ms
```

## `ParsedBlock`

```text
block_sequence         0-based, document order
block_type             heading | paragraph | list_item | table
                       | table_caption | footnote | page_header
                       | page_number | image | other
text                   normalized text, block separators applied
level                  heading level 1..6, null for non-headings
tag_name               source element name
fragment_sha256        sha256 of the source HTML fragment
table                  ParsedTable, only when block_type == table
image_count            images inside this block
attributes             dict, parser-specific extras, never read downstream
```

`block_type` values chosen to match the v1 plan's required distinctions. `page_header` and
`page_number` exist so boilerplate can be classified rather than deleted — the normalizer
drops them, but the parser records that they were seen.

## `ParsedTable`

```text
rows                   list[list[str]], cell text
header_rows            int, leading rows judged to be headers
n_rows, n_cols
has_merged_cells       bool, colspan/rowspan seen
markdown               normalized Markdown rendering
plain_text             row-joined text
table_kind             data | layout | unknown
caption                text, may be null
```

`table_kind` is a recorded heuristic, never a silent filter (v1 plan §6). Both kinds are
persisted — EX-21.1 has 511 characters of text and 68 table rows, so dropping layout tables
would empty it.

---

# 3. Canonical normalized document

## `NormalizedDocument`

The authoritative per-document record, written to
`data/normalized/sec/<cik10>/<form>/<date>_<accession>/<document_id>.json`.

```text
schema_version

# identity
document_id            norm:{cik10}:{accession}:{original_filename}
source_artifact_id     ties to the acquisition catalog
filing_id

# company
cik, cik10, company_name, tickers[]

# filing context, carried from acquisition unchanged
form, form_sanitized, filing_date, report_date, acceptance_datetime
items[]                SEC item codes — authoritative filing-level metadata
                       and useful weak labels; NOT passage-level event labels
is_amendment, amends_accession

# artifact context
artifact_role, exhibit_type, original_filename
source_url, source_path
document_type          narrative_primary | earnings_release | shareholder_letter
                       | supplemental | material_agreement | governance
                       | structured_exhibit | other

# content
title
sections[]             NormalizedSection, document order
blocks[]               ContentBlock, document order, flat and authoritative

# structured-data lane, linked not interpreted (v1 plan §10)
related_xbrl_artifact_ids[]

# provenance and determinism
selection_policy_version
parser_name, parser_version
normalizer_version
source_content_sha256  MUST equal the acquisition catalog sha256
content_sha256         over normalized text, for duplicate detection
run_id, normalized_at, code_commit, config_hash

# quality
flags[]                requires_image_processing | low_text_yield
                       | wide_table | oversize_table_passage | short_passage
                       | continuation_table | needs_review
stats                  ObjectStats
```

`blocks[]` is flat and authoritative; `sections[]` reference blocks by ID rather than
nesting them. One block therefore has exactly one representation, and section restructuring
never rewrites block content.

## `NormalizedSection`

```text
section_id             {document_id}#s{section_sequence}
section_sequence
title                  section heading text
level                  1..6
parent_section_id      null for top level
heading_block_id       the block that produced this heading, null for synthetic root
block_ids[]            blocks belonging to this section, document order
char_count
heading_path[]         ancestor titles, root first — the human-readable locator
```

## `ContentBlock`

```text
block_id               {document_id}#b{block_sequence}
block_sequence
section_id             owning section
block_type             heading | paragraph | list_item | table
                       | table_caption | footnote | other
text
char_count
level                  headings only
block_sha256           over normalized text
locator                SourceLocator
table                  TableBlock, only when block_type == table
```

## `SourceLocator`

The provenance model. Byte offsets into the source file are deliberately absent —
sec-parser cannot supply them reliably (v1 plan §5).

```text
artifact_id
block_sequence
tag_name
fragment_sha256        detects source drift and re-locates after re-parsing
heading_path[]
char_start, char_end   offsets WITHIN this block's own text, never the source file
table_index            position among tables, tables only
```

## `TableBlock`

```text
table_index
rows, header_rows, n_rows, n_cols
has_merged_cells
markdown, plain_text
table_kind             data | layout | unknown
caption
flags[]                wide_table | continuation_table
```

---

# 4. Passages

## `Passage`

Written to `<document_id>.passages.jsonl`, one JSON object per line.

```text
passage_id             {document_id}#p{passage_sequence}
passage_sequence
document_id
source_artifact_id, filing_id

# retrieval and filtering context, denormalized on purpose so a consumer
# needs one file rather than a join
cik10, company_name, form, filing_date, report_date, items[]
artifact_role, exhibit_type, document_type, source_url

# content
text
char_count
passage_kind           narrative | table | list | footnote
section_id
heading_path[]         section context, NOT concatenated into text
block_ids[]            every block contributing to this passage
table_id               set when passage_kind == table

# provenance and determinism
locators[]             SourceLocator per contributing block
content_sha256
passage_strategy_name, passage_strategy_version
parser_name, parser_version, normalizer_version
run_id
flags[]                short_passage | oversize_table_passage
```

Denormalizing filing context onto each passage is deliberate: extraction and evidence
panels need company, form, date, and item codes with every passage, and a consumer that
must join three files to display one quote will eventually skip the join.

`heading_path` stays a separate field rather than being prepended to `text`, so the passage
text remains exactly what the filing says. Any consumer wanting heading context can add it;
none can remove it once baked in.

---

# 5. Runs and issues

## `NormalizationRun`

```text
run_id, stage, created_at, finished_at
config_hash
selection_policy_version
parser_name, parser_version
normalizer_version
passage_strategy_name, passage_strategy_version
code_commit, python_version, platform, dependency_versions{}
counts{}               selected, parsed, normalized, failed, skipped,
                       documents, sections, blocks, passages, tables
errors[]
```

The version tuple is recorded on the run **and** on every document and passage, so a mixed
corpus is detectable rather than merely discouraged (v1 plan §13 check 15).

## `NormalizationIssue`

Appended to `issues.jsonl`. A failure is a recorded outcome, never a gap.

```text
issue_id
run_id
artifact_id, document_id      document_id null when parsing failed
severity                      error | warning | info
code                          PARSE_FAILED | UNSUPPORTED_FORMAT | EMPTY_OUTPUT
                              | LOW_TEXT_YIELD | IMAGE_HEAVY | WIDE_TABLE
                              | OVERSIZE_TABLE_PASSAGE | HIDDEN_CONTENT_DETECTED
                              | DUPLICATE_CONTENT_HASH | SOURCE_HASH_MISMATCH
                              | NEEDS_REVIEW
detail
```

## `ObjectStats`

```text
char_count, block_count, section_count, passage_count
table_count, data_table_count, layout_table_count
image_count
max_section_depth
text_yield_pct         normalized chars / source bytes
```

---

# 6. Derived catalogs

Rebuilt from the per-document JSON files. Never appended to concurrently, never
authoritative — the same rule acquisition follows.

| File | Row | Purpose |
| --- | --- | --- |
| `selection.jsonl` | one per **acquired** artifact | decision + reason code for all 2,019 |
| `documents.jsonl` | one per normalized document | corpus-level querying |
| `passages.jsonl` | one per passage | the extraction lane's primary input |
| `issues.jsonl` | one per issue | failures and flags |

`documents.jsonl` and `passages.jsonl` finally use the names acquisition deliberately
reserved. Sort order is `(cik10, filing_date, accession, document_id)` and then sequence,
so rebuilds are byte-identical.
