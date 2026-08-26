"""Application startup tests.

Verifies the lifespan wires dependencies up and tears them down, that the
contract's routes are registered under the right prefix, and that no business
endpoint has been invented ahead of its implementation.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.cache.redis_client import NullCache
from app.core.config import Settings
from app.db.session import Database
try:
    from app.integrations.llm.interface import ModelGatewayInterface
except ImportError:
    from app.integrations.model_gateway.base import ModelGatewayInterface
from app.main import create_app


def test_create_app_returns_a_configured_application(settings: Settings) -> None:
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert app.state.settings is settings


def test_contract_routes_are_registered_under_the_prefix(settings: Settings) -> None:
    app = create_app(settings)
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert f"{settings.api_v1_prefix}/health" in paths
    assert f"{settings.api_v1_prefix}/ready" in paths


def test_no_undocumented_business_routes_are_registered(settings: Settings) -> None:
    """Only system and implemented business operations are registered.

    Business endpoints must arrive with their contract-conforming
    implementation, not as placeholders.
    """
    app = create_app(settings)
    api_paths = {
        route.path  # type: ignore[attr-defined]
        for route in app.routes
        if getattr(route, "path", "").startswith(settings.api_v1_prefix)
    }
    expected = {
        f"{settings.api_v1_prefix}/health",
        f"{settings.api_v1_prefix}/ready",
        f"{settings.api_v1_prefix}/ai/execute",
        f"{settings.api_v1_prefix}/forecasts",
        f"{settings.api_v1_prefix}/anomalies",
        f"{settings.api_v1_prefix}/optimization/recommendations",
        f"{settings.api_v1_prefix}/optimization/analyze",
        f"{settings.api_v1_prefix}/optimization/{{id}}/approve",
        f"{settings.api_v1_prefix}/optimization/{{id}}/apply",
        f"{settings.api_v1_prefix}/optimization/{{id}}/rollback",
    }
    if f"{settings.api_v1_prefix}/models" in api_paths:
        expected.add(f"{settings.api_v1_prefix}/models")
        expected.add(f"{settings.api_v1_prefix}/models/{{id}}")
    assert api_paths == expected


async def test_lifespan_initialises_and_releases_dependencies(
    settings: Settings,
) -> None:
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.database, Database)
        assert isinstance(app.state.cache, NullCache)
        assert isinstance(app.state.model_gateway, ModelGatewayInterface)

        # The database is genuinely connected, not merely constructed.
        assert await app.state.database.healthcheck() is True
        captured_gateway = app.state.model_gateway

    # Shutdown closed the gateway.
    assert await captured_gateway.healthcheck() is False


async def test_sqlite_pragmas_are_applied(settings: Settings) -> None:
    """Foreign keys must be ON; SQLite disables them by default."""
    from sqlalchemy import text

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        database: Database = app.state.database
        async with database.engine.connect() as connection:
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar()

    assert foreign_keys == 1
    assert busy_timeout == settings.sqlite_busy_timeout_ms


def test_openapi_and_docs_are_disabled_outside_debug(settings: Settings) -> None:
    """Schema and docs endpoints must not be exposed when debug is off."""
    production_like = settings.model_copy(update={"debug": False})
    app = create_app(production_like)
    assert app.docs_url is None
    assert app.openapi_url is None
