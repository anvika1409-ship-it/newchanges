"""Request ID middleware tests.

Correlation IDs are required on every request (AI_DEVELOPMENT_RULES.md
section 18, ARCHITECTURE.md section 15).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.core.middleware import REQUEST_ID_HEADER, TRACE_ID_HEADER


async def test_request_id_is_generated_when_absent(
    client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/health")
    assert response.status_code == 200

    request_id = response.headers[REQUEST_ID_HEADER]
    # A well-formed uuid4 was generated.
    assert uuid.UUID(request_id).version == 4


async def test_inbound_request_id_is_honoured(
    client: AsyncClient, api_prefix: str
) -> None:
    supplied = "caller-supplied-id-123"
    response = await client.get(
        f"{api_prefix}/health", headers={REQUEST_ID_HEADER: supplied}
    )
    assert response.headers[REQUEST_ID_HEADER] == supplied


async def test_each_request_gets_a_distinct_id(
    client: AsyncClient, api_prefix: str
) -> None:
    first = await client.get(f"{api_prefix}/health")
    second = await client.get(f"{api_prefix}/health")
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_trace_id_defaults_to_request_id(
    client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/health")
    assert response.headers[TRACE_ID_HEADER] == response.headers[REQUEST_ID_HEADER]


async def test_trace_id_is_honoured_independently(
    client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(
        f"{api_prefix}/health",
        headers={REQUEST_ID_HEADER: "req-1", TRACE_ID_HEADER: "trace-9"},
    )
    assert response.headers[REQUEST_ID_HEADER] == "req-1"
    assert response.headers[TRACE_ID_HEADER] == "trace-9"


async def test_malformed_inbound_id_is_replaced(
    client: AsyncClient, api_prefix: str
) -> None:
    """A header carrying injected content must not reach the log stream."""
    response = await client.get(
        f"{api_prefix}/health",
        headers={REQUEST_ID_HEADER: "bad id with spaces"},
    )
    returned = response.headers[REQUEST_ID_HEADER]
    assert returned != "bad id with spaces"
    assert uuid.UUID(returned).version == 4


async def test_oversized_inbound_id_is_replaced(
    client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(
        f"{api_prefix}/health", headers={REQUEST_ID_HEADER: "a" * 500}
    )
    assert uuid.UUID(response.headers[REQUEST_ID_HEADER]).version == 4


async def test_error_responses_carry_the_request_id(client: AsyncClient) -> None:
    """The id must survive the exception handler path, not just the happy path."""
    response = await client.get("/does-not-exist", headers={REQUEST_ID_HEADER: "err-1"})
    assert response.status_code == 404
    assert response.headers[REQUEST_ID_HEADER] == "err-1"
    assert response.json()["request_id"] == "err-1"
