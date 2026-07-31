"""Authoritative issues must exist before the derived catalog is built.

The bug this guards: the CLI wrote the per-run issue file after `pipeline.run()` returned,
but the catalog stage had already globbed that directory, so a fresh run left
`normalization_catalog/issues.jsonl` empty and only a second `build-catalog` fixed it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from normalization.core.models import NormalizationIssue
from normalization.pipeline import STAGE_ORDER, NormalizationPipeline, NormalizationRequest
from normalization.stages.catalog.jsonl_catalog import ISSUES_CATALOG, JsonlCatalogStage
from normalization.stages.issues import IssueLogRequest, RunIssueLogStage
from normalization.utils.jsonl import read_jsonl


# -- ordering, proven with spies ---------------------------------------------------------


class SpyResult:
    def __init__(self, ok=True):
        self.ok = ok
        self.for_processing = []
        self.documents = []
        self.issues = []
        self.comparisons = []
        self.issue_count = 0
        self.path = None


class SpyStage:
    def __init__(self, name, log, ok=True):
        self.name = name
        self._log = log
        self._ok = ok
        self.requests = []

    def run(self, request):
        self._log.append(self.name)
        self.requests.append(request)
        return SpyResult(self._ok)


def _pipeline(log, failing=None):
    stages = {n: SpyStage(n, log, ok=(n != failing)) for n in STAGE_ORDER}
    pipeline = NormalizationPipeline(
        select=stages["select"], normalize=stages["normalize"], issues=stages["write-issues"],
        catalog=stages["build-catalog"], verify=stages["verify"], report=stages["report"],
    )
    return pipeline, stages


def test_issues_are_written_before_the_catalog_stage():
    log: list[str] = []
    pipeline, _ = _pipeline(log)
    pipeline.run(NormalizationRequest(run_id="r"))
    assert log.index("write-issues") < log.index("build-catalog")


def test_full_stage_order_is_explicit():
    log: list[str] = []
    pipeline, _ = _pipeline(log)
    pipeline.run(NormalizationRequest(run_id="r"))
    assert log == ["select", "normalize", "write-issues", "build-catalog", "verify", "report"]


def test_issue_stage_receives_issues_from_every_producing_stage():
    log: list[str] = []
    pipeline, stages = _pipeline(log)

    select_issue = NormalizationIssue(
        issue_id="r:a:needs_review", run_id="r", artifact_id="a",
        severity="warning", code="NEEDS_REVIEW", detail="from selection")
    normalize_issue = NormalizationIssue(
        issue_id="r:b:fallback", run_id="r", artifact_id="b",
        severity="warning", code="PARSER_FALLBACK", detail="from normalize")

    original = stages["select"].run
    stages["select"].run = lambda req: _with_issues(original(req), [select_issue])
    original_norm = stages["normalize"].run
    stages["normalize"].run = lambda req: _with_issues(original_norm(req), [normalize_issue])

    pipeline.run(NormalizationRequest(run_id="r"))
    codes = {i.code for i in stages["write-issues"].requests[0].issues}
    assert codes == {"NEEDS_REVIEW", "PARSER_FALLBACK"}


def _with_issues(result, issues):
    result.issues = issues
    return result


def test_catalog_is_skipped_when_issue_writing_fails():
    """Derived output must never be built from an incomplete authoritative set."""
    log: list[str] = []
    pipeline, _ = _pipeline(log, failing="write-issues")
    result = pipeline.run(NormalizationRequest(run_id="r"))
    assert "build-catalog" not in log
    assert result.failed_stage == "write-issues"


def test_catalog_failure_leaves_issues_written():
    log: list[str] = []
    pipeline, _ = _pipeline(log, failing="build-catalog")
    result = pipeline.run(NormalizationRequest(run_id="r"))
    assert "write-issues" in log
    assert result.issues is not None and result.issues.ok


def test_cli_does_not_persist_issues_after_the_pipeline():
    """Guards the regression directly: the CLI must not write the issue file."""
    source = (Path(__file__).resolve().parents[2] / "normalization" / "cli.py").read_text()
    assert "-issues.jsonl" not in source
    assert "write_jsonl" not in source


# -- the stage itself --------------------------------------------------------------------


def _issue(n: int, run_id: str = "run-1") -> NormalizationIssue:
    return NormalizationIssue(
        issue_id=f"{run_id}:artifact-{n}:flag", run_id=run_id, artifact_id=f"artifact-{n}",
        severity="warning", code="HIERARCHY_UNCERTAIN", detail=f"detail {n}")


@pytest.fixture
def tmp_config(repo_config, tmp_path):
    config = repo_config.model_copy(update={"root": tmp_path})
    return config


def test_zero_issue_run_writes_an_empty_authoritative_file(tmp_config):
    """An absent file and a clean run must not be indistinguishable."""
    result = RunIssueLogStage(tmp_config).run(IssueLogRequest(run_id="run-1", issues=[]))
    assert result.ok and result.issue_count == 0
    assert result.path.is_file()
    assert result.path.read_text() == ""


def test_issues_are_written_deterministically(tmp_config):
    stage = RunIssueLogStage(tmp_config)
    forward = stage.run(IssueLogRequest(run_id="run-1", issues=[_issue(2), _issue(1)]))
    first = forward.path.read_bytes()
    stage.run(IssueLogRequest(run_id="run-1", issues=[_issue(1), _issue(2)]))
    assert forward.path.read_bytes() == first


def test_rewriting_the_same_run_does_not_duplicate(tmp_config):
    stage = RunIssueLogStage(tmp_config)
    stage.run(IssueLogRequest(run_id="run-1", issues=[_issue(1)]))
    result = stage.run(IssueLogRequest(run_id="run-1", issues=[_issue(1)]))
    assert len(read_jsonl(result.path)) == 1


def test_duplicate_issue_ids_within_a_run_are_reported(tmp_config):
    result = RunIssueLogStage(tmp_config).run(
        IssueLogRequest(run_id="run-1", issues=[_issue(1), _issue(1)])
    )
    assert result.issue_count == 1
    assert result.duplicate_ids == ["run-1:artifact-1:flag"]


def test_no_temporary_file_survives(tmp_config):
    result = RunIssueLogStage(tmp_config).run(IssueLogRequest(run_id="run-1", issues=[_issue(1)]))
    assert not list(result.path.parent.glob("*.tmp"))


# -- authoritative -> derived --------------------------------------------------------------


def _catalog(tmp_config):
    from normalization.core.manifests import ManifestRepository
    from normalization.core.storage import LocalNormalizedStore

    store = LocalNormalizedStore(tmp_config.normalized_root, tmp_config.tmp_root, "run-1")
    return JsonlCatalogStage(tmp_config, store, ManifestRepository(tmp_config.manifests_root))


def test_aggregate_matches_the_authoritative_records(tmp_config):
    from normalization.stages.catalog import CatalogRequest

    issues = [_issue(n) for n in range(5)]
    written = RunIssueLogStage(tmp_config).run(IssueLogRequest(run_id="run-1", issues=issues))
    _catalog(tmp_config).run(CatalogRequest(run_id="run-1"))

    aggregate = read_jsonl(tmp_config.catalog_root / ISSUES_CATALOG)
    authoritative = read_jsonl(written.path)
    assert len(aggregate) == len(authoritative) == 5
    assert {r["issue_id"] for r in aggregate} == {r["issue_id"] for r in authoritative}


def test_zero_issue_run_yields_an_empty_aggregate(tmp_config):
    from normalization.stages.catalog import CatalogRequest

    RunIssueLogStage(tmp_config).run(IssueLogRequest(run_id="run-1", issues=[]))
    result = _catalog(tmp_config).run(CatalogRequest(run_id="run-1"))
    assert result.issue_count == 0
    assert (tmp_config.catalog_root / ISSUES_CATALOG).read_text() == ""


def test_aggregate_rebuild_is_byte_identical(tmp_config):
    from normalization.stages.catalog import CatalogRequest

    RunIssueLogStage(tmp_config).run(IssueLogRequest(run_id="run-1", issues=[_issue(n) for n in range(4)]))
    catalog = _catalog(tmp_config)
    catalog.run(CatalogRequest(run_id="run-1"))
    first = (tmp_config.catalog_root / ISSUES_CATALOG).read_bytes()
    catalog.run(CatalogRequest(run_id="run-1"))
    assert (tmp_config.catalog_root / ISSUES_CATALOG).read_bytes() == first


def test_rebuilding_does_not_duplicate_across_runs(tmp_config):
    """Two runs are two authoritative files; ids are run-scoped, so no row is repeated."""
    from normalization.stages.catalog import CatalogRequest

    stage = RunIssueLogStage(tmp_config)
    stage.run(IssueLogRequest(run_id="run-1", issues=[_issue(1, "run-1")]))
    stage.run(IssueLogRequest(run_id="run-2", issues=[_issue(1, "run-2")]))
    result = _catalog(tmp_config).run(CatalogRequest(run_id="run-2"))
    rows = read_jsonl(tmp_config.catalog_root / ISSUES_CATALOG)
    assert result.issue_count == len(rows) == 2
    assert len({r["issue_id"] for r in rows}) == 2


# -- a fresh complete run, end to end -----------------------------------------------------


def test_fresh_pipeline_run_leaves_a_correct_aggregate_immediately(repo_config, tmp_path,
                                                                   corpus_available):
    """The regression, at the level it actually bit: one run, no second command."""
    if not corpus_available:
        import pytest as _p
        _p.skip("acquisition corpus not present")

    from normalization.context import build_normalization_context
    from normalization.pipeline import NormalizationRequest

    # Real acquisition inputs, isolated normalization outputs: point the acquisition path
    # at the repository absolutely so only derived output lands in tmp_path.
    config = repo_config.model_copy(deep=True, update={"root": tmp_path})
    config.normalization.paths.acquisition_catalog = str(
        repo_config.acquisition_catalog_root
    )
    fixtures = [
        l.strip()
        for l in (Path(__file__).resolve().parents[2]
                  / "plans/normalization/spike_fixtures.txt").read_text().splitlines()
        if l.strip() and not l.startswith("#")
    ][:4]

    context = build_normalization_context(run_id="fresh", config=config)
    result = context.pipeline.run(
        NormalizationRequest(run_id="fresh-run", artifact_ids=fixtures, review=False)
    )

    assert result.ok, result.failed_stage
    assert result.issues is not None and result.issues.path.is_file()

    aggregate = read_jsonl(config.catalog_root / ISSUES_CATALOG)
    authoritative = read_jsonl(result.issues.path)
    assert len(aggregate) == len(authoritative)
    assert result.catalog.issue_count == len(authoritative)


def test_acquisition_catalog_is_read_from_the_repository(repo_config, tmp_path):
    """The isolated config must still resolve acquisition inputs to the real corpus."""
    config = repo_config.model_copy(update={"root": tmp_path})
    assert config.acquisition_catalog_root == tmp_path / "data" / "catalog"
