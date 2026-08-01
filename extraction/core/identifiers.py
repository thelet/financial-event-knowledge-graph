"""Deterministic identities for extracted claims.

Pure functions, same rule as `normalization/core/identity.py`: derived from stable identity
and structural position, never from randomness, and readable because they surface in
evidence panels.

    observation_id  obs:{metric_id}:{subject}:{period_key}:{lane}:{passage_digest12}
    claim_id        claim:{claim_kind}:{observation_digest12}
    event_id        evt:{event_type_id}:{occurred_on}:{passage_digest12}

The passage digest is the part that earns its place. Two filings routinely report the same
metric for the same period — a shareholder letter rounds to $38 million what the
reconciliation table states as 38,228 thousand — and an id built only from
(metric, subject, period) would collapse them into one, silently discarding whichever
arrived second. Keeping them distinct is also what makes the restatement question in
ONTOLOGY_V1_IMPLEMENTATION §16.5 decidable later instead of already lost: `SUPERSEDES` is a
graph-layer policy over a complete set of observations, and it needs both to exist.
"""

from __future__ import annotations

import hashlib
import re

DIGEST_CHARS = 12

_SAFE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    """Lowercase, punctuation collapsed to hyphens. Readability, not uniqueness — the
    digest carries uniqueness."""
    return _SAFE.sub("-", str(value).strip().lower()).strip("-")


def digest(*parts: str, length: int = DIGEST_CHARS) -> str:
    """Stable short hash over an ordered part list.

    Parts are joined with a separator that cannot occur in a slug, so ("a", "bc") and
    ("ab", "c") cannot collide.
    """
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def observation_id(
    metric_id: str,
    subject_entity_id: str,
    period_key: str,
    lane: str,
    passage_id: str,
) -> str:
    return ":".join((
        "obs",
        _slug(metric_id),
        _slug(subject_entity_id),
        period_key,
        _slug(lane),
        digest(passage_id),
    ))


def claim_id(claim_kind: str, payload_id: str) -> str:
    return ":".join(("claim", _slug(claim_kind), digest(payload_id)))


def event_id(event_type_id: str, occurred_on: str | None, passage_id: str) -> str:
    return ":".join((
        "evt",
        _slug(event_type_id),
        occurred_on or "undated",
        digest(passage_id),
    ))


def relationship_instance_id(
    relationship_id: str, source_id: str, target_id: str, passage_id: str
) -> str:
    return ":".join((
        "rel",
        _slug(relationship_id),
        _slug(source_id),
        _slug(target_id),
        digest(passage_id),
    ))
