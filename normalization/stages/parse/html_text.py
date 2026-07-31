"""HTML text extraction with correct separator handling.

Two corpus facts drive this module:

1. `lxml.text_content()` concatenates adjacent elements with no separator, producing
   run-together tokens such as `ASSETSFor` and `ActivitiesNet` — 238 of them in the FY2025
   10-K alone. Separators must be inserted at *block* boundaries only: inserting them
   between inline elements instead would break words that inline XBRL splits mid-sentence.

2. Inline XBRL brings up to 7,245 `display:none` elements holding 27,772 characters into a
   single 10-K. That content must be removed before any text is read, or it pollutes every
   passage.
"""

from __future__ import annotations

import re

import lxml.html
from lxml.html import HtmlElement

from ...utils.text import normalize_whitespace

# Elements that end a run of text. Inline elements deliberately absent: separating them
# would split words that <span>/<ix:*> wrapping breaks apart mid-token.
BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "center", "dd",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
        "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)

DROP_TAGS = frozenset({"script", "style", "noscript", "head", "meta", "link"})

# Inline-XBRL metadata containers. These are never rendered by a browser but carry no
# `display:none`, so the hidden-content rule does not catch them: <ix:resources> holds
# <xbrli:unit>iso4217:USD</xbrli:unit> and friends, which leaked into text as `USDxbrli`.
# `ix:nonFraction`, `ix:nonNumeric` and `ix:continuation` are deliberately NOT here -- they
# wrap values the filing actually displays.
XBRL_METADATA_TAGS = frozenset(
    {"ix:header", "ix:hidden", "ix:references", "ix:resources", "ix:exclude"}
)

# Namespaces that are pure XBRL metadata; nothing under them is display content.
XBRL_METADATA_PREFIXES = ("xbrli:", "xbrldi:", "link:", "xlink:", "iso4217:", "xsi:")


def is_xbrl_metadata(tag: str) -> bool:
    lowered = tag.lower()
    return lowered in XBRL_METADATA_TAGS or lowered.startswith(XBRL_METADATA_PREFIXES)

_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


def parse_html_bytes(raw: bytes) -> HtmlElement:
    """Parse raw bytes.

    Bytes, never str: 91 of 377 artifacts in this corpus begin with an XML declaration and
    `lxml.html.fromstring` raises `ValueError: Unicode strings with encoding declaration
    are not supported` when handed a `str`.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("parse_html_bytes requires bytes; SEC inline-XBRL files declare an encoding")
    return lxml.html.fromstring(raw)


def strip_non_content(root: HtmlElement) -> int:
    """Remove scripts, styles, comments, <head> and hidden content. Returns count removed.

    Hidden content is where inline XBRL parks its context and unit definitions along with
    duplicate fact text. Comments matter too: filing agents leave licensing banners in the
    head, and one of them put "Broadridge PROfile 25.10.1.5333" into extracted document
    text.
    """
    removed = 0
    for comment in root.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
            removed += 1
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        parent = element.getparent()
        if parent is None:
            continue
        tag = element.tag.lower()
        if tag in DROP_TAGS or is_xbrl_metadata(tag) or is_hidden(element):
            parent.remove(element)
            removed += 1
    return removed


def is_hidden(element: HtmlElement) -> bool:
    style = element.get("style") or ""
    if style and _HIDDEN_STYLE.search(style):
        return True
    return element.get("hidden") is not None or (element.get("aria-hidden") == "true")


def element_text(element: HtmlElement) -> str:
    """Text of an element with block-boundary separators inserted.

    Inline runs are concatenated directly so words survive `<span>` and `<ix:*>` splitting;
    block boundaries contribute a space so headings never fuse into the paragraph below.
    """
    parts: list[str] = []
    _walk(element, parts)
    return normalize_whitespace("".join(parts))


def _walk(element: HtmlElement, parts: list[str]) -> None:
    tag = element.tag.lower() if isinstance(element.tag, str) else ""
    block = tag in BLOCK_TAGS

    if block:
        parts.append(" ")
    if element.text:
        parts.append(element.text)
    for child in element:
        if isinstance(child.tag, str) and (
            child.tag.lower() in DROP_TAGS or is_xbrl_metadata(child.tag)
        ):
            continue
        _walk(child, parts)
        if child.tail:
            parts.append(child.tail)
    if block:
        parts.append(" ")


def visible_text(root: HtmlElement) -> str:
    """Whole-document visible text. Used for the source-coverage check."""
    return element_text(root)


def plain_text_from_bytes(raw: bytes) -> str:
    """Reference extraction used to measure a parser's source coverage."""
    root = parse_html_bytes(raw)
    strip_non_content(root)
    return visible_text(root)


def count_images(element: HtmlElement) -> int:
    return len(element.xpath(".//img"))


def font_weight_of(element: HtmlElement) -> int | None:
    """Numeric font weight from inline style or legacy markup.

    This corpus has zero <h1>-<h4> elements, so weight is the only styling signal
    available for heading inference.
    """
    style = (element.get("style") or "").lower().replace(" ", "")
    match = re.search(r"font-weight:(\d{3}|bold|bolder|normal|lighter)", style)
    if match:
        value = match.group(1)
        if value.isdigit():
            return int(value)
        return {"bold": 700, "bolder": 800, "normal": 400, "lighter": 300}[value]
    tag = element.tag.lower() if isinstance(element.tag, str) else ""
    if tag in {"b", "strong", "th"}:
        return 700
    return None


def font_size_of(element: HtmlElement) -> float | None:
    style = (element.get("style") or "").lower().replace(" ", "")
    match = re.search(r"font-size:([\d.]+)(pt|px|em|rem)", style)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "px":
        return round(value * 0.75, 2)
    if unit in {"em", "rem"}:
        return round(value * 12.0, 2)
    return value


def text_align_of(element: HtmlElement) -> str | None:
    style = (element.get("style") or "").lower().replace(" ", "")
    match = re.search(r"text-align:(\w+)", style)
    return match.group(1) if match else (element.get("align") or None)


def inside_table(element: HtmlElement) -> bool:
    parent = element.getparent()
    while parent is not None:
        if isinstance(parent.tag, str) and parent.tag.lower() == "table":
            return True
        parent = parent.getparent()
    return False
