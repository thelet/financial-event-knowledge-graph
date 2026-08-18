"""Builders for the story layer's frozen types, and the recorded executor every stage tests against.

Offline, no database, no model server.

Every type in `story/core/models.py` is required at construction to be internally consistent,
which is the point — and it makes a hand-built example verbose. These builders supply the
smallest valid instance of each and take keyword overrides, so a test that cares about one
field says so by naming that field and nothing else.

**No fixture here reads the corpus.** S0 has no stage that touches `data/`, and a structural
contract that could only be tested against a finished run would be a contract nobody could
check from a clean checkout. `tests/story/fixtures/` arrives at S3, when there is something
real to slice.

**`RecordedReadExecutor` is the database, for every test that is not marked `neo4j`** (S0c).
It imports no driver — that is the claim it exists to make good: `ReadQueryExecutor` is a
structural type, so a stage can be written and driven with `neo4j` uninstalled. It records
every call, including the timeout, because §16's "a timeout on every statement" is a property
a caller has to be *shown* keeping, and a fake that discarded the argument would let a stage
drop it silently. It lives here rather than in one step's test file because S0b and S1 both
consume the same protocol and neither should reimplement this.

Deliberately **no `__init__.py` in this directory**: `pyproject.toml`'s
`pythonpath = [".", "tests", …]` puts `tests/` on `sys.path`, so a `tests/story/__init__.py`
would make `tests/story` importable as the top-level package `story` and shadow the real one.
Measured, not assumed — with the file present, `import story; story.STORY_LAYOUT_VERSION`
raises `AttributeError` from inside a test. No other directory under `tests/` has one either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pytest

from story.core.models import (
    BudgetParameters,
    Draft,
    DraftSentence,
    EditorialPlan,
    EvidenceRequest,
    EvidenceRole,
    FactBinding,
    HealthStatus,
    KeyPoint,
    PackageBudget,
    PackagedDocument,
    PackagedFact,
    PackagedPassage,
    PackagedSubject,
    PackageIdentity,
    PassageCitation,
    SentenceKind,
    StatementClass,
    StoryCandidate,
    StoryEvidencePackage,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The run this implementation is built on (IMPLEMENTATION_STEPS §0, verified 2026-08-03).
#: Pinned as strings rather than read from `data/`, because these tests assert the *shape* of
#: an identity block and must run on a clean checkout where `data/` is absent.
GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
GRAPH_PROJECTION_VERSION = "1.2.0"
EXTRACTION_RUN_ID = "extract-v1-lexical-833f7bcfbce9"
RUN_COMPLETE_SHA256 = "1cc8f7b01c040531" + "0" * 48
ONTOLOGY_ID = "real_estate_marketplace_v1"
ONTOLOGY_VERSION = "2.0.0"
ONTOLOGY_DEFINITION_HASH = "bb94f522ba122470" + "0" * 48


def make_candidate(**overrides: Any) -> StoryCandidate:
    """F1 from §6.3: adjusted EBITDA crosses zero between 2022Q2 and 2022Q3."""
    fields: dict[str, Any] = dict(
        candidate_id="cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:761fc3148c01",
        detector_id="detector:metric_move",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id=GRAPH_RUN_ID,
        subject_entity_id="opendoor",
        story_type="metric_move",
        metric_ids=("adjusted_ebitda",),
        anchor_period_keys=("2022Q2", "2022Q3"),
        anchor_observation_ids=("obs:adjusted-ebitda:a", "obs:adjusted-ebitda:b"),
        signals={"delta": -429_000_000.0, "crosses_zero": True},
        evidence_request=EvidenceRequest(
            metric_ids=("adjusted_ebitda",),
            period_keys=("2022Q2", "2022Q3"),
            observation_ids=("obs:adjusted-ebitda:a", "obs:adjusted-ebitda:b"),
        ),
    )
    fields.update(overrides)
    return StoryCandidate(**fields)


def make_package(**overrides: Any) -> StoryEvidencePackage:
    """One fact, one passage, one document — the smallest package a draft can cite."""
    fields: dict[str, Any] = dict(
        package_id="pkg:metric-move-adjusted-ebitda-opendoor-2022q2-2022q3:0123456789ab",
        candidate_id=make_candidate().candidate_id,
        detector_id="detector:metric_move",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id=GRAPH_RUN_ID,
        graph_projection_version=GRAPH_PROJECTION_VERSION,
        extraction_run_id=EXTRACTION_RUN_ID,
        run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_id=ONTOLOGY_ID,
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
        ontology_semantic_version=ONTOLOGY_VERSION,
        subject=PackagedSubject(entity_id="opendoor", entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(
            PackagedFact(
                observation_id="obs:adjusted-ebitda:b",
                metric_id="adjusted_ebitda",
                metric_label="Adjusted EBITDA",
                period_key="2022Q3",
                period_start="2022-07-01",
                period_end="2022-09-30",
                shape="duration",
                value=-211_000_000.0,
                unit="USD",
                currency="USD",
                scale="millions",
                printed_form="(211)",
                source_lane="normalized_table",
                validation_state="ok",
                passage_id="psg:1",
                document_id="doc:1",
                quoted_text="(211)",
            ),
        ),
        primary_passages=(
            PackagedPassage(passage_id="psg:1", document_id="doc:1",
                            text="Adjusted EBITDA (211)", char_count=21,
                            role=EvidenceRole.PRIMARY_SUPPORT),
        ),
        documents=(PackagedDocument(document_id="doc:1", form="10-Q"),),
        budget=PackageBudget(artifact_token_estimate=536, prompt_token_estimate=402,
                             section_counts={"facts": 1}, parameters=BudgetParameters()),
    )
    fields.update(overrides)
    return StoryEvidencePackage(**fields)


def make_identity(package: StoryEvidencePackage | None = None) -> PackageIdentity:
    return (package or make_package()).identity


def make_plan(**overrides: Any) -> EditorialPlan:
    fields: dict[str, Any] = dict(
        candidate_id=make_candidate().candidate_id,
        package_id=make_package().package_id,
        thesis="Adjusted EBITDA crossed zero in 2022Q3.",
        why_it_matters="It is the only sign reversal in twenty-five quarters.",
        key_points=(
            KeyPoint(claim="Adjusted EBITDA was negative $211 million in 2022Q3.",
                     required_fact_ids=("obs:adjusted-ebitda:b",),
                     required_citation_passage_ids=("psg:1",),
                     statement_class=StatementClass.REPORTED),
        ),
    )
    fields.update(overrides)
    return EditorialPlan(**fields)


def make_draft(**overrides: Any) -> Draft:
    text = "Adjusted EBITDA was negative $211 million in the third quarter of 2022."
    fields: dict[str, Any] = dict(
        candidate_id=make_candidate().candidate_id,
        package_id=make_package().package_id,
        sentences=(
            DraftSentence(
                index=0,
                text=text,
                kind=SentenceKind.REPORTED,
                fact_bindings=(
                    FactBinding(fact_id="obs:adjusted-ebitda:b",
                                rendered="negative $211 million",
                                char_start=text.index("negative"),
                                char_end=text.index("negative") + len("negative $211 million"),
                                metric_surface="Adjusted EBITDA",
                                period_surface="the third quarter of 2022"),
                ),
                # The handle the package above minted for the fact this sentence binds, read
                # off the row rather than spelled: `PassageCitation.evidence_handle` is required
                # with no default, and a handle written by hand in a fixture is a second
                # authority on a format §3.4 check 7 says has exactly one.
                citations=(PassageCitation(
                    passage_id="psg:1", document_id="doc:1", char_start=0, char_end=21,
                    evidence_handle=make_package().facts[0].evidence_handle),),
            ),
        ),
    )
    fields.update(overrides)
    return Draft(**fields)


@dataclass(frozen=True)
class RecordedRead:
    """One `read()` call, exactly as it arrived. `timeout_seconds` is recorded, not assumed."""

    statement: str
    parameters: dict[str, Any]
    timeout_seconds: float


@dataclass
class RecordedReadExecutor:
    """A `story.contracts.ReadQueryExecutor` with a script where the database would be.

    Structural conformance and no import of `story.providers`: the point of the protocol is
    that a stage can be exercised with no driver installed, and a fake that reached for the
    adapter to satisfy it would quietly undo that.

    `rows_by_statement` is keyed by the exact statement text, so a test that changes a query
    by one character stops matching — which is the failure a test wants, not one to smooth
    over with fuzzy lookup. `raises` makes an unreachable database a scriptable state.
    """

    rows_by_statement: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    default_rows: tuple[dict[str, Any], ...] = ()
    health: HealthStatus = field(
        default_factory=lambda: HealthStatus(ok=True, status="ok", detail="recorded"))
    raises: Exception | None = None
    calls: list[RecordedRead] = field(default_factory=list)
    closed: bool = False

    def read(
        self,
        statement: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], ...]:
        self.calls.append(RecordedRead(statement, dict(parameters), timeout_seconds))
        if self.raises is not None:
            raise self.raises
        return self.rows_by_statement.get(statement, self.default_rows)

    def verify_connectivity(self) -> HealthStatus:
        return self.health

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def recorded_executor() -> RecordedReadExecutor:
    return RecordedReadExecutor()


@pytest.fixture
def candidate() -> StoryCandidate:
    return make_candidate()


@pytest.fixture
def package() -> StoryEvidencePackage:
    return make_package()


@pytest.fixture
def plan() -> EditorialPlan:
    return make_plan()


@pytest.fixture
def draft() -> Draft:
    return make_draft()
