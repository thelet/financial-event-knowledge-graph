"""Shared graph-layer meaning: what a node is, what the inputs are, how things are keyed.

`core` carries application meaning, not generic helpers (the repository's rule for the
difference). Six modules, each a concern that stands on its own:

    models.py      GraphNode / GraphEdge / GraphExport and the declared export order
    inputs.py      typed readers over the seven extraction catalogs and the corpus subset
    keys.py        node and edge key policy, including §4.2's unresolved-entity rule
    derivation.py  the observation-id recomputation and the warning re-derivation
    citations.py   which passages this run cites, and the one empty-passage_id policy
    manifest.py    the graph run id, its content digest, and the run manifest

`keys.py` is separate from `models.py` because §4.2's policy is the piece most likely to be
argued about and it deserves its own tests. `citations.py` is here rather than in a stage
because **both** projection stages need the answer and two copies of it had already drifted
apart — see its docstring for the table of how.
"""
