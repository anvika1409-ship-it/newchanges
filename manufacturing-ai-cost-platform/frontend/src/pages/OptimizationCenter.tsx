/**
 * Optimization Center.
 *
 * Lists recommendations and offers the lifecycle actions: approve, reject and
 * apply. Every saving shown is ESTIMATED or SIMULATED and is labelled as such —
 * none of it is realized spend (AI_DEVELOPMENT_RULES.md sections 41 and 42).
 *
 * The action buttons reflect what the lifecycle permits, but they are not the
 * control: authorization is enforced server-side and a 403 is surfaced to the
 * user rather than hidden. Disabling a button is a courtesy to avoid a pointless
 * round trip, never a security boundary (SECURITY.md section 23).
 */

import { useCallback, useEffect, useState } from 'react';
import { ApiRequestError } from '../services/apiClient';
import {
  applyRecommendation,
  decideRecommendation,
  listRecommendations,
} from '../services/optimization';
import type {
  OptimizationRecommendation,
  RecommendationStatus,
  RiskLevel,
} from '../types/optimization';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; requestId: string | null }
  | { kind: 'ready'; items: OptimizationRecommendation[] };

const STATUS_STYLE: Record<RecommendationStatus, string> = {
  DRAFT: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  PENDING_APPROVAL: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  APPROVED: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300',
  REJECTED: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-400',
  APPLIED: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  ROLLED_BACK: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
};

const RISK_STYLE: Record<RiskLevel, string> = {
  LOW: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  MEDIUM: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  HIGH: 'bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300',
  CRITICAL: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
};

/** A value that is genuinely unknown renders as "—", never as 0. */
function Unknown() {
  return (
    <span className="text-slate-400" title="Not available">
      —
    </span>
  );
}

function Money({ value }: { value: number | null }) {
  if (value === null) return <Unknown />;
  return <>{value.toLocaleString(undefined, { maximumFractionDigits: 2 })}</>;
}

function Percent({ value, signed = false }: { value: number | null; signed?: boolean }) {
  if (value === null) return <Unknown />;
  const sign = signed && value > 0 ? '+' : '';
  return (
    <>
      {sign}
      {value.toFixed(1)}%
    </>
  );
}

function Chip({ label, className }: { label: string; className: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${className}`}
    >
      {label}
    </span>
  );
}

export function OptimizationCenter() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const page = await listRecommendations({ limit: 50 }, signal);
      setState({ kind: 'ready', items: page.items });
    } catch (error) {
      if (signal?.aborted) return;
      const apiError = error instanceof ApiRequestError ? error : null;
      setState({
        kind: 'error',
        message: apiError?.message ?? 'Unexpected error.',
        requestId: apiError?.requestId ?? null,
      });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  /**
   * Run a lifecycle action.
   *
   * A refusal from the backend is shown verbatim rather than swallowed: if the
   * caller lacks the permission, they need to know that, not see the row
   * silently fail to change.
   */
  const act = useCallback(
    async (id: string, action: () => Promise<unknown>, successText: string) => {
      setBusyId(id);
      setNotice(null);
      try {
        await action();
        setNotice({ tone: 'ok', text: successText });
        await load();
      } catch (error) {
        const apiError = error instanceof ApiRequestError ? error : null;
        const suffix = apiError?.requestId ? ` (request ${apiError.requestId})` : '';
        setNotice({
          tone: 'error',
          text: `${apiError?.message ?? 'Action failed.'}${suffix}`,
        });
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          Optimization Center
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Recommendations awaiting review. Every figure below is an estimate of
          what a change would save, not money already saved.
        </p>
      </header>

      <div
        role="note"
        className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-400 md:p-4"
      >
        <strong className="font-medium text-slate-800 dark:text-slate-200">
          Reading these figures:
        </strong>{' '}
        savings, quality and latency impacts are <em>projections</em>. Applying a
        recommendation creates a new versioned routing policy and can be rolled
        back.
      </div>

      {notice && (
        <div
          role="status"
          className={
            notice.tone === 'ok'
              ? 'rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200'
              : 'rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200'
          }
        >
          {notice.text}
        </div>
      )}

      {state.kind === 'loading' && (
        <div role="status" className="rounded-lg border border-slate-200 p-6 dark:border-slate-700">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Loading recommendations&hellip;
          </p>
        </div>
      )}

      {state.kind === 'error' && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-6 dark:border-red-800 dark:bg-red-950"
        >
          <p className="font-medium text-red-900 dark:text-red-200">
            Could not load recommendations
          </p>
          <p className="mt-1 text-sm text-red-800 dark:text-red-300">{state.message}</p>
          {state.requestId && (
            <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">
              request_id: {state.requestId}
            </p>
          )}
        </div>
      )}

      {state.kind === 'ready' && state.items.length === 0 && (
        <div className="rounded-lg border border-slate-200 p-8 text-center dark:border-slate-700">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            No optimization recommendations yet. Run an analysis to generate one.
          </p>
        </div>
      )}

      {state.kind === 'ready' && (
        <div className="space-y-4">
          {state.items.map((item) => (
            <RecommendationCard
              key={item.id}
              item={item}
              busy={busyId === item.id}
              onApprove={() =>
                act(
                  item.id,
                  () => decideRecommendation(item.id, { decision: 'APPROVED' }),
                  'Recommendation approved.',
                )
              }
              onReject={() =>
                act(
                  item.id,
                  () => decideRecommendation(item.id, { decision: 'REJECTED' }),
                  'Recommendation rejected.',
                )
              }
              onApply={() =>
                act(
                  item.id,
                  () => applyRecommendation(item.id, { activation_mode: 'CANARY' }),
                  'Policy activated as a canary.',
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function RecommendationCard({
  item,
  busy,
  onApprove,
  onReject,
  onApply,
}: {
  item: OptimizationRecommendation;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  onApply: () => void;
}) {
  // Lifecycle gating only. The backend decides who may do these things.
  const canDecide = item.status === 'PENDING_APPROVAL' || item.status === 'DRAFT';
  const canApply = item.status === 'APPROVED';

  return (
    <article className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-800">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="break-all font-mono text-sm text-slate-500 dark:text-slate-400 sm:break-normal">
            {item.id}
          </h2>
          {item.workload_id && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              workload: {item.workload_id}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Chip label={item.provenance} className={STATUS_STYLE.DRAFT} />
          <Chip label={item.risk_level} className={RISK_STYLE[item.risk_level]} />
          <Chip
            label={item.status.replace(/_/g, ' ')}
            className={STATUS_STYLE[item.status]}
          />
        </div>
      </div>

      <dl className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-500">Current strategy</dt>
          <dd className="mt-0.5 text-sm text-slate-900 dark:text-slate-100">
            {item.current_strategy ?? <Unknown />}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-500">
            Recommended strategy
          </dt>
          <dd className="mt-0.5 text-sm text-slate-900 dark:text-slate-100">
            {item.recommended_strategy ?? <Unknown />}
          </dd>
        </div>
      </dl>

      <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Est. saving" sub={item.provenance}>
          <Money value={item.estimated_saving} />
        </Metric>
        <Metric label="Est. saving %" sub={item.provenance}>
          <Percent value={item.estimated_saving_percent} />
        </Metric>
        <Metric label="Quality impact" sub="Projected">
          <Percent value={item.quality_impact_percent} signed />
        </Metric>
        <Metric label="Latency impact" sub="Projected">
          <Percent value={item.latency_impact_percent} signed />
        </Metric>
      </dl>

      {item.recommendation_reason && (
        <div className="mt-4">
          <h3 className="text-xs uppercase tracking-wide text-slate-500">Reason</h3>
          <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">
            {item.recommendation_reason}
          </p>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
        <span>
          Policy version:{' '}
          {item.applied_policy_id ? (
            <span className="font-mono">{item.applied_policy_id}</span>
          ) : (
            <span title="No policy activated yet">not yet applied</span>
          )}
        </span>
        {item.approved_by && <span>Approved by: {item.approved_by}</span>}
      </div>

      <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-200 pt-4 dark:border-slate-700">
        <button
          type="button"
          onClick={onApprove}
          disabled={busy || !canDecide}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy || !canDecide}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          Reject
        </button>
        <button
          type="button"
          onClick={onApply}
          disabled={busy || !canApply}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40"
          title="Applies as a canary — a routing change that has never carried traffic should not take all of it"
        >
          Apply (canary)
        </button>
        {busy && <span className="self-center text-xs text-slate-500">Working&hellip;</span>}
      </div>
    </article>
  );
}

function Metric({
  label,
  sub,
  children,
}: {
  label: string;
  sub: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-slate-100">
        {children}
      </dd>
      <dd className="text-[10px] uppercase tracking-wide text-slate-400">{sub}</dd>
    </div>
  );
}
