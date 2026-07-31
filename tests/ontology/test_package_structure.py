"""Boundaries the package promises to keep, enforced rather than documented."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ontology.contracts import ConceptRegistry, Ontology, OntologyLoader

PACKAGE = Path(__file__).resolve().parents[2] / "ontology"

#: Dependencies whose absence is the point of this layer. It must stay usable with none of
#: them installed, so an extractor, a graph store or a UI can each pick their own.
FORBIDDEN_PREFIXES = (
    "anthropic", "openai", "langchain", "llama_index", "instructor", "dspy",
    "neo4j", "networkx", "rdflib", "owlready2", "sparqlwrapper",
    "chromadb", "qdrant_client", "pinecone", "weaviate", "faiss",
    "sentence_transformers", "transformers", "torch", "numpy", "pandas",
    "sqlalchemy", "psycopg", "sqlite3", "redis",
    "acquisition", "normalization",
)


def python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def executable_source(path: Path) -> str:
    """The module with comments and docstrings removed.

    Structural rules are about what the code *does*. A docstring naming `homes_sold` to
    explain why a rule exists is exactly the kind of comment this repository wants; a string
    literal `"homes_sold"` in a condition is the duplication it forbids.
    """
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


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_llm_graph_embedding_or_pipeline_dependency(path):
    forbidden = imported_modules(path) & set(FORBIDDEN_PREFIXES)
    assert not forbidden, f"{path.relative_to(PACKAGE)} imports {sorted(forbidden)}"


def test_the_package_imports_cleanly_on_its_own():
    """Transitive imports count too — a forbidden dependency two hops away still ships."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, ontology; "
         "loaded = set(sys.modules); "
         "bad = [m for m in loaded if m.split('.')[0] in "
         f"{FORBIDDEN_PREFIXES!r}]; "
         "print(sorted(bad))"],
        capture_output=True, text=True, cwd=PACKAGE.parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", f"transitively imported {result.stdout}"


def test_no_module_is_named_implementation_or_utils():
    names = {p.stem for p in python_files()} | {d.name for d in PACKAGE.rglob("*") if d.is_dir()}
    assert not names & {"implementation", "impl", "utils", "helpers", "misc"}


def test_only_the_composition_root_knows_which_versions_exist():
    """Every other module depends on the contract, so a second version costs one table entry."""
    offenders = [
        path.relative_to(PACKAGE)
        for path in python_files()
        if "versions" not in path.parts
        and path.name != "context.py"
        and "real_estate_marketplace_v1" in executable_source(path)
    ]
    assert not offenders


def test_no_concept_id_is_hard_coded_in_the_library():
    """YAML is authoritative; a second copy in Python is a copy that drifts."""
    library = [p for p in python_files() if "versions" not in p.parts]
    concept_ids = ("homes_sold", "adjusted_gross_profit", "contribution_margin",
                   "pct_homes_on_market_gt_120_days", "opendoor_accountable", "lender")
    offenders = {
        path.relative_to(PACKAGE): [c for c in concept_ids if c in executable_source(path)]
        for path in library
    }
    assert not {k: v for k, v in offenders.items() if v}


def test_outside_callers_never_need_the_loader():
    """`public.py` is the version's only entry point, exactly as each stage package has one."""
    for path in python_files():
        if path.name in {"loader.py", "public.py"} or "versions" in path.parts:
            continue
        assert "real_estate_marketplace_v1.loader" not in executable_source(path)


def test_implementations_satisfy_the_declared_protocols(ontology):
    assert isinstance(ontology, Ontology)
    assert isinstance(ontology.registry, ConceptRegistry)

    from ontology.versions.real_estate_marketplace_v1.loader import YamlDefinitionLoader

    assert isinstance(YamlDefinitionLoader(), OntologyLoader)


def test_yaml_is_where_the_concepts_live():
    version_dir = PACKAGE / "versions" / "real_estate_marketplace_v1"
    yaml_bytes = sum(p.stat().st_size for p in version_dir.glob("*.yaml"))
    python_bytes = sum(p.stat().st_size for p in version_dir.glob("*.py"))
    assert yaml_bytes > python_bytes * 3, "definitions have started migrating into Python"
