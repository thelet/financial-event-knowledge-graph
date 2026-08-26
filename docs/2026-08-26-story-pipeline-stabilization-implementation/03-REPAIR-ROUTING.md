# 03 — Repair routing

*Verified 2026-08-26.*

---

## 1. Ownership is a table, not a judgment

`story/stages/generation/repair.py`. Five owners; `ROUTING` maps each code to exactly one, and
**a code absent from the table routes to `CODE_OWNED`** — the safe direction, because an
unrouted refusal reaches a person rather than a model.

| Owner | Route |
| --- | --- |
| `PLANNER` | one further planner call with feedback |
| `WRITER_STRUCTURAL` | one further writer call with feedback |
| `WRITER_PROSE` | one further writer call with feedback |
| `EVIDENCE` | **no repair** — the package does not hold what the claim needs |
| `CODE_OWNED` | **no repair** — code chose the thing being refused; the run is rejected so the defect stays visible |

`repairable_owner(codes)` returns `None` — no repair at all — when a refusal carries *any*
`CODE_OWNED` or `EVIDENCE` code, or when it carries **two different repairable owners**. Mixing
them would send a writer a prompt containing a defect code chose; two repairable owners are two
stages' work and a bound of one does not allow both.

## 2. Bounds

| Bound | Value | Why |
| --- | --- | --- |
| planner repairs | **1** | the failure it addresses is one wrong claim with an exact correction |
| writer structural repairs | **1** | same |
| writer prose repairs | **1** | same |
| on a `provider_failed` run | **0** | the adapter already exhausted its own `max_retries: 2` |
| on a `CODE_OWNED`/`EVIDENCE` finding | **0** | — |

**A bound of one makes repair a straight-line second attempt rather than a loop**, so it cannot
oscillate and needs no convergence argument.

The bounds live in `config/story.yaml` (`generation.max_planner_repairs`,
`max_writer_repairs`), **not in code**. `story_run_id` has seventeen inputs and the number of
generations is not among them, so two runs of one candidate — one repaired, one not — would mint
the same id and therefore the same directory, and finalisation is `os.replace`. `config_hash`
*is* a run-id input. This is the same argument `_mint_run_id` records for `length_target`.

## 3. There are no blind retries

Every repair changes the **prompt**. Measured across the stored corpus, live-to-live on an
identical `request_sha256`: the local llama.cpp server returned byte-identical answers **4 times
in 4**; the OpenAI adapter returned different ones **2 times in 2**, because it records
`temperature: 0.0` and sends `temperature_sent: false` — the field never reaches the wire for
those models. So re-asking an unchanged prompt has no mechanism of action locally and unknown
odds remotely.

**An empty `feedback` renders byte-identically to no feedback at all** (`_feedback_lines`
returns `[]`). That is load-bearing: `prompt` is a `request_identity` digest input, so a first
attempt that carried an empty heading would key differently from every generation already
recorded, and the whole replay store would miss for a feature that had not fired.

## 4. The three payloads

* **Planner** — the verified pair, the verified direction, the refusal's own sentence, and
  *"rewrite the thesis and any key point that states the direction; name the same facts"*. No
  trace, no manifest, no draft.
* **Writer structural** — what went wrong, plus **every slot this run offers**, handle to
  string, so the prompt says what a correct sentence may contain rather than only what the last
  one did wrong.
* **Writer prose** — `VerificationFinding` used as it stands: code, sentence text, span,
  `expected`, `observed`, and the eleven-member `Remedy`. **Nothing is derived.** Capped at five
  findings — a draft with twenty is one to reject, and a repair prompt longer than the original
  has stopped being feedback.

## 5. Provenance

`StoryRunManifest.call_sites: list[dict]` — one row per model call, in call order: `stage`,
`attempt`, `outcome`, `codes`, `schema_name`, `answered`.

It sits **beside** the two named provenance blocks rather than replacing them, and it carries
**no `provider_model_id` and no path**. That is deliberate: `demo_ui.api.PROVIDER_MODEL_BLOCKS`
reduces a block's `provider_model_id` to a basename by name, and a list carrying one would need
that redaction rewritten or it would put an operator's absolute weights path in the browser — a
defect that already shipped once. `test_no_response_carries_an_absolute_path` still passes.

`generations.jsonl` needed no change: the store is a digest-keyed dict with no ordering or
cardinality assumption, and a repair prompt keys its own row automatically.

## 6. What repair actually did, live

Over 24 live runs across two providers, repair fired on roughly half of the refused ones and
converted none of them by itself — the accepted runs were accepted on the **first** attempt.

That is worth stating plainly rather than dressing up. The reading is that Phases 1–3 removed
the failures repair was designed for: recovery makes the slot grammar optional, so the
structural failures that killed both original live runs no longer happen, and what is left is
prose the model gets wrong in ways one round of feedback does not fix. Repair is bounded, it is
recorded, it never routes a code-owned defect to a model, and on this corpus it is not yet
earning its cost.

## 7. `MissingGenerationError`

It is a `LookupError`, not a `StoryProviderError`, and `run_demo` does not catch it — so the
first repair call on a *replayed* run escapes and writes no directory. Repair is therefore only
exercised on the live path in this phase, and the replay fixtures carry no repair rows. Catching
it and turning it into a disposition is the first thing to do before repair is enabled for
replay.
