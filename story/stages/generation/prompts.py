"""What the two personas are told, how the package is rendered, and what an answer must be.

Responsibility: pure functions over a `StoryEvidencePackage` — two personas, two prompts and
two schemas — and the versions that identify them. Nothing here calls a model, opens a file, asks
the clock or iterates an unordered collection: the prompt is half of the request identity the
replay store keys on (§14, `story/providers/generation_store.py`), so a rendering that varied
with set order would make every stored generation unreachable on the next run.

**The schema is constant except for one field, and the exception is §11's.** Everything the
model may say from a *closed* vocabulary is an `enum` here — `statement_class`,
`unusable_evidence[].reason`, `causal_language`. Everything that depends on *this* package —
which fact ids exist, which passage ids resolve, which warning codes the package carries — is
checked by code after the call (`planner.plan_violations`) and is deliberately **not** an enum,
because otherwise the same wrong answer would be a schema violation in one package and a plan
rejection in another, and the two have different meanings: a schema violation is a statement
about the runtime, a plan rejection is a statement about §11.

`causal_language` is the one package-dependent enum, because §11 requires it: it is computed by
code before the call and the schema is pinned to the single value computed, so the grammar
cannot emit the other one. See `planner.causal_language_for`.

**§15.3's portable subset only** — `type`, `required`, `properties`, `additionalProperties`,
`enum`, `items`. `minItems` is prohibited, which is why "counterpoints must be non-empty"
cannot be expressed here at all and lives in `planner.plan_violations` instead. Every schema
this module builds is run through `validate_portable_schema` by a test, and by the provider
before any request is sent.

**What the planner is shown is a slice, not the package** (§10.2.1 point 3): facts, metrics,
events, warnings, conflicts and *excerpts*. Relationships, documents, compatibility decisions,
evidence sources, formula windows and the retrieval trace are not rendered — the planner
neither orders nor cites them, and the token budget is 8,192 for the whole request. The
writer's passage set is derived from fact bindings by code and never from this plan's
`required_citation_passage_ids`, so nothing here can filter what the writer later sees.

**The writer is shown a different slice, and it is the one `writer.writer_passages` computed**
— the *whole* text of every passage a packaged fact was read from, plus the accepted plan and
the surfaces each fact may be named by. `writer_prompt` takes those passages as an argument
rather than deriving them, because §10.2.1 point 3 is a rule about evidence and this module is
about rendering; the rule lives beside the stage that must not be able to break it.

**Three things the writer is told that it would otherwise have to guess, and code computes all
three** (§2's line: the model chooses words, code chooses facts).

* `metric_surfaces_for` — which surfaces name this metric and *only* this metric inside this
  package. §13.5 refuses `"gross margin"` because `gaap_gross_margin`'s own label is
  `"Gross Margin"` and `"gross margin" ⊂ "adjusted gross margin"`; a writer left to pick a
  surface picks that one. The filter here is a **conservative local approximation** of §13.5's
  alias index — it drops any surface that is a sub-phrase of another package metric's surface —
  and the verifier remains the authority. It cannot *add* a surface the index would refuse for a
  reason the package does not carry, which is why the approximation is safe in the direction
  that matters.
* `period_surface_for` — one surface per fact, in §13.4's closed grammar, derived from the
  fact's own endpoints. The grammar is the verifier's; this is the writing direction of it, and
  `tests/story/test_story_writer.py` round-trips every surface it emits back through
  `period_grammar.resolve` rather than trusting the pair to agree.
* `WARNING_QUALIFIER_PHRASES` — the phrases that count as having stated a required warning.
  Restated from §13's table rather than imported: `story.stages.verification` is not a surface
  this stage may import (`test_no_stage_imports_another_stage`), and a writer not shown the
  phrases would be refused for silence it was never told how to break. A test asserts the two
  copies are identical, so the duplication is checked rather than hoped over.

**`WRITER_OPERATIONS` is narrower than the verifier's `OPERATION_INPUTS`, and it is narrower by
four rather than by five.** `extremum` and `absence` need a full comparison set that §10.2's
twelve-fact cap cannot guarantee, and `temporal_order` needs two dated items where all three
`executive_change` events carry `occurred_on: null`; a writer able to declare them would be
half-supporting a claim §13.14 then refuses anyway. **`compare_levels` is here, and its absence
was a defect.** §13.14 requires a comparative to be expressed as `compare_levels` or
`compare_deltas`, and with neither in the grammar the construction could not be declared at all
— so *"the GAAP gross margin was 15.9 percentage points lower than the adjusted gross margin"*,
which is true, correctly bound and the whole content of the demo candidate, was refused with
`unsupported_comparative` for a reason no rewrite could fix *(measured 2026-08-04, the recorded
Qwen run)*. It is recomputed three ways — the direction against the values, the size against the
gap, and the metric named on each side of the comparing word against the input it was declared
as — so a comparison the sentence does not support refuses rather than passing.

**What `compare_levels` still cannot say, and the prompt does not promise it can**: one metric
against itself in two periods. §13.14 reads the two sides out of the sentence through §13.5's
alias index, so two sides carrying one metric are two sides nothing can tell apart, and the
verifier refuses them rather than checking half of the claim. Rule 12's *"name each figure's
metric on its own side"* is that requirement stated the writing way; the demo candidate is
cross-metric and never meets the case.

`compare_deltas` stays out and the writer is not taught it: a side of it is a *change of one
metric*, so it needs four bound observations forming two same-metric deltas over the same pair
of periods, and every fact in the demo package is 2022Q3. The verifier recomputes it if a
hand-written draft declares one; the grammar does not offer a branch the prompt cannot teach.
`delta_relative` stays out for §13.3's third gate: the demo's own inputs straddle zero, where a
relative change is arithmetically defined and rhetorically meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from story.core.models import (
    CausalLanguage,
    EditorialPlan,
    PackagedFact,
    PackagedPassage,
    SentenceKind,
    StatementClass,
    StoryEvidencePackage,
    UnusableReason,
)

#: Bumped whenever the wording below or the rendering changes. It is a digest input to every
#: stored generation, so answers produced under an older wording become unreachable rather
#: than silently re-used — the single failure a replay cache cannot show you.
#:
#: **1.1.0**: §4 S4. Three sections were added to the rendering — COMPANY IDENTITY, METRIC
#: SEMANTICS and COMPARISON RULES — and rule 8 with them. The planner now receives what each
#: figure *means*, which of them may be set against which, and an explicit statement that no
#: description of the company exists. Every generation recorded under 1.0.0 answered a prompt
#: that carried none of it.
PLANNER_PROMPT_VERSION = "1.1.0"

#: Reaches the wire and the store. `extraction`'s provider hard-codes one schema name for every
#: call; three story personas against one package would be indistinguishable in a capture.
PLANNER_SCHEMA_NAME = "story_editorial_plan"

#: What a call site passes as `max_tokens`. A constant rather than a default argument, for the
#: reason `PINNED_TEMPERATURE` is one: it is a digest input, and a determinism input with a
#: default is one a call site can end up without.
#:
#: **2048, and 1024 was measured to be too small.** `config/extraction.yaml`'s
#: `max_output_tokens: 1024` is the validated setting for a *claim*; a plan for the 2022Q3
#: divergence package runs to **752-899 completion tokens** when it completes, and the first
#: live attempt at 1024 was cut off mid-object — `finish_reason: length`, and the transport
#: raised `assistant content is not JSON: Expecting ',' delimiter: line 67 column 4`
#: *(measured 2026-08-04 against the running Qwen3.5-9B-Q4_K_M)*. The request still fits the
#: server's `-c 8192`: this package's prompt measures **1,520 prompt tokens**, and a run that
#: spent the whole output budget reported `total_tokens: 3568`.
PLANNER_MAX_TOKENS = 2048

#: How much of a passage the planner is shown. §10.2.1 excerpts explanatory and counter-evidence
#: passages at ±400 characters in the package itself; a fact-bound table arrives whole (Rule A
#: needs it), and the median backing passage is 2,144.5 characters, so rendering four of them in
#: full would spend the context on markdown pipes. The rendering says how much it cut.
PLANNER_EXCERPT_CHARS = 400

#: **A wording experiment was run against this text and its conclusion did not survive, so the
#: text is unchanged and the finding is the confound.** Rewriting rule 4 as an imperative
#: ("you must write at least one counterpoint … a plan with no counterpoint is rejected")
#: appeared to push Qwen3.5-9B into a token loop that spent all 2,048 output tokens repeating
#: a passage id, 3 runs of 3, against 0 of 3 for the wording below. Re-running the *identical*
#: two prompts an hour later inverted the result: the wording below produced the loop 3 of 3,
#: then produced an accepted plan 4 of 4, with no edit in between *(all measured 2026-08-04,
#: same package, same schema, temperature 0.0)*. **The outcome tracks the server's cache state,
#: not the wording** — `cached_tokens: 1516` on a 1,520-token prompt — so no wording claim is
#: supportable from this evidence and none is made. The experiment is recorded rather than
#: deleted because the tempting conclusion was wrong in a way a single run would not show.
#:
#: What *is* supportable: §15.3's portable subset has no `maxLength`, so every free-text field
#: here is unbounded, and a 9B model that starts repeating inside one will do so until
#: `max_tokens`. That failure is clean — `finish_reason: length`, unparseable JSON, one
#: attempt, `StoryProviderResponseError`, no plan — and it is the reason `plan_violations`
#: rather than this string is where §11 is enforced.
PLANNER_SYSTEM = """\
You are the editorial planner for an investor post about one company's reported figures.

You are given an evidence package and nothing else. You have no tools, no search and no \
access to any database: the package is your entire universe. Plan the post; you do not write \
it.

Rules:
1. Every key point and every counterpoint names the ids it rests on. Use only ids that appear \
in the package below, spelled exactly as they appear. A plan naming any other id is rejected \
and never reaches the writer. `required_fact_ids` holds only ids beginning `obs:`, from the \
FACTS section. `required_citation_passage_ids` holds only passage ids, from the PASSAGE \
EXCERPTS and COUNTER-EVIDENCE sections. Putting a passage id in `required_fact_ids` is a \
rejection.
2. Introduce no number, no period and no entity that is not in the package. Do not restate a \
figure in different units. Do not compute a new one.
3. Say why something happened only if a quoted span in the package says so. The \
`causal_language` field is fixed for you and you may not choose it.
4. If the package carries counter-evidence, every counterpoint you write must rest on at least \
one id drawn from it, and every counter-evidence item you do not use must appear in \
`unusable_evidence` with one of the five listed reasons. A counterpoint grounded in nothing is \
not a counterpoint.
5. `required_warnings` may name only warning codes listed in the package's WARNINGS section. \
The writer is refused if it drops one.
6. `statement_class` is `reported` for a figure quoted from a filing, `calculated` for a \
figure derived from two of them, and `explanatory` for a claim resting on a passage rather \
than a number.
7. `prohibited_claims` are claims the writer must not make even though the evidence is nearby \
- name them plainly.
8. COMPANY IDENTITY, METRIC SEMANTICS and COMPARISON RULES tell you what the subject is, what \
each figure means and which figures may be set against which. They are definitions: they carry \
no figure and no id you may name. Where a line says NOT AVAILABLE, the corpus does not hold \
that answer and neither do you - plan no claim that needs it.

Answer with the JSON object the schema describes and nothing else.\
"""


def planner_schema(*, causal_language: CausalLanguage) -> dict[str, Any]:
    """§11's output shape, inside §15.3's portable subset, with `causal_language` pinned.

    Built fresh on every call rather than shared as a module constant: the returned mapping
    reaches a provider that copies it into a request body, and a shared nested dict is one
    mutation away from re-keying every stored generation that ever used it.

    `causal_language`'s enum holds exactly one member — the value code computed from the
    package. §15.3 permits `enum` and prohibits everything that could express a range, so a
    single-member enum is the only way to say "this field is not yours to choose" in a grammar.
    """
    id_array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "thesis", "why_it_matters", "key_points", "counterpoints", "required_warnings",
            "causal_language", "uncertainty", "structure", "prohibited_claims",
            "unusable_evidence",
        ],
        "properties": {
            "thesis": {"type": "string"},
            "why_it_matters": {"type": "string"},
            "key_points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim", "required_fact_ids",
                                 "required_citation_passage_ids", "statement_class"],
                    "properties": {
                        "claim": {"type": "string"},
                        "required_fact_ids": dict(id_array),
                        "required_citation_passage_ids": dict(id_array),
                        "statement_class": {
                            "type": "string",
                            "enum": [member.value for member in StatementClass],
                        },
                    },
                },
            },
            "counterpoints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim", "required_fact_ids",
                                 "required_citation_passage_ids"],
                    "properties": {
                        "claim": {"type": "string"},
                        "required_fact_ids": dict(id_array),
                        "required_citation_passage_ids": dict(id_array),
                    },
                },
            },
            "required_warnings": {"type": "array", "items": {"type": "string"}},
            "causal_language": {"type": "string", "enum": [causal_language.value]},
            "uncertainty": {"type": "string"},
            "structure": {"type": "array", "items": {"type": "string"}},
            "prohibited_claims": {"type": "array", "items": {"type": "string"}},
            "unusable_evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "reason"],
                    "properties": {
                        "id": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "enum": [member.value for member in UnusableReason],
                        },
                    },
                },
            },
        },
    }


def planner_prompt(package: StoryEvidencePackage) -> str:
    """The package as the planner sees it. Deterministic, and it says what it cut.

    Every id is printed exactly as it will have to be spelled back, because the code check
    after the call compares strings and a rendering that prettified an id would make the model
    fail a rule it was never shown.
    """
    lines: list[str] = [
        "CANDIDATE  " + package.candidate_id,
        "PACKAGE    " + package.package_id,
        "SUBJECT    " + _subject(package),
        "",
        IDENTITY_HEADING,
    ]
    lines.extend(_identity_lines(package))
    lines += ["", "FACTS"]
    lines.extend(_fact_lines(package.facts))
    lines += ["", "METRICS"]
    lines.extend(_metric_lines(package))
    lines += ["", SEMANTICS_HEADING]
    lines.extend(_semantic_lines(package))
    lines += ["", COMPARISON_HEADING]
    lines.extend(_comparability_lines(package))
    lines += ["", "EVENTS"]
    lines.extend(_event_lines(package))
    lines += ["", "WARNINGS"]
    lines.extend(_warning_lines(package))
    lines += ["", "CONFLICTS"]
    lines.extend(_conflict_lines(package))
    lines += ["", "PASSAGE EXCERPTS"]
    excerpts = (_passage_lines(package.primary_passages, "primary")
                + _passage_lines(package.context_passages, "context")
                + _passage_lines(package.explanatory_passages, "explanatory"))
    lines.extend(excerpts or ["  (none)"])
    # The count is in the heading because the rule is about all of them. A 9B model given the
    # rule in the system message and the items 2,000 characters later returned
    # `counterpoints: []` against a package holding one — measured 2026-08-04, and the exact
    # vacuity §11's correction predicted.
    counter = _passage_lines(package.counter_evidence, "counter")
    count = len(package.counter_evidence)
    lines += ["", f"COUNTER-EVIDENCE ({count} item{'' if count == 1 else 's'}; every one must "
                  "be used by a counterpoint or listed in unusable_evidence)"]
    lines.extend(counter or ["  (none)"])
    return "\n".join(lines)


def _subject(package: StoryEvidencePackage) -> str:
    subject = package.subject
    state = "resolved" if subject.resolved else "UNRESOLVED - never render this as a name"
    return f"{subject.entity_id}  {subject.entity_text!r}  ({state})"


def _fact_lines(facts: Sequence[PackagedFact]) -> list[str]:
    if not facts:
        return ["  (none)"]
    lines: list[str] = []
    for fact in facts:
        lines.append(f"  [{fact.observation_id}]")
        printed = f'  printed "{fact.printed_form}"' if fact.printed_form else ""
        lines.append(
            f"      {fact.metric_id} ({fact.metric_label})  {fact.period_key}  "
            f"{fact.value} {fact.unit}{printed}")
        state = f"      lane {fact.source_lane}  state {fact.validation_state}"
        if fact.warning_codes:
            state += "  warnings " + ",".join(fact.warning_codes)
        if fact.ambiguity_codes:
            state += "  ambiguities " + ",".join(fact.ambiguity_codes)
        lines.append(state)
        if fact.passage_id:
            quoted = f' quoting "{fact.quoted_text}"' if fact.quoted_text else ""
            lines.append(f"      cited in passage {fact.passage_id}{quoted}")
        elif fact.evidence_source_id:
            lines.append(f"      evidenced by {fact.evidence_source_id} (no filed passage)")
    return lines


# ---------------------------------------------------------------------------------------
# §4 S4 — the ontology's declarations, rendered as facts in *both* prompts
#
# **One rendering, two personas, and that is the point.** A definition is not a planner-shaped
# thing or a writer-shaped thing: `housing_inventory_homes` is a count at a moment in both
# prompts or the two models are working from different meanings. The slices differ in evidence
# (§10.2.1 point 3) and must not differ in semantics.
#
# **No `fact_id` is printed, deliberately.** §11 rule 1 restricts `required_fact_ids` to ids
# beginning `obs:`, so a rendered `sem:…` id is an id the planner would be rejected for using;
# printing one would be an invitation to a refusal. The ids exist on the package for §4 S6's
# evidence panel, which is where a reader needs them.
#
# **The unavailable rows are printed too, and that is the whole of §4 S4's *"no company
# description may be invented"*.** A model shown nothing about what Opendoor does will supply
# the answer from its weights; a model shown `NOT AVAILABLE` followed by the reason has been
# told not to. `IdentityFact.available` is the field that carries it and this is what renders it.
# ---------------------------------------------------------------------------------------

IDENTITY_HEADING = (
    "COMPANY IDENTITY (everything known about the subject; a line marked NOT AVAILABLE is not "
    "yours to fill in)")
SEMANTICS_HEADING = (
    "METRIC SEMANTICS (what each figure means - declared by the ontology, which is the "
    "authority on it; these are definitions, not figures)")
COMPARISON_HEADING = (
    "COMPARISON RULES (which figures may be set against which, and on what terms)")


def _identity_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.identity_facts:
        return ["  (none)"]
    return [
        "  " + ("" if fact.available else "NOT AVAILABLE - ") + fact.statement
        for fact in package.identity_facts
    ]


def _semantic_lines(package: StoryEvidencePackage) -> list[str]:
    """Grouped by metric, statements only.

    Grouped because a flat list of eleven sentences about two metrics reads as eleven unrelated
    assertions, and the question a reader (and a 9B model) is answering is *"what does this
    figure mean"*, one metric at a time. The `attribute` is not printed: the statement already
    says which declaration it is, and the label would be a second copy of it.
    """
    if not package.semantic_facts:
        return ["  (none)"]
    lines: list[str] = []
    current = ""
    for fact in package.semantic_facts:
        if fact.metric_id != current:
            current = fact.metric_id
            lines.append(f"  {current}")
        lines.append("      - " + fact.statement)
    return lines


def _comparability_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.comparability_facts:
        return ["  (none)"]
    return ["  - " + fact.statement for fact in package.comparability_facts]


def _metric_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.metrics:
        return ["  (none)"]
    lines: list[str] = []
    for metric in package.metrics:
        head = f"  {metric.metric_id} ({metric.label})  unit {metric.unit}"
        if metric.period_type:
            head += f"  period_type {metric.period_type}"
        lines.append(head)
        if metric.population:
            lines.append(f"      population: {metric.population}")
        if metric.distinct_from:
            # The ontology's declarations are why two margins may be compared as a divergence
            # pair and may never be merged (§6.9 R2). A planner that did not see them could
            # write "gross margin" for both.
            lines.append("      distinct from " + ", ".join(metric.distinct_from))
        for ambiguity in metric.ambiguities:
            lines.append(f"      ambiguity {ambiguity.code}: {ambiguity.description}"
                         f" (impact: {ambiguity.impact})")
    return lines


def _event_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.events:
        return ["  (none)"]
    lines: list[str] = []
    for event in package.events:
        when = event.occurred_on or event.announced_on or "date unknown"
        lines.append(f"  [{event.event_id}] {event.event_type_id}  {when}"
                     f"  basis {event.date_basis or 'unstated'}")
        for name in sorted(event.properties):
            lines.append(f'      {name} = "{event.properties[name]}" (verbatim string)')
        if event.passage_id:
            lines.append(f"      cited in passage {event.passage_id}")
    return lines


def _warning_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.warnings:
        return ["  (none)"]
    return [
        f"  {warning.code}  [{warning.severity.value}]  {warning.detail}".rstrip()
        for warning in package.warnings
    ]


def _conflict_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.conflicts:
        return ["  (none)"]
    lines: list[str] = []
    for conflict in package.conflicts:
        values = ", ".join(str(cluster.value) for cluster in conflict.clusters)
        lines.append(f"  {conflict.slot}: {values}  ({conflict.classification},"
                     f" resolved by {conflict.resolution_rule})")
    return lines


def _passage_lines(passages: Sequence[PackagedPassage], role: str) -> list[str]:
    lines: list[str] = []
    for passage in passages:
        excerpt = passage.text[:PLANNER_EXCERPT_CHARS]
        shown = len(excerpt)
        # Stated rather than implied. A planner shown 400 of 2,564 characters and told nothing
        # would read the excerpt as the passage, and "the filing does not say" is exactly the
        # inference this package must never invite.
        extent = (f"characters 0-{shown} of {passage.char_count}"
                  if shown < len(passage.text) or shown < passage.char_count
                  else f"whole passage, {passage.char_count} characters")
        head = f"  [{passage.passage_id}]  {role}"
        if passage.passage_kind:
            head += f"  kind {passage.passage_kind}"
        if passage.excerpted:
            head += f"  excerpted at offset {passage.char_start}"
        lines.append(head)
        lines.append(f"      {extent}")
        if passage.heading_path:
            lines.append("      section: " + " > ".join(passage.heading_path))
        lines.append("      " + excerpt.replace("\n", "\n      "))
    # Empty means empty: the "(none)" line belongs to the *section*, and returning it here
    # would print it once per absent passage kind.
    return lines


# ---------------------------------------------------------------------------------------
# §12 — the writer
# ---------------------------------------------------------------------------------------

#: Bumped whenever the wording or the rendering below changes, for the reason
#: `PLANNER_PROMPT_VERSION` is: it is a digest input to every stored generation.
#:
#: **1.1.0**: the draft schema gained `calculation.period_surface` and `compare_levels`, and
#: rules 5, 11, 12 and 17 changed with them. Every generation recorded under 1.0.0 answers a
#: different question and is unreachable by construction — the schema is in `request_identity`
#: — and the bump is what makes that visible in the row rather than only in the digest.
#:
#: **1.2.0**: §4 S4, the same three sections the planner gained, plus rule 18. The two prompts
#: render them from **one** pair of functions: a definition that differed between the persona
#: that plans a claim and the persona that writes it would be two meanings for one number.
WRITER_PROMPT_VERSION = "1.2.0"

#: Reaches the wire and the store, and is not the planner's name — two personas against one
#: package must be distinguishable in a capture.
WRITER_SCHEMA_NAME = "story_post_draft"

#: What a call site passes as `max_tokens`. 2048, the planner's figure, and the reason it is not
#: larger is measured rather than assumed: the writer's prompt carries the *whole* text of every
#: bound passage, the median backing passage is 2,144.5 characters (§10.2.1), and the server is
#: `-c 8192`. A five-sentence draft with its bindings and citations serialises to well under
#: 2,048 completion tokens; what is observed live is recorded in `tests/story/test_story_writer.py`
#: rather than predicted here.
WRITER_MAX_TOKENS = 2048

#: How many sentences a call site asks for unless it says otherwise. §12 takes a *length target*
#: as an input, so it is an argument to `writer_prompt` and this is only the demo's value.
DEFAULT_LENGTH_TARGET = 5

#: The `Calculation.operation`s the grammar admits. Narrower than the verifier's table, and the
#: narrowing is the point — see this module's docstring.
WRITER_OPERATIONS: tuple[str, ...] = (
    "delta_pp", "delta_bps", "difference", "ratio", "sum", "compare_levels")

#: What counts as having stated a required warning (§13's *"required qualifiers present"*).
#: **Restated from `story/stages/verification/deterministic.py`'s
#: `REQUIRED_WARNING_QUALIFIERS`, and `test_the_writer_is_shown_the_same_warning_phrases_the_verifier_requires`
#: asserts the two are identical.** A stage may not import another stage, and the alternative to
#: a checked copy is a writer refused for silence nobody told it how to break.
WARNING_QUALIFIER_PHRASES: Mapping[str, tuple[str, ...]] = {
    "filing_date_unknown": ("filing date", "date it was filed", "as-filed date",
                            "when it was filed"),
    "counter_evidence_same_document": ("same filing", "elsewhere in the filing",
                                       "another table in the same"),
    "conflicting_values": ("conflict", "two values", "two readings", "also reported"),
    "warned_observation": ("flagged", "carries a warning", "data-quality"),
}


@dataclass(frozen=True, slots=True)
class StyleProfile:
    """§12's *"style is separate from facts"*, as a value the request carries separately.

    Rendered into the **system** message and never into the prompt, which is what makes the
    §12 test meaningful: two drafts of one package under two profiles must have identical
    binding fact ids, and that can only be shown if the evidence rendering provably never sees
    the profile. `writer_prompt` does not take one, so it cannot.

    Not a `StoryModel`: nothing persists a style profile at this step — S11 owns
    `config/story.yaml` — and a pydantic model in `story/core/models.py` would be a contract
    change made from inside a stage that does not own that file.
    """

    profile_id: str
    voice: str
    sentence_length: str
    house_conventions: tuple[str, ...] = ()


#: The demo's profile. Plain, short, and it says nothing about numbers — a house convention that
#: could change a rendering is a style rule that can change a fact.
PLAIN_INVESTOR_STYLE = StyleProfile(
    profile_id="investor-plain:1",
    voice="plain, factual, no adjectives that are not in the filing",
    sentence_length="one claim per sentence, at most 25 words",
    house_conventions=(
        "write figures as the filing prints them",
        "name the period in every sentence that states a figure",
    ),
)

#: **The rules a draft must satisfy, stated once, in the order the verifier applies them.** Every
#: line here corresponds to a §13 refusal code, and the wording names the failure rather than the
#: virtue — a 9B model given *"be careful with percentages"* writes `15.9%`, and a 9B model given
#: *"a difference between two percentages is measured in percentage points, never in percent"*
#: has been told what the check is.
#:
#: Rules 3 and 8 are what make the draft checkable at all, and both are stated as *substring*
#: rules rather than as offsets. §12 specifies `char_start`/`char_end` on every binding and
#: citation; asking a 9B model to count characters would fail on every call, so the model
#: declares the exact substring and `writer.draft_from` locates it — deterministically, refusing
#: a substring that occurs twice rather than choosing between the occurrences. The declaration is
#: still the model's and the verifier still never guesses one, which is what §12 is protecting.
#:
#: **Rules 5, 6, 7 and 9 were added after a live run, and each closes a defect that run made
#: rather than one this text predicted** *(measured 2026-08-04, Qwen3.5-9B-Q4_K_M, this package,
#: three identical attempts: 1,698 prompt tokens, 937 completion tokens, `finish_reason: stop`,
#: ~12.8 s, byte-identical answers)*. The first wording produced a §12-clean draft that §13
#: refused five times over:
#:
#: * `calculation_does_not_recompute`, expected `-15.9`. The model listed
#:   `(adjusted, gaap)` and `_recompute` is `values[1] - values[0]`, so the sign inverted. **Input
#:   order was never stated** — rule 6 now states it, and this is the defect most likely to have
#:   reached a published post, because the numeral it produces is right and its sign is not.
#: * `formula_version_not_valid_for_period`. The model filled the field with the package id.
#:   §15.3 has no null, so an unused string field is a box a model fills; rule 7 and the FORMULA
#:   WINDOWS section give it the answer *"empty"* and something to check it against.
#: * `unbound_numeral` on `"2022"` in the calculated sentence. **This is a §12 finding, not a
#:   model error**: a `calculated` sentence carries no `fact_binding`, a period surface was only
#:   declarable *on* a binding, and §13.1 covers a numeral by binding span, calculation result,
#:   period surface or allowlist. So a calculated sentence naming its own period was
#:   unverifiable by construction. The first wording told the writer not to name the period;
#:   **that was the wrong end to fix it from**, and `Calculation.period_surface` is the right
#:   one — rule 5 now asks for the period rather than forbidding it, and §13.4 resolves the
#:   declared surface and requires it to agree with every input observation.
#: * `citation_reused_for_unrelated_claim`, twice. The model re-cited both table spans in an
#:   explanatory sentence that bound nothing. Rule 9 states §13.7's predicate — reuse *and*
#:   non-support — rather than banning reuse, which a two-column table legitimately needs.
WRITER_SYSTEM = """\
You are the writer for an investor post about one company's reported figures.

You are given an evidence slice, an accepted editorial plan and a style profile. You have no \
tools, no search and no access to any database: what you are shown is your entire universe. \
Follow the plan; you did not choose it.

Write the post as a list of sentences, each one carrying the evidence for what it says.

Rules:
1. Introduce no number, no date, no period and no company that is not in the FACTS or PASSAGES \
sections. Do not restate a figure in different units.
2. `kind` is `reported` for a figure quoted from a filing, `calculated` for a figure you derive \
from two of them, `explanatory` for a claim resting on a passage rather than a number, and \
`connective` for a sentence that carries no claim at all - no figure, no comparison, no \
characterisation.
3. Every numeral in a sentence must be declared. A `reported` sentence lists one \
`fact_bindings` entry per figure: `fact_id` exactly as FACTS spells it, `rendered` the exact \
run of characters in your own `text` that holds the figure, and `metric_surface` and \
`period_surface` copied from the surfaces that fact offers. `rendered` must appear in `text` \
exactly once, character for character. An undeclared numeral is refused.
4. Use a metric surface exactly as it is offered. A shorter one names two metrics and is \
refused - write "GAAP gross margin" or "adjusted gross margin", never "gross margin".
5. A `calculated` sentence carries exactly one `calculation` and **no citation**: it states \
something you computed, not something the filing said. It carries no fact binding, so write no \
figure in it other than the calculation's own result. Name the period in it and put those exact \
words in the calculation's `period_surface` - every input must be from that one period, and a \
calculation over two different periods must name neither.
6. In `input_observation_ids` the **base comes first and the subject second**, and every \
operation is computed as second minus first (or second divided by first). To say that the \
second figure stands 15.9 points above the first, list the lower one first.
7. `formula_version_id` is "" unless the FORMULA WINDOWS section names a window for this \
metric. An arithmetic difference between two figures has no formula version, and inventing one \
is refused.
8. A `reported` or `explanatory` sentence carries no calculation and at least one citation. A \
citation names a passage id from PASSAGES and a `quote` that occurs in that passage exactly \
once, character for character. Quote the table row the figure was read from.
9. Cite a span only in a sentence that binds a figure read from it. Do not carry a citation \
another sentence already used into a sentence that binds nothing - a citation repeated to \
decorate a second claim is provenance the passage does not supply.
10. A difference between two percentages is measured in **percentage points**, never in \
percent: write "15.9 percentage points", never "15.9%". A `%` figure beside a word like rose, \
fell, up or down is refused as unresolvable.
11. Never write a superlative or a uniqueness claim (only, sole, first, last, never, always, \
worst, best, largest, smallest, record), an absence claim (has not, did not, no longer), or an \
ordering of two items (before, after, until, since). Nothing you have been shown can support \
one.
12. One comparison between two figures (higher, lower, better, worse, more, less) is allowed, \
in a `calculated` sentence and nowhere else. Set `operation` to `compare_levels`, list the two \
`input_observation_ids` **in the order the sentence names them**, and set `expression` to \
`left < right` when the sentence says the first is lower and `left > right` when it says the \
first is higher. Name each figure's metric on its own side of the comparing word, use one \
comparing word in the sentence, and make `result_rendered` the size of the gap. \
"GAAP gross margin was 15.9 percentage points lower than adjusted gross margin" lists GAAP \
first, then adjusted, with `left < right`.
13. Never write about the future: no expectation, guidance, outlook, forecast, target or plan.
14. Write about the subject and no one else. No competitor, no index, no "the market", no "the \
industry", no "peers".
15. State every warning listed under REQUIRED WARNINGS, using one of the phrases it lists.
16. Write every counterpoint the plan lists, resting on the same ids the plan names.
17. The title states no claim of its own: no figure, no superlative, no comparison, no cause. \
It may name the period the post is about, written in the compact form the candidate id uses \
(2022Q3). That compact form belongs in the title only - inside a sentence, a period is written \
with the period surface the FACTS section gives you.
18. COMPANY IDENTITY, METRIC SEMANTICS and COMPARISON RULES tell you what the subject is, what \
each figure means and which figures may be set against which. They are definitions, not \
evidence: they carry no figure you may write and no passage you may cite, and a sentence that \
states one of them still needs its own citation like any other. Where a line says NOT \
AVAILABLE, the corpus does not hold that answer and neither do you - write nothing that needs \
it. In particular, write nothing about what the company does, sells, or competes in.

Answer with the JSON object the schema describes and nothing else.\
"""


def writer_system(style: StyleProfile) -> str:
    """The persona and the style profile, as two labelled sections of one system message.

    §12: *"It is passed as a distinct system-prompt section and is never mixed with the
    evidence. A style change must not be able to change a number."* Concatenating here rather
    than interpolating into `WRITER_SYSTEM` keeps the rules one immutable string that a style
    profile has no way to edit.
    """
    lines = [
        WRITER_SYSTEM,
        "",
        "STYLE PROFILE " + style.profile_id,
        "  voice: " + style.voice,
        "  sentences: " + style.sentence_length,
    ]
    lines.extend("  " + convention for convention in style.house_conventions)
    lines.append("")
    lines.append("Style governs wording only. It may never change a figure, a period, a metric "
                 "or a citation.")
    return "\n".join(lines)


def writer_schema() -> dict[str, Any]:
    """§12's draft shape, inside §15.3's portable subset.

    **`calculation` is an array of zero or one, and that is a workaround stated rather than
    hidden.** §15.3 permits no `null` type and no `anyOf`, and requires every property, so
    *"a calculation or nothing"* cannot be expressed as a nullable object. An empty array is the
    only portable spelling of absence, and `writer.draft_from` refuses a second element rather
    than picking one. `formula_version_id` is a string for the same reason and `""` means null —
    an arithmetic derivation the ontology declares no formula for. `period_surface` is required
    and may be `""`: §15.3 requires every property, and a derivation whose sentence names no
    period declares none, which §13.1 then refuses if a period word is in the text after all.

    Character offsets are absent by design: the model declares `rendered` and `quote`, and code
    locates them (see `WRITER_SYSTEM`, rules 3 and 6).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "sentences"],
        "properties": {
            "title": {"type": "string"},
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "kind", "fact_bindings", "calculation", "citations"],
                    "properties": {
                        "text": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": [member.value for member in SentenceKind],
                        },
                        "fact_bindings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["fact_id", "rendered", "metric_surface",
                                             "period_surface"],
                                "properties": {
                                    "fact_id": {"type": "string"},
                                    "rendered": {"type": "string"},
                                    "metric_surface": {"type": "string"},
                                    "period_surface": {"type": "string"},
                                },
                            },
                        },
                        "calculation": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["operation", "input_observation_ids", "expression",
                                             "result_rendered", "formula_version_id",
                                             "period_surface"],
                                "properties": {
                                    "operation": {
                                        "type": "string",
                                        "enum": list(WRITER_OPERATIONS),
                                    },
                                    "input_observation_ids": {
                                        "type": "array", "items": {"type": "string"}},
                                    "expression": {"type": "string"},
                                    "result_rendered": {"type": "string"},
                                    "formula_version_id": {"type": "string"},
                                    "period_surface": {"type": "string"},
                                },
                            },
                        },
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["passage_id", "quote"],
                                "properties": {
                                    "passage_id": {"type": "string"},
                                    "quote": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def metric_surfaces_for(package: StoryEvidencePackage, metric_id: str) -> tuple[str, ...]:
    """Surfaces that name this metric and no other metric in this package (§13.5).

    A surface is dropped when it is a sub-phrase of some *other* package metric's surface —
    `"gross margin"` inside `"adjusted gross margin"` — because §13.5 resolves by longest match
    and the shorter one is exactly the ambiguity the section is about. Order is the metric's own
    (`label`, then `aliases`), deduplicated on the normalised form, so a rendering is stable
    across two builds of one package.

    An empty result is a real answer: the writer is then told not to name that metric, which is
    the honest outcome for a package whose two metrics share every surface.
    """
    own = next((metric for metric in package.metrics if metric.metric_id == metric_id), None)
    if own is None:
        return ()
    others = {
        _normalised_phrase(surface)
        for metric in package.metrics if metric.metric_id != metric_id
        for surface in (metric.metric_id, metric.label, *metric.aliases)
    }
    kept: list[str] = []
    seen: set[str] = set()
    for surface in (own.label, *own.aliases):
        phrase = _normalised_phrase(surface)
        if not phrase or phrase in seen:
            continue
        if any(f" {phrase} " in f" {other} " for other in others if other != phrase):
            continue
        seen.add(phrase)
        kept.append(surface)
    return tuple(kept)


def _normalised_phrase(surface: str) -> str:
    return " ".join(surface.replace("_", " ").lower().split())


#: The ordinal a quarter is written with, and the day its last month ends on. A table and not
#: arithmetic over month numbers, for `period_grammar`'s own reason: the failure mode of an index
#: is silent, and the first draft of this table had September ending on the 31st.
_QUARTERS: Mapping[int, tuple[str, int, int]] = {
    1: ("first", 3, 31), 4: ("second", 6, 30), 7: ("third", 9, 30), 10: ("fourth", 12, 31)}

_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")


def period_surface_for(fact: PackagedFact) -> str | None:
    """The one surface §13.4's grammar accepts for this fact's own endpoints, or `None`.

    Derived from `period_start`/`period_end`/`instant_date` and never from `period_key`: the key
    is a label and the grammar resolves to endpoints, so a surface built from the key would be
    an assertion that the two agree. `None` for a window the closed grammar has no form for —
    the writer is then told that fact may not be named, which is §13.4's refusal reached before
    the sentence is written rather than after.
    """
    if fact.instant_date:
        moment = _as_date(fact.instant_date)
        if moment is None:
            return None
        return f"{_MONTH_NAMES[moment.month - 1]} {moment.day}, {moment.year}"
    start, end = _as_date(fact.period_start), _as_date(fact.period_end)
    if start is None or end is None or start.year != end.year:
        return None
    if start.day == 1 and start.month in _QUARTERS:
        ordinal, last_month, last_day = _QUARTERS[start.month]
        if (end.month, end.day) == (last_month, last_day):
            return f"the {ordinal} quarter of {start.year}"
    if (start.month, start.day) == (1, 1):
        if (end.month, end.day) == (12, 31):
            return f"fiscal {start.year}"
        if (end.month, end.day) == (9, 30):
            return f"the nine months ended September 30, {start.year}"
        if (end.month, end.day) == (6, 30):
            return f"the first half of {start.year}"
    return None


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def writer_prompt(
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    passages: Sequence[PackagedPassage],
    *,
    length_target: int = DEFAULT_LENGTH_TARGET,
) -> str:
    """The plan, the facts and the writer's own passage slice, rendered deterministically.

    `passages` is an argument because §10.2.1 point 3 makes the slice a rule rather than a
    rendering choice — `writer.writer_passages` owns it, and a prompt that derived its own would
    be a second answer to *"what may this model cite?"*.

    The style profile is **not** a parameter. §12 requires it to be a separate system-prompt
    section, and a signature with nowhere to put it is what makes that structural.
    """
    lines: list[str] = [
        "CANDIDATE  " + package.candidate_id,
        "PACKAGE    " + package.package_id,
        "SUBJECT    " + _subject(package),
        "",
        IDENTITY_HEADING,
    ]
    lines.extend(_identity_lines(package))
    lines += ["", "PLAN"]
    lines.extend(_plan_lines(plan))
    lines += ["", "REQUIRED WARNINGS"]
    lines.extend(_required_warning_lines(plan, package))
    lines += ["", "FACTS"]
    lines.extend(_writer_fact_lines(package))
    lines += ["", SEMANTICS_HEADING]
    lines.extend(_semantic_lines(package))
    lines += ["", COMPARISON_HEADING]
    lines.extend(_comparability_lines(package))
    lines += ["", "FORMULA WINDOWS (the only version ids a calculation may name)"]
    lines.extend(_formula_window_lines(package))
    count = len(passages)
    lines += ["", f"PASSAGES ({count} whole passage{'' if count == 1 else 's'}; every one is a "
                  "passage a fact above was read from, and a citation may name no other)"]
    lines.extend(_writer_passage_lines(passages))
    lines += ["", f"LENGTH  about {length_target} sentences."]
    return "\n".join(lines)


def _plan_lines(plan: EditorialPlan) -> list[str]:
    lines = [
        "  thesis        " + plan.thesis,
        "  why it matters " + plan.why_it_matters,
    ]
    if plan.uncertainty:
        lines.append("  uncertainty   " + plan.uncertainty)
    for position, section in enumerate(plan.structure, start=1):
        lines.append(f"  structure {position}. {section}")
    # Stated as an instruction rather than as a field value: `forbidden` is the answer to a
    # question the writer would otherwise answer by inference, and §13.10 A bans
    # LLM-originated causation unconditionally.
    if plan.causal_language is CausalLanguage.FORBIDDEN:
        lines.append("  causation     FORBIDDEN - never state or imply why anything happened, "
                     "in any sentence")
    else:
        lines.append("  causation     REPORTED ONLY - you may state a cause only in an "
                     "explanatory sentence that names the filing as the source and cites the "
                     "span that says it")
    for point in plan.key_points:
        lines.append(f"  key point [{point.statement_class.value}] {point.claim}")
        lines.extend(_id_lines(point.required_fact_ids, point.required_citation_passage_ids))
    for counterpoint in plan.counterpoints:
        lines.append("  counterpoint (must appear in the post) " + counterpoint.claim)
        lines.extend(_id_lines(counterpoint.required_fact_ids,
                               counterpoint.required_citation_passage_ids))
    for claim in plan.prohibited_claims:
        lines.append("  never claim   " + claim)
    return lines


def _id_lines(fact_ids: Sequence[str], passage_ids: Sequence[str]) -> list[str]:
    lines: list[str] = []
    if fact_ids:
        lines.append("      facts     " + ", ".join(fact_ids))
    if passage_ids:
        lines.append("      passages  " + ", ".join(passage_ids))
    return lines


def _required_warning_lines(
    plan: EditorialPlan, package: StoryEvidencePackage
) -> list[str]:
    if not plan.required_warnings:
        return ["  (none)"]
    detail = {warning.code: warning.detail for warning in package.warnings}
    lines: list[str] = []
    for code in plan.required_warnings:
        lines.append(f"  {code}  {detail.get(code, '')}".rstrip())
        phrases = WARNING_QUALIFIER_PHRASES.get(code)
        # A code with no declared phrase is a refusal at §13 whatever the post says, and telling
        # the writer so is better than letting it invent a paraphrase that cannot satisfy one.
        if phrases:
            lines.append("      say it with one of: " + " | ".join(phrases))
        else:
            lines.append("      this code declares no accepted phrase and cannot be satisfied")
    return lines


def _writer_fact_lines(package: StoryEvidencePackage) -> list[str]:
    if not package.facts:
        return ["  (none)"]
    lines: list[str] = []
    for fact in package.facts:
        lines.append(f"  [{fact.observation_id}]")
        printed = f'  printed "{fact.printed_form}"' if fact.printed_form else ""
        lines.append(f"      {fact.metric_id}  {fact.value} {fact.unit}{printed}")
        surfaces = metric_surfaces_for(package, fact.metric_id)
        if surfaces:
            lines.append("      metric surface: write one of "
                         + ", ".join(f'"{surface}"' for surface in surfaces))
        else:
            lines.append("      metric surface: no surface names this metric uniquely in this "
                         "package - do not write about this fact")
        period = period_surface_for(fact)
        if period:
            lines.append(f'      period surface: write exactly "{period}"')
        else:
            lines.append("      period surface: this period has no permitted surface - do not "
                         "write about this fact")
        if fact.passage_id:
            quoted = f' quoting "{fact.quoted_text}"' if fact.quoted_text else ""
            row = f' from row "{fact.row_label}"' if fact.row_label else ""
            lines.append(f"      read from passage {fact.passage_id}{row}{quoted}")
        elif fact.evidence_source_id:
            lines.append(f"      evidenced by {fact.evidence_source_id} (no filed passage; "
                         "this fact cannot be cited and must not be written)")
        if fact.warning_codes:
            lines.append("      warnings " + ", ".join(fact.warning_codes))
    return lines


def _formula_window_lines(package: StoryEvidencePackage) -> list[str]:
    """§10's `formula_windows[]`, and *"(none)"* is the load-bearing case.

    §13.9 checks `formula_version_id` against this section and refuses a version it does not
    hold; the demo package holds none, because a cross-metric gap is arithmetic rather than an
    ontology identity. Rendering the empty section is what makes rule 7's *"write ''"*
    checkable by the model rather than a rule about a section it cannot see.
    """
    if not package.formula_windows:
        return ["  (none - every calculation here is arithmetic, so write \"\")"]
    return [
        f"  {window.version_id}  for {window.metric_id}  "
        f"valid {window.valid_from or 'always'}..{window.valid_to or 'now'}"
        for window in package.formula_windows
    ]


def _writer_passage_lines(passages: Sequence[PackagedPassage]) -> list[str]:
    if not passages:
        return ["  (none)"]
    lines: list[str] = []
    for passage in passages:
        head = f"  [{passage.passage_id}]  document {passage.document_id}"
        if passage.passage_kind:
            head += f"  kind {passage.passage_kind}"
        lines.append(head)
        if passage.heading_path:
            lines.append("      section: " + " > ".join(passage.heading_path))
        # Whole, not excerpted: §10.2.1 point 2 excerpts explanatory and counter-evidence
        # passages and never a passage a fact is bound to, because §13.7's Rule A needs the
        # entire table to reconstruct a cell.
        lines.append(f"      whole passage, {passage.char_count} characters")
        lines.append("      " + passage.text.replace("\n", "\n      "))
    return lines
