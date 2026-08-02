"""The event lane's live gate: the real server, real filings, real ontology claims.

Marked `live`, so `pytest -m "not live"` stays green on a machine with no model.
`test_event_lane.py` proves every refusal offline against a stub; this file proves the
refusals were the right ones to have, against output nobody wrote down in advance.

**The gate passages were chosen from the corpus by a stated rule and never from the benchmark**
(STAGE_10 §10). The rule, applied to `passages.jsonl` and reproducible from it: among narrative
passages of 300–2,200 characters that print at least one resolvable calendar date and that no
reviewed case annotates, take the highest by printed-date count with ties broken by passage id
— run separately over passages whose heading path names SUBSEQUENT EVENTS (42 qualify) and over
8-K earnings-release exhibits carrying a wire-service dateline (1 qualifies).

| gate | form | shape | why it is here |
| --- | --- | --- | --- |
| subsequent events | 10-K | four printed dates, one of them a convertible-note exchange | four dates in one passage is the shape a date reader gets wrong: three of them belong to reporting periods and one belongs to an event |
| dateline | 8-K EX-99.1 | a wire-service dateline, "today announced", and "as previously announced" | the announcement-versus-occurrence probe on a passage nobody annotated: the distribution date, the record date and the dateline are three different days in four sentences |

The second is the gate that matters. V1 §4.0b says the announcement date and the occurrence
date are different facts about different days, and the benchmark's only witness to that is a
case whose gold was corrected on 2026-08-02. This passage is a second witness, unannotated, and
the assertion below is the one the rule turns on: no date reaches a payload unless the passage
prints it, and the filing date reaches neither field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from extraction.core.assembly import assemble_events
from extraction.core.periods import date_phrases, parse_printed_date
from extraction.core.validation import validate
from extraction.providers import LocalOpenAICompatibleGenerationProvider, ProviderConfig
from extraction.stages.narrative import (
    DATE_NOT_STATED,
    EVENT_ISSUE_CODES,
    EVENT_LANE_NAME,
    OntologyGuidedEventLane,
    PassageContext,
)
from ontology import load_ontology

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]

SUBSEQUENT_EVENTS = "norm:0001801169:0001801169-21-000011:open-20201231.htm#p156"
DATELINE = "norm:0001801169:0001140361-25-042984:ef20059583_ex99-1.htm#p2"
GATE = (SUBSEQUENT_EVENTS, DATELINE)


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
    # Skip, not error, exactly as the other live files do: the generation server is
    # legitimately stopped while the offline stages are worked on.
    if not instance.health().ok:
        instance.close()
        pytest.skip(f"no generation server at {config.base_url}")
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def extractions(ontology, provider, passages):
    """One request per gate passage, once for the module. The lane is the shipped one."""
    lane = OntologyGuidedEventLane(ontology, provider)
    produced = {}
    for passage_id in GATE:
        row = passages[passage_id]
        produced[passage_id] = lane.extract_passage(
            row["text"],
            context=PassageContext(
                passage_id=passage_id, document_type=row["document_type"],
                form=row.get("form"), filing_date=row.get("filing_date"),
                heading_path=tuple(row.get("heading_path") or ())),
            document_id=row["document_id"])
    return produced, lane.stats


@pytest.mark.parametrize("passage_id", GATE)
def test_the_answer_conforms_and_produces_a_payload_or_a_stated_silence(
        extractions, passage_id):
    """"The JSON parsed" is not the gate. Either a payload, or a reason there is none."""
    produced, _ = extractions
    extraction = produced[passage_id]
    assert extraction.request_sha256
    assert "MODEL_ANSWER_UNUSABLE" not in {issue.code for issue in extraction.issues}
    assert extraction.events or extraction.relationships or extraction.issues
    for issue in extraction.issues:
        assert issue.code in EVENT_ISSUE_CODES, issue.code


@pytest.mark.parametrize("passage_id", GATE)
def test_no_date_reaches_a_payload_unless_the_passage_prints_it(
        extractions, passages, passage_id):
    """The temporal rule, against output nobody wrote down in advance.

    Both dates on every emitted event must resolve from a phrase the passage prints, and the
    document's own `filing_date` must reach neither unless the passage happens to print that
    day too.
    """
    produced, _ = extractions
    row = passages[passage_id]
    printed = {parse_printed_date(phrase) for phrase in date_phrases(row["text"])}
    for event in produced[passage_id].events:
        for value in (event.occurred_on, event.announced_on):
            assert value is None or value in printed, (event.event_type_id, value)
        if row.get("filing_date") not in printed:
            assert event.occurred_on != row.get("filing_date")
            assert event.announced_on != row.get("filing_date")


def test_the_two_date_answers_are_recorded_separately_and_auditably(extractions, passages):
    """The §4.0b probe on an unannotated passage, and it finds the residual rather than passing.

    **Measured 2026-08-02, and the finding is the point.** This press release is datelined
    2025-11-21, names a record date eleven days earlier, and describes a distribution on the
    dateline day. The lane emits a `securities_issuance` carrying `occurred_on` **and**
    `announced_on` both 2025-11-21, from a quoted span — "Opendoor Technologies Inc. (Nasdaq:
    OPEN) (" — that reports neither. The prompt states the rule in rule 3 and the model still
    answered one question twice.

    So this test asserts what the lane can actually guarantee, which is that the answer is
    **auditable**: each date is a phrase the passage prints, and the phrase the model chose for
    each field is recorded separately on the payload, so a reader can see that one was copied
    into the other. Narrowing this further needs a way to check that a sentence *reports an
    announcement*, which is a reading and not a constraint — the same shape as the §8a.12
    period-attribution residual the metric lane measures rather than closes.

    Asserting the desired behaviour here would make this file a wish. It is recorded in
    STAGE_12 §7 and in the committed report instead.
    """
    produced, _ = extractions
    extraction = produced[DATELINE]
    text = passages[DATELINE]["text"]
    printed = {parse_printed_date(phrase) for phrase in date_phrases(text)}
    dateline = parse_printed_date(text.split("(GLOBE NEWSWIRE)")[0])
    assert dateline is not None and dateline in printed

    copied = []
    for event in extraction.events:
        metadata = event.extractor_metadata
        assert "occurrence_date_text" in metadata and "announcement_date_text" in metadata
        for field, key in (("occurred_on", "occurrence_date_text"),
                           ("announced_on", "announcement_date_text")):
            value = getattr(event, field)
            chosen = metadata[key]
            assert (value is None) == (chosen in ("", DATE_NOT_STATED)), (field, chosen)
            assert value is None or parse_printed_date(chosen) == value
        if event.occurred_on is not None and event.occurred_on == event.announced_on:
            copied.append(event)
    print(f"{DATELINE}: {len(extraction.events)} events, {len(copied)} carrying one date in "
          f"both fields — the §4.0b residual, measured and not closed")


@pytest.mark.parametrize("passage_id", GATE)
def test_every_quoted_span_is_verbatim_in_the_passage(extractions, passages, passage_id):
    """The one failure this pipeline exists to refuse, checked on live output."""
    produced, _ = extractions
    text = passages[passage_id]["text"]
    for payload in list(produced[passage_id].events) + list(
            produced[passage_id].relationships):
        assert payload.raw_text in text, payload.raw_text[:80]
        assert payload.source_lane == EVENT_LANE_NAME


@pytest.mark.parametrize("passage_id", GATE)
def test_every_emitted_payload_reaches_an_ontology_claim_and_validates_clean(
        extractions, ontology, passages, passage_id):
    """V1 §11 criterion 1 on live output, driven the whole way through assembly."""
    produced, _ = extractions
    extraction = produced[passage_id]
    assembly = assemble_events(
        list(extraction.events), list(extraction.relationships), passage_rows=passages)
    assert len(assembly.claims) == len(extraction.events) + len(extraction.relationships)
    findings = validate(assembly.claims, ontology=ontology,
                        passages=CatalogPassages(passages))
    assert findings.errors == [], [f.detail for f in findings.errors]


def test_every_emitted_property_and_participant_is_one_its_event_type_declares(
        extractions, ontology):
    """The vocabulary's own declarations, enforced against live output rather than trusted."""
    produced, _ = extractions
    seen = 0
    for extraction in produced.values():
        for event in extraction.events:
            definition = ontology.registry.event_type(event.event_type_id)
            assert definition is not None
            declared = {p.role: set(p.entity_types) for p in (definition.participants or ())}
            assert set(event.properties) <= set(definition.allowed_properties or ())
            for participant in event.participants:
                assert participant.role in declared
                assert participant.entity_type in declared[participant.role]
                seen += 1
    assert seen, "no participant was emitted on either gate passage"


def test_runtime_statistics_are_recorded(extractions):
    """Printed for the record, asserted only for the shape: every one of them moves."""
    _, stats = extractions
    assert len(stats) == len(GATE)
    for entry in stats:
        assert entry.offered_event_types == 19
        assert entry.finish_reason == "stop", entry
        print(f"{entry.passage_id}: prompt {entry.prompt_tokens}, completion "
              f"{entry.completion_tokens}, {entry.latency_ms:.0f} ms, "
              f"{entry.offered_event_types} event types offered")
