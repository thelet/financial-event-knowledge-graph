# DATA_ACQUISITION.md

## Acquisition Design — Sources, Scope, Tools, and Storage

This document specifies the data-acquisition pipeline from
[01_PRODUCT_DIRECTION_AND_ARCHITECTURE.md](01_PRODUCT_DIRECTION_AND_ARCHITECTURE.md) §4
for the first anchor company.

It covers the full acquisition mechanism, including related companies and non-company
entities. Only Wave 0 is in scope to build now; §4 explains why the later waves cannot yet
be enumerated and specifies how they will be derived instead.

**Anchor company: Opendoor Technologies Inc.**

Facts in this document marked *(verified)* were confirmed against EDGAR on 2026-07-31.
Facts marked *(unverified)* are working assumptions to be confirmed on ingest and must not
be treated as established until a filing supports them.

---

## 1. Anchor profile *(verified)*

| Field | Value |
| --- | --- |
| CIK | `0001801169` |
| Name | Opendoor Technologies Inc. |
| Former name | Social Capital Hedosophia Holdings Corp. II (SPAC merger, Dec 2020) |
| Tickers | OPEN, OPENL, OPENW, OPENZ (Nasdaq — four listed securities) |
| SIC | 6531 — Real Estate Agents & Managers |
| Fiscal year end | 12-31 |
| Filing history | 665 filings, 2020-01-31 → 2026-07-29 |

The multiple ticker classes are not incidental — OPENL/OPENW/OPENZ are warrant and note
classes, and they make capital-structure events (§7) first-class rather than peripheral.

### Corpus composition *(verified)*

| Form | Count | Role in the graph |
| --- | --- | --- |
| 10-K | 6 | Structure, risk factors, segments, MD&A |
| 10-Q | 19 | Quarterly narrative and metrics |
| **8-K** | **75** | **The event spine** |
| DEF 14A / DEFA14A | 7 / 10 | Executives, compensation, board |
| S-1 / S-1/A / S-4/A / 425 / 424B* | ~50 | SPAC merger and capital raises |
| Form 3 / 4 / 4/A | 34 / 290 / 9 | Insider holdings — structured XML |
| SC 13D/G + amendments | ~35 | Ownership and activist positions |
| Other (CORRESP, S-8, CERT, …) | ~40 | Low priority |

The entire public history of the company is 665 filings and fits in a single
`data.sec.gov/submissions/` response. This is a bounded corpus with a definite answer to
"did we get everything?" — a property worth protecting.

---

## 2. The 8-K item code is free ground truth

Every 8-K carries SEC-assigned item codes classifying the event it reports. Across
Opendoor's 75 8-Ks *(verified)*:

| Item | Count | Event type |
| --- | --- | --- |
| 2.02 | 23 | Results of Operations → EarningsResult, GuidanceChange |
| 5.02 | 18 | Director/Officer Change → LeadershipChange |
| 1.01 | 7 | Material Agreement → Partnership, FinancingAgreement |
| 3.02 | 7 | Unregistered Equity Sale → CapitalRaise |
| 2.03 | 2 | Direct Financial Obligation → DebtFacility |
| 3.01 | 2 | Listing/Delisting Notice → ListingCompliance |
| 2.01 | 1 | Completion of Acquisition |
| 4.01 | 1 | Auditor Change |
| 5.06 | 1 | Shell Company Status Change (de-SPAC) |
| 5.07 | 7 | Shareholder Vote |
| 7.01 / 8.01 / 9.01 | 34 / 24 / 51 | Reg FD, Other, Exhibits (carriers, not types) |

**Consequence for acquisition:** item codes must be captured as first-class document
metadata and carried through parsing into the normalized document
(`sec_items: ["2.02", "7.01", "9.01"]`). They are lost if the pipeline treats an 8-K as
undifferentiated HTML.

**Consequence for the project:** this is a source of **weak labels** for event-type
classification at zero annotation cost. 01 §12 and 03 name "evaluation datasets" as a
durable asset; this contributes toward one before any model runs.

The limit must be stated precisely, because it is easy to overclaim. Item codes are
authoritative *filing-level* metadata: an Item 5.02 filing does report a director or officer
change. They are **not** passage-level event labels — one filing may report several events,
an item code says nothing about which passage carries the event, and a filing may discuss
events beyond the ones its codes name. They are therefore a strong prior and a review
signal, not a scoring key. Acquisition retains them faithfully; how far they can be trusted
as labels is a question for the extraction phase to answer, not to assume.

The 8-K exhibits carry the substance: **EX-99.1** is typically the earnings press release,
**EX-99.2** typically the shareholder letter or investor presentation. Exhibits must be
fetched and stored as separate documents, not discarded as attachments.

---

## 3. Wave model

"Fetch the anchor and everything related" is circular: related entities are discovered *by*
extraction, which runs *after* acquisition. Two rules break the circularity.

**Rule 1 — node coverage ≠ document coverage.** An entity can be a first-class graph node
with rich relationships and zero documents of its own. Its facts come from passages in the
anchor's filings. Only an explicit, small set of companies gets its own corpus.

**Rule 2 — acquisition expands in waves, not one pass.**

| Wave | Membership | Fetched | Status |
| --- | --- | --- | --- |
| **W0** | Opendoor | Everything, full history | **Build now** |
| **W1** | Derived peer set (§4) | 10-K + earnings 8-Ks, 2 years | Derived after W0 |
| **W2** | Every other entity named in W0/W1 text | Nothing — mention-only nodes | Automatic |
| **W3** | W2 entities promoted on graph evidence | Promoted to W1, re-acquired | Later |

W3 is the incremental-update pipeline of 01 §2. It is the mechanism that makes the frontier
evidence-driven rather than guessed.

---

## 4. Why W1 cannot be enumerated yet

The W1 membership question was tested on 2026-07-31 using EDGAR full-text search for
inbound mentions — which companies name "Opendoor" in a 10-K. 175 hits *(verified)*:

| Filer | Hits | Assessment |
| --- | --- | --- |
| Lennar Corp (LEN) | 15 | Homebuilder — strong, unanticipated signal |
| FS KKR Capital (FSK) | 7 | BDC — likely a capital provider |
| RE/MAX Holdings (RMAX) | 7 | Brokerage |
| Douglas Elliman (DOUG) | 6 | Brokerage |
| Compass (COMP) | 5 | Brokerage/competitor |
| Newmark Group (NMRK) | 5 | CRE services — relevance unclear |
| Goldman Sachs (GS) | 4 | Likely lender/underwriter |
| Anywhere Real Estate / Realogy (HOUS) | 8 | Brokerage/competitor |
| Redfin (RDFN) | 3 | Competitor |
| ~10 blank-check SPACs | 35 | **Noise** — cite Opendoor as a de-SPAC precedent |

The method fails in both directions:

- **False negatives.** Zillow (Z/ZG) and Offerpad (OPAD) appear nowhere in the results.
  Zillow exited iBuying and later became an Opendoor channel partner *(unverified)*;
  Offerpad is the only pure-play iBuyer competitor. Both are unquestionably W1. Companies
  routinely describe competitors generically ("other iBuyers") without naming them.
- **False positives.** SIC 6770 blank-check companies account for 35 of 175 hits with no
  business relationship whatsoever.

Two corrections follow, both of which belong in the pipeline:

1. **SIC filtering** removes most noise. Of the 175 hits: SIC 6531 (real estate agents) 82,
   1520 (homebuilders) 15, 6770 (blank checks) 35. Retain 6531, 1520, 6500, 6512, 6798,
   6162; drop 6770.
2. **Inbound mentions are one half of the signal.** The other half — which entities
   *Opendoor* names in its own filings — requires W0 to be ingested and extracted.

**Therefore W1 is derived, not declared.** The procedure, to run after the first W0 graph
build:

```text
outbound mentions (from W0 extraction)
        +
inbound mentions (EDGAR FTS, SIC-filtered)
        +
known-partner list (human review, evidence-cited)
        ↓
  candidate set → human confirmation → W1 manifest
```

Nothing about this requires new machinery. W1 companies are SEC filers reached through the
same `DocumentSource`, the same parsers, and the same storage as W0. **W1 is a manifest
entry, not a new adapter.** That is why this document can specify it without it being built.

One known complication: conglomerates. If News Corp (Realtor.com/Move) enters W1, its 10-K
is overwhelmingly irrelevant to real estate. That is a `PassageSelectionStrategy` problem
for the graph pipeline, not an acquisition problem — acquisition fetches the whole filing.

---

## 5. Entities with no document source

Several entity classes central to Opendoor's business have no filings at all:

| Entity class | Examples | Source |
| --- | --- | --- |
| Markets (metros) | Phoenix, Atlanta, Dallas | None — mention-only |
| Macro drivers | Mortgage rates, home-price appreciation, housing turnover | None — mention-only |
| Regulators | FTC, NAR-settlement effects | Occasional filings; mention-only |
| Capital counterparties | Named lenders in Item 1.01/2.03 8-Ks | Mention-only unless independently a filer |
| Individual customers | Home sellers/buyers | Not entities — out of scope |

These are W2 mention-only nodes. Their evidence is a passage in an Opendoor filing, which
is sufficient and honest.

**Deliberately deferred: reference time series.** Mortgage rates (FRED), home-price indices,
and metro-level housing data are genuinely useful for grounding macro-driver nodes in
numbers. They are **not** added now, for a structural reason: a time series is not a
document. It has no passages, no sections, and no supporting text. Forcing it into
`NormalizedDocument` would corrupt the canonical model that 01 §5 exists to protect. When
added, it belongs behind a separate `ReferenceDataSource` interface with its own storage
lane and its own evidence semantics.

---

## 6. Source inventory and tool allocation

### Sources

| Source | Endpoint / access | Provides |
| --- | --- | --- |
| EDGAR submissions | `data.sec.gov/submissions/CIK##########.json` | Complete filing history, form types, dates, **8-K item codes** |
| EDGAR archives | `www.sec.gov/Archives/edgar/data/<cik>/<accession>/` | Filing documents and exhibits |
| EDGAR XBRL | `data.sec.gov/api/xbrl/companyfacts/` | Tagged financials incl. company extension tags |
| EDGAR full-text search | `efts.sec.gov/LATEST/search-index` | Inbound mentions (§4); 2001+ coverage |
| Opendoor IR site | `investors.opendoor.com` | Shareholder letters, press releases, webcasts — **unverified, see §11** |

### Tool allocation

Evaluated 2026-07-31. All three are MIT-licensed.

| Interface | Tool | Notes |
| --- | --- | --- |
| `DocumentSource` | **httpx** + SEC REST APIs — *see note* | `data.sec.gov/submissions/` for discovery; `<accession>-index-headers.html` for attachments |
| `DocumentFetcher` | **httpx** with a hand-rolled rate limiter and retry policy | SEC User-Agent, 5 req/s, `Retry-After` honored |
| `RawDocumentStore` | Local filesystem | Nothing else warranted at this size |
| `DocumentParser` — 10-K/10-Q | **sec-parser** (0.58.1, released 2024-06-09) | Semantic tree + Part/Item section detection |
| `DocumentParser` — 8-K + EX-99.x | **trafilatura** or **selectolax** | Short flat press releases; a semantic tree buys nothing |
| `DocumentParser` — PDF | **pymupdf4llm**, or **docling** if tables prove critical | IR decks and letters only |
| `DocumentNormalizer` | Custom, **pydantic** | Ours by design — no library should own the canonical format |
| `NormalizedDocumentStore` | JSONL now, Parquet later | Diffable and greppable matters more than columnar speed while the schema is unstable |
| Catalog / index | **DuckDB** over the JSONL | Query the corpus without loading it; zero setup |

**Note on edgartools.** It was the planned `DocumentSource` and `DocumentFetcher`, on the
grounds that exhibit types are unavailable without it. That premise proved false: EDGAR's
`<accession>-index-headers.html` carries the authoritative SGML header with `TYPE`,
`SEQUENCE`, `FILENAME`, and `DESCRIPTION` per document in ~11 KB *(verified 2026-07-31)*.
Acquisition therefore needs no filing library at all. edgartools remains a reasonable
choice for XBRL *interpretation* in a later phase — v0 stores XBRL files without reading
them. See the v0 plan §13 for the full set of API findings.

**sec-parser caveats.** It is tuned for 10-Q, secondarily 10-K, and thin on 8-K — which is
awkward here, since 8-Ks are the event spine. Hence the split above: it handles the 25
periodic reports, and the 75 event-bearing 8-K exhibits use a simpler parser. Its known
weakness is pre-2015 filing HTML; Opendoor's corpus begins 2020-01-31, entirely modern
HTML, comfortably inside its tested range. No PyPI release since 2024-06-09 despite repo
commits through 2026-06-25 — pin `0.58.1`, treat as frozen-but-working, and keep it behind
`DocumentParser` so it can be swapped if it rots.

### Rejected

**OpenEDGAR** (LexPredict, MIT, last commit 2022-12-26). Django + Celery + Postgres +
Apache Tika + S3 — corpus-scale infrastructure for building "all filings by all filers"
datasets. Three problems: dormant for three and a half years; architecturally opposed to
01 §2 (easy local execution, simple debugging); and its core value proposition —
reconstructing filing metadata by crawling quarterly `full-index` files — was obsoleted by
the `data.sec.gov` REST APIs, which return Opendoor's entire 665-filing history in one
request. Its Tika text extraction also produces flat text with no section structure, which
is worse than sec-parser for the evidence layer. The OpenEDGAR paper remains a useful
reference on corpus-construction pitfalls, and its `Company / Filing / FilingDocument`
schema is a reasonable model for the catalog in §8.

### The adapter-boundary risk

Dropping edgartools removes one leakage risk but not the general one. The temptation is to
let provider-shaped data — SEC submissions-JSON field names, SGML `TYPE` strings, sec-parser
element classes — flow downstream into extraction. That is how a proof-of-concept choice
becomes the permanent architecture, the failure mode 02 §9 exists to prevent.

v0 draws the line deliberately: acquisition owns SEC-shaped concepts (accession, item codes,
exhibit types) and stores them faithfully, but produces no `NormalizedDocument` or `Passage`
at all. Those are the parsing phase's output, and the conversion from SEC-shaped metadata to
canonical models happens there, in one place. Nothing outside the acquisition package should
read `_filing.json` field names directly; the catalog is the interface.

---

## 7. Ontology implications

Recorded here because acquisition surfaced them; docs 01 §7 and 03 need revision.

The existing ontology is semiconductor-shaped and largely does not fit:

| Docs assume | Opendoor reality |
| --- | --- |
| Product, Technology | Essentially absent — a service sold in *places* |
| SupplyConstraint, CapacityExpansion | No supply chain. Analog is **MarketEntry / MarketExit** |
| Customer wins/losses | Customers are households, not entities. Instead: **channel partners** |
| — | **Market** (metro area) as a first-class node type |
| — | **Inventory** — homes held, aging, write-downs; the central risk |
| — | **MacroDriver** — mortgage rates, HPA, housing turnover; constantly cited and genuinely causal |
| — | **CapitalStructure** — debt facilities and named lenders; existential for an inventory-heavy balance sheet |
| — | **Regulator** — FTC, NAR-settlement effects |

Ontology v1 should be `real_estate_marketplace_v1`, not `semiconductor_v1`. This is the
versioned-ontology swap the architecture was designed for.

---

## 8. Pipeline structure and storage

### Two phases, always

```text
Phase 1 — discovery   → writes a manifest (JSON, ~1 request per company)
        [ human reviews: counts, form types, date coverage, total size ]
Phase 2 — download    → consumes the manifest; resumable; idempotent
```

The manifest is the specification of what the corpus *is*. Scope is reviewable before
anything large is pulled, a failed run resumes rather than re-deciding scope, and the
corpus becomes reproducible from a version-controlled artifact.

### Layout

The authoritative layout is specified in
[plans/data-fetching/V0_OPENDOOR_FETCH.md](../plans/data-fetching/V0_OPENDOOR_FETCH.md) §7,
which supersedes any sketch here. In outline:

```text
config/                        # TRACKED — companies.yaml, fetch.yaml
manifests/                     # TRACKED — immutable, one file per run
  filings/<run_id>.json
  artifacts/<run_id>.json
data/                          # gitignored in full — regenerable
  raw/sec/<cik10>/<form>/<date>_<accession>/
    _filing.json               # per-filing metadata; the source of truth
    source/<original-edgar-filename>
  catalog/                     # DERIVED — rebuilt from _filing.json
    filings.jsonl              # one row per accession
    artifacts.jsonl            # one row per downloaded file
  runs/<run_id>.json
  parsed/                      # later phases
  normalized/
    documents/<document_id>.json
    passages/<document_id>.jsonl
```

`documents.jsonl` and `passages.jsonl` are reserved for the parsing and normalization
phases and are deliberately not used by acquisition — a filing, a downloaded file, and a
normalized document are three different things.

### Rules

- **Raw is immutable and byte-exact.** Files are stored under `source/` with the exact
  filenames EDGAR served. A re-fetch that differs becomes a new version; nothing is
  overwritten in place.
- **Canonical identity is `source + CIK + accession`.** Filing `sec:{cik10}:{accession}`,
  artifact `sec:{cik10}:{accession}:{original_filename}`. Ticker is a display value, never
  an identifier — an issuer may hold several at once and they change.
- **Per-filing metadata is authoritative; global catalogs are derived.** `_filing.json` is
  written atomically per filing; `filings.jsonl` and `artifacts.jsonl` are rebuilt from it
  and are never the source of download state.
- **SHA-256 of raw bytes on every artifact.** Detects silent change and makes re-fetches
  idempotent.
- **`fetched_at` on everything.** Filings are immutable once filed; web pages are not.
  Without it, a corrected document is indistinguishable from a corrupted one.
- **XBRL in a separate lane** from narrative text — stored, not interpreted. Opendoor's key
  operating metrics (homes purchased, homes sold, inventory) are likely custom XBRL
  extension tags *(unverified)*. No language model should be asked to read a number that is
  already tagged.
- **`data/` is gitignored in full.** Configuration, schemas, and immutable manifests are
  version-controlled; downloaded bytes and derived catalogs are not.

---

## 9. Access etiquette and licensing

- SEC requires a declared User-Agent containing a contact email, and requests no more than
  ~10 requests/second. Exceeding it results in IP-level blocking. v0 configures 5 req/s
  with concurrency 4 and honors `Retry-After`.
- EDGAR content is public domain. IR-site content is not — respect `robots.txt` and terms.
- **Transcripts are deferred** (§11). Scraping Motley Fool or Seeking Alpha is against those
  sites' terms; founding the corpus on it is a poor basis for an asset 03 designates as
  durable.
- **Search APIs are not document sources.** A search endpoint returns whatever ranks well
  today: no stable external IDs, no verifiable completeness, no reproducibility. This
  conflicts directly with 01 §12.10. Tools such as Tavily may be used for one-time IR URL
  discovery (human-reviewed, then frozen into the manifest) or as W3 frontier *hints* — never
  as an evidence source. If added, they get a separate interface and a lower provenance tier.

---

## 10. First milestone

### Scope

- **W0 — Opendoor, complete:** 6 10-K, 19 10-Q, all 75 8-K with exhibits, 7 DEF 14A, full
  XBRL history. ~110 documents plus exhibits.
- **W1:** not yet — derived after the first graph build (§4).
- **Form 4s (290):** deferred. Structured XML, no LLM needed — a cheap Executive-layer
  population later.
- **Transcripts:** deferred. EX-99.2 shareholder letters arrive via EDGAR (§11.2), so no
  separate transcript source is needed to reach a first graph.

### Acceptance criteria

1. `artifacts.jsonl` contains one row per fetched file with a stable, deterministic ID, and
   `filings.jsonl` one row per accession. Both are derived from per-filing metadata.
2. Every 8-K carries its SEC item codes through acquisition and into the normalized document.
3. Every 8-K exhibit is a separate artifact, not a discarded attachment.
4. Every passage carries `document_id`, `section_id`, `section_title`, and character offsets
   sufficient to quote it back exactly.
5. Re-running the pipeline over the same manifest produces byte-identical normalized output.
6. Deleting `parsed/` and `normalized/` and re-running rebuilds them from `raw/` alone.
7. No module outside `adapters/` imports `edgar` or `sec_parser`.

---

## 11. Open questions

1. **Opendoor IR site structure** — `investors.opendoor.com` was unreachable from the
   development sandbox on 2026-07-31 (host blocked, not a site outage). Needs verification:
   are shareholder letters published there, in what format, and is there a stable URL
   pattern or RSS feed?
2. ~~**Are shareholder letters already EX-99.2 exhibits?**~~ **Closed 2026-07-31 — yes.**
   Earnings 8-Ks carry EX-99.1 (earnings release, ~500 KB), EX-99.2 (shareholder letter,
   ~43 KB plus 13 images), and EX-99.3 (supplemental) *(verified on Q3 and Q4 2025)*. EDGAR
   supplies them, so **no IR-site adapter is needed**. Caveat: EX-99.2's substance appears to
   be largely rendered as images, so its text yield is likely low — a parsing-phase concern,
   recorded in the v0 plan.
3. **Custom XBRL tags** — does Opendoor tag homes purchased/sold and inventory units as
   extension elements? Determines how much operating data bypasses the LLM entirely.
4. **The 2025-09-19 Item 5.02** is presumed to be the CEO transition *(unverified)*. It is a
   good first extraction test precisely because the answer is checkable.
5. **Amended filings** (8-K/A, 4/A, 10-K/A) — supersession policy: keep both and link, or
   prefer the amendment? Affects the temporal layer, so decide before the graph build.
