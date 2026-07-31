"""Corpus report.

Observations about the acquired corpus, for human review. Deliberately *not* acceptance
criteria: an Item 2.02 filing without an EX-99.1 is a pattern worth looking at, not a
pipeline defect (v0 plan section 12).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.config import AppConfig
from ...core.manifests import ManifestRepository
from ...core.models import ArtifactManifest
from ...core.storage import ARTIFACTS_CATALOG, FILINGS_CATALOG
from ...utils.jsonl import read_jsonl

ITEM_RESULTS_OF_OPERATIONS = "2.02"


def build_corpus_report(
    catalog_root: Path, artifact_manifest: ArtifactManifest | None = None
) -> str:
    filings = read_jsonl(Path(catalog_root) / FILINGS_CATALOG)
    artifacts = read_jsonl(Path(catalog_root) / ARTIFACTS_CATALOG)

    if not filings:
        return "# Corpus report\n\nNo filings in the catalog. Run download and build-catalog.\n"

    by_filing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        by_filing[artifact["filing_id"]].append(artifact)

    sections = [
        "# Corpus report",
        "",
        f"Filings: **{len(filings)}**  |  Artifacts: **{len(artifacts)}**  |  "
        f"Bytes: **{sum(a['size_bytes'] for a in artifacts) / 1_048_576:.1f} MiB**",
        "",
        _section_forms_by_year(filings),
        _section_artifact_kinds(artifacts),
        _section_exhibit_roles(artifacts),
        _section_item_202(filings, by_filing),
        _section_flags(filings, by_filing, artifact_manifest),
        _section_largest(artifacts),
    ]
    return "\n".join(sections)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _section_forms_by_year(filings: list[dict[str, Any]]) -> str:
    counts: dict[tuple[str, str], int] = Counter(
        (f["form"], f["filing_date"][:4]) for f in filings
    )
    years = sorted({year for _, year in counts})
    forms = sorted({form for form, _ in counts})
    rows = []
    for form in forms:
        row = [form]
        total = 0
        for year in years:
            value = counts.get((form, year), 0)
            total += value
            row.append(str(value) if value else "")
        row.append(f"**{total}**")
        rows.append(row)
    totals = ["**Total**"]
    for year in years:
        totals.append(f"**{sum(counts.get((f, year), 0) for f in forms)}**")
    totals.append(f"**{len(filings)}**")
    rows.append(totals)
    return "## Filings by form and year\n\n" + _table(["Form", *years, "Total"], rows) + "\n"


def _section_artifact_kinds(artifacts: list[dict[str, Any]]) -> str:
    counts = Counter(a["artifact_kind"] for a in artifacts)
    sizes: dict[str, int] = defaultdict(int)
    for artifact in artifacts:
        sizes[artifact["artifact_kind"]] += artifact["size_bytes"]
    rows = [
        [kind, str(counts[kind]), f"{sizes[kind] / 1_048_576:.1f}"]
        for kind in sorted(counts)
    ]
    rows.append(
        [
            "**Total**",
            f"**{len(artifacts)}**",
            f"**{sum(sizes.values()) / 1_048_576:.1f}**",
        ]
    )
    return "## Artifact kinds\n\n" + _table(["Kind", "Count", "MiB"], rows) + "\n"


def _section_exhibit_roles(artifacts: list[dict[str, Any]]) -> str:
    counts = Counter(
        a["role"] for a in artifacts if a["artifact_kind"] == "exhibit" and a["role"]
    )
    if not counts:
        return "## Exhibit roles\n\nNo exhibits.\n"
    rows = [
        [role, str(count)] for role, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return "## Exhibit roles\n\n" + _table(["Role", "Count"], rows[:30]) + "\n"


def _section_item_202(
    filings: list[dict[str, Any]], by_filing: dict[str, list[dict[str, Any]]]
) -> str:
    rows = []
    for filing in sorted(filings, key=lambda f: f["filing_date"], reverse=True):
        if ITEM_RESULTS_OF_OPERATIONS not in (filing.get("items") or []):
            continue
        roles = sorted(
            {
                a["role"]
                for a in by_filing.get(filing["filing_id"], [])
                if a["artifact_kind"] == "exhibit" and a["role"]
            }
        )
        rows.append(
            [
                filing["filing_date"],
                filing["accession"],
                ", ".join(filing["items"]),
                ", ".join(roles) if roles else "**none**",
            ]
        )
    if not rows:
        return "## Item 2.02 filings\n\nNone.\n"
    return (
        f"## Item 2.02 filings and their resolved exhibit roles ({len(rows)})\n\n"
        "Item codes are authoritative filing-level metadata and useful weak labels. They "
        "are not passage-level event labels.\n\n"
        + _table(["Filed", "Accession", "Items", "Exhibit roles"], rows)
        + "\n"
    )


def _section_flags(
    filings: list[dict[str, Any]],
    by_filing: dict[str, list[dict[str, Any]]],
    artifact_manifest: ArtifactManifest | None,
) -> str:
    lines = ["## Flagged for review", ""]
    flagged = False

    no_exhibits = [
        f
        for f in filings
        if not any(a["artifact_kind"] == "exhibit" for a in by_filing.get(f["filing_id"], []))
    ]
    if no_exhibits:
        flagged = True
        lines.append(f"### Filings with no exhibits ({len(no_exhibits)})")
        lines.append("")
        for filing in sorted(no_exhibits, key=lambda f: f["filing_date"])[:20]:
            lines.append(
                f"- {filing['filing_date']} {filing['form']} {filing['accession']}"
                f"  items={','.join(filing.get('items') or []) or '-'}"
            )
        lines.append("")

    item_202_without_exhibit = [
        f
        for f in filings
        if ITEM_RESULTS_OF_OPERATIONS in (f.get("items") or [])
        and not any(
            a["artifact_kind"] == "exhibit" and (a["role"] or "").startswith("ex99")
            for a in by_filing.get(f["filing_id"], [])
        )
    ]
    if item_202_without_exhibit:
        flagged = True
        lines.append(f"### Item 2.02 filings with no EX-99.x ({len(item_202_without_exhibit)})")
        lines.append("")
        for filing in item_202_without_exhibit:
            lines.append(f"- {filing['filing_date']} {filing['accession']}")
        lines.append("")

    amendments = [f for f in filings if f.get("is_amendment")]
    if amendments:
        flagged = True
        lines.append(f"### Amendments ({len(amendments)})")
        lines.append("")
        lines.append(
            "Preserved as filed. `amends_accession` is null: the submissions API does not "
            "link an amendment to the filing it amends, and inferring it is a heuristic "
            "rather than a reliable determination."
        )
        lines.append("")
        for filing in sorted(amendments, key=lambda f: f["filing_date"]):
            lines.append(f"- {filing['filing_date']} {filing['form']} {filing['accession']}")
        lines.append("")

    if artifact_manifest and artifact_manifest.anomalies:
        flagged = True
        counts = Counter(a.kind for a in artifact_manifest.anomalies)
        lines.append(f"### Resolution anomalies ({len(artifact_manifest.anomalies)})")
        lines.append("")
        for kind, count in sorted(counts.items()):
            lines.append(f"- `{kind}`: {count}")
        lines.append("")
        for anomaly in artifact_manifest.anomalies[:20]:
            lines.append(f"  - {anomaly.accession} `{anomaly.kind}` {anomaly.detail}")
        lines.append("")

    if not flagged:
        lines.append("Nothing flagged.")
        lines.append("")
    return "\n".join(lines)


def _section_largest(artifacts: list[dict[str, Any]]) -> str:
    largest = sorted(artifacts, key=lambda a: -a["size_bytes"])[:15]
    rows = [
        [
            f"{a['size_bytes'] / 1_048_576:.1f}",
            a["artifact_kind"],
            a["exhibit_type"] or "-",
            a["original_filename"][:52],
            a["filing_date"],
        ]
        for a in largest
    ]
    return (
        "## Largest artifacts\n\n"
        + _table(["MiB", "Kind", "Type", "Filename", "Filed"], rows)
        + "\n"
    )


# --------------------------------------------------------------------------------------
# Public stage
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportRequest:
    artifacts_run_id: str | None = None


@dataclass(frozen=True)
class ReportResult:
    path: Path
    text: str

    @property
    def ok(self) -> bool:
        return True


class MarkdownReportStage:
    """Renders the corpus report from the catalogs."""

    name = "report"

    def __init__(self, config: AppConfig, manifests: ManifestRepository) -> None:
        self._config = config
        self._manifests = manifests

    def run(self, request: ReportRequest) -> ReportResult:
        artifact_manifest = None
        try:
            run_id = request.artifacts_run_id or self._manifests.latest_artifact_run_id()
            artifact_manifest = self._manifests.read_artifact_manifest(run_id)
        except FileNotFoundError:
            run_id = "no-manifest"

        text = build_corpus_report(self._config.catalog_root, artifact_manifest)
        self._config.reports_root.mkdir(parents=True, exist_ok=True)
        path = self._config.reports_root / f"{run_id}-corpus.md"
        path.write_text(text + "\n", encoding="utf-8")
        return ReportResult(path=path, text=text)
