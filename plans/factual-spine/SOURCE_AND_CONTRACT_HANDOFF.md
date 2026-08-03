# Source and contract handoff

Companion to [V1_OPENDOOR_FACTUAL_SPINE.md](V1_OPENDOOR_FACTUAL_SPINE.md). Everything here must
be confirmed **before** the stage named in the last column. Nothing here is a plan; it is the set
of things a plan cannot decide for itself.

Written 2026-08-03. Every "verified" line names how it was checked and when.

---

## 1. External sources — what was actually verified

| Source | Endpoint | Verified 2026-08-03 | Credential | Blocks |
| --- | --- | --- | --- | --- |
| SEC Companyfacts | `data.sec.gov/api/xbrl/companyfacts/CIK0001801169.json` | ✅ 200, 1.30 MB, taxonomies `dei`/`us-gaap`/`ecd` | none (User-Agent required) | F1 |
| SEC full-text search | `efts.sec.gov/LATEST/search-index` | ✅ 200, 12 hits for `"transcript"` on this CIK | none | — |
| Yahoo chart | `query1.finance.yahoo.com/v8/finance/chart/OPEN` | ✅ 1,532 sessions, 2020-06-18 → 2026-07-24 (128 of them pre-de-SPAC IPOB), adjusted close present, no split/dividend events | none | F8 |
| Stooq CSV | `stooq.com/q/d/l/?s=open.us&i=d` | ❌ **returns a JavaScript proof-of-work challenge, not CSV** | n/a | F8 |
| Opendoor IR | `investor.opendoor.com` | ✅ exists; hosts events, replays, quarterly reports | none | F6 |
| FMP transcripts | `financialmodelingprep.com` | ⚠️ API exists; **pricing tiers not verified**; display/redistribution requires a separate Data Display and Licensing Agreement | API key | F6 |

`investors.opendoor.com` (plural) does **not** resolve. The correct host is
`investor.opendoor.com`.

---

## 2. The five things that must be decided by a person

### 2.1 Transcript source *(blocks F6 — do not start it without this)*

**The finding that changes the question:** Opendoor has replaced its traditional earnings
conference call with a *"Financial Open House"* video event streamed on `investor.opendoor.com`,
Robinhood, YouTube and X, with a live shareholder Q&A. Recent quarters are therefore not
analyst-call transcripts, and a schema assuming operator → prepared remarks → analyst Q&A will
misfit them.

**SEC is not an option.** Opendoor has never furnished a transcript to EDGAR — all 12 full-text
hits for `"transcript"` are incidental usages in agreements, a 2020 SPAC 425, and the 2025
litigation stipulation. *(verified)*

**No terms page for any option below was retrieved and read.** FMP's terms page returns 403 to a
non-browser client. Every "Terms" cell is therefore `(unverified)` and is a *question*, not a
finding. **This table cannot settle the decision — it scopes the checks that would.**

| Option | Cost | History | Terms | Provisional |
| --- | --- | --- | --- | --- |
| Paid API (FMP or similar) | *(unverified)* | multi-year | *(unverified)* — reportedly needs a separate agreement to display or redistribute | ✅ provisionally recommended |
| Local ASR on the IR webcast | free + GPU | **replay retention window unverified — this is the single fact that decides the option** | *(unverified)* | fallback |
| Free aggregators (Motley Fool, Insider Monkey, Investing.com, Globe and Mail) | free | multi-year | *(unverified)* | ❌ not recommended |
| Seeking Alpha | paywalled | multi-year | *(unverified)* | ❌ |
| Enterprise (AlphaSense, Capital IQ, LSEG, FactSet) | enterprise | excellent | *(unverified)* | ❌ disproportionate |

**Checks to run before F6** — each is cheap and each currently blocks a decision:

- Retrieve `investor.opendoor.com` events pages and record how far back replays actually go.
- Retrieve and quote the terms of the one paid API being considered, from a browser.
- Confirm whether prepared-remarks scripts are separately posted (they are the company's own
  fixed work — see plan §6.3).

**Questions only the founder can answer**

1. Is a paid API subscription acceptable, and at what monthly ceiling?
2. Is it acceptable that the prototype **stores** full transcript text locally but **never
   republishes** it, and that generated posts quote only short attributed excerpts?
3. If neither: drop transcripts, and accept that management explanation — the richest material
   for an insightful post — is limited to filed letters and MD&A.

**Constraint to carry into any answer:** whatever the source, the extraction rule is that only
`speaker_role == executive` utterances may become company claims. An analyst's premise must never
become an observation. This is enforced in validation, not in a prompt.

### 2.2 Stock-price provider *(blocks F8)*

**Recommended:** Yahoo's chart endpoint for the prototype — it works today with no credential and
returns the complete 1,414-session series with adjusted close. **Behind a swappable adapter**, so
the composition root can move to a credentialed provider without touching the lane.

**Stated plainly:** the endpoint is undocumented and Yahoo's terms discourage automated access.
It is appropriate for a local prototype and inappropriate for anything published. If the
prototype is ever published, this becomes a paid-provider decision.

**Fallback:** Tiingo or Alpha Vantage free tier (both need a key; 2026 limits unverified).

### 2.3 Guidance in the ontology now, or later? *(blocks F5, influences F3)*

**Recommended: yes, minimally, and before F3 runs.** Three small changes — an
`AssertionType.GUIDED` member, typed range fields, a future-period constraint. The reason for the
ordering is practical: the full narrative run is the largest time commitment in the plan, and if
guidance is not representable when it happens, every outlook passage is discarded and must be
re-run later.

### 2.4 Company-hosted releases alongside SEC copies? *(blocks F4)*

**Recommended: no.** SEC copies exist for all 93 EX-99.x documents. A second copy creates
duplicate-document reconciliation work for zero new facts. The ontology already declares
`accountable.opendoor.com` as a `discovery_only` disclosure channel, which by its own constraint
may not support a historical claim.

### 2.5 Inline XBRL acquisition route *(decide at F1)*

Companyfacts covers four of the six deferred metrics. The other two are **company-extension
concepts absent from Companyfacts entirely**. Three routes:

| Route | Cost | Note |
| --- | --- | --- |
| Flip `config/fetch.yaml: render_artifacts: true` | re-resolves artifacts across 109 filings | acquires `*_htm.xml` instances; changes acquisition scope |
| Parse inline XBRL from primary `.htm` already on disk | no new fetching | normalization already strips inline-XBRL metadata, so the parser must read raw bytes |
| Accept four of six for now | free | leaves `inventory_valuation_adjustment` and `homes_under_resale_contract` deferred |

**Recommended:** take Companyfacts first, scope the two extension concepts at F1, then choose.
Do not flip the acquisition flag before knowing whether the second route suffices.

---

## 3. Contract changes requiring a version bump

Each is a committed-contract change, not an implementation detail. Grouped because they should
land together in F0 rather than one per stage.

| Change | File | Why it is a contract change |
| --- | --- | --- |
| Evidence resolves without a `passage_id` | `extraction/core/validation.py`, `graph/core/citations.py`, `graph/core/inputs.py`, `graph/stages/projection/edges.py`, `extraction/stages/catalog/jsonl_catalog.py` | **gates F1, F6, F8** — an XBRL fact, price bar or transcript utterance cannot enter the catalogs today. `required_passage_id` also guards claim, issue and rejection rows, and `ClaimRow.passage_id` is a required `str` |
| XBRL `structural_position` carrying `accn`/`fy`/`fp` | `extraction/core/identifiers.py` callers | **without it every XBRL fact for one (metric, period) mints the same id** — `digest()` over no parts is the constant `e3b0c44298fc` (verified) |
| `AssertionType.GUIDED` | `ontology/core/values.py` | enums are the ontology's shape; changes `definition_hash` consumers |
| `SourceLane` members for transcript and market data | `ontology/core/values.py` | same |
| Precision / significant-figures field on the observation | `extraction/core/models.py`, `ontology/core/models.py` | both are `extra="forbid"`; every catalog reader must be updated |
| Future-period constraint | `ontology/core/constraints.py` | turns an accident into an invariant |
| `document_type` becomes an enum | `normalization/core/models.py` | eight values currently unenforced |
| Ontology instance properties projected | `graph/stages/projection/nodes.py` | `cik`, `tickers`, `exchange`, `mic` are declared and silently dropped |
| `loader.CONCRETE_LABELS`, `schema.CONSTRAINED_RELATIONSHIP_TYPES` | `graph/stages/load/` | hand-maintained allowlists; every new label and predicate needs an entry |

**The evidence-contract change is the one that matters.** `EvidenceReference` already declares
`xbrl_concept`, `accession`, `source_url` and `disclosure_channel_id`, and `claims.yaml` already
declares `xbrl_fact` (`required_fields: [xbrl_concept, accession]`) and `external_page`. The
model is ready; three validators are not.

---

## 4. Corrections to record, not to quietly fix

Per the repository's own convention — a correction is more valuable than a clean narrative.

1. **`us-gaap:Revenues` does not exist in Opendoor's XBRL facts.** `metrics.yaml` records the
   mapping as `verified: true`. The issuer tags
   `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` (78 facts). The verification
   that would have caught it — resolving the label against the issuer's own facts rather than
   against the US-GAAP taxonomy — was never run. Fix at F2 **with the reason recorded in the
   commit message**, and add the regression test described in the plan §2.5.
2. **`acceptance_datetime` is stored with a `Z` suffix but SEC publishes it in Eastern Time.**
   *(SEC timezone verified; the mislabel is visible in the data — 71 of 109 filings fall in hour
   16, immediately after the 16:00 ET close, which is implausible under a UTC reading.)* Fix at
   F8; until then, no event window can be trusted.
3. **`document_type: earnings_release` has 22/45 precision.** Not a bug in the rule — EX-99.1 is
   a slot number, not a semantic label — but it is currently invisible, because none of the 23
   mistyped documents carries a review flag.
4. **Six column-period mis-assignments exist in the current graph, unmarked** — four of them
   same-document, same-lane. Listed in plan §8.1. The existing conflict check cannot see them
   because its identity includes the passage. **They are extraction defects, not issuer
   disagreement**, and must be fixed rather than surfaced as counter-evidence: a multi-header
   table's `Year Ended December 31,` column group is inheriting a sibling `March 31,` instant.
5. **`README.md` is stale** — claims extraction is "planned only" and cites 717 tests; 2,530 run
   with no external dependency and three further layers are implemented.
6. **`us-gaap:Revenues` is not the only mapping never checked against issuer facts.** The
   regression test in plan §2.5 should cover every `mapping_type: exact` mapping, not just this
   one — the defect class is "verified against the taxonomy, never against the filer".

---

## 5. What must be true before F12

The story-evidence contract is the last stage and the one most likely to be built on sand. Before
it starts:

- Every fact type resolves to evidence, including non-passage evidence *(F0)*.
- Guidance is comparable to actuals by a **deterministic** check with `not_comparable` as a
  first-class result *(F5)*.
- Price reactions carry the causation caveat as data, not as prose *(F8)*.
- Conflicting observations are enumerated and reviewed rather than silently coexisting *(F9)*.
- A bounded retriever exists; `OBSERVATION_OF_SUBJECT` is excluded from default traversal —
  the `opendoor` node has degree ≥ 2,707 *(F12)*.
