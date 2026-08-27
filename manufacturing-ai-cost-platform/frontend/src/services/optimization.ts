/**
 * Optimization and simulation API service.
 *
 * Every call goes through `apiClient`, which attaches the bearer token. The
 * frontend holds no credentials of its own and makes no authorization decision:
 * approve, reject and apply are all authorized server-side, and a user who
 * cannot perform one gets a 403 from the backend regardless of what the UI
 * shows (AI_DEVELOPMENT_RULES.md section 19, SECURITY.md section 23).
 *
 * Hiding a button is a courtesy, not a control.
 */

import { apiClient } from './apiClient';
import type {
  ApprovalDecision,
  ModelList,
  OptimizationApplyResult,
  OptimizationRecommendation,
  OptimizationRecommendationList,
  RecommendationStatus,
  SimulationRequest,
  SimulationResult,
} from '../types/optimization';

/** List recommendations, optionally filtered by lifecycle status. */
export function listRecommendations(
  options: { status?: RecommendationStatus; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<OptimizationRecommendationList> {
  return apiClient.get<OptimizationRecommendationList>('/optimization/recommendations', {
    signal,
    query: {
      status: options.status,
      limit: options.limit,
      offset: options.offset,
    },
  });
}

/**
 * Approve or reject a recommendation.
 *
 * Both decisions use the same endpoint; the contract's `ApprovalDecision`
 * carries which one. There is no separate reject route to call.
 */
export function decideRecommendation(
  id: string,
  decision: ApprovalDecision,
  signal?: AbortSignal,
): Promise<OptimizationRecommendation> {
  return apiClient.post<OptimizationRecommendation>(
    `/optimization/${encodeURIComponent(id)}/approve`,
    decision,
    { signal },
  );
}

/**
 * Apply an approved recommendation, creating a new versioned routing policy.
 *
 * Defaults to a canary activation: a routing change that has never carried
 * traffic should not take all of it at once (ARCHITECTURE.md section 10).
 */
export function applyRecommendation(
  id: string,
  body: { activation_mode?: 'CANARY' | 'FULL'; canary_traffic_percent?: number; reason?: string } = {},
  signal?: AbortSignal,
): Promise<OptimizationApplyResult> {
  return apiClient.post<OptimizationApplyResult>(
    `/optimization/${encodeURIComponent(id)}/apply`,
    body,
    { signal },
  );
}

/**
 * Run a what-if simulation.
 *
 * Read-only: creates no policy, approves nothing, invokes no model. Every
 * figure it returns is FORECAST or SIMULATED and must be labelled as such
 * wherever it is displayed (AI_WORKFLOWS.md section 10).
 */
export function runSimulation(
  body: SimulationRequest,
  signal?: AbortSignal,
): Promise<SimulationResult> {
  return apiClient.post<SimulationResult>('/optimization/simulate', body, { signal });
}

/** Enabled models, for building a mix. */
export function listModels(signal?: AbortSignal): Promise<ModelList> {
  return apiClient.get<ModelList>('/models', {
    signal,
    query: { enabled: true, limit: 200 },
  });
}
