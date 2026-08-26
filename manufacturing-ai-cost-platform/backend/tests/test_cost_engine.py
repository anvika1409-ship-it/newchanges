"""Cost engine tests.

Covers the required cases: unknown pricing, estimated vs actual, and the
determinism acceptance criterion.

The negative assertions here are the important ones. Every "must be None" and
"must not be ACTUAL" exists because the alternative is reporting a cost figure
the platform cannot stand behind (AI_DEVELOPMENT_RULES.md section 10). If one
fails, fix the engine — never relax the expectation into a default of zero.
"""

from __future__ import annotations

import pytest

from app.services.cost_engine import (
    CostUnit,
    ModelPricing,
    Provenance,
    TokenUsage,
    ToolUsage,
    UsageSource,
    compute_cost,
    parse_cost_unit,
    weakest,
)

USD = "USD"


def _pricing(
    input_cost: float | None = 0.001,
    output_cost: float | None = 0.002,
    cost_unit: str | None = "per_1k_tokens",
) -> ModelPricing:
    return ModelPricing(
        input_cost=input_cost,
        output_cost=output_cost,
        cost_unit=cost_unit,
        currency=USD,
    )


def _reported(input_tokens: int = 1000, output_tokens: int = 500) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        source=UsageSource.PROVIDER_REPORTED,
    )


# ===========================================================================
# Arithmetic
# ===========================================================================
def test_input_and_output_are_priced_separately() -> None:
    """1000 in at 0.001/1k, 500 out at 0.002/1k."""
    result = compute_cost(_reported(1000, 500), _pricing())

    assert result.input_cost == pytest.approx(0.001)
    assert result.output_cost == pytest.approx(0.001)
    assert result.total_cost == pytest.approx(0.002)
    assert result.currency == USD


@pytest.mark.parametrize(
    ("unit", "expected_input_cost"),
    [
        ("per_token", 1.0),
        ("per_1k_tokens", 0.001),
        ("per_1m_tokens", 0.000001),
    ],
)
def test_each_recognised_unit_scales_correctly(
    unit: str, expected_input_cost: float
) -> None:
    result = compute_cost(
        _reported(1000, 0), _pricing(input_cost=0.001, output_cost=0.0, cost_unit=unit)
    )
    assert result.input_cost == pytest.approx(expected_input_cost)


def test_zero_tokens_cost_nothing_and_are_not_unknown() -> None:
    """A genuine zero is a real value; only None means "not known"."""
    result = compute_cost(_reported(0, 0), _pricing())

    assert result.total_cost == 0.0
    assert result.provenance is Provenance.ACTUAL


def test_cost_unit_parsing_tolerates_operator_formatting() -> None:
    assert parse_cost_unit("PER_1K_TOKENS") is CostUnit.PER_1K_TOKENS
    assert parse_cost_unit("per-1k-tokens") is CostUnit.PER_1K_TOKENS
    assert parse_cost_unit(" per 1m tokens ") is CostUnit.PER_1M_TOKENS


# ===========================================================================
# Unknown pricing
# ===========================================================================
def test_missing_rates_produce_no_total(caplog: pytest.LogCaptureFixture) -> None:
    """The seeded GenAILab models all have null pricing, so this is the norm."""
    result = compute_cost(_reported(), _pricing(input_cost=None, output_cost=None))

    assert result.total_cost is None
    assert result.provenance is Provenance.UNAVAILABLE
    assert result.unavailable_reason == "pricing_not_configured"
    # The critical negative: unknown pricing is never zero.
    assert result.total_cost != 0.0


def test_a_rate_without_its_unit_is_not_pricing() -> None:
    """A number with no unit cannot be scaled, so it cannot be used."""
    result = compute_cost(_reported(), _pricing(cost_unit=None))

    assert result.total_cost is None
    assert result.provenance is Provenance.UNAVAILABLE
    assert result.unavailable_reason == "cost_unit_unrecognised"


def test_an_unrecognised_unit_is_refused_not_guessed() -> None:
    """Assuming "per 1K" for a model priced per million misreports by 1000x."""
    result = compute_cost(_reported(), _pricing(cost_unit="per_fortnight"))

    assert result.total_cost is None
    assert result.provenance is Provenance.UNAVAILABLE


def test_half_configured_pricing_is_still_unusable() -> None:
    result = compute_cost(_reported(), _pricing(output_cost=None))
    assert result.total_cost is None
    assert result.provenance is Provenance.UNAVAILABLE


def test_unknown_token_counts_produce_no_total() -> None:
    """Pricing is configured, but half the usage is missing."""
    usage = TokenUsage(
        input_tokens=1000, output_tokens=None, source=UsageSource.PROVIDER_REPORTED
    )
    result = compute_cost(usage, _pricing())

    assert result.input_cost == pytest.approx(0.001)
    assert result.total_cost is None
    assert result.unavailable_reason == "incomplete_token_usage"


def test_known_components_survive_an_unavailable_total() -> None:
    """An operator still needs to see what *was* known."""
    result = compute_cost(
        _reported(),
        _pricing(input_cost=None, output_cost=None),
        tools=ToolUsage(calls=2, estimated_cost_per_call=0.5),
    )
    assert result.total_cost is None
    assert result.tool_cost == pytest.approx(1.0)


# ===========================================================================
# Estimated vs actual
# ===========================================================================
def test_provider_reported_tokens_yield_an_actual_cost() -> None:
    result = compute_cost(_reported(), _pricing())

    assert result.provenance is Provenance.ACTUAL
    assert result.actual_cost == pytest.approx(0.002)
    assert result.estimated_cost is None


def test_locally_estimated_tokens_yield_an_estimated_cost() -> None:
    """Pre-execution token counts are a prediction, whatever the pricing."""
    usage = TokenUsage(input_tokens=1000, output_tokens=500, source=UsageSource.ESTIMATED)
    result = compute_cost(usage, _pricing())

    assert result.provenance is Provenance.ESTIMATED
    assert result.estimated_cost == pytest.approx(0.002)
    # The critical negative: an estimate never lands in the actual column.
    assert result.actual_cost is None


def test_default_usage_source_is_estimated() -> None:
    """The safe default. A caller that does not say must not get ACTUAL."""
    result = compute_cost(TokenUsage(input_tokens=10, output_tokens=10), _pricing())
    assert result.provenance is Provenance.ESTIMATED


def test_tool_cost_downgrades_an_otherwise_actual_total() -> None:
    """`tools.estimated_cost` is an estimate by its own column name."""
    result = compute_cost(
        _reported(), _pricing(), tools=ToolUsage(calls=3, estimated_cost_per_call=0.01)
    )

    assert result.tool_cost == pytest.approx(0.03)
    assert result.provenance is Provenance.ESTIMATED
    assert result.actual_cost is None


def test_infrastructure_allocation_downgrades_an_actual_total() -> None:
    """An allocation is a division of a bill, not an observed charge."""
    result = compute_cost(_reported(), _pricing(), infrastructure_cost=0.5)

    assert result.infrastructure_cost == pytest.approx(0.5)
    assert result.provenance is Provenance.ESTIMATED


def test_unpriced_tools_do_not_contribute_or_downgrade() -> None:
    """Calls with no configured rate add nothing rather than adding zero."""
    result = compute_cost(
        _reported(), _pricing(), tools=ToolUsage(calls=5, estimated_cost_per_call=None)
    )

    assert result.tool_cost is None
    assert result.provenance is Provenance.ACTUAL


def test_all_four_components_sum_into_the_total() -> None:
    result = compute_cost(
        _reported(1000, 500),
        _pricing(),
        tools=ToolUsage(calls=2, estimated_cost_per_call=0.25),
        infrastructure_cost=0.1,
    )

    assert result.input_cost == pytest.approx(0.001)
    assert result.output_cost == pytest.approx(0.001)
    assert result.tool_cost == pytest.approx(0.5)
    assert result.infrastructure_cost == pytest.approx(0.1)
    assert result.total_cost == pytest.approx(0.602)


def test_provenance_never_upgrades() -> None:
    assert weakest(Provenance.ACTUAL, Provenance.ESTIMATED) is Provenance.ESTIMATED
    assert weakest(Provenance.ESTIMATED, Provenance.UNAVAILABLE) is Provenance.UNAVAILABLE
    assert weakest(Provenance.ACTUAL, Provenance.ACTUAL) is Provenance.ACTUAL
    # No arguments cannot mean "trustworthy".
    assert weakest() is Provenance.UNAVAILABLE


# ===========================================================================
# Acceptance: determinism
# ===========================================================================
def test_the_same_input_produces_an_identical_result() -> None:
    """Byte-for-byte equality, not approximate equality."""
    usage = _reported(1234, 567)
    pricing = _pricing(input_cost=0.0000031, output_cost=0.0000097, cost_unit="per_token")
    tools = ToolUsage(calls=3, estimated_cost_per_call=0.017)

    first = compute_cost(usage, pricing, tools=tools, infrastructure_cost=0.0031)
    second = compute_cost(usage, pricing, tools=tools, infrastructure_cost=0.0031)

    assert first == second
    assert repr(first.total_cost) == repr(second.total_cost)


def test_decimal_arithmetic_avoids_float_drift() -> None:
    """0.1 + 0.2 in binary floating point is famously not 0.3.

    Rates at this scale are exactly where that shows up, so the engine works in
    Decimal and a total that should be exact is exact.
    """
    result = compute_cost(
        _reported(100, 200),
        _pricing(input_cost=0.001, output_cost=0.001, cost_unit="per_token"),
    )
    assert result.total_cost == 0.3


def test_component_order_does_not_change_the_total() -> None:
    """Two runs whose components arrive by different code paths still agree."""
    priced_via_tools = compute_cost(
        _reported(1000, 500), _pricing(), tools=ToolUsage(calls=1, estimated_cost_per_call=0.1)
    )
    priced_via_infrastructure = compute_cost(
        _reported(1000, 500), _pricing(), infrastructure_cost=0.1
    )
    assert priced_via_tools.total_cost == priced_via_infrastructure.total_cost


def test_pricing_reads_off_a_registry_entry() -> None:
    """The engine takes pricing from the registry, never from a model name."""

    class Entry:
        input_cost = 0.002
        output_cost = 0.004
        cost_unit = "per_1k_tokens"

    pricing = ModelPricing.from_registry_entry(Entry(), currency=USD)

    assert pricing.is_usable
    assert compute_cost(_reported(1000, 1000), pricing).total_cost == pytest.approx(0.006)


def test_pricing_from_an_unpriced_registry_entry_is_unusable() -> None:
    """Matches every model in the shipped seed file."""

    class Entry:
        input_cost = None
        output_cost = None
        cost_unit = None

    pricing = ModelPricing.from_registry_entry(Entry(), currency=USD)
    assert not pricing.is_usable
