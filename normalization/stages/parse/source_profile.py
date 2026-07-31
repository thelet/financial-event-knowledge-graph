"""Parser-independent measurement of a source document.

Answers "what is actually in these bytes?" without asking any parser, so a parser's output
can be judged against the source rather than against another parser. Used by the
table-content-loss fallback trigger.

Probe selection is deterministic — the first qualifying cell of each table in document
order — because a random sample would make the fallback decision, and therefore the corpus,
non-reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .html_text import element_text, parse_html_bytes, strip_non_content


@dataclass(frozen=True)
class SourceProfile:
    """What the raw bytes contain, measured independently of any parser."""

    table_count: int = 0
    text_chars: int = 0
    probes: list[str] = field(default_factory=list)

    def detected_ratio(self, parser_tables: int) -> float:
        """Fraction of source tables a parser recognized as tables."""
        if self.table_count == 0:
            return 1.0
        return parser_tables / self.table_count

    def probe_coverage(self, parsed_text: str) -> float:
        """Fraction of sampled source-table cells that survive in the parsed output.

        This is the content question. A parser may legitimately not model a table as a
        table -- SEC filings use tables for page layout constantly -- as long as the text
        inside it still reaches the document.
        """
        if not self.probes:
            return 1.0
        return sum(1 for p in self.probes if p in parsed_text) / len(self.probes)


def profile_source(
    raw: bytes, *, probe_count: int = 8, min_probe_length: int = 18
) -> SourceProfile:
    """Measure table structure and sample distinctive table content."""
    root = parse_html_bytes(raw)
    strip_non_content(root)

    tables = root.xpath("//table")
    probes: list[str] = []
    for table in tables:
        if len(probes) >= probe_count:
            break
        for cell in table.xpath(".//td|.//th"):
            text = element_text(cell)
            # Long enough to be distinctive: a probe of "$" or "2025" would match by
            # accident and make the coverage measure meaningless.
            if len(text) >= min_probe_length:
                probes.append(text)
                break

    return SourceProfile(
        table_count=len(tables),
        text_chars=len(element_text(root)),
        probes=probes,
    )
