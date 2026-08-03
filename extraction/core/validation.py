"""Validation the ontology cannot do for us.

`ontology.validate_claims` checks a claim against the vocabulary. It cannot check that the
evidence points at something real, because the ontology has no corpus — `evidence_types`
declare required field *names* and nothing about their grammar, and the example fixtures use
illustrative ids (`doc-…`, `tbl-…`) that do not match the corpus at all. Nothing today would
catch an extractor emitting an anchor that resolves to nothing, so that check lives here.

Four failures fail a run:

1. evidence that does not satisfy the contract for the kind it declares — a missing required
   field, a field belonging to a different kind, or a passage id that resolves to nothing;
2. a claim whose `source_lane` is in its metric's `forbidden_source_lanes`;
3. `ontology.validate_claims` returning any error;
4. one observation identity carrying two different values.

The fourth is the one that would otherwise pass quietly: one passage, one lane, one metric,
one subject, one period, and two readings that disagree about the number. See
`observation_identity` for why it is keyed on that tuple and pointedly not on the
`observation_id`, which now separates the very rows this check exists to compare.

**The evidence contract is discriminated by kind and read from the vocabulary**
*(2026-08-03, F0 Part B)*. Before that this module required a `passage_id` on every
reference, which made an XBRL fact, a market-data row and a calculated value unrepresentable
even though `EvidenceReference` already declared their fields and `claims.yaml` already
declared two of the kinds. Making `passage_id` merely optional would have traded a blocker
for a silent hole — nothing would then stop a non-filed source from carrying an invented
passage id, which is the one failure the contract exists to prevent. So each kind declares
what it must carry and what it may carry, in `claims.yaml`, and this module hard-codes no
field list at all: see `_EvidenceContract`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from ontology.core.models import OntologyClaim

EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
#: A claim carries no evidence reference at all. Unchanged in meaning: a reference that is
#: present but incomplete is `EVIDENCE_FIELD_MISSING`, which is a different fault.
EVIDENCE_MISSING = "EVIDENCE_MISSING"
#: A reference declares a kind the loaded ontology does not define. Refused rather than
#: waved through: an undeclared kind has no contract, so nothing could check it.
EVIDENCE_KIND_UNKNOWN = "EVIDENCE_KIND_UNKNOWN"
#: A reference omits a field its kind requires.
EVIDENCE_FIELD_MISSING = "EVIDENCE_FIELD_MISSING"
#: A reference populates a field its kind does not declare — one object claiming to be two
#: kinds of evidence at once.
EVIDENCE_KIND_MIXED = "EVIDENCE_KIND_MIXED"
#: A non-filed source carrying a filed-passage anchor. Split out of `EVIDENCE_KIND_MIXED`
#: because it is the specific fabrication F0 §2.2 names, and a run report should be able to
#: count it by itself.
EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND = "EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND"
FORBIDDEN_SOURCE_LANE = "FORBIDDEN_SOURCE_LANE"
ONTOLOGY_INVALID = "ONTOLOGY_INVALID"
DUPLICATE_OBSERVATION_CONFLICT = "DUPLICATE_OBSERVATION_CONFLICT"
QUOTED_TEXT_NOT_IN_PASSAGE = "QUOTED_TEXT_NOT_IN_PASSAGE"
# The ontology said something short of an error. Carried under its own code so a reader can
# tell an evidence warning this module raised from a vocabulary warning it relayed.
ONTOLOGY_WARNING = "ONTOLOGY_WARNING"

#: Never subject to the per-kind field contract: it *is* the discriminator.
_DISCRIMINATOR = "evidence_kind"


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


@dataclass(frozen=True)
class _EvidenceContract:
    """The per-kind evidence contract, read once from the loaded vocabulary.

    Nothing here is a field list written in Python. `required` and `permitted` come from
    `EvidenceTypeDefinition.required_fields` / `optional_fields`, and the two derived sets are
    computed from those declarations rather than enumerated:

    * `passage_kinds` — the kinds that *require* a `passage_id`. A kind that requires one is a
      kind whose evidence lives in the normalized corpus and must resolve against it.
    * `filed_passage_fields` — the fields declared by passage kinds and by no other kind:
      `passage_id`, `table_id`, `block_ids`, `char_start`, `char_end` on the vocabulary as it
      stands. `document_id`, `quoted_text` and `source_url` are deliberately *not* in it —
      an XBRL fact legitimately names a document and quotes text, so treating those as
      filed-passage anchors would refuse honest evidence.

    Built per call rather than cached: a run holds one ontology, the loop below is over
    thousands of references and this is seven small sets, and a module-level cache keyed on an
    ontology object is a way for two ontologies in one process to see each other's rules.
    """

    required: Mapping[str, frozenset[str]]
    permitted: Mapping[str, frozenset[str]]
    passage_kinds: frozenset[str]
    filed_passage_fields: frozenset[str]

    @classmethod
    def of(cls, ontology) -> "_EvidenceContract":
        definitions = ontology.registry.by_category("evidence_type")
        required = {str(d.evidence_kind): frozenset(d.required_fields) for d in definitions}
        permitted = {str(d.evidence_kind): frozenset(d.permitted_fields) for d in definitions}
        passage_kinds = frozenset(
            kind for kind, fields in required.items() if "passage_id" in fields)
        elsewhere: set[str] = set()
        for kind, fields in permitted.items():
            if kind not in passage_kinds:
                elsewhere |= fields
        filed = frozenset().union(*(permitted[k] for k in passage_kinds)) if passage_kinds \
            else frozenset()
        return cls(required=required, permitted=permitted, passage_kinds=passage_kinds,
                   filed_passage_fields=filed - elsewhere)


def _populated(reference) -> frozenset[str]:
    """The fields this reference actually carries.

    `None`, `""` and `()` all count as absent — the catalog writes `null` for an unset string
    and the lane writes `""` for one it read as blank, and a contract that told them apart
    would pass a reference whose `provider` is the empty string.

    `char_start=0` is deliberately *present*: it is a legal offset, and a truthiness test
    would drop the first character of a document.
    """
    carried: set[str] = set()
    for name in type(reference).model_fields:
        if name == _DISCRIMINATOR:
            continue
        value = getattr(reference, name, None)
        if value is None or (isinstance(value, (str, tuple, list, frozenset)) and not value):
            continue
        carried.add(name)
    return frozenset(carried)


def _describe(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))


def validate_evidence_resolves(
    claims: list[OntologyClaim], passages, *, ontology
) -> list[ValidationFinding]:
    """Every reference must satisfy the contract for the kind it declares.

    Passage resolution is checked through the `PassageSource` port rather than by importing
    normalization storage, so this is testable against hand-built passages.

    **`ontology` became required here in F0 Part B.** It was previously implicit: the one rule
    this function knew was "there must be a `passage_id`", which needed no vocabulary. The
    rules are now declared per kind and there is no honest way to check them without reading
    the ontology that declares them — the same argument `deferred_metric_ids` makes. A default
    of `None` would let a caller silently get no checking at all.
    """
    contract = _EvidenceContract.of(ontology)
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
            findings.extend(
                _check_reference(reference, contract, passages, claim.claim_id))
    return findings


def _check_reference(
    reference, contract: _EvidenceContract, passages, claim_id: str
) -> list[ValidationFinding]:
    kind = str(reference.evidence_kind)
    if kind not in contract.permitted:
        return [ValidationFinding(
            EVIDENCE_KIND_UNKNOWN,
            f"evidence kind {kind!r} is not declared by the loaded ontology; "
            f"declared kinds are {_describe(contract.permitted)}",
            claim_id)]

    findings: list[ValidationFinding] = []
    carried = _populated(reference)

    absent = contract.required[kind] - carried
    if absent:
        findings.append(ValidationFinding(
            EVIDENCE_FIELD_MISSING,
            f"evidence of kind {kind} requires {_describe(absent)}",
            claim_id))

    foreign = carried - contract.permitted[kind]
    fabricated = foreign & contract.filed_passage_fields
    if fabricated:
        findings.append(ValidationFinding(
            EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND,
            f"evidence of kind {kind} carries the filed-passage field(s) "
            f"{_describe(fabricated)}; a source that is not a filed passage may not name one",
            claim_id))
    if foreign - fabricated:
        findings.append(ValidationFinding(
            EVIDENCE_KIND_MIXED,
            f"evidence of kind {kind} carries {_describe(foreign - fabricated)}, "
            "which that kind does not declare; one reference is one kind",
            claim_id))

    # The check this module was written for, unchanged: a passage kind's anchor must exist.
    if kind in contract.passage_kinds and reference.passage_id:
        if not passages.exists(reference.passage_id):
            findings.append(ValidationFinding(
                EVIDENCE_UNRESOLVED,
                f"{reference.passage_id} does not resolve in the passage catalog",
                claim_id))
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


def observation_identity(observation) -> tuple:
    """What must agree about a value: (metric, subject, period, lane, passages).

    **Deliberately not the `observation_id`, and this is the whole of the check's survival.**
    It keyed on the id until `observation_id` gained a structural row discriminator
    *(2026-08-02)*, and the two rows of one table row that disagree about a value are exactly
    the rows the discriminator now separates — so keying on the id would have taken this check
    from 4 failures to 0 on the same corpus and called the run clean. A repair that hides the
    defect it was measured against is not a repair.

    This is the identity the id carried *before* the discriminator, so the check means what it
    always meant: one passage, one lane, one metric, one subject, one period, two values.

    Not broadened to (metric, subject, period) alone, which the module docstring's own example
    forbids: a letter rounding to $38 million and a table stating 38,228 thousand are two
    readings of one fact that the id scheme keeps distinct on purpose. *(Measured on
    `extract-v1-lexical-2422c4252c07`: this key flags 4, dropping the passage flags 124.)*
    """
    passages = tuple(sorted({
        str(reference.passage_id or "") for reference in (observation.evidence or ())}))
    return (
        str(observation.metric_id),
        str(observation.subject_entity_id),
        observation.period_start,
        observation.period_end,
        observation.instant_date,
        str(observation.source_lane),
        passages,
        non_passage_anchors(observation),
    )


def non_passage_anchors(observation) -> tuple[tuple[str, ...], ...]:
    """The identity of evidence that names no passage, for kinds that name none.

    Added 2026-08-03 with the discriminated evidence contract. Without it every observation
    whose evidence is an XBRL fact or a market-data row collapses to the single passage key
    `("",)`, and `validate_no_conflicting_duplicates` would then read a restatement — the same
    metric, subject and period tagged with a different value in a later filing — as one
    identity holding two values, and fail the run on a fact that is legitimately two facts.

    **Empty on every one of the 2,717 references in `extract-v1-lexical-2422c4252c07`**
    *(verified 2026-08-03)*, because all of them are `normalized_passage` or
    `normalized_table`. The identity tuple those rows produce is therefore unchanged and the
    check still flags exactly the 4 conflicts it was measured against.
    """
    anchors = set()
    for reference in (observation.evidence or ()):
        if reference.passage_id:
            continue
        anchor = tuple(str(part) for part in (
            reference.evidence_kind, reference.accession or "",
            reference.xbrl_concept or "", reference.source_identity or "",
            reference.row_identity or "", reference.session_date or "",
            reference.source_url or "") if part)
        if len(anchor) > 1:
            anchors.add(anchor)
    return tuple(sorted(anchors))


def validate_no_conflicting_duplicates(claims: list[OntologyClaim]) -> list[ValidationFinding]:
    seen: dict[tuple, tuple[object, str]] = {}
    findings: list[ValidationFinding] = []
    for claim in claims:
        observation = _observation(claim)
        if observation is None:
            continue
        identity = observation_identity(observation)
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = (observation.value, observation.observation_id)
            continue
        previous_value, previous_id = previous
        if previous_value != observation.value:
            # Both ids, because they are no longer the same id. The discriminator is what makes
            # the two readings distinct rows, and a message naming one of them would leave a
            # reader unable to find the other.
            findings.append(ValidationFinding(
                DUPLICATE_OBSERVATION_CONFLICT,
                f"{previous_id} = {previous_value!r} and "
                f"{observation.observation_id} = {observation.value!r} report one "
                "(metric, subject, period) on one passage",
                claim.claim_id))
    return findings


def validate(
    claims: list[OntologyClaim], *, ontology, passages
) -> ExtractionValidationResult:
    """Every gate, in one call. Ontology validation last so structural problems surface first.

    **The ontology's warnings are relayed, not dropped.** Only its errors were copied until
    review found it *(2026-08-02)*, so `unpreferred_source_lane` — the vocabulary saying a
    metric it expects from a table arrived from prose — could never reach a caller. "0 errors"
    was true of the narrative lane's live gate and "clean" was not: four of its claims carried
    that warning and nothing could see them. It is exactly the signal step 11 scores a lane on,
    and a validator that discards the softer half of its own answer teaches a reader to trust a
    number that was never measured.
    """
    result = ExtractionValidationResult()
    result.errors.extend(validate_evidence_resolves(claims, passages, ontology=ontology))
    result.errors.extend(validate_source_lanes(claims, ontology))
    result.errors.extend(validate_no_conflicting_duplicates(claims))
    result.warnings.extend(validate_quoted_text(claims, passages))

    ontology_result = ontology.validate_claims(claims)
    for error in getattr(ontology_result, "errors", ()) or ():
        result.errors.append(ValidationFinding(
            ONTOLOGY_INVALID, getattr(error, "message", str(error)),
            getattr(error, "claim_id", None)))
    for warning in getattr(ontology_result, "warnings", ()) or ():
        result.warnings.append(ValidationFinding(
            ONTOLOGY_WARNING,
            f"{getattr(warning, 'code', '')}: {getattr(warning, 'message', str(warning))}",
            getattr(warning, "claim_id", None)))
    return result
