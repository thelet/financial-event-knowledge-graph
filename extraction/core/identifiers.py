"""Deterministic identities for extracted claims.

Pure functions, same rule as `normalization/core/identity.py`: derived from stable identity
and structural position, never from randomness, and readable because they surface in
evidence panels.

    observation_id  obs:{metric_id}:{subject}:{period_key}:{lane}:{position_digest12}
    claim_id        claim:{claim_kind}:{payload_digest12}
    event_id        evt:{event_type_id}:{occurred_on}:{position_digest12}
    relationship_instance_id
                    rel:{relationship_id}:{source}:{target}:{passage_digest12}
    entity_id       a readable key for an entity a passage names

`relationship_instance_id` was missing from this list until step 12 — the module documented
three of the four ids it defines. Step 12 is also where `event_id` and
`relationship_instance_id` acquired their first callers and their first tests.

The passage digest is the part that earns its place. Two filings routinely report the same
metric for the same period — a shareholder letter rounds to $38 million what the
reconciliation table states as 38,228 thousand — and an id built only from
(metric, subject, period) would collapse them into one, silently discarding whichever
arrived second. Keeping them distinct is also what makes the restatement question in
ONTOLOGY_V1_IMPLEMENTATION §16.5 decidable later instead of already lost: `SUPERSEDES` is a
graph-layer policy over a complete set of observations, and it needs both to exist.

**That argument was entirely about *cross-passage* collisions, and the corpus contains the
within-passage kind it never covered** *(measured 2026-08-02 on
`extract-v1-lexical-2422c4252c07`, before `structural_position` existed: 2,715 observation
rows carry 2,657 distinct ids — **58 ids describe two grid cells each**, across 12 table
passages)*. One table row reports one metric under several period columns, and the passage
digest cannot tell those columns apart. Four of the 58 carry disagreeing values, e.g.

    obs:adjusted-ebitda:opendoor:2022-01-01_2022-09-30:normalized-table:98849a208451
        183000000.0  row "Adjusted EBITDA", column "2022"
       -211000000.0  row "Adjusted EBITDA", column "September 30, 2022"

`observation_id` therefore digests structural position, not the passage alone — the same
extension `event_id` took when participants entered its digest, and additive in the same way:
an empty `structural_position` reproduces the previous id byte for byte, so a narrative claim,
which has no grid position to state, keeps the id it already had.
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
    structural_position: tuple[str, ...] = (),
) -> str:
    """`obs:{metric}:{subject}:{period}:{lane}:{digest12}` over the reading's position.

    `structural_position` is where the passage was read — for a table, the grid coordinates
    the lane already knows (`LaneClaim.structural_position`). It must be **structural**: a
    grid coordinate, a block id, an ordinal. Never the value, never what a label appears to
    mean, never anything a model produced, and never a counter that depends on the order rows
    happened to be visited. Those would make the id a function of the answer rather than of
    the position, which is the property that lets a second run over the same bytes recompute
    the same id.

    **Empty `structural_position` reproduces the previous digest exactly** — `digest(passage_id)`
    and `digest(*[passage_id])` are the same call — so the extension is additive and every
    narrative-lane id is unchanged by it. This is the discipline `event_id` used when
    `participants` entered its digest; see that docstring.
    """
    return ":".join((
        "obs",
        _slug(metric_id),
        _slug(subject_entity_id),
        period_key,
        _slug(lane),
        digest(passage_id, *structural_position),
    ))


def claim_id(claim_kind: str, payload_id: str) -> str:
    return ":".join(("claim", _slug(claim_kind), digest(payload_id)))


def entity_id(name: str) -> str:
    """A readable key for an entity from the name a passage prints.

    Underscores rather than the hyphens `_slug` produces. Every entity id this vocabulary
    holds is underscored — `opendoor_accountable`, `sec_edgar`, and the ad-hoc ids in the
    committed ontology example fixtures (`counterparty_bank`,
    `facility_2022_senior_revolving`) — so a hyphenated one would be the only id of its shape
    in the graph *(measured 2026-08-02: `registry.definitions.instances` holds four ids and
    none contains a hyphen)*.

    Entity **resolution** is deliberately not attempted. This maps a printed name to a stable,
    readable key and nothing else; deciding that two spellings denote one entity needs a
    corpus-wide pass V1 does not have. Nothing validates an entity id — no ontology constraint
    reads one, only `entity_type` — so an unresolved id is a naming question and not a
    validity one.
    """
    return _slug(name).replace("-", "_")


def unresolved_entity_id(subject_entity_id: str, entity_type: str) -> str:
    """The id of a participant a filing describes by type and never names.

    A filing that names a participant only by description is a real participant with no name, and both alternatives
    are wrong: dropping it loses the fact, and defaulting it to the parent records an
    obligation against the wrong entity. The placeholder is explicit and uniform so a later
    entity-resolution pass can find every one of them by shape rather than by guesswork.
    """
    return f"{entity_id(subject_entity_id)}_unnamed_{entity_id(entity_type)}"


def event_id(
    event_type_id: str,
    occurred_on: str | None,
    passage_id: str,
    participants: tuple[tuple[str, str], ...] = (),
) -> str:
    """`evt:{type}:{date-or-undated}:{digest12}` over the event's structural position.

    **`participants` was added at step 12 because the id collided without it** *(measured
    2026-08-02, before the parameter existed:
    `event_id("executive_change", None, "…ef20055426_ex99-1.htm#p2")` returns
    `evt:executive-change:undated:0365d72eac21` for both events on that passage)*. One 8-K
    reports two executive changes — an incoming chief executive and a returning chairman — in
    two sentences, and the passage states no effective date for either, so
    (type, date, passage) is one triple for both and the two events would have shared an id
    and one of them would have been discarded downstream. Who took part is what distinguishes
    them, which makes it structural position rather than a disambiguating counter.

    Only `occurred_on` appears in the readable segment, and `announced_on` deliberately does
    not. The segment answers "when did this happen"; an event whose passage dates only its
    announcement reads `undated`, which is the true answer, and promoting the announcement
    date into it would be the inference V1 §4.0b forbids.

    Empty `participants` reproduces the previous digest exactly — `digest(passage_id)` and
    `digest(*[passage_id])` are the same call — so the extension is additive.
    """
    parts = [passage_id]
    for role, participant_id in participants:
        parts.extend((_slug(role), _slug(participant_id)))
    return ":".join((
        "evt",
        _slug(event_type_id),
        occurred_on or "undated",
        digest(*parts),
    ))


def relationship_instance_id(
    relationship_id: str, source_id: str, target_id: str, passage_id: str
) -> str:
    """`rel:{predicate}:{source}:{target}:{digest12}`, the endpoints slugged for readability.

    **The endpoint segments are not the entity ids**, and the difference is cosmetic rather
    than structural: `_slug` collapses punctuation to hyphens, so the underscored
    `dana_reyes` reads `dana-reyes` here while the payload carries the underscored form.
    Left alone rather than special-cased — `_slug` is the one readability rule every id in
    this module goes through, and `observation_id` in two committed reports is built with it.
    It cannot collide: every id `entity_id` mints is underscored, so no two entity ids differ
    only by a character `_slug` folds *(noted at step 12, when this function acquired its
    first caller)*.
    """
    return ":".join((
        "rel",
        _slug(relationship_id),
        _slug(source_id),
        _slug(target_id),
        digest(passage_id),
    ))
