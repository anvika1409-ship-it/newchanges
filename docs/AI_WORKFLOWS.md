# AI Workflows

## 1. Workflow Philosophy

The platform uses different AI techniques for different tasks.

| Task | Primary technology |
|---|---|
| Quality inspection | Multimodal AI / Vision |
| Predictive maintenance | ML + LLM reasoning |
| Supply-chain reasoning | LLM / agent |
| Cost forecasting | Time Series / ML |
| Cost anomaly | ML/statistics |
| Root-cause explanation | LLM + rules |
| Optimization | ML + rules + optimization |
| Runtime routing | Rules + lightweight ML |
| Policy enforcement | Deterministic rules |

Core principle:

> Do not use an LLM when a deterministic rule or lightweight ML model can safely perform the job.

---

## 2. Primary Runtime Workflow — Quality Check

```text
Product Image
    |
    v
FastAPI
    |
    v
Authentication / RBAC
    |
    v
Input Guardrails
    |
    v
Cost-Aware Orchestrator
    |
    +--> Workload Type
    +--> Image Complexity
    +--> Business Priority
    +--> Quality Requirement
    +--> Budget
    +--> Routing Policy
    |
    v
Model Capability Filter
    |
    v
Vision Model Selection
    |
    v
Policy / Risk Check
    |
    v
GenAILab
    |
    v
Multimodal Vision Model
    |
    v
Defect Result
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

### Runtime example

Simple image:

```text
Image
 -> Simple classifier
 -> Phi Vision / approved lower-cost vision model
 -> Result
```

Complex/safety-critical image:

```text
Image
 -> Complexity/risk classifier
 -> Higher-capability vision model
 -> Result
```

The platform must not call an expensive LLM to choose the model for every image.

---

## 3. Predictive Maintenance Workflow

### Step A — Detect anomaly

Use sensor/log ML or statistical logic.

```text
Sensor Data
    |
    v
Feature Extraction
    |
    v
Anomaly Detector
    |
    +--> Normal -> monitor
    |
    +--> Anomaly -> continue
```

### Step B — Reason about anomaly

```text
Machine anomaly
    |
    v
Retrieve authorized history
    |
    v
Context filtering
    |
    v
LLM reasoning
    |
    v
Root cause hypotheses
    |
    v
Maintenance recommendation
```

### Step C — Guard high-risk actions

A recommendation such as "schedule inspection" may be low risk.

An instruction that could stop equipment must require policy-based human approval.

---

## 4. Supply Chain Workflow

```text
Inventory
Supplier Data
Logistics Data
Demand
    |
    v
FastAPI
    |
    v
Cost-Aware Orchestrator
    |
    v
Budget / Priority / Risk
    |
    v
Relevant data retrieval
    |
    v
LLM / Optimization workflow
    |
    v
Candidate plans
    |
    v
Optimization scoring
    |
    v
Recommendation
    |
    v
Human approval if required
```

The LLM should reason over structured candidates where possible rather than being given unlimited unstructured data.

---

## 5. Cost Investigation LangGraph

LangGraph should manage state and orchestration.

```text
START
  |
  v
Load Usage Events
  |
  v
Aggregate Cost
  |
  v
Detect Anomaly
  |
  +--> No anomaly --> END
  |
  +--> Anomaly
          |
          v
      Identify Drivers
          |
          v
      Compare Models
          |
          v
      Compare Workloads
          |
          v
      Root Cause Analysis
          |
          v
      Generate Recommendation
          |
          v
      Estimate Saving
          |
          v
      Risk Evaluation
          |
          v
         END
```

State should contain structured data such as:

```text
request_id
scope
time_window
anomaly
drivers
candidate_optimizations
estimated_savings
quality_impact
risk
```

---

## 6. Optimization LangGraph

```text
START
  |
  v
Load workload history
  |
  v
Load model registry
  |
  v
Load current policy
  |
  v
Load budget state
  |
  v
Evaluate candidate strategies
  |
  +--> Model routing
  +--> Context reduction
  +--> Tool reduction
  +--> Agent selection
  +--> Scheduling
  |
  v
Score candidates
  |
  +--> cost
  +--> quality
  +--> latency
  +--> risk
  +--> business priority
  |
  v
Select recommended strategy
  |
  v
Generate explanation
  |
  v
Generate policy proposal
  |
  v
END
```

---

## 7. Runtime Routing Workflow

This is the most important cost-saving workflow.

```text
New Request
    |
    v
Request Classifier
    |
    +--> simple
    +--> medium
    +--> complex
    |
    v
Routing Policy
    |
    v
Budget Check
    |
    v
Business Priority
    |
    v
Model Registry
    |
    v
Candidate Models
    |
    v
Cost/Quality/Latency constraints
    |
    v
Select model
```

Example:

```text
Simple + normal priority
 -> lower-cost approved model

Complex + normal priority
 -> stronger model

Safety critical
 -> high-capability approved model

Budget critical
 -> cheaper approved strategy unless policy forbids
```

---

## 8. Guardrail Workflow

### Input

```text
Input
 -> schema validation
 -> size check
 -> data classification
 -> prompt-injection check
 -> authorized
```

### Context

```text
Retrieved context
 -> access control
 -> relevance filtering
 -> trusted/untrusted classification
 -> context limit
 -> model
```

### Tool

```text
LLM tool request
 -> tool allowlist
 -> user/role authorization
 -> parameter validation
 -> cost limit
 -> timeout
 -> execute
```

### Output

```text
Model output
 -> schema validation
 -> business-rule validation
 -> sensitive-data check
 -> risk classification
 -> final response/action
```

---

## 9. Feedback Workflow

Every execution emits telemetry.

```text
Execution
   |
   +--> cost
   +--> quality
   +--> latency
   +--> errors
   +--> selected model
   +--> selected policy
        |
        v
      SQLite
        |
        v
   Analytics / ML
        |
        v
   Policy improvement
```

The feedback loop must not introduce a second expensive AI execution into every request.

---

## 10. What-if Simulation Workflow

```text
User changes assumptions
        |
        v
Simulation Input
        |
        +--> production volume
        +--> image volume
        +--> request volume
        +--> budget
        +--> model mix
        |
        v
Forecast
        |
        v
Optimization
        |
        v
Compare:
  current
  forecast
  optimized
        |
        v
Savings + quality + risk
```

Simulation results must clearly be labeled as estimates.
