"""Pipeline ordering, data flow, and abort semantics, using fake stages only.

No network, no filesystem, no real stage implementation. These tests exist to prove the
pipeline depends on the contract rather than on any concrete stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from acquisition.pipeline import AcquisitionPipeline, AcquisitionRequest

EXPECTED_ORDER = ["discover", "resolve", "download", "build-catalog", "verify", "report"]


@dataclass
class FakeResult:
    ok: bool = True
    run_id: str = "fake-run"
    # Enough attributes that the pipeline can thread values between stages.
    filings: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)


class FakeStage:
    """Records the requests it received and returns a configurable result."""

    def __init__(self, name: str, log: list, ok: bool = True) -> None:
        self.name = name
        self._log = log
        self._ok = ok
        self.requests: list = []

    def run(self, request):
        self._log.append(self.name)
        self.requests.append(request)
        return FakeResult(ok=self._ok, run_id=f"{self.name}-run")


def build(log: list, failing: str | None = None) -> tuple[AcquisitionPipeline, dict]:
    stages = {
        name: FakeStage(name, log, ok=(name != failing)) for name in EXPECTED_ORDER
    }
    pipeline = AcquisitionPipeline(
        discover=stages["discover"],
        resolve=stages["resolve"],
        download=stages["download"],
        catalog=stages["build-catalog"],
        verify=stages["verify"],
        report=stages["report"],
    )
    return pipeline, stages


# -- Ordering ----------------------------------------------------------------------------


def test_stages_run_in_order():
    log: list = []
    pipeline, _ = build(log)
    result = pipeline.run(AcquisitionRequest())
    assert log == EXPECTED_ORDER
    assert result.ok
    assert result.completed == EXPECTED_ORDER


def test_pipeline_exposes_its_stages_in_order():
    log: list = []
    pipeline, _ = build(log)
    assert [s.name for s in pipeline.stages] == EXPECTED_ORDER


# -- Data flow ---------------------------------------------------------------------------


def test_discover_run_id_is_passed_to_resolve():
    log: list = []
    pipeline, stages = build(log)
    pipeline.run(AcquisitionRequest())
    assert stages["resolve"].requests[0].filings_run_id == "discover-run"


def test_resolve_run_id_is_passed_to_download_verify_and_report():
    log: list = []
    pipeline, stages = build(log)
    pipeline.run(AcquisitionRequest())
    assert stages["download"].requests[0].artifacts_run_id == "resolve-run"
    assert stages["verify"].requests[0].artifacts_run_id == "resolve-run"
    assert stages["report"].requests[0].artifacts_run_id == "resolve-run"


def test_verify_receives_both_manifest_run_ids():
    log: list = []
    pipeline, stages = build(log)
    pipeline.run(AcquisitionRequest())
    verify_request = stages["verify"].requests[0]
    assert verify_request.filings_run_id == "discover-run"
    assert verify_request.artifacts_run_id == "resolve-run"


def test_request_options_reach_the_right_stages():
    log: list = []
    pipeline, stages = build(log)
    pipeline.run(
        AcquisitionRequest(
            cik="1801169", include_amendments=False, limit=7, force=True,
            check_hashes=False,
        )
    )
    assert stages["discover"].requests[0].include_amendments is False
    assert stages["discover"].requests[0].cik == "1801169"
    assert stages["resolve"].requests[0].limit == 7
    assert stages["download"].requests[0].force is True
    assert stages["download"].requests[0].limit == 7
    assert stages["verify"].requests[0].check_hashes is False


def test_progress_callbacks_are_forwarded():
    log: list = []
    pipeline, stages = build(log)

    def resolve_progress(*_): ...
    def download_progress(*_): ...

    pipeline.run(
        AcquisitionRequest(
            resolve_progress=resolve_progress, download_progress=download_progress
        )
    )
    assert stages["resolve"].requests[0].progress is resolve_progress
    assert stages["download"].requests[0].progress is download_progress


# -- Abort semantics ---------------------------------------------------------------------


@pytest.mark.parametrize("failing", EXPECTED_ORDER[:-1])
def test_failure_aborts_and_skips_later_stages(failing):
    log: list = []
    pipeline, _ = build(log, failing=failing)
    result = pipeline.run(AcquisitionRequest())

    stop = EXPECTED_ORDER.index(failing)
    assert log == EXPECTED_ORDER[: stop + 1]
    assert not result.ok
    assert result.failed_stage == failing
    assert result.completed == EXPECTED_ORDER[:stop]


def test_report_failure_is_recorded_but_nothing_follows():
    log: list = []
    pipeline, _ = build(log, failing="report")
    result = pipeline.run(AcquisitionRequest())
    assert log == EXPECTED_ORDER
    assert result.failed_stage == "report"
    assert not result.ok


def test_failed_stage_result_is_still_available_for_inspection():
    log: list = []
    pipeline, _ = build(log, failing="download")
    result = pipeline.run(AcquisitionRequest())
    assert result.download is not None
    assert result.download.ok is False
    assert result.catalog is None


def test_verify_failure_prevents_the_report():
    """Reproduces the original acquire behaviour exactly."""
    log: list = []
    pipeline, _ = build(log, failing="verify")
    result = pipeline.run(AcquisitionRequest())
    assert "report" not in log
    assert result.report is None


# -- Observability -----------------------------------------------------------------------


def test_stage_hooks_fire_for_every_stage():
    log: list = []
    started: list = []
    finished: list = []
    pipeline, _ = build(log)
    pipeline.run(
        AcquisitionRequest(
            on_stage_start=started.append,
            on_stage_finish=lambda name, _outcome: finished.append(name),
        )
    )
    assert started == EXPECTED_ORDER
    assert finished == EXPECTED_ORDER


def test_stage_hooks_fire_for_a_failing_stage_too():
    log: list = []
    finished: list = []
    pipeline, _ = build(log, failing="resolve")
    pipeline.run(
        AcquisitionRequest(on_stage_finish=lambda name, _o: finished.append(name))
    )
    assert finished == ["discover", "resolve"]


# -- Replaceability ----------------------------------------------------------------------


def test_a_stage_implementation_can_be_swapped_without_touching_the_pipeline():
    log: list = []

    class AlternativeDiscover:
        name = "discover"

        def run(self, request):
            log.append("alternative-discover")
            return FakeResult(run_id="alt-run")

    stages = {name: FakeStage(name, log) for name in EXPECTED_ORDER}
    pipeline = AcquisitionPipeline(
        discover=AlternativeDiscover(),
        resolve=stages["resolve"],
        download=stages["download"],
        catalog=stages["build-catalog"],
        verify=stages["verify"],
        report=stages["report"],
    )
    result = pipeline.run(AcquisitionRequest())

    assert result.ok
    assert log[0] == "alternative-discover"
    assert stages["resolve"].requests[0].filings_run_id == "alt-run"


def test_pipeline_does_no_io():
    """The pipeline module must not reach for the network or the filesystem."""
    import acquisition.pipeline as module

    source = module.__file__
    text = open(source, encoding="utf-8").read()
    for forbidden in ("httpx", "open(", "Path(", "mkdir", "write_text", "requests"):
        assert forbidden not in text, f"pipeline.py should not reference {forbidden!r}"
