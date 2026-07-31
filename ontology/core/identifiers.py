"""Identifier rules and deterministic hashing.

Concept ids are snake_case and stable: they appear in extraction schemas, graph nodes and
evidence panels, so they are readable by design rather than opaque.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

CONCEPT_ID = re.compile(r"^[a-z][a-z0-9_]*$")
RELATIONSHIP_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")
INSTANCE_ID = re.compile(r"^[a-z][a-z0-9_.:-]*$")


def validate_concept_id(value: str) -> str:
    if not CONCEPT_ID.match(value or ""):
        raise ValueError(f"concept id must be snake_case: {value!r}")
    return value


def validate_relationship_id(value: str) -> str:
    if not RELATIONSHIP_ID.match(value or ""):
        raise ValueError(f"relationship id must be UPPER_SNAKE_CASE: {value!r}")
    return value


def validate_instance_id(value: str) -> str:
    if not INSTANCE_ID.match(value or ""):
        raise ValueError(f"instance id must be lower-case: {value!r}")
    return value


def canonical_json(payload: Any) -> str:
    """Stable rendering: sorted keys, no incidental whitespace, ASCII-escaped."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_alias(alias: str) -> str:
    """Case- and whitespace-normalized lookup key.

    Quotation marks are folded because the corpus writes both `homes "on the market"` and
    `homes on the market`. The exact source label is always preserved separately; this is
    only the lookup key.
    """
    folded = re.sub(r"[“”‘’\"']", "", str(alias).lower())
    return re.sub(r"\s+", " ", folded).strip()
