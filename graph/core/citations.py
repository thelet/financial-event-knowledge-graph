"""Which passages one run cites — asked once, answered once, for both projection stages.

The node builder needs it to decide which `:Passage` and `:Document` nodes exist; the edge
builder needs it to decide which `PART_OF` edges exist. Those two answers must be the same
answer, and until 2026-08-03 they were two functions that were **not**:

| | node builder | edge builder |
| --- | --- | --- |
| union | claims ∪ issues ∪ rejected_claims ∪ evidence | claims ∪ issues ∪ evidence |
| an empty `passage_id` | raised | was silently skipped |

Both differences point the same way: a passage cited *only* by a rejection got a `:Passage`
node and no `PART_OF` edge, and an empty `passage_id` — legal in a catalog (§2.3 trap 2) —
either stopped the build or quietly removed a citation depending on which builder saw it
first. Neither divergence fires on `extract-v1-lexical-2422c4252c07`, where all four unions
are the same 8,776 passages and no row carries an empty id *(verified 2026-08-02)*, which is
exactly why it had to be fixed from the code rather than from a failing run.

**The one policy, stated once.** The union is the widest of the four — every row that
literally holds a citation, including `rejected_claims.jsonl` — and an empty `passage_id` is
a **refusal**, not a skip. Refusing is the stricter of the two behaviours and the one §10
criterion 4 depends on: a fact whose evidence cannot be located is not projectable, and
skipping it would turn that guarantee into a silent drop the first time one appeared.
"""

from __future__ import annotations

from typing import Iterable

from .inputs import ClaimRow, ExtractionRunInputs, GraphInputError, cited_passage_of


class EmptyPassageCitationError(GraphInputError):
    """A row cites `""` as its passage. Legal in the catalog, never a node key.

    Carries §6.4's code so the export can file it as a rejected row rather than crash the
    run: it is a malformed row, not a broken run.
    """

    code = "MISSING_REQUIRED_KEY"


def required_passage_id(passage_id: str, *, cited_by: str) -> str:
    """`""` is a legal catalog value and never a passage node key (§2.3 trap 2)."""
    if not passage_id.strip():
        raise EmptyPassageCitationError(
            f"{cited_by} cites an empty passage_id; a fact with no locatable evidence is not "
            "projectable (§10 criterion 4)")
    return passage_id


def cited_passage_ids(inputs: ExtractionRunInputs) -> tuple[str, ...]:
    """Every passage some row of this run points at, sorted. 8,776 on the real run.

    Four sources, in the order the catalogs are written. §3.1 says "claim or issue"; the two
    remaining files are folded in because they are also rows that literally hold a citation —
    an `EVIDENCED_BY` target must have a `PART_OF`, and a rejection names the passage its
    refusal is about. *Measured 2026-08-02: all four unions are the same 8,776 passages*
    (153 by claims, 8,757 by issues), so the wider set is robustness rather than a different
    answer on this run.
    """
    cited: set[str] = set()
    for claim in inputs.claims:
        if not claim.passage_id and not cites_a_passage(inputs, claim):
            # F0 Part B. `claims.jsonl` writes `passage_id: ""` when the claim's evidence
            # names no passage (`jsonl_catalog._anchor`), which is now a legal state rather
            # than a broken row: an XBRL fact, a market-data row and a calculated value cite
            # no passage and never will. The refusal below is kept for the case it was
            # written for — a claim whose evidence *does* name a passage and whose own
            # `passage_id` is empty is still a fact with unlocatable evidence, and the two
            # are told apart by reading the evidence rather than by trusting the empty string.
            continue
        cited.add(required_passage_id(claim.passage_id, cited_by=claim.claim_id))
    for issue in inputs.issues:
        cited.add(required_passage_id(issue.passage_id, cited_by=issue.issue_id))
    for rejection in inputs.rejected_claims:
        cited.add(required_passage_id(
            rejection.passage_id, cited_by=rejection.rejection_id))
    for row in inputs.evidence:
        # `None` means "this evidence names no passage" — either a passage row that left the
        # field null, or a row of a kind that has no such column at all. Neither is a
        # citation, so neither adds anything here.
        passage_id = cited_passage_of(row)
        if passage_id is not None:
            cited.add(required_passage_id(passage_id, cited_by=row.claim_id))
    return tuple(sorted(cited))


def cites_a_passage(inputs: ExtractionRunInputs, claim: ClaimRow) -> bool:
    """Whether any of this claim's evidence rows names a filed passage.

    Read from `evidence.jsonl` rather than from `claims.passage_id`, because the empty string
    is exactly the value being interpreted and cannot also be the evidence for the
    interpretation. `check_evidence_claim_ids` has already guaranteed every claim has at least
    one evidence row, so an empty answer here means "every reference this claim carries is of
    a kind that names no passage" and not "we found nothing".
    """
    return any(cited_passage_of(row) is not None
               for row in inputs.evidence_by_claim_id.get(claim.claim_id, ()))


def documents_of(inputs: ExtractionRunInputs, passage_ids: Iterable[str]) -> tuple[str, ...]:
    """The documents owning those passages, sorted, skipping ids the corpus does not hold.

    Missing passages are the `:Passage` builder's refusal to make, not this function's: it
    names the passage and the catalog, and a second refusal here would report the same fact
    twice in a worse message.
    """
    return tuple(sorted({inputs.passages_by_id[passage_id].document_id
                         for passage_id in passage_ids
                         if passage_id in inputs.passages_by_id}))


__all__ = [
    "EmptyPassageCitationError",
    "cited_passage_ids",
    "cites_a_passage",
    "documents_of",
    "required_passage_id",
]
