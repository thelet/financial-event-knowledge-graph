"""Ontology alias evidence for a passage.

The lexical half of candidate scoping, and the half that must never be overruled. Whatever
retrieval is added later — `HybridOntologyCandidateScope` and its `EmbeddingProvider` — may
only *add* to what this module finds. An exact alias, an ambiguous alias, a table row label,
a stable core concept and a confusion-group sibling are all protected: the ontology's
`distinct_from` declarations exist to stop `homes_sold` collapsing into `homes_purchased`
and adjusted figures into GAAP ones, and a cosine distance is not entitled to overrule a
declared distinction.

Nothing here names a metric. The alias index is built from whatever ontology is loaded, so a
vocabulary edit changes selection without a code change, and nothing is hard-coded around
the twenty in-scope metrics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The fold moved to `core.concept_resolution` when the canonical-label precedence was
# extracted there (STAGE_08 \u00a72): that module is the lowest layer that needs it, and `core/`
# may not import a stage. Re-exported here because it is part of this module's published
# surface and callers should not have to know where matching normalisation lives.
from ...core.concept_resolution import fold

# Table row labels sit in the first cell of a Markdown pipe row.
_ROW_LABEL = re.compile(r"^\|\s*([^|]{2,120}?)\s*\|", re.M)
_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


def row_labels(text: str) -> tuple[str, ...]:
    """The first cell of every Markdown pipe row, in document order.

    Public because the lexical scope resolves each one through the shared resolver rather
    than re-deriving what counts as a row label from the same regex a second time.
    """
    return tuple(_ROW_LABEL.findall(text or ""))


def matches_whole(surface: str, text: str) -> bool:
    """Whether a surface form occurs as a whole phrase in `text`, exactly as `hits` decides it.

    Exposed so a caller can ask the same question of the raw and the folded text and learn
    whether the fold was what made the match \u2014 the difference between `exact_alias` and
    `normalized_alias`.
    """
    return bool(_boundary(surface).search(text or ""))


def _boundary(alias: str) -> re.Pattern[str]:
    """Whole-phrase match. Substring matching turns "margin" into a hit inside
    "marginal" and every ambiguity count becomes noise."""
    pattern = _WORD_BOUNDARY_CACHE.get(alias)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(fold(alias))}(?![a-z0-9])", re.I)
        _WORD_BOUNDARY_CACHE[alias] = pattern
    return pattern


@dataclass(frozen=True)
class AliasHit:
    surface_form: str
    concept_ids: tuple[str, ...]
    ambiguous: bool
    in_row_label: bool = False
    # Whether any matched concept is a metric. Entity aliases — "opendoor", "the company" —
    # match almost every passage in a single-company corpus, so treating any alias hit as
    # candidate evidence selects 6,098 of 10,507 narrative passages and means nothing. The
    # lanes consume metric observations; metric evidence is what makes a passage a
    # candidate. Entity hits are still recorded, because they are what a subject resolver
    # will need.
    is_metric: bool = False


@dataclass
class AliasIndex:
    """Surface form to concepts, taken from the loaded ontology's registry.

    Formula versions are already absent from that index — the ontology work excluded them
    after finding that `contribution_profit_v1`, labelled "Contribution Profit (Loss)", made
    every mention of that phrase resolve to both the metric and the dated formula behind it
    and look ambiguous when it is not. Rebuilding the index here would reintroduce it.
    """

    by_surface: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ambiguous_surfaces: frozenset[str] = frozenset()
    metric_concepts: frozenset[str] = frozenset()
    # A metric's own canonical label, folded and lowercased. Kept separate from
    # `by_surface` because a shared ambiguous alias can be spelled identically to a
    # concept's canonical label and would otherwise shadow it - `gaap_gross_margin` is
    # labelled "Gross Margin", and `aliases.yaml` declares that same string ambiguous.
    canonical_labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_ontology(cls, ontology) -> "AliasIndex":
        """Built from the registry's own alias index, not re-derived from metric labels.

        The seven deliberately ambiguous forms — `homes`, `contracts`, `under contract`,
        `gross profit`, `gross margin`, `margin`, `contribution` — are declared in
        `aliases.yaml` and belong to no single concept's vocabulary, so a surface set built
        from metric labels alone misses every one of them. Asking the registry gets all 267
        surfaces and its own ambiguity verdict, which is the answer that must govern:
        picking one candidate for a bare "homes" is what merges `homes_purchased` into
        `homes_under_contract`, the failure the ontology research names as the most damaging
        available to this vocabulary.
        """
        surfaces: dict[str, tuple[str, ...]] = {}
        ambiguous: set[str] = set()

        for surface, concept_ids in ontology.registry.alias_index.items():
            key = str(surface).strip().lower()
            if len(key) < 3:
                continue
            surfaces[key] = tuple(sorted(str(c) for c in concept_ids))
            if ontology.registry.is_ambiguous(key):
                ambiguous.add(key)

        metric_definitions = ontology.registry.by_category("metric_definition")
        metrics = frozenset(c.concept_id for c in metric_definitions)
        canonical: dict[str, str] = {}
        for concept in metric_definitions:
            label = fold(str(getattr(concept, "label", "") or "")).strip().lower()
            # A label shared by two metrics is genuinely ambiguous and must not be a
            # shortcut for either. None exist today; the guard keeps that true.
            if label:
                canonical[label] = "" if label in canonical else concept.concept_id
        return cls(
            by_surface=surfaces,
            ambiguous_surfaces=frozenset(ambiguous),
            metric_concepts=metrics,
            canonical_labels={k: v for k, v in canonical.items() if v},
        )

    def confusion_siblings(self, ontology, concept_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Every metric a matched metric is declared `distinct_from`, plus the reverse.

        Carried into the candidate set on purpose. If a passage mentions `homes_sold`, the
        lane must also be able to see `homes_purchased` — otherwise it cannot tell that the
        row it is reading is the *other* one, and the declaration meant to prevent the
        confusion never gets a chance to.
        """
        siblings: set[str] = set()
        for concept_id in concept_ids:
            concept = ontology.registry.find(concept_id)
            if concept is None:
                continue
            siblings.update(str(s) for s in (getattr(concept, "distinct_from", ()) or ()))
        return tuple(sorted(siblings - set(concept_ids)))

    def hits(self, text: str) -> tuple[AliasHit, ...]:
        if not text:
            return ()
        folded = fold(text)
        labels = fold(" \n".join(row_labels(text))).lower()
        found: list[AliasHit] = []
        for surface, concept_ids in self.by_surface.items():
            if not _boundary(surface).search(folded):
                continue
            found.append(AliasHit(
                surface_form=surface,
                concept_ids=concept_ids,
                ambiguous=surface in self.ambiguous_surfaces,
                in_row_label=bool(_boundary(surface).search(labels)),
                is_metric=bool(set(concept_ids) & self.metric_concepts),
            ))
        return tuple(sorted(found, key=lambda h: h.surface_form))
