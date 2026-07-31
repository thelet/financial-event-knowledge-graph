# Spike corpus

Nineteen artifacts from the acquired Opendoor corpus, used to validate normalization before
any full run. Every one is already on disk — no acquisition is required.

Selection principle: cover every parsing hazard the corpus actually contains
([V1_DOCUMENT_NORMALIZATION.md](V1_DOCUMENT_NORMALIZATION.md) §1), each markup era from
2020 to 2026, and both ends of the size range (511 to 543,609 characters). Eighteen are
expected to be selected for normalization; one certification is included as a **negative
control** that the selection policy must reject.

All figures measured 2026-07-31 by profiling the real files.

| # | Accession | Role | KB | Text | Tbl | Img | ix: | Selected | Why |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `0001801169-26-000010` | primary | 2877 | 543,609 | 83 | 1 | 1631 | yes | Largest narrative document in the corpus. 83 tables, 1,631 inline-XBRL tags, 7,245 hidden elements. The hardest hierarchy and hidden-content case. |
| 2 | `0001801169-25-000092` | primary | 1952 | 224,441 | 65 | 0 | 1104 | yes | Recent 10-Q. 65 tables, 1,104 ix: tags. Verifies periodic-report hierarchy without 10-K scale. |
| 3 | `0001104659-20-121496` | primary | 420 | 87,912 | 26 | 0 | 0 | yes | 2020 10-Q from the legacy `<font>` markup era, no inline XBRL. Proves heading inference is not tuned to modern markup only. |
| 4 | `0001801169-26-000009` | primary | 34 | 4,773 | 7 | 0 | 36 | yes | Q4-2025 earnings 8-K carrier. Items 2.02/7.01/9.01, short primary pointing at three exhibits. |
| 5 | `0001104659-21-010984` | primary | 28 | 10,143 | 11 | 0 | 0 | yes | The Item 2.02 filing with results stated INLINE and no EX-99.x. The corpus outlier that made the 23-vs-22 invariant wrong; selection must not skip it as a thin carrier doc. |
| 6 | `0001140361-25-035480` | primary | 26 | 5,118 | 9 | 0 | 26 | yes | 2025-09-19 Item 5.02 leadership change - the CEO transition. Short, event-dense, the archetype for event extraction. |
| 7 | `0001140361-25-019770` | primary | 46 | 12,850 | 14 | 0 | 26 | yes | Items 1.01/2.03/3.02/7.01/9.01 capital and debt filing. Multi-item 8-K, tests item-code carry-through. |
| 8 | `0001801169-26-000009` | ex99-01 | 507 | 37,492 | 9 | 0 | 0 | yes | Q4-2025 earnings release. 507 KB, 1,417 `<font>` tags, 9 financial tables. The primary table-readability test. |
| 9 | `0001801169-25-000090` | ex99-01 | 477 | 35,753 | 9 | 0 | 0 | yes | Q3-2025 earnings release. Second period, so passage and table output can be compared across quarters. |
| 10 | `0001801169-26-000009` | ex99-02 | 42 | 37,728 | 0 | 13 | 0 | yes | Shareholder letter, 13 images, 85.8% text yield. The artifact that disproved the image-heavy assumption. |
| 11 | `0001801169-25-000016` | ex99-02 | 68 | 57,090 | 0 | 30 | 0 | yes | Shareholder letter with the highest image count in the corpus (30). Calibrates the image-heavy threshold. |
| 12 | `0001801169-26-000009` | ex99-03 | 5 | 2,208 | 0 | 5 | 0 | yes | Short supplemental, 2,208 chars with 5 images - closest artifact to tripping the image-heavy rule. |
| 13 | `0001140361-26-017460` | primary | 2186 | 339,636 | 375 | 40 | 217 | yes | 2026 proxy. 375 tables, most of them layout scaffolding. The layout-vs-data classification stress test. |
| 14 | `0001104659-21-057984` | primary | 1000 | 206,182 | 47 | 34 | 0 | yes | 2021 proxy with 2,083 `<font>` tags. Oldest and most unusual markup in the corpus. |
| 15 | `0001801169-26-000010` | ex21-01 | 10 | 511 | 1 | 0 | 0 | yes | Subsidiary list: 511 chars of text, 68 table rows. Empty unless tables are preserved structurally. |
| 16 | `0001140361-25-042984` | ex4-01 | 423 | 157,585 | 23 | 6 | 0 | yes | Description of securities. 157,585 chars, 23 tables. Explains the OPEN/OPENL/OPENW/OPENZ classes. |
| 17 | `0001104659-20-054449` | ex10-12 | 104 | 60,864 | 137 | 0 | 0 | yes | Material agreement, 137 tables inside 60 KB. Contract prose interleaved with dense tables. |
| 18 | `0001104659-20-054449` | ex3-01 | 261 | 104,648 | 323 | 0 | 0 | yes | Charter, 323 tables, legacy markup. Extreme layout-table density. |
| 19 | `0001801169-26-000010` | ex31-01 | 12 | 3,461 | 1 | 0 | 0 | no | Certification. Must be EXCLUDED by selection - the negative control. |

**Filenames**

1. `sec/0001801169/10-K/2026-02-19_0001801169-26-000010/source/open-20251231.htm`
2. `sec/0001801169/10-Q/2025-11-06_0001801169-25-000092/source/open-20250930.htm`
3. `sec/0001801169/10-Q/2020-11-05_0001104659-20-121496/source/tm2029660-1_10q.htm`
4. `sec/0001801169/8-K/2026-02-19_0001801169-26-000009/source/open-20260219.htm`
5. `sec/0001801169/8-K/2021-02-02_0001104659-21-010984/source/tm215021d1_8k.htm`
6. `sec/0001801169/8-K/2025-09-19_0001140361-25-035480/source/ef20055829_8k.htm`
7. `sec/0001801169/8-K/2025-05-19_0001140361-25-019770/source/ef20049190_8k.htm`
8. `sec/0001801169/8-K/2026-02-19_0001801169-26-000009/source/q42025formxex991earningsre.htm`
9. `sec/0001801169/8-K/2025-11-06_0001801169-25-000090/source/q32025formxex991earningsre.htm`
10. `sec/0001801169/8-K/2026-02-19_0001801169-26-000009/source/exhibit992-q42025form8xk.htm`
11. `sec/0001801169/8-K/2025-02-27_0001801169-25-000016/source/exhibit9924q24opendoorsh.htm`
12. `sec/0001801169/8-K/2026-02-19_0001801169-26-000009/source/exhibit993-4q25opendoors.htm`
13. `sec/0001801169/DEF-14A/2026-04-28_0001140361-26-017460/source/ny20064714x1_def14a.htm`
14. `sec/0001801169/DEF-14A/2021-04-30_0001104659-21-057984/source/tm2112233-1_def14a.htm`
15. `sec/0001801169/10-K/2026-02-19_0001801169-26-000010/source/a2025ex211xlistofsubsidiar.htm`
16. `sec/0001801169/8-K/2025-11-21_0001140361-25-042984/source/ef20059583_ex4-1.htm`
17. `sec/0001801169/8-K/2020-04-30_0001104659-20-054449/source/tm2017926d1_ex10-12.htm`
18. `sec/0001801169/8-K/2020-04-30_0001104659-20-054449/source/tm2017926d1_ex3-1.htm`
19. `sec/0001801169/10-K/2026-02-19_0001801169-26-000010/source/a202510-kexhibit311.htm`

---

# Coverage against the required categories

| Required | Covered by | Notes |
| --- | --- | --- |
| One 10-K | 1 | FY2025, the largest document |
| Two 10-Qs | 2, 3 | Deliberately one modern and one 2020-era |
| Several 8-K primaries | 4, 5, 6, 7 | Earnings carrier, inline results, leadership, capital |
| Several EX-99.1 releases | 8, 9 | Two consecutive quarters |
| Item 2.02 with inline results | 5 | The corpus outlier |
| Leadership-change filings | 6 | Item 5.02 CEO transition |
| A material agreement | 17 | EX-10.12, 137 tables |
| A capital or debt filing | 7 | Items 1.01/2.03/3.02 |
| One DEF 14A | 13, 14 | Two, because markup differs sharply by era |
| A subsidiary list | 15 | EX-21.1, the table-only document |
| Description of securities | 16 | EX-4.1 |
| Text-rich EX-99.2 / EX-99.3 | 10, 11, 12 | All three are text-rich (§1.3) |
| An image-heavy shareholder letter | 11 | 30 images — the maximum in the corpus |
| At least one difficult table | 13, 15, 18 | 375 layout tables; table-only; 323 tables |
| Malformed or unusual HTML | 3, 14, 18 | Legacy `<font>`, up to 2,083 tags |

Two categories deserve comment.

**"Image-heavy shareholder letter" is not what it sounds like.** Fixture 11 has the highest
image count in the corpus (30) yet still yields 57,090 characters of text at 81.5%. It is
included to calibrate the image-heavy threshold, not because its text is missing. The
closest artifact to genuinely tripping that rule is fixture 12 — EX-99.3, 2,208 characters
across 5 images.

**No artifact in this corpus is malformed** in the sense of failing to parse. All 377
narrative artifacts parsed without error under lxml. Fixtures 3, 14 and 18 stand in for
"unusual" instead: legacy `<font>`-based markup, extreme layout-table density, and the
absence of any semantic structure. That is the real difficulty here — not broken HTML, but
HTML that carries no structural information at all.

---

# What each fixture must demonstrate

| Fixture | Must show |
| --- | --- |
| 1 (10-K) | Multi-level hierarchy across Parts I–IV; zero hidden inline-XBRL text in any passage; 83 tables preserved |
| 2, 3 (10-Q) | Same hierarchy quality in both markup eras |
| 4 (8-K) | Short carrier document normalizes without inventing structure |
| 5 (inline results) | Substantive financial content extracted from the primary itself |
| 6 (Item 5.02) | Event-dense short document produces coherent, self-contained passages |
| 7 (capital) | Multiple item codes carried onto every passage |
| 8, 9 (EX-99.1) | Financial tables readable as Markdown; quarter-over-quarter output comparable |
| 10, 11 (EX-99.2) | Full narrative recovered; images flagged, not treated as content loss |
| 12 (EX-99.3) | Image-heavy heuristic behaves sensibly near its boundary |
| 13, 14 (DEF 14A) | Layout tables classified as layout without discarding data tables |
| 15 (EX-21.1) | **Every subsidiary survives as a structured table.** *Corrected during implementation:* the document has 68 `<tr>` but only **4 subsidiaries** — the rest are spacer rows. The criterion is that all 4 survive, not that 68 rows do. Still the sharpest test, because flattening the table empties the document |
| 16 (EX-4.1) | Long governance prose with interleaved tables stays ordered |
| 17 (EX-10.12) | Contract prose and dense tables both survive |
| 18 (EX-3.1) | 323 tables do not fragment the document into unusable passages |
| 19 (EX-31.1) | **Excluded at selection** with reason `BOILERPLATE_CERTIFICATION`. Never reaches the parser, so it is not required to normalize |

---

# Running the spike

Both parsers run over the **18 selected** fixtures in `comparison` mode, so the parser
protocol is proven to be a real seam and the outputs can be diffed. Fixture 19 is not
parsed at all in normal operation — it is rejected at selection, which is exactly what it
is there to demonstrate:

```bash
# selection first: 18 included, fixture 19 excluded as BOILERPLATE_CERTIFICATION
python -m normalization select --fixtures plans/normalization/spike_fixtures.txt

# both parsers over the 18 selected, outputs diffed
python -m normalization spike --mode comparison
```

A separate parser-level test may hand fixture 19 straight to a parser to confirm
certifications do not crash it, but that is a parser test and sits outside the
normalization pipeline.

Output goes to `data/normalization_reports/<run_id>-review.md`, reviewed against the
dimensions in V1 §15, with findings recorded in `normalization_review.jsonl`.

The full run over the 294 selected artifacts happens only after V1 §16 passes.
