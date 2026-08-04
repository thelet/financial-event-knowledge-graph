"""What the planner is told, how the package is rendered to it, and what its answer must be.

Responsibility: three pure functions over a `StoryEvidencePackage` — a persona, a prompt, and
a schema — and the versions that identify them. Nothing here calls a model, opens a file, asks
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
"""

from __future__ import annotations

from typing import Any, Sequence

from story.core.models import (
    CausalLanguage,
    PackagedFact,
    PackagedPassage,
    StatementClass,
    StoryEvidencePackage,
    UnusableReason,
)

#: Bumped whenever the wording below or the rendering changes. It is a digest input to every
#: stored generation, so answers produced under an older wording become unreachable rather
#: than silently re-used — the single failure a replay cache cannot show you.
PLANNER_PROMPT_VERSION = "1.0.0"

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
        "FACTS",
    ]
    lines.extend(_fact_lines(package.facts))
    lines += ["", "METRICS"]
    lines.extend(_metric_lines(package))
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
