"""Composition root wiring, and CLI delegation to stages and the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from acquisition import cli
from acquisition.context import AcquisitionContext, build_acquisition_context
from acquisition.core.manifests import ManifestRepository
from acquisition.core.storage import LocalRawArtifactStore
from acquisition.pipeline import AcquisitionPipeline
from acquisition.stages.catalog import JsonlCatalogStage
from acquisition.stages.discover import SecDiscoverStage
from acquisition.stages.download import LocalDownloadStage
from acquisition.stages.report import MarkdownReportStage
from acquisition.stages.resolve import SgmlResolveStage
from acquisition.stages.verify import CorpusVerifyStage


# -- Context -----------------------------------------------------------------------------


class _FakeClient:
    request_count = 0
    closed = False

    def close(self) -> None:
        self.closed = True


def _context(temp_config, **overrides) -> AcquisitionContext:
    overrides.setdefault("client", _FakeClient())
    return build_acquisition_context(config=temp_config, **overrides)


def test_context_constructs_the_current_implementations(temp_config):
    context = _context(temp_config)
    assert isinstance(context.discover, SecDiscoverStage)
    assert isinstance(context.resolve, SgmlResolveStage)
    assert isinstance(context.download, LocalDownloadStage)
    assert isinstance(context.catalog, JsonlCatalogStage)
    assert isinstance(context.verify, CorpusVerifyStage)
    assert isinstance(context.report, MarkdownReportStage)
    assert isinstance(context.pipeline, AcquisitionPipeline)


def test_context_builds_infrastructure_from_config(temp_config):
    context = _context(temp_config)
    assert isinstance(context.store, LocalRawArtifactStore)
    assert isinstance(context.manifests, ManifestRepository)
    assert context.store.raw_root == temp_config.raw_root
    assert context.manifests.root == temp_config.manifests_root


def test_pipeline_receives_the_same_stage_instances(temp_config):
    context = _context(temp_config)
    assert list(context.pipeline.stages) == [
        context.discover,
        context.resolve,
        context.download,
        context.catalog,
        context.verify,
        context.report,
    ]


def test_one_client_is_shared_by_every_network_stage(temp_config):
    """A shared client means one rate limiter across stage boundaries."""
    client = _FakeClient()
    context = _context(temp_config, client=client)
    for stage in (context.discover, context.resolve, context.download):
        assert stage._client is client


def test_any_stage_can_be_replaced_at_the_composition_root(temp_config):
    class AlternativeDiscover:
        name = "discover"

        def run(self, request):  # pragma: no cover - never invoked here
            raise AssertionError

    context = _context(temp_config, discover=AlternativeDiscover())
    assert isinstance(context.discover, AlternativeDiscover)
    assert context.pipeline.stages[0] is context.discover
    # Everything else still gets the default implementation.
    assert isinstance(context.resolve, SgmlResolveStage)


def test_context_closes_its_client(temp_config):
    client = _FakeClient()
    with _context(temp_config, client=client):
        assert not client.closed
    assert client.closed


# -- CLI ---------------------------------------------------------------------------------


@dataclass
class _Rendered:
    ok: bool = True
    run_id: str = "r"
    manifest_path: Path = Path("/tmp/manifest.json")
    config_hash: str = "c" * 64
    manifest_hash: str = "m" * 64
    filings: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    anomalies: list = field(default_factory=list)
    filing_count: int = 0
    artifact_count: int = 0
    requests_made: int = 0
    text: str = "report body"
    path: Path = Path("/tmp/report.md")
    filings_run_id: str = "f"
    artifacts_run_id: str = "a"

    def render(self) -> str:
        return "rendered"

    @property
    def summary(self):
        return self

    @property
    def report(self):
        return self

    @property
    def failures(self):
        return []


class _RecordingStage:
    def __init__(self, name: str, calls: list, ok: bool = True) -> None:
        self.name = name
        self._calls = calls
        self._ok = ok

    def run(self, request):
        self._calls.append((self.name, request))
        return _Rendered(ok=self._ok)


class _RecordingPipeline:
    def __init__(self, calls: list, ok: bool = True) -> None:
        self._calls = calls
        self._ok = ok
        self.stages = ()

    def run(self, request):
        self._calls.append(("pipeline", request))
        from acquisition.pipeline import AcquisitionResult

        return AcquisitionResult(failed_stage=None if self._ok else "verify")


@pytest.fixture
def cli_harness(temp_config, monkeypatch):
    """Replaces the CLI's context with recording fakes."""
    calls: list = []
    names = ["discover", "resolve", "download", "build-catalog", "verify", "report"]
    stages = {name: _RecordingStage(name, calls) for name in names}
    context = AcquisitionContext(
        config=temp_config,
        client=_FakeClient(),
        store=LocalRawArtifactStore(temp_config.raw_root, temp_config.tmp_root),
        manifests=ManifestRepository(temp_config.manifests_root),
        discover=stages["discover"],
        resolve=stages["resolve"],
        download=stages["download"],
        catalog=stages["build-catalog"],
        verify=stages["verify"],
        report=stages["report"],
        pipeline=_RecordingPipeline(calls),
    )
    monkeypatch.setattr(cli, "_context", lambda args: context)
    return calls, context


@pytest.mark.parametrize(
    "command,expected",
    [
        ("discover", "discover"),
        ("resolve", "resolve"),
        ("download", "download"),
        ("build-catalog", "build-catalog"),
        ("verify", "verify"),
        ("report", "report"),
    ],
)
def test_each_command_delegates_to_its_stage(cli_harness, capsys, command, expected):
    calls, _ = cli_harness
    assert cli.main([command]) == 0
    assert [name for name, _ in calls] == [expected]


def test_acquire_delegates_to_the_pipeline(cli_harness, capsys):
    calls, _ = cli_harness
    assert cli.main(["acquire"]) == 0
    assert [name for name, _ in calls] == ["pipeline"]


def test_acquire_does_not_call_stages_directly(cli_harness, capsys):
    calls, _ = cli_harness
    cli.main(["acquire"])
    assert all(name == "pipeline" for name, _ in calls)


def test_cli_flags_reach_the_stage_request(cli_harness, capsys):
    calls, _ = cli_harness
    cli.main(["download", "--force", "--limit", "3"])
    _, request = calls[0]
    assert request.force is True
    assert request.limit == 3


def test_no_amendments_flag_reaches_discover(cli_harness, capsys):
    calls, _ = cli_harness
    cli.main(["discover", "--no-amendments"])
    assert calls[0][1].include_amendments is False


def test_skip_hashes_flag_reaches_verify(cli_harness, capsys):
    calls, _ = cli_harness
    cli.main(["verify", "--skip-hashes"])
    assert calls[0][1].check_hashes is False


def test_run_id_flags_reach_their_stages(cli_harness, capsys):
    calls, _ = cli_harness
    cli.main(["resolve", "--filings-run-id", "RUN-1"])
    assert calls[0][1].filings_run_id == "RUN-1"


def test_failing_stage_yields_a_nonzero_exit_code(temp_config, monkeypatch, capsys):
    calls: list = []
    failing = _RecordingStage("verify", calls, ok=False)
    context = AcquisitionContext(
        config=temp_config,
        client=_FakeClient(),
        store=LocalRawArtifactStore(temp_config.raw_root, temp_config.tmp_root),
        manifests=ManifestRepository(temp_config.manifests_root),
        discover=failing, resolve=failing, download=failing, catalog=failing,
        verify=failing, report=failing,
        pipeline=_RecordingPipeline(calls, ok=False),
    )
    monkeypatch.setattr(cli, "_context", lambda args: context)
    assert cli.main(["verify"]) == 1
    assert cli.main(["acquire"]) == 1


def test_all_commands_are_registered():
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert set(choices) == {
        "discover", "resolve", "download", "build-catalog", "verify", "report", "acquire",
    }
