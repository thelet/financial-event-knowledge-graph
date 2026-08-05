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
| `UNIT_CONTRADICTS_ONTOLOGY` | ontology guard, ~~other concept~~ — **this concept** | *"housing_inventory_homes is reported in homes, not 'percent'"* |

**Not one of them challenges "inventory fell from 12,788 homes to 6,261 homes."** Every one is a
diagnostic about a claim the run **refused to emit**, recorded in a filing the package cites.

**Corrected at S2 (2026-08-05).** The last row is not about a different concept: it carries
`concept_ids = ['housing_inventory_homes']`, so it is about *this* metric. It still does not
qualify, and the reason is better than the one this table gave — an `:Issue` records what the
graph does **not** contain. This one says a `percent` reading was rejected because the ontology
declares `homes`, and the packaged fact is in `homes`: the guard agrees with the fact it was
filed against. `counter_evidence.classify_issue` therefore compares the *packaged fact's* unit
with the ontology's declared unit rather than parsing the issue's prose, and the same code
**does** qualify when the carried fact is in a unit the ontology does not declare.

### 1.1 The other half, and it is worse

The same query run over the observations:

```
housing_inventory_homes 2023-03-31 → 6 observations, all 6261.0 homes, 6 different documents
housing_inventory_homes 2022-12-31 → 6 observations, all 12788.0 homes, 5 different documents
```

*(2022-12-31's document count corrected from six to **five** at S3, re-measured 2026-08-05: one
filing contributes two of its six rows. The 2023-03-31 slot is 6 and 6 as stated.)*

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

**Moved again at S2/S3 (2026-08-05).** The committed D4 package is now
`pkg:…-opendoor-2022q3:91fd3fe66619`, digest
`cca51303822e67c06df51825bc10d210c6d9ff4f657814ac94d17818217498ed`, and `PACKAGE_VERSION` is
`1.2.0` — one more section (`diagnostic_passages`) and one more field
(`PackagedPassage.diagnostic_codes`). `tests/story/fixtures/story_demo/evidence_package.json` was
rebuilt live against `graph-v1-0483dc6b4b10` and is the only fixture that moved. The thirty
strict xfails are **still xfailing**, which is what says the store key moved the way S1 measured
rather than some other way.

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

### S2 — Counter-evidence qualification — **landed 2026-08-05**

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

**What landed, and the four places it is narrower or wider than the words above.**

The six bases are named constants in `counter_evidence.py` beside the two that were already
there, and the two vocabularies now mean different things: `same_passage` / `same_document` are
the **grain** — where a row was found — and the six are the **basis** — what qualified it.
`CounterEvidenceRow` refuses a row that calls itself counter-evidence on a bare grain, so §1's
defect is unconstructible rather than merely unproduced.

1. **Value-based bases come from the observations, not from an `:Issue`.** An issue is a record
   of a claim the run **refused to emit**, so it carries no value that could disagree with one.
   `value_outside_tolerance` and `opposite_direction` are produced by putting the other
   observations of a packaged slot through `observation_equivalence.same_reading` — the same
   function S3 uses, read the other way. Measured: **zero rows** on this run, because all 36
   multi-valued slots collapse to one cluster under presentation tolerance (§6.1 step 2). That is
   the corpus's answer, not a missing feature, and the tests exercise it synthetically.
2. **An issue qualifies only at passage grain**, and only on a code that impugns the citation
   chain of the very cell a fact was read from — the quoted span is absent, the value disagrees
   with its quote, the period is not grounded, the passage defines rather than reports, the
   subject is another company, or the fact's own unit is one the ontology does not declare.
   A refusal recorded in a neighbouring table cannot contradict the cited cell, because it is
   not about that cell and the graph holds no observation from it.
3. **`AMBIGUOUS_ALIAS`'s exception is unreachable and the branch was not written.** `narrow` has
   already dropped every row whose `concept_ids` share nothing with the candidate's metrics, so
   every surviving row names this metric among its candidates — which is a declared ambiguity,
   not a proof of incompatibility. A branch nobody can trigger is a check that is claimed and
   not made.
4. **The demoted rows needed somewhere to go, so §10 gained a twentieth section.**
   `diagnostic_passages[]` holds every row `find_counter_evidence` produced that did not qualify,
   each carrying the role it actually plays. Leaving them in `counter_evidence[]` would have kept
   §11's planner obliged to write a counterpoint about an `AMBIGUOUS_ALIAS` refusal, which is the
   defect; dropping them would have made the reclassification unreviewable. A row whose passage
   the package **already carries** is not duplicated — its issue codes are stamped onto the row
   that is there, through the new `PackagedPassage.diagnostic_codes`. On the inventory candidate
   both of §1's two passages take that branch, so the reclassification costs the package nothing.
   `PACKAGE_VERSION` moved to `1.2.0`; S7's single re-record covers both bumps.

**No warning is emitted for a demoted row**, and that is deliberate: both `counter_evidence_*`
codes are `CLAIM_QUALIFYING`, and `deterministic.REQUIRED_WARNING_QUALIFIERS` turns
`counter_evidence_same_document` into a sentence the post must write. Demanding *"as reported
elsewhere in the filing"* about an extraction diagnostic is §1's defect one layer down. The
diagnostic travels on the row — role, `match_basis`, `diagnostic_codes` — and in
`budget.section_ledger`, which records what the section had and what it carried.

**The quality filter is `passage_quality.assess`, and its floor is a measurement.** 20 content
characters and 3 word tokens, against a corpus whose smallest observation-evidencing passage
carries **82 characters and 12 word tokens** (n = 150). It refuses 417 of 8,776 passages — 39
structural, 78 short, 300 filing boilerplate — and **none of the 150 that evidence a fact**.
Every packaged passage is now assessed, not only the counter-evidence candidates.

### S3 — Corroboration preservation — **landed 2026-08-05**

The five discarded sources come back. For a canonical fact with multiple concordant
observations: keep **one canonical fact**, designate one source `primary_support` by the existing
deterministic source-lane priority, and carry the rest as `corroborating_support` with their
observation, passage and document ids. **Do not mint duplicate facts** unless the observations
genuinely differ in scope.

**The primary source is the one §6.1 step 5 already chose**, reached through
`section_bounds.fact_sort_key`'s `role_rank`: representative before supporting before minority,
and the representative is `min(scale_precision, filing_date, observation_id)`. No second lane
priority was written, because a second one is a second authority.

**Corroboration is `derive_corroboration`, and it is derived on every rebuild like
`documents[]`.** The selection stage records every concordant source of every carried
observation; assembly subtracts whatever `facts[]` is still carrying and turns the rest into the
three id lists. Both halves of that were measured wrong first and are recorded rather than
quietly fixed:

* stamped **once, before the cap**, every corroboration list on the three §6.3 spikes came out
  empty — at that point `facts[]` still held every reading the cap would later drop;
* stamped with **no subtraction at all**, the id lists cost 487–876 prompt tokens per package and
  pushed F1 and F3 from three primary passages to two — paying a whole 780-token table of filed
  text to repeat, as an id, a reading the package was already carrying in full.

**Measured after both fixes (2026-08-05).** The inventory candidate carries 3 facts and 3
primaries where it carried 2 and 2, its anchors name **5 corroborating observations across 5
documents** and **4 across 3**, and its `counter_evidence[]` is empty. F1 and F3 still give up one
primary passage each — the row they lose is a *concordant second reading of a slot they already
carry*, and what replaces it is the complete source list for every fact they keep.

**`corroborating_support` as a passage role is used in one place**: a demoted counter-evidence row
whose passage is also a source of a concordant observation. That is §1.1's sentence exactly — the
shareholder letter is a corroborating source the package re-introduced as a contradiction.
Corroborating passages are otherwise carried by id and not as passage rows, which is what §10's
own note says (*"three id lists on the fact rather than five more rows in `facts[]`"*) and what
the token budget allows: a second copy of a number the package already states is the least
defensible row it could ship. `TRIM_PLAN`'s `corroborating_passages` step is therefore inert
today and is correct for the day S6 renders them.

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

### S5 — Warning taxonomy, trimming priority, and one new refusal — **landed 2026-08-05**

Split the warning vocabulary so a diagnostic cannot read as a contradiction:
`substantive_counter_evidence`, `fact_quality_warning`, `extraction_issue`, `retrieval_warning`,
`capability_limitation`.

**A refinement of `WarningKind`, not a third axis, and the choice is load-bearing.** `KIND_OF` is
now *derived* from `CATEGORY_OF` through `KIND_OF_CATEGORY`, which pins each category to one
kind. Two independent tables over one code set can disagree silently — a code filed as
`capability_limitation` and `CLAIM_QUALIFYING` would demand a sentence about a tool V1 does not
have — and one table that induces the other cannot. **No code's kind moved**: the same sixteen
qualify a claim and the same thirteen record the build, asserted code by code in
`tests/story/test_story_warning_taxonomy.py`, so the planner's `claim_qualifying_warnings` filter
and §13's disclosure check behave exactly as they did and their tests are untouched.

**The category is a table lookup, not a field on `PackagedWarning`, and that is a measurement.**
`kind` is on the row because the *planner* reads it and a stage may not import another stage.
Nothing has that relationship to the category — the code catalogue and the evidence panel both
import `warning_codes` already. Carrying it anyway cost **170 prompt tokens on a full twenty-row
`warnings[]`** *(measured 2026-08-05)*, inside the slice §10.2's budget binds, where §10.2.1's
median passage is 536 tokens. A label the UI can look up is not worth a third of a passage.

Reclassify: `subject_identity_not_read_from_graph`, `evidence_sources_absent_in_v1` and
`relationships_unavailable_in_v1` are **capability limitations** and must not reduce the
verification status of a post. All three were already `BUILD_PROVENANCE`, so the reclassification
is *within* provenance and changes no obligation: each fires on **every** package — `:Entity` is
off `cypher.py`'s allowlist, no §9 tool returns a relationship, the corpus holds **zero**
`:EvidenceSource` nodes — and a permanent structural absence read as a caveat about *this*
evidence is a reader told to distrust evidence that is fine. Relabelled, never hidden: still
constructible, still carried, still severity-ordered, still described in the catalogue.

**Trimming priority.** Protected and untrimmable: candidate-anchor observed facts, required
derived facts, metric semantic facts, subject identity facts, comparability facts, primary
supporting passages. Trimmable, in order: explanatory passages, optional context, extra
corroborating passages, lower-severity diagnostics, non-required relationships. Every section
records available / carried / dropped / reason.

**Implemented as `TRIM_PLAN`, a list of row *classes*, with `TRIM_ORDER` derived from it.** Two
of the five trimmable classes are rows *inside* a section — corroborating passages inside
`primary_passages`, and (until S2 gave them a section) diagnostics inside `counter_evidence` — so
a list of section names could not express the order. `TrimStep` carries a role filter; the
section list is a projection of the plan and never the other way round.

Three points where the implementation is narrower than the words above, each deliberate:

* **"Primary supporting passages" is enforced at row grain**, by the existing
  `may_drop_primary` floor: a primary backing a slot the candidate anchored on is untouchable, a
  primary backing nothing required is trimmable last. Making the whole section untrimmable would
  have stopped `token_budget_trimmed` firing in cases where it fires today, which is exactly the
  weakening this plan forbids.
* **"Required derived facts" needs no second marker.** A derived fact on a required slot is
  required by the slot rule; one on any other slot is not required at all.
* **"Non-required relationships" is vacuous in V1.** `relationships[]` is empty on every package
  (`relationships_unavailable_in_v1`), so no step was written for a section `PackageSections`
  does not have.

**A required semantic or anchor fact that will not fit is a package refusal, not a warning.**
This is the one new blocking behaviour in the plan; it is the only honest answer, since a post
written without its metric's definition is a post whose numbers have no declared meaning.

Landed as `required_fact_does_not_fit`, **`REFUSE`**, raised through `packaged_warning` beside
`package_exceeds_token_ceiling` rather than through a second refusal channel — `blocking()`
already collects it and §13.17's gate already acts on it. It fires only from the irreducible
state (over §10.2's 6,000-token ceiling with every trimmable class at its floor) and only when
protected facts are present, and its `subject_ids` name them, so the rejection is dispatchable.

`budget.section_ledger` is the available / carried / dropped / reason record, one row per
section, plus `protected` and `required_dropped` — the latter **measured** from the surviving
facts, so §4 S6's *"no required fact was dropped"* is a reading rather than a claim. `available`
is `None` where an earlier §10.2 cap dropped rows and reported no count: *"4 of 4 available"*
about a section that had nine is a false statement. `package_assembly.note_available` is the hook
that makes it exact, and the **selection stage is the only place that can call it** — three call
sites in `evidence_package.py` (`len(plans)`, `len(order)`, `len(built)`), left for S4/S6 because
that file was being edited concurrently by S2/S3.

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
