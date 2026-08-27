"""Application startup tests.

Verifies the lifespan wires dependencies up and tears them down, that the
contract's routes are registered under the right prefix, and that no business
endpoint has been invented ahead of its implementation.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.cache.redis_client import NullCache
from app.core.config import Settings
from app.db.session import Database
from app.integrations.llm.interface import ModelGatewayInterface
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


def _contract_operations() -> set[tuple[str, str]]:
    """(method, path) pairs declared in API_CONTRACT.yaml."""
    import yaml

    contract_path = Path(__file__).resolve().parents[2] / "docs" / "API_CONTRACT.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    return {
        (method.upper(), path)
        for path, operations in contract["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }


def test_no_undocumented_endpoint_is_registered(settings: Settings) -> None:
    """Every registered route must exist in API_CONTRACT.yaml.

    Endpoints are never invented in code (AI_DEVELOPMENT_RULES.md sections 2
    and 18). Compared against the contract rather than a hand-maintained list,
    so it keeps working as endpoints land and still fails the moment one
    appears that the contract does not declare.
    """
    app = create_app(settings)
    prefix = settings.api_v1_prefix

    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(prefix):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            registered.add((method, path[len(prefix) :]))

    undocumented = registered - _contract_operations()
    assert not undocumented, (
        f"endpoints not declared in API_CONTRACT.yaml: {sorted(undocumented)}"
    )


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
