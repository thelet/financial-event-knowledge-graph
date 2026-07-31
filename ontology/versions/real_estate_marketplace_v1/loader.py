"""Loads `real_estate_marketplace_v1` from its YAML files into typed definitions.

YAML is authoritative. This module knows the *file layout* of the version package and the
*shape* of each section; it never knows a concept name. Adding a metric is a YAML edit.

Two YAML conveniences are normalized here rather than pushed into the models:

- Category-specific files carry `metrics:` / `event_types:` / … rather than repeating
  `category:` on every entry, so the section name supplies the category.
- `external_mappings.yaml` attaches mappings to concepts declared elsewhere, so a FIBO
  review can happen in one file instead of scattered across five.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence, TypeVar

import yaml
from pydantic import ValidationError

from ...contracts import OntologyDefinitions
from ...core.constraints import ConstraintParameters
from ...core.errors import (
    E_UNKNOWN_REFERENCE,
    OntologyLoadError,
    ValidationIssue,
)
from ...core.models import (
    AgreementTypeDefinition,
    AliasEntry,
    ClaimTypeDefinition,
    ConceptDefinition,
    ConceptInstance,
    EntityTypeDefinition,
    EventTypeDefinition,
    EvidenceTypeDefinition,
    ExternalMapping,
    InstrumentTypeDefinition,
    MetricDefinition,
    MetricFormulaVersion,
    OntologyMetadata,
    RelationshipDefinition,
    RoleTypeDefinition,
    StatusTypeDefinition,
)
from ...core.values import Canonicality

DEFINITION_DIR = Path(__file__).resolve().parent

#: file -> section key -> model. Drives loading entirely; there is no per-section code.
_SECTIONS: tuple[tuple[str, str, type[ConceptDefinition]], ...] = (
    ("entities.yaml", "entity_types", EntityTypeDefinition),
    ("roles.yaml", "role_types", RoleTypeDefinition),
    ("instruments.yaml", "instrument_types", InstrumentTypeDefinition),
    ("instruments.yaml", "agreement_types", AgreementTypeDefinition),
    ("metrics.yaml", "metrics", MetricDefinition),
    ("formulas.yaml", "formula_versions", MetricFormulaVersion),
    ("events.yaml", "event_types", EventTypeDefinition),
    ("relationships.yaml", "relationships", RelationshipDefinition),
    ("claims.yaml", "evidence_types", EvidenceTypeDefinition),
    ("claims.yaml", "claim_types", ClaimTypeDefinition),
    ("claims.yaml", "status_types", StatusTypeDefinition),
)

T = TypeVar("T")


class YamlDefinitionLoader:
    """Reads the version package's YAML files. Parse and attach only — no validation.

    Validation belongs to `core.constraints`, called by `context.load_ontology`, so the same
    checks apply to definitions built in a test without touching disk.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or DEFINITION_DIR

    def load(self) -> OntologyDefinitions:
        issues: list[ValidationIssue] = []
        sections = self._parse_sections(issues)
        mappings = self._external_mappings(issues)

        for key, entries in sections.items():
            sections[key] = tuple(_attach_mappings(entry, mappings) for entry in entries)

        unattached = set(mappings) - {c.concept_id for group in sections.values() for c in group}
        for concept_id in sorted(unattached):
            issues.append(ValidationIssue(
                E_UNKNOWN_REFERENCE,
                f"external_mappings.yaml/{concept_id}",
                "mappings declared for a concept that does not exist",
                concept_id=concept_id,
            ))

        if issues:
            raise OntologyLoadError(issues)

        return OntologyDefinitions(
            metadata=OntologyMetadata(**self._read("ontology.yaml")),
            instances=self._instances(),
            aliases=self._aliases(),
            constraints=ConstraintParameters.from_yaml(self._read("constraints.yaml")),
            **sections,  # type: ignore[arg-type]
        )

    # -- sections ----------------------------------------------------------------------

    def _parse_sections(
        self, issues: list[ValidationIssue]
    ) -> dict[str, tuple[ConceptDefinition, ...]]:
        documents: dict[str, Mapping[str, Any]] = {}
        sections: dict[str, tuple[ConceptDefinition, ...]] = {}

        for filename, key, model in _SECTIONS:
            if filename not in documents:
                documents[filename] = self._read(filename)
            raw_entries = documents[filename].get(key, ())
            sections[key] = tuple(
                _build(model, raw, f"{filename}/{key}[{index}]", issues)
                for index, raw in enumerate(raw_entries)
            )
            sections[key] = tuple(e for e in sections[key] if e is not None)

        return sections

    # -- non-concept sections ------------------------------------------------------------

    def _instances(self) -> tuple[ConceptInstance, ...]:
        raw = self._read("entities.yaml").get("instances", ())
        return tuple(ConceptInstance(**_stringify_properties(entry)) for entry in raw)

    def _aliases(self) -> tuple[AliasEntry, ...]:
        raw = self._read("aliases.yaml").get("aliases", ())
        return tuple(AliasEntry(**entry) for entry in raw)

    def _external_mappings(
        self, issues: list[ValidationIssue]
    ) -> dict[str, tuple[Mapping[str, Any], ...]]:
        attached: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for entry in self._read("external_mappings.yaml").get("mappings", ()):
            concept_id = entry["concept_id"]
            if concept_id in attached:
                issues.append(ValidationIssue(
                    "duplicate_mapping_block",
                    f"external_mappings.yaml/{concept_id}",
                    "concept has more than one mapping block; merge them",
                    concept_id=concept_id,
                ))
            attached[concept_id] = tuple(entry.get("external_mappings", ()))
        return attached

    # -- io ------------------------------------------------------------------------------

    def _read(self, filename: str) -> Mapping[str, Any]:
        path = self._directory / filename
        if not path.exists():
            raise OntologyLoadError([ValidationIssue(
                "missing_definition_file", filename, f"expected at {path}"
            )])
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            raise OntologyLoadError([ValidationIssue(
                "malformed_definition_file", filename, "top level must be a mapping"
            )])
        return parsed


# --------------------------------------------------------------------------------------


def _build(
    model: type[T], raw: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> T | None:
    """Turn one pydantic failure into one issue per field, keeping the file path.

    Letting `ValidationError` escape would report the first bad entry and lose every other,
    which makes fixing a freshly written YAML file a one-error-per-run exercise.
    """
    try:
        return model(**raw)  # type: ignore[arg-type]
    except ValidationError as error:
        for detail in error.errors():
            location = ".".join(str(part) for part in detail["loc"])
            issues.append(ValidationIssue(
                "invalid_definition",
                f"{path}.{location}" if location else path,
                detail["msg"],
                concept_id=raw.get("concept_id"),
            ))
    except (TypeError, ValueError) as error:
        issues.append(ValidationIssue(
            "invalid_definition", path, str(error), concept_id=raw.get("concept_id")
        ))
    return None


def _attach_mappings(
    concept: ConceptDefinition, mappings: Mapping[str, Sequence[Mapping[str, Any]]]
) -> ConceptDefinition:
    """Merge `external_mappings.yaml` entries onto a concept declared elsewhere.

    Appends rather than replaces: a metric declares its US-GAAP and XBRL mappings inline,
    next to the metric they qualify, while FIBO review happens in one file.
    """
    extra = mappings.get(concept.concept_id)
    if not extra:
        return concept
    # Constructed rather than passed to `model_copy` as raw dicts: `model_copy` does not
    # validate, so a malformed mapping would survive as a dict and only fail much later.
    built = tuple(ExternalMapping(**dict(m)) for m in extra)
    return concept.model_copy(
        update={"external_mappings": (*concept.external_mappings, *built)}
    )


def _stringify_properties(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Instance properties are a free-form map; YAML gives ints and bools for some.

    Coerced to strings so a snapshot hash cannot change because a YAML scalar was quoted
    differently in a later edit.

    `canonicality` is the one property checked against a vocabulary. It decides whether a
    channel may be cited as evidence at all, so a typo there would silently promote a
    discovery-only source to a citable one.
    """
    prepared = dict(entry)
    properties = prepared.get("properties")
    if isinstance(properties, Mapping):
        declared = properties.get("canonicality")
        if declared is not None and declared not in set(Canonicality):
            raise OntologyLoadError([ValidationIssue(
                "unknown_canonicality",
                f"entities.yaml/instances/{entry.get('instance_id')}",
                f"{declared!r} is not one of {[str(c) for c in Canonicality]}",
            )])
        prepared["properties"] = {k: str(v) for k, v in properties.items()}
    return prepared
