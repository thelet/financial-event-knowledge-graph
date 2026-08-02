# Stage 8 — lexical ontology candidate scoping

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 8, §4.2a.
**Goal:** decide which concepts the narrative lane may consider for a passage, lexically, and
make every inclusion auditable. Offline. No provider.

---

# 1. Scope

```text
extraction/core/concept_resolution.py   NEW — the shared resolver (see §2)
extraction/stages/scoping/
  __init__.py
  public.py                             ScopedConcept, CandidateScope result, reason codes
  lexical.py                            LexicalOntologyCandidateScope
```

Satisfies `extraction.contracts.OntologyCandidateScope`. Its `candidates_for(text)` returns
concept ids; a richer `scope_for(text)` returns the reasoned result the report needs.

# 2. The shared-resolver requirement

Canonical-label precedence currently exists **twice and differently**:

- `AliasIndex.canonical_labels` holds the map;
- `deterministic_lane._resolve_label` implements the precedence (whole label → canonical
  label wins over a same-spelled ambiguous alias → longest ambiguous → nothing);
- `typed_selector._partition` implements something else again for selection.

A third copy in the scope would be the same defect that produced a report scoring itself
twice. **Extract the precedence into `extraction/core/concept_resolution.py`** as one public
function, and have the table lane call it. Behaviour must not change — the table-lane
benchmark must still read 11 cases / 49 gold / 410 emitted / recall 0.939 / six dimensions
1.000, and `benchmarks/extraction/v1/reports/table_lane_v1.json` must regenerate identically
apart from `implementation_commit`.

Typed selection keeps its own coarser signal (it answers "is this passage worth reading",
not "which concept is this row"), but must not contradict the resolver: any concept the
resolver returns for a row label must be reachable in that passage's selection candidates.

# 3. What the scope must preserve

Every one of these is included and carries a reason; none may be dropped by any later
ranking (stage 9 may only *add*):

| Reason code | Meaning |
| --- | --- |
| `exact_alias` | an ontology alias matched whole |
| `normalized_alias` | matched after the typography fold (curly quotes, dashes, NBSP) |
| `canonical_label` | the concept's own label matched whole |
| `ambiguous_alias` | a declared-ambiguous surface matched — **all** its concepts included |
| `stable_core` | always present: `opendoor`, `company`, `public_company` |
| `known_instance` | a `ConceptInstance` from the ontology matched |
| `table_label` | matched in a Markdown row label |
| `confusion_sibling` | a `distinct_from` sibling of an included metric |

`confusion_sibling` is the one that looks optional and is not: a passage naming `homes_sold`
must let the lane see `homes_purchased`, or the lane cannot tell that the sentence in front
of it is the other one, and the declaration meant to prevent the merge never gets a chance.

# 4. Runtime must not see gold

No module under `extraction/` may import or name the benchmark. The existing test enforces
it; the new report generator lives under `benchmarks/` like the stage-6 one.

# 5. Evaluation

Over all benchmark cases with a `passage_id` (all 26 — a scope is defined for any passage):

- required-concept recall — gold claim metrics present in the scope;
- **critical-concept recall** — the confusable pairs, scored separately because an aggregate
  hides them: `homes_sold`/`homes_purchased`, `acquisition_contracts`/`homes_under_contract`,
  `gaap_gross_profit`/`adjusted_gross_profit`, `gaap_gross_margin`/`adjusted_gross_margin`,
  `contribution_profit`/`contribution_margin`;
- known-instance recall;
- ambiguity preservation — for each case whose gold expects an `AMBIGUOUS_ALIAS` abstention,
  are **all** candidates of that surface in scope;
- candidate count by reason code;
- scope size: mean, median, max;
- misses, each with a stated cause.

Named probes that must appear in the report by name: `Homes sold in period`, and bare prose
mentions of `Gross Margin` and `Gross profit`.

# 6. Durable report

`benchmarks/extraction/v1/reports/lexical_scope_v1.{json,md}`, byte-identical on
regeneration, `implementation_commit` the only varying field — same rules as stage 6.

Per case: passage id, scope size, every included concept with its reason(s), protected
candidates, confusion-group expansions, gold metrics, misses.

Commands, added to the existing CLI:

```bash
python -m benchmarks.extraction.v1 scope-report          # regenerate
python -m benchmarks.extraction.v1 scope <case_id>       # one case
python -m benchmarks.extraction.v1 scope-diff <case_id>  # expected vs included
```

# 7. Tests — `tests/extraction/test_lexical_scoping.py`

1. Each of the eight reason codes is produced by a real corpus passage.
2. A declared-ambiguous surface contributes **all** its concepts, never one.
3. `homes_sold` in a passage pulls `homes_purchased` in as a sibling.
4. Stable core concepts are present for a passage that mentions no metric.
5. The typography fold works: a curly-quoted 120-day label scopes the metric.
6. `scope_for` is deterministic — same text, same order, twice.
7. The scope satisfies `OntologyCandidateScope` driven through the protocol.
8. Nothing under `extraction/` imports the benchmark.
9. **The table lane is unchanged by the resolver extraction** — assert the same values on
   two known claims.
10. Report determinism, and committed-report-matches-fresh, as in stage 6.

# 8. Acceptance

- [x] Critical-concept recall **1.000** and ambiguity preservation **1.000**. These are
      genuine gates: a confusable pair or an ambiguous surface that lexical matching drops is
      a defect, because the surface is *there* to be matched.
- [x] Required-concept recall **0.959 (47/49)** as measured 2026-08-01 — *gate corrected
      after measurement*. **Now 0.960 (48/50)** *(verified 2026-08-02)*: the founder
      correction to `population-portfolio-mdna-fy2023-10k` added one required concept, and
      the lexical scope already reached it via `normalized_alias`, so numerator and
      denominator both rose by one. **The two misses below are unchanged** — the correction
      moved the denominator, not the shortfall, and the gate still fails.

  I set 1.000 before measuring, on the assumption that whole-phrase alias matching could
  reach every gold metric. It cannot, and the two misses show why. Both are
  `pct_homes_on_market_gt_120_days` in prose:

  | Passage says | Nearest declared surface |
  | --- | --- |
  | "our homes **were** listed on the market for more than 120 days" | "homes **had been** listed on the market for more than 120 days" |
  | "our homes **in inventory** had been listed on the market…" | same — "in inventory" interposes |

  Neither is a whole-phrase match of any of the metric's five surfaces, and **no metric
  declares itself `distinct_from` this one**, so sibling expansion cannot reach it either.
  Closing the gap lexically would need one of: broadening the ontology alias (fitting the
  vocabulary to a fixture, and a founder gate for meaning), or substring/token-overlap
  matching (which reintroduces the failure §8a.3 removed — "Contribution Profit per Home
  Sold" read as `contribution_profit`).

  **This is the measurement that motivates stage 9.** Semantic retrieval that may only *add*
  candidates is precisely the mechanism for a paraphrase no declared surface covers. The
  lexical recall measured in the same run is the baseline hybrid scoping must beat; if it does
  not, lexical stays the default. It was **0.959** here and is **0.960** after the 2026-08-02
  benchmark correction — which is exactly why the criterion is now computed against the
  same-run lexical view rather than against a transcribed constant
  ([STAGE_09_HYBRID_SCOPING.md](STAGE_09_HYBRID_SCOPING.md) §11.7).

  **Stage 9 measured it and did not settle the default** —
  [STAGE_09_HYBRID_SCOPING.md](STAGE_09_HYBRID_SCOPING.md) §11.3c. Hybrid reaches 0.980 at the
  derived `top_k` 2 and recovers one of the two paraphrases; the other is reachable only at a
  cap no gold-independent statistic establishes. What that answers is *reachability*, which is
  all a scope can be scored on. The runtime default is deferred to step 13 and decided on step
  11's narrative-extraction evidence, with `scoping.strategy` staying `lexical` meanwhile.
- [ ] Table-lane benchmark numbers unchanged; its report regenerates identically.
- [ ] `pytest -m "not live"` green.
- [ ] Reports committed and byte-reproducible.

# 9. Non-goals

Embeddings, ranking, top-k, the narrative lane, prompts, the provider. Changing the ontology.
Changing typed selection's policy.
