"""What a story run is called, what it consumed, and what it produced.

Responsibility: the run's own record, and the **only clock in the package** (§14). Everything
else in `story/` is a pure function of its inputs, which is what makes `story rebuild`'s
byte-identity claim checkable; `manifest.json` carries `created_at` and the artifact digests,
which is what lets a reader tell *which* two runs were identical. That split is
`graph/core/manifest.py`'s and `extraction/core/manifest.py`'s before it.

The manifest is **written last** and is the completion marker: a `data/story_runs/<id>/`
directory without one is unambiguously incomplete, and §14's staging directory is renamed
into place only once it exists.

Three commits, not one, for the same reason the graph layer records two: `extraction_code_commit`
is what the extraction manifest recorded, `graph_code_commit` is what the projection recorded,
and `story_code_commit` is `git rev-parse HEAD` in the tree that ran *this*. Collapsing them
would let a reader believe the story run knows which source produced the facts. It knows
three things, and says so.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from story import STORY_LAYOUT_VERSION


def utc_now_iso() -> str:
    """The manifest's clock, and the only one in the layer.

    Same shape as `graph/core/manifest.py:utc_now_iso` — second precision, explicit UTC —
    so three manifests of one pipeline sort against each other without a parser.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def code_commit(root: Path | None = None) -> str | None:
    """`git rev-parse HEAD`, or `None`. Mirrors `graph/core/manifest.py:141-155`.

    `None` rather than an exception: a checkout without git is a legitimate way to run the
    agent, and a manifest saying "no commit recorded" is more useful than a run that refuses
    to finish over it.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root) if root else None,
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


@dataclass(frozen=True)
class StoryRunManifest:
    """One story run, described flatly enough to be read without the code that wrote it.

    **A frozen dataclass and not a pydantic model**, unlike everything in `core/models.py`.
    The manifest is written once and never parsed back by this package — `render()` *is* its
    contract — so the one thing pydantic buys here (validated deserialization) is the one
    thing nothing needs. `GraphRunManifest` makes the same call for the same reason.

    Every substantive field is reproducible from the inputs. `created_at` and the three code
    commits are the only entries two runs of one selection over one graph may legitimately
    differ on, and `story_run_id` digests none of them.

    `story_run_id_inputs` records the digest input list *as it was used*, because §14's
    correction was about the list and not about the algorithm: a reader holding two
    directories must be able to see why they are two directories without re-running either.
    """

    story_run_id: str
    story_layout_version: str
    created_at: str
    story_code_commit: str | None
    config_hash: str

    #: What was read. `run_complete_sha256` and not just `extraction_run_id`, because §18
    #: measured one extraction run id naming two different sets of bytes.
    graph_run_id: str
    graph_projection_version: str
    graph_code_commit: str | None
    extraction_run_id: str
    extraction_code_commit: str | None
    run_complete_sha256: str
    ontology_id: str
    ontology_version: str
    ontology_definition_hash: str

    #: Who generated, and under what. `prompt_versions` is per call site — the planner, the
    #: writer and the advisory verifier are three prompts and §15.1 wants each recorded.
    prompt_versions: dict[str, str]
    model_id: str
    provider_model_id: str
    temperature: float
    max_tokens: int
    schema_digests: dict[str, str]

    #: What decided the candidates, and — `ranking_policy_version` — what decided which of them
    #: could become a post. §6.10's score is behaviour-changing and had no version of its own
    #: until founder gate G2 changed the units term and nothing in the record moved.
    detector_versions: dict[str, str]
    policy_version: str
    ranking_policy_version: str
    selection: dict[str, Any]
    budget: dict[str, Any]

    counts: dict[str, Any]
    token_totals: dict[str, Any]
    artifacts: dict[str, str]
    story_run_id_inputs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        """The bytes written to `manifest.json`, matching the graph and extraction layers.

        `indent=2, sort_keys=True, ensure_ascii=False` and a trailing newline — deliberately
        *not* the compact encoding `canonical_json` uses for content digests. A manifest is
        read by a human and diffed by git; a digest input is neither.
        """
        return json.dumps(
            self.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_manifest(
    *,
    story_run_id: str,
    config_hash: str,
    graph_run_id: str,
    graph_projection_version: str,
    graph_code_commit: str | None,
    extraction_run_id: str,
    extraction_code_commit: str | None,
    run_complete_sha256: str,
    ontology_id: str,
    ontology_version: str,
    ontology_definition_hash: str,
    prompt_versions: dict[str, str],
    model_id: str,
    provider_model_id: str,
    temperature: float,
    max_tokens: int,
    schema_digests: dict[str, str],
    detector_versions: dict[str, str],
    policy_version: str,
    ranking_policy_version: str,
    selection: dict[str, Any],
    budget: dict[str, Any],
    counts: dict[str, Any],
    token_totals: dict[str, Any],
    artifacts: dict[str, str],
    story_run_id_inputs: list[str] | None = None,
    story_layout_version: str = STORY_LAYOUT_VERSION,
    root: Path | None = None,
    now: str | None = None,
) -> StoryRunManifest:
    """Assemble the manifest, reading the clock and `git rev-parse` exactly once.

    `now` is injectable so a determinism test can build two manifests that differ in nothing;
    it defaults to the real clock rather than being required, because a caller that has to
    supply a timestamp will eventually supply the wrong one.
    """
    return StoryRunManifest(
        story_run_id=story_run_id,
        story_layout_version=story_layout_version,
        created_at=now if now is not None else utc_now_iso(),
        story_code_commit=code_commit(root),
        config_hash=config_hash,
        graph_run_id=graph_run_id,
        graph_projection_version=graph_projection_version,
        graph_code_commit=graph_code_commit,
        extraction_run_id=extraction_run_id,
        extraction_code_commit=extraction_code_commit,
        run_complete_sha256=run_complete_sha256,
        ontology_id=ontology_id,
        ontology_version=ontology_version,
        ontology_definition_hash=ontology_definition_hash,
        prompt_versions=dict(prompt_versions),
        model_id=model_id,
        provider_model_id=provider_model_id,
        temperature=float(temperature),
        max_tokens=int(max_tokens),
        schema_digests=dict(schema_digests),
        detector_versions=dict(detector_versions),
        policy_version=policy_version,
        ranking_policy_version=ranking_policy_version,
        selection=dict(selection),
        budget=dict(budget),
        counts=dict(counts),
        token_totals=dict(token_totals),
        artifacts=dict(artifacts),
        story_run_id_inputs=list(story_run_id_inputs or []),
    )


__all__ = [
    "StoryRunManifest",
    "build_manifest",
    "code_commit",
    "utc_now_iso",
]
