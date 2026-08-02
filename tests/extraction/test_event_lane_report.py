"""The committed event report is a claim, and these tests are what make it one.

The discipline is step 11's, applied from the start rather than after a review. Two of its
findings are the reason several tests below exist rather than one: three of its Markdown tables
were checked by nothing, and its "the runner computes no score" guard was an operator grep that
`statistics.fmean(...) * 0.5` walked straight through. So the checks here are structural — a
sentinel substituted into `totals` and the artifact re-rendered — with the grep kept as the
cheap half, and **every rendered table is compared cell by cell to the object it renders.**

The strongest one is `test_every_prose_number_in_the_markdown_is_a_number_the_run_computed`:
the brief for this stage asks for every prose number to be checked, not only the tables, and a
report's prose is where a stale transcription survives longest.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from benchmarks.extraction.v1 import event_evaluation, event_runner
from extraction.stages.narrative import AnswerStore, MissingAnswerError
from extraction.stages.narrative.answer_store import ROW_FIELDS
from extraction.stages.narrative.events_public import ISSUE_CODES as EVENT_ISSUE_CODES

REPO = Path(__file__).resolve().parents[2]
BENCHMARK_PACKAGE = REPO / "benchmarks" / "extraction" / "v1"
COMMITTED_JSON = event_runner.REPORTS_DIR / f"{event_runner.REPORT_STEM}.json"
COMMITTED_MARKDOWN = event_runner.REPORTS_DIR / f"{event_runner.REPORT_STEM}.md"

# Fixed so a report built in a test never depends on the working tree's HEAD; the real commit
# is the one field allowed to vary.
PINNED_COMMIT = "0" * 40

COMMIT_IN_JSON = re.compile(r'^(\s*"implementation_commit": ")[^"]*(",?)$', re.MULTILINE)
COMMIT_IN_MARKDOWN = re.compile(r"^\| implementation commit \| `[^`]*` \|$", re.MULTILINE)

# What may never appear in a byte-identical artifact. `max_output_tokens` is deliberately not
# here and is deliberately in the report: it is a configured budget the request digest is taken
# over, so it identifies the run rather than measuring it. Every string below *is* a
# measurement — it differs between two identical requests.
FORBIDDEN_IN_JSON = (
    "prompt_tokens", "completion_tokens", "total_tokens", "latency", "elapsed_",
    "timestamp", "created_at", "duration_", "wall_clock", "generated_at", "_ms",
    "attempts", "raw_sha256",
)

_NEGATIONS = ("not", "never", "refus", "cannot")
_PRECISION_WINDOW = 80

# A value no real score can take, so its presence in a rendered cell proves the renderer read
# `totals` and its absence proves it did not.
_SENTINEL_SCORE = 0.123456


@pytest.fixture(scope="module")
def store_path() -> Path:
    if not event_runner.ANSWER_STORE.is_file():
        pytest.skip(f"no committed answer store at {event_runner.ANSWER_STORE}")
    return event_runner.ANSWER_STORE


@pytest.fixture(scope="module")
def report(store_path, repo_config):
    if not (repo_config.catalog_root / "passages.jsonl").is_file():
        pytest.skip("no normalized corpus")
    return event_runner.build_report(
        catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)


@pytest.fixture(scope="module")
def committed() -> dict:
    if not COMMITTED_JSON.is_file():
        pytest.skip("no committed event report")
    return json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))


def _rows_under(markdown: str, heading: str) -> list[list[str]]:
    """The cells of every table row under a heading, up to the next heading of any depth."""
    start = markdown.index(heading)
    rest = markdown[start + len(heading):]
    end = min((offset for offset in (rest.find("\n## "), rest.find("\n### "))
               if offset != -1), default=-1)
    section = rest if end == -1 else rest[:end]
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines()
            if line.startswith("|") and not set(line) <= set("| -")]


def _case_rows(markdown: str, case_id: str, heading: str) -> list[list[str]]:
    """The rows of one `#### ` table inside one case's `### ` section, or [] if absent.

    Sliced on the sub-heading rather than filtered by column count: the per-case tables have
    overlapping widths — the gold-events table and the expected-abstentions table are both five
    columns — and a width filter reads one as the other.
    """
    start = markdown.index(f"\n### {case_id}\n")
    end = markdown.find("\n### ", start + 1)
    section = markdown[start:] if end == -1 else markdown[start:end]
    if heading not in section:
        return []
    rest = section[section.index(heading) + len(heading):]
    stop = rest.find("\n#### ")
    body = rest if stop == -1 else rest[:stop]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in body.splitlines()
            if line.startswith("|") and not set(line) <= set("| -")]
    return rows[1:]  # the header row, which names columns rather than carrying values


# -- reproducibility --------------------------------------------------------------------------


def test_replaying_twice_is_byte_identical(tmp_path, store_path, repo_config):
    """Two full replays, not one object rendered twice.

    Rendering the same object twice would only prove `json.dumps` is a function. The point is
    that the store, the lane, both scopes, the scoring and the ordering are deterministic end
    to end with no server involved.
    """
    def generate(directory: Path) -> tuple[bytes, bytes]:
        built = event_runner.build_report(
            catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)
        json_path, markdown_path = event_runner.write_reports(built, directory)
        return json_path.read_bytes(), markdown_path.read_bytes()

    first = generate(tmp_path / "first")
    second = generate(tmp_path / "second")
    assert first[0] == second[0]
    assert first[1] == second[1]


def test_the_committed_json_is_byte_for_byte_what_a_fresh_replay_renders(report):
    fresh = COMMIT_IN_JSON.sub(r"\1<commit>\2", event_runner.render_json(report))
    on_disk = COMMIT_IN_JSON.sub(
        r"\1<commit>\2", COMMITTED_JSON.read_text(encoding="utf-8"))
    assert on_disk.encode("utf-8") == fresh.encode("utf-8")


def test_the_committed_markdown_is_byte_for_byte_what_a_fresh_replay_renders(report):
    fresh = COMMIT_IN_MARKDOWN.sub(
        "| implementation commit | `<commit>` |", event_runner.render_markdown(report))
    on_disk = COMMIT_IN_MARKDOWN.sub(
        "| implementation commit | `<commit>` |",
        COMMITTED_MARKDOWN.read_text(encoding="utf-8"))
    assert on_disk.encode("utf-8") == fresh.encode("utf-8")


def test_the_report_names_the_store_it_was_replayed_from(report, store_path):
    import hashlib

    assert report.answer_store["answers"] == len(AnswerStore(store_path))
    assert report.answer_store["bytes"] == store_path.stat().st_size
    assert report.answer_store["sha256"] == hashlib.sha256(
        store_path.read_bytes()).hexdigest()


def test_no_duration_token_count_or_timestamp_appears_in_the_json(committed):
    text = json.dumps(committed)
    for forbidden in FORBIDDEN_IN_JSON:
        assert forbidden not in text, forbidden


def test_the_answer_store_carries_no_operational_statistic(store_path):
    for line in store_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            assert set(row) == set(ROW_FIELDS)


def test_a_replay_with_a_missing_answer_raises_and_names_the_request(tmp_path, repo_config):
    """A silence must never be scored as an abstention.

    One row is removed from a copy of the store and the report is rebuilt; the failure must
    be an error naming the request, not a case that quietly scores zero.
    """
    store = AnswerStore(event_runner.ANSWER_STORE)
    rows = event_runner.ANSWER_STORE.read_text(encoding="utf-8").splitlines()
    assert len(rows) >= 2
    truncated = tmp_path / "event_v1.jsonl"
    truncated.write_text("\n".join(rows[1:]) + "\n", encoding="utf-8")
    with pytest.raises(MissingAnswerError) as raised:
        event_runner.build_report(
            catalog_root=repo_config.catalog_root, store_path=truncated,
            implementation_commit=PINNED_COMMIT)
    assert "no stored answer for request" in str(raised.value)
    assert len(store) == len(rows)


def test_an_absent_store_is_an_error_rather_than_an_empty_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        event_runner.replay_provider(tmp_path / "absent.jsonl")


# -- every score computed in exactly one place -------------------------------------------------


def test_the_runner_computes_no_score_of_its_own():
    """The cheap half of the guard, kept as the cheap half and not as the guard.

    It catches a runner that grew its own `hits / total`. It does not catch one that
    recomputes a score any other way, which is why the sentinel test below exists.
    """
    tree = ast.parse((BENCHMARK_PACKAGE / "event_runner.py").read_text(encoding="utf-8"))
    divisions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv))
        and not (isinstance(node.right, ast.JoinedStr)
                 or (isinstance(node.right, ast.Constant)
                     and isinstance(node.right.value, str)))
    ]
    assert divisions == [], [ast.unparse(node) for node in divisions]


def test_the_markdown_reads_every_score_out_of_totals_and_derives_none(report):
    """Substitute an impossible value into `totals["scores"]` and re-render.

    A renderer that reads `totals` prints it in every score position; one that recomputes
    anything prints something else, and the something else is exactly what this finds.
    """
    saved = {name: dict(view.totals["scores"]) for name, view in report.views.items()}
    saved_delta = dict(report.comparison["score_delta_hybrid_minus_lexical"])
    try:
        for view in report.views.values():
            for name in view.totals["scores"]:
                view.totals["scores"][name] = _SENTINEL_SCORE
        for name in report.comparison["score_delta_hybrid_minus_lexical"]:
            report.comparison["score_delta_hybrid_minus_lexical"][name] = _SENTINEL_SCORE
        markdown = event_runner.render_markdown(report)
    finally:
        for name, view in report.views.items():
            view.totals["scores"].update(saved[name])
        report.comparison["score_delta_hybrid_minus_lexical"].update(saved_delta)

    printed = re.findall(r"[|+ ]\*{0,2}\+?(\d\.\d{3})\*{0,2} \|", markdown)
    assert printed, "no three-decimal number was found in the rendered Markdown at all"
    unexpected = sorted({v for v in printed if float(v) != round(_SENTINEL_SCORE, 3)})
    assert unexpected == [], (
        "a three-decimal number in the Markdown did not come from totals['scores']; the "
        "renderer is deriving it", unexpected)


def test_every_rendered_table_number_is_the_number_it_renders(report):
    """Every table in the artifact, cell by cell, against the object it renders.

    Step 11 shipped with three tables checked by nothing, and a `+0.4` mutation on the one
    step 13 reads produced a row contradicting the headline with the suite green. A number
    nothing compares to its source is a screenshot.
    """
    markdown = event_runner.render_markdown(report)
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]

    # (1) the headline, including its printed denominators.
    headline = {row[0]: row for row in _rows_under(markdown, "## Headline")}
    for name, key, _ in event_evaluation.DIMENSIONS:
        row = headline[name]
        assert f"({lexical.totals['score_denominators'][key]} lex / " in row[1], name
        assert f"{hybrid.totals['score_denominators'][key]} hyb)" in row[1], name
        assert float(row[2].strip("*")) == round(lexical.totals["scores"][key], 3), name
        assert float(row[3].strip("*")) == round(hybrid.totals["scores"][key], 3), name
    for label, key in (("matched/emitted, events *(not precision)*",
                        "matched_over_emitted_events"),
                       ("matched/emitted, relationships *(not precision)*",
                        "matched_over_emitted_relationships")):
        row = headline[label]
        assert float(row[2]) == round(lexical.totals["scores"][key], 3), label
        assert float(row[3]) == round(hybrid.totals["scores"][key], 3), label

    # (2) Counts — every row is a `totals` key printed verbatim under both scopes.
    counts = _rows_under(markdown, "## Counts")[1:]
    assert len(counts) >= 20
    for label, lex, hyb in counts:
        candidates = [key for key, value in lexical.totals.items()
                      if isinstance(value, int) and str(value) == lex
                      and str(hybrid.totals.get(key)) == hyb]
        assert candidates, (label, lex, hyb)

    # (3) the routing table, which is the evidence the rejected options rest on.
    routing = {row[0]: row for row in _rows_under(markdown, "## The routing mechanism")}
    assert int(routing["declared event types offered"][1]) == report.vocabulary["event_types"]
    assert int(routing["of those, declaring any alias"][1]) == 0
    assert int(routing[
        "relationship predicates derived from their `allowed_relationships`"][1]) == (
        report.vocabulary["relationship_predicates"])

    # (4) the per-case reachability table under the same heading.
    reach = {row[0]: row for row in _rows_under(markdown, "## The routing mechanism")}
    for entry in report.comparison["scope_reachability"]:
        row = reach[f"[`{entry['case_id']}`](#{entry['case_id']})"]
        assert row[4] == (f"{entry['lexical_scope_concepts']}/"
                          f"{entry['hybrid_scope_concepts']}"), entry["case_id"]
        for column, key in ((2, "gold_event_types_the_lexical_scope_offers"),
                            (3, "gold_event_types_the_hybrid_scope_offers")):
            assert (row[column] == "**none**") is not bool(entry[key]), entry["case_id"]

    # (5) the failure-category census.
    for row in _rows_under(markdown, "## Failure classification"):
        category = row[0].strip("`")
        if category not in event_evaluation.FAILURE_CATEGORIES:
            continue
        assert int(row[1]) == lexical.totals["failures_by_category"][category], category
        assert int(row[2]) == hybrid.totals["failures_by_category"][category], category

    # (6) the issue census.
    for row in _rows_under(markdown, "## Rejections and findings"):
        code = row[0].strip("`")
        if code not in lexical.totals["issues_by_code"]:
            continue
        assert row[1] == (f"{lexical.totals['rejections_by_code'].get(code, 0)}/"
                          f"{hybrid.totals['rejections_by_code'].get(code, 0)}"), code
        assert row[2] == (f"{lexical.totals['model_abstentions_by_code'].get(code, 0)}/"
                          f"{hybrid.totals['model_abstentions_by_code'].get(code, 0)}"), code

    # (7) the scope comparison's delta column.
    seen = set()
    for row in _rows_under(markdown, "## Lexical versus hybrid"):
        key = row[0]
        if key not in report.comparison["score_delta_hybrid_minus_lexical"]:
            continue
        seen.add(key)
        assert float(row[1]) == round(
            report.comparison["score_delta_hybrid_minus_lexical"][key], 3), key
    assert seen == set(report.comparison["score_delta_hybrid_minus_lexical"])

    # (8) the structured-output table.
    validity = {row[0]: row for row in _rows_under(markdown, "## Structured-output validity")}
    for label, key in (("provider calls the lane made", "provider_calls"),
                       ("distinct requests behind them", "distinct_requests"),
                       ("conformant on the first attempt", "conformant_first_attempt"),
                       ("conformant after a retry", "conformant_after_retry"),
                       ("never conformant", "never_conformant")):
        assert int(validity[label][1]) == report.structured_output[key], label

    # (9) the per-case summary.
    summary = {row[0]: row for row in _rows_under(markdown, "## Per-case results")}
    for case in lexical.cases:
        other = hybrid.case(case.case_id)
        row = summary[f"[`{case.case_id}`](#{case.case_id})"]
        assert int(row[1]) == case.counts["gold_events"], case.case_id
        assert int(row[2]) == case.counts["gold_relationships"], case.case_id
        assert row[3] == (
            f"{case.counts['matched_events']}+{case.counts['matched_relationships']}/"
            f"{other.counts['matched_events']}+{other.counts['matched_relationships']}")
        assert row[5] == f"{case.counts['failures']}/{other.counts['failures']}"


def test_every_score_is_its_own_numerator_over_its_own_denominator(report):
    """The headline against the fraction printed beside it, in the same object.

    **Added by adversarial review 2026-08-02, and it was needed.** `_ratio` was changed to
    `min(1.0, hit / total + 0.25)`, the report was regenerated, and the suite stayed green:
    `participant entity id` read **1.000** with `score_numerators` 6 and `score_denominators`
    7 in the same JSON, and `abstention code agreement` read 0.250 over a numerator of 0.
    Every other check compared the Markdown to `totals["scores"]` and nothing compared
    `totals["scores"]` to anything, so the one number a reader takes away was the one number
    derived from nothing checkable.
    """
    for view in report.views.values():
        numerators = view.totals["score_numerators"]
        denominators = view.totals["score_denominators"]
        for key, value in view.totals["scores"].items():
            total = denominators[key]
            expected = 1.0 if total == 0 else round(numerators[key] / total, 6)
            assert value == expected, (view.name, key, numerators[key], total, value)
            assert numerators[key] <= total, (view.name, key)


def test_the_routing_vocabulary_counts_are_the_offered_vocabularys_own(report):
    """The routing table's five counts, against the object the lane actually reads.

    **Added by adversarial review 2026-08-02.** `participant roles / entity types / property
    names` was rendered from `report.vocabulary` and compared to nothing; `+5` on the roles
    count regenerated a report reading `22 / 24 / 43` with the suite green. And
    `event_types_declaring_an_alias` was the literal `0`, checked by an assertion that the
    literal equalled `0` — the row the whole routing argument rests on was declared rather
    than measured, so an alias added to an event type could not have moved it.
    """
    from extraction.stages.narrative import offered_vocabulary
    from ontology import load_ontology

    ontology = load_ontology()
    vocabulary = offered_vocabulary(ontology)
    assert report.vocabulary["event_types"] == len(vocabulary.event_type_ids)
    assert report.vocabulary["relationship_predicates"] == len(vocabulary.relationship_ids)
    assert report.vocabulary["participant_roles"] == len(vocabulary.roles)
    assert report.vocabulary["entity_types"] == len(vocabulary.entity_types)
    assert report.vocabulary["declared_property_names"] == len(vocabulary.property_names)
    assert report.vocabulary["event_types_declaring_an_alias"] == sum(
        1 for definition in ontology.registry.by_category("event_type")
        if tuple(definition.aliases or ()))

    markdown = event_runner.render_markdown(report)
    routing = {row[0]: row for row in _rows_under(markdown, "## The routing mechanism")}
    assert routing["participant roles / entity types / property names"][1] == (
        f"{len(vocabulary.roles)} / {len(vocabulary.entity_types)} / "
        f"{len(vocabulary.property_names)}")


def test_every_per_case_table_cell_is_the_value_it_renders(report):
    """The six per-case tables, cell by cell, against the `CaseScore` they render.

    **Added by adversarial review 2026-08-02**, which falsified three of them and regenerated
    the artifact with the suite green: the participants table printed the *expected* entity id
    in the emitted column, hiding the run's only `participant_wrong`; and the expected-
    abstentions table printed `honoured: yes` for `NO_HEADCOUNT_STATED` while the Counts table
    said 3 of 4 and the failure table listed the same expectation as `silence_broken`. Step 11
    shipped three unchecked tables and the finding recurred here in six.
    """
    markdown = event_runner.render_markdown(report)
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    seen_tables = 0
    for case in lexical.cases:
        other = hybrid.case(case.case_id)

        gold_rows = _case_rows(markdown, case.case_id, "#### Gold events")
        seen_tables += bool(gold_rows)
        assert len(gold_rows) == len(case.gold_events), case.case_id
        matched: dict[str, list] = {}
        for match in case.matched_events:
            matched.setdefault(match.event_type_id, []).append(match)
        used: dict[str, int] = {}
        for gold, row in zip(case.gold_events, gold_rows):
            pool = matched.get(gold.event_type_id) or []
            index = used.get(gold.event_type_id, 0)
            used[gold.event_type_id] = index + 1
            assert row[0] == f"`{gold.event_type_id}`", case.case_id
            if index >= len(pool):
                assert row[4] == "**MISSED**", case.case_id
                continue
            match = pool[index]
            # The verdict, and the two gold dates it was scored against, must be this gold
            # event's own — not the next one's.
            assert match.expected_occurred_on == gold.occurred_on, case.case_id
            assert match.expected_announced_on == gold.announced_on, case.case_id
            assert (row[4] == "ok") is match.all_ok, (case.case_id, row)
            if not match.all_ok:
                assert row[4] == "**WRONG**: " + ", ".join(match.failed_dimensions)

        participant_rows = _case_rows(
            markdown, case.case_id, "#### Participants of the matched events")
        seen_tables += bool(participant_rows)
        expected_participants = [(m, p) for m in case.matched_events for p in m.participants]
        assert len(participant_rows) == len(expected_participants), case.case_id
        for (match, participant), row in zip(expected_participants, participant_rows):
            assert row[2] == f"`{participant.expected_entity_id}`", case.case_id
            assert row[3].strip("*") == (
                f"`{participant.emitted_entity_id}`" if participant.emitted_entity_id
                else "—"), case.case_id
            assert (row[3].startswith("**")) is not participant.participant_entity_ok
            assert row[5].strip("*") == (
                f"`{participant.emitted_entity_type}`" if participant.emitted_entity_type
                else "—"), case.case_id
            assert row[6] == ("yes" if participant.emitted_named
                              else "**unresolved**" if participant.emitted_named is False
                              else "—"), case.case_id

        edge_rows = _case_rows(markdown, case.case_id, "#### Gold relationships")
        seen_tables += bool(edge_rows)
        assert len(edge_rows) == len(case.gold_relationships), case.case_id
        edges: dict[str, list] = {}
        for match in case.matched_relationships:
            edges.setdefault(match.relationship_id, []).append(match)
        used_edges: dict[str, int] = {}
        for gold, row in zip(case.gold_relationships, edge_rows):
            pool = edges.get(gold.relationship_id) or []
            index = used_edges.get(gold.relationship_id, 0)
            used_edges[gold.relationship_id] = index + 1
            assert row[0] == f"`{gold.relationship_id}`", case.case_id
            assert row[1] == f"`{gold.source_id}` → `{gold.target_id}`", case.case_id
            if index >= len(pool):
                assert row[3] == "**MISSED**", case.case_id
                continue
            match = pool[index]
            assert match.expected_source_id == gold.source_id, case.case_id
            assert match.expected_target_id == gold.target_id, case.case_id
            assert row[2] == f"`{match.emitted_source_id}` → `{match.emitted_target_id}`"
            assert (row[3] == "ok") is match.all_ok, (case.case_id, row)

        abstention_rows = _case_rows(markdown, case.case_id, "#### Expected abstentions")
        seen_tables += bool(abstention_rows)
        assert len(abstention_rows) == len(case.abstentions), case.case_id
        for verdict, row in zip(case.abstentions, abstention_rows):
            assert row[0] == f"`{verdict.reason}`", case.case_id
            assert row[1] == ("yes" if verdict.silence_kept else "**no**"), case.case_id
            assert row[2] == ("yes" if verdict.answer_usable else "**no**"), case.case_id
            assert row[3] == ("yes" if verdict.honoured else "**no**"), case.case_id
            assert row[4] == ("yes" if verdict.code_recorded else "no"), case.case_id

        issue_rows = _case_rows(markdown, case.case_id, "#### Issues recorded")
        seen_tables += bool(issue_rows)
        assert len(issue_rows) == len(set(case.issues_by_code) | set(other.issues_by_code))
        for row in issue_rows:
            code = row[0].strip("`")
            assert row[1] == (f"{case.rejections_by_code.get(code, 0)}/"
                              f"{case.model_abstentions_by_code.get(code, 0)}"), code
            assert row[2] == (f"{other.rejections_by_code.get(code, 0)}/"
                              f"{other.model_abstentions_by_code.get(code, 0)}"), code

        addition_rows = _case_rows(markdown, case.case_id, "#### Emitted but not annotated")
        seen_tables += bool(addition_rows)
        assert [r[1].strip("`") for r in addition_rows] == list(
            case.additional_events) + list(case.additional_relationships), case.case_id
    # Every one of the six shapes must have been exercised at least once, or a heading rename
    # would turn this whole test into six empty loops.
    assert seen_tables >= 13, seen_tables


def test_every_prose_number_in_the_markdown_is_a_number_the_run_computed(report):
    """The brief asks for **every prose number** to be computed and checked, not only tables.

    Every integer appearing outside a table row is collected and required to be a number the
    run produced — a total, a denominator, a numerator, a declared structured-output count, a
    per-case count, or one of the few structural constants the prose legitimately names. A
    hand-typed figure that drifts from the run fails here, which is the failure mode a report
    of this shape actually has.
    """
    markdown = event_runner.render_markdown(report)
    prose = "\n".join(line for line in markdown.splitlines()
                      if not line.startswith("|") and not line.startswith("#"))
    # Inline code is a *name*, not a number: `norm:0001801169:…#p117` and
    # `asset_backed_senior_revolving_2022_10` carry digits that identify things rather than
    # count them, and this test is about counts. Names are checked by the tests that resolve
    # them — every passage id, every predicate, every property — not by this one.
    prose = re.sub(r"`[^`]*`", "`…`", prose)
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]

    allowed: set[int] = set()
    for view in (lexical, hybrid):
        allowed.update(v for v in view.totals.values() if isinstance(v, int))
        allowed.update(view.totals["score_denominators"].values())
        allowed.update(view.totals["score_numerators"].values())
        allowed.update(view.totals["failures_by_category"].values())
        allowed.update(view.totals["event_pairs_by_basis"].values())
        allowed.update(view.totals["relationship_pairs_by_basis"].values())
        for case in view.cases:
            allowed.update(case.counts.values())
    allowed.update(v for v in report.structured_output.values() if isinstance(v, int))
    allowed.update(v for v in report.vocabulary.values() if isinstance(v, int))
    allowed.update(report.comparison[key] for key in (
        "cases", "cases_with_identical_requests", "cases_with_identical_payloads",
        "gold_event_types_reached_by_any_scope"))
    # Structural constants the prose names, each with the object it comes from.
    allowed.add(len(event_evaluation.DIMENSIONS))
    allowed.add(len(event_evaluation.FAILURE_CATEGORIES))
    allowed.add(len(event_runner.ROUTING["rejected"]))
    from extraction.stages.narrative import EVENT_MODEL_ABSTENTION_REASONS
    allowed.add(len(EVENT_MODEL_ABSTENTION_REASONS))
    # Section references — "§11.2", "§4.0b", "V1 §11" — and the measured prompt figures the
    # routing section quotes, which are declared data checked by
    # `test_the_declared_prompt_cost_is_what_the_lane_actually_builds`.
    for reference in re.findall(r"§[\d.]+[a-z]?", prose):
        allowed.update(int(part) for part in re.findall(r"\d+", reference))
    for measured in re.findall(r"\d[\d,]*", event_runner.ROUTING["cost"]):
        allowed.add(int(measured.replace(",", "")))
    allowed.update({4, 3})  # the "3.5 characters per token" and "4.39–4.48" ratios
    allowed.update({0, 1, 2, 512, 8192, 9})  # rule numbers, the slot, the output floor

    printed = {int(value.replace(",", ""))
               for value in re.findall(r"(?<![\w.])(\d[\d,]*)(?![\w.%])", prose)}
    assert printed - allowed == set(), sorted(printed - allowed)


def test_the_declared_prompt_cost_is_what_the_lane_actually_builds(report, repo_config):
    """The routing section quotes a prompt cost. It must be the cost the lane pays.

    Declared rather than derived because it is a *token* count from the server and the report
    may carry no measurement that moves; the character count and the budget are pure, so both
    are re-derived here and the quoted range must contain them.
    """
    from benchmarks.extraction.v1 import runner as table_runner
    from extraction.stages.narrative import (
        DEFAULT_MAX_OUTPUT_TOKENS,
        OntologyGuidedEventLane,
        PassageContext,
        output_budget,
    )
    from extraction.stages.narrative.event_prompt import render_event_types
    from ontology import load_ontology

    ontology = load_ontology()
    passages = table_runner.load_passages(repo_config.catalog_root)

    class _Recorder:
        model_id = "stub"

        def __init__(self):
            self.prompts: list[str] = []

        def generate(self, *, prompt, schema, max_tokens=1024, temperature=0.0):
            self.prompts.append(prompt)
            raise RuntimeError("not reached")

    lane = OntologyGuidedEventLane(ontology, _Recorder())
    block = render_event_types(lane._definitions)
    assert str(len(block)) in event_runner.ROUTING["cost"].replace(",", "")

    budgets = []
    for case in event_runner.load_event_cases():
        row = passages[case.passage_id]
        provider = _Recorder()
        one = OntologyGuidedEventLane(ontology, provider)
        try:
            one.extract_passage(row["text"], context=PassageContext(
                passage_id=case.passage_id, document_type=row["document_type"],
                form=row.get("form"), filing_date=row.get("filing_date"),
                heading_path=tuple(row.get("heading_path") or ())))
        except RuntimeError:
            pass
        budget, _ = output_budget(
            provider.prompts[0], ceiling=DEFAULT_MAX_OUTPUT_TOKENS, context_tokens=8192)
        budgets.append(budget)
    quoted = [int(v.replace(",", ""))
              for v in re.findall(r"(\d[\d,]*)–(\d[\d,]*) tokens of the",
                                  event_runner.ROUTING["cost"])[0]]
    assert quoted == [min(budgets), max(budgets)], (quoted, sorted(budgets))
    del lane


# -- the classifier -------------------------------------------------------------------------------


def test_the_failure_classifier_is_total_and_disjoint(report):
    """Every missed gold payload, every wrong match and every broken silence, exactly once."""
    for view in report.views.values():
        for case in view.cases:
            expected = (len(case.missed_events) + len(case.missed_relationships)
                        + sum(1 for m in case.matched_events if not m.all_ok)
                        + sum(1 for m in case.matched_relationships if not m.all_ok)
                        + sum(1 for v in case.abstentions if not v.honoured))
            assert len(case.failures) == expected, case.case_id
            for failure in case.failures:
                assert failure.category in event_evaluation.FAILURE_CATEGORIES


def test_a_non_gold_emission_is_never_classified_as_a_failure(report):
    """`matched/emitted` must not become a precision under another name."""
    for view in report.views.values():
        for case in view.cases:
            subjects = {failure.subject for failure in case.failures}
            for key in case.additional_events + case.additional_relationships:
                assert key not in subjects, key


def test_the_decision_order_is_the_category_list_and_the_report_prints_it():
    assert event_evaluation.DECISION_ORDER == event_evaluation.FAILURE_CATEGORIES
    printed = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    line = next(l for l in printed.splitlines() if l.startswith("Decision order:"))
    assert [part.strip(" `") for part in line.split(":", 1)[1].split("→")] == list(
        event_evaluation.DECISION_ORDER)


def test_the_order_decides_which_category_a_multiply_wrong_match_gets():
    """A pair wrong about its evidence and its date is `evidence_ungrounded`, not both.

    Driven through the real classifier with a real `EventMatch`, and asserted by reversing the
    order and watching the answer change — a category that survives a reversed order was not
    decided by the order.
    """
    match = event_evaluation.EventMatch(
        event_type_id="workforce_reduction", basis="event_type_only", shared_entity_ids=0,
        expected_occurred_on="2020-04-15", emitted_occurred_on="2021-03-04",
        expected_announced_on=None, emitted_announced_on=None,
        expected_properties={}, emitted_properties={}, additional_property_names=(),
        participants=(), raw_text="x",
        occurrence_date_ok=False, announcement_date_ok=True, properties_ok=True,
        event_evidence_ok=False)
    failures = event_evaluation.classify_failures(
        gold_events=(), gold_relationships=(), matched_events=(match,),
        matched_relationships=(), missed_events=(), missed_relationships=(),
        abstentions=(), usable=True, issues=())
    assert [f.category for f in failures] == [event_evaluation.EVIDENCE_UNGROUNDED]

    saved = event_evaluation.DECISION_ORDER
    try:
        event_evaluation.DECISION_ORDER = tuple(reversed(saved))
        reversed_failures = event_evaluation.classify_failures(
            gold_events=(), gold_relationships=(), matched_events=(match,),
            matched_relationships=(), missed_events=(), missed_relationships=(),
            abstentions=(), usable=True, issues=())
    finally:
        event_evaluation.DECISION_ORDER = saved
    assert [f.category for f in reversed_failures] == [
        event_evaluation.OCCURRENCE_DATE_WRONG]


class _Payload:
    """The minimum `_event_match` and `_relationship_match` read off an emitted payload."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _gold_event(**overrides):
    base = dict(
        event_type_id="executive_change", occurred_on="2020-04-15",
        announced_on="2020-04-16",
        participants=(event_evaluation.GoldParticipant(
            role="officer", entity_id="dana_reyes", entity_type="person"),),
        properties={"position": "Chief Executive Officer"}, note=None)
    base.update(overrides)
    return event_evaluation.GoldEvent(**base)


def _emitted_event(**overrides):
    base = dict(
        event_type_id="executive_change", occurred_on="2020-04-15",
        announced_on="2020-04-16",
        participants=[_Payload(role="officer", entity_id="dana_reyes",
                               entity_type="person", entity_text="Dana Reyes", named=True)],
        properties={"position": "Chief Executive Officer"}, raw_text="x")
    base.update(overrides)
    return _Payload(**base)


EVENT_DISAGREEMENTS = (
    ("occurrence_date_ok", {"occurred_on": "2021-01-01"}),
    ("announcement_date_ok", {"announced_on": "2021-01-01"}),
    ("properties_ok", {"properties": {"position": "Chairman"}}),
)
PARTICIPANT_DISAGREEMENTS = (
    ("participant_role_ok", {"role": "employer"}),
    ("participant_entity_ok", {"entity_id": "someone_else"}),
    ("participant_entity_type_ok", {"entity_type": "public_company"}),
)


def test_every_event_dimension_is_false_when_the_pair_disagrees_about_it():
    """One disagreement at a time, driven through the real matcher.

    **Added by adversarial review 2026-08-02.** Nine of the eighteen scored dimensions read
    1.000 on the committed run, and replacing all nine computations with the literal `True`
    left the whole suite green and the artifact byte-identical: `occurrence_date_ok`,
    `announcement_date_ok`, `event_evidence_ok`, `participant_role_ok`,
    `participant_entity_type_ok`, `source_id_ok`, `source_type_ok`, `target_type_ok` and
    `relationship_evidence_ok`. A dimension nothing can make fail is not measuring anything,
    and 1.000 is exactly the value that hides it.
    """
    agree = event_evaluation._event_match(
        _gold_event(), _emitted_event(), "shared_participants", lambda payload: True)
    for name in ("occurrence_date_ok", "announcement_date_ok", "properties_ok",
                 "event_evidence_ok"):
        assert getattr(agree, name) is True, name
    assert agree.all_ok

    for name, override in EVENT_DISAGREEMENTS:
        match = event_evaluation._event_match(
            _gold_event(), _emitted_event(**override), "shared_participants",
            lambda payload: True)
        assert getattr(match, name) is False, name
        assert match.failed_dimensions == (name[:-3],), name

    ungrounded = event_evaluation._event_match(
        _gold_event(), _emitted_event(), "shared_participants", lambda payload: False)
    assert ungrounded.event_evidence_ok is False
    assert ungrounded.failed_dimensions == ("event_evidence",)

    for name, override in PARTICIPANT_DISAGREEMENTS:
        emitted = _emitted_event(participants=[_Payload(
            role="officer", entity_id="dana_reyes", entity_type="person",
            entity_text="Dana Reyes", named=True, **{})])
        emitted.participants[0].__dict__.update(override)
        match = event_evaluation._event_match(
            _gold_event(), emitted, "shared_participants", lambda payload: True)
        assert getattr(match.participants[0], name) is False, name
        assert not match.all_ok, name


RELATIONSHIP_DISAGREEMENTS = (
    ("source_id_ok", {"source_id": "someone_else"}),
    ("target_id_ok", {"target_id": "somewhere_else"}),
    ("source_type_ok", {"source_type": "subsidiary"}),
    ("target_type_ok", {"target_type": "subsidiary"}),
)


def test_every_relationship_dimension_is_false_when_the_pair_disagrees_about_it():
    """The same, for edges. Same reason: four of the five read 1.000 and could not fail."""
    gold = event_evaluation.GoldRelationship(
        relationship_id="HOLDS_POSITION_AT", source_id="dana_reyes", source_type="person",
        target_id="opendoor", target_type="public_company", note=None)
    fields = dict(relationship_id="HOLDS_POSITION_AT", source_id="dana_reyes",
                  source_type="person", target_id="opendoor",
                  target_type="public_company", raw_text="x")
    agree = event_evaluation._relationship_match(
        gold, _Payload(**fields), "shared_endpoints", lambda payload: True)
    assert agree.all_ok

    for name, override in RELATIONSHIP_DISAGREEMENTS:
        match = event_evaluation._relationship_match(
            gold, _Payload(**{**fields, **override}), "shared_endpoints",
            lambda payload: True)
        assert getattr(match, name) is False, name
        assert match.failed_dimensions == (name[:-3],), name

    ungrounded = event_evaluation._relationship_match(
        gold, _Payload(**fields), "shared_endpoints", lambda payload: False)
    assert ungrounded.relationship_evidence_ok is False
    assert ungrounded.failed_dimensions == ("relationship_evidence",)


NAMED_FAILURES = (
    ("event-executive-change-ceo-2025", event_evaluation.PROPERTIES_WRONG),
    ("event-credit-facility-established-2022", event_evaluation.PARTICIPANT_WRONG),
    ("event-credit-facility-established-2022", event_evaluation.ENDPOINT_WRONG),
    ("event-workforce-reduction-2020", event_evaluation.PROPERTIES_WRONG),
    ("event-workforce-reduction-2020", event_evaluation.SILENCE_BROKEN),
)


@pytest.mark.parametrize("case_id,category", NAMED_FAILURES,
                         ids=[f"{c}-{k}" for c, k in NAMED_FAILURES])
def test_a_named_failure_keeps_its_named_category(report, case_id, category):
    """Five (case, category) pairs pinned by name.

    Without these the classifier could redistribute every failure and the totals would still
    add up. Step 11 found exactly that: reversing the decision order changed the artifact and
    left the suite green.
    """
    case = report.case(case_id)
    assert category in {failure.category for failure in case.failures}


# -- the scored claims, driven ---------------------------------------------------------------------


def test_the_run_emits_every_gold_event_and_every_gold_relationship(report):
    """The stage's own goal, stated as a test rather than as a paragraph."""
    for view in report.views.values():
        assert view.totals["matched_events"] == view.totals["gold_events"] == 4
        assert view.totals["matched_relationships"] == view.totals["gold_relationships"] == 2


def test_an_abstained_occurrence_date_scores_as_correct_and_a_filled_one_would_not(report):
    """The temporal rule as a scored dimension, checked in both directions.

    The two executive-change pairs have gold with **no** `occurred_on`; the run emits none and
    scores them correct. Mutating one emitted date to any value must make the same pair wrong —
    otherwise the dimension is measuring nothing.
    """
    matched = [m for view in report.views.values() for m in view.matched_executive_changes()] \
        if hasattr(report.views["lexical"], "matched_executive_changes") else [
            m for view in report.views.values() for case in view.cases
            for m in case.matched_events if m.expected_occurred_on is None]
    assert matched, "no matched event has an absent gold occurrence date"
    assert all(m.emitted_occurred_on is None and m.occurrence_date_ok for m in matched)

    import dataclasses

    for match in matched:
        filled = dataclasses.replace(match, emitted_occurred_on="2025-09-11")
        assert filled.expected_occurred_on != filled.emitted_occurred_on
        assert not dataclasses.replace(
            filled, occurrence_date_ok=(
                filled.expected_occurred_on == filled.emitted_occurred_on)
        ).occurrence_date_ok


def test_no_emitted_event_carries_a_date_its_passage_does_not_print(report, repo_config):
    """Every date in every payload is a date the passage prints, resolved.

    The strongest available check on "the filing date populates neither": the 8-K's filing
    date is 2025-09-11 and the passage never prints it.
    """
    from benchmarks.extraction.v1 import runner as table_runner
    from extraction.core.periods import date_phrases, parse_printed_date

    passages = table_runner.load_passages(repo_config.catalog_root)
    for view in report.views.values():
        for case in view.cases:
            printed = {parse_printed_date(phrase)
                       for phrase in date_phrases(passages[case.passage_id]["text"])}
            for event in case.events:
                for value in (event.occurred_on, event.announced_on):
                    assert value is None or value in printed, (case.case_id, value)


def test_every_emitted_payload_validates_against_the_ontology(report):
    """V1 §11 criterion 1, for events and relationships, under both scopes."""
    for view in report.views.values():
        assert view.totals["ontology_errors"] == 0, [
            e for case in view.cases for e in case.ontology_errors]


def test_every_emitted_relationship_predicate_resolves_the_way_the_validator_looks_it_up():
    """`registry.relationship`, not `registry.find`. The defect commit `1a96c22` fixed."""
    from ontology import load_ontology

    ontology = load_ontology()
    payload = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    seen = 0
    for view in payload["views"].values():
        for case in view["cases"]:
            for edge in case["emitted_relationships"] + case["gold_relationships"]:
                seen += 1
                assert ontology.registry.relationship(edge["relationship_id"]) is not None
    assert seen


def test_every_emitted_property_is_one_its_event_type_declares():
    """`allowed_properties` had no consumer; this is one of the two it now has."""
    from ontology import load_ontology

    ontology = load_ontology()
    payload = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    for view in payload["views"].values():
        for case in view["cases"]:
            for event in case["emitted_events"]:
                declared = set(ontology.registry.event_type(
                    event["event_type_id"]).allowed_properties or ())
                assert set(event["properties"]) <= declared, event


def test_every_unresolved_participant_is_flagged_and_named_as_a_placeholder():
    """The `UNNAMED_ENTITY` abstention's substance, checked against the payload."""
    payload = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    unresolved = [p for view in payload["views"].values() for case in view["cases"]
                  for event in case["emitted_events"] for p in event["participants"]
                  if not p["named"]]
    assert unresolved
    for participant in unresolved:
        assert "_unnamed_" in participant["entity_id"], participant


# -- the artifact says what it measures ---------------------------------------------------------------


def test_matched_over_emitted_is_never_called_precision():
    for path in (COMMITTED_MARKDOWN, COMMITTED_JSON,
                 BENCHMARK_PACKAGE / "event_runner.py",
                 BENCHMARK_PACKAGE / "event_evaluation.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer("precision", text, re.I):
            window = text[max(0, match.start() - _PRECISION_WINDOW):match.end()].lower()
            assert any(word in window for word in _NEGATIONS), (path.name, window)


def test_an_unmatched_emission_is_named_an_addition_and_never_a_false_positive():
    text = COMMITTED_MARKDOWN.read_text(encoding="utf-8").lower()
    assert "non-gold" in text
    assert "false positive" not in text or "never a false positive" in text


def test_every_issue_code_in_the_report_is_declared_by_the_lane(committed):
    for view in committed["views"].values():
        for case in view["cases"]:
            for code in case["issues_by_code"]:
                assert code in EVENT_ISSUE_CODES, code


def test_the_declared_structured_output_counts_match_the_store(report, store_path):
    """A conformant answer is a row. The one direction that can be checked, checked."""
    store = AnswerStore(store_path)
    declared = report.structured_output
    assert declared["conformant_first_attempt"] == len(store)
    assert declared["never_conformant"] == len(event_runner.UNUSABLE_ANSWERS)
    assert declared["distinct_requests"] == (
        declared["conformant_first_attempt"] + declared["never_conformant"])
    assert declared["provider_calls"] == declared["distinct_requests"] * len(
        event_runner.SCOPES)
    digests = {case.request_digest for view in report.views.values()
               for case in view.cases if case.request_digest}
    assert len(digests) == declared["distinct_requests"]


def test_the_report_states_the_routing_decision_and_its_rejected_options(committed):
    routing = committed["vocabulary"]["routing"]
    assert routing["chosen"]
    assert len(routing["rejected"]) >= 4
    for entry in routing["rejected"]:
        assert entry["option"] and entry["why_not"]
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    for entry in routing["rejected"]:
        assert entry["option"] in markdown


def test_the_routing_evidence_is_measured_rather_than_asserted(report):
    """The claim "no candidate scope reaches these event types" is a measurement, so measure it.

    If a future vocabulary change put one of them in scope, this number moves and the report
    says so rather than repeating a sentence that stopped being true.
    """
    assert report.comparison["gold_event_types_reached_by_any_scope"] == 0
    for view in report.views.values():
        for case in view.cases:
            assert case.gold_event_types_in_scope == ()
            # …and the scope was really asked: it offered concepts, just not these.
            assert case.scope_concept_ids


def test_both_scopes_were_run_and_agree_because_the_menu_is_not_a_scope(report):
    """The identity is computed from the run, not asserted from the design."""
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    assert report.comparison["cases_with_identical_requests"] == len(lexical.cases)
    assert report.comparison["cases_with_identical_payloads"] == len(lexical.cases)
    assert all(value == 0.0
               for value in report.comparison["score_delta_hybrid_minus_lexical"].values())
    # The scopes themselves are not identical — they offered different concept counts — so
    # the agreement above is about the lane and not about the scopes being the same object.
    assert any(len(case.scope_concept_ids)
               != len(hybrid.case(case.case_id).scope_concept_ids)
               for case in lexical.cases)


def test_the_benchmark_readme_does_not_restate_the_event_results():
    """The rule the other report sections live under, applied to the one this stage added.

    A number copied into a README is a number no test checks and nothing regenerates. The
    section therefore states no score at all and points at the report; the only figure it may
    carry is the port the generation server listens on.
    """
    from benchmarks.extraction.v1 import runner

    readme = (runner.PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("### Event and relationship lane"):
                     readme.index("## Review status")]
    # A port, and the "§4.0b" plan reference the temporal rule points at. Nothing else may be
    # a number, and in particular no score.
    allowed = {"8080", "4", "4.0"}
    stray = [n for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", section)
             if n not in allowed]
    assert stray == [], stray
    assert "reports/event_relationship_v1.md" in section
    assert "non-gold addition" in section


def test_extraction_names_nothing_in_this_directory():
    """The hard constraint, re-asserted from this side of the boundary."""
    for path in (REPO / "extraction").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) and body and isinstance(
                    body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                assert "benchmark" not in node.value.lower(), (path.name, node.value)


def test_no_case_id_or_passage_id_from_the_benchmark_appears_under_extraction():
    """Stronger than the substring rule: the actual identifiers, looked for by value."""
    identifiers: set[str] = set()
    for case in event_runner.load_event_cases():
        identifiers.add(case.case_id)
        identifiers.add(case.passage_id)
        identifiers.add(case.document_id)
    for path in (REPO / "extraction").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for identifier in identifiers:
            assert identifier not in text, (path.name, identifier)


def test_every_emitted_event_carries_a_unique_deterministic_id(report):
    """The identity the catalogs will be keyed on, present and distinct in the artifact.

    Added during review repair 2026-08-02. `event_id` had already collided once on this very
    corpus — two undated executive changes on one 8-K shared `(type, date, passage)` until
    participants entered the digest — and the report printed no id at all, so a recurrence
    would have been invisible in the durable evidence. A collision does not fail loudly
    downstream; it silently merges two events into one.

    Also asserts what the readable segment says: an event whose passage dates only its
    announcement reads `undated` there, never the announcement date. Promoting `announced_on`
    into an identity that answers "when did this happen" is the inference V1 §4.0b forbids,
    and an id is the one place it would be permanent.
    """
    from extraction.core.identifiers import event_id

    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    for scope in event_runner.SCOPES:
        emitted = [e for case in committed["views"][scope]["cases"]
                   for e in case["emitted_events"]]
        assert emitted, scope
        ids = [e["event_id"] for e in emitted]
        assert len(set(ids)) == len(ids), f"{scope}: colliding event ids {ids}"

        for entry in emitted:
            assert entry["event_id"] == event_id(
                entry["event_type_id"], entry["occurred_on"],
                entry["evidence"]["passage_id"],
                tuple((p["role"], p["entity_id"]) for p in entry["participants"])), entry
            segment = entry["event_id"].split(":")[2]
            if entry["occurred_on"]:
                assert segment == entry["occurred_on"]
            else:
                assert segment == "undated", entry["event_id"]
                if entry["announced_on"]:
                    assert entry["announced_on"] not in entry["event_id"], (
                        "the announcement date reached the occurrence segment")
