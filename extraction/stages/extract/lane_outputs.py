"""The durable record of what the lanes produced, and the only reader of it.

STAGE_13 §7: the run writes lane outputs, and the catalogs are built **from those persisted
outputs**. That split is what makes the byte-identity claim checkable rather than asserted —
two builds read the same bytes off disk instead of sharing an object in memory — and it is
what lets `rebuild` and `report` be CLI verbs against a finished directory.

One file, tagged rows, because the five row kinds are produced together and consumed together;
five files would be five orderings to keep in step and one join to write. The order is the
order the run visited passages, which is deterministic (candidates sorted by passage id, lanes
in a fixed sequence) — so this file is itself byte-identical across two runs of the same
inputs, and the catalogs it feeds are byte-identical for a reason that does not depend on the
catalogs' own sorting.

Nothing volatile is written here. No timestamp, no duration, no token count, no latency and no
absolute path: `GenerationStats` exists precisely so those stay off every payload, and a record
containing one could not be byte-identical (STAGE_09 §11.2).
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ...core.models import LaneClaim, LaneEvent, LaneRelationship
from .public import ExtractResult, PassageOutcome, RunIssue

FILENAME = "lane_outputs.jsonl"

CLAIM = "claim"
EVENT = "event"
RELATIONSHIP = "relationship"
ISSUE = "issue"
OUTCOME = "outcome"
UNSELECTED = "unselected"
KINDS: tuple[str, ...] = (CLAIM, EVENT, RELATIONSHIP, ISSUE, OUTCOME, UNSELECTED)


def _line(kind: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {"kind": kind, **payload}, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")) + "\n"


def render(result: ExtractResult) -> str:
    """The file's exact bytes. Public so a test can compare two runs without a filesystem."""
    parts: list[str] = []
    parts.extend(_line(CLAIM, claim.model_dump(mode="json")) for claim in result.claims)
    parts.extend(_line(EVENT, event.model_dump(mode="json")) for event in result.events)
    parts.extend(_line(RELATIONSHIP, edge.model_dump(mode="json"))
                 for edge in result.relationships)
    parts.extend(_line(ISSUE, issue.as_row()) for issue in result.issues)
    parts.extend(_line(OUTCOME, outcome.as_row()) for outcome in result.outcomes)
    parts.extend(_line(UNSELECTED, {"key": key, "passages": count})
                 for key, count in sorted(result.unselected.items()))
    return "".join(parts)


def parse(lines: Iterable[str]) -> ExtractResult:
    """The file back as an `ExtractResult`. The inverse of `render`, and tested as one.

    Unknown kinds raise rather than being ignored. A file written by a later layout and read
    by this one would otherwise present as a run that produced less, which is the failure mode
    a tagged format exists to prevent.
    """
    result = ExtractResult()
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        kind = row.pop("kind")
        if kind == CLAIM:
            result.claims.append(LaneClaim.model_validate(row))
        elif kind == EVENT:
            result.events.append(LaneEvent.model_validate(row))
        elif kind == RELATIONSHIP:
            result.relationships.append(LaneRelationship.model_validate(row))
        elif kind == ISSUE:
            result.issues.append(RunIssue(
                code=row["code"], severity=row["severity"], lane=row["lane"],
                passage_id=row["passage_id"], document_id=row["document_id"],
                document_type=row["document_type"], detail=row["detail"],
                concept_ids=tuple(row.get("concept_ids") or ()),
                row_label=row.get("row_label"), quoted_span=row.get("quoted_span"),
                request_sha256=row.get("request_sha256"),
                rejected_claim=bool(row.get("rejected_claim")),
                raw_finding=row.get("raw_finding")))
        elif kind == OUTCOME:
            result.outcomes.append(PassageOutcome(
                passage_id=row["passage_id"], document_id=row["document_id"],
                document_type=row["document_type"], lane=row["lane"],
                selection_reason=row["selection_reason"], claims=int(row["claims"]),
                events=int(row["events"]), relationships=int(row["relationships"]),
                issue_codes=tuple(row.get("issue_codes") or ()),
                request_sha256=row.get("request_sha256"),
                request_issued=bool(row.get("request_issued")),
                scope_size=row.get("scope_size")))
        elif kind == UNSELECTED:
            result.unselected[row["key"]] = int(row["passages"])
        else:
            raise ValueError(f"unknown lane output kind {kind!r}; known: {KINDS}")
    return result
