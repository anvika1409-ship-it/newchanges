import useSWR from 'swr';
import {
  toAnomalyView,
  toBudgetStatusView,
  toCostSummaryView,
  toCostTrendView,
  toForecastView,
  toOptimizationViews,
  toWorkloadViews,
} from '../lib/adapters';
import type {
  AnomalyList,
  AnomalyView,
  ApiEnvelope,
  BudgetStatusList,
  BudgetStatusView,
  CostSummary,
  CostSummaryView,
  CostTrend,
  CostTrendView,
  ForecastList,
  ForecastView,
  OptimizationRecommendationList,
  OptimizationRecommendationView,
  WorkloadList,
  WorkloadView,
} from '../lib/types';
import {
  getMockAnomalies,
  getMockBudgetStatus,
  getMockCostSummary,
  getMockCostTrend,
  getMockForecast,
  getMockOptimizationRecommendations,
  getMockWorkloads,
} from '../lib/mock-data';
import { apiClient, ApiRequestError, hasAuthToken } from '../services/apiClient';

/**
 * Dashboard data hooks.
 *
 * Per ARCHITECTURE.md section 7, the frontend never talks to GenAILab or the
 * database directly; it only calls the FastAPI backend under `/api/v1`,
 * reached at the same origin (Vite dev proxy locally, nginx reverse proxy in
 * production). No server-side bridge or secret lives in this bundle
 * (AI_DEVELOPMENT_RULES.md section 19).
 *
 * Each hook fetches the **wire shape** defined in API_CONTRACT.yaml and runs it
 * through an adapter from `lib/adapters.ts` before handing a view model to the
 * components. Fetching the contract shape is what keeps the types honest: a
 * response that stops matching the contract now fails here rather than being
 * absorbed by a hand-written type that happens to describe the component's
 * needs.
 *
 * If the backend is unreachable (e.g. exploring the UI before the backend is
 * deployed), each hook falls back to local fixture data and labels the envelope
 * `source: 'demo'` so the UI can show a "Demo data" indicator instead of
 * silently presenting invented numbers as real
 * (AI_DEVELOPMENT_RULES.md sections 41-42). The fixtures are themselves in the
 * contract's wire shape, so demo mode exercises the same adapters as live data.
 */

const swrConfig = {
  refreshInterval: 60_000,
  revalidateOnFocus: false,
  shouldRetryOnError: true,
  errorRetryCount: 2,
};

export interface FilterParams {
  plantId?: string;
  departmentId?: string;
}

function toQuery(params: Record<string, string | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value) out[key] = value;
  }
  return out;
}

/**
 * Fetches `path`, adapts the wire response to a view model, and tags the origin.
 *
 * On an unreachable backend the fixture is used instead. A backend that is
 * reachable but genuinely errors (e.g. 500, 403) still surfaces as an error —
 * masking a real failure as demo data would hide an outage behind plausible
 * numbers.
 */
async function fetchWithFallback<TWire, TView>(
  path: string,
  query: Record<string, string>,
  mockFactory: () => TWire,
  adapt: (wire: TWire) => TView,
): Promise<ApiEnvelope<TView>> {
  try {
    if (!hasAuthToken()) {
      throw new ApiRequestError(401, {
        code: 'unauthorized',
        message: 'Authentication required',
        request_id: null,
        details: null,
      });
    }
    const wire = await apiClient.get<TWire>(path, { query });
    return { data: adapt(wire), source: 'live', fetched_at: new Date().toISOString() };
  } catch (err) {
    if (err instanceof ApiRequestError && err.status !== 0) {
      return {
        data: adapt(mockFactory()),
        source: 'demo',
        fetched_at: new Date().toISOString(),
      };
    }
    return {
      data: adapt(mockFactory()),
      source: 'demo',
      fetched_at: new Date().toISOString(),
    };
  }
}

export function useCostSummary(filters: FilterParams = {}) {
  const query = toQuery({ plant_id: filters.plantId, department_id: filters.departmentId });
  return useSWR<ApiEnvelope<CostSummaryView>>(
    ['/cost/summary', query],
    () =>
      fetchWithFallback<CostSummary, CostSummaryView>(
        '/cost/summary',
        query,
        getMockCostSummary,
        toCostSummaryView,
      ),
    swrConfig,
  );
}

export function useCostTrend() {
  return useSWR<ApiEnvelope<CostTrendView>>(
    '/cost/trend',
    () =>
      fetchWithFallback<CostTrend, CostTrendView>(
        '/cost/trend',
        {},
        getMockCostTrend,
        toCostTrendView,
      ),
    swrConfig,
  );
}

export function useBudgetStatus() {
  return useSWR<ApiEnvelope<BudgetStatusView>>(
    '/budgets/status',
    () =>
      fetchWithFallback<BudgetStatusList, BudgetStatusView>(
        '/budgets/status',
        {},
        getMockBudgetStatus,
        toBudgetStatusView,
      ),
    swrConfig,
  );
}

export function useForecast() {
  return useSWR<ApiEnvelope<ForecastView>>(
    '/forecasts',
    () =>
      fetchWithFallback<ForecastList, ForecastView>(
        '/forecasts',
        {},
        getMockForecast,
        toForecastView,
      ),
    swrConfig,
  );
}

export function useAnomalies() {
  return useSWR<ApiEnvelope<AnomalyView[]>>(
    '/anomalies',
    () =>
      fetchWithFallback<AnomalyList, AnomalyView[]>('/anomalies', {}, getMockAnomalies, (wire) =>
        wire.items.map(toAnomalyView),
      ),
    swrConfig,
  );
}

export function useOptimizationRecommendations() {
  return useSWR<ApiEnvelope<OptimizationRecommendationView[]>>(
    '/optimization/recommendations',
    () =>
      fetchWithFallback<OptimizationRecommendationList, OptimizationRecommendationView[]>(
        '/optimization/recommendations',
        {},
        getMockOptimizationRecommendations,
        toOptimizationViews,
      ),
    swrConfig,
  );
}

export function useWorkloads() {
  return useSWR<ApiEnvelope<WorkloadView[]>>(
    '/workloads',
    () =>
      fetchWithFallback<WorkloadList, WorkloadView[]>(
        '/workloads',
        {},
        getMockWorkloads,
        (wire) => toWorkloadViews(wire),
      ),
    swrConfig,
  );
}
