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

There is **no `config/story.yaml` yet** — S11 owns it (plan §23). So the defaults below are
constants, `from_config` reads a `provider:` block for the day that file exists, and
`load_provider_config` layers `.env` and the process environment over them. Nothing here
opens a YAML file: `config/graph.yaml` has no provider block, and inventing one now would give
a reader two answers to "which server?".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pydantic import SecretStr

#: `story/providers/public.py` -> `story/providers` -> `story` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

#: The one transport this package knows how to build. Not a free-text label: `from_config`
#: refuses anything else rather than falling through to a default.
KIND_LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"
SUPPORTED_KINDS = frozenset({KIND_LOCAL_OPENAI_COMPATIBLE})

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
    """


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
    """

    def __init__(self, message: str, violations: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.violations = violations


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

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "StoryProviderConfig":
        """The `provider:` block of a parsed `config/story.yaml`, for when S11 mints one.

        A secret in that mapping is **refused**, not read: the file is tracked, and a
        configuration that can hold a key is one that will eventually hold a committed key.
        """
        provider = config.get("provider") or {}
        if not isinstance(provider, Mapping):
            raise StoryProviderConfigurationError(
                f"provider: must be a mapping, got {type(provider).__name__}")
        for secret_name in ("api_key", "api_token", "authorization", "token"):
            if secret_name in provider:
                raise StoryProviderConfigurationError(
                    f"provider.{secret_name} must not appear in a configuration file; set "
                    f"{ENV_API_KEY} in the environment or in {DEFAULT_ENV_PATH.name}")
        loaded = cls(
            kind=str(provider.get("kind", DEFAULT_KIND)),
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
        )
        return loaded.validated()

    def validated(self) -> "StoryProviderConfig":
        """Reject a configuration that cannot produce constrained output. Idempotent.

        Called from the constructor too, so a hand-built config cannot slip past the check
        that only `from_config` would otherwise apply.
        """
        if self.kind not in SUPPORTED_KINDS:
            raise StoryProviderConfigurationError(
                f"provider.kind {self.kind!r} is not one of {sorted(SUPPORTED_KINDS)}; the "
                "story layer dispatches on this value rather than defaulting, because a typo "
                "that silently selected the local server would be found by reading the "
                "generations rather than by reading the error")
        if not self.base_url:
            raise StoryProviderConfigurationError("provider.base_url is required")
        if not self.model:
            raise StoryProviderConfigurationError(
                "provider.model is required: it is the identity every stored generation is "
                "keyed under, and the server reports a filesystem path rather than a name")
        if self.max_retries < 0:
            raise StoryProviderConfigurationError("provider.max_retries must not be negative")
        if self.max_output_tokens <= 0:
            raise StoryProviderConfigurationError("provider.max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise StoryProviderConfigurationError("provider.timeout_seconds must be positive")
        if self.max_output_tokens >= self.context_tokens:
            raise StoryProviderConfigurationError(
                f"provider.max_output_tokens ({self.max_output_tokens}) leaves no room for a "
                f"prompt in provider.context_tokens ({self.context_tokens})")
        return self

    @property
    def authorization_headers(self) -> dict[str, str]:
        """`Authorization` when a key was supplied, and an empty dict otherwise.

        Built at the last possible moment, the way `StoryNeo4jSettings.auth` builds its pair,
        so the secret exists as a plain string for the length of one request.
        """
        if self.api_key is None or not self.api_key.get_secret_value():
            return {}
        return {"Authorization": f"Bearer {self.api_key.get_secret_value()}"}


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
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Mapping[str, str] | None = None,
) -> StoryProviderConfig:
    """Constants, then a `provider:` block, then `.env`, then the process environment.

    Later wins — the order `load_neo4j_settings` uses, for the same reason: a machine that
    exports a variable means it, and a story run must resolve the same server the operator
    thinks it is talking to.

    An environment variable set to the empty string is an override *to empty*, not an absence,
    so `STORY_LLM_BASE_URL=` fails `validated()` rather than quietly restoring the default.
    """
    base = StoryProviderConfig.from_config(config or {})

    layered: dict[str, str] = {}
    for source in (read_env_file(env_path),
                   dict(environ if environ is not None else os.environ)):
        for name in ENV_NAMES:
            if name in source:
                layered[name] = source[name]

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


def _as_int(value: Any, fallback: int, name: str) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise StoryProviderConfigurationError(f"{name} must be an integer, got {value!r}") from exc


def _as_float(value: Any, fallback: float, name: str) -> float:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StoryProviderConfigurationError(f"{name} must be a number, got {value!r}") from exc
