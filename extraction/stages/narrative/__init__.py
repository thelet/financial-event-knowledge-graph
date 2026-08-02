"""The ontology-guided narrative claim lane: prose, a local model, and the same `LaneClaim`.

Five concerns, five modules, because each is a different failure mode: a contract
(`public.py`), a pure string builder (`prompt.py`), an orchestration (`narrative_lane.py`),
the boundary that turns an untrusted answer into a typed claim or refuses it
(`response_mapping.py`), and the durable record that makes a run replayable without a GPU
(`answer_store.py`).

`response_mapping.py` is the one that must never be folded into the lane. Every "the model
said something the ontology does not permit" decision lives there, and it is testable with a
literal dict and no provider. What it deliberately does *not* hold is generic text reading:
span location is `core.text_spans`, figure and scale-word reading is `core.numbers`, and the
period vocabulary is `core.periods`, each of which carries no ontology and no model answer.
`period_phrases` and `resolve_period_phrase` are re-exported below because they were this
package's vocabulary before they were `core`'s, and callers should not have to know that moved.

**Step 12 added a second lane to this package rather than a second stage.** Exactly one stage
may name a provider — `tests/extraction/test_provider.py` fixes it as this one and forbids any
other stage naming one at all — so events and relationships come through this port or through a
second adapter that the same test forbids. Four modules mirroring the four concerns above
(`events_public.py`, `event_prompt.py`, `event_lane.py`, `event_mapping.py`), sharing this
package's fifth: `answer_store.py` is one durable record for both lanes, keyed by request
identity, so a prompt from either lane finds its own answer and neither can find the other's.
"""

from ...core.periods import date_phrases, period_phrases, resolve_period_phrase
from ...core.numbers import printed_magnitude
from .answer_store import (
    IDENTITY_VERSION,
    AnswerStore,
    MissingAnswerError,
    ReplayingGenerationProvider,
    StoredAnswer,
    request_identity,
)
from .event_lane import EventGenerationStats, OntologyGuidedEventLane
from .event_mapping import EventMappingContext, map_answer as map_event_answer
from .event_prompt import (
    EVENT_PROMPT_VERSION,
    build_prompt as build_event_prompt,
    render_event_type,
)
from .events_public import (
    DATE_NOT_STATED,
    ENTITY_NOT_NAMED,
    EVENT_LANE_NAME,
    EVENT_LANE_VERSION,
    ISSUE_CODES as EVENT_ISSUE_CODES,
    LANE_DECISIONS_WITHOUT_AN_ANSWER,
    MODEL_ABSTENTION_REASONS as EVENT_MODEL_ABSTENTION_REASONS,
    EventExtraction,
    EventIssue,
    OfferedVocabulary,
    offered_vocabulary,
    response_schema as event_response_schema,
)
from .narrative_lane import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MIN_OUTPUT_TOKENS,
    TEMPERATURE,
    GenerationStats,
    OntologyGuidedNarrativeClaimLane,
    output_budget,
)
from .prompt import PROMPT_VERSION, PassageContext, build_prompt, render_concept
from .public import (
    ISSUE_CODES,
    LANE_NAME,
    LANE_VERSION,
    MODEL_ABSTENTION_REASONS,
    PERIOD_NOT_PRINTED,
    PROMPT_EXCEEDS_CONTEXT,
    AmbiguousSurface,
    NarrativeExtraction,
    NarrativeIssue,
    ambiguous_surfaces_for,
    response_schema,
)
from .response_mapping import (
    INLINE_PROSE,
    PRECEDING_CONTEXT,
    MappingContext,
    map_answer,
)

__all__ = [
    "AmbiguousSurface", "AnswerStore", "DATE_NOT_STATED", "DEFAULT_CONTEXT_TOKENS",
    "DEFAULT_MAX_OUTPUT_TOKENS", "ENTITY_NOT_NAMED", "EVENT_ISSUE_CODES", "EVENT_LANE_NAME",
    "EVENT_LANE_VERSION", "EVENT_MODEL_ABSTENTION_REASONS", "EVENT_PROMPT_VERSION",
    "EventExtraction", "EventGenerationStats", "EventIssue", "EventMappingContext",
    "GenerationStats", "IDENTITY_VERSION", "INLINE_PROSE", "ISSUE_CODES",
    "LANE_DECISIONS_WITHOUT_AN_ANSWER", "LANE_NAME",
    "LANE_VERSION", "MIN_OUTPUT_TOKENS", "MODEL_ABSTENTION_REASONS", "MappingContext",
    "MissingAnswerError", "NarrativeExtraction", "NarrativeIssue", "OfferedVocabulary",
    "OntologyGuidedEventLane",
    "OntologyGuidedNarrativeClaimLane", "PERIOD_NOT_PRINTED", "PRECEDING_CONTEXT",
    "PROMPT_EXCEEDS_CONTEXT", "PROMPT_VERSION", "PassageContext",
    "ReplayingGenerationProvider", "StoredAnswer", "TEMPERATURE", "ambiguous_surfaces_for",
    "build_event_prompt", "build_prompt", "date_phrases", "event_response_schema",
    "map_answer", "map_event_answer", "offered_vocabulary", "output_budget", "period_phrases",
    "printed_magnitude", "render_concept", "render_event_type", "request_identity",
    "resolve_period_phrase", "response_schema",
]
