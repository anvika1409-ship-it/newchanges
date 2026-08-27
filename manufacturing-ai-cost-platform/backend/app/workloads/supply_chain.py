"""Supply Chain AI workflow.

Implements the multi-stage Supply Chain optimization pipeline:
    Request
    -> Orchestrator & Policy Check
    -> Authorized Data Retrieval & Context Filtering
    -> Candidate Analysis (Deterministic ML/Optimization)
    -> LLM / Optimization Reasoning (via ModelGateway when complex)
    -> Recommendation Generation
    -> Risk Evaluation & Governance Gating
    -> Telemetry & Cost Tracking

Follows AI_WORKFLOWS.md section 4, ARCHITECTURE.md section 2, and
SECURITY.md sections 9, 13, and 14.

Critical governance rules:
- LLM outputs are recommendations ONLY. The workflow NEVER directly mutates
  or executes updates against external supplier ERP or logistics TMS systems.
- High-risk actions (critical stockout, supplier switching, severe budget overrun)
  require explicit human policy approval (requires_approval=True).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.intelligence.supply_chain_optimizer import (
    SupplyChainAnalysisResult,
    SupplyChainOptimizer,
)

# Imported directly. The previous form was a three-level try/except cascade that
# fell back to `app.integrations.model_gateway.base` — a module removed when the
# gateway moved to `app/integrations/llm/` — and then to `ModelGatewayInterface
# = Any`, which would have silently disabled the type contract that keeps LLM
# access behind the gateway. A missing gateway interface must fail loudly at
# import, not degrade to `Any` (AI_DEVELOPMENT_RULES.md sections 4.4 and 26).
from app.integrations.llm.interface import (
    Message,
    ModelGatewayInterface,
    Role,
    TextGenerationRequest,
)

from app.telemetry.emitter import TelemetryEmitter
from app.telemetry.tracker import CostProvenance, CostTracker, CostType

logger = get_logger(__name__)

WORKFLOW_TYPE = "supply_chain"

# Risk Thresholds
CRITICAL_STOCKOUT_THRESHOLD = 0.5  # If shortage is > 50% of safety stock
HIGH_SUPPLIER_RISK_THRESHOLD = 0.4  # If supplier risk_score > 0.4
HIGH_BUDGET_OVERRUN_THRESHOLD = 0.2  # If budget overrun > 20%


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Input validation result."""

    is_valid: bool
    errors: tuple[str, ...] = ()


class SupplyChainWorkflow:
    """Modular Supply Chain AI optimization workflow.

    Dependencies are injected:
    * ``model_gateway``: Gateway abstraction for LLM trade-off reasoning.
    * ``telemetry``: Telemetry event sink.
    * ``cost_tracker``: Cost tracking with provenance.
    """

    workflow_type: str = WORKFLOW_TYPE

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayInterface,
        telemetry: TelemetryEmitter,
        cost_tracker: CostTracker,
        optimizer: SupplyChainOptimizer | None = None,
        default_model: str = "supply-chain-optimizer-model",
    ) -> None:
        self._gateway = model_gateway
        self._telemetry = telemetry
        self._cost_tracker = cost_tracker
        self._optimizer = optimizer or SupplyChainOptimizer()
        self._default_model = default_model

    # ── 1. Input Validation ────────────────────────────────────────

    async def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        """Validate that all required supply chain inputs are provided."""
        errors: list[str] = []

        if input_data is None:
            return ValidationResult(is_valid=False, errors=("input_data is required",))

        # Check required fields
        required_fields = ["inventory", "demand", "suppliers", "logistics", "lead_time"]
        for field_name in required_fields:
            if field_name not in input_data:
                errors.append(f"{field_name} is required")
            elif input_data[field_name] is None:
                errors.append(f"{field_name} must not be null")

        # Specific type validations
        if "inventory" in input_data and input_data["inventory"] is not None:
            inv = input_data["inventory"]
            if not isinstance(inv, (list, dict)):
                errors.append("inventory must be a list or dict")
            elif len(inv) == 0:
                errors.append("inventory must not be empty")

        if "suppliers" in input_data and input_data["suppliers"] is not None:
            sups = input_data["suppliers"]
            if not isinstance(sups, list):
                errors.append("suppliers must be a list")
            elif len(sups) == 0:
                errors.append("suppliers must not be empty")

        if "logistics" in input_data and input_data["logistics"] is not None:
            logs = input_data["logistics"]
            if not isinstance(logs, list):
                errors.append("logistics must be a list")
            elif len(logs) == 0:
                errors.append("logistics must not be empty")

        if errors:
            return ValidationResult(is_valid=False, errors=tuple(errors))
        return ValidationResult(is_valid=True)

    # ── 2. Core Execution Pipeline ─────────────────────────────────

    async def execute(
        self, execution_id: str, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Run the end-to-end Supply Chain AI workflow."""
        inventory = input_data["inventory"]
        demand = input_data["demand"]
        suppliers = input_data["suppliers"]
        logistics = input_data["logistics"]
        lead_time = input_data.get("lead_time")
        budget_limit = input_data.get("budget_limit")
        business_priority = input_data.get("business_priority", "NORMAL")

        # Step 1: Input Validation Telemetry
        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {"step": "input_validation", "status": "passed"},
        )

        # Step 2: Policy & Budget Pre-Check
        budget_status = self._evaluate_budget_policy(budget_limit, input_data)
        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {
                "step": "budget_policy_check",
                "budget_status": budget_status,
                "business_priority": business_priority,
            },
        )

        # Step 3: Context Filtering & Security Guardrails
        filtered_suppliers = self._filter_supplier_context(suppliers)
        filtered_logistics = self._filter_logistics_context(logistics)

        # Step 4: Candidate Optimization & Scoring
        analysis = self._optimizer.analyze(
            inventory=inventory,
            demand=demand,
            suppliers=filtered_suppliers,
            logistics=filtered_logistics,
            lead_time_constraint=lead_time,
            budget_limit=budget_limit,
        )

        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {
                "step": "candidate_analysis",
                "stockout_items": list(analysis.stockout_items),
                "total_shortage_units": analysis.total_shortage_units,
                "total_landed_cost": analysis.total_landed_cost,
                "budget_pressure": analysis.budget_pressure,
            },
        )

        # Step 5: Determine Complexity & Run LLM Reasoning when required
        is_complex = (
            len(analysis.stockout_items) > 0
            or analysis.budget_pressure
            or analysis.max_supplier_risk >= HIGH_SUPPLIER_RISK_THRESHOLD
        )

        llm_explanation: str | None = None
        model_used: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None
        data_quality = "ACTUAL"

        if is_complex:
            llm_result = await self._llm_reasoning(
                execution_id=execution_id,
                analysis=analysis,
                lead_time=lead_time,
                budget_limit=budget_limit,
                business_priority=business_priority,
            )
            llm_explanation = llm_result["explanation"]
            model_used = llm_result["model_used"]
            prompt_tokens = llm_result.get("prompt_tokens")
            completion_tokens = llm_result.get("completion_tokens")
            total_tokens = llm_result.get("total_tokens")
            data_quality = "ESTIMATED"
        else:
            llm_explanation = self._deterministic_explanation(analysis)

        # Step 6: Risk Evaluation & Human-in-the-Loop Gating
        risk_level = self._evaluate_risk(analysis, budget_status)
        requires_approval = risk_level in ("critical", "high") or analysis.budget_pressure
        recommendation_type = self._determine_recommendation_type(analysis)
        recommended_action = self._build_recommended_action(
            analysis=analysis,
            llm_explanation=llm_explanation,
            risk_level=risk_level,
            requires_approval=requires_approval,
        )

        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {
                "step": "risk_evaluation",
                "risk_level": risk_level,
                "requires_approval": requires_approval,
                "used_llm": is_complex,
            },
        )

        # Build supplier allocation summary
        allocations = []
        for item_id, sup in analysis.selected_suppliers.items():
            qty = analysis.recommended_order_quantity.get(item_id, 0.0)
            allocations.append(
                {
                    "item_id": item_id,
                    "selected_supplier_id": sup.supplier_id,
                    "supplier_name": sup.name,
                    "order_quantity": qty,
                    "unit_price": sup.unit_price,
                    "supplier_risk_score": sup.risk_score,
                }
            )

        logistics_summary = None
        if analysis.selected_logistics:
            logistics_summary = {
                "selected_route_id": analysis.selected_logistics.route_id,
                "shipping_mode": analysis.selected_logistics.shipping_mode,
                "estimated_transit_days": analysis.selected_logistics.transit_days,
                "logistics_cost_usd": analysis.total_logistics_cost,
            }

        return {
            "workflow_type": WORKFLOW_TYPE,
            "recommendation_type": recommendation_type,
            "priority": business_priority,
            "status": "completed",
            "inventory_status": {
                "items_analyzed": len(analysis.inventory_items),
                "stockout_items": list(analysis.stockout_items),
                "total_shortage_units": analysis.total_shortage_units,
            },
            "supplier_allocation": allocations,
            "logistics_plan": logistics_summary,
            "total_estimated_cost_usd": analysis.total_landed_cost,
            "budget_status": budget_status,
            "budget_pressure": analysis.budget_pressure,
            "budget_overrun_usd": analysis.budget_overrun_usd,
            "total_lead_time_days": analysis.total_lead_time_days,
            "risk_level": risk_level,
            "requires_approval": requires_approval,
            "explanation": llm_explanation or "",
            "recommended_action": recommended_action,
            "data_quality": data_quality,
            "model_used": model_used,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    # ── 3. Output Formatter ────────────────────────────────────────

    async def format_output(self, raw_output: dict[str, Any]) -> dict[str, Any]:
        """Format the output dictionary according to API_CONTRACT.yaml."""
        return {
            "workflow_type": raw_output["workflow_type"],
            "recommendation_type": raw_output["recommendation_type"],
            "priority": raw_output["priority"],
            "status": raw_output["status"],
            "inventory_status": raw_output["inventory_status"],
            "supplier_allocation": raw_output["supplier_allocation"],
            "logistics_plan": raw_output["logistics_plan"],
            "total_estimated_cost_usd": raw_output["total_estimated_cost_usd"],
            "budget_status": raw_output["budget_status"],
            "budget_pressure": raw_output["budget_pressure"],
            "budget_overrun_usd": raw_output["budget_overrun_usd"],
            "total_lead_time_days": raw_output["total_lead_time_days"],
            "risk_level": raw_output["risk_level"],
            "requires_approval": raw_output["requires_approval"],
            "explanation": raw_output["explanation"],
            "recommended_action": raw_output["recommended_action"],
            "data_quality": raw_output["data_quality"],
        }

    # ── 4. Internal Reasoning & Gating Helpers ─────────────────────

    def _evaluate_budget_policy(
        self, budget_limit: float | None, input_data: dict[str, Any]
    ) -> str:
        """Deterministic budget policy evaluation (SECURITY.md section 13)."""
        if budget_limit is not None and budget_limit <= 0:
            return "BLOCK"
        return "ALLOW"

    def _filter_supplier_context(
        self, suppliers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Context guardrail: sanitize and bound supplier data."""
        filtered = []
        for s in suppliers[:10]:  # Bound to top 10 suppliers
            filtered.append(
                {
                    "supplier_id": s.get("supplier_id"),
                    "name": s.get("name"),
                    "items_supplied": s.get("items_supplied", []),
                    "unit_price": s.get("unit_price"),
                    "lead_time_days": s.get("lead_time_days"),
                    "reliability_score": s.get("reliability_score", 0.9),
                    "risk_score": s.get("risk_score", 0.1),
                    "capacity": s.get("capacity", 10000.0),
                    "is_primary": s.get("is_primary", False),
                }
            )
        return filtered

    def _filter_logistics_context(
        self, logistics: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Context guardrail: sanitize and bound logistics data."""
        filtered = []
        for l in logistics[:5]:  # Bound to top 5 routes
            filtered.append(
                {
                    "route_id": l.get("route_id"),
                    "origin": l.get("origin"),
                    "destination": l.get("destination"),
                    "shipping_mode": l.get("shipping_mode"),
                    "cost_per_unit": l.get("cost_per_unit"),
                    "transit_days": l.get("transit_days"),
                    "reliability": l.get("reliability", 0.95),
                    "risk_score": l.get("risk_score", 0.05),
                }
            )
        return filtered

    async def _llm_reasoning(
        self,
        *,
        execution_id: str,
        analysis: SupplyChainAnalysisResult,
        lead_time: Any,
        budget_limit: float | None,
        business_priority: str,
    ) -> dict[str, Any]:
        """Invoke ModelGateway for complex multi-objective reasoning."""
        self._telemetry.emit(
            "llm.call_started",
            execution_id,
            {"model": self._default_model, "reason": "supply_chain_tradeoff_optimization"},
        )

        system_prompt = (
            "You are a supply chain optimization specialist. Analyze the provided inventory "
            "deficits, scored supplier options, logistics candidates, and budget constraints. "
            "Return a JSON object with a single key 'explanation' containing a clear, "
            "concise operational summary of the trade-offs (balancing cost, stockout risk, "
            "and lead time) and the rationale for the recommended procurement plan. "
            "Do not execute or invent any external actions."
        )

        # Contextual summary as untrusted structured user payload (SECURITY.md section 9)
        user_content = json.dumps(
            {
                "stockout_items": list(analysis.stockout_items),
                "total_shortage_units": analysis.total_shortage_units,
                "recommended_orders": analysis.recommended_order_quantity,
                "procurement_cost_usd": analysis.total_procurement_cost,
                "logistics_cost_usd": analysis.total_logistics_cost,
                "total_landed_cost_usd": analysis.total_landed_cost,
                "budget_limit_usd": budget_limit,
                "budget_pressure": analysis.budget_pressure,
                "budget_overrun_usd": analysis.budget_overrun_usd,
                "max_supplier_risk": analysis.max_supplier_risk,
                "lead_time_days": analysis.total_lead_time_days,
                "business_priority": business_priority,
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
                max_output_tokens=512,
            )
            response = await self._gateway.generate_text(req)
        else:
            req = TextGenerationRequest(
                model=self._default_model,
                messages=list(messages_tuple),
                response_format="json_object",
                temperature=0.2,
                max_output_tokens=512,
            )
            response = await self._gateway.generate(req)

        usage = response.usage
        cost_usd = 0.0
        provenance = CostProvenance.ESTIMATED

        self._cost_tracker.record_cost(
            execution_id=execution_id,
            cost_type=CostType.LLM_INFERENCE,
            amount_usd=cost_usd,
            description="Supply Chain trade-off LLM reasoning",
            provenance=provenance,
        )

        self._telemetry.emit(
            "llm.call_completed",
            execution_id,
            {
                "model": response.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "latency_ms": response.latency_ms,
            },
        )

        self._telemetry.emit(
            "cost.recorded",
            execution_id,
            {
                "cost_type": CostType.LLM_INFERENCE,
                "amount_usd": cost_usd,
                "provenance": provenance,
            },
        )

        explanation = response.content
        try:
            parsed = json.loads(response.content)
            if isinstance(parsed, dict) and "explanation" in parsed:
                explanation = parsed["explanation"]
        except (json.JSONDecodeError, TypeError):
            pass

        return {
            "explanation": explanation,
            "model_used": response.model,
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }

    def _deterministic_explanation(self, analysis: SupplyChainAnalysisResult) -> str:
        """Deterministic summary for routine replenishment plans."""
        if not analysis.stockout_items:
            return (
                "All inventory items meet target safety stock levels. "
                "No immediate replenishment order required; continue standard monitoring."
            )
        items_str = ", ".join(analysis.stockout_items)
        return (
            f"Replenishment plan computed for items ({items_str}). "
            f"Total order volume: {analysis.total_shortage_units} units with estimated landed cost "
            f"${analysis.total_landed_cost:,.2f} over {analysis.total_lead_time_days} days lead time."
        )

    def _evaluate_risk(
        self, analysis: SupplyChainAnalysisResult, budget_status: str
    ) -> str:
        """Deterministic risk level classification."""
        if budget_status == "BLOCK":
            return "critical"

        # Check for critical stockouts
        if len(analysis.stockout_items) >= 3 or analysis.total_shortage_units > 1000:
            return "critical"

        # Check for supplier risk or budget pressure
        if analysis.max_supplier_risk >= HIGH_SUPPLIER_RISK_THRESHOLD:
            return "high"

        if analysis.budget_pressure or analysis.budget_overrun_usd > 0:
            return "high"

        if len(analysis.stockout_items) > 0:
            return "medium"

        return "low"

    def _determine_recommendation_type(self, analysis: SupplyChainAnalysisResult) -> str:
        """Categorize the recommendation type."""
        if analysis.max_supplier_risk >= HIGH_SUPPLIER_RISK_THRESHOLD:
            return "supplier_switch"
        if analysis.budget_pressure:
            return "inventory_rebalance"
        if len(analysis.stockout_items) > 0:
            return "replenishment"
        return "replenishment"

    def _build_recommended_action(
        self,
        *,
        analysis: SupplyChainAnalysisResult,
        llm_explanation: str | None,
        risk_level: str,
        requires_approval: bool,
    ) -> str:
        """Build governance-gated recommended action text."""
        prefix = ""
        if requires_approval:
            prefix = "RECOMMENDATION ONLY — requires authorized approval before execution. "

        if not analysis.stockout_items and not analysis.budget_pressure:
            return f"{prefix}Maintain current inventory levels and monitor scheduled supplier deliveries."

        actions = []
        if analysis.stockout_items:
            items_str = ", ".join(analysis.stockout_items)
            actions.append(f"Place replenishment purchase orders for {items_str}")
        if analysis.selected_logistics:
            actions.append(f"route via {analysis.selected_logistics.shipping_mode}")
        if analysis.budget_pressure:
            actions.append(f"review budget overrun of ${analysis.budget_overrun_usd:,.2f}")

        action_summary = "; ".join(actions)
        return f"{prefix}{action_summary}."
