"""The nine §9 tools, the three that answer `Unavailable`, and the trace of what ran.

Responsibility: turning a tool name and a parameter map into one of §9's five answers, within
§9's bounds. It owns the parameter contract, the bound arithmetic and the retrieval trace; it
owns no Cypher (that is `cypher.py`), no escaping (`lucene_escaping.py`), no metric metadata
(`metric_metadata.py`) and **no driver at all**.

**The executor is injected and this module imports nothing that could open a connection.**
Decision D1: `story/providers/neo4j_connection.py` is the only module in the package that
names `neo4j`, `story/context.py` is the only one that constructs it, and every tool here is
written against `story.contracts.ReadQueryExecutor`. The practical consequence is that the
whole of §9 is exercised offline against `tests/story/conftest.py`'s `RecordedReadExecutor`,
with the driver uninstallable.

**Bounds are arithmetic, not trust.** Every statement is asked for `MAX_ROWS[tool] + 1` rows;
if the extra one arrives the result is capped to the declared bound and `truncated=True`. §9
requires `truncated: bool` *"rather than silently capping"*, and `len(rows) == cap` cannot
distinguish a full page from a bound that bit — the extra row can.

**A caller value never chooses a limit, a label or an index.** `$row_limit` is read from
`MAX_ROWS` by tool name; `$excerpt_chars` and `$handle_limit` are constructor-owned; the
fulltext index name is a literal inside `cypher.py`. There is no path from `parameters` to any
of them, which is what makes "a model cannot widen a query" checkable rather than asserted.

**Every parameter map is closed.** An unknown key is `Refused`, not ignored: a model that can
add `limit=1000` and have it silently dropped has learned nothing, while one that gets a
refusal has learned the shape of the tool. The same goes for a missing required key, a
malformed date and a context window wider than §9's three.

**A refusal is a value.** Nothing here raises for a bad parameter, an unknown tool, a missing
id or an unreachable database — §11's planner has to branch on all four, and an exception
would make "the database is down" indistinguishable from "that observation does not exist".

**§16's timeout is on every statement and has no default here either.** `ReadQueryExecutor.read`
takes `timeout_seconds` keyword-only with no default (finding F10: the server reads `0` as *no
timeout*), and this class threads one value through every call. S11's `config/story.yaml` owns
the number; `DEFAULT_TIMEOUT_SECONDS` below is what it is until that file exists, stated once.
"""

from __future__ import annotations

import re
import time
from typing import Any, Mapping, Sequence

import story.stages.retrieval.cypher as cypher
from ontology.contracts import ConceptRegistry
from story.contracts import ReadQueryExecutor
from story.core.models import RetrievalResult, RetrievalTraceEntry
from story.stages.retrieval.lucene_escaping import build_query
from story.stages.retrieval.metric_metadata import (
    MetricResolution,
    default_definition_hash,
    default_registry,
    definition_row,
    resolve_metric_surface,
    with_hash_comparison,
)
from story.stages.retrieval.results import (
    MAX_ROWS,
    UNAVAILABLE_TOOLS,
    ambiguous,
    not_found,
    ok,
    refused,
    unavailable,
)

#: Until `config/story.yaml` exists (S11). Generous enough that the 8,776-passage fulltext scan
#: completes and tight enough to be a ceiling — §16's point is that there *is* one, and F10 is
#: why it may never be zero.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: §10.2.1's excerpt radius, used to bound `search_passages`' text. The full passage is
#: `get_passage_context`'s answer; a search result that carried 3,224 characters per row would
#: spend the package's whole token budget on candidates the packager will discard.
DEFAULT_EXCERPT_CHARS = 400

#: How many ids `compare_metric_periods` collects per period. Its two rows aggregate up to
#: thirteen observations for `adjusted_ebitda` 2022Q2/2022Q3, and the caller needs handles to
#: cite, not the whole set — `get_metric_history` returns every one.
DEFAULT_HANDLE_LIMIT = 10

#: §9's ceiling on `get_passage_context`. Asking for more is refused rather than clamped: a
#: clamp teaches a caller that its request was honoured.
MAX_CONTEXT_NEIGHBOURS = 3

#: `YYYY-MM-DD`, the form every date property in this graph is stored in *(F13: the graph holds
#: no temporal type — `occurred_on`, `filing_date`, `period_start` are all strings)*. Validated
#: here because a malformed date compares lexicographically against real ones and returns a
#: quietly wrong window instead of an error.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: `(required, optional)` per tool. The closed parameter contract §9 implies and §16 depends
#: on: *"no tool takes a label or property name as a parameter"* is only true if the set of
#: accepted parameters is enumerated somewhere, and this is that somewhere.
TOOL_PARAMETERS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "list_metrics": (frozenset(), frozenset()),
    "get_metric_definition": (frozenset({"metric_id"}), frozenset()),
    "get_metric_history": (
        frozenset({"metric_id"}), frozenset({"shape", "since", "until"})),
    "compare_metric_periods": (
        frozenset({"metric_id", "period_key_a", "period_key_b"}), frozenset()),
    "get_fact_evidence": (frozenset(), frozenset({"observation_id", "event_id"})),
    "get_passage_context": (frozenset({"passage_id"}), frozenset({"before", "after"})),
    "search_passages": (
        frozenset({"terms"}), frozenset({"document_types", "since", "until"})),
    "get_events_in_window": (frozenset({"since", "until"}), frozenset()),
    "find_counter_evidence": (frozenset({"metric_id", "period_key"}), frozenset()),
    "get_guidance_history": (frozenset(), frozenset({"metric_id"})),
    "compare_guidance_to_actual": (frozenset(), frozenset({"metric_id", "period_key"})),
    "get_price_reaction": (frozenset(), frozenset({"date", "window"})),
}

#: The two period shapes `get_metric_history` can filter on, derived from the fields a graph
#: observation actually carries (C2). There is no `shape` property to validate against, so the
#: vocabulary is stated here and the `CASE` in `cypher.METRIC_HISTORY` is its only reader.
PERIOD_SHAPES = frozenset({"duration", "instant"})

TOOL_NAMES: tuple[str, ...] = tuple(sorted(TOOL_PARAMETERS))


class BoundedGraphRetriever:
    """`story.contracts.GraphRetriever` over an injected read executor.

    Structural conformance, no inheritance — the protocol exists so a stage above can be driven
    by a five-line stub, and a base class would make that a lie.

    Stateful in exactly one way: it accumulates a `RetrievalTraceEntry` per call, because §10
    requires the evidence package to carry `retrieval_trace[]` and a retriever that could not
    report what it did would make the package's provenance an assertion. Nothing else is
    remembered between calls; there is no cache, so two identical calls hit the database twice
    and a test asserting stable ordering is asserting it about the *query*.
    """

    def __init__(
        self,
        executor: ReadQueryExecutor,
        *,
        registry: ConceptRegistry | None = None,
        ontology_definition_hash: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
        handle_limit: int = DEFAULT_HANDLE_LIMIT,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be positive, got {timeout_seconds!r}; the server reads "
                "0 as 'no timeout' (F10), so a non-positive value removes §16's ceiling while "
                "looking like the tightest one possible"
            )
        self._executor = executor
        self._registry = registry
        self._ontology_definition_hash = ontology_definition_hash
        self._timeout_seconds = timeout_seconds
        self._excerpt_chars = excerpt_chars
        self._handle_limit = handle_limit
        self._trace: list[RetrievalTraceEntry] = []

    # -- the protocol ---------------------------------------------------------------------

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        """Run one §9 tool and record what it did.

        The trace entry is written on every path — refusal, `NotFound`, `Unavailable` and all —
        because "the model asked for guidance three times and was told no" is exactly the sort
        of thing §10's provenance is for, and a trace that recorded only successes would make a
        package look like it had never been told anything.
        """
        started = time.perf_counter()
        result = self._dispatch(tool, parameters)
        self._trace.append(
            RetrievalTraceEntry(
                tool=tool,
                parameters=dict(parameters),
                row_count=len(result.rows),
                truncated=result.truncated,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                outcome=result.outcome,
            )
        )
        return result

    def trace(self) -> Sequence[RetrievalTraceEntry]:
        """Every call so far, in order. A tuple, so a consumer cannot extend the record."""
        return tuple(self._trace)

    # -- dispatch -------------------------------------------------------------------------

    def _dispatch(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        if tool in UNAVAILABLE_TOOLS:
            code, detail = UNAVAILABLE_TOOLS[tool]
            return unavailable(code, detail)
        if tool not in MAX_ROWS:
            return refused(
                "unknown_tool",
                f"{tool!r} is not a §9 tool; the nine available are "
                f"{', '.join(sorted(MAX_ROWS))}")
        rejection = self._check_parameters(tool, parameters)
        if rejection is not None:
            return rejection
        # `getattr` on a caller-supplied string, and it is safe for one reason worth stating:
        # the `tool not in MAX_ROWS` guard above has already refused everything that is not one
        # of §9's nine code-owned names, so the only strings that reach here are ones this
        # module wrote. A dispatch dict would be equally safe and would need the nine names
        # listed a second time, which is the drift this avoids.
        return getattr(self, f"_{tool}")(parameters)

    def _check_parameters(
        self, tool: str, parameters: Mapping[str, Any]
    ) -> RetrievalResult | None:
        required, optional = TOOL_PARAMETERS[tool]
        supplied = frozenset(parameters)
        unknown = supplied - required - optional
        if unknown:
            return refused(
                "unknown_parameter",
                f"{tool} accepts {sorted(required | optional)}; got {sorted(unknown)}")
        missing = required - supplied
        if missing:
            return refused("missing_parameter", f"{tool} requires {sorted(missing)}")
        return None

    # -- the nine tools -------------------------------------------------------------------

    def _list_metrics(self, _parameters: Mapping[str, Any]) -> RetrievalResult:
        """Every metric the graph holds, with the ontology's opinion of each one.

        `defined_in_ontology` is not decoration. C4 makes the ontology authoritative, so a
        `:Metric` node the ontology does not define is a projection built against a different
        definition set — visible here as `False` on one row, rather than as a definition lookup
        that fails several tools later.
        """
        outcome = self._read("list_metrics", cypher.LIST_METRICS, {})
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        registry = self._resolved_registry()
        return ok(
            tuple(
                {**row,
                 "defined_in_ontology": registry.find(str(row.get("metric_id"))) is not None}
                for row in rows
            ),
            truncated=truncated,
        )

    def _get_metric_definition(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        """C4's tool. The ontology answers; the graph says only whether it was projected."""
        resolution = self._resolve(parameters["metric_id"])
        if isinstance(resolution, RetrievalResult):
            return resolution
        metric_id = str(resolution.metric_id)

        # C4, stated as control flow: the definition comes from the ontology or the tool
        # answers `NotFound`. There is deliberately no branch that reads the node instead —
        # `:Metric` carries no percentage bound to read, and `None` from a missing property
        # would make §13.3's bound check pass on everything.
        definition = self._resolved_registry().find(metric_id)
        if definition is None:
            return not_found(
                metric_id,
                detail="the ontology defines no such metric; C4 forbids falling back to the "
                       "node, which carries no metric metadata to fall back to")

        outcome = self._read("get_metric_definition", cypher.METRIC_NODE,
                             {"metric_id": metric_id})
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        row = definition_row(definition, rows[0] if rows else None)
        return ok((with_hash_comparison(row, self._resolved_definition_hash()),),
                  truncated=truncated)

    def _get_metric_history(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        resolution = self._resolve(parameters["metric_id"])
        if isinstance(resolution, RetrievalResult):
            return resolution

        shape = parameters.get("shape")
        if shape is not None and shape not in PERIOD_SHAPES:
            return refused(
                "unknown_period_shape",
                f"shape must be one of {sorted(PERIOD_SHAPES)} or absent; got {shape!r}. The "
                "shape is derived from the fields an observation carries (C2); there is no "
                "`shape` property to name")
        window = self._check_window(parameters.get("since"), parameters.get("until"))
        if window is not None:
            return window

        outcome = self._read(
            "get_metric_history",
            cypher.METRIC_HISTORY,
            {"metric_id": resolution.metric_id,
             "shape": shape,
             "since": parameters.get("since"),
             "until": parameters.get("until")},
        )
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        return ok(rows, truncated=truncated)

    def _compare_metric_periods(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        """Two aggregated rows, and three refusals this layer can make from measurement alone.

        §6.9's full comparability ruling belongs to S2 and needs the ontology's formula windows
        and distinct-groups. What is decidable *here*, from the rows themselves, is narrower and
        worth refusing early: the same key twice, a duration compared against an instant, and
        two different units for one metric. Each is measured from the fields present rather than
        asserted, so none of them is a comparability opinion this layer is not entitled to.
        """
        resolution = self._resolve(parameters["metric_id"])
        if isinstance(resolution, RetrievalResult):
            return resolution
        period_a = parameters["period_key_a"]
        period_b = parameters["period_key_b"]
        if period_a == period_b:
            return refused(
                "period_keys_identical",
                f"both periods are {period_a!r}; a comparison of a period with itself is a "
                "question with no answer, not a comparison that happens to be zero")

        outcome = self._read(
            "compare_metric_periods",
            cypher.COMPARE_METRIC_PERIODS,
            {"metric_id": resolution.metric_id,
             "period_key_a": period_a,
             "period_key_b": period_b,
             "handle_limit": self._handle_limit},
        )
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome

        found = {str(row.get("period_key")) for row in rows}
        absent = [key for key in (period_a, period_b) if key not in found]
        if absent:
            return not_found(
                ", ".join(absent),
                detail=f"{resolution.metric_id} has no observation in "
                       f"{'that period' if len(absent) == 1 else 'those periods'}")

        shapes = {shape for row in rows for shape in row.get("distinct_period_shapes") or ()}
        if len(shapes) > 1:
            return refused(
                "period_shape_mismatch",
                f"{period_a} and {period_b} do not share a period shape: {sorted(shapes)}. §13.4 "
                "calls conflating a duration with an instant catastrophic; the shape is read "
                "from the date fields present, not from a property")
        units = {unit for row in rows for unit in row.get("distinct_units") or ()}
        if len(units) > 1:
            return refused(
                "unit_mismatch",
                f"{resolution.metric_id} is reported in {sorted(units)} across {period_a} and "
                f"{period_b}; §13.2 forbids comparing across units")
        return ok(rows, truncated=truncated)

    def _get_fact_evidence(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        """C3's tool: the citation quote lives on the `EVIDENCED_BY` edge, and is returned.

        One id or the other, never both and never neither — an observation and an event are two
        different statements over two different labels, and a tool that accepted both at once
        would have to decide which one it meant.
        """
        observation_id = parameters.get("observation_id")
        event_id = parameters.get("event_id")
        if observation_id is not None and event_id is not None:
            return refused(
                "ambiguous_fact_id",
                "give observation_id or event_id, not both; they name different labels")
        if observation_id is None and event_id is None:
            return refused("missing_parameter", "get_fact_evidence requires observation_id or event_id")

        statement = (cypher.FACT_EVIDENCE_FOR_OBSERVATION if observation_id is not None
                     else cypher.FACT_EVIDENCE_FOR_EVENT)
        identifier = observation_id if observation_id is not None else event_id
        key = "observation_id" if observation_id is not None else "event_id"
        outcome = self._read("get_fact_evidence", statement, {key: identifier})
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        if not rows:
            return not_found(
                str(identifier),
                detail="no such fact, or it is evidenced by an `:EvidenceSource` rather than a "
                       "filed passage — zero of those exist today (§13.7.2 Rule C)")
        return ok(rows, truncated=truncated)

    def _get_passage_context(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        before = parameters.get("before", MAX_CONTEXT_NEIGHBOURS)
        after = parameters.get("after", MAX_CONTEXT_NEIGHBOURS)
        for name, value in (("before", before), ("after", after)):
            if not isinstance(value, int) or isinstance(value, bool):
                return refused("invalid_parameter", f"{name} must be an integer; got {value!r}")
            if value < 0 or value > MAX_CONTEXT_NEIGHBOURS:
                return refused(
                    "context_window_exceeds_bound",
                    f"{name}={value} is outside §9's 0..{MAX_CONTEXT_NEIGHBOURS}; refused rather "
                    "than clamped, because a clamp reports a request as honoured")

        outcome = self._read(
            "get_passage_context",
            cypher.PASSAGE_CONTEXT,
            {"passage_id": parameters["passage_id"], "before": before, "after": after},
        )
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        if not rows:
            return not_found(
                str(parameters["passage_id"]),
                detail="the graph holds 8,776 of the corpus's 12,442 passages, so an id that "
                       "exists in the corpus may not be cited by any fact and therefore may not "
                       "be here")
        return ok(rows, truncated=truncated)

    def _search_passages(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        terms = parameters["terms"]
        if isinstance(terms, str) or not isinstance(terms, Sequence):
            return refused(
                "invalid_parameter",
                f"terms must be a list of strings, not {type(terms).__name__}; a bare string "
                "would be escaped as one term and silently change the search")
        if any(not isinstance(term, str) for term in terms):
            return refused("invalid_parameter", "every element of terms must be a string")
        lucene_query = build_query(terms)
        if not lucene_query:
            return refused(
                "no_searchable_terms",
                f"{list(terms)!r} escapes to an empty query; an empty fulltext query matches "
                "nothing while looking like a search")

        document_types = parameters.get("document_types")
        if document_types is not None:
            if isinstance(document_types, str) or not isinstance(document_types, Sequence):
                return refused("invalid_parameter", "document_types must be a list of strings")
            if any(not isinstance(value, str) for value in document_types):
                return refused("invalid_parameter",
                               "every element of document_types must be a string")
            document_types = list(document_types)
        window = self._check_window(parameters.get("since"), parameters.get("until"))
        if window is not None:
            return window

        outcome = self._read(
            "search_passages",
            cypher.SEARCH_PASSAGES,
            {"lucene_query": lucene_query,
             "document_types": document_types,
             "since": parameters.get("since"),
             "until": parameters.get("until"),
             "excerpt_chars": self._excerpt_chars},
        )
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        return ok(rows, truncated=truncated)

    def _get_events_in_window(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        since = parameters["since"]
        until = parameters["until"]
        window = self._check_window(since, until, required=True)
        if window is not None:
            return window
        outcome = self._read(
            "get_events_in_window", cypher.EVENTS_IN_WINDOW, {"since": since, "until": until})
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        return ok(rows, truncated=truncated)

    def _find_counter_evidence(self, parameters: Mapping[str, Any]) -> RetrievalResult:
        """The only tool that reaches `:Issue`, and it never returns a `:NotAttempted` one.

        Zero rows is an `Ok`. "This filing records no refused claim about this metric" is a
        finding about the corpus; turning it into a `NotFound` would make an honest absence
        look like a broken identifier.
        """
        resolution = self._resolve(parameters["metric_id"])
        if isinstance(resolution, RetrievalResult):
            return resolution
        outcome = self._read(
            "find_counter_evidence",
            cypher.COUNTER_EVIDENCE,
            {"metric_id": resolution.metric_id, "period_key": parameters["period_key"]},
        )
        if isinstance(outcome, RetrievalResult):
            return outcome
        rows, truncated = outcome
        return ok(rows, truncated=truncated)

    # -- shared machinery -----------------------------------------------------------------

    def _read(
        self, tool: str, statement: str, parameters: Mapping[str, Any]
    ) -> tuple[tuple[Mapping[str, Any], ...], bool] | RetrievalResult:
        """Run one statement at `MAX_ROWS[tool] + 1` and report whether the bound bit.

        The `+ 1` is the whole of the truncation contract: the database is asked for one row
        more than the caller may have, the extra one is dropped, and its arrival is the only
        evidence that anything was dropped.

        **The `except Exception` is deliberate and is scoped to one call.** `story/` may not
        import `neo4j` (D1), so this module cannot name `Neo4jError` or `ServiceUnavailable` to
        catch them by type. The body of the `try` is a single call into the injected executor
        and contains none of this package's own logic, so nothing of ours can be swallowed —
        and a database that timed out or went away is a state §11's planner must branch on, not
        a traceback the pipeline dies of.
        """
        limit = MAX_ROWS[tool]
        try:
            rows = self._executor.read(
                statement,
                {**parameters, "row_limit": limit + 1},
                timeout_seconds=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — see the docstring; `neo4j` is unimportable here
            return unavailable(
                "graph_read_failed",
                f"{tool} could not read the graph: {type(exc).__name__}: {exc}")
        return tuple(rows[:limit]), len(rows) > limit

    def _resolve(self, surface: Any) -> MetricResolution | RetrievalResult:
        """A metric surface to one metric id, or the refusal that says why it could not be.

        Applied at every tool that names a metric, not only at `get_metric_definition`. §13.5
        makes metric identity a REFUSE, and eight surfaces in this ontology denote more than one
        metric — `gross margin` denotes `adjusted_gross_margin` and `gaap_gross_margin`, which
        moved 11.6 → -12.6 and 13.2 → 3.3 across 2022Q2/2022Q3 *(verified live)*. A tool that
        picked one would have written half the story before the planner saw it.
        """
        if not isinstance(surface, str) or not surface.strip():
            return refused("invalid_parameter", f"metric_id must be a non-empty string; got {surface!r}")
        resolution = resolve_metric_surface(self._resolved_registry(), surface)
        if resolution.is_ambiguous:
            return ambiguous(surface, resolution.candidates,
                             detail="§9 requires the caller to choose; the ontology's "
                                    "mutually_distinct_groups make these separate metrics")
        if resolution.metric_id is None:
            return not_found(
                surface,
                detail="no metric id and no alias in the ontology denotes it, and C4 forbids "
                       "reading metric metadata off the node instead")
        return resolution

    def _check_window(
        self, since: Any, until: Any, *, required: bool = False
    ) -> RetrievalResult | None:
        """`YYYY-MM-DD` on both ends, and `since <= until`.

        Dates are compared as strings by every statement in `cypher.py`, because F13 measured
        that this graph stores no temporal type — `occurred_on`, `filing_date` and
        `period_start` are all `String`. Lexicographic comparison of ISO dates is correct;
        lexicographic comparison of `2022-1-1` against `2022-09-30` is not, which is why the
        form is checked rather than trusted.
        """
        for name, value in (("since", since), ("until", until)):
            if value is None:
                if required:
                    return refused("missing_parameter", f"{name} is required")
                continue
            if not isinstance(value, str) or not _ISO_DATE.match(value):
                return refused(
                    "malformed_date",
                    f"{name}={value!r} is not YYYY-MM-DD; the graph stores dates as strings "
                    "(F13) and a malformed one compares against real dates instead of failing")
        if isinstance(since, str) and isinstance(until, str) and since > until:
            return refused("inverted_window", f"since={since} is after until={until}")
        return None

    def _resolved_registry(self) -> ConceptRegistry:
        if self._registry is None:
            self._registry = default_registry()
        return self._registry

    def _resolved_definition_hash(self) -> str:
        if self._ontology_definition_hash is None:
            self._ontology_definition_hash = default_definition_hash()
        return self._ontology_definition_hash


__all__ = [
    "DEFAULT_EXCERPT_CHARS",
    "DEFAULT_HANDLE_LIMIT",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CONTEXT_NEIGHBOURS",
    "PERIOD_SHAPES",
    "TOOL_NAMES",
    "TOOL_PARAMETERS",
    "BoundedGraphRetriever",
]
