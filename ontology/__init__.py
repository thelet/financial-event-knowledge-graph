"""Executable ontology for the financial-event knowledge graph.

A library, not a pipeline: it loads, resolves, answers questions and validates. It has no
LLM, graph, embedding, vector-store or database dependency, and it does not import the
acquisition or normalization packages — evidence is referenced by id through a small
boundary model so either side can change independently.
"""

from .context import (
    DEFAULT_ONTOLOGY_ID,
    LoadedOntology,
    available_ontologies,
    build_ontology,
    load_ontology,
)
from .contracts import ConceptRegistry, Ontology, OntologyDefinitions

__all__ = [
    "ConceptRegistry",
    "DEFAULT_ONTOLOGY_ID",
    "LoadedOntology",
    "Ontology",
    "OntologyDefinitions",
    "available_ontologies",
    "build_ontology",
    "load_ontology",
]
