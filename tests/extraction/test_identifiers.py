"""Observation identity: the empty-digest refusal, the XBRL position, and what must not move.

Three claims, and they pull against each other, which is why they are tested together:

1. **Nothing that already has an id may get a different one.** The pinned literals below are
   real rows of `extract-v1-lexical-2422c4252c07`, copied from `observations.jsonl` with their
   grid coordinates from `claims.jsonl`, so this file fails from a clean checkout if the rule
   drifts. The corpus-wide version of the same claim — all 2,707, recomputed end to end from
   the catalogs — is `tests/graph/test_derivation.py::test_every_real_observation_id_recomputes`
   and is not duplicated here.
2. **An XBRL fact must get an id that discriminates.** Same metric, subject, period and lane,
   three filings, three ids (F0 §3.1).
3. **A lane that discriminates nothing must be refused**, not handed the constant `e3b0c44298fc`.

The XBRL *lane* is F1/F2 and does not exist. These tests drive the identity contract it has to
use, with values from the filings the plan names.
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pytest

from extraction.core import identifiers
from extraction.core.identifiers import (
    EmptyIdentityError,
    digest,
    observation_id,
    xbrl_structural_position,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The constant `digest()` returned for an input that says nothing: `sha256("")[:12]`.
#: Named so a test can assert it is never *minted*, rather than asserting a bare string.
EMPTY_DIGEST = "e3b0c44298fc"

#: The FY2020 revenue triple V1 §2.2 requires as three observations: one original 10-K and two
#: later filings restating the same year in a comparative column. The accessions are the ones
#: the plan measured; the values (2,583,121,000 / 2,583,000,000 / 2,583,000,000) belong to F2
#: and are not asserted here — identity is what this file tests.
FY2020_REVENUE_FILINGS = (
    ("0001801169-21-000011", 2020),   # FY2020 10-K, the original
    ("0001801169-22-000027", 2021),   # FY2021 10-K, FY2020 comparative
    ("0001801169-23-000024", 2022),   # FY2022 10-K, FY2020 comparative
)

REVENUE_CONCEPT = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def fy2020_revenue_position(accession: str, filing_year: int, **overrides):
    return xbrl_structural_position(
        accession=accession, concept=REVENUE_CONCEPT, fiscal_year=filing_year,
        fiscal_period="FY", unit="USD", **overrides)


def fy2020_revenue_id(accession: str, filing_year: int, **overrides) -> str:
    return observation_id(
        "revenue", "opendoor", "FY2020", "xbrl", None,
        fy2020_revenue_position(accession, filing_year, **overrides))


# -- what must not move ---------------------------------------------------------------------
#
# Five real rows of `extract-v1-lexical-2422c4252c07`, chosen from passages Part A does not
# touch: two narrative readings with no structural position, three table readings with the grid
# coordinates the id digests (`metric_label_row_index` / `period_header_column_index`).

REAL_ROWS = (
    ("adjusted_ebitda_margin", "2022Q2", "normalized_narrative",
     "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#p7", (),
     "obs:adjusted-ebitda-margin:opendoor:2022Q2:normalized-narrative:603c21481ff3"),
    ("adjusted_ebitda", "2022Q2", "normalized_narrative",
     "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#p7", (),
     "obs:adjusted-ebitda:opendoor:2022Q2:normalized-narrative:603c21481ff3"),
    ("adjusted_ebitda_margin", "2020-01-01_2020-06-30", "normalized_table",
     "norm:0001801169:0001801169-21-000101:open-20210630.htm#p164", ("row=25", "column=8"),
     "obs:adjusted-ebitda-margin:opendoor:2020-01-01_2020-06-30:normalized-table:04b4b4389a6e"),
    ("adjusted_ebitda_margin", "2020-01-01_2020-09-30", "normalized_table",
     "norm:0001801169:0001801169-21-000120:open-20210930.htm#p170", ("row=26", "column=8"),
     "obs:adjusted-ebitda-margin:opendoor:2020-01-01_2020-09-30:normalized-table:a01670ddb918"),
    ("adjusted_ebitda_margin", "2020Q1", "normalized_table",
     "norm:0001801169:0001801169-21-000016:q12021form8-kxexhibit991.htm#p25",
     ("row=24", "column=6"),
     "obs:adjusted-ebitda-margin:opendoor:2020Q1:normalized-table:f151d6ec5503"),
)


@pytest.mark.parametrize("metric,period,lane,passage,position,expected", REAL_ROWS)
def test_a_filed_observation_id_is_unchanged(
        metric, period, lane, passage, position, expected) -> None:
    """Ids already written to a catalog are a contract with everything that cites them."""
    assert observation_id(metric, "opendoor", period, lane, passage, position) == expected


def test_the_five_argument_call_every_existing_caller_makes_still_works() -> None:
    metric, period, lane, passage, _position, expected = REAL_ROWS[0]
    assert observation_id(metric, "opendoor", period, lane, passage) == expected


def test_the_identity_version_is_declared_and_is_not_part_of_any_id(monkeypatch) -> None:
    """§3.3: the version is a declaration for a manifest, not a digest input.

    If it were an input, every one of the 2,707 filed ids would have moved when it was
    introduced — so changing it here must change nothing.
    """
    assert identifiers.OBSERVATION_IDENTITY_VERSION == "1.1.0"
    metric, period, lane, passage, position, expected = REAL_ROWS[2]
    monkeypatch.setattr(identifiers, "OBSERVATION_IDENTITY_VERSION", "9.9.9")
    assert observation_id(metric, "opendoor", period, lane, passage, position) == expected


# -- the empty-digest refusal ---------------------------------------------------------------


def test_digest_refuses_an_input_that_carries_nothing() -> None:
    for parts in ((), ("",), ("", ""), ("   ", "\t")):
        with pytest.raises(EmptyIdentityError):
            digest(*parts)


def test_one_real_part_beside_a_blank_one_is_a_legitimate_digest() -> None:
    """The XBRL shape: no passage, but a position. And the blank still occupies its slot, so
    `("", "a")` cannot collide with `("a",)`."""
    assert digest("", "accession=0001801169-21-000011") != digest(
        "accession=0001801169-21-000011")


def test_an_observation_with_neither_passage_nor_position_is_refused() -> None:
    """The collision itself (F0 §3.1), asserted as a refusal rather than as a constant."""
    for passage in (None, "", "   "):
        with pytest.raises(EmptyIdentityError, match="no passage_id"):
            observation_id("revenue", "opendoor", "FY2020", "xbrl", passage)


def test_the_constant_is_never_minted() -> None:
    """Every route that used to reach `e3b0c44298fc` now raises instead of returning it."""
    with pytest.raises(EmptyIdentityError):
        digest()
    with pytest.raises(EmptyIdentityError):
        observation_id("revenue", "opendoor", "FY2020", "xbrl", None, ())
    assert not observation_id(
        "revenue", "opendoor", "FY2020", "xbrl", None,
        fy2020_revenue_position(*FY2020_REVENUE_FILINGS[0])).endswith(EMPTY_DIGEST)


def test_a_narrative_claim_with_an_empty_position_is_not_caught_by_the_refusal() -> None:
    """The predicate is *all* identity inputs empty, not `structural_position` empty.

    A narrative claim legitimately states no position and relies on its passage alone — 17 of
    the run's observations do — so a rule keyed on `structural_position == ()` would refuse
    every one of them.
    """
    metric, period, lane, passage, position, expected = REAL_ROWS[0]
    assert position == ()
    assert observation_id(metric, "opendoor", period, lane, passage, position) == expected


# -- the XBRL structural position -------------------------------------------------------------


def test_three_filings_of_one_fact_are_three_observations() -> None:
    """F0 §3.1: without the position all three were `obs:revenue:opendoor:FY2020:xbrl:
    e3b0c44298fc`. The plan needs 2,583,121,000 / 2,583,000,000 / 2,583,000,000 to exist."""
    ids = [fy2020_revenue_id(accession, year) for accession, year in FY2020_REVENUE_FILINGS]
    assert len(set(ids)) == 3
    # And they are recognisably readings of one metric: only the digest segment differs.
    assert len({identifier.rsplit(":", 1)[0] for identifier in ids}) == 1
    assert all(identifier.startswith("obs:revenue:opendoor:FY2020:xbrl:") for identifier in ids)


def test_an_amendment_is_distinct_from_the_filing_it_amends() -> None:
    """A `10-K/A` carries its own accession, which is why no `form` component is needed."""
    original = fy2020_revenue_id("0001801169-21-000011", 2020)
    amendment = fy2020_revenue_id("0001801169-21-000099", 2020)
    assert original != amendment


def test_two_contexts_in_one_filing_are_two_facts() -> None:
    original, year = FY2020_REVENUE_FILINGS[0]
    assert (fy2020_revenue_id(original, year, context_id="c-14")
            != fy2020_revenue_id(original, year, context_id="c-21"))


def test_a_dimensioned_fact_is_distinct_from_the_consolidated_one() -> None:
    original, year = FY2020_REVENUE_FILINGS[0]
    consolidated = fy2020_revenue_id(original, year)
    segment = fy2020_revenue_id(
        original, year, dimensions={"srt:ProductOrServiceAxis": "open:HomesMember"})
    other = fy2020_revenue_id(
        original, year, dimensions={"srt:ProductOrServiceAxis": "open:ServicesMember"})
    assert len({consolidated, segment, other}) == 3


def test_two_concepts_mapped_to_one_metric_stay_two_readings() -> None:
    """`Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax` both map to
    `revenue`; collapsing them would overwrite one with the other."""
    original, year = FY2020_REVENUE_FILINGS[0]
    standard = observation_id(
        "revenue", "opendoor", "FY2020", "xbrl", None,
        xbrl_structural_position(accession=original, concept="us-gaap:Revenues",
                                 fiscal_year=year, fiscal_period="FY", unit="USD"))
    assert standard != fy2020_revenue_id(original, year)


def test_the_unit_is_part_of_fact_identity() -> None:
    original, year = FY2020_REVENUE_FILINGS[0]
    assert (fy2020_revenue_id(original, year)
            != observation_id(
                "revenue", "opendoor", "FY2020", "xbrl", None,
                xbrl_structural_position(accession=original, concept=REVENUE_CONCEPT,
                                         fiscal_year=year, fiscal_period="FY",
                                         unit="USD-per-shares")))


def test_the_position_states_the_components_f0_requires() -> None:
    position = fy2020_revenue_position(*FY2020_REVENUE_FILINGS[1], context_id="c-14")
    assert position == (
        "accession=0001801169-22-000027", "fy=2021", "fp=FY",
        f"concept={REVENUE_CONCEPT}", "context=c-14", "unit=USD")


def test_an_absent_component_keeps_its_slot() -> None:
    """Companyfacts states no `contextRef` — it has already collapsed contexts — so `context`
    is empty there rather than absent, and nothing shifts into its place."""
    position = fy2020_revenue_position(*FY2020_REVENUE_FILINGS[0])
    assert position[4] == "context="
    assert [part.split("=")[0] for part in position] == [
        "accession", "fy", "fp", "concept", "context", "unit"]


def test_a_position_with_no_filing_or_concept_is_refused() -> None:
    with pytest.raises(EmptyIdentityError):
        xbrl_structural_position(accession="", concept=REVENUE_CONCEPT)
    with pytest.raises(EmptyIdentityError):
        xbrl_structural_position(accession="0001801169-21-000011", concept="  ")


# -- determinism ------------------------------------------------------------------------------


def test_an_exact_repeat_is_the_same_id() -> None:
    """Stability is the other half of uniqueness: re-reading one filing must not mint a
    second row for a fact that already has one."""
    first = fy2020_revenue_id(*FY2020_REVENUE_FILINGS[0])
    second = fy2020_revenue_id(*FY2020_REVENUE_FILINGS[0])
    assert first == second


def test_dimensional_identity_does_not_depend_on_the_order_it_arrives_in() -> None:
    axes = [("srt:ProductOrServiceAxis", "open:HomesMember"),
            ("us-gaap:StatementGeographicalAxis", "open:PhoenixMember"),
            ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")]
    original, year = FY2020_REVENUE_FILINGS[0]
    expected = fy2020_revenue_id(original, year, dimensions=dict(axes))
    shuffler = random.Random(20260803)
    for _ in range(8):
        shuffled = list(axes)
        shuffler.shuffle(shuffled)
        assert fy2020_revenue_id(original, year, dimensions=shuffled) == expected
        assert fy2020_revenue_id(original, year, dimensions=dict(shuffled)) == expected


def test_ids_do_not_depend_on_the_order_the_facts_are_read_in() -> None:
    """No ordinal, no counter: shuffling the fact list must reproduce the same id per fact.

    A counter minted to dodge a collision would pass a uniqueness test and fail this one.
    """
    facts = [(accession, year, context)
             for accession, year in FY2020_REVENUE_FILINGS
             for context in ("c-1", "c-2", "c-3")]
    in_order = {fact: fy2020_revenue_id(fact[0], fact[1], context_id=fact[2])
                for fact in facts}
    shuffler = random.Random(4)
    for _ in range(5):
        shuffled = list(facts)
        shuffler.shuffle(shuffled)
        assert {fact: fy2020_revenue_id(fact[0], fact[1], context_id=fact[2])
                for fact in shuffled} == in_order


def test_every_id_in_a_realistic_fixture_set_is_unique_over_the_whole_set() -> None:
    """Uniqueness checked across the whole output, not within a batch — the failure mode is
    two batches each internally clean.
    """
    dimension_sets: tuple[dict[str, str], ...] = (
        {},
        {"srt:ProductOrServiceAxis": "open:HomesMember"},
        {"srt:ProductOrServiceAxis": "open:ServicesMember"},
        {"srt:ProductOrServiceAxis": "open:HomesMember",
         "us-gaap:StatementGeographicalAxis": "open:PhoenixMember"},
    )
    ids: list[str] = []
    for accession, year in FY2020_REVENUE_FILINGS:
        for concept in (REVENUE_CONCEPT, "us-gaap:Revenues"):
            for context in ("c-1", "c-2"):
                for dimensions in dimension_sets:
                    ids.append(observation_id(
                        "revenue", "opendoor", "FY2020", "xbrl", None,
                        xbrl_structural_position(
                            accession=accession, concept=concept, fiscal_year=year,
                            fiscal_period="FY", context_id=context, unit="USD",
                            dimensions=dimensions)))
    assert len(ids) == 48
    assert len(set(ids)) == 48


def test_an_xbrl_id_is_the_same_in_a_second_process() -> None:
    """In-process equality only proves the function is not reading a clock; a separate
    interpreter proves it is not reading `hash()`, whose salt is per-process."""
    accession, year = FY2020_REVENUE_FILINGS[2]
    program = (
        "from extraction.core.identifiers import observation_id, xbrl_structural_position;"
        "print(observation_id('revenue', 'opendoor', 'FY2020', 'xbrl', None,"
        f" xbrl_structural_position(accession={accession!r}, concept={REVENUE_CONCEPT!r},"
        f" fiscal_year={year!r}, fiscal_period='FY', unit='USD',"
        " dimensions={'srt:ProductOrServiceAxis': 'open:HomesMember'})))")
    other = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                           cwd=str(REPO_ROOT), check=True)
    assert other.stdout.strip() == fy2020_revenue_id(
        accession, year, dimensions={"srt:ProductOrServiceAxis": "open:HomesMember"})


def test_an_xbrl_id_cannot_collide_with_a_passage_id_that_reads_like_a_position() -> None:
    """The empty passage keeps its slot, so `(None, ("a",))` and `("a", ())` are two inputs."""
    position = fy2020_revenue_position(*FY2020_REVENUE_FILINGS[0])
    assert observation_id("revenue", "opendoor", "FY2020", "xbrl", None, position) != (
        observation_id("revenue", "opendoor", "FY2020", "xbrl", position[0], position[1:]))
