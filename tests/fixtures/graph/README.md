# Graph-layer fixture — a real slice of one finalized extraction run

**These are real values from real SEC filings. Never edit a row by hand.** Every line here was
copied byte-for-byte out of a finalized run; the numbers, the quoted sentences and the passage
text are what Opendoor filed. Hand-editing a value would make the fixture assert something no
filing says, and nothing downstream could tell. To change what the fixture covers, widen the
selection and re-copy from the run — do not retype.

## Provenance *(verified 2026-08-02)*

| Field | Value |
| --- | --- |
| `run_id` | `extract-v1-lexical-2422c4252c07` |
| `code_commit` (from `manifest.json`) | `4d3ae1e8e2b90356932a33c6b611e444d1396faa` |
| `config_hash` | `e1bb4eaf254fd61c247e35b847d0d30d0faf0948fa2e7053b8b96e1c4a8b2c2c` |
| `ontology_id` | `real_estate_marketplace_v1` |
| run `created_at` | `2026-08-02T16:02:44+00:00` |
| extraction source | `data/extraction_runs/extract-v1-lexical-2422c4252c07/` (gitignored) |
| catalog source | `data/normalization_catalog/{passages,documents}.jsonl` (gitignored) |

The layout mirrors the run directory so the same reader can be pointed at either:

```
tests/fixtures/graph/
  extraction_run/{claims,observations,evidence,events,relationships,issues,rejected_claims}.jsonl
  extraction_run/manifest.json
  normalization_catalog/{passages,documents}.jsonl
```

`manifest.json` is the run's own manifest, copied unmodified — so its `counts` and
`catalog_digests` describe the **full run**, not this fixture. That is deliberate: a fixture
manifest edited to match the slice would no longer be the run's manifest. Its digests still
verify against the full run, never against these files.

`lane_outputs.jsonl` and `report.md` are not included; the graph projection reads neither.

## Selection criteria

The whole fixture is derived from **six passages**, chosen for what they exercise. Everything
else follows mechanically, with no per-row cherry-picking:

| # | passage_id | why it is here |
| --- | --- | --- |
| 1 | `norm:0001801169:0001801169-23-000137:q32023formxex991earningsre.htm#p29` | the conflict-guard table: kept observations *and* refused readings on one passage |
| 2 | `norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#p7` | anchors the required warned observation `claim:metric-observation:78dcfb719539`; also a `NO_STORED_ANSWER` issue |
| 3 | `norm:0001801169:0001801169-21-000120:open-20210930.htm#p150` | six warned `market_count` observations read out of a table |
| 4 | `norm:0001801169:0001801169-22-000108:open-20220930.htm#p117` | the occurred_on-only event, the `BORROWS_UNDER` relationship, and the unresolved participant |
| 5 | `norm:0001801169:0001140361-25-034609:ef20055426_ex99-1.htm#p2` | the announced_on-only executive-change events |
| 6 | `norm:0001801169:0001801169-21-000021:open-20210331.htm#p157` | anchors `issue:012bb3cca946`, an `AMBIGUOUS_ALIAS` refusal |

Given that set:

- **`claims.jsonl`** — every claim in the run whose `passage_id` is one of the six. 39 rows.
- **`observations.jsonl`, `evidence.jsonl`, `events.jsonl`, `relationships.jsonl`** — every row
  whose `claim_id` is one of those 39. No filtering by kind.
- **`issues.jsonl`** — every issue in the run whose `passage_id` is one of the six. 45 rows.
  Not just the required codes: taking a passage's *whole* issue set is what makes the
  refused-vs-kept test meaningful, since a hand-picked subset could hide a refusal that the
  projection wrongly promoted.
- **`rejected_claims.jsonl`** — included, 9 rows: every rejected claim on the six passages.
  Each mirrors an issue row (`rej:<hex>` ↔ `issue:<hex>`, with `rejected_claim: true`), and the
  mirroring is exact for all 9 *(verified)*.
- **`passages.jsonl`** — the six passage rows, in the table order above.
- **`documents.jsonl`** — the six documents those passages belong to (one each; no two selected
  passages share a document).

Every line is **byte-identical to its source line** — the files were assembled by copying raw
lines, not by re-serializing. That means the run files keep their compact separators and the
catalog files keep their spaced separators, exactly as each writer emits them. Verified by set
containment of every fixture line in the corresponding source file *(verified 2026-08-02)*.

## Row counts and sizes

| file | rows | bytes |
| --- | ---: | ---: |
| `extraction_run/claims.jsonl` | 39 | 41,876 |
| `extraction_run/observations.jsonl` | 30 | 24,207 |
| `extraction_run/evidence.jsonl` | 39 | 23,480 |
| `extraction_run/events.jsonl` | 5 | 4,881 |
| `extraction_run/relationships.jsonl` | 4 | 2,257 |
| `extraction_run/issues.jsonl` | 45 | 29,683 |
| `extraction_run/rejected_claims.jsonl` | 9 | 12,267 |
| `extraction_run/manifest.json` | — | 4,655 |
| `normalization_catalog/passages.jsonl` | 6 | 22,168 |
| `normalization_catalog/documents.jsonl` | 6 | 8,995 |
| **total** (data files, excluding this README) | | **174,469** |

Claims break down as 30 `metric_observation`, 5 `event`, 4 `relationship`.

## Covered cases

| # | case | id(s) satisfying it |
| --- | --- | --- |
| 1 | plain metric observation, table lane (`source_lane=normalized_table`), no warning | `claim:metric-observation:c25154b68aff` → `obs:adjusted-ebitda:opendoor:2022-04-01_2022-12-31:normalized-table:63ecb0d9f982` (−351,000,000 USD). 20 other unwarned observations are present. |
| 2 | WARNED metric observations | required: `claim:metric-observation:78dcfb719539` → `obs:adjusted-ebitda-margin:opendoor:2022Q2:normalized-narrative:603c21481ff3`. Plus six warned `market_count` (see list below), which is more than the two asked for. |
| 3 | event with **only** `announced_on` | `evt:executive-change:undated:4c7324fecc42` (`claim:event:2de36bc317d3`, `announced_on=2025-09-10`, `occurred_on=null`). Two sibling events on the same passage behave the same way: `…:5f0724b23967`, `…:70ee824c0430`. |
| 4 | event with **only** `occurred_on` | `evt:credit-facility-established:2022-10-19:4b384d2ba0fa` (`claim:event:a5823d3b7c19`, `occurred_on=2022-10-19`, `announced_on=null`). |
| 5 | relationship | `rel:borrows-under:opendoor-unnamed-subsidiary:asset-backed-senior-revolving-credit-facility:a129a14c7956` (`claim:relationship:8634c2a7a667`, `BORROWS_UNDER`). Three `HOLDS_POSITION_AT` rows come along from passage 5. |
| 6 | unresolved participant | `opendoor_unnamed_subsidiary` — a participant of case 4's event (`role=borrower`) and the `source_id` of case 5's relationship. The run's own note about it is `issue:b6e82d165999` (`PARTICIPANT_NOT_NAMED`, passage 4). |
| 7 | issues from refused readings | `AMBIGUOUS_COLUMN_ALIGNMENT`: `issue:33b3e6868f8e`, `issue:2a0dc847c5a3` (both required) plus `issue:d286c2c44506`, `issue:da2d090061f9` — all four on passage 1. `AMBIGUOUS_ALIAS`: `issue:012bb3cca946` (passage 6) plus 7 more. `NO_STORED_ANSWER`: `issue:9aa12c4e04e0` (passage 2). `UNRESOLVED_METRIC`: `issue:04666be52e11` (passage 1) plus 18 more. |
| 8 | evidence for every included claim | 39 evidence rows for 39 claims, exactly one each *(verified)*. |
| 9 | every cited passage | all 6 passages referenced by any claim, observation, event, relationship, evidence or issue row are present. |
| 10 | every document of those passages | all 6 present. |
| 11 | extraction manifest | `extraction_run/manifest.json`, byte-identical to the run's *(verified)*. |

Issue codes present (45 rows): `UNRESOLVED_METRIC` 19, `AMBIGUOUS_ALIAS` 8,
`AMBIGUOUS_COLUMN_ALIGNMENT` 4, `PROPERTY_VALUE_NOT_IN_PASSAGE` 4,
`DEFERRED_REQUIRED_SOURCE_LANE` 3, `DERIVED_COMPARISON` 2, `VALUE_CONTRADICTS_QUOTED_TEXT` 2,
`NO_STORED_ANSWER` 1, `ANNOUNCEMENT_EQUALS_OCCURRENCE` 1, `PARTICIPANT_NOT_NAMED` 1.

## Warned observations — how they were derived

The run records **no per-claim warning**: `observations.jsonl` carries no warning field, and
`issues.jsonl` holds refusals and diagnostics, not validator warnings. Warnings are
*re-derivable*, and that is how this list was produced:

1. read an `observations.jsonl` row and its single `evidence.jsonl` row (joined on `claim_id`);
2. rebuild `ontology.core.models.MetricObservation` from them —
   `population_definition_raw` → `Population(definition_raw=…)`, the evidence row →
   one `EvidenceReference` (`evidence_kind`, `passage_id`, `document_id`, `table_id`,
   `block_ids`, `quoted_text`, `source_url`);
3. call `ontology.load_ontology().validate_observation(obs)`;
4. a non-empty `result.warnings` means warned.

Replayed over the **full run** this yields **186 warnings across 2,707 observations**, which
matches the previously verified count *(verified 2026-08-02)*. Every warning in both the run
and this fixture has code `unpreferred_source_lane`. No observation in the fixture produces a
validation *error*.

**9 of the 30 observations here are warned:**

| claim_id | observation_id | metric | source_lane |
| --- | --- | --- | --- |
| `claim:metric-observation:78dcfb719539` | `obs:adjusted-ebitda-margin:opendoor:2022Q2:normalized-narrative:603c21481ff3` | `adjusted_ebitda_margin` | `normalized_narrative` |
| `claim:metric-observation:df70552c0f23` | `obs:adjusted-ebitda:opendoor:2022Q2:normalized-narrative:603c21481ff3` | `adjusted_ebitda` | `normalized_narrative` |
| `claim:metric-observation:ec98a02e1677` | `obs:housing-inventory-homes:opendoor:2022-06-30:normalized-narrative:603c21481ff3` | `housing_inventory_homes` | `normalized_narrative` |
| `claim:metric-observation:5c015f5c2740` | `obs:market-count:opendoor:2018-12-31:normalized-table:03bbb76a46eb` | `market_count` | `normalized_table` |
| `claim:metric-observation:1695ebeafddb` | `obs:market-count:opendoor:2019-12-31:normalized-table:c2c626dbd5cd` | `market_count` | `normalized_table` |
| `claim:metric-observation:f8970bff3aed` | `obs:market-count:opendoor:2020-12-31:normalized-table:8016f552c671` | `market_count` | `normalized_table` |
| `claim:metric-observation:4137f2a36dfc` | `obs:market-count:opendoor:2021-03-31:normalized-table:08e222305d7d` | `market_count` | `normalized_table` |
| `claim:metric-observation:239937eb9239` | `obs:market-count:opendoor:2021-06-30:normalized-table:b94309ac0ede` | `market_count` | `normalized_table` |
| `claim:metric-observation:ea8c43aa5dc2` | `obs:market-count:opendoor:2021-09-30:normalized-table:15b2b4bf513f` | `market_count` | `normalized_table` |

The other 21 observations validate clean, so the fixture distinguishes warned from unwarned
rather than only demonstrating one of them.

## Refused vs kept on `…q32023formxex991earningsre.htm#p29`

That passage is a nine-quarter non-GAAP reconciliation table whose header repeats a year label
in two column positions. The tables lane read two different cells of one row into the *same*
metric-and-period identity with *different* values, and the conflict guard refused **both**
readings rather than choosing one. It also read six other cells whose column mapping was
unambiguous, and kept them. Both outcomes are in this fixture.

Refused (from the four `AMBIGUOUS_COLUMN_ALIGNMENT` issue rows, parsed out of `detail`):

| metric | period | conflicting cells |
| --- | --- | --- |
| `adjusted_ebitda` | `2022-01-01_2022-09-30` | col 10 "September 30, 2022" = −211,000,000 vs col 14 "2022" = 183,000,000 |
| `adjusted_ebitda` | `2023-01-01_2023-09-30` | col 2 "September 30, 2023" = −49,000,000 vs col 12 "2023" = −558,000,000 |
| `adjusted_ebitda_margin` | `2022-01-01_2022-09-30` | col 10 = −6.3 vs col 14 = 1.4 |
| `adjusted_ebitda_margin` | `2023-01-01_2023-09-30` | col 2 = −5.0 vs col 12 = −9.2 |

Kept on the same passage (all `source_lane=normalized_table`, all `subject_entity_id=opendoor`):
`adjusted_ebitda` and `adjusted_ebitda_margin` at `2022-04-01_2022-12-31`,
`2022-07-01_2023-03-31` and `2022-10-01_2023-06-30`.

**Verification result *(verified 2026-08-02)*: no observation in this fixture — on this passage
or any other — has the identity `(metric_id, subject, period, source_lane, passage_id)` of any
refused reading. The intersection of the four refused identities with all 30 observation
identities is empty.** Period identity was compared as `instant_date` when present, otherwise
`period_start_period_end`; subject is `opendoor` for every observation on the passage, and lane
is `normalized_table` for the refusals and for every kept table reading. A projection that
turned a refused cell into a node would break this check.

## Consistency checks that hold over these files *(verified 2026-08-02)*

- 39 claims, 39 distinct `claim_id`s, exactly one evidence row per claim.
- every `passage_id` referenced by a claim, observation, evidence, event, relationship or issue
  row is present in `passages.jsonl` (6 referenced, 6 present).
- every `document_id` of those passages — and every `document_id` on a claim or issue row — is
  present in `documents.jsonl`.
- every `claim_id` in `observations.jsonl`, `events.jsonl` and `relationships.jsonl` exists in
  `claims.jsonl`.
- the set of `payload_id`s in `claims.jsonl` equals the union of `observation_id`,
  `event_id` and `relationship_instance_id` across the payload files.
