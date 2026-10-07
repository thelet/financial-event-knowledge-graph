"""The factual claim of one detector-defined story, bound once, before any model has spoken.

Responsibility: say which two facts a story is about and which way the quantity moved, as one
object a planner can be shown and a plan can be checked against. Nothing here is computed for
the first time — every value already existed, on `StoryCandidate.signals`, in
`StoryEvidencePackage.facts` and on the `DerivedFact`s code executed. The type exists because
those three producers were three authorities and no consumer held them together
(01-TARGET-ARCHITECTURE §4), so it is deliberately **thin and derived** rather than a fourth
copy of the data.

Boundaries: `story.core.models` and `story.core.lexicon`, and the standard library. No stage
import — no detector, no `detector_config`, no derivation tool
(`test_core_never_imports_a_stage_a_provider_or_the_cli`). That constraint is also the design:
the direction word is *read off the candidate*, so a Research Agent that published the same
`signals` mapping would produce the same spine and `spine_for` could not tell which of the two
wrote it. Nothing here reaches past `signals` into how a detector decided.

**The direction is never the sign of a delta, and that is the whole reason this field is
carried rather than recomputed.** `detector_config.quantity_direction(metric_id, delta)` reads
the metric's stored sign convention: `direct_selling_costs` is negative on 46 of 46 canonical
values, so *"costs rose"* is a **fall** in the stored number. A consumer handed `delta` and no
metric id cannot get that right, and one handed both would be a second implementation of a rule
that already has one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from story.core import lexicon
from story.core.models import (
    CausalLanguage,
    DerivedFact,
    PackagedFact,
    StoryCandidate,
    StoryEvidencePackage,
)

#: The only story type with a spine shape in this phase. `cross_metric_divergence` compares two
#: metrics inside one period and has no `from`/`to` pair to bind; forcing this shape onto it
#: would mean inventing a *from* fact, and 01-TARGET-ARCHITECTURE §4 says not to.
METRIC_MOVE = "metric_move"

#: `detector_config`'s three words, copied because `core/` may not import a stage. A **checked**
#: copy, which is the repository's precedent for a necessary one
#: (`slot_table.TWO_PERIOD_OPERATIONS` against the derivation stage's): a test asserts this set
#: is exactly `{DIRECTION_INCREASE, DIRECTION_DECREASE, DIRECTION_UNCHANGED}`. Three words with
#: an equality test is a different proposition from a hundred-word lexicon with one, which is
#: why `story/core/lexicon.py` exists and this constant does not follow it there.
DIRECTION_WORDS: frozenset[str] = frozenset({"increase", "decrease", "unchanged"})

#: What `direction` holds when the detector published none, which is what `quantity_direction`
#: returning `None` for an `UNVERIFIED` sign convention produces: `candidate_from_move` sets no
#: `direction` key at all and attaches `SIGN_CONVENTION_UNVERIFIED` instead. A spine that cannot
#: state a direction says so in the same shape it states one, rather than guessing or raising.
DIRECTION_UNSTATED = ""


@dataclass(frozen=True, slots=True)
class StorySpine:
    """The two facts, the two periods, the two values, and which way the quantity went."""

    candidate_id: str
    package_id: str
    story_type: str
    subject_entity_id: str
    metric_id: str
    from_fact_id: str
    to_fact_id: str
    from_period: str
    to_period: str
    from_value: float
    to_value: float
    unit: str
    #: `"increase"`, `"decrease"`, `"unchanged"` — the detector's word — or `""`. Never the sign
    #: of a delta; see the module docstring for the cost of getting that wrong.
    direction: str
    #: `False` when the candidate published no direction at all.
    direction_verified: bool
    #: The same fact in `lexicon.CHANGE_DIRECTION`'s boolean vocabulary, so a plan check is a
    #: comparison rather than a translation. `None` when the direction is unstated or
    #: `"unchanged"`, both of which say nothing about which way the stored number went.
    direction_polarity: bool | None
    #: The derivations code executed for this story, in the order the caller executed them.
    #: Ids and not the facts: the facts are already an artifact of the run, and a spine holding
    #: a second copy of them would be the thing this type exists to stop.
    derived_fact_ids: tuple[str, ...]
    #: §11's package-level permission, computed by `planner.causal_language_for` and passed in
    #: — see `spine_for`. `None` when no caller stated it.
    causal_language: CausalLanguage | None

    def __post_init__(self) -> None:
        """Refuse to hold a direction, a verification flag and a polarity that disagree.

        A binding of three producers is only worth more than the three producers if it cannot
        be assembled into a claim none of them made. The three fields are one datum in three
        spellings, so two of them are derivable from the first and the constructor checks that
        the caller derived them rather than trusting it.
        """
        if self.direction_verified != (self.direction in DIRECTION_WORDS):
            raise ValueError(
                f"direction_verified={self.direction_verified} contradicts "
                f"direction={self.direction!r}")
        expected = lexicon.change_direction(self.direction)
        if self.direction_polarity is not expected:
            raise ValueError(
                f"direction_polarity={self.direction_polarity!r} is not the lexicon's answer "
                f"for direction={self.direction!r}, which is {expected!r}")


def spine_for(
    candidate: StoryCandidate,
    package: StoryEvidencePackage,
    derived: Sequence[DerivedFact] = (),
    *,
    causal_language: CausalLanguage | None = None,
) -> StorySpine | None:
    """The spine of one detector-defined story, or `None` when there is nothing to bind.

    Pure: no clock, no filesystem, no ordering assumption beyond `anchor_period_keys` sorting
    ascending, which `StoryCandidate` already enforces at construction.

    **`None` is one answer for two situations, deliberately.** A story type with no spine shape
    and a `metric_move` whose pair cannot be resolved both mean *plan without a spine*, which is
    the caller's only available action either way. A reason code with no consumer would go stale
    before it acquired one; when a caller can act on the difference, the difference gets a type.

    **`causal_language` is an argument and not a computation.** `planner.causal_language_for`
    owns it and lives in the generation stage, which `core/` may not import
    (01-TARGET-ARCHITECTURE §6). `pipeline.py` already computes it for the plan and the
    derivation call, so the composition root passes the one it computed rather than a second
    module re-deriving §11's rule over the same package.
    """
    if candidate.story_type != METRIC_MOVE:
        return None
    # A spine binding a package minted for a different candidate would state a claim about
    # facts the detector never measured, which is the one error this type could make silently.
    if package.candidate_id != candidate.candidate_id:
        return None
    if len(candidate.metric_ids) != 1 or len(candidate.anchor_period_keys) != 2:
        return None

    metric_id = candidate.metric_ids[0]
    from_period, to_period = candidate.anchor_period_keys

    from_fact = _sole_fact(package.facts, metric_id, from_period)
    to_fact = _sole_fact(package.facts, metric_id, to_period)
    if from_fact is None or to_fact is None:
        return None
    # `anchor_observation_ids` holds the *supporting* set — 8 ids, 4 per period, for this
    # candidate — so the pair is resolved against the package, which carries exactly the two
    # canonical readings §6.1 chose. Refusing on anything but one fact per period is what keeps
    # "the spine's from-fact" from meaning "whichever of four matched first".
    if from_fact.unit != to_fact.unit:
        return None

    direction = _direction_of(candidate.signals)
    return StorySpine(
        candidate_id=candidate.candidate_id,
        package_id=package.package_id,
        story_type=candidate.story_type,
        subject_entity_id=candidate.subject_entity_id,
        metric_id=metric_id,
        from_fact_id=from_fact.observation_id,
        to_fact_id=to_fact.observation_id,
        from_period=from_fact.period_key,
        to_period=to_fact.period_key,
        from_value=from_fact.value,
        to_value=to_fact.value,
        unit=from_fact.unit,
        direction=direction,
        direction_verified=direction in DIRECTION_WORDS,
        direction_polarity=lexicon.change_direction(direction),
        derived_fact_ids=tuple(fact.fact_id for fact in derived),
        causal_language=causal_language,
    )


def _sole_fact(
    facts: Sequence[PackagedFact],
    metric_id: str,
    period_key: str,
) -> PackagedFact | None:
    """The one packaged reading of this metric in this period, or `None` for none or several."""
    matches = [f for f in facts if f.metric_id == metric_id and f.period_key == period_key]
    return matches[0] if len(matches) == 1 else None


def _direction_of(signals: Mapping[str, bool | int | float | str]) -> str:
    """The detector's word, or `DIRECTION_UNSTATED`.

    A word this module does not recognise is treated as no word at all rather than carried
    through: `direction_polarity` would be `None` for it anyway, and a spine claiming
    `direction_verified` over a vocabulary it cannot map is the failure pointing the wrong way.
    """
    word = signals.get("direction")
    if isinstance(word, str) and word in DIRECTION_WORDS:
        return word
    return DIRECTION_UNSTATED


__all__ = [
    "DIRECTION_UNSTATED",
    "DIRECTION_WORDS",
    "METRIC_MOVE",
    "StorySpine",
    "spine_for",
]
