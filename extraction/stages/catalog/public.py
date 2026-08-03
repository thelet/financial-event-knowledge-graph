"""Public contract for the derived catalogs.

Seven files, each a derived index rebuilt whole from the persisted lane outputs and never
appended to (V1_CLAIM_EXTRACTION §4.6). The per-item record is authoritative; these are joins
over it, and the fact that they can be thrown away and rebuilt byte-identically is the property
STAGE_13 §7 asks a run to demonstrate.

**Every row carries the deterministic id of what it describes** (STAGE_13 §3), and `render`
refuses a row that does not. Step 12 found `event_id` colliding on the real corpus and found
both id fields missing from the durable report; a catalog whose rows cannot be joined on an id
is a report with extra steps.

**No volatile value may enter a row.** No timestamp, no duration, no token count, no absolute
path. `manifest.json` is the one place a run records its environment. `render` cannot check
that for you — it is a rule about what callers put in rows — so `tests/extraction` checks it
over the written files instead, which is where a violation would actually be.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

CLAIMS = "claims.jsonl"
OBSERVATIONS = "observations.jsonl"
EVENTS = "events.jsonl"
RELATIONSHIPS = "relationships.jsonl"
EVIDENCE = "evidence.jsonl"
ISSUES = "issues.jsonl"
REJECTED = "rejected_claims.jsonl"

CATALOG_FILES: tuple[str, ...] = (
    CLAIMS, OBSERVATIONS, EVENTS, RELATIONSHIPS, EVIDENCE, ISSUES, REJECTED)

# What identifies a row in each catalog. One table, so the identity rule is stated once and
# read by `render`, by the duplicate-identity verification and by the tests, rather than
# restated three times in three shapes.
#
# `evidence.jsonl` is the one with a compound key, and STAGE_13 §3 names it: "its claim id,
# plus `passage_id`". One claim may cite more than one passage, so the claim id alone is not an
# identity there — and a table that pretended otherwise would make the duplicate check flag
# every multi-anchor claim as a collision.
#
# **The evidence key became `(claim_id, evidence_index)` on 2026-08-03** (F0 Part B). The
# reason is not a preference: with a discriminated evidence contract, an `xbrl_fact`,
# `market_data` or `calculated` row has **no `passage_id` column at all**, so `render` would
# have refused to write one under `MissingIdentityError`. `evidence_index` is the position the
# reference already occupies on its claim — total for every kind, and still distinguishing two
# references of one claim, which is what STAGE_13 §3 asked `passage_id` for. Two references of
# one claim to *one* passage were previously one identity and are now two, which is the more
# honest answer: they are two rows. Verified 2026-08-03 to leave `evidence.jsonl` byte-identical
# on the current run, where every claim carries exactly one reference at index 0.
IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    CLAIMS: ("claim_id",),
    OBSERVATIONS: ("observation_id",),
    EVENTS: ("event_id",),
    RELATIONSHIPS: ("relationship_instance_id",),
    EVIDENCE: ("claim_id", "evidence_index"),
    ISSUES: ("issue_id",),
    REJECTED: ("rejection_id",),
}


def _identity_part(value: Any) -> str:
    """One identity field as a string, with `0` a value and `None` an absence.

    `str(value or "")` was the same thing until an identity field could be an integer:
    `evidence_index=0` is the first reference on a claim, and truthiness would have erased it
    into the same key as a row with no index at all.
    """
    return "" if value is None else str(value)


def identity_of(name: str, row: dict[str, Any]) -> str:
    """A row's identity as one string, for sorting and for collision detection."""
    return "\x1f".join(_identity_part(row.get(field)) for field in IDENTITY_FIELDS[name])


class MissingIdentityError(ValueError):
    """A catalog row arrived without the id its catalog is keyed by.

    Raised rather than defaulted. An id filled in with an empty string joins to everything and
    to nothing, and the row would look present in every count while being unreachable from any
    evidence panel.
    """


def render(name: str, rows: Sequence[dict[str, Any]]) -> str:
    """Rows as JSONL bytes: sorted, canonical, and refusing a row with no id.

    Sorted by `(id, canonical form)` rather than by id alone. Two *distinct* payloads sharing
    an id is exactly what the duplicate-identity verification exists to find, so the sort has
    to stay total when it happens — otherwise a collision would make the file's bytes depend on
    dict ordering, and the byte-identity proof would fail for a reason that has nothing to do
    with the collision.
    """
    encoded: list[tuple[str, str]] = []
    for row in rows:
        for field_name in IDENTITY_FIELDS[name]:
            # `is None or == ""` rather than falsy: an id of `""` is still the absence this
            # refuses, and `evidence_index=0` is a real position that truthiness would reject.
            value = row.get(field_name)
            if value is None or value == "":
                raise MissingIdentityError(
                    f"a {name} row carries no {field_name}: {sorted(row)[:8]}")
        encoded.append((identity_of(name, row), json.dumps(
            row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))))
    return "".join(line + "\n" for _, line in sorted(encoded))


@dataclass
class CatalogSet:
    """The seven catalogs, plus what building them decided.

    `claims` is kept beside the rows because verification asks the ontology about typed claims
    and asks the rows about identities, and rebuilding one from the other would mean a bug in
    the rebuild could hide a bug in the catalogs.
    """

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    claims: list = field(default_factory=list)
    # Metric ids `assemble` deferred because their first source lane does not exist here.
    deferred_metric_ids: tuple[str, ...] = ()

    def render(self) -> dict[str, str]:
        return {name: render(name, self.rows.get(name, ())) for name in CATALOG_FILES}

    def counts(self) -> dict[str, int]:
        return {name: len(self.rows.get(name, ())) for name in CATALOG_FILES}
