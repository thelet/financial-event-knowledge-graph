"""Object factories for normalization tests."""

from __future__ import annotations

from normalization.core.models import SelectedArtifact


def make_artifact(**overrides) -> SelectedArtifact:
    base = dict(
        artifact_id="sec:0001801169:0001801169-26-000009:test.htm",
        filing_id="sec:0001801169:0001801169-26-000009",
        cik=1801169, cik10="0001801169", company_name="Opendoor Technologies Inc.",
        tickers=["OPEN"], accession="0001801169-26-000009", form="8-K",
        form_sanitized="8-K", filing_date="2026-02-19", report_date="2026-02-19",
        items=["2.02"], artifact_kind="exhibit", role="ex99-01", exhibit_type="EX-99.1",
        description="EX-99.1", original_filename="test.htm",
        source_path="sec/0001801169/8-K/2026-02-19_0001801169-26-000009/source/test.htm",
        source_url="https://www.sec.gov/Archives/edgar/data/1801169/x/test.htm",
        media_type="text/html", file_extension=".htm", size_bytes=1000,
        source_sha256="0" * 64, decision="include", reason_code="EARNINGS_MATERIAL",
        policy_version="v1",
    )
    base.update(overrides)
    return SelectedArtifact(**base)
