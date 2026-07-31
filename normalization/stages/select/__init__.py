"""SELECT: decide which acquired artifacts enter normalization."""

from .catalog_selection import CatalogSelectStage
from .public import (
    ARCHIVAL_ONLY,
    BELOW_TEXT_FLOOR,
    BOILERPLATE_CERTIFICATION,
    EARNINGS_MATERIAL,
    GOVERNANCE_DOCUMENT,
    MATERIAL_AGREEMENT,
    NEEDS_REVIEW,
    NON_NARRATIVE_MEDIA,
    PRIMARY_NARRATIVE,
    REASON_CODES,
    STRUCTURED_EXHIBIT,
    XBRL_LANE,
    SelectRequest,
    SelectResult,
    SelectStage,
)

__all__ = [
    "CatalogSelectStage", "SelectRequest", "SelectResult", "SelectStage", "REASON_CODES",
    "PRIMARY_NARRATIVE", "EARNINGS_MATERIAL", "MATERIAL_AGREEMENT", "GOVERNANCE_DOCUMENT",
    "STRUCTURED_EXHIBIT", "BOILERPLATE_CERTIFICATION", "NON_NARRATIVE_MEDIA", "XBRL_LANE",
    "ARCHIVAL_ONLY", "BELOW_TEXT_FLOOR", "NEEDS_REVIEW",
]
