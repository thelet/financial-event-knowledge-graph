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

# Table row labels sit in the first cell of a Markdown pipe row.
_ROW_LABEL = re.compile(r"^\|\s*([^|]{2,120}?)\s*\|", re.M)
_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}

# Typographic variants folded before matching. Filings set quotes and dashes as typography;
# the ontology transcribes them as ASCII. `pct_homes_on_market_gt_120_days` is labelled
# `Percentage of homes "on the market" ...` with straight quotes, and every KPI table in the
# corpus prints it with curly ones, so without this fold the metric resolves in no table at
# all.
#
# The encoding correction is what exposed this. Before it these characters arrived as
# mojibake and matched nothing either way, so the mismatch was invisible.
# Quotes are *dropped*, not converted, because that is what the registry already does when
# it builds its own index: the stored surface for the 120-day metric is
# `percentage of homes on the market for greater than 120 days (at period end)` with no
# quote characters at all. Folding to straight quotes would still miss it.
_TYPOGRAPHY = str.maketrans({
    "\u201c": "", "\u201d": "", "\u2018": "", "\u2019": "", '"': "", "'": "",
    "\u2014": "-", "\u2013": "-", "\u2212": "-", "\u00a0": " ",
})


def fold(text: str) -> str:
    """Typographic normalisation for matching only. Never applied to stored text."""
    return (text or "").translate(_TYPOGRAPHY)


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

        metrics = frozenset(
            c.concept_id for c in ontology.registry.by_category("metric_definition")
        )
        return cls(
            by_surface=surfaces,
            ambiguous_surfaces=frozenset(ambiguous),
            metric_concepts=metrics,
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
        row_labels = fold(" \n".join(_ROW_LABEL.findall(text))).lower()
        found: list[AliasHit] = []
        for surface, concept_ids in self.by_surface.items():
            if not _boundary(surface).search(folded):
                continue
            found.append(AliasHit(
                surface_form=surface,
                concept_ids=concept_ids,
                ambiguous=surface in self.ambiguous_surfaces,
                in_row_label=bool(_boundary(surface).search(row_labels)),
                is_metric=bool(set(concept_ids) & self.metric_concepts),
            ))
        return tuple(sorted(found, key=lambda h: h.surface_form))
