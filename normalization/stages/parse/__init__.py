"""PARSE: turn raw artifact bytes into canonical ParsedDocument blocks.

Exports the parser protocol, both implementations, and the mode/trigger vocabulary.
Parser-native objects never leave this package.
"""

from .lxml_parser import LxmlDocumentParser
from .public import (
    FALLBACK_TRIGGERS,
    MODE_COMPARISON,
    MODE_FALLBACK,
    MODE_NORMAL,
    MODES,
    TRIGGER_COVERAGE,
    TRIGGER_EMPTY,
    TRIGGER_EXCEPTION,
    TRIGGER_INVALID,
    TRIGGER_NO_BLOCKS,
    TRIGGER_SOURCE_HASH,
    DocumentParser,
    ParserError,
)
from .sec_html_parser import SecHtmlDocumentParser

__all__ = [
    "DocumentParser", "ParserError", "LxmlDocumentParser", "SecHtmlDocumentParser",
    "MODES", "MODE_NORMAL", "MODE_FALLBACK", "MODE_COMPARISON", "FALLBACK_TRIGGERS",
    "TRIGGER_EXCEPTION", "TRIGGER_EMPTY", "TRIGGER_NO_BLOCKS", "TRIGGER_INVALID",
    "TRIGGER_SOURCE_HASH", "TRIGGER_COVERAGE",
]
