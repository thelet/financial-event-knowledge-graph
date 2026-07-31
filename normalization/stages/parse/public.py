"""Public contract for parsing.

`DocumentParser` is a *strategy* protocol consumed by the normalize stage, not a pipeline
stage: parsing has no independent run-to-disk semantics. `ParsedDocument` is ephemeral and
never persisted.

No parser-native type appears in this module or anywhere above it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...core.models import ParsedDocument, SelectedArtifact

# Parser modes (v1 plan section 4.5).
MODE_NORMAL = "normal"
MODE_FALLBACK = "fallback"
MODE_COMPARISON = "comparison"
MODES = (MODE_NORMAL, MODE_FALLBACK, MODE_COMPARISON)

# The only conditions that trigger fallback in normal mode. Weak hierarchy is deliberately
# absent: it produces a warning, never a silent parser switch.
TRIGGER_EXCEPTION = "parser_exception"
TRIGGER_EMPTY = "empty_result"
TRIGGER_NO_BLOCKS = "no_usable_blocks"
TRIGGER_INVALID = "invalid_canonical_output"
TRIGGER_SOURCE_HASH = "source_hash_mismatch"
TRIGGER_COVERAGE = "source_coverage_below_threshold"
# Substantial table structure exists, the parser recognized almost none of it, AND sampled
# table content is missing from its output. All three are required: SEC filings use tables
# for page layout constantly, so a low detection ratio alone means nothing as long as the
# text inside those tables still reaches the document.
TRIGGER_TABLE_CONTENT_LOSS = "TABLE_CONTENT_LOSS"
FALLBACK_TRIGGERS = (
    TRIGGER_EXCEPTION, TRIGGER_EMPTY, TRIGGER_NO_BLOCKS,
    TRIGGER_INVALID, TRIGGER_SOURCE_HASH, TRIGGER_COVERAGE,
    TRIGGER_TABLE_CONTENT_LOSS,
)


class ParserError(RuntimeError):
    """A parser could not produce usable canonical output."""

    def __init__(self, trigger: str, detail: str) -> None:
        super().__init__(f"{trigger}: {detail}")
        self.trigger = trigger
        self.detail = detail


@runtime_checkable
class DocumentParser(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def supports(self, artifact: SelectedArtifact) -> bool: ...

    def parse(self, artifact: SelectedArtifact, raw: bytes) -> ParsedDocument: ...
