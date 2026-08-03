"""Fixtures for the graph input layer. Offline, no database, no network.

Two corpora, and the difference matters. `tests/fixtures/graph/` is a committed real slice of
run `extract-v1-lexical-833f7bcfbce9` — every test that can run from a clean checkout uses
it. `data/extraction_runs/` holds the full run and is gitignored (`.gitignore:3`), so the
tests that assert the run's own measured numbers — 2,707 recomputed ids, 186 warnings,
46 mirrored rejections — skip when it is absent rather than failing or being marked `live`.
They touch nothing outside the repository, so `live` would be the wrong signal entirely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graph.core.inputs import ExtractionRunInputs, load_run

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "graph"
FIXTURE_RUN = FIXTURE_ROOT / "extraction_run"
FIXTURE_CATALOG = FIXTURE_ROOT / "normalization_catalog"

#: Pinned rather than discovered: the numbers these tests assert are *this* run's numbers,
#: and silently reading a different run would turn a real regression into a mystery.
REAL_RUN_ID = "extract-v1-lexical-833f7bcfbce9"
REAL_RUN = REPO_ROOT / "data" / "extraction_runs" / REAL_RUN_ID
REAL_CATALOG = REPO_ROOT / "data" / "normalization_catalog"

REAL_RUN_AVAILABLE = REAL_RUN.is_dir() and (REAL_CATALOG / "passages.jsonl").is_file()
REAL_RUN_REASON = (
    f"{REAL_RUN} or {REAL_CATALOG} is absent — data/ is gitignored, so the full-run "
    "assertions only run on a machine that has produced the run"
)


@pytest.fixture(scope="session")
def fixture_inputs() -> ExtractionRunInputs:
    return load_run(FIXTURE_RUN)


@pytest.fixture(scope="session")
def real_inputs() -> ExtractionRunInputs:
    if not REAL_RUN_AVAILABLE:  # pragma: no cover - environment-dependent
        pytest.skip(REAL_RUN_REASON)
    return load_run(REAL_RUN, catalog_directory=REAL_CATALOG)


@pytest.fixture(scope="session")
def ontology():
    from ontology import load_ontology

    return load_ontology()
