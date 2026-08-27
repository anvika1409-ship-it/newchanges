# Manufacturing AI Cost Intelligence & Autonomous Optimization Platform

A cost-aware AI runtime, governance and optimization layer for manufacturing AI
workloads. The platform decides before expensive model execution occurs,
measures the actual outcome, and learns from history to improve future routing.

> **Status:** feature-complete for the demonstration scope. 22 of the 30 paths
> in `API_CONTRACT.yaml` are implemented and covered by 837 passing tests. See
> [Scope](#scope) for what is deliberately absent, and
> [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) before treating
> any of it as production-ready.

**Evaluating this submission?** Start with
[`docs/SUBMISSION.md`](docs/SUBMISSION.md), then run the demo from
[`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md).

---

## Source of truth

`docs/` holds the architectural source-of-truth documents. When they conflict
with code, the documents win until the architecture is intentionally changed and
the affected document is updated.

| Document | Authoritative for |
|---|---|
| `docs/ARCHITECTURE.md` | System structure, runtime flow, component boundaries |
| `docs/SECURITY.md` | Trust boundaries, guardrails, approvals, secrets |
| `docs/API_CONTRACT.yaml` | API surface: endpoints, request/response shapes, status codes |
| `docs/DATABASE_SCHEMA.md` | Persisted field names, types and storage semantics |
| `docs/AI_WORKFLOWS.md` | Which technology handles each stage |
| `docs/AI_DEVELOPMENT_RULES.md` | Mandatory development rulebook |

Read `AI_DEVELOPMENT_RULES.md` before contributing. It is not optional reading.

These documents were produced *by* the implementation and describe what was
built, rather than what should be:

| Document | Purpose |
|---|---|
| [`docs/SUBMISSION.md`](docs/SUBMISSION.md) | Start here. What was built, how to evaluate it, what is claimed and what is not |
| [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) | Setup, reset, exact API calls and expected output, GenAILab fallback, five-minute judge story |
| [`docs/DEMO_VIDEO_SCRIPT.md`](docs/DEMO_VIDEO_SCRIPT.md) | Shot-by-shot recording script with narration and timings |
| [`docs/INTEGRATION_TEST_REPORT.md`](docs/INTEGRATION_TEST_REPORT.md) | End-to-end validation results and the defects it surfaced |
| [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) | Completed controls, known limitations, hackathon compromises, next steps |

---

## Layout

```text
manufacturing-ai-cost-platform/
├── backend/            FastAPI application (Python 3.12+)
│   ├── app/
│   │   ├── api/            API contract surface
│   │   ├── core/           config, logging, errors, middleware
│   │   ├── db/             engine, session, declarative base
│   │   ├── cache/          Redis abstraction
│   │   ├── integrations/   external services, incl. GenAILab
│   │   ├── security/       authn, JWT, RBAC, tenant/plant/dept scope
│   │   ├── repositories/   persistence abstraction
│   │   ├── telemetry/      cost/quality/trace collection
│   │   ├── orchestrator/   runtime routing
│   │   ├── policies/       deterministic policy logic
│   │   ├── guardrails/     AI/security guardrails
│   │   ├── intelligence/   ML and analytics
│   │   ├── optimization/   optimization lifecycle
│   │   ├── workloads/      manufacturing demo workloads
│   │   └── services/       business services
│   ├── alembic/        migrations
│   └── tests/
├── frontend/           React + TypeScript + Tailwind (Vite)
├── docs/               source-of-truth documents
└── docker-compose.yml  development stack
```

Module boundaries follow `AI_DEVELOPMENT_RULES.md` section 30. A feature belongs
in its module, not appended to whichever file is already open.

---

## Quick start

### 1. Environment files

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Both `.env` files are gitignored. Never commit real credentials.

### 2. Docker

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Backend health | http://localhost:8000/api/v1/health |
| Backend readiness | http://localhost:8000/api/v1/ready |
| Frontend | http://localhost:8080 |

### 3. Local (without Docker)

Backend:

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
```

Set `JWT_SECRET` in `backend/.env` to a random value of at least 32 bytes — the
application refuses to start without one. Then seed the demo dataset and run:

```bash
cd backend && .venv/Scripts/python -m app.db.seed.demo_cli reset
```

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:create_app --factory --reload
```

On Linux or macOS the interpreter is at `.venv/bin/` instead of
`.venv/Scripts/`.

The UI needs a bearer token to show live data; mint one with
`.venv/Scripts/python -m app.security.dev_token --role ADMIN` and paste it into
the field in the application header. Without it every page falls back to local
fixtures, labelled as such. Full detail in
[`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md).

Frontend:

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8000`, so the browser
sees a single origin.

---

## Verification

```bash
cd backend && .venv/Scripts/python -m pytest
```

```bash
cd backend && .venv/Scripts/python -m ruff check app tests
```

```bash
cd frontend && npm test && npm run build
```

Last full run: **837 backend tests passed**, 27 frontend tests passed, frontend
typecheck and build clean. Two further tests carry the `smoke` marker and are
excluded by default because they make a real, billable call to a live provider;
run them explicitly with `-m smoke` once GenAILab credentials are configured.

---

## Scope

### Implemented

**Runtime and cost control**

- **Cost-aware orchestrator** (`POST /api/v1/ai/execute`) — classifies
  complexity, resolves a versioned routing policy, filters candidate models by
  capability and budget, and produces an `ExecutionPlan` *before* any model is
  called. Deterministic rules and lightweight scoring only; no LLM is called to
  decide a route.
- **Provider-agnostic model gateway** with GenAILab and mock implementations,
  bounded retry, exponential backoff with full jitter, and a circuit breaker.
  One fallback attempt on failure, then an honest error — never a fabricated
  result.
- **Model registry** with capability, modality and budget filtering, where an
  unknown attribute never satisfies a requirement.
- **Telemetry persistence and aggregation** — every execution records cost,
  tokens, latency, quality, guardrail decision and provenance.
  `/cost/summary`, `/cost/by-model`, `/cost/by-agent`, `/cost/by-plant`,
  `/cost/trend`, with date-range and scope filters.
- **Budgets** (`/budgets`, `/budgets/status`) at tenant, plant, department and
  workload scope, with warning and critical thresholds.

**Intelligence and optimization**

- Anomaly detection (`/anomalies`) and forecasting (`/forecasts`).
- Optimization lifecycle (`/optimization/*`): analyze, recommend, approve,
  apply, roll back. Activation creates a new immutable policy version and
  supersedes its predecessor.
- What-if simulator (`/optimization/simulate`) returning current, forecast and
  optimized cost, each carrying its own provenance label.

**Security**

- JWT validation (signature, expiry, not-before, issuer, audience; `alg: none`
  refused), pluggable identity adapters, and an OIDC seam that fails loudly
  rather than faking validation.
- RBAC over six roles with the role-to-permission policy stated once, tenant
  resolution from the authenticated principal, and resource-level scope
  (tenant / plant / department). Cross-tenant reads return 404, not 403.
- Four guardrail layers — input, context, tool and output — wired into the
  execution path.
- Request size limits, rate-limiting hooks, audit logging, and a startup audit
  that refuses to serve if any route lacks authentication.

**Frontend** — Dashboard, Quality Inspection, Optimization Center, What-if
Simulator and Status pages. Wire types mirror the contract exactly and are
converted by adapters into view models, so a response that drifts from the
contract fails at the boundary instead of being absorbed.

**Operations** — Alembic async migrations, Docker files and compose stack,
deterministic demo dataset with a reset command, and a development token CLI.

### Not implemented

- **Eight read-only control-plane and governance paths** in the contract:
  `/workloads`, `/agents`, `/plants`, `/departments`, `/policies`,
  `/governance/approvals`, `/governance/approvals/{id}/decide` and
  `/governance/audit`. The underlying tables exist and are seeded; only the
  listing endpoints are absent. Approval and audit *behaviour* is implemented
  and enforced — it is the HTTP surface for browsing it that is missing.
- **Enterprise OIDC authentication.** The adapter is a seam that raises; wiring
  it needs the deployment's issuer, JWKS endpoint, audience and claim mapping.
- **Live GenAILab execution.** The client is implemented and unit-tested
  against a stub, but has never been exercised against the live service — no
  credentials were available. The smoke test exists and is excluded by default.
- **Reconciliation of token claims against stored role assignments.** Roles
  arrive as token claims; the `users` and `user_roles` tables are seeded but
  nothing yet checks a claim against them, and `tenants.status` is not checked.
- **`ExecutionBudget` enforcement.** Implemented and tested, but not wired: no
  agent loop exists on the request path, so wiring it would mean inventing a
  call site.

### What is real, and what is not

The demo dataset is **simulated**. It describes a fictional company, no request
in it was ever executed, and no invoice backs any figure in it. `actual_cost` is
`0.0` across the dataset because nothing was billed, and the platform will not
populate that field without a billing record.

Provenance labels — `ACTUAL`, `ESTIMATED`, `FORECAST`, `SIMULATED`,
`UNAVAILABLE` — are attached per field and never blended. Unknown is reported as
`null`, never as `0`. What the demonstration shows is the **control loop**, not
a savings claim.

### Known open items

- **Applying a recommendation does not repin the model.**
  `optimization_recommendations` stores its strategy as free text and
  `DATABASE_SCHEMA.md` defines no column naming a target model, so activation
  advances the policy version and supersedes the predecessor without changing
  the model. Encoding a machine-readable target requires a schema change agreed
  first.
- **Redis role.** `ARCHITECTURE.md` section 13 lists Redis as a core component
  but does not state what the platform stores in it. Only a connection, health
  probe and cache abstraction exist; no caching semantics were invented.
- **Rate limiting is per-process.** It does not coordinate across workers.
- **Role-to-permission matrix.** `SECURITY.md` section 4 names the six roles but
  states no mapping from role to operation. The defaults in
  `backend/app/security/permissions.py::ROLE_PERMISSIONS` are a documented
  assumption derived from the role names, the contract's operations and the one
  worked example in section 4. Confirm the intended matrix and correct it there;
  no call site restates it.
- **SQLite.** Migration considerations for PostgreSQL are recorded in
  `docs/PRODUCTION_READINESS.md`.

---

## Non-negotiables

These are enforced in review, and several are enforced in code:

1. **All LLM access goes through `ModelGatewayInterface`.** No business module
   constructs a provider SDK client.
2. **No secrets in the frontend, in logs, in prompts or in source control.**
   `VITE_`-prefixed values are compiled into the public bundle by definition.
3. **Cost values carry provenance.** ACTUAL, ESTIMATED, FORECAST and SIMULATED
   are never blended or relabelled. Token usage is never fabricated; when a
   provider reports none, provenance becomes ESTIMATED or UNAVAILABLE.
4. **LLMs recommend; deterministic policy code authorizes.** An LLM cannot
   override a budget, an authorization decision or an approval requirement.
5. **Optimization recommendations are not production changes.** They require
   policy validation, risk assessment and approval before becoming a versioned
   routing policy, and every activation must be reversible.
6. **Runtime routing prefers rules and lightweight ML.** An expensive LLM is
   never called merely to choose a model for a request.
7. **SQLite stays behind SQLAlchemy repositories** so the MVP database can be
   replaced without touching business logic.
8. **Every feature ships with tests**, and test results are reported honestly.
