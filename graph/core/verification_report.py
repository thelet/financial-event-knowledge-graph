"""The vocabulary a post-load verification answers in: one `Check` per claim, one report.

**Driver-free by rule** (V1_GRAPH_PROTOTYPE §11, and `tests/graph/test_graph_package_structure.py`
enforces it transitively). Nothing here knows what Bolt is, what Cypher is, or what an export
file looks like. A report is a value: it can be built by hand, compared, printed and asserted
with no database running, which is what makes the *verifier itself* testable — a check that
only fails against a live server is a check nobody has ever seen fail.

## Why a list of checks and not a boolean

§10 lists twelve criteria and `graph/stages/load/verification.py` answers them with twenty-two
named checks. A single `verified: bool` would collapse "the graph is missing 4,000 issues" and
"one warned label is absent" into the same word. Each `Check` therefore carries its own
`expected` and `actual`, rendered as text, so a failure report reads as a table rather than as
a stack trace.

## Why `expected` and `actual` are strings

They are printed far more often than they are computed against — a `Check` ends up in a
terminal, in a commit message, in a document. Rendering at construction keeps one rendering
rule in one place, and makes two reports comparable by equality even though the underlying
values are counts, sets and mappings of three different shapes. The four constructors below
are the only way this module builds a check, so "how a difference is described" is a property
of this file rather than of twenty-two call sites.

`detail` is where a set difference names names. §10's requirement, restated: *"counts differ"
is not a finding*. A failing check must say which keys were missing and which were unexpected,
capped at `MAX_EXAMPLES` so a report about 17,127 missing issues is still readable.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

#: How many differing keys a `detail` names before it summarises the rest as a count. The
#: number is small on purpose: the report is read by a human deciding what to look at next,
#: and five keys is enough to recognise a pattern (all one label, all one document).
MAX_EXAMPLES = 5


def render(value: Any) -> str:
    """One rendering rule for the three shapes a check compares.

    Mappings are rendered sorted and inline (`Document=185 Entity=9 …`) rather than as a dict
    repr, because the comparison a reader makes is per key and a Python repr buries the keys in
    quotes. Sequences are rendered sorted too — a check comparing two sets must not report a
    difference that is only an ordering.
    """
    if isinstance(value, Mapping):
        return " ".join(f"{key}={value[key]}" for key in sorted(value)) or "<empty>"
    if isinstance(value, (set, frozenset)):
        return ", ".join(sorted(str(item) for item in value)) or "<empty>"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "<empty>"
    return str(value)


def _examples(items: Iterable[str]) -> str:
    ordered = sorted(str(item) for item in items)
    shown = ", ".join(repr(item) for item in ordered[:MAX_EXAMPLES])
    if len(ordered) > MAX_EXAMPLES:
        shown += f", and {len(ordered) - MAX_EXAMPLES} more"
    return shown


class Check(BaseModel):
    """One named claim about the loaded graph, with what it expected and what it found.

    `name` is stable and machine-readable (`node_counts_by_base_label`), because a caller
    asserting on one check should not have to match a sentence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    expected: str
    actual: str
    detail: str = ""

    @classmethod
    def comparing(
        cls, name: str, *, expected: Any, actual: Any, detail: str = ""
    ) -> "Check":
        """A scalar claim: one count, one hash, one version."""
        return cls(
            name=name,
            passed=expected == actual,
            expected=render(expected),
            actual=render(actual),
            detail=detail,
        )

    @classmethod
    def comparing_counts(
        cls,
        name: str,
        *,
        expected: Mapping[str, int],
        actual: Mapping[str, int],
        subject: str,
        detail: str = "",
    ) -> "Check":
        """A per-key claim: counts by label, counts by relationship type.

        A key present in one side and absent from the other is reported as `expected N, found
        0` rather than as a missing key, because "the graph has no `:Event`" and "the graph has
        the wrong number of events" are the same failure to whoever has to fix it.
        """
        expected_counts = {key: int(value) for key, value in expected.items()}
        actual_counts = {key: int(value) for key, value in actual.items()}
        differing = sorted(
            key
            for key in set(expected_counts) | set(actual_counts)
            if expected_counts.get(key, 0) != actual_counts.get(key, 0)
        )
        differences = "; ".join(
            f"{key}: expected {expected_counts.get(key, 0)}, found {actual_counts.get(key, 0)}"
            for key in differing[:MAX_EXAMPLES]
        )
        if len(differing) > MAX_EXAMPLES:
            differences += f"; and {len(differing) - MAX_EXAMPLES} more {subject}"
        return cls(
            name=name,
            passed=not differing,
            expected=render(expected_counts),
            actual=render(actual_counts),
            detail=differences or detail,
        )

    @classmethod
    def comparing_sets(
        cls,
        name: str,
        *,
        expected: Iterable[str],
        actual: Iterable[str],
        subject: str,
        detail: str = "",
    ) -> "Check":
        """A set claim: every exported key present, and nothing else present.

        `expected` and `actual` render as sizes rather than as the sets themselves — 17,127
        issue ids do not belong in a summary line — and the `detail` names the difference,
        which is the part a reader can act on.
        """
        expected_set = {str(item) for item in expected}
        actual_set = {str(item) for item in actual}
        missing = expected_set - actual_set
        unexpected = actual_set - expected_set
        parts = []
        if missing:
            parts.append(f"missing {len(missing)}: {_examples(missing)}")
        if unexpected:
            parts.append(f"unexpected {len(unexpected)}: {_examples(unexpected)}")
        return cls(
            name=name,
            passed=not missing and not unexpected,
            expected=f"{len(expected_set)} {subject}",
            actual=f"{len(actual_set)} {subject}",
            detail="; ".join(parts) or detail,
        )

    @classmethod
    def forbidding(
        cls,
        name: str,
        *,
        subject: str,
        found: int,
        examples: Sequence[str] = (),
        detail: str = "",
    ) -> "Check":
        """A claim that a set is empty: dangling endpoints, unevidenced facts, identity edges.

        `examples` may be shorter than `found` — the queries that produce these collect a
        handful of offenders rather than all of them, because a check that returned every one
        of 17,127 offenders would be a second failure on top of the first.
        """
        parts = [f"for example {_examples(examples)}"] if examples else []
        if detail:
            parts.append(detail)
        return cls(
            name=name,
            passed=found == 0,
            expected=f"0 {subject}",
            actual=f"{found} {subject}",
            detail="; ".join(parts),
        )

    def describe(self) -> str:
        line = f"{'PASS' if self.passed else 'FAIL'}  {self.name}"
        if self.passed:
            return f"{line}  ({self.actual})"
        line += f"\n      expected: {self.expected}\n      actual:   {self.actual}"
        return f"{line}\n      detail:   {self.detail}" if self.detail else line


class VerificationReport(BaseModel):
    """Every check one verification ran, and the two counts that put them in context.

    `passed` is derived from the checks rather than stored, so a report cannot claim success
    while carrying a failure — the state is not representable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_run_id: str
    node_count: int
    relationship_count: int
    expected_node_count: int
    expected_relationship_count: int
    checks: tuple[Check, ...] = ()

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def check(self, name: str) -> Check:
        """The named check, or `KeyError` — a test asserting on a check that this verification
        never ran must fail loudly rather than silently assert nothing."""
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(f"no check named {name!r}; ran {[c.name for c in self.checks]}")

    def describe(self) -> str:
        header = (
            f"{'PASSED' if self.passed else 'FAILED'}  {self.graph_run_id}  "
            f"{self.node_count}/{self.expected_node_count} nodes  "
            f"{self.relationship_count}/{self.expected_relationship_count} relationships  "
            f"({len(self.failures)} of {len(self.checks)} checks failed)"
        )
        return "\n".join([header, *(check.describe() for check in self.checks)])


__all__ = [
    "MAX_EXAMPLES",
    "Check",
    "VerificationReport",
    "render",
]
