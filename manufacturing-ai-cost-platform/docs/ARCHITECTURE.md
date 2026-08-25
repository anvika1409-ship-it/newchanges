# Manufacturing AI Cost Intelligence & Autonomous Optimization Platform

## 1. Purpose

This document is the architectural source of truth for the Manufacturing AI Cost Intelligence & Autonomous Optimization Platform.

The platform sits in front of manufacturing AI workloads and makes AI execution cost-aware before expensive model execution occurs.

Core principle:

> Decide first → Execute → Measure → Learn → Improve future decisions.

The platform does not replace manufacturing AI systems. It provides a centralized cost, governance, optimization, and orchestration layer for existing and future AI workloads.

---

## 2. Manufacturing Use Cases

### Quality Control

Multimodal AI analyzes product images and identifies defects.

Typical inputs:
- product/component image
- production line
- product type
- inspection priority
- quality threshold

Typical outputs:
- pass/fail
- defect type
- confidence
- quality metadata

### Predictive Maintenance

AI/ML analyzes equipment data and maintenance history.

Typical inputs:
- machine sensor data
- equipment logs
- incident reports
- maintenance history

Typical outputs:
- anomaly score
- predicted failure risk
- probable root cause
- maintenance recommendation

### Supply Chain Optimization

AI analyzes inventory, supplier and logistics data.

Typical inputs:
- demand
- inventory
- supplier data
- lead times
- logistics data

Typical outputs:
- supplier recommendation
- routing recommendation
- inventory recommendation
- risk explanation

---

## 3. High-Level Architecture

```text
                     MANUFACTURING SYSTEMS
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
        Quality AI      Maintenance AI    Supply Chain AI
        (workload         (workload          (workload
         client)           client)            client)
             |                |                |
             +----------------+----------------+
                              |
                              v
                    SECURITY / API EDGE
                              |
                              v
                   PYTHON + FASTAPI PLATFORM
                              |
              +---------------+---------------+
              |                               |
              v                               v
        CONTROL PLANE                  RUNTIME CONTROL
              |                               |
      Policies / Budgets               Cost-Aware
      Models / Agents                  Orchestrator
      Governance / RBAC                       |
              |                               v
              |                       Policy + Guardrails
              |                               |
              |                               v
              |                        Model Gateway
              |                               |
              |                               v
              |                         GenAILab Gateway
              |                               |
              |                  +------------+------------+
              |                  |            |            |
              |                  v            v            v
              |               Vision       Text       Reasoning
              |                Models       Models       Models
              |                  |            |            |
              +------------------+------------+------------+
                                         |
                                         v
                               RESULT + TELEMETRY
                                         |
                +------------------------+------------------------+
                |                        |                        |
                v                        v                        v
             COST                     QUALITY                  SECURITY
                |                        |                        |
                +------------------------+------------------------+
                                         |
                                         v
                                  SQLITE DATABASE
                                         |
                                         v
                        ANALYTICS / ML / OPTIMIZATION
                                         |
                +------------------------+------------------------+
                |                        |                        |
                v                        v                        v
            Forecasting              Anomaly                Optimization
            Time Series              ML/Stats              ML + Rules + LLM
                |                        |                        |
                +------------------------+------------------------+
                                         |
                                         v
                             PROPOSED ROUTING POLICY
                                         |
                                         v
                      POLICY VALIDATION + RISK EVALUATION
                                         |
                                         v
                            APPROVAL (AUTO OR HUMAN)
                                         |
                                         v
                     VERSIONED ROUTING POLICY (ACTIVATED)
                                         |
                                         v
                              COST-AWARE ORCHESTRATOR
```

---

## 4. Runtime Request Flow

A new AI request must NOT run the manufacturing AI first.

Correct flow:

```text
New Request
    |
    v
Authentication / Authorization
    |
    v
Input Guardrails
    |
    v
Cost-Aware Orchestrator
    |
    +--> workload type
    +--> business priority
    +--> risk
    +--> budget
    +--> complexity
    +--> routing policy
    +--> model capability
    |
    v
Execution Plan
    |
    v
Context / Tool Guardrails
    |
    v
GenAILab Model Gateway
    |
    v
Selected AI Model / Agent
    |
    v
Result
    |
    v
Output Guardrails
    |
    v
Cost + Quality + Latency Telemetry
    |
    v
SQLite
```

The optimization layer should normally operate asynchronously or periodically. It should not invoke an expensive LLM on every request merely to decide a runtime model.

---

## 5. Control Plane

The control plane manages:

- tenants
- plants
- departments
- users
- roles
- workloads
- agents
- models
- budgets
- routing policies
- guardrail policies
- approval workflows
- optimization recommendations
- audit configuration

The control plane is the management and governance layer.

---

## 6. Runtime Control Plane

The Cost-Aware Orchestrator is responsible for:

1. request validation
2. authentication and authorization checks
3. input guardrail evaluation
4. tenant/plant/department identification
5. workload identification
6. priority and risk determination
7. budget evaluation
8. complexity classification
9. routing-policy lookup
10. model capability filtering
11. model selection
12. agent/workflow selection
13. context limits
14. tool limits
15. execution plan generation
16. context / tool guardrail evaluation
17. AI execution
18. output guardrail evaluation
19. telemetry capture

Guardrails are layered, not a single stage. Input guardrails run before
orchestration so malformed or unauthorized requests are rejected before any
classification or routing work occurs. Context and tool guardrails run after
the execution plan is produced, and output guardrails run on the model result.
AI_WORKFLOWS.md section 8 is authoritative for guardrail layer detail.

Runtime routing should prefer:
- deterministic policies
- lightweight classifiers
- model registry metadata
- budget thresholds
- business priority
- historical performance

---

## 7. Model Gateway

All model calls must go through an internal abstraction.

```text
Application
    |
    v
ModelGatewayInterface
    |
    v
GenAILabAdapter
    |
    v
AsyncOpenAI
    |
    v
https://genailab.tcs.in/v1
```

The frontend must never access GenAILab directly.

Environment variables:

```text
GENAI_BASE_URL=https://genailab.tcs.in/v1
GENAI_API_KEY=<secret>
SSL_VERIFY=false
```

For production, TLS verification should normally be enabled. If the internal gateway requires a custom CA, prefer configuring that CA over globally disabling verification.

---

## 8. Model Capability Strategy

Models are selected by capability, constraints and measured performance, not only by model name.

Model registry should classify:
- text
- vision/multimodal
- embedding
- speech
- coding/reasoning

Example vision models:
- azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct
- azure_ai/genailab-maas-Phi-3.5-vision-instruct

Embedding model:
- azure/genailab-maas-text-embedding-3-large

Speech:
- azure/genailab-maas-whisper

Available text/reasoning models are registered through configuration and database metadata.

Do not assume price, quality, context length or latency from model names. These are configurable metadata.

---

## 9. Intelligence Layer

### ML / Time Series

Use for:
- AI cost forecasting
- workload forecasting
- demand prediction
- expected budget consumption

### ML / Statistics

Use for:
- cost anomaly detection
- token anomalies
- latency anomalies
- unusual workload behavior
- model performance deviations

### LLM + Rules

Use for:
- root-cause explanation
- complex reasoning
- natural-language analysis
- optimization explanation

### Optimization / ML

Use for:
- model routing
- model selection
- context optimization
- agent/workflow selection
- workload scheduling
- cost-quality trade-offs

---

## 10. Optimization Lifecycle

```text
Historical Data
      |
      v
Analytics / ML
      |
      v
Optimization Engine
      |
      v
Recommendation
      |
      v
Policy Validation
      |
      v
Risk Evaluation
      |
      +--> Low risk --> Auto approval if policy allows
      |
      +--> Higher risk --> Human approval
      |
      v
Versioned Routing Policy
      |
      v
Optional Canary
      |
      v
Monitor
      |
      +--> Success --> Rollout
      |
      +--> Failure --> Rollback
```

The optimization engine must never directly modify production behavior without policy validation and the required approval.

---

## 11. Security Architecture

Layers:

1. API security
2. identity and RBAC
3. tenant isolation
4. input validation
5. prompt-injection protection
6. context authorization
7. tool authorization
8. output validation
9. budget guardrails
10. audit logging
11. secrets management
12. observability

High-risk actions require human approval.

---

## 12. Data Layer

### MVP

Use SQLite.

Reasons:
- easy local setup
- zero external database dependency
- fast hackathon iteration
- portable demo
- adequate for single-node prototype workloads

The application must use SQLAlchemy repositories/services so SQLite is not directly coupled to business logic.

### Production evolution

The data-access layer should remain migration-friendly so the same logical schema can later move to PostgreSQL or another enterprise relational database.

Do not use SQLite-specific SQL in business logic unless isolated in infrastructure adapters.

---

## 13. Core Components

### Backend

- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- httpx
- AsyncOpenAI
- Redis (role not yet specified; must be documented per AI_DEVELOPMENT_RULES.md
  section 29 or removed from the MVP stack)
- background workers
- LangGraph for stateful agent workflows

### Frontend

- React
- TypeScript
- Tailwind CSS
- charting library

### ML

- pandas
- NumPy
- scikit-learn
- time-series library as required

---

## 14. LangGraph Usage

LangGraph should be used for stateful multi-step AI workflows, not for every API endpoint.

The graphs below are summaries. AI_WORKFLOWS.md sections 5 and 6 are
authoritative for node-level detail, branch conditions and state schema.

Suitable workflows:

### Cost Investigation Graph

```text
START
  |
  v
Load cost data
  |
  v
Detect anomaly
  |
  v
Analyze root cause
  |
  v
Generate recommendation
  |
  v
Estimate savings
  |
  v
Risk evaluation
  |
  v
END
```

### Optimization Graph

```text
START
  |
  v
Load workload history
  |
  v
Compare models
  |
  v
Evaluate cost
  |
  v
Evaluate quality
  |
  v
Evaluate business constraints
  |
  v
Recommend strategy
  |
  v
END
```

---

## 15. Production Observability

Every AI request should have:

- request_id
- trace_id
- tenant_id
- user_id
- workload_id
- agent_id
- model_id
- policy_version

Collect:
- latency
- model latency
- tokens
- cost
- errors
- retry count
- fallback
- guardrail decision
- budget decision

Use OpenTelemetry-compatible instrumentation.

---

## 16. Architectural Quality Goals

The solution should be:

- secure
- modular
- observable
- model-agnostic
- provider-agnostic
- workload-agnostic
- testable
- policy-driven
- cost-aware
- extensible
- migration-friendly

Primary product statement:

> An AI Cost Intelligence and Autonomous Optimization layer that makes enterprise AI workloads cost-aware before execution, measures the actual outcome, learns from historical behavior and continuously improves future routing and resource decisions.
