"""Typed readers over one finalized extraction run, plus the corpus rows the graph cites.

Seven catalog row shapes, one run manifest, and a deliberately narrow view of the two
normalization catalogs. Every field list here is the shape the catalog writer actually
emits (`extraction/stages/catalog/jsonl_catalog.py`), **not** the shape of the ontology model
behind it: V1_GRAPH_PROTOTYPE §0b records that no catalog row is a `model_dump()` of
anything, so a reader built from `ontology.core.models` fails on all 2,717 claim rows.

Three rules run through the module, all from §2.2–§2.3:

* **Fail loudly, never default.** The seven catalog readers are `extra="forbid"`; an
  unexpected field stops the projection by name rather than being silently dropped. **Every
  declared field is also required** — a nullable one is spelled `X | None` with no `= None`,
  so `null` must be written for the reader to accept the row. All seven catalogs are
  single-shaped *(verified 2026-08-02: one distinct key tuple per file, on both the run and
  the fixture)*, so a `= None` default could only ever fire on a row the writer stopped
  emitting — and it would fire silently. A `participants` key that disappeared upstream would
  have projected zero participants and 10 missing `PARTICIPATES_IN` edges without a word.
* **`lane` is not `source_lane`.** Routing vocabulary and ontology vocabulary. Both are
  carried and neither is mapped onto the other.
* **`passage_id` / `document_id` / `document_type` may be empty strings**
  (`jsonl_catalog.py:170-173`). The reader accepts `""` — it is what the run may legally
  contain — and `graph.core.keys` refuses it as a node key. Rejecting it here would make a
  legal run unreadable; accepting it as a key would mint a node with no identity.

Nullability below is measured, not assumed *(verified 2026-08-02 against
`data/extraction_runs/extract-v1-lexical-2422c4252c07`)*: `confidence` null on all 2,717
claims and 2,707 observations; `currency` null on 1,710; `period_start`/`period_end` null on
the 403 instants and `instant_date` null on the 2,304 durations; `row_label`/`column_label`
null on the 17 narrative rows; `scale`/`scale_location` null on 97; `table_id` null on 27
evidence rows; `raw_finding` null on `assemble`-origin rejections (none in this run).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict

# -- errors ----------------------------------------------------------------------------


class GraphInputError(RuntimeError):
    """Base for every refusal this layer makes about its inputs."""


class MissingCatalogError(GraphInputError):
    """A file the projection cannot proceed without is absent."""


class CatalogRowConflict(GraphInputError):
    """Two catalogs describe one id and disagree on a field they both carry.

    Names the id and the field, because "the catalogs disagree" is not actionable and
    "`claim:metric-observation:78dcfb719539`.`document_type`" is.
    """

    def __init__(self, *, identifier: str, field: str, left_name: str, left: Any,
                 right_name: str, right: Any) -> None:
        self.identifier = identifier
        self.field = field
        super().__init__(
            f"{identifier}: {left_name}.{field}={left!r} disagrees with "
            f"{right_name}.{field}={right!r}"
        )


class CatalogSetMismatch(GraphInputError):
    """Two catalogs that must cover the same id set do not."""

    def __init__(self, *, description: str, only_left: Iterable[str],
                 only_right: Iterable[str], left_name: str, right_name: str) -> None:
        self.only_left = tuple(sorted(only_left))
        self.only_right = tuple(sorted(only_right))
        super().__init__(
            f"{description}: {len(self.only_left)} only in {left_name} "
            f"{self.only_left[:5]}, {len(self.only_right)} only in {right_name} "
            f"{self.only_right[:5]}"
        )


# -- the seven catalog row shapes ------------------------------------------------------


class _CatalogRow(BaseModel):
    """Frozen and `extra="forbid"`: §2.2's "fail loudly, never default", made executable."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimRow(_CatalogRow):
    """`claims.jsonl` (`jsonl_catalog.py:176-189`) — the authority for provenance.

    §2.2 P4: where this row and a payload row carry the same field, this one is the source
    the graph reads. The payload files carry the payload.
    """

    claim_id: str
    claim_kind: str
    payload_id: str
    lane: str
    passage_id: str
    document_id: str
    document_type: str
    assertion_type: str
    confidence: float | None
    extractor_metadata: dict[str, Any]


class ObservationRow(_CatalogRow):
    """`observations.jsonl` (`:192-221`) — 24 keys, a derived index that is not redundant.

    `value` keeps the model's own union (`ontology/core/models.py:373-398`) though it is a
    float on all 2,707 rows including counts like `market_count`. §5.4: the graph stores what
    the catalog holds and does not re-type it.
    """

    observation_id: str
    claim_id: str
    metric_id: str
    subject_entity_id: str
    subject_type: str
    value: float | int | str | bool
    unit: str
    currency: str | None
    period_start: str | None
    period_end: str | None
    instant_date: str | None
    population_definition_raw: str | None
    source_lane: str
    lane: str
    assertion_type: str
    confidence: float | None
    passage_id: str
    document_id: str
    document_type: str
    ambiguity_codes: tuple[str, ...]
    scale: str | None
    scale_location: str | None
    row_label: str | None
    column_label: str | None


class Participant(_CatalogRow):
    """Exactly the three keys the catalog writes (`:249-253`).

    `LaneEventParticipant.named` and `entity_text` exist on the lane model and **reach no
    catalog** (§1.4a). The `_unnamed_` substring in `entity_id` is the only placeholder
    signal that survives, which is why `graph.core.keys.is_placeholder` reads the id shape.
    """

    role: str
    entity_id: str
    entity_type: str


class EventRow(_CatalogRow):
    """`events.jsonl` (`:224-261`) — the only source of five fields.

    `dates_equal`, `review_flag`, `occurrence_date_text`, `announcement_date_text` and
    `evidence_quoted_text` are computed nowhere else (§0b item 3, §5.5). `claims.jsonl` alone
    is not sufficient for an event and neither is this file alone.
    """

    event_id: str
    claim_id: str
    event_type_id: str
    occurred_on: str | None
    announced_on: str | None
    occurrence_date_text: str | None
    announcement_date_text: str | None
    dates_equal: bool
    review_flag: str | None
    participants: tuple[Participant, ...]
    properties: dict[str, Any]
    assertion_type: str
    passage_id: str
    document_id: str
    document_type: str
    evidence_quoted_text: str | None
    lane: str


class RelationshipRow(_CatalogRow):
    """`relationships.jsonl` (`:264-282`). No `properties` field — the model has none."""

    relationship_instance_id: str
    claim_id: str
    relationship_id: str
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    valid_from: str | None
    valid_to: str | None
    assertion_type: str
    passage_id: str
    document_id: str
    document_type: str
    lane: str


class EvidenceRow(_CatalogRow):
    """`evidence.jsonl` (`:285-300`).

    Identity is `(claim_id, passage_id)`, not `claim_id` alone (§2.3 trap 4) — one row per
    claim in this run, and nothing may be built on that. No char offsets: `char_start` /
    `char_end` are dropped by the writer (trap 3). `passage_id` / `document_id` are optional
    because `EvidenceReference` declares them so; both are strings on all 2,717 rows here.
    """

    claim_id: str
    claim_kind: str
    evidence_index: int
    evidence_kind: str
    passage_id: str | None
    document_id: str | None
    table_id: str | None
    block_ids: tuple[str, ...]
    source_url: str | None
    quoted_text: str | None


class IssueRow(_CatalogRow):
    """`issues.jsonl` (`:86-90`), carrying the run's own `issue_id` (§4.3).

    `severity` discriminates four categories; `rejected_claim` is the flag that a mirroring
    `rejected_claims.jsonl` row exists. **Never sum the two files** (§2.2 P10).
    """

    issue_id: str
    code: str
    severity: str
    lane: str
    passage_id: str
    document_id: str
    document_type: str
    concept_ids: tuple[str, ...]
    row_label: str | None
    quoted_span: str | None
    request_sha256: str | None
    rejected_claim: bool
    detail: str


class RejectedClaimRow(_CatalogRow):
    """`rejected_claims.jsonl` (`:92-121`) — the seventh shape.

    `raw_finding` is the **pre-validation model output**, and its `value`/`unit`/`scale` are
    frequently the reason the claim was refused (§2.3). It is carried opaquely and must never
    be read as fact. Null for an `assemble`-origin rejection, which also writes no mirroring
    issue row at all (`:98-121`).
    """

    rejection_id: str
    refused_by: str
    raw_finding: dict[str, Any] | None
    code: str
    severity: str
    lane: str
    passage_id: str
    document_id: str
    document_type: str
    concept_ids: tuple[str, ...]
    row_label: str | None
    quoted_span: str | None
    request_sha256: str | None
    rejected_claim: bool
    detail: str


# -- the run manifest ------------------------------------------------------------------


class VerificationCheck(_CatalogRow):
    """One entry of `manifest.verification`.

    `passed` is the signal the graph reads, not the presence of `run.complete`:
    `extraction/pipeline.py:126` verifies and `:174` finalizes unconditionally, so a run with
    failing checks still produces a marked directory (§1.2, handoff §4.1).
    """

    check: str
    description: str
    examined: int
    failures: int
    failures_by_code: dict[str, int]
    passed: bool
    warnings: int
    warnings_by_code: dict[str, int]


class RunManifest(_CatalogRow):
    """`manifest.json`, strict at the top level.

    Strict rather than lenient: the manifest is how the graph learns which ontology and which
    commit produced the data, and a key appearing that this reader has never seen means the
    extraction layer changed shape — exactly the event a graph run should stop on rather than
    ignore. `lanes` is present in the real manifest and therefore declared here even though
    the graph does not read it; dropping it would be the silent-default this plan forbids.

    Nested blocks stay `dict` where the graph only carries them through to its own manifest.
    `verification` is modelled, because §5.5 reconciles a derived warning count against it.
    """

    run_id: str
    code_commit: str
    ontology_id: str
    ontology_definition_hash: str
    counts: dict[str, int]
    verification: tuple[VerificationCheck, ...]
    corpus: dict[str, Any]
    scope: dict[str, Any]
    bounds: dict[str, Any]
    lanes: tuple[dict[str, Any], ...]
    extractor_version: str
    layout_version: str
    created_at: str
    config_hash: str
    catalog_digests: dict[str, str]
    environment: dict[str, Any]
    dependencies: dict[str, Any]
    provider: dict[str, Any]

    def check(self, name: str) -> VerificationCheck:
        for entry in self.verification:
            if entry.check == name:
                return entry
        raise GraphInputError(f"manifest has no verification check named {name!r}")

    @property
    def ontology_validation_warnings(self) -> int:
        """The aggregate §5.5 reconciles against. 186 in this run."""
        return self.check("ontology_validation").warnings


# -- the normalization catalogs, read as a documented subset ---------------------------


class _CorpusRow(BaseModel):
    """The one reader in this module that is **not** `extra="forbid"`, deliberately.

    `passages.jsonl` and `documents.jsonl` carry 31 and 35 fields; the graph needs 9 and 10
    of them (§3.1, §4.1). The seven catalog readers forbid extras because an unexpected field
    in an extraction catalog means the run this graph consumes changed shape — the projection
    should stop. The corpus catalogs are a *different* boundary: they are written by
    normalization for many consumers, they legitimately grow fields the graph has no opinion
    about (`parser_version`, `locators`, `text_yield_pct`), and failing a graph build because
    normalization added a diagnostic column would be a false alarm.

    So this is an **allow-subset** reader, and the subset is declared rather than discovered:
    every field the graph reads is named below, and one that disappeared upstream would still
    fail here as a missing required field. Ignoring unknown keys is not the same as accepting
    unknown data.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")


class PassageRow(_CorpusRow):
    """The passage fields §3.1's `:Passage` node and §7.2's Browser search need."""

    passage_id: str
    document_id: str
    passage_kind: str
    text: str
    section_id: str
    heading_path: tuple[str, ...]
    table_id: str | None
    char_count: int
    source_url: str


class DocumentRow(_CorpusRow):
    """The document fields §3.1's `:Document` node needs.

    `report_date` and `title` are optional because `normalization/core/models.py` declares
    them so — measured: `report_date` null on 1 of 294 documents.
    """

    document_id: str
    form: str
    filing_date: str
    report_date: str | None
    accession: str
    source_url: str
    document_type: str
    title: str | None
    company_name: str
    cik10: str


# -- cross-file consistency ------------------------------------------------------------

#: The seven fields `claims.jsonl` and `observations.jsonl` both carry. §2.2 P4 measured them
#: byte-identical on all 2,707 rows; the graph asserts it rather than trusting it, because
#: "derived index" is a property of the writer and the graph reads both files.
SHARED_CLAIM_OBSERVATION_FIELDS = (
    "claim_id", "lane", "passage_id", "document_id", "document_type", "assertion_type",
    "confidence",
)

#: The five an event or relationship row shares with its claim. No `confidence`: the payload
#: catalogs do not carry one, which is itself a shape difference and not a disagreement.
SHARED_CLAIM_PAYLOAD_FIELDS = (
    "lane", "passage_id", "document_id", "document_type", "assertion_type",
)


def check_claims_agree_with_observations(
    claims: Sequence[ClaimRow], observations: Sequence[ObservationRow]
) -> None:
    """Raise `CatalogRowConflict` on the first disagreeing (claim_id, field)."""
    by_id = {claim.claim_id: claim for claim in claims}
    for observation in observations:
        claim = by_id.get(observation.claim_id)
        if claim is None:
            raise CatalogSetMismatch(
                description="observation cites a claim_id absent from claims.jsonl",
                only_left=(observation.claim_id,), only_right=(),
                left_name="observations.jsonl", right_name="claims.jsonl")
        for field in SHARED_CLAIM_OBSERVATION_FIELDS:
            left, right = getattr(claim, field), getattr(observation, field)
            if left != right:
                raise CatalogRowConflict(
                    identifier=observation.claim_id, field=field,
                    left_name="claims.jsonl", left=left,
                    right_name="observations.jsonl", right=right)


def check_payload_ids(
    claims: Sequence[ClaimRow],
    observations: Sequence[ObservationRow],
    events: Sequence[EventRow],
    relationships: Sequence[RelationshipRow],
) -> None:
    """`{claims.payload_id}` must equal the union of the three payload id columns (§2.2 P2)."""
    declared = {claim.payload_id for claim in claims}
    present = ({observation.observation_id for observation in observations}
               | {event.event_id for event in events}
               | {row.relationship_instance_id for row in relationships})
    if declared != present:
        raise CatalogSetMismatch(
            description="claims.payload_id does not match the payload catalogs",
            only_left=declared - present, only_right=present - declared,
            left_name="claims.jsonl", right_name="payload catalogs")


def check_evidence_claim_ids(
    claims: Sequence[ClaimRow], evidence: Sequence[EvidenceRow]
) -> None:
    """Every claim is evidenced and every evidence row belongs to a claim (§2.2 P11)."""
    declared = {claim.claim_id for claim in claims}
    present = {row.claim_id for row in evidence}
    if declared != present:
        raise CatalogSetMismatch(
            description="evidence.claim_id does not match claims.claim_id",
            only_left=declared - present, only_right=present - declared,
            left_name="claims.jsonl", right_name="evidence.jsonl")


def check_claims_agree_with_payloads(
    claims: Sequence[ClaimRow],
    events: Sequence[EventRow],
    relationships: Sequence[RelationshipRow],
) -> None:
    """Event and relationship rows cite a claim that exists and agree with it.

    §2.2 P4 measured the byte-identity claim for `observations.jsonl` only, and the 10-row
    gap it describes is exactly these events and relationships. They carry five of the same
    provenance fields, written by the same `_anchor` / `_lane_of` helpers
    (`jsonl_catalog.py:141-173`), and they agree on all 10 rows of this run
    *(verified 2026-08-02)* — so the graph asserts it here rather than reading whichever file
    it happened to reach first.
    """
    declared = {claim.claim_id: claim for claim in claims}
    for row, name in ((r, "events.jsonl") for r in events):
        _agree(declared, row, name)
    for row, name in ((r, "relationships.jsonl") for r in relationships):
        _agree(declared, row, name)


def _agree(claims_by_id: Mapping[str, ClaimRow], row: Any, catalog: str) -> None:
    claim = claims_by_id.get(row.claim_id)
    if claim is None:
        raise CatalogSetMismatch(
            description=f"{catalog} cites a claim_id absent from claims.jsonl",
            only_left=(row.claim_id,), only_right=(),
            left_name=catalog, right_name="claims.jsonl")
    for field in SHARED_CLAIM_PAYLOAD_FIELDS:
        left, right = getattr(claim, field), getattr(row, field)
        if left != right:
            raise CatalogRowConflict(
                identifier=row.claim_id, field=field,
                left_name="claims.jsonl", left=left, right_name=catalog, right=right)


def check_cross_catalog_consistency(
    *,
    claims: Sequence[ClaimRow],
    observations: Sequence[ObservationRow],
    events: Sequence[EventRow],
    relationships: Sequence[RelationshipRow],
    evidence: Sequence[EvidenceRow],
) -> None:
    """Every join the projection relies on, checked once at load."""
    check_claims_agree_with_observations(claims, observations)
    check_claims_agree_with_payloads(claims, events, relationships)
    check_payload_ids(claims, observations, events, relationships)
    check_evidence_claim_ids(claims, evidence)


# -- the rejection <-> issue join ------------------------------------------------------


@dataclass(frozen=True)
class RejectionIssueJoin:
    """One refused finding written to two files with two ids (§2.2 P5, P10).

    `issue_id` and `rejection_id` are both `_ordinal_id` over the same parts tuple
    (`jsonl_catalog.py:84-96`), so a lane rejection and its mirroring issue share a hex.
    An `assemble`-origin rejection writes no issue row at all (`:98-121`) and lands in
    `unmirrored` — absence of a mirror is a documented shape, not a failure.
    """

    pairs: tuple[tuple[RejectedClaimRow, IssueRow], ...]
    unmirrored: tuple[RejectedClaimRow, ...]

    @property
    def mirrored(self) -> int:
        return len(self.pairs)

    @property
    def total(self) -> int:
        return len(self.pairs) + len(self.unmirrored)


def _id_hex(value: str, *, prefix: str) -> str:
    head, _, hexpart = value.partition(":")
    if head != prefix or not hexpart:
        raise GraphInputError(f"expected an id of the form {prefix}:<hex>, got {value!r}")
    return hexpart


def join_rejections_to_issues(
    issues: Sequence[IssueRow], rejected_claims: Sequence[RejectedClaimRow]
) -> RejectionIssueJoin:
    """Pair each rejection with the issue that mirrors it, by shared hex."""
    by_hex: dict[str, IssueRow] = {}
    for issue in issues:
        digest = _id_hex(issue.issue_id, prefix="issue")
        if digest in by_hex:
            raise GraphInputError(f"issue_id hex {digest!r} occurs twice in issues.jsonl")
        by_hex[digest] = issue

    pairs: list[tuple[RejectedClaimRow, IssueRow]] = []
    unmirrored: list[RejectedClaimRow] = []
    for rejection in rejected_claims:
        issue = by_hex.get(_id_hex(rejection.rejection_id, prefix="rej"))
        if issue is None:
            unmirrored.append(rejection)
        else:
            pairs.append((rejection, issue))
    return RejectionIssueJoin(pairs=tuple(pairs), unmirrored=tuple(unmirrored))


# -- the loaded run --------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionRunInputs:
    """Everything one projection reads, already parsed, joined and checked.

    Immutable and index-carrying: the projection stages walk these collections several times
    each, and rebuilding a 17,127-row index per lookup is the kind of accidental quadratic a
    frozen value type should make impossible.
    """

    directory: Path
    catalog_directory: Path | None
    manifest: RunManifest
    claims: tuple[ClaimRow, ...]
    observations: tuple[ObservationRow, ...]
    events: tuple[EventRow, ...]
    relationships: tuple[RelationshipRow, ...]
    evidence: tuple[EvidenceRow, ...]
    issues: tuple[IssueRow, ...]
    rejected_claims: tuple[RejectedClaimRow, ...]
    passages: tuple[PassageRow, ...]
    documents: tuple[DocumentRow, ...]
    claims_by_id: Mapping[str, ClaimRow]
    evidence_by_claim_id: Mapping[str, tuple[EvidenceRow, ...]]
    passages_by_id: Mapping[str, PassageRow]
    documents_by_id: Mapping[str, DocumentRow]
    rejection_issue_join: RejectionIssueJoin

    @classmethod
    def from_rows(
        cls,
        *,
        manifest: RunManifest,
        claims: Sequence[ClaimRow] = (),
        observations: Sequence[ObservationRow] = (),
        events: Sequence[EventRow] = (),
        relationships: Sequence[RelationshipRow] = (),
        evidence: Sequence[EvidenceRow] = (),
        issues: Sequence[IssueRow] = (),
        rejected_claims: Sequence[RejectedClaimRow] = (),
        passages: Sequence[PassageRow] = (),
        documents: Sequence[DocumentRow] = (),
        directory: Path | None = None,
        catalog_directory: Path | None = None,
    ) -> "ExtractionRunInputs":
        """Build and check. The path `load_run` takes, and the one tests take with no disk."""
        check_cross_catalog_consistency(
            claims=claims, observations=observations, events=events,
            relationships=relationships, evidence=evidence)

        by_claim: dict[str, list[EvidenceRow]] = {}
        for row in evidence:
            by_claim.setdefault(row.claim_id, []).append(row)

        return cls(
            directory=directory or Path("."),
            catalog_directory=catalog_directory,
            manifest=manifest,
            claims=tuple(claims),
            observations=tuple(observations),
            events=tuple(events),
            relationships=tuple(relationships),
            evidence=tuple(evidence),
            issues=tuple(issues),
            rejected_claims=tuple(rejected_claims),
            passages=tuple(passages),
            documents=tuple(documents),
            claims_by_id=MappingProxyType({c.claim_id: c for c in claims}),
            evidence_by_claim_id=MappingProxyType(
                {key: tuple(rows) for key, rows in by_claim.items()}),
            passages_by_id=MappingProxyType({p.passage_id: p for p in passages}),
            documents_by_id=MappingProxyType({d.document_id: d for d in documents}),
            rejection_issue_join=join_rejections_to_issues(issues, rejected_claims),
        )


# -- reading from disk -----------------------------------------------------------------

#: The seven files a run must hold, in the order a digest over them is taken
#: (`graph.core.manifest.input_content_digest`). Public because the run id derives from these
#: bytes and a second, privately-held list would be a second answer to "what did we read".
CATALOG_FILES = ("claims", "observations", "events", "relationships", "evidence",
                 "issues", "rejected_claims")

RowT = TypeVar("RowT", bound=BaseModel)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL with the line number in the error.

    Duplicated from `normalization/utils/jsonl.py` rather than imported, on that module's own
    reasoning: the graph must not depend on normalization's internals, and the interface
    between them is the on-disk catalog.
    """
    if not path.is_file():
        raise MissingCatalogError(f"missing catalog file: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise GraphInputError(f"{path}:{number}: malformed JSONL: {exc}") from exc
    return rows


def _parse(path: Path, model: type[RowT]) -> tuple[RowT, ...]:
    return tuple(model(**row) for row in _read_jsonl(path))


def _default_catalog_directory(directory: Path) -> Path | None:
    """Where the normalization catalogs sit relative to a run directory.

    Two real layouts, both checked because both exist: the repository keeps runs at
    `data/extraction_runs/<run_id>/` beside `data/normalization_catalog/` (two levels up),
    and `tests/fixtures/graph/` mirrors the run under `extraction_run/` beside
    `normalization_catalog/` (one level up). Explicit `catalog_directory=` overrides both.
    """
    for candidate in (directory.parent / "normalization_catalog",
                      directory.parent.parent / "normalization_catalog"):
        if (candidate / "passages.jsonl").is_file():
            return candidate
    return None


def load_run(
    directory: Path | str,
    *,
    catalog_directory: Path | str | None = None,
) -> ExtractionRunInputs:
    """Read one finalized extraction run and the corpus rows it cites.

    Consistency is checked here rather than by the caller: every join the projection makes —
    claim to observation, claim to payload, claim to evidence — is asserted once, at the
    boundary, so a stage downstream can index without re-proving that the index is safe.
    """
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise MissingCatalogError(f"missing run manifest: {manifest_path}")
    manifest = RunManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))

    models: dict[str, type[BaseModel]] = {
        "claims": ClaimRow, "observations": ObservationRow, "events": EventRow,
        "relationships": RelationshipRow, "evidence": EvidenceRow, "issues": IssueRow,
        "rejected_claims": RejectedClaimRow,
    }
    rows = {name: _parse(directory / f"{name}.jsonl", models[name])
            for name in CATALOG_FILES}

    resolved = (Path(catalog_directory) if catalog_directory is not None
                else _default_catalog_directory(directory))
    if resolved is None:
        raise MissingCatalogError(
            f"no normalization catalog found beside {directory}; pass catalog_directory=")
    passages = _parse(resolved / "passages.jsonl", PassageRow)
    documents = _parse(resolved / "documents.jsonl", DocumentRow)

    return ExtractionRunInputs.from_rows(
        manifest=manifest, passages=passages, documents=documents,
        directory=directory, catalog_directory=resolved,
        **rows,  # type: ignore[arg-type]
    )


__all__ = [
    "CATALOG_FILES",
    "CatalogRowConflict",
    "CatalogSetMismatch",
    "ClaimRow",
    "DocumentRow",
    "EventRow",
    "EvidenceRow",
    "ExtractionRunInputs",
    "GraphInputError",
    "IssueRow",
    "MissingCatalogError",
    "ObservationRow",
    "Participant",
    "PassageRow",
    "RejectedClaimRow",
    "RejectionIssueJoin",
    "RelationshipRow",
    "RunManifest",
    "SHARED_CLAIM_OBSERVATION_FIELDS",
    "SHARED_CLAIM_PAYLOAD_FIELDS",
    "VerificationCheck",
    "check_claims_agree_with_observations",
    "check_cross_catalog_consistency",
    "check_evidence_claim_ids",
    "check_claims_agree_with_payloads",
    "check_payload_ids",
    "join_rejections_to_issues",
    "load_run",
]
