"""The vector cache: one JSON file, one identity, and a miss that is never a zero vector.

No vector database and no ANN index. 131 concept vectors and a few dozen passage vectors are
a dict, and a brute-force cosine over 131 unit vectors is a `sum(a*b for …)` — which is why
this stage adds no dependency (STAGE_09 §1).

**Identity is the whole point.** A vector produced by a different model, at different
dimensions, or from a different rendering of the same concept is not a stale entry; it is a
wrong answer that looks exactly like a right one. The header therefore carries a `cache_key`
— sha256 over `definition_hash | model_id | dimensions | renderer_version |
text_normalization_version`, in that order — and a file whose key disagrees with the computed
one is **rejected outright**, never silently reused and never partially reused. Editing
`aliases.yaml` moves `definition_hash` and invalidates the cache; that is the intended
behaviour, not an inconvenience.

**Vectors are stored as base64 of little-endian float32**, not decimal text. A 1024-float
vector is not human-auditable at any precision, so readability buys nothing there, while
float32 round-trips bit-identically, so the encoding is never the thing that moved. Everything
a human can actually check — the label, the exact text embedded, its sha256, the header —
stays plain text beside it.

**The committed cache is the authority, and it has to be.** The encoding round-trips exactly;
the *server* does not. Its output depends on the request that preceded it (see
`providers/local_openai_compatible_embeddings.py`, which corrects STAGE_09 §1.1 with the
measurement). Two full rebuilds from an empty cache — same texts, same order, same batching —
were byte-identical to each other on one occasion and **were not on another** *(both measured
2026-08-01; STAGE_09 §11.1)*. Identical request history is therefore not sufficient for
identical bytes, and byte-identity of a rebuild is not a property to depend on or to explain
away. What does hold is agreement: a rebuilt vector is within cosine 0.9999 of the committed
one and selects the same candidates. So the file is committed, it is the authority, and a
rebuild is checked for *agreement*, never for bytes. Byte-identity is required only of reports
generated **from** the committed file, where nothing but this code is in the loop.

**Fill-through, or an error.** A miss with a provider embeds and records; a miss with no
provider raises and names the text. Never a zero vector: a zero vector has cosine 0.0 against
everything, so a scope built on one would quietly return no semantic candidates and look like
a model that found nothing rather than a cache that was incomplete.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .concept_rendering import (
    RENDERER_VERSION,
    TEXT_NORMALIZATION_VERSION,
    normalize_for_embedding,
)

CACHE_FORMAT = "embedding-cache-v1"

# How much of a missing text an error message quotes. Long enough to identify a passage,
# short enough that a stack trace stays readable.
MISSING_TEXT_PREVIEW = 120


class VectorCacheError(RuntimeError):
    """Base of every failure this module raises."""


class StaleVectorCacheError(VectorCacheError):
    """The file on disk was built for a different ontology, model, or rendering."""


class MissingVectorError(VectorCacheError):
    """A text is not cached and no provider is configured to embed it."""


@dataclass(frozen=True)
class CacheIdentity:
    """The five inputs that decide whether two vectors are comparable.

    `definition_hash` is in here rather than only in the report because the rendered string of
    a concept is a function of the ontology: an alias added to `aliases.yaml` changes what was
    embedded, and a cache that survived that edit would compare a new passage against an old
    vocabulary while every hash in the report still looked consistent.
    """

    definition_hash: str
    model_id: str
    dimensions: int
    renderer_version: str = RENDERER_VERSION
    text_normalization_version: str = TEXT_NORMALIZATION_VERSION

    @property
    def key(self) -> str:
        material = "|".join((
            self.definition_hash,
            self.model_id,
            str(self.dimensions),
            self.renderer_version,
            self.text_normalization_version,
        ))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_header(self) -> dict:
        return {
            "format": CACHE_FORMAT,
            "cache_key": self.key,
            "definition_hash": self.definition_hash,
            "model_id": self.model_id,
            "dimensions": self.dimensions,
            "renderer_version": self.renderer_version,
            "text_normalization_version": self.text_normalization_version,
        }


@dataclass(frozen=True)
class CachedVector:
    """One embedded text. `label` is audit metadata and is not part of any identity."""

    text: str
    text_sha256: str
    vector: tuple[float, ...]
    label: str = ""


def text_key(text: str) -> str:
    """The cache key of a text: sha256 of its normalized form.

    Normalized first, so a passage that gained a line break in re-normalization is the same
    entry rather than a silent second embedding of the same sentence.
    """
    return hashlib.sha256(
        normalize_for_embedding(text).encode("utf-8")).hexdigest()


def encode_vector(vector: Sequence[float]) -> str:
    """Little-endian float32, base64. The inverse of `decode_vector`, bit for bit."""
    return base64.b64encode(
        struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


def decode_vector(encoded: str) -> tuple[float, ...]:
    raw = base64.b64decode(encoded.encode("ascii"))
    if len(raw) % 4:
        raise VectorCacheError(
            f"encoded vector is {len(raw)} bytes, not a whole number of float32s")
    return struct.unpack(f"<{len(raw) // 4}f", raw)


class VectorCache:
    """Load, look up, fill through, save. One file, one identity.

    Not thread-safe and deliberately not concurrent: per the repository's durability rules a
    derived index is rebuilt rather than appended to by two writers, and `save` stages into a
    temporary file and renames, so a partial write is never observed as a complete cache.
    """

    def __init__(
        self,
        identity: CacheIdentity,
        *,
        entries: dict[str, CachedVector] | None = None,
        provider=None,
        path: Path | None = None,
    ) -> None:
        self._identity = identity
        self._entries: dict[str, CachedVector] = dict(entries or {})
        self._provider = provider
        self._path = path
        self._added = 0

    # -- identity ---------------------------------------------------------------------------

    @property
    def identity(self) -> CacheIdentity:
        return self._identity

    @property
    def cache_key(self) -> str:
        return self._identity.key

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def added(self) -> int:
        """Entries embedded rather than read, since load. The cache-miss count of a run."""
        return self._added

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, text: object) -> bool:
        return isinstance(text, str) and text_key(text) in self._entries

    # -- loading ----------------------------------------------------------------------------

    @classmethod
    def load(
        cls, path: Path, identity: CacheIdentity, *, provider=None
    ) -> "VectorCache":
        """The cache at `path`, or an empty one if there is no file there yet.

        A missing file is an ordinary state — the first run against a fresh checkout of the
        runtime cache root. A file that exists and disagrees is not, and raises.
        """
        if not path.is_file():
            return cls(identity, provider=provider, path=path)

        document = json.loads(path.read_text(encoding="utf-8"))
        header = document.get("header") or {}
        found = header.get("cache_key")
        if found != identity.key:
            raise StaleVectorCacheError(
                f"{path.name} was built for cache_key {found!r}, this run computes "
                f"{identity.key!r}: header {header!r} against "
                f"{identity.as_header()!r}. Rebuild it; a cache is never partially reused."
            )

        entries: dict[str, CachedVector] = {}
        for row in document.get("entries") or ():
            vector = decode_vector(row["vector"])
            if len(vector) != identity.dimensions:
                raise StaleVectorCacheError(
                    f"{path.name} entry {row.get('key')!r} has {len(vector)} dimensions, "
                    f"not {identity.dimensions}")
            entries[row["key"]] = CachedVector(
                text=row["text"], text_sha256=row["key"], vector=vector,
                label=row.get("label", ""))
        return cls(identity, entries=entries, provider=provider, path=path)

    # -- lookup -----------------------------------------------------------------------------

    def vector_for(self, text: str, *, label: str = "") -> tuple[float, ...]:
        return self.vectors_for((text,), labels=(label,))[0]

    def vectors_for(
        self, texts: Sequence[str], *, labels: Sequence[str] | None = None
    ) -> tuple[tuple[float, ...], ...]:
        """Vectors for `texts`, in order. Fills through on a miss, or raises naming it.

        Misses are embedded once and de-duplicated by key first: the concept vocabulary and
        the ablation arm differ in exactly one rendered string, and embedding the other 130
        twice would be a minute of GPU time spent proving `sha256` works.
        """
        if labels is not None and len(labels) != len(texts):
            raise VectorCacheError(
                f"{len(labels)} labels for {len(texts)} texts")

        keys = [text_key(text) for text in texts]
        pending: dict[str, tuple[str, str]] = {}
        for position, (key, text) in enumerate(zip(keys, texts)):
            if key in self._entries or key in pending:
                continue
            label = labels[position] if labels is not None else ""
            pending[key] = (text, label)

        if pending:
            self._fill(pending)
        return tuple(self._entries[key].vector for key in keys)

    def _fill(self, pending: dict[str, tuple[str, str]]) -> None:
        if self._provider is None:
            text, _label = next(iter(pending.values()))
            raise MissingVectorError(
                f"{len(pending)} text(s) are not in the vector cache and no embedding "
                f"provider is configured; the first is "
                f"{normalize_for_embedding(text)[:MISSING_TEXT_PREVIEW]!r}. A miss is never "
                f"a zero vector: rebuild the cache against the embedding server, or pass a "
                f"provider."
            )

        # Normalized before the wire, not only before the hash. The stored text is what was
        # actually sent, so a cached vector and a freshly embedded one are the same function
        # of the same bytes.
        ordered = sorted(pending.items())
        normalized = [normalize_for_embedding(text) for _key, (text, _label) in ordered]
        vectors = self._provider.embed(normalized)
        if len(vectors) != len(ordered):
            raise VectorCacheError(
                f"provider returned {len(vectors)} vectors for {len(ordered)} texts")

        for (key, (_raw, label)), text, vector in zip(ordered, normalized, vectors):
            if len(vector) != self._identity.dimensions:
                raise VectorCacheError(
                    f"provider returned {len(vector)} dimensions, not "
                    f"{self._identity.dimensions}")
            self._entries[key] = CachedVector(
                text=text, text_sha256=key, vector=tuple(vector), label=label)
            self._added += 1

    # -- writing ----------------------------------------------------------------------------

    def as_document(self) -> dict:
        """The file's exact content. Entries sorted by key, so two runs write one file."""
        return {
            "header": self._identity.as_header(),
            "entries": [
                {"key": entry.text_sha256, "label": entry.label, "text": entry.text,
                 "vector": encode_vector(entry.vector)}
                for entry in sorted(self._entries.values(), key=lambda e: e.text_sha256)
            ],
        }

    def save(self, path: Path | None = None) -> Path:
        """Atomic: stage beside the target, then rename. A partial file is never named."""
        target = path or self._path
        if target is None:
            raise VectorCacheError("no path to save to")
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(target.name + ".staging")
        staged.write_text(
            json.dumps(self.as_document(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8")
        os.replace(staged, target)
        self._path = target
        return target


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """A dot product, because both operands are unit vectors.

    The embedding adapter refuses any vector whose norm is not 1.0 within tolerance, so the
    division a general cosine would do is a division by 1.0 — arithmetic that could only
    introduce a difference between two runs. `math.fsum` rather than `sum` because it is
    exactly rounded and therefore order-insensitive: this number is rounded into a committed
    report, and a future change to how the 1024 terms are traversed must not be able to move
    its last decimal.

    A length mismatch raises. `zip` would silently stop at the shorter operand and return a
    partial dot product — a number in [0, 1] that looks exactly like a real similarity, which
    is how a 1024-dimension cache compared against a 768-dimension one would produce a
    plausible, wrong ranking instead of an error.
    """
    if len(left) != len(right):
        raise VectorCacheError(
            f"cosine between vectors of {len(left)} and {len(right)} dimensions: a truncated "
            f"dot product is indistinguishable from a real similarity")
    return math.fsum(a * b for a, b in zip(left, right))
