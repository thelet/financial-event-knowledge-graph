"""The freshness gate's prose must not name the machine the demo runs on.

Found by the agent that wrote `app.js`, confirmed against the running server on 2026-08-05:
four of the fourteen `FreshnessCheck` rows carried an absolute path naming the operator's home
directory — `read from /mnt/c/Users/thele/Projects/FKG-story-agent-impl/data/graph_runs/…` —
into the bodies of `POST /demo/evidence-package` and `GET /demo/story-suggestions/{run_id}`.

It is the same class of leak as `provider_model_id`, which `_manifest_payload` already reduces
to a filename, and it contradicts `api.py`'s own stated rule that no response names a
filesystem location.

**Why the fix is a scrub of the rendered payload rather than an omitted field.** A
`FreshnessCheck`'s `detail`, `expected` and `observed` are free-form prose composed by the
gate — the path sits inside a sentence, so there is no field to drop and no structured seam to
intercept. Dropping the whole check would cost the reader the gate's verdict, which is the one
thing that panel exists to show.

The tests below pin both halves: the machine goes, and the run identity stays. The second half
matters as much as the first — a scrub that reduced the path to nothing would pass a
leak test and destroy `graph-v1-0483dc6b4b10`, the snapshot the entire demo is pinned to.
"""

from __future__ import annotations

import re

import pytest

from story.demo_ui.api import _scrub_paths

#: Any absolute path whose second segment is a user directory. Deliberately broader than the
#: scrubber's own pattern: a test that reuses the implementation's regex proves only that the
#: function is self-consistent.
OPERATOR_PATH = re.compile(r"/(?:mnt|home|Users|var|opt|srv)/[A-Za-z0-9_.-]+/[^\s\"',]{3,}")

REAL_DETAILS = (
    "read from /mnt/c/Users/thele/Projects/FKG-story-agent-impl/data/graph_runs/"
    "graph-v1-0483dc6b4b10; projection 1.2.0",
    "/mnt/c/Users/thele/Projects/FKG-story-agent-impl/data/extraction_runs/"
    "extract-v1-lexical-833f7bcfbce9",
    "sha256(/mnt/c/Users/thele/Projects/FKG-story-agent-impl/data/extraction_runs/"
    "extract-v1-lexical-833f7bcfbce9/run.complete)",
    "10 files listed by run.complete under /home/operator/fkg/data/extraction_runs/"
    "extract-v1-lexical-833f7bcfbce9, each hashed",
)


@pytest.mark.parametrize("detail", REAL_DETAILS)
def test_no_operator_directory_survives_a_scrub(detail: str) -> None:
    assert OPERATOR_PATH.search(detail), "the fixture must contain what the scrub removes"
    assert not OPERATOR_PATH.search(_scrub_paths(detail))


@pytest.mark.parametrize(
    "detail, kept",
    [(REAL_DETAILS[0], "graph_runs/graph-v1-0483dc6b4b10"),
     (REAL_DETAILS[1], "extraction_runs/extract-v1-lexical-833f7bcfbce9"),
     (REAL_DETAILS[2], "extract-v1-lexical-833f7bcfbce9/run.complete")])
def test_the_run_identity_survives_the_scrub(detail: str, kept: str) -> None:
    """The half a leak test alone would let regress to `...`."""
    assert kept in _scrub_paths(detail)


def test_the_prose_around_a_path_is_untouched() -> None:
    scrubbed = _scrub_paths(REAL_DETAILS[0])
    assert scrubbed.startswith("read from ")
    assert scrubbed.endswith("; projection 1.2.0")


def test_a_scrub_reaches_through_the_nesting_a_freshness_report_actually_has() -> None:
    """`model_dump` gives a mapping of lists of mappings; a string-only scrub would miss it."""
    report = {"graph_run_id": "graph-v1-0483dc6b4b10",
              "checks": [{"name": "graph_manifest", "detail": REAL_DETAILS[0], "passed": True},
                         {"name": "extraction_run_directory", "detail": REAL_DETAILS[1]}]}
    scrubbed = _scrub_paths(report)
    assert not OPERATOR_PATH.search(repr(scrubbed))
    assert scrubbed["graph_run_id"] == "graph-v1-0483dc6b4b10"
    assert scrubbed["checks"][0]["passed"] is True, "non-string values must pass through intact"


def test_a_string_with_no_path_is_returned_unchanged() -> None:
    plain = "the distinct graph_run_id over every loaded node; more than one means two runs"
    assert _scrub_paths(plain) == plain


def test_both_response_paths_route_through_the_scrub() -> None:
    """Structural, because the two leaking bodies are built in two different functions.

    A future third dump site is the way this regresses, so the assertion is on the source: no
    `freshness` value may be a bare `model_dump` call.
    """
    import inspect

    from story.demo_ui import api

    source = inspect.getsource(api)
    bare = re.findall(r'"freshness":\s*(?!_scrub_paths)[A-Za-z_][\w.]*\.model_dump', source)
    assert bare == [], f"a freshness payload bypasses the scrub: {bare}"
