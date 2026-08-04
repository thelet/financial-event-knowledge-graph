"""§10.2's caps, §10.2.1's token arithmetic, and the order a section is trimmed in.

Responsibility: how big the package may be and what leaves it first. No retrieval, no graph, no
assembly — this module answers two questions and nothing else: *is this section over its cap?*
and *which row goes?*

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
from typing import Any, Callable, Mapping, Sequence, TypeVar

from story.core.models import BudgetParameters, canonical_json

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

#: The order sections give ground in when the token estimate is over budget, and the floor each
#: keeps. Read left to right: the cheapest evidence goes first and the most load-bearing last.
#:
#: * **`explanatory_passages` first.** They are the only section §11's correction admits is not
#:   model-free — ranked by `search_passages`, which top-k displacement steers — so they are the
#:   rows whose absence costs the least and whose presence is least defensible.
#: * **`context_passages` next.** A neighbour is context for a passage that is still there.
#: * **`primary_passages` third, and it takes its facts with it.** §13.7's Rule A needs the
#:   whole table a fact is bound to, so dropping a primary while keeping the facts that cite it
#:   would leave a fact with no resolvable citation — the one thing §10 may not produce.
#: * **`counter_evidence` last, and never to zero.** §11 requires a non-empty `counterpoints`
#:   whenever `counter_evidence` is non-empty, and a trimmer that emptied the section would
#:   discharge that rule by deleting the inconvenient evidence. That is the exact shape of the
#:   silent-omission channel §11 exists to close.
TRIM_ORDER: tuple[str, ...] = (
    "explanatory_passages",
    "context_passages",
    "primary_passages",
    "counter_evidence",
)

#: How few rows a trimmed section may be left with. `primary_passages` keeps one because a
#: package with no passage cites nothing; `counter_evidence` keeps one for the §11 reason above,
#: and only when retrieval found any in the first place.
TRIM_FLOOR: Mapping[str, int] = {
    "explanatory_passages": 0,
    "context_passages": 0,
    "primary_passages": 1,
    "counter_evidence": 1,
}


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
    "TRIM_FLOOR",
    "TRIM_ORDER",
    "BudgetExceedsCeiling",
    "cap_for",
    "check_budget",
    "context_sort_key",
    "estimate_tokens",
    "explanatory_sort_key",
    "fact_sort_key",
    "passage_sort_key",
    "prompt_slice",
    "truncate",
]
