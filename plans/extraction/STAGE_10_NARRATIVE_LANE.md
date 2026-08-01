# Stage 10 — the ontology-guided narrative claim lane

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 10, §4.3.
**Predecessors:** [STAGE_07_LOCAL_PROVIDER.md](STAGE_07_LOCAL_PROVIDER.md) (the provider),
[STAGE_09_HYBRID_SCOPING.md](STAGE_09_HYBRID_SCOPING.md) (the scope), commit `6e1fc39`.
**Goal:** the second `ClaimLane` — model-backed, reading prose, emitting the same
`LaneClaim` the deterministic table lane emits, and abstaining rather than guessing.

Scoring against the benchmark is **step 11**, not this stage. What this stage owes is a lane
that runs, abstains correctly, and produces claims that survive assembly, ontology validation
and evidence validation. "The JSON parsed" is not the gate.

---

# 1. Runtime, re-verified before implementation *(verified 2026-08-01)*

Both servers are up and coexist on the 16 GiB card.

| | Generation | Embedding |
| --- | --- | --- |
| port | 8080 | 8081 |
| `/health` | `{"status":"ok"}` | `{"status":"ok"}` |
| model id reported | `/home/thele/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf` | echoes the request |
| `n_ctx_slot` | **8192** | 2048 |
| combined VRAM | **9,356 MiB of 16,311 MiB** | |

`pytest -m live tests/extraction/test_provider_live.py` → 11 passed. `enable_thinking: false`
is enforced at config load and is not a tuning knob (§13 open decision 1).

**The model id in `/v1/models` is the absolute path, not the basename in `config/extraction.yaml`.**
Do not start comparing them for equality; step 7 already treats the configured model as the
identity and the wire value as informational.

# 2. Scope

*Corrected 2026-08-02: this section listed five files and described four concerns. The
implementation is five modules plus the package `__init__`, and four modules in `core/`.*

```text
extraction/stages/narrative/
  __init__.py          re-exports, including the two names that moved to core/
  public.py            the issue vocabulary, the response schema, the declared-ambiguous
                       wordings, and the structured result
  prompt.py            prompt construction, pure, versioned
  narrative_lane.py    OntologyGuidedNarrativeClaimLane
  response_mapping.py  model answer -> LaneClaim / NarrativeIssue, with rejection
  answer_store.py      persisted answers keyed by request identity; replay without a GPU
extraction/core/
  units.py             one unit rule both lanes go through
  text_spans.py        find a retyped span, report the original's own characters
  numbers.py           + printed_magnitude / identifies_its_subject / the scale-word readers
  periods.py           + period_phrases / resolve_period_phrase / reporting_period_keys
```

Five modules in the stage plus the package `__init__`, because each is a distinct failure mode:
a contract, a pure string builder, an orchestration, the boundary that turns an untrusted answer
into a typed claim or refuses it, and a durable record. `answer_store.py` is its own file rather
than a dict inside
the lane because §7 asks for two things — byte-identical generation and byte-identical replay —
only one of which is achievable, and the module exists to hold that distinction.

`response_mapping.py` is the one that must not be folded into the lane: every "the model said
something the ontology does not permit" decision lives there, and it has to be testable without
a provider.

**What is in `core/` and why.** Each of these carries no ontology and no model answer, which is
the repository's own test for `core/` material.

| addition | why it is not in the lane |
| --- | --- |
| `units.py` | unit is a scored dimension, and two lanes disagreeing about it would be invisible |
| `text_spans.py` | a shifted slice is a defect a text-reader review catches and a rejection-boundary review does not — which is exactly what happened (§14 S1.1) |
| `numbers.py` additions | reading a printed figure is arithmetic, and the accounting-sign defect (§14 S5.3) is the same kind of thing |
| `periods.py` additions | `period_phrases` is *schema vocabulary*: `narrative_lane` builds the prompt from it before any answer exists, so importing it from the rejection boundary was a layering inversion |

Satisfies `extraction.contracts.ClaimLane`. `supports()` accepts narrative candidates;
`extract()` takes **one** candidate and its text.

# 3. What the lane is allowed to see, and nothing else

Per candidate, the prompt carries exactly:

1. the passage text, verbatim;
2. the candidate concepts from the injected `OntologyCandidateScope`, each rendered with its
   id, label, unit, value type, period type and — where declared — its `distinct_from`
   siblings and its ambiguity notes;
3. the passage's own metadata: document type, filing form, filing date, heading path;
4. the response schema.

It does **not** carry: benchmark gold, other passages, the corpus, previously extracted
claims, or any concept outside the scope. The scope is injected at the composition root, so
step 11 can run the same lane under the lexical and the hybrid scope without touching lane
code — that comparison is why the injection point matters.

**`distinct_from` siblings are in the prompt on purpose.** The scope pulls them in
(`confusion_sibling`) precisely so the model can be told "this is `homes_sold`, and
`homes_purchased` is a different metric it is not". A prompt that lists only the likely
concept throws that away.

# 4. Abstention is a first-class answer

The schema requires the model to return, per finding, either a claim or an abstention with a
reason. Reuse the table lane's issue vocabulary where the meaning is identical
(`AMBIGUOUS_ALIAS`, `UNRESOLVED_METRIC`, `MISSING_PERIOD`, `PERIOD_TYPE_MISMATCH`,
`MISSING_UNIT`, `INVALID_NUMBER`, `DEFERRED_REQUIRED_SOURCE_LANE`,
`MISSING_REQUIRED_CONTEXT`) and add only what prose actually needs. Do not invent a parallel
vocabulary; two spellings of the same abstention make step 11's failure classification
meaningless.

A passage yielding nothing returns a `LaneResult` with empty `claims` and populated
`abstentions`. Silence and abstention are different answers and the benchmark scores the
difference — that is already `ClaimLane`'s stated contract.

# 5. The rejection boundary — `response_mapping.py`

Every one of these rejects the finding, records why, and never repairs it into a claim:

| Condition | Why it is a rejection, not a fix |
| --- | --- |
| `metric_id` not in the candidate scope | the model invented a concept; silently remapping it is §7's forbidden move |
| `metric_id` names a metric whose first `source_lane_preference` is `xbrl` | deferred by §3.1, decided from the ontology, never a hard-coded list |
| unit or value type contradicts the metric's declaration | the ontology declares it; the model does not get a vote |
| period type contradicts the metric's `period_type` | an instant reported as a duration is wrong, not roundable |
| a quoted span not found in the passage | fabricated evidence, the one failure this pipeline exists to refuse |
| value not parseable, or scale asserted without a declaration in the text | §8a.5 — scale applies to money, not counts |

Rejections go to `rejected_claims` with the raw model finding attached, because step 11
classifies them and step 13 writes them to `rejected_claims.jsonl`.

# 6. Evidence — the §8a.11 constraint

**The evidence anchor is the normalized `passage_id`. It does not move.** `verify` (§4.5)
resolves every claim's evidence against `passages.jsonl`, and an anchor that does not resolve
is the one thing the pipeline must refuse.

Within that: §8a.11 measured that a whole-passage vector cannot use a paraphrase a
sentence-level vector ranks first. So the lane **may** ask the model for the exact quoted
span it read a value from, use that span to check the value is really there, and record it in
`extractor_metadata` and in the claim's `raw_text`.

It **must not** mint a sub-passage identifier — no `#p10:s3`, no character offsets used as an
evidence id. A span is a *check* and a *provenance note* inside a passage-anchored claim, not
an anchor of its own.

# 7. Determinism and replay

`temperature: 0.0`, `enable_thinking: false`, one request per candidate, no batching.

Model outputs are persisted keyed by **stable content identity** — the digest of
(prompt, schema, model id, sampling parameters) — so step 11 can regenerate its report without
a GPU and prove it byte-identical. Store `content_sha256` and the parsed content.

**Volatile response metadata stays out of the authoritative record.** `raw_sha256` covers
llama.cpp's envelope, which carries a fresh `id`, `created` and `timings` on every identical
request (`contracts.GenerationResult` documents this, measured 2026-08-01). Latency and token
counts are operational statistics: they may be logged and printed, and they may not enter a
file that is required to be byte-identical — the same rule that settled STAGE_09 §11.2.

Stage 9 also established that the local runtime is not bit-reproducible across differing
request histories. Do not claim byte-identical *generation*; claim byte-identical *replay from
the persisted outputs*, and test that.

# 8. §7 policies apply at assembly, in one place

- **7.1** — every `pct_homes_on_market_gt_120_days` claim carries
  `population_definition_raw` verbatim from its passage. Two claims whose values differ are
  not one series.
- **7.3** — every `homes_sold` claim carries the `homes_sold_recognition_point` ambiguity code.
- **7.5** — the lane never emits `SUPERSEDES`. Both observations stand, distinguished by the
  passage digest in the ID.
- **7.6** — never infer an end date, a termination, or any fact from absence.

These belong to `core/assembly.py`, which both lanes already go through. Do not reimplement
them in the narrative lane.

**Two honest corrections to what §8 claims** *(2026-08-02)*.

**`assemble` has no non-test caller yet.** `git grep 'assemble('` outside `core/assembly.py`
hits tests only; `benchmarks/extraction/v1/runner.py` imports `deferred_metric_ids` and nothing
else and builds its report without going through assembly. So "the §7.1/§7.3 move leaves
`table_lane_v1.*` byte-identical" is **true and vacuous** — the report generator never calls the
function that changed. The policies themselves are sound and ontology-derived
(`metric.population`, `metric.ambiguities`), and step 13 is where `assemble` acquires a real
caller. Recorded so nobody reads the byte-identical report as evidence the policies were
exercised.

**§7.3 as implemented is wider than §7.3 as written, and the plan was amended rather than the
code narrowed.** §7.3 names one ambiguity code on one metric; `declared_ambiguity_codes`
attaches every ambiguity the ontology declares against the metric, which today means four
metrics rather than one (`homes_sold`, `acquisition_contracts`,
`pct_homes_on_market_gt_120_days`, `homes_under_resale_contract`). Amending the plan is the
right direction: each of those codes was recorded on the concept precisely so it would travel
with the observation instead of being silently decided, and narrowing the code to one metric id
would put a metric name in the extractor that the vocabulary already states. V1 §7.3 now says
so.

# 9. Tests

**Offline** — `tests/extraction/test_narrative_lane.py`, a deterministic stub
`GenerationProvider` replaying recorded answers:

1. The lane satisfies `ClaimLane` driven **through** the protocol.
2. A claim naming a concept outside the scope is rejected and recorded, not emitted.
3. A claim whose unit contradicts the metric's declaration is rejected.
4. A claim whose period type contradicts the metric's declaration is rejected.
5. A quoted span absent from the passage is rejected as fabricated evidence.
6. A metric whose first source-lane preference is `xbrl` is rejected as deferred — derived
   from the ontology, asserted for a metric the ontology actually declares that way.
7. An empty answer produces abstentions, not silence, and a `LaneResult` that still names
   the passage.
8. Every emitted claim passes `core.assembly`, `ontology.validate_claims` and evidence
   validation, driven end to end.
9. §7.1 and §7.3 policies are applied to claims that require them.
10. No sub-passage evidence identifier is ever produced — assert every evidence id resolves
    in `passages.jsonl`.
11. Prompt construction is pure and versioned: same candidate, same scope, same string.
12. The prompt contains the candidate's `distinct_from` siblings when the scope supplied them.
13. Replay from persisted outputs is byte-identical, and no latency or token count reaches
    the persisted artifact.
14. Structural: nothing under `extraction/stages/tables/` imports `extraction.providers`;
    nothing under `extraction/` imports the benchmark. Both exist and must stay green.

**Live** — `tests/extraction/test_narrative_lane_live.py`, `@pytest.mark.live`, fixture
gated on `health().ok` like the other two live files:

15. The live gate, on **representative narrative passages** — shareholder-letter prose and
    MD&A prose, not a table. Step 7's smoke test used a table and proved integration only.
16. For each gate passage: schema-conformant output, at least one claim or a defensible
    abstention, the claim reaching `OntologyClaim`, `ontology.validate_claims` clean,
    evidence validation clean, and the quoted span present in the passage.
17. Runtime statistics recorded: tokens, latency, and the rejection count.

# 10. Live gate — demonstrated, not asserted

Pick the gate passages from the corpus by document type and passage kind, **not** from the
benchmark case list, and say which were used and why. The gate passes when a real
shareholder-letter passage and a real MD&A passage each produce at least one claim that
survives the full validation chain, or an abstention that is correct for a stated reason.

Bounded repair is expected and is not a founder gate: prompt revisions, schema changes, parser
corrections, retries and provider restarts are ordinary work. The 9B model *failing the
structural live gate after bounded repair* is a founder gate — reaching it means saying so,
with what was tried.

# 11. Acceptance

- [x] `pytest -m "not live"` green — **1,499**, up 35 from the 1,464 at `6e1fc39`.
- [x] `pytest -m live` green with both servers up — **49**, up 7 from 42.
- [x] The live gate demonstrated on narrative passages, with recorded numbers (§10, and see
      the caveat in §12a about which of those numbers are measurements).
- [x] No change to the table lane, the ontology, the benchmark cases, or any committed report.
- [x] `scoping.strategy` still `lexical`; the scope injected, not hard-coded.

# 12a. What review found after the lane ran *(2026-08-02)*

An adversarial review, most of it by mutation, found fourteen defects that the suite as
committed did not catch. Each is fixed, each has a test that fails without the fix, and each
test was proven by re-applying the mutation and watching it go red. The two worth carrying
forward into step 11 rather than closing:

**The recorded evidence slice was silently misaligned.** `_find_folded` took its offsets over
`fold(text)` and sliced `text`. `fold` deletes quote characters, so every deleted character
before a match shifted the recorded span left — and the shifted span is still a contiguous run
of the passage, which is why `validate_quoted_text` never complained. 5,967 of the corpus's
12,442 passages contain a fold-deleted character. The reader now maps transformed positions back
to original ones one character at a time, and the module moved to `core/text_spans.py`, where it
reads as what it is.

**`period_label` narrows wrong-period selection; it does not eliminate it.** The docstring said
the enum made a wrong-year answer "unrepresentable". It does not: a comparative MD&A paragraph
prints its prior-period phrase, so choosing it passes every check. What is claimed now is what
is true — the enum removes every period the passage does not print. The residual is a **measured
dimension for step 11** (V1 §8a.12): every claim records which phrase was chosen, whether it sits
inside the quoted evidence sentence, and how far away it is when it does not.

Two smaller results that change how a number should be read:

- **The definitional gate passage's abstention is partly structural.** `period_phrases` returns
  `()` there, so the schema's `period_label` enum degenerates to `[""]` and any claim would have
  been auto-refused with `MISSING_PERIOD` before definitions were considered. On the runs
  recorded here the model did decline on its own, twice, with `DEFINITIONAL_NOT_OBSERVATIONAL` —
  but the empty claim list is not by itself evidence of that, and the live test now prints which
  of the two declined.
- **`AMBIGUOUS_ALIAS` was advisory and is now enforced.** The prompt asked the model to abstain
  on declared-ambiguous wordings and nothing checked that it had; live, with
  `gaap_gross_margin` and `adjusted_gross_margin` both in scope and the passage printing a bare
  "gross margin", the lane emitted the GAAP one. A bare ambiguous wording can no longer select a
  concept: the quoted sentence must print a word belonging to the chosen concept and not to the
  ambiguous wording.

# 12. Non-goals

Benchmark scoring (step 11). Events and relationships (step 12). The integrated run and
catalogs (step 13). A second model, model comparison, batching, streaming, concurrency, or
fine-tuning. Changing the ontology, the benchmark, or the scope's default.
