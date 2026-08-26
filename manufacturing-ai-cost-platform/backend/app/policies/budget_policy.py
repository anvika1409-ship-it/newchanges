"""Budget policy: states, outcomes and precedence.

Deterministic, server-side and pure — no I/O, no clock, no LLM. SECURITY.md
section 13 and AI_DEVELOPMENT_RULES.md section 11 both require this: a budget
rule must be deterministic, and an LLM can never override one. Everything here
is a function of its arguments, so the same inputs always yield the same
decision (this feature's acceptance criterion).

Two separate things live here, and conflating them is the usual mistake:

``BudgetState``
    How full a budget is *right now*. NORMAL / WARNING / CRITICAL / EXCEEDED,
    derived from consumption against the thresholds stored on the budget row.
``PolicyOutcome``
    What to *do* about a request. ALLOW / DOWNGRADE / REQUIRE_APPROVAL / BLOCK,
    from SECURITY.md section 13.

A state is an observation; an outcome is a decision. The mapping between them is
policy, which SECURITY.md deliberately leaves to the deployment ("according to
policy"), so it is a configurable ``BudgetPolicy`` with a documented default
rather than a hard-coded ``if`` chain.

Precedence
----------

Several budgets can apply to one request at once — an enterprise budget, the
plant's, the workload's, the model's. They are all evaluated and **the most
restrictive outcome wins**. A narrower budget can never loosen a broader one,
and a broader one can never loosen a narrower one. Ties break on a fixed scope
order and then on budget id, so the deciding budget is reproducible rather than
whichever row the database happened to return first.

Request-level limits
--------------------

The caller-facing list of budget scopes includes "request", but a request limit
is deliberately *not* a ``budgets`` row: DATABASE_SCHEMA.md section 12 and
SECURITY.md section 13 both state that request-level limits live on
``routing_policies.max_cost_per_request`` and on ``AIExecutionRequest.max_cost``.
Adding REQUEST to the ``budgets.scope_type`` CHECK constraint would contradict
the schema document. It is modelled here as ``RequestLimit`` and takes part in
the same precedence chain, so it is enforced alongside the stored budgets
without inventing a database value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class BudgetState(StrEnum):
    """How full a budget is.

    Boundaries are inclusive at the lower edge: consumption exactly equal to the
    warning threshold is WARNING, not NORMAL. A threshold that did not fire when
    reached would not be a threshold.
    """

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EXCEEDED = "EXCEEDED"


class PolicyOutcome(StrEnum):
    """What to do about a request (SECURITY.md section 13)."""

    ALLOW = "ALLOW"
    DOWNGRADE = "DOWNGRADE"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


#: Severity order for composing several budgets. Higher is more restrictive.
_OUTCOME_SEVERITY: dict[PolicyOutcome, int] = {
    PolicyOutcome.ALLOW: 0,
    PolicyOutcome.DOWNGRADE: 1,
    PolicyOutcome.REQUIRE_APPROVAL: 2,
    PolicyOutcome.BLOCK: 3,
}


class BudgetScope(StrEnum):
    """Budget scopes from DATABASE_SCHEMA.md section 12, plus REQUEST.

    REQUEST is not persisted in ``budgets`` — see the module docstring. It is
    included here so precedence covers every limit that can apply to a request.
    """

    REQUEST = "REQUEST"
    MODEL = "MODEL"
    AGENT = "AGENT"
    WORKLOAD = "WORKLOAD"
    DEPARTMENT = "DEPARTMENT"
    PLANT = "PLANT"
    TENANT = "TENANT"
    ENTERPRISE = "ENTERPRISE"


#: Scopes that ``budgets.scope_type`` actually stores. REQUEST is absent by
#: design; the CHECK constraint on the table lists exactly these seven.
PERSISTED_SCOPES: frozenset[BudgetScope] = frozenset(
    {
        BudgetScope.ENTERPRISE,
        BudgetScope.TENANT,
        BudgetScope.PLANT,
        BudgetScope.DEPARTMENT,
        BudgetScope.WORKLOAD,
        BudgetScope.AGENT,
        BudgetScope.MODEL,
    }
)

#: Tie-break order, narrowest first. Only consulted when two budgets produce the
#: same outcome severity, purely so the *reported* deciding budget is stable.
_SCOPE_PRECEDENCE: tuple[BudgetScope, ...] = (
    BudgetScope.REQUEST,
    BudgetScope.MODEL,
    BudgetScope.AGENT,
    BudgetScope.WORKLOAD,
    BudgetScope.DEPARTMENT,
    BudgetScope.PLANT,
    BudgetScope.TENANT,
    BudgetScope.ENTERPRISE,
)

_SCOPE_RANK: dict[BudgetScope, int] = {
    scope: index for index, scope in enumerate(_SCOPE_PRECEDENCE)
}


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """How budget states translate into decisions.

        ASSUMPTION. SECURITY.md section 13 lists the four outcomes and says they
        apply "according to policy", without stating which state produces which
        outcome. These defaults are this platform's starting policy, not a quote
        from a source-of-truth document. They are collected here so a deployment
        can change them in one place.

    The defaults:

    * NORMAL, WARNING -> ALLOW. A warning is for humans to see, not a reason to
      degrade a production request.
    * CRITICAL -> DOWNGRADE. Close to the limit, so prefer a cheaper approved
      strategy rather than refusing work outright.
    * EXCEEDED -> BLOCK. The limit is spent.

    ``require_approval_on_projected_breach`` covers the case the state alone
    cannot express: the budget is not yet exceeded, but *this request* would
    push it over. Blocking outright would be harsh for the request that happens
    to arrive at the boundary, and allowing it silently defeats the limit, so a
    human decides. This is the path that produces the contract's 202 response
    for ``/ai/execute``.
    """

    outcome_by_state: dict[BudgetState, PolicyOutcome] = field(
        default_factory=lambda: {
            BudgetState.NORMAL: PolicyOutcome.ALLOW,
            BudgetState.WARNING: PolicyOutcome.ALLOW,
            BudgetState.CRITICAL: PolicyOutcome.DOWNGRADE,
            BudgetState.EXCEEDED: PolicyOutcome.BLOCK,
        }
    )
    require_approval_on_projected_breach: bool = True

    def outcome_for(self, state: BudgetState) -> PolicyOutcome:
        """Outcome for a state, defaulting to BLOCK if a state is unmapped.

        Failing closed: an incomplete policy must not become permission.
        """
        return self.outcome_by_state.get(state, PolicyOutcome.BLOCK)


DEFAULT_BUDGET_POLICY = BudgetPolicy()


@dataclass(frozen=True, slots=True)
class BudgetLimit:
    """One limit under evaluation, whether stored or request-level.

    ``consumed_*`` are the spend already recorded in the budget's period.
    Actual and estimated are kept apart all the way through and are only
    combined for the consumption figure, where excluding estimates would
    understate how full the budget is.
    """

    scope: BudgetScope
    scope_id: str
    amount: float
    currency: str
    consumed_actual_cost: float = 0.0
    consumed_estimated_cost: float = 0.0
    warning_threshold_percent: float = 80.0
    critical_threshold_percent: float = 95.0
    budget_id: str | None = None
    #: Set when spend could not be compared against the limit — for example a
    #: budget denominated in a currency the configured conversion policy cannot
    #: reach. Such a limit is never silently skipped.
    unevaluable_reason: str | None = None

    @property
    def consumed_total(self) -> float:
        """Spend counted against the limit.

        Estimated spend counts. A budget that only tracked confirmed charges
        would report headroom that does not exist, because the estimate is
        frequently all the platform will ever have (AI_DEVELOPMENT_RULES.md
        section 10).
        """
        return self.consumed_actual_cost + self.consumed_estimated_cost


@dataclass(frozen=True, slots=True)
class BudgetEvaluation:
    """The verdict for one limit."""

    scope: BudgetScope
    scope_id: str
    budget_id: str | None
    amount: float
    currency: str
    consumed_actual_cost: float
    consumed_estimated_cost: float
    consumed_percent: float
    projected_percent: float
    state: BudgetState | None
    outcome: PolicyOutcome
    reason: str

    @property
    def is_evaluable(self) -> bool:
        return self.state is not None


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """The composed verdict across every applicable limit."""

    outcome: PolicyOutcome
    #: The evaluation that produced ``outcome``. ``None`` only when no limit
    #: applied at all.
    deciding: BudgetEvaluation | None
    evaluations: tuple[BudgetEvaluation, ...]

    @property
    def reason(self) -> str:
        return self.deciding.reason if self.deciding else "no_budget_configured"


def _percent(consumed: float, amount: float) -> float:
    """Consumption as a percentage, computed exactly.

    ``Decimal`` keeps a value that lands exactly on a threshold from drifting to
    one side of it through binary rounding — which would make a boundary case
    non-reproducible, and boundary cases are precisely where budgets act.
    """
    if amount <= 0:
        # Guarded by a CHECK constraint on the table; a zero-amount limit would
        # otherwise divide by zero and is treated as fully consumed.
        return 100.0
    return float((Decimal(str(consumed)) / Decimal(str(amount))) * Decimal(100))


def classify_state(
    consumed_percent: float,
    *,
    warning_threshold_percent: float,
    critical_threshold_percent: float,
) -> BudgetState:
    """Map a consumption percentage onto a budget state.

    Thresholds are inclusive lower bounds. ``EXCEEDED`` is fixed at 100% and is
    not configurable — spending more than the limit is what "exceeded" means,
    whatever the warning levels are set to.
    """
    if consumed_percent >= 100.0:
        return BudgetState.EXCEEDED
    if consumed_percent >= critical_threshold_percent:
        return BudgetState.CRITICAL
    if consumed_percent >= warning_threshold_percent:
        return BudgetState.WARNING
    return BudgetState.NORMAL


def evaluate_limit(
    limit: BudgetLimit,
    *,
    request_cost: float = 0.0,
    policy: BudgetPolicy = DEFAULT_BUDGET_POLICY,
) -> BudgetEvaluation:
    """Evaluate one limit against current spend plus this request's cost.

    Args:
        limit: the limit and the spend already recorded against it.
        request_cost: estimated cost of the request being authorized. Zero when
            reporting status rather than authorizing a request.
        policy: the state-to-outcome mapping to apply.

    Returns:
        A ``BudgetEvaluation``. A limit that cannot be compared carries
        ``state=None`` and ``REQUIRE_APPROVAL`` — never ``ALLOW``, because a
        limit nobody can evaluate must not silently become permission.
    """
    consumed_percent = _percent(limit.consumed_total, limit.amount)
    projected_percent = _percent(limit.consumed_total + request_cost, limit.amount)

    if limit.unevaluable_reason is not None:
        return BudgetEvaluation(
            scope=limit.scope,
            scope_id=limit.scope_id,
            budget_id=limit.budget_id,
            amount=limit.amount,
            currency=limit.currency,
            consumed_actual_cost=limit.consumed_actual_cost,
            consumed_estimated_cost=limit.consumed_estimated_cost,
            consumed_percent=consumed_percent,
            projected_percent=projected_percent,
            state=None,
            outcome=PolicyOutcome.REQUIRE_APPROVAL,
            reason=limit.unevaluable_reason,
        )

    state = classify_state(
        consumed_percent,
        warning_threshold_percent=limit.warning_threshold_percent,
        critical_threshold_percent=limit.critical_threshold_percent,
    )
    outcome = policy.outcome_for(state)
    reason = f"budget_{state.lower()}"

    # Already-exceeded stays BLOCK; there is nothing left to approve against.
    projected_breach = (
        policy.require_approval_on_projected_breach
        and state is not BudgetState.EXCEEDED
        and projected_percent >= 100.0
    )
    if projected_breach:
        outcome = PolicyOutcome.REQUIRE_APPROVAL
        reason = "budget_projected_breach"

    return BudgetEvaluation(
        scope=limit.scope,
        scope_id=limit.scope_id,
        budget_id=limit.budget_id,
        amount=limit.amount,
        currency=limit.currency,
        consumed_actual_cost=limit.consumed_actual_cost,
        consumed_estimated_cost=limit.consumed_estimated_cost,
        consumed_percent=consumed_percent,
        projected_percent=projected_percent,
        state=state,
        outcome=outcome,
        reason=reason,
    )


def _decision_sort_key(evaluation: BudgetEvaluation) -> tuple[int, int, str]:
    """Most restrictive first, then narrowest scope, then id.

    The second and third components exist only to make the reported deciding
    budget deterministic when several produce the same outcome.
    """
    return (
        -_OUTCOME_SEVERITY[evaluation.outcome],
        _SCOPE_RANK.get(evaluation.scope, len(_SCOPE_PRECEDENCE)),
        evaluation.budget_id or evaluation.scope_id,
    )


def decide(
    limits: list[BudgetLimit],
    *,
    request_cost: float = 0.0,
    policy: BudgetPolicy = DEFAULT_BUDGET_POLICY,
) -> BudgetDecision:
    """Evaluate every applicable limit and compose one decision.

    The most restrictive outcome wins. With no limits configured the outcome is
    ALLOW: an absent budget is not a limit of zero, and refusing every request
    until someone configures a budget would be a denial of service rather than a
    control.

    ``evaluations`` is returned sorted most-restrictive-first, so a caller can
    show the deciding limit and the runners-up without re-sorting.
    """
    evaluations = [
        evaluate_limit(limit, request_cost=request_cost, policy=policy)
        for limit in limits
    ]
    if not evaluations:
        return BudgetDecision(
            outcome=PolicyOutcome.ALLOW, deciding=None, evaluations=()
        )

    ordered = sorted(evaluations, key=_decision_sort_key)
    deciding = ordered[0]
    return BudgetDecision(
        outcome=deciding.outcome, deciding=deciding, evaluations=tuple(ordered)
    )
