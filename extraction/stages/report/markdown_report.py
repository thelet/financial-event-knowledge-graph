"""Rendering `report.md`. Formatting only — every number is looked up, none is derived.

The rule is enforced two ways and both are necessary. `tests/extraction/test_integrated_run.py`
overwrites the report's data with sentinels and asserts every rendered cell changes to match,
which catches a cell that was computed here; and an AST check asserts this module contains no
division at all, which catches a rate computed here that a sentinel would not distinguish from
a looked-up one. Step 12 shipped three tables that disagreed with the data they rendered, and
they were caught by adversarial review rather than by the suite.

No timestamp, no duration, no token count, no absolute path (STAGE_13 §7). `manifest.json` is
the one place a run records its environment; this file is a statement about the corpus, and a
statement about the corpus that changed between two identical runs would be a statement about
the clock.
"""

from __future__ import annotations

from typing import Any, Sequence

from .public import PASSAGE_LIST_CEILING, RunReport


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "**no**"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "—"
    return str(value)


def render_markdown(report: RunReport) -> str:
    lines: list[str] = []
    lines.extend(_headline(report))
    lines.extend(_coverage(report))
    lines.extend(_silences(report))
    lines.extend(_distribution(report))
    lines.extend(_verification(report))
    lines.extend(_temporal(report))
    lines.extend(_scoping(report))
    lines.extend(_follow_up(report))
    return "\n".join(lines).rstrip("\n") + "\n"


def _headline(report: RunReport) -> list[str]:
    lines = [
        f"# Extraction run `{report.run_id}`",
        "",
        "Three lanes over the normalized corpus, with every candidate's outcome recorded — "
        "including the candidates that produced nothing, which is what a coverage table "
        "exists to show.",
        "",
        "## What this run is bounded by",
        "",
        "The narrative and event lanes replay recorded provider answers and issue no new "
        "request. The corpus is far larger than the set of recorded answers, so most "
        "candidates have none; each of those is recorded as `NO_STORED_ANSWER` with the "
        "digest of the request it would have issued, and appears in the coverage table below. "
        "The table lane needs no provider and runs over the whole corpus. A bounded run that "
        "said so is the alternative to a run that looks complete because it only visited what "
        "was cheap.",
        "",
    ]
    lines.extend(_table(
        ("Bound", "Value"),
        [(key, report.bounds[key]) for key in sorted(report.bounds)]))
    lines.extend(["", "## Headline", ""])
    lines.extend(_table(
        ("", "Value"),
        [("run id", report.headline["run_id"]),
         ("corpus", report.headline["corpus_id"]),
         ("ontology definition hash", report.headline["ontology_definition_hash"]),
         ("all five self-verifications passed", report.headline["verification_passed"])]))
    lines.extend(["", "## Counts", ""])
    lines.extend(_table(
        ("Count", "Value"),
        [(key.replace("_", " "), report.counts[key]) for key in sorted(report.counts)]))
    lines.extend(["", "### The corpus consumed", ""])
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), report.corpus[key]) for key in sorted(report.corpus)]))
    lines.extend(["", "### The vocabulary", ""])
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), report.ontology[key])
         for key in sorted(report.ontology)]))
    lines.append("")
    return lines


def _coverage(report: RunReport) -> list[str]:
    lanes = sorted({lane for row in report.metric_coverage for lane in row["by_lane"]})
    lines = [
        "## Coverage — every in-scope metric, zeros included",
        "",
        "The in-scope set is computed from the ontology: a metric is in scope when the first "
        "entry of its `source_lane_preferences` is not the lane this corpus lacks. Nothing "
        "below is a hand-written list, so a metric added to the vocabulary appears here with "
        "no edit — and a metric that produced nothing is a row, not an absence.",
        "",
    ]
    lines.extend(_table(
        ("Metric", "In scope", "Observations", *lanes, "Documents"),
        [(row["metric_id"], row["in_scope"], row["observations"],
          *[row["by_lane"].get(lane, 0) for lane in lanes], row["documents"])
         for row in report.metric_coverage]))
    lines.extend([
        "",
        "### Deferred — the metrics whose first source lane this corpus does not hold",
        "",
        "Recorded rather than dropped. A silent skip and a stated deferral look identical in "
        "a catalog, and only one of them tells the next plan what to build.",
        "",
    ])
    lines.extend(_table(
        ("Metric", "Status", "Reason", "Required lane", "Observations emitted"),
        [(row["metric_id"], row["status"], row["reason"], row["required_lane"],
          row["observations"]) for row in report.deferred_metrics]))
    lines.append("")
    return lines


def _silences(report: RunReport) -> list[str]:
    lines = [
        "## Every candidate that produced no claim, grouped by reason",
        "",
        "`candidates` counts distinct (passage, lane) pairs carrying the code; `occurrences` "
        "counts the rows in `issues.jsonl`, which is higher wherever one passage produced the "
        "same refusal about several rows. The complete passage-by-passage record is "
        "`issues.jsonl`; every code with at most "
        f"{PASSAGE_LIST_CEILING} candidates is also listed in full below, and the larger ones "
        "are five figures of passage ids that belong in a file rather than in a document.",
        "",
    ]
    lines.extend(_table(
        ("Code", "Severities", "Candidates", "Occurrences", "Lanes"),
        [(row["code"], row["severities"], row["candidates"], row["occurrences"], row["lanes"])
         for row in report.no_claim_by_code]))
    if report.no_claim_passages:
        lines.extend(["", "### The short tails, in full", ""])
        for code in sorted(report.no_claim_passages):
            lines.append(f"**{code}**")
            lines.append("")
            lines.extend(f"- `{passage_id}`"
                         for passage_id in report.no_claim_passages[code])
            lines.append("")
    lines.extend([
        "### Passages the selector did not offer any lane",
        "",
        "Not candidates, and therefore not in the table above. Counted here so a passage's "
        "absence from extraction is as auditable as its presence.",
        "",
    ])
    lines.extend(_table(
        ("Lane", "Reason", "Passages"),
        [(row["lane"], row["reason"], row["passages"]) for row in report.unselected]))
    lines.append("")
    return lines


def _distribution(report: RunReport) -> list[str]:
    lines = ["## Counts by lane", ""]
    lines.extend(_table(
        ("Lane", "Candidates", "With a claim", "Claims", "Observations", "Events",
         "Relationships", "Issues", "Requests issued", "No stored answer"),
        [(row["lane"], row["candidates"], row["candidates_with_a_claim"], row["claims"],
          row["observations"], row["events"], row["relationships"], row["issues"],
          row["requests_issued"], row["requests_without_a_stored_answer"])
         for row in report.by_lane]))
    lines.extend(["", "## Counts by document type", ""])
    lines.extend(_table(
        ("Document type", "Candidates", "Claims", "Observations", "Events",
         "Relationships", "Issues"),
        [(row["document_type"], row["candidates"], row["claims"], row["observations"],
          row["events"], row["relationships"], row["issues"])
         for row in report.by_document_type]))
    lines.extend(["", "## Counts by claim kind", ""])
    lines.extend(_table(
        ("Claim kind", "Claims"),
        [(row["claim_kind"], row["claims"]) for row in report.by_claim_kind]))
    lines.extend(["", "## Payloads refused", "",
                  "Every row here is also in `rejected_claims.jsonl`, with the thing that was "
                  "refused. A rejection without the payload cannot be reviewed.", ""])
    lines.extend(_table(
        ("Refused by", "Code", "Rejections"),
        [(row["refused_by"], row["code"], row["rejections"]) for row in report.rejected]))
    lines.append("")
    return lines


def _verification(report: RunReport) -> list[str]:
    lines = [
        "## What the run verified about itself",
        "",
        "Five checks. `examined` is the denominator — a check with no failures over nothing "
        "examined is not the same answer as one over five thousand, and a table without the "
        "denominator would let an empty run look verified.",
        "",
    ]
    lines.extend(_table(
        ("Check", "Passed", "Examined", "Failures", "Warnings", "What it checks"),
        [(row["check"], row["passed"], row["examined"], row["failures"], row["warnings"],
          row["description"]) for row in report.verification]))
    detail = [row for row in report.verification
              if row["failures_by_code"] or row["warnings_by_code"]]
    if detail:
        lines.extend(["", "### Findings by code", ""])
        lines.extend(_table(
            ("Check", "Kind", "Code", "Count"),
            [(row["check"], kind, code, count)
             for row in detail
             for kind, census in (("failure", row["failures_by_code"]),
                                  ("warning", row["warnings_by_code"]))
             for code, count in census.items()]))
    lines.append("")
    return lines


def _temporal(report: RunReport) -> list[str]:
    lines = [
        "## Temporal residual — a review flag, not an error",
        "",
        "Every event whose `occurred_on` equals its `announced_on`. **Equal dates are not "
        "proof of a defect**: a change can be announced the day it takes effect. They are the "
        "shape the step 12 residual takes — a model answering both date questions with one "
        "printed phrase from a sentence that says only \"announced\" — so each is recorded as "
        "a diagnostic, nothing is refused, and no classifier is built. The phrase each field "
        "was read from and the sentence both came out of are below and in `events.jsonl`.",
        "",
    ]
    if report.temporal_residual:
        lines.extend(_table(
            ("Event", "Type", "Date", "Occurrence phrase", "Announcement phrase", "Passage"),
            [(row["event_id"], row["event_type_id"], row["occurred_on"],
              row["occurrence_date_text"], row["announcement_date_text"], row["passage_id"])
             for row in report.temporal_residual]))
        lines.extend(["", "The evidence each was read from:", ""])
        for row in report.temporal_residual:
            lines.append(f"- `{row['event_id']}` — {row['evidence_quoted_text']}")
    else:
        lines.append("No event in this run carries both dates equal.")
    lines.append("")
    return lines


def _scoping(report: RunReport) -> list[str]:
    lines = [
        "## Candidate scoping — what this run cost, and what it does not decide",
        "",
        "The strategy in force is configuration, not code. What a run can measure about it is "
        "corpus cost: how many concepts the scope offered each candidate, how many requests "
        "were issued, and how many had no recorded answer. **It cannot decide lexical versus "
        "hybrid**, and deliberately does not try: that comparison is made from the committed "
        "evaluation reports by reporting code outside this package, and no gold annotation "
        "reaches a runtime decision here.",
        "",
    ]
    lines.extend(_table(
        ("", "Value"),
        [(key.replace("_", " "), report.scoping[key]) for key in sorted(report.scoping)]))
    lines.append("")
    return lines


def _follow_up(report: RunReport) -> list[str]:
    lines = ["## Follow-up", ""]
    lines.extend(f"- {item}" for item in report.follow_up)
    lines.append("")
    return lines
