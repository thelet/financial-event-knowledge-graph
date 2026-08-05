"""S5 — which evidence enters a `StoryEvidencePackage`. The wall the model cannot see past.

Responsibility: **selection**, and it is the only module in this stage that reads the graph.
Given one `StoryCandidate` and its `EvidenceRequest` it chooses the facts, the passages, the
counter-evidence, the metrics, the formula windows, the conflicts, the comparability decisions
and the warnings — and hands them to `package_assembly.PackageAssembler`, which owns §10.2's
caps, the trim, `documents[]`, `package_id` and `package_content_digest` and reads nothing.

**Nothing here calls a model, and `build_story_evidence_package` is deliberately not a §9 tool**
(§9, in as many words: *"a model that can call it can widen its own universe"*). The
`EvidenceBuilder` protocol takes a candidate and a request and returns a package; there is no
argument through which a string a model produced can reach a selection.

**Four rulings this module carries, each with the measurement behind it.**

1. **A truncated read is never treated as complete.** `get_metric_history` is bounded at 200 and
   five metrics exceed it; AR2's b1 measured what one un-paged call costs — *93 candidates and
   zero refusals, losing 20 real candidates and giving 3 slots a wrong `n_docs`*. So this module
   **never calls `get_metric_history`**: observations arrive from the caller, which paged them
   and proved completeness (`canonicalization._paged_history` refuses rather than returning
   short). `PACKAGING_TOOLS` is that rule with a raise behind it, and the caller's own
   `unreadable` list becomes a `REFUSE`-severity package warning. Every other tool that answers
   `truncated=True` produces a warning naming it.
2. **A capped filtered search pool is disclosed, and starves the section only when it must.**
   `search_passages` applies `{limit: 500}` *before* the document filter, so `["the"]` +
   `shareholder_letter` returns 9 rows where the unbounded pool returns 26. Retrieval discloses
   `candidate_pool_size`; packaging decides (IMPLEMENTATION_STEPS §7's ruling). The decision
   here is narrow: a capped pool is always a warning carrying the pool limit, the rows after
   filters, the term source and the filter basis — and the explanatory section is dropped only
   when the pool capped **and** a filter was applied, which is the only combination in which
   completeness cannot be established. An uncapped pool with a filter, or a capped pool with no
   filter, keeps its rows and its warning.
3. **Query terms come from the candidate, never from a model.** `query_terms.derive_terms` is
   the only producer and `refuse_undeclared_terms` is checked against the argument actually
   passed.
4. **Counter-evidence joins at document grain and says so on every row.**
   `counter_evidence.match_basis_of` is the reader.

**What §10 asks for and this run cannot give**, recorded here rather than left as an empty
section a reader would read as a fact about Opendoor:

    relationships[]      no §9 tool implemented at S1 returns a relationship — `find_related_
                         entities` is in §9's table and is not among the nine that shipped
    evidence_sources[]   zero `:EvidenceSource` nodes exist (§13.7.2); the section exists so
                         the XBRL lane cannot arrive by widening `PackagedFact`
    events[].properties  §13.8: the projection exposes `property_names` and not the values,
                         deliberately — *"strings that look like facts and are not"*
    documents[].title    `:Document` carries `title` and no §9 tool returns it
    documents[].content_sha256   `:Document` carries no such property at all
    facts[].printed_form / scale_location   no `:Observation` carries either (defect P2)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ontology.contracts import ConceptRegistry
from ontology.core.models import MetricDefinition, MetricFormulaVersion

from story.contracts import GraphRetriever
from story.core.graph_identity import GraphIdentity
from story.core.models import (
    BudgetParameters,
    CompatibilityDecision,
    Conflict,
    ConflictCluster,
    EvidenceRequest,
    EvidenceRole,
    MetricAmbiguity,
    PackagedEvent,
    PackagedFact,
    PackagedFormulaWindow,
    PackagedMetric,
    PackagedPassage,
    PackagedSubject,
    PassageQuality,
    RetrievalOutcome,
    RetrievalResult,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.core.observation_equivalence import same_reading
from story.core.series import (
    CanonicalPoint,
    CanonicalStatus,
    ClaimKind,
    ComparabilityAuthority,
    ObservationRecord,
    Refuse,
    build_series,
    comparable,
)
import story.stages.packaging.counter_evidence as counter
import story.stages.packaging.package_assembly as assembly
import story.stages.packaging.passage_excerpts as passage_excerpts
import story.stages.packaging.passage_quality as passage_quality
import story.stages.packaging.query_terms as query_terms
import story.stages.packaging.section_bounds as section_bounds
import story.stages.packaging.warning_codes as codes

#: The only §9 tools this stage may call. `get_metric_history` is **absent on purpose** — see
#: ruling 1 in the module docstring — and so are `list_metrics`, `get_metric_definition` and
#: `compare_metric_periods`, whose answers are the ontology's or the caller's and would be a
#: second authority beside C4's.
PACKAGING_TOOLS = frozenset({
    "get_fact_evidence",
    "get_passage_context",
    "search_passages",
    "find_counter_evidence",
})

#: How a slot is rendered where §10 asks for a string — `Conflict.slot` and both ends of a
#: `CompatibilityDecision`. `@` and not `:` because every id in this package already uses `:` as
#: its own separator and a slot that read like an id would be one a consumer tried to resolve.
SLOT_SEPARATOR = "@"

#: The order a slot's readings are drawn in when §10.2's `facts[]` cap bites. §6.1 step 5 chose
#: the representative and §10 has no licence to prefer another; the minority reading is last
#: because §10.1 requires it disclosed, not preferred.
_ROLE_REPRESENTATIVE, _ROLE_SUPPORTING, _ROLE_MINORITY, _ROLE_OTHER = 0, 1, 2, 3


class PackagingError(RuntimeError):
    """A packaging call that cannot be answered with a package.

    Deliberately rare. Almost everything this module discovers is a *warning* on the package —
    an incomplete read, a capped pool, a truncated section — because §11's planner has to be
    able to branch on it and a traceback is not a branch. What raises is only what makes the
    package meaningless: no fact, or a caller reaching for a tool this stage may not call.
    """


@dataclass(frozen=True, slots=True)
class _FactPlan:
    """One observation selected for `facts[]`, with everything the drop rule reads."""

    observation_id: str
    record: ObservationRecord
    is_anchor: bool
    metric_rank: int
    role_rank: int

    @property
    def slot(self) -> tuple[str, str]:
        return (self.record.metric_id, self.record.period.key)


class BoundedEvidencePackageBuilder:
    """`story.contracts.EvidenceBuilder`, within every bound §10.2 states.

    Structural conformance and no inheritance, matching `BoundedGraphRetriever`: the protocol
    exists so S7 can be driven by a stub, and a base class would make that a lie.

    **What is injected and why each one is.** The retriever is the only surface that reaches a
    database (D1). `identity` is the graph run's own manifest block, so §13.13's run-consistency
    fields are copied from the projection rather than asserted here. `records` and `points` are
    the caller's *proved-complete* observation load and canonical layer — ruling 1 — and this
    class refuses to fetch them itself. `registry` and `authority` are the ontology, which C4
    makes authoritative for every metric field; a builder that read them off the `:Metric` node
    would fill `percentage_min` with `None` and make §13.3's bound check pass on everything.
    """

    def __init__(
        self,
        retriever: GraphRetriever,
        *,
        identity: GraphIdentity,
        records: Sequence[ObservationRecord],
        points: Sequence[CanonicalPoint] = (),
        registry: ConceptRegistry | None = None,
        authority: ComparabilityAuthority | None = None,
        budget: BudgetParameters = BudgetParameters(),
        unreadable: Sequence[tuple[str, str]] = (),
        subject_text: str | None = None,
        subject_labels: Sequence[str] = (),
        subject_resolved: bool | None = None,
    ) -> None:
        section_bounds.check_budget(budget)
        self._retriever = retriever
        self._identity = identity
        self._records = tuple(records)
        self._points = tuple(points)
        self._registry = registry
        self._authority = authority
        self._budget = budget
        self._unreadable = tuple(unreadable)
        self._subject_text = subject_text
        self._subject_labels = tuple(subject_labels)
        self._subject_resolved = subject_resolved

        self._by_observation = {record.observation_id: record for record in self._records}
        self._by_slot: dict[tuple[str, str], list[ObservationRecord]] = {}
        for record in self._records:
            self._by_slot.setdefault(record.slot_key, []).append(record)
        self._point_of = {point.slot_key: point for point in self._points}
        self._series = build_series(self._points) if self._points else {}

    # -- the protocol ---------------------------------------------------------------------

    def build(
        self, candidate: StoryCandidate, request: EvidenceRequest
    ) -> StoryEvidencePackage:
        """One candidate's whole universe, bounded, warned, trimmed, identified and digested.

        The order is not arbitrary. Facts are chosen first because every other section is
        derived from them — the primary passages are the ones facts cite, the documents are the
        ones those passages belong to, the metrics are the ones the facts reference, and the
        counter-evidence is scoped to the documents the facts were read from. A packager that
        chose passages first would be choosing evidence for facts it had not selected yet.
        """
        trace_from = len(self._retriever.trace())
        sections = assembly.PackageSections()

        plans = self._select_facts(candidate, request)
        if not plans:
            raise PackagingError(
                f"{candidate.candidate_id}: the evidence request names no observation this "
                f"builder holds — {len(self._records)} records loaded, "
                f"metric_ids={list(request.metric_ids)}, period_keys={list(request.period_keys)}. "
                "A package with no fact cites nothing and §11's planner has nothing to plan over")

        sections.required_slots = {
            self._by_observation[observation_id].slot_key
            for observation_id in
            set(candidate.anchor_observation_ids) | set(request.observation_ids)
            if observation_id in self._by_observation
        }
        self._add_facts(sections, plans)
        if not sections.facts:
            raise PackagingError(
                f"{candidate.candidate_id}: every selected observation failed to resolve a "
                "citation chain (§13.7); a package of uncitable facts is not a package")
        self._add_primary_passages(sections)
        self._add_context_passages(sections)
        if request.want_explanatory_search:
            self._add_explanatory_passages(sections, candidate, request)
        if request.want_counter_evidence:
            self._add_counter_evidence(sections, candidate, request)
        self._add_events(sections, request)
        self._add_metrics(sections, candidate, request)
        self._add_formula_windows(sections, candidate, request)
        self._add_conflicts(sections, plans)
        self._add_compatibility(sections, candidate, request)
        self._add_run_level_warnings(sections, candidate)
        sections.retrieval_trace = list(self._retriever.trace()[trace_from:])
        sections.subject = self._subject(candidate, sections.facts)

        # Selection ends here. Everything from the caps to the digest belongs to
        # `package_assembly.PackageAssembler`, which reads no graph and selects nothing — and
        # that split is what makes "the digest reproduces" testable without a database.
        return assembly.PackageAssembler(
            identity=self._identity, budget=self._budget).finalize(sections, candidate)

    def _subject(
        self, candidate: StoryCandidate, facts: Sequence[PackagedFact]
    ) -> PackagedSubject:
        """§10's `subject`, inferred from the observations because no §9 tool reads `:Entity`.

        `cypher.py` excludes the label deliberately — `opendoor` carries 2,704
        `OBSERVATION_OF_SUBJECT` edges and one hop outward from the entity is the whole graph —
        so there is no read that could return `entity_text`, `labels` or the node's own
        `resolved`. What *is* available is every packaged fact's own `subject_entity_id`, which
        is the projection's resolved key: `resolved` is `True` when they all agree with the
        candidate's and `False` otherwise, which §13.11 turns into a refusal. The caller may
        override; the disclosure warning is emitted either way.
        """
        subjects = {
            self._by_observation[fact.observation_id].subject_entity_id
            for fact in facts if fact.observation_id in self._by_observation
        }
        inferred = bool(subjects) and subjects == {candidate.subject_entity_id}
        return PackagedSubject(
            entity_id=candidate.subject_entity_id,
            entity_text=self._subject_text or candidate.subject_entity_id,
            resolved=self._subject_resolved if self._subject_resolved is not None else inferred,
            labels=self._subject_labels,
        )

    # -- facts ----------------------------------------------------------------------------

    def _select_facts(
        self, candidate: StoryCandidate, request: EvidenceRequest
    ) -> tuple[_FactPlan, ...]:
        """§10.2's `facts[]`, slot-major round-robin, capped, and stated rather than incidental.

        The sequence is built in two steps and both are total:

        1. Every observation of every `(metric, period)` slot the request names, plus every
           `observation_id` it names outright, is ranked by
           `section_bounds.fact_sort_key` — *anchor first, request metric order, earliest period,
           representative before supporting before minority, then observation id*.
        2. That ranking is dealt **round-robin across slots**, in the order the slots first
           appear in it. The cap then takes a prefix.

        Step 2 is the part that matters. A straight prefix of step 1 would give a
        twelve-observation 2022Q2 slot the whole budget and ship a `metric_move` candidate whose
        2022Q3 side — the half the story is about — carried no fact at all. Round-robin
        guarantees every requested slot is represented before any slot is represented twice, so
        the cap can starve a *reading* and never a *slot*.
        """
        metric_rank = {metric_id: index for index, metric_id in enumerate(request.metric_ids)}
        anchors = set(candidate.anchor_observation_ids) | set(request.observation_ids)
        wanted: dict[str, _FactPlan] = {}

        for record in self._candidate_records(request, anchors):
            metric_id = record.metric_id
            wanted[record.observation_id] = _FactPlan(
                observation_id=record.observation_id,
                record=record,
                is_anchor=record.observation_id in anchors,
                metric_rank=metric_rank.get(metric_id, len(metric_rank) + 1),
                role_rank=self._role_rank(record),
            )

        ranked = sorted(
            wanted.values(),
            key=lambda plan: section_bounds.fact_sort_key(
                plan.observation_id,
                is_anchor=plan.is_anchor,
                metric_rank=plan.metric_rank,
                anchor_date=plan.record.period.anchor_date or "",
                period_key=plan.record.period.key,
                role_rank=plan.role_rank,
            ),
        )

        by_slot: dict[tuple[str, str], list[_FactPlan]] = {}
        for plan in ranked:
            by_slot.setdefault(plan.slot, []).append(plan)

        dealt: list[_FactPlan] = []
        depth = 0
        while any(len(group) > depth for group in by_slot.values()):
            for group in by_slot.values():
                if len(group) > depth:
                    dealt.append(group[depth])
            depth += 1
        return tuple(dealt)

    def _candidate_records(
        self, request: EvidenceRequest, anchors: Iterable[str]
    ) -> tuple[ObservationRecord, ...]:
        """Every observation the request reaches, from the slots and from the named ids."""
        found: dict[str, ObservationRecord] = {}
        periods = set(request.period_keys)
        for metric_id in request.metric_ids:
            for (slot_metric, slot_period), group in self._by_slot.items():
                if slot_metric != metric_id:
                    continue
                if periods and slot_period not in periods:
                    continue
                for record in group:
                    found[record.observation_id] = record
        for observation_id in anchors:
            record = self._by_observation.get(observation_id)
            if record is not None:
                found[observation_id] = record
        return tuple(found.values())

    def _role_rank(self, record: ObservationRecord) -> int:
        """Where §6.1 put this reading in its own slot. Unknown slots rank last, not first."""
        point = self._point_of.get(record.slot_key)
        if point is None:
            return _ROLE_OTHER
        if record.observation_id == point.representative_observation_id:
            return _ROLE_REPRESENTATIVE
        if record.observation_id in point.supporting_observation_ids:
            return _ROLE_SUPPORTING
        if record.observation_id in point.minority_observation_ids:
            return _ROLE_MINORITY
        return _ROLE_OTHER

    def _add_facts(
        self, sections: assembly.PackageSections, plans: Sequence[_FactPlan]
    ) -> None:
        """Resolve every selected observation's citation chain, then cap.

        A fact whose chain does not close is **dropped and named**, not shipped: §13.7's whole
        subject is what a citation supports, and a `PackagedFact` with no passage and no
        `:EvidenceSource` cannot even be constructed (`_has_some_evidence` on the model). Zero
        such rows exist in this run — all 2,714 evidence edges land on a `:Passage` — so the
        branch is a guard, and it is the guard that will matter the day the XBRL lane emits a
        leaf with no `PART_OF` edge.
        """
        cap = section_bounds.cap_for(self._budget, "facts")
        kept: list[PackagedFact] = []
        unciteable: list[str] = []

        for plan in plans[:cap]:
            evidence = self._call("get_fact_evidence", {"observation_id": plan.observation_id})
            if evidence.outcome is not RetrievalOutcome.OK or not evidence.rows:
                unciteable.append(plan.observation_id)
                continue
            if evidence.truncated:
                sections.warnings.append(codes.packaged_warning(
                    codes.RETRIEVAL_TRUNCATED,
                    subject_ids=(plan.observation_id,),
                    detail="get_fact_evidence returned its 10-row bound for this observation, "
                           "so it is evidenced by more passages than the package carries; the "
                           "first by (passage_id, edge_key) is the one cited"))
            row = evidence.rows[0]
            assembly.remember_document(sections, row)
            kept.append(self._packaged_fact(plan, row))

        if unciteable:
            sections.warnings.append(codes.packaged_warning(
                codes.EVIDENCE_CHAIN_INCOMPLETE,
                subject_ids=unciteable,
                detail=f"{len(unciteable)} selected observation(s) resolved no `EVIDENCED_BY` "
                       "passage and were dropped; §13.7's Rules A and B both begin with a span "
                       "that must exist in `Passage.text`"))
        if len(plans) > cap:
            sections.caps_hit.add("facts")
            sections.warnings.append(codes.packaged_warning(
                codes.SECTION_TRUNCATED,
                subject_ids=("facts",),
                detail=f"facts: {len(plans)} selected, {cap} carried. Dealt round-robin across "
                       f"{len({plan.slot for plan in plans})} slots so every slot is represented "
                       "before any slot is represented twice; the drop took the tail"))
        sections.facts = kept
        # The concordant sources of every carried fact, in full. Which of them are *discarded*
        # — and so which belong on the fact's corroboration lists — depends on what survives the
        # trim, so the subtraction is `package_assembly.derive_corroboration`'s and is redone on
        # every rebuild, exactly as `documents[]` is.
        for fact in kept:
            sections.corroboration[fact.observation_id] = self._corroboration(
                self._by_observation[fact.observation_id])

    def _packaged_fact(self, plan: _FactPlan, evidence: Mapping[str, Any]) -> PackagedFact:
        """One observation, merged from the caller's record and its `EVIDENCED_BY` edge.

        **Two sources, because neither tool carries the whole fact (C3).** `get_metric_history`
        — which the caller ran, paged — carries the period fields, the lane, the validation state
        and the `ambiguity_codes` §6.6 D8 needs; `quoted_text`, `row_label`, `column_label` and
        `source_url` hang off the edge and the passage, and only `get_fact_evidence` returns
        them. A package built from one of the two would look complete and cite nothing, or cite
        everything and be unable to say which period it was about.

        `ambiguity_codes` is the union of what the observation carries and what the *ontology*
        declares for its metric — S3's hand-off, verbatim: *"`CanonicalPoint` carries no
        `ambiguity_codes`, so §6.6 D8's requirement is not satisfiable from the canonical layer;
        §10 packages them from the observations instead."* The union rather than the
        observation's own set, because a fact whose metric declares an ambiguity must carry it
        whether or not the extractor stamped the row: 88 `pct_homes_on_market_gt_120_days` rows
        carry `pct_120_days_denominator` and 124 `homes_sold` rows carry
        `homes_sold_recognition_point` *(counted live)*, and those are the only two metrics in
        the ontology whose observations were stamped at all.
        """
        # The three `corroborating_*` lists are left empty here and filled by `_add_facts` once
        # the cap has run: corroboration names what the package discarded, and until `facts[]`
        # is final nothing knows what that is.
        record = plan.record
        return PackagedFact(
            observation_id=record.observation_id,
            metric_id=record.metric_id,
            metric_label=self._metric_label(record.metric_id),
            period_key=record.period.key,
            period_start=record.period.period_start,
            period_end=record.period.period_end,
            instant_date=record.period.instant_date,
            # The story vocabulary (`quarter`, `instant`, `fiscal_year`, …) and not the
            # retrieval layer's `duration`/`instant`: R3's classifier is what every comparability
            # rule reads, and a package that named a different vocabulary would make §13.4's
            # period checks compare two spellings of one idea.
            shape=record.period.shape.value,
            value=record.value,
            unit=record.unit,
            currency=record.currency,
            scale=record.scale,
            # No `:Observation` carries `scale_location` or `printed_form` (defect P2: the same
            # `adjusted_ebitda` quarter is quoted `(27,075)` and `(27)`), so neither is invented.
            scale_location=None,
            printed_form=None,
            row_label=assembly.text_or_none(evidence, "row_label"),
            column_label=assembly.text_or_none(evidence, "column_label"),
            source_lane=record.source_lane,
            validation_state=record.validation_state,
            warning_codes=record.warning_codes,
            ambiguity_codes=self._ambiguity_codes(record),
            passage_id=assembly.text_or_none(evidence, "passage_id"),
            document_id=assembly.text_or_none(evidence, "document_id"),
            source_url=assembly.text_or_none(evidence, "source_url"),
            quoted_text=assembly.text_or_none(evidence, "quoted_text"),
        )

    def _ambiguity_codes(self, record: ObservationRecord) -> tuple[str, ...]:
        declared = tuple(a.code for a in self._ambiguities(record.metric_id))
        return tuple(sorted(set(record.ambiguity_codes) | set(declared)))

    def _corroboration(
        self, record: ObservationRecord
    ) -> tuple[tuple[str, str, str], ...]:
        """§4 S3 — the concordant sources canonicalisation collapsed, recorded rather than lost.

        **The measurement this exists for.** `housing_inventory_homes` 2023-03-31 holds six
        observations, all `6261.0 homes`, in six different documents; 2022-12-31 holds six, all
        `12788.0`, in **five** *(re-measured 2026-08-05; §1.1 says six documents for both slots
        and the second slot's count is five — one filing contributes two rows)*. §6.1 keeps one
        and §10 carried one passage, so the best-corroborated fact in the candidate arrived
        looking thinly sourced.

        **One canonical fact, and the primary source is the one §6.1 step 5 already chose.**
        There is no second source-lane priority here: `_select_facts` ranks by
        `section_bounds.fact_sort_key`, whose `role_rank` puts the slot's *representative* ahead
        of its supporting and minority readings, and the representative is
        `min(scale_precision, filing_date, observation_id)` — the most precisely printed reading
        of the earliest filing. Whatever that ordering carries is `primary_support`; every other
        concordant reading is recorded here by id. Minting a second `facts[]` row instead would
        give §13 two things to verify where the corpus has one.

        Concordance is `observation_equivalence.same_reading`, which is the same single-pair
        tolerance test `canonicalization._cluster` uses, so a source recorded here is a source
        §6.1 put in the same cluster. Quarantined readings are excluded (`_comparable_peers`):
        a flattened-table read is not a second source, it is a defective one.

        **This returns every concordant peer, and the subtraction happens later.** §10.2's
        round-robin can legitimately deal a second concordant reading of one slot into `facts[]`
        once every slot is represented, and that observation was not discarded — it is in the
        package with its own passage, which is strictly more than an id. Listing it here as well
        would have each of two rows claim the other as its own second source and would spend
        §10.2's budget twice on one thing. Which peers survive is only known after the trim, so
        `package_assembly.derive_corroboration` does the subtraction on every rebuild.
        """
        authority = self._resolved_authority()
        return tuple(sorted(
            (peer.observation_id, peer.passage_id or "", peer.document_id or "")
            for peer in self._comparable_peers(record)
            if same_reading(record, peer, authority=authority)))

    def _declared_unit(self, metric_id: str) -> str | None:
        """The ontology's unit for a metric, which is what makes `UNIT_CONTRADICTS_ONTOLOGY`
        decidable without parsing the issue's prose. C4: the unit is the ontology's, never the
        `:Metric` node's."""
        definition = self._resolved_registry().find(metric_id)
        unit = getattr(definition, "unit", None)
        return str(unit) if unit else None

    # -- passages -------------------------------------------------------------------------

    def _add_primary_passages(self, sections: assembly.PackageSections) -> None:
        """The passages the surviving facts are bound to, in fact order, capped.

        Full text, never excerpted (§10.2.1 point 2's exception, §13.7's Rule A). Fetched
        through `get_passage_context` with a zero window, which is the only §9 tool that returns
        a `:Passage.text` in full — `search_passages` returns `left(text, 400)` by design.
        """
        cap = section_bounds.cap_for(self._budget, "primary_passages")
        order: list[str] = []
        for fact in sections.facts:
            if fact.passage_id and fact.passage_id not in order:
                order.append(fact.passage_id)

        kept: list[PackagedPassage] = []
        # Only the passages that can survive the cap are fetched. Reading all twelve a
        # twelve-fact package could cite and then discarding eight is eight bounded reads and
        # eight `retrieval_trace` rows spent on evidence the package will not carry — and the
        # trace is inside the same token budget the passages are competing for.
        for passage_id in order[:cap]:
            row = self._passage_row(passage_id)
            if row is None:
                continue
            assembly.remember_document(sections, row)
            # `PRIMARY_SUPPORT` is true today and not an aspiration: this section is built from
            # the passages the surviving facts are bound to, one entry per `fact.passage_id`.
            kept.append(self._packaged_passage(
                row, excerpt=passage_excerpts.whole(str(row.get("text") or "")),
                role=EvidenceRole.PRIMARY_SUPPORT))
        kept.sort(key=lambda passage: section_bounds.passage_sort_key(
            passage.passage_id, first_citing_fact_rank=order.index(passage.passage_id)))

        # `len(order)` and not `len(kept)`: only the first `cap` passages were fetched, so the
        # kept list can never exceed the cap and comparing against it would report every
        # truncation as no truncation.
        if len(order) > cap:
            sections.caps_hit.add("primary_passages")
            sections.warnings.append(codes.packaged_warning(
                codes.SECTION_TRUNCATED,
                subject_ids=("primary_passages",),
                detail=f"primary_passages: {len(order)} cited, {cap} carried; the passage cited "
                       "by the highest-ranked surviving fact stays and the tail goes, and the "
                       "facts bound to a dropped passage go with it (§13.7 Rule A)"))
        sections.primary_passages = kept[:cap]
        assembly.drop_orphaned_facts(sections)

    def _add_context_passages(self, sections: assembly.PackageSections) -> None:
        """±`max_context_neighbours` around each primary, full text, excluding other primaries.

        A neighbour that is itself a primary is not context — shipping it twice would spend the
        budget twice and give the writer two rows for one passage — and the anchor is excluded
        for the same reason.
        """
        neighbours = section_bounds.cap_for(self._budget, "context_passages")
        if neighbours <= 0:
            return
        primaries = {passage.passage_id for passage in sections.primary_passages}
        seen: set[str] = set(primaries)
        collected: list[tuple[tuple[int, int, int, str], PackagedPassage]] = []

        for rank, primary in enumerate(sections.primary_passages):
            result = self._call("get_passage_context", {
                "passage_id": primary.passage_id,
                "before": neighbours,
                "after": neighbours,
            })
            if result.outcome is not RetrievalOutcome.OK:
                continue
            for row in result.rows:
                passage_id = str(row.get("passage_id") or "")
                if row.get("is_anchor") or passage_id in seen or not passage_id:
                    continue
                seen.add(passage_id)
                assembly.remember_document(sections, row)
                offset = int(row.get("offset_from_anchor") or 0)
                collected.append((
                    section_bounds.context_sort_key(
                        passage_id, primary_rank=rank, offset=offset),
                    self._packaged_passage(
                        row, excerpt=passage_excerpts.whole(str(row.get("text") or "")),
                        role=EvidenceRole.CONTEXT),
                ))

        collected.sort(key=lambda pair: pair[0])
        sections.context_passages = [passage for _key, passage in collected]

    def _add_explanatory_passages(
        self,
        sections: assembly.PackageSections,
        candidate: StoryCandidate,
        request: EvidenceRequest,
    ) -> None:
        """§10's `explanatory_passages[]` — the one section §11 admits is not model-free.

        Ruling 2 lives here. The pool cap is read off `candidate_pool_size` on the rows
        themselves, compared against the retriever's own inner limit, and the decision is:

        * pool capped **and** a filter applied → the section is dropped, `search_pool_starved`,
          because the rows that came back are a consequence of the cap and no completeness claim
          can be made about them;
        * pool capped, no filter → rows kept, `search_pool_capped` recorded with the limit, the
          post-filter row count, the term source and the filter basis;
        * pool not capped → rows kept, and a `retrieval_truncated` warning if the 25-row bound
          bit, because that bound is a different fact from the pool.
        """
        registry = self._resolved_registry()
        derived = query_terms.derive_terms(
            registry, metric_ids=request.metric_ids or candidate.metric_ids,
            period_keys=request.period_keys or candidate.anchor_period_keys)
        if not derived:
            return
        query_terms.refuse_undeclared_terms(derived.terms, derived)

        window = self._search_window(request)
        parameters: dict[str, Any] = {"terms": list(derived.terms), **window}
        result = self._call("search_passages", parameters)
        if result.outcome is not RetrievalOutcome.OK or not result.rows:
            return

        pool = max((int(row.get("candidate_pool_size") or 0) for row in result.rows), default=0)
        limit = self._inner_search_limit()
        filtered = bool(window)
        if pool >= limit and filtered:
            sections.warnings.append(codes.packaged_warning(
                codes.SEARCH_POOL_STARVED,
                subject_ids=tuple(derived.terms),
                detail=_pool_detail(pool, limit, len(result.rows), derived, window)
                       + ". The section was dropped rather than shipped short: rows below a "
                         "capped pool cannot be distinguished from rows the corpus does not have"))
            return
        if pool >= limit:
            sections.warnings.append(codes.packaged_warning(
                codes.SEARCH_POOL_CAPPED,
                subject_ids=tuple(derived.terms),
                detail=_pool_detail(pool, limit, len(result.rows), derived, window)
                       + ". No document filter was applied, so the cap changed the ranking pool "
                         "and not the eligibility of a row"))
        if result.truncated:
            sections.warnings.append(codes.packaged_warning(
                codes.RETRIEVAL_TRUNCATED,
                subject_ids=("search_passages",),
                detail=f"search_passages returned its 25-row bound for {list(derived.terms)}; "
                       "the rows below the bound scored lower and are not in the package"))

        cap = section_bounds.cap_for(self._budget, "explanatory_passages")
        bound_ids = [fact.passage_id for fact in sections.facts if fact.passage_id]
        excluded = set(bound_ids) | {p.passage_id for p in sections.primary_passages}
        ranked = sorted(result.rows, key=lambda row: section_bounds.explanatory_sort_key(
            str(row.get("passage_id") or ""), score=float(row.get("score") or 0.0)))

        kept: list[PackagedPassage] = []
        for row in ranked:
            if len(kept) >= cap:
                break
            passage_id = str(row.get("passage_id") or "")
            if not passage_id or passage_id in excluded:
                # A search hit that a fact is bound to is already in `primary_passages` in full.
                # Skipping it is what keeps the guard below unreachable in production — and the
                # guard stays, because "unreachable today" is how §13.7's Rule A stops holding.
                continue
            passage_excerpts.refuse_excerpting_bound_passage(passage_id, bound_ids)
            full = self._passage_row(passage_id)
            if full is not None:
                assembly.remember_document(sections, full)
            text = str((full or row).get("text") or row.get("text_excerpt") or "")
            excerpt = passage_excerpts.window(
                text, needles=derived.terms, radius=self._budget.excerpt_radius_chars)
            # `CONTEXT` and not a role of its own. An explanatory passage is a fulltext hit
            # that explains; it is bound to no fact, so it supports nothing, and §4 S1's
            # vocabulary has no `explanatory` member because the distinction that matters to a
            # reader is what the passage *does*, not which search found it. The section name
            # still records the provenance, and S6 owns the one canonical mapper.
            kept.append(self._packaged_passage(
                full or row, excerpt=excerpt, role=EvidenceRole.CONTEXT,
                query_terms=derived.terms, score=float(row.get("score") or 0.0)))
        if len(ranked) > len(kept):
            sections.caps_hit.add("explanatory_passages")
        sections.explanatory_passages = kept

    # -- counter-evidence -----------------------------------------------------------------

    def _add_counter_evidence(
        self,
        sections: assembly.PackageSections,
        candidate: StoryCandidate,
        request: EvidenceRequest,
    ) -> None:
        """§10's `counter_evidence[]` — **and only what qualifies as counter-evidence** (§4 S2).

        One `find_counter_evidence` call per `(metric, period)` the candidate anchored on —
        which is the period narrowing, since the rows carry no period of their own — then
        `counter.narrow`, then a ±400 window centred on the refused row label. Everything up to
        here is unchanged.

        **What S2 changed is what the rows are then allowed to be called.** The join is still at
        document grain and every row still says so, but the grain is now the row's *provenance*
        rather than its *claim*: `counter.classify_issue` decides the role, and only a row that
        qualifies on one of the six named bases reaches `counter_evidence[]`. The rest go to
        `diagnostic_passages[]` with their text, their issue codes and the role they actually
        play, so the reclassification is reviewable and nothing is silently discarded.

        Two things happen before any classification:

        1. **`_divergent_reading_rows` runs first.** A second reading of a slot the package
           anchors on is the only counter-evidence here that rests on a *value*, and it is what
           `EvidenceRole.COUNTER_EVIDENCE` is for. Measured on this run it produces nothing —
           all 36 multi-valued slots collapse to one cluster under presentation tolerance (§6.1
           step 2) — which is the correct answer and not a missing feature.
        2. **The quality filter.** §4 S2: *"Near-empty passages never become counter-evidence."*
           A pipe-only table promoted to a counterpoint obliges §11's planner to write about
           nothing.
        """
        cited_documents = sorted({fact.document_id for fact in sections.facts if fact.document_id})
        cited_passages = sorted({fact.passage_id for fact in sections.facts if fact.passage_id})
        fact_by_passage = {fact.passage_id: fact for fact in sections.facts if fact.passage_id}
        corroborating = {passage_id for fact in sections.facts
                         for passage_id in fact.corroborating_passage_ids}
        metrics = tuple(request.metric_ids or candidate.metric_ids)
        periods = tuple(request.period_keys or candidate.anchor_period_keys)

        def classify(row: Mapping[str, Any]) -> counter.Classification:
            passage_id = str(row.get("passage_id") or "")
            return counter.classify_issue(
                row,
                grain=counter.match_basis(passage_id, cited_passages),
                bound_fact=fact_by_passage.get(passage_id),
                declared_unit=self._declared_unit(str(row.get("metric_id") or "")))

        rows: list[tuple[str, Mapping[str, Any]]] = []
        dropped: list[str] = []
        for metric_id in metrics:
            for period_key in periods:
                result = self._call(
                    "find_counter_evidence",
                    {"metric_id": metric_id, "period_key": period_key})
                if result.outcome is RetrievalOutcome.UNAVAILABLE:
                    sections.warnings.append(codes.packaged_warning(
                        codes.COUNTER_EVIDENCE_UNAVAILABLE,
                        subject_ids=(metric_id, period_key),
                        detail=result.reason))
                    continue
                if result.outcome is not RetrievalOutcome.OK:
                    continue
                if result.truncated:
                    sections.warnings.append(codes.packaged_warning(
                        codes.RETRIEVAL_TRUNCATED,
                        subject_ids=("find_counter_evidence", metric_id, period_key),
                        detail=result.reason or "the 25-row bound bit"))
                kept, lost = counter.narrow(
                    result.rows, metric_ids=metrics, cited_document_ids=cited_documents,
                    # Severity is not qualification: a passage carrying both an `AMBIGUOUS_ALIAS`
                    # rejection and a `QUOTED_SPAN_NOT_IN_PASSAGE` one must be represented by the
                    # second, and both are severity rank 0.
                    prefers=lambda row: classify(row).qualifies)
                rows.extend((period_key, row) for row in kept)
                dropped.extend(lost)

        built: list[counter.CounterEvidenceRow] = self._divergent_reading_rows(sections)
        seen: set[str] = {item.passage.passage_id for item in built}
        for period_key, row in rows:
            passage_id = str(row.get("passage_id") or "")
            if passage_id in seen:
                continue
            seen.add(passage_id)
            if not classify(row).qualifies and self._stamp_diagnostic(
                    sections, passage_id, str(row.get("code") or "")):
                # **A demoted row whose passage the package already carries is stamped, not
                # duplicated.** The passage is in `primary_passages` in full; a second
                # byte-identical row costs ~780 tokens to say nothing the first does not, and it
                # is what pushed §10.2's budget past a supporting passage. What the demotion
                # actually adds is the issue code, so the issue code is what is added — to the
                # row already there. Measured on the inventory candidate: both of §1's two
                # counter-evidence passages take this branch, so the reclassification costs the
                # package nothing at all.
                continue
            full = self._passage_row(passage_id)
            if full is None:
                dropped.append(str(row.get("issue_id") or ""))
                continue
            assembly.remember_document(sections, full)
            text = str(full.get("text") or "")
            grain = counter.match_basis(passage_id, cited_passages)
            if grain == counter.MATCH_BASIS_SAME_PASSAGE:
                # §13.7's Rule A again, from the other side. A refusal that sits in a passage a
                # fact is bound to is the strongest counter-evidence there is — but shipping an
                # *excerpt* of it would put two `PackagedPassage` rows in the package under one
                # `passage_id` with different text, and a consumer indexing passages by id could
                # then run Rule A's column check against a fragment. Carried whole, the two rows
                # are byte-identical and the ambiguity does not exist. The cost is the passage's
                # tokens twice, which the budget discloses rather than hides.
                excerpt = passage_excerpts.whole(text)
            else:
                passage_excerpts.refuse_excerpting_bound_passage(passage_id, cited_passages)
                excerpt = passage_excerpts.window(
                    text, needles=counter.needles_for(row),
                    radius=self._budget.excerpt_radius_chars)

            passage = self._packaged_passage(
                full, excerpt=excerpt, role=EvidenceRole.COUNTER_EVIDENCE,
                diagnostic_codes=(str(row.get("code") or ""),))
            found = classify(row)
            role, basis, why = found.role, found.match_basis, found.why
            if role is EvidenceRole.WARNING_ONLY and passage_id in corroborating:
                # §1.1 in one row. `q12023formxex992sharehol.htm#p8` is a source of a
                # *concordant* observation of `housing_inventory_homes` 2023-03-31 — one of the
                # six that all read 6,261 homes — and the package re-introduced it as a
                # contradiction because it carries an `AMBIGUOUS_ALIAS` about the word
                # "inventory". Having decided the issue is a diagnostic, the honest role for the
                # passage is the one the observations already establish: it corroborates.
                role = EvidenceRole.CORROBORATING_SUPPORT
                why = (f"{why} This passage is also a source of a concordant observation of "
                       "this slot, so it corroborates the fact it was collected against.")
            if passage.quality_status is PassageQuality.UNUSABLE:
                # The quality filter overrides the classification in one direction only. A
                # passage with no proposition in it cannot be a counterpoint whatever the issue
                # attached to it says, and `unusable` is a stronger statement than
                # `warning_only`: there is nothing here to warn *about*.
                role, basis = EvidenceRole.UNUSABLE, grain
                why = passage_quality.describe(
                    passage_quality.Assessment(
                        passage.quality_status, passage.unusable_reason), passage_id)
            built.append(counter.CounterEvidenceRow(
                passage=passage.model_copy(update={"role": role, "match_basis": basis}),
                match_basis=basis,
                grain=grain,
                role=role,
                issue_id=str(row.get("issue_id") or ""),
                code=str(row.get("code") or ""),
                severity=str(row.get("severity") or ""),
                severity_rank=int(row.get("severity_rank") or 0),
                metric_id=str(row.get("metric_id") or ""),
                period_key=period_key,
                row_label=assembly.text_or_none(row, "row_label"),
                excerpt=excerpt,
                why=why,
            ))

        self._place_associations(sections, built, dropped)

    def _stamp_diagnostic(
        self, sections: assembly.PackageSections, passage_id: str, code: str
    ) -> bool:
        """Record an issue code on a passage the package already carries. `True` when it did.

        The three passage sections are searched in the order §10 lists them, and only one row
        can match — a passage is in exactly one section by construction (`_add_context_passages`
        and `_add_explanatory_passages` both exclude what is already carried).
        """
        if not code:
            return False
        for section in ("primary_passages", "context_passages", "explanatory_passages"):
            rows: list[PackagedPassage] = getattr(sections, section)
            for index, passage in enumerate(rows):
                if passage.passage_id != passage_id:
                    continue
                rows[index] = passage.model_copy(update={
                    "diagnostic_codes": tuple(sorted(set(passage.diagnostic_codes) | {code}))})
                return True
        return False

    def _place_associations(
        self,
        sections: assembly.PackageSections,
        built: Sequence[counter.CounterEvidenceRow],
        dropped: Sequence[str],
    ) -> None:
        """Split the classified rows into the two sections, cap each, and disclose each.

        Both sections share `max_counter_evidence`: the diagnostics are the rows the same
        retrieval produced, they are bounded by the same argument about how much of one filing's
        bookkeeping a model should read, and giving them a cap of their own would be a budget
        parameter added for a section that did not exist yesterday.

        **Only counter-evidence rows emit a disclosure warning.** Both `counter_evidence_*`
        codes are `CLAIM_QUALIFYING`, so a warning on a demoted row would oblige the post to
        write a sentence about an extraction diagnostic — §1's defect, one layer down. The
        demoted row's diagnostic travels on the row: its role, its `match_basis` and its
        `diagnostic_codes`.
        """
        cap = section_bounds.cap_for(self._budget, "counter_evidence")
        qualifying = sorted((row for row in built if row.role is EvidenceRole.COUNTER_EVIDENCE),
                            key=lambda item: item.sort_key)
        # Corroborating rows last, because §4 S5 ranks *"extra corroborating passages"* ahead of
        # *"lower-severity diagnostics"* in the trim and the tail is what a trim takes: a second
        # copy of a number the package already states is the cheapest row here to lose.
        demoted = sorted((row for row in built if row.role is not EvidenceRole.COUNTER_EVIDENCE),
                         key=lambda item: (
                             1 if item.role is EvidenceRole.CORROBORATING_SUPPORT else 0,
                             item.sort_key))

        if len(qualifying) > cap:
            sections.caps_hit.add("counter_evidence")
            sections.warnings.append(codes.packaged_warning(
                codes.SECTION_TRUNCATED,
                subject_ids=("counter_evidence",),
                detail=f"counter_evidence: {len(qualifying)} qualified, {cap} carried, ordered "
                       "value evidence first, then passage-grain, then most severe; nothing "
                       f"dropped outranks anything carried. Dropped issue ids: "
                       f"{sorted(set(dropped))[:8]}"))
        assembly.note_available(sections, "counter_evidence", len(qualifying))
        assembly.note_available(sections, "diagnostic_passages", len(demoted))
        if len(demoted) > cap:
            assembly.note_drop(sections, "diagnostic_passages", codes.SECTION_TRUNCATED,
                               count=len(demoted) - cap)
            sections.caps_hit.add("diagnostic_passages")

        for item in qualifying[:cap]:
            sections.counter_evidence.append(item.passage)
            sections.warnings.append(item.disclosure())
        for item in demoted[:cap]:
            sections.diagnostic_passages.append(item.passage)

    def _divergent_reading_rows(
        self, sections: assembly.PackageSections
    ) -> list[counter.CounterEvidenceRow]:
        """Counter-evidence that rests on a **value**: a second reading of a packaged slot.

        This is S2's first qualifying basis and S3's mirror image. For every packaged fact, the
        other observations of its slot are put through
        `observation_equivalence.same_reading`; the ones it calls equivalent become that fact's
        corroboration (`_corroboration`), and the ones it calls divergent become rows here, with
        `counter.basis_for_divergence` turning the divergence reason into the match basis. One
        arithmetic, two answers — which is why S2 and S3 are one change.

        **Quarantined observations are excluded.** §6.1 step 1 quarantines a narrative-lane read
        of a flattened table because the read is defective, and `canonical_point_warning` already
        discloses it; promoting one to counter-evidence would let a known-bad row contradict a
        good one.

        Measured on `graph-v1-0483dc6b4b10`: **zero rows**. 36 of 537 slots hold more than one
        distinct value and every one of them collapses to a single cluster under presentation
        tolerance, so nothing in this corpus disagrees about a number. That is the finding, and
        the code is what will notice the day it stops being true.
        """
        authority = self._resolved_authority()
        rows: list[counter.CounterEvidenceRow] = []
        seen: set[str] = set()
        for fact in sections.facts:
            record = self._by_observation.get(fact.observation_id)
            if record is None:
                continue
            for peer in self._comparable_peers(record):
                answer = same_reading(record, peer, authority=authority)
                if answer or peer.passage_id is None or peer.passage_id in seen:
                    continue
                full = self._passage_row(peer.passage_id)
                if full is None:
                    continue
                seen.add(peer.passage_id)
                assembly.remember_document(sections, full)
                text = str(full.get("text") or "")
                grain = counter.match_basis(peer.passage_id, [fact.passage_id or ""])
                if grain == counter.MATCH_BASIS_SAME_PASSAGE:
                    excerpt = passage_excerpts.whole(text)
                else:
                    excerpt = passage_excerpts.window(
                        text, needles=(str(peer.value), record.metric_id),
                        radius=self._budget.excerpt_radius_chars)
                basis = counter.basis_for_divergence(answer.reason)
                rows.append(counter.CounterEvidenceRow(
                    passage=self._packaged_passage(
                        full, excerpt=excerpt, role=EvidenceRole.COUNTER_EVIDENCE,
                        match_basis=basis),
                    match_basis=basis,
                    grain=grain,
                    role=EvidenceRole.COUNTER_EVIDENCE,
                    metric_id=record.metric_id,
                    period_key=record.period.key,
                    excerpt=excerpt,
                    why=answer.detail,
                ))
        return rows

    def _comparable_peers(self, record: ObservationRecord) -> tuple[ObservationRecord, ...]:
        """The other observations of this record's slot, minus the ones §6.1 quarantined."""
        point = self._point_of.get(record.slot_key)
        quarantined = set(point.quarantined_observation_ids) if point else set()
        return tuple(
            peer for peer in self._by_slot.get(record.slot_key, ())
            if peer.observation_id != record.observation_id
            and peer.observation_id not in quarantined)

    # -- events, metrics, formulas, conflicts, comparability -------------------------------

    def _add_events(
        self, sections: assembly.PackageSections, request: EvidenceRequest
    ) -> None:
        """§10's `events[]`, and **only** the events the request names.

        No window search. §6.7 closes event–metric proximity *structurally* — D5 is the detector
        that may relate an event to a move, it is not implemented in V1, and a packager that
        pulled every event near the candidate's quarter into the model's universe would reopen
        the channel §6.7 exists to close from the other end. For `2022Q4` that would be a credit
        facility and a workforce reduction sitting beside eight metric moves with nothing
        stating that the other seven exist.

        `properties` is empty on every row and that is the projection's decision, not a gap
        here: §13.8 makes event properties free strings and `cypher.FACT_EVIDENCE_FOR_EVENT`
        returns `property_names` rather than the values, *"without being handed a numeral it
        could bind."* `participants` is empty because no §9 tool traverses `PARTICIPATES_IN`.
        """
        cap = section_bounds.cap_for(self._budget, "events")
        for event_id in tuple(request.event_ids)[:cap]:
            result = self._call("get_fact_evidence", {"event_id": event_id})
            if result.outcome is not RetrievalOutcome.OK or not result.rows:
                continue
            row = result.rows[0]
            occurred = assembly.text_or_none(row, "occurred_on")
            announced = assembly.text_or_none(row, "announced_on")
            state = assembly.text_or_none(row, "validation_state")
            sections.events.append(PackagedEvent(
                event_id=event_id,
                event_type_id=str(row.get("event_type_id") or ""),
                occurred_on=occurred,
                announced_on=announced,
                date_basis="occurred_on" if occurred else ("announced_on" if announced else None),
                review_flag=None if state in (None, "clean", "ok") else state,
                passage_id=assembly.text_or_none(row, "passage_id"),
                quoted_text=assembly.text_or_none(row, "quoted_text"),
            ))
            if occurred is None:
                sections.warnings.append(codes.packaged_warning(
                    codes.EVENT_DATE_ABSENT,
                    subject_ids=(event_id,),
                    detail="the event carries no `occurred_on`; §13.8 refuses an asserted "
                           "effective date and §13.14 refuses an asserted ordering"))
            if state not in (None, "clean", "ok"):
                sections.warnings.append(codes.packaged_warning(
                    codes.EVENT_REVIEW_FLAG, subject_ids=(event_id,),
                    detail=f"validation_state={state!r}"))
        if len(request.event_ids) > cap:
            sections.caps_hit.add("events")

    def _add_metrics(
        self,
        sections: assembly.PackageSections,
        candidate: StoryCandidate,
        request: EvidenceRequest,
    ) -> None:
        """§10's `metrics[]`, from the **ontology** and never from the `:Metric` node (C4).

        The node carries 22 properties and none of them is `percentage_min`, `percentage_max`,
        `distinct_from` or `reconciles_to` *(checked live)*. A packager that read them off the
        graph would get `None` — because Neo4j stores no null and the property was never written
        — and `None` reads as *"no bound"*, which turns §13.3's percentage check, the single
        most likely factual error, into a check that always passes.
        """
        registry = self._resolved_registry()
        authority = self._resolved_authority()
        referenced = list(dict.fromkeys(
            [*request.metric_ids, *candidate.metric_ids,
             *(fact.metric_id for fact in sections.facts)]))
        cap = section_bounds.cap_for(self._budget, "metrics")
        for metric_id in referenced[:cap]:
            definition = registry.find(metric_id)
            if not isinstance(definition, MetricDefinition):
                continue
            population = definition.population
            sections.metrics.append(PackagedMetric(
                metric_id=metric_id,
                label=definition.label,
                unit=definition.unit,
                allowed_units=tuple(definition.allowed_units),
                period_type=getattr(definition.period_type, "value", definition.period_type),
                aliases=tuple(definition.aliases),
                distinct_from=tuple(definition.distinct_from),
                mutually_distinct_groups=tuple(sorted(authority.groups.get(metric_id, ()))),
                ambiguities=tuple(
                    MetricAmbiguity(code=a.code, description=a.description, impact=a.impact)
                    for a in definition.ambiguities),
                population=_population_text(population),
                percentage_min=definition.percentage_min,
                percentage_max=definition.percentage_max,
            ))
        if len(referenced) > cap:
            sections.caps_hit.add("metrics")
        self._add_ambiguity_warnings(sections)
        self._add_population_warning(sections)

    def _add_ambiguity_warnings(self, sections: assembly.PackageSections) -> None:
        """§10.1: a used fact whose metric declares an ambiguity carries the ontology's own words.

        The `description` and `impact` are copied verbatim rather than summarised — §10.1
        requires the caveat to reach the post, and a paraphrase of a caveat is a new claim.
        """
        for metric in sections.metrics:
            subjects = tuple(
                fact.observation_id for fact in sections.facts if fact.metric_id == metric.metric_id)
            for ambiguity in metric.ambiguities:
                sections.warnings.append(codes.packaged_warning(
                    codes.METRIC_AMBIGUITY_DECLARED,
                    subject_ids=(metric.metric_id, *subjects),
                    detail=f"{ambiguity.code} (impact: {ambiguity.impact}) — "
                           f"{ambiguity.description}"))

    def _add_population_warning(self, sections: assembly.PackageSections) -> None:
        """§10.1's *"differing `population_definition_raw` between two compared facts"*."""
        populations = {m.metric_id: m.population for m in sections.metrics if m.population}
        if len(set(populations.values())) > 1:
            sections.warnings.append(codes.packaged_warning(
                codes.POPULATION_DEFINITION_DIFFERS,
                subject_ids=tuple(populations),
                detail="; ".join(f"{k}: {v}" for k, v in sorted(populations.items()))))

    def _add_formula_windows(
        self,
        sections: assembly.PackageSections,
        candidate: StoryCandidate,
        request: EvidenceRequest,
    ) -> None:
        """§10's `formula_windows[]`, one per referenced metric-period, with the notes intact.

        `adjustment_components` keeps its `note` text because the notes are the only place the
        corpus says what an adjustment *is*, and §6.3's F8 shows an expression whose declared
        four-term form and the corpus's three-term identity disagree. A window without its notes
        would let a verifier check the wrong identity and pass.

        A metric whose anchor dates resolve to two different versions raises
        `formula_window_boundary_crossed` — §10.1's own item, and the one that matters most for
        `adjusted_gross_profit`, the only versioned metric in this ontology.
        """
        registry = self._resolved_registry()
        cap = section_bounds.cap_for(self._budget, "formula_windows")
        dates = sorted({
            record.period.anchor_date
            for record in self._records_for(request, candidate)
            if record.period.anchor_date})
        seen: set[tuple[str, str]] = set()
        by_metric: dict[str, set[str]] = {}

        for metric_id in dict.fromkeys([*request.metric_ids, *candidate.metric_ids]):
            for as_of in dates:
                formula = registry.formula_for(metric_id, as_of)
                if formula is None:
                    continue
                by_metric.setdefault(metric_id, set()).add(formula.concept_id)
                if (metric_id, formula.concept_id) in seen or len(seen) >= cap:
                    continue
                seen.add((metric_id, formula.concept_id))
                sections.formula_windows.append(_packaged_window(metric_id, formula))

        for metric_id, versions in sorted(by_metric.items()):
            if len(versions) > 1:
                sections.warnings.append(codes.packaged_warning(
                    codes.FORMULA_WINDOW_BOUNDARY_CROSSED,
                    subject_ids=(metric_id, *sorted(versions)),
                    detail=f"{metric_id}'s anchor dates resolve to {len(versions)} formula "
                           f"versions ({', '.join(sorted(versions))}); a comparison across the "
                           "boundary compares two definitions"))

    def _add_conflicts(
        self, sections: assembly.PackageSections, plans: Sequence[_FactPlan]
    ) -> None:
        """§10's `conflicts[]` and the two §10.1 warnings that read the same slots.

        A slot appears here only when it held **more than one reading** or failed to resolve —
        a slot with one value is not a conflict, and a section that listed every slot would be
        the unbounded shape §0c item 4 measured. Measured on this run: 537 slots, 36
        multi-valued, all 36 collapsing to one cluster under presentation tolerance, so the rows
        this section produces today are `presentation_rounding` and nothing else.
        """
        cap = section_bounds.cap_for(self._budget, "conflicts")
        slots = list(dict.fromkeys(plan.slot for plan in plans))
        for slot in slots:
            point = self._point_of.get(slot)
            if point is None:
                continue
            rendered = SLOT_SEPARATOR.join(slot)
            if point.n_docs == 1:
                sections.warnings.append(codes.packaged_warning(
                    codes.SINGLE_SOURCE, subject_ids=(rendered,),
                    detail="one document reports this slot; §6.10's corroboration term reads "
                           "the same number"))
            if point.warnings:
                sections.warnings.append(codes.packaged_warning(
                    codes.CANONICAL_POINT_WARNING, subject_ids=(rendered,),
                    detail=", ".join(point.warnings)))
            if point.status is CanonicalStatus.CONFLICT:
                sections.warnings.append(codes.packaged_warning(
                    codes.SLOT_UNRESOLVED, subject_ids=(rendered,),
                    detail="the slot exists and emits no canonical value (§6.1 step 4)"))
            if point.distinct_values <= 1 and point.status is CanonicalStatus.OK:
                continue
            if len(sections.conflicts) >= cap:
                sections.caps_hit.add("conflicts")
                break
            classification, rule = _classify(point)
            sections.conflicts.append(Conflict(
                slot=rendered,
                clusters=tuple(
                    ConflictCluster(value=cluster.value,
                                    document_ids=cluster.document_ids,
                                    observation_ids=cluster.observation_ids)
                    for cluster in point.clusters),
                classification=classification,
                resolution_rule=rule,
            ))
            sections.warnings.append(codes.packaged_warning(
                codes.FACT_CONFLICT_DISCLOSED, subject_ids=(rendered,),
                detail=f"{point.distinct_values} distinct readings, "
                       f"{len(point.clusters)} cluster(s), classified {classification}"))

    def _add_compatibility(
        self,
        sections: assembly.PackageSections,
        candidate: StoryCandidate,
        request: EvidenceRequest,
    ) -> None:
        """§10's `compatibility[]` — **only the decisions the candidate's own comparisons made.**

        This is the section §0c item 4 names: *"every comparability decision made"*, O(n²) over a
        26-quarter series, in a package with a token budget and no cap. The comparisons a
        candidate makes are derivable from its own shape and are at most a handful:

            two metrics, one period      → one `DIVERGENCE` pair
            one metric, two periods      → one `MOVEMENT` pair
            one metric, three or more    → consecutive `ACCELERATION` pairs
            one slot                     → none; there is nothing to compare

        The claim kind is not cosmetic: R8's tolerance floor and R10's adjacency apply to a step
        claim and not to a level one, so a `MOVEMENT` and a `LEVEL` over the same two points can
        legitimately disagree, and recording which one was asked is what makes the answer
        readable.
        """
        cap = section_bounds.cap_for(self._budget, "compatibility")
        authority = self._resolved_authority()
        metrics = tuple(dict.fromkeys(request.metric_ids or candidate.metric_ids))
        periods = tuple(dict.fromkeys(request.period_keys or candidate.anchor_period_keys))

        pairs: list[tuple[ClaimKind, CanonicalPoint, CanonicalPoint]] = []
        if len(metrics) >= 2 and periods:
            for period in periods:
                left, right = self._point_of.get((metrics[0], period)), self._point_of.get(
                    (metrics[1], period))
                if left is not None and right is not None:
                    pairs.append((ClaimKind.DIVERGENCE, left, right))
        elif len(metrics) == 1 and len(periods) >= 2:
            ordered = [p for p in (self._point_of.get((metrics[0], key)) for key in periods)
                       if p is not None]
            ordered.sort(key=lambda point: (point.period.anchor_date or "", point.period.key))
            claim = ClaimKind.ACCELERATION if len(ordered) >= 3 else ClaimKind.MOVEMENT
            pairs.extend((claim, later, earlier)
                         for earlier, later in zip(ordered, ordered[1:]))

        for claim, left, right in pairs[:cap]:
            series = self._series.get(left.metric_id) if claim in (
                ClaimKind.MOVEMENT, ClaimKind.ACCELERATION) else None
            answer = comparable(left, right, claim=claim, authority=authority, series=series)
            left_id = SLOT_SEPARATOR.join(left.slot_key)
            right_id = SLOT_SEPARATOR.join(right.slot_key)
            if isinstance(answer, Refuse):
                sections.compatibility.append(CompatibilityDecision(
                    left_id=left_id, right_id=right_id, rule_id=answer.rule,
                    comparable=False, reason=f"{answer.reason}: {answer.detail}"))
                sections.warnings.append(codes.packaged_warning(
                    codes.COMPARISON_REFUSED, subject_ids=(left_id, right_id),
                    detail=f"{answer.rule} — {answer.reason}: {answer.detail}"))
                continue
            reason = "; ".join(f"{w.code}: {w.detail}" for w in answer.warnings)
            sections.compatibility.append(CompatibilityDecision(
                left_id=left_id, right_id=right_id, rule_id=f"§6.9/{claim.value}",
                comparable=True, reason=reason))
            if answer.warnings:
                sections.warnings.append(codes.packaged_warning(
                    codes.COMPARISON_WARNED, subject_ids=(left_id, right_id), detail=reason))
        if len(pairs) > cap:
            sections.caps_hit.add("compatibility")

    def _add_run_level_warnings(
        self, sections: assembly.PackageSections, candidate: StoryCandidate
    ) -> None:
        """The disclosures that are about the run and the layer rather than about one row."""
        if self._unreadable:
            sections.warnings.append(codes.packaged_warning(
                codes.OBSERVATION_LOAD_INCOMPLETE,
                subject_ids=tuple(name for name, _reason in self._unreadable),
                detail="the caller could not read " + "; ".join(
                    f"{name}: {reason}" for name, reason in self._unreadable)
                       + ". §10 may not treat an incomplete read as complete (AR2 b1)"))
        if candidate.warnings:
            sections.warnings.append(codes.packaged_warning(
                codes.CANDIDATE_WARNING,
                subject_ids=(candidate.candidate_id,),
                detail=", ".join(candidate.warnings)))
        for fact in sections.facts:
            if fact.validation_state == "warned":
                sections.warnings.append(codes.packaged_warning(
                    codes.UNPREFERRED_SOURCE_LANE, subject_ids=(fact.observation_id,),
                    detail=f"validation_state=warned on {fact.metric_id} {fact.period_key} "
                           f"(lane {fact.source_lane})"))
        sections.warnings.append(codes.packaged_warning(
            codes.RELATIONSHIPS_UNAVAILABLE_IN_V1,
            detail="`relationships[]` is empty because no §9 tool implemented at S1 returns a "
                   "relationship; `find_related_entities` is in §9's table and is not one of the "
                   "nine. An empty section here is a gap in the retrieval layer"))
        sections.warnings.append(codes.packaged_warning(
            codes.EVIDENCE_SOURCES_ABSENT_IN_V1,
            detail="`evidence_sources[]` is empty because zero `:EvidenceSource` nodes exist — "
                   "all evidence edges land on a `:Passage` (§13.7.2). Rule C is Unavailable, "
                   "not silently satisfied"))
        sections.warnings.append(codes.packaged_warning(
            codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH,
            subject_ids=(candidate.subject_entity_id,),
            detail="no §9 tool reads `:Entity` — `opendoor` carries 2,704 "
                   "`OBSERVATION_OF_SUBJECT` edges and one hop outward is the whole graph — so "
                   "`entity_text` and `labels` are the caller's and `resolved` is inferred from "
                   "the observations' own `subject_entity_id`"))

    # -- assembly, bounds and identity -----------------------------------------------------

    # -- shared machinery -----------------------------------------------------------------

    def _call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        """One §9 call, refused outright when the tool is not one this stage may make.

        Ruling 1 as control flow. `get_metric_history` is bounded at 200 rows, five metrics
        exceed it, and only canonical-series construction pages it and proves completeness — so
        a packaging call to it would be the exact defect AR2's b1 measured, and the cheapest way
        to make that unreachable is to refuse the name.
        """
        if tool not in PACKAGING_TOOLS:
            raise PackagingError(
                f"packaging may not call {tool!r}; the tools it may call are "
                f"{sorted(PACKAGING_TOOLS)}. `get_metric_history` in particular is bounded at "
                "200 rows and five metrics exceed it — only canonical-series construction pages "
                "it and proves completeness, and one un-paged call yields 93 candidates and "
                "zero refusals (AR2 b1)")
        return self._retriever.call(tool, parameters)

    def _passage_row(self, passage_id: str) -> Mapping[str, Any] | None:
        """One passage in full, through the only §9 tool that returns whole `:Passage.text`."""
        result = self._call(
            "get_passage_context", {"passage_id": passage_id, "before": 0, "after": 0})
        if result.outcome is not RetrievalOutcome.OK or not result.rows:
            return None
        return result.rows[0]

    def _packaged_passage(
        self,
        row: Mapping[str, Any],
        *,
        excerpt: passage_excerpts.Excerpt,
        role: EvidenceRole,
        match_basis: str = "",
        query_terms: Sequence[str] = (),
        score: float | None = None,
        diagnostic_codes: Sequence[str] = (),
    ) -> PackagedPassage:
        """`char_count` is the **full** passage's length even on an excerpt, so a fragment is
        visibly a fragment: `char_end - char_start < char_count` is the check, and a row that
        reported the window's own length would make every excerpt look whole.

        `role` is a required argument for the reason `PackagedPassage.role` is a required field:
        every one of the call sites below knows which section it is filling, and a default here
        would be this helper answering for the one that does not.

        **`quality_status` is computed here, over the passage's own full text and never over the
        window.** S1 left it `UNASSESSED` because nothing assessed it; S2 assesses every row the
        packager builds, from one call site, so no section can be the one that forgot. The text
        assessed is `row["text"]` — what `get_passage_context` returned — rather than
        `excerpt.text`, because a ±400-character window of a long table can look like a fragment
        while the passage it came from is 2,262 characters of KPI rows, and *"is there a
        proposition here"* is a question about the passage.

        A `PRIMARY_SUPPORT` row assessed `UNUSABLE` keeps its role: a fact is bound to it and
        §13.7's Rule A needs it whatever its content, so the finding is recorded and the row
        stays. Measured over `graph-v1-0483dc6b4b10`: **all 150 passages that evidence an
        observation assess `usable`**, so the branch is a guard rather than a live case.
        """
        assessment = passage_quality.assess(
            str(row.get("text") or "") or excerpt.text)
        return PackagedPassage(
            passage_id=str(row.get("passage_id") or ""),
            document_id=str(row.get("document_id") or ""),
            text=excerpt.text,
            char_count=int(row.get("char_count") or len(excerpt.text)),
            heading_path=tuple(str(part) for part in (row.get("heading_path") or ())),
            section_id=assembly.text_or_none(row, "section_id"),
            passage_kind=assembly.text_or_none(row, "passage_kind"),
            source_url=assembly.text_or_none(row, "source_url"),
            char_start=excerpt.char_start,
            char_end=excerpt.char_end,
            excerpted=excerpt.excerpted,
            query_terms=tuple(query_terms),
            score=score,
            role=role,
            match_basis=match_basis,
            quality_status=assessment.quality,
            unusable_reason=assessment.reason,
            diagnostic_codes=tuple(sorted(set(diagnostic_codes))),
        )

    def _records_for(
        self, request: EvidenceRequest, candidate: StoryCandidate
    ) -> tuple[ObservationRecord, ...]:
        anchors = set(candidate.anchor_observation_ids) | set(request.observation_ids)
        return self._candidate_records(request, anchors)

    def _search_window(self, request: EvidenceRequest) -> dict[str, Any]:
        """The date window a filtered explanatory search would use, or `{}` for an unfiltered one.

        Derived from the anchor periods' own dates and nothing else. `document_types` is
        deliberately never set: it is the filter that turns R2b's capped pool into a starved one
        — `["the"]` + `shareholder_letter` is the measured case — and packaging has no question
        that needs it.
        """
        dates = sorted({
            record.period.anchor_date
            for key in request.period_keys
            for record in self._records
            if record.period.key == key and record.period.anchor_date
        })
        if not dates:
            return {}
        return {"since": dates[0], "until": dates[-1]}

    def _inner_search_limit(self) -> int:
        """The retriever's own pre-filter pool bound, read off it rather than restated.

        `getattr` because `GraphRetriever` is a structural protocol and a stub used in a test
        legitimately has no such attribute; the fallback is R2b's measured value, and the
        comparison it feeds is `pool >= limit`, which is conservative in the safe direction.
        """
        return int(getattr(self._retriever, "_inner_search_limit", 500))

    def _metric_label(self, metric_id: str) -> str:
        definition = self._resolved_registry().find(metric_id)
        return definition.label if definition is not None else metric_id

    def _ambiguities(self, metric_id: str) -> tuple[Any, ...]:
        definition = self._resolved_registry().find(metric_id)
        return tuple(getattr(definition, "ambiguities", ()) or ())

    def _resolved_registry(self) -> ConceptRegistry:
        if self._registry is None:
            from ontology import load_ontology

            self._registry = load_ontology(self._identity.ontology_id).registry
        return self._registry

    def _resolved_authority(self) -> ComparabilityAuthority:
        if self._authority is None:
            from story.core.series import default_authority

            self._authority = default_authority()
        return self._authority


# -- module-level helpers -------------------------------------------------------------------


def _classify(point: CanonicalPoint) -> tuple[str, str]:
    """§6.1's own vocabulary for what happened to a multi-valued slot, and the rule that did it."""
    if point.status is CanonicalStatus.CONFLICT:
        return "unresolved", "canon-policy:1.0.0/step4_no_document_majority"
    if point.status is CanonicalStatus.RESOLVED_BY_MAJORITY:
        return "resolved_by_majority", "canon-policy:1.0.0/step4_document_majority"
    return "presentation_rounding", "canon-policy:1.0.0/step2_presentation_tolerance"


def _packaged_window(metric_id: str, formula: MetricFormulaVersion) -> PackagedFormulaWindow:
    return PackagedFormulaWindow(
        metric_id=metric_id,
        version_id=formula.concept_id,
        valid_from=formula.valid_from,
        valid_to=formula.valid_to,
        expression=formula.expression,
        component_metrics=tuple(formula.component_metrics),
        adjustment_components=tuple(
            {
                "label": component.label,
                "metric_id": component.metric_id or "",
                "operation": component.operation,
                # Stringified rather than left a bool: `PackagedFormulaWindow` types the
                # component as `Mapping[str, str]`, and a bool in a string map is a type error
                # in one direction and a silently coerced `"True"` in the other.
                "included": "true" if component.included else "false",
                "note": component.note or "",
            }
            for component in formula.adjustment_components),
        basis="cohort" if any(
            "cohort" in (component.note or "").lower()
            for component in formula.adjustment_components) else "period",
    )


def _population_text(population: Any) -> str | None:
    """The ontology's population wording, normalized first and raw as the fallback.

    `PackagedMetric.population` is one string and the ontology's `PopulationDefinition` holds a
    normalized form plus up to three raw variants — three is the real count for the 120-day
    metric. The normalized form is preferred and the variants are joined when there is none,
    because dropping to the first variant silently would assert one of three wordings is the
    definition, which is exactly what the ambiguity says nobody has established.
    """
    if population is None:
        return None
    if getattr(population, "normalized", None):
        return str(population.normalized)
    variants = tuple(getattr(population, "raw_variants", ()) or ())
    return " | ".join(variants) if variants else None


def _pool_detail(
    pool: int,
    limit: int,
    rows: int,
    derived: query_terms.DerivedTerms,
    window: Mapping[str, Any],
) -> str:
    """Everything the S5 ruling requires a capped-pool warning to record, in one string."""
    return (
        f"search_pool_capped: candidate_pool_size={pool} reached the retriever's inner "
        f"limit of {limit}, which `db.index.fulltext.queryNodes` applies **before** the "
        f"document filter; {rows} row(s) survived the filters. "
        f"query_term_source=code_derived_from_candidate ({derived.basis}), "
        f"terms={list(derived.terms)}, filter_basis="
        f"{dict(sorted(window.items())) or 'none'}"
    )


__all__ = [
    "PACKAGING_TOOLS",
    "SLOT_SEPARATOR",
    "BoundedEvidencePackageBuilder",
    "PackagingError",
]
