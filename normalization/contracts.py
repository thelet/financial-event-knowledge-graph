"""The shared pipeline-stage contract.

Deliberately small, and the same shape the acquisition package uses: a stage has a name,
takes one typed request, returns one typed result, and the result says whether it
succeeded.

Dependency-light by rule — typing only.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

RequestT = TypeVar("RequestT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


@runtime_checkable
class StageResult(Protocol):
    @property
    def ok(self) -> bool:
        """False when the stage did not achieve its purpose and the run should stop."""


@runtime_checkable
class PipelineStage(Protocol[RequestT, ResultT]):
    """One step of the normalization pipeline.

    The replaceability boundary. `runtime_checkable` verifies method presence only, so
    conformance tests call stages *through* the protocol rather than asserting isinstance.
    """

    @property
    def name(self) -> str: ...

    def run(self, request: RequestT) -> ResultT: ...
