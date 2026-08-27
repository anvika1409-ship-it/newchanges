/**
 * Demo fixtures.
 *
 * Every factory returns the **wire shape** defined in API_CONTRACT.yaml, not a
 * shape convenient for a component. That is deliberate: fixtures that diverge
 * from the contract are how the previous types drifted — the mocks compiled,
 * the components rendered, and nobody noticed the API returned something else
 * entirely. Demo mode now runs through the same adapters as live data, so a
 * contract change breaks both together.
 *
 * These values are illustrative and are always surfaced with
 * `source: 'demo'` so the UI can label them. They are never presented as real
 * operational data (AI_DEVELOPMENT_RULES.md sections 41 and 42).
 */

import type {
  AnomalyList,
  BudgetStatusList,
  CostSummary,
  CostTrend,
  ForecastList,
  OptimizationRecommendationList,
  PageInfo,
  WorkloadList,
} from './types'

const CURRENCY = 'USD'

function page(total: number): PageInfo {
  return { total, limit: 50, offset: 0 }
}

/** Hours ago, as an ISO timestamp. */
function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

/** Days ahead, as a date-only string. */
function daysAhead(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10)
}

export function getMockCostSummary(): CostSummary {
  return {
    actual_cost: 12_480.55,
    estimated_cost: 1_920.4,
    unavailable_cost_events: 37,
    currency: CURRENCY,
    total_requests: 184_209,
    total_tokens: 96_412_880,
    average_cost_per_request: 0.0782,
    budget_consumed_percent: 72.4,
    forecast_month_end_cost: 19_850.0,
  }
}

export function getMockCostTrend(): CostTrend {
  const points = Array.from({ length: 14 }, (_, index) => {
    const day = 13 - index
    const actual = 780 + Math.round(Math.sin(index / 2) * 120) + index * 12
    return {
      bucket_start: new Date(Date.now() - day * 86_400_000).toISOString(),
      actual_cost: actual,
      estimated_cost: Math.round(actual * 0.14),
      currency: CURRENCY,
      total_requests: 11_500 + index * 180,
      total_tokens: 6_100_000 + index * 90_000,
    }
  })
  return { granularity: 'day', points }
}

export function getMockBudgetStatus(): BudgetStatusList {
  const items: BudgetStatusList['items'] = [
    {
      budget_id: 'bud-ent-001',
      scope_type: 'ENTERPRISE',
      scope_id: 'enterprise',
      amount: 20_000,
      consumed_actual_cost: 12_480.55,
      consumed_estimated_cost: 1_920.4,
      consumed_percent: 72.0,
      currency: CURRENCY,
      threshold_state: 'WARNING',
    },
    {
      budget_id: 'bud-plant-01',
      scope_type: 'PLANT',
      scope_id: 'plant-chennai-01',
      amount: 8_000,
      consumed_actual_cost: 7_640.2,
      consumed_estimated_cost: 240.0,
      consumed_percent: 98.5,
      currency: CURRENCY,
      threshold_state: 'CRITICAL',
    },
    {
      budget_id: 'bud-plant-02',
      scope_type: 'PLANT',
      scope_id: 'plant-pune-02',
      amount: 6_000,
      consumed_actual_cost: 2_310.9,
      consumed_estimated_cost: 180.5,
      consumed_percent: 41.5,
      currency: CURRENCY,
      threshold_state: 'NORMAL',
    },
    {
      budget_id: 'bud-dept-qa',
      scope_type: 'DEPARTMENT',
      scope_id: 'dept-quality-assurance',
      amount: 4_000,
      consumed_actual_cost: 4_310.0,
      consumed_estimated_cost: 95.0,
      consumed_percent: 110.1,
      currency: CURRENCY,
      threshold_state: 'EXCEEDED',
    },
    {
      // A budget the platform could not evaluate. Reported, not hidden, and not
      // silently treated as healthy.
      budget_id: 'bud-model-vision',
      scope_type: 'MODEL',
      scope_id: 'model-vision-primary',
      amount: 2_500,
      consumed_actual_cost: 0,
      consumed_estimated_cost: 0,
      consumed_percent: null,
      currency: CURRENCY,
      threshold_state: 'NORMAL',
      unevaluable_reason: 'no_cost_events_recorded',
    },
  ]
  return { items, page: page(items.length) }
}

export function getMockForecast(): ForecastList {
  const items = Array.from({ length: 14 }, (_, index) => {
    const predicted = 820 + index * 26
    return {
      id: `fc-${index + 1}`,
      scope_type: 'TENANT',
      scope_id: 'tenant-demo',
      forecast_date: daysAhead(index + 1),
      predicted_cost: predicted,
      lower_bound: Math.round(predicted * 0.86),
      upper_bound: Math.round(predicted * 1.15),
      confidence: 0.82,
      forecast_model_name: 'seasonal-naive',
      forecast_model_version: '0.1.0',
      provenance: 'FORECAST' as const,
    }
  })
  return { items, page: page(items.length) }
}

export function getMockAnomalies(): AnomalyList {
  const items: AnomalyList['items'] = [
    {
      id: 'anom-001',
      timestamp: hoursAgo(3),
      scope_type: 'PLANT',
      scope_id: 'plant-chennai-01',
      anomaly_type: 'cost_spike',
      severity: 'CRITICAL',
      expected_value: 310.0,
      actual_value: 1_240.6,
      deviation_percent: 300.2,
      reason: 'Vision workload retried repeatedly against a higher-cost model.',
      status: 'OPEN',
      resolved_at: null,
    },
    {
      id: 'anom-002',
      timestamp: hoursAgo(9),
      scope_type: 'MODEL',
      scope_id: 'model-reasoning-large',
      anomaly_type: 'token_volume',
      severity: 'HIGH',
      expected_value: 4_200_000,
      actual_value: 7_950_000,
      deviation_percent: 89.3,
      reason: 'Context length grew after a prompt change.',
      status: 'ACKNOWLEDGED',
      resolved_at: null,
    },
    {
      id: 'anom-003',
      timestamp: hoursAgo(26),
      scope_type: 'DEPARTMENT',
      scope_id: 'dept-maintenance',
      anomaly_type: 'latency',
      severity: 'MEDIUM',
      expected_value: 1_850,
      actual_value: 3_020,
      deviation_percent: 63.2,
      reason: 'Provider latency rose during a regional incident.',
      status: 'RESOLVED',
      resolved_at: hoursAgo(20),
    },
    {
      id: 'anom-004',
      timestamp: hoursAgo(40),
      scope_type: 'WORKLOAD',
      scope_id: 'wl-supply-chain',
      anomaly_type: 'cost_per_request',
      severity: 'LOW',
      expected_value: 0.071,
      actual_value: 0.089,
      deviation_percent: 25.4,
      reason: 'Mix shifted toward a higher-cost model.',
      status: 'OPEN',
      resolved_at: null,
    },
  ]
  return { items, page: page(items.length) }
}

export function getMockOptimizationRecommendations(): OptimizationRecommendationList {
  const items: OptimizationRecommendationList['items'] = [
    {
      id: 'rec-001',
      workload_id: 'wl-quality-check',
      current_strategy: 'All inspections on the high-capability vision model',
      recommended_strategy: 'Route simple inspections to the lower-cost vision model',
      estimated_saving: 3_180.0,
      estimated_saving_percent: 24.6,
      quality_impact_percent: -0.8,
      latency_impact_percent: -12.0,
      risk_level: 'MEDIUM',
      recommendation_reason:
        'Simple single-part images account for 61% of volume and were classified SIMPLE.',
      status: 'PENDING_APPROVAL',
      provenance: 'SIMULATED',
      applied_policy_id: null,
      superseded_policy_id: null,
      created_at: hoursAgo(6),
      approved_at: null,
      applied_at: null,
      rolled_back_at: null,
      approved_by: null,
    },
    {
      id: 'rec-002',
      workload_id: 'wl-maintenance',
      current_strategy: 'Full sensor history in every prompt',
      recommended_strategy: 'Trim context to the last 24 hours plus anomalies',
      estimated_saving: 1_420.5,
      estimated_saving_percent: 18.2,
      quality_impact_percent: null,
      latency_impact_percent: -8.5,
      risk_level: 'LOW',
      recommendation_reason: 'Median prompt carries 3.2x the context the model uses.',
      status: 'APPROVED',
      provenance: 'ESTIMATED',
      applied_policy_id: null,
      superseded_policy_id: null,
      created_at: hoursAgo(30),
      approved_at: hoursAgo(4),
      applied_at: null,
      rolled_back_at: null,
      approved_by: 'finops-lead',
    },
    {
      id: 'rec-003',
      workload_id: 'wl-supply-chain',
      current_strategy: 'Single-pass reasoning on every request',
      recommended_strategy: 'Cache supplier lookups for 6 hours',
      estimated_saving: 890.0,
      estimated_saving_percent: 11.4,
      quality_impact_percent: 0.0,
      latency_impact_percent: -31.0,
      risk_level: 'LOW',
      recommendation_reason: 'Supplier data changes far less often than it is queried.',
      status: 'APPLIED',
      provenance: 'ESTIMATED',
      applied_policy_id: 'pol-supply-v4',
      superseded_policy_id: 'pol-supply-v3',
      created_at: hoursAgo(96),
      approved_at: hoursAgo(72),
      applied_at: hoursAgo(70),
      rolled_back_at: null,
      approved_by: 'finops-lead',
    },
  ]
  return { items, page: page(items.length) }
}

export function getMockWorkloads(): WorkloadList {
  const items: WorkloadList['items'] = [
    {
      id: 'wl-quality-check',
      plant_id: 'plant-chennai-01',
      department_id: 'dept-quality-assurance',
      name: 'Surface defect inspection',
      workload_type: 'quality_check',
      description: 'Multimodal inspection of finished components.',
      business_priority: 'HIGH',
      risk_level: 'HIGH',
      status: 'ACTIVE',
    },
    {
      id: 'wl-maintenance',
      plant_id: 'plant-pune-02',
      department_id: 'dept-maintenance',
      name: 'Spindle failure prediction',
      workload_type: 'predictive_maintenance',
      description: 'Anomaly detection plus reasoning over maintenance history.',
      business_priority: 'CRITICAL',
      risk_level: 'CRITICAL',
      status: 'ACTIVE',
    },
    {
      id: 'wl-supply-chain',
      plant_id: 'plant-chennai-01',
      department_id: 'dept-logistics',
      name: 'Supplier routing advisor',
      workload_type: 'supply_chain',
      description: 'Ranks supplier and routing options against lead times.',
      business_priority: 'NORMAL',
      risk_level: 'MEDIUM',
      status: 'ACTIVE',
    },
  ]
  return { items, page: page(items.length) }
}
