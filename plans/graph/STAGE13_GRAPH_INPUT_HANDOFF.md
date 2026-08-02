# Stage 13 → graph: the input handoff, answered

Companion to [V1_GRAPH_PROTOTYPE.md](V1_GRAPH_PROTOTYPE.md). Written before Stage 13 existed, as
a checklist of eleven open questions; **closed at G0 on 2026-08-02** against the finalized run.

| | |
| --- | --- |
| Run | `data/extraction_runs/extract-v1-lexical-2422c4252c07/` |
| Extraction commit | `b1d55f4` (the manifest records `4d3ae1e` — see §3) |
| Integrity | `sha256sum -c run.complete` → **10 / 10 OK** |
| Self-verification | all five checks passed: evidence 2,717/0 · ontology 2,717/0 errors, **186 warnings** · forbidden lanes 2,707/0 · duplicate identities 25,324/0 · conflicting duplicates 2,707/0 |

---

## 1. The eleven questions, answered

Full answers are in `V1_GRAPH_PROTOTYPE.md` §2.2. Condensed here, with the verdict on each
working assumption:

| # | Assumption | Verdict |
| --- | --- | --- |
| P1 | 4 files at `data/extraction_runs/<run_id>/` | **partly wrong** — 11 files; run id is `extract-v1-lexical-{hash12}`, clock-free, not `{UTC}Z-{hash8}` |
| P2 | events/relationships in `claims.jsonl` | **partly wrong** — in `claims.jsonl` *and* in payload files carrying `dates_equal` and `review_flag`, computed nowhere else |
| P3 | rows are `claim.model_dump()` | **wrong** — hand-built flat dicts; the full `OntologyClaim` is on disk nowhere |
| P4 | `observations.jsonl` derived | **right** — byte-identical on all shared fields; gap = 6 events + 4 relationships |
| P5 | one issues file with a code and a discriminator | **right, and better** — `severity` gives four categories and `issue_id` is supplied |
| P6 | per-claim validation state recorded | **wrong** — aggregate only; closed by re-derivation (§2) |
| P7 | a handful of metadata keys | **understated** — 38 keys, 8 shapes, keys omitted rather than nulled |
| P8 | manifest carries six provenance fields | **right, plus more**; but **no `finished_at`**, and no upstream normalization *run* id — only a content-addressed `corpus_id` |
| P9 | strategy recorded | **right** — `lexical` |
| P10 | deferrals and rejections written | **right** — 358 deferral issues; 46 rejections in two files. The two ids **do** share a hex (46/46 verified), so an id-level join exists for `refused_by: "lane"` rejections; an `assemble`-origin rejection writes no mirroring issue at all. Still: do not sum. |
| P11 | counts unknown | **measured** — 2,707 observations · 17 metrics · 1 subject · 6 events · 4 relationships · 17,127 issues. Passages/documents **cited by claims** 153 / 49; **cited by claims or issues** — i.e. the real `:Passage` / `:Document` node counts — **8,776 / 185** |

## 2. The one gap, and how it was closed without invention

**P6 is the only question whose answer was materially worse than assumed.** The run's 186
`unpreferred_source_lane` warnings cannot be attributed to individual claims from any artifact:
the verifier builds `CheckFinding` objects carrying `claim_id`
(`extraction/stages/verify/public.py:49-53`) and then writes only counts (`:70-80`).

Rather than invent a warning state or silently drop it, the graph layer **re-derives** it:
reconstruct each `MetricObservation` from `observations.jsonl` + `evidence.jsonl`, call the
ontology's own `validate_observation()`, and check the total against the manifest.

*Executed at G0 over all 2,707 observations:* **186 warnings, all `unpreferred_source_lane`** —
`market_count` 176, `housing_inventory_homes` 3, and seven other metrics at one each. The manifest records 186. The two
agree, so the derivation is verified rather than assumed, and G1 fails the build if they ever
diverge.

That is the difference between deriving and inventing: the rule is the ontology's, the validator
is the same one the run used, and the answer is checked against a number the run recorded
independently.

## 3. Provenance imprecision worth carrying forward

`manifest.code_commit` is `4d3ae1e`, one commit before the finalized `b1d55f4`. The data is
nonetheless `b1d55f4`'s output — that commit's message states this run's exact numbers (2,707
observations "down 8", 2,717 claims, 186 warnings, 25,324 duplicate identities) — because
`code_commit` is `git rev-parse HEAD` at run time (`extraction/core/manifest.py:44-51`) and the
run was executed from a working tree that was committed afterwards.

Not a defect, and not the graph layer's to fix. The graph manifest records **both** the
extraction manifest's `code_commit` and the repository HEAD the projection ran at, so the record
is honest about which commit produced the data and which one read it.

## 4. What the graph must not assume — carried from G0 into G1

1. **`run.complete` means written, not verified.** Read `manifest.verification[*].passed`
   (`extraction/pipeline.py:126` verifies, `:174` finalizes unconditionally).
2. **A refusal is per-reading or per-property, never per-node.** `PARTICIPANT_NOT_NAMED` kept its
   event; `PROPERTY_VALUE_NOT_IN_PASSAGE` kept its event and dropped one property. Never suppress
   a fact because an issue names its passage.
3. **`lane` ≠ `source_lane`** — routing vocabulary vs ontology vocabulary. Carry both, map
   neither.
4. **`issue_id` / `rejection_id` are ordinal, not content addresses.** Valid keys within one run;
   never use them to diff two runs. They do share a hex where a lane rejection mirrors an issue.
5. **One rejected finding is two rows in two files with two ids.** Do not sum.
6. **No `period_key` field** — recompute with `extraction/core/models.py:134-149`. Better: the
   **whole `observation_id` is recomputable** from the catalogs, since the id-bearing grid
   coordinates survive as `metric_label_row_index` / `period_header_column_index` (verified:
   2,707/2,707). Note `value_column_index` is a different column and reproduces only 582.
7. **No char offsets in evidence** — spans exist only on 23 claims (17 narrative + 6 event) via
   `extractor_metadata`, and on none of the 2,690 table claims.
8. **`named` and `entity_text` reach no catalog.** The `_unnamed_` id substring is the only
   placeholder signal that survives.
9. **10,852 of 17,127 issues are `NO_STORED_ANSWER`** — a bound of this run
   (`provider_calls_permitted: 0`, 15 recorded answers), not a finding about the corpus.
10. **`passage_id` / `document_id` / `document_type` can be empty strings, not null**
    (`jsonl_catalog.py:170-173`). Zero occurrences here; still never a valid node key.

## 5. The fixture

`tests/fixtures/graph/` holds a real slice of this run: claims of all three kinds, a warned
observation, an event with only `announced_on`, an event with only `occurred_on`, a
relationship, the unresolved participant, conflict-guard and alias issues, every cited passage
and document, and the manifest verbatim.

It exists because `data/` is gitignored — without it, no graph test could run from a clean
checkout. Its README records the selection criteria, the warned-observation derivation, and the
verification that no refused reading appears as an observation.

**These are real filed values. Never edit them by hand.**
