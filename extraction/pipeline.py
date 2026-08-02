"""The orchestrator. It owns ordering, and nothing else.

Six steps, each of which is a stage that runs on its own: select, extract, catalog, verify,
report, finalize. Nothing here decides what a claim is, what a catalog holds, or what a check
means; move any of those in and the stages stop being independently runnable, which is the
property that makes `rebuild` and `report` possible against a directory that already exists.

**The catalogs are built from the persisted file, not from the object in memory** (STAGE_13
§7). `run` writes `lane_outputs.jsonl` into the staging directory and then reads it back before
building anything. That round trip is not ceremony: it is what makes "two builds from the same
persisted outputs are byte-identical" a claim about the files rather than about a data
structure, and it is what `rebuild_catalogs` exercises on a finished run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import ExtractionContext
from .core.config import RUN_LAYOUT_VERSION
from .core.manifest import EXTRACTOR_VERSION, RunManifest, code_commit, utc_now_iso
from .core.run_directory import (
    MANIFEST_FILENAME,
    RunDirectory,
    RunDirectoryWriter,
    make_run_id,
)
from .stages.catalog import CATALOG_FILES, CatalogSet, build as build_catalogs
from .stages.extract import ExtractRequest, ExtractResult, lane_outputs
from .stages.narrative.event_prompt import EVENT_PROMPT_VERSION
from .stages.narrative.prompt import PROMPT_VERSION
from .stages.report import REPORT_FILENAME, build_report, render_markdown
from .stages.select import SelectionRequest
from .stages.verify import VerificationResult, verify


@dataclass
class RunOutcome:
    run_id: str
    path: Path
    catalogs: CatalogSet
    verification: VerificationResult
    extract: ExtractResult
    manifest: dict[str, Any] = field(default_factory=dict)
    report_markdown: str = ""

    @property
    def ok(self) -> bool:
        return self.verification.ok


def run_id_for(context: ExtractionContext) -> str:
    """Deterministic and readable: same inputs, same directory, so two runs can be compared."""
    return make_run_id(
        config_hash=context.config.config_hash(),
        corpus_id=context.corpus.identity().corpus_id,
        ontology_definition_hash=context.ontology.definition_hash,
        strategy=context.scoping.strategy)


def corpus_block(context: ExtractionContext) -> dict[str, Any]:
    identity = context.corpus.identity()
    return {
        "corpus_id": identity.corpus_id,
        "documents": identity.documents,
        "passages": identity.passages,
        "table_passages": identity.table_passages,
        "content_digest": identity.content_digest,
    }


def scope_block(context: ExtractionContext) -> dict[str, Any]:
    return {
        "strategy": context.scoping.strategy,
        "scope_name": str(getattr(context.scope, "name", "")),
        "scope_version": str(getattr(context.scope, "version", "")),
    }


def bounds_block(context: ExtractionContext, request: ExtractRequest) -> dict[str, Any]:
    """What this run could and could not reach, stated as data rather than as prose.

    `recorded_answers` is the ceiling on the two model-backed lanes, and saying so is the
    condition STAGE_13 §1.1 puts on a bounded run being trustworthy at all.
    """
    return {
        "lanes": list(request.lanes),
        "candidate_limit_per_lane": request.limit,
        "documents_requested": (None if request.document_ids is None
                                else len(request.document_ids)),
        "recorded_answers_available": len(context.answers),
        "provider_calls_permitted": 0,
        "prompt_version": PROMPT_VERSION,
        "event_prompt_version": EVENT_PROMPT_VERSION,
        "model_id": context.provider_config.model,
    }


def run(context: ExtractionContext, request: ExtractRequest | None = None) -> RunOutcome:
    """Select, extract, persist, catalog, verify, report, finalize. In that order and no other."""
    request = request or ExtractRequest()
    selection = context.selector.select_from_rows(
        context.corpus.rows(),
        SelectionRequest(run_id="extraction", lanes=tuple(request.lanes)))
    extraction = context.runner.run(request, selection.candidates)

    run_id = run_id_for(context)
    writer = RunDirectoryWriter(context.config.runs_root, run_id)
    writer.begin()
    writer.write(lane_outputs.FILENAME, lane_outputs.render(extraction))

    # Read back before building. See the module docstring: the catalogs are a function of the
    # file, and only a round trip makes that true rather than intended.
    persisted = lane_outputs.parse(
        (writer.paths.staging / lane_outputs.FILENAME).read_text(
            encoding="utf-8").splitlines())

    catalogs = build_catalogs(persisted, ontology=context.ontology, corpus=context.corpus)
    rendered = catalogs.render()
    for name in CATALOG_FILES:
        writer.write(name, rendered[name])

    verification = verify(catalogs, ontology=context.ontology, passages=context.corpus)

    report = build_report(
        run_id=run_id,
        rows=catalogs.rows,
        outcomes=persisted.outcomes,
        unselected=persisted.unselected,
        ontology=context.ontology,
        corpus=corpus_block(context),
        verification=verification.as_rows(),
        bounds=bounds_block(context, request),
        scoping=scope_block(context))
    markdown = render_markdown(report)
    writer.write(REPORT_FILENAME, markdown)

    manifest = RunManifest(
        run_id=run_id,
        extractor_version=EXTRACTOR_VERSION,
        layout_version=RUN_LAYOUT_VERSION,
        config_hash=context.config.config_hash(),
        code_commit=code_commit(context.config.root),
        created_at=utc_now_iso(),
        ontology_id=str(context.ontology.metadata.ontology_id),
        ontology_definition_hash=context.ontology.definition_hash,
        corpus=corpus_block(context),
        scope=scope_block(context),
        lanes=[
            {"lane": "tables", "name": context.table_lane.name,
             "version": context.table_lane.version},
            {"lane": "narrative", "name": context.narrative_lane.name,
             "version": context.narrative_lane.version},
            {"lane": "events", "name": context.event_lane.name,
             "version": context.event_lane.version},
        ],
        provider={
            "mode": "replay_only",
            "model_id": context.provider_config.model,
            "context_tokens": context.provider_config.context_tokens,
            "recorded_answers": len(context.answers),
        },
        bounds=bounds_block(context, request),
        counts=dict(report.counts),
        verification=verification.as_rows(),
        catalog_digests=dict(writer.written),
    ).as_dict()
    writer.write(MANIFEST_FILENAME, json.dumps(
        manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    path = writer.finalize()
    return RunOutcome(
        run_id=run_id, path=path, catalogs=catalogs, verification=verification,
        extract=persisted, manifest=manifest, report_markdown=markdown)


# -- what a finished directory supports ---------------------------------------------------------


def load_lane_outputs(run_path: Path) -> ExtractResult:
    directory = RunDirectory(run_path)
    return lane_outputs.parse(directory.read(lane_outputs.FILENAME).splitlines())


def rebuild_catalogs(run_path: Path, *, ontology, corpus) -> dict[str, str]:
    """The catalogs, rebuilt from the persisted lane outputs of a finished run.

    Returns rendered bytes rather than writing them. The byte-identity claim is about what a
    rebuild *produces*, and a function that wrote over the originals could not be used to check
    itself.
    """
    persisted = load_lane_outputs(run_path)
    return build_catalogs(persisted, ontology=ontology, corpus=corpus).render()


def regenerate_report(run_path: Path, *, ontology) -> str:
    """`report.md`, rebuilt from a finished run directory and nothing else.

    Every input is a file in the directory: the catalogs, the lane outputs, and the manifest's
    record of the corpus, the bounds, the scope and the five verification results. That is why
    it comes out byte-identical — there is nothing left for it to disagree with.
    """
    directory = RunDirectory(run_path)
    manifest = directory.manifest()
    persisted = lane_outputs.parse(directory.read(lane_outputs.FILENAME).splitlines())
    rows = {name: directory.rows(name) for name in CATALOG_FILES}
    report = build_report(
        run_id=manifest["run_id"],
        rows=rows,
        outcomes=persisted.outcomes,
        unselected=persisted.unselected,
        ontology=ontology,
        corpus=manifest["corpus"],
        verification=manifest["verification"],
        bounds=manifest["bounds"],
        scoping=manifest["scope"])
    return render_markdown(report)
