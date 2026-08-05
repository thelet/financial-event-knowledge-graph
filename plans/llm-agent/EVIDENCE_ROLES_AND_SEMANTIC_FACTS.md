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

**Moved again at S3a (2026-08-05), and this time with no version bump.** The committed D4
package is `pkg:…-opendoor-2022q3:6a858ae5c031`, digest
`a4cbd740b86f72dd31def43f3ba561762db0ee7a50366fd41ff4f645fdf8bfed`, and `PACKAGE_VERSION` is
**still `1.2.0`**: no field and no section changed, only which rows the collapse selects.
`package_id` moved anyway, because it digests the sorted fact and passage ids — so the store key
moved with it and the thirty strict xfails are still xfailing. Same one fixture.

**And again at S4 (2026-08-05), through the digest rather than the id.** The D4 package is still
`pkg:…-opendoor-2022q3:6a858ae5c031` — the ontology facts change no `fact_id` and no `passage_id`
— and its digest is now
`a661dc38b0f177a0a3bd0def7e06618b26e00d7ee6b2f5247d1113fc2ac78c75`. The store key moved for a
different reason: `PLANNER_PROMPT_VERSION` and `WRITER_PROMPT_VERSION` are both
`request_identity` inputs and both bumped. Same one fixture, still xfailing thirty.

**And again at S6a (2026-08-05), through the digest again.** `package_id` is still
`pkg:…-opendoor-2022q3:6a858ae5c031` — no fact and no passage id moved — and the digest is now
`5c420f8c50717b731656e1bec1c744b26f378669a9c00d25395f0ac4c51e6ad3`, because
`concordant_readings_collapsed`'s `kind` is a field on `PackagedWarning` and it changed from
`claim_qualifying` to `build_provenance`. Exactly two lines of the fixture moved. The store key
moved again anyway: `WRITER_PROMPT_VERSION` is `1.3.0`. Thirty strict xfails, still xfailing.

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

### S3a — One canonical fact, and the scope test that was measured away — **landed 2026-08-05**

S3 restored corroboration and **did not collapse**, so the package carried the same reading
twice. Measured on the inventory candidate, `facts[]` held `housing_inventory_homes 2022-12-31 =
12,788 homes` from `open-20221231.htm#p98` **and** from `open-20231231.htm#p105` — the 2023 10-K
restating the prior year. Same metric, period, value, unit, scale, row label, column label and
source lane. The cause is `_select_facts`'s round-robin: once every slot holds one reading the
deal comes back round and gives a slot a second one. Two identical `observed` rows let §12's
writer bind two sentences to one number as though it were two independent facts.

`_add_facts` now folds a reading into the first carried fact `observation_equivalence.
same_reading` calls equivalent. There is no new ordering: `plans` arrives sorted by
`section_bounds.fact_sort_key`, so the survivor is §6.1 step 5's representative and every later
reading becomes corroboration through the machinery S3 already built.

**The scope test the plan asked for was written, measured, and removed — the correction is the
finding.** §4 S3 says *"unless the observations genuinely differ in scope"*, and the obvious
reading of scope is `row_label` / `column_label`. A veto on those two fields was implemented
first. Against the 400 `(metric, period)` slots holding more than one reading of one value it
refuses **253, and is wrong on all 253**:

| slots | the "scope difference" | what it actually is |
| ---: | --- | --- |
| 211 | `'2022'` vs `'September 30, 2022'` | two spellings of the period E3 already compared. All 32 distinct column labels in the corpus are period headers |
| 25 | `direct selling costs(1)` vs `direct selling costs(4)` | a footnote marker moved |
| 17 | `contribution profit` vs `contribution profit (loss)` | a filing added `(loss)` when the value went negative |
| 1 | `Gross Margin` vs `Gross margin` | a capital letter, in one 10-Q, two tables apart |

A row label names the metric and a column label names the period, and **both are already decided
against structured fields** by E2 and E3 — so deferring to the printed header is letting
presentation overrule the graph, which is the one thing `observation_equivalence` says it never
does. `same_reading` is therefore the whole test. A cohort difference reaches it as a different
`metric_id`, because §6.9 R9 derives the cohort basis from the metric's own formula.

**Four consequences, each measured rather than predicted.**

1. **`corroborating_support` was a dead branch and now fires.** §1.1's own sentence — a passage
   that sources a concordant observation and was re-introduced as a contradiction — was
   implemented at S3 against `fact.corroborating_passage_ids`, which is `()` on every row until
   `derive_corroboration` runs at assembly. It reads `sections.corroboration` now, and both of
   the `contribution_margin` package's diagnostics take it.
2. **F3 no longer needs the token trim at all** (3,904 prompt tokens, `caps_hit` empty), and
   context passages are non-zero on all three spikes for the first time. F3's two facts are read
   from one table, so the package holds one primary passage and has room for both neighbours.
3. **The counter-evidence scan narrowed by one filing on two candidates**, because
   `counter.narrow` filters to the documents `facts[]` cite and a collapsed reading's filing is
   now a corroborating document rather than a cited one. Every row it removed is an
   `AMBIGUOUS_ALIAS` refusal that did not qualify before either. Whether the scan should follow
   corroborating documents as well is **S7's *"missed contradiction"* question**, left open here.
4. `concordant_readings_collapsed` is the disclosure — `ANNOTATE`, ~~`FACT_QUALITY_WARNING`, the
   mirror of `single_source`~~ — and the `facts` ledger row now reports `available` from
   `len(plans)` with the collapse named as its reason. `PACKAGE_VERSION` did **not** move: no
   field and no section changed, only which rows are selected.

   **Corrected at S6 (2026-08-05): the mirror argument was wrong, and it was a live refusal.**
   `FACT_QUALITY_WARNING` induces `CLAIM_QUALIFYING`, and a claim-qualifying code with no entry in
   `deterministic.REQUIRED_WARNING_QUALIFIERS` is answered by §13 with
   `required_warning_has_no_declared_qualifier` — a refusal — the moment a planner names it.
   Measured across **every candidate this graph run can package: 258 of 262 carry this code**,
   including the live D4 demo package, which is the one S7 re-records. `single_source` says *one
   filing saw this* and a reader needs it; this says *several filings agreed and the builder
   folded them into one row*, which is a statement about the assembly and is the opposite of a
   caveat. It is now `PACKAGE_COMPOSITION` → `BUILD_PROVENANCE`, a **sixth** category, because
   neither existing provenance category fits: nothing came out smaller than the corpus, so it is
   not a `RETRIEVAL_WARNING` (S3a's own argument, still correct), and V1 does this rather than
   failing to, so it is not a `CAPABILITY_LIMITATION`. `PACKAGE_VERSION` still does not move — no
   field and no section changed — but `PackagedWarning.kind` is on the wire, so the D4 digest
   moved to `5c420f8c5071…` with `package_id` unchanged at `…:6a858ae5c031`.

### S4 — Ontology as semantic facts — **landed 2026-08-05**

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

**What landed**: `story/stages/packaging/ontology_facts.py`, a pure function of the registry and
of the comparability answers `_add_compatibility` already computed. No retriever, no graph, no
clock. `PackagedMetric.description` is filled from the ontology, and the two prompts render three
new sections from **one** pair of functions.

**No `get_subject_context` tool was added, and the reason is that it would return nothing new.**
The ontology's `ConceptInstance` for `opendoor` declares the legal name, `cik`, `tickers` and
`exchange`; the `:Entity` node carries **byte-identical values** *(checked live 2026-08-05)*, so
reading the ontology is not weaker than the graph read `subject_identity_not_read_from_graph`
says is unavailable — and `:Entity` is off `cypher.py`'s allowlist anyway. A tool that duplicates
a declaration is a second authority, which C4 forbids.

**No company description exists, anywhere.** The `opendoor` instance carries three properties and
none is a description; the `:Entity` node carries twenty and none is a description; **no
`:Entity` node in the graph has a `description` property key at all**. So the row is emitted with
`available: false` and a statement that forbids supplying one, and both prompts render it as
`NOT AVAILABLE - …`. A model shown nothing supplies an answer from its weights; a model shown the
absence has been told not to.

**Citation handles are empty on every semantic fact, measured.** Two of 26 metrics carry
`source_evidence`, and both entries hold an accession, a form, a filing date and a quote with
**no `passage_id`, no `document_id` and no span**. `PassageCitation` requires all three, and
`EvidenceSourceCitation` would name an `:EvidenceSource` this corpus does not have. The filed
sentence travels in the fact's `statement` and its filing in `source`; the handle stays empty
rather than naming a location nothing can check.

**Coverage over all 26 metrics.** `label`, `description`, `unit`, `period_type`, `value_type` and
`gaap_status`: 26 of 26. `aliases`: 24, of which **ten are the label spelled again** and produce
no row — *"Gross Margin also appears in filings as 'Gross Margin'"* is 85 tokens of nothing. A
formula version: 8 metrics. `population` / `numerator_description` / `denominator_description`:
**one** — `pct_homes_on_market_gt_120_days`. `ambiguities`: 4. So *"scope rules where
source-backed"* produces **nothing** on any package built so far, and that is the finding.

**Token cost, measured live on `graph-v1-0483dc6b4b10`.**

| package | prompt slice | semantic | identity | comparability | S4 total | context lost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| F1 | 5,010 | 417 | 409 | 131 | **957** | 1 |
| F2 | 4,986 | 488 | 409 | 133 | **1,030** | 2 |
| F3 / D4 | 4,993 | 1,012 | 409 | 423 | **1,844** | 2 |
| contribution_margin | 5,137 | 609 | 409 | 136 | **1,154** | 0 (2 diagnostics) |
| inventory | 4,885 | 506 | 409 | 145 | **1,060** | 0 (3 diagnostics) |

**Two packages finish over §10.2's 5,000-token total with every trimmable class at its floor** —
F1 at 5,010 and `contribution_margin` at 5,137 — and that is the state §4 S5 designed for rather
than a failure to hide. A semantic fact is untrimmable, so the trim ran out of rows it was
allowed to take and stopped above the target; both are far under
`MAX_TOTAL_TOKENS_CEILING`, so **`required_fact_does_not_fit` does not fire on any live
candidate**. The refusal exists and this corpus does not reach it. What it cost is stated in the
last column: context passages are back to zero on all five, and the two `corroborating_support`
diagnostics S3a had just made real are trimmed away with their ledger row recording it.

**The prompt-slice estimate is a conservative proxy and the real request is a third of it.** The
planner prompt this produces measures **6,740–7,281 characters, ~1,685–1,820 tokens** against the
server's 8,192 context, up from ~1,137–1,416 before S4. The 5,000-token bound is over the
package's *canonical JSON* minus two sections, which is the largest slice a model could be shown
and not the slice it is shown.

`PLANNER_PROMPT_VERSION` moved to **1.1.0** and `WRITER_PROMPT_VERSION` to **1.2.0**, with one new
rule each (planner 8, writer 18) saying that these sections are definitions, carry no citable id,
and that a `NOT AVAILABLE` line is not the model's to fill in. `PACKAGE_VERSION` did **not** move:
S1 added the three sections and this fills them.

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

**And the classification the five categories left undecided — S6a, 2026-08-05.** S5's claim that
*"no code's kind moved"* was the right safety property and it hid a second one nobody had stated:
a `CLAIM_QUALIFYING` code with no declared prose qualifier is a **trap**, because §13 answers
`required_warning_has_no_declared_qualifier` — a refusal — for any code the planner names and the
qualifier table does not carry. Measured 2026-08-05: **four codes declared a qualifier, of which
one is a code a package can carry, against sixteen claim-qualifying codes**. Three of the four —
`filing_date_unknown`, `conflicting_values`, `warned_observation` — are not in
`warning_codes.SEVERITY_OF` at all, so `plan_violations` refuses them as `unknown_warning_code`
before §13 ever looks them up.

The rule is untouched. Three qualifiers were added and each is a repair rather than a policy:
`fact_conflict_disclosed` and `unpreferred_source_lane` are the codes `conflicting_values` and
`warned_observation` were renamed to, and take their existing phrases unchanged; `single_source`
is the one code whose meaning is wholly in the code (*"one document reports this slot"*) and it
now carries the whole source-count disclosure alone. The remaining twelve are named in
`deterministic.QUALIFIER_NOT_DECLARED` with a reason each — *container* (the meaning is in
`detail`: `metric_ambiguity_declared`, `candidate_warning`, `canonical_point_warning`,
`comparison_warned`), *refuses first* (`entity_unresolved`, `comparison_refused`), *never
observed* on any of the 262 packages, or, for `counter_evidence_same_passage`, that §11's
counterpoint obligation is already stronger than a phrase. `test_story_warning_taxonomy.py`
requires the two tables together to be total over `CLAIM_QUALIFYING`, so a seventeenth cannot
arrive latent.

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
