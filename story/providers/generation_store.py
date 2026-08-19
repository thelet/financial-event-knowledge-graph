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

**A third, added 2026-08-19 at MULTI_PROVIDER_OPENAI §5.1: `provider_id`.** Until then the
digest covered `model_id` and nothing about *which adapter* was asked, so two providers
configured with one model string shared a row — the defect that plan records as F1. Two things
make it a real collision rather than a theoretical one: the two adapters send **structurally
different requests** for the same logical inputs (llama.cpp's nested `response_format` against
the Responses API's flat `text.format`), so one digest over both is a digest that means two
things; and a local llama.cpp server **answers to any model string**, so distinct model names
are not a defence. `IDENTITY_VERSION` moved to `story-generation-v2` in the same change, which
re-keys every committed row — the answers themselves are untouched and byte-identical, because
the digest is a pure function of inputs the demo path rebuilds deterministically.
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
IDENTITY_VERSION = "story-generation-v2"

#: The keys written for each row, in the order they are written. Fixed rather than derived
#: from the dataclass so that adding a field cannot silently reorder a committed file and
#: break the byte comparison that is the point of the file.
#:
#: **`model_id` is the identity and `provider_model_id` is the wire value, and they differ.**
#: The server reports the absolute `.gguf` path while the configuration names the basename,
#: and `request_identity` digests the configured one. Upstream stored only the wire value and
#: a replay-only run keyed every lookup on a string the digest was never taken over, missing
#: every row — a defect found by review on 2026-08-02 and not reproduced here.
#:
#: **`provider_id` was inserted before `model_id` on 2026-08-19, and that did reorder the
#: committed files.** The rule above is about a field arriving *silently*: `story-generation-v2`
#: re-keys every row, so every committed store was rewritten in the same change and there was no
#: byte comparison left to break. It sits beside the two model identifiers because the three
#: together are the answer to "who was asked", and a reader who has to scan to the end of a row
#: to find out which adapter it came from is reading a row that does not state its own key.
ROW_FIELDS: tuple[str, ...] = (
    "request_sha256", "content_sha256", "provider_id", "model_id", "provider_model_id",
    "schema_name", "prompt_version", "temperature", "max_tokens", "finish_reason",
    "raw_content",
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
    provider_id: str,
    model_id: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """The digest a generation is stored under. Eight inputs, and each earns its place.

    | input | why |
    | --- | --- |
    | `system` | three personas, one package — planner and verifier would share a row |
    | `prompt` | the request itself |
    | `schema` | the grammar the server was constrained by; a new schema is a new question |
    | `schema_name` | reaches the wire, and names the call site in a stored row |
    | `provider_id` | **2026-08-19**; which adapter was asked. Not implied by anything else — below |
    | `model_id` | the *configured* name, never the path the server reports |
    | `temperature` | pinned at 0.0 today, in the digest so it could never move unnoticed |
    | `max_tokens` | a determinism input, not a budget knob (§15.3): truncation is an answer |

    **What moved, and why** *(MULTI_PROVIDER_OPENAI §5.1, 2026-08-19)*. `provider_id` was the
    eighth input and `IDENTITY_VERSION` went to `story-generation-v2` in the same change. Two
    measurements make it necessary rather than tidy. First, the adapters do not send the same
    bytes: the local one nests the grammar under `response_format.json_schema`, the Responses
    one puts `name`/`strict`/`schema` flat inside `text.format` — so one digest standing for
    both is a digest that means two things, and a replayed row would answer a request that was
    never issued in that shape. Second, **a local llama.cpp server answers to any model string**,
    so telling the two apart by `model_id` alone is a defence that the runtime does not
    actually provide; the collision would present as a silently wrong replay, which is the one
    failure this store exists to prevent.

    A blank `provider_id` is **refused** rather than digested. `""` is not a provider, and
    hashing it would mint a key that looks like an identity and states nothing — the same
    argument `story/core/keys.py:_require` makes for every readable id in the package. Call
    sites read the value off a provider object that may be a decorator or a test double, so the
    empty string is exactly what a missing forward produces, and it has to fail here rather
    than turn into a row nothing can find again.

    The free-text fields are digested *before* the join rather than concatenated into it. With
    two of them, a flat `\\x1f` join is ambiguous — a system prompt ending in the separator
    would produce the digest of a different `(system, prompt)` pair — and that ambiguity is
    cheap to remove and expensive to discover.

    The schema is serialised with sorted keys because a JSON Schema is a mapping and Python's
    insertion order is not part of what was asked; two stages that build the same schema
    through different code paths must produce one key.
    """
    if not str(provider_id).strip():
        raise ValueError(
            "cannot key a generation on a blank provider_id: the digest tells two adapters "
            "apart and an empty string tells nothing apart. The caller reads it off the "
            "provider — a decorator that does not forward `provider_id` lands here")
    payload = "\x1f".join((
        IDENTITY_VERSION,
        "system=" + _text_digest(system),
        "prompt=" + _text_digest(prompt),
        "schema=" + _text_digest(json.dumps(dict(schema), sort_keys=True,
                                            separators=(",", ":"))),
        "schema_name=" + _text_digest(schema_name),
        "provider_id=" + _text_digest(provider_id),
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
    #: Which adapter produced this. A digest input since `story-generation-v2`, and stored so
    #: the file states its own key: a store whose rows do not say which provider they came from
    #: cannot be replayed from the file alone, which is the property `identity_model_id` already
    #: exists for and `identity_provider_id` now mirrors.
    provider_id: str
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
        provider_id: str,
        model_id: str,
        schema_name: str,
        temperature: float,
        max_tokens: int,
        prompt_version: str,
    ) -> "StoredGeneration":
        """`provider_id` and `model_id` are required keywords and are the identity.

        No default on either, on purpose: the caller is the only thing that knows which strings
        `request_sha256` was computed over, and reading them off `result` instead is precisely
        the defect this signature exists to prevent — `result.model_id` is the *wire* value and
        a `GenerationResult` names no provider at all.
        """
        return cls(
            request_sha256=request_sha256,
            content_sha256=result.content_sha256,
            provider_id=provider_id,
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

    def identity_provider_id(self) -> str | None:
        """The adapter every row was keyed under, or `None` if the rows disagree.

        `identity_model_id`'s argument, applied to the input that was missing from the digest
        until 2026-08-19: a file holding an OpenAI row beside a Qwen row cannot be replayed
        under one identity, and answering with either one would turn every row of the other
        into a miss — which is the silently-wrong-replay failure `request_identity` names.
        """
        identities = {row.provider_id for row in self._rows.values()}
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
        provider_id: str | None = None,
        model_id: str | None = None,
        prompt_version: str = "",
    ) -> None:
        resolved = model_id or getattr(inner, "model_id", "") or store.identity_model_id()
        if not resolved:
            raise ValueError(
                "a replay-only provider needs a model_id: with no server to ask and no rows "
                "to read one from, nothing states which model the stored generations came from")
        resolved_provider = (provider_id or getattr(inner, "provider_id", "")
                             or store.identity_provider_id())
        if not resolved_provider:
            raise ValueError(
                "a replay-only provider needs a provider_id: since `story-generation-v2` the "
                "request digest tells the adapters apart, and with no server to ask and no rows "
                "to read one from, nothing states which adapter the stored generations came "
                "from. Defaulting to the local one would key an OpenAI row as a Qwen row")
        self._store = store
        self._inner = inner
        # Falling back to the store's own rows is what makes a written file sufficient to
        # replay from. It is the last resort, not the first: an explicit id and a live server
        # both state the identity more authoritatively than a file does.
        self._model_id = resolved
        self._provider_id = resolved_provider
        self._prompt_version = prompt_version

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def store(self) -> GenerationStore:
        return self._store

    @property
    def config(self) -> Any:
        """The inner provider's `StoryProviderConfig`, or `None` on a replay-only run.

        Forwarded rather than reconstructed, and `None` rather than a stand-in: the manifest's
        `provider_settings` block reads it to record what was actually sent, and a replay-only
        run sent nothing at all. A synthesised config here would put a `max_output_tokens` in
        the manifest of a run that issued no request.
        """
        return getattr(self._inner, "config", None)

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
            provider_id=self._provider_id, model_id=self._model_id, temperature=temperature,
            max_tokens=max_tokens)
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
            result, request_sha256=identity, provider_id=self._provider_id,
            model_id=self._model_id, schema_name=schema_name,
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
                  "schema_name": stored.schema_name, "provider_id": stored.provider_id},
    )
