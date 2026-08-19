"""S13 — the derivation stage: code computes every quantity, the model only words it.

DETERMINISTIC_FACT_TOOLS §4. The planner **requests** a derivation by naming an operation and
two package fact ids; this stage **validates and executes** it, and the result is a
`DerivedFact` the writer binds exactly as it binds an observed one. The property the whole stage
exists for, stated so it can be tested: *every numeral in an accepted post is either a packaged
observation or a derived fact code computed — no numeral is ever the model's own arithmetic.*

    public.py       the refusal taxonomy, the validated intermediate, the tool version
    offers.py       §4.2's validation, and §4.3's offer set built out of the same predicate
    operations.py   §4.1's seven operations, each pure over already-validated inputs
    execute.py      request -> validate -> execute -> DerivedFact, or a typed refusal; §7's facts

**Three things this stage does not have, each on purpose.** No generic calculator and no
expression string — §10 rejects `evaluate(expression)` as *"arbitrary Python by another name"*.
No native provider tool-calling — §5 uses a structured planner field followed by deterministic
execution, because two providers mean two tool protocols and `request_identity` already digests
the schema. And no arithmetic of its own: `numerals` owns subtraction, relative change and what
a result may be rendered in, and reimplementing any of it is the defect this stage was written
to avoid.

**It imports `story.core` and no sibling stage** (`test_story_package_structure.py`). Two things
it needs live in siblings — `detector_config.quantity_direction` and
`planner.causal_language_for` — and both arrive as arguments from the composition root rather
than as imports; `public.DirectionOracle` records why that is the right shape and not a
workaround.

The four types that cross a stage boundary — `DerivationRequest`, `DerivedFact`,
`EvidenceScopeFact` and `DerivationOperation` — are in `story/core/models.py`, because the
planner, the writer and the verifier all name them and none of them may import this package.

**Two of the re-exports below shadow the module they came from**, and a caller has to know it:
`execute` and `offers` are functions here, so `import story.stages.derivation.execute as m`
binds the *function* — `import a.b as c` reads the package attribute and only falls back to
`sys.modules` when there is none. Reach a submodule as `from story.stages.derivation.execute
import execute`, which is unambiguous and is how every consumer in this repository does it. The
names are the plan's (§4 lists the four files) and the functions' names are the natural ones;
recording the collision is cheaper than renaming a module three later packets are coded
against.
"""

from __future__ import annotations

from story.stages.derivation.execute import (
    DETECTOR_SIGNALS,
    evidence_scope_facts,
    execute,
    execute_all,
)
from story.stages.derivation.offers import (
    ADMITTED_UNITS,
    OFFERABLE_OPERATIONS,
    SAME_PERIOD_OPERATIONS,
    TWO_PERIOD_OPERATIONS,
    is_offered,
    offers,
    validate,
)
from story.stages.derivation.operations import (
    CORPUS_UNITS,
    OPERATIONS,
    OperationOutcome,
    period_surface_hint,
    round_delta,
)
from story.stages.derivation.public import (
    NO_SUPPORTED_CAUSAL_EXPLANATION,
    NO_SUPPORTED_CAUSAL_EXPLANATION_STATEMENT,
    SIGN_CONVENTION_UNVERIFIED,
    TOOL_VERSION,
    DerivationRefusal,
    DerivationRefusalCode,
    DerivationResult,
    DirectionOracle,
    ValidatedDerivation,
)

__all__ = [
    "ADMITTED_UNITS",
    "CORPUS_UNITS",
    "DETECTOR_SIGNALS",
    "NO_SUPPORTED_CAUSAL_EXPLANATION",
    "NO_SUPPORTED_CAUSAL_EXPLANATION_STATEMENT",
    "OFFERABLE_OPERATIONS",
    "OPERATIONS",
    "SAME_PERIOD_OPERATIONS",
    "SIGN_CONVENTION_UNVERIFIED",
    "TOOL_VERSION",
    "TWO_PERIOD_OPERATIONS",
    "DerivationRefusal",
    "DerivationRefusalCode",
    "DerivationResult",
    "DirectionOracle",
    "OperationOutcome",
    "ValidatedDerivation",
    "evidence_scope_facts",
    "execute",
    "execute_all",
    "is_offered",
    "offers",
    "period_surface_hint",
    "round_delta",
    "validate",
]
