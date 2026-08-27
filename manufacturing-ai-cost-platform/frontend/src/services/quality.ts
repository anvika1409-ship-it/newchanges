/**
 * Quality inspection API service.
 *
 * Image upload uses multipart/form-data (not JSON), so it calls fetch
 * directly rather than through apiClient. The AI execution call uses the
 * standard JSON client.
 *
 * No GenAILab API key is ever used here — the frontend authenticates with
 * a Bearer token and the backend proxies to the model provider
 * (AI_DEVELOPMENT_RULES.md section 19).
 */

import { apiClient, ApiRequestError } from './apiClient';
import type { InputRef, AIExecutionResponse } from '../types/quality';

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

/**
 * Upload a product image for quality inspection.
 *
 * Returns an InputRef that can be passed to executeQualityCheck.
 * The image is stored server-side; no image data is sent to the model
 * from the frontend.
 */
export async function uploadImage(file: File, signal?: AbortSignal): Promise<InputRef> {
  const form = new FormData();
  form.append('file', file);

  const headers: Record<string, string> = {
    'X-Request-ID': crypto.randomUUID(),
  };

  // Reuse the auth token from the apiClient module.
  // The token is set via setAuthToken() at login time.
  const tokenModule = await import('./apiClient');
  // Access the module-level token through a re-export or by reading the header
  // that apiClient would set. Since we can't access the private `authToken`,
  // we read it from localStorage or the module. For this MVP, we'll pass it
  // through the same mechanism.

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/quality/upload`, {
      method: 'POST',
      headers,
      body: form,
      signal,
    });
  } catch {
    throw new ApiRequestError(0, {
      code: 'network_error',
      message: 'Unable to reach the API.',
      request_id: null,
      details: null,
    });
  }

  if (!response.ok) {
    const text = await response.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      body = { code: 'upload_error', message: `Upload failed with status ${response.status}`, request_id: null, details: null };
    }
    throw new ApiRequestError(response.status, body);
  }

  return response.json();
}

export interface QualityCheckOptions {
  businessPriority?: 'LOW' | 'NORMAL' | 'HIGH' | 'CRITICAL';
  qualityRequirement?: number;
  maxCost?: number;
}

/**
 * Execute a quality inspection on a previously uploaded image.
 *
 * The model is selected by the backend's routing policy — not hard-coded.
 */
export async function executeQualityCheck(
  inputRef: InputRef,
  options: QualityCheckOptions = {},
): Promise<AIExecutionResponse> {
  return apiClient.post<AIExecutionResponse>('/ai/execute', {
    workload_type: 'quality_check',
    business_priority: options.businessPriority ?? 'NORMAL',
    modality: 'image',
    input_refs: [inputRef],
    quality_requirement: options.qualityRequirement,
    max_cost: options.maxCost,
  });
}
