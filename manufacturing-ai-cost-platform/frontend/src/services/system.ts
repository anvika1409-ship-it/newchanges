/**
 * System endpoints.
 *
 * Only the two operations the contract currently defines. Business services are
 * added as their endpoints are implemented.
 */

import { apiClient } from './apiClient';
import type { HealthResponse, ReadinessResponse } from '../types/api';

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiClient.get<HealthResponse>('/health', { signal });
}

export function getReadiness(signal?: AbortSignal): Promise<ReadinessResponse> {
  return apiClient.get<ReadinessResponse>('/ready', { signal });
}
