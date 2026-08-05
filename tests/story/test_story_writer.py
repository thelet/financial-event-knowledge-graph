"""§12's writer: what it refuses, what it hands to §13, and one run against the real model.

**The package below is the founder's demo candidate and every value in it is a real corpus
value**: `adjusted_gross_margin` 2022Q3 is `3.3`, `gaap_gross_margin` 2022Q3 is `-12.6`, the gap
is `15.899999999999999` and renders `15.9`, the subject is `opendoor`, and the candidate is
`cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:opendoor:2022Q3:9682f1c1c85a`
*(verified live 2026-08-03/04 by S3 and the census tests)*. It is built by hand from the S0
contract rather than read from `data/`, for the reason S9's fixtures are: a generation test must
run on a clean checkout where the run directory is absent. The observation-id digests are opaque
handles written by hand and nothing here recomputes one.

**Everything normal runs against a fake `StoryGenerationProvider`** driven through the protocol.
One test at the end is marked `live` and talks to the real Qwen server; it skips when nothing is
listening.

**The test that carries the demo claim is
`test_the_writers_own_output_survives_the_deterministic_verifier_end_to_end`** — a draft this
module produced from a fake generation, judged by S9's `DeterministicVerifier` with no finding at
all. It is the first place the two halves of the pipeline meet, and the division of labour it
demonstrates is asserted from both sides: everything §13 owns is shown *refusing* a writer draft
in the tests above it, and none of those refusals is re-implemented in `writer.py`.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import socket
from pathlib import Path
from typing import Any, Mapping

import pytest

from story.contracts import StoryGenerationProvider
from story.core.keys import package_content_digest
from story.core.models import (
    BudgetParameters,
    Calculation,
    CausalLanguage,
    Counterpoint,
    Draft,
    DraftSentence,
    EditorialPlan,
    EvidenceRole,
    FactBinding,
    GenerationResult,
    HealthStatus,
    KeyPoint,
    PackageBudget,
    PackagedDocument,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    PackagedSubject,
    PackagedWarning,
    PassageCitation,
    SentenceKind,
    Severity,
    StatementClass,
    StoryEvidencePackage,
)
from story.core.numerals import tokenize_numerals
from story.providers.portable_schema import PORTABLE_KEYWORDS, validate_portable_schema
from story.providers.public import (
    PINNED_TEMPERATURE,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    load_provider_config,
)
from story.stages.generation.prompts import (
    PLAIN_INVESTOR_STYLE,
    WARNING_QUALIFIER_PHRASES,
    WRITER_MAX_TOKENS,
    WRITER_OPERATIONS,
    WRITER_PROMPT_VERSION,
    WRITER_SCHEMA_NAME,
    WRITER_SYSTEM,
    StyleProfile,
    metric_surfaces_for,
    period_surface_for,
    writer_prompt,
    writer_schema,
    writer_system,
)
from story.stages.generation.writer import (
    BINDING_RENDERING_AMBIGUOUS,
    BINDING_RENDERING_NOT_IN_TEXT,
    CITATION_QUOTE_AMBIGUOUS,
    CITATION_QUOTE_NOT_IN_PASSAGE,
    MORE_THAN_ONE_CALCULATION,
    NO_SENTENCES,
    PLAN_NAMES_ANOTHER_PACKAGE,
    THESIS_ABANDONED,
    UNRESOLVABLE_FACT_ID,
    UNRESOLVABLE_PASSAGE_ID,
    DraftRejected,
    draft_from,
    draft_violations,
    render_markdown,
    write_story,
    writer_passages,
)
# The verifier is a *different stage*, and `writer.py` may not import it —
# `test_the_writer_module_reaches_no_graph_no_retrieval_and_no_verifier` asserts that. A test
# may, and must: the demo claim is that these two meet.
from story.stages.verification import DeterministicVerifier
from story.stages.verification.deterministic import REQUIRED_WARNING_QUALIFIERS
from story.stages.verification.period_grammar import resolve as resolve_period
from story.stages.packaging.counter_evidence import MATCH_BASIS_SAME_DOCUMENT

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_MODULE = REPO_ROOT / "story" / "stages" / "generation" / "writer.py"

CANDIDATE_ID = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
                "opendoor:2022Q3:9682f1c1c85a")
PACKAGE_ID = ("pkg:cross-metric-divergence-adjusted-gross-margin-gaap-gross-margin-opendoor-"
              "2022q3:4f2b91c7de08")
GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
EXTRACTION_RUN_ID = "extract-v1-lexical-833f7bcfbce9"
RUN_COMPLETE_SHA256 = "1cc8f7b01c040531" + "0" * 48
ONTOLOGY_DEFINITION_HASH = (
    "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34")

AGM_ID = "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:7c1a04e9b2d5"
GGM_ID = "obs:gaap-gross-margin:opendoor:2022Q3:normalized-table:1e77b0c9a3f4"

PASSAGE_ID = "psg:opendoor-10q-2022q3:margins-table"
COUNTER_PASSAGE_ID = "psg:opendoor-10q-2022q3:inventory-table"
DOCUMENT_ID = "doc:opendoor-10q-2022q3"

#: A margins table as the extraction lane normalises one. Both quoted texts occur verbatim,
#: which is §13.7 Rule A step 1 — measured to hold on 2,714/2,714 evidence rows.
PASSAGE_TEXT = (
    "Three Months Ended September 30, 2022\n"
    "Revenue $3,394\n"
    "Gross Margin (12.6)\n"
    "Adjusted Gross Margin 3.3\n"
)
COLUMN_LABEL = "Three Months Ended September 30, 2022"

#: Counter-evidence at **document** grain: a neighbouring table of the same filing that evidences
#: no packaged fact. §10's join is at document grain, which is why this shape is the ordinary one.
COUNTER_TEXT = "Inventory 2,152\n"


def make_fact(**overrides: Any) -> PackagedFact:
    fields: dict[str, Any] = dict(
        observation_id=AGM_ID,
        metric_id="adjusted_gross_margin",
        metric_label="Adjusted Gross Margin",
        period_key="2022Q3",
        period_start="2022-07-01",
        period_end="2022-09-30",
        shape="duration",
        value=3.3,
        unit="percent",
        scale="units",
        row_label="Adjusted Gross Margin",
        column_label=COLUMN_LABEL,
        source_lane="normalized_table",
        validation_state="ok",
        passage_id=PASSAGE_ID,
        document_id=DOCUMENT_ID,
        quoted_text="3.3",
    )
    fields.update(overrides)
    return PackagedFact(**fields)


GGM_FACT = make_fact(
    observation_id=GGM_ID,
    metric_id="gaap_gross_margin",
    metric_label="Gross Margin",
    value=-12.6,
    row_label="Gross Margin",
    quoted_text="(12.6)",
)

METRICS = (
    PackagedMetric(metric_id="adjusted_gross_margin", label="Adjusted Gross Margin",
                   unit="percent", allowed_units=("percent",),
                   aliases=("adjusted gross margin",),
                   mutually_distinct_groups=("margin_measures",)),
    PackagedMetric(metric_id="gaap_gross_margin", label="Gross Margin", unit="percent",
                   allowed_units=("percent",),
                   aliases=("gaap gross margin", "GAAP gross margin"),
                   mutually_distinct_groups=("margin_measures",)),
)


def make_package(**overrides: Any) -> StoryEvidencePackage:
    """The demo package, with `package_content_digest` stamped the way §10.3 computes it."""
    fields: dict[str, Any] = dict(
        package_id=PACKAGE_ID,
        candidate_id=CANDIDATE_ID,
        detector_id="detector:cross_metric_divergence",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id=GRAPH_RUN_ID,
        graph_projection_version="1.2.0",
        extraction_run_id=EXTRACTION_RUN_ID,
        run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor", entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(make_fact(), GGM_FACT),
        metrics=METRICS,
        primary_passages=(
            PackagedPassage(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=PASSAGE_TEXT,
                            char_count=len(PASSAGE_TEXT), passage_kind="normalized_table",
                            role=EvidenceRole.PRIMARY_SUPPORT),
        ),
        counter_evidence=(
            PackagedPassage(passage_id=COUNTER_PASSAGE_ID, document_id=DOCUMENT_ID,
                            text=COUNTER_TEXT, char_count=len(COUNTER_TEXT),
                            passage_kind="normalized_table", excerpted=True,
                            role=EvidenceRole.COUNTER_EVIDENCE,
                            match_basis=MATCH_BASIS_SAME_DOCUMENT),
        ),
        warnings=(
            PackagedWarning(code="filing_date_unknown", severity=Severity.ANNOTATE,
                            subject_ids=(CANDIDATE_ID,),
                            detail="the candidate's own filing date is not knowable"),
        ),
        documents=(PackagedDocument(document_id=DOCUMENT_ID, form="10-Q",
                                    document_type="10-Q", filing_date="2022-11-03"),),
        budget=PackageBudget(artifact_token_estimate=1180, prompt_token_estimate=1010,
                             section_counts={"facts": 2, "metrics": 2},
                             parameters=BudgetParameters()),
    )
    fields.update(overrides)
    package = StoryEvidencePackage(**fields)
    return package.with_content_digest(package_content_digest(package.digestible_payload()))


def make_plan(**overrides: Any) -> EditorialPlan:
    """An accepted plan for that package — the shape §11 hands the writer."""
    fields: dict[str, Any] = dict(
        candidate_id=CANDIDATE_ID,
        package_id=PACKAGE_ID,
        thesis="Opendoor's two gross margins were 15.9 points apart in 2022Q3.",
        why_it_matters="The adjusted figure is positive while the GAAP figure is not.",
        key_points=(
            KeyPoint(claim="Adjusted gross margin was 3.3% in 2022Q3.",
                     required_fact_ids=(AGM_ID,),
                     required_citation_passage_ids=(PASSAGE_ID,),
                     statement_class=StatementClass.REPORTED),
            KeyPoint(claim="GAAP gross margin was (12.6)% in the same quarter.",
                     required_fact_ids=(GGM_ID,),
                     required_citation_passage_ids=(PASSAGE_ID,),
                     statement_class=StatementClass.REPORTED),
        ),
        counterpoints=(
            Counterpoint(claim="The GAAP figure is negative in the same quarter.",
                         required_fact_ids=(GGM_ID,)),
        ),
        required_warnings=("filing_date_unknown",),
        causal_language=CausalLanguage.FORBIDDEN,
        structure=("What the two figures are", "What is not known"),
        prohibited_claims=("Any statement that the adjustment explains the gap.",),
    )
    fields.update(overrides)
    return EditorialPlan(**fields)


# -- a valid answer, and the fakes that return it ---------------------------------------------

AGM_TEXT = "Adjusted gross margin was 3.3% in the third quarter of 2022."
GGM_TEXT = "GAAP gross margin was -12.6% in the third quarter of 2022."
GAP_TEXT = "The gap between the two measures was 15.9 percentage points."
WARNING_TEXT = (
    "Both figures come from the same table, whose filing date is not recorded in this run.")


def binding(fact_id: str, rendered: str, metric_surface: str) -> dict[str, Any]:
    return {"fact_id": fact_id, "rendered": rendered, "metric_surface": metric_surface,
            "period_surface": "the third quarter of 2022"}


def citation(quote: str, passage_id: str = PASSAGE_ID) -> dict[str, Any]:
    return {"passage_id": passage_id, "quote": quote}


def sentence(text: str, kind: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"text": text, "kind": kind, "fact_bindings": [],
                           "calculation": [], "citations": []}
    row.update(overrides)
    return row


def valid_answer(**overrides: Any) -> dict[str, Any]:
    """What a conformant model returns for `make_package()` under `make_plan()`."""
    content: dict[str, Any] = {
        "title": "Two gross margins in one quarter",
        "sentences": [
            sentence(AGM_TEXT, "reported",
                     fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                     citations=[citation("Adjusted Gross Margin 3.3")]),
            sentence(GGM_TEXT, "reported",
                     fact_bindings=[binding(GGM_ID, "-12.6%", "GAAP gross margin")],
                     citations=[citation("Gross Margin (12.6)")]),
            sentence(GAP_TEXT, "calculated", calculation=[{
                "operation": "delta_pp",
                "input_observation_ids": [GGM_ID, AGM_ID],
                "expression": "adjusted_gross_margin - gaap_gross_margin",
                "result_rendered": "15.9 percentage points",
                "formula_version_id": "",
                "period_surface": ""}]),
            sentence(WARNING_TEXT, "connective"),
        ],
    }
    content.update(overrides)
    return content


class FakeWriteProvider:
    """A `StoryGenerationProvider` with a fixed answer and a record of what it was asked.

    Not a mock library and not a subclass: the protocol is structural, so the way to prove the
    writer uses nothing but the protocol is to hand it an object that implements exactly the
    protocol and nothing else.
    """

    def __init__(self, content: Mapping[str, Any] | None = None,
                 raises: Exception | None = None, model_id: str = "Qwen3.5-9B-Q4_K_M.gguf"):
        self.content = dict(content if content is not None else valid_answer())
        self.raises = raises
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    def generate(self, *, system: str, prompt: str, schema: Mapping[str, Any],
                 schema_name: str, max_tokens: int, temperature: float) -> GenerationResult:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "schema_name": schema_name, "max_tokens": max_tokens,
                           "temperature": temperature})
        if self.raises is not None:
            raise self.raises
        raw = json.dumps(self.content, sort_keys=True, separators=(",", ":"))
        return GenerationResult(
            content=self.content, raw_content=raw,
            model_id="/models/Qwen3.5-9B-Q4_K_M.gguf",
            prompt_tokens=1620, completion_tokens=418, total_tokens=2038, latency_ms=6120.0,
            raw_sha256="b" * 64,
            content_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            finish_reason="stop", attempts=1,
            metadata={"schema_name": schema_name, "configured_model": self.model_id})

    def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ok", detail="fake")


def write_with(
    provider: StoryGenerationProvider,
    package: StoryEvidencePackage | None = None,
    plan: EditorialPlan | None = None,
    *,
    style: StyleProfile = PLAIN_INVESTOR_STYLE,
):
    """Every test goes through this, annotated with the protocol, so the surface under test is
    the contract rather than the concrete fake."""
    return write_story(
        package if package is not None else make_package(),
        plan if plan is not None else make_plan(),
        provider=provider, style=style, length_target=5, max_tokens=WRITER_MAX_TOKENS)


def draft_of(answer: Mapping[str, Any], **kwargs: Any) -> Draft:
    return write_with(FakeWriteProvider(answer), **kwargs).draft


@pytest.fixture
def verifier() -> DeterministicVerifier:
    return DeterministicVerifier(
        graph_run_id=GRAPH_RUN_ID,
        run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
    )


def codes_of(verified) -> set[str]:  # type: ignore[no-untyped-def]
    return {found.code for found in verified.all_findings}


# -- rule: no retrieval, no graph, no search, no verifier ---------------------------------------


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_writer_module_reaches_no_graph_no_retrieval_and_no_verifier():
    """§12: the writer has a provider, a package and a plan, and no way to ask for anything else.

    The verifier is in the list for its own reason: a writer that could import §13 would be a
    writer that could hold a second, weaker copy of a refusal, and the two would eventually
    disagree about the same draft.
    """
    imported = module_imports(WRITER_MODULE)
    for forbidden in ("neo4j", "httpx", "requests"):
        assert not any(name.split(".")[0] == forbidden for name in imported), imported
    for stage in ("story.stages.retrieval", "story.stages.detection", "story.stages.ranking",
                  "story.stages.freshness", "story.stages.packaging",
                  "story.stages.verification"):
        assert not any(name.startswith(stage) for name in imported), imported
    assert "story.providers.neo4j_connection" not in imported


def test_the_writer_takes_a_package_a_plan_a_provider_a_style_and_two_budgets():
    """A signature with no room for a retriever or a passage list is §10.2.1 point 3 stated
    structurally: the slice cannot be supplied, so it can only be derived."""
    parameters = inspect.signature(write_story).parameters
    assert list(parameters) == ["package", "plan", "provider", "style", "length_target",
                                "max_tokens"]
    assert parameters["max_tokens"].default is inspect.Parameter.empty
    assert parameters["length_target"].default is inspect.Parameter.empty
    assert "temperature" not in parameters


def test_the_writer_prompt_names_no_tool_and_asks_for_no_search():
    prompt = writer_prompt(make_package(), make_plan(), writer_passages(make_package()))
    for tool in ("search_passages", "find_counter_evidence", "get_fact_evidence", "terms["):
        assert tool not in prompt and tool not in WRITER_SYSTEM


def test_the_same_package_and_plan_render_the_same_prompt_every_time():
    """The prompt is half of the request identity the replay store keys on (§14)."""
    package, plan = make_package(), make_plan()
    first = writer_prompt(package, plan, writer_passages(package))
    second = writer_prompt(make_package(), make_plan(), writer_passages(make_package()))
    assert first == second


# -- rule: §10.2.1 point 3, the slice is derived from fact bindings by code ----------------------


def test_the_writers_passage_slice_is_derived_from_fact_bindings_and_takes_no_plan():
    """§10.2.1 point 3, in the form that cannot be argued with: there is no plan parameter."""
    assert list(inspect.signature(writer_passages).parameters) == ["package"]
    passages = writer_passages(make_package())
    assert [passage.passage_id for passage in passages] == [PASSAGE_ID]


def test_a_plan_that_cites_fewer_passages_does_not_narrow_the_writers_universe():
    """The correction §10.2.1 point 3 makes: `required_citation_passage_ids` is model output, and
    letting it choose the slice would let one model filter the next model's universe."""
    package = make_package()
    narrow = make_plan(key_points=(
        KeyPoint(claim="Adjusted gross margin was 3.3% in 2022Q3.",
                 required_fact_ids=(AGM_ID,), required_citation_passage_ids=(),
                 statement_class=StatementClass.REPORTED),))
    assert (writer_prompt(package, narrow, writer_passages(package))
            .count(f"[{PASSAGE_ID}]") == 1)
    assert writer_passages(package) == writer_passages(package)


def test_counter_evidence_that_evidences_no_packaged_fact_is_not_in_the_slice():
    """§10's counter-evidence join is at document grain, and §13.7 refuses citing one as support.

    Excluding it is the same answer from the other end: the writer is not shown text it could
    only misuse, and the counterpoint that rests on it can be honoured only by binding a fact.
    """
    package = make_package()
    assert COUNTER_PASSAGE_ID not in {p.passage_id for p in writer_passages(package)}
    assert COUNTER_PASSAGE_ID not in writer_prompt(package, make_plan(),
                                                   writer_passages(package))


def test_every_passage_the_writer_is_shown_arrives_whole_and_not_excerpted():
    """§10.2.1 point 2: excerpting is never applied to a passage a fact is bound to, because
    §13.7's Rule A needs the whole table to reconstruct a cell."""
    prompt = writer_prompt(make_package(), make_plan(), writer_passages(make_package()))
    assert PASSAGE_TEXT.strip() in prompt.replace("\n      ", "\n")
    assert all(not passage.excerpted for passage in writer_passages(make_package()))


# -- rule: §15.3's portable subset ---------------------------------------------------------------


def test_the_writer_schema_is_inside_the_portable_subset():
    validate_portable_schema(writer_schema())


def walk_schema(schema: Mapping[str, Any], path: str = "$"):
    yield path, schema
    for name, subschema in (schema.get("properties") or {}).items():
        yield from walk_schema(subschema, f"{path}.{name}")
    if isinstance(schema.get("items"), Mapping):
        yield from walk_schema(schema["items"], f"{path}[]")


def test_the_writer_schema_uses_no_keyword_llama_cpp_would_silently_drop():
    """`minItems`, `maxLength`, `pattern` and `anyOf` are dropped by the grammar *and* ignored by
    the local checker, so a schema using one is unconstrained without saying so (§15.3)."""
    for path, subschema in walk_schema(writer_schema()):
        assert set(subschema) <= PORTABLE_KEYWORDS, f"{path}: {sorted(subschema)}"


def test_every_object_in_the_writer_schema_forbids_extras_and_requires_every_property():
    for path, subschema in walk_schema(writer_schema()):
        if subschema.get("type") == "object":
            assert subschema.get("additionalProperties") is False, path
            assert sorted(subschema["required"]) == sorted(subschema["properties"]), path
        if subschema.get("type") == "array":
            assert isinstance(subschema.get("items"), Mapping), path


def test_a_calculation_is_an_array_because_the_portable_subset_has_no_nullable_object():
    """§15.3 has no `null` type and no `anyOf`, and requires every property. An empty array is
    the only portable spelling of *"this sentence derived nothing"*."""
    calculation = writer_schema()["properties"]["sentences"]["items"]["properties"]["calculation"]
    assert calculation["type"] == "array"
    assert calculation["items"]["type"] == "object"


def test_the_writers_operations_exclude_the_machinery_it_could_never_satisfy():
    """§13.14's machinery, minus the one form this package can actually support.

    A superlative needs `extremum` over a full comparison set and §10.2 caps `facts[]` at
    twelve; an absence claim needs `absence`, which a bounded package can never establish; an
    ordering needs `temporal_order` and all three `executive_change` events carry
    `occurred_on: null`. None is in the grammar, so the writer cannot half-support one.

    **`compare_levels` is in the grammar, and it has to be.** §13.14 requires a comparative to
    be expressed as `compare_levels` or `compare_deltas`, and with neither in the enum the
    construction was undeclarable — which refused the demo's own true, correctly bound sentence
    for a reason no rewrite could reach. `compare_deltas` stays out because a side of it is a
    change of one metric across two periods, and every fact in this package is 2022Q3.
    """
    for operation in ("extremum", "compare_deltas", "absence", "temporal_order",
                      "delta_relative"):
        assert operation not in WRITER_OPERATIONS
    assert "compare_levels" in WRITER_OPERATIONS
    schema = writer_schema()["properties"]["sentences"]["items"]["properties"]["calculation"]
    assert schema["items"]["properties"]["operation"]["enum"] == list(WRITER_OPERATIONS)


# -- rule: the request is pinned, and style never touches the evidence ---------------------------


def test_the_request_carries_the_pinned_temperature_the_schema_name_and_the_style_system():
    provider = FakeWriteProvider()
    write_with(provider)
    call = provider.calls[0]
    assert call["temperature"] == PINNED_TEMPERATURE == 0.0
    assert call["schema_name"] == WRITER_SCHEMA_NAME
    assert call["max_tokens"] == WRITER_MAX_TOKENS
    assert call["system"] == writer_system(PLAIN_INVESTOR_STYLE)
    assert PLAIN_INVESTOR_STYLE.voice in call["system"]


def test_two_style_profiles_change_the_system_message_and_nothing_the_evidence_says():
    """§12: *"a style change must not be able to change a number"*.

    The strong half is structural and is asserted here — `writer_prompt` has no style parameter,
    so the evidence rendering is byte-identical under two profiles and the binding fact ids
    cannot differ. The weak half is what a fake provider can show: two runs, two profiles,
    identical bindings. Stated rather than dressed up, because a fake that returns one answer
    would produce identical bindings whatever the writer did.
    """
    assert "style" not in inspect.signature(writer_prompt).parameters
    terse = StyleProfile(profile_id="terse:1", voice="terse", sentence_length="under 15 words")
    first, second = FakeWriteProvider(), FakeWriteProvider()
    plain_draft = write_with(first).draft
    terse_draft = write_with(second, style=terse).draft

    assert first.calls[0]["prompt"] == second.calls[0]["prompt"]
    assert first.calls[0]["system"] != second.calls[0]["system"]
    assert ([b.fact_id for s in plain_draft.sentences for b in s.fact_bindings]
            == [b.fact_id for s in terse_draft.sentences for b in s.fact_bindings])
    assert terse_draft.style_profile_id == "terse:1"


# -- rule: the surfaces the writer is told to use are the ones the verifier accepts ---------------


def test_the_metric_surface_offered_to_the_writer_is_never_the_ambiguous_one():
    """§13.5's headline case: `gaap_gross_margin`'s own label *is* "Gross Margin", and
    `"gross margin" ⊂ "adjusted gross margin"`, so the surface a writer would reach for first is
    the refused one. It is filtered out before the writer ever sees it."""
    package = make_package()
    # `"gaap gross margin"` and `"GAAP gross margin"` are one surface, not two: the index
    # normalises case, and offering both would be offering the model a choice with no content.
    assert metric_surfaces_for(package, "gaap_gross_margin") == ("gaap gross margin",)
    assert metric_surfaces_for(package, "adjusted_gross_margin") == ("Adjusted Gross Margin",)
    assert "Gross Margin" not in metric_surfaces_for(package, "gaap_gross_margin")


@pytest.mark.parametrize(
    "start,end,instant,expected",
    [("2022-07-01", "2022-09-30", None, "the third quarter of 2022"),
     ("2022-01-01", "2022-03-31", None, "the first quarter of 2022"),
     ("2022-04-01", "2022-06-30", None, "the second quarter of 2022"),
     ("2022-10-01", "2022-12-31", None, "the fourth quarter of 2022"),
     ("2022-01-01", "2022-09-30", None, "the nine months ended September 30, 2022"),
     ("2022-01-01", "2022-06-30", None, "the first half of 2022"),
     ("2022-01-01", "2022-12-31", None, "fiscal 2022"),
     (None, None, "2022-09-30", "September 30, 2022")])
def test_every_period_surface_the_writer_emits_resolves_back_to_its_own_endpoints(
        start, end, instant, expected):
    """The grammar is §13.4's and this is the writing direction of it, so the pair is
    round-tripped rather than assumed to agree. A surface that resolved to different endpoints
    would be `period_mismatch` on a draft the writer told the model to produce."""
    fact = make_fact(period_start=start, period_end=end, instant_date=instant,
                     period_key="x", shape="instant" if instant else "duration")
    surface = period_surface_for(fact)
    assert surface == expected
    resolved = resolve_period(surface)
    assert resolved.resolved
    assert (resolved.period_start, resolved.period_end, resolved.instant_date) == (
        start, end, instant)


def test_a_window_outside_the_closed_grammar_is_offered_no_surface_at_all():
    """§13.4 refuses a surface it cannot resolve; the honest thing is to tell the writer that
    fact may not be named rather than to invent a form the grammar will reject."""
    fact = make_fact(period_start="2022-02-01", period_end="2022-11-30", period_key="odd")
    assert period_surface_for(fact) is None
    package = make_package(facts=(fact,))
    assert "do not write about this fact" in writer_prompt(
        package, make_plan(), writer_passages(package))


def test_the_writer_is_shown_the_same_warning_phrases_the_verifier_requires():
    """A stage may not import another stage, so §13's qualifier table is restated in `prompts.py`.

    The duplication is deliberate and this is what keeps it honest: a writer not shown the
    phrases would be refused for silence nobody told it how to break, and two copies that could
    drift would be worse than one import.
    """
    assert WARNING_QUALIFIER_PHRASES == REQUIRED_WARNING_QUALIFIERS
    prompt = writer_prompt(make_package(), make_plan(), writer_passages(make_package()))
    assert "filing_date_unknown" in prompt
    assert "say it with one of: filing date | " in prompt


# -- rule: the writer refuses what only it can see ------------------------------------------------


def test_a_binding_to_a_fact_the_package_does_not_hold_is_refused():
    """§12: the writer may not add a fact. This is *"add facts"* refused before verification."""
    answer = valid_answer()
    answer["sentences"][0]["fact_bindings"] = [
        binding("obs:adjusted-gross-margin:opendoor:2021Q3:normalized-table:invented", "3.3%",
                "Adjusted Gross Margin")]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert UNRESOLVABLE_FACT_ID in raised.value.codes


def test_a_calculation_over_an_input_the_package_does_not_hold_is_refused():
    answer = valid_answer()
    answer["sentences"][2]["calculation"][0]["input_observation_ids"] = [GGM_ID, "obs:invented"]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert UNRESOLVABLE_FACT_ID in raised.value.codes


def test_a_rendering_its_own_sentence_does_not_contain_is_refused():
    """§12: the binding declares which span of the writer's own text states the fact. A rendering
    the text does not carry points the whole of §13 at the wrong characters."""
    answer = valid_answer()
    answer["sentences"][0]["fact_bindings"] = [
        binding(AGM_ID, "3.30%", "Adjusted Gross Margin")]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert raised.value.codes == (BINDING_RENDERING_NOT_IN_TEXT,)


def test_a_rendering_that_occurs_twice_in_its_own_sentence_is_refused_rather_than_guessed():
    """Two occurrences and no ground for choosing: locating the substring is deterministic only
    while it is unique, and a verifier handed the wrong one checks the wrong claim."""
    text = "Adjusted gross margin was 3.3%, and 3.3% is what the reconciliation shows."
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                 citations=[citation("Adjusted Gross Margin 3.3")])])
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert raised.value.codes == (BINDING_RENDERING_AMBIGUOUS,)
    assert "occurs 2 times" in str(raised.value)


def test_a_citation_to_a_passage_outside_the_writers_slice_is_refused():
    """§10.2.1 point 3 again, from the answer's side: the counter-evidence passage is not in the
    slice, so a citation naming it is a citation to text the writer was never shown."""
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation("Inventory 2,152", COUNTER_PASSAGE_ID)]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert raised.value.codes == (UNRESOLVABLE_PASSAGE_ID,)


def test_a_quote_the_cited_passage_does_not_contain_is_refused():
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation("Adjusted Gross Margin 4.4")]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert raised.value.codes == (CITATION_QUOTE_NOT_IN_PASSAGE,)


def test_a_quote_that_occurs_twice_in_its_passage_resolves_to_no_span_and_is_refused():
    """§13.7 measured 523 `quoted_text` strings occurring more than once inside their own
    passage; a citation that cannot say which occurrence resolves to nothing."""
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation("Gross Margin")]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert raised.value.codes == (CITATION_QUOTE_AMBIGUOUS,)


def test_two_calculations_on_one_sentence_are_refused_rather_than_one_being_picked():
    answer = valid_answer()
    answer["sentences"][2]["calculation"] = [
        answer["sentences"][2]["calculation"][0],
        {"operation": "difference", "input_observation_ids": [AGM_ID, GGM_ID],
         "expression": "a - b", "result_rendered": "15.9", "formula_version_id": "",
         "period_surface": ""}]
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer))
    assert raised.value.codes == (MORE_THAN_ONE_CALCULATION,)


def test_a_draft_resting_on_no_fact_the_plan_named_is_refused_as_a_changed_thesis():
    """§12: *"the writer must not change the thesis"*, in the only form the draft contract can
    express — a `Draft` has no thesis field, so what is checkable is that the post rests on the
    evidence the plan chose."""
    other = make_fact(observation_id="obs:revenue:opendoor:2022Q3:normalized-table:aa01",
                      metric_id="revenue", metric_label="Revenue", value=3394.0, unit="USD",
                      currency="USD", scale="millions", row_label="Revenue",
                      quoted_text="3,394")
    package = make_package(facts=(make_fact(), GGM_FACT, other))
    text = "Revenue was $3,394 million in the third quarter of 2022."
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[{"fact_id": other.observation_id, "rendered": "$3,394 million",
                                 "metric_surface": "Revenue",
                                 "period_surface": "the third quarter of 2022"}],
                 citations=[citation("Revenue $3,394")])])
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(answer), package)
    assert THESIS_ABANDONED in raised.value.codes


def test_a_draft_with_no_sentence_at_all_is_refused():
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(valid_answer(sentences=[])))
    assert NO_SENTENCES in raised.value.codes


def test_a_plan_built_against_another_package_is_refused_before_the_model_is_called():
    """Spending a generation to discover a mismatch would put a wrong answer in the replay store
    under a request that looked legitimate."""
    provider = FakeWriteProvider()
    with pytest.raises(DraftRejected) as raised:
        write_with(provider, plan=make_plan(package_id="pkg:some-other-package:0000"))
    assert raised.value.codes == (PLAN_NAMES_ANOTHER_PACKAGE,)
    assert provider.calls == []


def test_a_hand_written_draft_is_judged_by_the_same_function_as_a_generated_one():
    """`draft_violations` is pure, so a draft no model produced is judged by one function."""
    package, plan = make_package(), make_plan()
    text = AGM_TEXT
    hand = Draft(
        candidate_id=CANDIDATE_ID, package_id=PACKAGE_ID,
        sentences=(DraftSentence(
            index=0, text=text, kind=SentenceKind.REPORTED,
            fact_bindings=(FactBinding(
                fact_id=AGM_ID, rendered="3.3%", char_start=0, char_end=4,
                metric_surface="Adjusted Gross Margin",
                period_surface="the third quarter of 2022"),)),))
    assert [violation.code for violation in draft_violations(hand, package, plan)] == [
        BINDING_RENDERING_NOT_IN_TEXT]


# -- rule: malformed output fails distinctly, and is never retried ---------------------------------


def test_content_that_is_not_json_fails_as_a_response_error_and_is_asked_for_once():
    provider = FakeWriteProvider(raises=StoryProviderResponseError(
        "assistant content is not JSON: Expecting ',' delimiter: line 67 column 4"))
    with pytest.raises(StoryProviderResponseError):
        write_with(provider)
    assert len(provider.calls) == 1


def test_an_answer_missing_a_required_property_fails_as_a_schema_error_and_is_never_retried():
    answer = valid_answer()
    del answer["sentences"][0]["citations"]
    provider = FakeWriteProvider(answer)
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(provider)
    assert any("citations" in violation for violation in raised.value.violations)
    assert len(provider.calls) == 1


def test_a_sentence_kind_outside_the_enum_fails_as_a_schema_error():
    answer = valid_answer()
    answer["sentences"][0]["kind"] = "causal"
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(FakeWriteProvider(answer))
    assert any("kind" in violation for violation in raised.value.violations)


def test_an_operation_outside_the_enum_fails_as_a_schema_error():
    """The narrowing has teeth only if the grammar refuses the wider name."""
    answer = valid_answer()
    answer["sentences"][2]["calculation"][0]["operation"] = "extremum"
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(FakeWriteProvider(answer))
    assert any("extremum" in violation for violation in raised.value.violations)


def test_an_answer_of_the_wrong_shape_fails_as_a_schema_error_rather_than_a_draft_rejection():
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(FakeWriteProvider(valid_answer(sentences="four of them")))
    assert any("expected array" in violation for violation in raised.value.violations)


def test_a_schema_violation_and_a_draft_rejection_are_different_failures():
    """One is a statement about the runtime, the other about §12; a caller must tell them apart."""
    assert not issubclass(DraftRejected, StoryProviderSchemaError)
    assert not issubclass(StoryProviderSchemaError, DraftRejected)


# -- the accepted draft ----------------------------------------------------------------------------


def test_a_conformant_answer_becomes_a_structured_draft_with_located_spans():
    written = write_with(FakeWriteProvider())
    draft = written.draft
    assert draft.candidate_id == CANDIDATE_ID and draft.package_id == PACKAGE_ID
    assert draft.prompt_version == WRITER_PROMPT_VERSION
    assert draft.model_id == "Qwen3.5-9B-Q4_K_M.gguf"
    assert [s.index for s in draft.sentences] == [0, 1, 2, 3]
    assert [s.kind for s in draft.sentences] == [
        SentenceKind.REPORTED, SentenceKind.REPORTED, SentenceKind.CALCULATED,
        SentenceKind.CONNECTIVE]
    first = draft.sentences[0].fact_bindings[0]
    assert first.rendered == "3.3%"
    assert draft.sentences[0].text[first.char_start:first.char_end] == "3.3%"
    assert written.generation.total_tokens == 2038


def test_a_citation_is_located_in_the_passage_and_rebased_to_the_full_passage_text():
    """§10.2.1 point 2: `PackagedPassage.char_start` is the offset of `text[0]` into the full
    `:Passage.text`, so a citation an evidence panel can resolve is an absolute one."""
    draft = draft_of(valid_answer())
    citation_handle = draft.sentences[0].citations[0]
    assert citation_handle.passage_id == PASSAGE_ID
    assert citation_handle.document_id == DOCUMENT_ID
    start = PASSAGE_TEXT.index("Adjusted Gross Margin 3.3")
    assert (citation_handle.char_start, citation_handle.char_end) == (
        start, start + len("Adjusted Gross Margin 3.3"))


def test_the_identity_fields_come_from_the_package_and_not_from_the_model():
    """A model that returned a different candidate id could not re-key the artifact."""
    answer = valid_answer(title="cand:invented:9999")
    draft = draft_of(answer)
    assert draft.candidate_id == CANDIDATE_ID and draft.package_id == PACKAGE_ID


def test_an_empty_formula_version_is_read_as_no_declared_formula():
    """§15.3 has no null, so `""` is how the grammar spells *"the ontology declares no formula
    for this derivation"* — a cross-metric gap is arithmetic, not an ontology identity."""
    draft = draft_of(valid_answer())
    assert draft.sentences[2].calculation is not None
    assert draft.sentences[2].calculation.formula_version_id is None


# -- the Markdown renderer, which may only read the draft --------------------------------------------


def test_the_markdown_renderer_takes_the_structured_draft_and_nothing_else():
    """§12: *"a rendered Markdown post may be produced only from the structured draft"*. One
    argument, and it is the draft: there is nowhere to pass the model's raw answer."""
    assert list(inspect.signature(render_markdown).parameters) == ["draft"]
    written = write_with(FakeWriteProvider())
    assert isinstance(written.draft, Draft)
    assert not hasattr(written, "markdown")


def test_the_rendered_post_carries_every_sentence_verbatim_and_invents_no_numeral():
    draft = draft_of(valid_answer())
    rendered = render_markdown(draft)
    assert rendered.startswith("# Two gross margins in one quarter")
    for sentence_row in draft.sentences:
        assert sentence_row.text in rendered
    written_numerals = {token.text for token in tokenize_numerals(rendered)}
    declared = {token.text for sentence_row in draft.sentences
                for token in tokenize_numerals(sentence_row.text)}
    declared |= {token.text for token in tokenize_numerals(draft.title)}
    # The panels quote ids and character offsets, which are not prose; what must hold is that no
    # numeral appears in the *post* that the draft did not already carry.
    body = rendered.split("## Sources")[0]
    assert {token.text for token in tokenize_numerals(body)} <= declared
    assert written_numerals  # the post does state figures


def test_the_rendered_post_shows_its_citations_and_its_derivation():
    rendered = render_markdown(draft_of(valid_answer()))
    assert "## Sources" in rendered and PASSAGE_ID in rendered
    assert "## Derivations" in rendered
    assert "adjusted_gross_margin - gaap_gross_margin = 15.9 percentage points" in rendered


def test_rendering_the_same_draft_twice_produces_the_same_bytes():
    draft = draft_of(valid_answer())
    assert render_markdown(draft) == render_markdown(draft)


# ---------------------------------------------------------------------------------------
# The prohibitions §13 owns. Every one is written by the writer and refused by the verifier,
# and none of them is re-implemented in `writer.py`.
# ---------------------------------------------------------------------------------------


def test_an_invented_number_the_draft_does_not_declare_is_refused_by_the_verifier(verifier):
    """§13.1: a numeral nobody declared is a claim nobody checked. The writer declares spans and
    does not police numerals — that division is the reason §12 makes the draft structured."""
    text = "Adjusted gross margin was 3.3% in the third quarter of 2022, down from 9.9%."
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                 citations=[citation("Adjusted Gross Margin 3.3")]),
        valid_answer()["sentences"][3]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "unbound_numeral" in codes_of(verified)
    assert verified.passed is False


def test_an_invented_date_is_refused_because_nothing_in_the_draft_binds_it(verifier):
    """§13.8 and §13.1: the package's own `filing_date_unknown` warning is the reason a date is
    the easiest thing in this candidate to invent."""
    text = "The figures were filed on November 3, 2022."
    answer = valid_answer(sentences=[valid_answer()["sentences"][0],
                                     sentence(text, "connective")])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "unbound_numeral" in codes_of(verified)


def test_an_invented_entity_is_refused_as_a_foreign_subject(verifier):
    """§13.6: all 2,704 observations carry `subject_entity_id: "opendoor"`, so a comparative post
    is not verifiable and must not be drafted."""
    text = "Adjusted gross margin was 3.3% in the third quarter of 2022, unlike Offerpad."
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                 citations=[citation("Adjusted Gross Margin 3.3")]),
        valid_answer()["sentences"][3]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "foreign_subject_named" in codes_of(verified)


def test_unsupported_causation_is_refused_even_though_the_plan_forbade_it_in_words(verifier):
    """§13.10 A: LLM-originated causation is banned unconditionally, and the plan's
    `causal_language: forbidden` is rendered into the prompt as an instruction — which is a
    request. This is the refusal."""
    assert "FORBIDDEN" in writer_prompt(make_package(), make_plan(),
                                        writer_passages(make_package()))
    text = ("Adjusted gross margin was 3.3% in the third quarter of 2022 because the "
            "adjustment excluded inventory writedowns.")
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                 citations=[citation("Adjusted Gross Margin 3.3")]),
        valid_answer()["sentences"][3]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "causal_construction_forbidden" in codes_of(verified)


def test_a_dropped_required_warning_is_refused(verifier):
    """§12: the writer must not omit a `required_warning`. Dropping the last sentence drops the
    only place the post says "filing date"."""
    answer = valid_answer(sentences=valid_answer()["sentences"][:3])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    absent = [f for f in verified.all_findings if f.code == "required_warning_absent"]
    assert absent and absent[0].observed == "filing_date_unknown"
    assert verified.passed is False


def test_a_dropped_plan_counterpoint_is_refused(verifier):
    """§17.14: requiring counterpoints in the *plan* was satisfiable with one ungrounded
    sentence, and no §13 check required one to survive into the draft. This is that check, seen
    from the writer's side — the draft that drops the GAAP figure drops the counterpoint."""
    rows = valid_answer()["sentences"]
    answer = valid_answer(sentences=[rows[0], rows[2], rows[3]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "required_counterpoint_absent" in codes_of(verified)
    assert verified.passed is False


def test_document_grain_counter_evidence_cannot_be_cited_by_the_writer_and_is_refused_if_it_is(
        verifier):
    """The prohibition from both ends.

    The writer's slice excludes the passage, so the ordinary path refuses the citation as
    `unresolvable_passage_id` (asserted above). Handed the passage anyway — the state a future
    caller could reach by passing its own slice — the draft builds, and §13.7 refuses it as
    `counter_evidence_cited_as_support`: presenting a neighbouring table of the same filing as
    support for a cited cell presents an association as a contradiction's opposite.
    """
    package = make_package()
    smuggled = (*writer_passages(package), *package.counter_evidence)
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation("Inventory 2,152", COUNTER_PASSAGE_ID)]
    draft = draft_from(answer, package, make_plan(), passages=smuggled, model_id="fake")
    verified = verifier.verify(draft, package, make_plan())
    assert "counter_evidence_cited_as_support" in codes_of(verified)


def test_a_percentage_where_percentage_points_are_meant_is_refused(verifier):
    """§13.3: the two readings differ by `100/|v1|`, which is base-dependent. `15.9%` is the
    single most likely factual error in this candidate, and rule 7 of the system prompt exists
    for it."""
    assert "percentage points" in WRITER_SYSTEM
    text = "The gap between the two measures was 15.9%."
    rows = valid_answer()["sentences"]
    answer = valid_answer(sentences=[rows[0], rows[1], sentence(text, "calculated", calculation=[{
        "operation": "delta_pp", "input_observation_ids": [GGM_ID, AGM_ID],
        "period_surface": "",
        "expression": "adjusted_gross_margin - gaap_gross_margin",
        "result_rendered": "15.9%", "formula_version_id": ""}]), rows[3]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "percentage_point_surface_missing" in codes_of(verified)


def test_a_calculated_sentence_that_cites_a_passage_is_refused(verifier):
    """§13.9: `claims.yaml` gives `calculated` `optional_fields: []` — no filed-passage field is
    permitted. The gap is a calculation over two observations, not a reported fact."""
    answer = valid_answer()
    answer["sentences"][2]["citations"] = [citation("Adjusted Gross Margin 3.3")]
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "calculated_sentence_cites_passage" in codes_of(verified)


def test_the_bare_metric_surface_the_writer_was_told_not_to_use_is_refused(verifier):
    """§13.5: `"gross margin"` resolves to both margins and is refused, which is why the prompt
    offers `"GAAP gross margin"` and `"Adjusted Gross Margin"` and nothing shorter."""
    answer = valid_answer()
    answer["sentences"][1]["fact_bindings"] = [binding(GGM_ID, "-12.6%", "gross margin")]
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "metric_surface_ambiguous" in codes_of(verified)


def test_a_uniqueness_claim_is_refused_and_the_writer_cannot_declare_the_machinery(verifier):
    """§13.14's headline attack, and it is false here: `adjusted_gross_margin` is negative in
    **2022Q4 (−3.2) and 2023Q1 (−3.3)**, so *"the only negative quarter"* is wrong about this
    metric while the same sentence about GAAP gross margin is right — unfalsifiable by
    inspection. The writer's grammar carries no `extremum`, so it cannot even claim the
    machinery."""
    text = "That was the only quarter with a negative adjusted gross margin."
    rows = valid_answer()["sentences"]
    answer = valid_answer(sentences=[rows[0], rows[1], sentence(text, "connective"), rows[3]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "unsupported_superlative" in codes_of(verified)
    assert verified.passed is False


def test_a_claim_smuggled_into_the_title_is_refused_because_no_binding_can_reach_it(verifier):
    """D5 found the title was an unbound hole and closed it: §12 gives bindings, calculations and
    citations to *sentences*, so a headline is the cheapest place for an unsupported claim to
    survive into a published post. Rule 13 of the system prompt is the writer's half."""
    verified = verifier.verify(
        draft_of(valid_answer(title="Opendoor's worst quarter ever, in 2 charts")),
        make_package(), make_plan())
    title = verified.check("title")
    assert {f.code for f in title.findings} == {"unsupported_superlative", "unbound_numeral"}
    assert verified.passed is False


# ---------------------------------------------------------------------------------------
# The demo claim in miniature
# ---------------------------------------------------------------------------------------


def test_the_writers_own_output_survives_the_deterministic_verifier_end_to_end(verifier):
    """**S8 into S9, with nothing in between.** A draft this module built from one generation,
    judged by the deterministic verifier against the same package and plan, with no finding at
    all — not one WARN, not one ANNOTATE.

    Every value is the founder's candidate's own: `3.3`, `-12.6`, and a gap of
    `15.899999999999999` rendered `15.9 percentage points`. The calculation differences two
    *different* metrics, which §13.9's original *"sharing metric and unit"* clause would have
    refused and its 2026-08-04 correction permits — that clause is the story.
    """
    package, plan = make_package(), make_plan()
    written = write_with(FakeWriteProvider(), package, plan)

    verified = verifier.verify(written.draft, package, plan)
    assert verified.all_findings == (), [f.code for f in verified.all_findings]
    assert verified.passed is True

    # The panels §13.17 requires, populated from the writer's own declarations.
    assert [entry.fact_id for entry in verified.fact_ledger] == [AGM_ID, GGM_ID]
    assert len(verified.calculation_ledger) == 1
    assert verified.calculation_ledger[0].recomputed_value == 15.899999999999999
    assert round(verified.calculation_ledger[0].recomputed_value, 1) == 15.9

    # And the post is rendered from the draft that passed, never from the model's answer.
    rendered = render_markdown(written.draft)
    assert "15.9 percentage points" in rendered and "15.9%" not in rendered


# ---------------------------------------------------------------------------------------
# The real server
# ---------------------------------------------------------------------------------------


def server_is_listening(base_url: str, timeout_seconds: float = 1.0) -> bool:
    host, _, port = base_url.rsplit("/", 1)[-1].partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=timeout_seconds):
            return True
    except OSError:
        return False


@pytest.mark.live
def test_the_real_model_writes_this_post_and_the_rules_judge_what_comes_back():
    """S8 through the real transport, judged by §12 and then by §13.

    **This test does not assert that the model produces a publishable post, and the measurement
    is the point.** Recorded 2026-08-04 against the running Qwen3.5-9B-Q4_K_M, same package,
    same plan, temperature 0.0 — the observed outcomes are written into the report for this
    step; a draft §13 refuses with a structured explanation is a valid result and is not tuned
    away.

    What is asserted is what must hold on every run: exactly one attempt, no retry, and either a
    draft whose every id resolves and whose every span was located, or a typed refusal naming a
    §12 code. §13's verdict on it is reported through the skip message rather than asserted,
    because whether a 9B model's prose survives §13.14 is not a claim this repository makes.

    The planner's own live note applies here and the writer's prompt is larger: the model is
    unreliable on this runtime — roughly a quarter of planner calls hit a repetition loop inside
    an unbounded free-text field, and §15.3's portable subset has no `maxLength` with which to
    bound one.
    """
    from story.providers.openai_compatible import StoryOpenAICompatibleProvider

    config = load_provider_config()
    if not server_is_listening(config.base_url):
        pytest.skip(f"no model server listening on {config.base_url}")

    package, plan = make_package(), make_plan()
    with StoryOpenAICompatibleProvider(config) as provider:
        try:
            written = write_story(package, plan, provider=provider,
                                  length_target=5, max_tokens=WRITER_MAX_TOKENS)
        except DraftRejected as rejected:
            pytest.skip(f"the live model's draft was refused by §12: {rejected.codes}")
        except StoryProviderSchemaError as violated:
            pytest.skip(f"the live model's answer violated the schema: {violated.violations}")
        except StoryProviderResponseError as unparseable:
            pytest.skip(f"the live model returned no usable object: {unparseable}")

    assert draft_violations(written.draft, package, plan) == ()
    assert written.generation.attempts == 1
    assert written.generation.finish_reason == "stop"
    assert written.generation.prompt_tokens + written.generation.completion_tokens < 8192

    verified = DeterministicVerifier(
        graph_run_id=GRAPH_RUN_ID, run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH).verify(written.draft, package, plan)
    if not verified.passed:
        pytest.skip("the live draft is structurally sound and §13 refuses it: "
                    + ", ".join(sorted(codes_of(verified))))
    assert verified.passed is True
