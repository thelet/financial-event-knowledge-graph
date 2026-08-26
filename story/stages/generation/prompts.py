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
**the slot table**: per row, the handle a sentence names it by and the exact set of strings that
row may be written as. `writer_prompt` takes both the passages and the rows as arguments rather
than deriving either, and for two different reasons. §10.2.1 point 3 is a rule about evidence and
this module is about rendering, so the passage slice lives beside the stage that must not be able
to break it. The rows come from `story.stages.composition.slot_table`, which this stage **may not
import at all** — `test_no_stage_imports_another_stage` forbids it — and which must in any case
be called once per run, by the composition root, so that the prompt prints the same answer the
compiler fills from.

**The writer answers with sentence templates and code compiles them** (S4 of
`docs/2026-08-23-deterministic-draft-compiler/`). A sentence is `text` carrying `{{F3}}` and
`{{F3.period}}` placeholders, a `kind`, and the passage handles an `explanatory` one rests on.
There is no `fact_bindings` array and no `citations` array: every figure, every metric surface,
every period surface and every citation is written by
`story/stages/composition/compile.py`, from the row the slot names, with the span recorded rather
than searched for. What the model still owns is the whole of §6's table — the words, the order,
the `kind`, which fact to state, which handle sits on which side of a comparison, which passage a
claim rests on, and the title.

**Three things the writer is told that it would otherwise have to guess, and code computes all
three** (§2's line: the model chooses words, code chooses facts).

* `metric_surfaces_for` — which surfaces name this metric and *only* this metric inside this
  package. **The writer's prompt no longer calls it**: `slot_table` does, and the prompt prints
  the answer. The planner's rendering still does, and the name stays this package's public one. §13.5 refuses `"gross margin"` because `gaap_gross_margin`'s own label is
  `"Gross Margin"` and `"gross margin" ⊂ "adjusted gross margin"`; a writer left to pick a
  surface picks that one. The filter here is a **conservative local approximation** of §13.5's
  alias index — it drops any surface that is a sub-phrase of another package metric's surface —
  and the verifier remains the authority. It cannot *add* a surface the index would refuse for a
  reason the package does not carry, which is why the approximation is safe in the direction
  that matters.
* `period_surface_for` — one surface per fact, in §13.4's closed grammar, derived from the
  fact's own endpoints. Same note: the writer's prompt reads it off the row rather than calling
  it. The grammar is the verifier's; this is the writing direction of it, and
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
from typing import Any, Mapping, Protocol, Sequence

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
from story.core.spine import StorySpine
from story.core.renderings import metric_surfaces, period_surface_of_fact

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
PLANNER_PROMPT_VERSION = "2.0.0"

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
1. Name a fact by its handle, never by its id. Every row of FACTS and DERIVED FACTS prints a \
handle in brackets - `F1`, `D1` - and `facts` holds those handles and nothing else. A handle \
that is not printed is rejected and the plan never reaches the writer.
2. Introduce no number, no period and no entity that is not in the package. Do not restate a \
figure in different units, and never work one out yourself - not a change, not a percentage, \
not a gap, not a ratio. Every figure you may build on is already printed, including the ones \
code computed for you under DERIVED FACTS.
3. VERIFIED CHANGE states what the filings show and which way it runs. It is measured, not \
inferred, and it is not yours to restate or contradict. You decide what it means and what \
matters about it; you do not decide whether it rose or fell.
4. Say why something happened only if a quoted span in the package says so. Where the package \
carries no such span you may not state a cause, imply one, or place two facts side by side so \
that a reader infers one.
5. Write a counterpoint **only** if the COUNTER-EVIDENCE section below lists an item, and rest \
it on at least one handle drawn from that section. Where that section says none, return an \
empty `counterpoint` and an empty `counterpoint_facts`: a counterpoint grounded in nothing is \
not a counterpoint, and writing one loses the whole plan.
6. `thesis` is one sentence stating the claim of the post. `why_it_matters` says why a reader \
should care. `uncertainty` states what the evidence does not settle - or is empty if nothing \
qualifies the claim.
7. COMPANY IDENTITY, METRIC SEMANTICS and COMPARISON RULES tell you what the subject is, what \
each figure means and which figures may be set against which. They are definitions: they carry \
no figure and no handle you may name. Where a line says NOT AVAILABLE, the corpus does not hold \
that answer and neither do you - plan no claim that needs it.

Answer with the JSON object the schema describes and nothing else.\
"""

def planner_schema() -> dict[str, Any]:
    """§11's output shape, reduced to the seven leaves that are editorial. Portable subset.

    Built fresh on every call rather than shared as a module constant: the returned mapping
    reaches a provider that copies it into a request body, and a shared nested dict is one
    mutation away from re-keying every stored generation that ever used it.

    **Seven leaves left, and each of the twelve that went because code already knew the
    answer or because nothing read it.** 1.2.0 asked for nineteen. What went, and what fills it:

    | field | why it is gone |
    | --- | --- |
    | `causal_language` | a one-member enum the planner could not influence, and `plan_story` re-stamped `causal_language_for(package)` over the answer regardless. A field the model cannot change does not belong in its grammar |
    | `requested_derivations` (3) | the detector already published which arithmetic it fired on (`signals["fired_on"]`), and `story/core/spine.py` executes it **before** this call. The planner was copying a triple out of a list code generated so that code could compare it back by string equality |
    | `required_citation_passage_ids` | a lookup: `PackagedFact.passage_id` gives the passage for every fact a point names, and `_referenced_passage_ids` already did the join |
    | `statement_class` | decided by which rows a point names — an `F` row is `reported`, a `D` row is `calculated`. Observed wrong in the corpus: `story-v1-76da8465cd95` returned `explanatory` for a package holding **zero** explanatory passages |
    | `required_warnings` | `claim_qualifying_warnings` already computes the admissible set by filtering build provenance out. Pinning it to that whole set is the only answer that can be right |
    | `structure` | rendered into the writer prompt and validated by nothing. gpt-5-nano returned the schema's own property names as its structure |
    | `prohibited_claims` | rendered into the writer prompt and read by no check anywhere. The real guard is `language_safety`'s five closed lexicons and 96 refusing codes; a free-text hint nothing enforces can only mislead |
    | `unusable_evidence` (2) | exists to account for unused counter-evidence, and is code's the moment `counterpoint_facts` says which items were used |

    **The ids became handles, and that is the largest change to what the model must produce.**
    `required_fact_ids` held 70-character `obs:` digests the model had to spell back exactly;
    `facts` holds `F1`, `D1` — two characters, printed beside their values in the prompt.
    `editorial_plan_from` resolves them, so `EditorialPlan` keeps every field it has today and
    every stored plan still loads. A `D` handle resolves to its two **input observations**,
    because §11 restricts `required_fact_ids` to ids beginning `obs:` and a derived fact is not
    one.

    **`angle` was in the plan for this change and is not here.** It was proposed as somewhere for
    editorial framing to go once `structure` and `prohibited_claims` left. `why_it_matters`
    already carries that, unvalidated, and a second unvalidated field saying the same thing is
    what `structure` was.

    **`counterpoint` stays, as prose plus handles rather than a nested array.** No package in the
    corpus carries counter-evidence, so it is empty for every candidate that exists — but
    `plan_violations` refuses a package *with* counter-evidence and no counterpoint, and removing
    the field would make that refusal unsatisfiable rather than unnecessary.
    """
    handles = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["thesis", "why_it_matters", "uncertainty",
                     "key_points", "counterpoint", "counterpoint_facts"],
        "properties": {
            "thesis": {"type": "string"},
            "why_it_matters": {"type": "string"},
            "uncertainty": {"type": "string"},
            "key_points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim", "facts"],
                    "properties": {
                        "claim": {"type": "string"},
                        "facts": dict(handles),
                    },
                },
            },
            "counterpoint": {"type": "string"},
            "counterpoint_facts": dict(handles),
        },
    }


def planner_prompt(
    package: StoryEvidencePackage,
    *,
    offered: Sequence[DerivationRequest] = (),
    slots: Sequence[SlotRowView] = (),
    spine: StorySpine | None = None,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    feedback: str = "",
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
    by_fact_id = _by_fact_id(slots)
    lines.extend(_fact_lines(package.facts, by_fact_id))
    # **No DERIVED FACTS section here, and the writer's is unchanged.** That section prints each
    # row's *slots*, which is writer vocabulary — a planner names a handle and writes no slot at
    # all. VERIFIED CHANGE below lists the same rows with their handles and their figures, which
    # is everything a plan can use, and printing both would show the planner a grammar it is not
    # allowed to write in.
    if spine is not None:
        # Printed **after** the rows so that the handles it names are already on the page, and
        # printed at all because the planner used to have to infer this from two raw floats six
        # lines apart. `story-v1-76da8465cd95` inferred it backwards and wrote *"rose"* over
        # 556 -> 110; nothing between there and the writer read the claim, and the run reached
        # a model with a factually inverted plan. The direction here is the detector's own word
        # from `quantity_direction`, which reads the metric's sign convention — so it is not
        # recoverable from the sign of a delta and was never the planner's to work out.
        lines += ["", VERIFIED_CHANGE_HEADING]
        lines.extend(_spine_lines(spine, by_fact_id))
    if offered:
        # Only where something is still the planner's to ask for. A story type whose derivations
        # code already executed prints no offer list, because an offer it may not take is an
        # invitation to a refusal.
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
    if count:
        lines += ["", f"COUNTER-EVIDENCE ({count} item{'' if count == 1 else 's'}; the "
                      "counterpoint must rest on a handle drawn from one of these)"]
        lines.extend(counter)
    else:
        # **Stated as an instruction rather than as an empty list**, because a model reading
        # "(none)" wrote one anyway. Measured live 2026-08-26 against gpt-5.4 on two candidates:
        # both returned a well-formed counterpoint with no handles, `Counterpoint`'s validator
        # refused a claim grounded in nothing, and the whole plan was lost to
        # `plan_not_constructible` — for the one field the package could not support.
        lines += ["", "COUNTER-EVIDENCE (none)",
                  "  This package carries no counter-evidence, so there is nothing a "
                  "counterpoint could rest on.",
                  '  Return `counterpoint: ""` and `counterpoint_facts: []`. A counterpoint '
                  "grounded in nothing is not a counterpoint, and writing one loses the plan."]
    lines.extend(_feedback_lines(feedback))
    return "\n".join(lines)


VERIFIED_CHANGE_HEADING = (
    "VERIFIED CHANGE (code measured this from the filings; it is not yours to restate or "
    "contradict)")


def _spine_lines(
    spine: StorySpine, by_fact_id: Mapping[str, SlotRowView] | None = None
) -> list[str]:
    """The story's factual core, in the words the rows above already print it in.

    **Every string here is one another section already published**, which is deliberate: a
    figure spelled one way under FACTS and another way here is two figures as far as a 9B model
    is concerned, and that exact reconciliation failure is recorded against
    `renderings.observed_figure`. So the surfaces are read off the slot rows rather than
    formatted again.

    **`direction_verified` is printed when it is false, and the line says the measurement is
    unavailable rather than omitting it.** `quantity_direction` answers `None` where a metric's
    sign convention is unverified — `cost_of_revenue` and `inventory_valuation_adjustment` have
    no observation to measure it from — and a section that silently dropped the direction would
    read as *"no direction"* rather than *"the corpus cannot say"*.
    """
    rows = by_fact_id or {}

    def side(fact_id: str, period: str) -> str:
        row = rows.get(fact_id)
        handle = f"[{row.handle}]" if row is not None else ""
        figure = row.offers.get("", "") if row is not None else ""
        return f"{period:>8}  {figure or '(no writable figure)'}  {handle}"

    lines = [
        f"  metric     {spine.metric_id}",
        f"  from     {side(spine.from_fact_id, spine.from_period)}",
        f"  to       {side(spine.to_fact_id, spine.to_period)}",
    ]
    if spine.direction_verified:
        lines.append(f"  direction  {spine.direction}")
    else:
        lines.append("  direction  NOT AVAILABLE - this metric's sign convention is unverified "
                     "in this corpus, so no direction may be stated")
    for fact_id in spine.derived_fact_ids:
        row = rows.get(fact_id)
        if row is None:
            continue
        figure = row.offers.get("", "")
        phrase = row.offers.get("direction", "")
        lines.append(f"  computed   [{row.handle}] {phrase} {figure}".rstrip())
    lines.append("  You decide what this means and what matters about it. You do not decide "
                 "which way it went.")
    return lines


TITLE_PERIODS_HEADING = (
    "TITLE PERIODS (the only numeral a title may carry, spelled exactly like this)")


def _title_period_lines(package: StoryEvidencePackage) -> list[str]:
    """The period keys this package's own facts carry, which is what §13.15 licenses in a title.

    **Printed rather than described, and finding out why is what this section is for.** Rule 12
    used to say *"the compact form the candidate id uses (2022Q3)"* and left the model to derive
    it. `claims.check_title` licenses a numeral only inside an exact occurrence of a period key
    **the package carries** — and half the corpus's `metric_move` candidates are instant-dated,
    so their keys are `2025-06-30`, not `2025Q2`. Measured live 2026-08-26 against three of
    them: Qwen followed the rule, wrote *"Opendoor 2025Q2"*, and earned `unbound_numeral` twice
    over — on `2025` and on `2` — for a title the rule had asked for. The instruction was
    unsatisfiable and the model was refused for obeying it.

    So the admissible strings are printed, the way every other legal string in this prompt is.
    A package whose facts carry no period key at all prints the line that says so, and rule 12's
    *"may"* then means no numeral at all.
    """
    keys = sorted({fact.period_key for fact in package.facts if fact.period_key})
    if not keys:
        return ["  (none — this title may carry no numeral at all)"]
    return [f"  {key}" for key in keys]


def _feedback_lines(feedback: str) -> list[str]:
    """A previous attempt's refusal, appended as its own section, or nothing at all.

    **Appended rather than interpolated, and last.** The evidence above it is identical to the
    first attempt's, so a reader diffing two stored prompts sees exactly one added block and can
    tell a repair from a fresh call without parsing either.

    **The empty string renders nothing, which is what makes a first attempt byte-identical to
    the run that had no repair at all.** `prompt` is a `request_identity` digest input, so a
    first call that carried an empty heading would key differently from every generation already
    recorded — and the whole replay store would miss for a feature that had not fired.
    """
    if not feedback.strip():
        return []
    return ["", "PREVIOUS ATTEMPT WAS REFUSED", *(f"  {line}" for line in feedback.split("\n"))]


def _subject(package: StoryEvidencePackage) -> str:
    subject = package.subject
    state = "resolved" if subject.resolved else "UNRESOLVED - never render this as a name"
    return f"{subject.entity_id}  {subject.entity_text!r}  ({state})"


def _fact_lines(
    facts: Sequence[PackagedFact], by_fact_id: Mapping[str, SlotRowView] | None = None
) -> list[str]:
    """One row per filed reading, headed by the handle a plan names it with.

    **The id is still printed and is no longer what the model has to reproduce.** §11's rule 1
    now asks for `F1`, and `editorial_plan_from` resolves it back to this id — but a reader of a
    stored prompt has to be able to join the two, and an evidence panel shows the id. Printing
    the handle alone would make the prompt unreadable against every artifact beside it.
    """
    if not facts:
        return ["  (none)"]
    rows = by_fact_id or {}
    lines: list[str] = []
    for fact in facts:
        row = rows.get(fact.observation_id)
        handle = f"[{row.handle}]  " if row is not None else ""
        lines.append(f"  {handle}[{fact.observation_id}]")
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
#:
#: **3.0.0**: S4 of `docs/2026-08-23-deterministic-draft-compiler/`. The writer stops declaring
#: machine metadata and starts writing **sentence templates with slots**. `fact_bindings` and
#: `citations` are gone from the schema — both were object arrays the model filled and code then
#: checked — and a sentence is three properties: `text` carrying `{{F3}}` / `{{F3.period}}`
#: placeholders, `kind`, and a `rests_on` list of passage handles. Every figure, every metric
#: surface, every period surface and every citation is written by
#: `story/stages/composition/compile.py` from a trusted row, and the span of each substitution is
#: recorded rather than searched for.
#:
#: **A major bump, and it removes fields as 2.0.0 did.** A draft recorded under 2.2.0 carries two
#: arrays this schema does not admit, and an answer written for this schema carries braces the
#: old parser would have copied into the post verbatim, so the rows are unreachable in both
#: directions. `writer.draft_from` stays as the reader for what is already on disk; nothing on
#: the pipeline path calls it.
#:
#: **What the prompt now prints is the slot table, not a rendering.** `_writer_fact_lines` and
#: `_derived_fact_lines` print each row's handle and the exact set of slots
#: `story.stages.composition.slot_table` says that row offers, so *"what may be written for this
#: row"* is answered in exactly one place and the prompt cannot offer a string the compiler will
#: refuse. That is why the rows are an **argument** to `writer_prompt` — see its docstring.
WRITER_PROMPT_VERSION = "4.0.0"

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

#: **The rules a draft must satisfy, stated once, in the order the answer is built.** Every line
#: names a refusal rather than a virtue — a 9B model given *"be careful with percentages"* writes
#: `15.9%`, and a 9B model given *"a difference between two percentages is measured in percentage
#: points, never in percent"* has been told what the check is.
#:
#: **Fourteen rules, down from seventeen, and the arithmetic of the change is worth recording**
#: (S4 of `docs/2026-08-23-deterministic-draft-compiler/`). Five rules were about metadata the
#: model no longer writes: 3 (`rendered` is a substring of your own text), 5's binding clauses, 6
#: (copy the evidence id) and 7 (give a sentence the evidence id of a fact it binds) collapse
#: into two — *"write a slot, not a number"* and *"code cites for you"* — because there is no
#: `fact_bindings` array and no `citations` array left to get wrong. Three pairs then merged
#: because each pair was one idea split across two lines: no-future with subject-only, required
#: warnings with required counterpoints, and the definition sections with EVIDENCE SCOPE. Rules 2
#: and 3 are new and are the two ways a *template* fails that no §13 code names: a slot the table
#: does not offer, and a field slot with no figure slot beside it.
#:
#: **What did not move.** Percentage points, superlatives, the single comparison, the future,
#: the subject, the required warnings, the counterpoints, the title and the definition sections
#: say what they said. §13 has not changed a rule, so neither has the prompt's account of it.
#:
#: **The metric slot is stated as optional and that is a measured judgment, not a softening.**
#: `{{F1.metric}}` and prose naming the same metric are the same string to §13.5 —
#: `metric_surface_absent_from_text` reads `MetricAliasIndex` over the sentence, and a compiled
#: draft whose sentence named *"adjusted gross margin"* in the model's own words while binding
#: through `{{F1}}` verified with zero findings *(driven through `DeterministicVerifier`
#: 2026-08-23)*. Saying so costs nothing and buys prose that reads: a post in which every metric
#: name is an interpolation reads like a form letter. The figure and the period are **not**
#: optional, because those two are the ones §2.3 measured being retyped — 15 of 18
#: `unbound_numeral` refusals were the literal string `2022`.
#:
#: **Rule 10's last clause is the one thing the compiler deliberately cannot help with.** Which
#: handle sits on which side of *"lower than"* is the semantic claim itself, and
#: `comparative_not_supported_by_text` fired 12 times in the corpus on exactly that — 8 of them
#: one sentence written in eight separate runs, with the right number, the right binding and the
#: claim inverted. Telling the writer that nothing checks it for them is the honest wording.
WRITER_SYSTEM = """\
You are the writer for an investor post about one company's reported figures.

You are given an evidence slice, an accepted editorial plan and a style profile. You have no \
tools, no search and no access to any database: what you are shown is your entire universe. \
Follow the plan; you did not choose it.

Write the post as a list of sentences. You write the words. Code writes every figure, every \
period and every citation.

Rules:
1. Every figure and every period in your post comes from a row below, spelled the way that row \
spells it. You may write the row's slot - `{{F3}}` is that row's figure, `{{F3.metric}}` the \
name of what it measures, `{{F3.period}}` the period it covers - or you may write the row's \
own printed string and code will bind it for you. Both are accepted. What is refused is a \
figure, a date or a period of your own: work nothing out, restate nothing in another unit, and \
copy nothing from a table. A numeral that matches no row is bound to nothing and is refused.
2. Only the rows printed below exist, and only the slots printed under a row exist. A slot \
naming a row that is not printed, or a field that row does not print, is refused and no post \
is written at all; a row printed with no slot is a row you cannot write about.
3. A sentence that names a row's metric or period must also carry that row's figure. The \
period and the metric are read off the figure they belong to, so a sentence that names a row \
without writing its figure states a period nothing accounts for.
4. Naming a metric in your own words is allowed and often reads better, provided the words name \
that metric and no other - write "GAAP gross margin" or "adjusted gross margin", never "gross \
margin", which names two.
5. A DERIVED FACTS row is a figure code computed from two filed readings. `{{D1}}` is that \
figure and `{{D1.direction}}` is the words for which way it runs. Do not name the operation, do \
not reverse the direction the row prints, and do not write the two readings it was computed \
from unless the sentence writes their figures too.
6. You write no citations at all. Code cites the facts your sentences name, and your answer has \
no field for an evidence id, a passage id or a quote.
7. A difference between two percentages is measured in **percentage points**, never in \
percent. A percentage-point row's own figure writes "percentage points" for you; what is \
refused is a `%` figure of your own beside a word like rose, fell, up or down.
8. Never write a superlative or a uniqueness claim (only, sole, first, last, never, always, \
worst, best, largest, smallest, record), an absence claim (has not, did not, no longer), or an \
ordering of two items (before, after, until, since). Nothing you have been shown can support \
one.
9. One comparison between two figures (higher, lower, better, worse, more, less) is allowed, \
and only where a DERIVED FACTS row supports it. Name each figure's metric on its own side of \
the comparing word and use one comparing word in the sentence. Which figure you put on which \
side of that word is your claim about the world, and no row checks it for you.
10. Never write about the future - no expectation, guidance, outlook, forecast, target or plan, \
and nothing a company "will" do - and write about the subject and no one else: no competitor, \
no index, no "the market", no "the industry", no "peers".
11. State every warning listed under REQUIRED WARNINGS, using one of the phrases it lists, and \
write every counterpoint the plan lists, resting on the same rows the plan names.
12. The title carries no figure and states no claim of its own: no superlative, no comparison, \
no cause. It may name the period the post is about, and the only spelling it may use is one of \
the tokens printed under TITLE PERIODS below - that is the only numeral a title may hold. \
Inside a sentence a period is written the way its row prints it and never as one of those \
tokens.
13. COMPANY IDENTITY, METRIC SEMANTICS, COMPARISON RULES and EVIDENCE SCOPE carry no figure and \
are not evidence. The first three are definitions - what the subject is, what each figure \
means, which figures may be set against which - and where a line says NOT AVAILABLE the corpus \
does not hold that answer and neither do you; in particular, write nothing about what the \
company does, sells or competes in. EVIDENCE SCOPE states what this package's evidence does not \
contain: obey it, and never report it.

What an answer looks like. Suppose the sections below printed a row `[F4]` offering `{{F4}}` \
-> "4.2 percent", `{{F4.metric}}` -> "Gross Margin" and `{{F4.period}}` -> "the fourth quarter \
of 2021", and a row `[D2]` offering `{{D2}}` -> "1.1 percentage points" and `{{D2.direction}}` \
-> "lower than". Either of these is a correct answer:

  {"title": "Acme 2021Q4",
   "sentences": [
     {"text": "Acme reported {{F4.metric}} of {{F4}} in {{F4.period}}."},
     {"text": "That is {{D2}} {{D2.direction}} the quarter before."}]}

  {"title": "Acme 2021Q4",
   "sentences": [
     {"text": "Acme reported Gross Margin of 4.2 percent in the fourth quarter of 2021."},
     {"text": "That is 1.1 percentage points lower than the quarter before."}]}

The second writes the rows' own strings instead of their slots, and code binds them to the same \
facts. What neither does is invent: no figure, no date and no period that a row did not print. \
A sentence reading "Acme reported gross margin of 4.3 percent in Q4" is refused three times \
over - a figure no row prints, a period in a form no row prints, and a metric name that \
matches two.

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
    """The sentence, and nothing else. Two leaves, inside §15.3's portable subset.

    **`kind` and `rests_on` are gone, and the schema is now prose plus a title.** They were the
    last two machine-facing fields the model authored, and both were measured wrong on the same
    day: `story-v1-76da8465cd95` put fact handles in `rests_on` (six refusals, run over), and
    `kind` is derivable from the rows a sentence's slots name — `story/stages/composition/`
    computes it, and doing so is *stricter* than asking, because three of the four wrong labels
    become unrepresentable rather than refused.

    **`rests_on` went because the route it serves cannot currently succeed.** An `explanatory`
    sentence rests on a passage; `EvidenceRequest.want_explanatory_search` defaults to `False`
    and every detector leaves it there, so `package.explanatory_passages` is empty in every run
    that exists. The `P` rows a draft could name are the *tables facts were read from*, and a
    claim resting on a number grid is what gpt-5-nano wrote. Measured: **0 of 17 accepted runs
    used an explanatory sentence**, and the PASSAGES section printing those tables was 5,924 of
    this prompt's 11,179 characters — 182 numerals, none of them legal for the model to write.

    None of that vocabulary is deleted. `SentenceKind.EXPLANATORY`, the `P` rows, the compiler's
    `rests_on` handling and the four codes that guard it all stay, unreachable from this schema
    and still tested, exactly as the eight pre-3.0.0 writer codes stay for replayed artifacts.
    When a story type turns explanatory retrieval on, the route returns.

    **The template is the reference list, and there is deliberately no `facts_used` array.** Two
    fields that can disagree about which facts a sentence uses is a state worth making
    unrepresentable; the placeholders inside `text` already say it exactly once — and where the
    model writes no placeholder at all, `story/stages/composition/recovery.py` recovers one from
    the literal it did write.

    `additionalProperties: false` is what makes each removal a refusal rather than a hope: an
    answer that carries a `fact_bindings` array anyway — which is exactly what the 50 recorded
    2.2.0 drafts carry — fails `schema_violations` before a template exists.
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
                    "required": ["text"],
                    "properties": {
                        # Carries `{{H}}` and `{{H.field}}` where the model chose to write one;
                        # everything outside a slot is the model's own prose and is copied
                        # through untouched, recovered or not.
                        "text": {"type": "string"},
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
#:
#: **Neither is called by this module any more, and that is S4 rather than rot.** The writer's
#: prompt used to answer *"what may be written for this row"* by calling an emitter; it now
#: prints what `story.stages.composition.slot_table` already decided, which is the seam §4.1
#: exists to establish — one answer, printed by the prompt and filled by the compiler. The two
#: private aliases that stood beside these (`_observed_figure`, `_derived_figure`) are **gone**,
#: because a private name exists for this module's own printer and had no reader left; these two
#: stay because they are the *package's* public surface and removing a name from
#: `story.stages.generation.__all__` is not S4's decision to make.
metric_surfaces_for = metric_surfaces
period_surface_for = period_surface_of_fact


class SlotRowView(Protocol):
    """What the prompt printer needs of one slot-table row, as a structural type.

    **This is `story.stages.composition.public.SlotRow` seen through the four attributes this
    module reads, and it is a `Protocol` because this module may not import that class.**
    `tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` forbids
    one stage importing another from the module path alone, and the composition stage is where
    the slot table lives. The rows therefore arrive as an argument — see `writer_prompt` — and
    the annotation has to describe them without naming them.

    **A `Protocol` and not `object`**, which was the alternative. `object` would type-check
    everything and tell a reader nothing: the four attributes below *are* the contract between
    the prompt the model reads and the compiler that fills what it wrote, and a signature saying
    `slots: Sequence[object]` would leave that contract stated only in prose. The members are
    read-only properties rather than bare annotations so that a frozen dataclass satisfies the
    protocol — a variable member would require the attribute be settable, which `SlotRow` is
    deliberately not.

    `kind` is a plain `str` for the same reason. `SlotKind` is a `str` enum precisely so that its
    *value* is what an artifact carries, so `row.kind == PASSAGE_ROW` compares correctly against
    a member without this module importing the enum, and `writer.py` pins the one string it
    depends on against the real enum in a test rather than by discipline.
    """

    @property
    def handle(self) -> str: ...
    @property
    def fact_id(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def offers(self) -> Mapping[str, str]: ...
    @property
    def evidence_handles(self) -> tuple[str, ...]: ...


#: `SlotKind.PASSAGE`'s value, which is the only member either module in this stage has to
#: recognise: a passage row is the one a `rests_on` handle may name and the one that offers no
#: text slot. A checked copy of one string, in the repository's established shape —
#: `WARNING_QUALIFIER_PHRASES` and `slot_table.TWO_PERIOD_OPERATIONS` are the precedent — and
#: `tests/story/test_story_writer.py::test_the_passage_row_marker_is_the_composition_enums_own`
#: asserts it equals the enum member rather than trusting this line.
PASSAGE_ROW = "passage"

#: `SlotKind.DERIVED`'s value, under the same rule and for the same reason as `PASSAGE_ROW`: this
#: stage may not import the enum, and the string is what a row carries.
DERIVED_ROW = "derived"


def _by_fact_id(slots: Sequence[SlotRowView]) -> Mapping[str, SlotRowView]:
    """The rows keyed on the id they were minted for, which is how each section finds its handle.

    Keyed on `fact_id` rather than walked positionally beside `package.facts`. The handles *are*
    positional — `slot_table` assigns `F1..Fn` over the package's facts in package order — but a
    printer that relied on that would print `F2`'s slots under `F3`'s reading the first time a
    caller passed a table built from a different row set, and the failure would be silent. The
    three id namespaces (`obs:`, `fact:derived:`, a `passage_id`) do not collide.
    """
    return {row.fact_id: row for row in slots}


def _slot_lines(row: SlotRowView | None, *, indent: str = "      ") -> list[str]:
    """Every slot one row offers, spelled as a template writes it, beside the words it inserts.

    **The exact set the row offers and nothing else**, which is R3 as a rendering: `SlotRow.offers`
    omits a field the row has no legal value for and never carries an empty one, so a slot that
    is not on this list does not exist for this row and the compiler refuses a template naming it
    (`field_not_offered_by_row`). The prompt's old *"do not write about this fact"* was advice;
    this is the offer set itself.

    **The inserted words are printed beside each slot**, and the alternative — printing the slot
    names alone — was rejected. A model has to build a sentence *around* the words code will
    insert: whether `{{D1.direction}}` reads *"decreased by"* or *"lower than"* decides the rest
    of the clause, and whether `{{F1}}` is `$556 million` or `-12.6 percent` decides the article
    before it. Showing the figure does invite a model to retype it, and that is the one failure
    §13.1 catches with full force on this path — `unbound_numeral` reads coverage off bindings
    the compiler wrote, so a retyped number is refused rather than published.
    """
    if row is None:
        return [indent + "no handle in this run's slot table - do not write about this row"]
    if not row.offers:
        return [indent + "slots: none - this row carries nothing you may write"]
    return [indent + "slots: " + "  ".join(
        f'{_slot(row.handle, field)} -> "{value}"' for field, value in row.offers.items())]


def _slot(handle: str, field: str) -> str:
    """`{{F3}}` for the value slot, `{{F3.period}}` for a field. The empty key names no field."""
    return "{{" + handle + ("" if not field else "." + field) + "}}"


def writer_prompt(
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    passages: Sequence[PackagedPassage],
    *,
    slots: Sequence[SlotRowView] = (),
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    length_target: int = DEFAULT_LENGTH_TARGET,
    feedback: str = "",
) -> str:
    """The plan, the facts, the derived facts, the writer's passage slice — and the slot table.

    `passages` is an argument because §10.2.1 point 3 makes the slice a rule rather than a
    rendering choice — `writer.writer_passages` owns it, and a prompt that derived its own would
    be a second answer to *"what may this model cite?"*.

    `derived_facts` is an argument for a stronger version of the same reason: they are §3's
    *separate artifact*, minted by `story/stages/derivation/` from the plan's own requests, and
    they may not enter `StoryEvidencePackage.facts` at all — a package whose contents depended
    on a model call would put a model's selection inside `package_content_digest`, which is a
    `story_run_id` input. So there is nowhere in the package for this function to read them
    from, and that is deliberate rather than inconvenient.

    **`slots` is an argument for the same reason a third time, and this one is structural rather
    than editorial.** The rows come from `story.stages.composition.slot_table`, and
    `story/stages/generation/` may not import `story/stages/composition/` —
    `test_no_stage_imports_another_stage` reads it off the module path. Calling `slot_table()`
    here is therefore not a thing this module can do, and *should* not be even if it could: the
    prompt must print the same answer the compiler will fill from, and a second call is a second
    place for that answer to come from. `story/pipeline.py` is the composition root, it may
    import everything, and it builds the table once and hands it to both. The rows are typed by
    `SlotRowView` rather than by their class, which that protocol's docstring argues for.

    **The default is empty and prints a prompt nothing can be written from**, exactly as
    `derived_facts=()` does. That is the safe direction: a caller that forgot the table gets
    every row marked *"no handle in this run's slot table"* and a model with no legal slot to
    write, rather than a prompt that quietly offered handles the compiler will not recognise.

    The style profile is **not** a parameter. §12 requires it to be a separate system-prompt
    section, and a signature with nowhere to put it is what makes that structural.
    """
    by_fact_id = _by_fact_id(slots)
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
    lines += ["", FACTS_HEADING]
    lines.extend(_writer_fact_lines(package, by_fact_id))
    derived = [row for row in derived_facts if isinstance(row, DerivedFact)]
    lines += ["", _derived_heading(derived)]
    lines.extend(_derived_fact_lines(derived, by_fact_id))
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
    if passages:
        # Omitted entirely when there is none, rather than printed as an empty heading. Every
        # other section here renders "(none)" because an absent section would read as an
        # omission; this one is the opposite case. A heading over no passage would invite a
        # sentence resting on evidence the run does not carry, and the section's whole cost is
        # the tables it prints — see `writer.write_story` for the measurement.
        count = len(passages)
        lines += ["", f"PASSAGES ({count} whole passage{'' if count == 1 else 's'}; every one "
                      "is a passage a fact above was read from. Read them; write no text out "
                      "of them, and name one only in rests_on, by its handle)"]
        lines.extend(_writer_passage_lines(passages, by_fact_id))
    lines += ["", TITLE_PERIODS_HEADING]
    lines.extend(_title_period_lines(package))
    lines += ["", f"LENGTH  about {length_target} sentences."]
    lines.extend(_feedback_lines(feedback))
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


#: The FACTS heading, which now has to say what a row *is* rather than what to copy out of it.
#: One string rather than an f-string built at call time, because the prompt text is half of the
#: request identity §14's replay store keys on and a heading that varied would be a second key.
FACTS_HEADING = (
    "FACTS (one row per filed reading; the handle in brackets is what a slot names, and the "
    "slots line is every slot that row has)")


def _writer_fact_lines(
    package: StoryEvidencePackage, by_fact_id: Mapping[str, SlotRowView]
) -> list[str]:
    """One observation per block: what it reads, which handle names it, and its exact slot set.

    **The machine reading is still printed and is still not writable.** `metric_id`, `value` and
    `unit` are how the package stores the fact — `3.3 percent`, `556000000.0 USD` — and a
    sentence carrying either spelling is what §2 measured going wrong. They are here because the
    model has to *select* a fact before it can write one, and selection needs the metric's name
    and the magnitude; what may be written is the slots line beneath, and rule 1 says the
    difference in one sentence.

    **No evidence id is printed and that is the change, not an omission** (§6, R5). The writer
    writes no citation, so a handle in the prompt would be a token to copy back into a field
    that no longer exists. What survives is the *negative* case: a fact for which the package
    minted no handle cannot be cited at all, and a template binding it is
    `no_evidence_handle_for_bound_fact` at compile time, so the row says so where a reader of
    the prompt can act on it rather than only where the refusal fires.
    """
    if not package.facts:
        return ["  (none)"]
    lines: list[str] = []
    for fact in package.facts:
        row = by_fact_id.get(fact.observation_id)
        lines.append(f"  [{row.handle if row is not None else '-'}]  {fact.metric_id}")
        printed = f'  printed "{fact.printed_form}"' if fact.printed_form else ""
        lines.append(f"      reads {fact.value} {fact.unit}{printed}"
                     "  (the package's own spelling; you never write it)")
        lines.extend(_slot_lines(row))
        if row is not None and not row.evidence_handles:
            # Reachable without the model doing anything wrong: `_has_some_evidence` admits a
            # fact carrying an `evidence_source_id` and no `passage_id`, and no handle is minted
            # for one. R6 — the compiler refuses a binding to it rather than omitting a citation.
            lines.append("      no filed evidence backs this row; a sentence writing it is "
                         "refused, so write nothing about it")
        if fact.warning_codes:
            lines.append("      warnings " + ", ".join(fact.warning_codes))
    return lines


# ---------------------------------------------------------------------------------------
# DETERMINISTIC_FACT_TOOLS §4.4 and §7 — what code computed, printed as slots
#
# **The row prints no figure of its own any more, and the two paragraphs of measurement that
# stood here are the reason it does not have to.** Under 2.1.0 and 2.2.0 this section handed the
# writer a *quoted string to copy* — `write exactly "$446 million" - those characters, never
# "446000000.0 USD"` — because the model had to retype the figure into `rendered`, and three
# live drafts measured it retyping the wrong spelling: `"15.9 percentage_points"` against its own
# prose, then `"446000000.0 USD"`, then `446 million USD` copied off the neighbouring FACTS row.
# Each was `derived_unit_mismatch` or a declared span that did not occur in the sentence.
#
# There is nothing to retype now. `{{D1}}` inserts the same string those wordings were trying to
# get copied — `slot_table` reads it from `renderings.legal_renderings`, which is where
# `derived_figure` lives — and the model never touches it. The counter-example is gone with the
# failure it named: naming a refused spelling beside an accepted one is this module's rule for a
# string the model must *write*, and it writes none.
#
# **Three things about a derived row still have to be said in the prompt.**
#
# * **No evidence of its own.** §6 mints no handle for a derivation, and the compiler cites the
#   two observations it was computed from. The row does not name them: the writer cites nothing,
#   and printing two ids would be printing two tokens with nowhere to go.
# * **The direction is a slot, not a sign to read.** `DisplaySemantics` is a closed vocabulary
#   code chose, because the sign of `result` does not settle the direction — 46 of 46 canonical
#   `direct_selling_costs` values are stored negative, so a fall in the number is a rise in the
#   cost. `{{D1.direction}}` hands over the words; rule 5 forbids reversing them.
# * **A word-valued row offers no figure at all.** `crossed_zero` and `trend_direction` answer a
#   word, `legal_renderings` returns nothing for them, and §13.2 refuses a numeral written for
#   one. Such a row simply has no `{{D1}}` on its slots line, which is R3 doing the work the old
#   *"this row carries no figure, so write no number for it"* sentence was doing by instruction.
# ---------------------------------------------------------------------------------------


def _derived_heading(derived: Sequence[DerivedFact]) -> str:
    count = len(derived)
    if not count:
        return ("DERIVED FACTS (none; the plan requested no derivation, so there is no derived "
                "row to write a slot for)")
    return (f"DERIVED FACTS ({count}; code computed each one from two FACTS rows - name its "
            "slots exactly as you name a fact's above)")


def _derived_fact_lines(
    derived: Sequence[DerivedFact], by_fact_id: Mapping[str, SlotRowView]
) -> list[str]:
    """One derived fact per block: what it is of, and the exact slot set its row offers.

    **The metric and the period names are the row's, and the row offers one name per thing.**
    `_derived_row` in `slot_table` decides that: `.metric` only where the derivation stays inside
    one metric and `.from_metric`/`.to_metric` only where it does not, `.period` only for a
    same-period operation and `.from_period`/`.to_period` only for a two-period one. Printing
    them from here would be a second answer to a question that module already answers, which is
    the whole of §4.1.

    **The period *keys* are no longer printed, and that is deliberate.** This block used to open
    `{metric_id}, {from_period} -> {to_period}`, which spells `2022Q2 -> 2022Q3` — the literal
    string §2.3 measured being retyped into 15 of 18 `unbound_numeral` refusals. The periods are
    on the slots line now, in the words §13.4's grammar reads, and nowhere else.
    """
    if not derived:
        return ["  (none)"]
    lines: list[str] = []
    for fact in derived:
        row = by_fact_id.get(fact.fact_id)
        lines.append(f"  [{row.handle if row is not None else '-'}]  {fact.metric_id}")
        lines.append("      computed by code from two filed readings; it was never filed itself")
        lines.extend(_slot_lines(row))
        if fact.warning_codes:
            lines.append("      warnings " + ", ".join(fact.warning_codes))
    return lines


def _evidence_scope_lines(scope: Sequence[EvidenceScopeFact]) -> list[str]:
    """§7's claim, as the sentence code minted and with no id to bind.

    The `fact_id` is deliberately not printed. It is not bindable — the row carries no result,
    no unit and no period — and printing an id beside every other bindable row would invite a
    binding the schema would then have nowhere to resolve. What the writer needs from this
    section is the constraint, and the constraint is the statement.
    """
    return ["  " + fact.statement for fact in scope]


def _writer_passage_lines(
    passages: Sequence[PackagedPassage], by_fact_id: Mapping[str, SlotRowView]
) -> list[str]:
    """Each passage under the handle `rests_on` names it by, whole and never excerpted.

    **A passage row offers no slot and the block says so.** `{{P2}}` in sentence text is
    `field_not_offered_by_row`: a passage has no value, no metric and no period to write, and an
    `explanatory` sentence says what the filing says in the model's own words. The handle exists
    for one field, `rests_on`, and the compiler resolves it to the span of a fact read from that
    passage.
    """
    if not passages:
        return ["  (none)"]
    lines: list[str] = []
    for passage in passages:
        row = by_fact_id.get(passage.passage_id)
        handle = row.handle if row is not None else "-"
        head = f"  [{handle}]  document {passage.document_id}"
        if passage.passage_kind:
            head += f"  kind {passage.passage_kind}"
        lines.append(head)
        if passage.heading_path:
            lines.append("      section: " + " > ".join(passage.heading_path))
        # Whole, not excerpted: §10.2.1 point 2 excerpts explanatory and counter-evidence
        # passages and never a passage a fact is bound to, because §13.7's Rule A needs the
        # entire table to reconstruct a cell.
        lines.append(f"      whole passage, {passage.char_count} characters")
        lines.append(f'      name it as rests_on: ["{handle}"] - it has no slot for your text')
        lines.append("      " + passage.text.replace("\n", "\n      "))
    return lines
