"""Resolving a metric surface, and assembling a metric definition from the authority for it.

Responsibility: correction **C4**, in one place. `:Metric` nodes carry 22 properties and none
of them is `percentage_min`, `percentage_max`, `distinct_from` or `reconciles_to` *(checked
live 2026-08-03 against `graph-v1-0483dc6b4b10`)*. The ontology carries all four. So metric
metadata comes from `ontology.core.models.MetricDefinition` and the graph contributes exactly
two things: whether the metric was projected at all, and under which ontology hash.

**There is no fallback, and that is the point.** The failure C4 guards against is a consumer
reading `metric.percentage_min` off a node, getting `None` because Neo4j stores no null and
the property was never written, and treating "no bound" as "unbounded" — which turns §13.3's
percentage check, *"the single most likely factual error"*, into a check that always passes.
A definition here is built from a `MetricDefinition` or it is not built; `get_metric_definition`
returns `NotFound` when the ontology does not know the id even if the node exists, and says so.

**Why resolution lives here too.** §9's `Ambiguous(candidates)` names *"a metric surface
resolving to two members of a `mutually_distinct_group`"*, and the index that answers it is the
ontology's alias index — the same authority, one lookup away. Eight surfaces are ambiguous in
`real_estate_marketplace_v1` 2.0.0 *(measured)*: `gross margin` denotes `adjusted_gross_margin`
and `gaap_gross_margin`, `margin` denotes four metrics, `homes` four. A retriever that silently
picked the first would answer a question nobody asked.

The registry is injected. `default_registry()` exists so a tool can be constructed without a
composition root, and it is cached because `load_ontology` parses and validates YAML — but S11
will pass the context's registry in, and nothing here reaches for a file at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ontology.contracts import ConceptRegistry
from ontology.core.models import MetricDefinition

#: The ontology this graph run was projected under (IMPLEMENTATION_STEPS §0). Named rather
#: than defaulted implicitly so a mismatch against `Metric.ontology_id` is a visible field on
#: the returned row instead of a silent disagreement between two authorities.
DEFAULT_ONTOLOGY_ID = "real_estate_marketplace_v1"


@dataclass(frozen=True)
class MetricResolution:
    """What a surface denotes: one metric, several, or none.

    Three fields rather than an `Optional[str]` because the caller has three different answers
    to give — `Ok`, `Ambiguous(candidates)` and `NotFound` — and collapsing "no match" and
    "many matches" into `None` is what makes an ambiguous surface silently resolvable.
    """

    surface: str
    metric_id: str | None
    candidates: tuple[str, ...]

    @property
    def is_ambiguous(self) -> bool:
        return self.metric_id is None and len(self.candidates) > 1


def resolve_metric_surface(registry: ConceptRegistry, surface: str) -> MetricResolution:
    """An exact metric id, then the alias index, and never a guess between candidates.

    Exact id first because `adjusted_gross_margin` is both a concept id and (via its own
    aliases) a surface, and a caller that passed the id means the id. The alias path filters to
    `MetricDefinition`: `resolve_alias` answers over every concept category, and an entity or
    an event type that happens to share a surface with a metric is not a candidate for a tool
    whose contract is `metric_id`.
    """
    exact = registry.find(surface)
    if isinstance(exact, MetricDefinition):
        return MetricResolution(surface=surface, metric_id=exact.concept_id, candidates=(exact.concept_id,))

    matches = tuple(
        concept.concept_id
        for concept in registry.resolve_alias(surface)
        if isinstance(concept, MetricDefinition)
    )
    if len(matches) == 1:
        return MetricResolution(surface=surface, metric_id=matches[0], candidates=matches)
    return MetricResolution(surface=surface, metric_id=None, candidates=matches)


def definition_row(
    definition: MetricDefinition, graph_row: Mapping[str, Any] | None
) -> dict[str, Any]:
    """One `get_metric_definition` row: the ontology's answer, plus what the graph holds.

    Every ontology-sourced field is flat and named as the ontology names it, so a consumer
    comparing a package against `ontology.contracts` is comparing like with like.
    `metadata_source` is a field rather than a docstring because the whole of C4 is a claim
    about provenance, and a row that carries its provenance can be checked by a test.

    `present_in_graph` is `False` rather than absent when the node is missing: a metric the
    ontology defines and the projection never wrote is a real state — it means no observation
    was extracted for it — and it is one a detector needs to see, not one to hide behind a
    `NotFound`.

    `ontology_hash_matches_graph` compares the loaded ontology's `definition_hash` against the
    stamp the projection wrote onto the node. When it is `False` the definition being returned
    is not the definition the graph was built under, and every percentage bound below it is
    suspect — the staleness §7 exists for, surfaced at the one tool that would otherwise launder
    it.
    """
    row: dict[str, Any] = {
        "metric_id": definition.concept_id,
        "metadata_source": "ontology",
        "label": definition.label,
        "description": definition.description,
        "metric_category": _plain(definition.metric_category),
        "value_type": _plain(definition.value_type),
        "unit": definition.unit,
        "allowed_units": list(definition.allowed_units),
        "period_type": _plain(definition.period_type),
        "gaap_status": _plain(definition.gaap_status),
        "subject_types": list(definition.subject_types),
        "source_lane_preferences": [_plain(lane) for lane in definition.source_lane_preferences],
        "forbidden_source_lanes": [_plain(lane) for lane in definition.forbidden_source_lanes],
        # The four C4 fields. They exist here and nowhere on the node; the tests assert that a
        # percentage metric arrives with real bounds, so a regression to a node read fails
        # rather than returning `None` and looking merely empty.
        "percentage_min": definition.percentage_min,
        "percentage_max": definition.percentage_max,
        "distinct_from": list(definition.distinct_from),
        "reconciles_to": definition.reconciles_to,
        "numerator_description": definition.numerator_description,
        "denominator_description": definition.denominator_description,
        "valid_from": definition.valid_from,
        "valid_to": definition.valid_to,
        "aliases": list(definition.aliases),
    }
    row["present_in_graph"] = graph_row is not None
    if graph_row is None:
        row["graph_label"] = None
        row["graph_ontology_id"] = None
        row["graph_ontology_version"] = None
        row["graph_ontology_definition_hash"] = None
        row["graph_run_id"] = None
        row["ontology_hash_matches_graph"] = None
        return row
    row["graph_label"] = graph_row.get("graph_label")
    row["graph_ontology_id"] = graph_row.get("graph_ontology_id")
    row["graph_ontology_version"] = graph_row.get("graph_ontology_version")
    row["graph_ontology_definition_hash"] = graph_row.get("graph_ontology_definition_hash")
    row["graph_run_id"] = graph_row.get("graph_run_id")
    return row


def with_hash_comparison(row: dict[str, Any], ontology_definition_hash: str) -> dict[str, Any]:
    """Fill `ontology_hash_matches_graph` once the loaded ontology's hash is known.

    Separate from `definition_row` because the hash belongs to the *ontology*, not to the
    definition, and threading a whole `LoadedOntology` into a row builder to read one string
    would put the loader inside the row.
    """
    graph_hash = row.get("graph_ontology_definition_hash")
    row["ontology_hash_matches_graph"] = (
        None if graph_hash is None else graph_hash == ontology_definition_hash)
    return row


@lru_cache(maxsize=4)
def default_registry(ontology_id: str = DEFAULT_ONTOLOGY_ID) -> ConceptRegistry:
    """The loaded ontology's registry, parsed once per process.

    Imported inside the function so that importing this module — which the structural tests do
    for every module in `story/` — does not read a directory of YAML. Cached because
    `load_ontology` validates the whole definition set on every call, and nine tools sharing one
    retriever should not pay for that nine times.
    """
    from ontology import load_ontology

    return load_ontology(ontology_id).registry


@lru_cache(maxsize=4)
def default_definition_hash(ontology_id: str = DEFAULT_ONTOLOGY_ID) -> str:
    from ontology import load_ontology

    return load_ontology(ontology_id).definition_hash


def _plain(value: Any) -> Any:
    """An enum member as its value; anything else untouched.

    `MetricCategory`, `ValueType`, `PeriodType`, `GaapStatus` and `SourceLane` are `str` enums,
    so they would serialise correctly by accident — but a row that carried an enum would make
    the package's JSON depend on the enum's `__str__`, and S0 already made that mistake's cost
    explicit by declaring every vocabulary as `str, Enum`.
    """
    return getattr(value, "value", value)


__all__ = [
    "DEFAULT_ONTOLOGY_ID",
    "MetricResolution",
    "default_definition_hash",
    "default_registry",
    "definition_row",
    "resolve_metric_surface",
    "with_hash_comparison",
]
