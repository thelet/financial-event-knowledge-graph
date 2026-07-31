"""Deterministic rebuild of the derived catalogs.

Authoritative inputs are the per-document JSON, the per-document passages JSONL, the
immutable selection manifests, and the per-run issue files. Derived catalogs are rebuilt
from those and never appended to concurrently.

No catalog row carries a run id or timestamp, so a rebuild on unchanged inputs is
byte-identical.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.config import AppConfig
from ...core.manifests import ManifestRepository
from ...core.storage import LocalNormalizedStore
from ...utils.jsonl import read_jsonl, write_jsonl
from .public import CatalogRequest, CatalogResult

DOCUMENTS_CATALOG = "documents.jsonl"
PASSAGES_CATALOG = "passages.jsonl"
SELECTION_CATALOG = "selection.jsonl"
ISSUES_CATALOG = "issues.jsonl"


class JsonlCatalogStage:
    name = "build-catalog"

    def __init__(
        self, config: AppConfig, store: LocalNormalizedStore, manifests: ManifestRepository
    ) -> None:
        self._config = config
        self._store = store
        self._manifests = manifests

    def run(self, request: CatalogRequest) -> CatalogResult:
        root = self._config.catalog_root
        doc_rows: list[dict[str, Any]] = []
        passage_rows: list[dict[str, Any]] = []
        unreadable: list[str] = []

        for path in self._store.iter_documents():
            document = self._store.read_document(path)
            if document is None:
                unreadable.append(str(path))
                continue
            doc_rows.append(_document_row(document, path, self._config.normalized_root))
            passage_rows.extend(self._store.read_passages(path))

        doc_rows.sort(key=lambda r: (r["cik10"], r["filing_date"], r["accession"], r["document_id"]))
        passage_rows.sort(key=lambda r: (r["document_id"], r["passage_sequence"]))

        doc_rows, dup_docs = _dedupe(doc_rows, "document_id")
        passage_rows, dup_passages = _dedupe(passage_rows, "passage_id")

        selection_rows = self._selection_rows()
        issue_rows = self._issue_rows()

        return CatalogResult(
            documents_path=write_jsonl(root / DOCUMENTS_CATALOG, doc_rows),
            passages_path=write_jsonl(root / PASSAGES_CATALOG, passage_rows),
            selection_path=write_jsonl(root / SELECTION_CATALOG, selection_rows),
            issues_path=write_jsonl(root / ISSUES_CATALOG, issue_rows),
            document_count=len(doc_rows),
            passage_count=len(passage_rows),
            selection_count=len(selection_rows),
            issue_count=len(issue_rows),
            duplicate_document_ids=dup_docs,
            duplicate_passage_ids=dup_passages,
            unreadable=unreadable,
        )

    def _selection_rows(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for run_id in self._manifests.list_selection_run_ids():
            manifest = self._manifests.read_selection_manifest(run_id)
            for artifact in manifest.artifacts:
                rows[artifact.artifact_id] = {
                    "artifact_id": artifact.artifact_id,
                    "filing_id": artifact.filing_id,
                    "cik10": artifact.cik10,
                    "accession": artifact.accession,
                    "form": artifact.form,
                    "filing_date": artifact.filing_date,
                    "artifact_kind": artifact.artifact_kind,
                    "role": artifact.role,
                    "exhibit_type": artifact.exhibit_type,
                    "size_bytes": artifact.size_bytes,
                    "decision": artifact.decision,
                    "reason_code": artifact.reason_code,
                    "matched_rule": artifact.matched_rule,
                    "policy_version": artifact.policy_version,
                }
        return [rows[k] for k in sorted(rows)]

    def _issue_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        runs_root = self._config.runs_root
        if runs_root.is_dir():
            for path in sorted(runs_root.glob("*-issues.jsonl")):
                rows.extend(read_jsonl(path))
        rows.sort(key=lambda r: (r.get("artifact_id", ""), r.get("code", ""), r.get("issue_id", "")))
        deduped, _ = _dedupe(rows, "issue_id")
        return deduped


def _document_row(document, path: Path, root: Path) -> dict[str, Any]:
    return {
        "document_id": document.document_id,
        "source_artifact_id": document.source_artifact_id,
        "filing_id": document.filing_id,
        "cik10": document.cik10,
        "company_name": document.company_name,
        "accession": document.source_artifact_id.split(":")[2],
        "form": document.form,
        "filing_date": document.filing_date,
        "report_date": document.report_date,
        "items": document.items,
        "artifact_role": document.artifact_role,
        "exhibit_type": document.exhibit_type,
        "document_type": document.document_type,
        "title": document.title,
        "original_filename": document.original_filename,
        "source_url": document.source_url,
        "section_count": document.stats.section_count,
        "block_count": document.stats.block_count,
        "passage_count": document.stats.passage_count,
        "table_count": document.stats.table_count,
        "data_table_count": document.stats.data_table_count,
        "layout_table_count": document.stats.layout_table_count,
        "char_count": document.stats.char_count,
        "text_yield_pct": document.stats.text_yield_pct,
        "max_section_depth": document.stats.max_section_depth,
        "flags": document.flags,
        "parser_name": document.parser_name,
        "parser_version": document.parser_version,
        "normalizer_version": document.normalizer_version,
        "derivation_id": document.derivation_id,
        "content_sha256": document.content_sha256,
        "source_content_sha256": document.source_content_sha256,
        "document_path": str(path.relative_to(root).as_posix()),
    }


def _dedupe(rows: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], list[str]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    duplicates: list[str] = []
    for row in rows:
        identifier = row.get(key)
        if identifier in seen:
            duplicates.append(str(identifier))
            continue
        seen.add(identifier)
        unique.append(row)
    return unique, duplicates
