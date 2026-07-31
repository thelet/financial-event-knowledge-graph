"""Indexed lookup over a loaded set of definitions.

Built once, then read-only. Every index is derived from `OntologyDefinitions`; nothing here
reads a file or names a concept.

Alias resolution deliberately returns *all* candidates rather than a best guess. "gross
profit" genuinely denotes both the GAAP measure and the adjusted one, and the ontology's job
is to say so, not to pick. Callers that need one answer must supply the context that decides
it.
"""

from __future__ import annotations

from collections import defaultdict

from .contracts import OntologyDefinitions
from .core.errors import ConceptNotFoundError
from .core.identifiers import normalize_alias
from .core.models import (
    AliasEntry,
    ConceptDefinition,
    ConceptInstance,
    EventTypeDefinition,
    MetricDefinition,
    MetricFormulaVersion,
    RelationshipDefinition,
)
from .core.values import ConceptCategory


class InMemoryConceptRegistry:
    """Concept, alias, formula and relationship lookup."""

    def __init__(self, definitions: OntologyDefinitions) -> None:
        self._definitions = definitions
        self._by_id: dict[str, ConceptDefinition] = {
            concept.concept_id: concept for concept in definitions.concepts
        }
        self._by_category: dict[str, tuple[ConceptDefinition, ...]] = {}
        grouped: dict[str, list[ConceptDefinition]] = defaultdict(list)
        for concept in definitions.concepts:
            grouped[str(concept.category)].append(concept)
        self._by_category = {k: tuple(v) for k, v in grouped.items()}

        self._relationships: dict[str, RelationshipDefinition] = {
            r.relationship_id: r for r in definitions.relationships
        }
        self._instances: dict[str, ConceptInstance] = {
            i.instance_id: i for i in definitions.instances
        }
        self._formulas: dict[str, list[MetricFormulaVersion]] = defaultdict(list)
        for formula in definitions.formula_versions:
            self._formulas[formula.metric_id].append(formula)
        for versions in self._formulas.values():
            versions.sort(key=lambda f: (f.valid_from, f.version))

        self._aliases = _build_alias_index(definitions)
        self._alias_entries: dict[str, AliasEntry] = {
            normalize_alias(entry.alias): entry for entry in definitions.aliases
        }

    # -- concepts ------------------------------------------------------------------------

    @property
    def definitions(self) -> OntologyDefinitions:
        return self._definitions

    def find(self, concept_id: str) -> ConceptDefinition | None:
        return self._by_id.get(concept_id)

    def concept(self, concept_id: str) -> ConceptDefinition:
        concept = self._by_id.get(concept_id)
        if concept is None:
            raise ConceptNotFoundError(f"no concept {concept_id!r} in {self._label()}")
        return concept

    def metric(self, metric_id: str) -> MetricDefinition:
        concept = self.concept(metric_id)
        if not isinstance(concept, MetricDefinition):
            raise ConceptNotFoundError(
                f"{metric_id!r} is a {concept.category}, not a metric definition"
            )
        return concept

    def event_type(self, event_type_id: str) -> EventTypeDefinition:
        concept = self.concept(event_type_id)
        if not isinstance(concept, EventTypeDefinition):
            raise ConceptNotFoundError(
                f"{event_type_id!r} is a {concept.category}, not an event type"
            )
        return concept

    def relationship(self, relationship_id: str) -> RelationshipDefinition | None:
        return self._relationships.get(relationship_id)

    def instance(self, instance_id: str) -> ConceptInstance | None:
        return self._instances.get(instance_id)

    def by_category(self, category: str) -> tuple[ConceptDefinition, ...]:
        return self._by_category.get(str(category), ())

    @property
    def metrics(self) -> tuple[MetricDefinition, ...]:
        return self._definitions.metrics

    # -- aliases -------------------------------------------------------------------------

    def resolve_alias(self, surface_form: str) -> tuple[ConceptDefinition, ...]:
        """All concepts a surface form may denote, ordered by concept id.

        Ordered so a caller inspecting the result gets the same answer every run — an
        unordered set here would make ambiguity reports non-reproducible.
        """
        ids = self._aliases.get(normalize_alias(surface_form), ())
        return tuple(self._by_id[i] for i in ids if i in self._by_id)

    def is_ambiguous(self, surface_form: str) -> bool:
        key = normalize_alias(surface_form)
        entry = self._alias_entries.get(key)
        if entry is not None and entry.ambiguous:
            return True
        return len(self._aliases.get(key, ())) > 1

    def alias_entry(self, surface_form: str) -> AliasEntry | None:
        return self._alias_entries.get(normalize_alias(surface_form))

    @property
    def alias_index(self) -> dict[str, tuple[str, ...]]:
        return dict(self._aliases)

    # -- formulas ------------------------------------------------------------------------

    def formulas_for(self, metric_id: str) -> tuple[MetricFormulaVersion, ...]:
        return tuple(self._formulas.get(metric_id, ()))

    def formula_for(
        self, metric_id: str, as_of: str | None = None
    ) -> MetricFormulaVersion | None:
        """The formula in force on a date.

        `as_of` is the date of the VALUE, not of the filing. A 2024 filing restating FY2021
        must use the FY2021 formula for the FY2021 column, and passing the filing date here
        would silently return the wrong one.

        With no date, the latest version is returned — correct for "what is the formula now",
        and never used by validation, which always has a date.
        """
        versions = self._formulas.get(metric_id, [])
        if not versions:
            return None
        if as_of is None:
            return versions[-1]
        for formula in versions:
            if formula.valid_from <= as_of and (
                formula.valid_to is None or as_of <= formula.valid_to
            ):
                return formula
        return None

    # -- taxonomy ------------------------------------------------------------------------

    def ancestors(self, concept_id: str) -> tuple[str, ...]:
        """`is_a` chain, nearest first. Cycles are caught at load; guarded anyway."""
        chain: list[str] = []
        seen = {concept_id}
        current = getattr(self.find(concept_id), "is_a", None)
        while current and current not in seen:
            chain.append(current)
            seen.add(current)
            current = getattr(self.find(current), "is_a", None)
        return tuple(chain)

    def is_a(self, concept_id: str, ancestor_id: str) -> bool:
        return concept_id == ancestor_id or ancestor_id in self.ancestors(concept_id)

    def accepts_type(self, declared: tuple[str, ...], candidate: str) -> bool:
        """Whether a declared endpoint or player list admits a candidate type.

        Subtype-aware: a slot declaring `company` accepts `public_company`, because a public
        company is a company. Without this, every declaration would have to enumerate its own
        subtypes and would silently rot when one is added.
        """
        return any(self.is_a(candidate, allowed) for allowed in declared)

    # -- internals -----------------------------------------------------------------------

    def _label(self) -> str:
        return self._definitions.metadata.ontology_id


def _build_alias_index(definitions: OntologyDefinitions) -> dict[str, tuple[str, ...]]:
    """Merge concept-local aliases with `aliases.yaml`, plus every canonical label.

    Labels are indexed too because a table row usually holds the canonical label verbatim,
    and requiring it to be repeated in `aliases` would be duplication that drifts.
    """
    index: dict[str, set[str]] = defaultdict(set)

    for concept in definitions.concepts:
        # Formula versions are excluded: they share their metric's label by design
        # ("Contribution Profit (Loss)"), and a table row naming that phrase means the
        # metric, never the dated formula behind it.
        if concept.category == ConceptCategory.METRIC_FORMULA:
            continue
        for surface in (concept.label, concept.concept_id, *concept.aliases):
            key = normalize_alias(surface)
            if key:
                index[key].add(concept.concept_id)

    for entry in definitions.aliases:
        key = normalize_alias(entry.alias)
        if key:
            index[key].update(entry.concept_ids)

    return {key: tuple(sorted(ids)) for key, ids in index.items()}
