"""§10's `counter_evidence[]`: which refusals reach the model, at what grain, and how that grain
is disclosed.

Responsibility: turning `find_counter_evidence` rows into bounded, excerpted package rows, each
one carrying the basis on which it was associated with the candidate's facts. It performs no
retrieval and takes no free text — the tool it consumes is keyed on `(metric_id, period_key)`
and §11's second consequence is that **counter-evidence may never come from `search_passages`**,
because a term list that ranked the inconvenient passage out of the top 25 would satisfy §11's
non-empty-counterpoints rule by deleting the counterpoint.

**The join is at document grain, and that was measured rather than preferred.** Joining on the
*passages* that evidence a fact returns **zero rows** for `adjusted_ebitda` 2022Q3 and for
`gaap_gross_margin` 2022Q3 — the refusals live in neighbouring tables of the same filing, not in
the cell the number came from. At document grain the same call returns **9 rows for
`contribution_margin` 2022Q3 and 3 for `homes_sold` 2022Q3** *(verified live 2026-08-03, and
re-verified against `graph-v1-0483dc6b4b10` while writing this module)*. Counter-evidence is
*"what this filing would not let the run say about this metric"*, and the filing is the scope in
which that is true.

**Which is exactly why every row says so.** A document-level association presented as a direct
contradiction is a §13.14 refusal waiting to happen — *"the same sentence about GAAP gross
margin is true and about adjusted gross margin is false"* is the shape of the attack, and a
package that let a neighbouring table's `AMBIGUOUS_ALIAS` refusal read as a contradiction of the
cited cell would be handing the writer that sentence.

**S2 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS changed what this module is allowed to conclude from
that join, and it is the whole point of the stage.** Document grain is where the rows *come
from*; it was never a reason to call one a contradiction. §1 measured the cost: the inventory
candidate's two counter-evidence passages carried 20 issues between them and **not one
challenged** *"inventory fell from 12,788 homes to 6,261 homes"* — nineteen `AMBIGUOUS_ALIAS`
refusals about a table label the ontology cannot split between `housing_inventory_homes` and
`inventory_balance`, and one `UNIT_CONTRADICTS_ONTOLOGY` rejection of a claim nobody emitted.
The best-corroborated fact in the corpus arrived labelled as heavily contradicted.

So a passage may be `counter_evidence` only on one of the six named bases below, each of which
is deterministic evidence that the passage challenges *this* story. Everything else keeps its
row, keeps its text and keeps its issue codes, and is labelled `warning_only` — a diagnostic
that qualifies a fact rather than a counterpoint that opposes it.

**Correcting §1 on one point.** The plan's table calls `UNIT_CONTRADICTS_ONTOLOGY` an *"ontology
guard, other concept"*. Measured against `graph-v1-0483dc6b4b10` on 2026-08-05 the row carries
`concept_ids = ['housing_inventory_homes']`: it is about **this** metric. It still does not
qualify, and the reason is better than the one the plan gave — an `:Issue` is a record of a
claim the run **refused to emit**, so it says what the graph does *not* contain. This one says a
`percent` reading was rejected because the ontology declares `homes`; the packaged fact is in
`homes`. The guard agrees with the fact it was filed against.

**The workaround this module recorded is gone.** It used to say that §10's `counter_evidence[]`
is typed `tuple[PackagedPassage, ...]` in a module S5 did not own and which had no field for a
match basis, so the basis travelled as one of two `ANNOTATE` warnings per row and
`match_basis_of` read it back out of the package. S1 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS owns
that module: `PackagedPassage.match_basis` is a real field and `match_basis_of` reads it.
The two disclosure warnings stay, because they are what §13.17's evidence panel renders beside
the claim and no consumer of them changed; both they and the field are written from one value at
one call site, and `CounterEvidenceRow` refuses a row where the two disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import story.stages.packaging.warning_codes as warning_codes
from story.core.models import (
    EvidenceRole,
    PackagedFact,
    PackagedPassage,
    PackagedWarning,
    StoryEvidencePackage,
)
from story.core.observation_equivalence import DIVERGENCE_REASONS
from story.stages.packaging.passage_excerpts import Excerpt

# -- the two association grains -------------------------------------------------------------
#
# These say **where** a row was found relative to the cited cell. They are not reasons to
# believe anything: §4 S2 is one sentence about them — *"same-document proximity is not enough,
# a shared keyword is not enough, a repeated value is never counter-evidence"* — and a row that
# qualifies on nothing more than one of these is a `warning_only` diagnostic.

#: The issue was found in a passage a used fact is itself bound to.
MATCH_BASIS_SAME_PASSAGE = "same_passage"

#: The issue was found in a *different* passage of a document a used fact was read from. The
#: ordinary case in this corpus, and the one §13.14 is waiting for.
MATCH_BASIS_SAME_DOCUMENT = "same_document"

ASSOCIATION_BASES: frozenset[str] = frozenset(
    {MATCH_BASIS_SAME_PASSAGE, MATCH_BASIS_SAME_DOCUMENT})

# -- the six qualifying bases (§4 S2) -------------------------------------------------------
#
# A passage may be `counter_evidence` **only** on one of these, and each names deterministic
# evidence that the passage challenges *this* story rather than sitting near it.

#: Same metric and period, value outside presentation tolerance. Produced from the observations
#: themselves, never from an `:Issue`: an issue is a record of a claim the run **refused to
#: emit**, so it carries no value that could disagree with one.
MATCH_BASIS_VALUE_OUTSIDE_TOLERANCE = "value_outside_tolerance"

#: Same metric, materially different period or scope, undermining the stated comparison.
MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON = "scope_undermines_comparison"

#: An explicit textual qualification or limitation relevant to the thesis — the corpus stating
#: that the cited passage defines rather than reports, or reports guidance rather than fact.
MATCH_BASIS_TEXTUAL_LIMITATION = "textual_limitation"

#: Opposite direction under a comparable definition: two readings of one slot that disagree
#: about the sign, not only about the size.
MATCH_BASIS_OPPOSITE_DIRECTION = "opposite_direction"

#: An issue code that changes how **this fact** may be read — the citation chain of the very
#: passage a used fact was read from is impugned.
MATCH_BASIS_ISSUE_CHANGES_READING = "issue_changes_reading"

#: Conflicting subject, cohort, unit, formula version or period semantics.
MATCH_BASIS_INCOMPATIBLE_SEMANTICS = "incompatible_semantics"

QUALIFYING_BASES: frozenset[str] = frozenset({
    MATCH_BASIS_VALUE_OUTSIDE_TOLERANCE,
    MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON,
    MATCH_BASIS_TEXTUAL_LIMITATION,
    MATCH_BASIS_OPPOSITE_DIRECTION,
    MATCH_BASIS_ISSUE_CHANGES_READING,
    MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
})

#: Which warning code carries which **grain**. One mapping, read in both directions — written by
#: `disclosure()` and read by `package_assembly.without_orphaned_disclosures` — so the pair
#: cannot drift into disagreement.
#:
#: **Keyed on the grain and not on the qualifying basis**, and the split is deliberate. The two
#: codes say *where* the row sits relative to the cited cell, which is what §13.17's evidence
#: panel renders and what `deterministic.REQUIRED_WARNING_QUALIFIERS` demands a sentence about;
#: the qualifying basis says *why the row is counter-evidence at all* and travels on the row
#: itself, in `PackagedPassage.match_basis`. Adding six more codes here would have meant editing
#: `warning_codes.py`, which S5 owns concurrently, for a distinction the row already carries.
BASIS_CODE: Mapping[str, str] = {
    MATCH_BASIS_SAME_PASSAGE: warning_codes.COUNTER_EVIDENCE_SAME_PASSAGE,
    MATCH_BASIS_SAME_DOCUMENT: warning_codes.COUNTER_EVIDENCE_SAME_DOCUMENT,
}
CODE_BASIS: Mapping[str, str] = {code: basis for basis, code in BASIS_CODE.items()}

# -- what an `:Issue` may and may not qualify on ---------------------------------------------

#: The issue codes that qualify a row as counter-evidence, and the basis each one qualifies on.
#: Applied **only at passage grain** — see `classify_issue`.
#:
#: Every member is an issue about the *citation chain of the cited cell*: the quoted span does
#: not occur in the passage, the value disagrees with the quote it was read from, the period is
#: not grounded in the text, the passage defines rather than reports. Each of those changes how
#: the fact bound to that passage may be read, whatever claim the issue itself rejected.
#:
#: **What is deliberately absent, and why.** `AMBIGUOUS_ALIAS` is 466 of the run's 6,278 issues
#: and is the code §1 measured the whole defect on: *"'Inventory (at period end)' resolves to 2
#: concepts; no claim emitted"* is a fact-quality caveat the ontology already states through
#: `metric_ambiguity_declared`, not a counterpoint. §4 S2 admits one exception — *"unless it
#: proves the bound fact's metric identity, value, scope or period is actually incompatible"* —
#: and that exception is **unreachable from this row shape**: `narrow` has already dropped every
#: row whose `concept_ids` share nothing with the candidate's metrics, so every surviving
#: `AMBIGUOUS_ALIAS` names this metric among its candidates, which is the definition of a
#: declared ambiguity rather than a proof of incompatibility. Writing the branch anyway would be
#: dead code claiming a check nobody can trigger.
#:
#: `DERIVED_COMPARISON` and `DERIVED_CHANGE_COLUMN` say a *change* column was not a reported
#: level. The package computes its own change from two levels, so the refusal agrees with the
#: package rather than contradicting it. `DEFERRED_REQUIRED_SOURCE_LANE` is a capability limit
#: — *"revenue requires a source lane this corpus does not contain"* — and S5 reclassifies it.
QUALIFYING_ISSUE_CODES: Mapping[str, str] = {
    "QUOTED_SPAN_NOT_IN_PASSAGE": MATCH_BASIS_ISSUE_CHANGES_READING,
    "VALUE_CONTRADICTS_QUOTED_TEXT": MATCH_BASIS_ISSUE_CHANGES_READING,
    "PROPERTY_VALUE_NOT_IN_PASSAGE": MATCH_BASIS_ISSUE_CHANGES_READING,
    "PERIOD_NOT_GROUNDED_IN_PASSAGE": MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON,
    "MISSING_PERIOD": MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON,
    "DEFINITIONAL_NOT_OBSERVATIONAL": MATCH_BASIS_TEXTUAL_LIMITATION,
    "GUIDANCE_NOT_REPORTED": MATCH_BASIS_TEXTUAL_LIMITATION,
    "NOT_THE_SUBJECT_COMPANY": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "UNIT_CONTRADICTS_ONTOLOGY": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
}

#: Codes from `QUALIFYING_ISSUE_CODES` that qualify only at `rejection` severity.
#: `MISSING_PERIOD` is 214 refusals and 3 rejections in this run: the refusals are *"this
#: percentage appears in a table row without a specific period label"*, which is a statement
#: about a row nobody emitted, while a rejection is a claim that was withdrawn because its
#: period could not be established. Only the second says anything about a period the package
#: relies on.
REJECTION_ONLY_ISSUE_CODES: frozenset[str] = frozenset({"MISSING_PERIOD"})

#: How a divergent observation's reason becomes a `match_basis`. Total over
#: `observation_equivalence.DIVERGENCE_REASONS` and checked below, so a reason added there
#: without a decision here is an import error rather than a row that quietly gets no basis.
DIVERGENCE_BASIS: Mapping[str, str] = {
    "SUBJECT_MISMATCH": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "METRIC_MISMATCH": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "PERIOD_MISMATCH": MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON,
    "UNCOMPARABLE_SHAPE": MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON,
    "PERCENT_VERSUS_PERCENTAGE_POINTS": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "UNIT_MISMATCH": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "CURRENCY_MISMATCH": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "FORMULA_VERSION_MISMATCH": MATCH_BASIS_INCOMPATIBLE_SEMANTICS,
    "OPPOSITE_SIGN": MATCH_BASIS_OPPOSITE_DIRECTION,
    "VALUE_OUTSIDE_TOLERANCE": MATCH_BASIS_VALUE_OUTSIDE_TOLERANCE,
}

if set(DIVERGENCE_BASIS) != set(DIVERGENCE_REASONS):  # pragma: no cover - a source edit
    raise ValueError(
        "every divergence reason must map to a match basis: "
        f"{sorted(set(DIVERGENCE_BASIS) ^ set(DIVERGENCE_REASONS))} differ")


@dataclass(frozen=True, slots=True)
class Classification:
    """What a row turned out to be, and on what basis — S2's answer for one association.

    Held as a value rather than returned as a tuple because three call sites read it and two of
    them read only one field; a bare `(role, basis, why)` tuple is where the third field starts
    being dropped.
    """

    role: EvidenceRole
    match_basis: str
    why: str

    @property
    def qualifies(self) -> bool:
        return self.role is EvidenceRole.COUNTER_EVIDENCE


def classify_issue(
    row: Mapping[str, Any],
    *,
    grain: str,
    bound_fact: PackagedFact | None,
    declared_unit: str | None,
) -> Classification:
    """One `find_counter_evidence` row, classified — the whole of S2's issue half.

    **Passage grain is necessary and is not sufficient.** An `:Issue` records a claim the run
    refused to emit; a refusal recorded in a *neighbouring table of the same filing* cannot
    contradict the cell this package cites, because it is not about that cell and the graph
    holds no observation from it. So a document-grain row is `warning_only` whatever its code
    or severity, and a passage-grain row still has to carry a code from
    `QUALIFYING_ISSUE_CODES`.

    **Severity is an input and not the test.** `severity_rank` orders the section (D6) and a
    `rejection` about another concept is still not about this story — §1's
    `UNIT_CONTRADICTS_ONTOLOGY` is a rejection, is about this very metric, and still does not
    challenge *"inventory fell from 12,788 homes to 6,261 homes"*.

    `declared_unit` is the ontology's unit for the metric, which is what makes
    `UNIT_CONTRADICTS_ONTOLOGY` decidable without parsing the issue's prose: the issue says some
    claim's unit contradicts the ontology, and it qualifies only when the unit the **packaged
    fact** carries is the one being contradicted. Measured on the inventory candidate: the fact
    is in `homes`, the ontology declares `homes`, the rejected claim was in `percent` — so the
    issue proves a claim nobody made was wrong, and the packaged fact is untouched.
    """
    code = str(row.get("code") or "")
    severity = str(row.get("severity") or "")

    if grain != MATCH_BASIS_SAME_PASSAGE:
        return Classification(
            EvidenceRole.WARNING_ONLY, MATCH_BASIS_SAME_DOCUMENT,
            f"{code or 'the issue'} was recorded elsewhere in the same filing, not in the cell "
            "this package cites; document proximity is not evidence about this fact")

    basis = QUALIFYING_ISSUE_CODES.get(code)
    if basis is None:
        return Classification(
            EvidenceRole.WARNING_ONLY, MATCH_BASIS_SAME_PASSAGE,
            f"{code or 'the issue'} is an extraction or data-quality diagnostic about a claim "
            "the run declined to emit; it qualifies the cited fact and does not contradict it")
    if code in REJECTION_ONLY_ISSUE_CODES and severity != "rejection":
        return Classification(
            EvidenceRole.WARNING_ONLY, MATCH_BASIS_SAME_PASSAGE,
            f"{code} at {severity!r} severity describes a row no claim was emitted from; only a "
            "rejection withdraws a claim whose period could not be established")
    if code == "UNIT_CONTRADICTS_ONTOLOGY":
        fact_unit = None if bound_fact is None else bound_fact.unit
        if declared_unit is None or fact_unit is None or fact_unit == declared_unit:
            return Classification(
                EvidenceRole.WARNING_ONLY, MATCH_BASIS_SAME_PASSAGE,
                f"{code} rejected a claim whose unit contradicted the ontology; the packaged "
                f"fact is in {fact_unit!r}, which is the unit the ontology declares "
                f"({declared_unit!r}), so the guard agrees with this fact rather than "
                "contradicting it")

    return Classification(
        EvidenceRole.COUNTER_EVIDENCE, basis,
        f"{code} ({severity}) was found in the passage the cited fact was read from and "
        f"qualifies on {basis}")


def basis_for_divergence(reason: str) -> str:
    """The `match_basis` a divergent same-slot observation is counter-evidence on.

    A second reading of the very slot the story anchors on is the only counter-evidence in this
    package that rests on a *value*, which is why S2 and S3 are one change: the observations
    `observation_equivalence.same_reading` calls equivalent become corroboration, and the ones
    it calls divergent become this.
    """
    return DIVERGENCE_BASIS[reason]


@dataclass(frozen=True, slots=True)
class CounterEvidenceRow:
    """One association, ready to enter the package, with everything a drop rule needs.

    Held apart from the `PackagedPassage` it will become because the package type carries none
    of `severity`, `code` or `issue_id`, and §10.2's drop rule is stated over exactly those:
    *least severe first*, which is D6's ruling for the retrieval bound applied to the packaging
    bound. Building the passage and then re-deriving its severity from a warning string would be
    the string surgery `results.result_code` exists to prevent.

    **`grain` and `match_basis` are two fields because they are two facts.** Before S2 they were
    one: `match_basis` held `same_passage` or `same_document` and the section it landed in was
    the claim. S2 makes the claim explicit — `role` says what the row is, `match_basis` says the
    basis it qualified on, and `grain` keeps saying where the row sits relative to the cited
    cell, which is what §13.17's panel renders and what the two disclosure codes are keyed on.
    A row that did not qualify carries its grain in both fields, which is the honest answer:
    proximity is all that was established.

    `issue_id`, `code`, `severity` and `severity_rank` are empty on a row built from a divergent
    **observation** rather than from an `:Issue` — that row's evidence is a value, not a refusal,
    and it ranks above every issue-derived row because a disagreement about the number is
    stronger than a diagnostic about the extraction.
    """

    passage: PackagedPassage
    match_basis: str
    grain: str
    role: EvidenceRole
    metric_id: str
    period_key: str
    excerpt: Excerpt
    issue_id: str = ""
    code: str = ""
    severity: str = ""
    severity_rank: int = 3
    row_label: str | None = None
    #: Why this row is what it is, in one sentence — `Classification.why`, or the
    #: `observation_equivalence.Divergent.detail` for an observation-derived row. Rendered into
    #: the disclosure and, for a demoted row, into the diagnostic the package carries.
    why: str = ""

    def __post_init__(self) -> None:
        """The passage and the row agree on the basis and the role, or neither is built.

        Three readers now exist — `match_basis_of` reads `passage.match_basis`, `disclosure()`
        writes `self.match_basis` into the warning, and every consumer of the package reads
        `passage.role` — and a row that could hold two answers is a package whose panel and
        whose consumer disagree about what it found. All three are set from one classification
        in `evidence_package._add_counter_evidence`; this is what makes that a rule rather than
        a habit.

        The role and the basis are checked against each other for the same reason: a
        `counter_evidence` row whose basis is a bare association grain is precisely the defect
        §1 measured, and it must be unconstructible rather than merely unproduced.
        """
        if self.passage.match_basis != self.match_basis:
            raise ValueError(
                f"{self.passage.passage_id}: the row was built with match_basis="
                f"{self.match_basis!r} and its passage carries "
                f"{self.passage.match_basis!r}; the disclosure warning and the packaged row "
                "would then say different things about the same association")
        if self.passage.role is not self.role:
            raise ValueError(
                f"{self.passage.passage_id}: the row was built with role={self.role.value} and "
                f"its passage carries {self.passage.role.value}")
        if self.grain not in ASSOCIATION_BASES:
            raise ValueError(
                f"{self.passage.passage_id}: grain={self.grain!r} is not one of "
                f"{sorted(ASSOCIATION_BASES)}; the grain says where the row was found and has "
                "only ever had two answers")
        qualifying = self.match_basis in QUALIFYING_BASES
        if (self.role is EvidenceRole.COUNTER_EVIDENCE) is not qualifying:
            raise ValueError(
                f"{self.passage.passage_id}: role={self.role.value} with "
                f"match_basis={self.match_basis!r}. A passage is counter-evidence exactly when "
                f"it qualified on one of {sorted(QUALIFYING_BASES)}; §1 measured what happens "
                "when a shared document is allowed to be the basis")

    @property
    def sort_key(self) -> tuple[int, int, int, str, str]:
        """§10.2's `counter_evidence[]` drop rule, total and stated.

        Value evidence before issue evidence, then passage-grain before document-grain, then
        most severe first, then the issue code, then the issue id. A row that disagrees about
        the *number* leads because it is the only kind of counter-evidence in this package that
        rests on one; below that the rank is the one `cypher.COUNTER_EVIDENCE`'s own `CASE`
        produced, so the package's order and the query's order agree rather than being two
        opinions.
        """
        return (
            0 if not self.code else 1,
            0 if self.grain == MATCH_BASIS_SAME_PASSAGE else 1,
            self.severity_rank,
            self.code,
            self.issue_id or self.passage.passage_id,
        )

    def disclosure(self) -> PackagedWarning:
        """The warning that carries this row's basis and grain into the package.

        **Emitted for counter-evidence rows only.** A demoted row's diagnostic travels on the
        row itself — its role, its `match_basis` and its `diagnostic_codes` — because both codes
        below are `CLAIM_QUALIFYING`, and `deterministic.REQUIRED_WARNING_QUALIFIERS` turns
        `counter_evidence_same_document` into a sentence the post must write. Demanding *"as
        reported elsewhere in the filing"* about an extraction diagnostic is the §13 pressure §1
        is complaining about, one layer down.
        """
        issue = (f"issue {self.code} ({self.severity})" if self.code
                 else "a second reading of this slot")
        return warning_codes.packaged_warning(
            BASIS_CODE[self.grain],
            subject_ids=(self.passage.passage_id, self.issue_id, self.metric_id),
            detail=(
                f"match_basis={self.match_basis}; {issue} concerns "
                f"{self.metric_id} {self.period_key} and was found in "
                f"{self.passage.passage_id}"
                + (f" at row {self.row_label!r}" if self.row_label else "")
                + (
                    ". The cited fact was read from another passage of the same filing, so this "
                    "is an association at document grain and not a contradiction of the cited "
                    "cell (§13.14)"
                    if self.grain == MATCH_BASIS_SAME_DOCUMENT
                    else ". The cited fact was read from this same passage"
                )
                + (f". {self.why}" if self.why else "")
            ),
        )


def narrow(
    rows: Sequence[Mapping[str, Any]],
    *,
    metric_ids: Sequence[str],
    cited_document_ids: Sequence[str],
    prefers: Callable[[Mapping[str, Any]], bool] | None = None,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    """The rows this candidate's facts are actually associated with, and the ids that were not.

    **Period narrowing happens at the call, not here.** `find_counter_evidence` is keyed on
    `(metric_id, period_key)` and the rows it returns carry no period field of their own, so a
    filter written against one would be a rule that never fires — the shape of defect P8, where
    a guard spelled `narrative` against a stored `normalized_narrative` read as a rule and was
    not one. The builder issues one call per `(metric, period)` the candidate anchored on, and
    that is the period narrowing.

    Four further narrowings, all deterministic and all from fields the row already carries:

    1. **Document scope.** A row whose `document_id` is not one the packaged facts were read
       from is dropped. `find_counter_evidence` already scopes to the documents reporting the
       metric-period, but the package's *facts* are a subset of those observations after
       §10.2's cap, and an issue from a filing the package cites nothing from is an association
       with nothing.
    2. **Metric.** A row whose `metric_id` is not one the candidate references is dropped. The
       tool cannot return one — it queries `CONCERNS_METRIC` on the metric it was given — but a
       package assembled from two tool calls can, and the check costs a comparison.
    3. **Compatibility, where available.** `concept_ids` names the concepts an `AMBIGUOUS_ALIAS`
       refusal could not choose between. When it is present and shares nothing with the
       candidate's metrics, the refusal is about a different quantity that happened to be
       refused in the same filing; it is dropped. When it is absent — `UNIT_CONTRADICTS_ONTOLOGY`
       rows carry none — nothing is inferred from the absence (C2).
    4. **One row per passage.** Nine `AMBIGUOUS_ALIAS` refusals over four passages is four rows
       of evidence, not nine; shipping the same passage twice would spend §10.2's budget twice
       to say one thing. The most severe issue in a passage represents it — **unless `prefers`
       accepts a later one**, which is S2's amendment and is the only behavioural change here.
       Severity is not the same question as qualification: a `QUOTED_SPAN_NOT_IN_PASSAGE`
       rejection and an `AMBIGUOUS_ALIAS` rejection are both rank 0, and the first is the one
       that says something about this fact. `prefers` is passed the raw row and answers *"would
       this one qualify as counter-evidence?"*; a passage that has such a row is represented by
       it. Within each group the tool's severity order still decides, so the choice stays total.

    Returns the kept rows in the tool's own order and the `issue_id`s that were dropped, because
    *"counter-evidence never silently dropped"* is S5's acceptance condition and a count is not
    a record.
    """
    metrics = set(metric_ids)
    documents = set(cited_document_ids)
    eligible: list[tuple[int, Mapping[str, Any]]] = []
    dropped: list[str] = []

    for index, row in enumerate(rows):
        issue_id = str(row.get("issue_id") or "")
        document_id = str(row.get("document_id") or "")
        metric_id = str(row.get("metric_id") or "")
        passage_id = str(row.get("passage_id") or "")
        concepts = row.get("concept_ids")
        if document_id not in documents or metric_id not in metrics or not passage_id:
            dropped.append(issue_id)
            continue
        if isinstance(concepts, (list, tuple)) and concepts and not (set(map(str, concepts)) & metrics):
            dropped.append(issue_id)
            continue
        eligible.append((index, row))

    # Positions rather than the rows themselves: two rows of one query can be equal mappings,
    # and a set of dicts is not a thing that exists.
    representative: dict[str, int] = {}
    for index, row in eligible:
        passage_id = str(row["passage_id"])
        held = representative.get(passage_id)
        if held is None or (
                prefers is not None and prefers(row) and not prefers(rows[held])):
            representative[passage_id] = index

    chosen = set(representative.values())
    kept = [row for index, row in eligible if index in chosen]
    dropped.extend(str(row.get("issue_id") or "")
                   for index, row in eligible if index not in chosen)
    return tuple(kept), tuple(dropped)


def match_basis(passage_id: str, cited_passage_ids: Sequence[str]) -> str:
    """Passage grain when the refusal sits in a passage a fact cites, document grain otherwise."""
    return (
        MATCH_BASIS_SAME_PASSAGE
        if passage_id in set(cited_passage_ids)
        else MATCH_BASIS_SAME_DOCUMENT
    )


def match_basis_of(package: StoryEvidencePackage) -> Mapping[str, str]:
    """Every counter-evidence passage in a package, mapped to the basis it was associated on.

    **Reads `PackagedPassage.match_basis`.** Until S1 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS the
    row type had no such field and this function reconstructed the basis from the two disclosure
    warnings — which meant a package trimmed past its warning cap silently lost the answer for a
    row it was still carrying, and a caller could not tell that from a row with no basis at all.
    The signature and the contract are unchanged: `match_basis_of(package)[passage_id]` is the
    whole interface, and a row carrying no basis is a missing key rather than a plausible
    default.
    """
    return {row.passage_id: row.match_basis
            for row in package.counter_evidence if row.match_basis}


def needles_for(row: Mapping[str, Any]) -> tuple[str, ...]:
    """What the excerpt window should centre on, most specific first.

    `row_label` before `rejected_claim` before `code`: the label is the line of the table the
    refusal is about and is the shortest string that locates it, the claim is longer and may be
    re-wrapped in the passage, and the code is a last resort that will usually not occur in
    filed text at all. Ordered, not scored — `passage_excerpts.window` takes the first that
    occurs, and a "closest match" rule would let a code that happens to appear in a footnote
    move the window off the row.
    """
    return tuple(
        value for value in (
            str(row.get("row_label") or ""),
            str(row.get("rejected_claim") or ""),
            str(row.get("code") or ""),
        ) if value and value not in {"True", "False"}
    )


__all__ = [
    "ASSOCIATION_BASES",
    "BASIS_CODE",
    "CODE_BASIS",
    "DIVERGENCE_BASIS",
    "MATCH_BASIS_INCOMPATIBLE_SEMANTICS",
    "MATCH_BASIS_ISSUE_CHANGES_READING",
    "MATCH_BASIS_OPPOSITE_DIRECTION",
    "MATCH_BASIS_SAME_DOCUMENT",
    "MATCH_BASIS_SAME_PASSAGE",
    "MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON",
    "MATCH_BASIS_TEXTUAL_LIMITATION",
    "MATCH_BASIS_VALUE_OUTSIDE_TOLERANCE",
    "QUALIFYING_BASES",
    "QUALIFYING_ISSUE_CODES",
    "REJECTION_ONLY_ISSUE_CODES",
    "Classification",
    "CounterEvidenceRow",
    "basis_for_divergence",
    "classify_issue",
    "match_basis",
    "match_basis_of",
    "narrow",
    "needles_for",
]
