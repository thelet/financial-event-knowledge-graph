"""The committed narrative-lane report is a claim, and these tests are what make it one.

STAGE_11 §6 asks for eight. They are the same discipline the table-lane report tests apply,
plus the three prose adds: the report must be rebuildable from the committed answer store with
no server, a missing answer must raise rather than be scored as a silence, and no operational
statistic may reach the artifact.

Two of them are worth stating as what they defend against, because this repository has met
both failures already. `test_the_markdown_headline_is_the_json_headline` and
`test_per_observation_verdicts_agree_with_the_aggregate_scores` exist because the step 6
runner duplicated the verdict logic and printed 46 `WRONG` rows beside an accuracy of 1.000
with every test green. `test_the_committed_markdown_is_byte_for_byte_what_a_fresh_replay_renders`
exists because a committed report that nobody diffs is a screenshot.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from benchmarks.extraction.v1 import narrative_evaluation, narrative_runner
from extraction.stages.narrative import AnswerStore, MissingAnswerError
from extraction.stages.narrative.answer_store import ROW_FIELDS
from extraction.stages.narrative.public import ISSUE_CODES

REPO = Path(__file__).resolve().parents[2]
EXTRACTION_PACKAGE = REPO / "extraction"
BENCHMARK_PACKAGE = REPO / "benchmarks" / "extraction" / "v1"
COMMITTED_JSON = narrative_runner.REPORTS_DIR / f"{narrative_runner.REPORT_STEM}.json"
COMMITTED_MARKDOWN = narrative_runner.REPORTS_DIR / f"{narrative_runner.REPORT_STEM}.md"

# Fixed so a report built in a test never depends on the working tree's HEAD; the real commit
# is the one field allowed to vary, and §3 says the reproducibility check holds it still.
PINNED_COMMIT = "0" * 40

COMMIT_IN_JSON = re.compile(r'^(\s*"implementation_commit": ")[^"]*(",?)$', re.MULTILINE)
COMMIT_IN_MARKDOWN = re.compile(r"^\| implementation commit \| `[^`]*` \|$", re.MULTILINE)

# What may never appear in a byte-identical artifact. `max_output_tokens` is deliberately not
# here and is deliberately in the report: it is a configured budget that the request digest is
# taken over, so it is part of what identifies the run rather than a measurement of it. Every
# string below is a *measurement* — it differs between two identical requests.
FORBIDDEN_IN_JSON = (
    "prompt_tokens", "completion_tokens", "total_tokens", "latency", "elapsed_",
    "timestamp", "created_at", "duration_", "wall_clock", "generated_at", "_ms",
    "attempts", "raw_sha256",
)

# The window around the word "precision" that must carry a denial. STAGE_11 §2 and §4.0 both
# refuse to let `matched_over_emitted` be called precision; this is that refusal made checkable
# rather than trusted.
_NEGATIONS = ("not", "never", "refus", "cannot")
_PRECISION_WINDOW = 80


@pytest.fixture(scope="module")
def store_path() -> Path:
    if not narrative_runner.ANSWER_STORE.is_file():
        pytest.skip(f"no committed answer store at {narrative_runner.ANSWER_STORE}")
    return narrative_runner.ANSWER_STORE


@pytest.fixture(scope="module")
def report(store_path, repo_config):
    if not (repo_config.catalog_root / "passages.jsonl").is_file():
        pytest.skip("no normalized corpus")
    return narrative_runner.build_report(
        catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)


@pytest.fixture(scope="module")
def committed() -> dict:
    if not COMMITTED_JSON.is_file():
        pytest.skip("no committed narrative report")
    return json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))


# -- (1)(2) reproducibility -------------------------------------------------------------------


def test_replaying_twice_is_byte_identical(tmp_path, store_path, repo_config):
    """Two full replays, not one report rendered twice.

    Rendering the same object twice would only prove `json.dumps` is a function. The point is
    that the answer store, the lane, both scopes, the scoring and the ordering are deterministic
    end to end with no server involved.
    """
    def generate(directory: Path) -> tuple[bytes, bytes]:
        built = narrative_runner.build_report(
            catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)
        json_path, markdown_path = narrative_runner.write_reports(built, directory)
        return json_path.read_bytes(), markdown_path.read_bytes()

    first = generate(tmp_path / "first")
    second = generate(tmp_path / "second")
    assert first[0] == second[0]
    assert first[1] == second[1]


def test_the_committed_json_is_byte_for_byte_what_a_fresh_replay_renders(report, committed):
    """Bytes, not `json.loads`.

    Parsing both sides would compare the data and ignore the serialisation, so the mandated
    `indent=2, sort_keys=True, ensure_ascii=False` and the trailing newline would be
    unenforced. Only `implementation_commit` is blanked; every other byte must match.
    """
    fresh = COMMIT_IN_JSON.sub(
        r"\1<commit>\2", narrative_runner.render_json(report))
    on_disk = COMMIT_IN_JSON.sub(
        r"\1<commit>\2", COMMITTED_JSON.read_text(encoding="utf-8"))
    assert on_disk.encode("utf-8") == fresh.encode("utf-8")


def test_the_committed_markdown_is_byte_for_byte_what_a_fresh_replay_renders(report):
    """The Markdown is the artifact people actually read, and substring checks pass on prose
    that merely names the right things."""
    fresh = COMMIT_IN_MARKDOWN.sub(
        "| implementation commit | `<commit>` |", narrative_runner.render_markdown(report))
    on_disk = COMMIT_IN_MARKDOWN.sub(
        "| implementation commit | `<commit>` |",
        COMMITTED_MARKDOWN.read_text(encoding="utf-8"))
    assert on_disk.encode("utf-8") == fresh.encode("utf-8")


def test_the_report_names_the_store_it_was_replayed_from(report, store_path):
    """A report that cannot say which bytes produced it is not evidence that those bytes do."""
    import hashlib

    data = store_path.read_bytes()
    assert report.answer_store["answers"] == len(AnswerStore(store_path))
    assert report.answer_store["bytes"] == len(data)
    assert report.answer_store["sha256"] == hashlib.sha256(data).hexdigest()
    assert report.model["replay_only"] is True


# -- (3) no operational statistic in the artifact -----------------------------------------------


def test_no_duration_token_count_or_timestamp_appears_in_the_json(committed):
    """STAGE_09 §11.2's rule, checked over the serialised bytes rather than over the model.

    Over the text and not over the parsed keys on purpose: a token count smuggled into a
    free-text detail string would pass a key check and still make the artifact irreproducible.
    """
    text = COMMITTED_JSON.read_text(encoding="utf-8")
    offenders = [needle for needle in FORBIDDEN_IN_JSON if needle in text]
    assert offenders == [], offenders
    # The one token-shaped field that is allowed, named so the exclusion above is deliberate
    # rather than an oversight: it is a configured budget the request digest is taken over.
    assert committed["model"]["max_output_tokens"] == 4096


def test_the_answer_store_carries_no_operational_statistic(store_path):
    """The same rule one layer down.

    Against the literal field list rather than against `ROW_FIELDS`, so that adding a token
    count to `StoredAnswer` fails here instead of silently redefining what the rule permits.
    """
    permitted = {"request_sha256", "content_sha256", "model_id", "provider_model_id",
                 "temperature", "max_tokens", "prompt_version", "finish_reason", "raw_content"}
    assert set(ROW_FIELDS) == permitted
    for line in store_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        assert set(json.loads(line)) == permitted


# -- (4) every dimension computed once ----------------------------------------------------------


def test_the_runner_computes_no_score_of_its_own():
    """An operator grep, kept as the cheap half of the guard and **not** as the guard.

    It catches a runner that grew its own `hits / total`. It does not catch a runner that
    recomputes a score any other way: review 2026-08-02 replaced the rendered dimensions with
    `statistics.fmean(...) * 0.5`, printed `evidence_accuracy | 0.500 | 1.000 | +0.000` — a
    number arithmetically impossible from the verdicts beside it — and the suite stayed green.
    `test_the_markdown_reads_every_score_out_of_totals_and_derives_none` is the structural
    check; this one narrows the search when that fails.
    """
    tree = ast.parse((BENCHMARK_PACKAGE / "narrative_runner.py").read_text(encoding="utf-8"))
    divisions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv))
        # `pathlib.Path.__truediv__` is the same operator. A path join always divides by a
        # string; a ratio never does, so the exclusion cannot hide one.
        and not (isinstance(node.right, ast.JoinedStr)
                 or (isinstance(node.right, ast.Constant)
                     and isinstance(node.right.value, str)))
    ]
    assert divisions == [], [ast.unparse(node) for node in divisions]


# A value no real score can take, so its presence in the rendered table proves the renderer
# read `totals` and its absence proves it did not. Three decimals, because that is what the
# Markdown prints.
_SENTINEL_SCORE = 0.123456


def test_the_markdown_reads_every_score_out_of_totals_and_derives_none(report):
    """The structural form of "computed in exactly one place": substitute and look.

    Every score in `view.totals["scores"]` is replaced with one impossible value and the
    Markdown is re-rendered. A renderer that reads `totals` prints that value in every score
    position; a renderer that recomputes anything — from the matches, from `statistics.fmean`,
    from a second pass over the cases — prints something else, and the something else is
    exactly what this finds. Restored afterwards so the module-scoped report is unchanged.
    """
    saved = {name: dict(view.totals["scores"]) for name, view in report.views.items()}
    try:
        for view in report.views.values():
            for name in view.totals["scores"]:
                view.totals["scores"][name] = _SENTINEL_SCORE
        markdown = narrative_runner.render_markdown(report)
    finally:
        for name, view in report.views.items():
            view.totals["scores"].update(saved[name])

    printed = re.findall(r"\| \*{0,2}(\d\.\d{3})\*{0,2} \|", markdown)
    printed += re.findall(r"\| \*{0,2}(\d\.\d{3})\*{0,2} \| \*{0,2}\d\.\d{3}", markdown)
    assert printed, "no three-decimal number was found in the rendered Markdown at all"
    unexpected = sorted({value for value in printed if float(value) != round(
        _SENTINEL_SCORE, 3)})
    assert unexpected == [], (
        "a three-decimal number in the Markdown did not come from totals['scores']; the "
        "renderer is deriving it", unexpected)


def test_per_observation_verdicts_agree_with_the_aggregate_scores(report):
    """The two halves of the report must be the same measurement.

    Both come from `evaluation.compare`; this is what makes that structural fact observable.
    """
    for scope, view in report.views.items():
        matches = [m for case in view.cases for m in case.matched]
        matched_total = view.totals["matched_observations"]
        assert len(matches) == matched_total, scope

        for dimension in narrative_evaluation.SCORED_MATCH_DIMENSIONS:
            accuracy = view.totals["scores"][f"{dimension}_accuracy"]
            applicable = [m for m in matches if m.applies(dimension)]
            hits = sum(1 for m in applicable if getattr(m, f"{dimension}_ok"))
            assert len(applicable) == view.totals["score_denominators"][
                f"{dimension}_accuracy"], (scope, dimension)
            assert hits == round(accuracy * len(applicable)), (scope, dimension)

        gold_total = view.totals["gold_observations"]
        assert matched_total == round(
            view.totals["scores"]["metric_identity_accuracy"] * gold_total), scope

        expected = view.totals["expected_abstentions"]
        honoured = sum(c.honoured_abstentions for c in view.cases)
        assert honoured == round(
            view.totals["scores"]["abstention_honoured_rate"] * expected), scope

        # Every score's numerator and denominator are the ones its ratio was taken from, so no
        # ratio in the artifact can be a number nothing counted.
        for name, ratio in view.totals["scores"].items():
            hits = view.totals["score_numerators"][name]
            total = view.totals["score_denominators"][name]
            assert ratio == (1.0 if total == 0 else round(hits / total, 6)), (scope, name)


def test_the_markdown_headline_is_the_json_headline(report):
    """Every headline number in the Markdown is the JSON's, to the digit it prints.

    Read back out of the rendered table rather than asserted as a substring: a renderer that
    recomputed a score would produce a number that still *looks* like a score, and only a
    comparison against the scored object can tell the two apart.
    """
    markdown = narrative_runner.render_markdown(report)
    for name, key, _ in narrative_evaluation.DIMENSIONS:
        row = re.search(rf"^\| {re.escape(name)} \| [^|]+ \| \*\*([\d.]+)\*\* \| "
                        rf"\*\*([\d.]+)\*\* \|$", markdown, re.MULTILINE)
        assert row is not None, name
        for index, scope in enumerate(narrative_runner.SCOPES):
            printed = float(row.group(index + 1))
            scored = report.views[scope].totals["scores"][key]
            assert printed == round(scored, 3), (name, scope)


def _rows_under(markdown: str, heading: str) -> list[list[str]]:
    """The cells of every table row under a heading, up to the next heading."""
    start = markdown.index(heading)
    end = markdown.find("\n## ", start + len(heading))
    section = markdown[start:end if end != -1 else len(markdown)]
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines()
            if line.startswith("|") and not set(line) <= set("| -")]


def test_every_rendered_table_number_is_the_number_it_renders(report):
    """Four tables, checked cell by cell against the object each one renders.

    **Only the eight-row headline was checked before, and three tables were checked by
    nothing.** Review 2026-08-02 added `+0.4` to the lexical-versus-hybrid comparison — the
    table step 13 reads — and produced a row saying `metric_identity_accuracy | 0.926 | 0.579
    | +0.053` on one line while the headline said `**0.526** | **0.579**` on another, with the
    whole suite green. A number nothing compares to its source is a screenshot.
    """
    markdown = narrative_runner.render_markdown(report)
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]

    # (1) the headline, including the denominators it now prints.
    headline = {row[0]: row for row in _rows_under(markdown, "## Headline")}
    for name, key, _ in narrative_evaluation.DIMENSIONS:
        row = headline[name]
        assert f"({lexical.totals['score_denominators'][key]} lex / " in row[1], name
        assert f"{hybrid.totals['score_denominators'][key]} hyb)" in row[1], name
        assert float(row[2].strip("*")) == round(lexical.totals["scores"][key], 3), name
        assert float(row[3].strip("*")) == round(hybrid.totals["scores"][key], 3), name

    # (2) Counts — every row is a `totals` key printed verbatim.
    counts = _rows_under(markdown, "## Counts")[1:]
    assert len(counts) >= 20
    matched = 0
    for label, lex, hyb in counts:
        candidates = [key for key, value in lexical.totals.items()
                      if isinstance(value, int) and str(value) == lex
                      and str(hybrid.totals.get(key)) == hyb]
        assert candidates, (label, lex, hyb)
        matched += 1
    assert matched == len(counts)

    # (3) the failure-category census.
    for row in _rows_under(markdown, "## Failure classification")[1:]:
        category = row[0].strip("`")
        if category not in narrative_evaluation.FAILURE_CATEGORIES:
            continue
        assert int(row[1]) == lexical.totals["failures_by_category"][category], category
        assert int(row[2]) == hybrid.totals["failures_by_category"][category], category

    # (4) the lexical-versus-hybrid comparison, the table step 13 reads.
    seen = set()
    for row in _rows_under(markdown, "## Lexical versus hybrid"):
        key = row[0]
        if key not in lexical.totals["scores"]:
            continue
        seen.add(key)
        assert float(row[1]) == round(lexical.totals["scores"][key], 3), key
        assert float(row[2]) == round(hybrid.totals["scores"][key], 3), key
        assert float(row[3]) == round(
            report.comparison["score_delta_hybrid_minus_lexical"][key], 3), key
        assert row[4] == (f"{lexical.totals['score_denominators'][key]}/"
                          f"{hybrid.totals['score_denominators'][key]}"), key
    assert seen == set(lexical.totals["scores"])


# -- (5) the classifier is total and disjoint ----------------------------------------------------


def test_the_failure_classifier_is_total_and_disjoint(report):
    """Every classified disagreement gets exactly one of the ten categories, and the count is
    the count of disagreements the benchmark can adjudicate.

    Totality is checked as an identity rather than as "no failure is uncategorised": a
    classifier that silently dropped a miss would satisfy the weaker form.
    """
    for scope, view in report.views.items():
        for case in view.cases:
            wrong_matches = sum(1 for m in case.matched if not m.all_ok)
            broken_silences = sum(v.failure_count for v in case.abstentions)
            expected = len(case.missed) + wrong_matches + broken_silences
            assert len(case.failures) == expected, (scope, case.case_id)
            for failure in case.failures:
                assert failure.category in narrative_evaluation.FAILURE_CATEGORIES
                assert failure.source in (
                    narrative_evaluation.MISSED_GOLD,
                    narrative_evaluation.WRONG_MATCH,
                    narrative_evaluation.OVER_EMITTED)
        assert set(view.totals["failures_by_category"]) == set(
            narrative_evaluation.FAILURE_CATEGORIES), scope
        assert sum(view.totals["failures_by_category"].values()) == view.totals["failures"]


def test_the_decision_order_is_the_category_list_and_the_report_prints_it():
    """Two runs must classify one failure the same way, so the order is data, not a code path
    reading order, and the report states it.

    **Equality of tuples, not of sets** *(review 2026-08-02)*. Reversing `DECISION_ORDER`
    changed the artifact and left the set comparison green, so the order — which is the whole
    point of stating one — was unpinned.
    """
    assert narrative_evaluation.DECISION_ORDER == narrative_evaluation.FAILURE_CATEGORIES
    assert narrative_evaluation.DECISION_ORDER == (
        "schema_or_parse_failure", "ambiguity_collapsed", "evidence_ungrounded",
        "metric_misidentified", "metric_not_identified", "period_wrong", "subject_wrong",
        "unit_wrong", "scale_wrong", "value_wrong")
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    assert "Decision order: " in markdown
    printed = re.search(r"^Decision order: (.+)$", markdown, re.MULTILINE)
    assert printed is not None
    assert printed.group(1) == " → ".join(
        f"`{c}`" for c in narrative_evaluation.DECISION_ORDER)


def test_the_order_decides_which_category_a_multiply_wrong_match_gets():
    """Not "an order exists" but "this order is applied", driven through the classifier.

    A match failing both evidence and value must be `evidence_ungrounded`, because the order
    runs from "the answer could not be read" to "a field is wrong" and a value complaint about
    a sentence that is not in the passage names the wrong problem.
    """
    both_wrong = _match_record(evidence_ok=False, value_ok=False)
    failures = narrative_evaluation.classify_failures(
        gold=[], claims=[], issues=[], matched=[both_wrong], abstentions=[])
    assert [f.category for f in failures] == ["evidence_ungrounded"]

    only_value = _match_record(value_ok=False)
    failures = narrative_evaluation.classify_failures(
        gold=[], claims=[], issues=[], matched=[only_value], abstentions=[])
    assert [f.category for f in failures] == ["value_wrong"]


def _match_record(**overrides) -> narrative_evaluation.MatchRecord:
    fields = dict(
        metric_id="homes_sold", period_key="2023Q3", expected_value=1.0, emitted_value=1.0,
        expected_unit="homes", emitted_unit="homes", expected_scale="units",
        emitted_scale="units", expected_subject_entity_id="opendoor",
        emitted_subject_entity_id="opendoor", expected_population=None,
        emitted_population=None, expected_ambiguity_codes=(), emitted_ambiguity_codes=(),
        lane_claim_ambiguity_codes=(), raw_text="", value_ok=True, unit_ok=True, scale_ok=True,
        period_ok=True, subject_ok=True, evidence_ok=True, population_ok=True,
        ambiguity_codes_ok=True, population_contained_in_expected=False)
    fields.update(overrides)
    return narrative_evaluation.MatchRecord(**fields)


def _gold(**overrides) -> narrative_evaluation.GoldObservation:
    fields = dict(metric_id="homes_sold", period_key="2023Q3", value=1.0, unit="homes",
                  scale_applied="units", currency=None, subject_entity_id="opendoor",
                  population_definition_raw=None, ambiguity_codes=(), note=None)
    fields.update(overrides)
    return narrative_evaluation.GoldObservation(**fields)


def test_a_gold_metric_nothing_was_said_about_is_not_identified():
    """The classifier's fallback, pinned.

    Review 2026-08-02 changed it to return `VALUE_WRONG`, which reported a never-claimed gold
    metric as a wrong value (`value_wrong: 3, metric_not_identified: 0`) with the suite green.
    """
    failures = narrative_evaluation.classify_failures(
        gold=[_gold()], claims=[], issues=[], matched=[], abstentions=[])
    assert [f.category for f in failures] == ["metric_not_identified"]
    assert failures[0].source == narrative_evaluation.MISSED_GOLD


NAMED_FAILURES = (
    # (case_id, category, metric_id) — read off the run, and the reason each is what it is.
    # A census that only counts categories cannot tell a relabelling from a real move.
    ("letter-prose-multiple-metrics-q4-2021", "period_wrong", "adjusted_gross_profit"),
    ("letter-prose-run-together-kpi-row-q1-2025", "evidence_ungrounded",
     "housing_inventory_homes"),
    ("population-two-wordings-one-passage-q4-2023", "metric_misidentified",
     "acquisition_contracts"),
    ("negative-ambiguous-alias-bare-gross-profit", "schema_or_parse_failure", "—"),
    ("population-our-homes-q4-2021", "metric_misidentified",
     "pct_homes_on_market_gt_120_days"),
)


@pytest.mark.parametrize("case_id,category,metric_id", NAMED_FAILURES,
                         ids=[f"{c}-{k}" for c, k, _ in NAMED_FAILURES])
def test_a_named_failure_gets_its_named_category(report, case_id, category, metric_id):
    """Named failures, not a category histogram.

    Reversing `DECISION_ORDER` and changing the classifier's fallback both changed the
    artifact and left every test green, because nothing tied one failure to one category.
    """
    case = report.case(case_id)
    assert case is not None, case_id
    found = [f for f in case.failures if f.metric_id == metric_id]
    assert found, (case_id, metric_id, [(f.category, f.metric_id) for f in case.failures])
    assert category in {f.category for f in found}, (
        case_id, metric_id, [f.category for f in found])


# -- (6) a missing answer raises ------------------------------------------------------------------


def test_a_replay_with_a_missing_answer_raises_and_names_the_request(
        tmp_path, store_path, repo_config):
    """It never scores a silence as an abstention.

    The failure mode this prevents is not hypothetical: `ReplayingGenerationProvider` with an
    `inner` falls through to a server on a miss, so the same code that reports offline would
    quietly regenerate. `inner=None` is the configuration this report runs under, and a
    truncated store must fail loudly enough to name the request that went missing.
    """
    rows = [l for l in store_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) > 1
    truncated = tmp_path / "narrative_v1.jsonl"
    truncated.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(MissingAnswerError) as raised:
        narrative_runner.build_report(
            catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT,
            store_path=truncated)
    message = str(raised.value)
    assert "no stored answer for request" in message
    assert f"this store holds {len(rows) - 1}" in message


def test_an_absent_store_is_an_error_rather_than_an_empty_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        narrative_runner.replay_provider(tmp_path / "absent.jsonl")


# -- (7) the layout constraint ---------------------------------------------------------------------


def test_extraction_imports_nothing_from_the_benchmark():
    """The rule that decided where this runner lives, as an executable check.

    Duplicated from `test_typed_selection.py` on purpose: this stage adds two benchmark modules
    that import the narrative lane, and the direction of that dependency is the thing worth
    re-asserting where it was newly put at risk.
    """
    offenders = []
    for path in EXTRACTION_PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("benchmark" in name for name in names):
                offenders.append(f"{path.relative_to(REPO)}: {names}")
        if "benchmarks/extraction" in source:
            offenders.append(f"{path.relative_to(REPO)}: benchmark path literal")
    assert offenders == []


# -- (8) matched_over_emitted is never called precision ---------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(narrative_runner.REPORTS_DIR.glob("*.md"))
    + sorted(narrative_runner.REPORTS_DIR.glob("*.json"))
    + sorted(BENCHMARK_PACKAGE.glob("*.py")),
    ids=lambda p: p.name)
def test_no_artifact_calls_matched_over_emitted_precision(path: Path):
    """Every use of the word carries a denial within 80 characters.

    Checked over every committed report and every benchmark module rather than over the one
    report this stage adds. The reason the word is refused is that precision is not measurable
    against this benchmark at all: each case annotates a deliberate subset of its passage, so
    an unmatched emitted claim is unrequired, not a false positive.
    """
    text = " ".join(path.read_text(encoding="utf-8").split())
    for match in re.finditer("precision", text):
        window = text[max(0, match.start() - _PRECISION_WINDOW):
                      match.end() + _PRECISION_WINDOW].lower()
        assert any(negation in window for negation in _NEGATIONS), (
            path.name, text[max(0, match.start() - 120):match.end() + 60])


# -- the report describes the run it claims to describe ----------------------------------------------


def test_every_passage_id_in_the_report_resolves(committed, repo_config):
    from benchmarks.extraction.v1 import runner

    passages = runner.load_passages(repo_config.catalog_root)
    for view in committed["views"].values():
        for case in view["cases"]:
            assert case["passage_id"] in passages, case["case_id"]
            for emitted in case["emitted"]:
                assert emitted["evidence"]["passage_id"] in passages
                assert emitted["raw_text"] in passages[case["passage_id"]]["text"]


def test_every_issue_code_in_the_report_is_declared_by_the_lane(committed):
    codes = {
        code
        for view in committed["views"].values()
        for case in view["cases"]
        for code in case["issues_by_code"]
    }
    assert codes
    assert codes <= ISSUE_CODES


def test_the_declared_structured_output_counts_match_the_store(report, store_path):
    """Hand-declared because a request whose answer never conformed has no row to store, so the
    denominator cannot be recovered from the file. Held to the file in the one direction it
    can be: conformant answers are rows."""
    declared = report.structured_output
    assert declared["conformant_first_attempt"] == len(AnswerStore(store_path))
    assert declared["requests_issued"] == (
        declared["conformant_first_attempt"] + declared["never_conformant"])
    # Not a measurement of zero. The adapter retries transport failures and 5xx only; a schema
    # violation raises on the first answer and is never retried at temperature 0.
    assert declared["retries_possible"] is False
    assert declared["conformant_after_retry"] == 0


def test_the_benchmark_readme_does_not_restate_the_narrative_results():
    """The rule the hybrid section already lives under, applied to the section step 11 added.

    A number copied into a README is a number no test checks and nothing regenerates. The
    narrative section therefore states no score at all and points at the report; the only
    figures it may carry are the case counts that fix its scope and the port the generation
    server listens on.
    """
    from benchmarks.extraction.v1 import runner

    readme = (runner.PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("### Narrative lane"):readme.index("## Review status")]

    for verdict in ("hybrid becomes the default", "lexical stays the default", "founder gate"):
        assert verdict not in section, verdict
    # `14` and `13` fix which cases are scored — the same kind of statement the Scope table at
    # the top of this README makes — and `8080` is a port. `4.0`, `7.1` and `7.3` are plan
    # section numbers naming which policies became dimensions. Nothing else may be a number,
    # and in particular no score.
    allowed = {"14", "13", "8080", "11", "8a", "7", "6", "4", "2", "4.0", "7.1", "7.3"}
    stray = [n for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", section)
             if n not in allowed]
    assert stray == [], stray
    assert "reports/narrative_lane_v1.md" in section
    assert "unrequired" in section


# -- (A1) a declared failure may never shadow a stored answer -----------------------------------


def test_a_declared_unusable_answer_never_shadows_a_stored_one(store_path):
    """The disjointness the whole `UNUSABLE_ANSWERS` justification rests on, enforced.

    `_ReplayWithRecordedFailures.generate` consults the declared failures **before** the
    store, and nothing checked the two were disjoint. Review 2026-08-02 added one entry
    pointing at a request the store does answer: metric identity moved from 0.526/0.579 to
    0.316/0.368, emitted from 28/30 to 12/14, the four `DUPLICATE_OBSERVATION_CONFLICT` errors
    vanished, and the suite stayed green at 1,537.
    """
    from benchmarks.extraction.v1.narrative_runner import _ReplayWithRecordedFailures
    from extraction.stages.narrative import ReplayingGenerationProvider
    from extraction.stages.narrative.prompt import PROMPT_VERSION

    store = AnswerStore(store_path)
    answered = sorted(json.loads(line)["request_sha256"]
                      for line in store_path.read_text(encoding="utf-8").splitlines()
                      if line.strip())
    inner = ReplayingGenerationProvider(store, None, prompt_version=PROMPT_VERSION)

    with pytest.raises(ValueError) as raised:
        _ReplayWithRecordedFailures(
            inner, ({"request_sha256": answered[0], "case_id": "invented"},))
    assert answered[0] in str(raised.value)


def test_the_committed_declarations_are_disjoint_from_the_committed_store(store_path):
    declared = {entry["request_sha256"] for entry in narrative_runner.UNUSABLE_ANSWERS}
    stored = {json.loads(line)["request_sha256"]
              for line in store_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert declared & stored == set()


# -- (A5) no dimension is asserted in the artifact ------------------------------------------------


def test_evidence_resolution_is_read_for_every_emitted_claim_not_asserted(report, committed):
    """`"resolves": true` was a literal on every emitted claim, matched or not.

    It is a scored dimension. Eighteen of the twenty-eight claims it was printed on were
    unmatched, so nothing had computed it for them — the report was stating a verdict it had
    not reached *(review 2026-08-02)*.
    """
    from benchmarks.extraction.v1 import runner as table_runner

    passages = table_runner.load_passages(None)
    for scope, view in report.views.items():
        for case in view.cases:
            assert len(case.claim_evidence_resolves) == len(case.claims), case.case_id
            for claim, resolves in zip(case.claims, case.claim_evidence_resolves):
                truth = (claim.passage_id in passages
                         and claim.raw_text in passages[claim.passage_id]["text"])
                assert resolves is truth, (scope, case.case_id, claim.metric_id)
            # And where a claim is also a matched pair, the two readings are one reading.
            by_key = {(m.metric_id, m.period_key): m for m in case.matched}
            for claim, resolves in zip(case.claims, case.claim_evidence_resolves):
                match = by_key.get((claim.metric_id, claim.period.key))
                if match is not None and match.emitted_value == claim.value:
                    assert match.evidence_ok is resolves, (scope, case.case_id)

    printed = [emitted["evidence"]["resolves"]
               for view in committed["views"].values()
               for case in view["cases"] for emitted in case["emitted"]]
    assert printed, "no emitted claim in the committed report"


def _case_score(**overrides):
    """One scored case, built through `score_case` so it is the object the renderer sees."""
    fields = dict(
        case_id="synthetic", scope="lexical", passage_id="p#1", gold=(),
        expected_abstentions=(), claims=[], issues=[], scope_concept_ids=(),
        request_digest=None,
        validation={"warnings": (), "errors": (), "assembly_rejections": ()},
        resolves_evidence=lambda claim: False, reporting_keys=frozenset(),
        sentence_period_keys=lambda text: frozenset(),
        ambiguity_codes_for=lambda metric_id: ())
    fields.update(overrides)
    return narrative_evaluation.score_case(**fields)


def test_a_claim_whose_evidence_does_not_resolve_renders_as_not_resolving():
    """Driven with a claim that does **not** resolve, because every claim in this run does.

    A literal `True` is indistinguishable from a read value while every value is True, and
    that is precisely the state the artifact shipped in: reinstating the literal left the whole
    suite green. The renderer is therefore given a case where the answer is False.
    """
    from extraction.core.models import LaneClaim, PeriodRef

    claim = LaneClaim(
        metric_id="homes_sold", value=1.0, unit="homes",
        period=PeriodRef(period_start="2023-07-01", period_end="2023-09-30"),
        subject_entity_id="opendoor", subject_type="public_company",
        source_lane="normalized_narrative", assertion_type="reported",
        passage_id="p#1", document_id="p", raw_text="not in any passage")
    case = _case_score(claims=[claim])

    assert case.claim_evidence_resolves == (False,)
    rendered = narrative_runner._case_json(case)
    assert rendered["emitted"][0]["evidence"]["resolves"] is False, (
        "the renderer is asserting a dimension rather than reading it")

    resolving = _case_score(claims=[claim], resolves_evidence=lambda c: True)
    assert narrative_runner._case_json(resolving)["emitted"][0]["evidence"]["resolves"] is True


def test_prompt_identity_is_read_from_the_digest_and_not_from_the_concepts():
    """Two cases with the same concepts and different prompts must not count as one run.

    The committed run cannot distinguish the two rules — the scopes that agree on concepts also
    agree on digests there — so the rule is driven with a case where they disagree. Without it,
    reinstating the concept-set inference left the suite green.
    """
    def view(name, digest):
        case = _case_score(case_id="synthetic", scope=name,
                           scope_concept_ids=("homes_sold",), request_digest=digest)
        return narrative_runner.ScopeView(
            name=name, cases=[case], totals=narrative_evaluation.totals([case]))

    same = narrative_runner._comparison(
        {"lexical": view("lexical", "aaa"), "hybrid": view("hybrid", "aaa")})
    assert same["cases_with_identical_scope"] == 1

    different = narrative_runner._comparison(
        {"lexical": view("lexical", "aaa"), "hybrid": view("hybrid", "bbb")})
    assert different["cases_with_identical_scope"] == 0, (
        "identical concept lists were counted as one request; the digest is what settles it")
    assert different["cases_without_a_request"] == 0


# -- (B1) the two §7 policies are scored ------------------------------------------------------------


def test_population_wording_and_ambiguity_codes_are_scored_dimensions():
    assert "population" in narrative_evaluation.PROSE_MATCH_DIMENSIONS
    assert "ambiguity_codes" in narrative_evaluation.PROSE_MATCH_DIMENSIONS
    keys = {key for _, key, _ in narrative_evaluation.DIMENSIONS}
    assert {"population_accuracy", "ambiguity_codes_accuracy"} <= keys


def test_a_disagreeing_population_wording_is_not_a_clean_match():
    """§7.1 says verbatim, so a shorter denominator wording is a different one.

    The defect this closes: gold gives `5% of our homes were listed on the market for more
    than 120 days`, the claim carries `our homes`, neither was a scored dimension, and
    `all_ok` was True. All seven matched `pct_homes_on_market_gt_120_days` pairs in the run
    disagreed this way and all seven scored clean.
    """
    match = _match_record(
        expected_population="5% of our homes were listed on the market for more than 120 days",
        emitted_population="our homes", population_ok=False,
        population_contained_in_expected=True)
    assert match.applies("population")
    assert match.all_ok is False
    assert "population" in match.failed_dimensions


def test_a_missing_declared_ambiguity_code_is_not_a_clean_match():
    match = _match_record(expected_ambiguity_codes=("pct_120_days_denominator",),
                          emitted_ambiguity_codes=(), ambiguity_codes_ok=False)
    assert match.applies("ambiguity_codes")
    assert match.all_ok is False


def test_a_metric_the_case_annotates_no_policy_for_is_not_asked(report):
    """The denominators are gold-driven, so a metric with nothing declared is not scored as
    correct for having nothing to get wrong."""
    plain = _match_record()
    assert plain.applies("population") is False
    assert plain.applies("ambiguity_codes") is False
    assert plain.all_ok is True

    for view in report.views.values():
        for case in view.cases:
            for match in case.matched:
                assert match.applies("population") == (
                    match.expected_population is not None)
                assert match.applies("ambiguity_codes") == bool(match.expected_ambiguity_codes)


def test_the_run_scores_the_population_dimension_over_a_real_denominator(report):
    """It has to be asked of something, or the dimension is decoration."""
    for scope, view in report.views.items():
        assert view.totals["score_denominators"]["population_accuracy"] >= 3, scope
        assert view.totals["score_denominators"]["ambiguity_codes_accuracy"] >= 2, scope


def test_the_lane_claim_carries_no_ambiguity_code_and_the_report_says_so(report):
    """§7.3 lives in `assemble`, and the report scores pre-assembly `LaneClaim`s.

    Both readings are recorded so the report cannot show the assembled one and be read as
    showing what the lane emitted.
    """
    matched = [m for view in report.views.values() for c in view.cases for m in c.matched]
    assert matched
    assert all(m.lane_claim_ambiguity_codes == () for m in matched)
    assert any(m.emitted_ambiguity_codes for m in matched)


# -- (B2) the recommendation is per claim, not per ratio ---------------------------------------------


def test_the_recommendation_never_calls_an_added_claim_correct_unless_it_is(report):
    """A ratio rises when its denominator moves; that is not a dimension improving.

    Adding one correct match took `value_accuracy` from 9/10 to 10/11 and the old comparison
    listed it under "improved by hybrid" though hybrid corrected no value.
    """
    comparison = report.comparison
    added = comparison["claims_added_by_one_scope"]
    for entry in added:
        case = report.case(entry["case_id"], entry["scope"])
        match = next((m for m in case.matched
                      if f"{m.metric_id}@{m.period_key}" == entry["claim"]), None)
        assert entry["clean_on_every_scored_dimension"] == bool(match and match.all_ok)
        assert entry["failed_dimensions"] == list(match.failed_dimensions) if match else True

    clean = [e for e in added if e["is_gold"] and e["clean_on_every_scored_dimension"]]
    assert comparison["recommendation"]["gold_claims_added_clean"] == len(clean)


def test_the_verdict_cannot_claim_correctness_no_added_claim_has():
    """`_recommendation` driven directly, because the run has only one shape to offer.

    Reinstating the ratio-delta branch was caught only by the byte-identical comparison, which
    says the artifact changed and not that it became wrong. This says which.
    """
    added_dirty = [{"scope": "hybrid", "case_id": "c", "claim": "m@p", "is_gold": True,
                    "clean_on_every_scored_dimension": False,
                    "failed_dimensions": ["population"]}]
    verdict = narrative_runner._recommendation(
        differences=["c"], added=added_dirty, regressions=[])
    assert verdict["verdict"] == "hybrid reaches more and is not clean on what it reaches"
    assert "correct gold claim" not in verdict["summary"]
    assert "no dimension fell" not in verdict["summary"]
    assert verdict["gold_claims_added_clean"] == 0
    assert verdict["gold_claims_added_not_clean"] == 1
    assert "population" in verdict["summary"]

    added_clean = [dict(added_dirty[0], clean_on_every_scored_dimension=True,
                        failed_dimensions=[])]
    clean_verdict = narrative_runner._recommendation(
        differences=["c"], added=added_clean, regressions=[])
    assert clean_verdict["verdict"] == "hybrid, on narrow evidence"

    mixed = narrative_runner._recommendation(
        differences=["c"], added=added_clean,
        regressions=[{"case_id": "c", "claim": "m@p", "no_longer_clean_under": "hybrid"}])
    assert mixed["verdict"] == "mixed"


def test_the_ratio_movement_keys_are_not_named_as_improvements(report):
    """The keys themselves are the fix: `scores_improved_by_hybrid` read as a claim about a
    dimension and was a claim about a ratio."""
    assert "scores_improved_by_hybrid" not in report.comparison
    assert "scores_degraded_by_hybrid" not in report.comparison
    assert "score_ratios_higher_under_hybrid" in report.comparison
    assert "score_ratios_lower_under_hybrid" in report.comparison


# -- (B3) the abstention readings are three, and named for what they measure --------------------------


def test_the_abstention_rate_is_not_called_an_ambiguity_rate(report):
    """`reason_stated = bool(issues)` is any issue anywhere, over fifteen expectations of
    which one names ambiguity candidates. The name said otherwise."""
    assert "ambiguity_accuracy" not in report.views["lexical"].totals["scores"]
    for key in ("abstention_honoured_rate", "abstention_code_agreement",
                "ambiguity_preserved_rate"):
        assert key in report.views["lexical"].totals["scores"], key
    assert not hasattr(
        report.views["lexical"].cases[0].abstentions[0]
        if report.views["lexical"].cases[0].abstentions else object(), "reason_stated")


def test_ambiguity_proper_is_scored_over_the_expectations_that_declare_candidates(report):
    """And the denominator is 1, which the report states rather than hides."""
    for scope, view in report.views.items():
        declaring = [v for c in view.cases for v in c.ambiguity_expectations]
        assert view.totals["score_denominators"]["ambiguity_preserved_rate"] == len(declaring)
        assert view.totals["score_denominators"]["abstention_honoured_rate"] == view.totals[
            "expected_abstentions"], scope
        assert len(declaring) == 1, scope
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    assert "(1 lex / 1 hyb)" in markdown


def test_the_code_agreement_rate_is_reported_separately(report):
    for scope, view in report.views.items():
        agreeing = sum(1 for c in view.cases for v in c.abstentions if v.code_recorded)
        assert view.totals["code_agreeing_abstentions"] == agreeing, scope
        assert view.totals["score_numerators"]["abstention_code_agreement"] == agreeing


# -- (B4) the matcher's choice is explicit ------------------------------------------------------------


def test_the_matcher_keeps_the_first_claim_in_emission_order_and_records_the_collision():
    """Explicit and gold-blind. A dict comprehension kept the last one silently, and on
    `letter-prose-multiple-metrics-q4-2021` that displaced the $152 million figure equal to
    gold with the $525 million one."""
    from benchmarks.extraction.v1.narrative_evaluation import _index_by_key

    class Claim:
        def __init__(self, value):
            self.metric_id = "contribution_profit"
            self.value = value
            self.period = type("P", (), {"key": "2021Q4"})()

    first, second = Claim(152.0), Claim(525.0)
    indexed, collided = _index_by_key([first, second])
    assert indexed[("contribution_profit", "2021Q4")] is first
    assert collided == ("contribution_profit@2021Q4",)

    indexed, collided = _index_by_key([second, first])
    assert indexed[("contribution_profit", "2021Q4")] is second, (
        "the rule is emission order, not the value that agrees with gold")


def test_every_collision_is_counted_and_disclosed(report):
    for scope, view in report.views.items():
        assert view.totals["duplicate_claim_keys"] == sum(
            len(c.duplicate_claim_keys) for c in view.cases), scope
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    assert "keeps the **first in emission order**" in markdown
    assert "`value_accuracy` must be read beside that number" in markdown


# -- (B5) the double counting is stated wherever it reaches a denominator -------------------------------


def test_two_cases_annotate_one_passage_and_both_forms_of_the_count_are_reported(report):
    for scope, view in report.views.items():
        passages = [c.passage_id for c in view.cases]
        assert len(set(passages)) < len(passages), scope
        assert view.totals["distinct_passages"] == len(set(passages)), scope
        assert view.totals["emitted_observations_distinct"] < view.totals[
            "emitted_observations"], scope
        assert "matched_over_distinct_emitted" in view.totals["scores"], scope
        for key in ("ontology_warnings", "ontology_errors", "assembly_rejections"):
            assert f"{key}_distinct" in view.totals, key


# -- (B6) prompt identity, not concept identity ---------------------------------------------------------


def test_scope_identity_is_the_request_digest(report):
    """`request_identity` was already imported and the weaker inference was used anyway.

    The strong claim holds: 13 of 14 requests are identical and
    `letter-prose-inventory-and-120d-q2-2022` is the sole difference.
    """
    lexical, hybrid = report.views["lexical"], report.views["hybrid"]
    identical = [c.case_id for c in lexical.cases
                 if c.request_digest == hybrid.case(c.case_id).request_digest]
    differing = [c.case_id for c in lexical.cases
                 if c.request_digest != hybrid.case(c.case_id).request_digest]
    assert report.comparison["cases_with_identical_scope"] == len(identical) == 13
    assert differing == ["letter-prose-inventory-and-120d-q2-2022"]
    assert report.comparison["cases_without_a_request"] == 1

    # And the digest is the one the store is keyed on, not a second hash of the same thing.
    stored = {json.loads(line)["request_sha256"]
              for line in narrative_runner.ANSWER_STORE.read_text(
                  encoding="utf-8").splitlines() if line.strip()}
    declared = {e["request_sha256"] for e in narrative_runner.UNUSABLE_ANSWERS}
    issued = {c.request_digest for view in report.views.values() for c in view.cases
              if c.request_digest is not None}
    assert issued <= stored | declared


def test_the_two_scopes_are_scored_over_the_same_cases(report):
    lexical = [c.case_id for c in report.views["lexical"].cases]
    hybrid = [c.case_id for c in report.views["hybrid"].cases]
    assert lexical == hybrid
    assert len(lexical) == 14
    assert report.comparison["cases"] == 14
