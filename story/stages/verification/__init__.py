"""S9 — the deterministic verifier. The stage the demo claim rests on.

`DeterministicVerifier` satisfies `story.contracts.DraftVerifier` and is constructible with
**neither a database nor a model provider**: every check is a pure function of the draft, the
package and the plan. That is not a convenience for testing; it is what lets §13.16's rule —
*"the model may only tighten, never loosen"* — be true by construction, because there is no
seam here for a model to reach through.

    verifier = DeterministicVerifier(graph_run_id=..., run_complete_sha256=...)
    verified = verifier.verify(draft, package, plan)
    if not verified.passed:
        rejection = rejection_for(verified)      # actionable, with a remedy per finding

`verify` returns a `VerifiedDraft` whether or not the draft survived — a refusal has somewhere
to be recorded and cannot be lost by a verifier that raises. `rejection_for` is the other half
of §13.17: a `RejectedDraft` carries the same checks and refuses to be constructed unless
something in them blocks, so an acceptance cannot be filed as a rejection or the reverse.

Eight modules, split where a concern is genuinely separate rather than to make the directory
look uniform:

    codes.py           §13.17's gate — severity and remedy per code, in one table      264
    period_grammar.py  §13.4's closed grammar for a period surface                     203
    metric_surfaces.py §13.5's alias index, longest match wins                         204
    language.py        the closed lexicons §13.6, §13.10, §13.14, §13.15 refuse on     337
    package_index.py   the lookups over one package, incl. §13.7.1's column census     183
    citations.py       §13.7's three rules — reconstruction, containment, Rule C       490
    claims.py          §13.10, §13.14, §13.15 — what a sentence may assert             636
    deterministic.py   ten of the twelve checks, and the assembly                    1,417

`deterministic.py` reached 1,804 lines before `claims.py` was split out of it, and the split is
a boundary rather than a size target: *"does this number match this fact"* and *"may this
sentence say this at all"* share nothing but the package index — one reaches for
`story/core/numerals.py`, the other for a closed lexicon, and neither calls the other. The six
small modules are each a vocabulary that `deterministic.py` and `claims.py` both apply; folding
any of them back in would put the vocabulary inside one of its two consumers.
"""

from __future__ import annotations

from story.core.models import RejectedDraft, VerifiedDraft
from story.stages.verification.codes import GATE, GateEntry, UndeclaredCode, finding
from story.stages.verification.deterministic import (
    REQUIRED_WARNING_QUALIFIERS,
    DeterministicVerifier,
)
from story.stages.verification.metric_surfaces import MetricAliasIndex
from story.stages.verification.package_index import PackageIndex


class DraftAccepted(ValueError):
    """`rejection_for` was called on a verification nothing blocked.

    Raised rather than returning `None`: §14 files an acceptance and a rejection into different
    directories, and a caller that could receive `None` would have to remember which. The
    condition is `VerifiedDraft.passed`, which is derived from the findings.
    """


def rejection_for(verified: VerifiedDraft) -> RejectedDraft:
    """The §13.17 rejection artifact for a verification that did not survive the gate.

    Carries the checks whole rather than only the blocking findings: a rejection that dropped
    its WARNs and ANNOTATEs would be a report of the draft's worst sentence instead of a report
    of the draft, and the `examined` denominators are what say which checks even ran.
    """
    if verified.passed:
        raise DraftAccepted(
            f"{verified.candidate_id}: nothing blocked, so this is an acceptance — a rejection "
            "with no blocking finding is an acceptance written into the wrong directory "
            "(§13.17, §14)")
    return RejectedDraft(
        candidate_id=verified.candidate_id,
        package_identity=verified.package_identity,
        draft_content_sha256=verified.draft_content_sha256,
        checks=verified.checks,
        findings=verified.findings,
    )


__all__ = [
    "GATE",
    "REQUIRED_WARNING_QUALIFIERS",
    "DeterministicVerifier",
    "DraftAccepted",
    "GateEntry",
    "MetricAliasIndex",
    "PackageIndex",
    "UndeclaredCode",
    "finding",
    "rejection_for",
]
