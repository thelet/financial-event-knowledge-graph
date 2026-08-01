"""The lexical ontology candidate scope. Offline, no provider, no ranking.

Decides which concepts a lane may consider for a passage on lexical evidence alone, and
records why for each one. It only ever *adds*: there is no threshold, no top-k and no
filter anywhere below, because every reason it can produce is protected and stage 9's
embeddings are entitled to extend this set and nothing else
(`extraction.contracts.EmbeddingProvider` states the same rule).

Two things it does not do itself. The canonical-label precedence comes from
`core.concept_resolution`, shared with the deterministic table lane, so a row label cannot
mean one metric to the lane and another to the scope. The confusion-group expansion comes
from `AliasIndex.confusion_siblings`, shared with typed selection for the same reason.

Nothing here names a metric. The three stable-core ids are entity identity rather than
vocabulary and arrive as a constructor argument.
"""

from __future__ import annotations

from collections import defaultdict

from ...core.concept_resolution import (
    CANONICAL_LABEL,
    LabelResolution,
    fold,
    resolve_label,
)
from ..select.alias_evidence import AliasIndex, matches_whole, row_labels
from .public import (
    AMBIGUOUS_ALIAS,
    CONFUSION_SIBLING,
    EXACT_ALIAS,
    KNOWN_INSTANCE,
    NORMALIZED_ALIAS,
    STABLE_CORE,
    STABLE_CORE_CONCEPTS,
    TABLE_LABEL,
    CandidateScope,
    ConfusionExpansion,
    ScopedConcept,
)

SCOPE_NAME = "lexical"
SCOPE_VERSION = "1.0.0"


class LexicalOntologyCandidateScope:
    """Satisfies `extraction.contracts.OntologyCandidateScope`.

    `candidates_for` is the protocol's answer — concept ids, sorted. `scope_for` is the same
    computation with its evidence intact, which is what the benchmark report and any evidence
    panel need. The protocol method is defined in terms of the richer one so the two can
    never disagree about what is in scope.
    """

    name = SCOPE_NAME
    version = SCOPE_VERSION

    def __init__(
        self,
        ontology,
        alias_index: AliasIndex,
        stable_core: tuple[str, ...] = STABLE_CORE_CONCEPTS,
    ) -> None:
        self._ontology = ontology
        self._aliases = alias_index
        self._stable_core = tuple(sorted(stable_core))
        # Instance ids and labels, folded once. Instances are a handful and the surfaces are
        # short, so this is a list rather than an index; rebuilding the registry's alias
        # machinery for four entries would be structure for its own sake.
        self._instances: tuple[tuple[str, str], ...] = tuple(sorted(
            (instance.instance_id, surface)
            for instance in ontology.registry.definitions.instances
            for surface in {instance.instance_id, str(getattr(instance, "label", "") or "")}
            if len(surface) >= 3
        ))

    def candidates_for(self, text: str) -> tuple[str, ...]:
        return self.scope_for(text).concept_ids

    def scope_for(self, text: str) -> CandidateScope:
        """Every concept the passage licences, with its reasons. Sorted, so two runs agree."""
        reasons: dict[str, set[str]] = defaultdict(set)
        surfaces: dict[str, set[str]] = defaultdict(set)

        for concept_id in self._stable_core:
            reasons[concept_id].add(STABLE_CORE)

        self._add_alias_hits(text, reasons, surfaces)
        self._add_row_labels(text, reasons, surfaces)
        self._add_known_instances(text, reasons, surfaces)
        expansions = self._add_confusion_siblings(reasons)

        return CandidateScope(
            concepts=tuple(
                ScopedConcept(
                    concept_id=concept_id,
                    reasons=tuple(sorted(reasons[concept_id])),
                    surfaces=tuple(sorted(surfaces[concept_id])),
                )
                for concept_id in sorted(reasons)
            ),
            expansions=expansions,
        )

    # -- the four sources of evidence ---------------------------------------------------------

    def _add_alias_hits(self, text: str, reasons, surfaces) -> None:
        """Whole-phrase alias matches over the passage.

        An ambiguous surface contributes **all** its concepts, never a best one. Picking one
        for a bare "homes" is what merges `homes_purchased` into `homes_under_contract`, the
        failure the ontology research names as the most damaging available to this
        vocabulary — and a scope that picked would remove the lane's only chance to notice.
        """
        for hit in self._aliases.hits(text):
            # `hits` matched against the folded text. If the same surface does not match the
            # raw text, the fold is the only reason this candidate exists — worth its own
            # reason code, because it is the number that goes to zero if the fold regresses.
            needed_fold = not matches_whole(hit.surface_form, text)
            owner = self._aliases.canonical_labels.get(hit.surface_form)
            for concept_id in hit.concept_ids:
                surfaces[concept_id].add(hit.surface_form)
                if needed_fold:
                    reasons[concept_id].add(NORMALIZED_ALIAS)
                if hit.ambiguous:
                    reasons[concept_id].add(AMBIGUOUS_ALIAS)
                elif not needed_fold:
                    reasons[concept_id].add(EXACT_ALIAS)
                if owner == concept_id:
                    reasons[concept_id].add(CANONICAL_LABEL)
                if hit.in_row_label:
                    reasons[concept_id].add(TABLE_LABEL)

    def _add_row_labels(self, text: str, reasons, surfaces) -> None:
        """Markdown row labels, resolved through the shared resolver.

        This is where STAGE_08 §2 is cashed in rather than asserted. The scope does not decide
        what "Gross Margin" means in a row label — `core.concept_resolution` does, and the
        table lane asks the same function the same question. A concept the resolver returns
        for a row is therefore in scope by construction, which is the property §2 requires of
        selection and this stage can actually guarantee.
        """
        for label in row_labels(text):
            resolution: LabelResolution = resolve_label(self._aliases, label)
            for concept_id in resolution.concept_ids:
                surfaces[concept_id].add(resolution.normalized_label)
                reasons[concept_id].add(TABLE_LABEL)
                if resolution.basis == CANONICAL_LABEL:
                    reasons[concept_id].add(CANONICAL_LABEL)
                elif resolution.ambiguous:
                    reasons[concept_id].add(AMBIGUOUS_ALIAS)
                else:
                    reasons[concept_id].add(EXACT_ALIAS)

    def _add_known_instances(self, text: str, reasons, surfaces) -> None:
        """Named instances — the registrant, the exchange, the disclosure channels.

        The instance id is what enters the scope, not its type: `public_company` is already
        reachable through the alias index and through the stable core, while `opendoor` is the
        thing a subject resolver has to be able to name.
        """
        folded = fold(text)
        for instance_id, surface in self._instances:
            if matches_whole(surface, folded):
                reasons[instance_id].add(KNOWN_INSTANCE)
                surfaces[instance_id].add(surface.lower())

    def _add_confusion_siblings(self, reasons) -> tuple[ConfusionExpansion, ...]:
        """`distinct_from` siblings of everything already in scope.

        The reason this looks optional and is not: a passage naming `homes_sold` must let the
        lane see `homes_purchased`, or the lane cannot tell that the sentence in front of it
        is the other one, and the declaration meant to prevent the merge never gets a chance.

        The sibling set comes from `AliasIndex.confusion_siblings` — the same function typed
        selection uses — so the two stages cannot drift. The attribution below only says which
        in-scope concept each sibling came from; it adds nothing to the set.
        """
        included = tuple(sorted(reasons))
        siblings = self._aliases.confusion_siblings(self._ontology, included)
        if not siblings:
            return ()

        sibling_set = set(siblings)
        expansions: list[ConfusionExpansion] = []
        for concept_id in included:
            concept = self._ontology.registry.find(concept_id)
            declared = tuple(getattr(concept, "distinct_from", ()) or ()) if concept else ()
            for sibling_id in sorted(str(s) for s in declared):
                if sibling_id in sibling_set:
                    expansions.append(ConfusionExpansion(concept_id, sibling_id))

        for sibling_id in siblings:
            reasons[sibling_id].add(CONFUSION_SIBLING)
        return tuple(expansions)
