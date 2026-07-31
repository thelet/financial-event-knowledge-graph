"""Generic stateless helpers.

Strictly for helpers that own no application state, represent no domain concept, and
belong to no single stage. Application services -- storage, configuration, manifests, the
SEC client -- live in `core/`, not here.
"""
