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

**A lane with neither a passage nor a position had no identity at all, and the module said so
by returning a constant** *(verified 2026-08-03: `digest()` over no parts, and over only blank
parts, is `e3b0c44298fc`)*. An XBRL fact has no passage and no grid, so every XBRL reading of
one `(metric, subject, period, lane)` minted `obs:…:xbrl:e3b0c44298fc` — one id for the
original 10-K, the next year's comparative and the year after's, which is the "silently
discarding whichever arrived second" failure this module exists to prevent, arriving through
the one door it had left open. Two changes close it, F0 §3:

* `digest` **refuses** an input whose parts are all empty rather than minting the constant. A
  lane that supplies no discriminator is a lane whose ids are not unique, and an exception at
  the point of minting is cheaper than a duplicate found in a catalog;
* `xbrl_structural_position` states what an XBRL fact supplies *instead of* a grid coordinate —
  the filing it was tagged in, and the fact's coordinates within it.

The XBRL lane is not implemented here (F1/F2 own it). What is implemented is the identity
contract it must use, so that it cannot be written against the collision.
"""

from __future__ import annotations

import hashlib
import re
# `typing`, not `collections.abc`: the narrative lane's import allowlist
# (`tests/extraction/test_narrative_lane.py:1644`) admits the first and not the second, and
# widening a guard that exists to keep a wire format out of the lane is not worth a spelling
# preference. Caught by that test, which is what it is for.
from typing import Iterable, Mapping, Sequence

DIGEST_CHARS = 12

#: The version of the *identity rule*, not of the code around it.
#:
#: 1.0.0 is the unversioned rule that minted every id in `extract-v1-lexical-2422c4252c07`:
#: `digest(passage_id, *structural_position)`, with the grid discriminator added at step 12.
#: 1.1.0 is this module: the empty-digest refusal and the XBRL structural position. **Minor,
#: because it is additive** — every one of that run's 2,707 observation ids recomputes byte for
#: byte under it *(verified 2026-08-03 via `graph.core.derivation.mismatched_observation_ids`
#: over the full run: 2,707 recomputed, 0 mismatches, id-set sha256 unchanged at
#: `7e31fb6d867e…`)*. 1.1.0 mints ids 1.0.0 could not mint; it changes none that it could.
#:
#: **Deliberately not a digest part.** Folding the version into the hash would change all
#: 2,707 ids, which is exactly what F0 §3.3 forbids, and would make every future clarification
#: of this file a corpus rebuild. It is a declaration for a manifest to record, not an input.
OBSERVATION_IDENTITY_VERSION = "1.1.0"

_SAFE = re.compile(r"[^a-z0-9]+")


class EmptyIdentityError(ValueError):
    """An id was asked for from inputs that discriminate nothing.

    Raised rather than defaulted, for the same reason `MissingIdentityError` is raised in the
    catalog stage: an id that every caller with no information shares is not an id. It joins
    every such row to every other, and whichever row is written second wins.
    """


def _all_blank(parts: Iterable[object]) -> bool:
    """True when nothing in `parts` carries a character. Empty input included."""
    return all(str(part).strip() == "" for part in parts)


def _slug(value: str) -> str:
    """Lowercase, punctuation collapsed to hyphens. Readability, not uniqueness — the
    digest carries uniqueness."""
    return _SAFE.sub("-", str(value).strip().lower()).strip("-")


def digest(*parts: str, length: int = DIGEST_CHARS) -> str:
    """Stable short hash over an ordered part list.

    Parts are joined with a separator that cannot occur in a slug, so ("a", "bc") and
    ("ab", "c") cannot collide.

    **Refuses an input that carries nothing** — no parts, or parts that are all empty or
    whitespace. Both are `sha256("")` and both used to return `e3b0c44298fc`, a constant that
    reads like an identity and is the absence of one.

    The test is *all* parts, not any: `digest("", "accession=…")` is a legitimate call, and it
    is the shape the XBRL lane makes — no passage, but a position. One blank part beside a
    real one still discriminates, and blanks are how an absent optional component keeps its
    slot (`fy=` below) instead of shifting every part after it.
    """
    if _all_blank(parts):
        raise EmptyIdentityError(
            f"digest() over {len(parts)} part(s) that carry no characters: an id built from "
            "this is the constant every empty input shares, not an identity. The lane must "
            "supply a discriminator — a passage, a grid coordinate, an XBRL position.")
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def observation_id(
    metric_id: str,
    subject_entity_id: str,
    period_key: str,
    lane: str,
    passage_id: str | None,
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

    `passage_id` may be `None` — an XBRL fact is tagged in an instance document, not read out
    of a normalized passage, and F0 §2.2 makes fabricating a passage id for it the specific
    failure the evidence contract exists to prevent. A lane with no passage must then carry the
    whole discriminator in `structural_position`; supplying neither raises
    `EmptyIdentityError`, and the refusal is precisely the collision in F0 §3.1.

    The empty passage keeps its slot in the digest rather than being dropped, so an XBRL
    position `(p,)` cannot collide with a passage id that happens to read `p`.
    """
    parts = (passage_id or "", *structural_position)
    if _all_blank(parts):
        raise EmptyIdentityError(
            f"{metric_id}/{subject_entity_id}/{period_key} on lane {lane!r}: no passage_id and "
            "no structural_position, so every reading of this metric, subject, period and lane "
            "would mint one id. A lane with no passage must state its own position — see "
            "`xbrl_structural_position`.")
    return ":".join((
        "obs",
        _slug(metric_id),
        _slug(subject_entity_id),
        period_key,
        _slug(lane),
        digest(*parts),
    ))


def xbrl_structural_position(
    *,
    accession: str,
    concept: str,
    fiscal_year: str | int | None = None,
    fiscal_period: str | None = None,
    context_id: str | None = None,
    unit: str | None = None,
    dimensions: Mapping[str, str] | Sequence[tuple[str, str]] = (),
) -> tuple[str, ...]:
    """Where an XBRL fact sits, as ordered digest parts — the grid coordinates of a filing.

    This is the same mechanism `LaneClaim.structural_position` uses and not a second identity
    system: an ordered tuple of self-describing parts, fed to `observation_id`, derived only
    from where the fact was tagged and never from what it says.

    ::

        ("accession=0001801169-22-000027", "fy=2021", "fp=FY",
         "concept=us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
         "context=", "unit=USD")

    **`accession` is what makes the FY2020 revenue triple three observations.** The plan needs
    2,583,121,000 from the FY2020 10-K (`0001801169-21-000011`), 2,583,000,000 from the FY2021
    10-K's comparative column (`-22-000027`) and 2,583,000,000 again from the FY2022 10-K
    (`-23-000024`) to exist as three rows: same metric, same subject, same period, same lane,
    three filings. Accession separates them, and it separates an amendment from what it amends
    for the same reason — a `10-K/A` is filed under its own accession, so no `form` part is
    needed to tell the two apart.

    The remaining parts:

    * `fy`/`fp` — the *filing's* fiscal frame, which is not the fact's period. Companyfacts
      states both beside every fact, and they differ exactly for a comparative: the FY2021 10-K
      reports FY2020 revenue with `fy=2021, fp=FY` *(unverified — F1 owns acquisition and no
      Companyfacts fixture is committed yet; this is the API's documented fiscal-year and
      fiscal-period focus, not a measurement)*. Carried because they are the filing coordinate
      a reader needs to see why two ids differ, and because a filing can restate one period in
      two frames. Absent, they are empty and accession alone still discriminates.
    * `concept` — two concepts can map to one metric (`Revenues` and
      `RevenueFromContractWithCustomerExcludingAssessedTax`), and V1 §2.5 requires the
      extension concept and the standard one to be separate readings, not one overwritten.
    * `context` — the instance document's `contextRef`. **Empty for a Companyfacts fact, and
      that is correct rather than missing**: the Companyfacts API states `start`/`end`/`accn`/
      `fy`/`fp`/`form`/`filed`/`frame` and no context id, having already collapsed contexts to
      the consolidated dimensionless series *(unverified, same reason as `fy`/`fp`)*.
      Inline-XBRL parsing (F1) has contexts and fills it. Requiring it here would have blocked
      the Companyfacts lane, which is why it is optional and accession is not.
    * `unit` — an XBRL fact is identified by concept, context *and* unit (the spec's own
      duplicate-fact rule), so a per-share and a total tagged under one concept and context are
      two facts.
    * `dimensions` — one `dim:{axis}={member}` part per axis, **sorted by axis**, so a
      dimensional identity read out of a dict, a list or a shuffled list is one tuple. Absent
      dimensions add no parts: the dimensionless Companyfacts series keeps the shortest tuple,
      and a segmented fact can never collide with it because it carries parts the other lacks.

    Absent optional components render as an empty value (`fy=`) rather than being dropped, so
    the tuple's shape is fixed and "the source did not state one" cannot be confused with a
    neighbouring component shifting into the slot.

    Values are used verbatim, not `_slug`ged: `us-gaap:Revenues` and `dei:Revenues` differ by
    a character `_slug` folds, and an accession's hyphens are its format. `_slug` is for
    readable segments, and none of these appear in one.

    Rejected: the instance document's own `id` attribute on the fact element. It is unique
    within one rendering and not stable across re-renderings of the same filing, so an id built
    on it would fail to reproduce on a second parse — the property every other id here has.
    """
    accession = str(accession).strip()
    concept = str(concept).strip()
    if not accession or not concept:
        raise EmptyIdentityError(
            "an XBRL structural position needs at least the filing it was tagged in and the "
            f"concept it was tagged as; got accession={accession!r}, concept={concept!r}")

    pairs = (tuple(dimensions.items()) if isinstance(dimensions, Mapping)
             else tuple(dimensions))
    return (
        f"accession={accession}",
        f"fy={'' if fiscal_year is None else str(fiscal_year).strip()}",
        f"fp={'' if fiscal_period is None else str(fiscal_period).strip()}",
        f"concept={concept}",
        f"context={'' if context_id is None else str(context_id).strip()}",
        f"unit={'' if unit is None else str(unit).strip()}",
        *(f"dim:{str(axis).strip()}={str(member).strip()}"
          for axis, member in sorted(pairs, key=lambda pair: (str(pair[0]), str(pair[1])))),
    )


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
