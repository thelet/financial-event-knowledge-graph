"""Markdown reports: corpus quality, manual review, and parser comparison.

The review report exists so a human can judge what automation cannot: whether an inferred
hierarchy is right, whether a table reads, whether a passage is coherent. It therefore
leads with the things that need judgement rather than with totals.
"""

from __future__ import annotations

from collections import Counter

from ...core.config import AppConfig
from ...core.models import NormalizedDocument
from ...core.storage import LocalNormalizedStore
from ...utils.text import truncate
from .public import ReportRequest, ReportResult

REVIEW_DIMENSIONS = (
    "title", "section_hierarchy", "paragraph_order", "table_readability", "missing_content",
    "duplicated_content", "boilerplate_contamination", "traceability", "passage_coherence",
    "passage_size", "image_detection", "parser_failure",
)


class MarkdownReportStage:
    name = "report"

    def __init__(self, config: AppConfig, store: LocalNormalizedStore) -> None:
        self._config = config
        self._store = store

    def run(self, request: ReportRequest) -> ReportResult:
        root = self._config.reports_root
        root.mkdir(parents=True, exist_ok=True)
        result = ReportResult()

        corpus = _corpus_report(request)
        result.corpus_path = root / f"{request.run_id}-corpus.md"
        result.corpus_path.write_text(corpus, encoding="utf-8")

        if request.review:
            review = _review_report(request, self._store)
            result.review_path = root / f"{request.run_id}-review.md"
            result.review_path.write_text(review, encoding="utf-8")

        if request.comparisons:
            result.comparison_path = root / f"{request.run_id}-parser-comparison.md"
            result.comparison_path.write_text(_comparison_report(request), encoding="utf-8")
        return result


# --------------------------------------------------------------------------------------
# Corpus report
# --------------------------------------------------------------------------------------


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join(lines)


def _corpus_report(request: ReportRequest) -> str:
    docs = request.documents
    if not docs:
        return "# Normalized corpus report\n\nNo documents.\n"

    total_chars = sum(d.stats.char_count for d in docs)
    total_passages = sum(d.stats.passage_count for d in docs)
    total_tables = sum(d.stats.table_count for d in docs)

    out = [
        "# Normalized corpus report",
        "",
        f"Documents **{len(docs)}** | Passages **{total_passages}** | "
        f"Tables **{total_tables}** | Characters **{total_chars:,}**",
        "",
        "## By form and role",
        "",
    ]
    counts: Counter = Counter((d.form, d.artifact_role or d.document_type) for d in docs)
    out.append(_table(
        ["Form", "Role", "Docs", "Passages", "Tables", "Chars"],
        [[form, role, str(n),
          str(sum(d.stats.passage_count for d in docs if d.form == form and (d.artifact_role or d.document_type) == role)),
          str(sum(d.stats.table_count for d in docs if d.form == form and (d.artifact_role or d.document_type) == role)),
          f"{sum(d.stats.char_count for d in docs if d.form == form and (d.artifact_role or d.document_type) == role):,}"]
         for (form, role), n in sorted(counts.items())],
    ))

    out += ["", "## Parser outcomes", ""]
    parser_counts = Counter(f"{d.parser_name} {d.parser_version}" for d in docs)
    out.append(_table(["Parser", "Documents"], [[k, str(v)] for k, v in sorted(parser_counts.items())]))

    out += ["", "## Table classification", ""]
    kinds: Counter = Counter()
    for d in docs:
        for b in d.blocks:
            if b.table is not None:
                kinds[b.table.table_kind] += 1
    out.append(_table(["Kind", "Count"], [[k, str(v)] for k, v in sorted(kinds.items())]))

    out += ["", "## Passage size distribution", ""]
    out.append(_table(
        ["Metric", "Value"],
        [["documents", str(len(docs))],
         ["passages", str(total_passages)],
         ["mean passages/doc", f"{total_passages/len(docs):.1f}"],
         ["mean chars/passage", f"{total_chars/total_passages:.0f}" if total_passages else "-"]],
    ))

    out += ["", "## Flags requiring attention", ""]
    flag_counts: Counter = Counter(f for d in docs for f in d.flags)
    if flag_counts:
        out.append(_table(["Flag", "Documents"], [[k, str(v)] for k, v in sorted(flag_counts.items())]))
    else:
        out.append("None.")

    out += ["", "## Issues", ""]
    issue_counts = Counter((i.severity, i.code) for i in request.issues)
    if issue_counts:
        out.append(_table(["Severity", "Code", "Count"],
                          [[s, c, str(n)] for (s, c), n in sorted(issue_counts.items())]))
    else:
        out.append("None.")

    out += ["", "## Largest documents", ""]
    out.append(_table(
        ["Chars", "Blocks", "Sections", "Passages", "Form", "Role", "File"],
        [[f"{d.stats.char_count:,}", str(d.stats.block_count), str(d.stats.section_count),
          str(d.stats.passage_count), d.form, d.artifact_role or "-", d.original_filename[:40]]
         for d in sorted(docs, key=lambda d: -d.stats.char_count)[:12]],
    ))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------
# Review report
# --------------------------------------------------------------------------------------


def _review_report(request: ReportRequest, store: LocalNormalizedStore) -> str:
    docs = request.documents
    out = [
        "# Normalization review",
        "",
        f"Run `{request.run_id}` — {len(docs)} document(s).",
        "",
        "Judgement required on: inferred section hierarchy, table readability, passage "
        "coherence, and anything flagged below. Record findings in "
        "`normalization_review.jsonl` keyed by run and parser so two versions can be diffed.",
        "",
        "Dimensions: " + ", ".join(f"`{d}`" for d in REVIEW_DIMENSIONS),
        "",
        "## Warnings first",
        "",
    ]
    flagged = [d for d in docs if d.flags]
    if flagged:
        out.append(_table(
            ["Document", "Form", "Role", "Flags"],
            [[truncate(d.original_filename, 34), d.form, d.artifact_role or "-", ", ".join(d.flags)]
             for d in sorted(flagged, key=lambda d: d.original_filename)],
        ))
    else:
        out.append("No document raised a flag.")

    out += ["", "---", "", "## Per-document review", ""]
    for document in sorted(docs, key=lambda d: (d.form, d.original_filename)):
        out.extend(_document_review(document, store))
    return "\n".join(out) + "\n"


def _document_review(document: NormalizedDocument, store: LocalNormalizedStore) -> list[str]:
    out = [
        f"### {document.original_filename}",
        "",
        f"- **Form** {document.form} | **Role** {document.artifact_role or '-'} "
        f"| **Filed** {document.filing_date} | **Items** {', '.join(document.items) or '-'}",
        f"- **Type** {document.document_type} | **Parser** {document.parser_name} "
        f"{document.parser_version}",
        f"- **Title** {document.title or '(none)'}",
        f"- **Stats** {document.stats.block_count} blocks, {document.stats.section_count} sections, "
        f"{document.stats.passage_count} passages, {document.stats.table_count} tables, "
        f"{document.stats.char_count:,} chars, depth {document.stats.max_section_depth}",
        f"- **Flags** {', '.join(document.flags) or 'none'}",
        f"- **Source** {document.source_url}",
        "",
        "**Inferred hierarchy**",
        "",
        "```text",
    ]
    for section in document.sections[:40]:
        indent = "  " * section.level
        marker = {"filing_label": "L", "styled_text": "s", "synthetic_root": "R"}[section.heading_source]
        out.append(
            f"{indent}[{marker}{section.heading_confidence:.2f}] {truncate(section.title, 70)} "
            f"({len(section.block_ids)} blocks, {section.char_count:,} chars)"
        )
    if len(document.sections) > 40:
        out.append(f"  ... and {len(document.sections) - 40} more sections")
    out += ["```", ""]

    tables = [b for b in document.blocks if b.table is not None]
    if tables:
        out += ["**First table**", "", f"kind=`{tables[0].table.table_kind}` "
                f"confidence={tables[0].table.kind_confidence:.2f} "
                f"{tables[0].table.n_rows}x{tables[0].table.n_cols}", "", "```text"]
        out.append("\n".join(tables[0].table.markdown.splitlines()[:10]))
        out += ["```", ""]

    out += ["**First blocks**", "", "```text"]
    shown = 0
    for block in document.blocks:
        if block.block_type in ("page_header", "page_number") or not block.text.strip():
            continue
        out.append(f"[{block.block_type}] {truncate(block.text, 180)}")
        shown += 1
        if shown >= 6:
            break
    out += ["```", "", "---", ""]
    return out


# --------------------------------------------------------------------------------------
# Parser comparison
# --------------------------------------------------------------------------------------


def _comparison_report(request: ReportRequest) -> str:
    out = [
        "# Parser comparison",
        "",
        f"Run `{request.run_id}` — {len(request.comparisons)} artifact(s), both parsers.",
        "",
        "`char_ratio` is fallback chars / default chars. Values near 1.0 mean the two "
        "implementations agree on how much text the document contains.",
        "",
    ]
    out.append(_table(
        ["Artifact", "def blocks", "fb blocks", "def chars", "fb chars", "ratio",
         "def tbl", "fb tbl", "def head", "fb head"],
        [[truncate(c.artifact_id.split(":")[-1], 34), str(c.default_blocks), str(c.fallback_blocks),
          f"{c.default_chars:,}", f"{c.fallback_chars:,}", f"{c.char_ratio:.2f}",
          str(c.default_tables), str(c.fallback_tables),
          str(c.default_headings), str(c.fallback_headings)]
         for c in request.comparisons],
    ))
    notes = [(c.artifact_id, n) for c in request.comparisons for n in c.notes]
    if notes:
        out += ["", "## Notes", ""]
        out.extend(f"- `{a.split(':')[-1]}`: {n}" for a, n in notes)

    ratios = [c.char_ratio for c in request.comparisons if c.char_ratio]
    if ratios:
        divergent = [c for c in request.comparisons if c.char_ratio and not 0.8 <= c.char_ratio <= 1.25]
        out += ["", "## Divergence", "",
                f"mean ratio {sum(ratios)/len(ratios):.2f}; "
                f"{len(divergent)} artifact(s) outside 0.80-1.25."]
        for c in divergent:
            out.append(f"- `{c.artifact_id.split(':')[-1]}` ratio {c.char_ratio:.2f}")
    return "\n".join(out) + "\n"
