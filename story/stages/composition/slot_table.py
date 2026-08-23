"""What each trusted row may legally be written as, under a handle a sentence can carry.

Responsibility: one deterministic function of `(package, derived_facts, passages)` that assigns
short, prompt-legible handles and states, per row, exactly what a template may ask it for.
Nothing here parses a template, substitutes anything or refuses anything — it answers *"what
may be written for this row"*, and `compile.py` decides what a template naming something else
costs.

**Why this is the seam** (§4.1 of `docs/2026-08-23-deterministic-draft-compiler/`). The table is
a pure function of trusted rows and knows nothing about where they came from, so a Research
Agent that selects or computes facts adds rows and changes no prompt rule, no schema field and
no compiler branch. It is also the surface the writer's prompt prints, which is why the handles
are `F1`/`D1`/`P1` and not the real ids: `fact:derived:compare-levels:opendoor:adjusted-gross-
margin-gaap-gross-margin:2022Q3:5f8f78fad963` inside a sentence, retyped by a 9B model, is the
option §8 rejects.

**The strings themselves are `story.core.renderings`', not this module's.** A second emitter is
what that module exists to end; this one decides *which* of its answers a row is allowed to be
asked for, and under what name.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from story.core.models import (
    DerivationOperation,
    DerivedFact,
    EvidenceScopeFact,
    PackagedFact,
    PackagedPassage,
    StoryEvidencePackage,
)
from story.core.renderings import (
    direction_phrase,
    legal_renderings,
    metric_surfaces,
    period_surface_of_fact,
)
from story.stages.composition.public import SlotKind, SlotRow

#: The key a row holds its *value* under, and the field a `{{F3}}` slot asks for. The empty
#: string because `{{F3}}` names no field: it asks the row for the one thing it is.
VALUE_FIELD = ""

#: Every field name the grammar knows, for any row. A `{{F1.colour}}` is `unknown_slot_field`
#: against this set; a field in it that a particular row does not offer is
#: `field_not_offered_by_row`, which is a different fault and gets a different code.
SLOT_FIELDS: frozenset[str] = frozenset({
    "metric", "period", "direction", "from_metric", "to_metric", "from_period", "to_period"})

#: The operations that compute across two windows, and the two that compare within one.
#:
#: **A checked copy of `story/stages/derivation/offers.py`'s two sets, not an import**, because
#: a stage may not import a sibling stage and this stage may import `story.core.*` only. The
#: repository's precedent for a copy that must not drift is `prompts.WARNING_QUALIFIER_PHRASES`
#: and `renderings.NON_NUMERIC_DERIVED_UNITS`: the copy is asserted equal to the original by a
#: test rather than kept equal by discipline —
#: `tests/story/test_story_composition.py::test_the_period_split_agrees_with_the_derivation_stage`.
#:
#: The split decides which period slots a derived row offers, and it is a property of the
#: *operation* rather than of the row's two period keys. Reading it off `from_period !=
#: to_period` would have been an inference from data the offer set already constrains, and it
#: would answer differently for a hand-built row the offer set would never have granted.
TWO_PERIOD_OPERATIONS: frozenset[DerivationOperation] = frozenset({
    DerivationOperation.ABSOLUTE_CHANGE,
    DerivationOperation.PERCENTAGE_CHANGE,
    DerivationOperation.PERCENTAGE_POINT_CHANGE,
    DerivationOperation.CROSSED_ZERO,
    DerivationOperation.TREND_DIRECTION,
})

SAME_PERIOD_OPERATIONS: frozenset[DerivationOperation] = frozenset({
    DerivationOperation.COMPARE_LEVELS,
    DerivationOperation.RATIO,
})


def slot_table(
    package: StoryEvidencePackage,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    passages: Sequence[PackagedPassage] = (),
) -> tuple[SlotRow, ...]:
    """`F1..Fn`, `D1..Dm`, `P1..Pk` — every row a template may name, in one ordered tuple.

    Handles are one-based, gapless and **positional**: `F` over `package.facts` in package
    order, `D` over this run's derived facts in the order the derivation stage returned them,
    `P` over the passages the caller was handed (`evidence_slice.passages_backing_facts`, in
    that function's order). The package is already ordered and digested and the derivation stage
    already keeps the plan's order, so two builds of one run assign the same handles — which is
    what lets a stored `composition.json` be read back against a rebuilt table.

    **`EvidenceScopeFact` gets no handle**, and the omission is `writer._bindable_ids`': §7's
    row carries no result, no unit and no period, so there is nothing for a binding's `rendered`
    to be a rendering *of*. Giving it a handle would make a slot resolvable here and the binding
    unresolvable at §13.1 — the shape of disagreement between two stages this arrangement exists
    to avoid. The rows are accepted in the sequence, and skipped, so a caller can pass the
    derivation stage's whole output without filtering it first.

    A row that offers nothing at all is still in the table. It has a handle, so a template
    naming it earns `field_not_offered_by_row` — which says what is wrong — rather than
    `unknown_slot_handle`, which would say the row does not exist.
    """
    by_id = {fact.observation_id: fact for fact in package.facts}
    rows: list[SlotRow] = [
        _observed_row(f"F{ordinal}", fact, package)
        for ordinal, fact in enumerate(package.facts, start=1)]
    ordinal = 0
    for row in derived_facts:
        if not isinstance(row, DerivedFact):
            continue
        ordinal += 1
        rows.append(_derived_row(f"D{ordinal}", row, package, by_id))
    rows.extend(_passage_row(f"P{ordinal}", passage, package)
                for ordinal, passage in enumerate(passages, start=1))
    return tuple(rows)


def _observed_row(
    handle: str, fact: PackagedFact, package: StoryEvidencePackage
) -> SlotRow:
    """An observation's three questions: what it reads, what it is of, and when.

    Each offer is present only where `story.core.renderings` has an answer — the first of the
    legal renderings, the first unambiguous metric surface, the one period surface §13.4's
    closed grammar accepts. The **first** rendering and not all of them, because a slot resolves
    to one string: `legal_renderings` orders most-preferred first, and offering the row's second
    spelling under a second name would be offering a 9B model a way to be inconsistent for no
    gain.
    """
    offers: dict[str, str] = {}
    renderings = legal_renderings(fact, package)
    if renderings:
        offers[VALUE_FIELD] = renderings[0]
    surfaces = metric_surfaces(package, fact.metric_id)
    if surfaces:
        offers["metric"] = surfaces[0]
    period = period_surface_of_fact(fact)
    if period:
        offers["period"] = period
    return SlotRow(
        handle=handle,
        fact_id=fact.observation_id,
        kind=SlotKind.OBSERVED,
        offers=offers,
        evidence_handles=(fact.evidence_handle,) if fact.evidence_handle else (),
    )


def _derived_row(
    handle: str,
    derived: DerivedFact,
    package: StoryEvidencePackage,
    by_id: Mapping[str, PackagedFact],
) -> SlotRow:
    """A derivation's offers, with the metric and the period names **mutually exclusive**.

    For a same-metric change `.metric`, `.from_metric` and `.to_metric` would be three names for
    one string; for a two-period change `.period` and `.to_period` would be two names for one.
    Offering a redundant spelling is offering a way to be inconsistent for no gain, so the row
    offers one name per thing: `.metric` **only** when `from_metric_id == metric_id`, the pair
    **only** when they differ, `.period` **only** for a same-period operation, the pair **only**
    for a two-period one.

    **The two period surfaces are read off the input observations, not off `from_period` /
    `to_period`.** Those two fields are period *keys* — `"2022Q3"` — and §13.4 resolves a
    surface to *endpoints*, so a surface built from a key would be an assertion that the two
    agree. `_derived_period_findings` compares the declared surface against
    `index.fact(derived.to_fact_id)`'s own window *(read 2026-08-23)*, so the endpoints this
    reads are exactly the ones the check reads. `DerivedFact.period_surface_hint` is the same
    string by a different route — `execute` fills it from `to_point.period` — and
    `test_story_composition.py` asserts the two agree on the demo run rather than picking one
    and hoping.

    **`.direction` is offered for every row whose word is one a sentence can carry**, which is
    every `DisplaySemantics` member except `DIRECTION_UNVERIFIABLE`. It removes the last
    direction word the model has to reconstruct and weakens nothing: the model still chooses
    whether to use the slot and which metric sits on which side of it, which is the choice
    §5.1's highest-value refusal depends on being able to get wrong.
    """
    offers: dict[str, str] = {}
    renderings = legal_renderings(derived, package)
    if renderings:
        offers[VALUE_FIELD] = renderings[0]
    direction = direction_phrase(derived)
    if direction:
        offers["direction"] = direction
    if derived.from_metric_id == derived.metric_id:
        _offer_first(offers, "metric", metric_surfaces(package, derived.metric_id))
    else:
        _offer_first(offers, "from_metric", metric_surfaces(package, derived.from_metric_id))
        _offer_first(offers, "to_metric", metric_surfaces(package, derived.metric_id))
    from_fact = by_id.get(derived.from_fact_id)
    to_fact = by_id.get(derived.to_fact_id)
    if derived.operation in SAME_PERIOD_OPERATIONS:
        _offer_period(offers, "period", to_fact)
    elif derived.operation in TWO_PERIOD_OPERATIONS:
        _offer_period(offers, "from_period", from_fact)
        _offer_period(offers, "to_period", to_fact)
    return SlotRow(
        handle=handle,
        fact_id=derived.fact_id,
        kind=SlotKind.DERIVED,
        offers=offers,
        # §6: no handle is ever minted for a derived fact, so a sentence stating one cites the
        # two observations it was computed from. `(from, to)` and not sorted, because that is
        # the order the derivation reads them in and a citation list a reader can follow back
        # to the prompt is worth more than an alphabetical one.
        evidence_handles=tuple(
            fact.evidence_handle for fact in (from_fact, to_fact)
            if fact is not None and fact.evidence_handle),
    )


def _passage_row(
    handle: str, passage: PackagedPassage, package: StoryEvidencePackage
) -> SlotRow:
    """A passage, which offers **no text slot at all**.

    `P2` is only ever named in `rests_on`. A `{{P2}}` in sentence text is
    `field_not_offered_by_row`, because a passage has no value, no metric and no period to
    write — an `explanatory` sentence says what the filing says in the model's own words and
    cites the passage; it does not interpolate it.

    The handles are every fact read from this passage, in package order, which is what
    `compile_draft` walks to choose the citation for a sentence resting on it.
    """
    return SlotRow(
        handle=handle,
        fact_id=passage.passage_id,
        kind=SlotKind.PASSAGE,
        offers={},
        evidence_handles=tuple(
            fact.evidence_handle for fact in package.facts
            if fact.passage_id == passage.passage_id and fact.evidence_handle),
    )


def _offer_first(offers: dict[str, str], field: str, surfaces: Sequence[str]) -> None:
    """The first surface, or no key at all. Never a key holding `""` — R3 is a refusal."""
    if surfaces:
        offers[field] = surfaces[0]


def _offer_period(offers: dict[str, str], field: str, fact: PackagedFact | None) -> None:
    """The observation's own period surface, or no key.

    `None` for an input the package no longer holds, which is a package and a derivation
    artifact disagreeing about the run. The row then offers no period, and a template naming one
    is refused rather than filled from the period *key* — see `_derived_row`.
    """
    surface = period_surface_of_fact(fact) if fact is not None else None
    if surface:
        offers[field] = surface


__all__ = [
    "SAME_PERIOD_OPERATIONS",
    "SLOT_FIELDS",
    "TWO_PERIOD_OPERATIONS",
    "VALUE_FIELD",
    "slot_table",
]
