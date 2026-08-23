"""S3 — the composition stage: the model writes the sentence, code writes the metadata.

`docs/2026-08-23-deterministic-draft-compiler/`. The writer returns **sentence templates with
slots**; this stage fills every slot from a trusted row, records the span it wrote into, mints
the bindings and the citations, and emits the same `Draft` the verifier already consumes. The
property the stage exists for, stated so it can be tested: *every numeral a reader sees is
either inserted by code from a trusted row, or refused.*

    public.py       the contracts and the nine refusal codes
    slot_table.py   slot_table(package, derived_facts, passages) -> the rows and their offers
    compile.py      compile_draft(templates, package, plan, ...) -> CompiledDraft, or a refusal

**Three files and not one, and not five.** `public.py` is a vocabulary two other stages will
name (the pipeline catches `CompositionRefused`, the demo UI catalogues its codes) and holds no
logic; `slot_table.py` is the seam a Research Agent later produces and is a pure function of
trusted rows; `compile.py` is the parse-substitute-bind-cite pass, which is one concern and
stays one file at ~330 lines. Splitting the pass into a parser, a binder and a citer would put
three ~90-line modules where one readable left-to-right function belongs, which is the failure
mode `CLAUDE.md`'s *proportion over symmetry* names.

**It imports `story.core.*` and nothing else** — no sibling stage, no provider, no contract.
`tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` derives the
stage from the module path, so the rule applied to this package the moment it existed. The one
thing it needs from a sibling — which operations span two periods — is a **checked copy** in
`slot_table.py`, asserted equal to `story/stages/derivation/offers.py`'s by a test, following
the precedent `renderings.NON_NUMERIC_DERIVED_UNITS` set.

**It is a stage and not a `core` module because it refuses.** A shared module that both renders
and refuses would be a second authority inside the layer every stage imports; the rendering
itself lives in `story/core/renderings.py`, which this stage asks and does not duplicate.

**`slot_table` and `compile_draft` shadow the modules they came from**, exactly as
`story.stages.derivation`'s `offers` and `execute` do, and a caller has to know it: `import
story.stages.composition.slot_table as m` binds the *function*, because `import a.b as c` reads
the package attribute and falls back to `sys.modules` only when there is none. Reach a
submodule's constants as `from story.stages.composition.slot_table import TWO_PERIOD_OPERATIONS`,
which is unambiguous. The names are the implementation plan's and the functions' names are the
natural ones; recording the collision is cheaper than renaming a module the later stages are
coded against.

**And it is not a verifier** (R7). It answers *"what does this template mean"*. Direction
against prose, comparison order, causal language, superlatives, required warnings and
counterpoint survival are §13's, and a compiler that also checked them would be a second
authority that can disagree with the first.
"""

from __future__ import annotations

from story.stages.composition.compile import SLOT_PATTERN, compile_draft
from story.stages.composition.public import (
    FIELD_NOT_OFFERED_BY_ROW,
    NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
    NO_LEGAL_RENDERING,
    PASSAGE_HANDLE_UNKNOWN,
    RESTS_ON_WITHOUT_EXPLANATORY_KIND,
    SLOT_WITHOUT_BINDING,
    TEMPLATE_NOT_COMPILABLE,
    UNKNOWN_SLOT_FIELD,
    UNKNOWN_SLOT_HANDLE,
    CompiledDraft,
    CompositionRefused,
    CompositionViolation,
    SentenceTemplate,
    SlotFill,
    SlotKind,
    SlotRow,
)
from story.stages.composition.slot_table import (
    SAME_PERIOD_OPERATIONS,
    SLOT_FIELDS,
    TWO_PERIOD_OPERATIONS,
    VALUE_FIELD,
    slot_table,
)

__all__ = [
    "FIELD_NOT_OFFERED_BY_ROW",
    "NO_EVIDENCE_HANDLE_FOR_BOUND_FACT",
    "NO_LEGAL_RENDERING",
    "PASSAGE_HANDLE_UNKNOWN",
    "RESTS_ON_WITHOUT_EXPLANATORY_KIND",
    "SAME_PERIOD_OPERATIONS",
    "SLOT_FIELDS",
    "SLOT_PATTERN",
    "SLOT_WITHOUT_BINDING",
    "TEMPLATE_NOT_COMPILABLE",
    "TWO_PERIOD_OPERATIONS",
    "UNKNOWN_SLOT_FIELD",
    "UNKNOWN_SLOT_HANDLE",
    "VALUE_FIELD",
    "CompiledDraft",
    "CompositionRefused",
    "CompositionViolation",
    "SentenceTemplate",
    "SlotFill",
    "SlotKind",
    "SlotRow",
    "compile_draft",
    "slot_table",
]
