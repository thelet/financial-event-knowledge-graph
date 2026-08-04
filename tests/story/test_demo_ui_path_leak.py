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

from story.demo_ui.api import URI_PLACEHOLDER, _scrub_paths

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


# ---------------------------------------------------------------------------------------
# The second and third leaks, found 2026-08-05 by an adversarial review of the shipped fix.
#
# The scrub above was written, committed, and still left two holes: it never reached the
# *success* path of `GET /demo/story-suggestions/{run_id}`, and its regex structurally could
# not match a URI authority. Both were reproduced against the running server before these
# tests were written.
# ---------------------------------------------------------------------------------------

#: Verbatim from `graph_reachable`'s `detail` on the running server, 2026-08-05.
REACHABLE_DETAIL = "bolt://localhost:7687 database=neo4j"

#: Any URI authority. Like `OPERATOR_PATH`, deliberately not the implementation's own regex.
URI_AUTHORITY = re.compile(r"://(?!<)[^\s\"',;)]+")


def test_a_database_uri_does_not_survive_a_scrub() -> None:
    """`_ABSOLUTE_PATH` cannot match `//localhost` — its first segment would be empty.

    So the one check that names the database *by address* passed straight through a function
    whose whole job is to stop a response naming the operator's machine. A bolt URI with a
    host and a port is exactly what the no-secrets rule exists for.
    """
    assert URI_AUTHORITY.search(REACHABLE_DETAIL), "the fixture must contain what is removed"
    scrubbed = _scrub_paths(REACHABLE_DETAIL)
    assert "localhost" not in scrubbed
    assert "7687" not in scrubbed
    assert not URI_AUTHORITY.search(scrubbed)


def test_the_scheme_and_the_database_name_survive_the_uri_scrub() -> None:
    """The half a leak test alone would let regress to `...`, one level down.

    `bolt` says which protocol the gate reached the graph over and `database=neo4j` is the
    pipeline's own configuration; neither names the machine. Destroying them would make
    `graph_reachable` a check whose result cannot be read.
    """
    scrubbed = _scrub_paths(REACHABLE_DETAIL)
    assert scrubbed == f"bolt{URI_PLACEHOLDER} database=neo4j"


@pytest.mark.parametrize("detail", [
    "neo4j://neo4j.internal:7687 database=neo4j",
    "the model server at http://127.0.0.1:8080/v1/chat/completions did not answer",
    "https://user:pass@example.invalid/x refused",
])
def test_every_uri_scheme_is_reduced_not_only_bolt(detail: str) -> None:
    scrubbed = _scrub_paths(detail)
    assert not URI_AUTHORITY.search(scrubbed)
    assert "127.0.0.1" not in scrubbed and "8080" not in scrubbed
    assert "pass@" not in scrubbed


def test_the_discovery_success_path_is_scrubbed_and_not_only_its_failure_branch() -> None:
    """Structural, and aimed at the exact regression that happened.

    The first fix scrubbed `StaleGraphRefused`'s report and the package payload, and left
    `DiscoveryResult.as_dict()` alone — so a run that *passed* the gate returned four
    `freshness.checks[].detail` strings naming the operator's home directory. The refused
    branch was the rare one; the leak was on the ordinary one.
    """
    import inspect

    from story.demo_ui import api

    source = inspect.getsource(api.discovery_result)
    assert "_scrub_paths(result.as_dict())" in source, (
        "the discovery success payload must be scrubbed whole; scrubbing only its freshness "
        "block is how the next field that carries a path leaks silently")


def test_a_freshness_report_carrying_both_leaks_survives_neither() -> None:
    """The two rules compose. A payload holding a path *and* a URI must lose both."""
    report = {
        "graph_run_id": "graph-v1-0483dc6b4b10",
        "passed": True,
        "checks": [
            {"name": "graph_manifest", "detail": REAL_DETAILS[0]},
            {"name": "graph_reachable", "detail": REACHABLE_DETAIL},
        ],
    }
    scrubbed = _scrub_paths(report)
    rendered = repr(scrubbed)
    assert not OPERATOR_PATH.search(rendered)
    assert not URI_AUTHORITY.search(rendered)
    assert "graph-v1-0483dc6b4b10" in rendered
    assert "database=neo4j" in rendered
