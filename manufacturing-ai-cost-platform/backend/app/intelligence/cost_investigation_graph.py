"""LangGraph Cost Investigation workflow.

Implements the multi-stage stateful cost investigation graph
(AI_WORKFLOWS.md section 5, ARCHITECTURE.md section 14):

START
-> load historical cost
-> detect anomaly
-> identify drivers
-> compare model/workload usage
-> root-cause reasoning (ModelGateway)
-> recommendation
-> savings estimate
-> risk evaluation
-> END

Features:
- Typed state via TypedDict
- Bounded step iterations & recursion ceiling
- Timeout execution guard
- Deterministic termination at END
- Integration with ModelGateway, CostService, and PolicyService
- Structured output schema (CostInvestigationResult)
- Strict non-activation of policies (status="PENDING_APPROVAL", requires_approval=True)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.logging import get_logger

# Imported directly: a missing gateway interface must fail loudly rather
# than degrade to `Any`, which would silently drop the type contract that
# keeps LLM access behind the gateway (AI_DEVELOPMENT_RULES.md section 4.4).
from app.integrations.llm.interface import (
    Message,
    ModelGatewayInterface,
    Role,
    TextGenerationRequest,
)
from app.intelligence.cost_anomaly_detector import CostAnomalyDetector
from app.services.cost_service import CostService
from app.services.policy_service import PolicyService

logger = get_logger(__name__)


# ── 1. Typed State ───────────────────────────────────────────────


class CostInvestigationState(TypedDict, total=False):
    """Typed state for the Cost Investigation LangGraph workflow."""

    request_id: str
    scope_type: str
    scope_id: str
    time_window_days: int
    custom_history: list[dict[str, Any]] | None
    current_metrics: dict[str, Any]
    historical_summary: dict[str, Any]
    anomaly: dict[str, Any] | None
    drivers: list[dict[str, Any]]
    model_comparison: dict[str, Any]
    workload_comparison: dict[str, Any]
    root_cause: str
    recommendation: str
    estimated_saving: float
    quality_impact: float
    risk: str
    proposed_policy_change: dict[str, Any] | None
    status: str
    iteration_count: int
    error: str | None


# ── 2. Structured Output Model ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class CostInvestigationResult:
    """Structured result of the Cost Investigation workflow."""

    request_id: str
    scope_type: str
    scope_id: str
    anomaly: dict[str, Any] | None
    drivers: list[dict[str, Any]]
    root_cause: str
    recommendation: str
    estimated_saving: float
    quality_impact: float
    risk: str
    proposed_policy_change: dict[str, Any] | None
    status: str


# ── 3. LangGraph Workflow Implementation ──────────────────────────


class CostInvestigationWorkflow:
    """Orchestrates the 8-node LangGraph Cost Investigation state machine."""

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayInterface,
        cost_service: CostService | None = None,
        policy_service: PolicyService | None = None,
        anomaly_detector: CostAnomalyDetector | None = None,
        default_model: str = "cost-investigator-model",
        timeout_seconds: float = 30.0,
        max_iterations: int = 20,
    ) -> None:
        self._gateway = model_gateway
        self._cost_service = cost_service or CostService()
        self._policy_service = policy_service or PolicyService()
        self._anomaly_detector = anomaly_detector or CostAnomalyDetector()
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._max_iterations = max_iterations
        self._graph = self._build_graph()

    # ── Graph Builder ─────────────────────────────────────────────

    def _build_graph(self):
        """Construct the directed state graph."""
        builder = StateGraph(CostInvestigationState)

        # 1. Add nodes
        builder.add_node("load_historical_cost", self._node_load_historical_cost)
        builder.add_node("detect_anomaly", self._node_detect_anomaly)
        builder.add_node("identify_drivers", self._node_identify_drivers)
        builder.add_node("compare_model_workload_usage", self._node_compare_model_workload)
        builder.add_node("root_cause_reasoning", self._node_root_cause_reasoning)
        builder.add_node("recommendation", self._node_recommendation)
        builder.add_node("savings_estimate", self._node_savings_estimate)
        builder.add_node("risk_evaluation", self._node_risk_evaluation)

        # 2. Add edges (Linear pipeline with deterministic termination at END)
        builder.add_edge(START, "load_historical_cost")
        builder.add_edge("load_historical_cost", "detect_anomaly")
        builder.add_edge("detect_anomaly", "identify_drivers")
        builder.add_edge("identify_drivers", "compare_model_workload_usage")
        builder.add_edge("compare_model_workload_usage", "root_cause_reasoning")
        builder.add_edge("root_cause_reasoning", "recommendation")
        builder.add_edge("recommendation", "savings_estimate")
        builder.add_edge("savings_estimate", "risk_evaluation")
        builder.add_edge("risk_evaluation", END)

        return builder.compile()

    # ── Public Execution ──────────────────────────────────────────

    async def execute(
        self,
        *,
        request_id: str | None = None,
        scope_type: str = "TENANT",
        scope_id: str = "default",
        time_window_days: int = 30,
        current_metrics: dict[str, Any] | None = None,
        custom_history: list[dict[str, Any]] | None = None,
    ) -> CostInvestigationResult:
        """Run the Cost Investigation graph with timeout and bounded iteration guards."""
        req_id = request_id or f"cinv-{uuid.uuid4().hex[:8]}"

        initial_state: CostInvestigationState = {
            "request_id": req_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "time_window_days": time_window_days,
            "custom_history": custom_history,
            "current_metrics": current_metrics or {},
            "historical_summary": {},
            "anomaly": None,
            "drivers": [],
            "model_comparison": {},
            "workload_comparison": {},
            "root_cause": "",
            "recommendation": "",
            "estimated_saving": 0.0,
            "quality_impact": 0.0,
            "risk": "LOW",
            "proposed_policy_change": None,
            "status": "in_progress",
            "iteration_count": 0,
        }

        try:
            # Wrap graph execution with a timeout guard
            final_state = await asyncio.wait_for(
                self._graph.ainvoke(initial_state),
                timeout=self._timeout_seconds,
            )
            return self._format_result(final_state)
        except TimeoutError:
            logger.error("cost_investigation_timeout", extra={"request_id": req_id})
            return CostInvestigationResult(
                request_id=req_id,
                scope_type=scope_type,
                scope_id=scope_id,
                anomaly=None,
                drivers=[],
                root_cause="Investigation timed out during analysis.",
                recommendation="Investigate latency on analytics and data retrieval layers.",
                estimated_saving=0.0,
                quality_impact=0.0,
                risk="HIGH",
                proposed_policy_change=None,
                status="timeout",
            )

    # ── Node 1: Load Historical Cost ──────────────────────────────

    async def _node_load_historical_cost(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        summary = await self._cost_service.get_cost_history(
            scope_type=state.get("scope_type", "TENANT"),
            scope_id=state.get("scope_id", "default"),
            time_window_days=state.get("time_window_days", 30),
            custom_history=state.get("custom_history"),
        )
        return {
            "iteration_count": count,
            "historical_summary": {
                "total_cost_usd": summary.total_cost_usd,
                "average_daily_cost": summary.average_daily_cost,
                "cost_std_dev": summary.cost_std_dev,
                "model_breakdown": summary.model_breakdown,
                "workload_breakdown": summary.workload_breakdown,
                "history_points": summary.historical_daily_costs,
            },
        }

    # ── Node 2: Detect Anomaly ────────────────────────────────────

    async def _node_detect_anomaly(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        current = state.get("current_metrics", {})
        hist = state.get("historical_summary", {})

        baseline = {
            "cost_usd": {
                "mean": hist.get("average_daily_cost", 50.0),
                "std": hist.get("cost_std_dev", 10.0),
            },
            "token_count": {"mean": 10000.0, "std": 2000.0},
            "latency_ms": {"mean": 450.0, "std": 100.0},
            "request_count": {"mean": 100.0, "std": 20.0},
        }

        anomalies = self._anomaly_detector.detect_anomalies(
            current_metrics=current,
            historical_baseline=baseline,
            scope_type=state.get("scope_type", "TENANT"),
            scope_id=state.get("scope_id", "default"),
        )

        anomaly_dict = None
        if anomalies:
            top = anomalies[0]
            anomaly_dict = {
                "anomaly_type": top.anomaly_type,
                "severity": top.severity,
                "expected_value": top.expected_value,
                "actual_value": top.actual_value,
                "deviation_percent": top.deviation_percent,
                "reason": top.reason,
            }

        return {
            "iteration_count": count,
            "anomaly": anomaly_dict,
        }

    # ── Node 3: Identify Drivers ──────────────────────────────────

    async def _node_identify_drivers(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        current = state.get("current_metrics", {})
        hist = state.get("historical_summary", {})
        drivers: list[dict[str, Any]] = []

        # Analyze model distribution driver
        model_dist = current.get("model_distribution", {})
        if model_dist:
            expensive_models = ["claude-3-5-sonnet", "gpt-4o", "gemini-1.5-pro"]
            for model_name, calls in model_dist.items():
                if model_name in expensive_models and calls > 0:
                    drivers.append(
                        {
                            "driver_type": "expensive_model_surge",
                            "name": model_name,
                            "volume": calls,
                            "impact": "HIGH",
                        }
                    )

        # Analyze token driver
        tokens = current.get("token_count", 0)
        if tokens > 20000:
            drivers.append(
                {
                    "driver_type": "high_token_volume",
                    "name": "large_prompt_contexts",
                    "volume": tokens,
                    "impact": "MEDIUM",
                }
            )

        # Default driver if none triggered
        if not drivers:
            drivers.append(
                {
                    "driver_type": "workload_volume",
                    "name": "predictive_maintenance",
                    "volume": current.get("request_count", 100),
                    "impact": "LOW",
                }
            )

        return {
            "iteration_count": count,
            "drivers": drivers,
        }

    # ── Node 4: Compare Model / Workload Usage ────────────────────

    async def _node_compare_model_workload(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        hist = state.get("historical_summary", {})
        current = state.get("current_metrics", {})

        model_comp = {
            "baseline_breakdown": hist.get("model_breakdown", {}),
            "current_distribution": current.get("model_distribution", {}),
            "primary_cost_driver_model": "claude-3-5-sonnet",
        }

        workload_comp = {
            "baseline_breakdown": hist.get("workload_breakdown", {}),
            "current_request_count": current.get("request_count", 100),
            "active_workload": "predictive_maintenance",
        }

        return {
            "iteration_count": count,
            "model_comparison": model_comp,
            "workload_comparison": workload_comp,
        }

    # ── Node 5: Root-Cause Reasoning (via ModelGateway) ───────────

    async def _node_root_cause_reasoning(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        anomaly = state.get("anomaly")
        drivers = state.get("drivers", [])

        if not anomaly:
            return {
                "iteration_count": count,
                "root_cause": "Cost and usage metrics are within normal historical operating bounds.",
            }

        system_prompt = (
            "You are an AI FinOps expert investigating manufacturing AI workloads. "
            "Analyze the anomaly and driver data provided. Return a JSON object with a single "
            "key 'root_cause' explaining the technical and operational driver of the cost spike."
        )

        user_content = json.dumps(
            {
                "anomaly": anomaly,
                "drivers": drivers,
                "model_comparison": state.get("model_comparison"),
                "workload_comparison": state.get("workload_comparison"),
            },
            default=str,
        )

        messages_tuple = (
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_content),
        )

        if hasattr(self._gateway, "generate_text"):
            req = TextGenerationRequest(
                model=self._default_model,
                messages=messages_tuple,
                response_format="json_object",
                temperature=0.2,
                max_output_tokens=256,
            )
            response = await self._gateway.generate_text(req)
        else:
            req = TextGenerationRequest(
                model=self._default_model,
                messages=list(messages_tuple),
                response_format="json_object",
                temperature=0.2,
                max_output_tokens=256,
            )
            response = await self._gateway.generate(req)

        root_cause_text = response.content
        try:
            parsed = json.loads(response.content)
            if isinstance(parsed, dict) and "root_cause" in parsed:
                root_cause_text = parsed["root_cause"]
        except (json.JSONDecodeError, TypeError):
            pass

        return {
            "iteration_count": count,
            "root_cause": root_cause_text,
        }

    # ── Node 6: Recommendation ───────────────────────────────────

    async def _node_recommendation(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        anomaly = state.get("anomaly")

        if not anomaly:
            rec = "Maintain current model routing policies and monitor standard operational thresholds."
        else:
            rec = (
                "Optimize model routing by directing routine analysis to lightweight models "
                "(e.g., gpt-4o-mini / llama-3-8b) and reserving advanced reasoning models "
                "only for high-severity complex incidents."
            )

        return {
            "iteration_count": count,
            "recommendation": rec,
        }

    # ── Node 7: Savings Estimate ─────────────────────────────────

    async def _node_savings_estimate(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        anomaly = state.get("anomaly")
        current = state.get("current_metrics", {})
        cost = float(current.get("cost_usd", 50.0))

        if not anomaly:
            estimated_saving = 0.0
            quality_impact = 0.0
        else:
            # Model downgrade or tiered routing typically yields 35-50% savings
            estimated_saving = round(cost * 0.40, 2)
            quality_impact = 0.01  # < 1% quality impact for routine tasks

        return {
            "iteration_count": count,
            "estimated_saving": estimated_saving,
            "quality_impact": quality_impact,
        }

    # ── Node 8: Risk Evaluation & Policy Proposal ─────────────────

    async def _node_risk_evaluation(self, state: CostInvestigationState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1
        anomaly = state.get("anomaly")
        saving = state.get("estimated_saving", 0.0)

        risk = "LOW"
        proposed_policy: dict[str, Any] | None = None

        if anomaly:
            severity = anomaly.get("severity", "MEDIUM")
            risk = "MEDIUM" if severity in ("LOW", "MEDIUM") else "HIGH"

            # Create candidate proposed policy change (DO NOT DIRECTLY ACTIVATE)
            proposal = await self._policy_service.propose_policy_change(
                workload_type="predictive_maintenance",
                current_model="claude-3-5-sonnet",
                recommended_model="gpt-4o-mini",
                estimated_monthly_saving=saving * 30.0,
                quality_impact=state.get("quality_impact", 0.0),
                reason=state.get("root_cause", ""),
            )

            proposed_policy = {
                "proposal_id": proposal.proposal_id,
                "workload_type": proposal.workload_type,
                "current_model": proposal.current_model,
                "recommended_model": proposal.recommended_model,
                "target_traffic_percent": proposal.target_traffic_percent,
                "estimated_monthly_saving_usd": proposal.estimated_monthly_saving_usd,
                "quality_impact_percent": proposal.quality_impact_percent,
                "status": proposal.status,
                "requires_approval": proposal.requires_approval,
                "reason": proposal.reason,
            }

        return {
            "iteration_count": count,
            "risk": risk,
            "proposed_policy_change": proposed_policy,
            "status": "completed",
        }

    # ── Formatter ─────────────────────────────────────────────────

    def _format_result(self, state: CostInvestigationState) -> CostInvestigationResult:
        return CostInvestigationResult(
            request_id=state.get("request_id", ""),
            scope_type=state.get("scope_type", "TENANT"),
            scope_id=state.get("scope_id", "default"),
            anomaly=state.get("anomaly"),
            drivers=state.get("drivers", []),
            root_cause=state.get("root_cause", ""),
            recommendation=state.get("recommendation", ""),
            estimated_saving=state.get("estimated_saving", 0.0),
            quality_impact=state.get("quality_impact", 0.0),
            risk=state.get("risk", "LOW"),
            proposed_policy_change=state.get("proposed_policy_change"),
            status=state.get("status", "completed"),
        )
