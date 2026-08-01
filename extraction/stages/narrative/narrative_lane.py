"""The ontology-guided narrative claim lane.

Satisfies `extraction.contracts.ClaimLane` beside the deterministic table lane, so everything
downstream — `assemble`, `verify`, the catalog — is unable to tell which one read a passage.

Responsibility: build one request per candidate passage and turn one answer into a
`LaneResult`. Boundaries: it does not decide which concepts are in scope (the injected
`OntologyCandidateScope` does), it does not build the prompt string (`prompt.py` does), and it
does not decide whether an answer becomes a claim (`response_mapping.py` does). What is left
here is ordering, the provider call, and the failure that only this layer can see — a provider
that answered with something unusable.

**The scope is injected, never constructed.** Step 11 runs this lane under the lexical scope
and the hybrid scope and compares them, and that comparison is only meaningful if no lane code
changes between the two runs (STAGE_10 §3).

**One request per candidate, temperature 0, no batching.** Batching would make one passage's
answer depend on which passages shared its request, which is the property that would make the
persisted answers unreplayable.

An unusable answer is recorded, not raised. The provider's own taxonomy already separates
transport faults from model results: `ProviderUnavailable` and `ProviderTimeout` say the run
cannot continue and propagate, while an answer that will not parse or does not satisfy the
schema is data about the model and becomes an abstention. Retrying the second kind is
pointless at temperature 0 and the adapter says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.periods import period_phrases
from ...core.units import unit_for_metric
from ...providers.public import ProviderResponseError, ProviderSchemaError
from ..tables.public import UNRESOLVED_METRIC
from .prompt import PROMPT_VERSION, PassageContext, build_prompt
from .public import (
    LANE_NAME,
    LANE_VERSION,
    MODEL_ANSWER_UNUSABLE,
    NarrativeExtraction,
    NarrativeIssue,
    ambiguous_surfaces_for,
    response_schema,
)
from .response_mapping import MappingContext, map_answer

# Measured, not guessed, and measured by being wrong first. 2,048 truncated the MD&A
# highlights passage at 6,072 characters of JSON — fourteen required fields per claim and four
# per abstention is expensive, and a truncated answer is not a partial claim but an
# unparseable one *(measured 2026-08-01: `assistant content is not JSON: Unterminated string`
# on `open-20210930.htm#p142`, which reports ten figures)*.
#
# 3,072 truncated it again once the prompt began asking for whole-sentence spans, because a
# passage with sixteen findings repeats a long sentence sixteen times (8,925 characters).
#
# 4,096 against the validated 8,192-token slot. The largest prompt measured is 3,529 tokens —
# a 2,031-character letter passage with 17 candidate concepts and their ambiguity notes — so
# the worst case is 7,625 of 8,192. A prompt longer than that produces a truncated answer, and
# a truncated answer is already a recorded `MODEL_ANSWER_UNUSABLE` rather than a silent one:
# the failure is visible, which is what makes this number safe to set from a measurement.
DEFAULT_MAX_OUTPUT_TOKENS = 4096

# Not a tuning knob. STAGE_10 §7 fixes it, and the persisted answers are keyed on it, so a
# different value is a different request and a different record.
TEMPERATURE = 0.0


@dataclass(frozen=True)
class GenerationStats:
    """Operational statistics for one passage. Printed and logged, never persisted.

    Kept off `LaneClaim` and off `NarrativeIssue` on purpose: every one of these numbers moves
    between two identical requests, so a claim carrying them could not appear in an artifact
    required to be byte-identical (STAGE_10 §7, the rule STAGE_09 §11.2 settled).
    """

    passage_id: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    finish_reason: str
    attempts: int
    candidate_concepts: int
    replayed: bool = False


class OntologyGuidedNarrativeClaimLane:
    """Reads claims out of one normalized narrative passage, through a generation provider."""

    name = LANE_NAME
    version = LANE_VERSION

    def __init__(
        self,
        ontology,
        scope,
        provider,
        deferred_metric_ids: frozenset[str],
        *,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._ontology = ontology
        self._scope = scope
        self._provider = provider
        # Derived from the ontology by the caller, exactly as the table lane takes it. A metric
        # whose first source lane is unavailable must not become a claim merely because a
        # sentence names it.
        self._deferred = deferred_metric_ids
        self._max_output_tokens = max_output_tokens
        self._stats: list[GenerationStats] = []

    @property
    def stats(self) -> tuple[GenerationStats, ...]:
        return tuple(self._stats)

    @property
    def scope_name(self) -> str:
        return str(getattr(self._scope, "name", "unknown"))

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
    ) -> NarrativeExtraction:
        """One passage, one request, one structured result."""
        passage_id = context.passage_id
        document = document_id or passage_id.split("#")[0]
        concepts = self._candidate_metrics(text)

        if not concepts:
            # Silence and abstention are different answers. A passage the scope offers no
            # metric for produced no claim for a stateable reason, and saying so is what makes
            # its absence from the catalog auditable.
            return NarrativeExtraction(
                passage_id=passage_id,
                issues=[NarrativeIssue(
                    UNRESOLVED_METRIC,
                    f"the {self.scope_name} scope offered no metric concept for this passage",
                    passage_id)],
            )

        metric_ids = tuple(concept.concept_id for concept in concepts)
        units = tuple(sorted({
            unit for unit, _ in (unit_for_metric(c) for c in concepts) if unit}))
        phrases = period_phrases(text)
        schema = response_schema(metric_ids, units, phrases)
        # Derived once and used twice on purpose: the prompt warns about these wordings and
        # `response_mapping` refuses a claim that rests on one, and the two must be the same
        # list or the lane would enforce a rule it never stated.
        surfaces = ambiguous_surfaces_for(self._ontology, frozenset(metric_ids))
        rendered = build_prompt(
            text=text, concepts=concepts, context=context, schema=schema,
            ambiguous_surfaces=surfaces, period_phrases=phrases)

        try:
            answer = self._provider.generate(
                prompt=rendered, schema=schema,
                max_tokens=self._max_output_tokens, temperature=TEMPERATURE)
        except (ProviderResponseError, ProviderSchemaError) as error:
            return NarrativeExtraction(
                passage_id=passage_id,
                issues=[NarrativeIssue(
                    MODEL_ANSWER_UNUSABLE, str(error), passage_id,
                    metric_ids=metric_ids)],
            )

        self._stats.append(GenerationStats(
            passage_id=passage_id,
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            latency_ms=answer.latency_ms,
            finish_reason=answer.finish_reason,
            attempts=answer.attempts,
            candidate_concepts=len(concepts),
            replayed=bool(answer.metadata.get("replayed")),
        ))

        return map_answer(
            answer.content,
            ontology=self._ontology,
            context=MappingContext(
                passage_id=passage_id,
                document_id=document,
                passage_text=text,
                scope_concept_ids=frozenset(metric_ids),
                deferred_metric_ids=self._deferred,
                subject_entity_id=subject_entity_id,
                subject_type=subject_type,
                ambiguous_surfaces=surfaces,
                extractor_metadata={
                    "lane": LANE_NAME,
                    "lane_version": LANE_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "scope": self.scope_name,
                    "provider_model_id": answer.model_id,
                    "content_sha256": answer.content_sha256,
                    "candidate_concept_ids": list(metric_ids),
                },
            ),
        )

    # -- what the scope offered ------------------------------------------------------------------

    def _candidate_metrics(self, text: str) -> tuple:
        """The scope's answer, narrowed to metric definitions and sorted.

        Only `candidates_for` is used, which is the whole of the `OntologyCandidateScope`
        protocol — so the lexical and the hybrid scope are interchangeable here by
        construction rather than by care.

        Narrowed to metrics because the entity concepts a scope always carries (`opendoor`,
        `company`, `public_company`) are the subject of an observation, not a thing one can be
        made about; the subject reaches the prompt through `PassageContext` instead.

        **Deferred metrics stay in.** A sentence naming one must produce a recorded
        `DEFERRED_REQUIRED_SOURCE_LANE`, and a concept absent from the menu cannot be named,
        so removing them here would turn a measurable refusal into an invisible one.
        """
        candidates: list = []
        for concept_id in self._scope.candidates_for(text):
            concept = self._ontology.registry.find(concept_id)
            if concept is None:
                continue
            if str(getattr(concept.category, "value", concept.category)) != "metric_definition":
                continue
            candidates.append(concept)
        return tuple(sorted(candidates, key=lambda c: c.concept_id))
