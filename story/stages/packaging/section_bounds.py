"""§10.2's caps, §10.2.1's token arithmetic, and the order a section is trimmed in.

Responsibility: how big the package may be and what leaves it first. No retrieval, no graph, no
assembly — this module answers three questions and nothing else: *is this section over its cap?*,
*which row goes?* and, since §4 S5, *which rows may never go at all?*

**The third question is new and it changes what "does not fit" means.** Before §4 S5 every
section could be trimmed to a floor, so a package always fitted eventually — the only failure was
a package too large even at the floors. `PROTECTED_SECTIONS` makes some evidence untouchable: the
candidate's anchor facts, and the ontology's semantic, identity and comparability declarations. A
package that cannot hold those is not a package to trim harder; it is a package to refuse, and
`package_assembly` raises `required_fact_does_not_fit` for it.

**Why the truncation order is a constant and not an argument.** §10.2 bounds sixteen sections
and the first draft bounded seven of them (§0c item 4), which is how `compatibility[]` —
O(n²) over a 26-quarter series — got into a package with a token budget. Bounding a section is
only half the fix: a cap with no stated drop rule means *"the model did not see it"* depends on
dictionary insertion order, and two runs over one graph could ship two universes. The rules
below are stated once, applied by `truncate`, and asserted row by row in
`tests/story/test_story_evidence_package.py`.

**Four characters to a token, and that is §10.2.1's own arithmetic, not a guess.** The section
sizes *"1 backing passage, median → 536 tokens"* against a measured median of **2,144.5
characters**; 2144.5/4 = 536.1. So the estimator restates the plan's constant rather than
inventing a second one, and a package sized here is sized the way §10.2.1 sized its own table.
It is an estimate and is named one: no tokeniser ships with this repository, `tiktoken` is on
`test_story_package_structure.py`'s forbidden list, and the local runtime's tokeniser is a GGUF
detail that would make the budget depend on which model file is loaded.

**Two estimates, and only one of them is a bound.** `artifact_token_estimate` is taken over the
whole package's canonical JSON — it is what gets written to disk, and it is informational.
`prompt_token_estimate` is taken over `prompt_slice()`: the same payload minus `retrieval_trace`
and minus the `budget` block, which is **the largest slice any model can be shown**. §10.2's
5,000-token total and `MAX_TOTAL_TOKENS_CEILING` bind that second number, and the trim loop
targets it.

The reason is a measurement, not a preference. §10.2.1 sizes *"3 primaries + ±1 context ≈ 4,800
tokens"* over **passages alone**, while the estimate covered the whole artifact; on the F1 spike
`retrieval_trace` cost 1,084 tokens and `budget` 190, so §10.2's total could not hold §10.2.1's
own arithmetic and all three spike packages shipped two primaries, zero context and `facts`
below §10.2's stated 5–12 default. The trace is provenance for a human reviewer and for §14's
manifest; §10.2.1 point 3 says *"the planner and the writer see different slices of one
package"*, and the trace is in no slice. Counting it against the evidence budget starved the
thing the budget exists to protect. No ceiling was raised to fix this — the existing ones simply
became reachable.

**Fixed points.** The artifact estimate is taken with both budget estimates at `0` and
`package_content_digest` at `""`, and all three are stamped *after* it is known — an estimate
that included them would have no fixed point, the same defect S0's F6 found in §10.3's digest
and answered the same way. The prompt estimate needs no such care and is exact: it excludes the
block it is stored in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, TypeVar

from story.core.models import BudgetParameters, EvidenceRole, canonical_json

T = TypeVar("T")

#: §10.2.1's divisor. See the module docstring for the arithmetic it is taken from.
CHARS_PER_TOKEN = 4

#: §10.2's **ceilings**, which a configured cap may not exceed. `BudgetParameters` holds the
#: *defaults*; this is the other column of the plan's table, and it is here because a
#: `config/story.yaml` (S11) that raised `max_primary_passages` to 12 would be a budget change
#: nobody voted for — §10.2.1 measured 8 primaries with context at ~12,900 tokens, which exceeds
#: the server's entire 8,192 context before the system prompt.
CEILINGS: Mapping[str, int] = {
    "facts": 24,
    "events": 8,
    "relationships": 8,
    "primary_passages": 6,
    "context_passages": 2,          # ±2 per primary
    "explanatory_passages": 5,
    "counter_evidence": 6,
    "documents": 20,
    "metrics": 8,
    "formula_windows": 8,
    "warnings": 20,
    "conflicts": 8,
    "compatibility": 12,
    "retrieval_trace": 40,
}

#: The `BudgetParameters` field each ceiling constrains. Two names for one bound is how they
#: drift, so the mapping is written once and `check_budget` is its only reader.
CAP_FIELDS: Mapping[str, str] = {
    "facts": "max_facts",
    "events": "max_events",
    "relationships": "max_relationships",
    "primary_passages": "max_primary_passages",
    "context_passages": "max_context_neighbours",
    "explanatory_passages": "max_explanatory_passages",
    "counter_evidence": "max_counter_evidence",
    "documents": "max_documents",
    "metrics": "max_metrics",
    "formula_windows": "max_formula_windows",
    "warnings": "max_warnings",
    "conflicts": "max_conflicts",
    "compatibility": "max_compatibility",
    "retrieval_trace": "max_retrieval_trace",
}

#: §10.2's total, and the ceiling it may not pass. **Both bind `prompt_token_estimate`**, not the
#: artifact: what leaves under 1,200 tokens for the system prompt and the plan schema is what is
#: put in front of the model. The ceiling is a refusal and not a clamp: §10.2.1 measured the
#: local runtime at `-c 8192` with `max_output_tokens 1024`.
MAX_TOTAL_TOKENS_CEILING = 6000

#: The two package sections `prompt_slice` removes, and why each is not evidence.
#:
#: * **`retrieval_trace`** is provenance. It records which tool ran with which parameters and how
#:   many rows came back — a reviewer's audit trail and §14's manifest input. No planner or
#:   writer slice contains it, and a model that read it would be reading about its own universe
#:   rather than from it.
#: * **`budget`** is the measurement block itself, so excluding it is also what makes
#:   `prompt_token_estimate` exact rather than approximate.
#:
#: Deliberately not extended further. `warnings` (491–548 tokens on the spikes) and `documents`
#: (173–185) are both read by §11 and §13, so trimming the estimate by dropping them would be
#: the relaxation this split is not.
PROMPT_EXCLUDED_SECTIONS: tuple[str, ...] = ("budget", "retrieval_trace")

#: The sections the token trimmer may **never** take a row from, and the reason each is in the
#: list (§4 S5's *"protected and untrimmable"*).
#:
#: * **`facts`** — protected at *row* grain, not wholesale. A fact whose `(metric_id,
#:   period_key)` is one of the candidate's anchor slots is untouchable: §6.11 digests
#:   `anchor_observation_ids` into the `candidate_id`, so a package that dropped one would claim
#:   evidence it does not carry, and the half it dropped is the half the story is about. That
#:   covers §4 S5's *"candidate-anchor observed facts"* and its *"required derived facts"* alike
#:   — a derived fact for a required slot is required by the same rule and needs no second flag.
#:   Other facts leave only with the primary passage they cite (§13.7's Rule A), never alone.
#: * **`semantic_facts`, `identity_facts`, `comparability_facts`** — wholesale. These are the
#:   ontology's declarations (§4 S4), and they are what makes a number mean something: a post
#:   written without its metric's definition is a post whose numbers have no declared meaning.
#:   They are also small — a definition is a sentence, a passage is a table — so trimming them
#:   buys almost nothing and costs the claim its semantics.
#:
#: Not everything absent from `TRIM_PLAN` is *protected*: `metrics`, `events`, `conflicts`,
#: `compatibility`, `warnings`, `documents` and `retrieval_trace` are bounded by their own §10.2
#: caps and are simply not part of the token trim. `warnings` and `retrieval_trace` are excluded
#: on purpose and the reason is stated where the cap is applied — a trimmer that removed the
#: record of its own deletion would delete the disclosure with the evidence.
PROTECTED_SECTIONS: tuple[str, ...] = (
    "comparability_facts",
    "facts",
    "identity_facts",
    "semantic_facts",
)


@dataclass(frozen=True, slots=True)
class TrimStep:
    """One class of row the trimmer may take, and where it lives.

    A step rather than a bare section name because §4 S5's order is finer than §10's sections:
    *"extra corroborating passages"* and *"lower-severity diagnostics"* are rows **inside**
    `primary_passages` and `counter_evidence`, distinguished by `EvidenceRole`, and they go
    before the supporting and disputing rows they sit beside. Expressing that as two more
    sections would have been a schema change (S1 owns the sections and it has landed); expressing
    it as a role filter needs nothing new and is inert until S2 and S3 assign the roles.
    """

    #: What `caps_hit` and the section ledger record when this step fires.
    name: str
    #: The `PackageSections` attribute the rows live in.
    section: str
    #: The roles this step is allowed to take, or `None` for *"any row in the section"*.
    roles: tuple[EvidenceRole, ...] | None = None

    def takes(self, row: Any) -> bool:
        return self.roles is None or getattr(row, "role", None) in self.roles


#: §4 S5's trimming priority, row class by row class. Read top to bottom: the cheapest evidence
#: goes first and the most load-bearing last, and nothing in `PROTECTED_SECTIONS` appears at all.
#:
#: * **`explanatory_passages` first.** They are the only section §11's correction admits is not
#:   model-free — ranked by `search_passages`, which top-k displacement steers — so they are the
#:   rows whose absence costs the least and whose presence is least defensible.
#: * **`context_passages` next.** A neighbour is context for a passage that is still there.
#: * **extra corroborating passages third.** §4 S3 keeps one canonical fact, designates one
#:   source `primary_support` and carries the concordant rest as `corroborating_support`. A
#:   second source for a fact already carried is real evidence and is still the cheapest thing
#:   in the package to lose: the fact, its value and its citation all survive without it. Inert
#:   until S3 assigns the role, which is why it is written now rather than after — a priority
#:   that arrives with the rows it orders arrives too late to have been decided.
#: * **lower-severity diagnostics fourth**, and they have a section of their own: S2 demotes a
#:   row that does not qualify as counter-evidence into `diagnostic_passages` rather than
#:   relabelling it in place. Those rows are extraction and data-quality diagnostics — §1
#:   measured twenty of them, not one of which challenged the fact it was attached to — and they
#:   give way before a row that genuinely disputes the story, and before any supporting passage,
#:   because a diagnostic explains a fact and never evidences one. Floor `0`: unlike
#:   `counter_evidence`, no §11 rule requires a counterpoint for a diagnostic, so emptying the
#:   section discharges no obligation. The disclosure survives the rows — each demoted row's
#:   codes are already on the fact it qualifies.
#: * **non-required relationships fifth, and vacuous in V1.** `relationships[]` is empty on
#:   every package (`relationships_unavailable_in_v1`: no §9 tool returns one), so there is no
#:   section to take a row from. Named here rather than implemented as an empty step, because a
#:   `TrimStep` over an attribute `PackageSections` does not have would be structure built for a
#:   capability that does not exist.
#: * **`primary_passages` sixth, and it takes its facts with it.** §13.7's Rule A needs the
#:   whole table a fact is bound to, so dropping a primary while keeping the facts that cite it
#:   would leave a fact with no resolvable citation — the one thing §10 may not produce. A
#:   primary backing a required slot is refused by `may_drop_last_primary`, which is where §4
#:   S5's *"primary supporting passages"* protection is enforced: the *supporting* ones are
#:   untouchable, and a primary that supports nothing required is not one of them.
#: * **`counter_evidence` last, and never to zero.** §11 requires a non-empty `counterpoints`
#:   whenever `counter_evidence` is non-empty, and a trimmer that emptied the section would
#:   discharge that rule by deleting the inconvenient evidence. That is the exact shape of the
#:   silent-omission channel §11 exists to close.
#: S2's demoted rows, named because `PackageSections.diagnostic_passages` points at it: the
#: section is S2's and its place in the priority is S5's, so neither stage has to state the
#: other's decision.
DIAGNOSTIC_TRIM_STEP = TrimStep("diagnostic_passages", "diagnostic_passages")

TRIM_PLAN: tuple[TrimStep, ...] = (
    TrimStep("explanatory_passages", "explanatory_passages"),
    TrimStep("context_passages", "context_passages"),
    TrimStep("corroborating_passages", "primary_passages",
             (EvidenceRole.CORROBORATING_SUPPORT,)),
    DIAGNOSTIC_TRIM_STEP,
    TrimStep("primary_passages", "primary_passages"),
    TrimStep("counter_evidence", "counter_evidence"),
)

#: The sections `TRIM_PLAN` touches, in the order it first touches them. Derived rather than
#: written twice: the plan is the decision and this is a projection of it, so a step added to
#: one cannot go missing from the other.
#:
#: **`primary_passages` reads third and that is a projection artifact, not a demotion.** The
#: third *step* takes only corroborating rows out of that section; the supporting rows are not
#: reachable until the fifth. A section list cannot express that, which is why `TRIM_PLAN` is the
#: decision and this is a view of it — and why the trimmer iterates the plan and never this.
TRIM_ORDER: tuple[str, ...] = tuple(dict.fromkeys(step.section for step in TRIM_PLAN))

#: How few rows a trimmed section may be left with. `primary_passages` keeps one because a
#: package with no passage cites nothing; `counter_evidence` keeps one for the §11 reason above,
#: and only when retrieval found any in the first place.
TRIM_FLOOR: Mapping[str, int] = {
    "explanatory_passages": 0,
    "context_passages": 0,
    "diagnostic_passages": 0,
    "primary_passages": 1,
    "counter_evidence": 1,
}

if set(TRIM_ORDER) != set(TRIM_FLOOR):  # pragma: no cover - a source edit, not a state
    raise ValueError(
        "every trimmable section must state the floor it keeps: "
        f"{sorted(set(TRIM_ORDER) ^ set(TRIM_FLOOR))} differ between TRIM_PLAN and TRIM_FLOOR")

if set(TRIM_ORDER) & set(PROTECTED_SECTIONS):  # pragma: no cover - a source edit, not a state
    raise ValueError(
        "a protected section may not be trimmable: "
        f"{sorted(set(TRIM_ORDER) & set(PROTECTED_SECTIONS))} is in both")


def droppable_index(rows: Sequence[Any], step: TrimStep) -> int | None:
    """Which row this step gives up next, or `None` when it may give up nothing.

    The **last** matching row, because every section is already sorted into its own stated drop
    order (`explanatory_sort_key`, `context_sort_key`, `passage_sort_key`, and
    `CounterEvidenceRow.sort_key`), so the tail is the least defensible row by construction and
    a step that searched for one would be a second, weaker ranking.

    The floor is read per **section** and not per step: `TRIM_FLOOR["primary_passages"]` is 1
    because a package with no passage cites nothing, and that is true however the row that would
    have gone was labelled. So a corroborating row is not taken out of a section already at its
    floor — the trimmer moves to the next step instead.
    """
    if len(rows) <= TRIM_FLOOR[step.section]:
        return None
    for index in range(len(rows) - 1, -1, -1):
        if step.takes(rows[index]):
            return index
    return None


class BudgetExceedsCeiling(ValueError):
    """A configured cap above §10.2's ceiling. Refused at construction, never clamped.

    Clamping would report a configuration as honoured. `graph_tools` makes the same choice for
    `get_passage_context`'s window and says so: *"a clamp teaches a caller that its request was
    honoured."*
    """


def check_budget(budget: BudgetParameters) -> None:
    """Every cap is inside §10.2's ceiling, or the builder refuses to exist.

    Called from the builder's `__init__` rather than from `build`, so a misconfigured pipeline
    fails once at composition instead of once per candidate.
    """
    offences = [
        f"{field}={getattr(budget, field)} exceeds §10.2's ceiling of {CEILINGS[section]} "
        f"for {section}"
        for section, field in sorted(CAP_FIELDS.items())
        if getattr(budget, field) > CEILINGS[section]
    ]
    if budget.max_total_tokens > MAX_TOTAL_TOKENS_CEILING:
        offences.append(
            f"max_total_tokens={budget.max_total_tokens} exceeds §10.2's ceiling of "
            f"{MAX_TOTAL_TOKENS_CEILING}")
    if budget.excerpt_radius_chars <= 0:
        offences.append(
            f"excerpt_radius_chars={budget.excerpt_radius_chars} is not a window; §10.2.1's "
            "excerpt is ±400 characters around a matched span")
    if offences:
        raise BudgetExceedsCeiling("; ".join(offences))


def cap_for(budget: BudgetParameters, section: str) -> int:
    """The configured cap for one §10.2 section, by the section's own name."""
    return int(getattr(budget, CAP_FIELDS[section]))


def truncate(
    rows: Sequence[T], cap: int, *, key: Callable[[T], Any] | None = None
) -> tuple[tuple[T, ...], int]:
    """The first `cap` rows in the section's stated order, and how many were dropped.

    `key` sorts first when the caller's order is not already the drop order. It is passed
    explicitly at every call site rather than defaulted, because "which row goes" is the
    decision this module exists to make and a default would let one section make it by accident.
    """
    ordered = list(rows) if key is None else sorted(rows, key=key)
    if cap < 0:
        raise ValueError(f"a negative cap is not a bound: {cap}")
    return tuple(ordered[:cap]), max(len(ordered) - cap, 0)


def estimate_tokens(payload: Any) -> int:
    """§10.2.1's estimate over the canonical JSON of a package payload.

    Typed `Any` and not `Mapping` so a single passage's text can be sized with the same function
    the package is sized with — the plan's own table quotes both, and two estimators would be
    two answers.

    Rounded **up**: a budget that rounded down would let a package sit one row over its cap and
    report itself inside it.
    """
    return math.ceil(len(canonical_json(payload)) / CHARS_PER_TOKEN)


def prompt_slice(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The package payload minus the sections no model slice contains (§10.2.1 point 3).

    Takes the package's `digestible_payload()` and removes `PROMPT_EXCLUDED_SECTIONS`. A
    subtraction rather than an allow-list on purpose: a section added to §10 later is evidence
    until somebody argues otherwise, and an allow-list would silently exempt it from the budget
    that is meant to bound what the model reads.
    """
    return {name: value for name, value in payload.items()
            if name not in PROMPT_EXCLUDED_SECTIONS}


def fact_sort_key(
    fact_id: str,
    *,
    is_anchor: bool,
    metric_rank: int,
    anchor_date: str,
    period_key: str,
    role_rank: int,
) -> tuple[int, int, str, str, int, str]:
    """§10.2's `facts[]` drop rule, as one total key.

    Read in order: the candidate's own anchor observations survive first, then the metrics in
    the order the `EvidenceRequest` named them, then the earliest period, then the slot's
    representative reading before its supporting readings before its minority ones, then the
    observation id. Every component is total and the last is unique, so two runs over one graph
    drop the same rows.

    **Anchors first is the load-bearing part.** §6.11 digests `anchor_observation_ids` into the
    `candidate_id`; a cap that dropped one would ship a package whose id claims evidence the
    package does not carry.
    """
    return (0 if is_anchor else 1, metric_rank, anchor_date, period_key, role_rank, fact_id)


def passage_sort_key(passage_id: str, *, first_citing_fact_rank: int) -> tuple[int, str]:
    """`primary_passages[]`'s drop rule: the passage cited by the surviving-est fact stays.

    Derived from the fact order rather than from the passage's own properties — length, kind or
    score — because §13.7 makes a primary passage the thing a *fact* is bound to, and ranking
    passages independently would let the cap drop the table the first fact was read from.
    """
    return (first_citing_fact_rank, passage_id)


def context_sort_key(
    passage_id: str, *, primary_rank: int, offset: int
) -> tuple[int, int, int, str]:
    """`context_passages[]`'s drop rule: the outermost neighbour of the least-ranked primary.

    `abs(offset)` before `offset` so ±2 goes before ±1, and `-offset` before `+offset` at equal
    distance so the *following* passage — the one a table's footnotes live in — survives the
    preceding one. Measured reason: on the 2022Q3 spike the anchor's `+1` neighbour is the
    *Current Housing Environment* narrative and the `-1` is a statement-of-comprehensive-loss
    fragment, so the later passage is the one carrying explanation.
    """
    return (primary_rank, -abs(offset), offset, passage_id)


def explanatory_sort_key(passage_id: str, *, score: float) -> tuple[float, str]:
    """`explanatory_passages[]`'s drop rule: lowest fulltext score first. Negated so the sort is
    ascending in "droppability" like every other key here."""
    return (-score, passage_id)


__all__ = [
    "CAP_FIELDS",
    "CEILINGS",
    "CHARS_PER_TOKEN",
    "MAX_TOTAL_TOKENS_CEILING",
    "PROMPT_EXCLUDED_SECTIONS",
    "DIAGNOSTIC_TRIM_STEP",
    "PROTECTED_SECTIONS",
    "TRIM_FLOOR",
    "TRIM_ORDER",
    "TRIM_PLAN",
    "BudgetExceedsCeiling",
    "TrimStep",
    "cap_for",
    "check_budget",
    "context_sort_key",
    "droppable_index",
    "estimate_tokens",
    "explanatory_sort_key",
    "fact_sort_key",
    "passage_sort_key",
    "prompt_slice",
    "truncate",
]
