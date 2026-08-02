# The lexical-versus-hybrid scoping decision

**Default: `lexical`. Strength: weak.**

The two scopes issue identical request digests on 13 of 14 narrative cases and on 3 of 3 event cases — the event lane takes no scope at all — so the whole extraction comparison rests on 1 of 17 reviewed cases. A decision resting on one case is a weak decision whichever way it goes.

Made from the four committed evaluation reports and from nothing else — no fresh benchmark run, no gold annotation reaching a runtime decision, and no number this file computes for itself. The pipeline does not and cannot make this comparison: four AST guards forbid anything under `extraction/` from reaching these reports.

## The width of the comparison, beside its result

|  | Value |
| --- | --- |
| reviewed cases that could distinguish the scopes at all | 1 |
| reviewed cases compared | 17 |
| required concepts that distinguish the scopes | 1 |
| required concepts compared | 50 |

## Reachability

From the committed scope reports. Reachability is what step 9 measured and is *not* extraction quality — a concept the scope reaches is a concept the lane may then get wrong.

|  | Value |
| --- | --- |
| denominator | 50 |
| hybrid required concept recall | 0.98 |
| hybrid scope size mean | 18.461538 |
| hybrid stage 09 criterion 1 holds | **no** |
| lexical only report recall | 0.96 |
| lexical required concept recall | 0.96 |
| lexical scope size mean | 17.923077 |

## Metric extraction

From the narrative lane report, both scopes.

|  | Value |
| --- | --- |
| cases | 14 |
| cases with a difference | 1 |
| cases with identical scope | 13 |
| cases without a request | 1 |
| gold claims added clean | 0 |
| gold claims added not clean | 1 |
| gold claims only hybrid reached | 1 |
| gold claims only lexical reached | 0 |
| per claim regressions | 0 |
| verdict recorded at step 11 | hybrid reaches more and is not clean on what it reaches |

### The cases where the two scopes differ at all

| Case | Concepts only hybrid | Claims only hybrid | …of which gold | Claims only lexical |
| --- | --- | --- | --- | --- |
| letter-prose-inventory-and-120d-q2-2022 | pct_homes_on_market_gt_120_days | pct_homes_on_market_gt_120_days@2022-06-30 | pct_homes_on_market_gt_120_days@2022-06-30 | — |

## Event and relationship extraction

The event lane takes no candidate scope: all nineteen declared event types carry no alias, and the lane offers the declared category entire. Both scopes were run anyway, because asserting "the scope makes no difference" without running the second scope would be asserting the design rather than the result.

|  | Value |
| --- | --- |
| cases | 3 |
| cases with identical payloads | 3 |
| cases with identical requests | 3 |
| gold event types reached by any scope | 0 |
| score deltas that are nonzero | none |

## Corpus cost

The hybrid scope ranks a passage against concept vectors, so a corpus-scale run needs an embedding of every candidate passage. This repository commits the reviewed cases' vectors and no corpus-scale cache, and `config/extraction.yaml` deliberately declares no cache root for one. Hybrid's corpus cost is therefore unmeasured, and stated as unmeasured rather than estimated.

|  | Value |
| --- | --- |
| committed text vectors | 26 |
| corpus passages | 12442 |
| hybrid runs offline | **no** |
| lexical runs offline | yes |

## The four conditions, answered

Hybrid becomes the default only if criteria 1 and 3 both hold. 2 and 4 are recorded because they are what a reader would otherwise assume.

| Criterion | Holds | Statement |
| --- | --- | --- |
| adds_a_clean_gold_claim | **no** | hybrid reaches at least one gold claim that lexical misses and is clean on every scored dimension |
| no_per_claim_regression | yes | no claim is clean under lexical and not clean under hybrid |
| comparison_is_wider_than_one_case | **no** | more than one reviewed case can distinguish the two scopes at all |
| corpus_cost_is_reproducible_offline | **no** | hybrid's corpus-scale cost can be measured from committed artifacts, with no server |

## What this leaves open

- Population wording is the dimension the one added claim fails, and whether gold's whole clause or the lane's noun phrase is right is a founder decision that would change this verdict if it went the other way.
- The second paraphrase probe is unreached under *both* scopes because the concept sits at rank 2 and `top_k` is 2. Raising `top_k` to 3 takes required-concept recall to 1.000 in the committed sensitivity sweep and is not tested against extraction quality at all.
- Hybrid's corpus-scale cost is unmeasured. Measuring it needs an embedding cache this repository does not commit, which is a build decision rather than a scoring one.

## Provenance

The commit below is the only field permitted to move between two regenerations of this report; everything else is a function of the four committed reports it reads.

|  | Value |
| --- | --- |
| implementation commit | `14f3ad64a7383ae4ee657701d344c7fc3461ce61` |
| ontology definition hash | `e8d4af709be275c679210bbebe174354f52e621f0363566e928fad47945ba8bc` |
| source `event_relationship_v1.json` | `a986321833afcfc4f264602a34507c92ec48fb3e` |
| source `hybrid_scope_v1.json` | `a986321833afcfc4f264602a34507c92ec48fb3e` |
| source `lexical_scope_v1.json` | `a986321833afcfc4f264602a34507c92ec48fb3e` |
| source `narrative_lane_v1.json` | `a986321833afcfc4f264602a34507c92ec48fb3e` |
