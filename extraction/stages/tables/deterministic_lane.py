"""The deterministic table claim lane.

No provider of any kind. Everything it emits derives from the selected table passage, the
preceding passage selection attached to it, document metadata, the ontology registry, and
the two shared readers. An executable test fails if this package imports a generation or
embedding provider, the benchmark, or a graph component.

The lane emits `LaneClaim` objects only. Turning those into `OntologyClaim`s is `assemble`'s
job, and keeping the split is what lets this be tested against a table with no ontology
claim machinery in the way.

Row labels resolve through the ontology registry with the step-4 typography fold, so the
120-day metric — labelled with straight quotes in the vocabulary and printed with curly ones
in every filing — resolves in both. Aliases are never broadened to make a fixture pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core import numbers, periods
from ...core.models import (
    LaneAbstention,
    LaneClaim,
    LaneResult,
    PeriodRef,
    ScaleDeclaration,
)
from ..select.alias_evidence import AliasIndex, fold
from .header_analysis import HeaderAnalysis, PeriodColumn, analyse, resolve_for_row
from .public import (
    AMBIGUOUS_ALIAS,
    AMBIGUOUS_COLUMN_ALIGNMENT,
    DEFERRED_REQUIRED_SOURCE_LANE,
    DERIVED_CHANGE_COLUMN,
    INVALID_NUMBER,
    METRIC_DEFAULT,
    MISSING_PERIOD,
    MISSING_UNIT,
    PERIOD_TYPE_MISMATCH,
    PRECEDING_CONTEXT,
    REGISTRANT_METADATA,
    TABLE_HEADER,
    UNRESOLVED_METRIC,
    UNSUPPORTED_TABLE_SHAPE,
    TableIssue,
)
from .table_grid import Cell, TableGrid, parse_markdown_table

LANE_NAME = "normalized_table"
LANE_VERSION = "1.0.0"

# Rows that structure a reconciliation rather than report a metric.
_STRUCTURAL_LABELS = frozenset({"adjustments:", "adjustments", "non-gaap financial highlights",
                                "non-gaap financial highlights (1)"})


@dataclass
class TableExtraction:
    """Everything one table produced, claims and explained silences alike."""

    claims: list[LaneClaim] = field(default_factory=list)
    issues: list[TableIssue] = field(default_factory=list)
    header: HeaderAnalysis | None = None

    def as_lane_result(self, passage_id: str) -> LaneResult:
        return LaneResult(
            passage_id=passage_id,
            lane=LANE_NAME,
            claims=tuple(self.claims),
            abstentions=tuple(
                LaneAbstention(
                    reason=issue.code,
                    detail=issue.detail,
                    passage_id=issue.passage_id,
                    candidate_metric_ids=issue.candidate_concept_ids,
                    row_label=issue.raw_label,
                )
                for issue in self.issues
            ),
        )


class DeterministicTableClaimLane:
    """Reads claims out of a normalized table passage. No model, no network."""

    name = LANE_NAME
    version = LANE_VERSION

    def __init__(self, ontology, alias_index: AliasIndex, deferred_metric_ids: frozenset[str]):
        self._ontology = ontology
        self._aliases = alias_index
        # Derived from the ontology by the caller, never a literal list here. A metric whose
        # first source lane is unavailable must not be emitted merely because a table row
        # happens to match it.
        self._deferred = deferred_metric_ids

    def supports(self, candidate) -> bool:
        return candidate.passage_kind == "table" and candidate.lane == "tables"

    def extract(self, candidate, text: str, preceding_text: str | None = None) -> LaneResult:
        return self.extract_table(
            text,
            passage_id=candidate.passage_id,
            preceding_text=preceding_text,
            preceding_passage_id=candidate.preceding_passage_id,
        ).as_lane_result(candidate.passage_id)

    # -- the flow ---------------------------------------------------------------------------

    def extract_table(
        self,
        text: str,
        *,
        passage_id: str,
        document_id: str | None = None,
        table_id: str | None = None,
        block_ids: tuple[str, ...] = (),
        preceding_text: str | None = None,
        preceding_passage_id: str | None = None,
        subject_entity_id: str = "opendoor",
        subject_type: str = "public_company",
    ) -> TableExtraction:
        result = TableExtraction()
        grid = parse_markdown_table(
            text, passage_id=passage_id, table_id=table_id, block_ids=block_ids)
        document = document_id or passage_id.split("#")[0]

        if len(grid.data_rows()) < 2 or grid.width < 2:
            result.issues.append(TableIssue(
                UNSUPPORTED_TABLE_SHAPE,
                f"table has {len(grid.data_rows())} data rows and width {grid.width}",
                passage_id, table_id))
            return result

        header = analyse(grid)
        result.header = header
        if not header.ok:
            result.issues.append(TableIssue(
                MISSING_PERIOD,
                "no column resolved to a period; "
                + (f"unresolved labels: {[l for _, l in header.unresolved_columns][:4]}"
                   if header.unresolved_columns else "no header row carried period labels"),
                passage_id, table_id))
            return result

        scale = self._scale_for(grid, header, preceding_text, preceding_passage_id)

        for row_index in grid.data_rows():
            if row_index < header.first_data_row:
                continue
            self._read_row(grid, header, row_index, scale, result,
                           passage_id=passage_id, document_id=document, table_id=table_id,
                           block_ids=block_ids, preceding_passage_id=preceding_passage_id,
                           subject_entity_id=subject_entity_id, subject_type=subject_type)
        return result

    # -- one row ----------------------------------------------------------------------------

    def _read_row(
        self, grid, header, row_index, scale, result, *, passage_id, document_id, table_id,
        block_ids, preceding_passage_id, subject_entity_id, subject_type,
    ) -> None:
        raw_label = grid.row_label(row_index)
        if not raw_label or raw_label.lower() in _STRUCTURAL_LABELS:
            return

        normalized = fold(raw_label).strip()
        concept_ids, ambiguous = self._resolve_label(raw_label)

        if ambiguous:
            result.issues.append(TableIssue(
                AMBIGUOUS_ALIAS,
                f"{raw_label!r} resolves to {len(concept_ids)} concepts; no claim emitted",
                passage_id, table_id, row_index=row_index,
                raw_label=raw_label, normalized_label=normalized,
                candidate_concept_ids=concept_ids))
            return

        if not concept_ids:
            result.issues.append(TableIssue(
                UNRESOLVED_METRIC, f"{raw_label!r} matches no metric in the ontology",
                passage_id, table_id, row_index=row_index,
                raw_label=raw_label, normalized_label=normalized))
            return

        metric_id = concept_ids[0]
        if metric_id in self._deferred:
            result.issues.append(TableIssue(
                DEFERRED_REQUIRED_SOURCE_LANE,
                f"{metric_id} requires a source lane this corpus does not contain",
                passage_id, table_id, row_index=row_index,
                raw_label=raw_label, normalized_label=normalized,
                candidate_concept_ids=(metric_id,), required_lane="xbrl"))
            return

        metric = self._ontology.registry.find(metric_id)
        unit, currency = self._unit_for(metric, raw_label, grid, row_index)
        if unit is None:
            result.issues.append(TableIssue(
                MISSING_UNIT, f"no unit determinable for {metric_id}",
                passage_id, table_id, row_index=row_index, raw_label=raw_label))
            return

        cells, misalignment = self._value_cells(grid, row_index, header)
        if misalignment:
            result.issues.append(TableIssue(
                AMBIGUOUS_COLUMN_ALIGNMENT, f"{raw_label!r}: {misalignment}",
                passage_id, table_id, row_index=row_index, raw_label=raw_label))
            return

        for column, cell in zip(header.period_columns, cells):
            if cell is None:
                continue
            self._read_cell(
                grid, column, cell, row_index, metric, metric_id, unit, currency, scale,
                result, raw_label=raw_label, passage_id=passage_id, document_id=document_id,
                table_id=table_id, block_ids=block_ids,
                preceding_passage_id=preceding_passage_id,
                subject_entity_id=subject_entity_id, subject_type=subject_type)

    def _read_cell(
        self, grid, column: PeriodColumn, cell: Cell, row_index, metric, metric_id, unit,
        currency, scale, result, *, raw_label, passage_id, document_id, table_id, block_ids,
        preceding_passage_id, subject_entity_id, subject_type,
    ) -> None:
        if column.is_change_column:
            result.issues.append(TableIssue(
                DERIVED_CHANGE_COLUMN,
                f"column {column.column_label!r} is a period-over-period change",
                passage_id, table_id, row_index=row_index,
                column_index=column.column_index, raw_label=raw_label))
            return

        raw_value = cell.text.strip()

        if numbers.is_nil(raw_value):
            # A dash is nil, not zero and not missing. Recorded and skipped: converting it
            # to 0 would assert a reported figure the filing does not contain.
            return
        magnitude = numbers.parse_magnitude(raw_value)
        if magnitude is None:
            if raw_value and raw_value.upper() not in {"N/A", "NA", "NM"}:
                result.issues.append(TableIssue(
                    INVALID_NUMBER, f"{raw_value!r} is not a magnitude",
                    passage_id, table_id, row_index=row_index,
                    column_index=column.column_index, raw_label=raw_label,
                    raw_value=raw_value))
            return

        period = resolve_for_row(column, raw_label)
        if period is None:
            result.issues.append(TableIssue(
                MISSING_PERIOD, f"column {column.column_label!r} carries no usable period",
                passage_id, table_id, row_index=row_index,
                column_index=column.column_index, raw_label=raw_label))
            return

        mismatch = self._period_type_mismatch(metric, period)
        if mismatch:
            result.issues.append(TableIssue(
                PERIOD_TYPE_MISMATCH, mismatch, passage_id, table_id, row_index=row_index,
                column_index=column.column_index, raw_label=raw_label))
            return

        value, applied = self._apply_scale(magnitude, unit, raw_label, scale)

        result.claims.append(LaneClaim(
            metric_id=metric_id,
            value=value,
            unit=unit,
            currency=currency,
            period=period,
            subject_entity_id=subject_entity_id,
            subject_type=subject_type,
            source_lane=LANE_NAME,
            assertion_type="explicitly_reported",
            passage_id=passage_id,
            document_id=document_id,
            raw_text=raw_value,
            scale=applied,
            row_label=raw_label,
            column_label=column.column_label,
            population_definition_raw=(
                raw_label if metric_id == "pct_homes_on_market_gt_120_days" else None),
            extractor_metadata={
                "lane": LANE_NAME,
                "lane_version": LANE_VERSION,
                "table_id": table_id,
                "block_ids": list(block_ids),
                "row_index": cell.row_index,
                "value_column_index": cell.column_index,
                "period_header_row_index": column.header_row_index,
                "period_header_column_index": column.column_index,
                "metric_label_row_index": row_index,
                "subject_basis": REGISTRANT_METADATA,
                "scale_source": applied.location if applied else METRIC_DEFAULT,
                "preceding_context_passage_id": (
                    preceding_passage_id if applied and applied.location == PRECEDING_CONTEXT
                    else None),
            },
        ))

    # -- resolution helpers -------------------------------------------------------------------

    def _resolve_label(self, raw_label: str) -> tuple[tuple[str, ...], bool]:
        """Resolve a row label to metric concepts, and say whether it is ambiguous.

        **Whole-label match only, after stripping footnote markers.** Substring matching is
        what a deterministic lane must not do, and the reconciliation tables show why: it
        reads "Contribution Profit per Home Sold" as `contribution_profit` (a $31 per-home
        figure emitted as a $31,000 quarterly total) and "Holding costs on sales – Current
        Period" as `holding_costs`, which is one of two rows the filing never totals. Both
        are wrong in ways that pass every downstream check.

        Footnote markers are stripped because they are typography, not meaning: "Direct
        selling costs(3)" is the metric, "(3)" is a reference to a note below the table.

        A whole-label exact match also outranks a same-spelled ambiguous alias.
        `gaap_gross_margin`'s canonical label *is* "Gross Margin", and `aliases.yaml`
        separately declares that string ambiguous across the GAAP and adjusted concepts, so
        without this the metric is unreachable from its own name and every KPI table's
        `Gross Margin` row yields nothing. The ambiguity entry's own note says what it is
        for — "Only the qualifier 'Adjusted' separates them, and it is sometimes only in the
        row label" — which is a rule about bare prose mentions, not about a cell whose
        entire contents are the metric's name.

        Anything that is not a whole-label match yields no claim: either `AMBIGUOUS_ALIAS`
        when the label's only matches are declared ambiguous, or `UNRESOLVED_METRIC`.
        """
        normalized = _strip_footnotes(fold(raw_label)).strip().lower()

        canonical = self._aliases.canonical_labels.get(normalized)
        if canonical:
            return (canonical,), False

        exact = self._aliases.by_surface.get(normalized)
        if exact:
            metric_ids = tuple(c for c in exact if c in self._aliases.metric_concepts)
            if len(metric_ids) == 1:
                return metric_ids, False
            if len(metric_ids) > 1:
                return metric_ids, True

        hits = [h for h in self._aliases.hits(raw_label) if h.is_metric]
        ambiguous_hits = [h for h in hits if h.ambiguous]
        if ambiguous_hits:
            widest = max(ambiguous_hits, key=lambda h: len(h.surface_form))
            candidates = tuple(sorted(
                c for c in widest.concept_ids if c in self._aliases.metric_concepts))
            return candidates, True
        return (), False

    def _unit_for(self, metric, raw_label: str, grid: TableGrid, row_index: int):
        """Unit and currency for a row.

        Monetary metrics report absolute USD. The ontology's `USD_millions` names the unit a
        metric is *customarily presented* in, not the scale a particular table used - the
        same metric appears in thousands in a 2021 reconciliation and millions in a 2025
        KPI table. Emitting the declared string as the unit would make the claim's unit
        depend on the vocabulary's presentation choice rather than on the filing.
        """
        declared = str(getattr(metric, "unit", "") or "")
        value_type = str(getattr(metric, "value_type", "") or "")
        if declared == "percent" or value_type == "percentage":
            return "percent", None
        if value_type == "monetary" or declared.lower().startswith("usd"):
            return "usd", "USD"
        return declared or None, None

    def _period_type_mismatch(self, metric, period: PeriodRef) -> str | None:
        """Refuse a period whose shape the metric forbids.

        An instant metric read as a duration and a duration metric read as an instant are
        both wrong in ways that survive every other check, so neither is inferred from the
        table's shape alone.
        """
        declared = str(getattr(metric, "period_type", "") or "")
        if declared == "instant" and not period.is_instant:
            return f"{metric.concept_id} is an instant metric but the column gave a duration"
        if declared == "duration" and period.is_instant:
            return f"{metric.concept_id} is a duration metric but the column gave an instant"
        return None

    def _apply_scale(self, magnitude, unit, raw_label, scale):
        """Scale applies to money, never to counts.

        "(in thousands, except percentages)" does not except "Homes sold in period", but the
        row is a count of homes and multiplying it by a thousand produced 2,462,000 homes
        sold in a quarter. The exception list is a presentation note about the monetary
        columns; the unit decides whether a scale can apply at all.
        """
        if unit != "usd" or scale is None:
            return magnitude, None if scale is None else ScaleDeclaration(
                scale="units", location=scale.location,
                source_passage_id=scale.source_passage_id,
                declaration_text=scale.declaration_text,
                exceptions_text=scale.exceptions_text)
        if numbers.scale_excludes(scale.exceptions_text, raw_label):
            return magnitude, ScaleDeclaration(
                scale="units", location=scale.location,
                source_passage_id=scale.source_passage_id,
                declaration_text=scale.declaration_text,
                exceptions_text=scale.exceptions_text)
        return numbers.apply_scale(magnitude, scale.scale), scale

    def _scale_for(self, grid, header, preceding_text, preceding_passage_id):
        """Table-local declaration wins; the preceding passage is the fallback.

        Precedence is not arbitrary. A table that states its own scale is stating it for
        itself, while a preceding paragraph may introduce several tables — so the nearer,
        narrower declaration governs. 65 of 74 KPI-bearing tables declare it inline and 9
        rely on the passage before.
        """
        for row_index in grid.data_rows():
            for cell in grid.rows[row_index]:
                found = numbers.parse_scale_declaration(cell.text)
                if found:
                    return ScaleDeclaration(
                        scale=found[0], location=TABLE_HEADER,
                        declaration_text=cell.text.strip(), exceptions_text=found[1])
        if preceding_text:
            found = numbers.parse_scale_declaration(preceding_text)
            if found:
                return ScaleDeclaration(
                    scale=found[0], location=PRECEDING_CONTEXT,
                    source_passage_id=preceding_passage_id,
                    declaration_text=_declaration_line(preceding_text),
                    exceptions_text=found[1])
        return None

    def _value_cells(
        self, grid: TableGrid, row_index: int, header: HeaderAnalysis
    ) -> tuple[list[Cell | None], str | None]:
        """Align a data row's values to the period columns, by ordinal position.

        Raw column index does not work and the corpus says so plainly. In the Q1 2025 KPI
        table the header dates sit at columns 2, 4, 6, 8, 10 while the `Homes sold` values
        sit at 2, 5, 8, 11, 14 — because a `$` row and a `%` row consume different numbers
        of layout cells, so every row has its own spacing. Indexing positionally drops the
        third column and shifts every later period by one, producing claims that are right
        about the metric and the value and wrong about the year.

        What survives the layout is *order*: the k-th reported magnitude in a row belongs to
        the k-th period column. Returns a diagnostic string instead of an alignment when the
        counts disagree, because emitting a value under an alignment that is only one of
        several possible is exactly the silent error this lane exists to avoid.
        """
        row = grid.rows[row_index] if 0 <= row_index < len(grid.rows) else []
        label_column = next((c.column_index for c in row if not c.is_empty), -1)

        values: list[Cell] = []
        for cell in row:
            if cell.column_index <= label_column:
                continue
            text = cell.text.strip()
            if not text or text in {"$", "%", "(", ")"}:
                continue
            if numbers.is_nil(text):
                values.append(cell)
                continue
            if numbers.parse_magnitude(text) is not None:
                values.append(cell)

        columns = header.period_columns
        if len(values) == len(columns):
            return list(values), None

        # A change column carries a value on some rows and not others: the 10-Q prints
        # "Homes sold 2,946 / 3,078 / (132)" but "Percentage ... 27% / 15%" with the change
        # cell blank. Falling back to the reporting columns alone resolves both without
        # guessing, because the change column is never emitted anyway.
        reporting = [c for c in columns if not c.is_change_column]
        if len(values) == len(reporting):
            aligned: list[Cell | None] = []
            iterator = iter(values)
            for column in columns:
                aligned.append(None if column.is_change_column else next(iterator))
            return aligned, None

        return [None] * len(columns), (
            f"row has {len(values)} values for {len(columns)} period columns "
            f"({len(reporting)} reporting)")

def _next_column_start(grid: TableGrid, column: PeriodColumn) -> int:
    starts = sorted(
        c.column_index for row in grid.rows for c in row
        if c.row_index == column.header_row_index and not c.is_empty
        and c.column_index > column.column_index
    )
    return starts[0] if starts else grid.width + 1


_PERIOD_QUALIFIER = re.compile(r"\s*\((?:at\s+period\s+end|unaudited)\)\s*$", re.I)


def _strip_footnotes(label: str) -> str:
    """Normalise a row label for matching.

    Two removals, both typography rather than identity. Trailing note references — "(3)",
    "(4)(5)" — point at a note below the table. And "(at period end)" states *when* a stock
    was measured, not *what* it is: the ontology happens to carry a suffixed alias for
    `housing_inventory_homes` and not for `market_count`, and the metric a row reports
    should not depend on that inconsistency.

    The suffix is stripped for matching only. `periods.is_instant_label` still reads it off
    the original label, so the row is correctly typed as an instant.
    """
    cleaned = re.sub(r"(\s*\(\d+\))+\s*$", "", label or "").strip()
    return _PERIOD_QUALIFIER.sub("", cleaned).strip()


def _declaration_line(text: str) -> str:
    for line in text.splitlines():
        if numbers.parse_scale_declaration(line):
            return line.strip()
    return text.strip()[:120]
