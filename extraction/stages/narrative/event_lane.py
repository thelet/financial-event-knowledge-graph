"""The ontology-guided event and relationship lane.

Responsibility: build one request per passage and turn one answer into an `EventExtraction`.
Boundaries: it does not decide which event types exist (`events_public.offered_vocabulary`
reads them off the ontology), it does not build the prompt string (`event_prompt.py` does), and
it does not decide whether an answer becomes a payload (`event_mapping.py` does). What is left
here is ordering, the provider call, and the failure only this layer can see — a provider that
answered with something unusable.

**One request per passage, temperature 0, no batching**, and the same `AnswerStore` the metric
lane uses. Batching would make one passage's answer depend on which passages shared its
request, which is the property that would make the persisted answers unreplayable.

**No candidate scope is injected, and that is the routing decision, not an omission.** The
metric lane takes an `OntologyCandidateScope` because forty-odd metric definitions cannot all
fit one prompt and lexical or semantic evidence has to narrow them. Nineteen event types can,
and every one of them declares `aliases = ()` — so there is no surface to narrow on and any
ranker would be a parameter fitted to whatever it was tuned against. Offering the declared
category whole is the smallest mechanism that reaches an event type at all, and it has nothing
to fit. See `events_public` for the alternatives and STAGE_12 §4 for why each was rejected.

Consequently this lane reads the *same* menu on every passage, so a report comparing candidate
scopes has nothing to compare on it. That is a finding about the mechanism and the report says
so rather than presenting two identical columns as a comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.periods import date_phrases
from ...providers.public import ProviderResponseError, ProviderSchemaError
from .answer_store import request_identity
from .event_mapping import EventMappingContext, map_answer
from .event_prompt import EVENT_PROMPT_VERSION, build_prompt
from .events_public import (
    EVENT_LANE_NAME,
    EVENT_LANE_VERSION,
    EventExtraction,
    EventIssue,
    offered_vocabulary,
    response_schema,
)
from .narrative_lane import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MIN_OUTPUT_TOKENS,
    TEMPERATURE,
    output_budget,
)
from .prompt import PassageContext
from .public import MODEL_ANSWER_UNUSABLE, PROMPT_EXCEEDS_CONTEXT


@dataclass(frozen=True)
class EventGenerationStats:
    """Operational statistics for one passage. Printed and logged, never persisted.

    Kept off every payload for the reason STAGE_09 §11.2 settled: each of these numbers moves
    between two identical requests, so a record containing one could never be byte-identical.
    """

    passage_id: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    finish_reason: str
    attempts: int
    offered_event_types: int
    replayed: bool = False


class OntologyGuidedEventLane:
    """Reads events and relationships out of one normalized narrative passage."""

    name = EVENT_LANE_NAME
    version = EVENT_LANE_VERSION

    def __init__(
        self,
        ontology,
        provider,
        *,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    ) -> None:
        self._ontology = ontology
        self._provider = provider
        self._max_output_tokens = max_output_tokens
        self._context_tokens = context_tokens
        self._stats: list[EventGenerationStats] = []
        # Read once per lane rather than once per passage: it is a function of the vocabulary
        # alone, so recomputing it per passage would suggest a passage could change it.
        self._vocabulary = offered_vocabulary(ontology)
        self._definitions = tuple(
            definition
            for definition in (ontology.registry.event_type(concept_id)
                               for concept_id in self._vocabulary.event_type_ids)
            if definition is not None)
        # Looked up by `relationship_id`, the upper-case index `validate_relationship` reads,
        # never by `concept_id` — the two indexes both answer and only one of them is the one
        # a payload is judged against.
        self._predicates = tuple(
            definition
            for definition in (ontology.registry.relationship(predicate)
                               for predicate in self._vocabulary.relationship_ids)
            if definition is not None)

    @property
    def stats(self) -> tuple[EventGenerationStats, ...]:
        return tuple(self._stats)

    @property
    def vocabulary(self):
        return self._vocabulary

    def supports(self, candidate) -> bool:
        return candidate.passage_kind == "narrative" and candidate.lane == "narrative"

    def extract(self, candidate, text: str):
        """The protocol's method. Metadata this lane would like comes from the candidate only.

        `CandidatePassage` carries no filing form, date or heading path, so the prompt built
        here says so rather than inventing them. A caller holding the catalog row calls
        `extract_passage` and gets a better-informed prompt; both go through the same code.
        """
        return self.extract_passage(
            text,
            context=PassageContext(
                passage_id=candidate.passage_id,
                document_type=candidate.document_type,
            ),
            document_id=candidate.document_id,
        ).as_lane_result()

    def extract_passage(
        self,
        text: str,
        *,
        context: PassageContext,
        document_id: str | None = None,
        subject_entity_id: str = "opendoor",
        subject_type: str = "public_company",
    ) -> EventExtraction:
        """One passage, one request, one structured result."""
        passage_id = context.passage_id
        document = document_id or passage_id.split("#")[0]
        dates = date_phrases(text)
        schema = response_schema(self._vocabulary, dates)
        rendered = build_prompt(
            text=text, definitions=self._definitions, predicates=self._predicates,
            vocabulary=self._vocabulary, context=context, schema=schema,
            date_phrases=dates, subject_type=subject_type)

        budget, estimated = output_budget(
            rendered, ceiling=self._max_output_tokens, context_tokens=self._context_tokens)
        if budget < MIN_OUTPUT_TOKENS:
            return EventExtraction(
                passage_id=passage_id,
                issues=[EventIssue(
                    PROMPT_EXCEEDS_CONTEXT,
                    f"an estimated {estimated} prompt tokens leave {budget} of the "
                    f"{self._context_tokens}-token slot for an answer, below the "
                    f"{MIN_OUTPUT_TOKENS} one event needs; no request was issued",
                    passage_id)],
            )

        identity = request_identity(
            prompt=rendered, schema=schema, model_id=str(self._provider.model_id),
            temperature=TEMPERATURE, max_tokens=budget)
        try:
            answer = self._provider.generate(
                prompt=rendered, schema=schema, max_tokens=budget, temperature=TEMPERATURE)
        except (ProviderResponseError, ProviderSchemaError) as error:
            return EventExtraction(
                passage_id=passage_id,
                issues=[EventIssue(MODEL_ANSWER_UNUSABLE, str(error), passage_id)],
                request_sha256=identity,
            )

        self._stats.append(EventGenerationStats(
            passage_id=passage_id,
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            latency_ms=answer.latency_ms,
            finish_reason=answer.finish_reason,
            attempts=answer.attempts,
            offered_event_types=len(self._vocabulary),
            replayed=bool(answer.metadata.get("replayed")),
        ))

        mapped = map_answer(
            answer.content,
            ontology=self._ontology,
            context=EventMappingContext(
                passage_id=passage_id,
                document_id=document,
                passage_text=text,
                vocabulary=self._vocabulary,
                subject_entity_id=subject_entity_id,
                subject_type=subject_type,
                extractor_metadata={
                    "lane": EVENT_LANE_NAME,
                    "lane_version": EVENT_LANE_VERSION,
                    "prompt_version": EVENT_PROMPT_VERSION,
                    "provider_model_id": answer.model_id,
                    "content_sha256": answer.content_sha256,
                    "offered_event_type_ids": list(self._vocabulary.event_type_ids),
                },
            ),
        )
        mapped.request_sha256 = identity
        return mapped
