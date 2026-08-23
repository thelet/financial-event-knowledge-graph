"""S7 and S8 — the only two modules in this package that call a model, and neither has a tool.

The planner (§11) takes an evidence package and returns a schema-validated `EditorialPlan`; the
writer (§12) takes that accepted plan and returns the sentence templates the draft compiler
turns into a `Draft`. Both run at temperature
0.0 with no graph access, no retrieval and no way to ask for different evidence. The two share
one prompt module because a persona and its schema are the same kind of thing, and splitting
them would be one file per function.

    prompts.py    two personas, two renderings, two schemas inside §15.3's portable subset
    planner.py    §11's call, and the four rules that are code after it rather than prompt text
    writer.py     §12's call, the passage slice §10.2.1 point 3 derives by code, the template
                  parsing rules, and the Markdown renderer that may only read a structured draft

Entry points:

    planned = plan_story(package, provider=provider, offered=offers(package, candidate),
                         max_tokens=PLANNER_MAX_TOKENS)   # offers(): S13's stage
    written = write_story(package, planned.plan, provider=provider, derived_facts=derived,
                          slots=slot_table(package, derived, passages),   # composition's
                          length_target=DEFAULT_LENGTH_TARGET, max_tokens=WRITER_MAX_TOKENS)
    written.templates     # sentences with their slots unfilled; the compiler fills them
    written.title         # prose, and it carries no slot
    written.generation    # tokens, latency and content digest, for §14's manifest
    render_markdown(draft)   # the post, from a compiled draft and never from the model

**The slot table is an argument and is not built here** (S4 of
`docs/2026-08-23-deterministic-draft-compiler/`). It lives in `story/stages/composition/`, this
package may not import that one, and the same rows must reach the prompt the model reads and the
compiler that fills what it wrote. `story/pipeline.py` is the composition root and passes them
to both; `SlotRowView` is the structural type this package describes them by.

A rejected plan raises `EditorialPlanRejected` carrying its `PlanViolation`s and a rejected
draft raises `DraftRejected` carrying its `DraftViolation`s; a malformed answer raises the
provider's own `StoryProviderResponseError` or `StoryProviderSchemaError`, and none of them is
ever retried.

**Neither module imports `story.stages.verification`, and neither may.** §13 decides whether a
draft is publishable; this package decides what to ask for and what an answer must resolve to.
"""

from __future__ import annotations

from story.stages.generation.planner import (
    CAUSAL_LANGUAGE_NOT_COMPUTED,
    CAUSAL_MARKERS,
    DERIVATION_NOT_OFFERED,
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
    claim_qualifying_warnings,
    editorial_plan_from,
    plan_story,
    plan_violations,
)
from story.stages.generation.prompts import (
    DEFAULT_LENGTH_TARGET,
    FACTS_HEADING,
    PLAIN_INVESTOR_STYLE,
    PLANNER_EXCERPT_CHARS,
    PLANNER_MAX_TOKENS,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    PLANNER_SYSTEM,
    WARNING_QUALIFIER_PHRASES,
    WRITER_MAX_TOKENS,
    WRITER_PROMPT_VERSION,
    WRITER_SCHEMA_NAME,
    WRITER_SYSTEM,
    SlotRowView,
    StyleProfile,
    metric_surfaces_for,
    period_surface_for,
    planner_prompt,
    planner_schema,
    writer_prompt,
    writer_schema,
    writer_system,
)
from story.stages.generation.writer import (
    BINDING_RENDERING_AMBIGUOUS,
    BINDING_RENDERING_NOT_IN_TEXT,
    CITATION_QUOTE_AMBIGUOUS,
    CITATION_QUOTE_NOT_IN_PASSAGE,
    DRAFT_NOT_CONSTRUCTIBLE,
    EVIDENCE_HANDLE_OUT_OF_BOUNDS,
    MALFORMED_SLOT,
    NO_SENTENCES,
    PASSAGE_ROW,
    PLAN_NAMES_ANOTHER_PACKAGE,
    RESTS_ON_NOT_A_PASSAGE_HANDLE,
    RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE,
    SLOT_PATTERN,
    THESIS_ABANDONED,
    UNRESOLVABLE_EVIDENCE_HANDLE,
    DraftRejected,
    DraftViolation,
    SentenceTemplate,
    WrittenStory,
    draft_from,
    draft_violations,
    render_markdown,
    templates_from,
    write_story,
    writer_passages,
)

__all__ = [
    "BINDING_RENDERING_AMBIGUOUS",
    "BINDING_RENDERING_NOT_IN_TEXT",
    "CAUSAL_LANGUAGE_NOT_COMPUTED",
    "CAUSAL_MARKERS",
    "CITATION_QUOTE_AMBIGUOUS",
    "CITATION_QUOTE_NOT_IN_PASSAGE",
    "DERIVATION_NOT_OFFERED",
    "COUNTERPOINT_MISSING",
    "COUNTERPOINT_UNGROUNDED",
    "COUNTER_EVIDENCE_UNACCOUNTED",
    "DEFAULT_LENGTH_TARGET",
    "DRAFT_NOT_CONSTRUCTIBLE",
    "EVIDENCE_HANDLE_OUT_OF_BOUNDS",
    "FACTS_HEADING",
    "MALFORMED_SLOT",
    "NEGATION_TOKENS",
    "NO_KEY_POINTS",
    "NO_SENTENCES",
    "PASSAGE_ROW",
    "PLAIN_INVESTOR_STYLE",
    "PLANNER_EXCERPT_CHARS",
    "PLANNER_MAX_TOKENS",
    "PLANNER_PROMPT_VERSION",
    "PLANNER_SCHEMA_NAME",
    "PLANNER_SYSTEM",
    "PLAN_NAMES_ANOTHER_PACKAGE",
    "PLAN_NOT_CONSTRUCTIBLE",
    "RESTS_ON_NOT_A_PASSAGE_HANDLE",
    "RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE",
    "SLOT_PATTERN",
    "SPAN_EXCERPT",
    "SPAN_FACT_QUOTE",
    "THESIS_ABANDONED",
    "THESIS_EMPTY",
    "UNKNOWN_UNUSABLE_ID",
    "UNKNOWN_WARNING_CODE",
    "UNRESOLVABLE_EVIDENCE_HANDLE",
    "UNRESOLVABLE_FACT_ID",
    "UNRESOLVABLE_PASSAGE_ID",
    "WARNING_QUALIFIER_PHRASES",
    "WRITER_MAX_TOKENS",
    "WRITER_PROMPT_VERSION",
    "WRITER_SCHEMA_NAME",
    "WRITER_SYSTEM",
    "CausalMarkerHit",
    "CitedSpan",
    "DraftRejected",
    "DraftViolation",
    "EditorialPlanRejected",
    "PlanViolation",
    "PlannedStory",
    "SentenceTemplate",
    "SlotRowView",
    "StyleProfile",
    "WrittenStory",
    "causal_language_for",
    "causal_marker_hits",
    "cited_spans",
    "claim_qualifying_warnings",
    "draft_from",
    "draft_violations",
    "editorial_plan_from",
    "metric_surfaces_for",
    "period_surface_for",
    "plan_story",
    "plan_violations",
    "planner_prompt",
    "planner_schema",
    "render_markdown",
    "templates_from",
    "write_story",
    "writer_passages",
    "writer_prompt",
    "writer_schema",
    "writer_system",
]
