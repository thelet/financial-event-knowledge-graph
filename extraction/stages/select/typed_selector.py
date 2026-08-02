"""Typed candidate selection over the normalized corpus.

Four axes, in the order they are cheapest to decide: document type, passage kind, ontology
alias evidence, and lane. Every passage gets exactly one reason code per lane it was
considered for, selected or not.

Routing lives here rather than inside the lanes. A lane that filtered a shared stream by its
own private rules would make the decision invisible, and "not every table is deterministic"
is precisely the kind of judgement that has to be auditable.

The table lane carries its preceding passage. 65 of 74 KPI-bearing tables declare their
magnitude scale inside the table and 9 declare it in the passage immediately before
*(verified 2026-08-01)*; without the predecessor the second form is unreadable and every
dollar figure in it comes out a million times too small.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.models import CandidatePassage
from .alias_evidence import AliasIndex
from .public import (
    ADJACENT_SCALE_CONTEXT,
    AMBIGUOUS_ALIAS,
    CONTRACT_BOILERPLATE,
    DOCUMENT_TYPE_PRIOR,
    EXACT_ALIAS,
    GOVERNANCE_BOILERPLATE,
    HEADING_PRIOR,
    NO_CANDIDATE_SIGNAL,
    TABLE_LABEL,
    UNSUPPORTED_DOCUMENT_TYPE,
    UNSUPPORTED_PASSAGE_KIND,
    SelectionRequest,
    SelectionResult,
)

# Inclusion codes in precedence order. A passage matching several gets the most specific,
# so a count by reason answers "why did this passage get in" rather than "what did it touch".
_INCLUSION_PRECEDENCE = (
    TABLE_LABEL, EXACT_ALIAS, AMBIGUOUS_ALIAS, HEADING_PRIOR, DOCUMENT_TYPE_PRIOR,
    ADJACENT_SCALE_CONTEXT,
)


@dataclass(frozen=True)
class LanePolicy:
    name: str
    passage_kinds: frozenset[str]
    document_types: frozenset[str]
    carry_preceding_passage: bool
    # None means "whatever the policy's global signal rule says". A lane sets it only when its
    # own routing disagrees with that rule, and today exactly one does: the event lane offers
    # the declared event category entire, every event type carries `aliases = ()`, and so
    # requiring a *metric* alias hit would exclude an event passage for having no metric in it
    # — the routing gap STAGE_12 §1.2 named *(added 2026-08-02, step 13)*.
    require_alias_evidence: bool | None = None


@dataclass(frozen=True)
class SelectionPolicy:
    """The declarative half, loaded from `config/extraction.yaml`."""

    policy_version: str
    lanes: tuple[LanePolicy, ...]
    excluded_document_types: dict[str, frozenset[str]]
    heading_priors: tuple[str, ...]
    require_alias_evidence: bool

    @classmethod
    def from_config(cls, config: dict) -> "SelectionPolicy":
        selection = config["selection"]
        lanes = tuple(
            LanePolicy(
                name=name,
                passage_kinds=frozenset(spec["passage_kinds"]),
                document_types=frozenset(spec["document_types"]),
                carry_preceding_passage=bool(spec.get("carry_preceding_passage", False)),
                require_alias_evidence=(
                    None if spec.get("require_alias_evidence") is None
                    else bool(spec["require_alias_evidence"])),
            )
            for name, spec in selection["lanes"].items()
        )
        excluded = {
            reason: frozenset(types)
            for reason, types in (selection.get("excluded_document_types") or {}).items()
        }
        signals = selection.get("signals") or {}
        return cls(
            policy_version=selection.get("policy_version", "v1"),
            lanes=lanes,
            excluded_document_types=excluded,
            heading_priors=tuple(signals.get("heading_priors") or ()),
            require_alias_evidence=bool(signals.get("require_alias_evidence", True)),
        )

    def exclusion_for(self, document_type: str) -> str | None:
        """The reason a document type is excluded outright, if it is.

        `material_agreement` and `governance` are excluded because contract recitals and
        governance schedules carry no operating or non-GAAP metric — semantic routing, not
        a cost argument. They amount to 122 of 1,935 table passages after the encoding
        correction, so the saving is small; the reason to keep the rule is that admitting
        them dilutes every candidate the lane does emit.
        """
        for reason, types in self.excluded_document_types.items():
            if document_type in types:
                return reason
        return None


class TypedCandidateSelector:
    """Selects candidates from the normalized passage catalog."""

    name = "typed_selector"

    def __init__(self, policy: SelectionPolicy, alias_index: AliasIndex, ontology) -> None:
        self._policy = policy
        self._aliases = alias_index
        self._ontology = ontology

    def run(self, request: SelectionRequest) -> SelectionResult:
        raise NotImplementedError(
            "TypedCandidateSelector.run needs a passage source; use select_from_rows"
        )

    def select_from_rows(
        self, rows: list[dict], request: SelectionRequest | None = None
    ) -> SelectionResult:
        """Decide every (passage, lane) pair from catalog rows.

        Rows are plain dicts from `passages.jsonl` rather than a normalization type, so this
        stays testable without a corpus and extraction keeps importing nothing from
        normalization's storage.
        """
        lanes = self._lanes_for(request)
        by_document = _group_by_document(rows)
        result = SelectionResult()

        for row in rows:
            document_type = row.get("document_type") or ""
            passage_kind = row.get("passage_kind") or ""
            text = row.get("text") or ""
            heading = " ".join(row.get("heading_path") or ())

            hits = self._aliases.hits(text)
            matched, ambiguous_matched, row_label_matched = _partition(hits)
            heading_hit = self._heading_prior(heading)
            excluded_reason = self._policy.exclusion_for(document_type)

            for lane in lanes:
                reason = self._reason_for(
                    lane=lane,
                    document_type=document_type,
                    passage_kind=passage_kind,
                    excluded_reason=excluded_reason,
                    has_alias=bool(matched),
                    has_ambiguous=bool(ambiguous_matched),
                    has_row_label=bool(row_label_matched),
                    has_heading_prior=heading_hit,
                )
                preceding = None
                if lane.carry_preceding_passage and reason in _INCLUSION_PRECEDENCE:
                    preceding = _preceding_passage_id(by_document, row)

                result.candidates.append(CandidatePassage(
                    passage_id=row["passage_id"],
                    document_id=row["document_id"],
                    document_type=document_type,
                    passage_kind=passage_kind,
                    lane=lane.name,
                    reason=reason,
                    matched_metric_ids=self._candidate_concepts(matched, ambiguous_matched),
                    preceding_passage_id=preceding,
                ))
        return result

    # -- policy ----------------------------------------------------------------------------

    def _lanes_for(self, request: SelectionRequest | None) -> tuple[LanePolicy, ...]:
        if request is None:
            return self._policy.lanes
        wanted = set(request.lanes)
        return tuple(lane for lane in self._policy.lanes if lane.name in wanted)

    def _heading_prior(self, heading: str) -> bool:
        lowered = heading.lower()
        return any(prior.lower() in lowered for prior in self._policy.heading_priors)

    def _reason_for(
        self,
        *,
        lane: LanePolicy,
        document_type: str,
        passage_kind: str,
        excluded_reason: str | None,
        has_alias: bool,
        has_ambiguous: bool,
        has_row_label: bool,
        has_heading_prior: bool,
    ) -> str:
        # Kind first: a narrative passage is not a table lane's business whatever its
        # document type, and reporting the document-type reason there would be misleading.
        if passage_kind not in lane.passage_kinds:
            return UNSUPPORTED_PASSAGE_KIND
        if excluded_reason is not None:
            return {
                "contract_boilerplate": CONTRACT_BOILERPLATE,
                "governance_boilerplate": GOVERNANCE_BOILERPLATE,
            }.get(excluded_reason, UNSUPPORTED_DOCUMENT_TYPE)
        if document_type not in lane.document_types:
            return UNSUPPORTED_DOCUMENT_TYPE

        if has_row_label:
            return TABLE_LABEL
        if has_alias:
            return EXACT_ALIAS
        if has_ambiguous:
            return AMBIGUOUS_ALIAS
        if has_heading_prior:
            return HEADING_PRIOR
        require = (self._policy.require_alias_evidence
                   if lane.require_alias_evidence is None
                   else lane.require_alias_evidence)
        if not require:
            return DOCUMENT_TYPE_PRIOR
        return NO_CANDIDATE_SIGNAL

    def _candidate_concepts(self, matched, ambiguous) -> tuple[str, ...]:
        """Everything the lane may consider, including confusion-group siblings.

        Siblings are added, never substituted. A passage mentioning `homes_sold` must let
        the lane see `homes_purchased` too, or it cannot tell that the row in front of it is
        the other one — which is the confusion `distinct_from` exists to prevent.
        """
        concepts: set[str] = set()
        for hit in (*matched, *ambiguous):
            concepts.update(hit.concept_ids)
        siblings = self._aliases.confusion_siblings(self._ontology, tuple(sorted(concepts)))
        return tuple(sorted(concepts | set(siblings)))


def _partition(hits):
    """Only metric hits count as candidate evidence.

    In a single-company corpus the entity aliases match nearly everything — "opendoor",
    "the company". Admitting them selected 6,098 narrative and 928 table candidates, a set
    that has stopped discriminating. Restricting to metric hits gives 3,072 narrative and
    503 table candidates *(measured 2026-08-01)*. Entity hits are still recorded on the
    candidate, because a subject resolver will need them.
    """
    metric_hits = tuple(h for h in hits if h.is_metric)
    matched = tuple(h for h in metric_hits if not h.ambiguous)
    ambiguous = tuple(h for h in metric_hits if h.ambiguous)
    row_labels = tuple(h for h in metric_hits if h.in_row_label and not h.ambiguous)
    return matched, ambiguous, row_labels


def _group_by_document(rows: list[dict]) -> dict[str, dict[int, dict]]:
    grouped: dict[str, dict[int, dict]] = {}
    for row in rows:
        grouped.setdefault(row["document_id"], {})[row["passage_sequence"]] = row
    return grouped


def _preceding_passage_id(by_document: dict[str, dict[int, dict]], row: dict) -> str | None:
    """The passage immediately before, same document.

    Not restricted to the same heading path: the Q1 2025 scale declaration at `#p13` and its
    table at `#p14` share a heading, but relying on that would break the moment a filing
    agent nests them differently, and a wrong scale is worse than a redundant lookup.
    """
    siblings = by_document.get(row["document_id"]) or {}
    return (siblings.get(row["passage_sequence"] - 1) or {}).get("passage_id")
