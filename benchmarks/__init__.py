"""Benchmarks. Deliberately outside `extraction/`.

Runtime extraction code may not import anything from here — an executable test
(`tests/extraction/test_typed_selection.py::test_runtime_selection_never_imports_the_benchmark`)
parses every module under `extraction/` and fails on a benchmark import or path literal. The
dependency runs one way only: a benchmark composes the pipeline, never the reverse.
"""
