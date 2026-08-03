# F0 — contract extension and confirmed-defect repair

The first stage of [V1_OPENDOOR_FACTUAL_SPINE.md](V1_OPENDOOR_FACTUAL_SPINE.md). Nothing here
ingests a new source; F0 exists so that F1, F3, F5, F6 and F8 do not each rediscover the same
three blockers.

**Status:** planned 2026-08-03, against `extract-v1-lexical-2422c4252c07` and
`graph-v1-886059d862ce`. Branch `impl/f0-factual-spine-contracts`, base `df50be9`.

| | |
| --- | --- |
| Scope | evidence contract · observation identity · ontology enums · instance-property projection · **plus** repair of the six confirmed period-assignment defects |
| Explicitly not in scope | Companyfacts acquisition · inline XBRL parsing · XBRL fact emission · full provider run · transcripts · prices · guidance extraction · reconciliation · retrieval |

---

## 0. The two responsibilities, kept apart

F0 does two unrelated things and they must not be allowed to hide each other:

1. **Part A repairs current data.** It changes what the corpus says. Six observations are wrong
   and must stop existing; their ids disappear and the catalogs change.
2. **Parts B–E extend contracts.** They change what the corpus *can* say. No existing
   observation's value, unit or period may move.

**These land as separate commits with separate verification.** A reader must be able to see, from
the history alone, which artifact changes came from correcting a fact and which came from
widening a contract. If Part A and Part B were one commit, "2,707 → 2,701 observations" and "every
row gained a field" would be indistinguishable causes of one hash change.

---

## 1. Part A — the six confirmed defects

### 1.1 Verified inventory

Measured 2026-08-03. Each group holds exactly one wrong observation; the majority value in every
group is corroborated by other filings and is correct.

| # | Metric | Period | Wrong value | Correct | Passage | Lane |
| --- | --- | --- | ---: | ---: | --- | --- |
| 1 | `market_count` | instant 2021-03-31 | **44** | 27 | `open-20220331.htm#p122` | tables |
| 2 | `market_count` | instant 2022-03-31 | **53** | 45 | `open-20230331.htm#p109` | tables |
| 3 | `market_count` | instant 2023-03-31 | **50** | 53 | `open-20240331.htm#p112` | tables |
| 4 | `market_count` | instant 2023-12-31 | **53** | 50 | `q42023formxex992sharehol.htm#p20` | narrative |
| 5 | `pct_homes_on_market_gt_120_days` | instant 2023-12-31 | **55** | 18 | `q42023formxex992sharehol.htm#p20` | narrative |
| 6 | `housing_inventory_homes` | instant 2023-12-31 | **12,788** | 5,326 | `q42023formxex992sharehol.htm#p20` | narrative |

Observation ids to disappear (verified present in the current run):

```
obs:market-count:opendoor:2023-12-31:normalized-narrative:46093176cdf5
obs:housing-inventory-homes:opendoor:2023-12-31:normalized-narrative:46093176cdf5
obs:pct-homes-on-market-gt-120-days:opendoor:2023-12-31:normalized-narrative:46093176cdf5
```
plus the three table-lane ids at `open-2022/2023/20240331.htm` (to be recorded exactly by A1).

**These are two different bugs that happen to produce the same symptom.** Treating them as one
would produce a fix that works on three rows and is a special case on the other three.

### 1.2 Defect A — table lane, group-to-column binding *(defects 1–3)*

`extraction/stages/tables/header_analysis.py:_assign_groups` decides which duration heading governs
which date column. Colspan does not survive normalization (`normalization/stages/parse/tables.py:43`
records only a `has_merged` boolean and discards the span), so the function reconstructs the
binding with three rules in order: shape-based, **even division**, then positional.

Measured on `open-20220331.htm#p122`:

```
group spans : [(row 2, col 6, 'March 31,'), (row 2, col 8, 'Year Ended December 31,')]
date cells  : [(6,'2022'), (8,'2021'), (10,'2020'), (12,'2019')]
even division: 4 cells / 2 phrases = 2 each  ->  cols 6,8 = 'March 31,'
```

So `2021` — a child of `Year Ended December 31,` whose heading *starts at its own column* — is
bound to `March 31,`, and the Dec-31-2021 market count of 44 is emitted as instant `2021-03-31`.
The true split is 1 + 3.

**Positional binding would read this table correctly**, but it runs last, deliberately: the
docstring records that the Q4 2020 reconciliation (`Three Months Ended December 31,` /
`Year Ended December 31,` over `2020 2019 2020 2019`) is bound *wrongly* by position and
*correctly* by even division. Both layouts put two headings on the first two date columns. From
the collapsed grid they are structurally identical, which is why one rule cannot serve both.

**The discriminator is the date labels themselves, and it is exact on this corpus.** Two column
groups presented side by side necessarily *repeat* their period labels — `2020 2019 2020 2019`,
`2021 2020 2021 2020`. A single descending run of *distinct* labels — `2022 2021 2020 2019` —
cannot be two parallel groups, because no group repeats a period.

Measured over all 1,935 table passages: even division and positional binding disagree on **92**
tables.

| Date labels | Tables | Correct rule |
| --- | ---: | --- |
| Repeat (`2021 2020 2021 2020`) | **87** | even division — parallel groups |
| All distinct (`2022 2021 2020 2019`) | **5** | positional — sequential groups |

The 5 distinct-label tables are exactly the *Expansion into New Markets* table in each Q1 10-Q:
`open-2021/2022/2023/2024/20250331.htm`. Three of them produce defects 1–3; the other two
(`#p144`, `#p101`) produce further mis-dated readings that this fix also removes.

**Rule to add, between even division and positional:** when even division and positional binding
disagree, prefer even division only if the date labels repeat; otherwise prefer positional. When
labels are all distinct *and* positional is unsupported, refuse with the existing
`AMBIGUOUS_COLUMN_ALIGNMENT` rather than guessing.

This is a reading of the rendered layout, not a filename, metric or value special case — the
requirement in the task brief. It changes the binding on **5** tables and leaves 87 untouched.

### 1.3 Defect B — narrative lane, a table that was never a table *(defects 4–6)*

`q42023formxex992sharehol.htm#p20` is classified `passage_kind: narrative`, but its text is a
**de-structured table** — the shareholder letter's grid did not survive parsing:

```
22 Three Months Ended Year Ended December 31, December 31, 2023 September 30, 2023
June 30, 2023 March 31, 2023 December 31, 2022 2023 2022 Revenue $ 870 $ 980 $ 1,976 ...
```

Seven period headers and seven value runs, flattened into one line of prose. The narrative lane
has no column-binding machinery — it cannot have any, because there are no columns left — so the
model bound `December 31, 2022` values to `2023-12-31`. The same document says the right answer
in real prose at `#p9`: *"As of December 31, 2023, 18% of our homes had been listed on the market
for more than 120 days."*

**This is not fixable in header analysis and must not be fixed by editing the recorded answer.**
The stored answers are evidence of what the model returned; hand-correcting one would make the
replay store a fiction.

The honest fix is a **refusal**: a narrative passage carrying a flattened period grid cannot
ground a period, and the lane must say so. The signal is structural and measurable — several
resolvable period labels in one narrative passage, with no sentence structure between them.
A2 owns choosing the exact predicate and threshold, subject to two constraints:

- it must be expressed over passage structure, never over a document name or metric id;
- it must refuse, not guess. A wrong period silently corrected is worse than an absent fact.

A new abstention code is expected (working name `PERIOD_NOT_GROUNDED_IN_PASSAGE`). Correct
narrative observations elsewhere — including the 18% at `#p9` — must survive.

### 1.4 Acceptance for Part A

- All six wrong observations are absent from the rebuilt catalogs.
- The correct value in each of the six groups survives, with its id unchanged.
- No observation id outside the affected passages changes. Any that does is explained.
- The two further mis-datings at `#p144` and `#p101` are also gone.
- A before/after audit is produced (§5).
- The wrong readings become **refusals**, not warnings and not `counter_evidence`.

---

## 2. Part B — the evidence contract

### 2.1 The blocker

`extraction/core/validation.py:validate_evidence_resolves` requires `reference.passage_id` on
every evidence reference and emits `EVIDENCE_MISSING` otherwise. `graph/core/citations.py:
required_passage_id` refuses an empty one, and it guards **claim, issue and rejection rows**, not
only evidence rows; `graph/core/inputs.py:ClaimRow.passage_id` is a required `str`.

`EvidenceReference` already declares `xbrl_concept`, `accession`, `source_url` and
`disclosure_channel_id`, and `claims.yaml` already declares `xbrl_fact`
(`required_fields: [xbrl_concept, accession]`) and `external_page`. **The model anticipated this;
the validators did not.**

### 2.2 A discriminated contract, not an optional field

Making `passage_id` optional and accepting anything would trade a blocker for a silent hole.
B implements an explicit variant per evidence kind, each with its own required fields:

| Kind | Required | Passage required |
| --- | --- | --- |
| `normalized_passage` | `passage_id`, resolvable `document_id` | **yes** |
| `normalized_table` | `passage_id` or `table_id`, `document_id` | yes |
| `xbrl_fact` | `accession`, `xbrl_concept`, `source_url`; context/fact coordinates once F1 exists | no |
| `external_page` / market data | provider, stable source identity, `fetched_at`, row identity, instrument, session date | no |
| `calculated` | input observation ids, calculation expression, calculation version | no — and it **may not** cite a filed passage |

Rules the validator must enforce:

- exactly one kind per evidence object; mixtures fail;
- empty evidence fails, as today;
- a `passage_id` may not be supplied for a non-passage kind — fabricating one is the specific
  failure this contract exists to prevent;
- passage evidence validates **exactly as it does today**. No existing check is weakened.

Transcripts reuse `normalized_passage` if and only if they normalize into `Document` + `Passage`;
the factual-spine plan §6.4 prefers that, so no transcript-specific variant is added in F0.

### 2.3 Graph representation

Passage evidence keeps `Fact -[:EVIDENCED_BY]-> Passage -[:PART_OF]-> Document` unchanged.

Non-passage evidence gets a typed evidence-source node — **never a fabricated `:Passage`**.
E owns the choice between one general `:EvidenceSource` node with a `kind` property and one label
per kind, judged against the existing loader allowlists (`loader.CONCRETE_LABELS`,
`schema.CONSTRAINED_RELATIONSHIP_TYPES`), which are hand-maintained and must be updated in step.

Because F0 ingests nothing, this is proved with **contract fixtures**: representative XBRL,
market-data and calculated evidence rows that project and load without a live source lane.
No APOC. Neo4j stays rebuildable from the catalogs.

---

## 3. Part C — observation identity

### 3.1 The collision

`digest()` over no parts is the constant `e3b0c44298fc` *(verified)*. An XBRL observation has
neither `passage_id` nor grid coordinates, so every XBRL reading of one `(metric, subject,
period, lane)` mints the same id:

```
obs:revenue:opendoor:FY2020:xbrl:e3b0c44298fc   # original FY2020 10-K
obs:revenue:opendoor:FY2020:xbrl:e3b0c44298fc   # FY2021 10-K comparative
obs:revenue:opendoor:FY2020:xbrl:e3b0c44298fc   # FY2022 10-K comparative
```

The FY2020 revenue triple the plan requires as three observations would be one row three times.

### 3.2 Extend the existing mechanism

No UUIDs and no parallel identity system. The XBRL lane supplies a `structural_position` in the
same way the table lane supplies grid coordinates. For XBRL it must carry at least:
`accession`, `fy`, `fp`, concept, and context identity — with room for dimensional identity when
F1 needs it.

The contract must distinguish original from comparative filing, multiple contexts, dimensions,
and amended filings, while keeping an exact repeated copy stable.

**A defensive change belongs here too:** an empty digest input should be refused rather than
silently producing the constant. A lane that supplies no discriminator is a lane whose ids are
not unique, and the identifier module should say so instead of minting `e3b0c44298fc`.

### 3.3 Compatibility

- All 2,707 existing ids unchanged, except those removed by Part A.
- Fixture XBRL ids unique, deterministic, and order-independent.
- Duplicate-id checks operate over the whole output, not per batch.

---

## 4. Parts D and E — ontology and projection

**D1 `AssertionType.GUIDED`.** `guidance_issuance.inference_restrictions` requires an
`assertion_type` other than `reported`, and no member fits. Add `GUIDED`; do not overload
`calculated` (which triggers required calculation fields), `classified` or `inferred`.

**D2 typed guidance values.** `low_value`/`high_value`/`unit`/`currency` become typed rather than
`dict[str, str]`, so `"1.0 billion"` cannot enter where a scaled number belongs. Point guidance
sets both bounds equal; qualitative guidance carries neither and is not forced to invent one.
No guidance extraction, no revision or status derivation.

**D3 future-period invariant.** No constraint today prevents a future-dated observation. The rule
must not be "reject anything after now": it must permit reporting lag (measured minimum +15 days,
maximum 1,045), permit `GUIDED` assertions on future periods, and permit calculated future facts
where explicitly allowed — while refusing a `reported` observation whose period ends after the
filing that carries it.

**D4 source lanes.** Add only the enum values later stages need. `xbrl`, `calculated`,
`company_dashboard` and `manual_annotation` already exist; transcript and market-data lanes do
not. Adding them changes the ontology definition hash and every fixture that pins it.

**E instance properties.** `graph/stages/projection/nodes.py` reads only `instance_id`,
`concept_id` and `label`, so the declared `cik`, `tickers`, `exchange` on `opendoor` and
`mic: XNAS` on `nasdaq` never reach Neo4j. Project declared instance properties only — no
invented values, no arbitrary YAML leaking into node properties.

---

## 5. Version and migration discipline

Every version bump is justified or not taken. Candidates:

| Version | Changes? | Because |
| --- | --- | --- |
| Ontology `definition_hash` | **yes** | new `AssertionType` member and new source lanes |
| Extraction catalog / layout version | **yes if** any row gains a field | readers are `extra="forbid"` with every field required |
| Observation identity version | **yes** | empty-digest refusal plus XBRL structural position |
| Graph projection version | **yes** | new evidence nodes, instance properties |
| Graph schema / load version | **yes** | new labels, constraints, allowlist entries |
| Run manifest version | only if its shape changes | |

For each: record old behaviour, new behaviour, whether old catalogs still read, whether a rebuild
is required, and that mixed incompatible artifacts **fail loudly** rather than load quietly.

Part A alone changes `observations.jsonl` content but no schema. Parts B–E change schema. The
commit split keeps these legible.

---

## 6. Verification

Offline suite (`pytest -m "not live and not neo4j"`, currently **2,530** passing) plus the
Neo4j-marked tests with the existing wipe guard. Then the full chain: extraction self-verification,
Stage-13 catalog build, graph projection, deterministic re-projection comparison, Neo4j load,
`graph verify`, id recomputation, dangling-edge and duplicate-id checks.

A before/after audit records: affected ids before and after, values, periods, passages,
structural positions, which ids disappeared, whether any unrelated id moved, and updated
observation / issue / rejection / node / edge counts.

**Unit tests are not sufficient evidence.** A corpus-level check must show the six defects are
gone, and a test that passes only because the affected rows are absent is not a passing test.

---

## 7. Work split

| Agent | Owns | Must not touch |
| --- | --- | --- |
| A | multi-header binding, flattened-narrative refusal, fixtures, defect regression | contracts, identity, ontology |
| B | evidence reference model, validation, catalog rows, graph inputs/citations | header logic, ontology enums |
| C | identifier rules, XBRL structural position, collision and determinism tests | evidence, ontology |
| D | `AssertionType.GUIDED`, source lanes, typed guidance range, future-period rule | graph projection, header logic |
| E | evidence and instance-property projection, allowlists, schema/loader | extraction internals |

An adversarial reviewer runs after integration.

---

## 8. Founder gates

1. **Part A changes filed facts.** Six observations disappear and the graph's answer for
   `market_count` at three quarter-ends changes. This is a correction, but it is a change of
   record and should be seen before it is committed.
2. **Ontology `definition_hash` changes**, invalidating every fixture that pins it.
3. **If the flattened-narrative refusal (§1.3) removes correct observations elsewhere**, the
   count and the list must be reviewed rather than absorbed.
