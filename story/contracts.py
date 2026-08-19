"""The public contract of the story layer.

Dependency-light by rule, the same rule `graph/contracts.py` and `extraction/contracts.py`
state: `typing`, this package's core models, and nothing else. **No driver, no HTTP client, no
configuration, no storage import** — and in particular no `neo4j`, which decision D1 confines
to `story/providers/neo4j_connection.py`, and no `httpx`, which §5 confines to
`story/providers/`. `tests/story/test_story_package_structure.py` asserts the import set of
this file exactly, so the rule cannot erode by one convenient import.

Six protocols, at six different stages of life. None is implemented at S0, and declaring them
now is not speculation — it is the boundary that keeps the driver and the HTTP client out.
Every stage is written against a protocol nothing in `story/core` may import an
implementation of, so "one module imports neo4j" is a structural fact rather than a
convention.

    StoryDetector             S3   pure; canonical series in, candidates out
    GraphRetriever            S1   §9's bounded, code-owned tools
    ReadQueryExecutor         S0c  the one surface a driver is allowed behind
    EvidenceBuilder           S5   the wall the model cannot see past
    StoryGenerationProvider   S6   the only module that will open an HTTP connection
    DraftVerifier             S9   constructible with neither a database nor a model

Structural typing only. A `Protocol` is what lets a test drive a five-line stub through the
same surface the real implementation exposes; `isinstance` against a `runtime_checkable`
protocol proves only that some attributes exist, so the tests here drive the object instead.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from story.core.models import (
    DerivedFact,
    Draft,
    EditorialPlan,
    EvidenceRequest,
    EvidenceScopeFact,
    GenerationResult,
    HealthStatus,
    RetrievalResult,
    RetrievalTraceEntry,
    StoryCandidate,
    StoryEvidencePackage,
    VerifiedDraft,
)


@runtime_checkable
class StoryDetector(Protocol):
    """A canonical series in, `StoryCandidate`s out. **No model, ever** (§6).

    `detector_id` and `version` are on the protocol because both are digest inputs to every
    candidate the detector mints (§6.11): a threshold change that did not move `version` would
    mutate candidates in place instead of minting new ones.

    `series` is left structural — `story.core.series.CanonicalSeries` arrives at S2, and
    naming it here would put a canonicalisation module into the contract every consumer
    imports. `graph.contracts.GraphProjection.project` makes the same choice for the same
    reason.
    """

    @property
    def detector_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def detect(self, series: object) -> Sequence[StoryCandidate]: ...


@runtime_checkable
class GraphRetriever(Protocol):
    """Bounded, read-only, parameterised access to the graph (§9, §16).

    **The only surface through which anything in this package reaches a database**, and the
    reason it is a protocol is that every stage above it must be testable with no server
    running. No caller passes Cypher: `tool` names one of §9's code-owned statements and
    `parameters` are bound, never interpolated, so a model cannot widen a query by choosing a
    string.

    `trace` exists because §10 requires the package to carry a `retrieval_trace[]`: which tool
    ran, with what, how many rows came back and whether a bound bit. A retriever that could
    not report what it did would make the package's provenance an assertion.
    """

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult: ...

    def trace(self) -> Sequence[RetrievalTraceEntry]: ...


@runtime_checkable
class ReadQueryExecutor(Protocol):
    """A read-only Cypher session, with the driver on the other side of it.

    Satisfied at S0c by `story/providers/neo4j_connection.py` — **not** by anything under
    `story/core/`, which must stay constructible with no database and no environment. The
    adapter is story-owned rather than borrowed: `graph/stages/load/connection.py` holds
    `load_settings`/`driver_for`, and `graph.stages` is not a surface this package may import
    (WORKSTREAM_BOUNDARY §4). The duplication is a handful of lines and is named where it
    happens, which is the trade §15.2 already makes for the provider transport.

    **Why this shape.**

    * `statement` and `parameters` are two arguments, and there is nowhere else to put a
      value. A protocol cannot forbid an f-string — that is S1's structural scan, which
      refuses any Cypher constant built by interpolation — but a signature with no room for
      one is what makes the scan a rule about a single module rather than about the whole
      package.
    * `timeout_seconds` is keyword-only with **no default**. §16 requires a timeout on every
      statement, and a default is how one call site ends up without one.
    * Rows cross as plain `dict`s of plain Python values. **No `neo4j.Record`, no
      `neo4j.Node`, no `neo4j.time.DateTime` ever reaches a story stage** — the rule
      `extraction/providers/public.py` states for `GenerationResult`, and the reason
      `story/core/` can be tested with no driver installed at all.
    * `verify_connectivity` returns a `HealthStatus` rather than raising, for the same reason
      the generation provider's `health` does: a database that is not running is an ordinary
      state for the freshness gate to branch on.

    Read-only is a promise this type cannot enforce and the implementation must: §16 and
    WORKSTREAM_BOUNDARY §3 forbid `CREATE`, `MERGE`, `SET`, `DELETE` and every index or
    constraint statement, and S1's scan is what checks it.
    """

    def read(
        self,
        statement: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], ...]: ...

    def verify_connectivity(self) -> HealthStatus: ...

    def close(self) -> None: ...


@runtime_checkable
class EvidenceBuilder(Protocol):
    """A candidate and its `EvidenceRequest` in, the model's entire universe out (§10).

    Two arguments and not one: the request states what the *detector* anchored on, and the
    builder decides how much of it fits inside §10.2's bounds. Keeping them apart is what
    stops a detector widening its own evidence — the request is a claim about relevance, never
    about size.

    Deterministic: rebuilding from one graph run must reproduce `package_content_digest`.
    """

    def build(
        self, candidate: StoryCandidate, request: EvidenceRequest
    ) -> StoryEvidencePackage: ...


@runtime_checkable
class StoryGenerationProvider(Protocol):
    """Schema-constrained generation against an OpenAI-compatible server (§15).

    `system` is a separate argument rather than a prefix on `prompt`, which is the one
    extension §15.1 requires over the extraction provider: a planner, a writer and a verifier
    are three personas, and folding them into one string hides the structure inside the
    request digest that the replay store is keyed by. `schema_name` is likewise a parameter
    because the extraction path hard-codes it to `"extraction_claim"` for every call.

    A schema violation is the model's answer and is **never retried**; a timeout is never
    retried either. Only transport faults are retryable — one axis, six error classes.
    `health` returns a value rather than raising, because a local server that is not running
    is an ordinary state for a caller to branch on.

    **`provider_id` is on the protocol and `model_id` is not**, which is a judgment worth
    recording rather than an oversight. S12 made the provider a digest input to
    `request_identity` and to `story_run_id`: two providers answering to one model string are
    two different requests, so every implementation — the local adapter, the OpenAI adapter,
    the replaying store and the demo's decorators — must be able to say which one it is, and a
    decorator that forgot to forward it would silently key a run under the provider it wraps.
    `model_id` stays implicit because a replaying provider can be constructed without one and
    infers it from the rows it holds; widening the protocol to demand it would make that
    legitimate state unrepresentable.
    """

    @property
    def provider_id(self) -> str: ...

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float,
    ) -> GenerationResult: ...

    def health(self) -> HealthStatus: ...


@runtime_checkable
class DraftVerifier(Protocol):
    """A draft and the package it was written from in, a decision out (§13).

    Returns a `VerifiedDraft` whether or not the draft survived — `passed` is derived from the
    findings, so a refusal has somewhere to be recorded and cannot be lost by a verifier that
    raises instead. The deterministic layer is the final authority; the model may only tighten
    (§13.16), which is a property of the implementation and not something a protocol can
    state.

    `plan` is required because §13 has checks the draft alone cannot answer: a dropped
    `required_warning` and an absent counterpoint are both refusals about what the *plan*
    asked for.

    **`derived_facts` is defaulted, and that is the one place this protocol is permissive**
    (DETERMINISTIC_FACT_TOOLS §6). A run that requested no derivation produces none, and every
    call site written before S13 means exactly that — so the default is the truthful value
    rather than a convenience. It is not a way to skip the argument: a draft binding a
    `fact:derived:` id against an empty sequence is `derived_fact_not_in_run`, a REFUSE, so a
    caller that forgot to pass what it computed fails closed rather than verifying a number
    against nothing.
    """

    def verify(
        self,
        draft: Draft,
        package: StoryEvidencePackage,
        plan: EditorialPlan,
        derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    ) -> VerifiedDraft: ...


__all__ = [
    "DraftVerifier",
    "EvidenceBuilder",
    "GraphRetriever",
    "ReadQueryExecutor",
    "StoryDetector",
    "StoryGenerationProvider",
]
