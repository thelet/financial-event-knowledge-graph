# Stage 6 — durable table-lane benchmark report

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 6.
**Goal:** persist the table-lane evaluation that currently exists only inside a test, as a
committed, byte-reproducible artifact.

**This stage changes no extraction behaviour.** If the report disagrees with the lane, the
report is wrong. Altering table extraction to improve a number here is out of scope and a
gate failure.

---

# 1. The architectural constraint that decides the layout

`tests/extraction/test_typed_selection.py::test_runtime_selection_never_imports_the_benchmark`
parses every module under `extraction/` and fails on any import or path literal mentioning
the benchmark. That test must keep passing.

So the runner — which loads gold YAML, drives the lane and writes the reports — **cannot live
under `extraction/`**. It lives with the benchmark it serves:

```text
benchmarks/
  __init__.py
  extraction/
    __init__.py
    v1/
      __init__.py
      runner.py          loads gold, drives the lane, builds the report model
      __main__.py        CLI
      reports/
        table_lane_v1.json
        table_lane_v1.md
      cases/             (existing, unchanged)
      README.md          (existing, gains a "Reports" section)
```

`extraction/stages/tables/evaluation.py` already takes gold as a parameter and stays as it
is. The runner composes it; the scoring logic is not duplicated.

# 2. Public interface

```python
# benchmarks/extraction/v1/runner.py
def load_table_cases() -> list[BenchmarkCase]: ...
def run_table_lane(cases, *, passages, ontology) -> TableLaneReport: ...
def render_json(report: TableLaneReport) -> str: ...
def render_markdown(report: TableLaneReport) -> str: ...
def write_reports(report: TableLaneReport, directory: Path) -> tuple[Path, Path]: ...
```

`TableLaneReport` is a plain dataclass. No pydantic requirement; JSON is rendered with
`json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False)` and a trailing newline.

# 3. Required JSON content

Deterministic ordering everywhere: cases by `case_id`, observations by
`(metric_id, period_key)`, issues by `(code, row_index, raw_label)`.

| Field | Source |
| --- | --- |
| `benchmark_version` | `"v1"` |
| `ontology_definition_hash` | `ontology.definition_hash` |
| `normalization_corpus` | document/passage/table counts + `documents.jsonl` sha256 |
| `implementation_commit` | `git rev-parse HEAD` at generation time |
| `lane` | name + version from `DeterministicTableClaimLane` |
| `cases[]` | all 11 case ids, passage id, table id, document type |
| `cases[].gold[]` | all 49, with metric, value, unit, scale, period, subject |
| `cases[].emitted[]` | all 410, with the same fields plus evidence and raw text |
| `cases[].matched[]` / `missed[]` / `unmatched_emitted[]` | keyed by `(metric, period)` |
| `cases[].scores` | **seven** dimensions — see the correction below |
| `cases[].issues[]` | code, row index, raw label, candidates, required lane |
| `totals` | the same scores aggregated, plus counts |
| `unsupported_table_shapes` | the four from §8a.7, as declared data |
| `known_misses` | the three `Homes sold in period` observations, with why |

**`implementation_commit` is the one volatile field.** It must be the only thing that changes
between runs at different commits, and the determinism check regenerates twice at the same
commit.

# 4. Required Markdown content

Readable without running Python. Must let a reader go from a number to the filing:

- header block: counts, all **seven** scores, generation identity;
- a totals table;
- per-case section: case id, passage id, table id, gold/emitted/matched counts, scores;
- a per-case table of **every gold observation** with expected vs emitted vs verdict;
- unmatched emitted observations, explicitly labelled *not errors* — gold is a deliberate
  subset (§4.0);
- issues grouped by code, with row label and row index;
- the three known misses with the §8a.8 explanation;
- the unsupported shapes from §8a.7.

# 5. Commands

Added to `benchmarks/extraction/v1/README.md`:

```bash
python -m benchmarks.extraction.v1 report            # regenerate both reports
python -m benchmarks.extraction.v1 evaluate          # print totals, write nothing
python -m benchmarks.extraction.v1 case <case_id>    # one case in detail
python -m benchmarks.extraction.v1 claims <case_id>  # every emitted observation for one case
```

# 6. Tests — `tests/extraction/test_table_lane_report.py`

1. Regenerating twice at one commit is **byte-identical** for both files.
2. The committed JSON matches a freshly generated one, ignoring `implementation_commit` —
   catches a stale committed report.
3. Totals in the JSON equal the numbers the existing evaluation produces.
4. Every `passage_id` in the report resolves in the corpus.
5. Every issue code is in `ISSUE_CODES`.
6. The three known misses are present and are the *only* misses.
7. Markdown mentions every case id and every issue code that occurs.
8. `benchmarks/` is importable and `extraction/` still imports nothing from it.

# 7. Acceptance gates

- [ ] Both reports exist, committed, and regenerate byte-identically.
- [ ] Recall 0.939, six dimensions 1.000, 11 cases / 49 gold / 410 emitted reproduced exactly.
- [ ] The benchmark-leakage test still passes.
- [ ] `pytest -m "not live"` green; count rises by the new tests only.
- [ ] No file under `extraction/` modified.

# 8. Non-goals

- Changing the table lane, the ontology, or any benchmark case.
- Improving recall.
- Narrative lane, provider, scoping, catalogs, run manifests.
- HTML, charts, or a diffing tool.

---

# 9. Corrections *(2026-08-02, found by review during step 11)*

Two, both about what the committed report contains rather than about how it was built. Neither
changes a number; both change what a number may be read as.

| § | What this brief said | What is true |
| --- | --- | --- |
| §3, §4 | `cases[].scores` and the header block carry "the eight dimensions" | **Seven.** They carry `metric_recall`, `matched_over_emitted` and the six per-match dimensions. `ambiguity`, which V1 §4.0 names among the eight, is scored by nothing here — no score in `table_lane_v1.json`, per case or in total, is named for it. The table cases *do* declare expected abstentions and the lane *does* record `AMBIGUOUS_ALIAS`, so it is measurable in this lane and was simply not measured. |
| §7 | "six dimensions 1.000" is an acceptance gate | One of the six cannot be anything else. `evaluation.evaluate_case` matches a claim to a gold claim on `(metric_id, period key)` and `compare` then tests `claim.period.key == gold.period_key` — true by construction — so `period_accuracy: 1.000` is a property of the matcher, not a measurement of the lane. The same holds for the metric half of a match. |

**Neither is repaired by changing this report's numbers**, and the reason is the one this brief
already gives for everything else in it: re-matching on metric alone to make `period_accuracy`
able to fall would change which pairs *every* dimension is computed over, in a committed
artifact, to move a number rather than to fix a lane. What is repaired is the claim: the report
now carries a `score_caveats` block naming both, its Markdown states both in prose, and
`tests/extraction/test_table_lane_report.py` fails if either caveat is dropped while the
condition it describes still holds.

The real period evidence in this report was always the paragraph under Totals: 21 emitted
observations on `kpi-table-q4-2023-earnings` carry the wrong period, sit outside the gold set,
and are scored by nothing — beside a `period_accuracy` of 1.000.
