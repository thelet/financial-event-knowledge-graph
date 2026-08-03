"""Evidence that names no filed passage, as a node the graph can point at (F0 §2.3).

Passage evidence keeps `Fact -[:EVIDENCED_BY]-> Passage -[:PART_OF]-> Document`, unchanged
and unweakened. The five other `EvidenceKind` members name no passage and never will — an
XBRL fact is located by accession and concept, a market row by provider and session, a
derivation by its inputs — so each gets an `:EvidenceSource` node instead. **Never a
fabricated `:Passage`**: that is the single failure the discriminated evidence contract
exists to prevent (F0 §2.2), and minting a passage node for a price quote would put a number
nobody filed one hop from the filed ones, indistinguishable by shape.

**Why one base label with a concrete label per kind, rather than five base labels.** Both
allowlists already have this shape and the choice is theirs, not a matter of taste:

| | one `:EvidenceSource` + 5 concrete | one base label per kind |
| --- | --- | --- |
| `models.BASE_LABELS` | +1 — stays the closed set §3.1 declares | +5 |
| `schema.CONSTRAINTS` | +1 uniqueness constraint | +5, one key property each |
| `loader.CONCRETE_LABELS` | +5, which is what that list is *for* | +0, and the base list carries them |
| "all non-filed evidence" | `MATCH (:EvidenceSource)` | a five-way `OR` over labels |
| `GraphEdge` endpoints | one declared endpoint base label | five |

The second column is the same reasoning `:Entity` already won: `opendoor` is an `:Entity`
carrying `:PublicCompany:Company`, and the base label is what a constraint and a `MATCH`
attach to while the concrete label is what a reader filters on. Five base labels would also
mean five key properties (`xbrlfact_id`, `marketdata_id`, …) for one idea.

**This module owns the mapping and nothing else.** It builds no `GraphNode` and no
`GraphEdge` — `nodes.py` and `edges.py` do, from the same `EvidenceSource` value, which is
the point: `graph.core.citations` records what it cost the last time the two builders each
decided for themselves which passages a run cites, and an evidence-source key computed twice
would fail the same way.

Determinism (§4.4): every property list is built from the row's declared fields in a fixed
order, and `evidence_sources` walks the run's catalog order.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from extraction.core.identifiers import digest

from ...core.inputs import (
    CalculatedEvidenceRow,
    EvidenceRow,
    EvidenceRowT,
    ExternalPageEvidenceRow,
    FilingMetadataEvidenceRow,
    GraphInputError,
    MarketDataEvidenceRow,
    XbrlEvidenceRow,
)
from ...core.keys import evidence_source_node_key

#: The base label every non-passage evidence source carries. Declared in
#: `graph.core.models.BASE_LABELS`; named here so the two builders write it once each.
EVIDENCE_SOURCE_LABEL = "EvidenceSource"

#: `evidence_kind` -> the concrete label that kind's node also carries. Hand-maintained
#: beside `loader.CONCRETE_LABELS`, and held to `ontology.core.values.EvidenceKind` by
#: `tests/graph/test_evidence_sources.py` — the same discipline `EVIDENCE_ROW_MODELS` uses,
#: for the same reason: a kind added to the enum and not here must fail by name rather than
#: project as an unlabelled node.
EVIDENCE_SOURCE_LABELS: Mapping[str, str] = MappingProxyType({
    "xbrl_fact": "XbrlFact",
    "filing_metadata": "FilingMetadata",
    "external_page": "ExternalPage",
    "market_data": "MarketData",
    "calculated": "Calculated",
})

#: The row shapes that declare a `quoted_text` column. `filing_metadata`, `market_data` and
#: `calculated` do not have one at all — a submission's items, a closing price and a
#: subtraction are not quotations — so reading the attribute on them is an `AttributeError`,
#: not a `None`. Spelled as an isinstance tuple rather than `getattr(row, …, None)` because a
#: renamed field must stop the run instead of quietly becoming absent (`inputs.
#: cited_passage_of` makes the same argument for `passage_id`).
QUOTING_ROWS = (EvidenceRow, XbrlEvidenceRow, ExternalPageEvidenceRow)


class EvidenceSourceError(GraphInputError):
    """A non-passage evidence row this stage cannot turn into a node."""

    code = "EVIDENCE_SOURCE_NOT_PROJECTABLE"


class UnmappedEvidenceKindError(EvidenceSourceError):
    """A row whose kind has a reader but no evidence-source label.

    Reachable only by adding a kind to `graph.core.inputs.EVIDENCE_ROW_MODELS` and not here.
    It is a refusal rather than a default label because "some node with no kind" is the shape
    of a fact whose origin cannot be told apart from a filed one.
    """


class EvidenceSourceConflictError(EvidenceSourceError):
    """Two evidence rows mint one source key and describe the source differently.

    The keys below are the source's coordinates, so two rows that agree on them are two
    citations of one thing and must agree on what that thing is. They are refused here rather
    than merged: the loader's `MERGE` would take whichever row it saw last, silently, and the
    plan's rule is that a disagreement between two catalog rows is named (`inputs.
    CatalogRowConflict` refuses the same shape of thing across catalogs).
    """


@dataclass(frozen=True)
class EvidenceSource:
    """One citable thing that is not a filed passage, ready for both builders.

    `properties` holds only what the *source* is, never what one citation of it said:
    `quoted_text` varies per citing claim and rides on the `EVIDENCED_BY` edge, exactly where
    a passage citation's `quoted_text` already rides.
    """

    key: str
    evidence_kind: str
    concrete_label: str
    properties: Mapping[str, Any]

    @property
    def labels(self) -> tuple[str, ...]:
        return (EVIDENCE_SOURCE_LABEL, self.concrete_label)


def quoted_text_of(row: EvidenceRowT) -> str | None:
    """The words this row quoted, or `None` for a kind that quotes nothing."""
    return row.quoted_text if isinstance(row, QUOTING_ROWS) else None


def evidence_source_of(row: EvidenceRowT) -> EvidenceSource | None:
    """The source node this row cites, or `None` when it cites a filed passage.

    `isinstance(row, EvidenceRow)` is the passage test — the one `graph.core.inputs` declares
    — so a `normalized_passage` or `normalized_table` row returns `None` here and keeps the
    passage chain it has always had.

    Identity per kind, taken from the fields the row actually carries and nothing else:

    | Kind | Identity | Note |
    | --- | --- | --- |
    | `xbrl_fact` | accession, concept | context and dimensions join the row *and this key* when F1 ingests XBRL (F0 §3.2); until then two taggings of one concept in one filing are one source, because the run holds nothing that separates them and inventing a separator is worse |
    | `filing_metadata` | accession | submission-level: the accession **is** the source |
    | `external_page` | channel, `fetched_at`, digest(url) | a page is mutable and undated by anything but the fetch, so two readings of one URL are two sources |
    | `market_data` | provider, instrument, session, digest(series, row) | the row identity survives a provider revising a session |
    | `calculated` | version, digest(expression, inputs) | the derivation is the evidence; two facts from one arithmetic over one input set share it |
    """
    if isinstance(row, EvidenceRow):
        return None
    label = EVIDENCE_SOURCE_LABELS.get(row.evidence_kind)
    if label is None:
        raise UnmappedEvidenceKindError(
            f"evidence row for claim {row.claim_id!r} declares evidence_kind "
            f"{row.evidence_kind!r}, which has a reader but no evidence-source label; "
            f"mapped kinds are {', '.join(sorted(EVIDENCE_SOURCE_LABELS))}")

    if isinstance(row, XbrlEvidenceRow):
        identity: tuple[str, ...] = (row.accession, row.xbrl_concept)
        properties: dict[str, Any] = {
            "accession": row.accession,
            "xbrl_concept": row.xbrl_concept,
            "source_url": row.source_url,
            "document_id": row.document_id,
        }
    elif isinstance(row, FilingMetadataEvidenceRow):
        identity = (row.accession,)
        properties = {
            "accession": row.accession,
            "source_url": row.source_url,
            "document_id": row.document_id,
        }
    elif isinstance(row, ExternalPageEvidenceRow):
        identity = (row.disclosure_channel_id, row.fetched_at, digest(row.source_url))
        properties = {
            "source_url": row.source_url,
            "disclosure_channel_id": row.disclosure_channel_id,
            "fetched_at": row.fetched_at,
        }
    elif isinstance(row, MarketDataEvidenceRow):
        identity = (row.provider, row.instrument_id, row.session_date,
                    digest(row.source_identity, row.row_identity))
        properties = {
            "provider": row.provider,
            "source_identity": row.source_identity,
            "row_identity": row.row_identity,
            "instrument_id": row.instrument_id,
            "session_date": row.session_date,
            "fetched_at": row.fetched_at,
            "source_url": row.source_url,
            "disclosure_channel_id": row.disclosure_channel_id,
        }
    elif isinstance(row, CalculatedEvidenceRow):
        identity = (row.calculation_version,
                    digest(row.calculation_expression, *row.input_observation_ids))
        properties = {
            "calculation_expression": row.calculation_expression,
            "calculation_version": row.calculation_version,
            # A list rather than a joined string: these are `:Observation` keys, and §12's
            # `COMPUTED_FROM` edge — deferred, not rejected — is built from exactly this list
            # the day it lands. Flattening them would make it unreadable without a parser.
            "input_observation_ids": list(row.input_observation_ids),
        }
    else:  # pragma: no cover - unreachable while EVIDENCE_ROW_MODELS and the tuple above agree
        raise UnmappedEvidenceKindError(
            f"{type(row).__name__} is a non-passage evidence row this module has no identity "
            "rule for; add one rather than letting it fall through to a generic node")

    key = evidence_source_node_key(row.evidence_kind, *identity)
    return EvidenceSource(
        key=key,
        evidence_kind=row.evidence_kind,
        concrete_label=label,
        # The key is a property as well as the MERGE identity, exactly as `passage_id` is on
        # `:Passage`: `schema.KEY_PROPERTIES` reads it back and `verification` compares it.
        properties={"evidence_source_id": key, "evidence_kind": row.evidence_kind,
                    **{name: properties[name] for name in sorted(properties)}},
    )


def evidence_sources(rows: Iterable[EvidenceRowT]) -> tuple[EvidenceSource, ...]:
    """Every distinct source those rows cite, in first-seen catalog order.

    First-seen rather than sorted: the export applies `NODE_SORT_KEY`, and the builders are
    required to be deterministic in *content* rather than pre-sorted (`nodes.build_nodes`
    says so). Catalog order is the run's order and is stable.
    """
    seen: dict[str, EvidenceSource] = {}
    for row in rows:
        source = evidence_source_of(row)
        if source is None:
            continue
        existing = seen.get(source.key)
        if existing is None:
            seen[source.key] = source
            continue
        if dict(existing.properties) != dict(source.properties):
            differing = sorted(
                name for name in set(existing.properties) | set(source.properties)
                if existing.properties.get(name) != source.properties.get(name))
            raise EvidenceSourceConflictError(
                f"{source.key}: two evidence rows describe one source differently on "
                f"{differing}; the key is the source's coordinates, so rows that share it "
                "must agree on what it is")
    return tuple(seen.values())


__all__ = [
    "EVIDENCE_SOURCE_LABEL",
    "EVIDENCE_SOURCE_LABELS",
    "QUOTING_ROWS",
    "EvidenceSource",
    "EvidenceSourceConflictError",
    "EvidenceSourceError",
    "UnmappedEvidenceKindError",
    "evidence_source_of",
    "evidence_sources",
    "quoted_text_of",
]
