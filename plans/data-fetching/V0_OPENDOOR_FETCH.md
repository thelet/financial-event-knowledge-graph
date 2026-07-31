# v0 — Opendoor Filing Acquisition

**Status:** plan only, not implemented.
**Scope:** fetch Opendoor Technologies filings and their exhibits from SEC EDGAR into a
local `data/` tree with machine-usable structure and metadata. Nothing else.

Implements Wave 0 of [docs/04_DATA_ACQUISITION.md](../../docs/04_DATA_ACQUISITION.md).

Facts marked *(verified)* were confirmed against EDGAR on 2026-07-31.

---

# SUMMARY

## Tools

| Purpose | Tool | Notes |
| --- | --- | --- |
| Filing discovery | **edgartools** `5.44.0` (MIT) | Filing history, form/date filters, accession metadata |
| Exhibit type resolution | **edgartools** attachment API | **Required** — exhibit types are *not* in `index.json` (§6.3) |
| Download | **edgartools**, `httpx` fallback | Handles SEC User-Agent and rate limiting |
| Retry / backoff | **tenacity** | 429 and 5xx only |
| Metadata models | **pydantic** v2 | `_filing.json` and catalog row schemas |
| Catalog queries | **DuckDB** over JSONL | Optional in v0; JSONL is queryable without it |
| Config | **PyYAML** | One `fetch.yaml`, no hardcoded scope |

Not used in v0: `sec-parser`, `trafilatura`, `pymupdf4llm` (parsing is v1); Tavily, any
transcript source, any IR-site crawler.

## Folder structure

```text
data/                                             # gitignored except manifests
├── catalog/
│   ├── companies.json                            # wave assignment, CIK, tickers, aliases
│   ├── manifests/
│   │   └── <run_id>.json                         # discovery output — VERSION CONTROLLED
│   ├── documents.jsonl                           # one row per downloaded file
│   └── runs/
│       └── <run_id>.json                         # run provenance and outcome
└── raw/
    └── sec/
        └── OPEN/                                 # ticker
            ├── 10-K/
            │   └── 2026-02-19_0001801169-26-000010/
            │       ├── _filing.json              # metadata + document role map
            │       ├── primary__open-20251231.htm
            │       ├── ex21-01__a2025ex211xlistofsubsidiar.htm
            │       ├── ex10-38__a2025ex1038opendoorxofferl.htm
            │       ├── xbrl__open-20251231_htm.xml
            │       └── assets/
            │           └── open-20251231_g1.jpg
            ├── 10-Q/
            ├── 8-K/
            │   └── 2026-02-19_0001801169-26-000009/
            │       ├── _filing.json
            │       ├── primary__open-20260219.htm
            │       ├── ex99-01__q42025formxex991earningsre.htm
            │       ├── ex99-02__exhibit992-q42025form8xk.htm
            │       ├── ex99-03__exhibit993-4q25opendoors.htm
            │       └── assets/
            │           ├── exhibit992-q42025form8xk001.jpg
            │           └── … (13 files)
            └── DEF-14A/
```

Three properties this is designed for:

- **Chronologically sortable** — `sorted(glob("data/raw/sec/OPEN/8-K/*/"))` is in filing-date
  order, because the directory name is `<filing_date>_<accession>`.
- **Role-addressable across filings** — `glob("data/raw/sec/OPEN/8-K/*/ex99-01__*.htm")`
  returns every earnings release in one expression. This is why filenames carry a role prefix.
- **Traceable** — the accession is in the path and the original EDGAR filename survives after
  the `__`, so the source URL is reconstructable from the path alone.

## Documents we will receive *(verified counts)*

| Form | Filings | Per filing | Notes |
| --- | --- | --- | --- |
| **10-K** | 6 | Primary ~2.9 MB + ~10 exhibits + XBRL | FY2020–FY2025 |
| **10-Q** | 19 | Primary + certifications + XBRL | |
| **8-K** | 75 | Body + 0–3 EX-99.x + images | 23 carry Item 2.02 |
| **DEF 14A** | 7 | Primary | Executives, comp, board |
| Total | **107 filings** | | ~350–500 files, est. **150–250 MB** |

**Earnings 8-K structure is consistent** *(verified on Q3 and Q4 2025)*:

| Exhibit | Size | Content |
| --- | --- | --- |
| Primary (`open-<date>.htm`) | ~35 KB | 8-K body, item codes |
| **EX-99.1** | **~500 KB** | Earnings press release — **the text-rich, high-value document** |
| EX-99.2 | ~43 KB + 13 JPG | Shareholder letter — **mostly images; low text yield (unverified)** |
| EX-99.3 | ~5 KB + 5 JPG | Supplemental — minimal text |

**10-K exhibits worth naming** *(verified on FY2025)*: `EX-21.1` list of subsidiaries (a free
structured entity list), `EX-10.38`–`EX-10.41` executive offer letters (~235 KB each, directly
relevant to the 2025 leadership churn), `EX-4.7` description of securities (explains the
OPENL/OPENW/OPENZ classes), `EX-23.1` auditor consent. Certifications `EX-31.x`/`EX-32.x` are
boilerplate — fetched, but flagged `low_value: true` so parsing can skip them.

**Closed by this investigation:** doc 04 open question #2. Shareholder-letter content is filed
as EX-99.2/99.3, so **no IR-site adapter is needed for v0**.

---

# 1. Scope

**In:** Opendoor (CIK `0001801169`) 10-K, 10-Q, 8-K, DEF 14A — full history 2020-01-31 →
present, including all exhibits, images, and raw XBRL files.

**Out of v0:** parsing, normalization, passages, canonical models, W1 peer companies, IR
sites, transcripts, XBRL *interpretation* (files are stored, not read), Form 3/4/5, SC 13D/G,
S-1/S-4/424B, DEFA14A.

Out-of-scope forms are excluded by config, not by code — adding `"4"` to `fetch.yaml` must be
sufficient to acquire them later.

# 2. Pipeline

Two phases with a reviewable artifact between them (doc 04 §8).

```text
Phase 1 — DISCOVER      ~1 request. Writes data/catalog/manifests/<run_id>.json
          └── lists every filing that WILL be fetched, with form, date,
              accession, item codes, size — before anything is downloaded

        [ human reviews: counts by form, date coverage, total bytes ]

Phase 2 — DOWNLOAD      Consumes the manifest. Resumable, idempotent.
          └── per filing: resolve exhibit roles → fetch files →
              hash → write _filing.json → append to documents.jsonl

Phase 3 — VERIFY        Reads only what is on disk. Reports gaps and
          └── mismatches against the manifest. Exit non-zero on failure.
```

Each phase is a separate entry point. Phase 2 is re-runnable at any time: a filing directory
containing a complete `_filing.json` whose hashes match is skipped.

# 3. Naming rules

These must be implemented as one shared function each, not inline string formatting.

**Filing directory:** `<filing_date>_<accession_with_dashes>`
e.g. `2026-02-19_0001801169-26-000009`
Date first for sort order; accession for uniqueness and URL reconstruction.

**Form directory:** the form name, sanitized — `/` → `-`, space → `-`.
`DEF 14A` → `DEF-14A`, `8-K/A` → `8-K-A`, `10-K/A` → `10-K-A`.
Sanitization is mandatory: unsanitized form names create paths or collide.

**File:** `<role>__<original_edgar_filename>`

| Role prefix | Applies to |
| --- | --- |
| `primary__` | The filing's primary document |
| `ex99-01__`, `ex21-01__`, `ex10-38__` | Exhibits — normalized, zero-padded, lowercased |
| `xbrl__` | `_htm.xml`, `_cal.xml`, `_def.xml`, `_lab.xml`, `_pre.xml` |
| `full__` | The `<accession>.txt` complete submission |
| (none) | Images and binaries → `assets/`, original name unchanged |

Exhibit role normalization: `EX-99.1` → `ex99-01`, `EX-10.38` → `ex10-38`, `EX-4.7` → `ex4-07`.
Zero-padding the minor number keeps lexical and numeric order identical.

**Why a role prefix rather than a pure EDGAR mirror.** Original filenames are inconsistent and
unparseable — the same exhibit type appears as `q42025formxex991earningsre.htm` in one filing
and `exhibit992-q42025form8xk.htm` in another *(verified)*. Without a prefix, every consumer
must load `_filing.json` to find the earnings release. With it, a glob suffices. The original
name is fully preserved after the `__`, so nothing is lost. **This is a deliberate deviation
from doc 04 §8's "raw mirrors the source's own paths"** — the bytes are untouched, only the
container name is enriched, and `_filing.json` records the original name explicitly.

**Document ID:** `sec-OPEN-8K-20260219-0001801169-26-000009-ex99-01`

Deterministic, derived from `(source, ticker, form, date, accession, role)`. Chosen over a
UUID5 because these IDs will appear in graph nodes and evidence panels, and doc 01 §2 values
debuggability. Same inputs always produce the same ID, satisfying doc 01 §12.10.

# 4. Metadata

## `_filing.json` (one per filing directory)

Source: EDGAR submissions JSON + edgartools attachment resolution.

```text
cik, company_name, ticker
accession, accession_nodash
form, form_sanitized
filing_date, report_date, acceptance_datetime
items[]                     # 8-K item codes — REQUIRED, doc 04 §2
file_number, act, film_number
is_xbrl, is_inline_xbrl, is_amendment
size_reported               # from submissions JSON, for verification
source_index_url
fetched_at, fetcher_version, edgartools_version
documents[]:
    role                    # primary | ex99-01 | xbrl | full | asset
    exhibit_type            # "EX-99.1" as reported by EDGAR, or null
    description             # EDGAR's description field, or null
    original_filename
    stored_path             # relative to the filing directory
    size_bytes
    sha256
    source_url
    low_value               # true for EX-31.x / EX-32.x certifications
```

## `documents.jsonl` (one row per downloaded file)

Flat projection for querying: `document_id`, `cik`, `ticker`, `form`, `filing_date`,
`report_date`, `accession`, `items`, `role`, `exhibit_type`, `stored_path`, `size_bytes`,
`sha256`, `source_url`, `fetched_at`, `low_value`.

Answers "give me every EX-99.1 from an Item 2.02 8-K, chronologically" without touching 107
`_filing.json` files.

## `manifests/<run_id>.json`

The corpus specification: run parameters (CIK, forms, date range, config hash) plus the full
expected filing list with accession, form, date, item codes, reported size. This is the
version-controlled artifact — the corpus is reproducible from it.

# 5. Implementation steps

Ordered so each step is independently verifiable.

1. **Fix `.gitignore`.** It currently ignores all of `data/`, contradicting doc 04 §8's
   "the manifest is version-controlled." Add a negation for `data/catalog/manifests/`.
2. **`fetch.yaml`** — cik, ticker, forms list, date range, output root, rate limit, User-Agent
   identity. No scope value hardcoded anywhere else.
3. **Naming module** — the four functions from §3, pure and unit-tested against the verified
   real filenames in this document. Write these tests first; they are cheap and they are what
   makes the tree automatable.
4. **Discovery** — submissions JSON → filtered filing list → manifest. Prints a summary table
   by form with counts, date range, and total reported bytes. No downloads.
5. **Exhibit role resolution** — for one filing, produce the role map. **Verify against the
   three filings in the SUMMARY before proceeding**; this is where `index.json`'s `type` field
   will mislead (§6.3).
6. **Download** — one filing end to end (use `0001801169-26-000009`, the Q4 2025 earnings 8-K,
   which exercises primary + 3 exhibits + 18 images). Then the full run.
7. **Catalog writers** — `_filing.json`, `documents.jsonl`, `runs/<run_id>.json`.
8. **Verify command** — §7 acceptance criteria as an executable check.

# 6. Risks and gotchas

**6.1 — SEC rate limits.** ~10 req/s and a User-Agent with a contact email are mandatory;
exceeding it means IP-level blocking. ~107 filings × ~5 files plus images ≈ 500 requests. Cap
concurrency at 5 and set a deliberate delay. There is no deadline here — being slow is free,
being blocked is not.

**6.2 — Images are most of the bytes.** The Q4 2025 earnings 8-K carries 18 JPGs totalling
~2 MB against ~580 KB of HTML *(verified)*. v0 fetches them (they are the shareholder letter's
actual content), but they go in `assets/` and are excluded from `documents.jsonl` text
accounting. If total size becomes a problem, `assets/` is the first thing to make optional.

**6.3 — `index.json` does not carry exhibit types.** Its `type` field returns an icon
reference (`text.gif`, `image2.gif`), not `EX-99.1` *(verified)*. Exhibit types come from the
submission SGML header or edgartools' attachment API. Building on the `type` field will
silently produce a role map where everything is an icon name.

**6.4 — Amended filings.** 8-K/A and 4/A exist in the corpus. v0 stores them as ordinary
filings with `is_amendment: true` and makes no supersession decision. That decision belongs to
the graph's temporal layer (doc 04 §11.5) and must not be pre-empted by deleting or
overwriting anything here.

**6.5 — Filenames are not stable identifiers.** Never key anything on the EDGAR filename. The
accession plus role is the identity; the filename is provenance.

**6.6 — EX-99.2 text yield is unverified.** 43 KB of HTML around 13 images suggests the
substance is rendered as pictures. Confirm during v1 parsing. If text yield is near zero, the
shareholder letter needs OCR or a vision model — a v2 concern, but do not assume it parses.

# 7. Acceptance criteria

1. `documents.jsonl` has one row per downloaded file with a stable deterministic ID.
2. All 107 expected filings present; `verify` reports zero gaps against the manifest.
3. Every 8-K's `_filing.json` carries its `items[]` array.
4. Every exhibit is stored as its own file with a resolved role — none discarded, none merged.
5. `glob("data/raw/sec/OPEN/8-K/*/ex99-01__*.htm")` returns all 23 earnings releases.
6. `sorted(glob("data/raw/sec/OPEN/8-K/*/"))` is in filing-date order.
7. Re-running Phase 2 downloads nothing and changes no file.
8. Every stored file's recorded `sha256` matches its bytes on disk.
9. Every file's `source_url` resolves to a live EDGAR URL.
10. Deleting one filing directory and re-running restores it byte-identically.

# 8. Open decisions

1. **Store the `<accession>.txt` full submission?** It duplicates every document (~2× storage)
   but carries the authoritative SGML header. Recommendation: store the parsed header in
   `_filing.json` and skip the file.
2. **Fetch `assets/` images in v0?** Recommendation: yes — they are the shareholder letter's
   real content, and re-fetching later costs another 500 requests.
3. **Include DEFA14A (10 filings)?** Proxy supplements, often substantive during contested
   periods. Recommendation: defer to v1; it is a one-line config change.
4. **Rendered `R*.htm` XBRL viewer files** — excluded. They are derived artifacts, not source.
