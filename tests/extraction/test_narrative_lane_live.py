"""The narrative lane's live gate: the real server, real prose, real ontology claims.

Marked `live`, so `pytest -m "not live"` stays green on a machine with no model.
`test_narrative_lane.py` proves every refusal offline; this file proves the refusals were the
right ones to have, against output nobody wrote down in advance.

**The gate passages were chosen from the corpus by document type, passage kind and lexical
scope size — never from the benchmark case list** (STAGE_10 §10). The rule, applied to
`passages.jsonl` and reproducible from it: among narrative passages of 300–2,200 characters
containing a digit, whose lexical scope holds at least two non-deferred metric concepts and
which print at least one resolvable date, take the highest-scoring by concept count, ties
broken by passage id. Run separately over `document_type == shareholder_letter` and over 10-K
and 10-Q passages whose heading path names Management's Discussion. That rule produced 120 and
189 qualifying passages respectively; the three below are its top answers of each shape.

| gate | document type | form | shape | why it is here |
| --- | --- | --- | --- | --- |
| letter | shareholder_letter | 8-K | bulleted 3Q23 highlights, 17 concepts in scope | the lane no table reader can serve: every figure is inside a sentence, the period is a quarter shorthand, and the scale is the word "million" |
| MD&A, reporting | narrative_primary | 10-Q | "increased by X to Y" bullets | every figure is stated beside a change, so a lane that cannot tell a level from a comparison emits twice as many observations as the filing makes |
| MD&A, definitional | narrative_primary | 10-K | the non-GAAP definition of Contribution Profit | the correct answer is no claim at all; a lane that mines a number out of a definition is attaching a value to a definition |

**Which of these numbers are measurements and which are session-dependent.** Recorded against
llama.cpp serving Qwen3.5-9B-Q4_K_M at 8,192 context. The *claims* were identical on every run:
same metrics, same values, same periods, same spans. The *token counts and the abstention
counts were not* — the letter passage produced 1,850 completion tokens and 6 chosen abstentions
run standalone and 2,123 / 9 under the full `pytest -m live` session on the same code
*(measured 2026-08-01, confirmed by review 2026-08-02)*. STAGE_09 established that this runtime
is not bit-reproducible across differing request histories, and the request history is exactly
what a shared session changes. Treat the token and abstention columns as an order of magnitude,
not as a measurement; the claim columns are the measurement, and the tests below assert those.

| | letter | MD&A reporting | MD&A definitional |
| --- | --- | --- | --- |
| prompt tokens *(session-dependent)* | ~3,400 | ~3,400 | ~3,060 |
| completion tokens *(session-dependent)* | 1,850–2,123 | ~2,000 | ~271 |
| wall clock *(session-dependent)* | ~29 s | ~31 s | ~5 s |
| claims emitted *(stable)* | 6 | 1 | 0 |
| rejected by this lane *(varies with the above)* | 2 | 10 | 0 |
| abstentions the model chose *(session-dependent)* | 6–9 | 0 | 2 |
| `ontology.validate_claims` errors *(stable)* | 0 | 0 | — |
| evidence validation errors *(stable)* | 0 | 0 | — |

**"0 warnings" was never true and the validator was hiding it.** `core.validation.validate`
copied only the ontology's errors, so the letter's four `unpreferred_source_lane` warnings —
the vocabulary saying these metrics are usually read from a table — could not reach a caller
*(found by review 2026-08-02)*. They are relayed now and asserted below by kind, which is the
signal step 11 scores a second lane on.

The MD&A reporting passage is the honest number in that table. The model composes its
quotation out of the list's introductory clause and one bullet, skipping the bullets between
them; both halves are verbatim and the join is not, so six otherwise-correct figures are
refused as `QUOTED_SPAN_NOT_IN_PASSAGE`. Six prompt revisions did not move it. Recorded here
rather than accommodated: a lane that accepted a composed quotation would accept a figure
welded to a sentence it never appeared in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from extraction.core.assembly import assemble, deferred_metric_ids
from extraction.core.periods import (
    period_phrases,
    reporting_period_keys,
    resolve_period_phrase,
)
from extraction.core.validation import (
    ONTOLOGY_WARNING,
    QUOTED_TEXT_NOT_IN_PASSAGE,
    validate,
)
from extraction.providers import LocalOpenAICompatibleGenerationProvider, ProviderConfig
from extraction.stages.narrative import (
    ISSUE_CODES,
    LANE_NAME,
    OntologyGuidedNarrativeClaimLane,
    PassageContext,
)
from extraction.stages.narrative.public import MODEL_ANSWER_UNUSABLE
from extraction.stages.scoping import LexicalOntologyCandidateScope
from extraction.stages.select import AliasIndex
from ontology import load_ontology

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]

LETTER = "norm:0001801169:0001801169-23-000137:q32023formxex992sharehol.htm#p2"
MDNA_REPORTING = "norm:0001801169:0001801169-21-000120:open-20210930.htm#p142"
MDNA_DEFINITIONAL = "norm:0001801169:0001801169-21-000011:open-20201231.htm#p128"
GATE = (LETTER, MDNA_REPORTING, MDNA_DEFINITIONAL)


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
    missing = [pid for pid in GATE if pid not in rows]
    if missing:
        pytest.skip(f"gate passages absent from this corpus: {missing}")
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
def provider():
    config = ProviderConfig.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))
    instance = LocalOpenAICompatibleGenerationProvider(config)
    # Skip, not error, exactly as the other two live files do: the generation server is
    # legitimately stopped while the offline stages are worked on, and a setup exception is
    # indistinguishable in the summary from a broken provider.
    if not instance.health().ok:
        instance.close()
        pytest.skip(f"no generation server at {config.base_url}")
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def lane(ontology, provider):
    """The scope is injected here and nowhere else.

    `scoping.strategy` is `lexical` and stays so. Step 11 constructs the same lane with
    `HybridOntologyCandidateScope` and compares, which is only meaningful because no line of
    lane code changes between the two.
    """
    scope = LexicalOntologyCandidateScope(ontology, AliasIndex.from_ontology(ontology))
    return OntologyGuidedNarrativeClaimLane(
        ontology, scope, provider, deferred_metric_ids(ontology))


@pytest.fixture(scope="module")
def extractions(lane, passages):
    """One generation per gate passage, shared. Re-asking per assertion measures the server."""
    results = {}
    for passage_id in GATE:
        row = passages[passage_id]
        results[passage_id] = lane.extract_passage(
            row["text"],
            context=PassageContext(
                passage_id=passage_id,
                document_type=row["document_type"],
                form=row.get("form"),
                filing_date=row.get("filing_date"),
                heading_path=tuple(row.get("heading_path") or ())),
            document_id=row["document_id"])
    return results


@pytest.fixture(scope="module")
def validated(extractions, ontology, passages):
    """Every gate passage's claims driven the whole way: assemble, ontology, evidence."""
    results = {}
    for passage_id, extraction in extractions.items():
        assembly = assemble(list(extraction.claims), ontology=ontology, passage_rows=passages)
        findings = validate(assembly.claims, ontology=ontology,
                            passages=CatalogPassages(passages))
        results[passage_id] = (assembly, findings)
    return results


# -- (15)(16) the gate ---------------------------------------------------------------------------------


@pytest.mark.parametrize("passage_id", GATE)
def test_every_gate_passage_produces_a_usable_schema_conformant_answer(
        extractions, passage_id):
    """Conformance is enforced by the adapter, so the observable form is this: no passage
    came back with an answer the lane could not use."""
    codes = [issue.code for issue in extractions[passage_id].issues]
    assert MODEL_ANSWER_UNUSABLE not in codes, codes


@pytest.mark.parametrize("passage_id", GATE)
def test_every_gate_passage_answers_with_a_claim_or_a_stated_reason(extractions, passage_id):
    """Silence is the one answer this lane may not give."""
    extraction = extractions[passage_id]
    assert extraction.claims or extraction.issues
    assert all(issue.code in ISSUE_CODES for issue in extraction.issues), \
        extraction.counts_by_code()
    assert all(issue.detail or issue.rejected_claim for issue in extraction.issues)


@pytest.mark.parametrize("passage_id", (LETTER, MDNA_REPORTING))
def test_reporting_prose_yields_claims_that_survive_the_whole_chain(
        extractions, validated, passage_id):
    """A real letter passage and a real MD&A passage each produce at least one claim that
    reaches `OntologyClaim` with both validators clean. STAGE_10 §10's gate condition."""
    extraction = extractions[passage_id]
    assembly, findings = validated[passage_id]
    assert extraction.claims, extraction.counts_by_code()
    assert assembly.claims, [r.detail for r in assembly.rejected]
    assert findings.ok, [(f.code, f.detail) for f in findings.errors]
    # Not "no warnings". A quotation that is not in its passage is a defect and stays an
    # assertion; the ontology saying a metric is usually read from a table is information about
    # this lane, and asserting it away is how "0 warnings" got printed for weeks while four
    # were being raised and dropped.
    assert QUOTED_TEXT_NOT_IN_PASSAGE not in {f.code for f in findings.warnings}
    assert {f.code for f in findings.warnings} <= {ONTOLOGY_WARNING}
    for claim in assembly.claims:
        assert claim.metric_observation is not None
        assert claim.metric_observation.observation_id.startswith("obs:")


def test_the_ontologys_source_lane_warnings_are_visible(validated):
    """The signal step 11 needs, asserted where it is produced.

    Every one of these says the same thing: a metric the vocabulary expects from a table
    arrived from prose. That is not an error and it is not nothing, and a validator that
    relayed only errors made it unobservable."""
    _, findings = validated[LETTER]
    relayed = [f for f in findings.warnings if f.code == ONTOLOGY_WARNING]
    assert relayed, "the ontology raised no warning at all, which is itself worth knowing"
    assert all("unpreferred_source_lane" in f.detail for f in relayed)
    assert findings.ok, "a preference is not a prohibition"


def test_the_definitional_passage_abstains_rather_than_mining_a_number(extractions, passages):
    """The 10-K's Non-GAAP section explains how Contribution Profit is calculated and reports
    no value for it. Emitting anything here would attach a value to a definition.

    **This result is partly structural, and saying so is the point** *(corrected at review
    2026-08-02)*. `period_phrases` returns `()` for this passage — it prints no date, no
    "... ended <date>" and no quarter shorthand — so `response_schema` degenerates its
    `period_label` enum to `[""]` and **any** claim the model proposed would have been refused
    with `MISSING_PERIOD` before anything about definitions was considered. The passage is
    still the right gate: a definition paragraph that states no period is the shape this lane
    must not mine. But the empty claim list is not by itself evidence that the model recognised
    a definition, and the assertion below records which of the two did the work.
    """
    extraction = extractions[MDNA_DEFINITIONAL]
    assert period_phrases(passages[MDNA_DEFINITIONAL]["text"]) == (), \
        "this passage's structural abstention rests on it printing no resolvable period"
    assert extraction.claims == [], [c.metric_id for c in extraction.claims]
    assert extraction.issues
    assert all(issue.code in ISSUE_CODES for issue in extraction.issues)
    # Recorded, not asserted: whether the model declined on its own or the lane refused it.
    print("\ndefinitional passage, who declined:",
          {"model chose": [i.code for i in extraction.issues if not i.rejected_claim],
           "lane refused": [i.code for i in extraction.issues if i.rejected_claim]})


@pytest.mark.parametrize("passage_id", GATE)
def test_every_quoted_span_is_really_in_its_passage(extractions, passages, passage_id):
    """The check §6 allows and §8a.11 bounds: a span verifies a value, and never becomes an
    anchor. What is recorded is the passage's own characters."""
    text = passages[passage_id]["text"]
    for claim in extractions[passage_id].claims:
        assert claim.raw_text in text
        assert claim.extractor_metadata["value_text"] in claim.raw_text
        assert claim.extractor_metadata["span_match"] in ("exact", "folded")


@pytest.mark.parametrize("passage_id", GATE)
def test_the_evidence_anchor_is_the_normalized_passage_id(validated, passages, passage_id):
    """No sub-passage identifier, no character-offset id: an anchor that would not resolve in
    `passages.jsonl` is the one thing this pipeline must refuse."""
    assembly, _ = validated[passage_id]
    for claim in assembly.claims:
        for reference in claim.payload_evidence:
            assert reference.passage_id in passages
            assert reference.char_start is None and reference.char_end is None


def _keys_of(text: str) -> set[str]:
    keys = set()
    for phrase in period_phrases(text):
        for kind in ("duration", "instant"):
            resolved = resolve_period_phrase(phrase, period_type=kind)
            if resolved is not None:
                keys.add(resolved.key)
    return keys


@pytest.mark.parametrize("passage_id", GATE)
def test_no_claim_states_a_period_its_passage_does_not(extractions, passages, passage_id):
    """The floor: a period the passage never prints cannot be emitted at all.

    True but weak, and kept as the floor rather than sold as the guarantee. On
    `open-20210930.htm#p142` the passage prints its prior-year comparative too, so this
    assertion accepts a 2020 phrase attached to a 2021 figure — precisely the failure its
    predecessor's docstring claimed to prevent *(review 2026-08-02)*. The real guard is the
    next test."""
    keys = _keys_of(passages[passage_id]["text"])
    for claim in extractions[passage_id].claims:
        assert claim.period.key in keys, (claim.metric_id, claim.period, sorted(keys))


@pytest.mark.parametrize("passage_id", GATE)
def test_no_claim_attaches_a_prior_period_to_a_figure_that_does_not_state_one(
        extractions, passages, passage_id):
    """The guard the previous test was supposed to be, on the dimension §8a.7a names.

    The rule, in two cases, and neither of them is "the phrase appears somewhere in the
    passage":

    1. **The quoted sentence prints its own period.** Then the claim's period must be one the
       *sentence* states. Reaching past a date printed in the evidence to a different one
       elsewhere is not an attribution a reader could reproduce.
    2. **The quoted sentence prints none.** Then the claim's period must be the passage's own
       reporting period — the latest of each type, which is what a filing paragraph is about.
       A comparative paragraph prints its prior year too, and attaching that to an unqualified
       figure is the wrong-year failure with a different route in.

    This can fail on a legitimately reported prior-period *level* quoted in a sentence with no
    date of its own. That would be a real finding about this corpus rather than a flaky test,
    and the recorded attribution on the claim says which case it fell into.
    """
    text = passages[passage_id]["text"]
    reporting = reporting_period_keys(text)
    for claim in extractions[passage_id].claims:
        sentence_keys = _keys_of(claim.raw_text)
        attribution = {
            "metric": claim.metric_id,
            "period": claim.period.key,
            "chosen_phrase": claim.extractor_metadata.get("period_label"),
            "in_evidence": claim.extractor_metadata.get("period_label_in_evidence"),
            "distance": claim.extractor_metadata.get("period_label_distance_from_evidence"),
            "sentence states": sorted(sentence_keys),
            "passage reporting period": sorted(reporting),
        }
        if sentence_keys:
            assert claim.period.key in sentence_keys, attribution
        else:
            assert claim.period.key in reporting, attribution


@pytest.mark.parametrize("passage_id", GATE)
def test_every_claim_records_where_it_reached_for_its_period(extractions, passage_id):
    """§8a.12's measurable. Step 11 scores period attribution, and it cannot score what the
    lane did not record."""
    for claim in extractions[passage_id].claims:
        metadata = claim.extractor_metadata
        assert metadata["period_label"]
        assert metadata["period_label_in_evidence"] in (True, False)
        assert isinstance(metadata["period_label_distance_from_evidence"], int)
        assert isinstance(metadata["period_label_char_start"], int)


@pytest.mark.parametrize("passage_id", GATE)
def test_nothing_vendor_shaped_survives_into_a_claim(extractions, passage_id):
    for claim in extractions[passage_id].claims:
        serialised = json.dumps(claim.extractor_metadata)
        assert "httpx" not in serialised
        for volatile in ("latency", "prompt_tokens", "completion_tokens", "timings"):
            assert volatile not in serialised
        assert claim.source_lane == LANE_NAME


# -- (17) runtime statistics ---------------------------------------------------------------------------------


def test_runtime_statistics_are_recorded_for_every_gate_passage(lane, extractions, capsys):
    """Tokens, latency and the rejection count, printed so a run is auditable and kept off
    every artifact — they move between two identical requests, and STAGE_09 §11.2 settled
    that such numbers may be logged and may not be persisted."""
    stats = {stat.passage_id: stat for stat in lane.stats}
    lines = [f"{'passage':56} {'prompt':>7} {'compl':>6} {'ms':>8} "
             f"{'claims':>7} {'reject':>7} {'abstain':>8}"]
    for passage_id in GATE:
        stat = stats[passage_id]
        extraction = extractions[passage_id]
        rejected = len(extraction.rejected_claims)
        lines.append(
            f"{passage_id.split(':')[-1]:56} {stat.prompt_tokens:>7} "
            f"{stat.completion_tokens:>6} {stat.latency_ms:>8.0f} "
            f"{len(extraction.claims):>7} {rejected:>7} "
            f"{len(extraction.issues) - rejected:>8}")
        assert stat.prompt_tokens > 0
        assert stat.completion_tokens > 0
        assert stat.latency_ms > 0
        assert stat.finish_reason == "stop", "a truncated answer is not a partial claim"
        assert stat.candidate_concepts > 0
        assert not stat.replayed

    counts: dict[str, int] = {}
    for extraction in extractions.values():
        for code, number in extraction.counts_by_code().items():
            counts[code] = counts.get(code, 0) + number
    lines.append("rejections and abstentions by reason: " + ", ".join(
        f"{code}={number}" for code, number in sorted(counts.items())))
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_the_answer_is_stable_at_temperature_zero(lane, passages):
    """Temperature 0 and one request per passage. The content digest is what determinism is
    checked against; the envelope digest is the clock (`GenerationResult`'s docstring)."""
    row = passages[MDNA_DEFINITIONAL]
    context = PassageContext(
        passage_id=MDNA_DEFINITIONAL, document_type=row["document_type"],
        form=row.get("form"), filing_date=row.get("filing_date"),
        heading_path=tuple(row.get("heading_path") or ()))
    first = lane.extract_passage(row["text"], context=context,
                                 document_id=row["document_id"])
    second = lane.extract_passage(row["text"], context=context,
                                  document_id=row["document_id"])
    assert [c.model_dump() for c in first.claims] == [c.model_dump() for c in second.claims]
    assert [i.code for i in first.issues] == [i.code for i in second.issues]
