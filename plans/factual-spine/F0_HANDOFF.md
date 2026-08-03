# F0 — handoff to the next session

Written mid-implementation when the previous session ran out of context. **Nothing below is
committed except the two planning commits.** All implementation work is uncommitted in the
working tree of one branch.

Paste the prompt in §8 into a fresh Claude Code session.

---

## 1. Where you are

| | |
| --- | --- |
| Repo | `C:\Users\thele\Projects\Prototyping-Financial-Knowlege-Graph` (WSL: `/mnt/c/Users/thele/Projects/Prototyping-Financial-Knowlege-Graph`) |
| Branch | **`impl/f0-factual-spine-contracts`** |
| Base | `df50be9` (on `main`) |
| Commits made | `df50be9` factual-spine plan · `66a1a00` F0 plan. **No implementation commit yet.** |
| Working tree | ~45 files modified/untracked, **all uncommitted** |
| Other worktrees | `/home/thele/fkg-env-neo4j`, `/mnt/c/.../FKG-graph-plan`, `/mnt/c/.../FKG-llm-agent-plan` — **do not touch** |
| Pre-existing stash | `stash@{0} "stage-12 partial"` — **unrelated, do not drop or apply** |

Authoritative plans: `plans/factual-spine/V1_OPENDOOR_FACTUAL_SPINE.md`,
`plans/factual-spine/SOURCE_AND_CONTRACT_HANDOFF.md`,
`plans/factual-spine/F0_CONTRACT_EXTENSION.md` (the F0 spec — read this first).

---

## 2. What is done and verified

### Part A — the six defects are FIXED and audited ✅

Root cause was **two** bugs, not one:

- **Table lane** (defects 1–3): `_assign_groups` in `extraction/stages/tables/header_analysis.py`
  tried even division before positional binding. On `open-20220331.htm#p122` the headings sit at
  columns 6 and 8 over date cells 6,8,10,12 — even division split 2+2 when the true split is 1+3,
  so the Dec-31-2021 market count of 44 became instant `2021-03-31`.
  **Fix:** when even division and positional binding disagree, decide by *date-label repetition*.
  Measured over 1,935 table passages: they disagree on 92 tables — 87 with repeating labels
  (`2021 2020 2021 2020`, genuinely parallel groups → even division) and 5 with all-distinct
  labels (`2022 2021 2020 2019`, sequential → positional). The 5 are the *Expansion into New
  Markets* table in each Q1 10-Q. No filename/metric/value special-casing.
- **Narrative lane** (defects 4–6): `q42023formxex992sharehol.htm#p20` is typed `narrative` but
  its text is a flattened seven-period grid; the model bound `December 31, 2022` values to
  `2023-12-31`. **Fix:** a structural refusal — `PERIOD_NOT_GROUNDED_IN_PASSAGE`, raised when the
  span a period is grounded on falls inside a run of ≥3 period phrases separated only by
  whitespace/punctuation. Recorded answers were **not** edited.

**Audited result (I ran this myself, it matched a prediction I registered beforehand):**

```
observations 2707 -> 2704 ;  ids stable 2699 ;  8 disappeared ;  5 appeared
all six defects gone; all six correct values survive
```

The 8 removed are the 6 named defects **plus 2 more** the general fix also caught
(`market_count` 2020-03-31 at `#p144`, 2024-03-31 at `#p101`). The 5 that appeared are the same
table readings re-dated to `2020/2021/2022/2023/2024-12-31`, digest suffix unchanged.

Audit harness: `/tmp/claude-1000/.../scratchpad/f0_audit.py` with the before-snapshot in
`f0_before/`. **Re-copy these somewhere durable — `/tmp` may be cleared.**

### Part B — evidence contract ✅
Discriminated by `evidence_kind`, driven from `claims.yaml` rather than hard-coded. New kinds
`market_data` and `calculated`; new `optional_fields` on `EvidenceTypeDefinition` (needed to
express "an xbrl_fact may NOT carry a passage_id"). New codes `EVIDENCE_KIND_UNKNOWN`,
`EVIDENCE_FIELD_MISSING`, `EVIDENCE_KIND_MIXED`, `EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND`.
`IDENTITY_FIELDS[EVIDENCE]` moved from `(claim_id, passage_id)` to `(claim_id, evidence_index)`.
72 new tests. `evidence.jsonl` byte-identical for existing rows.

**Key measured correction to the plan:** enum additions are **hash-neutral** (the snapshot holds
only resolved definitions). The definition-hash move is caused by `optional_fields` + the new
evidence-type declarations, not by `AssertionType`/`SourceLane`.

### Part C — observation identity ✅
`digest()` over all-blank parts now raises `EmptyIdentityError` instead of returning the constant
`e3b0c44298fc`. New `xbrl_structural_position(accession, fy, fp, concept, context, unit,
dimensions...)`. `OBSERVATION_IDENTITY_VERSION = "1.1.0"`, deliberately **not** a digest input.
All existing ids reproduce unchanged (2,704/0 mismatches via
`graph.core.derivation.mismatched_observation_ids`). 27 tests.

### Part D — ontology ⚠️ landed but unverified by me
`AssertionType.GUIDED` is present in `ontology/core/values.py`, and 9 new ontology example
fixtures exist (`examples/valid/13-16*`, `examples/invalid/13-17*`) covering guidance ranges,
qualitative guidance, reporting lag at the measured maximum, a guided future period, and a
reported observation of a future period. Typed guidance range, future-period invariant and new
source lanes are believed done. **The agent was stopped before reporting — verify all of it
yourself, and confirm the ontology still loads and every example behaves as declared.**

### Part E — graph projection ❌ NOT STARTED — this is your first task
Confirmed by inspection after the agent was stopped: `graph/stages/projection/nodes.py` contains
**no** instance-property projection and **no** evidence-source node (0 markers). Nothing of Part E
landed. It still owns, in full:
- non-passage evidence projection — and note Agent B's warning that
  `graph/stages/projection/edges.py:410,525-540` still dereferences `row.passage_id`
  unconditionally and will `AttributeError` on the new row shapes;
- ontology instance-property projection (`cik`, `tickers`, `exchange`, `mic` are declared and
  silently dropped, ~line 613);
- `loader.CONCRETE_LABELS` and `schema.CONSTRAINED_RELATIONSHIP_TYPES` allowlists;
- the stale pinned corpus counts in `tests/graph/` listed in §5.

---

## 3. The one thing that will confuse you: the ontology hash cascade

The ontology `definition_hash` has moved **twice**:

```
e8d4af709be2…  original (pinned in the committed run manifest, benchmark reports, vector caches)
c4c2b4dec049…  after Part B
337e0e595 34d…  after Part D   <-- current
```

This makes ~130 tests fail, and **every one of them is a correct refusal, not a bug**:

- `OntologyMismatchError` in `tests/graph/*` — the finalized run records the old hash. The graph
  layer is refusing to project a run built under a different vocabulary. **Correct behaviour.**
- `StaleVectorCacheError` in `test_hybrid_scoping.py` — `benchmarks/extraction/v1/vectors/
  concepts.json` is keyed on the definition hash. **Correct behaviour.**
- `test_*_report.py` — committed benchmark reports embed the old hash.

**All of it is fixed by regenerating derived artifacts (§4). Do not "fix" these tests.**

---

## 4. The regeneration cascade — run this AFTER D and E are verified

Order matters. Steps 2–6 are offline; step 1 needs a GPU server.

```bash
# 1. Vector cache — REQUIRES the embedding server (currently DOWN)
~/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen3-embedding-0.6b/Qwen3-Embedding-0.6B-f16.gguf \
  --embedding --pooling last --embd-normalize 2 \
  -ngl 99 -c 2048 -ub 2048 --host 127.0.0.1 --port 8081 &
python -m benchmarks.extraction.v1 hybrid-build     # rebuilds vectors + hybrid report
# then stop the server

# 2. Re-run extraction under the final ontology (offline: replay-only, provider_calls_permitted 0)
python -m extraction run
python -m extraction rebuild        # must report all seven catalogs byte-identical

# 3. Regenerate the offline benchmark reports
python -m benchmarks.extraction.v1 report
python -m benchmarks.extraction.v1 scope-report
python -m benchmarks.extraction.v1 narrative-report
python -m benchmarks.extraction.v1 event-report
python -m benchmarks.extraction.v1 scoping-decision

# 4. Graph
python -m graph project
python -m graph load <export> --replace     # Neo4j container fkg-neo4j is UP
python -m graph verify

# 5. Re-copy the graph test fixture slice (tests/fixtures/graph/) — its manifest pins the old hash

# 6. Suites
python -m pytest -m "not live and not neo4j" -q
FKG_GRAPH_TESTS_MAY_WIPE=1 python -m pytest -m neo4j -q
```

**NEVER run `narrative-build` or `event-build`** — those call the generation server and would be
a provider run, which is F3, explicitly out of F0 scope.

Environment: conda `base` (NOT `fkg-llm`, despite the name). Neo4j runs in Docker Desktop; use
`sg docker -c "..."` if the shell lacks the group.

---

## 5. Known open items

1. **Agent A's benchmark trade-off needs a founder decision.** Regenerating
   `narrative_lane_v1.*` moved 2 gold observations from *matched-but-wrong* to *missed*:
   metric-identity recall 0.550 → 0.450, **value accuracy 0.818 → 1.000**, `value_wrong` 1 → 0.
   The gold case `recon-shareholder-letter-table-as-prose` annotates `#p20` — the passage the lane
   now correctly refuses — so that case is **unmeetable by construction**. Recommend re-scoping the
   case or reclassifying it as a must-refuse passage. Agent A changed no benchmark case.
2. **F3 forward cost:** 111 of the 3,072 selected narrative passages carry a flattened grid and
   will now be refused (3.6% ceiling). One recorded answer got such a passage *right* by luck and
   would now be refused — intended, but worth watching.
3. **`RUN_LAYOUT_VERSION`** was not bumped (no existing row gained a field). Bump to `1.1.0` when
   the first non-passage-emitting lane lands (F1).
4. **`OBSERVATION_IDENTITY_VERSION`** is declared but not recorded in the run manifest; F1 should
   add it (`extraction/core/manifest.py`).
5. Agent B extended `graph/core/derivation._evidence_reference` and `observation_identity`
   (slightly outside its brief) — review those two.

---

## 6. Process warnings from this session

- All five agents shared **one working tree**. Agent B ran `git stash push -u` and nearly lost
  Agent C's work; Agent C's edits were silently reverted once and had to be re-applied.
  **Use `isolation: "worktree"` for parallel agents, or run them sequentially.**
- Verify `xbrl_structural_position` is still in `extraction/core/identifiers.py`,
  `_labels_repeat` in `header_analysis.py`, and `optional_fields` in `ontology/core/models.py`
  before trusting anything.

---

## 7. Commit plan (nothing is committed yet)

The F0 spec requires **current-data correction and contract expansion to land separately**:

```
fix(extraction): correct multi-header period binding and refuse flattened grids   [Part A]
feat(evidence): support typed non-passage evidence                                [Part B]
fix(identity): define deterministic external-fact identity                        [Part C]
feat(ontology): add guided assertions and future-period checks                    [Part D]
feat(graph): project evidence and ontology instance properties                    [Part E]
docs(factual-spine): record F0 implementation results                             [report]
```

Do not squash A into B–E. Then run an **adversarial review subagent** against the checklist in the
original F0 brief (silent observation changes, weakened passage evidence, XBRL still needing a
passage, fake passages for market/transcript data, id collisions, unnecessary id changes, rebuild
incompatibility, `extra="forbid"` failures, stale allowlists, unaccounted hash changes, missing
migration notes, a known-bad observation surviving, tests passing only because rows are absent,
and premature implementation of later stages).

---

## 8. Prompt for the next session

> Continue implementing **F0 only** of the Opendoor factual-spine plan in
> `C:\Users\thele\Projects\Prototyping-Financial-Knowlege-Graph`.
>
> Read `plans/factual-spine/F0_HANDOFF.md` first — it is a complete state dump from the previous
> session, which ran out of context mid-implementation. Then read
> `plans/factual-spine/F0_CONTRACT_EXTENSION.md` (the F0 spec) and
> `plans/factual-spine/V1_OPENDOOR_FACTUAL_SPINE.md`.
>
> You are on branch `impl/f0-factual-spine-contracts`, base `df50be9`. Parts A, B and C are done
> and verified. Part D landed but is unverified. Part E may be incomplete. **Nothing is committed.**
>
> Do this, in order:
> 1. Verify the working tree is intact (handoff §6) and confirm what Parts D and E actually did.
> 2. Finish Part E if incomplete: non-passage evidence projection, ontology instance-property
>    projection, loader/schema allowlists, and the stale pinned counts in `tests/graph/`.
> 3. Run the regeneration cascade in handoff §4 **in that order**. Step 1 needs the embedding
>    server started; steps 2–6 are offline. Never run `narrative-build` or `event-build`.
> 4. Get `pytest -m "not live and not neo4j"` green, then the `neo4j`-marked tests with
>    `FKG_GRAPH_TESTS_MAY_WIPE=1`.
> 5. Re-run the Part A audit and confirm the six defects are still gone and ids are still stable.
> 6. Commit as the six narrow commits in handoff §7 — keep the Part A data correction in its own
>    commit, separate from the contract changes.
> 7. Run an independent adversarial-review subagent against the checklist in §7, verify its
>    findings yourself, and fix what is real.
> 8. Write the F0 implementation report into `plans/factual-spine/` and surface the founder
>    decision in handoff §5.1.
>
> Do not push. Do not start F1 or any later stage. Do not touch the other worktrees or the
> pre-existing `stash@{0}`. Use worktree isolation if you run parallel subagents.
