"""The normalized passage catalog, read once and served three ways.

Three consumers want three different views of the same file and none of them should open it
itself: the selector wants the rows, the lanes want a passage's text, and `verify` wants the
`PassageSource` port so evidence resolution is checked against something real. This is the one
place `passages.jsonl` is parsed.

**It reads a file and nothing else.** No normalization type is imported — the rows are plain
dicts, exactly as `TypedCandidateSelector.select_from_rows` already expects — so extraction
keeps importing nothing from normalization's storage and this stays testable from a list of
dicts with no corpus on disk.

`corpus_id` is here because a run id derives from it. It is a statement about the *contents*
of the catalog, not about when it was built: two catalogs with the same documents, passages
and passage digests are the same corpus to a run that reads them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .identifiers import digest

PASSAGES_FILENAME = "passages.jsonl"


@dataclass(frozen=True)
class CorpusIdentity:
    """What a run consumed, in a form two runs can be compared on.

    Readable first and digested second, the same rule every other id in this layer follows:
    `294d-12442p-…` says at a glance which corpus a run directory belongs to, and the digest
    says whether it is *that* corpus rather than one of the same size.
    """

    documents: int
    passages: int
    table_passages: int
    content_digest: str

    @property
    def corpus_id(self) -> str:
        return f"{self.documents}d-{self.passages}p-{self.content_digest}"


class JsonlPassageCorpus:
    """The catalog in memory, satisfying `extraction.contracts.PassageSource`.

    12,442 rows is roughly 60 MB of text and the run touches almost all of it — a lazy reader
    would re-parse the file for every lane. Held whole, sorted by passage id, so every
    derived artifact has one ordering and it is not the file's.
    """

    def __init__(self, rows: Iterable[dict]) -> None:
        ordered = sorted(rows, key=lambda row: row["passage_id"])
        self._rows: dict[str, dict] = {row["passage_id"]: row for row in ordered}

    @classmethod
    def from_catalog(cls, catalog_root: Path) -> "JsonlPassageCorpus":
        path = Path(catalog_root) / PASSAGES_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"{path} does not exist; normalize the corpus first")
        with path.open(encoding="utf-8") as handle:
            return cls(json.loads(line) for line in handle if line.strip())

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self) -> list[dict]:
        return list(self._rows.values())

    def __iter__(self) -> Iterator[dict]:
        return iter(self._rows.values())

    def row(self, passage_id: str) -> dict | None:
        return self._rows.get(passage_id)

    # -- the `PassageSource` port ------------------------------------------------------------

    def text_of(self, passage_id: str) -> str | None:
        row = self._rows.get(passage_id)
        return None if row is None else str(row.get("text") or "")

    def exists(self, passage_id: str) -> bool:
        return passage_id in self._rows

    def document_of(self, passage_id: str) -> str | None:
        row = self._rows.get(passage_id)
        return None if row is None else str(row.get("document_id") or "")

    # -- identity ----------------------------------------------------------------------------

    def identity(self) -> CorpusIdentity:
        """Counts plus a digest over (passage id, content digest) for every passage.

        `content_sha256` is the normalizer's own per-passage digest, so this covers the text
        without re-hashing 60 MB, and it changes if any passage's bytes change. Passage ids are
        included beside it because two corpora can hold the same texts under different
        anchors, and evidence resolves on the anchor.
        """
        parts: list[str] = []
        documents: set[str] = set()
        tables = 0
        for row in self._rows.values():
            parts.append(str(row["passage_id"]))
            parts.append(str(row.get("content_sha256") or ""))
            documents.add(str(row.get("document_id") or ""))
            if str(row.get("passage_kind") or "") == "table":
                tables += 1
        return CorpusIdentity(
            documents=len(documents),
            passages=len(self._rows),
            table_passages=tables,
            content_digest=digest(*parts),
        )
