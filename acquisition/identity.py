"""Canonical identities, naming, and artifact classification.

Every function here is pure and deterministic. Identity rules come from the v0 plan
section 2; naming rules from section 7; classification from section 3.

Nothing in this module performs I/O or interprets document content.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

SOURCE_SEC = "sec"

# Artifact kinds (v0 plan section 3).
KIND_PRIMARY = "primary"
KIND_EXHIBIT = "exhibit"
KIND_XBRL = "xbrl"
KIND_ASSET = "asset"
KIND_FULL_SUBMISSION = "full_submission"
KIND_INDEX_HEADER = "index_header"
KIND_RENDER_ARTIFACT = "render_artifact"
KIND_OTHER = "other"

# SGML TYPE values that denote binary/rendered assets rather than filed text.
_ASSET_TYPES = {"GRAPHIC", "ZIP", "PDF", "EXCEL", "JSON", "GIF", "JPEG"}

# Exhibit prefixes whose content is XBRL rather than narrative.
_XBRL_TYPE_PREFIXES = ("EX-100.", "EX-101.", "EX-99.SDR")

# Filenames matching these suffixes are XBRL regardless of declared TYPE, for filings that
# omit an EX-101.* type. Note "_htm.xml" is deliberately absent: the inline XBRL instance
# is IDEA-generated and is caught by the render-artifact rule first.
_XBRL_FILENAME_SUFFIXES = (
    "_cal.xml",
    "_def.xml",
    "_lab.xml",
    "_pre.xml",
    ".xsd",
)

# Sarbanes-Oxley certifications: boilerplate, downloaded and cataloged but not worth
# parsing. See v0 plan SUMMARY.
_LOW_PRIORITY_EXHIBIT_MAJORS = {"31", "32"}

# SEC's IDEA system generates rendering artifacts and appends them to the SGML DOCUMENT
# list alongside filer-submitted documents. They carry DESCRIPTION "IDEA: ..." (v0 plan
# section 13.7). They are derived from the filing, not part of it.
_IDEA_DESCRIPTION_PREFIX = "IDEA:"
_RENDER_ARTIFACT_NAMES = {"show.js", "report.css", "filingsummary.xml", "metalinks.json"}
_RENDER_ARTIFACT_RE = re.compile(r"^(r\d+\.htm|.*-xbrl\.zip|.*_htm\.xml)$")

_MEDIA_TYPES = {
    ".htm": "text/html",
    ".html": "text/html",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".xsd": "application/xml",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".css": "text/css",
    ".js": "text/javascript",
}

_ACCESSION_RE = re.compile(r"^(\d{10})-?(\d{2})-?(\d{6})$")


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------


def cik10(cik: int | str) -> str:
    """Zero-padded 10-digit CIK. The canonical issuer identity."""
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"CIK contains no digits: {cik!r}")
    value = digits.lstrip("0") or "0"
    if len(value) > 10:
        raise ValueError(f"CIK too long: {cik!r}")
    return value.zfill(10)


def normalize_accession(accession: str) -> str:
    """Return the dashed accession form, accepting dashed or undashed input.

    The accession prefix identifies the *filing agent*, not the issuer -- Opendoor's 2020
    filings carry prefix 0001104659 (v0 plan section 13.5). Never derive a CIK from this.
    """
    raw = str(accession).strip()
    match = _ACCESSION_RE.match(raw)
    if not match:
        raise ValueError(f"Not a valid SEC accession number: {accession!r}")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def accession_nodash(accession: str) -> str:
    return normalize_accession(accession).replace("-", "")


def filing_id(cik: int | str, accession: str) -> str:
    """Canonical filing identity: source + CIK + accession."""
    return f"{SOURCE_SEC}:{cik10(cik)}:{normalize_accession(accession)}"


def artifact_id(cik: int | str, accession: str, original_filename: str) -> str:
    """Canonical artifact identity.

    Keyed on the original filename rather than SGML SEQUENCE, because the full-submission
    text file and the index-header file are not part of the SGML DOCUMENT list and so have
    no sequence number. Filenames are unique within a filing directory.
    """
    name = str(original_filename).strip()
    if not name:
        raise ValueError("original_filename must not be empty")
    return f"{filing_id(cik, accession)}:{name}"


# --------------------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------------------


def sanitize_form(form: str) -> str:
    """Filesystem-safe form name.

    Mandatory: an unsanitized "8-K/A" silently creates a nested directory.
    "DEF 14A" -> "DEF-14A", "8-K/A" -> "8-K-A".
    """
    value = str(form).strip().upper()
    if not value:
        raise ValueError("form must not be empty")
    value = re.sub(r"[\s/\\]+", "-", value)
    value = re.sub(r"[^A-Z0-9._-]", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-.")
    if not value:
        raise ValueError(f"form sanitized to empty string: {form!r}")
    return value


def is_amendment(form: str) -> bool:
    return str(form).strip().upper().endswith("/A")


def filing_dir_name(filing_date: str, accession: str) -> str:
    """Directory name: date first for chronological sort, accession for uniqueness."""
    date = str(filing_date).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise ValueError(f"filing_date must be YYYY-MM-DD: {filing_date!r}")
    return f"{date}_{normalize_accession(accession)}"


def filing_relpath(cik: int | str, form: str, filing_date: str, accession: str) -> PurePosixPath:
    """Path of a filing directory relative to the raw root."""
    return PurePosixPath(
        SOURCE_SEC,
        cik10(cik),
        sanitize_form(form),
        filing_dir_name(filing_date, accession),
    )


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


def file_extension(filename: str) -> str:
    suffix = PurePosixPath(str(filename)).suffix.lower()
    return suffix


def media_type_for(filename: str) -> str:
    return _MEDIA_TYPES.get(file_extension(filename), "application/octet-stream")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def normalize_exhibit_role(sgml_type: str) -> str | None:
    """Normalize an SGML TYPE into a stable role slug.

    EX-99.1    -> ex99-01
    EX-10.38   -> ex10-38
    EX-4.7     -> ex4-07
    EX-101.SCH -> ex101-sch

    The minor number is zero-padded to two digits so lexical and numeric order agree.
    Returns None for types that are not exhibits.
    """
    value = str(sgml_type).strip().upper()
    if not value.startswith("EX-"):
        return None
    body = value[3:].strip()
    if not body:
        return None
    major, _, minor = body.partition(".")
    major_slug = _slug(major)
    if not major_slug:
        return None
    if not minor:
        return f"ex{major_slug}"
    minor = minor.strip()
    minor_slug = minor.zfill(2) if minor.isdigit() else _slug(minor)
    if not minor_slug:
        return f"ex{major_slug}"
    return f"ex{major_slug}-{minor_slug}"


def is_render_artifact(filename: str, description: str | None) -> bool:
    """True for SEC-generated rendering output rather than filer-submitted content.

    DESCRIPTION is the primary signal here. That does not contradict the rule that
    classification keys on TYPE (v0 plan 13.4): TYPE genuinely cannot distinguish these --
    the inline XBRL instance and FilingSummary.xml are both filed as TYPE "XML" -- while
    the "IDEA:" marker is generated by one system and is consistent. The filename patterns
    are a fallback for filings that omit DESCRIPTION.
    """
    if description and description.strip().upper().startswith(_IDEA_DESCRIPTION_PREFIX):
        return True
    lowered = str(filename).strip().lower()
    return lowered in _RENDER_ARTIFACT_NAMES or bool(_RENDER_ARTIFACT_RE.match(lowered))


def _is_xbrl(sgml_type: str, filename: str) -> bool:
    upper = str(sgml_type).strip().upper()
    if upper.startswith(_XBRL_TYPE_PREFIXES):
        return True
    lower = str(filename).strip().lower()
    return lower.endswith(_XBRL_FILENAME_SUFFIXES)


def is_low_priority_exhibit(role: str | None) -> bool:
    """Certifications (EX-31.x, EX-32.x) are boilerplate."""
    if not role:
        return False
    match = re.match(r"^ex(\d+)(?:-|$)", role)
    return bool(match and match.group(1) in _LOW_PRIORITY_EXHIBIT_MAJORS)


def classify_artifact(
    sgml_type: str,
    filename: str,
    sequence: int | None,
    form: str,
    description: str | None = None,
) -> tuple[str, str | None, bool]:
    """Classify one SGML DOCUMENT entry.

    Returns (artifact_kind, role, low_processing_priority).

    Classification uses TYPE only. DESCRIPTION is inconsistent across years -- the same
    exhibit appears as "EX-99.1" in 2026 and "EXHIBIT 99.1" in 2020 (v0 plan section 13.4)
    -- so it is stored verbatim and never used to decide anything.
    """
    type_value = str(sgml_type).strip().upper()

    # Checked first: an IDEA-generated file may otherwise look like XBRL or an asset.
    if is_render_artifact(filename, description):
        return KIND_RENDER_ARTIFACT, None, True

    if _is_xbrl(type_value, filename):
        return KIND_XBRL, normalize_exhibit_role(type_value), True

    if type_value in _ASSET_TYPES:
        return KIND_ASSET, None, True

    if type_value.startswith("EX-"):
        role = normalize_exhibit_role(type_value)
        return KIND_EXHIBIT, role, is_low_priority_exhibit(role)

    # Primary document: TYPE matches the filing's form. Fall back to sequence 1 for the
    # rare filing whose primary TYPE is spelled differently from the form.
    if type_value and type_value == str(form).strip().upper():
        return KIND_PRIMARY, KIND_PRIMARY, False
    if sequence == 1:
        return KIND_PRIMARY, KIND_PRIMARY, False

    return KIND_OTHER, None, True


# --------------------------------------------------------------------------------------
# EDGAR URLs
# --------------------------------------------------------------------------------------

EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions"


def submissions_url(cik: int | str) -> str:
    return f"{SEC_SUBMISSIONS}/CIK{cik10(cik)}.json"


def filing_base_url(cik: int | str, accession: str) -> str:
    """Archive directory URL. Uses the *issuer* CIK, unpadded, per EDGAR's layout."""
    return f"{EDGAR_ARCHIVES}/{int(cik10(cik))}/{accession_nodash(accession)}"


def artifact_url(cik: int | str, accession: str, filename: str) -> str:
    return f"{filing_base_url(cik, accession)}/{filename}"


def index_json_url(cik: int | str, accession: str) -> str:
    return f"{filing_base_url(cik, accession)}/index.json"


def index_headers_filename(accession: str) -> str:
    return f"{normalize_accession(accession)}-index-headers.html"


def full_submission_filename(accession: str) -> str:
    return f"{normalize_accession(accession)}.txt"
