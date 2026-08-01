"""Extraction tests read the *normalized* corpus.

`tests/conftest.py` binds `repo_config` to the acquisition configuration, whose
`catalog_root` is `data/catalog`. The normalized passage catalog lives under
`data/normalization_catalog`, so without this override every corpus-backed check here
skips silently rather than failing — which is how a benchmark full of dangling passage ids
would go unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from normalization.core.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def repo_config():
    return load_config(REPO_ROOT)
