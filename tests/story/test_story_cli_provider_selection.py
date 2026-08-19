"""When `python -m story demo` judges the provider it was asked for.

One claim, and it is about **order**: an unusable `--provider`/`--model` pair is refused before
the freshness gate, the detectors and the packaging pass — roughly thirty seconds of work — run.

The demo UI already settles this the same way and says so in `start_generation`: *"Before the
graph is read and before anything is composed: an unknown pair is a 400 the client can fix, and
spending a packaging pass to reach the same answer would make a typo cost the same as a run."*
The command line had the opposite order until 2026-08-19, so a mistyped provider cost a full
run's worth of reading before saying the one thing it knew at argument-parse time.

Offline: the graph half is stubbed at the two seams `cmd_demo` uses, and the point of every
test here is precisely that the stubs are *not reached*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from story import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_ID = ("cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
                "opendoor:2022Q3")


@pytest.fixture
def graph_reads(monkeypatch) -> list[str]:
    """Records every step `cmd_demo` takes into the graph, and refuses to do any of them."""
    reached: list[str] = []

    def context(*args, **kwargs):
        reached.append("build_story_context")
        raise AssertionError("the graph was opened for a run that cannot be built")

    def inputs(*args, **kwargs):
        reached.append("resolve_demo_inputs")
        raise AssertionError("the freshness gate ran for a run that cannot be built")

    monkeypatch.setattr(cli, "build_story_context", context)
    monkeypatch.setattr(cli, "resolve_demo_inputs", inputs)
    return reached


def test_a_provider_this_build_does_not_have_is_refused_before_the_graph_is_opened(
        graph_reads, capsys):
    """`--provider anthropic` spent the freshness gate, detection and packaging first."""
    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID,
                     "--provider", "anthropic"])

    assert code == cli.EXIT_FAILED
    assert graph_reads == [], "the pair is knowable at parse time; nothing may be read first"
    err = capsys.readouterr().err
    assert "anthropic" in err and "provider.kind" in err


def test_a_model_the_selected_provider_does_not_declare_is_refused_the_same_way(
        graph_reads, capsys):
    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID,
                     "--provider", "openai", "--model", "gpt-4o-mini"])

    assert code == cli.EXIT_FAILED
    assert graph_reads == []
    assert "gpt-4o-mini" in capsys.readouterr().err


def test_a_provider_with_no_recorded_store_is_refused_before_the_graph_is_opened(
        graph_reads, capsys):
    """The other pre-run refusal, and the one the shipped configuration really produces.

    `demo.generation_stores` names no OpenAI store deliberately (MULTI_PROVIDER_OPENAI §10.2),
    so a replay run against OpenAI cannot start — and that is knowable from configuration alone,
    which makes it the same class of answer as an unknown provider id.
    """
    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID,
                     "--provider", "openai"])

    assert code == cli.EXIT_FAILED
    assert graph_reads == []
    err = capsys.readouterr().err
    assert "openai" in err and "--live" in err


def test_a_usable_selection_still_reaches_the_graph(monkeypatch, capsys):
    """The guard on the guard: moving the check earlier must not stop the run happening.

    A refusal raised from `resolve_demo_inputs` is the proof that the provider was built, the
    context was opened and the gate was reached — in that order.
    """
    class Closable:
        def close(self) -> None:
            self.closed = True

    reached: list[str] = []
    monkeypatch.setattr(cli, "build_story_context",
                        lambda *a, **k: (reached.append("context"), Closable())[1])

    def inputs(*args, **kwargs):
        reached.append("inputs")
        raise cli.StoryDemoError("stopped here on purpose")

    monkeypatch.setattr(cli, "resolve_demo_inputs", inputs)

    code = cli.main(["--root", str(REPO_ROOT), "demo", "--candidate-id", CANDIDATE_ID])

    assert code == cli.EXIT_FAILED
    assert reached == ["context", "inputs"]
    assert "stopped here on purpose" in capsys.readouterr().err
