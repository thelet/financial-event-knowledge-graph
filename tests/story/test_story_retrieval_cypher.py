"""The structural scan §16 calls story's primary control, applied to every Cypher in `story/`.

Not to `story/stages/retrieval/` — to the whole package. §16 records that scoping this to the
retrieval stage was a defect in the plan's own first draft, because §7's freshness gate also
issues Cypher and the read-only guarantee has to cover the stage that ships first. So this walks
`story/**/*.py` and applies every rule to every Cypher statement it finds, including S0c's
connectivity probe and S0b's freshness statements.

**Docstrings are stripped before anything is examined**, with the technique
`tests/ontology/test_package_structure.py:30-46` uses. The rules here are about what the code
*runs*. `cypher.py`'s module docstring says the word `RETURN` while explaining that no statement
may return a whole node, and a scan that could not tell that apart from a query would be a scan
nobody could write documentation around.

**The scan asserts its own corpus.** A structural test that discovers what to check can pass by
discovering nothing — delete `cypher.py` and every rule below is vacuously true. So the first
test pins the exact set of statement names S1 declares and requires the scan to have found all
of them, plus Cypher from outside the retrieval stage. That is the difference between a rule
and a decoration.

**Two tiers on interpolation, and the reason is a real disagreement found by running this.**

* *Package-wide*: a statement's text must be **fixed at import time** — a single string
  constant, or an f-string whose every hole is a module-level string constant in the same file.
  That is the property §16 actually buys: no caller value, no parameter, no attribute and no
  function result can reach the query text, so what the database is asked is decided by the
  source and nothing else. `story/stages/freshness/loaded_graph.py` builds four statements this
  way, interpolating its own `LOAD_MARKER_LABEL` and `RUN_ID_PROPERTY`; that is not a value
  reaching a query, and condemning it would be enforcing a spelling rather than a boundary.
* *Retrieval stage*: every statement is one `ast.Constant`, full stop. S1's own rule, kept as
  written, because this is the stage a model's choices flow into and the stage where "read the
  file and see the whole query" has to stay true.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from story.stages.retrieval import cypher

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "story"
RETRIEVAL = PACKAGE / "stages" / "retrieval"

#: A string is treated as Cypher when it returns something. Every statement in this package
#: does, including `CONNECTIVITY_PROBE = "RETURN 1 AS ok"`, and no ordinary message, code or
#: identifier in `story/` contains the bare word.
_CYPHER_MARKER = re.compile(r"\bRETURN\b")

#: WORKSTREAM_BOUNDARY §3 and §16, verbatim. Matched case-sensitively and on a word boundary,
#: so `offset_from_anchor` is not a `SET` and `document_types` is not a `CREATE`.
WRITE_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DROP", "LOAD CSV", "FOREACH")

#: §9's eight base labels, plus the status labels the plan names by hand: `NotAttempted`, which
#: every `:Issue` query must exclude, `Warned`, which §10.1 surfaces, and `GraphLoad`, which §7
#: reads. The allowlist matches by presence and never by exact set — the live graph carries 23
#: labels because the projection adds ontology-derived and evidence-kind ones, and a test
#: whitelisting the exact set would reject the nodes this plan depends on (§9).
ALLOWED_LABELS = frozenset({
    "Metric", "Observation", "Event", "Passage", "Document", "Entity", "Issue",
    "EvidenceSource", "NotAttempted", "Warned", "GraphLoad",
})

#: §9's twelve relationship types.
ALLOWED_RELATIONSHIP_TYPES = frozenset({
    "HAS_OBSERVATION", "EVIDENCED_BY", "PART_OF", "PARTICIPATES_IN", "OBSERVATION_OF_SUBJECT",
    "RECONCILES_TO", "DISTINCT_FROM", "HOLDS_POSITION_AT", "BORROWS_UNDER", "PLACEHOLDER_FOR",
    "FOUND_IN", "CONCERNS_METRIC",
})

#: A statement that neither matches, calls nor unwinds returns only literals, and its row count
#: is a property of the language rather than something a `LIMIT` could improve. S0c's
#: `RETURN 1 AS ok` is the one such statement in the package.
_READS_ROWS = re.compile(r"\b(MATCH|CALL|UNWIND)\b")

#: Cypher's aggregating functions. A `RETURN` composed only of these, with no grouping key
#: beside them, produces exactly one row — a hard bound stated in the language, which is what
#: the `LIMIT` rule is asking for. `story/stages/freshness/loaded_graph.py`'s three inventory
#: statements are bounded this way and are not weaker for it.
_AGGREGATES = ("count(", "collect(", "sum(", "avg(", "min(", "max(", "percentileDisc(",
               "stDev(")

#: What S1 owns. Pinned so the scan cannot pass by finding nothing.
EXPECTED_STATEMENT_NAMES = frozenset(cypher.STATEMENTS)


def executable_source(path: Path) -> str:
    """The module with its docstrings removed. `tests/ontology/test_package_structure.py:30-46`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def story_modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _is_cypher(value: object) -> bool:
    return isinstance(value, str) and bool(_CYPHER_MARKER.search(value))


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"`. The only names an f-string hole may name."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _fixed_text(value: ast.expr, constants: dict[str, str]) -> str | None:
    """The statement's text if it is decided at import time, else `None`.

    Three shapes are decidable: a bare constant, an f-string whose every hole is a
    module-level string constant, and an implicit concatenation of those (which the parser has
    already folded into one constant or one `JoinedStr`).
    """
    if isinstance(value, ast.Constant):
        return value.value if isinstance(value.value, str) else None
    if not isinstance(value, ast.JoinedStr):
        return None
    parts: list[str] = []
    for piece in value.values:
        if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
            parts.append(piece.value)
        elif isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name) \
                and piece.value.id in constants and piece.format_spec is None \
                and piece.conversion == -1:
            parts.append(constants[piece.value.id])
        else:
            return None
    return "".join(parts)


def cypher_statements() -> dict[str, str]:
    """Every Cypher statement in `story/`, keyed by `module::name`, with its text resolved.

    Built from the docstring-stripped source, so what is collected is what the package can hand
    to a driver. An assignment that *contains* Cypher but is not fixed at import time is
    recorded with an empty text, which is how the interpolation test finds it — dropping it
    would make the offending case the one thing the scan cannot see.
    """
    found: dict[str, str] = {}
    for path in story_modules():
        tree = ast.parse(executable_source(path))
        constants = _module_string_constants(tree)
        module = path.relative_to(REPO_ROOT).as_posix()
        assigned: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not _holds_cypher(node.value):
                continue
            assigned.add(id(node.value))
            text = _fixed_text(node.value, constants)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[f"{module}::{target.id}"] = text or ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and _is_cypher(node.value) \
                    and id(node) not in assigned:
                found.setdefault(f"{module}::literal@{node.lineno}", node.value)
    return found


def _holds_cypher(value: ast.expr) -> bool:
    return any(_is_cypher(node.value)
               for node in ast.walk(value) if isinstance(node, ast.Constant))


def _without_brackets(statement: str) -> str:
    """Relationship types and list slices both live in `[...]`; labels never do."""
    return re.sub(r"\[[^\]]*\]", "[]", statement)


def _return_clauses(statement: str) -> list[str]:
    return re.findall(r"\bRETURN\b(.*?)(?:\bORDER BY\b|\bLIMIT\b|$)", statement, re.S)


def _pattern_variables(statement: str) -> set[str]:
    """Names bound to a node or a relationship by a pattern — the things `RETURN` may not name."""
    found = set(re.findall(r"\(\s*([a-z_][A-Za-z0-9_]*)\s*[:)]", statement))
    found |= set(re.findall(r"\[\s*([a-z_][A-Za-z0-9_]*)\s*:", statement))
    return found


STATEMENTS = cypher_statements()
STATEMENT_IDS = sorted(STATEMENTS)
RESOLVED_IDS = sorted(name for name, text in STATEMENTS.items() if text)


def test_the_scan_found_every_statement_this_step_declares_so_it_cannot_pass_vacuously() -> None:
    found_names = {key.split("::", 1)[1] for key in STATEMENTS}
    missing = EXPECTED_STATEMENT_NAMES - found_names
    assert not missing, f"the scan did not find {sorted(missing)}; every rule below would be vacuous"
    assert len(STATEMENTS) > len(EXPECTED_STATEMENT_NAMES), (
        f"the scan found only S1's own statements: {STATEMENT_IDS}")


def test_the_scan_reaches_cypher_outside_the_retrieval_stage() -> None:
    """§16's correction: scoping this to `retrieval/` left the stage that ships first uncovered."""
    outside = [key for key in STATEMENTS if "stages/retrieval/" not in key]
    assert outside, (
        "no Cypher was found outside `story/stages/retrieval/`, so this scan proves nothing "
        "about §7's freshness gate or S0c's adapter")


@pytest.mark.parametrize("name", STATEMENT_IDS)
def test_every_cypher_statement_in_the_package_is_fixed_at_import_time(name: str) -> None:
    """No caller value, no parameter, no attribute and no call may reach a query's text.

    Satisfied by a plain constant, or by an f-string whose holes are module-level string
    constants in the same file — in both cases the text is decided by the source and by nothing
    a caller does.
    """
    assert STATEMENTS[name], (
        f"{name} builds Cypher from something that is not a module-level literal; a value could "
        "reach the query text instead of arriving as a bound parameter")


@pytest.mark.parametrize(
    "name", [n for n in STATEMENT_IDS if "stages/retrieval/" in n])
def test_every_retrieval_statement_is_one_string_constant_with_no_interpolation_at_all(
    name: str,
) -> None:
    """S1's own stricter rule: the stage a model's choices flow into keeps its queries readable
    in one piece. `story/stages/retrieval/cypher.py` is the file a reviewer reads to check §16,
    and a statement assembled from three names is a statement they have to assemble too."""
    module, _, constant = name.partition("::")
    tree = ast.parse(executable_source(REPO_ROOT / module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == constant for t in node.targets):
            assert isinstance(node.value, ast.Constant), (
                f"{name} is not a single string constant")


@pytest.mark.parametrize("path", story_modules(), ids=lambda p: p.name)
def test_no_module_builds_a_cypher_statement_with_an_operator_or_a_method_call(
    path: Path,
) -> None:
    """`+`, `%`, `.format()`, `.join()` and `.replace()` near a statement are all the same bug."""
    tree = ast.parse(executable_source(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and _holds_cypher(node):
            raise AssertionError(f"{path.name}:{node.lineno} builds Cypher with an operator")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in {"format", "join", "replace"} and _holds_cypher(node.func):
            raise AssertionError(
                f"{path.name}:{node.lineno} builds Cypher with .{node.func.attr}()")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_no_cypher_statement_contains_a_write_clause(name: str) -> None:
    """§16's named rule, and the reason it is package-wide rather than stage-wide."""
    offenders = [keyword for keyword in WRITE_KEYWORDS
                 if re.search(rf"\b{re.escape(keyword)}\b", STATEMENTS[name])]
    assert not offenders, f"{name} contains {offenders}; the story layer is read-only (§16)"


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_every_cypher_statement_that_reads_rows_is_bounded(name: str) -> None:
    """A `LIMIT`, or a `RETURN` of aggregates alone, which is one row by construction.

    The second half is not a loophole and was not written to accommodate anything: a statement
    whose every returned field is `count(...)`/`collect(...)` with no grouping key returns
    exactly one row whatever the database holds, which is a stronger bound than a `LIMIT` and
    is stated in the language rather than in a number.
    """
    statement = STATEMENTS[name]
    if not _READS_ROWS.search(statement):
        return
    if re.search(r"\bLIMIT\b", statement):
        return
    clauses = _return_clauses(statement)
    fields = [field.strip() for clause in clauses for field in clause.split(",") if field.strip()]
    assert fields and all(
        any(aggregate in field for aggregate in _AGGREGATES) for field in fields), (
        f"{name} reads rows, has no LIMIT, and returns non-aggregate fields "
        f"{[f for f in fields if not any(a in f for a in _AGGREGATES)]}; §16 requires a hard "
        "bound on every statement that can return rows")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_no_cypher_statement_contains_a_variable_length_path(name: str) -> None:
    assert not re.search(r"\[\s*\w*\s*:\s*[A-Z_|]+\s*\*", STATEMENTS[name]), (
        f"{name} contains a variable-length pattern; §16 bounds path length at two hops "
        "structurally")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_no_cypher_statement_calls_apoc(name: str) -> None:
    assert "apoc." not in STATEMENTS[name].lower(), f"{name} calls APOC; §16 forbids it"


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_no_cypher_statement_returns_a_whole_node(name: str) -> None:
    """§9 requires a named field list, for two reasons and the second is easy to miss.

    A whole node blows §10.2's token budget, *and* it arrives as a `neo4j.graph.Node`, which
    S0c's `plain_value` refuses outright rather than flattening — so `RETURN o` is a runtime
    error dressed as a query. The check is against the variables the statement's own patterns
    bind, so `RETURN period_key` (introduced by a `WITH … AS period_key`) passes and
    `RETURN observation` does not.
    """
    statement = STATEMENTS[name]
    assert "{." not in statement, f"{name} uses a map projection; name the fields instead"
    bound = _pattern_variables(statement)
    for clause in _return_clauses(statement):
        for field in clause.split(","):
            head = field.strip().split(" AS ")[0].strip()
            assert head not in bound, (
                f"{name} returns the whole of {head!r}; §9 requires an explicit field list")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_no_cypher_statement_traverses_observation_of_subject(name: str) -> None:
    """§9: `opendoor` carries 2,704 of them, so one hop outward is the whole graph.

    Stronger than the plan's "never outward from `:Entity`": the edge is traversed by nothing at
    all, and subject identity is read from `Observation.subject_entity_id` instead — a property
    lookup, and the same answer.
    """
    assert "OBSERVATION_OF_SUBJECT" not in STATEMENTS[name], (
        f"{name} names OBSERVATION_OF_SUBJECT; the subject is a property, not a hop")


def test_only_the_counter_evidence_statement_reaches_issue_found_in_or_concerns_metric() -> None:
    """§9: `:Issue` is reachable from two entry points, and `find_counter_evidence` is S1's one."""
    for name, statement in STATEMENTS.items():
        touching = [token for token in ("Issue", "FOUND_IN", "CONCERNS_METRIC")
                    if token in statement]
        if not touching:
            continue
        assert name.endswith("::COUNTER_EVIDENCE"), (
            f"{name} names {touching}; §9 allows only find_counter_evidence to")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_every_cypher_statement_touching_issue_excludes_not_attempted(name: str) -> None:
    """10,852 of 17,130 issues record a question that was never asked.

    A retriever that surfaced them would report the run's own bounds as a finding about
    Opendoor — the most plausible-looking wrong answer this layer could produce.
    """
    statement = STATEMENTS[name]
    if ":Issue" not in statement:
        return
    assert re.search(r"NOT \w+:NotAttempted", statement), (
        f"{name} reads `:Issue` without excluding `:NotAttempted`")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_every_label_a_cypher_statement_names_is_on_the_allowlist(name: str) -> None:
    labels = set(re.findall(r":([A-Za-z][A-Za-z0-9]*)", _without_brackets(STATEMENTS[name])))
    assert labels <= ALLOWED_LABELS, (
        f"{name} names labels outside §9's allowlist: {sorted(labels - ALLOWED_LABELS)}")


@pytest.mark.parametrize("name", RESOLVED_IDS)
def test_every_relationship_type_a_cypher_statement_names_is_on_the_allowlist(name: str) -> None:
    types: set[str] = set()
    for segment in re.findall(r"\[[^\]]*\]", STATEMENTS[name]):
        types.update(re.findall(r":([A-Z][A-Z0-9_]*)", segment))
    assert types <= ALLOWED_RELATIONSHIP_TYPES, (
        f"{name} names relationship types outside §9's twelve: "
        f"{sorted(types - ALLOWED_RELATIONSHIP_TYPES)}")


@pytest.mark.parametrize("name", sorted(cypher.STATEMENTS))
def test_every_retrieval_statement_orders_by_a_unique_key_before_it_limits(name: str) -> None:
    """A `LIMIT` over an unordered result is a different 25 rows every run.

    The last sort key of every statement is unique within its result — an id, an `edge_key`, the
    parsed `passage_index`, or the aggregation key `period_key`, which `compare_metric_periods`
    produces exactly one row per. Without this, "deterministic ordering" is a claim about the
    database's scan order rather than about the query.
    """
    statement = cypher.STATEMENTS[name]
    order_by = re.search(r"\bORDER BY\b(.*?)\bLIMIT\b", statement, re.S)
    assert order_by, f"{name} limits without ordering"
    last_key = order_by.group(1).strip().rstrip(",").split(",")[-1].strip()
    assert last_key.endswith(("_id", "edge_key", "period_key", "passage_index")), (
        f"{name} orders last by {last_key!r}, which is not unique per row")


def test_every_retrieval_statement_binds_its_limit_to_a_parameter() -> None:
    """The bound comes from `MAX_ROWS`, never from the caller.

    A literal would be equally safe and equally hard to check against §9's table; a parameter
    means the two agree by construction and `graph_tools._read` is the only thing that fills it.
    """
    for name, statement in cypher.STATEMENTS.items():
        assert "LIMIT $row_limit" in statement, f"{name} does not bind its LIMIT"
