/**
 * Adapter tests.
 *
 * The adapters are the contract boundary: they turn a wire response into
 * something the dashboard renders, and they are the one place a field the API
 * does not return must be written as `null`.
 *
 * The rule under test throughout: **an unknown is never a zero, and a blend is
 * never labelled with the stronger provenance.** Both mistakes render as a
 * confident number the platform cannot actually support.
 */

import { describe, expect, it } from 'vitest';
import {
  toAnomalyView,
  toBudgetStatusView,
  toCostSummaryView,
  toCostTrendView,
  toForecastView,
  toOptimizationView,
  toWorkloadViews,
} from './adapters';
import type {
  Anomaly,
  BudgetStatusList,
  CostSummary,
  CostTrend,
  ForecastList,
  OptimizationRecommendation,
  WorkloadList,
} from './types';

// ===========================================================================
// Cost summary
// ===========================================================================
function summary(overrides: Partial<CostSummary> = {}): CostSummary {
  return {
    actual_cost: 100,
    estimated_cost: 0,
    unavailable_cost_events: 0,
    currency: 'USD',
    total_requests: 10,
    total_tokens: 1000,
    average_cost_per_request: 10,
    budget_consumed_percent: 50,
    forecast_month_end_cost: 200,
    ...overrides,
  };
}

describe('toCostSummaryView', () => {
  it('derives total from the actual/estimated split without losing either', () => {
    const view = toCostSummaryView(summary({ actual_cost: 60, estimated_cost: 40 }));
    expect(view.total_cost).toBe(100);
    expect(view.actual_cost).toBe(60);
    expect(view.estimated_cost).toBe(40);
  });

  it('labels a blend ESTIMATED rather than overclaiming ACTUAL', () => {
    const view = toCostSummaryView(summary({ actual_cost: 60, estimated_cost: 40 }));
    expect(view.provenance).toBe('ESTIMATED');
  });

  it('labels purely measured spend ACTUAL', () => {
    expect(toCostSummaryView(summary({ estimated_cost: 0 })).provenance).toBe('ACTUAL');
  });

  it('reports UNAVAILABLE when there is no spend to describe', () => {
    const view = toCostSummaryView(summary({ actual_cost: 0, estimated_cost: 0 }));
    expect(view.provenance).toBe('UNAVAILABLE');
  });

  it('leaves windowed figures null rather than inventing them', () => {
    // today_cost and month_to_date_cost need their own windowed /cost/summary
    // calls. Null renders as "—"; a number here would have no source.
    const view = toCostSummaryView(summary());
    expect(view.today_cost).toBeNull();
    expect(view.month_to_date_cost).toBeNull();
  });

  it('preserves a null average rather than coercing it to zero', () => {
    // A period with no traffic has no average. Zero would read as "free".
    const view = toCostSummaryView(
      summary({ total_requests: 0, average_cost_per_request: null }),
    );
    expect(view.average_cost_per_request).toBeNull();
  });

  it('carries the forecast through under its own name', () => {
    expect(toCostSummaryView(summary()).forecast_month_end_cost).toBe(200);
  });
});

// ===========================================================================
// Cost trend
// ===========================================================================
describe('toCostTrendView', () => {
  const trend: CostTrend = {
    granularity: 'day',
    points: [
      {
        bucket_start: '2026-08-01T00:00:00Z',
        actual_cost: 30,
        estimated_cost: 10,
        currency: 'USD',
        total_requests: 5,
        total_tokens: 500,
      },
    ],
  };

  it('maps bucket_start to a timestamp and sums the split', () => {
    const view = toCostTrendView(trend);
    expect(view.points[0]!.timestamp).toBe('2026-08-01T00:00:00Z');
    expect(view.points[0]!.cost).toBe(40);
  });

  it('labels each point with its own blended provenance', () => {
    expect(toCostTrendView(trend).points[0]!.provenance).toBe('ESTIMATED');
  });

  it('survives an empty series', () => {
    const view = toCostTrendView({ granularity: 'day', points: [] });
    expect(view.points).toEqual([]);
    expect(view.currency).toBe('');
  });
});

// ===========================================================================
// Budget status
// ===========================================================================
describe('toBudgetStatusView', () => {
  const list: BudgetStatusList = {
    items: [
      {
        budget_id: 'b1',
        scope_type: 'PLANT',
        scope_id: 'plant-1',
        amount: 1000,
        consumed_actual_cost: 400,
        consumed_estimated_cost: 100,
        consumed_percent: 50,
        currency: 'USD',
        threshold_state: 'WARNING',
      },
    ],
    page: { total: 1, limit: 50, offset: 0 },
  };

  it('sums consumption across both provenances', () => {
    expect(toBudgetStatusView(list).items[0]!.consumed_amount).toBe(500);
  });

  it('uses the scope id as the label, since the API returns no display name', () => {
    expect(toBudgetStatusView(list).items[0]!.scope_label).toBe('plant-1');
  });

  it('carries the threshold state through unchanged', () => {
    expect(toBudgetStatusView(list).items[0]!.status).toBe('WARNING');
  });

  it('surfaces an unevaluable budget rather than showing it as healthy', () => {
    const unevaluable: BudgetStatusList = {
      items: [
        {
          ...list.items[0]!,
          consumed_percent: null,
          unevaluable_reason: 'no_cost_events_recorded',
        },
      ],
      page: list.page,
    };
    const view = toBudgetStatusView(unevaluable).items[0]!;
    expect(view.consumed_percent).toBeNull();
    expect(view.unevaluable_reason).toBe('no_cost_events_recorded');
  });
});

// ===========================================================================
// Forecast
// ===========================================================================
describe('toForecastView', () => {
  const list: ForecastList = {
    items: [
      {
        id: 'f1',
        scope_type: 'TENANT',
        scope_id: 't1',
        forecast_date: '2026-09-01',
        predicted_cost: 100,
        lower_bound: 80,
        upper_bound: 120,
        confidence: 0.8,
        forecast_model_name: 'seasonal-naive',
        forecast_model_version: '0.1.0',
        provenance: 'FORECAST',
      },
    ],
    page: { total: 1, limit: 50, offset: 0 },
  };

  it('is always labelled FORECAST', () => {
    expect(toForecastView(list).provenance).toBe('FORECAST');
  });

  it('averages reported confidence', () => {
    expect(toForecastView(list).model_confidence).toBeCloseTo(0.8);
  });

  it('reports null confidence when none was measured', () => {
    const noConfidence: ForecastList = {
      items: [{ ...list.items[0]!, confidence: null }],
      page: list.page,
    };
    expect(toForecastView(noConfidence).model_confidence).toBeNull();
  });
});

// ===========================================================================
// Anomaly
// ===========================================================================
describe('toAnomalyView', () => {
  const anomaly: Anomaly = {
    id: 'a1',
    timestamp: '2026-08-01T10:00:00Z',
    scope_type: 'PLANT',
    scope_id: 'plant-1',
    anomaly_type: 'cost_spike',
    severity: 'HIGH',
    expected_value: 100,
    actual_value: 400,
    deviation_percent: 300,
    reason: 'Retries against a higher-cost model.',
    status: 'OPEN',
    resolved_at: null,
  };

  it('maps the contract field names onto what the panel renders', () => {
    const view = toAnomalyView(anomaly);
    expect(view.detected_at).toBe('2026-08-01T10:00:00Z');
    expect(view.observed_value).toBe(400);
    expect(view.metric).toBe('cost_spike');
    expect(view.summary).toBe('Retries against a higher-cost model.');
  });

  it('falls back to identifiers rather than inventing a label', () => {
    const bare = toAnomalyView({
      ...anomaly,
      scope_id: null,
      anomaly_type: null,
      reason: null,
    });
    expect(bare.scope_label).toBe('PLANT');
    expect(bare.metric).toBe('Unspecified');
    expect(bare.summary).toBe('No reason recorded.');
  });

  it('preserves null metrics rather than zeroing them', () => {
    const view = toAnomalyView({ ...anomaly, expected_value: null, actual_value: null });
    expect(view.expected_value).toBeNull();
    expect(view.observed_value).toBeNull();
  });
});

// ===========================================================================
// Optimization
// ===========================================================================
function recommendation(
  overrides: Partial<OptimizationRecommendation> = {},
): OptimizationRecommendation {
  return {
    id: 'r1',
    workload_id: 'wl-1',
    current_strategy: 'All on the premium model',
    recommended_strategy: 'Tiered routing',
    estimated_saving: 500,
    estimated_saving_percent: 25,
    quality_impact_percent: -1,
    latency_impact_percent: -10,
    risk_level: 'MEDIUM',
    recommendation_reason: 'Most inspections classify SIMPLE.',
    status: 'PENDING_APPROVAL',
    provenance: 'SIMULATED',
    applied_policy_id: null,
    superseded_policy_id: null,
    created_at: '2026-08-01T00:00:00Z',
    approved_at: null,
    applied_at: null,
    rolled_back_at: null,
    approved_by: null,
    ...overrides,
  };
}

describe('toOptimizationView', () => {
  it('composes a title from the strategies rather than inventing one', () => {
    expect(toOptimizationView(recommendation()).title).toBe(
      'All on the premium model → Tiered routing',
    );
  });

  it('treats anything not yet applied as still simulated', () => {
    expect(toOptimizationView(recommendation()).simulation_only).toBe(true);
    expect(
      toOptimizationView(recommendation({ status: 'APPLIED' })).simulation_only,
    ).toBe(false);
  });

  it('preserves a null saving rather than showing zero', () => {
    const view = toOptimizationView(
      recommendation({ estimated_saving: null, estimated_saving_percent: null }),
    );
    expect(view.estimated_saving_amount).toBeNull();
    expect(view.estimated_saving_percent).toBeNull();
  });

  it('carries the lifecycle status through unchanged', () => {
    expect(toOptimizationView(recommendation({ status: 'ROLLED_BACK' })).status).toBe(
      'ROLLED_BACK',
    );
  });
});

// ===========================================================================
// Workloads
// ===========================================================================
describe('toWorkloadViews', () => {
  const list: WorkloadList = {
    items: [
      {
        id: 'wl-1',
        plant_id: 'plant-1',
        department_id: 'dept-1',
        name: 'Surface inspection',
        workload_type: 'quality_check',
        description: null,
        business_priority: 'HIGH',
        risk_level: 'HIGH',
        status: 'ACTIVE',
      },
    ],
    page: { total: 1, limit: 50, offset: 0 },
  };

  it('reports null spend when no cost row matches, not zero', () => {
    // /workloads carries no cost. Absent data is not "spent nothing".
    const view = toWorkloadViews(list)[0]!;
    expect(view.total_cost).toBeNull();
    expect(view.request_count).toBeNull();
    expect(view.provenance).toBe('UNAVAILABLE');
  });

  it('joins spend from a cost breakdown when one is supplied', () => {
    const view = toWorkloadViews(list, {
      dimension: 'plant',
      items: [
        {
          id: 'wl-1',
          name: null,
          actual_cost: 80,
          estimated_cost: 20,
          currency: 'USD',
          total_requests: 10,
          total_tokens: 1000,
        },
      ],
    })[0]!;
    expect(view.total_cost).toBe(100);
    expect(view.average_cost_per_request).toBe(10);
    expect(view.provenance).toBe('ESTIMATED');
  });

  it('never reports a trend, because no endpoint returns one', () => {
    expect(toWorkloadViews(list)[0]!.trend_percent).toBeNull();
  });
});
