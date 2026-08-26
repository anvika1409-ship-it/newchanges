"""Budget policy tests.

Covers the required cases: threshold behaviour, multiple scopes, precedence,
exceeded budget, and the determinism acceptance criterion.

These are the rules that decide whether an AI execution is allowed to spend
money. Every assertion that something is *refused* is a control. If one fails,
fix the policy — never loosen the expectation.
"""

from __future__ import annotations

import pytest

from app.policies.budget_policy import (
    DEFAULT_BUDGET_POLICY,
    PERSISTED_SCOPES,
    BudgetLimit,
    BudgetPolicy,
    BudgetScope,
    BudgetState,
    PolicyOutcome,
    classify_state,
    decide,
    evaluate_limit,
)


def _limit(
    *,
    scope: BudgetScope = BudgetScope.TENANT,
    scope_id: str = "tenant-a",
    amount: float = 100.0,
    actual: float = 0.0,
    estimated: float = 0.0,
    warning: float = 80.0,
    critical: float = 95.0,
    budget_id: str | None = None,
    unevaluable: str | None = None,
) -> BudgetLimit:
    return BudgetLimit(
        scope=scope,
        scope_id=scope_id,
        amount=amount,
        currency="USD",
        consumed_actual_cost=actual,
        consumed_estimated_cost=estimated,
        warning_threshold_percent=warning,
        critical_threshold_percent=critical,
        budget_id=budget_id,
        unevaluable_reason=unevaluable,
    )


# ===========================================================================
# Threshold behaviour
# ===========================================================================
@pytest.mark.parametrize(
    ("consumed_percent", "expected"),
    [
        (0.0, BudgetState.NORMAL),
        (79.99, BudgetState.NORMAL),
        (80.0, BudgetState.WARNING),
        (94.99, BudgetState.WARNING),
        (95.0, BudgetState.CRITICAL),
        (99.99, BudgetState.CRITICAL),
        (100.0, BudgetState.EXCEEDED),
        (250.0, BudgetState.EXCEEDED),
    ],
)
def test_state_boundaries(consumed_percent: float, expected: BudgetState) -> None:
    """Thresholds are inclusive lower bounds.

    Exactly 80% is WARNING. A threshold that did not fire when reached would not
    be a threshold.
    """
    assert (
        classify_state(
            consumed_percent, warning_threshold_percent=80.0, critical_threshold_percent=95.0
        )
        is expected
    )


def test_exceeded_is_fixed_at_one_hundred_percent() -> None:
    """Not configurable: spending more than the limit is what "exceeded" means,
    whatever the warning levels happen to be."""
    state = classify_state(
        100.0, warning_threshold_percent=10.0, critical_threshold_percent=20.0
    )
    assert state is BudgetState.EXCEEDED


def test_custom_thresholds_are_honoured() -> None:
    assert (
        classify_state(
            55.0, warning_threshold_percent=50.0, critical_threshold_percent=60.0
        )
        is BudgetState.WARNING
    )


def test_estimated_spend_counts_towards_consumption() -> None:
    """A budget tracking only confirmed charges would report headroom that does
    not exist — the estimate is often all the platform will ever have."""
    evaluation = evaluate_limit(_limit(amount=100.0, actual=40.0, estimated=45.0))

    assert evaluation.consumed_percent == pytest.approx(85.0)
    assert evaluation.state is BudgetState.WARNING


def test_a_zero_amount_limit_is_treated_as_fully_consumed() -> None:
    """Guarded by a CHECK constraint, so this is defence in depth rather than a
    reachable state. It must not divide by zero or read as unlimited."""
    evaluation = evaluate_limit(_limit(amount=0.0, actual=0.0))
    assert evaluation.state is BudgetState.EXCEEDED


# ===========================================================================
# State to outcome
# ===========================================================================
@pytest.mark.parametrize(
    ("actual", "expected_state", "expected_outcome"),
    [
        (10.0, BudgetState.NORMAL, PolicyOutcome.ALLOW),
        (85.0, BudgetState.WARNING, PolicyOutcome.ALLOW),
        (96.0, BudgetState.CRITICAL, PolicyOutcome.DOWNGRADE),
        (120.0, BudgetState.EXCEEDED, PolicyOutcome.BLOCK),
    ],
)
def test_default_policy_mapping(
    actual: float, expected_state: BudgetState, expected_outcome: PolicyOutcome
) -> None:
    evaluation = evaluate_limit(_limit(actual=actual))
    assert evaluation.state is expected_state
    assert evaluation.outcome is expected_outcome


def test_an_exceeded_budget_blocks() -> None:
    """The headline case: the limit is spent, so the request does not run."""
    decision = decide([_limit(amount=100.0, actual=100.01)])

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.deciding is not None
    assert decision.deciding.state is BudgetState.EXCEEDED
    assert decision.reason == "budget_exceeded"


def test_an_unmapped_state_fails_closed() -> None:
    """An incomplete policy must not become permission."""
    policy = BudgetPolicy(outcome_by_state={BudgetState.NORMAL: PolicyOutcome.ALLOW})
    evaluation = evaluate_limit(_limit(actual=99.0), policy=policy)

    assert evaluation.state is BudgetState.CRITICAL
    assert evaluation.outcome is PolicyOutcome.BLOCK


def test_the_state_to_outcome_mapping_is_configurable() -> None:
    """SECURITY.md section 13 says "according to policy", so a deployment can
    choose to block at CRITICAL rather than downgrade."""
    strict = BudgetPolicy(
        outcome_by_state={
            BudgetState.NORMAL: PolicyOutcome.ALLOW,
            BudgetState.WARNING: PolicyOutcome.DOWNGRADE,
            BudgetState.CRITICAL: PolicyOutcome.BLOCK,
            BudgetState.EXCEEDED: PolicyOutcome.BLOCK,
        }
    )
    assert evaluate_limit(_limit(actual=85.0), policy=strict).outcome is (
        PolicyOutcome.DOWNGRADE
    )


# ===========================================================================
# Projected breach -> REQUIRE_APPROVAL
# ===========================================================================
def test_a_request_that_would_breach_requires_approval() -> None:
    """Below the limit now, over it if this request runs.

    Blocking would be harsh for whichever request arrives at the boundary;
    allowing silently defeats the limit. A human decides — this is the path that
    produces the contract's 202 for /ai/execute.
    """
    decision = decide([_limit(amount=100.0, actual=50.0)], request_cost=60.0)

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.reason == "budget_projected_breach"


def test_a_request_that_fits_does_not_require_approval() -> None:
    decision = decide([_limit(amount=100.0, actual=50.0)], request_cost=10.0)
    assert decision.outcome is PolicyOutcome.ALLOW


def test_an_already_exceeded_budget_blocks_rather_than_asking() -> None:
    """There is nothing left to approve against."""
    decision = decide([_limit(amount=100.0, actual=150.0)], request_cost=1.0)
    assert decision.outcome is PolicyOutcome.BLOCK


def test_projected_breach_can_be_disabled() -> None:
    policy = BudgetPolicy(require_approval_on_projected_breach=False)
    decision = decide(
        [_limit(amount=100.0, actual=50.0)], request_cost=60.0, policy=policy
    )
    assert decision.outcome is PolicyOutcome.ALLOW


# ===========================================================================
# Multiple scopes and precedence
# ===========================================================================
def test_the_most_restrictive_outcome_wins() -> None:
    """A roomy enterprise budget must not rescue an exhausted plant budget."""
    decision = decide(
        [
            _limit(scope=BudgetScope.ENTERPRISE, scope_id="ent", amount=1_000_000.0, actual=1.0),
            _limit(scope=BudgetScope.TENANT, scope_id="tenant-a", amount=1000.0, actual=10.0),
            _limit(scope=BudgetScope.PLANT, scope_id="plant-1", amount=100.0, actual=100.0),
        ]
    )

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.deciding is not None
    assert decision.deciding.scope is BudgetScope.PLANT


def test_a_narrow_budget_cannot_loosen_a_broader_one() -> None:
    """Precedence is severity, not specificity. An empty plant budget does not
    override an exhausted enterprise budget."""
    decision = decide(
        [
            _limit(scope=BudgetScope.ENTERPRISE, scope_id="ent", amount=100.0, actual=100.0),
            _limit(scope=BudgetScope.PLANT, scope_id="plant-1", amount=100.0, actual=0.0),
        ]
    )

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.deciding is not None
    assert decision.deciding.scope is BudgetScope.ENTERPRISE


def test_every_documented_scope_can_be_evaluated() -> None:
    """All eight scopes the caller asked for, including REQUEST."""
    limits = [
        _limit(scope=scope, scope_id=f"{scope.lower()}-1", amount=100.0, actual=10.0)
        for scope in BudgetScope
    ]
    decision = decide(limits)

    assert len(decision.evaluations) == 8
    assert decision.outcome is PolicyOutcome.ALLOW
    assert {evaluation.scope for evaluation in decision.evaluations} == set(BudgetScope)


def test_request_scope_is_not_a_persisted_budget_scope() -> None:
    """DATABASE_SCHEMA.md section 12: request-level limits are not budgets.

    They live on routing_policies.max_cost_per_request and
    AIExecutionRequest.max_cost, which is why REQUEST is absent from the table's
    CHECK constraint.
    """
    assert BudgetScope.REQUEST not in PERSISTED_SCOPES
    assert len(PERSISTED_SCOPES) == 7
    assert {str(scope) for scope in PERSISTED_SCOPES} == {
        "ENTERPRISE",
        "TENANT",
        "PLANT",
        "DEPARTMENT",
        "WORKLOAD",
        "AGENT",
        "MODEL",
    }


def test_a_request_limit_participates_in_precedence() -> None:
    """A per-request ceiling is enforced alongside the stored budgets."""
    decision = decide(
        [
            _limit(scope=BudgetScope.TENANT, amount=1_000_000.0, actual=0.0),
            _limit(scope=BudgetScope.REQUEST, scope_id="routing_policy", amount=0.50),
        ],
        request_cost=0.75,
    )

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.deciding is not None
    assert decision.deciding.scope is BudgetScope.REQUEST


def test_no_budgets_configured_allows() -> None:
    """An absent budget is not a limit of zero. Refusing every request until
    someone configures one would be a denial of service, not a control."""
    decision = decide([])

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.deciding is None
    assert decision.reason == "no_budget_configured"


# ===========================================================================
# Unevaluable limits
# ===========================================================================
def test_an_unevaluable_limit_is_never_silently_allowed() -> None:
    """A budget nobody can compare against spend must not become permission."""
    decision = decide([_limit(unevaluable="currency_not_convertible")])

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.deciding is not None
    assert decision.deciding.state is None
    assert decision.reason == "currency_not_convertible"


def test_an_unevaluable_limit_still_loses_to_a_block() -> None:
    decision = decide(
        [
            _limit(scope=BudgetScope.TENANT, unevaluable="currency_not_convertible"),
            _limit(scope=BudgetScope.PLANT, scope_id="plant-1", amount=10.0, actual=20.0),
        ]
    )
    assert decision.outcome is PolicyOutcome.BLOCK


# ===========================================================================
# Acceptance: determinism
# ===========================================================================
def test_the_same_limits_produce_an_identical_decision() -> None:
    limits = [
        _limit(scope=BudgetScope.TENANT, amount=100.0, actual=33.33, estimated=11.11),
        _limit(scope=BudgetScope.PLANT, scope_id="plant-1", amount=50.0, actual=47.5),
    ]

    first = decide(limits, request_cost=1.23)
    second = decide(limits, request_cost=1.23)

    assert first == second
    assert first.evaluations == second.evaluations


def test_input_order_does_not_change_the_decision() -> None:
    """The database may return budget rows in any order."""
    a = _limit(scope=BudgetScope.ENTERPRISE, scope_id="ent", amount=1000.0, actual=1.0)
    b = _limit(scope=BudgetScope.PLANT, scope_id="plant-1", amount=100.0, actual=99.0)
    c = _limit(scope=BudgetScope.MODEL, scope_id="model-1", amount=100.0, actual=50.0)

    forwards = decide([a, b, c])
    backwards = decide([c, b, a])

    assert forwards.outcome is backwards.outcome
    assert forwards.deciding == backwards.deciding
    assert forwards.evaluations == backwards.evaluations


def test_ties_break_on_scope_then_id() -> None:
    """Two budgets producing the same outcome must still name the same winner
    every time, or telemetry would attribute the decision inconsistently."""
    narrow = _limit(scope=BudgetScope.MODEL, scope_id="model-1", amount=100.0, actual=100.0)
    broad = _limit(scope=BudgetScope.TENANT, scope_id="tenant-a", amount=100.0, actual=100.0)

    for limits in ([narrow, broad], [broad, narrow]):
        decision = decide(limits)
        assert decision.deciding is not None
        # Narrowest scope wins the tie.
        assert decision.deciding.scope is BudgetScope.MODEL


def test_percentages_are_exact_at_the_boundary() -> None:
    """Binary floating point can put a value that should land exactly on a
    threshold just below it, which would make a boundary case flap."""
    evaluation = evaluate_limit(_limit(amount=3.0, actual=2.4))
    assert evaluation.consumed_percent == 80.0
    assert evaluation.state is BudgetState.WARNING


def test_evaluations_are_returned_most_restrictive_first() -> None:
    decision = decide(
        [
            _limit(scope=BudgetScope.TENANT, amount=1000.0, actual=1.0),
            _limit(scope=BudgetScope.PLANT, scope_id="p", amount=100.0, actual=100.0),
            _limit(scope=BudgetScope.MODEL, scope_id="m", amount=100.0, actual=96.0),
        ]
    )
    outcomes = [evaluation.outcome for evaluation in decision.evaluations]
    assert outcomes == [
        PolicyOutcome.BLOCK,
        PolicyOutcome.DOWNGRADE,
        PolicyOutcome.ALLOW,
    ]


def test_default_policy_is_shared_and_unmutated() -> None:
    """A shared default that a caller could mutate would leak policy changes
    between requests."""
    assert DEFAULT_BUDGET_POLICY.outcome_for(BudgetState.EXCEEDED) is PolicyOutcome.BLOCK
    custom = BudgetPolicy()
    custom.outcome_by_state[BudgetState.EXCEEDED] = PolicyOutcome.ALLOW
    assert DEFAULT_BUDGET_POLICY.outcome_for(BudgetState.EXCEEDED) is PolicyOutcome.BLOCK
