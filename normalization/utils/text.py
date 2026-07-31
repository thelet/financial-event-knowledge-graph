"""Text helpers.

The separator rule here is a correctness requirement, not cosmetics: SEC HTML places
headings and paragraphs in adjacent block elements, and concatenating their text without a
separator produces run-together tokens such as `ASSETSFor` and `ActivitiesNet`.
"""

from __future__ import annotations

import re

_WS = re.compile(r"[\s   ]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'“(]?[A-Z0-9])")


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace, including the non-breaking kinds SEC HTML is full of."""
    return _WS.sub(" ", str(text)).strip()


def split_sentences(text: str) -> list[str]:
    """Coarse sentence split, used only to break over-long blocks on a safe boundary."""
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


_EMAIL_OR_URL = re.compile(r"\S+@\S+|https?://\S+|www\.\S+")


def has_run_together_tokens(text: str) -> list[str]:
    """Detect the `ASSETSFor` failure: an all-caps run immediately followed by TitleCase.

    Emails and URLs are removed first: `ODInvestor@opendoor.com` is a real address, not a
    fused pair of words, and flagging it would train reviewers to ignore the check.
    """
    cleaned = _EMAIL_OR_URL.sub(" ", str(text))
    return re.findall(r"\b[A-Z]{3,}[a-z][a-z]+\b", cleaned)


def truncate(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
