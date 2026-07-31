"""Fixtures for normalization tests. No network; corpus files are read-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from normalization.core.config import load_config
from normalization.core.models import SelectedArtifact

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_LIST = REPO_ROOT / "plans" / "normalization" / "spike_fixtures.txt"


@pytest.fixture(scope="session")
def repo_config():
    return load_config(REPO_ROOT)


@pytest.fixture(scope="session")
def corpus_available(repo_config) -> bool:
    return (repo_config.acquisition_catalog_root / "artifacts.jsonl").is_file()


@pytest.fixture(scope="session")
def acquisition_rows(repo_config, corpus_available):
    if not corpus_available:
        pytest.skip("acquisition corpus not present")
    path = repo_config.acquisition_catalog_root / "artifacts.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture(scope="session")
def spike_ids() -> list[str]:
    return [
        l.strip() for l in FIXTURE_LIST.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
