"""Cost engine.

Turns token usage plus configured pricing into a cost breakdown with an honest
provenance label. Pure and deterministic: no I/O, no clock, no randomness. The
same inputs always produce the same output, which is what makes cost reporting
reproducible and testable (this feature's acceptance criterion).

The governing rule, from AI_DEVELOPMENT_RULES.md section 10 and
DATABASE_SCHEMA.md section 15:

    **Never fabricate an actual cost.**

Concretely that means all of the following:

* A cost is ACTUAL only when it is computed from token counts the *provider
  reported* and from pricing that is *configured*. Anything else is at best
  ESTIMATED.
* Missing pricing does not become zero. It becomes UNAVAILABLE, and no total is
  reported at all — a total that silently omits the model's own cost would
  understate spend, which is worse than reporting nothing.
* Any estimated component drags the whole result down to ESTIMATED. Tool cost
  comes from ``tools.estimated_cost`` (DATABASE_SCHEMA.md section 11.1) and
  infrastructure cost is an allocation; neither is ever an observed charge, so
  a total containing them cannot be ACTUAL.

Provenance forms a lattice — ``ACTUAL > ESTIMATED > UNAVAILABLE`` — and the
total takes the weakest label of the components it contains. Degrading is
always allowed; upgrading never is.

Arithmetic uses ``Decimal`` rather than ``float``. Token-level rates produce
very small numbers, and binary floating point makes the result depend on the
order terms are added. Exact decimal arithmetic keeps the engine reproducible.
Values cross into the database as floats because ``cost_events`` stores REAL
(DATABASE_SCHEMA.md section 15).

Pricing units are not documented by GenAILab (see ``app/db/seed`` — every seeded
model has null pricing), so this module recognises an explicit, closed set of
units and treats anything else as unusable. That is deliberate: guessing what
"per 1K tokens" meant for a model priced per million would misreport spend by
a factor of a thousand.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Working precision for cost arithmetic. Token rates run to several decimal
#: places before multiplication, so results are quantized well below the
#: smallest currency unit and rounded for display, never for storage.
COST_QUANTUM = Decimal("0.0000000001")


class Provenance(StrEnum):
    """Where a cost figure came from (DATABASE_SCHEMA.md section 15)."""

    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


#: Weakest-wins ordering. A total is labelled with the lowest rank among the
#: components it contains.
_PROVENANCE_RANK: dict[Provenance, int] = {
    Provenance.ACTUAL: 2,
    Provenance.ESTIMATED: 1,
    Provenance.UNAVAILABLE: 0,
}


def weakest(*values: Provenance) -> Provenance:
    """The least trustworthy provenance among ``values``.

    Used to combine components. ``ACTUAL`` survives only when every contributing
    component is itself ACTUAL.
    """
    if not values:
        return Provenance.UNAVAILABLE
    return min(values, key=lambda value: _PROVENANCE_RANK[value])


class UsageSource(StrEnum):
    """Whether token counts were observed or predicted.

    This is the distinction that decides ACTUAL vs ESTIMATED, so it is a
    required, explicit input rather than something inferred. A caller that does
    not know must say ``ESTIMATED``.
    """

    #: Returned by the provider in its usage payload after execution.
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    #: Counted or predicted locally, typically before execution.
    ESTIMATED = "ESTIMATED"


class CostUnit(StrEnum):
    """Recognised pricing units for ``models.cost_unit``.

    A closed set on purpose. An unrecognised unit makes pricing unusable rather
    than being assumed to mean one of these.
    """

    # noqa on each: the linter's hardcoded-password heuristic fires on the
    # substring "token", which here means an LLM token, not a credential.
    PER_TOKEN = "per_token"  # noqa: S105
    PER_1K_TOKENS = "per_1k_tokens"  # noqa: S105
    PER_1M_TOKENS = "per_1m_tokens"  # noqa: S105


#: How many tokens one unit of the configured rate covers.
_TOKENS_PER_UNIT: dict[CostUnit, Decimal] = {
    CostUnit.PER_TOKEN: Decimal(1),
    CostUnit.PER_1K_TOKENS: Decimal(1000),
    CostUnit.PER_1M_TOKENS: Decimal(1_000_000),
}


def parse_cost_unit(value: str | None) -> CostUnit | None:
    """Map a stored ``cost_unit`` string onto a recognised unit, or ``None``.

    Comparison is case-insensitive and tolerates hyphens, because the column is
    operator-entered free text. It does not tolerate an unknown unit.
    """
    if not value:
        return None
    normalised = value.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return CostUnit(normalised)
    except ValueError:
        logger.info("cost_unit_unrecognised", extra={"cost_unit": value})
        return None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts for one execution.

    ``None`` means "not known", which is not the same as zero. A null input
    count makes the input cost unavailable; a genuine zero costs nothing.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    source: UsageSource = UsageSource.ESTIMATED

    @property
    def is_complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Configured rates for one model (DATABASE_SCHEMA.md section 11).

    Rates are configuration supplied by an operator. They are never inferred
    from a model's name (AI_DEVELOPMENT_RULES.md section 5, ARCHITECTURE.md
    section 8).
    """

    input_cost: float | None
    output_cost: float | None
    cost_unit: str | None
    currency: str

    @property
    def unit(self) -> CostUnit | None:
        return parse_cost_unit(self.cost_unit)

    @property
    def is_usable(self) -> bool:
        """Both rates present *and* a recognised unit.

        A rate without its unit is not pricing — it is a number with no meaning.
        """
        return (
            self.input_cost is not None
            and self.output_cost is not None
            and self.unit is not None
        )

    @classmethod
    def from_registry_entry(cls, entry: Any, *, currency: str) -> ModelPricing:
        """Read pricing off a ``ModelRegistryEntry``.

        Typed loosely so the engine does not import the ORM layer; the cost
        engine stays a pure computation that a test can drive with a stub.
        """
        return cls(
            input_cost=getattr(entry, "input_cost", None),
            output_cost=getattr(entry, "output_cost", None),
            cost_unit=getattr(entry, "cost_unit", None),
            currency=currency,
        )


@dataclass(frozen=True, slots=True)
class ToolUsage:
    """Tool invocations billed at the registry's configured estimate.

    ``tools.estimated_cost`` is named "estimated" in DATABASE_SCHEMA.md section
    11.1 and is treated as such: any tool cost included in a total caps that
    total's provenance at ESTIMATED.
    """

    calls: int = 0
    estimated_cost_per_call: float | None = None

    @property
    def is_priced(self) -> bool:
        return self.calls > 0 and self.estimated_cost_per_call is not None


@dataclass(frozen=True, slots=True)
class CostComputation:
    """The engine's result.

    ``total_cost`` is ``None`` whenever the model cost could not be computed.
    Components that *are* known are still reported, so an operator can see what
    was missing, but they are never presented as a total.
    """

    input_cost: float | None
    output_cost: float | None
    tool_cost: float | None
    infrastructure_cost: float | None
    total_cost: float | None
    currency: str
    provenance: Provenance
    #: Machine-readable cause when provenance is UNAVAILABLE. ``None`` otherwise.
    unavailable_reason: str | None = None

    @property
    def actual_cost(self) -> float | None:
        """The value to store in ``cost_events.actual_cost``.

        Populated only for an ACTUAL result. Any other provenance leaves the
        column null rather than writing an estimate into an "actual" field.
        """
        return self.total_cost if self.provenance is Provenance.ACTUAL else None

    @property
    def estimated_cost(self) -> float | None:
        """The value to store in ``cost_events.estimated_cost``."""
        return self.total_cost if self.provenance is Provenance.ESTIMATED else None


def _to_decimal(value: float | int | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):  # pragma: no cover - guarded upstream
        logger.warning("cost_value_not_numeric")
        return None


def _quantize(value: Decimal) -> float:
    return float(value.quantize(COST_QUANTUM))


def _token_cost(
    tokens: int | None, rate: float | None, unit: CostUnit
) -> Decimal | None:
    """cost = tokens / tokens-per-unit * rate."""
    if tokens is None or rate is None:
        return None
    token_decimal = Decimal(tokens)
    rate_decimal = _to_decimal(rate)
    if rate_decimal is None:
        return None
    return (token_decimal / _TOKENS_PER_UNIT[unit]) * rate_decimal


def compute_cost(
    usage: TokenUsage,
    pricing: ModelPricing,
    *,
    tools: ToolUsage | None = None,
    infrastructure_cost: float | None = None,
) -> CostComputation:
    """Compute the cost of one execution.

    Args:
        usage: token counts and whether the provider reported them.
        pricing: configured rates for the model that ran.
        tools: tool invocations, billed at the registry's estimated rate.
        infrastructure_cost: an allocation of shared infrastructure spend, when
            the deployment has one. Always treated as an estimate — an
            allocation is a division of a bill, not an observed charge.

    Returns:
        A ``CostComputation``. ``total_cost`` is ``None`` when the model cost
        could not be computed; the caller must not substitute zero.

    Provenance:
        ``ACTUAL`` requires provider-reported tokens, usable pricing, and no
        estimated component. Otherwise ``ESTIMATED`` when the model cost is
        computable, and ``UNAVAILABLE`` when it is not.
    """
    tools = tools or ToolUsage()
    currency = pricing.currency

    tool_cost = _tool_cost(tools)
    infrastructure = _to_decimal(infrastructure_cost)

    unit = pricing.unit
    if not pricing.is_usable or unit is None:
        # No usable pricing: components that are known are still surfaced, but
        # there is no total to report and nothing is invented in its place.
        return CostComputation(
            input_cost=None,
            output_cost=None,
            tool_cost=_quantize(tool_cost) if tool_cost is not None else None,
            infrastructure_cost=(
                _quantize(infrastructure) if infrastructure is not None else None
            ),
            total_cost=None,
            currency=currency,
            provenance=Provenance.UNAVAILABLE,
            unavailable_reason=_pricing_gap(pricing),
        )

    input_cost = _token_cost(usage.input_tokens, pricing.input_cost, unit)
    output_cost = _token_cost(usage.output_tokens, pricing.output_cost, unit)

    if input_cost is None or output_cost is None:
        # Pricing is configured but the usage is not fully known. Reporting a
        # partial total would understate the request's cost.
        return CostComputation(
            input_cost=_quantize(input_cost) if input_cost is not None else None,
            output_cost=_quantize(output_cost) if output_cost is not None else None,
            tool_cost=_quantize(tool_cost) if tool_cost is not None else None,
            infrastructure_cost=(
                _quantize(infrastructure) if infrastructure is not None else None
            ),
            total_cost=None,
            currency=currency,
            provenance=Provenance.UNAVAILABLE,
            unavailable_reason="incomplete_token_usage",
        )

    total = input_cost + output_cost
    if tool_cost is not None:
        total += tool_cost
    if infrastructure is not None:
        total += infrastructure

    provenance = _resolve_provenance(
        usage_source=usage.source,
        has_tool_cost=tool_cost is not None,
        has_infrastructure=infrastructure is not None,
    )

    return CostComputation(
        input_cost=_quantize(input_cost),
        output_cost=_quantize(output_cost),
        tool_cost=_quantize(tool_cost) if tool_cost is not None else None,
        infrastructure_cost=(
            _quantize(infrastructure) if infrastructure is not None else None
        ),
        total_cost=_quantize(total),
        currency=currency,
        provenance=provenance,
        unavailable_reason=None,
    )


def _tool_cost(tools: ToolUsage) -> Decimal | None:
    if not tools.is_priced:
        return None
    rate = _to_decimal(tools.estimated_cost_per_call)
    if rate is None:
        return None
    return Decimal(tools.calls) * rate


def _resolve_provenance(
    *, usage_source: UsageSource, has_tool_cost: bool, has_infrastructure: bool
) -> Provenance:
    """Combine the provenance of every contributing component.

    Tool cost and infrastructure allocation are estimates by definition, so
    either one present caps the total at ESTIMATED however the tokens were
    obtained.
    """
    token_provenance = (
        Provenance.ACTUAL
        if usage_source is UsageSource.PROVIDER_REPORTED
        else Provenance.ESTIMATED
    )
    components = [token_provenance]
    if has_tool_cost:
        components.append(Provenance.ESTIMATED)
    if has_infrastructure:
        components.append(Provenance.ESTIMATED)
    return weakest(*components)


def _pricing_gap(pricing: ModelPricing) -> str:
    """Name the specific missing piece, for operator diagnostics."""
    if pricing.input_cost is None or pricing.output_cost is None:
        return "pricing_not_configured"
    return "cost_unit_unrecognised"
