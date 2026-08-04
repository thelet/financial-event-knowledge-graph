"""S4's entry point: candidates in, one deterministic order out. No model, ever.

Responsibility: compose the three halves — cluster (`deduplication.py`), score (`scoring.py`)
against the run's own history (`metric_history.py`), order — and own the two rules that belong to
neither: the **tie-break** and the **audience partition**. It reads no graph, holds no state
between calls, and caches nothing.

**The tie-break, declared.** Rows sort by

    (audience rank, −total, candidate_id)

`candidate_id` is last and is unique by §6.11, so the key is a total order and a shuffled input
produces an identical output. `total` is compared at `SCORE_PRECISION` because it is *stored*
rounded to it: eight weighted floats leave residues at the seventeenth digit, and two candidates
that differ only there are a tie, not an order.

**Audience is the first key, which is stronger than §6.10 asks and deliberately so.** §6.10
forbids an internal candidate outranking an external one *for post generation*; sorting on it
makes that true of the whole ranking, so no consumer can reintroduce the failure by reading the
list instead of the accessor. `for_post_selection` narrows further, to external group
representatives. **Every one of the 262 live candidates is `external`** — D15, D16 and D17 are
not implemented — so this partition is exercised by a synthetic internal candidate today. It is
built now because the guarantee has to exist before the detectors that need it, not after.

**Ranks are over the deduplicated set, and a suppressed member is not hidden.** A cluster's
representative takes the next rank; every other member of that cluster is scored in full, keeps
its place in the ordering, and carries **its representative's rank** with `dedup_group` naming it.
So `dedup_group == candidate_id` reads *"this candidate is its own story"* and anything else reads
*"this is the same story as that one"* — which is exactly what `CandidateScore.dedup_group`'s
docstring asks for, *"recorded so a reader can see what a rank suppressed."*

**Election runs after scoring, grouping runs before it.** §6.10 requires deduplication to precede
scoring so that no score decides what a group *is*; which member speaks for a group is a different
question, and answering it by anything other than the score would mean the top-ranked candidate of
a cluster could be suppressed by a worse one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from story.core.models import Audience, CandidateScore, StoryCandidate
from story.core.series import ComparabilityAuthority
from story.stages.ranking.deduplication import CandidateCluster, deduplicate
from story.stages.ranking.metric_history import MetricHistory
from story.stages.ranking.scoring import (
    SCORE_PRECISION,
    AcceptedPost,
    prior_candidate_counts,
    score_candidate,
)

#: The audience partition, as a sort key. External first, and the mapping is exhaustive over
#: `Audience` so a third member could not silently sort into the middle.
AUDIENCE_ORDER: Mapping[Audience, int] = {Audience.EXTERNAL: 0, Audience.INTERNAL: 1}


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """One row of the ranking. The candidate is *referenced*, never modified.

    §6.4 is explicit that a candidate holding its own score would let a detector rank itself, so
    the score lives here beside the candidate and not on it. `suppressed` carries the reason each
    absent component is absent — a component missing from `score.components` was not computable,
    and that is a different finding from a contribution of zero.
    """

    candidate: StoryCandidate
    score: CandidateScore
    cluster: CandidateCluster
    suppressed: Mapping[str, str]

    @property
    def is_representative(self) -> bool:
        return self.score.dedup_group == self.candidate.candidate_id

    @property
    def audience(self) -> Audience:
        return self.candidate.audience


@dataclass(frozen=True, slots=True)
class RankingResult:
    """Every candidate, ordered, plus the clusters the order was assigned over."""

    ranked: tuple[RankedCandidate, ...] = ()
    clusters: tuple[CandidateCluster, ...] = ()

    @property
    def representatives(self) -> tuple[RankedCandidate, ...]:
        """One row per story, in rank order — the deduplicated set the ranks are over."""
        return tuple(row for row in self.ranked if row.is_representative)

    @property
    def for_post_selection(self) -> tuple[RankedCandidate, ...]:
        """§6.10's guarantee, as the accessor a caller should reach for: external stories only.

        Internal candidates are `fact_conflict`, `coverage_gap` and `formula_closure_break` —
        data-quality findings whose audience is the run's own operators. They are ranked and
        returned by `ranked`; they are never offered to the writer.
        """
        return tuple(row for row in self.representatives if row.audience is Audience.EXTERNAL)

    def row(self, candidate_id: str) -> RankedCandidate | None:
        return next(
            (row for row in self.ranked if row.candidate.candidate_id == candidate_id), None
        )


def ordering_key(row: RankedCandidate) -> tuple[int, float, str]:
    """The declared tie-break (see the module docstring), as one function so it has one home."""
    return (
        AUDIENCE_ORDER[row.candidate.audience],
        -round(row.score.total, SCORE_PRECISION),
        row.candidate.candidate_id,
    )


def rank_candidates(
    candidates: Sequence[StoryCandidate],
    history: MetricHistory,
    *,
    authority: ComparabilityAuthority | None = None,
    accepted_history: Sequence[AcceptedPost] = (),
) -> RankingResult:
    """§6.10, end to end, over one run's candidates.

    `accepted_history` is §6.10's repetition input and defaults to empty because **V1 has
    published nothing**: there is no store of accepted posts in this repository, and the term is
    therefore exactly zero on every candidate today. It is a parameter rather than a lookup so
    that the day such a store exists, it is passed in by the caller that owns it.
    """
    clusters = tuple(
        cluster
        for audience in sorted(AUDIENCE_ORDER, key=lambda item: AUDIENCE_ORDER[item])
        # Clustered per audience: an internal data-quality finding and an external story about
        # the same metric and quarter are not one story, and a cluster spanning the boundary
        # would let a post suppress the report that says the post's numbers are in doubt.
        for cluster in deduplicate(
            [item for item in candidates if item.audience is audience],
            history,
            authority=authority,
        )
    )
    cluster_of = {
        member_id: cluster for cluster in clusters for member_id in cluster.member_ids
    }
    priors = prior_candidate_counts(candidates, history)

    scored = [
        (
            candidate,
            score_candidate(
                candidate,
                history,
                prior_count=priors.get(candidate.candidate_id, 0),
                accepted_history=accepted_history,
            ),
        )
        for candidate in candidates
    ]
    # Ordered before ranks and representatives exist, on the two keys that do not depend on
    # either. `dedup_group` and `rank` are filled in one pass over this order below.
    ordered = sorted(
        scored,
        key=lambda pair: (
            AUDIENCE_ORDER[pair[0].audience],
            -round(pair[1].total, SCORE_PRECISION),
            pair[0].candidate_id,
        ),
    )

    representative_of: dict[str, str] = {}
    rank_of: dict[str, int] = {}
    next_rank = 1
    for candidate, _breakdown in ordered:
        cluster = cluster_of.get(candidate.candidate_id)
        member_ids = cluster.member_ids if cluster is not None else (candidate.candidate_id,)
        root = next((member for member in member_ids if member in representative_of), None)
        if root is None:
            representative_of[candidate.candidate_id] = candidate.candidate_id
            rank_of[candidate.candidate_id] = next_rank
            next_rank += 1
        else:
            representative_of[candidate.candidate_id] = representative_of[root]
            rank_of[candidate.candidate_id] = rank_of[representative_of[root]]

    ranked = tuple(
        RankedCandidate(
            candidate=candidate,
            score=CandidateScore(
                candidate_id=candidate.candidate_id,
                total=breakdown.total,
                components=dict(breakdown.components),
                rank=rank_of[candidate.candidate_id],
                dedup_group=representative_of[candidate.candidate_id],
            ),
            cluster=cluster_of.get(
                candidate.candidate_id,
                CandidateCluster(
                    anchor_period_key="",
                    member_ids=(candidate.candidate_id,),
                    correlated_metric_ids=tuple(candidate.metric_ids),
                ),
            ),
            suppressed=dict(breakdown.suppressed),
        )
        for candidate, breakdown in ordered
    )
    return RankingResult(ranked=ranked, clusters=clusters)


__all__ = [
    "AUDIENCE_ORDER",
    "RankedCandidate",
    "RankingResult",
    "ordering_key",
    "rank_candidates",
]
