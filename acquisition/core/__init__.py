"""Shared application infrastructure.

Canonical models, identity rules, artifact classification, configuration, SEC HTTP access,
storage with atomic finalization, manifest persistence, and run provenance.

Depends on nothing above it: core must not import stages, the pipeline, or the CLI.
"""
