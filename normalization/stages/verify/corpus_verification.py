"""Corpus verification.

Implements the twenty checks in v1 plan §13. Failures are errors; quality signals that
need human judgement are warnings, so the exit code means "the corpus is broken", not "a
document looked unusual".
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ...core.config import AppConfig
from ...core.identity import (
    derivation_id as make_derivation_id,
    document_id_from_artifact_id,
    sha256_bytes,
    sha256_text,
)
from ...core.manifests import ManifestRepository
from ...core.storage import LocalNormalizedStore
from ...utils.jsonl import read_jsonl
from ...utils.text import has_run_together_tokens
from ..catalog.jsonl_catalog import DOCUMENTS_CATALOG, PASSAGES_CATALOG
from ..parse.tables import render_markdown, render_plain_text
from .public import SEVERITY_ERROR, SEVERITY_WARNING, VerificationReport, VerifyRequest, VerifyResult

# Fields that must never appear in an authoritative record: they change every run and
# would make byte-identical reruns impossible (v1 plan §12).
VOLATILE_FIELDS = ("run_id", "normalized_at", "code_commit", "created_at", "timestamp")


class CorpusVerifyStage:
    name = "verify"

    def __init__(
        self, config: AppConfig, store: LocalNormalizedStore, manifests: ManifestRepository
    ) -> None:
        self._config = config
        self._store = store
        self._manifests = manifests

    def run(self, request: VerifyRequest) -> VerifyResult:
        report = VerificationReport()

        run_id = request.selection_run_id or self._manifests.latest_selection_run_id()
        selection = self._manifests.read_selection_manifest(run_id)
        expected = {
            a.artifact_id: a for a in selection.artifacts if a.decision in ("include", "needs_review")
        }
        report.stats["selection.total"] = len(selection.artifacts)
        report.stats["selection.for_processing"] = len(expected)

        documents = {}
        for path in self._store.iter_documents():
            document = self._store.read_document(path)
            if document is None:
                report.add(SEVERITY_ERROR, "unreadable_document", str(path))
                continue
            documents[document.document_id] = (document, path)
        report.stats["documents.found"] = len(documents)

        self._check_coverage(expected, documents, report)
        self._check_documents(expected, documents, request.check_hashes, report)
        self._check_catalogs(documents, report)
        self._check_staging(report)
        return VerifyResult(report=report)

    # -- checks ------------------------------------------------------------------------

    def _check_coverage(self, expected, documents, report) -> None:
        """1. Every selected artifact has a document or a recorded failure."""
        failures = self._recorded_failures()
        by_artifact = {d.source_artifact_id for d, _ in documents.values()}
        for artifact_id in sorted(expected):
            if artifact_id in by_artifact or artifact_id in failures:
                continue
            report.add(
                SEVERITY_ERROR, "selected_artifact_not_normalized",
                f"{artifact_id} has neither a document nor a recorded failure",
            )
        report.stats["issues.recorded_failures"] = len(failures)

    def _recorded_failures(self) -> set[str]:
        out: set[str] = set()
        runs_root = self._config.runs_root
        if runs_root.is_dir():
            for path in runs_root.glob("*-issues.jsonl"):
                for row in read_jsonl(path):
                    if row.get("severity") == "error":
                        out.add(row.get("artifact_id", ""))
        return out

    def _check_documents(self, expected, documents, check_hashes, report) -> None:
        passages_total = 0
        tables_total = 0
        derivations: set[str] = set()

        for document_id, (document, path) in sorted(documents.items()):
            artifact = expected.get(document.source_artifact_id)

            # 2. links to an acquisition artifact
            if artifact is None:
                report.add(
                    SEVERITY_ERROR, "document_without_selected_artifact",
                    f"{document_id} -> {document.source_artifact_id}",
                )
            else:
                # 9. source hash matches acquisition
                if document.source_content_sha256 != artifact.source_sha256:
                    report.add(
                        SEVERITY_ERROR, "source_hash_mismatch",
                        f"{document_id}: {document.source_content_sha256[:12]} vs "
                        f"{artifact.source_sha256[:12]}",
                    )
                elif check_hashes:
                    raw_path = self._config.acquisition_raw_root / artifact.source_path
                    if raw_path.is_file() and sha256_bytes(raw_path.read_bytes()) != artifact.source_sha256:
                        report.add(SEVERITY_ERROR, "raw_bytes_changed", str(raw_path))

            # 4. IDs are deterministic
            if document.document_id != document_id_from_artifact_id(document.source_artifact_id):
                report.add(SEVERITY_ERROR, "nondeterministic_document_id", document_id)

            # 18. no volatile fields
            payload = json.loads(path.read_text(encoding="utf-8"))
            for field in VOLATILE_FIELDS:
                if field in payload:
                    report.add(
                        SEVERITY_ERROR, "volatile_field_in_document", f"{document_id}: {field}"
                    )

            # 6. section and block ordering is stable and complete
            sequences = [b.block_sequence for b in document.blocks]
            if sequences != sorted(sequences):
                report.add(SEVERITY_ERROR, "block_order_unstable", document_id)
            section_ids = {s.section_id for s in document.sections}
            for block in document.blocks:
                if block.section_id not in section_ids:
                    report.add(
                        SEVERITY_ERROR, "block_without_section", f"{document_id}: {block.block_id}"
                    )

            # 16. table renderings recompute from rows
            for block in document.blocks:
                if block.table is None:
                    continue
                tables_total += 1
                if render_markdown(block.table.rows, block.table.header_rows) != block.table.markdown:
                    report.add(SEVERITY_ERROR, "table_markdown_mismatch", f"{document_id}: {block.block_id}")
                if render_plain_text(block.table.rows) != block.table.plain_text:
                    report.add(SEVERITY_ERROR, "table_plain_text_mismatch", f"{document_id}: {block.block_id}")

            # derivation_id is reproducible from its inputs
            expected_derivation = make_derivation_id(
                selection_policy_version=document.selection_policy_version,
                parser_name=document.parser_name,
                parser_version=document.parser_version,
                normalizer_version=document.normalizer_version,
                passage_strategy_name="section_aware",
                passage_strategy_version="1.0.0",
                config_hash=document.config_hash,
                source_content_sha256=document.source_content_sha256,
            )
            if expected_derivation != document.derivation_id:
                report.add(SEVERITY_WARNING, "derivation_id_unexpected", document_id)
            derivations.add(document.derivation_id)

            passages_total += self._check_passages(document, path, report)

            # 7 / 11. quality flags surface rather than pass silently
            if "hierarchy_uncertain" in document.flags:
                report.add(SEVERITY_WARNING, "hierarchy_uncertain",
                           f"{document_id} ({document.form} {document.artifact_role})")
            if "requires_image_processing" in document.flags:
                report.add(SEVERITY_WARNING, "image_heavy", document_id)
            if "low_text_yield" in document.flags:
                report.add(SEVERITY_WARNING, "low_text_yield", document_id)
            if document.stats.char_count == 0:
                report.add(SEVERITY_ERROR, "empty_document", document_id)

        report.stats["passages.total"] = passages_total
        report.stats["tables.total"] = tables_total
        report.stats["derivation_ids.distinct"] = len(derivations)

    def _check_passages(self, document, path: Path, report) -> int:
        """3, 17. Passages link to blocks; eligible blocks are covered exactly once."""
        rows = self._store.read_passages(path)
        raw_path = self._config.acquisition_raw_root / document.source_path
        source_text = raw_path.read_text(encoding="utf-8", errors="replace") if raw_path.is_file() else ""
        block_ids = {b.block_id for b in document.blocks}
        seen: Counter[str] = Counter()

        for row in rows:
            for field in VOLATILE_FIELDS:
                if field in row:
                    report.add(SEVERITY_ERROR, "volatile_field_in_passage",
                               f"{row.get('passage_id')}: {field}")
            if not row.get("block_ids"):
                report.add(SEVERITY_ERROR, "passage_without_blocks", str(row.get("passage_id")))
            for block_id in row.get("block_ids", []):
                if block_id not in block_ids:
                    report.add(SEVERITY_ERROR, "passage_block_not_in_document",
                               f"{row.get('passage_id')} -> {block_id}")
                seen[block_id] += 1
            if not row.get("source_url"):
                report.add(SEVERITY_ERROR, "passage_without_source_url", str(row.get("passage_id")))
            if sha256_text(row.get("text", "")) != row.get("content_sha256"):
                report.add(SEVERITY_ERROR, "passage_hash_mismatch", str(row.get("passage_id")))
            runs = has_run_together_tokens(row.get("text", ""))
            if runs:
                # A token that appears verbatim in the source file was written that way by
                # the filer -- one 2021 proxy really does contain "INSTRUCTIONSPlease". A
                # token that appears only after extraction is our separator bug, which is
                # the `ASSETSFor` class this check exists to catch.
                introduced = [tok for tok in runs if tok not in source_text]
                native = [tok for tok in runs if tok in source_text]
                if introduced:
                    report.add(SEVERITY_ERROR, "run_together_tokens_introduced",
                               f"{row.get('passage_id')}: {introduced[:3]}")
                if native:
                    report.add(SEVERITY_WARNING, "run_together_tokens_in_source",
                               f"{row.get('passage_id')}: {native[:3]}")

        # A block appearing in several passages is legitimate only when it was too long
        # for one and the passages hold disjoint slices of it. Overlapping ranges would
        # mean duplicated evidence, which is the thing the no-overlap rule forbids.
        ranges: dict[str, list[tuple[int, int]]] = {}
        for row in rows:
            for locator in row.get("locators", []):
                ranges.setdefault(locator["block_id"] if "block_id" in locator else "", [])
        for row in rows:
            for block_id, locator in zip(row.get("block_ids", []), row.get("locators", [])):
                ranges.setdefault(block_id, []).append(
                    (locator.get("char_start", 0), locator.get("char_end", 0))
                )
        for block_id, spans in ranges.items():
            if not block_id or len(spans) < 2:
                continue
            ordered = sorted(spans)
            for (a_start, a_end), (b_start, _) in zip(ordered, ordered[1:]):
                if b_start < a_end:
                    report.add(SEVERITY_ERROR, "overlapping_passage_ranges",
                               f"{block_id}: {(a_start, a_end)} overlaps {b_start}")
                    break

        eligible = {
            b.block_id for b in document.blocks
            if b.block_type not in ("heading", "page_header", "page_number", "image")
            and (b.text.strip() or b.block_type == "table")
        }
        for block_id in sorted(eligible - set(seen)):
            report.add(SEVERITY_ERROR, "eligible_block_without_passage",
                       f"{document.document_id}: {block_id}")
        return len(rows)

    def _check_catalogs(self, documents, report) -> None:
        """12. Catalogs match the files on disk, both directions."""
        root = self._config.catalog_root
        docs_path, passages_path = root / DOCUMENTS_CATALOG, root / PASSAGES_CATALOG
        if not docs_path.is_file():
            report.add(SEVERITY_WARNING, "catalog_absent", "run build-catalog")
            return
        try:
            doc_rows = read_jsonl(docs_path)
            passage_rows = read_jsonl(passages_path)
        except ValueError as exc:
            report.add(SEVERITY_ERROR, "corrupt_catalog", str(exc))
            return

        report.stats["catalog.documents"] = len(doc_rows)
        report.stats["catalog.passages"] = len(passage_rows)

        catalog_ids = {r["document_id"] for r in doc_rows}
        disk_ids = set(documents)
        for missing in sorted(disk_ids - catalog_ids):
            report.add(SEVERITY_ERROR, "catalog_divergence", f"on disk, not in catalog: {missing}")
        for extra in sorted(catalog_ids - disk_ids):
            report.add(SEVERITY_ERROR, "catalog_divergence", f"in catalog, not on disk: {extra}")

        for rows, key, label in ((doc_rows, "document_id", "documents"), (passage_rows, "passage_id", "passages")):
            counts = Counter(r.get(key) for r in rows)
            for identifier, count in counts.items():
                if count > 1:
                    report.add(SEVERITY_ERROR, "duplicate_id_in_catalog", f"{label}: {identifier}")

    def _check_staging(self, report) -> None:
        """13. Interrupted runs leave no finalized partial document."""
        tmp = self._config.tmp_root
        if tmp.is_dir():
            leftovers = [p for p in tmp.iterdir() if p.is_dir()]
            if leftovers:
                report.stats["staging.leftover_dirs"] = len(leftovers)
                report.add(SEVERITY_WARNING, "incomplete_staging_directory",
                           f"{len(leftovers)} under {tmp}; safe to delete")
