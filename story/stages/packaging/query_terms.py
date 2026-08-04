"""The terms `search_passages` is called with, derived by code from the candidate.

Responsibility: §11's correction, in one place. *"The planner may not choose search terms. Terms
are derived by code from the candidate's metric aliases and period surfaces (§6.4's
`EvidenceRequest`), never authored by a model."* This module is the derivation, and it is the
only path by which a term reaches §9's fulltext tool on the pipeline's route.

**Why the correction exists, with the number.** AR1 measured that `search_passages` ranks
`ORDER BY score DESC LIMIT 25`, so **adding three innocuous terms to a two-term query dropped 18
of the 25 passages the original returned**. Escaping closes the *operator* channel (proved:
escaped `margin "NOT" gross` → 3,318 hits, unescaped → 372) and does nothing about ordinary
top-k displacement. A model that chooses terms therefore chooses which evidence exists, which is
the silent-omission channel §11 is written to close.

**Why stopwords are screened here and not in retrieval.** R2b's `{limit: 500}` applies before
the document filter, so a degenerate term set starves a filtered query: `["the"]` +
`shareholder_letter` returns **9 rows instead of 26** *(measured)*. The orchestrator's ruling
(IMPLEMENTATION_STEPS §7) is that retrieval discloses and packaging decides, and the first half
of deciding is not to ask a degenerate question. `"the"` matches 6,873 of 8,776 passages; a
metric alias matches 432. So the screen below is not a general stopword list — it is the rule
that a pipeline term must be a **licensed surface**, and a surface no ontology licenses is not
one.

**No term is ever taken from prose.** Not from the candidate's signals — §6.4 forbids prose
there and a live test asserts none of the 19 signal keys carries any — not from a warning
string, and not from a passage. The two sources are the ontology's alias index and
`extraction.core.models.PeriodRef`'s own rendering of the period, both of which are code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ontology.contracts import ConceptRegistry
from ontology.core.models import MetricDefinition

#: The four Lucene keywords that are also English words. `lucene_escaping.build_query` already
#: quotes them, so this is a second, earlier refusal — a *derived* term that happened to be
#: `NOT` would be a defect in this module, not a query to escape.
_LUCENE_KEYWORDS = frozenset({"AND", "OR", "NOT", "TO"})

#: How short a derived surface may be before it stops selecting. Measured against the alias
#: index: every metric alias in `real_estate_marketplace_v1` 2.0.0 is at least four characters,
#: so this refuses nothing today and is the guard that keeps a one-letter alias added upstream
#: from turning a pipeline search into a corpus scan.
MIN_TERM_CHARS = 4

#: How many terms one search may carry. §11's correction makes the *count* part of the query's
#: identity — every added term re-ranks — so a query built from twelve aliases of four metrics is
#: a different question from one built from two. Six is the largest alias set any single metric
#: in this ontology declares plus its label, and a search that needed more would be a search over
#: more metrics than §10.2's `metrics[]` cap admits.
MAX_TERMS = 6


@dataclass(frozen=True, slots=True)
class DerivedTerms:
    """The terms, and the code path that produced each kind of them.

    `basis` is not decoration. §11's third consequence is that *"the retrieval trace must record
    the exact terms and the `truncated` flag"*, and the trace does — §9's tools are closed
    parameter contracts, so the terms reach it as the tool's own `terms` argument and nothing
    else may. What the trace cannot say is *where the terms came from*, because there is no
    parameter to put it in; `basis` is that, and it is carried into the `search_pool_capped`
    warning, which is the row a reviewer reads when the pool is the reason a query is short.
    """

    terms: tuple[str, ...]
    metric_surfaces: tuple[str, ...]
    period_surfaces: tuple[str, ...]
    dropped: tuple[str, ...]

    @property
    def basis(self) -> str:
        return (
            f"metric_aliases={len(self.metric_surfaces)},"
            f"period_surfaces={len(self.period_surfaces)},"
            f"dropped={len(self.dropped)}"
        )

    def __bool__(self) -> bool:
        return bool(self.terms)


class ModelSuppliedTerm(ValueError):
    """A term that did not come from the ontology or from a period key.

    Its own type because §11's guarantee is the one this module carries, and a caller that got a
    `ValueError` could reasonably retry with the term removed; a caller that gets this should
    stop and find out how a model reached a query.
    """


def metric_surfaces(registry: ConceptRegistry, metric_ids: Sequence[str]) -> tuple[str, ...]:
    """A metric's label and its declared aliases, in the ontology's own order, deduplicated.

    The label first because it is the surface a filing prints — `Adjusted EBITDA`, `Gross
    Margin` — and the aliases after it because they are the variants the alias index licenses.
    Deduplication is case-insensitive on the first occurrence: `gross margin` and `Gross Margin`
    are one query term to a fulltext index and two rows in `terms[]` would spend a slot of
    `MAX_TERMS` on nothing.
    """
    surfaces: list[str] = []
    seen: set[str] = set()
    for metric_id in metric_ids:
        definition = registry.find(metric_id)
        if not isinstance(definition, MetricDefinition):
            continue
        for surface in (definition.label, *definition.aliases):
            folded = surface.strip().casefold()
            if folded and folded not in seen:
                seen.add(folded)
                surfaces.append(surface.strip())
    return tuple(surfaces)


def period_surfaces(period_keys: Sequence[str]) -> tuple[str, ...]:
    """The period keys themselves, which are already surfaces a filing prints.

    `PeriodRef.key` renders `2022-07-01..2022-09-30` as `2022Q3` and `2021-01-01..2021-12-31` as
    `FY2021` (`story/core/periods.py`), and both forms occur in the corpus as column labels. A
    raw `{start}_{end}` key does not, so it is dropped rather than searched for: it would match
    nothing while looking like a period filter.
    """
    return tuple(
        key for key in dict.fromkeys(period_keys)
        if key and "_" not in key and len(key) >= MIN_TERM_CHARS
    )


def derive_terms(
    registry: ConceptRegistry,
    *,
    metric_ids: Sequence[str],
    period_keys: Sequence[str],
    max_terms: int = MAX_TERMS,
) -> DerivedTerms:
    """§11's derivation: metric aliases, then period surfaces, screened and bounded.

    Metric surfaces come first because they are what selects — a metric alias has a pool of 432
    of 8,776 passages, a bare year has thousands — and the period surface narrows within that.
    Reversing the order would spend the term budget on the less selective half.
    """
    metrics = metric_surfaces(registry, metric_ids)
    periods = period_surfaces(period_keys)
    kept: list[str] = []
    dropped: list[str] = []
    for surface in (*metrics, *periods):
        if _is_screened(surface):
            dropped.append(surface)
        elif len(kept) < max_terms:
            kept.append(surface)
        else:
            dropped.append(surface)
    return DerivedTerms(
        terms=tuple(kept),
        metric_surfaces=tuple(s for s in metrics if s in kept),
        period_surfaces=tuple(s for s in periods if s in kept),
        dropped=tuple(dropped),
    )


def refuse_undeclared_terms(
    terms: Sequence[str], derived: DerivedTerms
) -> None:
    """Every term about to be searched came out of `derive_terms`, or this raises.

    The check exists because the guarantee is not *"this module derives terms"* — it is *"no
    other string ever reaches the tool"*, and the two are different claims. Called immediately
    before the retrieval call, so the assertion is on the argument actually passed.
    """
    unknown = sorted(set(terms) - set(derived.terms))
    if unknown:
        raise ModelSuppliedTerm(
            f"{unknown} did not come from the candidate's metric aliases or period surfaces. "
            "§11's correction: adding three innocuous terms to a two-term query evicted 18 of "
            "25 previously-returned passages, so a term list is a choice about which evidence "
            "exists and code makes it")


def _is_screened(surface: str) -> bool:
    """A surface too short, too generic, or a Lucene keyword in disguise."""
    stripped = surface.strip()
    if len(stripped) < MIN_TERM_CHARS:
        return True
    return stripped.upper() in _LUCENE_KEYWORDS


__all__ = [
    "MAX_TERMS",
    "MIN_TERM_CHARS",
    "DerivedTerms",
    "ModelSuppliedTerm",
    "derive_terms",
    "metric_surfaces",
    "period_surfaces",
    "refuse_undeclared_terms",
]
