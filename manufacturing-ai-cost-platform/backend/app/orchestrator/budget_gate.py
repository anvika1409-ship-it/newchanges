"""Budget evaluation for the orchestrator (step 8).

Adapts the deterministic policy in ``app.policies.budget_policy`` to the
orchestrator's needs: collect every limit that applies to this request, then let
the policy compose one decision.

Deliberately thin. The rules — which state maps to which outcome, how limits
compose — belong to the policy module and are not restated here. This only
decides *which* limits apply.

An LLM has no input into any of this (AI_DEVELOPMENT_RULES.md sections 11
and 18).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.policies.budget_policy import (
    BudgetDecision,
    BudgetLimit,
    BudgetScope,
    PolicyOutcome,
    decide,
)

logger = get_logger(__name__)


@runtime_checkable
class BudgetEvaluator(Protocol):
    """What the orchestrator needs from budget checking."""

    async def evaluate(
        self,
        *,
        tenant_id: str,
        plant_id: str | None = None,
        department_id: str | None = None,
        workload_id: str | None = None,
        estimated_cost: float = 0.0,
    ) -> BudgetDecision: ...


class RepositoryBudgetEvaluator:
    """Loads applicable budgets and evaluates them.

    Scopes are collected from most specific to least: workload, department,
    plant, tenant, enterprise. Every applicable limit is evaluated and the
    policy takes the most restrictive outcome, so a tenant-level block is not
    escaped by a comfortable plant-level budget.
    """

    #: Order matters only for logging; the policy composes by severity.
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def evaluate(
        self,
        *,
        tenant_id: str,
        plant_id: str | None = None,
        department_id: str | None = None,
        workload_id: str | None = None,
        estimated_cost: float = 0.0,
    ) -> BudgetDecision:
        scopes: list[tuple[BudgetScope, str]] = [(BudgetScope.TENANT, tenant_id)]
        if plant_id:
            scopes.append((BudgetScope.PLANT, plant_id))
        if department_id:
            scopes.append((BudgetScope.DEPARTMENT, department_id))
        if workload_id:
            scopes.append((BudgetScope.WORKLOAD, workload_id))

        limits: list[BudgetLimit] = []
        for scope, scope_id in scopes:
            try:
                limits.extend(
                    await self._load_limits(tenant_id, scope, scope_id)
                )
            except Exception:
                # A lookup failure must not silently become "no budget".
                # Logged and surfaced as an unevaluable limit, which the policy
                # treats conservatively rather than ignoring.
                logger.exception(
                    "budget_limit_lookup_failed",
                    extra={"scope": str(scope), "scope_id": scope_id},
                )
                limits.append(
                    BudgetLimit(
                        scope=scope,
                        scope_id=scope_id,
                        amount=0.0,
                        currency="",
                        unevaluable_reason="lookup_failed",
                    )
                )

        return decide(limits, request_cost=estimated_cost)

    async def _load_limits(
        self, tenant_id: str, scope: BudgetScope, scope_id: str
    ) -> list[BudgetLimit]:
        loader = getattr(self._repository, "limits_for_scope", None)
        if loader is None:
            return []
        return list(await loader(tenant_id=tenant_id, scope=str(scope), scope_id=scope_id))


class NullBudgetEvaluator:
    """Allows everything. For tests that are not about budget.

    Never used in the application wiring: a request that reaches production
    without a budget check is exactly what this platform exists to prevent.
    """

    async def evaluate(self, **_: Any) -> BudgetDecision:
        return BudgetDecision(
            outcome=PolicyOutcome.ALLOW, deciding=None, evaluations=()
        )


class StaticBudgetEvaluator:
    """Returns a fixed outcome. For exercising downgrade and block paths."""

    def __init__(self, outcome: PolicyOutcome) -> None:
        self._outcome = outcome

    async def evaluate(self, **_: Any) -> BudgetDecision:
        return BudgetDecision(outcome=self._outcome, deciding=None, evaluations=())
