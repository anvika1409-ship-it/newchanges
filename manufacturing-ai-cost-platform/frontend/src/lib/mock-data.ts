import type {
  Anomaly,
  BudgetStatus,
  CostSummary,
  CostTrend,
  Forecast,
  OptimizationRecommendation,
  Workload,
} from './types'

/**
 * Demo fixtures returned by the internal /api/v1/* route handlers when
 * BACKEND_API_BASE_URL is not configured. Shapes follow API_CONTRACT.yaml.
 * This is clearly demo data, never presented as ACTUAL provenance for
 * anything the contract defines as a live figure.
 */

const CURRENCY = 'INR'

function daysAgoIso(days: number, hour = 6): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - days)
  d.setUTCHours(hour, 0, 0, 0)
  return d.toISOString()
}

export function getMockCostSummary(): CostSummary {
  return {
    total_cost: 4218430,
    currency: CURRENCY,
    total_requests: 128940,
    total_tokens: 812_400_000,
    average_cost_per_request: 32.71,
    budget_consumed_percent: 78.4,
    projected_month_end_cost: 5240000,
    today_cost: 168230,
    month_to_date_cost: 4218430,
    provenance: 'ACTUAL',
    generated_at: new Date().toISOString(),
  }
}

export function getMockCostTrend(): CostTrend {
  const points = Array.from({ length: 30 }, (_, i) => {
    const dayIndex = 29 - i
    const base = 120000 + Math.sin(i / 3) * 25000 + i * 1800
    const isForecast = dayIndex < 0
    return {
      timestamp: daysAgoIso(dayIndex),
      cost: Math.round(base),
      provenance: 'ACTUAL' as const,
    }
  })

  // Append a short forward-looking forecast tail distinct from actuals.
  const forecastTail = Array.from({ length: 7 }, (_, i) => {
    const lastActual = points[points.length - 1].cost
    const projected = lastActual + (i + 1) * 4200
    const d = new Date()
    d.setUTCDate(d.getUTCDate() + i + 1)
    d.setUTCHours(6, 0, 0, 0)
    return {
      timestamp: d.toISOString(),
      cost: Math.round(projected),
      provenance: 'FORECAST' as const,
    }
  })

  return {
    granularity: 'day',
    currency: CURRENCY,
    points: [...points, ...forecastTail],
  }
}

export function getMockBudgetStatus(): BudgetStatus {
  return {
    currency: CURRENCY,
    items: [
      {
        scope_type: 'ENTERPRISE',
        scope_id: 'ent-01',
        scope_label: 'Enterprise-wide AI budget',
        period: 'MONTHLY',
        amount: 5400000,
        currency: CURRENCY,
        consumed_amount: 4218430,
        consumed_percent: 78.1,
        warning_threshold_percent: 80,
        critical_threshold_percent: 95,
        projected_overrun_amount: 0,
        projected_overrun_percent: -3,
        status: 'ON_TRACK',
      },
      {
        scope_type: 'PLANT',
        scope_id: 'plant-pune',
        scope_label: 'Pune Assembly Plant',
        period: 'MONTHLY',
        amount: 1200000,
        currency: CURRENCY,
        consumed_amount: 1092000,
        consumed_percent: 91,
        warning_threshold_percent: 80,
        critical_threshold_percent: 95,
        projected_overrun_amount: 84000,
        projected_overrun_percent: 7,
        status: 'WARNING',
      },
      {
        scope_type: 'PLANT',
        scope_id: 'plant-chennai',
        scope_label: 'Chennai Powertrain Plant',
        period: 'MONTHLY',
        amount: 950000,
        currency: CURRENCY,
        consumed_amount: 946500,
        consumed_percent: 99.6,
        warning_threshold_percent: 80,
        critical_threshold_percent: 95,
        projected_overrun_amount: 156000,
        projected_overrun_percent: 16.4,
        status: 'CRITICAL',
      },
      {
        scope_type: 'DEPARTMENT',
        scope_id: 'dept-quality',
        scope_label: 'Quality Engineering',
        period: 'MONTHLY',
        amount: 680000,
        currency: CURRENCY,
        consumed_amount: 401200,
        consumed_percent: 59,
        warning_threshold_percent: 80,
        critical_threshold_percent: 95,
        projected_overrun_amount: 0,
        projected_overrun_percent: -18,
        status: 'ON_TRACK',
      },
    ],
  }
}

export function getMockForecast(): Forecast {
  const points = Array.from({ length: 30 }, (_, i) => {
    const d = new Date()
    d.setUTCDate(d.getUTCDate() + i + 1)
    d.setUTCHours(6, 0, 0, 0)
    const base = 175000 + i * 3600
    return {
      timestamp: d.toISOString(),
      forecast_cost: Math.round(base),
      lower_bound: Math.round(base * 0.88),
      upper_bound: Math.round(base * 1.14),
    }
  })

  return {
    horizon_days: 30,
    currency: CURRENCY,
    model_confidence: 0.86,
    points,
    provenance: 'FORECAST',
  }
}

export function getMockAnomalies(): Anomaly[] {
  return [
    {
      id: 'anom-2201',
      detected_at: daysAgoIso(0, 3),
      severity: 'CRITICAL',
      scope_type: 'PLANT',
      scope_label: 'Chennai Powertrain Plant',
      metric: 'hourly_ai_spend',
      expected_value: 8200,
      observed_value: 26400,
      deviation_percent: 222,
      currency: CURRENCY,
      summary:
        'Predictive maintenance workload spend spiked 3.2x above baseline after a model fallback to a higher-cost provider.',
      status: 'OPEN',
    },
    {
      id: 'anom-2198',
      detected_at: daysAgoIso(1, 14),
      severity: 'HIGH',
      scope_type: 'WORKLOAD',
      scope_label: 'Supply Chain Risk Scoring',
      metric: 'cost_per_request',
      expected_value: 4.1,
      observed_value: 11.6,
      deviation_percent: 183,
      currency: CURRENCY,
      summary: 'Average cost per request nearly tripled following a routing policy change at 14:05 IST.',
      status: 'ACKNOWLEDGED',
    },
    {
      id: 'anom-2190',
      detected_at: daysAgoIso(2, 9),
      severity: 'MEDIUM',
      scope_type: 'DEPARTMENT',
      scope_label: 'Quality Engineering',
      metric: 'token_usage',
      expected_value: 1_200_000,
      observed_value: 1_640_000,
      deviation_percent: 37,
      currency: CURRENCY,
      summary: 'Token consumption for defect-image analysis trended above the 7-day baseline for two consecutive shifts.',
      status: 'OPEN',
    },
    {
      id: 'anom-2183',
      detected_at: daysAgoIso(4, 21),
      severity: 'LOW',
      scope_type: 'MODEL',
      scope_label: 'vision-defect-classifier-v3',
      metric: 'latency_cost_ratio',
      expected_value: 0.9,
      observed_value: 1.2,
      deviation_percent: 33,
      currency: CURRENCY,
      summary: 'Minor increase in cost-per-latency ratio, within acceptable operating range but trending upward.',
      status: 'RESOLVED',
    },
  ]
}

export function getMockOptimizationRecommendations(): OptimizationRecommendation[] {
  return [
    {
      id: 'opt-9001',
      workload_id: 'wl-quality-defect-vision',
      workload_label: 'Defect Vision Inspection',
      title: 'Downgrade routine inspections to a smaller vision model',
      description:
        'Simulation shows 41% of quality_check requests can route to a lower-cost model with no measurable drop in detection accuracy.',
      estimated_saving_amount: 186000,
      estimated_saving_percent: 22,
      currency: CURRENCY,
      risk_level: 'LOW',
      status: 'PROPOSED',
      simulation_only: true,
      provenance: 'SIMULATED',
    },
    {
      id: 'opt-8994',
      workload_id: 'wl-predictive-maintenance-core',
      workload_label: 'Predictive Maintenance Core',
      title: 'Cache repeated sensor-pattern lookups',
      description: 'Deduplicating near-identical sensor windows before model calls reduces redundant executions.',
      estimated_saving_amount: 94500,
      estimated_saving_percent: 11,
      currency: CURRENCY,
      risk_level: 'LOW',
      status: 'APPROVED',
      simulation_only: true,
      provenance: 'SIMULATED',
    },
    {
      id: 'opt-8977',
      workload_id: 'wl-supply-chain-risk',
      workload_label: 'Supply Chain Risk Scoring',
      title: 'Batch low-priority supplier risk checks',
      description: 'Grouping non-urgent scoring requests into hourly batches cuts per-request overhead materially.',
      estimated_saving_amount: 62000,
      estimated_saving_percent: 9,
      currency: CURRENCY,
      risk_level: 'MEDIUM',
      status: 'APPLIED',
      simulation_only: false,
      provenance: 'ESTIMATED',
    },
  ]
}

export function getMockWorkloads(): Workload[] {
  return [
    {
      id: 'wl-quality-defect-vision',
      workload_type: 'quality_check',
      label: 'Defect Vision Inspection',
      plant_id: 'plant-chennai',
      plant_label: 'Chennai Powertrain Plant',
      department_label: 'Quality Engineering',
      total_cost: 846200,
      currency: CURRENCY,
      request_count: 41230,
      average_cost_per_request: 20.53,
      trend_percent: 14.2,
      provenance: 'ACTUAL',
    },
    {
      id: 'wl-predictive-maintenance-core',
      workload_type: 'predictive_maintenance',
      label: 'Predictive Maintenance Core',
      plant_id: 'plant-chennai',
      plant_label: 'Chennai Powertrain Plant',
      department_label: 'Reliability Engineering',
      total_cost: 812900,
      currency: CURRENCY,
      request_count: 18760,
      average_cost_per_request: 43.33,
      trend_percent: 31.6,
      provenance: 'ACTUAL',
    },
    {
      id: 'wl-supply-chain-risk',
      workload_type: 'supply_chain',
      label: 'Supply Chain Risk Scoring',
      plant_id: 'plant-pune',
      plant_label: 'Pune Assembly Plant',
      department_label: 'Procurement',
      total_cost: 598400,
      currency: CURRENCY,
      request_count: 52100,
      average_cost_per_request: 11.49,
      trend_percent: -4.8,
      provenance: 'ACTUAL',
    },
    {
      id: 'wl-quality-torque-audit',
      workload_type: 'quality_check',
      label: 'Torque Audit Verification',
      plant_id: 'plant-pune',
      plant_label: 'Pune Assembly Plant',
      department_label: 'Quality Engineering',
      total_cost: 421700,
      currency: CURRENCY,
      request_count: 30880,
      average_cost_per_request: 13.65,
      trend_percent: 2.1,
      provenance: 'ACTUAL',
    },
    {
      id: 'wl-predictive-maintenance-hvac',
      workload_type: 'predictive_maintenance',
      label: 'HVAC Failure Prediction',
      plant_id: 'plant-nashik',
      plant_label: 'Nashik Component Plant',
      department_label: 'Facilities',
      total_cost: 287300,
      currency: CURRENCY,
      request_count: 9430,
      average_cost_per_request: 30.47,
      trend_percent: 6.4,
      provenance: 'ACTUAL',
    },
  ]
}
