"""The graph pipeline stages.

Three packages, per V1_GRAPH_PROTOTYPE §11: `projection` (catalogs + ontology -> nodes and
edges, no driver), `load` (the only place `neo4j` may be imported), `verify` (post-load Cypher
assertions). Only `projection` exists at G1, which is deliberate — the graph model is tested
without a database, and a stage that does not exist cannot leak a driver import into one that
does.
"""
