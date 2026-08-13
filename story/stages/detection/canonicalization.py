"""`canon-policy:1.0.0` — grouping observations into fact-slots and choosing what each one says.

Responsibility: §6.1, end to end. Quarantine, cluster, resolve, choose a representative, and
record the provenance the six fields of §6.1 step 6 name. It owns no comparability rule — R1–R10
are `story/core/series.py`'s — and it owns no Cypher: every row arrives through the injected
`GraphRetriever`, which is the only surface in this package that reaches a database (§9, D1).

**Measured against `graph-v1-0483dc6b4b10` / `extract-v1-lexical-833f7bcfbce9`, 2026-08-03:
2,704 observations → 537 slots, 36 of them holding more than one distinct value, 10 of those
differing by more than 1% — and all ten are thousands-versus-millions rounding, which step 2
collapses. 537 `ok`, 0 `resolved_by_majority`, 0 `conflict`.** The run holds no semantic fact
conflict; the majority rule exists for the run that does.

**`get_metric_history` is bounded at 200 rows and five metrics exceed it**, so this module
pages. Discovered here, not planned for: `adjusted_ebitda`, `adjusted_ebitda_margin`,
`contribution_margin`, `contribution_profit` and `gaap_gross_margin` each return
`truncated=True` on a single call, and a canonicaliser that took the first page would have
built its census from 2,238 observations and 480 slots while reporting nothing wrong. The
paging is keyset paging on the tool's own `$since` window — which filters on
`coalesce(period_end, instant_date)`, the same expression the statement orders by — advancing
to the last anchor date returned and de-duplicating on `observation_id`. It re-reads one
anchor date per page and terminates because no single anchor date carries 200 rows of one
metric *(the largest is 20: `gaap_gross_margin` at `2023-09-30` and `2023-12-31`)*. If one ever
did, `_paged_history` refuses rather than silently losing the rest.

**The quote and the filing date come from a second tool.** C3 puts `quoted_text` on the
`EVIDENCED_BY` edge and `filing_date` on the `:Document`, and `get_metric_history` returns
neither, so §6.1 step 1's quarantine test and step 5's tie-break need `get_fact_evidence` per
observation. That is 2,704 calls and it costs 2.7 s against the live container *(measured)*, so
it is on by default; `with_evidence=False` exists for a caller that wants the census alone, and
the points it produces carry `quarantine_not_evaluated` and `filing_date_unknown` rather than
pretending the tests were run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from story.contracts import GraphRetriever
from story.core.models import RetrievalOutcome
from story.core.periods import StoryPeriod
from story.core.series import (
    CanonicalPoint,
    CanonicalStatus,
    ObservationRecord,
    ValueCluster,
    observation_period,
    within_tolerance,
)

#: §6.1's identity. Inside every `candidate_id` digest (§6.11), so a change to any rule in this
#: module must change this string or it would mutate candidates in place instead of minting new
#: ones.
POLICY_VERSION = "canon-policy:1.0.0"

#: How many consecutive numeric tokens make a narrative read a flattened table row (§6.1 step 1).
MIN_FLATTENED_NUMERALS = 3

#: `Month DD, YYYY`, masked before the numeral run is counted. **The mask is load-bearing**: the
#: plan's own example, *"As of December 31, 2023, 18% of our homes…"*, is a real observation in
#: this run (`pct_homes_on_market_gt_120_days`, two of them) and reads as `31, 2023, 18` — three
#: numerals separated by punctuation and spaces — without it. Masking to a word rather than to
#: nothing is what breaks the run: the replacement has to contain a letter, because the rule's
#: separator test is *"non-alphabetic characters"*.
_DATE_LITERAL = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November"
    r"|December)\s+\d{1,2},\s*\d{4}\b",
    re.IGNORECASE,
)
_DATE_MASK = "DATE"

#: A printed number: digits, with thousands separators and a decimal part inside the token
#: rather than between two tokens. `1,234.5` is one numeral and not three, which is the whole
#: reason this is a regex and not `str.split`.
_NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?")

_ALPHABETIC = re.compile(r"[A-Za-z]")

#: Sorts after every real ISO date, so an observation whose filing date is unknown loses the
#: "earliest filing wins" tie-break instead of winning it by default (§6.1 step 5).
_UNKNOWN_FILING_DATE = "9999-12-31"

#: The warnings this module can put on a point. Named as constants because §10.1 renders them
#: and a test pins the set; a code invented inline is a code no renderer knows about.
LANE_DEFECT = "lane_defect"
QUARANTINE_NOT_EVALUATED = "quarantine_not_evaluated"
FILING_DATE_UNKNOWN = "filing_date_unknown"
MINORITY_READING_PRESENT = "minority_reading_present"
SLOT_UNIT_DISAGREEMENT = "slot_unit_disagreement"
SLOT_CURRENCY_DISAGREEMENT = "slot_currency_disagreement"


@dataclass(frozen=True, slots=True)
class SlotCensus:
    """What a canonicalisation run measured. The acceptance test of §6.1 reads exactly this."""

    observations: int
    metrics: int
    slots: int
    #: Slots holding more than one distinct raw value — §6.1's 36.
    multi_valued: int
    #: Slots where tolerance clustering did **not** collapse them to one reading. §6.1's claim
    #: is that this is 0 on the current run, which is the same claim as `conflict == 0` and
    #: `resolved_by_majority == 0`, stated as the input rather than as the outcome.
    multi_cluster: int
    ok: int
    resolved_by_majority: int
    conflict: int
    quarantined: int


@dataclass(frozen=True, slots=True)
class ObservationLoad:
    """Every observation read, and every metric that could not be read.

    `unreadable` is a field and not a log line: a metric the retriever refused is a hole in the
    series, and a census computed over an unknown number of metrics is a census that cannot be
    compared with the plan's.
    """

    records: tuple[ObservationRecord, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()
    calls: int = 0

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(sorted({record.metric_id for record in self.records}))


def is_flattened_table_read(quoted_text: str) -> bool:
    """§6.1 step 1's test: ≥3 consecutive numerals separated only by non-alphabetic characters.

    **This fires on nothing in the current run and is tested synthetically.** F0's
    `PERIOD_NOT_GROUNDED_IN_PASSAGE` refusal already removed the three rows it caught in the
    2026-08-02 snapshot — all from `…q42023formxex992sharehol.htm#p20` — so live data cannot
    exercise it. A rule that fires on nothing and is never tested is a rule that has silently
    stopped working, so `tests/story/test_story_series.py` drives it against a hand-built
    flattened row and against the two real narrative quotes it must *not* catch.

    What it is for: a narrative-lane extractor that read a table which lost its cell boundaries
    emits a value with a quote like *"18% 21% 23% 46%"*, where the number bound to the period is
    whichever one the extractor happened to pick. The quote is evidence of nothing, so the
    observation is kept in the graph and dropped from the series.
    """
    masked = _DATE_LITERAL.sub(_DATE_MASK, quoted_text)
    matches = list(_NUMERAL.finditer(masked))
    run = 1 if matches else 0
    longest = run
    for previous, current in zip(matches, matches[1:]):
        separator = masked[previous.end():current.start()]
        run = run + 1 if not _ALPHABETIC.search(separator) else 1
        longest = max(longest, run)
    return longest >= MIN_FLATTENED_NUMERALS


def canonicalize(records: Sequence[ObservationRecord]) -> tuple[CanonicalPoint, ...]:
    """Every fact-slot in the input, canonicalised, ordered by `(metric_id, anchor, period)`.

    Deterministic in the strong sense: the output depends on the set of records and not on the
    order they arrive in. Every sort inside is total — `observation_id` is the last key
    everywhere — because a canonical value that moved when the retriever changed page size
    would take every `candidate_id` with it.
    """
    slots: dict[tuple[str, str], list[ObservationRecord]] = {}
    for record in records:
        slots.setdefault(record.slot_key, []).append(record)
    return tuple(
        sorted(
            (canonicalize_slot(group) for group in slots.values()),
            key=lambda point: (
                point.metric_id,
                point.period.anchor_date or "",
                point.period.key,
            ),
        )
    )


def canonicalize_slot(records: Sequence[ObservationRecord]) -> CanonicalPoint:
    """§6.1 steps 1–6 for one `(metric_id, period_key)` slot."""
    if not records:
        raise ValueError("a fact-slot with no observation is not a slot")
    metric_id, _period_key = records[0].slot_key
    period = records[0].period
    subject = records[0].subject_entity_id

    quarantined, kept = _quarantine(records)
    warnings = set()
    if quarantined:
        warnings.add(LANE_DEFECT)
    if any(record.is_narrative and record.quoted_text is None for record in records):
        warnings.add(QUARANTINE_NOT_EVALUATED)
    if not kept:
        # Every reading of this slot was a flattened-table artefact. The slot exists — the
        # observations are still in the graph — and it emits no value, which is the same state
        # a conflict leaves it in and for the same reason: nothing here is citable.
        return _empty_point(metric_id, period, subject, records, quarantined, warnings)

    if len({record.unit for record in kept}) > 1:
        warnings.add(SLOT_UNIT_DISAGREEMENT)
    if len({record.currency for record in kept}) > 1:
        warnings.add(SLOT_CURRENCY_DISAGREEMENT)
    if any(record.filing_date is None for record in kept):
        warnings.add(FILING_DATE_UNKNOWN)

    clusters = _cluster(kept)
    status, winner, minority = _resolve(clusters)
    if minority:
        warnings.add(MINORITY_READING_PRESENT)
    supporting = winner if status is not CanonicalStatus.CONFLICT else []
    representative = _representative(supporting) if supporting else None
    documents = _documents(kept)

    return CanonicalPoint(
        metric_id=metric_id,
        period=period,
        subject_entity_id=subject,
        status=status,
        value=None if representative is None else representative.value,
        unit=representative.unit if representative else kept[0].unit,
        scale=representative.scale if representative else None,
        currency=representative.currency if representative else kept[0].currency,
        representative_observation_id=(
            None if representative is None else representative.observation_id
        ),
        supporting_observation_ids=_ids(supporting),
        minority_observation_ids=_ids(minority),
        quarantined_observation_ids=_ids(quarantined),
        n_docs=len(documents),
        first_filed=_first_filed(kept),
        last_filed=_last_filed(kept),
        warnings=tuple(sorted(warnings)),
        clusters=tuple(_as_cluster(group) for group in clusters),
        distinct_values=len({record.value for record in kept}),
    )


def census(points: Sequence[CanonicalPoint]) -> SlotCensus:
    """The numbers §6.1 states, and the ones a reader needs to check them.

    `observations` is counted off the points rather than taken as an argument, so it reports
    what canonicalisation *accounted for*. A caller that also knows how many records it fed in
    can compare the two, and a record that fell out of every slot would show up as the
    difference instead of as an echo of the input.
    """
    return SlotCensus(
        observations=sum(
            len(point.supporting_observation_ids)
            + len(point.minority_observation_ids)
            + len(point.quarantined_observation_ids)
            for point in points
        ),
        metrics=len({point.metric_id for point in points}),
        slots=len(points),
        multi_valued=sum(1 for point in points if point.distinct_values > 1),
        multi_cluster=sum(1 for point in points if len(point.clusters) > 1),
        ok=sum(1 for point in points if point.status is CanonicalStatus.OK),
        resolved_by_majority=sum(
            1 for point in points if point.status is CanonicalStatus.RESOLVED_BY_MAJORITY
        ),
        conflict=sum(1 for point in points if point.status is CanonicalStatus.CONFLICT),
        quarantined=sum(len(point.quarantined_observation_ids) for point in points),
    )


# -- reading the graph ---------------------------------------------------------------------


def load_observations(
    retriever: GraphRetriever,
    *,
    metric_ids: Sequence[str] | None = None,
    with_evidence: bool = True,
) -> ObservationLoad:
    """Every observation of every named metric, through §9's tools and nothing else.

    `metric_ids=None` asks `list_metrics` for the projected set — 26 in this run — rather than
    naming them here, because a 27th metric should arrive as more rows under the same contract
    (WORKSTREAM_BOUNDARY §5) and a hard-coded list is what turns that into a code change.
    """
    unreadable: list[tuple[str, str]] = []
    calls = 0
    if metric_ids is None:
        listed = retriever.call("list_metrics", {})
        calls += 1
        if listed.outcome is not RetrievalOutcome.OK:
            return ObservationLoad(unreadable=(("list_metrics", listed.reason),), calls=calls)
        metric_ids = [str(row["metric_id"]) for row in listed.rows]

    rows: list[Mapping[str, Any]] = []
    for metric_id in metric_ids:
        page, used, failure = _paged_history(retriever, metric_id)
        calls += used
        if failure is not None:
            unreadable.append((metric_id, failure))
            continue
        rows.extend(page)

    evidence: dict[str, Mapping[str, Any]] = {}
    if with_evidence:
        for row in rows:
            observation_id = str(row["observation_id"])
            result = retriever.call("get_fact_evidence", {"observation_id": observation_id})
            calls += 1
            if result.outcome is RetrievalOutcome.OK and result.rows:
                evidence[observation_id] = result.rows[0]

    return ObservationLoad(
        records=tuple(
            record_from_rows(row, evidence.get(str(row["observation_id"]))) for row in rows
        ),
        unreadable=tuple(unreadable),
        calls=calls,
    )


def record_from_rows(
    history: Mapping[str, Any], evidence: Mapping[str, Any] | None = None
) -> ObservationRecord:
    """One `get_metric_history` row, optionally enriched by its `get_fact_evidence` row.

    C2 in one function: every optional field is read with `.get` and an absent key and a `None`
    value produce the same record, because Neo4j cannot tell them apart and neither may a
    consumer.

    **The five cell indices come from `history`, not from `evidence`.** They are `:Observation`
    node properties (TABLE_CELL_CITATIONS §1.3), and both statements return them — but
    `evidence` is `None` on every path that loads a series without paying for a
    `get_fact_evidence` call per observation, and a coordinate that appeared only when someone
    asked for the quote would be a cell identity that comes and goes. `passage_kind` is the
    exception and stays on the evidence side: it belongs to the `:Passage`, which only the
    evidence statement reaches.
    """
    return ObservationRecord(
        observation_id=str(history["observation_id"]),
        metric_id=str(history["metric_id"]),
        subject_entity_id=_text(history, "subject_entity_id") or "",
        period=observation_period(history),
        value=float(history["value"]),
        unit=_text(history, "unit") or "",
        scale=_text(history, "scale"),
        currency=_text(history, "currency"),
        source_lane=_text(history, "source_lane") or "",
        validation_state=_text(history, "validation_state") or "",
        document_id=_text(history, "document_id"),
        passage_id=_text(history, "passage_id"),
        filing_date=None if evidence is None else _text(evidence, "filing_date"),
        quoted_text=None if evidence is None else _text(evidence, "quoted_text"),
        warning_codes=_texts(history, "warning_codes"),
        ambiguity_codes=_texts(history, "ambiguity_codes"),
        row_index=_integer(history, "row_index"),
        value_column_index=_integer(history, "value_column_index"),
        period_header_row_index=_integer(history, "period_header_row_index"),
        period_header_column_index=_integer(history, "period_header_column_index"),
        metric_label_row_index=_integer(history, "metric_label_row_index"),
        passage_kind=None if evidence is None else _text(evidence, "passage_kind"),
    )


def _paged_history(
    retriever: GraphRetriever, metric_id: str
) -> tuple[list[Mapping[str, Any]], int, str | None]:
    """Every row for one metric, past `get_metric_history`'s 200-row bound.

    Keyset paging on `$since`, which the statement applies to `coalesce(period_end,
    instant_date)` — the same expression it orders by, so a page boundary is a date and not an
    offset. Each page after the first re-reads the anchor date it stopped on, which is why
    `seen` is keyed by `observation_id`.

    The refusal is the important part. If a page comes back truncated and contributed nothing
    new, one anchor date holds more rows than the bound and no `$since` value can move past it;
    the loader says so rather than returning a silently short series.
    """
    seen: dict[str, Mapping[str, Any]] = {}
    since: str | None = None
    calls = 0
    while True:
        parameters: dict[str, Any] = {"metric_id": metric_id}
        if since is not None:
            parameters["since"] = since
        result = retriever.call("get_metric_history", parameters)
        calls += 1
        if result.outcome is not RetrievalOutcome.OK:
            return [], calls, result.reason or result.outcome.value
        added = 0
        for row in result.rows:
            observation_id = str(row.get("observation_id"))
            if observation_id not in seen:
                seen[observation_id] = row
                added += 1
        if not result.truncated:
            return list(seen.values()), calls, None
        anchor = _anchor_of(result.rows[-1])
        if anchor is None or (anchor == since and added == 0):
            return (
                [],
                calls,
                f"paging stalled at {anchor!r}: one anchor date holds more observations than "
                f"get_metric_history's row bound, so no $since value can advance past it",
            )
        since = anchor


def _anchor_of(row: Mapping[str, Any]) -> str | None:
    return _text(row, "period_end") or _text(row, "instant_date")


# -- §6.1's six steps ----------------------------------------------------------------------


def _quarantine(
    records: Sequence[ObservationRecord],
) -> tuple[list[ObservationRecord], list[ObservationRecord]]:
    """Step 1. A narrative record with no quote is *not* quarantined, and says so elsewhere.

    Refusing to quarantine on missing evidence rather than quarantining on it: the rule is a
    statement about a quote, and an absent quote is a statement about the loader. The point
    carries `quarantine_not_evaluated` so the difference is visible instead of assumed.
    """
    quarantined = [
        record
        for record in records
        if record.is_narrative
        and record.quoted_text is not None
        and is_flattened_table_read(record.quoted_text)
    ]
    excluded = {record.observation_id for record in quarantined}
    kept = [record for record in records if record.observation_id not in excluded]
    return quarantined, kept


def _cluster(records: Sequence[ObservationRecord]) -> list[list[ObservationRecord]]:
    """Step 2. Single-linkage over the sorted values, at each pair's coarser tolerance.

    Single-linkage — each value is compared with its neighbour, not with the cluster's first
    member — because presentation tolerance is a property of a *pair* of printings. Chaining is
    the known cost: three readings a million apart in sequence become one cluster. It does not
    arise here (every multi-valued slot in the run holds exactly two readings, one thousands and
    one millions), and the alternative, anchoring on the first value, would split
    `[24.6M, 25.0M, 25.6M]` into two clusters that each round to the same printed figure.
    """
    ordered = sorted(records, key=lambda record: (record.value, record.observation_id))
    clusters: list[list[ObservationRecord]] = [[ordered[0]]]
    for record in ordered[1:]:
        previous = clusters[-1][-1]
        tolerance = max(previous.tolerance, record.tolerance)
        if within_tolerance(previous.value, record.value, tolerance):
            clusters[-1].append(record)
        else:
            clusters.append([record])
    return clusters


def _resolve(
    clusters: Sequence[Sequence[ObservationRecord]],
) -> tuple[CanonicalStatus, list[ObservationRecord], list[ObservationRecord]]:
    """Steps 3–4. One cluster is `ok`; more than one goes to the document-support majority.

    The majority is over **distinct documents**, and the bar is two rules deep on purpose: the
    winner needs at least two documents *and* at least twice the runner-up. One filing cannot
    outvote another however many rows it contributes, and two-against-one is a disagreement, not
    a resolution.
    """
    if len(clusters) == 1:
        return CanonicalStatus.OK, list(clusters[0]), []
    ranked = sorted(
        clusters,
        key=lambda group: (-len(_documents(group)), _representative_key(_representative(group))),
    )
    top, runner_up = ranked[0], ranked[1]
    top_docs, runner_docs = len(_documents(top)), len(_documents(runner_up))
    losers = [record for group in ranked[1:] for record in group]
    if top_docs >= 2 and top_docs >= 2 * runner_docs:
        return CanonicalStatus.RESOLVED_BY_MAJORITY, list(top), losers
    return CanonicalStatus.CONFLICT, [], [record for group in ranked for record in group]


def _representative(records: Sequence[ObservationRecord]) -> ObservationRecord:
    """Step 5. `(scale_precision, filing_date, observation_id)`, earliest filing wins ties.

    *"That is the reading the market saw first, and the one whose text a post will quote."* The
    id is the last key and is unique, so the choice is total: two runs over the same slot pick
    the same observation whatever order the rows arrived in.
    """
    return min(records, key=_representative_key)


def _representative_key(record: ObservationRecord) -> tuple[int, str, str]:
    return (
        record.scale_precision,
        record.filing_date or _UNKNOWN_FILING_DATE,
        record.observation_id,
    )


def _empty_point(
    metric_id: str,
    period: StoryPeriod,
    subject: str,
    records: Sequence[ObservationRecord],
    quarantined: Sequence[ObservationRecord],
    warnings: set[str],
) -> CanonicalPoint:
    """A slot whose every reading was quarantined. Status `conflict`: it emits no value."""
    return CanonicalPoint(
        metric_id=metric_id,
        period=period,
        subject_entity_id=subject,
        status=CanonicalStatus.CONFLICT,
        value=None,
        unit=records[0].unit,
        scale=None,
        currency=records[0].currency,
        representative_observation_id=None,
        quarantined_observation_ids=_ids(quarantined),
        n_docs=len(_documents(records)),
        warnings=tuple(sorted(warnings)),
    )


def _as_cluster(records: Sequence[ObservationRecord]) -> ValueCluster:
    return ValueCluster(
        value=_representative(records).value,
        observation_ids=_ids(records),
        document_ids=tuple(_documents(records)),
    )


def _documents(records: Iterable[ObservationRecord]) -> list[str]:
    return sorted({record.document_id for record in records if record.document_id})


def _ids(records: Iterable[ObservationRecord]) -> tuple[str, ...]:
    return tuple(sorted(record.observation_id for record in records))


def _first_filed(records: Iterable[ObservationRecord]) -> str | None:
    dates = sorted(record.filing_date for record in records if record.filing_date)
    return dates[0] if dates else None


def _last_filed(records: Iterable[ObservationRecord]) -> str | None:
    dates = sorted(record.filing_date for record in records if record.filing_date)
    return dates[-1] if dates else None


def _text(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) and value else None


def _integer(row: Mapping[str, Any], key: str) -> int | None:
    """A grid coordinate, or `None` for a row that has no position in a grid.

    `isinstance(value, bool)` is excluded because `bool` is a subclass of `int` in Python and a
    driver-returned boolean arriving as row 1 is the kind of coordinate that would resolve to a
    real cell and be wrong. Nothing on `:Observation` carries a boolean under these names today;
    the guard is here because the failure mode is silent.
    """
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _texts(row: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = row.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


__all__ = [
    "FILING_DATE_UNKNOWN",
    "LANE_DEFECT",
    "MINORITY_READING_PRESENT",
    "MIN_FLATTENED_NUMERALS",
    "POLICY_VERSION",
    "QUARANTINE_NOT_EVALUATED",
    "SLOT_CURRENCY_DISAGREEMENT",
    "SLOT_UNIT_DISAGREEMENT",
    "ObservationLoad",
    "SlotCensus",
    "canonicalize",
    "canonicalize_slot",
    "census",
    "is_flattened_table_read",
    "load_observations",
    "record_from_rows",
]
