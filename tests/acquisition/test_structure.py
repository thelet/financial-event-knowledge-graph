"""Import-direction rules, enforced rather than documented.

    core, utils, contracts  <-  stages  <-  pipeline  <-  context  <-  cli

Prevents the layering from eroding silently, and catches the specific regression this
refactor fixed: verify and report importing from the catalog stage.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "acquisition"
STAGE_NAMES = {"discover", "resolve", "download", "catalog", "verify", "report"}


def _module_files() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _absolute_internal_imports(path: Path) -> set[str]:
    """Return dotted `acquisition.*` targets for every import in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package_parts = path.relative_to(PACKAGE).parts[:-1]
    targets: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.startswith("acquisition"):
                    targets.add(node.module)
                continue
            base = list(package_parts)
            for _ in range(node.level - 1):
                if base:
                    base.pop()
            if node.module:
                base.extend(node.module.split("."))
            targets.add(".".join(["acquisition", *base]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("acquisition"):
                    targets.add(alias.name)
    return targets


def _relative_name(path: Path) -> str:
    return ".".join(["acquisition", *path.relative_to(PACKAGE).with_suffix("").parts])


@pytest.mark.parametrize(
    "path", [p for p in _module_files() if p.parts[-2] == "core"], ids=lambda p: p.name
)
def test_core_imports_nothing_above_it(path):
    for target in _absolute_internal_imports(path):
        assert not target.startswith("acquisition.stages"), f"{path.name} -> {target}"
        for forbidden in ("acquisition.pipeline", "acquisition.cli", "acquisition.context"):
            assert not target.startswith(forbidden), f"{path.name} -> {target}"


@pytest.mark.parametrize(
    "path", [p for p in _module_files() if "utils" in p.parts], ids=lambda p: p.name
)
def test_utils_imports_nothing_from_the_application(path):
    for target in _absolute_internal_imports(path):
        assert target in {"acquisition", "acquisition.utils"} or target.startswith(
            "acquisition.utils"
        ), f"{path.name} -> {target}"


def test_contracts_has_no_internal_imports():
    assert _absolute_internal_imports(PACKAGE / "contracts.py") == set()


@pytest.mark.parametrize(
    "path",
    [p for p in _module_files() if "stages" in p.parts and p.name != "__init__.py"],
    ids=lambda p: f"{p.parts[-2]}/{p.name}",
)
def test_a_stage_never_imports_another_stage(path):
    own_stage = path.parts[-2]
    for target in _absolute_internal_imports(path):
        if not target.startswith("acquisition.stages."):
            continue
        other = target.split(".")[2]
        assert other == own_stage, (
            f"{own_stage}/{path.name} imports from stage {other!r}: {target}"
        )


@pytest.mark.parametrize(
    "path",
    [p for p in _module_files() if "stages" in p.parts],
    ids=lambda p: f"{p.parts[-2]}/{p.name}",
)
def test_stages_do_not_import_pipeline_context_or_cli(path):
    for target in _absolute_internal_imports(path):
        for forbidden in ("acquisition.pipeline", "acquisition.context", "acquisition.cli"):
            assert not target.startswith(forbidden), f"{path} -> {target}"


def test_pipeline_does_not_import_context_or_cli():
    for target in _absolute_internal_imports(PACKAGE / "pipeline.py"):
        assert not target.startswith("acquisition.context")
        assert not target.startswith("acquisition.cli")


def test_pipeline_imports_only_stage_public_surfaces():
    """The pipeline must not reach into a stage's internal modules."""
    for target in _absolute_internal_imports(PACKAGE / "pipeline.py"):
        if target.startswith("acquisition.stages."):
            parts = target.split(".")
            assert len(parts) == 3, f"pipeline reaches into {target}"


def test_cli_does_not_import_stage_internals():
    for target in _absolute_internal_imports(PACKAGE / "cli.py"):
        if target.startswith("acquisition.stages."):
            parts = target.split(".")
            assert len(parts) == 3, f"cli reaches into {target}"


def test_cli_does_not_import_infrastructure_directly():
    """The CLI gets everything through the context."""
    targets = _absolute_internal_imports(PACKAGE / "cli.py")
    for forbidden in ("acquisition.core.sec_client", "acquisition.core.storage"):
        assert forbidden not in targets


def test_no_import_cycles():
    graph: dict[str, set[str]] = {}
    for path in _module_files():
        name = _relative_name(path)
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        graph.setdefault(name, set()).update(
            t for t in _absolute_internal_imports(path) if t != name
        )

    def reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = list(graph.get(start, ()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph.get(current, ()))
        return seen

    for module in graph:
        assert module not in reachable(module), f"import cycle involving {module}"


def test_every_stage_package_exports_its_public_surface():
    for stage in sorted(STAGE_NAMES):
        init = PACKAGE / "stages" / stage / "__init__.py"
        assert init.is_file(), f"missing {init}"
        text = init.read_text(encoding="utf-8")
        assert "__all__" in text, f"{stage}/__init__.py declares no __all__"


def test_stage_internals_are_reachable_only_through_the_package():
    """Callers get what they need from the package, never from stage.py directly."""
    import importlib

    for stage in sorted(STAGE_NAMES):
        module = importlib.import_module(f"acquisition.stages.{stage}")
        exported = set(getattr(module, "__all__", ()))
        assert exported, f"{stage} exports nothing"
        assert any(name.endswith("Stage") for name in exported), (
            f"{stage} exports no stage class"
        )
        assert any(name.endswith("Request") for name in exported), (
            f"{stage} exports no request type"
        )


def test_every_stage_has_a_public_module():
    for stage in sorted(STAGE_NAMES):
        assert (PACKAGE / "stages" / stage / "public.py").is_file(), stage


@pytest.mark.parametrize("stage", sorted(STAGE_NAMES))
def test_public_never_imports_its_own_implementation(stage):
    """The contract must not depend on the implementation; the reverse is the rule."""
    public = PACKAGE / "stages" / stage / "public.py"
    for target in _absolute_internal_imports(public):
        assert not target.startswith(f"acquisition.stages.{stage}."), (
            f"{stage}/public.py imports {target}"
        )


@pytest.mark.parametrize("stage", sorted(STAGE_NAMES))
def test_public_carries_no_infrastructure_dependency(stage):
    """public.py may reference canonical models, never HTTP, storage, or manifests."""
    public = PACKAGE / "stages" / stage / "public.py"
    forbidden = {
        "acquisition.core.sec_client",
        "acquisition.core.storage",
        "acquisition.core.manifests",
        "acquisition.core.config",
        "acquisition.core.runmeta",
    }
    assert not (forbidden & _absolute_internal_imports(public)), stage


@pytest.mark.parametrize("stage", sorted(STAGE_NAMES))
def test_public_declares_a_stage_protocol(stage):
    import importlib

    module = importlib.import_module(f"acquisition.stages.{stage}.public")
    protocols = [
        name
        for name in dir(module)
        if name.endswith("Stage") and getattr(module, name).__module__ == module.__name__
    ]
    assert protocols, f"{stage}/public.py declares no stage protocol"


@pytest.mark.parametrize("stage", sorted(STAGE_NAMES))
def test_implementation_satisfies_its_own_stage_protocol(stage):
    """The concrete class and the protocol are both reachable from the package."""
    import importlib

    package = importlib.import_module(f"acquisition.stages.{stage}")
    exported = set(package.__all__)
    protocol_names = [
        n for n in exported if n.endswith("Stage") and not _is_concrete(package, n)
    ]
    concrete_names = [n for n in exported if n.endswith("Stage") and _is_concrete(package, n)]
    assert protocol_names, f"{stage} exports no protocol"
    assert concrete_names, f"{stage} exports no concrete implementation"
    for protocol in protocol_names:
        for concrete in concrete_names:
            cls = getattr(package, concrete)
            assert isinstance(cls.__new__(cls), getattr(package, protocol))


def _is_concrete(package, name: str) -> bool:
    import inspect

    obj = getattr(package, name)
    return inspect.isclass(obj) and not getattr(obj, "_is_protocol", False)


def test_resolve_keeps_its_parser_private():
    """sgml is an implementation detail of resolve and is not part of its surface."""
    import acquisition.stages.resolve as resolve

    assert "parse_index_headers" not in getattr(resolve, "__all__", ())
    assert "sgml" not in getattr(resolve, "__all__", ())
