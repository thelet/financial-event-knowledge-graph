"""Reader for the example claim fixtures.

The fixtures are executable documentation: each one states what it demonstrates, and each
invalid one states which codes it must trigger. Tests assert the codes, so a fixture whose
prose no longer matches the behaviour fails rather than quietly becoming wrong.

Generic over versions — a fixture is a claim, and claims are version-independent. Only the
directory comes from the version package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

from .core.models import OntologyClaim


@dataclass(frozen=True)
class ExampleClaim:
    name: str
    path: Path
    description: str
    claim: OntologyClaim
    expected_codes: frozenset[str]
    should_validate: bool

    def __str__(self) -> str:
        return self.name


def load_examples(directory: Path, should_validate: bool) -> tuple[ExampleClaim, ...]:
    """Read one fixture directory, sorted by filename so test ids are stable."""
    return tuple(_read(path, should_validate) for path in sorted(directory.glob("*.yaml")))


def iter_examples(examples_root: Path) -> Iterator[ExampleClaim]:
    yield from load_examples(examples_root / "valid", should_validate=True)
    yield from load_examples(examples_root / "invalid", should_validate=False)


def _read(path: Path, should_validate: bool) -> ExampleClaim:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    expected = frozenset(raw.get("expected_codes", ()))
    if not should_validate and not expected:
        raise ValueError(
            f"{path.name}: an invalid fixture must state the codes it expects, "
            f"otherwise it passes as long as *anything* is wrong with it"
        )
    return ExampleClaim(
        name=path.stem,
        path=path,
        description=str(raw.get("description", "")).strip(),
        claim=OntologyClaim(**raw["claim"]),
        expected_codes=expected,
        should_validate=should_validate,
    )
