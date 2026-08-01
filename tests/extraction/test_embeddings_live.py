"""The live gate for the embedding runtime: the real server on 8081, the real vectors.

Marked `live` so `pytest -m "not live"` stays green on a machine with no model. Mocks cannot
satisfy this file by design — `test_hybrid_scoping.py` proves the wire contract and the cache
rules, and this one proves the wire contract was right about the server that is running.

Recorded on 2026-08-01 against llama.cpp serving `Qwen3-Embedding-0.6B-f16.gguf` with
`--pooling last --embd-normalize 2 -ngl 99 -c 2048 -ub 2048`:

| | |
| --- | --- |
| `/health` | `{"status":"ok"}` |
| dimensions | 1024 |
| vector norm | 1.0000000 within 1e-6 |
| determinism | **conditional** — see below |
| oversize input | HTTP 400 `exceed_context_size_error` at 3,002 tokens against `-c 2048` |

**STAGE_09 §1.1 originally said two identical requests return bit-identical vectors. They do
not**, and this file is where the correction is pinned. The vector depends on the request that
preceded it in the slot: `A, C, C` yields two different `C` vectors, `A, C … A, C` yields the
same one twice. The disagreement is up to 1.3e-4 per component, cosine 0.9999996 — no ranking
in the benchmark moves, and byte-identical regeneration is not a property the code can promise.
`cache_prompt: false` was tried and changes nothing.

**What these tests assert is the contract, never the artifact.** STAGE_09 §1.1a states it as
five clauses; clauses 2 and 3 are the live ones and are asserted here at exactly their stated
thresholds:

2. two compatible requests for the same text agree to **cosine ≥ 0.9999**;
3. a scope built on rebuilt vectors selects **identical** candidates for all 26 benchmark
   passages.

An earlier version of this file asserted the *inequality* — that two back-to-back requests come
back different. That pinned a llama.cpp scheduling artifact as a requirement: the suite would
have gone red the day the server is fixed, run single-slot, or replaced with one that is
genuinely deterministic, none of which is a regression in anything this repository owns. The
non-determinism is a **measurement**, recorded above and in §11.1; it is not a requirement, and
nothing here demands it.

`-ub 2048` remains load-bearing: the longest rendered concept exceeds the 512-token default
physical batch.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from benchmarks.extraction.v1 import hybrid_scope_runner
from extraction.providers import (
    EmbeddingConfig,
    LocalOpenAICompatibleEmbeddingProvider,
)
from extraction.stages.scoping import CacheIdentity, VectorCache, concept_vectors
from ontology import load_ontology

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def config():
    return EmbeddingConfig.from_config(
        yaml.safe_load((REPO / "config" / "extraction.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def provider(config):
    with LocalOpenAICompatibleEmbeddingProvider(config) as live:
        if not live.health().ok:
            pytest.skip(f"no embedding server at {config.base_url}")
        yield live


@pytest.fixture(scope="module")
def ontology():
    return load_ontology()


def test_the_server_is_up_and_returns_unit_vectors_of_the_configured_width(provider, config):
    health = provider.health()
    assert health.ok and health.status == "ok"

    (vector,) = provider.embed(["Homes sold in period"])
    assert len(vector) == config.dimensions == 1024
    norm = math.sqrt(sum(value * value for value in vector))
    assert abs(norm - 1.0) < 1e-6, norm


AGREEMENT_COSINE = 0.9999
"""STAGE_09 §1.1a clause 2, verbatim. Named so the two tests that use it cannot drift apart."""


def test_repeated_requests_agree_to_the_contract_tolerance(provider):
    """Clause 2 of §1.1a, asserted at exactly its stated threshold and at nothing else.

    The same text is embedded three times under two different request histories — back to back,
    and after an unrelated predecessor. Every pair must agree to cosine ≥ 0.9999. Whether any
    particular pair is *bit*-identical is deliberately not asserted in either direction: clause
    1 says byte identity is not required, and requiring its opposite would pin the scheduler.

    Observed on 2026-08-01 for the record, and not as a requirement: the two vectors taken after
    the same predecessor were bit-identical, the back-to-back pair differed by up to 1.3e-4 per
    component at cosine 0.9999996. Both satisfy the clause; so would a server on which all three
    were identical.
    """
    first, second = "Homes sold in period", "Contribution Margin was 5.4% in the quarter"

    provider.embed([first])
    after_first = provider.embed([second])[0]
    back_to_back = provider.embed([second])[0]
    provider.embed([first])
    after_first_again = provider.embed([second])[0]

    for label, other in (("back to back", back_to_back),
                         ("after the same predecessor", after_first_again)):
        cosine = math.fsum(a * b for a, b in zip(after_first, other))
        assert cosine >= AGREEMENT_COSINE, (label, cosine)


def test_a_rebuilt_concept_cache_agrees_with_the_committed_one_and_scopes_identically(
    provider, ontology, config, tmp_path
):
    """131 concepts plus the ablation arm, embedded from nothing, against the committed file.

    Clauses 2 and 3 of §1.1a together, and clause 4 by implication: the committed file is the
    authority and the rebuild is checked against it, never the other way round.

    Not a byte comparison. The server's output depends on its request history, so a rebuild
    inside a test session cannot reproduce the bytes of one built from a cold start, and
    asserting that would be asserting the scheduler rather than the model. What is asserted is
    what the report rests on — every vector agrees to cosine ≥ 0.9999, and the scope built on
    the rebuilt vectors admits exactly the same candidates for every one of the 26 benchmark
    passages. A different rendering, model or pooling flag fails both.

    Measured on this run *(2026-08-01)*: 127 of the 131 vectors came back bit-identical, four
    did not, and the worst disagreement was `regulator` at cosine 1 - 1.5e-5 with a largest
    single component difference of 6.5e-4. The clause's 0.9999 is that worst case with an order
    of magnitude of headroom, and is still four orders tighter than anything that could move a
    rank.
    """
    from benchmarks.extraction.v1 import runner, scope_runner
    from extraction.stages.scoping import HybridOntologyCandidateScope, cosine
    from extraction.stages.select import AliasIndex
    from extraction.stages.scoping import LexicalOntologyCandidateScope

    identity = CacheIdentity(ontology.definition_hash, config.model, config.dimensions)
    rebuilt = VectorCache(identity, provider=provider, path=tmp_path / "concepts.json")
    fresh = concept_vectors(ontology, rebuilt)
    concept_vectors(ontology, rebuilt, include_raw_variants=True)
    assert len(rebuilt) == 132

    committed = VectorCache.load(hybrid_scope_runner.CONCEPT_VECTORS, identity)
    known = concept_vectors(ontology, committed)
    assert set(fresh) == set(known)
    for concept_id, vector in fresh.items():
        agreement = cosine(vector, known[concept_id])
        assert agreement >= AGREEMENT_COSINE, (concept_id, agreement)

    catalog = runner.load_passages()
    lexical = LexicalOntologyCandidateScope(ontology, AliasIndex.from_ontology(ontology))
    texts = VectorCache.load(hybrid_scope_runner.TEXT_VECTORS, identity)
    on_committed = HybridOntologyCandidateScope(lexical, known, texts)
    on_rebuilt = HybridOntologyCandidateScope(lexical, fresh, texts)
    for case in scope_runner.load_scope_cases():
        text = catalog[case.passage_id]["text"]
        assert (on_rebuilt.candidates_for(text)
                == on_committed.candidates_for(text)), case.case_id


def test_an_input_longer_than_the_context_is_a_typed_error_not_a_hang(provider):
    """3,002 tokens against `-c 2048`. The server answers HTTP 400 with a typed body, and the
    adapter must surface that rather than retry it into a timeout."""
    from extraction.providers import ProviderResponseError

    with pytest.raises(ProviderResponseError) as raised:
        provider.embed(["word " * 3000])
    assert "400" in str(raised.value)
