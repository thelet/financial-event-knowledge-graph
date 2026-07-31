"""Pipeline wiring, storage atomicity, fallback policy, and import-direction rules."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from normalization.contracts import PipelineStage
from normalization.context import build_normalization_context, build_parser
from normalization.core.models import NormalizedDocument, NormalizedSection
from normalization.core.storage import LocalNormalizedStore
from normalization.pipeline import NormalizationPipeline, NormalizationRequest, STAGE_ORDER

PACKAGE = Path(__file__).resolve().parents[2] / "normalization"


# -- pipeline ----------------------------------------------------------------------------


class FakeResult:
    def __init__(self, ok=True):
        self.ok = ok
        self.for_processing = []
        self.documents = []
        self.issues = []
        self.comparisons = []
        self.issue_count = 0
        self.path = None


class FakeStage:
    def __init__(self, name, log, ok=True):
        self.name = name
        self._log = log
        self._ok = ok
        self.requests = []

    def run(self, request):
        self._log.append(self.name)
        self.requests.append(request)
        return FakeResult(self._ok)


def _pipeline(log, failing=None):
    stages = {n: FakeStage(n, log, ok=(n != failing)) for n in STAGE_ORDER}
    return NormalizationPipeline(
        select=stages["select"], normalize=stages["normalize"],
        issues=stages["write-issues"], catalog=stages["build-catalog"],
        verify=stages["verify"], report=stages["report"],
    ), stages


def test_stages_run_in_order():
    log = []
    pipeline, _ = _pipeline(log)
    result = pipeline.run(NormalizationRequest(run_id="r"))
    assert log == list(STAGE_ORDER)
    assert result.ok


@pytest.mark.parametrize("failing", STAGE_ORDER[:-1])
def test_failure_aborts_the_run(failing):
    log = []
    pipeline, _ = _pipeline(log, failing=failing)
    result = pipeline.run(NormalizationRequest(run_id="r"))
    stop = list(STAGE_ORDER).index(failing)
    assert log == list(STAGE_ORDER)[: stop + 1]
    assert result.failed_stage == failing


def test_fake_stages_can_be_injected():
    log = []
    pipeline, stages = _pipeline(log)
    pipeline.run(NormalizationRequest(run_id="r", mode="comparison"))
    assert stages["normalize"].requests[0].mode == "comparison"


def test_every_pipeline_stage_satisfies_the_protocol():
    context = build_normalization_context(run_id="t")
    for stage in context.pipeline.stages:
        assert isinstance(stage, PipelineStage)
        assert isinstance(stage.name, str) and stage.name


def test_context_selects_the_configured_parsers():
    context = build_normalization_context(run_id="t")
    assert context.default_parser.name == "sec_html"
    assert context.fallback_parser.name == "lxml"


def test_parser_can_be_replaced_at_the_composition_root():
    context = build_normalization_context(run_id="t", default_parser=build_parser("lxml", None)
                                          if False else None)
    swapped = build_normalization_context(
        run_id="t", default_parser=build_parser("lxml", context.config)
    )
    assert swapped.default_parser.name == "lxml"
    assert swapped.fallback_parser.name == "lxml"


# -- storage -----------------------------------------------------------------------------


def _document(doc_id="norm:0001801169:0001801169-26-000009:x.htm") -> NormalizedDocument:
    return NormalizedDocument(
        document_id=doc_id, source_artifact_id="sec:0001801169:0001801169-26-000009:x.htm",
        filing_id="f", cik=1801169, cik10="0001801169", company_name="Opendoor", form="8-K",
        form_sanitized="8-K", filing_date="2026-02-19", original_filename="x.htm",
        source_url="https://www.sec.gov/x", source_path="p", document_type="earnings_release",
        selection_policy_version="v1", parser_name="sec_html", parser_version="0.58.1",
        normalizer_version="1.0.0", config_hash="c" * 64, source_content_sha256="s" * 64,
        content_sha256="h" * 64, derivation_id="d" * 64,
        sections=[NormalizedSection(section_id="S0", section_sequence=0, title="root", level=0)],
    )


def test_finalize_writes_document_and_passages(tmp_path):
    store = LocalNormalizedStore(tmp_path / "norm", tmp_path / "tmp", "run")
    path = store.finalize(_document(), [])
    assert path.is_file()
    assert json.loads(path.read_text())["document_id"] == _document().document_id


def test_no_staging_debris_after_success(tmp_path):
    store = LocalNormalizedStore(tmp_path / "norm", tmp_path / "tmp", "run")
    store.finalize(_document(), [])
    assert not (tmp_path / "tmp").exists() or not any((tmp_path / "tmp").iterdir())


def test_interrupted_finalization_leaves_nothing_final(tmp_path):
    """A staging directory that never completes must not produce a readable document."""
    store = LocalNormalizedStore(tmp_path / "norm", tmp_path / "tmp", "run")
    staging = tmp_path / "tmp" / "run" / "partial"
    staging.mkdir(parents=True)
    (staging / "partial.passages.jsonl").write_text("")
    assert list(store.iter_documents()) == []


def test_rewriting_is_byte_identical(tmp_path):
    store = LocalNormalizedStore(tmp_path / "norm", tmp_path / "tmp", "run")
    first = store.finalize(_document(), []).read_bytes()
    second = store.finalize(_document(), []).read_bytes()
    assert first == second


# -- structural rules --------------------------------------------------------------------


def _modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = path.relative_to(PACKAGE).parts[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.startswith("normalization"):
                    out.add(node.module)
                continue
            base = list(parts)
            for _ in range(node.level - 1):
                if base:
                    base.pop()
            if node.module:
                base.extend(node.module.split("."))
            out.add(".".join(["normalization", *base]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("normalization"):
                    out.add(alias.name)
    return out


FORBIDDEN = (
    "graphiti", "neo4j", "py2neo", "cypher", "langchain", "llama_index", "openai",
    "anthropic", "sentence_transformers", "faiss", "chromadb", "qdrant", "pinecone",
    "tiktoken", "transformers", "torch", "pytesseract", "easyocr",
)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_graph_extraction_or_llm_imports(path):
    """v1 plan: normalization must not depend on a graph, an LLM, or an embedding model."""
    source = path.read_text(encoding="utf-8").lower()
    for name in FORBIDDEN:
        assert f"import {name}" not in source, f"{path.name} imports {name}"
        assert f"from {name}" not in source, f"{path.name} imports from {name}"


def test_normalization_never_imports_the_acquisition_package():
    """The on-disk corpus is the interface between the two packages."""
    for path in _modules():
        source = path.read_text(encoding="utf-8")
        assert "import acquisition" not in source
        assert "from acquisition" not in source


def test_core_and_utils_do_not_import_upward():
    for path in _modules():
        if path.parts[-2] not in ("core", "utils"):
            continue
        for target in _imports(path):
            assert not target.startswith("normalization.stages"), f"{path.name} -> {target}"
            assert not target.startswith("normalization.pipeline")
            assert not target.startswith("normalization.cli")


def test_contracts_has_no_internal_imports():
    assert _imports(PACKAGE / "contracts.py") == set()


def test_parser_types_stay_inside_the_parse_stage():
    """sec_parser must appear in exactly one module."""
    users = [p for p in _modules() if "sec_parser" in p.read_text(encoding="utf-8")
             and "import sec_parser" in p.read_text(encoding="utf-8")]
    assert [p.name for p in users] == ["sec_html_parser.py"]


def test_public_modules_carry_no_infrastructure_dependency():
    for path in _modules():
        if path.name != "public.py":
            continue
        for target in _imports(path):
            assert not target.endswith(".storage"), f"{path} -> {target}"
            assert not target.endswith(".manifests"), f"{path} -> {target}"


def test_no_import_cycles():
    graph: dict[str, set[str]] = {}
    for path in _modules():
        name = ".".join(["normalization", *path.relative_to(PACKAGE).with_suffix("").parts])
        name = name.removesuffix(".__init__")
        graph.setdefault(name, set()).update(t for t in _imports(path) if t != name)

    def reachable(start: str) -> set[str]:
        seen, stack = set(), list(graph.get(start, ()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph.get(current, ()))
        return seen

    for module in graph:
        assert module not in reachable(module), f"cycle involving {module}"
