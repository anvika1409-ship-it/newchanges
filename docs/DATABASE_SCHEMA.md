# Database Schema — SQLite MVP

## 1. Database Strategy

The hackathon MVP uses **SQLite**.

SQLite is selected because it provides:
- zero infrastructure setup
- fast local development
- easy demo deployment
- simple backup/export
- reliable relational modeling for a single-node prototype

Production migration path:

```text
Application Services
       |
       v
Repository Interfaces
       |
       +--> SQLite for MVP
       |
       +--> PostgreSQL for production
```

Business logic must not depend on SQLite-specific SQL.

Use SQLAlchemy for all persistence.

Use Alembic for migrations.

---

## 2. Entity Overview

```text
Tenant
  |
  +--> Plant
        |
        +--> Department
              |
              +--> Workload
                    |
                    +--> Agent

Model
  |
  +--> Routing Policy

Tool
  |
  +--> Tool authorization (roles / workloads)

Workload
  |
  +--> Usage Event
  |
  +--> Cost Event
  |
  +--> Forecast
  |
  +--> Anomaly
  |
  +--> Optimization Recommendation
```

---

## 3. tenants

Stores enterprise/customer boundary.

Fields:

```text
id                  TEXT PK
name                TEXT NOT NULL
status              TEXT NOT NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```

---

## 4. users

Fields:

```text
id                  TEXT PK
tenant_id           TEXT FK -> tenants.id
username            TEXT UNIQUE NOT NULL
email               TEXT
status              TEXT NOT NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```

---

## 5. roles

Fields:

```text
id                  TEXT PK
name                TEXT UNIQUE NOT NULL
description         TEXT
```

Suggested roles:
- ADMIN
- FINOPS_MANAGER
- AI_ENGINEER
- PLANT_MANAGER
- ANALYST
- VIEWER

---

## 6. user_roles

Fields:

```text
user_id             TEXT FK -> users.id
role_id             TEXT FK -> roles.id
scope_type          TEXT NOT NULL
scope_id            TEXT NOT NULL
PRIMARY KEY(user_id, role_id, scope_type, scope_id)
```

Scope types:
- TENANT
- PLANT
- DEPARTMENT

A role assignment is always scoped. This is what allows SECURITY.md section 4
to be enforced: a PLANT_MANAGER holds the role at `scope_type = PLANT` for a
specific `scope_id` and therefore cannot read another plant's budgets.

Use `scope_type = TENANT` with the user's tenant id for enterprise-wide roles
such as ADMIN.

---

## 7. plants

Fields:

```text
id                  TEXT PK
tenant_id           TEXT FK -> tenants.id
name                TEXT NOT NULL
location            TEXT
timezone            TEXT
status              TEXT NOT NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```

---

## 8. departments

Fields:

```text
id                  TEXT PK
plant_id            TEXT FK -> plants.id
name                TEXT NOT NULL
status              TEXT NOT NULL
```

---

## 9. workloads

Represents an AI business workload.

Fields:

```text
id                  TEXT PK
plant_id            TEXT FK -> plants.id
department_id       TEXT FK -> departments.id
name                TEXT NOT NULL
workload_type       TEXT NOT NULL
description         TEXT
business_priority   TEXT NOT NULL
risk_level          TEXT NOT NULL
status              TEXT NOT NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```

Workload types:
- quality_check
- predictive_maintenance
- supply_chain

---

## 10. agents

Fields:

```text
id                  TEXT PK
workload_id         TEXT FK -> workloads.id
name                TEXT NOT NULL
agent_type          TEXT NOT NULL
description         TEXT
default_model_id    TEXT
status              TEXT NOT NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```

Agents are logical workloads/orchestrated capabilities. They are not required to be fully autonomous LLM agents.

---

## 11. models

Fields:

```text
id                              TEXT PK
model_name                     TEXT UNIQUE NOT NULL
provider                       TEXT
capability                     TEXT NOT NULL
modality                       TEXT
input_cost                     REAL
output_cost                    REAL
cost_unit                      TEXT
max_context_tokens             INTEGER
supports_vision                BOOLEAN
supports_tools                 BOOLEAN
supports_structured_output     BOOLEAN
supports_embeddings            BOOLEAN
quality_score                  REAL
latency_score                  REAL
risk_level                     TEXT
enabled                        BOOLEAN
created_at                     DATETIME
updated_at                     DATETIME
```

Capability values (matches the `capability` filter in API_CONTRACT.yaml):
- text
- vision
- multimodal
- embedding
- speech
- coding
- reasoning

Modality values (matches `AIExecutionRequest.modality` in API_CONTRACT.yaml):
- text
- image
- multimodal
- structured

`capability` describes the model's primary role. `modality` describes the input
types the model accepts. The two columns use different vocabularies on purpose;
do not treat `vision` and `image` as the same value.

Do not assume price values.

They are configurable.

---

## 11.1 tools

Tool registry required by SECURITY.md section 11.

A model cannot call a tool that is not registered and enabled here.

Fields:

```text
id                          TEXT PK
tenant_id                   TEXT FK -> tenants.id
name                        TEXT UNIQUE NOT NULL
description                 TEXT
allowed_roles               TEXT NOT NULL
allowed_workloads           TEXT
risk_level                  TEXT NOT NULL
estimated_cost              REAL
enabled                     BOOLEAN NOT NULL
created_at                  DATETIME
updated_at                  DATETIME
```

`allowed_roles` and `allowed_workloads` hold serialized lists.

Tool parameters are validated server-side against a schema owned by the
guardrails module; they are not stored here.

High-risk tools require an approval record (see section 19).

---

## 12. budgets

Fields:

```text
id                          TEXT PK
tenant_id                   TEXT FK -> tenants.id
scope_type                  TEXT NOT NULL
scope_id                    TEXT NOT NULL
amount                      REAL NOT NULL
currency                    TEXT NOT NULL
period                      TEXT NOT NULL
warning_threshold_percent   REAL NOT NULL
critical_threshold_percent  REAL NOT NULL
status                      TEXT NOT NULL
created_at                  DATETIME
updated_at                  DATETIME
```

Scopes:
- ENTERPRISE
- TENANT
- PLANT
- DEPARTMENT
- WORKLOAD
- AGENT
- MODEL

`tenant_id` is required so tenant isolation (SECURITY.md section 5) can be
enforced with an indexed query. `scope_type = ENTERPRISE` has no parent entity
to derive tenancy from, which is why the column cannot be omitted.

Request-level limits are not budgets. They live on `routing_policies`
(`max_cost_per_request`, `max_total_tokens_per_request`) and on
`AIExecutionRequest.max_cost`.

---

## 13. routing_policies

Fields:

```text
id                              TEXT PK
tenant_id                       TEXT FK -> tenants.id
workload_type                   TEXT NOT NULL
complexity                      TEXT NOT NULL
business_priority               TEXT
selected_model_id               TEXT FK -> models.id
selected_agent_id               TEXT FK -> agents.id
max_context_tokens              INTEGER
max_tool_calls                  INTEGER
max_cost_per_request            REAL
max_total_tokens_per_request    INTEGER
minimum_quality_score           REAL
risk_level                      TEXT
version                         INTEGER NOT NULL
status                          TEXT NOT NULL
canary_traffic_percent          REAL
reason                          TEXT
created_by                      TEXT
approved_by                     TEXT
created_at                      DATETIME
activated_at                    DATETIME
```

A policy must be versioned.

`version` is an INTEGER. The same value is carried on
`usage_events.routing_policy_version` and on
`ExecutionPlan.routing_policy_version` in API_CONTRACT.yaml.

Statuses:
- DRAFT
- PENDING_APPROVAL
- CANARY
- ACTIVE
- SUPERSEDED
- ROLLED_BACK

`canary_traffic_percent` applies only while `status = CANARY` and supports the
controlled-activation stage in ARCHITECTURE.md section 10 and SECURITY.md
section 15. Policies are never updated destructively; a change creates a new
version (AI_DEVELOPMENT_RULES.md section 45).

`max_total_tokens_per_request` is the `max_tokens_per_request` limit named in
SECURITY.md section 13. It is distinct from `max_context_tokens`, which bounds
input context only.

---

## 14. usage_events

Primary telemetry table.

Fields:

```text
id                          TEXT PK
request_id                  TEXT NOT NULL
trace_id                    TEXT
tenant_id                   TEXT
user_id                     TEXT
plant_id                    TEXT
department_id               TEXT
workload_id                 TEXT
agent_id                    TEXT
model_id                    TEXT
timestamp                   DATETIME NOT NULL
input_tokens                INTEGER
output_tokens               INTEGER
total_tokens                INTEGER
context_tokens              INTEGER
image_count                 INTEGER
tool_calls                  INTEGER
execution_time_ms           INTEGER
model_latency_ms            INTEGER
status                      TEXT
error_code                  TEXT
retry_count                 INTEGER
fallback_used               BOOLEAN
quality_score               REAL
business_priority           TEXT
risk_level                  TEXT
routing_policy_version      INTEGER
budget_decision             TEXT
guardrail_decision          TEXT
created_at                  DATETIME NOT NULL
```

These columns are the persistence target for the per-request observability
fields required by ARCHITECTURE.md section 15 and AI_DEVELOPMENT_RULES.md
section 28.

`execution_time_ms` is total request latency. `model_latency_ms` is the gateway
call alone.

`budget_decision` records the outcome the budget policy returned before
execution:
- ALLOW
- DOWNGRADE
- REQUIRE_APPROVAL
- BLOCK

`guardrail_decision` records the terminal guardrail outcome for the request
(ALLOW, or the layer that rejected it). Per-layer detail for the four layers in
AI_WORKFLOWS.md section 8 belongs in audit events, not in this column.

Scope columns are denormalized copies rather than foreign keys so telemetry
rows survive control-plane deletions and can be retained independently
(see section 23).

Indexes should be created for:
- timestamp
- tenant_id
- user_id
- plant_id
- workload_id
- agent_id
- model_id
- request_id

---

## 15. cost_events

Fields:

```text
id                          TEXT PK
usage_event_id              TEXT FK -> usage_events.id
estimated_cost              REAL
actual_cost                 REAL
currency                    TEXT NOT NULL
provenance                  TEXT NOT NULL
input_cost                  REAL
output_cost                 REAL
tool_cost                   REAL
infrastructure_cost        REAL
created_at                  DATETIME NOT NULL
```

Provenance:
- ACTUAL
- ESTIMATED
- UNAVAILABLE

Do not fabricate actual costs.

Currency:

All aggregated reporting (cost summaries, budget consumption, forecasts) is
computed in a single configurable platform base currency. A cost event recorded
in another currency must be converted to the base currency before aggregation.
The conversion policy is configuration, not business logic.

---

## 16. forecasts

Fields:

```text
id                          TEXT PK
tenant_id                   TEXT FK -> tenants.id
scope_type                  TEXT
scope_id                    TEXT
forecast_date               DATE
predicted_cost              REAL
lower_bound                 REAL
upper_bound                 REAL
confidence                  REAL
forecast_model_name         TEXT
forecast_model_version      TEXT
created_at                  DATETIME
```

`forecast_model_name` names the forecasting algorithm. It is unrelated to
`models.model_name`, which names an LLM, and must not be joined to it.

Every value in this table is a FORECAST and must be labelled as such wherever
it is displayed (AI_DEVELOPMENT_RULES.md sections 41 and 42).

---

## 17. anomalies

Fields:

```text
id                          TEXT PK
tenant_id                   TEXT FK -> tenants.id
timestamp                   DATETIME
scope_type                  TEXT
scope_id                    TEXT
anomaly_type                TEXT
severity                    TEXT
expected_value              REAL
actual_value                REAL
deviation_percent           REAL
reason                      TEXT
status                      TEXT
created_at                  DATETIME
resolved_at                 DATETIME
```

---

## 18. optimization_recommendations

Fields:

```text
id                          TEXT PK
tenant_id                   TEXT FK -> tenants.id
workload_id                 TEXT FK -> workloads.id
current_strategy            TEXT
recommended_strategy        TEXT
estimated_saving            REAL
estimated_saving_percent    REAL
quality_impact_percent      REAL
latency_impact_percent      REAL
risk_level                  TEXT
recommendation_reason       TEXT
status                      TEXT
applied_policy_id           TEXT FK -> routing_policies.id
superseded_policy_id        TEXT FK -> routing_policies.id
created_at                  DATETIME
approved_at                 DATETIME
applied_at                  DATETIME
rolled_back_at              DATETIME
approved_by                 TEXT
```

Statuses:
- DRAFT
- PENDING_APPROVAL
- APPROVED
- REJECTED
- APPLIED
- ROLLED_BACK

`applied_policy_id` is the routing policy version this recommendation
activated. `superseded_policy_id` is the version it replaced. Both are required
for the version-based rollback mandated by AI_DEVELOPMENT_RULES.md section 45:
rollback reactivates `superseded_policy_id` and does not delete
`applied_policy_id`.

`estimated_saving` and `estimated_saving_percent` are ESTIMATED or SIMULATED
values and must never be displayed as realized savings.

---

## 19. approvals

Fields:

```text
id                          TEXT PK
tenant_id                   TEXT FK -> tenants.id
resource_type               TEXT NOT NULL
resource_id                 TEXT NOT NULL
action                      TEXT NOT NULL
risk_level                  TEXT
requested_by                TEXT
approved_by                 TEXT
status                      TEXT NOT NULL
comments                    TEXT
created_at                  DATETIME
decided_at                  DATETIME
```

Statuses:
- PENDING
- APPROVED
- REJECTED
- EXPIRED

This table backs every approval path in the platform, not only optimization:
the `REQUIRE_APPROVAL` budget outcome (SECURITY.md section 13), high-risk
manufacturing actions (SECURITY.md section 14) and high-risk tool use
(SECURITY.md section 11).

---

## 20. audit_events

Fields:

```text
id                          TEXT PK
timestamp                   DATETIME NOT NULL
request_id                  TEXT
trace_id                    TEXT
tenant_id                   TEXT
user_id                     TEXT
action                      TEXT NOT NULL
resource_type               TEXT
resource_id                 TEXT
before_state                TEXT
after_state                 TEXT
reason                      TEXT
approval_id                 TEXT FK -> approvals.id
ip_address                  TEXT
user_agent                  TEXT
```

`approval_id` is the `approval` field listed in SECURITY.md section 16. It is
populated for any action that required an approval decision.

Do not store secrets in audit events.

Sensitive values should be redacted.

---

## 21. model_registry_history

Optional but recommended.

Fields:

```text
id                          TEXT PK
model_id                    TEXT
change_type                 TEXT
old_value                   TEXT
new_value                   TEXT
changed_by                  TEXT
created_at                  DATETIME
```

---

## 22. SQLite Production Considerations

For SQLite MVP:

- Enable WAL mode.
- Enable foreign keys.
- Use busy timeout.
- Use connection pooling appropriate for SQLite.
- Avoid long write transactions.
- Keep background workers conservative with writes.
- Do not run multiple independent writers against the same database file without a clear locking strategy.
- Back up the database file regularly.
- Keep the application stateless apart from database/cache dependencies.

When moving to PostgreSQL:

- keep SQLAlchemy repositories unchanged where possible,
- migrate indexes/constraints,
- use PostgreSQL concurrency features,
- move to a centralized database service.

---

## 23. Data Retention

Define configurable retention for:
- raw usage events
- cost events
- audit events
- optimization history
- forecasts

Large raw images and files should be stored outside SQLite.

SQLite stores metadata and references, not large media blobs.
