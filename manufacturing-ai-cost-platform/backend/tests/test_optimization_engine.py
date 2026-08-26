"""Unit tests for the Optimization Engine (STEP 15).

Tests cover:
- Generation of candidate strategies across all 6 dimensions:
  1. model_routing
  2. context_reduction
  3. agent_reduction
  4. tool_call_reduction
  5. workload_scheduling
  6. model_mix
- Multi-objective scoring with priority weighting (NORMAL, HIGH, CRITICAL, LOW)
- Strict governance: proposed policy is marked PENDING_APPROVAL and never activated
- Output schema verification
"""

from __future__ import annotations

import pytest

from app.optimization.engine import (
    OptimizationAnalysisResult,
    OptimizationCandidate,
    OptimizationEngine,
)


@pytest.fixture
def engine() -> OptimizationEngine:
    return OptimizationEngine()


class TestOptimizationEngine:
    """Test suite for the multi-objective Optimization Engine."""

    def test_generates_all_six_candidate_strategies(self, engine: OptimizationEngine) -> None:
        """The engine must evaluate all 6 required candidate optimization strategy types."""
        result = engine.analyze(
            workload_id="predictive_maintenance",
            historical_cost=2000.0,
            business_priority="NORMAL",
        )

        assert isinstance(result, OptimizationAnalysisResult)
        assert len(result.all_candidates) == 6

        strategy_types = {c.strategy_type for c in result.all_candidates}
        expected_types = {
            "model_routing",
            "context_reduction",
            "agent_reduction",
            "tool_call_reduction",
            "workload_scheduling",
            "model_mix",
        }
        assert strategy_types == expected_types

    def test_multi_objective_scoring_normal_priority(self, engine: OptimizationEngine) -> None:
        """Under normal priority, model_routing or model_mix with high savings and low quality drop wins."""
        result = engine.analyze(
            workload_id="predictive_maintenance",
            historical_cost=1000.0,
            business_priority="NORMAL",
        )

        assert result.estimated_saving > 0.0
        assert result.estimated_saving_percent > 0.0
        assert len(result.recommended_strategy) > 0
        assert len(result.reasoning) > 0
        assert result.risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

        # Candidates are properly ordered by composite score
        scores = [c.composite_score for c in result.all_candidates]
        assert scores == sorted(scores, reverse=True)

    def test_critical_priority_penalizes_latency_and_quality_drop(
        self, engine: OptimizationEngine
    ) -> None:
        """Under CRITICAL priority, strategies with higher risk or latency delays are penalized."""
        result_critical = engine.analyze(
            workload_id="vision_inspection",
            historical_cost=1500.0,
            business_priority="CRITICAL",
        )

        # Off-peak scheduling introduces batch latency and should have a lower score than low-latency strategies
        candidates_by_type = {c.strategy_type: c for c in result_critical.all_candidates}
        assert (
            candidates_by_type["model_routing"].composite_score
            > candidates_by_type["workload_scheduling"].composite_score
        )

    def test_policy_proposal_never_activated_automatically(
        self, engine: OptimizationEngine
    ) -> None:
        """Governance enforcement: proposed policy is strictly proposals-only."""
        result = engine.analyze(
            workload_id="predictive_maintenance",
            historical_cost=3000.0,
        )

        policy = result.proposed_policy
        assert isinstance(policy, dict)
        assert policy["status"] == "PENDING_APPROVAL"
        assert policy["status"] != "ACTIVE"
        assert policy["requires_approval"] is True
        assert policy["estimated_monthly_saving_usd"] > 0
        assert "policy_id" in policy

    def test_result_structure_completeness(self, engine: OptimizationEngine) -> None:
        """Verify all fields required by prompt and architecture contract are present."""
        result = engine.analyze(
            workload_id="quality_control",
            historical_cost=800.0,
            current_strategy="PRIMARY (claude-3-5-sonnet)",
        )

        assert result.current_strategy == "PRIMARY (claude-3-5-sonnet)"
        assert isinstance(result.recommended_strategy, str)
        assert isinstance(result.estimated_saving, float)
        assert isinstance(result.estimated_saving_percent, float)
        assert isinstance(result.quality_impact, float)
        assert isinstance(result.latency_impact, float)
        assert isinstance(result.risk, str)
        assert isinstance(result.reasoning, str)
        assert isinstance(result.proposed_policy, dict)
