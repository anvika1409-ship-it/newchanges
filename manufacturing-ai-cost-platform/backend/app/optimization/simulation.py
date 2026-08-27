"""What-if simulation.

Implements the What-if Simulation Workflow (AI_WORKFLOWS.md section 10): vary
volume, budget and model-mix assumptions, then compare current, forecast and
optimized cost.

Deterministic arithmetic over recorded telemetry and registry pricing. No model
is invoked and no LLM is consulted — asking a model to estimate a cost would
cost money to produce a number the registry already defines, and would not be
reproducible (AI_DEVELOPMENT_RULES.md sections 7, 17 and 43).

Three rules the whole module turns on:

* **Nothing here changes production.** A simulation creates no policy, approves
  nothing and activates nothing (SECURITY.md section 15).
* **Unknown is never zero.** A model with no registry pricing cannot be costed;
  it is reported in ``unpriced_model_ids`` rather than treated as free, which
  would understate the optimized cost and manufacture a saving.
* **Every figure carries its provenance.** Current spend is measured, forecast
  is projected, optimized is simulated, and they are never conflated
  (AI_DEVELOPMENT_RULES.md sections 41 and 42).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.logging import get_logger
from app.db.models.registry import ModelRegistryEntry

logger = get_logger(__name__)


class Provenance(StrEnum):
    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"
    FORECAST = "FORECAST"
    SIMULATED = "SIMULATED"
    UNAVAILABLE = "UNAVAILABLE"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_RISK_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


@dataclass(frozen=True, slots=True)
class Figure:
    """One monetary figure with its provenance attached.

    ``amount`` is None when the figure could not be computed. A None is not a
    zero, and the two must never be rendered the same way.
    """

    amount: float | None
    currency: str | None
    provenance: Provenance

    @classmethod
    def unavailable(cls) -> Figure:
        return cls(amount=None, currency=None, provenance=Provenance.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class ModelMixEntry:
    model_id: str
    share_percent: float


@dataclass(frozen=True, slots=True)
class SimulationInput:
    """The assumptions being simulated (AI_WORKFLOWS.md section 10)."""

    request_volume: int
    production_volume: int | None = None
    image_volume: int | None = None
    budget_amount: float | None = None
    model_mix: tuple[ModelMixEntry, ...] = ()
    horizon_days: int = 30
    workload_id: str | None = None


@dataclass(frozen=True, slots=True)
class Baseline:
    """Measured spend over the observed window.

    Supplied by the caller from recorded telemetry, so this module stays pure
    and testable without a database.
    """

    actual_cost: float
    estimated_cost: float
    total_requests: int
    currency: str

    @property
    def known_spend(self) -> float:
        """Spend the platform can account for."""
        return self.actual_cost + self.estimated_cost

    @property
    def cost_per_request(self) -> float | None:
        """Mean spend per request, or None when there is no traffic to average.

        None rather than 0.0: a window with no requests has no rate, and a zero
        would project a free future.
        """
        if self.total_requests <= 0:
            return None
        return self.known_spend / self.total_requests


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """The comparison. Every figure is labelled; none is a commitment."""

    horizon_days: int
    current_cost: Figure
    forecast_cost: Figure
    optimized_cost: Figure
    estimated_saving: Figure
    estimated_saving_percent: float | None
    quality_impact_percent: float | None
    risk_level: RiskLevel
    within_budget: bool | None
    unpriced_model_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    provenance: Provenance = Provenance.SIMULATED


def _blended_rate(
    mix: tuple[ModelMixEntry, ...],
    registry: dict[str, ModelRegistryEntry],
) -> tuple[float | None, tuple[str, ...], str | None]:
    """Weighted cost per request across the proposed mix.

    Returns ``(rate, unpriced_model_ids, cost_unit)``.

    A model with unknown pricing makes the whole blend uncomputable rather than
    contributing zero. Dropping it silently would make an unpriced mix look
    cheap and invent a saving that does not exist.
    """
    if not mix:
        return None, (), None

    unpriced: list[str] = []
    weighted = 0.0
    total_share = 0.0
    cost_unit: str | None = None

    for entry in mix:
        model = registry.get(entry.model_id)
        if model is None or not model.has_known_pricing:
            unpriced.append(entry.model_id)
            continue
        # input_cost + output_cost is the per-unit rate the registry defines.
        # The unit itself is registry metadata and is carried through rather
        # than assumed to be tokens.
        rate = (model.input_cost or 0.0) + (model.output_cost or 0.0)
        weighted += rate * (entry.share_percent / 100.0)
        total_share += entry.share_percent
        cost_unit = cost_unit or model.cost_unit

    if unpriced or total_share <= 0:
        return None, tuple(unpriced), cost_unit
    return weighted, (), cost_unit


def _quality_impact(
    mix: tuple[ModelMixEntry, ...],
    registry: dict[str, ModelRegistryEntry],
    baseline_quality: float | None,
) -> float | None:
    """Percentage change in weighted quality against the baseline.

    None when any model in the mix has no measured quality score, or when there
    is no baseline to compare against. Unknown quality is never reported as
    "no change" — that would present an unmeasured risk as a safe one.
    """
    if not mix or baseline_quality is None or baseline_quality <= 0:
        return None

    weighted = 0.0
    total_share = 0.0
    for entry in mix:
        model = registry.get(entry.model_id)
        if model is None or model.quality_score is None:
            return None
        weighted += model.quality_score * (entry.share_percent / 100.0)
        total_share += entry.share_percent

    if total_share <= 0:
        return None
    return ((weighted - baseline_quality) / baseline_quality) * 100.0


def _mix_risk(
    mix: tuple[ModelMixEntry, ...], registry: dict[str, ModelRegistryEntry]
) -> RiskLevel:
    """The highest risk level present in the mix.

    A model with no declared risk contributes MEDIUM, not LOW: an unclassified
    model is an unknown, and treating unknowns as safe is how a risky change
    gets waved through.
    """
    highest = RiskLevel.LOW
    for entry in mix:
        model = registry.get(entry.model_id)
        if model is None:
            continue
        declared = (model.risk_level or "").strip().upper()
        try:
            level = RiskLevel(declared)
        except ValueError:
            level = RiskLevel.MEDIUM
        if _RISK_ORDER.index(level) > _RISK_ORDER.index(highest):
            highest = level
    return highest


def simulate(
    simulation_input: SimulationInput,
    baseline: Baseline,
    registry: dict[str, ModelRegistryEntry],
    *,
    baseline_quality: float | None = None,
) -> SimulationResult:
    """Compare current, forecast and optimized cost under the given assumptions.

    Args:
        simulation_input: the assumptions being varied.
        baseline: measured spend over the observed window.
        registry: candidate models by id.
        baseline_quality: mean measured quality of current routing, if known.
    """
    assumptions: list[str] = []
    currency = baseline.currency

    # --- current: what has actually been spent -----------------------------
    current = Figure(
        amount=baseline.known_spend,
        currency=currency,
        # Measured, but a blend of recorded actuals and estimates, so it is
        # labelled as the weaker of the two rather than overclaimed as ACTUAL.
        provenance=(
            Provenance.ACTUAL if baseline.estimated_cost == 0 else Provenance.ESTIMATED
        ),
    )

    # --- forecast: current rate applied to the assumed volume --------------
    rate = baseline.cost_per_request
    if rate is None:
        forecast = Figure.unavailable()
        assumptions.append(
            "No recorded traffic in the baseline window, so no per-request rate "
            "could be derived and the forecast is unavailable."
        )
    else:
        forecast = Figure(
            amount=rate * simulation_input.request_volume,
            currency=currency,
            provenance=Provenance.FORECAST,
        )
        assumptions.append(
            f"Forecast projects the measured rate of {rate:.6f} {currency} per "
            f"request across {simulation_input.request_volume} requests."
        )

    # --- optimized: the proposed mix at registry pricing -------------------
    blended, unpriced, cost_unit = _blended_rate(simulation_input.model_mix, registry)
    if blended is None:
        optimized = Figure.unavailable()
        if unpriced:
            assumptions.append(
                "Optimized cost is unavailable: "
                f"{len(unpriced)} model(s) in the mix have no registry pricing. "
                "An unpriced model is not free, so no blended rate was computed."
            )
        elif not simulation_input.model_mix:
            assumptions.append("No model mix supplied, so no optimized cost was computed.")
    else:
        optimized = Figure(
            amount=blended * simulation_input.request_volume,
            currency=currency,
            provenance=Provenance.SIMULATED,
        )
        assumptions.append(
            f"Optimized cost applies the proposed mix at a blended rate of "
            f"{blended:.6f} {currency} per {cost_unit or 'unit'}."
        )

    # --- saving: only when both sides are known ----------------------------
    if forecast.amount is not None and optimized.amount is not None:
        saving_amount = forecast.amount - optimized.amount
        saving = Figure(
            amount=saving_amount,
            currency=currency,
            provenance=Provenance.SIMULATED,
        )
        saving_percent = (
            (saving_amount / forecast.amount) * 100.0 if forecast.amount else None
        )
    else:
        saving = Figure.unavailable()
        saving_percent = None
        assumptions.append(
            "Savings cannot be computed without both a forecast and an "
            "optimized cost."
        )

    # --- budget ------------------------------------------------------------
    within_budget: bool | None = None
    if simulation_input.budget_amount is not None and optimized.amount is not None:
        within_budget = optimized.amount <= simulation_input.budget_amount

    # --- volumes that inform but do not drive cost -------------------------
    if simulation_input.image_volume:
        assumptions.append(
            f"{simulation_input.image_volume} images assumed. Image handling is "
            "priced per request by the registry, not per image, so image volume "
            "is recorded but does not independently scale cost."
        )
    if simulation_input.production_volume:
        assumptions.append(
            f"{simulation_input.production_volume} production units assumed, for "
            "context only — cost scales with AI request volume."
        )

    assumptions.append(
        "Every figure is a projection, not a commitment. Applying any change "
        "requires the normal recommendation lifecycle: validation, approval and "
        "a versioned policy."
    )

    return SimulationResult(
        horizon_days=simulation_input.horizon_days,
        current_cost=current,
        forecast_cost=forecast,
        optimized_cost=optimized,
        estimated_saving=saving,
        estimated_saving_percent=saving_percent,
        quality_impact_percent=_quality_impact(
            simulation_input.model_mix, registry, baseline_quality
        ),
        risk_level=_mix_risk(simulation_input.model_mix, registry),
        within_budget=within_budget,
        unpriced_model_ids=unpriced,
        assumptions=tuple(assumptions),
    )
