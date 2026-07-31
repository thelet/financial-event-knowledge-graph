"""The shared pipeline-stage contract.

Deliberately small. It standardizes four things and nothing more: a stage has a name, it
takes one typed request, it returns one typed result, and the result says whether it
succeeded. Stages do not share a prepare/validate/execute/finalize lifecycle, so none is
imposed.

This module must stay dependency-light -- typing only. It sits below every stage in the
import order.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

RequestT = TypeVar("RequestT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


@runtime_checkable
class StageResult(Protocol):
    """What every stage result must expose so the pipeline can decide to continue."""

    @property
    def ok(self) -> bool:
        """False when the stage did not achieve its purpose and the run should stop."""


@runtime_checkable
class PipelineStage(Protocol[RequestT, ResultT]):
    """One step of the acquisition pipeline.

    The replaceability boundary: the pipeline depends on this, never on a concrete stage
    class, so an implementation can be swapped in `context.py` without touching
    orchestration.

    Note for tests: `runtime_checkable` verifies method *presence* only, never signatures.
    An `isinstance` check against this protocol proves very little on its own -- conformance
    tests should call stages *through* the protocol with real request objects.
    """

    @property
    def name(self) -> str:
        """Stable stage name, used in logs, run records, and progress output."""

    def run(self, request: RequestT) -> ResultT:
        """Execute the stage once."""
