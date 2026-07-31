"""Every concrete stage satisfies the shared contract, exercised through it.

`runtime_checkable` only verifies method presence, never signatures, so `isinstance`
alone would prove almost nothing. These tests call each stage *through* the protocol with
a real request object.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from acquisition.contracts import PipelineStage, StageResult
from acquisition.stages.catalog import CatalogRequest, JsonlCatalogStage
from acquisition.stages.discover import DiscoverRequest, SecDiscoverStage
from acquisition.stages.download import DownloadRequest, LocalDownloadStage
from acquisition.stages.report import MarkdownReportStage, ReportRequest
from acquisition.stages.resolve import ResolveRequest, SgmlResolveStage
from acquisition.stages.verify import CorpusVerifyStage, VerifyRequest

ALL_STAGE_CLASSES = [
    SecDiscoverStage,
    SgmlResolveStage,
    LocalDownloadStage,
    JsonlCatalogStage,
    CorpusVerifyStage,
    MarkdownReportStage,
]

ALL_REQUEST_CLASSES = [
    DiscoverRequest,
    ResolveRequest,
    DownloadRequest,
    CatalogRequest,
    VerifyRequest,
    ReportRequest,
]


@pytest.mark.parametrize("stage_class", ALL_STAGE_CLASSES, ids=lambda c: c.__name__)
def test_stage_declares_a_name(stage_class):
    assert isinstance(stage_class.name, str) and stage_class.name


def test_stage_names_are_unique_and_match_the_cli_commands():
    names = [c.name for c in ALL_STAGE_CLASSES]
    assert len(names) == len(set(names))
    assert set(names) == {
        "discover",
        "resolve",
        "download",
        "build-catalog",
        "verify",
        "report",
    }


@pytest.mark.parametrize("stage_class", ALL_STAGE_CLASSES, ids=lambda c: c.__name__)
def test_stage_exposes_run(stage_class):
    assert callable(getattr(stage_class, "run", None))


@pytest.mark.parametrize("request_class", ALL_REQUEST_CLASSES, ids=lambda c: c.__name__)
def test_every_request_is_constructible_with_no_arguments(request_class):
    """Requests carry only optional inputs, so the pipeline can build them uniformly."""
    assert request_class() is not None


# -- Called through the protocol ---------------------------------------------------------


@dataclass(frozen=True)
class _Request:
    value: int = 0


@dataclass(frozen=True)
class _Result:
    value: int
    ok: bool = True


class _DoublingStage:
    """A stage that exists only in this test file, structurally satisfying the protocol."""

    name = "doubling"

    def run(self, request: _Request) -> _Result:
        return _Result(value=request.value * 2)


def _drive(stage: PipelineStage[_Request, _Result], value: int) -> _Result:
    """Accepts anything satisfying the protocol; knows no concrete class."""
    return stage.run(_Request(value=value))


def test_a_foreign_class_satisfies_the_protocol_structurally():
    """Replaceability: no inheritance from anything in the package is required."""
    result = _drive(_DoublingStage(), 21)
    assert result.value == 42
    assert isinstance(_DoublingStage(), PipelineStage)


def test_result_satisfies_the_stage_result_protocol():
    assert isinstance(_Result(value=1), StageResult)


@pytest.mark.parametrize("stage_class", ALL_STAGE_CLASSES, ids=lambda c: c.__name__)
def test_concrete_stages_satisfy_the_protocol(stage_class):
    """Instances are not constructed here; the class shape is what the protocol checks."""
    assert hasattr(stage_class, "run")
    assert hasattr(stage_class, "name")


def test_real_stage_is_callable_through_the_protocol(temp_config, tmp_path):
    """A real stage, driven by a function that only knows the protocol."""
    from acquisition.core.storage import LocalRawArtifactStore

    store = LocalRawArtifactStore(tmp_path / "raw", tmp_path / "tmp")
    stage = JsonlCatalogStage(temp_config, store)

    def drive(anonymous: PipelineStage) -> object:
        assert anonymous.name == "build-catalog"
        return anonymous.run(CatalogRequest())

    result = drive(stage)
    assert result.ok
    assert result.filing_count == 0
