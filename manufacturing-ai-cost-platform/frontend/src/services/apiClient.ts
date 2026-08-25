/**
 * Typed API client.
 *
 * Rules this file exists to enforce (AI_DEVELOPMENT_RULES.md section 19):
 *  - no secrets in the frontend; the browser never holds the GenAILab key
 *  - no direct provider or database calls; the backend is the only origin
 *  - every failure surfaces as a typed ApiError, never a raw exception
 *
 * The bearer token is supplied by the host application at runtime. It is held
 * in memory only and is never written to localStorage, where any script on the
 * page could read it.
 */

import type { ApiError } from '../types/api';

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

const REQUEST_ID_HEADER = 'X-Request-ID';

let authToken: string | null = null;

/** Set or clear the bearer token used for protected endpoints. */
export function setAuthToken(token: string | null): void {
  authToken = token;
}

/** Error carrying the backend's contract-shaped body. */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, body: ApiError) {
    super(body.message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id ?? null;
    this.details = body.details ?? null;
  }
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export interface RequestOptions {
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined>;
}

async function request<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
  body?: unknown,
): Promise<T> {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  const headers: Record<string, string> = {
    Accept: 'application/json',
    // Correlates this call with the backend log line for the same request.
    [REQUEST_ID_HEADER]: newRequestId(),
  };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method,
      headers,
      signal: options.signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    // Network-level failure: no HTTP response exists, so synthesise the shape
    // callers already handle rather than leaking a DOMException.
    throw new ApiRequestError(0, {
      code: 'network_error',
      message: 'Unable to reach the API.',
      request_id: null,
      details: null,
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const parsed: unknown = text ? safeJsonParse(text) : null;

  if (!response.ok) {
    const fallback: ApiError = {
      code: 'http_error',
      message: `Request failed with status ${response.status}`,
      request_id: response.headers.get(REQUEST_ID_HEADER),
      details: null,
    };
    throw new ApiRequestError(response.status, isApiError(parsed) ? parsed : fallback);
  }

  return parsed as T;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    'message' in value
  );
}

export const apiClient = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, options, body),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, options, body),
};
