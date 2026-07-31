"""Policy-driven selection over the acquisition catalog.

Reads `data/catalog/artifacts.jsonl` and `filings.jsonl` as data. Nothing here imports the
acquisition package — the on-disk catalog is the interface between the two, which is what
lets either side be replaced.

Every acquired artifact receives exactly one decision and one reason code. Rules are
evaluated in order and the first match wins, so policy is readable top to bottom.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ...core.config import AppConfig, SelectionRule
from ...core.manifests import ManifestRepository
from ...core.models import SelectedArtifact, SelectionManifest
from ...core.runmeta import utc_now_iso
from ...utils.jsonl import read_jsonl
from .public import BELOW_TEXT_FLOOR, SelectRequest, SelectResult


class CatalogSelectStage:
    """Applies the versioned selection policy to every acquired artifact."""

    name = "select"

    def __init__(self, config: AppConfig, manifests: ManifestRepository) -> None:
        self._config = config
        self._manifests = manifests

    # -- stage -------------------------------------------------------------------------

    def run(self, request: SelectRequest) -> SelectResult:
        rows = self._load_artifacts()
        filings = self._load_filings()

        wanted = set(request.artifact_ids) if request.artifact_ids else None
        xbrl_by_accession = _xbrl_index(rows)

        artifacts: list[SelectedArtifact] = []
        for row in rows:
            if wanted is not None and row["artifact_id"] not in wanted:
                continue
            decision, reason, rule_id = self._decide(row)
            artifacts.append(
                self._to_selected(
                    row,
                    filings.get(row["filing_id"], {}),
                    decision,
                    reason,
                    rule_id,
                    xbrl_by_accession.get(row["accession"], []),
                )
            )

        artifacts.sort(key=lambda a: (a.cik10, a.filing_date, a.accession, a.original_filename))
        counts = _counts(artifacts)

        manifest_path: Path | None = None
        if request.write_manifest:
            manifest = SelectionManifest(
                run_id=request.run_id,
                created_at=utc_now_iso(),
                policy_version=self._config.policy.version,
                config_hash=self._config.config_hash(),
                counts=counts,
                artifacts=artifacts,
            )
            manifest_path = self._manifests.write_selection_manifest(manifest)

        return SelectResult(artifacts=artifacts, manifest_path=manifest_path, counts=counts)

    # -- policy ------------------------------------------------------------------------

    def _decide(self, row: dict[str, Any]) -> tuple[str, str, str | None]:
        for rule in self._config.policy.rules:
            if _matches(rule, row):
                decision, reason = rule.decision, rule.reason
                # The text floor is a policy-level guard applied after a positive match:
                # a document with no text and no tables cannot be normalized usefully.
                if decision == "include" and self._below_text_floor(row):
                    return "exclude", BELOW_TEXT_FLOOR, rule.id
                return decision, reason, rule.id
        return (
            self._config.policy.default_decision,
            self._config.policy.default_reason,
            None,
        )

    def _below_text_floor(self, row: dict[str, Any]) -> bool:
        """Size is the only pre-parse signal available; text is measured later.

        Deliberately conservative: a small file that contains a table (EX-21.1 is 10 KB
        holding 68 rows) must survive, so the floor only rejects tiny files.
        """
        return row.get("size_bytes", 0) < 1024

    # -- loading -----------------------------------------------------------------------

    def _load_artifacts(self) -> list[dict[str, Any]]:
        path = self._config.acquisition_catalog_root / "artifacts.jsonl"
        rows = read_jsonl(path)
        if not rows:
            raise FileNotFoundError(
                f"No acquisition artifacts at {path}; run the acquisition pipeline first"
            )
        return rows

    def _load_filings(self) -> dict[str, dict[str, Any]]:
        path = self._config.acquisition_catalog_root / "filings.jsonl"
        return {r["filing_id"]: r for r in read_jsonl(path)}

    def _to_selected(
        self,
        row: dict[str, Any],
        filing: dict[str, Any],
        decision: str,
        reason: str,
        rule_id: str | None,
        xbrl_ids: list[str],
    ) -> SelectedArtifact:
        return SelectedArtifact(
            artifact_id=row["artifact_id"],
            filing_id=row["filing_id"],
            cik=int(row["cik10"]),
            cik10=row["cik10"],
            company_name=filing.get("company_name", ""),
            tickers=filing.get("tickers", []),
            accession=row["accession"],
            form=row["form"],
            form_sanitized=filing.get("form_sanitized", row["form"].replace("/", "-")),
            filing_date=row["filing_date"],
            report_date=filing.get("report_date"),
            acceptance_datetime=filing.get("acceptance_datetime"),
            items=row.get("items", []),
            is_amendment=filing.get("is_amendment", False),
            amends_accession=filing.get("amends_accession"),
            artifact_kind=row["artifact_kind"],
            role=row.get("role"),
            exhibit_type=row.get("exhibit_type"),
            description=row.get("description"),
            original_filename=row["original_filename"],
            source_path=row["stored_path"],
            source_url=row["source_url"],
            media_type=row["media_type"],
            file_extension=row.get("file_extension", ""),
            size_bytes=row["size_bytes"],
            source_sha256=row["sha256"],
            decision=decision,  # type: ignore[arg-type]
            reason_code=reason,
            matched_rule=rule_id,
            policy_version=self._config.policy.version,
            related_xbrl_artifact_ids=xbrl_ids,
        )


# --------------------------------------------------------------------------------------
# Rule matching
# --------------------------------------------------------------------------------------


def _matches(rule: SelectionRule, row: dict[str, Any]) -> bool:
    for key, expected in rule.when.items():
        if not _match_one(key, expected, row):
            return False
    return True


def _match_one(key: str, expected: Any, row: dict[str, Any]) -> bool:
    role = (row.get("role") or "").lower()
    description = (row.get("description") or "").lower()

    if key == "artifact_kind":
        return row.get("artifact_kind") == expected
    if key == "artifact_kind_in":
        return row.get("artifact_kind") in set(expected)
    if key == "media_type":
        return row.get("media_type") == expected
    if key == "media_type_not":
        return row.get("media_type") != expected
    if key == "role_in":
        return role in {str(v).lower() for v in expected}
    if key == "role_prefix":
        return role.startswith(str(expected).lower())
    if key == "role_prefix_any":
        return any(role.startswith(str(v).lower()) for v in expected)
    if key == "description_matches":
        return any(str(v).lower() in description for v in expected)
    if key == "items_any":
        return bool(set(row.get("items", [])) & {str(v) for v in expected})
    if key == "form_in":
        return row.get("form") in set(expected)
    if key == "max_size_bytes":
        return row.get("size_bytes", 0) <= int(expected)
    raise ValueError(f"Unknown selection rule key: {key!r}")


def _xbrl_index(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Accession -> XBRL artifact ids, so a document can link its structured-data lane."""
    index: dict[str, list[str]] = {}
    for row in rows:
        if row.get("artifact_kind") == "xbrl":
            index.setdefault(row["accession"], []).append(row["artifact_id"])
    for ids in index.values():
        ids.sort()
    return index


def _counts(artifacts: list[SelectedArtifact]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(artifacts)}
    for artifact in artifacts:
        counts[f"decision.{artifact.decision}"] = counts.get(f"decision.{artifact.decision}", 0) + 1
        counts[f"reason.{artifact.reason_code}"] = counts.get(f"reason.{artifact.reason_code}", 0) + 1
    return counts


def render_summary(result: SelectResult) -> str:
    lines = [f"{'DECISION / REASON':<40}{'COUNT':>8}", f"{'-' * 40} {'-' * 7}"]
    for key in sorted(k for k in result.counts if k.startswith("decision.")):
        lines.append(f"{key.split('.', 1)[1]:<40}{result.counts[key]:>8}")
    lines.append("")
    for key in sorted(k for k in result.counts if k.startswith("reason.")):
        lines.append(f"  {key.split('.', 1)[1]:<38}{result.counts[key]:>8}")
    lines.append(f"{'-' * 40} {'-' * 7}")
    lines.append(f"{'TOTAL':<40}{result.counts.get('total', 0):>8}")
    return "\n".join(lines)
