"""The story provider boundary: configuration, errors, and the constants a call site pins.

Responsibility: everything about reaching a model server that is *not* a wire format. No
`httpx` here, deliberately — a stage that needs the error taxonomy or the pinned temperature
imports this module and pulls no HTTP client into `sys.modules` with it, which is the split
`extraction/providers/public.py` already makes and the reason
`extraction/providers/__init__.py` resolves its adapters lazily.

**Re-stated from `extraction/providers/public.py`, not imported** (plan §15.2).
`extraction.providers` and `extraction.contracts` are not surfaces this package may reach
(WORKSTREAM_BOUNDARY §4), and `story/providers/neo4j_connection.py` already settles the same
trade the same way for Bolt. What is duplicated is the six-class hierarchy, the retry
constants and the defaults of the validated local runtime; the duplication is knowing and is
named here so a reader does not take it for an accident.

**Three deliberate divergences from the extraction original**, each of them a defect in it
that §15.1 asks not to reproduce:

* **`temperature` is not a configuration field.** It is `PINNED_TEMPERATURE`, a constant a
  call site passes. It is a digest input to every stored generation (§14), so a value that
  could be edited in a YAML file would silently re-key the whole replay store.
* **`api_key` never comes from a tracked file.** It is read from the environment or from
  `.env` and from nowhere else, and a config mapping carrying one is refused rather than
  honoured. Masked by `SecretStr` so a traceback or a pytest assertion dump cannot print it.
* **`kind` is dispatched on.** `ProviderConfig.kind` exists upstream and is never read; here
  an unrecognised value is a typed raise at construction, the pattern
  `extraction/context.py:93-103` uses for `build_scope`.

The defaults below are constants, `from_config` reads the `provider:` block of a parsed
`config/story.yaml` — S11 minted that file — and `load_provider_config` layers `.env` and the
process environment over them. Nothing here opens a YAML file: the caller that parsed the
document passes the mapping in, so this module stays free of a file format as well as of a
transport.

**S12 added a second provider and no second concept.** `kind` was already dispatched on, so it
*is* the provider id (`provider_id` is a property returning it, not a parallel field), and the
OpenAI block is nested inside `provider:` rather than sitting beside it — one place to look for
"which server?", and every key of it is a `config_hash` input exactly as the local keys are.
Three fields join the config for the settings that differ between the two — `supports_temperature`,
`reasoning_effort`, `store_responses` — and `validated()` **refuses** the combinations that mean
nothing (a `reasoning_effort` on the local server, a local server that claims it cannot take the
pinned temperature) rather than ignoring them, which is the same argument the `kind` dispatch
already makes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from pydantic import SecretStr

#: `story/providers/public.py` -> `story/providers` -> `story` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

#: The two transports this package knows how to build. Not a free-text label: `from_config`
#: refuses anything else rather than falling through to a default.
#:
#: `PROVIDER_LOCAL` is an alias and not a second string — the kind *is* the provider id, and
#: minting `provider_id = "local"` beside `kind = "local_openai_compatible"` would put two
#: names on one concept in the digest that keys the replay store.
KIND_LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"
PROVIDER_LOCAL = KIND_LOCAL_OPENAI_COMPATIBLE
PROVIDER_OPENAI = "openai"
SUPPORTED_KINDS = frozenset({PROVIDER_LOCAL, PROVIDER_OPENAI})

#: What a person sees. Deliberately not the base URL: the catalogue below is rendered by the
#: demo UI, and §6 allows it a provider label and a model id and nothing else.
PROVIDER_LABELS = {
    PROVIDER_LOCAL: "Local llama.cpp server",
    PROVIDER_OPENAI: "OpenAI",
}

# The validated runtime, from LOCAL_RUNTIME_VALIDATED.md §1 as `extraction/providers/public.py`
# records it (measured 2026-08-01). Restated rather than imported; if the machine's server
# moves, both files move together or the two workstreams are talking to different models.
DEFAULT_KIND = KIND_LOCAL_OPENAI_COMPATIBLE
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MODEL = "Qwen3.5-9B-Q4_K_M.gguf"
DEFAULT_CONTEXT_TOKENS = 8192
DEFAULT_MAX_OUTPUT_TOKENS = 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 2

#: §15.3, and a constant rather than a field on purpose. Every story call site passes this;
#: `max_tokens` has no default anywhere in this package for the same reason — both are digest
#: inputs to `request_identity`, and a determinism input with a default is one a call site can
#: end up without, exactly as `ReadQueryExecutor.read`'s `timeout_seconds` argues for Cypher.
PINNED_TEMPERATURE = 0.0

#: Transient, and nothing else. Every other status the server can return is a statement about
#: the request, which repeating the identical request cannot change.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: A health probe that waits two minutes has stopped being a health probe (§15.1).
HEALTH_TIMEOUT_CEILING_SECONDS = 10.0

#: The environment names this package reads, and the only ones. `STORY_LLM_*` rather than
#: `NEO4J_*`'s bare style because the repository has no existing convention for a model server
#: — extraction reads none at all — and an unprefixed `LLM_BASE_URL` in a shared `.env` would
#: be ambiguous the day the extraction lane wants its own. `.env.example` is not edited here:
#: no key is required by the local server, and adding a blank row for one that nothing needs
#: would be a claim that a key exists.
ENV_BASE_URL = "STORY_LLM_BASE_URL"
ENV_MODEL = "STORY_LLM_MODEL"
ENV_API_KEY = "STORY_LLM_API_KEY"
ENV_TIMEOUT_SECONDS = "STORY_LLM_TIMEOUT_SECONDS"
ENV_MAX_RETRIES = "STORY_LLM_MAX_RETRIES"
ENV_MAX_OUTPUT_TOKENS = "STORY_LLM_MAX_OUTPUT_TOKENS"
ENV_CONTEXT_TOKENS = "STORY_LLM_CONTEXT_TOKENS"
ENV_NAMES = (ENV_BASE_URL, ENV_MODEL, ENV_API_KEY, ENV_TIMEOUT_SECONDS, ENV_MAX_RETRIES,
             ENV_MAX_OUTPUT_TOKENS, ENV_CONTEXT_TOKENS)

#: OpenAI's two, and they break the `STORY_LLM_*` convention on purpose. `OPENAI_API_KEY` is
#: the name the whole ecosystem already exports and the one the brief names; a story-prefixed
#: alias would mean an operator with a working key still has an unavailable provider. The model
#: override *is* prefixed, because `OPENAI_MODEL` is not an ecosystem name and an unprefixed one
#: in a shared `.env` would be ambiguous the day the extraction lane wants its own.
ENV_OPENAI_API_KEY = "OPENAI_API_KEY"
ENV_OPENAI_MODEL = "STORY_OPENAI_MODEL"
OPENAI_ENV_NAMES = (ENV_OPENAI_API_KEY, ENV_OPENAI_MODEL)

# The OpenAI defaults, from plan §4.4. Constants beside the config for the same reason the
# local ones are: a config file is a digest input a run may not have, and a package that could
# not build a provider without one would be a package no test can construct in three lines.
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5-nano"
DEFAULT_OPENAI_CONTEXT_TOKENS = 128000
DEFAULT_OPENAI_MAX_OUTPUT_TOKENS = 4096
DEFAULT_OPENAI_TIMEOUT_SECONDS = 180.0
DEFAULT_OPENAI_MAX_RETRIES = 2

#: The model list `config/story.yaml` names, restated so a caller with no config file resolves
#: the same two models. `supports_temperature` is **measured, not guessed** (plan §3, probed
#: 2026-08-19): `gpt-5-nano` answers `temperature: 0.0` with HTTP 400 `Unsupported parameter`,
#: `gpt-4.1-mini` accepts it. Sending it to find out would spend a 400 per run on a static fact.
DEFAULT_OPENAI_MODELS: tuple[Mapping[str, Any], ...] = (
    {"id": "gpt-5-nano", "supports_temperature": False, "reasoning_effort": "minimal"},
    {"id": "gpt-4.1-mini", "supports_temperature": True},
)

#: The values the Responses API accepts for `reasoning.effort`, as a **union across models**.
#: Refused rather than passed through, because a misspelt effort is a 400 at the far end of a
#: request that carries a whole evidence package.
#:
#: **The vocabulary is per model, and this set cannot express that** *(measured 2026-08-19,
#: while running the §10 comparison)*. `gpt-5-nano` takes `minimal`; `gpt-5.4` refuses it —
#: `Unsupported value: 'minimal' is not supported with the 'gpt-5.4' model. Supported values
#: are: 'none', 'low', 'medium', 'high', and 'xhigh'.` So this check catches a typo and nothing
#: more: the value that is actually right for a model is declared beside that model in
#: `config/story.yaml`, verified against the API, and a wrong-but-spelled-correctly effort is a
#: 400 that arrives as `StoryProviderResponseError` with the API's own sentence in it. Narrowing
#: this to one model's list would refuse a legal value for another, which is worse than the 400.
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})

#: §4.3's sentence, verbatim and in one place: the catalogue renders it and the provider raises
#: it, and two spellings of the same fact would be two things to keep in step.
OPENAI_KEY_MISSING_REASON = (
    f"{ENV_OPENAI_API_KEY} is not set in the environment or in the repository-root "
    f"{DEFAULT_ENV_PATH.name}")

#: What a browser is told about a configuration that did not resolve, and **the only shape**
#: `_provider_option` may put in `unavailable_reason`.
#:
#: **Found by an adversarial review 2026-08-19, and it is a boundary defect rather than a
#: message defect.** The catalogue used to render `str(exc)`, so `STORY_OPENAI_MODEL=
#: internal-codename-orion-7` came back to the browser as *"provider.openai model
#: 'internal-codename-orion-7' is not one of […]"* and `STORY_LLM_CONTEXT_TOKENS=many` as
#: *"must be an integer, got 'many'"* — an environment variable's **value** in the option label
#: and in `#provider-notice`, against four docstrings, plan §6 and the sentence at
#: `index.html:85` that two tests assert.
#:
#: The exception is right to name the value: an operator debugging a configuration needs it,
#: and `load_provider_config` still raises it whole to the CLI and to a test. What is fixed is
#: that the string never crosses into a response — the reason below is built from
#: `StoryProviderConfigurationError.setting`, which is a literal written in this file's source
#: and can therefore hold nothing an operator did not publish.
#:
#: The setting name comes **first** because `app.js` renders this whole sentence as the
#: `<option>` label as well as in `#provider-notice`, and an option is read at a glance.
UNUSABLE_CONFIG_REASON = (
    "{setting} is not usable as configured. Its value is not shown here — a configuration value "
    "can itself be private, and this sentence reaches a browser. The full message does name it "
    "and is raised where the configuration is read.")

#: The fallback `setting` for a refusal that declared none. Value-free by construction, which
#: is the property that matters: a raise site added later without a `setting=` degrades to a
#: vaguer sentence, never to a leaking one.
UNNAMED_SETTING = "the provider configuration"

# -- what a response body may weigh ------------------------------------------------------------
#
#: A response is bounded by what the request asked for, and `max_output_tokens` is the anchor:
#: it is the only number in the request that says how much text was invited. 64 bytes per
#: requested token is deliberately loose — a JSON token is 3–8 UTF-8 bytes and `\uXXXX`
#: escaping costs at most 6 per character — so the bound refuses a body that is *categorically*
#: wrong (an 8 MB `output_text`, which an adversarial review posted through both adapters
#: unchallenged on 2026-08-19) rather than one that merely ran long.
#:
#: **Pre-existing, and S12 is what makes it matter.** Before a second provider the only endpoint
#: was a process on loopback; a remote endpoint over TLS is a different threat model, and an
#: unbounded body lands whole in `generations.jsonl` and in a run's artifacts.
RESPONSE_BYTES_PER_OUTPUT_TOKEN = 64
#: A floor, so a small `max_output_tokens` cannot make the ceiling smaller than a legitimate
#: envelope: the response carries the whole `usage` block, an id, a status and a dated model
#: name beside the text.
MIN_RESPONSE_BYTE_CEILING = 64 * 1024


def response_byte_ceiling(max_output_tokens: int) -> int:
    """How many bytes of response body the two adapters accept for one request.

    A function of the request rather than a constant, because the two providers configure
    `max_output_tokens` an order of magnitude apart (2048 locally, 4096 for OpenAI) and a single
    number would be either slack for one or a bound the other trips on legitimately.
    """
    return max(MIN_RESPONSE_BYTE_CEILING, int(max_output_tokens) * RESPONSE_BYTES_PER_OUTPUT_TOKEN)


class StoryProviderError(RuntimeError):
    """Base of every failure this boundary is allowed to raise.

    Story's own hierarchy rather than `extraction.providers`', mirroring the argument
    `StoryGraphError` already makes for Bolt: a caller catching a story fault must not also
    catch the extraction lane's, and the two packages fail at different boundaries.
    """


class StoryProviderConfigurationError(StoryProviderError):
    """The request could not be built at all — configuration, or a schema the runtime cannot
    enforce.

    Raised where a request is *constructed*, never as a result of one. It sits on neither end
    of the retry axis: it is not a transport fault and it is not the model's answer, because
    nothing was ever sent.

    Carrying the §15.3 schema refusal here rather than in a seventh class keeps the hierarchy
    at six and keeps the axis readable: a schema using `pattern` is unusable configuration in
    exactly the way `enable_thinking: true` is, and failing at build time is the whole point
    (llama.cpp drops the keyword silently and the local checker ignores it, so the alternative
    is an unconstrained request that says nothing).

    **`setting` names the key or the environment variable that is wrong, and nothing else**
    *(added 2026-08-19, after a review found the message itself in a browser)*. The message is
    allowed to quote the offending value — an operator debugging a configuration needs it, and
    every raise below that can name one does. The *catalogue* is not: it renders
    `UNUSABLE_CONFIG_REASON` over this field, which is a literal from this file's own source.
    Two fields rather than one carefully-worded message, because a message that had to be safe
    for a browser would be a message that could not help an operator, and both readers exist.
    """

    def __init__(self, message: str, *, setting: str | None = None) -> None:
        super().__init__(message)
        self.setting = setting


class StoryProviderUnavailable(StoryProviderError):
    """Nothing is listening, or the server refused the connection."""


class StoryProviderTimeout(StoryProviderError):
    """The request exceeded its per-request budget. **Never retried** — see the transport."""


class StoryProviderTransportError(StoryProviderError):
    """A transport-level fault that survived every permitted attempt."""


class StoryProviderResponseError(StoryProviderError):
    """A response arrived and could not be understood.

    Covers a non-retryable HTTP status, a missing choice, blank content, content that is not
    JSON and content that is not an object. **Never a silent `{}`**: an empty object is a
    legitimate model answer for some schemas, so conflating the two would make an unparseable
    response indistinguishable from a real one.
    """


class StoryProviderSchemaError(StoryProviderError):
    """Valid JSON that does not satisfy the requested schema.

    A model result, not a fault, and **deliberately not retried**: the server was asked for
    schema-constrained output and answered, and re-asking at temperature 0 returns the same
    thing while charging for it twice.

    **`generation` is the `GenerationResult` the raiser was judging, or `None`** *(added
    2026-08-19)*. Two call sites raise this class and only one of them holds a result: an
    *adapter* raises it while translating a response, before a `GenerationResult` has been
    constructed at all, so there is nothing to attach and the field stays `None`; a *stage*
    (`plan_story`, `write_story`) raises it about a result it already has, and attaches it so
    the run can record a call that was made and paid for. Typed `Any` rather than imported,
    because this module is defined by importing no model and no transport.
    """

    def __init__(self, message: str, violations: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.violations = violations
        self.generation: Any = None


@dataclass(frozen=True)
class StoryProviderConfig:
    """Which server, which model, and what the transport is allowed to do about a fault.

    Deliberately **not** carrying `temperature` (see `PINNED_TEMPERATURE`) and deliberately
    not carrying `enable_thinking`: `LOCAL_RUNTIME_VALIDATED.md` §4 measured a four-field
    schema request spending its entire budget reasoning and returning empty content, so the
    extraction config holds a field whose only legal value is `False`. Here the flag is
    unconditional on the wire and there is no field at all — one fewer thing that can be set
    wrong, and the same guarantee.
    """

    kind: str = DEFAULT_KIND
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    #: Environment-only, masked, and absent for the local server. `repr=False` on top of
    #: `SecretStr` because a frozen dataclass prints its fields in every assertion dump.
    api_key: SecretStr | None = field(default=None, repr=False)

    # -- S12: what differs between the two providers, with local-safe defaults ---------------
    #
    #: Whether `temperature` may be sent at all. A *capability of the model*, not a preference:
    #: `gpt-5-nano` answers a `temperature` with HTTP 400 (plan §3). `PINNED_TEMPERATURE` is
    #: unchanged and still a constant — this field decides whether it reaches the wire, and the
    #: adapter records in `metadata` that it did not, so the manifest cannot claim a value that
    #: was never sent.
    supports_temperature: bool = True
    #: `reasoning.effort` for a reasoning model, `None` for every other. Refused on the local
    #: kind rather than ignored.
    reasoning_effort: str | None = None
    #: Whether the provider may retain the request. **False everywhere**, and a field rather
    #: than a literal so the manifest can state it: a story request carries the whole evidence
    #: package, and OpenAI's default is to keep it.
    store_responses: bool = False

    @property
    def provider_id(self) -> str:
        """The kind, under the name the rest of the system calls it by.

        A property and not an eighth field: two spellings of one value in a frozen dataclass is
        two things that can disagree, and this one is a digest input to every stored generation.
        """
        return self.kind

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> "StoryProviderConfig":
        """The `provider:` block of a parsed `config/story.yaml`, dispatched on the provider id.

        A secret in that mapping is **refused**, not read — at the top level and inside the
        nested `openai:` block alike: the file is tracked, and a configuration that can hold a
        key is one that will eventually hold a committed key.

        Both new arguments are keyword-only and default to `None`, so the call this method has
        always taken — one positional mapping — resolves the local server exactly as before.
        `provider_id` omitted means `provider.default`, then `provider.kind`, then the local
        constant; that chain is what keeps the CLI, the demo API and every existing test on the
        local path with no edit.
        """
        provider = _provider_block(config)
        resolved = provider_id or default_provider_id(config)
        if resolved == PROVIDER_OPENAI:
            return cls._from_openai_block(provider, model_id)
        loaded = cls(
            # `resolved`, not `provider["kind"]`: with no argument the two are the same value
            # by construction, and with one the caller's choice is the one `validated()` must
            # judge — otherwise an unsupported id would be refused as a typo in the file.
            kind=resolved,
            base_url=str(provider.get("base_url", DEFAULT_BASE_URL)).rstrip("/"),
            model=str(provider.get("model", DEFAULT_MODEL)),
            context_tokens=_as_int(provider.get("context_tokens"), DEFAULT_CONTEXT_TOKENS,
                                   "provider.context_tokens"),
            max_output_tokens=_as_int(provider.get("max_output_tokens"),
                                      DEFAULT_MAX_OUTPUT_TOKENS,
                                      "provider.max_output_tokens"),
            timeout_seconds=_as_float(provider.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS,
                                      "provider.timeout_seconds"),
            max_retries=_as_int(provider.get("max_retries"), DEFAULT_MAX_RETRIES,
                                "provider.max_retries"),
            # **Read, so that `validated()`'s local refusals can fire from a file** *(found by
            # an adversarial review 2026-08-19)*. This branch used to construct the three S12
            # fields from their dataclass defaults, so `provider.reasoning_effort: high` in
            # `config/story.yaml` was **silently dropped** and the refusal below it could only
            # be reached from a hand-built dataclass — the exact "looks configured and is not"
            # failure the comment beside that refusal says it exists to prevent, reproduced by
            # the code meant to prevent it.
            supports_temperature=_as_bool(provider.get("supports_temperature"), True,
                                          "provider.supports_temperature"),
            reasoning_effort=_as_optional_str(provider.get("reasoning_effort")),
            store_responses=_as_bool(provider.get("store_responses"), False,
                                     "provider.store_responses"),
        )
        return loaded.validated()

    @classmethod
    def _from_openai_block(
        cls, provider: Mapping[str, Any], model_id: str | None
    ) -> "StoryProviderConfig":
        """`provider.openai:`, with the chosen model's declared capabilities folded in.

        The model is looked up in the configured list rather than passed through, because
        `supports_temperature` is the whole reason the list exists: a model nobody declared has
        no measured answer to "may this request carry a temperature?", and guessing is how a run
        discovers a static fact through a 400 (§9's last rejected option).
        """
        block = provider.get("openai") or {}
        if not isinstance(block, Mapping):
            raise StoryProviderConfigurationError(
                f"provider.openai: must be a mapping, got {type(block).__name__}",
                setting="provider.openai")
        _refuse_secrets(block, "provider.openai")

        models = openai_model_options(provider)
        name = str(model_id or block.get("default_model", DEFAULT_OPENAI_MODEL))
        chosen = next((option for option in models if option.model_id == name), None)
        if chosen is None:
            raise StoryProviderConfigurationError(
                f"provider.openai model {name!r} is not one of "
                f"{[option.model_id for option in models]}; a model reaches the wire only if "
                "its `supports_temperature` was measured against the API, and an undeclared "
                "one would have to be guessed at",
                # The message names the model and the catalogue does not: the name can arrive
                # from `STORY_OPENAI_MODEL`, and an environment variable's value is the one
                # thing §6 forbids the payload to carry.
                setting="provider.openai.models")
        return cls(
            kind=PROVIDER_OPENAI,
            base_url=str(block.get("base_url", DEFAULT_OPENAI_BASE_URL)).rstrip("/"),
            model=name,
            context_tokens=_as_int(block.get("context_tokens"), DEFAULT_OPENAI_CONTEXT_TOKENS,
                                   "provider.openai.context_tokens"),
            max_output_tokens=_as_int(block.get("max_output_tokens"),
                                      DEFAULT_OPENAI_MAX_OUTPUT_TOKENS,
                                      "provider.openai.max_output_tokens"),
            timeout_seconds=_as_float(block.get("timeout_seconds"),
                                      DEFAULT_OPENAI_TIMEOUT_SECONDS,
                                      "provider.openai.timeout_seconds"),
            max_retries=_as_int(block.get("max_retries"), DEFAULT_OPENAI_MAX_RETRIES,
                                "provider.openai.max_retries"),
            supports_temperature=chosen.supports_temperature,
            reasoning_effort=chosen.reasoning_effort,
            # Not read from the block, and there is no key that could turn it on: a story
            # request carries the whole evidence package, and OpenAI's default is to retain it.
            store_responses=False,
        ).validated()

    def validated(self) -> "StoryProviderConfig":
        """Reject a configuration that cannot produce constrained output. Idempotent.

        **This is not called from `__init__`, and this docstring said it was** *(corrected
        2026-08-19, checked by constructing `StoryProviderConfig(kind="nonsense")` — it
        succeeds)*. `StoryProviderConfig` is a plain frozen dataclass with no `__post_init__`.
        What actually covers a hand-built config is that **both adapters call `validated()` in
        their own `__init__`**, alongside `from_config` and `load_provider_config` — so nothing
        reaches a wire unvalidated, which is the guarantee the sentence was reaching for. The
        difference is only *when* an invalid dataclass is refused: at the adapter rather than at
        the dataclass. Left as it is here rather than moved into `__post_init__`, which is a
        change to every construction path and belongs to whoever needs it, not to a docstring
        correction.

        **The API key is judged here as well** — see `_refuse_unusable_api_key`. It is a
        configuration check and not a transport one, and putting it anywhere else was measured
        to be too late.
        """
        if self.kind not in SUPPORTED_KINDS:
            raise StoryProviderConfigurationError(
                f"provider.kind {self.kind!r} is not one of {sorted(SUPPORTED_KINDS)}; the "
                "story layer dispatches on this value rather than defaulting, because a typo "
                "that silently selected the local server would be found by reading the "
                "generations rather than by reading the error",
                setting="provider.kind")
        if not self.base_url:
            raise StoryProviderConfigurationError("provider.base_url is required",
                                                  setting="provider.base_url")
        if not self.model:
            raise StoryProviderConfigurationError(
                "provider.model is required: it is the identity every stored generation is "
                "keyed under, and the server reports a filesystem path rather than a name",
                setting="provider.model")
        if self.max_retries < 0:
            raise StoryProviderConfigurationError("provider.max_retries must not be negative",
                                                  setting="provider.max_retries")
        if self.max_output_tokens <= 0:
            raise StoryProviderConfigurationError("provider.max_output_tokens must be positive",
                                                  setting="provider.max_output_tokens")
        if self.timeout_seconds <= 0:
            raise StoryProviderConfigurationError("provider.timeout_seconds must be positive",
                                                  setting="provider.timeout_seconds")
        if self.max_output_tokens >= self.context_tokens:
            raise StoryProviderConfigurationError(
                f"provider.max_output_tokens ({self.max_output_tokens}) leaves no room for a "
                f"prompt in provider.context_tokens ({self.context_tokens})",
                setting="provider.max_output_tokens")
        _refuse_unusable_api_key(self.api_key, self.kind)

        # Dispatch on the kind rather than tolerate a setting that means nothing to it. A
        # `reasoning_effort` the local transport silently drops is the same class of defect as
        # a schema keyword llama.cpp silently drops — it looks configured and is not.
        if self.kind == PROVIDER_LOCAL:
            if self.reasoning_effort is not None:
                raise StoryProviderConfigurationError(
                    f"provider.reasoning_effort {self.reasoning_effort!r} means nothing to "
                    f"{PROVIDER_LOCAL}: the local server has no such parameter, so the value "
                    "would be recorded in the manifest as a setting that never reached a wire",
                    setting="provider.reasoning_effort")
            if not self.supports_temperature:
                raise StoryProviderConfigurationError(
                    f"{PROVIDER_LOCAL} always accepts a temperature, and PINNED_TEMPERATURE is "
                    "a digest input to every stored generation; declaring it unsupported would "
                    "drop that input from the request while leaving it in the key",
                    setting="provider.supports_temperature")
            if self.store_responses:
                raise StoryProviderConfigurationError(
                    f"provider.store_responses means nothing to {PROVIDER_LOCAL}: nothing is "
                    "retained beyond the process that answered",
                    setting="provider.store_responses")
        elif self.reasoning_effort is not None and self.reasoning_effort not in REASONING_EFFORTS:
            raise StoryProviderConfigurationError(
                f"provider.reasoning_effort {self.reasoning_effort!r} is not one of "
                f"{sorted(REASONING_EFFORTS)}; a misspelt effort is a 400 at the far end of a "
                "request carrying a whole evidence package",
                setting="provider.reasoning_effort")
        return self

    @property
    def authorization_headers(self) -> dict[str, str]:
        """`Authorization` when a key was supplied, and an empty dict otherwise.

        Built at the last possible moment, the way `StoryNeo4jSettings.auth` builds its pair,
        so the secret exists as a plain string for the length of one request.

        Nothing is checked here, and that is the point: `validated()` has already refused a key
        that cannot be a header, so this property cannot be the place a malformed one is
        discovered. It used to be — see `_refuse_unusable_api_key`.
        """
        if self.api_key is None or not self.api_key.get_secret_value():
            return {}
        return {"Authorization": f"Bearer {self.api_key.get_secret_value()}"}


def _refuse_unusable_api_key(api_key: SecretStr | None, kind: str) -> None:
    """Refuse a key that cannot become an `Authorization` header, before one is ever built.

    **Two reproduced disclosures, one cause** *(adversarial review, 2026-08-19)*. `httpx`
    encodes a header value as ASCII, so a key holding any non-ASCII character —
    `kľúč-SECRET-9999`, an em dash, a trailing U+00A0 — raises `UnicodeEncodeError` from inside
    `client.post`/`client.get`. That is neither `httpx.TimeoutException` nor `httpx.HTTPError`,
    so it escaped `generate()`'s six classes entirely and made `health()` raise against its
    "never raises" docstring — and `UnicodeEncodeError.args[1]` **is the whole key**, so any
    `repr()` of it discloses the credential. A key holding a control character got further:
    h11 refuses it with the *bytes repr* of the header, `b'Bearer GLORP_TOPSECRET_2026\\nX: 1'`,
    which escapes the newline — so `_redacted`'s exact-substring half missed it and only the
    `sk-`-shaped prefix of an `sk-` key was caught. A non-`sk-` key came out in full, inside a
    typed error message and inside `health().detail`.

    **Malformed means: anything outside printable ASCII, U+0021–U+007E.** That is the wire's
    own rule stated positively rather than a taste — RFC 9110 field values admit visible ASCII,
    and a bearer token has no legitimate use for a space, a tab, a control character or a
    non-ASCII letter. Refusing the *whole class* is why it closes both findings at once: a
    permissive check that only rejected `\\n` and `\\r` would still hand `httpx` an
    unencodable key, and a check that only rejected non-ASCII would still hand h11 a newline.

    Refused as a configuration error rather than sanitised, because a key with a stray
    character is a key that will not authenticate: silently trimming it would turn a typo in
    `.env` into a 401 nobody can explain.

    **The message names no part of the key.** The position is enough to find the character in
    an editor, and the value of a credential is the one thing no message may quote — which is
    the whole difference between this and the other refusals in this file.
    """
    if api_key is None:
        return
    key = api_key.get_secret_value()
    for index, char in enumerate(key):
        if "\x21" <= char <= "\x7e":
            continue
        env_name = ENV_OPENAI_API_KEY if kind == PROVIDER_OPENAI else ENV_API_KEY
        raise StoryProviderConfigurationError(
            f"{env_name} holds a character no HTTP header can carry, at position {index + 1}: "
            "only printable ASCII (U+0021–U+007E) reaches an Authorization header, and "
            "anything else raises out of the transport carrying the key in its own arguments. "
            "The character is not quoted here and neither is the key. Check for a smart quote, "
            "a non-breaking space or a line break pasted into the value.",
            setting=env_name)


def read_env_file(path: Path) -> dict[str, str]:
    """The `KEY=VALUE` lines of the repository-root `.env`. Missing file -> `{}`.

    Four lines, re-stated from `story/providers/neo4j_connection.py:211` rather than imported
    from it — importing would put `neo4j` into the closure of every module that wanted a
    config, which is precisely what `tests/story/test_story_package_structure.py` forbids. The
    parse must stay identical to that one and to the loader's: Compose strips `export `,
    surrounding quotes and stray whitespace where a plain `split("=")` does not, and the
    discrepancy surfaces only as an opaque auth failure.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def load_provider_config(
    config: Mapping[str, Any] | None = None,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Mapping[str, str] | None = None,
) -> StoryProviderConfig:
    """Constants, then a `provider:` block, then `.env`, then the process environment.

    Later wins — the order `load_neo4j_settings` uses, for the same reason: a machine that
    exports a variable means it, and a story run must resolve the same server the operator
    thinks it is talking to.

    An environment variable set to the empty string is an override *to empty*, not an absence,
    so `STORY_LLM_BASE_URL=` fails `validated()` rather than quietly restoring the default.

    **`provider_id` and `model_id` are keyword-only and default to `None`, and that is a hard
    compatibility requirement rather than a style choice.** Called as it has always been called
    — one positional mapping — this resolves `provider.default` (the local server) and returns
    what it returned before S12, so the CLI, the demo API and every existing test keep their
    behaviour with no edit.

    An explicit `model_id` outranks `STORY_OPENAI_MODEL`, which outranks the file. That inverts
    the "machine wins" order above on purpose: the environment is an operator's default for a
    machine, while the argument is a selection the server itself offered from
    `provider_catalogue` and a person then made.
    """
    layered: dict[str, str] = {}
    for source in (read_env_file(env_path),
                   dict(environ if environ is not None else os.environ)):
        for name in ENV_NAMES + OPENAI_ENV_NAMES:
            if name in source:
                layered[name] = source[name]

    resolved = provider_id or default_provider_id(config or {})
    if resolved == PROVIDER_OPENAI:
        base = StoryProviderConfig.from_config(
            config or {}, provider_id=PROVIDER_OPENAI,
            model_id=model_id or layered.get(ENV_OPENAI_MODEL))
        # Blank is absent, not an override to empty: §4.3 makes availability turn on a
        # *non-empty* key, and an exported-but-empty `OPENAI_API_KEY` is the shape a shell
        # profile leaves behind, not a credential anyone meant to supply.
        key = (layered.get(ENV_OPENAI_API_KEY) or "").strip()
        return replace(base, api_key=SecretStr(key) if key else None).validated()

    base = StoryProviderConfig.from_config(config or {}, provider_id=resolved)
    key = layered.get(ENV_API_KEY)
    return StoryProviderConfig(
        kind=base.kind,
        base_url=layered.get(ENV_BASE_URL, base.base_url).rstrip("/"),
        model=layered.get(ENV_MODEL, base.model),
        context_tokens=_as_int(layered.get(ENV_CONTEXT_TOKENS), base.context_tokens,
                               ENV_CONTEXT_TOKENS),
        max_output_tokens=_as_int(layered.get(ENV_MAX_OUTPUT_TOKENS), base.max_output_tokens,
                                  ENV_MAX_OUTPUT_TOKENS),
        timeout_seconds=_as_float(layered.get(ENV_TIMEOUT_SECONDS), base.timeout_seconds,
                                  ENV_TIMEOUT_SECONDS),
        max_retries=_as_int(layered.get(ENV_MAX_RETRIES), base.max_retries, ENV_MAX_RETRIES),
        api_key=None if key is None else SecretStr(key),
    ).validated()


# -- the registry the demo UI renders ----------------------------------------------------------


@dataclass(frozen=True)
class ModelOption:
    """One model a person may pick, and the two facts that change how it is called.

    `supports_temperature` is measured against the API rather than inferred from a name (plan
    §3): `gpt-5-nano` refuses `temperature` with a 400 and `gpt-4.1-mini` accepts it, and there
    is nothing in either string that says so.
    """

    model_id: str
    label: str
    supports_temperature: bool
    reasoning_effort: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "label": self.label,
            "supports_temperature": self.supports_temperature,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass(frozen=True)
class ProviderOption:
    """What a browser is allowed to know about a provider (§6).

    **No base URL, no key, no environment value, no timeout.** `as_dict` is the payload and it
    is written out field by field rather than derived from the dataclass, which is the rule
    `ComposedPrompts.as_dict` already follows: a field added here must be added there
    deliberately, so a URL cannot arrive in a response by being added to a config object.

    `unavailable_reason` names an environment *variable* and never its value — "OpenAI is
    missing" and "OpenAI does not exist" are different facts and the interface has to be able
    to tell them apart.

    **That sentence was false for one class of reason until 2026-08-19**, and this docstring
    was one of four asserting it. The missing-key path always obeyed it (`OPENAI_KEY_MISSING_
    REASON` is a constant), but a *configuration* failure was rendered as `str(exc)`, and those
    messages quote the value they refused — so `STORY_OPENAI_MODEL=internal-codename-orion-7`
    reached the browser verbatim. `_provider_option` now builds every such reason from
    `UNUSABLE_CONFIG_REASON` and a setting name, so the rule holds on both paths rather than on
    the one that happened to use a constant.
    """

    provider_id: str
    label: str
    available: bool
    unavailable_reason: str
    models: tuple[ModelOption, ...]
    default_model_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "label": self.label,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "models": [model.as_dict() for model in self.models],
            "default_model_id": self.default_model_id,
        }


def provider_catalogue(
    config: Mapping[str, Any] | None = None,
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Mapping[str, str] | None = None,
) -> tuple[ProviderOption, ...]:
    """Every provider this build knows, whether or not it can be used right now.

    **Never raises**, which is the same argument `health()` makes: an unconfigured provider is
    an ordinary state for an interface to render, and a catalogue that threw would leave the
    page with nothing to say about the provider that *is* configured. A configuration error is
    reported as an unavailable option carrying its own message, not as an exception.

    The local server is reported available whenever its configuration resolves. Whether the
    process is actually listening is `health()`'s question and needs an HTTP client, which this
    module is defined by not importing.
    """
    return tuple(
        _provider_option(provider_id, config, env_path=env_path, environ=environ)
        for provider_id in (PROVIDER_LOCAL, PROVIDER_OPENAI)
    )


def default_provider_id(config: Mapping[str, Any] | None = None) -> str:
    """`provider.default`, then `provider.kind`, then the local constant.

    Two keys and not one, because they answer different questions: `kind` describes the block
    it sits in — the local server — while `default` says which provider a call site that made
    no choice gets. Falling back to `kind` is what makes a pre-S12 config file resolve exactly
    as it did.

    **A non-string is refused rather than stringified** *(found by an adversarial review
    2026-08-19)*. This used to `str()` whatever the key held, so `default: {a: 1}` reached
    `GET /demo/providers` as `default_provider_id: "{'a': 1}"` — nonsense in a payload, and
    nonsense that fails safe only by accident downstream. The rest of this module dispatches on
    `kind` rather than defaulting for exactly this reason; the same rule applies to the key that
    chooses which `kind` is reached for.
    """
    provider = _provider_block(config or {})
    for key in ("default", "kind"):
        value = provider.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise StoryProviderConfigurationError(
                f"provider.{key} must be a provider id, got {type(value).__name__} "
                f"{value!r}; it names one of {sorted(SUPPORTED_KINDS)}",
                setting=f"provider.{key}")
        return value
    return DEFAULT_KIND


def openai_model_options(provider: Mapping[str, Any]) -> tuple[ModelOption, ...]:
    """`provider.openai.models`, or the measured defaults when the block names **no key at all**.

    Three cases, dispatched on rather than collapsed into one fallback *(corrected 2026-08-19,
    after a review reproduced the collapse)*:

    * **absent** — no `models:` key — is the only one that reaches `DEFAULT_OPENAI_MODELS`. A
      caller with no config file at all must resolve the same models `config/story.yaml` names,
      which is why the constant exists.
    * **an empty list** is a statement, and it is honoured. `models: []` used to hand back
      `['gpt-5-nano', 'gpt-4.1-mini']`, so an operator who emptied the list to take OpenAI out
      of a build got two models back and no indication that anything had been ignored. An empty
      tuple here makes every model selection fail to resolve, which is what the catalogue
      renders as an unavailable provider — the operator's own intent, arrived at honestly.
    * **anything else** — `models: "x"`, a mapping, a number — is a mistake in the file and is
      refused. Falling through to the defaults meant a typo produced a working configuration
      that was not the one written down, which is the failure mode this module's `kind` dispatch
      already argues against.
    """
    block = provider.get("openai") or {}
    declared = block.get("models") if isinstance(block, Mapping) else None
    if declared is None:
        rows: Any = DEFAULT_OPENAI_MODELS
    elif isinstance(declared, (list, tuple)):
        rows = declared
    else:
        raise StoryProviderConfigurationError(
            f"provider.openai.models must be a list, got {type(declared).__name__}; an "
            "unreadable list used to fall through to the built-in defaults, so a typo produced "
            "a configuration that worked and was not the one written down",
            setting="provider.openai.models")

    options: list[ModelOption] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise StoryProviderConfigurationError(
                f"provider.openai.models[{index}] must be a mapping, got {type(row).__name__}",
                setting="provider.openai.models")
        model_id = str(row.get("id") or "")
        if not model_id:
            raise StoryProviderConfigurationError(
                f"provider.openai.models[{index}] must declare an id",
                setting="provider.openai.models")
        effort = row.get("reasoning_effort")
        options.append(ModelOption(
            model_id=model_id,
            label=str(row.get("label") or model_id),
            supports_temperature=bool(row.get("supports_temperature", True)),
            reasoning_effort=None if effort is None else str(effort),
        ))
    return tuple(options)


def _provider_option(
    provider_id: str,
    config: Mapping[str, Any] | None,
    *,
    env_path: Path,
    environ: Mapping[str, str] | None,
) -> ProviderOption:
    label = PROVIDER_LABELS.get(provider_id, provider_id)
    try:
        resolved = load_provider_config(
            config, provider_id=provider_id, env_path=env_path, environ=environ)
    except StoryProviderConfigurationError as exc:
        # `exc.setting`, never `str(exc)`. The message is allowed to quote the value that is
        # wrong and every raise in this module that can name one does; this is the one place
        # where the string crosses into something a browser renders, and `UNUSABLE_CONFIG_REASON`
        # records what a review found when it did not.
        return ProviderOption(provider_id, label, False, _unusable_reason(exc), (), "")

    if provider_id == PROVIDER_OPENAI:
        try:
            models = openai_model_options(_provider_block(config or {}))
        except StoryProviderConfigurationError as exc:
            # Unreachable while `load_provider_config` above reads the same list, and here
            # regardless: `provider_catalogue` promises never to raise, and a promise that
            # depends on two functions agreeing about what they read is not one.
            return ProviderOption(provider_id, label, False, _unusable_reason(exc), (), "")
        has_key = resolved.api_key is not None and bool(resolved.api_key.get_secret_value())
        reason = "" if has_key else OPENAI_KEY_MISSING_REASON
        return ProviderOption(provider_id, label, has_key, reason, models, resolved.model)

    # One model, and it is the one a run would use — the file's value with the environment
    # layered over it, not the file's value alone.
    model = ModelOption(resolved.model, resolved.model, resolved.supports_temperature,
                        resolved.reasoning_effort)
    return ProviderOption(provider_id, label, True, "", (model,), resolved.model)


def _unusable_reason(exc: StoryProviderConfigurationError) -> str:
    """The one sentence a refused configuration is allowed to become in a payload.

    `getattr` rather than an attribute read, because `StoryProviderConfigurationError` is raised
    from three other modules — the two adapters and `portable_schema` — and one of them
    constructing it positionally must not turn a rendered catalogue into an `AttributeError`.
    """
    setting = getattr(exc, "setting", None) or UNNAMED_SETTING
    return UNUSABLE_CONFIG_REASON.format(setting=setting)


def _provider_block(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `provider:` mapping, with a committed secret refused wherever it is read.

    The refusal lives here rather than in `from_config` so that every reader of the block —
    the loader, the catalogue, the default-id lookup — is covered by it. A key in a tracked
    file must fail on the first read, not on the first *request*.
    """
    provider = config.get("provider") or {}
    if not isinstance(provider, Mapping):
        raise StoryProviderConfigurationError(
            f"provider: must be a mapping, got {type(provider).__name__}", setting="provider")
    _refuse_secrets(provider, "provider")
    nested = provider.get("openai")
    if isinstance(nested, Mapping):
        _refuse_secrets(nested, "provider.openai")
    return provider


def _refuse_secrets(block: Mapping[str, Any], path: str) -> None:
    env_name = ENV_OPENAI_API_KEY if path.endswith("openai") else ENV_API_KEY
    for secret_name in ("api_key", "api_token", "authorization", "token"):
        if secret_name in block:
            raise StoryProviderConfigurationError(
                f"{path}.{secret_name} must not appear in a configuration file; set "
                f"{env_name} in the environment or in {DEFAULT_ENV_PATH.name}",
                setting=f"{path}.{secret_name}")


# Each of the four below quotes the value it refused, and each is reachable from an environment
# variable — `STORY_LLM_CONTEXT_TOKENS=many` raises "must be an integer, got 'many'". That is
# right for an operator and is why `setting=` travels beside it: the catalogue renders the
# setting name and never the message. See `UNUSABLE_CONFIG_REASON`.


def _as_int(value: Any, fallback: int, name: str) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise StoryProviderConfigurationError(
            f"{name} must be an integer, got {value!r}", setting=name) from exc


def _as_float(value: Any, fallback: float, name: str) -> float:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StoryProviderConfigurationError(
            f"{name} must be a number, got {value!r}", setting=name) from exc


def _as_bool(value: Any, fallback: bool, name: str) -> bool:
    """A YAML boolean, or a refusal. **Never `bool(value)`** — `bool("false")` is `True`, and
    the two settings this reads decide whether a temperature reaches the wire and whether a
    provider may retain an evidence package."""
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    raise StoryProviderConfigurationError(
        f"{name} must be true or false, got {type(value).__name__} {value!r}", setting=name)


def _as_optional_str(value: Any) -> str | None:
    """`None` stays `None`; anything else becomes the string `validated()` judges.

    Deliberately not refusing a non-string: `reasoning_effort: none` parses as YAML's `None`
    and `reasoning_effort: minimal` as a string, so the values a person actually writes are
    already covered, and a number here reaches `REASONING_EFFORTS` and is refused there with
    the vocabulary in the message.
    """
    return None if value is None else str(value)
