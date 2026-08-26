"""Currency conversion policy.

DATABASE_SCHEMA.md section 15 states the rule this module implements:

    All aggregated reporting is computed in a single configurable platform base
    currency. A cost event recorded in another currency must be converted to the
    base currency before aggregation. The conversion policy is configuration,
    not business logic.

So conversion is a lookup in configured rates, never a fetch from a rate feed
and never a guess. When no rate is configured for a currency pair, the converter
says so rather than returning a number.

Refusing to convert is the point. Summing 100 INR into a USD total because no
rate was configured would silently misreport spend by roughly two orders of
magnitude, and the resulting figure would look entirely plausible. A budget that
cannot be compared is reported as unevaluable and reaches a human
(``BudgetLimit.unevaluable_reason``); a cost event that cannot be converted is
counted and reported separately rather than folded into the total.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConversionUnavailableError(Exception):
    """No configured rate reaches the target currency.

    Carries the pair so an operator can see exactly which rate to configure.
    """

    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target
        super().__init__(
            f"No configured conversion rate from {source} to {target}. "
            "Add it to CURRENCY_RATES; rates are configuration and are never "
            "inferred."
        )


def normalise_currency(code: str | None) -> str | None:
    """Upper-case an ISO currency code, or ``None`` when absent."""
    if not code:
        return None
    cleaned = code.strip().upper()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class CurrencyConverter:
    """Converts amounts using configured rates.

    Args:
        base_currency: the platform's reporting currency.
        rates: multipliers *into* the base currency, keyed by source code.
            ``{"INR": 0.012}`` means one INR is 0.012 of the base. The base
            currency itself never needs an entry.

    Conversion is exact decimal arithmetic so an aggregate does not shift with
    the order rows were summed in.
    """

    base_currency: str
    rates: dict[str, float] | None = None

    def can_convert(self, source: str | None) -> bool:
        normalised = normalise_currency(source)
        if normalised is None:
            return False
        if normalised == self.base_currency:
            return True
        return normalised in (self.rates or {})

    def to_base(self, amount: float, source: str | None) -> float:
        """Convert ``amount`` into the base currency.

        Raises:
            ConversionUnavailableError: when no rate is configured for the pair.
                Deliberately an exception rather than a fallback: a caller must
                decide what to do about an unconvertible figure, not receive a
                fabricated one.
        """
        normalised = normalise_currency(source)
        if normalised is None:
            raise ConversionUnavailableError("UNKNOWN", self.base_currency)
        if normalised == self.base_currency:
            return amount

        rate = (self.rates or {}).get(normalised)
        if rate is None:
            logger.warning(
                "currency_rate_not_configured",
                extra={"source_currency": normalised, "base_currency": self.base_currency},
            )
            raise ConversionUnavailableError(normalised, self.base_currency)

        return float(Decimal(str(amount)) * Decimal(str(rate)))
