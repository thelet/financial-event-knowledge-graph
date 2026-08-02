"""Running the three lanes over the selected candidates, and recording every outcome.

Routing lives here, in the open, for the reason `typed_selector` gives for owning the other
half of it: a lane that filtered a shared stream by its own private rules would make the
decision invisible. What this module adds is the *per-passage* record — one `PassageOutcome`
for every selected candidate, whether it produced a claim or not — because STAGE_13 §4 asks
for every candidate that produced nothing and the reason, and that is only answerable if the
empty ones are rows.

**A miss is recorded, never skipped.** The narrative and event lanes replay committed answers;
the corpus is far larger than the set of recorded answers, so most candidates have none. Each
of those becomes `NO_STORED_ANSWER` carrying the digest of the request that was not issued
(STAGE_13 §1.1). Silently skipping them is the one behaviour that would make a bounded run
look complete.

**No provider is named, constructed or configured here.** The lanes arrive built. That is what
lets this module be driven by three stubs in a test and by three real lanes in a run, and it is
also what keeps `test_no_stage_can_reach_a_provider_except_the_lane_the_contract_names` green.
"""

from __future__ import annotations

from ...core.models import CandidatePassage
from ..narrative import MissingAnswerError
from ..narrative.prompt import PassageContext
from .public import (
    ANNOUNCEMENT_EQUALS_OCCURRENCE,
    DIAGNOSTIC,
    EVENT_LANE,
    NARRATIVE_LANE,
    NO_STORED_ANSWER,
    NOT_ATTEMPTED,
    REFUSAL,
    REJECTION,
    SILENT_NO_FINDING,
    TABLE_LANE,
    ExtractRequest,
    ExtractResult,
    PassageOutcome,
    RunIssue,
)

STAGE_NAME = "extract"


def passage_context(row: dict) -> PassageContext:
    """The passage's own metadata, exactly as the recorded requests were built from it.

    Every field is read off the catalog row. Adding one, dropping one, or defaulting one
    differently changes the prompt, therefore the request digest, therefore whether a recorded
    answer is reachable at all — so this is a single function rather than a construction
    repeated per lane.
    """
    return PassageContext(
        passage_id=row["passage_id"],
        document_type=str(row.get("document_type") or ""),
        form=row.get("form"),
        filing_date=row.get("filing_date"),
        heading_path=tuple(row.get("heading_path") or ()),
    )


class LaneRunner:
    """Runs whichever lanes it was given over whichever candidates it was given."""

    name = STAGE_NAME

    def __init__(
        self,
        corpus,
        *,
        table_lane=None,
        narrative_lane=None,
        event_lane=None,
        progress=None,
    ) -> None:
        self._corpus = corpus
        self._lanes = {
            TABLE_LANE: table_lane,
            NARRATIVE_LANE: narrative_lane,
            EVENT_LANE: event_lane,
        }
        self._progress = progress

    def run(self, request: ExtractRequest, candidates: list[CandidatePassage]) -> ExtractResult:
        result = ExtractResult()
        wanted = set(request.lanes)
        documents = set(request.document_ids) if request.document_ids else None

        for lane in (lane for lane in (TABLE_LANE, NARRATIVE_LANE, EVENT_LANE)
                     if lane in wanted and self._lanes.get(lane) is not None):
            selected = [
                candidate for candidate in candidates
                if candidate.lane == lane and candidate.selected
                and (documents is None or candidate.document_id in documents)
            ]
            selected.sort(key=lambda candidate: candidate.passage_id)
            if request.limit is not None:
                selected = selected[:request.limit]
            for index, candidate in enumerate(selected, start=1):
                self._one(lane, candidate, result)
                if self._progress is not None:
                    self._progress(lane, index, len(selected))

        for candidate in candidates:
            if candidate.selected or candidate.lane not in wanted:
                continue
            key = f"{candidate.lane}:{candidate.reason}"
            result.unselected[key] = result.unselected.get(key, 0) + 1
        result.unselected = dict(sorted(result.unselected.items()))
        return result

    # -- one candidate -------------------------------------------------------------------------

    def _one(self, lane: str, candidate: CandidatePassage, result: ExtractResult) -> None:
        row = self._corpus.row(candidate.passage_id) or {}
        text = str(row.get("text") or "")
        before = len(result.claims), len(result.events), len(result.relationships)
        codes: list[str] = []
        digest_seen: str | None = None
        issued = False
        scope_size: int | None = None

        if lane == TABLE_LANE:
            codes = self._tables(candidate, row, text, result)
        else:
            digest_seen, issued, codes, scope_size = self._narrative_or_events(
                lane, candidate, row, text, result)

        claims = len(result.claims) - before[0]
        events = len(result.events) - before[1]
        relationships = len(result.relationships) - before[2]
        if not (claims or events or relationships or codes):
            codes = [SILENT_NO_FINDING]
            result.issues.append(RunIssue(
                code=SILENT_NO_FINDING, severity=REFUSAL, lane=lane,
                passage_id=candidate.passage_id, document_id=candidate.document_id,
                document_type=candidate.document_type,
                detail="the lane recorded neither a payload nor a reason for this passage",
                request_sha256=digest_seen))

        result.outcomes.append(PassageOutcome(
            passage_id=candidate.passage_id,
            document_id=candidate.document_id,
            document_type=candidate.document_type,
            lane=lane,
            selection_reason=candidate.reason,
            claims=claims,
            events=events,
            relationships=relationships,
            issue_codes=tuple(sorted(set(codes))),
            request_sha256=digest_seen,
            request_issued=issued,
            scope_size=scope_size,
        ))

    def _tables(self, candidate, row, text, result: ExtractResult) -> list[str]:
        preceding = candidate.preceding_passage_id
        extraction = self._lanes[TABLE_LANE].extract_table(
            text,
            passage_id=candidate.passage_id,
            document_id=candidate.document_id,
            table_id=row.get("table_id"),
            block_ids=tuple(row.get("block_ids") or ()),
            preceding_text=self._corpus.text_of(preceding) if preceding else None,
            preceding_passage_id=preceding,
        )
        result.claims.extend(extraction.claims)
        for issue in extraction.issues:
            result.issues.append(RunIssue(
                code=issue.code, severity=REFUSAL, lane=TABLE_LANE,
                passage_id=issue.passage_id, document_id=candidate.document_id,
                document_type=candidate.document_type, detail=issue.detail,
                concept_ids=tuple(issue.candidate_concept_ids),
                row_label=issue.raw_label))
        return [issue.code for issue in extraction.issues]

    def _narrative_or_events(self, lane, candidate, row, text, result: ExtractResult):
        """Both model-backed lanes, because the miss path is the same on both.

        The two differ only in what they emit and which method reads the passage, and folding
        the shared half here is what keeps `NO_STORED_ANSWER` one behaviour rather than two
        that could drift.
        """
        context = passage_context(row)
        try:
            extraction = self._lanes[lane].extract_passage(
                text, context=context, document_id=candidate.document_id)
        except MissingAnswerError as miss:
            result.issues.append(RunIssue(
                code=NO_STORED_ANSWER, severity=NOT_ATTEMPTED, lane=lane,
                passage_id=candidate.passage_id, document_id=candidate.document_id,
                document_type=candidate.document_type, detail=str(miss),
                request_sha256=miss.request_sha256 or None))
            return (miss.request_sha256 or None, False, [NO_STORED_ANSWER],
                    getattr(miss, "scope_size", None))

        if lane == NARRATIVE_LANE:
            result.claims.extend(extraction.claims)
            for issue in extraction.issues:
                result.issues.append(RunIssue(
                    code=issue.code,
                    severity=REJECTION if issue.rejected_claim else REFUSAL,
                    lane=lane, passage_id=issue.passage_id,
                    document_id=candidate.document_id,
                    document_type=candidate.document_type, detail=issue.detail,
                    concept_ids=tuple(issue.metric_ids), quoted_span=issue.quoted_span,
                    request_sha256=extraction.request_sha256,
                    rejected_claim=issue.rejected_claim, raw_finding=issue.raw_finding))
        else:
            result.events.extend(extraction.events)
            result.relationships.extend(extraction.relationships)
            for issue in extraction.issues:
                result.issues.append(RunIssue(
                    code=issue.code,
                    severity=REJECTION if issue.rejected_claim else REFUSAL,
                    lane=lane, passage_id=issue.passage_id,
                    document_id=candidate.document_id,
                    document_type=candidate.document_type, detail=issue.detail,
                    concept_ids=tuple(issue.event_type_ids), quoted_span=issue.quoted_span,
                    request_sha256=extraction.request_sha256,
                    rejected_claim=issue.rejected_claim, raw_finding=issue.raw_finding))
            for event in extraction.events:
                diagnostic = temporal_residual_issue(
                    event, document_type=candidate.document_type)
                if diagnostic is not None:
                    result.issues.append(diagnostic)

        codes = [issue.code for issue in extraction.issues]
        return (extraction.request_sha256,
                extraction.request_sha256 is not None,
                codes,
                getattr(extraction, "scope_size", None))


def temporal_residual_issue(event, *, document_type: str) -> RunIssue | None:
    """STAGE_13 §4.1, recorded rather than fixed.

    Both dates present and equal is the shape the step 12 residual takes: on a non-gold
    workforce reduction the model answered both date questions with one printed phrase from a
    sentence that says only "announced". It is **not proof** — a change can be announced the day
    it takes effect — so this is `DIAGNOSTIC` and nothing is refused. The phrase each field was
    read from stays on the event in `events.jsonl`, and the report names the general
    announcement-versus-occurrence verifier as follow-up.
    """
    if not event.occurred_on or event.occurred_on != event.announced_on:
        return None
    return RunIssue(
        code=ANNOUNCEMENT_EQUALS_OCCURRENCE, severity=DIAGNOSTIC, lane=EVENT_LANE,
        passage_id=event.passage_id, document_id=event.document_id,
        document_type=document_type,
        detail=(f"{event.event_type_id} carries occurred_on and announced_on both "
                f"{event.occurred_on}; a review flag, not an error — a change can be "
                "announced the day it takes effect"),
        concept_ids=(event.event_type_id,),
        quoted_span=event.raw_text or None)
