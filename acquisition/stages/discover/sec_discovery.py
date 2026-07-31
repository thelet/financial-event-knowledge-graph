"""SEC implementation of the DISCOVER stage.

Reads data.sec.gov/submissions/, follows continuation files, filters by form and date, and
converts the API's parallel-array encoding into canonical filing records. Fetches no filing
artifacts.

Everything provider-specific is confined here, behind `public.DiscoverStage`.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ...core import identity
from ...core.config import AppConfig, CompanyConfig
from ...core.manifests import ManifestRepository
from ...core.models import FilingManifest, FilingRecord, RunRecord
from ...core.runmeta import build_run_metadata, utc_now_iso, write_run_record
from ...core.sec_client import SecClient
from .public import DiscoverRequest, DiscoverResult


SUBMISSIONS_BASE = identity.SEC_SUBMISSIONS

class FilingDiscoverer:
    """Discovers filings for one issuer from data.sec.gov/submissions/."""

    def __init__(self, client: SecClient) -> None:
        self._client = client

    def discover(
        self,
        company: CompanyConfig,
        *,
        forms: Iterable[str],
        date_from: str,
        date_to: str,
        include_amendments: bool = True,
    ) -> list[FilingRecord]:
        payload = self._fetch_submissions(company.cik)
        wanted = _form_matcher(forms, include_amendments)

        company_name = payload.get("name") or company.name
        tickers = payload.get("tickers") or company.tickers

        rows: list[dict[str, Any]] = []
        rows.extend(_rows_from_block(payload.get("filings", {}).get("recent", {})))

        # Issuers with more than ~1000 filings have older ones in continuation files.
        for extra in payload.get("filings", {}).get("files", []) or []:
            name = extra.get("name")
            if not name:
                continue
            block = self._fetch_json(f"{SUBMISSIONS_BASE}/{name}")
            rows.extend(_rows_from_block(block))

        records: list[FilingRecord] = []
        for row in rows:
            form = (row.get("form") or "").strip()
            filing_date = (row.get("filingDate") or "").strip()
            accession = (row.get("accessionNumber") or "").strip()
            if not form or not filing_date or not accession:
                continue
            if not wanted(form):
                continue
            if not (date_from <= filing_date <= date_to):
                continue
            records.append(
                _to_record(row, company=company, company_name=company_name, tickers=tickers)
            )

        # Deterministic order: chronological, then accession for same-day filings.
        records.sort(key=lambda r: (r.filing_date, r.accession))
        return records

    def _fetch_submissions(self, cik: int | str) -> dict[str, Any]:
        return self._fetch_json(identity.submissions_url(cik))

    def _fetch_json(self, url: str) -> dict[str, Any]:
        result = self._client.get_bytes(url)
        return json.loads(result.content)

def _rows_from_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Transpose the parallel-array encoding used by the submissions API."""
    if not block:
        return []
    keys = [k for k, v in block.items() if isinstance(v, list)]
    if not keys:
        return []
    length = min(len(block[k]) for k in keys)
    return [{k: block[k][i] for k in keys} for i in range(length)]

def _form_matcher(forms: Iterable[str], include_amendments: bool):
    wanted = {f.strip().upper() for f in forms}

    def matches(form: str) -> bool:
        value = form.strip().upper()
        if value in wanted:
            return True
        if include_amendments and value.endswith("/A"):
            return value[:-2] in wanted
        return False

    return matches

def _to_record(
    row: dict[str, Any],
    *,
    company: CompanyConfig,
    company_name: str,
    tickers: list[str],
) -> FilingRecord:
    form = (row.get("form") or "").strip()
    accession = identity.normalize_accession(row.get("accessionNumber"))
    filing_date = (row.get("filingDate") or "").strip()

    items_raw = (row.get("items") or "").strip()
    items = [item.strip() for item in items_raw.split(",") if item.strip()]

    size = row.get("size")
    return FilingRecord(
        filing_id=identity.filing_id(company.cik, accession),
        source=identity.SOURCE_SEC,
        cik10=company.cik10,
        company_name=company_name,
        tickers=list(tickers),
        accession=accession,
        accession_nodash=identity.accession_nodash(accession),
        form=form,
        form_sanitized=identity.sanitize_form(form),
        is_amendment=identity.is_amendment(form),
        # Deliberately null: the submissions API does not link an amendment to the filing
        # it amends, and inferring it from period-of-report is a heuristic, not a reliable
        # determination (v0 plan section 11).
        amends_accession=None,
        filing_date=filing_date,
        report_date=(row.get("reportDate") or "").strip() or None,
        acceptance_datetime=(row.get("acceptanceDateTime") or "").strip() or None,
        items=items,
        file_number=(row.get("fileNumber") or "").strip() or None,
        act=(row.get("act") or "").strip() or None,
        film_number=(row.get("filmNumber") or "").strip() or None,
        primary_document=(row.get("primaryDocument") or "").strip() or None,
        primary_doc_description=(row.get("primaryDocDescription") or "").strip() or None,
        is_xbrl=bool(row.get("isXBRL")),
        is_inline_xbrl=bool(row.get("isInlineXBRL")),
        size_reported=int(size) if isinstance(size, (int, str)) and str(size).isdigit() else None,
        filing_dir=str(
            identity.filing_relpath(company.cik, form, filing_date, accession)
        ),
        source_index_url=identity.index_json_url(company.cik, accession),
        source_index_headers_url=identity.artifact_url(
            company.cik, accession, identity.index_headers_filename(accession)
        ),
    )

class SecDiscoverStage:
    """Discovers filings from the SEC submissions API and writes a filing manifest."""

    name = "discover"

    def __init__(
        self, config: AppConfig, client: SecClient, manifests: ManifestRepository
    ) -> None:
        self._config = config
        self._client = client
        self._manifests = manifests

    def run(self, request: DiscoverRequest) -> DiscoverResult:
        config = self._config
        company = config.company(request.cik)
        config_hash = config.config_hash()
        run = build_run_metadata("discover", config_hash, root=config.root)
        date_to = config.fetch.effective_date_to()

        before = self._client.request_count
        filings = FilingDiscoverer(self._client).discover(
            company,
            forms=config.fetch.forms,
            date_from=config.fetch.date_from,
            date_to=date_to,
            include_amendments=request.include_amendments,
        )
        requests_made = self._client.request_count - before

        manifest = FilingManifest(
            run=run,
            company_cik10=company.cik10,
            forms=list(config.fetch.forms),
            date_from=config.fetch.date_from,
            date_to=date_to,
            filings=filings,
        )
        path = self._manifests.write_filing_manifest(manifest)

        write_run_record(
            config.runs_root,
            RunRecord(
                run=run,
                started_at=run.created_at,
                finished_at=utc_now_iso(),
                config_snapshot={
                    "forms": config.fetch.forms,
                    "date_from": config.fetch.date_from,
                },
                counts={"filings": len(filings)},
                requests_made=requests_made,
            ),
        )
        return DiscoverResult(
            run_id=run.run_id,
            manifest_path=path,
            filings=filings,
            requests_made=requests_made,
            company_name=company.name,
            company_cik10=company.cik10,
            date_from=config.fetch.date_from,
            date_to=date_to,
            config_hash=config_hash,
            manifest_hash=manifest.manifest_hash or "",
        )
