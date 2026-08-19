"""Deterministic, readable ids for candidates, packages and runs.

Responsibility: identity, and nothing else. Pure functions of their arguments — no clock, no
filesystem, no environment, no randomness — so a second run over the same graph mints the same
names and two runs that differ in anything that changes their content cannot collide.

The rule every id here follows is `extraction/core/identifiers.py`'s: **readable segments
never carry uniqueness, the digest does.** The segments exist because these ids surface in
evidence panels and in `data/story_runs/` directory listings, and a reader should be able to
tell what a directory holds without opening it. `digest` itself is imported rather than
restated — one hashing rule in one place, and `EmptyIdentityError` already says the right
thing when an id is asked for from nothing.

**What goes in a digest.** Structural inputs only: ids, version strings, hashes, and the
parameters that decide *what is in* a run. Never a measured value, never a threshold, never
anything a model produced. `detector_version` and `policy_version` are inside `candidate_id`
so a threshold or canonicalisation change **mints a new candidate** rather than silently
mutating one (§6.11); the selection flags and the budget parameters are inside `story_run_id`
for the same reason, and because leaving them out was a real defect — `--limit 3` and
`--limit 20` minted one id, and atomic finalisation would then have replaced one run with the
other (§14).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from extraction.core.identifiers import DIGEST_CHARS, EmptyIdentityError, digest
# The layout version lives on the package root because §14 makes it a property of the
# directory layout rather than of the id function; imported rather than restated so a caller
# never has to decide which of two constants is authoritative.
from story import STORY_LAYOUT_VERSION

from .models import BudgetParameters, RunSelection, canonical_json

#: `story-v1-<digest12>` (§14), mirroring `extract-v1-…` and `graph-v1-…`.
RUN_ID_PREFIX = "story"

_SAFE = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    """Lowercase, punctuation collapsed to hyphens. Readability, not uniqueness.

    **Restated rather than imported.** `extraction.core.identifiers._slug` is private, and
    reaching across a package boundary for a leading-underscore name is a dependency on
    somebody else's internals that no test can hold in place. The rule is four characters of
    regex and the two copies are checked against each other in
    `tests/story/test_story_keys.py`, which is the same trade §15.2 makes for the provider
    transport — restate a small thing, assert it has not drifted.
    """
    return _SAFE.sub("-", str(value).strip().lower()).strip("-")


def _require(**parts: str) -> None:
    """Every named part carries a character, or the id is refused at the point of minting.

    `digest` already refuses an input where *all* parts are blank; this refuses one where any
    required part is, because `cand:metric-move::opendoor:…` is an id that reads like an
    identity and is missing one.
    """
    empty = sorted(name for name, value in parts.items() if not str(value).strip())
    if empty:
        raise EmptyIdentityError(
            f"cannot mint an id: {', '.join(empty)} carries no characters")


def candidate_id(
    *,
    detector_id: str,
    detector_version: str,
    policy_version: str,
    scope: str,
    subject_entity_id: str,
    anchor_period_keys: Sequence[str],
    metric_ids: Sequence[str] = (),
    anchor_input_ids: Sequence[str] = (),
) -> str:
    """`cand:{detector_slug}:{scope_slug}:{subject}:{anchor}:{digest12}` — §6.11.

        digest12 = digest(detector_id, detector_version, policy_version,
                          *sorted(metric_ids), *sorted(anchor_input_ids))

    Both id lists are sorted **here**, so the order a detector happened to visit its
    observations in cannot change a candidate's identity. `anchor_period_keys` is sorted too,
    for the readable segment only: it carries no uniqueness, but an id whose printed form
    depended on argument order would still be two strings for one thing in
    `candidates.jsonl`.

    `scope` is the detector's own naming of what the candidate is about — the joined metric
    ids for a metric detector, the event type for `leadership_change`. It is a segment and not
    a digest part on purpose: two candidates that agreed on every digest input and differed in
    scope would be one candidate whose scope was rendered two ways, which is a detector bug
    the digest should not paper over.

    Worked, matching §6.11's examples:

        cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:<digest12>
        cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:opendoor:2022Q3:…
        cand:leadership-change:executive-change:opendoor:2025-09-10:<digest12>

    The leadership anchor is the **announcement** date, which is why it differs from the event
    id — that reads `evt:executive-change:undated:…` because `occurred_on` is null on all three
    executive changes. The candidate carries `date_basis: announced` so the two reconcile.
    """
    _require(
        detector_id=detector_id,
        detector_version=detector_version,
        policy_version=policy_version,
        scope=scope,
        subject_entity_id=subject_entity_id,
    )
    if not anchor_period_keys:
        raise EmptyIdentityError(
            f"{detector_id}: a candidate with no anchor period has nothing to be about")
    return ":".join((
        "cand",
        # `detector:metric_move` -> `metric-move`. The `detector:` head is constant across
        # every detector and would be nine characters of noise in every id.
        slug(detector_id.split(":", 1)[-1]),
        slug(scope),
        slug(subject_entity_id),
        "_".join(sorted(anchor_period_keys)),
        digest(detector_id, detector_version, policy_version,
               *sorted(metric_ids), *sorted(anchor_input_ids)),
    ))


def candidate_slug(value: str) -> str:
    """The readable half of a candidate id, for use as a segment of a package id.

    Drops the constant `cand` head and the trailing digest — the digest is re-derived into
    `package_id`'s own, and repeating it would make the segment claim a uniqueness it is not
    carrying.
    """
    parts = value.split(":")
    if len(parts) < 3 or parts[0] != "cand":
        # Not a candidate id this module minted. Slug it whole rather than guess at its
        # shape: a wrong segment is readable and harmless, a wrong digest is not.
        return slug(value)
    return "-".join(slug(part) for part in parts[1:-1])


def package_id(
    *,
    candidate_id: str,
    package_version: str,
    graph_run_id: str,
    run_complete_sha256: str,
    ontology_definition_hash: str,
    fact_ids: Sequence[str] = (),
    passage_ids: Sequence[str] = (),
    budget: BudgetParameters | None = None,
) -> str:
    """`pkg:{candidate_slug}:{digest12}` — §10.3.

    The digest covers the package version, the candidate, the graph run, **the extraction
    run's `run.complete` digest**, the ontology hash, the sorted fact and passage ids, and the
    budget parameters.

    `run_complete_sha256` and not `extraction_run_id`: §18 measured
    `extract-v1-lexical-2422c4252c07` naming two different sets of bytes two days apart, so
    the run id is not an identity and the marker's digest is the only field that distinguishes
    them. The budget parameters are in because a package built at four primary passages and
    one built at six are different packages, and §10.2's caps are the thing that decides.
    """
    _require(
        candidate_id=candidate_id,
        package_version=package_version,
        graph_run_id=graph_run_id,
        run_complete_sha256=run_complete_sha256,
        ontology_definition_hash=ontology_definition_hash,
    )
    parameters = budget if budget is not None else BudgetParameters()
    return ":".join((
        "pkg",
        candidate_slug(candidate_id),
        digest(
            f"package_version={package_version}",
            f"candidate_id={candidate_id}",
            f"graph_run_id={graph_run_id}",
            f"run_complete_sha256={run_complete_sha256}",
            f"ontology_definition_hash={ontology_definition_hash}",
            "facts=" + ",".join(sorted(fact_ids)),
            "passages=" + ",".join(sorted(passage_ids)),
            *parameters.digest_parts(),
        ),
    ))


def package_content_digest(payload: Mapping[str, Any]) -> str:
    """`sha256` over the package's canonical JSON — §10.3, full length, not a `digest12`.

    Fed `StoryEvidencePackage.digestible_payload()`, which is the package with the field that
    holds this value removed; hashing a structure that contains its own hash has no fixed
    point. `digest` over a single part joins nothing, so this is exactly
    `sha256(canonical_json)` and the repository still has one hashing function.
    """
    return digest(canonical_json(payload), length=64)


def draft_content_sha256(payload: Mapping[str, Any]) -> str:
    """`sha256` over a draft's canonical JSON — §13.17's `draft_content_sha256`.

    Over the *structured* draft rather than over rendered prose: the bindings are what §13
    checked, and a hash that covered only the text would let a rebinding pass as the same
    draft.
    """
    return digest(canonical_json(payload), length=64)


def story_run_id(
    *,
    graph_run_id: str,
    run_complete_sha256: str,
    ontology_definition_hash: str,
    config_hash: str,
    prompt_version: str,
    provider_id: str,
    model_id: str,
    provider_model_id: str,
    temperature: float,
    max_tokens: int,
    schema_digests: Mapping[str, str],
    detector_versions: Mapping[str, str],
    policy_version: str,
    ranking_policy_version: str,
    selection: RunSelection,
    budget: BudgetParameters | None = None,
    story_layout_version: str = STORY_LAYOUT_VERSION,
) -> str:
    """`story-v{major}-{digest12}` over every behaviour-changing input — §14.

    Derived, with **no clock**, matching `graph/core/manifest.py:104-139`. The property the
    tests assert is not the field list but the behaviour it exists for: *two runs differing in
    any one digest input get different ids; two differing in none get the same id.* The field
    list is the thing that drifted in the first draft, so asserting it directly would assert
    the drift.

    Five inputs are here because leaving them out was a measured defect rather than an
    oversight:

    * `selection` — `--limit 3` and `--limit 20` minted one id, and §1.6's atomic
      finalisation would then `os.replace` the second run's directory over the first's.
    * `budget` — §10.2's caps decide what is in every package in the run.
    * `provider_model_id` — the extraction data shows it is a filesystem path
      (`…/Qwen3.5-9B-Q4_K_M.gguf`). Swapping the GGUF behind an unchanged `model_id` changes
      every generation, and without this nothing would notice.
    * `provider_id` — **added 2026-08-19** (MULTI_PROVIDER_OPENAI §5.2, finding F2). The digest
      already covered both model identifiers and nothing about *which adapter* produced them,
      so a Qwen run and an OpenAI run over one graph, one config and one candidate minted one
      `story-v1-…` and §1.6's finalisation would have replaced one with the other. It is not
      implied by `model_id`: a local llama.cpp server answers to any model string, so two
      providers can be configured with one name and the run id would not notice. Beside it,
      `StoryRunManifest.provider_id` records the same value so a reader holding two directories
      can see why they are two.
    * `ranking_policy_version` — §6.10's score decides which candidate becomes a post, and
      neither `detector_versions` nor `policy_version` moves when a scoring term does. Founder
      gate G2 changed the units term with nothing in the id to show for it, which is the
      `selection` defect again. It arrives as a **parameter** rather than as an import because
      `core/` may not import a stage (`test_core_never_imports_a_stage_a_provider_or_the_cli`);
      `story.stages.ranking.RANKING_POLICY_VERSION` is the value a caller passes, exactly as
      `detector_versions` and `policy_version` are already threaded in from the detectors.

    Parts are labelled `name=value` so an empty optional keeps its slot instead of shifting
    every part after it, and so two string inputs cannot be swapped without changing the
    digest — the discipline `extraction/core/identifiers.py` states for `xbrl_structural_position`.
    """
    _require(
        graph_run_id=graph_run_id,
        run_complete_sha256=run_complete_sha256,
        ontology_definition_hash=ontology_definition_hash,
        config_hash=config_hash,
        prompt_version=prompt_version,
        provider_id=provider_id,
        model_id=model_id,
        provider_model_id=provider_model_id,
        policy_version=policy_version,
        ranking_policy_version=ranking_policy_version,
        story_layout_version=story_layout_version,
    )
    parameters = budget if budget is not None else BudgetParameters()
    parts = (
        f"story_layout_version={story_layout_version}",
        f"graph_run_id={graph_run_id}",
        f"run_complete_sha256={run_complete_sha256}",
        f"ontology_definition_hash={ontology_definition_hash}",
        f"config_hash={config_hash}",
        f"prompt_version={prompt_version}",
        f"provider_id={provider_id}",
        f"model_id={model_id}",
        f"provider_model_id={provider_model_id}",
        # Rendered with `repr`-stable formatting: `0.0` and `0` are the same temperature and
        # must not be two ids, and `float` is what the config layer hands over.
        f"temperature={float(temperature)!r}",
        f"max_tokens={int(max_tokens)}",
        "schema_digests=" + ",".join(
            f"{name}={schema_digests[name]}" for name in sorted(schema_digests)),
        "detector_versions=" + ",".join(
            f"{name}={detector_versions[name]}" for name in sorted(detector_versions)),
        f"policy_version={policy_version}",
        f"ranking_policy_version={ranking_policy_version}",
        *selection.digest_parts(),
        *parameters.digest_parts(),
    )
    return "-".join((
        RUN_ID_PREFIX,
        "v" + story_layout_version.split(".")[0],
        digest(*parts),
    ))


__all__ = [
    "DIGEST_CHARS",
    "RUN_ID_PREFIX",
    "STORY_LAYOUT_VERSION",
    "candidate_id",
    "candidate_slug",
    "draft_content_sha256",
    "package_content_digest",
    "package_id",
    "slug",
    "story_run_id",
]
