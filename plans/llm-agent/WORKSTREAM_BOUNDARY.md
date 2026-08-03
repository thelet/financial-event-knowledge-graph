# Workstream boundary — story agent vs factual spine

Two sessions are working on this repository at the same time. This file records who owns what,
which surfaces are shared, and what has to be coordinated. It exists so neither session
discovers the other's edit as a merge conflict.

**Written 2026-08-03** from worktree `FKG-llm-agent-plan`, branch `plan/llm-graph-agent`,
**rebased onto `edc2d5a` after F0 landed**. Companion to [V1_STORY_AGENT.md](V1_STORY_AGENT.md)
§0d.

---

## 1. Checkouts

| Path | Branch | Session |
| --- | --- | --- |
| `C:\Users\thele\Projects\Prototyping-Financial-Knowlege-Graph` | `impl/f0-factual-spine-contracts` (= `main` at `edc2d5a`) | factual spine (active) |
| `C:\Users\thele\Projects\FKG-llm-agent-plan` | `plan/llm-graph-agent` | story agent (this plan) |
| `C:\Users\thele\Projects\FKG-graph-plan` | `plan/graph-v1` | dormant |
| `~/fkg-env-neo4j` | `env/neo4j-local` | dormant |

`plan/llm-graph-agent` is **not merged into `main`** and is not pushed.

---

## 2. File ownership

| Owner | Paths |
| --- | --- |
| **Factual spine** | `acquisition/` `normalization/` `extraction/` `ontology/` `graph/` `benchmarks/` `docs/` `plans/factual-spine/` `plans/extraction/` `plans/graph/` `config/*.yaml` except `story.yaml` `manifests/` `normalization_manifests/` `tests/` except `tests/story/` |
| **Story agent** | `story/` `config/story.yaml` `tests/story/` `plans/llm-agent/` |
| **Shared, read-only for the story agent** | `data/` `pyproject.toml` `.env` `compose.yaml` |

**There is no file both workstreams edit.** That includes the structural tests: the assertion
that `graph/` never imports `story` lives in `tests/story/test_story_package_structure.py` and
walks `graph/` from there, rather than extending
`tests/graph/test_graph_package_structure.py::test_upstream_packages_never_import_graph`.

`pyproject.toml` is shared and **the story agent adds no dependency to it** (V1_STORY_AGENT §4,
§5). If that ever changes it is a coordination event, not an edit.

---

## 3. Shared runtime resources

Both sessions use one Neo4j container (`fkg-neo4j`, compose project `fkg`, ports
`127.0.0.1:7687` / `:7474`) and one llama.cpp server pair (`:8080` generation, `:8081`
embeddings).

**Rules while both sessions are active:**

- The story agent is **read-only** against Neo4j — no `CREATE`, `MERGE`, `SET`, `DELETE`, index
  or constraint statement, ever (V1_STORY_AGENT §16). It never runs `graph load --replace`.
- Only the factual-spine session runs `python -m graph load`. The story agent reads whatever is
  loaded and refuses to proceed when it is stale (§7 of the plan).
- Neither session writes into the other's run directories under `data/`.
- The llama.cpp server is single-slot. Two concurrent `live`-marked runs will queue, not fail;
  neither session should assume exclusive use in a timing-sensitive test.

---

## 4. The interface the story agent consumes

Only these, and nothing else:

| Surface | What is depended on |
| --- | --- |
| `data/graph_runs/<id>/` | `manifest.json` (identity, `inputs.run_complete_sha256`, `counts`, `artifacts`), `nodes.jsonl`, `edges.jsonl` |
| Neo4j | All eight base labels `Metric, Observation, Event, Passage, Document, Entity, Issue, EvidenceSource` and all twelve relationship types `HAS_OBSERVATION, EVIDENCED_BY, PART_OF, PARTICIPATES_IN, OBSERVATION_OF_SUBJECT, RECONCILES_TO, DISTINCT_FROM, HOLDS_POSITION_AT, BORROWS_UNDER, PLACEHOLDER_FOR, FOUND_IN, CONCERNS_METRIC`; the `passage_text` fulltext index; `:Issue` from two entry points only |
| `ontology.core.` and `ontology` | metric definitions, aliases, `mutually_distinct_groups`, `distinct_from`, formula windows, percentage bounds, ambiguity blocks |
| `extraction.core.` | `PeriodRef.key` for period-key derivation, `digest` and `normalize_alias` for identifiers |
| `data/extraction_runs/<id>/run.complete` | its sha256, for the staleness gate only |

**It does not import** `extraction.stages`, `extraction.providers`, `extraction.contracts`,
`normalization.*`, `acquisition.*`, or anything under `graph/stages/`. `graph.core.models` and
`graph.contracts` are candidates for the allowed list at L1 and are marked *(unverified)* until
the first structural test is written.

---

## 5. Contract changes that need coordination

None is a file conflict; all three are contract changes.

| Change on the factual-spine side | Effect on the story agent | When it needs saying |
| --- | --- | --- |
| A new node label or relationship type | Extend §9's allowlist; a tool touching it is new work | Before it lands in a graph run |
| ~~A new member of `AssertionType`~~ | **LANDED at F0** — `AssertionType.GUIDED`, enforced by `forbidden_assertion_types`. D10/D11 now wait only on a lane | done |
| ~~An observation value model that can hold a range~~ | **LANDED at F0, differently** — the range is typed on `guidance_issuance.properties`, not on the observation. This plan asked for the wrong shape | done |
| A lane that emits `guidance_issuance` events | Turns on D10/D11 via §6.8's `REQUIRED_FACTS`; no story-side change needed | When the lane is scheduled |
| A lane that emits `xbrl_fact`, `market_data` or `calculated` evidence | Turns on §13.7.2 Rule C, which returns `Unavailable` until then | When the lane is scheduled |
| Populating `reported_at` on observations | Makes `check_future_period` actually fire | Whenever — the story agent does not depend on it |
| A change to `period_key` derivation | Breaks candidate ids and package digests; both are versioned, so it mints new ones rather than corrupting old ones | Before it lands |
| A new metric definition | **No change needed** — arrives as more rows under the same contract | Never |
| A new source lane, document type, or more events | **No change needed** | Never |

The last two rows are the point of the design. Most factual-spine progress requires nothing
from this side.

---

## 6. Things this session found in the shared tree

Recorded here as well as in V1_STORY_AGENT §18, because they are the factual-spine session's to
act on. **Items 1 and 2 were resolved by F0 between the first writing and the rebase.**

1. ~~The loaded graph is stale.~~ **RESOLVED.** `graph-v1-0483dc6b4b10` records
   `run_complete_sha256 = 1cc8f7b0…` and the file hashes `1cc8f7b0…`; Neo4j holds 2,704
   observations under that id. Re-verified 2026-08-03 after the rebase.
2. **`extraction_run_id` is still not an identity.** `extract-v1-lexical-2422c4252c07` named two
   different sets of bytes, two days apart, at two code commits. F0 minted a *new* id
   (`…-833f7bcfbce9`) because its inputs changed, which is the scheme working — but nothing
   prevents an in-place regeneration from recurring. `run.complete`'s digest is the only field
   that distinguishes them, and the graph manifest already records it.
3. **`formulas.yaml`'s `adjusted_gross_profit` v2 expression does not reproduce the corpus** —
   `adjusted_gross_margin < gaap_gross_margin` in 16 of 26 quarters. Still open after F0.
4. **The current run holds no semantic fact conflicts.** Re-verified against
   `…-833f7bcfbce9`: 537 slots, 36 multi-valued, 10 above 1% spread, **all** thousands-vs-millions
   rounding. `V1_OPENDOOR_FACTUAL_SPINE.md` §8.1 describes an older snapshot and is stale in
   that one paragraph.
5. **Neo4j Community 5.26.28 has vector index support** (`db.index.vector.queryNodes`, mode
   `READ`) and **no role-based access control** (`SHOW ROLES` → `UnsupportedAdministrationCommand`).
   Both measured live on 2026-08-03.
6. **Three doc-comments in the graph layer went stale at F0**: `graph/stages/load/loader.py:76-77`
   still says `Warned 186, Rejected 46` and `FOUND_IN 17,127 · EVIDENCED_BY 2,713 ·
   HAS_OBSERVATION 2,707`; `graph/stages/load/schema.py:126-127` still cites the retired
   `graph-v1-886059d862ce`. Cosmetic, but they are the numbers a reader would trust.
7. **`check_future_period` abstains on the whole corpus.** `reported_at` is populated by no lane,
   so the guard F0 built never fires on real data. Worth a note in the F0 report; the story
   agent cannot rely on it.

---

## 7. Merge intent

`plan/llm-graph-agent` holds planning documents only — no code, no test, no config. When the
factual-spine session is at a quiet point, the two files under `plans/llm-agent/` can be
cherry-picked onto `main` as a single documentation commit. Nothing in this branch touches a
file `main` has changed, so the cherry-pick is expected to be clean; it is still the other
session's call when to take it.
