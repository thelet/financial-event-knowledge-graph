# Local Neo4j environment

The graph layer needs one thing outside the repository: a local Neo4j 5 Community server.
This document is how to get it, and how to prove it works. Nothing here loads a graph —
`plans/graph/V1_GRAPH_PROTOTYPE.md` §5 owns that.

| Piece | Value |
| --- | --- |
| Image | `neo4j:5.26.28-community` — the 5-series LTS, pinned to an exact patch |
| Container | `fkg-neo4j` |
| Browser | <http://localhost:7474> |
| Bolt | `bolt://localhost:7687` |
| Data | Docker named volumes — compose keys `neo4j_data`/`neo4j_logs`, realised as `fkg_neo4j_data`/`fkg_neo4j_logs` under the pinned `name: fkg`. Never in the working tree |
| Credentials | `.env` in the repository root, gitignored, copied from `.env.example`. **Not Neo4j-only since 2026-08-19** — it also carries `OPENAI_API_KEY` for the story layer's OpenAI provider |
| Driver | `neo4j>=6.2,<7` in `pyproject.toml` |

## Prerequisites

Docker Desktop for Windows with WSL 2 integration enabled for the `Ubuntu` distribution.
Docker runs on the Windows side; the `docker` CLI reaches it from WSL through that
integration. Do not install a second Docker engine inside WSL.

Nothing else. No APOC, no Bloom, no Neo4j Desktop — the plan's §5 design uses uniqueness
constraints, indexes and a full-text index, all of which Community Edition provides.

## Setup

```bash
pip install -e ".[dev]"       # all runtime dependencies, including the neo4j driver
cp .env.example .env          # then set NEO4J_PASSWORD — it is empty in the template
docker compose up -d
docker compose ps             # wait for STATUS to read (healthy)
```

`NEO4J_PASSWORD` is empty on purpose: `compose.yaml` refuses to start without it, so an
unedited copy cannot stand up a database whose password is published here. Use 8 or more
characters, letters and digits only — see `.env.example` on why `$` and quotes misbehave.
Requires Compose 2.3.4 or newer for the top-level `name:` key.

Neo4j sets the password on first start only. If you change `NEO4J_PASSWORD` afterwards the
container keeps the old one — `docker compose down -v` (which deletes the database) is what
resets it.

## Verification

Every check below was run on 2026-08-02 against this configuration. The Result column is what
was observed, not what was expected.

| # | Check | Command | Result *(verified 2026-08-02)* |
| --- | --- | --- | --- |
| 1 | Docker reachable from WSL | `docker version` | client 29.6.2 / server 29.6.2 |
| 2 | Container healthy | `docker compose ps` | `healthy` 20s after start |
| 3 | Browser answers | `curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:7474` | `HTTP 200`; body reports `"neo4j_version":"5.26.28","neo4j_edition":"community"` |
| 4 | Bolt authenticates | the Python check below | `connectivity: ok`, `RETURN 1 AS ok → 1` |
| 5 | Version and edition | the Python check below | `Neo4j Kernel 5.26.28 community` |
| 6 | Data survives a restart | write a node, `docker compose restart`, re-count | marker node present after restart; database returned to 0 nodes, 0 labels |
| 7 | Not reachable off-host | `curl http://<LAN-or-tailnet-IP>:7474` | refused on all 7 non-loopback interfaces. Before the bindings were narrowed to `127.0.0.1`, both the LAN address and a tailnet address answered `HTTP 200` |
| 8 | Empty password fails fast | `cp .env.example /tmp/fkg-check.env && docker compose --env-file /tmp/fkg-check.env config` | exits 1: `required variable NEO4J_PASSWORD is missing a value` |

> **Check 8 writes to a scratch file, and that correction matters** *(2026-08-23)*. It used to
> read `cp .env.example .env`, which is a destructive command on any machine that has already
> been set up: it overwrites the real `.env`, and `.env` now carries **two** secrets — the Neo4j
> password and the story layer's `OPENAI_API_KEY`. Neo4j sets its password on **first start
> only**, so a container started before the overwrite keeps the old one and then rejects the
> templated credentials, with no recovery short of `docker compose down -v` — which discards the
> volumes and the loaded graph with them. The check is worth keeping; it just may not be run
> against the live file.
>
> `docker compose config` also **renders `NEO4J_AUTH` in cleartext**, exactly as `docker inspect`
> does below. Do not paste its output anywhere.

The Python check — connect, query, report, close. Run it from the directory holding `.env`:

```bash
python - <<'PY'
import pathlib
from neo4j import GraphDatabase

# Strips `export `, surrounding quotes and stray whitespace, because Compose does and a
# plain split-on-`=` does not — a disagreement that surfaces only as an opaque auth failure.
env = {}
for line in pathlib.Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")

with GraphDatabase.driver(
    env["NEO4J_URI"], auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"])
) as driver:
    driver.verify_connectivity()
    database = env.get("NEO4J_DATABASE", "neo4j")
    records, _, _ = driver.execute_query("RETURN 1 AS ok", database_=database)
    print("ok:", records[0]["ok"])
    rows, _, _ = driver.execute_query(
        "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition",
        database_=database,
    )
    for row in rows:
        print(row["name"], row["versions"][0], row["edition"])
PY
```

It must print `ok: 1` and `Neo4j Kernel 5.26.28 community`.

## Two things that bit on first start

Both are fixed in `compose.yaml`; they are recorded because the failure modes are silent or
misleading, not because the file still has them.

- **`name: fkg` is pinned, and must stay pinned.** Compose otherwise derives the project name
  from the containing directory, so running from a worktree and from the repository root
  produces two different volumes with the same role. The second one comes up as a working,
  *empty* database — a wrong answer rather than an error.
- **`server.memory.pagecache.size` takes a single underscore in the environment variable.**
  Written as `NEO4J_server_memory_pagecache__size` it becomes `server.memory.pagecache_size`,
  which strict config validation rejects, and the container crash-loops. The two heap settings
  *do* need the doubled underscore (`server.memory.heap.initial_size`). The rule is
  mechanical — `_` is `.`, `__` is `_` — but the three settings do not look alike.

## Note on the test suite

`pytest -m "not live and not neo4j"` run from a **git worktree** fails six tests in
`tests/extraction/test_{event_lane,narrative_lane}_report.py` and `test_hybrid_scoping.py` with
`FileNotFoundError: data/normalization_catalog/passages.jsonl`. This is not a regression: those
tests replay from `data/`, which is gitignored and therefore exists only in the main working
tree. *(Verified 2026-08-02 by running the same three files in a clean worktree of unmodified
`main`: the identical six fail there, while the main working tree passes all 191.)*

The **marker changed after that measurement** *(2026-08-23)*. `pyproject.toml` now declares a
`neo4j` marker covering 177 tests, so a bare `-m "not live"` needs the container running and is
no longer the offline command. The offline invocation is `-m "not live and not neo4j"`. The
six-failure finding above was measured under the old marker and has not been re-run in a
worktree since; the three files still collect 191 tests.

## What is deliberately not here

- **No `./neo4j_data/` bind mount**, though `plans/graph/V1_GRAPH_PROTOTYPE.md` §5.1 names one
  and `.gitignore` still reserves it. The working tree is on `/mnt/c`, NTFS behind the WSL 9p
  layer, where Neo4j's store files meet file-locking and ownership failures. Named volumes are
  persistent, are outside the working tree, and cannot be committed by accident — which is what
  §5.1 was after. Switch back by replacing the `volumes:` entries with `./neo4j_data:/data` if
  the repository ever moves onto the Linux filesystem.

  **This no longer contradicts the plan — the handoff was taken up** *(verified 2026-08-23)*.
  `plans/graph/V1_GRAPH_PROTOTYPE.md:281` now reads *"That gitignore entry turned out to be
  moot"*, and `:648` states *"Data lives in named Docker volumes `fkg_neo4j_data` /
  `fkg_neo4j_logs`, not in `./neo4j_data/`"*. The paragraph this replaces called it "an open
  item, not a resolved one"; it is resolved, and a reader arriving from the plan is now pointed
  at the volumes rather than at a directory that does not exist.
- **No password anywhere in Git.** `.env.example` ships `NEO4J_PASSWORD` empty rather than
  plausible, so an unedited copy fails fast instead of starting a database whose password is
  public. Note the password is *not* hidden from the local machine: `docker inspect` shows
  `NEO4J_AUTH` in cleartext to anyone in the `docker` group — and so does `docker compose
  config`, which check 8 above runs, so its output is not safe to paste. `.gitignore` already
  ignores `.env` and `.env.*` while un-ignoring `.env.example`.
- **`config/graph.yaml` now exists** *(added at G2; this bullet previously said it did not,
  which was true when this document was written)*. It holds non-secret defaults only — URI,
  database name, batch sizes, graph-run root, schema version, timeout, replace policy. §5.1's
  rule is unchanged and is now enforced by a test: credentials come from the environment or the
  ignored `.env`, and nothing secret-shaped may appear in the YAML.
