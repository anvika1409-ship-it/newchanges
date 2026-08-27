/**
 * What-if Simulator.
 *
 * Implements the What-if Simulation Workflow (AI_WORKFLOWS.md section 10): vary
 * volume, budget and model-mix assumptions and compare current, forecast and
 * optimized cost.
 *
 * Every output is labelled with the provenance the API returned. Nothing here
 * is realized spend, and running a simulation changes nothing — it creates no
 * policy, approves nothing and invokes no model.
 *
 * A figure the backend could not compute renders as "Not available", never as
 * zero. An unpriced model showing "$0" would read as free and manufacture a
 * saving that does not exist.
 */

import { useCallback, useEffect, useState } from 'react';
import { ApiRequestError } from '../services/apiClient';
import { listModels, runSimulation } from '../services/optimization';
import type {
  ModelMixEntry,
  Provenance,
  RegisteredModel,
  RiskLevel,
  SimulationFigure,
  SimulationResult,
} from '../types/optimization';

const PROVENANCE_STYLE: Record<Provenance, string> = {
  ACTUAL: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  ESTIMATED: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  FORECAST: 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300',
  SIMULATED: 'bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300',
  UNAVAILABLE: 'bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
};

const RISK_STYLE: Record<RiskLevel, string> = {
  LOW: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  MEDIUM: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  HIGH: 'bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300',
  CRITICAL: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
};

interface FormState {
  production_volume: string;
  image_volume: string;
  request_volume: string;
  budget_amount: string;
  horizon_days: string;
}

const INITIAL_FORM: FormState = {
  production_volume: '',
  image_volume: '',
  request_volume: '10000',
  budget_amount: '',
  horizon_days: '30',
};

/** Parse an optional numeric field. Blank means "not supplied", not zero. */
function optionalNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === '') return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

export function WhatIfSimulator() {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [mix, setMix] = useState<Record<string, string>>({});
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );

  useEffect(() => {
    const controller = new AbortController();
    listModels(controller.signal)
      .then((page) => setModels(page.items))
      .catch(() => {
        // A registry that cannot be listed leaves the mix empty; the simulation
        // still runs and reports that no optimized cost could be computed.
        if (!controller.signal.aborted) setModels([]);
      });
    return () => controller.abort();
  }, []);

  const setField = (key: keyof FormState) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((previous) => ({ ...previous, [key]: event.target.value }));

  const mixEntries: ModelMixEntry[] = Object.entries(mix)
    .map(([model_id, share]) => ({ model_id, share_percent: Number(share) }))
    .filter((entry) => Number.isFinite(entry.share_percent) && entry.share_percent > 0);

  const mixTotal = mixEntries.reduce((sum, entry) => sum + entry.share_percent, 0);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setRunning(true);
      setError(null);
      try {
        const simulation = await runSimulation({
          request_volume: Number(form.request_volume) || 0,
          production_volume: optionalNumber(form.production_volume),
          image_volume: optionalNumber(form.image_volume),
          budget_amount: optionalNumber(form.budget_amount),
          horizon_days: Number(form.horizon_days) || 30,
          model_mix: mixEntries,
        });
        setResult(simulation);
      } catch (caught) {
        const apiError = caught instanceof ApiRequestError ? caught : null;
        setError({
          message: apiError?.message ?? 'Simulation failed.',
          requestId: apiError?.requestId ?? null,
        });
        setResult(null);
      } finally {
        setRunning(false);
      }
    },
    [form, mixEntries],
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          What-if Simulator
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Compare what your AI spend would look like under different volume,
          budget and routing assumptions.
        </p>
      </header>

      <div
        role="note"
        className="rounded-lg border border-violet-300 bg-violet-50 p-3 text-sm text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200"
      >
        <strong className="font-semibold">Simulation only.</strong> Every figure
        on this page is a projection, labelled with where it came from. Running a
        simulation changes nothing — no policy is created or activated, and no
        model is called. Applying a change requires the normal approval flow in
        the Optimization Center.
      </div>

      <form
        onSubmit={submit}
        className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-800"
      >
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Assumptions
        </h2>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field
            id="request_volume"
            label="Request volume"
            hint="AI requests over the horizon. Drives cost."
            value={form.request_volume}
            onChange={setField('request_volume')}
            required
          />
          <Field
            id="production_volume"
            label="Production volume"
            hint="Units produced. Context only."
            value={form.production_volume}
            onChange={setField('production_volume')}
          />
          <Field
            id="image_volume"
            label="Image volume"
            hint="Images inspected over the horizon."
            value={form.image_volume}
            onChange={setField('image_volume')}
          />
          <Field
            id="budget_amount"
            label="Budget"
            hint="Compared against the simulated cost."
            value={form.budget_amount}
            onChange={setField('budget_amount')}
          />
          <Field
            id="horizon_days"
            label="Horizon (days)"
            hint="Period the volumes cover."
            value={form.horizon_days}
            onChange={setField('horizon_days')}
          />
        </div>

        <fieldset className="mt-6">
          <legend className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Model mix
          </legend>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Share of request volume routed to each model. A model without
            registry pricing cannot be costed — the result will say so rather
            than treat it as free.
          </p>

          {models.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
              No enabled models available.
            </p>
          ) : (
            <div className="mt-3 space-y-2">
              {models.map((model) => (
                <div
                  key={model.id}
                  className="flex flex-col gap-2 rounded-md border border-slate-200 p-2 sm:flex-row sm:items-center sm:gap-3 dark:border-slate-700"
                >
                  <label
                    htmlFor={`mix-${model.id}`}
                    className="flex-1 truncate font-mono text-xs text-slate-700 dark:text-slate-300"
                  >
                    {model.model_name}
                    {!model.has_known_pricing && (
                      <span
                        className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-sans font-medium uppercase text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                        title="No registry pricing; cost cannot be computed for this model"
                      >
                        unpriced
                      </span>
                    )}
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      id={`mix-${model.id}`}
                      type="number"
                      min={0}
                      max={100}
                      inputMode="numeric"
                      placeholder="0"
                      value={mix[model.id] ?? ''}
                      onChange={(event) =>
                        setMix((previous) => ({ ...previous, [model.id]: event.target.value }))
                      }
                      className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm sm:w-24 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
                    />
                    <span className="text-xs text-slate-500">%</span>
                  </div>
                </div>
              ))}
              <p
                className={
                  mixTotal > 0 && Math.abs(mixTotal - 100) > 0.01
                    ? 'text-xs text-amber-700 dark:text-amber-400'
                    : 'text-xs text-slate-500 dark:text-slate-400'
                }
              >
                Mix total: {mixTotal.toFixed(0)}%
                {mixTotal > 0 && Math.abs(mixTotal - 100) > 0.01 && ' — should total 100%'}
              </p>
            </div>
          )}
        </fieldset>

        <button
          type="submit"
          disabled={running}
          className="mt-6 rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {running ? 'Simulating…' : 'Run simulation'}
        </button>
      </form>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-5 dark:border-red-800 dark:bg-red-950"
        >
          <p className="font-medium text-red-900 dark:text-red-200">Simulation failed</p>
          <p className="mt-1 text-sm text-red-800 dark:text-red-300">{error.message}</p>
          {error.requestId && (
            <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">
              request_id: {error.requestId}
            </p>
          )}
        </div>
      )}

      {result && <SimulationOutput result={result} models={models} />}
    </div>
  );
}

function SimulationOutput({
  result,
  models,
}: {
  result: SimulationResult;
  models: RegisteredModel[];
}) {
  const nameFor = (id: string) => models.find((m) => m.id === id)?.model_name ?? id;

  return (
    <section
      className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-800"
      aria-label="Simulation results"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Results over {result.horizon_days} days
        </h2>
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold uppercase ${PROVENANCE_STYLE.SIMULATED}`}
        >
          Simulated
        </span>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <FigureCard label="Current cost" figure={result.current_cost} />
        <FigureCard label="Forecast cost" figure={result.forecast_cost} />
        <FigureCard label="Optimized cost" figure={result.optimized_cost} />
        <FigureCard
          label="Estimated saving"
          figure={result.estimated_saving}
          percent={result.estimated_saving_percent}
        />
      </div>

      <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Quality impact</p>
          <p className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-slate-100">
            {result.quality_impact_percent === null ? (
              <span className="text-slate-400" title="Not measured for every model in the mix">
                Not available
              </span>
            ) : (
              `${result.quality_impact_percent > 0 ? '+' : ''}${result.quality_impact_percent.toFixed(1)}%`
            )}
          </p>
          <p className="text-[10px] uppercase tracking-wide text-slate-400">Simulated</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Risk</p>
          <p className="mt-1">
            <span
              className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${RISK_STYLE[result.risk_level]}`}
            >
              {result.risk_level}
            </span>
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Within budget</p>
          <p className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-slate-100">
            {result.within_budget === null ? (
              <span className="text-slate-400" title="Budget or simulated cost unknown">
                Not available
              </span>
            ) : result.within_budget ? (
              'Yes'
            ) : (
              'No'
            )}
          </p>
        </div>
      </div>

      {result.unpriced_model_ids.length > 0 && (
        <div
          role="note"
          className="mt-5 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          <strong className="font-medium">Cost could not be computed.</strong>{' '}
          {result.unpriced_model_ids.length} model
          {result.unpriced_model_ids.length === 1 ? '' : 's'} in the mix have no
          registry pricing:{' '}
          <span className="font-mono text-xs">
            {result.unpriced_model_ids.map(nameFor).join(', ')}
          </span>
          . An unpriced model is not free, so no optimized cost or saving is shown.
        </div>
      )}

      {result.assumptions.length > 0 && (
        <details className="mt-5">
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-slate-500">
            Assumptions ({result.assumptions.length})
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600 dark:text-slate-400">
            {result.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function FigureCard({
  label,
  figure,
  percent,
}: {
  label: string;
  figure: SimulationFigure;
  percent?: number | null;
}) {
  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900 dark:text-slate-100">
        {figure.amount === null ? (
          <span className="text-base text-slate-400" title="Could not be computed">
            Not available
          </span>
        ) : (
          <>
            {figure.amount.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            {figure.currency && (
              <span className="ml-1 text-sm font-normal text-slate-500">
                {figure.currency}
              </span>
            )}
          </>
        )}
      </p>
      {percent !== undefined && percent !== null && (
        <p className="text-xs text-slate-500">{percent.toFixed(1)}%</p>
      )}
      <span
        className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${PROVENANCE_STYLE[figure.provenance]}`}
      >
        {figure.provenance}
      </span>
    </div>
  );
}

function Field({
  id,
  label,
  hint,
  value,
  onChange,
  required,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  required?: boolean;
}) {
  return (
    <div>
      <label
        htmlFor={id}
        className="block text-xs font-medium text-slate-700 dark:text-slate-300"
      >
        {label}
        {required && <span className="ml-0.5 text-red-600">*</span>}
      </label>
      <input
        id={id}
        type="number"
        min={0}
        inputMode="numeric"
        value={value}
        onChange={onChange}
        required={required}
        aria-describedby={`${id}-hint`}
        className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
      />
      <p id={`${id}-hint`} className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
        {hint}
      </p>
    </div>
  );
}
