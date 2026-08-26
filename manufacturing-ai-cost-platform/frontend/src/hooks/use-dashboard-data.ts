import useSWR from 'swr';
import type {
  Anomaly,
  ApiEnvelope,
  BudgetStatus,
  CostSummary,
  CostTrend,
  Forecast,
  OptimizationRecommendation,
  Workload,
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
import { apiClient, ApiRequestError } from '../services/apiClient';

/**
 * Dashboard data hooks.
 *
 * Per ARCHITECTURE.md section 7, the frontend never talks to GenAILab or the
 * database directly; it only calls the FastAPI backend under `/api/v1`,
 * reached at the same origin (Vite dev proxy locally, nginx reverse proxy in
 * production — see vite.config.ts / nginx.conf). No server-side bridge or
 * secret lives in this bundle (AI_DEVELOPMENT_RULES.md section 19).
 *
 * If the backend is unreachable (e.g. exploring the UI before the backend is
 * deployed), each hook falls back to local fixture data and labels the
 * envelope `source: 'demo'` so the UI can show a "Demo data" indicator
 * instead of silently presenting invented numbers as real
 * (AI_DEVELOPMENT_RULES.md sections 41-42).
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
 * Fetches `path` from the real backend. On any failure (network error,
 * non-2xx, timeout) resolves the supplied fixture instead, tagged as demo
 * data, so the dashboard stays explorable before the backend is wired up.
 */
async function fetchWithFallback<T>(
  path: string,
  query: Record<string, string>,
  mockFactory: () => T,
): Promise<ApiEnvelope<T>> {
  try {
    const data = await apiClient.get<T>(path, { query });
    return { data, source: 'live', fetched_at: new Date().toISOString() };
  } catch (err) {
    // A real backend that is reachable but genuinely errors (e.g. 500) should
    // still surface as an error rather than being masked as demo data.
    if (err instanceof ApiRequestError && err.status !== 0) {
      throw err;
    }
    return { data: mockFactory(), source: 'demo', fetched_at: new Date().toISOString() };
  }
}

export function useCostSummary(filters: FilterParams = {}) {
  const query = toQuery({ plant_id: filters.plantId, department_id: filters.departmentId });
  return useSWR<ApiEnvelope<CostSummary>>(
    ['/cost/summary', query],
    () => fetchWithFallback('/cost/summary', query, getMockCostSummary),
    swrConfig,
  );
}

export function useCostTrend() {
  return useSWR<ApiEnvelope<CostTrend>>(
    '/cost/trend',
    () => fetchWithFallback('/cost/trend', {}, getMockCostTrend),
    swrConfig,
  );
}

export function useBudgetStatus() {
  return useSWR<ApiEnvelope<BudgetStatus>>(
    '/budgets/status',
    () => fetchWithFallback('/budgets/status', {}, getMockBudgetStatus),
    swrConfig,
  );
}

export function useForecast() {
  return useSWR<ApiEnvelope<Forecast>>(
    '/forecasts',
    () => fetchWithFallback('/forecasts', {}, getMockForecast),
    swrConfig,
  );
}

export function useAnomalies() {
  return useSWR<ApiEnvelope<Anomaly[]>>(
    '/anomalies',
    () => fetchWithFallback('/anomalies', {}, getMockAnomalies),
    swrConfig,
  );
}

export function useOptimizationRecommendations() {
  return useSWR<ApiEnvelope<OptimizationRecommendation[]>>(
    '/optimization/recommendations',
    () => fetchWithFallback('/optimization/recommendations', {}, getMockOptimizationRecommendations),
    swrConfig,
  );
}

export function useWorkloads() {
  return useSWR<ApiEnvelope<Workload[]>>(
    '/workloads',
    () => fetchWithFallback('/workloads', {}, getMockWorkloads),
    swrConfig,
  );
}
