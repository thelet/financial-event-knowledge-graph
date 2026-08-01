"""Turning an ontology concept into the one string that gets embedded. Pure, versioned.

Two functions and two version constants, and nothing else may decide what an embedding sees.
The version constants are part of the vector cache's identity (STAGE_09 §3), so a change to
either function must change a constant or the cache silently starts mixing vectors produced
by two different renderings — which is the one failure a cosine score cannot show you.

**What is excluded is the substantive decision here** (STAGE_09 §2.1):

`source_evidence[].quote` holds real filing sentences. They are provenance for the
*definition*, not part of it, and embedding them would let a benchmark passage match itself
through the ontology rather than through meaning.

`population.raw_variants` holds wordings transcribed from the corpus. Exactly one concept
carries any — `pct_homes_on_market_gt_120_days`, whose variants include "our homes in
inventory", verbatim the wording of the second lexical miss stage 9 exists to recover.
Including it would make that recovery partly circular. `include_raw_variants` exists only so
the benchmark can measure what the exclusion costs and report it; no runtime path passes it.
"""

from __future__ import annotations

RENDERER_VERSION = "v1"

# The normalization applied to every string before it is embedded or hashed, versioned
# separately from the renderer because it also applies to passage text, which no renderer
# touches. v1 is whitespace folding and nothing else: the corpus is Markdown, so a table
# passage carries newlines and column padding that are layout rather than content, and two
# spellings of the same sentence must not become two cache entries.
TEXT_NORMALIZATION_VERSION = "v1"

FIELD_SEPARATOR = " | "


def normalize_for_embedding(text: str) -> str:
    """Fold every run of whitespace to one space and strip. The whole of `v1`.

    Deliberately not the typography fold `core.concept_resolution` applies: that one exists to
    make a curly quote match a straight one in an *exact* alias comparison, and an embedding
    model tokenizes both perfectly well. Applying it here would mean the cache key of a
    passage depended on a lexical-matching decision it has nothing to do with.
    """
    return " ".join(text.split())


def render_concept(concept, *, include_raw_variants: bool = False) -> str:
    """Renderer `v1`: label, category, aliases in declaration order, description.

    Aliases keep declaration order rather than being sorted: the first alias is the concept's
    own filed spelling in this vocabulary, and sorting would put "% of Homes…" ahead of it on
    a punctuation accident.

    `include_raw_variants` appends `population.raw_variants` and is the ablation arm only —
    see the module docstring. It is a keyword with a default rather than a second function
    because the two renderings must otherwise be identical, and two functions would drift.
    """
    fields = [concept.label, concept.category.value]
    fields.extend(concept.aliases)
    if include_raw_variants:
        population = getattr(concept, "population", None)
        fields.extend(getattr(population, "raw_variants", ()) or ())
    fields.append(concept.description)
    # An empty field would render as a bare separator and change every downstream token
    # position for the concepts that have no description, which is a difference in the
    # embedding with no difference in the ontology.
    return normalize_for_embedding(
        FIELD_SEPARATOR.join(field for field in fields if field and field.strip()))


def render_concepts(concepts, *, include_raw_variants: bool = False) -> dict[str, str]:
    """`concept_id -> rendered string`, for the whole vocabulary. Order is the caller's."""
    return {
        concept.concept_id: render_concept(
            concept, include_raw_variants=include_raw_variants)
        for concept in concepts
    }
