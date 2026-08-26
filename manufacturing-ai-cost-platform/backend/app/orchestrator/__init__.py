"""Runtime routing — the Cost-Aware Orchestrator.

AI_DEVELOPMENT_RULES.md section 30 assigns runtime routing to this module.

The orchestrator decides before expensive execution occurs: classify, budget,
route, guard, plan — then execute (ARCHITECTURE.md sections 4 and 6). No LLM is
called to make a routing decision; classification is arithmetic and selection is
a sort over model-registry metadata.
"""

from app.orchestrator.budget_gate import (
    BudgetEvaluator,
    NullBudgetEvaluator,
    RepositoryBudgetEvaluator,
    StaticBudgetEvaluator,
)
from app.orchestrator.classification import (
    BusinessPriority,
    ClassificationInput,
    Complexity,
    ComplexityThresholds,
    RiskLevel,
    classify_complexity,
    determine_risk,
)
from app.orchestrator.orchestrator import (
    BudgetBlockedError,
    CostAwareOrchestrator,
    ExecutionResult,
    NoCompatibleModelError,
    OrchestrationRequest,
)
from app.orchestrator.plan import ExecutionPlan

__all__ = [
    "BudgetBlockedError",
    "BudgetEvaluator",
    "BusinessPriority",
    "ClassificationInput",
    "Complexity",
    "ComplexityThresholds",
    "CostAwareOrchestrator",
    "ExecutionPlan",
    "ExecutionResult",
    "NoCompatibleModelError",
    "NullBudgetEvaluator",
    "OrchestrationRequest",
    "RepositoryBudgetEvaluator",
    "RiskLevel",
    "StaticBudgetEvaluator",
    "classify_complexity",
    "determine_risk",
]
