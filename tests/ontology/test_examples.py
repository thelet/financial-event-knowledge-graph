"""The shipped fixtures are executable documentation, so they are executed.

Each invalid fixture names the codes it must trigger. Asserting the specific codes — not
merely that something failed — is what stops a fixture from passing for the wrong reason
after an unrelated rule changes.
"""

from __future__ import annotations

import pytest

from ontology.examples import load_examples


def _ids(examples):
    return [e.name for e in examples]


def valid_examples(examples_dir):
    return load_examples(examples_dir / "valid", should_validate=True)


def invalid_examples(examples_dir):
    return load_examples(examples_dir / "invalid", should_validate=False)


def test_both_fixture_sets_are_populated(examples_dir):
    assert len(valid_examples(examples_dir)) >= 12
    assert len(invalid_examples(examples_dir)) >= 10


def test_every_valid_fixture_validates(ontology, examples_dir):
    failures = {
        example.name: ontology.validate_claim(example.claim).render()
        for example in valid_examples(examples_dir)
        if not ontology.validate_claim(example.claim).ok
    }
    assert not failures, failures


def test_every_invalid_fixture_fails_with_the_codes_it_declares(ontology, examples_dir):
    for example in invalid_examples(examples_dir):
        result = ontology.validate_claim(example.claim)
        assert not result.ok, f"{example.name} was expected to fail"
        missing = example.expected_codes - result.codes
        assert not missing, f"{example.name}: expected {sorted(missing)}, got {sorted(result.codes)}"


def test_every_fixture_explains_itself(examples_dir):
    for example in (*valid_examples(examples_dir), *invalid_examples(examples_dir)):
        assert len(example.description) > 40, f"{example.name} has no real description"


def test_an_invalid_fixture_that_declares_no_codes_is_refused(tmp_path):
    """Otherwise it passes as long as *anything* is wrong with it."""
    (tmp_path / "x.yaml").write_text(
        "description: no codes\n"
        "claim: {claim_id: c, claim_kind: event, event: {event_id: e, event_type_id: t}}\n"
    )
    with pytest.raises(ValueError, match="expects"):
        load_examples(tmp_path, should_validate=False)


def test_the_fixtures_cover_every_distinction_the_research_calls_load_bearing(
    ontology, examples_dir
):
    covered = set()
    for example in invalid_examples(examples_dir):
        covered |= ontology.validate_claim(example.claim).codes
    for code in ("source_lane_not_allowed_for_metric", "missing_evidence",
                 "reported_observation_must_not_carry_calculation_fields",
                 "calculated_observation_requires_inputs_and_formula",
                 "discovery_only_channel_cited_as_canonical_evidence",
                 "unknown_relationship_predicate", "event_participant_invalid",
                 "observation_unit_not_allowed_for_metric"):
        assert code in covered, f"no fixture demonstrates {code}"
