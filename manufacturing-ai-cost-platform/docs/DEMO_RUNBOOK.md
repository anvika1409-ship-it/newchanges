# Demo Runbook

Operating instructions for the judge demonstration of the Manufacturing AI Cost
Intelligence & Autonomous Optimization Platform.

Everything below was executed against this repository and the outputs are
transcribed from the actual responses. Where a number is quoted, it is what the
API returned, not what it ought to return.

---

## 0. Read this before demonstrating

### The dataset is simulated. Say so out loud.

The demo runs on a seeded dataset describing a fictional company,
**"ACME Manufacturing (DEMO)"**. No AI request in it was ever executed, no
invoice backs any figure in it, and no saving in it was ever realised.

The platform distinguishes provenance labels, and the demo relies on that
distinction being visible rather than smoothed over:

| Label | Meaning | Where it appears in the demo |
|---|---|---|
| `ACTUAL` | Measured from a real execution or a provider bill | **Nowhere in the seeded data.** `actual_cost` is `0.0` across the dataset |
| `ESTIMATED` | Computed from registry pricing, not billed | Every seeded cost event; the recommendation's savings |
| `FORECAST` | A projection over a future window | `/forecasts`; the simulator's `forecast_cost` |
| `SIMULATED` | A hypothetical that never ran | The what-if simulator; the seeded model quality/latency metadata |
| `UNAVAILABLE` | Genuinely unknown — not zero | Live mock executions, where the mock gateway reports no token usage |

A judge asking "is that real money?" should get a straight no. The claim worth
making is about the *mechanism* — the platform decides before spending, records
what it spent, detects a change, and routes differently afterwards — not about a
savings figure.

### One honest caveat about model metadata

`app/db/seed/genailab_models.json` leaves pricing, context windows, quality and
latency **null** for all four GenAILab models, because none of those values is
documented in any source document and guessing them would be fabrication.

The demo seed then overlays **SIMULATED** quality, latency and pricing onto two
vision models (`_apply_simulated_model_metadata` in `app/db/seed/demo_data.py`),
because cost comparison is meaningless without a price. So when `GET /models`
shows `input_cost: 0.0001`, that is a demo assumption, not GenAILab's price
list. If a judge asks where the pricing came from, that is the answer.

---

## 1. Demo setup

### Prerequisites

- Python 3.12 with the backend virtualenv installed (`backend/.venv`)
- Node 20+ for the frontend
- No Redis and no GenAILab credentials required (see section 5)

### 1.1 Backend environment

From `backend/`, create `.env` from the template and set a signing secret:

```bash
cp .env.example .env
```

The four values that matter for the demo:

```
APP_ENV=development
AUTH_MODE=development
MODEL_GATEWAY_PROVIDER=mock
JWT_SECRET=<a random value of at least 32 bytes>
```

`JWT_SECRET` is a credential. Generate one; do not commit it, and do not reuse a
value from another environment.

> **The single most common demo failure:** the token command in 1.3 and the
> running server must read the *same* `JWT_SECRET`. If they differ, every
> request returns `401 invalid_token` and the server log shows
> `"reason":"InvalidSignatureError"`. Both read `backend/.env`, so run both from
> `backend/` and this takes care of itself.

### 1.2 Seed the demo dataset

```bash
python -m app.db.seed.demo_cli reset
```

Expected output — these counts are pinned by `tests/test_demo_dataset.py` and
should match exactly:

```
Demo dataset reset. All values are SIMULATED.
  tenant: tenant-acme-manufacturing
  models               4
  routing_policies     9
  usage_events         500
  cost_events          500
  anomalies            1
  forecasts            7
  recommendations      1
```

`seed` is idempotent (it skips when the demo tenant exists); `reset` drops and
recreates. Both refuse to run when `APP_ENV=production`.

### 1.3 Mint a bearer token

There is no login route — in a real deployment tokens come from the identity
provider. For local work and demonstrations:

```bash
python -m app.security.dev_token --role ADMIN
```

Roles worth having open in separate terminals during the demo:

| Role | Used for | Command |
|---|---|---|
| `AI_ENGINEER` | Executing workloads (steps 1-4, 11) | `python -m app.security.dev_token --role AI_ENGINEER` |
| `ADMIN` | Approving and applying (steps 9-10) | `python -m app.security.dev_token --role ADMIN` |
| `VIEWER` | Demonstrating a refusal | `python -m app.security.dev_token --role VIEWER` |

The command refuses to run when `APP_ENV=production` or when `AUTH_MODE=oidc`.
The token it prints grants real access for its lifetime — treat it as a
credential.

### 1.4 Start the backend

```bash
python -m uvicorn app.main:create_app --factory --port 8000
```

Verify:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

```json
{"status":"alive"}
```

```bash
curl -s http://127.0.0.1:8000/api/v1/ready
```

```json
{"status":"ready","checks":{"database":true,"cache":true}}
```

Interactive API docs are at `http://127.0.0.1:8000/docs` and are a perfectly
good demo surface if a browser is easier than curl.

### 1.5 Start the frontend

```bash
npm install && npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api` to
`http://localhost:8000`, so no CORS setup is needed.

**Then connect the UI to the backend.** In the header there is a status chip
reading **`Demo fixtures`** and a token field. Paste the ADMIN token from 1.3
and click **Connect**; the chip turns to **`Live API`** and every page reloads
against the backend.

This step is not optional. The bundle ships without a token by design — a token
embedded in a `VITE_*` variable would be readable by anyone who loads the page —
so until you connect one, **every page renders local fixture data**, clearly
labelled, and none of the seeded story is visible. The token is held in memory
only; it is not written to `localStorage` and is gone on reload.

---

## 2. Demo reset

Between run-throughs, from `backend/`:

```bash
python -m app.db.seed.demo_cli reset
```

This drops every table, recreates the schema and reseeds. It returns the
platform to the exact state in 1.2, which matters because the story is
one-directional: once `demo-opt-rec-001` is applied it is `APPLIED`, and a
second approval attempt returns `409`.

To confirm state at any point without changing it:

```bash
python -m app.db.seed.demo_cli status
```

If the UI was connected, click **Clear token** and reconnect after a reset, or
just reload the page.

---

## 3. The eleven-step story, with exact calls and expected output

Set up two shell variables from 1.3:

```bash
ENG=$(python -m app.security.dev_token --role AI_ENGINEER)
```

```bash
ADM=$(python -m app.security.dev_token --role ADMIN)
```

And the inspection request used in steps 1-4 and 11:

```bash
REQ='{"workload_type":"quality_check","business_priority":"NORMAL","plant_id":"plant-pune","department_id":"dept-plant-pune-quality","workload_id":"wl-plant-pune-quality_check","request_payload":{"line":"assembly-4","part":"housing-A"},"input_refs":[{"ref":"obj://demo/inspection-1","content_type":"image/png"}],"modality":"image"}'
```

### Steps 1-4 — A workload creates AI demand; the orchestrator decides *before* executing; a model is selected; cost, quality and latency are captured

**UI:** Quality Inspection, then run an inspection.
**API:**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/execute -H "Authorization: Bearer $ENG" -H "Content-Type: application/json" -d "$REQ"
```

Actual response:

```json
{
  "request_id": "3ebe9489-2760-4399-8d5e-e42ca4e403ac",
  "execution_plan": {
    "workload_type": "quality_check",
    "complexity": "MEDIUM",
    "selected_model_id": "model-vision-llama-90b",
    "estimated_cost": null,
    "max_context_tokens": 4096,
    "max_tool_calls": 5,
    "routing_policy_version": 1,
    "budget_status": "ALLOW",
    "risk_level": "LOW"
  },
  "result": { "verdict": "INCONCLUSIVE", "defect_type": null, "confidence": null },
  "usage": { "input_tokens": 0, "output_tokens": 0, "total_tokens": 0 },
  "cost": { "amount": null, "currency": "per_1k_tokens", "provenance": "UNAVAILABLE" },
  "quality_score": null
}
```

**The point to make:** `execution_plan` is the whole argument. Complexity,
model, token ceiling, tool-call ceiling and budget verdict were all decided
*before* the model was called, by deterministic rules and a versioned routing
policy — not by asking an LLM what to do. `routing_policy_version: 1` is the
number that changes in step 11.

**Do not skip past the nulls.** `cost.amount` is `null` with provenance
`UNAVAILABLE` because the mock gateway returns no token usage, so there is
nothing to price. The platform reports "unknown" rather than `0`. That is the
behaviour that keeps a dashboard honest, and it is worth one sentence.

`verdict: "INCONCLUSIVE"` is the same discipline: the mock gateway returns a
placeholder string, and the workload refuses to read a pass/fail out of it.

### Step 5 — The platform detects an abnormal cost increase

**UI:** Dashboard, Anomalies panel.
**API:**

```bash
curl -s -H "Authorization: Bearer $ADM" http://127.0.0.1:8000/api/v1/anomalies
```

```json
{"items":[{
  "id": "demo-anomaly-001",
  "timestamp": "2026-08-25T08:00:00",
  "scope_type": "WORKLOAD",
  "scope_id": "wl-plant-pune-quality_check",
  "anomaly_type": "cost_spike",
  "severity": "HIGH",
  "expected_value": 0.48,
  "actual_value": 6.24,
  "deviation_percent": 1200.0,
  "reason": "SIMULATED DEMO: quality_check volume rose 3x and every request routed to the high-capability vision model, including simple single-part inspections.",
  "status": "OPEN"
}],"page":{"total":1,"limit":50,"offset":0}}
```

The spike is real in the data, not just asserted in the anomaly row — six days
of `quality_check` at 40 requests/day on the cheap model, then a final day at
120 requests on the expensive one. `GET /cost/trend` shows it as a flat
`0.72`/day baseline followed by the jump, and `tests/test_demo_dataset.py`
asserts the split (240 requests on Phi, 120 on Llama).

### Steps 6-8 — Root cause explained; a cheaper strategy proposed; savings and quality impact estimated

**UI:** Optimization Center.
**API:**

```bash
curl -s -H "Authorization: Bearer $ADM" http://127.0.0.1:8000/api/v1/optimization/recommendations
```

```json
{"items":[{
  "id": "demo-opt-rec-001",
  "workload_id": "wl-plant-pune-quality_check",
  "current_strategy": "Use Llama-90B-Vision for all quality checks",
  "recommended_strategy": "Route simple checks to Phi-3.5-vision; keep Llama for complex",
  "estimated_saving": 120.0,
  "estimated_saving_percent": 18.5,
  "quality_impact_percent": -0.5,
  "latency_impact_percent": 15.0,
  "risk_level": "LOW",
  "recommendation_reason": "SIMULATED DEMO: Phi-3.5 handles simple defect patterns at lower cost. Complex / safety-critical checks remain on the high-capability model.",
  "status": "DRAFT",
  "provenance": "ESTIMATED",
  "approved_at": null,
  "applied_at": null
}]}
```

**Say the quiet part:** `provenance: "ESTIMATED"` and `status: "DRAFT"`. This is
a proposal that has never run and has not been approved. `120.0` is a projection
under stated assumptions, not money saved.

Note the recommendation carries **quality and latency impact alongside the
saving** (-0.5% quality, +15% latency). A cost tool that reports only the saving
is asking to be trusted with a decision it has not shown you.

For the live-generation path rather than the seeded row, `POST
/optimization/analyze` with `{"workload_id": "...", "simulation_only": true}`
produces a recommendation from current telemetry.

**Optional, and a good one if there is time — the What-if Simulator** (UI:
What-if; API: `POST /api/v1/optimization/simulate`):

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/optimization/simulate -H "Authorization: Bearer $ADM" -H "Content-Type: application/json" -d '{"request_volume":10000,"workload_id":"wl-plant-pune-quality_check","production_volume":50000,"image_volume":10000,"budget_amount":500,"horizon_days":30,"model_mix":[{"model_id":"model-vision-phi-35","share_percent":70},{"model_id":"model-vision-llama-90b","share_percent":30}]}'
```

```json
{
  "provenance": "SIMULATED",
  "horizon_days": 30,
  "current_cost":   {"amount": 10.80,  "provenance": "ESTIMATED"},
  "forecast_cost":  {"amount": 215.14, "provenance": "FORECAST"},
  "optimized_cost": {"amount": 52.80,  "provenance": "SIMULATED"},
  "estimated_saving": {"amount": 162.34, "provenance": "SIMULATED"},
  "estimated_saving_percent": 75.46,
  "risk_level": "MEDIUM",
  "within_budget": true,
  "assumptions": ["..."]
}
```

Four different provenance labels in one response, each attached to its own
figure rather than to the response as a whole — and an `assumptions` list that
states the per-request rate used and ends with: applying any change still
requires validation, approval and a versioned policy.

### Step 9 — Human approval, shown where it is needed

First show the refusal. **This is the more interesting half of the step:**

```bash
VIEW=$(python -m app.security.dev_token --role VIEWER)
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/optimization/demo-opt-rec-001/approve -H "Authorization: Bearer $VIEW" -H "Content-Type: application/json" -d '{"decision":"APPROVED"}'
```

```json
{"code":"forbidden","message":"Principal 'demo-viewer' lacks FINOPS_MANAGER or ADMIN role to approve LOW risk policy","request_id":"..."}
```

`403`. Then approve properly:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/optimization/demo-opt-rec-001/approve -H "Authorization: Bearer $ADM" -H "Content-Type: application/json" -d '{"decision":"APPROVED","comments":"Reviewed by FinOps"}'
```

```json
{"id":"demo-opt-rec-001","status":"APPROVED","approved_by":"demo-admin","approved_at":"2026-08-27T09:54:32.381807Z"}
```

**UI:** Optimization Center, then **Approve** on the recommendation card. The UI
sends `{"decision": ...}` and the Reject button sends `REJECTED` — worth
mentioning that authorization is enforced by the backend, so hiding a button
would not have been a control.

### Step 10 — The new routing policy is activated

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/optimization/demo-opt-rec-001/apply -H "Authorization: Bearer $ADM" -H "Content-Type: application/json" -d '{"activation_mode":"FULL"}'
```

```json
{
  "recommendation_id": "demo-opt-rec-001",
  "status": "APPLIED",
  "applied_policy_id": "201b059a-473e-4e1e-8757-343550c7407b",
  "applied_policy_version": 2,
  "superseded_policy_id": "policy-quality_check-medium-v1",
  "activation_mode": "FULL"
}
```

Policies are immutable and versioned: v2 is *created*, v1 is *superseded*, and
`superseded_policy_id` records exactly which row was replaced — so the change is
auditable and `POST /optimization/{id}/rollback` has something to return to.
`activation_mode: "CANARY"` with `canary_traffic_percent` is also supported.

An `AuditEvent` is written recording who approved and applied.

### Step 11 — A future request automatically uses the optimized strategy

Re-run the **identical** request from step 1:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/execute -H "Authorization: Bearer $ENG" -H "Content-Type: application/json" -d "$REQ"
```

```json
{"execution_plan": {
  "selected_model_id": "model-vision-llama-90b",
  "routing_policy_version": 2,
  "complexity": "MEDIUM",
  "budget_status": "ALLOW"
}}
```

**`routing_policy_version` went from 1 to 2 with no change to the request and no
restart.** The next execution picked up the newly activated policy on its own.
That is the closing claim.

**Be straight about what did not change.** `selected_model_id` is the same. The
recommendation records its strategy only as free text
("Route simple checks to Phi-3.5-vision..."), and `DATABASE_SCHEMA.md` defines
no column naming a target model, so activation advances the policy version and
supersedes the predecessor but does not itself repin the model — model selection
still falls back to capability and budget filtering. Encoding a machine-readable
target needs a schema change agreed first, and inventing one to make the demo
land better would have been exactly the kind of fabrication this platform exists
to avoid. The limitation is recorded in `app/services/policy_lifecycle.py` at
the line that sets `selected_model_id=None`.

If a judge presses on this: the control loop — detect, explain, propose,
approve, version, activate, take effect — is complete and demonstrable end to
end. The payload the loop carries is one schema field short.

---

## 4. Dashboard walkthrough

**UI:** Dashboard, after connecting the token.

```bash
curl -s -H "Authorization: Bearer $ADM" http://127.0.0.1:8000/api/v1/cost/summary
```

```json
{
  "actual_cost": 0.0,
  "estimated_cost": 10.80,
  "unavailable_cost_events": 0,
  "currency": "USD",
  "total_requests": 500,
  "total_tokens": 320000,
  "average_cost_per_request": 0.0216,
  "budget_consumed_percent": null,
  "forecast_month_end_cost": null
}
```

Three things worth pointing at:

- `actual_cost` and `estimated_cost` are **separate fields**, never summed into
  one "cost" number. `actual_cost: 0.0` is truthful: nothing here was billed.
- `unavailable_cost_events` counts executions that could not be priced at all.
  It rises as you run live executions in step 1 (the mock gateway reports no
  usage), which is the honest outcome, not a bug to hide.
- `budget_consumed_percent: null` — not `0`. Unknown is not zero anywhere in
  this platform.

Also available: `/cost/by-model`, `/cost/by-agent`, `/cost/by-plant`,
`/cost/trend`, `/budgets/status`, `/forecasts`, `/models`.

`/cost/by-model` makes the spike attributable in one glance:

```json
{"items":[
  {"id":"model-vision-llama-90b","estimated_cost":6.24,"total_requests":120},
  {"id":"model-vision-phi-35",   "estimated_cost":4.56,"total_requests":380}
]}
```

120 requests on the expensive model cost more than 380 on the cheap one. That
single line is the entire optimization argument.

(These are the counts at freshly seeded state. Each live execution you run in
step 1 adds to the Llama row, so expect 121 or 122 by the time you reach the
dashboard.)

---

## 5. Fallback if GenAILab is unavailable

**The demo does not require GenAILab, and the default configuration does not use
it.** `MODEL_GATEWAY_PROVIDER=mock` in `.env.example` is the demo setting, and
every output in this runbook was produced with it.

### If you were planning to demo against live GenAILab and it is down

Set `MODEL_GATEWAY_PROVIDER=mock` and restart. Nothing else changes: the mock
implements the same `ModelGateway` interface, so the orchestrator, guardrails,
telemetry, policies and every screen behave identically. What you lose is only
real token counts and real latency.

Symptom to recognise: `POST /ai/execute` returns a gateway error, and the server
log shows `"event":"orchestrator_primary_model_failed"` with a normalized
`error_code`.

### What the platform does on its own when a model call fails

Worth demonstrating deliberately if the failure happens live, because the
handling is a feature:

1. The gateway applies a bounded retry with exponential backoff and full jitter.
2. A circuit breaker opens after repeated failures and stops sending traffic to
   a provider that is down.
3. The orchestrator makes **one** fallback attempt against the next-best
   candidate model when the policy allows it, and marks the telemetry row
   `fallback_used = true`.
4. If no fallback qualifies, the normalized error propagates. **The request
   fails.** It does not return a fabricated result.

### If GenAILab credentials are missing entirely

Starting with `MODEL_GATEWAY_PROVIDER=genailab` and no `GENAI_API_KEY` fails at
startup with `GENAI_API_KEY must be set when MODEL_GATEWAY_PROVIDER=genailab`.
That is a deliberate fail-fast, not a crash to apologise for — the alternative
is a server that accepts requests it cannot serve.

### Other failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `401 invalid_token`, log says `InvalidSignatureError` | Token minted with a different `JWT_SECRET` than the server | Run both from `backend/` so both read the same `.env` |
| `401 unauthorized` on every UI panel, chip reads `Demo fixtures` | No token connected in the header | Section 1.5 |
| `409` on approve | The recommendation was already applied in a previous run | `python -m app.db.seed.demo_cli reset` |
| Dashboard totals are zero | Dataset not seeded, or the backend is pointed at a different `DATABASE_URL` than the one seeded | `demo_cli status` to confirm |
| Redis connection errors | `REDIS_ENABLED=true` with no Redis running | Set `REDIS_ENABLED=false`; the cache is optional |

---

## 6. The five-minute judge story

Timings assume the setup in section 1 is already done and both terminals are
open. Rehearse the reset — walking in with a half-applied recommendation is the
one failure that cannot be recovered mid-demo.

**0:00 — The problem (30s)**

> A manufacturing plant runs AI on every part that comes off the line — vision
> quality checks, predictive maintenance, supply-chain forecasting. Each call
> costs money. Most platforms find out what that cost was at the end of the
> month, from an invoice. By then the decisions that caused it are three weeks
> old.

**0:30 — Decide before you spend (60s)**

Run `POST /ai/execute`. Put the `execution_plan` on screen.

> Before any model is called, the platform has already chosen: complexity
> MEDIUM, this vision model, a 4096-token ceiling, five tool calls maximum,
> budget verdict ALLOW, under routing policy version 1. That decision is made by
> deterministic rules and a versioned policy — we do not call an expensive model
> to decide which model to call.

Point at `cost.amount: null` / `provenance: "UNAVAILABLE"`:

> And where we do not know, we say null. Not zero.

**1:30 — Something changed (45s)**

Dashboard trend, then `/anomalies`.

> Flat baseline, then a spike. The platform flagged it: expected 0.48, actual
> 6.24, twelve hundred percent over. Cause, from the telemetry itself — volume
> tripled and every request went to the high-capability model, including simple
> single-part inspections.

Show `/cost/by-model`:

> 120 requests on the expensive model cost more than 380 on the cheap one.

**2:15 — What to do about it (60s)**

Optimization Center.

> Route simple checks to the smaller vision model, keep the large one for
> complex and safety-critical work. Estimated saving 18.5%. Quality impact minus
> half a percent. Latency up 15%. Risk LOW.
>
> Every one of those is labelled ESTIMATED, and the recommendation is in DRAFT.
> Nothing has run. This is a proposal with its costs *and* its trade-offs on the
> table.

**3:15 — A human decides (45s)**

Attempt the approval as VIEWER first — show the `403` — then approve as ADMIN.

> An analyst cannot approve a change that spends money. Enforced in the backend,
> so it holds whether the call comes from our UI or from curl.

Apply it.

> Version 2 created, version 1 superseded, recorded against the user who
> approved it. Policies are immutable and versioned, so this is auditable and
> reversible.

**4:00 — It takes effect on its own (45s)**

Re-run the identical request from 0:30.

> Same request, no restart, no deployment. Routing policy version 2. The next
> execution picked up the new policy by itself. That is the loop closing:
> workload creates demand, orchestrator decides before spending, telemetry
> records it, anomaly detection catches the change, optimization proposes, a
> human approves, a versioned policy activates, and the next request routes
> under it.

**4:45 — Close (15s)**

> Every number you have seen is simulated or estimated — this is a seeded
> dataset, not production spend, and the platform labels which is which on every
> field rather than blending them into one figure. What is real is the control
> loop, and it runs end to end.

### If a judge asks

- **"Are these real savings?"** — No. Simulated dataset, estimated figures. No
  execution in it was ever billed. `actual_cost` is `0.0` and the platform will
  not populate it without a billing record.
- **"Where did the model pricing come from?"** — The GenAILab seed file leaves
  pricing null because it is not documented anywhere; the demo seed overlays
  simulated prices so cost comparison is possible. Section 0.
- **"Did the model actually change in step 11?"** — No, and that is a real gap,
  not a demo shortcut. The policy version advanced and took effect; the
  recommendation has no machine-readable target model to pin. Section 3, step 11.
- **"Is this production-ready?"** — See `docs/PRODUCTION_READINESS.md`, which
  lists what is complete, what is a hackathon compromise, and what is not done.
- **"What if GenAILab is down?"** — Section 5. Bounded retry, circuit breaker,
  one fallback attempt, then an honest failure.

---

## 7. What this demo does not show

Stated here so nobody has to discover it live:

- **No live GenAILab execution.** Every run uses the mock gateway. The GenAILab
  client is implemented and unit-tested against a stub, but has never been
  exercised against the live service.
- **No real cost.** `actual_cost` is `0.0` throughout, by design.
- **Applying a recommendation does not repin the model.** Section 3, step 11.
- **Rate limiting is per-process.** It does not coordinate across workers.
- **The dataset is anchored to a fixed date** (`2026-08-26`) so the numbers stay
  reproducible. Demonstrating far in the future may push the seeded week outside
  a default dashboard window; reseed or widen the range if so.
- **`docker compose` has not been run end to end** in this environment; the
  runbook above uses the local processes that were verified.

---

## Appendix — command reference

| Purpose | Command |
|---|---|
| Seed (idempotent) | `python -m app.db.seed.demo_cli seed` |
| Reset (destructive) | `python -m app.db.seed.demo_cli reset` |
| State check | `python -m app.db.seed.demo_cli status` |
| Mint a token | `python -m app.security.dev_token --role ADMIN` |
| Start backend | `python -m uvicorn app.main:create_app --factory --port 8000` |
| Start frontend | `npm run dev` (from `frontend/`) |
| Backend tests | `python -m pytest` (from `backend/`) |
| Frontend tests | `npm test` (from `frontend/`) |
