"""The story agent's pipeline stages, each independently runnable.

Holds nothing itself. A stage lives in its own subpackage, imports no other stage
(`tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage`), and
receives everything external as an argument — the `ReadQueryExecutor` above all, which is why
no stage constructs a driver and every stage is testable with no server running (D1).
"""
