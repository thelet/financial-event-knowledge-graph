"""Every code the evidence package may carry in `warnings[]`, with the severity it carries it at.

Responsibility: the vocabulary, and nothing else. No retrieval, no assembly, no bounds — this
module is what a test imports to say *"that package should have disclosed this"*, and what the
three other packaging modules import to say it. A code invented at a call site is a code no
renderer knows about and no verifier can require, which is the failure §10.1 exists to prevent:
its whole claim is that the warnings *"are all computable today"*, and a computable warning
that nobody named is not one.

**Two families, and the split matters.** The first eleven are §10.1's own list — properties of
the *evidence*: an unpreferred lane, a declared ambiguity, an unresolved entity, an undated
event, a population disagreement, a formula boundary, a single source, a conflicted slot. The
rest are properties of the *packaging*: a read that was truncated, a search pool that capped, a
counter-evidence row that matched at document grain, a section that hit its cap, a token budget
that trimmed. §10.1 did not list the second family because the first draft of §10 assumed
retrieval always answered completely; AR2's b1 measured what that costs — *"a single un-paged
`get_metric_history` yields 93 candidates and zero refusals"* — and the rule that came out of it
is that an incomplete read is never silence.

**Severity is a property of the code, not of the call site.** `Severity.REFUSE` here means
§13.17's gate must stop a draft built on this package; `WARN` means it must be acknowledged;
`ANNOTATE` means it must be rendered beside the claim. Two call sites raising one code at two
severities would make the gate depend on which one ran, so the mapping lives here and
`packaged_warning` is the only constructor.

**Kind is a property of the code too, and the two families above are exactly the split.** A
warning either qualifies a claim — an unpreferred lane, a declared ambiguity, a population
wording, a single source — or it records how this package was built. §10.1 never drew the line,
and the first end-to-end demo run showed what that costs: the plan copied `token_budget_trimmed`,
`section_truncated`, `subject_identity_not_read_from_graph`, `evidence_sources_absent_in_v1` and
`relationships_unavailable_in_v1` into `required_warnings`, and §13 then demanded five sentences
of build provenance from an investor post *(measured 2026-08-04 on the recorded Qwen run: five
of the nine blocking findings)*. `KIND_OF` is that line, drawn once, here. Build provenance still
travels on the package, still reaches the evidence panel and the manifest, and is still capped
and ordered by severity — it simply demands no sentence. The disclosure rule itself is untouched:
a `CLAIM_QUALIFYING` code the post leaves unsaid still refuses the draft.

The test for which family a code belongs to is *"does a reader need it to read the sentence
correctly?"*, not *"is it important?"*. `retrieval_truncated` and `search_pool_capped` are
important and are provenance: they say the read was partial, which is a fact about the package
rather than about Opendoor, and §13.14 already refuses outright the claims — absence, uniqueness
— that a partial read is what would make false.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from story.core.models import PackagedWarning, Severity, WarningKind

# -- §10.1's own list ---------------------------------------------------------------------

#: A used fact whose `validation_state` is `warned`. 185 observations carry
#: `unpreferred_source_lane` in this run (§10.1), and the state is on the node rather than
#: derivable, so this is a copy of a measurement and not a judgement.
UNPREFERRED_SOURCE_LANE = "unpreferred_source_lane"

#: The metric of a used fact declares an ambiguity in the ontology. §6.6 D8's requirement,
#: relocated: `CanonicalPoint` carries no ambiguity codes, so the candidate cannot surface one
#: and §10 packages them from the observations and the ontology instead (S3's hand-off).
METRIC_AMBIGUITY_DECLARED = "metric_ambiguity_declared"

#: An entity named by the package is not resolved to a key. §13.11 refuses a draft that renders
#: one as a name.
ENTITY_UNRESOLVED = "entity_unresolved"

#: A used event carries no `occurred_on`. All three `executive_change` events are in this state
#: and §13.8 refuses an asserted effective date on any of them.
EVENT_DATE_ABSENT = "event_date_absent"

#: A used event's `validation_state` is not clean.
EVENT_REVIEW_FLAG = "event_review_flag"

#: Two compared facts sit under different population wordings. The 120-day metric has three
#: (§6.6 D8), and comparing across them is a comparison of two denominators.
POPULATION_DEFINITION_DIFFERS = "population_definition_differs"

#: The candidate's anchor dates resolve to two different formula versions of one metric.
FORMULA_WINDOW_BOUNDARY_CROSSED = "formula_window_boundary_crossed"

#: A used slot is corroborated by exactly one document. §6.10's corroboration term reads the
#: same number; this is the disclosure half.
SINGLE_SOURCE = "single_source"

#: A used slot held more than one reading, with the classification §6.1 gave it.
FACT_CONFLICT_DISCLOSED = "fact_conflict_disclosed"

#: A used slot's canonical status is `conflict` — it exists and emits no value.
SLOT_UNRESOLVED = "slot_unresolved"

#: A used point carried a canonicalisation warning of its own (`lane_defect`,
#: `minority_reading_present`, `slot_unit_disagreement`, …). Copied through rather than
#: re-derived, because the canonical layer is where those were measured.
CANONICAL_POINT_WARNING = "canonical_point_warning"

# -- what packaging itself discovers ------------------------------------------------------

#: The observation set handed to the builder was not proved complete — the caller reported a
#: metric it could not read, or paging that stalled. **REFUSE**: AR2's b1 is exactly this state
#: passing as complete, and it cost 20 real candidates and three wrong `n_docs`.
OBSERVATION_LOAD_INCOMPLETE = "observation_load_incomplete"

#: A §9 tool the package used returned `truncated=True`. Never silence (the S5 ruling).
RETRIEVAL_TRUNCATED = "retrieval_truncated"

#: `search_passages` ranked a pool that hit `{limit: 500}` **before** the document filter, so a
#: filtered query can be starved — measured, `["the"]` + `shareholder_letter` returns 9 rows
#: where the unbounded pool returns 26. R2b discloses; packaging decides (§7's ruling).
SEARCH_POOL_CAPPED = "search_pool_capped"

#: The capped pool starved a filtered query whose completeness the package needed, so the
#: explanatory section was dropped rather than shipped short. **REFUSE** of the section, not of
#: the package: the rest of the evidence is unaffected.
SEARCH_POOL_STARVED = "search_pool_starved"

#: A counter-evidence row is associated with a used fact **at document grain** and not at
#: passage grain. The refusals sit in neighbouring tables of the same filing; passage-grain
#: joins return zero rows for the 2022Q3 metrics *(measured live)*. §13.14 is waiting for a
#: draft that reads this as a direct contradiction, so it is disclosed on every row.
COUNTER_EVIDENCE_SAME_DOCUMENT = "counter_evidence_same_document"

#: A counter-evidence row whose issue was found in a passage a used fact is itself bound to.
#: Direct, and still disclosed — because `match_basis` has to be readable off **every** row for
#: a reader to know that the others are not. §10's `counter_evidence[]` is typed as
#: `PackagedPassage`, which S5 does not own and which has no field for a basis, so the basis
#: travels as this pair of codes; `counter_evidence.match_basis_of` is the reader.
COUNTER_EVIDENCE_SAME_PASSAGE = "counter_evidence_same_passage"

#: `find_counter_evidence` answered `Unavailable` — there was nowhere to look (D5). Distinct
#: from an empty `Ok`, which means the filings refused nothing.
COUNTER_EVIDENCE_UNAVAILABLE = "counter_evidence_unavailable"

#: A §10.2 cap bound a section and rows the builder had were not shipped.
SECTION_TRUNCATED = "section_truncated"

#: The token estimate exceeded §10.2's 5,000 and rows were dropped to fit.
TOKEN_BUDGET_TRIMMED = "token_budget_trimmed"

#: The irreducible package still exceeds §10.2's 6,000-token ceiling. **REFUSE**: the local
#: runtime is `-c 8192` and a package over the ceiling does not leave room for the system
#: prompt, so the draft built on it would be written from a truncated universe.
PACKAGE_EXCEEDS_TOKEN_CEILING = "package_exceeds_token_ceiling"

#: No §9 tool reads `:Entity` — `cypher.py` excludes it because `opendoor` has degree ≥ 2,704 —
#: so `entity_text`, `labels` and `resolved` are the caller's or are inferred from the
#: observations' own `subject_entity_id`. Stated rather than assumed benign.
SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH = "subject_identity_not_read_from_graph"

#: A fact's `get_fact_evidence` call returned nothing, or returned no passage — the citation
#: chain §13.7 checks does not close. **REFUSE**: the fact is dropped and the code records why.
EVIDENCE_CHAIN_INCOMPLETE = "evidence_chain_incomplete"

#: `relationships[]` is empty because S1 shipped no §9 tool that returns a relationship —
#: `find_related_entities` is in §9's table and is not one of the nine implemented. Recorded so
#: an empty section reads as a gap in the retrieval layer rather than as a fact about Opendoor.
RELATIONSHIPS_UNAVAILABLE_IN_V1 = "relationships_unavailable_in_v1"

#: `evidence_sources[]` is empty because zero `:EvidenceSource` nodes exist (§13.7.2). Recorded
#: for the same reason: the day the XBRL lane emits one, the absence stops being structural.
EVIDENCE_SOURCES_ABSENT_IN_V1 = "evidence_sources_absent_in_v1"

#: A comparison the candidate itself made is refused by §6.9. The candidate exists, so the
#: detector permitted it under some claim kind; a refusal here means the packager's claim kind
#: is stricter, and the planner must see which.
COMPARISON_REFUSED = "comparison_refused"

#: A comparison the candidate made is permitted **and must be disclosed** — R9's channel.
COMPARISON_WARNED = "comparison_warned"

#: The candidate itself carried warnings from its detector. Carried through so the planner sees
#: what the detector already knew — `relative_change_across_zero` is the sharpest case (R4a).
CANDIDATE_WARNING = "candidate_warning"


#: Every code above, with the severity §13.17's gate reads. A code absent from this mapping
#: cannot be constructed, which is what makes "no warning is invented at a call site" a
#: mechanical property rather than a convention.
SEVERITY_OF: Mapping[str, Severity] = {
    UNPREFERRED_SOURCE_LANE: Severity.WARN,
    METRIC_AMBIGUITY_DECLARED: Severity.ANNOTATE,
    ENTITY_UNRESOLVED: Severity.REFUSE,
    EVENT_DATE_ABSENT: Severity.WARN,
    EVENT_REVIEW_FLAG: Severity.WARN,
    POPULATION_DEFINITION_DIFFERS: Severity.WARN,
    FORMULA_WINDOW_BOUNDARY_CROSSED: Severity.WARN,
    SINGLE_SOURCE: Severity.ANNOTATE,
    FACT_CONFLICT_DISCLOSED: Severity.WARN,
    SLOT_UNRESOLVED: Severity.WARN,
    CANONICAL_POINT_WARNING: Severity.ANNOTATE,
    OBSERVATION_LOAD_INCOMPLETE: Severity.REFUSE,
    RETRIEVAL_TRUNCATED: Severity.WARN,
    SEARCH_POOL_CAPPED: Severity.WARN,
    SEARCH_POOL_STARVED: Severity.WARN,
    COUNTER_EVIDENCE_SAME_DOCUMENT: Severity.ANNOTATE,
    COUNTER_EVIDENCE_SAME_PASSAGE: Severity.ANNOTATE,
    COUNTER_EVIDENCE_UNAVAILABLE: Severity.WARN,
    SECTION_TRUNCATED: Severity.ANNOTATE,
    TOKEN_BUDGET_TRIMMED: Severity.WARN,
    PACKAGE_EXCEEDS_TOKEN_CEILING: Severity.REFUSE,
    SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH: Severity.ANNOTATE,
    EVIDENCE_CHAIN_INCOMPLETE: Severity.REFUSE,
    RELATIONSHIPS_UNAVAILABLE_IN_V1: Severity.ADVISORY,
    EVIDENCE_SOURCES_ABSENT_IN_V1: Severity.ADVISORY,
    COMPARISON_REFUSED: Severity.REFUSE,
    COMPARISON_WARNED: Severity.WARN,
    CANDIDATE_WARNING: Severity.WARN,
}

#: Which audience each code is for. **Total over `SEVERITY_OF` and checked below**, so a code
#: added without a decision is a construction error rather than a silent default — the default
#: on `PackagedWarning` protects a hand-built warning, not this table.
#:
#: The provenance side is short and every member names a property of the *build*: an incomplete
#: or capped read, a section or a budget that trimmed, a lane that emits nothing in V1, an
#: identity the §9 tools do not read. Everything else qualifies a claim, including the four
#: `counter_evidence_*` codes — where a contradicting row sits relative to the cited cell is
#: something a reader needs in order to read the sentence — and `candidate_warning`, which
#: carries the detector's own caveats about the comparison the post is built on.
KIND_OF: Mapping[str, WarningKind] = {
    UNPREFERRED_SOURCE_LANE: WarningKind.CLAIM_QUALIFYING,
    METRIC_AMBIGUITY_DECLARED: WarningKind.CLAIM_QUALIFYING,
    ENTITY_UNRESOLVED: WarningKind.CLAIM_QUALIFYING,
    EVENT_DATE_ABSENT: WarningKind.CLAIM_QUALIFYING,
    EVENT_REVIEW_FLAG: WarningKind.CLAIM_QUALIFYING,
    POPULATION_DEFINITION_DIFFERS: WarningKind.CLAIM_QUALIFYING,
    FORMULA_WINDOW_BOUNDARY_CROSSED: WarningKind.CLAIM_QUALIFYING,
    SINGLE_SOURCE: WarningKind.CLAIM_QUALIFYING,
    FACT_CONFLICT_DISCLOSED: WarningKind.CLAIM_QUALIFYING,
    SLOT_UNRESOLVED: WarningKind.CLAIM_QUALIFYING,
    CANONICAL_POINT_WARNING: WarningKind.CLAIM_QUALIFYING,
    COUNTER_EVIDENCE_SAME_DOCUMENT: WarningKind.CLAIM_QUALIFYING,
    COUNTER_EVIDENCE_SAME_PASSAGE: WarningKind.CLAIM_QUALIFYING,
    COMPARISON_REFUSED: WarningKind.CLAIM_QUALIFYING,
    COMPARISON_WARNED: WarningKind.CLAIM_QUALIFYING,
    CANDIDATE_WARNING: WarningKind.CLAIM_QUALIFYING,
    OBSERVATION_LOAD_INCOMPLETE: WarningKind.BUILD_PROVENANCE,
    RETRIEVAL_TRUNCATED: WarningKind.BUILD_PROVENANCE,
    SEARCH_POOL_CAPPED: WarningKind.BUILD_PROVENANCE,
    SEARCH_POOL_STARVED: WarningKind.BUILD_PROVENANCE,
    COUNTER_EVIDENCE_UNAVAILABLE: WarningKind.BUILD_PROVENANCE,
    SECTION_TRUNCATED: WarningKind.BUILD_PROVENANCE,
    TOKEN_BUDGET_TRIMMED: WarningKind.BUILD_PROVENANCE,
    PACKAGE_EXCEEDS_TOKEN_CEILING: WarningKind.BUILD_PROVENANCE,
    SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH: WarningKind.BUILD_PROVENANCE,
    EVIDENCE_CHAIN_INCOMPLETE: WarningKind.BUILD_PROVENANCE,
    RELATIONSHIPS_UNAVAILABLE_IN_V1: WarningKind.BUILD_PROVENANCE,
    EVIDENCE_SOURCES_ABSENT_IN_V1: WarningKind.BUILD_PROVENANCE,
}

if set(KIND_OF) != set(SEVERITY_OF):  # pragma: no cover - a source edit, not a state
    raise ValueError(
        "every declared warning code must declare a kind: "
        f"{sorted(set(SEVERITY_OF) ^ set(KIND_OF))} differ between SEVERITY_OF and KIND_OF")

#: Most severe first. `warnings[]` is capped at 20 (§10.2) and this is the order a drop obeys,
#: so the rule D6 established for counter-evidence — *"nothing dropped can outrank anything
#: returned"* — holds for warnings too. A cap that dropped a `REFUSE` while keeping an
#: `ADVISORY` would hide the one thing §13.17 acts on.
SEVERITY_RANK: Mapping[Severity, int] = {
    Severity.REFUSE: 0,
    Severity.WARN: 1,
    Severity.ANNOTATE: 2,
    Severity.ADVISORY: 3,
}


class UnknownWarningCode(ValueError):
    """A code `SEVERITY_OF` does not declare. Its own type so a test can require the refusal."""


def packaged_warning(
    code: str, *, subject_ids: Sequence[str] = (), detail: str = ""
) -> PackagedWarning:
    """One warning at its declared severity, with the subjects it is about.

    `subject_ids` is sorted here rather than at each call site: two packages that disclosed the
    same warning about the same three facts in two visit orders would produce two
    `package_content_digest`s for one universe, and §10.3 requires a rebuild to reproduce the
    digest.
    """
    severity = SEVERITY_OF.get(code)
    if severity is None:
        raise UnknownWarningCode(
            f"{code!r} is not a declared package warning; §10.1 requires every warning to be "
            f"computable and named, and a code invented at a call site is renderable by nothing. "
            f"Declared codes: {', '.join(sorted(SEVERITY_OF))}")
    return PackagedWarning(
        code=code,
        severity=severity,
        kind=KIND_OF[code],
        subject_ids=tuple(sorted(set(subject_ids))),
        detail=detail,
    )


def claim_qualifying(warnings: Sequence[PackagedWarning]) -> tuple[PackagedWarning, ...]:
    """The warnings a post has to *say*, as opposed to the ones it has to carry.

    A helper for the same reason `blocking` is one: *"which warnings demand a sentence"* is a
    property of the vocabulary above and must move with it. The generation stage cannot call
    this — a stage may not import another stage — and reads `PackagedWarning.kind` off the
    package instead, which is why the kind is a field and not a lookup at the far end.
    """
    return tuple(w for w in warnings if w.kind is WarningKind.CLAIM_QUALIFYING)


def warning_sort_key(warning: PackagedWarning) -> tuple[int, str, str]:
    """Most severe first, then by code, then by subject. Total, so a cap is deterministic."""
    return (SEVERITY_RANK[warning.severity], warning.code, ",".join(warning.subject_ids))


def blocking(warnings: Sequence[PackagedWarning]) -> tuple[PackagedWarning, ...]:
    """The `REFUSE`-severity warnings, which §13.17's gate must act on.

    A helper rather than a filter written at four call sites, because "which warnings stop a
    draft" is a property of the vocabulary above and should move with it.
    """
    return tuple(w for w in warnings if w.severity is Severity.REFUSE)


__all__ = [
    "CANDIDATE_WARNING",
    "CANONICAL_POINT_WARNING",
    "COMPARISON_REFUSED",
    "COMPARISON_WARNED",
    "COUNTER_EVIDENCE_SAME_DOCUMENT",
    "COUNTER_EVIDENCE_SAME_PASSAGE",
    "COUNTER_EVIDENCE_UNAVAILABLE",
    "ENTITY_UNRESOLVED",
    "EVENT_DATE_ABSENT",
    "EVENT_REVIEW_FLAG",
    "EVIDENCE_CHAIN_INCOMPLETE",
    "EVIDENCE_SOURCES_ABSENT_IN_V1",
    "FACT_CONFLICT_DISCLOSED",
    "FORMULA_WINDOW_BOUNDARY_CROSSED",
    "KIND_OF",
    "METRIC_AMBIGUITY_DECLARED",
    "OBSERVATION_LOAD_INCOMPLETE",
    "PACKAGE_EXCEEDS_TOKEN_CEILING",
    "POPULATION_DEFINITION_DIFFERS",
    "RELATIONSHIPS_UNAVAILABLE_IN_V1",
    "RETRIEVAL_TRUNCATED",
    "SEARCH_POOL_CAPPED",
    "SEARCH_POOL_STARVED",
    "SECTION_TRUNCATED",
    "SEVERITY_OF",
    "SEVERITY_RANK",
    "SINGLE_SOURCE",
    "SLOT_UNRESOLVED",
    "SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH",
    "TOKEN_BUDGET_TRIMMED",
    "UNPREFERRED_SOURCE_LANE",
    "UnknownWarningCode",
    "blocking",
    "claim_qualifying",
    "packaged_warning",
    "warning_sort_key",
]
