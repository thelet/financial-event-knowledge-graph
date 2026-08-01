"""Persisted model answers, keyed by what was asked. Replay without a GPU.

STAGE_10 §7 asks for two different things and only one of them is achievable, which is the
reason this module exists as its own file rather than as a dict inside the lane.

**Not achievable:** byte-identical *generation*. STAGE_09 established that the local runtime
is not bit-reproducible across differing request histories, so a second run against the server
is not promised to agree with the first.

**Achievable and tested:** byte-identical *replay*. An answer is stored under the digest of
everything that determined it — prompt, schema, model, temperature, output budget — so a later
run reconstructs the same claims from the same answers with no server involved, and step 11
can regenerate a report on a machine with no GPU.

**What is deliberately absent from the file is the whole design.** Latency, token counts,
attempt counts, llama.cpp's `id`, `created` and `timings`, and `raw_sha256` — which digests an
envelope carrying all three — are operational statistics. They change on every identical
request, so a record containing them could never be byte-identical, and the same rule settled
STAGE_09 §11.2. They may be logged and printed; they may not be stored here.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...contracts import GenerationResult

# Part of the request digest. A change to what the digest covers must change this, or answers
# keyed under the old rule become silently unreachable rather than visibly stale.
IDENTITY_VERSION = "v1"

# The keys written for each answer, in the order they are written. Fixed rather than derived
# from a dict so that adding a field to `StoredAnswer` cannot silently reorder a committed
# file and break the byte comparison that is the point of the file.
#
# **`model_id` is the identity, `provider_model_id` is the wire value, and they differ.** The
# server reports the absolute `.gguf` path while `config/extraction.yaml` names the basename,
# and `request_identity` digests the configured one. The file previously stored only the wire
# value, so a replay-only run configured from the file's own contents keyed every lookup on a
# string the digest was never taken over and missed every row *(verified against the live
# server 2026-08-01, found by review 2026-08-02; the offline test passed because the stub makes
# the two identical)*. Both are stored now: a record that cannot state what it is keyed by is
# not a durable record.
ROW_FIELDS: tuple[str, ...] = (
    "request_sha256", "content_sha256", "model_id", "provider_model_id", "temperature",
    "max_tokens", "prompt_version", "finish_reason", "raw_content",
)


class MissingAnswerError(LookupError):
    """A replay-only store was asked for an answer it does not hold.

    An error rather than a fall-through to generation. A replay that quietly reaches for a
    server is not a replay, and the failure would present as a slow run rather than a missing
    record.
    """


def request_identity(
    *, prompt: str, schema: dict, model_id: str, temperature: float, max_tokens: int
) -> str:
    """The digest an answer is stored under.

    The schema is serialised with sorted keys because a JSON Schema is a mapping and Python's
    insertion order is not part of what was asked; two runs that build the same schema through
    different code paths must produce one key.
    """
    payload = "\x1f".join((
        IDENTITY_VERSION,
        prompt,
        json.dumps(schema, sort_keys=True, separators=(",", ":")),
        model_id,
        f"{float(temperature):.6f}",
        str(int(max_tokens)),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredAnswer:
    """One answer, with everything needed to rebuild a claim from it and nothing volatile.

    `raw_content` rather than the parsed object is what is stored, and `content` is parsed back
    from it. A scale or a sign error is only recoverable when the printed form survives, and
    round-tripping through `json.loads` twice would let a re-serialisation change the bytes the
    digest was taken over.
    """

    request_sha256: str
    content_sha256: str
    # The identifier `request_sha256` was taken over — what the run called the model.
    model_id: str
    # What the server called itself. Informational, never part of a digest, and kept so a
    # replayed `GenerationResult` reports the same model id the recorded one did.
    provider_model_id: str
    temperature: float
    max_tokens: int
    prompt_version: str
    finish_reason: str
    raw_content: str

    @property
    def content(self) -> dict[str, Any]:
        parsed = json.loads(self.raw_content)
        return parsed if isinstance(parsed, dict) else {}

    def as_row(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in ROW_FIELDS}

    @classmethod
    def from_row(cls, row: dict) -> "StoredAnswer":
        return cls(**{field: row[field] for field in ROW_FIELDS})

    @classmethod
    def from_result(
        cls, result: GenerationResult, *, request_sha256: str, model_id: str,
        temperature: float, max_tokens: int, prompt_version: str,
    ) -> "StoredAnswer":
        """`model_id` is required and is the identity, not the wire value.

        A keyword with no default on purpose. The caller is the only thing that knows which
        string `request_sha256` was computed over, and reading it off `result` instead is
        precisely the defect this signature exists to prevent.
        """
        return cls(
            request_sha256=request_sha256,
            content_sha256=result.content_sha256,
            model_id=model_id,
            provider_model_id=result.model_id,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            prompt_version=prompt_version,
            finish_reason=result.finish_reason,
            raw_content=result.raw_content,
        )


class AnswerStore:
    """A JSONL file of answers, rebuilt whole and never appended to concurrently.

    Derived-index discipline, the same the normalization catalogs follow: rows are sorted by
    request digest and the file is rewritten in full, because an append-only log of model
    answers grows a duplicate every time a passage is re-read and the file stops being a
    function of the run.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._answers: dict[str, StoredAnswer] = {}
        if self._path is not None and self._path.is_file():
            self.load()

    @property
    def path(self) -> Path | None:
        return self._path

    def __len__(self) -> int:
        return len(self._answers)

    def load(self) -> None:
        self._answers = {}
        if self._path is None or not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                answer = StoredAnswer.from_row(json.loads(line))
                self._answers[answer.request_sha256] = answer

    def get(self, request_sha256: str) -> StoredAnswer | None:
        return self._answers.get(request_sha256)

    def identity_model_id(self) -> str | None:
        """The model identifier every row was keyed under, or None if the rows disagree.

        The file describing its own key is what makes a replay reconstructable from the file
        alone. Disagreement returns None rather than a first row's answer: a store holding two
        models' answers cannot be replayed under one identity, and picking one silently would
        turn every row of the other into a miss.
        """
        identities = {answer.model_id for answer in self._answers.values()}
        return identities.pop() if len(identities) == 1 else None

    def put(self, answer: StoredAnswer) -> None:
        self._answers[answer.request_sha256] = answer

    def render(self) -> str:
        """The file's exact bytes. Public so a test can compare two runs without a filesystem."""
        return "".join(
            json.dumps(self._answers[key].as_row(), sort_keys=False,
                       ensure_ascii=False, separators=(",", ":")) + "\n"
            for key in sorted(self._answers)
        )

    def write(self, path: Path | None = None) -> Path:
        """Stage, then rename. A partial file must be unambiguously incomplete."""
        target = Path(path) if path is not None else self._path
        if target is None:
            raise ValueError("AnswerStore has no path to write to")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".partial")
        staging.write_text(self.render(), encoding="utf-8")
        os.replace(staging, target)
        self._path = target
        return target


class ReplayingGenerationProvider:
    """Satisfies `extraction.contracts.GenerationProvider` from a store, and optionally a server.

    A decorator rather than a branch inside the lane. The lane's job is to build one request
    and read one answer; whether that answer came from a GPU three weeks ago or from a socket
    just now is not a decision it should be able to see, and a `if self._store` in the middle
    of `extract` would make every replay test a test of that branch.

    With `inner=None` the store is authoritative and a miss raises. That is the configuration
    step 11 runs under, and it is the one that proves the answers on disk are sufficient.
    """

    def __init__(
        self,
        store: AnswerStore,
        inner=None,
        *,
        model_id: str | None = None,
        prompt_version: str = "",
    ) -> None:
        resolved = model_id or getattr(inner, "model_id", "") or store.identity_model_id()
        if not resolved:
            raise ValueError(
                "a replay-only provider needs a model_id: with no server to ask and no rows "
                "to read one from, nothing states which model the stored answers came from")
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
    def store(self) -> AnswerStore:
        return self._store

    def generate(
        self, *, prompt: str, schema: dict, max_tokens: int = 1024, temperature: float = 0.0
    ) -> GenerationResult:
        identity = request_identity(
            prompt=prompt, schema=schema, model_id=self._model_id,
            temperature=temperature, max_tokens=max_tokens)
        stored = self._store.get(identity)
        if stored is not None:
            return _replayed(stored, identity)
        if self._inner is None:
            raise MissingAnswerError(
                f"no stored answer for request {identity[:12]}…; this store holds "
                f"{len(self._store)}")
        result = self._inner.generate(
            prompt=prompt, schema=schema, max_tokens=max_tokens, temperature=temperature)
        self._store.put(StoredAnswer.from_result(
            result, request_sha256=identity, model_id=self._model_id,
            temperature=temperature, max_tokens=max_tokens,
            prompt_version=self._prompt_version))
        return result


def _replayed(stored: StoredAnswer, identity: str) -> GenerationResult:
    """A stored answer as a `GenerationResult`, with the unrecorded fields left empty.

    Zeroes rather than remembered numbers. Token counts and latency were deliberately not
    stored, and inventing plausible ones here would make a replayed result indistinguishable
    from a generated one in exactly the place a reader would want to tell them apart.
    """
    return GenerationResult(
        content=stored.content,
        raw_content=stored.raw_content,
        # The wire value, because that is what `GenerationResult.model_id` means and what the
        # recorded run put on its claims. The identity lives in `StoredAnswer.model_id`.
        model_id=stored.provider_model_id or stored.model_id,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        latency_ms=0.0,
        raw_sha256="",
        content_sha256=stored.content_sha256,
        finish_reason=stored.finish_reason,
        attempts=0,
        metadata={"replayed": True, "request_sha256": identity},
    )
