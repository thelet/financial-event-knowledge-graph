"""S5 — the bounded evidence package. §10, and the wall the model cannot see past.

Eight modules, split by concern rather than for symmetry. The counts are statements rather than
lines of file, because that is what says whether a split earned itself; each of the first six
holds a rule at least two of the others read.

    warning_codes.py       118  the §10.1 vocabulary and the severity §13.17's gate acts on
    section_bounds.py      102  §10.2's ceilings, §10.2.1's token arithmetic, the drop rules
    query_terms.py          81  §11's correction — terms derived, never authored
    passage_excerpts.py     49  §10.2.1's ±400 window, and the passages that may never have one
    passage_quality.py      36  whether a passage's content can carry evidence at all (§4 S2)
    counter_evidence.py    185  which associations qualify as counter-evidence, and on what basis
    package_assembly.py    250  the caps, the trim, `documents[]`, the id and the digest
    evidence_package.py    821  which evidence goes in — the only module that reads the graph

`passage_quality.py` is a file rather than four lines inside `counter_evidence.py` because it
answers a different question about a different thing: *"is there a proposition in this text?"* is
true or false whichever story is being written, and every packaged passage is now put through it,
not only the counter-evidence candidates. Keeping it beside the qualification rule would have let
a relevance judgement drift into it, which is how a quality filter starts deleting inconvenient
evidence.

The split between the last two is the one worth stating, because it is the only one that could
have gone either way. They fail differently: a selection bug ships the wrong evidence, an
assembly bug ships a package whose digest will not reproduce or whose `documents[]` names a
filing nothing cites. The second kind is testable with no retriever at all. Everything above
them is a rule at least two of the others read, which is what earns each of those a file rather
than a section.

**The one thing to know before calling anything here**: `BoundedEvidencePackageBuilder` does not
load observations. §10.2's bounds are about what a *model* sees; ruling 1 of S5 is about what
the *builder* believes, and the two are different. `get_metric_history` is bounded at 200 rows,
five metrics exceed it, and only canonical-series construction pages it and proves completeness
(`story/stages/detection/canonicalization.py:_paged_history`, which refuses rather than
returning short). So the caller hands over its already-proved-complete `ObservationRecord`s and
`CanonicalPoint`s, and `PACKAGING_TOOLS` refuses the name of any tool that could reopen the hole.

**`build_story_evidence_package` is deliberately not a §9 tool.** §9 says why in one line: *"a
model that can call it can widen its own universe."* There is no model call anywhere in this
package and no argument through which a string a model produced reaches a selection.
"""

from __future__ import annotations

from story.stages.packaging.counter_evidence import (
    ASSOCIATION_BASES,
    MATCH_BASIS_SAME_DOCUMENT,
    MATCH_BASIS_SAME_PASSAGE,
    QUALIFYING_BASES,
    QUALIFYING_ISSUE_CODES,
    Classification,
    CounterEvidenceRow,
    basis_for_divergence,
    classify_issue,
    match_basis,
    match_basis_of,
)
from story.stages.packaging.evidence_package import (
    PACKAGING_TOOLS,
    SLOT_SEPARATOR,
    BoundedEvidencePackageBuilder,
    PackagingError,
)
from story.stages.packaging.package_assembly import (
    TRACE_ELAPSED_MS_NOT_CARRIED,
    PackageAssembler,
    PackageSections,
)
from story.stages.packaging.passage_excerpts import (
    EXCERPT_RADIUS_CHARS,
    BoundPassageExcerpted,
    Excerpt,
    refuse_excerpting_bound_passage,
    whole,
    window,
)
from story.stages.packaging.passage_quality import (
    MIN_CONTENT_CHARS,
    MIN_WORD_TOKENS,
    Assessment,
    assess,
)
from story.stages.packaging.query_terms import (
    MAX_TERMS,
    DerivedTerms,
    ModelSuppliedTerm,
    derive_terms,
)
from story.stages.packaging.section_bounds import (
    CEILINGS,
    CHARS_PER_TOKEN,
    MAX_TOTAL_TOKENS_CEILING,
    PROMPT_EXCLUDED_SECTIONS,
    PROTECTED_SECTIONS,
    TRIM_FLOOR,
    TRIM_ORDER,
    TRIM_PLAN,
    BudgetExceedsCeiling,
    TrimStep,
    check_budget,
    estimate_tokens,
    prompt_slice,
)
from story.stages.packaging.warning_codes import (
    CATEGORY_OF,
    KIND_OF,
    KIND_OF_CATEGORY,
    SEVERITY_OF,
    UnknownWarningCode,
    blocking,
    capability_limitations,
    category_of,
    claim_qualifying,
    packaged_warning,
)

__all__ = [
    "ASSOCIATION_BASES",
    "CATEGORY_OF",
    "CEILINGS",
    "CHARS_PER_TOKEN",
    "EXCERPT_RADIUS_CHARS",
    "KIND_OF",
    "KIND_OF_CATEGORY",
    "MATCH_BASIS_SAME_DOCUMENT",
    "MATCH_BASIS_SAME_PASSAGE",
    "MAX_TERMS",
    "MIN_CONTENT_CHARS",
    "MIN_WORD_TOKENS",
    "MAX_TOTAL_TOKENS_CEILING",
    "PACKAGING_TOOLS",
    "PROMPT_EXCLUDED_SECTIONS",
    "PROTECTED_SECTIONS",
    "QUALIFYING_BASES",
    "QUALIFYING_ISSUE_CODES",
    "SEVERITY_OF",
    "SLOT_SEPARATOR",
    "TRACE_ELAPSED_MS_NOT_CARRIED",
    "TRIM_FLOOR",
    "TRIM_ORDER",
    "TRIM_PLAN",
    "Assessment",
    "BoundPassageExcerpted",
    "BoundedEvidencePackageBuilder",
    "BudgetExceedsCeiling",
    "Classification",
    "CounterEvidenceRow",
    "DerivedTerms",
    "Excerpt",
    "ModelSuppliedTerm",
    "PackageAssembler",
    "PackageSections",
    "PackagingError",
    "TrimStep",
    "UnknownWarningCode",
    "assess",
    "basis_for_divergence",
    "blocking",
    "capability_limitations",
    "category_of",
    "check_budget",
    "claim_qualifying",
    "classify_issue",
    "derive_terms",
    "estimate_tokens",
    "match_basis",
    "match_basis_of",
    "packaged_warning",
    "prompt_slice",
    "refuse_excerpting_bound_passage",
    "whole",
    "window",
]
