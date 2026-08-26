"""Supply Chain candidate optimization and scoring engine.

Evaluates inventory deficits, scores supplier and logistics candidates
deterministically, and computes multi-objective trade-offs
(cost, speed, reliability, risk) before LLM reasoning
(AI_WORKFLOWS.md section 4, AI_DEVELOPMENT_RULES.md section 7).

Deterministic optimization is preferred over raw LLM guesswork for numerical
calculations (landed cost, lead times, safety stock deficits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ItemInventoryStatus:
    """Evaluation of an individual inventory item."""

    item_id: str
    name: str
    current_stock: float
    safety_stock: float
    reorder_point: float
    unit_cost: float
    demand_units: float
    net_stock: float  # current_stock - demand_units
    shortage_units: float  # max(0.0, safety_stock - net_stock)
    is_stockout: bool
    stockout_risk: float  # 0.0 to 1.0


@dataclass(frozen=True, slots=True)
class SupplierCandidate:
    """Scored supplier candidate for procurement."""

    supplier_id: str
    name: str
    item_id: str
    unit_price: float
    lead_time_days: int
    reliability_score: float  # 0.0 - 1.0 (1.0 = best)
    risk_score: float  # 0.0 - 1.0 (0.0 = lowest risk, 1.0 = highest risk)
    capacity: float
    is_primary: bool
    composite_score: float  # higher is better


@dataclass(frozen=True, slots=True)
class LogisticsCandidate:
    """Scored logistics route candidate."""

    route_id: str
    origin: str
    destination: str
    shipping_mode: str
    cost_per_unit: float
    transit_days: int
    reliability: float  # 0.0 - 1.0
    risk_score: float  # 0.0 - 1.0


@dataclass(frozen=True, slots=True)
class SupplyChainAnalysisResult:
    """Aggregated optimization and candidate analysis result."""

    inventory_items: dict[str, ItemInventoryStatus] = field(default_factory=dict)
    stockout_items: tuple[str, ...] = ()
    total_shortage_units: float = 0.0
    recommended_order_quantity: dict[str, float] = field(default_factory=dict)
    supplier_candidates: dict[str, list[SupplierCandidate]] = field(default_factory=dict)
    selected_suppliers: dict[str, SupplierCandidate] = field(default_factory=dict)
    selected_logistics: LogisticsCandidate | None = None
    total_procurement_cost: float = 0.0
    total_logistics_cost: float = 0.0
    total_landed_cost: float = 0.0
    total_lead_time_days: int = 0
    max_supplier_risk: float = 0.0
    budget_pressure: bool = False
    budget_overrun_usd: float = 0.0
    data_quality: str = "ACTUAL"


class SupplyChainOptimizer:
    """Deterministic candidate ranking and trade-off analyzer for Supply Chain workloads."""

    def analyze(
        self,
        *,
        inventory: list[dict[str, Any]] | dict[str, Any],
        demand: list[dict[str, Any]] | dict[str, Any],
        suppliers: list[dict[str, Any]],
        logistics: list[dict[str, Any]],
        lead_time_constraint: dict[str, Any] | int | float | None = None,
        budget_limit: float | None = None,
    ) -> SupplyChainAnalysisResult:
        """Run candidate analysis and multi-objective optimization."""
        # 1. Normalize inventory & demand inputs
        inv_list = self._normalize_list_or_dict(inventory, "item_id")
        demand_dict = self._normalize_demand(demand)

        # 2. Analyze inventory & shortages
        inv_status_map: dict[str, ItemInventoryStatus] = {}
        stockout_items: list[str] = []
        total_shortage: float = 0.0
        reorder_quantities: dict[str, float] = {}

        for item in inv_list:
            item_id = str(item.get("item_id", "default_item"))
            name = str(item.get("name", item_id))
            current = float(item.get("current_stock", 0.0))
            safety = float(item.get("safety_stock", 0.0))
            reorder_pt = float(item.get("reorder_point", safety))
            unit_cost = float(item.get("unit_cost", 1.0))
            req_demand = float(demand_dict.get(item_id, item.get("forecast_demand", 0.0)))

            net = current - req_demand
            # Shortage occurs when net stock drops below safety stock
            shortage = max(0.0, safety - net)
            is_stockout = net <= 0.0 or current <= 0.0

            # Stockout risk estimation based on ratio of net stock to safety stock
            if is_stockout:
                stockout_risk = 1.0
            elif safety > 0:
                stockout_risk = max(0.0, min(1.0, 1.0 - (net / (safety * 2.0))))
            else:
                stockout_risk = 0.0

            status_obj = ItemInventoryStatus(
                item_id=item_id,
                name=name,
                current_stock=current,
                safety_stock=safety,
                reorder_point=reorder_pt,
                unit_cost=unit_cost,
                demand_units=req_demand,
                net_stock=round(net, 2),
                shortage_units=round(shortage, 2),
                is_stockout=is_stockout,
                stockout_risk=round(stockout_risk, 3),
            )
            inv_status_map[item_id] = status_obj

            if shortage > 0 or is_stockout:
                stockout_items.append(item_id)
                total_shortage += shortage
                # Recommend replenishing to safety stock + anticipated demand buffer
                reorder_qty = shortage + (safety * 0.5)
                reorder_quantities[item_id] = round(reorder_qty, 2)

        # 3. Analyze & score supplier candidates per item
        supplier_candidates_map: dict[str, list[SupplierCandidate]] = {}
        selected_suppliers: dict[str, SupplierCandidate] = {}
        total_procurement_cost = 0.0
        max_supplier_risk = 0.0

        for item_id, item_status in inv_status_map.items():
            order_qty = reorder_quantities.get(item_id, 0.0)
            item_candidates: list[SupplierCandidate] = []

            for sup in suppliers:
                supplied_items = sup.get("items_supplied", [])
                # If supplier lists specific items, filter; otherwise assume general supplier
                if supplied_items and item_id not in supplied_items and sup.get("item_id") != item_id:
                    continue

                sup_id = str(sup.get("supplier_id", "sup_unknown"))
                sup_name = str(sup.get("name", sup_id))
                price = float(sup.get("unit_price", item_status.unit_cost))
                lt_days = int(sup.get("lead_time_days", 7))
                reliability = float(sup.get("reliability_score", 0.9))
                risk = float(sup.get("risk_score", 0.1))
                cap = float(sup.get("capacity", 10000.0))
                is_primary = bool(sup.get("is_primary", False))

                # Composite score: reward high reliability & primary status, penalize price, risk, and lead time
                # Normalize: reliability [0-1] * 40 - risk [0-1] * 30 - price_penalty * 20 - lead_time * 10
                price_factor = 1.0 / max(0.1, price / max(0.1, item_status.unit_cost))
                lead_time_factor = 1.0 / max(1.0, float(lt_days))
                composite = (reliability * 40.0) + (price_factor * 25.0) + (lead_time_factor * 15.0) - (risk * 30.0)
                if is_primary:
                    composite += 10.0

                candidate = SupplierCandidate(
                    supplier_id=sup_id,
                    name=sup_name,
                    item_id=item_id,
                    unit_price=price,
                    lead_time_days=lt_days,
                    reliability_score=reliability,
                    risk_score=risk,
                    capacity=cap,
                    is_primary=is_primary,
                    composite_score=round(composite, 2),
                )
                item_candidates.append(candidate)

            # Sort candidates by composite score descending
            item_candidates.sort(key=lambda c: c.composite_score, reverse=True)
            supplier_candidates_map[item_id] = item_candidates

            # Select best candidate
            if item_candidates:
                best = item_candidates[0]
                selected_suppliers[item_id] = best
                if order_qty > 0:
                    total_procurement_cost += best.unit_price * order_qty
                max_supplier_risk = max(max_supplier_risk, best.risk_score)

        # 4. Analyze & score logistics candidates
        selected_logistics: LogisticsCandidate | None = None
        total_logistics_cost = 0.0
        max_transit_days = 0

        if logistics:
            scored_logistics: list[tuple[float, LogisticsCandidate]] = []
            for log in logistics:
                r_id = str(log.get("route_id", "route_default"))
                origin = str(log.get("origin", "hub_a"))
                dest = str(log.get("destination", "plant"))
                mode = str(log.get("shipping_mode", "standard_ground"))
                cost_unit = float(log.get("cost_per_unit", 2.0))
                t_days = int(log.get("transit_days", 3))
                rel = float(log.get("reliability", 0.95))
                r_risk = float(log.get("risk_score", 0.05))

                log_cand = LogisticsCandidate(
                    route_id=r_id,
                    origin=origin,
                    destination=dest,
                    shipping_mode=mode,
                    cost_per_unit=cost_unit,
                    transit_days=t_days,
                    reliability=rel,
                    risk_score=r_risk,
                )
                # Composite score for logistics: reliability & speed vs cost & risk
                score = (rel * 50.0) - (cost_unit * 5.0) - (t_days * 3.0) - (r_risk * 20.0)
                scored_logistics.append((score, log_cand))

            scored_logistics.sort(key=lambda x: x[0], reverse=True)
            selected_logistics = scored_logistics[0][1]
            total_ordered_units = sum(reorder_quantities.values())
            if total_ordered_units > 0:
                total_logistics_cost = selected_logistics.cost_per_unit * total_ordered_units
            max_transit_days = selected_logistics.transit_days

        # 5. Combined metrics & budget pressure
        supplier_max_lead_time = max(
            [s.lead_time_days for s in selected_suppliers.values()], default=0
        )
        total_lead_time = supplier_max_lead_time + max_transit_days
        total_landed = total_procurement_cost + total_logistics_cost

        budget_pressure = False
        budget_overrun = 0.0
        if budget_limit is not None and budget_limit > 0:
            if total_landed > budget_limit:
                budget_pressure = True
                budget_overrun = round(total_landed - budget_limit, 2)

        return SupplyChainAnalysisResult(
            inventory_items=inv_status_map,
            stockout_items=tuple(stockout_items),
            total_shortage_units=round(total_shortage, 2),
            recommended_order_quantity=reorder_quantities,
            supplier_candidates=supplier_candidates_map,
            selected_suppliers=selected_suppliers,
            selected_logistics=selected_logistics,
            total_procurement_cost=round(total_procurement_cost, 2),
            total_logistics_cost=round(total_logistics_cost, 2),
            total_landed_cost=round(total_landed, 2),
            total_lead_time_days=total_lead_time,
            max_supplier_risk=round(max_supplier_risk, 3),
            budget_pressure=budget_pressure,
            budget_overrun_usd=budget_overrun,
            data_quality="ACTUAL",
        )

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _normalize_list_or_dict(
        data: list[dict[str, Any]] | dict[str, Any], key_name: str
    ) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # If dictionary keyed by item_id
            items = []
            for k, v in data.items():
                if isinstance(v, dict):
                    entry = dict(v)
                    if key_name not in entry:
                        entry[key_name] = k
                    items.append(entry)
                else:
                    items.append({key_name: k, "current_stock": float(v)})
            return items
        return []

    @staticmethod
    def _normalize_demand(
        demand: list[dict[str, Any]] | dict[str, Any]
    ) -> dict[str, float]:
        if isinstance(demand, dict):
            return {str(k): float(v) for k, v in demand.items() if isinstance(v, (int, float))}
        if isinstance(demand, list):
            result = {}
            for d in demand:
                item_id = str(d.get("item_id", "default_item"))
                qty = float(d.get("forecast_demand", d.get("demand_units", d.get("quantity", 0.0))))
                result[item_id] = qty
            return result
        return {}
