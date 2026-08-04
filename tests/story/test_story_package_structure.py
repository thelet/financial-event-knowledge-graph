"""Boundaries the story package promises to keep, enforced rather than documented.

Mirrors `tests/graph/test_graph_package_structure.py` — the same `modules()`/`imports()`/
`closure()` machinery, the same argument. A rule nothing executes is a rule that quietly stops
holding, and every rule here is easy to satisfy today precisely because most of the package
does not exist yet. That is when it is worth writing down: the test is what will fail the day
the retrieval stage is added in the wrong package.

**The forbidden-import rule is checked transitively, not by grepping one file.** A driver or a
framework that arrived through `story.core.keys -> extraction.core.something -> neo4j` would
satisfy a per-file check and still put a Bolt connection inside a stage the plan says must run
with no database. The closure of first-party imports is walked and every module in it is
checked. The traversal stops at third-party packages deliberately: this suite must not need
`pydantic`'s own import graph to be stable to tell whether the story layer reached for a
framework.

**The upstream-never-imports-story rule lives here and not in the other packages' tests.**
WORKSTREAM_BOUNDARY §2: the factual-spine session owns `tests/` except `tests/story/`, so
extending `tests/graph/test_graph_package_structure.py::test_upstream_packages_never_import_graph`
would be an edit to a file the other workstream owns. Walking `graph/`, `extraction/`,
`ontology/`, `normalization/` and `acquisition/` from *this* file asserts the same thing and
belongs to nobody else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "story"
FIRST_PARTY = ("story", "graph", "extraction", "ontology", "normalization", "acquisition")

#: The frameworks and libraries this package is defined by *not* needing (V1_STORY_AGENT §1
#: "Out", §4, §5). The first row is the graph-RAG landscape the plan rejects by name: adopting
#: one of them is a design decision, not an import, and it must not be able to arrive as one.
#:
#: **`neo4j` is deliberately not in this tuple** — it has its own pair of rules below, because
#: it is the one dependency this package *will* take, behind exactly one module. Folding it in
#: here would mean editing this list the day the adapter lands, which is the edit that turns a
#: boundary into a formality.
FORBIDDEN = (
    "lightrag", "neo4j_graphrag", "llama_index", "llamaindex", "langchain", "langchain_core",
    "graphrag", "graphiti", "haystack", "dspy", "instructor", "guidance",
    "py2neo", "networkx", "rdflib", "owlready2",
    "chromadb", "qdrant_client", "pinecone", "weaviate", "faiss", "lancedb",
    "openai", "anthropic", "cohere", "tiktoken",
    "sentence_transformers", "transformers", "torch", "numpy", "pandas", "scipy", "sklearn",
    "sqlalchemy", "psycopg", "redis",
)

#: The driver, and the one module allowed to name it. A **path**, not a dotted name, so a
#: module moved out of it loses the exemption automatically — `graph`'s own rule, restated.
#:
#: The founder decision that placed it here is worth recording: the adapter is *not* in
#: `story/core/`, because `core/` must stay independent of both the driver and the
#: environment, and it is *not* borrowed from `graph/stages/load/connection.py`, because
#: `graph.stages` is not a surface this package may import (WORKSTREAM_BOUNDARY §4).
DRIVER = "neo4j"
DRIVER_OWNING_MODULE = PACKAGE / "providers" / "neo4j_connection.py"

#: What `story/` may import from the packages before it (WORKSTREAM_BOUNDARY §4,
#: IMPLEMENTATION_STEPS §2). `extraction.core` and `ontology.core` carry shared *meaning* —
#: ids, period keys, the ontology's own models — and `graph.core`/`graph.contracts` carry the
#: projection's vocabulary, which the freshness gate and the retrieval layer both read. The
#: boundary document marked the two `graph` surfaces *(unverified)* pending this test; they
#: are verified by it now.
ALLOWED_UPSTREAM = ("extraction.core.", "ontology.core.", "ontology.contracts",
                    "graph.core.", "graph.contracts")
ALLOWED_UPSTREAM_EXACT = ("ontology",)

#: Never allowed, and named separately from "not on the allowlist" so a failure says which
#: rule broke. `extraction.contracts` is on this list even though it holds `GenerationResult`
#: — §15.2's whole argument is that the story provider restates that type rather than
#: importing it.
FORBIDDEN_UPSTREAM = ("extraction.stages", "extraction.providers", "extraction.contracts",
                      "graph.stages", "normalization.", "acquisition.")

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


STORY_MODULES = modules(PACKAGE)


# -- rule 0: a guard on the guards ----------------------------------------------------------


def test_the_story_modules_this_file_walks_exist():
    """An empty parametrization would pass every rule below without examining anything."""
    assert len(STORY_MODULES) >= 5
    assert {p.name for p in STORY_MODULES} >= {
        "__init__.py", "contracts.py", "models.py", "keys.py", "manifest.py"}
    assert (PACKAGE / "contracts.py").is_file()
    assert (PACKAGE / "core" / "models.py").is_file()


def test_the_closure_actually_leaves_the_story_package():
    """The transitive checks are only meaningful if the traversal reaches upstream code."""
    reached = closure(STORY_MODULES)
    assert any(name.startswith("extraction.") for name in reached), sorted(reached)


# -- rule 1: no framework, driver or numeric library, transitively ---------------------------


def test_no_forbidden_dependency_is_reachable_from_any_story_module():
    """Walk what `story/` actually reaches — upstream included — and check all of it.

    §1 of IMPLEMENTATION_STEPS lists LightRAG, `neo4j-graphrag`, LlamaIndex, Graphiti and
    Microsoft GraphRAG as out of scope and *not to be started*. An import is how each of them
    would start.
    """
    reached = closure(STORY_MODULES)
    offences: list[str] = []
    for name, path in sorted(reached.items()):
        for imported in sorted(imports(path)):
            if imported.split(".")[0] in FORBIDDEN:
                offences.append(f"{name} imports {imported}")
    assert offences == []


@pytest.mark.parametrize("path", STORY_MODULES, ids=lambda p: p.name)
def test_no_story_module_names_a_framework(path):
    """Direct imports, over the whole package including `providers/`.

    A framework import is forbidden everywhere and forever — there is no module that gets an
    exemption, unlike the driver below, because adopting LightRAG or LlamaIndex is a scope
    decision §1 already made and not a dependency any one module needs.
    """
    for imported in imports(path):
        assert imported.split(".")[0] not in FORBIDDEN, f"{path.name} imports {imported}"


# -- rule 1b: the driver lives behind one module ----------------------------------------------


def test_the_driver_is_named_only_in_the_story_owned_connection_module():
    """`neo4j` may be imported from `story/providers/neo4j_connection.py`, and it must be.

    Written at S0 before the module existed, because this is the rule that has to hold the day
    it does. **S0c completed the second half**: the "somebody must actually import it"
    assertion that `tests/graph/test_graph_package_structure.py::
    test_the_driver_is_named_only_inside_the_load_stage` carries is now here too. Without it
    the rule is satisfiable by deleting the adapter, which is a different package than the one
    the plan describes.
    """
    assert STORY_MODULES, "no story module was walked; the rule below proves nothing"
    importers = {
        path.relative_to(PACKAGE).as_posix()
        for path in STORY_MODULES
        if any(imported.split(".")[0] == DRIVER for imported in imports(path))
    }
    assert importers == {DRIVER_OWNING_MODULE.relative_to(PACKAGE).as_posix()}, (
        f"the driver must be imported by the connection module and by nothing else, "
        f"found {sorted(importers)}")


def test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage():
    """Transitively, the half a per-file check cannot see.

    A driver that arrived through `story.core.keys -> extraction.core.something -> neo4j`
    would satisfy the test above and still put a Bolt connection inside `core/`, which §5
    requires to be constructible with no database.

    **Corrected at S0c; the S0 spelling was impossible to satisfy.** It walked *everything
    except the connection module* and asserted the closure reached no driver — but D1 requires
    `story/context.py` to construct the executor, so the composition root imports the adapter
    by design and the closure from it reaches `neo4j` in one hop. The rule as written could
    only be kept by a package with no composition root, or by hiding the import behind
    `importlib`, which would defeat the import-graph check it belongs to.

    The set walked is now the one that must stay driver-free — the contract, `core/` and every
    stage — which is exactly how `tests/graph/test_graph_package_structure.py::
    test_no_driver_is_reachable_from_core_or_projection` scopes the same rule (`DATABASE_FREE`
    is `graph/core` plus `graph/stages/projection`, not "everything but the loader"). The
    guarantee is unchanged and a *stage* that imported the adapter still fails here.

    **Widened at D6, for the same reason and no further.** `story/cli.py` calls
    `build_story_context` — it is the only module in the package that builds one — so the
    closure from it reaches the composition root and then the driver in two hops;
    `story/__main__.py` reaches it in three, and `story/pipeline.py` names `StoryContext` for
    the type of its argument. The S0c correction's own argument applies unchanged: the rule as
    written could only be kept by a package with no command-line entry point, and
    `TOP_LEVEL_MODULES` below already declares all four of these files as part of the layout.

    **`pipeline.py` is exempt even though its import is under `TYPE_CHECKING`**, because
    `imports()` reads the AST and an `ast.walk` sees a guarded import exactly as it sees any
    other — correctly, since a rule that could be satisfied by indenting an import would not be
    a rule. The guard stays in `pipeline.py` as the honest statement of a module that
    constructs no context; the exemption here is what makes the statement checkable rather than
    what makes it true.

    The exemption is a list of four named composition and entry modules rather than a
    directory, and the walked set is what the rule is about: `contracts.py`, all of `core/`,
    and every stage. A stage or a `core/` module that reached the adapter still fails here, and
    `test_the_composition_root_is_the_only_module_outside_providers_that_reaches_the_adapter`
    keeps the *direct* import at one file.
    """
    exempt = (DRIVER_OWNING_MODULE, PACKAGE / "context.py", PACKAGE / "cli.py",
              PACKAGE / "pipeline.py", PACKAGE / "__main__.py")
    driver_free = [p for p in STORY_MODULES if p not in exempt]
    assert driver_free, "nothing to walk"
    assert any(p.is_relative_to(PACKAGE / "core") for p in driver_free)
    offences = [
        f"{name} imports {imported}"
        for name, path in sorted(closure(driver_free).items())
        for imported in sorted(imports(path))
        if imported.split(".")[0] == DRIVER
    ]
    assert offences == []


def test_the_composition_root_is_the_only_module_outside_providers_that_reaches_the_adapter():
    """D1's runtime half: no retrieval tool, no detector and nothing in `core/` builds a driver.

    The exemption the test above grants `story/context.py` is bounded here rather than left
    open. `story.providers.neo4j_connection` may be imported by the composition root and by
    nothing else in the package; a stage that wanted an executor takes one as an argument.
    """
    adapter = "story.providers.neo4j_connection"
    importers = {
        path.relative_to(PACKAGE).as_posix()
        for path in STORY_MODULES
        if path != DRIVER_OWNING_MODULE and adapter in imports(path)
    }
    assert importers == {"context.py"}, sorted(importers)


def test_the_driver_never_reaches_the_contract_or_the_core():
    """The half of the boundary that must survive S0c unchanged.

    `contracts.py` declares `ReadQueryExecutor` as a *structural* type — the reason the
    protocol exists is that a stage can be written and tested against it with no driver
    installed. The moment either this file or `core/` names `neo4j`, that stops being true.
    """
    protected = [p for p in STORY_MODULES
                 if p.name == "contracts.py" or p.is_relative_to(PACKAGE / "core")]
    assert len(protected) >= 5, "the protected set is empty or shrank unexpectedly"
    for path in protected:
        for imported in imports(path):
            assert imported.split(".")[0] != DRIVER, f"{path.name} imports {imported}"


def test_no_story_module_opens_an_http_connection():
    """§5 confines `httpx` to `story/providers/`, which does not exist at S0.

    Split from `FORBIDDEN` rather than folded into it because the two rules have different
    futures: a framework import stays forbidden forever, while `httpx` becomes legal in one
    package at S6. Written as a path so a module moved out of `providers/` loses the exemption
    automatically.
    """
    provider_package = PACKAGE / "providers"
    offenders = {
        path.relative_to(PACKAGE).as_posix()
        for path in STORY_MODULES
        if not path.is_relative_to(provider_package)
        and any(imported.split(".")[0] in ("httpx", "requests", "urllib3", "aiohttp")
                for imported in imports(path))
    }
    assert offenders == set()


# -- rule 2: contracts.py depends on almost nothing -------------------------------------------


def test_contracts_imports_only_typing_and_this_packages_models():
    """No driver, no HTTP client, no configuration, no storage — and nothing else either."""
    assert imports(PACKAGE / "contracts.py") == {
        "__future__", "typing", "story.core.models"}


# -- rule 3: the dependency runs one way ------------------------------------------------------


@pytest.mark.parametrize(
    "package", ["acquisition", "normalization", "ontology", "extraction", "graph"])
def test_upstream_packages_never_import_story(package):
    """The factual spine must build, test and ship with `story/` deleted.

    Walked from this file rather than added to each package's own structural test, because
    WORKSTREAM_BOUNDARY §2 gives those files to the other session and *"there is no file both
    workstreams edit."*
    """
    for path in modules(REPO_ROOT / package):
        for imported in imports(path):
            assert imported.split(".")[0] != "story", f"{path} imports {imported}"


# -- rule 4: only upstream's shared surfaces --------------------------------------------------


@pytest.mark.parametrize("path", STORY_MODULES, ids=lambda p: p.name)
def test_story_reaches_only_shared_upstream_meaning(path):
    for imported in sorted(imports(path)):
        head = imported.split(".")[0]
        if head not in FIRST_PARTY or head == "story":
            continue
        allowed = (imported in ALLOWED_UPSTREAM_EXACT
                   or imported.startswith(ALLOWED_UPSTREAM))
        assert allowed, f"{path.name} imports {imported}, which is not a shared surface"


@pytest.mark.parametrize("path", STORY_MODULES, ids=lambda p: p.name)
def test_story_never_reaches_into_an_upstream_stage_or_private_contract(path):
    for imported in imports(path):
        for forbidden in FORBIDDEN_UPSTREAM:
            assert not imported.startswith(forbidden), f"{path.name}: {imported}"


# -- rule 5: stages are independent, core knows about none of them, and there are no cycles ---


def stage_of(name: str) -> str | None:
    parts = name.split(".")
    return parts[2] if len(parts) > 2 and parts[:2] == ["story", "stages"] else None


@pytest.mark.parametrize("path", STORY_MODULES, ids=lambda p: p.name)
def test_no_stage_imports_another_stage(path):
    own = stage_of(module_name(path))
    if own is None:
        return
    for imported in imports(path):
        other = stage_of(imported)
        assert other is None or other == own, f"{path.name} imports stage {other}"


@pytest.mark.parametrize("path", STORY_MODULES, ids=lambda p: p.name)
def test_core_never_imports_a_stage_a_provider_or_the_cli(path):
    if not path.is_relative_to(PACKAGE / "core"):
        return
    for imported in imports(path):
        for forbidden in ("story.stages", "story.providers", "story.cli", "story.pipeline",
                          "story.context"):
            assert not imported.startswith(forbidden), f"{path.name} imports {imported}"


def test_the_story_package_has_no_import_cycle():
    """Depth-first over the package's own modules, reporting the cycle it found."""
    edges = {module_name(path): {i for i in imports(path) if i.startswith("story")}
             for path in STORY_MODULES}
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


# -- rule 6: every module and every directory is named for what it is --------------------------


@pytest.mark.parametrize("path", STORY_MODULES, ids=lambda p: p.name)
def test_no_catch_all_module(path):
    assert path.name not in CATCH_ALL_NAMES, f"{path} is named for nothing in particular"


def test_no_directory_exists_as_an_empty_placeholder():
    """A stage directory exists only once something is in it.

    `story/stages/` is absent at S0, and that is the assertion: an empty package would put a
    directory in the layout that no test can constrain, and a `stages/__init__.py` beside no
    stage is a promise rather than a boundary. The rule survives S1 unchanged — it says every
    directory that exists holds a module, not that a particular set exists.
    """
    empty = [
        directory.relative_to(REPO_ROOT).as_posix()
        for directory in PACKAGE.rglob("*")
        if directory.is_dir() and directory.name != "__pycache__"
        and not [p for p in directory.rglob("*.py")
                 if "__pycache__" not in p.parts and p.name != "__init__.py"]
    ]
    assert empty == [], f"directories holding nothing but an __init__.py: {empty}"


#: §5's layout, at the top level. Everything else belongs to a stage, to the providers, or to
#: `core/`; a seventh module sitting beside `contracts.py` is how a package acquires a
#: `pipeline`-shaped thing nobody decided to add.
TOP_LEVEL_MODULES = {"__init__.py", "contracts.py", "context.py", "pipeline.py", "cli.py",
                     "__main__.py"}
DECLARED_SUBPACKAGES = {"core", "stages", "providers"}


def test_the_package_layout_is_the_one_the_plan_declares():
    """Every module is a declared top-level one or lives in a declared subpackage (§5).

    Deliberately not a frozen file list: S1 through S12 add modules and a test that had to be
    edited at every step would be edited without being read. What it holds fixed is the
    *shape* — the six names §5 puts at the top level, and the three subpackages under them.
    """
    stray = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in STORY_MODULES
        if (len(path.relative_to(PACKAGE).parts) == 1
            and path.name not in TOP_LEVEL_MODULES)
        or (len(path.relative_to(PACKAGE).parts) > 1
            and path.relative_to(PACKAGE).parts[0] not in DECLARED_SUBPACKAGES)
    )
    assert stray == []


def test_s0_created_the_six_modules_it_owns():
    """The step's `Owns` column, executable. Additive: later steps add, they do not remove."""
    present = {p.relative_to(PACKAGE).as_posix() for p in STORY_MODULES}
    assert present >= {"__init__.py", "contracts.py", "core/__init__.py", "core/keys.py",
                       "core/manifest.py", "core/models.py"}
