# Security & Guardrails

## 1. Security Objective

The platform controls AI execution, budget and model selection. Therefore a compromise could cause:

- unauthorized AI usage
- cost explosions
- sensitive data disclosure
- unauthorized model/tool usage
- malicious routing changes
- unsafe manufacturing actions
- audit tampering

Security must be applied to every layer.

---

## 2. Trust Boundaries

```text
External User/System
        |
        v
API Edge
        |
        v
FastAPI
        |
        +--> Control Plane
        |
        +--> Runtime Plane
                 |
                 v
             Guardrails
                 |
                 v
             Model Gateway
                 |
                 v
              GenAILab
```

External data such as documents, logs, retrieved text and images should be treated as untrusted unless explicitly classified.

---

## 3. Identity & Authentication

Target architecture:

- OAuth2/OIDC compatible
- JWT validation
- short-lived access tokens
- role-based access control
- service-to-service authentication where needed

MVP can use a development authentication adapter, but the application structure must allow an enterprise OIDC implementation.

---

## 4. Authorization / RBAC

Suggested roles:

```text
ADMIN
FINOPS_MANAGER
AI_ENGINEER
PLANT_MANAGER
ANALYST
VIEWER
```

Authorization must be evaluated at both:

1. endpoint level
2. resource/object level

Example:

A Plant Manager should not automatically see another plant's budgets.

---

## 5. Tenant Isolation

Every scoped record should carry tenant ownership either directly or through parent entities.

Queries must include the tenant scope derived from authenticated identity.

Never trust client-provided tenant IDs without server-side authorization.

---

## 6. Secrets Management

Never hard-code:

- GenAILab API key
- database credentials
- JWT signing secrets
- service credentials

Use environment variables for the MVP.

Use a dedicated secret manager/vault in production.

Never expose secrets to:
- React
- logs
- API responses
- audit records
- LLM prompts
- source control

---

## 7. GenAILab Connection

Configuration:

```text
GENAI_BASE_URL=https://genailab.tcs.in/v1
GENAI_API_KEY=<secret>
SSL_VERIFY=false
```

For production:

- prefer TLS verification enabled
- use enterprise CA certificates where required
- restrict outbound traffic to approved destinations
- apply timeouts
- apply retries with limits
- use circuit breakers
- do not log authorization headers or payload secrets

---

## 8. Input Guardrails

Validate:

- content type
- JSON schema
- image/file type
- file size
- request size
- allowed workload types
- business priority
- maximum cost
- allowed fields

Reject malformed requests before model execution.

---

## 9. Prompt Injection Protection

Potential prompt injection can come from:

- uploaded documents
- web pages
- machine logs
- maintenance reports
- supplier information
- RAG results

Mitigations:

- treat retrieved/external content as data, not instructions
- separate system instructions from untrusted content
- use allowlisted tools
- validate generated tool calls
- limit model permissions
- use output validation
- do not grant the model direct credentials

No prompt-injection detector is perfect. Defense in depth is required.

---

## 10. Context Guardrails

Before adding retrieved context:

1. authorization check
2. data classification
3. relevance filtering
4. source trust assessment
5. token/context limit
6. sensitive-data filtering

Do not send an entire database or unrestricted document collection into the model.

---

## 11. Tool Guardrails

Every tool must be registered.

Tool registry fields:

```text
tool_id
name
description
allowed_roles
allowed_workloads
risk_level
estimated_cost
enabled
```

A model cannot call an unregistered tool.

Tool parameters must be validated server-side.

High-risk tools require approval.

---

## 12. Output Guardrails

Validate:

- schema
- required fields
- allowed actions
- sensitive information
- confidence
- business constraints

Never execute generated SQL, shell commands or privileged operations directly from model output.

---

## 13. Budget Guardrails

Limits should exist at:

- request
- agent
- workload
- department
- plant
- tenant
- enterprise

Example:

```text
max_cost_per_request
max_tokens_per_request
max_tool_calls
max_context_tokens
daily_budget
monthly_budget
```

When limits are exceeded:

```text
ALLOW
DOWNGRADE
REQUIRE_APPROVAL
BLOCK
```

according to policy.

---

## 14. High-Risk Manufacturing Actions

High-risk actions include:

- stopping production
- changing machine configuration
- changing suppliers
- changing production routing
- triggering destructive maintenance
- changing enterprise budget controls

The AI may recommend such actions.

It must not independently execute them unless explicitly authorized by policy.

Default:

```text
AI recommendation
      |
      v
Risk = HIGH
      |
      v
Human approval
      |
      v
Execute
```

---

## 15. Optimization Safety

Optimization recommendations are not production changes.

Lifecycle:

```text
Recommendation
     |
     v
Policy validation
     |
     v
Risk assessment
     |
     v
Approval
     |
     v
Versioned policy
     |
     v
Canary / controlled activation
     |
     v
Monitoring
     |
     +--> pass -> rollout
     |
     +--> fail -> rollback
```

Every policy change must be auditable.

---

## 16. Audit Logging

Audit events should capture:

```text
timestamp
request_id
trace_id
user_id
tenant_id
action
resource
before_state
after_state
reason
approval
```

Examples:
- budget changed
- model enabled/disabled
- routing policy changed
- optimization approved
- optimization rejected
- guardrail triggered
- high-risk action approved

Never store secrets in audit records.

---

## 17. Privacy and Data Minimization

Store only the minimum data required.

For large images/documents:

- use object storage
- store references in SQLite
- avoid embedding unnecessary raw content in logs
- redact sensitive fields

Do not log complete prompts or responses by default when they may contain sensitive manufacturing or personal data.

---

## 18. API Security

Implement:

- request size limits
- rate limiting
- CORS allowlist
- HTTPS in production
- security headers
- validation
- consistent error responses
- request IDs
- authorization on every protected endpoint

Do not expose internal exception details.

---

## 19. Resilience / Cost Safety

The LLM integration should support:

- timeout
- bounded retries
- exponential backoff
- circuit breaker
- fallback model
- max execution duration
- max tool calls

Never allow an agent loop to continue indefinitely.

LangGraph workflows should have explicit termination conditions and recursion/iteration limits.

---

## 20. Observability & Security Monitoring

Track:

- authentication failures
- authorization failures
- rate limit events
- cost anomalies
- unusual model selection
- prompt-injection detections
- tool denials
- policy changes
- repeated failures
- excessive token consumption

---

## 21. SQLite Security

SQLite is acceptable for the MVP but is a file-based database.

Protect:

- database file permissions
- backup files
- file path exposure
- logs containing database paths

Do not place the SQLite file in a publicly served frontend directory.

Production migration should use a centralized enterprise database.

---

## 22. Security Testing

Required tests:

- unauthorized endpoint access
- invalid JWT
- cross-tenant access attempt
- role escalation attempt
- malformed request
- oversized payload
- tool authorization failure
- budget bypass attempt
- prompt injection samples
- output schema violation
- high-risk action without approval
- optimization without approval
- audit integrity checks
- secret leakage checks

---

## 23. Secure Development Rules

1. Security controls live in server-side code.
2. Never trust frontend authorization.
3. Never trust LLM-generated authorization decisions.
4. Never trust client-provided tenant ownership.
5. Never allow the model to bypass policies.
6. Never expose API keys to the browser.
7. Never log secrets.
8. Never execute arbitrary model-generated code.
9. Every privileged operation must be auditable.
10. Every high-risk action must have an approval path.
