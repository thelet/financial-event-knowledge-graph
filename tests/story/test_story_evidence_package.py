"""S5 — §10's bounds, §10.2.1's excerpts, §10.3's digest, and the four rulings S5 carries.

Offline by default. Every bound and every truncation rule is driven against a scripted retriever
so the suite runs with `neo4j` uninstalled; the `neo4j`-marked tests at the bottom build the real
packages for §6.3's three approved 2022Q3 spikes against `graph-v1-0483dc6b4b10` and assert the
things only a real graph can answer — that every citation chain closes, that the digest
reproduces on a rebuild, and what the packages actually cost in tokens.

**The scripted retriever is the database, and it is scripted from the real corpus.** The rows
below are copied from `graph-v1-0483dc6b4b10` — the same passage ids, the same `(12.6)` quote,
the same 2,164-character table — because a fixture that cannot express the failure is why a
check goes missing. AR1 found that shape once (twenty gate tests against a run directory with no
contents), R4b found it again (three divergence fixtures built on the defect they were meant to
catch), and this file is the third place it could have happened: a 40-character passage would
have made every §10.2.1 excerpt rule vacuous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pytest

from ontology import load_ontology
from story.core.graph_identity import GraphIdentity
from story.core.models import (
    BudgetParameters,
    EvidenceRequest,
    PackagedFact,
    PackagedPassage,
    PackagedSubject,
    RetrievalOutcome,
    RetrievalResult,
    RetrievalTraceEntry,
    Severity,
    StoryCandidate,
)
from story.core.periods import story_period
from story.core.series import ObservationRecord
from story.stages.detection.canonicalization import canonicalize
from story.stages.packaging import (
    CEILINGS,
    MAX_TOTAL_TOKENS_CEILING,
    PACKAGING_TOOLS,
    TRACE_ELAPSED_MS_NOT_CARRIED,
    TRIM_FLOOR,
    TRIM_ORDER,
    BoundedEvidencePackageBuilder,
    BoundPassageExcerpted,
    BudgetExceedsCeiling,
    ModelSuppliedTerm,
    PackagingError,
    UnknownWarningCode,
    derive_terms,
    estimate_tokens,
    match_basis_of,
)
from story.stages.packaging import counter_evidence as counter
from story.stages.packaging import package_assembly as assembly
from story.stages.packaging import passage_excerpts, query_terms, section_bounds
from story.stages.packaging import warning_codes as codes

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
EXTRACTION_RUN_ID = "extract-v1-lexical-833f7bcfbce9"
RUN_COMPLETE_SHA256 = "1cc8f7b01c0405311e70f5306635809c73685ed8e079cb35b92a7c36b8179d8e"
ONTOLOGY_DEFINITION_HASH = "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34"

#: §6.3's F2, and its real passages. `#p139` is the 2022Q3 financial-highlights table and
#: `#p133` is the 2022Q2 one; both are real ids on the current run and both are ~2,150
#: characters, which is the §10.2.1 median that makes the token budget bite.
Q3_DOCUMENT = "norm:0001801169:0001801169-22-000108:open-20220930.htm"
Q2_DOCUMENT = "norm:0001801169:0001801169-22-000077:open-20220630.htm"
Q3_PASSAGE = f"{Q3_DOCUMENT}#p139"
Q2_PASSAGE = f"{Q2_DOCUMENT}#p133"

#: A stand-in of the measured median length (2,144.5 characters, n = 150). Built rather than
#: pasted so the file stays readable, and long enough that every excerpt rule is exercised.
TABLE_TEXT = (
    "Financial Highlights\n\n| | Three Months Ended September 30, |\n"
    + "| Gross Margin | (12.6)% | 11.6% |\n"
    + ("| Contribution (Loss) Profit | (0.7)% | 10.1% |\n" * 30)
    + "| Adjusted EBITDA | (211) | 218 |\n"
)


# ---------------------------------------------------------------------------------------
# Fixtures — a scripted §9 retriever, and two real fact-slots to build a package from
# ---------------------------------------------------------------------------------------


@dataclass
class ScriptedRetriever:
    """A `story.contracts.GraphRetriever` with a script where the graph would be.

    Structural conformance and no import of the real retriever: the whole point of the protocol
    is that a stage above it runs with no driver, and a fake that reached for
    `BoundedGraphRetriever` would quietly undo that. `calls` records every dispatch, because
    several rules here are about which tool ran and with what.
    """

    passages: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    evidence: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    search_rows: tuple[Mapping[str, Any], ...] = ()
    search_truncated: bool = False
    counter_rows: Mapping[tuple[str, str], tuple[Mapping[str, Any], ...]] = field(
        default_factory=dict)
    counter_unavailable: tuple[str, str] | None = None
    evidence_truncated: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _trace: list[RetrievalTraceEntry] = field(default_factory=list)
    #: Mirrors `BoundedGraphRetriever`'s own attribute, which the builder reads to learn the
    #: pre-filter pool bound (R2b's `{limit: 500}`).
    _inner_search_limit: int = 500

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        self.calls.append((tool, dict(parameters)))
        result = self._dispatch(tool, parameters)
        self._trace.append(RetrievalTraceEntry(
            tool=tool, parameters=dict(parameters), row_count=len(result.rows),
            truncated=result.truncated, elapsed_ms=1.5, outcome=result.outcome))
        return result

    def trace(self) -> Sequence[RetrievalTraceEntry]:
        return tuple(self._trace)

    def _dispatch(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        if tool == "get_fact_evidence":
            row = self.evidence.get(str(parameters.get("observation_id")))
            if row is None:
                return RetrievalResult(outcome=RetrievalOutcome.NOT_FOUND, reason="not_found: x")
            return RetrievalResult(outcome=RetrievalOutcome.OK, rows=(row,),
                                   truncated=self.evidence_truncated)
        if tool == "get_passage_context":
            anchor = self.passages.get(str(parameters.get("passage_id")))
            if anchor is None:
                return RetrievalResult(outcome=RetrievalOutcome.NOT_FOUND, reason="not_found: x")
            before = int(parameters.get("before", 0))
            after = int(parameters.get("after", 0))
            rows = [{**anchor, "is_anchor": True, "offset_from_anchor": 0}]
            for offset in range(-before, after + 1):
                if offset == 0:
                    continue
                neighbour = self.passages.get(_neighbour(anchor["passage_id"], offset))
                if neighbour is not None:
                    rows.append({**neighbour, "is_anchor": False,
                                 "offset_from_anchor": offset})
            return RetrievalResult(outcome=RetrievalOutcome.OK, rows=tuple(rows))
        if tool == "search_passages":
            return RetrievalResult(outcome=RetrievalOutcome.OK, rows=self.search_rows,
                                   truncated=self.search_truncated)
        if tool == "find_counter_evidence":
            key = (str(parameters["metric_id"]), str(parameters["period_key"]))
            if self.counter_unavailable == key:
                return RetrievalResult(outcome=RetrievalOutcome.UNAVAILABLE,
                                       reason="no_document_scope: nothing to look in")
            return RetrievalResult(outcome=RetrievalOutcome.OK,
                                   rows=self.counter_rows.get(key, ()))
        raise AssertionError(f"the builder called {tool!r}, which it may not")


def _neighbour(passage_id: str, offset: int) -> str:
    document, ordinal = passage_id.rsplit("#p", 1)
    return f"{document}#p{int(ordinal) + offset}"


def passage_row(passage_id: str, text: str = TABLE_TEXT, **overrides: Any) -> dict[str, Any]:
    document_id = passage_id.rsplit("#p", 1)[0]
    row: dict[str, Any] = {
        "passage_id": passage_id,
        "document_id": document_id,
        "text": text,
        "char_count": len(text),
        "passage_kind": "table",
        "heading_path": ["Item 2.", "Financial Highlights"],
        "section_id": f"{document_id}#s81",
        "source_url": f"https://www.sec.gov/Archives/{document_id.rsplit(':', 1)[-1]}",
        "form": "10-Q",
        "document_type": "narrative_primary",
        "filing_date": "2022-11-03",
        "report_date": "2022-09-30",
    }
    row.update(overrides)
    return row


def evidence_row(observation_id: str, passage_id: str, quoted: str, **overrides: Any) -> dict:
    document_id = passage_id.rsplit("#p", 1)[0]
    row: dict[str, Any] = {
        "observation_id": observation_id,
        "passage_id": passage_id,
        "document_id": document_id,
        "quoted_text": quoted,
        "row_label": "Gross Margin",
        "column_label": "2022",
        "source_url": f"https://www.sec.gov/Archives/{document_id.rsplit(':', 1)[-1]}",
        "form": "10-Q",
        "document_type": "narrative_primary",
        "accession": document_id.split(":")[2],
        "filing_date": "2022-11-03",
        "report_date": "2022-09-30",
    }
    row.update(overrides)
    return row


def observation(observation_id: str, period_key: str, value: float, passage_id: str,
                metric_id: str = "gaap_gross_margin", **overrides: Any) -> ObservationRecord:
    start, end = {"2022Q2": ("2022-04-01", "2022-06-30"),
                  "2022Q3": ("2022-07-01", "2022-09-30")}[period_key]
    fields: dict[str, Any] = dict(
        observation_id=observation_id,
        metric_id=metric_id,
        subject_entity_id="opendoor",
        period=story_period(start, end, None),
        value=value,
        unit="percent",
        scale="units",
        currency=None,
        source_lane="normalized_table",
        validation_state="clean",
        document_id=passage_id.rsplit("#p", 1)[0],
        passage_id=passage_id,
        filing_date="2022-11-03",
        quoted_text="(12.6)",
    )
    fields.update(overrides)
    return ObservationRecord(**fields)


IDENTITY = GraphIdentity(
    graph_run_id=GRAPH_RUN_ID,
    graph_projection_version="1.2.0",
    extraction_run_id=EXTRACTION_RUN_ID,
    extraction_run_directory=f"data/extraction_runs/{EXTRACTION_RUN_ID}",
    run_complete_sha256=RUN_COMPLETE_SHA256,
    input_content_digest="40ea214c701c",
    ontology_id="real_estate_marketplace_v1",
    ontology_version="2.0.0",
    ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
    node_count=28836,
    edge_count=35600,
)


def make_records() -> tuple[ObservationRecord, ...]:
    """Two slots of `gaap_gross_margin`, the 2022Q2 one carrying three readings.

    The asymmetry is the point: a straight prefix of the fact ranking would spend the whole cap
    on 2022Q2 and ship a move with one side.
    """
    return (
        observation("obs:ggm:2022Q3:a", "2022Q3", -12.6, Q3_PASSAGE),
        observation("obs:ggm:2022Q2:a", "2022Q2", 11.6, Q2_PASSAGE),
        observation("obs:ggm:2022Q2:b", "2022Q2", 11.6, f"{Q2_DOCUMENT}#p134"),
        observation("obs:ggm:2022Q2:c", "2022Q2", 11.6, f"{Q2_DOCUMENT}#p135"),
    )


def make_candidate(**overrides: Any) -> StoryCandidate:
    fields: dict[str, Any] = dict(
        candidate_id="cand:metric-move:gaap-gross-margin:opendoor:2022Q2_2022Q3:15b62d34dbaa",
        detector_id="detector:metric_move",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id=GRAPH_RUN_ID,
        subject_entity_id="opendoor",
        story_type="metric_move",
        metric_ids=("gaap_gross_margin",),
        anchor_period_keys=("2022Q2", "2022Q3"),
        anchor_observation_ids=("obs:ggm:2022Q2:a", "obs:ggm:2022Q3:a"),
        signals={"delta_pp": -24.2, "crosses_zero": True},
        evidence_request=EvidenceRequest(
            metric_ids=("gaap_gross_margin",),
            period_keys=("2022Q2", "2022Q3"),
            observation_ids=("obs:ggm:2022Q2:a", "obs:ggm:2022Q3:a"),
        ),
    )
    fields.update(overrides)
    return StoryCandidate(**fields)


def make_retriever(**overrides: Any) -> ScriptedRetriever:
    passages = {
        row["passage_id"]: row
        for row in (
            passage_row(Q3_PASSAGE),
            passage_row(f"{Q3_DOCUMENT}#p138", text="Preceding narrative. " * 20),
            passage_row(f"{Q3_DOCUMENT}#p140", text="Following narrative. " * 20),
            passage_row(Q2_PASSAGE),
            passage_row(f"{Q2_DOCUMENT}#p134"),
            passage_row(f"{Q2_DOCUMENT}#p135"),
        )
    }
    evidence = {
        "obs:ggm:2022Q3:a": evidence_row("obs:ggm:2022Q3:a", Q3_PASSAGE, "(12.6)"),
        "obs:ggm:2022Q2:a": evidence_row("obs:ggm:2022Q2:a", Q2_PASSAGE, "11.6"),
        "obs:ggm:2022Q2:b": evidence_row("obs:ggm:2022Q2:b", f"{Q2_DOCUMENT}#p134", "11.6"),
        "obs:ggm:2022Q2:c": evidence_row("obs:ggm:2022Q2:c", f"{Q2_DOCUMENT}#p135", "11.6"),
    }
    fields: dict[str, Any] = dict(passages=passages, evidence=evidence)
    fields.update(overrides)
    return ScriptedRetriever(**fields)


@pytest.fixture(scope="module")
def registry():  # type: ignore[no-untyped-def]
    """The real ontology. C4 makes it authoritative for every metric field in the package, and a
    hand-built stub would mean testing the package against a definition set nobody ships."""
    return load_ontology("real_estate_marketplace_v1").registry


def make_builder(retriever: ScriptedRetriever, registry, **overrides: Any):  # type: ignore[no-untyped-def]
    records = overrides.pop("records", make_records())
    fields: dict[str, Any] = dict(
        identity=IDENTITY,
        records=records,
        points=canonicalize(records),
        registry=registry,
    )
    fields.update(overrides)
    return BoundedEvidencePackageBuilder(retriever, **fields)


def build_package(registry, retriever: ScriptedRetriever | None = None, **overrides: Any):  # type: ignore[no-untyped-def]
    candidate = overrides.pop("candidate", make_candidate())
    request = overrides.pop("request", candidate.evidence_request)
    scripted = retriever if retriever is not None else make_retriever()
    return make_builder(scripted, registry, **overrides).build(candidate, request)


# ---------------------------------------------------------------------------------------
# §10.1 — the warning vocabulary
# ---------------------------------------------------------------------------------------


def test_a_warning_code_the_vocabulary_does_not_declare_cannot_be_constructed():
    """§10.1's claim is that every warning is computable and named. A code invented at a call
    site is renderable by no evidence panel and requireable by no §13 check."""
    with pytest.raises(UnknownWarningCode) as raised:
        codes.packaged_warning("looks_fine_to_me")

    assert "looks_fine_to_me" in str(raised.value)
    assert codes.UNPREFERRED_SOURCE_LANE in str(raised.value)


def test_every_declared_warning_code_carries_a_severity_the_gate_can_act_on():
    assert set(codes.SEVERITY_OF.values()) <= set(Severity)
    assert codes.SEVERITY_OF[codes.OBSERVATION_LOAD_INCOMPLETE] is Severity.REFUSE
    assert codes.SEVERITY_OF[codes.EVIDENCE_CHAIN_INCOMPLETE] is Severity.REFUSE
    assert codes.SEVERITY_OF[codes.PACKAGE_EXCEEDS_TOKEN_CEILING] is Severity.REFUSE


def test_the_warning_cap_drops_the_least_severe_row_so_a_refusal_can_never_be_capped_away():
    """D6's ruling, applied to §10.2's 20-warning bound: nothing dropped may outrank anything
    kept. A cap that dropped a REFUSE while keeping an ADVISORY would hide the only rows §13.17
    acts on."""
    warnings = [
        codes.packaged_warning(codes.RELATIONSHIPS_UNAVAILABLE_IN_V1),
        codes.packaged_warning(codes.SINGLE_SOURCE, subject_ids=("a",)),
        codes.packaged_warning(codes.EVIDENCE_CHAIN_INCOMPLETE, subject_ids=("b",)),
        codes.packaged_warning(codes.RETRIEVAL_TRUNCATED, subject_ids=("c",)),
    ]

    kept, dropped = section_bounds.truncate(warnings, 2, key=codes.warning_sort_key)

    assert [w.severity for w in kept] == [Severity.REFUSE, Severity.WARN]
    assert dropped == 2
    assert codes.blocking(kept) == (kept[0],)


def test_a_warnings_subject_ids_are_sorted_so_two_visit_orders_are_one_digest():
    forward = codes.packaged_warning(codes.SINGLE_SOURCE, subject_ids=("b", "a", "c"))
    backward = codes.packaged_warning(codes.SINGLE_SOURCE, subject_ids=("c", "b", "a"))

    assert forward.subject_ids == ("a", "b", "c") == backward.subject_ids


# ---------------------------------------------------------------------------------------
# §10.2 — the bounds table and the drop rules
# ---------------------------------------------------------------------------------------


def test_every_section_the_plan_bounds_has_a_ceiling_and_a_budget_field():
    """§0c item 4: the first draft bounded seven of sixteen sections. This is the other nine."""
    assert set(CEILINGS) == set(section_bounds.CAP_FIELDS)
    defaults = BudgetParameters()
    for section, field_name in section_bounds.CAP_FIELDS.items():
        assert hasattr(defaults, field_name), section
        assert getattr(defaults, field_name) <= CEILINGS[section], section


def test_the_plans_own_ceiling_numbers_are_the_ones_this_module_enforces():
    """Copied from §10.2's table by hand, so a silently widened bound fails here rather than
    passing because the table and the code were changed together."""
    assert CEILINGS == {
        "facts": 24, "events": 8, "relationships": 8, "primary_passages": 6,
        "context_passages": 2, "explanatory_passages": 5, "counter_evidence": 6,
        "documents": 20, "metrics": 8, "formula_windows": 8, "warnings": 20,
        "conflicts": 8, "compatibility": 12, "retrieval_trace": 40,
    }
    assert MAX_TOTAL_TOKENS_CEILING == 6000
    assert BudgetParameters().max_total_tokens == 5000


def test_a_configured_cap_above_the_plans_ceiling_is_refused_rather_than_clamped(registry):
    """§10.2.1 measured 8 primaries with context at ~12,900 tokens — more than the local
    runtime's whole 8,192 context. A clamp would report that configuration as honoured."""
    with pytest.raises(BudgetExceedsCeiling) as raised:
        make_builder(make_retriever(), registry,
                     budget=BudgetParameters(max_primary_passages=12))

    assert "max_primary_passages=12" in str(raised.value)
    assert "6" in str(raised.value)


def test_a_configured_token_budget_above_the_plans_ceiling_is_refused(registry):
    with pytest.raises(BudgetExceedsCeiling):
        make_builder(make_retriever(), registry,
                     budget=BudgetParameters(max_total_tokens=9000))


def test_the_token_estimator_reproduces_the_plans_own_measured_passage_arithmetic():
    """§10.2.1 sizes *"1 backing passage, median → 536 tokens"* against a measured median of
    2,144.5 characters. 2144.5/4 = 536.1, so the estimator restates the plan's constant rather
    than inventing a second one."""
    assert section_bounds.CHARS_PER_TOKEN == 4
    # 2,142 characters plus the two quotes JSON adds is 2,144 — the measured median to the
    # character — and 2144/4 is exactly the 536 the plan's table states.
    assert estimate_tokens("x" * 2142) == 536


def test_the_trim_order_touches_only_evidence_and_never_the_provenance_the_review_needs():
    """`retrieval_trace[]` and `warnings[]` are bounded by their own caps and are deliberately
    absent from the token trimmer: §11's third consequence requires the trace to record the
    exact terms and the truncated flag *"so a reviewer can see what the bound cut"*, and a
    trimmer that removed that row would delete the record of its own deletion."""
    assert TRIM_ORDER == ("explanatory_passages", "context_passages",
                          "primary_passages", "counter_evidence")
    assert set(TRIM_ORDER) == set(TRIM_FLOOR)
    assert TRIM_FLOOR["primary_passages"] == 1
    assert TRIM_FLOOR["counter_evidence"] == 1
    assert "retrieval_trace" not in TRIM_ORDER
    assert "warnings" not in TRIM_ORDER
    assert "facts" not in TRIM_ORDER


def test_counter_evidence_is_the_last_section_the_trimmer_touches():
    """§11 requires a non-empty `counterpoints` whenever `counter_evidence` is non-empty. A
    trimmer that emptied the section would discharge that rule by deleting the evidence, which
    is the silent-omission channel §11 exists to close."""
    assert TRIM_ORDER[-1] == "counter_evidence"
    assert TRIM_ORDER.index("explanatory_passages") == 0


# ---------------------------------------------------------------------------------------
# §10.2.1 — the ±400 window, and the passages that may never have one
# ---------------------------------------------------------------------------------------


def test_a_passage_shorter_than_the_window_is_returned_whole_and_not_called_an_excerpt():
    """A "window" containing the whole passage is not an excerpt, and reporting it as one would
    make an evidence panel offer to fetch a rest that does not exist."""
    excerpt = passage_excerpts.window("short passage", needles=("passage",), radius=400)

    assert excerpt.excerpted is False
    assert (excerpt.char_start, excerpt.char_end) == (0, len("short passage"))


def test_the_window_is_centred_on_the_first_needle_and_its_offsets_index_the_full_text():
    text = "A" * 1000 + "Gross Margin" + "B" * 1000
    excerpt = passage_excerpts.window(text, needles=("Gross Margin",), radius=400)

    assert excerpt.excerpted is True
    assert excerpt.text == text[excerpt.char_start:excerpt.char_end]
    assert excerpt.char_end - excerpt.char_start == 800
    assert "Gross Margin" in excerpt.text
    assert excerpt.matched_on == "Gross Margin"


def test_needles_are_tried_in_the_order_given_rather_than_by_which_one_occurs_first():
    """The order is the caller's statement of what the excerpt is evidence *of*. A
    closest-match rule would let an issue code appearing in a footnote pull the window off the
    row the refusal is about."""
    text = "B" * 900 + "second needle" + "C" * 900 + "first needle" + "D" * 900
    excerpt = passage_excerpts.window(
        text, needles=("first needle", "second needle"), radius=400)

    assert excerpt.matched_on == "first needle"
    assert "first needle" in excerpt.text
    assert "second needle" not in excerpt.text


def test_matching_is_case_insensitive_because_one_filing_prints_gross_margin_two_ways():
    """Measured on `graph-v1-0483dc6b4b10`: the 2022Q3 evidence edge carries `row_label: "Gross
    Margin"` and the 2022Q2 one carries `"Gross margin"`. A case-sensitive search would fall
    back to the head of the passage for one of them and nobody would notice."""
    text = "E" * 1000 + "Gross margin" + "F" * 1000
    excerpt = passage_excerpts.window(text, needles=("Gross Margin",), radius=400)

    assert excerpt.matched_on == "Gross Margin"
    assert "Gross margin" in excerpt.text


def test_the_window_falls_back_to_the_head_when_no_needle_occurs_and_says_it_matched_nothing():
    text = "G" * 3000
    excerpt = passage_excerpts.window(text, needles=("absent",), radius=400)

    assert (excerpt.char_start, excerpt.char_end, excerpt.matched_on) == (0, 800, "")
    assert excerpt.excerpted is True


def test_a_window_at_the_very_end_of_a_passage_keeps_its_full_width():
    text = "H" * 2000 + "needle"
    excerpt = passage_excerpts.window(text, needles=("needle",), radius=400)

    assert excerpt.char_end == len(text)
    assert excerpt.char_end - excerpt.char_start == 800


def test_excerpting_a_passage_a_fact_is_bound_to_is_refused_because_rule_a_needs_the_table():
    """§13.7.1: 179 of 485 `(passage_id, column_label)` pairs are ambiguous, covering 61.3% of
    table observations. That check cannot run on a fragment — an excerpt carrying one column
    would let an ambiguous pair look unique and turn a REFUSE into a pass."""
    with pytest.raises(BoundPassageExcerpted) as raised:
        passage_excerpts.refuse_excerpting_bound_passage(Q3_PASSAGE, [Q3_PASSAGE, Q2_PASSAGE])

    assert "column_label" in str(raised.value)
    passage_excerpts.refuse_excerpting_bound_passage("other#p1", [Q3_PASSAGE])


# ---------------------------------------------------------------------------------------
# §11's correction — the terms come from the candidate
# ---------------------------------------------------------------------------------------


def test_query_terms_are_derived_from_the_metric_label_and_the_ontologys_own_aliases(registry):
    derived = derive_terms(
        registry, metric_ids=("gaap_gross_margin",), period_keys=("2022Q2", "2022Q3"))

    definition = registry.find("gaap_gross_margin")
    assert derived.terms[0] == definition.label
    assert set(derived.metric_surfaces) <= {definition.label, *definition.aliases}
    assert derived.period_surfaces == ("2022Q2", "2022Q3")


def test_a_period_key_the_corpus_never_prints_is_dropped_rather_than_searched_for(registry):
    """`PeriodRef.key` falls back to `{start}_{end}` for a window it cannot name — three
    cross-year windows in this run. Searching for `2022-04-01_2022-12-31` matches nothing while
    looking like a period filter."""
    derived = derive_terms(
        registry, metric_ids=("gaap_gross_margin",),
        period_keys=("2022Q3", "2022-04-01_2022-12-31"))

    assert derived.period_surfaces == ("2022Q3",)
    assert "2022-04-01_2022-12-31" not in derived.terms


def test_a_lucene_keyword_can_never_become_a_pipeline_query_term(registry):
    """`["margin", "NOT", "gross"]` is a boolean exclusion, not a filter — a model-controlled
    channel for suppressing evidence. Escaping handles it at the tool; this refuses it a layer
    earlier, where a *derived* `NOT` would be a defect rather than a query."""
    derived = derive_terms(registry, metric_ids=(), period_keys=("NOT ", "2022Q3"))

    assert derived.terms == ("2022Q3",)
    assert derived.dropped == ("NOT ",)


def test_a_term_that_no_derivation_produced_is_refused_before_it_reaches_the_tool(registry):
    """§11's guarantee is not *"this module derives terms"* — it is *"no other string ever
    reaches the tool"*, and the two are different claims."""
    derived = derive_terms(registry, metric_ids=("gaap_gross_margin",), period_keys=("2022Q3",))

    with pytest.raises(ModelSuppliedTerm) as raised:
        query_terms.refuse_undeclared_terms([*derived.terms, "the"], derived)

    assert "'the'" in str(raised.value)
    assert "18 of" in str(raised.value)


def test_the_derived_term_list_is_bounded_because_every_added_term_re_ranks(registry):
    """AR1: adding three innocuous terms to a two-term query evicted 18 of the 25 passages the
    original returned. The count is part of the query's identity, so it is bounded."""
    derived = derive_terms(
        registry,
        metric_ids=("gaap_gross_margin", "adjusted_gross_margin", "contribution_margin"),
        period_keys=("2022Q1", "2022Q2", "2022Q3", "2022Q4"),
        max_terms=3)

    assert len(derived.terms) == 3
    assert derived.dropped


# ---------------------------------------------------------------------------------------
# The counter-evidence ruling — document grain, narrowed, and disclosed
# ---------------------------------------------------------------------------------------


def issue_row(issue_id: str, passage_id: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "issue_id": issue_id,
        "code": "AMBIGUOUS_ALIAS",
        "severity": "refusal",
        "severity_rank": 1,
        "detail": "two concepts share this surface",
        "rejected_claim": False,
        "row_label": "Gross Margin",
        "concept_ids": ["gaap_gross_margin", "adjusted_gross_margin"],
        "lane": "normalized_table",
        "metric_id": "gaap_gross_margin",
        "passage_id": passage_id,
        "document_id": passage_id.rsplit("#p", 1)[0],
        "document_type": "narrative_primary",
    }
    row.update(overrides)
    return row


def test_a_refusal_from_a_filing_the_package_cites_nothing_from_is_dropped_and_named():
    rows = (issue_row("issue:a", Q3_PASSAGE),
            issue_row("issue:b", "norm:0001801169:other:x.htm#p1"))

    kept, dropped = counter.narrow(
        rows, metric_ids=("gaap_gross_margin",), cited_document_ids=(Q3_DOCUMENT,))

    assert [row["issue_id"] for row in kept] == ["issue:a"]
    assert dropped == ("issue:b",)


def test_a_refusal_that_shares_no_concept_with_the_candidate_is_a_different_quantity():
    """`concept_ids` names what an `AMBIGUOUS_ALIAS` refusal could not choose between. A
    refusal about homes sold that happens to sit in the same filing is not counter-evidence
    about a margin."""
    rows = (issue_row("issue:c", Q3_PASSAGE, concept_ids=["homes_sold", "homes_purchased"]),)

    kept, dropped = counter.narrow(
        rows, metric_ids=("gaap_gross_margin",), cited_document_ids=(Q3_DOCUMENT,))

    assert kept == ()
    assert dropped == ("issue:c",)


def test_an_issue_carrying_no_concept_ids_is_kept_because_absence_asserts_nothing():
    """C2: Neo4j stores no null, so an absent `concept_ids` is *"the node carries no such
    property"* and never *"this concerns nothing"*. `UNIT_CONTRADICTS_ONTOLOGY` rows carry none."""
    rows = (issue_row("issue:d", Q3_PASSAGE, code="UNIT_CONTRADICTS_ONTOLOGY",
                      concept_ids=None),)

    kept, _dropped = counter.narrow(
        rows, metric_ids=("gaap_gross_margin",), cited_document_ids=(Q3_DOCUMENT,))

    assert [row["issue_id"] for row in kept] == ["issue:d"]


def test_two_refusals_in_one_passage_ship_as_one_row_of_evidence():
    """Nine `AMBIGUOUS_ALIAS` refusals over four passages is four rows, not nine — measured
    live on `contribution_margin` 2022Q3. Shipping one passage twice spends §10.2's budget
    twice to say one thing."""
    rows = (issue_row("issue:e", Q3_PASSAGE),
            issue_row("issue:f", Q3_PASSAGE, severity="rejection", severity_rank=0))

    kept, dropped = counter.narrow(
        rows, metric_ids=("gaap_gross_margin",), cited_document_ids=(Q3_DOCUMENT,))

    assert len(kept) == 1
    assert dropped == ("issue:f",)


def counter_row(basis: str, rank: int, issue_id: str,
                passage_id: str = Q3_PASSAGE) -> counter.CounterEvidenceRow:
    excerpt = passage_excerpts.window(TABLE_TEXT, needles=("Gross Margin",))
    return counter.CounterEvidenceRow(
        passage=_packaged(passage_id, excerpt), match_basis=basis, issue_id=issue_id,
        code="AMBIGUOUS_ALIAS", severity="refusal", severity_rank=rank,
        metric_id="gaap_gross_margin", period_key="2022Q3", row_label="Gross Margin",
        excerpt=excerpt)


def _packaged(passage_id: str, excerpt: passage_excerpts.Excerpt) -> PackagedPassage:
    return PackagedPassage(
        passage_id=passage_id, document_id=passage_id.rsplit("#p", 1)[0], text=excerpt.text,
        char_count=len(TABLE_TEXT), char_start=excerpt.char_start, char_end=excerpt.char_end,
        excerpted=excerpt.excerpted)


def test_counter_evidence_is_ordered_passage_grain_first_then_most_severe():
    ordered = sorted(
        (counter_row(counter.MATCH_BASIS_SAME_DOCUMENT, 0, "issue:rejection-elsewhere"),
         counter_row(counter.MATCH_BASIS_SAME_PASSAGE, 1, "issue:refusal-here")),
        key=lambda item: item.sort_key)

    assert [item.issue_id for item in ordered] == [
        "issue:refusal-here", "issue:rejection-elsewhere"]


def test_a_document_grain_association_is_never_presented_as_a_direct_contradiction():
    """§13.14 is waiting for a draft that reads a neighbouring table's refusal as a
    contradiction of the cited cell. The disclosure says which it is, in the ANNOTATE row the
    evidence panel renders beside the claim."""
    disclosure = counter_row(
        counter.MATCH_BASIS_SAME_DOCUMENT, 1, "issue:g").disclosure()

    assert disclosure.code == codes.COUNTER_EVIDENCE_SAME_DOCUMENT
    assert disclosure.severity is Severity.ANNOTATE
    assert "match_basis=same_document" in disclosure.detail
    assert "not a contradiction of the cited cell" in disclosure.detail


# ---------------------------------------------------------------------------------------
# The builder — every bound, every rule, offline
# ---------------------------------------------------------------------------------------


def test_the_package_carries_the_run_identity_the_projection_recorded(registry):
    package = build_package(registry)

    assert package.graph_run_id == GRAPH_RUN_ID
    assert package.extraction_run_id == EXTRACTION_RUN_ID
    assert package.run_complete_sha256 == RUN_COMPLETE_SHA256
    assert package.ontology_definition_hash == ONTOLOGY_DEFINITION_HASH
    assert package.ontology_semantic_version == "2.0.0"
    assert package.graph_projection_version == "1.2.0"
    assert package.package_id.startswith("pkg:metric-move-gaap-gross-margin-opendoor-")


def test_every_section_of_a_built_package_is_inside_its_own_bound(registry):
    package = build_package(registry)
    budget = package.budget.parameters

    assert len(package.facts) <= budget.max_facts
    assert len(package.events) <= budget.max_events
    assert len(package.relationships) <= budget.max_relationships
    assert len(package.primary_passages) <= budget.max_primary_passages
    assert len(package.context_passages) <= (
        budget.max_context_neighbours * 2 * len(package.primary_passages))
    assert len(package.explanatory_passages) <= budget.max_explanatory_passages
    assert len(package.counter_evidence) <= budget.max_counter_evidence
    assert len(package.documents) <= budget.max_documents
    assert len(package.metrics) <= budget.max_metrics
    assert len(package.formula_windows) <= budget.max_formula_windows
    assert len(package.warnings) <= budget.max_warnings
    assert len(package.conflicts) <= budget.max_conflicts
    assert len(package.compatibility) <= budget.max_compatibility
    assert len(package.retrieval_trace) <= budget.max_retrieval_trace
    assert package.budget.token_estimate <= MAX_TOTAL_TOKENS_CEILING


def test_the_fact_cap_deals_round_robin_so_a_busy_slot_cannot_starve_the_other_half_of_a_move(
        registry):
    """The 2022Q2 slot holds three readings and 2022Q3 holds one. A straight prefix of the fact
    ranking at a cap of two would ship two 2022Q2 readings and no 2022Q3 — a package about a
    move that carries one side of it."""
    package = build_package(registry, budget=BudgetParameters(max_facts=2))

    assert {fact.period_key for fact in package.facts} == {"2022Q2", "2022Q3"}


def test_dropping_a_primary_passage_drops_the_facts_that_were_bound_to_it(registry):
    """§13.7's Rule A needs the whole passage a fact cites. A fact left behind by the passage
    cap would satisfy every count in §10.2 with a citation chain that does not close."""
    package = build_package(registry, budget=BudgetParameters(max_primary_passages=1))

    kept = {passage.passage_id for passage in package.primary_passages}
    assert len(kept) == 1
    assert {fact.passage_id for fact in package.facts} <= kept
    assert "primary_passages" in package.budget.caps_hit


def test_no_trim_may_strand_a_slot_the_candidate_anchored_on(registry):
    """§6.11 digests `anchor_observation_ids` into the `candidate_id`, so a package carrying
    only the 2022Q2 side of a 2022Q2 → 2022Q3 move is a package whose own id claims evidence it
    does not hold. Measured before this floor existed: `contribution_margin 2022Q2 → 2022Q3`
    was trimmed to the 2022Q2 reading alone."""
    package = build_package(registry, budget=BudgetParameters(max_total_tokens=1))

    assert {(fact.metric_id, fact.period_key) for fact in package.facts} == {
        ("gaap_gross_margin", "2022Q2"), ("gaap_gross_margin", "2022Q3")}
    assert codes.TOKEN_BUDGET_TRIMMED in {w.code for w in package.warnings}


def test_a_package_that_cannot_be_trimmed_under_the_ceiling_refuses_rather_than_pretending(
        registry):
    """The local runtime is `-c 8192` with `max_output_tokens 1024`. A package over §10.2's
    6,000-token ceiling leaves no room for the system prompt, so a draft written from it would
    be written from a truncated universe — and that is a REFUSE for §13.17's gate, not a note."""
    huge = "Z" * 40_000
    retriever = make_retriever(passages={
        Q3_PASSAGE: passage_row(Q3_PASSAGE, text=huge),
        Q2_PASSAGE: passage_row(Q2_PASSAGE, text=huge),
    })
    package = build_package(registry, retriever)

    assert package.budget.token_estimate > MAX_TOTAL_TOKENS_CEILING
    assert codes.PACKAGE_EXCEEDS_TOKEN_CEILING in {w.code for w in codes.blocking(package.warnings)}


def test_context_passages_are_the_neighbours_of_a_primary_and_never_a_primary_itself(registry):
    package = build_package(
        registry, budget=BudgetParameters(max_primary_passages=1, max_total_tokens=6000))

    primaries = {passage.passage_id for passage in package.primary_passages}
    context = {passage.passage_id for passage in package.context_passages}
    assert context
    assert not (context & primaries)
    assert all(passage.excerpted is False for passage in package.context_passages)


def test_a_primary_passage_is_never_excerpted_and_a_context_passage_is_never_excerpted(registry):
    package = build_package(registry, budget=BudgetParameters(max_total_tokens=6000))

    for passage in (*package.primary_passages, *package.context_passages):
        assert passage.excerpted is False
        assert passage.char_start == 0
        assert passage.char_end == passage.char_count


# -- ruling 1: a truncated read is never treated as complete ---------------------------------


def test_the_builder_refuses_to_call_get_metric_history_at_all(registry):
    """`get_metric_history` is bounded at 200 and five metrics exceed it. AR2's b1 measured what
    one un-paged call costs: 93 candidates and zero refusals, losing 20 real candidates and
    giving 3 slots a wrong `n_docs`. Only canonical-series construction may page and prove
    completeness, so the name is refused here rather than merely unused."""
    assert "get_metric_history" not in PACKAGING_TOOLS
    assert PACKAGING_TOOLS == {"get_fact_evidence", "get_passage_context",
                               "search_passages", "find_counter_evidence"}

    builder = make_builder(make_retriever(), registry)
    with pytest.raises(PackagingError) as raised:
        builder._call("get_metric_history", {"metric_id": "gaap_gross_margin"})

    assert "200" in str(raised.value)
    assert "93 candidates" in str(raised.value)


def test_a_built_package_names_only_tools_the_stage_may_call(registry):
    retriever = make_retriever()
    build_package(registry, retriever)

    assert {tool for tool, _parameters in retriever.calls} <= PACKAGING_TOOLS


def test_an_incomplete_observation_load_becomes_a_refusing_warning_and_never_silence(registry):
    package = build_package(
        registry, unreadable=(("contribution_margin", "paging stalled at '2023-09-30'"),))

    blocking = {warning.code for warning in codes.blocking(package.warnings)}
    assert codes.OBSERVATION_LOAD_INCOMPLETE in blocking
    detail = next(w.detail for w in package.warnings
                  if w.code == codes.OBSERVATION_LOAD_INCOMPLETE)
    assert "contribution_margin" in detail
    assert "paging stalled" in detail


def test_a_truncated_evidence_read_is_disclosed_rather_than_taken_as_the_whole_chain(registry):
    package = build_package(registry, make_retriever(evidence_truncated=True))

    truncations = [w for w in package.warnings if w.code == codes.RETRIEVAL_TRUNCATED]
    assert truncations
    assert "10-row bound" in truncations[0].detail


def test_a_truncated_search_is_disclosed_with_the_bound_that_bit(registry):
    retriever = make_retriever(
        search_rows=tuple(
            {"passage_id": f"{Q3_DOCUMENT}#p{200 + n}", "document_id": Q3_DOCUMENT,
             "score": 5.0 - n, "candidate_pool_size": 120, "text_excerpt": "x" * 400,
             "char_count": 400}
            for n in range(3)),
        search_truncated=True)
    request = make_candidate().evidence_request.model_copy(
        update={"want_explanatory_search": True})
    package = build_package(registry, retriever, request=request)

    assert codes.RETRIEVAL_TRUNCATED in {w.code for w in package.warnings}


# -- ruling 2: the capped filtered pool ------------------------------------------------------


def search_row(ordinal: int, pool: int) -> dict[str, Any]:
    passage_id = f"{Q3_DOCUMENT}#p{300 + ordinal}"
    return {
        "passage_id": passage_id,
        "document_id": Q3_DOCUMENT,
        "score": 5.0 - ordinal,
        "candidate_pool_size": pool,
        "text_excerpt": "y" * 400,
        "char_count": 400,
        "form": "10-Q",
        "document_type": "narrative_primary",
        "filing_date": "2022-11-03",
        "report_date": "2022-09-30",
    }


def test_a_capped_pool_behind_a_filter_drops_the_explanatory_section_and_records_everything(
        registry):
    """R2b's measurement: `{limit: 500}` is applied **before** the document filter, so
    `["the"]` + `shareholder_letter` returns 9 rows where the unbounded pool returns 26. Rows
    below a capped pool cannot be told apart from rows the corpus does not have, so the section
    is dropped rather than shipped short — and the warning carries the pool limit, the rows
    after filters, the query-term source and the filter basis."""
    retriever = make_retriever(search_rows=tuple(search_row(n, pool=500) for n in range(3)))
    request = make_candidate().evidence_request.model_copy(
        update={"want_explanatory_search": True})
    package = build_package(registry, retriever, request=request)

    assert package.explanatory_passages == ()
    warning = next(w for w in package.warnings if w.code == codes.SEARCH_POOL_STARVED)
    assert "candidate_pool_size=500" in warning.detail
    assert "inner limit of 500" in warning.detail
    assert "3 row(s) survived the filters" in warning.detail
    assert "query_term_source=code_derived_from_candidate" in warning.detail
    assert "filter_basis={'since'" in warning.detail


def test_a_capped_pool_with_no_filter_keeps_its_rows_and_still_records_the_cap(registry):
    """*"Do not automatically reject every capped pool"* — reject only where completeness
    cannot be established. With no filter the cap changed the ranking pool and not the
    eligibility of any row, so the rows stand and the cap is disclosed."""
    retriever = make_retriever(search_rows=tuple(search_row(n, pool=500) for n in range(3)))
    candidate = make_candidate()
    # An anchor period with no dated record leaves `_search_window` empty, which is the
    # unfiltered case: the tool is called with terms alone.
    request = EvidenceRequest(metric_ids=("gaap_gross_margin",), period_keys=(),
                              want_explanatory_search=True)
    package = build_package(registry, retriever, candidate=candidate, request=request,
                            budget=BudgetParameters(max_total_tokens=6000))

    assert package.explanatory_passages
    warning = next(w for w in package.warnings if w.code == codes.SEARCH_POOL_CAPPED)
    assert "No document filter was applied" in warning.detail
    assert codes.SEARCH_POOL_STARVED not in {w.code for w in package.warnings}


def test_an_uncapped_pool_raises_no_pool_warning_at_all(registry):
    retriever = make_retriever(search_rows=tuple(search_row(n, pool=278) for n in range(3)))
    request = make_candidate().evidence_request.model_copy(
        update={"want_explanatory_search": True})
    package = build_package(registry, retriever, request=request,
                            budget=BudgetParameters(max_total_tokens=6000))

    assert package.explanatory_passages
    codes_present = {w.code for w in package.warnings}
    assert codes.SEARCH_POOL_CAPPED not in codes_present
    assert codes.SEARCH_POOL_STARVED not in codes_present


def test_an_explanatory_passage_is_excerpted_and_carries_the_terms_that_found_it(registry):
    retriever = make_retriever(search_rows=tuple(search_row(n, pool=278) for n in range(3)))
    retriever.passages = {
        **retriever.passages,
        **{search_row(n, 278)["passage_id"]: passage_row(
            search_row(n, 278)["passage_id"], text="J" * 900 + "Gross Margin" + "K" * 900)
           for n in range(3)},
    }
    request = make_candidate().evidence_request.model_copy(
        update={"want_explanatory_search": True})
    package = build_package(registry, retriever, request=request,
                            budget=BudgetParameters(max_total_tokens=6000))

    assert package.explanatory_passages
    for passage in package.explanatory_passages:
        assert passage.excerpted is True
        assert passage.char_end - passage.char_start == 800
        assert passage.char_count > passage.char_end - passage.char_start
        assert "Gross Margin" in passage.text
        assert passage.query_terms
        assert passage.score is not None


# -- ruling 4: counter-evidence match basis --------------------------------------------------


def test_every_counter_evidence_row_in_a_package_carries_a_readable_match_basis(registry):
    other_passage = f"{Q3_DOCUMENT}#p120"
    retriever = make_retriever(
        counter_rows={("gaap_gross_margin", "2022Q3"): (issue_row("issue:h", other_passage),)})
    retriever.passages = {**retriever.passages,
                          other_passage: passage_row(other_passage, text="L" * 3000)}
    package = build_package(registry, retriever,
                            budget=BudgetParameters(max_total_tokens=6000))

    assert len(package.counter_evidence) == 1
    basis = match_basis_of(package)
    assert basis == {other_passage: counter.MATCH_BASIS_SAME_DOCUMENT}
    assert package.counter_evidence[0].excerpted is True


def test_a_refusal_in_a_passage_a_fact_cites_is_carried_whole_so_rule_a_cannot_read_a_fragment(
        registry):
    """Two `PackagedPassage` rows under one `passage_id` with different text would let a
    consumer indexing by id run §13.7's column check against an excerpt. Carried whole, the two
    rows are byte-identical and the ambiguity does not exist."""
    retriever = make_retriever(
        counter_rows={("gaap_gross_margin", "2022Q3"): (issue_row("issue:i", Q3_PASSAGE),)})
    package = build_package(registry, retriever,
                            budget=BudgetParameters(max_total_tokens=6000))

    assert match_basis_of(package) == {Q3_PASSAGE: counter.MATCH_BASIS_SAME_PASSAGE}
    primary = next(p for p in package.primary_passages if p.passage_id == Q3_PASSAGE)
    assert package.counter_evidence[0].text == primary.text
    assert package.counter_evidence[0].excerpted is False


def test_a_disclosure_for_a_counter_evidence_row_the_trim_removed_is_removed_with_it(registry):
    """A warning naming a `passage_id` the package no longer carries is worse than no warning:
    it spends one of the twenty slots on an id nobody can resolve."""
    rows = tuple(
        issue_row(f"issue:{n}", f"{Q3_DOCUMENT}#p{400 + n}", severity_rank=n)
        for n in range(3))
    retriever = make_retriever(counter_rows={("gaap_gross_margin", "2022Q3"): rows})
    retriever.passages = {
        **retriever.passages,
        **{row["passage_id"]: passage_row(row["passage_id"], text="M" * 4000) for row in rows},
    }
    package = build_package(registry, retriever, budget=BudgetParameters(max_total_tokens=1))

    assert len(package.counter_evidence) == TRIM_FLOOR["counter_evidence"]
    assert set(match_basis_of(package)) == {
        passage.passage_id for passage in package.counter_evidence}


def test_counter_evidence_that_had_nowhere_to_look_is_unavailable_and_not_an_empty_ok(registry):
    """D5: `revenue` has 170 attempted issues and 0 observations — it has no number *because* of
    the refusals, and `Ok([])` there reads as "this filing refused nothing"."""
    retriever = make_retriever(counter_unavailable=("gaap_gross_margin", "2022Q3"))
    package = build_package(registry, retriever)

    warning = next(w for w in package.warnings
                   if w.code == codes.COUNTER_EVIDENCE_UNAVAILABLE)
    assert "no_document_scope" in warning.detail


# -- §6.6 D8's ambiguity codes, §10.1's warnings, and C4 --------------------------------------


def test_a_fact_whose_metric_declares_an_ambiguity_carries_it_even_when_the_row_does_not(
        registry):
    """S3's hand-off: `CanonicalPoint` carries no ambiguity codes, so §10 packages them from the
    observations — and from the ontology, because a fact whose metric declares an ambiguity must
    carry it whether or not the extractor stamped the row. 88 `pct_>120d` rows carry
    `pct_120_days_denominator` and 124 `homes_sold` rows carry `homes_sold_recognition_point`;
    those are the only two metrics stamped at all."""
    records = (
        observation("obs:hs:2022Q3:a", "2022Q3", 8_500.0, Q3_PASSAGE,
                    metric_id="homes_sold", unit="homes", ambiguity_codes=()),
        observation("obs:hs:2022Q2:a", "2022Q2", 3_500.0, Q2_PASSAGE,
                    metric_id="homes_sold", unit="homes", ambiguity_codes=()),
    )
    candidate = make_candidate(
        candidate_id="cand:metric-move:homes-sold:opendoor:2022Q2_2022Q3:aaaaaaaaaaaa",
        metric_ids=("homes_sold",),
        anchor_observation_ids=("obs:hs:2022Q2:a", "obs:hs:2022Q3:a"),
        evidence_request=EvidenceRequest(
            metric_ids=("homes_sold",), period_keys=("2022Q2", "2022Q3"),
            observation_ids=("obs:hs:2022Q2:a", "obs:hs:2022Q3:a")))
    retriever = make_retriever(evidence={
        "obs:hs:2022Q3:a": evidence_row("obs:hs:2022Q3:a", Q3_PASSAGE, "8,500"),
        "obs:hs:2022Q2:a": evidence_row("obs:hs:2022Q2:a", Q2_PASSAGE, "3,500"),
    })
    package = build_package(registry, retriever, candidate=candidate,
                            request=candidate.evidence_request, records=records)

    assert all("homes_sold_recognition_point" in fact.ambiguity_codes
               for fact in package.facts)
    ambiguity = next(w for w in package.warnings
                     if w.code == codes.METRIC_AMBIGUITY_DECLARED)
    assert "homes_sold_recognition_point" in ambiguity.detail
    assert "title transfer" in ambiguity.detail


def test_metric_rows_come_from_the_ontology_and_not_from_the_metric_node(registry):
    """C4: the `:Metric` node carries 22 properties and none of them is `percentage_min`,
    `percentage_max`, `distinct_from` or `reconciles_to`. A packager that read them off the
    graph would get `None` — Neo4j stores no null and the property was never written — and
    `None` reads as *"no bound"*, which makes §13.3's percentage check pass on everything."""
    package = build_package(registry)

    metric = next(m for m in package.metrics if m.metric_id == "gaap_gross_margin")
    definition = registry.find("gaap_gross_margin")
    assert metric.percentage_min == definition.percentage_min
    assert metric.percentage_max == definition.percentage_max
    assert metric.aliases == tuple(definition.aliases)
    assert metric.unit == definition.unit


def test_an_unpreferred_source_lane_on_a_used_fact_is_disclosed(registry):
    records = tuple(
        observation(record.observation_id, record.period.key, record.value,
                    record.passage_id or Q3_PASSAGE, validation_state="warned")
        for record in make_records())
    package = build_package(registry, records=records)

    assert codes.UNPREFERRED_SOURCE_LANE in {w.code for w in package.warnings}


def test_the_empty_sections_v1_cannot_fill_say_why_they_are_empty(registry):
    """An empty section a reader takes as a fact about Opendoor is the failure. `relationships[]`
    is empty because S1 shipped no §9 tool that returns one; `evidence_sources[]` is empty
    because zero `:EvidenceSource` nodes exist (§13.7.2)."""
    package = build_package(registry)

    present = {w.code for w in package.warnings}
    assert codes.RELATIONSHIPS_UNAVAILABLE_IN_V1 in present
    assert codes.EVIDENCE_SOURCES_ABSENT_IN_V1 in present
    assert codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH in present
    assert package.relationships == ()
    assert package.evidence_sources == ()


def test_an_event_enters_the_package_only_when_the_request_names_it(registry):
    """§6.7 closes event-metric proximity structurally: D5 is the detector that may relate an
    event to a move and it is not implemented in V1. A packager that pulled every event near the
    candidate's quarter would reopen the channel from the other end — for 2022Q4 that is a
    credit facility and a workforce reduction beside eight metric moves."""
    retriever = make_retriever()
    package = build_package(registry, retriever)

    assert package.events == ()
    assert "get_events_in_window" not in PACKAGING_TOOLS
    assert not any(tool == "get_events_in_window" for tool, _parameters in retriever.calls)


def test_the_candidates_own_comparison_is_the_only_one_the_package_records(registry):
    """§0c item 4: *"every comparability decision made"* is O(n²) over a 26-quarter series, and
    it is how an unbounded section got into a package with a token budget."""
    package = build_package(registry)

    assert len(package.compatibility) == 1
    decision = package.compatibility[0]
    assert decision.left_id == "gaap_gross_margin@2022Q3"
    assert decision.right_id == "gaap_gross_margin@2022Q2"
    assert decision.rule_id == "§6.9/movement"


# -- §10.3 — identity and reproducibility -----------------------------------------------------


def test_rebuilding_from_one_graph_run_reproduces_the_package_id_and_the_content_digest(
        registry):
    """§10.3: *"Rebuilding a package from the same graph run must produce the same digest, and a
    test asserts it."* This is that test."""
    first = build_package(registry)
    second = build_package(registry)

    assert first.package_id == second.package_id
    assert first.package_content_digest == second.package_content_digest
    assert len(first.package_content_digest) == 64


def test_the_retrieval_trace_carries_no_wall_clock_because_a_clock_cannot_reproduce(registry):
    """§10's `retrieval_trace[].elapsed_ms` and §10.3's reproducibility requirement contradict
    each other. The timing stays on the retriever, which §14's manifest can carry; the package
    keeps the tool, the parameters, the row count, the `truncated` flag and the outcome, which
    are all functions of the graph run."""
    package = build_package(registry)

    assert package.retrieval_trace
    assert all(entry.elapsed_ms == TRACE_ELAPSED_MS_NOT_CARRIED
               for entry in package.retrieval_trace)
    assert any(entry.tool == "get_fact_evidence" for entry in package.retrieval_trace)


def test_the_content_digest_is_over_the_package_minus_the_field_that_holds_it(registry):
    """S0's F6: a digest over a structure containing itself has no fixed point."""
    from story.core.keys import package_content_digest

    package = build_package(registry)

    assert package.package_content_digest == package_content_digest(
        package.digestible_payload())
    assert "package_content_digest" not in package.digestible_payload()


def test_changing_any_evidence_changes_the_digest(registry):
    package = build_package(registry)
    tampered = package.model_copy(update={
        "facts": (package.facts[0].model_copy(update={"value": 99.9}), *package.facts[1:])})

    from story.core.keys import package_content_digest

    assert package_content_digest(tampered.digestible_payload()) != (
        package.package_content_digest)


def test_two_budgets_produce_two_package_ids_so_a_run_cannot_overwrite_another(registry):
    """§14's measured defect: `--limit 3` and `--limit 20` minted one id, and atomic
    finalisation would have replaced one run's directory with the other's."""
    wide = build_package(registry, budget=BudgetParameters(max_total_tokens=6000))
    narrow = build_package(registry, budget=BudgetParameters(max_total_tokens=5000))

    assert wide.package_id != narrow.package_id


def test_a_request_naming_no_observation_this_builder_holds_refuses(registry):
    candidate = make_candidate(
        anchor_observation_ids=(),
        evidence_request=EvidenceRequest(metric_ids=("adjusted_ebitda",),
                                         period_keys=("2019Q4",)))
    with pytest.raises(PackagingError) as raised:
        build_package(registry, candidate=candidate, request=candidate.evidence_request)

    assert "no observation this builder holds" in str(raised.value)


def test_a_fact_that_resolves_no_passage_is_dropped_and_named_rather_than_shipped(registry):
    """§13.7's Rules A and B both begin with a span that must exist in `Passage.text`. Zero
    `:EvidenceSource` rows exist today, so this branch is the guard that matters the day the
    XBRL lane emits a leaf with no `PART_OF` edge."""
    retriever = make_retriever()
    retriever.evidence = {
        key: row for key, row in retriever.evidence.items() if key != "obs:ggm:2022Q2:c"}
    package = build_package(registry, retriever, budget=BudgetParameters(max_total_tokens=6000))

    assert "obs:ggm:2022Q2:c" not in {fact.observation_id for fact in package.facts}
    warning = next(w for w in package.warnings if w.code == codes.EVIDENCE_CHAIN_INCOMPLETE)
    assert "obs:ggm:2022Q2:c" in warning.subject_ids


def test_every_fact_in_a_package_resolves_to_a_passage_a_document_and_a_source_url(registry):
    package = build_package(registry)
    passages = {passage.passage_id for passage in package.primary_passages}
    documents = {document.document_id for document in package.documents}

    assert package.facts
    for fact in package.facts:
        assert fact.passage_id in passages
        assert fact.document_id in documents
        assert fact.source_url and fact.source_url.startswith("https://")
        assert fact.quoted_text


def test_the_subject_is_inferred_from_the_observations_because_no_tool_reads_entity(registry):
    package = build_package(registry)

    assert package.subject.entity_id == "opendoor"
    assert package.subject.resolved is True
    assert codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH in {w.code for w in package.warnings}


def test_a_package_whose_facts_disagree_about_the_subject_is_not_resolved(registry):
    records = (
        observation("obs:ggm:2022Q3:a", "2022Q3", -12.6, Q3_PASSAGE),
        observation("obs:ggm:2022Q2:a", "2022Q2", 11.6, Q2_PASSAGE,
                    subject_entity_id="opendoor_unnamed_subsidiary"),
    )
    package = build_package(registry, records=records)

    assert package.subject.resolved is False


# ---------------------------------------------------------------------------------------
# Assembly — the half that needs no graph at all
# ---------------------------------------------------------------------------------------


def hand_built_sections() -> assembly.PackageSections:
    """Two facts, two passages, two documents, assembled by hand and by nothing else."""
    sections = assembly.PackageSections()
    sections.subject = PackagedSubject(entity_id="opendoor", entity_text="opendoor",
                                       resolved=True)
    for observation_id, period_key, value, passage_id in (
            ("obs:ggm:2022Q3:a", "2022Q3", -12.6, Q3_PASSAGE),
            ("obs:ggm:2022Q2:a", "2022Q2", 11.6, Q2_PASSAGE)):
        document_id = passage_id.rsplit("#p", 1)[0]
        sections.facts.append(PackagedFact(
            observation_id=observation_id, metric_id="gaap_gross_margin",
            metric_label="GAAP Gross Margin", period_key=period_key, shape="quarter",
            value=value, unit="percent", source_lane="normalized_table",
            validation_state="clean", passage_id=passage_id, document_id=document_id,
            source_url="https://www.sec.gov/x", quoted_text=str(value)))
        sections.primary_passages.append(PackagedPassage(
            passage_id=passage_id, document_id=document_id, text=TABLE_TEXT,
            char_count=len(TABLE_TEXT), char_end=len(TABLE_TEXT)))
        assembly.remember_document(sections, passage_row(passage_id))
    sections.required_slots = {("gaap_gross_margin", "2022Q3")}
    return sections


def test_the_assembler_produces_a_package_with_no_retriever_no_ontology_and_no_graph():
    """The claim the split between `evidence_package.py` and `package_assembly.py` rests on."""
    package = assembly.PackageAssembler(
        identity=IDENTITY, budget=BudgetParameters()).finalize(
            hand_built_sections(), make_candidate())

    assert len(package.facts) == 2
    assert package.package_content_digest
    assert {document.document_id for document in package.documents} == {
        Q3_DOCUMENT, Q2_DOCUMENT}


def test_a_trim_rebuilds_documents_so_none_names_a_filing_the_package_no_longer_cites():
    """§10 calls `documents[]` *derived*. Filtering it instead of rebuilding it would leave a
    filing in the package after the last passage that cited it was dropped."""
    package = assembly.PackageAssembler(
        identity=IDENTITY, budget=BudgetParameters(max_total_tokens=1)).finalize(
            hand_built_sections(), make_candidate())

    cited = {passage.document_id for passage in package.primary_passages}
    assert {document.document_id for document in package.documents} == cited
    assert len(package.primary_passages) == 1
    assert package.primary_passages[0].document_id == Q3_DOCUMENT


def test_the_assembler_refuses_to_assemble_a_package_with_no_subject():
    sections = hand_built_sections()
    sections.subject = None

    with pytest.raises(ValueError) as raised:
        assembly.PackageAssembler(identity=IDENTITY, budget=BudgetParameters()).assemble(
            sections, make_candidate(), token_estimate=0)

    assert "13.11" in str(raised.value)


# ---------------------------------------------------------------------------------------
# Structure — the stage's boundaries, executable
# ---------------------------------------------------------------------------------------


def test_no_packaging_module_reaches_a_model_a_provider_or_another_stage():
    """§2's line: the model chooses words and code chooses facts. §10's builder is entirely on
    the code side, and *"no model call anywhere in this stage"* is a property an import graph can
    hold rather than a promise a docstring makes."""
    import ast
    import pathlib

    package_dir = pathlib.Path(__file__).resolve().parents[2] / "story" / "stages" / "packaging"
    modules = sorted(p for p in package_dir.rglob("*.py") if "__pycache__" not in p.parts)
    assert len(modules) >= 6

    forbidden = ("story.providers", "story.stages.detection", "story.stages.retrieval",
                 "story.stages.ranking", "story.stages.freshness", "story.context", "httpx")
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in imported:
            assert not any(name.startswith(bad) for bad in forbidden), f"{path.name}: {name}"


def test_building_a_package_is_not_a_retrieval_tool_a_model_could_call():
    """§9: *"`build_story_evidence_package` is deliberately not a tool ... a model that can call
    it can widen its own universe."*"""
    from story.stages.retrieval import TOOL_NAMES

    assert not any("package" in tool for tool in TOOL_NAMES)
    assert "build_story_evidence_package" not in TOOL_NAMES


# ---------------------------------------------------------------------------------------
# Live — §6.3's three approved 2022Q3 spikes, built from the loaded graph
# ---------------------------------------------------------------------------------------


SPIKE_IDS = {
    "F1": "cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:1503b5b21731",
    "F2": "cand:metric-move:gaap-gross-margin:opendoor:2022Q2_2022Q3:15b62d34dbaa",
    "F3": ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
           "opendoor:2022Q3:9682f1c1c85a"),
}

#: A fourth candidate, because the three spikes have **no** counter-evidence: measured live,
#: `find_counter_evidence` returns zero rows for `adjusted_ebitda`, `gaap_gross_margin` and
#: `adjusted_gross_margin` at 2022Q3, and 9 rows for `contribution_margin` at the same quarter.
#: A `match_basis` rule tested only against the spikes would be a rule tested against nothing.
COUNTER_EVIDENCE_ID = "cand:metric-move:contribution-margin:opendoor:2022Q2_2022Q3:8f04ed6b9ca5"


@pytest.fixture(scope="module")
def live_packages():  # type: ignore[no-untyped-def]
    """Every spike package, built once from one load of the live graph.

    `verify_connectivity` first, matching every other live fixture in this suite: F12 measured
    that the handshake fails in 0.0 s against a refused port while `execute_query` retries a
    managed transaction for 35 seconds.
    """
    from story.context import build_story_context
    from story.core.graph_identity import read_graph_identity
    from story.stages.detection import (
        detect_cross_metric_divergence, detect_metric_moves, load_observations)
    from story.core.series import build_series
    from story.stages.retrieval.graph_tools import BoundedGraphRetriever

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        identity = read_graph_identity(context.graph_runs_root, GRAPH_RUN_ID)
        retriever = BoundedGraphRetriever(context.executor)
        load = load_observations(retriever)
        points = canonicalize(load.records)
        candidates = {
            candidate.candidate_id: candidate
            for candidate in (
                *detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID,
                                     unreadable=load.unreadable).candidates,
                *detect_cross_metric_divergence(
                    build_series(points), graph_run_id=GRAPH_RUN_ID).candidates,
            )
        }
        builder = BoundedEvidencePackageBuilder(
            retriever, identity=identity, records=load.records, points=points,
            unreadable=load.unreadable)
        wanted = {**SPIKE_IDS, "counter": COUNTER_EVIDENCE_ID}
        packages = {
            name: builder.build(candidates[cid], candidates[cid].evidence_request)
            for name, cid in wanted.items()
        }
        yield {"packages": packages, "candidates": candidates, "builder": builder,
               "identity": identity, "load": load, "points": points, "retriever": retriever}
    finally:
        context.close()


@pytest.mark.neo4j
def test_live_every_section_of_every_spike_package_is_inside_its_bound(live_packages):  # type: ignore[no-untyped-def]
    for name, package in live_packages["packages"].items():
        budget = package.budget.parameters
        assert 0 < len(package.facts) <= budget.max_facts, name
        assert len(package.events) <= budget.max_events, name
        assert len(package.relationships) <= budget.max_relationships, name
        assert 0 < len(package.primary_passages) <= budget.max_primary_passages, name
        assert len(package.context_passages) <= (
            budget.max_context_neighbours * 2 * len(package.primary_passages)), name
        assert len(package.explanatory_passages) <= budget.max_explanatory_passages, name
        assert len(package.counter_evidence) <= budget.max_counter_evidence, name
        assert len(package.documents) <= budget.max_documents, name
        assert len(package.metrics) <= budget.max_metrics, name
        assert len(package.formula_windows) <= budget.max_formula_windows, name
        assert len(package.warnings) <= budget.max_warnings, name
        assert len(package.conflicts) <= budget.max_conflicts, name
        assert len(package.compatibility) <= budget.max_compatibility, name
        assert len(package.retrieval_trace) <= budget.max_retrieval_trace, name


@pytest.mark.neo4j
def test_live_the_three_spike_packages_are_inside_the_five_thousand_token_budget(live_packages):  # type: ignore[no-untyped-def]
    """Measured 2026-08-04 against `graph-v1-0483dc6b4b10`: F1 = 4,261, F2 = 4,136,
    F3 = 4,473 tokens, all under §10.2's 5,000. Every one of the three is trimmed to get there
    and every one reports `token_budget` in `caps_hit` — the fifteen sections beside the
    passages cost about 1,700 tokens, which §10.2.1's own arithmetic never counted.
    """
    packages = live_packages["packages"]
    measured = {name: packages[name].budget.token_estimate for name in SPIKE_IDS}

    assert all(estimate <= 5000 for estimate in measured.values()), measured
    assert all(estimate > 3000 for estimate in measured.values()), measured
    assert packages["counter"].budget.token_estimate <= MAX_TOTAL_TOKENS_CEILING


@pytest.mark.neo4j
def test_live_every_facts_citation_chain_resolves_to_a_passage_a_document_and_a_url(live_packages):  # type: ignore[no-untyped-def]
    """§9: *"a fact without a citation handle is not a return value."* §13.7 needs the whole
    chain — the quote, the passage it occurs in, the document that passage is part of, and the
    URL a reader can open."""
    for name, package in live_packages["packages"].items():
        passages = {passage.passage_id: passage for passage in package.primary_passages}
        documents = {document.document_id for document in package.documents}
        for fact in package.facts:
            assert fact.passage_id in passages, (name, fact.observation_id)
            assert fact.document_id in documents, (name, fact.observation_id)
            assert fact.source_url and fact.source_url.startswith("https://www.sec.gov/")
            assert fact.quoted_text
            assert fact.quoted_text in passages[fact.passage_id].text, fact.observation_id


@pytest.mark.neo4j
def test_live_rebuilding_a_spike_package_reproduces_its_digest(live_packages):  # type: ignore[no-untyped-def]
    """§10.3, against the real graph rather than a script."""
    builder = live_packages["builder"]
    candidates = live_packages["candidates"]

    for name, candidate_id in SPIKE_IDS.items():
        candidate = candidates[candidate_id]
        rebuilt = builder.build(candidate, candidate.evidence_request)
        original = live_packages["packages"][name]
        assert rebuilt.package_id == original.package_id, name
        assert rebuilt.package_content_digest == original.package_content_digest, name


@pytest.mark.neo4j
def test_live_counter_evidence_carries_a_match_basis_on_every_row(live_packages):  # type: ignore[no-untyped-def]
    """Measured live 2026-08-04: passage-grain joins return **zero** rows for `adjusted_ebitda`,
    `gaap_gross_margin` and `adjusted_gross_margin` at 2022Q3 — the refusals sit in neighbouring
    tables of the same filing — and `contribution_margin` 2022Q3 returns 9 at document grain."""
    package = live_packages["packages"]["counter"]

    assert package.counter_evidence
    basis = match_basis_of(package)
    assert set(basis) == {row.passage_id for row in package.counter_evidence}
    assert set(basis.values()) <= {counter.MATCH_BASIS_SAME_DOCUMENT,
                                   counter.MATCH_BASIS_SAME_PASSAGE}
    for passage_id, value in basis.items():
        disclosure = next(w for w in package.warnings
                          if passage_id in w.subject_ids and w.code in counter.CODE_BASIS)
        assert f"match_basis={value}" in disclosure.detail

    for name in SPIKE_IDS:
        assert live_packages["packages"][name].counter_evidence == ()


@pytest.mark.neo4j
def test_live_the_three_spikes_carry_the_numbers_section_6_3_states(live_packages):  # type: ignore[no-untyped-def]
    """A package whose bounds all pass and whose facts are the wrong ones is a package that
    passes S5 and fails the whole point of it."""
    packages = live_packages["packages"]

    values = {(fact.metric_id, fact.period_key): fact.value
              for fact in packages["F1"].facts}
    assert values[("adjusted_ebitda", "2022Q2")] == 218_000_000.0
    assert values[("adjusted_ebitda", "2022Q3")] == -211_000_000.0

    values = {(fact.metric_id, fact.period_key): fact.value
              for fact in packages["F2"].facts}
    assert values[("gaap_gross_margin", "2022Q2")] == 11.6
    assert values[("gaap_gross_margin", "2022Q3")] == -12.6

    values = {(fact.metric_id, fact.period_key): fact.value
              for fact in packages["F3"].facts}
    assert values[("adjusted_gross_margin", "2022Q3")] == 3.3
    assert values[("gaap_gross_margin", "2022Q3")] == -12.6


@pytest.mark.neo4j
def test_live_an_explanatory_search_is_bounded_excerpted_and_traced(live_packages):  # type: ignore[no-untyped-def]
    """`want_explanatory_search` is off on all four detectors, so the only way this section is
    ever exercised against the real fulltext index is by asking for it here."""
    candidate = live_packages["candidates"][SPIKE_IDS["F2"]]
    builder = BoundedEvidencePackageBuilder(
        live_packages["retriever"], identity=live_packages["identity"],
        records=live_packages["load"].records, points=live_packages["points"],
        budget=BudgetParameters(max_total_tokens=6000, max_facts=2, max_primary_passages=1))
    request = candidate.evidence_request.model_copy(update={"want_explanatory_search": True})

    package = builder.build(candidate, request)

    assert package.explanatory_passages
    searches = [entry for entry in package.retrieval_trace if entry.tool == "search_passages"]
    assert len(searches) == 1
    assert searches[0].parameters["terms"] == ["Gross Margin", "2022Q2", "2022Q3"]
    for passage in package.explanatory_passages:
        assert passage.excerpted is True
        assert passage.char_end - passage.char_start <= 800
        assert passage.query_terms == ("Gross Margin", "2022Q2", "2022Q3")


@pytest.mark.neo4j
def test_live_the_filter_starvation_the_ruling_is_about_is_still_real(live_packages):  # type: ignore[no-untyped-def]
    """R2b's measurement, re-taken, because the packaging ruling rests on it: `{limit: 500}` is
    applied before the document filter, so a degenerate term set plus a filter returns fewer
    rows than the corpus holds. If this ever stops being true the ruling should be revisited
    rather than carried."""
    retriever = live_packages["retriever"]
    unfiltered = retriever.call("search_passages", {"terms": ["the"]})
    filtered = retriever.call(
        "search_passages", {"terms": ["the"], "document_types": ["shareholder_letter"]})
    alias = retriever.call("search_passages", {"terms": ["Adjusted EBITDA"]})

    pool_of = lambda result: max(int(r["candidate_pool_size"]) for r in result.rows)
    assert pool_of(unfiltered) == 500
    assert pool_of(filtered) == 500
    assert len(filtered.rows) < len(unfiltered.rows)
    assert pool_of(alias) < 500
