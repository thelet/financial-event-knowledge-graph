"""The hybrid ontology candidate scope: the lexical scope, plus semantic neighbours.

**Add-only, structurally.** `scope_for` starts from the lexical scope's own answer and never
removes, reorders or re-reasons a candidate in it. A concept both stages find carries both
reasons and stays protected; a concept only the embedding finds carries `semantic_neighbour`
alone and is not protected. There is no code path here that can drop a protected candidate,
and that is the point of building the union this way rather than ranking a merged list
(STAGE_09 §4.1).

**The selection rule, and why it is shaped the way it is.** Every figure behind it lives in the
committed stage 9 hybrid-scope report — its distribution section, generated from the same
vectors this scope ranks with, and whose two derivations are asserted against the shipped
configuration by `tests/extraction/test_hybrid_scoping.py`. Measured constants are
deliberately **not** repeated here: a number in a docstring that no test holds to the report
is a number that drifts from it silently.

The shape, which is the part that belongs in the code:

The similarity distribution is a mixture whose location moves with the text, so one absolute
number does not mean the same thing to two texts. Length dilutes — a whole KPI table's best
concept sits about where a single sentence's tenth does, and the wide separation a true
single-fact paraphrase shows exists only at sentence granularity. An absolute threshold can
therefore only **floor**; rank has to **select**.

So `top_k` is the selector and `min_similarity` is the floor:

- `top_k` comes from the only scale-free statistic the distribution admits — how many concepts
  stand clear of a text's *own* field at its own mean + 3 sd — taken at its maximum over the
  measured texts.
- `min_similarity` comes from the pooled background: mean + 1 sd, rounded to two places. One sd
  above the pooled mean excludes roughly 84% of all pairs by construction. It is a statement
  about the background of the measurement and about no particular text.

Neither number was chosen against a gold answer, and the report states what recall and what
scope growth they produce rather than the other way round. The honest limitation is stated
there too: a fixed pair approximates the per-text z-threshold the statistic actually describes,
and this configuration interface cannot express an adaptive one. For most texts that threshold
would admit nothing at all, while a fixed `top_k` still admits two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .concept_rendering import RENDERER_VERSION, render_concepts
from .public import (
    SEMANTIC_NEIGHBOUR,
    SCOPE_REASONS,
    CandidateScope,
    ScopedConcept,
)
from .vector_cache import VectorCache, cosine

SCOPE_NAME = "hybrid"
SCOPE_VERSION = "1.0.0"

# Measured, not preferred. See the module docstring for the rule that produced each, and the
# stage 9 report for the statistics the rule was applied to.
DEFAULT_TOP_K = 2
DEFAULT_MIN_SIMILARITY = 0.45

# Similarities are rounded before they are compared to the threshold, not only before they are
# printed. A candidate the report shows at exactly `min_similarity` must be one the scope
# admitted, or the report and the scope disagree about their own boundary.
SIMILARITY_DIGITS = 6

STRATEGIES = ("lexical", "hybrid")


@dataclass(frozen=True)
class SemanticNeighbour:
    """One concept the embedding ranks for a text, with its evidence.

    `rank` is over the whole vocabulary, before `top_k` and before the floor, so a report can
    say "the metric was there at rank 2 and the cap was 2" — which is a different finding from
    "the model did not find it" and the two are indistinguishable from a filtered list.
    """

    concept_id: str
    similarity: float
    rank: int


@dataclass(frozen=True)
class ScopingConfig:
    """`scoping:` in `config/extraction.yaml`. Policy in config, never in code.

    `strategy` is the one key here with no consumer yet: step 10's composition root is what
    reads it to decide which scope to build. It is still validated at load, so an unknown value
    fails where it is written rather than at the first passage. It stays `lexical` until
    STAGE_09 §7's decision rule is satisfied by the committed report.

    There is deliberately **no `cache_root`**. STAGE_09 §3 promised one for a runtime cache
    under `data/embedding_cache/`, and nothing in this stage fills it: the vectors in play are
    the committed ones the stage 9 report reads, which is what makes that report regenerable
    offline. A key that validates and does nothing is worse than an absent one, so it arrives
    with the runtime that needs it (STAGE_09 §11.5).
    """

    strategy: str = "lexical"
    renderer_version: str = RENDERER_VERSION
    top_k: int = DEFAULT_TOP_K
    min_similarity: float = DEFAULT_MIN_SIMILARITY

    @classmethod
    def from_config(cls, config: dict) -> "ScopingConfig":
        scoping = config.get("scoping") or {}
        hybrid = scoping.get("hybrid") or {}
        loaded = cls(
            strategy=str(scoping.get("strategy", "lexical")),
            renderer_version=str(hybrid.get("renderer_version", RENDERER_VERSION)),
            top_k=int(hybrid.get("top_k", DEFAULT_TOP_K)),
            min_similarity=float(hybrid.get("min_similarity", DEFAULT_MIN_SIMILARITY)),
        )
        return loaded.validated()

    def validated(self) -> "ScopingConfig":
        if self.strategy not in STRATEGIES:
            raise ValueError(
                f"scoping.strategy must be one of {STRATEGIES}, not {self.strategy!r}")
        # A configuration naming a renderer this code does not implement would silently embed
        # `v1` strings under a `v2` cache key — the one substitution the cache key exists to
        # make impossible.
        if self.renderer_version != RENDERER_VERSION:
            raise ValueError(
                f"scoping.hybrid.renderer_version is {self.renderer_version!r} but this code "
                f"implements {RENDERER_VERSION!r}")
        if self.top_k < 1:
            raise ValueError("scoping.hybrid.top_k must be at least 1")
        if not 0.0 <= self.min_similarity <= 1.0:
            raise ValueError(
                "scoping.hybrid.min_similarity must be a cosine in [0.0, 1.0]")
        return self


def concept_vectors(
    ontology, cache: VectorCache, *, include_raw_variants: bool = False
) -> dict[str, tuple[float, ...]]:
    """`concept_id -> unit vector`, through the cache, for the whole vocabulary.

    Rendering happens here rather than inside the scope so that the ablation arm
    (`include_raw_variants=True`, STAGE_09 §2.1) is the same code path with one argument
    changed. Exactly one concept in this vocabulary carries `population.raw_variants`, so the
    two arms differ in one rendered string and the cache de-duplicates the other 130 by hash.
    """
    rendered = render_concepts(
        ontology.registry.definitions.concepts, include_raw_variants=include_raw_variants)
    concept_ids = sorted(rendered)
    vectors = cache.vectors_for(
        [rendered[concept_id] for concept_id in concept_ids], labels=concept_ids)
    return dict(zip(concept_ids, vectors))


class HybridOntologyCandidateScope:
    """Satisfies `extraction.contracts.OntologyCandidateScope`. Lexical ∪ semantic.

    Takes the lexical scope as a collaborator rather than subclassing or reimplementing it:
    the add-only guarantee is only worth anything if the lexical answer that goes in is the
    one that comes out, and this way there is exactly one implementation of it.
    """

    name = SCOPE_NAME
    version = SCOPE_VERSION

    def __init__(
        self,
        lexical,
        vectors: Mapping[str, tuple[float, ...]],
        text_cache: VectorCache,
        *,
        top_k: int = DEFAULT_TOP_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> None:
        self._lexical = lexical
        self._vectors = dict(vectors)
        self._texts = text_cache
        self._top_k = int(top_k)
        self._min_similarity = float(min_similarity)

    @property
    def top_k(self) -> int:
        return self._top_k

    @property
    def min_similarity(self) -> float:
        return self._min_similarity

    @property
    def lexical(self):
        return self._lexical

    def with_vectors(self, vectors: Mapping[str, tuple[float, ...]]) -> "HybridOntologyCandidateScope":
        """The same lexical half, the same text cache and the same rule, over another index.

        For the §2.1 renderer ablation, which is a question about the *vocabulary* and must
        differ from the configured scope in nothing else. Building a second ranking by hand
        there is how an ablation stops being an ablation.
        """
        return HybridOntologyCandidateScope(
            self._lexical, vectors, self._texts,
            top_k=self._top_k, min_similarity=self._min_similarity)

    @property
    def indexed_concepts(self) -> tuple[str, ...]:
        """The vocabulary the similarity is computed over. Sorted, and reported, because a
        scope that silently indexed 130 of 131 concepts would look exactly like one that
        found nothing for the missing one."""
        return tuple(sorted(self._vectors))

    # -- the protocol ----------------------------------------------------------------------

    def candidates_for(self, text: str) -> tuple[str, ...]:
        return self.scope_for(text).concept_ids

    def scope_for(self, text: str) -> CandidateScope:
        """The lexical scope unchanged, plus the selected semantic neighbours."""
        lexical = self._lexical.scope_for(text)
        return self.merge(lexical, self.neighbours_for(text))

    # -- the semantic half ------------------------------------------------------------------

    def ranked_for(self, text: str) -> tuple[SemanticNeighbour, ...]:
        """Every concept, by descending similarity. No threshold, no cap — the raw ranking.

        Ties break on `concept_id` so that two runs over identical vectors produce identical
        ranks. A tie is common and meaningful here: four concepts sit within 0.016 of the top
        for the row label `Homes sold in period`.
        """
        vector = self._texts.vector_for(text)
        scored = sorted(
            (
                (round(cosine(vector, concept_vector), SIMILARITY_DIGITS), concept_id)
                for concept_id, concept_vector in self._vectors.items()
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )
        return tuple(
            SemanticNeighbour(concept_id=concept_id, similarity=similarity, rank=rank)
            for rank, (similarity, concept_id) in enumerate(scored)
        )

    def select(
        self, ranking: Sequence[SemanticNeighbour], *, top_k: int | None = None
    ) -> tuple[SemanticNeighbour, ...]:
        """The floor and the cap applied to a ranking. The whole of the selection rule.

        Separated from `ranked_for` so that a caller holding a ranking already — the report's
        sensitivity curve, which asks what other caps would have produced over the *same*
        ranking — applies this rule rather than writing its own. `top_k` overrides the
        configured cap and nothing else: the floor is not a variable there.
        """
        above = [n for n in ranking if n.similarity >= self._min_similarity]
        return tuple(above[: self._top_k if top_k is None else int(top_k)])

    def neighbours_for(self, text: str) -> tuple[SemanticNeighbour, ...]:
        """The ranking after the floor and the cap. What `scope_for` actually adds."""
        return self.select(self.ranked_for(text))

    def semantic_scope_for(self, text: str) -> CandidateScope:
        """The semantic candidates alone. **Diagnostic only — never a runtime scope.**

        It isolates what retrieval can and cannot do on its own, which is the only way to say
        whether the hybrid's numbers come from the embedding or from the lexical half it
        contains. Nothing structural stops this `CandidateScope` being handed to a lane — it is
        the same type `scope_for` returns — so the guarantee is a naming and review one, not a
        type one: it drops every protected reason by construction, and the report measures it
        failing ambiguity preservation on purpose so that a reader can see what it costs.
        """
        return CandidateScope(
            concepts=tuple(
                ScopedConcept(concept_id=n.concept_id, reasons=(SEMANTIC_NEIGHBOUR,))
                for n in sorted(self.neighbours_for(text), key=lambda n: n.concept_id)
            ),
            reason_vocabulary=SCOPE_REASONS,
        )

    # -- the union --------------------------------------------------------------------------

    def merge(
        self, lexical: CandidateScope, neighbours: Sequence[SemanticNeighbour]
    ) -> CandidateScope:
        """Union by concept id. Nothing is dropped and no protected reason is touched.

        Public for the same reason `select` is: the report builds the same union at eight
        different caps, and a second implementation of it there is how the sensitivity table
        and the configured scope would start disagreeing about what "hybrid" means.

        The result is sorted by concept id, which is the order the lexical scope already
        produces, so every protected candidate keeps its relative position and the semantic
        ones interleave. Sorting by similarity would put an unprotected candidate ahead of a
        protected one in the serialised list — legal under §4.1, which forbids reordering
        *protected* candidates, and still the wrong signal to send a reader.
        """
        semantic = {n.concept_id for n in neighbours}
        existing = {concept.concept_id for concept in lexical.concepts}

        concepts = [
            concept if concept.concept_id not in semantic else ScopedConcept(
                concept_id=concept.concept_id,
                reasons=tuple(sorted(set(concept.reasons) | {SEMANTIC_NEIGHBOUR})),
                surfaces=concept.surfaces,
            )
            for concept in lexical.concepts
        ]
        concepts.extend(
            ScopedConcept(concept_id=concept_id, reasons=(SEMANTIC_NEIGHBOUR,))
            for concept_id in sorted(semantic - existing)
        )
        return CandidateScope(
            concepts=tuple(sorted(concepts, key=lambda c: c.concept_id)),
            expansions=lexical.expansions,
            reason_vocabulary=SCOPE_REASONS,
        )
