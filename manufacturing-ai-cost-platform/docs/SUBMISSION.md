# Submission

**Manufacturing AI Cost Intelligence & Autonomous Optimization Platform**

A cost-aware AI runtime for manufacturing workloads. It decides which model to
use *before* spending money, records what each execution actually cost, detects
when that cost changes, proposes a cheaper strategy, requires a human to approve
it, and routes the next request under the new versioned policy.

---

## Evaluate it in ten minutes

| Time | Do this |
|---|---|
| 2 min | Read [what is claimed](#what-is-claimed-and-what-is-not) below. It states plainly which numbers are real. |
| 5 min | Run the demo: [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) sections 1 and 3. Setup is three commands. |
| 3 min | Skim [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for the limitations we found ourselves. |

Fastest possible check that it works, from `backend/`:

```bash
.venv/Scripts/python -m pytest
```

Expected: `837 passed, 2 deselected`. The two deselected carry the `smoke`
marker — they make a real billable call to a live provider and are excluded by
default.

---

## What is claimed, and what is not

This matters more than any feature list, because the subject is cost.

**Not claimed:** any production saving. The demo dataset is **simulated**. It
describes a fictional company, no request in it was ever executed, and no
invoice backs any figure in it. `actual_cost` is `0.0` across the entire dataset
because nothing was billed, and the platform will not populate that field
without a billing record.

**Claimed:** the control loop runs end to end, and every figure carries a label
saying what kind of number it is.

The platform distinguishes five provenance labels and never blends them:

| Label | Meaning |
|---|---|
| `ACTUAL` | Measured from a real execution or a provider bill |
| `ESTIMATED` | Computed from registry pricing, not billed |
| `FORECAST` | A projection over a future window |
| `SIMULATED` | A hypothetical that never ran |
| `UNAVAILABLE` | Genuinely unknown — reported as `null`, never as `0` |

A live execution against the mock gateway returns
`cost: {amount: null, provenance: "UNAVAILABLE"}` because the mock reports no
token usage, so there is nothing to price. Reporting `0` there would have been
easier and would have made the dashboard look better. It would also have been a
lie about money.

---

## The demonstration, in one line each

1. A quality-inspection workload creates AI demand — `POST /ai/execute`
2. The orchestrator decides **before** the model call — the response carries an `ExecutionPlan`
3. A model is selected by capability, budget and a versioned routing policy
4. Cost, tokens, latency and quality are captured with provenance
5. An abnormal cost increase is detected — `GET /anomalies`, deviation 1200%
6. The cause is attributable from telemetry — volume tripled *and* routed to the expensive model
7. A cheaper strategy is proposed — `GET /optimization/recommendations`
8. Saving, quality impact and latency impact are estimated together, all labelled `ESTIMATED`
9. A human approves — a VIEWER attempt returns `403`; an ADMIN succeeds
10. A new routing policy is activated — v2 created, v1 superseded, audited
11. The next identical request routes under `routing_policy_version: 2`, with no restart

Verified end to end over HTTP against a running server. Exact calls and actual
responses are in [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) section 3.

---

## What was built

**22 of the 30 paths** in `API_CONTRACT.yaml`, across 126 backend modules and 49
frontend modules.

**Runtime** — cost-aware orchestrator producing an `ExecutionPlan` before
execution; provider-agnostic model gateway (GenAILab + mock) with bounded retry,
jittered backoff, circuit breaker and one fallback attempt; model registry where
an unknown attribute never satisfies a requirement.

**Cost** — telemetry persistence and aggregation across model, agent, plant and
time; budgets at four scopes with warning and critical thresholds.

**Intelligence** — anomaly detection, forecasting, optimization lifecycle
(analyze → recommend → approve → apply → roll back) with immutable versioned
policies, and a what-if simulator.

**Security** — JWT validation with `alg: none` refused, RBAC over six roles,
tenant isolation (cross-tenant returns 404, not 403), four guardrail layers
wired into the execution path, request size limits, audit logging, and a startup
audit that refuses to serve if any route lacks authentication.

**Frontend** — five pages, with contract-mirroring wire types converted by
adapters into view models, so a drifting response fails at the boundary rather
than being absorbed.

---

## Evidence

| Check | Result |
|---|---|
| Backend tests | **837 passed**, 2 deselected (`smoke`) |
| Frontend tests | 27 passed |
| Frontend typecheck + build | Clean |
| Lint (`ruff`) | Clean on all modules authored for this submission |
| End-to-end story | Verified over HTTP against a running server |
| Secrets in frontend bundle | None — the bearer token is entered at runtime and held in memory only |

Test files: 49. The suite includes dedicated security tests for unauthenticated
access, invalid and expired tokens, wrong role, cross-tenant access, budget
bypass, policy bypass, tool misuse, prompt injection, sensitive output, oversized
requests and secret leakage.

---

## Deliberately absent

Listed here rather than left to be discovered:

- **Eight read-only control-plane and governance listing paths.** The tables
  exist and are seeded; approval and audit *behaviour* is implemented and
  enforced. It is the HTTP surface for browsing it that is missing.
- **Live GenAILab execution.** The client is implemented and unit-tested against
  a stub but has never run against the live service — no credentials were
  available. The smoke test exists, gated behind four opt-in checks.
- **Enterprise OIDC.** A seam that raises rather than a fake that validates
  nothing. Wiring it needs the deployment's issuer, JWKS endpoint, audience and
  claim mapping.
- **Applying a recommendation does not repin the model.** The recommendation
  stores its strategy as free text and `DATABASE_SCHEMA.md` defines no column
  naming a target model. Activation advances the policy version and supersedes
  its predecessor, but model selection still falls back to capability and budget
  filtering. Inventing a schema field to make the demo land better was the one
  shortcut most worth refusing, in a project about not fabricating cost data.
- **`ExecutionBudget` enforcement.** Implemented and tested, not wired — no
  agent loop exists on the request path, so wiring it would mean inventing a
  call site.
- **Rate limiting is per-process.** It does not coordinate across workers.

`docker compose` has not been run end to end in the development environment
used; the documented local workflow is the one that was verified.

---

## Judgment calls worth knowing about

**Model metadata is null, on purpose.**
`backend/app/db/seed/genailab_models.json` leaves pricing, context windows,
quality and latency `null` for all four GenAILab models, because none of those
values is documented in any source document. The demo seed then overlays
**simulated** pricing so that cost comparison is possible at all, and labels it
as such. Guessing a real price would have been fabrication; leaving it null and
saying so is the honest form of the same feature.

**Unknown never satisfies a requirement.** A model with unknown capability stays
out of a candidate set until someone fills in the metadata that proves it
qualifies. Failing closed costs a candidate; failing open spends money on a
model nobody verified.

**LLMs recommend; deterministic code authorizes.** No LLM can override a budget,
an authorization decision or an approval requirement, and none is called merely
to choose a model for a request.

---

## Defects found and fixed during validation

Recorded because they show what the test and review passes were actually for:

- `/ai/execute` had authentication but **no authorization** — a VIEWER could
  spend money. `Permission.AI_EXECUTE` existed and was correctly withheld; the
  endpoint never checked it.
- **A rejected optimization was recorded as approved.** The backend accepted
  `{approved: bool = True}` while the contract and frontend send
  `{decision, comments}`. Pydantic ignored the unknown field and defaulted
  `approved` to `True`, so clicking *Reject* approved the change.
- **Telemetry for refused executions was silently rolled back** — the recorder
  shared the request session, which rolls back on exception. It now commits
  independently.
- **Activated policies were unreachable** — the workload *id* was used as its
  *type*, so a new policy landed under a key nothing looked up and no future
  request ever saw it.
- **The UI could never reach the backend** — the token setter was exported but
  never called, so every page silently fell back to fixtures.

Full detail in [`INTEGRATION_TEST_REPORT.md`](INTEGRATION_TEST_REPORT.md).

---

## Repository map

```text
manufacturing-ai-cost-platform/
├── backend/          FastAPI, Python 3.12+, 126 modules, 49 test files
│   ├── app/
│   │   ├── api/            contract surface
│   │   ├── orchestrator/   runtime routing, ExecutionPlan
│   │   ├── integrations/   model gateway (GenAILab + mock)
│   │   ├── guardrails/     input / context / tool / output
│   │   ├── security/       authn, JWT, RBAC, scope
│   │   ├── telemetry/      cost, quality, trace capture
│   │   ├── intelligence/   anomaly detection, forecasting
│   │   ├── optimization/   recommendation lifecycle, simulator
│   │   ├── repositories/   persistence abstraction
│   │   └── db/seed/        deterministic demo dataset + CLI
│   └── tests/
├── frontend/         React + TypeScript + Vite, 49 modules
└── docs/             source-of-truth documents + this submission
```

## Documents

| Document | Read it for |
|---|---|
| [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) | Running the demo: setup, reset, exact calls, expected output, fallback |
| [`DEMO_VIDEO_SCRIPT.md`](DEMO_VIDEO_SCRIPT.md) | Recording the demo video: shots, narration, timings |
| [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) | Completed controls, limitations, SQLite→PostgreSQL migration |
| [`INTEGRATION_TEST_REPORT.md`](INTEGRATION_TEST_REPORT.md) | End-to-end validation and the defects it surfaced |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`SECURITY.md`](SECURITY.md) · [`API_CONTRACT.yaml`](API_CONTRACT.yaml) · [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md) · [`AI_WORKFLOWS.md`](AI_WORKFLOWS.md) · [`AI_DEVELOPMENT_RULES.md`](AI_DEVELOPMENT_RULES.md) | The source-of-truth documents the implementation was held to |
