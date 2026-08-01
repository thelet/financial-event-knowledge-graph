"""Public contract for ontology candidate scoping.

Reason codes are the point of this module. A scope that answered "these 21 concepts" and
nothing else would be unauditable in exactly the way the ontology's `distinct_from`
declarations exist to prevent: a reader could not tell whether `homes_purchased` is present
because the passage names it or because `homes_sold` dragged it in as the sibling the lane
must be able to reject. Every candidate therefore carries the reasons it is in the set.

All eight lexical reasons are **protected**. A later ranking stage — the hybrid scope and its
`EmbeddingProvider` — may add candidates and may reorder them, and may never remove one of
these. `extraction.contracts.EmbeddingProvider` states the same rule from the other side.

The ninth reason, `semantic_neighbour`, is the one the hybrid scope adds and the one that is
**not** protected. That asymmetry is the whole of stage 9's safety argument: a similarity
score may put a concept in front of a lane, and may never be the reason one is taken away.

No benchmark import belongs here, and an executable test enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass

# The four codes selection already had. Re-declared nowhere: a passage selected because of a
# table row label and a concept scoped because of a table row label are the same finding, and
# two spellings of `table_label` would make a report that joined them silently wrong.
from ...core.models import (
    AMBIGUOUS_ALIAS_SIGNAL as AMBIGUOUS_ALIAS,
    EXACT_ALIAS,
    STABLE_CORE,
    TABLE_LABEL,
)

# `canonical_label` comes from the shared resolver, which is what decides it.
from ...core.concept_resolution import CANONICAL_LABEL

# The three this stage adds. `normalized_alias` is separated from `exact_alias` because the
# difference is a measurement, not a nicety: it says how many candidates only exist because
# filings set quotes and dashes as typography, which is the failure the encoding correction
# uncovered and the number that would silently go to zero if the fold regressed.
NORMALIZED_ALIAS = "normalized_alias"
KNOWN_INSTANCE = "known_instance"
CONFUSION_SIBLING = "confusion_sibling"

# Stage 9's addition: a concept whose rendered ontology entry is near the passage in
# embedding space. Evidence of a kind, and weaker than every reason above it — it names no
# surface in the text, so a reader cannot check it by looking.
SEMANTIC_NEIGHBOUR = "semantic_neighbour"

# The eight the lexical scope produces, and the only ones a later stage may not drop. Stated
# as its own name rather than as `SCOPE_REASONS` because the two stopped being equal the
# moment `semantic_neighbour` arrived, and the rule "protected candidates may not be dropped"
# has to keep meaning what it meant at stage 8. `semantic_neighbour` is deliberately absent:
# STAGE_09 §4.1.
PROTECTED_REASONS = frozenset({
    EXACT_ALIAS, NORMALIZED_ALIAS, CANONICAL_LABEL, AMBIGUOUS_ALIAS, STABLE_CORE,
    KNOWN_INSTANCE, TABLE_LABEL, CONFUSION_SIBLING,
})

# Every reason any scope in this package can produce.
SCOPE_REASONS = PROTECTED_REASONS | frozenset({SEMANTIC_NEIGHBOUR})

# Always in scope, whatever the passage says. A single-registrant corpus has a subject even
# when the sentence in front of the lane never names it, and a lane with no entity in its
# candidate set has no way to say whose homes these are. Named here rather than derived
# because they are the identity of the corpus, not part of its vocabulary; the scope takes
# them as a constructor argument so a second registrant does not need a code change.
STABLE_CORE_CONCEPTS: tuple[str, ...] = ("company", "opendoor", "public_company")


@dataclass(frozen=True)
class ScopedConcept:
    """One concept a lane may consider, and every reason it is allowed to.

    Reasons accumulate rather than compete. A concept named by its canonical label in a table
    row label is `canonical_label` *and* `table_label`, and collapsing that to one "best"
    code would throw away the evidence that distinguishes a row heading from a prose mention
    — which is the distinction the ambiguous-alias entries in `aliases.yaml` turn on.
    """

    concept_id: str
    reasons: tuple[str, ...]
    surfaces: tuple[str, ...] = ()

    @property
    def protected(self) -> bool:
        return any(reason in PROTECTED_REASONS for reason in self.reasons)


@dataclass(frozen=True)
class ConfusionExpansion:
    """A `distinct_from` sibling pulled in, and the concept that pulled it.

    Recorded with its source because "why is `homes_purchased` here" has a different answer
    from "why is `homes_sold` here", and only the first one is the ontology defending itself.
    """

    concept_id: str
    sibling_id: str


@dataclass(frozen=True)
class CandidateScope:
    """Everything a lane may consider for one passage, with the evidence for each candidate.

    `reason_vocabulary` is the set of codes the scope that built this one *could* have
    produced, and it exists so that `counts_by_reason` can key a zero honestly. A lexical
    scope reporting `semantic_neighbour: 0` would be claiming a check that never ran; a hybrid
    scope omitting the key when no neighbour cleared the threshold would hide the one number
    that says the threshold is doing something. The default is the eight protected codes
    because the lexical scope is the one that does not pass an argument.
    """

    concepts: tuple[ScopedConcept, ...]
    expansions: tuple[ConfusionExpansion, ...] = ()
    reason_vocabulary: frozenset[str] = PROTECTED_REASONS

    def __post_init__(self) -> None:
        """`reason_vocabulary` must cover every reason actually present, or the counts lie.

        `counts_by_reason` keys on the vocabulary alone, so a scope built with a narrower one
        than it carries reports candidates that are in `concept_ids` and in `len(scope)` under
        no reason at all — the sum of the counts silently stops matching the set. That is the
        exact failure mode the vocabulary was introduced to prevent, in the other direction,
        so it is refused at construction rather than discovered in a report.
        """
        present = {reason for concept in self.concepts for reason in concept.reasons}
        unknown = present - set(self.reason_vocabulary)
        if unknown:
            raise ValueError(
                f"CandidateScope carries reasons {sorted(unknown)} that its "
                f"reason_vocabulary {sorted(self.reason_vocabulary)} does not declare; "
                f"counts_by_reason() would omit the candidates carrying them")

    @property
    def concept_ids(self) -> tuple[str, ...]:
        return tuple(concept.concept_id for concept in self.concepts)

    @property
    def protected_ids(self) -> tuple[str, ...]:
        return tuple(c.concept_id for c in self.concepts if c.protected)

    def __len__(self) -> int:
        return len(self.concepts)

    def __contains__(self, concept_id: object) -> bool:
        return concept_id in set(self.concept_ids)

    def reasons_for(self, concept_id: str) -> tuple[str, ...]:
        return next((c.reasons for c in self.concepts if c.concept_id == concept_id), ())

    def by_reason(self, reason: str) -> tuple[str, ...]:
        return tuple(c.concept_id for c in self.concepts if reason in c.reasons)

    def counts_by_reason(self) -> dict[str, int]:
        """Candidates carrying each reason. Every reason is keyed, including the zeroes.

        A reason missing from the mapping and a reason that fired nothing are different
        findings, and a report that printed only the non-zero ones could not tell a reader
        which of the codes stopped working. Which codes are keyed is `reason_vocabulary`.
        """
        return {
            reason: len(self.by_reason(reason))
            for reason in sorted(self.reason_vocabulary)
        }
