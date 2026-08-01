"""The two shared readers, driven by the shapes the corpus actually contains.

Every fixture string here is copied from a passage the benchmark cites, so a failure points
at a real filing rather than an invented one.
"""

from __future__ import annotations

import pytest

from extraction.core import numbers, periods
from extraction.core.models import SCALE_FACTOR, PeriodRef

# -- periods -------------------------------------------------------------------------------


def test_bare_year_takes_its_duration_from_the_column_group():
    """The Q4 2020 reconciliation: one header row, two groups, identical end dates.

    A reader that takes the year row alone cannot tell a quarter from a full year, and both
    are present in this table with the same 2020 label.
    """
    quarter = periods.resolve("2020", group_header="Three Months Ended December 31,")
    year = periods.resolve("2020", group_header="Year Ended December 31,")
    assert (quarter.period_start, quarter.period_end) == ("2020-10-01", "2020-12-31")
    assert (year.period_start, year.period_end) == ("2020-01-01", "2020-12-31")
    assert quarter.period_end == year.period_end, "the trap is that the end dates agree"
    assert quarter.key == "2020Q4" and year.key == "FY2020"


def test_a_full_date_column_under_a_three_month_group_resolves_to_that_quarter():
    period = periods.resolve("March 31, 2025", group_header="Three Months Ended")
    assert (period.period_start, period.period_end) == ("2025-01-01", "2025-03-31")
    assert period.key == "2025Q1"


def test_a_period_end_row_is_an_instant_whatever_its_column_group_says():
    """`Homes in inventory (at period end)` sits in a table whose other rows are flows."""
    period = periods.resolve(
        "March 31, 2025",
        group_header="Three Months Ended",
        row_label="Homes in inventory (at period end)",
    )
    assert period.is_instant
    assert period.instant_date == "2025-03-31"
    assert period.period_start is None and period.period_end is None


def test_an_unstated_duration_is_refused_rather_than_guessed():
    """Guessing "quarter" is how a full-year figure silently becomes a Q4 figure."""
    assert periods.resolve("December 31, 2020", group_header="") is None


def test_fiscal_quarter_shorthand_from_the_letters():
    period = periods.resolve("4Q21")
    assert (period.period_start, period.period_end) == ("2021-10-01", "2021-12-31")
    assert period.key == "2021Q4"


def test_prose_instant_from_an_as_of_sentence():
    period = periods.resolve_instant(
        "As of March 31, 2023, 59% of our homes in inventory had been listed"
    )
    assert period.instant_date == "2023-03-31"


@pytest.mark.parametrize(
    "start,end,expected",
    [("2023-01-01", "2023-03-31", "2023Q1"), ("2023-10-01", "2023-12-31", "2023Q4"),
     ("2023-01-01", "2023-12-31", "FY2023"), ("2023-02-01", "2023-05-31", "2023-02-01_2023-05-31")],
)
def test_period_key_is_readable_and_unambiguous(start, end, expected):
    assert PeriodRef(period_start=start, period_end=end).key == expected


# -- numbers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell,expected",
    [("97,132", 97132.0), ("2,946", 2946.0), ("13.0", 13.0), ("50", 50.0),
     ("(30)", -30.0), ("(0.3)", -0.3), ("(17,340)", -17340.0), ("$", None),
     ("%", None), ("Gross Margin", None), ("", None), ("   ", None)],
)
def test_magnitude_parsing(cell, expected):
    assert numbers.parse_magnitude(cell) == expected


def test_parenthesised_negatives_keep_their_sign():
    """(0.3) is the smallest magnitude in the Q1 2025 table and the one where losing the
    parentheses is least visible - it turns a loss into a gain."""
    assert numbers.parse_magnitude("(0.3)") == -0.3
    assert numbers.parse_magnitude("0.3") == 0.3


@pytest.mark.parametrize("dash", ["—", "–", "-", "‒", "―"])
def test_a_dash_is_nil_and_not_a_number(dash):
    """"Restructuring in cost of revenue" reads "—" for both December-31 columns. Nil is
    not a reported zero and not a missing value."""
    assert numbers.is_nil(dash)
    assert numbers.parse_magnitude(dash) is None


def test_a_mojibake_corrupted_cell_still_parses():
    """The corpus is clean, but a rebuild predating the encoding fix produces `97,132Â`.
    Returning None there would look like a metric-matching failure, not a corpus problem."""
    assert numbers.parse_magnitude("97,132Â") == 97132.0
    assert numbers.parse_magnitude("13.0Â") == 13.0


def test_scale_declaration_is_found_in_both_places_the_corpus_puts_it():
    in_table = numbers.parse_scale_declaration("(in thousands, except percentages)")
    preceding = numbers.parse_scale_declaration(
        "(In millions, except percentages, homes sold, number of markets, homes purchased, "
        "and homes in inventory)"
    )
    assert in_table == ("thousands", "percentages")
    assert preceding[0] == "millions"
    assert "homes sold" in preceding[1]


def test_scale_exceptions_exempt_the_rows_they_name():
    exceptions = "percentages, homes sold, number of markets, homes purchased, and homes in inventory"
    assert numbers.scale_excludes(exceptions, "Homes sold")
    assert numbers.scale_excludes(exceptions, "Homes purchased")
    assert numbers.scale_excludes(exceptions, "Homes in inventory (at period end)")
    assert not numbers.scale_excludes(exceptions, "Contribution Profit")
    assert not numbers.scale_excludes(exceptions, "Adjusted EBITDA")


def test_applying_a_scale_is_what_turns_54_into_54_million():
    """The failure this guards is right on every dimension but scale, and wrong by six
    orders of magnitude."""
    assert numbers.apply_scale(54.0, "millions") == 54_000_000
    assert numbers.apply_scale(97038.0, "thousands") == 97_038_000
    assert numbers.apply_scale(2946.0, "units") == 2946
    assert SCALE_FACTOR["millions"] == 1_000_000


@pytest.mark.parametrize(
    "phrase,expected",
    [("Adjusted gross profit was $279 million", 279_000_000.0),
     ("representing $6.6 billion in value", 6_600_000_000.0),
     ("$1.3 billion in capital", 1_300_000_000.0),
     ("was $152 million", 152_000_000.0)],
)
def test_prose_money_carries_its_own_scale(phrase, expected):
    """Letters state the magnitude as a word, so scale travels with the number instead of
    being declared once for a table."""
    value, _ = numbers.parse_money_phrase(phrase)
    assert value == expected


def test_prose_percent():
    value, raw = numbers.parse_percent_phrase("only 8% of our homes had been listed")
    assert value == 8.0 and raw == "8%"
