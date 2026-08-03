# Evidence-contract fixture — **synthetic, and deliberately so**

Unlike `tests/fixtures/graph/`, **these rows are not copied from a run.** No lane emits an
`xbrl_fact`, a `market_data` or a `calculated` reference yet — F0 extends the contract and
ingests nothing (`plans/factual-spine/F0_CONTRACT_EXTENSION.md` §2.3) — so the only way to
prove the contract is to write the rows the future lanes will write and hold the writer, the
readers and the validator to them.

Nothing here is evidence of anything Opendoor said. Two fields are nevertheless real, because
a fixture that used `accession-1` would not catch a reader that accepted any string: the
accession `0001801169-21-000021` and its `source_url` are the real coordinates of Opendoor's
Q1 2021 10-Q, taken from `tests/fixtures/graph/normalization_catalog/documents.jsonl`. The
XBRL concept is a real US-GAAP element name. **The `market_data` values are invented** — no
provider has been chosen, and inventing a plausible provider name is exactly the kind of thing
that should stay obviously fake.

## Files

| File | What it holds | What it proves |
| --- | --- | --- |
| `valid_rows.jsonl` | one `evidence.jsonl` row per evidence kind | every declared kind round-trips writer → reader → `EvidenceReference` → validator with no finding |
| `invalid_references.json` | references the validator must refuse, each with the codes it must emit | the contract refuses by name, not by silence |
| `invalid_rows.json` | catalog rows the reader must refuse | a fabricated `passage_id` on a non-passage kind fails at *parse* time, because that row shape has no such column |

`valid_rows.jsonl` is keyed by `(claim_id, evidence_index)`, the catalog identity F0 Part B
moved evidence to; the claim ids are named after the kind so a failure message says which case
broke.

## The rule these files exist to hold

A `passage_id` may not appear on a kind that names no passage. It is refused three times over,
on purpose: the row shape has no such column (`graph.core.inputs`), the writer never writes one
(`jsonl_catalog._EVIDENCE_ROW_FIELDS`), and the validator names it
(`EVIDENCE_PASSAGE_ON_NON_PASSAGE_KIND`). One check would be enough if nothing downstream ever
constructed a reference by hand; three are cheap and the failure mode — a market price
presented as something a filing said — is not recoverable once it is in the graph.
