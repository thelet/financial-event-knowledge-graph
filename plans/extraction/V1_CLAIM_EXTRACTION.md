# v1 — Claim Extraction

**Status:** plan. Nothing implemented.
**Scope:** turn the normalized corpus into validated `OntologyClaim` objects carrying
evidence that points back into that corpus, for the 20 metrics the corpus can actually
support.

Consumes the corpus built by
[../normalization/V1_DOCUMENT_NORMALIZATION.md](../normalization/V1_DOCUMENT_NORMALIZATION.md)
and the vocabulary built by
[../ontology/ONTOLOGY_V1_IMPLEMENTATION.md](../ontology/ONTOLOGY_V1_IMPLEMENTATION.md).

```text
Normalized passages → candidate selection → lane extraction → claim assembly
                    → ontology validation → claim catalog
```

**Out of scope, and enforced by a structural test:** graph construction, Neo4j, Graphiti,
entity resolution beyond the single anchor company, embeddings, vector search, RAG,
question answering, post generation, XBRL fact extraction (§3.1), transcript or
peer-company acquisition.

Facts marked *(verified)* were measured against the committed corpus and the installed
libraries on **2026-08-01**, by the commands recorded beside them. Facts marked
*(unverified)* are design intent that the implementation must confirm.

---

# 1. What the normalized corpus actually offers

## 1.1 Shape *(verified)*

`data/normalization_catalog/` holds 294 documents and **14,203 passages** — 11,216
narrative, 2,987 table. Issues: 60 `NEEDS_REVIEW`, 30 `HIERARCHY_UNCERTAIN`, 9
`PARSER_FALLBACK`.

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
| narrative_primary | 8,865 | 1,648 |
| material_agreement | 2,427 | 1,130 |
| earnings_release | 959 | 145 |
| governance | 800 | 44 |
| structured_exhibit | 28 | 10 |
| other | 337 | 9 |
| shareholder_letter | 619 | **1** |
| supplemental | 168 | 0 |

Two consequences that shape the whole design:

**`material_agreement` contributes 1,130 of 2,987 table passages and no KPIs.** The most
common table heading in the corpus is `Whereas:` (941 occurrences) — credit-agreement
recitals. Running a metric extractor over all 2,987 tables would spend 38% of its work on
contract boilerplate. Candidate selection must be typed, not exhaustive (§4.1).

**Shareholder letters have exactly one table across 26 documents.** The KPI commentary in
letters is prose. Any design that treats "KPIs live in tables" as universal loses the
letter lane entirely — and the letters are where the 120-day-portfolio wording that
ontology §16.1 flags as high-impact actually appears.

## 1.3 Table passages are Markdown pipe tables, and they are sparse *(verified)*

`norm:0001801169:0001801169-21-000016:q12021form8-kxexhibit991.htm#p20`:

```text
|  |  | Three Months Ended |  |  |  |  |
| (in thousands, exceptÂ percentages) |  | March 31, 2021 |  | December 31, 2020 |  |
| Gross profit (GAAP) |  | $ | 97,132Â |  |  | $ | 38,365Â |
| Gross Margin |  | 13.0Â | % |  | 15.4Â | % |
| Adjusted Gross Profit |  | $ | 97,038Â |  |  | $ | 38,228Â |
| Adjusted Gross Margin |  | 13.0Â | % |  | 15.4Â | % |
```

Everything a claim needs is present but scattered across cells that HTML layout, not
meaning, decided:

- the **metric label** is in column 0;
- the **currency sigil** `$`, the **magnitude** and the **percent sign** occupy three
  separate columns;
- the **period** lives two header rows up, split between a duration phrase
  (`Three Months Ended`) and a date (`March 31, 2021`);
- the **unit scale** (`in thousands`) is in a parenthetical in the header row, not
  beside the number.

A row-and-column reader must therefore collapse empty columns before aligning a value to
its period header. This is mechanical and fully testable offline — it is the reason the
table lane needs no model (§4.2).

## 1.4 The corpus carries an encoding defect *(verified — root cause reproduced)*

**7,920 of 14,203 passages (55.8%), across 268 of 294 documents, contain an orphaned `Â`
or `â`.** Visible above as `97,132Â` and `exceptÂ percentages`.

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
`normalization/stages/parse/sec_html_parser.py:179` and `:233` then re-encode that string
with `source.encode("utf-8")` and hand the bytes to `parse_html_bytes`. Because the
document declares no charset, lxml falls back to latin-1 and reads `\xc2\xa0` as `Â` +
NBSP. A later whitespace pass folds the NBSP into a space, leaving the `Â` stranded — no
passage in the corpus still contains a real U+00A0 *(verified)*.

`parse_html_bytes` taking bytes is correct and documented (91 artifacts carry an XML
declaration that makes `lxml.html.fromstring` reject a `str`). The defect is only at the
two call sites that manufacture bytes from a string we already decoded, without telling
lxml the encoding they used.

**What this does and does not cost.** I expected it to break metric-name matching. It does
not: stripping `Â`/`â` and re-running alias matching over all 14,203 passages recovers
**zero** additional hits, in either lane *(verified)*. The damage is confined to value
parsing — `97,132Â`, `13.0Â`, `DecemberÂ 31` — which a number parser must strip anyway.

It is still a prerequisite (§8), for a different reason than I first assumed: passage
`content_sha256` and `fragment_sha256` are content-addressed. Fixing the encoding after
extraction has emitted claims invalidates every evidence hash in the graph and forces a
re-verification pass. `passage_id` is positional (`#p20`) and survives, so the blast radius
is the hashes, not the anchors — but re-normalizing first is hours, and re-normalizing
later is a migration.

## 1.5 Candidate density *(verified)*

Case-insensitive substring match of each coverable metric's label and declared aliases
against all 14,203 passages. A **floor**, not an estimate — naive matching, no
normalization of the metric label:

| Metric | Passages | Documents |
| --- | --- | --- |
| homes_sold | 554 | 86 |
| adjusted_ebitda | 490 | 90 |
| contribution_margin | 444 | 87 |
| holding_costs | 445 | 84 |
| contribution_profit | 432 | 78 |
| adjusted_gross_profit | 340 | 80 |
| gaap_gross_margin | 308 | 87 |
| direct_selling_costs | 203 | 84 |
| adjusted_ebitda_margin | 200 | 85 |
| adjusted_gross_margin | 190 | 84 |
| housing_inventory_homes | 133 | 75 |
| homes_purchased | 128 | 70 |
| home_price_appreciation | 109 | 43 |
| contribution_profit_after_interest | 94 | 27 |
| homes_under_contract | 55 | 42 |
| mortgage_rate | 44 | 28 |
| acquisition_contracts | 10 | 6 |
| pct_homes_on_market_gt_120_days | 10 | 10 |

Aggregate: **1,150 narrative and 131 table passages** mention at least one coverable
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

## 4.1 `select` — typed candidate selection

Emits a `CandidatePassage` per (passage, lane) with a recorded reason code, so a passage's
absence from extraction is as auditable as its presence — the same discipline
`selection.jsonl` already applies to artifacts.

Policy, from §1.2 and §1.5:

| Lane | Included `document_type` | Rationale |
| --- | --- | --- |
| tables | earnings_release, narrative_primary, structured_exhibit, supplemental | 1,803 table passages; where reconciliation and KPI tables live |
| narrative | earnings_release, shareholder_letter, narrative_primary | letters are prose-only (§1.2) |
| — excluded | material_agreement, governance, other | 1,183 table passages of recitals and boilerplate, no KPIs |

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

# 8. Prerequisite: fix the encoding defect first

Fix the two call sites in `normalization/stages/parse/sec_html_parser.py` (§1.4) so lxml is
told the encoding of bytes we produced ourselves, re-run normalization, and re-verify.

Order matters for the reason given in §1.4, not the one I first assumed: `content_sha256`
is content-addressed, so this is hours now and a hash migration later. `passage_id` is
positional and unaffected, so nothing about the ID scheme in §5 changes.

**This is a normalization change, and belongs in a normalization commit** with its own
before/after counts — not folded into extraction work. Expected result: the 7,920-passage
count falls to 0, and `V1_DOCUMENT_NORMALIZATION.md` gains a correction note recording that
the original run shipped this defect and why it was missed.

---

# 9. Implementation sequence

Each step ends with a green suite.

| # | Step | Gate |
| --- | --- | --- |
| 0 | Fix the encoding defect, re-normalize, re-verify (§8) | 0 passages with `Â`/`â`; document counts unchanged at 294/14,203 |
| 1 | `core/` models, identifiers, `periods.py`, `numbers.py` | Unit tests from committed real cells |
| 2 | `contracts.py`, `public.py` per stage, `context.py` | Structural + conformance tests |
| 3 | `select` stage + `config/extraction.yaml` | Candidate counts match §4.1 policy exactly |
| 4 | `tables` lane | Committed reconciliation-table fixtures extract correctly |
| 5 | `assemble` + §7 policies | Every §7 policy has a test that fails without it |
| 6 | `verify` | Dangling evidence and forbidden-lane claims both fail the run |
| 7 | `catalog` + manifest | Two runs byte-identical |
| 8 | `narrative` lane | Marked `live`; suite still green offline |
| 9 | Report + sweep README and the two upstream plans | No stale cross-references |

Steps 0–7 are fully offline. Only step 8 introduces a provider.

---

# 10. Tests

- Architectural rules executable, not documented: extraction imports normalization's
  catalog and the ontology's public contract, and **nothing** imports a stage from another
  stage. Import-direction and cycle tests, as in the other two packages.
- Fixtures from the real corpus, saved and committed — including at least one passage
  carrying the pre-fix mojibake, so `numbers.py` proves it handles both forms.
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

Criterion 5 is deliberately "a stated count", not a coverage threshold. §1.5 gives a floor
from naive matching; setting a target before the table lane runs would be inventing a
measurement.

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
| Run the table lane over all 2,987 table passages | 1,183 are contract and governance boilerplate; the top table heading corpus-wide is `Whereas:` (941). Typed selection instead (§4.1). |

---

# 13. Open decisions

1. **Narrative-lane provider and prompt strategy** — deliberately unspecified until the
   claim contract is proven by the table lane (§4.3). It is a step-8 decision, and making
   it now would be choosing a provider before knowing what the contract demands.
2. **Whether `select` should also emit candidates for event and relationship claims.** This
   plan covers `metric_observation` only. The ontology defines `event_claim` and
   `relationship_claim`, and the corpus plainly contains both (partnership announcements,
   credit facilities, the 64 material agreements). **Recommendation:** yes, but in a
   follow-on plan — metric observations exercise the full path end to end at the smallest
   scope, and the 64 material agreements deserve their own selection policy rather than an
   afterthought in a metrics plan.
