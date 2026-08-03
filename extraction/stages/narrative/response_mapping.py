"""An untrusted model answer to typed claims, or to recorded refusals.

Responsibility: decide, for each object the model returned, whether it becomes a `LaneClaim`
or a `NarrativeIssue`. Boundaries: it is handed a parsed dict, the passage text, an ontology
and the scope's answer. It never calls a provider, never opens a file, and never sees a
prompt — which is what lets every refusal below be tested with a literal dict and no GPU.

It also does no text reading of its own. Span location lives in `core.text_spans`, figure and
scale-word reading in `core.numbers`, and period phrases in `core.periods`; what is left here
is the part that needs an ontology and an answer, which is the part worth reviewing as a
rejection boundary *(the split was made after review 2026-08-02, when a shifted slice survived
inside a module nobody was reading as a text reader — see `core.text_spans`)*.

**Nothing here repairs.** Every condition in STAGE_10 §5 rejects the finding, records the raw
object that caused it, and stops. Remapping an out-of-scope metric to the nearest one in
scope, rounding an instant into a duration, or trusting a value the quoted span does not
contain would each produce a claim that is wrong in a way every downstream check passes.

Three of the checks deserve their reasoning stated here rather than at the call site:

**The quoted span is verified against the passage and then replaced by the passage's own
bytes.** The model retypes a span; the corpus stores it. `core.text_spans.locate` compares
whitespace-normalised, falling back to the typographic fold `core.concept_resolution` already
defines, which distinguishes a retyping artifact — a curly quote set as a straight one — from a
fabrication. What is recorded is the slice of the passage that the map says the match occupies,
never the model's transcription, so `validate_quoted_text` compares the catalog against itself
and a warning there means a real defect rather than a whitespace difference.

**A declared-ambiguous wording never selects a concept on its own.** `aliases.yaml` marks
"gross margin", "contribution", "homes" and five more as ambiguous, and the prompt asks the
model to abstain on them. Asking was not enough: with `gaap_gross_margin` and
`adjusted_gross_margin` both in scope and the passage printing a bare "gross margin", the model
emitted `gaap_gross_margin` *(measured live 2026-08-01)*. `_ambiguity_unsettled` refuses that
here, because ambiguity preservation is a hard constraint everywhere else in this pipeline and
an advisory version of it is not one.

**The period is never stated by the answer; it is resolved by `core.periods` from a phrase the
passage prints.** `period_phrases` finds the phrases in a passage that resolve without a model,
`public.response_schema` makes them the enum of `period_label`, and the answer's only
contribution is choosing one. This narrows the dimension where a wrong answer survives every
other check — the period-type test catches an instant read as a duration and catches nothing
about a duration read as the wrong duration (V1_CLAIM_EXTRACTION §8a.7a, 21 wrongly dated
observations). It does not close it; see `_resolve_period`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...core import numbers, periods
from ...core.concept_resolution import fold
from ...core.models import (
    SCALE_FACTOR,
    GUIDANCE_NOT_REPORTED,
    NOT_THE_SUBJECT_COMPANY,
    LaneClaim,
    ScaleDeclaration,
)
from ...core.text_spans import LocatedSpan, locate, normalize
from ...core.units import is_monetary, unit_for_metric
from ..tables.public import (
    AMBIGUOUS_ALIAS,
    DEFERRED_REQUIRED_SOURCE_LANE,
    INVALID_NUMBER,
    MISSING_PERIOD,
    MISSING_REQUIRED_CONTEXT,
    MISSING_UNIT,
    PERIOD_TYPE_MISMATCH,
)
from .public import (
    ANOTHER_PARTY,
    DEFINITION_ONLY,
    DEFINITIONAL_NOT_OBSERVATIONAL,
    DERIVED_COMPARISON,
    FORWARD_GUIDANCE,
    LANE_NAME,
    METRIC_OUT_OF_SCOPE,
    MISSING_POPULATION_DEFINITION,
    PERIOD_NOT_GROUNDED_IN_PASSAGE,
    PERIOD_NOT_PRINTED,
    PERIOD_OVER_PERIOD_CHANGE,
    QUOTED_SPAN_NOT_IN_PASSAGE,
    SCALE_NOT_APPLICABLE,
    SCALE_NOT_DECLARED,
    UNIT_CONTRADICTS_ONTOLOGY,
    VALUE_CONTRADICTS_QUOTED_TEXT,
    VALUE_NOT_IN_QUOTED_SPAN,
    AmbiguousSurface,
    NarrativeExtraction,
    NarrativeIssue,
)

DURATION = periods.DURATION
INSTANT = periods.INSTANT

# Where a scale word was found, and the two answers are not equally strong. `inline_prose` is
# the word printed with the figure; `preceding_context` is the word printed elsewhere in the
# passage, which is how a letter declaring "(in millions)" once governs a run of figures
# (V1_CLAIM_EXTRACTION §8a.6, measured on the table lane: 9 of 74 table passages declare their
# scale outside the table). Both are `ScaleDeclaration.location` members; see that field.
INLINE_PROSE = "inline_prose"
PRECEDING_CONTEXT = "preceding_context"

_SCALE_WORDS = re.compile(r"\b(thousand|million|billion)s?\b", re.I)

# A printed figure and a model's transcription of it may differ in the last place shown when
# the model retypes "7.3" as 7.30. Nothing wider: this is a transcription tolerance, not a
# rounding allowance, and 279 versus 280 is a different number.
_VALUE_TOLERANCE = 1e-9

# Words carried by so many surface forms that their presence says nothing about which concept
# was meant. Kept deliberately short — a longer list would start deciding which qualifiers
# count, which is the judgement the ontology is supposed to own.
_UNINFORMATIVE = frozenset({"a", "an", "and", "at", "by", "for", "in", "of", "on", "or",
                            "per", "the", "to"})

_STATEMENT_REFUSALS = {
    PERIOD_OVER_PERIOD_CHANGE: (
        DERIVED_COMPARISON,
        "a period-over-period change is not an observation of a measured period"),
    DEFINITION_ONLY: (
        DEFINITIONAL_NOT_OBSERVATIONAL,
        "the passage defines the metric rather than reporting a value for it"),
    FORWARD_GUIDANCE: (
        GUIDANCE_NOT_REPORTED,
        "an outlook is not a reported figure for a period"),
}


@dataclass(frozen=True)
class MappingContext:
    """Everything the boundary needs that is not the answer itself.

    A frozen record rather than nine parameters, because the same context maps every finding
    in one answer and threading it through by hand is how one check ends up asking a different
    passage than another.
    """

    passage_id: str
    document_id: str
    passage_text: str
    scope_concept_ids: frozenset[str]
    deferred_metric_ids: frozenset[str]
    subject_entity_id: str = "opendoor"
    subject_type: str = "public_company"
    # The declared-ambiguous wordings the *prompt* warned about, so the rule the model was
    # given and the rule enforced here are one list read twice rather than two lists.
    ambiguous_surfaces: tuple[AmbiguousSurface, ...] = ()
    extractor_metadata: dict[str, Any] = field(default_factory=dict)


def map_answer(content: dict, *, ontology, context: MappingContext) -> NarrativeExtraction:
    """The whole boundary. One parsed answer in, claims and recorded refusals out."""
    extraction = NarrativeExtraction(passage_id=context.passage_id)

    for finding in _objects(content.get("claims")):
        outcome = _map_claim(finding, ontology=ontology, context=context)
        if isinstance(outcome, LaneClaim):
            extraction.claims.append(outcome)
        else:
            extraction.issues.append(outcome)

    for finding in _objects(content.get("abstentions")):
        extraction.issues.append(_map_abstention(finding, context=context))

    return extraction


# -- one proposed claim ------------------------------------------------------------------------


def _map_claim(finding: dict, *, ontology, context: MappingContext) -> LaneClaim | NarrativeIssue:
    """Every rejection in STAGE_10 §5, in the order that makes a refusal explainable.

    Ordered cheapest-and-most-fundamental first: which concept, then whose figure, then what
    kind of statement, then the evidence, then the number, then the period. A unit complaint
    about a metric the model invented would name the wrong problem.
    """
    reject = _rejector(finding, context)
    metric_id = _text(finding.get("metric_id"))

    if not metric_id or metric_id not in context.scope_concept_ids:
        return reject(METRIC_OUT_OF_SCOPE,
                      f"{metric_id!r} was not among the concepts offered for this passage",
                      metric_ids=(metric_id,) if metric_id else ())

    metric = ontology.registry.find(metric_id)
    if metric is None:
        return reject(METRIC_OUT_OF_SCOPE,
                      f"{metric_id!r} is not a concept in the loaded ontology",
                      metric_ids=(metric_id,))

    if metric_id in context.deferred_metric_ids:
        return reject(DEFERRED_REQUIRED_SOURCE_LANE,
                      f"{metric_id} requires a source lane this corpus does not contain",
                      metric_ids=(metric_id,))

    if _text(finding.get("subject")) == ANOTHER_PARTY:
        return reject(NOT_THE_SUBJECT_COMPANY,
                      "the answer says this figure is about someone other than the filing "
                      "company; it must not be emitted as an observation of the registrant",
                      metric_ids=(metric_id,))

    subject_types = tuple(str(t) for t in (getattr(metric, "subject_types", ()) or ()))
    if subject_types and not ontology.registry.accepts_type(
            subject_types, context.subject_type):
        # `mortgage_rate` and `home_price_appreciation` declare `geographic_market`: they are
        # observations about a housing market, not about the registrant. The model reads them
        # correctly out of a letter that quotes them, and attaching the registrant as their
        # subject would assert a measurement of the company that no filing makes. Decided from
        # the declaration through the registry's own subtype-aware test, so a metric that
        # later admits `public_company` needs no edit here.
        return reject(NOT_THE_SUBJECT_COMPANY,
                      f"{metric_id} is an observation about {list(subject_types)}, and this "
                      f"lane's subject is {context.subject_type}",
                      metric_ids=(metric_id,))

    refusal = _STATEMENT_REFUSALS.get(_text(finding.get("statement_type")))
    if refusal:
        return reject(refusal[0], refusal[1], metric_ids=(metric_id,))

    span = _locate(context.passage_text, _text(finding.get("evidence_sentence")))
    if span is None:
        return reject(QUOTED_SPAN_NOT_IN_PASSAGE,
                      "the quoted span is not present in the passage",
                      metric_ids=(metric_id,))

    value_text = _text(finding.get("value_text"))
    if _locate(span.text, value_text) is None:
        return reject(VALUE_NOT_IN_QUOTED_SPAN,
                      f"{value_text!r} does not occur in the span it was said to come from",
                      metric_ids=(metric_id,))
    if not numbers.identifies_its_subject(span.text):
        return reject(MISSING_REQUIRED_CONTEXT,
                      f"the quoted span {span.text!r} locates a number and names nothing "
                      "that identifies what it measures",
                      metric_ids=(metric_id,))

    # After the evidence and not with the other "which concept" checks, because the question
    # this answers is whether the *quoted sentence* settles the ambiguity, and there is no
    # sentence to ask until the span has been located.
    unsettled = _ambiguity_unsettled(
        metric, span.text, surfaces=context.ambiguous_surfaces)
    if unsettled:
        return reject(AMBIGUOUS_ALIAS, unsettled, metric_ids=(metric_id,))

    printed = numbers.printed_magnitude(value_text)
    if printed is None:
        return reject(INVALID_NUMBER, f"{value_text!r} is not a printed magnitude",
                      metric_ids=(metric_id,))
    magnitude, printed_scale = printed

    stated = finding.get("value")
    if not isinstance(stated, (int, float)) or isinstance(stated, bool):
        return reject(INVALID_NUMBER, f"value {stated!r} is not a number",
                      metric_ids=(metric_id,))
    if abs(float(stated) - magnitude) > _VALUE_TOLERANCE:
        return reject(VALUE_CONTRADICTS_QUOTED_TEXT,
                      f"value {stated!r} is not the magnitude printed in {value_text!r} "
                      f"({magnitude})",
                      metric_ids=(metric_id,))

    unit, currency = unit_for_metric(metric)
    if unit is None:
        return reject(MISSING_UNIT, f"no unit determinable for {metric_id}",
                      metric_ids=(metric_id,))
    if _text(finding.get("unit")) != unit:
        return reject(UNIT_CONTRADICTS_ONTOLOGY,
                      f"{metric_id} is reported in {unit}, not "
                      f"{_text(finding.get('unit'))!r}",
                      metric_ids=(metric_id,))

    if str(getattr(metric, "value_type", "")) == "integer" and magnitude != int(magnitude):
        return reject(INVALID_NUMBER,
                      f"{metric_id} is an integer metric and {magnitude} is not whole",
                      metric_ids=(metric_id,))

    scale, scale_source, scale_location, scale_error = _resolve_scale(
        finding, unit=unit, value_text=value_text, printed_scale=printed_scale,
        span_text=span.text, passage_text=context.passage_text)
    if scale_error:
        return reject(scale_error[0], scale_error[1], metric_ids=(metric_id,))

    period, period_span, period_error = _resolve_period(
        finding, metric=metric, passage_text=context.passage_text)
    if period_error:
        return reject(period_error[0], period_error[1], metric_ids=(metric_id,))

    population, population_error = _resolve_population(
        finding, metric=metric, passage_text=context.passage_text)
    if population_error:
        return reject(population_error[0], population_error[1], metric_ids=(metric_id,))

    declaration = (
        ScaleDeclaration(scale=scale, location=scale_location,
                         source_passage_id=context.passage_id,
                         declaration_text=scale_source)
        if scale != "units" else None)

    return LaneClaim(
        metric_id=metric_id,
        value=magnitude * SCALE_FACTOR[scale],
        unit=unit,
        currency=currency,
        period=period,
        subject_entity_id=context.subject_entity_id,
        subject_type=context.subject_type,
        source_lane=LANE_NAME,
        # `reported` and not a stronger word: `AssertionType` declares four members and this
        # is the one the ontology accepts for a figure a filing states.
        assertion_type="reported",
        passage_id=context.passage_id,
        document_id=context.document_id,
        # The passage's own bytes, not the model's transcription — see the module docstring.
        raw_text=span.text,
        scale=declaration,
        population_definition_raw=population,
        extractor_metadata={
            **context.extractor_metadata,
            "value_text": value_text,
            "printed_magnitude": magnitude,
            "scale_declared_in": scale_source if scale != "units" else None,
            "scale_declaration_location": scale_location if scale != "units" else None,
            "period_label": _text(finding.get("period_label")),
            "span_match": span.basis,
            "span_char_start": span.start,
            "span_char_end": span.end,
            **_period_attribution(period_span, span),
        },
    )


# -- the pieces a claim is refused over ----------------------------------------------------------


def _resolve_scale(finding, *, unit, value_text, printed_scale, span_text, passage_text):
    """A magnitude scale is a *reading*, never an assertion.

    Two refusals, from V1_CLAIM_EXTRACTION §8a.5. A scale on anything but money is refused
    outright — the exception list in "(in thousands, except percentages)" is a presentation
    note about the monetary columns, and multiplying a count of homes by a thousand is how
    2,462,000 homes sold in a quarter got emitted. And a scale no word in the text declares is
    refused even for money, because the alternative is the model deciding the order of
    magnitude of a dollar figure on its own.

    **Where the word was found is recorded and the two locations are not equally strong.**
    Beside the figure is `inline_prose`; anywhere else in the passage is `preceding_context`,
    the same distinction §8a.6 makes for the table lane, where 9 of 74 table passages declare
    their scale outside the table. The passage-wide reading is kept rather than dropped
    because refusing it would refuse every letter that writes "(in millions)" once and then
    prints a column of bare figures — and prompt rule 4 states the fallback, so the mapping is
    not more permissive than the rule the model was given.
    """
    scale = _text(finding.get("scale")) or "units"
    if scale not in SCALE_FACTOR:
        return "units", None, None, (INVALID_NUMBER, f"{scale!r} is not a magnitude scale")
    if scale == "units":
        return "units", None, None, None

    if not is_monetary(unit):
        return "units", None, None, (
            SCALE_NOT_APPLICABLE,
            f"scale {scale} asserted for a {unit} figure; scale applies to money only")

    if printed_scale is not None:
        if printed_scale != scale:
            return "units", None, None, (
                SCALE_NOT_DECLARED,
                f"{value_text!r} prints {printed_scale}, not {scale}")
        return scale, value_text, INLINE_PROSE, None

    word = numbers.scale_word(scale)
    if _SCALE_WORDS.search(span_text) and numbers.declares_scale_word(span_text, word):
        return scale, numbers.scale_declaration_phrase(span_text, word), INLINE_PROSE, None
    if numbers.declares_scale_word(passage_text, word):
        return (scale, numbers.scale_declaration_phrase(passage_text, word),
                PRECEDING_CONTEXT, None)
    return "units", None, None, (
        SCALE_NOT_DECLARED,
        f"scale {scale} is asserted and the word {word!r} is printed nowhere in the passage")


def _resolve_period(finding, *, metric, passage_text):
    """The period is read by `core.periods` from a phrase the passage prints. Never by a model.

    The answer contributes one thing: *which* printed phrase a figure belongs to. What that
    phrase means is decided by the same module the table lane's headers go through. A period
    the passage does not state in a resolvable form therefore cannot be emitted, by
    construction rather than by checking.

    Two measurements produced this design, in this order.

    Trusting the answer's ISO dates produced six claims on the Q3 2023 shareholder letter
    dated `2022-07-01..2022-09-30` — right metric, right value, right unit, wrong year, every
    downstream check passing *(measured 2026-08-01)*. That is precisely the failure
    V1_CLAIM_EXTRACTION §8a.7a records: the period-type check catches an instant read as a
    duration and catches nothing about a duration read as the wrong one. The model held that
    answer across three prompt revisions; the passage names "3Q23" three times and opens with
    a footnote mentioning 2022, and it anchored to the footnote.

    Requiring the *quoted* phrase to resolve refused every claim on that passage instead, for
    the same reason: the model kept answering "third quarter". Safe and useless.

    So the phrase is constrained by the schema (`public.response_schema` builds its enum from
    `core.periods.period_phrases`) and resolved here.

    **What this achieves, stated exactly.** The model can no longer state a period; it can only
    point at one that the passage prints. That removes every period the passage does not name —
    including the wrong-year answer above, which named a year the letter never printed. It does
    **not** make a wrong period unrepresentable *(corrected after review 2026-08-02, where the
    docstring claimed it did)*. A comparative MD&A paragraph prints its prior-period phrase too:
    on `open-20210930.htm#p142` the enum is `('three months ended September 30, 2021',
    'September 30, 2021', 'three months ended September 30, 2020', 'September 30, 2020')`, and
    an answer attaching the 2020 phrase to a 2021 figure passes every check here. The residual
    is a **measured dimension for step 11**, not a closed one (V1_CLAIM_EXTRACTION §8a.12): the
    chosen phrase and its position relative to the quoted evidence are recorded on every claim
    by `_period_attribution` so period attribution can be scored rather than assumed.

    **The residual the enum itself created, and the member that closes it**
    *(added 2026-08-02)*. Narrowing `period_label` to printed phrases left no way to answer
    "this figure's period is not among them", and step 11 measured the consequence on
    `q42021formxex992sharehol.htm#p10`: the letter reports 4Q21 *and* the full year, the phrases
    are exactly `['December 31, 2021', '4Q21', '4Q20']`, and the model labelled the full-year
    Contribution Profit `4Q21` rather than abstaining — two values under one deterministic
    observation id. `public.PERIOD_NOT_PRINTED` is now the enum's last member and is checked
    **before** the period-type test, because a model saying "no printed phrase gives this
    period" has answered the question this function asks and the kind of a period it declines
    to name is not a further disagreement.

    Still refused, and correctly: a passage whose only period wording is "the third quarter",
    with no date and no shorthand anywhere in it. Resolving that needs the filing date, and
    reading a period off the filing date is an inference the passage does not make.

    **And refused since 2026-08-03: a period phrase grounded inside a flattened column header.**
    The narrowing above assumes the printed phrase is *prose about the figure*. It is not, when
    the passage is a table that lost its columns during normalization —
    `q42023formxex992sharehol.htm#p20` prints seven period headings in a row and then seven
    values per metric, and choosing one heading says nothing about which of the seven values it
    governs. That is `PERIOD_NOT_GROUNDED_IN_PASSAGE`; see `core.periods.flattened_period_grids`
    for the signal and `public.PERIOD_NOT_GROUNDED_IN_PASSAGE` for what it cost.
    """
    label = _text(finding.get("period_label"))
    if label == PERIOD_NOT_PRINTED:
        return None, None, (
            MISSING_PERIOD,
            "the answer states that no phrase this passage prints gives this figure's period")

    kind = _text(finding.get("period_kind"))
    declared = str(getattr(metric, "period_type", "") or "")
    if declared and kind and declared != kind:
        return None, None, (
            PERIOD_TYPE_MISMATCH,
            f"{metric.concept_id} is a {declared} metric and the answer gives a {kind}")

    if not label:
        return None, None, (MISSING_PERIOD,
                            "the answer names no period phrase from the passage")
    located = _locate(passage_text, label)
    if located is None:
        return None, None, (
            MISSING_PERIOD,
            f"the period phrase {label[:60]!r} is not printed in the passage")

    # Checked against the span the period is *grounded on* rather than against every occurrence
    # of the phrase. A letter that prints "4Q23" in a sentence and again in a chart's axis
    # labels grounds on the sentence, and refusing there would remove a correctly dated figure
    # to punish a coincidence of wording — measured on `q42023formxex992sharehol.htm#p9`, whose
    # 18% is the right answer to the very figure #p20 gets wrong.
    grids = periods.flattened_period_grids(passage_text)
    if any(start <= located.start and located.end <= end for start, end in grids):
        return None, None, (
            PERIOD_NOT_GROUNDED_IN_PASSAGE,
            f"the period phrase {located.text[:60]!r} sits inside a run of period headings "
            "this passage prints with no table structure left to bind them to columns; which "
            "figure it governs is not readable")

    period_type = declared or kind or DURATION
    resolved = periods.resolve_period_phrase(located.text, period_type=period_type)
    if resolved is None:
        return None, None, (
            MISSING_PERIOD,
            f"no {period_type} is readable from the printed phrase {located.text[:60]!r}")
    return resolved, located, None


def _period_attribution(period_span: LocatedSpan | None, evidence: LocatedSpan) -> dict:
    """Where the chosen period phrase sits relative to the sentence the figure was read from.

    Recorded because `_resolve_period` narrows wrong-period selection without eliminating it,
    and the thing step 11 needs in order to score what is left is not the period alone but
    *how far the model reached for it*. A phrase inside the quoted sentence is the strongest
    attribution; one 900 characters away in a comparative paragraph is the shape of the failure
    §8a.12 asks to be measured.

    These are numbers about a claim, not an evidence anchor. §8a.11's rule is unchanged: the
    anchor is the passage id, and nothing here is ever used to cite anything.
    """
    if period_span is None:
        return {"period_label_char_start": None, "period_label_in_evidence": None,
                "period_label_distance_from_evidence": None}
    inside = period_span.start >= evidence.start and period_span.end <= evidence.end
    if inside:
        distance = 0
    elif period_span.end <= evidence.start:
        distance = period_span.end - evidence.start          # negative: before the sentence
    else:
        distance = period_span.start - evidence.end          # positive: after it
    return {
        "period_label_char_start": period_span.start,
        "period_label_in_evidence": inside,
        "period_label_distance_from_evidence": distance,
    }


def _resolve_population(finding, *, metric, passage_text):
    """§7.1, at the only point that can see the passage.

    A metric whose ontology entry declares a `population` has three filed wordings in this
    corpus and no filing reconciles them, so an observation without the wording it was read
    under is not comparable to any other. The lane cannot supply one it did not read, so it
    refuses rather than normalising — assembly carries whatever arrives here verbatim.

    **The wording must be printed, and what is carried is the passage's slice, not the
    answer's.** `population_definition_raw` is the one model-authored free-text field that
    reaches an `OntologyClaim` body, and `validate_quoted_text` inspects `raw_text` only, so a
    fabricated denominator wording would survive assembly and verify with nothing to catch it.
    """
    if getattr(metric, "population", None) is None:
        return None, None
    wording = _text(finding.get("population_text"))
    if not wording:
        return None, (
            MISSING_POPULATION_DEFINITION,
            f"{metric.concept_id} declares a population and the answer quotes no wording "
            "for it; two observations with different denominators are not one series")
    located = _locate(passage_text, wording)
    if located is None:
        return None, (
            MISSING_POPULATION_DEFINITION,
            f"the population wording {wording[:60]!r} is not printed in the passage")
    return located.text, None


# -- ambiguity preservation ---------------------------------------------------------------------


def _ambiguity_unsettled(metric, span_text, *, surfaces) -> str | None:
    """Whether a declared-ambiguous wording is doing the choosing. Returns why, or None.

    **The rule: a bare ambiguous wording never selects a concept.** For every surface
    `aliases.yaml` declares ambiguous that (a) names this metric among two or more candidates
    the scope offered and (b) is printed in the quoted sentence, the sentence must also print
    at least one word that belongs to the chosen concept's own surface forms and *not* to the
    ambiguous wording. That word is the qualifier the ontology's note says is the only thing
    separating them.

    Derived from the vocabulary, not from a list of metric ids, and it lands where the ontology
    says it should:

    | quoted sentence | chosen | outcome |
    | --- | --- | --- |
    | "or 9.8% gross margin" | `gaap_gross_margin` | refused — its every surface *is* "Gross Margin", so nothing beyond the bare wording can ever support it |
    | "Adjusted Gross Margin of 12%" | `adjusted_gross_margin` | allowed — "adjusted" is printed |
    | "Sold 2,687 homes across our markets" | `homes_sold` | allowed — "sold" is printed |
    | "Contribution Profit of $43 million" | `contribution_profit` | allowed — "profit" is printed |

    The first row is the live failure this exists for: both concepts in scope, no qualifier in
    the passage, the model abstained on the Adjusted pair and emitted the GAAP one anyway
    *(measured 2026-08-01)*.

    **What it does not do**, stated rather than implied: it does not adjudicate between two
    concepts that both print a qualifier, and it says nothing about a wording the ontology has
    not declared ambiguous. It refuses the unqualified case, which is the case the ontology
    declares and the case that was observed.
    """
    if not surfaces:
        return None
    words_in_span = _words(span_text)
    metric_id = str(getattr(metric, "concept_id", ""))
    for surface in surfaces:
        if metric_id not in surface.concept_ids or len(surface.concept_ids) < 2:
            continue
        if not _prints_surface(span_text, surface.surface):
            continue
        qualifiers = _concept_words(metric) - _words(surface.surface) - _UNINFORMATIVE
        printed = sorted(qualifiers & words_in_span)
        if printed:
            continue
        rivals = ", ".join(c for c in surface.concept_ids if c != metric_id)
        return (f"the quoted span uses {surface.surface!r}, which the ontology declares "
                f"ambiguous between {list(surface.concept_ids)}, and prints no wording that "
                f"separates {metric_id} from {rivals}"
                + (f": {' '.join(surface.note.split())}" if surface.note else ""))
    return None


def _prints_surface(text: str, surface: str) -> bool:
    """Whether the ambiguous wording itself is in the text, folded and whitespace-collapsed."""
    haystack, _ = normalize(fold(text or "").lower())
    needle, _ = normalize(fold(surface or "").lower())
    return bool(needle) and re.search(rf"\b{re.escape(needle)}s?\b", haystack) is not None


def _concept_words(concept) -> frozenset[str]:
    """Every word the ontology spells this concept with, across its label and its aliases."""
    surfaces = [str(getattr(concept, "label", "") or "")]
    surfaces += [str(alias) for alias in (getattr(concept, "aliases", ()) or ())]
    words: set[str] = set()
    for surface in surfaces:
        words |= _words(surface)
    return frozenset(words)


def _words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", fold(text or "").lower()))


# -- an abstention the model chose -----------------------------------------------------------------


def _map_abstention(finding: dict, *, context: MappingContext) -> NarrativeIssue:
    """Recorded as offered. The schema constrains the reason, so there is nothing to refuse.

    The metric ids are filtered to the scope for the same reason a claim's are: a reason code
    attached to a concept that was never on the menu would put a concept into step 11's
    classification that this passage never considered.
    """
    metric_ids = tuple(
        m for m in (_text(v) for v in _strings(finding.get("metric_ids")))
        if m in context.scope_concept_ids)
    quoted = _text(finding.get("evidence_sentence"))
    located = _locate(context.passage_text, quoted) if quoted else None
    return NarrativeIssue(
        code=_text(finding.get("reason")) or "UNRESOLVED_METRIC",
        detail=_text(finding.get("detail")),
        passage_id=context.passage_id,
        metric_ids=metric_ids,
        quoted_span=located.text if located else None,
        raw_finding=dict(finding),
        rejected_claim=False,
    )


# -- answer shape ----------------------------------------------------------------------------------------


def _locate(text: str, candidate: str) -> LocatedSpan | None:
    """`core.text_spans.locate` with this package's typographic fold bound in."""
    return locate(text, candidate, fold=fold)


def _rejector(finding: dict, context: MappingContext):
    """A refusal carries the object that caused it. STAGE_10 §5: step 13 writes these out."""
    def reject(code: str, detail: str, *, metric_ids: tuple[str, ...] = ()) -> NarrativeIssue:
        return NarrativeIssue(
            code=code,
            detail=detail,
            passage_id=context.passage_id,
            metric_ids=metric_ids,
            quoted_span=_text(finding.get("evidence_sentence")) or None,
            raw_finding=dict(finding),
            rejected_claim=True,
        )
    return reject


def _objects(value) -> tuple[dict, ...]:
    return tuple(item for item in (value or ()) if isinstance(item, dict))


def _strings(value) -> tuple[Any, ...]:
    return tuple(value or ()) if isinstance(value, (list, tuple)) else ()


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""
