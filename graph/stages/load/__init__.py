"""The only stage that may hold a database connection (V1_GRAPH_PROTOTYPE §11).

`graph/core/` and `graph/stages/projection/` are database-free by rule and by test: the whole
graph model is checked with no server running, and the export is the artifact both the loader
and the determinism check read. Everything that knows what Bolt is lives here.

Six modules, and no re-export of any of them from this file — importing `graph.stages.load`
must not be enough to drag a driver in:

    connection.py   settings, `.env`, and the single place `neo4j.GraphDatabase.driver` is called
    schema.py       §5.2's uniqueness constraints — seven on node keys, twelve on relationship
                    `edge_key` — and the §5.3 indexes
    reader.py       strict typed reading of nodes.jsonl / edges.jsonl, refusing anything Neo4j
                    cannot store *before* a row is sent
    loader.py       batched, parameterized loading, with the whole-load identity check and the
                    per-batch submitted-versus-written comparison that together turn Cypher's
                    silent `MATCH` filtering and silent `MERGE` collapsing into failures
    lifecycle.py    which run the database holds, whether it may be replaced, and the completion
                    metadata that is written only after verification succeeds
    verification.py the §10 sweep — reconciling what the database holds against what the export
                    says, check by named check

The list is stated rather than derived because it is the boundary itself: a seventh module here
is a deliberate decision, and one *outside* here that imports a driver fails
`test_the_driver_is_named_only_inside_the_load_stage`.

The report those checks produce lives in `graph/core/verification_report.py`, deliberately on
the other side of the boundary: a result you can build, compare and assert with no server is
what lets the verifier's own failure modes be tested offline.
"""
