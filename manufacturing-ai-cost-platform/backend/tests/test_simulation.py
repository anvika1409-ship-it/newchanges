"""What-if simulation tests.

The rule under test throughout: **an unknown is never a zero, and a projection
is never presented as a measurement.**

A simulator that quietly treats an unpriced model as free manufactures a saving
that does not exist — and a saving is exactly what someone will act on.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.db.models.registry import ModelRegistryEntry
from app.optimization.simulation import (
    Baseline,
    ModelMixEntry,
    Provenance,
    RiskLevel,
    SimulationInput,
    simulate,
)

CURRENCY = "USD"


def model(**overrides: Any) -> ModelRegistryEntry:
    """A registry entry, unpriced and unmeasured unless a test says otherwise."""
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "model_name": f"m-{uuid.uuid4().hex[:6]}",
        "provider": "genailab",
        "capability": "reasoning",
        "input_cost": None,
        "output_cost": None,
        "cost_unit": None,
        "quality_score": None,
        "risk_level": None,
        "enabled": True,
    }
    defaults.update(overrides)
    return ModelRegistryEntry(**defaults)


def priced(model_id: str, rate: float, **overrides: Any) -> ModelRegistryEntry:
    """A fully priced model: both rates and the unit are present."""
    return model(
        id=model_id,
        input_cost=rate / 2,
        output_cost=rate / 2,
        cost_unit="per_1k_tokens",
        **overrides,
    )


def baseline(**overrides: Any) -> Baseline:
    defaults: dict[str, Any] = {
        "actual_cost": 100.0,
        "estimated_cost": 0.0,
        "total_requests": 1000,
        "currency": CURRENCY,
    }
    defaults.update(overrides)
    return Baseline(**defaults)


def sim_input(**overrides: Any) -> SimulationInput:
    defaults: dict[str, Any] = {"request_volume": 2000}
    defaults.update(overrides)
    return SimulationInput(**defaults)


# ===========================================================================
# Provenance labelling
# ===========================================================================
def test_the_result_as_a_whole_is_simulated() -> None:
    result = simulate(sim_input(), baseline(), {})
    assert result.provenance is Provenance.SIMULATED


def test_each_figure_carries_its_own_provenance() -> None:
    """Current is measured, forecast is projected, optimized is simulated."""
    m = priced("m1", 0.05)
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("m1", 100.0),)), baseline(), {"m1": m}
    )

    assert result.current_cost.provenance is Provenance.ACTUAL
    assert result.forecast_cost.provenance is Provenance.FORECAST
    assert result.optimized_cost.provenance is Provenance.SIMULATED
    assert result.estimated_saving.provenance is Provenance.SIMULATED


def test_a_baseline_mixing_actual_and_estimated_is_labelled_estimated() -> None:
    """The weaker label wins; a blend must not be overclaimed as measured."""
    result = simulate(
        sim_input(), baseline(actual_cost=60.0, estimated_cost=40.0), {}
    )
    assert result.current_cost.provenance is Provenance.ESTIMATED
    assert result.current_cost.amount == pytest.approx(100.0)


# ===========================================================================
# Forecast
# ===========================================================================
def test_forecast_projects_the_measured_rate_onto_the_assumed_volume() -> None:
    # 100.0 over 1000 requests = 0.10 each; 2000 requests projects to 200.
    result = simulate(sim_input(request_volume=2000), baseline(), {})
    assert result.forecast_cost.amount == pytest.approx(200.0)


def test_no_baseline_traffic_makes_the_forecast_unavailable() -> None:
    """A window with no requests has no rate. Projecting zero would claim a
    free future rather than an unknown one."""
    result = simulate(sim_input(), baseline(actual_cost=0.0, total_requests=0), {})

    assert result.forecast_cost.amount is None
    assert result.forecast_cost.provenance is Provenance.UNAVAILABLE
    assert any("no per-request rate" in a for a in result.assumptions)


# ===========================================================================
# Unpriced models are never free
# ===========================================================================
def test_an_unpriced_model_makes_the_optimized_cost_unavailable() -> None:
    """The defect this guards: treating unknown pricing as zero invents a saving."""
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("m1", 100.0),)),
        baseline(),
        {"m1": model(id="m1")},  # no pricing
    )

    assert result.optimized_cost.amount is None
    assert result.optimized_cost.provenance is Provenance.UNAVAILABLE
    assert result.unpriced_model_ids == ("m1",)
    assert any("not free" in a for a in result.assumptions)


def test_a_partly_unpriced_mix_is_still_uncomputable() -> None:
    """Dropping the unpriced half would make the mix look cheap."""
    result = simulate(
        sim_input(
            model_mix=(ModelMixEntry("cheap", 50.0), ModelMixEntry("unknown", 50.0))
        ),
        baseline(),
        {"cheap": priced("cheap", 0.02), "unknown": model(id="unknown")},
    )

    assert result.optimized_cost.amount is None
    assert result.unpriced_model_ids == ("unknown",)


def test_a_model_missing_from_the_registry_counts_as_unpriced() -> None:
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("ghost", 100.0),)), baseline(), {}
    )
    assert result.unpriced_model_ids == ("ghost",)
    assert result.optimized_cost.amount is None


def test_no_mix_yields_no_optimized_cost() -> None:
    result = simulate(sim_input(model_mix=()), baseline(), {})
    assert result.optimized_cost.amount is None
    assert any("No model mix supplied" in a for a in result.assumptions)


# ===========================================================================
# Optimized cost and savings
# ===========================================================================
def test_optimized_cost_blends_the_mix_by_share() -> None:
    result = simulate(
        sim_input(
            request_volume=1000,
            model_mix=(ModelMixEntry("a", 50.0), ModelMixEntry("b", 50.0)),
        ),
        baseline(),
        {"a": priced("a", 0.10), "b": priced("b", 0.02)},
    )
    # blended rate = 0.10*0.5 + 0.02*0.5 = 0.06 -> 1000 requests = 60
    assert result.optimized_cost.amount == pytest.approx(60.0)


def test_saving_is_forecast_minus_optimized() -> None:
    result = simulate(
        sim_input(request_volume=1000, model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),  # rate 0.10 -> forecast 100
        {"a": priced("a", 0.04)},  # optimized 40
    )
    assert result.estimated_saving.amount == pytest.approx(60.0)
    assert result.estimated_saving_percent == pytest.approx(60.0)


def test_a_more_expensive_mix_reports_a_negative_saving() -> None:
    """An increase is shown as a negative saving, not clamped to zero."""
    result = simulate(
        sim_input(request_volume=1000, model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),  # forecast 100
        {"a": priced("a", 0.25)},  # optimized 250
    )
    assert result.estimated_saving.amount == pytest.approx(-150.0)


def test_saving_is_unavailable_when_either_side_is_unknown() -> None:
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),
        {"a": model(id="a")},  # unpriced -> no optimized cost
    )
    assert result.estimated_saving.amount is None
    assert result.estimated_saving_percent is None


# ===========================================================================
# Quality impact
# ===========================================================================
def test_quality_impact_is_null_without_a_baseline() -> None:
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),
        {"a": priced("a", 0.02, quality_score=0.9)},
        baseline_quality=None,
    )
    assert result.quality_impact_percent is None


def test_quality_impact_is_null_when_a_model_is_unmeasured() -> None:
    """Unknown quality is never reported as "no change" — that would present an
    unmeasured risk as a safe one."""
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 50.0), ModelMixEntry("b", 50.0))),
        baseline(),
        {
            "a": priced("a", 0.02, quality_score=0.9),
            "b": priced("b", 0.02, quality_score=None),
        },
        baseline_quality=0.8,
    )
    assert result.quality_impact_percent is None


def test_quality_impact_is_computed_when_everything_is_measured() -> None:
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),
        {"a": priced("a", 0.02, quality_score=0.9)},
        baseline_quality=1.0,
    )
    # 0.9 vs 1.0 -> a 10% drop
    assert result.quality_impact_percent == pytest.approx(-10.0)


# ===========================================================================
# Risk
# ===========================================================================
def test_risk_is_the_highest_in_the_mix() -> None:
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 50.0), ModelMixEntry("b", 50.0))),
        baseline(),
        {
            "a": priced("a", 0.02, risk_level="LOW"),
            "b": priced("b", 0.02, risk_level="HIGH"),
        },
    )
    assert result.risk_level is RiskLevel.HIGH


def test_an_unclassified_model_is_medium_risk_not_low() -> None:
    """Treating unknowns as safe is how a risky change gets waved through."""
    result = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),
        {"a": priced("a", 0.02, risk_level=None)},
    )
    assert result.risk_level is RiskLevel.MEDIUM


# ===========================================================================
# Budget
# ===========================================================================
def test_within_budget_is_true_when_the_simulated_cost_fits() -> None:
    result = simulate(
        sim_input(
            request_volume=1000,
            budget_amount=100.0,
            model_mix=(ModelMixEntry("a", 100.0),),
        ),
        baseline(),
        {"a": priced("a", 0.05)},  # 50
    )
    assert result.within_budget is True


def test_within_budget_is_false_when_it_does_not() -> None:
    result = simulate(
        sim_input(
            request_volume=1000,
            budget_amount=10.0,
            model_mix=(ModelMixEntry("a", 100.0),),
        ),
        baseline(),
        {"a": priced("a", 0.05)},  # 50
    )
    assert result.within_budget is False


def test_within_budget_is_null_when_either_side_is_unknown() -> None:
    """Null, not False: an unknown cost has not been shown to exceed anything."""
    no_budget = simulate(
        sim_input(model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),
        {"a": priced("a", 0.05)},
    )
    no_cost = simulate(
        sim_input(budget_amount=100.0, model_mix=(ModelMixEntry("a", 100.0),)),
        baseline(),
        {"a": model(id="a")},
    )
    assert no_budget.within_budget is None
    assert no_cost.within_budget is None


# ===========================================================================
# It changes nothing
# ===========================================================================
def test_simulation_is_pure_and_repeatable() -> None:
    """Same inputs, same output — a simulation someone acts on must be
    reproducible, and nothing may be mutated along the way."""
    mix = (ModelMixEntry("a", 100.0),)
    registry = {"a": priced("a", 0.03)}
    base = baseline()

    first = simulate(sim_input(model_mix=mix), base, registry)
    second = simulate(sim_input(model_mix=mix), base, registry)

    assert first == second
    assert base.actual_cost == 100.0  # untouched
    assert registry["a"].input_cost == pytest.approx(0.015)


def test_every_result_states_that_it_is_not_a_commitment() -> None:
    result = simulate(sim_input(), baseline(), {})
    assert any("not a commitment" in a for a in result.assumptions)
