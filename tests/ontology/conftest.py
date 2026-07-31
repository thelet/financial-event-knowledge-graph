"""Shared fixtures. The real ontology is loaded once — it is read-only after construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from ontology import load_ontology
from ontology.versions.real_estate_marketplace_v1.public import (
    DEFINITION_DIR,
    EXAMPLES_DIR,
)

PACKAGE = Path(__file__).resolve().parents[2] / "ontology"


@pytest.fixture(scope="session")
def ontology():
    return load_ontology()


@pytest.fixture(scope="session")
def registry(ontology):
    return ontology.registry


@pytest.fixture(scope="session")
def definitions(ontology):
    return ontology.definitions


@pytest.fixture(scope="session")
def definition_dir() -> Path:
    return DEFINITION_DIR


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES_DIR
