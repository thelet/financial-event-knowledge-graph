"""Composition root.

The single place concrete implementations are named, matching the acquisition and
normalization packages. Replacing a lane, a candidate scope or the source of provider answers
means changing this file, not a stage.

**It is also the only place a provider is constructed**, which is why it sits beside the
package rather than inside `stages/`. An executable test forbids every stage but the narrative
one from naming a provider at all, and a run that built one inside its own stage would put the
whole point of that boundary in one commit's blast radius.

**No gold annotation is reachable from here.** What the replaying provider reads is a store of
*answers* — what a model returned for a request digest — and never a case file, an expected
claim or an expected abstention. `tests/extraction/test_integrated_run.py` asserts the second
half against the real files, and four AST guards assert nothing under `extraction/` even names
the directory the answers live in. The path is configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.assembly import deferred_metric_ids
from .core.config import ExtractionConfig, load_config
from .core.corpus import JsonlPassageCorpus
from .providers.public import ProviderConfig
from .stages.extract import LaneRunner
from .stages.narrative import (
    AnswerStore,
    OntologyGuidedEventLane,
    OntologyGuidedNarrativeClaimLane,
    ReplayingGenerationProvider,
)
from .stages.narrative.prompt import PROMPT_VERSION
from .stages.scoping import LexicalOntologyCandidateScope, ScopingConfig
from .stages.select import AliasIndex, SelectionPolicy, TypedCandidateSelector
from .stages.tables import DeterministicTableClaimLane


class ScopeUnavailableError(RuntimeError):
    """A candidate scope was configured that this process cannot construct.

    The hybrid scope ranks a passage against concept vectors, so it needs an embedding of every
    candidate passage — which needs the embedding server. There is no committed corpus-scale
    vector cache and `config/extraction.yaml` deliberately declares no `cache_root` for one
    (STAGE_09 §11.5), so an offline run under `strategy: hybrid` cannot be built. Raised here,
    at construction, rather than failing on the first passage.
    """


@dataclass
class ExtractionContext:
    config: ExtractionConfig
    ontology: Any
    corpus: JsonlPassageCorpus
    alias_index: AliasIndex
    selection_policy: SelectionPolicy
    selector: TypedCandidateSelector
    scoping: ScopingConfig
    scope: Any
    provider_config: ProviderConfig
    answers: AnswerStore
    table_lane: DeterministicTableClaimLane
    narrative_lane: OntologyGuidedNarrativeClaimLane
    event_lane: OntologyGuidedEventLane
    runner: LaneRunner


def load_answers(paths: tuple[Path, ...], *, model_id: str) -> AnswerStore:
    """Every configured answer file, merged into one store keyed by request digest.

    Merged rather than chained. The digest covers the prompt, the schema, the model and the
    sampling parameters, so a metric prompt cannot find an event answer and the two files can
    share one index safely — and one store means one `identity_model_id` question rather than
    two that could disagree.

    A row keyed under a different model identity is **skipped and not silently replayed**. The
    identity is what the digest was taken over; replaying an answer under a model that did not
    produce it would make every claim on it a claim about the wrong run.
    """
    store = AnswerStore()
    for path in paths:
        if not Path(path).is_file():
            continue
        for answer in AnswerStore(Path(path)).answers():
            if answer.model_id == model_id:
                store.put(answer)
    return store


def build_scope(ontology, alias_index: AliasIndex, scoping: ScopingConfig, *, scope=None):
    """The candidate scope named by configuration, or the one the caller injected."""
    if scope is not None:
        return scope
    if scoping.strategy == "lexical":
        return LexicalOntologyCandidateScope(ontology, alias_index)
    raise ScopeUnavailableError(
        f"scoping.strategy is {scoping.strategy!r}; that scope embeds every candidate passage "
        "and needs the embedding server, and this repository commits no corpus-scale vector "
        "cache. Run with the lexical scope, or inject a scope built against a live embedding "
        "provider.")


def build_extraction_context(
    root: Path | None = None,
    *,
    config: ExtractionConfig | None = None,
    ontology=None,
    corpus: JsonlPassageCorpus | None = None,
    scope=None,
    provider=None,
    progress=None,
) -> ExtractionContext:
    from ontology import load_ontology

    config = config or load_config(root)
    ontology = ontology if ontology is not None else load_ontology()
    corpus = corpus if corpus is not None else JsonlPassageCorpus.from_catalog(
        config.catalog_root)

    alias_index = AliasIndex.from_ontology(ontology)
    selection_policy = SelectionPolicy.from_config(config.raw)
    selector = TypedCandidateSelector(selection_policy, alias_index, ontology)

    scoping = ScopingConfig.from_config(config.raw)
    resolved_scope = build_scope(ontology, alias_index, scoping, scope=scope)

    provider_config = ProviderConfig.from_config(config.raw)
    answers = load_answers(config.answer_store_paths, model_id=provider_config.model)
    resolved_provider = provider or ReplayingGenerationProvider(
        answers, inner=None, model_id=provider_config.model, prompt_version=PROMPT_VERSION)

    deferred = deferred_metric_ids(ontology)
    table_lane = DeterministicTableClaimLane(ontology, alias_index, deferred)
    narrative_lane = OntologyGuidedNarrativeClaimLane(
        ontology, resolved_scope, resolved_provider, deferred,
        context_tokens=provider_config.context_tokens)
    event_lane = OntologyGuidedEventLane(
        ontology, resolved_provider, context_tokens=provider_config.context_tokens)

    return ExtractionContext(
        config=config,
        ontology=ontology,
        corpus=corpus,
        alias_index=alias_index,
        selection_policy=selection_policy,
        selector=selector,
        scoping=scoping,
        scope=resolved_scope,
        provider_config=provider_config,
        answers=answers,
        table_lane=table_lane,
        narrative_lane=narrative_lane,
        event_lane=event_lane,
        runner=LaneRunner(
            corpus, table_lane=table_lane, narrative_lane=narrative_lane,
            event_lane=event_lane, progress=progress),
    )
