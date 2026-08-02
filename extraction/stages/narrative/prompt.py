"""Prompt construction for the narrative lane. Pure, versioned, and the only place it happens.

Pure because the prompt is half of the request identity that makes replay possible
(STAGE_10 §7): the persisted answer is keyed on the digest of (prompt, schema, model, sampling
parameters), so a prompt that varied with the clock, the filesystem or a set's iteration order
would make every stored answer unreachable on the next run. Nothing here reads a file, asks
the time, or iterates an unordered collection.

`PROMPT_VERSION` is part of that identity. Changing the wording below without changing the
constant would leave answers produced by the old wording indistinguishable from answers
produced by the new one — the single failure a replay cache cannot show you.

**What the prompt carries is a closed list** (STAGE_10 §3): the passage verbatim, the injected
scope's concepts with their declared unit, value type, period type, `distinct_from` siblings
and ambiguity notes, the passage's own metadata, and the response schema. It carries no other
passage, no previously extracted claim, no benchmark, and no concept the scope did not offer.

The `distinct_from` siblings are in the prompt on purpose and are the reason the scope pulls
them in. A prompt listing only the likely concept cannot be told "this is `homes_sold`, and
`homes_purchased` is a different metric it is not", which is the confusion the ontology's
declarations exist to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .public import PERIOD_NOT_PRINTED

# Bumped from 1.0.0 at review *(2026-08-02)*. Three rules named `quoted_span`, a field the
# schema has never had — it is `evidence_sentence` — rule 6 said the period phrases were listed
# "below" when they are rendered above, and rule 4 stated a scale rule stricter than the one
# `response_mapping._resolve_scale` applies. The prompt string is itself part of the request
# identity, so old answers become unreachable rather than silently re-used; this constant is
# what makes the *recorded* answers say which wording produced them.
# Bumped to 1.2.0 *(2026-08-02)*: `period_label` gained `public.PERIOD_NOT_PRINTED` as its last
# enum member and rule 6 now names it. The enum is rendered into the prompt, so every stored
# answer produced under 1.1.0 is unreachable rather than silently re-used.
PROMPT_VERSION = "1.2.0"

# Anything longer is a heading path that has swallowed a page of table of contents; the corpus
# has several. Truncated rather than dropped because the first entries are the informative
# ones and an empty context line would read as "this passage has no section".
_HEADING_BUDGET = 240


@dataclass(frozen=True)
class PassageContext:
    """The passage's own metadata, and nothing derived from any other passage.

    Filing form and date are here because a period phrase is only resolvable against them:
    "the third quarter" in an 8-K filed 2023-11-02 is not the same quarter as in one filed
    three months later, and a lane that hides the filing date is asking the model to guess.
    """

    passage_id: str
    document_type: str
    form: str | None = None
    filing_date: str | None = None
    heading_path: tuple[str, ...] = ()
    subject_entity_id: str = "opendoor"
    subject_label: str = "Opendoor Technologies Inc."


def render_concept(concept) -> str:
    """One ontology concept as one line, with everything the refusals turn on.

    Unit, value type and period type are here because each is a rejection condition in
    `response_mapping.py`: an answer that contradicts one is refused rather than repaired, and
    refusing an answer the prompt never stated the rule for would be scoring the lane on a
    rule the model was not given.
    """
    parts = [
        f"- {concept.concept_id}",
        f'label: "{concept.label}"',
        f"unit: {getattr(concept, 'unit', '')}",
        f"value_type: {_enum_value(getattr(concept, 'value_type', ''))}",
        f"period_type: {_enum_value(getattr(concept, 'period_type', ''))}",
    ]
    aliases = tuple(getattr(concept, "aliases", ()) or ())
    if aliases:
        parts.append("also written: " + "; ".join(str(a) for a in aliases[:6]))
    siblings = tuple(str(s) for s in (getattr(concept, "distinct_from", ()) or ()))
    if siblings:
        parts.append("NOT the same metric as: " + ", ".join(siblings))
    population = getattr(concept, "population", None)
    if population is not None:
        parts.append(
            "population wording required: copy the phrase naming what the percentage is "
            "of into population_text")
    for ambiguity in tuple(getattr(concept, "ambiguities", ()) or ()):
        note = " ".join(str(getattr(ambiguity, "description", "")).split())
        if note:
            parts.append(f"unresolved ({getattr(ambiguity, 'code', '')}): {note}")
    return "\n    ".join(parts)


def render_concepts(concepts) -> str:
    """Concepts in the order given. The caller sorts; sorting twice would hide a caller bug."""
    return "\n".join(render_concept(concept) for concept in concepts)


def render_ambiguous_surfaces(surfaces) -> str:
    """The ontology's declared-ambiguous wordings, as warnings about the passage's own words."""
    return "\n".join(
        f'- "{surface.surface}" may be any of: {", ".join(surface.concept_ids)}'
        + (f"\n    {' '.join(surface.note.split())}" if surface.note else "")
        for surface in surfaces
    )


def build_prompt(
    *, text: str, concepts, context: PassageContext, schema: dict, ambiguous_surfaces=(),
    period_phrases: tuple[str, ...] = (),
) -> str:
    """The exact string sent to the provider. Same inputs, same bytes, always.

    The rules below are written as refusals rather than as encouragements, and each is paired
    with a check in `response_mapping.py`: a rule the mapping does not enforce is decoration,
    and a check the prompt never stated is a trap.

    **The pairing is asserted, not claimed** *(review 2026-08-02 found this docstring asserting
    it while two rules had no check)*. `tests/extraction/test_narrative_lane.py` walks the rule
    text and fails on any field name the schema does not carry, which is what caught three
    rules constraining a `quoted_span` the model never sees. The two unenforced rules were
    fixed on the mapping side rather than deleted here: the `AMBIGUOUS_ALIAS` instruction is
    now enforced by `response_mapping._ambiguity_unsettled`, and rule 4's scale rule was
    restated to match `_resolve_scale`, which accepts a scale word printed anywhere in the
    passage and records where it was found.
    """
    heading = " > ".join(context.heading_path)[:_HEADING_BUDGET]
    ambiguity_block = (
        ["", "WORDINGS THE ONTOLOGY DECLARES AMBIGUOUS — if the sentence you quote uses one of",
         "these and does not also print a word that separates the concepts it could mean,",
         "abstain with AMBIGUOUS_ALIAS rather than choosing. A claim resting on the bare",
         "wording is rejected, so abstaining loses you nothing:",
         render_ambiguous_surfaces(ambiguous_surfaces)]
        if ambiguous_surfaces else [])
    return "\n".join((
        "You are extracting metric observations from ONE passage of an SEC filing by "
        f"{context.subject_label}.",
        "",
        "PASSAGE METADATA",
        f"  document type: {context.document_type}",
        f"  filing form: {context.form or 'unknown'}",
        f"  filing date: {context.filing_date or 'unknown'}",
        f"  section: {heading or '(none)'}",
        f"  the filing company: {context.subject_entity_id} ({context.subject_label})",
        "",
        "CANDIDATE CONCEPTS — you may name these and no others:",
        render_concepts(concepts),
        *ambiguity_block,
        "",
        # The only periods this passage states in a form that resolves without a model. The
        # schema constrains `period_label` to exactly this list, so it is shown rather than
        # left implicit: a grammar that silently refuses every other answer would otherwise
        # look to the model like an arbitrary failure.
        "PERIOD PHRASES THIS PASSAGE STATES — period_label must be one of these:",
        ("\n".join(f'- "{phrase}"' for phrase in period_phrases)
         if period_phrases else
         "  (none — this passage states no resolvable period)"),
        # The escape hatch the enum lacked until 2026-08-02. Rendered as a listed choice rather
        # than described in the rules, because the model picks `period_label` off this block.
        f'- "{PERIOD_NOT_PRINTED}"',
        "",
        "RULES",
        "  1. Report only figures printed in the passage below. Never compute, convert,",
        "     annualise, sum or infer a figure that is not printed.",
        "  2. evidence_sentence comes FIRST in every object and must be a SINGLE CONTIGUOUS",
        "     run of characters from the passage — as if dragged with a highlighter from one",
        "     start point to one end point, skipping NOTHING in between. Skipping any words",
        "     in the middle rejects the claim, even if the meaning is preserved. Do NOT",
        "     prepend an introductory clause that is separated from the figure by other text;",
        "     in a list, quote only the one item containing the figure. The period does not",
        "     belong here — it goes in period_label — so never reach backwards for a date.",
        "     It must still name what the figure measures: 'Contribution Profit increased by",
        "     $149.7 million to $169.7 million' is right, 'to $169.7 million' and",
        "     '$169.7 million' are both rejected. When one sentence reports a change and a",
        "     level, quote that whole sentence twice: on the claim for the level and on the",
        "     abstention for the change. If you cannot copy such a run, do not report the",
        "     figure.",
        "  3. value_text is the figure exactly as printed inside evidence_sentence, including",
        "     any $, %, comma, parentheses or scale word. value is that same figure as a number",
        "     WITHOUT applying the scale word: '$43 million' is value_text '$43 million',",
        "     value 43, scale millions. '4.4%' is value 4.4, scale units.",
        "  4. scale is only ever a word actually printed in the passage, and only for monetary",
        "     figures. Printed with the figure is what you should quote; a scale declared once",
        "     for a block of figures — '(in millions)' — also counts, and is recorded as the",
        "     weaker source. Counts of homes, markets or contracts and percentages are always",
        "     scale units, whatever the passage says elsewhere.",
        "  5. unit must be the unit listed above for the metric_id you chose. If the",
        "     passage's figure is not in that unit, it is a different metric or none.",
        "  6. period_label must be one of the period phrases listed above, chosen because it",
        "     is the period the figure is reported for. Do not write dates: the phrase is",
        "     resolved for you. The phrase does NOT have to sit beside the figure — a run of",
        "     figures at the end of a letter and a heading are as valid a source as the",
        "     sentence itself. If none of the listed phrases is the figure's period — a",
        "     full-year total in a letter that prints only quarter labels, for instance —",
        f'     answer "{PERIOD_NOT_PRINTED}"',
        "     and do NOT reach for the nearest phrase that is printed. A figure carrying that",
        "     answer is dropped, which is the correct outcome: a wrong period is worse than a",
        "     missing figure. Abstaining with MISSING_PERIOD says the same thing.",
        "  7. statement_type: reported_level for a figure that IS the measure for its",
        "     period; period_over_period_change for an increase, a decrease, or a 'versus'",
        "     difference; definition_only where the passage explains the metric instead of",
        "     reporting it; forward_guidance for an expectation or an outlook.",
        "     ONLY reported_level figures belong in claims. Everything else goes in",
        "     abstentions — do not write a claim object for a change, a definition or an",
        "     outlook. 'Contribution Profit increased by $149.7 million to $169.7 million'",
        "     is ONE claim for $169.7 million and ONE abstention, DERIVED_COMPARISON, for",
        "     the $149.7 million increase.",
        "  8. subject: the_filing_company for the company's own figure, another_party for a",
        "     figure about the overall market, a competitor, or anyone else.",
        "  9. population_text is required for a metric marked 'population wording required':",
        "     copy the phrase from the passage saying what the percentage is a percentage of.",
        "     Leave it an empty string for every other metric.",
        " 10. Write at most one claim per figure, and do not repeat a figure you have already",
        "     reported. Put every figure you decline to report into abstentions, with the",
        "     reason and an evidence_sentence. Declining is a correct answer; guessing is not.",
        "     If the passage reports nothing, return empty claims and say why in abstentions.",
        "",
        "Answer with JSON matching this schema exactly:",
        json.dumps(schema, sort_keys=True, separators=(",", ":")),
        "",
        "PASSAGE:",
        text,
    ))


def _enum_value(value) -> str:
    """Ontology enums are `StrEnum`; `str()` of one is the member, not the value, in repr."""
    return str(getattr(value, "value", value) or "")
