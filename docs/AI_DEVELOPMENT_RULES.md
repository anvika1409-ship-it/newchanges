# AI Development Rules

## 1. Purpose

This document is the mandatory development rulebook for all human developers and AI coding tools working on the Manufacturing AI Cost Intelligence & Autonomous Optimization Platform.

It applies to:

- Claude Code
- Vercel v0
- LangGraph/LangChain tooling
- other AI coding assistants
- human developers
- reviewers

The documents below are the primary architectural source of truth:

```text
docs/ARCHITECTURE.md
docs/API_CONTRACT.yaml
docs/DATABASE_SCHEMA.md
docs/AI_WORKFLOWS.md
docs/SECURITY.md
```

When these documents conflict with generated code, the documentation wins until the architecture is intentionally changed and the affected documents are updated.

---

# 2. Golden Rules

1. **Do not invent architecture.**
2. **Do not invent APIs.**
3. **Do not invent database fields.**
4. **Do not invent GenAILab capabilities or pricing.**
5. **Never hard-code secrets.**
6. **Never expose LLM credentials to the frontend.**
7. **All LLM access must go through the Model Gateway abstraction.**
8. **All cost-affecting AI executions must produce telemetry.**
9. **All privileged actions must go through policy enforcement.**
10. **LLMs may recommend; deterministic policy code authorizes.**
11. **High-risk manufacturing actions require human approval unless explicitly authorized by policy.**
12. **Every production-affecting optimization must be versioned and auditable.**
13. **Every new feature must include tests.**
14. **Do not modify unrelated modules.**
15. **Do not hide failures or claim tests passed when they were not run.**
16. **Prefer the simplest reliable implementation over unnecessary complexity.**
17. **Use deterministic rules or lightweight ML when an LLM is unnecessary.**
18. **Never allow an LLM to bypass security, budget, authorization, or approval controls.**
19. **Do not introduce a new dependency without a clear reason.**
20. **Preserve backward compatibility unless a documented API/schema change is approved.**

---

# 3. Source-of-Truth Hierarchy

Use this precedence order:

```text
1. Explicit approved architectural decision
2. ARCHITECTURE.md
3. SECURITY.md
4. API_CONTRACT.yaml
5. DATABASE_SCHEMA.md
6. AI_WORKFLOWS.md
7. Existing tested code
8. Task-specific implementation instructions
9. AI-generated assumptions
```

AI-generated assumptions are the lowest-confidence source.

Scope of precedence:

- API_CONTRACT.yaml is authoritative for the API surface: endpoints, request
  and response shapes, status codes.
- DATABASE_SCHEMA.md is authoritative for persisted field names, field types
  and storage semantics.

Where a field appears in both, the API must adopt the database type. A type
disagreement is a defect in the contract, not a licence to store a different
type.

If required information is missing:

- do not silently guess,
- create an abstraction/configuration point,
- document the assumption,
- or stop that part of the implementation until the contract is known.

---

# 4. Architecture Boundaries

## 4.1 FastAPI owns

FastAPI is the application/control plane.

It owns:

- API endpoints
- request validation
- authentication integration
- authorization
- orchestration
- policy enforcement
- persistence services
- telemetry
- business workflows
- integration boundaries

## 4.2 React owns

React owns:

- presentation
- user interaction
- client-side state
- visualization
- API consumption

React must NOT own:

- authorization decisions
- budget decisions
- model selection
- secrets
- direct GenAILab calls
- direct database access

## 4.3 LangGraph owns

LangGraph owns stateful multi-step AI workflows.

Examples:

- cost investigation
- optimization reasoning
- root-cause explanation
- what-if analysis

LangGraph does NOT replace FastAPI.

## 4.4 Model Gateway owns

The Model Gateway abstraction owns:

- model invocation
- GenAILab integration
- provider-specific behavior
- timeout
- retry
- error normalization
- model telemetry

Business logic must not contain direct provider-specific HTTP/SDK calls.

---

# 5. GenAILab Rules

Gateway:

```text
https://genailab.tcs.in/v1
```

Environment variables:

```text
GENAI_BASE_URL
GENAI_API_KEY
SSL_VERIFY
```

Rules:

- API keys are environment-only.
- Never hard-code a key.
- Never commit `.env` files containing real credentials.
- Never print API keys.
- Never send credentials to the frontend.
- Never place credentials into prompts.
- Never log authorization headers.
- Use the AsyncOpenAI client through the gateway abstraction.
- Keep GenAILab-specific behavior inside the GenAILab adapter.
- Do not assume undocumented response fields.
- Do not assume pricing unless explicitly configured/provided.
- Do not assume every model supports every modality.
- Model capability must come from the model registry.

### TLS

`SSL_VERIFY=false` may be used for the current internal development environment when required.

For production:

- prefer `SSL_VERIFY=true`,
- use the approved internal CA where required,
- do not disable TLS verification globally without a documented security exception.

---

# 6. AI Model Selection Rules

Never select a model using scattered `if/elif` statements across the application.

Use:

```text
Model Registry
+
Routing Policy
+
Business Constraints
+
Budget
+
Quality
+
Latency
```

Preferred runtime flow:

```text
Request
  ↓
Classify
  ↓
Policy
  ↓
Budget
  ↓
Model Registry
  ↓
Candidate Models
  ↓
Optimization score
  ↓
Selected model
```

Do not call an expensive LLM simply to decide which model should handle every request.

Use:

- rules,
- lightweight ML,
- cached routing policies,
- deterministic constraints

for normal runtime routing.

Use LLM reasoning for complex strategic decisions.

---

# 7. LLM Usage Rules

An LLM should be used when it provides meaningful value.

Good uses:

- root-cause reasoning
- summarization
- complex troubleshooting
- explanation
- unstructured reasoning
- natural-language recommendations
- multi-step reasoning in LangGraph

Avoid LLMs for:

- simple arithmetic
- fixed validation
- budget threshold checks
- authorization
- deterministic routing
- database constraints
- simple anomaly thresholds
- security decisions that can be enforced deterministically

---

# 8. LangGraph Rules

LangGraph workflows must:

- have a clear state schema,
- have explicit entry and exit conditions,
- have bounded iteration,
- have error handling,
- have checkpoint/persistence strategy where required,
- avoid uncontrolled loops,
- return structured outputs,
- respect policy and approval boundaries.

A LangGraph node must not directly bypass:

- RBAC
- budget policy
- guardrails
- audit
- Model Gateway

If a graph needs a privileged action, it must call a server-side policy-protected service.

---

# 9. Manufacturing Workload Rules

The manufacturing workloads are demonstrations/pluggable clients of the platform.

Primary examples:

- Quality Control
- Predictive Maintenance
- Supply Chain

Do not turn the project into a full manufacturing execution system.

The goal is to demonstrate:

```text
AI Workload
→ Cost-Aware Execution
→ Cost Measurement
→ Intelligence
→ Optimization
```

Workload-specific logic should remain modular.

---

# 10. Cost Management Rules

Every model execution should capture, where available:

```text
request_id
trace_id
tenant_id
plant_id
department_id
workload_id
agent_id
model_id
timestamp
input_tokens
output_tokens
total_tokens
context_tokens
tool_calls
execution_time_ms
estimated_cost
actual_cost
cost_provenance
quality_score
priority
risk
routing_policy_version
```

Never fabricate actual usage.

When actual token/cost data is unavailable:

```text
provenance = ESTIMATED
```

or:

```text
provenance = UNAVAILABLE
```

Cost estimates must be clearly distinguished from actual spend.

---

# 11. Budget Rules

Budget checks happen BEFORE expensive execution.

Budget scopes may include:

- enterprise
- tenant
- plant
- department
- workload
- agent
- model
- request

Possible policy outcomes:

```text
ALLOW
DOWNGRADE
REQUIRE_APPROVAL
BLOCK
```

A budget rule must be deterministic and server-side.

An LLM cannot override a budget policy.

---

# 12. Optimization Rules

Optimization must be treated as a controlled policy lifecycle:

```text
Analyze
  ↓
Recommend
  ↓
Validate
  ↓
Risk assess
  ↓
Approve
  ↓
Version
  ↓
Activate
  ↓
Monitor
  ↓
Rollback if required
```

Never allow:

```text
LLM
 ↓
Directly modify production policy
```

All policy changes must have:

- policy version,
- reason,
- creator,
- approval,
- timestamp,
- activation status,
- rollback capability.

---

# 13. Security Rules

## Authentication

Use the configured authentication abstraction.

Do not bypass authentication for protected APIs.

## Authorization

Authorization must be checked server-side.

Never trust frontend permission state.

Never trust client-provided tenant ownership.

## Tenant isolation

Every tenant-scoped request must derive tenant identity from authenticated context.

Never rely solely on a tenant ID sent by the client.

## Secrets

Never store secrets in:

- source code
- React code
- tests
- README
- screenshots
- logs
- database records
- prompts
- audit events

---

# 14. Guardrail Rules

Guardrails must be layered.

## Input

Validate:

- schema
- size
- content type
- workload type
- priority
- limits

## Context

Validate:

- access permissions
- relevance
- source trust
- sensitive data
- context size

## Tools

Use:

- allowlists
- authorization
- parameter validation
- risk classification
- rate limits
- timeouts

## Output

Validate:

- schema
- business constraints
- sensitive information
- allowed actions
- confidence/quality where applicable

---

# 15. High-Risk Action Rules

Examples:

- stop production
- alter machine settings
- change supplier
- execute destructive maintenance
- change enterprise policy
- significantly modify AI budget controls

Default workflow:

```text
AI recommendation
  ↓
Risk classification
  ↓
Human approval
  ↓
Authorized action
```

Never allow the model itself to decide that human approval is unnecessary.

---

# 16. Database Rules

MVP database:

**SQLite**

Use:

- SQLAlchemy
- Alembic
- repository/service abstraction

Do not write business logic around SQLite-specific SQL.

Avoid:

- direct SQL from API routes,
- database access from React,
- passing SQLAlchemy objects directly through API contracts,
- database connection logic scattered across services.

All database changes must include migrations.

---

# 17. SQLite Rules for the MVP

Because SQLite is file-based:

- enable foreign keys,
- enable WAL mode where appropriate,
- use sensible busy timeout,
- avoid long transactions,
- serialize high-volume writes where necessary,
- keep the database file outside publicly served directories,
- back up the database file,
- never store large images directly in SQLite.

The application should remain migration-friendly for a future production relational database.

---

# 18. API Rules

All APIs use:

```text
/api/v1
```

Rules:

- validate every request,
- return consistent error structures,
- use correct HTTP status codes,
- use request/correlation IDs,
- support pagination for potentially large collections,
- never expose internal stack traces,
- never expose internal secrets,
- never make frontend behavior depend on undocumented API fields.

The OpenAPI contract is authoritative.

If an API change is required:

1. update API_CONTRACT.yaml,
2. update backend,
3. update frontend,
4. update tests,
5. document migration impact.

---

# 19. Frontend Rules

Use:

- React
- TypeScript
- Tailwind CSS

Rules:

- no secrets,
- no direct database access,
- no direct GenAILab calls,
- no duplicated business rules,
- no security decisions solely on frontend,
- use typed API clients,
- handle loading/error/empty states,
- display actual vs estimated cost distinctly,
- display optimization estimates as estimates,
- provide confirmation for consequential actions.

---

# 20. v0 Rules

v0 should primarily accelerate frontend work.

Use v0 for:

- layouts
- dashboards
- cards
- charts
- forms
- reusable UI components
- visual refinement

Do not allow v0 to:

- redesign backend architecture,
- invent API contracts,
- invent database schemas,
- introduce direct provider calls,
- add secrets,
- bypass RBAC.

The frontend must conform to API_CONTRACT.yaml.

---

# 21. Claude Code Rules

Claude Code is the preferred repository-level coding assistant.

Before making changes, it must:

1. inspect relevant files,
2. read applicable source-of-truth docs,
3. understand dependencies,
4. identify impacted modules,
5. propose the minimal change.

After changes:

1. run tests,
2. run lint/type checks where configured,
3. report failures,
4. summarize changed files,
5. state assumptions.

Do not rewrite the entire repository to implement a small feature.

Do not modify unrelated files.

---

# 22. AI Coding Change Protocol

Every AI coding task should include:

```text
Context
Goal
Allowed files
API contract
Database contract
Security requirements
Acceptance criteria
Tests required
Out-of-scope items
```

For every coding request, explicitly tell the coding AI:

> Do not modify files outside the requested scope unless a dependency requires it. If a dependency is required, explain it first and identify the file(s) that must change.

---

# 23. No Blind Code Generation

Never accept generated code without review.

For generated code:

```text
Generate
  ↓
Compile
  ↓
Run unit tests
  ↓
Run integration tests
  ↓
Security review
  ↓
Code review
  ↓
Merge
```

Do not merge code only because it looks correct.

---

# 24. Testing Rules

Every feature must have appropriate tests.

## Unit

Test:

- functions
- services
- policy evaluation
- routing
- cost calculation

## API

Test:

- validation
- authorization
- response contracts
- errors

## Integration

Test:

- SQLite
- Redis
- GenAILab mocked adapter
- orchestration

## AI safety

Test:

- prompt injection
- malicious tool input
- output schema violations
- sensitive-data leakage
- budget bypass
- authorization bypass

## End-to-end

Primary path:

```text
Quality Request
→ Orchestrator
→ Mock/GenAILab
→ Result
→ Usage event
→ Cost
→ Dashboard API
```

---

# 25. Mocking Rules

Tests must not depend on live LLM APIs.

Use:

```text
ModelGatewayInterface
       |
       +--> GenAILabAdapter
       |
       +--> MockModelGateway
```

Unit/integration tests should use the mock.

Live GenAILab calls should exist only in explicitly configured integration/smoke tests.

---

# 26. Error Handling Rules

Never use:

```python
except Exception:
    pass
```

Never silently swallow failures.

Exceptions should be:

- logged,
- classified,
- normalized,
- returned safely.

Do not leak internal exception details to clients.

---

# 27. Logging Rules

Logs should contain useful operational information:

```text
timestamp
level
service
request_id
trace_id
tenant_id where appropriate
event
duration
status
```

Do NOT log:

- API keys
- passwords
- bearer tokens
- cookies
- full sensitive prompts
- full sensitive responses
- raw secrets

---

# 28. Observability Rules

AI executions should be traceable end-to-end:

```text
request_id
→ orchestration
→ model call
→ telemetry
→ database
→ analytics
```

Track:

- latency
- token usage
- cost
- model
- routing policy
- guardrail decision
- errors
- retries
- fallback

---

# 29. Dependency Rules

Before adding a dependency:

1. Confirm it is necessary.
2. Prefer mature packages.
3. Check compatibility.
4. Pin a safe version range.
5. Add/update documentation.
6. Add tests.
7. Avoid duplicate libraries performing the same job.

Do not introduce a framework merely because an AI coding tool prefers it.

---

# 30. File Ownership Rules

Respect module boundaries.

Suggested ownership:

```text
api/              → API contract / FastAPI
orchestrator/     → runtime routing
policies/         → deterministic policy logic
guardrails/       → AI/security guardrails, tool registry enforcement
integrations/     → external services including GenAILab
intelligence/     → ML/analytics
optimization/     → optimization
workloads/        → manufacturing demo workloads
repositories/     → persistence
services/         → business services
telemetry/        → cost/quality/trace collection
```

A feature should live in the appropriate module instead of being added to a random existing file.

---

# 31. Team Ownership

### Member 1 — Architecture / FastAPI / Integration

Own:
- FastAPI
- orchestration
- GenAILab adapter
- API integration

### Member 2 — FinOps / Database

Own:
- SQLite
- schema
- cost engine
- budget engine
- cost APIs

### Member 3 — AI/ML / LangGraph

Own:
- forecasting
- anomaly detection
- optimization
- LangGraph workflows
- evaluation

### Member 4 — Frontend

Own:
- React
- TypeScript
- Tailwind
- dashboards
- v0-generated components
- API integration

### Member 5 — Security / DevOps / QA

Own:
- auth
- RBAC
- guardrails
- audit
- Docker
- CI/CD
- automated testing
- security testing

Ownership does not eliminate peer review.

---

# 32. Git Rules

Use:

```text
main
develop (optional)
feature/*
fix/*
```

Rules:

- small commits,
- clear commit messages,
- pull requests for shared branches,
- no direct unreviewed production merge,
- no secrets,
- no generated temporary files,
- no large unrelated refactors.

Recommended commit style:

```text
feat: add cost-aware routing
feat: add model registry
fix: handle budget exceeded response
test: add routing policy tests
security: enforce tenant authorization
docs: update API contract
```

---

# 33. Branch Rules for AI Tools

Every AI coding task should happen on a feature branch.

Example:

```text
feature/cost-orchestrator
feature/sqlite-cost-engine
feature/langgraph-optimization
feature/dashboard
feature/security-guardrails
```

Do not allow multiple AI tools to modify the same files concurrently without coordination.

---

# 34. API Contract First

When backend and frontend work in parallel:

```text
API_CONTRACT.yaml
       |
       +--> FastAPI implementation
       |
       +--> TypeScript API types
       |
       +--> Frontend UI
       |
       +--> Tests
```

The API contract is the synchronization point.

Frontend developers must not invent endpoints.

Backend developers must not silently change response shapes.

---

# 35. Database Contract First

Similarly:

```text
DATABASE_SCHEMA.md
       |
       +--> SQLAlchemy
       +--> Alembic
       +--> repositories
       +--> services
       +--> analytics queries
```

If a new field is required:

1. update schema documentation,
2. update migration,
3. update model,
4. update API if necessary,
5. update tests.

---

# 36. Prompt Management Rules

Store important production prompts as versioned files or database records.

Suggested structure:

```text
prompts/
  quality/
  maintenance/
  supply_chain/
  cost_analysis/
  optimization/
```

Each production prompt should have:

```text
prompt_id
version
purpose
model_requirements
input_schema
output_schema
risk_level
created_at
```

Prompt changes must be auditable.

---

# 37. Structured Output Rules

Where an LLM result is consumed programmatically:

- request structured output where supported,
- validate against Pydantic schemas,
- reject invalid outputs,
- never trust free-form text as a command.

Example:

```text
LLM
 ↓
JSON
 ↓
Pydantic validation
 ↓
Business rule validation
 ↓
Action
```

---

# 38. Data Quality Rules

ML/optimization decisions depend on trustworthy historical data.

Before using data:

- remove duplicates,
- handle missing values,
- validate timestamps,
- identify estimated vs actual cost,
- detect outliers,
- record data quality issues.

Do not train optimization logic on obviously corrupted data.

---

# 39. Optimization Evaluation Rules

Never evaluate optimization solely on cost savings.

Evaluate:

```text
Cost
Quality
Latency
Business SLA
Risk
User impact
```

Example acceptance:

```text
Cost reduction >= 20%
AND
Quality degradation <= 1%
AND
Latency increase <= 10%
AND
Risk within policy
```

The thresholds must be configurable by workload.

---

# 40. Production vs MVP Rules

The MVP may use:

- SQLite
- mock authentication adapter
- simplified workers
- simulated manufacturing workloads
- configurable pricing
- simplified forecast models
- mock external finance integration

But the code must preserve the right interfaces for future production integrations.

Never design the MVP in a way that forces a complete rewrite for production.

---

# 41. Demo Data Rules

Demo data must be clearly identified.

Never mix:

```text
REAL
SIMULATED
ESTIMATED
PROJECTED
```

without labeling.

Dashboard labels should distinguish:

- Actual Cost
- Estimated Cost
- Forecast Cost
- Simulated Savings

---

# 42. No Fake Business Outcomes

Do not display fabricated production savings as actual results.

For demo data, explicitly label:

```text
Illustrative
Simulated
Estimated
Projected
```

The system may simulate a manufacturing workload for demonstration, but the UI must not present simulation data as real enterprise operational data.

---

# 43. Performance Rules

Avoid unnecessary LLM calls.

Prefer:

```text
Rules
→ lightweight classifier
→ cache
→ selected model
```

before:

```text
LLM reasoning
```

Use asynchronous I/O.

Avoid blocking operations inside async request handlers.

Move long-running jobs to background workers.

---

# 44. Cost Safety Rules for AI Agents

Every agent workflow should have:

- maximum iterations,
- maximum tool calls,
- maximum token budget,
- maximum elapsed time,
- maximum estimated cost,
- termination condition.

No unbounded agent loop.

LangGraph graphs must have explicit safeguards against recursion/iteration runaway.

---

# 45. Rollback Rules

Any production-affecting routing or optimization policy must support rollback.

Rollback must be:

- version-based,
- auditable,
- authorization-protected,
- fast.

Do not overwrite policies destructively.

Create new versions instead.

---

# 46. Code Review Checklist

Before merge, verify:

### Architecture
- correct module
- no boundary violation
- no duplicate business logic

### Security
- auth
- authorization
- secrets
- input validation
- audit

### AI
- correct model capability
- gateway abstraction
- guardrails
- structured output
- limits

### Cost
- telemetry
- budget check
- actual vs estimated distinction

### Database
- migration
- indexes
- tenant scope

### Tests
- unit
- API
- integration
- failure paths

### Documentation
- API updated
- schema updated
- workflow updated if needed

---

# 47. AI Coding Agent Final Checklist

Before an AI coding tool finishes a task, it must verify:

```text
[ ] Read relevant source-of-truth docs
[ ] Did not invent APIs
[ ] Did not invent database fields
[ ] Did not expose secrets
[ ] Used ModelGateway
[ ] Respected PolicyEngine
[ ] Added validation
[ ] Added error handling
[ ] Added tests
[ ] Updated docs when needed
[ ] Did not modify unrelated files
[ ] Ran available tests
[ ] Reported failures honestly
```

---

# 48. Final Rule

The platform is not:

> "An LLM with a dashboard."

It is:

> **A secure, policy-controlled, cost-aware enterprise AI runtime that observes, predicts, optimizes and governs AI workloads.**

Every implementation decision should strengthen this core product purpose.
