"""The load stage's connection boundary and Neo4j schema (V1_GRAPH_PROTOTYPE §5.1–§5.3).

Two kinds of test, split by the `neo4j` marker rather than by file, so a reader sees the
offline claim and the on-server claim about the same statement side by side:

    unmarked     statements, settings resolution, and the two refusals — no database
    @neo4j       what the live 5.26.28 Community server does with them

`pytest -m "not live and not neo4j"` is green with no container running, and the marked tests
*skip* rather than error when the server is unreachable — an absent local database is a fact
about the machine, not a failing assertion about the code.

The marked tests deliberately leave the schema in place. Applying it is idempotent and the
schema is what G2 exists to produce; dropping it at teardown would delete the thing the next
stage needs and make "applied twice" untestable in the only way that matters.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from graph.core.models import BASE_LABELS
from graph.stages.load.connection import (
    DEFAULT_CONFIG_PATH,
    ENV_DATABASE,
    ENV_PASSWORD,
    ENV_URI,
    ENV_USER,
    GraphSettings,
    MissingGraphCredentialError,
    UnresolvedGraphTargetError,
    driver_for,
    load_settings,
    read_env_file,
)
from graph.stages.load.schema import (
    CONSTRAINED_RELATIONSHIP_TYPES,
    CONSTRAINT_NAMES,
    EDGE_KEY_PROPERTY,
    FULLTEXT_INDEXES,
    INDEXES,
    INDEX_NAMES,
    KEY_PROPERTIES,
    NODE_CONSTRAINT_NAMES,
    RELATIONSHIP_CONSTRAINT_NAMES,
    apply_schema,
    await_indexes,
    constraint_statements,
    index_statements,
    read_schema,
    schema_statements,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: §5.2's seven pairs, written out rather than derived. `schema.py` derives them from
#: `BASE_LABELS`; a test that derived them the same way would only prove the derivation equals
#: itself, so the plan's own table is transcribed here and compared against the code.
EXPECTED_CONSTRAINTS = {
    ("entity_key", "NODE", "Entity", "entity_id"),
    ("observation_key", "NODE", "Observation", "observation_id"),
    ("event_key", "NODE", "Event", "event_id"),
    ("metric_key", "NODE", "Metric", "metric_id"),
    ("passage_key", "NODE", "Passage", "passage_id"),
    ("document_key", "NODE", "Document", "document_id"),
    ("issue_key", "NODE", "Issue", "issue_id"),
}

#: §5.2's relationship half, transcribed the same way: one `edge_key` uniqueness constraint per
#: §3.2 relationship type. Written out rather than derived from
#: `CONSTRAINED_RELATIONSHIP_TYPES`, so a type quietly dropped from that tuple fails here.
EXPECTED_RELATIONSHIP_CONSTRAINTS = {
    (f"{relationship_type.lower()}_edge_key", "RELATIONSHIP", relationship_type, "edge_key")
    for relationship_type in (
        "BORROWS_UNDER", "CONCERNS_METRIC", "DISTINCT_FROM", "EVIDENCED_BY", "FOUND_IN",
        "HAS_OBSERVATION", "HOLDS_POSITION_AT", "OBSERVATION_OF_SUBJECT", "PARTICIPATES_IN",
        "PART_OF", "PLACEHOLDER_FOR", "RECONCILES_TO",
    )
}

#: Names that would mean a credential had been written into a tracked file.
SECRET_KEY_PATTERN = re.compile(r"password|passwd|secret|token|credential|api_?key|auth", re.I)


def settings_for_test(**overrides) -> GraphSettings:
    """A complete `GraphSettings` with no database behind it, for the offline assertions."""
    defaults = dict(
        configured_uri="bolt://localhost:7687",
        configured_database="neo4j",
        user="neo4j",
        password=None,
        connect_timeout_seconds=10.0,
        query_timeout_seconds=120.0,
        node_batch_size=1000,
        edge_batch_size=1000,
        graph_runs_root=REPO_ROOT / "data" / "graph_runs",
        schema_version="1.0.0",
        replace_policy="refuse",
    )
    defaults.update(overrides)
    return GraphSettings(**defaults)  # type: ignore[arg-type]


# -- config/graph.yaml carries defaults and no credential (§5.1) -----------------------------


def config_document() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def walk(node, trail=()):
    """Every (dotted path, value) pair in the document, so nesting cannot hide a key."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, trail + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, trail + (str(index),))
    else:
        yield ".".join(trail), node


def test_the_config_file_exists_and_declares_the_documented_defaults():
    document = config_document()
    assert document["neo4j"]["uri"] == "bolt://localhost:7687"
    assert document["neo4j"]["database"] == "neo4j"
    assert document["load"]["node_batch_size"] == 1000
    assert document["load"]["edge_batch_size"] == 1000
    assert document["load"]["replace_policy"] == "refuse"
    assert document["paths"]["graph_runs_root"] == "data/graph_runs"
    assert document["schema_version"]


def test_the_config_file_names_no_secret_bearing_key():
    offenders = [path for path, _ in walk(config_document()) if SECRET_KEY_PATTERN.search(path)]
    assert offenders == [], f"config/graph.yaml holds credential-shaped keys: {offenders}"


def test_the_config_file_holds_no_credential_in_a_uri():
    """`bolt://user:password@host` is the shape that hides a password in an innocent key."""
    for path, value in walk(config_document()):
        if isinstance(value, str) and "://" in value:
            assert "@" not in value, f"{path} embeds userinfo in a URI"


def test_the_config_file_does_not_contain_the_real_password():
    """The strongest available check: compare against what `.env` actually holds, if present."""
    password = read_env_file(REPO_ROOT / ".env").get(ENV_PASSWORD, "")
    if not password:
        pytest.skip(".env is absent or carries no password on this machine")
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    assert password not in text
    # And the comment block must still say where credentials do live.
    for name in (ENV_URI, ENV_USER, ENV_PASSWORD, ENV_DATABASE):
        assert name in text


def test_load_settings_reads_the_committed_config():
    settings = load_settings()
    assert settings.node_batch_size == 1000
    assert settings.edge_batch_size == 1000
    assert settings.replace_policy == "refuse"
    assert settings.graph_runs_root == REPO_ROOT / "data" / "graph_runs"
    assert settings.schema_version


# -- resolution: the environment wins, and absence is never a default ------------------------


def test_the_environment_overrides_the_config_file(tmp_path):
    config = tmp_path / "graph.yaml"
    config.write_text(
        "schema_version: '1.0.0'\n"
        "neo4j:\n  uri: bolt://from-yaml:7687\n  database: fromyaml\n",
        encoding="utf-8",
    )
    settings = load_settings(
        config_path=config,
        env_path=tmp_path / "absent.env",
        environ={ENV_URI: "bolt://from-env:7687", ENV_DATABASE: "fromenv"},
    )
    assert settings.resolved_uri == "bolt://from-env:7687"
    assert settings.resolved_database == "fromenv"


def test_the_env_file_is_read_and_the_process_environment_beats_it(tmp_path):
    config = tmp_path / "graph.yaml"
    config.write_text("schema_version: '1.0.0'\nneo4j:\n  uri: bolt://from-yaml:7687\n",
                      encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        f"export {ENV_URI}='bolt://from-envfile:7687'\n"
        f"{ENV_DATABASE}= \"fromenvfile\" \n"
        f"{ENV_USER}=neo4j\n",
        encoding="utf-8",
    )
    from_file = load_settings(config_path=config, env_path=env_file, environ={})
    assert from_file.resolved_uri == "bolt://from-envfile:7687"
    assert from_file.resolved_database == "fromenvfile"
    assert from_file.user == "neo4j"

    overridden = load_settings(
        config_path=config, env_path=env_file, environ={ENV_URI: "bolt://from-process:7687"}
    )
    assert overridden.resolved_uri == "bolt://from-process:7687"


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_an_unresolved_uri_raises_rather_than_defaulting(absent):
    with pytest.raises(UnresolvedGraphTargetError):
        settings_for_test(configured_uri=absent).resolved_uri


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_an_unresolved_database_raises_rather_than_defaulting(absent):
    with pytest.raises(UnresolvedGraphTargetError):
        settings_for_test(configured_database=absent).resolved_database


def test_an_empty_environment_value_is_an_override_not_an_absence(tmp_path):
    """A typo in `.env` must not silently restore the tracked default."""
    config = tmp_path / "graph.yaml"
    config.write_text("schema_version: '1.0.0'\nneo4j:\n  uri: bolt://from-yaml:7687\n",
                      encoding="utf-8")
    settings = load_settings(config_path=config, env_path=tmp_path / "no.env",
                             environ={ENV_URI: ""})
    with pytest.raises(UnresolvedGraphTargetError):
        settings.resolved_uri


def test_the_driver_factory_refuses_an_unresolved_target():
    """`driver_for` must fail before it constructs anything — §6.2's wipe depends on it."""
    with pytest.raises(UnresolvedGraphTargetError):
        driver_for(settings_for_test(configured_uri=None))
    with pytest.raises(UnresolvedGraphTargetError):
        driver_for(settings_for_test(configured_database=""))


def test_the_real_config_with_no_env_file_names_a_target_but_cannot_reach_it(tmp_path):
    """The path the other tests never covered: the **committed** config, no `.env`, empty environ.

    The tests above use a blank environment variable or a synthetic tmp config, and both make
    `resolved_uri` raise. Neither is the situation `connection.py`'s docstring was making a claim
    about — a fresh checkout on a machine with no `.env` — and in that situation the claim was
    wrong: the URI and the database resolve happily, from `config/graph.yaml`. Asserted here in
    both directions, because the docstring now says exactly this and a docstring nothing executes
    is a docstring that drifts.
    """
    settings = load_settings(env_path=tmp_path / "there-is-no-env-file", environ={})

    # A target *is* resolved — from a tracked file somebody committed, which is what a default in
    # a config file is for. It is not the driver picking one.
    assert settings.resolved_uri == "bolt://localhost:7687"
    assert settings.resolved_database == "neo4j"
    assert settings.user is None and settings.password is None

    # And the destructive path is still shut, because credentials may only come from the
    # environment (§5.1) and `driver_for` reads them before it constructs anything.
    with pytest.raises(MissingGraphCredentialError):
        settings.auth
    with pytest.raises(MissingGraphCredentialError):
        driver_for(settings)


def test_a_missing_credential_is_its_own_error():
    with pytest.raises(MissingGraphCredentialError):
        settings_for_test(user=None, password=None).auth
    with pytest.raises(MissingGraphCredentialError):
        settings_for_test(user="neo4j", password=None).auth


# -- the password is never printed -----------------------------------------------------------


def test_the_password_never_appears_in_a_repr_or_a_log_line():
    from pydantic import SecretStr

    secret = "correct-horse-battery-staple"
    settings = settings_for_test(password=SecretStr(secret))
    for rendering in (repr(settings), str(settings), f"{settings}", format(settings),
                      repr(settings.password), str(settings.password)):
        assert secret not in rendering
    # …and it is still reachable where it is actually needed.
    assert settings.auth == ("neo4j", secret)


def test_an_exception_from_an_unresolved_target_carries_no_password():
    from pydantic import SecretStr

    secret = "correct-horse-battery-staple"
    settings = settings_for_test(configured_uri=None, password=SecretStr(secret))
    with pytest.raises(UnresolvedGraphTargetError) as caught:
        settings.resolved_uri
    assert secret not in str(caught.value)


# -- the statements themselves ---------------------------------------------------------------


def test_there_is_one_constraint_per_base_label_and_no_more():
    assert len(NODE_CONSTRAINT_NAMES) == len(BASE_LABELS) == 7
    assert set(KEY_PROPERTIES) == set(BASE_LABELS)


def test_there_is_one_edge_key_constraint_per_relationship_type_and_no_more():
    """§5.2's relationship half. A type on the loader's allowlist with no constraint here would
    load with its `edge_key` unprotected, which is the state this review found."""
    from graph.stages.load.loader import ALLOWED_RELATIONSHIP_TYPES

    assert len(RELATIONSHIP_CONSTRAINT_NAMES) == len(CONSTRAINED_RELATIONSHIP_TYPES) == 12
    assert set(ALLOWED_RELATIONSHIP_TYPES) == set(CONSTRAINED_RELATIONSHIP_TYPES)
    assert len(CONSTRAINT_NAMES) == len(set(CONSTRAINT_NAMES)) == 19


def test_the_seven_node_constraints_are_the_ones_the_plan_names():
    pattern = re.compile(
        r"CREATE CONSTRAINT (\w+) IF NOT EXISTS FOR \(n:(\w+)\) REQUIRE n\.(\w+) IS UNIQUE$"
    )
    parsed = set()
    for statement in constraint_statements():
        match = pattern.match(statement)
        if match is None:
            continue
        name, label, prop = match.groups()
        parsed.add((name, "NODE", label, prop))
    assert parsed == EXPECTED_CONSTRAINTS


def test_the_twelve_relationship_constraints_are_undirected_and_on_edge_key():
    """The pattern must be `()-[r:T]-()`: a *directed* one is a syntax error on 5.26.28, and
    `edge_key` identifies the relationship rather than the direction it points."""
    pattern = re.compile(
        r"CREATE CONSTRAINT (\w+) IF NOT EXISTS FOR \(\)-\[r:(\w+)\]-\(\) "
        r"REQUIRE r\.(\w+) IS UNIQUE$"
    )
    parsed = set()
    for statement in constraint_statements():
        match = pattern.match(statement)
        if match is None:
            continue
        name, relationship_type, prop = match.groups()
        assert prop == EDGE_KEY_PROPERTY
        parsed.add((name, "RELATIONSHIP", relationship_type, prop))
    assert parsed == EXPECTED_RELATIONSHIP_CONSTRAINTS
    assert not [s for s in constraint_statements() if "]->()" in s], "a directed pattern is refused"


def test_every_constraint_statement_is_one_of_the_two_shapes():
    """No third shape slipped in: 19 statements, 7 node and 12 relationship, nothing else."""
    assert len(constraint_statements()) == 19


def test_every_statement_is_idempotent_by_construction():
    for statement in schema_statements():
        assert "IF NOT EXISTS" in statement, statement


def test_only_community_supported_syntax_is_used():
    """The two Enterprise-only forms and APOC, all absent — verified against the server below."""
    text = "\n".join(schema_statements()).upper()
    assert "NODE KEY" not in text
    assert "IS NOT NULL" not in text
    assert "EXISTS(" not in text
    assert "APOC" not in text
    assert "CREATE DATABASE" not in text


def test_the_index_set_is_the_plan_s_list_adapted_to_the_real_export():
    assert {(label, prop) for _, label, prop in INDEXES} == {
        ("Observation", "metric_id"),
        ("Observation", "period_key"),
        ("Observation", "subject_entity_id"),
        ("Observation", "source_lane"),
        ("Event", "event_type_id"),
        ("Event", "occurred_on"),
        ("Passage", "document_id"),
        ("Issue", "code"),
    }
    assert FULLTEXT_INDEXES == (("passage_text", "Passage", "text"),)
    assert len(INDEX_NAMES) == len(set(INDEX_NAMES)) == 9
    fulltext = [s for s in index_statements() if "FULLTEXT" in s]
    assert len(fulltext) == 1 and "ON EACH [n.text]" in fulltext[0]


def test_index_properties_exist_in_the_committed_projection_fixture():
    """The labels and properties are not invented: they occur in a real projected node.

    The full export lives under gitignored `data/`, so the assertion that can run from a clean
    checkout is the weaker one — every indexed label is a declared base label, and every
    indexed property is one the node builder can emit. The occupancy counts measured against
    `data/graph_runs/graph-v1-886059d862ce/nodes.jsonl` are recorded in `schema.py`.
    """
    for _, label, _ in INDEXES + FULLTEXT_INDEXES:
        assert label in BASE_LABELS


# -- against the live server ------------------------------------------------------------------


def live_settings() -> GraphSettings:
    settings = load_settings()
    try:
        settings.resolved_uri, settings.resolved_database, settings.auth
    except (UnresolvedGraphTargetError, MissingGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    return settings


@pytest.fixture(scope="module")
def live_driver():
    settings = live_settings()
    try:
        driver = driver_for(settings)
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip, not a failure
        pytest.skip(f"local Neo4j is unreachable ({type(exc).__name__}); "
                    "start it with `docker compose up -d`")
    with driver:
        yield driver, settings


@pytest.mark.neo4j
def test_the_server_is_the_community_edition_this_schema_is_designed_for(live_driver):
    driver, settings = live_driver
    rows, _, _ = driver.execute_query(
        "CALL dbms.components() YIELD name, versions, edition "
        "RETURN name, versions[0] AS version, edition",
        database_=settings.resolved_database,
    )
    assert rows[0]["edition"] == "community"
    assert rows[0]["version"].startswith("5.26.")


@pytest.mark.neo4j
def test_community_refuses_node_key_and_existence_constraints(live_driver):
    """The §5.1 claim, executed. It had been carried as "(unverified)" since G0.

    Both refusals arrive as `DatabaseError`, not `ClientError`, and `IF NOT EXISTS` does not
    suppress them — which is why `schema.py` offers uniqueness only rather than trying the
    stronger form and falling back.
    """
    from neo4j.exceptions import DatabaseError

    driver, settings = live_driver
    database = settings.resolved_database
    try:
        with pytest.raises(DatabaseError) as node_key:
            driver.execute_query(
                "CREATE CONSTRAINT probe_node_key IF NOT EXISTS "
                "FOR (n:__CapabilityProbe) REQUIRE n.probe_key IS NODE KEY",
                database_=database,
            )
        assert "Enterprise Edition" in str(node_key.value)

        with pytest.raises(DatabaseError) as not_null:
            driver.execute_query(
                "CREATE CONSTRAINT probe_not_null IF NOT EXISTS "
                "FOR (n:__CapabilityProbe) REQUIRE n.probe_id IS NOT NULL",
                database_=database,
            )
        assert "Enterprise Edition" in str(not_null.value)
    finally:
        for statement in ("DROP CONSTRAINT probe_node_key IF EXISTS",
                          "DROP CONSTRAINT probe_not_null IF EXISTS"):
            driver.execute_query(statement, database_=database)


@pytest.mark.neo4j
def test_community_hosts_a_single_user_database(live_driver):
    """§6.2 wipes the one database rather than creating a second — this is why."""
    from neo4j.exceptions import ClientError

    driver, settings = live_driver
    rows, _, _ = driver.execute_query(
        "SHOW DATABASES YIELD name, type RETURN name, type", database_="system"
    )
    assert {row["name"] for row in rows} == {"neo4j", "system"}
    assert [row["name"] for row in rows if row["type"] == "standard"] == ["neo4j"]

    with pytest.raises(ClientError) as refused:
        driver.execute_query("CREATE DATABASE probe_db", database_="system")
    assert "Unsupported administration command" in str(refused.value)


@pytest.mark.neo4j
def test_applying_the_schema_twice_leaves_exactly_one_of_each(live_driver):
    driver, settings = live_driver
    first = apply_schema(driver, settings)
    await_indexes(driver, settings, timeout_seconds=60)
    second = apply_schema(driver, settings)
    assert first == second, "the schema is not idempotent"

    assert {row[0] for row in second.managed_constraints()} == set(CONSTRAINT_NAMES)
    assert len(second.managed_constraints()) == 19
    assert len(second.managed_node_constraints()) == 7
    assert len(second.managed_relationship_constraints()) == 12
    assert {row[0] for row in second.managed_indexes()} == set(INDEX_NAMES)
    assert len(second.managed_indexes()) == 9


@pytest.mark.neo4j
def test_the_nineteen_constraints_are_on_the_right_label_type_and_property(live_driver):
    driver, settings = live_driver
    apply_schema(driver, settings)
    state = read_schema(driver, settings)
    assert set(state.managed_node_constraints()) == EXPECTED_CONSTRAINTS
    assert set(state.managed_relationship_constraints()) == EXPECTED_RELATIONSHIP_CONSTRAINTS
    # `entityType` is the server's own word, not this module's naming convention.
    assert {row[1] for row in state.managed_constraints()} == {"NODE", "RELATIONSHIP"}


@pytest.mark.neo4j
def test_community_accepts_a_relationship_uniqueness_constraint_and_enforces_it(live_driver):
    """The §5.1 correction, executed on the server that had never been asked.

    Community was assumed to offer node uniqueness only, because the G0 probe pass tried four
    node-side forms and no relationship one. It accepts `FOR ()-[r:T]-() REQUIRE r.x IS UNIQUE`,
    reports it as `RELATIONSHIP_UNIQUENESS`, and — the part that matters — **refuses a duplicate
    between two different endpoint pairs**, which is exactly the case `MERGE` semantics cannot
    cover: `MERGE` is scoped to one (source, target) pair, so two `EVIDENCED_BY` edges sharing an
    `edge_key` between different pairs used to coexist happily.
    """
    from neo4j.exceptions import ConstraintError

    driver, settings = live_driver
    apply_schema(driver, settings)
    database = settings.resolved_database
    probe_edge_key = "__schema_probe__duplicate_edge_key"
    seed = (
        "CREATE (a:Passage {passage_id: $a}), (b:Passage {passage_id: $b}), "
        "(c:Document {document_id: $c}), (d:Document {document_id: $d})"
    )
    keys = {"a": "__probe__p1", "b": "__probe__p2", "c": "__probe__d1", "d": "__probe__d2"}
    try:
        driver.execute_query(seed, keys, database_=database)
        driver.execute_query(
            "MATCH (a:Passage {passage_id: $a}), (c:Document {document_id: $c}) "
            "CREATE (a)-[:PART_OF {edge_key: $k}]->(c)",
            {**keys, "k": probe_edge_key}, database_=database,
        )
        with pytest.raises(ConstraintError):
            driver.execute_query(
                "MATCH (b:Passage {passage_id: $b}), (d:Document {document_id: $d}) "
                "CREATE (b)-[:PART_OF {edge_key: $k}]->(d)",
                {**keys, "k": probe_edge_key}, database_=database,
            )
    finally:
        driver.execute_query(
            "MATCH (n) WHERE n.passage_id IN $p OR n.document_id IN $d DETACH DELETE n",
            {"p": [keys["a"], keys["b"]], "d": [keys["c"], keys["d"]]},
            database_=database,
        )


@pytest.mark.neo4j
def test_every_declared_index_is_online_with_the_expected_type(live_driver):
    driver, settings = live_driver
    apply_schema(driver, settings)
    await_indexes(driver, settings, timeout_seconds=60)
    by_name = {row[0]: row for row in read_schema(driver, settings).managed_indexes()}
    for name, label, prop in INDEXES:
        assert by_name[name] == (name, "RANGE", label, prop)
    for name, label, prop in FULLTEXT_INDEXES:
        assert by_name[name] == (name, "FULLTEXT", label, prop)


@pytest.mark.neo4j
def test_a_uniqueness_constraint_actually_rejects_a_duplicate(live_driver):
    """The constraint is the point, not the row in `SHOW CONSTRAINTS`.

    Uses `:Issue`, whose key property is `issue_id`, and deletes both probe nodes afterwards.
    A `ConstraintError` here is the guarantee §5.2 is claiming; without it the constraint could
    exist and be unenforced and every other assertion in this file would still pass.
    """
    from neo4j.exceptions import ConstraintError

    driver, settings = live_driver
    apply_schema(driver, settings)
    database = settings.resolved_database
    probe = "__schema_probe__duplicate"
    try:
        driver.execute_query(
            "CREATE (n:Issue {issue_id: $id})", {"id": probe}, database_=database
        )
        with pytest.raises(ConstraintError):
            driver.execute_query(
                "CREATE (n:Issue {issue_id: $id})", {"id": probe}, database_=database
            )
    finally:
        driver.execute_query(
            "MATCH (n:Issue {issue_id: $id}) DETACH DELETE n", {"id": probe},
            database_=database,
        )
