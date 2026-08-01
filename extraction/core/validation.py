"""Validation the ontology cannot do for us.

`ontology.validate_claims` checks a claim against the vocabulary. It cannot check that the
evidence points at something real, because the ontology has no corpus — `evidence_types`
declare required field *names* and nothing about their grammar, and the example fixtures use
illustrative ids (`doc-…`, `tbl-…`) that do not match the corpus at all. Nothing today would
catch an extractor emitting an anchor that resolves to nothing, so that check lives here.

Four failures fail a run:

1. an evidence id that does not resolve against the passage catalog;
2. a claim whose `source_lane` is in its metric's `forbidden_source_lanes`;
3. `ontology.validate_claims` returning any error;
4. a duplicate observation id carrying a different value.

The fourth is the one that would otherwise pass quietly. Ids are deterministic, so two
identical readings of the same fact collide harmlessly; a collision with a *different* value
means the id scheme has lost a distinction it was supposed to keep.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ontology.core.models import OntologyClaim

EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
FORBIDDEN_SOURCE_LANE = "FORBIDDEN_SOURCE_LANE"
ONTOLOGY_INVALID = "ONTOLOGY_INVALID"
DUPLICATE_OBSERVATION_CONFLICT = "DUPLICATE_OBSERVATION_CONFLICT"
QUOTED_TEXT_NOT_IN_PASSAGE = "QUOTED_TEXT_NOT_IN_PASSAGE"


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    detail: str
    claim_id: str | None = None


@dataclass
class ExtractionValidationResult:
    errors: list[ValidationFinding] = field(default_factory=list)
    warnings: list[ValidationFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _observation(claim: OntologyClaim):
    return claim.metric_observation


def validate_evidence_resolves(
    claims: list[OntologyClaim], passages
) -> list[ValidationFinding]:
    """Every anchor must point at a passage that exists.

    Checked through the `PassageSource` port rather than by importing normalization storage,
    so this is testable against hand-built passages.
    """
    findings: list[ValidationFinding] = []
    for claim in claims:
        references = claim.payload_evidence
        if not references:
            observation = _observation(claim)
            # A calculated observation is re-derivable and carries its formula instead, so
            # absent evidence is only an error for a reported one.
            if observation is None or observation.assertion_type != "calculated":
                findings.append(ValidationFinding(
                    EVIDENCE_MISSING, "claim carries no evidence", claim.claim_id))
            continue
        for reference in references:
            passage_id = reference.passage_id
            if not passage_id:
                findings.append(ValidationFinding(
                    EVIDENCE_MISSING,
                    f"evidence of kind {reference.evidence_kind} has no passage_id",
                    claim.claim_id))
                continue
            if not passages.exists(passage_id):
                findings.append(ValidationFinding(
                    EVIDENCE_UNRESOLVED,
                    f"{passage_id} does not resolve in the passage catalog",
                    claim.claim_id))
    return findings


def validate_quoted_text(claims: list[OntologyClaim], passages) -> list[ValidationFinding]:
    """A quotation must be a quotation.

    A warning rather than an error: whitespace normalisation between the lane's reading and
    the catalog's text can legitimately differ, and failing a run over that would be worse
    than flagging it.
    """
    findings: list[ValidationFinding] = []
    for claim in claims:
        for reference in claim.payload_evidence:
            quoted = (reference.quoted_text or "").strip()
            if not quoted or not reference.passage_id:
                continue
            text = passages.text_of(reference.passage_id)
            if text is not None and quoted not in text:
                findings.append(ValidationFinding(
                    QUOTED_TEXT_NOT_IN_PASSAGE,
                    f"{quoted[:60]!r} not found in {reference.passage_id}",
                    claim.claim_id))
    return findings


def validate_source_lanes(claims: list[OntologyClaim], ontology) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for claim in claims:
        observation = _observation(claim)
        if observation is None:
            continue
        metric = ontology.registry.find(observation.metric_id)
        if metric is None:
            continue
        forbidden = {str(lane) for lane in (getattr(metric, "forbidden_source_lanes", ()) or ())}
        if str(observation.source_lane) in forbidden:
            findings.append(ValidationFinding(
                FORBIDDEN_SOURCE_LANE,
                f"{observation.metric_id} forbids source lane {observation.source_lane}",
                claim.claim_id))
    return findings


def validate_no_conflicting_duplicates(claims: list[OntologyClaim]) -> list[ValidationFinding]:
    seen: dict[str, object] = {}
    findings: list[ValidationFinding] = []
    for claim in claims:
        observation = _observation(claim)
        if observation is None:
            continue
        previous = seen.get(observation.observation_id)
        if previous is None:
            seen[observation.observation_id] = observation.value
        elif previous != observation.value:
            findings.append(ValidationFinding(
                DUPLICATE_OBSERVATION_CONFLICT,
                f"{observation.observation_id} seen with {previous!r} and {observation.value!r}",
                claim.claim_id))
    return findings


def validate(
    claims: list[OntologyClaim], *, ontology, passages
) -> ExtractionValidationResult:
    """Every gate, in one call. Ontology validation last so structural problems surface first."""
    result = ExtractionValidationResult()
    result.errors.extend(validate_evidence_resolves(claims, passages))
    result.errors.extend(validate_source_lanes(claims, ontology))
    result.errors.extend(validate_no_conflicting_duplicates(claims))
    result.warnings.extend(validate_quoted_text(claims, passages))

    ontology_result = ontology.validate_claims(claims)
    for error in getattr(ontology_result, "errors", ()) or ():
        result.errors.append(ValidationFinding(
            ONTOLOGY_INVALID, getattr(error, "message", str(error)),
            getattr(error, "claim_id", None)))
    return result
