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

**The first two of those three now live in `story/core/renderings.py`** and are re-exported here
under the names above (S1 of `docs/2026-08-23-deterministic-draft-compiler/`). The draft compiler
is a different stage and may not import this module, so a rendering rule only this module could
reach would have had to be written a second time — which is exactly how the period rule came to
have two spellings before the move. What stays this module's is the *instruction*: which line is
printed, what it tells the model to do, and what it says when a row has no legal surface.

**The writer no longer declares arithmetic at all, and `WRITER_OPERATIONS` is gone with the
field it constrained** (DETERMINISTIC_FACT_TOOLS §5). Until this version a `calculated` sentence
carried a `calculation` — an operation from a six-member enum, two input observation ids, an
expression string, a rendered result, a formula version and a period surface — and the number in
the prose was the model's arithmetic with the verifier checking its homework. It is now code's:
the planner *requests* a derivation, `story/stages/derivation/` validates and executes it, and
the writer binds the result with an ordinary `FactBinding` naming the derived fact's id.

**The measurement that forced the change is not the one the brief predicted, and it is worth
stating precisely because it decides the shape.** §2 of the plan re-read the two recorded runs
of `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`: `$446 million`
was **covered** — `Calculation.result_rendered` is §13.1's third covering mechanism — and the
arithmetic **recomputed cleanly** (`calculation_ledger[0].recomputed_value = 446000000.0`). What
refused the draft was `unbound_numeral` on the literal **`2022`**, because a `calculated`
sentence carries no `fact_bindings` and the model had left `Calculation.period_surface` empty
while its own text read *"in the third quarter of 2022"*. So the failure was never the
arithmetic: it was one optional-looking string the model had to remember to fill, and the note
that used to sit at rule 5 records the same defect being half-repaired once already — the first
wording told the writer *not to name the period*, which that note itself calls *"the wrong end
to fix it from"*. A derived fact bound as an ordinary `FactBinding` carries `period_surface`
**per binding**, and `_derived_fact_lines` prints the surface code minted from the derivation's
own `to_period`. The model cannot forget a field it no longer writes.

**What that removes from this module, and what it does not.** The `calculation` object, its
operation enum, the FORMULA WINDOWS section and rules 5, 6, 7 and 12's expression clause are all
gone: nothing in the writer's grammar names an operation, an input order, an expression or a
formula version any more, so none of them can be got wrong. `Calculation` stays in
`story/core/models.py` for artifact back-compatibility and §13 refuses a draft that carries one.
What survives unchanged is the *comparison* the demo candidate is about — *"the GAAP gross
margin was 15.9 percentage points lower than the adjusted gross margin"* — because
`compare_levels` is one of §4.1's seven operations, the derivation stage computes the gap and
its direction word, and the writer binds it. §13.14's three-way recomputation still applies to
what the sentence says; it simply no longer applies to a number the model worked out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from story.core.models import (
    CausalLanguage,
    DerivationOperation,
    DerivationRequest,
    DerivedFact,
    EditorialPlan,
    EvidenceScopeFact,
    PackagedFact,
    PackagedPassage,
    SentenceKind,
    StatementClass,
    StoryEvidencePackage,
    UnusableReason,
)
from story.core.renderings import (
    DIRECTIONAL_SEMANTICS,
    derived_figure,
    metric_surfaces,
    observed_figure,
    period_surface_of_fact,
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
#:
#: **1.2.0**: DETERMINISTIC_FACT_TOOLS §5. The schema gained `requested_derivations[]`, the
#: rendering gained the DERIVATIONS OFFERED section, and rule 2 stopped being *"do not compute a
#: new one"* and became *"do not compute one — ask for it"*. The question genuinely changed: a
#: planner under 1.1.0 was never shown a derivation it could request and had no field to request
#: one in, so every generation recorded under it answers a prompt with no offer set. Bumped
#: rather than re-keyed, which is §9's whole point — S12's two re-keys were valid precisely
#: because the request was unchanged, and this one is not.
PLANNER_PROMPT_VERSION = "1.2.0"

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
figure in different units, and never work one out yourself - not a change, not a percentage, \
not a gap, not a ratio. To use a figure that is not in FACTS, ask for it: put a line from \
DERIVATIONS OFFERED into `requested_derivations` and code computes it for you.
3. `requested_derivations` holds only lines copied from DERIVATIONS OFFERED, with `operation`, \
`from_fact_id` and `to_fact_id` exactly as that section spells them. A triple that is not on \
that list is rejected and the plan never reaches the writer, so do not adjust one, do not swap \
the two ids around, and do not invent an operation. Ask only for what a key point actually \
needs; an empty list is a correct answer for a plan that states levels and nothing else. Each \
requested derivation becomes one fact the writer may state, and the writer may state no \
computed figure you did not ask for.
4. Say why something happened only if a quoted span in the package says so. The \
`causal_language` field is fixed for you and you may not choose it.
5. If the package carries counter-evidence, every counterpoint you write must rest on at least \
one id drawn from it, and every counter-evidence item you do not use must appear in \
`unusable_evidence` with one of the five listed reasons. A counterpoint grounded in nothing is \
not a counterpoint.
6. `required_warnings` may name only warning codes listed in the package's WARNINGS section. \
The writer is refused if it drops one.
7. `statement_class` is `reported` for a figure quoted from a filing, `calculated` for a \
figure code computed from two of them under rule 3, and `explanatory` for a claim resting on a \
passage rather than a number.
8. `prohibited_claims` are claims the writer must not make even though the evidence is nearby \
- name them plainly.
9. COMPANY IDENTITY, METRIC SEMANTICS and COMPARISON RULES tell you what the subject is, what \
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

    **`requested_derivations[]` is package-independent and its enum is all seven operations**
    (DETERMINISTIC_FACT_TOOLS §4.1), which looks like an exception to this module's rule and is
    not one. The rule is that a *package-dependent* answer must be checked by code so that one
    wrong answer does not mean two different things in two packages; `operation` is a closed
    vocabulary the same in every package, exactly like `statement_class`. Whether a given
    **triple** is available here is package-dependent, and that is checked by code —
    `planner.plan_violations` refuses `derivation_not_offered` against the list the prompt
    printed. Pinning the enum per package to the operations this offer set happens to contain
    would have made the two providers' grammars differ per candidate for no gain: it still
    could not constrain the ids, which are the half a model gets wrong.

    **Three fields and no fourth, deliberately.** No expression string — §10 rejects a generic
    `evaluate(expression)` as *"arbitrary Python by another name"*. No result: a request
    carrying one would be the model doing the arithmetic with code checking its homework, which
    is the arrangement §1 replaces. And no native tool-calling on either provider, because two
    tool protocols keyed under one replay store is a request shape that would differ per
    provider while `request_identity` already digests this schema.
    """
    id_array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "thesis", "why_it_matters", "key_points", "counterpoints", "requested_derivations",
            "required_warnings", "causal_language", "uncertainty", "structure",
            "prohibited_claims", "unusable_evidence",
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
            "requested_derivations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operation", "from_fact_id", "to_fact_id"],
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [member.value for member in DerivationOperation],
                        },
                        "from_fact_id": {"type": "string"},
                        "to_fact_id": {"type": "string"},
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


def planner_prompt(
    package: StoryEvidencePackage,
    *,
    offered: Sequence[DerivationRequest] = (),
) -> str:
    """The package as the planner sees it. Deterministic, and it says what it cut.

    Every id is printed exactly as it will have to be spelled back, because the code check
    after the call compares strings and a rendering that prettified an id would make the model
    fail a rule it was never shown. The offer set is printed under the same rule and it is the
    reason the rule now has teeth twice: `planner.plan_violations` compares a requested triple
    against this list field by field, so a rendering that abbreviated an operation or tidied an
    id would refuse the model for copying back what it was shown.

    **`offered` is an argument and is not computed here** (DETERMINISTIC_FACT_TOOLS §4.3).
    `offers(package, candidate)` lives in `story/stages/derivation/`, which this stage may not
    import (`test_no_stage_imports_another_stage`), and the composition root passes the same
    tuple to this function and to the executor. That is not only an import rule: a list computed
    twice is a list that can drift, and §4.3's promise is that the planner may request only from
    *the list it was shown*. Defaulted to empty so a caller with no candidate — a rendering
    test, a package with no derivable pair — renders a section that says so.
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
    lines += ["", _offer_heading(offered)]
    lines.extend(_offer_lines(offered, package))
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
# DETERMINISTIC_FACT_TOOLS §4.3 — the offer set, printed
#
# **This section can be printed at all because `offers(package, candidate)` takes no model
# input.** It is a pure function of the package and the candidate, so the list is the same on
# every build of one package, `request_identity` digests it stably, and the run replays. A
# calculator the model described in words could not be printed, checked or replayed, which is
# §10's argument against one restated from the rendering end.
#
# **Each triple is printed as the three field names it must be spelled back under.** The check
# after the call is `is_offered`, an equality over `DerivationRequest`, so `operation`,
# `from_fact_id` and `to_fact_id` are the words the model has to produce and they are the words
# it is shown. This is `planner_prompt`'s existing rule — every id printed exactly as it will
# have to be spelled back — applied to the one field where a *triple* rather than a single id is
# the unit of comparison.
#
# The second line of each entry is a **gloss and carries no id**, for the reason the METRIC
# SEMANTICS section carries none: it exists so a 9B model can tell two offers apart without
# parsing two 70-character digests, and a value it could copy out of would be a second place to
# read a figure from. Values are the package's own, printed as FACTS prints them.
# ---------------------------------------------------------------------------------------

OFFER_HEADING = "DERIVATIONS OFFERED"


def _offer_heading(offered: Sequence[DerivationRequest]) -> str:
    count = len(offered)
    if not count:
        return (f"{OFFER_HEADING} (none; this package supports no derivation, so "
                "requested_derivations must be empty)")
    return (f"{OFFER_HEADING} ({count} available; copy a line into requested_derivations "
            "exactly as it is written, or ask for none)")


def _offer_lines(
    offered: Sequence[DerivationRequest], package: StoryEvidencePackage
) -> list[str]:
    if not offered:
        return ["  (none)"]
    by_id = {fact.observation_id: fact for fact in package.facts}
    lines: list[str] = []
    for request in offered:
        operation = getattr(request.operation, "value", request.operation)
        lines.append(f'  operation "{operation}"  from_fact_id "{request.from_fact_id}"  '
                     f'to_fact_id "{request.to_fact_id}"')
        gloss = _offer_gloss(by_id.get(request.from_fact_id), by_id.get(request.to_fact_id))
        if gloss:
            lines.append("      " + gloss)
    return lines


def _offer_gloss(from_fact: PackagedFact | None, to_fact: PackagedFact | None) -> str:
    """Which two readings a triple is over, in `(from, to)` order and with no id in it.

    Empty when either side is not in this package. That is unreachable through a real offer set
    — `offers` enumerates the package's own facts — and the branch exists because this function
    is also handed whatever a *replayed* plan named, where the ids came from another package.
    """
    if from_fact is None or to_fact is None:
        return ""
    return f"{_offer_side(from_fact)} -> {_offer_side(to_fact)}"


def _offer_side(fact: PackagedFact) -> str:
    return f"{fact.metric_id} {fact.period_key} ({fact.value} {fact.unit})"


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
#:
#: **1.3.0**: three codes gained accepted phrases (see `WARNING_QUALIFIER_PHRASES`), so a plan
#: that requires one of them now renders *"say it with one of: …"* where it rendered *"this code
#: declares no accepted phrase and cannot be satisfied"*. That is a change in what the writer is
#: told it may do, which is what this constant tracks; no wording of any rule moved.
#:
#: **1.4.0**: TABLE_CELL_CITATIONS S4 — the citation contract stopped being a retyped byte string
#: and became a deterministic evidence handle. `writer_schema`'s `citations[]` item is now
#: `{"evidence_id"}` where it was `{"passage_id", "quote"}`; `_writer_fact_lines` renders
#: `evidence id "ev:…"` where it rendered `quoting "7"`; rules 8 and 9 are rewritten, and the
#: PASSAGES heading no longer tells the writer it is choosing a passage. **The old wording and
#: the old gate contradicted each other**, so this is a bump that removes an impossible
#: instruction rather than one that adds a capability: `quoted_text` occurs more than once in
#: its own passage for 523 of 2,704 observations, and `writer.py`'s `len(occurrences) != 1`
#: refused exactly the string the prompt asked for. No generation recorded under 1.3.0 answers
#: this question, and every one of them is unreachable by construction — the system message, the
#: prompt and the schema are all digest inputs to `request_identity`.
#:
#: **2.0.0**: DETERMINISTIC_FACT_TOOLS §5 — the writer stopped declaring arithmetic. `calculation`
#: is gone from the schema and `WRITER_OPERATIONS` with it; the FORMULA WINDOWS section is gone;
#: rules 5, 6 and 7 are gone, and rule 12's expression clause with them. A derived value is now
#: bound by an ordinary `FactBinding` naming a `DerivedFact` code computed, and its
#: `period_surface` is printed for the writer to copy rather than remembered. **A major bump and
#: not a minor one**, because this is the first version to *remove* a field: a draft recorded
#: under 1.4.0 carries an object the schema no longer admits, so those rows are unreachable in
#: both directions rather than only forward. §9 re-records both fixtures live for exactly this
#: reason — the recorded answers are answers to a different question, and re-keying them would
#: be a lie about what was asked.
#:
#: **2.1.0**: two rendering repairs and rule 5's wording, both measured live against
#: Qwen3.5-9B-Q4_K_M on 2026-08-19 rather than reasoned about. The schema is untouched, which is
#: why this is a minor bump — a 2.0.0 answer is still *shaped* like a 2.1.0 one — and the request
#: digest still moves, so the stores are re-recorded rather than re-keyed.
#:
#: * `metric_surfaces_for` now offers the metric **id** as a surface. It offered only `label` and
#:   `aliases`, so `gaap_gross_margin` — whose label `"Gross Margin"` is a sub-phrase of
#:   `"Adjusted Gross Margin"` — had **no** offered surface, and both its FACTS row and the
#:   DERIVED FACTS row computed from it printed *"do not write about this fact"* for a metric the
#:   plan required the writer to state. `MetricAliasIndex` has always accepted the id form, and
#:   the committed accepted draft binds `"GAAP Gross Margin"` through it. Live measurement: with
#:   the contradiction removed, the three `metric_surface` values went from
#:   `percent, percent, percentage_points` to `gaap gross margin, Adjusted Gross Margin,
#:   gaap gross margin` on the same server, same package, same plan.
#: * The DERIVED FACTS row hands the figure over as a **quoted string a sentence can carry**
#:   rather than as `{result} {unit}` under a `metric_id`, which is a FACTS reading's shape.
#:   Three live drafts measured the difference. Shown `-15.9 percentage_points`, Qwen put
#:   `"15.9 percentage_points"` in `rendered` while writing *"15.9 percentage points"* in its
#:   own text — a declared span that does not occur in the sentence. Shown `$446000000.0`, it
#:   wrote `"446000000.0 USD"`, the neighbouring FACTS row's shape, which carries no unit
#:   surface and is `derived_unit_mismatch` on a derived fact. Shown `$446 million`, it wrote
#:   `$446 million`. So the row names the refused spelling beside the accepted one, and a
#:   monetary figure is offered at the scale its inputs were filed at. See the comment block
#:   above `_derived_heading`.
#:
#: End to end on the two candidates: the demo's cross-metric divergence went from three
#: `metric_surface_unresolved` to **accepted**, and
#: `cand:metric-move:adjusted-gross-profit:…` — the candidate DETERMINISTIC_FACT_TOOLS §2 exists
#: for — from `unbound_numeral` + `derived_unit_mismatch` to **accepted**.
#:
#: **2.2.0**: one rendering repair, and it is 2.1.0's second bullet finishing its own argument.
#: That bullet taught the DERIVED FACTS row to print money at the scale its inputs were filed
#: at, and left the FACTS row printing `556000000.0 USD`. Two shapes for one kind of thing, and
#: at H2 the model reconciled them the wrong way round: asked for two derivations instead of
#: one, it scaled the FACTS reading itself to write *"Adjusted Gross Profit of 556 million
#: USD"*, then copied that spelling onto a derived row whose own line read `write exactly "$446
#: million" - those characters, never "446000000.0 USD"`. It obeyed the same instruction on the
#: percentage row. `446 million USD` carries no currency surface — legal against an observation,
#: `derived_unit_mismatch` against a derivation — and that one span was the whole difference
#: between accepted and rejected. `_observed_figure` does the scaling the model was doing by
#: hand, through the function the derived row already used.
#:
#: **A minor bump and a narrow one.** The schema is untouched, no rule's wording moved, and the
#: new line is printed **only** for a `USD` reading filed at a scale word — so a package of
#: percentages renders byte-identically.
#:
#: **No fixture was re-recorded, and the reason corrects an assumption H3 started from.** This
#: constant is **not** a `request_identity` input — that digest is over the prompt *text*, the
#: system message, the schema and seven request settings — and the demo candidate carries two
#: `percent` facts, so its writer prompt does not move at all. A live Qwen run after the change
#: returned `generations.jsonl 8645d1a95a533b29…`, byte-for-byte the committed store, and the
#: `openai/` store still replays. What the bump does is make the change *visible* in a manifest
#: and in `Draft.prompt_version`, which is what a version is for; 2.0.0 had to re-record because
#: the **schema** moved, and that is a different thing.
WRITER_PROMPT_VERSION = "2.2.0"

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

#: What counts as having stated a required warning (§13's *"required qualifiers present"*).
#: **Restated from `story/stages/verification/deterministic.py`'s
#: `REQUIRED_WARNING_QUALIFIERS`, and `test_the_writer_is_shown_the_same_warning_phrases_the_verifier_requires`
#: asserts the two are identical.** A stage may not import another stage, and the alternative to
#: a checked copy is a writer refused for silence nobody told it how to break.
#:
#: The three S6 rows and the reason each is a repair rather than a new rule are documented at the
#: table this copies; the codes without a phrase are named, with a reason each, in that module's
#: `QUALIFIER_NOT_DECLARED`. Nothing about them is copied here: the writer is only ever shown the
#: phrases for the codes its own plan requires, and *"this code declares no accepted phrase"* is
#: already what `_writer_warning_lines` prints for the rest.
WARNING_QUALIFIER_PHRASES: Mapping[str, tuple[str, ...]] = {
    "filing_date_unknown": ("filing date", "date it was filed", "as-filed date",
                            "when it was filed"),
    "counter_evidence_same_document": ("same filing", "elsewhere in the filing",
                                       "another table in the same"),
    "conflicting_values": ("conflict", "two values", "two readings", "also reported"),
    "warned_observation": ("flagged", "carries a warning", "data-quality"),
    "fact_conflict_disclosed": ("conflict", "two values", "two readings", "also reported"),
    "unpreferred_source_lane": ("flagged", "carries a warning", "data-quality"),
    "single_source": ("one filing", "one document", "a single filing", "a single document",
                      "single source"),
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
#: Rules 3 and 6 are what make the draft checkable at all, and they are no longer the same kind
#: of rule. §12 specifies `char_start`/`char_end` on every binding and citation, and asking a 9B
#: model to count characters would fail on every call, so neither asks for an offset.
#:
#: * **Rule 3 is a substring rule and stays one.** `rendered` is a run of the model's *own*
#:   sentence, `writer.draft_from` locates it there, and a rendering occurring twice is refused
#:   rather than chosen between. The declaration is the model's and the verifier never guesses
#:   one, which is what §12 protects. Table flattening does not touch it.
#: * **Rule 6 was a substring rule and it was unsatisfiable** (TABLE_CELL_CITATIONS §1.2). It
#:   asked for a `quote` occurring in the cited passage *exactly once*, and the string the prompt
#:   handed the model was `EVIDENCED_BY.quoted_text` — a bare cell value of median 4 characters,
#:   occurring more than once in its own passage for **523 of 2,704** observations and 27 times
#:   in the worst case a demo candidate actually hits *(verified live 2026-08-13)*. §12 then
#:   refused that exact string as `citation_quote_ambiguous_in_passage`, so for a fifth of the
#:   corpus **no model output satisfied both the instruction and the gate**. It is now a
#:   *token* rule: the model copies back a `PackagedFact.evidence_handle`, and code resolves the
#:   coordinates behind it through `story.core.table_cells`. The model is never asked to
#:   reproduce source text, which is the whole of the repair.
#:
#: **Rules 4, 5, 7 and 15 exist because of measured failures, and two of them are what
#: DETERMINISTIC_FACT_TOOLS §5 replaced the arithmetic rules with.** The 2026-08-04 live run
#: *(Qwen3.5-9B-Q4_K_M, this package, three identical attempts: 1,698 prompt tokens, 937
#: completion tokens, `finish_reason: stop`, ~12.8 s, byte-identical answers)* produced a
#: §12-clean draft that §13 refused five times over. Three of those five findings are now
#: **unreachable by construction rather than by instruction**, which is the point of the change:
#:
#: * `calculation_does_not_recompute`, expected `-15.9`, because the model listed
#:   `(adjusted, gaap)` and `_recompute` is `values[1] - values[0]`. Input order was a rule the
#:   first wording never stated; it is now not a rule at all, because the model no longer lists
#:   inputs. `DerivedFact.from_fact_id`/`to_fact_id` are the planner's request and code's answer.
#: * `formula_version_not_valid_for_period`, because §15.3 has no null and an unused string
#:   field is a box a model fills — it filled it with the package id. There is no such field now,
#:   and the FORMULA WINDOWS section that existed to give it something to check against is gone
#:   with it.
#: * `unbound_numeral` on `"2022"` in the calculated sentence. **This is the finding the whole
#:   change is for.** A `calculated` sentence carried no `fact_binding`, so the only place it
#:   could declare a period was `Calculation.period_surface` — one optional-looking string —
#:   and the model left it empty while writing *"in the third quarter of 2022"*. The first
#:   wording told the writer not to name the period, and the note that stood here called that
#:   *"the wrong end to fix it from"*; the second repair asked for the period on the calculation,
#:   which still rested on the model remembering a field. Rule 5 is the third: a derived fact
#:   binds like any other, `FactBinding.period_surface` is per binding, and DERIVED FACTS prints
#:   the exact words to copy. There is no field left to forget.
#:
#: The two findings that were never about arithmetic are unchanged and still stated:
#: `citation_reused_for_unrelated_claim`, twice, when the model re-cited both table spans in an
#: explanatory sentence that bound nothing — rule 7 states §13.7's predicate, reuse *and*
#: non-support, rather than banning reuse, which a two-column table legitimately needs.
WRITER_SYSTEM = """\
You are the writer for an investor post about one company's reported figures.

You are given an evidence slice, an accepted editorial plan and a style profile. You have no \
tools, no search and no access to any database: what you are shown is your entire universe. \
Follow the plan; you did not choose it.

Write the post as a list of sentences, each one carrying the evidence for what it says.

Rules:
1. Introduce no number, no date, no period and no company that is not in the FACTS, DERIVED \
FACTS or PASSAGES sections. Do not restate a figure in different units. **Work nothing out \
yourself**: you do no arithmetic here, and there is nowhere in your answer to declare any. \
Every figure you write is one this prompt prints.
2. `kind` is `reported` for a figure quoted from a filing, `calculated` for a figure from the \
DERIVED FACTS section, `explanatory` for a claim resting on a passage rather than a number, and \
`connective` for a sentence that carries no claim at all - no figure, no comparison, no \
characterisation.
3. Every numeral in a sentence must be declared. List one `fact_bindings` entry per figure: \
`fact_id` exactly as FACTS or DERIVED FACTS spells it, `rendered` the exact run of characters \
in your own `text` that holds the figure, and `metric_surface` and `period_surface` copied from \
the surfaces that fact offers. `rendered` must appear in `text` exactly once, character for \
character. An undeclared numeral is refused.
4. Use a metric surface exactly as it is offered. A shorter one names two metrics and is \
refused - write "GAAP gross margin" or "adjusted gross margin", never "gross margin".
5. A figure in DERIVED FACTS was computed by code from two figures in FACTS, and you state it \
the same way you state any other: bind it by its `fact:derived:` id, write the figure in the \
words that row's `figure:` line quotes, and copy that row's metric surface and period surface. \
`rendered` is the run of characters in your own sentence, so it holds the words you wrote and \
never a field name or a unit spelled with an underscore. Do not name the \
operation, do not write the two figures it was computed from unless a sentence binds those \
figures too, and do not restate the result in another unit or another direction: the row's \
`says` line is the direction, and reversing it makes the sentence false about a number code \
computed.
6. Every `reported` and `explanatory` sentence carries at least one citation. A citation is one \
field, `evidence_id`, and it is the `evidence id` string printed under a fact in FACTS, copied \
character for character. Never write a passage id, a character position, or any run of text \
taken out of a passage: the evidence id already names the exact cell the figure was read from, \
and code turns it into a span. An evidence id no fact above prints is refused.
7. Give a sentence the evidence id of a fact it binds. A `calculated` sentence has no evidence \
of its own - a derived fact was computed, not filed - so cite the evidence ids of the two \
FACTS rows the DERIVED FACTS row names as its inputs, and cite nothing else. Do not carry an \
evidence id another sentence already used into a sentence that binds nothing: a citation \
repeated to decorate a second claim is provenance the evidence does not supply.
8. A difference between two percentages is measured in **percentage points**, never in \
percent: write "15.9 percentage points", never "15.9%". A `%` figure beside a word like rose, \
fell, up or down is refused as unresolvable.
9. Never write a superlative or a uniqueness claim (only, sole, first, last, never, always, \
worst, best, largest, smallest, record), an absence claim (has not, did not, no longer), or an \
ordering of two items (before, after, until, since). Nothing you have been shown can support \
one.
10. One comparison between two figures (higher, lower, better, worse, more, less) is allowed, \
and only where a DERIVED FACTS row supports it. Name each figure's metric on its own side of \
the comparing word, use one comparing word in the sentence, and let the row's own words say \
which way round it is. "GAAP gross margin was 15.9 percentage points lower than adjusted gross \
margin" states a `compare_levels` row whose `says` line reads "lower than".
11. Never write about the future: no expectation, guidance, outlook, forecast, target or plan.
12. Write about the subject and no one else. No competitor, no index, no "the market", no "the \
industry", no "peers".
13. State every warning listed under REQUIRED WARNINGS, using one of the phrases it lists.
14. Write every counterpoint the plan lists, resting on the same ids the plan names.
15. The title states no claim of its own: no figure, no superlative, no comparison, no cause. \
It may name the period the post is about, written in the compact form the candidate id uses \
(2022Q3). That compact form belongs in the title only - inside a sentence, a period is written \
with the period surface the FACTS or DERIVED FACTS section gives you.
16. COMPANY IDENTITY, METRIC SEMANTICS and COMPARISON RULES tell you what the subject is, what \
each figure means and which figures may be set against which. They are definitions, not \
evidence: they carry no figure you may write and no passage you may cite, and a sentence that \
states one of them still needs its own citation like any other. Where a line says NOT \
AVAILABLE, the corpus does not hold that answer and neither do you - write nothing that needs \
it. In particular, write nothing about what the company does, sells, or competes in.
17. EVIDENCE SCOPE, where it appears, states what this package's evidence does not contain. It \
is a limit on what you may write and never a sentence to write: obey it and do not report it.

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

    **`calculation` is gone, and with it every field a model could get arithmetic wrong in**
    (DETERMINISTIC_FACT_TOOLS §5). It used to be an array of zero or one — §15.3 has no `null`
    type and no `anyOf`, so an empty array was the only portable spelling of "or nothing" — and
    inside it sat an operation, two input observation ids, an expression, a rendered result, a
    formula version and a period surface. Six fields the model filled and code then checked. A
    derived value is now stated by an ordinary `fact_bindings` entry naming a `DerivedFact` id,
    which means the *same* four fields carry it that carry a reported figure, and
    `period_surface` — the one §2 measured the writer forgetting — is printed for it to copy.
    `additionalProperties: false` is what makes the removal a refusal rather than a hope: a
    model that emits a `calculation` anyway fails `schema_violations` before a draft is built.

    **A citation is one string, `evidence_id`, and the passage id and the quote are gone**
    (TABLE_CELL_CITATIONS §3.2). Character offsets are still absent by design — a 9B model
    cannot count characters — but the substring the model *does* declare is now only ever a run
    of **its own text**: `fact_bindings[].rendered`, located by `writer.draft_from` in the
    sentence that wrote it (`WRITER_SYSTEM` rule 3). Nothing here asks the model to reproduce a
    byte of the source. It was asked to until 1.3.0, and for 523 of 2,704 observations no answer
    satisfied both the instruction and the gate: `quoted_text` is a bare cell value occurring up
    to 32 times in its own passage, and rule 8's *"a quote that occurs in that passage exactly
    once"* named a string that does not exist. The handle is a pure function of coordinates the
    package already carries, so code resolves the span and the model copies a token back
    (`WRITER_SYSTEM` rules 8 and 9).
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
                    "required": ["text", "kind", "fact_bindings", "citations"],
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
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence_id"],
                                "properties": {
                                    "evidence_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


#: **Both of these moved to `story/core/renderings.py` and are re-exported here** (S1 of
#: `docs/2026-08-23-deterministic-draft-compiler/02-IMPLEMENTATION-PLAN.md`). The prompt printer
#: and the draft compiler must offer one string, and a private function inside this stage could
#: not be the one — the compiler is a different stage and may not import this module. What is
#: kept here is the *name*: both are in `story.stages.generation.__all__` and are imported by
#: `tests/story/test_story_writer.py`, so the alias is what makes the move a move and not a
#: rename every caller has to follow.
metric_surfaces_for = metric_surfaces
period_surface_for = period_surface_of_fact


def writer_prompt(
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    passages: Sequence[PackagedPassage],
    *,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    length_target: int = DEFAULT_LENGTH_TARGET,
) -> str:
    """The plan, the facts, the derived facts and the writer's own passage slice.

    `passages` is an argument because §10.2.1 point 3 makes the slice a rule rather than a
    rendering choice — `writer.writer_passages` owns it, and a prompt that derived its own would
    be a second answer to *"what may this model cite?"*.

    `derived_facts` is an argument for a stronger version of the same reason: they are §3's
    *separate artifact*, minted by `story/stages/derivation/` from the plan's own requests, and
    they may not enter `StoryEvidencePackage.facts` at all — a package whose contents depended
    on a model call would put a model's selection inside `package_content_digest`, which is a
    `story_run_id` input. So there is nowhere in the package for this function to read them
    from, and that is deliberate rather than inconvenient.

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
    derived = [row for row in derived_facts if isinstance(row, DerivedFact)]
    lines += ["", _derived_heading(derived)]
    lines.extend(_derived_fact_lines(derived, package))
    scope = [row for row in derived_facts if isinstance(row, EvidenceScopeFact)]
    if scope:
        # Printed only when there is one, unlike every other section here. The rest of this
        # prompt renders "(none)" because an absent section would be read as an omission; §7's
        # fact is the opposite case — a *heading* with nothing under it would be an invitation
        # to write a sentence about a limit that was never established.
        lines += ["", "EVIDENCE SCOPE (what this package's evidence does not contain)"]
        lines.extend(_evidence_scope_lines(scope))
    lines += ["", SEMANTICS_HEADING]
    lines.extend(_semantic_lines(package))
    lines += ["", COMPARISON_HEADING]
    lines.extend(_comparability_lines(package))
    count = len(passages)
    # The heading no longer says "a citation may name no other", because a citation no longer
    # names a passage at all — it names an evidence id, and code resolves which passage that is
    # (§3.2). The section stays because an `explanatory` sentence rests on what the filing says
    # rather than on a figure, and because §13.7's paraphrase checks read the passage the writer
    # was shown.
    lines += ["", f"PASSAGES ({count} whole passage{'' if count == 1 else 's'}; every one is a "
                  "passage a fact above was read from. Read them; do not cite them and do not "
                  "copy text out of them - cite the evidence id printed under the fact)"]
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
        figure = _observed_figure(fact)
        if figure is not None:
            lines.append(f'      figure: write "{figure}"')
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
        # The line the whole of TABLE_CELL_CITATIONS is about. It used to end
        # `quoting "{fact.quoted_text}"`, which for 523 of 2,704 observations named a string
        # occurring more than once in the passage it named — and §12's gate refused exactly
        # that. The handle replaces it; the *quote is deliberately not printed*, because a
        # prompt that shows the model the source bytes is a prompt that invites it to retype
        # them, which is the contract this step removed.
        #
        # The two branches are exhaustive over a well-formed package: `_has_some_evidence`
        # requires a `passage_id` or an `evidence_source_id`, and a `passage_id` always mints a
        # handle. A fact reaching neither prints no evidence line and is therefore uncitable,
        # which is the safe direction to fail in.
        if fact.evidence_handle:
            # The row label is the one piece of table context that survives, and it names
            # *which* row the handle points at rather than the value the model must not retype.
            row = f', row "{fact.row_label}"' if fact.row_label else ""
            lines.append(f'      evidence id: "{fact.evidence_handle}"  (cite this fact with '
                         f"that exact string; it is passage {fact.passage_id}{row})")
        elif fact.evidence_source_id:
            lines.append(f"      evidenced by {fact.evidence_source_id} (no filed passage; "
                         "this fact cannot be cited and must not be written)")
        if fact.warning_codes:
            lines.append("      warnings " + ", ".join(fact.warning_codes))
    return lines


#: `story.core.renderings.observed_figure`, under the name this module's own tests import and
#: the docstrings above still name. The rendering rule is the compiler's too, so it moved; the
#: *offer* — a `figure:` line printed only where there is a scaled form — stays here, because
#: what a prompt tells a model to do is this module's question and not `core`'s.
#:
#: **Offered rather than mandated, and the asymmetry with `_derived_figure_line` is real.**
#: §13.2 admits several surfaces for an *observed* numeral and admits only the currency-symbol
#: one for a derived USD result, so the derived row says *"write exactly … never …"* and the
#: FACTS row says *"write"*. Saying `never "556000000.0 USD"` there would be false: that spelling
#: is legal on an observation — `legal_renderings` offers it as the second form — and this
#: module's rule is that a wording names a real failure.
_observed_figure = observed_figure


# ---------------------------------------------------------------------------------------
# DETERMINISTIC_FACT_TOOLS §4.4 and §7 — what code computed, rendered for the writer
#
# **Printed with the same *fields* as FACTS and deliberately not in the same *shape*, which is a
# correction measured live on 2026-08-19.** The whole change is that a derived value stops being a
# special sentence the model declares arithmetic in and becomes an ordinary figure it binds, so
# each row prints an id to bind, a figure to write, a metric surface and a period surface — the
# same four things `_writer_fact_lines` prints, in the same order. What that first wording also
# printed was `{result} {unit}` in the machine's spelling, one line below a `metric_id`, exactly
# as a FACTS row prints `{value} {unit}`. A FACTS row survives that because its unit *is* the word
# a sentence uses (`percent`); a derived row does not, because `percentage_points` is not.
# Measured: Qwen copied `"15.9 percentage_points"` into `rendered` while writing *"15.9 percentage
# points"* in its own text, so the declared span did not occur in the sentence at all.
#
# So the figure is now handed over as one **quoted string to write**, on a line shaped like the
# two `write …` lines under it rather than like a FACTS reading. Its unit is written the way a
# sentence writes it — `_DERIVED_FIGURE_FORMATS`, and every row of that table produces a surface
# §13.2 reads the unit off, which `{result} {unit}` did not: `"446000000.0 USD"` tokenises as a
# unitless numeral and is `derived_unit_mismatch` on a derived fact, which is the second finding
# the live metric-move run earned under both providers. And a directional row prints the
# magnitude, because its `says` line already carries the direction — *"decreased by
# -446000000.0"* is a double negative in prose, and `sign_disagreement` is what §13.1 does to a
# written sign that argues with the result's.
#
# **Three differences from FACTS, each of them §6's requirement rather than a rendering choice.**
#
# * **No evidence id.** No handle is ever minted for a derived fact, because a citation must stay
#   attached to the observed facts a claim rests on. The row instead names the two FACTS rows it
#   was computed from, and rule 7 tells the writer to cite *their* evidence ids.
# * **A `says` line rather than a sign to read.** `DisplaySemantics` is a closed vocabulary code
#   chose, and it exists because the sign of `result` does not settle the direction: 46 of 46
#   canonical `direct_selling_costs` values are stored negative, so a fall in the number is a
#   rise in the cost. A writer inferring "decreased" from a minus sign would be right about this
#   candidate and wrong about that one.
# * **The period surface is `to_period`'s.** §13.4 requires a binding to a derived fact to resolve
#   a period surface agreeing with the period the claim is *about*, which is the later one — and
#   this is the line that would have prevented the `unbound_numeral` on `2022` that §2 measured.
# ---------------------------------------------------------------------------------------


def _derived_heading(derived: Sequence[DerivedFact]) -> str:
    count = len(derived)
    if not count:
        return ("DERIVED FACTS (none; the plan requested no derivation, so write no figure "
                "that is not in FACTS)")
    return (f"DERIVED FACTS ({count}; code computed each one from two FACTS rows - bind them "
            "exactly as you bind a fact above)")


def _derived_fact_lines(
    derived: Sequence[DerivedFact], package: StoryEvidencePackage
) -> list[str]:
    """One derived fact per block, with everything a `FactBinding` for it needs and nothing else.

    The metric surface is taken through `metric_surfaces_for` rather than off
    `DerivedFact.metric_surfaces`, and the difference matters on exactly the demo candidate.
    That field carries the two inputs' own `metric_label`s in `(from, to)` order, and one of
    them is `"Gross Margin"` — the surface §13.5 refuses, because `"gross margin"` is a
    sub-phrase of `"adjusted gross margin"` and the alias index resolves by longest match. The
    package-local filter is the same one every FACTS row is rendered through, so a derived fact
    and the observation it came from are offered the same words.
    """
    if not derived:
        return ["  (none)"]
    lines: list[str] = []
    for fact in derived:
        lines.append(f"  [{fact.fact_id}]")
        lines.append(f"      {fact.metric_id}, {fact.from_period} -> {fact.to_period}")
        lines.append(f"      says: {fact.display_semantics.value}")
        if fact.result is None:
            # §13.1 at the writing end: a word-valued row has no numeral, and one written for it
            # is `derived_unit_mismatch` rather than a rounding argument.
            lines.append(f'      answer: the words "{fact.result_word}" - this row carries no '
                         "figure, so write no number for it")
        else:
            lines.append("      figure: " + _derived_figure_line(fact, package))
        surfaces = metric_surfaces_for(package, fact.metric_id)
        if surfaces:
            lines.append("      metric surface: write one of "
                         + ", ".join(f'"{surface}"' for surface in surfaces))
        else:
            lines.append("      metric surface: no surface names this metric uniquely in this "
                         "package - do not write about this fact")
        if fact.period_surface_hint:
            lines.append(f'      period surface: write exactly "{fact.period_surface_hint}"')
        else:
            lines.append("      period surface: this period has no permitted surface - do not "
                         "write about this fact")
        # Named rather than resolved to their values: the writer needs them to know which two
        # evidence ids to cite (rule 7), and a value printed here would be a second place to
        # read a figure the FACTS section already prints with its own surfaces.
        lines.append(f"      computed from {fact.from_fact_id} and {fact.to_fact_id} - cite "
                     "both of their evidence ids and no others")
        if fact.warning_codes:
            lines.append("      warnings " + ", ".join(fact.warning_codes))
    return lines


#: `story.core.renderings.derived_figure`, for `_observed_figure`'s reason. The two tables that
#: used to sit here — `_DERIVED_FIGURE_FORMATS` and `_SCALE_WORDS` — moved with it, and so did
#: `_DIRECTIONAL_SEMANTICS`, which `_derived_figure_line` below still reads under its public name.
_derived_figure = derived_figure


def _derived_figure_line(fact: DerivedFact, package: StoryEvidencePackage) -> str:
    """The figure to write, and — where the machine spelling differs — the one not to.

    **The counter-example is generated rather than written, and it is the string the model
    actually produced twice** *(measured 2026-08-19)*. `{result} {unit}` is what a FACTS row
    looks like, so a writer shown a derived row beside two FACTS rows copies the neighbour's
    shape: Qwen wrote `"15.9 percentage_points"` under one wording and `"446000000.0 USD"` under
    the next, and both are `derived_unit_mismatch` because §13.2 requires a derived numeral to
    carry its unit's *surface*. Naming the refused form beside the accepted one is this module's
    own rule — *"the wording names the failure rather than the virtue"* — applied to a rendering
    instead of to a check. Omitted where the two forms coincide, so no row carries a warning
    against a mistake it cannot invite.
    """
    figure = _derived_figure(fact, package)
    magnitude = (abs(fact.result) if fact.display_semantics in DIRECTIONAL_SEMANTICS
                 else fact.result)
    machine = f"{magnitude} {fact.unit}"
    if machine == figure:
        return f'write "{figure}"'
    return f'write exactly "{figure}" - those characters, never "{machine}"'


def _evidence_scope_lines(scope: Sequence[EvidenceScopeFact]) -> list[str]:
    """§7's claim, as the sentence code minted and with no id to bind.

    The `fact_id` is deliberately not printed. It is not bindable — the row carries no result,
    no unit and no period — and printing an id beside every other bindable row would invite a
    binding the schema would then have nowhere to resolve. What the writer needs from this
    section is the constraint, and the constraint is the statement.
    """
    return ["  " + fact.statement for fact in scope]


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
