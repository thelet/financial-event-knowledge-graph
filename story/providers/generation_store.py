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

**A fourth and a fifth, added 2026-08-19 at `story-generation-v3` after an adversarial review
reproduced the collision: `temperature_sent` and `reasoning_effort`.** S12 gave
`StoryProviderConfig` two settings that decide what the request body contains and left both out
of the key, so structurally different wire requests shared one `request_sha256`:

| the configuration | `reasoning` in the body | `temperature` in the body |
| --- | --- | --- |
| `reasoning_effort=None` | *no block at all* | absent |
| `reasoning_effort="none"` | `{"effort":"none"}` | absent |
| `reasoning_effort="medium"` | `{"effort":"medium"}` | absent |
| `reasoning_effort="high"` | `{"effort":"high"}` | absent |
| `supports_temperature=True` | `{"effort":"none"}` | `0.0` |

All five to one digest, and by construction rather than by coincidence: neither value was an
input, so the payload `request_identity` hashed was character-for-character the same for all of
them. *Reproduced 2026-08-19 by building the five bodies through
`StoryOpenAIResponsesProvider.request_body` and keying each under the v2 rule:* **5 distinct
request bodies, 1 distinct digest** (`ae13b6c9d9a5…`). That is §5.1's argument one level down — a row recorded at
`effort: none` would silently answer a request that would have gone out at `medium`, which is
the wrong replay this store exists to prevent — and it is not hypothetical: plan §10 records
`gpt-5.4`'s effort being changed *during* the live comparison. `config_hash` covers
`story_run_id` and **not** this key, so the run id was never the defence here.

`IDENTITY_VERSION` moved to `story-generation-v3` in the same change and the five committed
stores were **re-keyed, not re-recorded**, by the technique v2 used: every `raw_content` and
every `content_sha256` is byte-identical to what the servers said, and
`tests/story/test_story_demo.py` proves it against artifact hashes measured before the change.
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
IDENTITY_VERSION = "story-generation-v3"

#: How an **absent** `reasoning_effort` is spelled inside the digest's `reasoning_effort=` part.
#: A distinct, stable value and not a dropped part: dropping it would shift every part after it,
#: so "no reasoning block" would produce the digest of a *different* request that happened to
#: have one fewer input — the ambiguity the labelled-parts join below exists to remove.
#:
#: The other branch is a 64-character hex digest of the declared effort, so the two can never
#: collide **by construction** rather than by a promise about the vocabulary: no output of
#: `_text_digest` is six characters long. That matters because `REASONING_EFFORTS` is explicitly
#: a union across models that this repository expects to grow (`providers/public.py`), so a
#: sentinel that relied on "no model will ever call an effort `absent`" would be a rule with an
#: expiry date.
NO_REASONING_EFFORT = "absent"

#: "The caller said nothing", which is **not** the same as `None`. `reasoning_effort=None` is a
#: legal, digested value — it is what every local row carries — so a `None` default on the
#: replay provider would be a caller silently agreeing to the local server's settings, and an
#: OpenAI request would be keyed as a Qwen one. That is the collision `story-generation-v3`
#: closes, and a default is not the place to reopen it. A private class rather than a string,
#: because every string is a value some provider could legitimately declare — and rather than a
#: bare `object()`, because this value reaches the error message a caller has to act on and
#: `<object object at 0x7f…>` is not a sentence.
class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<not stated>"


UNSET: Any = _Unset()

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
#:
#: **`temperature_sent` and `reasoning_effort` were inserted after `temperature` on 2026-08-19**,
#: for `provider_id`'s reason and by `provider_id`'s licence: `story-generation-v3` re-keys every
#: row, so all five committed stores were rewritten in the same change and there was no byte
#: comparison left to break. They sit next to `temperature` because the three together are the
#: answer to "how was this request parameterised", and a row that states its key must state all
#: of it — a store whose rows did not say which effort produced them could not be replayed from
#: the file alone, which is exactly what `identity_provider_id` already argues.
ROW_FIELDS: tuple[str, ...] = (
    "request_sha256", "content_sha256", "provider_id", "model_id", "provider_model_id",
    "schema_name", "prompt_version", "temperature", "temperature_sent", "reasoning_effort",
    "max_tokens", "finish_reason", "raw_content",
)

#: The digest inputs a row restates, and the ones `GenerationStore.get` compares a hit against.
#: Deliberately a subset of `ROW_FIELDS`: `system`, `prompt` and `schema` are digest inputs that
#: the row does **not** carry — deliberately, since §21's byte-identity claim rests on the file
#: holding nothing volatile and a prompt is the largest thing a run could store — so they can be
#: checked only by the digest itself, while these seven can be checked twice.
STATED_IDENTITY_FIELDS: tuple[str, ...] = (
    "provider_id", "model_id", "schema_name", "temperature", "temperature_sent",
    "reasoning_effort", "max_tokens",
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


class MislabelledGenerationError(ValueError):
    """A row was found under a digest and states an identity that digest could not produce.

    **Not** a `LookupError` and deliberately not a subclass of `MissingGenerationError`: a
    caller that treated this as a miss would fall through to the server, and the one thing that
    must not happen when a store contradicts itself is a run that quietly carries on. The row is
    on disk, it was found, and it is wrong — which is a statement about the file, not about the
    lookup.

    *Added 2026-08-19.* Until then the row carried `provider_id`, `model_id`, `schema_name`,
    `temperature` and `max_tokens` "so the file states its own key" and **nothing compared the
    statement to the key**: an adversarial review rewrote every row of the committed OpenAI
    fixture to claim `local_openai_compatible`/`Qwen3.5-9B-Q4_K_M.gguf`, left `request_sha256`
    alone, and an `openai`/`gpt-5.4` lookup still returned a HIT. The digest is the defence
    against a *lookup* going to the wrong row; this is the defence against a *row* being the
    wrong row, and they are different failures.
    """

    def __init__(self, message: str, *, request_sha256: str = "",
                 differences: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.request_sha256 = request_sha256
        self.differences = differences


@dataclass(frozen=True)
class RequestIdentity:
    """A request digest, beside the part of the request a stored row restates.

    One object rather than a digest and a loose bundle of strings, because the check
    `GenerationStore.get` makes is only worth making if the values it compares came from the
    *same call* that produced the digest. A caller that computed the key from one set of strings
    and the comparison from another would be verifying a claim nobody made — and that is not a
    theoretical slip: `request_identity`'s own docstring already records `model_id` being the
    configured name while `provider_model_id` is the wire value, which is exactly the kind of
    near-miss a second, hand-assembled argument list attracts.

    `system`, `prompt` and `schema` are absent on purpose. They are digest inputs and a row does
    not carry them (§21 — the file holds nothing volatile and nothing large), so they are
    checked by the digest and by nothing else. See `STATED_IDENTITY_FIELDS`.
    """

    sha256: str
    provider_id: str
    model_id: str
    schema_name: str
    temperature: float
    temperature_sent: bool
    reasoning_effort: str | None
    max_tokens: int

    @classmethod
    def of(
        cls,
        *,
        system: str,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
        provider_id: str,
        model_id: str,
        temperature: float,
        temperature_sent: bool,
        reasoning_effort: str | None,
        max_tokens: int,
    ) -> "RequestIdentity":
        """The digest and the claim, from one argument list. See `request_identity`."""
        return cls(
            sha256=request_identity(
                system=system, prompt=prompt, schema=schema, schema_name=schema_name,
                provider_id=provider_id, model_id=model_id, temperature=temperature,
                temperature_sent=temperature_sent, reasoning_effort=reasoning_effort,
                max_tokens=max_tokens),
            provider_id=str(provider_id),
            model_id=str(model_id),
            schema_name=str(schema_name),
            temperature=float(temperature),
            temperature_sent=bool(temperature_sent),
            reasoning_effort=None if reasoning_effort is None else str(reasoning_effort),
            max_tokens=int(max_tokens),
        )

    def stated(self) -> dict[str, Any]:
        """What a row keyed under this identity must say about itself."""
        return {name: getattr(self, name) for name in STATED_IDENTITY_FIELDS}


def request_identity(
    *,
    system: str,
    prompt: str,
    schema: Mapping[str, Any],
    schema_name: str,
    provider_id: str,
    model_id: str,
    temperature: float,
    temperature_sent: bool,
    reasoning_effort: str | None,
    max_tokens: int,
) -> str:
    """The digest a generation is stored under. Ten inputs, and each earns its place.

    | input | why |
    | --- | --- |
    | `system` | three personas, one package — planner and verifier would share a row |
    | `prompt` | the request itself |
    | `schema` | the grammar the server was constrained by; a new schema is a new question |
    | `schema_name` | reaches the wire, and names the call site in a stored row |
    | `provider_id` | **2026-08-19**; which adapter was asked. Not implied by anything else — below |
    | `model_id` | the *configured* name, never the path the server reports |
    | `temperature` | pinned at 0.0 today, in the digest so it could never move unnoticed |
    | `temperature_sent` | **2026-08-19**; whether that pinned value reached the body at all |
    | `reasoning_effort` | **2026-08-19**; how much the model was told to think before answering |
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

    **What moved again, and why** *(2026-08-19, an adversarial review of the above)*.
    `temperature_sent` and `reasoning_effort` were the ninth and tenth inputs and
    `IDENTITY_VERSION` went to `story-generation-v3`. The paragraph above argues that a digest
    standing for two request shapes is a digest that means two things; S12 then added two config
    fields that change the request shape and left them out of it, so five bodies keyed to one
    digest — the module docstring has the reproduction. They are two inputs and not one because
    they are two independent facts: `supports_temperature` decides whether `temperature` is in
    the body at all, and `reasoning_effort` decides whether a `reasoning` block is and what is
    in it. A single "settings" digest over both would still be right, and would tell a reader
    who is looking at a re-key nothing about which of the two moved.

    `temperature_sent` is a *separate* part from `temperature` rather than a way of spelling it
    (an absent temperature as `NaN`, say). The pinned value is what the call site asked for and
    is true of the request in the sense §15.1 means; whether it reached the wire is a fact about
    the adapter. `provider_settings` in the manifest already keeps them apart for that reason,
    and a key that folded them together would be the one place in the system that did not.

    An absent `reasoning_effort` is `NO_REASONING_EFFORT` and never a dropped part — see that
    constant for why a shifting join is worse than a sentinel.

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
        f"temperature_sent={'true' if temperature_sent else 'false'}",
        "reasoning_effort=" + (NO_REASONING_EFFORT if reasoning_effort is None
                               else _text_digest(str(reasoning_effort))),
        f"max_tokens={int(max_tokens)}",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _first_stated(*candidates: Any) -> Any:
    """The first candidate that is not `UNSET`, or `UNSET`.

    An explicit walk rather than `or`, and that is the point: `False` and `None` are both legal
    values of the two settings this resolves, so `a or b or c` would skip past a caller that
    said `False` and land on a file that said `True`.
    """
    for candidate in candidates:
        if candidate is not UNSET:
            return candidate
    return UNSET


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
    #: Whether the pinned temperature reached the request body. A digest input since
    #: `story-generation-v3`, and stored beside `temperature` because the two genuinely differ:
    #: OpenAI's reasoning models refuse the parameter, so a row printing only `temperature: 0.0`
    #: would state a value the request never carried — the same argument the manifest's
    #: `provider_settings` block makes, kept in the one other place that has to make it.
    temperature_sent: bool
    #: `reasoning.effort` as it reached the body, or `None` for a request that carried no
    #: `reasoning` block at all. A digest input since `story-generation-v3`. JSON `null` is the
    #: file's spelling of absent and is unambiguous there; `NO_REASONING_EFFORT` is the digest's,
    #: which needs a sentinel for a reason that constant records.
    reasoning_effort: str | None
    max_tokens: int
    finish_reason: str
    raw_content: str

    @property
    def content(self) -> dict[str, Any]:
        parsed = json.loads(self.raw_content)
        return parsed if isinstance(parsed, dict) else {}

    def as_row(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ROW_FIELDS}

    def disagrees_with(self, identity: RequestIdentity) -> tuple[str, ...]:
        """Every field where this row's own statement contradicts the identity that found it.

        The comparison the docstrings above always implied and nothing ever made. Empty is the
        answer for a row that was written under this identity, and a name in it is a file that
        says one thing and is keyed as another.

        Compared as the row stores them — `float`, `bool`, `int`, `str | None` — rather than as
        text, so `0.0` and `"0.0"` cannot pass for agreement and a JSON `null` effort cannot
        pass for the string `"None"`.
        """
        stated = identity.stated()
        return tuple(name for name in STATED_IDENTITY_FIELDS
                     if getattr(self, name) != stated[name])

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredGeneration":
        return cls(**{name: row[name] for name in ROW_FIELDS})

    @classmethod
    def from_result(
        cls,
        result: GenerationResult,
        *,
        identity: RequestIdentity,
        prompt_version: str,
    ) -> "StoredGeneration":
        """The identity is passed whole, and it is the only source of the row's key fields.

        **This signature took seven scalars until 2026-08-19 and now takes one object**, for the
        reason `RequestIdentity` records: the caller is the only thing that knows what
        `request_sha256` was computed over, and every scalar it had to re-supply here was a
        chance to supply a different one. Reading them off `result` instead was never an option
        and is precisely the defect the old signature existed to prevent — `result.model_id` is
        the *wire* value and a `GenerationResult` names no provider, no effort and no
        temperature at all.

        `prompt_version` stays a separate argument because it is deliberately **not** a digest
        input: it labels a row for a reader, and folding it into the identity would re-key every
        stored generation the day a prompt's version string was bumped without its text moving.
        """
        return cls(
            request_sha256=identity.sha256,
            content_sha256=result.content_sha256,
            provider_id=identity.provider_id,
            model_id=identity.model_id,
            provider_model_id=result.model_id,
            schema_name=identity.schema_name,
            prompt_version=prompt_version,
            temperature=identity.temperature,
            temperature_sent=identity.temperature_sent,
            reasoning_effort=identity.reasoning_effort,
            max_tokens=identity.max_tokens,
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

    def get(self, identity: RequestIdentity) -> StoredGeneration | None:
        """The row this identity was keyed under, **checked against what the row says it is**.

        *Added 2026-08-19, and the whole method existed without it.* `StoredGeneration` carries
        `provider_id`, `model_id`, `schema_name`, `temperature` and `max_tokens` so that "the
        file states its own key", and nothing verified the statement: an adversarial review
        rewrote every row of the committed OpenAI fixture to claim the local provider and the
        Qwen model, left `request_sha256` untouched, and an `openai`/`gpt-5.4` lookup still
        returned a HIT and replayed the row. The digest guards the *lookup*; this guards the
        *row*, and a file that has been edited, merged or concatenated is the case where only
        the second one is left.

        A raise and not a miss, for `MislabelledGenerationError`'s reason: a miss falls through
        to a server or to `MissingGenerationError`, and both of those report "this row is not
        here" about a row that is here and is wrong.
        """
        row = self._rows.get(identity.sha256)
        if row is None:
            return None
        differences = row.disagrees_with(identity)
        if differences:
            stated = identity.stated()
            raise MislabelledGenerationError(
                f"the row stored under {identity.sha256} contradicts the identity that found "
                f"it: " + "; ".join(
                    f"{name} is {getattr(row, name)!r} on the row, {stated[name]!r} in the "
                    f"request" for name in differences)
                + ". A row states its own key so a store can be replayed from the file alone; "
                  "this file states one key and is filed under another",
                request_sha256=identity.sha256, differences=differences)
        return row

    def row(self, request_sha256: str) -> StoredGeneration | None:
        """The row at a digest, verifying nothing. For reading a file, never for replaying one.

        Separate from `get` rather than a flag on it, because the two have different callers and
        only one of them is allowed to skip the check: a tool or a test inspecting a store has
        no request in hand to check against, while a replay always does and must never be able
        to opt out by omitting an argument.
        """
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

    def identity_reasoning_effort(self) -> Any:
        """The effort every row was keyed under, or `UNSET` if the rows disagree or there are none.

        **`UNSET` and not `None`, which is where this one differs from its two neighbours**:
        `None` is a legal value here — it is what every local row carries — so the
        `None`-means-disagreement convention `identity_model_id` uses would read a store holding
        a `medium` row beside a `high` one as "no reasoning at all" and key every lookup wrong.
        The neighbours can use `None` because no row can carry a `None` provider or model.
        """
        efforts = {row.reasoning_effort for row in self._rows.values()}
        return efforts.pop() if len(efforts) == 1 else UNSET

    def identity_temperature_sent(self) -> Any:
        """Whether every row was keyed under a request that carried the temperature.

        `UNSET` on disagreement for `identity_reasoning_effort`'s reason — `False` is a value and
        not an absence, so it cannot double as "the rows do not agree".
        """
        sent = {row.temperature_sent for row in self._rows.values()}
        return sent.pop() if len(sent) == 1 else UNSET

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
        temperature_sent: Any = UNSET,
        reasoning_effort: Any = UNSET,
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
        inner_config = getattr(inner, "config", None)
        resolved_sent = _first_stated(
            temperature_sent,
            # The adapter's own capability, which is exactly the condition both adapters branch
            # on when deciding whether to put `temperature` in the body.
            UNSET if inner_config is None else getattr(
                inner_config, "supports_temperature", UNSET),
            store.identity_temperature_sent())
        resolved_effort = _first_stated(
            reasoning_effort,
            UNSET if inner_config is None else getattr(
                inner_config, "reasoning_effort", UNSET),
            store.identity_reasoning_effort())
        if resolved_sent is UNSET or resolved_effort is UNSET:
            # **This is what "a replay-only provider has no config" was decided to mean**
            # (2026-08-19). It does not mean "assume the local server's settings": since
            # `story-generation-v3` those two settings are in the key, and assuming them is
            # assuming a key. It means the rows have to say — which they now do, because
            # `ROW_FIELDS` carries both — and a store that cannot say, because it is empty or
            # because its rows disagree, cannot be replayed from at all. That is the same
            # sentence `identity_provider_id` already made true of the adapter, and it is what
            # keeps the committed fixtures replayable from the file alone with nothing passed in.
            raise ValueError(
                "a replay-only provider needs to know how the requests were parameterised: "
                f"since {IDENTITY_VERSION} the digest covers whether `temperature` reached the "
                "body and which `reasoning.effort` did, and with no server to ask and no rows "
                "agreeing on either, nothing states what the stored generations were keyed "
                "under. Assuming the local server's settings would key an OpenAI row as a Qwen "
                f"row (have temperature_sent={resolved_sent!r}, "
                f"reasoning_effort={resolved_effort!r})")
        self._store = store
        self._inner = inner
        # Falling back to the store's own rows is what makes a written file sufficient to
        # replay from. It is the last resort, not the first: an explicit id and a live server
        # both state the identity more authoritatively than a file does.
        self._model_id = resolved
        self._provider_id = resolved_provider
        self._temperature_sent = bool(resolved_sent)
        self._reasoning_effort = resolved_effort
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

        **It is not what the request key is built from, and since 2026-08-19 that distinction is
        load-bearing.** `__init__` reads `supports_temperature` and `reasoning_effort` off this
        same object when there is one, but resolves them once, at construction, and falls back
        to the rows when there is not — so a replay-only run has no config here and still keys
        its lookups correctly. A manifest may honestly say "this run sent nothing"; a lookup may
        never say "this run had no settings", because the recorded request had some.
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
        identity = RequestIdentity.of(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            provider_id=self._provider_id, model_id=self._model_id, temperature=temperature,
            temperature_sent=self._temperature_sent, reasoning_effort=self._reasoning_effort,
            max_tokens=max_tokens)
        stored = self._store.get(identity)
        if stored is not None:
            return _replayed(stored, identity.sha256)
        if self._inner is None:
            raise MissingGenerationError(
                f"no stored generation for request {identity.sha256} (schema "
                f"{schema_name!r}); this store holds {len(self._store)}",
                request_sha256=identity.sha256)
        result = self._inner.generate(
            system=system, prompt=prompt, schema=schema, schema_name=schema_name,
            max_tokens=max_tokens, temperature=temperature)
        self._store.put(StoredGeneration.from_result(
            result, identity=identity, prompt_version=self._prompt_version))
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
