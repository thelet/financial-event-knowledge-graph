"""Persisted model generations, keyed by what was asked. Replay with no GPU and no server.

Named for the artifact it renders — `data/story_runs/<id>/generations.jsonl` (§14) — rather
than `answer_store.py` after the extraction original: the three story call sites are a
planner, a writer and an advisory verifier, and what they produce is a generation, not an
answer to a question.

§21's byte-identity claim rests on the distinction this module draws, and it is the same one
`extraction/stages/narrative/answer_store.py` records:

**Not achievable:** byte-identical *generation*. The local runtime is not bit-reproducible
across differing request histories, so a second run against the server is not promised to
agree with the first.

**Achievable and tested:** byte-identical *replay*. A generation is stored under the digest of
everything that determined it, so a later run rebuilds the same plan, the same draft and the
same verification from the same rows with nothing running.

**What is deliberately absent from the file is the design.** Latency, token counts, attempt
counts, llama.cpp's `id`/`created`/`timings`, and `raw_sha256` — which digests an envelope
carrying all three — change on every identical request. A record containing them could never
be byte-identical. They belong in the manifest and the report (§14), which is where the token
totals a reader wants are kept; they may not be stored here.

**Two digest inputs the extraction original does not have**: `system` and `schema_name`. Both
follow from §15.1 — three personas against one package would otherwise collide on one row, and
the planner's schema and the writer's schema would be told apart only by their content.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from story.core.models import GenerationResult, HealthStatus
from story.providers.public import PINNED_TEMPERATURE

#: Part of the request digest. A change to *what* the digest covers must change this, or rows
#: keyed under the old rule become silently unreachable rather than visibly stale. Prefixed
#: with `story-` because this store and extraction's may sit on one disk and are not
#: interchangeable: the same prompt, schema and model produce different keys here.
IDENTITY_VERSION = "story-generation-v1"

#: The keys written for each row, in the order they are written. Fixed rather than derived
#: from the dataclass so that adding a field cannot silently reorder a committed file and
#: break the byte comparison that is the point of the file.
#:
#: **`model_id` is the identity and `provider_model_id` is the wire value, and they differ.**
#: The server reports the absolute `.gguf` path while the configuration names the basename,
#: and `request_identity` digests the configured one. Upstream stored only the wire value and
#: a replay-only run keyed every lookup on a string the digest was never taken over, missing
#: every row — a defect found by review on 2026-08-02 and not reproduced here.
ROW_FIELDS: tuple[str, ...] = (
    "request_sha256", "content_sha256", "model_id", "provider_model_id", "schema_name",
    "prompt_version", "temperature", "max_tokens", "finish_reason", "raw_content",
)


class MissingGenerationError(LookupError):
    """A replay-only store was asked for a generation it does not hold.

    An error rather than a fall-through to the server. A replay that quietly reaches for a GPU
    is not a replay, and the failure would present as a slow run rather than a missing record.
    It carries the digest it missed on, because a run records every miss with the request it
    would have issued and a key that has to be parsed back out of prose is not a record.
    """

    def __init__(self, message: str, *, request_sha256: str = "") -> None:
        super().__init__(message)
        self.request_sha256 = request_sha256


def request_identity(
    *,
    system: str,
    prompt: str,
    schema: Mapping[str, Any],
    schema_name: str,
    model_id: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """The digest a generation is stored under. Seven inputs, and each earns its place.

    | input | why |
    | --- | --- |
    | `system` | three personas, one package — planner and verifier would share a row |
    | `prompt` | the request itself |
    | `schema` | the grammar the server was constrained by; a new schema is a new question |
    | `schema_name` | reaches the wire, and names the call site in a stored row |
    | `model_id` | the *configured* name, never the path the server reports |
    | `temperature` | pinned at 0.0 today, in the digest so it could never move unnoticed |
    | `max_tokens` | a determinism input, not a budget knob (§15.3): truncation is an answer |

    The free-text fields are digested *before* the join rather than concatenated into it. With
    two of them, a flat `\\x1f` join is ambiguous — a system prompt ending in the separator
    would produce the digest of a different `(system, prompt)` pair — and that ambiguity is
    cheap to remove and expensive to discover.

    The schema is serialised with sorted keys because a JSON Schema is a mapping and Python's
    insertion order is not part of what was asked; two stages that build the same schema
    through different code paths must produce one key.
    """
    payload = "\x1f".join((
        IDENTITY_VERSION,
        "system=" + _text_digest(system),
        "prompt=" + _text_digest(prompt),
        "schema=" + _text_digest(json.dumps(dict(schema), sort_keys=True,
                                            separators=(",", ":"))),
        "schema_name=" + _text_digest(schema_name),
        "model_id=" + _text_digest(model_id),
        f"temperature={float(temperature):.6f}",
        f"max_tokens={int(max_tokens)}",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredGeneration:
    """One generation, with everything needed to rebuild a stage's input and nothing volatile.

    `raw_content` rather than the parsed object is what is stored, and `content` is parsed back
    from it: round-tripping through `json.loads` twice would let a re-serialisation change the
    bytes `content_sha256` was taken over, and the printed form is the only thing that survives
    a scale or a sign error well enough to diagnose it.
    """

    request_sha256: str
    content_sha256: str
    #: The identifier `request_sha256` was taken over — what the run called the model.
    model_id: str
    #: What the server called itself. Informational, never a digest input, and kept so a
    #: replayed result reports the same model id the recorded one did.
    provider_model_id: str
    #: Which call site this row came from. A digest input, and stored as well because a file a
    #: reader cannot sort into planner, writer and verifier rows is a file nobody reads.
    schema_name: str
    prompt_version: str
    temperature: float
    max_tokens: int
    finish_reason: str
    raw_content: str

    @property
    def content(self) -> dict[str, Any]:
        parsed = json.loads(self.raw_content)
        return parsed if isinstance(parsed, dict) else {}

    def as_row(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ROW_FIELDS}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredGeneration":
        return cls(**{name: row[name] for name in ROW_FIELDS})

    @classmethod
    def from_result(
        cls,
        result: GenerationResult,
        *,
        request_sha256: str,
        model_id: str,
        schema_name: str,
        temperature: float,
        max_tokens: int,
        prompt_version: str,
    ) -> "StoredGeneration":
        """`model_id` is a required keyword and is the identity, not the wire value.

        No default, on purpose: the caller is the only thing that knows which string
        `request_sha256` was computed over, and reading it off `result` instead is precisely
        the defect this signature exists to prevent.
        """
        return cls(
            request_sha256=request_sha256,
            content_sha256=result.content_sha256,
            model_id=model_id,
            provider_model_id=result.model_id,
            schema_name=schema_name,
            prompt_version=prompt_version,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            finish_reason=result.finish_reason,
            raw_content=result.raw_content,
        )


class GenerationStore:
    """A JSONL file of generations, rebuilt whole and never appended to concurrently.

    Derived-index discipline, the same rule the normalization catalogs and the extraction
    answer store follow: rows are sorted by request digest and the file is rewritten in full,
    because an append-only log grows a duplicate every time a candidate is re-planned and the
    file stops being a function of the run.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._rows: dict[str, StoredGeneration] = {}
        if self._path is not None and self._path.is_file():
            self.load()

    @property
    def path(self) -> Path | None:
        return self._path

    def __len__(self) -> int:
        return len(self._rows)

    def load(self) -> None:
        self._rows = {}
        if self._path is None or not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = StoredGeneration.from_row(json.loads(line))
                self._rows[row.request_sha256] = row

    def get(self, request_sha256: str) -> StoredGeneration | None:
        return self._rows.get(request_sha256)

    def put(self, row: StoredGeneration) -> None:
        self._rows[row.request_sha256] = row

    def generations(self) -> tuple[StoredGeneration, ...]:
        """Every row, ordered by request digest — the order `render` writes them in."""
        return tuple(self._rows[key] for key in sorted(self._rows))

    def identity_model_id(self) -> str | None:
        """The model identifier every row was keyed under, or `None` if the rows disagree.

        The file describing its own key is what makes a replay reconstructable from the file
        alone. Disagreement returns `None` rather than some row's answer: a store holding two
        models' generations cannot be replayed under one identity, and picking one silently
        would turn every row of the other into a miss.
        """
        identities = {row.model_id for row in self._rows.values()}
        return identities.pop() if len(identities) == 1 else None

    def render(self) -> str:
        """The file's exact bytes. Public so a test can compare two runs with no filesystem."""
        return "".join(
            json.dumps(self._rows[key].as_row(), sort_keys=False, ensure_ascii=False,
                       separators=(",", ":")) + "\n"
            for key in sorted(self._rows)
        )

    def write(self, path: Path | None = None) -> Path:
        """Stage, then rename. A partial file must be unambiguously incomplete (§1.6)."""
        target = Path(path) if path is not None else self._path
        if target is None:
            raise ValueError("GenerationStore has no path to write to")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".partial")
        staging.write_text(self.render(), encoding="utf-8")
        os.replace(staging, target)
        self._path = target
        return target


class ReplayingStoryGenerationProvider:
    """Satisfies `story.contracts.StoryGenerationProvider` from a store, and optionally a server.

    A decorator rather than a branch inside a stage. A stage's job is to build one request and
    read one result; whether that result came from a GPU three weeks ago or from a socket just
    now is not a decision it should be able to see, and an `if self._store` in the middle of
    the planner would make every replay test a test of that branch.

    With `inner=None` the store is authoritative and a miss raises. That is the configuration
    `story rebuild` runs under, and it is the one that proves the rows on disk are sufficient.
    """

    def __init__(
        self,
        store: GenerationStore,
        inner: Any = None,
        *,
        model_id: str | None = None,
        prompt_version: str = "",
    ) -> None:
        resolved = model_id or getattr(inner, "model_id", "") or store.identity_model_id()
        if not resolved:
            raise ValueError(
                "a replay-only provider needs a model_id: with no server to ask and no rows "
                "to read one from, nothing states which model the stored generations came from")
        self._store = store
        self._inner = inner
        # Falling back to the store's own rows is what makes a written file sufficient to
        # replay from. It is the last resort, not the first: an explicit id and a live server
        # both state the identity more authoritatively than a file does.
        self._model_id = resolved
        self._prompt_version = prompt_version

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def store(self) -> GenerationStore:
        return self._store

    def health(self) -> HealthStatus:
        """The inner provider's, or a statement that no server is needed.

        `ok=True` with no server is the honest answer, not a convenient one: a replay-only run
        is fully able to proceed, and reporting it unhealthy would make the freshness gate
        refuse the one configuration that provably needs nothing running.
        """
        if self._inner is not None:
            return self._inner.health()
        return HealthStatus(
            ok=True, status="replay",
            detail=f"replay-only, {len(self._store)} stored generations")

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float = PINNED_TEMPERATURE,
    ) -> GenerationResult:
        identity = request_identity(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            model_id=self._model_id, temperature=temperature, max_tokens=max_tokens)
        stored = self._store.get(identity)
        if stored is not None:
            return _replayed(stored, identity)
        if self._inner is None:
            raise MissingGenerationError(
                f"no stored generation for request {identity} (schema {schema_name!r}); this "
                f"store holds {len(self._store)}",
                request_sha256=identity)
        result = self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        self._store.put(StoredGeneration.from_result(
            result, request_sha256=identity, model_id=self._model_id, schema_name=schema_name,
            temperature=temperature, max_tokens=max_tokens,
            prompt_version=self._prompt_version))
        return result


def _replayed(stored: StoredGeneration, identity: str) -> GenerationResult:
    """A stored row as a `GenerationResult`, with the unrecorded fields left empty.

    Zeroes rather than remembered numbers. Token counts and latency were deliberately not
    stored, and inventing plausible ones here would make a replayed result indistinguishable
    from a generated one in exactly the place a reader wants to tell them apart.
    """
    return GenerationResult(
        content=stored.content,
        raw_content=stored.raw_content,
        # The wire value, because that is what `GenerationResult.model_id` means and what the
        # recorded run put on its artifacts. The identity lives in `StoredGeneration.model_id`.
        model_id=stored.provider_model_id or stored.model_id,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        latency_ms=0.0,
        raw_sha256="",
        content_sha256=stored.content_sha256,
        finish_reason=stored.finish_reason,
        attempts=0,
        metadata={"replayed": True, "request_sha256": identity,
                  "schema_name": stored.schema_name},
    )
