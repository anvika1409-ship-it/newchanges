/**
 * Types mirroring docs/API_CONTRACT.yaml.
 *
 * The contract is the synchronization point between frontend and backend
 * (AI_DEVELOPMENT_RULES.md section 34). Endpoints and response shapes are never
 * invented here; when the contract changes, these types follow it.
 */

/** The `Error` schema returned by every failing endpoint. */
export interface ApiError {
  code: string;
  message: string;
  request_id?: string | null;
  details?: Record<string, unknown> | null;
}

/** GET /health */
export interface HealthResponse {
  status: 'alive';
}

/** GET /ready */
export interface ReadinessResponse {
  status: 'ready' | 'not_ready';
  checks: Record<string, boolean>;
}

/** Pagination envelope shared by collection responses. */
export interface PageInfo {
  total: number;
  limit: number;
  offset: number;
}

/**
 * Cost provenance.
 *
 * Actual, estimated, forecast and simulated values must be labelled distinctly
 * in the UI and never blended into one figure
 * (AI_DEVELOPMENT_RULES.md sections 41 and 42).
 */
export type CostProvenance = 'ACTUAL' | 'ESTIMATED' | 'UNAVAILABLE';
export type ProjectionKind = 'FORECAST' | 'SIMULATED';
