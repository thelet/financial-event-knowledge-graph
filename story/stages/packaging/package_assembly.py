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
    SectionLedgerEntry,
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
    #: §4 S2's demoted rows: what `find_counter_evidence` produced that did not qualify as
    #: counter-evidence, each carrying the role it actually plays. Selected by
    #: `evidence_package._add_counter_evidence`, trimmed by `section_bounds.TRIM_PLAN`'s
    #: `DIAGNOSTIC_TRIM_STEP` — fourth, ahead of any supporting passage.
    diagnostic_passages: list[PackagedPassage] = field(default_factory=list)
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
    #: Every concordant source of each carried observation, as `(observation_id, passage_id,
    #: document_id)` triples (§4 S3). Written by the selection stage, which is the only thing
    #: that can compare two observations; turned into the facts' three `corroborating_*` lists by
    #: `derive_corroboration`, which subtracts whatever `facts[]` still carries after the trim.
    corroboration: dict[str, tuple[tuple[str, str, str], ...]] = field(default_factory=dict)
    #: What each section held **before** anything bound it, where the stage that bound it said
    #: so. Written by `note_available` and read by the ledger; a section absent from here was
    #: either never bound or was bound by a stage that did not report the count.
    available_counts: dict[str, int] = field(default_factory=dict)
    #: Rows this assembler dropped, by section. Separate from `available_counts` because the two
    #: are known at different times and by different code.
    dropped_counts: dict[str, int] = field(default_factory=dict)
    #: The warning codes that explain each section's drops, so the ledger's *"reason"* resolves
    #: through the same catalogue the panel renders warnings from.
    drop_reasons: dict[str, set[str]] = field(default_factory=dict)
    #: Sections a §10.2 cap already bound **before** assembly began, snapshotted from `caps_hit`
    #: so the ledger can tell them apart from the sections this assembler trims. Each gets
    #: `section_truncated` as its reason; one that also reported no count through
    #: `note_available` gets `available: None` rather than `carried` — *"4 of 4 available"* about
    #: a section that had nine is a false statement, and the `section_truncated` warning those
    #: stages raise carries the real numbers in its detail.
    upstream_caps: set[str] = field(default_factory=set)


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

        **§4 S5's refusal is raised here, and only when the package is irreducible.** A
        protected fact is never trimmed, so the only way one *"will not fit"* is that the
        package still exceeds §10.2's ceiling with every trimmable section at its floor. That is
        the state the ceiling branch below is already in, and the refusal names the facts rather
        than the section: a rejection a reviewer can dispatch, not one they have to search for.
        """
        # Snapshotted before the first trim, because `caps_hit` gains this assembler's own
        # sections as it goes and the ledger has to say *which* bound took which rows.
        sections.upstream_caps = {name for name in sections.caps_hit
                                  if name in section_counts(sections)}
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
            stranded = protected_fact_ids(sections)
            if stranded:
                # A second code rather than a longer detail on the first, because the two say
                # different things to different readers. `package_exceeds_token_ceiling` is
                # *"this package is too large"*, which a smaller budget or a shorter passage can
                # answer. This one is *"no package that carries this story's required facts fits
                # at all"* — §4 S5's one new blocking behaviour — and the ids are what makes it
                # dispatchable. Both are `REFUSE`, so §13.17's gate needs no new mechanism.
                sections.warnings.append(codes.packaged_warning(
                    codes.REQUIRED_FACT_DOES_NOT_FIT,
                    subject_ids=stranded,
                    detail=f"{len(stranded)} fact(s) §4 S5 protects are in a package that is "
                           f"{estimates.prompt} tokens against a ceiling of "
                           f"{section_bounds.MAX_TOTAL_TOKENS_CEILING} with every trimmable "
                           "section at its floor. A protected fact is never trimmed — an anchor "
                           "observation carries the story and a semantic fact carries what its "
                           "numbers mean — so this refuses rather than shipping a post whose "
                           f"numbers have no declared meaning: {', '.join(stranded[:8])}"))
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
        """Drop exactly one row, in `TRIM_PLAN`'s order, honouring each section's floor.

        Returns whether anything moved, so the caller's loop terminates on a package that cannot
        get any smaller rather than spinning. Dropping the *last* matching row of a step is
        dropping the least defensible one, because every section here is already sorted into its
        own stated drop order.

        **A primary passage is put back when its removal would strand a required slot.** That is
        the second floor, above `TRIM_FLOOR`'s "at least one", and it is where §4 S5's *"primary
        supporting passages are protected"* is enforced: dropping a primary drops the facts bound
        to it (§13.7's Rule A), and dropping the last fact of a slot the candidate anchored on
        produces a package that describes half a comparison. The trimmer then moves to the next
        step rather than giving up, so `counter_evidence` can still yield.

        Nothing in `PROTECTED_SECTIONS` is reachable from here at all — the ontology's semantic,
        identity and comparability facts are not in `TRIM_PLAN`, and a package that cannot fit
        without them refuses in `finalize` instead.
        """
        for step in section_bounds.TRIM_PLAN:
            rows = getattr(sections, step.section)
            index = section_bounds.droppable_index(rows, step)
            if index is None:
                continue
            if step.section == "primary_passages":
                if not may_drop_primary(sections, index):
                    continue
                rows.pop(index)
                drop_orphaned_facts(sections)
            else:
                rows.pop(index)
            note_drop(sections, step.section, codes.TOKEN_BUDGET_TRIMMED)
            sections.caps_hit.add(step.name)
            sections.caps_hit.add(step.section)
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
            note_drop(sections, "warnings", codes.SECTION_TRUNCATED, count=dropped)
        sections.warnings = list(warnings)
        trace, dropped_trace = section_bounds.truncate(
            sections.retrieval_trace, section_bounds.cap_for(self.budget, "retrieval_trace"))
        if dropped_trace:
            sections.caps_hit.add("retrieval_trace")
            note_drop(sections, "retrieval_trace", codes.SECTION_TRUNCATED, count=dropped_trace)
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
                        *sections.explanatory_passages, *sections.counter_evidence,
                        *sections.diagnostic_passages):
            if passage.document_id and passage.document_id not in cited:
                cited.append(passage.document_id)
        for fact in sections.facts:
            if fact.document_id and fact.document_id not in cited:
                cited.append(fact.document_id)
        cap = section_bounds.cap_for(self.budget, "documents")
        if len(cited) > cap:
            sections.caps_hit.add("documents")
            # Assigned rather than accumulated: `documents[]` is derived and rebuilt from the
            # surviving passages on every trim, so what the previous rebuild dropped is not a
            # drop this one made.
            sections.dropped_counts["documents"] = len(cited) - cap
            sections.drop_reasons.setdefault("documents", set()).add(codes.SECTION_TRUNCATED)
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
        facts = derive_corroboration(sections)
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
            diagnostic_passages=tuple(sections.diagnostic_passages),
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
                section_ledger=section_ledger(sections),
                parameters=self.budget,
                caps_hit=tuple(sorted(sections.caps_hit)),
            ),
        )


# -- the invariants a trim has to preserve ---------------------------------------------------


def may_drop_primary(sections: PackageSections, index: int) -> bool:
    """Would the package still carry a fact for every slot the candidate anchored on?

    Takes the index rather than assuming the tail, because §4 S5's trim plan can reach past a
    corroborating row to the supporting one behind it. *"Which primary is protected"* is a
    property of the facts bound to it and of nothing else, so the answer must be asked about the
    row that is actually going.
    """
    surviving = {passage.passage_id
                 for position, passage in enumerate(sections.primary_passages)
                 if position != index}
    covered = {
        (fact.metric_id, fact.period_key)
        for fact in sections.facts
        if fact.passage_id and fact.passage_id in surviving
    }
    return sections.required_slots <= covered


def may_drop_last_primary(sections: PackageSections) -> bool:
    """`may_drop_primary` about the tail — the question every caller asked before §4 S5."""
    return may_drop_primary(sections, len(sections.primary_passages) - 1)


def protected_fact_ids(sections: PackageSections) -> tuple[str, ...]:
    """Every fact §4 S5 protects, by id, sorted — what a refusal names.

    Two families, and both are required for the same reason. A fact on one of the candidate's
    anchor slots *is* the story: §6.11 digests `anchor_observation_ids` into the `candidate_id`,
    so a package without one claims evidence it does not carry. A semantic, identity or
    comparability fact is what the story's numbers **mean**: the ontology declares the metric's
    definition, unit, scale and comparison rules, and a post written without them states a number
    whose declared meaning never reached the model.

    Derived facts need no separate marker: a derived fact for a required slot is required by the
    slot rule, and one for any other slot is not required at all.
    """
    anchored = {
        fact.observation_id for fact in sections.facts
        if (fact.metric_id, fact.period_key) in sections.required_slots
    }
    ontology = {fact.fact_id for fact in (*sections.semantic_facts, *sections.identity_facts,
                                          *sections.comparability_facts)}
    return tuple(sorted(anchored | ontology))


def note_available(sections: PackageSections, section: str, available: int) -> None:
    """Record what a section held before the stage that bound it took rows away.

    The selection stage applies §10.2's caps before the assembler ever sees the sections, and
    only it knows what it had — `facts` is capped against `len(plans)`, `primary_passages`
    against `len(order)`, `counter_evidence` against `len(built)`. Without this the ledger can
    only report *"4 carried"* and must report `available` as unknown, because *"4 of 4"* about a
    section that had nine is a false statement and §4 S6 renders that number to a reader.
    """
    sections.available_counts[section] = max(
        available, sections.available_counts.get(section, 0))


def note_drop(sections: PackageSections, section: str, reason: str, *, count: int = 1) -> None:
    """One section gave up `count` rows, for a reason named by a warning code."""
    sections.dropped_counts[section] = sections.dropped_counts.get(section, 0) + count
    sections.drop_reasons.setdefault(section, set()).add(reason)


def section_ledger(sections: PackageSections) -> tuple[SectionLedgerEntry, ...]:
    """§4 S5's available / carried / dropped / reason, one row per section.

    Built from `section_counts` rather than from a second list of section names, so a section
    added to §10 cannot appear in one and not the other.

    `required_dropped` is **measured, not asserted**: `facts` reports whether any slot the
    candidate anchored on has no surviving fact, and a protected section reports whether it lost
    a row at all. Both are false on every package the assembler can produce — that is what the
    protection and the refusal are for — and the point of measuring is that the panel's *"no
    required fact was dropped"* is then a reading rather than a claim.
    """
    counts = section_counts(sections)
    rows: list[SectionLedgerEntry] = []
    for name in sorted(counts):
        carried = counts[name]
        dropped = sections.dropped_counts.get(name, 0)
        noted = sections.available_counts.get(name)
        if noted is not None:
            available: int | None = max(noted, carried + dropped)
        elif name in sections.upstream_caps:
            available = None
        else:
            available = carried + dropped
        reasons = set(sections.drop_reasons.get(name, ()))
        if name in sections.upstream_caps:
            reasons.add(codes.SECTION_TRUNCATED)
        rows.append(SectionLedgerEntry(
            section=name,
            available=available,
            carried=carried,
            dropped=dropped,
            reasons=tuple(sorted(reasons)),
            protected=name in section_bounds.PROTECTED_SECTIONS,
            required_dropped=required_dropped(sections, name, dropped=dropped),
        ))
    return tuple(rows)


def required_dropped(sections: PackageSections, name: str, *, dropped: int) -> bool:
    """Did this section lose something §4 S5 protects?"""
    if name == "facts":
        covered = {(fact.metric_id, fact.period_key) for fact in sections.facts}
        return bool(sections.required_slots - covered)
    return dropped > 0 and name in section_bounds.PROTECTED_SECTIONS


def derive_corroboration(sections: PackageSections) -> tuple[PackagedFact, ...]:
    """§4 S3's three `corroborating_*` lists — **derived, like `documents[]`, on every rebuild.**

    §1.1 measured the defect: `housing_inventory_homes` 2023-03-31 holds six observations, all
    `6261.0 homes`, in six documents; canonicalisation keeps one and §10 carried one passage, so
    five concordant sources were discarded and the best-corroborated fact in the candidate
    arrived looking thinly sourced. The selection stage puts every concordant source on
    `sections.corroboration`; this subtracts the ones `facts[]` is still carrying and turns the
    rest into the three id lists.

    **The subtraction is why this is derived rather than stamped.** §10.2's round-robin used to
    deal a second *concordant* reading of one slot into `facts[]`, and that reading was then in
    the package with its own passage — strictly more than an id — so listing it as corroboration
    as well would have spent the budget twice on one source and had each of two rows claim the
    other as its own second source. §4 S3a collapses concordant readings in the selection stage
    instead, so on this corpus the subtraction now removes **nothing** *(re-measured
    2026-08-05)*. It stays: a reading the collapse declines to fold, and any reading the trim
    later removes, both have to be re-answered, and which readings survive is not known until
    the token trim has finished — the trim rebuilds the package once per dropped row, so the
    answer is recomputed each time, the same reason `derive_documents` rebuilds rather than
    filters.

    **Measured, because the obvious placement is wrong in both directions.** Stamped once in the
    selection stage before the cap, every list on the three §6.3 spikes came out *empty*:
    `facts[]` still held every reading the cap would later drop. Stamped with no subtraction at
    all, the ids cost 487–876 tokens per package and pushed F1 and F3 from three primary
    passages to two — paying a whole table of filed text to repeat, as an id, a reading the
    package was already carrying in full.
    """
    carried = {fact.observation_id for fact in sections.facts}
    rows: list[PackagedFact] = []
    for fact in sections.facts:
        # `model_copy` does not re-validate, so it does not re-mint `evidence_handle` — which is
        # correct here and only here: the three corroboration lists are ids of readings the
        # package discarded, and the handle names the passage and cell of the reading it kept.
        # A copy that changed `passage_id` or `cell` would have to go back through validation.
        sources = [
            source for source in sections.corroboration.get(fact.observation_id, ())
            if source[0] not in carried
        ]
        rows.append(fact.model_copy(update={
            "corroborating_observation_ids": tuple(sorted({source[0] for source in sources})),
            "corroborating_passage_ids": tuple(sorted({s[1] for s in sources if s[1]})),
            "corroborating_document_ids": tuple(sorted({s[2] for s in sources if s[2]})),
        }))
    return tuple(rows)


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
        # Counted from the day the section exists, for the same reason the three ontology-fact
        # sections are: §4 S2 moves rows *out* of `counter_evidence` and a reader comparing two
        # runs must be able to see where they went.
        "diagnostic_passages": len(sections.diagnostic_passages),
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
    "derive_corroboration",
    "drop_orphaned_facts",
    "may_drop_last_primary",
    "may_drop_primary",
    "note_available",
    "note_drop",
    "protected_fact_ids",
    "remember_document",
    "required_dropped",
    "section_counts",
    "section_ledger",
    "text_or_none",
    "without_orphaned_disclosures",
]
