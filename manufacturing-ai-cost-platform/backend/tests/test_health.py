"""Health endpoint tests.

The contract declares GET /health under the /api/v1 server prefix with a 200
"Service is alive" response and no security requirement.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_200(client: AsyncClient, api_prefix: str) -> None:
    response = await client.get(f"{api_prefix}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_health_requires_no_authentication(
    client: AsyncClient, api_prefix: str
) -> None:
    """No Authorization header is sent, and the probe still succeeds."""
    response = await client.get(f"{api_prefix}/health")
    assert response.status_code == 200


async def test_health_is_not_exposed_unversioned(
    client: AsyncClient,
) -> None:
    """Only the contract path exists.

    An unversioned /health alias would be an undocumented endpoint.
    """
    response = await client.get("/health")
    assert response.status_code == 404


async def test_health_error_body_matches_contract_error_schema(
    client: AsyncClient,
) -> None:
    """A 404 still returns the contract's Error shape, not FastAPI's default."""
    response = await client.get("/health")
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "details"}
    assert body["code"] == "not_found"
    assert body["request_id"]
