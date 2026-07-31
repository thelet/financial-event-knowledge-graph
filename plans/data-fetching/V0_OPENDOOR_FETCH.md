# v0 — Opendoor Filing Acquisition

**Scope:** acquire and organize the raw Opendoor Wave 0 corpus from SEC EDGAR. Nothing else.

Implements Wave 0 of [docs/04_DATA_ACQUISITION.md](../../docs/04_DATA_ACQUISITION.md).

**Explicitly out of scope:** parsing, normalization, passages, canonical document models,
OCR, transcripts, knowledge extraction, ontology, embeddings, graph construction,
retrieval, visualization. v0 downloads bytes and records what they are. Nothing in this
plan may interpret document content.

Facts marked *(verified)* were confirmed against the live SEC API on 2026-07-31; the
supporting observations are recorded in §13.

---

# SUMMARY

## Tools

| Purpose | Tool | Notes |
| --- | --- | --- |
| HTTP | **httpx** `0.28.1` | Single client, configured rate limit and User-Agent |
| Models / validation | **pydantic** `2.13.4` | Manifests, filing metadata, catalog rows |
| Config | **PyYAML** `6.0.3` | `config/companies.yaml`, `config/fetch.yaml` |
| Tests | **pytest** `8.4.2` | Unit, fixture, filesystem, and one live test |
| Retry / rate limit | **stdlib** (hand-rolled) | ~40 lines with an injectable clock — see §13.6 |
| Everything else | **stdlib** | `hashlib`, `json`, `pathlib`, `html.parser`, `argparse` |

**edgartools is not used.** The plan previously required it for exhibit-type resolution on
the grounds that `index.json` lacks types. That premise was wrong: EDGAR publishes
`<accession>-index-headers.html`, an ~11 KB file carrying the authoritative SGML header
with `TYPE`, `SEQUENCE`, `FILENAME`, and `DESCRIPTION` for every submitted document
*(verified)*. With that, the whole acquisition needs one HTTP client and no framework.
Rationale and the full set of API findings are in §13.

Also unused in v0: `sec-parser`, `trafilatura`, `pymupdf4llm`, `duckdb`, `tenacity`, any
transcript source, any IR-site crawler.

## Folder structure

```text
config/                                   # TRACKED
├── companies.yaml                        # issuer scope: CIK, aliases, wave
└── fetch.yaml                            # forms, date range, rate limits, paths

manifests/                                # TRACKED — immutable, one file per run
├── filings/<run_id>.json                 # DISCOVER output
└── artifacts/<run_id>.json               # RESOLVE output

data/                                     # GITIGNORED in full — regenerable
├── raw/sec/<cik10>/<form>/<date>_<accession>/
│   ├── _filing.json                      # per-filing metadata; the source of truth
│   └── source/                           # exact EDGAR bytes, exact EDGAR filenames
│       ├── open-20260219.htm
│       ├── q42025formxex991earningsre.htm
│       ├── exhibit992-q42025form8xk001.jpg
│       ├── 0001801169-26-000009.txt
│       └── 0001801169-26-000009-index-headers.html
├── catalog/                              # DERIVED — rebuildable, never authoritative
│   ├── filings.jsonl                     # one row per filing/accession
│   └── artifacts.jsonl                   # one row per downloaded file
├── runs/<run_id>.json                    # run provenance and outcome
├── reports/<run_id>-corpus.md            # corpus report (§12)
└── tmp/                                  # staging for atomic finalization; never read
```

Concrete example:

```text
data/raw/sec/0001801169/8-K/2026-02-19_0001801169-26-000009/
```

## Documents we will receive *(verified counts)*

| Form | Filings | Notes |
| --- | --- | --- |
| 10-K | 6 | FY2020–FY2025; primary ~2.9 MB plus ~10 exhibits and XBRL |
| 10-Q | 19 | |
| 8-K | 75 | 24 carry Item 2.02 |
| 8-K/A | 2 | amendments, preserved as filed (§11) |
| DEF 14A | 7 | |
| **Total** | **109** | |

### Actual results — run `20260731T134913Z-d1cddf6a`

| Measure | Estimated | **Actual** |
| --- | --- | --- |
| Filings | 107 + amendments | **109** |
| Artifacts | 2,500–3,500 | **2,019** |
| On disk | 350–550 MB | **739.6 MiB** |
| Requests (download) | ~2,500–3,500 | **2,023** |
| Failures | — | **0** |
| Resolution anomalies | — | **0** |

Two estimates were off and the reasons are worth recording. Artifact count came in **under**
because SEC-generated render artifacts are excluded (§13.7) — 7 per XBRL filing. Bytes came in
**over** because the full-submission `.txt` is not "roughly the size of the rest" but 475.8 MiB
of the 739.6 MiB total — it embeds every document uuencoded, so binary assets inflate it well
beyond their stored size. Storing it remains the right call per §5, but the cost is closer to
2× the rest of the corpus than to parity.

Artifact breakdown: asset 1,060 (135.0 MiB), xbrl 364 (44.2), exhibit 268 (20.5), primary 109
(62.8), full_submission 109 (475.8), index_header 109 (1.2).

Earnings 8-K exhibit structure, consistent across Q3 and Q4 2025 *(verified)*: primary 8-K
body ~35 KB; **EX-99.1** earnings release ~500 KB; **EX-99.2** shareholder letter ~43 KB
plus 13 JPGs; **EX-99.3** supplemental ~5 KB plus 5 JPGs.

10-K exhibits of note *(verified, FY2025)*: `EX-21.1` subsidiaries, `EX-10.38`–`EX-10.41`
executive offer letters, `EX-4.7` description of securities, `EX-23.1` auditor consent.
`EX-31.x`/`EX-32.x` certifications are boilerplate — downloaded and cataloged, flagged
`low_processing_priority`.

---

# 1. Scope

**In:** Opendoor Technologies Inc., CIK `0001801169`, forms 10-K / 10-Q / 8-K / DEF 14A,
full history from 2020-01-31, including every exhibit, image, XBRL file, and the
full-submission text file.

**Out of v0, excluded by configuration rather than by code:** Form 3/4/5, SC 13D/G,
S-1/S-4/424B, DEFA14A, and every W1 peer company. Adding any of them must be a
`config/fetch.yaml` edit and nothing more.

# 2. Canonical identity

Ticker is **not** an identifier. It changes, and an issuer may carry several at once —
Opendoor currently lists four *(verified: OPEN, OPENL, OPENW, OPENZ)*.

| Identity | Form | Notes |
| --- | --- | --- |
| Filing | `sec:{cik10}:{accession_dashed}` | e.g. `sec:0001801169:0001801169-26-000009` |
| Artifact | `sec:{cik10}:{accession_dashed}:{original_filename}` | Filename is unique within a filing directory |

`cik10` is the zero-padded 10-digit CIK. Ticker appears only as a display value in
`config/companies.yaml`, in filing metadata, and in catalog rows.

Artifact identity keys on the original filename rather than on `SEQUENCE`, because the
full-submission `.txt` and the index-header file are not part of the SGML `DOCUMENT` list
and therefore have no sequence number.

The design must tolerate: multiple tickers per issuer; ticker changes; missing
`reportDate`; amendments; forms containing `/` or spaces; and artifacts with no
recognizable exhibit role.

# 3. Data model

```text
Filing  (one accession)
  ├── artifact  kind=primary            the filing's own document
  ├── artifact  kind=exhibit            EX-99.1, EX-21.1, EX-10.38, …
  ├── artifact  kind=xbrl               EX-101.*, inline XBRL, .xsd
  ├── artifact  kind=asset              GRAPHIC and other binaries
  ├── artifact  kind=full_submission    <accession>.txt
  ├── artifact  kind=index_header       <accession>-index-headers.html
  └── artifact  kind=render_artifact    SEC IDEA output -- excluded by default (13.7)
```

`artifact_kind` and `role` are separate concepts and both are recorded:

```text
artifact_kind: exhibit
role:          ex99-01
exhibit_type:  EX-99.1
```

`role` is a normalized slug, `exhibit_type` is EDGAR's raw `TYPE` string. `role` is `null`
for assets and for anything whose type does not normalize cleanly — an unrecognized type is
recorded verbatim and reported, never silently dropped (§4 RESOLVE).

# 4. Pipeline stages

Five independently runnable stages. `acquire` runs them in order but is only a convenience.

```text
DISCOVER ──► manifests/filings/<run_id>.json          (immutable)
RESOLVE  ──► manifests/artifacts/<run_id>.json        (immutable)
DOWNLOAD ──► data/raw/... + _filing.json              (atomic per filing)
BUILD_CATALOG ──► data/catalog/*.jsonl                (derived, atomic)
VERIFY   ──► report + exit code
```

### DISCOVER

Reads `config/`. Fetches `data.sec.gov/submissions/CIK{cik10}.json`, following the
`filings.files[]` continuation array when an issuer has more than 1,000 filings. Filters by
form and date from config. Preserves accession, form, filing date, report date, acceptance
datetime, item codes, XBRL flags, file number, act, film number, primary document, and
reported size. Prints a summary table by form before writing. **Downloads no filing
artifacts.** Writes an immutable filing manifest.

### RESOLVE

For each filing in a named filing manifest, fetches `<accession>-index-headers.html` and
parses the SGML `DOCUMENT` blocks into the expected artifact set, then appends the
full-submission and index-header artifacts. Records per artifact: accession, artifact kind,
role, exhibit type, original filename, source URL, expected size, description, media type,
sequence, and `low_processing_priority`.

Expected sizes come from the filing's `index.json` directory listing, which carries `size`
and `last-modified` per file but **not** usable types (§13.1). Types come only from the SGML
header.

Explicitly reports: unrecognized `TYPE` values, documents present in the header but absent
from `index.json` (or the reverse), and case-folded filename collisions (§16.4). Writes an
immutable artifact manifest referencing the filing manifest's `run_id`.

### DOWNLOAD

Consumes a named artifact manifest. Per filing: stage into a temporary directory, download
every expected artifact, validate, hash, write `_filing.json`, then finalize atomically
(§6). Skips filings that are already valid and complete; repairs those that are not.
Writes nothing to the global catalogs.

### BUILD_CATALOG

Rebuilds `filings.jsonl` and `artifacts.jsonl` from finalized `_filing.json` files only
(§9). Never consulted to decide what to download.

### VERIFY

Cross-checks filing manifest, artifact manifest, filesystem, per-filing metadata, hashes,
and generated catalogs (§15). Prints a human-readable report and exits non-zero on any
failure.

# 5. The full submission file

The `<accession>.txt` complete submission **is stored**. It duplicates content and roughly
doubles on-disk size, but the corpus is small and it provides authoritative SGML headers,
attachment sequence and type information, a fallback if library or API behavior changes,
and a single-file archival representation of the filing.

```text
artifact_kind: full_submission
low_processing_priority: true
```

It is not parsed in v0. The `<accession>-index-headers.html` file is likewise stored
(`artifact_kind: index_header`), since it is the evidence behind every role assignment.

# 6. Atomic filing completion

A partially downloaded filing must be unambiguously incomplete. Presence of a filing
directory means nothing; presence of a **valid `_filing.json` inside a finalized directory**
means everything.

```text
1. Create data/tmp/<run_id>/<accession>/
2. Download every expected artifact into it
3. Validate HTTP status and byte length
4. Compute SHA-256 and size for each
5. Write _filing.json  (last file written into the temp directory)
6. os.replace() the temp directory onto the final path
```

`_filing.json` is written last inside the staging directory, and the directory rename is a
single atomic operation on the same filesystem — `data/tmp/` and `data/raw/` share one root
for exactly this reason. An interrupted run leaves debris only under `data/tmp/`, which is
never read by any stage and is safe to delete at any time.

**Repair path.** If a final directory exists but is invalid, it is moved to
`data/tmp/<run_id>/quarantine/` first, the new directory is renamed into place, and the
quarantine is removed only after the rename succeeds. No destructive delete precedes a
successful replacement.

**Skip conditions.** A finalized filing is skipped on rerun only when all four hold:

1. `_filing.json` exists and validates against the schema;
2. every artifact it records exists on disk;
3. every recorded SHA-256 matches the bytes on disk;
4. its artifact set matches the resolved artifact manifest.

Any failure triggers a full re-download of that filing. Partial in-place patching is not
attempted — it is the state most likely to produce a corpus that looks complete and is not.

# 7. Storage layout and source fidelity

**Decision: exact source mirroring.** Original EDGAR filenames are preserved byte-for-byte
under a `source/` directory. The previous plan's role-prefixed naming scheme
(`ex99-01__q42025formxex991earningsre.htm`) is **withdrawn**.

The tradeoff it was resolving is real: EDGAR filenames are inconsistent, and the same
exhibit type appears as `q42025formxex991earningsre.htm` in one filing and
`exhibit992-q42025form8xk.htm` in another *(verified)*, so filenames cannot be used to find
an earnings release. But the fix was aimed at the wrong layer. Enriching filenames makes
globs the de-facto query API, which quietly couples every downstream consumer to a storage
convention and creates a second, subtly different name for every file.

The correct answer is that **the catalog is the query API**. `artifacts.jsonl` supports
"every EX-99.1 from an Item 2.02 8-K, chronologically" directly, and it stays correct if the
storage layout ever changes. Globs remain usable for ad-hoc inspection but are not a
contract.

Consequently:

- File bytes and filenames under `source/` are exactly what EDGAR served.
- `_filing.json` records role, kind, exhibit type, sequence, and source URL per artifact.
- No downstream identity depends on a stored path — identity is §2, and `stored_path` is
  derived data that a consumer may recompute but must not key on.

Directory naming, which is ours and carries no source-fidelity claim:

| Component | Rule |
| --- | --- |
| Issuer | `cik10`, zero-padded — `0001801169` |
| Form | sanitized: `/` → `-`, whitespace → `-`, uppercased. `DEF 14A` → `DEF-14A`, `8-K/A` → `8-K-A` |
| Filing | `<filing_date>_<accession_dashed>` — date first for chronological sort, accession for uniqueness |

Form sanitization is mandatory; an unsanitized `8-K/A` silently creates a nested directory.

# 8. Metadata schemas

## `_filing.json` — authoritative, one per finalized filing

```text
schema_version
filing_id, source, cik, cik10, company_name, tickers[]
accession, accession_nodash
form, form_sanitized, is_amendment, amends_accession
filing_date, report_date, acceptance_datetime
items[]                      # 8-K item codes — always retained
file_number, act, film_number
is_xbrl, is_inline_xbrl, size_reported
source_index_url, source_index_headers_url
run_id, fetched_at, fetcher_version, tool_versions{}
artifacts[]:
    artifact_id, artifact_kind, role, exhibit_type, description
    original_filename, stored_path, sequence
    media_type, file_extension
    size_bytes, sha256
    source_url, http_status, etag, last_modified
    download_attempts, downloaded_at, verification_status
    low_processing_priority
```

## `filings.jsonl` — derived, one row per accession

`filing_id`, `source`, `cik10`, `company_name`, `tickers`, `accession`, `form`,
`form_sanitized`, `filing_date`, `report_date`, `acceptance_datetime`, `items`,
`is_amendment`, `amends_accession`, `is_xbrl`, `is_inline_xbrl`, `artifact_count`,
`total_bytes`, `filing_dir`, `run_id`, `fetched_at`.

## `artifacts.jsonl` — derived, one row per downloaded file

`artifact_id`, `filing_id`, `cik10`, `accession`, `form`, `filing_date`, `items`,
`artifact_kind`, `role`, `exhibit_type`, `description`, `sequence`, `original_filename`,
`stored_path`, `media_type`, `file_extension`, `size_bytes`, `sha256`, `source_url`,
`http_status`, `downloaded_at`, `verification_status`, `low_processing_priority`.

Images and binaries **are** included. They may be excluded from text-oriented byte
summaries, but they are downloaded artifacts and are cataloged and verified like any other.

## Manifests

Filing manifest: run metadata (§10) plus the full expected filing list.
Artifact manifest: run metadata, the originating `filings_run_id`, and the full expected
artifact list with the fields named in §4 RESOLVE.

## `runs/<run_id>.json`

Stage, start and end time, configuration snapshot, counts attempted/succeeded/skipped/
failed, bytes downloaded, request count, and any errors.

# 9. Catalogs are derived artifacts

Nothing appends to a global JSONL file during concurrent downloads. Each finalized filing
owns its `_filing.json`; the catalogs are rebuilt from those.

The catalog builder must:

1. read every finalized `_filing.json` under the raw root;
2. sort deterministically — filings by `(cik10, filing_date, accession)`, artifacts by
   `(cik10, filing_date, accession, sequence_or_none, original_filename)`;
3. deduplicate by canonical identity (§2), reporting any duplicate rather than silently
   collapsing it;
4. write to a temporary file in the destination directory;
5. `os.replace()` it onto the final path.

Byte-identical output for identical inputs is an acceptance criterion (§15.5). Catalogs are
never consulted to decide what to download — the filesystem and the manifests hold that
state.

# 10. Manifests are immutable

A manifest is written once and never modified. Writing to an existing manifest path is an
error, not an overwrite.

- Every DISCOVER run creates a new `run_id`.
- DOWNLOAD may reuse an existing manifest by `run_id`; the default is the most recent.
- Changed configuration produces a different `config_hash` and therefore a new manifest.

Recorded in every manifest and run record:

```text
run_id, stage, created_at
config_hash, manifest_hash
code_commit, python_version, platform
fetcher_version, dependency_versions{}
```

`run_id` is `<utc_compact_timestamp>-<config_hash[:8]>`, e.g. `20260731T140233Z-a1b2c3d4`.
`config_hash` is SHA-256 over the canonicalized effective config; `manifest_hash` is
SHA-256 over the manifest body with the hash field excluded.

# 11. Amendments

Amended filings are preserved exactly as received. `is_amendment` is set when the form ends
in `/A`. `amends_accession` is populated **only** when it can be determined reliably; when
it cannot, it is `null`. Nothing is overwritten, deleted, or merged, and no supersession
decision is made — that belongs to the graph's temporal layer (doc 04 §11.5) and must not be
pre-empted here.

# 12. Corpus report, not corpus invariants

The previous plan asserted that 23 Item 2.02 filings must yield exactly 23 EX-99.1
exhibits. That is a corpus observation, not a law, and hard-coding it makes an unrelated
filing pattern look like a pipeline defect.

Replaced by:

- **Hard requirement:** every artifact in the resolved artifact manifest is present and
  correct. This is exact and corpus-independent.
- **Corpus report** (`data/reports/<run_id>-corpus.md`): filings by form and year, Item 2.02
  filings against their resolved exhibit roles, artifact-kind distribution, filings with no
  exhibits, unrecognized types, and size distribution.
- **Flagged for review, not failed:** an Item 2.02 filing with no EX-99.x, or an
  unrecognized exhibit type.

SEC item codes are authoritative filing-level metadata and useful weak labels. They are not
passage-level event labels, and doc 04 §2's framing is narrowed accordingly.

# 13. External API findings

Verified 2026-07-31 against the live SEC API. These corrected the plan's earlier
assumptions.

**13.1 — `index.json` carries no usable types.** Its per-item `type` field returns an icon
reference (`text.gif`, `image2.gif`), not `EX-99.1`. Item keys are exactly
`last-modified`, `name`, `type`, `size`. It is useful for expected sizes and modification
times, and for nothing else.

**13.2 — `<accession>-index-headers.html` is the authoritative source.** ~11 KB,
HTML-escaped SGML inside `<PRE>`, with a `DOCUMENT` block per submitted document carrying
`TYPE`, `SEQUENCE`, `FILENAME`, `DESCRIPTION`. This replaces edgartools entirely.

**13.3 — `.hdr.sgml` is not a substitute.** It returns HTTP 200 but contains **zero**
`DOCUMENT` blocks — issuer and filing header metadata only.

**13.4 — `DESCRIPTION` is inconsistent and must not drive classification.** The same
exhibit appears as `EX-99.1` in a 2026 filing and `EXHIBIT 99.1` in a 2020 one; primary
documents appear as `8-K` and `FORM 8-K`. Classification uses `TYPE` only; `DESCRIPTION` is
stored verbatim as metadata.

**13.5 — The accession prefix is the filing agent, not the issuer.** Opendoor's 2020
filings carry accession prefix `0001104659` (a filing agent) while the archive path uses the
issuer CIK `1801169`. CIK must come from configuration and from the submissions API, never
from parsing an accession.

**13.6 — No retry library.** Retry policy is hand-rolled: exponential backoff with jitter,
honoring `Retry-After`; retry on 429, 500, 502, 503, 504, and transport errors only; never
retry other 4xx. Chosen over tenacity because the policy is small, the clock must be
injectable for deterministic tests, and it removes a dependency. tenacity `9.1.4` is
installed but unused.

**13.7 — SEC-generated render artifacts ARE in the DOCUMENT list, and are marked.**
*This corrects an earlier assumption in this plan, which claimed they were absent and that
header-based resolution excluded them for free. It does not.* The Q4 2025 earnings 8-K header
carries 33 `DOCUMENT` blocks: 26 filer-submitted, and 7 generated by SEC's IDEA system --
`R1.htm`, `Show.js`, `report.css`, `FilingSummary.xml`, `MetaLinks.json`,
`<accession>-xbrl.zip`, and `<accession>_htm.xml` (the extracted inline-XBRL instance).

They are identified by `DESCRIPTION` beginning `"IDEA:"`. Using DESCRIPTION here does not
contradict 13.4: TYPE genuinely cannot separate them, since the inline XBRL instance and
`FilingSummary.xml` are both filed as TYPE `XML`, whereas the `IDEA:` marker is emitted by a
single system and is consistent. A filename-pattern fallback covers filings that omit
DESCRIPTION.

These are classified `artifact_kind: render_artifact` and excluded by default via
`include.render_artifacts`. Filer-submitted XBRL is unaffected: the `EX-101.*` linkbases
carry proper exhibit types and are retained.

**13.9 — SEQUENCE numbers are not contiguous.** The same filing runs 1..36 across 33
documents. Never infer a document count from the maximum sequence, and never use sequence as
an artifact key -- hence filename-based artifact identity (section 2).

**13.10 — `index.json` reports no size for some entries.** The full-submission `.txt` and the
index-header file have an empty `size` field, so `expected_size` is null for them. Sizes are
recorded from the actual download instead.

**13.8 — SEC access requirements.** A `User-Agent` naming the application and a contact
email is mandatory; the request ceiling is ~10/s. v0 configures 5 req/s and concurrency 4.
Estimated total: 1 discovery request, 107 resolve requests, and ~2,500–3,500 download
requests — roughly 10–15 minutes at the configured rate. Being slow is free; being blocked
is not.

# 14. Implementation steps

Ordered so each step is independently verifiable.

1. `config/companies.yaml`, `config/fetch.yaml`, and `.gitignore` for `data/`.
2. Identity and naming module — pure functions, unit-tested against the real filenames in
   this document.
3. Pydantic models for manifests, filing metadata, and catalog rows.
4. `SecClient` — rate limiting, retry, User-Agent, injectable clock.
5. DISCOVER against the live submissions API; review the summary against §SUMMARY counts.
6. SGML header parser plus artifact classification, tested against saved fixtures **before**
   any bulk run.
7. RESOLVE for the three representative filings in §17, then the full corpus.
8. `LocalRawArtifactStore` — staging, atomic finalization, inspection.
9. DOWNLOAD for one filing (`0001801169-26-000009`: primary + 3 exhibits + 18 images +
   XBRL), then the full run.
10. BUILD_CATALOG.
11. VERIFY.
12. Corpus report.

# 15. Acceptance criteria

1. Every filing in the filing manifest is finalized.
2. Every artifact in the artifact manifest is present on disk.
3. Every stored artifact's recorded SHA-256 matches its bytes.
4. Every finalized filing has a schema-valid `_filing.json`.
5. Catalogs rebuild deterministically — byte-identical across repeated runs on unchanged
   inputs.
6. Rerunning DOWNLOAD issues no unnecessary network requests.
7. Deleting one finalized filing directory and rerunning restores it byte-identically.
8. An interrupted download never appears complete; debris is confined to `data/tmp/`.
9. All 8-K item codes are retained in `_filing.json` and in `filings.jsonl`.
10. Original filenames and source URLs are preserved for every artifact.
11. Primary documents, exhibits, XBRL files, image assets, full submissions, and index
    headers all appear in `artifacts.jsonl`.
12. VERIFY exits non-zero on missing, corrupted, duplicated, or unexpected artifacts.
13. A configuration change produces a different `config_hash` and a new immutable manifest;
    an existing manifest path is never overwritten.
14. No parsing, normalization, OCR, transcript, extraction, or graph code exists in the
    acquisition package.

# 16. Risks

**16.1 — Rate limiting.** ~2,500–3,500 requests. Concurrency 4 at 5 req/s. Blocking is
IP-level and there is no deadline here.

**16.2 — Storage doubling.** Storing the full submission roughly doubles bytes. Accepted
per §5; actual size is reported after the run.

**16.3 — Filenames are not identifiers.** Nothing keys on a stored path or filename except
artifact identity within a filing (§2), which is scoped by accession.

**16.4 — Case-insensitive filesystem.** The working tree is on WSL `/mnt/c` (DrvFs), which
is case-insensitive. Two EDGAR files differing only in case would collide. RESOLVE checks
for case-folded collisions within each filing and reports them.

**16.5 — Atomic rename portability.** `os.replace()` on a directory requires the target to
not exist. The repair path (§6) quarantines first, so the rename target is always absent.

**16.6 — Large single files.** The FY2025 10-K primary document is ~2.9 MB and its full
submission is larger. Downloads stream to disk rather than buffering whole files in memory.

# 17. Representative filings for testing

| Accession | Form | Why |
| --- | --- | --- |
| `0001801169-26-000009` | 8-K | Items 2.02/7.01/9.01; primary + EX-99.1/2/3 + 18 GRAPHIC + XBRL |
| `0001801169-26-000010` | 10-K | Many exhibit types, certifications, full XBRL set |
| `0001104659-20-132667` | 8-K | 2020, agent-filed: accession prefix ≠ CIK, `DESCRIPTION` in the old style |

# 18. Open decisions

1. **DEFA14A (10 filings)** — proxy supplements, often substantive during contested periods.
   Deferred to v1; a one-line config change.
2. **Manifest size in git.** An artifact manifest is ~2,500–3,500 entries. Tracked as
   specified in §10; if repeated discovery runs make this unwieldy, compress or prune old
   manifests rather than making them mutable.
3. **`data/catalog/` is gitignored.** Derived and rebuildable. Revisit only if the catalogs
   become an interchange format rather than a local index.
