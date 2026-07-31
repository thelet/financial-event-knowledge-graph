# Opendoor concept and metric research

**Status:** research and documentation only. No extraction, ontology-loading, graph, or
database code was written.

**Purpose:** evidence base for designing the first executable ontology and extraction schema
for the Opendoor financial event knowledge graph.

Facts marked *(corpus)* come from the normalized corpus built by
[../normalization/V1_DOCUMENT_NORMALIZATION.md](../normalization/V1_DOCUMENT_NORMALIZATION.md)
— 294 documents, 14,203 passages, 2,987 tables, filings 2020-04-30 to 2026-06-12. Facts
marked *(XBRL)* come from the 97 taxonomy schemas in the acquired raw corpus. Facts marked
*(external)* come from the sources named in §2 and are dated.

---

# 1. Executive summary

Six findings shape the ontology more than anything else.

**1. Opendoor's operating KPIs do not exist in XBRL.** *(XBRL)* Across **336 distinct custom
`open:` elements** in every 10-K and 10-Q from FY2020 to Q1 2026, there is **no** tag for
homes sold, homes purchased, contribution profit, contribution margin, adjusted gross
profit, or the 120-day aging metric. A search for those strings across all 97 taxonomy files
returns **zero hits**. The earnings 8-K that carries the KPI tables has 32 inline-XBRL facts,
**all of them `dei:` cover-page tags**, and the EX-99.1 exhibit holding the actual numbers
has no inline XBRL at all. Every KPI in this graph must come from narrative-table
extraction. This is the single most consequential finding for extraction design.

**2. What *is* structurally available is the GAAP spine, not the KPI layer.** *(XBRL)* The
FY2025 10-K carries 535 distinct inline concepts — 377 `us-gaap:`, 89 `open:`, 39 `dei:` —
including `us-gaap:InventoryRealEstate`, `us-gaap:GrossProfit`, `us-gaap:CostOfRevenue`,
plus custom `open:InventoryRealEstateValuationAdjustmentDecreaseIncrease` and
`open:InventoryRealEstateInResaleContract`. The graph should source GAAP amounts from XBRL
and everything else from narrative, and record which lane each value came from.

**3. Non-GAAP definitions drifted, and the drift is silent.** *(corpus)* Adjusted Gross
Profit in FY2020 was "gross profit under GAAP adjusted for (1) inventory **impairment** in
the current period, (2) inventory **impairment** in prior periods, plus (3) **restructuring
in cost of revenue**". By Q1 2025 the reconciliation uses "inventory **valuation
adjustment** — Current Period / Prior Periods" and **no restructuring line**. Same metric
name, different formula and different vocabulary. Any ontology that stores a bare
`AdjustedGrossProfit` number without a versioned formula will silently mix two definitions.

**4. The 120-day metric has three different denominator wordings.** *(corpus)* The 10-K/10-Q
MD&A says such homes "represented 8% of **our portfolio**"; shareholder letters say "**our
homes** had been listed"; the Q1 2023 letter says "**our homes in inventory** had been
listed". The published table label is fixed — `Percentage of homes "on the market" for
greater than 120 days (at period end)` — but the population it divides by is not stated
identically across sources. This must be recorded as an ambiguity, not resolved by
assumption.

**5. `accountable.opendoor.com` is not reproducible evidence.** *(external, 2026-08-01)* The
live dashboard shows one metric — weekly acquisition contract tracking, currently "+6% vs
previous week" — with **no as-of date, no archive, no download, no methodology link, and no
stated data source**, and it explicitly describes itself as "an accountability initiative
based on estimates". Wayback holds **9 daily-collapsed snapshots** between 2025-11-06 and
2026-07-26, some of them HTTP 522 failures — roughly a quarter of the weeks in a weekly
series. It is a discovery source, never canonical evidence.

**6. FIBO covers Opendoor's capital structure well and its operations not at all.** Credit
facility, revolving line of credit, lender, borrower, collateral, publicly held company and
common share are **direct fits with verified URIs**. There is **no** FIBO class for homes
sold, contribution margin, or inventory aging, and `EconomicIndicator` must not be stretched
to cover them — FIBO defines it as a measure of economic activity *in a statistical region*,
which a company KPI is not.

**Recommendation for ontology v1:** 24 concepts (§19), of which 9 map directly to FIBO with
verified URIs, 13 require custom real-estate-marketplace definitions, and 2 are adopted as
FIBO *patterns* rather than FIBO classes. A metric-definition / metric-observation split
(§16) is essential and is the main structural decision this report asks you to approve.

---

# 2. Sources and methodology

## 2.1 Primary — the normalized corpus

| Artifact | Scale |
| --- | --- |
| `data/normalization_catalog/documents.jsonl` | 294 documents |
| `data/normalization_catalog/passages.jsonl` | 14,203 passages (11,216 narrative, 2,987 table) |
| Per-document normalized JSON | 294 files, 9,629 sections |
| Coverage | 109 primary, 45 EX-99.1, 26 EX-99.2, 22 EX-99.3, 60 EX-10.x, 7 EX-21.1 |

Method: regex sweep across **all 14,203 passages** for 26 concept families, then manual
reading of representative passages per concept across the full 2020–2026 range rather than
only the most recent filing. 30 passages were read closely and recorded verbatim in
`research/reviewed_passages.jsonl`.

Keyword counts were used only to locate candidates. Every conclusion below rests on a read
passage, cited with passage id, accession, form, date and role.

**Two disambiguation hazards found and recorded**, both of which would corrupt a naive
keyword extractor:

- `"more than 120 days"` appears in charter and bylaw **notice windows** — "not less than 90
  days nor more than 120 days prior to the one-year anniversary" (EX-3.1, `0001104659-20-054449`
  p15; EX-3.1 `2023-01-24` p6). Unrelated to inventory aging.
- `"eXp"` matches inside *expenses*, *expected*, *experience* — 4,058 passages, essentially
  all false positives. Partner-name matching needs word boundaries and case sensitivity.

## 2.2 FIBO

Repository: `https://github.com/edmcouncil/fibo` (MIT licence; development on `master`,
formal releases quarterly). Class definitions were read from **raw ontology RDF**, not from
the viewer or from memory. Every URI in this report was returned by fetching the file that
declares it. Where a class does **not** exist, that is stated rather than invented — see
Warrant in §17.

## 2.3 Opendoor materials

| Source | Date | Classification |
| --- | --- | --- |
| Filed EX-99.1 earnings releases (in corpus) | 2020-09-15 → 2026-05-07 | **canonical evidence** |
| Filed EX-99.2 shareholder letters (in corpus) | 2020-09-15 → 2026-05-07 | **canonical evidence** |
| 10-K / 10-Q MD&A (in corpus) | 2021-03-04 → 2026-05-07 | **canonical evidence** |
| DEF 14A (in corpus) | 2021-04-30 → 2026-04-28 | **canonical evidence** |
| `accountable.opendoor.com` | fetched 2026-08-01; launched Nov 2025 | **discovery-only** (§7) |

Every metric definition in this report was recovered from the **filed** corpus. No definition
depends on a web page.

## 2.4 SEC / XBRL

97 `.xsd` schemas and 97 `_lab.xml` label linkbases from the acquired corpus, covering every
10-K and 10-Q FY2020–Q1 2026, plus the inline-XBRL facts in the primary documents themselves.
Inventory in `research/xbrl_tag_inventory.jsonl`.

## 2.5 Research artifacts produced

```text
plans/ontology/research/
├── concept_occurrences.jsonl   566 time-spread samples across 26 concepts (5,656 total hits)
├── metric_aliases.json         9 metrics with canonical labels, aliases, formulas, drift notes
├── xbrl_tag_inventory.jsonl    336 custom open: elements with first/last period and KPI flag
├── fibo_candidate_mappings.jsonl  16 mappings with verified URIs and fit assessment
└── reviewed_passages.jsonl     30 closely-read passages with verbatim quotes
```

---

# 3. Findings from the normalized corpus

Concept frequency across all 14,203 passages *(corpus)*:

| Concept family | Passages | Documents | First | Last |
| --- | --- | --- | --- | --- |
| Inventory valuation adjustment / impairment | 740 | 86 | 2020-09-15 | 2026-05-07 |
| Asset-backed debt | 663 | 87 | 2021-03-04 | 2026-05-07 |
| Homes sold | 520 | 86 | 2020-09-15 | 2026-05-07 |
| Adjusted EBITDA | 479 | 90 | 2020-09-15 | 2026-05-07 |
| Credit facility | 419 | 66 | 2020-04-30 | 2026-05-07 |
| Contribution Profit | 411 | 78 | 2020-09-15 | 2026-05-07 |
| Contribution Margin | 401 | 87 | 2020-09-15 | 2026-05-07 |
| Holding costs | 374 | 84 | 2020-09-15 | 2026-05-07 |
| Adjusted Gross Profit | 333 | 80 | 2020-09-15 | 2026-05-07 |
| Adjusted Gross Margin | 190 | 84 | 2020-09-15 | 2026-05-07 |
| Borrowing capacity | 174 | 39 | 2021-03-04 | 2026-05-07 |
| Homes in inventory | 116 | 75 | 2021-03-04 | 2026-05-07 |
| Home price appreciation | 109 | 43 | 2021-03-04 | 2026-05-07 |
| Buybox | 93 | — | 2022-02-24 | 2026-05-07 |
| Homes under contract / acquisition contracts | 67 | 30 | 2021-03-04 | 2026-05-07 |
| Homes purchased | 65 | 69 | 2021-03-04 | 2026-05-07 |
| **% homes on market > 120 days** | **56** | **47** | 2022-02-24 | 2026-05-07 |
| Mortgage rate | 41 | 28 | 2020-09-15 | 2026-05-07 |
| accountable.opendoor.com | 14 | 13 | 2025-11-06 | 2026-05-07 |
| Days on market | 10 | 12 | 2022-08-04 | 2025-11-06 |
| Management objective framework | 3 | 3 | 2025-11-06 | 2026-05-07 |

Two structural observations:

**The KPI layer lives in EX-99.1 and EX-99.2, not in the 10-K.** The recurring
"Key Metrics"/"Financial Highlights" table appears in every earnings 8-K exhibit and is
mirrored — with fewer comparative columns — in the 10-K/10-Q MD&A. Extraction should target
the exhibit tables and use MD&A as corroboration.

**"Housing turnover" returns zero hits.** The macro concept exists in the plan's ontology
sketch but Opendoor does not use that phrase. It appears as "home sales volume", "existing
home sales" and "transaction volumes". Do not create a concept from a phrase the corpus does
not contain.

---

# 4. FIBO modules reviewed

| Module | File read | Outcome |
| --- | --- | --- |
| BE / LegalEntities / CorporateBodies | `BE/LegalEntities/CorporateBodies.rdf` | 15 classes; `Corporation`, `PubliclyHeldCompany`, `StockCorporation` adopted |
| FBC / DebtAndEquities / Debt | `FBC/DebtAndEquities/Debt.rdf` | `CreditFacility`, `RevolvingLineOfCredit`, `Lender`, `Borrower`, `Collateral`, `CreditAgreement`, `BorrowingCapacity`, `hasCreditLimit`, `hasAvailableAmount` — the strongest fit in FIBO for this corpus |
| LOAN / LoansGeneral | `LOAN/LoansGeneral/Loans.rdf` | `Loan`, `SecuredLoan`, `CollateralizedLoan`, `OpenEndCredit`. **Does not define** credit facility, lender or borrower — those are in FBC |
| FBC / ProductsAndServices / ClientsAndAccounts | `.../ClientsAndAccounts.rdf` | Account-centric roles only; not needed for v1 |
| SEC / Equities / EquityInstruments | `SEC/Equities/EquityInstruments.rdf` | `Share`, `CommonShare`, `ListedShare`. **No Warrant class** |
| IND / EconomicIndicators | `IND/EconomicIndicators/EconomicIndicators.rdf` | `EconomicIndicator`, `hasIndicatorValue`, `hasBaselinePopulation`, `hasComparisonPopulation` |

Domains listed but **not** inspected because nothing in the corpus needs them: DER
(derivatives), MD (market data), CAE (corporate actions), BP (business process).

---

# 5. SEC / XBRL findings

## 5.1 Custom-tag inventory *(XBRL)*

336 distinct `open:` elements across all 10-K and 10-Q filings. Every element whose name
contains Home, Market, Inventory, Contract or Days:

| Custom element | First | Last | Label | Verdict |
| --- | --- | --- | --- | --- |
| `NumberOfHomeTransactionsCompleted` | 2020-12-31 | 2021-06-30 | "Number of home transactions completed" | **Not the KPI.** Value is `80,000` — cumulative homes transacted since inception, a narrative milestone. **Discontinued after Q2 2021** |
| `NumberOfCurrentlyOperatedMarkets` | 2020-12-31 | 2021-06-30 | "Number of currently operated markets" | Market count, instant. **Discontinued after Q2 2021** |
| `InventoryRealEstateInResaleContract` | 2022-03-31 | 2026-03-31 | "Under contract for sale" | Homes/inventory under **resale** contract — the sell side, not the buy side. Active, 17 filings |
| `InventoryRealEstateValuationAdjustmentDecreaseIncrease` | 2022-09-30 | 2026-03-31 | "Valuation adjustments" | The IVA feeding Adjusted Gross Profit. Active, 15 filings |
| `InventoryRealEstateFinishedGoods` | 2020-12-31 | 2021-12-31 | — | Discontinued |
| `NumberOfDaysOfGuaranteeOnHomeSales` | 2020-12-31 | 2021-12-31 | — | Product feature, not a KPI |

The remaining ~22 name-matching elements are about *marketable securities*, *market
condition* equity awards, or *at-the-market* offerings — pure substring collisions with no
operating meaning.

**Tag-name change over time:** two operating tags were introduced in FY2020 and abandoned by
Q3 2021; two inventory tags were introduced in 2022 and persist. The graph must therefore
key XBRL facts on `(element, period)` and expect coverage gaps, not assume a stable series.

## 5.2 What is *not* tagged

Search across all 97 taxonomy files:

| String | Files containing it |
| --- | --- |
| `HomesSold` | **0** |
| `HomesPurchased` | **0** |
| `ContributionProfit` | **0** |
| `ContributionMargin` | **0** |
| `AdjustedGross` | **0** |
| `OnTheMarket` | **0** |
| `DaysOnMarket` | **0** |
| `HomesUnderContract` | **0** |
| `NumberOfHomes` | **0** |

## 5.3 Where the numbers actually are

*(XBRL)* Earnings 8-K primary `open-20260219.htm`: 32 inline facts, 23 distinct, **every one
`dei:`** — `Security12bTitle`, `TradingSymbol`, `EntityCentralIndexKey` and similar cover-page
data. The KPI tables live in `q42025formxex991earningsre.htm` (EX-99.1), which contains **no
inline XBRL**.

FY2025 10-K `open-20251231.htm`: 535 distinct concepts — 377 `us-gaap:`, 89 `open:`, 39
`dei:`. Structurally available and directly useful: `us-gaap:InventoryRealEstate`,
`us-gaap:GrossProfit`, `us-gaap:CostOfRevenue`, `us-gaap:InventoryAdjustments`,
`open:InventoryRealEstateValuationAdjustmentDecreaseIncrease`,
`open:InventoryRealEstateInResaleContract`.

**Consequence for sourcing:** GAAP revenue, gross profit, cost of revenue and inventory come
from XBRL with units and periods attached. Every non-GAAP measure and every operating KPI
comes from narrative tables and must carry its own unit, period and evidence.

---

# 6. Opendoor-specific metric definitions *(all from filed documents)*

| Measure | Formula as filed | GAAP? |
| --- | --- | --- |
| **Adjusted Gross Profit** *(FY2020 wording)* | GAAP gross profit **+** inventory impairment current period **−** inventory impairment prior periods **+** restructuring in cost of revenue | non-GAAP |
| **Adjusted Gross Profit** *(Q1 2025 reconciliation)* | GAAP gross profit **+** inventory valuation adjustment — Current Period **−** inventory valuation adjustment — Prior Periods | non-GAAP |
| **Adjusted Gross Margin** | Adjusted Gross Profit ÷ Revenue | non-GAAP |
| **Contribution Profit (Loss)** | Adjusted Gross Profit (Loss) **−** holding costs incurred in the current period **−** holding costs incurred in prior periods **−** direct selling costs — all on homes sold during the current period | non-GAAP |
| **Contribution Margin** | Contribution Profit (Loss) ÷ Revenue | non-GAAP |
| **Contribution Profit After Interest** | Contribution Profit **−** interest expense under senior revolving credit facilities incurred on the homes sold during the period (may include interest recorded in prior periods) | non-GAAP |

Verbatim, 2024-05-02 10-Q p123 *(corpus)*:

> "We calculate Contribution Profit (Loss) as Adjusted Gross Profit (Loss), minus certain
> costs incurred on homes sold during the current period including: (1) holding costs
> incurred in the current period, (2) holding costs incurred in prior periods, and (3)
> direct selling costs. … Contribution Margin is Contribution Profit (Loss) as a percentage
> of revenue."

Verbatim, 2021-03-04 EX-99.1 p22 *(corpus)* — the intent statement that explains the whole
family:

> "Each of these measures is intended to present the economics related to homes sold during
> a given period. We do so by including revenue generated from homes sold (and adjacent
> services) in the period and only the expenses that are directly attributable to such home
> sales, even if such expenses were recognized in prior periods, and excluding expenses
> related to homes that remain in inventory as of the end of the period."

**This is a cohort measure, not a period measure.** It deliberately pulls costs from prior
periods and excludes costs on unsold inventory. The ontology must attach these to a
**resale cohort**, not merely to a fiscal quarter, or the semantics are lost. The company
says so explicitly: "Contribution Profit helps management assess inflows and outflows
directly associated with a specific resale cohort."

---

# 7. `accountable.opendoor.com` analysis

## 7.1 What the corpus says *(corpus)*

DEF 14A 2026-04-28 (`0001140361-26-017460`) p26 and p75:

> "In November 2025, we also launched a dashboard at accountable.opendoor.com, where
> stockholders are able to track in real time our weekly acquisition contract performance
> and product, feature and partnership launches."

EX-99.1 2026-02-19 (`0001801169-26-000009`) p10 — Reg FD channel designation:

> "Opendoor investors and others should note that we have used, and intend to continue to
> use, our website (including accountable.opendoor.com and investor.opendoor.com), press
> releases, Securities and Exchange Commission ('SEC') filings, blogs, community hub and
> social media accounts, as well as the X … accounts of its Chief Executive Officer …"

EX-99.1 2026-02-19 p8 — **guidance partially delegated to the dashboard**:

> "Q1 2026 Financial Outlook: … Acquisitions: You can track our acquisition contracts on
> accountable.opendoor.com."

## 7.2 What the live site says *(external, fetched 2026-08-01)*

One metric: weekly acquisition contract tracking, "+6% vs previous week", with a chart
labelled "Post Kaz", "Previous Year", "Projected trend". Also product updates and leadership
tweets. **No as-of date, no data source, no methodology or definitions link, no archive, no
download.** Self-describes as "an accountability initiative based on estimates"; states the
company "does not plan to update any of the content except as required by law". It does
supply one useful definition: acquisition contracts means "any contract where Opendoor will
acquire a property".

## 7.3 Archive availability *(external, 2026-08-01)*

Wayback CDX returns **9 daily-collapsed snapshots**, earliest `2025-11-06`, latest
`2026-07-26`, status codes `200`, `301`, `522`. For a weekly series spanning ~38 weeks that
is roughly a quarter coverage, with some captures being server errors.

## 7.4 Recommendation

**Treat as a discovery source, not evidence. Do not create an `Accountable` node in v1.**

Reasoning: the values are estimates, undated, unarchived and mutable; the same underlying
concept (acquisition contracts) *is* reported in filed exhibits with hard numbers — "5,000+
acquisition contracts in 1Q 2026; double 4Q 2025" (EX-99.1 2026-05-07 p3) — which is
reproducible evidence. Sourcing a graph fact from a page whose value changes weekly and
whose history is 25% recoverable would break the corpus-rebuildability guarantee the whole
pipeline is built on.

**Where it *does* belong:** as a `DisclosureChannel` value on the provenance of a
`ManagementObjective` (§14), because the filed documents *name* it as the tracking mechanism.
The graph records "Opendoor said this objective is tracked at accountable.opendoor.com" —
which is a filed, citable claim — without ingesting any dashboard value.

If dashboard values are ever wanted, they must first be captured into the acquisition corpus
with a fetch timestamp and content hash, exactly as SEC artifacts are. That is a separate
`DocumentSource` adapter, not a normalization concern.

---

# 8. Homes greater than 120 days

## 8.1 Exact wording *(corpus)*

**Canonical table label**, unchanged 2024-02-15 → 2026 and used in EX-99.1, EX-99.2, 10-K and
10-Q tables:

> `Percentage of homes "on the market" for greater than 120 days (at period end)`

**Canonical definition**, 10-K/10-Q MD&A, e.g. 2022-02-24 10-K p95 and 2024-08-01 10-Q p116:

> "One such metric is our percentage of homes 'on the market' for greater than 120 days (as
> measured from initial listing date). As of June 30, 2024, such homes represented 14% of our
> portfolio, compared to 15% for the broader market…"

## 8.2 Determinations

| Question | Answer | Confidence |
| --- | --- | --- |
| Exact wording | `Percentage of homes "on the market" for greater than 120 days (at period end)` | high |
| Comparison operator | **strictly `>`** — "greater than", "more than", "over" throughout; never "at least" or "≥" | high |
| Threshold | 120 days | high |
| Measured from | **initial listing date** | high |
| Unit | percent | high |
| Count also published? | **No.** Only a percentage in every instance found | high |
| Period type | **instant**, "at period end" / "as of <date>" | high |
| Reporting frequency | quarterly — "(Reported quarterly)" in the Q3 2025 and Q4 2025 objective tables | high |
| **Denominator** | **ambiguous** — "our portfolio" (MD&A), "our homes" (letters), "our homes in inventory" (Q1 2023 letter) | **low** |
| Definition changed? | Label stable since at least FY2021; denominator *wording* varies by document type | medium |
| In XBRL? | **No** — zero hits across 97 taxonomy files | high |
| Where it appears | EX-99.1 tables, EX-99.2 letters, 10-K/10-Q MD&A, and the Accountable objective table | high |

## 8.3 The comparison population is a separate observation

Every occurrence pairs the company figure with a market figure: "compared to 24% for the
broader market, **adjusted for our buybox**" (2022-02-24 10-K p95); "versus 33% for the
overall market" (2026-05-07 EX-99.1 p5). *(corpus)*

These are **two observations of the same metric definition with different populations**, not
one fact with a footnote. "Buybox" (93 passages) is Opendoor's acquisition criteria filter,
and the market comparison is explicitly restricted to it.

## 8.4 Observed series *(corpus)*

| As of | Opendoor | Market (buybox-adj.) | Source |
| --- | --- | --- | --- |
| 2021-12-31 | 8% | 24% | 10-K 2022-02-24 p95 |
| 2022-03-31 | 7% | 24% | 10-Q 2022-05-05 p126 |
| 2022-06-30 | 5% | 14% | EX-99.2 2022-08-04 p7 |
| 2022-09-30 | 21% | 15% | 10-Q 2022-11-03 p130 |
| 2022-12-31 | 55% | 33% | EX-99.2 2023-02-23 p10 |
| 2023-03-31 | 59% | 23% | EX-99.2 2023-05-04 p8 |
| 2023-06-30 | 24% | 16% | EX-99.2 2023-08-03 p8 |
| 2023-09-30 | 12% | 15% | EX-99.2 2023-11-02 p11 |
| 2023-12-31 | 18% | 21% | EX-99.2 2024-02-15 p9 |
| 2024-03-31 | 15% | 19% | EX-99.2 2024-05-02 p7 |
| 2024-06-30 | 14% | 15% | EX-99.2 2024-08-01 p7 |
| 2024-09-30 | 23% | 18% | EX-99.2 2024-11-07 p5 |
| 2024-12-31 | 46% | — | EX-99.1 2025-02-27 p16 |
| Q4 2025 | 51% → 33% QoQ | — | EX-99.1 2026-02-19 p4 |
| Q1 2026 | 33% → 10% QoQ | 33% | EX-99.1 2026-05-07 p5 |

## 8.5 Recommended model

**Not** a string, and **not** a property on an inventory snapshot. Model as an
`OperatingMetricDefinition` plus `OperatingMetricObservation`, with the threshold semantics
on the *definition* so they cannot drift per observation:

```text
OperatingMetricDefinition
  local_id           pct_homes_on_market_gt_120_days
  label              Percentage of homes "on the market" for greater than 120 days
  unit               percent
  period_type        instant                       # "at period end"
  comparison_operator ">"                          # never collapse to "120 days"
  threshold_value    120
  threshold_unit     day
  measured_from      initial_listing_date
  numerator          homes on the market longer than the threshold
  denominator        AMBIGUOUS - see 8.2; record the wording per observation
  is_gaap            false
  first_seen         2022-02-24 (FY2021 10-K)

OperatingMetricObservation
  metric             -> pct_homes_on_market_gt_120_days
  subject            -> Company:Opendoor        | or  Population:MLS-buybox-adjusted
  population_role    baseline | comparison       # FIBO IND pattern, §17
  observation_date   2024-06-30                  # instant
  reporting_period   -> FiscalPeriod Q2 2024
  value              14.0
  unit               percent
  denominator_wording "our portfolio"            # verbatim, per observation
  source             -> Passage / TableBlock
```

Recording `denominator_wording` verbatim per observation is how the §8.2 ambiguity is
preserved rather than silently resolved.

---

# 9. Homes sold

| Question | Answer *(corpus)* | Confidence |
| --- | --- | --- |
| Label | "Homes sold" / "Homes Sold"; early decks also "Resale Closes" | high |
| Meaning | Homes resold by Opendoor in the period. **No explicit definition of the recognition point** (title transfer vs closing) was found in any filing | **low** |
| Period type | duration (quarter and year) | high |
| Unit | homes (count) | high |
| Frequency | quarterly and annual | high |
| In XBRL? | **No.** `NumberOfHomeTransactionsCompleted` is *not* this metric — its value is 80,000, a cumulative-since-inception milestone, and it was discontinued after Q2 2021 | high |
| Ties to | revenue, Contribution Profit (the "resale cohort"), inventory | high |

Evidence of the cohort tie, 2021-05-11 EX-99.3 p1 *(corpus)* — the Key Metrics table places
them together: `Homes Purchased 2,892 461 799 2,016 3,594 / Homes Sold 4,908 2,924 1,232 849
2,462 / Homes in Inventory 3,557 …`.

**Recommendation:** `OperatingMetricDefinition` + `OperatingMetricObservation`, exactly the
model sketched in the task. Do not create a node per quarterly value. Flag the recognition
point as an open ambiguity (§22).

---

# 10. Homes purchased

| Question | Answer *(corpus)* | Confidence |
| --- | --- | --- |
| Label | "Homes purchased" / "Homes Purchased"; also "home acquisition volumes" | high |
| Meaning | **Closed acquisitions**, not contracts signed — distinct from "in contract to purchase" which is reported separately in the same documents | high |
| Period type | duration | high |
| Unit | homes | high |
| In XBRL? | **No** | high |

**The corpus proves these must not be merged.** 2021-08-11 EX-99.2 p6 *(corpus)*:

> "…we ended the quarter with 8,158 homes under contract to purchase, representing an
> aggregate purchase price of $2,962 million, which is over double last quarter. Homes under
> contract is another strong indicator of home acquisition momentum, as we would generally
> expect to close on these homes in the following quarter."

And 10-K 2021-03-04 p398 *(corpus)*: "As of December 31, 2020, the Company was in contract to
purchase 1,742 homes for an aggregate purchase price of $466.4 million."

So there are **four distinct concepts**, and the ontology needs all four:

```text
HomesPurchased       duration, count   closed acquisitions in the period
HomesUnderContract   instant,  count   signed but not yet closed, at period end
                                        + companion aggregate purchase price (USD)
AcquisitionContracts duration, count   contracts signed in a period (weekly on Accountable,
                                        quarterly in filings: "5,000+ in 1Q 2026")
HomesInInventory     instant,  count   owned and unsold at period end
```

`HomesUnderContract` is the only one of the four carrying **two units** (a count and a
dollar amount) for the same observation — the model must allow a companion measure rather
than forcing two unrelated observations.

**Observation or event?** Both, and they must be linked rather than duplicated. The Q1 2026
DEF 14A p86 *(corpus)* — "we increased our homes purchased by 46% quarter-over-quarter" —
is a **derived fact** (a QoQ delta) asserted inside a narrative about a **leadership change**.
§16.2 and §16.3 define how those connect without triplicating the number.

---

# 11. Contribution Profit and Contribution Margin

Formulas are in §6. Determinations:

| Question | Answer *(corpus)* |
| --- | --- |
| GAAP? | Explicitly non-GAAP, "supplemental measures used by management in evaluating unit level economics" |
| Numerator / denominator of the margin | Contribution Profit ÷ **Revenue** (not adjusted revenue) |
| Are prior-period costs included? | **Yes, deliberately** — holding costs from prior periods on homes sold this period are subtracted; costs on unsold inventory are excluded |
| Reconciled to | GAAP Gross Profit, in a reconciliation table in every earnings release |
| Formula changed? | **Yes.** FY2020 Adjusted Gross Profit included a restructuring adjustment and used "inventory impairment"; the Q1 2025 reconciliation uses "inventory valuation adjustment" and has no restructuring line |
| Percentage? | Contribution Margin and Adjusted Gross Margin are percentages; Contribution Profit and Adjusted Gross Profit are USD |
| In XBRL? | **No** custom tag for any of them |

**The six must stay separate**, as the task requires. The Q1 2025 reconciliation table
(EX-99.3 2025-05-06 p3) shows all of them coexisting with different values *(corpus)*:

> "Revenue (GAAP) $1,153 … Gross profit (GAAP) $99 … Gross Margin 8.6% … Adjusted Gross
> Profit $100 … Adjusted Gross Margin 8.7% …"

so `GAAPGrossProfit` 99 ≠ `AdjustedGrossProfit` 100, and `GAAPGrossMargin` 8.6% ≠
`AdjustedGrossMargin` 8.7%, in the same table, same period. A generic `Margin` node would
destroy this.

**Recommended model — several connected objects, not one:**

```text
NonGAAPMeasureDefinition  contribution_margin
  unit percent | period_type duration | is_gaap false
  reconciles_to -> us-gaap:GrossProfit
  formula_version cm_v2 (effective 2022-Q1 .. present)
    expression  ContributionProfit / Revenue
  formula_version cm_v1 (2020-Q3 .. 2021-Q4)     # same expression, different AGP inputs

MetricFormulaVersion  agp_v1 / agp_v2
  agp_v1  GrossProfit + InventoryImpairmentCurrent - InventoryImpairmentPrior
                      + RestructuringInCostOfRevenue
  agp_v2  GrossProfit + InventoryValuationAdjustmentCurrent
                      - InventoryValuationAdjustmentPrior

OperatingMetricObservation  value 8.7, period Q1-2025, formula_version -> agp_v2,
                            source -> TableBlock in EX-99.3
```

Binding each observation to a `MetricFormulaVersion` is what makes the §6 drift visible
instead of silent.

---

# 12. Other recurring operating metrics

| Concept | Corpus support | Category | v1 |
| --- | --- | --- | --- |
| Inventory (balance, USD) | 116+ passages; `us-gaap:InventoryRealEstate` in XBRL | metric observation | **include** |
| Homes in inventory (count) | Key Metrics table, every quarter | metric observation | **include** |
| Inventory Valuation Adjustment | 740 passages; `open:InventoryRealEstateValuationAdjustment…` | metric observation + XBRL fact | **include** |
| Holding costs | 374 passages; a Contribution Profit input | metric observation | **include** |
| Direct selling costs | 82 passages; a Contribution Profit input | metric observation | **include** |
| Adjusted EBITDA | 479 passages, 90 documents | non-GAAP metric | **include** |
| Adjusted Net Income | 91 passages; the stated 2026 target | non-GAAP metric | **include** |
| Buybox | 93 passages | **property** of a comparison population, not a node | property |
| Days on market | only 10 passages | superseded in practice by the 120-day metric | defer |
| Resale velocity | 11 passages, mostly as a narrative objective name | narrative theme, not a measured metric | defer |
| Revenue per home / cost per home | not reported directly; derivable | derived metric | defer |
| Acquisition cohort / home sale cohort | implied by the Contribution Profit definition, never tabulated | needed as a **link target**, not a reported metric | **include as entity** |
| Conversion rate, offers, seller requests | **no corpus support** | — | reject |
| Return on inventory | phrase appears in intent statements only, never as a number | — | defer |

---

# 13. Entity candidates

| Local class | Evidence *(corpus)* | FIBO | v1 |
| --- | --- | --- | --- |
| `Company` | Opendoor + 7 EX-21.1 subsidiary lists; peers named in DEF 14A | `PubliclyHeldCompany` / `Corporation` — **direct** | include |
| `Person` | CEO Kaz Nejatian, board members (DEF 14A, Item 5.02 8-Ks) | FND (not inspected) — use local + Wikidata id | include |
| `Market` (metro) | 64 passages naming metros; Phoenix 42, Portland 19, Austin 18, Raleigh 17 | none | include (custom) |
| `CreditFacility` | 419 passages; named facilities with lenders and capacity | `CreditFacility` / `RevolvingLineOfCredit` — **direct** | include |
| `Security` | OPEN, OPENL, OPENW, OPENZ | `CommonShare` / `ListedShare` — direct; **no Warrant class** | include |
| `Lender` (role) | Credit Suisse 27, Barclays 17, Goldman 9 passages | `Lender` — **direct**, and correctly a *role* | include |
| `Partner` (role) | Zillow 70 passages (2022→2026), Redfin 20, eBay 3 | none | include (custom role) |
| `Regulator` | SEC, Nasdaq, FTC, CFPB in risk factors | BE (not inspected) | defer |
| `ResaleCohort` | required by the Contribution Profit definition | none | include (custom) |
| `Product` / `Feature` | Opendoor Exclusives, Marketplace, Cash Now, List with Opendoor, Key Connect | none | include (custom) |
| `FiscalPeriod` | every table column | none needed | include (custom) |

Note on markets: the corpus reports a **count** (21 markets in 2020, 50 in 2024) far more
often than it names individual metros. Both matter — the count is a metric observation, the
named metros are entities.

---

# 14. Event candidates

Grounded in the acquisition layer's SEC item codes (which are authoritative filing-level
metadata) plus corpus evidence:

| Event | Item code | Corpus support | v1 |
| --- | --- | --- | --- |
| `EarningsResult` | 2.02 (23 filings) | every earnings 8-K | include |
| `GuidanceChange` | 2.02 / 7.01 | 175 "guidance" passages; explicit "Financial Outlook" sections | include |
| `LeadershipChange` | 5.02 (**18 filings**) | CEO transition 2025-09-19 | include |
| `MaterialAgreement` | 1.01 (7) | EX-10.x exhibits | include |
| `DebtFacilityChange` | 1.01 / 2.03 (2) | 419 credit-facility passages | include |
| `CapitalRaise` | 3.02 (7) | ATM offerings, convertible notes, warrants | include |
| `ListingComplianceEvent` | 3.01 (2) | Nasdaq minimum-bid notices | include |
| `StrategyChange` | 7.01 / 8.01 | "Opendoor 2.0", management objectives | include |
| `MarketEntry` / `MarketExit` | — | 33 passages; market count 21→50 | include |
| `ProductLaunch` / `FeatureLaunch` | — | Exclusives, Marketplace, Cash Now | include |
| `Partnership` | 1.01 / 8.01 | Zillow 2022→2026 | include |
| `Acquisition` | 2.01 (1) | one only | defer |
| `RestructuringEvent` | — | workforce reductions, `RestructuringAndRelatedCost…HeadcountPercent` in XBRL | include |
| `RegulatoryEvent` | — | FTC settlement referenced in risk factors | defer |

**New candidate the corpus surfaced:** `ManagementObjective`. From Q3 2025 onward the
earnings release opens with a three-row table — "Management Objective | Why This Matters |
How You Can Hold Us Accountable" (2025-11-06 EX-99.1 p3), becoming "Management Objective |
How You Can Hold Us Accountable | How We're Executing" (2026-02-19 p3). The three objectives
are Scale Acquisitions, Improve Unit Economics and Resale Velocity, Build Operating Leverage.
This is a durable, company-declared accountability framework that binds *objectives* to
*specific metrics* to *reported outcomes* — exactly the shape an event graph wants, and it
names the metrics that matter to management. **Include in v1.**

---

# 15. Relationship candidates

```text
Company        -[OPERATES_IN {from,to}]->            Market
Company        -[BORROWER_UNDER]->                   CreditFacility
Company        -[LENDER_UNDER]->                     CreditFacility        # role, time-bound
CreditFacility -[SECURED_BY]->                       Collateral(HomesInInventory)
Company        -[ISSUED]->                           Security
Person         -[HELD_ROLE {title,from,to}]->        Company
Company        -[PARTNERED_WITH {from,to,channel}]-> Company
Event          -[REPORTED_IN]->                      Document
Observation    -[OBSERVED_IN_PERIOD]->               FiscalPeriod
Observation    -[MEASURES]->                         MetricDefinition
Observation    -[SUPPORTED_BY]->                     Passage/TableBlock    # required, §21
Observation    -[COMPARED_AGAINST]->                 Observation           # buybox market
Observation    -[ATTRIBUTED_TO_COHORT]->            ResaleCohort
DerivedFact    -[DERIVED_FROM]->                     Observation[]          # + formula
Event          -[EVIDENCES]->                        Observation
ManagementObjective -[TRACKED_BY]->                  MetricDefinition
ManagementObjective -[DISCLOSED_VIA]->               DisclosureChannel      # §7.4
MacroIndicator -[POSSIBLY_CONTRIBUTES_TO]->         Event                  # inferred, §21
```

`PARTNERED_WITH` must be time-bound: Zillow appears as a **competitor** in early filings and
as a **partner** later. The same pair of companies, opposite relationship, different periods.
This is the clearest case in the corpus for the stable-entity / temporal-role distinction.

---

# 16. Metric definition / observation model

## 16.1 The split

```text
OperatingMetricDefinition        stable, one per concept
  local_id, label, aliases[], unit, period_type,
  comparison_operator?, threshold_value?, threshold_unit?, measured_from?,
  numerator_description, denominator_description,
  is_gaap, reconciles_to?, formula_versions[]

OperatingMetricObservation       one per (metric, subject, period, population)
  metric -> definition
  subject -> Company | Population
  population_role: baseline | comparison
  value, unit
  period_type: instant | duration
  observation_date | period_start + period_end
  reporting_period -> FiscalPeriod
  formula_version -> MetricFormulaVersion?
  source_lane: xbrl | narrative_table | narrative_text
  denominator_wording?           verbatim, when the source varies (§8)
  companion_measure?             e.g. aggregate purchase price for HomesUnderContract
  supported_by -> Passage/TableBlock   REQUIRED
```

Roughly 20 metric definitions will carry several hundred observations. One node type per
quarterly value would produce thousands of unrelatable nodes.

## 16.2 Reported versus derived

```text
reported:  ContributionMargin = 8.7%, Q1 2025, from EX-99.3 table
derived:   HomesPurchased QoQ change = +46%, Q4 2025 -> Q1 2026
             derivation: (v2 - v1) / v1
             inputs: [observation_a, observation_b]
             asserted_in: DEF 14A 2026-04-28 p86      # company asserted it
             derivation_status: company_asserted | graph_computed
```

The distinction matters here specifically: Opendoor *states* the +46% itself, so the graph
holds a company-asserted derived fact with evidence — not a number the graph invented. A
graph-computed delta with no filed assertion must be labelled differently.

## 16.3 Event versus observation

The rise in homes purchased is **one observation**, referenced by **several events**, never
copied:

```text
OperatingMetricObservation(HomesPurchased, Q1 2026, 4,300ish)
   <-[EVIDENCES]- EarningsResult(2026-05-07)
   <-[EVIDENCES]- StrategyChange("Scale Acquisitions" objective)
   <-[DERIVED_FROM]- DerivedFact(QoQ +45%)
```

## 16.4 Stable entity versus temporal role

`Company(Zillow)` is one node for all time. `PARTNERED_WITH` and `COMPETES_WITH` are
time-bounded edges between the same pair. Same for `Person(Kaz Nejatian)` and
`HELD_ROLE(CEO, from 2025-09)`.

---

# 17. FIBO alignment table

All URIs below were read from the RDF that declares them.

| Local concept | FIBO class | Module | URI | Fit |
| --- | --- | --- | --- | --- |
| Company (public) | `PubliclyHeldCompany` | BE/LegalEntities/CorporateBodies | `…/BE/LegalEntities/CorporateBodies/PubliclyHeldCompany` | **direct** |
| Company (general) | `Corporation` | BE/LegalEntities/CorporateBodies | `…/CorporateBodies/Corporation` | **direct** |
| CreditFacility | `CreditFacility` | FBC/DebtAndEquities/Debt | `…/FBC/DebtAndEquities/Debt/CreditFacility` | **direct** |
| Revolving facility | `RevolvingLineOfCredit` | FBC/DebtAndEquities/Debt | `…/Debt/RevolvingLineOfCredit` | **direct** |
| Lender (role) | `Lender` | FBC/DebtAndEquities/Debt | `…/Debt/Lender` | **direct** |
| Borrower (role) | `Borrower` | FBC/DebtAndEquities/Debt | `…/Debt/Borrower` | **direct** |
| Collateral | `Collateral` | FBC/DebtAndEquities/Debt | `…/Debt/Collateral` | **direct** |
| Common stock | `CommonShare` | SEC/Equities/EquityInstruments | `…/SEC/Equities/EquityInstruments/CommonShare` | **direct** |
| Listed security | `ListedShare` | SEC/Equities/EquityInstruments | `…/EquityInstruments/ListedShare` | **direct** |
| Borrowing capacity | `BorrowingCapacity` | FBC/DebtAndEquities/Debt | `…/Debt/BorrowingCapacity` | **partial** — FIBO frames it as a lender's belief about a party; Opendoor reports a contractual per-facility limit. Prefer `hasCreditLimit` for the number |
| Material agreement | `CreditAgreement` | FBC/DebtAndEquities/Debt | `…/Debt/CreditAgreement` | **partial** — covers credit agreements only; offer letters and partnerships need FND or a local class |
| Macro indicator | `EconomicIndicator` | IND/EconomicIndicators | `…/IND/EconomicIndicators/EconomicIndicators/EconomicIndicator` | **direct for macro, UNSUITABLE for company KPIs** |
| Metric value | `hasIndicatorValue` | IND/EconomicIndicators | `…/EconomicIndicators/hasIndicatorValue` | **pattern only** |
| Baseline / comparison population | `hasBaselinePopulation`, `hasComparisonPopulation` | IND/EconomicIndicators | `…/EconomicIndicators/hasComparisonPopulation` | **pattern, strong conceptual match** for §8.3 |
| **Warrant** | **none found** | SEC/Equities/EquityInstruments | — | **absent** — no Warrant class in that file. OPENW/OPENZ need a local class or another module; **no URI invented** |
| Homes sold, homes purchased, contribution margin, 120-day aging, inventory aging | **none** | — | — | **no FIBO equivalent** — custom definitions required |

**The semantic mismatch to avoid.** FIBO defines `EconomicIndicator` as "statistical measure
of economic activity that is regular and comparable **in the context of a statistical area
(region)**". Mortgage rate, home price appreciation and housing supply fit. Homes Sold and
Contribution Margin are measures of *one company's operations*, not of a region's economy.
Mapping them to `EconomicIndicator` because both are "numbers over time" would be exactly
the similarly-named-class trap the brief warns about.

---

# 18. XBRL and US-GAAP alignment table

| Local concept | Standard US-GAAP | Opendoor custom | Source lane |
| --- | --- | --- | --- |
| Revenue | `us-gaap:Revenues` | — | **XBRL** |
| Gross profit (GAAP) | `us-gaap:GrossProfit` | — | **XBRL** |
| Cost of revenue | `us-gaap:CostOfRevenue` | — | **XBRL** |
| Inventory (real estate) | `us-gaap:InventoryRealEstate` | — | **XBRL** |
| Inventory valuation adjustment | `us-gaap:InventoryAdjustments` (related) | `open:InventoryRealEstateValuationAdjustmentDecreaseIncrease` (2022-03→2026-03) | **XBRL** |
| Homes under resale contract | — | `open:InventoryRealEstateInResaleContract` (2022-03→2026-03) | **XBRL** — *unit ambiguous, see §22* |
| Market count | — | `open:NumberOfCurrentlyOperatedMarkets` (2020-12→2021-06, **discontinued**) | XBRL then **narrative** |
| Cumulative homes transacted | — | `open:NumberOfHomeTransactionsCompleted` (2020-12→2021-06, **discontinued**) | XBRL then narrative — **not** homes sold |
| **Homes sold** | none | **none** | **narrative table** |
| **Homes purchased** | none | **none** | **narrative table** |
| **Homes under contract (buy side)** | none | **none** | **narrative text** |
| **Adjusted Gross Profit / Margin** | none (non-GAAP) | **none** | **narrative table** |
| **Contribution Profit / Margin** | none (non-GAAP) | **none** | **narrative table** |
| **% homes on market > 120 days** | none | **none** | **narrative table** |
| Adjusted EBITDA | none (non-GAAP) | none | **narrative table** |
| Debt / facilities | `us-gaap:DebtInstrument*` family | `open:DebtInstrumentRepaymentsSaleOfRealEstateInventoryExpectedPeriod` | **XBRL** |

**Do not** map Contribution Profit to `us-gaap:GrossProfit`. It reconciles *to* gross profit
but is a different measure with prior-period costs pulled in and unsold-inventory costs
excluded. The reconciliation is a relationship (`reconciles_to`), not an equivalence.

---

# 19. Recommended ontology v1

**24 concepts.** Every one is grounded in at least one read passage or verified XBRL element.

### Entities (9)
`Company` · `Person` · `Market` · `CreditFacility` · `Security` · `Product` · `ResaleCohort`
· `FiscalPeriod` · `Document`

### Metric layer (4)
`OperatingMetricDefinition` · `MetricFormulaVersion` · `OperatingMetricObservation` ·
`DerivedFact`

### Events (8)
`EarningsResult` · `GuidanceChange` · `LeadershipChange` · `MaterialAgreement` ·
`DebtFacilityChange` · `CapitalRaise` · `MarketEntryExit` · `ProductLaunch`

### Framework and evidence (3)
`ManagementObjective` · `Passage` (evidence) · `MacroIndicator`

### Seeded metric definitions (instances, not classes)
`homes_sold` · `homes_purchased` · `homes_under_contract` · `acquisition_contracts` ·
`homes_in_inventory` · `inventory_balance` · `inventory_valuation_adjustment` ·
`pct_homes_on_market_gt_120_days` · `adjusted_gross_profit` · `adjusted_gross_margin` ·
`contribution_profit` · `contribution_margin` · `contribution_profit_after_interest` ·
`adjusted_ebitda` · `adjusted_net_income` · `market_count` · `revenue` · `gross_profit`

---

# 20. Concepts deferred or rejected

| Concept | Decision | Reason |
| --- | --- | --- |
| Offers, seller requests, conversion rate | **reject** | No corpus support at all |
| Housing turnover | **reject** | Zero hits; Opendoor says "home sales volume" |
| `Accountable` as a node | **reject for v1** | §7 — unreproducible; becomes a `DisclosureChannel` property |
| Days on market | defer | 10 passages; superseded by the 120-day metric |
| Resale velocity | defer | Narrative objective name, not a measured value |
| Return on inventory | defer | Never given a number |
| Revenue/cost per home | defer | Derivable; add once the observation model works |
| Acquisition | defer | One 2.01 filing |
| Regulatory event | defer | Risk-factor mentions only |
| Buybox | **property**, not node | A filter parameter on a comparison population |
| Warrant | **custom class** | No FIBO class exists (§17) |
| Schema.org / Wikidata | **external mapping only** | Optional `wikidata_id` on Company, Person, Market |

---

# 21. Evidence and provenance requirements

Every metric observation, event and relationship **must** carry:

```text
supported_by      -> passage_id or block_id   (normalized corpus)
source_artifact   -> artifact_id              (acquisition corpus)
accession, form, filing_date, artifact_role
heading_path                                   human-readable location
source_url                                     live EDGAR URL
source_lane       xbrl | narrative_table | narrative_text
assertion_type    reported_directly
                | calculated_from_reported_facts
                | classified_by_extractor
                | inferred_relationship
```

The normalization layer already supplies all of these — `passage_id`, `document_id`,
`source_url`, `heading_path`, `locators[]` and `items[]` are on every one of the 14,203
passages — so the evidence contract costs nothing new to honour.

`assertion_type` is the distinction the brief insists on, and it is not optional: the +46%
homes-purchased change is `reported_directly` when Opendoor states it and
`calculated_from_reported_facts` when the graph computes it. A `POSSIBLY_CONTRIBUTES_TO`
edge from a mortgage-rate move to an earnings miss is `inferred_relationship` and must never
be presented as reported.

---

# 22. Open ambiguities

Recorded rather than resolved.

1. **120-day denominator.** "our portfolio" / "our homes" / "our homes in inventory" — three
   wordings across document types. Are listed-only homes the denominator, or all owned homes?
   *Impact: high.* The metric is a ratio; the denominator changes its meaning.
2. **Homes-sold recognition point.** No filing found defines whether it counts title
   transfer, closing, or contract completion. *Impact: medium.*
3. **`open:InventoryRealEstateInResaleContract` unit.** Label "Under contract for sale",
   observed value 781. Count of homes or dollars in thousands? Must be resolved from the
   context ref before use. *Impact: medium.*
4. **Adjusted Gross Profit formula versions.** Two known (§6). Were there intermediate
   variants between FY2021 and FY2024? Requires reading each year's reconciliation.
   *Impact: high* for any cross-year comparison.
5. **Acquisition contracts: weekly vs quarterly.** Accountable reports weekly; filings report
   quarterly ("5,000+ in 1Q 2026"). Same definition at two frequencies, or two measures?
   *Impact: medium.*
6. **Market count vs named markets.** 21 (2020) → 50 (2024), but individual entry/exit events
   are rarely announced. Reconstructing a per-market timeline may not be possible.
   *Impact: medium.*
7. **Contribution Profit cohort boundary.** "Homes sold during the current period" is the
   cohort, but prior-period costs are included — so a cohort spans periods. How should
   `ResaleCohort` be keyed? *Impact: high* for cohort modelling.
8. **Buybox definition.** Referenced 93 times, never defined numerically in the corpus.
   *Impact: low* for v1, since it is only a comparison-population label.

---

# 23. Recommended next extraction benchmark

Do not annotate the whole corpus. Build a small, reviewable benchmark that tests exactly the
distinctions this report identified.

**Proposed set — 12 documents, ~40 gold facts:**

| Document | Tests |
| --- | --- |
| EX-99.1 2026-05-07 (`0001801169-26-000013`) | Full Key Metrics table; management-objective framework; guidance |
| EX-99.1 2026-02-19 (`0001801169-26-000009`) | Same table one quarter earlier — cross-period consistency |
| EX-99.3 2025-05-06 (`0001801169-25-000037`) | The reconciliation table — all six margin/profit measures at once |
| EX-99.1 2021-03-04 (`0001801169-21-000011` family) | The **v1 formula** wording, for drift detection |
| 10-K 2022-02-24 | 120-day metric with "our portfolio" denominator |
| EX-99.2 2023-05-04 | 120-day metric with "homes in inventory" denominator |
| 10-Q 2021-05-12 | "in contract to purchase N homes for aggregate purchase price" |
| EX-99.2 2021-08-11 | Homes under contract vs homes purchased, stated together |
| 8-K primary 2025-09-19 (`0001140361-25-035480`) | LeadershipChange event, Item 5.02 |
| 8-K primary 2025-05-19 (`0001140361-25-019770`) | DebtFacilityChange / CapitalRaise, Items 1.01/2.03/3.02 |
| DEF 14A 2026-04-28 | Company-asserted derived fact (+46% QoQ) attributed to a leadership change |
| EX-3.1 2020-04-30 | **Negative control** — "more than 120 days" notice window that must *not* extract |

**Success criteria to agree before extraction is built:**

1. Every extracted observation carries metric, value, unit, period type, period, subject and
   a resolvable evidence pointer.
2. The 120-day metric extracts operator `>`, threshold `120`, unit `percent`, instant period,
   and the verbatim denominator wording.
3. The EX-3.1 negative control yields **zero** inventory-aging observations.
4. Contribution Profit, Contribution Margin, Adjusted Gross Profit, Adjusted Gross Margin,
   GAAP Gross Profit and GAAP Gross Margin from the Q1 2025 reconciliation extract as **six
   distinct** observations with their correct differing values.
5. Homes purchased, homes under contract and acquisition contracts extract as three distinct
   metrics from documents that state them together.
6. The company-asserted +46% extracts as a `DerivedFact` with `derivation_status =
   company_asserted`, not as a reported observation.
7. No extracted fact lacks `assertion_type`.

Only after this benchmark passes should extraction run over the 294-document corpus.
