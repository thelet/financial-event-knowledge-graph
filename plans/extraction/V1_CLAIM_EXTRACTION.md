# v1 — Claim Extraction

**Status:** partially implemented. The provider-independent half is built and green — shared
contracts, deterministic identities, assembly, validation, typed candidate selection and the
deterministic table lane. The narrative lane and its local provider are next; the runtime for
them is built and gated but unused. **1,405 tests pass offline.**

**Scope:** turn the normalized corpus into validated `OntologyClaim` objects carrying
evidence that points back into that corpus, for the 20 metrics the corpus can actually
support.

| Stage | State | Commit |
| --- | --- | --- |
| This plan | written, then corrected twice against measurement | `4af55ae`, `7b063a6` |
| Encoding prerequisite (§8) | **done** | `a0eb6bc`, `928e806` |
| Reviewed benchmark (§4.0) | **done** — 26 cases, 69 gold claims *(68 at `0cc3678`; +1 by the 2026-08-02 founder correction below)* | `0cc3678` |
| Local runtime probe and build | **done** — gated, unused so far | `0dbcc11`, `614f1ac` |
| `core/` + `contracts.py` + assembly + validation (§4.4, §4.5, §5, §6) | **done** | `f92533a` |
| `select` (§4.1) | **done** — 503 table, 3,072 narrative candidates | `614f1ac` |
| `tables` lane (§4.2) | **done** — recall 0.939, six dimensions at 1.000 | `22aafa4` |
| `narrative` lane (§4.3) | not started | — |
| candidate scoping (§4.2a) | **done** — lexical `e97ad5a`; hybrid measured, uncommitted | `e97ad5a` |
| `catalog` (§4.6) | not started | — |

Sections 1–3 are measurement. Sections 4–7 are durable design decisions. Section 8a records
what implementation measured and changed. Section 9 is the remaining sequence.

Consumes the corpus built by
[../normalization/V1_DOCUMENT_NORMALIZATION.md](../normalization/V1_DOCUMENT_NORMALIZATION.md)
and the vocabulary built by
[../ontology/ONTOLOGY_V1_IMPLEMENTATION.md](../ontology/ONTOLOGY_V1_IMPLEMENTATION.md).

```text
Normalized passages → candidate selection → lane extraction → claim assembly
                    → ontology validation → claim catalog
```

**Out of scope, and enforced by a structural test:** graph construction, Neo4j, Graphiti,
entity resolution beyond the single anchor company, vector search, RAG, question answering,
post generation, XBRL fact extraction (§3.1), transcript or peer-company acquisition,
and comparison of larger or alternative generation models.

**In scope, narrowly.** Three things this plan originally excluded were later accepted into
v1 and are in §9, bounded so they do not become their own projects:

- **Embeddings — inside candidate scoping only** (§4.2a, steps 8–9). They may add semantic
  candidates and may never remove a protected one. No vector database; ~131 concept vectors
  cached locally.
- **Events and relationships — a representative subset** (step 12): the benchmark's 4 events
  and 2 relationships, proving the claim path carries non-metric payloads. Not a sweep of the
  64 material agreements.
- **A local generation provider** (step 7), for the narrative lane. One model, no comparison.

Facts marked *(verified)* were measured against the committed corpus and the installed
libraries on **2026-08-01**, by the commands recorded beside them. Facts marked
*(unverified)* are design intent that the implementation must confirm.

---

# 1. What the normalized corpus actually offers

## 1.1 Shape *(verified)*

`data/normalization_catalog/` holds 294 documents and **12,442 passages** — 10,507
narrative, 1,935 table. Issues: 60 `NEEDS_REVIEW`, 30 `HIERARCHY_UNCERTAIN`, 0
`PARSER_FALLBACK`.

These are the corrected counts. Every figure in this section was re-measured on 2026-08-01
after the encoding defect of §1.4 was fixed and the corpus re-normalized; the pre-fix
figures this plan first carried are superseded, and §1.2 changed materially as a result.

| Form | Documents |
| --- | --- |
| 8-K | 203 |
| 10-Q | 44 |
| 10-K | 34 |
| DEF 14A | 7 |
| 8-K/A | 6 |

## 1.2 Tables are not where you would guess *(verified)*

| `document_type` | Passages | Table passages |
| --- | --- | --- |
| narrative_primary | 8,567 | 1,648 |
| earnings_release | 917 | 145 |
| material_agreement | 1,345 | 78 |
| governance | 636 | 44 |
| structured_exhibit | 27 | 10 |
| other | 243 | 9 |
| shareholder_letter | 583 | **1** |
| supplemental | 124 | 0 |

Two consequences that shape the whole design:

**Typed selection is still right, but for a much weaker reason than I first wrote.** The
pre-fix corpus showed `material_agreement` holding 1,130 of 2,987 table passages, with
`Whereas:` (941 occurrences) the commonest table heading corpus-wide — an apparently
overwhelming case for excluding it. Almost all of those tables came from the nine documents
that were falling back to the lxml parser because of the encoding defect. After the fix,
`material_agreement` holds **78 of 1,935** table passages and `Whereas:` is gone from the
heading census entirely. Excluding contract and governance tables now skips 131 of 1,935
tables — **6.8%, not 38%**. Worth doing, and still auditable, but it is a tidiness argument
now rather than a cost argument.

**Shareholder letters have exactly one table across 26 documents.** The KPI commentary in
letters is prose. Any design that treats "KPIs live in tables" as universal loses the
letter lane entirely — and the letters are where the 120-day-portfolio wording that
ontology §16.1 flags as high-impact actually appears.

## 1.3 Table passages are Markdown pipe tables, and they are sparse *(verified)*

`norm:0001801169:0001801169-21-000016:q12021form8-kxexhibit991.htm#p20`:

```text
|  |  | Three Months Ended |  |  |  |  |
| (in thousands, except percentages) |  | March 31, 2021 |  | December 31, 2020 |  |
| Gross profit (GAAP) |  | $ | 97,132 |  |  | $ | 38,365 |
| Gross Margin |  | 13.0 | % |  | 15.4 | % |
| Adjusted Gross Profit |  | $ | 97,038 |  |  | $ | 38,228 |
| Adjusted Gross Margin |  | 13.0 | % |  | 15.4 | % |
```

Everything a claim needs is present but scattered across cells that HTML layout, not
meaning, decided:

- the **metric label** is in column 0;
- the **currency sigil** `$`, the **magnitude** and the **percent sign** occupy three
  separate columns;
- the **period** lives two header rows up, split between a duration phrase
  (`Three Months Ended`) and a date (`March 31, 2021`);
- the **unit scale** (`in thousands`) is in a parenthetical in the header row, not
  beside the number — **or not in the table at all**. *(corrected 2026-08-01)* Of the 74
  KPI-bearing table passages, **65 declare the scale inside the table and 9 declare it in
  the immediately preceding narrative passage**; none omit it. The Q1 2025 earnings release
  is the second form: `#p14` holds the table and `#p13` holds "(In millions, except
  percentages, homes sold, number of markets, homes purchased, and homes in inventory)". A
  lane that only reads the table gets every dollar figure wrong by six orders of magnitude
  and every count right, which is why the benchmark scores `scale` as its own dimension.

A row-and-column reader must therefore collapse empty columns before aligning a value to
its period header. This is mechanical and fully testable offline — it is the reason the
table lane needs no model (§4.2).

## 1.4 The corpus carried an encoding defect *(fixed 2026-08-01)*

**Fixed and re-normalized.** Recorded here because it changed this plan's own measurements
and because §1.2's argument was built on its artifacts.

Before the fix, **7,920 of 14,203 passages (55.8%), across 268 of 294 documents, carried an
orphaned `Â` or `â`** — 178,625 corrupted characters. The §1.3 sample above read
`97,132Â` and `exceptÂ percentages`.

This is ours, not the SEC's. The raw byte at that position is the entity `&#160;`:

```text
raw bytes around 97,132: b'97,132&#160;</fo'
declared charset: []            # the document declares none
```

The mechanism, reproduced directly against the installed lxml:

```python
s2 = '<html><body><p>97,132\xa0</p></body></html>'   # NBSP already resolved
html.fromstring(s2.encode('utf-8')).text_content()
# -> '97,132Â\xa0'
```

`sec_parser` resolves `&#160;` to U+00A0 and returns a `str`.
`normalization/stages/parse/sec_html_parser.py:179` and `:233` then re-encoded that string
with `source.encode("utf-8")` and handed the bytes to `parse_html_bytes`. Because the
document declares no charset, lxml fell back to latin-1 and read `\xc2\xa0` as `Â` + NBSP.
A later whitespace pass folded the NBSP into a space, stranding the `Â`.

It was never NBSP-specific: em dashes, curly quotes and accented letters were mangled by the
same round trip. `parse_html_bytes` now takes an optional `encoding`, and the two call sites
that manufacture bytes from a decoded `str` declare it.

`parse_html_bytes` taking bytes is correct and documented (91 artifacts carry an XML
declaration that makes `lxml.html.fromstring` reject a `str`). The defect is only at the
two call sites that manufacture bytes from a string we already decoded, without telling
lxml the encoding they used.

**What it cost, and what I got wrong about it.** I expected it to break metric-name
matching. It did not: stripping `Â`/`â` and re-running alias matching recovered **zero**
additional hits in either lane. The damage was confined to value parsing.

The reason it had to be fixed first was different from the one I first gave: passage
`content_sha256` and `fragment_sha256` are content-addressed, so fixing it after extraction
emitted claims would have turned a few hours of re-normalization into a hash migration.
`passage_id` is positional and survived — 12,442 of 14,203 ids unchanged, none renumbered.

**The consequence I did not anticipate at all** was §1.2. Nine documents were falling back
to the lxml parser because the corruption defeated `TABLE_CONTENT_LOSS`'s probe matching,
and those nine were producing 1,052 of the corpus's 2,987 table passages — including every
`Whereas:` recital table that made contract boilerplate look like 38% of the table lane's
work. See `V1_DOCUMENT_NORMALIZATION.md` §16b.

## 1.5 Candidate density *(verified)*

Case-insensitive substring match of each coverable metric's label and declared aliases
against all 12,442 passages. A **floor**, not an estimate — naive matching, no
normalization of the metric label:

| Metric | Passages | Documents |
| --- | --- | --- |
| homes_sold | 553 | 86 |
| adjusted_ebitda | 489 | 90 |
| holding_costs | 445 | 84 |
| contribution_margin | 444 | 87 |
| contribution_profit | 432 | 78 |
| adjusted_gross_profit | 340 | 80 |
| gaap_gross_margin | 308 | 87 |
| direct_selling_costs | 203 | 84 |
| adjusted_ebitda_margin | 200 | 85 |
| adjusted_gross_margin | 190 | 84 |
| housing_inventory_homes | 133 | 75 |
| homes_purchased | 127 | 70 |
| home_price_appreciation | 109 | 43 |
| contribution_profit_after_interest | 94 | 27 |
| homes_under_contract | 55 | 42 |
| mortgage_rate | 44 | 28 |
| acquisition_contracts | 10 | 6 |
| pct_homes_on_market_gt_120_days | 10 | 10 |

Aggregate: **1,148 narrative and 131 table passages** mention at least one coverable
metric. The two rarest metrics are exactly the two that ontology §16 flags as ambiguous —
`acquisition_contracts` (10 passages) and `pct_homes_on_market_gt_120_days` (10). There is
not enough corpus evidence to resolve either by extraction volume; §7 handles them by
policy instead.

---

# 2. What the ontology already gives, and what it withholds

`EvidenceReference` was designed against this corpus and needs no adapter *(verified)* —
its fields map one-to-one onto passage catalog fields:

| `EvidenceReference` | Passage catalog field |
| --- | --- |
| `passage_id` | `passage_id` |
| `document_id` | `document_id` |
| `table_id` | `table_id` |
| `block_ids` | `block_ids` |
| `char_start` / `char_end` | `locators[].char_start` / `char_end` |
| `source_url` | `source_url` |

**What the ontology does not constrain:** `evidence_types` declare `required_fields` as
field *names* only — no ID grammar. The fixtures in
`examples/valid/` use illustrative IDs (`doc-0001801169-24-000009-odt-20231231x10k`,
`tbl-0001801169-24-000009-014`) that do **not** match the corpus grammar
(`norm:0001801169:0001801169-21-000016:q12021form8-kxexhibit991.htm#p20`). Nothing today
would catch an extractor emitting an ID that resolves to nothing.

**Decision:** the extraction package owns that check. `verify` (§4.5) resolves every
emitted evidence ID against the passage catalog and fails on a dangling anchor. The
ontology stays generic — pinning the `norm:` grammar into `claims.yaml` would couple the
vocabulary to one corpus, which is the coupling the ontology layer exists to prevent.

Also verified: `table_id` in the corpus is a *block* id (`…#b8`), not a separate table
namespace. `normalized_table` evidence therefore addresses a block within a document, and
`table_id` and `block_ids` will frequently overlap. That is correct, not a bug, but the
verifier must not assume they are disjoint.

---

# 3. Scope: 20 metrics, not 26

## 3.1 The corpus contains no XBRL *(verified)*

Acquisition fetched **364 XBRL artifacts**. Normalization's selection policy excluded
every one of them as `NON_NARRATIVE_MEDIA` — that exclusion was deliberate and correct for
a *document* normalizer, and V1_DOCUMENT_NORMALIZATION explicitly listed "XBRL
interpretation" as out of scope.

The consequence lands here. Six of the 26 metrics name `xbrl` as their **first**
source-lane preference:

| Metric | Category |
| --- | --- |
| revenue | gaap_financial |
| gaap_gross_profit | gaap_financial |
| cost_of_revenue | gaap_financial |
| inventory_balance | gaap_financial |
| inventory_valuation_adjustment | gaap_financial |
| homes_under_resale_contract | gaap_financial |

**These six are out of scope for v1.** Not because they are hard, but because the lane
they prefer does not exist in the normalized corpus, and inventing a narrative fallback
for a GAAP figure that has an authoritative tagged source would be the wrong trade.

Two further metrics — `market_count` and `borrowing_capacity` — carry `xbrl` only as a
*fallback* behind a narrative or table preference. They stay in scope.

**20 metrics are coverable**: 18 with no `xbrl` in their preferences at all, plus those
two. This is not a consolation prize. The ontology records a measured research finding
that across 336 custom `open:` XBRL elements there is **no tag for any operating KPI or
non-GAAP measure** — every operating KPI and every non-GAAP measure, which is the entire
differentiating surface of this graph, is narrative- and table-sourced by necessity.

## 3.2 Recommended follow-on, not v1

An XBRL fact lane belongs in a separate plan against the 364 already-acquired artifacts,
feeding the same claim contract with `evidence_kind: xbrl_fact`. It needs no new
acquisition. It is deliberately excluded here to keep this plan's acceptance criteria
provable offline.

---

# 4. Stage structure

Same shape as `acquisition/` and `normalization/`: one package per stage, each
independently runnable, one orchestrator that owns ordering and nothing else.

```text
extraction/
  contracts.py            stage protocol (identical shape to the other two packages)
  context.py              composition root — selects lane implementations
  pipeline.py             ordering only
  cli.py, __main__.py
  core/
    models.py             CandidatePassage, LaneClaim, ExtractionRun
    identifiers.py        deterministic claim and observation ids
    periods.py            period phrase -> (period_start, period_end) / instant_date
    numbers.py            cell text -> value, unit, currency, scale
  stages/
    select/               passages -> candidates
    tables/               deterministic table lane
    narrative/            model-backed narrative lane
    assemble/             lane output -> OntologyClaim
    verify/               evidence resolution + ontology validation
    catalog/              derived jsonl indexes
```

`core/periods.py` and `core/numbers.py` are shared by both lanes and carry application
meaning, so they are `core/`, not `utils/`. They are the two places where §1.3's scattered
cells and §1.4's stray `Â` get resolved once.

## 4.0 The reviewed benchmark — the acceptance instrument *(built: `0cc3678`)*

`benchmarks/extraction/v1/`. Not a stage of the pipeline and deliberately outside it: every
value was read off the normalized passage it cites, and runtime extraction code may not
import it. An executable test fails if anything under `extraction/` references the benchmark
path or module.

| | Count |
| --- | --- |
| documents | 15 |
| cases | 26 |
| gold claims | 69 |
| gold events | 4 |
| gold relationships | 2 |
| expected abstentions | 23 |

Eight dimensions are scored independently — metric identity, value, unit, scale, period,
subject, evidence, ambiguity — because a claim can be right about the metric and wrong about
the period, and a blended score hides exactly that.

**Two corrections to that sentence, both found by review 2026-08-02 and both about what the
committed reports actually contain.**

| | What §4.0 said | What is true |
| --- | --- | --- |
| the table lane | eight dimensions are scored | **Seven.** `table_lane_v1.json` reports `metric_recall`, `matched_over_emitted` and six per-match dimensions; no score in it, per case or in total, is named for ambiguity. The table cases *do* declare expected abstentions and the lane *does* record `AMBIGUOUS_ALIAS`, so the dimension is measurable there and simply was not measured. The report now says so in its prose and in a `score_caveats` block, and step 6's brief is corrected below. |
| `period`, in both lanes | a scored dimension | **A tautology under the shared matching rule.** `evaluation.evaluate_case` keys claims on `(metric_id, period key)` and `compare` then tests `claim.period.key == gold.period_key`, so a matched pair agrees about its period by construction and `period_accuracy: 1.000` cannot fall however a lane dates a figure. The same holds for the metric half of a match. Both reports now mark the number as what it is rather than leaving a 1.000 that cannot fail; the real period evidence is `period_wrong` in the narrative failure classification, §8a.12's attribution table, and the 21 wrongly dated observations on `kpi-table-q4-2023-earnings` that sit outside the gold set. |

**The narrative report scores twelve, and the four beyond the eight are corrections rather
than additions.** §7.1's population wording and §7.3's ambiguity codes were scored by nothing,
so seven matched `pct_homes_on_market_gt_120_days` pairs that disagreed with gold about the
denominator all scored clean; and "ambiguity" as implemented was measuring whether a required
silence was kept for *any* stated reason, over fifteen expectations of which exactly one names
ambiguity candidates. That one dimension is now three — an abstention-honouring rate, a
code-agreement rate, and ambiguity preservation over a denominator of 1, stated as 1.

**It is the acceptance instrument for every lane**, and §11's criteria are checked against
it. Two properties matter more than its size:

- **A third of it is negative.** A gold set of positives only measures recall and rewards a
  lane that emits everything. 23 expected abstentions across the cases.
- **Precision is not measurable against it.** Each case names a deliberate subset of its
  table's claims, so an unmatched claim is usually a right answer the case did not list.
  Reported as a ratio for run-to-run movement, never as an accuracy.

## 4.0a The one gold annotation corrected after measurement *(founder decision, 2026-08-02)*

`population-portfolio-mdna-fy2023-10k` expected a `DEFINITIONAL_NOT_OBSERVATIONAL` abstention
and carried no gold claim. It now carries one gold observation:
`pct_homes_on_market_gt_120_days` = 18 percent, instant 2023-12-31, subject `opendoor`,
`population.definition_raw` = `"such homes represented 18% of our portfolio"`,
`ambiguity_codes: [pct_120_days_denominator]`.

**Why the old annotation was wrong.** Its stated grounds were *"the value for the period is
reported in the KPI table at open-20231231.htm#p105, not here"*. That is false about the
passage it cites, which ends: *"As of December 31, 2023, such homes represented 18% of our
portfolio, compared to 21% for the broader market…"* — metric, subject, instant and value all
printed. A paragraph that defines a metric and then reports it is doing both; the definition
does not withdraw the sentence after it, and a figure appearing in a KPI table does not
withdraw independent prose evidence for the same reported fact. Two independent readings and
the lane all disagreed with the case, and the lane's disagreement was recorded in
`narrative_lane_v1.json`'s `review_questions` for a founder to settle rather than acted on.

A second defect was found while correcting it: the case's `capture_instead.text` was the
**FY2021** 10-K's parenthesised wording, carried in the ontology's `source_evidence` for this
metric, and does not occur in the FY2023 passage at all. `capture_instead` was the one
annotation field no integrity check read.

**What was deliberately not done.** The comparison figure — 21% for the broader market — is
neither gold nor an expected abstention. The founder's condition was that it become a second
gold claim only if the ontology, subject model and benchmark schema *already* supported the
broader market as a distinct subject. They do not: the metric declares
`subject_types: ('company',)`, all 69 gold claims are subject `opendoor`, and the ontology
models the market side as `population.comparison_population`. It is comparison context, left
unscored for V1.

**What moved, and what did not.** No lane, prompt, model or extraction rule changed. The
narrative answer store is byte-identical (`f20f36f6…`, 12 rows, 57,850 bytes) and the report
regenerated by pure replay — which is also the proof gold never reaches the prompt. Only the
scoring moved, and every dimension moved up or held:

| | lexical before → after | hybrid before → after |
| --- | --- | --- |
| gold observations | 19 → 20 | 19 → 20 |
| matched observations | 10 → 11 | 11 → 12 |
| expected abstentions | 15 → 14 | 15 → 14 |
| `metric_identity_accuracy` | 0.526 → 0.550 | 0.579 → 0.600 |
| `value_accuracy` | 0.800 → 0.818 | 0.818 → 0.833 |
| `abstention_honoured_rate` | 0.867 → 0.929 | 0.867 → 0.929 |
| `matched_over_emitted` | 0.435 → 0.478 | 0.458 → 0.500 |
| required-concept recall (scope) | 0.959 → 0.960 | 0.980 → 0.980 |

The hybrid verdict is unchanged — *lexical stays the default* — and the table-lane report's
only diff is its recorded `implementation_commit`.

**What the correction exposed, and left open.** The new match takes `population_accuracy`'s
denominator from 4 to 5 and the score stays **0.000**, which is now measured across five
matched pairs with one shape: in every one the lane emits the bare denominator and the gold
records the whole clause, and in every one **the emitted string is a substring of the
expected** (`population_contained_in_expected: true`).

| case | lane emitted | gold expects |
| --- | --- | --- |
| `population-portfolio-mdna-fy2023-10k` | `our portfolio` | `such homes represented 18% of our portfolio` |
| `population-our-homes-q4-2021` | `our homes` | `only 8% of our homes had been listed on the market for more than 120 days` |
| `population-two-wordings-one-passage-q4-2023` | `our homes` | `18% of our homes had been listed on the market for more than 120 days` |
| `letter-prose-inventory-and-120d-q2-2022` (hybrid only) | `our homes` | `5% of our homes were listed on the market for more than 120 days` |
| `recon-shareholder-letter-table-as-prose` | `homes "on the market" for greater than 120 days` | `Percentage of homes "on the market" for greater than 120 days (at period end)` |

Both sides quote the passage verbatim; they disagree about **how much of the sentence
`definition_raw` should span**. The substantive requirement §7.1 exists to protect is met —
the lane's wordings still separate `our homes` from `our portfolio` from `our homes in
inventory`, so no two series are merged. So `population_accuracy: 0.000` is currently
measuring a span convention, not a lost denominator, and it should not be read as the lane
failing to preserve population wording.

**This is a founder gate and is not decided here.** Fixing the span convention on either side
changes scoring across five pairs, and §7.1's meaning is the founder's to set. Recorded, with
the evidence, exactly as the annotation defect above was.

## 4.1 `select` — typed candidate selection

Emits a `CandidatePassage` per (passage, lane) with a recorded reason code, so a passage's
absence from extraction is as auditable as its presence — the same discipline
`selection.jsonl` already applies to artifacts.

Policy, from §1.2 and §1.5:

| Lane | Included `document_type` | Rationale |
| --- | --- | --- |
| tables | earnings_release, narrative_primary, structured_exhibit, supplemental | 1,803 table passages; where reconciliation and KPI tables live |
| narrative | earnings_release, shareholder_letter, narrative_primary | letters are prose-only (§1.2) |
| — excluded | material_agreement, governance, other | 131 table passages of recitals and boilerplate, no KPIs |

Selection is by `document_type` and alias hit, both recorded. It is a policy in
`config/extraction.yaml`, never in code.

## 4.2 `tables` — deterministic, no model

The lane that does most of the work and needs no provider. Given §1.3's structure:

1. collapse empty columns; retain the original column index for every surviving cell so
   evidence keeps a real `char_start`/`char_end`;
2. parse header rows into period columns (`Three Months Ended` + `March 31, 2021` →
   duration; a bare `December 31, 2020` under a balance-sheet heading → instant);
3. read the unit scale from the header parenthetical (`in thousands`);
4. match column 0 against the metric alias index via `registry.resolve_alias`;
5. emit one `LaneClaim` per (metric, period column) with `source_lane: normalized_table`.

Fully offline-testable against committed fixtures. This is where a fixture corpus earns
its keep — the reconciliation tables recur quarterly with stable layout, so a handful of
committed passages covers most of the corpus.

**Ambiguous aliases stay ambiguous.** The ontology declares 7 aliases as `ambiguous: true`
(`homes`, `contracts`, `under contract`, `gross profit`, `gross margin`, `margin`,
`contribution`). When `resolve_alias` returns more than one concept, the lane emits no
claim and records an `AMBIGUOUS_ALIAS` issue naming the candidates. Guessing would
manufacture exactly the metric confusion the `distinct_from` declarations exist to prevent.

## 4.2a The lane hierarchy, and what may never overrule what

Both lanes satisfy one `ClaimLane` protocol and neither is named in `contracts.py`:

```text
ClaimLane
├── DeterministicTableClaimLane        no provider, fully offline
└── OntologyGuidedNarrativeClaimLane   a local generation provider, behind its own port
```

`extract` takes **one candidate at a time**, not a batch. Routing stays outside the lanes so
it is auditable: the caller decides which lane sees which passage and records the reason,
rather than each lane filtering a shared stream by private rules. Not every table is
deterministic and not every narrative needs the same model path.

Candidate scoping is a separate protocol with two implementations, compared on the benchmark
before either becomes the default:

```text
OntologyCandidateScope
├── LexicalOntologyCandidateScope      alias and label matching only
└── HybridOntologyCandidateScope       lexical, plus semantic neighbours from an EmbeddingProvider
```

**Embeddings may only add.** A similarity score may never remove an exact alias candidate,
an ambiguous alias candidate, a table-label candidate, a stable core concept, or a
confusion-group sibling. The ontology's `distinct_from` declarations exist to stop metrics
collapsing into each other — `homes_sold` from `homes_purchased`, adjusted from GAAP — and a
cosine distance is not entitled to overrule a declared distinction. No vector database: the
concept vectors are cached locally, keyed by ontology `definition_hash`, model id,
dimensions and renderer version, so a vocabulary edit invalidates them automatically.

## 4.3 `narrative` — model-backed, behind the same protocol

1,150 candidate passages. Prose KPI commentary and the letter lane cannot be read
mechanically.

Both lanes satisfy one `ClaimLane` protocol; `context.py` picks the implementations. The
provider object dies inside the narrative lane's adapter — nothing downstream imports a
vendor type, and `LaneClaim` carries only `extractor_metadata`, the free dict the ontology
never interprets.

**Sequencing recommendation:** build and land the table lane first, with the narrative lane
defined by its protocol and a single conformance test, then implement it. That keeps the
suite green and fully offline through the larger half of the work, and it means the claim
contract is proven by a real lane before a model is pointed at it.

## 4.4 `assemble` — lane output to `OntologyClaim`

Builds `MetricObservation` and wraps it in `OntologyClaim`. Where §7's policies apply
(population wording, restatement, recognition point), they apply here — one place, not
scattered through both lanes.

## 4.5 `verify`

Fails the run on any of:

- an evidence ID that does not resolve against `passages.jsonl` (§2);
- `ontology.validate_claims` returning any error;
- a claim whose `source_lane` is in its metric's `forbidden_source_lanes`;
- a duplicate `observation_id` with a differing value.

## 4.6 `catalog`

Derived, rebuilt, never appended to: `claims.jsonl`, `observations.jsonl`,
`evidence.jsonl`, `issues.jsonl`. Per-run metadata stays authoritative.

---

# 5. Determinism

Claim and observation IDs derive from stable identity and structural position, never from
random UUIDs, matching the rule the other two packages follow:

```text
obs:<metric_id>:<subject_entity_id>:<period_key>:<lane>:<passage_id_digest12>
claim:<claim_kind>:<obs_id_digest12>
```

`period_key` is `2023Q4` / `FY2023` / `2023-12-31` for instants. The passage digest keeps
two filings reporting the same metric-period pair distinguishable — which is precisely the
restatement case §7.5 has to decide about, and an ID scheme that collapsed them would hide
it.

A run writes an immutable manifest with config hash, code commit, ontology
`definition_hash` (`3372c5777c1d…`), and the normalization run id it consumed. Staged into
a temporary directory, completion marker written last, renamed into place.

**Acceptance test:** two runs over the same corpus produce byte-identical catalogs.

---

# 6. Contracts

`extraction/public.py` per stage, dependency-light — no HTTP, storage, manifest, or
configuration imports:

```python
@runtime_checkable
class ClaimLane(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str: ...
    def extract(self, candidate: CandidatePassage) -> tuple[LaneClaim, ...]: ...
```

Conformance tests drive objects *through* the protocol. No `isinstance` assertion against
a runtime-checkable protocol — it verifies method presence and proves almost nothing.

---

# 7. The six questions ontology §16 deferred to this layer

Each gets a policy here, because "TBD" is not an answer the corpus cannot give.

## 7.1 The 120-day denominator — *high impact*

Three filed wordings, never identical: "our portfolio" (10-K/10-Q MD&A), "our homes"
(letters), "our homes in inventory" (Q1 2023 letter).

**Policy:** every `pct_homes_on_market_gt_120_days` observation carries
`population.definition_raw` verbatim from its source passage. Two observations whose
`definition_raw` differ are **not** comparable and the catalog must not present them as one
series. With only 10 candidate passages (§1.5), this is cheap to enforce and impossible to
resolve statistically. Unchanged from the ontology's recommendation.

## 7.2 Acquisition contracts at two frequencies — *medium*

**Policy:** the weekly `accountable.opendoor.com` series is discovery-only and cannot be
cited; it is not in the normalized corpus anyway, so v1 extracts only the quarterly
filed figure. No new decision needed — recorded so the next plan does not re-open it.

## 7.3 Homes-sold recognition point — *medium*

No filing defines it. **Policy:** extract the reported figure as filed and attach the
ambiguity code `homes_sold_recognition_point` to every observation, rather than picking a
recognition point the filings do not state. Matters only at quarter boundaries; flagging
it costs one field and inventing an answer would be a fabricated measurement.

**Amended 2026-08-02, widened to every declared ambiguity.** The implementation
(`core.assembly.declared_ambiguity_codes`) attaches *all* the ambiguities the ontology records
against a metric, not only this one, which today covers four metrics rather than one:

| metric | ambiguity code |
| --- | --- |
| `homes_sold` | `homes_sold_recognition_point` |
| `acquisition_contracts` | `acquisition_contracts_period_semantics` |
| `pct_homes_on_market_gt_120_days` | `pct_120_days_denominator` |
| `homes_under_resale_contract` | `resale_contract_unit` |

The plan is amended rather than the code narrowed, and the reason is the one that produced this
policy in the first place: each code was recorded on the concept precisely so it would travel
with the observation instead of being silently decided, and there is no argument for carrying
one and dropping three. Narrowing would also mean writing a metric id into the extractor that
the vocabulary already states — the failure mode `deferred_metric_ids` exists to avoid.

## 7.4 `open:InventoryRealEstateInResaleContract` unit — *medium*

Belongs to `homes_under_resale_contract`, which is **out of scope for v1** (§3.1, XBRL-first).
Resolve it when the XBRL lane is built, from the context ref rather than the label.

## 7.5 Restatement handling — the one that needs a real decision

`SUPERSEDES` exists and is `derived`, but nothing decides which observation supersedes
which when a 10-K restates a prior year.

**Recommendation:** extraction does **not** emit `SUPERSEDES`. It emits both observations,
each with its own filing evidence and `reported_at`, and the ID scheme (§5) keeps them
distinct via the passage digest. Superseding is a graph-layer policy over a complete set of
observations; deciding it inside a per-passage extractor means deciding it without seeing
the other filing. Recorded as an explicit deferral with a reason, not an oversight.

## 7.6 Partnership termination

No `partnership_termination` event type exists because no such disclosure was found.
**Policy:** the extractor never infers an end date from absence — `partnership_announcement`
already declares that `inference_restrictions`, and `verify` should fail any claim that
sets an end date without a passage asserting one.

---

# 8. Prerequisite: the encoding defect — **done**

Fixed and landed as its own normalization commit before any extraction work, with the
correction note in `V1_DOCUMENT_NORMALIZATION.md` §16b.

| | Before | After |
| --- | --- | --- |
| mojibake characters | 178,625 | **0** |
| passages | 14,203 | **12,442** |
| table passages | 2,987 | **1,935** |
| documents | 294 | **294** |
| `PARSER_FALLBACK` | 9 | **0** |

A second defect surfaced while verifying the first: the derived issue catalog concatenated
every run's issue file, growing 99 → 189 → 279 across three runs of an unchanged corpus.
Fixed separately. All four catalogs are now byte-identical across two independent full runs
plus a third rebuild, `verify` passes, and 717 tests pass offline.

---

# 8a. What implementation measured and changed *(2026-08-01)*

Design decisions live in §4–§7 and are unchanged. What follows is what building the lanes
*measured* — each found by a wrong answer rather than by reasoning, and each now carried by a
test that fails without it.

## 8a.1 Value columns align by ordinal, not by position

In the Q1 2025 KPI table the header dates sit at original columns **2, 4, 6, 8, 10** while
its `Homes sold` values sit at **2, 5, 8, 11, 14**. A `$` row and a `%` row consume different
numbers of layout cells, so every row has its own spacing.

Positional indexing dropped the third column and reported 3,615 homes under **2Q24 instead of
3Q24** — right metric, right value, wrong year, and nothing downstream could catch it. What
survives the Markdown rendering is *order*: the k-th reported magnitude belongs to the k-th
period column.

Where the counts disagree the row is refused with `AMBIGUOUS_COLUMN_ALIGNMENT`, with one
exception that is not a guess — a change column carries a value on some rows and not others
(the 10-Q prints `Homes sold 2,946 / 3,078 / (132)` but `Percentage … 27% / 15%` with the
change cell blank), so falling back to the reporting columns alone resolves it.

## 8a.2 Duration groups also assign by ordinal

The Q4 2020 reconciliation puts `Three Months Ended December 31,` and `Year Ended December
31,` at columns 2 and 4, above years at 2, 4, 6, 8. The `colspan` that placed the first
phrase over the first *two* years does not survive the rendering, so "nearest phrase at or
left of this column" handed 2019 to the annual group and reported a quarterly margin as a
full year.

Even division in document order gives `2020Q4, 2019Q4, FY2020, FY2019`. An uneven split is
not guessed at — it falls back to position, and the resulting period is still checked against
the metric's declared period type.

## 8a.3 Metric resolution is whole-label, never substring

Substring matching read **"Contribution Profit per Home Sold"** as `contribution_profit` and
emitted a $31 per-home figure as a **$31,000 quarterly total**; and **"Holding costs on sales
– Current Period"** as `holding_costs`, which is one of two rows the filing never totals.
Both pass every downstream check.

Resolution is now exact on the whole label after stripping footnote markers (`(3)`, `(4)(5)`)
and the `(at period end)` qualifier. The qualifier is stripped **for matching only** —
`periods.is_instant_label` still reads it off the original, so the row is correctly typed as
an instant.

## 8a.4 A concept's canonical label outranks a same-spelled ambiguous alias

**An ontology defect, recorded rather than patched around.** `gaap_gross_margin`'s canonical
label *is* `Gross Margin`, and `aliases.yaml` separately declares that exact string ambiguous
across the GAAP and adjusted concepts. The metric was therefore unreachable from its own
name, and every KPI table's `Gross Margin` row yielded nothing — the headline GAAP margin,
invisible.

A whole-label match against a concept's own canonical label now wins. The ambiguity entry's
own note says what it is for: *"Only the qualifier 'Adjusted' separates them, and it is
sometimes only in the row label"* — a rule about bare prose, not about a cell whose entire
contents are the metric's name. Bare and partial matches stay ambiguous and still emit no
claim, and §7's ambiguity protections are unrelaxed.

The same shadowing applies to `gaap_gross_profit` / `Gross profit`. Whether `aliases.yaml`
should stop declaring a concept's own canonical label ambiguous is an ontology decision, not
an extraction one, and is left open (§13).

## 8a.5 Scale applies to monetary values, not to counts

`(in thousands, except percentages)` does not except `Homes sold in period`, and multiplying
the count produced **2,462,000 homes sold in a quarter**. The exception list is a
presentation note about the monetary columns; the *unit* decides whether a scale can apply at
all.

Related: the ontology's `USD_millions` names the unit a metric is customarily *presented* in,
not the scale a given table used — the same metric appears in thousands in a 2021
reconciliation and millions in a 2025 KPI table. Claims carry absolute USD.

## 8a.6 Scale comes from the table header or the preceding passage

§1.3 already carries this correction — the plan originally said the scale was always a
parenthetical in the header row, and building the benchmark showed otherwise. Restated here
because it is what the lane and selection are built around: of the 74 KPI-bearing table
passages, **65 declare the scale inside the table and 9 in the immediately preceding
narrative passage**, and none omit it. Selection therefore attaches the predecessor to every
table candidate (§4.1).

**Precedence: table-local wins.** A table stating its own scale states it for itself, while a
preceding paragraph may introduce several tables. The source is recorded on every claim as
`table_header`, `preceding_context` or `metric_default`, so "nobody declared one" is never
mistaken for "the table said units".

## 8a.7 Table shapes not yet supported

Stated rather than discovered later:

| Shape | Behaviour |
| --- | --- |
| A single period column | No claims — header detection requires ≥2 period-ish cells in a row. No corpus KPI table is like this; a future one could be. |
| Date columns not evenly divisible by duration groups, and the date shapes cannot separate them | **Refused** with `AMBIGUOUS_COLUMN_ALIGNMENT`. No claim. *(corrected in stage 6b — see §8a.7a)* |
| A row label genuinely spanning several cells | Only the first non-empty cell is read as the label. |
| Nested or merged data cells beyond layout gutters | Unrecognised; the row is refused with `AMBIGUOUS_COLUMN_ALIGNMENT` rather than guessed. |

## 8a.7a Duration groups bind to columns by date shape *(stage 6b, `93e771b`→)*

The original rule — even division, else fall back to position — was wrong on a real table,
and the claim beside it that "a wrong answer is caught rather than emitted" was wrong twice
over. **The period-type check catches an instant read as a duration; it does not catch a
duration read as the wrong duration.**

`kpi-table-q4-2023-earnings` has **7 period columns over 2 duration groups**: five full dates
under `Three Months Ended` beside two bare years under `Year Ended December 31,`. Seven does
not divide by two, so the positional fallback read `December 31, 2022` as **FY2022** and
`March 31, 2023` and `June 30, 2023` as **twelve-month durations** — **21 observations with
the wrong period**. None were gold, so the case scored 1.000 on every dimension and the
totals were unaffected. That is exactly how it stayed invisible, and it is why the durable
report (step 6) was worth building before trusting the lane.

**The discriminator.** A column label is either a full date (`March 31, 2023`) or a bare year
(`2023`); a duration phrase either supplies a month and day (`Year Ended December 31,`) or
does not (`Three Months Ended`). They fit together exactly one way — a full-date column
already carries its own month and day, so it belongs to the phrase supplying none; a bare
year is unusable without one, so it belongs to the phrase supplying it. This is a reading of
the layout, not an inference.

Applied only when it is unique: exactly one phrase of each kind, and both column shapes
present. Otherwise even division, which still handles the Q4 2020 reconciliation's four
uniform bare years under two date-supplying phrases. **Otherwise the table is refused** with
`AMBIGUOUS_COLUMN_ALIGNMENT` and emits nothing.

A latent bug surfaced while testing it: the header row carrying the dates usually also
carries the scale declaration in its first cell, and that cell was being counted as a column,
inflating the division the assignment depends on. Group assignment now counts period-shaped
cells only, while change columns still become columns.

## 8a.8 `Homes sold in period` — an unresolved surface form

The Q1 2021 reconciliation labels its homes-sold row `Homes sold in period`, which the
ontology does not carry as an alias. It is the **only** remaining benchmark recall miss (3 of
49 gold observations, all from this one row, row index 17).

**The abstention is `AMBIGUOUS_ALIAS`, not `UNRESOLVED_METRIC`** *(corrected 2026-08-01)*.
The whole label matches nothing, so the widest surface hit inside it is the declared-ambiguous
`homes`, and the lane abstains over four candidates — `homes_purchased`, `homes_sold`,
`homes_under_contract`, `housing_inventory_homes`. The refusal is right and the earlier
account of its mechanism was not.

**Left unresolved deliberately.** Broadening the alias would be fitting the vocabulary to a
fixture. The benchmark case says as much itself — the label "has to resolve semantically or
this row is missed" — which makes it work for the narrative or hybrid-scoped lane, not for a
deterministic reader.

## 8a.9 Table-lane benchmark results *(`22aafa4`)*

11 reviewed table cases, 49 gold observations, 410 observations emitted:

| Measure | Result |
| --- | --- |
| metric recall | **0.939** |
| value accuracy | **1.000** |
| unit accuracy | **1.000** |
| scale accuracy | **1.000** |
| period accuracy | **1.000** |
| subject accuracy | **1.000** |
| evidence accuracy | **1.000** |

Issues: `AMBIGUOUS_ALIAS` 43, `UNRESOLVED_METRIC` 24, `DERIVED_CHANGE_COLUMN` 16,
`DEFERRED_REQUIRED_SOURCE_LANE` 11.

Remaining misses: `homes_sold` at 2021Q1, 2020Q4 and 2020Q1, all from §8a.8's single row.

## 8a.10 Selection results *(`614f1ac`)*

503 table candidates and 3,072 narrative candidates over 24,884 (passage, lane) decisions,
one reason code each. Benchmark required-concept recall **1.000**, known-instance recall
**1.000**, ambiguity preservation **1.000**.

Alias evidence is restricted to **metric** concepts. Admitting entity aliases selected 6,098
narrative passages — in a single-company corpus `opendoor` and `the company` match nearly
everything, and a candidate set that size has stopped discriminating. Entity hits are still
recorded on the candidate for a subject resolver.

## 8a.11 Whole-passage embedding dilutes, and the evidence boundary does not move *(verified 2026-08-01)*

Measured at step 9 while deciding lexical versus hybrid candidate scoping
([STAGE_09_HYBRID_SCOPING.md](STAGE_09_HYBRID_SCOPING.md) §11.4). It is recorded here because
it is a **constraint stage 10 inherits**, not a step-9 detail.

The lexically unreachable wording "59% of our homes in inventory had been listed on the market
for more than 120 days" behaves completely differently at two granularities against the same
131-concept index:

| Granularity | Rank of `pct_homes_on_market_gt_120_days` | Similarity | Margin over next |
| --- | --- | --- | --- |
| the sentence alone (82 chars) | **0** | 0.8307 | 0.1375 |
| the 2,046-character passage it occurs in | **2** | 0.5169 | — |

In that whole passage **nothing** stands clear of the passage's own background at 3 sd. So the
model can paraphrase; what the benchmark measured is that a whole-passage vector cannot use it.

**The evidence boundary remains the normalized passage.** A claim cites a `passage_id`, and
§4.5's `verify` resolves it against `passages.jsonl`.

- Stage 10 **may** focus prompts, or semantic scoring, on **evidence-resolvable spans** inside
  a passage — sentences it can still cite by that passage's id.
- Stage 10 **must not** create synthetic evidence anchors. A sub-passage identifier would not
  resolve in `passages.jsonl`, `verify` would fail it by construction, and evidence that
  cannot be checked is the one thing this pipeline exists to refuse.

## 8a.12 Period attribution is narrowed, not closed — **step 11 must score it** *(2026-08-02)*

§8a.7a records the failure: the period-type check catches an instant read as a duration and
catches nothing about a duration read as the *wrong* duration, and 21 observations were wrongly
dated because of it. Stage 10 narrowed that for the narrative lane by refusing to let the model
state a period at all: `core.periods.period_phrases` collects the phrases a passage prints that
resolve without a model, and the response schema makes them the enum of `period_label`.

**What that achieves, and what it does not.** It removes every period the passage does not
print — including the measured failure it was built for, six claims dated a year early on a
letter that never printed the year. It does **not** make a wrong period unrepresentable, and the
implementation claimed for a while that it did. A comparative MD&A paragraph prints its
prior-period phrase too. On `open-20210930.htm#p142` the enum is:

| position | phrase | resolves to |
| --- | --- | --- |
| 1 | `three months ended September 30, 2021` | 2021Q3 — the reporting period |
| 2 | `September 30, 2021` | 2021-09-30 |
| 3 | `three months ended September 30, 2020` | 2020Q3 — the comparative |
| 4 | `September 30, 2020` | 2020-09-30 |

Attaching phrase 3 to a 2021 figure passes the period-type check, the value check, the evidence
check and `ontology.validate_claims`.

**So it is measured rather than assumed.** Every narrative claim records, in
`extractor_metadata`:

| field | what it says |
| --- | --- |
| `period_label` | which printed phrase the model chose |
| `period_label_char_start` | where that phrase sits in the passage |
| `period_label_in_evidence` | whether it is inside the quoted evidence sentence |
| `period_label_distance_from_evidence` | signed characters to the sentence when it is not — negative before, positive after |

**Step 11 scores period attribution as its own dimension**, using these: the rate at which a
claim's period comes from its own evidence sentence, and the rate at which a claim whose
sentence states no period takes the passage's reporting period rather than a comparative one.
`core.periods.reporting_period_keys` computes the second, and the live gate asserts both
(`test_no_claim_attaches_a_prior_period_to_a_figure_that_does_not_state_one`). None of these
numbers is an evidence anchor: §8a.11's rule is unchanged, and the anchor is the passage id.

**Related, and recorded here so a gate result is not over-read.** A passage that prints no
resolvable period gets an enum of `[""]`, so *every* claim on it is refused with
`MISSING_PERIOD` by construction. The 10-K definitional gate passage
(`open-20201231.htm#p128`) is one of these. Its empty claim list is therefore partly structural,
and a run must say whether the model declined or the schema did before reporting it as evidence
that the lane recognises a definition.

## 8a.13 Step 11 measured it, the residual had a different shape, and the schema was fixed *(2026-08-02)*

`benchmarks/extraction/v1/reports/narrative_lane_v1.{json,md}`, both scopes, replayed from a
committed answer store. §8a.12 asked for the rate at which a claim takes a *comparative*
period. That is not where the failures were.

| | Lexical | Hybrid |
| --- | --- | --- |
| claims emitted | 23 | 24 |
| period phrase inside the quoted evidence sentence | 5 | 6 |
| period resolved to something other than the reporting period | 1 | 1 |
| greatest distance from the evidence sentence | 1,354 characters | 1,354 characters |

**The comparative count is an upper bound.** `reporting_period_keys` is the latest printed end
date of each type, and `event-credit-facility-established-2022` prints a *maturity* date as its
latest, so a correctly dated claim counts as comparative there.

**The model reaches a long way.** Only 5 of 23 claims took the phrase from their own evidence
sentence; the rest reached backwards, up to 1,354 characters.

**The real residual was an unrepresentable period, and it is now closed.** On
`q42021formxex992sharehol.htm#p10` the letter reports both 4Q21 and the full year;
`period_phrases()` yields exactly `['December 31, 2021', '4Q21', '4Q20']`, so the enum could
not express FY2021 at all — and the model attached `4Q21` to the full-year figures rather than
abstaining. That produced `contribution_profit` at both $152M and $525M for 2021Q4 and
`contribution_margin` at both 4.0 and 6.5: four `DUPLICATE_OBSERVATION_CONFLICT` errors, and
§11 criterion 1 failing.

The fix is a schema member, not a prompt tweak: `narrative.public.PERIOD_NOT_PRINTED` is the
last member of the `period_label` enum on every passage, rule 6 names it, and
`response_mapping._resolve_period` maps it to `MISSING_PERIOD` — checked before the
period-type test, because a model declining to name a period has not also made a claim about
that period's kind. `PROMPT_VERSION` is 1.2.0 and the answer store was regenerated whole.
**After the rebuild: zero ontology errors under both scopes.** This is a correctness fix — the
schema could not express a state the corpus has — and STAGE_11 §12.1 records it with the
mechanism.

**Two of the four `period_wrong` failures are this shape, not three.** *(Corrected
2026-08-02; this section and STAGE_11 both said three.)* The four are
`adjusted_gross_margin@FY2021` and `adjusted_gross_profit@FY2021`, which are the
unrepresentable-FY2021 shape; `adjusted_gross_profit@2020Q4`, where `4Q20` **is** printed and
resolvable, making it a plain comparative miss; and
`pct_homes_on_market_gt_120_days@2022-12-31`, which is on a different passage entirely.

The enum still does **not** make a wrong period unrepresentable: a comparative paragraph
prints its prior-period phrase and the enum offers it. What is now representable is "none of
these".

---

# 9. Implementation sequence

The authoritative order. Each step ends with a green offline suite and its own narrow commit.

| # | Step | Stage (§4) | State | Gate |
| --- | --- | --- | --- | --- |
| 0 | Encoding prerequisite, re-normalize, re-verify | — (§8) | **done** `a0eb6bc` `928e806` | 0 mojibake passages; 294 / 12,442 / 1,935; catalogs byte-identical |
| 1 | Repository documentation corrected | — | **done** `7b063a6` | No stale cross-references |
| 2 | Reviewed benchmark constructed | §4.0 | **done** `0cc3678` | 26 cases, 69 gold claims (68 at `0cc3678`), integrity test green |
| 3 | Shared contracts, identities, assembly, validation | §4.4 §4.5 §5 §6 | **done** `f92533a` | Structural + conformance tests; no provider reachable |
| 4 | Typed candidate selection | §4.1 | **done** `614f1ac` | One reason code per (passage, lane); benchmark recall 1.000 |
| 5 | Deterministic table lane | §4.2 | **done** `22aafa4` | Recall ≥0.93; six dimensions at 1.000 |
| 6 | Table-lane benchmark evaluation report | §4.0 | **done** `1d37eba` | `benchmarks/extraction/v1/reports/table_lane_v1.{json,md}`; two regenerations byte-identical |
| 7 | Local Qwen runtime and real provider | §4.3 | runtime **done** `614f1ac`; provider not started | Provider defaults to `enable_thinking: false`; config test |
| 8 | Lexical candidate scoping | §4.2a | **done** `e97ad5a` | `LexicalOntologyCandidateScope` never removes a protected candidate |
| 9 | Embedding index and hybrid scoping | §4.2a | **done** (uncommitted) | Cache keyed by `definition_hash`, model id, dimensions, renderer version; §7 rule applied, **default deferred to step 13** |
| 10 | Narrative metric extraction | §4.3 | **done** (uncommitted) | Marked `live`; offline suite green at 1,499, live at 49. See [STAGE_10_NARRATIVE_LANE.md](STAGE_10_NARRATIVE_LANE.md) §12a for what review corrected |
| 11 | Narrative benchmark evaluation | §4.0 | **done** (uncommitted) | `reports/narrative_lane_v1.{json,md}`, both scopes, replayed offline from `answers/narrative_v1.jsonl`; offline suite 1,537, live 49. See [STAGE_11_NARRATIVE_EVALUATION.md](STAGE_11_NARRATIVE_EVALUATION.md) §8–§11 for the scores, the step 13 recommendation and four corrections to that brief |
| 12 | Representative event and relationship extraction | §4.4 | not started | The benchmark's 4 events and 2 relationships |
| 13 | Full benchmark comparison and recommendation | §4.0 | not started | Lexical vs hybrid scoping **decided here**, on step 11's extraction evidence |

Steps 0–6 and 8 are fully offline. Step 7 introduces the only provider; steps 9–13 use it.

`catalog` (§4.6) and the run manifest fold into step 13, since a derived index of claims is
only meaningful once both lanes emit.

**Where reports and catalogs live.** Benchmark evaluation reports are durable artifacts of
the benchmark, not of a corpus run, so they are committed under
`benchmarks/extraction/v1/reports/` — `table_lane_v1.{json,md}` at step 6,
`narrative_lane_v1.{json,md}` at step 11. Run-specific derived catalogs belong to a run and
live under `data/extraction_runs/<run_id>/` (step 13), which is gitignored like the rest of
`data/`. Keeping them apart stops a benchmark result from being mistaken for corpus data, and
stops a report from being silently overwritten by a run.

## 9.1 Numbering used before this revision

Steps 0–5 were executed under an earlier numbering, and their commit messages use it. It
matched this table except that the benchmark had no step of its own. Recorded so old messages
stay readable:

| Earlier instruction step | This plan |
| --- | --- |
| 0 — encoding fix | 0 |
| 1 — README correction | 1 |
| 2 — benchmark construction | 2 |
| 3 — shared contracts and validation | 3 |
| 4 — typed candidate selection | 4 |
| 5 — deterministic table lane | 5 |

An older `§9` in the first draft of this plan numbered the same work 0–9 without the
benchmark; `core/` and `contracts.py` were its steps 1–2, `select` its step 3 and `tables`
its step 4. Commit `4af55ae` refers to that draft.

---

# 10. Tests

- Architectural rules executable, not documented: extraction imports normalization's
  catalog and the ontology's public contract, and **nothing** imports a stage from another
  stage. Import-direction and cycle tests, as in the other two packages.
- Fixtures from the real corpus, saved and committed — including at least one passage in
  the pre-fix mojibake form, so `numbers.py` stays robust to text from any corpus rebuilt
  before the §8 fix.
- Every §7 policy has a test that fails when the policy is removed.
- Provider-touching narrative tests marked `live`; `pytest -m "not live"` stays green.
- Tests written during implementation, not after.

---

# 11. Acceptance

1. Every emitted claim passes `ontology.validate_claims` with zero errors.
2. Every evidence reference resolves to a real passage in `passages.jsonl`.
3. No claim uses a lane in its metric's `forbidden_source_lanes`.
4. Two runs over the same corpus produce byte-identical catalogs.
5. Claims exist for all 20 in-scope metrics, with a stated count per metric and a stated
   reason for every candidate passage that produced none.
6. The six §16 questions are each answered by a policy with a test, or recorded as an
   explicit deferral with a reason (§7.4, §7.5).

7. Every lane is scored against the reviewed benchmark (§4.0) on all eight dimensions, with
   per-case results, and its abstentions are scored as answers rather than silences.

Criterion 5 is deliberately "a stated count", not a coverage threshold. §1.5 gives a floor
from naive matching; setting a target before the table lane runs would be inventing a
measurement.

**Met so far**, against the table lane: criterion 2 (evidence accuracy 1.000), criterion 3
(no claim uses a forbidden or deferred lane), criterion 7 for the table half. Criteria 4 and 5
need `catalog` wired into a run; criterion 6 is answered by §7 and tested.

**Criterion 1 held for the narrative lane on the second measurement at step 11**
*(2026-08-02)*. It did not on the first: four `DUPLICATE_OBSERVATION_CONFLICT` errors, two
distinct — on `q42021formxex992sharehol.htm#p10` the lane emitted `contribution_profit` at
both $152 million and $525 million for 2021Q4, and `contribution_margin` at both 4.0 and 6.5,
so two different values collided under one deterministic observation id. The cause was
§8a.13's unrepresentable-period residual, not a defect in the id scheme; the id was doing
exactly what it exists to do, which is to make the collision visible. §8a.13 gave the schema a
member for "no printed phrase gives this figure's period", the model uses it, the full-year
figures are refused rather than misdated, and the rebuilt report carries **zero ontology
validation errors under both scopes**. No `assemble`-level §7 policy rejected any claim, and
criterion 7 is met for the narrative half under both candidate scopes.

**Criterion 7's "all eight dimensions" was only ever seven for the table lane, and twelve is
what the narrative half needed.** `table_lane_v1.json` reports `metric_recall`,
`matched_over_emitted` and six per-match dimensions; it never scored `ambiguity`, which §4.0
names as one of the eight, and it now carries a `score_caveats` block saying so.
`narrative_lane_v1.json` scores twelve: the eight, plus §7.1's population wording and §7.3's
ambiguity codes — neither of which any report scored until review found it — plus the split of
"ambiguity" into an abstention-honouring rate, a code-agreement rate, and ambiguity
preservation over a denominator of 1. Each denominator is stated. Two of the twelve are
tautologies under the shared matching rule — claims are matched on `(metric_id, period key)`,
so `period_accuracy: 1.000` is a property of the matcher rather than a measurement of either
lane, and both reports now say so.

**Population wording is 0.000 and it is the result criterion 7 was worth having.** Every
matched `pct_homes_on_market_gt_120_days` pair disagrees with gold about the filed denominator
— gold carries the whole clause, the lane carries the noun phrase inside it — and until
2026-08-02 the dimension scored 1.000 by not existing. §7.1 says verbatim; whether the
reviewers want the clause or the phrase is a founder decision, and the disagreement is now
visible either way.

---

# 12. Rejected options

| Option | Why rejected |
| --- | --- |
| One model-backed extractor for both lanes | The table lane is mechanical (§1.3) and fully testable offline. Routing it through a model would trade determinism and a green offline suite for nothing. |
| Extract all 26 metrics, falling back to narrative for the 6 GAAP figures | Their authoritative source is a tagged XBRL fact that exists in 364 already-acquired artifacts. A narrative fallback would be a worse number with a weaker citation. |
| Build the XBRL lane in this plan | Doubles scope and makes acceptance depend on a lane the normalized corpus does not contain. Separate plan, no new acquisition needed (§3.2). |
| Emit `SUPERSEDES` during extraction | A per-passage extractor cannot see the other filing. Graph-layer policy (§7.5). |
| Pin the `norm:` ID grammar into `claims.yaml` | Couples the vocabulary to one corpus. The extraction verifier owns it instead (§2). |
| Fix the encoding defect after extraction | `content_sha256` is content-addressed; later means a hash migration (§8). |
| Run the table lane over all 1,935 table passages | 131 are contract and governance boilerplate. A weaker argument than it looked pre-fix (§1.2), but typed selection also keeps the reason a passage was skipped auditable. |

---

# 13. Open decisions

0. **Should `aliases.yaml` stop declaring a concept's own canonical label ambiguous?**
   Raised by §8a.4. `gaap_gross_margin` is labelled `Gross Margin` and that same string is
   declared ambiguous, so the metric is unreachable from its own name; `gaap_gross_profit` /
   `Gross profit` is the same shape. Extraction works around it with canonical-label
   precedence, which is sound for a table cell but does not help a bare prose mention.
   **Recommendation:** leave the vocabulary alone for now and revisit once the narrative lane
   has measured how often a bare mention actually occurs — changing `aliases.yaml` shifts
   `definition_hash` and invalidates the concept vectors step 9 **has now built**
   (`benchmarks/extraction/v1/vectors/`, rejected outright on a key mismatch, never partially
   reused). The cost of that revision is now concrete: a rebuild against the embedding server
   and a regenerated `hybrid_scope_v1.{json,md}`.

1. **The narrative-lane provider is fixed; the prompt strategy is not.** This decision was
   deliberately left open until the claim contract was proven by the table lane. It has since
   been made and validated (`614f1ac`, `LOCAL_RUNTIME_VALIDATED.md`):

   | | |
   | --- | --- |
   | model | `Qwen3.5-9B-Q4_K_M.gguf` (`unsloth/Qwen3.5-9B-GGUF`) |
   | runtime | local `llama.cpp` OpenAI-compatible server |
   | context | 8,192 |
   | GPU offload | `-ngl 99`, flash attention on |
   | KV cache | `q8_0` / `q8_0` |
   | thinking | **`enable_thinking: false`** |

   Thinking is off by default and not a tuning knob. With it on, a four-field schema request
   spent all 900 tokens reasoning and returned empty content; with it off the same request
   returned schema-conformant JSON in 59 tokens. Leaving it on would make the lane's budget
   failures present as extraction failures.

   **What remains to be measured**, not chosen in advance: prompt and schema design, and
   retry and repair behaviour on malformed output. No larger or alternative model is compared
   in v1.

   **The scoping half of this is now partly answered and explicitly still open.** Steps 8 and
   9 measured *reachability* and nothing else: the lexical scope reaches 0.960 of required
   concepts (0.959 before the 2026-08-02 benchmark correction, §4.0a), the hybrid scope at
   the derived `top_k` 2 reaches 0.980, and no §7 criterion is
   damaged by the addition
   ([STAGE_09_HYBRID_SCOPING.md](STAGE_09_HYBRID_SCOPING.md) §11.3c). What that report cannot
   say is which scope produces better *claims*, and no gold-independent heuristic establishes
   the right runtime `top_k`. `scoping.strategy` therefore stays `lexical`, step 11 runs the
   narrative benchmark under **both** scopes, and **step 13 decides the default** on that
   evidence.

   **Step 11 has now produced that evidence, and it is narrow** *(2026-08-02,
   `reports/narrative_lane_v1.md`)*. The two scopes issue the **identical request** — measured
   as the digest the answer store is keyed on, not inferred from the concept lists — on **13 of
   the 14** prose cases, so the comparison rests on one case. On it, the concept only hybrid
   supplies produces a gold claim (metric identity 0.526 → 0.579) that is correct on value,
   unit, scale, period, subject and evidence and **wrong on population wording**: gold declares
   `5% of our homes were listed on the market for more than 120 days` and the claim carries
   `our homes`. No gold observation is clean under one scope and not the other.

   **Recommendation for step 13, restated: hybrid reaches more and is not clean on what it
   reaches.** The first pass of step 11 recommended "hybrid, on narrow evidence" on the
   strength of a claim it called correct on every dimension. That was true of the six
   dimensions then being scored and false of the claim — §7.1's population wording was scored
   by nothing. The recommendation is restated on the fuller set rather than preserved.

   Two things not to read into the numbers: `value_accuracy` and `metric_identity_accuracy` are
   both higher under hybrid and neither means hybrid corrected anything — adding one matched
   claim moves the denominator, 8/10 → 9/11. And the §16.1 wording gap is *not* closed: the
   second paraphrase is unreached under both scopes, because it ranks 2 in its passage and
   `top_k` is 2. `scoping.strategy` is still `lexical` and step 11 did not change it.
2. **Settled, not open — recorded because an earlier draft said otherwise.** This plan no
   longer covers `metric_observation` only. **V1 includes the benchmark's representative 4
   event claims and 2 relationship claims** (step 12), as a bounded proof that the same claim
   path carries non-metric payloads: the same provider, candidate-scoping boundaries,
   evidence validation, ontology validation and abstention behaviour.

   **Broad event and relationship extraction remains follow-on work**, including a sweep of
   the 64 material agreements, which deserves its own selection policy rather than an
   afterthought in a metrics plan.

   One thing genuinely still open: `select` routes only metric candidates today, so step 12
   may need the smallest typed routing extension that reaches those six cases. Anything
   wider than the benchmark subset is a founder gate.
