"""The ontology loads, resolves and is deterministic."""

from __future__ import annotations

import pytest
import yaml

from ontology import load_ontology
from ontology.core.errors import OntologyLoadError
from ontology.versions.real_estate_marketplace_v1.loader import YamlDefinitionLoader
from ontology.validation import validate_definitions


def test_loads_and_validates(ontology):
    assert validate_definitions(ontology.definitions).ok


def test_metadata_identifies_its_research_source(ontology):
    metadata = ontology.metadata
    assert metadata.ontology_id == "real_estate_marketplace_v1"
    # 2.0.0 at F0: two `required_fields` additions make the vocabulary breaking, not
    # additive, so a reference legal under 1.0.0 can be refused under this one.
    assert metadata.semantic_version == "2.0.0"
    assert "OPENDOOR_CONCEPT_AND_METRIC_RESEARCH" in metadata.created_from_research_version


def test_every_declared_category_is_populated(definitions):
    """A silently empty section would look like a passing load."""
    for name in ("entity_types", "role_types", "instrument_types", "agreement_types",
                 "metrics", "formula_versions", "event_types", "relationships",
                 "evidence_types", "claim_types", "status_types", "instances", "aliases"):
        assert getattr(definitions, name), f"{name} is empty"


def test_definition_hash_is_stable_across_loads():
    assert load_ontology().definition_hash == load_ontology().definition_hash


def test_snapshot_is_byte_identical_across_loads():
    assert load_ontology().render_snapshot() == load_ontology().render_snapshot()


def test_snapshot_carries_no_timestamp_or_run_id(ontology):
    rendered = ontology.render_snapshot()
    for volatile in ("created_at", "run_id", "code_commit", "generated_at", "timestamp"):
        assert volatile not in rendered


def test_hash_changes_when_a_definition_changes(definitions):
    from ontology.serialization import definition_hash

    changed = definitions.metrics[0].model_copy(update={"unit": "count"})
    mutated = definitions.__class__(
        **{**definitions.__dict__, "metrics": (changed, *definitions.metrics[1:])}
    )
    assert definition_hash(mutated) != definition_hash(definitions)


def test_missing_definition_file_is_reported_with_its_path(tmp_path):
    with pytest.raises(OntologyLoadError) as error:
        YamlDefinitionLoader(tmp_path).load()
    assert "missing_definition_file" in str(error.value) or "ontology.yaml" in str(error.value)


def test_malformed_entry_reports_every_bad_field_not_just_the_first(tmp_path, definition_dir):
    """One error per run would make fixing a fresh YAML file a serial exercise."""
    for path in definition_dir.glob("*.yaml"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    metrics = yaml.safe_load((tmp_path / "metrics.yaml").read_text())
    metrics["metrics"][0]["value_type"] = "not_a_value_type"
    metrics["metrics"][1]["period_type"] = "not_a_period_type"
    (tmp_path / "metrics.yaml").write_text(yaml.safe_dump(metrics))

    with pytest.raises(OntologyLoadError) as error:
        YamlDefinitionLoader(tmp_path).load()
    assert len(error.value.issues) >= 2


def test_external_mappings_for_an_unknown_concept_fail_the_load(tmp_path, definition_dir):
    for path in definition_dir.glob("*.yaml"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    document = yaml.safe_load((tmp_path / "external_mappings.yaml").read_text())
    document["mappings"].append({"concept_id": "no_such_concept", "external_mappings": []})
    (tmp_path / "external_mappings.yaml").write_text(yaml.safe_dump(document))

    with pytest.raises(OntologyLoadError):
        YamlDefinitionLoader(tmp_path).load()


def test_unknown_ontology_id_names_what_is_available():
    from ontology.core.errors import OntologyError

    with pytest.raises(OntologyError, match="real_estate_marketplace_v1"):
        load_ontology("no_such_ontology")
