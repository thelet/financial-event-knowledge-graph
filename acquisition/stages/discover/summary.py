"""Human-readable rendering of discovery results.

Formatting only. Kept apart from the contract and from the SEC implementation so neither
has to care how results are displayed.
"""

from __future__ import annotations

from collections import Counter

from ...core.models import FilingRecord


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
