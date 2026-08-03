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

#: Cypher's clause keywords. A statement in this repository begins with one of these, which is
#: the whole of the rule below — prose does not.
_CLAUSE_KEYWORDS = frozenset({
    "MATCH", "OPTIONAL", "RETURN", "WITH", "WHERE", "UNWIND", "CALL", "YIELD", "ORDER", "LIMIT",
    "SKIP", "UNION", "USING", "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP",
    "FOREACH", "LOAD",
})

#: The first word of a string, which is what decides whether it is a statement. A clause keyword
#: is followed by whitespace, an opening bracket, or the end of the fragment — the lookahead is
#: there because `story/core/models.py` holds the constant `"limit="`, whose first word is
#: `LIMIT` and which is a keyword-argument prefix rather than a bound on anything.
_FIRST_WORD = re.compile(r"^\s*([A-Za-z_]+)(?=\s|\(|$)")

#: A node pattern with a label, or a relationship bracket. The second way a string can be
#: Cypher: a *fragment* that an f-string will concatenate into a statement does not have to
#: begin with a clause keyword, and `story/stages/freshness/loaded_graph.py` holds four of them.
_PATTERN_MARKER = re.compile(
    r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_]|\(\s*:\s*[A-Za-z_]|-\[|\]->|<-\[")

#: WORKSTREAM_BOUNDARY §3 and §16, plus `DETACH` — `tests/story/test_story_freshness.py` already
#: names it and `DETACH DELETE` is the spelling a mutation reaches for first.
#:
#: **Matched case-*insensitively*, on a word boundary, against the upper-cased statement.** D4:
#: the case-sensitive form let `"match (n) set n.x = 1 return n"` through, and `EXPLAIN` on the
#: live server produced a `SetProperty` operator for it — Neo4j does not care about the
#: spelling and neither may this. The word boundary is what keeps the false positives out and
#: is unaffected by the case fold: `OFFSET_FROM_ANCHOR` has no `\bSET\b` in it because `F`
#: precedes the `S`, and `$DOCUMENT_TYPES` has no `\bCREATE\b`.
WRITE_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "LOAD CSV",
                  "FOREACH")

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


def non_docstring_strings(path: Path) -> list[tuple[int, str]]:
    """Every string literal in the file that is not a docstring, with its line.

    `executable_source` above answers the same question by re-unparsing, which loses the line
    numbers a failure message needs to be actionable. This keeps them, at the cost of the same
    docstring identification done twice — the same technique
    `tests/story/test_story_freshness.py:658` uses.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _first_word(text: str) -> str:
    match = _FIRST_WORD.match(text)
    return match.group(1).upper() if match else ""


def _is_cypher(value: object) -> bool:
    """A string this scan will collect and apply every structural rule to.

    **D4 replaced `\\bRETURN\\b` here**, which was two defects at once. It gated collection on a
    word a write statement need not contain — `"MATCH (n:Metric) DETACH DELETE n"` was not
    examined by *any* rule below — and it was one keyword where the language has many, so a
    statement with no `RETURN` escaped the `LIMIT` and label rules too.

    The replacement is the shape of a statement rather than one of its words: Cypher begins
    with a clause keyword. That is precise in both directions — it collects every statement in
    `story/` including a lower-cased one, and it leaves prose alone, which matters because
    `story/providers/neo4j_connection.py` raises with *"is a Node: return named properties
    instead … would drop its labels"*, a sentence containing both `return` and `drop` and no
    Cypher at all. Gating on the mere presence of a keyword would flag it and the fix would be
    to delete the explanation.
    """
    return isinstance(value, str) and _first_word(value) in _CLAUSE_KEYWORDS


def _could_be_a_statement(value: str) -> bool:
    """The wider net the write-clause scan casts, and only it.

    Deliberately over-inclusive relative to `_is_cypher`: it also catches a *fragment* carrying
    a labelled node pattern or a relationship bracket, because a write clause hidden in a piece
    of an f-string is the same bug as one in a whole statement. It is not used to collect
    statements, because a string like `"exactly one (:GraphLoad) node; found "` is a message and
    would fail the `LIMIT` rule for no reason. A false positive here costs one rewritten
    sentence; a false negative costs §16's read-only guarantee.
    """
    return _first_word(value) in _CLAUSE_KEYWORDS or bool(_PATTERN_MARKER.search(value))


def _write_keywords_in(statement: str) -> list[str]:
    """Every write clause the string names, case-folded and word-boundaried."""
    upper = statement.upper()
    return [keyword for keyword in WRITE_KEYWORDS
            if re.search(rf"\b{re.escape(keyword)}\b", upper)]


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

    A constant *inside* a collected assignment is not collected a second time. That mattered
    once `_is_cypher` widened past `RETURN` (D4): `story/stages/freshness/loaded_graph.py`
    builds its statements from pieces like `"MATCH (m:"`, and each piece would otherwise arrive
    here as a statement of its own and be asked, absurdly, for a `LIMIT`. The assembled text is
    already collected under the assignment's name and is where the rules belong.
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
            assigned.update(id(part) for part in ast.walk(node.value))
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
    offenders = _write_keywords_in(STATEMENTS[name])
    assert not offenders, f"{name} contains {offenders}; the story layer is read-only (§16)"


def test_no_string_constant_anywhere_in_the_package_could_be_a_write_statement() -> None:
    """The half of §16's read-only rule that does not depend on being collected as a statement.

    D4 found the previous scan evadable twice over, and the reviewer proved both by mutation
    against the live server: `"MATCH (n:Metric) DETACH DELETE n"` was skipped for want of a
    `RETURN`, and `"match (n) set n.x = 1 return n"` for want of upper case. `EXPLAIN` produced
    `DetachDelete` and `SetProperty` for them, so the server would have run either one.

    So this reads every non-docstring string constant in `story/` — assigned or not, collected
    as a statement or not, fragment or whole — and applies the keyword list case-folded to
    anything that could be part of a statement. `tests/story/test_story_freshness.py` was
    already doing exactly this for its own stage and was strictly stronger than §16's
    package-wide rule; this closes the gap the other way.
    """
    offences = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line} {sorted(found)}: {text[:80]!r}"
        for path in story_modules()
        for line, text in non_docstring_strings(path)
        if _could_be_a_statement(text) and (found := _write_keywords_in(text))
    ]
    assert offences == [], f"the story layer is read-only (§16): {offences}"


def test_the_write_clause_scan_catches_both_spellings_d4_found_it_missing() -> None:
    """The scan's own mutation test, so the two evasions cannot come back unnoticed.

    A structural rule with no test of its own is a rule that can be weakened by a one-character
    edit — which is what happened: `\\bRETURN\\b` and a case-sensitive keyword match each looked
    like a detail. Both mutations below are verbatim from the review, and the third is the
    sentence in `story/providers/neo4j_connection.py` that any cruder rule flags.
    """
    for mutation in ("MATCH (n:Metric) DETACH DELETE n",
                     "match (n) set n.x = 1 return n",
                     "MATCH (n) DETACH DELETE n",
                     "CREATE (n:Metric {metric_id: 'x'})",
                     "\nMERGE (m:Metric)\nRETURN m.metric_id AS metric_id\n"):
        assert _could_be_a_statement(mutation), f"{mutation!r} would not be scanned at all"
        assert _write_keywords_in(mutation), f"{mutation!r} is a write and was not flagged"

    prose = ("a returned field is a Node: return named properties instead. Flattening it here "
             "would drop its labels and its identity while still looking like a plain row")
    assert not _could_be_a_statement(prose), "the scan reads English as Cypher"


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


#: The statements `find_counter_evidence` owns. Two since D5: the second reads the scope an
#: empty first result cannot be interpreted without. The rule §9 states is about the *tool* that
#: may reach `:Issue`, so it is written as an allowlist of that tool's statements rather than as
#: one name — but it stays an allowlist, and a third statement has to be added here on purpose.
COUNTER_EVIDENCE_STATEMENTS = frozenset({"COUNTER_EVIDENCE", "COUNTER_EVIDENCE_SCOPE"})


def test_only_the_counter_evidence_statements_reach_issue_found_in_or_concerns_metric() -> None:
    """§9: `:Issue` is reachable from two entry points, and `find_counter_evidence` is S1's one."""
    for name, statement in STATEMENTS.items():
        touching = [token for token in ("Issue", "FOUND_IN", "CONCERNS_METRIC")
                    if token in statement]
        if not touching:
            continue
        assert name.rpartition("::")[2] in COUNTER_EVIDENCE_STATEMENTS, (
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
