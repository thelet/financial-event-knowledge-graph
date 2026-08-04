"""§13.5's alias index: what a metric surface denotes, with longest match winning.

Responsibility: `"adjusted gross margin"` → `{adjusted_gross_margin}`, `"gross margin"` →
refused as declared-ambiguous, and `"the margin"` → refused as unresolved. Nothing here reads
a draft or a graph; it takes the package's own `metrics[]` section and answers questions about
strings.

**The index is built from the package, not from the ontology.** §13.5 names *"the ontology
alias index"* and `story/stages/retrieval/metric_metadata.py` already reaches it — but that
call parses and validates a directory of YAML, and this stage's contract is that it is
constructible with neither a database nor a provider and runs offline in a test. §10's
`metrics[]` is *"the full definition row for every metric the package references"*, populated
from the ontology by the packager (C4), carrying `label`, `aliases` and
`mutually_distinct_groups`. So the authority is the same; the copy that reaches the verifier
is the one the model was shown, which is the copy a verdict about the draft should be made
against.

**What that costs, stated rather than discovered later.** §10.2 caps `metrics[]` at eight, so
a surface that is ambiguous across the ontology's 26 metrics can resolve uniquely inside a
package that carries only one of the two. That is precisely §13.5's headline case —
`gaap_gross_margin`'s own label *is* `"Gross Margin"` — and it would let *"gross margin was
13.2%"* through on a package holding one margin. `DECLARED_AMBIGUOUS` closes it: §13.5's eight
measured surfaces are a module constant and they compete in the same longest-match scan, so
they refuse whether or not the package happens to carry both sides.

**Longest match is mandatory, not an optimisation** (§13.5). `"gross margin" ⊂ "adjusted gross
margin"`, `"adjusted ebitda" ⊂ "adjusted ebitda margin"`, `"revenue" ⊂ "cost of revenue"`. A
left-to-right first-match scanner assigns *"adjusted gross margin was 13.2%"* to
`gaap_gross_margin` — a wrong metric carrying a plausible number, which is §17's attack 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from story.core.models import StoryEvidencePackage

#: §13.5's measured table: eight surfaces that resolve to more than one metric in
#: `real_estate_marketplace_v1` 2.0.0, each with the `mutually_distinct_group` that makes the
#: ambiguity material. Carried as a constant because it is a property of the **ontology**, and
#: a package bounded at eight metrics cannot be asked whether a surface it does not carry is
#: ambiguous. `inventory` has no group and is a §18 gap in the ontology; it is refused here on
#: the same footing, because two metrics under one surface is the condition §13.5 states and
#: the missing group is a reason to fix the ontology rather than to let the surface through.
DECLARED_AMBIGUOUS: Mapping[str, tuple[str, ...]] = {
    "margin": ("adjusted_gross_margin", "gaap_gross_margin", "contribution_margin",
               "adjusted_ebitda_margin"),
    "gross margin": ("adjusted_gross_margin", "gaap_gross_margin"),
    "gross profit": ("adjusted_gross_profit", "gaap_gross_profit"),
    "contribution": ("contribution_margin", "contribution_profit",
                     "contribution_profit_after_interest"),
    "homes": ("homes_sold", "homes_purchased", "housing_inventory_homes",
              "homes_under_contract"),
    "contracts": ("acquisition_contracts", "homes_under_contract"),
    "under contract": ("homes_under_contract", "homes_under_resale_contract"),
    "inventory": ("housing_inventory_homes", "inventory_balance"),
}

_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


def normalise(surface: str) -> str:
    """Lowercase, punctuation dropped, whitespace collapsed, leading article removed.

    `"Adjusted Gross Margin"`, `"adjusted gross-margin"` and `"the adjusted gross margin"` are
    one surface. Punctuation is dropped rather than mapped because the corpus's own labels
    carry hyphens and parentheses inconsistently (`"Adjusted Gross Profit (Loss)"`), and a rule
    that preserved them would make the index depend on a filing's typography.
    """
    text = _PUNCTUATION.sub(" ", surface.strip().lower().replace("_", " "))
    text = _SPACE.sub(" ", text).strip()
    return re.sub(r"^the\s+", "", text)


@dataclass(frozen=True, slots=True)
class MetricSurfaceResolution:
    """What one surface denotes, and everything §13.5's four refusals need to decide.

    `matched` is the index entry that won the longest-match scan, which is not the same string
    the writer typed: *"adjusted gross margin for the quarter"* matches `adjusted gross
    margin`. A finding quotes both, because *"the surface you wrote resolved through this
    shorter surface"* is the actionable half.
    """

    surface: str
    matched: str | None
    metric_ids: tuple[str, ...]
    declared_ambiguous: bool
    shared_groups: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        return bool(self.metric_ids)

    @property
    def unique_metric_id(self) -> str | None:
        return self.metric_ids[0] if len(self.metric_ids) == 1 else None


class MetricAliasIndex:
    """Surfaces to metric ids, plus the group membership §13.5's third refusal reads.

    Built once per verification and queried per sentence. A class rather than a pair of module
    functions because the index and the group map have to be derived from the same package and
    must not be able to come from two.
    """

    def __init__(
        self,
        entries: Mapping[str, tuple[str, ...]],
        groups: Mapping[str, tuple[str, ...]],
    ) -> None:
        self._entries = dict(entries)
        self._groups = dict(groups)
        # Longest first, then alphabetical, so the scan is total and its order is not the
        # dictionary's insertion order — two packages listing their metrics in different orders
        # must resolve a surface the same way.
        self._by_length = sorted(self._entries, key=lambda key: (-len(key), key))

    @classmethod
    def from_package(cls, package: StoryEvidencePackage) -> "MetricAliasIndex":
        """The package's `metrics[]` section, plus §13.5's eight declared-ambiguous surfaces.

        A metric's `metric_id`, its `label` and every one of its `aliases` are surfaces for it.
        The id is included because a binding may name the id directly and a writer's
        `metric_surface` sometimes is the id; the label is included because §13.5's own worked
        case turns on `gaap_gross_margin`'s label being `"Gross Margin"`.
        """
        entries: dict[str, set[str]] = {}
        groups: dict[str, tuple[str, ...]] = {}
        for metric in package.metrics:
            groups[metric.metric_id] = tuple(metric.mutually_distinct_groups)
            surfaces = (metric.metric_id, metric.label, *metric.aliases)
            for raw in surfaces:
                key = normalise(raw)
                if key:
                    entries.setdefault(key, set()).add(metric.metric_id)
        for surface, metric_ids in DECLARED_AMBIGUOUS.items():
            entries.setdefault(normalise(surface), set()).update(metric_ids)
        return cls({key: tuple(sorted(value)) for key, value in entries.items()}, groups)

    def resolve(self, surface: str) -> MetricSurfaceResolution:
        """Longest match wins; a surface matching nothing resolves to nothing (§13.5)."""
        text = normalise(surface)
        for key in self._by_length:
            if not _contains_phrase(text, key):
                continue
            metric_ids = self._entries[key]
            return MetricSurfaceResolution(
                surface=surface,
                matched=key,
                metric_ids=metric_ids,
                declared_ambiguous=key in DECLARED_AMBIGUOUS,
                shared_groups=self._shared_groups(metric_ids),
            )
        return MetricSurfaceResolution(
            surface=surface, matched=None, metric_ids=(), declared_ambiguous=False,
            shared_groups=(),
        )

    def licenses(self, surface: str, metric_id: str) -> bool:
        """§13.7 Rule A step 3: is this surface a licensed one for this metric?

        Used for a table's `row_label`, which is the filing's own wording for the row the value
        was read off — `"Adjusted Gross Profit (Loss)"`. Resolution is the same scan; the
        question is only whether the metric survives it.
        """
        return metric_id in self.resolve(surface).metric_ids

    def _shared_groups(self, metric_ids: tuple[str, ...]) -> tuple[str, ...]:
        """The `mutually_distinct_group`s more than one of these metrics belongs to.

        §13.5's third refusal. Two metrics that merely share a surface may be a naming
        coincidence; two that share a *declared distinct* group are two things the ontology
        says must never be confused, which is the case a verifier has to stop.
        """
        if len(metric_ids) < 2:
            return ()
        counted: dict[str, int] = {}
        for metric_id in metric_ids:
            for group in self._groups.get(metric_id, ()):
                counted[group] = counted.get(group, 0) + 1
        return tuple(sorted(group for group, count in counted.items() if count > 1))


def _contains_phrase(text: str, phrase: str) -> bool:
    """Whole-word containment. `"revenue" ⊄ "revenues per home"` by accident of prefixing.

    Both sides are already normalised to lowercase words separated by single spaces, so the
    boundary test is a space-padded substring and needs no regex compilation per lookup — this
    runs once per index entry per surface, and the index is a few dozen entries.
    """
    return f" {phrase} " in f" {text} "


__all__ = [
    "DECLARED_AMBIGUOUS",
    "MetricAliasIndex",
    "MetricSurfaceResolution",
    "normalise",
]
