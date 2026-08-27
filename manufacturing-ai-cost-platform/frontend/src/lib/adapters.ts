/**
 * Wire → view adapters.
 *
 * The single place where an API response becomes something the dashboard
 * renders. Keeping the mapping here means every derivation is visible in one
 * file, and a field the API does not return has to be written as `null` rather
 * than quietly appearing in a component.
 *
 * Two rules:
 *
 * * **Derive, never invent.** A label falls back to the id the API did return.
 *   Nothing is fabricated to fill a gap.
 * * **Unknown stays null.** A figure the API could not compute is `null`, which
 *   the UI renders as "—". It is never coerced to 0, because 0 reads as "free"
 *   or "no change" (AI_DEVELOPMENT_RULES.md sections 41 and 42).
 */

import type {
  Anomaly,
  AnomalyView,
  BudgetStatus,
  BudgetStatusList,
  BudgetStatusView,
  CostBreakdown,
  CostSummary,
  CostSummaryView,
  CostTrend,
  CostTrendView,
  ForecastList,
  ForecastView,
  OptimizationRecommendation,
  OptimizationRecommendationList,
  OptimizationRecommendationView,
  Provenance,
  WorkloadList,
  WorkloadView,
} from './types'

/**
 * The weakest provenance present in a blend.
 *
 * A total mixing measured and estimated spend is ESTIMATED, not ACTUAL —
 * overclaiming the stronger label would present a partly-guessed figure as
 * measured.
 */
function blendedProvenance(actual: number, estimated: number): Provenance {
  if (actual === 0 && estimated === 0) return 'UNAVAILABLE'
  return estimated > 0 ? 'ESTIMATED' : 'ACTUAL'
}

export function toCostSummaryView(wire: CostSummary): CostSummaryView {
  return {
    actual_cost: wire.actual_cost,
    estimated_cost: wire.estimated_cost,
    total_cost: wire.actual_cost + wire.estimated_cost,
    unavailable_cost_events: wire.unavailable_cost_events,
    currency: wire.currency,
    total_requests: wire.total_requests,
    total_tokens: wire.total_tokens,
    average_cost_per_request: wire.average_cost_per_request,
    budget_consumed_percent: wire.budget_consumed_percent,
    forecast_month_end_cost: wire.forecast_month_end_cost,
    // Need their own windowed /cost/summary calls, which the dashboard does not
    // yet make. Null renders as "—" rather than showing an unsourced number.
    today_cost: null,
    month_to_date_cost: null,
    provenance: blendedProvenance(wire.actual_cost, wire.estimated_cost),
  }
}

export function toCostTrendView(wire: CostTrend): CostTrendView {
  const currency = wire.points[0]?.currency ?? ''
  return {
    granularity: wire.granularity,
    currency,
    points: wire.points.map((point) => ({
      timestamp: point.bucket_start,
      cost: point.actual_cost + point.estimated_cost,
      currency: point.currency,
      provenance: blendedProvenance(point.actual_cost, point.estimated_cost),
    })),
  }
}

function toBudgetItemView(wire: BudgetStatus) {
  return {
    budget_id: wire.budget_id,
    scope_type: wire.scope_type,
    scope_id: wire.scope_id,
    // The API returns no display name for a scope, so the id stands in.
    scope_label: wire.scope_id,
    amount: wire.amount,
    consumed_amount: wire.consumed_actual_cost + wire.consumed_estimated_cost,
    consumed_percent: wire.consumed_percent,
    currency: wire.currency,
    status: wire.threshold_state,
    unevaluable_reason: wire.unevaluable_reason ?? null,
  }
}

export function toBudgetStatusView(wire: BudgetStatusList): BudgetStatusView {
  return {
    currency: wire.items[0]?.currency ?? '',
    items: wire.items.map(toBudgetItemView),
  }
}

export function toForecastView(wire: ForecastList): ForecastView {
  const confidences = wire.items
    .map((item) => item.confidence)
    .filter((value): value is number => value !== null)

  return {
    horizon_days: wire.items.length,
    currency: '',
    model_confidence:
      confidences.length > 0
        ? confidences.reduce((sum, value) => sum + value, 0) / confidences.length
        : null,
    points: wire.items.map((item) => ({
      timestamp: item.forecast_date,
      forecast_cost: item.predicted_cost,
      lower_bound: item.lower_bound,
      upper_bound: item.upper_bound,
    })),
    provenance: 'FORECAST',
  }
}

export function toAnomalyView(wire: Anomaly): AnomalyView {
  return {
    id: wire.id,
    detected_at: wire.timestamp,
    severity: wire.severity,
    scope_label: wire.scope_id ?? wire.scope_type ?? 'Unknown scope',
    metric: wire.anomaly_type ?? 'Unspecified',
    expected_value: wire.expected_value,
    observed_value: wire.actual_value,
    deviation_percent: wire.deviation_percent,
    summary: wire.reason ?? 'No reason recorded.',
    status: wire.status ?? 'OPEN',
  }
}

/**
 * Compose a human-readable title from the strategies.
 *
 * The API returns no title. Composing one from fields it did return keeps the
 * panel readable without putting words in the API's mouth.
 */
function recommendationTitle(wire: OptimizationRecommendation): string {
  if (wire.current_strategy && wire.recommended_strategy) {
    return `${wire.current_strategy} → ${wire.recommended_strategy}`
  }
  return wire.recommended_strategy ?? wire.current_strategy ?? 'Optimization recommendation'
}

export function toOptimizationView(
  wire: OptimizationRecommendation,
): OptimizationRecommendationView {
  return {
    id: wire.id,
    workload_id: wire.workload_id,
    workload_label: wire.workload_id ?? 'All workloads',
    title: recommendationTitle(wire),
    description: wire.recommendation_reason ?? 'No reason recorded.',
    estimated_saving_amount: wire.estimated_saving,
    estimated_saving_percent: wire.estimated_saving_percent,
    quality_impact_percent: wire.quality_impact_percent,
    latency_impact_percent: wire.latency_impact_percent,
    // The recommendation carries no currency; the cost endpoints own that.
    currency: null,
    risk_level: wire.risk_level,
    status: wire.status,
    // Anything not yet applied is still a projection.
    simulation_only: wire.status !== 'APPLIED',
    provenance: wire.provenance,
    applied_policy_id: wire.applied_policy_id,
  }
}

export function toOptimizationViews(
  wire: OptimizationRecommendationList,
): OptimizationRecommendationView[] {
  return wire.items.map(toOptimizationView)
}

/**
 * Workload rows joined to their spend.
 *
 * `/workloads` carries no cost, so spend is looked up from a plant/agent cost
 * breakdown when one is supplied. A workload with no matching row reports null
 * spend rather than zero — absent data is not "spent nothing".
 */
export function toWorkloadViews(
  wire: WorkloadList,
  breakdown?: CostBreakdown,
): WorkloadView[] {
  const spendById = new Map(
    (breakdown?.items ?? []).map((item) => [item.id, item] as const),
  )

  return wire.items.map((workload) => {
    const spend = spendById.get(workload.id)
    const total = spend ? spend.actual_cost + spend.estimated_cost : null
    return {
      id: workload.id,
      label: workload.name,
      workload_type: workload.workload_type,
      plant_id: workload.plant_id,
      // No display names are returned for plants or departments.
      plant_label: workload.plant_id,
      department_label: workload.department_id,
      total_cost: total,
      currency: spend?.currency ?? null,
      request_count: spend?.total_requests ?? null,
      average_cost_per_request:
        spend && spend.total_requests > 0 ? total! / spend.total_requests : null,
      // No endpoint returns a per-workload trend. The previous type asserted
      // one, so the UI showed a movement nothing had measured.
      trend_percent: null,
      provenance: spend
        ? blendedProvenance(spend.actual_cost, spend.estimated_cost)
        : 'UNAVAILABLE',
    }
  })
}
