"""Readiness endpoint tests.

The contract declares GET /ready with 200 when dependencies are ready and 503
when they are not.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import FailingCache


async def test_ready_returns_200_when_dependencies_are_healthy(
    client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/ready")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True
    assert body["checks"]["cache"] is True


async def test_ready_returns_503_when_a_dependency_fails(settings: Settings) -> None:
    """A failing cache probe must degrade readiness, not liveness."""
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        app.state.cache = FailingCache()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get(f"{settings.api_v1_prefix}/ready")
            health = await client.get(f"{settings.api_v1_prefix}/health")

    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["cache"] is False

    # Liveness must stay green so the container is not restarted for a
    # dependency outage.
    assert health.status_code == 200


async def test_ready_does_not_call_the_model_gateway(
    client: AsyncClient, api_prefix: str, app
) -> None:
    """Readiness must not make a billable model call."""
    gateway = app.state.model_gateway
    await client.get(f"{api_prefix}/ready")
    assert gateway.call_count == 0
