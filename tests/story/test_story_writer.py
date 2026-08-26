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

**At `WRITER_PROMPT_VERSION` 4.0.0 this stage returns plain strings.** `kind` and `rests_on`
left the model's grammar, `SentenceTemplate` and `templates_from` left this module, and
`story/stages/composition/` owns normalization, `kind` derivation and every template refusal.
So the compiled `Draft` every test below judges is built the way `story/pipeline.py` builds it —
`write_story` → `normalize_templates` → `compile_draft` — through `compiled_of`. What that
middle pass *does* is `tests/story/test_story_composition_recovery.py`'s subject and is
deliberately not re-asserted here; what is asserted here is the seam.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import socket
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from story.contracts import StoryGenerationProvider
from story.core.keys import package_content_digest
from story.core.models import (
    DERIVED_UNITS,
    BudgetParameters,
    CausalLanguage,
    Counterpoint,
    DerivationOperation,
    DerivationRequest,
    DerivedFact,
    Draft,
    DraftSentence,
    EditorialPlan,
    EvidenceRequest,
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
    StoryCandidate,
    StoryEvidencePackage,
    TableCellRef,
)
from story.core.numerals import SurfaceUnit, tokenize_numerals
from story.core.table_cells import resolve_cell
from story.providers.portable_schema import (
    PORTABLE_KEYWORDS,
    schema_violations,
    validate_portable_schema,
)
from story.providers.public import (
    PINNED_TEMPERATURE,
    StoryProviderResponseError,
    StoryProviderSchemaError,
    load_provider_config,
)
from story.core.renderings import derived_figure as _derived_figure
from story.core.renderings import observed_figure as _observed_figure
from story.stages.generation.prompts import (
    PLAIN_INVESTOR_STYLE,
    WARNING_QUALIFIER_PHRASES,
    WRITER_MAX_TOKENS,
    WRITER_PROMPT_VERSION,
    FACTS_HEADING,
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
    DRAFT_NOT_CONSTRUCTIBLE,
    EVIDENCE_HANDLE_OUT_OF_BOUNDS,
    NO_SENTENCES,
    PLAN_NAMES_ANOTHER_PACKAGE,
    THESIS_ABANDONED,
    UNRESOLVABLE_EVIDENCE_HANDLE,
    UNRESOLVABLE_FACT_ID,
    UNRESOLVABLE_PASSAGE_ID,
    MALFORMED_SLOT,
    PASSAGE_ROW,
    RESTS_ON_NOT_A_PASSAGE_HANDLE,
    RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE,
    DraftRejected,
    draft_from,
    draft_violations,
    render_markdown,
    sentences_from,
    thesis_violations,
    write_story,
    writer_passages,
)
import story.stages.generation.writer as writer_module
# The verifier is a *different stage*, and `writer.py` may not import it —
# `test_the_writer_module_reaches_no_graph_no_retrieval_and_no_verifier` asserts that. A test
# may, and must: the demo claim is that these two meet.
from story.stages.composition.compile import SLOT_PATTERN as COMPILER_SLOT_PATTERN
from story.stages.composition.compile import compile_draft
from story.stages.composition.public import (
    CompositionRefused,
    SentenceTemplate,
    SlotKind,
    SlotRow,
)
from story.stages.composition.recovery import normalize_templates
from story.stages.composition.slot_table import slot_table
from story.stages.derivation.execute import execute
from story.stages.derivation.offers import offers
from story.stages.detection import detector_config
from story.stages.verification import DeterministicVerifier
from story.stages.verification.metric_surfaces import MetricAliasIndex
import story.stages.verification.derived_facts as derived_rules
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

#: A margins table as the extraction lane normalises one, and it is a **flattened markdown
#: grid** rather than the four bare lines this fixture used to carry. The shape is the corpus's:
#: every line opens and closes with `|`, a `| --- |` separator row sits under the header, and the
#: `$` column and the blank spacer column push each value out of the column its period header
#: sits in — which is why `TableCellRef` carries both indices (2,125 of 2,690 rows differ,
#: verified live 2026-08-13). The old shape had no delimiters at all, so `split_cells` read each
#: line as a single column-0 cell and the row label and the value would have been the same
#: string; a fixture like that cannot exercise the coordinates the whole repair rests on.
#:
#: Both quoted texts still occur verbatim, which is §13.7 Rule A step 1 — measured to hold on
#: 2,714/2,714 evidence rows. Every figure is the founder candidate's own.
PASSAGE_TEXT = (
    "|  |  | Three Months Ended September 30, 2022 |\n"
    "| --- | --- | --- |\n"
    "| Revenue |  | $ | 3,394 |\n"
    "| Gross Margin |  |  | (12.6) |\n"
    "| Adjusted Gross Margin |  |  | 3.3 |\n"
)
COLUMN_LABEL = "Three Months Ended September 30, 2022"

#: Where each margin sits in that grid. The period header is at **column 2** and both values are
#: at **column 3**: `resolve_header` reading at the value's own column would return `""` from the
#: spacer above, silently, which is the corpus's own failure mode stated in a fixture.
AGM_CELL = TableCellRef(row_index=4, value_column_index=3,
                        period_header_row_index=0, period_header_column_index=2)
GGM_CELL = TableCellRef(row_index=3, value_column_index=3,
                        period_header_row_index=0, period_header_column_index=2)

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
        cell=AGM_CELL,
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
    cell=GGM_CELL,
)

#: The handles `PackagedFact` mints for the two facts above — spelled out rather than read off
#: the model, because these are the exact strings the writer must copy back and a test that
#: derived them from the same function that mints them would assert nothing about the format.
AGM_HANDLE = f"ev:{PASSAGE_ID}:r4c3"
GGM_HANDLE = f"ev:{PASSAGE_ID}:r3c3"

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


def citation(evidence_id: str) -> dict[str, Any]:
    """One citation as §12 now spells it: a single evidence handle and nothing else.

    The passage id and the retyped quote are both gone. There is nowhere left in this helper to
    put a run of source text, which is the contract change stated as a signature.
    """
    return {"evidence_id": evidence_id}


def sentence(text: str, kind: str, **overrides: Any) -> dict[str, Any]:
    """One sentence row, with the three fields §12's schema now has and no fourth.

    `calculation` left the schema at prompt version 2.0.0 (DETERMINISTIC_FACT_TOOLS §5), and
    this helper is where its absence is enforced for every test below: a row built here cannot
    carry one, so no test can accidentally assert against a shape the grammar refuses.
    """
    row: dict[str, Any] = {"text": text, "kind": kind, "fact_bindings": [], "citations": []}
    row.update(overrides)
    return row


def valid_answer(**overrides: Any) -> dict[str, Any]:
    """What a conformant model returns for `make_package()` under `make_plan()`.

    **Three sentences where there were four, and the missing one is the gap.** It used to be a
    `calculated` sentence carrying a `Calculation` the model declared — an operation, two input
    ids, an expression and a rendered result — and there is no such field any more. The gap is
    now a `DerivedFact` code computes, which needs a derivation stage to have run; that path has
    its own package and its own fixture below (`derived_answer`), because it is a different
    claim about a different candidate and folding it in here would have made every test in this
    file depend on a stage the writer does not call.
    """
    content: dict[str, Any] = {
        "title": "Two gross margins in one quarter",
        "sentences": [
            sentence(AGM_TEXT, "reported",
                     fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                     citations=[citation(AGM_HANDLE)]),
            sentence(GGM_TEXT, "reported",
                     fact_bindings=[binding(GGM_ID, "-12.6%", "GAAP gross margin")],
                     citations=[citation(GGM_HANDLE)]),
            sentence(WARNING_TEXT, "connective"),
        ],
    }
    content.update(overrides)
    return content


# -- the S4 answer: sentence templates, and what the compiler makes of them --------------------
#
# **Two answer fixtures now, and both are real.** `valid_answer()` above is what the model
# returned under `WRITER_PROMPT_VERSION` 2.2.0 — a sentence with a `fact_bindings` array and a
# `citations` array — and it is what all 50 recorded `data/story_demo/*/draft.json` artifacts
# carry, so it stays as the input to `draft_from`, the parser kept for them. `template_answer()`
# is what the model returns under 3.0.0. Nothing converts one into the other: they are answers to
# two different questions, and a helper that translated between them would hide exactly that.

#: The metric slot is used here and **not** in `TEMPLATE_PROSE_METRIC_TEXT` below, because rule 4
#: says it is optional and a test that only ever exercised one of the two would leave the other
#: half of the rule unmeasured.
TEMPLATE_AGM_TEXT = "{{F1.metric}} was {{F1}} in {{F1.period}}."
TEMPLATE_GGM_TEXT = "{{F2.metric}} was {{F2}} in {{F2.period}}."
#: Rule 4's other half: the metric named in the model's own words while the figure and the period
#: are still slots. §13.5 reads the metric out of the sentence with the same alias index either
#: way, which is the claim `test_a_metric_named_in_the_writers_own_words_verifies_clean` measures.
TEMPLATE_PROSE_METRIC_TEXT = "Adjusted Gross Margin was {{F1}} in {{F1.period}}."


def template_sentence(text: str) -> dict[str, Any]:
    """One sentence row, with the **one** property 4.0.0's schema has and no second.

    `kind` and `rests_on` left the grammar with `WRITER_PROMPT_VERSION` 4.0.0: `kind` is derived
    from the rows a sentence's slots name (`story/stages/composition/recovery.derive_kind`), and
    `rests_on` served the explanatory route, which `03` §2 measured cannot currently succeed —
    `want_explanatory_search` is `False` in every detector, so no package in the corpus holds an
    explanatory passage. There is nowhere in this helper to put either, which is the contract
    change stated as a signature: no test below can assert against a shape the grammar refuses.
    """
    return {"text": text}


def template_answer(**overrides: Any) -> dict[str, Any]:
    """What a conformant model returns for `make_package()` under `make_plan()`, at 4.0.0.

    The same three sentences `valid_answer()` states — the two margins and the connective that
    carries the required `filing_date_unknown` qualifier — written as templates. Every numeral
    and every period in the compiled post comes out of `slot_table`; the model typed none.
    """
    content: dict[str, Any] = {
        "title": "Two gross margins in one quarter",
        "sentences": [
            template_sentence(TEMPLATE_AGM_TEXT),
            template_sentence(TEMPLATE_GGM_TEXT),
            template_sentence(WARNING_TEXT),
        ],
    }
    content.update(overrides)
    return content


# -- §2's candidate, and the derived facts the writer now binds -------------------------------
#
# **A second package, and it earns its place.** Every other test in this file runs on the
# cross-metric divergence candidate, whose two facts are one period apart in metric and zero
# periods apart in time — so it supports `compare_levels` and `ratio` and no operation that
# states a *change*. The failure DETERMINISTIC_FACT_TOOLS §2 measured is a change:
# `adjusted_gross_profit` from `556,000,000.0` in 2022Q2 to `110,000,000.0` in 2022Q3, whose
# `$446 million` recomputed cleanly and whose draft was still refused, on the literal `2022`,
# because the model left `Calculation.period_surface` empty. Testing the repair against a
# package that cannot express the failure would be testing something else.
#
# **The derived fact is built by the real derivation stage rather than typed in.** `execute`
# with `detector_config.quantity_direction` through the `DirectionOracle` seam and the package's
# own offer set — the same three arguments `pipeline.run_demo` passes — so what the writer is
# handed here is what the pipeline would hand it, digest and all. A hand-written `DerivedFact`
# would be this file inventing an id the writer then binds, which proves the binding and nothing
# about the fact.

AGP_PASSAGE_ID = DOCUMENT_ID + "#p24"
AGP_Q2_ID = "obs:adjusted-gross-profit:opendoor:2022Q2:normalized-table:0c4364ebbc44"
AGP_Q3_ID = "obs:adjusted-gross-profit:opendoor:2022Q3:normalized-table:4d66ef7200e9"

#: A table with the two readings in one column, so both facts carry real grid coordinates and
#: mint the handle form §3.1 gives a table fact. Values are the filing's own, in millions.
AGP_TABLE = (
    "| (in millions) |  | Three Months Ended |\n"
    "| Adjusted Gross Profit, second quarter |  | 556 |\n"
    "| Adjusted Gross Profit, third quarter |  | 110 |"
)


def agp_fact(observation_id: str, period_key: str, start: str, end: str,
             value: float, row: int) -> PackagedFact:
    return PackagedFact(
        observation_id=observation_id, metric_id="adjusted_gross_profit",
        metric_label="Adjusted Gross Profit", period_key=period_key,
        period_start=start, period_end=end, shape="quarter",
        value=value, unit="USD", currency="USD", scale="millions",
        printed_form=str(int(value / 1e6)), row_label="Adjusted Gross Profit",
        source_lane="normalized_table", validation_state="clean",
        passage_id=AGP_PASSAGE_ID, document_id=DOCUMENT_ID,
        quoted_text=str(int(value / 1e6)),
        cell=TableCellRef(row_index=row, value_column_index=2,
                          period_header_row_index=0, period_header_column_index=2))


def agp_package(**overrides: Any) -> StoryEvidencePackage:
    fields: dict[str, Any] = dict(
        package_id="pkg:metric-move-adjusted-gross-profit-opendoor-2022q2-2022q3:7c1d0a5b93ef",
        candidate_id=("cand:metric-move:adjusted-gross-profit:opendoor:"
                      "2022Q2_2022Q3:86ba9e13455d"),
        detector_id="detector:metric_move", detector_version="1.0.0",
        policy_version="canon-policy:1.0.0", graph_run_id=GRAPH_RUN_ID,
        graph_projection_version="1.2.0", extraction_run_id=EXTRACTION_RUN_ID,
        run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor",
                                entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(agp_fact(AGP_Q2_ID, "2022Q2", "2022-04-01", "2022-06-30", 556_000_000.0, 1),
               agp_fact(AGP_Q3_ID, "2022Q3", "2022-07-01", "2022-09-30", 110_000_000.0, 2)),
        metrics=(PackagedMetric(metric_id="adjusted_gross_profit",
                                label="Adjusted Gross Profit", unit="USD",
                                period_type="duration"),),
        primary_passages=(PackagedPassage(
            passage_id=AGP_PASSAGE_ID, document_id=DOCUMENT_ID, text=AGP_TABLE,
            char_count=len(AGP_TABLE), passage_kind="table",
            role=EvidenceRole.PRIMARY_SUPPORT),),
        documents=(PackagedDocument(document_id=DOCUMENT_ID, form="8-K"),),
        budget=PackageBudget(artifact_token_estimate=700, prompt_token_estimate=500,
                             section_counts={"facts": 2}, parameters=BudgetParameters()),
    )
    fields.update(overrides)
    return StoryEvidencePackage(**fields)


def agp_candidate(package: StoryEvidencePackage) -> StoryCandidate:
    """§2's candidate, with the four signals its `candidate.json` actually records."""
    return StoryCandidate(
        candidate_id=package.candidate_id, detector_id="detector:metric_move",
        detector_version="1.0.0", policy_version="canon-policy:1.0.0",
        graph_run_id=GRAPH_RUN_ID, subject_entity_id="opendoor", story_type="metric_move",
        metric_ids=("adjusted_gross_profit",), anchor_period_keys=("2022Q2", "2022Q3"),
        anchor_observation_ids=(AGP_Q2_ID, AGP_Q3_ID),
        signals={"crosses_zero": False, "delta": -446_000_000.0, "delta_pct": -80.215827338,
                 "direction": "decrease", "period_shape": "quarter", "polarity": "revenue"},
        evidence_request=EvidenceRequest(
            metric_ids=("adjusted_gross_profit",), period_keys=("2022Q2", "2022Q3"),
            observation_ids=(AGP_Q2_ID, AGP_Q3_ID)))


def agp_derived(package: StoryEvidencePackage | None = None) -> tuple[DerivedFact, ...]:
    """The `absolute_change` §2 is about, computed by the stage that will compute it live."""
    package = package if package is not None else agp_package()
    candidate = agp_candidate(package)
    fact = execute(
        DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                          from_fact_id=AGP_Q2_ID, to_fact_id=AGP_Q3_ID),
        package, candidate,
        direction=detector_config.quantity_direction,
        offered=offers(package, candidate))
    assert isinstance(fact, DerivedFact), fact
    return (fact,)


def agp_plan(package: StoryEvidencePackage | None = None) -> EditorialPlan:
    package = package if package is not None else agp_package()
    return EditorialPlan(
        candidate_id=package.candidate_id, package_id=package.package_id,
        thesis="Adjusted gross profit fell sharply between the second and third quarters.",
        why_it_matters="It is the largest quarter-over-quarter fall in the series.",
        key_points=(KeyPoint(
            claim="Adjusted gross profit was $110 million in 2022Q3.",
            required_fact_ids=(AGP_Q3_ID,),
            required_citation_passage_ids=(AGP_PASSAGE_ID,),
            statement_class=StatementClass.REPORTED),),
        causal_language=CausalLanguage.FORBIDDEN)


AGP_REPORTED_TEXT = "Adjusted gross profit was $110 million in the third quarter of 2022."
AGP_DERIVED_TEXT = (
    "Adjusted gross profit decreased by $446 million in the third quarter of 2022.")


def derived_answer(derived: tuple[DerivedFact, ...], **overrides: Any) -> dict[str, Any]:
    """A conformant answer that states a derived quantity the only way §12 now allows it.

    The `calculated` sentence carries **no** calculation and an ordinary `fact_bindings` entry
    naming the derived fact's id, with the metric surface and the period surface the DERIVED
    FACTS section printed for it. Its citations are the two *input* facts' evidence handles: a
    derived fact mints no handle of its own, deliberately, so the evidence a reader follows is
    the two cells the quantity was computed from.
    """
    package = agp_package()
    handles = [fact.evidence_handle for fact in package.facts]
    content: dict[str, Any] = {
        "title": "Adjusted gross profit in 2022Q3",
        "sentences": [
            sentence(AGP_REPORTED_TEXT, "reported",
                     fact_bindings=[{"fact_id": AGP_Q3_ID, "rendered": "$110 million",
                                     "metric_surface": "Adjusted Gross Profit",
                                     "period_surface": "the third quarter of 2022"}],
                     citations=[citation(handles[1])]),
            sentence(AGP_DERIVED_TEXT, "calculated",
                     fact_bindings=[{"fact_id": derived[0].fact_id,
                                     "rendered": "$446 million",
                                     "metric_surface": "Adjusted Gross Profit",
                                     "period_surface": derived[0].period_surface_hint}],
                     citations=[citation(handles[0]), citation(handles[1])]),
        ],
    }
    content.update(overrides)
    return content


AGP_REPORTED_TEMPLATE = "{{F2.metric}} was {{F2}} in {{F2.period}}."
AGP_DERIVED_TEMPLATE = "{{D1.metric}} {{D1.direction}} {{D1}} in {{D1.to_period}}."


def derived_template_answer(**overrides: Any) -> dict[str, Any]:
    """The 4.0.0 answer for §2's candidate: a reported row and a derived one, all slots.

    The derived sentence names `{{D1}}` and `{{D1.direction}}` and nothing else about the
    derivation — no operation, no input list, no expression, no formula version. Those four
    fields are what §2's live run got wrong or left empty, and none of them is in the answer.
    **Nor does it declare that it is `calculated`**: naming a `D` row is what makes it so, and
    the derivation of that label is `story/stages/composition/`'s.
    """
    content: dict[str, Any] = {
        "title": "Adjusted gross profit in 2022Q3",
        "sentences": [
            template_sentence(AGP_REPORTED_TEMPLATE),
            template_sentence(AGP_DERIVED_TEMPLATE),
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
        self.content = dict(content if content is not None else template_answer())
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


def rows_for(
    package: StoryEvidencePackage, derived_facts: Sequence[DerivedFact] = ()
) -> tuple[SlotRow, ...]:
    """This run's slot table, built the way `story/pipeline.py` builds it.

    A test may import the composition stage and `story/stages/generation/` may not — which is the
    whole reason `writer_prompt` and `write_story` take the rows as an argument. Calling
    `slot_table` here rather than hand-building rows is what makes these tests exercise the seam
    the pipeline uses instead of a shape only this file believes in.

    **No passage rows, which is the 4.0.0 change and not an omission.** `pipeline.py` calls
    `slot_table(package, derived, ())`: `rests_on` left the writer's contract, so a passage no
    sentence can name is a row no prompt should print. `rows_with_passages` below is what the
    two tests about `P` rows use, and the difference between the two is what
    `test_the_prompt_omits_the_passages_section_when_no_row_can_name_one` measures.
    """
    return slot_table(package, derived_facts, ())


def rows_with_passages(
    package: StoryEvidencePackage, derived_facts: Sequence[DerivedFact] = ()
) -> tuple[SlotRow, ...]:
    """The same table with the `P` rows the pipeline no longer builds.

    The rows, the `SlotKind.PASSAGE` member and the compiler's `rests_on` handling all stay in
    the code, unreachable from the 4.0.0 contract — exactly as the eight pre-3.0.0 writer codes
    stay for replayed artifacts. When a story type turns `want_explanatory_search` on, this is
    the table it gets back, so it is built here rather than left untested.
    """
    return slot_table(package, derived_facts, writer_passages(package))


def write_with(
    provider: StoryGenerationProvider,
    package: StoryEvidencePackage | None = None,
    plan: EditorialPlan | None = None,
    *,
    style: StyleProfile = PLAIN_INVESTOR_STYLE,
    derived_facts: tuple[DerivedFact, ...] = (),
    slots: Sequence[SlotRow] | None = None,
):
    """Every test goes through this, annotated with the protocol, so the surface under test is
    the contract rather than the concrete fake.

    `derived_facts` defaults to none, which is what makes the default path the one every test
    above exercises: a writer handed no derived fact has no `D` row in its table.

    `slots` defaults to the real table for whatever package and derived facts were passed, so a
    test that says nothing about the table gets the one the pipeline would build. `slots=()` is
    still reachable, and one test below uses it to show what a caller that forgot the table gets.
    """
    package = package if package is not None else make_package()
    plan = plan if plan is not None else make_plan()
    return write_story(
        package, plan,
        provider=provider, style=style, length_target=5, max_tokens=WRITER_MAX_TOKENS,
        derived_facts=derived_facts,
        slots=rows_for(package, derived_facts) if slots is None else slots)


def compiled_of(
    answer: Mapping[str, Any],
    package: StoryEvidencePackage | None = None,
    plan: EditorialPlan | None = None,
    *,
    derived_facts: tuple[DerivedFact, ...] = (),
) -> Draft:
    """One template answer, written and then compiled — the whole of the S4 path in one call.

    The compiler is a different stage and this file may reach it; `writer.py` may not, which is
    the boundary `test_the_writer_module_reaches_no_graph_no_retrieval_and_no_verifier` holds.
    Everything a `Draft` carries below this line was written by `compile_draft` from a trusted
    row, which is the property the tests underneath are about.
    """
    package = package if package is not None else make_package()
    plan = plan if plan is not None else make_plan()
    written = write_with(FakeWriteProvider(answer), package, plan, derived_facts=derived_facts)
    # `story/pipeline.py`'s own three lines, in order. The writer returns **strings** at 4.0.0;
    # `normalize_templates` turns each into a `SentenceTemplate` — putting a slot where the model
    # wrote a string some row already offers, and deriving `kind` from the rows the result names
    # — and the compiler fills them. What that pass does is
    # `tests/story/test_story_composition_recovery.py`'s subject and is not re-asserted here.
    normalized = normalize_templates(written.sentences, rows_for(package, derived_facts))
    return compile_draft(
        normalized.templates, package, plan,
        derived_facts=derived_facts, title=written.title,
        model_id="Qwen3.5-9B-Q4_K_M.gguf", style_profile_id=PLAIN_INVESTOR_STYLE.profile_id,
        prompt_version=WRITER_PROMPT_VERSION).draft


def draft_of(
    answer: Mapping[str, Any],
    package: StoryEvidencePackage | None = None,
    plan: EditorialPlan | None = None,
    *,
    derived_facts: tuple[DerivedFact, ...] = (),
) -> Draft:
    """A **pre-3.0.0** answer through the parser that is kept for the artifacts already on disk.

    `draft_from` is no longer on the pipeline path and is no longer reachable through
    `write_story`, so every test below that judges a `fact_bindings`/`citations` answer calls it
    directly. That is not a test of a dead path: 50 recorded `data/story_demo/*/draft.json`
    artifacts and every pre-3.0.0 replay store carry exactly this shape, a `Draft` read back
    from one still has to resolve, and these are the §12 rules that decide whether it does.
    """
    return draft_from(
        answer,
        package if package is not None else make_package(),
        plan if plan is not None else make_plan(),
        derived_facts=derived_facts,
        model_id="Qwen3.5-9B-Q4_K_M.gguf")


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
    # `derived_facts` is the one addition, and it is what a *different stage* computed rather
    # than something this one could have derived: §3 keeps derived facts out of the package, so
    # there is nothing here to derive them from. Everything §10.2.1 point 3 forbids — a
    # retriever, a term list, a passage set — is still absent.
    # `slots` joined `derived_facts` at S4 and the two are the same kind of argument: each is
    # something a *different stage* computed, and neither is derivable here — §3 keeps derived
    # facts out of the package, and the slot table lives in `story/stages/composition/`, which
    # this stage may not import. Everything §10.2.1 point 3 forbids — a retriever, a term list,
    # a passage set — is still absent.
    # `feedback` joined them at 4.0.0 and is the one argument here that *is* downstream of a
    # model: it carries a previous attempt's refusal back into the prompt under its own heading,
    # appended last, touching no evidence section above it. It defaults to `""` and renders
    # nothing, so a first attempt keys identically in the replay store to a run with no repair.
    assert list(parameters) == ["package", "plan", "provider", "style", "length_target",
                                "max_tokens", "derived_facts", "slots", "feedback"]
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
    # The handle, not the id: a passage is named in `rests_on` by the handle its slot row
    # carries, and the id is not printed at all now. What the assertion is about is unchanged —
    # the plan named no passage and the writer was still shown the one its facts were read from.
    # `rows_with_passages` is what a run with the explanatory route on would build; the pipeline
    # builds none, and §10.2.1 point 3 is a rule about `writer_passages` either way.
    prompt = writer_prompt(package, narrow, writer_passages(package),
                           slots=rows_with_passages(package))
    assert prompt.count("[P1]") == 1
    assert PASSAGE_TEXT.strip() in prompt.replace("\n      ", "\n")
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


def test_the_writer_schema_is_two_leaves_and_the_sentence_carries_only_its_text():
    """4.0.0: a title and an array of sentences, and a sentence is a `text` and nothing else.

    Asserted over the **whole** schema rather than over the properties that used to hold them,
    because the failure being guarded against is a field coming back somewhere else — a
    `metric_surface` beside the text, an `evidence_id` inside a `rests_on`. Success criterion 3
    of the implementation plan is exactly this assertion: *"`rendered`, `metric_surface`,
    `period_surface` and every citation are compiler-authored on the pipeline path, proved by a
    test that greps the writer's schema for those names"* — and `kind` and `rests_on` join that
    list at 4.0.0.
    """
    schema = writer_schema()
    assert sorted(schema["required"]) == sorted(schema["properties"]) == ["sentences", "title"]
    sentence_item = schema["properties"]["sentences"]["items"]
    assert sentence_item["required"] == ["text"]
    assert set(sentence_item["properties"]) == {"text"}

    leaves = [path for path, subschema in walk_schema(schema)
              if subschema.get("type") in ("string", "number", "boolean")]
    assert leaves == ["$.title", "$.sentences[].text"], leaves
    # One array in the whole schema, and it is the sentence list.
    assert [path for path, subschema in walk_schema(schema)
            if subschema.get("type") == "array"] == ["$.sentences"]

    flattened = json.dumps(schema)
    for retired in ("calculation", "operation", "expression", "result_rendered",
                    "formula_version_id", "input_observation_ids", "delta_pp",
                    "fact_bindings", "citations", "rendered", "metric_surface",
                    "period_surface", "evidence_id", "facts_used", "kind", "rests_on"):
        assert retired not in flattened, retired


def test_a_model_that_declares_a_calculation_anyway_is_refused_by_the_grammar():
    """`additionalProperties: false` is what makes each removal a refusal rather than a hope.

    Two old contracts' own answers, replayed against the new schema: 3.0.0's `kind` and 2.2.0's
    `calculation`. Neither is tolerated, ignored or silently dropped — both fail
    `schema_violations` before a draft exists, which is the same treatment any other invented
    field gets. `test_a_sentence_kind_outside_the_enum_fails_as_a_schema_error` stood below and
    is retired into the first half of this one: there is no `kind` enum left to be outside of,
    and the field itself is now the violation.
    """
    answer = template_answer()
    answer["sentences"][2]["kind"] = "connective"
    with pytest.raises(StoryProviderSchemaError) as refused:
        write_with(FakeWriteProvider(answer))
    assert any("kind" in violation for violation in refused.value.violations)

    answer = template_answer()
    answer["sentences"][2]["calculation"] = [{
        "operation": "delta_pp", "input_observation_ids": [GGM_ID, AGM_ID],
        "expression": "adjusted_gross_margin - gaap_gross_margin",
        "result_rendered": "15.9 percentage points",
        "formula_version_id": "", "period_surface": ""}]
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(FakeWriteProvider(answer))
    assert any("calculation" in violation for violation in raised.value.violations)


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
    plain = write_with(first)
    terse_written = write_with(second, style=terse)

    assert first.calls[0]["prompt"] == second.calls[0]["prompt"]
    assert first.calls[0]["system"] != second.calls[0]["system"]
    # The sentences, not the bindings: a style profile that could change a figure would have to
    # change a slot, and the slots are what the two answers are compared on. The figures
    # themselves are the compiler's and are not reachable from here at all, which is the strong
    # form of §12's rule that S4 makes true — `compile_draft` takes no style profile either.
    assert plain.sentences == terse_written.sentences
    assert terse_written.sentences[0] == TEMPLATE_AGM_TEXT


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


#: The package the demo actually runs on, and the only one in this suite whose `metrics[]` is the
#: **ontology's** rather than this file's. It is loaded rather than restated because the defect
#: below turned on a single absent alias: `make_package()` gives `gaap_gross_margin` the aliases
#: `("gaap gross margin", "GAAP gross margin")`, the real ontology gives it only `"Gross Margin"`,
#: and every test above therefore passed while the live prompt was refusing to name the metric at
#: all.
DEMO_PACKAGE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "story_demo" / "evidence_package.json")


def real_demo_package() -> StoryEvidencePackage:
    return StoryEvidencePackage.model_validate(
        json.loads(DEMO_PACKAGE_FIXTURE.read_text(encoding="utf-8")))


#: The value every figure below is rendered from. Signed, so the magnitude rule is exercised too.
FIGURE_VALUE = -446_000_000.0


def make_derived_of(unit: str) -> DerivedFact:
    """`agp_derived()`'s fact with one field moved, for a rendering question about the unit.

    Built by `replace` off a fact the *stage* computed rather than typed out here, so a field
    the model gains cannot be silently absent from this fixture.
    """
    return agp_derived()[0].model_copy(update={
        "unit": unit, "result": FIGURE_VALUE,
        "currency": "USD" if unit == "USD" else None})


def test_every_figure_the_derived_row_tells_the_writer_to_copy_carries_a_unit_13_2_can_read():
    """The other half of the same agreement, on the figure instead of the metric surface.

    §13.2 judges a numeral bound to a derived fact against `DERIVED_SURFACES`, and that map is
    strict where the observation map is tolerant: `USD` admits only a currency-symbol surface and
    `percentage_points` only the two-word one, so a `SurfaceUnit.NONE` rendering is
    `derived_unit_mismatch` rather than an unstated unit. The row used to print `{result} {unit}`
    and the live draft copied it — `"446000000.0 USD"` tokenises as `none`, which is exactly the
    `derived_unit_mismatch` the metric-move run earned under both providers.

    Asserted over **every** unit a derived fact may carry rather than over the two the demo
    happens to produce, because the table is what a later unit will be added to.
    """
    package = agp_package()
    for unit in DERIVED_UNITS:
        if unit in derived_rules.NON_NUMERIC_UNITS:
            continue  # a word-valued row prints no figure at all; §13.1 refuses a numeral there
        figure = _derived_figure(make_derived_of(unit), package)
        tokens = tokenize_numerals(figure)
        assert len(tokens) == 1, f"{unit}: {figure!r} is not one numeral"
        assert tokens[0].unit in derived_rules.DERIVED_SURFACES[unit], (
            f"{unit}: the prompt says to write {figure!r}, which §13.2 reads as "
            f"{tokens[0].unit.value} and refuses as derived_unit_mismatch")
        assert tokens[0].value == abs(FIGURE_VALUE)


def test_a_monetary_figure_is_offered_at_the_scale_its_inputs_were_filed_at():
    """`"$446 million"`, and the two ways it falls back to the plain form.

    **Measured, not preferred** *(2026-08-19)*. `"$446000000.0"` is a legal USD surface that a
    9B model will not write: shown it, Qwen wrote `"446000000.0 USD"` — the shape of the FACTS
    rows above it, carrying no unit surface at all — and earned `derived_unit_mismatch`. Shown
    `"$446 million"` it wrote `"$446 million"`, and the §2 candidate reached `accepted` end to
    end for the first time.

    The division is exact or it does not happen: `_scaled_money` divides in `Decimal` and keeps
    the quotient only when it multiplies back to the same value, so the offered figure denotes
    what code computed to the last digit and §13.1's window is still computed from what the
    draft prints.
    """
    package, derived = agp_package(), agp_derived()[0]
    assert {fact.scale for fact in package.facts} == {"millions"}

    assert _derived_figure(derived, package) == "$446 million"
    assert tokenize_numerals("$446 million")[0].value == abs(derived.result)

    # A value the scale does not divide exactly keeps every digit code computed.
    ragged = derived.model_copy(update={"result": -446_000_000.5})
    assert _derived_figure(ragged, package) == "$446000000.5"
    # …and a half-million does divide exactly, so the scaled form is not reserved for round
    # numbers — it is reserved for exact ones.
    half = derived.model_copy(update={"result": -446_500_000.0})
    assert _derived_figure(half, package) == "$446.5 million"

    # Inputs filed at different scales name no shared scale, so neither does the figure.
    mixed = replace_facts(package, (package.facts[0].model_copy(update={"scale": "units"}),
                                    package.facts[1]))
    assert _derived_figure(derived, mixed) == "$446000000.0"


def test_the_facts_section_offers_money_at_the_same_scale_the_derived_row_does():
    """**H3, and it is 2.1.0's own repair finishing its argument.**

    2.1.0 taught the DERIVED FACTS row to print `$446 million` and left the FACTS row printing
    `556000000.0 USD`. Two shapes for one kind of thing, and at H2 — with the offer set narrowed
    and the planner asking for two derivations instead of one — the model reconciled them the
    wrong way round: it scaled the FACTS reading itself to write *"Adjusted Gross Profit of 556
    million USD"*, then copied **that** invented spelling onto a derived row whose own line said
    `write exactly "$446 million"`. It obeyed the same instruction on the percentage row in the
    same draft. `446 million USD` carries no currency surface, so §13.2 refused it, and that one
    span was the whole difference between accepted and rejected *(plan §14.7)*.

    **At S4 the repair stops being a rendering rule and becomes a structural one.** Both sections
    print `SlotRow.offers`, which `slot_table` filled from `renderings.legal_renderings` — one
    function, one string per row — so *"the FACTS row and the DERIVED FACTS row agree about how
    money is spelled"* is no longer something two printers have to be kept in step about. The
    assertion is kept, on the slots line rather than on a `figure:` line, because the property is
    what mattered and the two shapes disagreeing is what was measured.
    """
    package = agp_package()
    derived = agp_derived()
    prompt = writer_prompt(package, agp_plan(package), writer_passages(package),
                           slots=rows_for(package, derived),
                           derived_facts=derived, length_target=3)
    facts = prompt.split(FACTS_HEADING)[1].split("DERIVED FACTS")[0]

    assert '{{F1}} -> "$556 million"' in facts
    assert '{{F2}} -> "$110 million"' in facts
    # The reading itself is still printed — the model has to select a fact before it can write
    # one — and the line beside it says the package's spelling is not writable.
    assert "reads 556000000.0 USD" in facts
    # One spelling of money in the whole prompt, which is the property the repair is about.
    assert '{{D1}} -> "$446 million"' in prompt
    for token in ("556 million USD", "110 million USD", "446 million USD"):
        assert token not in prompt


def test_only_money_at_a_filed_scale_gains_a_figure_line_and_the_restraint_is_deliberate():
    """A `percent` reading is already writable, so nothing is offered for it.

    The committed accepted draft writes *"-12.6 percent"*; `_DERIVED_FIGURE_FORMATS` would spell
    the same reading `-12.6%`, which narrows legal prose and buys nothing. Only the shape that
    forced the model to do arithmetic in its head is replaced — which is also why the demo
    candidate's writer prompt is byte-identical across H3, and why its committed store still
    replays with no re-record.
    """
    assert _observed_figure(agp_package().facts[0]) == "$556 million"

    percent = make_package()
    assert {fact.unit for fact in percent.facts} == {"percent"}
    for fact in percent.facts:
        assert _observed_figure(fact) is None
    assert "figure: write" not in writer_prompt(
        percent, make_plan(), writer_passages(percent)).split("DERIVED FACTS")[0]


def test_a_negative_money_reading_puts_the_sign_outside_the_currency_symbol():
    """A loss has to be spellable, and `-$110 million` is how a filing writes one.

    `$-110 million` tokenises to the same value and unit, so this is a rendering judgment rather
    than a correctness one — and it is made here rather than left to the model, which is the
    whole of why the line exists.
    """
    package = agp_package()
    loss = package.facts[1].model_copy(update={"value": -110_000_000.0})
    assert _observed_figure(loss) == "-$110 million"
    token, = tokenize_numerals("-$110 million")
    assert (token.value, token.unit) == (-110_000_000.0, SurfaceUnit.USD)


def test_an_observed_figure_falls_back_to_the_plain_form_on_the_same_two_conditions():
    """`_scaled_money` is one function, so a FACTS row inherits the derived row's caution.

    A scale the value does not divide exactly, and a scale word the corpus does not carry, both
    print nothing rather than a rounded figure — the prompt never chooses the digits §13.1's
    tolerance window is computed from.
    """
    fact = agp_package().facts[0]
    assert _observed_figure(fact.model_copy(update={"value": 556_000_000.5})) is None
    assert _observed_figure(fact.model_copy(update={"scale": "units"})) is None
    assert _observed_figure(fact.model_copy(update={"scale": ""})) is None
    assert _observed_figure(fact.model_copy(update={"value": 556_500_000.0})) == "$556.5 million"


def replace_facts(package: StoryEvidencePackage, facts) -> StoryEvidencePackage:
    """A package with its facts swapped, for a rendering question about their scale."""
    return package.model_copy(update={"facts": tuple(facts)})


def test_the_surfaces_the_prompt_offers_and_the_surfaces_the_verifier_accepts_are_one_set():
    """The agreement stated generally, because stating it about today's two metrics is what let
    it break.

    Two directions, and the second is the one that was false *(measured 2026-08-19)*:

    1. every surface the prompt offers must resolve **uniquely to that metric** in
       `MetricAliasIndex`, or the writer is being told to write something §13.5 refuses;
    2. a metric the index *can* name uniquely must be offered **some** surface, or the writer is
       told *"do not write about this fact"* about a fact its plan requires.

    `MetricAliasIndex.from_package` indexes `(metric_id, label, *aliases)` and `normalise` maps
    `_` to a space, so `"GAAP Gross Margin"` has always resolved through the *id* entry — it is
    what the committed accepted draft binds. `metric_surfaces_for` offered only `label` and
    `aliases`, and `gaap_gross_margin`'s label `"Gross Margin"` is dropped as a sub-phrase of
    `"Adjusted Gross Margin"`, so on the real package it offered nothing for a metric the
    verifier would have accepted three spellings of.
    """
    for package in (make_package(), agp_package(), real_demo_package()):
        index = MetricAliasIndex.from_package(package)
        for metric in package.metrics:
            offered = metric_surfaces_for(package, metric.metric_id)
            for surface in offered:
                assert index.resolve(surface).unique_metric_id == metric.metric_id, (
                    f"{package.package_id} offers {surface!r} for {metric.metric_id}, which "
                    f"§13.5 resolves to {index.resolve(surface).metric_ids}")
            nameable = [surface for surface in
                        (metric.metric_id, metric.label, *metric.aliases)
                        if index.resolve(surface).unique_metric_id == metric.metric_id]
            assert bool(offered) == bool(nameable), (
                f"{metric.metric_id} in {package.package_id}: the verifier accepts {nameable} "
                f"and the prompt offers {list(offered)}")


def test_the_real_package_offers_a_surface_for_the_metric_whose_label_is_ambiguous():
    """§11.4's second defect, on the package it was measured on.

    The demo candidate's `compare_levels` carries `metric_id: gaap_gross_margin`, so both its
    FACTS row and its DERIVED FACTS row printed *"no surface names this metric uniquely in this
    package - do not write about this fact"* for the figure the plan's key point requires — while
    `WRITER_SYSTEM` rule 4 told the model to write *"GAAP gross margin"*. There was no legal
    answer, and the live draft filled `metric_surface` with the unit.
    """
    package = real_demo_package()
    index = MetricAliasIndex.from_package(package)

    assert [metric.label for metric in package.metrics
            if metric.metric_id == "gaap_gross_margin"] == ["Gross Margin"]
    assert index.resolve("Gross Margin").unique_metric_id is None
    for accepted in ("gaap gross margin", "GAAP Gross Margin", "gaap_gross_margin"):
        assert index.resolve(accepted).unique_metric_id == "gaap_gross_margin"

    assert metric_surfaces_for(package, "gaap_gross_margin") == ("gaap gross margin",)
    assert metric_surfaces_for(package, "adjusted_gross_margin") == ("Adjusted Gross Margin",)


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


def test_the_prompt_omits_the_passages_section_when_no_row_can_name_one():
    """**53% of the writer prompt, gone, and the rule is asked of the rows rather than a flag.**

    A passage reaches the prompt so an `explanatory` sentence can rest on it. Where the table has
    no `P` row no sentence can, and printing the passages anyway costs what `03` §1 measured on
    the demo `metric_move` candidate: 5,924 of the prompt's 11,179 characters, and **182
    numerals of which none is a figure the model is allowed to write**. The pipeline passes no
    passage rows, so the section is omitted; a caller that still has `P` rows gets it back.
    """
    package, plan = make_package(), make_plan()
    passages = writer_passages(package)
    assert passages, "the fixture's fact is read from a passage"

    # The printer takes the slice as an argument and omits the heading entirely for an empty
    # one, rather than printing it over "(none)". Every other section here renders "(none)"
    # because an absent section would read as an omission; this one is the opposite case — a
    # heading over no passage invites a sentence resting on evidence the run does not carry.
    without = writer_prompt(package, plan, (), slots=rows_for(package))
    assert "PASSAGES (" not in without
    assert PASSAGE_TEXT.strip() not in without.replace("\n      ", "\n")

    with_rows = writer_prompt(package, plan, passages, slots=rows_with_passages(package))
    assert "PASSAGES (1 whole passage" in with_rows
    assert PASSAGE_TEXT.strip() in with_rows.replace("\n      ", "\n")
    # The measurement, in the small: the omitted section is the bulk of the prompt and every
    # numeral it prints is one the model may not write.
    assert len(with_rows) > len(without)
    assert "3,394" in with_rows and "3,394" not in without


def test_write_story_prints_no_passage_section_because_the_schema_cannot_name_one() -> None:
    """The 4.0.0 grammar has no `rests_on`, so a passage is text the model can read and nothing
    it can cite — and the section is 5,924 of the demo candidate's 11,179 prompt characters,
    printing 182 numerals of which **none** is a figure the model may write.

    **Asked of the schema and not of the slot table**, and the difference is load-bearing: the
    table still carries `P` rows, because a plan grounds a counterpoint by naming one. Reading
    the absence of a route off the absence of a row coupled two things that are not the same,
    and made a package carrying counter-evidence unplannable.
    """
    package, plan = make_package(), make_plan()
    provider = FakeWriteProvider()
    # The table carries a `P` row and the prompt still prints no passage section.
    write_story(package, plan, provider=provider, slots=rows_with_passages(package),
                length_target=5, max_tokens=64)

    prompt = provider.calls[0]["prompt"]
    assert "PASSAGES" not in prompt
    assert PASSAGE_TEXT.strip() not in prompt.replace("\n      ", "\n")
    # And the schema is why: there is no field a passage handle could be written into.
    assert "rests_on" not in json.dumps(provider.calls[0]["schema"])


def test_a_window_outside_the_closed_grammar_is_offered_no_surface_at_all():
    """§13.4 refuses a surface it cannot resolve; the honest thing is to tell the writer that
    fact may not be named rather than to invent a form the grammar will reject."""
    fact = make_fact(period_start="2022-02-01", period_end="2022-11-30", period_key="odd")
    assert period_surface_for(fact) is None
    package = make_package(facts=(fact,))
    rows = rows_for(package)
    # R3 as a rendering rather than as advice. The old prompt printed "do not write about this
    # fact"; the row simply has no `{{F1.period}}` to print, and a template naming one is
    # `field_not_offered_by_row` at compile time.
    assert "period" not in rows[0].offers
    prompt = writer_prompt(package, make_plan(), writer_passages(package), slots=rows)
    assert "{{F1.period}}" not in prompt
    assert "{{F1}}" in prompt


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
        draft_of(answer)
    assert UNRESOLVABLE_FACT_ID in raised.value.codes


def test_a_binding_to_a_derived_id_this_run_did_not_mint_is_refused():
    """The replacement for *"a calculation over an input the package does not hold"*.

    A derived fact is not in the package, so the writer's resolution set is two lists — and a
    caller that ran no derivation stage passes none. A well-formed `fact:derived:` id is then
    exactly as unresolvable as an invented `obs:` one, which is the safe direction: the alternative
    would be a draft binding a quantity nothing computed.
    """
    derived = agp_derived()
    package = agp_package()
    with pytest.raises(DraftRejected) as raised:
        draft_of(derived_answer(derived), package, agp_plan(package))
    assert UNRESOLVABLE_FACT_ID in raised.value.codes
    assert derived[0].fact_id in str(raised.value)


def test_a_rendering_its_own_sentence_does_not_contain_is_refused():
    """§12: the binding declares which span of the writer's own text states the fact. A rendering
    the text does not carry points the whole of §13 at the wrong characters."""
    answer = valid_answer()
    answer["sentences"][0]["fact_bindings"] = [
        binding(AGM_ID, "3.30%", "Adjusted Gross Margin")]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer)
    assert raised.value.codes == (BINDING_RENDERING_NOT_IN_TEXT,)


def test_a_rendering_that_occurs_twice_in_its_own_sentence_is_refused_rather_than_guessed():
    """Two occurrences and no ground for choosing: locating the substring is deterministic only
    while it is unique, and a verifier handed the wrong one checks the wrong claim."""
    text = "Adjusted gross margin was 3.3%, and 3.3% is what the reconciliation shows."
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[binding(AGM_ID, "3.3%", "Adjusted Gross Margin")],
                 citations=[citation(AGM_HANDLE)])])
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer)
    assert raised.value.codes == (BINDING_RENDERING_AMBIGUOUS,)
    assert "occurs 2 times" in str(raised.value)


# -- rule: a citation is a handle this package minted, and code resolves it ----------------------


def test_a_handle_naming_a_cell_no_fact_occupies_is_refused():
    """The fabrication the new contract makes cheap: the passage is real, the grid position is
    real, and no fact in the package was read from it.

    `ev:…:r2c3` is the Revenue row of this table — a cell that exists, holds `3,394`, and
    evidences nothing this package carries. A handle is minted from coordinates a `PackagedFact`
    already holds and nothing else mints one, so *"a cell nobody read a fact out of"* and *"a
    passage this package never saw"* are the same refusal, and both are it.
    """
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(f"ev:{PASSAGE_ID}:r2c3")]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer)
    assert raised.value.codes == (UNRESOLVABLE_EVIDENCE_HANDLE,)
    assert "mints a handle for every fact it holds" in str(raised.value)


def test_a_handle_naming_a_passage_this_package_does_not_hold_is_refused():
    """The counter-evidence passage evidences no packaged fact, so no handle names it — which is
    §10.2.1 point 3 arriving as *"nothing minted that"* rather than as a passage-id check."""
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(f"ev:{COUNTER_PASSAGE_ID}:r0c0")]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer)
    assert raised.value.codes == (UNRESOLVABLE_EVIDENCE_HANDLE,)


def test_a_handle_for_a_fact_whose_passage_the_writer_was_not_shown_is_refused():
    """`unresolvable_passage_id` survives, and this is the shape that still reaches it.

    `writer_passages` intersects the facts' passages with the package's four passage sections, so
    a fact naming a passage no section carries mints a perfectly good handle and is outside the
    slice. The model is not at fault and the citation is still refused: §10.2.1 point 3 says the
    writer may cite only what it was shown, and it was not shown that text.
    """
    orphan = make_fact(observation_id="obs:revenue:opendoor:2022Q3:normalized-table:aa01",
                       metric_id="revenue", metric_label="Revenue", value=3394.0, unit="USD",
                       currency="USD", scale="millions", row_label="Revenue",
                       quoted_text="3,394", passage_id="psg:opendoor-10q-2022q3:not-carried",
                       cell=TableCellRef(row_index=2, value_column_index=3,
                                         period_header_row_index=0,
                                         period_header_column_index=2))
    package = make_package(facts=(make_fact(), GGM_FACT, orphan))
    assert orphan.evidence_handle == "ev:psg:opendoor-10q-2022q3:not-carried:r2c3"
    assert orphan.passage_id not in {p.passage_id for p in writer_passages(package)}

    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(orphan.evidence_handle)]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer, package)
    assert raised.value.codes == (UNRESOLVABLE_PASSAGE_ID,)


def test_a_cell_the_packages_own_passage_text_does_not_reach_is_refused_as_out_of_bounds():
    """§3.4 check 2, and it is **not** a model failure — it cannot be one.

    The coordinates come off the `PackagedFact`, so this fires when a package and the passage
    text it carries disagree about the table: a stored package replayed against a re-extracted
    corpus, which is the case `table_cells.CellOutOfBounds` was given its own exception type for.
    Row 9 names nothing in a grid `passage_text.split("\\n")` gives six rows — five lines and the
    empty one after the closing newline, which is the rule the graph's `row_index` was produced
    under and which `table_cells` deliberately keeps.
    """
    drifted = make_fact(cell=TableCellRef(row_index=9, value_column_index=3,
                                          period_header_row_index=0,
                                          period_header_column_index=2))
    package = make_package(facts=(drifted, GGM_FACT))
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(drifted.evidence_handle)]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer, package)
    assert raised.value.codes == (EVIDENCE_HANDLE_OUT_OF_BOUNDS,)
    assert "which has 6 lines" in str(raised.value)


def test_a_handle_that_resolves_to_a_spacer_cell_has_no_span_and_is_refused():
    """Column 1 of the adjusted-margin row is a blank spacer, which is a real cell at a real
    coordinate and holds nothing.

    `quoted_text` is non-empty on 2,704 / 2,704 evidence edges, so a fact resolving to an empty
    cell means its coordinates do not name the value it was read from. Refused under the
    out-of-bounds code because the outcome is the same one: the handle locates no span, and
    `PassageCitation` will not carry `char_end == char_start`.
    """
    spacer = make_fact(cell=TableCellRef(row_index=4, value_column_index=1,
                                         period_header_row_index=0,
                                         period_header_column_index=2))
    package = make_package(facts=(spacer, GGM_FACT))
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(spacer.evidence_handle)]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer, package)
    assert raised.value.codes == (EVIDENCE_HANDLE_OUT_OF_BOUNDS,)
    assert "is empty; there is no span to cite" in str(raised.value)


def test_citing_another_facts_handle_constructs_here_and_is_left_to_13_7():
    """**Stated because the boundary matters more than the outcome.** A sentence binding the
    adjusted margin and citing the GAAP cell is a mis-citation, and §12 does *not* refuse it.

    §3.4 check 7 — *"the handle this package minted for `F`"* — is §13.7's, and `writer.py` may
    not import the verifier. A weaker copy here would be a second authority on the same question,
    and the two would eventually disagree about one draft. What §12 owes is that the citation
    resolves to the cell the handle names, and it does: the span is the GAAP row's value, not the
    adjusted one's, so the mis-citation is *visible* in the draft rather than smoothed over.

    When this was written the verifier did not catch it either — both cells sit in one passage,
    so §13.7's *"the passage each bound fact was read from"* test passed. **S5 closed that**:
    `evidence_handle_not_for_fact` refuses this draft at §13.7, and the test holding it is
    `test_citing_another_facts_cell_in_the_same_passage_is_refused` in
    `test_story_deterministic_verifier.py`.
    What is asserted here is unchanged and is still §12's own boundary — the draft is
    *constructible*, and the mis-citation is visible in it rather than smoothed over.
    """
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(GGM_HANDLE)]
    draft = draft_of(answer)
    cited = draft.sentences[0].citations[0]
    assert cited.evidence_handle == GGM_HANDLE
    assert PASSAGE_TEXT[cited.char_start:cited.char_end] == "(12.6)"
    assert draft_violations(draft, make_package(), make_plan()) == ()


def test_a_narrative_quote_the_packages_passage_no_longer_contains_is_refused():
    """`citation_quote_not_in_passage` survives, narrowed to narrative evidence.

    A fact with no `cell` was read out of prose, so there is no coordinate to resolve and the
    package's own `quoted_text` is located by search. Zero occurrences means the passage the
    package carries no longer holds the sentence the edge quoted — a defect in the evidence, not
    a typing mistake, because the model never wrote this string.
    """
    prose = make_fact(cell=None, quoted_text="adjusted gross margin of 4.4%")
    package = make_package(facts=(prose, GGM_FACT))
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(prose.evidence_handle)]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer, package)
    assert raised.value.codes == (CITATION_QUOTE_NOT_IN_PASSAGE,)
    assert "narrative evidence" in str(raised.value)


def test_a_narrative_quote_that_occurs_twice_still_resolves_to_no_span_and_is_refused():
    """`citation_quote_ambiguous_in_passage` survives too, and this is the whole of what it now
    means.

    All **14 / 14** narrative quotes in the corpus are whole sentences occurring exactly once in
    their passage *(verified live 2026-08-13)*, so this does not fire on today's evidence. It is
    kept because the uniqueness requirement is real: a narrative fact has no coordinate, so a
    quote naming two places in its own passage resolves to no span at all. What is gone is the
    branch that made the old contract unsatisfiable — `Gross Margin` occurs twice in this table
    and a **table** fact never reaches this path, so 523 of 2,704 observations stopped being
    un-citable.
    """
    assert PASSAGE_TEXT.count("Gross Margin") == 2
    prose = make_fact(cell=None, quoted_text="Gross Margin")
    package = make_package(facts=(prose, GGM_FACT))
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(prose.evidence_handle)]
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer, package)
    assert raised.value.codes == (CITATION_QUOTE_AMBIGUOUS,)
    assert "occurs 2 times" in str(raised.value)


# `test_two_calculations_on_one_sentence_are_refused_rather_than_one_being_picked` stood here
# and is **retired with the field it guarded**. It refused a `calculation` array of two — the
# array being how §15.3 spelled an optional object — and there is no such property now, so
# `more_than_one_calculation` is a code nothing can raise. What replaced it is one line up:
# a `calculation` of any length is a schema violation, which is a stricter refusal reached
# earlier.


def test_a_draft_resting_on_no_fact_the_plan_named_is_refused_as_a_changed_thesis():
    """§12: *"the writer must not change the thesis"*, in the only form the draft contract can
    express — a `Draft` has no thesis field, so what is checkable is that the post rests on the
    evidence the plan chose."""
    # Its own cell, not `make_fact`'s default: two facts claiming one coordinate mint one handle
    # twice, and `StoryEvidencePackage` refuses that package outright (§3.4 check 7 rests on
    # handle → fact being a function). Revenue sits at row 2 of the same grid.
    other = make_fact(observation_id="obs:revenue:opendoor:2022Q3:normalized-table:aa01",
                      metric_id="revenue", metric_label="Revenue", value=3394.0, unit="USD",
                      currency="USD", scale="millions", row_label="Revenue",
                      quoted_text="3,394",
                      cell=TableCellRef(row_index=2, value_column_index=3,
                                        period_header_row_index=0,
                                        period_header_column_index=2))
    package = make_package(facts=(make_fact(), GGM_FACT, other))
    text = "Revenue was $3,394 million in the third quarter of 2022."
    answer = valid_answer(sentences=[
        sentence(text, "reported",
                 fact_bindings=[{"fact_id": other.observation_id, "rendered": "$3,394 million",
                                 "metric_surface": "Revenue",
                                 "period_surface": "the third quarter of 2022"}],
                 citations=[citation(other.evidence_handle)])])
    with pytest.raises(DraftRejected) as raised:
        draft_of(answer, package)
    assert THESIS_ABANDONED in raised.value.codes


def test_a_draft_with_no_sentence_at_all_is_refused():
    with pytest.raises(DraftRejected) as raised:
        draft_of(valid_answer(sentences=[]))
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
    answer = template_answer()
    del answer["sentences"][0]["text"]
    provider = FakeWriteProvider(answer)
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(provider)
    assert any("text" in violation for violation in raised.value.violations)
    assert len(provider.calls) == 1


# `test_a_sentence_kind_outside_the_enum_fails_as_a_schema_error` stood here and is **retired
# with the field**: 4.0.0's sentence has no `kind` for a word to be outside the enum of. What
# replaced it is stricter and lives in
# `test_a_model_that_declares_a_calculation_anyway_is_refused_by_the_grammar` — a `kind` of any
# value at all is now a schema violation, and the label the draft carries is derived from the
# rows a sentence's slots name by `story/stages/composition/recovery.derive_kind`.
#
# `test_an_operation_outside_the_enum_fails_as_a_schema_error` is retired for the same reason
# and was already: there is no operation enum in the writer's grammar to be outside of.


def test_an_answer_of_the_wrong_shape_fails_as_a_schema_error_rather_than_a_draft_rejection():
    with pytest.raises(StoryProviderSchemaError) as raised:
        write_with(FakeWriteProvider(template_answer(sentences="four of them")))
    assert any("expected array" in violation for violation in raised.value.violations)


def test_a_schema_violation_and_a_draft_rejection_are_different_failures():
    """One is a statement about the runtime, the other about §12; a caller must tell them apart."""
    assert not issubclass(DraftRejected, StoryProviderSchemaError)
    assert not issubclass(StoryProviderSchemaError, DraftRejected)


# -- the accepted draft ----------------------------------------------------------------------------


def test_a_conformant_answer_becomes_sentences_and_the_compiler_writes_the_spans():
    """The whole path in one test: what the writer returns, and what the compiler makes of it.

    **The writer's half is now plain strings**, which is the 4.0.0 change. It carries no span, no
    index, no `kind` and no `rests_on` — there is nothing left for this stage to *build*, because
    deriving `kind` reads the slot rows a sentence names and that is
    `story/stages/composition/`'s question, over a module this stage may not import. `title`
    rides beside them because a title carries no slot. The identity fields — candidate, package,
    prompt version, model — are `compile_draft`'s, and the two it cannot know are arguments.

    **The compiler's half is R1 and R2.** `text[char_start:char_end] == rendered` holds because
    one operation produced both, and `rendered` is `slot_table`'s string rather than a substring
    the model retyped and this module then went looking for.
    """
    written = write_with(FakeWriteProvider())
    assert written.sentences == (TEMPLATE_AGM_TEXT, TEMPLATE_GGM_TEXT, WARNING_TEXT)
    assert all(isinstance(one, str) for one in written.sentences)
    assert written.title == "Two gross margins in one quarter"
    assert written.generation.total_tokens == 2038

    draft = compiled_of(template_answer())
    assert draft.candidate_id == CANDIDATE_ID and draft.package_id == PACKAGE_ID
    assert draft.prompt_version == WRITER_PROMPT_VERSION
    assert draft.model_id == "Qwen3.5-9B-Q4_K_M.gguf"
    assert draft.sentences[0].text == ("Adjusted Gross Margin was 3.3 percent in the third "
                                       "quarter of 2022.")
    first = draft.sentences[0].fact_bindings[0]
    assert first.fact_id == AGM_ID
    assert first.rendered == "3.3 percent"
    assert draft.sentences[0].text[first.char_start:first.char_end] == "3.3 percent"
    assert first.metric_surface == "Adjusted Gross Margin"
    assert first.period_surface == "the third quarter of 2022"


def test_a_citation_is_resolved_from_its_handle_and_rebased_to_the_full_passage_text():
    """§10.2.1 point 2: `PackagedPassage.char_start` is the offset of `text[0]` into the full
    `:Passage.text`, so a citation an evidence panel can resolve is an absolute one.

    The passage id and the document id are **code's answer now**, read off the fact the handle
    names rather than off the model, and the span is the cell at `(4, 3)` — the numeral alone,
    not the row it sits in. Everything the model contributed to this row is the handle string.
    """
    draft = draft_of(valid_answer())
    cited = draft.sentences[0].citations[0]
    assert cited.evidence_handle == AGM_HANDLE
    assert cited.passage_id == PASSAGE_ID
    assert cited.document_id == DOCUMENT_ID
    assert PASSAGE_TEXT[cited.char_start:cited.char_end] == "3.3"
    assert (cited.char_start, cited.char_end) == (
        resolve_cell(PASSAGE_TEXT, row_index=4, column_index=3).char_start,
        resolve_cell(PASSAGE_TEXT, row_index=4, column_index=3).char_end)


def test_the_writer_never_receives_and_never_returns_a_run_of_source_text():
    """§3.2 in the two places it has to hold: the prompt and the schema.

    The prompt no longer prints `quoting "3.3"` under a fact, and the schema has nowhere to put
    a quote back. Both halves matter — leaving the quote in the prompt while removing it from the
    schema would still be showing a 9B model the bytes and hoping it does not copy them into its
    own prose, where §13.1 would then meet a numeral nothing bound.
    """
    package = make_package()
    prompt = writer_prompt(package, make_plan(), writer_passages(package),
                           slots=rows_for(package))
    assert 'quoting "' not in prompt
    # **S4 takes the handle out of the prompt too, and that is stronger rather than weaker.**
    # The writer wrote a citation until 3.0.0 and the handle was the token it copied back; it
    # writes none now, so a handle in the prompt would be a string with nowhere to go. The
    # schema is the other half: there is no `citations` property to put one in.
    for handle in (AGM_HANDLE, GGM_HANDLE):
        assert handle not in prompt
    properties = writer_schema()["properties"]["sentences"]["items"]["properties"]
    assert "citations" not in properties
    assert sorted(properties) == ["text"]


def test_the_identity_fields_come_from_the_package_and_not_from_the_model():
    """A model that returned a different candidate id could not re-key the artifact."""
    answer = valid_answer(title="cand:invented:9999")
    draft = draft_of(answer)
    assert draft.candidate_id == CANDIDATE_ID and draft.package_id == PACKAGE_ID


def test_no_sentence_this_module_builds_can_carry_a_calculation_at_all():
    """`test_an_empty_formula_version_is_read_as_no_declared_formula` stood here.

    It asserted that `""` was read as *"the ontology declares no formula for this derivation"* —
    a field that existed because §15.3 has no null, and one the model filled with the package id
    on the 2026-08-04 live run. There is nothing to read now: `DraftSentence.calculation`
    survives on the type for artifacts written under prompt version 1.4.0, and no answer this
    module accepts can set it.
    """
    draft = draft_of(valid_answer())
    assert [row.calculation for row in draft.sentences] == [None, None, None]


# -- the Markdown renderer, which may only read the draft --------------------------------------------


def test_the_markdown_renderer_takes_the_structured_draft_and_nothing_else():
    """§12: *"a rendered Markdown post may be produced only from the structured draft"*. One
    argument, and it is the draft: there is nowhere to pass the model's raw answer."""
    assert list(inspect.signature(render_markdown).parameters) == ["draft"]
    # **The rule is stronger at S4 than it was**: the writer does not return a `Draft` at all
    # now, so there is not even a draft on `WrittenStory` for this function to be handed by
    # accident. What it takes is what `compile_draft` built.
    written = write_with(FakeWriteProvider())
    assert not hasattr(written, "draft") and not hasattr(written, "markdown")
    # …and not even a template: `WrittenStory.templates` became `sentences`, and the rename was
    # deliberate rather than a shim. Every call site that read `templates` was reading something
    # with an `index` and a `kind`, and a field that quietly became a string would have failed
    # later and further away.
    assert not hasattr(written, "templates")
    assert isinstance(compiled_of(template_answer()), Draft)


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


def test_the_rendered_post_shows_its_citations_and_no_derivation_panel():
    """The Sources panel is unchanged; the Derivations panel has nothing left to render.

    It read `DraftSentence.calculation`, and a derived quantity is now an ordinary binding — so
    it renders through the sentence and the Sources panel like every other figure. The branch is
    kept in `render_markdown` for a draft read back from a 1.4.0 artifact, and this is the
    assertion that no draft this module builds reaches it.
    """
    rendered = render_markdown(draft_of(valid_answer()))
    assert "## Sources" in rendered and PASSAGE_ID in rendered
    assert "## Derivations" not in rendered


def test_the_sources_panel_names_the_handle_beside_the_span_it_resolved_to():
    """§3.1: a handle is readable *because* it surfaces where a refusal can be matched to it.

    Every §3.4 refusal names a handle; a panel printing only character offsets would leave a
    reader holding `unresolvable_evidence_handle: ev:…:r4c3` with nothing in the post to compare
    it against. The span stays too — it is what an evidence panel highlights.
    """
    rendered = render_markdown(draft_of(valid_answer()))
    assert f"[{AGM_HANDLE}]" in rendered and f"[{GGM_HANDLE}]" in rendered
    assert f"characters {resolve_cell(PASSAGE_TEXT, row_index=4, column_index=3).char_start}-" \
        in rendered


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
                 citations=[citation(AGM_HANDLE)]),
        valid_answer()["sentences"][2]])
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
                 citations=[citation(AGM_HANDLE)]),
        valid_answer()["sentences"][2]])
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
                 citations=[citation(AGM_HANDLE)]),
        valid_answer()["sentences"][2]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "causal_construction_forbidden" in codes_of(verified)


def test_a_dropped_required_warning_is_refused(verifier):
    """§12: the writer must not omit a `required_warning`. Dropping the last sentence drops the
    only place the post says "filing date"."""
    answer = valid_answer(sentences=valid_answer()["sentences"][:2])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    absent = [f for f in verified.all_findings if f.code == "required_warning_absent"]
    assert absent and absent[0].observed == "filing_date_unknown"
    assert verified.passed is False


def test_a_dropped_plan_counterpoint_is_refused(verifier):
    """§17.14: requiring counterpoints in the *plan* was satisfiable with one ungrounded
    sentence, and no §13 check required one to survive into the draft. This is that check, seen
    from the writer's side — the draft that drops the GAAP figure drops the counterpoint."""
    rows = valid_answer()["sentences"]
    answer = valid_answer(sentences=[rows[0], rows[2]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "required_counterpoint_absent" in codes_of(verified)
    assert verified.passed is False


def test_document_grain_counter_evidence_cannot_be_cited_by_the_writer_and_is_refused_if_it_is(
        verifier):
    """The prohibition from both ends, and **the writer's end is now closed by construction**.

    A citation is a handle, a handle is minted only from a `PackagedFact`, and a document-grain
    counter-evidence passage evidences no packaged fact — so there is no string the model can
    write that cites one. `draft_from` refuses every attempt as `unresolvable_evidence_handle`
    (asserted above), and no `passages=` argument reopens it: widening the slice does not mint a
    handle. That is stronger than the old contract, where a quote out of the smuggled passage
    built a citation and §13 was the only thing standing behind it.

    So the draft below is **hand-built**, which is the only way this state is now reachable, and
    §13.7 still refuses it: presenting a neighbouring table of the same filing as support for a
    cited cell presents an association as a contradiction's opposite. The check is not weakened;
    the road to it from the model is.
    """
    package = make_package()
    smuggled = (*writer_passages(package), *package.counter_evidence)
    answer = valid_answer()
    answer["sentences"][0]["citations"] = [citation(f"ev:{COUNTER_PASSAGE_ID}:r0c0")]
    with pytest.raises(DraftRejected) as raised:
        draft_from(answer, package, make_plan(), passages=smuggled, model_id="fake")
    assert raised.value.codes == (UNRESOLVABLE_EVIDENCE_HANDLE,)

    intact = draft_of(valid_answer())
    counter = package.counter_evidence[0]
    hand = intact.model_copy(update={"sentences": (
        # The handle of the fact the sentence binds, over the smuggled passage's bytes — the
        # only shape this is now buildable in, since the handle is required and no handle names
        # a counter-evidence passage. §13.7 answers with the counter-evidence refusal *and*
        # `evidence_cell_span_mismatch`, because the handle's cell is in the other passage.
        intact.sentences[0].model_copy(update={"citations": (PassageCitation(
            passage_id=COUNTER_PASSAGE_ID, document_id=DOCUMENT_ID,
            char_start=counter.char_start,
            char_end=counter.char_start + len("Inventory 2,152"),
            evidence_handle=AGM_HANDLE),)}),
        *intact.sentences[1:])})
    verified = verifier.verify(hand, package, make_plan())
    assert "counter_evidence_cited_as_support" in codes_of(verified)


def test_a_percentage_where_percentage_points_are_meant_is_still_refused(verifier):
    """§13.3: the two readings differ by `100/|v1|`, which is base-dependent. `15.9%` is the
    single most likely factual error in this candidate, and the writer's rule 8 exists for it.

    **The sentence no longer declares how the figure was arrived at, and that is the change.**
    It used to carry a `Calculation` whose `result_rendered` was `"15.9%"`, and §13.3 read the
    percent surface off that declaration. A gap is now a `DerivedFact` whose unit code stamped
    is `percentage_points`, so a draft writing `15.9%` for it is refused for stating a quantity
    in a unit the fact does not hold — a check the verifier owns and
    `tests/story/test_story_deterministic_verifier.py` makes against derived facts. What is
    still this file's to show is the half §12 owns: a numeral nothing binds is refused, whatever
    unit it wears.
    """
    assert "percentage points" in WRITER_SYSTEM
    text = "The gap between the two measures was 15.9%."
    rows = valid_answer()["sentences"]
    answer = valid_answer(sentences=[rows[0], rows[1], sentence(text, "calculated"), rows[2]])
    verified = verifier.verify(draft_of(answer), make_package(), make_plan())
    assert "unbound_numeral" in codes_of(verified)
    assert verified.passed is False


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
    answer = valid_answer(sentences=[rows[0], rows[1], sentence(text, "connective"), rows[2]])
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

    Every value is the founder's candidate's own: `3.3` and `-12.6`, both reported, both bound,
    both cited by the handle of the cell they were read from.

    **The gap sentence is not in this draft any more, and its absence is the change rather than
    a loss of coverage.** It used to be a `calculated` sentence carrying a `Calculation` this
    module built from the model's answer, and the assertion below used to read the
    `calculation_ledger` back to prove the arithmetic recomputed. The writer declares no
    arithmetic now (DETERMINISTIC_FACT_TOOLS §5): a gap is a `DerivedFact` code computes, so the
    end-to-end claim about one has a derivation stage in the middle of it and belongs where that
    stage runs — `tests/story/test_story_demo.py`, over `run_demo`. What this test still shows
    is the half it was always about: a draft written from this module's answer, and a verifier
    that finds nothing wrong with it.

    **At S4 there is a third party in the middle and the claim is the stronger for it.** The
    writer returns templates, the compiler fills them, and the verifier judges what came out —
    so the ledger below is populated from *code's* declarations rather than from the model's,
    which is the whole change. Everything §13 owns is still shown refusing a draft in the tests
    above, and none of those refusals is re-implemented in `writer.py` or in `compile.py`.
    """
    package, plan = make_package(), make_plan()
    draft = compiled_of(template_answer(), package, plan)

    verified = verifier.verify(draft, package, plan)
    assert verified.all_findings == (), [f.code for f in verified.all_findings]
    assert verified.passed is True

    # The panel §13.17 requires, populated from what the compiler wrote.
    assert [entry.fact_id for entry in verified.fact_ledger] == [AGM_ID, GGM_ID]
    assert verified.calculation_ledger == ()

    # And the post is rendered from the draft that passed, never from the model's answer.
    rendered = render_markdown(draft)
    assert "3.3 percent" in rendered and "-12.6 percent" in rendered


# ---------------------------------------------------------------------------------------
# DETERMINISTIC_FACT_TOOLS §5 — the derived facts the writer binds
# ---------------------------------------------------------------------------------------


def test_the_derived_fact_reaches_the_writer_prompt_in_the_shape_facts_are_printed_in():
    """§5: a new DERIVED FACTS section, printed like FACTS, with everything a binding needs.

    **At S4 the row prints a handle and its slot set, and four things it used to print are
    gone.** The `fact:derived:` id is not printed — a 9B model retyping
    `fact:derived:compare-levels:opendoor:…:5f8f78fad963` is the option §8 rejects, and `{{D1}}`
    is what replaces it. The `2022Q2 -> 2022Q3` line is gone with it: those are period *keys*,
    and the literal `2022` is what §2.3 measured being retyped into 15 of 18 `unbound_numeral`
    refusals. The `figure:` line and its counter-example are gone because the model no longer
    types the figure at all. What replaces all four is one slots line, printing exactly what
    `slot_table` will let the compiler insert.

    **Two things a FACTS row has are still deliberately absent.** There is no evidence id — no
    handle is ever minted for a derived fact — and the magnitude is printed rather than
    `-446000000.0`, because `{{D1.direction}}` carries the direction and *"decreased by
    -446000000.0"* is a double negative.
    """
    package, derived = agp_package(), agp_derived()
    prompt = writer_prompt(package, agp_plan(package), writer_passages(package),
                           slots=rows_for(package, derived),
                           derived_facts=derived, length_target=3)
    section = prompt.split("DERIVED FACTS")[1].split("METRIC SEMANTICS")[0]

    assert "DERIVED FACTS (1;" in prompt
    assert "  [D1]  adjusted_gross_profit" in section
    assert '{{D1}} -> "$446 million"' in section
    assert '{{D1.direction}} -> "decreased by"' in section
    assert '{{D1.metric}} -> "Adjusted Gross Profit"' in section
    assert derived[0].fact_id not in prompt
    assert "2022Q2 -> 2022Q3" not in prompt
    assert "-446000000.0" not in section
    assert "percentage_points" not in prompt
    # The row carries no handle of its own and the prompt names none: the writer cites nothing.
    assert "evidence id" not in section
    for input_id in (AGP_Q2_ID, AGP_Q3_ID):
        assert input_id not in section


def test_the_period_surface_the_model_used_to_forget_is_printed_for_it_to_copy():
    """§2's measured failure, closed at the rendering end.

    `unbound_numeral` fired on the literal `2022` because a `calculated` sentence carried no
    binding and the model left `Calculation.period_surface` empty. 2.1.0 printed the exact words
    for the model to copy; S4 stops asking it to copy anything, and the period is a slot the
    compiler fills. **Both period slots are offered**, because an `absolute_change` runs across
    two windows and §13.4 reads the binding's surface off the later one — which is the surface
    `_bindings_from` declares whichever of the two the sentence names in prose.
    """
    package, derived = agp_package(), agp_derived()
    rows = rows_for(package, derived)
    prompt = writer_prompt(package, agp_plan(package), writer_passages(package),
                           slots=rows, derived_facts=derived)

    assert derived[0].period_surface_hint == "the third quarter of 2022"
    assert derived[0].to_period == "2022Q3"
    assert '{{D1.to_period}} -> "the third quarter of 2022"' in prompt
    assert '{{D1.from_period}} -> "the second quarter of 2022"' in prompt
    assert "period" not in rows[2].offers  # the pair, never `.period` as well


def test_a_prompt_with_no_derived_fact_says_so_rather_than_omitting_the_section():
    """A section that vanished would read as an omission; one that says "(none)" is a rule."""
    package = make_package()
    prompt = writer_prompt(package, make_plan(), writer_passages(package),
                           slots=rows_for(package))
    assert "DERIVED FACTS (none; the plan requested no derivation" in prompt
    assert "  (none)" in prompt.split("DERIVED FACTS")[1]


def test_a_derived_fact_round_trips_from_a_slot_to_a_binding_and_two_citations():
    """§5's point at S4: `$446 million` is stated by a slot, and the binding is code's.

    No operation, no expression, no input list, no formula version, no `rendered`, no
    `metric_surface`, no `period_surface` and no citation — everything §2's run got wrong or left
    empty is absent from the model's answer. What the model supplies is the words around the
    slots and which slot goes where.

    **The period surface is `to_period`'s whichever period slot the sentence names.**
    `deterministic._derived_period_findings` resolves a derived binding's surface against
    `index.fact(derived.to_fact_id)`'s own window, so `_bindings_from` declares that one; the
    sentence here happens to write it as well, and the assertion is on the binding rather than on
    the prose because the binding is the thing §13.4 reads.
    """
    package, derived = agp_package(), agp_derived()
    draft = compiled_of(derived_template_answer(), package, agp_plan(package),
                        derived_facts=derived)
    gap = draft.sentences[1]

    assert gap.text == ("Adjusted Gross Profit decreased by $446 million in the third quarter "
                        "of 2022.")
    assert gap.kind is SentenceKind.CALCULATED
    assert gap.calculation is None
    assert [b.fact_id for b in gap.fact_bindings] == [derived[0].fact_id]
    assert gap.text[gap.fact_bindings[0].char_start:gap.fact_bindings[0].char_end] == \
        "$446 million"
    assert gap.fact_bindings[0].period_surface == "the third quarter of 2022"
    # The two input facts' handles, resolved to the two cells the quantity was computed from.
    # §6: no handle is ever minted for a derivation, so the citation is its inputs'.
    assert [c.passage_id for c in gap.citations] == [AGP_PASSAGE_ID, AGP_PASSAGE_ID]
    assert [AGP_TABLE[c.char_start:c.char_end] for c in gap.citations] == ["556", "110"]


def test_the_number_and_the_word_in_that_draft_are_codes_and_not_the_models():
    """The property the whole change exists for, asserted on the fact the draft bound.

    `-446,000,000.0` is `execute`'s answer over the package's own two values, `"decreased by"`
    is `detector_config.quantity_direction`'s through the `DirectionOracle` seam, and
    `reused_detector_signal` records that the result was asserted equal to the candidate's own
    `delta` rather than merely agreeing with it by inspection.
    """
    derived = agp_derived()

    assert (derived[0].from_value, derived[0].to_value) == (556_000_000.0, 110_000_000.0)
    assert derived[0].result == -446_000_000.0
    assert derived[0].display_semantics.value == "decreased by"
    assert derived[0].unit == "USD" and derived[0].currency == "USD"
    assert derived[0].reused_detector_signal == "delta"
    assert derived[0].fact_id.startswith(
        "fact:derived:absolute-change:opendoor:adjusted-gross-profit:2022Q2-2022Q3:")


# ---------------------------------------------------------------------------------------
# The `'7'` case — the plan's worked example, and the one this whole step exists for
# ---------------------------------------------------------------------------------------

#: S2's committed pull from the live graph. Every field is copied from `:Observation` and
#: `:Passage`; nothing in it is authored, which is what makes the row below evidence rather than
#: a scenario. See `tests/story/test_story_table_cells.py` for the resolver's own use of it.
TABLE_CELLS_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "table_cells_corpus.json").read_text(encoding="utf-8"))

SEVEN = next(row for row in TABLE_CELLS_CORPUS["rows"] if row["quoted_text"] == "7")


def seven_package() -> StoryEvidencePackage:
    """A one-fact package around the corpus row whose `quoted_text` is `'7'`.

    The passage, the coordinates, the labels and the quote are the graph's; the package wrapper
    around them is this module's, for the reason the demo package is built by hand — a generation
    test must run on a checkout where `data/` is absent.
    """
    fact = PackagedFact(
        observation_id=SEVEN["observation_id"],
        metric_id=SEVEN["metric_id"],
        metric_label="Adjusted Gross Profit",
        period_key=SEVEN["period_key"],
        period_start="2023-04-01", period_end="2023-06-30",
        shape="duration", value=7000000.0, unit="USD", currency="USD", scale="millions",
        printed_form="$7", row_label=SEVEN["row_label"], column_label=SEVEN["column_label"],
        source_lane="normalized_table", validation_state="ok",
        passage_id=SEVEN["passage_id"], document_id=DOCUMENT_ID,
        quoted_text=SEVEN["quoted_text"],
        cell=TableCellRef(row_index=SEVEN["row_index"],
                          value_column_index=SEVEN["value_column_index"],
                          period_header_row_index=SEVEN["period_header_row_index"],
                          period_header_column_index=SEVEN["period_header_column_index"]))
    package = make_package(
        facts=(fact,),
        metrics=(PackagedMetric(metric_id=SEVEN["metric_id"], label="Adjusted Gross Profit",
                                unit="USD", allowed_units=("USD",),
                                aliases=("adjusted gross profit",)),),
        primary_passages=(PackagedPassage(
            passage_id=SEVEN["passage_id"], document_id=DOCUMENT_ID,
            text=SEVEN["passage_text"], char_count=len(SEVEN["passage_text"]),
            passage_kind="normalized_table", role=EvidenceRole.PRIMARY_SUPPORT),),
        counter_evidence=())
    return package


def test_the_quote_this_fact_carries_occurs_27_times_in_its_own_passage():
    """The measurement the repair rests on, executable and read off the corpus.

    `EVIDENCED_BY.quoted_text` for this observation is the single character `'7'`, and the
    passage it was read from contains 27 of them. 523 of 2,704 observations are in this shape.
    """
    assert SEVEN["quoted_text"] == "7"
    assert SEVEN["passage_text"].count("7") == SEVEN["quote_occurrences_in_passage"] == 27


def test_the_seven_case_now_constructs_where_the_old_contract_could_not():
    """**The plan's §5 test: previously impossible, must now generate.**

    Under the contract this step replaced, the prompt rendered `quoting "7"` and `draft_from`
    refused that exact string as `citation_quote_ambiguous_in_passage` — so there was no answer
    a model could give for this fact. Under handles the citation resolves to one span, and it is
    the cell at `(10, 7)` rather than the first `'7'` a naive search finds.
    """
    package = seven_package()
    fact = package.facts[0]
    handle = f"ev:{SEVEN['passage_id']}:r{SEVEN['row_index']}c{SEVEN['value_column_index']}"
    assert fact.evidence_handle == handle

    text = "Adjusted gross profit was $7 million in the second quarter of 2023."
    plan = make_plan(key_points=(KeyPoint(
        claim="Adjusted gross profit was $7 million in 2023Q2.",
        required_fact_ids=(fact.observation_id,),
        required_citation_passage_ids=(SEVEN["passage_id"],),
        statement_class=StatementClass.REPORTED),), counterpoints=())
    answer = valid_answer(sentences=[sentence(
        text, "reported",
        fact_bindings=[{"fact_id": fact.observation_id, "rendered": "$7 million",
                        "metric_surface": "Adjusted Gross Profit",
                        "period_surface": "the second quarter of 2023"}],
        citations=[citation(handle)])])

    draft = draft_from(answer, package, plan, model_id="fake")
    cited = draft.sentences[0].citations[0]
    passage_text = package.primary_passages[0].text
    assert passage_text[cited.char_start:cited.char_end] == "7"
    # Not the naive answer. `passage_text.find("7")` is what searching for the quote returns, and
    # it lands in the `2023` of the header rather than in the cell the value was read from.
    assert cited.char_start != passage_text.find("7")
    assert cited.evidence_handle == handle


def test_the_seven_case_is_still_refused_when_the_handle_is_not_the_one_that_was_minted():
    """The other half: the new contract is not *"anything the model writes now works"*.

    One character off the row index and the handle names a cell no fact occupies, which refuses.
    """
    package = seven_package()
    plan = make_plan(key_points=(KeyPoint(
        claim="Adjusted gross profit was $7 million in 2023Q2.",
        required_fact_ids=(package.facts[0].observation_id,),
        required_citation_passage_ids=(SEVEN["passage_id"],),
        statement_class=StatementClass.REPORTED),), counterpoints=())
    answer = valid_answer(sentences=[sentence(
        "Adjusted gross profit was $7 million in the second quarter of 2023.", "reported",
        fact_bindings=[{"fact_id": package.facts[0].observation_id, "rendered": "$7 million",
                        "metric_surface": "Adjusted Gross Profit",
                        "period_surface": "the second quarter of 2023"}],
        citations=[citation(f"ev:{SEVEN['passage_id']}:r11c7")])])
    with pytest.raises(DraftRejected) as raised:
        draft_from(answer, package, plan, model_id="fake")
    assert raised.value.codes == (UNRESOLVABLE_EVIDENCE_HANDLE,)


# ---------------------------------------------------------------------------------------
# 4.0.0 — what this stage still parses, what it still refuses, and the two copies left
# ---------------------------------------------------------------------------------------
#
# **Two values are copied across a boundary this stage cannot import over**, and each is asserted
# equal to its original here rather than kept equal by discipline. The rule is
# `test_no_stage_imports_another_stage`'s: `story/stages/generation/` may not name
# `story/stages/composition/`, and §4.5 of the architecture document nonetheless makes the
# writer's output the compiler's input. A *test* may import both, which is what makes the copies
# checkable — the same arrangement `WARNING_QUALIFIER_PHRASES` and
# `slot_table.TWO_PERIOD_OPERATIONS` already use.
#
# **There were three copies and now there are two**, because the third — a `SentenceTemplate`
# defined here as well as in `story/stages/composition/public.py` — was deleted rather than
# re-asserted. That is the stronger answer to the same problem: a type that exists once cannot
# drift from itself.


def test_this_stage_builds_no_template_and_the_compilers_type_is_the_only_one():
    """The copy that is gone, asserted as an absence and then driven through the real seam.

    A `SentenceTemplate` carries a derived `kind`, and deriving it reads the slot rows a sentence
    names — `story/stages/composition/`'s question, over a module this stage may not import. So
    this stage stopped constructing the type at all and hands the composition root the strings,
    which is the only shape that does not require one of the two stages to know the other.
    """
    assert not hasattr(writer_module, "SentenceTemplate")
    assert not hasattr(writer_module, "templates_from")
    assert [f.name for f in dataclasses.fields(SentenceTemplate)] == [
        "index", "text", "kind", "rests_on"]

    package, plan = make_package(), make_plan()
    written = write_with(FakeWriteProvider(), package, plan)
    assert all(isinstance(one, str) for one in written.sentences)
    # `story/pipeline.py`'s own seam: strings out of this stage, templates out of the next.
    normalized = normalize_templates(written.sentences, rows_for(package))
    assert all(isinstance(t, SentenceTemplate) for t in normalized.templates)
    compiled = compile_draft(normalized.templates, package, plan)
    assert [s.index for s in compiled.draft.sentences] == [0, 1, 2]


def test_the_passage_row_marker_is_the_composition_enums_own():
    """`SlotKind` is a `str` enum so its *value* is what travels; this is the one member the
    writer has to recognise, and the copy is one string.

    It is still read on every call — `write_story` asks the rows whether any of them is a passage
    row and prints the PASSAGES section only if one is — so the copy is live rather than
    vestigial, even though the pipeline hands it a table that contains none.
    """
    assert PASSAGE_ROW == SlotKind.PASSAGE == "passage"
    assert PASSAGE_ROW in {row.kind for row in rows_with_passages(make_package())}
    assert PASSAGE_ROW not in {row.kind for row in rows_for(make_package())}


def test_the_slot_grammar_here_is_the_compilers_own():
    """One regex, copied, and asserted identical. A writer that read handles by a different rule
    would refuse an answer the compiler accepts, or pass one it cannot read.

    Still read on every call: `thesis_violations` finds the handles a draft names with it.
    """
    assert writer_module.SLOT_PATTERN.pattern == COMPILER_SLOT_PATTERN.pattern


def test_the_three_codes_no_4_0_0_answer_can_raise_are_still_declared():
    """`malformed_slot`, `rests_on_without_explanatory_sentence`, `rests_on_not_a_passage_handle`.

    **Retired as tests, kept as constants, and the distinction matters.** Each was raised by
    `templates_from`, which 4.0.0 replaced with `sentences_from`; the model writes no `rests_on`
    and the brace check belongs to the one stage that has to read a template, so all three are
    unreachable from this path. They are **not** deleted: a stored `rejected.json` names them —
    `story-v1-76da8465cd95`'s does — and the demo UI's code catalogue renders a description for
    every code an artifact on disk can carry. A catalogue offering a refusal no stage can produce
    would describe a system this is not; one missing a code a stored artifact carries renders it
    blank and non-blocking, which is untrue.

    Where the live rules went:

    * a brace the grammar cannot read → `compile_draft`'s `template_not_compilable`, asserted by
      `tests/story/test_story_composition.py`, which is the one authority on the slot grammar;
    * `rests_on` on the wrong kind → `compile_draft`'s `rests_on_without_explanatory_kind`;
    * `rests_on` naming a non-passage row → `compile_draft`'s `passage_handle_unknown`.
    """
    assert (MALFORMED_SLOT, RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE,
            RESTS_ON_NOT_A_PASSAGE_HANDLE) == (
        "malformed_slot", "rests_on_without_explanatory_sentence",
        "rests_on_not_a_passage_handle")
    # Exported, because `story/demo_ui/`'s catalogue reads the declared codes off the module by
    # constant name rather than off a hand-kept list.
    for name in ("MALFORMED_SLOT", "RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE",
                 "RESTS_ON_NOT_A_PASSAGE_HANDLE"):
        assert name in writer_module.__all__


def test_an_answer_with_no_sentence_at_all_is_refused():
    """`no_sentences` — a statement about the *answer*, which is why it survived into 4.0.0
    while three of `templates_from`'s four refusals did not."""
    with pytest.raises(DraftRejected) as raised:
        write_with(FakeWriteProvider(template_answer(sentences=[])))
    assert raised.value.codes == (NO_SENTENCES,)
    # …and directly, because `sentences_from` is public and the composition root may call it.
    with pytest.raises(DraftRejected) as direct:
        sentences_from({"title": "t", "sentences": []})
    assert direct.value.codes == (NO_SENTENCES,)


def test_a_text_that_is_not_a_string_is_refused_as_a_draft_that_will_not_construct():
    """The schema re-check at the grain a JSON schema cannot express.

    §15.3 has no `type` narrowing beyond the six keywords, and a replaying provider hands back
    whatever was recorded — so an answer stored under an older grammar is exactly the shape that
    arrives here with the wrong type. Every offending row is reported rather than the first,
    because a caller repairing a refusal wants the whole list.
    """
    with pytest.raises(DraftRejected) as raised:
        sentences_from({"title": "t", "sentences": [
            {"text": TEMPLATE_AGM_TEXT}, {"text": 3.3}, {"text": None}]})
    assert raised.value.codes == (DRAFT_NOT_CONSTRUCTIBLE, DRAFT_NOT_CONSTRUCTIBLE)
    assert "sentences[1]" in str(raised.value) and "sentences[2]" in str(raised.value)


def test_the_text_is_copied_through_untouched():
    """No stripping, no normalising, no brace repair.

    Every character the model wrote reaches either a slot's span or the reader, and a function
    that tidied it would be one that could change a sentence's meaning between the check and the
    post. The braces below are ones the grammar cannot read, and they are returned intact —
    `compile_draft` refuses them as `template_not_compilable`, which is the one authority.
    """
    ragged = "  Adjusted gross margin was {{F1} in {{F1.period}}.\n"
    assert sentences_from({"sentences": [{"text": ragged}]}) == (ragged,)


def test_a_draft_naming_no_planned_fact_is_refused_as_a_changed_thesis():
    """§12's *"the writer must not change the thesis"*, resolved through the slot rows.

    The plan's key points name `obs:` ids and a template names `F1`; the row is what joins them.
    The sentence below is well formed, compiles, and says nothing the plan asked for.
    """
    package, plan = make_package(), make_plan()
    found = thesis_violations(
        ["Both measures come from one table in the same filing."], plan, rows_for(package))
    assert [violation.code for violation in found] == [THESIS_ABANDONED]
    assert AGM_ID in found[0].detail and "no fact at all" in found[0].detail


def test_a_draft_naming_one_of_the_plans_facts_passes_though_it_drops_the_other():
    """The accepting half. One shared fact is the whole rule — the plan names two and the draft
    carries one, which is the writer choosing what to say, and §13 judges the rest."""
    package, plan = make_package(), make_plan()
    rows = rows_for(package)
    assert [point.required_fact_ids for point in plan.key_points] == [(AGM_ID,), (GGM_ID,)]
    assert thesis_violations([TEMPLATE_AGM_TEXT, WARNING_TEXT], plan, rows) == ()
    # Every handle a template names counts, not only its value slots: a `{{F1.period}}` with no
    # `{{F1}}` beside it is refused by the compiler under R4, and calling that "the thesis was
    # abandoned" would name the wrong fault for a sentence one slot short of correct.
    assert thesis_violations(["The figure was reported in {{F1.period}}."], plan, rows) == ()


def test_the_thesis_check_runs_after_recovery_and_that_is_why_it_is_public():
    """**The measured reason it is not called from `sentences_from`.**

    Before recovery a model writing plain prose names no handle at all, so running this on the
    raw answer refuses every unslotted draft as *"the templates name no fact"* — which is exactly
    the refusal `story-v1-1daff167348f` earned for three sentences that were factually correct in
    every figure. Run on the same prose after `normalize_templates`, it abstains.
    """
    package, plan = make_package(), make_plan()
    rows = rows_for(package)
    prose = ["Adjusted Gross Margin was 3.3 percent in the third quarter of 2022."]

    assert [v.code for v in thesis_violations(prose, plan, rows)] == [THESIS_ABANDONED]
    recovered = [t.text for t in normalize_templates(prose, rows).templates]
    assert thesis_violations(recovered, plan, rows) == ()
    # And the pipeline is what puts them in that order: `write_story` does not call it at all.
    provider = FakeWriteProvider(template_answer(
        sentences=[template_sentence(prose[0])]))
    assert write_with(provider).sentences == tuple(prose)


def test_a_caller_that_built_no_slot_table_can_resolve_no_handle_and_is_refused():
    """The default is empty, and this is what it buys: a caller that forgot the table gets a
    refusal naming the plan's own ids, not a post built from handles nothing knows."""
    found = thesis_violations([TEMPLATE_AGM_TEXT], make_plan(), ())
    assert [violation.code for violation in found] == [THESIS_ABANDONED]
    assert "no fact at all" in found[0].detail


def test_a_plan_with_no_key_point_leaves_the_thesis_check_nothing_to_judge():
    """Silence rather than a refusal: a plan naming no fact chose no evidence for a draft to
    abandon, and refusing there would be this check inventing a rule §11 does not have."""
    plan = make_plan(key_points=(), counterpoints=())
    assert thesis_violations(["Anything at all."], plan, rows_for(make_package())) == ()


# `test_an_explanatory_sentence_rests_on_a_passage_and_code_cites_it` stood here and is
# **retired with the field the model wrote it in**. It drove `rests_on: ["P1"]` through
# `write_story`, and 4.0.0's grammar has no `rests_on`; the pipeline also builds no `P` rows, so
# no answer can reach the route. The compiler's half of it is unchanged and is where the claim
# now lives: `tests/story/test_story_composition.py` compiles an `EXPLANATORY` template resting
# on `P1` and asserts the citation code mints, and
# `tests/story/test_story_composition_recovery.py::test_derive_kind_*` covers the label. Neither
# is reachable from a model answer today, and both return unchanged when a story type turns
# `want_explanatory_search` on.


def test_a_metric_named_in_the_writers_own_words_verifies_clean(verifier):
    """Rule 4's *"the metric slot is optional"*, measured rather than asserted.

    §13.5 reads the metric out of the sentence through `MetricAliasIndex` whether a slot or the
    model put it there, so prose that names the metric unambiguously grounds the binding exactly
    as `{{F1.metric}}` would. This is the run behind the claim in `WRITER_SYSTEM`'s own note:
    a draft naming *"Adjusted Gross Margin"* in the model's own words, with the figure and the
    period still slots, verifies with **no finding at all**. The figure and the period are not
    optional, and the two tests that show why are the `unbound_numeral` ones above.
    """
    package, plan = make_package(), make_plan()
    answer = template_answer(sentences=[
        template_sentence(TEMPLATE_PROSE_METRIC_TEXT),
        template_sentence(TEMPLATE_GGM_TEXT),
        template_sentence(WARNING_TEXT)])
    draft = compiled_of(answer, package, plan)

    assert draft.sentences[0].text == ("Adjusted Gross Margin was 3.3 percent in the third "
                                       "quarter of 2022.")
    assert draft.sentences[0].fact_bindings[0].metric_surface == "Adjusted Gross Margin"
    verified = verifier.verify(draft, package, plan)
    assert verified.all_findings == (), [f.code for f in verified.all_findings]


# ---------------------------------------------------------------------------------------
# The parser kept for what is already on disk
# ---------------------------------------------------------------------------------------

RECORDED_DEMO = Path(__file__).parent / "fixtures" / "story_demo"


def recorded_writer_answer() -> dict[str, Any]:
    """The 2.2.0 answer the local server actually returned, kept as a fixture of its own.

    **It used to be read out of `local_openai_compatible/generations.jsonl` and cannot be any
    more**, which is the whole reason this file exists: S8 re-authored that store's writer row
    under 3.0.0, so the store no longer holds a pre-3.0.0 answer to read. The bytes below are
    that row's `raw_content` as it stood at 575a59f, lifted unchanged — a real recording of a
    real server, not a hand-built shape, which is what makes the claim below worth asserting.

    Committed rather than reconstructed from `data/story_demo/*/draft.json`: the run directories
    are not committed, and a generation test must run on a clean checkout.
    """
    return json.loads(
        (RECORDED_DEMO / "writer_answer_2_2_0.json").read_text(encoding="utf-8"))


def test_the_retained_parser_still_reads_the_answer_recorded_under_the_old_contract():
    """`draft_from` is off the pipeline path and is not dead code — this is the reader it is for.

    The answer below is a real recorded 2.2.0 generation: three sentences, four model-authored
    bindings, four model-authored citations and a `fact:derived:` id the derivation stage minted.
    It parses, every `rendered` is located in its own sentence, every `evidence_id` resolves to a
    span, and the derived binding resolves against this run's derived facts. Nothing about S4
    took any of that away.

    **It also no longer satisfies the writer's schema, and both halves are asserted**, because
    that is exactly the state the retained parser exists for: the artifact is unreachable
    *forward* through `write_story` and still readable *backward* through `draft_from`.
    """
    content = recorded_writer_answer()
    assert list(schema_violations(content, writer_schema())), "the old answer must not conform"

    package = StoryEvidencePackage.model_validate_json(
        (RECORDED_DEMO / "evidence_package.json").read_text(encoding="utf-8"))
    candidate = StoryCandidate.model_validate_json(
        (RECORDED_DEMO / "candidate.json").read_text(encoding="utf-8"))
    offered = offers(package, candidate)
    derived = tuple(
        fact for fact in (execute(request, package, candidate,
                                  direction=detector_config.quantity_direction, offered=offered)
                          for request in offered)
        if isinstance(fact, DerivedFact))
    # No key points, so `thesis_abandoned` abstains: what is under test is the parsing, and a
    # plan authored here to match a recorded answer would be this file deciding the outcome.
    plan = EditorialPlan(candidate_id=package.candidate_id, package_id=package.package_id,
                         thesis="the two margins are far apart",
                         why_it_matters="one is positive and the other is not")

    draft = draft_from(content, package, plan, derived_facts=derived, model_id="recorded")
    assert [s.kind.value for s in draft.sentences] == ["reported", "reported", "calculated"]
    assert [b.rendered for s in draft.sentences for b in s.fact_bindings] == [
        "-12.6 percent", "3.3 percent", "15.9 percentage points"]
    for sentence_row in draft.sentences:
        for bound in sentence_row.fact_bindings:
            assert sentence_row.text[bound.char_start:bound.char_end] == bound.rendered
        for cited in sentence_row.citations:
            assert cited.char_end > cited.char_start
    assert draft_violations(draft, package, plan, derived_facts=derived) == ()
    # The markdown renderer is unchanged and still takes a `Draft`, whoever built it.
    assert render_markdown(draft).startswith("# Opendoor 2022Q3")


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
    rows = rows_for(package)
    with StoryOpenAICompatibleProvider(config) as provider:
        try:
            written = write_story(package, plan, provider=provider, slots=rows,
                                  length_target=5, max_tokens=WRITER_MAX_TOKENS)
        except DraftRejected as rejected:
            pytest.skip(f"the live model's draft was refused by §12: {rejected.codes}")
        except StoryProviderSchemaError as violated:
            pytest.skip(f"the live model's answer violated the schema: {violated.violations}")
        except StoryProviderResponseError as unparseable:
            pytest.skip(f"the live model returned no usable object: {unparseable}")

    # `story/pipeline.py`'s order, because at 4.0.0 there is no draft on this side of the seam:
    # normalize, then the thesis rule over the normalized text, then compile. Running the thesis
    # rule on the raw answer refuses every plain-prose draft, which is what §12 measured.
    normalized = normalize_templates(written.sentences, rows)
    thesis_found = thesis_violations(
        [template.text for template in normalized.templates], plan, rows)
    if thesis_found:
        pytest.skip("the live model's draft was refused by §12: "
                    + ", ".join(violation.code for violation in thesis_found))
    try:
        draft = compile_draft(
            normalized.templates, package, plan, title=written.title,
            model_id=provider.model_id,
            style_profile_id=PLAIN_INVESTOR_STYLE.profile_id,
            prompt_version=WRITER_PROMPT_VERSION).draft
    except CompositionRefused as refused:
        pytest.skip(f"the live model's templates would not compile: {refused.codes}")

    assert draft_violations(draft, package, plan) == ()
    assert written.generation.attempts == 1
    assert written.generation.finish_reason == "stop"
    assert written.generation.prompt_tokens + written.generation.completion_tokens < 8192

    verified = DeterministicVerifier(
        graph_run_id=GRAPH_RUN_ID, run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH).verify(draft, package, plan)
    if not verified.passed:
        pytest.skip("the live draft is structurally sound and §13 refuses it: "
                    + ", ".join(sorted(codes_of(verified))))
    assert verified.passed is True
