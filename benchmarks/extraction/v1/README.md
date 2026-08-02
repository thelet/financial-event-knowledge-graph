# Extraction benchmark v1

Hand-reviewed gold claims for the claim-extraction phase. **Every value here was read off
the normalized passage it cites**, on 2026-08-01, against the corpus produced after the
encoding correction (`V1_DOCUMENT_NORMALIZATION.md` §16b). No value is inferred, computed,
or copied from a model's output.

The point of this benchmark is to make "the parser emitted claims" an inadmissible proof.
A lane is scored on eight dimensions independently, because a claim can be right about the
metric and wrong about the period, and a scorer that collapses that into one number hides
exactly the failures that matter for an evidence-linked graph.

## Scope

| | Count |
| --- | --- |
| documents | 15 |
| cases | 26 |
| gold claims | 68 |
| gold events | 4 |
| gold relationships | 2 |
| expected abstentions | 24 |
| distinct metrics covered | 15 of the 20 in scope |

Coverage is deliberate, not proportional: the ambiguous and adversarial cases are
over-represented relative to their corpus frequency, because they are where a lane fails
silently.

## Scored dimensions

A predicted claim is compared to a gold claim on each of these separately:

| Dimension | What it checks |
| --- | --- |
| `metric_identity` | the right `metric_id`, including not confusing `distinct_from` siblings |
| `value` | the numeric magnitude, sign included |
| `unit` | `homes`, `usd`, `percent`, `markets` |
| `scale` | thousands vs millions vs units — the single most dangerous silent error |
| `period` | `period_start`/`period_end` for durations, `instant_date` for stocks |
| `subject` | the entity and its type |
| `evidence` | the cited `passage_id` resolves and actually contains the value |
| `ambiguity` | declared ambiguities carried, ambiguous aliases abstained on |

`scale` gets its own dimension because a table lane that reads `1,153` from a
`(In millions…)` table and emits `1153` rather than `1153000000` produces a claim that is
right on every other dimension and wrong by six orders of magnitude.

## Case schema

```yaml
case_id: kpi-table-q1-2025            # unique, kebab-case
category: deterministic_kpi_table     # see below
lane: tables                          # tables | narrative | either
document_id: norm:…
passage_id: norm:…#p14
notes: >-
  Why this case is here and what it is meant to break.
scale_declaration:                    # omitted when the case carries no scale
  location: in_table | preceding_passage | none
  passage_id: norm:…#p13
  text: "(In millions, except percentages, …)"
gold_claims:
  - metric_id: homes_sold
    value: 2946
    unit: homes
    period_start: "2025-01-01"
    period_end: "2025-03-31"
    subject_entity_id: opendoor
    subject_type: public_company
    source_lane: normalized_table
    assertion_type: reported
    column_label: "March 31, 2025"    # which table column it came from
abstentions:                          # claims a correct lane must NOT emit
  - reason: AMBIGUOUS_ALIAS
    detail: …
```

### Categories

| Category | Cases | What it exercises |
| --- | --- | --- |
| `deterministic_kpi_table` | 7 | clean multi-period KPI tables; column alignment |
| `sparse_reconciliation_table` | 4 | scattered `$`/magnitude/`%` cells, mixed period types |
| `population_wording` | 4 | the 120-day denominator, verbatim, four ways |
| `shareholder_letter_prose` | 3 | prose-only lane; letters have 1 table across 26 documents |
| `negative_or_abstention` | 4 | text that looks extractable and is not |
| `event_or_relationship` | 3 | representative events and relationships |
| `formula_drift` | 1 | Adjusted Gross Profit's definition changing across years |

## Rules the benchmark itself encodes

**Ambiguous aliases are abstentions, not guesses.** The ontology declares seven aliases
ambiguous (`homes`, `contracts`, `under contract`, `gross profit`, `gross margin`,
`margin`, `contribution`). Where a passage uses one bare, the gold answer is *no claim*
plus an `AMBIGUOUS_ALIAS` issue naming the candidates.

**Population wording is carried verbatim or the claim is wrong.** The 120-day metric
appears with four wordings — "our homes", "our homes in inventory", "our portfolio", and
the KPI-table label — and no filing reconciles them. A claim without `population.definition_raw` is
scored incorrect on `ambiguity` even when its value matches.

**Out-of-scope metrics are gold abstentions, not gold claims.** Six metrics name XBRL as
their first source lane and have no lane in this corpus (`V1_CLAIM_EXTRACTION.md` §3.1).
Where such a metric appears in a benchmark table — `Revenue`, `Gross profit`, `Inventory
(at period end)` all do — the expected behaviour is to record:

```text
status: deferred
reason: UNAVAILABLE_REQUIRED_SOURCE_LANE
required_lane: xbrl
```

and emit no observation. A lane that reads `Revenue` off the table anyway is wrong even
though the number is right, because the authoritative tagged source exists and is not
being used.

## Reports

### Table lane

`reports/table_lane_v1.{json,md}` are the committed table-lane results, regenerated by the
runner rather than written by hand. Four commands, one of which writes anything:

```bash
python -m benchmarks.extraction.v1 report            # regenerate both reports
python -m benchmarks.extraction.v1 evaluate          # print totals, write nothing
python -m benchmarks.extraction.v1 case <case_id>    # one case in detail
python -m benchmarks.extraction.v1 claims <case_id>  # every emitted observation for one case
```

Regenerating at the same commit is byte-identical. `implementation_commit` is the only
field allowed to differ between runs — no timestamps, no durations, no absolute paths — so a
diff between two reports is a diff of behaviour. `tests/extraction/test_table_lane_report.py`
enforces that, and also that the committed JSON matches a fresh run.

The report is derived and never authoritative. **If a number in it disagrees with the lane,
the report is wrong**; changing extraction to improve a number here is a gate failure, not a
fix.

### Lexical scope

`reports/lexical_scope_v1.{json,md}` score `LexicalOntologyCandidateScope` — which concepts a
lane may consider for a passage, and why — over **all 26 cases**, not the 11 the table lane
answers for. A scope is defined for any passage, and the narrative cases are where wording
variance actually bites.

```bash
python -m benchmarks.extraction.v1 scope-report          # regenerate both reports
python -m benchmarks.extraction.v1 scope <case_id>       # one case's candidates and reasons
python -m benchmarks.extraction.v1 scope-diff <case_id>  # expected vs included
```

Same determinism rules, enforced by `tests/extraction/test_lexical_scoping.py`.

What it measures is *reachability*, not correctness: the scope only ever adds, so an admitted
concept the gold set does not name is not an error — stage 9's ranking is what narrows. What
would be unrecoverable is the opposite, so the numbers that matter are recalls.

| Gate (STAGE_08 §8) | Required | Measured |
| --- | --- | --- |
| required-concept recall | 1.000 | **0.959** (47/49) — **FAIL** |
| critical-concept recall | 1.000 | 1.000 (23/23) |
| ambiguity preservation | 1.000 | 1.000 (1/1) |

The shortfall is two cases, both `pct_homes_on_market_gt_120_days` in prose, both wordings
the ontology's five aliases for that metric do not carry: `letter-prose-inventory-and-120d-q2-2022`
("5% of our homes **were** listed on the market for more than 120 days") and
`population-our-homes-in-inventory-q1-2023` ("59% of our homes **in inventory** had been
listed…"). No declared-ambiguous surface reaches the metric and nothing declares itself
`distinct_from` it, so neither the ambiguity nor the confusion group can recover it. It is
left open: transcribing two more aliases would be fitting the vocabulary to two fixtures, and
a substring rule in the scope would be stage 9's ranking arriving a stage early. The report's
`known_misses` block states it, and a test fails if the set ever changes in either direction.

Critical-concept recall is scored separately from the aggregate because the aggregate hides
it. It asks, for every case whose gold names one of five confusable pairs — `homes_sold`/`homes_purchased`,
`acquisition_contracts`/`homes_under_contract`, `gaap_gross_profit`/`adjusted_gross_profit`,
`gaap_gross_margin`/`adjusted_gross_margin`, `contribution_profit`/`contribution_margin` —
whether **both** members are in scope. One without the other is the state in which a lane
cannot tell it is reading the wrong one.

### Hybrid scope

`reports/hybrid_scope_v1.{json,md}` score three scopes over the same reviewed cases: the lexical one
above, the semantic candidates alone as a **diagnostic**, and their union. The question is the
one STAGE_09 sets — does semantic retrieval beat the lexical baseline and recover the two
`pct_homes_on_market_gt_120_days` paraphrases, without unacceptable expansion or ambiguity
damage.

```bash
python -m benchmarks.extraction.v1 hybrid-report        # regenerate both reports, offline
python -m benchmarks.extraction.v1 hybrid-build         # fill the vector caches, then report
python -m benchmarks.extraction.v1 hybrid <case_id>     # one case's ranking and candidates
```

`hybrid-report` never touches the network: it reads the committed vectors under `vectors/`,
and a missing one is an error rather than a zero vector. `hybrid-build` is the only command
here that talks to the embedding server on 8081.

**Every score, the verdict and the `top_k` sweep live in
[`reports/hybrid_scope_v1.md`](reports/hybrid_scope_v1.md), and are deliberately not repeated
here.** A number copied into a README is a number nothing regenerates and no test checks; the
report's own decision block is computed from the scores above it, and the sweep beneath it is
computed at every cap. Read them there. The same applies to the sizes of the committed vector
caches, which the report's identity table states.

What this README is for is the parts that are *not* results:

`embedding_only` is a diagnostic and never a runtime option. It is in the report to show which
half of the hybrid column each number comes from.

`vectors/concepts.json` and `vectors/texts.json` are committed. Their header `cache_key` is a
sha256 over `definition_hash | model_id | dimensions | renderer_version |
text_normalization_version`; a file whose key disagrees is rejected, never partially reused.
Editing the ontology therefore invalidates them, which is the intended behaviour.

**The committed vectors are the authority, and a rebuild reproduces meaning rather than
bytes.** The embedding server's output depends on the request that preceded it, so byte
identity of a rebuilt cache is not a property this code promises. The determinism contract is
stated as five clauses in STAGE_09 §1.1 and asserted by
`tests/extraction/test_embeddings_live.py`: live vectors need not be byte-identical, repeated
compatible requests agree to cosine ≥ 0.9999, candidate selection is identical, the persisted
cache is authoritative, and reports generated from that cache are byte-identical.

Semantic additions the gold set does not name are called **unrequired additions** throughout,
never false positives. The benchmark annotates a deliberate subset, so an unannotated addition
is unmeasured rather than wrong — the same reason the table-lane report refuses to call its
matched-over-emitted ratio precision.

### Narrative lane

`reports/narrative_lane_v1.{json,md}` score `OntologyGuidedNarrativeClaimLane` over the 14
prose cases — the 13 whose `lane` is `narrative` plus the one whose `lane` is `either` —
**under both candidate scopes**, on the eight dimensions §4.0 names plus four the prose cases
made necessary — §7.1's population wording, §7.3's ambiguity codes, and the split of
"ambiguity" into an abstention-honouring rate and an ambiguity-preservation rate with its own
denominator. Beyond the dimensions: structured-output validity,
rejection-versus-abstention, the §8a period attribution, the ontology-warning census, and a
ten-category failure classification.

```bash
python -m benchmarks.extraction.v1 narrative-build      # generate, needs the server on 8080
python -m benchmarks.extraction.v1 narrative-report     # replay-only, offline
python -m benchmarks.extraction.v1 narrative <case_id>  # one case under both scopes
```

`narrative-report` never touches the network: it replays `answers/narrative_v1.jsonl` with
`inner=None`, so a missing answer raises and names the request rather than being scored as a
silence. `narrative-build` is the only command here that talks to the generation server.

`answers/narrative_v1.jsonl` is committed for the same reason the vectors are: without it the
report cannot be regenerated offline, and an artifact nobody can rebuild is not evidence. Each
row is keyed on the digest of (prompt, schema, model id, temperature, output budget) and holds
no latency, token count or attempt count — those move between two identical requests, so a
record containing them could never be byte-identical.

**Every score lives in [`reports/narrative_lane_v1.md`](reports/narrative_lane_v1.md) and is
deliberately not repeated here.** What belongs here is the parts that are not results:

Two of the dimensions are **tautologies under the matching rule**, in this report and in
the table-lane one. A predicted claim is matched to a gold claim on `(metric_id, period key)`,
so a matched pair agrees about its metric and its period by construction and `period_accuracy`
cannot read anything but a perfect score. The real period measurement is the `period_wrong`
category of the failure classification plus the attribution table, and the table-lane report's
period accuracy should be read the same way.

The `evidence` dimension is stronger for prose than for tables. For the table lane it means
the cited `passage_id` resolves; for the narrative lane it means that **and** that the quoted
span is verbatim inside that passage. Both go through the same `evaluation.compare`, which
takes the verdict as an argument for exactly this reason.

The failure classifier covers gold observations the lane did not emit, matched observations
that failed a dimension, and claims that break a silence a case requires. It deliberately does
**not** classify every unmatched emitted claim: those are unrequired, and categorising them as
failures would make matched-over-emitted a precision under another name.

Two reviewed cases annotate **one passage**, so anything counted per claim — emitted
observations, ontology warnings, ontology errors — enters the per-case totals twice. Both
forms of every affected count are reported, including a second matched-over-emitted taken over
distinct passages. Gold and matched observations are per case and are unaffected.

Where the lane emits two claims under one `(metric, period)` key, the matcher keeps the
**first in emission order** and records the collision. Emission order is used because it knows
nothing about gold: picking the claim that agrees with the case would make value accuracy
unable to fall.

## Review status

Every case is marked `reviewed: true` only after its values were checked against the cited
passage text. `tests/extraction/test_benchmark_integrity.py` enforces what can be checked
mechanically — that every `passage_id` resolves in the corpus, every `metric_id` exists in
the ontology, every gold value's digits actually occur in the cited passage, and no case
claims an out-of-scope metric. It cannot check that a period label was read correctly; that
is what the manual review is for.
