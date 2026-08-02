"""Where the graph layer resolves *which* database it is talking to, and opens the driver.

One responsibility, stated narrowly because the rest of the layer depends on it being narrow:
turn `config/graph.yaml` plus the environment into a `GraphSettings`, and hand back a
`neo4j.Driver`. This is the only module in the repository that calls
`neo4j.GraphDatabase.driver` — V1_GRAPH_PROTOTYPE §11 confines the driver to
`graph/stages/load/`, and confining it to one *file* inside that package is what makes
"nothing else opens a connection" checkable by reading one import line.

**Absent configuration is never a default here.** `resolved_uri` and `resolved_database`
raise rather than fall back, and `driver_for` reads both before it constructs anything. The
reason is §6.2's wipe: `MATCH (n) DETACH DELETE n` against "whatever the driver defaults to"
is a destructive operation aimed at an address nobody chose. A target that cannot be named is
an error at the boundary, so the destructive stage downstream cannot inherit a guess.

Credentials come from the environment only (§5.1). `config/graph.yaml` carries no user and no
password, and `GraphSettings` holds the password as a `pydantic.SecretStr` so that a `repr` of
the settings — in a traceback, a log line, a `pytest` assertion dump — prints a mask instead.
That is the one thing pydantic is used for here, and it is already a dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from neo4j import Driver, GraphDatabase, Query
from pydantic import SecretStr

#: `graph/stages/load/connection.py` -> `graph/stages/load` -> `graph/stages` -> `graph` -> root.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "graph.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

#: The four names §5.1 declares, and the only ones read from the environment. Listed as a
#: constant because a test asserts `config/graph.yaml` contains none of them.
ENV_URI = "NEO4J_URI"
ENV_USER = "NEO4J_USER"
ENV_PASSWORD = "NEO4J_PASSWORD"
ENV_DATABASE = "NEO4J_DATABASE"

REPLACE_POLICIES = ("refuse", "replace")


class GraphConnectionError(RuntimeError):
    """Anything that stops this module from naming or reaching a database."""


class GraphConfigError(GraphConnectionError):
    """`config/graph.yaml` is missing, unreadable, or holds a value that cannot be used."""


class UnresolvedGraphTargetError(GraphConnectionError):
    """No URI or no database could be resolved — see the module docstring on why this raises.

    Its own type, rather than a `ValueError`, so the wipe path in the loader can catch exactly
    this and say "refusing to delete from an unnamed database" instead of guessing at a cause.
    """


class MissingGraphCredentialError(GraphConnectionError):
    """`NEO4J_USER` or `NEO4J_PASSWORD` is absent from the environment and from `.env`."""


@dataclass(frozen=True)
class GraphSettings:
    """The whole of `config/graph.yaml`, with the environment overlaid on top.

    `configured_uri` and `configured_database` are the *raw* resolution result and may be
    `None` or empty; `resolved_uri` and `resolved_database` are the checked ones. Two names
    for one value looks redundant until the alternative is considered: a single always-valid
    field forces validation into `load_settings`, which means `config/graph.yaml` could not be
    read at all — to print the batch sizes, say — on a machine with no `.env`.
    """

    configured_uri: str | None
    configured_database: str | None
    user: str | None
    #: Masked in `repr` by `SecretStr`; reach the value through `auth`, never by printing this.
    password: SecretStr | None
    connect_timeout_seconds: float
    query_timeout_seconds: float
    node_batch_size: int
    edge_batch_size: int
    graph_runs_root: Path
    schema_version: str
    replace_policy: str

    @property
    def resolved_uri(self) -> str:
        if not (self.configured_uri or "").strip():
            raise UnresolvedGraphTargetError(
                f"no Neo4j URI resolved: set {ENV_URI} in the environment or in "
                f"{DEFAULT_ENV_PATH.name}, or `neo4j.uri` in config/graph.yaml. Refusing to "
                "fall back to a default, because the loader's wipe would then target a "
                "database nobody named."
            )
        return self.configured_uri.strip()  # type: ignore[union-attr]

    @property
    def resolved_database(self) -> str:
        if not (self.configured_database or "").strip():
            raise UnresolvedGraphTargetError(
                f"no Neo4j database resolved: set {ENV_DATABASE} in the environment or in "
                f"{DEFAULT_ENV_PATH.name}, or `neo4j.database` in config/graph.yaml. "
                "Refusing to let the driver pick the server's default database for a "
                "destructive operation."
            )
        return self.configured_database.strip()  # type: ignore[union-attr]

    @property
    def auth(self) -> tuple[str, str]:
        """The driver's `auth` pair, built at the last possible moment."""
        if not (self.user or "").strip():
            raise MissingGraphCredentialError(
                f"{ENV_USER} is not set; credentials never live in config/graph.yaml (§5.1)"
            )
        if self.password is None or not self.password.get_secret_value():
            raise MissingGraphCredentialError(
                f"{ENV_PASSWORD} is not set; credentials never live in config/graph.yaml (§5.1)"
            )
        return (self.user.strip(), self.password.get_secret_value())  # type: ignore[union-attr]

    def query(self, text: str) -> Query:
        """`text` carrying this configuration's server-side timeout.

        Here rather than in each caller so `query_timeout_seconds` has exactly one reader; the
        driver applies the timeout server-side only when the statement is wrapped this way.
        """
        return Query(text, timeout=self.query_timeout_seconds)  # type: ignore[arg-type]


def read_env_file(path: Path) -> dict[str, str]:
    """The four `KEY=VALUE` lines of the repository-root `.env`. Missing file -> `{}`.

    **Parsed here rather than with `python-dotenv`**, deliberately: the file is four lines, the
    dependency rule in this repository is to add a library only when it removes real work, and
    the one subtlety — Compose strips `export `, surrounding quotes and stray whitespace while
    a plain `split("=")` does not — is three lines to handle and is the exact discrepancy that
    `docs/05_LOCAL_NEO4J_ENVIRONMENT.md` records as surfacing only as an opaque auth failure.
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


def read_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise GraphConfigError(f"{path} does not exist")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GraphConfigError(f"{path} is not a YAML mapping")
    return loaded


def load_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Mapping[str, str] | None = None,
) -> GraphSettings:
    """`config/graph.yaml`, then `.env`, then the process environment — later wins.

    That order is the one a reader expects and the one that makes an explicit `export` able to
    redirect a single command at a different server without editing a tracked file.

    An environment variable set to the empty string is an override *to empty*, not an absence:
    it leaves `configured_uri` empty and `resolved_uri` therefore raises. Silently restoring
    the YAML default would turn a typo in `.env` into a connection to the wrong database, which
    is the failure this module exists to make impossible.
    """
    document = read_config(config_path)
    neo4j_section = document.get("neo4j") or {}
    load_section = document.get("load") or {}
    paths_section = document.get("paths") or {}

    layered: dict[str, str] = {}
    for source in (read_env_file(env_path), dict(environ if environ is not None else os.environ)):
        for name in (ENV_URI, ENV_USER, ENV_PASSWORD, ENV_DATABASE):
            if name in source:
                layered[name] = source[name]

    password = layered.get(ENV_PASSWORD)
    replace_policy = str(load_section.get("replace_policy", "refuse"))
    if replace_policy not in REPLACE_POLICIES:
        raise GraphConfigError(
            f"load.replace_policy is {replace_policy!r}, expected one of {REPLACE_POLICIES}"
        )

    settings = GraphSettings(
        configured_uri=layered.get(ENV_URI, neo4j_section.get("uri")),
        configured_database=layered.get(ENV_DATABASE, neo4j_section.get("database")),
        user=layered.get(ENV_USER),
        password=None if password is None else SecretStr(password),
        connect_timeout_seconds=float(neo4j_section.get("connect_timeout_seconds", 10)),
        query_timeout_seconds=float(neo4j_section.get("query_timeout_seconds", 120)),
        node_batch_size=_positive_int(load_section, "node_batch_size", 1000),
        edge_batch_size=_positive_int(load_section, "edge_batch_size", 1000),
        graph_runs_root=REPO_ROOT / str(paths_section.get("graph_runs_root", "data/graph_runs")),
        schema_version=str(document.get("schema_version", "")),
        replace_policy=replace_policy,
    )
    if not settings.schema_version:
        raise GraphConfigError(f"{config_path} declares no schema_version")
    return settings


def _positive_int(section: Mapping[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GraphConfigError(f"load.{key} is {value!r}, expected a positive integer")
    return value


def driver_for(settings: GraphSettings) -> Driver:
    """An open driver aimed at `settings.resolved_uri`. The caller closes it.

    Reads `resolved_uri` and `resolved_database` *before* constructing anything, so a run with
    an unnamed target fails here with `UnresolvedGraphTargetError` rather than several stages
    later with a connection to somewhere unintended. `resolved_database` is unused by the
    constructor — the driver takes it per query — and is read anyway for exactly that reason.
    """
    uri = settings.resolved_uri
    settings.resolved_database
    return GraphDatabase.driver(
        uri,
        auth=settings.auth,
        connection_timeout=settings.connect_timeout_seconds,
    )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENV_PATH",
    "ENV_DATABASE",
    "ENV_PASSWORD",
    "ENV_URI",
    "ENV_USER",
    "GraphConfigError",
    "GraphConnectionError",
    "GraphSettings",
    "MissingGraphCredentialError",
    "REPO_ROOT",
    "UnresolvedGraphTargetError",
    "driver_for",
    "load_settings",
    "read_config",
    "read_env_file",
]
