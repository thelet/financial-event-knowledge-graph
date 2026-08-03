# Workstream boundary — story agent vs factual spine

Two sessions are working on this repository at the same time. This file records who owns what,
which surfaces are shared, and what has to be coordinated. It exists so neither session
discovers the other's edit as a merge conflict.

**Written 2026-08-03** from worktree `FKG-llm-agent-plan`, branch `plan/llm-graph-agent`,
based on `df50be9`. Companion to [V1_STORY_AGENT.md](V1_STORY_AGENT.md).

---

## 1. Checkouts

| Path | Branch | Session |
| --- | --- | --- |
| `C:\Users\thele\Projects\Prototyping-Financial-Knowlege-Graph` | `main` | factual spine (active) |
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
| Neo4j | Labels `Metric, Observation, Event, Passage, Document, Entity` and relationship types `HAS_OBSERVATION, EVIDENCED_BY, PART_OF, PARTICIPATES_IN, OBSERVATION_OF_SUBJECT, RECONCILES_TO, DISTINCT_FROM, HOLDS_POSITION_AT, BORROWS_UNDER`; the `passage_text` fulltext index; `:Issue`/`FOUND_IN`/`CONCERNS_METRIC` from two entry points only |
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
| A new member of `AssertionType` (e.g. a guided assertion) | Unblocks detectors D10/D11; §6.8's `REQUIRED_FACTS` turns them on | At design time — the current rule is unsatisfiable (V1_STORY_AGENT §18) |
| An observation value model that can hold a range | Same — D10/D11 also need it | At design time |
| A change to `period_key` derivation | Breaks candidate ids and package digests; both are versioned, so it mints new ones rather than corrupting old ones | Before it lands |
| A new metric definition | **No change needed** — arrives as more rows under the same contract | Never |
| A new source lane, document type, or more events | **No change needed** | Never |

The last two rows are the point of the design. Most factual-spine progress requires nothing
from this side.

---

## 6. Things this session found in the shared tree

Recorded here as well as in V1_STORY_AGENT §18, because they are the factual-spine session's to
act on.

1. **The loaded graph is stale.** `graph-v1-886059d862ce` records
   `inputs.run_complete_sha256 = 7c921bc5…`; the directory now hashes `75f47628…`. 2,707
   observations are loaded; 2,704 exist. Rebuilding the graph resolves it.
2. **`extraction_run_id` is not an identity.** `extract-v1-lexical-2422c4252c07` names two
   different sets of bytes, two days apart, at two different code commits. Only
   `run.complete`'s digest distinguishes them.
3. **`formulas.yaml`'s `adjusted_gross_profit` v2 expression does not reproduce the corpus** —
   `adjusted_gross_margin < gaap_gross_margin` in 16 of 26 quarters.
4. **The current run holds no semantic fact conflicts.** 36 multi-valued slots, 10 above 1%
   spread, all thousands-vs-millions rounding. `V1_OPENDOOR_FACTUAL_SPINE.md` §8.1 describes
   the 2026-08-02 snapshot and is now stale in that one paragraph.
5. **Neo4j Community 5.26.28 has vector index support** (`db.index.vector.queryNodes`, mode
   `READ`) and **no role-based access control** (`SHOW ROLES` → `UnsupportedAdministrationCommand`).
   Both measured live on 2026-08-03.

---

## 7. Merge intent

`plan/llm-graph-agent` holds planning documents only — no code, no test, no config. When the
factual-spine session is at a quiet point, the two files under `plans/llm-agent/` can be
cherry-picked onto `main` as a single documentation commit. Nothing in this branch touches a
file `main` has changed, so the cherry-pick is expected to be clean; it is still the other
session's call when to take it.
