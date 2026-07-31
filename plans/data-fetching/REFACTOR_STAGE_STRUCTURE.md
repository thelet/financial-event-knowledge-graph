# Refactor — stage-oriented structure for the acquisition package

**Status:** plan.
**Scope:** structure only. No behavior, format, schema, ID, or corpus change.

Refactors the package built by
[V0_OPENDOOR_FETCH.md](V0_OPENDOOR_FETCH.md) from a flat 16-module package into a
stage-oriented one. The acquired corpus (109 filings, 2,019 artifacts) must survive
untouched.

---

# 1. Verdict on the proposed structure

**Adopt it, with three deviations.** The proposal is well judged for this codebase: it is
deeper than the current flat package without importing a ports-and-adapters framework, and
it fixes a real defect rather than only rearranging files.

## What it gets right

**Extracting the pipeline is the strongest argument.** `cli.py` is the largest module (360
lines) and imports 12 of the other 15. It currently owns stage ordering, composition,
persistence of run records, and terminal rendering all at once. `cmd_acquire` mutates
`args` between stages to make later stages pick up earlier manifests — orchestration
smuggled through an argparse namespace. That belongs in a pipeline object.

**A composition root is warranted, and buys a correctness improvement.** Each CLI command
currently constructs its own `SecClient`, so each gets its own `RateLimiter`. Back-to-back
stages can therefore briefly exceed the intended request rate. One client owned by the
context and shared across stages makes rate limiting globally correct rather than
per-stage.

**`core/` versus `utils/` is drawn in the right place.** Storage, config, manifests, and
the SEC client carry infrastructure meaning and belong in `core/`. The instruction not to
create a utility dumping ground is the right instinct.

## What it fixes that is a genuine defect, not just layout

`verify.py` and `report.py` both contain:

```python
from .catalog import ARTIFACTS_CATALOG, FILINGS_CATALOG, read_jsonl
```

Two stages depend on a third stage's module. Under the proposed rule that stages must not
import one another, this has to be resolved, and resolving it is an improvement
independent of any directory move: a generic JSONL reader and two on-disk filenames were
parked in the module that happened to write them first.

## Deviation 1 — parameterize the protocol at the pipeline

As written, the protocol is decorative:

```python
class AcquisitionPipeline:
    def __init__(self, discover: PipelineStage, resolve: PipelineStage, ...)
```

Unparameterized `PipelineStage` is `PipelineStage[Any, Any]`, so no type checker can catch
passing the resolve stage where discover belongs. The protocol is still worth defining —
it standardizes `name` and `run()` and documents the seam — but the pipeline must annotate
the parameterized form:

```python
def __init__(
    self,
    discover: PipelineStage[DiscoverRequest, DiscoverResult],
    resolve: PipelineStage[ResolveRequest, ResolveResult],
    ...
)
```

Same shape, same cost, actual type safety.

A related caution for tests: `@runtime_checkable` verifies method *presence* only, never
signatures, so `isinstance(stage, PipelineStage)` proves almost nothing. Conformance tests
must call stages *through* the protocol with real request objects, not assert `isinstance`
and stop.

## Deviation 2 — one `utils/` module, justified by the defect above

`utils/jsonl.py` holding `read_jsonl` / `write_jsonl`: stateless, no application state, no
domain concept, used by three stages. That is exactly the stated bar.

The two catalog *filenames* do not belong there — they name files on disk, the same
responsibility `core/storage.py` already carries via `FILING_METADATA_NAME`. They move to
`core/storage.py`.

Nothing else qualifies. `canonical_hash` stays in `core/config.py` (it defines
configuration identity), `sha256_file` stays in `core/storage.py` (it defines artifact
integrity). Neither is a generic helper despite looking like one.

## Deviation 3 — no compatibility re-exports

The proposal permits temporary shims. Nothing outside this repository imports these
modules, and the tests are being updated in the same change, so shims would be dead weight
from the moment they were written. All imports move in one pass and the old paths
disappear.

## What I would not change

The package-per-stage layout costs six near-empty `__init__.py` files, and five of the six
stages fit comfortably in one `stage.py` today. A flatter `stages/*.py` alternative would
save those files. I recommend against it: `resolve` genuinely needs a second module for
the SGML parser, mixed granularity is worse than uniform granularity, and `verify.py` (312
lines) and `report.py` (233 lines) are the next plausible split candidates. The `__init__.py`
is doing real work — it is where the public surface is declared.

---

# 2. Baseline

Recorded before any change, at commit `96b2274`, tag `checkpoint-flat-acquisition`:

| Measure | Value |
| --- | --- |
| Offline tests | **249 passed** |
| Live tests | **5 passed** |
| Filings | **109** |
| Artifacts | **2,019** |
| `data/raw` | **746 MB** |
| `filings.jsonl` md5 | `ff7004227447d710970357e443f83cb0` |
| `artifacts.jsonl` md5 | `5efd26657876390067ebf3b76fd847ac` |

Every one of these must be identical at the end.

---

# 3. Current module map

| Module | Lines | Responsibility | Imports (internal) | Destination |
| --- | --- | --- | --- | --- |
| `cli.py` | 360 | argparse, composition, ordering, run records, rendering | 12 modules | split: `cli.py` + `context.py` + `pipeline.py` |
| `resolution.py` | 321 | artifact resolution, cross-checks, summary | config, identity, models, sec_client, sgml | `stages/resolve/stage.py` |
| `identity.py` | 319 | identity, naming, classification (pure) | — | `core/identity.py` |
| `verify.py` | 312 | corpus verification checks | catalog, models, storage | `stages/verify/stage.py` |
| `sec_client.py` | 273 | rate limiting, retry, HTTP | config | `core/sec_client.py` |
| `download.py` | 252 | staging, download, finalize, repair | config, models, runmeta, sec_client, storage | `stages/download/stage.py` |
| `report.py` | 233 | corpus report rendering | catalog, models | `stages/report/stage.py` |
| `models.py` | 220 | persisted schemas | — | `core/models.py` |
| `catalog.py` | 215 | catalog rebuild, JSONL read/write | models, storage | split: `stages/catalog/stage.py` + `utils/jsonl.py` |
| `config.py` | 199 | config load, canonical hashing | identity | `core/config.py` |
| `storage.py` | 192 | atomic finalization, integrity | models | `core/storage.py` |
| `discovery.py` | 183 | submissions API, filtering, summary | config, identity, models, sec_client | `stages/discover/stage.py` |
| `sgml.py` | 121 | SGML header parser (pure) | — | `stages/resolve/sgml.py` |
| `manifests.py` | 115 | immutable manifest persistence | config, models | `core/manifests.py` |
| `runmeta.py` | 77 | run identity, environment provenance | models | `core/runmeta.py` |

No import cycles exist today. Two stage→stage edges exist (`verify→catalog`,
`report→catalog`) and are removed by this refactor.

---

# 4. Target tree

```text
acquisition/
├── __init__.py
├── __main__.py
├── cli.py                     argparse, rendering, exit codes
├── contracts.py               PipelineStage protocol
├── context.py                 composition root
├── pipeline.py                AcquisitionPipeline
│
├── core/
│   ├── __init__.py
│   ├── models.py
│   ├── identity.py
│   ├── config.py
│   ├── sec_client.py
│   ├── storage.py             + catalog filenames
│   ├── manifests.py
│   └── runmeta.py             + run-record writing
│
├── utils/
│   ├── __init__.py
│   └── jsonl.py               read_jsonl / write_jsonl
│
└── stages/
    ├── __init__.py
    ├── discover/{__init__,stage}.py
    ├── resolve/{__init__,stage,sgml}.py
    ├── download/{__init__,stage}.py
    ├── catalog/{__init__,stage}.py
    ├── verify/{__init__,stage}.py
    └── report/{__init__,stage}.py
```

# 5. Dependency direction

```text
core, utils, contracts     (no intra-package imports upward)
        ↑
stages/*                   (import core, utils, contracts; never each other)
        ↑
pipeline                   (imports contracts + stage public types)
        ↑
context                    (constructs concrete stages + pipeline)
        ↑
cli                        (imports context + public request types)
```

Cycle check: `contracts` imports only `typing`. `core` imports nothing above it. Each stage
imports downward only. `pipeline` imports stage packages' public surface, and no stage
imports `pipeline`. `context` imports stages and pipeline; neither imports `context`. `cli`
imports `context`; nothing imports `cli` except `__main__`. Acyclic.

Enforced by a test that walks the import graph and asserts these rules.

# 6. Stage contract

`contracts.py`:

```python
RequestT = TypeVar("RequestT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)

@runtime_checkable
class PipelineStage(Protocol[RequestT, ResultT]):
    @property
    def name(self) -> str: ...
    def run(self, request: RequestT) -> ResultT: ...
```

Plus a `StageResult` protocol carrying `ok: bool`, so the pipeline can decide whether to
abort without knowing any concrete result type.

No `prepare`/`validate`/`finalize`/`rollback` lifecycle — the stages do not share one.

# 7. Stage request/result types

Defined in each `stage.py`, frozen dataclasses. Persisted schemas stay in `core/models.py`.

| Stage | Request | Result (all carry `ok`) |
| --- | --- | --- |
| discover | `cik`, `include_amendments` | `run_id`, `manifest_path`, `filings`, `filing_count` |
| resolve | `filings_run_id`, `limit`, `progress` | `run_id`, `manifest_path`, `artifacts`, `anomalies` |
| download | `artifacts_run_id`, `limit`, `force`, `progress` | `run_id`, `summary`, `bytes_downloaded`, `requests_made` |
| catalog | (empty) | `filings_path`, `artifacts_path`, counts, duplicates |
| verify | `filings_run_id`, `artifacts_run_id`, `check_hashes` | `report` |
| report | `artifacts_run_id` | `path`, `text` |

`ok` reproduces today's exit-code logic exactly: discover/resolve require a non-empty
result, download requires no failures, catalog requires no duplicates or unreadable
metadata, verify requires no errors, report is always ok.

Stages own their own persistence (manifest, run record) through injected `core` services.
Stages never print; they accept an optional `progress` callable and return data the CLI
renders.

# 8. Pipeline

`AcquisitionPipeline.run(AcquisitionRequest) -> AcquisitionResult`, ordering:

```text
discover → resolve → download → catalog → verify      (abort on first not-ok)
                                                → report   (always runs if verify passed)
```

This reproduces `cmd_acquire` precisely, including that `report` runs last and its outcome
is the overall outcome. Each stage's `run_id` is passed explicitly to the next, replacing
today's `args` mutation. The pipeline performs no HTTP and no filesystem work and holds no
knowledge of stage internals.

# 9. Context

`build_acquisition_context(root=None) -> AcquisitionContext`, a context manager owning the
loaded config, one shared `SecClient`, the store, the manifest repository, the six
concrete stages, and the assembled pipeline. This is the single place concrete
implementations are named:

```python
discover = SecDiscoverStage(...)
resolve  = SgmlResolveStage(...)
download = LocalDownloadStage(...)
catalog  = JsonlCatalogStage(...)
verify   = CorpusVerifyStage(...)
report   = MarkdownReportStage(...)
```

`AcquisitionContext` accepts pre-built stages so tests can inject fakes. No factory or
registry: the near-term need does not exist, and the structure permits adding one later.

# 10. CLI

Keeps every current command, flag, and meaningful output. Reduced to: parse args → build
context → build a stage or pipeline request → call `run()` → render → map `ok` to an exit
code. `acquire` becomes `context.pipeline.run(...)`. No SEC calls, no storage operations,
no verification logic, no duplicated ordering.

# 11. Tests

Moved to `tests/acquisition/` (a parsing package is coming; adopting the prefix now avoids
a second move). Existing coverage is preserved — files are split along the new boundaries
where they already have section markers, not rewritten.

```text
tests/
├── conftest.py                       shared fixtures and doubles
└── acquisition/
    ├── core/       test_identity, test_config, test_manifests, test_sec_client, test_storage
    ├── stages/     test_discover, test_resolve, test_resolve_sgml, test_download,
    │               test_catalog, test_verify
    ├── test_contracts.py             protocol conformance, called through the protocol
    ├── test_pipeline.py              ordering, data flow, abort, fake injection
    ├── test_context.py               composition wiring
    ├── test_cli.py                   delegation to stages and pipeline
    ├── test_structure.py             import-direction rules
    └── integration/test_live.py
```

New tests must prove: every concrete stage satisfies the protocol *by being called through
it*; each stage is usable via its package's public surface alone; the pipeline calls stages
in order and threads outputs; the pipeline aborts exactly where `cmd_acquire` did; fake
stages can be injected; a stage implementation can be swapped without touching
`pipeline.py`; `acquire` delegates to the pipeline; each command delegates to its stage.

# 12. Compatibility

Unchanged, and verified rather than assumed: filing and artifact manifest formats,
`_filing.json`, catalog formats and byte-level output, filing and artifact IDs, the
`data/raw` directory layout, all hashes, run records, CLI commands and flags, deterministic
catalog rebuild, resumability and repair, verification semantics, and the existing 109
filings / 2,019 artifacts.

The corpus is not reacquired. `download` is expected to report 109 skipped and **0
requests** after the refactor.

# 13. Migration steps

Each step ends green before the next begins.

1. `core/` package; move models, identity, config, sec_client, storage, manifests, runmeta;
   update all imports.
2. `utils/jsonl.py`; move catalog filenames into `core/storage.py`; remove both
   `verify→catalog` and `report→catalog` edges.
3. `contracts.py`.
4. `stages/` packages, one stage at a time: move module, add request/result/stage wrapper,
   export from `__init__.py`.
5. `pipeline.py`.
6. `context.py`, including run-record writing moved to `core/runmeta.py`.
7. Rewrite `cli.py` thin.
8. Reorganize tests; add contract, pipeline, context, CLI, and structure tests.
9. Validation (§14).
10. Update `V0_OPENDOOR_FETCH.md` and `README.md` with the package map.

# 14. Validation

1. Full offline suite green, count ≥ 249.
2. Live suite green.
3. Each stage runnable independently via its CLI command.
4. Full `acquire` against the existing corpus.
5. `download` performs **0** requests.
6. Catalog rebuild byte-identical to the recorded md5s.
7. `verify` exits 0 with no findings.
8. 109 filings and 2,019 artifacts unchanged.
9. No persisted schema or ID changed.
10. No parsing, normalization, extraction, graph, or RAG code added.
