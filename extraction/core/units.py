"""What unit a claim carries for a metric, and why it is not the ontology's declared unit.

Shared because unit is a scored dimension and two lanes disagreeing about it would be
invisible: a table row and a prose sentence reporting the same metric must produce the same
`unit` string, or the same fact reached through two lanes stops being the same observation.

The rule is one sentence with one surprise in it. `MetricDefinition.unit` names the unit a
metric is *customarily presented* in — `USD_millions` — not the scale a particular source
used. The same metric appears in thousands in a 2021 reconciliation and in millions in a
2025 KPI table, so emitting the declared string would make a claim's unit depend on the
vocabulary's presentation choice rather than on the filing. Claims carry absolute USD and
record the scale separately (V1_CLAIM_EXTRACTION §8a.5).

**The absolute-dollar unit is `USD`, spelled as the ontology spells it** *(verified
2026-08-01, by running a monetary claim through `ontology.validate_claims`)*. Every monetary
metric declares `allowed_units: ['USD', 'USD_thousands', 'USD_millions']` and
`ontology.core.values.MONETARY_UNITS` is `{'USD', 'USD_millions', 'USD_thousands'}`, so a
lower-case `usd` is not in the vocabulary at all and `check_observation_unit` rejects it:
`unit 'usd' not allowed for adjusted_gross_profit`. A claim carrying it cannot pass §4.5's
verify, whatever else is right about it.
"""

from __future__ import annotations

USD = "USD"
USD_CURRENCY = "USD"
PERCENT = "percent"


def unit_for_metric(metric) -> tuple[str | None, str | None]:
    """`(unit, currency)` for a metric definition, or `(None, None)` when undeterminable.

    Currency is populated only for monetary metrics, because a count of homes with a
    currency attached is a claim about dollars that nothing in the filing supports.
    """
    declared = str(getattr(metric, "unit", "") or "")
    value_type = str(getattr(metric, "value_type", "") or "")
    if declared == PERCENT or value_type == "percentage":
        return PERCENT, None
    if value_type == "monetary" or declared.lower().startswith("usd"):
        return USD, USD_CURRENCY
    return declared or None, None


def is_monetary(unit: str | None) -> bool:
    """Whether a magnitude scale may apply at all.

    §8a.5, learned from a wrong answer: `(in thousands, except percentages)` does not except
    `Homes sold in period`, and multiplying that count produced 2,462,000 homes sold in a
    quarter. The exception list is a presentation note about the monetary columns; the unit
    is what decides whether scaling is meaningful.
    """
    return unit == USD
