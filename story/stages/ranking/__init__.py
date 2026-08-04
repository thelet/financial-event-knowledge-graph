"""S4 — §6.10's ranking and deduplication. Deterministic, decomposable, and with no model in it.

Four modules, each named for what it holds:

    metric_history.py     what the score is measured *against* — the metric's own delta
                          distribution per period shape, and its per-slot corroboration
    scoring.py            §6.10's eight terms and the eight weights, decomposed
    deduplication.py      §6.6 D3's collapse rule, and the sentence explaining every join
    candidate_ranking.py  the entry point: cluster, score, order, and the audience partition

**No LLM is the ranker and no LLM is a tie-breaker** (§6.10). Nothing here imports a provider,
opens a connection or reads a file; the canonical points arrive as an argument, exactly as they do
at a detector. Two runs over one graph produce one order, one set of totals and one set of
`dedup_group`s.

**The stage does not mutate a candidate.** §6.4 keeps `materiality`, `novelty`,
`evidence_quality`, `ambiguity_penalty` and `repetition_penalty` off `StoryCandidate` because *"a
candidate that carried its own score would let a detector rank itself"*. The output is
`CandidateScore` — `story/core/models.py`'s, not a second one declared here — carried on a
`RankedCandidate` row that *references* the candidate.

`ScoreBreakdown.suppressed` and `LineageRelation` are reached through their modules rather than
re-exported flat, for the reason `story/stages/detection/__init__.py` states about detector ids: a
package-level alias for a name that means something precise in one module is a second name for it.
"""

from __future__ import annotations

from story.stages.ranking.candidate_ranking import (
    AUDIENCE_ORDER,
    RankedCandidate,
    RankingResult,
    ordering_key,
    rank_candidates,
)
from story.stages.ranking.deduplication import (
    CandidateCluster,
    GroupingReason,
    LineageRelation,
    deduplicate,
    lineage_between,
)
from story.stages.ranking.metric_history import (
    MIN_DELTA_POPULATION,
    DeltaDistribution,
    MetricHistory,
    SlotFacts,
    build_metric_history,
)
from story.stages.ranking.scoring import (
    SCORE_PRECISION,
    WEIGHTS,
    AcceptedPost,
    ScoreBreakdown,
    clip,
    score_candidate,
)

__all__ = [
    "AUDIENCE_ORDER",
    "MIN_DELTA_POPULATION",
    "SCORE_PRECISION",
    "WEIGHTS",
    "AcceptedPost",
    "CandidateCluster",
    "DeltaDistribution",
    "GroupingReason",
    "LineageRelation",
    "MetricHistory",
    "RankedCandidate",
    "RankingResult",
    "ScoreBreakdown",
    "SlotFacts",
    "clip",
    "deduplicate",
    "lineage_between",
    "build_metric_history",
    "ordering_key",
    "rank_candidates",
    "score_candidate",
]
