"""The ontology's declarations, as facts the model reads beside the observations (§4 S4).

Responsibility: turning `MetricDefinition`, `ConceptInstance` and §6.9's answers into
`SemanticFact`, `IdentityFact` and `ComparabilityFact` rows. Pure functions of the ontology and
of a comparison already made — **no graph, no retriever, no model, no clock** — which is what
lets every one of them be driven from a hand-built ontology in a test with no database running.

**Why these are facts and not more metadata.** §10 already carries `metrics[]`, and
`prompts._metric_lines` already sends the planner `metric_id`, `label`, `unit`, `period_type`,
`population`, `distinct_from` and `ambiguities`. What no prompt carried is what the number
*means*: `housing_inventory_homes` is *"count of homes owned and unsold at period end"*, it is
an **instant** and not a duration, and `adjusted_gross_margin` is **non-GAAP** and must never be
merged with `gaap_gross_margin`. A post whose numbers have no declared meaning is what §4 S5
turns into a package refusal; this is the section that stops it being one.

**The ontology is authoritative — recon correction C4 — and the `:Metric` node is not.** The
node carries a `description` property with the same text *(checked live 2026-08-05:
`housing_inventory_homes` reads `'Count of homes owned and unsold at period end.'` in both)*,
and it is still not the source: the same node carries no `percentage_min`, no `distinct_from`
and no `population`, so a builder that read semantics off the graph would be reading half a
definition from a place the ontology does not own.

**What this ontology actually supplies, measured over all 26 metrics (2026-08-05).**

| declaration | coverage |
| --- | --- |
| `label`, `description`, `unit`, `period_type`, `value_type`, `gaap_status` | 26 of 26 |
| `aliases` | 24 of 26 (`cost_of_revenue` and `homes_under_resale_contract` have none) |
| a formula version | 8 metrics, 8 windows, one of them versioned twice |
| `population` / `numerator_description` / `denominator_description` | **1** — `pct_homes_on_market_gt_120_days` |
| `ambiguities` | 4 metrics, 4 ambiguities |
| `source_evidence` | **2** — `homes_under_contract` and `pct_homes_on_market_gt_120_days` |

**Citation handles are empty on every semantic fact, and that is a measurement rather than an
omission.** The two `source_evidence` entries carry an accession, a form, a filing date and a
quote — and **no `passage_id`, no `document_id` and no span**. `PassageCitation` requires all
three, and `EvidenceSourceCitation` would name an `:EvidenceSource` this corpus does not have
(zero nodes, §13.7.2). So the filed sentence travels in the fact's `statement` and its filing in
`source`, and `citations` stays empty rather than being filled with a span nobody can resolve —
which is the fabrication §13.7 exists to refuse.

**Scale.** This ontology encodes scale *in the unit* — `USD_millions`, with `allowed_units`
`('USD', 'USD_thousands', 'USD_millions')` — so *"unit"* and *"scale"* are one declaration and
are one fact. Emitting two rows would have the second restate the first and cost a row of
`fact_kind`/`authoritative`/`editable`/`citations` boilerplate to do it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ontology.contracts import ConceptRegistry
from ontology.core.models import MetricDefinition

from story.core.models import (
    ComparabilityFact,
    IdentityFact,
    PackagedMetric,
    PackagedSubject,
    SemanticFact,
)
from story.core.series import (
    CanonicalPoint,
    ClaimKind,
    Comparability,
    ComparabilityAuthority,
    Refuse,
    rules_evaluated,
)

#: The per-metric declarations §4 S4 names, as the `attribute` values this module emits. Written
#: out rather than derived from whatever the ontology happens to carry, because *"which
#: declarations should have reached the model"* is a question about the plan and not about the
#: data: a metric that declares no scope rule must be visibly missing a `scope` row, not
#: silently short one.
SEMANTIC_ATTRIBUTES: tuple[str, ...] = (
    "display_name",
    "aliases",
    "definition",
    "unit",
    "period_semantics",
    "formula_version",
    "scope",
)

#: The three `ComparabilityFact.rule_id`s that are not one of §6.9's numbered rules. A comparison
#: decision names the rule that decided it (`R2`, `R8`, …) or `§6.9/<claim>` when every gate
#: passed; the two standing rules below range over metrics rather than over a pair.
RULE_MUTUALLY_DISTINCT = "R2/mutually_distinct"
RULE_PERCENTAGE_POINTS = "§13.3/percentage_points"

#: `SemanticFact.value` and `IdentityFact.value` hold the structured form where there is one.
#: An empty string means the declaration is prose and has no other form — see the field's own
#: docstring; it is never a stand-in for "unknown".
_NO_STRUCTURED_VALUE = ""


def ontology_source(ontology_id: str, semantic_version: str) -> str:
    """What `source` says about a row this module read out of the ontology.

    `ontology:<id>@<semantic_version>` and not the definition hash: the hash is already a field
    of the package (`ontology_definition_hash`) and is not readable, and a reader looking at an
    evidence panel needs to know *which ontology*, not which of its bytes.

    **This doubles as the effective date §4 S4 asks identity facts to carry.** Nothing in this
    corpus dates an identity attribute — the `ConceptInstance` for `opendoor` carries a CIK,
    tickers and an exchange with no `valid_from` of any kind, and neither does the `:Entity`
    node *(checked live 2026-08-05: 20 properties, none of them a date)* — so the honest
    statement of *"as of when"* is the version of the ontology that declares it, and inventing a
    per-attribute date would be worse than saying so.
    """
    return f"ontology:{ontology_id}@{semantic_version}"


# -- per metric ------------------------------------------------------------------------------


def semantic_facts(
    metrics: Sequence[PackagedMetric],
    registry: ConceptRegistry,
    *,
    source: str,
    anchor_dates: Sequence[str] = (),
) -> tuple[SemanticFact, ...]:
    """Every declaration the ontology makes about every metric the package references.

    Order is the package's own metric order, then `SEMANTIC_ATTRIBUTES`, so two builds of one
    package render one byte sequence — these rows are inside `package_content_digest`.

    `anchor_dates` are the candidate's own period anchors and decide **which** formula version a
    `formula_version` row names. A metric whose anchors straddle a version boundary gets a row
    per version, which is the same fact `formula_window_boundary_crossed` warns about, stated as
    a definition rather than as a warning: the warning says *"you are comparing two
    definitions"*, and this says what the two definitions are.
    """
    facts: list[SemanticFact] = []
    for metric in metrics:
        definition = registry.find(metric.metric_id)
        if not isinstance(definition, MetricDefinition):
            continue
        for attribute in SEMANTIC_ATTRIBUTES:
            facts.extend(_facts_for(
                metric.metric_id, definition, attribute,
                registry=registry, source=source, anchor_dates=anchor_dates))
    return tuple(facts)


def _facts_for(
    metric_id: str,
    definition: MetricDefinition,
    attribute: str,
    *,
    registry: ConceptRegistry,
    source: str,
    anchor_dates: Sequence[str],
) -> tuple[SemanticFact, ...]:
    """The rows one attribute produces — usually one, none where nothing is declared."""
    if attribute == "display_name":
        return (_semantic(metric_id, attribute, source,
                          f"{metric_id} is written {definition.label!r}.", definition.label),)
    if attribute == "aliases":
        # An alias that *is* the label states nothing: `gaap_gross_margin`'s only alias is
        # `'Gross Margin'`, which is also its label, and the row would have read *"Gross Margin
        # also appears in filings as 'Gross Margin'"* — 85 tokens to say nothing. Ten of this
        # ontology's 24 alias sets are exactly this. Filtered on the normalised form, because
        # `'Homes in Inventory'` against label `'Homes in inventory'` is the same non-statement.
        others = tuple(alias for alias in definition.aliases
                       if alias.strip().casefold() != definition.label.strip().casefold())
        if not others:
            return ()
        return (_semantic(
            metric_id, attribute, source,
            f"{definition.label} also appears in filings as "
            + ", ".join(repr(alias) for alias in others) + ".",
            "; ".join(others)),)
    if attribute == "definition":
        if not definition.description:
            return ()
        basis = _gaap_phrase(definition)
        return (_semantic(
            metric_id, attribute, source,
            f"{definition.label} means: {definition.description}{basis}",
            definition.description),)
    if attribute == "unit":
        return (_semantic(metric_id, attribute, source, _unit_statement(definition),
                          definition.unit),)
    if attribute == "period_semantics":
        return (_semantic(metric_id, attribute, source, _period_statement(definition),
                          _enum_value(definition.period_type)),)
    if attribute == "formula_version":
        return _formula_facts(metric_id, definition, registry=registry, source=source,
                              anchor_dates=anchor_dates)
    if attribute == "scope":
        return _scope_facts(metric_id, definition, source=source)
    raise ValueError(f"{attribute!r} is not one of {SEMANTIC_ATTRIBUTES}")  # pragma: no cover


def _gaap_phrase(definition: MetricDefinition) -> str:
    """Whether a figure is GAAP, and it belongs in the definition rather than in a row of its own.

    A reader who is told what Adjusted Gross Margin *is* and not that it is non-GAAP has been
    told the smaller half: `gaap_status` is why `reconciles_to` exists and why §6.9 R2 keeps the
    two margins apart. It is one clause, so it costs one clause.
    """
    status = _enum_value(definition.gaap_status)
    return {
        "gaap": " It is a GAAP measure.",
        "non_gaap": " It is a non-GAAP measure the company defines itself.",
        "operating_kpi": " It is an operating metric the company reports, not a GAAP measure.",
        "macro_indicator": " It is a macroeconomic indicator, not a company figure.",
    }.get(status, "")


def _unit_statement(definition: MetricDefinition) -> str:
    """Unit and scale in one sentence, because this ontology states them as one field."""
    text = f"{definition.label} is reported in {definition.unit}"
    others = tuple(u for u in definition.allowed_units if u != definition.unit)
    if others:
        text += (", and filings also print it in " + ", ".join(others)
                 + "; the scale a figure was printed at is on the fact, not on the metric")
    return text + "."


def _period_statement(definition: MetricDefinition) -> str:
    """§4 S4's *"instant-vs-duration semantics"*, spelled out rather than named.

    `period_type: instant` is a word a model can copy and not a rule it can apply. The sentence
    says what the word means for a claim, because §13.4's period grammar refuses a duration
    surface on an instant fact and a model that has only seen the enum has not been told why.
    """
    if _enum_value(definition.period_type) == "instant":
        return (f"{definition.label} is measured at a single moment - it is a level on a date, "
                "not an amount over a period, and it is never summed across periods.")
    return (f"{definition.label} is measured over a period - it is an amount accumulated between "
            "two dates, and a figure for one quarter is not a figure for the year.")


def _formula_facts(
    metric_id: str,
    definition: MetricDefinition,
    *,
    registry: ConceptRegistry,
    source: str,
    anchor_dates: Sequence[str],
) -> tuple[SemanticFact, ...]:
    """One row per formula version in force on the candidate's own anchor dates.

    Dates are the ontology's `valid_from`/`valid_to` and are the same pair `formula_windows[]`
    already carries; the row exists because the *window* says when a definition applied and the
    *fact* says what the definition is.
    """
    seen: dict[str, Any] = {}
    for as_of in (tuple(anchor_dates) or (None,)):
        formula = registry.formula_for(metric_id, as_of)
        if formula is not None:
            seen.setdefault(formula.concept_id, formula)
    rows: list[SemanticFact] = []
    for version_id, formula in sorted(seen.items()):
        window = f"{formula.valid_from} to {formula.valid_to or 'the present'}"
        rows.append(_semantic(
            metric_id, "formula_version", source,
            f"{definition.label} is defined as {formula.expression}, under formula version "
            f"{version_id}, in force from {window}.",
            version_id))
    return tuple(rows)


def _scope_facts(
    metric_id: str, definition: MetricDefinition, *, source: str
) -> tuple[SemanticFact, ...]:
    """§4 S4's *"scope/inclusion rules **where source-backed**"* — and usually there are none.

    Three things can carry one: the ontology's `population` (what the denominator counts), its
    `numerator_description`/`denominator_description`, and a `source_evidence` quote. On this
    ontology **exactly one metric** has any of them — `pct_homes_on_market_gt_120_days` — so this
    returns `()` for every metric in every package built so far, and that emptiness is the
    finding rather than a gap in this function.

    A `source_evidence` quote is carried verbatim with its accession and form in `source`. It is
    **not** turned into a citation handle: those entries carry no `passage_id` and no span, so a
    `PassageCitation` built from one would name a location nothing can check.

    **Three sources produce three distinct attributes rather than three `scope` rows**, because
    `fact_id` is `sem:<metric>:<attribute>` and `pct_homes_on_market_gt_120_days` carries all
    three — one `scope` name would mint one id three times, and two rows under one id is a
    package no evidence panel can index. `SEMANTIC_ATTRIBUTES` names the *declaration* the plan
    asks for; the emitted attribute is finer where the ontology is.
    """
    rows: list[SemanticFact] = []
    population = definition.population
    if population is not None:
        wording = population.normalized or " | ".join(population.raw_variants)
        variants = ""
        if population.raw_variants and population.normalized:
            variants = (" Filings word it as "
                        + ", ".join(repr(v) for v in population.raw_variants) + ".")
        rows.append(_semantic(
            metric_id, "scope_population", source,
            f"{definition.label} is measured over {wording} (the ontology's confidence in that "
            f"reading is {population.confidence}).{variants}",
            wording))
    if definition.numerator_description or definition.denominator_description:
        rows.append(_semantic(
            metric_id, "scope_ratio", source,
            f"{definition.label} counts {definition.numerator_description or 'an unstated set'} "
            f"over {definition.denominator_description or 'an unstated set'}.",
            _NO_STRUCTURED_VALUE))
    filed = 0
    for evidence in definition.source_evidence:
        if not evidence.quote:
            continue
        filed += 1
        filing = " ".join(part for part in (evidence.form, evidence.accession,
                                            evidence.filing_date) if part)
        rows.append(_semantic(
            metric_id, "scope_filed" if filed == 1 else f"scope_filed_{filed}",
            filing or source,
            f"{definition.label}'s scope is stated in a filing: {evidence.quote!r}",
            _NO_STRUCTURED_VALUE))
    return tuple(rows)


def _semantic(
    metric_id: str, attribute: str, source: str, statement: str, value: str
) -> SemanticFact:
    """One row, with the two flags §4 S4 requires stated rather than defaulted.

    `authoritative=True` is C4 and `editable=False` is S6's *"ontology facts are not reachable
    from the prompt UI"*. Both are constants here because every row this function makes came out
    of the ontology; a row from anywhere else must not use this constructor.
    """
    return SemanticFact(
        fact_id=f"sem:{metric_id}:{attribute}",
        metric_id=metric_id,
        attribute=attribute,
        statement=statement,
        value=value,
        authoritative=True,
        editable=False,
        source=source,
    )


# -- per subject -----------------------------------------------------------------------------

#: The identity attributes §4 S4 names. `description` is last because it is the one that is
#: **not available** and a reader should meet the structured answers first.
IDENTITY_ATTRIBUTES: tuple[str, ...] = ("legal_name", "ticker", "entity_type", "description")

#: What the `description` row says when the corpus carries no source-backed one. Written as a
#: constant because it is the whole point of `IdentityFact.available` and a test asserts the
#: exact sentence reaches the wire: a model shown *"no description is available"* is a model
#: that has been told not to supply one, and a model shown nothing has been told nothing.
NO_DESCRIPTION_STATEMENT = (
    "No description of this company exists in the corpus or the ontology, so none is given. "
    "Do not describe what the company does, what it sells, or what market it operates in.")


def identity_facts(
    subject: PackagedSubject,
    registry: ConceptRegistry,
    *,
    source: str,
) -> tuple[IdentityFact, ...]:
    """Structured identity from the ontology's own `ConceptInstance`, and no description.

    **No company description may be invented from model knowledge, and there is none to carry.**
    Measured 2026-08-05 against `graph-v1-0483dc6b4b10`: the `opendoor` `ConceptInstance` holds
    `cik`, `tickers` and `exchange` and nothing else; the `:Entity` node holds twenty properties
    and **not one of them is a description**; and across all nine `:Entity` nodes no `description`
    property key exists at all. So the `description` row is emitted with `available=False` and a
    statement that says so, which is stronger than omitting it: an absent row leaves the model
    free to supply one from its weights and leaves S6's panel unable to say the corpus was asked.

    **`authoritative` is decided here, per row, and it is not one answer for the section.** S1
    left the decision open — *"a default would be this module answering a question the populating
    stage is the only one that can"* — and the honest split is by provenance. A row read from the
    ontology's instance is authoritative: the ontology declares Opendoor's legal name, tickers
    and exchange, and the `:Entity` node carries byte-identical values, so reading the ontology is
    not weaker than the graph read `subject_identity_not_read_from_graph` says is unavailable. A
    row that falls back to `PackagedSubject.entity_text` is **not** authoritative, because that
    string is the caller's; and the `description` row is not authoritative because nothing
    declared it.

    **No `get_subject_context` tool was added, and it would have returned nothing new.** §4 S4
    permits a bounded one; `cypher.py`'s allowlist excludes `:Entity` and a committed test bans
    `OBSERVATION_OF_SUBJECT` from traversal, and the values such a tool could legally return are
    the four above — which this reads from the ontology without opening a connection. A tool that
    duplicates a declaration is a second authority, which is what C4 forbids.
    """
    instance = _instance(registry, subject.entity_id)
    properties: Mapping[str, Any] = dict(getattr(instance, "properties", {}) or {})
    entity_type = getattr(instance, "concept_id", "") or ""
    rows: list[IdentityFact] = []

    legal_name = str(getattr(instance, "label", "") or "") or subject.entity_text
    from_ontology = instance is not None
    rows.append(_identity(
        subject.entity_id, "legal_name",
        f"The subject of this post is {legal_name}.", legal_name,
        available=bool(legal_name), authoritative=from_ontology,
        source=source if from_ontology else "package.subject.entity_text"))

    tickers = tuple(part.strip() for part in str(properties.get("tickers") or "").split(",")
                    if part.strip())
    exchange = str(properties.get("exchange") or "")
    if tickers:
        listed = f" on {exchange}" if exchange else ""
        others = (" It also has the tickers " + ", ".join(tickers[1:]) + "."
                  if len(tickers) > 1 else "")
        rows.append(_identity(
            subject.entity_id, "ticker",
            f"{legal_name} trades{listed} under the ticker {tickers[0]}.{others}",
            tickers[0], available=True, authoritative=True, source=source))
    else:
        rows.append(_identity(
            subject.entity_id, "ticker",
            f"No ticker is declared for {legal_name}.", _NO_STRUCTURED_VALUE,
            available=False, authoritative=False, source=source))

    type_definition = registry.find(entity_type) if entity_type else None
    described = getattr(type_definition, "description", "") or ""
    cik = str(properties.get("cik") or "")
    filer = f" Its SEC filer identifier (CIK) is {cik}." if cik else ""
    rows.append(_identity(
        subject.entity_id, "entity_type",
        (f"{legal_name} is a {entity_type}"
         + (f" - {described}" if described else "") + f"{filer}")
        if entity_type else f"No entity type is declared for {legal_name}.",
        entity_type, available=bool(entity_type), authoritative=from_ontology, source=source))

    rows.append(_identity(
        subject.entity_id, "description", NO_DESCRIPTION_STATEMENT, _NO_STRUCTURED_VALUE,
        available=False, authoritative=False, source=source))
    return tuple(rows)


def _instance(registry: ConceptRegistry, entity_id: str) -> Any:
    """The ontology's `ConceptInstance` for this subject, or `None`.

    `getattr` because `ConceptRegistry` — the protocol `story/` depends on — declares `concept`,
    `find`, `metric`, `by_category`, `resolve_alias`, `formula_for`, `ancestors` and `is_a`, and
    **not** `instance`. `InMemoryConceptRegistry` has it; the protocol does not, and `ontology/`
    is not a package this stage may edit to widen it. The same shape `_inner_search_limit` uses
    against `GraphRetriever`, for the same reason: a structural protocol plus a capability the
    caller may or may not have.
    """
    lookup = getattr(registry, "instance", None)
    return lookup(entity_id) if callable(lookup) else None


def _identity(
    entity_id: str,
    attribute: str,
    statement: str,
    value: str,
    *,
    available: bool,
    authoritative: bool,
    source: str,
) -> IdentityFact:
    return IdentityFact(
        fact_id=f"idn:{entity_id}:{attribute}",
        entity_id=entity_id,
        attribute=attribute,
        statement=statement,
        value=value if available else _NO_STRUCTURED_VALUE,
        available=available,
        authoritative=authoritative,
        editable=False,
        source=source,
    )


# -- per comparison --------------------------------------------------------------------------


def comparison_fact(
    claim: ClaimKind,
    left: CanonicalPoint,
    right: CanonicalPoint,
    answer: Comparability,
    *,
    source: str,
) -> ComparabilityFact:
    """One comparison the candidate makes, as the rule that governed it.

    **Not a second `CompatibilityDecision` and not a second implementation.** `answer` is what
    `series.comparable` already returned for this pair inside `_add_compatibility`; this renders
    it. `rules_evaluated` is `series`'s own — a refusal at R3 never reached R4, and a fact that
    claimed *"R1-R10 were applied"* about it would be false in the direction that matters.
    """
    evaluated = ", ".join(rules_evaluated(answer))
    metric_ids = tuple(sorted({left.metric_id, right.metric_id}))
    slots = f"{left.metric_id} {left.period.key} and {right.metric_id} {right.period.key}"
    if isinstance(answer, Refuse):
        statement = (f"{slots} may NOT be compared as a {claim.value} claim. "
                     f"{answer.rule} refused it: {answer.detail}. "
                     f"Rules evaluated, in order: {evaluated}.")
        rule_id = answer.rule
    else:
        warnings = "".join(f" Warning {w.code}: {w.detail}." for w in answer.warnings)
        statement = (f"{slots} may be compared as a {claim.value} claim: every comparability "
                     f"rule the ontology applies to this pair passed. Rules evaluated, in "
                     f"order: {evaluated}.{warnings}")
        rule_id = f"§6.9/{claim.value}"
    return ComparabilityFact(
        fact_id=f"cmp:{claim.value}:{left.metric_id}@{left.period.key}:"
                f"{right.metric_id}@{right.period.key}",
        rule_id=rule_id,
        metric_ids=metric_ids,
        statement=statement,
        authoritative=True,
        editable=False,
        source=source,
    )


def standing_facts(
    metrics: Sequence[PackagedMetric],
    authority: ComparabilityAuthority,
    *,
    source: str,
) -> tuple[ComparabilityFact, ...]:
    """The comparison rules that range over metrics rather than over one pair.

    Two of them, and both are rules a planner needs *before* it writes rather than a verdict on
    a comparison it already made:

    * **R2's mutually-distinct groups.** `adjusted_gross_margin` and `gaap_gross_margin` are both
      in `margin_measures`, which is precisely why they may be set against each other as a
      divergence pair and may never be merged or both called *"gross margin"*. §13.5 refuses the
      short surface; this is the reason for the refusal, stated up front.
    * **§13.3's percentage points.** A difference between two percentages is measured in
      percentage points, and merging the two is *"the single most likely factual error this
      package can make"* (`observation_equivalence`'s own words). The writer's rule 10 already
      says it; the planner had never been told, and the planner is what decides whether the post
      contains that comparison at all.

    The second is emitted only when the package holds two or more `percent` metrics, because a
    package that cannot express the confusion does not need to be warned about it.
    """
    rows: list[ComparabilityFact] = []
    present = {metric.metric_id for metric in metrics}
    groups: dict[str, set[str]] = {}
    for metric_id in sorted(present):
        for group in authority.groups.get(metric_id, ()):  # type: ignore[union-attr]
            groups.setdefault(group, set()).add(metric_id)
    for group, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        named = ", ".join(sorted(members))
        rows.append(ComparabilityFact(
            fact_id=f"cmp:distinct:{group}",
            rule_id=RULE_MUTUALLY_DISTINCT,
            metric_ids=tuple(sorted(members)),
            statement=(f"The ontology declares {named} mutually distinct (group {group!r}). "
                       "They may be set against each other as a divergence, and they may never "
                       "be merged, added, averaged, or called by one another's name."),
            authoritative=True,
            editable=False,
            source=source,
        ))
    percentages = tuple(sorted(m.metric_id for m in metrics if m.unit == "percent"))
    if len(percentages) >= 2:
        rows.append(ComparabilityFact(
            fact_id="cmp:percentage-points:" + "+".join(percentages),
            rule_id=RULE_PERCENTAGE_POINTS,
            metric_ids=percentages,
            statement=("A difference between two figures reported in percent is measured in "
                       "percentage points, never in percent. "
                       + ", ".join(percentages) + " are all reported in percent, so any gap "
                       "between two of them is a number of percentage points."),
            authoritative=True,
            editable=False,
            source=source,
        ))
    return tuple(rows)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


__all__ = [
    "IDENTITY_ATTRIBUTES",
    "NO_DESCRIPTION_STATEMENT",
    "RULE_MUTUALLY_DISTINCT",
    "RULE_PERCENTAGE_POINTS",
    "SEMANTIC_ATTRIBUTES",
    "comparison_fact",
    "identity_facts",
    "ontology_source",
    "semantic_facts",
    "standing_facts",
]
