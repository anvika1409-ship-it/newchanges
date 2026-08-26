"""Model gateway.

The only supported path from application code to any LLM provider
(ARCHITECTURE.md section 7). Business logic imports ModelGatewayInterface from
`interface`; it never imports a provider SDK.

    interface.py  ports and request/response types
    client.py     retry, backoff, circuit breaker, telemetry wrapper, mock
    genailab.py   the GenAILab adapter (the only module importing `openai`)
    errors.py     normalized exceptions
    telemetry.py  per-call telemetry record and sinks
"""
