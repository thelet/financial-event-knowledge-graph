"""S7 and S8 — the only two modules in this package that call a model, and neither has a tool.

The planner (§11) ships first and is what this directory holds today: an evidence package in,
a schema-validated `EditorialPlan` out, at temperature 0.0, with no graph access, no retrieval
and no way to ask for different evidence. The writer (§12) adds to `prompts.py` and a module
beside `planner.py` when S8 lands; the two share the prompt module because a persona and its
schema are the same kind of thing and splitting them would be one file per function.

    prompts.py    persona, package rendering, and §11's schema inside §15.3's portable subset
    planner.py    the call, and the three rules that are code after it rather than prompt text

Entry point:

    planned = plan_story(package, provider=provider, max_tokens=PLANNER_MAX_TOKENS)
    planned.plan          # accepted; every id in it resolves in the package
    planned.generation    # tokens, latency and content digest, for §14's manifest

A rejected plan raises `EditorialPlanRejected` carrying its `PlanViolation`s; a malformed
answer raises the provider's own `StoryProviderResponseError` or `StoryProviderSchemaError`,
and neither is ever retried.
"""

from __future__ import annotations

from story.stages.generation.planner import (
    CAUSAL_LANGUAGE_NOT_COMPUTED,
    CAUSAL_MARKERS,
    COUNTER_EVIDENCE_UNACCOUNTED,
    COUNTERPOINT_MISSING,
    COUNTERPOINT_UNGROUNDED,
    NEGATION_TOKENS,
    NO_KEY_POINTS,
    PLAN_NOT_CONSTRUCTIBLE,
    SPAN_EXCERPT,
    SPAN_FACT_QUOTE,
    THESIS_EMPTY,
    UNKNOWN_UNUSABLE_ID,
    UNKNOWN_WARNING_CODE,
    UNRESOLVABLE_FACT_ID,
    UNRESOLVABLE_PASSAGE_ID,
    CausalMarkerHit,
    CitedSpan,
    EditorialPlanRejected,
    PlannedStory,
    PlanViolation,
    causal_language_for,
    causal_marker_hits,
    cited_spans,
    editorial_plan_from,
    plan_story,
    plan_violations,
)
from story.stages.generation.prompts import (
    PLANNER_EXCERPT_CHARS,
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    PLANNER_SYSTEM,
    planner_prompt,
    planner_schema,
)

__all__ = [
    "CAUSAL_LANGUAGE_NOT_COMPUTED",
    "CAUSAL_MARKERS",
    "COUNTERPOINT_MISSING",
    "COUNTERPOINT_UNGROUNDED",
    "COUNTER_EVIDENCE_UNACCOUNTED",
    "NEGATION_TOKENS",
    "NO_KEY_POINTS",
    "PLANNER_EXCERPT_CHARS",
    "PLANNER_MAX_TOKENS",
    "PLANNER_PROMPT_VERSION",
    "PLANNER_SCHEMA_NAME",
    "PLANNER_SYSTEM",
    "PLAN_NOT_CONSTRUCTIBLE",
    "SPAN_EXCERPT",
    "SPAN_FACT_QUOTE",
    "THESIS_EMPTY",
    "UNKNOWN_UNUSABLE_ID",
    "UNKNOWN_WARNING_CODE",
    "UNRESOLVABLE_FACT_ID",
    "UNRESOLVABLE_PASSAGE_ID",
    "CausalMarkerHit",
    "CitedSpan",
    "EditorialPlanRejected",
    "PlanViolation",
    "PlannedStory",
    "causal_language_for",
    "causal_marker_hits",
    "cited_spans",
    "editorial_plan_from",
    "plan_story",
    "plan_violations",
    "planner_prompt",
    "planner_schema",
]
