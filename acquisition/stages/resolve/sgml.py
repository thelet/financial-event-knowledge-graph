"""Parser for EDGAR's <accession>-index-headers.html.

This file is the authoritative source of document types for a filing. index.json's per-item
`type` field returns an icon reference ("text.gif"), not an exhibit type, and .hdr.sgml
contains no DOCUMENT blocks at all (v0 plan sections 13.1-13.3).

The file is HTML-escaped SGML inside a <PRE> block. Each submitted document appears as:

    <DOCUMENT>
    <TYPE>EX-99.1
    <SEQUENCE>2
    <FILENAME>q42025formxex991earningsre.htm
    <DESCRIPTION>EX-99.1
    <TEXT>

Tags are unclosed, so parsing is line-oriented rather than tree-oriented.
"""

from __future__ import annotations

import html
import re

from pydantic import BaseModel

_PRE_RE = re.compile(r"<PRE>(.*?)</PRE>", re.IGNORECASE | re.DOTALL)
_TAG_LINE_RE = re.compile(r"^<([A-Z][A-Z0-9-]*)>(.*)$")
_ACCEPTANCE_RE = re.compile(r"^<ACCEPTANCE-DATETIME>(\d{14})", re.MULTILINE)
_ITEM_RE = re.compile(r"^ITEM INFORMATION:\s*(.+?)\s*$", re.MULTILINE)

_DOCUMENT_FIELDS = ("TYPE", "SEQUENCE", "FILENAME", "DESCRIPTION")


class HeaderDocument(BaseModel):
    """One <DOCUMENT> block from the SGML header."""

    type: str
    sequence: int | None = None
    filename: str
    description: str | None = None


class ParsedHeader(BaseModel):
    documents: list[HeaderDocument]
    acceptance_datetime: str | None = None
    item_descriptions: list[str] = []


class SgmlParseError(ValueError):
    """The header file could not be parsed into a usable document list."""


def _extract_sgml(raw: str) -> str:
    """Pull the SGML text out of its HTML wrapper.

    The <PRE> block is extracted from the raw bytes *before* unescaping, so that only the
    SGML tags survive as real angle brackets.
    """
    match = _PRE_RE.search(raw)
    body = match.group(1) if match else raw
    return html.unescape(body)


def parse_index_headers(raw: str) -> ParsedHeader:
    """Parse an index-headers.html document.

    Raises SgmlParseError if no DOCUMENT blocks are present -- a filing always has at
    least a primary document, so an empty list means the input was not what we think.
    """
    sgml = _extract_sgml(raw)

    chunks = sgml.split("<DOCUMENT>")
    if len(chunks) < 2:
        raise SgmlParseError("No <DOCUMENT> blocks found in index-headers document")

    documents: list[HeaderDocument] = []
    for chunk in chunks[1:]:
        fields = _parse_document_chunk(chunk)
        filename = fields.get("FILENAME", "").strip()
        type_value = fields.get("TYPE", "").strip()
        if not filename:
            # A DOCUMENT block without a FILENAME cannot be fetched; skip it rather than
            # inventing a name. RESOLVE reports the count mismatch against index.json.
            continue
        sequence_raw = fields.get("SEQUENCE", "").strip()
        documents.append(
            HeaderDocument(
                type=type_value,
                sequence=int(sequence_raw) if sequence_raw.isdigit() else None,
                filename=filename,
                description=(fields.get("DESCRIPTION") or "").strip() or None,
            )
        )

    if not documents:
        raise SgmlParseError("index-headers document contained no usable DOCUMENT blocks")

    acceptance = _ACCEPTANCE_RE.search(sgml)
    return ParsedHeader(
        documents=documents,
        acceptance_datetime=acceptance.group(1) if acceptance else None,
        item_descriptions=[m.strip() for m in _ITEM_RE.findall(sgml)],
    )


def _parse_document_chunk(chunk: str) -> dict[str, str]:
    """Read tag lines up to <TEXT>, which begins the document body."""
    fields: dict[str, str] = {}
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("<TEXT>"):
            break
        match = _TAG_LINE_RE.match(stripped)
        if not match:
            continue
        key = match.group(1).upper()
        if key in _DOCUMENT_FIELDS and key not in fields:
            fields[key] = match.group(2)
    return fields
