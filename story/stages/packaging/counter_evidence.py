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
from typing import Any, Mapping, Sequence

import story.stages.packaging.warning_codes as warning_codes
from story.core.models import PackagedPassage, PackagedWarning, StoryEvidencePackage
from story.stages.packaging.passage_excerpts import Excerpt

#: The issue was found in a passage a used fact is itself bound to.
MATCH_BASIS_SAME_PASSAGE = "same_passage"

#: The issue was found in a *different* passage of a document a used fact was read from. The
#: ordinary case in this corpus, and the one §13.14 is waiting for.
MATCH_BASIS_SAME_DOCUMENT = "same_document"

#: Which warning code carries which basis. One mapping, read in both directions — written by
#: `disclosure_for` and read by `match_basis_of` — so the pair cannot drift into disagreement.
BASIS_CODE: Mapping[str, str] = {
    MATCH_BASIS_SAME_PASSAGE: warning_codes.COUNTER_EVIDENCE_SAME_PASSAGE,
    MATCH_BASIS_SAME_DOCUMENT: warning_codes.COUNTER_EVIDENCE_SAME_DOCUMENT,
}
CODE_BASIS: Mapping[str, str] = {code: basis for basis, code in BASIS_CODE.items()}


@dataclass(frozen=True, slots=True)
class CounterEvidenceRow:
    """One refusal, ready to enter the package, with everything a drop rule needs.

    Held apart from the `PackagedPassage` it will become because the package type carries none
    of `severity`, `code` or `issue_id`, and §10.2's drop rule is stated over exactly those:
    *least severe first*, which is D6's ruling for the retrieval bound applied to the packaging
    bound. Building the passage and then re-deriving its severity from a warning string would be
    the string surgery `results.result_code` exists to prevent.
    """

    passage: PackagedPassage
    match_basis: str
    issue_id: str
    code: str
    severity: str
    severity_rank: int
    metric_id: str
    period_key: str
    row_label: str | None
    excerpt: Excerpt

    def __post_init__(self) -> None:
        """The passage and the row agree on the basis, or neither is built.

        Two readers now exist — `match_basis_of` reads `passage.match_basis` and `disclosure()`
        writes `self.match_basis` into the warning — and a row that could hold two answers is a
        package whose panel and whose consumer disagree about whether a refusal sits in the
        cited cell. Both are set from one value in `evidence_package._add_counter_evidence`;
        this is what makes that a rule rather than a habit.
        """
        if self.passage.match_basis != self.match_basis:
            raise ValueError(
                f"{self.passage.passage_id}: the row was built with match_basis="
                f"{self.match_basis!r} and its passage carries "
                f"{self.passage.match_basis!r}; the disclosure warning and the packaged row "
                "would then say different things about the same association")

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        """§10.2's `counter_evidence[]` drop rule, total and stated.

        Passage-grain before document-grain, then most severe first, then the issue code, then
        the issue id. Direct evidence outranks associated evidence because that is the ordering
        of how much a counterpoint built on it can claim; below that the rank is the one
        `cypher.COUNTER_EVIDENCE`'s own `CASE` produced, so the package's order and the query's
        order agree rather than being two opinions.
        """
        return (
            0 if self.match_basis == MATCH_BASIS_SAME_PASSAGE else 1,
            self.severity_rank,
            self.code,
            self.issue_id,
        )

    def disclosure(self) -> PackagedWarning:
        """The warning that carries this row's `match_basis` into the package."""
        return warning_codes.packaged_warning(
            BASIS_CODE[self.match_basis],
            subject_ids=(self.passage.passage_id, self.issue_id, self.metric_id),
            detail=(
                f"match_basis={self.match_basis}; issue {self.code} ({self.severity}) concerns "
                f"{self.metric_id} {self.period_key} and was found in "
                f"{self.passage.passage_id}"
                + (f" at row {self.row_label!r}" if self.row_label else "")
                + (
                    ". The cited fact was read from another passage of the same filing, so this "
                    "is an association at document grain and not a contradiction of the cited "
                    "cell (§13.14)"
                    if self.match_basis == MATCH_BASIS_SAME_DOCUMENT
                    else ". The cited fact was read from this same passage"
                )
            ),
        )


def narrow(
    rows: Sequence[Mapping[str, Any]],
    *,
    metric_ids: Sequence[str],
    cited_document_ids: Sequence[str],
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
       to say one thing. The most severe issue in a passage represents it.

    Returns the kept rows in the tool's own order and the `issue_id`s that were dropped, because
    *"counter-evidence never silently dropped"* is S5's acceptance condition and a count is not
    a record.
    """
    metrics = set(metric_ids)
    documents = set(cited_document_ids)
    kept: list[Mapping[str, Any]] = []
    dropped: list[str] = []
    seen_passages: set[str] = set()

    for row in rows:
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
        if passage_id in seen_passages:
            dropped.append(issue_id)
            continue
        seen_passages.add(passage_id)
        kept.append(row)

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
    "BASIS_CODE",
    "CODE_BASIS",
    "MATCH_BASIS_SAME_DOCUMENT",
    "MATCH_BASIS_SAME_PASSAGE",
    "CounterEvidenceRow",
    "match_basis",
    "match_basis_of",
    "narrow",
    "needles_for",
]
