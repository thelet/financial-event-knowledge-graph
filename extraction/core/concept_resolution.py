"""Resolving a surface form to ontology concepts, and the fold that resolution depends on.

Responsibility: given a concept index and a label, say which concepts the label denotes and
whether the answer is ambiguous. Boundaries: it is handed an index and a string. It does not
load an ontology, read a corpus, open a file, or know what a table row or a passage is — the
index it receives is the whole of its vocabulary, so a vocabulary edit changes resolution
without a code change here.

**It exists because the precedence existed twice.** `deterministic_lane._resolve_label`
implemented "a whole-label canonical match outranks a same-spelled ambiguous alias" for table
rows, and the lexical scope was about to implement it again. Two copies of that rule is how
`gaap_gross_margin` ends up reachable from its own name in one reader and not in the other,
and neither reader can tell. STAGE_08 §2 names a third copy as the same defect that produced
a report scoring itself twice.

The typographic fold lives here rather than in the selection stage because this module is the
lowest layer that needs it and `core/` may not import a stage. `alias_evidence` re-exports it,
so the fold still has exactly one definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from .models import AMBIGUOUS_ALIAS_SIGNAL, EXACT_ALIAS

# How a label was resolved. `CANONICAL_LABEL` is declared here rather than in `models.py`
# alongside the selection reason codes because it is this module's own distinction: selection
# never needed to say "the concept's own label, as opposed to an alias spelled the same way".
CANONICAL_LABEL = "canonical_label"
UNRESOLVED = "unresolved"

RESOLUTION_BASES = frozenset({CANONICAL_LABEL, EXACT_ALIAS, AMBIGUOUS_ALIAS_SIGNAL, UNRESOLVED})

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

_FOOTNOTE_MARKERS = re.compile(r"(\s*\(\d+\))+\s*$")
_PERIOD_QUALIFIER = re.compile(r"\s*\((?:at\s+period\s+end|unaudited)\)\s*$", re.I)


def fold(text: str) -> str:
    """Typographic normalisation for matching only. Never applied to stored text."""
    return (text or "").translate(_TYPOGRAPHY)


def strip_label_qualifiers(label: str) -> str:
    """Normalise a row label for matching.

    Two removals, both typography rather than identity. Trailing note references — "(3)",
    "(4)(5)" — point at a note below the table. And "(at period end)" states *when* a stock
    was measured, not *what* it is: the ontology happens to carry a suffixed alias for
    `housing_inventory_homes` and not for `market_count`, and the metric a row reports
    should not depend on that inconsistency.

    The suffix is stripped for matching only. `periods.is_instant_label` still reads it off
    the original label, so the row is correctly typed as an instant.
    """
    cleaned = _FOOTNOTE_MARKERS.sub("", label or "").strip()
    return _PERIOD_QUALIFIER.sub("", cleaned).strip()


@runtime_checkable
class SurfaceHit(Protocol):
    """One surface form found in a text. `AliasHit` satisfies this."""

    @property
    def surface_form(self) -> str: ...

    @property
    def concept_ids(self) -> tuple[str, ...]: ...

    @property
    def ambiguous(self) -> bool: ...

    @property
    def is_metric(self) -> bool: ...


@runtime_checkable
class ConceptIndex(Protocol):
    """The vocabulary this module resolves against. `AliasIndex` satisfies it structurally.

    Declared as a protocol so `core/` does not import the selection stage that happens to
    build the index today, and so a test can drive the precedence with three surfaces
    instead of 267.
    """

    @property
    def by_surface(self) -> dict[str, tuple[str, ...]]: ...

    @property
    def metric_concepts(self) -> frozenset[str]: ...

    @property
    def canonical_labels(self) -> dict[str, str]: ...

    def hits(self, text: str) -> Sequence[SurfaceHit]: ...


@dataclass(frozen=True)
class LabelResolution:
    """What a label resolved to, and on what grounds.

    `basis` is carried because two callers need different things from the same answer: the
    table lane needs only "which metric, and is it ambiguous", while the scope has to record
    *why* a concept is in the candidate set and must never drop it later.
    """

    concept_ids: tuple[str, ...]
    ambiguous: bool
    basis: str
    normalized_label: str

    @property
    def resolved(self) -> bool:
        """One concept, unambiguously. An abstention is not a resolution."""
        return bool(self.concept_ids) and not self.ambiguous


def resolve_label(index: ConceptIndex, label: str) -> LabelResolution:
    """Resolve a whole label to metric concepts, and say whether it is ambiguous.

    **Whole-label match only, after stripping footnote markers.** Substring matching is what
    a deterministic reader must not do, and the reconciliation tables show why: it reads
    "Contribution Profit per Home Sold" as `contribution_profit` (a $31 per-home figure
    emitted as a $31,000 quarterly total) and "Holding costs on sales – Current Period" as
    `holding_costs`, which is one of two rows the filing never totals. Both are wrong in ways
    that pass every downstream check.

    The precedence, in order:

    1. the whole folded label, footnote markers and the `(at period end)` qualifier removed,
       against the metrics' own canonical labels. A canonical label outranks a same-spelled
       ambiguous alias: `gaap_gross_margin`'s label *is* "Gross Margin", and `aliases.yaml`
       separately declares that string ambiguous across the GAAP and adjusted concepts, so
       without this the metric is unreachable from its own name and every KPI table's
       `Gross Margin` row yields nothing. The ambiguity entry's own note says what it is for
       — "Only the qualifier 'Adjusted' separates them, and it is sometimes only in the row
       label" — which is a rule about bare prose mentions, not about a cell whose entire
       contents are the metric's name;
    2. else the whole label as an exact alias: one metric resolves it, more than one is
       ambiguous;
    3. else, if the only surfaces found inside the label are declared-ambiguous ones, all the
       concepts of the widest such surface, marked ambiguous;
    4. else nothing.
    """
    normalized = strip_label_qualifiers(fold(label)).strip().lower()

    canonical = index.canonical_labels.get(normalized)
    if canonical:
        return LabelResolution((canonical,), False, CANONICAL_LABEL, normalized)

    exact = index.by_surface.get(normalized)
    if exact:
        metric_ids = tuple(c for c in exact if c in index.metric_concepts)
        if len(metric_ids) == 1:
            return LabelResolution(metric_ids, False, EXACT_ALIAS, normalized)
        if len(metric_ids) > 1:
            return LabelResolution(metric_ids, True, AMBIGUOUS_ALIAS_SIGNAL, normalized)

    hits = [h for h in index.hits(label) if h.is_metric]
    ambiguous_hits = [h for h in hits if h.ambiguous]
    if ambiguous_hits:
        # Widest wins: "gross profit" inside "adjusted gross profit" would otherwise let the
        # shorter declared-ambiguous form decide a label the longer one reads exactly.
        widest = max(ambiguous_hits, key=lambda h: len(h.surface_form))
        candidates = tuple(sorted(
            c for c in widest.concept_ids if c in index.metric_concepts))
        return LabelResolution(candidates, True, AMBIGUOUS_ALIAS_SIGNAL, normalized)

    return LabelResolution((), False, UNRESOLVED, normalized)
