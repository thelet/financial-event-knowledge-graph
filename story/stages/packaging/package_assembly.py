"""Turning selected sections into a `StoryEvidencePackage`: the caps, the trim, the id, the digest.

Responsibility: everything about the package that is **not** a decision about which evidence goes
in it. This module reads no graph, resolves no ontology and selects nothing — hand it the rows
`evidence_package.py` chose and it enforces §10.2's caps, trims to §10.2's token total in the
stated order, derives `documents[]`, mints `package_id` and stamps `package_content_digest`.

The boundary is worth having because the two halves fail differently. A selection bug ships the
wrong evidence; an assembly bug ships a package whose digest does not reproduce, whose `caps_hit`
disagrees with its own counts, or whose `documents[]` names a filing nothing in the package
cites. The second kind is testable with no retriever at all, and this is the module those tests
drive.

**§10.3's ordering is forced and is the reason `finalize` is one function.** `package_id`
digests the *sorted fact and passage ids*, so it cannot be minted before trimming;
`package_content_digest` covers the whole package including that id, so it cannot be computed
before the id exists; and the artifact token estimate is a field of the thing it measures —
S0's F6 again, answered the same way, by measuring with both estimates at `0` and the digest at
`""` and stamping all three afterwards.

**The trim targets `prompt_token_estimate`, not the artifact.** §10.2's total bounds what
reaches a model, and `retrieval_trace` reaches none — see `section_bounds.prompt_slice` for the
measurement that forced the split. Both numbers are reported; only one is a bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import story.stages.packaging.counter_evidence as counter
import story.stages.packaging.section_bounds as section_bounds
import story.stages.packaging.warning_codes as codes
from story.core.graph_identity import GraphIdentity
from story.core.keys import package_content_digest, package_id
from story.core.models import (
    PACKAGE_VERSION,
    BudgetParameters,
    ComparabilityFact,
    CompatibilityDecision,
    Conflict,
    IdentityFact,
    PackageBudget,
    PackagedDocument,
    PackagedEvent,
    PackagedFact,
    PackagedFormulaWindow,
    PackagedMetric,
    PackagedPassage,
    PackagedSubject,
    PackagedWarning,
    RetrievalTraceEntry,
    SemanticFact,
    StoryCandidate,
    StoryEvidencePackage,
)

#: What `retrieval_trace[].elapsed_ms` is set to inside a package, and it is not a measurement.
#:
#: **§10's trace field and §10.3's reproducibility requirement contradict each other, and this
#: is the resolution.** §10.3: *"Rebuilding a package from the same graph run must produce the
#: same digest, and a test asserts it."* `elapsed_ms` is wall-clock — the same call measured
#: 0.9 ms and 1.4 ms on two consecutive builds against the same container — so a digest over a
#: package containing it can never reproduce, and the requirement §10.3 states as testable would
#: be untestable. The timing stays where it was measured, on `BoundedGraphRetriever.trace()`,
#: which §14's run manifest can carry; the package keeps the tool, the parameters, the row count,
#: the `truncated` flag and the outcome, which are the fields a reviewer reads and are all
#: functions of the graph run.
#:
#: Zero and not a sentinel like `-1.0` because the field is typed `float` and every real call
#: takes more than zero: a reader who sees `0.0` on all forty rows can only conclude the package
#: does not carry timings, which is exactly what is true.
TRACE_ELAPSED_MS_NOT_CARRIED = 0.0


@dataclass(frozen=True, slots=True)
class TokenEstimates:
    """The two numbers `PackageBudget` carries, measured together over one payload.

    One type rather than two calls because both are functions of the same serialisation and
    measuring them separately would serialise the package twice per trim iteration — the loop
    already rebuilds it once per dropped row.
    """

    #: The whole package's canonical JSON. Informational: it is what is written to disk.
    artifact: int
    #: `section_bounds.prompt_slice` of it — the largest slice a model can be shown. **This is
    #: the number §10.2's total and ceiling bind.**
    prompt: int


@dataclass
class PackageSections:
    """The package under construction. Mutable, and never handed out.

    A working structure rather than sixteen local variables because the trim loop rebuilds the
    package repeatedly and has to be able to remove one row from one section without the rest of
    the assembly knowing which. `facts` and `primary_passages` move together — dropping a primary
    drops the facts bound to it (§13.7's Rule A) — and that coupling is the reason this is one
    object rather than sixteen.
    """

    subject: PackagedSubject | None = None
    facts: list[PackagedFact] = field(default_factory=list)
    #: The ontology's declarations as facts (§4 S4). Selected by S4; carried, counted and
    #: digested from S1 so the section cannot arrive without the budget noticing.
    semantic_facts: list[SemanticFact] = field(default_factory=list)
    identity_facts: list[IdentityFact] = field(default_factory=list)
    comparability_facts: list[ComparabilityFact] = field(default_factory=list)
    metrics: list[PackagedMetric] = field(default_factory=list)
    formula_windows: list[PackagedFormulaWindow] = field(default_factory=list)
    events: list[PackagedEvent] = field(default_factory=list)
    primary_passages: list[PackagedPassage] = field(default_factory=list)
    context_passages: list[PackagedPassage] = field(default_factory=list)
    explanatory_passages: list[PackagedPassage] = field(default_factory=list)
    counter_evidence: list[PackagedPassage] = field(default_factory=list)
    warnings: list[PackagedWarning] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    compatibility: list[CompatibilityDecision] = field(default_factory=list)
    documents: list[PackagedDocument] = field(default_factory=list)
    retrieval_trace: list[RetrievalTraceEntry] = field(default_factory=list)
    caps_hit: set[str] = field(default_factory=set)
    #: Which document each passage belongs to, so the derived `documents[]` can be rebuilt after
    #: a trim without re-reading anything.
    document_rows: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    #: The `(metric, period)` slots the candidate anchored on. **A trim may never leave one of
    #: these with no fact.** §6.11 digests `anchor_observation_ids` into the `candidate_id`, so a
    #: `metric_move` package carrying only the 2022Q2 side of a 2022Q2 → 2022Q3 move would be a
    #: package whose own id claims evidence it does not hold — and the half it dropped is the
    #: half the story is about. Measured before this floor existed: the token budget trimmed
    #: `contribution_margin 2022Q2 → 2022Q3` down to the 2022Q2 reading alone.
    required_slots: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class PackageAssembler:
    """§10.2's caps and §10.3's identity, over sections somebody else selected.

    Frozen and holding only the run identity and the budget: everything else it needs is in the
    `PackageSections` it is given, which is what makes it drivable from a test with no graph, no
    ontology and no retriever.
    """

    identity: GraphIdentity
    budget: BudgetParameters

    # -- the whole of §10.3, in the order §10.3 forces --------------------------------------

    def finalize(
        self, sections: PackageSections, candidate: StoryCandidate
    ) -> StoryEvidencePackage:
        """Cap, trim the model-visible slice to the token budget, then mint the id and digest.

        **The one place an estimate is knowingly approximate**, stated rather than hidden:
        stamping the measured numbers into the budget block lengthens the JSON by their digits —
        eight characters, two tokens — over what `artifact` measured. The alternative is a value
        that has to contain its own length, which has no fixed point. `prompt` is exact, because
        the block it is stored in is not in the slice it measures. The two budget warnings are
        added *before* the final measurement rather than after, so a trimmed package still
        reports the size it actually is.
        """
        self.cap_derived_sections(sections)
        estimates = self.estimate(sections, candidate)
        announced = False
        while estimates.prompt > self.budget.max_total_tokens and self.trim_one(sections):
            if not announced:
                # Announced on the **first** drop, not after the last, so the warning's own
                # ~60 tokens are inside every measurement that follows. Adding it afterwards was
                # measured to push a package back over the budget it had just been trimmed to
                # fit — `gaap_gross_margin 2022Q2 → 2022Q3` finished at 5,032 against 5,000.
                announced = True
                sections.warnings.append(codes.packaged_warning(
                    codes.TOKEN_BUDGET_TRIMMED,
                    subject_ids=("token_budget",),
                    detail=f"§10.2's {self.budget.max_total_tokens}-token budget bound the "
                           f"model-visible slice; rows were dropped in the order "
                           f"{', '.join(section_bounds.TRIM_ORDER)}, each section to its floor "
                           "before the next was touched, and no drop may strand a slot the "
                           "candidate anchored on"))
            self.cap_derived_sections(sections)
            estimates = self.estimate(sections, candidate)

        if estimates.prompt > section_bounds.MAX_TOTAL_TOKENS_CEILING:
            sections.warnings.append(codes.packaged_warning(
                codes.PACKAGE_EXCEEDS_TOKEN_CEILING,
                subject_ids=(candidate.candidate_id,),
                detail="the model-visible slice of the package is over §10.2's ceiling of "
                       f"{section_bounds.MAX_TOTAL_TOKENS_CEILING} tokens with every section at "
                       "its floor; the local runtime is `-c 8192` and this leaves no room for "
                       "the system prompt"))
            self.cap_derived_sections(sections)
            estimates = self.estimate(sections, candidate)

        package = self.assemble(sections, candidate, estimates=estimates)
        return package.with_content_digest(
            package_content_digest(package.digestible_payload()))

    def estimate(self, sections: PackageSections, candidate: StoryCandidate) -> TokenEstimates:
        """§10.2.1's two estimates over the package as the sections currently stand."""
        payload = self.assemble(
            sections, candidate, estimates=TokenEstimates(artifact=0, prompt=0)
        ).digestible_payload()
        return TokenEstimates(
            artifact=section_bounds.estimate_tokens(payload),
            prompt=section_bounds.estimate_tokens(section_bounds.prompt_slice(payload)))

    # -- the trim ---------------------------------------------------------------------------

    def trim_one(self, sections: PackageSections) -> bool:
        """Drop exactly one row, in `TRIM_ORDER`, honouring each section's floor.

        Returns whether anything moved, so the caller's loop terminates on a package that cannot
        get any smaller rather than spinning. Dropping the *last* row of a section is dropping
        the least defensible one, because every section here is already sorted into its own
        stated drop order.

        **A primary passage is put back when its removal would strand an anchor slot.** That is
        the second floor, above `TRIM_FLOOR`'s "at least one": dropping a primary drops the facts
        bound to it (§13.7's Rule A), and dropping the last fact of a slot the candidate anchored
        on produces a package that describes half a comparison. The trimmer then moves to the
        next section rather than giving up, so `counter_evidence` can still yield.
        """
        for name in section_bounds.TRIM_ORDER:
            rows = getattr(sections, name)
            if len(rows) <= section_bounds.TRIM_FLOOR[name]:
                continue
            if name == "primary_passages":
                if not may_drop_last_primary(sections):
                    continue
                rows.pop()
                drop_orphaned_facts(sections)
            else:
                rows.pop()
            sections.caps_hit.add(name)
            sections.caps_hit.add("token_budget")
            return True
        return False

    def cap_derived_sections(self, sections: PackageSections) -> None:
        """`documents[]`, `warnings[]` and `retrieval_trace[]`, recomputed after every trim.

        `documents[]` is *derived* (§10) and must be rebuilt rather than filtered: a trim that
        removed the last passage of a filing must remove the filing, or the package would carry
        a document nothing in it cites. `warnings[]` drops least-severe-first, which is D6's
        ruling applied to this cap — a bound that dropped a `REFUSE` while keeping an `ADVISORY`
        would hide the one thing §13.17 acts on.
        """
        sections.documents = self.derive_documents(sections)
        warnings, dropped = section_bounds.truncate(
            dedupe(without_orphaned_disclosures(sections)),
            section_bounds.cap_for(self.budget, "warnings"),
            key=codes.warning_sort_key)
        if dropped:
            sections.caps_hit.add("warnings")
        sections.warnings = list(warnings)
        trace, dropped_trace = section_bounds.truncate(
            sections.retrieval_trace, section_bounds.cap_for(self.budget, "retrieval_trace"))
        if dropped_trace:
            sections.caps_hit.add("retrieval_trace")
        sections.retrieval_trace = list(trace)

    def derive_documents(self, sections: PackageSections) -> list[PackagedDocument]:
        """§10's `documents[]`, built **only from cited passages**.

        The note in §10 is load-bearing: *"an `:EvidenceSource` carrying a `document_id` does NOT
        imply that `:Document` node exists"* (§13.7.2). So the section is derived from what the
        package actually cites and from nothing else, and `title` and `content_sha256` stay
        `None` — the node carries a `title` no §9 tool returns, and carries no `content_sha256`
        at all *(18 properties, checked live)*.
        """
        cited: list[str] = []
        for passage in (*sections.primary_passages, *sections.context_passages,
                        *sections.explanatory_passages, *sections.counter_evidence):
            if passage.document_id and passage.document_id not in cited:
                cited.append(passage.document_id)
        for fact in sections.facts:
            if fact.document_id and fact.document_id not in cited:
                cited.append(fact.document_id)
        cap = section_bounds.cap_for(self.budget, "documents")
        rows: list[PackagedDocument] = []
        for document_id in cited[:cap]:
            row = sections.document_rows.get(document_id) or {}
            rows.append(PackagedDocument(
                document_id=document_id,
                form=text_or_none(row, "form"),
                filing_date=text_or_none(row, "filing_date"),
                report_date=text_or_none(row, "report_date"),
                accession=text_or_none(row, "accession"),
                # No §9 tool returns `Document.source_url`, so this is the URL the *cited
                # passage* or the `EVIDENCED_BY` edge carries. Verified identical on this run:
                # both render the document's own `…/open-20220930.htm`.
                source_url=text_or_none(row, "source_url"),
                document_type=text_or_none(row, "document_type"),
                title=None,
                content_sha256=None,
            ))
        return rows

    # -- assembly ---------------------------------------------------------------------------

    def assemble(
        self, sections: PackageSections, candidate: StoryCandidate, *, estimates: TokenEstimates
    ) -> StoryEvidencePackage:
        facts = tuple(sections.facts)
        passages = tuple(sections.primary_passages)
        if sections.subject is None:
            raise ValueError(
                "a package with no subject cannot be assembled; §13.11 makes subject identity a "
                "refusal and an absent subject would be neither resolved nor unresolved")
        return StoryEvidencePackage(
            package_id=package_id(
                candidate_id=candidate.candidate_id,
                package_version=PACKAGE_VERSION,
                graph_run_id=self.identity.graph_run_id,
                run_complete_sha256=self.identity.run_complete_sha256 or "",
                ontology_definition_hash=self.identity.ontology_definition_hash,
                fact_ids=[fact.observation_id for fact in facts],
                passage_ids=[passage.passage_id for passage in passages],
                budget=self.budget,
            ),
            candidate_id=candidate.candidate_id,
            detector_id=candidate.detector_id,
            detector_version=candidate.detector_version,
            policy_version=candidate.policy_version,
            graph_run_id=self.identity.graph_run_id,
            graph_projection_version=self.identity.graph_projection_version,
            extraction_run_id=self.identity.extraction_run_id,
            run_complete_sha256=self.identity.run_complete_sha256 or "",
            ontology_id=self.identity.ontology_id,
            ontology_definition_hash=self.identity.ontology_definition_hash,
            ontology_semantic_version=self.identity.ontology_version,
            subject=sections.subject,
            facts=facts,
            semantic_facts=tuple(sections.semantic_facts),
            identity_facts=tuple(sections.identity_facts),
            comparability_facts=tuple(sections.comparability_facts),
            metrics=tuple(sections.metrics),
            formula_windows=tuple(sections.formula_windows),
            events=tuple(sections.events),
            relationships=(),
            evidence_sources=(),
            primary_passages=passages,
            context_passages=tuple(sections.context_passages),
            explanatory_passages=tuple(sections.explanatory_passages),
            counter_evidence=tuple(sections.counter_evidence),
            warnings=tuple(sections.warnings),
            conflicts=tuple(sections.conflicts),
            compatibility=tuple(sections.compatibility),
            documents=tuple(sections.documents),
            retrieval_trace=tuple(
                entry.model_copy(update={"elapsed_ms": TRACE_ELAPSED_MS_NOT_CARRIED})
                for entry in sections.retrieval_trace),
            budget=PackageBudget(
                artifact_token_estimate=estimates.artifact,
                prompt_token_estimate=estimates.prompt,
                section_counts=section_counts(sections),
                parameters=self.budget,
                caps_hit=tuple(sorted(sections.caps_hit)),
            ),
        )


# -- the invariants a trim has to preserve ---------------------------------------------------


def may_drop_last_primary(sections: PackageSections) -> bool:
    """Would the package still carry a fact for every slot the candidate anchored on?"""
    surviving = {passage.passage_id for passage in sections.primary_passages[:-1]}
    covered = {
        (fact.metric_id, fact.period_key)
        for fact in sections.facts
        if fact.passage_id and fact.passage_id in surviving
    }
    return sections.required_slots <= covered


def drop_orphaned_facts(sections: PackageSections) -> None:
    """Every fact still in the package cites a passage still in it (§13.7's Rule A).

    Called after any change to `primary_passages`. The alternative — keeping the fact and letting
    its `passage_id` dangle — produces a package that satisfies every count in §10.2 and whose
    citation chain does not close, which is the one failure §10 may not ship.
    """
    kept = {passage.passage_id for passage in sections.primary_passages}
    sections.facts = [
        fact for fact in sections.facts if fact.passage_id and fact.passage_id in kept]


def without_orphaned_disclosures(sections: PackageSections) -> list[PackagedWarning]:
    """Drop the `match_basis` disclosure of a counter-evidence row the trim removed.

    A disclosure about a passage that is no longer in the package is worse than no disclosure: it
    names a `passage_id` a reader cannot resolve and spends one of the twenty warning slots doing
    it. Recomputed on every cap rather than removed at the trim, because a trim can also happen
    through `primary_passages` and the two paths should not each have to remember this.
    """
    present = {passage.passage_id for passage in sections.counter_evidence}
    return [
        warning for warning in sections.warnings
        if warning.code not in counter.CODE_BASIS
        or any(subject in present for subject in warning.subject_ids)
    ]


def remember_document(sections: PackageSections, row: Mapping[str, Any]) -> None:
    """Keep the richest row seen for each document, for `documents[]` to be derived from.

    Richest by field count, and the reason is that the four tools return four different subsets:
    `get_fact_evidence` carries `accession`, `get_passage_context` does not, and neither carries
    `Document.title` or a `content_sha256` the node does not have. Taking the first row seen
    would make a document's `accession` depend on whether a fact or a neighbouring passage
    reached it first — two packages over one graph run differing in a field, which §10.3 forbids.
    """
    document_id = str(row.get("document_id") or "")
    if not document_id:
        return
    existing = sections.document_rows.get(document_id)
    fields = {key: row.get(key) for key in
              ("form", "document_type", "accession", "filing_date", "report_date", "source_url")}
    populated = sum(1 for value in fields.values() if value)
    if existing is None or populated > sum(1 for key, value in existing.items()
                                           if key in fields and value):
        sections.document_rows[document_id] = {"document_id": document_id, **fields}


def section_counts(sections: PackageSections) -> dict[str, int]:
    """§10's `budget.section_counts`. Every section, including the five that are always empty —
    a count that vanished when it reached zero would make an absent section unreadable."""
    return {
        "facts": len(sections.facts),
        # The three ontology-fact sections are counted from the day they exist rather than from
        # the day S4 fills them: a section the digest does not cover is a section two runs can
        # differ on silently, and `section_counts` is inside `package_content_digest`.
        "semantic_facts": len(sections.semantic_facts),
        "identity_facts": len(sections.identity_facts),
        "comparability_facts": len(sections.comparability_facts),
        "metrics": len(sections.metrics),
        "formula_windows": len(sections.formula_windows),
        "events": len(sections.events),
        "relationships": 0,
        "evidence_sources": 0,
        "primary_passages": len(sections.primary_passages),
        "context_passages": len(sections.context_passages),
        "explanatory_passages": len(sections.explanatory_passages),
        "counter_evidence": len(sections.counter_evidence),
        "warnings": len(sections.warnings),
        "conflicts": len(sections.conflicts),
        "compatibility": len(sections.compatibility),
        "documents": len(sections.documents),
        "retrieval_trace": len(sections.retrieval_trace),
    }


def dedupe(warnings: Sequence[PackagedWarning]) -> tuple[PackagedWarning, ...]:
    """One warning per `(code, subjects, detail)`.

    Two sections can legitimately discover the same thing — a slot's `single_source` and its
    conflict disclosure both walk the same canonical points — and a duplicated row would spend
    §10.2's twenty-warning budget saying one thing twice.
    """
    seen: dict[tuple[str, tuple[str, ...], str], PackagedWarning] = {}
    for warning in warnings:
        seen.setdefault((warning.code, warning.subject_ids, warning.detail), warning)
    return tuple(seen.values())


def text_or_none(row: Mapping[str, Any], key: str) -> str | None:
    """C2: an absent key and an empty value are one state, because Neo4j stores no null."""
    value = row.get(key)
    return value if isinstance(value, str) and value else None


__all__ = [
    "TRACE_ELAPSED_MS_NOT_CARRIED",
    "PackageAssembler",
    "PackageSections",
    "TokenEstimates",
    "dedupe",
    "drop_orphaned_facts",
    "may_drop_last_primary",
    "remember_document",
    "section_counts",
    "text_or_none",
    "without_orphaned_disclosures",
]
