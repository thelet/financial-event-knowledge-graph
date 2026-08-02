# Stage 9 — embedding index and hybrid candidate scoping

**Parent:** [V1_CLAIM_EXTRACTION.md](V1_CLAIM_EXTRACTION.md) §9 step 9, §4.2a.
**Predecessor:** [STAGE_08_LEXICAL_SCOPING.md](STAGE_08_LEXICAL_SCOPING.md), commit `e97ad5a`.
**Goal:** answer one question with a measurement, not a preference —

> Does semantic retrieval materially improve on lexical required-concept recall **0.959**,
> and specifically recover the two `pct_homes_on_market_gt_120_days` paraphrases, without
> unacceptable candidate-set expansion or ambiguity degradation?

*(0.959 is the figure this stage was scoped against, measured 2026-08-01. The 2026-08-02
benchmark correction moved it to 0.960 and hybrid's to 0.980; the answer is unchanged and the
verdict still reads "lexical stays the default". See §11.7.)*

**Hybrid is not the expected answer.** "Lexical stays the default" is a legitimate result and
must be reported as readily as the other one. §7 states the decision rule in advance so the
numbers cannot be read backwards into it.

---

# 1. Runtime decision — llama.cpp embedding server *(verified 2026-08-01)*

The plan named `Qwen/Qwen3-Embedding-0.6B` but not how to run it. Both options were
inspected before anything was installed.

| | Python stack | **llama.cpp embedding server (chosen)** |
| --- | --- | --- |
| present today | `torch`, `transformers`, `sentence-transformers` all **absent** from `base` and from `fkg-llm` | `~/llama.cpp/build/bin/llama-server` already built, CUDA, `sm_120`, validated 2026-08-01 |
| new Python dependencies | ~3 GB (torch + CUDA wheels + two libraries) | **none** |
| second CUDA runtime | yes, beside the validated llama.cpp one | no |
| wire format | in-process | `POST /v1/embeddings`, the same OpenAI-compatible shape `extraction/providers/` already owns |
| what it buys | nothing this stage needs | — |

Rejected the Python stack: three gigabytes of new dependency, and a second CUDA runtime to
keep in agreement with the first, to produce **131 concept vectors**. The repository's
dependency set stays `httpx`, `pydantic`, `PyYAML`. `numpy` is present in the environment but
is **not** a declared dependency and must not become one — cosine over 131 unit vectors is a
`sum(a*b for a, b in zip(...))`.

## 1.1 Verified runtime facts

Model `Qwen/Qwen3-Embedding-0.6B-GGUF`, **f16**, downloaded 2026-08-01:

```text
~/models/qwen3-embedding-0.6b/Qwen3-Embedding-0.6B-f16.gguf
1,197,629,632 bytes
sha256 421a27e58d165478cc7acb984a688c2aa41404968b0203e7cd743ece44c54340
```

f16 over the available Q8_0 (639,150,592 bytes) deliberately: 1.14 GiB on a 16 GiB card is
free, and this stage's entire purpose is deciding lexical versus hybrid on vector quality.
Quantization noise is the one confound worth paying 560 MiB of disk to remove.

Server, port **8081** so it coexists with the generation server on 8080:

```bash
~/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen3-embedding-0.6b/Qwen3-Embedding-0.6B-f16.gguf \
  --embedding --pooling last --embd-normalize 2 \
  -ngl 99 -c 2048 -ub 2048 --host 127.0.0.1 --port 8081
```

| Fact | Measured |
| --- | --- |
| `/health` | `{"status":"ok"}` |
| dimensions | **1024** |
| vector norm | 1.0000000059 — already L2-normalized, so cosine is a dot product |
| determinism | **conditional — the server is not request-independent.** The determinism contract below replaces the claim this row originally made *(corrected 2026-08-01, §11.1)* |
| VRAM, model resident | 3,212 MiB total including the 433 MiB idle baseline |
| batching | `input` accepts a list; 16 per request used below |

**The row above originally read "two identical requests returned bit-identical vectors".**
That is false, and it was load-bearing: the whole durability design was drafted around it. The
measurement is in §11.1. Rather than leave the correction only there, the claim is replaced
here by the contract that actually holds.

## 1.1a The determinism contract — five clauses *(verified 2026-08-01)*

What is required, what is not, and where each clause is asserted. Any statement about
reproducibility in this stage must be one of these five, or it is not a claim this code makes.

| # | Clause | Asserted by |
| --- | --- | --- |
| 1 | Live vectors are **not** required to be byte-identical between requests, runs, or rebuilds. | Nothing asserts byte identity; §11.1 records why. |
| 2 | Two compatible requests for the same text must agree to **cosine ≥ 0.9999**. | `test_embeddings_live.py::test_repeated_requests_agree_to_the_contract_tolerance` (live) |
| 3 | Candidate selection must be **identical** — a scope built on rebuilt vectors admits exactly the same concepts for all 26 benchmark passages. | `test_embeddings_live.py::test_a_rebuilt_concept_cache_agrees_with_the_committed_one_and_scopes_identically` (live) |
| 4 | The **persisted cache is authoritative**. A committed vector is the answer; a rebuild is checked against it, never the other way round. | `vector_cache.py` — a cache whose `cache_key` disagrees is rejected outright; the report's offline path has no provider, so a miss raises. |
| 5 | Reports generated **from that cache** must be **byte-identical** on regeneration, apart from `implementation_commit`. | `test_hybrid_scoping.py::test_regenerating_the_hybrid_report_twice_is_byte_identical` and the two committed-matches-fresh tests (offline) |

Clause 1 is the one that costs something to accept, and accepting it is what makes clauses 2-5
true statements rather than hopeful ones. A suite that asserted byte identity of live vectors
would be asserting the llama.cpp scheduler, and would go red the day the server is fixed or
run single-slot.

**`-ub 2048` is required, not tuning.** The physical batch must cover the longest single
input or the server rejects it; a KPI table passage exceeds the 512 default.

**The response's `model` field echoes the request string, not the loaded model.** Model
identity for the cache key must come from configuration, and the adapter must not read it
back off the wire and believe it.

# 2. Concept rendering — renderer `v1`

131 concepts, from `ontology.registry.definitions.concepts` — all categories, not the 26
metrics. Rendered by one pure function, versioned, with the version in the cache key.

Fields composed, joined by ` | `:

```text
label | category | alias… (declaration order) | description
```

## 2.1 What is deliberately excluded, and why

**`source_evidence[].quote`** — real filing sentences. They are provenance, not definition,
and embedding them would let a benchmark passage match itself through the ontology.

**`population.raw_variants`** — excluded after measuring, which is the interesting one.
Those variants are partly transcribed from the corpus: `pct_homes_on_market_gt_120_days`
lists `"our homes in inventory"`, which is verbatim the wording of the second lexical miss.
Including it would make a recovery of that case partly circular. Measured both ways
*(2026-08-01)*:

| Probe | with variants | without |
| --- | --- | --- |
| `5% of our homes were listed on the market for more than 120 days` | rank 0, 0.8133, margin 0.1977 | rank 0, **0.8126**, margin 0.1970 |
| `59% of our homes in inventory had been listed…` | rank 0, 0.8485, margin 0.1553 | rank 0, **0.8305**, margin 0.1373 |

Both recover at rank 0 either way. Excluding the corpus-fitted content costs 0.018 of
similarity on one probe and changes no ranking, so it is excluded and the measurement stays
clean. **Record this ablation in the report** — it is the evidence that the recovery is
semantic and not a transcription matching itself.

**`metric_formula` concepts (8 of the 131)** are rendered and indexed like the rest, but see
§4.2: they are the reason a naive top-k damages ambiguity, and the report must show it.

# 3. The vector cache

No vector database. Two JSON files, same format, one header each.

```text
benchmarks/extraction/v1/vectors/concepts.json   131 entries, committed
benchmarks/extraction/v1/vectors/texts.json      benchmark passages + probes, committed
data/embedding_cache/                            runtime, gitignored, same format
```

**The runtime root is not built in this stage** — §11.5. Only the two committed files exist,
and the `scoping.hybrid.cache_root` key that would have named the third is not shipped. The
design above stands for the composition root that fills it (step 10).

**Cache identity** — a header field `cache_key`, sha256 over, in this order:

```text
ontology definition_hash | model_id | dimensions | renderer_version | text_normalization_version
```

`e8d4af709be2…` is the current `definition_hash` (`3372c5777c1d…` until the 2026-08-02
temporal-model change, §11.8); §13 open decision 0 exists precisely
because editing `aliases.yaml` moves it and invalidates this cache, which is the intended
behaviour. A cache whose header `cache_key` does not match the computed one is **rejected as
stale**, never silently reused and never partially reused.

**Vector storage: base64 of little-endian float32**, not decimal text. A vector is not
human-auditable at any precision, so readability buys nothing, while float32 round-trips
bit-identically and makes byte-identical regeneration provable rather than argued. The
auditable content — `concept_id`, the rendered string, its sha256, the header — stays plain
text beside it. 131 × 1024 × 4 B ≈ 716 KiB base64.

**Fill-through.** A lookup miss with a provider configured embeds and writes; a lookup miss
with no provider is an error naming the missing text, never a zero vector. That is what lets
the benchmark report regenerate offline from the committed cache and still be rebuildable
from scratch against the live server.

# 4. `HybridOntologyCandidateScope`

```text
extraction/providers/local_openai_compatible_embeddings.py   the EmbeddingProvider adapter
extraction/stages/scoping/concept_rendering.py               renderer v1, pure
extraction/stages/scoping/vector_cache.py                    identity, load, fill-through
extraction/stages/scoping/hybrid.py                          HybridOntologyCandidateScope
```

Split this way because each holds a distinct concern with its own failure mode — an HTTP
adapter, a pure string function, a durability contract, a set-union policy. Do not split
further; a threshold constant does not need a module.

## 4.1 Add-only, enforced

`scope_for(text)` returns the **lexical scope unchanged**, plus semantic candidates. One new
reason code:

```python
SEMANTIC_NEIGHBOUR = "semantic_neighbour"   # in SCOPE_REASONS, NOT in PROTECTED_REASONS
```

`extraction/stages/scoping/public.py` already anticipates this exactly — it states
`PROTECTED_REASONS` as its own name rather than as `SCOPE_REASONS` "because the two will stop
being equal the moment the hybrid scope adds a semantic reason." Cash that in; do not widen
`PROTECTED_REASONS`.

A concept the lexical scope already found and the embedding also ranks carries **both**
reasons. It stays protected. The hybrid scope has no filter, no removal and no reordering of
protected candidates anywhere in it.

## 4.2 Selection rule — measured, not assumed

Take `top_k` by cosine subject to `min_similarity`, both from configuration. **Do not pick
them from the benchmark's gold answers** — choose them from the shape of the similarity
distribution, then report what recall and scope size they produce. Two measured facts to
work from *(2026-08-01)*:

| Probe | top-1 | gap to next |
| --- | --- | --- |
| `5% of our homes were listed…120 days` | `pct_homes_on_market_gt_120_days` 0.8126 | 0.199 |
| `59% of our homes in inventory…` | `pct_homes_on_market_gt_120_days` 0.8305 | 0.137 |
| `Homes sold in period` | `homes_sold` 0.7577 | **0.015** |
| `Contribution Margin was 5.4% in the quarter` | `contribution_margin` 0.7037 | 0.014 |

A true paraphrase separates by ~0.15; a short label does not separate at all — four concepts
sit within 0.016 of the top for `Homes sold in period`, and `contribution_margin_v1`, a
`metric_formula`, ranks second for the last probe. A fixed absolute threshold therefore
behaves completely differently on prose and on labels, and this is the finding that decides
the rule. Report the distribution; justify the rule from it in the brief's own terms.

# 5. Configuration

`config/extraction.yaml`, new keys. Every value is the validated runtime, not a guess.

```yaml
embedding:
  kind: local_openai_compatible
  base_url: http://127.0.0.1:8081
  model: Qwen3-Embedding-0.6B-f16.gguf
  dimensions: 1024
  timeout_seconds: 120
  max_retries: 2

scoping:
  strategy: lexical          # no consumer until step 10; validated at load — §11.5
  hybrid:
    renderer_version: v1
    top_k: 2                 # derived and asserted — §11.3, §8 test 15
    min_similarity: 0.45     # derived and asserted — §11.3, §8 test 15
```

`cache_root` was specified here and is **not shipped** — §11.5. Nothing in this stage fills it,
and a key that validates and does nothing is worse than an absent one.

`strategy` stays `lexical`. §7's decision rule is applied to the report and answered there;
the lexical-versus-hybrid *default* is deferred to step 13, which decides it on step 11's
narrative-extraction evidence rather than on reachability (§11.3c).

# 6. Evaluation — three views, one report

`benchmarks/extraction/v1/reports/hybrid_scope_v1.{json,md}`, generated by a `scope_runner`
extension, byte-identical on regeneration with `implementation_commit` the only varying
field — the same rules as steps 6 and 8.

| View | What it is | Why it is there |
| --- | --- | --- |
| `lexical` | step 8 unchanged | the baseline being beaten or not |
| `embedding_only` | **diagnostic**, never a candidate default | isolates what retrieval alone can and cannot do; it will fail ambiguity preservation and that is the point |
| `hybrid` | lexical ∪ semantic | the candidate |

Per view: required-concept recall, critical-concept recall, known-instance recall, ambiguity
preservation, counts by reason code, scope size mean/median/max/min, and per case the
candidates with their reasons.

Additionally, hybrid only:

- **semantic-only recoveries** — concepts hybrid reaches that lexical does not, each named,
  with its similarity and the case;
- **unrequired additions** — the cost side of the same coin, counted and characterised. Never
  called false positives, anywhere, in any artifact: the benchmark annotates a deliberate
  subset of what each passage licences, so an addition outside it is **unmeasured**, not
  wrong. Calling it a false positive would assert a measurement this benchmark does not make —
  the same reason step 6 refuses to call its matched-over-emitted ratio precision. The report
  key is `semantic_only.unrequired_additions`;
- **ambiguity delta** — for every case whose gold expects an `AMBIGUOUS_ALIAS` abstention, do
  all candidates of that surface remain in scope, and did semantic addition introduce a
  *new* near-neighbour that makes the abstention harder to reason about;
- **the renderer ablation of §2.1**, reproduced in the report;
- cache build time, cache reuse time, per-passage embedding latency, `cache_key`.

# 7. The decision rule — stated before the numbers

Hybrid becomes the default **only if all four hold**:

1. required-concept recall strictly greater than **the lexical view measured in the same
   run**, and both `pct_homes_on_market_gt_120_days` paraphrases recovered. *(Was the
   transcribed constant **0.959** until 2026-08-02; see §11.7 for why a transcribed baseline
   was the wrong instrument.)*
2. critical-concept recall **1.000** and ambiguity preservation **1.000** — unchanged, not
   merely non-catastrophic;
3. scope size mean no worse than **+25%** over lexical's 17.9 of 131 concepts;
4. no semantic addition displaces or outranks a protected candidate anywhere — structurally
   impossible by §4.1, and asserted by a test rather than trusted.

If 1 fails, lexical stays the default and the report says so plainly. If 1 holds and 3 fails,
that is a real tension and a **founder gate**, not a judgement call to make here.

**If the benchmark cannot distinguish the two strategies at all** — identical recall on all
four scores — that too is a founder gate, as §9 step 9 requires. Say it, stop, do not choose
by preference.

# 8. Tests — `tests/extraction/test_hybrid_scoping.py`

Offline. A deterministic stub `EmbeddingProvider` covers everything except the live gate.

1. Every lexical candidate survives in the hybrid scope, for all 26 benchmark passages —
   the add-only rule, driven through real corpus text rather than asserted.
2. A semantic candidate carries `semantic_neighbour` and is **not** protected.
3. A concept found both ways carries both reasons and **is** protected.
4. `SEMANTIC_NEIGHBOUR ∈ SCOPE_REASONS` and `∉ PROTECTED_REASONS`.
5. Renderer `v1` is pure and stable: same concept, same string, twice; and the string
   contains no `source_evidence` quote and no `population.raw_variants` entry.
6. Cache identity changes when any of the five inputs changes — five assertions, one per
   input, including `definition_hash`.
7. A cache whose header `cache_key` disagrees with the computed one is rejected, not reused.
8. Vectors round-trip base64 float32 bit-identically.
9. A cache miss with no provider raises, naming the text. It never returns a zero vector.
10. The hybrid scope satisfies `OntologyCandidateScope` driven **through** the protocol.
11. The hybrid scope with an all-zero-similarity provider is exactly the lexical scope.
12. Nothing under `extraction/` imports the benchmark; nothing under
    `extraction/stages/tables/` imports `extraction.providers`. Both already exist and must
    stay green.
13. Report determinism and committed-report-matches-fresh, as in steps 6 and 8.

14. The embedding adapter's failure paths, with a fake transport and no server: `health()`
    against a reachable and an unreachable endpoint, a 503 retried to the bound then raising,
    a timeout raising `ProviderTimeout` without a second attempt, a refused connection raising
    `ProviderUnavailable` rather than a generic transport fault, and a response carrying two
    elements with the same `index` raising rather than attaching a vector to the wrong text.
    `tests/extraction/test_provider.py` is the precedent and the shape to match.
15. The two configured numbers are the ones the report's own statistics produce: `top_k`
    equals the maximum of the standout-3sd histogram, and `min_similarity` equals the pooled
    mean + 1 sd rounded to two places. §11.3 says the verdict rests on the derivation, so the
    derivation is executable.
16. `reason_vocabulary` covers every reason a `CandidateScope` actually carries — a narrower
    one makes `counts_by_reason()` omit candidates that are still in `concept_ids`.

Live (`@pytest.mark.live`, `tests/extraction/test_embeddings_live.py`):

17. `/health` on 8081, dimensions 1024, unit norm.
18. ~~Two identical requests return bit-identical vectors.~~ **Not a requirement — §1.1a
    clause 1.** Implemented as clause 2: repeated compatible requests agree to cosine
    ≥ 0.9999. The earlier version asserted the *inequality* — that two back-to-back requests
    differ — which pinned a llama.cpp scheduling artifact as a requirement and would go red
    the day the server is fixed or run single-slot.
19. ~~A rebuilt concept cache is byte-identical to the committed one.~~ **Not achievable —
    §11.1.** Implemented as clause 3: a rebuilt cache agrees to cosine ≥ 0.9999 and selects
    identical candidates for all 26 cases.

# 9. Acceptance

- [x] Environment decision recorded with the numbers behind it (§1) — done above.
- [x] `pytest -m "not live"` green: **1,405 passed, 19 deselected**, from 1,331 + 74.
      The stage first landed at 1,379; the 26 tests added after review are the ones
      §8.14-16 name, plus the sweep, ablation-delegation, terminology, README and
      helper-identity checks the corrections above required.
- [~] `pytest -m live`: the four embedding gates pass against 8081
      (`tests/extraction/test_embeddings_live.py`). The generation server on 8080 is stopped
      for this stage, so `test_provider_live.py` was **not run** — named as skipped rather
      than counted as green.
- [x] Step 8's `lexical_scope_v1.{json,md}` regenerate **unchanged** apart from
      `implementation_commit`. Step 6's `table_lane_v1.*` likewise. Verified: four changed
      lines across the four files, all of them the commit.
- [x] `hybrid_scope_v1.{json,md}` committed and byte-reproducible from the committed vector
      cache, twice — clause 5 of §1.1a, with clause 1's qualification that a *rebuilt cache*
      reproduces the selection and not the bytes.
- [x] §7's decision rule applied: **lexical stays the default**, criterion 1 failed on its
      second half. §11.3 records what actually decided it, and §11.3c records that the runtime
      default is deferred to step 13 rather than settled by this report.

# 10. Non-goals

The narrative lane, prompts, generation (step 10). Benchmark scoring of model output
(step 11). Events and relationships (step 12). A vector database, an ANN index, a second
embedding model, reranking, or embedding anything outside candidate scoping. Editing the
ontology.


# 11. Corrections after implementation *(2026-08-01)*

Written after the stage was built and measured. Every item here contradicts something above
it; the brief is left intact and corrected here rather than quietly edited, because the
correction is the more useful record.

## 11.1 The embedding server is **not** request-independent — §1.1 is wrong

§1.1 records "two identical requests returned bit-identical vectors". They do not. The vector
depends on the request that preceded it in the slot:

| Sequence | Result |
| --- | --- |
| `A`, `C`, `C` | the two `C` vectors **differ**, up to 1.3e-4 per component |
| `A`, `C` … `A`, `C` | the two `C` vectors are **bit-identical** |
| `C` alone vs `C` at position 2 of a 3-input batch | differ, 3.2e-4 |
| cosine between two disagreeing `C` vectors | 0.9999996 |

`cache_prompt: false` was tried as a fix and changes nothing; it was removed rather than left
in place looking like it did something.

Consequences, all of which are implemented rather than noted:

- **§8's live test 16 cannot be written as byte-identity.** A rebuilt concept cache agrees
  with the committed one to cosine 0.9999 — on the run recorded, 127 of 131 vectors were
  bit-identical, the worst was `regulator` at 1 - 1.5e-5 — and produces *identical selected
  candidates for all 26 cases*. That is what the live test asserts.
- The committed cache is the authority. Offline regeneration of the report from it is exactly
  byte-identical, which is the property the offline suite pins.
- Two full rebuilds from empty *were* byte-identical to each other on one occasion and were
  not on another. It is not a property to depend on.

## 11.2 §6 asks for two things that cannot both hold

§6 requires "cache build time, cache reuse time, per-passage embedding latency" **in** a
report that is also byte-identical on regeneration with `implementation_commit` the only
varying field. A duration is not reproducible. Byte-identity won, because it is what steps 6
and 8 are held to and what a diff depends on; the timings are printed by the command that
writes the report and a test asserts no duration reaches the artifact. Measured on the run
recorded: 158 vectors embedded in 2.2 s (14 ms each) cold, whole report from the committed
caches in ~1.6 s with no network.

## 11.3 `top_k` and `min_similarity`, and what actually decided the stage

The distribution the brief's §4.2 tabulates is entirely of **short probes**. The benchmark
scores **whole passages**, and the two are not on one scale: 26 case passages top out at
0.6990 against a probe maximum of 0.8695, and the concept ranked first for a KPI table is
usually a `metric_formula` the table does not name. An absolute threshold can only floor; rank
has to select. `min_similarity` = pooled mean + 1 sd = 0.4531 → **0.45**; `top_k` = the largest
number of concepts standing clear of a text's own field at its own mean + 3 sd, which over 32
texts is 0 for 22, 1 for 8 and 2 for 2 → **2**.

Both derivations are **executable** (§8 test 15): the shipped config is asserted against the
report's own statistics, so an edit to either number that the distribution does not support
fails the suite. That is the difference between a derived number and a number beside a
paragraph claiming it was derived.

### 11.3a Counts: 32 texts are 26 distinct texts

The 26 cases sit on **20 distinct passages** — three of them read different columns of one Q1
2025 KPI table — so six passages are pooled twice or three times in every pooled statistic.
Both counts are now reported, and the report states which the statistics are weighted by:
**by case, all 32 texts**, which is the honest default for a benchmark whose unit is the case.
Deduplicated to the 26 distinct texts the standout-3sd histogram is `{0:18, 1:6, 2:2}` — the
same maximum, so **`top_k` is unaffected** by the double counting.

### 11.3b Which pool `min_similarity` came from, and why it is arguable

The pool moves the answer by 0.03-0.04, so it is an argument and not a detail *(all four
measured 2026-08-01)*:

| Pool | texts | mean + 1 sd | rounded |
| --- | --- | --- | --- |
| **all 32 texts (as shipped)** | 32 | 0.4531 | **0.45** |
| minus the two paraphrase probes | 30 | 0.4504 | 0.45 |
| 26 case passages only | 26 | 0.4159 | 0.42 |
| 20 distinct case passages | 20 | 0.4100 | 0.41 |

The shipped pool is all texts, case-weighted, for the same reason the statistics are: the
floor describes the background against which *cases* are scored.

**On gold leakage.** Two pooled texts — the `paraphrase-1` and `paraphrase-2` probes — are the
wordings of stage 8's two known misses, and are therefore derived from the gold answer this
stage is trying to reach. Dropping them entirely leaves `min_similarity` at 0.4504 → **0.45**,
unchanged, and leaves the histogram maximum at **2**, unchanged. Neither derived number depends
on the two gold-derived texts. That is the evidence that there is no material gold leakage in
the derivation, and it is visible in the report rather than asserted here.

### 11.3c The full `top_k` sweep

Computed at every cap in 1-8, on **all four** §7 criteria — not only the two that move. An
earlier version computed recall and scope growth and left the prose asserting the rest.

- `top_k` **2** reaches required-concept recall **0.980**;
- `top_k` **≥ 3** reaches **1.000**, with both paraphrases recovered;
- **every** tested `top_k` preserves critical-concept recall, known-instance recall and
  ambiguity preservation at **1.000**, and no cap displaces a protected candidate;
- the **+25% scope-cost budget does not discriminate anywhere** in 1-8: growth runs **+1.5% to
  +14.8%**, so criterion 3 holds at every cap and cannot choose between them;
- **no gold-independent heuristic decisively establishes the correct runtime `top_k`.** The
  distribution's own statistic gives 2; the scope-cost budget gives no answer; and the recall
  curve that separates the caps is read off the gold annotation, which is exactly what a
  runtime cap may not be tuned against. This is a **finding**, not a caveat, and it is why the
  lexical-versus-hybrid default is deferred to step 13.

**The verdict is about reachability, and the runtime default is not settled by it.** §7's rule
is applied and its answer is "lexical stays the default", but everything measured here is
whether a lane *could* see a concept. Whether the narrative lane extracts better claims under
one scope or the other is step 11's measurement, and the default is decided at step 13 on that
evidence. `scoping.strategy` stays `lexical` in the meantime.

## 11.4 The whole-passage dilution finding *(verified 2026-08-01)*

The finding §7 did not anticipate, and the one stage 10 inherits as a constraint:

- the paraphrase that stays unreached is not unreached because the model cannot paraphrase.
  As a **sentence** it is rank **0** at similarity **0.8307** with a margin of **0.1375**;
- embedded as the whole **2,046-character passage** it occurs in, the same concept ranks
  **third** (rank 2, 0.5169), and **nothing** in that passage stands clear of its own
  background at 3 sd;
- whole-passage embedding — not the vocabulary, not the model — is therefore what the
  benchmark actually measured.

**The evidence boundary remains the normalized passage.** A claim cites a `passage_id` and
`verify` resolves it against `passages.jsonl`. Stage 10 **may** focus prompts, or semantic
scoring, on **evidence-resolvable spans** within a passage — sentences it can still cite by
that passage's id. Stage 10 **must not** create synthetic evidence anchors: a sub-passage
identifier would not resolve against `passages.jsonl` in `verify`, and unverifiable evidence
is the one thing this pipeline exists to refuse. Recorded as a numbered finding in
`V1_CLAIM_EXTRACTION.md` §8a.11.

## 11.5 `scoping.hybrid.cache_root` is removed, not deferred

§3 promises `data/embedding_cache/` as a runtime cache "same format, gitignored". Nothing in
this stage fills it: the benchmark's committed caches under
`benchmarks/extraction/v1/vectors/` are what make the report regenerable offline, and
`hybrid-build` writes those. The key therefore validated and did nothing, and
`ScopingConfig.cache_path` was called nowhere.

Both are **removed** rather than left as decoration. §3 stands as the design for the runtime
cache; the key arrives with the composition root that fills it (step 10). `scoping.strategy`
is the one key that stays without a consumer, because step 10 reads it and because an
unrecognised value is rejected at load — the config comment says both.

## 11.6 Smaller things

- `population.raw_variants` is carried by exactly **one** concept in the vocabulary, so the
  §2.1 ablation costs one extra vector, not a second index.
- The ablation reproduces §2.1's direction but not its exact numbers: 0.8126 → 0.8190 and
  0.8307 → 0.8491 (§2.1 has 0.8133 and 0.8485). Rank 0 both ways in both arms, as §2.1 says.
- 26 cases sit on **20 distinct passages**, so `texts.json` holds 26 entries for 32 texts.
- `PROTECTED_REASONS` had to become its own set rather than `frozenset(SCOPE_REASONS)`, and
  `CandidateScope` had to learn which vocabulary it was built with — otherwise every lexical
  case in step 8's committed report would have gained a `semantic_neighbour: 0` key and the
  "regenerates unchanged" requirement would have failed. Two step-8 tests were narrowed from
  `SCOPE_REASONS` to `PROTECTED_REASONS` for the same reason; neither was weakened.
- `CandidateScope` now **refuses** a `reason_vocabulary` narrower than the reasons it carries.
  The vocabulary was added so a zero could be keyed honestly; a vocabulary too narrow was the
  same defect in the other direction, silently dropping candidates from `counts_by_reason()`
  that were still in `concept_ids` and in `len(scope)`.
- The report's §2.1 ablation and its `top_k` sweep are both built through
  `HybridOntologyCandidateScope`'s own `ranked_for`, `select` and `merge`. Both previously
  re-implemented part of the scope inside the runner — the ablation with its own rounding
  constant, equal to the scope's by coincidence.

## 11.7 The decision's baseline was a transcribed constant, and it went stale

*(Corrected 2026-08-02, after the founder correction to
`population-portfolio-mdna-fy2023-10k`. The correction is the finding.)*

§7 criterion 1 read *"required-concept recall strictly greater than 0.959"* and the runner
computed it against `LEXICAL_REQUIRED_RECALL_BASELINE = 0.959`, a literal transcribed from §7
so "the decision block is computed against [the numbers] and cannot drift from the sentence
that describes it". That reasoning was half right. The block could not drift from the
*sentence*; both could drift from the *measurement*, together and silently.

The benchmark correction added one gold claim. Lexical's own required-concept recall moved to
**0.960** while the constant, the criterion's prose and the committed report all still said
lexical was 0.959. Hybrid measured 0.980 in the same run, so criterion 1's arithmetic would
have kept returning the same answer — the comparison would simply no longer have been *to
lexical*. It would have been to a superseded number, in the direction that flatters hybrid,
with nothing failing.

The verdict did not move (criterion 1 also requires both paraphrases recovered, and hybrid
still misses `population-our-homes-in-inventory-q1-2023`, so it held `false` before and
after). That is luck, not a safeguard.

**What changed.** Criteria 1 and 3 now compare against the lexical view computed in the same
run, which is what "improves on lexical" and "no worse than +25% over lexical" actually mean,
and both statements are rendered from those same-run figures instead of carrying hand-copied
ones. `STAGE_09_LEXICAL_RECALL = 0.959` survives as provenance, reported beside criterion 1
as `stage_09_lexical_recall_as_measured` and read by no comparison.
`LEXICAL_SCOPE_SIZE_BASELINE = 17.9` was deleted outright: criterion 3 had always computed its
ratio from the same-run lexical mean, so the constant was read by nothing and existed only as
a hand-copied number inside the criterion's prose — the §6 defect, surviving in a third place.

**The general rule this stage keeps relearning:** a constant that *describes a measurement*
goes stale the moment the measurement moves, and it goes stale silently. Transcribe a number
only when nothing can recompute it. Here, everything could.

## 11.8 The vector cache was rebuilt, and the rebuild is the proof it did not matter

*(2026-08-02, with the `announced_on` temporal-model change.)*

`cache_key` is a hash over `definition_hash | model_id | dimensions | renderer_version |
text_normalization_version`, so changing one event type's temporal requirement invalidated
the committed vectors and 25 hybrid-scoping tests went red at once. That is the cache
behaving correctly — §1.1a clause 4 says a cache whose key disagrees is rejected outright,
never partially reused.

**It was rebuilt against the running server rather than re-keyed**, and the comparison is
worth recording because it separates two things a single hash change conflates.

| | Result |
| --- | --- |
| rendered text, all 132 concepts and 26 texts | **identical**, character for character |
| `definition_hash` | `3372c5777c1d…` → `e8d4af709be2…` |
| `cache_key` | `10e1a5add28c…` → `f6af53bb252d…` |
| byte-identical concept vectors | **128 of 132** |
| worst disagreement among the other 4 | 1 − cosine = **1.544e-05**, i.e. cosine 0.999985 |
| byte-identical text vectors | **26 of 26** |
| selected candidates, all 26 cases × 3 views | **identical** |
| semantic-only additions | **identical**, all 13 rows |
| every score in every view | **unchanged** |
| verdict | `lexical stays the default`, unchanged |

Renderer `v1` composes `label | category | alias… | description` and reads no temporal field,
so no rendered string could have moved — and none did. The four vectors that differ are the
server's own non-request-independence, measured at §11.1 and bounded by clause 2's
cosine ≥ 0.9999; they clear it by two orders of magnitude and change no ranking.

**This is what the five-clause contract was written for.** Under the original claim — "two
identical requests returned bit-identical vectors" — this rebuild would have read as a
regression, and the honest response would have been to hunt a difference that is the
scheduler's rather than the ontology's. Clause 1 concedes byte identity, clause 2 bounds the
disagreement, and clause 3 asserts the thing that actually matters: selection did not move.
Only clause 3 is load-bearing for a decision, and it held exactly.

