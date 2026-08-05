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

**§4 S5 adds five categories, and they are a *refinement* of that split rather than a third
axis.** Two independent tables over one code set can disagree, and the disagreement would be
silent: a code filed as `capability_limitation` and `CLAIM_QUALIFYING` would demand a sentence
about a tool the retrieval layer does not have. So `CATEGORY_OF` is the only per-code decision
and `KIND_OF` is *derived* from it through `KIND_OF_CATEGORY`, which is total over
`WarningCategory` and pins each category to one kind. The refinement changes no code's kind —
`tests/story/test_story_warning_taxonomy.py` pins all twenty-nine — so the planner's
`claim_qualifying_warnings` filter and §13's disclosure check behave exactly as they did.

The three reclassifications §4 S5 requires are reclassifications *within* provenance, which is
why the kinds could stay put: `subject_identity_not_read_from_graph`,
`relationships_unavailable_in_v1` and `evidence_sources_absent_in_v1` were already
`BUILD_PROVENANCE`, and were already indistinguishable from a section the budget trimmed. They
are not partial reads. Each is a thing V1 cannot do at all, and each fires on **every** package:
`:Entity` is off `cypher.py`'s allowlist because `opendoor` has degree ≥ 2,704, no §9 tool
returns a relationship, and the corpus contains **zero** `:EvidenceSource` nodes (measured
2026-08-03, §13.7.2). A permanent, universal, structural absence rendered as a warning about
*this* package is a reader's cue to distrust *this* evidence, and there is nothing here to
distrust. Relabelled, and still carried, still severity-ordered, still rendered.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from story.core.models import PackagedWarning, Severity, WarningCategory, WarningKind

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

#: `SINGLE_SOURCE` from the other side: two or more filings state this slot identically, and
#: `facts[]` carries **one** canonical row for them (§4 S3). The readings that were folded in are
#: named on that row's `corroborating_observation_ids`, so nothing is lost — but `facts: 12
#: selected, 2 carried` needs a reason a reader can resolve, and *"the section was truncated"*
#: would be the wrong one: a concordant second reading is not a row the budget took away.
CONCORDANT_READINGS_COLLAPSED = "concordant_readings_collapsed"

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

#: A fact §4 S5 protects — a candidate anchor observation, a required derived fact, or one of
#: the ontology's semantic, identity and comparability declarations — is in a package that
#: cannot be brought under the ceiling with every trimmable section at its floor. **REFUSE**,
#: and it is §4 S5's one new blocking behaviour: *"a required semantic or anchor fact that will
#: not fit is a package refusal, not a warning"*.
#:
#: The alternative was a warning and a smaller package, and it is not honest. A post written
#: without its metric's definition is a post whose numbers have no declared meaning: the reader
#: sees `adjusted_gross_margin −12.6%` and the model was never told what the ontology says that
#: measures, over which population, under which formula version. Trimming the definition costs
#: the post nothing visible and costs the claim everything, which is precisely the failure mode
#: a warning cannot repair. `subject_ids` names the protected facts, so a refusal is
#: dispatchable rather than a search.
#:
#: Raised through `packaged_warning` at `REFUSE`, following `package_exceeds_token_ceiling`
#: rather than inventing a second refusal channel: §13.17's gate already stops a draft built on
#: a package carrying any `REFUSE`, `blocking()` already collects them, and an exception here
#: would be a refusal no evidence panel could render and no manifest could record.
REQUIRED_FACT_DOES_NOT_FIT = "required_fact_does_not_fit"

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
    CONCORDANT_READINGS_COLLAPSED: Severity.ANNOTATE,
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
    REQUIRED_FACT_DOES_NOT_FIT: Severity.REFUSE,
    SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH: Severity.ANNOTATE,
    EVIDENCE_CHAIN_INCOMPLETE: Severity.REFUSE,
    RELATIONSHIPS_UNAVAILABLE_IN_V1: Severity.ADVISORY,
    EVIDENCE_SOURCES_ABSENT_IN_V1: Severity.ADVISORY,
    COMPARISON_REFUSED: Severity.REFUSE,
    COMPARISON_WARNED: Severity.WARN,
    CANDIDATE_WARNING: Severity.WARN,
}

#: What each code is *about* (§4 S5). **Total over `SEVERITY_OF` and checked below**, so a code
#: added without a decision is a construction error rather than a silent default — the default
#: on `PackagedWarning` protects a hand-built warning, not this table.
#:
#: The line between the first three is *where the problem is*, and it is drawn deliberately.
#: `FACT_QUALITY_WARNING` is a fact that came out intact and whose standing is qualified — one
#: document behind it, an unpreferred lane, a declared ambiguity, a population wording, a
#: comparability rule the candidate crossed. `EXTRACTION_ISSUE` is a fact the pipeline could not
#: finish: an entity it could not resolve, an event with no date, a slot that holds no value, a
#: canonicalisation warning. `SUBSTANTIVE_COUNTER_EVIDENCE` holds the two codes that ride on a
#: `counter_evidence[]` row and state the basis it was matched on — the family §4 S2 narrows,
#: and `counter_evidence_same_document` is the weaker basis *within* it rather than a different
#: kind of thing.
#:
#: `RETRIEVAL_WARNING` is every way the model's universe came out smaller than the corpus: a
#: query bound, a capped pool, a section cap, a token budget, an evidence chain that would not
#: close, a counter-evidence lookup with nowhere to look. `CAPABILITY_LIMITATION` is the three
#: §4 S5 names plus nothing else — see the module docstring for why a permanent structural
#: absence is not a short read.
CATEGORY_OF: Mapping[str, WarningCategory] = {
    UNPREFERRED_SOURCE_LANE: WarningCategory.FACT_QUALITY_WARNING,
    METRIC_AMBIGUITY_DECLARED: WarningCategory.FACT_QUALITY_WARNING,
    POPULATION_DEFINITION_DIFFERS: WarningCategory.FACT_QUALITY_WARNING,
    FORMULA_WINDOW_BOUNDARY_CROSSED: WarningCategory.FACT_QUALITY_WARNING,
    SINGLE_SOURCE: WarningCategory.FACT_QUALITY_WARNING,
    FACT_CONFLICT_DISCLOSED: WarningCategory.FACT_QUALITY_WARNING,
    # A statement about the *standing* of a fact that came out intact, which is what this
    # category is, and the exact mirror of `single_source` beside it: one says the number rests
    # on one filing, the other that it rests on several which agree. It is deliberately **not**
    # a `RETRIEVAL_WARNING` — nothing came out smaller than the corpus. Every reading is in the
    # package; five of six travel as ids on the row that carries the sixth, which §4 S3 argues
    # is more evidence than five duplicate rows, not less.
    CONCORDANT_READINGS_COLLAPSED: WarningCategory.FACT_QUALITY_WARNING,
    COMPARISON_REFUSED: WarningCategory.FACT_QUALITY_WARNING,
    COMPARISON_WARNED: WarningCategory.FACT_QUALITY_WARNING,
    CANDIDATE_WARNING: WarningCategory.FACT_QUALITY_WARNING,
    ENTITY_UNRESOLVED: WarningCategory.EXTRACTION_ISSUE,
    EVENT_DATE_ABSENT: WarningCategory.EXTRACTION_ISSUE,
    EVENT_REVIEW_FLAG: WarningCategory.EXTRACTION_ISSUE,
    SLOT_UNRESOLVED: WarningCategory.EXTRACTION_ISSUE,
    CANONICAL_POINT_WARNING: WarningCategory.EXTRACTION_ISSUE,
    COUNTER_EVIDENCE_SAME_DOCUMENT: WarningCategory.SUBSTANTIVE_COUNTER_EVIDENCE,
    COUNTER_EVIDENCE_SAME_PASSAGE: WarningCategory.SUBSTANTIVE_COUNTER_EVIDENCE,
    OBSERVATION_LOAD_INCOMPLETE: WarningCategory.RETRIEVAL_WARNING,
    RETRIEVAL_TRUNCATED: WarningCategory.RETRIEVAL_WARNING,
    SEARCH_POOL_CAPPED: WarningCategory.RETRIEVAL_WARNING,
    SEARCH_POOL_STARVED: WarningCategory.RETRIEVAL_WARNING,
    COUNTER_EVIDENCE_UNAVAILABLE: WarningCategory.RETRIEVAL_WARNING,
    EVIDENCE_CHAIN_INCOMPLETE: WarningCategory.RETRIEVAL_WARNING,
    SECTION_TRUNCATED: WarningCategory.RETRIEVAL_WARNING,
    TOKEN_BUDGET_TRIMMED: WarningCategory.RETRIEVAL_WARNING,
    PACKAGE_EXCEEDS_TOKEN_CEILING: WarningCategory.RETRIEVAL_WARNING,
    REQUIRED_FACT_DOES_NOT_FIT: WarningCategory.RETRIEVAL_WARNING,
    # -- the three §4 S5 reclassifications, each with its measurement ------------------------
    #
    # No §9 tool reads `:Entity` (`cypher.py`'s allowlist excludes it because `opendoor` has
    # degree ≥ 2,704) and `OBSERVATION_OF_SUBJECT` is banned from traversal, so this fires on
    # every package ever built. A warning that is always true of every package is not a property
    # of *this* evidence; it is the shape of the V1 retrieval layer, and reading it as a
    # data-quality caveat about the subject is the misreading the category prevents.
    SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH: WarningCategory.CAPABILITY_LIMITATION,
    # Zero `:EvidenceSource` nodes exist in the corpus (§13.7.2, measured 2026-08-03: all 2,714
    # evidence rows are `normalized_passage` or `normalized_table`). Passage and Document *are*
    # the V1 source model, so an empty section here is not a missing source — it is a lane that
    # emits nothing yet, and rendering it as an alarming empty section would tell a reader the
    # package failed to find sources it does carry.
    EVIDENCE_SOURCES_ABSENT_IN_V1: WarningCategory.CAPABILITY_LIMITATION,
    # No bounded relationship retrieval tool exists in V1 — `find_related_entities` is in §9's
    # table and is not one of the nine implemented, and §4 S4 forbids adding an unbounded one.
    # **It must not reduce the factual verification status of a post**: nothing the post says is
    # less true because a tool nobody wrote returned nothing, and §13's gate never sees it
    # because a `BUILD_PROVENANCE` code cannot become a `required_warning`.
    RELATIONSHIPS_UNAVAILABLE_IN_V1: WarningCategory.CAPABILITY_LIMITATION,
}

#: Which audience each category is for. **Total over `WarningCategory`**, and the reason the
#: five categories are a refinement rather than a third axis: `KIND_OF` is derived through this
#: table, so a code cannot be a capability limitation that also demands a sentence.
#:
#: The three claim-qualifying categories are the ones a reader needs in order to read the
#: sentence correctly — where a contradicting row sits relative to the cited cell, what the
#: fact's standing is, what the extractor could not finish. The two provenance categories are
#: properties of the build: how much of the corpus reached the package, and what V1 cannot do at
#: all. §13.14 already refuses outright the absence and uniqueness claims a partial read would
#: make false, so demanding prose for either would be demanding build plumbing from an investor
#: post — the defect `KIND_OF` was drawn to end.
KIND_OF_CATEGORY: Mapping[WarningCategory, WarningKind] = {
    WarningCategory.SUBSTANTIVE_COUNTER_EVIDENCE: WarningKind.CLAIM_QUALIFYING,
    WarningCategory.FACT_QUALITY_WARNING: WarningKind.CLAIM_QUALIFYING,
    WarningCategory.EXTRACTION_ISSUE: WarningKind.CLAIM_QUALIFYING,
    WarningCategory.RETRIEVAL_WARNING: WarningKind.BUILD_PROVENANCE,
    WarningCategory.CAPABILITY_LIMITATION: WarningKind.BUILD_PROVENANCE,
}

if set(CATEGORY_OF) != set(SEVERITY_OF):  # pragma: no cover - a source edit, not a state
    raise ValueError(
        "every declared warning code must declare a category: "
        f"{sorted(set(SEVERITY_OF) ^ set(CATEGORY_OF))} differ between SEVERITY_OF and "
        "CATEGORY_OF")

if set(KIND_OF_CATEGORY) != set(WarningCategory):  # pragma: no cover - a source edit
    raise ValueError(
        "every category must state the audience it is for: "
        f"{sorted(c.value for c in set(WarningCategory) ^ set(KIND_OF_CATEGORY))} is undecided")

#: Which audience each *code* is for — **derived**, never declared twice. The planner's
#: `claim_qualifying_warnings` and §13's disclosure check read this split (through
#: `PackagedWarning.kind`, since a stage may not import another stage), and it is unchanged by
#: §4 S5: the same sixteen codes qualify a claim and the same thirteen record the build.
KIND_OF: Mapping[str, WarningKind] = {
    code: KIND_OF_CATEGORY[category] for code, category in CATEGORY_OF.items()
}

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


def category_of(warning: PackagedWarning) -> WarningCategory:
    """One warning's §4 S5 category, looked up by code.

    A function rather than a field on `PackagedWarning`, and the reason is a measurement: the
    row is inside the slice the model reads, and a twenty-row `warnings[]` carrying its category
    costs 170 prompt tokens — a third of a passage at §10.2.1's 536-token median. Nothing that
    cannot import this module needs the answer, unlike `kind`, which the planner reads off the
    row because a stage may not import another stage.

    Falls back to the kind's own category for a code this table does not declare, so a package
    read back from an older artifact renders as something rather than raising: `claim_qualifying`
    becomes a fact-quality warning and `build_provenance` a retrieval warning, which is what each
    meant before the categories existed.
    """
    declared = CATEGORY_OF.get(warning.code)
    if declared is not None:
        return declared
    return (WarningCategory.FACT_QUALITY_WARNING
            if warning.kind is WarningKind.CLAIM_QUALIFYING
            else WarningCategory.RETRIEVAL_WARNING)


def capability_limitations(warnings: Sequence[PackagedWarning]) -> tuple[PackagedWarning, ...]:
    """The warnings that say *"V1 cannot do this"* rather than anything about the evidence.

    A helper for the same reason `claim_qualifying` and `blocking` are: the evidence panel and
    §4 S6's *"Facts sent to the model"* section render this family differently — as a stated
    limit of the retrieval layer, beside the empty section it explains — and *"which warnings
    are limits"* is a property of the vocabulary above. It filters nothing out of `warnings[]`:
    every row it returns is still in the package, still severity-ordered, still counted.
    """
    return tuple(w for w in warnings
                 if category_of(w) is WarningCategory.CAPABILITY_LIMITATION)


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
    "CATEGORY_OF",
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
    "KIND_OF_CATEGORY",
    "METRIC_AMBIGUITY_DECLARED",
    "OBSERVATION_LOAD_INCOMPLETE",
    "PACKAGE_EXCEEDS_TOKEN_CEILING",
    "POPULATION_DEFINITION_DIFFERS",
    "RELATIONSHIPS_UNAVAILABLE_IN_V1",
    "REQUIRED_FACT_DOES_NOT_FIT",
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
    "capability_limitations",
    "category_of",
    "claim_qualifying",
    "packaged_warning",
    "warning_sort_key",
]
