/**
 * Platform status page.
 *
 * Deliberately contains no cost, budget or optimization figures — those
 * belong on the dashboard (`src/pages/Dashboard.tsx`) where every number is
 * either a real backend response or clearly labelled demo data. This page
 * only proves connectivity to the FastAPI backend's /health and /ready
 * endpoints (AI_DEVELOPMENT_RULES.md sections 41-42).
 */

import { useEffect, useState } from 'react';
import { ApiRequestError } from '../services/apiClient';
import { getHealth, getReadiness } from '../services/system';
import type { ReadinessResponse } from '../types/api';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; requestId: string | null }
  | { kind: 'ready'; alive: boolean; readiness: ReadinessResponse };

export function StatusPage() {
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
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="font-mono text-lg font-semibold tracking-tight text-foreground sm:text-xl">
          Platform status
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Backend connectivity check. Cost, budget and optimization views live on the dashboard.
        </p>
      </header>

      {state.kind === 'loading' && (
        <div role="status" className="rounded-lg border border-border bg-card p-6">
          <p className="text-sm text-muted-foreground">Checking backend&hellip;</p>
        </div>
      )}

      {state.kind === 'error' && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-6">
          <p className="font-medium text-destructive">Could not reach the API</p>
          <p className="mt-1 text-sm text-destructive/80">{state.message}</p>
          {state.requestId && (
            <p className="mt-2 font-mono text-xs text-destructive/70">
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

function StatusCard({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium capitalize text-foreground">{label}</span>
        <span
          className={
            ok
              ? 'rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-medium text-emerald-400'
              : 'rounded-full bg-destructive/15 px-2 py-0.5 text-xs font-medium text-destructive'
          }
        >
          {ok ? 'OK' : 'FAILING'}
        </span>
      </div>
      <p className="mt-1 font-mono text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}
