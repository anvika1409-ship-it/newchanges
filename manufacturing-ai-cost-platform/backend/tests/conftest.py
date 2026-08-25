"""Shared test fixtures.

Tests never touch a live LLM API or a real Redis server
(AI_DEVELOPMENT_RULES.md section 25). The model gateway is the mock
implementation and the cache is a stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.cache.redis_client import NullCache
from app.core.config import AppEnv, AuthMode, ModelGatewayProvider, Settings
from app.main import create_app


class FailingCache(NullCache):
    """Cache stub whose health probe fails, for readiness tests."""

    async def healthcheck(self) -> bool:
        return False


@pytest.fixture
def settings() -> Settings:
    """Isolated settings.

    Values are passed explicitly rather than read from a .env file so a
    developer's local environment cannot change test outcomes.
    """
    return Settings(
        app_env=AppEnv.DEVELOPMENT,
        debug=True,
        log_format="console",
        log_level="WARNING",
        # In-memory SQLite: no file is created and nothing leaks between tests.
        database_url="sqlite+aiosqlite:///:memory:",
        redis_enabled=False,
        model_gateway_provider=ModelGatewayProvider.MOCK,
        auth_mode=AuthMode.DEVELOPMENT,
        dev_principal_roles=["ADMIN"],
        cors_allow_origins=["http://localhost:5173"],
        genai_api_key="",
    )


@pytest.fixture
def app(settings: Settings):
    """Application instance built from the isolated settings."""
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """HTTP client that drives the app through its real lifespan.

    ``LifespanManager`` is not used; ASGITransport does not run lifespan, so the
    startup/shutdown hooks are exercised explicitly here.
    """
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


@pytest.fixture
def api_prefix(settings: Settings) -> str:
    return settings.api_v1_prefix


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the cached global settings from leaking across tests."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
