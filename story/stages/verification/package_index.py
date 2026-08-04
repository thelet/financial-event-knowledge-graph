"""The lookups every §13 check needs over one package, derived once instead of per sentence.

Responsibility: turning the sixteen bounded sections of a `StoryEvidencePackage` into the four
questions the checks actually ask — *does this id resolve*, *which passage is this*, *is this
`(passage, column_label)` pair ambiguous*, and *was this counter-evidence associated at
document grain or at passage grain*. No verdicts, no findings.

**Two of these are measurements the plan makes and the verifier must re-make.**

* §13.7.1's column rule is stated over the package, not over the corpus: *"179 of 485
  `(passage_id, column_label)` pairs are ambiguous, covering 1,656 of 2,704 observations
  (61.3%)"*. §13.12 says the same about conflicts — *"derive `conflicted_slots` from the
  package at verification time, never from a constant; a hard-coded count would pass a draft
  verified against a different run."* So both are computed here from the rows in hand.
* Counter-evidence grain is derived **structurally**, from whether a counter-evidence passage
  is a passage some packaged fact was read from. `story/stages/packaging/counter_evidence.py`
  reaches the same answer and discloses it as one of two warning codes, but a stage may not
  import another stage (`test_no_stage_imports_another_stage`), and the structural derivation
  needs no agreement about a string. The two are independent expressions of one fact, which is
  the better arrangement: a packaging bug that mislabels the basis is then visible as a
  disagreement rather than inherited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from story.core.models import (
    Conflict,
    PackagedDocument,
    PackagedEvent,
    PackagedFact,
    PackagedPassage,
    StoryEvidencePackage,
)

#: §13.7.1 escape hatch 2. §6.6's D15 detects a year-only column and §6.1 step 4 resolves it by
#: document majority; a fact carrying the classification may proceed with the minority reading
#: rendered beside it. Named here because it is the one ambiguity code this stage branches on.
YEAR_ONLY_COLUMN_AMBIGUITY = "year_only_column_ambiguity"

#: What a table observation's `source_lane` reads in this corpus. §13.7's two rules are split
#: on it: 2,690 table observations have a bare-numeral `quoted_text` of median 4 characters,
#: and the 14 narrative ones quote whole sentences.
TABLE_LANE = "normalized_table"


@dataclass(frozen=True, slots=True)
class ColumnSlot:
    """One `(passage_id, column_label)` pair and every `period_key` the package reads under it.

    `period_keys` is sorted so a finding's `observed` is stable across two builds of one
    package — the ambiguity is the *set*, and a set rendered in hash order would make an
    otherwise byte-identical rejection two different strings.
    """

    passage_id: str
    column_label: str
    period_keys: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.period_keys) > 1


class PackageIndex:
    """Every id in one package, resolvable in one place.

    Deliberately not a set of module functions over a package: the column-ambiguity map is
    O(facts) to build and is consulted once per citation, and a function that rebuilt it per
    call would make §13.7.1's check quadratic in a draft's citations for no reason.
    """

    def __init__(self, package: StoryEvidencePackage) -> None:
        self.package = package
        self.facts: Mapping[str, PackagedFact] = {
            fact.observation_id: fact for fact in package.facts}
        self.documents: Mapping[str, PackagedDocument] = {
            document.document_id: document for document in package.documents}
        self.events: Mapping[str, PackagedEvent] = {
            event.event_id: event for event in package.events}
        self.passages: Mapping[str, PackagedPassage] = {
            passage.passage_id: passage
            for section in (package.primary_passages, package.context_passages,
                            package.explanatory_passages, package.counter_evidence)
            for passage in section
        }
        self.evidence_source_ids: frozenset[str] = frozenset(
            source.evidence_source_id for source in package.evidence_sources)
        self.conflicts: Mapping[str, Conflict] = {
            conflict.slot: conflict for conflict in package.conflicts}
        self._columns = _column_slots(package)
        self._fact_passages = frozenset(
            fact.passage_id for fact in package.facts if fact.passage_id)
        self._counter_evidence_ids = frozenset(
            passage.passage_id for passage in package.counter_evidence)

    # -- resolution ---------------------------------------------------------------------

    def fact(self, fact_id: str) -> PackagedFact | None:
        return self.facts.get(fact_id)

    def passage(self, passage_id: str) -> PackagedPassage | None:
        return self.passages.get(passage_id)

    def is_table_fact(self, fact: PackagedFact) -> bool:
        return fact.source_lane == TABLE_LANE

    def facts_for_metric(self, metric_id: str) -> tuple[PackagedFact, ...]:
        return tuple(fact for fact in self.package.facts if fact.metric_id == metric_id)

    def slot_of(self, fact: PackagedFact) -> str:
        """§6.1's fact-slot key, rendered as `conflicts[].slot` renders it."""
        return f"{fact.metric_id}|{fact.period_key}"

    # -- §13.7.1 ------------------------------------------------------------------------

    def column_slot(self, fact: PackagedFact) -> ColumnSlot | None:
        """The `(passage_id, column_label)` pair this fact was read under, or `None`.

        `None` when the fact carries no `column_label` or no `passage_id` — a narrative
        observation carries neither, and C2 says an absent property is the only reading Neo4j
        can produce. The caller decides what that means; Rule A applies only to table facts.
        """
        if not (fact.passage_id and fact.column_label):
            return None
        return self._columns.get((fact.passage_id, fact.column_label))

    def distinguishing_siblings(self, fact: PackagedFact) -> tuple[str, ...]:
        """§13.7.1 hatch 1: labels in this fact's passage that resolve to one `period_key`.

        Measured, this rescues 9 of the 179 ambiguous pairs and 22 of 1,656 observations
        (1.3%), so the return is usually empty and the hatch is kept because it is free and
        correct where it applies — not because it carries load.
        """
        if not fact.passage_id:
            return ()
        return tuple(sorted(
            slot.column_label
            for (passage_id, _), slot in self._columns.items()
            if passage_id == fact.passage_id and not slot.ambiguous
        ))

    def column_ambiguity_classified(self, fact: PackagedFact) -> bool:
        """§13.7.1 hatch 2: D15 classified this slot and §6.1 step 4 resolved it."""
        return YEAR_ONLY_COLUMN_AMBIGUITY in fact.ambiguity_codes

    # -- counter-evidence grain ---------------------------------------------------------

    def is_counter_evidence(self, passage_id: str) -> bool:
        return passage_id in self._counter_evidence_ids

    def is_document_grain_counter_evidence(self, passage_id: str) -> bool:
        """A counter-evidence passage that evidences none of the package's own facts.

        §10's counter-evidence join is at **document** grain — measured, the passage-grain join
        returns zero rows for `adjusted_ebitda` 2022Q3 — so the ordinary counter-evidence row
        is a neighbouring table of the same filing. Presenting one as support for a cited cell
        is §13.14's attack shape, and this is the predicate that refuses it.
        """
        return passage_id in self._counter_evidence_ids and passage_id not in self._fact_passages


def _column_slots(package: StoryEvidencePackage) -> Mapping[tuple[str, str], ColumnSlot]:
    """Every `(passage_id, column_label)` in the package, with the period keys read under it."""
    gathered: dict[tuple[str, str], set[str]] = {}
    for fact in package.facts:
        if not (fact.passage_id and fact.column_label):
            continue
        gathered.setdefault((fact.passage_id, fact.column_label), set()).add(fact.period_key)
    return {
        key: ColumnSlot(passage_id=key[0], column_label=key[1], period_keys=tuple(sorted(value)))
        for key, value in gathered.items()
    }


__all__ = [
    "TABLE_LANE",
    "YEAR_ONLY_COLUMN_AMBIGUITY",
    "ColumnSlot",
    "PackageIndex",
]
