"""The five answers a §9 tool may give, and the row bounds it gives them within.

Responsibility: the vocabulary of a retrieval answer. No Cypher, no executor, no ontology —
this module is what a test imports to say "that call should have refused, with this code",
and what `graph_tools.py` imports to build the answer. Splitting it out is not symmetry: the
bounds table is read by three different places (the retriever, the structural scan, and the
live tests) and a constant that three files agree on should have one home.

**Why `RetrievalResult` is not subclassed into five types.** `story/core/models.py` froze it
at S0 with four fields — `outcome`, `rows`, `truncated`, `reason` — and S1 does not own that
file. So the five states of §9 are *constructors* over one frozen type rather than five types,
and the discriminating information that would otherwise be a field lives in `reason` under a
fixed `"<code>: <detail>"` shape. `result_code()` is the reader, so no caller anywhere does
string surgery on a reason to learn what happened; a test asserts the code, never the prose.

**Why `Unavailable` carries a code too.** §9: *"`Unavailable` is an answer and is rendered as
one"* — the agent must be able to say "the graph holds no guidance data, and here is the
refusal that records why". A reason with no code is a sentence a renderer can only print; a
code is something it can branch on and something a test can pin.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from story.core.models import RetrievalOutcome, RetrievalResult

#: §9's per-tool row caps, verbatim from the plan's table. The retriever asks the database for
#: `cap + 1` rows and reports `truncated` from whether the extra one came back, which is why
#: these are *the* numbers rather than a suggestion: a bound that bit is a fact the package
#: carries (§10.2), and `len(rows) == cap` alone can never distinguish a full page from a
#: truncation.
#:
#: `list_metrics` is 26 because the ontology defines 26 metrics and the graph holds 26
#: `:Metric` nodes *(verified live 2026-08-03)*; it is a cap and not a count, so a 27th metric
#: would truncate visibly rather than disappear.
MAX_ROWS: Mapping[str, int] = {
    "list_metrics": 26,
    "get_metric_definition": 1,
    "get_metric_history": 200,
    "compare_metric_periods": 2,
    "get_fact_evidence": 10,
    "get_passage_context": 7,
    "search_passages": 25,
    "get_events_in_window": 50,
    "find_counter_evidence": 25,
}

#: The three §9 tools that exist so a caller gets a reason instead of a missing attribute.
#: Each reason names the *lane* that would turn it on, because WORKSTREAM_BOUNDARY §5 makes
#: that a coordination event and an operator reading this should know what to wait for.
UNAVAILABLE_TOOLS: Mapping[str, tuple[str, str]] = {
    "get_guidance_history": (
        "no_guidance_lane",
        "the graph holds no guidance observations: `AssertionType.GUIDED` landed at F0 but no "
        "lane emits a `guidance_issuance` event yet, so there is nothing to read. §6.8's D10 "
        "turns this on when the lane is scheduled",
    ),
    "compare_guidance_to_actual": (
        "no_guidance_lane",
        "comparing guidance to actuals needs guidance, and no lane emits it yet (§6.8 D11). "
        "The actual side is available today through `get_metric_history`",
    ),
    "get_price_reaction": (
        "no_market_data_lane",
        "the graph holds no `:EvidenceSource:MarketData` node — all 2,710 evidence rows are "
        "`normalized_passage` or `normalized_table` (§13.7.2). Price reaction is out of V1 "
        "scope (§1) and would need a market-data lane and a canonical disclosure channel",
    ),
}

#: The fields that make a row citable, per tool. §9: *"a fact without a citation handle is not
#: a return value"*. Declared here rather than asserted ad hoc in a test, because the test that
#: checks it should fail when a **query** stops returning a handle, not when someone forgets to
#: extend the test — so the query and this table are checked against each other.
CITATION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "list_metrics": ("metric_id",),
    "get_metric_definition": ("metric_id",),
    "get_metric_history": ("observation_id", "passage_id", "document_id"),
    "compare_metric_periods": ("observation_ids", "passage_ids", "document_ids"),
    "get_fact_evidence": ("passage_id", "document_id", "source_url"),
    "get_passage_context": ("passage_id", "document_id", "source_url"),
    "search_passages": ("passage_id", "document_id", "source_url"),
    "get_events_in_window": ("event_id", "passage_id", "document_id"),
    "find_counter_evidence": ("issue_id", "passage_id", "document_id"),
}

#: The separator between a code and its explanation inside `RetrievalResult.reason`.
_CODE_SEPARATOR = ": "


def ok(rows: Sequence[Mapping[str, Any]], *, truncated: bool = False) -> RetrievalResult:
    """Rows the caller may use, and whether a bound bit.

    Zero rows is an `Ok`, not a `NotFound`: "this metric has no refused claim in these
    documents" is an answer about the corpus, while `NotFound` is an answer about an
    identifier. Collapsing the two would let an empty counter-evidence result read as a
    broken id.
    """
    return RetrievalResult(
        outcome=RetrievalOutcome.OK, rows=tuple(rows), truncated=truncated)


def not_found(identifier: str, *, detail: str = "") -> RetrievalResult:
    """No node carries that id. The id is in the reason so the caller can name it back."""
    suffix = f" ({detail})" if detail else ""
    return RetrievalResult(
        outcome=RetrievalOutcome.NOT_FOUND,
        reason=f"not_found{_CODE_SEPARATOR}{identifier}{suffix}",
    )


def ambiguous(surface: str, candidates: Sequence[str], *, detail: str = "") -> RetrievalResult:
    """A surface that denotes more than one thing, with every candidate returned.

    The candidates are `rows` and not just prose, because §9's example is a metric surface
    resolving to two members of a `mutually_distinct_group` and the caller has to be able to
    *choose* — a reason string listing them would make the choice a parsing exercise.
    """
    suffix = f" ({detail})" if detail else ""
    return RetrievalResult(
        outcome=RetrievalOutcome.AMBIGUOUS,
        rows=tuple({"surface": surface, "metric_id": candidate} for candidate in candidates),
        reason=(f"ambiguous_metric_surface{_CODE_SEPARATOR}{surface!r} denotes "
                f"{', '.join(candidates)}{suffix}"),
    )


def unavailable(code: str, detail: str) -> RetrievalResult:
    """The honest answer for a tool no lane feeds yet (§9, §13.7.2)."""
    return RetrievalResult(
        outcome=RetrievalOutcome.UNAVAILABLE, reason=f"{code}{_CODE_SEPARATOR}{detail}")


def refused(code: str, detail: str) -> RetrievalResult:
    """The call was not made, and this is why. Never an exception.

    A model asking for eight passages of context, or naming a tool that does not exist, is an
    ordinary state for the planner to branch on; raising would make a bad parameter indis-
    tinguishable from a dead database.
    """
    return RetrievalResult(
        outcome=RetrievalOutcome.REFUSED, reason=f"{code}{_CODE_SEPARATOR}{detail}")


def result_code(result: RetrievalResult) -> str:
    """The machine-readable half of `reason`, or `""` for an `Ok` with nothing to say."""
    if not result.reason:
        return ""
    code, separator, _detail = result.reason.partition(_CODE_SEPARATOR)
    return code if separator else result.reason


__all__ = [
    "CITATION_FIELDS",
    "MAX_ROWS",
    "UNAVAILABLE_TOOLS",
    "ambiguous",
    "not_found",
    "ok",
    "refused",
    "result_code",
    "unavailable",
]
