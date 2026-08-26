"""The claims the move to `story/core/lexicon.py` had to keep true, and the one it was made for.

02-PLANNER-STABILIZATION §5 moved five closed lexicons and the scan over them out of
`story/stages/verification/language.py` so a planner-side direction check could reach
`CHANGE_DIRECTION` without `story/stages/generation/` importing `story/stages/verification/`
(`test_story_package_structure.py::test_no_stage_imports_another_stage`).

Three things had to survive it, and each has a test here rather than a note:

* every name still resolves through `language`, and to the **same object**, so no call site and
  no existing assertion changed;
* the two absences §16's read-only scan forced on `CHANGE_DIRECTION` — no `"drop"`, no
  `"contract"` — are still absences, and the file they moved into is still scanned;
* `_compile`'s boundary convention and `_CLAUSE`'s measured `\\.(?!\\d)` guard behave exactly as
  they did, because both are the reason a specific false accept was caught.

`tests/story/test_story_verification_derived.py` and `test_story_deterministic_verifier.py`
already drive these lexicons through the rules that read them; this file is about the move.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from story.core import lexicon
from story.core.numerals import CHANGE_VERBS
from story.stages.verification import language
# The §16 scan's own walk, imported rather than re-implemented: the claim below is that *that*
# function reaches the new module, and a second rglob written here would prove only itself.
from tests.story.test_story_retrieval_cypher import story_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "story" / "core" / "lexicon.py"

#: Everything that moved. Named here rather than derived from `lexicon.__all__` so a name
#: silently dropped from the new module's export list fails this file instead of passing it.
MOVED = ("CHANGE_DIRECTION", "COMPARATIVE_DIRECTION", "COMPARATIVE_TERMS", "CROSSING_TERMS",
         "NEGATION_TOKENS", "LexicalMatch", "change_direction", "change_verbs",
         "comparative_direction", "comparatives", "crossing_claims", "crossing_direction",
         "negated", "occurrences")


# -- the move changed no call site ------------------------------------------------------------


@pytest.mark.parametrize("name", MOVED)
def test_every_moved_name_still_resolves_through_the_verification_module(name):
    """`language.CHANGE_DIRECTION` and `language.change_verbs(...)` are the same objects as
    `lexicon`'s, not copies of them.

    Identity and not equality: two equal mappings in two modules is precisely the outcome §5
    rejected, and it would satisfy an `==`.
    """
    assert getattr(language, name) is getattr(lexicon, name)
    assert name in language.__all__


def test_the_verification_module_still_offers_everything_it_did():
    """`__all__` is unchanged, so a `from language import *` importer sees the same surface."""
    assert set(MOVED) <= set(language.__all__)
    for name in language.__all__:
        assert getattr(language, name, None) is not None, name


def test_the_lexicons_scan_identically_through_either_module():
    """The scanners are one implementation reached by two names, driven rather than asserted."""
    text = ("Adjusted gross profit fell to $110 million, and the GAAP gross margin sat below "
            "the adjusted gross margin, which had turned negative.")
    assert language.change_verbs(text) == lexicon.change_verbs(text)
    assert language.comparatives(text) == lexicon.comparatives(text)
    assert language.crossing_claims(text) == lexicon.crossing_claims(text)
    # `"turned"` is a `None` member — it names a sign change without saying which way — and the
    # scan still reports it, because after H1 a caller may not read `None` as "nothing to check".
    assert [m.term for m in lexicon.change_verbs(text)] == ["fell", "turned"]
    assert lexicon.change_direction("fell") is False
    assert lexicon.change_direction("turned") is None
    assert [m.term for m in lexicon.comparatives(text)] == ["sat below"]
    assert [m.term for m in lexicon.crossing_claims(text)] == ["turned negative"]


# -- the boundary the move exists to permit ---------------------------------------------------


def test_the_new_home_imports_nothing_first_party():
    """Standard library only, which is what lets a `core/` module be reached from any stage.

    A `core/` module importing a stage is caught by
    `test_core_never_imports_a_stage_a_provider_or_the_cli`; this is the stronger claim the
    move actually rests on, since the whole purpose is that *both* the generation and the
    verification stage may reach it.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {"__future__", "re", "dataclasses", "typing"}


# -- the totality assertions the lexicons exist to support ------------------------------------


def test_change_direction_is_total_over_the_numeral_layers_change_verbs():
    """The assertion `test_story_verification_derived.py` makes through `language`, at the home.

    `story/core/numerals.py` already held `CHANGE_VERBS`, and that this map is total over it was
    §5's argument for `core/` being where the vocabulary belongs: the two lists were in two
    layers before the move and are in one now.
    """
    assert set(CHANGE_VERBS) <= set(lexicon.CHANGE_DIRECTION)


def test_comparative_direction_is_total_over_the_comparative_terms():
    """A widened lexicon with an unstated polarity turns a refusal into a pass."""
    for term in lexicon.COMPARATIVE_TERMS:
        assert lexicon.comparative_direction(term) is not None, term


def test_every_crossing_phrase_states_a_polarity():
    """`crossing_direction`'s `None` can only mean *"the caller passed a phrase the scan did not
    produce"*, which is a different contract from `change_direction`'s and is total by
    construction."""
    for phrase in lexicon.CROSSING_TERMS:
        assert lexicon.crossing_direction(phrase) is not None, phrase


# -- §16's read-only scan still covers the words, and they still avoid it ----------------------


def test_the_read_only_scan_walks_the_new_module():
    """The constraint below is only a constraint while §16's scan reaches this file.

    `test_no_string_constant_anywhere_in_the_package_could_be_a_write_statement` walks
    `story_modules()`, which is `story/**.py` — `story/core/` as much as `story/stages/`. Moving
    a hundred string constants into a differently-scanned directory would have been a silent
    weakening, so the coverage is asserted rather than assumed.
    """
    assert MODULE in story_modules()


@pytest.mark.parametrize("absent, present", [("drop", "dropped"), ("drops", "declined"),
                                             ("dropping", "slipped")])
def test_the_bare_drop_inflections_stay_out_of_the_change_lexicon(absent, present):
    """Not an oversight and not a gap to fill: a three-letter `"drop"` case-folds to `DROP`.

    §16's scan reads every non-docstring string constant in `story/` against the Cypher keyword
    list, so the word cannot appear as a constant here at all. `"dropped"`, `"fell"`,
    `"declined"` and `"slipped"` cover the same claim, which is why the cost is acceptable.
    """
    assert absent not in lexicon.CHANGE_DIRECTION
    assert present in lexicon.CHANGE_DIRECTION


def test_contract_and_contracts_stay_out_for_the_second_reason():
    """`"contracts"` is a `DECLARED_AMBIGUOUS` **metric** surface in this ontology, so the verb
    reading of it would collide with a metric name; the unambiguous inflections are listed."""
    assert "contract" not in lexicon.CHANGE_DIRECTION
    assert "contracts" not in lexicon.CHANGE_DIRECTION
    assert lexicon.CHANGE_DIRECTION["contracted"] is False
    assert lexicon.CHANGE_DIRECTION["contraction"] is False


# -- the two measured behaviours the scan machinery was fixed for -----------------------------


def test_the_boundary_convention_refuses_a_term_inside_a_word_or_a_hyphenation():
    """`(?<![\\w-])…(?![\\w-])`, driven rather than read off the pattern.

    The hyphen half is what keeps *"lower-cost"* and *"non-recurring"* from being read as a
    comparative and a change verb sitting loose in a sentence.
    """
    assert lexicon.comparatives("lower") and lexicon.change_verbs("fell")
    assert lexicon.comparatives("lower-cost inventory") == ()
    assert lexicon.comparatives("flowering") == ()
    assert lexicon.change_verbs("windfall") == ()
    assert lexicon.change_verbs("re-fell") == ()


def test_longest_alternative_wins_so_a_span_covers_the_whole_construction():
    """`"below"` is inside `"sat below"` and `"came in below"`; a shortest-match scan would
    report a span highlighting half the phrase, which is what a finding points a reader at."""
    assert [m.term for m in lexicon.comparatives("margin came in below plan")] \
        == ["came in below"]
    assert [m.term for m in lexicon.crossing_claims("it swung from a profit to a loss")] \
        == ["swung from a profit to a loss"]


def test_a_decimal_point_does_not_open_a_new_clause_between_a_negation_and_its_marker():
    """R8's measured fix to `_CLAUSE`, reproduced.

    *"The GAAP Gross Margin was **not** 15.9 percentage points lower than the Adjusted Gross
    Margin."* — before `\\.(?!\\d)`, the `.` inside `15.9` opened a clause between `not` and
    `lower`, `negated()` reported `False`, and the inverted comparison passed.
    """
    text = ("The GAAP Gross Margin was not 15.9 percentage points lower than the Adjusted "
            "Gross Margin.")
    match = lexicon.comparatives(text)[0]
    assert match.term == "lower"
    assert lexicon.negated(text, match) is True

    # A real clause boundary still ends the negation's scope, which is the half that must not
    # have been widened away.
    other = "Revenue fell, and adjusted gross profit was lower than in the prior quarter"
    assert lexicon.negated(other, lexicon.comparatives(other)[0]) is False
