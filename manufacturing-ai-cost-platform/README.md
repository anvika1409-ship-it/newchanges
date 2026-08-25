# Manufacturing AI Cost Intelligence & Autonomous Optimization Platform

A cost-aware AI runtime, governance and optimization layer for manufacturing AI
workloads. The platform decides before expensive model execution occurs,
measures the actual outcome, and learns from history to improve future routing.

> This repository currently contains the **development foundation only**. No
> business functionality is implemented. See [Scope](#scope) below.

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
│   │   ├── security/       authn/authz hooks
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
cd backend && python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
```

```bash
cd backend && ./.venv/bin/uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8000`, so the browser
sees a single origin.

---

## Verification

```bash
cd backend && ./.venv/bin/pytest
```

```bash
cd backend && ./.venv/bin/ruff check app tests && ./.venv/bin/mypy app
```

```bash
cd frontend && npm run build
```

---

## Scope

### Implemented

- FastAPI application factory with lifespan-managed dependencies
- `GET /api/v1/health` and `GET /api/v1/ready` — the only two operations the
  contract currently defines without a security requirement
- Configuration and environment loading, with production safety guards
- Structured JSON logging with secret redaction
- Request ID / trace ID middleware and request size limiting
- Global exception handling returning the contract's `Error` shape
- Async SQLAlchemy database abstraction with the required SQLite pragmas
- Redis abstraction (connection and health only)
- Model gateway interface with GenAILab and mock implementations
- Initial security hooks: bearer scheme, scoped principal, role guard
- Alembic wired for async migrations
- Dockerfiles, compose stack, `.env.example` files

### Not implemented

No business functionality exists yet. Specifically absent:

- every business endpoint in `API_CONTRACT.yaml` (`/ai/execute`, `/cost/*`,
  `/budgets*`, `/forecasts`, `/anomalies`, `/optimization/*`, `/models*`,
  `/workloads`, `/agents`, `/plants`, `/departments`, `/policies`,
  `/governance/*`)
- every ORM model in `DATABASE_SCHEMA.md`, and therefore every migration
- the cost-aware orchestrator, policy engine and guardrail layers
- telemetry emission — no execution path exists yet to emit it
- LangGraph workflows
- enterprise OIDC authentication

### Known open items

- **Redis role.** `ARCHITECTURE.md` section 13 lists Redis as a core component
  but does not state what the platform stores in it. This scaffold provides only
  a connection and health probe and invents no caching semantics. Resolve the
  architecture question before building on it.
- **Control plane coverage.** `ARCHITECTURE.md` section 5 claims management of
  tenants, users, roles, guardrail policies and audit configuration; the
  contract does not yet cover them.

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
