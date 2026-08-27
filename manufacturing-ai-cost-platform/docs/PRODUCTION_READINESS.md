# Production Readiness Assessment

**Date:** 2026-08-27
**Scope:** Manufacturing AI Cost Intelligence & Autonomous Optimization Platform

---

## Verdict

> **NOT production-ready.**
>
> Material controls remain incomplete. The platform is a well-covered hackathon
> MVP with a sound architecture and a genuinely enforced security core, but four
> items below must close before it takes production traffic — chief among them
> that **the live GenAILab integration has never been executed once**.

Validation at the time of writing:

| Suite | Result |
|---|---|
| Backend (unit + integration + security + e2e) | **825 passed, 0 failed** |
| Frontend (vitest) | **27 passed, 0 failed** |
| Frontend typecheck + build | 0 errors, builds |
| `mypy` on modules changed in this pass | 0 issues |
| `mypy app` whole tree | 13 errors in 7 files (pre-existing) |
| Live GenAILab smoke | **2 skipped — never run** |

---

## 1. Findings from this pass

Each was found by inspection, fixed minimally, and pinned with a regression
test. The pattern across all three is the same and worth naming: **the code
existed and was unit-tested; nothing invoked it.** A control that is present and
inert is more dangerous than one that is absent, because it reads as covered.

### 1.1 Audit logging wrote nothing — **HIGH** — `app/services`, `app/api`

`audit_events`, `AuditEventRepository` and `GET /governance/audit` all existed.
The only `AuditEvent(...)` construction in the codebase was in demo seed data.
`budgets.py` even carried a comment acknowledging "the service that writes it
does not yet [exist]".

Consequence: `/governance/audit` returned an empty list no matter what happened.
SECURITY.md section 16 requires records for approvals, rejections, budget and
policy changes; AI_DEVELOPMENT_RULES.md section 12 requires every policy change
to be auditable.

**Fix:** added `app/services/audit.py` (`AuditService`, `AuditAction`) and wired
it into `PolicyLifecycleService` for approve and reject — the most
security-critical auditable actions. State snapshots are scanned and **redacted
rather than dropped** if they contain anything credential-shaped: discarding the
record would destroy the evidence that a secret was present. A failed audit write
is logged loudly and does not fail the operation being audited.

**Regression tests:** 5 in `test_production_controls.py`, covering that an
approval and a rejection each write a distinct record, that correlation ids are
carried, that secrets are redacted, and that a write failure does not propagate.

**Still open:** budget changes and model enable/disable are auditable per
section 16 and are **not yet wired**. See §3.

### 1.2 Rate limiting was never invoked — **HIGH** — `app/core`, `app/api`

`app/core/rate_limit.py` implemented a sliding-window limiter with 6 passing
tests. No middleware, route or dependency called it, so SECURITY.md section 18's
rate-limiting requirement was satisfied on paper only.

**Fix:** the limiter is built once per process onto `app.state`, and
`/ai/execute` — the endpoint that spends money — depends on
`enforce_execute_rate_limit`. The dependency is declared **after** the permission
guard so an unauthorized caller cannot burn a tenant's allowance. Keyed on the
authenticated principal, not the client address.

**Regression tests:** 5, covering that the 4th request in a window of 3 returns
429, that a refused request never reaches the gateway, that one caller cannot
exhaust another's allowance, and that rejected VIEWER attempts consume nothing.

### 1.3 Execution/agent-loop limits are not invoked — **MEDIUM** — `app/guardrails`

`ExecutionBudget` bounds iterations, tool calls, tokens and wall-clock duration
(SECURITY.md section 19), with 11 passing tests. Nothing constructs it.

**Not fixed, deliberately.** No LangGraph workflow currently runs on the request
path, so there is no loop to bound — wiring it would mean inventing a call site.
Recorded here so it is wired the moment the first agent workflow lands. Flagged
rather than forced.

### 1.4 No dependency lockfile — **MEDIUM** — build

`pyproject.toml` pins direct dependencies with upper bounds, which is good
practice, but there is no lockfile. Transitive dependencies float, so two builds
of the same commit can differ.

**Not fixed** — generating a lockfile is a build-process decision (pip-tools, uv
or Poetry) that should be made deliberately rather than imposed by this pass.

---

## 2. Completed controls

Verified by the test suite, not by inspection alone.

### Architecture
- All LLM access is behind `ModelGatewayInterface`. Verified by a test that the
  `openai` SDK is imported in exactly one module (`integrations/llm/genailab.py`).
- SQLite is behind SQLAlchemy repositories; no raw `sqlite3` usage in `app/`.
- LangGraph is confined to analytical workflows and does not replace FastAPI.
- Module boundaries follow AI_DEVELOPMENT_RULES.md section 30.

### Security
- **Authentication:** real JWT validation — signature, expiry, not-before,
  issuer, audience; `alg: none` refused; HMAC keys under 32 bytes refused.
- **Authorization:** endpoint-level permissions plus resource-level scope.
  `/ai/execute` requires `AI_EXECUTE`, which read-only roles do not hold.
- **Tenant isolation:** tenant derives from the token; a client-supplied tenant
  that differs is refused; cross-tenant reads return 404 rather than 403, so the
  status code does not confirm a record exists.
- **Guardrails:** input and output layers run on every `/ai/execute`; refusals
  are recorded with layer and reason (`INPUT:instruction_like_content`).
- **Secrets:** `SecretStr` throughout, redaction in the log formatter, and a test
  scanning all frontend source for credential shapes.
- **TLS:** `SSL_VERIFY=false` refused when `APP_ENV=production` unless an
  explicit documented exception is set.

### Resilience
- Bounded retry with exponential backoff and full jitter; circuit breaker with
  half-open probe; provider `Retry-After` honoured; the SDK's own retries
  disabled so budgets are not silently multiplied.
- Timeouts at request and gateway level.

### Observability
- Structured JSON logs with correlation ids and secret redaction.
- One `usage_event` + `cost_event` per execution, including refusals, committed
  in their own transaction so a business rollback cannot discard them.
- `/health` (liveness, no dependencies) and `/ready` (database + cache; the model
  gateway is deliberately **not** probed, since that would be a billable call).

### Data
- 20 tables, all covered by migrations. Verified: `alembic upgrade head` on an
  empty database produces 20/20 tables with no drift against the ORM.
- SQLite pragmas applied per connection: `foreign_keys=ON`, WAL, busy timeout.

### Correctness of cost reporting
- ACTUAL / ESTIMATED / FORECAST / SIMULATED are never blended. Unknown values are
  `null`, never `0`. Unpriced models make a simulation report "unavailable"
  rather than manufacturing a saving.

---

## 3. Known limitations

Stated plainly, because the sections above would otherwise imply more coverage
than exists.

1. **The live GenAILab integration has never been executed.** Not once, in any
   environment. Every test runs against `MockModelGateway`. Retry, circuit
   breaking, error normalisation and usage extraction are unproven against the
   real endpoint. **This is the single largest risk.**

2. **Audit coverage is partial.** Optimization approve/reject are audited. Budget
   changes, model enable/disable, policy activation and rollback are named in
   SECURITY.md section 16 and are not yet wired.

3. **Rate limiting is per-process.** With N replicas the effective ceiling is N ×
   the policy. `RateLimiter` is an abstraction precisely so a Redis-backed
   implementation can replace it.

4. **Context and tool guardrails are library-only.** Implemented and unit-tested;
   no execution path currently retrieves context or calls tools, so nothing
   invokes them.

5. **No database backup routine.** DATABASE_SCHEMA.md section 22 says to back the
   file up regularly. Nothing in the application does.

6. **`docker compose` has never been started.** Docker is unavailable in the
   development environment used. The compose file parses and declares the
   expected services; the stack has not been brought up.

7. **OIDC is not implemented.** The seam exists and raises
   `NotImplementedError` rather than pretending. Production requires an
   enterprise identity provider.

8. **13 `mypy` errors remain** in seven modules (untyped LangGraph state, stale
   `# type: ignore` comments). No test fails on them.

9. **Frontend coverage is logic-only** — adapters are tested; no component
   rendering or browser-level end-to-end test exists.

10. **Redis's platform role is still undefined.** ARCHITECTURE.md section 13
    lists it as a core component without saying what it stores. It is currently a
    connection-checked dependency only.

---

## 4. Hackathon-only compromises

Deliberate MVP choices that are **not** production-appropriate and are permitted
by AI_DEVELOPMENT_RULES.md section 40.

| Compromise | Why it is acceptable now | What production needs |
|---|---|---|
| **SQLite** | Zero setup, portable demo, adequate single-node | PostgreSQL (see §5) |
| **Development auth adapter** | Locally signed HS256; validation is real | OIDC with JWKS and asymmetric keys |
| **`SSL_VERIFY=false`** | Permitted for the internal dev gateway | TLS verification with the approved internal CA |
| **In-memory rate limiter** | Correct for one worker | Redis-backed shared counters |
| **In-memory circuit breaker** | Per-process state is workable at one replica | Shared state so replicas learn together |
| **Seed/demo data** | Makes the UI explorable | Removed, or clearly gated behind a flag |
| **Frontend demo-data fallback** | Explorable before the backend is up | Removed; a failure should look like a failure |
| **`DEBUG=true` locally** | Enables `/docs` | Refused in production by config guard |

---

## 5. SQLite → PostgreSQL migration considerations

The data layer was built to be migration-friendly, but the move is not free.

**Already in place**
- All access is through SQLAlchemy repositories; no SQLite-specific SQL in
  business logic.
- Alembic migrations use `render_as_batch`, which is a SQLite necessity and
  harmless on PostgreSQL.
- Constraint naming conventions are set on the metadata, so constraints have
  stable names to alter later.
- `DATABASE_URL` is the only change needed to point at another engine.

**Needs attention before the switch**
1. **Type mapping.** SQLite is loosely typed; PostgreSQL is not. Booleans stored
   as integers, and `DATETIME` columns holding naive timestamps, will need review.
   Timestamps are written as UTC-aware — confirm they land in `timestamptz`.
2. **The migration chain has only ever run against SQLite.** Run it end to end on
   PostgreSQL in CI before relying on it.
3. **Concurrency changes shape.** The SQLite serialisation constraints below stop
   applying; connection pooling should be reconsidered (`pool_pre_ping` is
   already set for non-SQLite engines).
4. **`JSON`/text columns.** `before_state` / `after_state` are serialized text.
   PostgreSQL `jsonb` would be materially better for querying the audit trail.
5. **Check constraints** are declared in the ORM and migrations, so they carry
   over — but SQLite does not enforce all of them identically. Expect previously
   tolerated data to be rejected.

**SQLite concurrency, while it remains**
- WAL is enabled, foreign keys are on, and a busy timeout is set.
- DATABASE_SCHEMA.md section 22 warns against multiple independent writers.
  Telemetry now commits in its own transaction, which increases write frequency —
  acceptable at demo volume, but it is the first thing to watch under load.
- Background workers must stay conservative with writes.

---

## 6. GenAILab dependency considerations

1. **Unproven.** The integration has never run. Budget time for it to behave
   differently from the mock — response shapes, error codes and usage reporting
   are the likely surprises.
2. **Usage may not be reported.** The adapter already handles this: absent usage
   is recorded as `UNAVAILABLE`, never zero. If GenAILab does not return usage,
   **all cost figures become ESTIMATED**, which changes what the dashboard can
   honestly claim.
3. **Pricing is not documented anywhere.** The registry ships with `null` prices.
   Until an operator supplies them, cost cannot be computed and the simulator
   correctly refuses to invent a saving.
4. **Availability is a hard dependency** for AI execution. The circuit breaker
   fails fast, but there is no fallback provider.
5. **TLS.** `SSL_VERIFY=false` is currently permitted for the internal gateway.
   Production should use the approved CA.
6. **No documented health endpoint**, so `/ready` deliberately does not probe it.
7. **Rate and quota limits are unknown.** Our limiter protects the platform from
   its callers, not GenAILab from us.

---

## 7. Recommended next steps

In priority order.

**Before any production traffic**
1. **Run the live smoke test.** Nothing else on this list matters until the real
   integration has run once:
   `GENAI_SMOKE_TEST_ENABLED=true GENAI_SMOKE_TEST_MODEL=<id> MODEL_GATEWAY_PROVIDER=genailab pytest -m smoke`
2. **Implement the OIDC adapter.** The development adapter is refused in
   production, so the platform cannot currently start with `APP_ENV=production`
   and a real identity provider.
3. **Complete audit coverage** — budget changes, model enable/disable, policy
   activation and rollback.
4. **Populate registry pricing**, or accept that all cost reporting stays
   ESTIMATED.

**Before multi-replica deployment**
5. Redis-backed `RateLimiter` and circuit breaker.
6. Start the Docker stack and verify it end to end.
7. Define what Redis is actually for, or drop it from the stack.

**Operational**
8. Database backup and restore, with a **tested** restore.
9. Ship logs and OpenTelemetry traces to a real backend.
10. Add a dependency lockfile.

**Quality**
11. Wire context/tool guardrails and `ExecutionBudget` when the first agent
    workflow lands.
12. Clear the remaining 13 `mypy` errors and the pre-existing `ruff` findings.
13. Add frontend component and browser-level tests.

---

## 8. How to reproduce this assessment

```bash
cd backend && ./.venv/bin/pytest
cd backend && ./.venv/bin/ruff check app tests && ./.venv/bin/mypy app
cd frontend && npm test && npm run typecheck && npm run build

# Migration completeness
cd backend && rm -f data/check.db && DATABASE_URL="sqlite+aiosqlite:///./data/check.db" ./.venv/bin/alembic upgrade head
```
