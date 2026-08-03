"""Where the story layer resolves *which* database it reads, and opens a read-only driver.

The one module in `story/` that imports `neo4j`
(`tests/story/test_story_package_structure.py` enforces it), and the whole of what it does is
the six things WORKSTREAM_BOUNDARY §4.1 lists: URI/user/password/database configuration,
repository-consistent `.env` layering, driver creation, connectivity verification, lifecycle
and close, and **parameterised read statements with an explicit timeout**. It creates no
schema, wipes nothing, loads nothing, issues no write, carries no batch size and migrates
nothing — every one of those belongs to `graph/stages/load/`, which owns the write path.

**Re-stated from `graph/stages/load/connection.py`, not imported from it.** `graph.stages` is
not a surface this package may reach (WORKSTREAM_BOUNDARY §4), and the repository already
settles that trade the same way: `extraction/core/config.py:34-43` re-states `canonical_hash`
rather than cross a boundary, and says so in its docstring. Re-stating also buys something an
import could not — `driver_for` returns a **write-capable** driver, so a story layer built on
it would have read-only as a convention. Here the class exposes one method, it issues no write
clause, and the caller cannot supply one it did not write. What is duplicated is small and
named: the four-line `.env` parser, the layering order, and `SecretStr` on the password. The
`.env` file, the four variable names and the precedence rule are **shared behaviour** — a
story run and a graph load must reach the same server from the same machine — so a divergence
here would be the defect, not the duplication.

**What is deliberately *not* re-stated**: `GraphSettings`'s `node_batch_size`,
`edge_batch_size`, `replace_policy`, `graph_runs_root` and `schema_version`. `StoryNeo4jSettings`
is a different type with a different job, and a load setting on it would be an invitation.

**`RoutingControl.READ` is intent, not enforcement** (plan §16). The driver documentation is
explicit that routing is not access control, and on a single Community instance there is no
cluster to route within; Community also has no role-based access control at all
(`SHOW ROLES` -> `UnsupportedAdministrationCommand`, measured 2026-08-03), so a read-only
database user is not available either. The controls that actually hold are that this module
issues no write clause, that every statement is code-owned upstream of it, and that values
arrive only as bound parameters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from neo4j import Driver, GraphDatabase, Query, RoutingControl
from neo4j.exceptions import (
    AuthError,
    ConfigurationError,
    Neo4jError,
    ServiceUnavailable,
    SessionExpired,
)
from neo4j.graph import Entity as GraphEntity
from neo4j.graph import Path as GraphPath
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time
from pydantic import SecretStr

from story.core.models import HealthStatus

#: `story/providers/neo4j_connection.py` -> `story/providers` -> `story` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: `config/graph.yaml`'s `neo4j:` block, read for the target the machine already runs against.
#: The story layer has no configuration file of its own yet — `config/story.yaml` arrives at
#: S11 with the per-tool row and timeout budgets (plan §16) — and inventing one now to hold a
#: URI that is already committed a directory away would give a reader two answers to
#: "which server?". Only the `neo4j:` block is read; `load:` is not this module's business.
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "graph.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

#: The four names the repository puts in `.env`, and the only ones read from the environment.
ENV_URI = "NEO4J_URI"
ENV_USER = "NEO4J_USER"
ENV_PASSWORD = "NEO4J_PASSWORD"
ENV_DATABASE = "NEO4J_DATABASE"

#: The connectivity probe. A statement rather than only `Driver.verify_connectivity()` because
#: a reachable server is not the question the freshness gate asks — it needs to know that
#: *this* database answers a read under *these* credentials, and a socket handshake proves
#: neither. Kept free of every write keyword so the structural scan over `story/`'s Cypher
#: literals has something real to walk.
CONNECTIVITY_PROBE = "RETURN 1 AS ok"

#: What may cross the boundary untouched. Everything else is converted or refused.
PLAIN_SCALARS = (str, bool, int, float, bytes, bytearray)


class StoryGraphError(RuntimeError):
    """Anything that stops this module naming, reaching, or making sense of the database.

    Story's own hierarchy, mirroring `GraphConnectionError`'s shape without importing it, so a
    caller can catch a story fault without catching the load stage's.
    """


class StoryGraphUnconfiguredError(StoryGraphError):
    """No URI or no database was named — by `config/graph.yaml`, `.env`, or the environment.

    Its own type rather than a `ValueError` so the freshness gate can tell "nobody configured
    this checkout" from "the server is down", which are different answers for an operator.
    """


class StoryGraphCredentialError(StoryGraphError):
    """`NEO4J_USER` or `NEO4J_PASSWORD` is absent. Credentials never live in a tracked file."""


class StoryGraphUnreachableError(StoryGraphError):
    """The server could not be reached, or refused the credentials.

    Raised for *transport* faults only. A fault the server reports about the statement — a
    syntax error, a type error, a transaction timeout — is left as the driver's `Neo4jError`
    and propagates untouched, because it is the caller's statement that is wrong and wrapping
    it would hide the server's own code from the tool that has to act on it.
    """


class StoryGraphResultError(StoryGraphError):
    """A returned value has no plain-Python form this module is willing to invent.

    Not in the boundary document's list of three, and added because the conversion below needs
    somewhere to refuse: `RETURN o` hands back a `neo4j.graph.Node`, and flattening it to its
    properties would silently drop its labels and its identity while satisfying "no driver
    type escaped". §9 requires named return fields, so a whole node reaching here is a
    query-writing mistake and is reported as one.
    """


@dataclass(frozen=True)
class StoryNeo4jSettings:
    """Which server to read, under whose credentials, with what ceilings.

    Deliberately **not** `GraphSettings` and deliberately not shaped like it: no
    `node_batch_size`, no `edge_batch_size`, no `replace_policy`, no `schema_version`. Those
    configure a write, and a read-only adapter that carried them would be one field access
    away from being something else.

    `configured_*` are the raw resolution result and may be `None` or empty; `resolved_*` are
    the checked ones. Two names for one value is the same split `GraphSettings` makes, for the
    same reason: it keeps `config/graph.yaml` readable on a machine with no `.env`.
    """

    configured_uri: str | None
    configured_database: str | None
    user: str | None
    #: Masked in `repr` by `SecretStr`, so a traceback, a log line or a pytest assertion dump
    #: prints `SecretStr('**********')`. Reach the value through `auth`, never by printing it.
    password: SecretStr | None
    connect_timeout_seconds: float
    #: `neo4j.query_timeout_seconds` from `config/graph.yaml`, and **not** a default for
    #: `read()` — `ReadQueryExecutor.read` takes `timeout_seconds` keyword-only with no
    #: default precisely so no call site can end up without one. Its only use here is the
    #: connectivity probe. S11's `config/story.yaml` owns per-tool timeouts (§16).
    query_timeout_seconds: float

    @property
    def resolved_uri(self) -> str:
        if not (self.configured_uri or "").strip():
            raise StoryGraphUnconfiguredError(
                f"no Neo4j URI resolved: set {ENV_URI} in the environment or in "
                f"{DEFAULT_ENV_PATH.name}, or `neo4j.uri` in config/graph.yaml. Refusing to "
                "fall back to the driver's own default, because a story run would then read "
                "facts out of a database nobody named and cite them as this graph's."
            )
        return self.configured_uri.strip()  # type: ignore[union-attr]

    @property
    def resolved_database(self) -> str:
        if not (self.configured_database or "").strip():
            raise StoryGraphUnconfiguredError(
                f"no Neo4j database resolved: set {ENV_DATABASE} in the environment or in "
                f"{DEFAULT_ENV_PATH.name}, or `neo4j.database` in config/graph.yaml."
            )
        return self.configured_database.strip()  # type: ignore[union-attr]

    @property
    def auth(self) -> tuple[str, str]:
        """The driver's `auth` pair, built at the last possible moment."""
        if not (self.user or "").strip():
            raise StoryGraphCredentialError(
                f"{ENV_USER} is not set; credentials never live in config/graph.yaml"
            )
        if self.password is None or not self.password.get_secret_value():
            raise StoryGraphCredentialError(
                f"{ENV_PASSWORD} is not set; credentials never live in config/graph.yaml"
            )
        return (self.user.strip(), self.password.get_secret_value())  # type: ignore[union-attr]

    def read_query(self, text: str, timeout_seconds: float) -> Query:
        """`text` carrying a server-side timeout. The only way this module builds a statement.

        A bare `str` passed to `execute_query` **silently drops the timeout**, which is why
        `GraphSettings.query()` exists upstream and why this exists here: the wrapping happens
        in one place, so §16's "a timeout on every statement" is a property of the module
        rather than a habit of its callers.

        Zero is refused because Bolt reads a transaction timeout of 0 as *no timeout*, not as
        *expire immediately* — measured 2026-08-03 against 5.26.28: a 1.5-second statement
        completed under `timeout=0` and under `timeout=None` alike, and was terminated under
        `timeout=0.001`. An accidental `timeout_seconds=0` would therefore remove the ceiling
        while looking like the tightest one possible.
        """
        if timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be positive, got {timeout_seconds!r}; the server reads "
                "0 as 'no timeout'"
            )
        return Query(text, timeout=timeout_seconds)  # type: ignore[arg-type]


def read_env_file(path: Path) -> dict[str, str]:
    """The `KEY=VALUE` lines of the repository-root `.env`. Missing file -> `{}`.

    Parsed here rather than with `python-dotenv` for the reason the graph layer already
    records: the file is four lines, this repository adds a library only when it removes real
    work, and the one subtlety — Compose strips `export `, surrounding quotes and stray
    whitespace where a plain `split("=")` does not — is three lines. The discrepancy surfaces
    only as an opaque auth failure (`docs/05_LOCAL_NEO4J_ENVIRONMENT.md`), so parsing it
    differently from the loader is exactly the divergence worth avoiding.
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


def load_neo4j_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Mapping[str, str] | None = None,
) -> StoryNeo4jSettings:
    """`config/graph.yaml`, then `.env`, then the process environment — later wins.

    The same order the loader uses, because the two must resolve the same target from the same
    machine; a story run reading a different server than the one `graph load` wrote would
    produce citations for facts that are not there.

    An environment variable set to the empty string is an override *to empty*, not an absence:
    it leaves `configured_uri` empty and `resolved_uri` therefore raises. Restoring the YAML
    default would turn a typo in `.env` into a silent connection to the wrong database.

    A missing or unreadable `config/graph.yaml` is not fatal here, and that is the one
    deliberate divergence from `load_settings`: the loader needs `schema_version` and the batch
    sizes and cannot proceed without the file, while everything this module needs can arrive
    from the environment alone. A checkout with the file deleted and `NEO4J_*` exported can
    still read the graph; a checkout with neither raises `StoryGraphUnconfiguredError` at the
    first `resolved_uri`, which is the honest failure.
    """
    section = _neo4j_section(config_path)

    layered: dict[str, str] = {}
    for source in (read_env_file(env_path), dict(environ if environ is not None else os.environ)):
        for name in (ENV_URI, ENV_USER, ENV_PASSWORD, ENV_DATABASE):
            if name in source:
                layered[name] = source[name]

    password = layered.get(ENV_PASSWORD)
    return StoryNeo4jSettings(
        configured_uri=layered.get(ENV_URI, section.get("uri")),
        configured_database=layered.get(ENV_DATABASE, section.get("database")),
        user=layered.get(ENV_USER),
        password=None if password is None else SecretStr(password),
        connect_timeout_seconds=float(section.get("connect_timeout_seconds", 10)),
        query_timeout_seconds=float(section.get("query_timeout_seconds", 120)),
    )


def _neo4j_section(config_path: Path) -> Mapping[str, Any]:
    if not config_path.is_file():
        return {}
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return {}
    section = document.get("neo4j")
    return section if isinstance(section, dict) else {}


def plain_value(value: Any, *, field: str = "") -> Any:
    """One returned value as plain Python, or a refusal. Recursive, because rows nest.

    The rule `story/contracts.py` states for `ReadQueryExecutor`: no `neo4j.Record`, no
    `neo4j.Node`, no `neo4j.time.DateTime` reaches a stage, so `story/core/` and every stage
    above it stay testable with the driver uninstalled.

    *(Verified 2026-08-03 against the loaded run.)* The whole graph stores five property
    types — `Boolean`, `Double`, `Long`, `String`, `StringArray` — so in practice today the
    only conversion that fires is the list branch, for `heading_path` on `:Passage` and
    `block_ids` on `EVIDENCED_BY`, and those already arrive as plain `list`. The temporal and
    spatial branches exist because a projection version that writes a `date` property would
    otherwise leak `neo4j.time.Date` into an evidence package the day it landed, and nothing
    would fail until something tried to serialise it.
    """
    if value is None or isinstance(value, PLAIN_SCALARS):
        return value
    # **Order is load-bearing, and two of these were found by the test rather than reasoned
    # out.** `Point` and `Duration` are both `tuple` subclasses, and `Node`/`Relationship` are
    # both `Mapping`s over their properties — so a generic sequence branch placed first turns
    # `Duration(days=3)` into `[0, 3, 0, 0]`, and a generic mapping branch first flattens a
    # whole node to its properties and silently drops its labels. Every specific type is
    # therefore matched before either general one.
    if isinstance(value, Point):
        return {"srid": int(value.srid), "coordinates": [float(c) for c in value]}
    if isinstance(value, (Date, Time, DateTime, Duration)):
        return value.iso_format()
    if isinstance(value, (GraphEntity, GraphPath)):
        raise StoryGraphResultError(
            f"{field or 'a returned field'} is a {type(value).__name__}: return named "
            "properties instead. Flattening it here would drop its labels and its identity "
            "while still looking like a plain row (§9 requires named return fields)."
        )
    if isinstance(value, Mapping):
        return {str(key): plain_value(item, field=field) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_value(item, field=field) for item in value]
    raise StoryGraphResultError(
        f"{field or 'a returned field'} is a {type(value).__name__}, which has no plain "
        "Python form this module is willing to invent"
    )


def plain_row(record: Mapping[str, Any]) -> dict[str, Any]:
    """A `neo4j.Record` as an ordinary `dict`. Field names are carried into the refusal."""
    return {str(key): plain_value(record[key], field=str(key)) for key in record.keys()}


def driver_for_reading(settings: StoryNeo4jSettings) -> Driver:
    """An open driver aimed at `settings.resolved_uri`. The caller closes it.

    The only call to `neo4j.GraphDatabase.driver` in `story/`. Reads `resolved_uri`,
    `resolved_database` and `auth` *before* constructing anything, so an unconfigured checkout
    fails here — with a typed error naming what is missing — rather than several stages later
    against a server nobody chose. `resolved_database` is unused by the constructor, which
    takes it per query, and is read anyway for exactly that reason.
    """
    uri = settings.resolved_uri
    settings.resolved_database
    return GraphDatabase.driver(
        uri,
        auth=settings.auth,
        connection_timeout=settings.connect_timeout_seconds,
    )


class Neo4jReadExecutor:
    """`ReadQueryExecutor` over Bolt. Structural conformance, no inheritance.

    Holds the driver and hands back plain rows. It is constructed by `story/context.py` and by
    nothing else — no retrieval tool builds one, which is what makes "the driver lives behind
    one module" true at runtime as well as in the import graph (D1).

    The driver is passed in rather than built here so a test can drive `read()` against a stub
    that records what it was given, and prove offline that the timeout and the read routing
    reach the driver at all. `open_read_executor` is the composition-root spelling.
    """

    def __init__(self, settings: StoryNeo4jSettings, driver: Driver) -> None:
        self._settings = settings
        self._driver = driver
        #: Resolved once, at construction, for the same reason `driver_for_reading` reads it:
        #: a database name that only fails on the first read is a name nobody checked.
        self._database = settings.resolved_database

    @property
    def database(self) -> str:
        return self._database

    def __repr__(self) -> str:
        """Names the target, never the credentials.

        `SecretStr` already masks the password inside `StoryNeo4jSettings`; this method keeps
        the settings object out of the string entirely, so the mask is not the only thing
        standing between a traceback and a secret.
        """
        return f"{type(self).__name__}(uri={self._settings.resolved_uri!r}, " \
               f"database={self._database!r})"

    def read(
        self,
        statement: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], ...]:
        """Run one read statement with a server-side timeout and return plain rows.

        `routing_=RoutingControl.READ` is **intent, not enforcement** (§16): the driver's own
        documentation says routing is not for enforcing access control, and against a single
        Community instance there is no cluster to route within. It is set because a statement
        this module issues is a read and should say so — the controls that hold are that
        nothing here writes and that `parameters` is the only place a caller value can go.
        """
        query = self._settings.read_query(statement, timeout_seconds)
        try:
            records, _summary, _keys = self._driver.execute_query(
                query,
                dict(parameters),
                routing_=RoutingControl.READ,
                database_=self._database,
            )
        except (ServiceUnavailable, SessionExpired, AuthError, ConfigurationError) as exc:
            raise StoryGraphUnreachableError(
                f"{self._settings.resolved_uri} (database {self._database!r}) could not be "
                f"read: {type(exc).__name__}: {exc}"
            ) from exc
        return tuple(plain_row(record) for record in records)

    def verify_connectivity(self) -> HealthStatus:
        """Reachable, authenticated, and answering a read. **Never raises** for a dead server.

        Two steps, and the order matters for a measured reason: `Driver.verify_connectivity()`
        fails in **0.0s** against a refused port, while `execute_query` retries a managed
        transaction for **35s** before giving up (measured 2026-08-03 against
        `bolt://127.0.0.1:1`). A freshness gate that took thirty-five seconds to report "the
        database is down" would be one nobody ran. The probe statement follows, because a
        socket handshake does not prove that this database answers a read under these
        credentials.

        Status strings mirror `extraction/providers/local_openai_compatible.py`'s vocabulary,
        so an operator reading two health lines reads one set of words.
        """
        try:
            target = f"{self._settings.resolved_uri} database={self._database}"
        except StoryGraphError as exc:  # unconfigured is a state to report, not a crash
            return HealthStatus(ok=False, status="unconfigured", detail=str(exc))
        try:
            self._driver.verify_connectivity()
            rows = self.read(
                CONNECTIVITY_PROBE, {},
                timeout_seconds=self._settings.query_timeout_seconds,
            )
        except AuthError as exc:
            return HealthStatus(ok=False, status="unauthorized", detail=f"{target}: {exc}")
        except (ServiceUnavailable, SessionExpired, ConfigurationError,
                StoryGraphUnreachableError) as exc:
            return HealthStatus(ok=False, status="unavailable",
                                detail=f"{target}: {type(exc).__name__}: {exc}")
        except Neo4jError as exc:
            return HealthStatus(ok=False, status="error", detail=f"{target}: {exc.code}")
        if rows != ({"ok": 1},):
            return HealthStatus(ok=False, status="error",
                                detail=f"{target}: probe returned {rows!r}")
        return HealthStatus(ok=True, status="ok", detail=target)

    def close(self) -> None:
        """Idempotent — the driver's own `close()` is, and a gate that refuses may close twice."""
        self._driver.close()

    def __enter__(self) -> Neo4jReadExecutor:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def open_read_executor(settings: StoryNeo4jSettings | None = None) -> Neo4jReadExecutor:
    """Settings from the environment if none are given, then a driver, then the executor.

    The executor owns the driver it is handed from here on: `close()` closes it.
    """
    resolved = load_neo4j_settings() if settings is None else settings
    return Neo4jReadExecutor(resolved, driver_for_reading(resolved))


__all__ = [
    "CONNECTIVITY_PROBE",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENV_PATH",
    "ENV_DATABASE",
    "ENV_PASSWORD",
    "ENV_URI",
    "ENV_USER",
    "Neo4jReadExecutor",
    "PLAIN_SCALARS",
    "REPO_ROOT",
    "StoryGraphCredentialError",
    "StoryGraphError",
    "StoryGraphResultError",
    "StoryGraphUnconfiguredError",
    "StoryGraphUnreachableError",
    "StoryNeo4jSettings",
    "driver_for_reading",
    "load_neo4j_settings",
    "open_read_executor",
    "plain_row",
    "plain_value",
    "read_env_file",
]
