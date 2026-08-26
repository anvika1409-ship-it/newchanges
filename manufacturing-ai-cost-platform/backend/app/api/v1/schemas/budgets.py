"""Budget request and response schemas.

Mirrors ``Budget``, ``BudgetList``, ``BudgetCreate``, ``BudgetUpdate``,
``BudgetStatus`` and ``BudgetStatusList`` in API_CONTRACT.yaml.

Two contract notes worth knowing:

* ``BudgetCreate.currency`` is listed under ``required`` *and* carries
  ``default: INR``. Required wins — the field is mandatory here. That is also
  the safer reading: a budget silently defaulting to a currency the platform
  does not aggregate in would be reported as unevaluable rather than enforced.
* ``BudgetStatus.threshold_state`` was ``[OK, WARNING, CRITICAL]``. It is now
  ``[NORMAL, WARNING, CRITICAL, EXCEEDED]``, an intentional contract change
  recorded in API_CONTRACT.yaml. The old enum had no way to say a budget was
  spent, so an exceeded budget would have been reported as merely CRITICAL.

``scope_type`` here is the *stored* set of seven values. REQUEST is not among
them: request-level limits are not budgets (DATABASE_SCHEMA.md section 12).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.governance import Budget
from app.services.budget_service import BudgetStatusResult

#: The seven values `budgets.scope_type` accepts, matching the table's CHECK
#: constraint and the contract's enum.
ScopeTypeLiteral = Literal[
    "ENTERPRISE", "TENANT", "PLANT", "DEPARTMENT", "WORKLOAD", "AGENT", "MODEL"
]
PeriodLiteral = Literal["DAILY", "MONTHLY", "QUARTERLY", "ANNUAL"]
ThresholdStateLiteral = Literal["NORMAL", "WARNING", "CRITICAL", "EXCEEDED"]


class PageInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    limit: int
    offset: int


class BudgetResponse(BaseModel):
    """Contract schema ``Budget``."""

    model_config = ConfigDict(frozen=True)

    id: str
    scope_type: str
    scope_id: str
    amount: float
    currency: str
    period: str
    warning_threshold_percent: float
    critical_threshold_percent: float
    #: The row's lifecycle status (ACTIVE / INACTIVE / EXCEEDED), not its
    #: threshold state. ``BudgetStatus.threshold_state`` carries that.
    status: str

    @classmethod
    def from_row(cls, budget: Budget) -> BudgetResponse:
        return cls(
            id=budget.id,
            scope_type=budget.scope_type,
            scope_id=budget.scope_id,
            amount=budget.amount,
            currency=budget.currency,
            period=budget.period,
            warning_threshold_percent=budget.warning_threshold_percent,
            critical_threshold_percent=budget.critical_threshold_percent,
            status=budget.status,
        )


class BudgetListResponse(BaseModel):
    """Contract schema ``BudgetList``."""

    model_config = ConfigDict(frozen=True)

    items: list[BudgetResponse] = Field(default_factory=list)
    page: PageInfo


class BudgetCreateRequest(BaseModel):
    """Contract schema ``BudgetCreate``.

    ``tenant_id`` is deliberately absent. It is derived from the authenticated
    principal, never accepted from the client (SECURITY.md section 5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_type: ScopeTypeLiteral
    scope_id: str = Field(min_length=1)
    #: Strictly positive: the table's CHECK constraint is ``amount > 0``, and a
    #: zero-amount budget would block every request at its scope.
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    period: PeriodLiteral
    warning_threshold_percent: float = Field(default=80.0, gt=0, le=100)
    critical_threshold_percent: float = Field(default=95.0, gt=0, le=100)


class BudgetUpdateRequest(BaseModel):
    """Contract schema ``BudgetUpdate``.

    Every field is optional; only those supplied are changed. ``scope_type`` and
    ``scope_id`` are not updatable — repointing an existing budget at another
    scope would silently rewrite the history of both.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: float | None = Field(default=None, gt=0)
    period: PeriodLiteral | None = None
    warning_threshold_percent: float | None = Field(default=None, gt=0, le=100)
    critical_threshold_percent: float | None = Field(default=None, gt=0, le=100)
    status: Literal["ACTIVE", "INACTIVE", "EXCEEDED"] | None = None
    #: Recorded on the audit event for this change (SECURITY.md section 16).
    reason: str | None = None


class BudgetStatusResponse(BaseModel):
    """Contract schema ``BudgetStatus``."""

    model_config = ConfigDict(frozen=True)

    budget_id: str
    scope_type: str
    scope_id: str
    amount: float
    consumed_actual_cost: float
    consumed_estimated_cost: float
    consumed_percent: float
    currency: str
    #: ``None`` when the budget could not be compared against spend at all — for
    #: example a currency the configured conversion policy cannot reach. A null
    #: state is not a passing state.
    threshold_state: ThresholdStateLiteral | None = None
    #: Machine-readable cause when ``threshold_state`` is null.
    unevaluable_reason: str | None = None

    @classmethod
    def from_result(cls, result: BudgetStatusResult) -> BudgetStatusResponse:
        budget = result.budget
        return cls(
            budget_id=budget.id,
            scope_type=budget.scope_type,
            scope_id=budget.scope_id,
            amount=budget.amount,
            consumed_actual_cost=result.consumed_actual_cost,
            consumed_estimated_cost=result.consumed_estimated_cost,
            consumed_percent=result.consumed_percent,
            currency=budget.currency,
            threshold_state=result.state,
            unevaluable_reason=result.unevaluable_reason,
        )


class BudgetStatusListResponse(BaseModel):
    """Contract schema ``BudgetStatusList``."""

    model_config = ConfigDict(frozen=True)

    items: list[BudgetStatusResponse] = Field(default_factory=list)
    page: PageInfo
