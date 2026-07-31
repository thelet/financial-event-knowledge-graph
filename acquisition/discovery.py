"""DISCOVER: build the filing-level manifest.

Reads the SEC submissions API and produces the list of filings that *should* exist. Fetches
no filing artifacts (v0 plan section 4).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from . import identity
from .config import CompanyConfig
from .models import FilingRecord
from .sec_client import SecClient

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


def summarize(records: list[FilingRecord]) -> str:
    """Human-readable summary printed before a manifest is written."""
    if not records:
        return "No filings matched the configured scope."

    by_form = Counter(r.form for r in records)
    lines = [
        f"{'FORM':<12} {'COUNT':>6}  {'EARLIEST':<12} {'LATEST':<12}",
        f"{'-' * 12} {'-' * 6}  {'-' * 12} {'-' * 12}",
    ]
    for form in sorted(by_form):
        dates = [r.filing_date for r in records if r.form == form]
        lines.append(f"{form:<12} {by_form[form]:>6}  {min(dates):<12} {max(dates):<12}")

    total_bytes = sum(r.size_reported or 0 for r in records)
    all_dates = [r.filing_date for r in records]
    lines.extend(
        [
            f"{'-' * 12} {'-' * 6}  {'-' * 12} {'-' * 12}",
            f"{'TOTAL':<12} {len(records):>6}  {min(all_dates):<12} {max(all_dates):<12}",
            "",
            f"Amendments:            {sum(1 for r in records if r.is_amendment)}",
            f"With 8-K item codes:   {sum(1 for r in records if r.items)}",
            f"Reported size (SEC):   {total_bytes / 1_048_576:.1f} MiB",
        ]
    )
    return "\n".join(lines)
