"""The hybrid candidate scope, the vector cache, and the embedding adapter. Offline.

The whole file runs without a server. The committed vector caches under
`benchmarks/extraction/v1/vectors/` are the real ones, so the corpus-driven tests here drive
the real embedding of the real ontology; where a *controlled* similarity is needed, a stub
`EmbeddingProvider` supplies it, because "what happens when nothing clears the threshold" is
not a question a real model can be asked to answer on demand.

The interesting tests are again the negative ones. Stage 9's entire safety argument is that
embeddings may only *add* — so what has to be pinned is that a lexical candidate cannot leave,
that a semantic candidate cannot become protected, and that a cache which cannot answer says
so instead of returning a zero vector that would look like a model finding nothing.
"""

from __future__ import annotations

import json
import re
import socket
import struct
from pathlib import Path

import httpx
import pytest
import yaml

from benchmarks.extraction.v1 import hybrid_scope_runner, runner, scope_runner
from extraction.contracts import EmbeddingProvider, OntologyCandidateScope
from extraction.providers import (
    EmbeddingConfig,
    LocalOpenAICompatibleEmbeddingProvider,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeout,
    ProviderTransportError,
    ProviderUnavailable,
)
from extraction.stages.scoping import (
    PROTECTED_REASONS,
    SCOPE_REASONS,
    SEMANTIC_NEIGHBOUR,
    CacheIdentity,
    CandidateScope,
    ScopedConcept,
    HybridOntologyCandidateScope,
    LexicalOntologyCandidateScope,
    MissingVectorError,
    ScopingConfig,
    StaleVectorCacheError,
    VectorCache,
    concept_vectors,
    decode_vector,
    encode_vector,
    render_concept,
    text_key,
)
from extraction.stages.select import AliasIndex
from ontology import load_ontology

REPO = Path(__file__).resolve().parents[2]
COMMITTED_JSON = runner.REPORTS_DIR / f"{hybrid_scope_runner.REPORT_STEM}.json"
COMMITTED_MARKDOWN = runner.REPORTS_DIR / f"{hybrid_scope_runner.REPORT_STEM}.md"

PINNED_COMMIT = "0" * 40

COMMIT_IN_JSON = re.compile(r'^(\s*"implementation_commit": ")[^"]*(",?)$', re.MULTILINE)
COMMIT_IN_MARKDOWN = re.compile(r"^\| implementation commit \| `[^`]*` \|$", re.MULTILINE)

DIMENSIONS = 4
IDENTITY = CacheIdentity("hash", "stub-model", DIMENSIONS)


def _blank_commit_in_json(text: str) -> str:
    return COMMIT_IN_JSON.sub(r"\1<commit>\2", text)


def _blank_commit_in_markdown(text: str) -> str:
    return COMMIT_IN_MARKDOWN.sub("| implementation commit | `<commit>` |", text)


class StubEmbeddingProvider:
    """A deterministic `EmbeddingProvider` with the similarities written down by hand.

    Texts it does not know embed to all zeros, which is the point: a zero vector has cosine
    0.0 against every concept, so a stub that "knows nothing" produces a scope that adds
    nothing — and that is exactly the control the add-only tests need.
    """

    model_id = "stub-model"
    dimensions = DIMENSIONS

    def __init__(self, vectors: dict[str, tuple[float, ...]] | None = None) -> None:
        self._vectors = dict(vectors or {})
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        return tuple(
            self._vectors.get(text, (0.0,) * DIMENSIONS) for text in texts)


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


@pytest.fixture(scope="module")
def alias_index(ontology):
    return AliasIndex.from_ontology(ontology)


@pytest.fixture(scope="module")
def lexical(ontology, alias_index):
    return LexicalOntologyCandidateScope(ontology, alias_index)


@pytest.fixture(scope="module")
def passages(repo_config):
    catalog = repo_config.catalog_root / "passages.jsonl"
    if not catalog.is_file():
        pytest.skip("no normalized corpus")
    return runner.load_passages(repo_config.catalog_root)


@pytest.fixture(scope="module")
def embedding_config():
    import yaml

    return EmbeddingConfig.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def scoping_config():
    import yaml

    return ScopingConfig.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def scope(ontology, embedding_config):
    """The real hybrid scope over the committed caches. No provider: a miss is an error."""
    if not hybrid_scope_runner.CONCEPT_VECTORS.is_file():
        pytest.skip("no committed vector cache")
    built, _concepts, _texts = hybrid_scope_runner.build_scopes(
        ontology, provider=None,
        model_id=embedding_config.model, dimensions=embedding_config.dimensions)
    return built


@pytest.fixture(scope="module")
def report(passages, ontology, embedding_config, repo_config):
    if not hybrid_scope_runner.CONCEPT_VECTORS.is_file():
        pytest.skip("no committed vector cache")
    return hybrid_scope_runner.build_report(
        catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)


def stub_scope(lexical, *, concepts, texts, top_k=2, min_similarity=0.45):
    """A hybrid scope whose similarities are dictated rather than measured."""
    provider = StubEmbeddingProvider(texts)
    cache = VectorCache(IDENTITY, provider=provider)
    return HybridOntologyCandidateScope(
        lexical, concepts, cache, top_k=top_k, min_similarity=min_similarity)


# -- §8.1 the add-only rule, over the real corpus ------------------------------------------------


def test_every_lexical_candidate_survives_in_the_hybrid_scope(scope, passages):
    """All 26 benchmark passages, real text, real vectors.

    The rule the whole stage rests on. Membership is not enough: a candidate whose reasons
    were rewritten would still be present while having lost the evidence that makes it
    protected, so both are asserted.
    """
    for case in scope_runner.load_scope_cases():
        text = passages[case.passage_id]["text"]
        lexical = scope.lexical.scope_for(text)
        hybrid = scope.scope_for(text)

        assert set(lexical.concept_ids) <= set(hybrid.concept_ids), case.case_id
        for candidate in lexical.concepts:
            reasons = set(hybrid.reasons_for(candidate.concept_id))
            assert set(candidate.reasons) <= reasons, (case.case_id, candidate.concept_id)
        # A semantic addition can never join the protected set, so the protected set of the
        # hybrid scope is exactly the lexical scope.
        assert set(hybrid.protected_ids) == set(lexical.concept_ids), case.case_id
        assert lexical.expansions == hybrid.expansions, case.case_id


def test_the_hybrid_scope_never_reorders_a_protected_candidate(scope, passages):
    """Order, not only membership: the report serialises this list.

    The lexical scope emits concept ids in sorted order and the hybrid scope keeps that order
    while interleaving semantic candidates, so the protected ids must appear in the hybrid
    list in the same relative order they appear in the lexical one.
    """
    for case in scope_runner.load_scope_cases():
        text = passages[case.passage_id]["text"]
        lexical = list(scope.lexical.scope_for(text).concept_ids)
        hybrid = list(scope.scope_for(text).concept_ids)
        assert [c for c in hybrid if c in set(lexical)] == lexical, case.case_id


# -- §8.2-8.4 the reason code --------------------------------------------------------------------


def test_a_semantic_candidate_carries_semantic_neighbour_and_is_not_protected(lexical):
    """A concept no surface in the text names, admitted on similarity alone."""
    scope = stub_scope(
        lexical,
        concepts={"homes_sold": (1.0, 0.0, 0.0, 0.0), "holding_costs": (0.0, 1.0, 0.0, 0.0)},
        texts={"nothing named here": (0.0, 1.0, 0.0, 0.0)})

    result = scope.scope_for("nothing named here")

    assert result.reasons_for("holding_costs") == (SEMANTIC_NEIGHBOUR,)
    assert "holding_costs" not in result.protected_ids
    concept = next(c for c in result.concepts if c.concept_id == "holding_costs")
    assert concept.protected is False


def test_a_concept_found_both_ways_carries_both_reasons_and_stays_protected(lexical):
    """The union is a union of reasons, not a choice between them."""
    scope = stub_scope(
        lexical,
        concepts={"homes_sold": (1.0, 0.0, 0.0, 0.0)},
        texts={"Resale Closes were 2,946 in the quarter.": (1.0, 0.0, 0.0, 0.0)})

    result = scope.scope_for("Resale Closes were 2,946 in the quarter.")

    reasons = result.reasons_for("homes_sold")
    assert SEMANTIC_NEIGHBOUR in reasons
    assert "exact_alias" in reasons
    assert "homes_sold" in result.protected_ids


def test_semantic_neighbour_is_a_scope_reason_and_not_a_protected_one():
    """STAGE_09 §4.1, as one assertion. The asymmetry is the safety argument."""
    assert SEMANTIC_NEIGHBOUR in SCOPE_REASONS
    assert SEMANTIC_NEIGHBOUR not in PROTECTED_REASONS
    assert PROTECTED_REASONS < SCOPE_REASONS
    assert len(PROTECTED_REASONS) == 8


def test_a_reason_vocabulary_narrower_than_the_reasons_carried_is_refused():
    """The counting defect in the other direction, and the more dangerous one.

    `reason_vocabulary` exists so a zero can be keyed honestly. A vocabulary *narrower* than the
    reasons present is the same defect inverted: `counts_by_reason()` keys only the vocabulary,
    so a candidate carrying an undeclared reason is counted under nothing at all while still
    appearing in `concept_ids` and in `len(scope)`. The counts and the set silently stop
    agreeing, which is unfalsifiable from the report.
    """
    semantic = ScopedConcept(concept_id="holding_costs", reasons=(SEMANTIC_NEIGHBOUR,))

    with pytest.raises(ValueError) as raised:
        CandidateScope(concepts=(semantic,))          # defaults to PROTECTED_REASONS
    assert SEMANTIC_NEIGHBOUR in str(raised.value)

    # The same scope with the vocabulary that covers it is legal, and its counts add up.
    legal = CandidateScope(concepts=(semantic,), reason_vocabulary=SCOPE_REASONS)
    assert sum(legal.counts_by_reason().values()) == len(legal)
    assert legal.counts_by_reason()[SEMANTIC_NEIGHBOUR] == 1


def test_every_scope_the_package_builds_declares_the_reasons_it_carries(scope, passages):
    """Driven over the real corpus rather than asserted on a constructed pair.

    All three scopes the report scores — lexical, semantic-only and hybrid — for all 26
    passages: the counts must sum to the candidate count under a vocabulary that covers them.
    """
    for case in scope_runner.load_scope_cases():
        text = passages[case.passage_id]["text"]
        for built in (scope.lexical.scope_for(text), scope.semantic_scope_for(text),
                      scope.scope_for(text)):
            present = {r for c in built.concepts for r in c.reasons}
            assert present <= set(built.reason_vocabulary), case.case_id
            counted = sum(
                1 for c in built.concepts
                if any(built.counts_by_reason().get(r) for r in c.reasons))
            assert counted == len(built), case.case_id


# -- §8.5 the renderer ----------------------------------------------------------------------------


def test_renderer_v1_is_pure_and_excludes_the_corpus_fitted_content(ontology):
    """Same concept, same string, twice — and neither a filed quote nor a raw variant in it.

    `pct_homes_on_market_gt_120_days` is the only concept in the vocabulary carrying
    `population.raw_variants`, and one of them — "our homes in inventory" — is verbatim the
    wording of the second stage-8 miss. If the renderer let it through, a recovery of that
    case would be a transcription matching itself rather than a semantic match.
    """
    concept = ontology.registry.find("pct_homes_on_market_gt_120_days")

    rendered = render_concept(concept)
    assert rendered == render_concept(concept)
    assert concept.label in rendered
    assert concept.category.value in rendered
    for alias in concept.aliases:
        assert alias in rendered

    for variant in concept.population.raw_variants:
        assert variant not in rendered
    for evidence in concept.source_evidence:
        if evidence.quote:
            assert evidence.quote not in rendered

    # And the ablation arm is the same function with one argument, or the measurement in the
    # report is comparing two things that differ in more than the variants.
    with_variants = render_concept(concept, include_raw_variants=True)
    assert with_variants != rendered
    for variant in concept.population.raw_variants:
        assert variant in with_variants


def test_every_concept_renders_to_a_non_empty_stable_string(ontology):
    """131 concepts, no exceptions. A concept that rendered to "" would embed as whatever the
    model does with an empty string and would sit at a fixed, meaningless similarity."""
    for concept in ontology.registry.definitions.concepts:
        rendered = render_concept(concept)
        assert rendered.strip(), concept.concept_id
        assert "  " not in rendered, concept.concept_id
        assert rendered == render_concept(concept)


# -- §8.6-8.9 the cache ----------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("definition_hash", "other"),
    ("model_id", "other-model"),
    ("dimensions", 8),
    ("renderer_version", "v2"),
    ("text_normalization_version", "v2"),
])
def test_cache_identity_changes_when_any_of_its_five_inputs_changes(field, value):
    """Five inputs, five assertions. `definition_hash` is the one that matters most: editing
    `aliases.yaml` changes what was embedded, and a cache that survived that edit would score
    a new passage against an old vocabulary with every other hash still agreeing."""
    import dataclasses

    changed = dataclasses.replace(IDENTITY, **{field: value})
    assert changed.key != IDENTITY.key
    assert len(changed.key) == 64


def test_a_cache_whose_key_disagrees_is_rejected_not_reused(tmp_path):
    cache = VectorCache(IDENTITY, provider=StubEmbeddingProvider({"a": (1.0, 0, 0, 0)}))
    cache.vector_for("a")
    path = cache.save(tmp_path / "vectors.json")

    other = CacheIdentity("a-different-ontology", "stub-model", DIMENSIONS)
    with pytest.raises(StaleVectorCacheError) as raised:
        VectorCache.load(path, other)

    assert IDENTITY.key in str(raised.value)
    assert other.key in str(raised.value)
    # Nothing partial: the entry that *would* have been reusable is not.
    assert "a-different-ontology" in str(raised.value)


def test_vectors_round_trip_base64_float32_bit_identically():
    """Bit-identical, not almost. Byte-identical regeneration of a committed cache is provable
    only if the encoding is exact for the values it actually stores."""
    original = struct.unpack("<4f", struct.pack("<4f", 0.1, -0.25, 1e-8, 0.9999999))
    assert decode_vector(encode_vector(original)) == original

    encoded = encode_vector(original)
    assert encode_vector(decode_vector(encoded)) == encoded


def test_cosine_refuses_two_vectors_of_different_widths():
    """`zip` would stop at the shorter operand and return a partial dot product.

    That number is in [0, 1] and indistinguishable from a real similarity, so a 1024-dimension
    cache compared against a 768-dimension one would produce a plausible, wrong ranking rather
    than an error. The cache checks dimensions on load; this is the same refusal one layer down,
    where a caller assembling vectors from two sources reaches it first.
    """
    from extraction.stages.scoping import VectorCacheError, cosine

    assert cosine((1.0, 0.0), (1.0, 0.0)) == 1.0
    with pytest.raises(VectorCacheError) as raised:
        cosine((1.0, 0.0, 0.0), (1.0, 0.0))
    assert "3" in str(raised.value) and "2" in str(raised.value)


def test_a_miss_with_no_provider_raises_and_names_the_text():
    """Never a zero vector. A zero vector scores 0.0 against everything, so a scope built on
    one is indistinguishable from a model that found nothing."""
    cache = VectorCache(IDENTITY, provider=None)
    with pytest.raises(MissingVectorError) as raised:
        cache.vector_for("a passage the cache has never seen")

    assert "a passage the cache has never seen" in str(raised.value)
    assert len(cache) == 0


def test_a_miss_with_a_provider_fills_through_and_is_reused():
    provider = StubEmbeddingProvider({"one": (1.0, 0.0, 0.0, 0.0)})
    cache = VectorCache(IDENTITY, provider=provider)

    assert cache.vector_for("one", label="first") == (1.0, 0.0, 0.0, 0.0)
    assert cache.added == 1
    assert cache.vector_for("one") == (1.0, 0.0, 0.0, 0.0)
    assert cache.added == 1
    assert len(provider.calls) == 1
    assert cache.as_document()["entries"][0]["label"] == "first"


def test_the_cache_normalizes_before_hashing_so_layout_is_not_identity():
    """A passage that gained a line break is the same text, not a second embedding."""
    provider = StubEmbeddingProvider({"one two": (1.0, 0.0, 0.0, 0.0)})
    cache = VectorCache(IDENTITY, provider=provider)

    assert cache.vector_for("one two") == cache.vector_for("one\n  two\n")
    assert len(cache) == 1
    assert text_key("one two") == text_key(" one \t two ")


def test_a_saved_cache_reloads_to_the_same_vectors(tmp_path):
    provider = StubEmbeddingProvider({"a": (0.5, 0.5, 0.5, 0.5), "b": (1.0, 0.0, 0.0, 0.0)})
    cache = VectorCache(IDENTITY, provider=provider)
    cache.vectors_for(("a", "b"), labels=("a", "b"))
    path = cache.save(tmp_path / "vectors.json")

    reloaded = VectorCache.load(path, IDENTITY)
    assert reloaded.vector_for("a") == (0.5, 0.5, 0.5, 0.5)
    assert reloaded.vector_for("b") == (1.0, 0.0, 0.0, 0.0)
    assert path.read_bytes() == VectorCache.load(path, IDENTITY).save(
        tmp_path / "again.json").read_bytes()


# -- §8.10-8.11 the scope as a contract -------------------------------------------------------------


def test_the_hybrid_scope_satisfies_the_candidate_scope_protocol(scope, passages):
    """Driven through the protocol, not asserted against it."""
    contract: OntologyCandidateScope = scope

    assert contract.name == "hybrid"
    text = passages[
        "norm:0001801169:0001801169-25-000037:q12025formxex991earningsre.htm#p14"]["text"]
    candidates = contract.candidates_for(text)
    assert isinstance(candidates, tuple)
    assert all(isinstance(c, str) for c in candidates)
    assert list(candidates) == sorted(candidates)
    assert candidates == scope.scope_for(text).concept_ids


def test_a_scope_whose_similarities_are_all_zero_is_exactly_the_lexical_scope(
    lexical, passages
):
    """The control. With nothing above the floor the hybrid scope must be indistinguishable
    from the lexical one — same ids, same reasons, same surfaces, same expansions."""
    scope = stub_scope(
        lexical,
        concepts={"homes_sold": (1.0, 0.0, 0.0, 0.0), "holding_costs": (0.0, 1.0, 0.0, 0.0)},
        texts={})

    for case in scope_runner.load_scope_cases():
        text = passages[case.passage_id]["text"]
        expected = lexical.scope_for(text)
        actual = scope.scope_for(text)
        assert actual.concept_ids == expected.concept_ids, case.case_id
        assert [c.reasons for c in actual.concepts] == [c.reasons for c in expected.concepts]
        assert [c.surfaces for c in actual.concepts] == [c.surfaces for c in expected.concepts]
        assert actual.expansions == expected.expansions


def test_the_floor_and_the_cap_each_do_their_own_job(lexical):
    """Two candidates above the floor, a cap of one; then a floor above both, and neither."""
    concepts = {
        "holding_costs": (1.0, 0.0, 0.0, 0.0),
        "market_count": (0.9, 0.4358898943540674, 0.0, 0.0),
    }
    text = {"unrelated wording": (1.0, 0.0, 0.0, 0.0)}

    capped = stub_scope(lexical, concepts=concepts, texts=text, top_k=1, min_similarity=0.5)
    assert capped.semantic_scope_for("unrelated wording").concept_ids == ("holding_costs",)

    floored = stub_scope(lexical, concepts=concepts, texts=text, top_k=5, min_similarity=0.95)
    assert floored.semantic_scope_for("unrelated wording").concept_ids == ("holding_costs",)

    both = stub_scope(lexical, concepts=concepts, texts=text, top_k=5, min_similarity=0.5)
    assert set(both.semantic_scope_for("unrelated wording").concept_ids) == set(concepts)


def test_the_ranking_is_deterministic_and_breaks_ties_by_concept_id(lexical):
    """A tie is common and meaningful here — four concepts sit within 0.016 of the top for the
    row label `Homes sold in period` — so the tie-break has to be stated, not incidental."""
    concepts = {"zebra": (1.0, 0.0, 0.0, 0.0), "alpha": (1.0, 0.0, 0.0, 0.0)}
    scope = stub_scope(
        lexical, concepts=concepts, texts={"tied": (1.0, 0.0, 0.0, 0.0)}, top_k=1)

    first = scope.ranked_for("tied")
    assert [n.concept_id for n in first] == ["alpha", "zebra"]
    assert [n.rank for n in first] == [0, 1]
    assert first == scope.ranked_for("tied")


# -- §8.12 the architectural rules stay green --------------------------------------------------------


def test_nothing_under_extraction_imports_the_benchmark():
    """Re-asserted here because this stage adds three modules under `extraction/` and a report
    generator that is deliberately not one of them."""
    import ast

    offenders = []
    for path in (REPO / "extraction").rglob("*.py"):
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


def test_the_scoping_stage_cannot_reach_a_provider():
    """The hybrid scope takes an `EmbeddingProvider` by contract and never names one.

    `test_provider.py::test_no_stage_can_reach_a_provider` covers every stage; this narrows it
    to the package that now has a reason to be tempted.
    """
    import ast

    offenders = []
    for path in (REPO / "extraction" / "stages" / "scoping").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if "provider" in name.lower() or name.split(".")[0] in ("httpx", "requests"):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []


# -- the embedding adapter, without a server -----------------------------------------------------------


def embedding_provider_on(handler, **overrides):
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    config = EmbeddingConfig(**overrides)
    client = httpx.Client(transport=httpx.MockTransport(recording))
    return LocalOpenAICompatibleEmbeddingProvider(
        config, client=client, sleep=lambda _s: None), calls


def free_port() -> int:
    """A port nothing is listening on. Bound and released so the number is genuinely unused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def unit_vector(dimensions: int, index: int) -> list[float]:
    vector = [0.0] * dimensions
    vector[index] = 1.0
    return vector


def embedding_envelope(vectors, *, indices=None) -> dict:
    """The shape the running llama.cpp server returns, read off the wire 2026-08-01."""
    indices = indices if indices is not None else range(len(vectors))
    return {
        "model": "m",
        "object": "list",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
        "data": [
            {"embedding": vector, "index": index, "object": "embedding"}
            for vector, index in zip(vectors, indices)
        ],
    }


def test_the_adapter_satisfies_the_embedding_provider_contract():
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json=embedding_envelope([unit_vector(1024, 0)])))
    contract: EmbeddingProvider = provider

    assert contract.model_id == "Qwen3-Embedding-0.6B-f16.gguf"
    assert contract.dimensions == 1024
    vectors = contract.embed(["one"])
    assert vectors == ((1.0,) + (0.0,) * 1023,)


def test_the_model_id_is_the_configured_one_never_the_response(embedding_config):
    """The server echoes the `model` string it was sent, so the wire cannot be believed about
    its own identity — and that identity is part of the cache key."""
    provider, calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json={
            "model": "whatever-we-asked-for", "object": "list",
            "data": [{"embedding": unit_vector(1024, 0), "index": 0}]}))

    provider.embed(["one"])
    assert provider.model_id == embedding_config.model
    assert json.loads(calls[0].content)["model"] == embedding_config.model


def test_vectors_are_reordered_by_index_not_zipped_positionally():
    """`data` is not promised in request order. A silently permuted batch would attach every
    vector to the wrong text and produce a plausible, wrong similarity table."""
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json=embedding_envelope(
            [unit_vector(1024, 1), unit_vector(1024, 0)], indices=[1, 0])),
        dimensions=1024)

    first, second = provider.embed(["a", "b"])
    assert second[1] == 1.0 and second[0] == 0.0
    assert first[0] == 1.0 and first[1] == 0.0


def test_a_vector_that_is_not_a_unit_vector_is_refused():
    """Without `--embd-normalize 2` the server returns norms of 12 to 30 for this model, and
    every cosine downstream would be wrong by an unknown factor while still looking sane."""
    raw = [0.5] * 1024  # norm 16.0
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json=embedding_envelope([raw])))

    with pytest.raises(ProviderResponseError) as raised:
        provider.embed(["one"])
    assert "norm" in str(raised.value)


def test_a_vector_of_the_wrong_width_is_refused():
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json=embedding_envelope([unit_vector(512, 0)])))

    with pytest.raises(ProviderResponseError) as raised:
        provider.embed(["one"])
    assert "512" in str(raised.value)


def test_an_oversize_input_is_a_response_error_and_is_not_retried():
    """Measured 2026-08-01: 3,002 tokens against `-c 2048` returns HTTP 400. Asking again
    cannot make the text shorter, so the shared retry rule must not."""
    def handler(_request):
        return httpx.Response(400, json={"error": {
            "code": 400, "type": "exceed_context_size_error",
            "message": "request (3002 tokens) exceeds the available context size"}})

    provider, calls = embedding_provider_on(handler)
    with pytest.raises(ProviderResponseError):
        provider.embed(["a very long passage"])
    assert len(calls) == 1


# -- the adapter's failure paths, with a fake transport ------------------------------------------
# `test_provider.py` is the precedent and these mirror it one for one. Every branch below was
# reachable only from a live server before, which is another way of saying it was untested: all
# four of emptying RETRYABLE_STATUSES, making health() answer `ok` unconditionally, raising
# ProviderTransportError instead of ProviderUnavailable on a refused connection, and removing the
# duplicate-`index` guard passed the whole suite.


def test_health_reports_ok_when_the_server_says_so():
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json={"status": "ok"}))
    status = provider.health()
    assert status.ok and status.status == "ok"
    assert status.detail is None


def test_health_on_a_closed_port_reports_unhealthy_rather_than_raising():
    """A server that is not running is an ordinary state, and `hybrid-build` branches on it.

    Against a real closed port, not a mock: the mock cannot produce the connect error that the
    `httpx.HTTPError` arm exists to catch.
    """
    provider = LocalOpenAICompatibleEmbeddingProvider(
        EmbeddingConfig(base_url=f"http://127.0.0.1:{free_port()}", timeout_seconds=2))
    try:
        status = provider.health()
    finally:
        provider.close()
    assert status.ok is False
    assert status.status in ("unavailable", "timeout")
    assert status.detail


def test_health_reports_a_loading_server_as_not_ok():
    """llama.cpp answers 503 while the model is still loading, and a vector built against a
    half-loaded server is not a vector this cache may key."""
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(503, json={"status": "loading model"}))
    assert provider.health().ok is False


def test_a_five_hundred_and_three_is_retried_to_the_bound_then_raises():
    """`max_retries` counts attempts *after* the first, so three attempts in total.

    The bound matters as much as the retry: an unbounded retry against a server that is down
    turns a cache build into a hang.
    """
    provider, calls = embedding_provider_on(
        lambda _r: httpx.Response(503, text="loading model"), max_retries=2)

    with pytest.raises(ProviderTransportError) as raised:
        provider.embed(["one"])
    assert len(calls) == 3
    assert "503" in str(raised.value)


def test_a_five_hundred_and_three_followed_by_a_success_returns_the_vector():
    """The other half of the same rule: a retry that succeeds must return, not raise."""
    responses = [
        httpx.Response(503, text="loading model"),
        httpx.Response(200, json=embedding_envelope([unit_vector(1024, 0)])),
    ]
    provider, calls = embedding_provider_on(lambda _r: responses.pop(0))

    assert provider.embed(["one"]) == ((1.0,) + (0.0,) * 1023,)
    assert len(calls) == 2


def test_a_timeout_raises_provider_timeout_and_is_not_multiplied():
    """Not retried on purpose: on a single-slot local server a timeout means the model is still
    working, and a second full wait behind the first turns one slow batch into a stall."""
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    provider, calls = embedding_provider_on(slow)
    with pytest.raises(ProviderTimeout):
        provider.embed(["one"])
    assert len(calls) == 1


def test_a_refused_connection_reports_unavailable_rather_than_a_generic_fault():
    """`ProviderUnavailable` and `ProviderTransportError` are different findings: nothing is
    listening on 8081 versus the server answered and the exchange failed. A caller deciding
    whether to tell the operator to start a server needs the first, and both are `ProviderError`
    so a test that only caught the base class would not notice the difference."""
    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    provider, calls = embedding_provider_on(refused, max_retries=1)
    with pytest.raises(ProviderUnavailable) as raised:
        provider.embed(["one"])
    assert len(calls) == 2
    assert "unreachable" in str(raised.value)


def test_two_embeddings_carrying_the_same_index_are_refused_by_name():
    """The failure that would otherwise be silent and wrong rather than loud.

    `data` is ordered by each element's `index`, so two elements claiming index 0 would write
    the same slot twice and leave one input with no vector. Both the guard and its absence raise
    `ProviderResponseError` — without the guard the *missing* check catches it one step later —
    so the assertion is on which failure it is. The difference is not cosmetic: "two embeddings
    carry index 0" says the server permuted a batch, and "no embedding returned for inputs [1]"
    says it returned too few, and only the first is a reason to distrust every vector in the
    response.
    """
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json=embedding_envelope(
            [unit_vector(1024, 0), unit_vector(1024, 1)], indices=[0, 0])))

    with pytest.raises(ProviderResponseError) as raised:
        provider.embed(["a", "b"])
    assert "two embeddings carry index 0" in str(raised.value)


def test_an_index_outside_the_batch_is_refused():
    provider, _calls = embedding_provider_on(
        lambda _r: httpx.Response(200, json=embedding_envelope(
            [unit_vector(1024, 0)], indices=[7])))

    with pytest.raises(ProviderResponseError) as raised:
        provider.embed(["a"])
    assert "outside" in str(raised.value)


def test_an_empty_request_never_reaches_the_network():
    """llama.cpp answers `{"input": []}` with HTTP 500; a caller with nothing to embed has not
    made an error."""
    provider, calls = embedding_provider_on(
        lambda _r: httpx.Response(500, text="should not be called"))

    assert provider.embed([]) == ()
    assert calls == []


def test_batches_are_split_at_the_configured_size():
    responses = []

    def handler(request):
        texts = json.loads(request.content)["input"]
        responses.append(len(texts))
        return httpx.Response(200, json=embedding_envelope(
            [unit_vector(1024, i % 1024) for i in range(len(texts))]))

    provider, _calls = embedding_provider_on(handler, batch_size=2)
    provider.embed(["a", "b", "c", "d", "e"])
    assert responses == [2, 2, 1]


def test_an_embedding_configuration_without_a_model_is_refused():
    """The model name is the only statement of which model produced a cached vector."""
    with pytest.raises(ProviderConfigurationError):
        EmbeddingConfig(model="").validated()
    with pytest.raises(ProviderConfigurationError):
        EmbeddingConfig(dimensions=0).validated()


def test_the_shipped_embedding_config_matches_the_validated_runtime(embedding_config):
    assert embedding_config.base_url == "http://127.0.0.1:8081"
    assert embedding_config.model == "Qwen3-Embedding-0.6B-f16.gguf"
    assert embedding_config.dimensions == 1024


def test_the_shipped_scoping_config_is_the_measured_rule(scoping_config):
    """Config, not code. The two numbers are the ones the report derives and defends."""
    assert scoping_config.strategy == "lexical"
    assert scoping_config.renderer_version == "v1"
    assert scoping_config.top_k == 2
    assert scoping_config.min_similarity == 0.45


def test_the_scoping_config_carries_no_key_without_a_consumer():
    """STAGE_09 §11.5. `cache_root` validated and did nothing, so it is gone.

    Stated as an assertion rather than as a note because "configuration that validates and does
    nothing" is exactly the state that survives a review unnoticed and is discovered a stage
    later, when somebody sets it and it has no effect.
    """
    import yaml

    raw = yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8"))
    hybrid = raw["scoping"]["hybrid"]
    assert "cache_root" not in hybrid
    assert set(hybrid) == {"renderer_version", "top_k", "min_similarity"}
    assert not hasattr(ScopingConfig(), "cache_root")


def test_a_scoping_config_naming_an_unimplemented_renderer_is_refused():
    """A `v2` renderer_version with `v1` code would embed v1 strings under a v2 cache key —
    the one substitution the cache key exists to make impossible."""
    with pytest.raises(ValueError):
        ScopingConfig.from_config({"scoping": {"hybrid": {"renderer_version": "v2"}}})


def test_an_unrecognised_strategy_is_rejected_at_load_and_names_itself():
    """`strategy` has no consumer until step 10, which is exactly why it must fail at load.

    A key nothing reads and nothing validates is a key that silently means whatever was typed.
    """
    with pytest.raises(ValueError) as raised:
        ScopingConfig.from_config({"scoping": {"strategy": "semantic"}})
    assert "semantic" in str(raised.value)
    assert "lexical" in str(raised.value) and "hybrid" in str(raised.value)


# -- the two derived numbers, held to the statistics that derived them ------------------------------


def test_top_k_is_the_maximum_of_the_standout_histogram(report, scoping_config):
    """STAGE_09 §11.3: `top_k` is the largest number of concepts standing clear of a text's own
    field at its own mean + 3 sd. That sentence is the whole justification for the cap, and
    §11.3 says the verdict rests on it — so it is asserted rather than narrated.

    Without this, the report can print "so the cap is the maximum observed, 9" beside a config
    saying `top_k: 2` and every test stays green.
    """
    standout = report.distribution["standout_3sd_counts"]
    histogram = {int(count): texts for count, texts in standout["histogram"].items()}

    assert standout["max"] == max(histogram), "the stated maximum is not the histogram's"
    assert sum(histogram.values()) == report.distribution["texts"]
    assert scoping_config.top_k == standout["max"]
    assert report.selection["top_k"] == standout["max"]

    # And the derivation survives the double counting: 32 texts are 26 distinct texts, and the
    # cap must not be an artifact of six passages being pooled more than once (§11.3a).
    distinct = standout["distinct"]
    assert distinct["texts"] == report.distribution["distinct_texts"]
    assert distinct["max"] == standout["max"]


def test_min_similarity_is_the_pooled_mean_plus_one_sd_rounded(report, scoping_config):
    """STAGE_09 §11.3: `min_similarity` = pooled mean + 1 sd, rounded to two places.

    Both halves are checked — that the report's own arithmetic holds, and that the shipped
    number is what it produces. Without the second, the report can print "0.3245 + 0.1286 =
    0.7103, rounded to 0.45" and stay green.
    """
    pooled = report.distribution["pooled_all_texts"]

    assert pooled["mean_plus_1sd"] == pytest.approx(pooled["mean"] + pooled["sd"], abs=1e-5)
    assert scoping_config.min_similarity == round(pooled["mean_plus_1sd"], 2)
    assert report.selection["min_similarity"] == round(pooled["mean_plus_1sd"], 2)

    # The pool is an argument (§11.3b), so the shipped pool must be the one the table names as
    # shipped, and it must be the one the floor came from.
    shipped = report.distribution["min_similarity_pools"][0]
    assert shipped["pool"].endswith("(as shipped)")
    assert shipped["texts"] == report.distribution["texts"]
    assert shipped["rounded"] == scoping_config.min_similarity


def test_dropping_the_gold_derived_probes_moves_neither_derived_number(report, scoping_config):
    """The two paraphrase probes are the wordings of stage 8's known misses, so they are the
    only texts in the pool derived from the gold answer. If either derived number depended on
    them, the derivation would be fitted to the answer it is supposed to be independent of.

    This is the measurement behind "no material gold leakage", which §11.3b requires to be
    visible rather than asserted.
    """
    pools = {row["pool"]: row for row in report.distribution["min_similarity_pools"]}
    without = pools["minus the two paraphrase probes"]

    assert without["texts"] == report.distribution["texts"] - 2
    assert without["rounded"] == scoping_config.min_similarity

    standout = report.distribution["standout_3sd_counts"]
    probe_names = {probe["name"] for probe in hybrid_scope_runner.PARAPHRASE_PROBES}
    remaining = [row["standout_3sd"] for name, row in report.distribution["per_text"].items()
                 if name not in probe_names]
    assert max(remaining) == standout["max"] == scoping_config.top_k


# -- §8.13 the committed report ------------------------------------------------------------------------


def test_regenerating_the_hybrid_report_twice_is_byte_identical(
    tmp_path, report, repo_config
):
    """Two full generations, not one object rendered twice."""
    def generate(directory: Path) -> tuple[bytes, bytes]:
        built = hybrid_scope_runner.build_report(
            catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)
        json_path, markdown_path = hybrid_scope_runner.write_reports(built, directory)
        return json_path.read_bytes(), markdown_path.read_bytes()

    first = generate(tmp_path / "first")
    second = generate(tmp_path / "second")
    assert first[0] == second[0]
    assert first[1] == second[1]


def test_the_committed_hybrid_json_is_byte_for_byte_what_a_fresh_run_renders(report):
    committed = _blank_commit_in_json(COMMITTED_JSON.read_text(encoding="utf-8"))
    fresh = _blank_commit_in_json(hybrid_scope_runner.render_json(report))
    assert committed.encode("utf-8") == fresh.encode("utf-8")


def test_the_committed_hybrid_markdown_is_byte_for_byte_what_a_fresh_run_renders(report):
    committed = _blank_commit_in_markdown(COMMITTED_MARKDOWN.read_text(encoding="utf-8"))
    fresh = _blank_commit_in_markdown(hybrid_scope_runner.render_markdown(report))
    assert committed.encode("utf-8") == fresh.encode("utf-8")


def test_the_report_carries_no_duration_or_timestamp():
    """STAGE_09 §6 asks for both timings and byte-identity, and those contradict. Byte-identity
    won; this is what stops the other half creeping back in."""
    text = COMMITTED_JSON.read_text(encoding="utf-8")
    for forbidden in ("latency", "elapsed", "_ms", "seconds", "timestamp", "generated_at"):
        assert forbidden not in text, forbidden
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", text)


def test_the_report_regenerates_offline_from_the_committed_caches(repo_config):
    """No provider anywhere in the path. A missing vector raises rather than scoring zero, so
    a green run here is evidence the committed caches are complete."""
    built = hybrid_scope_runner.build_report(
        catalog_root=repo_config.catalog_root, implementation_commit=PINNED_COMMIT)
    assert built.embedding["concepts_indexed"] == 131
    assert built.views["hybrid"].totals["cases"] == 26


def test_the_decision_block_is_computed_from_the_scores_it_reports(report):
    """The verdict is derived, and this is what stops it becoming a sentence someone typed."""
    decision = report.decision
    criteria = {c["criterion"]: c for c in decision["criteria"]}
    scores = report.views["hybrid"].totals["scores"]

    assert criteria[1]["measured"]["required_concept_recall"] == (
        scores["required_concept_recall"])
    assert criteria[2]["holds"] == (
        scores["critical_concept_recall"] == 1.0 and scores["ambiguity_preservation"] == 1.0)
    assert criteria[4]["measured"]["violations"] == []
    if all(c["holds"] for c in decision["criteria"]):
        assert decision["verdict"] == "hybrid becomes the default"
    elif not criteria[1]["holds"]:
        assert decision["verdict"] in (
            "lexical stays the default",
            "founder gate: the benchmark cannot distinguish the two strategies")


def test_the_committed_report_and_the_shipped_config_agree(report, scoping_config):
    """A report describing `top_k` 3 beside a config saying 2 would be worse than either."""
    config = scoping_config
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    assert committed["selection"]["top_k"] == config.top_k
    assert committed["selection"]["min_similarity"] == config.min_similarity
    assert report.selection["top_k"] == config.top_k

    # And the strategy in config is the one the report's verdict licenses.
    if committed["decision"]["verdict"] != "hybrid becomes the default":
        assert config.strategy == "lexical"


def test_the_benchmark_readme_does_not_restate_the_hybrid_results(report):
    """A number copied into a README is a number no test checks and nothing regenerates.

    Fabricating a recall of 1.000, a scope mean of 99.999, a verdict of "hybrid becomes the
    default" and a cache of 500 entries in this file left the whole suite green — the same
    defect step 6 fixed, relocated. The fix is that the hybrid section states no result at all
    and points at the report; this asserts the section stays that way, and that its one factual
    claim about the report's contents is true.
    """
    readme = (runner.PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    # Ends at the next section rather than at "## Review status": step 11 added a narrative
    # section between them, and a slice that swallowed it would fail this test on numbers that
    # are not the hybrid scope's. The narrative section is held to the same rule by
    # `test_narrative_lane_report.py`.
    section = readme[readme.index("### Hybrid scope"):readme.index("### Narrative lane")]

    for verdict in ("hybrid becomes the default", "lexical stays the default",
                    "founder gate"):
        assert verdict not in section, verdict
    for score in ("required-concept recall |", "scope size, mean",
                  "critical-concept recall |"):
        assert score not in section, score
    # No measured figure at all — neither a ratio nor a count. `500 entries` for a 132-entry
    # cache was one of the fabrications that survived, and it is an integer, so integers are
    # checked too. Four numbers are allowed and none of them is a result: the section reference
    # `§1.1a`, the determinism contract's tolerance (a fixed clause of that section), and the
    # port the embedding server listens on. Digits inside an identifier — `sha256`,
    # `pct_homes_on_market_gt_120_days`, `STAGE_09` — are not numbers a reader could read as a
    # result, and the boundaries exclude them.
    allowed = {"1", "1.1", "0.9999", "8081"}
    stray = [n for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", section)
             if n not in allowed]
    assert stray == [], stray

    assert "reports/hybrid_scope_v1.md" in section
    assert "unrequired additions" in section
    # The one number the section does carry is the contract tolerance, and it is the clause's.
    assert "cosine ≥ 0.9999" in section


def test_the_benchmark_readme_lexical_gates_match_the_committed_report():
    """The lexical section does still quote three gate numbers, so they are checked.

    Kept rather than deleted because STAGE_08's three gates are the reason that section exists,
    and one of them is a *failure* a reader should meet before the report. Quoting them is only
    admissible if something fails when they drift.
    """
    from benchmarks.extraction.v1 import scope_runner as lexical_runner

    readme = (runner.PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    committed = json.loads(
        (runner.REPORTS_DIR / f"{lexical_runner.REPORT_STEM}.json").read_text(
            encoding="utf-8"))
    scores = committed["totals"]["scores"]
    denominators = committed["totals"]["denominators"]

    for key, denominator in (("required_concept_recall", "required_concepts"),
                             ("critical_concept_recall", "critical_checks"),
                             ("ambiguity_preservation", "ambiguity_cases")):
        total = denominators[denominator]
        hit = round(scores[key] * total)
        assert f"{scores[key]:.3f}** ({hit}/{total})" in readme \
            or f"{scores[key]:.3f} ({hit}/{total})" in readme, key


def test_the_three_views_are_all_scored_over_every_case(report):
    """The denominator is derived from the gold rather than written down.

    It used to read `== 49`, which is the number the benchmark happened to carry on the day
    it was written. The 2026-08-02 correction to `population-portfolio-mdna-fy2023-10k`
    added one gold claim and this test failed with `50 == 49` — the right thing to happen,
    but the wrong reason to have to edit a test, because a literal cannot distinguish "the
    gold changed" from "a view silently stopped scoring a case". Recomputing it from the
    case files tests the claim in the name: every view is scored over the whole population,
    and all three agree on what that population is.
    """
    cases = _load_case_files()
    expected_cases = len(cases)
    expected_concepts = len({(c["case_id"], claim["metric_id"])
                             for c in cases for claim in (c.get("gold_claims") or [])})
    for view in hybrid_scope_runner.VIEWS:
        assert len(report.views[view].cases) == expected_cases
        assert (report.views[view].totals["denominators"]["required_concepts"]
                == expected_concepts), view


def _load_case_files() -> list[dict]:
    cases: list[dict] = []
    for path in sorted((runner.PACKAGE_ROOT / "cases").glob("*.yaml")):
        cases.extend(yaml.safe_load(path.read_text(encoding="utf-8"))["cases"])
    return cases


def test_the_embedding_only_view_is_a_diagnostic_that_fails_ambiguity(report):
    """It is in the report to be beaten, not to be adopted. If it ever stopped failing, the
    thing to check would be whether it had quietly acquired protected reasons."""
    scores = report.views["embedding_only"].totals["scores"]
    assert scores["ambiguity_preservation"] < 1.0
    assert scores["critical_concept_recall"] < 1.0
    assert report.views["embedding_only"].totals["counts_by_reason"][SEMANTIC_NEIGHBOUR] > 0
    for reason in PROTECTED_REASONS:
        assert report.views["embedding_only"].totals["counts_by_reason"][reason] == 0


def test_the_renderer_ablation_is_reproduced_in_the_report(report, ontology):
    """STAGE_09 §2.1 requires this measurement in the report, because it is the evidence that
    the sentence-level recovery is semantic rather than a transcription matching itself."""
    assert len(report.ablation) == 2
    for row in report.ablation:
        assert row.concept_id == "pct_homes_on_market_gt_120_days"
        assert row.rank_v1 == 0
        assert row.rank_with_variants == 0
        # The arm that includes the corpus-fitted wording cannot score lower; if it did, the
        # exclusion would be doing something other than what §2.1 claims.
        assert row.similarity_with_variants >= row.similarity_v1


def test_the_ablation_ranks_through_the_scope_and_not_a_second_implementation(report, scope):
    """The `v1` arm of the ablation must be the scope's own ranking, to the last decimal.

    It re-implemented the ranking with its own rounding constant, equal to the scope's by
    coincidence. Equal-by-coincidence is not a property, and the first time the two constants
    diverge the ablation quietly stops measuring the index the scope actually uses.
    """
    for row in report.ablation:
        ranking = scope.ranked_for(row.probe)
        entry = next(n for n in ranking if n.concept_id == row.concept_id)
        assert (row.rank_v1, row.similarity_v1) == (entry.rank, entry.similarity), row.probe


def test_the_sweep_computes_every_criterion_at_every_cap(report, scope, passages):
    """B11: criteria 2 and 4 are computed at other `top_k`, not asserted in prose.

    §11.3 says the founder decision turns on the sentence "at `top_k` 3 every §7 criterion
    holds". Checking a row against a scope rebuilt at that cap is what makes the sentence a
    measurement — a hard-coded `True` in the runner would otherwise read identically.
    """
    keys = {"required_concept_recall", "critical_concept_recall", "known_instance_recall",
            "ambiguity_preservation", "criterion_2_holds", "criterion_3_holds",
            "criterion_4_holds", "scope_size_mean", "unrequired_additions"}
    assert [row["top_k"] for row in report.sensitivity] == list(range(1, 9))
    for row in report.sensitivity:
        assert keys <= set(row), row["top_k"]

    # The `top_k` 3 row, rebuilt independently through the scope at that cap: the mean it
    # reports and the criterion-4 claim it makes must both hold against a real scope, not
    # against a constant the runner wrote down.
    checked = next(row for row in report.sensitivity if row["top_k"] == 3)
    sizes = []
    for case in scope_runner.load_scope_cases():
        text = passages[case.passage_id]["text"]
        lexical = scope.lexical.scope_for(text)
        merged = scope.merge(lexical, scope.select(scope.ranked_for(text), top_k=3))
        sizes.append(len(merged))
        assert set(merged.protected_ids) == set(lexical.concept_ids), case.case_id
    assert checked["scope_size_mean"] == pytest.approx(sum(sizes) / len(sizes), abs=1e-6)
    assert checked["criterion_4_holds"] is True


class _SweepCase:
    def __init__(self, scope):
        self.scope = scope


class _SweepView:
    """The two things `_sensitivity` asks of the lexical view, and nothing else."""

    def __init__(self, cases, mean):
        self._cases = cases
        self.totals = {"scope_size": {"mean": mean}}

    def case(self, case_id):
        return self._cases[case_id]


def test_the_sweep_says_criterion_two_fails_when_it_actually_fails(scope, passages, ontology):
    """The sweep's criteria must be computed, and on this corpus every one of them holds.

    That is the problem: replacing `criterion_2_holds` with a literal `True` produces a report
    identical to the real one, and a test comparing the flag to the row's own scores agrees with
    the literal too. So the sweep is driven over a *doctored* lexical half — emptied, so the
    union is the semantic candidates alone — where criterion 2 must fail. A hardcoded flag says
    it holds; a computed one says it does not.

    The `embedding_only` view of the real report is the same situation and is why it exists:
    retrieval alone loses ambiguity preservation and critical-concept recall.
    """
    cases = scope_runner.load_scope_cases()
    empty = {case.case_id: _SweepCase(CandidateScope(concepts=())) for case in cases}
    rankings = {case.case_id: scope.ranked_for(passages[case.passage_id]["text"])
                for case in cases}
    views = {"lexical": _SweepView(empty, mean=17.9)}
    instance_ids = {i.instance_id for i in ontology.registry.definitions.instances}

    rows = hybrid_scope_runner._sensitivity(
        cases, views, rankings, scope, instance_ids=instance_ids)

    assert any(not row["criterion_2_holds"] for row in rows), \
        "criterion 2 is not being computed: it cannot hold with no lexical candidates"
    for row in rows:
        assert row["criterion_2_holds"] == (
            row["critical_concept_recall"] == 1.0
            and row["ambiguity_preservation"] == 1.0), row["top_k"]
        # Criterion 4 still holds here: an empty lexical set is trivially preserved. That is
        # the right answer, and it shows the two criteria are not the same computation twice.
        assert row["criterion_4_holds"] is True


class _LeakyScope:
    """A scope whose union lets a semantic candidate become **protected**.

    The failure criterion 4 exists to detect, and one the real scope cannot be made to produce
    — `merge` adds `semantic_neighbour` and nothing else, which is the whole structural
    argument. So it is produced here instead, because a criterion that can only ever be
    computed as `True` on real data is indistinguishable from a literal `True`.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def select(self, ranking, *, top_k=None):
        return self._inner.select(ranking, top_k=top_k)

    def merge(self, lexical, neighbours):
        merged = self._inner.merge(lexical, neighbours)
        lexical_ids = set(lexical.concept_ids)
        return CandidateScope(
            concepts=tuple(
                candidate if candidate.concept_id in lexical_ids else ScopedConcept(
                    concept_id=candidate.concept_id,
                    reasons=tuple(sorted(set(candidate.reasons) | {"exact_alias"})),
                    surfaces=candidate.surfaces)
                for candidate in merged.concepts),
            expansions=merged.expansions,
            reason_vocabulary=merged.reason_vocabulary)


def test_the_sweep_says_criterion_four_fails_when_a_semantic_candidate_becomes_protected(
    scope, passages, ontology
):
    """Criterion 4 at every cap, computed rather than assumed.

    On the real scope it holds structurally, so a literal `True` reads identically. Driven
    through a union that deliberately promotes a semantic candidate into the protected set, a
    computed criterion 4 must report the violation.
    """
    cases = scope_runner.load_scope_cases()
    lexical_cases = {case.case_id: _SweepCase(
        scope.lexical.scope_for(passages[case.passage_id]["text"])) for case in cases}
    rankings = {case.case_id: scope.ranked_for(passages[case.passage_id]["text"])
                for case in cases}
    views = {"lexical": _SweepView(lexical_cases, mean=17.923077)}
    instance_ids = {i.instance_id for i in ontology.registry.definitions.instances}

    rows = hybrid_scope_runner._sensitivity(
        cases, views, rankings, _LeakyScope(scope), instance_ids=instance_ids)

    assert any(not row["criterion_4_holds"] for row in rows), \
        "criterion 4 is not being computed: a promoted semantic candidate went unreported"


def test_no_artifact_of_this_stage_calls_an_unrequired_addition_a_false_positive(report):
    """C1. The benchmark annotates a deliberate subset, so an unannotated addition is
    *unmeasured*, not wrong. Naming it a false positive would assert a measurement this
    benchmark does not make — the same reason step 6 refuses to call its ratio precision.

    Checked across the generated Markdown, the JSON and the runner — the three places where the
    phrase would be *describing* the additions. STAGE_09 §6 and the benchmark README are
    deliberately not checked: they state the prohibition, which requires naming it.
    """
    markdown = COMMITTED_MARKDOWN.read_text(encoding="utf-8").lower()
    payload = COMMITTED_JSON.read_text(encoding="utf-8").lower()
    source = (Path(hybrid_scope_runner.__file__)).read_text(encoding="utf-8").lower()

    for text, where in ((markdown, "markdown"), (payload, "json"), (source, "runner")):
        assert "false positive" not in text, where
        assert "false-positive" not in text, where

    semantic = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))["semantic_only"]
    assert "unrequired_additions" in semantic
    assert "additions" not in semantic
    assert "unrequired additions" in markdown


def test_the_unreached_prose_names_the_probe_that_belongs_to_the_missed_case(report):
    """B6: the paragraph beneath the table followed `paraphrase-2` by name.

    It agreed with the data by coincidence — the table above it iterates the missed set, and the
    prose quoted one hard-coded probe and only the first missed row. If the *other* paraphrase
    were the one out of reach, the report would have quoted the wrong sentence's similarity
    beside the right case's rank and read entirely plausibly.
    """
    markdown = hybrid_scope_runner.render_markdown(report)
    missed = report.decision["criteria"][0]["measured"]["paraphrases_missed"]
    probe_of_case = {p["case_id"]: p["name"]
                     for p in hybrid_scope_runner.PARAPHRASE_PROBES}

    for case_id, _concept_id in missed:
        probe = probe_of_case[case_id]
        sentence = report.distribution["per_text"][probe]
        passage = report.distribution["per_text"][case_id]
        assert f"For `{case_id}`: the sentence alone" in markdown
        assert f"`{probe}` probe, {sentence['characters']} characters" in markdown
        assert f"{passage['characters']}-character passage" in markdown
        # And the similarity quoted is the probe's, not the passage's.
        assert f"rank 0, {sentence['top_1_similarity']:.4f}" in markdown

    # Not the reverse: a probe belonging to a case that was *reached* must not be quoted.
    reached = set(probe_of_case) - {case_id for case_id, _c in missed}
    for case_id in reached:
        assert f"For `{case_id}`: the sentence alone" not in markdown


def test_the_scope_reports_share_one_implementation_of_their_helpers():
    """B5: `_ratio`, `_anchor` and `_cell` existed in two and three copies.

    Identity, not equality — a re-implementation that happens to agree today is the state that
    lets two reports in one directory round to different places without a diff showing it.
    """
    from benchmarks.extraction.v1 import scope_runner as lexical_runner

    assert lexical_runner._ratio is runner.ratio
    assert lexical_runner._anchor is runner.anchor
    assert lexical_runner._cell is runner.cell
    assert hybrid_scope_runner._anchor is runner.anchor
    assert hybrid_scope_runner._cell is runner.cell

    for module in (lexical_runner, hybrid_scope_runner):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for name in ("def _ratio(", "def _anchor(", "def _cell("):
            assert name not in source, f"{module.__name__} redefines {name}"


def test_both_paraphrases_are_recovered_at_sentence_granularity(report):
    """The finding the two probes exist for, separated from the passage-level question."""
    for name in ("paraphrase-1", "paraphrase-2"):
        probe = next(p for p in report.probes if p.name == name)
        assert probe.head[0].concept_id == "pct_homes_on_market_gt_120_days"
        assert probe.head[0].rank == 0
        assert probe.head[0].similarity > 0.8
        assert "pct_homes_on_market_gt_120_days" in {
            n.concept_id for n in probe.selected}


def test_the_ablation_arm_is_the_only_extra_entry_in_the_concept_cache(ontology,
                                                                      embedding_config):
    """132 entries for 131 concepts. Anything else means a stale rendering is cached beside a
    current one, which is the state the cache key cannot detect because both are `v1`."""
    identity = CacheIdentity(
        ontology.definition_hash, embedding_config.model, embedding_config.dimensions)
    cache = VectorCache.load(hybrid_scope_runner.CONCEPT_VECTORS, identity)
    rendered = set(concept_vectors(ontology, cache))
    assert len(rendered) == 131
    assert len(cache) == 132
