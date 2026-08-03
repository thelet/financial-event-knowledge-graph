"""The vocabulary §7's gate answers in: one `FreshnessCheck` per claim, one report, one verdict.

Responsibility: what a staleness refusal *is*. Nothing here reads a manifest, opens a database
or knows what Cypher looks like — a report is a value that can be built by hand, compared,
printed and asserted with no server running and no `data/` directory, which is what makes the
gate itself testable. A check that only fails against a live graph is a check nobody has ever
seen fail.

**`passed` is derived, never stored** — `graph/core/verification_report.py:217`'s discipline,
and the reason it matters more here than anywhere else in this package: §7 says there is no
flag that skips the gate and `config/story.yaml` cannot authorise one, so "a report that says
it passed while holding a refusal" must not be a representable state rather than a state some
call site is trusted to avoid.

**Why not `graph.core.verification_report.Check` itself.** That type is importable here —
`graph.core.` is a shared surface (WORKSTREAM_BOUNDARY §4, enforced by
`tests/story/test_story_package_structure.py::test_story_reaches_only_shared_upstream_meaning`,
whose docstring records that the boundary document's *(unverified)* marking on the two `graph`
surfaces is verified by it) — and it is still the wrong type. It is `extra="forbid"` and
carries no refusal code, and §7 names five codes that a caller branches on; `VerificationReport`
additionally requires `node_count`, `relationship_count` and two expected counts, which a
report about a *missing extraction directory* has no honest value for. So the shape is modelled
on that file and the one piece that is genuinely shared — `render`, the single rule for how a
difference is described — is imported rather than restated, because two rendering rules in one
repository is the defect, not the import.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from graph.core.verification_report import render

from story.core.models import StoryModel


class RefusalCode(str, Enum):
    """Why the gate refused. §7's five, plus three the plan's prose implies but does not name.

    An enum rather than a free string for the reason §15.3 gives everywhere else in this
    package: a code a caller has to match by sentence is not a code. Every `FreshnessCheck`
    carries the one it *would* raise, whether or not it fired, so a passing report still says
    what each check was guarding against.
    """

    #: The graph run directory or its `manifest.json` is absent or unreadable. Not in §7's
    #: list, because §7 assumes a manifest; a gate that raised here would report "the graph is
    #: stale" as a traceback.
    GRAPH_MANIFEST_UNREADABLE = "graph_manifest_unreadable"

    #: The manifest names an extraction directory that is not on this machine. Required by
    #: S0b's acceptance ("missing extraction directory → typed refusal, not `FileNotFoundError`")
    #: and distinct from the digest mismatch below: nothing to hash is a different operator
    #: action from hashing to the wrong thing.
    EXTRACTION_RUN_DIRECTORY_MISSING = "extraction_run_directory_missing"

    #: §7 check 1. `sha256(run.complete)` is not what the manifest recorded, **or** a file
    #: `run.complete` lists no longer holds the digest it records — the extraction run was
    #: regenerated or written into under the bytes the graph was projected from. **The one that
    #: matters.**
    #:
    #: One code for both, deliberately (R2a). `run.complete` *is* the manifest of the other
    #: files, so "the marker moved" and "a file the marker names moved" are the same sentence —
    #: the extraction inputs are not what the graph was built from — and the same operator
    #: action: rebuild the graph from the run it actually reads, or restore the run. A second
    #: code would ask every caller to branch on a distinction that changes nothing it does,
    #: and §7's list is short because a code a caller does not act on is noise. The two are
    #: still separate *checks*, so the report names which one refused and why.
    PACKAGE_INPUT_DIGEST_MISMATCH = "package_input_digest_mismatch"

    #: The database did not answer. Reported as a refusal rather than raised, for the same
    #: reason `ReadQueryExecutor.verify_connectivity` returns a value: a server that is not
    #: running is an ordinary state for a gate to branch on.
    GRAPH_UNREACHABLE = "graph_unreachable"

    #: §7 check 2. No `:GraphLoad` marker, or one whose `status` is not `complete`.
    LOAD_INCOMPLETE = "load_incomplete"

    #: §7 check 2. The marker, the nodes or the observations name a different graph run.
    GRAPH_RUN_ID_MISMATCH = "graph_run_id_mismatch"

    #: §7 check 2. The marker's or the database's counts are not the manifest's `counts`.
    COUNT_MISMATCH = "count_mismatch"

    #: §7 check 3. The vocabulary changed under a loaded run.
    ONTOLOGY_HASH_MISMATCH = "ontology_hash_mismatch"


class FreshnessCheck(StoryModel):
    """One named claim about the loaded graph, with what it expected and what it found.

    `name` is stable and machine-readable (`load_marker_counts`), so a test asserting on one
    check does not have to match a sentence, and `code` is what a caller acts on. Both are
    present on a passing check: a report is read as a table, and a column that appears only on
    failure makes the passing rows unreadable.

    `expected` and `observed` are strings for `graph/core/verification_report.py`'s reason —
    they are printed far more often than they are computed against, and rendering at
    construction keeps one rendering rule in one place while making two reports comparable by
    equality even though the underlying values are digests, counts and id lists.
    """

    name: str
    code: RefusalCode
    passed: bool
    expected: str
    observed: str
    detail: str = ""

    @classmethod
    def comparing(
        cls,
        name: str,
        code: RefusalCode,
        *,
        expected: Any,
        observed: Any,
        detail: str = "",
    ) -> "FreshnessCheck":
        """A claim that two values are equal. The only way this module decides `passed`.

        Comparison happens on the values, before rendering, so `28836` and `"28836"` are not
        made equal by being printed — the rendering is for the reader, never for the verdict.
        """
        return cls(
            name=name,
            code=code,
            passed=expected == observed,
            expected=render(expected),
            observed=render(observed),
            detail=detail,
        )

    @classmethod
    def refusing(
        cls,
        name: str,
        code: RefusalCode,
        *,
        expected: Any,
        observed: Any,
        detail: str = "",
    ) -> "FreshnessCheck":
        """A check that failed for a reason no equality expresses — an absent file, a dead
        server, an ontology that will not load."""
        return cls(
            name=name,
            code=code,
            passed=False,
            expected=render(expected),
            observed=render(observed),
            detail=detail,
        )

    def describe(self) -> str:
        line = f"{'PASS' if self.passed else 'FAIL'}  {self.name}"
        if self.passed:
            return f"{line}  ({self.observed})"
        line = f"REFUSE  {self.name}  [{self.code.value}]"
        line += f"\n        expected: {self.expected}\n        observed: {self.observed}"
        return f"{line}\n        detail:   {self.detail}" if self.detail else line


class FreshnessReport(StoryModel):
    """Every check §7's gate ran, and the verdict derived from them.

    `graph_run_id` is the run the gate was *asked* about, not one it read off the database —
    a report about a run with no manifest still has to say which run it was asked about.
    """

    graph_run_id: str
    checks: tuple[FreshnessCheck, ...] = ()

    @property
    def passed(self) -> bool:
        """Derived, and empty is not passing.

        A gate that ran nothing has not cleared anything, and an empty `all()` is `True` — the
        one way a report of no checks could authorise a story run.
        """
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def refusals(self) -> tuple[FreshnessCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    @property
    def refusal_codes(self) -> tuple[RefusalCode, ...]:
        """The distinct codes that fired, in the order the checks ran.

        Ordered by occurrence rather than sorted, because the first refusal is usually the
        cause and the rest are its consequences: a graph loaded from a superseded export fails
        the digest check *and* every count below it.
        """
        seen: list[RefusalCode] = []
        for check in self.refusals:
            if check.code not in seen:
                seen.append(check.code)
        return tuple(seen)

    def check(self, name: str) -> FreshnessCheck:
        """The named check, or `KeyError`.

        A test asserting on a check this gate never ran must fail loudly rather than silently
        assert nothing — `VerificationReport.check` makes the same call.
        """
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(f"no check named {name!r}; ran {[c.name for c in self.checks]}")

    def describe(self) -> str:
        header = (
            f"{'PASSED' if self.passed else 'REFUSED'}  {self.graph_run_id}  "
            f"({len(self.refusals)} of {len(self.checks)} checks refused"
            + (f": {', '.join(code.value for code in self.refusal_codes)}"
               if self.refusals else "")
            + ")"
        )
        return "\n".join([header, *(check.describe() for check in self.checks)])


__all__ = [
    "FreshnessCheck",
    "FreshnessReport",
    "RefusalCode",
]
