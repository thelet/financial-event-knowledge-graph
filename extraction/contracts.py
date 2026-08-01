"""The public contracts of the extraction layer.

Dependency-light by rule: typing, the core models, and the ontology's boundary types. No
HTTP, storage, manifest, configuration or provider import belongs here, and no lane
implementation may be named.

Two protocols matter. `PipelineStage` is the same shape the acquisition and normalization
packages use, so a stage is replaceable by swapping it in `context.py`. `ClaimLane` is the
one this layer adds: it is what makes a deterministic table reader and a model-backed
narrative reader interchangeable to everything downstream.

    ClaimLane
    ├── DeterministicTableClaimLane      (no provider, fully offline)
    └── OntologyGuidedNarrativeClaimLane (a local generation provider, behind its own port)

Neither is named here. The narrative lane's provider dies at its own adapter boundary and
nothing downstream — not `assemble`, not `verify`, not the catalog — knows a model was
involved beyond an opaque `extractor_metadata` dict the ontology never interprets.
"""

from __future__ import annotations

from typing import Protocol, Sequence, TypeVar, runtime_checkable

from .core.models import CandidatePassage, LaneResult

RequestT = TypeVar("RequestT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


@runtime_checkable
class StageResult(Protocol):
    @property
    def ok(self) -> bool:
        """False when the stage did not achieve its purpose and the run should stop."""


@runtime_checkable
class PipelineStage(Protocol[RequestT, ResultT]):
    """One step of the extraction pipeline."""

    @property
    def name(self) -> str: ...

    def run(self, request: RequestT) -> ResultT: ...


@runtime_checkable
class ClaimLane(Protocol):
    """Reads claims out of one candidate passage.

    `extract` takes a single candidate rather than a batch so that routing stays explicit:
    the caller decides which lane sees which passage and records why, instead of each lane
    filtering a shared stream by its own private rules. Not every table is deterministic and
    not every narrative needs the same model path — the routing decision is auditable
    precisely because it happens outside the lanes.

    A lane returns a `LaneResult` even when it read nothing. Silence and abstention are
    different answers, and the benchmark scores the difference.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def supports(self, candidate: CandidatePassage) -> bool: ...

    def extract(self, candidate: CandidatePassage, text: str) -> LaneResult: ...


@runtime_checkable
class PassageSource(Protocol):
    """Read access to the normalized corpus.

    A port, so `assemble` and `verify` can be driven from hand-built passages in tests
    without a corpus on disk, and so extraction never imports normalization's storage.
    """

    def text_of(self, passage_id: str) -> str | None: ...

    def exists(self, passage_id: str) -> bool: ...

    def document_of(self, passage_id: str) -> str | None: ...


@runtime_checkable
class GenerationProvider(Protocol):
    """Where a model answer comes from. The narrative lane's only outside dependency.

    Deliberately minimal and provider-shaped rather than vendor-shaped: a local
    llama.cpp server, a different local runtime, or a mock all satisfy it. `schema` is a
    JSON Schema the response must conform to — structured output is a requirement, not a
    convenience, because a lane that parses prose into claims reintroduces exactly the
    ambiguity the ontology exists to remove.
    """

    @property
    def model_id(self) -> str: ...

    def generate(
        self, *, prompt: str, schema: dict, max_tokens: int = 1024, temperature: float = 0.0
    ) -> dict: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Vectors for ontology candidate scoping, and nothing else.

    Scoped this narrowly on purpose. Embeddings may *add* semantic candidates to a lane's
    consideration set; they may never remove an exact alias match, an ambiguous alias, a
    table-label match, a stable core concept, or a confusion-group sibling. The ontology's
    `distinct_from` declarations exist to stop metrics collapsing into each other, and a
    similarity score is not entitled to overrule them.
    """

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


@runtime_checkable
class OntologyCandidateScope(Protocol):
    """Which concepts a lane should consider for a passage.

    Implementations are `LexicalOntologyCandidateScope` and `HybridOntologyCandidateScope`;
    the hybrid one takes an `EmbeddingProvider`. Both satisfy this, so the benchmark can
    compare them before either becomes the default.
    """

    @property
    def name(self) -> str: ...

    def candidates_for(self, text: str) -> tuple[str, ...]: ...
