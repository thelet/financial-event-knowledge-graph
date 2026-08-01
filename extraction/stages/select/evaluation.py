"""Scoring a selection policy against the benchmark.

Strictly after the fact. Selection itself never reads a gold annotation — a policy that
consulted the answers would be measuring itself against its own output — and an executable
test enforces that the runtime selector imports nothing from `benchmarks/`.

What is measured, and why each one is separate:

* **required-concept recall** — of the metrics the gold claims name, how many did selection
  put in front of a lane. The headline number: a concept missed here cannot be recovered
  downstream, so this bounds the whole pipeline.
* **known-instance recall** — of the specific (passage, metric) pairs the gold names, how
  many survived. Stricter than concept recall: a policy can reach every concept somewhere in
  the corpus while missing the exact passage a claim must cite.
* **candidate counts by lane and document type** — the cost side.
* **missed gold concepts** — named, not counted. A number says a policy is 94% good; a list
  says which metric it cannot see.
* **ambiguity preservation** — whether passages whose gold answer is an `AMBIGUOUS_ALIAS`
  abstention still reach a lane. A policy that filters them out looks precise and has
  destroyed the abstention the benchmark is scoring.
* **false-positive candidate density** — selected passages carrying no gold claim, as a
  fraction. Not an error on its own: candidate selection is recall-oriented and a passage
  with no gold claim may still deserve reading. It is the number that tells you what a
  recall gain cost.
* **exclusions by reason code** — the audit trail, so a recall loss can be attributed to a
  specific rule rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.models import CandidatePassage


@dataclass(frozen=True)
class GoldCase:
    """The benchmark projected onto what selection can be judged against."""

    case_id: str
    passage_id: str
    document_id: str
    document_type: str
    lane: str
    metric_ids: frozenset[str]
    expects_ambiguity_abstention: bool = False


@dataclass
class SelectionEvaluation:
    required_concept_recall: float = 0.0
    known_instance_recall: float = 0.0
    ambiguity_preservation: float = 0.0
    false_positive_density: float = 0.0
    candidates_by_lane: dict[str, int] = field(default_factory=dict)
    candidates_by_document_type: dict[str, int] = field(default_factory=dict)
    missed_concepts: tuple[str, ...] = ()
    missed_instances: tuple[tuple[str, str], ...] = ()
    exclusions_by_reason: dict[str, int] = field(default_factory=dict)
    gold_concepts: int = 0
    gold_instances: int = 0

    def as_report(self) -> str:
        lines = [
            f"required-concept recall   {self.required_concept_recall:.3f} "
            f"({self.gold_concepts} gold concepts)",
            f"known-instance recall     {self.known_instance_recall:.3f} "
            f"({self.gold_instances} gold instances)",
            f"ambiguity preservation    {self.ambiguity_preservation:.3f}",
            f"false-positive density    {self.false_positive_density:.3f}",
            "candidates by lane        " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.candidates_by_lane.items())),
        ]
        if self.missed_concepts:
            lines.append("missed concepts           " + ", ".join(self.missed_concepts))
        if self.missed_instances:
            lines.append(f"missed instances          {len(self.missed_instances)}")
        return "\n".join(lines)


def evaluate(
    candidates: list[CandidatePassage], gold: list[GoldCase]
) -> SelectionEvaluation:
    selected = [c for c in candidates if c.selected]
    selected_ids = {c.passage_id for c in selected}
    # A passage reaches a lane if *any* lane selected it. Gold cases name the lane they
    # expect, but a passage read by the other lane is not a selection failure — it is a
    # routing question, and conflating the two would hide which one is wrong.
    by_passage: dict[str, list[CandidatePassage]] = {}
    for candidate in selected:
        by_passage.setdefault(candidate.passage_id, []).append(candidate)

    gold_concepts = {m for case in gold for m in case.metric_ids}
    reached_concepts = {
        m for case in gold if case.passage_id in selected_ids for m in case.metric_ids
    }
    missed_concepts = tuple(sorted(gold_concepts - reached_concepts))

    gold_instances = {
        (case.passage_id, metric) for case in gold for metric in case.metric_ids
    }
    reached_instances = {
        (passage_id, metric)
        for passage_id, metric in gold_instances
        if passage_id in selected_ids
    }
    missed_instances = tuple(sorted(gold_instances - reached_instances))

    ambiguity_cases = [c for c in gold if c.expects_ambiguity_abstention]
    preserved = [c for c in ambiguity_cases if c.passage_id in selected_ids]

    gold_passages = {case.passage_id for case in gold}
    false_positives = [p for p in selected_ids if p not in gold_passages]

    by_lane: dict[str, int] = {}
    by_document_type: dict[str, int] = {}
    for candidate in selected:
        by_lane[candidate.lane] = by_lane.get(candidate.lane, 0) + 1
        by_document_type[candidate.document_type] = (
            by_document_type.get(candidate.document_type, 0) + 1)

    exclusions: dict[str, int] = {}
    for candidate in candidates:
        if not candidate.selected:
            exclusions[candidate.reason] = exclusions.get(candidate.reason, 0) + 1

    return SelectionEvaluation(
        required_concept_recall=_ratio(len(reached_concepts), len(gold_concepts)),
        known_instance_recall=_ratio(len(reached_instances), len(gold_instances)),
        ambiguity_preservation=_ratio(len(preserved), len(ambiguity_cases)),
        false_positive_density=_ratio(len(false_positives), len(selected_ids)),
        candidates_by_lane=by_lane,
        candidates_by_document_type=by_document_type,
        missed_concepts=missed_concepts,
        missed_instances=missed_instances,
        exclusions_by_reason=exclusions,
        gold_concepts=len(gold_concepts),
        gold_instances=len(gold_instances),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator
