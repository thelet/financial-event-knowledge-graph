"""The only stage that may hold a database connection (V1_GRAPH_PROTOTYPE §11).

`graph/core/` and `graph/stages/projection/` are database-free by rule and by test: the whole
graph model is checked with no server running, and the export is the artifact both the loader
and the determinism check read. Everything that knows what Bolt is lives here.

Two modules at this checkpoint, and no re-export of them from this file — importing
`graph.stages.load` must not be enough to drag a driver in:

    connection.py   settings, `.env`, and the single place `neo4j.GraphDatabase.driver` is called
    schema.py       the seven §5.2 uniqueness constraints and the §5.3 indexes
"""
