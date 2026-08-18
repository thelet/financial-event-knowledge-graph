"""One reading of a built package, for every surface that shows one — §4 S6's canonical mapper.

Responsibility: project a `StoryEvidencePackage` into the rows a reader is shown, and be the
**only** place that decides what a row says about itself. No HTTP, no graph, no run state, no
pipeline: this module takes a package and returns dictionaries.

**Why it exists rather than four serialisers agreeing by convention.** §4 S6 requires the same
evidence item to carry the same role in `POST /demo/evidence-package`, `GET /demo/runs/{id}/
sources`, the story-suggestion payloads, the stored artifacts and the UI DTO, and it forbids
per-serialiser patching *because that is how the inconsistency arose*: `_sources_payload` wrote
its own `role` from the §10 section a passage sat in — `"primary"`, `"context"`,
`"explanatory"`, `"diagnostic"` — while the package endpoint dumped the model and returned
`PackagedPassage.role`. Two vocabularies, one field name, and a `diagnostic_passages` row read
as `"diagnostic"` on one endpoint and `warning_only` on the other. A shared table would have
been agreed and then drifted; a shared function cannot.

**A section is a place and a role is a claim, and both are rendered.** S2 split them
deliberately and the split is load-bearing here: `diagnostic_passages` is named for *why a row
was collected* — `find_counter_evidence` pointed at it — and each row's `EvidenceRole` says
*what it turned out to be*, which for the inventory story's shareholder letter is
`primary_support` carrying an `AMBIGUOUS_ALIAS` in `diagnostic_codes`. Rendering only the
section would call that passage a diagnostic; rendering only the role would lose the reason it
is in the package at all. Every row carries `section` **and** `role`, and `SECTION_OF_ROLE` is
not a table here because there is no such function.

**`role: None` is unreachable and this module is where that is enforced.** `PackagedPassage.role`
has been required since S1, so a package that reached this file has one; `role_of` raises
`UnroledPassage` rather than defaulting, because every candidate default is a claim about
evidence (S1's own argument) and a `None` that reached a panel would render as an unlabelled
passage beside a labelled one.

**The five fact groups are §4 S6's own division** — observed, derived, semantic, company
context, comparison rules — and `model_facts` is what the *"Facts sent to the model"* section
renders. Two properties of that payload are worth stating because both are easy to get quietly
wrong:

* **An unavailable row stays a row.** The corpus holds no company description — not on the
  ontology instance, not on any `:Entity` node, and no `:Entity` node has a `description`
  property key at all (S4, measured) — so `identity_facts` carries one with `available: false`
  and an empty `value`. It is rendered as unavailable rather than dropped, because a panel that
  omitted it would leave a reader unable to tell *"nobody asked"* from *"the corpus was asked
  and has nothing"*, and would leave the gap for a model to fill.
* **`available: null` in the section ledger means nobody counted**, never *"the same as
  carried"*. S5 put the `None` there on purpose: *"4 of 4 available"* about a section that had
  nine is a false statement. `section_summary` returns `available_known: false` and no number.

**A table passage carries its grid, and for the same reason it carries its role.** 503 of the
corpus's 8,776 passages are flattened tables and 2,690 of 2,704 evidence edges point into one
*(verified live 2026-08-18)*, so "the evidence" is almost always one cell of a grid. Every
passage row therefore carries `cell_marks` — where each fact and each citation lands — and
`grid`, the rows and columns those coordinates index, both built by `story.demo_ui.table_grid`
and by nothing else. A renderer that placed the highlight itself would be the same
per-serialiser patching this module exists to prevent, one layer down: it would be a third
opinion about where a cell is, beside `resolve_cell`'s and the verifier's.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from story.core.models import (
    EvidenceRole,
    FactKind,
    PackagedPassage,
    StoryEvidencePackage,
    WarningCategory,
)
from story.stages.packaging import section_bounds
from story.stages.packaging import warning_codes

from . import table_grid

# ---------------------------------------------------------------------------------------
# Roles.
# ---------------------------------------------------------------------------------------


class UnroledPassage(ValueError):
    """A passage reached a serialiser without a role. §4 S6: `role: None` must be unreachable."""


#: The label a reader sees. §4 S6 names five of these six in one line — *"primary support ·
#: corroborating support · context · counter-evidence · unusable"* — and `warning_only` is the
#: sixth because S2 created it: it is the role of a row `find_counter_evidence` produced that
#: does **not** dispute the story, and calling it "counter-evidence" is the whole defect §1
#: measured.
ROLE_LABEL: Mapping[EvidenceRole, str] = {
    EvidenceRole.PRIMARY_SUPPORT: "primary support",
    EvidenceRole.CORROBORATING_SUPPORT: "corroborating support",
    EvidenceRole.CONTEXT: "context",
    EvidenceRole.COUNTER_EVIDENCE: "counter-evidence",
    EvidenceRole.WARNING_ONLY: "data-quality diagnostic",
    EvidenceRole.UNUSABLE: "unusable",
}

#: What each role means for what a reader may do with the row. Written here and nowhere else,
#: for `ROLE_LABEL`'s reason.
ROLE_DESCRIPTION: Mapping[EvidenceRole, str] = {
    EvidenceRole.PRIMARY_SUPPORT:
        "the passage a packaged fact was read from; a citation resolves into this text",
    EvidenceRole.CORROBORATING_SUPPORT:
        "a second, concordant source for a fact the package already carries. It agrees with "
        "the number; it does not dispute it",
    EvidenceRole.CONTEXT:
        "surrounding or explanatory disclosure. It supports nothing by itself and no fact is "
        "bound to it",
    EvidenceRole.COUNTER_EVIDENCE:
        "deterministic evidence that challenges this story. §11's planner must weigh it, and "
        "it must never be rendered as support",
    EvidenceRole.WARNING_ONLY:
        "an extraction or data-quality diagnostic about a fact the package carries. It "
        "qualifies how the fact reads and it is not a counterpoint",
    EvidenceRole.UNUSABLE:
        "content that can carry no evidence at all — structural markup, boilerplate, or too "
        "little text to hold a proposition. `unusable_reason` says which",
}

#: The §10 sections a passage can sit in, in the order a panel should read them, with what the
#: section says about *why the row was fetched*. Keyed by the package's own attribute names, so
#: there is one vocabulary rather than a display one beside a schema one.
SECTION_DESCRIPTION: Mapping[str, str] = {
    "primary_passages": "fetched because a packaged fact cites it (§10 primary_passages)",
    "context_passages": "fetched as a neighbour of a primary passage (±2)",
    "explanatory_passages": "returned by the fulltext search the candidate asked for",
    "counter_evidence": (
        "returned by `find_counter_evidence` **and** qualified by §4 S2 as genuinely "
        "disputing this story"),
    "diagnostic_passages": (
        "returned by `find_counter_evidence` and **not** qualified (§4 S2) — the row's own "
        "role says what it turned out to be. None of these disputes the thesis"),
}

SECTION_ORDER: tuple[str, ...] = tuple(SECTION_DESCRIPTION)


def role_of(passage: Any) -> EvidenceRole:
    """One passage's role, or a refusal. Never a default — see the module docstring."""
    role = getattr(passage, "role", None)
    if role is None:
        raise UnroledPassage(
            f"{getattr(passage, 'passage_id', passage)!r} reached a serialiser with no "
            "EvidenceRole. §4 S6: `role: None` must be unreachable, and every default this "
            "could take is a claim about the evidence")
    return EvidenceRole(role)


def role_block(passage: Any) -> dict[str, Any]:
    """The role fields every surface renders, from one place.

    `is_counter_evidence` is derived here rather than left to each renderer for the reason the
    §1 defect gives: a counter-evidence passage drawn identically to a supporting one turns
    evidence *against* the thesis into evidence for it, and a boolean is harder to forget than
    a string comparison.
    """
    role = role_of(passage)
    return {
        "role": role.value,
        "role_label": ROLE_LABEL[role],
        "role_description": ROLE_DESCRIPTION[role],
        "is_counter_evidence": role is EvidenceRole.COUNTER_EVIDENCE,
        "is_support": role in (EvidenceRole.PRIMARY_SUPPORT,
                               EvidenceRole.CORROBORATING_SUPPORT),
    }


def passage_payload(
    passage: PackagedPassage, *, section: str, also_in: Sequence[str] = (),
    marks: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One passage as every surface serialises it: the whole row, plus role, plus section,
    plus — for a flattened table — the grid it was flattened from with its cited cells in place.

    The model dump comes first and the derived fields are written over it, so `role` on the
    wire is `PackagedPassage.role` by construction and cannot be a second opinion.

    `also_in` names the **other** sections the same `passage_id` appears in, and it is not
    hypothetical — see `sections_by_passage`.

    `marks` are `table_grid`'s rows for the evidence that lands in this passage — one per
    packaged fact read out of it, plus one per citation pointing into it where the caller has a
    draft. They are a parameter rather than something derived here because this function is
    given a passage and not a package: the fact join is `passage_rows`' and the citation join
    belongs to the endpoint that holds the draft. Both go through `table_grid.grid_of`, so the
    two endpoints cannot end up with two accounts of where a cell is — the same argument that
    put `role` here.
    """
    if section not in SECTION_DESCRIPTION:
        raise KeyError(f"{section!r} is not a §10 passage section: {sorted(SECTION_DESCRIPTION)}")
    payload = passage.model_dump(mode="json")
    payload.update(role_block(passage))
    payload["section"] = section
    payload["section_description"] = SECTION_DESCRIPTION[section]
    payload["row_id"] = f"{section}:{passage.passage_id}"
    payload["also_in"] = list(also_in)
    payload["cell_marks"] = [dict(mark) for mark in marks]
    payload["grid"] = table_grid.grid_of(passage, marks=payload["cell_marks"])
    return payload


def sections_by_passage(package: StoryEvidencePackage) -> dict[str, tuple[str, ...]]:
    """Which §10 sections each `passage_id` appears in — ordinarily one, and not always.

    **Measured live on 2026-08-05, on `cand:cross-metric-divergence:adjusted-gross-profit-
    contribution-profit:opendoor:2021Q4` and two others**:
    `q42021formxex992sharehol.htm#p10` is in `primary_passages` as `primary_support` **and** in
    `counter_evidence` as `counter_evidence`. That is §1.1's own sentence — one passage
    simultaneously a source and a declared contradiction — except that S2 has since made the
    second half earn it: the basis is `scope_undermines_comparison`, one of the six, and the
    row carries `MISSING_PERIOD`. So it is a passage genuinely playing two parts, not the
    document-grain accident §1 measured.

    It matters here because a serialiser keyed on `passage_id` **silently picks one**: the
    previous `_sources_payload` built `{passage.passage_id: passage}` across all five sections
    in order, so `counter_evidence` overwrote `primary_passages` and the panel showed a
    supporting passage as a counterpoint and nothing else. `passage_rows` returns one row per
    occurrence instead, and each row names the others.
    """
    found: dict[str, list[str]] = {}
    for section, rows in passage_sections(package).items():
        for row in rows:
            found.setdefault(row.passage_id, []).append(section)
    return {passage_id: tuple(sections) for passage_id, sections in found.items()}


def fact_cell_marks(package: StoryEvidencePackage) -> dict[str, list[dict[str, Any]]]:
    """Where each packaged fact's value sits in the passage it was read from, by passage id.

    The join lives here because this module is the one that holds a package and a passage at
    the same time; `table_grid` is given the pair and decides nothing about which pair.

    A fact whose `passage_id` names no passage in the package produces no mark and is not an
    error here — §13.7 refuses a *citation* that does that, and `_sources_payload` reports the
    unresolved ones. Silently inventing a passage to hang the mark on would be worse.
    """
    passages = {passage.passage_id: passage
                for rows in passage_sections(package).values() for passage in rows}
    marks: dict[str, list[dict[str, Any]]] = {}
    for fact in package.facts:
        passage = passages.get(fact.passage_id or "")
        if passage is None:
            continue
        marks.setdefault(passage.passage_id, []).append(table_grid.fact_mark(fact, passage))
    return marks


def passage_rows(
    package: StoryEvidencePackage,
    *,
    extra_marks: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Every passage occurrence, serialised once per section it occurs in.

    Sorted by `passage_id` then by `SECTION_ORDER`, so two runs over one package produce one
    ordering and a panel keyed on `row_id` is stable.

    Every row carries the marks for the facts read out of it. `extra_marks` is how a caller
    holding a *draft* adds the citation marks — `GET /demo/runs/{id}/sources` does, and
    `POST /demo/evidence-package` cannot, because a package has no citations. A parameter
    rather than a second serialiser patching `row["grid"]` afterwards: the mark list and the
    grid's `marked_by` indices are built from one sequence, and patching one of them would
    leave the indices pointing at the wrong mark.
    """
    elsewhere = sections_by_passage(package)
    facts = fact_cell_marks(package)
    added = dict(extra_marks or {})
    rows = [
        passage_payload(
            passage, section=section,
            also_in=[other for other in elsewhere[passage.passage_id] if other != section],
            marks=[*facts.get(passage.passage_id, ()),
                   *added.get(passage.passage_id, ())])
        for section, passages in passage_sections(package).items()
        for passage in passages
    ]
    order = {name: index for index, name in enumerate(SECTION_ORDER)}
    rows.sort(key=lambda row: (row["passage_id"], order[row["section"]]))
    return rows


def passage_sections(
    package: StoryEvidencePackage,
) -> dict[str, tuple[PackagedPassage, ...]]:
    """The five passage sections, in `SECTION_ORDER`, including the empty ones.

    Empty sections are kept because a dropped `counter_evidence: 0` reads as *"not
    considered"*, and the endpoint reports separately whether it was asked for.
    """
    return {name: tuple(getattr(package, name)) for name in SECTION_ORDER}


def role_counts(package: StoryEvidencePackage) -> dict[str, int]:
    """How many rows carry each role, every role, including the zeros."""
    counted = {role.value: 0 for role in EvidenceRole}
    for rows in passage_sections(package).values():
        for row in rows:
            counted[role_of(row).value] += 1
    return counted


def role_catalogue(package: StoryEvidencePackage | None = None) -> list[dict[str, Any]]:
    """The role vocabulary itself, so a panel renders labels the server declares.

    With a package it also carries this package's counts, including the zeros: a role nothing
    plays and a role nobody looked for are different facts, and only the second is reassuring.
    """
    counted = {} if package is None else role_counts(package)
    return [{"role": role.value, "label": ROLE_LABEL[role],
             "description": ROLE_DESCRIPTION[role],
             "count": counted.get(role.value)} for role in EvidenceRole]


# ---------------------------------------------------------------------------------------
# The facts the model was given.
# ---------------------------------------------------------------------------------------

#: §4 S6's five groups, each naming the package section it reads and the `FactKind` its rows
#: carry. `observed` and `derived` are two kinds inside one section, which is why the group is
#: a triple rather than a section name: `PackagedFact.fact_kind` is what separates them, and a
#: derived quantity is the writer's `Calculation` today so the group is ordinarily empty.
FACT_GROUPS: tuple[tuple[str, str, str, FactKind], ...] = (
    ("observed_facts", "Observed facts", "facts", FactKind.OBSERVED),
    ("derived_facts", "Derived facts", "facts", FactKind.DERIVED),
    ("semantic_facts", "Semantic facts", "semantic_facts", FactKind.SEMANTIC),
    ("company_context", "Company context", "identity_facts", FactKind.IDENTITY),
    ("comparison_rules", "Comparison rules", "comparability_facts", FactKind.COMPARABILITY),
)

#: What each group is, in one sentence a panel can print under its heading.
GROUP_DESCRIPTION: Mapping[str, str] = {
    "observed_facts": "one reading of one filed cell, with the passage it was read from",
    "derived_facts": (
        "a quantity computed from readings. Empty on every package built so far: a derivation "
        "is the writer's `Calculation`, recomputed by §13.9, and is not a packaged fact"),
    "semantic_facts": (
        "what the ontology declares a metric means. Authoritative and read-only — the numbers "
        "in the post have no declared meaning without them"),
    "company_context": (
        "what is known about the subject, structurally. A row marked unavailable is the corpus "
        "answering, and nothing may fill it in"),
    "comparison_rules": (
        "the ontology's rules about which metrics may be compared with which. The planner "
        "needs the rule to avoid writing the sentence"),
}

#: Why nothing in this panel is editable, stated once. §4 S6: semantic and identity facts render
#: read-only and authoritative; prompt instructions stay editable and ontology facts are not
#: reachable from the prompt UI. Observed facts are not editable either, for a stronger reason —
#: they are what a filing says.
NOT_EDITABLE_BECAUSE: Mapping[str, str] = {
    "observed": "read from a filing; §2's line is that code chooses facts and the model chooses "
                "words, and neither this panel nor the prompt UI may edit one",
    "derived": "recomputed by §13.9 from the facts it names",
    "semantic": "the ontology is authoritative (C4) and is not reachable from the prompt UI",
    "identity": "the ontology is authoritative (C4) and is not reachable from the prompt UI",
    "comparability": "the ontology is authoritative (C4) and is not reachable from the prompt UI",
}


def in_model_slice(section: str) -> bool:
    """Whether a section reaches a model at all.

    Read from `section_bounds.PROMPT_EXCLUDED_SECTIONS` rather than answered `True`: the
    exclusion list is the packaging stage's own, it is what `prompt_token_estimate` is taken
    over, and a panel that hard-coded the answer would keep saying it after the list moved.
    """
    return section not in section_bounds.PROMPT_EXCLUDED_SECTIONS


def _observed_statement(fact: Any, value_display: str) -> str:
    """A sentence rendered from the row's own fields — never a paraphrase of a filing.

    `PackagedFact` carries no `statement`: it is a metric, a period, a value and a unit. The
    panel needs one line per fact, so the fields are joined in a fixed order and the row also
    carries every component separately, so a reader comparing them can see that this string is
    a rendering rather than a source. `statement_source` says so on the row.
    """
    unit = f" {fact.unit}" if fact.unit else ""
    return f"{fact.metric_label} · {fact.period_key} = {value_display}{unit}"


def _fact_rows(
    package: StoryEvidencePackage,
    section: str,
    kind: FactKind,
    display: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if section == "facts":
        for fact in package.facts:
            if fact.fact_kind is not kind:
                continue
            shown = display(fact.value)
            rows.append({
                "fact_id": fact.observation_id,
                "fact_kind": fact.fact_kind.value,
                "statement": _observed_statement(fact, shown),
                "statement_source": "rendered from this row's own fields",
                "value": fact.value,
                "value_display": shown,
                "unit": fact.unit,
                "period_key": fact.period_key,
                "metric_id": fact.metric_id,
                "source": fact.document_id or fact.evidence_source_id or "",
                "source_detail": f"{fact.source_lane} · {fact.validation_state}",
                "passage_id": fact.passage_id,
                # The token a §12 or §13.7 refusal names when it refuses a citation to this
                # fact, and the only citation field the model writes (TABLE_CELL_CITATIONS
                # §3.2). Printed on the row so a reader can match `ev:…:r5c2` in a rejection to
                # the line it came from without parsing the handle.
                "evidence_handle": fact.evidence_handle,
                "evidence_handle_absent_because": (
                    "" if fact.evidence_handle is not None else
                    "this fact names no filed passage, so no handle was minted and V1 refuses "
                    "a citation to it (§13.7.2)"),
                "cell": None if fact.cell is None else fact.cell.model_dump(mode="json"),
                "row_label": fact.row_label,
                "column_label": fact.column_label,
                "quoted_text": fact.quoted_text,
                "authoritative": False,
                "authority": "a filing, through the extraction run this graph was built from",
                "editable": False,
                "not_editable_because": NOT_EDITABLE_BECAUSE[fact.fact_kind.value],
                "available": True,
                "warning_codes": list(fact.warning_codes),
                "corroborating_document_ids": list(fact.corroborating_document_ids),
                "corroborating_observation_ids": list(fact.corroborating_observation_ids),
                "in_model_slice": in_model_slice(section),
            })
        return rows

    for fact in getattr(package, section):
        rows.append({
            "fact_id": fact.fact_id,
            "fact_kind": fact.fact_kind.value,
            "statement": fact.statement,
            "statement_source": "the ontology's own words",
            "value": getattr(fact, "value", ""),
            "value_display": getattr(fact, "value", ""),
            "attribute": getattr(fact, "attribute", ""),
            "metric_id": getattr(fact, "metric_id", ""),
            "metric_ids": list(getattr(fact, "metric_ids", ())),
            "entity_id": getattr(fact, "entity_id", ""),
            "rule_id": getattr(fact, "rule_id", ""),
            "source": fact.source,
            "source_detail": "",
            "authoritative": fact.authoritative,
            "authority": ("the ontology, which is authoritative for this declaration"
                          if fact.authoritative else
                          "not read from the graph — see the capability limitation beside it"),
            "editable": fact.editable,
            "not_editable_because": ("" if fact.editable
                                     else NOT_EDITABLE_BECAUSE[fact.fact_kind.value]),
            # `available` exists on `IdentityFact` alone, and `True` is the honest default for
            # the two kinds that cannot be unavailable: a declaration the ontology makes is
            # there or the row does not exist.
            "available": bool(getattr(fact, "available", True)),
            "citations": [citation.model_dump(mode="json") for citation in fact.citations],
            "warning_codes": list(fact.warning_codes),
            "in_model_slice": in_model_slice(section),
        })
    return rows


def section_summary(package: StoryEvidencePackage, section: str) -> dict[str, Any]:
    """One section's ledger row, with `available: null` rendered as *unknown* and never as carried.

    S5's `SectionLedgerEntry.available` is `None` where an upstream §10.2 cap dropped rows and
    reported no count. `available_known` is the field a renderer branches on; `available` stays
    `null` beside it rather than being filled with `carried`, which would turn *"the section had
    nine"* into *"the section had four"*.
    """
    row = next((entry for entry in package.budget.section_ledger
                if entry.section == section), None)
    carried = package.budget.section_counts.get(section, 0)
    if row is None:
        return {"section": section, "available": None, "available_known": False,
                "carried": carried, "dropped": 0, "reasons": [], "protected": False,
                "required_dropped": False, "complete": None}
    return {
        "section": section,
        "available": row.available,
        "available_known": row.available is not None,
        "carried": row.carried,
        "dropped": row.dropped,
        "reasons": list(row.reasons),
        "protected": row.protected,
        "required_dropped": row.required_dropped,
        # `None` where `available` is unknown: "everything available was carried" is exactly the
        # claim the missing count makes uncheckable.
        "complete": None if row.available is None else row.available == row.carried,
    }


def model_facts(
    package: StoryEvidencePackage, *, display: Any
) -> dict[str, Any]:
    """§4 S6's *"Facts sent to the model"*, in five groups.

    `display` is the number formatter the rest of the API already uses, passed in rather than
    imported: this module holds no formatting rule of its own, and one fact must read the same
    way in every panel (F14).
    """
    groups: list[dict[str, Any]] = []
    for group, label, section, kind in FACT_GROUPS:
        rows = _fact_rows(package, section, kind, display)
        groups.append({
            "group": group,
            "label": label,
            "description": GROUP_DESCRIPTION[group],
            "section": section,
            "fact_kind": kind.value,
            "count": len(rows),
            "unavailable": sum(1 for row in rows if not row["available"]),
            "in_model_slice": in_model_slice(section),
            "ledger": section_summary(package, section),
            "rows": rows,
        })
    return {
        "groups": groups,
        "total": sum(group["count"] for group in groups),
        "prompt_excluded_sections": list(section_bounds.PROMPT_EXCLUDED_SECTIONS),
        "slice_note": (
            "every row below is inside the slice a model can be shown; "
            + ", ".join(section_bounds.PROMPT_EXCLUDED_SECTIONS)
            + " are the package sections that reach no prompt. What a section had before the "
              "budget bound it is in its ledger row, and an unknown count is shown as unknown."),
    }


# ---------------------------------------------------------------------------------------
# Warnings, as a panel has to read them.
# ---------------------------------------------------------------------------------------

#: What each §4 S5 category means for a reader, so the panel can say *"a limit of this build"*
#: rather than printing five kinds of thing as one undifferentiated list. Keyed by the enum's
#: value, and total over it — `warning_codes.category_of` never returns anything else.
CATEGORY_LABEL: Mapping[str, str] = {
    "substantive_counter_evidence": "disputes this story",
    "fact_quality_warning": "qualifies a fact this post uses",
    "extraction_issue": "the pipeline could not finish a row",
    "retrieval_warning": "what reached the model is smaller than the corpus",
    "capability_limitation": "V1 cannot do this at all",
    "package_composition": "how this package was assembled",
}

#: The standing sentence a code carries beyond its own `detail`, for the three §4 S6 names its
#: numbers alone do not make readable. Not a re-derivation of the counts — `detail` carries
#: those and is rendered verbatim — but the consequence a reader has to be told once.
CONSEQUENCE: Mapping[str, str] = {
    warning_codes.RETRIEVAL_TRUNCATED:
        "this result is not exhaustive. The bound is stated in the detail beside it, with the "
        "ordering it kept and the lowest severity that survived; how many rows exist beyond it "
        "was not counted",
    warning_codes.SEARCH_POOL_CAPPED:
        "this result is not exhaustive: the pool was capped before the filter was applied",
    warning_codes.TOKEN_BUDGET_TRIMMED:
        "rows were dropped to fit the prompt. No protected fact was dropped — the ledger's "
        "`required_dropped` is measured from the surviving facts, not asserted",
    warning_codes.SECTION_TRUNCATED:
        "a §10.2 cap bound this section. The ledger row says how many were carried, and says "
        "so rather than guessing when the number available was never counted",
}


def warning_payload(warning: Any, explanation: Mapping[str, Any]) -> dict[str, Any]:
    """One warning as every panel renders it: the row, its category, and its consequence."""
    category = warning_codes.category_of(warning)
    return {
        **warning.model_dump(mode="json"),
        "category": category.value,
        "category_label": CATEGORY_LABEL[category.value],
        "is_capability_limitation": category is WarningCategory.CAPABILITY_LIMITATION,
        "consequence": CONSEQUENCE.get(warning.code, ""),
        "explanation": dict(explanation),
    }


def warning_view(
    package: StoryEvidencePackage, explanations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Every warning, plus the two groupings §4 S6 asks a panel to keep apart.

    `capability_limitations` is the family that **must not reduce the post's verification
    status** (§4 S5): each fires on every package ever built, none is `REFUSE`, and none can
    become a `required_warning` because a `BUILD_PROVENANCE` code is filtered out of the
    planner's list. Rendered as a limit of V1, beside the empty section it explains.
    """
    rows = [warning_payload(warning, explanation)
            for warning, explanation in zip(package.warnings, explanations)]
    limitation_codes = {w.code for w in warning_codes.capability_limitations(package.warnings)}
    return {
        "rows": rows,
        "capability_limitations": [row for row in rows if row["code"] in limitation_codes],
        "claim_qualifying": [row["code"]
                             for row in rows
                             if row["kind"] == "claim_qualifying"],
        "blocking": [w.code for w in warning_codes.blocking(package.warnings)],
        "limitation_note": (
            "a capability limitation is a thing this version cannot do at all — no §9 tool "
            "reads `:Entity`, none returns a relationship, and the corpus holds zero "
            "`:EvidenceSource` nodes. It is not a defect in this evidence and it does not "
            "reduce the verification status of the post."),
        "section_ledger": [section_summary(package, entry.section)
                           for entry in package.budget.section_ledger],
    }


__all__ = [
    "CATEGORY_LABEL",
    "CONSEQUENCE",
    "FACT_GROUPS",
    "GROUP_DESCRIPTION",
    "NOT_EDITABLE_BECAUSE",
    "ROLE_DESCRIPTION",
    "ROLE_LABEL",
    "SECTION_DESCRIPTION",
    "SECTION_ORDER",
    "UnroledPassage",
    "fact_cell_marks",
    "in_model_slice",
    "model_facts",
    "passage_payload",
    "passage_rows",
    "passage_sections",
    "sections_by_passage",
    "role_block",
    "role_catalogue",
    "role_counts",
    "role_of",
    "section_summary",
    "warning_payload",
    "warning_view",
]
