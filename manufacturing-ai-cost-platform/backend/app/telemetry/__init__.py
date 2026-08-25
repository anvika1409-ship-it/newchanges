"""Cost, quality and trace collection (AI_DEVELOPMENT_RULES.md section 30: telemetry/).

Every cost-affecting AI execution must emit telemetry (section 8). No
execution path exists yet, so no emitter is implemented here. The
correlation inputs it will consume are in app/core/context.py and the
usage/latency fields on ModelResponse."""
