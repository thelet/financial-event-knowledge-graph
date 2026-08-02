# Stage 13 → graph: the input handoff checklist

Companion to [V1_GRAPH_PROTOTYPE.md](V1_GRAPH_PROTOTYPE.md). This is the checklist stage **G0**
works through once extraction step 13 lands and is committed.

**Why it exists.** The graph layer's entire input surface — the run directory, the catalogs,
the manifest — is produced by step 13, which was still in progress when the graph plan was
written *(verified 2026-08-02 at `a986321`: `data/extraction_runs/` does not exist,
`extraction/` has no `catalog` stage, no `context.py`, no `pipeline.py`, no `cli.py`)*. The
payload *shapes* are firm because they are frozen pydantic models. The *files* are not.

Nothing here asks the extraction session to change anything. Each item is a question to answer
by reading the finalized run, and a note on what the graph does if the answer differs from the
working assumption.

---

## A. Firm already — do not re-derive, just confirm the run agrees

| Fact | Source | Confirm by |
| --- | --- | --- |
| `OntologyClaim`, `MetricObservation`, `EventInstance`, `RelationshipInstance`, `EvidenceReference`, `Population` field sets | `ontology/core/models.py:333-476`, all `extra="forbid"` | a round-trip: every catalog row parses back into its model with no extra and no missing field |
| Id grammars — `obs:`, `claim:`, `evt:`, `rel:` | `extraction/core/identifiers.py` | ids in the run match the grammar; no UUIDs |
| Predicate vocabulary (30) and endpoint types | `definitions/relationships.yaml` | every `relationship_id` in the run is declared |
| Event types (19), participants, `allowed_properties`, temporal requirements | `definitions/events.yaml` | every `event_type_id` and every `properties` key is declared |
| Ontology `definition_hash` `e8d4af709be2…` | manifest | matches the run manifest; if it moved, the vocabulary changed and §3.2 of the plan is re-derived |
| Passage / document catalog schema (**31** / 35 fields) | `data/normalization_catalog/*.jsonl` — untracked (`.gitignore:3`), so measured against the working corpus on 2026-08-02, not against a commit | unchanged since the normalization run the extraction manifest names |
| Evidence anchors are passage ids that resolve; no sub-passage anchors | `V1_CLAIM_EXTRACTION` §8a.11 | every `passage_id` in the run resolves in `passages.jsonl` |
| Announcement and occurrence are separate, neither inferred | §4.0b, `models.py:409-431` | events exist in the run with `occurred_on: null` and an `announced_on` |

---

## B. Pending — the eleven questions G0 must answer

Each carries the graph plan's working assumption and the cost of being wrong.

| # | Question | Working assumption | If different |
| --- | --- | --- | --- |
| **P1** | Run directory and catalog filenames | `data/extraction_runs/<run_id>/{claims,observations,evidence,issues}.jsonl` | reader paths only |
| **P2** | Do events and relationships live in `claims.jsonl` or separate files? | one `claims.jsonl` for all three `claim_kind`s | reader only; the projection dispatches on `claim_kind` either way |
| **P3** | Row shape — serialized `OntologyClaim`, or flattened? | `claim.model_dump()` with sorted keys | if flattened, `core/inputs.py` grows a mapping layer; nothing downstream changes |
| **P4** | Is `observations.jsonl` authoritative or a derived index of `claims.jsonl`? | derived; **claims are authoritative** | if observations carry fields claims do not, the projection reads both and G0 records which wins |
| **P5** | What is an `issues.jsonl` row? Abstentions, policy rejections, deferrals, validation warnings — one file or several? Does it carry an id? | one file; each row has a `code`, a discriminator, and a `passage_id` where one applies; **no id** (the projection mints one, plan §4.3) | if Stage 13 mints ids, adopt them and delete the minting rule rather than keeping both |
| **P6** | Is validation state recorded **per claim**, or only at run level? | per `claim_id`, listing warning codes | if run-level only, `validation_state`/`:Warned` degrade to a manifest note and query Q9 loses its per-observation slice. **This is the one pending item that changes an acceptance criterion** (plan §14.2) |
| **P7** | Which `extractor_metadata` keys reach the catalog? | at least `ambiguity_codes`, `scale`, `scale_location`, `row_label`, `column_label`; narrative adds all four of `V1_CLAIM_EXTRACTION` §8a.12's period fields — `period_label`, `period_label_char_start`, `period_label_in_evidence`, `period_label_distance_from_evidence` | the whitelist in plan §5.4 is edited; unknown keys still survive in `extractor_metadata_json`, so nothing is lost either way |
| **P8** | Manifest filename and contents | `extraction_manifests/<run_id>.json` (from `config/extraction.yaml` → `paths.manifests_root`), carrying run id, created/finished, config hash, code commit, ontology `definition_hash`, the normalization run id consumed, `scoping.strategy`, and counts | any missing field becomes a gap in the graph manifest's provenance and is named as such rather than filled in |
| **P9** | The step-13 verdict: `scoping.strategy` = `lexical` or `hybrid`? | recorded from the manifest, never assumed | provenance only — the projection does not branch on it |
| **P10** | Are deferred metrics and policy-rejected claims written at all? | yes, into `issues.jsonl` | if absent, acceptance criterion 5's "`:Issue` count equals `issues.jsonl` rows" is restated against whatever exists, and the gap is recorded |
| **P11** | Real counts — observations, events, relationships, distinct cited passages and documents | unknown. Reference points: the table lane emitted 410 observations over 11 benchmark cases; selection produced 503 table and 3,072 narrative candidates | sets the Browser limits and the §7.4 hairball rules; a surprise here is recorded at G3, not designed around in advance |

---

## C. Things the graph needs that step 13 might not produce

Not requests — observations, so that if any of them is missing the graph records the gap
instead of inventing a substitute.

1. **A stable `run_id`** matching the other layers' `{UTC}Z-{config_hash[:8]}` shape
   (`normalization/core/runmeta.py:24-26`). The graph manifest records the extraction run id;
   an unstable or absent one makes provenance untraceable.
2. **Byte-identical catalogs across two runs.** `V1_CLAIM_EXTRACTION` criterion 4 requires it.
   The graph's own determinism check (plan §6.5) is meaningless if its input is not stable.
3. **Atomic finalization** — a completion marker written last, so a partial extraction run is
   unambiguously incomplete. The graph loader must be able to refuse an unfinished run rather
   than load half of it.
4. **`named: false` participants preserved into the catalog.** `LaneEventParticipant.named`
   exists on the lane model but `EventParticipantRef` (`ontology/core/models.py:401-406`) has
   **no `named` field** — so the flag survives into the graph only if the placeholder id shape
   (`*_unnamed_*`) or `extractor_metadata` carries it. The projection can detect the placeholder
   by shape, which is exactly what the extraction docstring says the uniform placeholder is
   for; G0 confirms that this is the intended contract and not an accident.
5. **`entity_text` for participants.** Same shape of question: it is on `LaneEventParticipant`
   and not on `EventParticipantRef`. Without it, an unresolved node has no printed description
   to show, and plan §7.2's `:Unresolved` caption falls back to the placeholder id. Worth
   knowing before G1 rather than discovering in Browser.

Items 4 and 5 are the two most likely to bite, because both are lane-model fields that the
ontology payload does not declare. Neither is a defect — the ontology payload is deliberately
narrow — but both decide what an unresolved participant looks like on screen.

---

## D. What G0 produces

1. Every row of §B answered, in this file, with the date and the file or command that answered
   it.
2. A committed fixture under `tests/fixtures/graph/`: a handful of claims covering all three
   `claim_kind`s (including one event dated only by `announced_on` and one with an unnamed
   participant), their passages and documents, an issues sample, and the manifest. Small enough
   to read, real enough to project. Copied from the **committed** Stage 13 output, never from an
   uncommitted working tree.
3. Corrections applied to `V1_GRAPH_PROTOTYPE.md` wherever the finalized run contradicts it,
   each with the reason — the repository's standing rule, and the reason this checklist is a
   document rather than a conversation.
