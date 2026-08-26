/**
 * Types mirror the schemas defined in API_CONTRACT.yaml (OpenAPI 3.0.3).
 * Field names intentionally match the contract's snake_case wire format.
 *
 * Endpoints referenced in this dashboard (all under /api/v1, bearer-auth protected):
 *   GET /cost/summary
 *   GET /cost/trend
 *   GET /budgets/status
 *   GET /forecasts
 *   GET /anomalies
 *   GET /optimization/recommendations
 *   GET /workloads
 *
 * List-item shapes for budgets/status, forecasts, anomalies, optimization
 * recommendations and workloads are not fully enumerated in the contract
 * (it only documents the path, params and top-level description). The shapes
 * below are inferred to fit the contract's naming conventions and should be
 * reconciled with the backend's actual response schema once available.
 */

/** How a numeric value was produced. Must always be shown to the user. */
export type Provenance = 'ACTUAL' | 'ESTIMATED' | 'FORECAST' | 'SIMULATED'

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export type BudgetPeriod = 'DAILY' | 'MONTHLY' | 'QUARTERLY' | 'ANNUAL'

export type BudgetScopeType = 'ENTERPRISE' | 'PLANT' | 'DEPARTMENT' | 'WORKLOAD' | 'AGENT' | 'MODEL'

export interface CostSummary {
  total_cost: number
  currency: string
  total_requests: number
  total_tokens: number
  average_cost_per_request: number
  budget_consumed_percent: number
  projected_month_end_cost: number
  /** Not in the base contract schema; included for the "today" KPI required by this dashboard. */
  today_cost: number
  /** Not in the base contract schema; included for the "monthly" KPI required by this dashboard. */
  month_to_date_cost: number
  provenance: Provenance
  generated_at: string
}

export interface CostTrendPoint {
  timestamp: string
  cost: number
  provenance: Provenance
}

export interface CostTrend {
  granularity: 'hour' | 'day' | 'week' | 'month'
  currency: string
  points: CostTrendPoint[]
}

export interface BudgetStatusItem {
  scope_type: BudgetScopeType
  scope_id: string
  scope_label: string
  period: BudgetPeriod
  amount: number
  currency: string
  consumed_amount: number
  consumed_percent: number
  warning_threshold_percent: number
  critical_threshold_percent: number
  projected_overrun_amount: number
  projected_overrun_percent: number
  status: 'ON_TRACK' | 'WARNING' | 'CRITICAL' | 'EXCEEDED'
}

export interface BudgetStatus {
  currency: string
  items: BudgetStatusItem[]
}

export interface ForecastPoint {
  timestamp: string
  forecast_cost: number
  lower_bound: number
  upper_bound: number
}

export interface Forecast {
  horizon_days: number
  currency: string
  model_confidence: number
  points: ForecastPoint[]
  provenance: Provenance
}

export interface Anomaly {
  id: string
  detected_at: string
  severity: Severity
  scope_type: BudgetScopeType
  scope_label: string
  metric: string
  expected_value: number
  observed_value: number
  deviation_percent: number
  currency: string
  summary: string
  status: 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED'
}

export interface OptimizationRecommendation {
  id: string
  workload_id: string
  workload_label: string
  title: string
  description: string
  estimated_saving_amount: number
  estimated_saving_percent: number
  currency: string
  risk_level: Severity
  status: 'PROPOSED' | 'APPROVED' | 'APPLIED' | 'REJECTED'
  simulation_only: boolean
  provenance: Provenance
}

export interface Workload {
  id: string
  workload_type: 'quality_check' | 'predictive_maintenance' | 'supply_chain'
  label: string
  plant_id: string
  plant_label: string
  department_label: string
  total_cost: number
  currency: string
  request_count: number
  average_cost_per_request: number
  trend_percent: number
  provenance: Provenance
}

/** Wrapper used by every internal /api/v1/* route so the client always knows the data's origin. */
export interface ApiEnvelope<T> {
  data: T
  source: 'live' | 'demo'
  fetched_at: string
}

export interface ApiErrorBody {
  error: string
  message: string
}
