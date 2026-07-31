"""Deterministic identities for normalized objects.

Pure functions. IDs derive from stable source identity and structural position, never from
randomness, and are readable because they surface in evidence panels.

    document_id  norm:{cik10}:{accession}:{original_filename}
    section_id   {document_id}#s{sequence}
    block_id     {document_id}#b{sequence}
    passage_id   {document_id}#p{sequence}

`document_id` has no content component, so it is stable when source bytes change. That is
deliberate: it is the same logical document, revised. The change surfaces through
source_content_sha256.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SOURCE = "norm"
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def cik10(cik: int | str) -> str:
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"CIK contains no digits: {cik!r}")
    value = digits.lstrip("0") or "0"
    if len(value) > 10:
        raise ValueError(f"CIK too long: {cik!r}")
    return value.zfill(10)


def document_id(cik: int | str, accession: str, original_filename: str) -> str:
    accession = str(accession).strip()
    if not _ACCESSION_RE.match(accession):
        raise ValueError(f"Not a dashed SEC accession: {accession!r}")
    name = str(original_filename).strip()
    if not name:
        raise ValueError("original_filename must not be empty")
    return f"{SOURCE}:{cik10(cik)}:{accession}:{name}"


def document_id_from_artifact_id(artifact_id: str) -> str:
    """Derive from an acquisition artifact_id: sec:{cik10}:{accession}:{filename}."""
    parts = str(artifact_id).split(":", 3)
    if len(parts) != 4 or parts[0] != "sec":
        raise ValueError(f"Not an acquisition artifact_id: {artifact_id!r}")
    return f"{SOURCE}:{parts[1]}:{parts[2]}:{parts[3]}"


def section_id(doc_id: str, sequence: int) -> str:
    return f"{doc_id}#s{_seq(sequence)}"


def block_id(doc_id: str, sequence: int) -> str:
    return f"{doc_id}#b{_seq(sequence)}"


def passage_id(doc_id: str, sequence: int) -> str:
    return f"{doc_id}#p{_seq(sequence)}"


def _seq(sequence: int) -> int:
    if not isinstance(sequence, int) or sequence < 0:
        raise ValueError(f"sequence must be a non-negative int: {sequence!r}")
    return sequence


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_hash(payload: Any) -> str:
    """SHA-256 over a canonical JSON rendering; key order and whitespace normalized."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def derivation_id(
    *,
    selection_policy_version: str,
    parser_name: str,
    parser_version: str,
    normalizer_version: str,
    passage_strategy_name: str,
    passage_strategy_version: str,
    config_hash: str,
    source_content_sha256: str,
) -> str:
    """One value answering "was this produced the same way?".

    Deterministic: identical inputs always give the same id, and no timestamp or run id
    contributes.
    """
    return canonical_hash(
        {
            "selection_policy_version": selection_policy_version,
            "parser_name": parser_name,
            "parser_version": parser_version,
            "normalizer_version": normalizer_version,
            "passage_strategy_name": passage_strategy_name,
            "passage_strategy_version": passage_strategy_version,
            "config_hash": config_hash,
            "source_content_sha256": source_content_sha256,
        }
    )


def document_relpath(cik: int | str, form_sanitized: str, filing_date: str, accession: str) -> str:
    """Directory for a document, mirroring the acquisition raw layout."""
    return f"sec/{cik10(cik)}/{form_sanitized}/{filing_date}_{accession}"
