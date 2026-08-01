"""The live gate: the real server, a real passage, and a real ontology claim at the end.

Marked `live` so `pytest -m "not live"` stays green on a machine with no model. Mocks cannot
satisfy this file by design — `test_provider.py` proves the wire contract, and this one
proves the wire contract was right.

The chain under test is the whole of STAGE_07 §6: health, one non-trivial request built from
the Q1 2025 KPI table, structured output that parses and conforms, a `LaneClaim` through
`assemble.to_claim`, `ontology.validate_claims`, and `extraction.core.validation.validate`
against the real passage catalog.

Recorded on 2026-08-01 against llama.cpp `b10217` (`ddd4ec1`) serving Qwen3.5-9B-Q4_K_M at
8,192 context, all layers offloaded:

| | |
| --- | --- |
| health | `{"status":"ok"}` |
| answer | `{"metric_id":"homes_sold","value":2946,"unit":"homes","period_end":"2025-03-31"}` |
| prompt tokens | 1,723 |
| completion tokens | 51 |
| wall clock | 1,446 ms cold, 795 ms warm (1,719 of 1,723 prompt tokens cached) |
| `ontology.validate_claims` | ok, 0 errors, 0 warnings |
| `extraction.core.validation.validate` | ok, 0 errors, 0 warnings |

2,946 is the gold value the deterministic table lane reads from the same cell, so the model
and the table reader agree — which is the only reason this gate means anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from extraction.core.assembly import to_claim
from extraction.core.models import LaneClaim, PeriodRef
from extraction.core.validation import validate
from extraction.providers import (
    LocalOpenAICompatibleGenerationProvider,
    ProviderConfig,
    schema_violations,
)
from ontology import load_ontology

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]

# The same passage the table lane's fixtures use, so both readers are scored on one cell.
KPI_Q1_2025 = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p14"

# Deliberately more than one metric, and deliberately including the two the ontology declares
# `distinct_from` each other. A schema offering only the right answer would test nothing.
CANDIDATES = ("homes_sold", "homes_purchased", "housing_inventory_homes", "market_count")


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    rows = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["passage_id"]] = row
    return rows


class CatalogPassages:
    """A `PassageSource` over the real catalog, so evidence resolution is not simulated."""

    def __init__(self, rows: dict[str, dict]):
        self._rows = rows

    def text_of(self, passage_id):
        row = self._rows.get(passage_id)
        return row["text"] if row else None

    def exists(self, passage_id):
        return passage_id in self._rows

    def document_of(self, passage_id):
        row = self._rows.get(passage_id)
        return row["document_id"] if row else None


@pytest.fixture(scope="module")
def config():
    return ProviderConfig.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def provider(config):
    instance = LocalOpenAICompatibleGenerationProvider(config)
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def schema(ontology):
    units = sorted({ontology.registry.find(c).unit for c in CANDIDATES})
    return {
        "type": "object",
        "properties": {
            "metric_id": {"type": "string", "enum": list(CANDIDATES)},
            "value": {"type": "number"},
            "unit": {"type": "string", "enum": units},
            "period_end": {"type": "string"},
        },
        "required": ["metric_id", "value", "unit", "period_end"],
        "additionalProperties": False,
    }


@pytest.fixture(scope="module")
def prompt(ontology, passages):
    """The ontology supplies the candidate list; the corpus supplies the table.

    Nothing here is a prompt strategy — that is step 10. It is the smallest prompt that makes
    the request non-trivial: 29 columns of Markdown, five period columns, and four concepts
    to choose between.
    """
    concepts = "\n".join(
        f"- {c} ({ontology.registry.find(c).label}), unit: {ontology.registry.find(c).unit}"
        for c in CANDIDATES)
    return (
        "You are reading one table from an SEC earnings release by Opendoor Technologies "
        "Inc.\nReport the number of homes the company sold in the three months ended "
        "March 31, 2025.\n\n"
        f"Choose metric_id from this list of ontology concepts:\n{concepts}\n\n"
        "unit must be the unit declared above for the metric_id you choose.\n"
        "period_end is an ISO date (YYYY-MM-DD).\n\n"
        "TABLE:\n" + passages[KPI_Q1_2025]["text"]
    )


@pytest.fixture(scope="module")
def answer(provider, prompt, schema):
    """One generation, shared. Re-asking per assertion would measure the server, not the code."""
    return provider.generate(prompt=prompt, schema=schema)


# -- (a) health ---------------------------------------------------------------------------------


def test_health_succeeds_against_the_running_server(provider):
    status = provider.health()
    assert status.ok, f"llama-server not healthy: {status}"
    assert status.status == "ok"


# -- (b)(c) one non-trivial request, parsed and conformant ---------------------------------------


def test_a_real_kpi_table_yields_schema_conformant_json(answer, schema):
    assert schema_violations(answer.content, schema) == []
    assert answer.finish_reason == "stop"
    assert answer.content["metric_id"] == "homes_sold"
    # The gold value the deterministic table lane reads from the same cell.
    assert answer.content["value"] == 2946
    assert answer.content["unit"] == "homes"
    assert answer.content["period_end"] == "2025-03-31"


def test_the_request_was_not_trivial(prompt, answer):
    """A one-line prompt would prove nothing about an 8,192-token budget."""
    assert len(prompt) > 3000
    assert answer.prompt_tokens > 1000


# -- (g) usage and timing -------------------------------------------------------------------------


def test_usage_and_timing_are_populated_and_non_zero(answer):
    assert answer.prompt_tokens > 0
    assert answer.completion_tokens > 0
    assert answer.total_tokens == answer.prompt_tokens + answer.completion_tokens
    assert answer.latency_ms > 0
    assert answer.attempts == 1
    assert answer.model_id.endswith("Qwen3.5-9B-Q4_K_M.gguf")
    assert len(answer.raw_sha256) == 64 and len(answer.content_sha256) == 64


def test_no_thinking_budget_was_spent(answer):
    """The measurement the whole `enable_thinking` rule rests on. With thinking enabled the
    same shape of request burned 900 tokens and returned nothing."""
    assert answer.metadata.get("reasoning_characters", 0) == 0
    assert answer.completion_tokens < 200


# -- (determinism) ---------------------------------------------------------------------------------


def test_two_identical_requests_agree_on_the_answer(provider, prompt, schema, answer):
    """The content digest is stable at temperature 0; the envelope digest is not, because
    llama.cpp stamps every response with a fresh `id`, `created` and `timings`. Asserting the
    envelope would be asserting the clock."""
    repeat = provider.generate(prompt=prompt, schema=schema)
    assert repeat.content_sha256 == answer.content_sha256
    assert repeat.content == answer.content
    assert repeat.raw_sha256 != answer.raw_sha256


# -- (d)(e)(f) the parsed output becomes a validated ontology claim ----------------------------------


@pytest.fixture(scope="module")
def ontology_claim(answer, passages):
    row = passages[KPI_Q1_2025]
    lane_claim = LaneClaim(
        metric_id=answer.content["metric_id"],
        value=answer.content["value"],
        unit=answer.content["unit"],
        period=PeriodRef(period_start="2025-01-01", period_end=answer.content["period_end"],
                         label_raw="March 31, 2025"),
        source_lane="normalized_narrative",
        passage_id=KPI_Q1_2025,
        document_id=row["document_id"],
        raw_text="2,946",
        extractor_metadata={
            "provider_model_id": answer.model_id,
            "prompt_tokens": answer.prompt_tokens,
            "completion_tokens": answer.completion_tokens,
            "content_sha256": answer.content_sha256,
        },
    )
    return to_claim(lane_claim, row)


def test_the_parsed_output_becomes_an_ontology_claim(ontology_claim):
    observation = ontology_claim.metric_observation
    assert observation.observation_id.startswith(
        "obs:homes-sold:opendoor:2025Q1:normalized-narrative:")
    assert observation.value == 2946
    assert observation.evidence[0].passage_id == KPI_Q1_2025


def test_the_claim_passes_ontology_validation(ontology, ontology_claim):
    result = ontology.validate_claims([ontology_claim])
    errors = list(getattr(result, "errors", ()) or ())
    assert not errors, [str(e) for e in errors]


def test_the_claim_passes_extraction_evidence_validation(ontology, ontology_claim, passages):
    result = validate([ontology_claim], ontology=ontology, passages=CatalogPassages(passages))
    assert result.ok, [(f.code, f.detail) for f in result.errors]
    assert not result.warnings, [(f.code, f.detail) for f in result.warnings]


def test_nothing_vendor_shaped_reached_the_claim(ontology_claim):
    """The provider dies at its own boundary: what survives into `extractor_metadata` is a
    model id and two integers, and the ontology never interprets any of it."""
    metadata = ontology_claim.extractor_metadata
    for value in metadata.values():
        assert isinstance(value, (str, int, float, bool, list, dict, type(None)))
    assert "httpx" not in json.dumps(metadata)
