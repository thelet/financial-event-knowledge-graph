"""§6.6 D3's collapse rule: one quarter's correlated candidates are one story, and it says why.

Responsibility: partition a candidate set into clusters, and record, for every join it made, the
sentence a reader needs to agree with it. It computes no score and decides no order.

**The rule.** Two candidates collapse when they share a *primary anchor period* — the period each
one's claim is about — **and** their metrics are related through `formulas.yaml`
`component_metrics`. §6.6 D3 states it as *"shared anchor quarter plus `formulas.yaml`
`component_metrics` lineage, with `correlated_metric_ids[]`"*; §6.10 restates it as
deduplication's first half.

**Primary anchor, not "any shared period", and that is the difference between a cluster and one
blob.** A `metric_move` anchored `2022Q2 → 2022Q3` shares `2022Q3` with one anchored `2022Q3 →
2022Q4`, which shares `2022Q4` with the next; joining on any overlap chains the whole corpus into
a single component through eight years of adjacent quarters. Each candidate's claim is about its
**latest** anchor, so that is what a group is keyed on: a two-quarter move about 2022Q3, a
four-quarter reversal ending 2022Q3 and a divergence at 2022Q3 are the same event; the move
2022Q3 → 2022Q4 is the next one.

Live, the rule produces exactly the cluster §6.3 and §6.10 describe: **thirteen candidates at
2022Q3** — seven `metric_move`s, five `trend_reversal`s and the `adjusted_gross_margin ↔
gaap_gross_margin` divergence — over seven correlated metrics, joined by the revenue denominator
the four margins share and by the `adjusted_gross_profit`/`contribution_profit`/`adjusted_ebitda`
component chain. `homes_sold` anchors the same quarter and is **not** in it: it appears in no
formula, so nothing in the ontology relates it to a margin, and collapsing it would assert an
economic connection the ontology does not declare.

**What is deliberately not a lineage relation.** `constraints.yaml`'s `mutually_distinct_groups`
relates `contribution_profit` to `adjusted_gross_profit` to `adjusted_ebitda` as *"profit
measures"* — R2 licenses a comparison between them, and D4 treats it as a weakest-kind relation.
It is not `component_metrics` lineage and it is not used here: it would collapse metrics whose
only declared relationship is that the ontology forbids confusing them, which is closer to the
opposite of "these are one story".

**Events are the half of §6.10's rule that has no data.** *"...and on `(passage_id, date)` for
events (the three `executive_change` rows are one story, not three)"* — D9 `leadership_change` is
not implemented at S3, and **all 262 live candidates carry an empty `event_ids`** *(verified
2026-08-04)*. Writing an event collapse now would be a rule with nothing to run on and no test
that was not built from an invented candidate; it is named here as the gap it is, and belongs to
whichever step lands D9.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from ontology.contracts import ConceptRegistry
from ontology.core.values import ConceptCategory
from story.core.models import StoryCandidate
from story.core.series import ComparabilityAuthority, default_authority
from story.stages.ranking.metric_history import MetricHistory
from story.stages.ranking.scoring import primary_period_key


class LineageRelation(str, Enum):
    """How `formulas.yaml` relates two metrics, in the order this module tests for it.

    The four names are `cross_metric_divergence.RelationKind`'s, minus its `distinct_group` —
    they are the ontology's relations, not a detector's vocabulary — and they are restated rather
    than imported because a stage may not import a sibling stage. `test_story_ranking.py` asserts
    the shared members still carry the same values in both modules.
    """

    SAME_METRIC = "same_metric"
    COMPONENT = "component"
    SHARED_COMPONENTS = "shared_components"
    CO_COMPONENT = "co_component"


@dataclass(frozen=True, slots=True)
class GroupingReason:
    """One join, with the ontology fact behind it. This is the *why* a reader asks for.

    Recorded per **merging** pair rather than per qualifying pair: a cluster of thirteen has
    seventy-eight related pairs and twelve joins, and twelve sentences are the argument for the
    cluster while seventy-eight are a restatement of it.
    """

    left_candidate_id: str
    right_candidate_id: str
    anchor_period_key: str
    relation: LineageRelation
    left_metric_id: str
    right_metric_id: str
    #: The metric the relation runs through: the component, the shared components, or the
    #: formula that names both. Empty only for `same_metric`.
    via_metric_ids: tuple[str, ...] = ()

    def sentence(self) -> str:
        """The join, rendered. A template, never free text — §6.4's rule for candidates applies
        to anything a reader might mistake for an editorial claim."""
        if self.relation is LineageRelation.SAME_METRIC:
            relation = f"both are about {self.left_metric_id}"
        elif self.relation is LineageRelation.COMPONENT:
            relation = (
                f"{self.left_metric_id} and {self.right_metric_id} are related because "
                f"{', '.join(self.via_metric_ids)} is a component of the other's formula"
            )
        elif self.relation is LineageRelation.SHARED_COMPONENTS:
            relation = (
                f"{self.left_metric_id} and {self.right_metric_id} share the component "
                f"{', '.join(self.via_metric_ids)}"
            )
        else:
            relation = (
                f"{self.left_metric_id} and {self.right_metric_id} are both components of "
                f"{', '.join(self.via_metric_ids)}"
            )
        return (
            f"{self.left_candidate_id} and {self.right_candidate_id} both anchor "
            f"{self.anchor_period_key}, and {relation} (formulas.yaml)"
        )


@dataclass(frozen=True, slots=True)
class CandidateCluster:
    """Candidates §6.6 D3 collapses into one story, and the joins that put them there.

    No representative is chosen here. Election is the ranker's, because it is by score, and
    §6.10 requires deduplication to run **before** scoring so that a score never decides what a
    grouping is.
    """

    anchor_period_key: str
    member_ids: tuple[str, ...]
    #: §6.6 D3's `correlated_metric_ids[]`: every metric the collapsed candidates are about.
    correlated_metric_ids: tuple[str, ...]
    reasons: tuple[GroupingReason, ...] = ()

    @property
    def is_collapsed(self) -> bool:
        return len(self.member_ids) > 1

    def explain(self) -> tuple[str, ...]:
        """One sentence per join, so the cluster argues for itself."""
        return tuple(reason.sentence() for reason in self.reasons)


def components_in_force(
    registry: ConceptRegistry | None, as_of: str | None
) -> Mapping[str, tuple[str, ...]]:
    """Every metric that declares a formula, mapped to that formula's `component_metrics`.

    Resolved **at a date**, through the registry's own `formula_for`, because
    `adjusted_gross_profit` is `_v1` through 2021Q4 and `_v2` from 2022Q1 and a lineage taken from
    "the latest formula" would describe a 2021 collapse with a 2022 definition. The candidate set
    of one cluster shares one anchor period, so one resolution serves the whole cluster.
    """
    if registry is None:
        return {}
    declared = {
        formula.metric_id
        for formula in registry.by_category(ConceptCategory.METRIC_FORMULA)
        if getattr(formula, "metric_id", None)
    }
    resolved: dict[str, tuple[str, ...]] = {}
    for metric_id in sorted(declared):
        formula = registry.formula_for(metric_id, as_of)
        if formula is not None:
            resolved[metric_id] = tuple(formula.component_metrics)
    return resolved


def lineage_between(
    left: str, right: str, components: Mapping[str, tuple[str, ...]]
) -> tuple[LineageRelation, tuple[str, ...]] | None:
    """The relation `formulas.yaml` declares between two metrics, or `None` for none.

    Tested strongest first — identity, then *"one is the other's component"*, then a shared
    component, then a shared parent — so the sentence a group renders names the closest
    relationship rather than the first one that happens to hold.
    """
    if left == right:
        return LineageRelation.SAME_METRIC, ()
    left_components = set(components.get(left, ()))
    right_components = set(components.get(right, ()))
    if right in left_components:
        return LineageRelation.COMPONENT, (right,)
    if left in right_components:
        return LineageRelation.COMPONENT, (left,)
    shared = tuple(sorted(left_components & right_components))
    if shared:
        return LineageRelation.SHARED_COMPONENTS, shared
    parents = tuple(
        sorted(
            parent
            for parent, members in components.items()
            if left in members and right in members
        )
    )
    if parents:
        return LineageRelation.CO_COMPONENT, parents
    return None


def deduplicate(
    candidates: Sequence[StoryCandidate],
    history: MetricHistory,
    *,
    authority: ComparabilityAuthority | None = None,
) -> tuple[CandidateCluster, ...]:
    """§6.6 D3's clusters over a candidate set, deterministic and order-independent.

    Union-find over candidates sorted by `candidate_id`, so the joins recorded — and therefore the
    sentences a group renders — do not depend on the order the detectors happened to run in. A
    pair joins when it shares a primary anchor period and any of its metrics are lineage-related;
    the reason kept is the one for the pair that actually merged two components.
    """
    resolved = default_authority() if authority is None else authority
    by_anchor: dict[str, list[StoryCandidate]] = {}
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        anchor = primary_period_key(candidate, history)
        if anchor is not None:
            by_anchor.setdefault(anchor, []).append(candidate)

    clusters: list[CandidateCluster] = []
    for anchor in sorted(by_anchor):
        members = by_anchor[anchor]
        components = components_in_force(resolved.registry, _as_of(members, history, anchor))
        clusters.extend(_cluster(anchor, members, components))
    return tuple(clusters)


def _as_of(
    members: Sequence[StoryCandidate], history: MetricHistory, anchor: str
) -> str | None:
    """The date a formula version is resolved at: the anchor period's own end or instant."""
    for candidate in members:
        for metric_id in candidate.metric_ids:
            slot = history.slot(metric_id, anchor)
            if slot is not None and slot.anchor_date is not None:
                return slot.anchor_date
    return None


def _cluster(
    anchor: str,
    members: Sequence[StoryCandidate],
    components: Mapping[str, tuple[str, ...]],
) -> list[CandidateCluster]:
    """Union-find over one anchor period's candidates, keeping the merging joins."""
    parent = {candidate.candidate_id: candidate.candidate_id for candidate in members}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    reasons: list[GroupingReason] = []
    for position, left in enumerate(members):
        for right in members[position + 1:]:
            relation = _relate(left, right, components)
            if relation is None:
                continue
            left_root, right_root = find(left.candidate_id), find(right.candidate_id)
            if left_root == right_root:
                continue
            parent[right_root] = left_root
            kind, left_metric, right_metric, via = relation
            reasons.append(
                GroupingReason(
                    left_candidate_id=left.candidate_id,
                    right_candidate_id=right.candidate_id,
                    anchor_period_key=anchor,
                    relation=kind,
                    left_metric_id=left_metric,
                    right_metric_id=right_metric,
                    via_metric_ids=via,
                )
            )

    grouped: dict[str, list[StoryCandidate]] = {}
    for candidate in members:
        grouped.setdefault(find(candidate.candidate_id), []).append(candidate)

    clusters: list[CandidateCluster] = []
    for root in sorted(grouped):
        block = grouped[root]
        ids = frozenset(candidate.candidate_id for candidate in block)
        clusters.append(
            CandidateCluster(
                anchor_period_key=anchor,
                member_ids=tuple(sorted(ids)),
                correlated_metric_ids=tuple(
                    sorted({metric for item in block for metric in item.metric_ids})
                ),
                reasons=tuple(
                    reason
                    for reason in reasons
                    if reason.left_candidate_id in ids and reason.right_candidate_id in ids
                ),
            )
        )
    return clusters


def _relate(
    left: StoryCandidate,
    right: StoryCandidate,
    components: Mapping[str, tuple[str, ...]],
) -> tuple[LineageRelation, str, str, tuple[str, ...]] | None:
    """The closest lineage relation between any metric of one candidate and any of the other's.

    Any-to-any because `cross_metric_divergence` is about a *pair*: the AGM↔GGM divergence is the
    same story as the AGM move, and a rule reading only the first metric id would miss half of the
    2022Q3 cluster.
    """
    best: tuple[LineageRelation, str, str, tuple[str, ...]] | None = None
    order = list(LineageRelation)
    for left_metric in left.metric_ids:
        for right_metric in right.metric_ids:
            found = lineage_between(left_metric, right_metric, components)
            if found is None:
                continue
            kind, via = found
            if best is None or order.index(kind) < order.index(best[0]):
                best = (kind, left_metric, right_metric, via)
    return best


__all__ = [
    "CandidateCluster",
    "GroupingReason",
    "LineageRelation",
    "components_in_force",
    "deduplicate",
    "lineage_between",
]
