# F0 — handoff (superseded)

**F0 is complete and committed.** This file previously carried a mid-implementation state dump
written when a session ran out of context; every statement in it — "nothing is committed", "Part E
not started" — is now false and has been removed rather than left to mislead.

Read instead:

| Document | What it holds |
| --- | --- |
| [F0_IMPLEMENTATION_REPORT.md](F0_IMPLEMENTATION_REPORT.md) | what was built, what it measured, the defects found, the versions moved, and the one founder decision outstanding |
| [F0_CONTRACT_EXTENSION.md](F0_CONTRACT_EXTENSION.md) | the F0 spec it was built against |
| [V1_OPENDOOR_FACTUAL_SPINE.md](V1_OPENDOOR_FACTUAL_SPINE.md) | the phase plan F0 is the first stage of |
| `f0_audit_artifacts/` | the before-snapshot and the audit harness that proved the correction |

Two process lessons worth carrying into F1, both paid for here:

1. **Do not run parallel agents in one working tree.** Five did. One stashed another's in-flight
   edits; a third's changes were reverted and had to be re-applied. Use worktree isolation, or
   run them in sequence.
2. **A derived artifact keyed on the ontology hash will refuse to load after a vocabulary
   change, and that is the system working.** `OntologyMismatchError` and `StaleVectorCacheError`
   both fired. They are cleared by regenerating in dependency order — vectors, extraction,
   reports, fixture slice, projection — never by relaxing the check. The order is in the
   implementation report §6.
