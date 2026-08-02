"""The integrated run: the run directory, the catalogs, the five checks, and the report.

STAGE_13 §8's ten tests, plus the two disciplines steps 11 and 12 shipped without. Both of
those are here because both were caught by adversarial review rather than by a suite: step 12
rendered three tables that disagreed with the data behind them, and neither step checked that a
rendered number was the number it rendered.

**The fixture run is real.** Real lanes, the real ontology, the real corpus rows and the real
recorded answers — narrowed to the documents the reviewed cases name, so it exercises all three
lanes and finishes in seconds. Nothing here stubs a lane, because a run assembled from stubs
would prove the harness and not the pipeline.

**Every check that can fail is driven until it does.** A verification with no red case is a
verification nobody has seen work, so each of the five §5 checks appears twice: once against
the run, and once against a payload built to violate exactly its condition.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from extraction.context import ScopeUnavailableError, build_extraction_context, load_answers
from extraction.core.config import load_config
from extraction.core.corpus import JsonlPassageCorpus
from extraction.core.run_directory import (
    COMPLETION_MARKER,
    IncompleteRunError,
    RunDirectory,
    RunDirectoryWriter,
    list_runs,
    make_run_id,
)
from extraction.pipeline import (
    load_lane_outputs,
    rebuild_catalogs,
    regenerate_report,
    run as run_pipeline,
    run_id_for,
)
from extraction.stages.catalog import (
    CATALOG_FILES,
    CLAIMS,
    EVENTS,
    EVIDENCE,
    IDENTITY_FIELDS,
    ISSUES,
    OBSERVATIONS,
    REJECTED,
    RELATIONSHIPS,
    CatalogSet,
    MissingIdentityError,
    identity_of,
    render,
)
from extraction.stages.extract import (
    ANNOUNCEMENT_EQUALS_OCCURRENCE,
    EVENT_LANE,
    LANES,
    NARRATIVE_LANE,
    NO_STORED_ANSWER,
    RUN_ISSUE_CODES,
    SEVERITIES,
    TABLE_LANE,
    ExtractRequest,
    lane_outputs,
)
from extraction.stages.report import REPORT_FILENAME, build_report, render_markdown
from extraction.stages.verify import (
    CHECKS,
    CONFLICTING_DUPLICATES,
    DUPLICATE_IDENTITIES,
    EVIDENCE_RESOLUTION,
    FORBIDDEN_LANES,
    ONTOLOGY_VALIDATION,
    verify,
)
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "extraction"
CASES_DIR = REPO / "benchmarks" / "extraction" / "v1" / "cases"


# -- the fixture run -------------------------------------------------------------------------


def _case_documents() -> set[str]:
    """The documents the reviewed cases name. Read, never listed.

    A hand-written list would drift from the cases the moment one moved, and the point of
    narrowing the fixture is to keep all three lanes exercised — which is a property of *which*
    documents, not of how many.
    """
    documents: set[str] = set()
    for path in sorted(CASES_DIR.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in loaded.get("cases") or ():
            documents.add(case["document_id"])
    return documents


@pytest.fixture(scope="session")
def ontology():
    return load_ontology()


@pytest.fixture(scope="session")
def narrowed_corpus(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    documents = _case_documents()
    rows = [
        row for row in (
            json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines()
            if line.strip())
        if row["document_id"] in documents
    ]
    if not rows:
        pytest.skip("the normalized corpus holds none of the reviewed documents")
    return JsonlPassageCorpus(rows)


@pytest.fixture(scope="session")
def run_context(narrowed_corpus, ontology, tmp_path_factory):
    """A context whose runs land in a scratch directory, over the narrowed corpus."""
    config = load_config(REPO)
    root = tmp_path_factory.mktemp("extraction_runs")
    config = replace(config, paths=replace(config.paths, runs_root=str(root)))
    return build_extraction_context(
        REPO, config=config, ontology=ontology, corpus=narrowed_corpus)


@pytest.fixture(scope="session")
def outcome(run_context):
    return run_pipeline(run_context, ExtractRequest())


@pytest.fixture(scope="session")
def directory(outcome):
    return RunDirectory(outcome.path)


# -- §8.1 the run directory holds what §2 names, and the marker is last -----------------------


def test_a_run_directory_holds_every_file_the_plan_names(outcome):
    present = {path.name for path in outcome.path.iterdir()}
    expected = {
        "manifest.json", "claims.jsonl", "observations.jsonl", "events.jsonl",
        "relationships.jsonl", "evidence.jsonl", "issues.jsonl", "rejected_claims.jsonl",
        "report.md",
    }
    assert expected <= present, expected - present


def test_the_persisted_lane_outputs_are_in_the_directory_too(outcome):
    """§2's file list omits them and §7 requires them.

    The catalogs are built from the persisted lane outputs, so a directory holding the catalogs
    and not the outputs could not rebuild itself — which is the whole of what §7 asks a run to
    demonstrate. Recorded as a correction to the plan rather than as an extra file.
    """
    assert (outcome.path / lane_outputs.FILENAME).is_file()


def test_the_completion_marker_is_written_last_and_covers_every_other_file(outcome):
    marker = outcome.path / COMPLETION_MARKER
    assert marker.is_file()
    listed = {line.split("  ", 1)[1] for line in
              marker.read_text(encoding="utf-8").splitlines() if line.strip()}
    on_disk = {path.name for path in outcome.path.iterdir()} - {COMPLETION_MARKER}
    assert listed == on_disk
    # Written last means the manifest is inside it. A marker that predated the manifest could
    # be produced by a run that died between the two.
    assert "manifest.json" in listed


def test_the_marker_digests_are_the_bytes_on_disk(directory):
    assert directory.unchanged() == []


# -- §8.2 a partial run is unambiguously incomplete -------------------------------------------


def test_a_staged_run_has_no_marker_and_the_loader_refuses_it(tmp_path):
    writer = RunDirectoryWriter(tmp_path, "extract-v1-lexical-abcdef123456")
    writer.begin()
    writer.write(CLAIMS, "")
    assert writer.paths.staging.is_dir()
    assert not (writer.paths.staging / COMPLETION_MARKER).exists()
    with pytest.raises(IncompleteRunError):
        RunDirectory(writer.paths.staging)
    assert list_runs(tmp_path) == []


def test_a_finished_run_is_listed_and_a_partial_one_is_not(tmp_path):
    writer = RunDirectoryWriter(tmp_path, "extract-v1-lexical-abcdef123456")
    writer.begin()
    writer.write(CLAIMS, "")
    final = writer.finalize()
    assert list_runs(tmp_path) == ["extract-v1-lexical-abcdef123456"]
    assert RunDirectory(final).run_id == "extract-v1-lexical-abcdef123456"
    (final / COMPLETION_MARKER).unlink()
    assert list_runs(tmp_path) == []


def test_a_second_run_replaces_the_first_only_after_it_is_complete(tmp_path):
    """The destination is removed after staging finishes, so a run id never names a partial."""
    for body in ("a\n", "b\n"):
        writer = RunDirectoryWriter(tmp_path, "extract-v1-lexical-abcdef123456")
        writer.begin()
        writer.write(CLAIMS, body)
        writer.finalize()
    final = tmp_path / "extract-v1-lexical-abcdef123456"
    assert (final / CLAIMS).read_text(encoding="utf-8") == "b\n"
    assert not (tmp_path / "extract-v1-lexical-abcdef123456.partial").exists()


# -- §8.3 catalogs rebuild byte-identically ----------------------------------------------------


def test_catalogs_rebuild_byte_identically_from_the_same_lane_outputs(
        outcome, run_context, ontology):
    first = rebuild_catalogs(outcome.path, ontology=ontology, corpus=run_context.corpus)
    second = rebuild_catalogs(outcome.path, ontology=ontology, corpus=run_context.corpus)
    assert first == second
    for name in CATALOG_FILES:
        assert first[name] == (outcome.path / name).read_text(encoding="utf-8"), name


def test_regenerating_the_report_from_a_finished_run_is_byte_identical(outcome, ontology):
    regenerated = regenerate_report(outcome.path, ontology=ontology)
    assert regenerated == (outcome.path / REPORT_FILENAME).read_text(encoding="utf-8")


def test_the_run_id_is_derived_from_its_inputs_and_holds_no_clock(run_context):
    run_id = run_id_for(run_context)
    assert run_id == run_id_for(run_context)
    assert not re.search(r"\d{8}T\d{6}Z|\d{4}-\d{2}-\d{2}", run_id)
    other = make_run_id(
        config_hash="a" * 64, corpus_id=run_context.corpus.identity().corpus_id,
        ontology_definition_hash=run_context.ontology.definition_hash, strategy="lexical")
    assert other != run_id, "a different config must not land on the same path"


def test_no_catalog_and_no_report_carries_a_timestamp_a_duration_or_an_absolute_path(outcome):
    """§7. The manifest is the one place environment may be recorded, and it is excluded."""
    forbidden = (
        re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
        re.compile(r"latency|elapsed|duration_ms|prompt_tokens|completion_tokens|total_tokens"),
        re.compile(r"created_at|finished_at|generated_at"),
        re.compile(re.escape(str(REPO))),
        re.compile(r"/tmp/|[A-Za-z]:\\\\"),
    )
    for name in (*CATALOG_FILES, REPORT_FILENAME, lane_outputs.FILENAME):
        text = (outcome.path / name).read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not pattern.search(text), f"{name} matches {pattern.pattern}"


def test_the_manifest_is_the_one_place_the_environment_is_recorded(directory):
    manifest = directory.manifest()
    assert manifest["created_at"]
    assert manifest["environment"]["python_version"]
    assert set(manifest["catalog_digests"]) >= set(CATALOG_FILES)


# -- §8.4 and §8.5 identity --------------------------------------------------------------------


def test_every_catalog_row_carries_the_id_its_catalog_is_keyed_by(outcome):
    for name in CATALOG_FILES:
        for row in RunDirectory(outcome.path).rows(name):
            for field_name in IDENTITY_FIELDS[name]:
                assert row.get(field_name), f"{name}: {field_name} missing from {sorted(row)}"


def test_a_row_without_its_id_is_refused_rather_than_written():
    with pytest.raises(MissingIdentityError):
        render(OBSERVATIONS, [{"metric_id": "homes_sold", "value": 1}])
    with pytest.raises(MissingIdentityError):
        render(EVIDENCE, [{"claim_id": "claim:metric-observation:abc"}])


def test_the_payload_ids_have_the_readable_shape_the_plan_declares(outcome):
    directory = RunDirectory(outcome.path)
    for row in directory.rows(CLAIMS):
        assert re.fullmatch(r"claim:[a-z-]+:[0-9a-f]{12}", row["claim_id"]), row["claim_id"]
    for row in directory.rows(OBSERVATIONS):
        assert row["observation_id"].startswith("obs:")
        assert row["observation_id"].count(":") == 5
    for row in directory.rows(EVENTS):
        assert re.fullmatch(r"evt:[a-z-]+:(undated|\d{4}-\d{2}-\d{2}):[0-9a-f]{12}",
                            row["event_id"]), row["event_id"]
    for row in directory.rows(RELATIONSHIPS):
        assert row["relationship_instance_id"].startswith("rel:")


def test_the_duplicate_identity_check_reads_every_catalog(outcome, run_context, ontology):
    """The check's denominator is every row of every catalog, not only the claims."""
    result = verify(outcome.catalogs, ontology=ontology, passages=run_context.corpus)
    total = sum(len(rows) for rows in outcome.catalogs.rows.values())
    assert result.by_id(DUPLICATE_IDENTITIES).examined == total


# -- §8.6 the coverage table is computed from the ontology ------------------------------------


def test_the_coverage_table_names_every_in_scope_metric_including_the_zeros(
        outcome, ontology):
    from extraction.core.assembly import deferred_metric_ids

    deferred = deferred_metric_ids(ontology)
    in_scope = {definition.concept_id
                for definition in ontology.registry.by_category("metric_definition")
                } - deferred
    report = _report_of(outcome, ontology)
    listed = {row["metric_id"] for row in report.metric_coverage if row["in_scope"]}
    assert listed == in_scope
    assert len(in_scope) == 20
    assert any(row["observations"] == 0 for row in report.metric_coverage), (
        "a coverage table with no zero in it is not showing what a coverage table is for")
    markdown = (outcome.path / REPORT_FILENAME).read_text(encoding="utf-8")
    for metric in in_scope:
        assert metric in markdown, metric


def test_the_six_xbrl_first_metrics_are_listed_separately_and_emit_nothing(outcome, ontology):
    from extraction.core.assembly import DEFERRED_REASON, deferred_metric_ids

    report = _report_of(outcome, ontology)
    deferred = {row["metric_id"] for row in report.deferred_metrics}
    assert deferred == deferred_metric_ids(ontology)
    assert len(deferred) == 6
    assert all(row["observations"] == 0 for row in report.deferred_metrics)
    assert all(row["reason"] == DEFERRED_REASON for row in report.deferred_metrics)


# -- §8.7 every no-claim candidate carries a code ---------------------------------------------


def test_every_candidate_that_produced_no_claim_carries_a_known_reason_code(outcome):
    outputs = load_lane_outputs(outcome.path)
    empty = [o for o in outputs.outcomes if o.produced_nothing]
    assert empty, "the fixture run must contain candidates that produced nothing"
    for candidate in empty:
        assert candidate.issue_codes, candidate.passage_id
        for code in candidate.issue_codes:
            assert code in RUN_ISSUE_CODES, code


def test_every_issue_row_carries_a_known_code_and_a_known_severity(directory):
    for row in directory.rows(ISSUES):
        assert row["code"] in RUN_ISSUE_CODES, row["code"]
        assert row["severity"] in SEVERITIES, row["severity"]


def test_a_candidate_with_no_recorded_answer_is_recorded_and_never_scored_as_a_silence(
        outcome):
    """STAGE_13 §1.1's condition on a bounded run being trustworthy at all."""
    outputs = load_lane_outputs(outcome.path)
    misses = [o for o in outputs.outcomes if NO_STORED_ANSWER in o.issue_codes]
    assert misses, "the corpus is larger than the recorded answers; some request must miss"
    for candidate in misses:
        assert candidate.lane in (NARRATIVE_LANE, EVENT_LANE)
        assert candidate.request_issued is False
        assert candidate.request_sha256, "a miss must name the request it missed on"
    rows = {row["passage_id"] for row in RunDirectory(outcome.path).rows(ISSUES)
            if row["code"] == NO_STORED_ANSWER}
    assert {candidate.passage_id for candidate in misses} <= rows
    for row in RunDirectory(outcome.path).rows(ISSUES):
        if row["code"] == NO_STORED_ANSWER:
            assert row["severity"] == "not_attempted", (
                "a request that was never issued is not a silence and not a refusal")


def test_a_candidate_the_lanes_reached_is_counted_and_a_selected_one_is_never_skipped(outcome):
    outputs = load_lane_outputs(outcome.path)
    lanes = {candidate.lane for candidate in outputs.outcomes}
    assert lanes <= set(LANES)
    assert {TABLE_LANE, NARRATIVE_LANE, EVENT_LANE} == lanes, (
        "the fixture must exercise all three lanes")
    seen = {(candidate.lane, candidate.passage_id) for candidate in outputs.outcomes}
    assert len(seen) == len(outputs.outcomes), "one row per candidate, no duplicates"


# -- §8.8 each of the five verifications fails when its condition is violated -------------------


def _claims_of(outcome):
    return list(outcome.catalogs.claims)


def _single(claims, predicate):
    for claim in claims:
        if predicate(claim):
            return claim
    pytest.skip("the fixture run produced no claim of the shape this check needs")


def test_all_five_checks_run_and_report_a_denominator(outcome, run_context, ontology):
    result = verify(outcome.catalogs, ontology=ontology, passages=run_context.corpus)
    assert [check.check for check in result.checks] == list(CHECKS)
    for check in result.checks:
        assert check.examined > 0, check.check


def test_evidence_resolution_fails_when_an_anchor_points_at_nothing(
        outcome, run_context, ontology):
    claims = _claims_of(outcome)
    victim = _single(claims, lambda c: c.metric_observation is not None)
    broken = victim.model_copy(deep=True)
    broken.metric_observation.evidence[0].passage_id = "norm:nope:nope:nope.htm#p1"
    catalogs = CatalogSet(rows=dict(outcome.catalogs.rows), claims=[broken])
    result = verify(catalogs, ontology=ontology, passages=run_context.corpus)
    assert not result.by_id(EVIDENCE_RESOLUTION).passed
    assert any(f.code == "EVIDENCE_UNRESOLVED"
               for f in result.by_id(EVIDENCE_RESOLUTION).failures)


def test_ontology_validation_fails_when_a_claim_names_a_concept_the_vocabulary_lacks(
        outcome, run_context, ontology):
    claims = _claims_of(outcome)
    victim = _single(claims, lambda c: c.metric_observation is not None)
    broken = victim.model_copy(deep=True)
    broken.metric_observation.metric_id = "not_a_metric_this_ontology_declares"
    catalogs = CatalogSet(rows=dict(outcome.catalogs.rows), claims=[broken])
    result = verify(catalogs, ontology=ontology, passages=run_context.corpus)
    assert not result.by_id(ONTOLOGY_VALIDATION).passed


def test_forbidden_lanes_fails_when_an_xbrl_first_metric_arrives_from_a_normalized_lane(
        outcome, run_context, ontology):
    from extraction.core.assembly import deferred_metric_ids

    claims = _claims_of(outcome)
    victim = _single(claims, lambda c: c.metric_observation is not None)
    broken = victim.model_copy(deep=True)
    broken.metric_observation.metric_id = sorted(deferred_metric_ids(ontology))[0]
    catalogs = CatalogSet(rows=dict(outcome.catalogs.rows), claims=[broken])
    result = verify(catalogs, ontology=ontology, passages=run_context.corpus)
    check = result.by_id(FORBIDDEN_LANES)
    assert not check.passed
    assert any(f.code == "DEFERRED_METRIC_EMITTED" for f in check.failures)


def test_duplicate_identities_fails_when_two_different_rows_share_an_id(
        run_context, ontology):
    rows = {name: [] for name in CATALOG_FILES}
    rows[OBSERVATIONS] = [
        {"observation_id": "obs:homes-sold:opendoor:2025Q1:normalized-table:aaaaaaaaaaaa",
         "value": 1},
        {"observation_id": "obs:homes-sold:opendoor:2025Q1:normalized-table:aaaaaaaaaaaa",
         "value": 2},
    ]
    result = verify(CatalogSet(rows=rows, claims=[]),
                    ontology=ontology, passages=run_context.corpus)
    check = result.by_id(DUPLICATE_IDENTITIES)
    assert not check.passed
    assert check.failures[0].code == "DUPLICATE_IDENTITY"


def test_duplicate_identities_tolerates_two_identical_rows(run_context, ontology):
    """Deterministic ids mean two identical readings of one fact are one row, not a collision."""
    row = {"observation_id": "obs:homes-sold:opendoor:2025Q1:normalized-table:aaaaaaaaaaaa",
           "value": 1}
    rows = {name: [] for name in CATALOG_FILES}
    rows[OBSERVATIONS] = [dict(row), dict(row)]
    result = verify(CatalogSet(rows=rows, claims=[]),
                    ontology=ontology, passages=run_context.corpus)
    assert result.by_id(DUPLICATE_IDENTITIES).passed


def test_conflicting_duplicates_fails_when_one_id_carries_two_values(
        outcome, run_context, ontology):
    claims = _claims_of(outcome)
    victim = _single(claims, lambda c: c.metric_observation is not None)
    other = victim.model_copy(deep=True)
    other.metric_observation.value = victim.metric_observation.value + 1
    catalogs = CatalogSet(rows=dict(outcome.catalogs.rows), claims=[victim, other])
    result = verify(catalogs, ontology=ontology, passages=run_context.corpus)
    check = result.by_id(CONFLICTING_DUPLICATES)
    assert not check.passed
    assert check.failures[0].code == "DUPLICATE_OBSERVATION_CONFLICT"


# -- §8.9 every number in report.md is the number it renders -----------------------------------


def _report_of(outcome, ontology):
    directory = RunDirectory(outcome.path)
    manifest = directory.manifest()
    outputs = load_lane_outputs(outcome.path)
    return build_report(
        run_id=manifest["run_id"],
        rows={name: directory.rows(name) for name in CATALOG_FILES},
        outcomes=outputs.outcomes,
        unselected=outputs.unselected,
        ontology=ontology,
        corpus=manifest["corpus"],
        verification=manifest["verification"],
        bounds=manifest["bounds"],
        scoping=manifest["scope"])


def test_every_report_count_is_recomputable_from_the_catalogs(outcome, ontology):
    directory = RunDirectory(outcome.path)
    report = _report_of(outcome, ontology)
    assert report.counts["claims"] == len(directory.rows(CLAIMS))
    assert report.counts["observations"] == len(directory.rows(OBSERVATIONS))
    assert report.counts["events"] == len(directory.rows(EVENTS))
    assert report.counts["relationships"] == len(directory.rows(RELATIONSHIPS))
    assert report.counts["evidence_references"] == len(directory.rows(EVIDENCE))
    assert report.counts["issues"] == len(directory.rows(ISSUES))
    assert report.counts["rejected_claims"] == len(directory.rows(REJECTED))
    outputs = load_lane_outputs(outcome.path)
    assert report.counts["candidates"] == len(outputs.outcomes)
    assert (report.counts["candidates_with_a_claim"]
            + report.counts["candidates_with_no_claim"]) == len(outputs.outcomes)


def test_every_coverage_row_is_the_count_of_the_observations_it_describes(outcome, ontology):
    rows = RunDirectory(outcome.path).rows(OBSERVATIONS)
    for entry in _report_of(outcome, ontology).metric_coverage:
        matching = [row for row in rows if row["metric_id"] == entry["metric_id"]]
        assert entry["observations"] == len(matching), entry["metric_id"]
        assert entry["documents"] == len({row["document_id"] for row in matching})
        for lane, count in entry["by_lane"].items():
            assert count == sum(1 for row in matching if row["source_lane"] == lane)


def test_every_lane_and_document_type_row_is_the_count_it_describes(outcome, ontology):
    directory = RunDirectory(outcome.path)
    claims = directory.rows(CLAIMS)
    issues = directory.rows(ISSUES)
    outputs = load_lane_outputs(outcome.path)
    report = _report_of(outcome, ontology)
    for entry in report.by_lane:
        lane = entry["lane"]
        assert entry["candidates"] == sum(
            1 for o in outputs.outcomes if o.lane == lane)
        assert entry["claims"] == sum(1 for row in claims if row["lane"] == lane)
        assert entry["issues"] == sum(1 for row in issues if row["lane"] == lane)
    for entry in report.by_document_type:
        document_type = entry["document_type"]
        assert entry["claims"] == sum(
            1 for row in claims if row["document_type"] == document_type)
    assert sum(entry["claims"] for entry in report.by_claim_kind) == len(claims)


def test_every_no_claim_group_is_the_set_of_candidates_it_describes(outcome, ontology):
    outputs = load_lane_outputs(outcome.path)
    issues = RunDirectory(outcome.path).rows(ISSUES)
    for entry in _report_of(outcome, ontology).no_claim_by_code:
        code = entry["code"]
        candidates = {(o.lane, o.passage_id) for o in outputs.outcomes
                      if o.produced_nothing and code in o.issue_codes}
        assert entry["candidates"] == len(candidates), code
        assert entry["lanes"] == sorted({lane for lane, _ in candidates})
        assert entry["occurrences"] == sum(1 for row in issues if row["code"] == code)
        assert entry["severities"] == sorted(
            {row["severity"] for row in issues if row["code"] == code})


def test_the_remaining_report_blocks_are_recomputable_too(
        outcome, ontology, run_context, narrowed_corpus):
    """The blocks the per-table tests above do not reach.

    Written because "every number is checked" is a claim about *every* number, and the four
    blocks below — the corpus, the vocabulary, the unselected census, the refusals and the
    scoping cost — are exactly the ones a reviewer would find unchecked.
    """
    directory = RunDirectory(outcome.path)
    report = _report_of(outcome, ontology)
    outputs = load_lane_outputs(outcome.path)
    issues = directory.rows(ISSUES)
    rejected = directory.rows(REJECTED)

    identity = narrowed_corpus.identity()
    assert report.corpus["documents"] == identity.documents
    assert report.corpus["passages"] == identity.passages == len(narrowed_corpus)
    assert report.corpus["table_passages"] == identity.table_passages
    assert report.corpus["corpus_id"] == identity.corpus_id

    metrics = list(ontology.registry.by_category("metric_definition"))
    assert report.ontology["metric_definitions"] == len(metrics)
    assert (report.ontology["in_scope_metrics"] + report.ontology["deferred_metrics"]
            == len(metrics))
    assert report.ontology["deferred_refusals_recorded"] == sum(
        1 for row in issues if row["code"] == "DEFERRED_REQUIRED_SOURCE_LANE")

    assert {(row["lane"], row["reason"]): row["passages"] for row in report.unselected} == {
        (key.split(":", 1)[0], key.split(":", 1)[1]): count
        for key, count in outputs.unselected.items()}

    for entry in report.rejected:
        assert entry["rejections"] == sum(
            1 for row in rejected
            if row["refused_by"] == entry["refused_by"] and row["code"] == entry["code"])
    assert sum(entry["rejections"] for entry in report.rejected) == len(rejected)

    sizes = [o.scope_size for o in outputs.outcomes if o.scope_size is not None]
    assert report.scoping["scoped_candidates"] == len(sizes)
    assert report.scoping["scope_size_min"] == min(sizes)
    assert report.scoping["scope_size_max"] == max(sizes)
    assert report.scoping["scope_size_mean"] == round(sum(sizes) / len(sizes), 3)
    assert report.scoping["requests_issued"] == sum(
        1 for o in outputs.outcomes if o.request_issued)
    assert report.scoping["requests_without_a_stored_answer"] == sum(
        1 for o in outputs.outcomes if NO_STORED_ANSWER in o.issue_codes)
    assert report.scoping["strategy"] == run_context.scoping.strategy
    assert report.counts["candidates_with_no_claim_and_no_code"] == 0, (
        "a candidate that produced nothing and said nothing is the one silence "
        "STAGE_13 §4 forbids")


def test_a_narrative_candidate_whose_request_missed_still_reports_its_scope_size(outcome):
    """The scope was consulted before the request; losing that number would make the
    corpus-cost figure a statement about the passages that happened to fail early."""
    outputs = load_lane_outputs(outcome.path)
    missed = [o for o in outputs.outcomes
              if o.lane == NARRATIVE_LANE and NO_STORED_ANSWER in o.issue_codes]
    assert missed, "the fixture must contain a narrative candidate with no recorded answer"
    assert any(o.scope_size for o in missed), (
        "every narrative miss reported a scope size of zero, which the selector's own alias "
        "evidence makes implausible")


def test_every_rendered_table_cell_is_a_value_the_report_holds(outcome, ontology):
    """The step 11 and step 12 finding, as a check.

    Every integer in every table row of `report.md` must appear in the report data. A cell the
    renderer computed would be a number with no source, which is exactly the shape of the three
    tables step 12 shipped that disagreed with what they rendered.
    """
    report = _report_of(outcome, ontology)
    markdown = render_markdown(report)
    known = set()
    for block in (report.counts, report.corpus, report.ontology, report.scoping,
                  report.headline, report.bounds):
        known.update(str(value) for value in block.values() if isinstance(value, (int, float)))
    for group in (report.metric_coverage, report.deferred_metrics, report.no_claim_by_code,
                  report.by_lane, report.by_document_type, report.by_claim_kind,
                  report.verification, report.unselected, report.rejected):
        for entry in group:
            for value in entry.values():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    known.add(str(value))
                elif isinstance(value, dict):
                    known.update(str(inner) for inner in value.values()
                                 if isinstance(inner, (int, float)))
    unexplained = []
    for line in markdown.splitlines():
        if not line.startswith("| ") or set(line) <= set("| -"):
            continue
        for cell in (part.strip() for part in line.strip("|").split("|")):
            if re.fullmatch(r"-?\d+(\.\d+)?", cell) and cell not in known:
                unexplained.append((cell, line))
    assert unexplained == [], unexplained[:5]


def test_the_renderer_computes_no_rate_of_its_own():
    """An AST check beside the value check, because a sentinel cannot distinguish the two.

    A division in the renderer produces a number that is *derived from* looked-up values, so it
    would pass a test that only asks whether every cell traces back to the data. The rule is
    that the renderer formats; the only way to check a rule about code is to read the code.
    """
    tree = ast.parse((PACKAGE / "stages" / "report" / "markdown_report.py").read_text(
        encoding="utf-8"))
    offenders = [
        ast.dump(node) for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mult, ast.Sub))
    ]
    assert offenders == []


def test_changing_a_report_value_changes_the_rendered_table(outcome, ontology):
    """The sentinel half. A cell that did not move was not read out of the data."""
    report = _report_of(outcome, ontology)
    before = render_markdown(report)
    for entry in report.by_lane:
        entry["claims"] = 987654
    after = render_markdown(report)
    assert before != after
    assert "987654" in after


# -- §8.10 nothing under extraction reads the evaluation harness -------------------------------


def test_nothing_under_extraction_imports_or_names_the_evaluation_harness():
    """The fifth guard, over the modules step 13 added.

    Duplicated from `test_narrative_lane.py` on purpose: the rule is what makes the scoping
    decision honest, and a rule enforced in one file is a rule that disappears when that file
    is split.
    """
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
                    and body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if "benchmark" in name.lower():
                    offenders.append(f"{path.relative_to(REPO)}: imports {name}")
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and "benchmark" in node.value.lower()):
                offenders.append(f"{path.relative_to(REPO)}: names {node.value!r}")
    assert offenders == []


def test_what_a_run_loads_is_answers_and_holds_no_gold_annotation():
    """The other half of the rule: the path is configuration, and what it points at is answers.

    A guard on names alone would pass over a configuration pointing at a case file. This one
    reads what a run actually loads and asserts every row is a recorded model answer — a
    request digest, a model, a finish reason and the raw response — with no expected claim, no
    expected abstention and no case id anywhere in it.
    """
    config = load_config(REPO)
    store = load_answers(config.answer_store_paths, model_id="Qwen3.5-9B-Q4_K_M.gguf")
    assert len(store), "the configured answer stores hold nothing; the run would be empty"
    forbidden = ("gold", "expected", "case_id", "gold_claims", "expected_abstentions")
    for answer in store.answers():
        row = answer.as_row()
        assert set(row) == {"request_sha256", "content_sha256", "model_id",
                            "provider_model_id", "temperature", "max_tokens",
                            "prompt_version", "finish_reason", "raw_content"}
        for key in row:
            assert key not in forbidden
        parsed = json.loads(answer.raw_content)
        assert not (set(parsed) & set(forbidden)), sorted(parsed)


def test_the_prompt_versions_this_run_replays_under_are_the_recorded_ones(outcome):
    """§1.1: no answer was regenerated, so no prompt version may have moved."""
    manifest = RunDirectory(outcome.path).manifest()
    config = load_config(REPO)
    store = load_answers(config.answer_store_paths, model_id=manifest["bounds"]["model_id"])
    recorded = {answer.prompt_version for answer in store.answers()}
    assert recorded == {manifest["bounds"]["prompt_version"],
                        manifest["bounds"]["event_prompt_version"]}
    assert manifest["provider"]["mode"] == "replay_only"
    assert manifest["bounds"]["provider_calls_permitted"] == 0


# -- the temporal residual, recorded and not fixed ---------------------------------------------


def test_an_event_with_equal_dates_is_flagged_for_review_and_never_refused(outcome):
    directory = RunDirectory(outcome.path)
    events = directory.rows(EVENTS)
    equal = [row for row in events if row["dates_equal"]]
    if not equal:
        pytest.skip("no event in the fixture run carries both dates equal")
    diagnostics = [row for row in directory.rows(ISSUES)
                   if row["code"] == ANNOUNCEMENT_EQUALS_OCCURRENCE]
    assert len(diagnostics) == len(equal)
    for row in diagnostics:
        assert row["severity"] == "diagnostic", "equal dates are a review flag, not an error"
        assert row["rejected_claim"] is False
    for row in equal:
        assert row["review_flag"] == ANNOUNCEMENT_EQUALS_OCCURRENCE
        assert row["occurrence_date_text"], "the chosen phrase must stay visible"
        assert row["announcement_date_text"]
        assert row["evidence_quoted_text"], "the raw evidence must stay visible"
        assert row["event_id"] in {claim["payload_id"] for claim in directory.rows(CLAIMS)}


def test_the_report_names_the_follow_up_verifier_and_says_it_is_a_flag_not_an_error(outcome):
    markdown = (outcome.path / REPORT_FILENAME).read_text(encoding="utf-8")
    assert "announcement-versus-occurrence verifier" in markdown
    assert "not proof of a defect" in markdown
    assert "review flag, not an error" in markdown


# -- the durable record round-trips ------------------------------------------------------------


def test_the_persisted_lane_outputs_parse_back_to_what_was_written(outcome):
    text = (outcome.path / lane_outputs.FILENAME).read_text(encoding="utf-8")
    parsed = lane_outputs.parse(text.splitlines())
    assert lane_outputs.render(parsed) == text


def test_an_unknown_lane_output_kind_raises_rather_than_being_ignored():
    with pytest.raises(ValueError):
        lane_outputs.parse(['{"kind":"something_else"}'])


def test_a_scope_that_cannot_be_built_offline_says_so_at_construction(ontology):
    from extraction.stages.scoping import ScopingConfig
    from extraction.stages.select import AliasIndex
    from extraction.context import build_scope

    with pytest.raises(ScopeUnavailableError):
        build_scope(ontology, AliasIndex.from_ontology(ontology),
                    ScopingConfig(strategy="hybrid"))


def test_the_configured_scope_is_the_one_the_run_records(outcome, run_context):
    manifest = RunDirectory(outcome.path).manifest()
    assert manifest["scope"]["strategy"] == run_context.scoping.strategy
    assert manifest["scope"]["scope_name"] == run_context.scope.name
    assert manifest["run_id"].split("-")[2] == run_context.scoping.strategy, (
        "the readable segment of the run id must say which scope produced it")


def test_the_manifest_and_the_report_state_the_same_five_results(outcome, ontology):
    """Two artifacts, one answer. A manifest and a report that disagreed about a check would
    make the check unciteable."""
    manifest = RunDirectory(outcome.path).manifest()
    report = _report_of(outcome, ontology)
    assert [row["check"] for row in manifest["verification"]] == list(CHECKS)
    assert manifest["verification"] == report.verification
    assert report.headline["verification_passed"] == all(
        row["passed"] for row in manifest["verification"])


def test_identity_of_uses_the_compound_key_evidence_needs():
    row = {"claim_id": "claim:event:abc", "passage_id": "norm:x#p1"}
    assert identity_of(EVIDENCE, row) == "claim:event:abc\x1fnorm:x#p1"
    assert identity_of(CLAIMS, {"claim_id": "claim:event:abc"}) == "claim:event:abc"


# -- the CLI, driven -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cli_root(outcome):
    """The scratch runs root, as the CLI's `--root` sees it.

    The CLI resolves its runs root from `config/extraction.yaml`, so a test driving it against
    a scratch run has to point the whole configuration at the scratch tree. Copying the config
    beside the run is the smallest way to do that without a monkeypatch that would also hide a
    change to how the CLI resolves paths.
    """
    root = outcome.path.parent.parent / "repo"
    (root / "config").mkdir(parents=True, exist_ok=True)
    document = yaml.safe_load(
        (REPO / "config" / "extraction.yaml").read_text(encoding="utf-8"))
    document["paths"]["extraction_runs"] = str(outcome.path.parent)
    document["run"]["answer_stores"] = [
        str(REPO / path) for path in document["run"]["answer_stores"]]
    document["paths"]["normalization_catalog"] = str(
        load_config(REPO).catalog_root)
    (root / "config" / "extraction.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return root


def _cli(cli_root, *argv):
    from extraction.cli import main

    return main(["--root", str(cli_root), *argv])


def test_the_cli_lists_runs_and_inspects_one(cli_root, outcome, capsys):
    assert _cli(cli_root, "runs") == 0
    assert outcome.run_id in capsys.readouterr().out
    assert _cli(cli_root, "inspect", outcome.run_id) == 0
    printed = capsys.readouterr().out
    assert outcome.run_id in printed
    for check in CHECKS:
        assert check in printed


def test_the_cli_shows_one_claim_with_its_payload_and_its_evidence(
        cli_root, outcome, capsys):
    claim_id = RunDirectory(outcome.path).rows(CLAIMS)[0]["claim_id"]
    assert _cli(cli_root, "claim", claim_id, "--run", outcome.run_id) == 0
    printed = capsys.readouterr().out
    assert claim_id in printed
    assert "evidence.jsonl" in printed


def test_the_cli_refuses_a_claim_id_no_run_holds(cli_root, outcome, capsys):
    assert _cli(cli_root, "claim", "claim:metric-observation:ffffffffffff",
                "--run", outcome.run_id) == 1
    assert "no claim" in capsys.readouterr().out


def test_the_cli_filters_by_metric_event_relationship_document_and_passage(
        cli_root, outcome, capsys):
    observations = RunDirectory(outcome.path).rows(OBSERVATIONS)
    metric = observations[0]["metric_id"]
    assert _cli(cli_root, "filter", "--metric", metric, "--run", outcome.run_id) == 0
    assert metric in capsys.readouterr().out
    assert _cli(cli_root, "filter", "--document", observations[0]["document_id"],
                "--run", outcome.run_id) == 0
    assert observations[0]["document_id"] in capsys.readouterr().out
    events = RunDirectory(outcome.path).rows(EVENTS)
    if events:
        assert _cli(cli_root, "filter", "--event", events[0]["event_type_id"],
                    "--run", outcome.run_id) == 0
        assert events[0]["event_type_id"] in capsys.readouterr().out


def test_the_cli_shows_issues_and_rejected_claims(cli_root, outcome, capsys):
    assert _cli(cli_root, "issues", "--code", NO_STORED_ANSWER,
                "--run", outcome.run_id) == 0
    assert NO_STORED_ANSWER in capsys.readouterr().out
    assert _cli(cli_root, "rejected", "--run", outcome.run_id) == 0
    capsys.readouterr()


def test_the_cli_regenerates_the_report_and_rebuilds_the_catalogs(
        cli_root, outcome, capsys):
    assert _cli(cli_root, "report", outcome.run_id) == 0
    assert "byte-identical" in capsys.readouterr().out
    assert _cli(cli_root, "rebuild", outcome.run_id) == 0
    printed = capsys.readouterr().out
    for name in CATALOG_FILES:
        assert name in printed
    assert "DIFFERS" not in printed


def test_the_cli_refuses_a_partial_run(cli_root, outcome, capsys):
    writer = RunDirectoryWriter(outcome.path.parent, "extract-v1-lexical-000000000000")
    writer.begin()
    writer.write(CLAIMS, "")
    try:
        assert _cli(cli_root, "inspect", "extract-v1-lexical-000000000000.partial") == 1
        assert "partial run" in capsys.readouterr().err
    finally:
        import shutil

        shutil.rmtree(writer.paths.staging)
