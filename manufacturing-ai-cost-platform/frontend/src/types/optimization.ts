/**
 * Optimization and simulation types.
 *
 * Mirrors API_CONTRACT.yaml field for field. The contract is the
 * synchronization point between frontend and backend, and the frontend does not
 * invent shapes (AI_DEVELOPMENT_RULES.md sections 20 and 34).
 *
 * Note these differ from the `OptimizationRecommendation` in `lib/types.ts`,
 * which predates the contract and carries fields the API does not return
 * (`title`, `description`, `workload_label`). New code follows the contract.
 */

/** Provenance of a displayed value. Never inferred — always sent by the API. */
export type Provenance = 'ACTUAL' | 'ESTIMATED' | 'FORECAST' | 'SIMULATED' | 'UNAVAILABLE';

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

/** Lifecycle states from DATABASE_SCHEMA.md section 18. */
export type RecommendationStatus =
  | 'DRAFT'
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'APPLIED'
  | 'ROLLED_BACK';

/** One recommendation, exactly as `OptimizationRecommendation` defines it. */
export interface OptimizationRecommendation {
  id: string;
  workload_id: string | null;
  current_strategy: string | null;
  recommended_strategy: string | null;
  /** Estimated, never realized. Null when it could not be computed. */
  estimated_saving: number | null;
  estimated_saving_percent: number | null;
  quality_impact_percent: number | null;
  latency_impact_percent: number | null;
  risk_level: RiskLevel;
  recommendation_reason: string | null;
  status: RecommendationStatus;
  /** ESTIMATED or SIMULATED — savings are never reported as realized. */
  provenance: 'ESTIMATED' | 'SIMULATED';
  /** The routing policy version this recommendation activated, once applied. */
  applied_policy_id: string | null;
  superseded_policy_id: string | null;
  created_at: string;
  approved_at: string | null;
  applied_at: string | null;
  rolled_back_at: string | null;
  approved_by: string | null;
}

export interface PageInfo {
  total: number;
  limit: number;
  offset: number;
}

export interface OptimizationRecommendationList {
  items: OptimizationRecommendation[];
  page: PageInfo;
}

/** Result of applying a recommendation. Carries the resulting policy version. */
export interface OptimizationApplyResult {
  recommendation_id: string;
  status: 'APPLIED';
  applied_policy_id: string;
  applied_policy_version: number;
  superseded_policy_id: string | null;
  activation_mode: 'CANARY' | 'FULL';
  canary_traffic_percent: number | null;
}

/** Approve or reject. Both go through the same endpoint. */
export interface ApprovalDecision {
  decision: 'APPROVED' | 'REJECTED';
  comments?: string;
}

// ── What-if simulation (AI_WORKFLOWS.md section 10) ────────────────────────

export interface ModelMixEntry {
  model_id: string;
  share_percent: number;
}

export interface SimulationRequest {
  request_volume: number;
  workload_id?: string | null;
  production_volume?: number | null;
  image_volume?: number | null;
  budget_amount?: number | null;
  model_mix?: ModelMixEntry[];
  horizon_days?: number;
}

/**
 * One cost figure with its provenance.
 *
 * `amount` is null when the figure could not be computed. Null is not zero, and
 * the UI must never render the two the same way — an unpriced model showing as
 * "$0" would read as free.
 */
export interface SimulationFigure {
  amount: number | null;
  currency: string | null;
  provenance: Provenance;
}

export interface SimulationResult {
  /** The result as a whole is simulated. */
  provenance: 'SIMULATED';
  horizon_days: number;
  current_cost: SimulationFigure;
  forecast_cost: SimulationFigure;
  optimized_cost: SimulationFigure;
  estimated_saving: SimulationFigure;
  estimated_saving_percent: number | null;
  quality_impact_percent: number | null;
  risk_level: RiskLevel;
  within_budget: boolean | null;
  /** Models in the mix with no registry pricing — reported, not assumed free. */
  unpriced_model_ids: string[];
  assumptions: string[];
}

/** A registry entry, as `/models` returns it. */
export interface RegisteredModel {
  id: string;
  model_name: string;
  provider: string | null;
  capability: string;
  has_known_pricing: boolean;
  quality_score: number | null;
  risk_level: string | null;
  enabled: boolean;
}

export interface ModelList {
  items: RegisteredModel[];
  page: PageInfo;
}
