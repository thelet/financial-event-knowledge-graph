"""Canonical snapshot and deterministic definition hash.

Same rule the normalization layer already applies to documents and passages: the
authoritative artifact contains no timestamp, run id or code commit, so loading and
serializing unchanged definitions twice is byte-identical and two ontologies can be compared
by hash alone.

That is why `definition_hash` is computed over the snapshot rather than over the YAML bytes:
a comment edit or a key reorder in YAML must not change the identity of the ontology, and a
silently dropped field must.
"""

from __future__ import annotations

from typing import Any

from .contracts import OntologyDefinitions
from .core.identifiers import canonical_hash, canonical_json

#: Excluded from the hashed snapshot because they carry no semantics. `notes` is prose for a
#: human reader; excluding it means a clarifying note does not invalidate a stored hash.
_VOLATILE_FIELDS = frozenset({"notes"})


def snapshot(definitions: OntologyDefinitions) -> dict[str, Any]:
    """A plain-data rendering of the resolved ontology, ordered deterministically."""
    return {
        "metadata": _dump(definitions.metadata),
        "entity_types": _dump_all(definitions.entity_types),
        "role_types": _dump_all(definitions.role_types),
        "instrument_types": _dump_all(definitions.instrument_types),
        "agreement_types": _dump_all(definitions.agreement_types),
        "metrics": _dump_all(definitions.metrics),
        "formula_versions": _dump_all(definitions.formula_versions),
        "event_types": _dump_all(definitions.event_types),
        "relationships": _dump_all(definitions.relationships),
        "evidence_types": _dump_all(definitions.evidence_types),
        "claim_types": _dump_all(definitions.claim_types),
        "status_types": _dump_all(definitions.status_types),
        "instances": sorted(
            (_dump(i) for i in definitions.instances), key=lambda d: d["instance_id"]
        ),
        "aliases": sorted(
            (_dump(a) for a in definitions.aliases), key=lambda d: d["alias"]
        ),
    }


def definition_hash(definitions: OntologyDefinitions) -> str:
    return canonical_hash(snapshot(definitions))


def render_snapshot(definitions: OntologyDefinitions) -> str:
    """The exact bytes whose hash is `definition_hash`. Written to disk unchanged."""
    return canonical_json(snapshot(definitions))


def _dump_all(concepts: Any) -> list[dict[str, Any]]:
    # Sorted by id, not by declaration order: reordering a YAML file for readability must
    # not change the hash.
    return sorted((_dump(c) for c in concepts), key=lambda d: d.get("concept_id", ""))


def _dump(model: Any) -> dict[str, Any]:
    dumped = model.model_dump(mode="json", exclude_none=True)
    return {k: v for k, v in dumped.items() if k not in _VOLATILE_FIELDS}
