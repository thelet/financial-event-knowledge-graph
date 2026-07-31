"""The one public entry point for this ontology version.

Everything outside this package imports from here, never from `loader`. Swapping the YAML
loader for a database-backed one would then be invisible to callers — the same reason every
stage package in this repository has a `public.py`.
"""

from __future__ import annotations

from pathlib import Path

from ...contracts import OntologyDefinitions
from .loader import DEFINITION_DIR, VERSION_DIR, YamlDefinitionLoader

ONTOLOGY_ID = "real_estate_marketplace_v1"

#: Example claim fixtures shipped with this version.
EXAMPLES_DIR = VERSION_DIR / "examples"

__all__ = ["ONTOLOGY_ID", "VERSION_DIR", "DEFINITION_DIR", "EXAMPLES_DIR",
           "load_definitions"]


def load_definitions(directory: Path | None = None) -> OntologyDefinitions:
    """Parse this version's definitions. Validation is the composition root's job."""
    return YamlDefinitionLoader(directory).load()
