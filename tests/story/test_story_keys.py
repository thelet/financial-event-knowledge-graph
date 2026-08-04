"""What the id scheme promises: same inputs, same name; different inputs, different name.

Two properties, and the second is the one that has already been got wrong twice in this
repository. `extraction_run_id` named two different sets of bytes two days apart (§18); the
first draft of `story_run_id` covered neither the selection flags nor the budget parameters,
so `--limit 3` and `--limit 20` minted one id and atomic finalisation would have replaced one
run's directory with the other's (§14). Both are the same failure: a digest that does not
cover something that changes the answer.

So `story_run_id` is tested by **parametrising over every digest input** rather than by
asserting a field list. §14 is explicit about why: *"a test asserts the property rather than
the field list, because the field list is the thing that drifted."* A new input added to the
signature without a row in `DIGEST_INPUTS` fails `test_every_story_run_id_parameter_is_covered`.
"""

from __future__ import annotations

from typing import Any

import pytest

from extraction.core.identifiers import DIGEST_CHARS, EmptyIdentityError, digest
from story import STORY_LAYOUT_VERSION
from story.core import keys
from story.core.models import BudgetParameters, RunSelection

CANDIDATE = dict(
    detector_id="detector:metric_move",
    detector_version="1.0.0",
    policy_version="canon-policy:1.0.0",
    scope="adjusted_ebitda",
    subject_entity_id="opendoor",
    anchor_period_keys=("2022Q2", "2022Q3"),
    metric_ids=("adjusted_ebitda",),
    anchor_input_ids=("obs:adjusted-ebitda:opendoor:2022Q2:normalized-table:aaaaaaaaaaaa",
                      "obs:adjusted-ebitda:opendoor:2022Q3:normalized-table:bbbbbbbbbbbb"),
)

RUN = dict(
    graph_run_id="graph-v1-0483dc6b4b10",
    run_complete_sha256="1cc8f7b01c040531" + "0" * 48,
    ontology_definition_hash="bb94f522ba122470" + "0" * 48,
    config_hash="cfg0123456789ab",
    prompt_version="story-planner-1.0.0",
    model_id="qwen3.5-9b",
    provider_model_id="/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf",
    temperature=0.0,
    max_tokens=1024,
    schema_digests={"editorial_plan": "aaaa", "draft": "bbbb"},
    detector_versions={"detector:metric_move": "1.0.0"},
    policy_version="canon-policy:1.0.0",
    ranking_policy_version="1.1.0",
    selection=RunSelection(limit=3, detector_ids=("detector:metric_move",)),
    budget=BudgetParameters(),
)

PACKAGE = dict(
    candidate_id=keys.candidate_id(**CANDIDATE),
    package_version="1.0.0",
    graph_run_id=RUN["graph_run_id"],
    run_complete_sha256=RUN["run_complete_sha256"],
    ontology_definition_hash=RUN["ontology_definition_hash"],
    fact_ids=("obs:a", "obs:b"),
    passage_ids=("psg:1",),
    budget=BudgetParameters(),
)


def replacing(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**base, **changes}


# -- candidate_id ------------------------------------------------------------------------


def test_one_candidate_built_twice_from_one_input_set_gets_one_id():
    assert keys.candidate_id(**CANDIDATE) == keys.candidate_id(**CANDIDATE)


def test_the_candidate_id_reads_as_the_plan_specifies_it():
    """`cand:{detector_slug}:{scope_slug}:{subject}:{anchor}:{digest12}` — §6.11."""
    value = keys.candidate_id(**CANDIDATE)
    head, detector, scope, subject, anchor, tail = value.split(":")
    assert head == "cand"
    assert detector == "metric-move", "the constant `detector:` head is dropped"
    assert scope == "adjusted-ebitda"
    assert subject == "opendoor"
    assert anchor == "2022Q2_2022Q3"
    assert len(tail) == DIGEST_CHARS and tail.isalnum()


def test_two_candidates_from_reordered_metric_ids_get_one_id():
    """§6.11 sorts the digest inputs, so the order a detector visited its metrics in cannot
    become part of a candidate's identity."""
    forwards = keys.candidate_id(**replacing(
        CANDIDATE, metric_ids=("adjusted_gross_margin", "gaap_gross_margin")))
    backwards = keys.candidate_id(**replacing(
        CANDIDATE, metric_ids=("gaap_gross_margin", "adjusted_gross_margin")))
    assert forwards == backwards


def test_two_candidates_from_reordered_anchor_observation_ids_get_one_id():
    reversed_anchors = tuple(reversed(CANDIDATE["anchor_input_ids"]))
    assert reversed_anchors != CANDIDATE["anchor_input_ids"]
    assert keys.candidate_id(**replacing(CANDIDATE, anchor_input_ids=reversed_anchors)) == \
        keys.candidate_id(**CANDIDATE)


def test_two_candidates_from_reordered_anchor_period_keys_get_one_id():
    """The anchor is a readable segment and carries no uniqueness — but it is still sorted,
    so two calls differing only in argument order cannot write two rows to
    `candidates.jsonl` describing one candidate."""
    assert keys.candidate_id(**replacing(
        CANDIDATE, anchor_period_keys=("2022Q3", "2022Q2"))) == keys.candidate_id(**CANDIDATE)


def test_a_bumped_detector_version_mints_a_new_candidate():
    """§6.11's reason for putting it in the digest: a threshold change must mint a new
    candidate rather than silently mutate an existing one."""
    assert keys.candidate_id(**replacing(CANDIDATE, detector_version="1.0.1")) != \
        keys.candidate_id(**CANDIDATE)


def test_a_bumped_policy_version_mints_a_new_candidate():
    """Same argument for canonicalisation: §6.1 deciding a slot differently is a different
    candidate, not the same one with new numbers."""
    assert keys.candidate_id(**replacing(CANDIDATE, policy_version="canon-policy:1.1.0")) != \
        keys.candidate_id(**CANDIDATE)


def test_a_different_metric_set_mints_a_new_candidate():
    assert keys.candidate_id(**replacing(CANDIDATE, metric_ids=("gaap_gross_margin",))) != \
        keys.candidate_id(**CANDIDATE)


def test_a_different_anchor_observation_mints_a_new_candidate():
    assert keys.candidate_id(**replacing(CANDIDATE, anchor_input_ids=("obs:elsewhere",))) != \
        keys.candidate_id(**CANDIDATE)


def test_the_readable_segments_carry_no_uniqueness():
    """Two candidates differing only in `scope` differ in the segment and share the digest.

    The digest is over structural inputs; `scope` is the detector's own naming of what the
    candidate is about. Asserting they share a digest is what proves the segment is
    readability and not identity.
    """
    a = keys.candidate_id(**CANDIDATE)
    b = keys.candidate_id(**replacing(CANDIDATE, scope="adjusted_ebitda_restated"))
    assert a != b
    assert a.split(":")[-1] == b.split(":")[-1]


def test_the_digest_is_the_repositorys_own_over_the_parts_the_plan_names():
    """Not a reimplementation: the same `digest` every other id in the repository uses, over
    exactly §6.11's part list."""
    expected = digest(
        CANDIDATE["detector_id"], CANDIDATE["detector_version"], CANDIDATE["policy_version"],
        *sorted(CANDIDATE["metric_ids"]), *sorted(CANDIDATE["anchor_input_ids"]))
    assert keys.candidate_id(**CANDIDATE).split(":")[-1] == expected


@pytest.mark.parametrize(
    "blank", ["detector_id", "detector_version", "policy_version", "scope",
              "subject_entity_id"])
def test_a_candidate_id_is_refused_when_a_required_part_is_blank(blank):
    """`cand:metric-move::opendoor:…` reads like an identity and is missing one. Refused at
    the point of minting, which is `EmptyIdentityError`'s whole argument."""
    with pytest.raises(EmptyIdentityError):
        keys.candidate_id(**replacing(CANDIDATE, **{blank: "  "}))


def test_a_candidate_with_no_anchor_period_is_refused():
    with pytest.raises(EmptyIdentityError):
        keys.candidate_id(**replacing(CANDIDATE, anchor_period_keys=()))


def test_a_candidate_with_no_metrics_is_allowed_because_leadership_change_has_none():
    """D9 anchors on an event, not on a metric (§6.5), and its id must still exist."""
    value = keys.candidate_id(
        detector_id="detector:leadership_change", detector_version="1.0.0",
        policy_version="canon-policy:1.0.0", scope="executive_change",
        subject_entity_id="opendoor", anchor_period_keys=("2025-09-10",),
        anchor_input_ids=("evt:executive-change:undated:0365d72eac21",))
    assert value.startswith("cand:leadership-change:executive-change:opendoor:2025-09-10:")


def test_the_local_slug_agrees_with_the_one_extractions_identifiers_applies():
    """`slug` is restated rather than imported from a private name; this is what stops the
    two drifting. Driven through a public id `extraction` builds with its own `_slug`."""
    from extraction.core.identifiers import relationship_instance_id

    for value in ("Adjusted EBITDA", "detector:metric_move", "pct_>120d", "  Spaced  ",
                  "Opendoor Technologies Inc."):
        built = relationship_instance_id(value, "s", "t", "p").split(":")[1]
        assert keys.slug(value) == built, value


# -- package_id --------------------------------------------------------------------------


def test_one_package_built_twice_from_one_input_set_gets_one_id():
    assert keys.package_id(**PACKAGE) == keys.package_id(**PACKAGE)


def test_the_package_id_reads_as_the_plan_specifies_it():
    """`pkg:{candidate_slug}:{digest12}` — §10.3, with the candidate's own digest dropped from
    the segment so it does not claim a uniqueness it is not carrying."""
    value = keys.package_id(**PACKAGE)
    head, slug, tail = value.split(":")
    assert head == "pkg"
    assert slug == "metric-move-adjusted-ebitda-opendoor-2022q2-2022q3"
    assert len(tail) == DIGEST_CHARS
    assert PACKAGE["candidate_id"].split(":")[-1] not in slug


def test_two_packages_from_reordered_fact_and_passage_ids_get_one_id():
    assert keys.package_id(**replacing(
        PACKAGE, fact_ids=("obs:b", "obs:a"), passage_ids=("psg:1",))) == \
        keys.package_id(**PACKAGE)


@pytest.mark.parametrize("field", ["candidate_id", "package_version", "graph_run_id",
                                   "run_complete_sha256", "ontology_definition_hash"])
def test_a_package_id_changes_when_any_identity_input_changes(field):
    assert keys.package_id(**replacing(PACKAGE, **{field: "different"})) != \
        keys.package_id(**PACKAGE)


def test_a_package_id_changes_when_a_fact_enters_or_leaves():
    assert keys.package_id(**replacing(PACKAGE, fact_ids=("obs:a",))) != \
        keys.package_id(**PACKAGE)


def test_a_package_id_changes_when_a_budget_cap_changes():
    """§10.2's caps decide what is in a package, so four primary passages and six are two
    packages and must not share a name."""
    assert keys.package_id(**replacing(
        PACKAGE, budget=BudgetParameters(max_primary_passages=6))) != keys.package_id(**PACKAGE)


def test_two_packages_of_one_candidate_over_two_graph_runs_differ():
    """The one collision the graph layer had to fix with `input_content_digest`: two different
    inputs naming one directory is data loss, not a naming inconvenience."""
    assert keys.package_id(**replacing(PACKAGE, graph_run_id="graph-v1-ffffffffffff")) != \
        keys.package_id(**PACKAGE)


# -- story_run_id ------------------------------------------------------------------------

#: Every digest input, with a value that differs from `RUN`'s. Parametrised rather than
#: asserted as a field list, per §14.
DIGEST_INPUTS: dict[str, Any] = {
    "graph_run_id": "graph-v1-ffffffffffff",
    "run_complete_sha256": "f" * 64,
    "ontology_definition_hash": "e" * 64,
    "config_hash": "cfgffffffffffff",
    "prompt_version": "story-planner-1.1.0",
    "model_id": "qwen3.5-14b",
    "provider_model_id": "/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q5_K_M.gguf",
    "temperature": 0.2,
    "max_tokens": 2048,
    "schema_digests": {"editorial_plan": "cccc", "draft": "bbbb"},
    "detector_versions": {"detector:metric_move": "1.0.1"},
    "policy_version": "canon-policy:1.1.0",
    # §6.10's score decides which candidate becomes a post, so two formulas are two runs.
    # Before this row, gate G2's change to the units term moved no id at all.
    "ranking_policy_version": "1.2.0",
    "selection": RunSelection(limit=20, detector_ids=("detector:metric_move",)),
    "budget": BudgetParameters(max_primary_passages=6),
    "story_layout_version": "2.0.0",
}


def test_one_run_described_twice_gets_one_id():
    assert keys.story_run_id(**RUN) == keys.story_run_id(**RUN)


def test_the_story_run_id_reads_as_the_plan_specifies_it():
    value = keys.story_run_id(**RUN)
    prefix, major, tail = value.split("-")
    assert prefix == "story"
    assert major == "v" + STORY_LAYOUT_VERSION.split(".")[0]
    assert len(tail) == DIGEST_CHARS


@pytest.mark.parametrize("field", sorted(DIGEST_INPUTS))
def test_two_runs_differing_in_one_digest_input_get_different_ids(field):
    """§14's property, one row per input. This is the test the first draft would have failed
    on `selection` and on the budget parameters."""
    assert keys.story_run_id(**replacing(RUN, **{field: DIGEST_INPUTS[field]})) != \
        keys.story_run_id(**RUN)


def test_every_story_run_id_parameter_is_covered_by_a_row_above():
    """A guard on the guards: an input added to the signature and not to `DIGEST_INPUTS`
    would be an input nothing proved the digest covers."""
    import inspect

    parameters = set(inspect.signature(keys.story_run_id).parameters)
    assert parameters == set(DIGEST_INPUTS)


@pytest.mark.parametrize(
    "field,value",
    [("limit", 20), ("candidate_ids", ("cand:x",)), ("detector_ids", ("detector:acceleration",)),
     ("since", "2022-01-01"), ("until", "2023-01-01")])
def test_two_runs_differing_in_one_selection_flag_get_different_ids(field, value):
    """`--limit`, `--candidates`, `--detectors`, `--since`, `--until` — the five §14 names, each
    checked. Their omission is the defect §14 records against its own first draft."""
    changed = RUN["selection"].model_copy(update={field: value})
    assert keys.story_run_id(**replacing(RUN, selection=changed)) != keys.story_run_id(**RUN)


def test_two_runs_selecting_the_same_detectors_in_two_orders_get_one_id():
    """Asking for two detectors in the other order is one selection, not two."""
    forwards = RUN["selection"].model_copy(update={"detector_ids": ("a", "b")})
    backwards = RUN["selection"].model_copy(update={"detector_ids": ("b", "a")})
    assert keys.story_run_id(**replacing(RUN, selection=forwards)) == \
        keys.story_run_id(**replacing(RUN, selection=backwards))


def test_two_runs_naming_the_same_schemas_in_two_orders_get_one_id():
    reordered = {"draft": "bbbb", "editorial_plan": "aaaa"}
    assert list(reordered) != list(RUN["schema_digests"])
    assert keys.story_run_id(**replacing(RUN, schema_digests=reordered)) == \
        keys.story_run_id(**RUN)


def test_an_integer_and_a_float_temperature_are_one_temperature():
    """`0` and `0.0` are the same setting and must not be two runs; the config layer hands
    over whichever the YAML parsed to."""
    assert keys.story_run_id(**replacing(RUN, temperature=0)) == keys.story_run_id(**RUN)


def test_swapping_the_model_id_and_the_provider_model_id_changes_the_id():
    """Labelled digest parts, so two string inputs cannot be exchanged silently."""
    swapped = replacing(RUN, model_id=RUN["provider_model_id"],
                        provider_model_id=RUN["model_id"])
    assert keys.story_run_id(**swapped) != keys.story_run_id(**RUN)


def test_the_story_run_id_reads_no_clock():
    """Derived, never stamped — `graph/core/manifest.py:104-139`'s rule. Two calls a second
    apart are the same call."""
    import time

    first = keys.story_run_id(**RUN)
    time.sleep(0.01)
    assert keys.story_run_id(**RUN) == first


@pytest.mark.parametrize(
    "blank", ["graph_run_id", "run_complete_sha256", "ontology_definition_hash", "config_hash",
              "prompt_version", "model_id", "provider_model_id", "policy_version",
              "ranking_policy_version"])
def test_a_story_run_id_is_refused_when_a_required_input_is_blank(blank):
    with pytest.raises(EmptyIdentityError):
        keys.story_run_id(**replacing(RUN, **{blank: ""}))


# -- content digests ---------------------------------------------------------------------


def test_the_package_content_digest_is_a_full_length_sha256_over_canonical_json():
    """§10.3: `sha256` over the canonical JSON with `sort_keys=True, separators=(",", ":")`."""
    import hashlib

    from story.core.models import canonical_json

    payload = {"b": 1, "a": [2, 3]}
    expected = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    assert keys.package_content_digest(payload) == expected
    assert len(expected) == 64


def test_two_payloads_differing_only_in_key_order_hash_the_same():
    assert keys.package_content_digest({"a": 1, "b": 2}) == \
        keys.package_content_digest({"b": 2, "a": 1})


def test_a_changed_value_anywhere_changes_the_content_digest():
    assert keys.package_content_digest({"a": 1}) != keys.package_content_digest({"a": 2})


def test_the_draft_digest_covers_the_bindings_and_not_only_the_text(draft):
    """A rebinding is a different draft. A hash over rendered prose would call the two one."""
    first = keys.draft_content_sha256(draft.digestible_payload())
    sentence = draft.sentences[0]
    rebound = sentence.model_copy(update={
        "fact_bindings": (sentence.fact_bindings[0].model_copy(
            update={"fact_id": "obs:adjusted-ebitda:a"}),)})
    moved = draft.model_copy(update={"sentences": (rebound,)})

    assert moved.sentences[0].text == draft.sentences[0].text
    assert keys.draft_content_sha256(moved.digestible_payload()) != first
    assert len(first) == 64
