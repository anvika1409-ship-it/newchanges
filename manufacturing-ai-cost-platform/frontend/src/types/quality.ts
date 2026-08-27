/**
 * Types for the Quality Control vertical slice.
 *
 * Matches the backend API contract (API_CONTRACT.yaml) and never invents
 * fields not present in the source-of-truth documents.
 */

/** Reference to an uploaded image stored server-side. */
export interface InputRef {
  ref: string;
  content_type: string;
  size_bytes: number | null;
  classification?: string | null;
}

/** Structured quality inspection result parsed from the vision model response. */
export interface QualityResult {
  verdict: 'PASS' | 'FAIL' | 'INCONCLUSIVE';
  defect_type: string | null;
  confidence: number | null;
  raw_response: string;
}

/** Cost with explicit provenance — never fabricated. */
export interface CostInfo {
  amount: number | null;
  currency: string | null;
  provenance: 'ACTUAL' | 'ESTIMATED' | 'UNAVAILABLE';
}

/** Token usage reported by the gateway. */
export interface UsageInfo {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
}

/** The routing decision made before execution. */
export interface ExecutionPlan {
  workload_type: string;
  complexity: 'SIMPLE' | 'MEDIUM' | 'COMPLEX';
  selected_model_id: string | null;
  selected_agent_id: string | null;
  estimated_cost: number | null;
  max_context_tokens: number | null;
  max_tool_calls: number | null;
  routing_policy_version: number | null;
  budget_status: 'ALLOW' | 'DOWNGRADE' | 'REQUIRE_APPROVAL' | 'BLOCK';
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
}

/** Full response from POST /ai/execute for a quality_check workload. */
export interface AIExecutionResponse {
  request_id: string;
  trace_id: string | null;
  execution_plan: ExecutionPlan;
  result: QualityResult & { content: string; finish_reason: string | null };
  usage: UsageInfo;
  cost: CostInfo;
  quality_score: number | null;
}
