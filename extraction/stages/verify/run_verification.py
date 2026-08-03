"""The five checks a run performs on itself.

Three of them delegate to `core.validation`, which already implements them for a list of claims
and has tests of its own; repeating the logic here to make five symmetrical functions would be
two implementations of one rule. What this module adds is the run-level framing: a denominator
for each check, a per-check result, and the two checks `core.validation` cannot do because they
are about the *catalogs* rather than about a claim — duplicate identities across seven files,
and the xbrl-first half of the forbidden-lane rule.

**`forbidden_lanes` is two conditions, not one** (STAGE_13 §5.3). `validate_source_lanes` reads
a metric's `forbidden_source_lanes`, which is a declaration most metrics do not carry. The
second condition is the one the corpus makes interesting: the six metrics whose *first* source
lane preference is `xbrl` must produce no normalized-lane observation at all. `assemble` already
defers them, so a failure here means the deferral was bypassed — which is exactly the kind of
thing a check is worth having for.
"""

from __future__ import annotations

from ...core.assembly import deferred_metric_ids
from ...core.validation import (
    validate_evidence_resolves,
    validate_no_conflicting_duplicates,
    validate_quoted_text,
    validate_source_lanes,
)
from ..catalog.public import CATALOG_FILES, identity_of
from .public import (
    CONFLICTING_DUPLICATES,
    DUPLICATE_IDENTITIES,
    EVIDENCE_RESOLUTION,
    FORBIDDEN_LANES,
    ONTOLOGY_VALIDATION,
    CheckFinding,
    CheckResult,
    VerificationResult,
)

STAGE_NAME = "verify"

DEFERRED_METRIC_EMITTED = "DEFERRED_METRIC_EMITTED"
DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"


def verify(catalogs, *, ontology, passages) -> VerificationResult:
    """All five, in §5's order. Structural problems surface before vocabulary ones."""
    claims = list(catalogs.claims)
    return VerificationResult(checks=[
        _evidence_resolution(claims, passages, ontology),
        _ontology_validation(claims, ontology),
        _forbidden_lanes(claims, ontology),
        _duplicate_identities(catalogs),
        _conflicting_duplicates(claims),
    ])


def _evidence_resolution(claims, passages, ontology) -> CheckResult:
    """Evidence satisfies its kind's contract, and quotations are quotations.

    `ontology` threaded through in F0 Part B: the evidence contract is declared per kind in
    the vocabulary, so the check cannot be made without it.

    The quoted-span half is a *warning*, matching `core.validation`'s own judgement: whitespace
    normalisation between a lane's reading and the catalog's text can legitimately differ, and
    failing a run over that would be worse than flagging it.
    """
    anchors = sum(len(claim.payload_evidence) for claim in claims)
    return CheckResult(
        check=EVIDENCE_RESOLUTION,
        failures=[CheckFinding(f.code, f.detail, f.claim_id)
                  for f in validate_evidence_resolves(
                      claims, passages, ontology=ontology)],
        warnings=[CheckFinding(f.code, f.detail, f.claim_id)
                  for f in validate_quoted_text(claims, passages)],
        examined=anchors)


def _ontology_validation(claims, ontology) -> CheckResult:
    """Every claim through the vocabulary. Warnings are relayed, not dropped.

    `unpreferred_source_lane` — the vocabulary saying a metric it expects from a table arrived
    from prose — is a warning, and review found it being discarded once already. A run that
    counted only errors would report "clean" over claims the ontology had reservations about.
    """
    result = ontology.validate_claims(claims)
    failures = [
        CheckFinding("ONTOLOGY_INVALID", getattr(error, "message", str(error)),
                     getattr(error, "claim_id", None))
        for error in (getattr(result, "errors", ()) or ())
    ]
    warnings = [
        CheckFinding(str(getattr(warning, "code", "ONTOLOGY_WARNING")),
                     getattr(warning, "message", str(warning)),
                     getattr(warning, "claim_id", None))
        for warning in (getattr(result, "warnings", ()) or ())
    ]
    return CheckResult(ONTOLOGY_VALIDATION, failures, warnings, examined=len(claims))


def _forbidden_lanes(claims, ontology) -> CheckResult:
    deferred = deferred_metric_ids(ontology)
    failures = [CheckFinding(f.code, f.detail, f.claim_id)
                for f in validate_source_lanes(claims, ontology)]
    observations = 0
    for claim in claims:
        observation = claim.metric_observation
        if observation is None:
            continue
        observations += 1
        if observation.metric_id in deferred:
            failures.append(CheckFinding(
                DEFERRED_METRIC_EMITTED,
                f"{observation.metric_id} declares an unavailable lane as its first source "
                f"preference and this run emitted a {observation.source_lane} observation "
                "for it",
                claim.claim_id))
    return CheckResult(FORBIDDEN_LANES, failures, examined=observations)


def _duplicate_identities(catalogs) -> CheckResult:
    """No two *distinct* rows share an id, in any catalog.

    Distinct is the load-bearing word. Ids are deterministic, so two identical readings of one
    fact collide harmlessly and are the same row; a collision between rows that differ means
    the id scheme has lost a distinction it was supposed to keep, and one of the two facts
    would be silently discarded by anything that indexes on the id.
    """
    failures: list[CheckFinding] = []
    examined = 0
    for name in CATALOG_FILES:
        rows = catalogs.rows.get(name, ())
        examined += len(rows)
        seen: dict[str, dict] = {}
        for row in rows:
            identity = identity_of(name, row)
            previous = seen.get(identity)
            if previous is None:
                seen[identity] = row
            elif previous != row:
                failures.append(CheckFinding(
                    DUPLICATE_IDENTITY,
                    f"{name}: {identity} describes two different rows",
                    str(row.get("claim_id") or identity)))
    return CheckResult(DUPLICATE_IDENTITIES, failures, examined=examined)


def _conflicting_duplicates(claims) -> CheckResult:
    observations = sum(1 for claim in claims if claim.metric_observation is not None)
    return CheckResult(
        CONFLICTING_DUPLICATES,
        failures=[CheckFinding(f.code, f.detail, f.claim_id)
                  for f in validate_no_conflicting_duplicates(claims)],
        examined=observations)
