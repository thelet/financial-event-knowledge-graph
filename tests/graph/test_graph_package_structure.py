"""Boundaries the graph package promises to keep, enforced rather than documented.

V1_GRAPH_PROTOTYPE §11 lists five structural rules to add at G1, and a rule nothing executes
is a rule that quietly stops holding — the same argument
`tests/ontology/test_package_structure.py` and `tests/normalization/test_pipeline_structure.py`
make for their own layers. All five are here, plus two the layout implies: `core/` may not
import a stage, and no module may be named after nothing in particular.

**The `neo4j` rule is checked transitively, not by grepping one file.** A driver that arrived
through `graph.core.inputs -> extraction.core.something -> neo4j` would satisfy a per-file
check and still put a Bolt connection inside a stage the plan says must run with no database.
So the closure of first-party imports is walked and every module in it is checked. The
traversal deliberately stops at third-party packages: this suite must not need `pydantic`'s
own import graph to be stable to tell whether the graph layer reached for a driver.

At G1 the rule is easy to satisfy — `graph/stages/load/` does not exist yet — and that is
exactly when it is worth writing down, because the test is what will fail the day the loader
is added in the wrong package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "graph"
FIRST_PARTY = ("graph", "extraction", "ontology", "normalization", "acquisition")

#: Drivers, clients and stores the projection is defined by *not* needing (§4.4, §11). `neo4j`
#: heads the list because it is the one this package will eventually depend on — in exactly
#: one stage that does not exist yet.
FORBIDDEN = (
    "neo4j", "py2neo", "graphiti", "networkx", "rdflib", "owlready2",
    "chromadb", "qdrant_client", "pinecone", "weaviate", "faiss",
    "openai", "anthropic", "langchain", "llama_index", "instructor",
    "sentence_transformers", "transformers", "torch", "numpy", "pandas",
    "sqlalchemy", "psycopg", "redis",
)

#: What `graph/` may import from the packages before it. `extraction.core` and `ontology.core`
#: carry shared *meaning* — ids, period keys, the ontology's own models — and are the surface
#: three packages already read each other through. `extraction.stages` is extraction's
#: internals, and a graph that reached into one would break the day a lane is rewritten.
ALLOWED_UPSTREAM = ("extraction.core.", "ontology.core.", "ontology.contracts")
ALLOWED_UPSTREAM_EXACT = ("ontology",)

#: Names that describe nothing. The repository's own rule: a module is named for what it is.
CATCH_ALL_NAMES = ("implementation.py", "service.py", "helpers.py", "utils.py", "misc.py",
                   "common.py", "base.py")


def modules(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: Path) -> str:
    relative = path.resolve().relative_to(REPO_ROOT)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def imports(path: Path) -> set[str]:
    """Every module this file imports, with relative imports resolved to absolute names."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    own = module_name(path).split(".")
    package_parts = own if (path.name == "__init__.py") else own[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    found.add(node.module)
                continue
            base = list(package_parts)
            for _ in range(node.level - 1):
                if base:
                    base.pop()
            if node.module:
                base.extend(node.module.split("."))
            found.add(".".join(base))
    return found


def source_file(dotted: str) -> Path | None:
    """The file a first-party dotted name lives in, or `None` for anything third-party."""
    if dotted.split(".")[0] not in FIRST_PARTY:
        return None
    as_module = REPO_ROOT / Path(*dotted.split(".")).with_suffix(".py")
    if as_module.is_file():
        return as_module
    as_package = REPO_ROOT / Path(*dotted.split(".")) / "__init__.py"
    return as_package if as_package.is_file() else None


def closure(paths: list[Path]) -> dict[str, Path]:
    """Every first-party module reachable from `paths`, including the roots themselves."""
    reached: dict[str, Path] = {}
    queue = list(paths)
    while queue:
        path = queue.pop()
        name = module_name(path)
        if name in reached:
            continue
        reached[name] = path
        for dotted in imports(path):
            resolved = source_file(dotted)
            if resolved is not None and module_name(resolved) not in reached:
                queue.append(resolved)
    return reached


GRAPH_MODULES = modules(PACKAGE)
DATABASE_FREE = [p for p in GRAPH_MODULES
                 if p.is_relative_to(PACKAGE / "core") or
                 p.is_relative_to(PACKAGE / "stages" / "projection")]


def test_the_database_free_modules_exist():
    """A guard on the guards: an empty parametrization would pass every rule below."""
    assert len(DATABASE_FREE) >= 6
    assert {p.name for p in DATABASE_FREE} >= {
        "models.py", "inputs.py", "keys.py", "derivation.py", "manifest.py",
        "nodes.py", "edges.py", "export.py"}


# -- rule 1: no driver under core/ or projection/, transitively -----------------------------


def test_no_driver_is_reachable_from_core_or_projection():
    """Walk what `graph/core` and `graph/stages/projection` actually reach, and check it all."""
    reached = closure(DATABASE_FREE)
    offences: list[str] = []
    for name, path in sorted(reached.items()):
        for imported in sorted(imports(path)):
            if imported.split(".")[0] in FORBIDDEN:
                offences.append(f"{name} imports {imported}")
    assert offences == []


def test_the_closure_actually_leaves_the_graph_package():
    """The transitive check is only meaningful if the traversal reaches upstream code."""
    reached = closure(DATABASE_FREE)
    assert any(name.startswith("extraction.") for name in reached)
    assert any(name.startswith("ontology.") for name in reached)


@pytest.mark.parametrize("path", GRAPH_MODULES, ids=lambda p: p.name)
def test_no_graph_module_names_a_driver(path):
    """Direct imports, over the whole package including `context`, `pipeline` and `cli`."""
    for imported in imports(path):
        assert imported.split(".")[0] not in FORBIDDEN, f"{path.name} imports {imported}"


# -- rule 2: contracts.py depends on almost nothing -----------------------------------------


def test_contracts_imports_only_typing_and_this_packages_models():
    """No driver, no HTTP client, no configuration, no storage — and nothing else either."""
    assert imports(PACKAGE / "contracts.py") == {"__future__", "typing", "graph.core.models"}


# -- rule 3: the packages before this one do not import it ----------------------------------


@pytest.mark.parametrize("package", ["acquisition", "normalization", "ontology", "extraction"])
def test_upstream_packages_never_import_graph(package):
    """The dependency runs one way: the graph is a consumer of catalogs, never a producer."""
    for path in modules(REPO_ROOT / package):
        for imported in imports(path):
            assert imported.split(".")[0] != "graph", f"{path} imports {imported}"


# -- rule 4: only upstream's public surfaces ------------------------------------------------


@pytest.mark.parametrize("path", GRAPH_MODULES, ids=lambda p: p.name)
def test_graph_reaches_only_shared_upstream_meaning(path):
    for imported in sorted(imports(path)):
        head = imported.split(".")[0]
        if head not in FIRST_PARTY or head == "graph":
            continue
        allowed = (imported in ALLOWED_UPSTREAM_EXACT
                   or imported.startswith(ALLOWED_UPSTREAM))
        assert allowed, f"{path.name} imports {imported}, which is not a shared surface"


@pytest.mark.parametrize("path", GRAPH_MODULES, ids=lambda p: p.name)
def test_graph_never_reaches_into_an_upstream_stage(path):
    for imported in imports(path):
        assert not imported.startswith("extraction.stages"), f"{path.name}: {imported}"
        assert not imported.startswith("normalization."), f"{path.name}: {imported}"
        assert not imported.startswith("acquisition."), f"{path.name}: {imported}"


# -- rule 5: stages are independent, and there are no cycles --------------------------------


def stage_of(name: str) -> str | None:
    parts = name.split(".")
    return parts[2] if len(parts) > 2 and parts[:2] == ["graph", "stages"] else None


@pytest.mark.parametrize("path", GRAPH_MODULES, ids=lambda p: p.name)
def test_no_stage_imports_another_stage(path):
    own = stage_of(module_name(path))
    if own is None:
        return
    for imported in imports(path):
        other = stage_of(imported)
        assert other is None or other == own, f"{path.name} imports stage {other}"


@pytest.mark.parametrize("path", GRAPH_MODULES, ids=lambda p: p.name)
def test_core_never_imports_a_stage(path):
    if not path.is_relative_to(PACKAGE / "core"):
        return
    for imported in imports(path):
        assert not imported.startswith("graph.stages"), f"{path.name} imports {imported}"


def test_the_graph_package_has_no_import_cycle():
    """Depth-first over the package's own modules, reporting the cycle it found."""
    edges = {module_name(path): {i for i in imports(path) if i.startswith("graph.")}
             for path in GRAPH_MODULES}
    edges = {name: {i for i in targets if i in edges} for name, targets in edges.items()}

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(name: str, trail: list[str]) -> list[str] | None:
        if name in visiting:
            return trail[trail.index(name):] + [name]
        if name in done:
            return None
        visiting.add(name)
        for target in sorted(edges[name]):
            cycle = walk(target, trail + [name])
            if cycle:
                return cycle
        visiting.discard(name)
        done.add(name)
        return None

    for name in sorted(edges):
        cycle = walk(name, [])
        assert cycle is None, " -> ".join(cycle)


# -- rule 6: every module is named for what it is -------------------------------------------


@pytest.mark.parametrize("path", GRAPH_MODULES, ids=lambda p: p.name)
def test_no_catch_all_module(path):
    assert path.name not in CATCH_ALL_NAMES, f"{path} is named for nothing in particular"


def test_the_stages_package_holds_only_projection_at_g1():
    """`load` and `verify` are G2/G3 and are absent rather than empty.

    An empty package would put a directory in the layout that no test can constrain, and the
    one thing §11 is emphatic about — the driver lives in exactly one stage — is easiest to
    keep true while that stage does not exist.
    """
    stages = sorted(p.name for p in (PACKAGE / "stages").iterdir()
                    if p.is_dir() and p.name != "__pycache__")
    assert stages == ["projection"]
