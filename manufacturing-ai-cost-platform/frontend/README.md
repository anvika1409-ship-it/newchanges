# Frontend — Merge Notes

This `frontend/` is the result of merging two sources:

- **`frontend.zip`** (base template) — Vite + React + TypeScript scaffold
  built directly against `docs/API_CONTRACT.yaml` and the rules in
  `AI_DEVELOPMENT_RULES.md` / `ARCHITECTURE.md` / `SECURITY.md`: typed
  `apiClient`, router foundation, Docker/nginx deployment, no secrets in the
  bundle.
- **`manufacturing-ai-cost-intelligence.zip`** (v0 dashboard) — the actual
  KPI / cost trend / budget / anomaly / optimization UI, built with v0 on
  React 19, Tailwind 4, shadcn/ui, recharts, framer-motion and SWR.

## What changed in the merge

1. **Kept the base template's deployment & security posture.**
   `Dockerfile`, `nginx.conf`, `.dockerignore`, `.env.example`, and the typed
   `src/services/apiClient.ts` / `src/services/system.ts` are unchanged.
   The frontend is a static SPA that only ever calls `/api/v1/*` on its own
   origin — nginx (prod) or the Vite dev proxy (`vite.config.ts`, dev) is
   what actually reaches the FastAPI backend. No backend credential or
   GenAILab detail ever reaches the browser (`ARCHITECTURE.md` §7,
   `SECURITY.md` §6).

2. **Removed the v0 project's Node-only backend bridge.**
   The original `lib/server/backend.ts` + Vite `dashboardApiPlugin` assumed
   a Next.js-style server runtime reading `BACKEND_API_BASE_URL` /
   `DASHBOARD_API_TOKEN` from `process.env` at request time. That pattern
   doesn't exist in a `vite build` + nginx static deployment, so it was
   dropped in favor of the base template's browser-fetch-through-proxy
   pattern (see `src/hooks/use-dashboard-data.ts`).

3. **Kept the "demo data" UX, done client-side.**
   If a call to the real backend fails (network error / unreachable — not a
   genuine 4xx/5xx from a live backend), `use-dashboard-data.ts` falls back
   to the fixtures in `src/lib/mock-data.ts` and tags the envelope
   `source: 'demo'`. The dashboard header already surfaces this via the
   "Demo data" badge, so nothing invented is ever presented as a real
   figure (`AI_DEVELOPMENT_RULES.md` §41–42).

4. **Ported all dashboard UI as-is.**
   Everything under `src/components/dashboard/`, `src/components/ui/`,
   `src/lib/{format,types,utils,mock-data}.ts` is unchanged from the v0
   project — only its file location moved under `src/`, which the `@/*` →
   `./src/*` alias in `tsconfig.json` / `vite.config.ts` already accounts
   for.

5. **Adopted the v0 project's newer toolchain.**
   `package.json` uses React 19 / Vite 7 / Tailwind 4 (required by the
   ported UI) rather than the base template's React 18 / Tailwind 3.
   `react-router-dom` was kept from the base template for the routing
   foundation described below.

6. **Routing.**
   - `/` → redirects to `/dashboard`
   - `/dashboard` → the full cost-intelligence dashboard (`DashboardShell`)
   - `/status` → a minimal `/health` + `/ready` connectivity check, with no
     business figures (kept from the base template's philosophy: never show
     fabricated numbers, even as a mockup)
   - `*` → 404

## Verified locally

```
npm install
npm run build      # tsc -b && vite build — passes clean
npm run dev         # Vite dev server on :5173, proxies /api → :8000
```

`vite preview` was used to smoke-test the production build; both `/` and
`/dashboard` serve `200` with the SPA fallback working as expected.

## Next steps for whoever picks this up

- Wire real authentication so `apiClient.setAuthToken(...)` is called with a
  real bearer token once auth exists (currently `null`, so protected
  endpoints will 401 against a real backend — the dashboard will fall back
  to demo data in that case, which is expected until auth is wired).
- `src/lib/types.ts` marks a few fields (`today_cost`,
  `month_to_date_cost` on `CostSummary`, and the full shape of
  `BudgetStatus` / `Forecast` / `Anomaly` / `OptimizationRecommendation` /
  `Workload`) as *inferred* — `API_CONTRACT.yaml` documents the paths and
  params for these endpoints but not their full response schemas yet.
  Reconcile with the backend's actual response shape once available, per
  `AI_DEVELOPMENT_RULES.md` §3 (source-of-truth hierarchy).
