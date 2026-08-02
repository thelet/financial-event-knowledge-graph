"""Node and edge keys: whose id the graph uses, and the one place it mints its own.

Node keys are the extraction ids unchanged (§4.1) — readable, because they surface in Browser
captions and evidence panels, and a graph keyed on UUIDs would be unusable in exactly the
tool this milestone optimises for. There is **one** deviation, §4.2's unresolved-entity rule,
and it lives here rather than inside a builder precisely because it is the piece most likely
to be argued about.

Nothing here reads a file, a catalog or a database. It takes ids and returns ids.
"""

from __future__ import annotations

from typing import Sequence

from extraction.core.identifiers import digest

from .inputs import EventRow, GraphInputError, IssueRow, RejectedClaimRow, RelationshipRow

#: The only placeholder signal that survives into the catalogs. `LaneEventParticipant.named`
#: and `entity_text` reach no catalog (§1.4a), so shape-matching the id is not a shortcut —
#: it is the sole available mechanism, and the uniform placeholder exists to provide it
#: (`extraction/core/identifiers.py:125-133`).
PLACEHOLDER_MARKER = "_unnamed_"

#: Separates the extraction placeholder id from the event that scopes it. `#` is already the
#: passage/document separator in the normalization id grammar, so it reads as "within".
UNRESOLVED_SEPARATOR = "#"


class PlaceholderAttributionError(GraphInputError):
    """A placeholder endpoint cannot be attributed to exactly one event.

    §4.2 keys an unresolved entity per event. A relationship endpoint that names a
    placeholder must therefore inherit the key of the event it belongs to — and if the run
    does not say which event that is, the honest answer is to stop. Guessing would attach a
    borrowing obligation to whichever event happened to sort first.
    """


class NodeKeyError(GraphInputError):
    """An id that cannot be a node key."""


def require_node_key(value: str | None, *, what: str) -> str:
    """`""` and `None` are legal catalog values and are never node keys (§2.3 trap 2)."""
    if value is None or not value.strip():
        raise NodeKeyError(f"{what} is empty; an empty id is never a node key")
    return value


def is_placeholder(entity_id: str) -> bool:
    """True for an id `unresolved_entity_id()` minted — `{subject}_unnamed_{type}`."""
    return PLACEHOLDER_MARKER in entity_id


def unresolved_node_key(extraction_entity_id: str, event_id: str) -> str:
    """§4.2: one placeholder node per event, never one per corpus.

    Extraction is per-passage and its placeholder is correct at that scale. A graph is
    corpus-scale, and at that scale one shared string asserts that every unnamed subsidiary
    in the corpus is one company — which would accumulate everyone's debt on one node, the
    mis-attribution the placeholder was invented to prevent. Over-splitting is visible and
    countable; over-merging is silent. The extraction id is kept verbatim as the
    `extraction_entity_id` property so a later resolution pass can still find every one.
    """
    require_node_key(extraction_entity_id, what="extraction_entity_id")
    require_node_key(event_id, what="event_id")
    return f"{extraction_entity_id}{UNRESOLVED_SEPARATOR}{event_id}"


def entity_node_key(entity_id: str, *, event_id: str | None = None) -> str:
    """The key for an entity node: the extraction id, or §4.2's per-event key.

    `event_id` is required when the id is a placeholder and ignored otherwise, so a caller
    that always passes the owning event still gets stable keys for named entities.
    """
    require_node_key(entity_id, what="entity_id")
    if not is_placeholder(entity_id):
        return entity_id
    if event_id is None:
        raise PlaceholderAttributionError(
            f"{entity_id!r} is a placeholder and needs the event that scopes it (§4.2)")
    return unresolved_node_key(entity_id, event_id)


def owning_event_id(entity_id: str, events: Sequence[EventRow]) -> str:
    """The single event a placeholder participates in, or an error.

    Matching is on participation, not on the passage: an event is what §4.2 scopes a
    placeholder to, and two events on one passage each get their own node.
    """
    matches = sorted({event.event_id for event in events
                      if any(p.entity_id == entity_id for p in event.participants)})
    if len(matches) == 1:
        return matches[0]
    raise PlaceholderAttributionError(
        f"{entity_id!r} participates in {len(matches)} events {matches[:5]}; "
        "§4.2 needs exactly one to key it by")


def relationship_endpoint_key(
    entity_id: str, relationship: RelationshipRow, events: Sequence[EventRow]
) -> str:
    """The key for one endpoint of a relationship claim.

    A placeholder endpoint must land on **the same node** as the event it belongs to — the
    unnamed borrower of `credit_facility_established` and the `source_id` of `BORROWS_UNDER`
    are one entity, and keying them apart would split one fact into two disconnected halves.
    Candidate events are narrowed to the relationship's own passage first: a placeholder is a
    per-passage assertion, so an event on another filing is not a candidate at all.
    """
    require_node_key(entity_id, what="relationship endpoint")
    if not is_placeholder(entity_id):
        return entity_id
    on_passage = [event for event in events if event.passage_id == relationship.passage_id]
    return unresolved_node_key(entity_id, owning_event_id(entity_id, on_passage))


# -- the remaining node keys: extraction's ids, validated, not rewritten ----------------


def observation_node_key(observation_id: str) -> str:
    return require_node_key(observation_id, what="observation_id")


def event_node_key(event_id: str) -> str:
    return require_node_key(event_id, what="event_id")


def metric_node_key(metric_id: str) -> str:
    return require_node_key(metric_id, what="metric_id")


def passage_node_key(passage_id: str) -> str:
    return require_node_key(passage_id, what="passage_id")


def document_node_key(document_id: str) -> str:
    return require_node_key(document_id, what="document_id")


def issue_node_key(issue_id: str) -> str:
    return require_node_key(issue_id, what="issue_id")


def issue_row_key(row: IssueRow | RejectedClaimRow) -> str:
    """The `:Issue` key of an `issues.jsonl` row, or of a rejection nothing mirrors (§4.3).

    An `assemble`-origin refusal writes **no** issue row (`jsonl_catalog.py:98-121`), so it
    has no `issue_id` and its `rejection_id` is the only id it has. Both files mint their ids
    with the same `_ordinal_id` over the same parts, so the two id spaces cannot collide.
    Stated here rather than in each of the two stages that need it, because a node key
    invented in one stage and referenced by the other is exactly what `check_endpoints`
    exists to catch — and never having to catch it is better.
    """
    issue_id = getattr(row, "issue_id", None)
    if isinstance(issue_id, str):
        return require_node_key(issue_id, what="issue_id")
    return require_node_key(getattr(row, "rejection_id", None), what="rejection_id")


def document_key_of_passage(passage_id: str) -> str:
    """`norm:{cik}:{accession}:{file}#p{n}` → `norm:{cik}:{accession}:{file}` (§4.1).

    The document id is the passage id without its `#p{n}` suffix, which is why `PART_OF` can
    be derived without a catalog lookup. A passage id carrying no suffix is a malformed id
    rather than a document id, and says so.
    """
    require_node_key(passage_id, what="passage_id")
    document_id, separator, _ = passage_id.partition("#")
    if not separator:
        raise NodeKeyError(f"{passage_id!r} has no '#p{{n}}' suffix; not a passage id")
    return require_node_key(document_id, what="document_id")


# -- edge keys -------------------------------------------------------------------------


def relationship_edge_key(relationship_instance_id: str) -> str:
    """A relationship claim is keyed on the id the run supplies (§4.1).

    The loader `MERGE`s on this, never on the endpoint pair: two filings asserting
    `BORROWS_UNDER` between one pair are two evidenced assertions, and collapsing them would
    discard a citation.
    """
    return require_node_key(relationship_instance_id, what="relationship_instance_id")


def derived_edge_key(
    edge_type: str, source_key: str, target_key: str, *discriminators: str
) -> str:
    """`{TYPE}:{source}:{target}`, plus a digest when the triple is not unique.

    The rule, stated once so every builder applies it the same way:

    * an edge whose (type, source, target) triple can occur at most once — `HAS_OBSERVATION`,
      `EVIDENCED_BY`, `PART_OF` — is keyed on the triple alone and stays readable;
    * an edge that can legitimately repeat between one pair — `PARTICIPATES_IN`, where one
      entity may hold two roles in one event — passes the fields that distinguish it, and the
      key gains a 12-character digest of them. Structural discriminators only: a role, an
      ordinal, a claim id. Never a value, never anything a model produced (the discipline
      `observation_id` states at `extraction/core/identifiers.py:77-90`).

    The digest is appended rather than replacing the triple so the readable part survives.
    """
    require_node_key(edge_type, what="edge type")
    require_node_key(source_key, what="edge source_key")
    require_node_key(target_key, what="edge target_key")
    key = f"{edge_type}:{source_key}:{target_key}"
    if discriminators:
        return f"{key}:{digest(*discriminators)}"
    return key


__all__ = [
    "NodeKeyError",
    "PLACEHOLDER_MARKER",
    "PlaceholderAttributionError",
    "UNRESOLVED_SEPARATOR",
    "derived_edge_key",
    "document_key_of_passage",
    "document_node_key",
    "entity_node_key",
    "event_node_key",
    "is_placeholder",
    "issue_node_key",
    "issue_row_key",
    "metric_node_key",
    "observation_node_key",
    "owning_event_id",
    "passage_node_key",
    "relationship_edge_key",
    "relationship_endpoint_key",
    "require_node_key",
    "unresolved_node_key",
]
