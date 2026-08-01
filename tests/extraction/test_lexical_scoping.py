"""The lexical ontology candidate scope, on the real corpus.

The scope's whole job is to *not lose* things: every reason code it can produce is protected,
and a later ranking stage may add candidates and never remove one. That makes the interesting
tests negative ones — an ambiguous surface that still contributes all four of its concepts, a
`distinct_from` sibling that is still there when nothing in the passage named it — because a
scope that quietly narrowed would pass every check that only asked "is the right answer in
here".

The committed report is a claim, and the last three tests are what make it one, on the same
terms STAGE_06 set for the table lane: regeneration is byte-identical, the committed JSON and
Markdown match a fresh run byte for byte, and the declared misses are exactly the misses.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from benchmarks.extraction.v1 import runner, scope_runner
from extraction.contracts import OntologyCandidateScope
from extraction.core.assembly import deferred_metric_ids
from extraction.core.concept_resolution import CANONICAL_LABEL, resolve_label
from extraction.stages.scoping import (
    AMBIGUOUS_ALIAS,
    CONFUSION_SIBLING,
    EXACT_ALIAS,
    KNOWN_INSTANCE,
    NORMALIZED_ALIAS,
    PROTECTED_REASONS,
    SCOPE_REASONS,
    STABLE_CORE,
    STABLE_CORE_CONCEPTS,
    TABLE_LABEL,
    LexicalOntologyCandidateScope,
)
from extraction.stages.select import AliasIndex
from extraction.stages.tables import DeterministicTableClaimLane
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
EXTRACTION_PACKAGE = REPO / "extraction"
COMMITTED_JSON = runner.REPORTS_DIR / f"{scope_runner.REPORT_STEM}.json"
COMMITTED_MARKDOWN = runner.REPORTS_DIR / f"{scope_runner.REPORT_STEM}.md"

KPI_Q1_2025 = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p14"
SCALE_Q1_2025 = "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p13"
# An 8-K narrative paragraph that names no metric at all: 731 characters of transaction
# recital. Chosen because a passage with one incidental metric mention would not distinguish
# "the stable core is unconditional" from "the stable core rode in on something else".
NO_METRIC_PASSAGE = "norm:0001801169:0001104659-20-054449:tm2017926d1_8k.htm#p9"

PINNED_COMMIT = "0" * 40

COMMIT_IN_JSON = re.compile(r'^(\s*"implementation_commit": ")[^"]*(",?)$', re.MULTILINE)
COMMIT_IN_MARKDOWN = re.compile(r"^\| implementation commit \| `[^`]*` \|$", re.MULTILINE)


def _blank_commit_in_json(text: str) -> str:
    return COMMIT_IN_JSON.sub(r"\1<commit>\2", text)


def _blank_commit_in_markdown(text: str) -> str:
    return COMMIT_IN_MARKDOWN.sub("| implementation commit | `<commit>` |", text)


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def alias_index(ontology):
    return AliasIndex.from_ontology(ontology)


@pytest.fixture(scope="module")
def scope(ontology, alias_index):
    return LexicalOntologyCandidateScope(ontology, alias_index)


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    return runner.load_passages(repo_config.catalog_root)


@pytest.fixture(scope="module")
def report(passages, ontology, repo_config):
    return scope_runner.run_scope(
        scope_runner.load_scope_cases(), passages=passages, ontology=ontology,
        catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)


# -- §7.1 every reason code is real ------------------------------------------------------------


def test_every_reason_code_is_produced_by_a_real_corpus_passage(scope, passages):
    """Eight declared codes, eight demonstrations, from filings rather than fixtures.

    A reason code no passage produces is either dead or broken, and the two are
    indistinguishable from the declaration alone. `normalized_alias` is the one that would
    rot silently: it exists only because filings set curly quotes where the ontology
    transcribes straight ones, and it would go to zero the moment the fold regressed without
    a single other assertion in this suite noticing.

    Narrowed from `SCOPE_REASONS` to `PROTECTED_REASONS` when stage 9 added
    `semantic_neighbour`: the set this scope is answerable for is the eight it can produce,
    and asking a lexical scope to demonstrate a semantic reason would be asking it to fail.
    `test_hybrid_scoping.py` demonstrates the ninth. The second assertion below is the one
    that keeps this strict — the lexical scope may produce *nothing else*.
    """
    produced: dict[str, str] = {}
    for case in scope_runner.load_scope_cases():
        result = scope.scope_for(passages[case.passage_id]["text"])
        for reason, count in result.counts_by_reason().items():
            if count and reason not in produced:
                produced[reason] = case.case_id

    missing = sorted(PROTECTED_REASONS - set(produced))
    assert missing == [], f"no corpus case produced {missing}"
    assert set(produced) == set(PROTECTED_REASONS)


def test_every_candidate_is_protected(scope, passages):
    """The lexical scope produces protected reasons only, and that is the point.

    Stage 9 may add candidates and reorder them; `extraction.contracts.EmbeddingProvider`
    says it may never remove one of these. The rule is only enforceable if the set it applies
    to is the whole set, so this asserts it rather than trusting the docstring.
    """
    for case in scope_runner.load_scope_cases():
        result = scope.scope_for(passages[case.passage_id]["text"])
        assert result.protected_ids == result.concept_ids, case.case_id
        for candidate in result.concepts:
            assert set(candidate.reasons) <= PROTECTED_REASONS, candidate.concept_id


# -- §7.2 an ambiguous surface contributes all of its concepts ---------------------------------


@pytest.mark.parametrize("surface", ["gross profit", "gross margin", "homes", "contribution"])
def test_a_declared_ambiguous_surface_contributes_all_its_concepts(
    scope, ontology, alias_index, surface
):
    """All of them, never a best one.

    Picking one for a bare "homes" is what merges `homes_purchased` into
    `homes_under_contract`, which the ontology research names as the most damaging failure
    available to this vocabulary. The expected set is asked of the registry rather than
    written down here, so an `aliases.yaml` edit changes the test without touching it.
    """
    assert ontology.registry.is_ambiguous(surface)
    declared = {c.concept_id for c in ontology.registry.resolve_alias(surface)}
    assert len(declared) > 1

    result = scope.scope_for(f"The filing reports {surface} for the quarter.")
    assert declared <= set(result.concept_ids), declared - set(result.concept_ids)
    for concept_id in declared:
        assert AMBIGUOUS_ALIAS in result.reasons_for(concept_id), concept_id


def test_the_resolvers_single_answer_never_narrows_the_scope(scope, alias_index):
    """`Gross Margin` resolves to one metric and scopes to both. Both are correct.

    The shared resolver reads a whole label whose entire contents are a metric's canonical
    name as that metric — right for a table cell, and the reason every KPI table's
    `Gross Margin` row yields a claim at all. In prose the qualifier "Adjusted" may sit
    anywhere in the sentence, so the scope keeps the adjusted concept too. A scope that
    deferred to the resolver here would delete the lane's only chance to notice.
    """
    resolution = resolve_label(alias_index, "Gross Margin")
    assert resolution.concept_ids == ("gaap_gross_margin",)
    assert resolution.basis == CANONICAL_LABEL

    result = scope.scope_for("Gross Margin")
    assert "gaap_gross_margin" in result
    assert "adjusted_gross_margin" in result
    assert CANONICAL_LABEL in result.reasons_for("gaap_gross_margin")
    assert AMBIGUOUS_ALIAS in result.reasons_for("adjusted_gross_margin")


# -- §7.3 confusion siblings -------------------------------------------------------------------


def test_homes_sold_pulls_homes_purchased_in_as_a_sibling(scope):
    """`Resale Closes` names `homes_sold` and nothing else, and the sibling still arrives.

    That alias is used rather than "homes sold" on purpose: "homes sold" contains the
    declared-ambiguous "homes", which would put `homes_purchased` in scope for a different
    reason and leave the sibling expansion untested. Here the only route is the ontology's
    `distinct_from` declaration, and the reason recorded proves it was the route taken.
    """
    result = scope.scope_for("Resale Closes were 2,946 in the quarter.")

    assert EXACT_ALIAS in result.reasons_for("homes_sold")
    assert result.reasons_for("homes_purchased") == (CONFUSION_SIBLING,)
    assert ("homes_sold", "homes_purchased") in {
        (e.concept_id, e.sibling_id) for e in result.expansions}


# -- §7.4 the stable core ----------------------------------------------------------------------


def test_stable_core_is_present_for_a_passage_that_mentions_no_metric(scope, passages):
    result = scope.scope_for(passages[NO_METRIC_PASSAGE]["text"])

    assert result.concept_ids == STABLE_CORE_CONCEPTS
    for concept_id in STABLE_CORE_CONCEPTS:
        assert result.reasons_for(concept_id) == (STABLE_CORE,)


# -- §7.5 the typography fold ------------------------------------------------------------------


def test_a_curly_quoted_120_day_label_scopes_the_metric(scope):
    """The row label as filings actually set it, against the ontology as it transcribes it.

    The vocabulary spells this metric with straight quotes and every KPI table in the corpus
    prints curly ones, so without the fold it resolves in no table at all. `normalized_alias`
    is asserted rather than mere presence: presence alone would still pass if the metric
    arrived through some other surface.
    """
    label = ("| Percentage of homes “on the market” for greater than 120 days "
             "(at period end) | 27% |")
    result = scope.scope_for(label)

    assert "pct_homes_on_market_gt_120_days" in result
    reasons = result.reasons_for("pct_homes_on_market_gt_120_days")
    assert NORMALIZED_ALIAS in reasons
    assert TABLE_LABEL in reasons

    # Straight quotes must not regress while the curly ones are being fixed.
    straight = scope.scope_for(label.replace("“", '"').replace("”", '"'))
    assert "pct_homes_on_market_gt_120_days" in straight


# -- §7.6 determinism --------------------------------------------------------------------------


def test_scope_for_is_deterministic(scope, passages):
    """Same text, same order, twice — over a real passage, not a three-word fixture.

    Order matters as much as membership: the report serialises the candidate list, and a set
    iteration leaking into it would make a committed artifact differ between two runs of
    identical code.
    """
    text = passages[KPI_Q1_2025]["text"]
    first = scope.scope_for(text)
    second = scope.scope_for(text)

    assert first.concept_ids == second.concept_ids
    assert [c.reasons for c in first.concepts] == [c.reasons for c in second.concepts]
    assert [c.surfaces for c in first.concepts] == [c.surfaces for c in second.concepts]
    assert first.expansions == second.expansions
    assert list(first.concept_ids) == sorted(first.concept_ids)


# -- §7.7 the protocol -------------------------------------------------------------------------


def test_the_scope_satisfies_the_candidate_scope_protocol(scope, passages):
    """Driven through the protocol, not asserted against it.

    `isinstance` against a runtime-checkable protocol proves the attribute names exist. What
    matters is that a caller holding only the contract gets a usable answer, so this binds the
    scope to a `OntologyCandidateScope`-typed name and uses it through that name alone.
    """
    contract: OntologyCandidateScope = scope

    assert contract.name == "lexical"
    candidates = contract.candidates_for(passages[KPI_Q1_2025]["text"])
    assert isinstance(candidates, tuple)
    assert candidates
    assert all(isinstance(c, str) for c in candidates)
    assert list(candidates) == sorted(candidates)
    # The protocol's answer and the richer one are the same set, by construction.
    assert candidates == scope.scope_for(passages[KPI_Q1_2025]["text"]).concept_ids
    assert contract.candidates_for("") == STABLE_CORE_CONCEPTS


# -- §7.8 the runtime never sees the benchmark -------------------------------------------------


def test_nothing_under_extraction_imports_the_benchmark():
    """A scope that read gold annotations would be measuring itself against its own output.

    The same rule the selection stage already carries, re-asserted here because this stage
    adds a package under `extraction/` and a report generator that is deliberately not in it.
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


def test_core_does_not_import_a_stage():
    """The direction that made `concept_resolution` land in `core/` rather than beside a lane.

    Both the table lane and the scope call it. If it imported either stage the dependency
    would be a cycle in waiting, and the next reader would put the third copy back.
    """
    offenders = []
    for path in (EXTRACTION_PACKAGE / "core").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("stages" in name for name in names):
                offenders.append(f"{path.relative_to(REPO)}: {names}")
        # A relative import of `..stages` carries no module name on the node.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.module:
                assert not node.module.startswith("stages"), path.name
    assert offenders == []


# -- §7.9 the table lane is unchanged by the extraction ----------------------------------------


def test_the_table_lane_is_unchanged_by_the_resolver_extraction(ontology, alias_index,
                                                                passages):
    """Two claims from the Q1 2025 KPI table, chosen for what they each depend on.

    `homes_sold` is the plain path — an exact alias, a count, and the scale exception that
    stops "(in thousands)" turning 2,946 homes into 2,946,000. `gaap_gross_margin` is the
    path the extraction could actually have broken: its row label *is* the string
    `aliases.yaml` declares ambiguous across the GAAP and adjusted concepts, so it emits a
    claim only because the canonical-label precedence outranks that ambiguity. If the
    precedence moved to `core.concept_resolution` incorrectly, this row is the first to go
    silent, and the benchmark's 46 matched observations would drop without any structural
    test failing.
    """
    lane = DeterministicTableClaimLane(
        ontology, alias_index, deferred_metric_ids(ontology))
    row = passages[KPI_Q1_2025]
    extraction = lane.extract_table(
        row["text"], passage_id=KPI_Q1_2025, table_id=row["table_id"],
        block_ids=tuple(row["block_ids"]),
        preceding_text=passages[SCALE_Q1_2025]["text"],
        preceding_passage_id=SCALE_Q1_2025)

    by_key = {(c.metric_id, c.period.key): c for c in extraction.claims}

    homes_sold = by_key[("homes_sold", "2025Q1")]
    assert homes_sold.value == 2946.0
    assert homes_sold.unit == "homes"
    assert (homes_sold.scale.scale if homes_sold.scale else "units") == "units"
    assert homes_sold.row_label == "Homes sold"

    gross_margin = by_key[("gaap_gross_margin", "2025Q1")]
    assert gross_margin.value == 8.6
    assert gross_margin.unit == "percent"
    assert gross_margin.row_label == "Gross Margin"

    # And the resolver, asked directly, gives the lane the same answer it acted on.
    assert resolve_label(alias_index, "Gross Margin").concept_ids == ("gaap_gross_margin",)


def test_the_resolver_returns_what_the_lane_scopes_for_every_row_label(
    scope, alias_index, passages
):
    """STAGE_08 §2's requirement, as an executable rule rather than a promise.

    Any concept the shared resolver returns for a row label must be reachable in that
    passage's scope. It holds by construction — the scope resolves each row label through the
    same function — and that is exactly why it is worth pinning: the construction is what a
    future refactor would remove.
    """
    from extraction.stages.select.alias_evidence import row_labels

    for case in scope_runner.load_scope_cases():
        text = passages[case.passage_id]["text"]
        result = scope.scope_for(text)
        for label in row_labels(text):
            for concept_id in resolve_label(alias_index, label).concept_ids:
                assert concept_id in result, (case.case_id, label, concept_id)


# -- §7.10 the committed report -----------------------------------------------------------------


def test_regenerating_the_scope_report_twice_is_byte_identical(
    tmp_path, passages, ontology, repo_config
):
    """Two full generations, not one report rendered twice.

    Rendering the same object twice would only prove `json.dumps` is a function. The point is
    that the scope, the scoring and the ordering are deterministic end to end.
    """
    def generate(directory: Path) -> tuple[bytes, bytes]:
        built = scope_runner.run_scope(
            scope_runner.load_scope_cases(), passages=passages, ontology=ontology,
            catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)
        json_path, markdown_path = scope_runner.write_reports(built, directory)
        return json_path.read_bytes(), markdown_path.read_bytes()

    first = generate(tmp_path / "first")
    second = generate(tmp_path / "second")
    assert first[0] == second[0]
    assert first[1] == second[1]


def test_the_committed_scope_json_is_byte_for_byte_what_a_fresh_run_renders(report):
    committed = _blank_commit_in_json(COMMITTED_JSON.read_text(encoding="utf-8"))
    fresh = _blank_commit_in_json(scope_runner.render_json(report))
    assert committed.encode("utf-8") == fresh.encode("utf-8")


def test_the_committed_scope_markdown_is_byte_for_byte_what_a_fresh_run_renders(report):
    committed = _blank_commit_in_markdown(COMMITTED_MARKDOWN.read_text(encoding="utf-8"))
    fresh = _blank_commit_in_markdown(scope_runner.render_markdown(report))
    assert committed.encode("utf-8") == fresh.encode("utf-8")


def test_the_declared_misses_are_the_only_misses(report):
    """The §16.1 wording gap, and nothing else.

    Both directions matter. A new miss is a regression; a declared miss that stopped being one
    means the report's declared block is stale, which is just as wrong. The gate STAGE_08 §8
    sets is 1.000 and this run does not meet it — the two misses below are the whole of the
    shortfall, and closing them by broadening the ontology or the scope would be fitting the
    vocabulary to two fixtures.
    """
    actual = {(case.case.case_id, concept_id)
              for case in report.cases for concept_id in case.missed_metric_ids}
    declared = {(m["case_id"], m["concept_id"]) for m in scope_runner.KNOWN_MISSES}
    assert actual == declared
    assert declared == {
        ("letter-prose-inventory-and-120d-q2-2022", "pct_homes_on_market_gt_120_days"),
        ("population-our-homes-in-inventory-q1-2023", "pct_homes_on_market_gt_120_days"),
    }

    # The story around each key, checked rather than trusted: the wording the report quotes
    # must really be in the passage, and the alias it names as closest must really be an alias
    # of the missed concept that the passage does not contain.
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    assert committed["totals"]["scores"]["required_concept_recall"] < 1.0
    assert committed["totals"]["scores"]["critical_concept_recall"] == 1.0
    assert committed["totals"]["scores"]["ambiguity_preservation"] == 1.0


def test_the_declared_miss_wordings_are_in_the_passages(passages, ontology):
    for miss in scope_runner.KNOWN_MISSES:
        case = next(c for c in scope_runner.load_scope_cases()
                    if c.case_id == miss["case_id"])
        text = passages[case.passage_id]["text"]
        assert miss["passage_wording"] in text, miss["case_id"]
        assert miss["closest_alias"] not in text.lower(), miss["case_id"]
        concept = ontology.registry.find(miss["concept_id"])
        assert any(miss["closest_alias"] == a.lower() for a in concept.aliases)


def test_the_critical_pairs_are_declared_distinct_by_the_ontology(ontology):
    """The report's transcription of §5, checked against the vocabulary it describes.

    A pair that is not declared `distinct_from` in both directions would make the confusion
    expansion unable to guarantee the symmetry the report claims, and the transcription is the
    only place that could go stale without anything failing.
    """
    for left, right in scope_runner.CRITICAL_PAIRS:
        assert right in ontology.registry.find(left).distinct_from, (left, right)
        assert left in ontology.registry.find(right).distinct_from, (right, left)


def test_ambiguity_preservation_covers_every_case_that_declares_it(report):
    """The denominator, not just the ratio.

    One case declares an `AMBIGUOUS_ALIAS` abstention. A preservation score of 1.000 over an
    empty denominator would read identically in the report, so the count is asserted here.
    """
    scored = [c for c in report.cases if c.ambiguity_preserved is not None]
    assert len(scored) == 1
    assert scored[0].case.case_id == "negative-ambiguous-alias-bare-gross-profit"
    assert scored[0].case.ambiguous_alias_candidates == (
        "adjusted_gross_margin", "adjusted_gross_profit", "gaap_gross_margin",
        "gaap_gross_profit")
    assert scored[0].missing_ambiguity_candidates == ()


def test_the_named_probes_appear_in_the_report_by_name(report):
    """STAGE_08 §5 names three wordings that must be findable in the report."""
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8")
    for name in ("Homes sold in period", "Gross Margin", "Gross profit"):
        assert name in markdown
    assert [p.name for p in report.probes] == [
        "Homes sold in period", "Gross Margin", "Gross profit"]

    homes = next(p for p in report.probes if p.name == "Homes sold in period")
    assert set(homes.scope.by_reason(AMBIGUOUS_ALIAS)) == {
        "homes_purchased", "homes_sold", "homes_under_contract", "housing_inventory_homes"}


def test_every_passage_id_in_the_scope_report_resolves(passages):
    """A report is evidence only if a reader can get from a candidate back to the filing."""
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    assert committed["cases"]
    for case in committed["cases"]:
        assert case["passage_id"] in passages, case["case_id"]
        assert passages[case["passage_id"]]["document_id"] == case["document_id"]
        # The eight, and only the eight. A `semantic_neighbour: 0` key here would be a report
        # of a check that never ran: the lexical scope has no embedding to run it with.
        assert set(case["counts_by_reason"]) == set(PROTECTED_REASONS)
        for candidate in case["candidates"]:
            assert set(candidate["reasons"]) <= PROTECTED_REASONS
            assert candidate["protected"] is True


def test_the_scope_report_covers_every_reviewed_case_with_a_passage():
    """All 26. A scope is defined for any passage, so a table-only report would hide the
    narrative cases where wording variance actually bites."""
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    case_ids = {c["case_id"] for c in committed["cases"]}
    declared = {c.case_id for c in scope_runner.load_scope_cases()}
    assert case_ids == declared
    assert len(case_ids) == 26


def test_known_instance_is_the_instance_id_not_its_type(scope, ontology):
    """`opendoor` is what a subject resolver needs to name; `public_company` it already has.

    Asserted because the alternative — scoping the instance's concept type — would make the
    reason code indistinguishable from the stable core and quietly untestable.
    """
    result = scope.scope_for("Opendoor Technologies Inc. reported results for the quarter.")
    assert KNOWN_INSTANCE in result.reasons_for("opendoor")
    assert {i.instance_id for i in ontology.registry.definitions.instances} >= {"opendoor"}
    assert KNOWN_INSTANCE not in result.reasons_for("public_company")
