# Evidence roles and semantic facts — implementation plan

Two defects with one shape: **the package tells the model the opposite of what the corpus says.**
Concordant sources are discarded; extraction diagnostics are promoted to contradictions; and the
ontology that defines every metric is carried as metadata rather than as fact.

**Written 2026-08-05** from worktree `FKG-story-agent-impl`, branch `impl/story-agent-v1`, base
`ce79f45`. Companion to [V1_STORY_AGENT.md](V1_STORY_AGENT.md) and
[INTERACTIVE_DEMO_UI.md](INTERACTIVE_DEMO_UI.md).

---

## 1. Root cause — measured, and not what the brief assumed

The brief says the package "can classify passages as counter-evidence even when they repeat the
same fact". The conclusion is right and **the mechanism is not**, which matters because fixing
the mechanism the brief describes would not fix the bug.

**`counter_evidence[]` never compares a value to anything.** `find_counter_evidence` is keyed on
`(metric_id, period_key)` and returns **`:Issue` nodes joined at document grain** — *"what this
filing would not let the run say about this metric"* (`counter_evidence.py:1-25`). A passage
becomes counter-evidence because an issue was recorded in the same document, never because of
what it says.

**Measured on the inventory candidate (2026-08-05, `graph-v1-0483dc6b4b10`).** The two passages
the planner was required to write counterpoints about carry **20 issues between them**:

| Code | Kind | Example detail |
| --- | --- | --- |
| `AMBIGUOUS_ALIAS` | extraction diagnostic | *"'Inventory (at period end)' resolves to 2 concepts; no claim emitted"* |
| `DEFERRED_REQUIRED_SOURCE_LANE` | capability limit | *"revenue requires a source lane this corpus does not contain"* |
| `DERIVED_COMPARISON` | extraction policy | *"The 53% decrease is a period-over-period change, not a reported level"* |
| `MISSING_PERIOD` | extraction diagnostic | *"This percentage appears in a table row without a specific period label"* |
| `UNIT_CONTRADICTS_ONTOLOGY` | ontology guard, **other concept** | *"housing_inventory_homes is reported in homes, not 'percent'"* |

**Not one of them challenges "inventory fell from 12,788 homes to 6,261 homes."** Every one is a
diagnostic about a *different* concept that happens to sit in the same filing.

### 1.1 The other half, and it is worse

The same query run over the observations:

```
housing_inventory_homes 2023-03-31 → 6 observations, all 6261.0 homes, 6 different documents
housing_inventory_homes 2022-12-31 → 6 observations, all 12788.0 homes, 6 different documents
```

**Twelve concordant sources, zero disagreement.** Canonicalisation collapses each slot to one
fact and the package carries **one** passage; the other five are discarded. One of the discarded
five is `q12023formxex992sharehol.htm#p8` — the shareholder letter — which the package then
re-introduces **as counter-evidence**, because it carries an `AMBIGUOUS_ALIAS` about the word
"inventory".

So a single passage is simultaneously the source of a corroborating observation and a declared
contradiction. That is the defect in one line.

**Net effect: the best-corroborated fact in the candidate is presented as thinly sourced and
heavily contradicted.** The planner behaved reasonably and §11 refused it correctly. The rule is
right; its input was wrong.

---

## 2. What is already true (do not rebuild it)

| Claim | Status |
| --- | --- |
| Planner receives metric semantics | **Partly.** `_metric_lines` already sends `metric_id`, `label`, `unit`, `period_type`, `population`, `distinct_from`, `ambiguities`. Missing: plain-language definition, scale, formula version + window, aliases |
| `PackagedMetric` carries a definition | **No** — it has no `description` field. The `:Metric` node does (22 properties incl. `description`), and the ontology is authoritative (C4) |
| Passages carry a role | **No.** `counter_evidence[]` is `tuple[PackagedPassage, ...]` and `PackagedPassage` has no role field. The basis travels as one of two `ANNOTATE` warnings, read back by `match_basis_of` — recorded as a known finding, not an oversight |
| Issue severity is available | **Yes** — `COUNTER_EVIDENCE` already returns a `severity_rank` (`rejection` 0, `refusal` 1, `diagnostic` 2) and sorts by it |
| A role enum exists | **No.** Reuse `Severity`, `WarningKind`, `RetrievalOutcome`. ~~Add exactly two new closed vocabularies~~ — **corrected at S1: four were added, not two.** `EvidenceRole` and `FactKind` as planned, plus `PassageQuality` and `PassageUnusableReason`, because `UnusableReason` could not be reused. See §4 S1 |
| `counter_evidence_unaccounted` | **Keep unchanged.** It is correct and this plan does not touch it |

---

## 3. The consequence that governs sequencing

**Every change in §4 alters `package_content_digest`, which is an input to `story_run_id`, which
is the key the recorded generation store is indexed by.** The committed replay demo
(`story-v1-552ffb8abf3e`, package `b01a8d573f08…`) **will stop replaying** the moment the package
schema moves.

**Measured at S1 (2026-08-05), and the mechanism is one step longer than this section assumed.**
The package rebuilt from `graph-v1-0483dc6b4b10` is now
`pkg:…-opendoor-2022q3:8e0cb0cf6655`, digest
`354f3d5944bd7b5851ba391aa6011ac9e13a5f583cc1c7d3711c8e8a3bb24913`, up from `a12e47402914` and
`b01a8d573f08…`. The extra step is what produces the `MissingGenerationError` this section
predicts: adding three sections and re-shaping four row types is exactly the event
`PACKAGE_VERSION` exists for, so it moved to `1.1.0`; that moves `package_id`; and `package_id`
is rendered into both the planner and the writer prompt, which is what the generation store is
keyed on. **A digest change alone would not have missed the store** — it would have fired
§13.13's `package_content_digest_mismatch` instead, which is a rejection rather than a miss.
Thirty tests are `xfail(strict=True)` naming S7.

This is not a reason to avoid the change; it is a reason to sequence it. The re-record is one
step (S7), it needs the live model server, and it must happen **once**, after every schema change
has landed — not per stage. Until S7, the replay demo is expected to fail with
`MissingGenerationError`, and that is a known intermediate state rather than a regression.

---

## 4. Stages

Ordered by dependency. S1 is a barrier; S2–S5 may run in parallel after it.

### S1 — One schema change, all at once — **landed 2026-08-05**

Because §3 makes each schema change expensive, they land together.

**New closed vocabularies** in `story/core/models.py`, following the existing `str, Enum` pattern:

```
EvidenceRole            primary_support | corroborating_support | context |
                        counter_evidence | warning_only | unusable
FactKind                observed | derived | semantic | identity | comparability
PassageQuality          unassessed | usable | unusable
PassageUnusableReason   empty_or_structural_only | no_relevant_proposition |
                        corrupted_extraction | insufficient_content
```

**Four, not the two §2 planned, and the count is corrected rather than quietly exceeded.**
`PassageUnusableReason` could not be four more members on `UnusableReason`: that enum's five
members are editorial dispositions the *planner model* declares about an item it chose not to use
(§11 point 2), and they reach the model as an `enum` in §15.3's portable schema. The four above
are decided by code before any model runs. Widening the existing enum would have offered the
planner `corrupted_extraction` as a judgement it has no way to establish, **and** would have
changed the grammar the server is constrained by — a change to what the model may *assert*, not
to what the packager may record. `PassageQuality` is then the type `quality_status` needs, and
its three members exist so *"nobody has looked"* and *"looked and it is fine"* stay different
facts. `tests/story/test_story_evidence_roles.py` asserts the planner schema did not widen.

**Field additions.** Every one is inert at S1; the stage that fills it is named against it.

| field | default | filled by |
| --- | --- | --- |
| `PackagedPassage.role: EvidenceRole` | **required, no default** | the section it is built from, today |
| `PackagedPassage.match_basis: str` | `""` | S2 adds the qualifying bases |
| `PackagedPassage.quality_status` | `UNASSESSED` | S2 |
| `PackagedPassage.unusable_reason` | `None` | S2 |
| `PackagedFact.fact_kind` | `OBSERVED` | — |
| `PackagedFact.corroborating_{observation,passage,document}_ids` | `()`, sorted-unique | S3 |
| `PackagedMetric.description: str \| None` | `None` | S4, **from the ontology** (C4) |
| `semantic_facts` / `identity_facts` / `comparability_facts` | `()` | S4 |

`role` has **no default** because every candidate default is a claim: `primary_support` would
have an unmigrated call site assert that an arbitrary passage backs a fact, and `unusable` that
it backs nothing. Seventeen call sites had to state one; each knew the answer. Counter-evidence
rows are labelled `COUNTER_EVIDENCE` — *what is true today*, not the `WARNING_ONLY` §1 shows most
of them deserve, because relabelling them here would be S1 performing S2's reclassification with
none of S2's evidence.

`SemanticFact`, `IdentityFact` and `ComparabilityFact` each carry **required** `authoritative` and
`editable` flags — the ontology is authoritative and subject identity is not
(`subject_identity_not_read_from_graph`), so a default would answer for S4 — plus
`citations: tuple[CitationHandle, ...]`, ordinarily empty, because a definition the ontology
asserts is not a sentence in a 10-Q. `ComparabilityFact` is the ontology's **rule**, deliberately
not a second `CompatibilityDecision`: that type is §6.9's answer about one *pair of slots*.
`IdentityFact.available` carries S4's *"mark the description unavailable"*.

`match_basis` is now a real field rather than a warning read back by `match_basis_of` — that
workaround existed only because S5 did not own `models.py`, and this stage does. The two
`ANNOTATE` disclosures stay, because §13.17's evidence panel renders them and no consumer of them
changed; both they and the field are written from one value at one call site, and
`CounterEvidenceRow` now refuses a row where the two disagree.

**Deliberately not done here.** No `BudgetParameters` cap was added for the three new sections:
`digest_parts()` feeds `package_id`, which is in the prompt, and the trimming priority is S5's.
`section_counts` and `prompt_slice` cover them from day one, because a section the digest does
not cover is a section two runs can differ on silently.

### S2 — Counter-evidence qualification

A passage may be `counter_evidence` **only** on deterministic evidence that it challenges *this
story*. Same-document proximity is not enough; a shared keyword is not enough; a repeated value
is never counter-evidence.

Qualifying bases (each a named `match_basis`):

- same metric and period, value outside presentation tolerance
- same metric, materially different period or scope, undermining the stated comparison
- explicit textual qualification or limitation relevant to the thesis
- opposite direction under a comparable definition
- an issue code that changes how *this fact* may be read
- conflicting subject, cohort, unit, formula version or period semantics

Everything else that is currently counter-evidence becomes `warning_only` (an extraction or
data-quality diagnostic) or `unusable`. **`AMBIGUOUS_ALIAS` on a passage a used fact is bound to
is a fact-quality warning, not a counterpoint** — unless it proves the bound fact's metric
identity, value, scope or period is actually incompatible.

**Comparison is on canonical values, never raw strings.** Normalise and compare value, sign,
unit, scale, metric identity, subject, period (or equivalent period), formula version, and the
accepted presentation tolerance. `6,261` / `6261` / `6.261 thousand` are equivalent;
`3.3%` / `3.30 percent` are equivalent; **`15.9%` and `15.9 percentage points` are not, and must
never merge** — §13.3 exists for exactly that confusion.

### S3 — Corroboration preservation

The five discarded sources come back. For a canonical fact with multiple concordant
observations: keep **one canonical fact**, designate one source `primary_support` by the existing
deterministic source-lane priority, and carry the rest as `corroborating_support` with their
observation, passage and document ids. **Do not mint duplicate facts** unless the observations
genuinely differ in scope.

### S4 — Ontology as semantic facts

Populate `semantic`, `identity` and `comparability` facts from the ontology — authoritative,
`editable: false` — and render them in the planner and writer prompts as *facts*, not metadata.

Per metric: display name, aliases, plain-language definition, unit, scale, instant-vs-duration
semantics, formula version and effective window, scope rules where source-backed, citation
handles. Per subject: legal name, ticker, entity type, source-backed description **where one
exists**.

**No company description may be invented from model knowledge.** If no source-backed description
exists, carry structured identity only and mark the description unavailable. A bounded
`get_subject_context` tool may be added only with an explicit `LIMIT`, explicit timeout, exact
entity id, read-only Cypher and no variable-length path — and **no outward traversal from the
2,704-edge Opendoor hub**. Broad relationship discovery is out of scope.

### S5 — Warning taxonomy, trimming priority, and one new refusal

Split the warning vocabulary so a diagnostic cannot read as a contradiction:
`substantive_counter_evidence`, `fact_quality_warning`, `extraction_issue`, `retrieval_warning`,
`capability_limitation`.

Reclassify: `subject_identity_not_read_from_graph`, `evidence_sources_absent_in_v1` and
`relationships_unavailable_in_v1` are **capability limitations** and must not reduce the
verification status of a post.

**Trimming priority.** Protected and untrimmable: candidate-anchor observed facts, required
derived facts, metric semantic facts, subject identity facts, comparability facts, primary
supporting passages. Trimmable, in order: explanatory passages, optional context, extra
corroborating passages, lower-severity diagnostics, non-required relationships. Every section
records available / carried / dropped / reason.

**A required semantic or anchor fact that will not fit is a package refusal, not a warning.**
This is the one new blocking behaviour in the plan; it is the only honest answer, since a post
written without its metric's definition is a post whose numbers have no declared meaning.

### S6 — Canonical role serialisation and UI

**One canonical mapper, serialised identically everywhere.** The same evidence item must carry
the same role in `POST /demo/evidence-package`, `GET /demo/runs/{id}/sources`, the story-
suggestion payloads, the stored artifacts and the UI DTO. Per-serialiser patching is forbidden —
it is how the current inconsistency arose (`sources` populates `role`, the package endpoint
returns `role: None`). **`role: None` must be unreachable.**

UI: a **Facts sent to the model** section in the selected-story panel, divided into observed,
derived, semantic, company context and comparison rules. Every row shows kind, statement, source,
authority, editability, warnings and whether it reached the model-visible slice. Semantic and
identity facts render read-only; prompt instructions stay editable but ontology facts are not
reachable from the prompt UI.

Warnings render with their real content: `retrieval_truncated` shows returned / known-available /
ordering / lowest retained severity and states the result is not exhaustive; `token_budget_trimmed`
shows before, after, sections trimmed, items dropped and that no required fact was dropped;
`section_truncated` shows *"4 of 9 primary passages sent to the model"* with a control to inspect
all available items.

### S7 — Regression, re-record, adversarial review

Re-run the inventory candidate end to end; re-record the generation store once (§3); confirm the
D4 demo still accepts; then an adversarial review aimed specifically at **false corroboration**
(two things merged that are not the same fact) and **missed contradiction** (something real
downgraded to a warning). Both are the failure modes this plan creates.

---

## 5. Tests

Beyond the per-stage suites: same-value corroboration across two documents, with comma, scale and
percentage-format variants, both sources preserved, one primary and one corroborating, neither
counter-evidence. Non-equivalence: percent vs percentage points, different units, subjects,
periods, incompatible formula versions, out-of-tolerance values, the same number on a different
metric — none may merge. Genuine counter-evidence still qualifies and still triggers planner
accounting. Empty passages (pipe-only tables, whitespace, separators, boilerplate) never become
counter-evidence and carry a typed reason. Role identity across all four surfaces. **And a
fixture with real unaccounted counter-evidence that still fires `counter_evidence_unaccounted`** —
the guard on this entire change.

Tests must inspect the **serialised provider request**, not only the package object: §4's claim is
that the model *receives* semantic facts, and only the wire proves it.

## 6. Out of scope

Broad relationship discovery. New unbounded traversals. Weakening `counter_evidence_unaccounted`,
truncation disclosure, or any §13 check. Model-assisted verification.
