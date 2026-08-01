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
"""

from ...core.periods import period_phrases, resolve_period_phrase
from ...core.numbers import printed_magnitude
from .answer_store import (
    IDENTITY_VERSION,
    AnswerStore,
    MissingAnswerError,
    ReplayingGenerationProvider,
    StoredAnswer,
    request_identity,
)
from .narrative_lane import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    TEMPERATURE,
    GenerationStats,
    OntologyGuidedNarrativeClaimLane,
)
from .prompt import PROMPT_VERSION, PassageContext, build_prompt, render_concept
from .public import (
    ISSUE_CODES,
    LANE_NAME,
    LANE_VERSION,
    MODEL_ABSTENTION_REASONS,
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
    "AmbiguousSurface", "AnswerStore", "DEFAULT_MAX_OUTPUT_TOKENS", "GenerationStats",
    "IDENTITY_VERSION", "INLINE_PROSE", "ISSUE_CODES", "LANE_NAME", "LANE_VERSION",
    "MODEL_ABSTENTION_REASONS", "MappingContext", "MissingAnswerError", "NarrativeExtraction",
    "NarrativeIssue", "OntologyGuidedNarrativeClaimLane", "PRECEDING_CONTEXT",
    "PROMPT_VERSION", "PassageContext", "ReplayingGenerationProvider", "StoredAnswer",
    "TEMPERATURE", "ambiguous_surfaces_for", "build_prompt", "map_answer", "period_phrases",
    "printed_magnitude", "render_concept", "request_identity", "resolve_period_phrase",
    "response_schema",
]
