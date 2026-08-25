"""Runtime routing (AI_DEVELOPMENT_RULES.md section 30: orchestrator/).

The Cost-Aware Orchestrator decides before expensive execution occurs
(ARCHITECTURE.md sections 4 and 6). Routing prefers deterministic
policies and lightweight classifiers; an expensive LLM is never called
merely to choose a model for a request (section 6 of the rules)."""
