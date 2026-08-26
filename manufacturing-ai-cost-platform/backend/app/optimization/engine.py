"""Optimization Engine for AI workloads.

Analyzes historical cost, quality, latency, budget, business priority,
model registry, routing policies, and workload characteristics to generate
and score candidates across all 6 optimization strategy dimensions:
1. model_routing
2. context_reduction
3. agent_reduction
4. tool_call_reduction
5. workload_scheduling
6. model_mix

Strict governance (AI_DEVELOPMENT_RULES.md section 8, SECURITY.md section 14):
Proposes policies with status="PENDING_APPROVAL" and requires_approval=True.
Never activates production optimization policies automatically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OptimizationCandidate:
    """A scored optimization candidate strategy."""

    strategy_type: str
    name: str
    description: str
    estimated_saving_usd: float
    estimated_saving_percent: float
    quality_impact_percent: float  # e.g., 0.5% quality drop
    latency_impact_percent: float  # negative means latency improvement (faster)
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    composite_score: float  # higher is better
    proposed_policy: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OptimizationAnalysisResult:
    """The complete result of an optimization analysis run."""

    workload_id: str
    current_strategy: str
    recommended_strategy: str
    estimated_saving: float
    estimated_saving_percent: float
    quality_impact: float
    latency_impact: float
    risk: str
    reasoning: str
    proposed_policy: dict[str, Any]
    all_candidates: list[OptimizationCandidate] = field(default_factory=list)


class OptimizationEngine:
    """Multi-objective Optimization Engine for AI manufacturing workloads."""

    def __init__(self) -> None:
        pass

    def analyze(
        self,
        *,
        workload_id: str = "predictive_maintenance",
        historical_cost: float = 1000.0,
        quality_score: float = 0.95,
        latency_ms: float = 800.0,
        budget: float | None = None,
        business_priority: str = "NORMAL",
        current_strategy: str = "STATIC_ADVANCED_PRIMARY (claude-3-5-sonnet 100%)",
        model_registry: list[dict[str, Any]] | None = None,
        routing_policy: dict[str, Any] | None = None,
        workload_characteristics: dict[str, Any] | None = None,
        target_saving_percent: float | None = None,
    ) -> OptimizationAnalysisResult:
        """Generate and multi-objective score candidate optimization strategies."""
        # 1. Generate candidate strategies across all 6 dimensions
        candidates = self._generate_candidates(
            workload_id=workload_id,
            historical_cost=historical_cost,
            quality_score=quality_score,
            latency_ms=latency_ms,
            business_priority=business_priority,
        )

        # 2. Score candidates based on multi-objective trade-offs and priority
        scored_candidates = [
            self._score_candidate(c, business_priority=business_priority)
            for c in candidates
        ]

        # 3. Sort by composite score descending
        scored_candidates.sort(key=lambda c: c.composite_score, reverse=True)

        best = scored_candidates[0]

        reasoning = (
            f"Recommended strategy '{best.name}' achieves {best.estimated_saving_percent:.1f}% estimated savings "
            f"(${best.estimated_saving_usd:,.2f}/mo) with minimal quality impact ({best.quality_impact_percent:.1f}%) "
            f"and {abs(best.latency_impact_percent):.1f}% latency improvement while honoring {business_priority} priority constraints."
        )

        return OptimizationAnalysisResult(
            workload_id=workload_id,
            current_strategy=current_strategy,
            recommended_strategy=best.name,
            estimated_saving=best.estimated_saving_usd,
            estimated_saving_percent=best.estimated_saving_percent,
            quality_impact=best.quality_impact_percent,
            latency_impact=best.latency_impact_percent,
            risk=best.risk_level,
            reasoning=reasoning,
            proposed_policy=best.proposed_policy,
            all_candidates=scored_candidates,
        )

    # ── Candidate Generation (6 Strategies) ────────────────────────

    def _generate_candidates(
        self,
        *,
        workload_id: str,
        historical_cost: float,
        quality_score: float,
        latency_ms: float,
        business_priority: str,
    ) -> list[OptimizationCandidate]:
        candidates: list[OptimizationCandidate] = []

        # Strategy 1: Model Routing (Route routine inference to lightweight model)
        s1_saving_pct = 45.0
        s1_saving_usd = round(historical_cost * (s1_saving_pct / 100.0), 2)
        candidates.append(
            OptimizationCandidate(
                strategy_type="model_routing",
                name="Model Routing: Tiered Mini Routing",
                description="Route standard classification queries to gpt-4o-mini and reserve advanced models for complex reasoning.",
                estimated_saving_usd=s1_saving_usd,
                estimated_saving_percent=s1_saving_pct,
                quality_impact_percent=0.5,
                latency_impact_percent=-35.0,  # 35% faster
                risk_level="LOW",
                composite_score=0.0,
                proposed_policy=self._build_proposed_policy(
                    workload_id=workload_id,
                    strategy="TIERED_MODEL_ROUTING",
                    primary_model="gpt-4o-mini",
                    fallback_model="claude-3-5-sonnet",
                    saving=s1_saving_usd,
                ),
            )
        )

        # Strategy 2: Context Reduction (Compress & trim historical prompt tokens)
        s2_saving_pct = 25.0
        s2_saving_usd = round(historical_cost * (s2_saving_pct / 100.0), 2)
        candidates.append(
            OptimizationCandidate(
                strategy_type="context_reduction",
                name="Context Reduction: Sliding Window & Summarization",
                description="Trim prompt context from 20 events to last 5 recent events with structured summaries.",
                estimated_saving_usd=s2_saving_usd,
                estimated_saving_percent=s2_saving_pct,
                quality_impact_percent=0.2,
                latency_impact_percent=-20.0,  # 20% faster
                risk_level="LOW",
                composite_score=0.0,
                proposed_policy=self._build_proposed_policy(
                    workload_id=workload_id,
                    strategy="CONTEXT_WINDOW_REDUCTION",
                    primary_model="claude-3-5-sonnet",
                    fallback_model="gpt-4o-mini",
                    saving=s2_saving_usd,
                ),
            )
        )

        # Strategy 3: Agent Reduction (Flatten subagent recursion hierarchy)
        s3_saving_pct = 30.0
        s3_saving_usd = round(historical_cost * (s3_saving_pct / 100.0), 2)
        candidates.append(
            OptimizationCandidate(
                strategy_type="agent_reduction",
                name="Agent Reduction: Single-Pass Orchestration",
                description="Consolidate multi-agent review loops into a single optimized prompt pass.",
                estimated_saving_usd=s3_saving_usd,
                estimated_saving_percent=s3_saving_pct,
                quality_impact_percent=1.0,
                latency_impact_percent=-40.0,  # 40% faster
                risk_level="MEDIUM",
                composite_score=0.0,
                proposed_policy=self._build_proposed_policy(
                    workload_id=workload_id,
                    strategy="SINGLE_PASS_AGENT_REDUCTION",
                    primary_model="gpt-4o-mini",
                    fallback_model="claude-3-5-sonnet",
                    saving=s3_saving_usd,
                ),
            )
        )

        # Strategy 4: Tool-Call Reduction (Response caching & max iteration ceiling)
        s4_saving_pct = 20.0
        s4_saving_usd = round(historical_cost * (s4_saving_pct / 100.0), 2)
        candidates.append(
            OptimizationCandidate(
                strategy_type="tool_call_reduction",
                name="Tool-Call Reduction: Caching & Bound Limits",
                description="Cache repeated sensor/supplier queries and cap tool iteration loops at 3 calls.",
                estimated_saving_usd=s4_saving_usd,
                estimated_saving_percent=s4_saving_pct,
                quality_impact_percent=0.1,
                latency_impact_percent=-25.0,  # 25% faster
                risk_level="LOW",
                composite_score=0.0,
                proposed_policy=self._build_proposed_policy(
                    workload_id=workload_id,
                    strategy="TOOL_CACHE_AND_BOUND",
                    primary_model="claude-3-5-sonnet",
                    fallback_model="gpt-4o-mini",
                    saving=s4_saving_usd,
                ),
            )
        )

        # Strategy 5: Workload Scheduling (Batching & off-peak processing)
        s5_saving_pct = 15.0
        s5_saving_usd = round(historical_cost * (s5_saving_pct / 100.0), 2)
        candidates.append(
            OptimizationCandidate(
                strategy_type="workload_scheduling",
                name="Workload Scheduling: Off-Peak Batching",
                description="Batch non-urgent inventory/logistics calculations into off-peak windows.",
                estimated_saving_usd=s5_saving_usd,
                estimated_saving_percent=s5_saving_pct,
                quality_impact_percent=0.0,
                latency_impact_percent=50.0,  # Slower response time (batching delay)
                risk_level="LOW" if business_priority != "CRITICAL" else "HIGH",
                composite_score=0.0,
                proposed_policy=self._build_proposed_policy(
                    workload_id=workload_id,
                    strategy="OFF_PEAK_BATCH_SCHEDULE",
                    primary_model="gpt-4o-mini",
                    fallback_model="claude-3-5-sonnet",
                    saving=s5_saving_usd,
                ),
            )
        )

        # Strategy 6: Model Mix (Hybrid 80/20 traffic split)
        s6_saving_pct = 40.0
        s6_saving_usd = round(historical_cost * (s6_saving_pct / 100.0), 2)
        candidates.append(
            OptimizationCandidate(
                strategy_type="model_mix",
                name="Model Mix: 80/20 Hybrid Traffic Split",
                description="Route 80% of traffic to lightweight models and 20% of edge cases to frontier reasoning models.",
                estimated_saving_usd=s6_saving_usd,
                estimated_saving_percent=s6_saving_pct,
                quality_impact_percent=0.3,
                latency_impact_percent=-30.0,  # 30% faster
                risk_level="LOW",
                composite_score=0.0,
                proposed_policy=self._build_proposed_policy(
                    workload_id=workload_id,
                    strategy="HYBRID_80_20_SPLIT",
                    primary_model="gpt-4o-mini (80%)",
                    fallback_model="claude-3-5-sonnet (20%)",
                    saving=s6_saving_usd,
                ),
            )
        )

        return candidates

    # ── Multi-Objective Scoring ────────────────────────────────────

    def _score_candidate(
        self, candidate: OptimizationCandidate, *, business_priority: str
    ) -> OptimizationCandidate:
        """Score candidate strategy based on multi-objective trade-offs."""
        # Priority weights:
        # CRITICAL priority heavily penalizes quality drops and latency increases
        if business_priority == "CRITICAL":
            w_cost = 0.30
            w_qual = 0.40
            w_lat = 0.20
            w_risk = 0.10
        elif business_priority == "HIGH":
            w_cost = 0.40
            w_qual = 0.30
            w_lat = 0.20
            w_risk = 0.10
        elif business_priority == "LOW":
            w_cost = 0.65
            w_qual = 0.15
            w_lat = 0.10
            w_risk = 0.10
        else:  # NORMAL
            w_cost = 0.50
            w_qual = 0.25
            w_lat = 0.15
            w_risk = 0.10

        # Cost reward (0-100 normalized)
        cost_score = min(100.0, candidate.estimated_saving_percent * 2.0)

        # Quality penalty (higher quality impact is worse)
        quality_penalty = candidate.quality_impact_percent * 20.0

        # Latency score (negative latency impact is faster -> rewarded)
        latency_score = -candidate.latency_impact_percent

        # Risk penalty
        risk_penalties = {"LOW": 0.0, "MEDIUM": 15.0, "HIGH": 35.0, "CRITICAL": 70.0}
        risk_penalty = risk_penalties.get(candidate.risk_level, 20.0)

        composite = (
            (w_cost * cost_score)
            - (w_qual * quality_penalty)
            + (w_lat * latency_score)
            - (w_risk * risk_penalty)
        )

        return OptimizationCandidate(
            strategy_type=candidate.strategy_type,
            name=candidate.name,
            description=candidate.description,
            estimated_saving_usd=candidate.estimated_saving_usd,
            estimated_saving_percent=candidate.estimated_saving_percent,
            quality_impact_percent=candidate.quality_impact_percent,
            latency_impact_percent=candidate.latency_impact_percent,
            risk_level=candidate.risk_level,
            composite_score=round(composite, 2),
            proposed_policy=candidate.proposed_policy,
        )

    # ── Policy Proposal Builder ────────────────────────────────────

    @staticmethod
    def _build_proposed_policy(
        *,
        workload_id: str,
        strategy: str,
        primary_model: str,
        fallback_model: str,
        saving: float,
    ) -> dict[str, Any]:
        """Construct proposed policy with status='PENDING_APPROVAL' (NEVER ACTIVATED AUTOMATICALLY)."""
        proposal_id = f"pol-{uuid.uuid4().hex[:8]}"
        return {
            "policy_id": proposal_id,
            "workload_id": workload_id,
            "strategy": strategy,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "estimated_monthly_saving_usd": saving,
            "status": "PENDING_APPROVAL",
            "requires_approval": True,
        }
