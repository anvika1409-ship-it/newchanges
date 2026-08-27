/**
 * Frontend types, in two clearly separated layers.
 *
 * ── Layer 1: wire types ────────────────────────────────────────────────────
 * Exact mirrors of the schemas in API_CONTRACT.yaml. Field names and
 * nullability match the contract precisely. These are what the API returns and
 * they are never edited to suit a component — the contract is the
 * synchronization point (AI_DEVELOPMENT_RULES.md sections 20 and 34).
 *
 * ── Layer 2: view models ───────────────────────────────────────────────────
 * What the dashboard renders. Every field is either copied from a wire type or
 * derived from one by an adapter in `lib/adapters.ts`. A value the API cannot
 * supply is typed `| null` and rendered as "—" rather than invented.
 *
 * The two layers exist because the previous single layer drifted: it carried
 * fields like `title`, `workload_label` and `today_cost` that no endpoint
 * returns, so the types compiled while describing a response that never
 * existed. Keeping the wire shape honest is what makes that impossible.
 */

// ===========================================================================
// Shared
// ===========================================================================

/**
 * How a numeric value was produced. Always displayed alongside the value
 * (AI_DEVELOPMENT_RULES.md sections 41 and 42).
 *
 * `UNAVAILABLE` means the figure could not be computed. It is not zero.
 */
export type Provenance = 'ACTUAL' | 'ESTIMATED' | 'FORECAST' | 'SIMULATED' | 'UNAVAILABLE'

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export type BudgetPeriod = 'DAILY' | 'MONTHLY' | 'QUARTERLY' | 'ANNUAL'

export type BudgetScopeType =
  | 'ENTERPRISE'
  | 'TENANT'
  | 'PLANT'
  | 'DEPARTMENT'
  | 'WORKLOAD'
  | 'AGENT'
  | 'MODEL'

export type WorkloadType = 'quality_check' | 'predictive_maintenance' | 'supply_chain'

/** Pagination envelope shared by every collection response. */
export interface PageInfo {
  total: number
  limit: number
  offset: number
}

/** The `Error` schema returned by every failing endpoint. */
export interface ApiError {
  code: string
  message: string
  request_id?: string | null
  details?: Record<string, unknown> | null
}

// ===========================================================================
// Layer 1 — wire types (exact contract mirrors)
// ===========================================================================

/** GET /cost/summary — `CostSummary`. */
export interface CostSummary {
  /** Sum of cost events with provenance ACTUAL. */
  actual_cost: number
  /** Sum of cost events with provenance ESTIMATED. Never added to the above. */
  estimated_cost: number
  /** Executions whose cost could not be computed. Counted, not zeroed. */
  unavailable_cost_events: number
  currency: string
  total_requests: number
  total_tokens: number
  /** Null when there was no traffic — a period with no requests has no average. */
  average_cost_per_request: number | null
  budget_consumed_percent: number | null
  /** A FORECAST value. Must be displayed as a forecast, not as spend. */
  forecast_month_end_cost: number | null
}

/** GET /cost/trend — `CostTrendPoint`. */
export interface CostTrendPoint {
  bucket_start: string
  actual_cost: number
  estimated_cost: number
  currency: string
  total_requests: number
  total_tokens: number
}

/** GET /cost/trend — `CostTrend`. */
export interface CostTrend {
  granularity: 'hour' | 'day' | 'week' | 'month'
  points: CostTrendPoint[]
}

/** GET /cost/by-* — `CostBreakdownItem`. */
export interface CostBreakdownItem {
  id: string | null
  name: string | null
  actual_cost: number
  estimated_cost: number
  currency: string
  total_requests: number
  total_tokens: number
}

/** GET /cost/by-* — `CostBreakdown`. */
export interface CostBreakdown {
  dimension: 'model' | 'agent' | 'plant'
  items: CostBreakdownItem[]
}

/** GET /budgets/status — `BudgetStatus`. */
export interface BudgetStatus {
  budget_id: string
  scope_type: BudgetScopeType
  scope_id: string
  amount: number
  consumed_actual_cost: number
  consumed_estimated_cost: number
  consumed_percent: number | null
  currency: string
  threshold_state: 'NORMAL' | 'WARNING' | 'CRITICAL' | 'EXCEEDED'
  /** Set when the budget could not be evaluated at all. */
  unevaluable_reason?: string | null
}

export interface BudgetStatusList {
  items: BudgetStatus[]
  page: PageInfo
}

/** GET /forecasts — `Forecast`. One row per forecast date. */
export interface Forecast {
  id: string
  scope_type: string | null
  scope_id: string | null
  forecast_date: string
  predicted_cost: number
  lower_bound: number | null
  upper_bound: number | null
  confidence: number | null
  /** The forecasting algorithm. Unrelated to an LLM's model name. */
  forecast_model_name: string | null
  forecast_model_version: string | null
  provenance: 'FORECAST'
}

export interface ForecastList {
  items: Forecast[]
  page: PageInfo
}

/** GET /anomalies — `Anomaly`. */
export interface Anomaly {
  id: string
  timestamp: string
  scope_type: string | null
  scope_id: string | null
  anomaly_type: string | null
  severity: Severity
  expected_value: number | null
  actual_value: number | null
  deviation_percent: number | null
  reason: string | null
  status: string | null
  resolved_at: string | null
}

export interface AnomalyList {
  items: Anomaly[]
  page: PageInfo
}

export type RecommendationStatus =
  | 'DRAFT'
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'APPLIED'
  | 'ROLLED_BACK'

/** GET /optimization/recommendations — `OptimizationRecommendation`. */
export interface OptimizationRecommendation {
  id: string
  workload_id: string | null
  current_strategy: string | null
  recommended_strategy: string | null
  /** Estimated, never realized savings. */
  estimated_saving: number | null
  estimated_saving_percent: number | null
  quality_impact_percent: number | null
  latency_impact_percent: number | null
  risk_level: Severity
  recommendation_reason: string | null
  status: RecommendationStatus
  provenance: 'ESTIMATED' | 'SIMULATED'
  applied_policy_id: string | null
  superseded_policy_id: string | null
  created_at: string
  approved_at: string | null
  applied_at: string | null
  rolled_back_at: string | null
  approved_by: string | null
}

export interface OptimizationRecommendationList {
  items: OptimizationRecommendation[]
  page: PageInfo
}

/** GET /workloads — `Workload`. */
export interface Workload {
  id: string
  plant_id: string
  department_id: string
  name: string
  workload_type: WorkloadType
  description: string | null
  business_priority: 'LOW' | 'NORMAL' | 'HIGH' | 'CRITICAL'
  risk_level: Severity
  status: string
}

export interface WorkloadList {
  items: Workload[]
  page: PageInfo
}

// ===========================================================================
// Layer 2 — view models (what the dashboard renders)
// ===========================================================================

/**
 * Cost KPIs.
 *
 * `total_cost` is a derived convenience — actual plus estimated — and is only
 * ever shown next to the split, never instead of it.
 *
 * `today_cost` and `month_to_date_cost` are null: they need their own windowed
 * `/cost/summary` calls (the contract's `from`/`to` parameters), which the
 * dashboard does not yet make. Null renders as "—". They were previously
 * invented fields on the wire type, which is how a number with no source ended
 * up on screen.
 */
export interface CostSummaryView {
  actual_cost: number
  estimated_cost: number
  total_cost: number
  unavailable_cost_events: number
  currency: string
  total_requests: number
  total_tokens: number
  average_cost_per_request: number | null
  budget_consumed_percent: number | null
  forecast_month_end_cost: number | null
  today_cost: number | null
  month_to_date_cost: number | null
  /** Weakest provenance present, so a blend is never overclaimed as measured. */
  provenance: Provenance
}

export interface CostTrendPointView {
  timestamp: string
  cost: number
  currency: string
  provenance: Provenance
}

export interface CostTrendView {
  granularity: 'hour' | 'day' | 'week' | 'month'
  currency: string
  points: CostTrendPointView[]
}

/** `scope_label` is the scope id — the API returns no display name for it. */
export interface BudgetStatusItemView {
  budget_id: string
  scope_type: BudgetScopeType
  scope_id: string
  scope_label: string
  amount: number
  consumed_amount: number
  consumed_percent: number | null
  currency: string
  status: 'NORMAL' | 'WARNING' | 'CRITICAL' | 'EXCEEDED'
  unevaluable_reason: string | null
}

export interface BudgetStatusView {
  currency: string
  items: BudgetStatusItemView[]
}

export interface ForecastPointView {
  timestamp: string
  forecast_cost: number
  lower_bound: number | null
  upper_bound: number | null
}

export interface ForecastView {
  horizon_days: number
  currency: string
  /** Mean confidence across points, or null when none was reported. */
  model_confidence: number | null
  points: ForecastPointView[]
  provenance: 'FORECAST'
}

export interface AnomalyView {
  id: string
  detected_at: string
  severity: Severity
  scope_label: string
  metric: string
  expected_value: number | null
  observed_value: number | null
  deviation_percent: number | null
  summary: string
  status: string
}

/**
 * A recommendation as the panel shows it.
 *
 * `title` and `description` are composed from the strategies and the reason.
 * The API returns no title, and inventing one would put words in its mouth.
 */
export interface OptimizationRecommendationView {
  id: string
  workload_id: string | null
  workload_label: string
  title: string
  description: string
  estimated_saving_amount: number | null
  estimated_saving_percent: number | null
  quality_impact_percent: number | null
  latency_impact_percent: number | null
  currency: string | null
  risk_level: Severity
  status: RecommendationStatus
  /** True unless the recommendation has actually been applied. */
  simulation_only: boolean
  provenance: 'ESTIMATED' | 'SIMULATED'
  applied_policy_id: string | null
}

/**
 * A workload row with its spend.
 *
 * Spend comes from a cost breakdown, not from `/workloads` — the workload
 * record carries no cost. `trend_percent` is null: no endpoint returns a
 * per-workload trend, and the previous type asserted one.
 */
export interface WorkloadView {
  id: string
  label: string
  workload_type: WorkloadType | null
  plant_id: string | null
  plant_label: string | null
  department_label: string | null
  total_cost: number | null
  currency: string | null
  request_count: number | null
  average_cost_per_request: number | null
  trend_percent: number | null
  provenance: Provenance
}

// ===========================================================================
// Transport
// ===========================================================================

/** Wrapper telling the UI whether it is looking at live or demo data. */
export interface ApiEnvelope<T> {
  data: T
  source: 'live' | 'demo'
  fetched_at: string
}
