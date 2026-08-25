/**
 * Placeholder dashboard.
 *
 * Deliberately contains no cost, budget or optimization figures. Showing
 * invented numbers — even as a mockup — would violate the rule against
 * presenting fabricated business outcomes (AI_DEVELOPMENT_RULES.md sections 41
 * and 42). It proves the frontend/backend wiring and nothing more.
 */

import { useEffect, useState } from 'react';
import { ApiRequestError } from '../services/apiClient';
import { getHealth, getReadiness } from '../services/system';
import type { ReadinessResponse } from '../types/api';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; requestId: string | null }
  | { kind: 'ready'; alive: boolean; readiness: ReadinessResponse };

export function DashboardPlaceholder() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const [health, readiness] = await Promise.all([
          getHealth(controller.signal),
          getReadiness(controller.signal),
        ]);
        setState({ kind: 'ready', alive: health.status === 'alive', readiness });
      } catch (error) {
        if (controller.signal.aborted) return;
        const apiError = error instanceof ApiRequestError ? error : null;
        setState({
          kind: 'error',
          message: apiError?.message ?? 'Unexpected error.',
          requestId: apiError?.requestId ?? null,
        });
      }
    }

    void load();
    return () => controller.abort();
  }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          Platform status
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Scaffold only. Cost, budget and optimization views are added as their
          endpoints are implemented.
        </p>
      </header>

      {state.kind === 'loading' && (
        <div
          role="status"
          className="rounded-lg border border-slate-200 bg-white p-6 dark:border-slate-700 dark:bg-slate-800"
        >
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Checking backend&hellip;
          </p>
        </div>
      )}

      {state.kind === 'error' && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-6 dark:border-red-800 dark:bg-red-950"
        >
          <p className="font-medium text-red-900 dark:text-red-200">
            Could not reach the API
          </p>
          <p className="mt-1 text-sm text-red-800 dark:text-red-300">{state.message}</p>
          {state.requestId && (
            <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">
              request_id: {state.requestId}
            </p>
          )}
        </div>
      )}

      {state.kind === 'ready' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <StatusCard label="Liveness" ok={state.alive} detail="/health" />
          {Object.entries(state.readiness.checks).map(([name, ok]) => (
            <StatusCard key={name} label={name} ok={ok} detail="/ready" />
          ))}
        </div>
      )}
    </div>
  );
}

function StatusCard({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium capitalize text-slate-900 dark:text-slate-100">
          {label}
        </span>
        <span
          className={
            ok
              ? 'rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200'
              : 'rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800 dark:bg-red-900 dark:text-red-200'
          }
        >
          {ok ? 'OK' : 'FAILING'}
        </span>
      </div>
      <p className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">{detail}</p>
    </div>
  );
}
